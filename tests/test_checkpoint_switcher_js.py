"""Frontend tests for the EPS Checkpoint Switcher frontend (a Python-side
node built in parallel against the same contract this file pins).

``web/eps_image/checkpoint_switcher.js`` factors its two trickiest bits of
logic into PURE exported functions -- ``selectionFromWidgetValue`` (parses
the `selection` widget's JSON string) and ``toggleName`` (add/remove a name
from an ordered list) -- precisely so this file can drive them under Node
without a litegraph node stub, following ``tests/test_distributor_js.py``'s
"served-layout" convention: the module's only import is
``../../../scripts/api.js``, resolved against the served layout, so the
fixture mirrors that directory depth in a tmp dir and byte-copies the real
module in. Get the relative import depth wrong and Node cannot resolve the
module at all -- the ``checkpoint_switcher_api`` fixture is therefore also a
regression test for the import path itself.

The rest of the file -- attach()'s class gate, the restore-race fix
(``wireConfigureReload``), both Vue-nodes hide flags, the JSON write path,
missing-row handling, and the fetch-failure/Retry branch -- is
structural/closure-bound (it only runs inside `attach()` against a real
litegraph node and a real DOM) and has no browser harness to drive here, so
it is pinned via SOURCE-TEXT assertions instead, matching
``tests/test_notebook_restore_js.py``'s module-scope ``source`` fixture
convention (that file's own docstring: notebook.js is "DOM/closure-bound...
so these are SOURCE-TEXT pins").

Skips cleanly when Node isn't installed; the LIVE mechanics (an actual
checkbox click reaching the widget, a real workflow save/reload round-trip,
the fetch hitting a real `/eps_ckpt/checkpoints`) are for the rig, not here.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKPOINT_SWITCHER_JS = REPO_ROOT / "web" / "eps_image" / "checkpoint_switcher.js"
# Since 2026-08-22 (FORMAT.md §7.6) the panel imports the cross-OS matcher
# from the lora_library side -- its real siblings are copied into the
# served layout too (see the fixture).
PATH_HEAL_JS = REPO_ROOT / "web" / "lora_library" / "path_heal.js"
API_JS = REPO_ROOT / "web" / "lora_library" / "api.js"
VERSION_JS = REPO_ROOT / "web" / "lora_library" / "version.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

# --------------------------------------------------------------- case tables

#: (JS source snippet for the raw value, expected selectionFromWidgetValue()
#: result). Built as raw JS rather than JSON-encoded from Python -- like
#: test_distributor_js.py's OUTPUT_LINK_CASES -- because `undefined` and a
#: bare (non-string) array have no JSON representation.
RAW_VALUE_CASES = [
    ("undefined", []),
    ("null", []),
    ("42", []),
    ("{}", []),  # a bare JS object, not the string "{}"
    ("[]", []),  # a bare JS array, not a JSON-string -- typeof raw !== 'string'
    ("'[]'", []),
    ("'not json'", []),
    ("'{}'", []),  # valid JSON, but not an array
    ("'null'", []),  # valid JSON, but not an array
    ("'42'", []),  # valid JSON, but not an array
    ("''", []),
    ("'   '", []),
    ('\'["a","b","a"]\'', ["a", "b"]),  # dedupe keeps the FIRST occurrence
    ('\'["a", 5, null, "b"]\'', ["a", "b"]),  # non-string entries dropped
    (
        '\'["sd15/model-a.safetensors","model-b.safetensors"]\'',
        ["sd15/model-a.safetensors", "model-b.safetensors"],
    ),
]

#: (list, name, checked, expected toggleName() result).
TOGGLE_NAME_CASES = [
    ([], "a", True, ["a"]),
    (["a"], "a", True, ["a"]),  # already checked -> idempotent, no dup
    (["a", "b"], "a", False, ["b"]),
    (["a", "b"], "c", False, ["a", "b"]),  # no-op removal of an absent name
    (["a", "b"], "b", True, ["a", "b"]),  # already checked -> unchanged
    (["b", "a", "c"], "a", False, ["b", "c"]),  # order stability: untouched entries keep order
    (["b", "a", "c"], "d", True, ["b", "a", "c", "d"]),  # new entries append at the end
]

#: (selection, fetched checkpoints, expected resolveSelectionSpelling() as a
#: plain object: entry -> LOCAL listed spelling | None). FORMAT.md §7.6 --
#: Python single-backslash strings land in JS as single-backslash strings
#: via json.dumps.
SPELLING_CASES = [
    # listed verbatim -> itself
    (["a.st"], ["a.st", "styles/b.st"], {"a.st": "a.st"}),
    # saved on Windows, this machine lists forward slashes
    (["styles\\b.st"], ["a.st", "styles/b.st"], {"styles\\b.st": "styles/b.st"}),
    # the reverse trip
    (["styles/b.st"], ["styles\\b.st"], {"styles/b.st": "styles\\b.st"}),
    # genuinely missing here
    (["gone.st"], ["a.st"], {"gone.st": None}),
    # exact-present wins even when a sibling normalizes to the same key
    (["p\\q.st"], ["p/q.st", "p\\q.st"], {"p\\q.st": "p\\q.st"}),
    # collision between two DISTINCT local files -> never guess
    (["p/q.st "], ["p/q.st", "p\\q.st"], {"p/q.st ": None}),
    # case is not folded
    (["A/b.st"], ["a/b.st"], {"A/b.st": None}),
    # duplicates collapse to one key; order of the rest is irrelevant here
    (["a.st", "a.st", "x\\y.st"], ["x/y.st", "a.st"], {"a.st": "a.st", "x\\y.st": "x/y.st"}),
    # degenerate inputs never throw
    ([], ["a.st"], {}),
    (None, None, {}),
    ([5, None, "a.st"], ["a.st"], {"a.st": "a.st"}),
]

PROBE_JS = """
import * as m from './extensions/comfyui-epsnodes/eps_image/checkpoint_switcher.js'

