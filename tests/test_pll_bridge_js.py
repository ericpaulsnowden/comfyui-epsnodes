"""Frontend tests for the EPS LoRA Picker's Power Lora Loader bridge
(``web/lora_library/pll_bridge.js`` -- FORMAT.md §6.13, M2).

The bridge is a minimal, deliberately DUPLICATED adaptation of
controller.js's §6.3 technique (probe-first feature detection,
addNewLoraWidget grow / tail removeWidget shrink / whole-object ``.value``
assignment). FORMAT.md §6.13 M2: controller.js is owner-validated code and
must be neither modified nor imported, so the duplication is kept honest
HERE instead -- the §6.3 MESSAGE VOCABULARY and the PROP_*/type constants
are pinned byte-identical against BOTH files, and the no-import rule is a
source assertion.

Unlike controller.js, the bridge's whole surface is drivable under Node in
a served-layout tmp dir (``test_picker_js.py``'s fixture convention, itself
from ``test_checkpoint_switcher_js.py``): ``app`` is the stubbed served
module the bridge imports, ``LiteGraph`` is a bare global the probe defines
itself mid-run (which also exercises the no-rgthree gate first), and
``writeRowsToPll`` needs only a widgets array plus the four LGraphNode
methods ``probePll`` feature-detects -- so grow/shrink/assign runs against
a fake PLL for real rather than being source-pinned.

Skips cleanly when Node isn't installed; real-rgthree behavior (an actual
Power Lora Loader receiving a Send) is for the rig, not here.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BRIDGE_JS = REPO_ROOT / "web" / "lora_library" / "pll_bridge.js"
CONTROLLER_JS = REPO_ROOT / "web" / "lora_library" / "controller.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

# ----------------------------------------------------------- shared vocabulary

#: §6.3's message texts, byte-identical in controller.js AND pll_bridge.js
#: (FORMAT.md §6.13 M2: "Probe failures disable Send with §6.3's own message
#: vocabulary"). test_vocabulary_strings_byte_identical_in_both_files pins
#: each against both sources, so drift in either file fails loudly.
MSG_NO_RGTHREE = "Install rgthree-comfy, or use EPS Apply LoRA Set instead"
MSG_SHAPE_DRIFT = "Power Lora Loader internals changed — controller disabled (v-check)"
# v0.64.0: the controller's target universe grew to both loader families
# (PLL + EPS LoRA Picker), so the shared vocabulary names both.
MSG_NO_TARGET_IN_GRAPH = (
    "No Power Lora Loader (rgthree) or EPS LoRA Picker node in this workflow yet — add one, then pick it above."
)
MSG_NO_TARGET_SELECTED = "Pick a target loader node above."

PLL_TYPE = "Power Lora Loader (rgthree)"

#: The exact constant declarations both files must carry -- the type string
#: and the dual-strength-mode node property controller.js reads.
SHARED_CONSTANT_LINES = [
    "const POWER_LORA_LOADER_TYPE = 'Power Lora Loader (rgthree)'",
    "const PROP_SHOW_STRENGTHS = 'Show Strengths'",
    "const PROP_SHOW_STRENGTHS_DUAL = 'Separate Model & Clip'",
]

PROBE_JS = """
import * as m from './extensions/comfyui-epsnodes/lora_library/pll_bridge.js'
import { app } from './scripts/app.js'

const PLL = m.POWER_LORA_LOADER_TYPE
const isRow = (w) => /^lora_\\d+$/.test(w.name)

function fakePll(id, properties = {}) {
  let counter = 0
  return {
    id,
    type: PLL,
    title: `Loader ${id}`,
    properties,
    widgets: [],
    size: [200, 100],
    dirtyCalls: 0,
    addNewLoraWidget() {
      this.widgets.push({
        name: `lora_${counter++}`,
        value: { on: true, lora: null, strength: 1, strengthTwo: null }
      })
    },
    removeWidget(widget) {
      const idx = this.widgets.indexOf(widget)
      if (idx !== -1) this.widgets.splice(idx, 1)
    },
    computeSize() {
      return [250, 150]
    },
    setDirtyCanvas() {
      this.dirtyCalls++
    }
  }
}

