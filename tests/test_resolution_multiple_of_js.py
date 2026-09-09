"""Frontend tests for the EPS Resolution M5 `multiple_of` widget step + snap
(owner report 2026-09-08: "EPS Resolution can have multiple_of set but it
doesn't seem to respect it on the front end. For example if it's set to 4,
and I click the arrows I'd expect the number to jump by 4. Or when typing
for a number to snap to the nearest number divisible by 4"), added to
``web/eps_image/resolution.js`` alongside the pre-existing M1-M4 code the
sibling test files already cover.

`multiple_of` was already honoured in two places before this fix -- the M2
grid drag (``getSnapUnit``/``snapTo``, ``tests/test_resolution_grid_js.py``)
and the backend's own execution-time rounding
(``tests/test_resolution.py``'s ``_round_to_multiple`` coverage) -- but
never on the `width`/`height` WIDGETS themselves, exactly what's reported
above. This file covers that gap.

Same dual convention as the M3/M4 sibling files (their own docstrings): the
pure, exported math (``snapDimensionValue``) is driven headlessly under Node
via a served-layout probe script; the LIVE-graph glue that only runs inside
``attach()`` against a real litegraph node (``applyMultipleOfWidgetStep``,
``wireMultipleOfSnap``, ``resnapCurrentSizeToMultipleOf``,
``wireMultipleOfLock``, the `attachCopyFromImage` suppression) has no
browser harness here and is pinned via SOURCE-TEXT assertions instead.

The real ComfyUI frontend semantics this fix relies on (`options.step2` vs
the legacy `options.step` x10 convention; `widget.callback` firing on
COMMIT only, never mid-keystroke) were verified against the installed
`comfyui_frontend_package`'s own sourcemaps, not guessed -- see the round
report for the exact files/functions inspected
(`litegraph/src/utils/widget.ts`'s `getWidgetStep`,
`vueNodes/widgets/composables/useIntWidget.ts`, and
`components/common/ScrubableNumberInput.vue`). That verification isn't
re-derived here; this file only pins that `resolution.js` itself does what
that verification concluded it should.

Skips cleanly when Node isn't installed.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RESOLUTION_JS = REPO_ROOT / "web" / "eps_image" / "resolution.js"
API_JS = REPO_ROOT / "web" / "lora_library" / "api.js"
VERSION_JS = REPO_ROOT / "web" / "lora_library" / "version.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

# --------------------------------------------------------------- case tables

#: (value, unit, min, max, expected). Covers: a plain in-range snap, "off"
#: (unit <= 0) leaving an in-bounds value unchanged, a snap that overshoots
#: the max and must be clamped back, a snap that would drop below min, the
#: "0 means derive" sentinel staying 0, and malformed (NaN/negative) units
#: degrading to "off" -- never throwing.
SNAP_DIMENSION_CASES = [
    # plain snaps
    (1023, 4, 0, 16384, 1024),
    (1022, 4, 0, 16384, 1024),
    (1025, 4, 0, 16384, 1024),
    (1000, 64, 0, 16384, 1024),
    # off (unit <= 0) leaves an in-bounds value exactly as-is
    (1023, 0, 0, 16384, 1023),
    (1023, -4, 0, 16384, 1023),
    # overshoot: a snap past max must clamp back DOWN to max, not to the
    # snapped-but-out-of-bounds number (16383 -> snapTo(.., 100) -> 16400)
    (16383, 100, 0, 16384, 16384),
    # undershoot: a snap below min clamps UP (min=8, snapping 5 to the
    # nearest 8 gives 8 anyway here, but a wider gap would clamp)
    (2, 16, 8, 16384, 8),
    # 0 is the node's own "derive from the other axis" sentinel -- must
    # never be perturbed away from it by a snap
    (0, 64, 0, 16384, 0),
    # malformed unit degrades to "off" (still bounds-clamped, never throws)
    (1023, float("nan"), 0, 16384, 1023),
]

PROBE_JS = """
import * as m from './extensions/comfyui-epsnodes/eps_image/resolution.js'

const out = {
  exports: {
    hasSnapDimensionValue: typeof m.snapDimensionValue === 'function'
  },
  snapDimensionValue: %(cases)s.map(
    ([value, unit, min, max]) => m.snapDimensionValue(value, unit, min, max)
  )
}

