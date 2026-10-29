"""Frontend tests for `web/eps_image/save_image.js` (FORMAT.md §6.14,
provenance M2): the filename-token fallback's pure helpers are driven under
Node via a served-layout probe (test_cross_sweep_js.py's convention; this
module imports `../../../scripts/app.js` and `../lora_library/api.js`, so
the fixture mirrors that depth and byte-copies the REAL api.js), and the
`app.handleFile` wrap is pinned by source text.

The same probe drives the `image_output` half: the cross-branch restore
guard's pure verdict (`imageOutputFromSavedValues`), the link-dimming
reconcile against a minimal fake node/graph (tests/test_image_grid_js.py's
shape for the sibling it was copied from), and `attach()` end to end on a
fake Save Image node."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SAVE_IMAGE_JS = REPO_ROOT / "web" / "eps_image" / "save_image.js"
EXTENSION_JS = REPO_ROOT / "web" / "eps_image.js"
API_JS = REPO_ROOT / "web" / "lora_library" / "api.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

#: (file name, expected token) -- the M1 grammar read from the END of the stem.
TOKEN_CASES = [
    ("Portrait_m2_i1_t3_00001_.png", "m2_i1_t3"),
    ("shot_2_m1_p1_00001_.png", "m1_p1"),
    ("pair_01_m1_v2_p1_00003_.png", "m1_v2_p1"),
    ("Neon City_i2_t1_00001_.png", "i2_t1"),
    ("Alpha_t1_00001_.webp", "t1"),
    ("plain_00001_.png", None),
    ("ComfyUI_00001_.png", None),
    ("m2_i1_t3.png", None),  # no counter: not a Save Image file name
    ("", None),
]

#: (token, multipliers [{soloValue}], expected verdict)
DECIDE_CASES = [
    ("m1_p1", [{"soloValue": ""}], "apply"),
    ("m1_p1", [{"soloValue": "m1_p1"}], "baked"),  # a baked file: leave it
    ("m1_p1", [{"soloValue": ""}, {"soloValue": ""}], "ambiguous"),
    ("m1_p1", [{"soloValue": "m9_p9"}], "none"),  # already soloed elsewhere: hands off
    ("m1_p1", [{"soloValue": ""}, {"soloValue": "m1_p1"}], "baked"),
    ("m1_p1", [], "none"),
    ("", [{"soloValue": ""}], "none"),
]

#: (saved widgets_values, expected verdict) for the cross-branch restore
#: guard: None = "main layout, leave it"; a bool = what image_output must be.
#: main serializes [filename_prefix, image_output]; the v1 branch
#: [filename_prefix, preview_only, format, bit_depth, input_color_space,
#: avif_crf] and, once ported, image_output at index 6.
V1_SIX = ["EPS", False, "png", "auto", "sRGB", 30]
GUARD_CASES = [
    ([], None),
    (["EPS"], None),  # main, pre-toggle save
    (["EPS", False], None),  # main, toggle off -- must NOT be overridden
    (["EPS", True], None),
    (V1_SIX, True),  # v1 before the port: preview_only's false must not land
    ([*V1_SIX, False], False),  # v1 after the port, toggle off
    ([*V1_SIX, True], True),
    ([*V1_SIX, None], True),  # only an explicit false is off
    ({"a": 1}, None),  # not an array: leave it
    (None, None),
    ("EPS", None),
]

PROBE_JS = """
import * as m from './extensions/comfyui-epsnodes/eps_image/save_image.js'
const out = {
  exports: {
    hasInit: typeof m.init === 'function',
    hasAttach: typeof m.attach === 'function',
    hasTokenFromFileName: typeof m.tokenFromFileName === 'function',
    hasDecideFilenameSolo: typeof m.decideFilenameSolo === 'function',
    hasImageOutputFromSavedValues: typeof m.imageOutputFromSavedValues === 'function',
    hasReconcileLinkDimming: typeof m.reconcileLinkDimming === 'function',
    hasOutputLinkIds: typeof m.outputLinkIds === 'function'
  },
  tokens: %(token_inputs)s.map((name) => m.tokenFromFileName(name)),
  verdicts: %(decide_inputs)s.map(([token, mults]) => m.decideFilenameSolo(token, mults)),
  guard: %(guard_inputs)s.map((values) => m.imageOutputFromSavedValues(values))
}