const out = {
  exports: {
    hasFindPllNodes: typeof m.findPllNodes === 'function',
    hasProbePll: typeof m.probePll === 'function',
    hasWriteRowsToPll: typeof m.writeRowsToPll === 'function',
    hasRowsForPll: typeof m.rowsForPll === 'function'
  },
  pllType: PLL
}

// BEFORE LiteGraph exists: the no-rgthree gate must fire first.
out.probeNoRgthree = m.probePll(null)

globalThis.LiteGraph = { registered_node_types: { [PLL]: function () {} } }

app.graph = { _nodes: [] }
out.probeNullNoCandidates = m.probePll(null)

const high = fakePll(7)
const low = fakePll(3)
app.graph = { _nodes: [high, low, { id: 5, type: 'SomeOtherNode' }] }
out.findPllOrder = m.findPllNodes().map((node) => node.id)
out.probeNullWithCandidates = m.probePll(null)
out.probeOk = m.probePll(low)
out.probeMissingApi = m.probePll({ id: 9, type: PLL, widgets: [] })

const drifted = fakePll(14)
drifted.addNewLoraWidget()
drifted.widgets[0].value = 'not-an-object'
out.probeRowDrift = m.probePll(drifted)

out.rowsForPll = {
  dualWithClip:
    m.rowsForPll([{ file: 'a.st', on: true, strength: 0.5, strength_clip: 0.25 }], true),
  dualClipNull:
    m.rowsForPll([{ file: 'a.st', on: true, strength: 0.5, strength_clip: null }], true),
  dualClipAbsent: m.rowsForPll([{ file: 'a.st', strength: 0.5 }], true),
  singleIgnoresClip:
    m.rowsForPll([{ file: 'a.st', on: false, strength: 0.5, strength_clip: 0.25 }], false),
  onDefaultsTrue: m.rowsForPll([{ file: 'a.st' }], false)
}

const single = fakePll(11)
m.writeRowsToPll(single, [
  { file: 'a/x.st', on: false, strength: 0.5, strength_clip: 0.25 },
  { file: 'y.st', on: true, strength: 1 }
])
out.writeSingle = {
  values: single.widgets.filter(isRow).map((w) => w.value),
  dirtyCalls: single.dirtyCalls,
  size: single.size
}

const dual = fakePll(12, { 'Show Strengths': 'Separate Model & Clip' })
m.writeRowsToPll(dual, [
  { file: 'a.st', on: true, strength: 0.1, strength_clip: 0.9 },
  { file: 'b.st', on: true, strength: 0.2 },
  { file: 'c.st', on: false, strength: 0.3, strength_clip: null }
])
out.writeDualGrow = dual.widgets.filter(isRow).map((w) => w.value)
m.writeRowsToPll(dual, [{ file: 'only.st', on: true, strength: 0.8, strength_clip: null }])
out.writeDualShrink = dual.widgets.filter(isRow).map((w) => w.value)

const stubborn = fakePll(13)
stubborn.removeWidget = () => {
  throw new Error('nope')
}
m.writeRowsToPll(stubborn, [
  { file: 'a.st', on: true, strength: 1 },
  { file: 'b.st', on: true, strength: 1 }
])
m.writeRowsToPll(stubborn, [{ file: 'b.st', on: true, strength: 1 }])
out.writeSpliceFallback = stubborn.widgets.filter(isRow).map((w) => w.value)