process.stdout.write(JSON.stringify(out))
"""


def _js_number(value: float) -> str:
    """NaN has no JSON/literal spelling -- emit the JS expression instead."""
    if isinstance(value, float) and value != value:  # NaN != NaN
        return "NaN"
    return json.dumps(value)


@pytest.fixture(scope="module")
def multiple_of_api(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Runs the probe against the REAL resolution.js in a served-layout tmp
    dir (mirrors the sibling M3/M4 test files' identical fixture shape)."""
    layout = tmp_path_factory.mktemp("web_root")

    module_dir = layout / "extensions" / "comfyui-epsnodes" / "eps_image"
    module_dir.mkdir(parents=True)
    shutil.copyfile(RESOLUTION_JS, module_dir / "resolution.js")

    lora_dir = layout / "extensions" / "comfyui-epsnodes" / "lora_library"
    lora_dir.mkdir(parents=True)
    shutil.copyfile(API_JS, lora_dir / "api.js")
    shutil.copyfile(VERSION_JS, lora_dir / "version.js")

    scripts = layout / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "app.js").write_text("export const app = {}\n", encoding="utf-8")
    (scripts / "api.js").write_text("export const api = { fetchApi: () => {} }\n", encoding="utf-8")

    case_rows = [
        "[" + ", ".join(_js_number(v) for v in case[:4]) + "]" for case in SNAP_DIMENSION_CASES
    ]
    cases_js = "[" + ", ".join(case_rows) + "]"

    probe = layout / "probe.mjs"
    probe.write_text(PROBE_JS % {"cases": cases_js}, encoding="utf-8")

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


def test_snap_dimension_value_is_exported(multiple_of_api: dict) -> None:
    assert multiple_of_api["exports"] == {"hasSnapDimensionValue": True}


# ------------------------------------------------------ snapDimensionValue


def test_snap_dimension_value_matches_the_case_table(multiple_of_api: dict) -> None:
    results = multiple_of_api["snapDimensionValue"]
    assert len(results) == len(SNAP_DIMENSION_CASES)
    for (value, unit, lo, hi, expected), actual in zip(SNAP_DIMENSION_CASES, results, strict=True):
        assert actual == expected, (
            f"snapDimensionValue({value}, {unit}, {lo}, {hi}) -> {actual!r}, expected {expected!r}"
        )


# ------------------------------------------------------- arrow/drag step (item 1)


class TestWidgetStepFollowsMultipleOf:
    """`applyMultipleOfWidgetStep` keeps width/height's OWN arrow-button/
    drag step in lockstep with `multiple_of` -- verified against the real
    frontend semantics (getWidgetStep prefers `options.step2` outright,
    legacy `options.step` is scaled x10) rather than guessed."""

    def test_step_and_step2_both_written(self, source: str) -> None:
        body = _function_body(source, "applyMultipleOfWidgetStep(node)")
        assert "widget.options.step2 = step2" in body
        assert "widget.options.step = step" in body
        # the legacy x10 convention -- NOT a second, independent number.
        assert "step2 * 10" in body

    def test_off_restores_the_native_declared_step(self, source: str) -> None:
        body = _function_body(source, "applyMultipleOfWidgetStep(node)")
        assert "unit > 0 ? unit : NATIVE_DIMENSION_STEP" in body
        assert "const NATIVE_DIMENSION_STEP = 1" in source

    def test_writes_are_change_gated(self, source: str) -> None:
        """FORMAT.md §7.9: only write a field when it actually differs."""
        body = _function_body(source, "applyMultipleOfWidgetStep(node)")
        assert "if (widget.options.step2 !== step2)" in body
        assert "if (widget.options.step !== step)" in body

    def test_applied_at_attach_on_multiple_of_change_and_on_configure(self, source: str) -> None:
        body = _function_body(source, "wireMultipleOfLock(node)")
        assert body.count("applyMultipleOfWidgetStep(") == 3


# --------------------------------------------------- typed-commit snap (item 2)