// ---- image_output link dimming (reconcileLinkDimming / outputLinkIds) ----
// Same fake shapes as tests/test_image_grid_js.py's for the sibling this
// was copied from.
const DIM = 'rgba(128,128,128,0.25)'
const OWNER_KEY = '__epsLinkColorOwner'
const OWNER = 'EPSSaveImage:image-output-off'
const RESYNC_HOOK = '__epsResyncLinkColors'

const makeLink = (id, targetId, color) => ({ id, target_id: targetId, color })
const makeGraph = (linksById, nodesById) => {
  const g = { links: linksById, getNodeById: (id) => nodesById?.[id], dirtyCount: 0 }
  g.setDirtyCanvas = () => { g.dirtyCount++ }
  return g
}
const makeSaveNode = (value, graph, linkIds, inputs) => ({
  comfyClass: 'EPSSaveImage',
  widgets: [{ name: 'filename_prefix', value: 'EPS' }, { name: 'image_output', value }],
  outputs: [{ name: 'images', links: linkIds }],
  inputs: inputs || [{ name: 'images', link: 77 }],
  graph
})

{
  const node = { outputs: [{ name: 'images', links: [1, null, 2] }, { name: 'other', links: [9] }] }
  out.dimIds = m.outputLinkIds(node)
  out.dimIdsMalformed = m.outputLinkIds({ outputs: [{ name: 'images', links: null }] })
}

// -- off dims, a second pass is a no-op, on restores EXACTLY (undefined and
// a colour someone else painted) --
{
  const linkA = makeLink(1, 100)
  const linkB = makeLink(2, 101, 'rgb(0,100,200)')
  const graph = makeGraph({ 1: linkA, 2: linkB }, {})
  const node = makeSaveNode(false, graph, [1, 2])
  m.reconcileLinkDimming(node)
  const afterDim = {
    aColor: linkA.color, aOwner: linkA[OWNER_KEY],
    bColor: linkB.color, bOwner: linkB[OWNER_KEY],
    dirty: graph.dirtyCount, tracked: [...node.__epsSaveDimmedLinkIds]
  }
  m.reconcileLinkDimming(node)
  const dirtyAfterNoop = graph.dirtyCount
  node.widgets[1].value = true
  m.reconcileLinkDimming(node)
  out.dimRoundTrip = {
    dimmedBoth: afterDim.aColor === DIM && afterDim.bColor === DIM,
    ownedWhileDimmed: afterDim.aOwner === OWNER && afterDim.bOwner === OWNER,
    trackedWhileDimmed: JSON.stringify(afterDim.tracked) === '[1,2]',
    dirtiedOnce: afterDim.dirty === 1,
    noopDoesNotRedirty: dirtyAfterNoop === afterDim.dirty,
    restoredUndefinedExactly: linkA.color === undefined,
    restoredPriorColorExactly: linkB.color === 'rgb(0,100,200)',
    ownerCleared: !(OWNER_KEY in linkA) && !(OWNER_KEY in linkB),
    trackedEmptyAfter: node.__epsSaveDimmedLinkIds.size === 0,
    dirtiedAgainForRestore: graph.dirtyCount === 2
  }
}

// -- the string "false" is off too (the backend's parse_image_output rule) --
{
  const linkA = makeLink(3, 100)
  const graph = makeGraph({ 3: linkA }, {})
  const node = makeSaveNode('false', graph, [3])
  m.reconcileLinkDimming(node)
  out.dimStringFalse = linkA.color === DIM
}

// -- a link another owner has claimed is left alone, untracked, no repaint --
{
  const linkA = makeLink(4, 100, 'orange')
  linkA[OWNER_KEY] = 'EPSImageGrid:collect-only'
  const graph = makeGraph({ 4: linkA }, {})
  const node = makeSaveNode(false, graph, [4])
  m.reconcileLinkDimming(node)
  out.dimOtherOwner = {
    colorUntouched: linkA.color === 'orange',
    ownerUntouched: linkA[OWNER_KEY] === 'EPSImageGrid:collect-only',
    notTracked: node.__epsSaveDimmedLinkIds.size === 0,
    notDirtied: graph.dirtyCount === 0
  }
}