process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def bridge_api(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Runs the probe against the REAL pll_bridge.js in a served-layout tmp
    dir (see module docstring) and returns its JSON output."""
    layout = tmp_path_factory.mktemp("web_root")

    module_dir = layout / "extensions" / "comfyui-epsnodes" / "lora_library"
    module_dir.mkdir(parents=True)
    shutil.copyfile(BRIDGE_JS, module_dir / "pll_bridge.js")
    # pll_bridge.js imports only `../../../scripts/app.js` -- stub it exactly
    # as test_picker_js.py does; the probe mutates `app.graph` per scenario.
    scripts = layout / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "app.js").write_text("export const app = {}\n", encoding="utf-8")

    probe = layout / "probe.mjs"
    probe.write_text(PROBE_JS, encoding="utf-8")

    result = subprocess.run(
        [NODE, str(probe)], capture_output=True, text=True, timeout=60, cwd=layout
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def source() -> str:
    """Raw text of pll_bridge.js -- for the attribution/no-import/technique
    pins the Node probe can't express."""
    return BRIDGE_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def controller_source() -> str:
    """Raw text of controller.js -- READ ONLY, never imported or modified
    (FORMAT.md §6.13 M2) -- the other half of every vocabulary pin."""
    return CONTROLLER_JS.read_text(encoding="utf-8")


def _function_body(source_text: str, signature: str) -> str:
    """The body of a top-level ``function <signature> {`` declaration (an
    `export`/`async` prefix, if any, is not part of *signature* and does not
    need to match), up to its closing brace at column 0 --
    test_checkpoint_switcher_js.py's identical helper."""
    start_match = re.search(re.escape(f"function {signature} {{") + r"\n", source_text)
    assert start_match, f"function {signature} {{ not found"
    start = start_match.end()
    end_match = re.search(r"\n\}\n", source_text[start:])
    assert end_match, f"function {signature}'s closing brace not found"
    return source_text[start : start + end_match.start()]


# ------------------------------------------------------------- parses / exports


def test_pll_bridge_js_parses() -> None:
    """`node --check` -- the file must at minimum be valid ES module syntax."""
    result = subprocess.run(
        [NODE, "--check", str(BRIDGE_JS)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


def test_module_exports_the_bridge_surface(bridge_api: dict) -> None:
    """picker.js's Send row consumes exactly these four plus the type
    constant -- nothing else is public."""
    assert bridge_api["exports"] == {
        "hasFindPllNodes": True,
        "hasProbePll": True,
        "hasWriteRowsToPll": True,
        "hasRowsForPll": True,
    }


def test_pll_type_string_is_the_exact_rgthree_spelling(bridge_api: dict) -> None:
    assert bridge_api["pllType"] == PLL_TYPE


# ------------------------------------------------------------------ probePll


def test_probe_without_rgthree_installed(bridge_api: dict) -> None:
    assert bridge_api["probeNoRgthree"] == {
        "ok": False,
        "code": "no-rgthree",
        "message": MSG_NO_RGTHREE,
    }


def test_probe_null_node_distinguishes_empty_graph_from_no_selection(bridge_api: dict) -> None:
    """The same two codes/messages controller.js's probeTarget() yields for
    a null node: no candidates at all vs candidates present but unpicked."""
    assert bridge_api["probeNullNoCandidates"] == {
        "ok": False,
        "code": "no-target-in-graph",
        "message": MSG_NO_TARGET_IN_GRAPH,
    }
    assert bridge_api["probeNullWithCandidates"] == {
        "ok": False,
        "code": "no-target-selected",
        "message": MSG_NO_TARGET_SELECTED,
    }


def test_probe_ok_reports_row_count(bridge_api: dict) -> None:
    assert bridge_api["probeOk"] == {
        "ok": True,
        "code": "ok",
        "message": "Ready — target has 0 rows.",
        "rowCount": 0,
    }


def test_probe_shape_drift_on_missing_api_and_on_drifted_row_values(bridge_api: dict) -> None:
    """Both drift flavors: a PLL-typed node missing addNewLoraWidget/etc.,
    and a row-shaped widget name whose value isn't rgthree's row object."""
    for key in ("probeMissingApi", "probeRowDrift"):
        assert bridge_api[key] == {
            "ok": False,
            "code": "shape-drift",
            "message": MSG_SHAPE_DRIFT,
        }, key


def test_find_pll_nodes_sorted_ascending_and_type_filtered(bridge_api: dict) -> None:
    """Graph order was [7, 3, non-PLL]; the combo order is ascending id with
    the non-PLL dropped (§6.13 M2)."""
    assert bridge_api["findPllOrder"] == [3, 7]


# ----------------------------------------------------------------- rowsForPll


def test_rows_for_pll_dual_mode_strength_two(bridge_api: dict) -> None:
    """Dual mode: strengthTwo carries strength_clip, falling back to
    strength when strength_clip is null (the picker's shape) or absent."""
    rows = bridge_api["rowsForPll"]
    expected = [{"on": True, "lora": "a.st", "strength": 0.5, "strengthTwo": 0.25}]
    assert rows["dualWithClip"] == expected
    follows = [{"on": True, "lora": "a.st", "strength": 0.5, "strengthTwo": 0.5}]
    assert rows["dualClipNull"] == follows
    assert rows["dualClipAbsent"] == follows


def test_rows_for_pll_single_mode_nulls_strength_two(bridge_api: dict) -> None:
    """Single mode: strengthTwo is null even when the row carries a
    strength_clip -- exactly applySetToTarget()'s assignment."""
    assert bridge_api["rowsForPll"]["singleIgnoresClip"] == [
        {"on": False, "lora": "a.st", "strength": 0.5, "strengthTwo": None}
    ]


def test_rows_for_pll_preserves_on_and_defaults_it_true(bridge_api: dict) -> None:
    """`on` preserved (§6.13: an off row is sent off, not dropped or
    forced on); absent defaults true, absent strength defaults 1."""
    assert bridge_api["rowsForPll"]["singleIgnoresClip"][0]["on"] is False
    assert bridge_api["rowsForPll"]["dualWithClip"][0]["on"] is True
    assert bridge_api["rowsForPll"]["onDefaultsTrue"] == [
        {"on": True, "lora": "a.st", "strength": 1, "strengthTwo": None}
    ]


# ------------------------------------------------------------- writeRowsToPll


def test_write_rows_grows_assigns_and_resizes_in_single_mode(bridge_api: dict) -> None:
    """An empty fake PLL without the dual property: two rows grown via
    addNewLoraWidget(), whole-object values with strengthTwo null, then the
    computeSize/setDirtyCanvas finish."""
    got = bridge_api["writeSingle"]
    assert got["values"] == [
        {"on": False, "lora": "a/x.st", "strength": 0.5, "strengthTwo": None},
        {"on": True, "lora": "y.st", "strength": 1, "strengthTwo": None},
    ]
    assert got["dirtyCalls"] >= 1
    assert got["size"] == [250, 150]  # grew to computeSize(), never shrank


def test_write_rows_dual_mode_then_shrinks_from_the_tail(bridge_api: dict) -> None:
    """The dual-property fake: strengthTwo populated per row (clip, absent,
    null all covered), then a second write shrinks 3 rows to 1."""
    assert bridge_api["writeDualGrow"] == [
        {"on": True, "lora": "a.st", "strength": 0.1, "strengthTwo": 0.9},
        {"on": True, "lora": "b.st", "strength": 0.2, "strengthTwo": 0.2},
        {"on": False, "lora": "c.st", "strength": 0.3, "strengthTwo": 0.3},
    ]
    assert bridge_api["writeDualShrink"] == [
        {"on": True, "lora": "only.st", "strength": 0.8, "strengthTwo": 0.8}
    ]


def test_write_rows_splices_when_remove_widget_throws(bridge_api: dict) -> None:
    """The try/splice fallback: a removeWidget() that throws still shrinks
    the widgets array (controller.js's applySetToTarget() fallback)."""
    assert bridge_api["writeSpliceFallback"] == [
        {"on": True, "lora": "b.st", "strength": 1, "strengthTwo": None}
    ]


# --------------------------------------------- §6.3 vocabulary / provenance pins


def test_vocabulary_strings_byte_identical_in_both_files(
    source: str, controller_source: str
) -> None:
    """THE §6.13 M2 vocabulary contract: every §6.3 message text appears
    verbatim in controller.js AND pll_bridge.js, so an edit to either side
    alone fails here instead of quietly forking the vocabulary."""
    messages = (MSG_NO_RGTHREE, MSG_SHAPE_DRIFT, MSG_NO_TARGET_IN_GRAPH, MSG_NO_TARGET_SELECTED)
    for message in messages:
        assert f"'{message}'" in source, f"bridge lost: {message!r}"
        assert f"'{message}'" in controller_source, f"controller lost: {message!r}"


def test_shared_constants_byte_identical_in_both_files(
    source: str, controller_source: str
) -> None:
    """The exact type string and the dual-mode PROP_* names/values -- the
    bridge must read the SAME node property controller.js reads."""
    for line in SHARED_CONSTANT_LINES:
        assert line in source, f"bridge lost: {line!r}"
        assert line in controller_source, f"controller lost: {line!r}"


def test_write_technique_matches_controller(source: str) -> None:
    """The §6.3 grow/shrink/assign technique, structurally: rgthree's own
    row-add method, the tail removeWidget with the splice fallback, the
    runaway-loop cap, and the resize/redraw finish."""
    assert "node.addNewLoraWidget()" in source
    assert "MAX_ROW_ADJUST_STEPS = 500" in source
    body = _function_body(source, "writeRowsToPll(node, rows)")
    assert body.count("MAX_ROW_ADJUST_STEPS") == 2  # both the grow and shrink loops
    assert "node.removeWidget(widget)" in body
    assert "node.widgets.splice(idx, 1)" in body.split("node.removeWidget(widget)", 1)[1]
    assert "const computed = node.computeSize()" in body
    assert "node.setDirtyCanvas(true, true)" in body


def test_attribution_comment_and_no_controller_import(source: str) -> None:
    """The provenance header names controller.js; the module NEVER imports
    it (FORMAT.md §6.13 M2: owner-validated code, duplication is the
    cheaper risk)."""
    assert "controller.js" in source
    assert "deliberately duplicated, not imported" in source
    assert "from './controller.js'" not in source
    assert re.findall(r"^import .*$", source, flags=re.MULTILINE) == [
        "import { app } from '../../../scripts/app.js'"
    ]


class TestPickerTargetFamilyV0640:
    """v0.64.0 (owner ask 2026-08-14: "eps lora state controller ... should
    be able to control eps lora picker ... even when the nodes are
    nested"): the EPS LoRA Picker joins the controller's target universe as
    a second family, and every discovery walk covers subgraphs."""

    def test_candidates_walk_the_whole_workflow_with_path_ids(
        self, controller_source: str
    ) -> None:
        body = _function_body(controller_source, "findTargetCandidates()")
        assert "api.walkLiveNodes(app.graph)" in body
        assert "if (!familyOf(node)) continue" in body
        assert "label: `${node.title || node.type} #${pathId}`" in body
        # ...and label resolution round-trips through the path-aware finder.
        resolve = _function_body(controller_source, "resolveTargetNode(label)")
        assert "api.findByPathId(app.graph, id)" in resolve
        assert "familyOf(node)" in resolve
        # The label regex accepts "#3:2"-style paths.
        assert "/#(-?\\d+(?::-?\\d+)*)\\s*$/" in controller_source

    def test_picker_probe_capture_apply_reuse_the_selection_widget(
        self, controller_source: str
    ) -> None:
        """The picker's whole state IS its hidden `selection` JSON widget
        (FORMAT.md §6.13), whose row shape equals the controller's internal
        shape -- capture parses it, apply rewrites it (scope preserved: it
        is per-workflow VIEW state, not part of a saved state)."""
        rows = _function_body(controller_source, "pickerRowsOf(node)")
        assert "JSON.parse" in rows
        assert "on: row.on !== false" in rows
        capture = _function_body(controller_source, "captureRows(node, { debugCapture = false } = {})".replace("async function ", ""))
        assert capture is not None
        assert "if (familyOf(node) === 'picker') return pickerRowsOf(node)" in controller_source
        apply_fn = _function_body(controller_source, "applySetToPicker(node, desired)")
        assert "if (typeof parsed?.scope === 'string') scope = parsed.scope" in apply_fn
        assert "widget.value = JSON.stringify({ scope, loras })" in apply_fn
        # The panel renders FROM the widget -- poke picker.js's reload seam.
        assert "node.__epsLpReload?.()" in apply_fn

    def test_rgthree_gate_moved_inside_the_pll_branch(self, controller_source: str) -> None:
        """A picker target must keep working on a machine WITHOUT rgthree:
        probeTarget dispatches to the picker probe BEFORE the rgthree gate,
        and probeTargets no longer gates up front at all."""
        probe = _function_body(controller_source, "probeTarget(node)")
        picker_dispatch = probe.index("if (familyOf(node) === 'picker') return probePickerTarget(node)")
        rgthree_gate = probe.index("if (!isRgthreeInstalled()) {")
        assert picker_dispatch < rgthree_gate
        multi = _function_body(controller_source, "probeTargets(nodes)")
        assert "isRgthreeInstalled" not in multi

    def test_all_targets_label_renamed_with_legacy_spelling_accepted(
        self, controller_source: str
    ) -> None:
        """"All loaders (N)" now spans both families; the sticky value in a
        pre-v0.64.0 save says "All Power Lora Loaders (N)" and must keep
        meaning "all of them" -- the regex accepts both spellings."""
        assert "const ALL_TARGETS_LABEL_PREFIX = 'All loaders'" in controller_source
        assert (
            "const ALL_TARGETS_RE = /^All (?:Power Lora )?[Ll]oaders \\(\\d+\\)$/"
            in controller_source
        )

    def test_push_receivers_and_ascending_order_are_nested_aware(
        self, controller_source: str
    ) -> None:
        body = _function_body(controller_source, "findApplySetNodes()")
        assert "walkLiveNodes(app.graph)" in body
        # Path ids order segment-numerically for All-capture and composites.
        cmp_body = _function_body(controller_source, "comparePathIds(a, b)")
        assert "split(':').map(Number)" in cmp_body
        assert "comparePathIds(a.id, b.id)" in _function_body(
            controller_source, "pllAscendingIndex(node)"
        )


class TestStateGroupsV0650:
    """§4.2 (v0.65.0, owner ask 2026-08-14: "the same ability to add a # to
    the left row and create groups as the lora notebooks. Same drag and
    drop etc."): the controller's left pane gains the Notebook's grouping
    -- `#`-named group creation, headers with tap-to-collapse, pointer
    drag for rows and whole groups, an armed group-remove ✕."""

    def test_hash_name_creates_a_group_not_a_state(self, controller_source: str) -> None:
        click = controller_source.split("_onCaptureClick() {", 1)[1].split("\n      }\n", 1)[0]
        assert "if (isCategoryNameInput(this._w.name?.value))" in click
        assert "this._runAction('New Group', () => this._doNewCategory())" in controller_source
        # the notebook's parse, verbatim by hand
        assert "function isCategoryNameInput(rawName)" in controller_source
        assert "function categoryNameFromInput(rawName)" in controller_source

    def test_capture_button_is_blocked_not_disabled(self, controller_source: str) -> None:
        """Group creation is pure layout and must work with NO loader in
        the graph -- so captureBtn left the probe-driven disable loop
        (its _doCapture already probes first and toasts; the picker Send
        button's blocked-not-disabled precedent)."""
        assert "this._actionButtons = [this._w.updateBtn, this._w.deleteBtn]" in controller_source

    def test_drag_uses_capture_phase_window_listeners_and_a_threshold(
        self, controller_source: str
    ) -> None:
        """notebook.js's exact posture: pointerdown + movement threshold
        decides click-vs-drag (a press that never travels stays a click ->
        _onSetPicked, so select-vs-apply is untouched), and the window
        listeners are CAPTURE-phase -- the 2026-07-30 Vue-renderer lesson."""
        assert "STATE_DRAG_THRESHOLD_PX" in controller_source
        drag = controller_source.split("_onStateRowPointerDown(event, source) {", 1)[1].split("\n      _computeStateDropTarget", 1)[0]
        assert "window.addEventListener('pointermove', onMove, { capture: true })" in drag
        assert "this._guarded('state row click', () => this._onSetPicked(drag.label))" in drag
        # a plain tap on a header collapses its group
        assert "this._guarded('group collapse', () => this._toggleCategoryCollapsed(drag.category))" in drag

    def test_moves_are_layout_edits_healed_by_the_server(self, controller_source: str) -> None:
        finish = controller_source.split("_finishStateDrag(drag) {", 1)[1].split("\n      }\n", 1)[0]
        assert "pullSlugFromLayout(layout, drag.slug)" in finish
        save = controller_source.split("async _saveLayout() {", 1)[1].split("\n      }\n", 1)[0]
        # the server's HEALED response replaces the cache -- a stale client
        # edit can never vanish a set from the pane
        assert "this._layoutCache = normalizeLayoutClient(data?.layout)" in save
        assert "this._refreshSetsCache().catch(() => {})" in save  # failure snaps back

    def test_group_delete_moves_states_out_never_deletes_them(self, controller_source: str) -> None:
        body = controller_source.split("_deleteCategory(category) {", 1)[1].split("\n      }\n", 1)[0]
        assert "layout.order[UNCATEGORIZED] = [...(layout.order[UNCATEGORIZED] || []), ...orphans]" in body
        assert "api.postJson" not in body.replace("this._saveLayout()", "")  # only the layout changes


# ------------------------------------------ v0.67.2: controller layout/poll/drag/name


def _method_body(source_text: str, signature: str) -> str:
    """The body of an INDENTED class method ``      <signature> {`` up to the
    next method at the same indent -- controller.js's node class methods
    are not top-level functions, so `_function_body` cannot find them."""
    head = f"      {signature} {{\n"
    start = source_text.index(head) + len(head)
    end = re.search(r"\n      \}\n", source_text[start:])
    assert end, f"{signature}: closing brace not found"
    return source_text[start : start + end.start()]


def test_layout_token_bumps_in_save_and_guards_the_poll(controller_source: str) -> None:
    """Owner report 2026-08-20 (a reorder "moved, then moved back, then showed
    up where I had moved them"): the sets-poll's layout GET used to paint
    the OLD server layout over an optimistic drag before the POST landed.
    The token is bumped in `_saveLayout` (every local edit calls it) and
    `_refreshSetsCache` snapshots it BEFORE its awaits, discarding a
    response the token outran or one landing mid-save."""
    save = _method_body(controller_source, "async _saveLayout()")
    assert save.lstrip().startswith("this._layoutToken++")
    assert "if (this._layoutSaveInFlight) {" in save
    assert "this._layoutSaveQueued = true" in save
    assert "} while (this._layoutSaveQueued)" in save
    assert "if (token === this._layoutToken) {" in save
    refresh = _method_body(controller_source, "async _refreshSetsCache()")
    assert refresh.index("const token = this._layoutToken") < refresh.index(
        "await api.getJson('/lora_library/sets')"
    )
    assert refresh.count("if (this._layoutSaveInFlight || token !== this._layoutToken) return") == 2
    assert "if (signature === this._layoutSignature) return" in refresh


def test_state_list_never_rebuilds_under_an_active_drag(controller_source: str) -> None:
    """A poll landing mid-gesture replaced the dragged row (and its pointer
    capture) -- the render defers and the pointerup/cancel paths flush,
    AFTER the drag is nulled so the drop's own render is never deferred."""
    render = _method_body(controller_source, "_renderStateList()")
    assert "if (this._stateDrag?.active) {" in render
    assert "this._renderAfterDrag = true" in render
    assert "_flushDeferredRender()" in controller_source
    pointerdown = _method_body(controller_source, "_onStateRowPointerDown(event, source)")
    on_up = pointerdown.split("const onUp = (upEvent) => {", 1)[1]
    on_up = on_up.split("const onCancel", 1)[0]
    assert on_up.index("this._stateDrag = null") < on_up.index("_finishStateDrag(drag)")
    assert "this._flushDeferredRender()" in on_up
    on_cancel = pointerdown.split("const onCancel = (cancelEvent) => {", 1)[1]
    on_cancel = on_cancel.split("function detach()", 1)[0]
    assert "this._flushDeferredRender()" in on_cancel


def test_poll_repaints_are_change_gated(controller_source: str) -> None:
    """The 4s poll tore every row down twice per tick for nothing; the sets
    apply and the probe/status writes now compare a signature first."""
    apply = _method_body(controller_source, "_applySetsResponse(data)")
    assert "const signature = JSON.stringify(this._setsCache)" in apply
    assert "if (signature === this._setsSignature) return" in apply
    probe = _method_body(controller_source, "_probeAndUpdateStatus()")
    assert "const probeKey = `${probe.ok ? 1 : 0}|${probe.message}`" in probe
    assert "if (probeKey === this._lastProbeKey) return" in probe
    # the probe itself still runs every beat (a deleted loader must be noticed)
    assert probe.index("probeTargets(targets)") < probe.index("probeKey")
    disarm = _method_body(controller_source, "_disarmDeleteButton()")
    assert "if (this._lastProbe) button.disabled = !this._lastProbe.ok" in disarm


def test_name_field_clears_through_the_widget_callback(controller_source: str) -> None:
    """Owner report 2026-08-20: after `# name` made a group, "the '# name'
    element also still shows". Clearing `.value` only repaints on the next
    canvas draw and the Vue input keeps its own copy until the callback
    fires -- every clear now goes through one helper doing value +
    callback + dirty (rig-verified under the Vue renderer)."""
    helper = _method_body(controller_source, "_clearNameField()")
    assert "widget.value = ''" in helper
    assert "widget.callback?.('')" in helper
    assert "this.setDirtyCanvas(true, true)" in helper
    assert "if (this._w.name) this._w.name.value = ''" not in controller_source
    new_cat = _method_body(controller_source, "async _doNewCategory()")
    assert "this._clearNameField()" in new_cat
    assert "this._collapsedCategories.delete(name)" in new_cat

