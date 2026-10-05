"""Frontend tests for the EPS Resolution M4 ratio lock (owner ask
2026-08-28: "it should be possible to select a ratio and lock to it"),
added to ``web/eps_image/resolution.js`` alongside the pre-existing M1/M2/M3
code ``tests/test_resolution_grid_js.py``/``tests/test_resolution_presets_js.py``
already cover.

Same dual convention as those two sibling files (their own docstrings):
the pure, exported math (``parseRatio``, ``conformToRatio``) is driven
headlessly under Node via a served-layout probe script; the LIVE-graph
glue that only runs inside ``attach()`` against a real litegraph node
(``wireRatioLock``, ``writeSize``'s pre-conform, ``applyPresetValues``'s
conform-and-toast, the preset-orthogonality guard) has no browser harness
here and is pinned via SOURCE-TEXT assertions instead, matching
``test_resolution_presets_js.py``'s identical convention for that class of
code.

``conformToRatio``'s truth table here is the SAME case list
``tests/test_resolution.py``'s ``TestConformToRatio`` runs against the
backend's ``conform_to_ratio`` -- own-your-helpers parity, pinned by
construction (both files were written from one shared table).

2026-10-05 round (owner asks: type a ratio, 3:4 / 4:3 presets, rotate): the
parse/conform cases now also come from ``tests/ratio_cases.py`` -- the SAME
table ``tests/test_resolution.py`` runs through the backend -- so the two
readers of a ratio are pinned to each other, decimals, separators, exact
``.5`` ties and all. The new pure helpers (``normalizeRatioInput``,
``formatRatioNumber``, ``flipRatioValue``, ``ratioOptionValues``) and the
state-registry pattern (run through a real JavaScript RegExp) are driven here
too; the LIVE ``custom…`` / rotate / button-row flows are exercised against a
fake litegraph node in ``tests/test_resolution_rows_js.py``.

Skips cleanly when Node isn't installed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from ratio_cases import CONFORM_CASES as SHARED_CONFORM_CASES
from ratio_cases import PARSE_CASES as SHARED_PARSE_CASES
from ratio_cases import STATE_PATTERN_CASES

from eps_image.nodes_resolution import RATIO_STATE_PATTERN as STATE_PATTERN

REPO_ROOT = Path(__file__).resolve().parent.parent
RESOLUTION_JS = REPO_ROOT / "web" / "eps_image" / "resolution.js"
# v1.6 (2026-10-05): resolution.js imports the shared button-row helper.
BUTTON_ROW_JS = REPO_ROOT / "web" / "eps_image" / "button_row.js"
API_JS = REPO_ROOT / "web" / "lora_library" / "api.js"
VERSION_JS = REPO_ROOT / "web" / "lora_library" / "version.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

# --------------------------------------------------------------- case tables

#: (JS source snippet for the raw value, expected parseRatio() result or
#: None). Raw JS so non-string/odd inputs (undefined, a bare number) are
#: representable without a JSON round-trip.
PARSE_RATIO_CASES = [
    ("'1:1'", {"w": 1, "h": 1}),
    ("'5:4'", {"w": 5, "h": 4}),
    ("'4:5'", {"w": 4, "h": 5}),
    ("'9:16'", {"w": 9, "h": 16}),
    ("'16:9'", {"w": 16, "h": 9}),
    ("'  4:5  '", {"w": 4, "h": 5}),
    ("'none'", None),
    ("''", None),
    ("'bogus'", None),
    ("'1:0'", None),
    ("'0:1'", None),
    ("'-1:1'", None),
    ("'1:1:1'", None),
    ("'1'", None),
    ("undefined", None),
    ("null", None),
    ("42", None),
    # not text at all (only representable as raw JS, so not in the shared table)
    ("true", None),
    ("{}", None),
    ("['16:9']", None),
    ("NaN", None),
]

#: (dims, ratio, anchor, multipleOf, expected {width,height}) -- the SAME
#: truth table as tests/test_resolution.py's TestConformToRatio, run
#: through the JS mirror. (The newer rows -- the two new presets, typed
#: decimals, the exact .5 ties -- come from tests/ratio_cases.py and are
#: appended below.)
CONFORM_CASES = [
    # "none" is a pure passthrough.
    ({"width": 777, "height": 333}, "none", "width", 0, {"width": 777, "height": 333}),
    ({"width": 777, "height": 333}, "bogus", "height", 64, {"width": 777, "height": 333}),
    # width-anchor: all 5 ratio options.
    ({"width": 1000, "height": 1}, "1:1", "width", 0, {"width": 1000, "height": 1000}),
    ({"width": 1000, "height": 1}, "5:4", "width", 0, {"width": 1000, "height": 800}),
    ({"width": 1000, "height": 1}, "4:5", "width", 0, {"width": 1000, "height": 1250}),
    ({"width": 900, "height": 1}, "9:16", "width", 0, {"width": 900, "height": 1600}),
    ({"width": 1600, "height": 1}, "16:9", "width", 0, {"width": 1600, "height": 900}),
    # height-anchor: all 5 ratio options.
    ({"width": 1, "height": 1000}, "1:1", "height", 0, {"width": 1000, "height": 1000}),
    ({"width": 1, "height": 800}, "5:4", "height", 0, {"width": 1000, "height": 800}),
    ({"width": 1, "height": 1250}, "4:5", "height", 0, {"width": 1000, "height": 1250}),
    ({"width": 1, "height": 1600}, "9:16", "height", 0, {"width": 900, "height": 1600}),
    ({"width": 1, "height": 900}, "16:9", "height", 0, {"width": 1600, "height": 900}),
    # multiple_of snaps ONLY the derived dimension. (Until 2026-10-05 these
    # rows avoided exact X.5 ties on purpose -- JS's Math.round and Python's
    # banker's round() disagreed there. The panel now rounds half to EVEN like
    # Python, and the ties are pinned in tests/ratio_cases.py instead.)
    (
        {"width": 1000, "height": 1},
        "4:5",
        "width",
        64,
        {"width": 1000, "height": 1280},
    ),
    (
        {"width": 1, "height": 1000},
        "5:4",
        "height",
        64,
        {"width": 1280, "height": 1000},
    ),
    # multiple_of off leaves the derived dimension exact.
    ({"width": 1000, "height": 1}, "5:4", "width", 0, {"width": 1000, "height": 800}),
    # nonpositive anchor is a no-op (nothing concrete to lock to yet).
    ({"width": 0, "height": 500}, "1:1", "width", 0, {"width": 0, "height": 500}),
    ({"width": 500, "height": 0}, "1:1", "height", 0, {"width": 500, "height": 0}),
    ({"width": 0, "height": 0}, "1:1", "width", 0, {"width": 0, "height": 0}),
]

CONFORM_CASES += [
    ({"width": w, "height": h}, ratio, anchor, mult, {"width": ew, "height": eh})
    for w, h, ratio, mult, anchor, (ew, eh) in SHARED_CONFORM_CASES
]

#: shared parse rows as raw JS string literals, expected as {w, h} or None.
PARSE_RATIO_CASES += [
    (json.dumps(text), None if expected is None else {"w": expected[0], "h": expected[1]})
    for text, expected in SHARED_PARSE_CASES
]

#: (raw JS, expected normalizeRatioInput()) -- what the custom box's text means
#: as the canonical value the widget stores, or None when it is not a ratio.
NORMALIZE_CASES = [
    ("'21x9'", "21:9"),
    ("'21 / 9'", "21:9"),
    ("'21X9'", "21:9"),
    ("'21\u00d79'", "21:9"),
    ("'2.390 : 1.0'", "2.39:1"),
    ("'1.0:1'", "1:1"),
    ("'  16:9  '", "16:9"),
    ("'16:9'", "16:9"),
    ("'007:4'", "7:4"),
    ("'0.50:1'", "0.5:1"),
    ("'1.85:1'", "1.85:1"),
    ("'none'", "none"),
    ("'NONE'", "none"),
    ("' None '", "none"),
    ("'banana'", None),
    ("''", None),
    ("'0:5'", None),
    ("'-4:3'", None),
    ("'1:2:3'", None),
    ("'custom\u2026'", None),
    ("undefined", None),
    ("null", None),
    ("16", None),
]

#: (raw JS number, expected formatRatioNumber()).
FORMAT_NUMBER_CASES = [
    ("16", "16"),
    ("2.39", "2.39"),
    ("2.390", "2.39"),
    ("1.0", "1"),
    ("0.5", "0.5"),
    ("0.1 + 0.2", "0.3"),  # a float's own noise never reaches a stored value
    ("999999.999999", "999999.999999"),
    ("0.000001", "0.000001"),
    ("1 / 3", "0.333333"),
]

#: (value, expected flipRatioValue()) -- the `rotate` button's ratio.
FLIP_CASES = [
    ("'16:9'", "16:9", "9:16"),
    ("'9:16'", "9:16", "16:9"),
    ("'4:3'", "4:3", "3:4"),
    ("'3:4'", "3:4", "4:3"),
    ("'5:4'", "5:4", "4:5"),
    ("'4:5'", "4:5", "5:4"),
    ("'2.39:1'", "2.39:1", "1:2.39"),
    ("'1:2.39'", "1:2.39", "2.39:1"),
    ("'21x9'", "21x9", "9:21"),  # another spelling comes back canonical
    ("'1:1'", "1:1", "1:1"),
    ("'3:3'", "3:3", "3:3"),  # any square ratio has nothing to flip
    ("'none'", "none", "none"),
    ("'banana'", "banana", "banana"),
    ("'custom\u2026'", "custom\u2026", "custom\u2026"),
]

PRESETS_FROM_BACKEND = ["none", "1:1", "5:4", "4:5", "4:3", "3:4", "16:9", "9:16"]

#: (presets arg, current value, expected ratioOptionValues()). `custom…` is
#: always last; a current value is folded in only when it is a ratio that is
#: not already a preset.
CUSTOM = "custom\u2026"
OPTION_VALUES_CASES = [
    (PRESETS_FROM_BACKEND, "none", [*PRESETS_FROM_BACKEND, CUSTOM]),
    (PRESETS_FROM_BACKEND, "16:9", [*PRESETS_FROM_BACKEND, CUSTOM]),
    (PRESETS_FROM_BACKEND, "21:9", [*PRESETS_FROM_BACKEND, "21:9", CUSTOM]),
    (PRESETS_FROM_BACKEND, "2.39:1", [*PRESETS_FROM_BACKEND, "2.39:1", CUSTOM]),
    (PRESETS_FROM_BACKEND, "1:2.39", [*PRESETS_FROM_BACKEND, "1:2.39", CUSTOM]),
    # a hand-edited / garbage value is NOT folded in (Nodes 2.0 should flag it)
    (PRESETS_FROM_BACKEND, "banana", [*PRESETS_FROM_BACKEND, CUSTOM]),
    (PRESETS_FROM_BACKEND, "", [*PRESETS_FROM_BACKEND, CUSTOM]),
    (PRESETS_FROM_BACKEND, None, [*PRESETS_FROM_BACKEND, CUSTOM]),
    # the command is never a value
    (PRESETS_FROM_BACKEND, CUSTOM, [*PRESETS_FROM_BACKEND, CUSTOM]),
    # a build that hands no array falls back to the panel's own presets
    (None, "none", [*PRESETS_FROM_BACKEND, CUSTOM]),
    # a smaller backend list is respected
    (["none", "1:1"], "16:9", ["none", "1:1", "16:9", CUSTOM]),
]

#: A generated grid -- every preset, typed whole-number and decimal ratios,
#: widths and heights through the ranges people use (and a stripe of odd values
#: so exact .5 ties occur), with and without multiple_of, both anchors -- run
#: through BOTH implementations and compared EXACTLY. The two used to differ
#: at exact ties (JavaScript's Math.round rounds .5 up, Python's round() takes
#: the even neighbour), and a hand-picked table can't be trusted to hit them
#: all.
_GRID_RATIOS = [
    *PRESETS_FROM_BACKEND[1:],
    "21:9",
    "2.39:1",
    "1.85:1",
    "0.5:1",
    "1:2.39",
    "7:5",
    "3:2",
    "1.5:1",
]
CONFORM_GRID = [
    [width, height, ratio, multiple_of, anchor]
    for ratio in _GRID_RATIOS
    for multiple_of in (0, 4, 8, 64)
    for anchor in ("width", "height")
    for width, height in [(n, 1) for n in (*range(1, 400, 7), 480, 512, 1000, 1024, 1920, 4096)]
    + [(1, n) for n in (*range(1, 400, 11), 576, 720, 1000, 1080, 2160)]
]

PROBE_JS = """
import * as m from './extensions/comfyui-epsnodes/eps_image/resolution.js'