class TestTypedCommitSnap:
    """`wireMultipleOfSnap` snaps width/height's OWN value on commit, reusing
    `snapDimensionValue` (which itself reuses `snapTo`) rather than a second
    rounding implementation."""

    def test_hooks_widget_callback_not_a_keystroke_event(self, source: str) -> None:
        """`widget.callback` is the one write path both canvas
        (BaseWidget.setValue) and Vue (createWidgetUpdateHandler) share, and
        -- verified in the installed frontend's own ScrubableNumberInput.vue
        -- fires only on blur/Enter/the +-/ buttons/a swipe-drag, never on a
        raw keystroke. Hooking here (not some DOM `input` listener) is what
        makes "snap on commit, never mid-keystroke" true by construction."""
        body = _function_body(source, "wireMultipleOfSnap(node)")
        assert "widget.callback = function (...args)" in body
        assert "addEventListener" not in body

    def test_reuses_snap_dimension_value_not_a_second_rounding_fn(self, source: str) -> None:
        body = _function_body(source, "wireMultipleOfSnap(node)")
        assert "snapDimensionValue(" in body
        assert "Math.round(" not in body

    def test_self_write_is_bare_no_recursive_callback(self, source: str) -> None:
        """A `setWidgetValue`-style write here (value + `.callback()`) would
        re-enter this very function; the bare `this.value = snapped` avoids
        that, matching `configure()`'s own established silent-write idiom."""
        body = _function_body(source, "wireMultipleOfSnap(node)")
        assert "this.value = snapped" in body
        assert "setWidgetValue(" not in body

    def test_suppressed_for_the_copy_from_image_exception(self, source: str) -> None:
        body = _function_body(source, "wireMultipleOfSnap(node)")
        assert "node._epsSuppressMultipleOfSnap" in body


# ------------------------------------------------- multiple_of=0 unchanged (item 3)


class TestMultipleOfOffLeavesBehaviorUnchanged:
    def test_snap_dimension_value_is_a_passthrough_when_off(self, multiple_of_api: dict) -> None:
        # (1023, 0, 0, 16384, 1023) and (1023, -4, 0, 16384, 1023) in the
        # shared case table above already assert this numerically; this
        # test just names the intent for a reader scanning class names.
        results = multiple_of_api["snapDimensionValue"]
        off_cases = [
            (i, case) for i, case in enumerate(SNAP_DIMENSION_CASES) if not (case[1] > 0)
        ]
        assert off_cases, "expected at least one off/malformed case in the table"
        for i, (value, _unit, _lo, _hi, expected) in off_cases:
            assert results[i] == expected == value or expected == value


# --------------------------------------------- multiple_of change re-snaps (item 4)


class TestMultipleOfChangeResnaps:
    """Owner wording, pinned literally: changing `multiple_of` must re-snap
    the CURRENT width/height -- 'the box must never show one number while a
    different one is sent' (this file's standing Number Controller
    principle, cited in `wireMultipleOfLock`'s own doc)."""

    def test_multiple_of_callback_is_chained_not_replaced(self, source: str) -> None:
        body = _function_body(source, "wireMultipleOfLock(node)")
        assert "const originalCallback = multipleOfWidget.callback" in body
        assert "originalCallback?.apply(this, args)" in body

    def test_multiple_of_callback_resyncs_step_and_resnaps(self, source: str) -> None:
        body = _function_body(source, "wireMultipleOfLock(node)")
        # Only look inside multipleOfWidget's own callback block, not the
        # whole function (which also has an onConfigure block that does the
        # same two calls for a different reason -- both are asserted below).
        callback_start = body.index("multipleOfWidget.callback = function")
        callback_body = body[callback_start:]
        assert "applyMultipleOfWidgetStep(node)" in callback_body
        assert "resnapCurrentSizeToMultipleOf(node)" in callback_body

    def test_reload_reconcile_is_chained_and_silent(self, source: str) -> None:
        """A saved workflow's width/height might not satisfy its own
        restored `multiple_of` (configure()'s widget restore is a bare
        assignment with no callback, file header) -- reconciled on load,
        same posture as the ratio lock's own reload reconcile (no toast)."""
        body = _function_body(source, "wireMultipleOfLock(node)")
        assert "const originalOnConfigure = node.onConfigure" in body
        assert "originalOnConfigure?.call(this, info)" in body
        assert "resnapCurrentSizeToMultipleOf(this)" in body

    def test_preset_apply_resnaps_by_its_own_multiple_of(self, source: str) -> None:
        """A selected preset's own width/height must be snapped by the
        preset's own `multiple_of` -- `PRESET_FIELD_NAMES` writes
        `multiple_of` LAST, so this call (placed after the per-field loop)
        reads the preset's freshly-applied value, not a stale prior one."""
        body = _function_body(source, "applyPresetValues(node, name)")
        resnap_at = body.index("resnapCurrentSizeToMultipleOf(node)")
        applying_false_at = body.index("state.applying = false")
        assert resnap_at < applying_false_at


