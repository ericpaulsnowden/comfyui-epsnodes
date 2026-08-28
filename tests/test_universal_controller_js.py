"""Frontend tests for the EPS Universal State Controller
(``web/lora_library/universal_controller.js``) -- the M1+M2 frontend for a
state controller that spans EVERY state-bearing node class in a workflow,
not just one loader family. Architectural blueprint:
``web/lora_library/controller.js`` (the Lora Loader State Controller); see
this new file's own header comment for the scope trims made porting it
(no drag-to-reorder, capture-only Save State, no NAS states-location UI).

Two-tier test strategy, mirroring ``tests/test_pll_bridge_js.py``:

1. The PURE, exported helpers (``validateStateValue``, ``buildStatePayload``,
   ``applyPlan``, ``exclusionsAfterToggle``, ``classToggleState``,
   ``summarizeCapture``, ``summarizeApply``, ``compareStateEntries``,
   ``parseCollapsedGroups``, ``isGroupNameInput``, ``groupNameFromInput``,
   ``normalizeExclusions``, ``normalizeRegistry``) touch no DOM/network and
   are driven for real under Node, in a served-layout tmp dir (the module
   imports ``./api.js`` and ``../../../scripts/app.js`` / ``scripts/api.js``,
   resolved exactly like ``test_pll_bridge_js.py``'s own fixture).
2. Everything else -- the registered LGraphNode subclass's optimistic
   save/delete, the provisional-slug in-flight guard, the armed two-click
   delete, the shared registry promise, the two-page toggle, the
   property-only exclusions shape, the callback-then-dirty write order --
   runs only inside a real litegraph node with a real DOM, which this repo
   has no browser harness for. Those are pinned via SOURCE-TEXT assertions
   against ``_method_body()``-extracted class methods, identical technique
   to ``test_pll_bridge_js.py``'s own ``controller_source`` pins.

The Backend routes this file's network calls target
(``/eps/state_registry``, ``/lora_library/universal_state*``) are a
concurrent backend round's contract, not exercised for real here -- every
probe stubs ``scripts/api.js``'s ``fetchApi`` as a no-op, exactly like
``test_pll_bridge_js.py``/``test_picker_js.py`` do for functions that never
reach the network.

Skips cleanly when Node isn't installed; the LIVE mechanics (a real DOM
widget, an actual round trip to the concurrent backend) are for the rig,
not here.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTROLLER_JS = REPO_ROOT / "web" / "lora_library" / "universal_controller.js"
API_JS = REPO_ROOT / "web" / "lora_library" / "api.js"
VERSION_JS = REPO_ROOT / "web" / "lora_library" / "version.js"
ENTRY_JS = REPO_ROOT / "web" / "lora_library.js"
LORA_CONTROLLER_JS = REPO_ROOT / "web" / "lora_library" / "controller.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

# --------------------------------------------------------------- the probe

PROBE_JS = r"""
import * as m from './extensions/comfyui-epsnodes/lora_library/universal_controller.js'

const out = {}

out.exports = {
  validateStateValue: typeof m.validateStateValue,
  buildStatePayload: typeof m.buildStatePayload,
  applyPlan: typeof m.applyPlan,
  exclusionsAfterToggle: typeof m.exclusionsAfterToggle,
  classToggleState: typeof m.classToggleState,
  summarizeCapture: typeof m.summarizeCapture,
  summarizeApply: typeof m.summarizeApply,
  compareStateEntries: typeof m.compareStateEntries,
  parseCollapsedGroups: typeof m.parseCollapsedGroups,
  isGroupNameInput: typeof m.isGroupNameInput,
  groupNameFromInput: typeof m.groupNameFromInput,
  normalizeExclusions: typeof m.normalizeExclusions,
  normalizeRegistry: typeof m.normalizeRegistry,
  registerControllerNode: typeof m.registerControllerNode,
  nameNodeDef: typeof m.nameNodeDef
}

// ------------------------------------------------------- validateStateValue

out.validateString = {
  ok: m.validateStateValue({ kind: 'string' }, 'hello'),
  tooLong: m.validateStateValue({ kind: 'string', max_len: 3 }, 'hello'),
  wrongType: m.validateStateValue({ kind: 'string' }, 5)
}
out.validateInt = {
  ok: m.validateStateValue({ kind: 'int', min: 0, max: 10 }, 5),
  belowMin: m.validateStateValue({ kind: 'int', min: 0 }, -1),
  aboveMax: m.validateStateValue({ kind: 'int', max: 10 }, 11),
  notInteger: m.validateStateValue({ kind: 'int' }, 1.5),
  wrongType: m.validateStateValue({ kind: 'int' }, '5')
}
out.validateFloat = {
  ok: m.validateStateValue({ kind: 'float', min: 0, max: 1 }, 0.5),
  belowMin: m.validateStateValue({ kind: 'float', min: 0 }, -0.1),
  aboveMax: m.validateStateValue({ kind: 'float', max: 1 }, 1.1),
  notFinite: m.validateStateValue({ kind: 'float' }, Infinity),
  nan: m.validateStateValue({ kind: 'float' }, NaN)
}
out.validateChoice = {
  okWithOptions: m.validateStateValue({ kind: 'choice', options: ['a', 'b'] }, 'a'),
  badWithOptions: m.validateStateValue({ kind: 'choice', options: ['a', 'b'] }, 'z'),
  okNoOptions: m.validateStateValue({ kind: 'choice' }, 'z'),
  numericOk: m.validateStateValue({ kind: 'choice', options: [1, 2] }, 2),
  wrongType: m.validateStateValue({ kind: 'choice' }, {})
}
out.validateLines = {
  ok: m.validateStateValue({ kind: 'lines' }, 'a\nb\nc'),
  wrongType: m.validateStateValue({ kind: 'lines' }, 5)
}
out.validateJsonArray = {
  ok: m.validateStateValue({ kind: 'json_array', items: 'string' }, ['a', 'b']),
  badItem: m.validateStateValue({ kind: 'json_array', items: 'string' }, ['a', 1]),
  notArray: m.validateStateValue({ kind: 'json_array', items: 'string' }, 'a'),
  defaultsToStringItems: m.validateStateValue({ kind: 'json_array' }, ['a'])
}
out.validateJsonObject = {
  ok: m.validateStateValue({ kind: 'json_object', key_pattern: '^[a-z]+$' }, { ab: 1 }),
  badKey: m.validateStateValue({ kind: 'json_object', key_pattern: '^[a-z]+$' }, { AB: 1 }),
  noPattern: m.validateStateValue({ kind: 'json_object' }, { ANY: 1 }),
  notObject: m.validateStateValue({ kind: 'json_object' }, 'nope'),
  arrayRejected: m.validateStateValue({ kind: 'json_object' }, [1, 2])
}
out.validateUnknownKind = m.validateStateValue({ kind: 'nope' }, 1)

