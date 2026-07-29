"""Frontend tests for EPS Image Grid's focus-clobber root fix (FORMAT.md
§6.6, 2026-07-27).

The bug (owner-reported across several rounds: "when a new image gets added
that image becomes the focus and there is no way to get back to view the
full grid"): frontend 1.45.21 wires every node's ``onDrawBackground`` to
``updatePreviews`` (litegraphService.ts), which re-renders the node's images
FROM ``app.nodeOutputs[locator]`` whenever ``node.images !==
store.images`` -- an ARRAY-IDENTITY comparison -- and core's ``executed``
handler REPLACES that store entry with only the refs a Run reported
(deliberately just the newly-appended ones). So the pack's own full-buffer
refresh lost by construction: repair, then clobber on the very next repaint,
forever. Reproduced live before fixing; the fix makes the store and
``node.images`` share ONE array identity (``syncCoreOutputStore``), with a
synchronous merge at ``executed`` time (``installExecutedMerge``).

These tests drive the PURE exported pieces under Node
(``mergeBufferRefs``/``syncCoreOutputStore``/``setNodeImagesFromRefs``)
against the same stub-`scripts/` served layout every *_js test here uses --
including a faithful re-implementation of ``updatePreviews``' own identity
check, taken verbatim from the extracted core source, as the regression pin.
``installExecutedMerge``/``scheduleRefresh`` are closure/network-bound, so
their wiring is pinned by source-text assertions instead (the
test_frame_saver_paste_js.py convention). Skips cleanly without Node.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
IMAGE_GRID_JS = REPO_ROOT / "web" / "eps_image" / "image_grid.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

PROBE_JS = """
globalThis.Image = class {
  set src(value) { this._src = value }
  get src() { return this._src }
}

import { app } from './scripts/app.js'
import { api } from './scripts/api.js'
import * as grid from './extensions/comfyui-epsnodes/eps_image/image_grid.js'

const R = (name) => ({ filename: name, subfolder: '', type: 'output' })
const rootGraph = {}
app.graph = rootGraph
app.nodeOutputs = {}
// The M2 batch test below exercises `notifyBatchResult`'s toast path -- a
// no-op `add` keeps it off stdout (its `console.info` fallback would
// otherwise interleave with the JSON this script writes at the very end).
app.extensionManager = { toast: { add: () => {} } }

const makeNode = (id, graph) => ({
  id, graph, imgs: null, images: undefined, imageIndex: 7,
  setDirtyCanvas() {}
})

// Core's updatePreviews trigger, verbatim from litegraphService.ts:
//   const isNewOutput = output && this.images !== output.images
const coreWouldRerender = (node) => {
  const output = app.nodeOutputs[String(node.id)]
  return !!(output && node.images !== output.images)
}

const out = {}

// ---- mergeBufferRefs ----
{
  const existing = [R('a.png'), R('b.png')]
  const m1 = grid.mergeBufferRefs(existing, [R('b.png'), R('c.png')])
  const m2 = grid.mergeBufferRefs(existing, [R('a.png')])
  const m3 = grid.mergeBufferRefs(existing, [])
  const m4 = grid.mergeBufferRefs(null, [R('x.png')])
  const m5 = grid.mergeBufferRefs(existing, [null, {}, R('d.png')])
  out.merge = {
    dedupes: m1.refs.map(r => r.filename),
    added: m1.added,
    identityWhenNothingNew: m2.refs === existing && m2.added === 0,
    identityOnEmptyIncoming: m3.refs === existing,
    nullExisting: m4.refs.map(r => r.filename),
    garbageSkipped: { names: m5.refs.map(r => r.filename), added: m5.added }
  }
}

// ---- setNodeImagesFromRefs: the load-bearing identity ----
{
  const node = makeNode(11, rootGraph)
  const refs = [R('a.png'), R('b.png'), R('c.png')]
  grid.setNodeImagesFromRefs(node, refs)
  out.set = {
    imgsLen: node.imgs.length,
    imageIndexNulled: node.imageIndex === null,
    storeHasEntry: !!app.nodeOutputs['11'],
    identityEqual: node.images === app.nodeOutputs['11'].images,
    coreWouldRerender: coreWouldRerender(node)
  }

  // The clobber sequence, replayed: core's executed handler REPLACES the
  // store with just the new ref...
  app.nodeOutputs['11'] = { images: [R('d.png')] }
  out.afterCoreReplace = { coreWouldRerender: coreWouldRerender(node) }
  // ...and the fix's executed-time merge (mergeBufferRefs +
  // setNodeImagesFromRefs, exactly what installExecutedMerge composes)
  // restores one shared identity in the same tick:
  const merged = grid.mergeBufferRefs(node.images, [R('d.png')])
  grid.setNodeImagesFromRefs(node, merged.refs)
  out.afterMerge = {
    added: merged.added,
    gridShowsAll: node.images.map(r => r.filename),
    imageIndexNulled: node.imageIndex === null,
    coreWouldRerender: coreWouldRerender(node)
  }

  // Cached re-send (same refs again): identity-preserving no-op merge, so
  // a user's focused cell must survive the store heal.
  node.imageIndex = 2
  app.nodeOutputs['11'] = { images: [R('d.png')] }
  const resend = grid.mergeBufferRefs(node.images, [R('d.png')])
  grid.syncCoreOutputStore(node, resend.refs)
  out.cachedResend = {
    added: resend.added,
    focusSurvived: node.imageIndex === 2,
    coreWouldRerender: coreWouldRerender(node)
  }
}

