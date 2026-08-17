"""Frontend tests for the EPS Resolution M3 size-presets UI (a `preset`
combo + Save/Delete buttons over the backend's hidden `presets` JSON-array
widget), added to ``web/eps_image/resolution.js`` alongside the pre-existing
M1/M2 code ``tests/test_resolution_grid_js.py`` already covers.

Follows ``tests/test_checkpoint_switcher_js.py``'s dual convention (that
file's own docstring, and this file's own task brief, both name it
explicitly): pure exported helpers (``selectionFromWidgetValue``,
``toggleSelection``, ``normalizeSelectionOrder``, ``dropdownLabelFor``,
``presetRowIndexFor``) are driven headlessly under Node via a served-layout
probe script (mirrors ``test_resolution_grid_js.py``'s own fixture -- same
directory depth, same byte-copy-the-real-module approach, this time ALSO
stubbing ``scripts/api.js`` since this module now imports it too); the rest
of the M3 code -- widget construction/hiding, the property-toggle chain,
Save/Delete's HTTP flows, the ContextMenu multi-select -- only runs inside
``attach()`` against a real litegraph node/DOM/network and has no browser
harness here, so it is pinned via SOURCE-TEXT assertions instead, matching
``test_checkpoint_switcher_js.py``/``test_notebook_restore_js.py``'s
identical convention for that class of code.

**One deliberate, documented divergence from the task brief's literal
wording**, pinned accurately here rather than asserted falsely: the brief
calls for the `preset` combo at ``node.widgets[0]``, above `width`. Verified
directly against this rig's installed `comfyui_frontend_package`'s
`LGraphNode.ts` (see ``resolution.js``'s own "M3: size presets" section
header for the full citation trail) that doing so would corrupt every
reload of width/height/resize_method/interpolation/multiple_of/presets for
every user's EXISTING saved workflow -- litegraph's `widgets_values`
save/restore is positional and NOT symmetric for a non-tail
`serialize:false` widget, and inserting anything (serialized or not) before
`width` shifts every later widget's restored value by one regardless. So
`preset`/`Save`/`Delete` are instead inserted immediately after every REAL
backend widget (i.e. after the hidden `presets` widget) and immediately
before the M2 pad -- the same provably-safe tail region the pad widget
already occupies alone. "Save + Delete directly above the pad" (the brief's
OTHER position requirement) is satisfied exactly as asked; the tests below
pin the REAL, safe ordering, not the literal-but-corrupting one.

Skips cleanly when Node isn't installed; the LIVE mechanics (an actual
combo click reaching a real node, a real workflow save/reload round-trip, a
SHIFT-click ContextMenu, the fetch hitting real `/eps_resolution/presets*`
routes) are for the rig, not here.
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

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

# --------------------------------------------------------------- case tables

#: (JS source snippet for the raw value, expected selectionFromWidgetValue()
#: result). Raw JS, not JSON-encoded from Python -- like
#: test_checkpoint_switcher_js.py's identical RAW_VALUE_CASES -- because
#: `undefined` and a bare (non-string) array have no JSON representation.
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
        '\'["Portrait","16:9 wide"]\'',
        ["Portrait", "16:9 wide"],
    ),
]

#: (list, name, checked, expected toggleSelection() result) -- identical
#: shape to test_checkpoint_switcher_js.py's TOGGLE_NAME_CASES for its
#: sibling toggleName().
TOGGLE_SELECTION_CASES = [
    ([], "a", True, ["a"]),
    (["a"], "a", True, ["a"]),  # already checked -> idempotent, no dup
    (["a", "b"], "a", False, ["b"]),
    (["a", "b"], "c", False, ["a", "b"]),  # no-op removal of an absent name
    (["a", "b"], "b", True, ["a", "b"]),  # already checked -> unchanged
    (["b", "a", "c"], "a", False, ["b", "c"]),  # order stability
    (["b", "a", "c"], "d", True, ["b", "a", "c", "d"]),  # new entries append
]

#: (selection, fetchedNames, expected normalizeSelectionOrder() result).
NORMALIZE_ORDER_CASES = [
    ([], ["A", "B"], []),
    # known names re-sort to FETCHED order, not the selection's own order.
    (["B", "A"], ["A", "B"], ["A", "B"]),
    # a selected name absent from the fetched store ("missing") is appended
    # after the known ones rather than dropped (req. 5).
    (["A", "Ghost"], ["A", "B"], ["A", "Ghost"]),
    (["Ghost", "A"], ["A", "B"], ["A", "Ghost"]),  # known-first regardless
    # two missing names keep THEIR OWN relative order among themselves.
    (["Ghost2", "Ghost1"], ["A"], ["Ghost2", "Ghost1"]),
    # duplicates in the raw selection collapse to one.
    (["A", "A"], ["A", "B"], ["A"]),
    # non-string entries are ignored, not treated as "missing" names.
    ([1, "A", None], ["A"], ["A"]),
    # non-array selection/fetchedNames degrade gracefully.
    (None, ["A"], []),
    (["A"], None, ["A"]),
]

#: (selection, expected dropdownLabelFor() result) -- req. 2's exact
#: "(none)"/name/"N presets" three-state contract.
DROPDOWN_LABEL_CASES = [
    ([], "(none)"),
    (["Portrait"], "(none)".replace("(none)", "Portrait")),  # i.e. "Portrait"
    (["A", "B"], "2 presets"),
    (["A", "B", "C"], "3 presets"),
]

#: (widgets array, name, expected presetRowIndexFor() result).
PRESET_ROW_INDEX_CASES = [
    ([], "grid", -1),
    ([{"name": "width"}, {"name": "grid"}, {"name": "Save"}], "grid", 1),
    ([{"name": "width"}], "grid", -1),
    # a null/falsy entry in the array must not crash the lookup.
    ([{"name": "a"}, None, {"name": "grid"}], "grid", 2),
    (None, "grid", -1),  # non-array widgets degrades to -1, not a throw
]

PROBE_JS = """
import * as m from './extensions/comfyui-epsnodes/eps_image/resolution.js'