// -- the toggle's own input socket wired: no dim (value unknown at edit
// time); unwire it and the next reconcile dims --
{
  const linkA = makeLink(5, 100)
  const graph = makeGraph({ 5: linkA }, {})
  const toggleInput = { name: 'image_output', widget: { name: 'image_output' }, link: 42 }
  const node = makeSaveNode(false, graph, [5], [{ name: 'images', link: 77 }, toggleInput])
  m.reconcileLinkDimming(node)
  const wiredNoDim = linkA.color === undefined && graph.dirtyCount === 0
  toggleInput.link = null
  m.reconcileLinkDimming(node)
  out.dimWiredInput = { wiredNoDim, dimmedOnceUnwired: linkA.color === DIM }
}

// -- forceUndim (the onRemoved path) restores regardless of the widget --
{
  const linkA = makeLink(6, 100, 'green')
  const graph = makeGraph({ 6: linkA }, {})
  const node = makeSaveNode(false, graph, [6])
  m.reconcileLinkDimming(node)
  const dimmedFirst = linkA.color === DIM
  m.reconcileLinkDimming(node, { forceUndim: true })
  out.dimForceUndim = {
    dimmedFirst,
    widgetStillOff: node.widgets[1].value === false,
    restoredAnyway: linkA.color === 'green',
    ownerCleared: !(OWNER_KEY in linkA)
  }
}

// -- a link disconnected while dimmed drops out of tracking --
{
  const linkA = makeLink(7, 100)
  const linkB = makeLink(8, 101)
  const linksById = { 7: linkA, 8: linkB }
  const graph = makeGraph(linksById, {})
  const node = makeSaveNode(false, graph, [7, 8])
  m.reconcileLinkDimming(node)
  node.outputs[0].links = [7]
  delete linksById[8]
  m.reconcileLinkDimming(node)
  out.dimDisconnect = {
    aStillDimmed: linkA.color === DIM,
    droppedFromTracking: !node.__epsSaveDimmedLinkIds.has(8)
  }
}

// -- the target's resync hook: never while dimming, exactly once on
// release, after the colour is back --
{
  let calls = 0
  let colorAtCall = null
  const linkA = makeLink(9, 500, 'orange')
  const target = { [RESYNC_HOOK]: () => { calls++; colorAtCall = linkA.color } }
  const graph = makeGraph({ 9: linkA }, { 500: target })
  const node = makeSaveNode(false, graph, [9])
  m.reconcileLinkDimming(node)
  const callsWhileDimmed = calls
  node.widgets[1].value = true
  m.reconcileLinkDimming(node)
  out.dimResyncHook = {
    noCallWhileDimming: callsWhileDimmed === 0,
    calledOnceOnRestore: calls === 1,
    colorRestoredFirst: colorAtCall === 'orange'
  }
}

// -- on: a pure no-op --
{
  const linkA = makeLink(10, 100)
  const graph = makeGraph({ 10: linkA }, {})
  const node = makeSaveNode(true, graph, [10])
  m.reconcileLinkDimming(node)
  out.dimOn = { untouched: linkA.color === undefined, notDirtied: graph.dirtyCount === 0 }
}

