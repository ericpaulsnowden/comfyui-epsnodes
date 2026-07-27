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
import * as grid from './extensions/comfyui-epsnodes/eps_image/image_grid.js'

const R = (name) => ({ filename: name, subfolder: '', type: 'output' })
const rootGraph = {}
app.graph = rootGraph
app.nodeOutputs = {}

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


def test_empty_buffer_deletes_the_store_entry(grid_api: dict) -> None:
    assert grid_api["empty"]["imgsLen"] == 0
    assert grid_api["empty"]["imagesCleared"] is True
    assert grid_api["empty"]["storeEntryDeleted"] is True


def test_subgraph_node_never_touches_the_store(grid_api: dict) -> None:
    # A subgraph node's locator is subgraph-scoped; writing the plain id
    # would hit an unrelated ROOT node's entry.
    assert grid_api["subgraph"]["storeUntouched"] is True
    assert grid_api["subgraph"]["imgsStillSet"] is True


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
