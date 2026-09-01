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
]

#: (dims, ratio, anchor, multipleOf, expected {width,height}) -- the SAME
#: truth table as tests/test_resolution.py's TestConformToRatio, run
#: through the JS mirror.
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
    # multiple_of snaps ONLY the derived dimension. Deliberately NOT an
    # exact X.5 rounding tie (e.g. 800/64 == 12.5): JS's Math.round and
    # Python's banker's-rounding round() disagree exactly at a tie, and
    # that is an accepted, narrow own-your-helpers divergence (see
    # roundToMultipleOf's/`_round_to_multiple`'s own docs) -- these two
    # values (1250/64 == 19.53125) are unambiguous on both sides.
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

PROBE_JS = """
import * as m from './extensions/comfyui-epsnodes/eps_image/resolution.js'

const out = {
  exports: {
    hasParseRatio: typeof m.parseRatio === 'function',
    hasConformToRatio: typeof m.conformToRatio === 'function'
  },
  parseRatio: [%(parse_inputs)s].map((v) => m.parseRatio(v)),
  conformToRatio: %(conform_inputs)s.map(
    ([dims, ratio, anchor, multipleOf]) => m.conformToRatio(dims, ratio, anchor, multipleOf)
  )
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
    assert ratio_api["exports"] == {"hasParseRatio": True, "hasConformToRatio": True}


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
            < attach.index("attachCopyFromImage(node)")
        )
