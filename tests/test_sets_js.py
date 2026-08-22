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


def test_sets_changed_refreshes_only_the_module_cache(source: str) -> None:
    """v0.68.1 (owner: "saving a state is slow / non-responsive"). Until
    v0.68.0 the CRUD handler ALSO awaited `app.refreshComboInNodes()`, on the
    premise that the Vue renderer builds its selects from the node
    DEFINITIONS. Read from the installed frontend (1.48.7): that call is
    `reloadNodeDefs()` -- GET /object_info (every pack's INPUT_TYPES re-run
    server-side), `registerNodeDef` for EVERY node type, every combo of
    every node rewritten, the Vue node-def store rebuilt, two toasts -- on
    the main thread right after Save. And the premise is false there: the
    Vue select evaluates `widget.options.values()` on open
    (`WidgetSelect` `handleOpenChange` -> `refreshOptions` ->
    `resolveRawValues`), i.e. THIS module's function and cache. So the CRUD
    event refreshes the module cache and nothing else; the wrap that
    survives the frontend's own "R" refresh stays."""
    body = _function_body(source, "initSetsFreshness()")
    assert "'lora_library:sets-changed'" in body
    assert "await refreshSetsCache(true)" in body
    assert "await app.refreshComboInNodes" not in body  # no global node-def reload on the CRUD path
    assert "app.refreshComboInNodes()" not in body.replace("`app.refreshComboInNodes()`", "")
    assert "refreshCombosEverywhere" not in source  # the helper is gone, not merely unused
    assert "wrapComfyComboRefresh()" in body
    # the mirrors-tag graph watch is armed at setup, when app.graph exists
    assert "installMirrorsGraphWatch()" in body


def test_forced_refresh_is_honored_while_a_fetch_is_in_flight(source: str) -> None:
    """v0.68.1: `if (fetchInFlight) return` used to come BEFORE `force` was
    honored, so the CRUD event's forced refresh could no-op against a
    throttled open-time refetch already in flight (and already stale) --
    `valuesIncluding`/`installSetValues` kept serving the old list. A forced
    caller now waits on that fetch and queues exactly one more."""
    body = _function_body(source, "refreshSetsCache(force = false)")
    in_flight = body.split("if (fetchInFlight) {", 1)[1].split("\n  }\n", 1)[0]
    assert "if (!force) return" in in_flight
    assert "refetchQueued = true" in in_flight
    assert "return fetchInFlight" in in_flight
    assert "} while (refetchQueued)" in body
    assert "fetchInFlight = fetchSetsOnce()" in body
    assert "let fetchInFlight = null" in source  # a promise now, not a boolean


class TestMirrorsComboIsPureV0681:
    """v0.68.1 perf: the `mirrors loader` combo's function-valued `values`
    walked the WHOLE workflow on every canvas draw (ComboWidget's
    `_displayValue` evaluates `values()` whenever no `getOptionLabel` is
    installed -- read from the installed bundle) and wrote `widget.value`
    from inside that getter. Now: identity `getOptionLabel` (the value IS the
    label), a pure `values` that still rebuilds on open in both renderers,
    and the vanished-loader self-heal moved to a graph-removal watch."""

    @pytest.fixture(scope="class")
    def source(self) -> str:
        return SETS_JS.read_text(encoding="utf-8")

    def test_values_is_pure_and_display_is_identity_mapped(self, source: str) -> None:
        body = _function_body(source, "attachMirrorsWidget(node)")
        assert "widget.options.values = () => [MIRRORS_ANY_VALUE, ...findPllCandidates().map((c) => c.label)]" in body
        assert "widget.options.getOptionLabel = (value) => (value == null ? '' : String(value))" in body
        assert "widget.value = MIRRORS_ANY_VALUE" not in body  # no write inside the getter any more

    def test_heal_rides_on_node_removal_not_on_draw(self, source: str) -> None:
        heal = _function_body(source, "healMirrorsTags()")
        assert "findPllCandidates()" in heal
        assert "widget.value = MIRRORS_ANY_VALUE" in heal
        watch = _function_body(source, "installMirrorsGraphWatch()")
        assert "api.walkGraphs(app.graph)" in watch
        assert "graph.__epsSetsMirrorsWatch" in watch  # install once per graph
        assert "original?.apply(this, args)" in watch  # chained, never replaced
        assert "scheduleMirrorsHeal()" in watch
        # one deferred pass per created node (configure() restores the tag
        # AFTER nodeCreated, so the tick lands after the whole load)
        attach = _function_body(source, "attachApplySetBehavior(node)")
        assert "installMirrorsGraphWatch()" in attach
        assert "scheduleMirrorsHeal()" in attach
        # the `set` combo's own name-mapping getOptionLabel is untouched
        installed = _function_body(source, "installSetValues(node)")
        assert "cachedNames.get(String(value)) || String(value)" in installed


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


class TestComboDisplaysNamesV0650:
    """Owner report 2026-08-14: a pushed state rendered as its SLUG
    ("state-1") instead of its NAME ("State 1"). The combo's value stays
    the slug (that is what executes); only the display maps through
    ComboWidget's own options.getOptionLabel seam."""

    @pytest.fixture(scope="class")
    def source(self) -> str:
        return SETS_JS.read_text(encoding="utf-8")

    def test_get_option_label_maps_slug_to_name_with_slug_fallback(self, source: str) -> None:
        body = _function_body(source, "installSetValues(node)")
        assert "widget.options.getOptionLabel = (value) =>" in body
        assert "cachedNames.get(String(value)) || String(value)" in body
        assert "value == null || value === 'None' ? 'None'" in body
        # the cache is rebuilt from the same feed the values come from
        assert "cachedNames = new Map((data.sets ?? []).map((row) => [row.slug, row.name || row.slug]))" in source