// ---- unchanged-content refresh preserves the user's view ----
{
  const node = makeNode(14, rootGraph)
  grid.setNodeImagesFromRefs(node, [R('a.png'), R('b.png')])
  const imgsBefore = node.imgs
  const imagesBefore = node.images
  node.imageIndex = 1 // user enlarged the second image
  // the forced post-run reconcile hands back a FRESH array, same content:
  grid.setNodeImagesFromRefs(node, [R('a.png'), R('b.png')])
  out.unchangedRefresh = {
    focusSurvived: node.imageIndex === 1,
    imgsUntouched: node.imgs === imgsBefore,
    imagesIdentityKept: node.images === imagesBefore,
    storeHealedToNodeArray: app.nodeOutputs['14'].images === imagesBefore,
    coreWouldRerender: coreWouldRerender(node)
  }
  // content actually changed -> full rebuild, back to the grid view:
  grid.setNodeImagesFromRefs(node, [R('a.png'), R('b.png'), R('c.png')])
  out.changedRefresh = {
    rebuilt: node.imgs !== imgsBefore && node.imgs.length === 3,
    backToGrid: node.imageIndex === null
  }
}

// ---- empty buffer clears the store entry ----
{
  const node = makeNode(12, rootGraph)
  grid.setNodeImagesFromRefs(node, [R('a.png')])
  grid.setNodeImagesFromRefs(node, [])
  out.empty = {
    imgsLen: node.imgs.length,
    imagesCleared: node.images === undefined,
    storeEntryDeleted: !('12' in app.nodeOutputs)
  }
}

// ---- subgraph node: store untouched ----
{
  const node = makeNode(13, { otherGraph: true })
  grid.setNodeImagesFromRefs(node, [R('a.png')])
  out.subgraph = {
    storeUntouched: !('13' in app.nodeOutputs),
    imgsStillSet: node.imgs.length === 1
  }
}

// ---- sortFilesForIngest (M1: numeric-aware ingest order) ----
{
  const files = [
    { name: 'img10.png', type: 'image/png' },
    { name: 'IMG2.png', type: 'image/png' },
    { name: 'img1.PNG', type: 'image/png' },
    { name: 'not-an-image.txt', type: 'text/plain' }, // filtered: wrong type
    { name: 'no-type.png' }, // filtered: missing type entirely
    { type: 'image/png' } // tolerated: missing name, sorts as ''
  ]
  const namesBefore = files.map((f) => f.name ?? null)
  const sorted = grid.sortFilesForIngest(files)
  out.sort = {
    names: sorted.map((f) => f.name ?? null),
    length: sorted.length,
    isNewArray: sorted !== files,
    inputUnmutated: files.map((f) => f.name ?? null).every((n, i) => n === namesBefore[i])
  }

  // Stability: two entries whose collated keys tie keep their input order.
  const tie = [
    { name: 'a.png', type: 'image/png', tag: 'first' },
    { name: 'a.png', type: 'image/png', tag: 'second' }
  ]
  out.sortStable = grid.sortFilesForIngest(tie).map((f) => f.tag)
}

// ---- imageUrlForRef / refFromImageSrc (M2: preview + epoch, rand= gone) ----
{
  const ref = { filename: 'a.png', subfolder: 'eps_image_grid/uuid1', type: 'output' }
  const plain = grid.imageUrlForRef(ref)
  const withEpoch = grid.imageUrlForRef(ref, { epoch: 7 })
  const withEpochAgain = grid.imageUrlForRef(ref, { epoch: 7 })
  const withDifferentEpoch = grid.imageUrlForRef(ref, { epoch: 8 })
  const preview = grid.imageUrlForRef(ref, { preview: true, epoch: 7 })
  // The test stub's `api.apiURL` is the identity function, so these come
  // back relative (`/view?...`) -- a real browser only ever assigns an
  // already-absolute `.src`, so this prepends a fake origin to simulate
  // that before round-tripping through `refFromImageSrc` (which does
  // `new URL(src)` with no base).
  const paramOrder = Array.from(new URL('http://x' + preview).searchParams.keys())
  const roundTrip = grid.refFromImageSrc('http://x' + preview)
  out.imageUrl = {
    noRandAnywhere: ![plain, withEpoch, preview].some((u) => u.includes('rand=')),
    stableForEqualInputs: withEpoch === withEpochAgain,
    differsByEpoch: withEpoch !== withDifferentEpoch,
    defaultEpochIsZero: plain.includes('v=0'),
    paramOrder,
    roundTrip
  }
}

