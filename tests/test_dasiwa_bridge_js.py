"""Frontend tests for the EPS LoRA Picker's DaSiWa Advanced LoRA Loader
bridge (``web/lora_library/dasiwa_bridge.js`` -- FORMAT.md §6.13, M4).

The bridge is the second entry in picker.js's comfyClass-keyed send-adapter
registry, mirroring ``pll_bridge.js``'s surface (find / probe / pure row
mapping / write). Unlike the rgthree bridge there is no widget surgery: the
whole write is one JSON string into ``properties.stack_data`` AND the
``stack_data`` widget (the node's own canvas UI mirrors properties -> widget
on every draw, so both sinks must carry the SAME string), a grow-only
height fit, and a redraw.

GPL boundary (FORMAT.md §6.13 M4): the target node -- ComfyUI-DaSiWa-Nodes'
``DaSiWa_LTX2LoraLoader`` -- is GPL-3.0. The bridge is NEW code driven by
that node's DATA format only; the source pins below hold that line: the
attribution comment must record the schema's provenance, and the module
must import nothing but the served ``scripts/app.js`` stub (never the clone).

Like ``test_pll_bridge_js.py``, the bridge's whole surface is drivable
under Node in a served-layout tmp dir: ``app`` is the stubbed served module
the bridge imports, and probe/write need only plain objects with a widgets
array, ``properties``, ``size``, and ``setDirtyCanvas`` -- so the mapping,
the ±5 clamp, the loud-lossy ``{flattened, clamped}`` reporting, and the
vs/as preservation all run for real rather than being source-pinned.

Skips cleanly when Node isn't installed; real-DaSiWa behavior (an actual
Advanced LoRA Loader receiving a Send) is for the rig, not here.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BRIDGE_JS = REPO_ROOT / "web" / "lora_library" / "dasiwa_bridge.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

# ----------------------------------------------------------- shared vocabulary

DASIWA_TYPE = "DaSiWa_LTX2LoraLoader"

#: §6.3-STYLE vocabulary, DaSiWa-specific text (FORMAT.md §6.13 M4: "drift
#: disables with a shape-drift message in the §6.3 vocabulary style").
MSG_NO_TARGET_SELECTED = "Pick a target DaSiWa Advanced LoRA Loader node above."
MSG_SHAPE_DRIFT = "DaSiWa Advanced LoRA Loader internals changed — send disabled (v-check)"

PROBE_JS = """
import * as m from './extensions/comfyui-epsnodes/lora_library/dasiwa_bridge.js'
import { app } from './scripts/app.js'

const TYPE = m.DASIWA_LOADER_TYPE

function fakeDasiwa(id, { properties = {}, widgets, size } = {}) {
  return {
    id,
    type: TYPE,
    title: `Stacker ${id}`,
    properties,
    widgets: widgets !== undefined ? widgets : [{ name: 'stack_data', value: '[]' }],
    size: size || [720, 100],
    dirtyCalls: 0,
    setDirtyCanvas() {
      this.dirtyCalls++
    }
  }
}

const out = {
  exports: {
    hasFindDasiwaNodes: typeof m.findDasiwaNodes === 'function',
    hasProbeDasiwa: typeof m.probeDasiwa === 'function',
    hasRowsForDasiwa: typeof m.rowsForDasiwa === 'function',
    hasWriteRowsToDasiwa: typeof m.writeRowsToDasiwa === 'function'
  },
  loaderType: TYPE
}

app.graph = { _nodes: [] }
out.probeNull = m.probeDasiwa(null)

const high = fakeDasiwa(7)
const low = fakeDasiwa(3)
app.graph = { _nodes: [high, low, { id: 5, type: 'SomeOtherNode' }] }
out.findOrder = m.findDasiwaNodes().map((node) => node.id)