// ------------------------------------------------------- buildStatePayload

const registry = {
  classes: {
    EPSSwitcher: {
      display: 'EPS Switcher',
      widgets: { mode: { kind: 'string', max_len: 20 } }
    },
    EPSResolution: {
      display: 'EPS Resolution',
      widgets: { width: { kind: 'int', min: 1, max: 8192 } }
    },
    EPSNotebook: {
      display: 'EPS Prompt Notebook',
      widgets: { file: { kind: 'string', max_len: 10 } }
    },
    EPSChoiceWidget: {
      display: 'EPS Choice Widget',
      widgets: { pick: { kind: 'choice' } }
    },
    NoWidgetsClass: { display: 'No Widgets', widgets: {} }
  }
}

out.buildPayloadBasic = m.buildStatePayload(
  [
    { pathId: '1', class: 'EPSSwitcher', title: 'Sw A', widgetValues: { mode: 'auto' } },
    { pathId: '2', class: 'EPSResolution', title: 'Res A', widgetValues: {} },
    { pathId: '3', class: 'UnknownClass', title: 'X', widgetValues: {} },
    { pathId: '4', class: 'NoWidgetsClass', title: 'Y', widgetValues: {} },
    { pathId: '5', class: 'EPSNotebook', title: 'Note', widgetValues: { file: 'a'.repeat(999) } }
  ],
  registry,
  { nodes: {}, classes: {} }
)

out.buildPayloadExclusions = m.buildStatePayload(
  [
    { pathId: '1', class: 'EPSSwitcher', title: 'Sw A', widgetValues: { mode: 'auto' } },
    { pathId: '2', class: 'EPSSwitcher', title: 'Sw B', widgetValues: { mode: 'manual' } },
    { pathId: '3', class: 'EPSResolution', title: 'Res A', widgetValues: { width: 512 } }
  ],
  registry,
  { nodes: { '2': false }, classes: { EPSResolution: false } }
)

// ------------------------------------------------------------------ applyPlan

const liveIndex = {
  '1': { class: 'EPSSwitcher', widgets: { mode: { value: 'auto', options: {} } } },
  '3': { class: 'EPSResolution', widgets: { width: { value: 512, options: {} } } },
  '9': { class: 'SomethingElse', widgets: {} },
  '20': {
    class: 'EPSChoiceWidget',
    widgets: { pick: { value: 'auto', options: { values: () => ['auto', 'manual'] } } }
  },
  // Live class MATCHES the state's class (so this is not a "not-found"), but
  // the registry has no entry for it at all -> exercises the 'unregistered' reason.
  '55': { class: 'UnregisteredLiveClass', widgets: {} }
}
const stateNodes = [
  { class: 'EPSSwitcher', id: '1', title: 'Sw A', widgets: { mode: 'manual' } },
  { class: 'EPSResolution', id: '3', title: 'Res A', widgets: { width: 99999 } },
  { class: 'EPSResolution', id: '404', title: 'Missing', widgets: {} },
  { class: 'EPSSwitcher', id: '9', title: 'Wrong class', widgets: {} },
  { class: 'UnregisteredLiveClass', id: '55', title: 'No reg', widgets: {} },
  { class: 'EPSChoiceWidget', id: '20', title: 'Choice', widgets: { pick: 'not-an-option' } }
]
out.applyPlanBasic = m.applyPlan(stateNodes, liveIndex, registry, { nodes: {}, classes: {} })
out.applyPlanExcluded = m.applyPlan(
  [{ class: 'EPSSwitcher', id: '1', title: 'Sw A', widgets: { mode: 'manual' } }],
  liveIndex,
  registry,
  { nodes: { '1': false }, classes: {} }
)
out.applyPlanClassExcluded = m.applyPlan(
  [{ class: 'EPSSwitcher', id: '1', title: 'Sw A', widgets: { mode: 'manual' } }],
  liveIndex,
  registry,
  { nodes: {}, classes: { EPSSwitcher: false } }
)

out.summarizeApplyBasic = m.summarizeApply(out.applyPlanBasic, registry)
out.summarizeApplyAllZero = m.summarizeApply(
  {
    matched: [],
    missing: [{ id: '1', class: 'EPSSwitcher', title: 'X', reason: 'not-found' }],
    skipped: []
  },
  registry
)

out.summarizeCaptureBasic = m.summarizeCapture('My State', out.buildPayloadBasic.nodes, registry)
out.summarizeCaptureSingular = m.summarizeCapture(
  'One',
  [{ class: 'EPSSwitcher', id: '1', title: 'A', widgets: {} }],
  registry
)

// ------------------------------------------------------------- misc helpers

out.compareStateEntries = [
  { name: 'Beta', slug: 'b' },
  { name: 'alpha', slug: 'a' },
  { name: 'alpha', slug: 'z' }
].sort(m.compareStateEntries)

out.parseCollapsedGroups = {
  array: m.parseCollapsedGroups(['A', 'B']),
  mixedArray: m.parseCollapsedGroups(['A', 1, null, 'B']),
  jsonString: m.parseCollapsedGroups('["A","B"]'),
  malformedString: m.parseCollapsedGroups('not json'),
  emptyString: m.parseCollapsedGroups(''),
  notArrayJson: m.parseCollapsedGroups('{"a":1}'),
  nullish: m.parseCollapsedGroups(null),
  number: m.parseCollapsedGroups(42)
}