// ---- addFilesToBuffer: batch order, hoisted refresh, epoch threading ----
// (the roadmap's M0 probe, extended for M2's rewrite of the same loop)
{
  const node = makeNode(21, rootGraph)
  node.properties = { uuid: '11111111-1111-1111-1111-111111111111' }

  const calls = []
  let addCallCount = 0
  let refreshCount = 0
  // Counts store WRITES for this node's own locator -- the store write
  // happens once per `setNodeImagesFromRefs` refresh (`syncCoreOutputStore`
  // -- see the identity tests above), so this is a direct spy on "how many
  // times did the display actually refresh".
  app.nodeOutputs = new Proxy(app.nodeOutputs, {
    set(target, prop, value) {
      if (prop === String(node.id)) refreshCount++
      target[prop] = value
      return true
    }
  })

  // A recording `api.fetchApi` stub -- reassigning the shared stub module's
  // export is deliberate (its docstring at the top of this file notes it's
  // a mutable object for exactly this). Real `File` instances (not plain
  // `{name, type}` objects) so `FormData.get('image')` hands the upload
  // route its name back, letting the stub prove upload/add ORDER without
  // needing to actually parse multipart bytes.
  api.fetchApi = async (route, options) => {
    const record = { route, method: options?.method }
    calls.push(record)
    if (route === '/upload/image') {
      const uploaded = options.body.get('image')
      record.uploadedName = uploaded?.name
      return {
        ok: true,
        json: async () => ({ name: uploaded?.name, subfolder: '', type: 'input' })
      }
    }
    if (route === '/eps_image_grid/add') {
      const body = JSON.parse(options.body)
      record.addFilename = body.filename
      addCallCount++
      // A growing buffer -- images.length increases by exactly one per
      // add, so the skip-detection heuristic sees "added", never "skipped".
      const images = Array.from({ length: addCallCount }, (_, i) => R(`buffered-${i}.png`))
      return {
        ok: true,
        json: async () => ({ ok: true, uuid: body.uuid, images, generation: 42 })
      }
    }
    return { ok: true, json: async () => ({}) }
  }

  const files = [
    new File(['x'], 'img10.png', { type: 'image/png' }),
    new File(['x'], 'img2.png', { type: 'image/png' }),
    new File(['x'], 'img1.png', { type: 'image/png' })
  ]
  const sorted = grid.sortFilesForIngest(files)
  const returned = await grid.addFilesToBuffer(node, sorted)

  out.batch = {
    sortedNames: sorted.map((f) => f.name),
    uploadOrder: calls.filter((c) => c.route === '/upload/image').map((c) => c.uploadedName),
    addOrder: calls.filter((c) => c.route === '/eps_image_grid/add').map((c) => c.addFilename),
    uploadCount: calls.filter((c) => c.route === '/upload/image').length,
    addCount: calls.filter((c) => c.route === '/eps_image_grid/add').length,
    refreshCount,
    returnedTrue: returned === true
  }
}

// ---- sortFilesForIngest: path-aware order (M3 folder picker) ----
{
  const files = [
    { name: 'img1.png', webkitRelativePath: 'shots/10/img1.png', type: 'image/png' },
    { name: 'img2.png', webkitRelativePath: 'shots/2/img2.png', type: 'image/png' },
    { name: 'img10.png', webkitRelativePath: 'shots/2/img10.png', type: 'image/png' },
    // No `webkitRelativePath` at all -- a plain multi-select mixed into
    // the same call (mixed presence). Falls back to `.name`; 'root.png'
    // collates before 'shots/...' ('r' < 's').
    { name: 'root.png', type: 'image/png' },
    // Wrong type -- the folder picker's ONLY filter (`accept` does
    // nothing on a directory input), must still be dropped.
    { name: 'ignored.txt', webkitRelativePath: 'shots/2/ignored.txt', type: 'text/plain' }
  ]
  const sorted = grid.sortFilesForIngest(files)
  out.folderSort = {
    order: sorted.map((f) => f.webkitRelativePath || f.name),
    length: sorted.length
  }
}

// ---- FOLDER_WARN_THRESHOLD (M3 foot-gun guard) ----
out.folderThreshold = {
  value: grid.FOLDER_WARN_THRESHOLD,
  isNumber: typeof grid.FOLDER_WARN_THRESHOLD === 'number'
}