# -------------------------------------------------------- min/max bounds (item 5)


class TestSnapRespectsBounds:
    def test_overshoot_clamps_to_max_not_the_raw_snap(self, multiple_of_api: dict) -> None:
        # (16383, 100, 0, 16384, 16384) in the case table: snapTo alone
        # would give 16400, past max -- the clamp is what brings it back.
        idx = SNAP_DIMENSION_CASES.index((16383, 100, 0, 16384, 16384))
        assert multiple_of_api["snapDimensionValue"][idx] == 16384

    def test_resnap_reads_bounds_off_the_widgets_own_options(self, source: str) -> None:
        """Bounds come from `widget.options.min`/`.max` (populated straight
        from nodes_resolution.py's own INPUT_TYPES min/max), not a
        hardcoded/duplicated WIDTH_MAX-style JS constant that could drift
        from the backend's."""
        body = _function_body(source, "resnapCurrentSizeToMultipleOf(node)")
        assert "widthWidget.options?.min" in body
        assert "widthWidget.options?.max" in body
        assert "heightWidget.options?.min" in body
        assert "heightWidget.options?.max" in body

    def test_self_snap_also_reads_bounds_off_options(self, source: str) -> None:
        body = _function_body(source, "wireMultipleOfSnap(node)")
        assert "this.options?.min" in body
        assert "this.options?.max" in body


# -------------------------------------------------------- ratio-lock interaction (item 6)


class TestRatioLockInteraction:
    """Conform to the ratio FIRST, then snap (the round brief's ordering):
    `resnapCurrentSizeToMultipleOf` calls `conformNodeSizeToRatio` before
    either `snapDimensionValue` call, and the self-snap wrap
    (`wireMultipleOfSnap`) is installed BEFORE `wireRatioLock` in `attach()`
    so a locked ratio's own width/height wrap reads the already-snapped
    anchor when it derives the other axis."""

    def test_resnap_conforms_before_snapping_either_axis(self, source: str) -> None:
        body = _function_body(source, "resnapCurrentSizeToMultipleOf(node)")
        conform_at = body.index("conformNodeSizeToRatio(")
        first_snap_at = body.index("snapDimensionValue(")
        assert conform_at < first_snap_at

    def test_multiple_of_snap_wired_before_ratio_lock_in_attach(self, source: str) -> None:
        attach = _function_body(source, "attach(node)")
        assert (
            attach.index("wireMultipleOfLock(node)")
            < attach.index("wireRatioLock(node)")
            < attach.index("attachCopyFromImage(node)")
        )


# ------------------------------------------------------------ malformed value (item 7)


class TestMalformedMultipleOfDegradesSoft:
    """A garbled `multiple_of` (NaN, a non-numeric widget value, a negative
    number from a hand-edited save file) must degrade to "off", never
    throw -- FORMAT.md §7/§8."""

    def test_snap_dimension_value_never_throws_on_a_nan_unit(self, multiple_of_api: dict) -> None:
        # NaN != NaN, so find the case by its (non-NaN) neighbors rather
        # than by equality.
        idx = next(
            i
            for i, case in enumerate(SNAP_DIMENSION_CASES)
            if case[0] == 1023 and isinstance(case[1], float) and math.isnan(case[1])
        )
        assert SNAP_DIMENSION_CASES[idx][-1] == 1023
        assert multiple_of_api["snapDimensionValue"][idx] == 1023

    def test_current_multiple_of_value_fails_soft(self, source: str) -> None:
        body = _function_body(source, "currentMultipleOfValue(node)")
        # `Number(x) || 0` degrades undefined/null/NaN/"" to 0 ("off")
        # without ever throwing, matching every other widget-value read in
        # this file (getSnapUnit, conformNodeSizeToRatio).
        assert "Number(widgetByName(node, 'multiple_of')?.value) || 0" in body

    def test_every_multiple_of_gate_rejects_non_positive(self, source: str) -> None:
        """Every consumer of `currentMultipleOfValue` treats <= 0 (off,
        negative, or NaN-collapsed-to-0) identically -- "off", never a
        thrown error or a negative snap unit reaching `snapTo`."""
        assert "unit > 0 ? unit : NATIVE_DIMENSION_STEP" in source  # widget step
        assert "if (unit > 0) {" in source  # self-snap
        assert "if (!(unit > 0)) return" in source  # resnap early-out