// ---- attach(): class gate, double-attach guard, the four hooks end to end ----
{
  const foreign = { comfyClass: 'SaveImage', widgets: [{ name: 'image_output', value: false }] }
  m.attach(foreign)
  const linkA = makeLink(11, 100)
  const graph = makeGraph({ 11: linkA }, {})
  const node = makeSaveNode(true, graph, [11])
  const widget = node.widgets[1]
  let originalCallbackCalls = 0
  widget.callback = () => { originalCallbackCalls++ }
  let originalOnConfigureCalls = 0
  node.onConfigure = () => { originalOnConfigureCalls++ }
  m.attach(node)
  const wrappedCallback = widget.callback
  m.attach(node) // second nodeCreated: must not re-wrap
  const sameCallbackAfterSecondAttach = widget.callback === wrappedCallback

  // a click: litegraph sets the value, then calls the callback
  widget.value = false
  widget.callback(false)
  const dimmedOnClick = linkA.color === DIM

  // a v1-layout save restored positionally left image_output = false
  // (preview_only's value): the guard flips it back on and the dim clears
  node.onConfigure({ widgets_values: ['EPS', false, 'png', 'auto', 'sRGB', 30] })
  const guardFlippedOn = widget.value === true
  const undimmedAfterGuard = linkA.color === undefined

  // a main-layout save with the toggle off: honoured, and dimmed
  widget.value = false
  node.onConfigure({ widgets_values: ['EPS', false] })
  const mainOffHonoured = widget.value === false
  const dimmedAfterMainOff = linkA.color === DIM

  // a wire landing on the output while off is dimmed on onConnectionsChange
  const linkB = makeLink(12, 101)
  graph.links[12] = linkB
  node.outputs[0].links.push(12)
  node.onConnectionsChange(2, 0, true, linkB, node.outputs[0])
  const newWireDimmed = linkB.color === DIM

  node.onRemoved()
  out.attach = {
    foreignUntouched: !('__epsSaveConfigureWrapped' in foreign) &&
      !foreign.widgets[0].__epsSaveDimSyncInstalled,
    hooksInstalled: widget.__epsSaveDimSyncInstalled === true &&
      node.__epsSaveLinkDimConnectionsWrapped === true &&
      node.__epsSaveLinkDimCleanupWrapped === true &&
      node.__epsSaveConfigureWrapped === true,
    sameCallbackAfterSecondAttach,
    originalCallbackChained: originalCallbackCalls >= 1,
    originalOnConfigureChained: originalOnConfigureCalls === 2,
    dimmedOnClick,
    guardFlippedOn,
    undimmedAfterGuard,
    mainOffHonoured,
    dimmedAfterMainOff,
    newWireDimmed,
    restoredOnRemove: linkA.color === undefined && linkB.color === undefined,
    widgetStillOffAfterRemove: widget.value === false
  }
}