// ---- delete: a removal always returns to grid view (M5 un-deferred) ----
// `setNodeImagesFromRefs` is the ONLY place `imageIndex` is ever written
// after a display refresh (the delete feature itself has no index math of
// its own -- it just hands `/remove`'s whole-remaining-buffer response to
// this, same as every other add/refresh path). This replays exactly that:
// a 3-image grid with the SECOND tile focused, then a refresh reflecting
// that tile's removal (2 images left, shifted).
{
  const node = makeNode(31, rootGraph)
  grid.setNodeImagesFromRefs(node, [R('a.png'), R('b.png'), R('c.png')])
  node.imageIndex = 1 // user had "b.png" enlarged -- then deletes it
  grid.setNodeImagesFromRefs(node, [R('a.png'), R('c.png')]) // /remove's response: b.png gone
  out.deleteFocus = {
    backToGrid: node.imageIndex === null,
    remaining: node.images.map((r) => r.filename)
  }

  // Deleting a tile that was NOT focused must equally drop back to grid --
  // content changed either way, so there is no special-cased "keep focus
  // if the deleted one wasn't focused" branch to verify separately.
  const node2 = makeNode(32, rootGraph)
  grid.setNodeImagesFromRefs(node2, [R('a.png'), R('b.png'), R('c.png')])
  node2.imageIndex = 0 // focused on "a.png", but "c.png" gets deleted instead
  grid.setNodeImagesFromRefs(node2, [R('a.png'), R('b.png')])
  out.deleteFocusUnrelatedTile = {
    backToGrid: node2.imageIndex === null,
    remaining: node2.images.map((r) => r.filename)
  }
}

// ---- basenameForUpload (2026-07-29 folder-upload fix) ----
out.basenames = [
  { name: 'Eric/IMG_1865.PNG' },
  { name: 'Eric' + String.fromCharCode(92) + 'IMG_1865.PNG' }, // backslash, escaping-proof
  { name: 'shots/2/IMG_1865.PNG' },
  { name: 'plain.png' },
  { name: '' },
  { name: '   ' },
  null
].map((f) => grid.basenameForUpload(f))

process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def grid_api(tmp_path_factory: pytest.TempPathFactory) -> dict:
    layout = tmp_path_factory.mktemp("web_root")
    module_dir = layout / "extensions" / "comfyui-epsnodes" / "eps_image"
    module_dir.mkdir(parents=True)
    shutil.copyfile(IMAGE_GRID_JS, module_dir / "image_grid.js")

    # image_grid.js's two imports, stubbed at the served depth
    # (`../../../scripts/{app,api}.js`) -- the same fixture shape
    # tests/test_distributor_js.py and test_resolution_grid_js.py use, so a
    # wrong relative depth fails module resolution outright.
    scripts = layout / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "app.js").write_text(
        "export const app = {}\nexport class ComfyApp {}\n", encoding="utf-8"
    )
    (scripts / "api.js").write_text(
        "export const api = {\n"
        "  apiURL: (p) => p,\n"
        "  fetchApi: async () => ({ ok: true, json: async () => ({}) }),\n"
        "  addEventListener: () => {}\n"
        "}\n",
        encoding="utf-8",
    )

    probe = layout / "probe.mjs"
    probe.write_text(PROBE_JS, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(probe)], capture_output=True, text=True, cwd=layout, timeout=60
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


def test_merge_dedupes_and_appends_in_order(grid_api: dict) -> None:
    assert grid_api["merge"]["dedupes"] == ["a.png", "b.png", "c.png"]
    assert grid_api["merge"]["added"] == 1
    assert grid_api["merge"]["nullExisting"] == ["x.png"]
    assert grid_api["merge"]["garbageSkipped"] == {"names": ["a.png", "b.png", "d.png"], "added": 1}


def test_merge_preserves_identity_when_nothing_new(grid_api: dict) -> None:
    # Load-bearing, not an optimization: the returned array IS the existing
    # one, so the store heal on a cached re-send keeps `node.images ===
    # store.images` and updatePreviews stays quiet.
    assert grid_api["merge"]["identityWhenNothingNew"] is True
    assert grid_api["merge"]["identityOnEmptyIncoming"] is True


def test_set_installs_one_shared_identity(grid_api: dict) -> None:
    """THE regression pin: after our refresh, core's own updatePreviews
    trigger (`output && node.images !== output.images`, verbatim from
    litegraphService.ts) must be FALSE -- the repaint clobber cannot fire."""
    assert grid_api["set"]["identityEqual"] is True
    assert grid_api["set"]["coreWouldRerender"] is False
    assert grid_api["set"]["imageIndexNulled"] is True
    assert grid_api["set"]["imgsLen"] == 3


def test_executed_replay_restores_shared_identity(grid_api: dict) -> None:
    """Core replaces the store with just the new ref (rerender armed); the
    executed-time merge must disarm it in the same tick AND show the whole
    grid with the new image included."""
    assert grid_api["afterCoreReplace"]["coreWouldRerender"] is True
    assert grid_api["afterMerge"]["added"] == 1
    assert grid_api["afterMerge"]["gridShowsAll"] == ["a.png", "b.png", "c.png", "d.png"]
    assert grid_api["afterMerge"]["imageIndexNulled"] is True
    assert grid_api["afterMerge"]["coreWouldRerender"] is False