out.groupNameInput = {
  hash: m.isGroupNameInput('# Portraits'),
  plain: m.isGroupNameInput('Portraits'),
  emptyIsNotHash: m.isGroupNameInput(''),
  extracted: m.groupNameFromInput('#   Portraits  '),
  extractedNoHash: m.groupNameFromInput('Portraits'),
  extractedManyHashes: m.groupNameFromInput('###Portraits')
}

out.normalizeExclusions = {
  onlyFalseKept: m.normalizeExclusions({
    nodes: { a: false, b: true, c: 'nope' },
    classes: { X: false, Y: true }
  }),
  malformed: m.normalizeExclusions('nonsense'),
  nullish: m.normalizeExclusions(null),
  empty: m.normalizeExclusions({})
}

out.normalizeRegistry = {
  ok: m.normalizeRegistry({
    format: 1,
    classes: { A: { display: 'A Class', widgets: { w: { kind: 'string' } } } }
  }),
  dropsMalformedClass: m.normalizeRegistry({ classes: { A: null, B: 'nope', C: {} } }),
  dropsMalformedWidget: m.normalizeRegistry({
    classes: { A: { widgets: { w: { kind: 'string' }, bad: 'nope' } } }
  }),
  missingDisplayFallsBackToClassId: m.normalizeRegistry({ classes: { A: { widgets: {} } } }),
  nullish: m.normalizeRegistry(null),
  noClasses: m.normalizeRegistry({})
}

// -------------------------------------------------------------- tri-state

out.classToggleState = {
  allOn: m.classToggleState({ nodes: {}, classes: {} }, 'K', ['1', '2']),
  allOffViaClassFlag: m.classToggleState({ nodes: {}, classes: { K: false } }, 'K', ['1', '2']),
  allOffViaEveryNode: m.classToggleState(
    { nodes: { '1': false, '2': false }, classes: {} },
    'K',
    ['1', '2']
  ),
  mixed: m.classToggleState({ nodes: { '1': false }, classes: {} }, 'K', ['1', '2']),
  noInstances: m.classToggleState({ nodes: {}, classes: {} }, 'K', [])
}

out.exclusionsAfterToggle = {
  nodeExclude: m.exclusionsAfterToggle(
    { nodes: {}, classes: {} },
    { type: 'node', pathId: '1', included: false }
  ),
  nodeReinclude: m.exclusionsAfterToggle(
    { nodes: { '1': false }, classes: {} },
    { type: 'node', pathId: '1', included: true }
  ),
  classAllOnClickTurnsAllOff: m.exclusionsAfterToggle(
    { nodes: {}, classes: {} },
    { type: 'class', klass: 'K', pathIds: ['1', '2'] }
  ),
  classMixedClickTurnsAllOn: m.exclusionsAfterToggle(
    { nodes: { '1': false }, classes: {} },
    { type: 'class', klass: 'K', pathIds: ['1', '2'] }
  ),
  classOffReopenRestoresPerNode: m.exclusionsAfterToggle(
    { nodes: { '1': false }, classes: { K: false } },
    { type: 'class', klass: 'K', pathIds: ['1', '2'] }
  ),
  unknownActionType: m.exclusionsAfterToggle(
    { nodes: { x: false }, classes: {} },
    { type: 'bogus' }
  )
}

// ---------------------------------------------------------- doesn't throw

m.registerControllerNode() // no LiteGraph/LGraphNode global -- must warn, not throw
m.nameNodeDef([{ name: 'EPSUniversalStateController', display_name: 'old' }])
m.nameNodeDef(null) // must not throw on a malformed defs array either
out.noThrow = true

// --- v0.83.0 JSON string<->structure seam ---
const OBJ_DESC = { kind: 'json_object', key_pattern: '^out_\\d+$' }
const ARR_DESC = { kind: 'json_array', items: 'string' }
out.captureSeam = {
  objParsed: m.captureWidgetValue(OBJ_DESC, '{"out_2": false}'),
  arrParsed: m.captureWidgetValue(ARR_DESC, '["A", "B"]'),
  emptyObj: m.captureWidgetValue(OBJ_DESC, ''),
  emptyArr: m.captureWidgetValue(ARR_DESC, '  '),
  already: m.captureWidgetValue(OBJ_DESC, { x: 1 }),
  malformed: m.captureWidgetValue(OBJ_DESC, '{nope'),
  scalarPassthrough: m.captureWidgetValue({ kind: 'int' }, 768)
}
out.writeSeam = {
  obj: m.widgetWriteValue(OBJ_DESC, { out_2: false }),
  arr: m.widgetWriteValue(ARR_DESC, ['A']),
  stringKept: m.widgetWriteValue(OBJ_DESC, '{"raw": true}'),
  scalar: m.widgetWriteValue({ kind: 'int' }, 512)
}
{
  const registry = { classes: { EPSDistributor: { display: 'D', widgets: { toggles: OBJ_DESC } } } }
  const payload = m.buildStatePayload(
    [{ class: 'EPSDistributor', pathId: '2', title: 'D',
       widgetValues: { toggles: '{"out_2": false}' } }],
    registry, {}
  )
  const stored = payload.nodes[0]?.widgets?.toggles
  const liveIndex = { '2': { class: 'EPSDistributor', widgets: { toggles: { options: {} } } } }
  const plan = m.applyPlan(payload.nodes, liveIndex, registry, {})
  out.seamRoundTrip = { stored, written: plan.matched[0]?.writes?.[0]?.value }
}

process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def controller_api(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Runs the probe against the REAL universal_controller.js in a
    served-layout tmp dir (module docstring), mirroring
    test_pll_bridge_js.py's ``bridge_api`` fixture exactly."""
    layout = tmp_path_factory.mktemp("web_root")

    module_dir = layout / "extensions" / "comfyui-epsnodes" / "lora_library"
    module_dir.mkdir(parents=True)
    shutil.copyfile(CONTROLLER_JS, module_dir / "universal_controller.js")
    # universal_controller.js imports `./api.js`, which imports `./version.js`
    # and `../../../scripts/api.js` -- copy the real siblings, stub the
    # served ComfyUI scripts exactly as test_pll_bridge_js.py/test_picker_js.py do.
    shutil.copyfile(API_JS, module_dir / "api.js")
    shutil.copyfile(VERSION_JS, module_dir / "version.js")

    scripts = layout / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "api.js").write_text("export const api = { fetchApi: () => {} }\n", encoding="utf-8")
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
    """Raw text of universal_controller.js -- for SOURCE-STRUCTURE
    assertions about code that only runs inside a real litegraph node with a
    real DOM (this repo has no browser harness to drive), matching
    test_pll_bridge_js.py's ``controller_source`` fixture convention."""
    return CONTROLLER_JS.read_text(encoding="utf-8")


