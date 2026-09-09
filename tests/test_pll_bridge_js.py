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


#: `_beginCategoryRename`'s signature (2026-08-28: gained a 2nd, defaulted
#: `initialText` param for the tab-switch draft restore) -- shared by both
#: `_method_body()` lookups below, so it is spelled out exactly once.
BEGIN_CATEGORY_RENAME_SIGNATURE = "_beginCategoryRename(category, initialText = category)"


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
    # v0.68.1: the change-gated apply moved into `_applyLayoutResponse` so
    # the shared poller and the per-node refetch share ONE guarded path;
    # the behavior (token re-check + signature gate) is unchanged.
    assert "this._applyLayoutResponse(layoutData, token)" in refresh
    apply_layout = _method_body(controller_source, "_applyLayoutResponse(layoutData, token)")
    assert "if (this._layoutSaveInFlight || token !== this._layoutToken) return" in apply_layout
    assert "if (signature === this._layoutSignature) return" in apply_layout


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



# ------------------------------------------ v0.68.1: perf + bug round (2026-08-21)


def test_shared_poller_replaces_the_per_node_heartbeat_poll(controller_source: str) -> None:
    """N controllers each polled sets + layout every 4 s from `_heartbeat()`
    -- draw-driven (2N requests per tick on a busy canvas, NONE under the Vue
    renderer), no shared cache. Now ONE module-level interval, one in-flight
    fetch pair for every live controller, paused while hidden, stopped with
    the last controller; CRUD is event-driven so the poll lengthened."""
    assert "const SETS_POLL_MS = 15000" in controller_source
    heartbeat = _method_body(controller_source, "_heartbeat()")
    assert "_refreshSetsCache" not in heartbeat
    assert "SETS_POLL_MS" not in heartbeat
    assert controller_source.count("setInterval(") == 1
    register = _function_body(controller_source, "registerController(node)")
    assert "liveControllers.add(node)" in register
    assert "if (!sharedPollTimer) {" in register
    assert "if (document.hidden) return" in register
    assert "scheduleSharedSetsRefresh()" in register
    unregister = _function_body(controller_source, "unregisterController(node)")
    assert "clearInterval(sharedPollTimer)" in unregister
    shared = _function_body(controller_source, "sharedSetsRefresh({ force = false } = {})")
    assert "if (sharedFetch) {" in shared  # one in-flight promise shared by everyone
    assert "if (force) sharedRefetchQueued = true" in shared
    run = _function_body(controller_source, "runSharedSetsFetch()".replace("async ", ""))
    # the v0.67.2 guards, per node: tokens snapshotted BEFORE any await
    assert run.index("const tokens = new Map(live().map((node) => [node, node._layoutToken]))") < run.index(
        "await api.getJson('/lora_library/sets')"
    )
    assert run.count("await api.getJson(") == 2  # sets once, layout once
    assert "node._applySetsResponse(setsData)" in run
    assert "node._applyLayoutResponse(layoutData, tokens.get(node))" in run
    assert "tokens.get(node) === node._layoutToken" in run
    # explicit refetch paths still have the per-node method
    assert "async _refreshSetsCache()" in controller_source
    save = _method_body(controller_source, "async _saveLayout()")
    assert "this._refreshSetsCache().catch(() => {})" in save


def test_controllers_subscribe_to_sets_changed_and_unsubscribe_on_remove(
    controller_source: str,
) -> None:
    """Controllers only DISPATCHED `lora_library:sets-changed`; nobody
    listened, so a capture in one controller reached the others on the next
    poll at best (never, under Vue). Per-node, capture-phase, removed in
    onRemoved, coalesced into the shared refresh."""
    sub = _method_body(controller_source, "_subscribeSetsChanged()")
    assert "if (this._onSetsChanged) return" in sub  # once per node
    assert "window.addEventListener('lora_library:sets-changed', this._onSetsChanged, { capture: true })" in sub
    assert "scheduleSharedSetsRefresh()" in sub
    unsub = _method_body(controller_source, "_unsubscribeSetsChanged()")
    assert "window.removeEventListener('lora_library:sets-changed', this._onSetsChanged, { capture: true })" in unsub
    added = _method_body(controller_source, "onAdded()")
    assert "this._subscribeSetsChanged()" in added
    assert "registerController(this)" in added
    assert "this._refreshSetsCache()" not in added
    assert "this._removed = false" in added


def test_on_removed_tears_down_drag_listeners_subscription_and_poller(controller_source: str) -> None:
    """`onRemoved` only cleared the delete-arm timer: an in-flight row drag's
    three capture-phase window listeners leaked, and late sets/layout
    responses rendered into the detached pane after a tab switch."""
    removed = _method_body(controller_source, "onRemoved()")
    assert "this._removed = true" in removed
    assert "this._stateDrag?.cleanup?.()" in removed
    assert "this._stateDrag = null" in removed
    assert "this._unsubscribeSetsChanged()" in removed
    assert "unregisterController(this)" in removed
    assert "clearTimeout(this._w.deleteBtn?._armTimer)" in removed  # the old cleanup survives
    apply_sets = _method_body(controller_source, "_applySetsResponse(data)")
    assert apply_sets.lstrip().startswith("if (this._removed) return")
    render = _method_body(controller_source, "_renderStateList()")
    assert "if (this._removed) return" in render
    apply_layout = _method_body(controller_source, "_applyLayoutResponse(layoutData, token)")
    assert apply_layout.lstrip().startswith("if (this._removed) return")


def test_target_combo_display_never_walks_the_graph(controller_source: str) -> None:
    """ComboWidget's per-draw `_displayValue` calls `options.values()` when
    no `options.getOptionLabel` is set (installed bundle) -- for the target
    combo that was `findTargetCandidates()` -> `api.walkLiveNodes` on EVERY
    draw. The value IS the label, so an identity mapper makes each draw O(1)
    while `values` stays a function (rebuilt on open, both renderers)."""
    build = _method_body(controller_source, "_buildWidgets()")
    target = build.split("this._w.target = this.addWidget(", 1)[1].split("\n        )\n", 1)[0]
    assert "values: () => this._targetComboValues()" in target
    assert "getOptionLabel: (value) => (value == null ? '' : String(value))" in target


def test_layout_edits_are_gated_on_a_loaded_layout(controller_source: str) -> None:
    """DATA LOSS: a drag / `#` group / group delete made before the first
    successful layout GET (or after a failed one) POSTed the EMPTY default
    layout (`normalizeLayoutClient(null)`) as a full replace and wiped every
    shared group. `_layoutLoaded` flips on a successful GET or POST
    response; an edit on an unloaded layout first awaits a fresh GET and
    applies it, then edits on top; a failed GET REFUSES the edit."""
    assert "this._layoutLoaded = false" in _method_body(controller_source, "constructor(title = NODE_TITLE)")
    apply_layout = _method_body(controller_source, "_applyLayoutResponse(layoutData, token)")
    assert "this._layoutLoaded = true" in apply_layout
    save = _method_body(controller_source, "async _saveLayout()")
    assert "this._layoutLoaded = true" in save
    ensure = _method_body(controller_source, "async _ensureLayoutLoaded()")
    assert ensure.lstrip().startswith("if (this._layoutLoaded) return true")
    assert "await api.getJson(LAYOUT_ROUTE)" in ensure
    assert "this._applyLayoutResponse(layoutData, token)" in ensure
    assert "this._setStatusText(MSG_LAYOUT_NOT_LOADED)" in ensure
    assert "this._toast('warn', NODE_TITLE, MSG_LAYOUT_NOT_LOADED)" in ensure
    assert "api.postJson" not in ensure  # refuse, never post the default
    assert "const MSG_LAYOUT_NOT_LOADED = 'Group layout not loaded yet — try again.'" in controller_source
    with_loaded = _method_body(controller_source, "_withLoadedLayout(label, edit)")
    assert "if (this._layoutLoaded) {" in with_loaded
    assert "this._ensureLayoutLoaded()" in with_loaded
    # every mutation path goes through the gate
    for signature in ("_finishStateDrag(drag)", "_deleteCategory(category)", "_commitCategoryRename()"):
        assert "this._withLoadedLayout(" in _method_body(controller_source, signature), signature
    assert "if (!(await this._ensureLayoutLoaded())) return" in _method_body(
        controller_source, "async _doNewCategory()"
    )


def test_group_headers_rename_in_place_on_double_click(controller_source: str) -> None:
    """Owner ask: controller groups "as identical as possible" to the
    Notebook. Double-click a header -> inline <input> over the label; Enter
    commits, Escape cancels, blur commits; rename = key in `layout.order` +
    entry in `layout.categories`, collapse follows, one `_saveLayout`;
    empty/duplicate names refused on the status line."""
    header = _method_body(controller_source, "_buildCategoryHeader(category)")
    assert "header.addEventListener('dblclick'" in header
    assert "this._beginCategoryRename(category)" in header
    # 2026-08-28: gained a 2nd, defaulted param (initialText) for the
    # tab-switch draft restore below -- the call site above stays 1-arg.
    begin = _method_body(controller_source, BEGIN_CATEGORY_RENAME_SIGNATURE)
    assert "const header = this._headerElOf(category)" in begin  # the CURRENT element, post re-render
    assert "className: 'llsc-inline-rename'" in begin
    assert "input.value = initialText" in begin
    assert "if (event.key === 'Enter') {" in begin
    assert "} else if (event.key === 'Escape') {" in begin
    assert "input.addEventListener('blur'" in begin
    assert "event.stopPropagation()" in begin  # keys never reach the canvas shortcuts
    commit = _method_body(controller_source, "_commitCategoryRename()")
    assert "const to = (rename.inputEl.value || '').trim()" in commit
    assert "this._setStatusText('Enter a name for this group.')" in commit
    assert 'this._setStatusText(`A group named "${to}" already exists.`)' in commit
    assert "layout.categories = layout.categories.map((c) => (c === from ? to : c))" in commit
    assert "layout.order[to] = layout.order[from] || []" in commit
    assert "delete layout.order[from]" in commit
    # Task 3 (2026-08-25): the migration now ALSO writes through to the
    # persisted `Collapsed groups` property -- see TestCollapsedGroupsV0800.
    assert "if (this._collapsedCategories.delete(from)) {" in commit
    assert "this._collapsedCategories.add(to)" in commit
    assert commit.count("this._saveLayout()") == 1
    # the editor survives the poll: no rebuild under an open rename
    render = _method_body(controller_source, "_renderStateList()")
    assert "if (this._categoryRename) {" in render
    assert ".llsc-inline-rename {" in controller_source


def test_noop_drop_skips_the_post(controller_source: str) -> None:
    """A state dropped back onto its own slot is not an edit -- no render,
    no full-replace POST (the Notebook's isNoopMove/isNoopCategoryMove)."""
    finish = _method_body(controller_source, "_finishStateDrag(drag)")
    assert "if (this._isNoopStateDrop(drag)) return" in finish
    noop = _method_body(controller_source, "_isNoopStateDrop(drag)")
    assert "const plan = this._groupedRows()" in noop
    assert "return target.category === category && next == null" in noop
    assert "const cats = this._layoutCache.categories" in noop


# ---------------------------------------------------------------------------
# v0.68.1 perf/polish round (audit 2026-08-20) -- pll_bridge.js pins ONLY.
# ---------------------------------------------------------------------------


def test_root_only_find_helper_is_marked_unused_by_the_picker(source: str) -> None:
    """Since v0.64.0 picker.js's findSendCandidates() walks the whole
    workflow itself; the registry's `find` key is gone (v0.68.1), so
    findPllNodes and probePll(null)'s null leg are reachable from nothing
    but this file's own probe. They stay ONLY because the pins above
    (`hasFindPllNodes`, `findPllOrder`, `probeNullNoCandidates` /
    `probeNullWithCandidates`) pin them -- remove together."""
    block = source.split("export function findPllNodes()", 1)[0]
    assert "UNUSED BY THE PICKER, marked for removal" in block[-1400:]
    picker = (REPO_ROOT / "web" / "lora_library" / "picker.js").read_text(encoding="utf-8")
    assert "pll.findPllNodes" not in picker
    assert "pll.probePll(null" not in picker


# ------------------------------------------------- v0.72.1: click keeps scroll, # feedback


