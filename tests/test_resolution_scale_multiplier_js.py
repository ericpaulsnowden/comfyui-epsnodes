"""Frontend tests for EPS Resolution's `scale_multiplier` (v0.101.0, owner
spec 2026-10-29, FORMAT.md §6.5), added to ``web/eps_image/resolution.js``
alongside the M1-M5 code the sibling ``tests/test_resolution_*_js.py`` files
already cover.

Same dual convention as those siblings (their own docstrings): the pure,
exported helpers -- ``scaleMultiplierDisabledState`` (THE greying decision
the spec asked to be importable), ``parseScaleMultiplier``,
``anyImageInputWired``, ``roundHalfUp``/``scaleSide`` (mirrors of the
backend's ``round_half_up``/``scale_side``, same case table as
``tests/test_resolution.py``'s ``TestScaleMultiplierPureHelpers``),
``formatScaleMultiplier`` and ``getScaleReadoutLine`` -- are driven
headlessly under Node via a served-layout probe; the LIVE-graph glue that
only runs inside ``attach()`` against a real litegraph node
(``applyScaleMultiplierState``, ``wireScaleMultiplier``, the pad's inert
class + pointerdown guard, the Save/Delete composition, the converge /
external-write / configure triggers) has no browser harness here and is
pinned via SOURCE-TEXT assertions, matching the siblings' identical
convention for that class of code. The real clicks, both renderers, are
for the rig.

On this line (v1, the 2026-10-05 round) the decision also names `rotate`
and the `custom…` ratio box, and the copy/rotate buttons are HTML buttons
in one row widget; ``tests/test_resolution_rows_js.py``'s ``TestScaleMode``
drives those through the real ``attach()`` against the fake litegraph node.
The port itself was rig-verified on main (classic + Nodes 2.0); the v1-only
greying of rotate / custom… is covered by tests only.

Skips cleanly when Node isn't installed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RESOLUTION_JS = REPO_ROOT / "web" / "eps_image" / "resolution.js"
# This line's resolution.js also imports the shared button-row module (the
# 2026-10-05 `Save | Delete` / `copy from image | rotate` rows) -- ES modules
# resolve every static import eagerly, so it must exist in the layout too.
BUTTON_ROW_JS = REPO_ROOT / "web" / "eps_image" / "button_row.js"
API_JS = REPO_ROOT / "web" / "lora_library" / "api.js"
VERSION_JS = REPO_ROOT / "web" / "lora_library" / "version.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

# --------------------------------------------------------------- case tables

#: (JS source for the raw value, expected parseScaleMultiplier()) -- the
#: SAME rule as nodes_resolution.py's coerce_scale_multiplier: a finite
#: positive number, else 0 ("off"). Raw JS so undefined/NaN are representable.
PARSE_CASES = [
    ("0", 0),
    ("0.0", 0),
    ("2", 2),
    ("1.5", 1.5),
    ("'1.5'", 1.5),
    ("-1", 0),
    ("undefined", 0),
    ("null", 0),
    ("'bogus'", 0),
    ("NaN", 0),
    ("Infinity", 0),
]

#: (multiplier, imageWired, expected scaleMultiplierDisabledState()) -- the
#: whole truth table of the owner's greying rules (spec item 4), plus this
#: line's two extra controls (`rotate`, the `custom…` box): both greyed
#: exactly when the scale mode is active, never otherwise.
OFF_NOTHING_WIRED = {
    "scaleActive": False, "sizeControls": False, "copyFromImage": True,
    "rotate": False, "customRatio": False,
}
OFF_WIRED = {
    "scaleActive": False, "sizeControls": False, "copyFromImage": False,
    "rotate": False, "customRatio": False,
}
ON = {
    "scaleActive": True, "sizeControls": True, "copyFromImage": True,
    "rotate": True, "customRatio": True,
}
DISABLED_STATE_CASES = [
    # multiplier off, nothing wired: only copy-from-image is disabled.
    (0, False, OFF_NOTHING_WIRED),
    # multiplier off, image wired: everything live -- today's behaviour.
    (0, True, OFF_WIRED),
    # multiplier on, nothing wired: the typed size still drives (fields
    # editable, rotate live), copy-from-image stays disabled (nothing to
    # copy from).
    (2, False, OFF_NOTHING_WIRED),
    # multiplier on, image wired: THE scale mode -- all of it greyed.
    (2, True, ON),
    (0.5, True, ON),
    # garbage multiplier reads as off.
    ("'x'", True, OFF_WIRED),
]

#: (live `node.inputs` array as JSON, expected anyImageInputWired()).
WIRED_CASES = [
    ([], False),
    ([{"name": "image", "link": None}], False),
    ([{"name": "image", "link": 7}], True),
    ([{"name": "image", "link": 0}], True),  # link id 0 is a real link
    ([{"name": "image", "link": None}, {"name": "image_2", "link": 3}], True),
    ([{"name": "image_8", "link": 3}], True),
    ([{"name": "image_9", "link": 3}], False),  # outside the 2..8 ceiling
    ([{"name": "mask", "link": 3}], False),
    ([None, {"name": "image_3", "link": 1}], True),
]

#: (value, expected roundHalfUp()) -- tests/test_resolution.py's table.
ROUND_CASES = [
    (2.5, 3),
    (3.5, 4),
    (2.4999, 2),
    (301.49999999999994, 302),
    (0.4, 0),
    (1501.5, 1502),
]

#: (size, multiplier, expected scaleSide()) -- the backend's table.
SCALE_SIDE_CASES = [
    (1920, 2.0, 3840),
    (1001, 1.5, 1502),
    (1001, 0.5, 501),
    (3, 0.1, 1),
    (1005, 0.3, 302),
    (0, 2.0, 0),
    (-1, 2.0, -1),
]

FORMAT_CASES = [(2, "2x"), (1.5, "1.5x"), (0.25, "0.25x"), (1, "1x"), (0.1, "0.1x")]

#: getScaleReadoutLine() inputs (JSON) and the expected line.
READOUT_CASES = [
    (
        "off_is_null",
        {"multiplier": 0, "imageWired": True,
         "summary": {"kind": "single", "width": 10, "height": 10},
         "rawW": 1024, "rawH": 1024, "multipleOf": 0},
        None,
    ),
    (
        "image_single_source",
        {"multiplier": 2, "imageWired": True,
         "summary": {"kind": "single", "width": 1920, "height": 1080},
         "rawW": 333, "rawH": 777, "multipleOf": 64},
        {"dims": "out 3840 x 2160", "tag": "(2x)", "aspect": "16:9", "mp": "8.3 MP"},
    ),
    (
        "image_single_source_rounds_half_up_and_ignores_multiple_of",
        {"multiplier": 1.5, "imageWired": True,
         "summary": {"kind": "single", "width": 1001, "height": 333},
         "rawW": 1, "rawH": 1, "multipleOf": 64},
        {"dims": "out 1502 x 500", "tag": "(1.5x)", "aspect": "751:250", "mp": "0.75 MP"},
    ),
    (
        "image_mixed_sources_says_so",
        {"multiplier": 2, "imageWired": True,
         "summary": {"kind": "mixed",
                     "sizes": [{"width": 1, "height": 1}, {"width": 2, "height": 2}]},
         "rawW": 1024, "rawH": 1024, "multipleOf": 0},
        {"dims": "out mixed sizes", "tag": "(2x each)", "aspect": None, "mp": None},
    ),
    (
        "image_wired_not_decoded_yet",
        {"multiplier": 2, "imageWired": True, "summary": {"kind": "none"},
         "rawW": 1024, "rawH": 1024, "multipleOf": 0},
        {"dims": "out ?", "tag": "(2x of input)", "aspect": None, "mp": None},
    ),
    (
        "no_image_is_the_owners_example",
        {"multiplier": 2, "imageWired": False, "summary": {"kind": "none"},
         "rawW": 1024, "rawH": 1024, "multipleOf": 0},
        {"dims": "2048 x 2048", "tag": "(2x of 1024 x 1024)", "aspect": None, "mp": "4.2 MP"},
    ),
    (
        "no_image_applies_multiple_of_like_the_backend_1472",
        {"multiplier": 1.5, "imageWired": False, "summary": {"kind": "none"},
         "rawW": 1000, "rawH": 1000, "multipleOf": 64},
        {"dims": "1472 x 1472", "tag": "(1.5x of 1000 x 1000)", "aspect": None, "mp": "2.2 MP"},
    ),
    (
        "no_image_zero_axis_stays_auto",
        {"multiplier": 2, "imageWired": False, "summary": {"kind": "none"},
         "rawW": 0, "rawH": 512, "multipleOf": 0},
        {"dims": "auto x 1024", "tag": "(2x of auto x 512)", "aspect": None, "mp": None},
    ),
    (
        "no_image_ignores_a_stale_summary",
        {"multiplier": 2, "imageWired": False,
         "summary": {"kind": "single", "width": 9, "height": 9},
         "rawW": 100, "rawH": 50, "multipleOf": 0},
        {"dims": "200 x 100", "tag": "(2x of 100 x 50)", "aspect": None, "mp": "0.02 MP"},
    ),
]

PROBE_JS = """
import * as m from './extensions/comfyui-epsnodes/eps_image/resolution.js'