process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def save_image_api(tmp_path_factory: pytest.TempPathFactory) -> dict:
    layout = tmp_path_factory.mktemp("web_root")
    pack = layout / "extensions" / "comfyui-epsnodes"
    (pack / "eps_image").mkdir(parents=True)
    (pack / "lora_library").mkdir(parents=True)
    shutil.copyfile(SAVE_IMAGE_JS, pack / "eps_image" / "save_image.js")
    shutil.copyfile(
        SAVE_IMAGE_JS.parent / "owned_link_colour.js", pack / "eps_image" / "owned_link_colour.js"
    )
    shutil.copyfile(API_JS, pack / "lora_library" / "api.js")
    # api.js imports its sibling version.js -- copy the real one too.
    shutil.copyfile(API_JS.parent / "version.js", pack / "lora_library" / "version.js")
    scripts = layout / "scripts"
    scripts.mkdir()
    (scripts / "app.js").write_text(
        "export const app = { graph: { _nodes: [] } }\n", encoding="utf-8"
    )
    (scripts / "api.js").write_text(
        "export const api = { fetchApi: () => {}, addEventListener: () => {} }\n", encoding="utf-8"
    )
    probe = layout / "probe.mjs"
    probe.write_text(
        PROBE_JS
        % {
            "token_inputs": json.dumps([name for name, _ in TOKEN_CASES]),
            "decide_inputs": json.dumps([[t, mults] for t, mults, _ in DECIDE_CASES]),
            "guard_inputs": json.dumps([values for values, _ in GUARD_CASES]),
        },
        encoding="utf-8",
    )
    result = subprocess.run(
        [NODE, str(probe)], capture_output=True, text=True, timeout=60, cwd=layout
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def source() -> str:
    return SAVE_IMAGE_JS.read_text(encoding="utf-8")


def test_parses() -> None:
    result = subprocess.run(
        [NODE, "--check", str(SAVE_IMAGE_JS)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


def test_exports(save_image_api: dict) -> None:
    assert save_image_api["exports"] == {
        "hasInit": True,
        "hasAttach": True,
        "hasTokenFromFileName": True,
        "hasDecideFilenameSolo": True,
        "hasImageOutputFromSavedValues": True,
        "hasReconcileLinkDimming": True,
        "hasOutputLinkIds": True,
    }


def test_token_from_file_name_cases(save_image_api: dict) -> None:
    for (name, expected), got in zip(TOKEN_CASES, save_image_api["tokens"], strict=True):
        assert got == expected, f"tokenFromFileName({name!r}) -> {got!r}, wanted {expected!r}"


def test_decide_filename_solo_cases(save_image_api: dict) -> None:
    for (token, mults, expected), got in zip(DECIDE_CASES, save_image_api["verdicts"], strict=True):
        assert got == expected, (
            f"decideFilenameSolo({token!r}, {mults!r}) -> {got!r}, wanted {expected!r}"
        )


def test_handle_file_is_wrapped_once_chained_and_image_only(source: str) -> None:
    """§7.5: wrap, never replace; the original runs first (the frontend loads
    the workflow), then the fallback reads the loaded graph; only image files;
    a baked file (solo already set) is left alone; ambiguity toasts instead
    of guessing."""
    assert "const original = app?.handleFile" in source
    assert "app.handleFile = async function (file, ...rest) {" in source
    assert "const result = await original.call(this, file, ...rest)" in source
    assert "applyFilenameSolo(file)" in source
    assert "if (installed) return" in source
    assert "/^image\\//i.test(file.type || '')" in source
    assert "verdict === 'apply'" in source and "verdict === 'ambiguous'" in source
    assert "widget.callback?.(token)" in source
    # no drop-path interception, no window listeners
    assert not re.search(r"window\.addEventListener", source)
    assert "onDragDrop" not in source


# ------------------------------------------------ image_output: restore guard


def test_image_output_from_saved_values_cases(save_image_api: dict) -> None:
    for (values, expected), got in zip(GUARD_CASES, save_image_api["guard"], strict=True):
        assert got == expected, (
            f"imageOutputFromSavedValues({values!r}) -> {got!r}, wanted {expected!r}"
        )


# ------------------------------------------------- image_output: link dimming


def test_output_link_ids_reads_only_the_images_output(save_image_api: dict) -> None:
    assert save_image_api["dimIds"] == [1, 2]  # the null and the other output skipped
    assert save_image_api["dimIdsMalformed"] == []


def test_off_dims_every_images_link_and_on_restores_each_exact_prior_colour(
    save_image_api: dict,
) -> None:
    result = save_image_api["dimRoundTrip"]
    assert result["dimmedBoth"] is True
    assert result["ownedWhileDimmed"] is True
    assert result["trackedWhileDimmed"] is True
    assert result["restoredUndefinedExactly"] is True
    assert result["restoredPriorColorExactly"] is True
    assert result["ownerCleared"] is True
    assert result["trackedEmptyAfter"] is True


def test_dim_is_change_gated(save_image_api: dict) -> None:
    result = save_image_api["dimRoundTrip"]
    assert result["dirtiedOnce"] is True
    assert result["noopDoesNotRedirty"] is True
    assert result["dirtiedAgainForRestore"] is True


def test_the_string_false_is_off_like_the_backend(save_image_api: dict) -> None:
    assert save_image_api["dimStringFalse"] is True


def test_a_link_another_owner_claimed_is_left_alone(save_image_api: dict) -> None:
    result = save_image_api["dimOtherOwner"]
    assert result["colorUntouched"] is True
    assert result["ownerUntouched"] is True
    assert result["notTracked"] is True
    assert result["notDirtied"] is True


def test_no_dim_while_the_toggles_own_input_is_wired(save_image_api: dict) -> None:
    # A Number Controller BOOLEAN row could drive it: the value is only known
    # at run time, so the wire stays as it is until the socket is unwired.
    result = save_image_api["dimWiredInput"]
    assert result["wiredNoDim"] is True
    assert result["dimmedOnceUnwired"] is True


def test_force_undim_restores_even_though_the_widget_still_says_off(
    save_image_api: dict,
) -> None:
    result = save_image_api["dimForceUndim"]
    assert result["dimmedFirst"] is True
    assert result["widgetStillOff"] is True
    assert result["restoredAnyway"] is True
    assert result["ownerCleared"] is True


def test_a_link_disconnected_while_dimmed_is_dropped_from_tracking(save_image_api: dict) -> None:
    result = save_image_api["dimDisconnect"]
    assert result["aStillDimmed"] is True
    assert result["droppedFromTracking"] is True


def test_target_resync_hook_fires_once_only_on_release_after_the_colour(
    save_image_api: dict,
) -> None:
    result = save_image_api["dimResyncHook"]
    assert result["noCallWhileDimming"] is True
    assert result["calledOnceOnRestore"] is True
    assert result["colorRestoredFirst"] is True


def test_on_is_a_pure_noop(save_image_api: dict) -> None:
    result = save_image_api["dimOn"]
    assert result["untouched"] is True
    assert result["notDirtied"] is True


# ------------------------------------------------------- image_output: attach


def test_attach_gates_on_class_guards_double_attach_and_wires_the_hooks(
    save_image_api: dict,
) -> None:
    result = save_image_api["attach"]
    assert result["foreignUntouched"] is True
    assert result["hooksInstalled"] is True
    assert result["sameCallbackAfterSecondAttach"] is True
    assert result["originalCallbackChained"] is True
    assert result["originalOnConfigureChained"] is True


def test_attach_end_to_end_click_configure_guard_connect_remove(save_image_api: dict) -> None:
    result = save_image_api["attach"]
    assert result["dimmedOnClick"] is True
    assert result["guardFlippedOn"] is True  # the v1-layout save: flipped back on
    assert result["undimmedAfterGuard"] is True
    assert result["mainOffHonoured"] is True  # a main save's off is kept
    assert result["dimmedAfterMainOff"] is True
    assert result["newWireDimmed"] is True
    assert result["restoredOnRemove"] is True
    assert result["widgetStillOffAfterRemove"] is True


def test_attach_is_registered_in_the_extensions_node_created() -> None:
    extension = EXTENSION_JS.read_text(encoding="utf-8")
    assert "safely('saveImage.attach', () => saveImage.attach?.(node))" in extension
    assert "safely('saveImage.init', () => saveImage.init?.())" in extension


def test_hooks_chain_never_replace_and_are_guarded_per_instance(source: str) -> None:
    """Each installer guards on its OWN flag (never `attachedNodes`, which
    only guards `attach()` itself) and calls the original it wrapped --
    image_grid.js's installer shape, copied by hand."""
    assert "originalCallback.apply(this, args)" in source
    assert "original.apply(this, args)" in source
    assert "original.call(this, info)" in source
    assert "forceUndim: true" in source
    assert "widget.__epsSaveDimSyncInstalled" in source
    assert "node.__epsSaveLinkDimConnectionsWrapped" in source
    assert "node.__epsSaveLinkDimCleanupWrapped" in source
    assert "node.__epsSaveConfigureWrapped" in source
    assert "attachedNodes.has(node)" in source


def test_dim_follows_the_cross_file_colour_convention(source: str) -> None:
    # image_grid.js's grey, distributor.js's owner key + resync hook, our own
    # owner value; the sibling files are named so a change finds them.
    assert "'rgba(128,128,128,0.25)'" in source
    assert "'__epsLinkColorOwner'" in source
    assert "'EPSSaveImage:image-output-off'" in source
    assert "'__epsResyncLinkColors'" in source
    assert "image_grid.js" in source and "distributor.js" in source and "bypass.js" in source
    assert "new WeakMap()" in source


def test_configure_wrap_runs_the_guard_then_rederives_the_dim(source: str) -> None:
    body = source[source.index("function installConfigureSync") :]
    body = body[: body.index("\n}\n") + 3]
    assert "imageOutputFromSavedValues(info?.widgets_values)" in body
    assert body.index("imageOutputFromSavedValues") < body.index("reconcileLinkDimming(this)")
    assert "widget.callback?.(verdict)" in body