def test_state_list_rebuild_restores_scroll_and_select_only_repaints(
    controller_source: str,
) -> None:
    """Owner report 2026-08-22: clicking a state scrolled a long list away.
    The rebuild remembers/restores scrollTop, and a plain selection no
    longer rebuilds at all -- it toggles the active class on the rows in
    the DOM (`_repaintSelection`), falling back to the rebuild only when
    the selected row is not in the DOM."""
    render = _method_body(controller_source, "_renderStateList()")
    assert "const scrollTop = listEl.scrollTop" in render
    save_at = render.index("const scrollTop = listEl.scrollTop")
    assert save_at < render.index("listEl.replaceChildren()")
    assert "if (scrollTop) listEl.scrollTop = scrollTop" in render
    repaint = _method_body(controller_source, "_repaintSelection()")
    assert "row.classList.toggle('llsc-row-active', active)" in repaint
    assert "if (selected && !seen) this._renderStateList()" in repaint
    silently = _method_body(controller_source, "_setSetValueSilently(label)")
    assert "this._repaintSelection()" in silently
    assert "this._renderStateList()" not in silently


def test_new_group_is_announced_with_toasts(controller_source: str) -> None:
    """`# name` creation, duplicate and missing-name outcomes all toast --
    the status line is hidden by default, so the flow used to be silent."""
    body = _method_body(controller_source, "async _doNewCategory()")
    created = "this._toast('info', NODE_TITLE, `Group \"${name}\" created"
    exists = "this._toast('warn', NODE_TITLE, `A group named \"${name}\" already exists.`)"
    assert created in body
    assert exists in body
    assert "Enter a group name after the #" in body



# ------------------------------------------------ library-on-a-NAS round (2026-08-22)
# controller.js pins ONLY (the controller's pins live here by convention).
# Owner: the Notebook shares between his computers (absolute NAS file per
# node) but the controller's states did NOT -- they live in the LIBRARY
# FOLDER (`<library>/sets`), still the pack's default LOCAL folder on his
# machines; "Surface it just like for the prompt library." FORMAT.md §6.3.

API_JS = REPO_ROOT / "web" / "lora_library" / "api.js"
VERSION_JS = REPO_ROOT / "web" / "lora_library" / "version.js"
# Browse… round (2026-08-22): controller.js now imports the Notebook's folder
# picker (`pickServerFolder`) and the Settings dialog's library-folder POST
# (`setLibraryDir`); settings.js imports path_heal.js. All three must ride
# along in the served layout -- and must themselves IMPORT under Node.
NOTEBOOK_JS = REPO_ROOT / "web" / "lora_library" / "notebook.js"
SETTINGS_JS = REPO_ROOT / "web" / "lora_library" / "settings.js"
PATH_HEAL_JS = REPO_ROOT / "web" / "lora_library" / "path_heal.js"
# v0.92.0 (shared-panel-code round): controller.js AND notebook.js both now
# import the shared search matcher -- must ride along too, or the served
# layout can't resolve either module's import under Node.
SEARCH_JS = REPO_ROOT / "web" / "lora_library" / "search.js"

STATES_LOC_PROBE_JS = """
import * as c from './extensions/comfyui-epsnodes/lora_library/controller.js'

const out = {
  exports: {
    statesLocationLine: typeof c.statesLocationLine === 'function',
    setsDirOf: typeof c.setsDirOf === 'function',
    parseCollapsedGroups: typeof c.parseCollapsedGroups === 'function',
    parseRenameDraft: typeof c.parseRenameDraft === 'function'
  },
  // Task 3 (2026-08-25): notebook.js's parseCollapsedSections() tolerance
  // pins, by hand, against controller.js's own duplicate.
  collapsedGroupsParse: [
    c.parseCollapsedGroups(['A', 'B']),
    c.parseCollapsedGroups(['A', 1, null, 'B', {}]), // non-strings dropped
    c.parseCollapsedGroups('["A","B"]'), // a hand-edited JSON string round-trips
    c.parseCollapsedGroups('not json'), // malformed JSON folds to []
    c.parseCollapsedGroups('[1,2]'), // a JSON array of non-strings folds to []
    c.parseCollapsedGroups(''), // blank string
    c.parseCollapsedGroups('   '), // whitespace-only string
    c.parseCollapsedGroups(null),
    c.parseCollapsedGroups(undefined),
    c.parseCollapsedGroups(42),
    c.parseCollapsedGroups({ A: true }) // a plain object, not an array
  ],
  // Tab-switch rename fix (2026-08-28): PROP_RENAME_DRAFT's own fail-soft
  // parser, same tolerance shape as parseCollapsedGroups() above.
  renameDraftParse: [
    c.parseRenameDraft(['Group A', 'Group A2']),
    c.parseRenameDraft('["Group A","Group A2"]'), // a hand-edited JSON string round-trips
    c.parseRenameDraft([]), // no open rename
    c.parseRenameDraft(['Group A']), // wrong length
    c.parseRenameDraft(['Group A', 'x', 'y']), // wrong length
    c.parseRenameDraft(['', 'x']), // empty category
    c.parseRenameDraft([1, 'x']), // non-string category
    c.parseRenameDraft(['Group A', 1]), // non-string text
    c.parseRenameDraft('not json'), // malformed JSON
    c.parseRenameDraft(''), // blank string
    c.parseRenameDraft('   '), // whitespace-only string
    c.parseRenameDraft(null),
    c.parseRenameDraft(undefined),
    c.parseRenameDraft(42),
    c.parseRenameDraft({ category: 'Group A', text: 'x' }) // a plain object, not an array
  ],
  defaultSameDir: c.statesLocationLine({ library_dir: '/home/u/lib', default_library_dir: '/home/u/lib/' }),
  defaultWinSeparators: c.statesLocationLine({ library_dir: 'C:\\\\Users\\\\e\\\\lib', default_library_dir: 'C:/Users/e/lib' }),
  configuredUnc: c.statesLocationLine({ library_dir: '\\\\\\\\nas\\\\share\\\\comfy', default_library_dir: 'C:\\\\Users\\\\e\\\\lib' }),
  configuredPosix: c.statesLocationLine({ library_dir: '/mnt/nas/comfy/', default_library_dir: '/home/u/lib' }),
  feedPreferred: c.statesLocationLine({ library_dir: '/x', default_library_dir: '/x', sets_dir: '/nas/lib/sets', is_default_library: false }),
  feedSaysDefault: c.statesLocationLine({ library_dir: '/nas', default_library_dir: '/y', sets_dir: '/nas/sets', is_default_library: true }),
  noteBecomesHint: c.statesLocationLine({ library_dir: '/nas/lib', default_library_dir: '/y', library_dir_note: '  folder unreachable ' }),
  defaultLocal: c.statesLocationLine({
    library_dir: '/home/u/lib',
    default_library_dir: '/home/u/lib',
    is_local: true,
  }),
  defaultRemote: c.statesLocationLine({
    library_dir: '/home/u/lib',
    default_library_dir: '/home/u/lib',
    is_local: false,
  }),
  configuredRemote: c.statesLocationLine({
    library_dir: '/mnt/nas/comfy',
    default_library_dir: '/home/u/lib',
    is_local: false,
  }),
  noteBeatsRemote: c.statesLocationLine({
    library_dir: '/home/u/lib',
    default_library_dir: '/home/u/lib',
    is_local: false,
    library_dir_note: 'unreachable',
  }),
  empty: c.statesLocationLine({}),
  nul: c.statesLocationLine(null),
  setsDir: [c.setsDirOf('/a/b/'), c.setsDirOf('C:\\\\lib'), c.setsDirOf(''), c.setsDirOf('/a'), c.setsDirOf(null)]
}
process.stdout.write(JSON.stringify(out))
"""

MSG_STATES_SHARED_TITLE = "Shared by every machine whose Library folder points here"
MSG_STATES_DEFAULT_LABEL = "States: this machine only (default library folder)"
# Browse… round (owner report 2026-08-22: could not find the Settings field
# -- it was DISABLED in his remote browser -- and missed a Browse… button
# next to Open folder): the hint names BOTH fixes and the remote caveat, in
# a local and a remote variant.
MSG_STATES_SHARE_HOWTO = (
    "To share between computers, set the Library folder to the same NAS folder on every machine — "
    "Browse… here (on the machine running ComfyUI), or Settings (gear) → EPSNodes → Library → "
    "Library folder. "
    "A remote browser sees that setting read-only."
)
MSG_STATES_SHARE_HOWTO_REMOTE = (
    "Set the Library folder on the machine running ComfyUI (its Settings → EPSNodes → Library → "
    "Library folder, "
    "or Browse… in a controller there) — a remote browser can only view it."
)
# `full` (the tooltip's sentence) = row 1 + " — " + row 2
MSG_STATES_DEFAULT = MSG_STATES_DEFAULT_LABEL + " — " + MSG_STATES_SHARE_HOWTO
MSG_STATES_DEFAULT_REMOTE = MSG_STATES_DEFAULT_LABEL + " — " + MSG_STATES_SHARE_HOWTO_REMOTE