def test_cached_resend_heals_store_without_yanking_focus(grid_api: dict) -> None:
    # A queued batch re-sends the same cached ui once per run; the user's
    # deliberately focused cell must not be yanked back to the grid.
    assert grid_api["cachedResend"]["added"] == 0
    assert grid_api["cachedResend"]["focusSurvived"] is True
    assert grid_api["cachedResend"]["coreWouldRerender"] is False


def test_unchanged_refresh_preserves_the_users_view(grid_api: dict) -> None:
    """The post-run reconcile fires on EVERY finished execution, including
    ones that appended nothing -- it must not reset an enlarged view or
    rebuild every <img>, and the store heal must reuse `node.images`' OWN
    identity (not the fresh fetch's array) to keep updatePreviews quiet."""
    assert grid_api["unchangedRefresh"]["focusSurvived"] is True
    assert grid_api["unchangedRefresh"]["imgsUntouched"] is True
    assert grid_api["unchangedRefresh"]["imagesIdentityKept"] is True
    assert grid_api["unchangedRefresh"]["storeHealedToNodeArray"] is True
    assert grid_api["unchangedRefresh"]["coreWouldRerender"] is False


def test_changed_refresh_rebuilds_and_returns_to_the_grid(grid_api: dict) -> None:
    assert grid_api["changedRefresh"]["rebuilt"] is True
    assert grid_api["changedRefresh"]["backToGrid"] is True


def test_empty_buffer_deletes_the_store_entry(grid_api: dict) -> None:
    assert grid_api["empty"]["imgsLen"] == 0
    assert grid_api["empty"]["imagesCleared"] is True
    assert grid_api["empty"]["storeEntryDeleted"] is True


def test_subgraph_node_never_touches_the_store(grid_api: dict) -> None:
    # A subgraph node's locator is subgraph-scoped; writing the plain id
    # would hit an unrelated ROOT node's entry.
    assert grid_api["subgraph"]["storeUntouched"] is True
    assert grid_api["subgraph"]["imgsStillSet"] is True


# ---- M1: sortFilesForIngest (roadmap owner decision: numeric-aware order) ----


def test_sort_files_for_ingest_is_numeric_aware_and_case_insensitive(grid_api: dict) -> None:
    names = grid_api["sort"]["names"]
    # A missing `.name` is tolerated (never thrown) and sorts as if its key
    # were '' -- first, ahead of every named file.
    assert names[0] is None
    # img1 < img2 < img10 numerically (not "1" < "10" < "2" lexically), and
    # case doesn't affect the order ("IMG2.png" sorts with "img2.png").
    assert names[1:] == ["img1.PNG", "IMG2.png", "img10.png"]


def test_sort_files_for_ingest_filters_non_images(grid_api: dict) -> None:
    # 6 inputs in: a .txt file and a file with no `type` at all are dropped;
    # a file with no `.name` is kept (tolerated, not a filter reason).
    assert grid_api["sort"]["length"] == 4


def test_sort_files_for_ingest_is_pure(grid_api: dict) -> None:
    assert grid_api["sort"]["isNewArray"] is True
    assert grid_api["sort"]["inputUnmutated"] is True


def test_sort_files_for_ingest_is_stable_for_equal_keys(grid_api: dict) -> None:
    assert grid_api["sortStable"] == ["first", "second"]


# ---- M2: imageUrlForRef / refFromImageSrc (preview + epoch, rand= gone) ----


def test_image_url_for_ref_never_carries_rand(grid_api: dict) -> None:
    """Buffer frames are append-only PNGs while a buffer lives
    (`image_grid_store.py`'s `_next_frame_filename`: "never reuses a name"),
    so a per-render `rand=Math.random()` cache-buster was pure waste -- the
    single cause of "100 files ~= 5,050 full-resolution loads" (roadmap
    M2). It's gone outright, replaced by the server-derived `generation`
    epoch (`v=`), which is stable across calls describing the same buffer
    contents and only changes when a Clear could have reused a filename."""
    assert grid_api["imageUrl"]["noRandAnywhere"] is True


def test_image_url_for_ref_epoch_is_a_stable_cache_token(grid_api: dict) -> None:
    assert grid_api["imageUrl"]["stableForEqualInputs"] is True
    assert grid_api["imageUrl"]["differsByEpoch"] is True
    assert grid_api["imageUrl"]["defaultEpochIsZero"] is True


def test_image_url_for_ref_preview_after_identity_params(grid_api: dict) -> None:
    assert grid_api["imageUrl"]["paramOrder"] == [
        "filename",
        "subfolder",
        "type",
        "preview",
        "v",
    ]