const out = {
  exports: {
    hasInit: typeof m.init === 'function',
    hasAttach: typeof m.attach === 'function',
    hasSelectionFromWidgetValue: typeof m.selectionFromWidgetValue === 'function',
    hasToggleSelection: typeof m.toggleSelection === 'function',
    hasNormalizeSelectionOrder: typeof m.normalizeSelectionOrder === 'function',
    hasDropdownLabelFor: typeof m.dropdownLabelFor === 'function',
    hasPresetRowIndexFor: typeof m.presetRowIndexFor === 'function'
  },
  selectionFromWidgetValue: [%(raw_values)s].map((v) => m.selectionFromWidgetValue(v)),
  toggleSelection: %(toggle_inputs)s.map(
    ([list, name, checked]) => m.toggleSelection(list, name, checked)
  ),
  normalizeSelectionOrder: %(normalize_inputs)s.map(
    ([selection, fetched]) => m.normalizeSelectionOrder(selection, fetched)
  ),
  dropdownLabelFor: %(dropdown_inputs)s.map((selection) => m.dropdownLabelFor(selection)),
  presetRowIndexFor: %(row_index_inputs)s.map(
    ([widgets, name]) => m.presetRowIndexFor(widgets, name)
  )
}

process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def presets_api(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Runs the probe against the REAL resolution.js in a served-layout tmp
    dir (see module docstring) and returns its JSON output."""
    layout = tmp_path_factory.mktemp("web_root")

    module_dir = layout / "extensions" / "comfyui-epsnodes" / "eps_image"
    module_dir.mkdir(parents=True)
    shutil.copyfile(RESOLUTION_JS, module_dir / "resolution.js")

    # resolution.js's two imports -- `../../../scripts/app.js` (pre-existing,
    # M1/M2) and `../../../scripts/api.js` (new, this round). Only the pure
    # helpers are exercised here, so bare stubs suffice; the relative DEPTH
    # is what's actually load-bearing (get it wrong and Node fails to
    # resolve the module at all) -- test_resolution_grid_js.py's identical
    # app.js stub, plus checkpoint_switcher_api's identical api.js stub.
    scripts = layout / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "app.js").write_text("export const app = {}\n", encoding="utf-8")
    (scripts / "api.js").write_text("export const api = { fetchApi: () => {} }\n", encoding="utf-8")

    probe = layout / "probe.mjs"
    probe.write_text(
        PROBE_JS
        % {
            "raw_values": ", ".join(js for js, _ in RAW_VALUE_CASES),
            "toggle_inputs": json.dumps(
                [[lst, name, checked] for lst, name, checked, _ in TOGGLE_SELECTION_CASES]
            ),
            "normalize_inputs": json.dumps(
                [[selection, fetched] for selection, fetched, _ in NORMALIZE_ORDER_CASES]
            ),
            "dropdown_inputs": json.dumps([selection for selection, _ in DROPDOWN_LABEL_CASES]),
            "row_index_inputs": json.dumps(
                [[widgets, name] for widgets, name, _ in PRESET_ROW_INDEX_CASES]
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
    """Raw text of resolution.js -- for SOURCE-STRUCTURE assertions about
    code that only runs inside attach() against a real litegraph node, a
    real DOM, and a real network (widget construction/hiding, the
    property-toggle chain, Save/Delete's HTTP flows, the ContextMenu
    multi-select), which this repo has no browser harness to drive -- see
    module docstring and test_checkpoint_switcher_js.py's identical
    convention for the same class of code."""
    return RESOLUTION_JS.read_text(encoding="utf-8")


def _function_body(source_text: str, signature: str) -> str:
    """The body of a top-level ``function <signature> {`` declaration (an
    `export` prefix, if any, is not part of *signature* and does not need to
    match), up to its closing brace at column 0 -- identical helper to
    test_checkpoint_switcher_js.py/test_distributor_js.py (this file's own
    top-level functions follow the same 2-space internal indent /
    unindented closing brace convention)."""
    start_match = re.search(re.escape(f"function {signature} {{") + r"\n", source_text)
    assert start_match, f"function {signature} {{ not found"
    start = start_match.end()
    end_match = re.search(r"\n\}\n", source_text[start:])
    assert end_match, f"function {signature}'s closing brace not found"
    return source_text[start : start + end_match.start()]


# ------------------------------------------------------------- parses / exports


def test_resolution_js_still_parses() -> None:
    """`node --check` -- the file must at minimum be valid ES module syntax
    after the M3 addition (test_resolution_grid_js.py pins the same thing;
    repeated here so this file stands alone)."""
    result = subprocess.run(
        [NODE, "--check", str(RESOLUTION_JS)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


def test_module_exports_the_presets_pure_helpers(presets_api: dict) -> None:
    """web/eps_image.js consumes init()/attach() (unchanged by M3); the five
    pure helpers are the seams this file's own probe drives directly."""
    assert presets_api["exports"] == {
        "hasInit": True,
        "hasAttach": True,
        "hasSelectionFromWidgetValue": True,
        "hasToggleSelection": True,
        "hasNormalizeSelectionOrder": True,
        "hasDropdownLabelFor": True,
        "hasPresetRowIndexFor": True,
    }


# --------------------------------------------------- selectionFromWidgetValue


def test_selection_from_widget_value_cases(presets_api: dict) -> None:
    pairs = zip(RAW_VALUE_CASES, presets_api["selectionFromWidgetValue"], strict=True)
    for (raw_js, expected), got in pairs:
        msg = f"selectionFromWidgetValue({raw_js}) -> {got!r}, wanted {expected!r}"
        assert got == expected, msg


def test_selection_from_widget_value_never_throws_on_malformed_input(presets_api: dict) -> None:
    """Every malformed/non-array case degrades to `[]` rather than the probe
    process crashing (a non-zero exit would already fail the fixture; this
    pins the specific contract)."""
    assert all(got == [] for got in presets_api["selectionFromWidgetValue"][:12])


# ------------------------------------------------------------ toggleSelection


def test_toggle_selection_cases(presets_api: dict) -> None:
    pairs = zip(TOGGLE_SELECTION_CASES, presets_api["toggleSelection"], strict=True)
    for (lst, name, checked, expected), got in pairs:
        msg = f"toggleSelection({lst!r}, {name!r}, {checked!r}) -> {got!r}, wanted {expected!r}"
        assert got == expected, msg


# ------------------------------------------------------- normalizeSelectionOrder


def test_normalize_selection_order_cases(presets_api: dict) -> None:
    pairs = zip(NORMALIZE_ORDER_CASES, presets_api["normalizeSelectionOrder"], strict=True)
    for (selection, fetched, expected), got in pairs:
        msg = f"normalizeSelectionOrder({selection!r}, {fetched!r}) -> {got!r}, wanted {expected!r}"
        assert got == expected, msg


# ------------------------------------------------------------- dropdownLabelFor


def test_dropdown_label_for_cases(presets_api: dict) -> None:
    pairs = zip(DROPDOWN_LABEL_CASES, presets_api["dropdownLabelFor"], strict=True)
    for (selection, expected), got in pairs:
        assert got == expected, f"dropdownLabelFor({selection!r}) -> {got!r}, wanted {expected!r}"


def test_dropdown_label_for_three_states_verbatim(presets_api: dict) -> None:
    """req. 2's exact contract, spelled out rather than derived from the
    table above: "(none)" / the sole name / "N presets"."""
    labels = presets_api["dropdownLabelFor"]
    assert labels[0] == "(none)"
    assert labels[1] == "Portrait"
    assert labels[2] == "2 presets"
    assert labels[3] == "3 presets"


# ------------------------------------------------------------- presetRowIndexFor


def test_preset_row_index_for_cases(presets_api: dict) -> None:
    pairs = zip(PRESET_ROW_INDEX_CASES, presets_api["presetRowIndexFor"], strict=True)
    for (widgets, name, expected), got in pairs:
        msg = f"presetRowIndexFor({widgets!r}, {name!r}) -> {got!r}, wanted {expected!r}"
        assert got == expected, msg


# ---------------------------------------------------- Vue-nodes hide flags (req. 1)


def test_presets_widget_hidden_with_both_flags(source: str) -> None:
    """FORMAT.md §7.5: canvas reads `widget.hidden`, the Vue-nodes renderer
    reads `widget.options.hidden` and ignores the first outright -- both
    must be set. (The backend also ships `options.hidden: true` in
    INPUT_TYPES for its own half; this is the frontend's half, which must
    not assume that.)"""
    body = _function_body(source, "hidePresetsWidget(widget)")
    assert "widget.hidden = true" in body
    assert "widget.options = { ...(widget.options || {}), hidden: true }" in body


def test_presets_widget_is_hidden_during_attach(source: str) -> None:
    body = _function_body(source, "attachPresetsUi(node)")
    assert "hidePresetsWidget(widget)" in body


# --------------------------------------------------------- property toggle (req. 4)


def test_presets_property_seeded_default_true(source: str) -> None:
    assert "node.addProperty(PROP_PRESETS_ENABLED, true, 'boolean')" in source
    assert "const PROP_PRESETS_ENABLED = 'Presets'" in source


def test_property_changed_is_chained_never_replaced(source: str) -> None:
    """The task brief's own explicit instruction: extend the SAME
    onPropertyChanged wrap attach() already installs for the M1/M2
    properties, rather than installing a second, competing wrap."""
    body = _function_body(source, "attach(node)")
    assert "const originalOnPropertyChanged = node.onPropertyChanged" in body
    assert "originalOnPropertyChanged?.call(this, name, value, prevValue)" in body
    # Every M1/M2 property branch is still present -- this is an ADDITION,
    # not a replacement of the existing chain.
    assert "applyPassthroughVisibility(this)" in body
    assert "applyOriginalSizeVisibility(this)" in body
    assert "applyGridVisibility(this)" in body
    # The new M3 branch.
    assert "name === PROP_PRESETS_ENABLED" in body
    assert "applyPresetsPropertyVisibility(this)" in body


def test_presets_off_hides_widgets_and_clears_selection(source: str) -> None:
    body = _function_body(source, "applyPresetsPropertyVisibility(node)")
    assert "widget.hidden = !enabled" in body
    assert "widget.options = { ...(widget.options || {}), hidden: !enabled }" in body
    assert "commitSelection(node, [])" in body


def test_presets_off_invariant_also_enforced_on_reconcile(source: str) -> None:
    """Not just the toggle handler -- a saved file with Presets:false and a
    stale non-empty `presets` array self-corrects on every reconcile too
    (restore-safety belt-and-suspenders)."""
    body = _function_body(source, "reconcilePresetsUi(node)")
    assert "!enabled && raw.length > 0" in body
    assert "state.selection = []" in body


# --------------------------------------------------------------- Save (req. 3)


def test_save_prefills_with_the_active_preset_for_update(source: str) -> None:
    """"prefilled with the active preset's name when one is selected --
    that's 'update'" (task brief, req. 3). Blank prefill (create-new)
    otherwise -- exactly one selected is this file's own definition of
    "active"."""
    body = _function_body(source, "openSaveDialog(node, event)")
    assert "state.selection.length === 1 ? state.selection[0] : ''" in body
    assert "promptPresetName(node, prefill" in body


def test_save_posts_current_field_values_and_base_mtime(source: str) -> None:
    body = _function_body(source, "performSave(node, name)")
    assert "for (const field of PRESET_FIELD_NAMES)" in body
    assert "values[field] = widget.value" in body
    assert "body.base_mtime = state.mtime" in body
    assert "method: 'POST'" in body
    assert "PRESETS_SAVE_ROUTE" in body


def test_save_success_sets_saved_preset_active_and_selected(source: str) -> None:
    """"refreshes the dropdown options, sets the saved preset active +
    selected" (task brief, req. 3)."""
    body = _function_body(source, "performSave(node, name)")
    assert "applyPresetsPayload(node, data)" in body
    assert "commitSelection(node, [name])" in body


# ------------------------------------------------------------- Delete (req. 3)


def test_delete_button_starts_disabled(source: str) -> None:
    body = _function_body(source, "createDeleteButton(node, state)")
    assert "btn.disabled = true" in body


def test_delete_enabled_state_tracks_exactly_one_selected(source: str) -> None:
    """Delete's disabled guard: enabled only when exactly one preset is
    selected -- this file's definition of "ACTIVE preset" (req. 3),
    re-derived from the selection rather than tracked separately."""
    body = _function_body(source, "updateDeleteEnabled(node)")
    assert "state.deleteBtn.disabled = state.selection.length !== 1" in body


def test_delete_button_has_a_no_op_guard_alongside_disabled(source: str) -> None:
    """"widget.disabled = true and a no-op guard in the callback" (task
    brief, req. 3) -- belt-and-suspenders, not relying on `disabled` alone."""
    body = _function_body(source, "createDeleteButton(node, state)")
    assert "if (!state.deleteBtn || state.deleteBtn.disabled) return" in body


def test_delete_has_no_confirm_dialog(source: str) -> None:
    """"no confirm dialog -- deletion is recoverable by re-saving" (task
    brief, req. 3): performDelete must not gate on window.confirm or an
    equivalent."""
    body = _function_body(source, "performDelete(node)")
    assert "confirm(" not in body


def test_delete_clears_the_deleted_name_from_selection(source: str) -> None:
    body = _function_body(source, "performDelete(node)")
    assert "commitSelection(node, state.selection.filter((entry) => entry !== active))" in body


# ---------------------------------------------------------------- 409 conflicts


def test_save_409_toasts_and_refetches(source: str) -> None:
    """"409 on either -> toast the conflict and re-fetch... no Reload/
    Overwrite UI needed for a 5-field record" (task brief, req. 3) --
    the Notebook's fuller §3.5 conflict UI is deliberately NOT reproduced
    here; see the file's own "Divergence from the Notebook" comment."""
    body = _function_body(source, "performSave(node, name)")
    assert "response.status === 409" in body
    assert "await loadPresets(node)" in body
    assert "'warn'" in body


def test_delete_409_toasts_and_refetches(source: str) -> None:
    body = _function_body(source, "performDelete(node)")
    assert "response.status === 409" in body
    assert "await loadPresets(node)" in body
    assert "'warn'" in body


def test_notebook_divergence_is_documented(source: str) -> None:
    """Pins that the divergence from the Notebook's Reload/Overwrite UI is
    an explained decision in the source, not a silent gap (task brief's own
    explicit instruction: "note this divergence from the notebook in a
    comment")."""
    body = _function_body(source, "performSave(node, name)")
    assert "Divergence from the Notebook" in body


# --------------------------------------------------------- multi-select ContextMenu


def test_multi_select_is_gated_on_a_modifier_key(source: str) -> None:
    body = _function_body(source, "presetComboOnClick(opts)")
    assert "event.shiftKey || event.ctrlKey || event.metaKey" in body
    assert "openMultiSelectMenu(node, opts)" in body


def test_multi_select_falls_through_to_the_real_stock_onclick(source: str) -> None:
    """A plain click (no modifier) must still open the real single-pick
    dropdown -- the shadow must not silently swallow the common case."""
    body = _function_body(source, "createPresetCombo(node, state)")
    assert "combo.onClick.bind(combo)" in body
    body2 = _function_body(source, "presetComboOnClick(opts)")
    assert "if (stockOnClick) stockOnClick(opts)" in body2


def test_multi_select_toggles_via_the_pure_helper_and_stays_open(source: str) -> None:
    """Verified against ContextMenu.ts (file header citation): returning
    `true` from an item's own callback is what keeps the menu open --
    pinned here as the literal `return true`, plus the toggle going through
    `toggleSelection`/`commitSelection` (the single write path) rather than
    a bespoke selection mutation."""
    body = _function_body(source, "openMultiSelectMenu(node, opts)")
    assert "toggleSelection(state.selection, name, nextChecked)" in body
    assert "commitSelection(node, toggleSelection(state.selection, name, nextChecked))" in body
    assert "return true" in body
    assert "this.textContent = rowLabel(name, nextChecked)" in body


def test_context_menu_referenced_as_an_ambient_global(source: str) -> None:
    """`LiteGraph` is used unimported (verified precedent:
    `lora_library/controller.js`'s header, citing rgthree's own shipped
    code) -- this file must not add an import for it."""
    assert "new LiteGraph.ContextMenu(" in source
    assert not re.search(r"^import .*LiteGraph.*from", source, re.MULTILINE)


# --------------------------------------------------- widget-order insertion (req. 3)
#
# See this file's own module docstring for why the ACTUAL, safe ordering
# ("combo/Save/Delete immediately before the pad") is pinned here rather
# than the brief's literal-but-corrupting "combo before width".


def test_combo_and_buttons_are_relocated_before_the_pad_in_order(source: str) -> None:
    body = _function_body(source, "attachPresetsUi(node)")
    combo_idx = body.index("relocateBeforePad(node, state.combo)")
    save_idx = body.index("relocateBeforePad(node, state.saveBtn)")
    delete_idx = body.index("relocateBeforePad(node, state.deleteBtn)")
    assert combo_idx < save_idx < delete_idx


def test_relocate_before_pad_uses_the_pure_row_index_helper(source: str) -> None:
    body = _function_body(source, "relocateBeforePad(node, widget)")
    assert "presetRowIndexFor(widgets, GRID_WIDGET_NAME)" in body
    assert "widgets.splice(insertAt, 0, widget)" in body


def test_relocate_before_pad_index_arithmetic() -> None:
    """The pure core of the splice arithmetic, direct in Python: inserting
    at the pad's current index (or the array's end if the pad is absent)
    must land the new widget immediately before the pad and never disturb
    anything else's relative order."""

    def preset_row_index_for(widgets: list, name: str) -> int:
        for i, w in enumerate(widgets):
            if w and w.get("name") == name:
                return i
        return -1

    widgets = [{"name": "width"}, {"name": "presets"}, {"name": "eps_resolution_grid"}]
    pad_index = preset_row_index_for(widgets, "eps_resolution_grid")
    assert pad_index == 2
    widgets.insert(pad_index, {"name": "preset"})
    assert [w["name"] for w in widgets] == ["width", "presets", "preset", "eps_resolution_grid"]

    # No pad yet -- insert lands at the tail (fail-soft, still provably safe
    # per the widget-order section: nothing kept ever follows it).
    no_pad = [{"name": "width"}, {"name": "presets"}]
    assert preset_row_index_for(no_pad, "eps_resolution_grid") == -1


def test_new_widgets_are_excluded_from_widgets_values(source: str) -> None:
    """The load-bearing correctness fix this whole ordering section exists
    for: EVERY frontend-inserted widget must carry the TOP-LEVEL
    `.serialize = false` flag (`LGraphNode.ts`'s `widgets_values`
    save/restore check -- NOT `options.serialize`, a different, API-prompt-
    only flag; see `image_grid.js`'s "Clear button" section, cited in this
    file's own header)."""
    combo_body = _function_body(source, "createPresetCombo(node, state)")
    assert "combo.serialize = false" in combo_body
    save_body = _function_body(source, "createSaveButton(node)")
    assert "btn.serialize = false" in save_body
    delete_body = _function_body(source, "createDeleteButton(node, state)")
    assert "btn.serialize = false" in delete_body


def test_presets_ui_attached_after_the_grid_so_the_pad_already_exists(source: str) -> None:
    body = _function_body(source, "attach(node)")
    grid_idx = body.index("attachSizeGrid(node)")
    presets_idx = body.index("attachPresetsUi(node)")
    assert grid_idx < presets_idx


# -------------------------------------------------------- restore-safety (req. 5)


def test_on_configure_is_chained_never_replaced(source: str) -> None:
    """`onConfigure` is the one hook that fires AFTER widgets_values are
    restored (whole-workflow load AND paste) -- checkpoint_switcher.js's
    wireConfigureReload pattern, this repo's most-burned frontend lesson.
    attachPresetsUi's own wrap must chain on top of attachSizeGrid's
    already-installed one, not replace it."""
    body = _function_body(source, "attachPresetsUi(node)")
    assert "const originalOnConfigure = node.onConfigure" in body
    assert "node.onConfigure = function presetsOnConfigure(info) {" in body
    assert "originalOnConfigure?.call(this, info)" in body
    assert "reconcilePresetsUi(this)" in body


def test_fetch_and_configure_both_reconcile_through_the_same_function(source: str) -> None:
    """Whichever of {the initial fetch, onConfigure} finishes LAST must
    produce the final render -- both call sites re-derive `state.selection`
    from the widget's CURRENT value via the same function."""
    configure_body = _function_body(source, "attachPresetsUi(node)")
    load_body = _function_body(source, "loadPresets(node)")
    assert "reconcilePresetsUi(this)" in configure_body
    assert "reconcilePresetsUi(node)" in load_body
    reconcile_body = _function_body(source, "reconcilePresetsUi(node)")
    assert "selectionFromWidgetValue(state.widget.value)" in reconcile_body


def test_missing_preset_name_stays_selected_with_a_missing_label(source: str) -> None:
    """"stays in the selection... server errors loudly at run time -- do not
    silently drop" (task brief, req. 5)."""
    normalize_body = _function_body(source, "normalizeSelectionOrder(selection, fetchedNames)")
    assert "return [...known, ...missing]" in normalize_body
    label_body = _function_body(source, "labelForToken(state, token)")
    assert "`${token} (missing)`" in label_body


# ----------------------------------------------------- req. 6: remote-correctness


def test_all_presets_fetches_are_relative_via_api_fetchapi(source: str) -> None:
    assert "import { api } from '../../../scripts/api.js'" in source
    for route_call in ("api.fetchApi(PRESETS_ROUTE)", "PRESETS_SAVE_ROUTE", "PRESETS_DELETE_ROUTE"):
        assert route_call in source
    assert "http://" not in source and "https://" not in source


def test_no_bubble_phase_window_pointer_listeners_added(source: str) -> None:
    """FORMAT.md §7.5: this M3 addition uses no window-level gesture
    listeners at all (every interaction is a widget click/callback), so
    this should find none -- protects against one being added later
    without the capture flag, matching test_vue_nodes_compat.py's pin for
    the rest of this file."""
    plain_adds = re.findall(r"window\.addEventListener\('pointer\w+', \w+\)", source)
    assert not plain_adds, f"bubble-phase window pointer listener(s) found: {plain_adds}"


# --------------------------------------------------------------- req. 7: byte-identical


def test_m1_m2_property_names_and_functions_untouched(source: str) -> None:
    """req. 7: "keep every existing behavior byte-identical" -- the M1/M2
    property constants and their apply-functions must still be present,
    unrenamed, alongside the new M3 ones."""
    for needle in (
        "const PROP_SHOW_PASSTHROUGH = 'Show passthrough image'",
        "const PROP_SHOW_ORIGINAL_SIZE = 'Show original size'",
        "const PROP_SHOW_GRID = 'Show grid'",
        "const PROP_GRID_MAX = 'Grid max'",
        "function applyPassthroughVisibility(node)",
        "function applyOriginalSizeVisibility(node)",
        "function applyGridVisibility(node)",
    ):
        assert needle in source


def test_apply_preset_values_only_touches_fields_when_selection_is_nonempty(source: str) -> None:
    """The five typed fields/pad/readouts are only ever written by this
    file's M3 code from ONE call site -- `commitSelection`'s `ordered.length
    === 1` branch -- never unconditionally, so an empty (unused) selection
    leaves M1/M2 completely untouched."""
    body = _function_body(source, "commitSelection(node, nextSelection)")
    assert "if (ordered.length === 1) applyPresetValues(node, ordered[0])" in body
    # The definition's own signature line + this one call site -- no other
    # call sites exist anywhere else in the file.
    assert source.count("applyPresetValues(node,") == 2


def test_save_click_can_never_be_a_silent_no_op(source: str) -> None:
    """Owner report 2026-08-09 ("clicking save on presets ... doesn't seem
    to do anything", on the FIRST save): this file was the pack's ONLY
    canvas.prompt call site passing a NULL event (distributor.js and
    switcher.js both pass theirs). LGraphCanvas.prompt reads
    LGraphCanvas.active_canvas and positions off the event; with neither it
    THROWS -- reproduced live on the rig -- and the window.prompt fallback
    sat OUTSIDE the try, so a browser that also refuses window.prompt left
    the click dead with no dialog and no message.

    Three pins, one per link in that chain."""
    body = _function_body(source, "promptPresetName(node, prefill, onCommit, event)")
    # 1. The real event reaches canvas.prompt.
    assert "canvas.prompt('Preset name', prefill || '', commit, event ?? null)" in body
    assert "commit, null)" not in body
    # 2. window.prompt is GUARDED (it can throw "prompt() is not supported").
    guarded = body[body.index("window.prompt"):]
    assert "catch" in guarded
    # 3. A self-owned dialog backstops both, so Save always opens something.
    assert "promptPresetNameFallback(node, prefill, commit)" in body


def test_save_button_forwards_litegraph_s_event(source: str) -> None:
    """litegraph hands a button callback (value, canvas, node, pos, event);
    the event is exactly what canvas.prompt needs, so the Save widget must
    forward it rather than dropping it."""
    body = _function_body(source, "createSaveButton(node)")
    assert "(_value, _canvas, _node, _pos, event) => openSaveDialog(node, event)" in body


def test_fallback_dialog_is_self_contained_and_canvas_safe(source: str) -> None:
    """The last-resort dialog owns its own DOM: keydown must stopPropagation
    (canvas hotkeys would otherwise eat the typing -- the same rule every
    text input in this pack follows), and it must clean itself up."""
    body = _function_body(source, "promptPresetNameFallback(node, prefill, commit)")
    assert "keyEvent.stopPropagation()" in body
    assert "overlay.remove()" in body
    assert "'Enter'" in body and "'Escape'" in body


def test_preset_buttons_stay_out_of_the_api_prompt() -> None:
    """v0.63.0: `options.serialize` gates the API PROMPT while
    `widget.serialize` gates the workflow FILE (executionUtil.ts vs
    LGraphNode.ts). Both preset buttons had only the latter, so every
    queued prompt carried phantom `"Save"`/`"Delete"` inputs for the node
    (rig-caught 2026-08-14 while adding the copy-from-image button)."""
    source = RESOLUTION_JS.read_text(encoding="utf-8")
    save = _function_body(source, "createSaveButton(node)")
    assert "{ serialize: false }" in save
    assert "btn.serialize = false" in save
    delete = _function_body(source, "createDeleteButton(node, state)")
    assert "{ serialize: false }" in delete
    assert "btn.serialize = false" in delete