const out = {
  exports: {
    hasInit: typeof m.init === 'function',
    hasAttach: typeof m.attach === 'function',
    hasDisabledState: typeof m.scaleMultiplierDisabledState === 'function',
    hasParse: typeof m.parseScaleMultiplier === 'function',
    hasAnyImageInputWired: typeof m.anyImageInputWired === 'function',
    hasRoundHalfUp: typeof m.roundHalfUp === 'function',
    hasScaleSide: typeof m.scaleSide === 'function',
    hasFormat: typeof m.formatScaleMultiplier === 'function',
    hasReadout: typeof m.getScaleReadoutLine === 'function'
  },
  parse: [%(parse_inputs)s].map((v) => m.parseScaleMultiplier(v)),
  disabledState: [%(state_inputs)s].map(
    ([mult, wired]) => m.scaleMultiplierDisabledState(mult, wired)
  ),
  wired: %(wired_inputs)s.map((inputs) => m.anyImageInputWired(inputs)),
  wiredNonArray: [
    m.anyImageInputWired(undefined), m.anyImageInputWired(null), m.anyImageInputWired({})
  ],
  round: %(round_inputs)s.map((v) => m.roundHalfUp(v)),
  scaleSide: %(scale_side_inputs)s.map(([s, mult]) => m.scaleSide(s, mult)),
  format: %(format_inputs)s.map((v) => m.formatScaleMultiplier(v)),
  readout: %(readout_inputs)s.map((args) => m.getScaleReadoutLine(args))
}