out.probeWrongType = m.probeDasiwa({ id: 9, type: 'SomeOtherNode', widgets: [] })
out.probeNoWidget = m.probeDasiwa(fakeDasiwa(20, { widgets: [] }))
out.probeEmptyArray = m.probeDasiwa(fakeDasiwa(21))
out.probeEmptyString = m.probeDasiwa(
  fakeDasiwa(22, { widgets: [{ name: 'stack_data', value: '' }] })
)
out.probeAbsentValue = m.probeDasiwa(
  fakeDasiwa(23, { widgets: [{ name: 'stack_data', value: undefined }] })
)
out.probeNotJson = m.probeDasiwa(
  fakeDasiwa(24, { widgets: [{ name: 'stack_data', value: 'not json' }] })
)
out.probeJsonObject = m.probeDasiwa(
  fakeDasiwa(25, { widgets: [{ name: 'stack_data', value: '{"on":true}' }] })
)
out.probeTwoRows = m.probeDasiwa(
  fakeDasiwa(26, {
    properties: {
      stack_data:
        '[{"on":true,"lora":"a.st","str":1,"vs":1,"as":1},' +
        '{"on":false,"lora":"b.st","str":1,"vs":1,"as":1}]'
    }
  })
)
out.probeOneRow = m.probeDasiwa(
  fakeDasiwa(27, {
    properties: { stack_data: '[{"on":true,"lora":"a.st","str":1,"vs":1,"as":1}]' }
  })
)
out.probePropertyWinsOverWidget = m.probeDasiwa(
  fakeDasiwa(28, {
    properties: { stack_data: '[]' },
    widgets: [{ name: 'stack_data', value: 'garbage' }]
  })
)
out.probeWidgetGarbagePropertyAbsent = m.probeDasiwa(
  fakeDasiwa(29, { widgets: [{ name: 'stack_data', value: 'garbage' }] })
)

out.rowsFor = {
  basicOrder: m.rowsForDasiwa(
    [
      { file: 'a/x.st', on: false, strength: 0.5, strength_clip: null },
      { file: 'y.st', strength: 2 }
    ],
    []
  ),
  clampHigh: m.rowsForDasiwa([{ file: 'sub/big.st', on: true, strength: 7 }], []),
  clampLow: m.rowsForDasiwa([{ file: 'neg.st', on: true, strength: -6.5 }], []),
  clipEqual: m.rowsForDasiwa([{ file: 'x.st', on: true, strength: 0.5, strength_clip: 0.5 }], []),
  clipDiffers: m.rowsForDasiwa(
    [{ file: 'dir/x.st', on: true, strength: 0.5, strength_clip: 0.25 }],
    []
  ),
  clipDiffersAndClamps: m.rowsForDasiwa(
    [{ file: 'w.st', on: true, strength: 9, strength_clip: 1 }],
    []
  ),
  preservesVsAs: m.rowsForDasiwa(
    [
      { file: 'a/x.st', on: true, strength: 1.25 },
      { file: 'new.st', on: true, strength: 1 }
    ],
    [
      { on: false, lora: 'a/x.st', str: 2, vs: 0.7, as: 0.3 },
      { on: true, lora: 'other.st', str: 1, vs: 0.1, as: 0.2 }
    ]
  ),
  separatorInsensitiveMatch: m.rowsForDasiwa(
    [{ file: 'a/x.st', on: true, strength: 1 }],
    [{ on: true, lora: 'a\\\\x.st', str: 1, vs: 0.7, as: 0.3 }]
  ),
  badExistingVsAs: m.rowsForDasiwa(
    [{ file: 'x.st', on: true, strength: 1 }],
    [{ on: true, lora: 'x.st', str: 1, vs: 'junk', as: null }]
  )
}

const target = fakeDasiwa(40, {
  properties: {
    stack_data: '[{"on":true,"lora":"keep.st","str":2,"vs":0.7,"as":0.3}]',
    model_type: 'LTX-2.3',
    theme: 'c'
  },
  widgets: [{ name: 'stack_data', value: 'stale-widget-copy' }],
  size: [720, 100]
})
out.writeResult = m.writeRowsToDasiwa(target, [
  { file: 'keep.st', on: false, strength: 7, strength_clip: null },
  { file: 'new/added.st', on: true, strength: 0.5, strength_clip: 0.25 }
])
out.writeTarget = {
  property: target.properties.stack_data,
  widget: target.widgets[0].value,
  same: target.properties.stack_data === target.widgets[0].value,
  modelType: target.properties.model_type,
  theme: target.properties.theme,
  size: target.size,
  dirtyCalls: target.dirtyCalls
}