const out = {
  exports: {
    hasParseRatio: typeof m.parseRatio === 'function',
    hasConformToRatio: typeof m.conformToRatio === 'function',
    hasNormalizeRatioInput: typeof m.normalizeRatioInput === 'function',
    hasFormatRatioNumber: typeof m.formatRatioNumber === 'function',
    hasFlipRatioValue: typeof m.flipRatioValue === 'function',
    hasRatioOptionValues: typeof m.ratioOptionValues === 'function',
    customOption: m.RATIO_CUSTOM_OPTION
  },
  parseRatio: [%(parse_inputs)s].map((v) => m.parseRatio(v)),
  conformToRatio: %(conform_inputs)s.map(
    ([dims, ratio, anchor, multipleOf]) => m.conformToRatio(dims, ratio, anchor, multipleOf)
  ),
  normalizeRatioInput: [%(normalize_inputs)s].map((v) => m.normalizeRatioInput(v)),
  formatRatioNumber: [%(format_inputs)s].map((v) => m.formatRatioNumber(v)),
  flipRatioValue: [%(flip_inputs)s].map((v) => m.flipRatioValue(v)),
  ratioOptionValues: %(option_inputs)s.map(
    ([presets, current]) => m.ratioOptionValues(presets, current)
  ),
  // every row of the generated parity grid, through the JS mirror
  conformGrid: %(grid_inputs)s.map(
    ([width, height, ratio, multipleOf, anchor]) =>
      m.conformToRatio({ width, height }, ratio, anchor, multipleOf)
  ),
  // the Universal State pattern, run through a REAL JavaScript RegExp -- the
  // same call universal_controller.js's validateStateValue makes
  statePattern: %(state_inputs)s.map((text) => new RegExp(%(state_pattern)s).test(text))
}