const out = {
  exports: {
    hasInit: typeof m.init === 'function',
    hasAttach: typeof m.attach === 'function',
    hasSelectionFromWidgetValue: typeof m.selectionFromWidgetValue === 'function',
    hasToggleName: typeof m.toggleName === 'function',
    hasResolveSelectionSpelling: typeof m.resolveSelectionSpelling === 'function'
  },
  constants: {
    classId: m.CLASS_ID,
    selectionWidgetName: m.SELECTION_WIDGET_NAME,
    route: m.ROUTE
  },
  selectionFromWidgetValue: [%(raw_values)s].map((v) => m.selectionFromWidgetValue(v)),
  toggleName: %(toggle_inputs)s.map(([list, name, checked]) => m.toggleName(list, name, checked)),
  resolveSelectionSpelling: %(spelling_inputs)s.map(([selection, checkpoints]) =>
    Object.fromEntries(m.resolveSelectionSpelling(selection, checkpoints))
  )
}

process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def checkpoint_switcher_api(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Runs the probe against the REAL checkpoint_switcher.js in a
    served-layout tmp dir (see module docstring) and returns its JSON
    output."""
    layout = tmp_path_factory.mktemp("web_root")

    module_dir = layout / "extensions" / "comfyui-epsnodes" / "eps_image"
    module_dir.mkdir(parents=True)
    shutil.copyfile(CHECKPOINT_SWITCHER_JS, module_dir / "checkpoint_switcher.js")

    # checkpoint_switcher.js imports `../../../scripts/api.js` -- stubbed
    # exactly as test_distributor_js.py stubs `scripts/app.js` -- and, since
    # FORMAT.md §7.6, `../lora_library/path_heal.js` (-> `./api.js` ->
    # `./version.js` + `../../../scripts/app.js`): the REAL siblings are
    # copied in. Only the pure helpers are exercised here, so a no-op
    # fetchApi / an empty app suffice; the relative DEPTHS are what's
    # actually load-bearing (get one wrong and Node fails to resolve the
    # module at all).
    lora_dir = layout / "extensions" / "comfyui-epsnodes" / "lora_library"
    lora_dir.mkdir(parents=True)
    shutil.copyfile(PATH_HEAL_JS, lora_dir / "path_heal.js")
    shutil.copyfile(API_JS, lora_dir / "api.js")
    shutil.copyfile(VERSION_JS, lora_dir / "version.js")

    scripts = layout / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "api.js").write_text("export const api = { fetchApi: () => {} }\n", encoding="utf-8")
    (scripts / "app.js").write_text("export const app = {}\n", encoding="utf-8")

    toggle_inputs = json.dumps(
        [[lst, name, checked] for lst, name, checked, _ in TOGGLE_NAME_CASES]
    )
    probe = layout / "probe.mjs"
    probe.write_text(
        PROBE_JS
        % {
            "raw_values": ", ".join(js for js, _ in RAW_VALUE_CASES),
            "toggle_inputs": toggle_inputs,
            "spelling_inputs": json.dumps([[sel, ckpts] for sel, ckpts, _ in SPELLING_CASES]),
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
    """Raw text of checkpoint_switcher.js -- for SOURCE-STRUCTURE assertions
    about code that only runs inside attach() against a real litegraph node
    and a real DOM (the class gate, the restore-race fix, both hide flags,
    the write path, missing-row handling, the fetch-failure/Retry branch),
    which this repo has no browser harness to drive -- see module docstring
    and test_notebook_restore_js.py's identical convention for the same
    class of code."""
    return CHECKPOINT_SWITCHER_JS.read_text(encoding="utf-8")


def _function_body(source_text: str, signature: str) -> str:
    """The body of a top-level ``function <signature> {`` declaration (an
    `export` prefix, if any, is not part of *signature* and does not need to
    match), up to its closing brace at column 0 -- test_distributor_js.py's
    identical helper (this file's own top-level functions follow the same
    2-space internal indent / unindented closing brace convention)."""
    start_match = re.search(re.escape(f"function {signature} {{") + r"\n", source_text)
    assert start_match, f"function {signature} {{ not found"
    start = start_match.end()
    end_match = re.search(r"\n\}\n", source_text[start:])
    assert end_match, f"function {signature}'s closing brace not found"
    return source_text[start : start + end_match.start()]


# ------------------------------------------------------------- parses / exports


def test_checkpoint_switcher_js_parses() -> None:
    """`node --check` -- the file must at minimum be valid ES module syntax."""
    result = subprocess.run(
        [NODE, "--check", str(CHECKPOINT_SWITCHER_JS)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


def test_module_exports_the_extension_entry_points_and_pure_helpers(
    checkpoint_switcher_api: dict,
) -> None:
    """web/eps_image.js consumes init()/attach(); selectionFromWidgetValue/
    toggleName are the pure seams this file's own probe drives directly."""
    assert checkpoint_switcher_api["exports"] == {
        "hasInit": True,
        "hasAttach": True,
        "hasSelectionFromWidgetValue": True,
        "hasToggleName": True,
        "hasResolveSelectionSpelling": True,
    }


def test_class_id_and_route_constants(checkpoint_switcher_api: dict) -> None:
    """Pins the exact contract given to both the frontend and backend
    implementers so a typo on either side fails loudly instead of the two
    sides silently disagreeing."""
    constants = checkpoint_switcher_api["constants"]
    assert constants["classId"] == "EPSCheckpointSwitcher"
    assert constants["selectionWidgetName"] == "selection"
    assert constants["route"] == "/eps_ckpt/checkpoints"


# --------------------------------------------------- selectionFromWidgetValue


def test_selection_from_widget_value_cases(checkpoint_switcher_api: dict) -> None:
    pairs = zip(RAW_VALUE_CASES, checkpoint_switcher_api["selectionFromWidgetValue"], strict=True)
    for (raw_js, expected), got in pairs:
        msg = f"selectionFromWidgetValue({raw_js}) -> {got!r}, wanted {expected!r}"
        assert got == expected, msg


def test_selection_from_widget_value_never_throws_on_malformed_input(
    checkpoint_switcher_api: dict,
) -> None:
    """Every malformed/non-array case in RAW_VALUE_CASES resolves to `[]`
    rather than the probe process crashing -- the probe's own non-zero exit
    code would already fail the fixture, but this pins the specific
    contract: malformed JSON, and any non-array JSON value, both degrade to
    an empty selection rather than propagating an error into attach()."""
    assert all(got == [] for got in checkpoint_switcher_api["selectionFromWidgetValue"][:11])


# -------------------------------------------------------------------- toggleName


def test_toggle_name_cases(checkpoint_switcher_api: dict) -> None:
    pairs = zip(TOGGLE_NAME_CASES, checkpoint_switcher_api["toggleName"], strict=True)
    for (lst, name, checked, expected), got in pairs:
        msg = f"toggleName({lst!r}, {name!r}, {checked!r}) -> {got!r}, wanted {expected!r}"
        assert got == expected, msg


# -------------------------------------------------------------- attach gating


def test_attach_gates_on_the_exact_class_id(source: str) -> None:
    """The task brief's literal requirement: attach() no-ops for every node
    type other than EPSCheckpointSwitcher."""
    body = _function_body(source, "attach(node)")
    assert "if (nodeClassOf(node) !== CLASS_ID) return" in body
    assert "export const CLASS_ID = 'EPSCheckpointSwitcher'" in source


def test_attach_guards_against_a_double_nodecreated(source: str) -> None:
    body = _function_body(source, "attach(node)")
    assert "if (attachedNodes.has(node)) return" in body
    assert "attachedNodes.add(node)" in body


def test_attach_never_throws(source: str) -> None:
    """The house pattern: every sibling file's attach() wraps its whole body
    in try/catch + console.warn(PREFIX, ...) so one bad node never blocks
    the other extensions' nodeCreated hooks."""
    body = _function_body(source, "attach(node)")
    assert body.strip().startswith("try {")
    assert "} catch (error) {" in body
    assert "console.warn(PREFIX, 'attach failed', error)" in body


# ---------------------------------------------------- Vue-nodes hide flags


def test_selection_widget_hidden_with_both_flags(source: str) -> None:
    """FORMAT.md §7.5: canvas reads `widget.hidden`, the Vue-nodes renderer
    reads `widget.options.hidden` and ignores the first outright -- both
    must be set or the widget leaks into a Vue node as a raw text field."""
    body = _function_body(source, "hideSelectionWidget(state)")
    assert "widget.hidden = true" in body
    assert "hidden: true" in body
    assert "widget.options = { ...(widget.options || {}), hidden: true }" in body


# -------------------------------------------------------- restore-correctness


def test_on_configure_is_chained_never_replaced(source: str) -> None:
    """`onConfigure` is the one hook that fires AFTER widgets_values are
    restored (whole-workflow load AND paste) -- notebook.js's
    wireConfigureReload header, this repo's most-burned frontend lesson."""
    assert "function wireConfigureReload(state)" in source
    assert "wireConfigureReload(state)" in _function_body(source, "attach(node)")
    body = _function_body(source, "wireConfigureReload(state)")
    assert "const originalOnConfigure = node.onConfigure" in body
    assert "node.onConfigure = function (info) {" in body
    assert "originalOnConfigure.apply(this, arguments)" in body
    assert "reloadFromWidget(state)" in body


def test_fetch_and_configure_both_reconcile_through_the_same_resync(source: str) -> None:
    """Whichever of {the initial fetch, onConfigure} finishes LAST must be
    the one that produces the final render -- both call sites re-derive
    `state.selection` from the widget's CURRENT value via the same
    function, rather than each trusting its own private snapshot."""
    configure_body = _function_body(source, "wireConfigureReload(state)")
    load_body = _function_body(source, "loadCheckpoints(state)")
    assert "reloadFromWidget(state)" in configure_body
    assert "reloadFromWidget(state)" in load_body
    reload_body = _function_body(source, "reloadFromWidget(state)")
    assert "selectionFromWidgetValue(state.widget.value)" in reload_body


# --------------------------------------------------------------- write path


def test_selection_written_as_json_with_callback_and_dirty_canvas(source: str) -> None:
    body = _function_body(source, "writeSelectionWidget(state, nextSelection)")
    assert "const json = JSON.stringify(ordered)" in body
    assert "state.widget.value = json" in body
    assert "state.widget.callback?.(json)" in body
    assert "state.node.graph?.setDirtyCanvas(true, true)" in body


def test_selection_order_is_the_fetched_list_order_not_click_order(source: str) -> None:
    """A toggle must not simply append the clicked name -- the write path
    re-sorts to the server's own order every time (task brief: "stable
    order = the server list order"), with unknown/missing names appended
    after rather than interleaved arbitrarily."""
    body = _function_body(source, "writeSelectionWidget(state, nextSelection)")
    # Since FORMAT.md §7.6 the sort key is each entry's LOCAL spelling (so a
    # foreign-spelled entry, heal setting OFF, keeps its stored string yet
    # sorts where its local file sits), one entry per local file.
    assert "const rank = new Map(state.checkpoints.map((name, index) => [name, index]))" in body
    assert "rank.get(localNameOf(state, a)) - rank.get(localNameOf(state, b))" in body
    assert "nextSelection.filter((name) => localNameOf(state, name) === null)" in body
    assert "const ordered = [...known, ...missing]" in body


def test_row_toggle_uses_the_pure_helper(source: str) -> None:
    body = _function_body(source, "onRowToggle(state, name, checked)")
    # §7.6: rows are keyed by local spelling, so an untick must also drop a
    # foreign-spelled alias of the same file -- aliases are cleared first,
    # then the pure helper applies the click.
    assert "localNameOf(state, entry) !== name" in body
    assert "toggleName(aliasFree, name, checked)" in body
    assert "writeSelectionWidget(state," in body


# ------------------------------------------------------------- missing rows


def test_missing_rows_are_synthesized_in_a_guarded_branch(source: str) -> None:
    """A selection entry absent from the fetched list must not be silently
    dropped -- buildRows() adds it back as its own row, guarded on it NOT
    being in the fetched set, still fully checked."""
    body = _function_body(source, "buildRows(state)")
    assert "missing: false" in body
    # §7.6: "missing" means resolves to NO local file (separator-insensitive),
    # not merely "not spelled the way this machine lists it".
    assert "if (localNameOf(state, name) === null)" in body
    assert "missing: true, checked: true" in body
    assert "checked: checkedLocal.has(name)" in body


def test_missing_rows_render_distinctly_but_stay_interactive(source: str) -> None:
    """Struck-through + ⚠-prefixed (task brief: "distinct... row"), and the
    checkbox construction is NOT branched on `missing` -- it must stay a
    normal, enabled, clickable checkbox so unchecking it is how the user
    removes it (task brief: "still uncheckable-off")."""
    body = _function_body(source, "buildRowEl(state, row)")
    assert "epscs-row-missing" in body
    assert "row.missing ?" in body
    assert "⚠" in body
    assert "disabled" not in body


# ------------------------------------------------------ empty / error states


def test_empty_state_message(source: str) -> None:
    body = _function_body(source, "renderList(state)")
    assert "allRows.length === 0" in body
    assert "'No checkpoints found'" in body


def test_fetch_failure_has_an_inline_error_and_a_retry_control(source: str) -> None:
    render_body = _function_body(source, "renderList(state)")
    assert "state.error" in render_body
    assert "'Retry'" in render_body
    assert "loadCheckpoints(state)" in render_body.split("'Retry'", 1)[1][:300]

    load_body = _function_body(source, "loadCheckpoints(state)")
    assert "} catch (error) {" in load_body
    assert "state.error = (error && error.message) || 'Failed to load checkpoints'" in load_body


def test_attach_never_throws_out_of_the_initial_load_either(source: str) -> None:
    """`loadCheckpoints` is async and called fire-and-forget from attach() --
    a rejection must still be caught, matching the sibling files'
    `.catch((error) => console.warn(...))` convention rather than becoming
    an unhandled promise rejection."""
    body = _function_body(source, "attach(node)")
    assert "loadCheckpoints(state).catch((error) => console.warn(PREFIX," in body


# ---------------------------------------------------- window-listener safety


def test_no_bubble_phase_window_pointer_listeners(source: str) -> None:
    """FORMAT.md §7.5: a bubble-phase `window.addEventListener('pointer...'`
    never fires once the Vue-nodes DOM wrapper stops propagation. This file
    adds no window-level listeners at all (every interaction is
    element-level -- see the file header), so this should find none; the
    assertion protects against one being added later without the capture
    flag, matching test_vue_nodes_compat.py's identical regex pin."""
    plain_adds = re.findall(r"window\.addEventListener\('pointer\w+', \w+\)", source)
    assert not plain_adds, f"bubble-phase window pointer listener(s) found: {plain_adds}"
    plain_removes = re.findall(r"window\.removeEventListener\('pointer\w+', \w+\)", source)
    assert not plain_removes, f"bubble-phase window pointer remove(s) found: {plain_removes}"


def test_filter_input_keydown_stops_propagation(source: str) -> None:
    """Canvas hotkeys (Delete/Ctrl+C/etc.) must not intercept typing in the
    filter box -- notebook.js's textarea/name-field keydown handlers do the
    same, cited in this file's buildFilterRow()."""
    body = _function_body(source, "buildFilterRow(state)")
    assert "addEventListener('keydown', (event) => event.stopPropagation())" in body


# -------------------------------------------------------------- width floor


def test_min_node_width_floor_is_present(source: str) -> None:
    assert "const MIN_NODE_WIDTH = 320" in source
    assert "installMinWidth(state.node, MIN_NODE_WIDTH)" in source


def test_width_floor_lifts_through_set_size_not_a_dead_is_array_guard(source: str) -> None:
    """v0.68.1: `LGraphNode.size` is a Proxy over a typed-array view on the
    installed frontend (1.48.7), never an Array, so installMinWidth's old
    `Array.isArray(node.size)` lift never ran; the lift goes through
    setSize() (litegraph's `_sizeUpdated` + the onResize wrap)."""
    assert "Array.isArray(node.size)" not in source
    body = _function_body(source, "installMinWidth(node, minWidth)")
    assert "if (node.size && node.size[0] < minWidth)" in body
    assert "node.setSize([minWidth, node.size[1]])" in body


# ------------------------------------------- cross-OS spelling (FORMAT.md §7.6)


def test_resolve_selection_spelling_cases(checkpoint_switcher_api: dict) -> None:
    """Each stored entry -> its LOCAL listed spelling: itself when listed;
    the ONE separator-insensitive match otherwise; null when nothing local
    matches (missing, collision, case difference)."""
    pairs = zip(SPELLING_CASES, checkpoint_switcher_api["resolveSelectionSpelling"], strict=True)
    for (selection, checkpoints, expected), got in pairs:
        msg = (
            f"resolveSelectionSpelling({selection!r}, {checkpoints!r}) -> {got!r}, "
            f"wanted {expected!r}"
        )
        assert got == expected, msg


def test_panel_shares_the_generic_matcher_and_gate(source: str) -> None:
    """One matcher for every combo in the pack: the panel imports
    healComboValue/isHealEnabled from path_heal.js rather than re-deriving
    the rule, so the generic heal and the panel can never disagree."""
    assert "import { healComboValue, isHealEnabled } from '../lora_library/path_heal.js'" in source
    body = _function_body(source, "resolveSelectionSpelling(selection, checkpoints)")
    assert "if (listed.has(name)) {" in body  # exact first
    assert "healComboValue(name, list)" in body
    assert "result.healed ? result.value : null" in body


def test_reload_reconciles_spelling_then_heals_then_paints(source: str) -> None:
    """Whichever of {fetch, onConfigure} lands last re-resolves every entry
    against the REAL list, heals (setting permitting), and only then
    repaints -- the same single reconcile step as before, extended."""
    body = _function_body(source, "reloadFromWidget(state)")
    reparse = body.index("selectionFromWidgetValue(state.widget.value)")
    spelling = body.index("refreshSpelling(state)")
    heal = body.index("healSelectionSpelling(state)")
    paint = body.index("renderList(state)")
    assert reparse < spelling < heal < paint


def test_heal_selection_spelling_is_gated_orderless_and_callback_free(source: str) -> None:
    """Setting ON: stored strings are rewritten to the local spelling in
    place -- order preserved, widget written directly (NO callback: a
    spelling fix, not a user edit), canvas marked dirty. Setting OFF (or
    list not loaded yet): untouched."""
    body = _function_body(source, "healSelectionSpelling(state)")
    assert body.strip().startswith("if (!state.loaded || !isHealEnabled()) return")
    assert "const local = localNameOf(state, name)" in body
    assert "state.widget.value = json" in body
    assert ".callback" not in body
    assert "state.node.graph?.setDirtyCanvas(true, true)" in body
    # a no-op when nothing changes: the widget is not rewritten needlessly
    assert "if (json === JSON.stringify(state.selection)) return" in body
    # no re-sorting on heal -- only writeSelectionWidget (a user click) imposes server order
    assert "rank" not in body and "sort(" not in body


def test_attach_primes_the_spelling_memo_and_count_uses_it(source: str) -> None:
    attach = _function_body(source, "attach(node)")
    assert "refreshSpelling(state)" in attach
    count = _function_body(source, "renderCount(state)")
    assert "localNameOf(state, name) !== null" in count
    local = _function_body(source, "localNameOf(state, name)")
    assert "state.spelling.has(name)" in local
    assert "state.checkpointSet.has(name) ? name : null" in local


def test_split_folder_splits_on_either_separator(source: str) -> None:
    """A foreign-spelled MISSING entry still groups/labels like its
    neighbours rather than showing a backslash-riddled base name."""
    body = _function_body(source, "splitFolder(name)")
    assert "Math.max(name.lastIndexOf('/'), name.lastIndexOf('\\\\'))" in body