def _method_body(source_text: str, signature: str) -> str:
    """Body of an INDENTED class method ``      <signature> {`` up to the
    next method at the same indent -- identical helper to
    test_pll_bridge_js.py's own (this file's class methods sit at the same
    6-space indent controller.js's do)."""
    head = f"      {signature} {{\n"
    start = source_text.index(head) + len(head)
    end = re.search(r"\n      \}\n", source_text[start:])
    assert end, f"{signature}: closing brace not found"
    return source_text[start : start + end.start()]


def test_syntax_is_valid() -> None:
    result = subprocess.run(
        [NODE, "--check", "--input-type=module"],
        input=CONTROLLER_JS.read_text(encoding="utf-8"),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


# ------------------------------------------------------------- pure helpers


class TestValidateStateValue:
    def test_string(self, controller_api: dict) -> None:
        v = controller_api["validateString"]
        assert v["ok"] == {"ok": True}
        assert v["tooLong"]["ok"] is False
        assert v["wrongType"]["ok"] is False

    def test_int(self, controller_api: dict) -> None:
        v = controller_api["validateInt"]
        assert v["ok"] == {"ok": True}
        assert v["belowMin"]["ok"] is False
        assert v["aboveMax"]["ok"] is False
        assert v["notInteger"]["ok"] is False
        assert v["wrongType"]["ok"] is False

    def test_float(self, controller_api: dict) -> None:
        v = controller_api["validateFloat"]
        assert v["ok"] == {"ok": True}
        assert v["belowMin"]["ok"] is False
        assert v["aboveMax"]["ok"] is False
        assert v["notFinite"]["ok"] is False
        assert v["nan"]["ok"] is False

    def test_choice_against_provided_options(self, controller_api: dict) -> None:
        v = controller_api["validateChoice"]
        assert v["okWithOptions"] == {"ok": True}
        assert v["badWithOptions"] == {"ok": False, "error": "value not in options"}
        # No options supplied (registry-only, capture time) -- any string/number passes.
        assert v["okNoOptions"] == {"ok": True}
        assert v["numericOk"] == {"ok": True}
        assert v["wrongType"]["ok"] is False

    def test_lines(self, controller_api: dict) -> None:
        v = controller_api["validateLines"]
        assert v["ok"] == {"ok": True}
        assert v["wrongType"]["ok"] is False

    def test_json_array_items(self, controller_api: dict) -> None:
        v = controller_api["validateJsonArray"]
        assert v["ok"] == {"ok": True}
        assert v["badItem"]["ok"] is False
        assert v["notArray"]["ok"] is False
        assert v["defaultsToStringItems"] == {"ok": True}

    def test_json_object_key_pattern(self, controller_api: dict) -> None:
        v = controller_api["validateJsonObject"]
        assert v["ok"] == {"ok": True}
        assert v["badKey"]["ok"] is False
        assert v["noPattern"] == {"ok": True}
        assert v["notObject"]["ok"] is False
        assert v["arrayRejected"]["ok"] is False

    def test_unknown_kind(self, controller_api: dict) -> None:
        expected = {"ok": False, "error": 'unknown kind "nope"'}
        assert controller_api["validateUnknownKind"] == expected


class TestBuildStatePayload:
    def test_drops_non_state_bearing_classes(self, controller_api: dict) -> None:
        nodes = controller_api["buildPayloadBasic"]["nodes"]
        classes = {n["class"] for n in nodes}
        assert "UnknownClass" not in classes  # not in the registry at all
        assert "NoWidgetsClass" not in classes  # registered but zero declared widgets

    def test_missing_widget_is_skipped_with_a_warning(self, controller_api: dict) -> None:
        payload = controller_api["buildPayloadBasic"]
        res_entry = next(n for n in payload["nodes"] if n["class"] == "EPSResolution")
        assert res_entry["widgets"] == {}  # "width" was never supplied
        assert any('missing widget "width"' in w for w in payload["warnings"])

    def test_invalid_value_is_skipped_not_the_whole_node(self, controller_api: dict) -> None:
        payload = controller_api["buildPayloadBasic"]
        note_entry = next(n for n in payload["nodes"] if n["class"] == "EPSNotebook")
        # The captured "file" value is 999 chars against a max_len of 10 --
        # invalid, so it's dropped from the node's widgets (the NODE still
        # appears in the payload; only that one field is missing).
        assert note_entry["widgets"] == {}
        warnings = payload["warnings"]
        assert any('skipping "file"' in w and "exceeds max_len 10" in w for w in warnings)

    def test_exclusions_drop_nodes_and_whole_classes(self, controller_api: dict) -> None:
        nodes = controller_api["buildPayloadExclusions"]["nodes"]
        by_path = {n["id"]: n for n in nodes}
        assert "1" in by_path  # included (default)
        assert "2" not in by_path  # excluded by node id
        assert "3" not in by_path  # excluded by whole class


class TestApplyPlan:
    def test_matches_by_exact_pathid_and_class(self, controller_api: dict) -> None:
        plan = controller_api["applyPlanBasic"]
        matched_ids = {m["id"] for m in plan["matched"]}
        assert matched_ids == {"1", "3", "20"}

    def test_class_mismatch_and_not_found_are_both_missing(self, controller_api: dict) -> None:
        plan = controller_api["applyPlanBasic"]
        missing_by_id = {m["id"]: m["reason"] for m in plan["missing"]}
        assert missing_by_id["404"] == "not-found"
        assert missing_by_id["9"] == "class-mismatch"  # live id 9 is "SomethingElse"

    def test_unregistered_class_counts_as_missing(self, controller_api: dict) -> None:
        plan = controller_api["applyPlanBasic"]
        missing_by_id = {m["id"]: m["reason"] for m in plan["missing"]}
        assert missing_by_id["55"] == "unregistered"

    def test_choice_validated_against_live_widget_options(self, controller_api: dict) -> None:
        plan = controller_api["applyPlanBasic"]
        choice_node = next(m for m in plan["matched"] if m["id"] == "20")
        assert choice_node["writes"] == []  # 'not-an-option' isn't in the live options() list
        assert choice_node["invalid"] == [{"name": "pick", "reason": "value not in options"}]

    def test_never_writes_an_invalid_value(self, controller_api: dict) -> None:
        plan = controller_api["applyPlanBasic"]
        res_a = next(m for m in plan["matched"] if m["id"] == "3")
        assert res_a["writes"] == []  # 99999 > max 8192
        assert res_a["invalid"] == [{"name": "width", "reason": "above max 8192"}]
        sw_a = next(m for m in plan["matched"] if m["id"] == "1")
        assert sw_a["writes"] == [{"name": "mode", "value": "manual"}]
        assert sw_a["invalid"] == []

    def test_node_level_exclusion_skips_the_node(self, controller_api: dict) -> None:
        plan = controller_api["applyPlanExcluded"]
        assert plan["matched"] == []
        expected = {"id": "1", "class": "EPSSwitcher", "title": "Sw A", "reason": "excluded"}
        assert plan["skipped"] == [expected]

    def test_class_level_exclusion_skips_every_instance(self, controller_api: dict) -> None:
        plan = controller_api["applyPlanClassExcluded"]
        assert plan["matched"] == []
        assert plan["skipped"][0]["reason"] == "excluded"


class TestSummarizeApply:
    def test_diff_toast_names_matched_missing_and_partial(self, controller_api: dict) -> None:
        result = controller_api["summarizeApplyBasic"]
        assert result["text"].startswith("Applied 3 of 6")
        assert "not found" in result["text"]
        assert "with skipped field(s)" in result["text"]
        assert result["severity"] == "success"

    def test_zero_matched_is_warn_not_a_silent_success(self, controller_api: dict) -> None:
        result = controller_api["summarizeApplyAllZero"]
        assert result["text"].startswith("Applied 0 of 1")
        assert result["severity"] == "warn"


class TestSummarizeCapture:
    def test_counts_per_class_and_pluralizes(self, controller_api: dict) -> None:
        text = controller_api["summarizeCaptureBasic"]
        assert text.startswith('Saved "My State" — ')
        assert "eps switcher" in text  # singular: exactly one EPSSwitcher captured

    def test_singular_count_is_not_pluralized(self, controller_api: dict) -> None:
        text = controller_api["summarizeCaptureSingular"]
        assert "1 eps switcher" in text
        assert "eps switchers" not in text


def test_compare_state_entries_sorts_by_name_then_slug(controller_api: dict) -> None:
    ordered = controller_api["compareStateEntries"]
    assert [e["slug"] for e in ordered] == ["a", "z", "b"]  # alpha/a, alpha/z, beta/b


class TestParseCollapsedGroups:
    def test_tolerant_of_every_shape(self, controller_api: dict) -> None:
        p = controller_api["parseCollapsedGroups"]
        assert p["array"] == ["A", "B"]
        assert p["mixedArray"] == ["A", "B"]
        assert p["jsonString"] == ["A", "B"]
        assert p["malformedString"] == []
        assert p["emptyString"] == []
        assert p["notArrayJson"] == []
        assert p["nullish"] == []
        assert p["number"] == []


class TestGroupNameInput:
    def test_hash_prefix_detection_and_extraction(self, controller_api: dict) -> None:
        g = controller_api["groupNameInput"]
        assert g["hash"] is True
        assert g["plain"] is False
        assert g["emptyIsNotHash"] is False
        assert g["extracted"] == "Portraits"
        assert g["extractedNoHash"] == "Portraits"
        assert g["extractedManyHashes"] == "Portraits"


class TestNormalizeExclusions:
    def test_only_false_entries_survive(self, controller_api: dict) -> None:
        n = controller_api["normalizeExclusions"]["onlyFalseKept"]
        assert n == {"nodes": {"a": False}, "classes": {"X": False}}

    def test_malformed_input_degrades_to_empty(self, controller_api: dict) -> None:
        assert controller_api["normalizeExclusions"]["malformed"] == {"nodes": {}, "classes": {}}
        assert controller_api["normalizeExclusions"]["nullish"] == {"nodes": {}, "classes": {}}
        assert controller_api["normalizeExclusions"]["empty"] == {"nodes": {}, "classes": {}}


class TestNormalizeRegistry:
    def test_well_formed_registry_round_trips(self, controller_api: dict) -> None:
        result = controller_api["normalizeRegistry"]["ok"]
        assert result["classes"]["A"]["display"] == "A Class"
        assert "w" in result["classes"]["A"]["widgets"]

    def test_malformed_class_entries_are_dropped(self, controller_api: dict) -> None:
        result = controller_api["normalizeRegistry"]["dropsMalformedClass"]
        assert "A" not in result["classes"]
        assert "B" not in result["classes"]
        assert "C" in result["classes"]  # a bare {} is still a valid (empty) class entry

    def test_malformed_widget_entries_are_dropped(self, controller_api: dict) -> None:
        result = controller_api["normalizeRegistry"]["dropsMalformedWidget"]
        widgets = result["classes"]["A"]["widgets"]
        assert "w" in widgets
        assert "bad" not in widgets

    def test_missing_display_falls_back_to_class_id(self, controller_api: dict) -> None:
        result = controller_api["normalizeRegistry"]["missingDisplayFallsBackToClassId"]
        assert result["classes"]["A"]["display"] == "A"

    def test_malformed_top_level_degrades_to_no_classes(self, controller_api: dict) -> None:
        assert controller_api["normalizeRegistry"]["nullish"] == {"classes": {}}
        assert controller_api["normalizeRegistry"]["noClasses"] == {"classes": {}}


class TestClassToggleStateTriState:
    """Per-class master checkbox, the switchers' Toggle All shape."""

    def test_all_on(self, controller_api: dict) -> None:
        assert controller_api["classToggleState"]["allOn"] is True

    def test_all_off_via_class_level_flag(self, controller_api: dict) -> None:
        assert controller_api["classToggleState"]["allOffViaClassFlag"] is False

    def test_all_off_via_every_node_individually(self, controller_api: dict) -> None:
        assert controller_api["classToggleState"]["allOffViaEveryNode"] is False

    def test_mixed_is_null_indeterminate(self, controller_api: dict) -> None:
        assert controller_api["classToggleState"]["mixed"] is None

    def test_no_live_instances_is_vacuously_on(self, controller_api: dict) -> None:
        assert controller_api["classToggleState"]["noInstances"] is True


class TestExclusionsAfterToggle:
    def test_node_toggle_off_and_back_on(self, controller_api: dict) -> None:
        e = controller_api["exclusionsAfterToggle"]
        assert e["nodeExclude"] == {"nodes": {"1": False}, "classes": {}}
        assert e["nodeReinclude"] == {"nodes": {}, "classes": {}}

    def test_class_master_all_on_click_turns_everything_off(self, controller_api: dict) -> None:
        e = controller_api["exclusionsAfterToggle"]["classAllOnClickTurnsAllOff"]
        assert e == {"nodes": {}, "classes": {"K": False}}

    def test_class_master_mixed_click_turns_everything_on(self, controller_api: dict) -> None:
        e = controller_api["exclusionsAfterToggle"]["classMixedClickTurnsAllOn"]
        assert e == {"nodes": {}, "classes": {}}  # node '1's own exclusion is cleared too

    def test_rechecking_an_off_class_restores_per_node_states(self, controller_api: dict) -> None:
        """Owner-spec contract: turning a class back ON does not force every
        node to "included" -- it removes the class-level veto and lets
        whatever per-node picks already existed show through again."""
        e = controller_api["exclusionsAfterToggle"]["classOffReopenRestoresPerNode"]
        # node 1 stays individually excluded; only the class-level flag is gone
        assert e == {"nodes": {"1": False}, "classes": {}}

    def test_unknown_action_type_is_a_pure_no_op_copy(self, controller_api: dict) -> None:
        e = controller_api["exclusionsAfterToggle"]["unknownActionType"]
        assert e == {"nodes": {"x": False}, "classes": {}}


def test_module_import_never_throws_and_exports_every_helper(controller_api: dict) -> None:
    assert controller_api["noThrow"] is True
    for name, kind in controller_api["exports"].items():
        assert kind == "function", f"{name} is not exported as a function"


# --------------------------------------------------- source-text structure


def test_node_type_and_title_and_category(source: str) -> None:
    assert "const NODE_TYPE = 'EPSUniversalStateController'" in source
    assert "const NODE_TITLE = 'EPS Universal State Controller'" in source
    assert "const NODE_CATEGORY = 'EPSNodes'" in source


def test_registry_is_fetched_once_via_a_module_scope_shared_promise(source: str) -> None:
    assert "let stateRegistryPromise = null" in source
    fetch_body = source.split("function fetchStateRegistry() {\n", 1)[1].split("\n}\n", 1)[0]
    assert "if (stateRegistryPromise) return stateRegistryPromise" in fetch_body
    assert "stateRegistryPromise = (async () => {" in fetch_body
    # the literal route path is a constant, never inlined as a string here
    assert "GET /eps/state_registry" not in fetch_body
    assert "STATE_REGISTRY_ROUTE" in fetch_body
    # a failed fetch degrades to an empty registry rather than rejecting
    assert "return { classes: {} }" in fetch_body


def test_included_nodes_property_stores_exclusions_only_default_included(source: str) -> None:
    ctor = _method_body(source, "constructor(title = NODE_TITLE)")
    assert "this.addProperty(PROP_INCLUDED_NODES, { nodes: {}, classes: {} }, 'object')" in ctor
    exclusions = _method_body(source, "_exclusions()")
    assert "normalizeExclusions(this.properties?.[PROP_INCLUDED_NODES])" in exclusions
    # normalizeExclusions itself (pure helper, also probed above) is the ONLY
    # place a `true` entry could sneak in -- confirm it never writes one.
    assert "value === false" in source
    assert re.search(r"nodes\[key\] = true", source) is None


def test_optimistic_create_paints_before_the_network_call(source: str) -> None:
    begin = _method_body(source, "_beginOptimisticCreate(name, count)")
    assert "this._nextProvisionalSlug()" in begin
    assert "this._saveInFlightSlugs.add(provisionalSlug)" in begin
    assert "this._renderStateList()" in begin
    assert "this._selectEntry(entry, { loadName: false })" in begin
    assert "this._clearNameField()" in begin
    capture = _method_body(source, "async _doCapture()")
    # the optimistic paint happens BEFORE the POST is awaited
    begin_idx = capture.index("_beginOptimisticCreate(name, nodes.length)")
    post_idx = capture.index("await api.postJson(STATE_ROUTE")
    assert begin_idx < post_idx
    assert "this._rollbackOptimisticCreate(" in capture
    assert capture.index("try {") < capture.index("this._rollbackOptimisticCreate(")
    assert "this._saveInFlightSlugs.delete(provisionalSlug)" in capture


def test_provisional_slug_guard_protects_the_in_flight_row(source: str) -> None:
    apply_states = _method_body(source, "_applyStatesResponse(data)")
    assert "this._saveInFlightSlugs.has(s.slug)" in apply_states
    assert "this._deleteInFlightSlugs.has(s.slug)" in apply_states
    assert "carriedProvisional" in apply_states
    assert "built.sort(compareStateEntries)" in apply_states


def test_delete_is_optimistic_with_its_own_in_flight_guard(source: str) -> None:
    do_delete = _method_body(source, "async _doDelete(entry)")
    assert do_delete.index("this._deleteInFlightSlugs.add(entry.slug)") < do_delete.index(
        "await api.postJson(STATE_DELETE_ROUTE"
    )
    assert "this._renderStateList()" in do_delete.split("await api.postJson")[0]
    assert "this._deleteInFlightSlugs.delete(entry.slug)" in do_delete
    assert "previousCache" in do_delete  # rollback restores the exact prior list on failure


def test_armed_two_click_delete_confirm(source: str) -> None:
    on_delete = _method_body(source, "_onDeleteClick()")
    assert "if (!button._armed) {" in on_delete
    assert "button._armed = true" in on_delete
    assert "LABEL_DELETE_CONFIRM" in on_delete
    assert "setTimeout(() => this._disarmDeleteButton(), DELETE_CONFIRM_MS)" in on_delete
    disarm = _method_body(source, "_disarmDeleteButton()")
    assert "button._armed = false" in disarm
    assert "clearTimeout(button._armTimer)" in disarm


def test_group_delete_is_also_an_armed_two_click(source: str) -> None:
    header = _method_body(source, "_buildCategoryHeader(category, matchCount)")
    assert "if (!deleteBtn._armed) {" in header
    assert "CATEGORY_DELETE_CONFIRM_MS" in header
    assert "this._deleteCategory(category)" in header


def test_apply_writes_value_then_callback_then_one_dirty_at_the_end(source: str) -> None:
    write_plan = _method_body(source, "_writeApplyPlan(plan, discovered)")
    value_idx = write_plan.index("widget.value = write.value")
    callback_idx = write_plan.index("widget.callback?.(write.value, app.canvas, liveNode)")
    assert value_idx < callback_idx
    # exactly one setDirtyCanvas call, AFTER the write loop -- not per-write
    assert write_plan.count("setDirtyCanvas") == 1
    assert write_plan.rfind("setDirtyCanvas") > callback_idx
    do_apply = _method_body(source, "async _doApply()")
    assert "applyPlan(full?.nodes || [], liveIndex, registry, exclusions)" in do_apply
    assert "this._writeApplyPlan(plan, discovered)" in do_apply
    assert "summarizeApply(plan, registry)" in do_apply


def test_two_page_toggle_is_a_property_free_instance_field(source: str) -> None:
    ctor = _method_body(source, "constructor(title = NODE_TITLE)")
    assert "this._activePage = 'states'" in ctor
    # Only TWO addProperty calls exist in the whole file, and neither is
    # about the page toggle -- it is a plain instance field, never a
    # right-click-Properties-panel entry.
    assert source.count("this.addProperty(") == 2
    assert "addProperty(PROP_COLLAPSED_GROUPS" in source
    assert "addProperty(PROP_INCLUDED_NODES" in source
    set_page = _method_body(source, "_setActivePage(page)")
    assert "this._activePage = page" in set_page
    assert "setDirtyCanvas" not in set_page  # a page switch is DOM-only, never persisted/dirtied
    assert "this.properties" not in set_page


def test_group_creation_via_hash_prefix_in_the_name_field(source: str) -> None:
    on_save = _method_body(source, "_onSaveClick()")
    assert "isGroupNameInput(this._w.name?.value)" in on_save
    assert "this._doNewCategory()" in on_save
    new_cat = _method_body(source, "async _doNewCategory()")
    assert "groupNameFromInput(this._w.name?.value)" in new_cat
    assert "this._ensureLayoutLoaded()" in new_cat
    assert "this._clearNameField()" in new_cat
    assert "this._saveLayout()" in new_cat


def test_collapsed_groups_property_read_and_write_halves(source: str) -> None:
    apply_prop = _method_body(source, "_applyCollapsedGroupsFromProperty()")
    assert "parseCollapsedGroups(this.properties?.[PROP_COLLAPSED_GROUPS])" in apply_prop
    sync_prop = _method_body(source, "_syncCollapsedGroupsProperty()")
    assert "properties[PROP_COLLAPSED_GROUPS] = Array.from(this._collapsedCategories)" in sync_prop
    assert "this.setDirtyCanvas(true, true)" in sync_prop
    toggle = _method_body(source, "_toggleCategoryCollapsed(category)")
    assert "this._syncCollapsedGroupsProperty()" in toggle


def test_layout_edit_is_refused_before_the_first_successful_load(source: str) -> None:
    ensure = _method_body(source, "async _ensureLayoutLoaded()")
    assert "if (this._layoutLoaded) return true" in ensure
    assert "MSG_LAYOUT_NOT_LOADED" in ensure
    with_loaded = _method_body(source, "_withLoadedLayout(label, edit)")
    assert "if (this._layoutLoaded) {" in with_loaded
    assert "this._ensureLayoutLoaded()" in with_loaded


def test_shared_poller_registers_and_unregisters_module_scope_state(source: str) -> None:
    assert "const liveUniversalControllers = new Set()" in source
    register = _method_body(source, "onAdded()")
    assert "registerUniversalController(this)" in register
    removed = _method_body(source, "onRemoved()")
    assert "unregisterUniversalController(this)" in removed
    refresh = source.split("async function sharedUniversalRefresh(", 1)[1]
    refresh = refresh.split("\nfunction announceStatesChanged", 1)[0]
    assert "if (uscSharedFetch) {" in refresh
    assert "uscRefetchQueued = true" in refresh
    assert "_applyStatesResponse(data)" in refresh


def test_registering_a_controller_kicks_an_immediate_refresh(source: str) -> None:
    """A node added mid-session must not sit on an empty list for up to
    USC_POLL_MS waiting on the next interval tick -- controller.js's
    scheduleSharedSetsRefresh() one-tick coalescer, duplicated here."""
    register_fn = source.split("function registerUniversalController(node) {\n", 1)[1]
    register_fn = register_fn.split("\n}\n", 1)[0]
    assert "scheduleUniversalKick()" in register_fn
    kick = source.split("function scheduleUniversalKick() {\n", 1)[1].split("\n}\n", 1)[0]
    assert "if (uscKickScheduled) return" in kick
    assert "setTimeout(() => {" in kick
    assert "sharedUniversalRefresh({ force: true })" in kick


def test_layout_is_only_touched_when_the_response_actually_carries_one(source: str) -> None:
    """Verified against the concurrent backend's real route handlers: only
    `GET /lora_library/universal_states` returns a `layout` key -- the
    create/delete POST responses carry `{ok, states, ...}` alone. Treating
    a MISSING layout as "the empty default" and writing it through would
    silently wipe every real group after the very next Save or Delete."""
    apply_states = _method_body(source, "_applyStatesResponse(data)")
    guard = "if (data && typeof data.layout === 'object' && data.layout !== null) {"
    assert guard in apply_states
    assert "this._layoutLoaded = true" in apply_states
    assert "normalizeLayoutClient(data.layout)" in apply_states
    # the OLD, buggy unconditional forms must be gone from THIS method,
    # not merely shadowed by the new guard (a stray second copy would
    # reintroduce the data-loss bug even with the guard present above it)
    assert "if (Array.isArray(data?.states)) this._layoutLoaded = true" not in apply_states
    assert "normalizeLayoutClient(data?.layout)" not in apply_states


def test_move_to_group_replaces_drag_reorder(source: str) -> None:
    """Scope trim #1 (see the file header): no drag machinery this round --
    confirm the file is honest about it (the header comment CITES
    controller.js's drag method names as the thing that was NOT ported, so
    this checks for their DEFINITION signature, not their mere mention) and
    that the select-based alternative actually writes through the same
    _saveLayout() path."""
    assert "_onStateRowPointerDown(event, source) {" not in source
    assert "_computeStateDropTarget(clientY, excludeSlug) {" not in source
    assert "_finishStateDrag(drag) {" not in source
    assert "NO drag-to-reorder" in source
    move = _method_body(source, "async _onMoveToGroupChange()")
    assert "pullSlugFromLayout(layout, entry.slug)" in move
    assert "await this._saveLayout()" in move


def test_save_state_is_capture_only_no_update_in_place(source: str) -> None:
    """Scope trim #2 (file header): this node has exactly three buttons per
    spec, so there is no separate overwrite-in-place path."""
    assert "LABEL_SAVE = 'Save State'" in source
    assert "LABEL_APPLY = 'Apply State'" in source
    assert "LABEL_DELETE = 'Delete State'" in source
    assert "_doUpdate" not in source
    assert "_saveAsNewName" not in source


def test_configure_repairs_a_stale_class_id_title(source: str) -> None:
    configure = _method_body(source, "configure(info)")
    assert "super.configure(info)" in configure
    assert "if (this.title === NODE_TYPE) this.title = NODE_TITLE" in configure


def test_is_virtual_node_never_enters_the_api_prompt(source: str) -> None:
    ctor = _method_body(source, "constructor(title = NODE_TITLE)")
    assert "this.isVirtualNode = true" in ctor


def test_no_import_from_lora_loader_state_controller(source: str) -> None:
    """The task's ownership boundary: this file may only import from
    ./api.js -- never controller.js, whose blueprint it clones by hand."""
    assert "from './controller.js'" not in source
    assert "from './notebook.js'" not in source
    assert "import { app }" in source
    assert "import * as api from './api.js'" in source


# ------------------------------------------------------- loader wiring (§7.1)


def test_entry_file_wires_the_new_module_alongside_controller_js() -> None:
    entry = ENTRY_JS.read_text(encoding="utf-8")
    assert "import * as universalController from './lora_library/universal_controller.js'" in entry
    init_body = entry.split("init() {\n", 1)[1].split("\n  },\n", 1)[0]
    assert "controller.registerControllerNode()" in init_body
    assert "universalController.registerControllerNode()" in init_body
    defs_body = entry.split("beforeRegisterVueAppNodeDefs(defs) {\n", 1)[1].split("\n  },\n", 1)[0]
    assert "controller.nameNodeDef(defs)" in defs_body
    assert "universalController.nameNodeDef(defs)" in defs_body


def test_entry_file_still_wires_every_pre_existing_module() -> None:
    """Regression guard: adding the new module must not disturb any
    existing registration line in the shared entry file."""
    entry = ENTRY_JS.read_text(encoding="utf-8")
    for needle in (
        "notebook.attachNotebookWidget(node)",
        "promptBuilder.attachPromptBuilderPanel(node)",
        "sets.attachApplySetBehavior(node)",
        "picker.attachPickerPanel(node)",
        "pathHeal.attachConfigureHeal(node)",
        "controller.registerControllerNode()",
        "controller.nameNodeDef(defs)",
        "initSettings()",
        "sets.initSetsFreshness()",
    ):
        assert needle in entry, needle


def test_lora_loader_state_controller_is_untouched() -> None:
    """Ownership boundary: this task must never edit controller.js itself."""
    text = LORA_CONTROLLER_JS.read_text(encoding="utf-8")
    assert "EPSUniversalStateController" not in text
    assert "universal_controller" not in text


# --------------- v0.83.0 rig catch: the JSON string<->structure seam


class TestJsonWidgetSeam:
    """A json_object/json_array widget carries a JSON STRING on the live
    widget but the §4.3 state stores the PARSED structure. The rig caught
    captures silently dropping every such widget; these pin the seam."""

    def test_capture_parses_json_string_widgets(self, controller_api: dict) -> None:
        out = controller_api["captureSeam"]
        assert out["objParsed"] == {"ok": True, "value": {"out_2": False}}
        assert out["arrParsed"] == {"ok": True, "value": ["A", "B"]}
        assert out["emptyObj"] == {"ok": True, "value": {}}
        assert out["emptyArr"] == {"ok": True, "value": []}
        assert out["already"] == {"ok": True, "value": {"x": 1}}
        assert out["malformed"]["ok"] is False
        assert "unparseable" in out["malformed"]["error"]
        assert out["scalarPassthrough"] == {"ok": True, "value": 768}

    def test_apply_serializes_back_to_the_widget_string(self, controller_api: dict) -> None:
        out = controller_api["writeSeam"]
        assert out["obj"] == '{"out_2":false}'
        assert out["arr"] == '["A"]'
        assert out["stringKept"] == '{"raw": true}'
        assert out["scalar"] == 512

    def test_build_payload_stores_structures_and_plan_writes_strings(
        self, controller_api: dict
    ) -> None:
        out = controller_api["seamRoundTrip"]
        # capture stored the PARSED object...
        assert out["stored"] == {"out_2": False}
        # ...and the apply plan's write carries the STRING form back
        assert out["written"] == '{"out_2":false}'