const tall = fakeDasiwa(41, { size: [720, 500] })
m.writeRowsToDasiwa(tall, [{ file: 'one.st', on: true, strength: 1 }])
out.writeTallSize = tall.size

const many = fakeDasiwa(42)
m.writeRowsToDasiwa(
  many,
  Array.from({ length: 10 }, (_, i) => ({ file: `l${i}.st`, on: true, strength: 1 }))
)
out.writeManySize = many.size

const widgetOnly = fakeDasiwa(43, {
  widgets: [{ name: 'stack_data', value: '[{"on":true,"lora":"w.st","str":1,"vs":0.4,"as":0.6}]' }]
})
out.writeWidgetOnly = {
  result: m.writeRowsToDasiwa(widgetOnly, [{ file: 'w.st', on: true, strength: 1 }]),
  rows: JSON.parse(widgetOnly.properties.stack_data)
}

const garbage = fakeDasiwa(44, { widgets: [{ name: 'stack_data', value: 'not json' }] })
out.writeGarbageTolerant = {
  result: m.writeRowsToDasiwa(garbage, [{ file: 'g.st', on: true, strength: 1 }]),
  rows: JSON.parse(garbage.properties.stack_data)
}

const bare = {
  id: 45,
  type: TYPE,
  widgets: [{ name: 'stack_data', value: '[]' }],
  size: [720, 100],
  dirtyCalls: 0,
  setDirtyCanvas() {
    this.dirtyCalls++
  }
}
m.writeRowsToDasiwa(bare, [{ file: 'b.st', on: true, strength: 1 }])
out.writeCreatesProperties = {
  property: bare.properties.stack_data,
  widget: bare.widgets[0].value
}