def test_ref_from_image_src_round_trips_a_preview_url(grid_api: dict) -> None:
    assert grid_api["imageUrl"]["roundTrip"] == {
        "name": "a.png",
        "subfolder": "eps_image_grid/uuid1",
        "type": "output",
    }


# ---- M0/M2: addFilesToBuffer -- batch order + hoisted refresh ----


def test_add_files_to_buffer_uploads_and_adds_in_sorted_order(grid_api: dict) -> None:
    batch = grid_api["batch"]
    assert batch["sortedNames"] == ["img1.png", "img2.png", "img10.png"]
    assert batch["uploadOrder"] == ["img1.png", "img2.png", "img10.png"]
    assert batch["addOrder"] == ["img1.png", "img2.png", "img10.png"]
    assert batch["uploadCount"] == 3
    assert batch["addCount"] == 3
    assert batch["returnedTrue"] is True


def test_add_files_to_buffer_refreshes_the_display_exactly_once(grid_api: dict) -> None:
    """THE M2 regression pin: before the rewrite, `addFilesToBuffer` called
    `setNodeImagesFromRefs` INSIDE the per-file loop, so 3 files meant 3
    full-buffer rebuilds (and 100 files meant ~5,050 image loads). The
    refresh is now hoisted to `runAddBatch`'s K=10-and-final cadence -- for
    a 3-file batch (under K), that means exactly ONE store write."""
    assert grid_api["batch"]["refreshCount"] == 1


# ---- M3: "Add folder..." -- path-aware sort + foot-gun threshold ----


def test_sort_files_for_ingest_is_path_aware_for_folder_picks(grid_api: dict) -> None:
    """`shots/2/...` must sort before `shots/10/...` (numeric across the
    path SEGMENT, not just within one filename), and files within one
    folder must still sort numerically among themselves."""
    assert grid_api["folderSort"]["order"] == [
        "root.png",  # no webkitRelativePath -- falls back to `.name`
        "shots/2/img2.png",
        "shots/2/img10.png",
        "shots/10/img1.png",
    ]


def test_sort_files_for_ingest_folder_filter_still_applies(grid_api: dict) -> None:
    # 5 inputs in: one non-image (.txt) dropped, the rest (including one
    # with no `webkitRelativePath` at all -- mixed presence) kept.
    assert grid_api["folderSort"]["length"] == 4


def test_folder_warn_threshold_is_exported_and_numeric(grid_api: dict) -> None:
    assert grid_api["folderThreshold"]["value"] == 200
    assert grid_api["folderThreshold"]["isNumber"] is True


# ---- M5 un-deferred: delete one image (owner ask 2026-07-29) ----


def test_delete_of_the_focused_tile_returns_to_grid_view(grid_api: dict) -> None:
    """No index math needed for delete -- `setNodeImagesFromRefs` already
    resets `imageIndex` to `null` on ANY content change (never just the
    additions this was originally written for), and a successful delete is
    by definition a content change (the buffer shrinks by one), so this
    already-proven mechanism covers the delete-focused-tile case for free."""
    assert grid_api["deleteFocus"]["backToGrid"] is True
    assert grid_api["deleteFocus"]["remaining"] == ["a.png", "c.png"]


def test_delete_of_an_unrelated_tile_also_returns_to_grid_view(grid_api: dict) -> None:
    # Whether or not the deleted tile was the focused one, `imageIndex`
    # cannot survive pointing at stale data -- there is no "was it the
    # focused index" branch in the actual implementation to test separately.
    assert grid_api["deleteFocusUnrelatedTile"]["backToGrid"] is True
    assert grid_api["deleteFocusUnrelatedTile"]["remaining"] == ["a.png", "b.png"]


# ---- closure-bound wiring, pinned by source text ----

_SOURCE = IMAGE_GRID_JS.read_text(encoding="utf-8")


def test_attach_installs_the_executed_merge() -> None:
    assert "installExecutedMerge(node)" in _SOURCE


def test_progress_refresh_is_forced_and_the_800ms_bet_is_gone() -> None:
    assert "scheduleRefresh(node, { force: true })" in _SOURCE
    assert ", 800)" not in _SOURCE


def test_set_never_clones_the_refs_array() -> None:
    # The whole fix rests on ONE array instance on both sides of core's
    # identity check; a well-meaning defensive copy would resurrect the bug
    # with every test above still green except this one.
    assert "node.images = refs" in _SOURCE
    assert "node.images = [...refs]" not in _SOURCE
    assert "node.images = refs.slice()" not in _SOURCE


# ---- M1/M2 bulk-add: more closure-bound wiring, pinned by source text ----