process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def scale_api(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Runs the probe against the REAL resolution.js in a served-layout tmp
    dir (mirrors test_resolution_ratio_js.py's identical fixture shape)."""
    layout = tmp_path_factory.mktemp("web_root")

    module_dir = layout / "extensions" / "comfyui-epsnodes" / "eps_image"
    module_dir.mkdir(parents=True)
    shutil.copyfile(RESOLUTION_JS, module_dir / "resolution.js")
    shutil.copyfile(BUTTON_ROW_JS, module_dir / "button_row.js")

    lora_dir = layout / "extensions" / "comfyui-epsnodes" / "lora_library"
    lora_dir.mkdir(parents=True)
    shutil.copyfile(API_JS, lora_dir / "api.js")
    shutil.copyfile(VERSION_JS, lora_dir / "version.js")

    scripts = layout / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "app.js").write_text("export const app = {}\n", encoding="utf-8")
    (scripts / "api.js").write_text("export const api = { fetchApi: () => {} }\n", encoding="utf-8")

    probe = layout / "probe.mjs"
    probe.write_text(
        PROBE_JS
        % {
            "parse_inputs": ", ".join(js for js, _ in PARSE_CASES),
            "state_inputs": ", ".join(
                f"[{mult}, {json.dumps(wired)}]" for mult, wired, _ in DISABLED_STATE_CASES
            ),
            "wired_inputs": json.dumps([inputs for inputs, _ in WIRED_CASES]),
            "round_inputs": json.dumps([v for v, _ in ROUND_CASES]),
            "scale_side_inputs": json.dumps([[s, mult] for s, mult, _ in SCALE_SIDE_CASES]),
            "format_inputs": json.dumps([v for v, _ in FORMAT_CASES]),
            "readout_inputs": json.dumps([args for _, args, _ in READOUT_CASES]),
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
    return RESOLUTION_JS.read_text(encoding="utf-8")


def _function_body(source_text: str, signature: str) -> str:
    """Body of a top-level ``function <signature> {`` up to its column-0
    closing brace -- the sibling JS test files' identical helper."""
    start_match = re.search(re.escape(f"function {signature} {{") + r"\n", source_text)
    assert start_match, f"function {signature} {{ not found"
    start = start_match.end()
    end_match = re.search(r"\n\}\n", source_text[start:])
    assert end_match, f"end of {signature} not found"
    return source_text[start : start + end_match.start()]


# --------------------------------------------------------------- exports


def test_resolution_js_still_parses() -> None:
    result = subprocess.run(
        ["node", "--check", "--input-type=module"],
        input=RESOLUTION_JS.read_text(encoding="utf-8"),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_scale_helpers_are_exported_alongside_the_entry_points(scale_api: dict) -> None:
    assert all(scale_api["exports"].values()), scale_api["exports"]


# ------------------------------------------------------------ pure helpers


def test_parse_scale_multiplier_matches_the_backend_rule(scale_api: dict) -> None:
    for (js, expected), actual in zip(PARSE_CASES, scale_api["parse"], strict=True):
        assert actual == expected, f"parseScaleMultiplier({js}) -> {actual!r}"


def test_disabled_state_truth_table(scale_api: dict) -> None:
    """THE decision, spec item 4: greyed iff multiplier > 0 AND an image is
    wired; copy-from-image additionally disabled whenever nothing is wired."""
    for (mult, wired, expected), actual in zip(
        DISABLED_STATE_CASES, scale_api["disabledState"], strict=True
    ):
        assert actual == expected, f"({mult}, {wired}) -> {actual!r}, wanted {expected!r}"


def test_any_image_input_wired_cases(scale_api: dict) -> None:
    for (inputs, expected), actual in zip(WIRED_CASES, scale_api["wired"], strict=True):
        assert actual == expected, f"{inputs!r} -> {actual!r}"
    assert scale_api["wiredNonArray"] == [False, False, False]


def test_round_half_up_mirrors_the_backend(scale_api: dict) -> None:
    for (value, expected), actual in zip(ROUND_CASES, scale_api["round"], strict=True):
        assert actual == expected, f"roundHalfUp({value}) -> {actual!r}"


def test_scale_side_mirrors_the_backend(scale_api: dict) -> None:
    for (size, mult, expected), actual in zip(
        SCALE_SIDE_CASES, scale_api["scaleSide"], strict=True
    ):
        assert actual == expected, f"scaleSide({size}, {mult}) -> {actual!r}"


def test_format_scale_multiplier(scale_api: dict) -> None:
    for (value, expected), actual in zip(FORMAT_CASES, scale_api["format"], strict=True):
        assert actual == expected, f"formatScaleMultiplier({value}) -> {actual!r}"


def test_scale_readout_line_cases(scale_api: dict) -> None:
    for (name, _args, expected), actual in zip(
        READOUT_CASES, scale_api["readout"], strict=True
    ):
        assert actual == expected, f"{name}: {actual!r}, wanted {expected!r}"


# ------------------------------------------------------- live-graph wiring pins


class TestGreyingWiresTheRightControls:
    def test_disabled_widget_list_is_exactly_the_backends_ignored_five(self, source: str) -> None:
        assert (
            "const SCALE_DISABLED_WIDGET_NAMES = "
            "['width', 'height', 'multiple_of', 'ratio', 'resize_method']"
        ) in source
        # interpolation stays live -- never in the list
        declaration = source.split("const SCALE_DISABLED_WIDGET_NAMES = ", 1)[1].split("\n", 1)[0]
        assert "'interpolation'" not in declaration

    def test_apply_touches_only_disabled_never_values_or_callbacks(self, source: str) -> None:
        body = _function_body(source, "applyScaleMultiplierState(node)")
        assert "setWidgetDisabled(" in body
        assert "setWidgetValue(" not in body
        assert ".callback" not in body
        assert "commitSelection(" not in body
        assert "conformNodeSizeToRatio(" not in body
        assert "resnapCurrentSizeToMultipleOf(" not in body
        setter = _function_body(source, "setWidgetDisabled(widget, disabled)")
        assert "widget.disabled = disabled" in setter
        assert ".options" not in setter  # the raw options object is not what Vue watches

    def test_apply_covers_the_preset_cluster_copy_button_and_pad(self, source: str) -> None:
        body = _function_body(source, "applyScaleMultiplierState(node)")
        assert "setWidgetDisabled(presets.combo, state.sizeControls)" in body
        assert "setWidgetDisabled(presets.saveBtn, state.sizeControls)" in body
        assert "updateDeleteEnabled(node)" in body
        # this line's `copy from image | rotate` ROW (HTML buttons): the two
        # have different gates -- copy whenever nothing is wired, rotate only
        # in scale mode.
        assert "setWidgetDisabled(copyRow.buttons.copy, state.copyFromImage)" in body
        assert "setWidgetDisabled(copyRow.buttons.rotate, state.rotate)" in body
        assert "grid.canvas.classList.toggle(GRID_INERT_CLASS, state.sizeControls)" in body
        assert "grid.scaleInert = state.sizeControls" in body
        assert "node.setDirtyCanvas?.(true, true)" in body  # Vue + canvas repaint request

    def test_save_delete_in_flight_state_is_respected(self, source: str) -> None:
        body = _function_body(source, "applyScaleMultiplierState(node)")
        assert "if (!presets.presetActionPending) {" in body

    def test_delete_and_save_gates_compose_with_the_scale_mode(self, source: str) -> None:
        delete = _function_body(source, "updateDeleteEnabled(node)")
        assert "state.selection.length !== 1 || isScaleModeActive(node)" in delete
        end = _function_body(source, "endPresetAction(node, state, activeBtn, originalLabel)")
        assert "state.saveBtn.disabled = isScaleModeActive(node)" in end

    def test_copy_row_handle_is_stashed_where_apply_finds_it(self, source: str) -> None:
        copy = _function_body(source, "attachCopyRotateRow(node)")
        assert "node._epsCopyRow = row" in copy


class TestV1RotateAndCustomRatio:
    """This line's two extra controls (2026-10-05 round) under the scale
    mode: the row's rotate `<button>` is disabled, AND `rotateSize` /
    `openCustomRatioBox` refuse even a programmatic call, so neither can
    touch a value the run ignores. Their live behaviour (real clicks on the
    real `<button>`s through `attach()`) is in
    tests/test_resolution_rows_js.py's `TestScaleMode`; these are the
    source pins. Tests only -- this port was not rig-verified on this line."""

    def test_rotate_refuses_before_it_reads_a_widget(self, source: str) -> None:
        body = _function_body(source, "rotateSize(node)")
        assert "if (currentScaleState(node).rotate) return" in body
        assert body.index("currentScaleState(node).rotate) return") < body.index("writeSize(")
        assert body.index("currentScaleState(node).rotate) return") < body.index("toast(")

    def test_custom_box_puts_the_widget_back_then_refuses_to_open(self, source: str) -> None:
        body = _function_body(source, "openCustomRatioBox(node, ratioWidget, event)")
        assert "if (currentScaleState(node).customRatio) return" in body
        # the widget is put back BEFORE the refusal (the command never stays)
        assert body.index("ratioWidget.value = previous") < body.index(
            "currentScaleState(node).customRatio) return"
        )
        assert body.index("currentScaleState(node).customRatio) return") < body.index("promptText(")

    def test_the_ratio_combo_itself_is_in_the_disabled_set(self, source: str) -> None:
        declaration = source.split("const SCALE_DISABLED_WIDGET_NAMES = ", 1)[1].split("\n", 1)[0]
        assert "'ratio'" in declaration


class TestPadIsInert:
    def test_css_class_dims_and_blocks_pointer_events(self, source: str) -> None:
        rule = source.split(".eps-res-grid-canvas.eps-res-grid-inert {", 1)[1].split("}", 1)[0]
        assert "opacity: 0.5" in rule
        assert "pointer-events: none" in rule
        assert "const GRID_INERT_CLASS = 'eps-res-grid-inert'" in source

    def test_pointerdown_guard_is_belt_and_suspenders(self, source: str) -> None:
        drag = _function_body(source, "attachGridDrag(node, canvasEl)")
        assert "if (node._epsGrid?.scaleInert) return" in drag
        # the guard sits inside the pointerdown handler, BEFORE its own
        # applyFromEvent write (the LAST call in the body -- onMove's own
        # call comes earlier in the source and is unreachable without a
        # pointerdown having armed `drag` first)
        assert drag.index("scaleInert) return") < drag.rindex("applyFromEvent(event)")
        assert drag.index("addEventListener('pointerdown'") < drag.index("scaleInert) return")

    def test_grid_state_seeds_the_flag_false(self, source: str) -> None:
        assert "scaleInert: false" in _function_body(source, "attachSizeGrid(node)")

    def test_pad_title_swaps_and_restores(self, source: str) -> None:
        body = _function_body(source, "applyScaleMultiplierState(node)")
        assert "state.sizeControls ? GRID_INERT_TITLE : GRID_TITLE" in body
        assert "canvasEl.title = GRID_TITLE" in _function_body(source, "attachSizeGrid(node)")


class TestTriggers:
    """Spec: live on connect/disconnect of any image input, a multiplier
    edit, workflow load, tab switch (nodes rebuilt -> attach + configure),
    and a Universal/State Controller apply."""

    def test_multiplier_callback_is_chained_not_replaced(self, source: str) -> None:
        body = _function_body(source, "wireScaleMultiplier(node)")
        assert "const originalCallback = widget.callback" in body
        assert "originalCallback?.apply(this, args)" in body
        # callback, connections, configure, initial
        assert body.count("applyScaleMultiplierState(") >= 4

    def test_connections_and_configure_are_chained_never_replaced(self, source: str) -> None:
        body = _function_body(source, "wireScaleMultiplier(node)")
        assert "const originalOnConnectionsChange = node.onConnectionsChange" in body
        assert "originalOnConnectionsChange?.apply(this, args)" in body
        assert "const originalOnConfigure = node.onConfigure" in body
        assert "originalOnConfigure?.call(this, info)" in body

    def test_deferred_converge_reapplies_after_the_inputs_settle(self, source: str) -> None:
        body = _function_body(source, "scheduleImageConverge(node)")
        assert body.index("revealExtraOutputs(node)") < body.index(
            "applyScaleMultiplierState(node)"
        )

    def test_external_write_subscription_reapplies(self, source: str) -> None:
        body = _function_body(source, "installExternalWriteSubscription()")
        assert "if (entry?.node?._epsScale) applyScaleMultiplierState(entry.node)" in body
        assert "node._epsScale = true" in _function_body(source, "wireScaleMultiplier(node)")

    def test_wired_last_in_attach_after_every_widget_exists(self, source: str) -> None:
        attach = _function_body(source, "attach(node)")
        assert attach.index("attachCopyRotateRow(node)") < attach.index("wireScaleMultiplier(node)")
        assert attach.index("attachPresetsUi(node)") < attach.index("wireScaleMultiplier(node)")
        assert attach.index("attachSizeGrid(node)") < attach.index("wireScaleMultiplier(node)")

    def test_multiplier_is_not_a_preset_field(self, source: str) -> None:
        """Editing the multiplier must not un-select a preset (spec: no side
        effects) -- it is not among the five fields the v0.67.1 wrap clears on."""
        assert (
            "const PRESET_FIELD_NAMES = "
            "['width', 'height', 'resize_method', 'interpolation', 'multiple_of']"
        ) in source


class TestReadout:
    def test_draw_grid_uses_the_scale_line_only_when_present(self, source: str) -> None:
        body = _function_body(source, "drawGrid(node, ctx, cssW)")
        assert "const scaleLine = currentScaleReadoutLine(node)" in body
        assert "const lines = scaleLine || getReadoutLines(disp)" in body
        # the pre-v0.101.0 MP draw stays unconditional when there is no scale line
        assert "!scaleLine ||" in body

    def test_live_readout_shares_the_incoming_size_walk(self, source: str) -> None:
        body = _function_body(source, "currentScaleReadoutLine(node)")
        assert "resolveIncomingImageSummary(node)" in body
        assert "anyImageInputWired(node.inputs)" in body


class TestRendererVerificationTrail:
    """The section header must carry the 1.52.7 citations for BOTH renderers
    (the research contract flags getter-defined `disabled` as unobserved in
    Vue and raw `options` mutation as non-reactive -- the code must use the
    setter, and the header must say why)."""

    def test_header_cites_both_renderers(self, source: str) -> None:
        section = source.split("v0.101.0: scale multiplier\n", 1)[1]
        assert "updateComputedDisabled" in section
        assert "getWidgetOnPos" in section
        assert "widgetValueStore" in section
        assert "useProcessedWidgets" in section
        assert "WidgetDOM.vue" in section