process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def bridge_api(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Runs the probe against the REAL dasiwa_bridge.js in a served-layout
    tmp dir (test_pll_bridge_js.py's fixture convention) and returns its
    JSON output."""
    layout = tmp_path_factory.mktemp("web_root")

    module_dir = layout / "extensions" / "comfyui-epsnodes" / "lora_library"
    module_dir.mkdir(parents=True)
    shutil.copyfile(BRIDGE_JS, module_dir / "dasiwa_bridge.js")
    # dasiwa_bridge.js imports only `../../../scripts/app.js` -- stub it
    # exactly as test_pll_bridge_js.py does; the probe mutates `app.graph`.
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
    """Raw text of dasiwa_bridge.js -- for the attribution/no-import/
    model_type pins the Node probe can't express."""
    return BRIDGE_JS.read_text(encoding="utf-8")


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


def test_dasiwa_bridge_js_parses() -> None:
    """`node --check` -- the file must at minimum be valid ES module syntax."""
    result = subprocess.run(
        [NODE, "--check", str(BRIDGE_JS)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


def test_module_exports_the_bridge_surface(bridge_api: dict) -> None:
    """picker.js's send-adapter registry consumes exactly these four plus
    the type constant -- pll_bridge.js's surface, mirrored."""
    assert bridge_api["exports"] == {
        "hasFindDasiwaNodes": True,
        "hasProbeDasiwa": True,
        "hasRowsForDasiwa": True,
        "hasWriteRowsToDasiwa": True,
    }


def test_loader_type_is_the_exact_dasiwa_spelling(bridge_api: dict) -> None:
    assert bridge_api["loaderType"] == DASIWA_TYPE


def test_find_dasiwa_nodes_sorted_ascending_and_type_filtered(bridge_api: dict) -> None:
    """Graph order was [7, 3, non-DaSiWa]; the candidate order is ascending
    id with the non-DaSiWa dropped (§6.13 M4's cross-family combo order)."""
    assert bridge_api["findOrder"] == [3, 7]


# ----------------------------------------------------------------- probeDasiwa


def test_probe_null_or_wrong_type_is_not_a_dasiwa_target(bridge_api: dict) -> None:
    for key in ("probeNull", "probeWrongType"):
        assert bridge_api[key] == {
            "ok": False,
            "code": "wrong-type",
            "message": MSG_NO_TARGET_SELECTED,
        }, key


def test_probe_missing_stack_widget_is_shape_drift(bridge_api: dict) -> None:
    assert bridge_api["probeNoWidget"] == {
        "ok": False,
        "code": "shape-drift",
        "message": MSG_SHAPE_DRIFT,
    }


def test_probe_ok_when_value_is_an_array_empty_or_absent(bridge_api: dict) -> None:
    """FORMAT.md §6.13 M4's exact gate: the existing value parses as a JSON
    array, or is empty/absent -- all three are a healthy target."""
    ready_zero = {"ok": True, "code": "ok", "message": "Ready — target has 0 rows.", "rowCount": 0}
    assert bridge_api["probeEmptyArray"] == ready_zero
    assert bridge_api["probeEmptyString"] == ready_zero
    assert bridge_api["probeAbsentValue"] == ready_zero


def test_probe_ok_reports_row_count_with_grammar(bridge_api: dict) -> None:
    assert bridge_api["probeTwoRows"] == {
        "ok": True,
        "code": "ok",
        "message": "Ready — target has 2 rows.",
        "rowCount": 2,
    }
    assert bridge_api["probeOneRow"]["message"] == "Ready — target has 1 row."


def test_probe_shape_drift_on_unparseable_or_non_array_value(bridge_api: dict) -> None:
    """A present value that is not JSON, or is JSON but not an array, is the
    drift signal -- the §6.3-style message, DaSiWa-specific text."""
    for key in ("probeNotJson", "probeJsonObject", "probeWidgetGarbagePropertyAbsent"):
        assert bridge_api[key] == {
            "ok": False,
            "code": "shape-drift",
            "message": MSG_SHAPE_DRIFT,
        }, key


def test_probe_reads_properties_before_the_widget(bridge_api: dict) -> None:
    """properties.stack_data is the node's live source of truth (its canvas
    UI mirrors properties -> widget) -- a healthy property wins over a
    garbage widget mirror."""
    assert bridge_api["probePropertyWinsOverWidget"]["ok"] is True


# ---------------------------------------------------------------- rowsForDasiwa


def test_rows_map_in_picker_order_with_schema_defaults(bridge_api: dict) -> None:
    """{file, on, strength} -> {on, lora, str, vs, as}: picker order kept,
    `on` preserved (false stays false), absent `on`/strength default
    true/1, new rows get vs/as 1.0, a null strength_clip never flattens."""
    assert bridge_api["rowsFor"]["basicOrder"] == {
        "rows": [
            {"on": False, "lora": "a/x.st", "str": 0.5, "vs": 1, "as": 1},
            {"on": True, "lora": "y.st", "str": 2, "vs": 1, "as": 1},
        ],
        "flattened": [],
        "clamped": [],
    }


def test_strength_clamps_to_dasiwa_own_plus_minus_five_and_is_named(bridge_api: dict) -> None:
    """±5 is DaSiWa's OWN range (not the picker's ±10); a strength that left
    it clamps AND lands in `clamped` by basename -- loud, never silent."""
    high = bridge_api["rowsFor"]["clampHigh"]
    assert high["rows"][0]["str"] == 5
    assert high["clamped"] == ["big.st"]
    assert high["flattened"] == []
    low = bridge_api["rowsFor"]["clampLow"]
    assert low["rows"][0]["str"] == -5
    assert low["clamped"] == ["neg.st"]


def test_differing_strength_clip_flattens_to_the_model_strength(bridge_api: dict) -> None:
    """DaSiWa has ONE strength: a finite strength_clip differing from
    strength sends the MODEL strength and names the row in `flattened`;
    an equal clip is lossless and stays quiet."""
    assert bridge_api["rowsFor"]["clipEqual"]["flattened"] == []
    differs = bridge_api["rowsFor"]["clipDiffers"]
    assert differs["rows"][0]["str"] == 0.5  # the model strength, not the clip
    assert differs["flattened"] == ["x.st"]


def test_a_row_can_be_both_flattened_and_clamped(bridge_api: dict) -> None:
    both = bridge_api["rowsFor"]["clipDiffersAndClamps"]
    assert both["rows"][0]["str"] == 5
    assert both["flattened"] == ["w.st"]
    assert both["clamped"] == ["w.st"]


def test_vs_as_preserved_on_raw_string_match_else_defaults(bridge_api: dict) -> None:
    """Re-sending preserves an existing row's vs/as multipliers when the
    same `lora` is already present (tuning done in DaSiWa's own UI must
    survive -- owner decision 2026-08-09); `on` and `str` still come from
    the PICKER row; new loras get 1.0/1.0."""
    got = bridge_api["rowsFor"]["preservesVsAs"]["rows"]
    assert got == [
        {"on": True, "lora": "a/x.st", "str": 1.25, "vs": 0.7, "as": 0.3},
        {"on": True, "lora": "new.st", "str": 1, "vs": 1, "as": 1},
    ]


def test_vs_as_match_is_separator_insensitive_but_writes_picker_spelling(
    bridge_api: dict,
) -> None:
    """Review 2026-08-09 (contract fix): DaSiWa's own UI stores folder_paths'
    NATIVE spellings -- backslashes on Windows -- while picker rows are
    forward-slash, so a raw-string compare silently reset vs/as tuning on
    exactly the machine it mattered most. Matching is §4
    separator-insensitive; the WRITTEN lora keeps the picker's spelling."""
    got = bridge_api["rowsFor"]["separatorInsensitiveMatch"]["rows"][0]
    assert got["vs"] == 0.7
    assert got["as"] == 0.3
    assert got["lora"] == "a/x.st"


def test_non_finite_existing_vs_as_degrade_to_defaults(bridge_api: dict) -> None:
    got = bridge_api["rowsFor"]["badExistingVsAs"]["rows"][0]
    assert got["vs"] == 1
    assert got["as"] == 1


# ------------------------------------------------------------ writeRowsToDasiwa


def test_write_sets_property_and_widget_to_the_same_json_string(bridge_api: dict) -> None:
    """BOTH sinks, ONE string: properties.stack_data (the node's live source
    of truth) and the stack_data widget (what serializes/executes) -- and
    the exact serialized shape, key order included, is DaSiWa's own
    {on, lora, str, vs, as}."""
    got = bridge_api["writeTarget"]
    assert got["same"] is True
    expected = (
        '[{"on":false,"lora":"keep.st","str":5,"vs":0.7,"as":0.3},'
        '{"on":true,"lora":"new/added.st","str":0.5,"vs":1,"as":1}]'
    )
    assert got["property"] == expected
    assert got["widget"] == expected


def test_write_reports_the_loud_lossy_lists_and_redraws(bridge_api: dict) -> None:
    """The write's return feeds picker.js's toast: keep.st clamped (7 -> 5),
    added.st flattened (clip 0.25 != strength 0.5) -- and the node was
    redrawn."""
    assert bridge_api["writeResult"] == {"flattened": ["added.st"], "clamped": ["keep.st"]}
    assert bridge_api["writeTarget"]["dirtyCalls"] >= 1


def test_write_leaves_model_type_and_other_properties_alone(bridge_api: dict) -> None:
    """model_type (Basic vs LTX-2.3) is the owner's own setting; the theme
    rides along as proof no sibling property is touched either."""
    assert bridge_api["writeTarget"]["modelType"] == "LTX-2.3"
    assert bridge_api["writeTarget"]["theme"] == "c"


def test_write_height_grows_to_their_row_math_but_never_shrinks(bridge_api: dict) -> None:
    """DaSiWa's own calcHeight(n) = 59 + n*28 + 2 (derived by reading
    ltx2_dynamic_ui.js): 2 rows from height 100 -> 117; 10 rows -> 341; a
    hand-grown 500 stays 500."""
    assert bridge_api["writeTarget"]["size"] == [720, 117]
    assert bridge_api["writeManySize"] == [720, 341]
    assert bridge_api["writeTallSize"] == [720, 500]


def test_write_preserves_vs_as_from_a_widget_only_stack(bridge_api: dict) -> None:
    """No properties.stack_data: the widget value is the tolerant fallback
    read, and its vs/as still survive the re-send."""
    got = bridge_api["writeWidgetOnly"]
    assert got["result"] == {"flattened": [], "clamped": []}
    assert got["rows"] == [{"on": True, "lora": "w.st", "str": 1, "vs": 0.4, "as": 0.6}]


def test_write_tolerates_a_garbage_existing_value(bridge_api: dict) -> None:
    """A torn/unparseable existing value degrades to "nothing to preserve"
    (probe already blocks drift at the Send gate) -- never a throw."""
    got = bridge_api["writeGarbageTolerant"]
    assert got["rows"] == [{"on": True, "lora": "g.st", "str": 1, "vs": 1, "as": 1}]


def test_write_creates_the_properties_object_when_missing(bridge_api: dict) -> None:
    got = bridge_api["writeCreatesProperties"]
    assert got["property"] == got["widget"]
    assert json.loads(got["property"]) == [{"on": True, "lora": "b.st", "str": 1, "vs": 1, "as": 1}]


# ------------------------------------------------- GPL boundary / provenance pins


def test_type_string_appears_verbatim_in_the_source(source: str) -> None:
    assert "export const DASIWA_LOADER_TYPE = 'DaSiWa_LTX2LoraLoader'" in source


def test_attribution_comment_records_gpl_data_only_provenance(source: str) -> None:
    """FORMAT.md §6.13 M4: "driven with DATA only, no code copied; an
    attribution comment records the schema's provenance"."""
    assert "ComfyUI-DaSiWa-Nodes" in source
    assert "DaSiWa Advanced LoRA Loader" in source
    assert "GPL-3.0" in source
    assert "no code was copied" in source


def test_no_import_of_the_clone_only_the_served_app_stub(source: str) -> None:
    """NEW code speaking the DATA format: the module's one import is the
    served scripts/app.js -- never the GPL clone (or anything else)."""
    assert re.findall(r"^import .*$", source, flags=re.MULTILINE) == [
        "import { app } from '../../../scripts/app.js'"
    ]
    assert "scratchpad" not in source


def test_model_type_appears_only_in_comments_never_writes(source: str) -> None:
    """The write leaves model_type alone -- every mention is commentary
    (the deliberately-untouched note), never a read-modify or assignment."""
    lines = [line for line in source.splitlines() if "model_type" in line]
    assert lines, "the deliberately-untouched model_type note is part of the contract"
    for line in lines:
        stripped = line.strip()
        assert stripped.startswith(("*", "//")), f"model_type outside a comment: {line!r}"


def test_write_targets_both_sinks_in_the_source(source: str) -> None:
    body = _function_body(source, "writeRowsToDasiwa(node, pickerRows)")
    assert "node.properties.stack_data = json" in body
    assert "widget.value = json" in body
    assert "node.setDirtyCanvas(true, true)" in body
    assert "Math.max(node.size[1], fitHeight)" in body


def test_row_math_constants_are_derived_and_cited(source: str) -> None:
    """The height constants must carry their provenance -- the exact numbers
    from reading (never copying) ltx2_dynamic_ui.js's layout block."""
    assert "const DASIWA_ROW_START = 59" in source
    assert "const DASIWA_ROW_H = 28" in source
    assert "const DASIWA_STR_MIN = -5" in source
    assert "const DASIWA_STR_MAX = 5" in source
    assert "ltx2_dynamic_ui.js" in source


def test_no_window_level_listeners(source: str) -> None:
    assert "window.addEventListener" not in source


# ---------------------------------------------------------------------------
# v0.68.1 perf/polish round (audit 2026-08-20)
# ---------------------------------------------------------------------------


def test_root_only_find_helper_is_marked_unused_by_the_picker(source: str) -> None:
    """Since v0.64.0 picker.js's findSendCandidates() walks the whole
    workflow itself; the registry's `find` key is gone (v0.68.1), so
    findDasiwaNodes is reachable from nothing but this file's own probe. It
    stays ONLY because the pins above (`hasFindDasiwaNodes`, `findOrder`)
    pin it -- remove together, alongside pll_bridge.js's findPllNodes so
    the two bridges keep mirroring each other's surface."""
    block = source.split("export function findDasiwaNodes()", 1)[0]
    assert "UNUSED BY THE PICKER, marked for removal" in block[-1200:]
    picker = (REPO_ROOT / "web" / "lora_library" / "picker.js").read_text(encoding="utf-8")
    assert "dasiwa.findDasiwaNodes" not in picker
