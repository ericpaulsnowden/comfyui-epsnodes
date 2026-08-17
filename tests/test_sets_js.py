"""Frontend tests for ``web/lora_library/sets.js`` -- the EPS Apply LoRA Set
`set` combo's freshness (FORMAT.md §7.4).

SOURCE-TEXT pins: everything here runs inside `attachApplySetBehavior()` /
`initSetsFreshness()` against a real litegraph node and a real `app`, which
this repo has no browser harness to drive (the same convention
``tests/test_switcher_js.py``/``test_cross_sweep_js.py`` use for
closure-bound code). The LIVE mechanics were rig-verified in both renderers
when this fix shipped -- see the v0.59.3 commit.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SETS_JS = REPO_ROOT / "web" / "lora_library" / "sets.js"
CONTROLLER_JS = REPO_ROOT / "web" / "lora_library" / "controller.js"

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")


def _function_body(source_text: str, signature: str) -> str:
    """Body of a top-level ``function <signature> {`` up to its column-0
    closing brace -- identical helper to the sibling JS test files."""
    start_match = re.search(re.escape(f"function {signature} {{") + r"\n", source_text)
    assert start_match, f"function {signature} {{ not found"
    start = start_match.end()
    end_match = re.search(r"\n\}\n", source_text[start:])
    assert end_match, f"end of {signature} not found"
    return source_text[start : start + end_match.start()]


@pytest.fixture(scope="module")
def source() -> str:
    return SETS_JS.read_text(encoding="utf-8")


def test_syntax_is_valid(source: str) -> None:
    import subprocess

    result = subprocess.run([NODE, "--check", str(SETS_JS)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_comfy_combo_refresh_is_wrapped_so_values_survive_it(source: str) -> None:
    """THE root cause (owner report 2026-08-09, "the sets ... aren't updated
    when the main list is updated"), reproduced live on the rig: ComfyUI's
    own `app.refreshComboInNodes()` REPLACES `widget.options.values` with a
    frozen array, so after any refresh -- including ones the frontend fires
    for its own reasons -- this combo could never see a newly created set
    again, in EITHER renderer. Wrapping that method and re-installing our
    values function afterwards is what makes the freshness durable."""
    body = _function_body(source, "wrapComfyComboRefresh()")
    assert "comboRefreshWrapped" in body  # idempotent: no double-wrap
    assert "const original = app.refreshComboInNodes.bind(app)" in body
    assert "await original(...args)" in body
    assert "installSetValues(node)" in body


def test_sets_changed_also_refreshes_the_node_definitions(source: str) -> None:
    """Our module cache alone cannot reach the node DEFINITIONS the Vue
    renderer builds its selects from; `refreshComboInNodes()` can (it
    refetches /object_info, and INPUT_TYPES re-reads the sets dir per call),
    so the CRUD event drives both halves."""
    body = _function_body(source, "initSetsFreshness()")
    assert "'lora_library:sets-changed'" in body
    assert "await refreshCombosEverywhere()" in body
    assert "wrapComfyComboRefresh()" in body
    guarded = _function_body(source, "refreshCombosEverywhere()")
    assert "catch" in guarded  # a flaky /object_info must not kill the CRUD path


def test_current_value_is_always_one_of_the_options(source: str) -> None:
    """A combo cannot DISPLAY a value that isn't one of its options -- a set
    pushed from the Controller (or living only on another machine's copy of
    a shared library) would render blank and read as "the push did nothing"
    (owner report 2026-08-09, second half). The server already accepts
    unknown values (VALIDATE_INPUTS returns True, §6.2), so listing it is
    exactly what will execute."""
    body = _function_body(source, "valuesIncluding(current)")
    assert "cachedValues.includes(current) ? cachedValues : [...cachedValues, current]" in body
    installed = _function_body(source, "installSetValues(node)")
    assert "return valuesIncluding(widget.value)" in installed


def test_push_seeds_the_option_when_the_list_is_frozen() -> None:
    """controller.js's half of the same fix: when ComfyUI's refresh has
    frozen the values into an array, Push seeds the slug itself so the push
    is visible immediately (sets.js re-installs its live function on the
    next refresh and the entry is legitimized from the server)."""
    controller = CONTROLLER_JS.read_text(encoding="utf-8")
    body = _function_body(controller, "pushStateToNode(node, slug)")
    assert "Array.isArray(widget.options.values)" in body
    assert "!widget.options.values.includes(slug)" in body
    assert "widget.options.values = [...widget.options.values, slug]" in body
    # Still no cross-module import between the two files (both headers' rule).
    assert "from './sets.js'" not in controller


class TestMirrorsPickerNestedV0640:
    """v0.64.0: the `mirrors loader` tag can name an EPS LoRA Picker too,
    and candidates come from the whole workflow (subgraphs included) with
    path-id labels matching controller.js's target combo."""

    @pytest.fixture(scope="class")
    def source(self) -> str:
        return SETS_JS.read_text(encoding="utf-8")

    def test_mirror_candidates_span_both_families_and_subgraphs(self, source: str) -> None:
        body = _function_body(source, "findPllCandidates()")
        assert "api.walkLiveNodes(app.graph)" in body
        assert "PICKER_NODE_CLASS" in body
        assert "label: `${node.title || node.type} #${pathId}`" in body
        assert "const PICKER_NODE_CLASS = 'EPSLoraPicker'" in source

    def test_label_id_regex_accepts_paths(self, source: str) -> None:
        body = _function_body(source, "pllIdFromLabel(label)")
        assert "/#(-?\\d+(?::-?\\d+)*)\\s*$/" in body

    def test_apply_set_discovery_is_nested_aware(self, source: str) -> None:
        body = _function_body(source, "applySetNodes()")
        assert "walkLiveNodes(app.graph)" in body