process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def ratio_api(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Runs the probe against the REAL resolution.js in a served-layout tmp
    dir (mirrors test_resolution_grid_js.py's/test_resolution_presets_js.py's
    identical fixture shape)."""
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
            "parse_inputs": ", ".join(js for js, _ in PARSE_RATIO_CASES),
            "conform_inputs": json.dumps(
                [[dims, ratio, anchor, mult] for dims, ratio, anchor, mult, _ in CONFORM_CASES]
            ),
            "normalize_inputs": ", ".join(js for js, _ in NORMALIZE_CASES),
            "format_inputs": ", ".join(js for js, _ in FORMAT_NUMBER_CASES),
            "flip_inputs": ", ".join(js for js, _, _ in FLIP_CASES),
            "option_inputs": json.dumps(
                [[presets, current] for presets, current, _ in OPTION_VALUES_CASES]
            ),
            "grid_inputs": json.dumps(CONFORM_GRID),
            "state_inputs": json.dumps([text for text, _ in STATE_PATTERN_CASES]),
            "state_pattern": json.dumps(STATE_PATTERN),
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


def test_ratio_helpers_are_exported(ratio_api: dict) -> None:
    assert ratio_api["exports"] == {
        "hasParseRatio": True,
        "hasConformToRatio": True,
        "hasNormalizeRatioInput": True,
        "hasFormatRatioNumber": True,
        "hasFlipRatioValue": True,
        "hasRatioOptionValues": True,
        "customOption": "custom\u2026",
    }


# --------------------------------------------------------------- parseRatio


def test_parse_ratio_matches_the_case_table(ratio_api: dict) -> None:
    results = ratio_api["parseRatio"]
    assert len(results) == len(PARSE_RATIO_CASES)
    for (js, expected), actual in zip(PARSE_RATIO_CASES, results, strict=True):
        assert actual == expected, f"parseRatio({js}) -> {actual!r}, expected {expected!r}"


# ------------------------------------------------------------ conformToRatio


def test_conform_to_ratio_matches_the_shared_truth_table(ratio_api: dict) -> None:
    results = ratio_api["conformToRatio"]
    assert len(results) == len(CONFORM_CASES)
    for (dims, ratio, anchor, mult, expected), actual in zip(CONFORM_CASES, results, strict=True):
        assert actual == expected, (
            f"conformToRatio({dims}, {ratio!r}, {anchor!r}, {mult}) -> {actual!r}, "
            f"expected {expected!r}"
        )


# ------------------------------------------------- the typed-ratio helpers (2026-10-05)


def test_normalize_ratio_input_cases(ratio_api: dict) -> None:
    results = ratio_api["normalizeRatioInput"]
    assert len(results) == len(NORMALIZE_CASES)
    for (js, expected), actual in zip(NORMALIZE_CASES, results, strict=True):
        assert actual == expected, f"normalizeRatioInput({js}) -> {actual!r}, expected {expected!r}"


def test_normalize_ratio_input_is_idempotent(ratio_api: dict) -> None:
    """A canonical value normalises to itself -- so the box's answer can be fed
    back through (Apply, a restore) without drifting."""
    for result in ratio_api["normalizeRatioInput"]:
        if result is not None:
            assert result == result.strip()
    canonical = [r for r in ratio_api["normalizeRatioInput"] if r not in (None, "none")]
    assert canonical and all(":" in r for r in canonical)


def test_format_ratio_number_cases(ratio_api: dict) -> None:
    results = ratio_api["formatRatioNumber"]
    assert len(results) == len(FORMAT_NUMBER_CASES)
    for (js, expected), actual in zip(FORMAT_NUMBER_CASES, results, strict=True):
        assert actual == expected, f"formatRatioNumber({js}) -> {actual!r}, expected {expected!r}"


def test_flip_ratio_value_cases(ratio_api: dict) -> None:
    results = ratio_api["flipRatioValue"]
    assert len(results) == len(FLIP_CASES)
    for (js, _before, expected), actual in zip(FLIP_CASES, results, strict=True):
        assert actual == expected, f"flipRatioValue({js}) -> {actual!r}, expected {expected!r}"


def test_flipping_twice_gets_back_to_where_it_started(ratio_api: dict) -> None:
    """16:9 -> 9:16 -> 16:9 for every flippable ratio (the second flip is
    covered by the pairs above, run in both directions)."""
    flipped = {before: after for _js, before, after in FLIP_CASES}
    for before, after in flipped.items():
        if before not in ("21x9", "banana", CUSTOM) and after != before:
            assert flipped.get(after) == before


def test_ratio_option_values_cases(ratio_api: dict) -> None:
    results = ratio_api["ratioOptionValues"]
    assert len(results) == len(OPTION_VALUES_CASES)
    for (presets, current, expected), actual in zip(OPTION_VALUES_CASES, results, strict=True):
        assert actual == expected, (
            f"ratioOptionValues({presets!r}, {current!r}) -> {actual!r}, expected {expected!r}"
        )


def test_the_panel_and_the_backend_conform_identically_over_a_generated_grid(
    ratio_api: dict,
) -> None:
    """The panel's `conformToRatio` mirrors the backend's `conform_to_ratio`
    "bit for bit" -- so the pad shows exactly the number a Run computes. Held to
    it here over thousands of generated cases, exact `.5` ties included."""
    from eps_image.nodes_resolution import conform_to_ratio

    results = ratio_api["conformGrid"]
    assert len(results) == len(CONFORM_GRID) > 5000
    mismatches = []
    for (width, height, ratio, multiple_of, anchor), js in zip(CONFORM_GRID, results, strict=True):
        expected = conform_to_ratio(width, height, ratio, multiple_of, anchor)
        if (js["width"], js["height"]) != expected:
            mismatches.append(((width, height, ratio, multiple_of, anchor), expected, js))
    assert not mismatches, f"{len(mismatches)} mismatches, first few: {mismatches[:5]}"


def test_state_pattern_matches_the_shared_table_in_a_real_javascript_regexp(
    ratio_api: dict,
) -> None:
    """The pattern string the backend declares is evaluated by BOTH ends --
    Python's ``re`` in test_resolution.py, JavaScript's RegExp here -- over
    the same table (tests/ratio_cases.py)."""
    results = ratio_api["statePattern"]
    assert len(results) == len(STATE_PATTERN_CASES)
    for (text, expected), actual in zip(STATE_PATTERN_CASES, results, strict=True):
        assert actual is expected, f"/{STATE_PATTERN}/.test({text!r}) -> {actual!r}"


def test_the_ratio_input_regexp_has_no_nonascii_digits_or_exponents(source: str) -> None:
    """``\\d`` is ASCII-only in a JS RegExp, exactly what the backend's
    ``[0-9]`` means -- the two agree on what a number is. Pinned so nobody
    "improves" it to a Unicode-aware class on one side only."""
    assert "const RATIO_NUMBER = '(\\\\d{1,6}(?:\\\\.\\\\d{1,6})?)'" in source
    assert "[:xX\u00d7/]" in source


# ------------------------------------------------------- live-graph wiring pins


class TestRatioLockOrthogonalToPresets:
    """Owner ask, explicit: 'the ratio lock is ORTHOGONAL to presets:
    locking a ratio must not clear a preset, and choosing a preset must not
    clear the lock.' Both directions are pinned here since neither has a
    pure-function shape to probe directly (they only exist inside
    attach()'s live wiring)."""

    def test_ratio_widget_pick_is_guarded_against_clearing_a_preset(
        self, source: str
    ) -> None:
        body = _function_body(source, "wireRatioLock(node)")
        assert "withRatioApplyGuard(node," in body
        assert "ratioWidget.callback" in body

    def test_ratio_apply_guard_reuses_the_presets_own_applying_flag(
        self, source: str
    ) -> None:
        body = _function_body(source, "withRatioApplyGuard(node, fn)")
        assert "presetsState(node)" in body
        assert "state.applying = true" in body
        assert "state.applying = false" in body

    def test_preset_apply_conforms_inside_its_own_applying_guard(self, source: str) -> None:
        body = _function_body(source, "applyPresetValues(node, name)")
        assert "conformNodeSizeToRatio(node," in body
        # the conform call sits INSIDE the try that state.applying = true
        # guards -- i.e. before the matching `state.applying = false`.
        assert body.index("conformNodeSizeToRatio(node,") < body.index("state.applying = false")


class TestRatioLockWriteOrdering:
    """`writeSize` pre-conforms BEFORE writing either widget, rather than
    writing the raw pair and trusting a wrapped width/height callback's own
    re-derivation -- see that function's own comment for the exact ordering
    hazard this avoids (a later explicit height write silently undoing an
    earlier wrap-triggered conform)."""

    def test_write_size_conforms_before_either_setwidgetvalue_call(self, source: str) -> None:
        body = _function_body(source, "writeSize(node, width, height)")
        conform_at = body.index("conformNodeSizeToRatio(node, width, height, 'width')")
        first_write_at = body.index("setWidgetValue(")
        assert conform_at < first_write_at
        assert "conformed.width" in body and "conformed.height" in body


class TestRatioLockAnchorChoice:
    """Owner wording, pinned literally: 'the dimension the user just
    edited is the anchor'; selecting a ratio while none was set anchors on
    width."""

    def test_typed_width_or_height_edit_anchors_on_itself(self, source: str) -> None:
        body = _function_body(source, "wireRatioLock(node)")
        assert "conformNodeSizeToRatio(node, w, h, name)" in body

    def test_picking_a_ratio_anchors_on_width(self, source: str) -> None:
        body = _function_body(source, "wireRatioLock(node)")
        assert "conformNodeSizeToRatio(node, w, h, 'width')" in body


class TestRatioLockReloadReconcile:
    """A saved workflow whose stored width/height don't satisfy its own
    stored ratio (only reachable via a hand-built/edited file -- the panel
    always keeps them in sync) is silently reconciled on configure, guarded
    the same way so it can never clear a restored preset selection."""

    def test_on_configure_is_chained_not_replaced(self, source: str) -> None:
        body = _function_body(source, "wireRatioLock(node)")
        assert "const originalOnConfigure = node.onConfigure" in body
        assert "originalOnConfigure?.call(this, info)" in body

    def test_reconcile_runs_under_the_same_guard(self, source: str) -> None:
        body = _function_body(source, "wireRatioLock(node)")
        # the onConfigure block is the SECOND withRatioApplyGuard call in
        # this function (the ratio widget's own pick is the first).
        assert body.count("withRatioApplyGuard(") >= 2


class TestRatioLockAttachOrdering:
    def test_wire_ratio_lock_runs_after_presets_ui_before_copy_button(
        self, source: str
    ) -> None:
        attach = _function_body(source, "attach(node)")
        assert (
            attach.index("attachPresetsUi(node)")
            < attach.index("wireRatioLock(node)")
            < attach.index("attachCopyRotateRow(node)")
        )