@pytest.fixture(scope="module")
def controller_api(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Runs STATES_LOC_PROBE_JS against the REAL controller.js in a served-
    layout tmp dir -- controller.js imports `../../../scripts/app.js` and
    `./api.js` (-> `../../../scripts/api.js`), so the layout mirrors that
    depth, copies the real api.js/version.js in and stubs the two core
    scripts (test_m3_pinning_js.py's fixture shape). Importing the module
    under Node is itself a regression test: its top level must stay free of
    DOM/LiteGraph touches (the node class is built lazily in
    registerControllerNode)."""
    layout = tmp_path_factory.mktemp("web_root")
    module_dir = layout / "extensions" / "comfyui-epsnodes" / "lora_library"
    module_dir.mkdir(parents=True)
    js_files = (
        CONTROLLER_JS, API_JS, VERSION_JS, NOTEBOOK_JS, SETTINGS_JS, PATH_HEAL_JS, SEARCH_JS
    )
    for src in js_files:
        shutil.copyfile(src, module_dir / src.name)
    scripts = layout / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "api.js").write_text(
        "export const api = { fetchApi: () => {}, apiURL: (p) => p, addEventListener: () => {} }\n",
        encoding="utf-8",
    )
    (scripts / "app.js").write_text("export const app = {}\n", encoding="utf-8")
    probe = layout / "probe.mjs"
    probe.write_text(STATES_LOC_PROBE_JS, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(probe)], capture_output=True, text=True, timeout=60, cwd=layout
    )
    assert result.returncode == 0, f"probe failed (controller.js must IMPORT under Node):\n{result.stderr}"
    return json.loads(result.stdout)


def test_states_location_line_default_library_says_so_plainly(controller_api: dict) -> None:
    assert controller_api["exports"] == {k: True for k in controller_api["exports"]}
    line = controller_api["defaultSameDir"]
    # row 1 = the fact, row 2 (hint) = the fix; the whole sentence is `full`
    # and rides in the tooltip with the full path (a 300 px node's list pane
    # is ~170 px wide -- one row would ellipsise the sentence to nothing,
    # wrapped prose swallowed the whole list on the rig)
    assert line["text"] == MSG_STATES_DEFAULT_LABEL
    assert line["full"] == MSG_STATES_DEFAULT
    assert line["hint"] == MSG_STATES_SHARE_HOWTO
    assert line["isDefault"] is True
    assert line["setsDir"] == "/home/u/lib/sets"
    assert line["title"] == "/home/u/lib/sets\n" + MSG_STATES_DEFAULT  # tooltip carries the full path
    # separator-/trailing-slash-insensitive equality (the §4 spirit)
    assert controller_api["defaultWinSeparators"]["isDefault"] is True
    assert controller_api["defaultWinSeparators"]["setsDir"] == "C:\\Users\\e\\lib\\sets"


def test_states_location_line_configured_folder_shows_the_path(controller_api: dict) -> None:
    unc = controller_api["configuredUnc"]
    assert unc["text"] == "States: \\\\nas\\share\\comfy\\sets"
    assert unc["full"] == unc["text"]
    assert unc["title"] == MSG_STATES_SHARED_TITLE
    assert unc["hint"] == MSG_STATES_SHARED_TITLE + "."
    assert unc["isDefault"] is False
    posix = controller_api["configuredPosix"]
    assert posix["text"] == "States: /mnt/nas/comfy/sets"
    assert posix["setsDir"] == "/mnt/nas/comfy/sets"


def test_states_location_line_prefers_the_sets_feed_fields(controller_api: dict) -> None:
    """`GET /lora_library/sets` (newer backend) carries `sets_dir` +
    `is_default_library`: the backend KNOWS; the client-side derivation is
    the fallback only."""
    feed = controller_api["feedPreferred"]
    assert feed["text"] == "States: /nas/lib/sets" and feed["isDefault"] is False
    says_default = controller_api["feedSaysDefault"]
    assert says_default["text"] == MSG_STATES_DEFAULT_LABEL and says_default["setsDir"] == "/nas/sets"
    assert says_default["full"] == MSG_STATES_DEFAULT


def test_states_location_line_surfaces_the_server_diagnosis_and_degrades_to_empty(
    controller_api: dict,
) -> None:
    # FORMAT.md §5 `library_dir_note` (unreachable / wrong-OS path) beats
    # the sharing reassurance -- it is the one thing worth a hint line.
    assert controller_api["noteBecomesHint"]["hint"] == "folder unreachable"
    for key in ("empty", "nul"):
        assert controller_api[key] == {
            "text": "", "full": "", "title": "", "hint": "", "isDefault": False, "setsDir": ""
        }
    assert controller_api["setsDir"] == ["/a/b/sets", "C:\\lib\\sets", "", "/a/sets", ""]


def test_controller_config_fetch_is_one_shared_30s_cache(controller_source: str) -> None:
    """The picker feed's shared-fetch shape: one module-level cache for
    EVERY controller, one in-flight promise, released on settle; fetched on
    attach and by the shared poller (so a Settings change shows within the
    TTL -- settings.js posts /config silently, there is no event to hear)."""
    assert "const CONTROLLER_CONFIG_TTL_MS = 30000" in controller_source
    assert "const CONFIG_ROUTE = '/lora_library/config'" in controller_source
    fetch = _function_body(controller_source, "fetchControllerConfig()")
    assert "Date.now() - controllerConfigAt < CONTROLLER_CONFIG_TTL_MS" in fetch
    assert "if (controllerConfigPromise) return controllerConfigPromise" in fetch
    assert "api.getJson(CONFIG_ROUTE)" in fetch
    assert "promise.then(settle, settle)" in fetch
    added = _method_body(controller_source, "onAdded()")
    assert "this._refreshStatesLocation()" in added
    refresh = _method_body(controller_source, "_refreshStatesLocation()")
    assert "fetchControllerConfig()" in refresh and "this._applyConfigResponse(config)" in refresh
    assert "if (this._removed) return" in refresh
    run = _function_body(controller_source, "runSharedSetsFetch()")
    assert "runSharedConfigFetch().catch(() => {})" in run
    shared = _function_body(controller_source, "runSharedConfigFetch()")
    assert "await fetchControllerConfig()" in shared
    assert "node._applyConfigResponse(config)" in shared
    # the poller pin above still holds: the config ride adds no GET literal
    assert run.count("await api.getJson(") == 2


def test_states_location_line_sits_under_the_list_and_never_crops_it(controller_source: str) -> None:
    """§7.2 sizing laws: the left pane is a flex column; the line is
    `flex: 0 0 auto` BELOW the `flex: 1 1 auto; min-height: 0` scrolling
    list (the list shrinks and scrolls), and its row rides in getMinHeight."""
    pane = _method_body(controller_source, "_buildStatePane()")
    assert "this._pane.statesLocEl = el('div', { className: 'llsc-states-loc' }, [" in pane
    # v0.92.0: gained a search field ABOVE the list (shared-panel-code round,
    # owner decision 2026-09-08) -- the list-above-states-loc order this test
    # protects is otherwise unchanged.
    assert (
        "const leftPane = el('div', { className: 'llsc-pane-left' }, [\n"
        "          this._pane.searchEl,\n"
        "          this._pane.listEl,\n"
        "          this._pane.statesLocEl\n"
        "        ])"
    ) in pane
    assert "this._pane.statesLocEl.style.display = 'none'" in pane  # hidden until known
    assert "getMinHeight: () => MIN_STATE_PANE_HEIGHT + STATES_LOCATION_PX" in pane
    css = controller_source.split("const STATE_PANE_CSS_TEXT = `", 1)[1].split("\n`\n", 1)[0]
    # Browse… round, measured on the rig at the 300 px floor: the fact/path
    # row, the Browse… + Open folder row that WRAPS under it there (the two
    # buttons on one row left the fact 21 px -- "St…"), and the hint
    # clamped to two lines (67 px measured; 72 leaves slack)
    assert "const STATES_LOCATION_PX = 72" in controller_source
    row_css = css.split(".llsc-states-loc-row {", 1)[1].split("\n}\n", 1)[0]
    assert "flex-wrap: wrap;" in row_css
    assert "flex: 1 1 120px;" in css.split(".llsc-states-loc-text {", 1)[1].split("\n}\n", 1)[0]
    assert ".llsc-states-loc-text + .llsc-states-loc-btn {" in css  # the wrapped pair sits right
    loc = css.split(".llsc-states-loc {", 1)[1].split("\n}\n", 1)[0]
    assert "flex: 0 0 auto;" in loc
    lst = css.split(".llsc-list {", 1)[1].split("\n}\n", 1)[0]
    assert "flex: 1 1 auto;" in lst and "min-height: 0;" in lst
    assert ".llsc-states-loc-hint:empty { display: none; }" in css
    assert "`" not in css  # CSS_TEXT is a template literal


def test_open_folder_is_loopback_gated_and_an_old_backend_toasts(controller_source: str) -> None:
    """The route is loopback-only (FORMAT.md §2/§5): the button hides for a
    remote viewer (it could only ever fail for them -- the Notebook's
    file-panel rule); a 404 from a backend older than the route says so."""
    assert "const SETS_OPEN_FOLDER_ROUTE = '/lora_library/sets/open_folder'" in controller_source
    render = _method_body(controller_source, "_renderStatesLocation()")
    assert "const isLocal = this._statesConfig ? this._statesConfig.is_local !== false : true" in render
    assert "pane.statesLocOpenBtn.style.display = isLocal && line.setsDir ? '' : 'none'" in render
    assert "const merged = { ...(this._statesConfig || {}), ...(this._statesFeed || {}) }" in render
    assert "const line = statesLocationLine(merged)" in render
    assert "if (signature === this._statesLocSignature) return" in render  # change-gated
    open_folder = _method_body(controller_source, "async _openStatesFolder()")
    assert "await api.postJson(SETS_OPEN_FOLDER_ROUTE, {})" in open_folder
    assert "if (error?.status === 404) {" in open_folder
    assert "this._toast('warn', NODE_TITLE, MSG_STATES_OPEN_NEEDS_BACKEND)" in open_folder
    assert "} else if (error?.status === 403) {" in open_folder
    assert "this._toast('warn', NODE_TITLE, MSG_STATES_OPEN_REMOTE)" in open_folder
    assert (
        "const MSG_STATES_OPEN_NEEDS_BACKEND =\n"
        "  'Open folder needs a newer EPSNodes backend — update the pack on the machine running ComfyUI and restart it.'"
    ) in controller_source
    assert "const MSG_STATES_OPEN_REMOTE = 'Only the machine running ComfyUI can open its folders.'" in controller_source
    pane = _method_body(controller_source, "_buildStatePane()")
    assert "'Open folder'," in pane
    assert "'Reveal the states folder on the machine running ComfyUI'" in pane


def test_user_facing_states_strings_are_verbatim(controller_source: str) -> None:
    assert (
        "const MSG_STATES_SHARED_TITLE = 'Shared by every machine whose Library folder points here'"
        in controller_source
    )
    assert "const MSG_STATES_DEFAULT_LABEL = 'States: this machine only (default library folder)'" in controller_source
    assert (
        "const MSG_STATES_SHARE_HOWTO =\n"
        "  'To share between computers, set the Library folder to the same NAS folder on every "
        "machine — ' +\n"
        "  'Browse… here (on the machine running ComfyUI), or Settings (gear) → EPSNodes → Library "
        "→ Library folder. ' +\n"
        "  'A remote browser sees that setting read-only.'"
    ) in controller_source
    assert (
        "const MSG_STATES_SHARE_HOWTO_REMOTE =\n"
        "  'Set the Library folder on the machine running ComfyUI (its Settings → EPSNodes → "
        "Library → Library folder, ' +\n"
        "  'or Browse… in a controller there) — a remote browser can only view it.'"
    ) in controller_source
    # `full` is composed, not a third copy
    assert "const MSG_STATES_DEFAULT =" not in controller_source
    # the line stays two ROWS -- row 1 one line, the hint CLAMPED to two
    # lines, never unbounded prose (rig 2026-08-22: the wrapped sentence
    # swallowed the whole list at the 300 px node width)
    css = controller_source.split("const STATE_PANE_CSS_TEXT = `", 1)[1].split("\n`\n", 1)[0]
    text_css = css.split(".llsc-states-loc-text {", 1)[1].split("\n}\n", 1)[0]
    assert "white-space: nowrap;" in text_css
    hint_css = css.split(".llsc-states-loc-hint {", 1)[1].split("\n}\n", 1)[0]
    assert "-webkit-line-clamp: 2;" in hint_css
    assert "display: -webkit-box;" in hint_css and "overflow: hidden;" in hint_css
    render = _method_body(controller_source, "_renderStatesLocation()")
    assert "pane.statesLocTextEl.classList.toggle('llsc-states-loc-prose', line.isDefault)" in render
    # the full hint always in the tooltip
    assert "pane.statesLocHintEl.title = line.hint || ''" in render


def test_states_location_line_has_local_and_remote_hint_variants(controller_api: dict) -> None:
    """Browse… round: the hint tells a LOCAL viewer the two fixes (Browse…
    here, or the Settings path -- read-only from a remote browser) and a
    REMOTE viewer that the host machine sets it; the server's diagnosis
    still beats both; the configured-folder reassurance is viewer-neutral."""
    local = controller_api["defaultLocal"]
    assert local["hint"] == MSG_STATES_SHARE_HOWTO
    assert local["full"] == MSG_STATES_DEFAULT
    assert local["title"] == "/home/u/lib/sets\n" + MSG_STATES_DEFAULT
    # is_local absent => local
    assert controller_api["defaultSameDir"]["hint"] == MSG_STATES_SHARE_HOWTO
    remote = controller_api["defaultRemote"]
    assert remote["text"] == MSG_STATES_DEFAULT_LABEL
    assert remote["hint"] == MSG_STATES_SHARE_HOWTO_REMOTE
    assert remote["full"] == MSG_STATES_DEFAULT_REMOTE
    assert remote["title"] == "/home/u/lib/sets\n" + MSG_STATES_DEFAULT_REMOTE
    assert controller_api["noteBeatsRemote"]["hint"] == "unreachable"
    configured_remote = controller_api["configuredRemote"]
    assert configured_remote["text"] == "States: /mnt/nas/comfy/sets"
    assert configured_remote["hint"] == MSG_STATES_SHARED_TITLE + "."


def test_browse_sits_before_open_folder_and_is_loopback_gated(controller_source: str) -> None:
    """Owner report 2026-08-22 ("an 'open folder' button but not a browse
    button like other nodes"): Browse… BEFORE Open folder, the Notebook
    file panel's order; both hidden for a remote viewer (the picker's route
    and the config POST are loopback-only, FORMAT.md §2/§5); the click opens
    the Notebook's own picker in FOLDER mode, titled for this machine."""
    assert "import { pickServerFolder } from './notebook.js'" in controller_source
    assert "import { setLibraryDir } from './settings.js'" in controller_source
    pane = _method_body(controller_source, "_buildStatePane()")
    assert "this._pane.statesLocBrowseBtn = this._createActionButton(" in pane
    assert "'Browse…'," in pane
    assert pane.index("'Browse…',") < pane.index("'Open folder',")
    assert (
        "          el('div', { className: 'llsc-states-loc-row' }, [\n"
        "            this._pane.statesLocTextEl,\n"
        "            this._pane.statesLocBrowseBtn,\n"
        "            this._pane.statesLocOpenBtn\n"
        "          ]),"
    ) in pane
    assert "this._browseLibraryFolder()" in pane
    render = _method_body(controller_source, "_renderStatesLocation()")
    assert "pane.statesLocBrowseBtn.style.display = isLocal ? '' : 'none'" in render
    assert "pane.statesLocOpenBtn.style.display = isLocal && line.setsDir ? '' : 'none'" in render
    browse = _method_body(controller_source, "async _browseLibraryFolder()")
    assert "const isLocal = config.is_local !== false" in browse
    assert "this._toast('warn', NODE_TITLE, MSG_LIBRARY_BROWSE_REMOTE)" in browse
    assert "const picked = await pickServerFolder({" in browse
    assert "title: MSG_LIBRARY_BROWSE_TITLE," in browse
    assert (
        "startDir: config.library_dir_exists === false ? FS_LIST_ROOTS : current || null,"
        in browse
    )
    assert "confirmLabel: MSG_LIBRARY_BROWSE_CONFIRM," in browse
    assert "confirmPrompt: libraryBrowsePrompt," in browse
    assert "if (!picked || this._removed) return" in browse
    assert "await this._applyLibraryFolder(picked)" in browse
    assert "const MSG_LIBRARY_BROWSE_TITLE = 'Library folder for this machine'" in controller_source
    assert "const MSG_LIBRARY_BROWSE_CONFIRM = 'Use this folder'" in controller_source
    assert "const MSG_LIBRARY_BROWSE_FINAL = 'Set library folder'" in controller_source
    assert "const FS_LIST_ROOTS = 'ROOTS'" in controller_source
    assert (
        "    `Set this machine's Library folder to ${path}? States, groups, favorites, presets and "
        "` +\n"
        "    'the default notebook will then be read from there.'"
    ) in controller_source


def test_library_folder_pick_posts_drops_the_cache_and_toasts(controller_source: str) -> None:
    """The pick goes through settings.js's own POST (`setLibraryDir`, which
    also mirrors the path into the `loraLibrary.libraryDir` setting so the
    dialog agrees), then EVERY controller re-reads: shared /config cache
    dropped, each node's sets-feed copy of the old folder forgotten (it is
    preferred over /config), the sets-changed event re-runs the shared poll
    (and refreshes the Apply LoRA Set combos, §7.4); success toasts the
    agreed sentence, failure the server's message."""
    apply = _method_body(controller_source, "async _applyLibraryFolder(path)")
    assert "response = await setLibraryDir(path)" in apply
    assert (
        "this._toast('error', NODE_TITLE, `Could not set the library folder: ${error?.message || "
        "error}`)"
        in apply
    )
    assert "dropControllerConfigCache()" in apply
    assert "for (const node of liveControllers) node._statesFeed = null" in apply
    assert "announceSetsChanged()" in apply
    assert "this._toast('info', NODE_TITLE, libraryFolderSetToast(resolved), 8000)" in apply
    assert apply.index("dropControllerConfigCache()") < apply.index("announceSetsChanged()")
    drop = _function_body(controller_source, "dropControllerConfigCache()")
    assert "controllerConfig = null" in drop and "controllerConfigAt = 0" in drop
    assert (
        "    `Library folder set to ${path} — states, groups, favorites, presets and the default ` "
        "+\n"
        "    'notebook now live there. Set the same folder on your other machines to share.'"
    ) in controller_source
    # `_toast` grew an optional life for the long sentence; the defaults hold
    toast = _method_body(controller_source, "_toast(severity, summary, detail, life)")
    assert "life: life ?? (severity === 'error' ? 6000 : 3000)" in toast
    # settings.js's half: the dialog's POST, reused -- serverValue FIRST so
    # the mirrored setting value never re-POSTs through onLibraryDirChanged
    settings = SETTINGS_JS.read_text(encoding="utf-8")
    set_fn = _function_body(settings, "setLibraryDir(path)")
    assert "const response = await postLibraryDir(trimmed)" in set_fn
    assert (
        "await app.extensionManager?.setting?.set?.('loraLibrary.libraryDir', serverValue)"
        in set_fn
    )
    assert "export async function setLibraryDir(path)" in settings
    post_fn = _function_body(settings, "postLibraryDir(trimmed)")
    assert "api.postJson('/lora_library/config', { library_dir: trimmed })" in post_fn
    assert "serverValue = trimmed === '' ? '' : (response.library_dir ?? trimmed)" in post_fn
    on_change = _function_body(settings, "onLibraryDirChanged(value)")
    assert "await postLibraryDir(trimmed)" in on_change
    assert "api.postJson" not in on_change  # one POST site, not two


def test_sets_feed_fields_apply_before_the_row_change_gate(controller_source: str) -> None:
    """`_applySetsResponse` returns early when the ROWS did not change
    (v0.67.2); the location line reads `sets_dir`/`is_default_library`
    BEFORE that gate, since it is about the folder, not the rows."""
    apply = _method_body(controller_source, "_applySetsResponse(data)")
    assert "typeof data?.sets_dir === 'string'" in apply
    assert "this._statesFeed = {" in apply
    assert "this._renderStatesLocation()" in apply
    assert apply.index("this._renderStatesLocation()") < apply.index("const signature = JSON.stringify(this._setsCache)")


def test_states_location_is_dom_only_and_torn_down(controller_source: str) -> None:
    """§7.5: DOM only, no window listeners, no canvas drawing; the element
    ResizeObserver (the Notebook's path re-fit pattern) is disconnected in
    onRemoved and re-armed on a re-add."""
    section = controller_source.split("// ----------------------------------------- states location (NAS round)", 1)[1]
    marker = "_toast(severity, summary, detail, life) {"
    # the section ends at _toast; a moved/renamed marker must not silently widen it
    assert marker in section
    section = section.split(marker, 1)[0]
    assert "window.addEventListener" not in section
    # Browse… round: the two new methods live in this section and add no
    # listener either -- the picker's own Escape handler is notebook.js's
    assert (
        "async _browseLibraryFolder()" in section and "async _applyLibraryFolder(path)"
        in section
    )
    assert "onDrawForeground" not in section
    assert "new ResizeObserver(() => this._fitStatesLocationText())" in section
    removed = _method_body(controller_source, "onRemoved()")
    assert "this._statesLocObserver?.disconnect()" in removed
    added = _method_body(controller_source, "onAdded()")
    assert "this._armStatesLocObserver()" in added
    fit = _method_body(controller_source, "_fitStatesLocationText()")
    assert "textEl.scrollWidth <= textEl.clientWidth + 1" in fit  # front-truncate only on real overflow
    assert "frontTruncateText(full, budget)" in fit


# ---------------------------------------------------------------------------
# 2026-08-25 round: three owner reports in one file --
#  (1) `# name` group creation "doesn't consistently create a new group"
#  (2) collapsed groups must persist per workflow (notebook.js's v0.79.0
#      `Collapsed sections` idiom, mirrored here as `Collapsed groups`)
#  (3) Delete state "can take upwards of 10 seconds ... feels
#      non-responsive" while a workflow is running
# ---------------------------------------------------------------------------


class TestFlakyGroupCreationFixV20260825:
    """Root cause 1: `_doNewCategory()` mutated `_layoutCache` synchronously
    but never repainted until `_saveLayout()`'s POST resolved -- on the
    owner's NAS-backed library (500-1000 ms round trips, slower still while
    a workflow runs) the toast said "created" while the pane looked
    unchanged for up to a second, reading as "nothing happened" and inviting
    a confused repeat click. Root cause 2: `_saveLayout()`'s catch block
    (shared by new-group/rename/delete-group/drag-reorder) never toasted a
    failed save -- only a `status` line hidden by default -- so a group that
    failed to persist on a flaky NAS write silently evaporated on the next
    refresh with zero explanation."""

    def test_new_category_paints_optimistically_before_the_save_resolves(
        self, controller_source: str
    ) -> None:
        new_cat = _method_body(controller_source, "async _doNewCategory()")
        assert "this._layoutCache.categories.push(name)" in new_cat
        render_at = new_cat.index("this._renderStateList()")
        save_at = new_cat.index("await this._saveLayout()")
        push_at = new_cat.index("this._layoutCache.categories.push(name)")
        # paint BEFORE the network round trip, not after
        assert push_at < render_at < save_at

    def test_save_layout_toasts_loudly_on_failure(self, controller_source: str) -> None:
        save = _method_body(controller_source, "async _saveLayout()")
        catch_block = save.split("} catch (error) {", 1)[1].split(
            "this._layoutSaveQueued = false", 1
        )[0]
        assert "this._setStatusText(failMessage)" in catch_block
        assert "this._toast('error', NODE_TITLE, failMessage)" in catch_block
        assert "Could not save the group layout:" in catch_block
        # still refetches so the pane snaps back to stored truth (unchanged)
        assert "this._refreshSetsCache().catch(() => {})" in save

    def test_duplicate_and_missing_name_are_still_loud(self, controller_source: str) -> None:
        """The two other no-op outcomes (v0.72.1) are untouched by this round."""
        new_cat = _method_body(controller_source, "async _doNewCategory()")
        assert (
            "this._toast('warn', NODE_TITLE, 'Enter a group name after the # "
            "(e.g. \"# Portraits\").')" in new_cat
        )
        assert 'this._toast(\'warn\', NODE_TITLE, `A group named "${name}" already exists.`)' in (
            new_cat
        )


class TestCollapsedGroupsPersistPerWorkflowV20260825:
    """Owner ask 2026-08-25: "They should both remember their closed
    states." `_collapsedCategories` was an in-memory-only Set, lost on every
    reload. Mirrors notebook.js's v0.79.0 `Collapsed sections` property
    idiom exactly, renamed for this file's own vocabulary (`Collapsed
    groups`, duplicated by hand per the isCategoryNameInput/
    categoryNameFromInput no-cross-import convention)."""

    def test_pure_parser_is_exported_and_tolerant(self, controller_api: dict) -> None:
        assert controller_api["exports"]["parseCollapsedGroups"] is True
        parsed = controller_api["collapsedGroupsParse"]
        assert parsed == [
            ["A", "B"],
            ["A", "B"],
            ["A", "B"],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
        ]

    def test_property_declared_right_after_debug_capture_and_applied_explicitly(
        self, controller_source: str
    ) -> None:
        ctor = _method_body(controller_source, "constructor(title = NODE_TITLE)")
        assert "const PROP_COLLAPSED_GROUPS = 'Collapsed groups'" in controller_source
        assert "this.addProperty(PROP_DEBUG_CAPTURE, false, 'boolean')" in ctor
        assert "this.addProperty(PROP_COLLAPSED_GROUPS, [], 'array')" in ctor
        # addProperty() never fires onPropertyChanged -- a fresh node needs
        # the explicit apply, BEFORE _buildWidgets()'s first render.
        assert ctor.index("this.addProperty(PROP_COLLAPSED_GROUPS, [], 'array')") < ctor.index(
            "this._guarded('build widgets', () => this._buildWidgets())"
        )
        assert "this._applyCollapsedGroupsFromProperty()" in ctor
        assert ctor.index("this.addProperty(PROP_COLLAPSED_GROUPS, [], 'array')") < ctor.index(
            "this._applyCollapsedGroupsFromProperty()"
        )

    def test_on_property_changed_reapplies_and_repaints(self, controller_source: str) -> None:
        changed = _method_body(controller_source, "onPropertyChanged(name, value)")
        assert "if (name === PROP_SHOW_STATUS) {" in changed  # existing branch preserved
        assert "if (name === PROP_COLLAPSED_GROUPS) {" in changed
        collapsed_branch = changed.split("if (name === PROP_COLLAPSED_GROUPS) {", 1)[1]
        assert "this._applyCollapsedGroupsFromProperty()" in collapsed_branch
        assert "this._renderStateList()" in collapsed_branch

    def test_read_and_write_halves(self, controller_source: str) -> None:
        read = _method_body(controller_source, "_applyCollapsedGroupsFromProperty()")
        assert "parseCollapsedGroups(this.properties?.[PROP_COLLAPSED_GROUPS])" in read
        assert "this._collapsedCategories = new Set(names)" in read
        write = _method_body(controller_source, "_syncCollapsedGroupsProperty()")
        assert (
            "this.properties[PROP_COLLAPSED_GROUPS] = Array.from(this._collapsedCategories)"
            in write
        )
        # this.setDirtyCanvas(...), matching every other call site in this
        # class -- not node.graph?.setDirtyCanvas(...), notebook.js's form
        # for a `state.node` reference obtained from outside the class.
        assert "this.setDirtyCanvas(true, true)" in write

    def test_every_mutation_site_writes_through_the_property(self, controller_source: str) -> None:
        """The 5 sites named in the owner's ask: the toggle, the rename
        migration, the group delete, the drag-landing reveal, and the
        fresh-category open."""
        toggle = _method_body(controller_source, "_toggleCategoryCollapsed(category)")
        assert "this._syncCollapsedGroupsProperty()" in toggle
        assert toggle.index("this._syncCollapsedGroupsProperty()") < toggle.index(
            "this._renderStateList()"
        )

        rename = _method_body(controller_source, "_commitCategoryRename()")
        assert "if (this._collapsedCategories.delete(from)) {" in rename
        rename_migration = rename.split("if (this._collapsedCategories.delete(from)) {", 1)[1]
        rename_migration = rename_migration.split("}", 1)[0]
        assert "this._collapsedCategories.add(to)" in rename_migration
        assert "this._syncCollapsedGroupsProperty()" in rename_migration

        delete_cat = _method_body(controller_source, "_deleteCategory(category)")
        assert (
            "if (this._collapsedCategories.delete(category)) this._syncCollapsedGroupsProperty()"
            in delete_cat
        )

        finish_drag = _method_body(controller_source, "_finishStateDrag(drag)")
        assert (
            "if (this._collapsedCategories.delete(target.category)) "
            "this._syncCollapsedGroupsProperty()" in finish_drag
        )

        new_cat = _method_body(controller_source, "async _doNewCategory()")
        assert (
            "if (this._collapsedCategories.delete(name)) this._syncCollapsedGroupsProperty()"
            in new_cat
        )

    def test_never_reaches_for_localstorage(self, controller_source: str) -> None:
        """Persist WITH THE WORKFLOW (a node property) -- not a browser-local
        stash that would desync between machines or a re-imported workflow."""
        assert "localStorage" not in controller_source


class TestRenameDraftSurvivesTabSwitchV20260828:
    """Owner report 2026-08-28: "tabbing to another workflow and tabbing
    back erases any changes you've made ... switching between workflows
    shouldn't ever reset anything." A tab switch tears the controller node
    down and rebuilds it; `_categoryRename` (the open inline group-rename
    `<input>`, see `_beginCategoryRename()`) was pure in-memory/DOM state --
    `onRemoved()` just nulled it, uncommitted. The fix mirrors
    notebook.js's v0.87.3/v0.87.4 `drafts` widget fix (same bug class, a
    different file) using this file's own established idiom instead: a
    node PROPERTY (`PROP_RENAME_DRAFT`, same shape as `PROP_COLLAPSED_GROUPS`
    just above), a fail-soft parser (`parseRenameDraft`), and a 2-phase
    restore (`onPropertyChanged` stashes it, `_restorePendingRenameDraft()`
    -- called from `_renderStateList()`'s tail -- reopens the editor once
    the category's header actually exists)."""

    def test_pure_parser_is_exported_and_tolerant(self, controller_api: dict) -> None:
        assert controller_api["exports"]["parseRenameDraft"] is True
        parsed = controller_api["renameDraftParse"]
        assert parsed == [
            {"category": "Group A", "text": "Group A2"},
            {"category": "Group A", "text": "Group A2"},
            None,  # []
            None,  # wrong length (1)
            None,  # wrong length (3)
            None,  # empty category
            None,  # non-string category
            None,  # non-string text
            None,  # malformed JSON
            None,  # blank string
            None,  # whitespace-only string
            None,  # null
            None,  # undefined
            None,  # 42
            None,  # a plain object, not an array
        ]

    def test_property_declared_right_after_collapsed_groups_and_applied_explicitly(
        self, controller_source: str
    ) -> None:
        ctor = _method_body(controller_source, "constructor(title = NODE_TITLE)")
        assert "const PROP_RENAME_DRAFT = 'Group rename draft'" in controller_source
        assert "this.addProperty(PROP_RENAME_DRAFT, [], 'array')" in ctor
        # addProperty() never fires onPropertyChanged -- a fresh node needs
        # the explicit apply, BEFORE _buildWidgets()'s first render.
        assert ctor.index("this.addProperty(PROP_RENAME_DRAFT, [], 'array')") < ctor.index(
            "this._guarded('build widgets', () => this._buildWidgets())"
        )
        assert "this._applyRenameDraftFromProperty()" in ctor
        assert ctor.index("this.addProperty(PROP_RENAME_DRAFT, [], 'array')") < ctor.index(
            "this._applyRenameDraftFromProperty()"
        )

    def test_on_property_changed_stashes_and_restores(self, controller_source: str) -> None:
        changed = _method_body(controller_source, "onPropertyChanged(name, value)")
        assert "if (name === PROP_RENAME_DRAFT) {" in changed
        branch = changed.split("if (name === PROP_RENAME_DRAFT) {", 1)[1]
        assert "this._applyRenameDraftFromProperty()" in branch
        assert "this._restorePendingRenameDraft()" in branch

    def test_read_write_and_clear_halves(self, controller_source: str) -> None:
        read = _method_body(controller_source, "_applyRenameDraftFromProperty()")
        assert "parseRenameDraft(this.properties?.[PROP_RENAME_DRAFT])" in read
        assert "this._pendingRenameDraft = " in read

        write = _method_body(controller_source, "_syncRenameDraftProperty()")
        assert (
            "this.properties[PROP_RENAME_DRAFT] = rename ? "
            "[rename.category, rename.inputEl.value] : []"
        ) in write
        assert "this.setDirtyCanvas(true, true)" in write

        clear = _method_body(controller_source, "_clearRenameDraftProperty()")
        assert "this.properties[PROP_RENAME_DRAFT] = []" in clear
        assert "this.setDirtyCanvas(true, true)" in clear

    def test_begin_category_rename_syncs_on_open_and_every_keystroke(
        self, controller_source: str
    ) -> None:
        begin = _method_body(controller_source, BEGIN_CATEGORY_RENAME_SIGNATURE)
        assert "this._categoryRename = rename" in begin
        assert begin.index("this._categoryRename = rename") < begin.index(
            "this._syncRenameDraftProperty()"
        ), "must sync on open, before the first keystroke can"
        assert "input.addEventListener('input', () => this._syncRenameDraftProperty())" in begin

    def test_commit_and_cancel_both_clear_the_draft(self, controller_source: str) -> None:
        """Only a real end-of-edit (Enter/blur commit, or Escape cancel) may
        clear the draft -- never a re-render (onRemoved, checked below,
        deliberately does NOT call this)."""
        cancel = _method_body(controller_source, "_cancelCategoryRename()")
        assert "this._categoryRename = null" in cancel
        assert "this._clearRenameDraftProperty()" in cancel
        assert cancel.index("this._categoryRename = null") < cancel.index(
            "this._clearRenameDraftProperty()"
        )

        commit = _method_body(controller_source, "_commitCategoryRename()")
        assert "this._categoryRename = null" in commit
        assert "this._clearRenameDraftProperty()" in commit
        # cleared unconditionally, before any of the empty/duplicate/gone
        # refusal branches -- editing is over the instant this line runs.
        assert commit.index("this._categoryRename = null") < commit.index(
            "this._clearRenameDraftProperty()"
        )
        assert commit.index("this._clearRenameDraftProperty()") < commit.index(
            "const from = rename.category"
        )

    def test_on_removed_does_not_clear_the_draft(self, controller_source: str) -> None:
        """The whole point of the property: an involuntary teardown
        (onRemoved, a tab switch) must NOT be treated as an end-of-edit."""
        removed = _method_body(controller_source, "onRemoved()")
        assert "this._categoryRename = null" in removed
        assert "_clearRenameDraftProperty" not in removed
        assert "_syncRenameDraftProperty" not in removed

    def test_render_state_list_restores_pending_draft_after_headers_are_built(
        self, controller_source: str
    ) -> None:
        render = _method_body(controller_source, "_renderStateList()")
        assert "this._restorePendingRenameDraft()" in render
        # after the row-building loop (headers must exist in `dragRows`
        # first, see `_headerElOf()`), not before.
        assert render.index("if (scrollTop) listEl.scrollTop = scrollTop") < render.index(
            "this._restorePendingRenameDraft()"
        )

    def test_restore_is_idempotent_and_gated(self, controller_source: str) -> None:
        restore = _method_body(controller_source, "_restorePendingRenameDraft()")
        assert "if (!pending || this._removed || this._categoryRename) return" in restore
        # never clobbers a live edit, and matches the category by NAME
        # against the loaded layout -- not blindly reopened.
        assert "this._layoutCache.categories.includes(pending.category)" in restore
        assert "this._pendingRenameDraft = null" in restore
        assert (
            "this._guarded('restore rename draft', () => "
            "this._beginCategoryRename(pending.category, pending.text))"
        ) in restore
        # a header that never shows up (layout confirmed loaded, category
        # genuinely gone) drops the stale draft instead of leaking it
        # forever.
        assert "if (this._layoutLoaded) {" in restore

    def test_never_reaches_for_localstorage(self, controller_source: str) -> None:
        assert "localStorage" not in controller_source


class TestOptimisticDeleteV20260825:
    """Owner report 2026-08-25: "Delete state for the Lora Loader can take
    upwards of 10 seconds if a workflow is running. The app feels
    non-responsive." The server work was already off-loop, but the FRONTEND
    awaited the whole round trip before touching the pane at all -- the row
    stayed put, nothing moved, no toast, for as long as the NAS write (or a
    busy queue) took. Optimistic UI: remove the row THE INSTANT the confirm
    click lands; roll back and toast loudly on failure."""

    def test_confirm_click_resolves_the_entry_and_hands_it_to_do_delete(
        self, controller_source: str
    ) -> None:
        click = _method_body(controller_source, "_onDeleteClick()")
        confirm_branch = click.split("this._disarmDeleteButton()", 1)[1]
        assert "const entry = this._selectedSetEntry()" in confirm_branch
        assert "if (!entry) {" in confirm_branch
        assert "this._toast('warn', NODE_TITLE, 'Pick a saved state first.')" in confirm_branch
        assert "this._runAction(LABEL_DELETE, () => this._doDelete(entry))" in confirm_branch
        # _doDelete() itself no longer re-resolves the selection -- it takes
        # the entry as a parameter now.
        assert "async _doDelete(entry) {" in controller_source

    def test_delete_removes_the_row_before_the_post_and_guards_the_slug(
        self, controller_source: str
    ) -> None:
        do_delete = _method_body(controller_source, "async _doDelete(entry)")
        assert "const previousCache = this._setsCache" in do_delete
        assert "const previousSignature = this._setsSignature" in do_delete
        assert "this._deleteInFlightSlugs.add(entry.slug)" in do_delete
        assert "this._setsCache = this._setsCache.filter((s) => s.slug !== entry.slug)" in do_delete
        assert "this._setsSignature = JSON.stringify(this._setsCache)" in do_delete
        # every optimistic step happens BEFORE the POST fires
        filter_at = do_delete.index(
            "this._setsCache = this._setsCache.filter((s) => s.slug !== entry.slug)"
        )
        render_at = do_delete.index("this._renderStateList()")
        toast_at = do_delete.index('this._toast(\'info\', NODE_TITLE, `Deleting "${entry.name}"…`)')
        post_at = do_delete.index(
            "await api.postJson('/lora_library/set/delete', { slug: entry.slug })"
        )
        assert filter_at < render_at < post_at
        assert toast_at < post_at
        # the guard is always released, success or failure
        assert do_delete.count("this._deleteInFlightSlugs.delete(entry.slug)") == 1
        assert "} finally {" in do_delete

    def test_applied_sets_response_filters_the_in_flight_slug(self, controller_source: str) -> None:
        """The shared poller (or any other response) must never resurrect a
        row `_doDelete()` already removed optimistically."""
        apply = _method_body(controller_source, "_applySetsResponse(data)")
        assert ".filter((s) => !this._deleteInFlightSlugs.has(s.slug))" in apply
        filter_at = apply.index(".filter((s) => !this._deleteInFlightSlugs.has(s.slug))")
        signature_at = apply.index("const signature = JSON.stringify(this._setsCache)")
        assert filter_at < signature_at

    def test_success_settles_quietly_with_the_same_feed_refresh_as_before(
        self, controller_source: str
    ) -> None:
        do_delete = _method_body(controller_source, "async _doDelete(entry)")
        try_block = do_delete.split("try {", 1)[1].split("} catch (error) {", 1)[0]
        assert "this._applySetsResponse(response)" in try_block
        assert "announceSetsChanged()" in try_block
        assert "const nextEntry = this._setsCache[0] || null" in try_block
        assert "this._selectEntry(nextEntry)" in try_block
        # no second "deleted" toast -- the "Deleting…" toast already covered it
        assert "_toast(" not in try_block

    def test_failure_rolls_back_the_row_and_toasts_loudly(self, controller_source: str) -> None:
        do_delete = _method_body(controller_source, "async _doDelete(entry)")
        catch_block = do_delete.split("} catch (error) {", 1)[1].split("} finally {", 1)[0]
        assert "this._setsCache = previousCache" in catch_block
        assert "this._setsSignature = previousSignature" in catch_block
        assert "this._renderStateList()" in catch_block
        assert (
            "this._toast('error', NODE_TITLE, `Could not delete \"${entry.name}\": "
            "${error?.message || error}`)" in catch_block
        )

    def test_deleting_toast_names_the_state(self, controller_source: str) -> None:
        do_delete = _method_body(controller_source, "async _doDelete(entry)")
        assert 'this._toast(\'info\', NODE_TITLE, `Deleting "${entry.name}"…`)' in do_delete


class TestOptimisticSaveAndPushV20260826:
    """Owner report 2026-08-26: "Saving and pushing a state in a lora state
    controller can take upwards of 10-20 seconds when a complex workflow is
    running." His library is a slow NAS and ComfyUI is GIL-busy mid-run, so
    every round trip can run long -- the fix is the SAME optimistic-UI shape
    `TestOptimisticDeleteV20260825` above validates for delete, extended to
    Save/New State (`_doCapture()`/`_captureComposite()`), Save State on an
    existing row (`_doUpdate()`/`_updateComposite()`), and Push State's
    background loader_slot sync (`_doPush()`)."""

    # ---------------------------------------------------------- pure helpers

    def test_provisional_slug_is_locally_invented_never_posted(
        self, controller_source: str
    ) -> None:
        body = _method_body(controller_source, "_nextProvisionalSlug()")
        assert body.strip() == (
            "return `pending-${Date.now().toString(36)}-${this._saveProvisionalSeq++}`"
        )

    def test_compare_set_entries_sorts_by_name_then_slug(self, controller_source: str) -> None:
        body = _function_body(controller_source, "compareSetEntries(a, b)")
        assert "(a.name || '').toLowerCase()" in body
        assert "(b.name || '').toLowerCase()" in body
        assert "if (an !== bn) return an < bn ? -1 : 1" in body
        assert "return a.slug < b.slug ? -1 : a.slug > b.slug ? 1 : 0" in body

    # -------------------------------------------------------- create (Save/New)

    def test_begin_optimistic_create_paints_synchronously_before_any_network_call(
        self, controller_source: str
    ) -> None:
        begin = _method_body(controller_source, "_beginOptimisticCreate(name, rowCount)")
        assert "await" not in begin  # entirely local -- nothing here touches the network
        assert "const provisionalSlug = this._nextProvisionalSlug()" in begin
        assert "const previousSelectedSlug = this._selectedSlug" in begin
        assert "this._saveInFlightSlugs.add(provisionalSlug)" in begin
        assert (
            "const entry = { slug: provisionalSlug, name, count: rowCount, "
            "label: `${name} (saving…)` }" in begin
        )
        assert "this._setsCache = [...this._setsCache, entry].sort(compareSetEntries)" in begin
        assert "this._setsSignature = JSON.stringify(this._setsCache)" in begin
        assert "this._renderStateList()" in begin
        assert "this._selectEntry(entry, { loadName: false })" in begin
        assert "this._clearNameField()" in begin
        assert 'this._toast(\'info\', NODE_TITLE, `Saving "${name}"…`)' in begin
        assert "return { provisionalSlug, previousSelectedSlug }" in begin
        # cache mutation, selection and the name-field clear all happen
        # BEFORE the toast that tells the user a save is starting
        cache_at = begin.index("this._setsCache = [...this._setsCache, entry]")
        select_at = begin.index("this._selectEntry(entry")
        clear_at = begin.index("this._clearNameField()")
        toast_at = begin.index("this._toast('info'")
        assert cache_at < select_at < clear_at < toast_at

    def test_rollback_optimistic_create_restores_selection_and_typed_name(
        self, controller_source: str
    ) -> None:
        rollback = _method_body(
            controller_source,
            "_rollbackOptimisticCreate(provisionalSlug, previousSelectedSlug, name, error)",
        )
        assert (
            "this._setsCache = this._setsCache.filter((s) => s.slug !== provisionalSlug)"
            in rollback
        )
        assert "this._selectEntry(previous, { loadName: false })" in rollback
        assert "this._selectedSlug = null" in rollback
        assert "this._setSetValueSilently('')" in rollback
        assert "this._w.name.value = name" in rollback
        assert "this._w.name.callback?.(name)" in rollback
        assert (
            'this._toast(\'error\', NODE_TITLE, `Could not save "${name}": '
            "${error?.message || error}`)" in rollback
        )

    def test_do_capture_paints_optimistically_then_posts_in_a_try_catch_finally(
        self, controller_source: str
    ) -> None:
        do_capture = _method_body(controller_source, "async _doCapture()")
        begin_at = do_capture.index(
            "const { provisionalSlug, previousSelectedSlug } = this._beginOptimisticCreate("
        )
        post_at = do_capture.index("await api.postJson('/lora_library/set'")
        assert begin_at < post_at  # optimistic paint happens before the network call
        try_block = do_capture.split("try {", 1)[1].split("} catch (error) {", 1)[0]
        assert "this._applySetsResponse(response)" in try_block
        assert "announceSetsChanged()" in try_block
        assert "this._selectSetBySlug(response.slug)" in try_block
        assert "this._clearNameField()" in try_block  # re-clears after selecting reloads it
        assert "const saved = this._setsCache.find((s) => s.slug === response.slug)" in try_block
        assert (
            "this._toast('success', NODE_TITLE, `Saved \"${saved?.name ?? name}\": "
            "${summarizeRowsForToast(loras)}`)" in try_block
        )
        catch_block = do_capture.split("} catch (error) {", 1)[1].split("} finally {", 1)[0]
        assert (
            "this._rollbackOptimisticCreate(provisionalSlug, previousSelectedSlug, name, error)"
            in catch_block
        )
        assert do_capture.count("this._saveInFlightSlugs.delete(provisionalSlug)") == 1
        assert "} finally {" in do_capture
        # no separate read-back GET -- the round trip this round drops
        assert "GET" not in do_capture
        assert do_capture.count("api.postJson") == 1
        assert do_capture.count("api.getJson") == 0

    def test_capture_composite_still_makes_exactly_one_post_and_drops_the_readback(
        self, controller_source: str
    ) -> None:
        composite = _method_body(controller_source, "async _captureComposite(targets, name)")
        assert composite.count("await api.postJson") == 1
        assert composite.count("api.getJson") == 0
        assert (
            "const { provisionalSlug, previousSelectedSlug } = this._beginOptimisticCreate("
            "name, loadersRows[0]?.length || 0)" in composite
        )
        begin_at = composite.index("this._beginOptimisticCreate(")
        post_at = composite.index("await api.postJson")
        assert begin_at < post_at
        assert (
            "this._rollbackOptimisticCreate(provisionalSlug, previousSelectedSlug, name, error)"
            in composite
        )
        assert composite.count("this._saveInFlightSlugs.delete(provisionalSlug)") == 1

    # -------------------------------------------------------- update (Save)

    def test_begin_optimistic_update_replaces_the_cache_entry_synchronously(
        self, controller_source: str
    ) -> None:
        begin = _method_body(controller_source, "_beginOptimisticUpdate(entry, newName, rowCount)")
        assert "await" not in begin
        assert "this._saveInFlightSlugs.add(entry.slug)" in begin
        assert "const name = newName ?? entry.name" in begin
        assert (
            "const updated = { slug: entry.slug, name, count: rowCount, "
            "label: name || entry.slug }" in begin
        )
        assert (
            "this._setsCache = this._setsCache.map((s) => (s.slug === entry.slug ? updated : s))"
            ".sort(compareSetEntries)" in begin
        )
        assert "this._selectEntry(updated, { loadName: false })" in begin
        assert 'this._toast(\'info\', NODE_TITLE, `Saving "${name}"…`)' in begin
        assert "return entry" in begin  # the OLD entry, for rollback

    def test_rollback_optimistic_update_restores_the_old_entry(
        self, controller_source: str
    ) -> None:
        rollback = _method_body(controller_source, "_rollbackOptimisticUpdate(previous, error)")
        assert (
            "this._setsCache = this._setsCache.map((s) => "
            "(s.slug === previous.slug ? previous : s)).sort(compareSetEntries)" in rollback
        )
        assert "this._selectEntry(previous, { loadName: false })" in rollback
        assert (
            'this._toast(\'error\', NODE_TITLE, `Could not save "${previous.name}": '
            "${error?.message || error}`)" in rollback
        )

    def test_do_update_reapplies_locally_before_the_optimistic_save_begins(
        self, controller_source: str
    ) -> None:
        do_update = _method_body(controller_source, "async _doUpdate()")
        apply_at = do_update.index("applySetToTargets(targets, { loras })")
        begin_at = do_update.index("this._beginOptimisticUpdate(entry, newName, loras.length)")
        get_at = do_update.index("await api.getJson('/lora_library/set'")
        post_at = do_update.index("await api.postJson('/lora_library/set'")
        # graph mutation, then the optimistic paint, THEN the network -- in
        # that order, none of the network calls gate the first two
        assert apply_at < begin_at < get_at < post_at
        try_block = do_update.split("try {", 1)[1]
        catch_block = try_block.split("} catch (error) {", 1)[1].split("} finally {", 1)[0]
        assert "this._rollbackOptimisticUpdate(previous, error)" in catch_block
        assert do_update.count("this._saveInFlightSlugs.delete(savedSlug)") == 1
        assert "} finally {" in do_update
        # exactly the two round trips this method has always made -- the
        # best-effort GET, then the write -- no third read-back fetch
        assert do_update.count("api.getJson") == 1
        assert do_update.count("api.postJson") == 1

    def test_update_composite_same_reordering_and_round_trip_count(
        self, controller_source: str
    ) -> None:
        composite = _method_body(
            controller_source, "async _updateComposite(targets, entry, newName)"
        )
        apply_at = composite.index("applySetToTargets(targets, {")
        begin_at = composite.index(
            "this._beginOptimisticUpdate(entry, newName, loadersRows[0]?.length || 0)"
        )
        get_at = composite.index("await api.getJson('/lora_library/set'")
        post_at = composite.index("await api.postJson('/lora_library/set'")
        assert apply_at < begin_at < get_at < post_at
        assert "this._rollbackOptimisticUpdate(previous, error)" in composite
        assert composite.count("this._saveInFlightSlugs.delete(savedSlug)") == 1
        assert composite.count("api.getJson") == 1
        assert composite.count("api.postJson") == 1

    # ----------------------------------------------- applySetsResponse guard

    def test_applied_sets_response_preserves_in_flight_saves(self, controller_source: str) -> None:
        """A poll response landing mid-save must not drop a create's not-yet-
        real provisional row, nor regress an update's local edit back to a
        stale pre-write snapshot."""
        apply = _method_body(controller_source, "_applySetsResponse(data)")
        assert "const localBySlug = new Map(" in apply
        assert (
            "previousCache.filter((s) => this._saveInFlightSlugs.has(s.slug))"
            ".map((s) => [s.slug, s])" in apply
        )
        assert "const remoteSlugs = new Set(list.map((s) => s.slug))" in apply
        assert "const local = localBySlug.get(s.slug)" in apply
        assert "if (local) return local" in apply
        assert "this._saveInFlightSlugs.has(entry.slug)" in apply
        assert "!remoteSlugs.has(entry.slug)" in apply
        assert "built.push(entry)" in apply
        assert (
            "this._setsCache = carriedProvisional ? built.sort(compareSetEntries) : built" in apply
        )
        # local-wins swap and the provisional carry-forward both happen
        # BEFORE the change-gated signature/render at the end
        local_at = apply.index("const local = localBySlug.get(s.slug)")
        carry_at = apply.index("built.push(entry)")
        signature_at = apply.index("const signature = JSON.stringify(this._setsCache)")
        assert local_at < signature_at
        assert carry_at < signature_at

    # ------------------------------------------------------------------ push

    def test_do_push_toasts_immediately_then_syncs_loaders_in_the_background(
        self, controller_source: str
    ) -> None:
        do_push = _method_body(controller_source, "async _doPush()")
        assert "const count = pushStateToNodes(applyNodes, entry.slug)" in do_push
        push_at = do_push.index("pushStateToNodes(applyNodes, entry.slug)")
        toast_at = do_push.index("this._toast(", push_at)
        sync_at = do_push.index("await this._syncLoaderSlotsForPush(applyNodes, entry.slug)")
        # the broadcast and its toast both land BEFORE the sync is awaited
        assert push_at < toast_at < sync_at
        assert "syncing loaders…" in do_push
        try_block = do_push.split("try {", 1)[1].split("} catch (error) {", 1)[0]
        assert (
            "const changed = await this._syncLoaderSlotsForPush(applyNodes, entry.slug)"
            in try_block
        )
        assert "if (changed > 0) {" in try_block
        assert "Synced loader_slot for" in try_block
        catch_block = do_push.split("} catch (error) {", 1)[1]
        assert "Pushed, but syncing loader slots failed" in catch_block

    def test_sync_loader_slots_for_push_returns_the_changed_count(
        self, controller_source: str
    ) -> None:
        sync = _method_body(controller_source, "async _syncLoaderSlotsForPush(applyNodes, slug)")
        assert "if (!Array.isArray(full?.loaders) || !full.loaders.length) return 0" in sync
        assert "let changed = 0" in sync
        assert "if (pushLoaderSlotForTag(node)) changed++" in sync
        assert sync.rstrip().endswith("return changed")

    def test_push_loader_slot_for_tag_reports_whether_it_changed_anything(
        self, controller_source: str
    ) -> None:
        body = _function_body(controller_source, "pushLoaderSlotForTag(node)")
        assert body.count("return false") == 3
        assert "slotWidget.value = index" in body
        assert body.rstrip().endswith("return true")


# --------------------------------------- name-field stale-read fix (2026-09-02)


def _const_line(source_text: str, name: str) -> str:
    """The single ``const NAME = ...`` declaration line, verbatim -- used to
    splice the REAL literal into the probe below instead of retyping it
    (and silently drifting from the source if it ever changes)."""
    match = re.search(rf"^const {name} = .*$", source_text, flags=re.MULTILINE)
    assert match, f"const {name} not found"
    return match.group(0)


#: Reassembles a probe-runnable copy of the exact READ path
#: `_onCaptureClick()`/`_onUpdateClick()` take, spliced together from real,
#: freshly-extracted controller.js source (never retyped by hand) plus a
#: minimal fake LiteGraph/DOM harness -- `_runAction` is stubbed as a spy
#: (which label a click chose) rather than executed, since `_doNewCategory`/
#: `_doCapture`/`_doUpdate`'s OWN network/layout behavior is already covered
#: by the pins above (test_new_group_is_announced_with_toasts,
#: TestFlakyGroupCreationFixV20260825, etc.) -- this probe exists to prove
#: the READ those methods depend on is correct, under conditions
#: (an open, uncommitted canvas-legacy prompt dialog) no existing test can
#: express as a source pin alone.
_FLUSH_PROBE_TEMPLATE = """
__NODE_TITLE_LINE__
__LABEL_CAPTURE_LINE__
__LABEL_UPDATE_LINE__

__IS_CATEGORY_FN__

__CATEGORY_FROM_FN__

const WARNINGS = []
const api = { warn: (...args) => { WARNINGS.push(args) } }
const app = { canvas: { prompt_box: null } }

function makeWidget(initialValue) {
  return {
    value: initialValue,
    callbackCalls: [],
    callback(v) {
      this.callbackCalls.push(v)
    }
  }
}

function makeDialog({ isConnected = true, value = null, hasValueInput = true } = {}) {
  const dialog = {
    isConnected,
    closed: false,
    querySelector(_sel) {
      return hasValueInput ? { value } : null
    },
    close() {
      dialog.closed = true
    }
  }
  return dialog
}

function makeThrowingDialog() {
  return {
    isConnected: true,
    closed: false,
    querySelector() {
      throw new Error('boom: dialog internals changed')
    },
    close() {
      this.closed = true
    }
  }
}

class FakeControllerNode {
  constructor(nameWidget) {
    this._w = { name: nameWidget }
    this.actions = []
    this.dirtyCalls = 0
  }
  _disarmDeleteButton() {}
  _runAction(label, _fn) {
    // Deliberately NOT invoking _fn(): _doNewCategory()/_doCapture()/
    // _doUpdate()'s own bodies are covered elsewhere -- this probe only
    // needs to know which branch a click took.
    this.actions.push(label)
  }
  _doNewCategory() {}
  _doCapture() {}
  _doUpdate() {}
  setDirtyCanvas() {
    this.dirtyCalls++
  }

__FLUSH_METHOD__

__CAPTURE_CLICK_METHOD__

__UPDATE_CLICK_METHOD__
}

function runScenario(build) {
  WARNINGS.length = 0
  app.canvas.prompt_box = null
  const ctx = build()
  ctx.run()
  return {
    actions: ctx.node.actions,
    value: ctx.node._w.name.value,
    callbackCalls: ctx.node._w.name.callbackCalls,
    dirtyCalls: ctx.node.dirtyCalls,
    dialogClosed: ctx.dialog ? ctx.dialog.closed : null,
    warnings: WARNINGS.length
  }
}

const out = {}

// Vue mode (always) and canvas-legacy mode once Enter/OK already committed
// (no dialog open): widget.value is already what's on screen.
out.alreadyCommittedNoDialog = runScenario(() => {
  const node = new FakeControllerNode(makeWidget('# Portraits'))
  return { node, dialog: null, run: () => node._onCaptureClick() }
})

// Canvas-legacy mode, typed but never committed (no Enter/OK) -- the bug
// this fix closes.
out.uncommittedHashValueFlushed = runScenario(() => {
  const dialog = makeDialog({ value: '# Portraits' })
  app.canvas.prompt_box = dialog
  const node = new FakeControllerNode(makeWidget(''))
  return { node, dialog, run: () => node._onCaptureClick() }
})

out.uncommittedPlainValueFlushed = runScenario(() => {
  const dialog = makeDialog({ value: 'My New State' })
  app.canvas.prompt_box = dialog
  const node = new FakeControllerNode(makeWidget(''))
  return { node, dialog, run: () => node._onCaptureClick() }
})

// The dialog is open but its live value already MATCHES widget.value
// (nothing pending) -- must be a strict no-op, not a re-fired callback.
out.alreadyInSyncDialogLeftAlone = runScenario(() => {
  const dialog = makeDialog({ value: 'Same' })
  app.canvas.prompt_box = dialog
  const node = new FakeControllerNode(makeWidget('Same'))
  return { node, dialog, run: () => node._onCaptureClick() }
})

// "#" alone, uncommitted -- must still reach the group branch so
// _doNewCategory()'s own "Enter a group name after the #" message (pinned
// above) is what the owner sees, not a silently-created mis-named state.
out.hashOnlyStillTakesGroupBranch = runScenario(() => {
  const dialog = makeDialog({ value: '#' })
  app.canvas.prompt_box = dialog
  const node = new FakeControllerNode(makeWidget(''))
  return { node, dialog, run: () => node._onCaptureClick() }
})

// A stale/detached dialog reference (already closed some other way) must
// never be read -- falls back to whatever widget.value already holds.
out.disconnectedDialogIgnored = runScenario(() => {
  const dialog = makeDialog({ isConnected: false, value: '# Portraits' })
  app.canvas.prompt_box = dialog
  const node = new FakeControllerNode(makeWidget('OldValue'))
  return { node, dialog, run: () => node._onCaptureClick() }
})

// A future frontend whose dialog shape no longer has ".value" -- degrades
// to widget.value, no throw, no warning (this is not an error condition).
out.missingValueElementDegradesSafely = runScenario(() => {
  const dialog = makeDialog({ hasValueInput: false })
  app.canvas.prompt_box = dialog
  const node = new FakeControllerNode(makeWidget('# Fallback'))
  return { node, dialog, run: () => node._onCaptureClick() }
})

// The internals probe itself throws -- must warn, never break the click.
out.throwingDialogNeverBreaksTheClick = runScenario(() => {
  const dialog = makeThrowingDialog()
  app.canvas.prompt_box = dialog
  const node = new FakeControllerNode(makeWidget('# Resilient'))
  return { node, dialog, run: () => node._onCaptureClick() }
})

// Save State's rename-in-place read (_saveAsNewName()) has the identical
// hazard -- _onUpdateClick() must flush too.
out.updateClickAlsoFlushesBeforeRename = runScenario(() => {
  const dialog = makeDialog({ value: 'Renamed' })
  app.canvas.prompt_box = dialog
  const node = new FakeControllerNode(makeWidget(''))
  return { node, dialog, run: () => node._onUpdateClick() }
})

process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def name_field_flush_probe(
    controller_source: str, tmp_path_factory: pytest.TempPathFactory
) -> dict:
    """Runs `_FLUSH_PROBE_TEMPLATE` (real, freshly-extracted `controller.js`
    source spliced into a minimal fake-LiteGraph harness -- see that
    constant's own doc comment) under Node and returns the parsed per-
    scenario results."""
    replacements = {
        "__NODE_TITLE_LINE__": _const_line(controller_source, "NODE_TITLE"),
        "__LABEL_CAPTURE_LINE__": _const_line(controller_source, "LABEL_CAPTURE"),
        "__LABEL_UPDATE_LINE__": _const_line(controller_source, "LABEL_UPDATE"),
        "__IS_CATEGORY_FN__": (
            "function isCategoryNameInput(rawName) {\n"
            + _function_body(controller_source, "isCategoryNameInput(rawName)")
            + "\n}"
        ),
        "__CATEGORY_FROM_FN__": (
            "function categoryNameFromInput(rawName) {\n"
            + _function_body(controller_source, "categoryNameFromInput(rawName)")
            + "\n}"
        ),
        "__FLUSH_METHOD__": (
            "  _flushPendingNameEdit() {\n"
            + _method_body(controller_source, "_flushPendingNameEdit()")
            + "\n  }"
        ),
        "__CAPTURE_CLICK_METHOD__": (
            "  _onCaptureClick() {\n"
            + _method_body(controller_source, "_onCaptureClick()")
            + "\n  }"
        ),
        "__UPDATE_CLICK_METHOD__": (
            "  _onUpdateClick() {\n"
            + _method_body(controller_source, "_onUpdateClick()")
            + "\n  }"
        ),
    }
    script = _FLUSH_PROBE_TEMPLATE
    for token, value in replacements.items():
        assert token in script, f"probe template missing {token}"
        script = script.replace(token, value)

    layout = tmp_path_factory.mktemp("flush_probe")
    probe = layout / "probe.mjs"
    probe.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(probe)], capture_output=True, text=True, timeout=60, cwd=layout
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


class TestNameFieldStaleReadFixV20260902:
    """Owner report 2026-09-02 (verbatim: "Also creating a group by adding
    a '# name' in the lora load state controller doesn't seem to work, or
    at least not reliably"). Three previous "fixes" are still in this file
    (v0.67.2/v0.72.1/v0.80.1, pinned above by
    test_name_field_clears_through_the_widget_callback,
    test_new_group_is_announced_with_toasts and
    test_save_layout_toasts_loudly_on_failure) -- every one of them was a
    real bug in what happens AFTER `isCategoryNameInput(this._w.name.value)`
    reads the field. None of them questioned whether that READ itself could
    be wrong. It can, reliably-*sometimes*, which is exactly what "doesn't
    seem to work, or at least not reliably" describes.

    VERIFY(live) FINDING, from the installed frontend's OWN source (not
    guessed from behavior): comfyui_frontend_package 1.48.7's bundled
    sourcemaps, `sourcesContent` recovered from
    `~/Library/Application Support/comfy_ps/rig/venv/lib/python3.14/
    site-packages/comfyui_frontend_package/static/assets/*.js.map` on
    Eric's rig. The ORIGINAL hypothesis behind this fix -- that the Vue
    renderer's DOM input only commits `widget.value` on blur/change -- is
    WRONG: PrimeVue's InputText (`primevue/inputtext/index.mjs`) writes on
    the native `input` event (`onInput() { this.writeValue(
    event.target.value, event) }`), and `useProcessedWidgets.ts`'s
    `createWidgetUpdateHandler` sets `widgetState.value` AND fires
    `widget.callback` on every keystroke -- Vue-rendered `widget.value` is
    always exactly what's on screen. The REAL bug is LEGACY CANVAS
    rendering: `TextWidget.onClick()` (`litegraph/src/widgets/
    TextWidget.ts`) opens `LGraphCanvas.prototype.prompt()`, a free-
    floating dialog with its OWN, disconnected `<input class="value">`.
    Per `LGraphCanvas.ts`, that dialog writes `widget.value` ONLY via
    Enter or its own "OK" button; a click anywhere else -- including this
    file's own `New State`/`New Group` DOM button (`_createActionButton()`
    builds a real `<button>`, not the `<canvas>` element the dialog's
    outside-click check tests for) -- just closes the dialog with no
    callback at all, dropping the typed text.

    This also nails down the Notebook/Controller asymmetry Eric has
    separately reported: notebook.js's name field (`state.nameFieldEl`) is
    a plain DOM `<input>` this pack owns and commits on the native `input`
    event -- it behaves like Vue mode UNCONDITIONALLY, on both renderers,
    because it was never a litegraph widget. `name` here is a REAL
    litegraph widget on purpose (FORMAT.md §6.3's hidden `set` widget needs
    a real widget slot next to it) -- the two differ by WIDGET KIND
    (pack-owned DOM input vs. native litegraph widget), not by an
    oversight. Do not "fix" the asymmetry by turning `name` into a DOM
    input to match the Notebook.
    """

    def test_flush_runs_before_the_group_vs_state_branch(self, controller_source: str) -> None:
        click = _method_body(controller_source, "_onCaptureClick()")
        flush_at = click.index("this._flushPendingNameEdit()")
        branch_at = click.index("if (isCategoryNameInput(this._w.name?.value))")
        assert flush_at < branch_at
        update_click = _method_body(controller_source, "_onUpdateClick()")
        update_flush_at = update_click.index("this._flushPendingNameEdit()")
        run_at = update_click.index("this._runAction(LABEL_UPDATE, () => this._doUpdate())")
        assert update_flush_at < run_at

    def test_flush_is_a_no_op_without_a_connected_dialog(self, controller_source: str) -> None:
        """No open canvas-legacy prompt (Vue mode always; canvas mode once
        Enter/OK already committed) -- widget.value is already
        authoritative, so this must do nothing rather than invent state."""
        flush = _method_body(controller_source, "_flushPendingNameEdit()")
        assert "const dialog = app.canvas?.prompt_box" in flush
        assert "if (!dialog?.isConnected) return" in flush
        assert "const input = dialog.querySelector?.('.value')" in flush
        assert "if (!input) return" in flush
        assert "if (live === widget.value) return" in flush

    def test_flush_commits_through_the_value_callback_dirty_idiom(
        self, controller_source: str
    ) -> None:
        """Same idiom `_clearNameField()` established for v0.67.2 -- value,
        then a guarded callback, then setDirtyCanvas -- so a flushed edit
        reads as a real commit to BOTH renderers, not just to `.value`."""
        flush = _method_body(controller_source, "_flushPendingNameEdit()")
        value_at = flush.index("widget.value = live")
        callback_at = flush.index("widget.callback?.(live)")
        dirty_at = flush.index("this.setDirtyCanvas(true, true)")
        close_at = flush.index("dialog.close?.()")
        assert value_at < callback_at < dirty_at < close_at
        assert "api.warn(`${NODE_TITLE}: name widget callback threw`, error)" in flush

    def test_flush_never_throws_out_of_the_click(self, controller_source: str) -> None:
        flush = _method_body(controller_source, "_flushPendingNameEdit()")
        assert flush.strip().startswith("const widget = this._w.name")
        assert "} catch (error) {" in flush
        assert "api.warn(`${NODE_TITLE}: could not flush pending name edit`, error)" in flush

    def test_prior_three_fixes_remain_wired(self, controller_source: str) -> None:
        """This fix changes only the READ that feeds the branch decision --
        confirms none of the three earlier fixes it must not regress were
        touched (full pins: test_name_field_clears_through_the_widget_
        callback, test_new_group_is_announced_with_toasts and
        test_save_layout_toasts_loudly_on_failure, above)."""
        clear = _method_body(controller_source, "_clearNameField()")
        assert "widget.value = ''" in clear
        assert "widget.callback?.('')" in clear
        new_cat = _method_body(controller_source, "async _doNewCategory()")
        assert 'this._toast(\'info\', NODE_TITLE, `Group "${name}" created' in new_cat
        save = _method_body(controller_source, "async _saveLayout()")
        assert "this._toast('error', NODE_TITLE, failMessage)" in save

    def test_uncommitted_hash_value_still_takes_the_group_branch(
        self, name_field_flush_probe: dict
    ) -> None:
        """The exact owner report: typed "# Portraits", never pressed
        Enter, clicked New State -- must still create a GROUP."""
        result = name_field_flush_probe["uncommittedHashValueFlushed"]
        assert result["actions"] == ["New Group"]
        assert result["value"] == "# Portraits"
        assert result["callbackCalls"] == ["# Portraits"]
        assert result["dialogClosed"] is True

    def test_committed_hash_value_still_takes_the_group_branch(
        self, name_field_flush_probe: dict
    ) -> None:
        """No dialog open at all (Vue mode; or canvas mode with Enter/OK
        already pressed) -- unaffected, still correct, no flush needed."""
        result = name_field_flush_probe["alreadyCommittedNoDialog"]
        assert result["actions"] == ["New Group"]
        assert result["value"] == "# Portraits"
        assert result["callbackCalls"] == []
        assert result["warnings"] == 0

    def test_non_hash_value_still_creates_a_state(self, name_field_flush_probe: dict) -> None:
        result = name_field_flush_probe["uncommittedPlainValueFlushed"]
        assert result["actions"] == ["New State"]
        assert result["value"] == "My New State"

    def test_already_in_sync_value_is_left_strictly_alone(
        self, name_field_flush_probe: dict
    ) -> None:
        """Nothing pending -- must not rewrite `.value`, re-fire the
        callback, or close a dialog it didn't act on."""
        result = name_field_flush_probe["alreadyInSyncDialogLeftAlone"]
        assert result["actions"] == ["New State"]
        assert result["callbackCalls"] == []
        assert result["dialogClosed"] is False

    def test_hash_only_value_reaches_the_group_branch_not_silence(
        self, name_field_flush_probe: dict
    ) -> None:
        """An empty/`#`-only value must still take the New Group branch, so
        `_doNewCategory()`'s own "Enter a group name after the #" toast
        (test_new_group_is_announced_with_toasts) is what the owner sees --
        a visible message, never silence."""
        result = name_field_flush_probe["hashOnlyStillTakesGroupBranch"]
        assert result["actions"] == ["New Group"]

    def test_disconnected_dialog_falls_back_to_the_widgets_own_value(
        self, name_field_flush_probe: dict
    ) -> None:
        result = name_field_flush_probe["disconnectedDialogIgnored"]
        assert result["actions"] == ["New State"]
        assert result["value"] == "OldValue"
        assert result["dialogClosed"] is False

    def test_missing_value_element_degrades_to_the_widgets_own_value(
        self, name_field_flush_probe: dict
    ) -> None:
        """A future frontend that renames/removes the `.value` class must
        not break the click -- just fall back to widget.value, silently."""
        result = name_field_flush_probe["missingValueElementDegradesSafely"]
        assert result["actions"] == ["New Group"]
        assert result["warnings"] == 0

    def test_a_throwing_internals_probe_never_breaks_the_click(
        self, name_field_flush_probe: dict
    ) -> None:
        """FORMAT.md §7 fail-soft law: internals that don't match what this
        fix verified must warn, not throw -- the click still completes
        using whatever `widget.value` already held."""
        result = name_field_flush_probe["throwingDialogNeverBreaksTheClick"]
        assert result["actions"] == ["New Group"]
        assert result["warnings"] == 1
        assert result["dialogClosed"] is False

    def test_update_click_also_flushes_before_the_rename_read(
        self, name_field_flush_probe: dict
    ) -> None:
        """Save State's rename-in-place read (`_saveAsNewName()`) has the
        identical stale-read hazard -- `_onUpdateClick()` flushes too."""
        result = name_field_flush_probe["updateClickAlsoFlushesBeforeRename"]
        assert result["actions"] == ["Save State"]
        assert result["value"] == "Renamed"


# ---------------------------------------------------------------------------
# v0.92.0 shared-panel-code round (owner decision 2026-09-08: "use Notebook
# as the model and make them all follow that paradigm"): controller.js
# (the LoRA Loader State Controller) had NO search box at all -- the odd one
# out among the panels with a left-hand list. It gains one now, matching its
# closest sibling's placement/rules and sharing web/lora_library/search.js's
# matcher with every other panel instead of a fourth reimplementation.
# ---------------------------------------------------------------------------


def test_search_field_is_added_above_the_list(controller_source: str) -> None:
    pane = _method_body(controller_source, "_buildStatePane()")
    assert "this._pane.searchEl = el('input', {" in pane
    assert "placeholder: 'Search states…'" in pane
    assert (
        "const leftPane = el('div', { className: 'llsc-pane-left' }, [\n"
        "          this._pane.searchEl,\n"
        "          this._pane.listEl,\n"
    ) in pane
    on_input = pane[pane.index("this._pane.searchEl.addEventListener('input'") :]
    assert "this._searchQuery = this._pane.searchEl.value" in on_input
    assert "this._renderStateList()" in on_input
    keydown = pane[pane.index("this._pane.searchEl.addEventListener('keydown'") :]
    assert "event.stopPropagation()" in keydown  # canvas hotkeys must not eat search typing
    assert "'Escape'" in keydown
    assert "this._pane.searchEl.value = ''" in keydown
    assert "this._searchQuery = ''" in keydown
    assert ".llsc-search {" in controller_source


def test_search_query_field_is_transient_never_serialized(controller_source: str) -> None:
    """§7.9: the query is pure VIEW state, correctly NOT persisted -- a
    plain instance field, initialized alongside its sibling collapse set,
    never written through a node property or a serialized widget."""
    ctor = _method_body(controller_source, "constructor(title = NODE_TITLE)")
    assert "this._searchQuery = ''" in ctor
    assert "properties['Search" not in controller_source
    assert "PROP_SEARCH" not in controller_source


def test_grouped_rows_filters_by_search_and_ignores_collapse_while_searching(
    controller_source: str,
) -> None:
    """FORMAT.md §7.2: while a query is active, collapse is ignored (a match
    hidden inside a collapsed group reads as "search is broken") and a group
    with zero matches drops out entirely -- the Notebook's/Universal State
    Controller's own rule, now shared here too."""
    matches = _method_body(controller_source, "_matchesSearch(entry)")
    assert "entryMatchesSearch(haystack, searchWords(query))" in matches
    assert "searchHaystack(entry.label || entry.slug || ''" in matches
    body = _method_body(controller_source, "_groupedRows()")
    assert "const searching = !!(this._searchQuery || '').trim()" in body
    assert "this._matchesSearch(entry)" in body
    assert "if (searching && !slugs.some((slug) => bySlug.has(slug))) continue" in body
    assert "if (!searching && this._collapsedCategories.has(category)) {" in body
    # with NO query this is exactly the pre-v0.92.0 method: full cache,
    # every header, collapse honored -- a pure de-dup for that path
    assert "const matching = searching\n          ? this._setsCache.filter" in body
    assert ": this._setsCache" in body


def test_category_header_forces_open_while_a_query_is_active(controller_source: str) -> None:
    header = _method_body(controller_source, "_buildCategoryHeader(category)")
    assert "const searching = !!(this._searchQuery || '').trim()" in header
    assert "const collapsed = !searching && this._collapsedCategories.has(category)" in header


def test_render_state_list_shows_no_match_message_and_disables_drag_while_searching(
    controller_source: str,
) -> None:
    """Mirrors notebook.js's own filtered-view rule: rows are not pushed to
    dragRows while a query is active (drag-reorder against a partial view
    would reorder the file in ways the view can't show); a plain click
    still selects/applies (that decision is a pointer-movement threshold,
    not gated on dragRows) -- see the source's own doc comment."""
    render = _method_body(controller_source, "_renderStateList()")
    assert "const searching = !!(this._searchQuery || '').trim()" in render
    assert 'text: `No states match "${query}".`' in render
    assert render.count("if (!searching) {") == 2  # header push + entry push
    assert "this._pane.dragRows.push({ kind: 'header'" in render
    assert "{ kind: 'entry', slug: entry.slug, label: entry.label, el: row }" in render


#: (query, entry label, expected) -- same table shape as
#: tests/test_universal_controller_js.py's SEARCH_MATCH_CASES; the first
#: case is the point of this whole round (a fourth panel now agrees).
CONTROLLER_SEARCH_MATCH_CASES = [
    ("style portrait", "Portrait Style A", True),  # multi-word AND, order-independent
    ("portrait style", "Portrait Style A", True),
    ("cine", "Cinematic Wide", True),  # partial-word substring
    ("portrait missing", "Portrait Style A", False),  # AND: one word absent
    ("", "Anything", True),  # blank query matches everything
    ("   ", "Anything", True),  # whitespace-only query matches everything
    ("xyz", "Portrait Style A", False),  # no match
]

CONTROLLER_SEARCH_MATCH_PROBE_JS = """
import { entryMatchesSearch, searchHaystack, searchWords } from './search.js'

function matchesSearch(entry) {
__MATCHES_SEARCH_BODY__
}

const cases = %(cases)s
const out = cases.map(([searchQuery, label]) =>
  matchesSearch.call({ _searchQuery: searchQuery }, { label })
)
process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def controller_search_match_api(
    controller_source: str, tmp_path_factory: pytest.TempPathFactory
) -> list:
    """Splices the REAL, extracted ``_matchesSearch(entry)`` method body --
    not a re-implementation -- into a standalone function and runs it under
    Node against the REAL search.js, exactly like
    tests/test_universal_controller_js.py's own ``search_match_api`` proves
    that panel's changed behavior. Here the method never changed BEHAVIOR
    (there was no search box before it), so this proves the NEW box's
    filtering is correct, using the shared matcher, from a standing start."""
    body = _method_body(controller_source, "_matchesSearch(entry)")
    script = CONTROLLER_SEARCH_MATCH_PROBE_JS % {
        "cases": json.dumps([[query, label] for query, label, _ in CONTROLLER_SEARCH_MATCH_CASES])
    }
    script = script.replace("__MATCHES_SEARCH_BODY__", body)
    layout = tmp_path_factory.mktemp("controller_search_match")
    shutil.copyfile(SEARCH_JS, layout / "search.js")
    probe = layout / "probe.mjs"
    probe.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(probe)], capture_output=True, text=True, timeout=30, cwd=layout
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


def test_controllers_new_search_box_filters_with_the_shared_multi_word_matcher(
    controller_search_match_api: list,
) -> None:
    expected = [case[2] for case in CONTROLLER_SEARCH_MATCH_CASES]
    pairs = zip(CONTROLLER_SEARCH_MATCH_CASES, controller_search_match_api, strict=True)
    for (query, label, want), got in pairs:
        msg = f"_matchesSearch(query={query!r}, label={label!r}) -> {got!r}, want {want!r}"
        assert got is want, msg
    assert controller_search_match_api == expected