def _function_body(marker: str) -> str:
    """The full brace-matched `{...}` body of the function whose
    declaration contains *marker* (e.g. ``"export async function
    addFilesToBuffer"``). Brace-matching (rather than e.g. "up to the next
    blank line" or "up to the next `export`") keeps the boundary exact
    regardless of what surrounds the function, including nested arrow
    functions and object/template-literal braces inside it (both are
    inherently balanced, so a naive counter still lands correctly).

    Skips past the PARAMETER LIST first (paren-matched) before looking for
    the body's opening brace -- `runAddBatch`'s own signature has a
    destructuring default (`{ noun = 'images' } = {}`), whose braces would
    otherwise be mistaken for the body itself.
    """
    start = _SOURCE.index(marker)
    paren_start = _SOURCE.index("(", start)
    depth = 0
    paren_end = None
    for i in range(paren_start, len(_SOURCE)):
        ch = _SOURCE[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                paren_end = i
                break
    if paren_end is None:
        raise AssertionError(f"unbalanced parens scanning from {marker!r}")

    brace_start = _SOURCE.index("{", paren_end)
    depth = 0
    for i in range(brace_start, len(_SOURCE)):
        ch = _SOURCE[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return _SOURCE[brace_start : i + 1]
    raise AssertionError(f"unbalanced braces scanning from {marker!r}")


def test_hoisted_refresh_not_inside_the_per_file_loop() -> None:
    """M2's whole reason for existing: `addFilesToBuffer` used to call
    `setNodeImagesFromRefs` once per file (100 files ~= 5,050 image loads).
    The refresh now lives entirely inside the shared `runAddBatch` --
    `addFilesToBuffer`'s OWN body must not call it directly."""
    body = _function_body("export async function addFilesToBuffer")
    assert "setNodeImagesFromRefs(" not in body
    assert "runAddBatch(" in body


def test_batch_runner_hoists_refresh_to_k_interval_and_final() -> None:
    body = _function_body("async function runAddBatch")
    assert "BATCH_REFRESH_INTERVAL" in body
    assert body.count("setNodeImagesFromRefs(") == 2  # the K-interval call + the final call


def test_uuid_captured_once_per_batch() -> None:
    """Roadmap risk #1, "uuid remint race": a debounced collision sweep can
    remint a node's uuid mid-batch; both bulk-ingest loops must capture
    `currentUuid(node)` ONCE, before their loop starts, and thread that same
    value through every item -- never re-read it per item."""
    files_body = _function_body("export async function addFilesToBuffer")
    assert files_body.count("currentUuid(node)") == 1
    assert "addUploadToBuffer(node, uploaded, uuid, signal)" in files_body

    clipspace_body = _function_body("async function addClipspaceToBuffer")
    assert clipspace_body.count("currentUuid(node)") == 1


def test_batch_runner_restores_the_label_in_finally() -> None:
    body = _function_body("async function runAddBatch")
    finally_index = body.index("finally")
    assert finally_index != -1
    assert "buttonWidget.label = idleLabel" in body[finally_index:]


def test_batch_runner_sends_one_aggregate_toast() -> None:
    body = _function_body("async function runAddBatch")
    assert body.count("notifyBatchResult(") == 1


def test_sort_applied_in_both_the_picker_and_drop_paths() -> None:
    picker_body = _function_body("function ensureFileInput")
    assert "sortFilesForIngest(" in picker_body

    drop_body = _function_body("function installDragAndDrop")
    assert "sortFilesForIngest(" in drop_body


def test_add_images_button_copies_clear_button_exactly() -> None:
    body = _function_body("function addAddImagesButton")
    assert "node.addWidget(" in body
    assert "widget.serialize = false" in body


def test_batch_runner_wires_an_abort_controller_for_cancel() -> None:
    body = _function_body("async function runAddBatch")
    assert "new AbortController()" in body
    assert "batchState.cancelled" in body
    assert "controller?.signal" in body


# ---- M3 "Add folder...": more closure-bound wiring, pinned by source text ----


def test_folder_input_uses_webkitdirectory() -> None:
    body = _function_body("function ensureFolderInput")
    assert "input.webkitdirectory = true" in body
    # accept is a no-op on a directory input (per the roadmap) -- must not
    # be set here the way the plain file picker sets it.
    assert "input.accept" not in body


def test_folder_batch_is_confirm_gated_on_the_warn_threshold() -> None:
    body = _function_body("async function startFolderBatch")
    assert "FOLDER_WARN_THRESHOLD" in body
    assert "window.confirm(" in body
    # The threshold must actually gate the confirm, not just co-occur.
    threshold_index = body.index("FOLDER_WARN_THRESHOLD")
    confirm_index = body.index("window.confirm(")
    assert threshold_index < confirm_index


def test_both_add_buttons_share_the_busy_guard() -> None:
    """Roadmap M3: 'one batch per node total, not per button' -- both
    button click handlers must route through the SAME cancel/busy check
    (`handleAddButtonClicked`), not two independent copies of it."""
    images_body = _function_body("function onAddImagesClicked")
    folder_body = _function_body("function onAddFolderClicked")
    assert "handleAddButtonClicked(" in images_body
    assert "handleAddButtonClicked(" in folder_body


def test_batch_runner_progress_targets_the_invoking_button() -> None:
    """`runAddBatch` must look up the progress/Cancel widget by a
    PARAMETER, not the "Add images..." constant hard-coded -- otherwise a
    folder-picker batch would silently mutate the wrong button."""
    body = _function_body("async function runAddBatch")
    assert "findWidget(node, buttonLabel)" in body
    assert "findWidget(node, ADD_IMAGES_BUTTON_LABEL)" not in body


def test_add_files_to_buffer_threads_button_label_through() -> None:
    body = _function_body("export async function addFilesToBuffer")
    assert "buttonLabel" in body
    assert "{ buttonLabel }" in body


# ---- M5 "delete this image": more closure-bound wiring, pinned by source text ----


def test_delete_menu_registered_on_the_same_surface_as_copy() -> None:
    """Roadmap M5 un-deferred: Delete must reuse the SAME resolution
    (`currentMenuSelection`) and the SAME wrap-`getExtraMenuOptions`-not-
    replace idiom `installCopyImageMenuItem` established, not a parallel
    mechanism for deciding "which tile is this menu about"."""
    body = _function_body("function installDeleteImageMenuItem")
    assert "currentMenuSelection(node)" in body
    assert "node.getExtraMenuOptions" in body
    assert "DELETE_MENU_LABEL" in body


def test_delete_derives_the_ref_from_node_images_never_from_src() -> None:
    """Roadmap ask, verbatim: derive the ref via `node.images[idx]` --
    NEVER parse `.src`. Neither the menu installer nor the delete request
    itself may reference `refFromImageSrc` or `.src` anywhere."""
    menu_body = _function_body("function installDeleteImageMenuItem")
    delete_body = _function_body("async function deleteBufferFrame")
    for body in (menu_body, delete_body):
        assert "refFromImageSrc" not in body
        assert ".src" not in body


def test_delete_refuses_while_a_batch_is_running() -> None:
    body = _function_body("function installDeleteImageMenuItem")
    assert "state.batch && !state.batch.done" in body
    assert "getNodeFileState(node)" in body


def test_delete_path_has_no_confirm_dialog() -> None:
    """Verified against `image_grid_store.py` before shipping this: every
    buffer frame is either a fresh tensor-encode or a PIL RE-ENCODED COPY
    of the uploaded/referenced source (never the source file itself), so
    deleting one is never destructive to anything the user can't
    regenerate -- same reasoning Clear already relies on for its own lack
    of a confirm dialog."""
    menu_body = _function_body("function installDeleteImageMenuItem")
    delete_body = _function_body("async function deleteBufferFrame")
    assert "window.confirm" not in menu_body
    assert "window.confirm" not in delete_body


def test_delete_refresh_reuses_the_generation_and_display_pipeline() -> None:
    body = _function_body("async function deleteBufferFrame")
    assert "noteBufferGeneration(node, result)" in body
    assert "setNodeImagesFromRefs(node, result.images)" in body


# ---- folder-upload filename + button order (2026-07-29 owner reports) ------


def test_upload_sends_a_bare_basename_never_a_path(grid_api: dict) -> None:
    """Owner bug: "Adding a folder fails" with
    `FileNotFoundError: ...\\Input\\Eric\\IMG_1865.PNG` from core's own
    /upload/image. A webkitdirectory pick names each File after its place in
    the tree, `FormData.append(name, file)` transmits that whole string as
    the filename, and core joins it into the path while only creating the
    directory implied by the SUBFOLDER param -- so the write target's parent
    never exists. Reproduced live (500 with a path-y name, 200 with a
    basename) before fixing."""
    assert grid_api["basenames"] == [
        "IMG_1865.PNG",  # posix folder pick
        "IMG_1865.PNG",  # windows separator
        "IMG_1865.PNG",  # nested
        "plain.png",  # already bare -- unchanged
        "image.png",  # empty name -> generic (core 400s a blank filename)
        "image.png",  # whitespace-only
        "image.png",  # missing/!File
    ]


def test_upload_call_passes_the_explicit_filename(grid_api: dict) -> None:
    # The third FormData argument IS the fix; without it the browser falls
    # back to file.name and the bug returns silently.
    source = (REPO_ROOT / "web" / "eps_image" / "image_grid.js").read_text(encoding="utf-8")
    assert "formData.append('image', file, basenameForUpload(file))" in source


def test_clear_button_is_installed_after_both_add_buttons() -> None:
    """Owner ask: "the clear button should be below the two add buttons" --
    destructive action last. Widget paint order is install order."""
    source = (REPO_ROOT / "web" / "eps_image" / "image_grid.js").read_text(encoding="utf-8")
    images_at = source.index("addAddImagesButton(node) // M1")
    folder_at = source.index("addAddFolderButton(node) // M3")
    clear_at = source.rindex("addClearButton(node)")
    assert images_at < folder_at < clear_at
