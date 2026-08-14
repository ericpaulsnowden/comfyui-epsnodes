"""Frontend tests for the EPS LoRA Picker panel (FORMAT.md §6.13, M1 -- a
Python-side node built in parallel against the same contract this file pins).

``web/lora_library/picker.js`` factors its trickiest logic into PURE
exported functions -- ``selectionFromWidgetValue`` (tolerant parse of the
`selection` widget's JSON string), ``serializeSelection`` (the stable-key-
order write path), ``clampStrength``, ``normalizeLoraName``, and
``listFolder`` (the client-derived tree) -- precisely so this file can
drive them under Node without a litegraph node stub, following
``tests/test_checkpoint_switcher_js.py``'s "served-layout" convention: the
module imports ``./api.js`` and ``../../../scripts/app.js``, resolved
against the served layout, so the fixture mirrors that directory depth in a
tmp dir and byte-copies the real modules in. Get the relative import depth
wrong and Node cannot resolve the module at all -- the ``picker_api``
fixture is therefore also a regression test for the import paths.

The rest -- attach()'s class gate, the restore-race fix
(``wireConfigureReload``), both Vue-nodes hide flags, the ⚠ ghost/missing
row conventions, the token-guarded fetch, the fire-and-forget recents POST,
the optimistic favorite flip -- is structural/closure-bound and pinned via
SOURCE-TEXT assertions, matching ``test_checkpoint_switcher_js.py``'s
module-scope ``source`` fixture convention.

Skips cleanly when Node isn't installed; the LIVE mechanics (a real Add
reaching the widget, a workflow save/reload round-trip, the fetch hitting a
real ``/lora_library/picker``) are for the rig, not here.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PICKER_JS = REPO_ROOT / "web" / "lora_library" / "picker.js"
API_JS = REPO_ROOT / "web" / "lora_library" / "api.js"
VERSION_JS = REPO_ROOT / "web" / "lora_library" / "version.js"
BRIDGE_JS = REPO_ROOT / "web" / "lora_library" / "pll_bridge.js"
DASIWA_JS = REPO_ROOT / "web" / "lora_library" / "dasiwa_bridge.js"
ENTRY_JS = REPO_ROOT / "web" / "lora_library.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

# --------------------------------------------------------------- case tables

#: (JS source snippet for the raw value, expected selectionFromWidgetValue()
#: result). Raw JS rather than JSON-encoded from Python -- like
#: test_checkpoint_switcher_js.py's RAW_VALUE_CASES -- because `undefined`
#: and bare non-string values have no JSON representation.
EMPTY = {"scope": "", "loras": []}
RAW_VALUE_CASES = [
    ("undefined", EMPTY),
    ("null", EMPTY),
    ("42", EMPTY),
    ("{}", EMPTY),  # a bare JS object, not the string "{}"
    ("[]", EMPTY),  # a bare JS array -- typeof raw !== 'string'
    ("''", EMPTY),
    ("'   '", EMPTY),
    ("'not json'", EMPTY),
    ("'[]'", EMPTY),  # valid JSON, but not an object
    ("'null'", EMPTY),  # valid JSON, but not an object
    (
        '\'{"scope":"a/b","loras":[{"file":"a/b/x.safetensors","on":true,"strength":1}]}\'',
        {
            "scope": "a/b",
            "loras": [
                {"file": "a/b/x.safetensors", "on": True, "strength": 1, "strength_clip": None}
            ],
        },
    ),
    (
        # Backslash spellings normalize (§4), `on` defaults true, strengths
        # clamp to [-10, 10], duplicates keep the FIRST occurrence.
        '\'{"scope":"a\\\\\\\\b","loras":[{"file":"a\\\\\\\\b\\\\\\\\x.st","strength":99},'
        '{"file":"a/b/x.st","strength":-99},{"file":"y.st","on":false,"strength":"junk"}]}\'',
        {
            "scope": "a/b",
            "loras": [
                {"file": "a/b/x.st", "on": True, "strength": 10, "strength_clip": None},
                {"file": "y.st", "on": False, "strength": 1, "strength_clip": None},
            ],
        },
    ),
    (
        # A finite strength_clip is preserved (a hand-edited or future-M row
        # must round-trip); rows without a string `file` are dropped.
        '\'{"loras":[{"file":"x.st","strength":0.5,"strength_clip":0.25},{"strength":1},null]}\'',
        {
            "scope": "",
            "loras": [{"file": "x.st", "on": True, "strength": 0.5, "strength_clip": 0.25}],
        },
    ),
]

#: (selection object as JSON, expected serialized string). Pins the STABLE
#: key order -- {"scope", "loras"} then {"file", "on", "strength",
#: "strength_clip"?} with strength_clip OMITTED when null (§6.13).
SERIALIZE_CASES = [
    (
        {"scope": "", "loras": []},
        '{"scope":"","loras":[]}',
    ),
    (
        {
            "scope": "a/b",
            "loras": [{"file": "x.st", "on": True, "strength": 1, "strength_clip": None}],
        },
        '{"scope":"a/b","loras":[{"file":"x.st","on":true,"strength":1}]}',
    ),
    (
        {
            "scope": "",
            "loras": [{"file": "x.st", "on": False, "strength": 0.5, "strength_clip": 0.25}],
        },
        '{"scope":"","loras":[{"file":"x.st","on":false,"strength":0.5,"strength_clip":0.25}]}',
    ),
]

#: A small served list exercising folders-first A-Z case-insensitive
#: ordering, recursive counts, and root/nested listings.
TREE_LORAS = [
    "Zeta/z.safetensors",
    "alpha/nested/deep.safetensors",
    "alpha/a.safetensors",
    "beta/b.safetensors",
    "Top.safetensors",
    "another-top.safetensors",
]
LIST_FOLDER_CASES = [
    (
        (TREE_LORAS, ""),
        {
            "folders": [
                {"name": "alpha", "count": 2, "path": "alpha"},
                {"name": "beta", "count": 1, "path": "beta"},
                {"name": "Zeta", "count": 1, "path": "Zeta"},
            ],
            "loras": [
                {"file": "another-top.safetensors", "label": "another-top.safetensors"},
                {"file": "Top.safetensors", "label": "Top.safetensors"},
            ],
        },
    ),
    (
        (TREE_LORAS, "alpha"),
        {
            "folders": [{"name": "nested", "count": 1, "path": "alpha/nested"}],
            "loras": [{"file": "alpha/a.safetensors", "label": "a.safetensors"}],
        },
    ),
    ((TREE_LORAS, "nope"), {"folders": [], "loras": []}),
]

#: (value, expected clampStrength(value) with the default fallback of 1).
CLAMP_CASES = [
    (0.5, 0.5),
    (-10, -10),
    (10, 10),
    (99, 10),
    (-99, -10),
    ("0.25", 0.25),
    ("junk", 1),
    (None, 1),
]

PROBE_JS = """
import * as m from './extensions/comfyui-epsnodes/lora_library/picker.js'

const out = {
  exports: {
    hasAttachPickerPanel: typeof m.attachPickerPanel === 'function',
    hasSelectionFromWidgetValue: typeof m.selectionFromWidgetValue === 'function',
    hasSerializeSelection: typeof m.serializeSelection === 'function',
    hasListFolder: typeof m.listFolder === 'function',
    hasClampStrength: typeof m.clampStrength === 'function',
    hasNormalizeLoraName: typeof m.normalizeLoraName === 'function'
  },
  constants: {
    classId: m.CLASS_ID,
    selectionWidgetName: m.SELECTION_WIDGET_NAME,
    route: m.ROUTE,
    routeFavorite: m.ROUTE_FAVORITE,
    routeRecent: m.ROUTE_RECENT,
    routeClearRecents: m.ROUTE_CLEAR_RECENTS
  },
  selectionFromWidgetValue: [%(raw_values)s].map((v) => m.selectionFromWidgetValue(v)),
  serializeSelection: %(serialize_inputs)s.map((sel) => m.serializeSelection(sel)),
  listFolder: %(list_folder_inputs)s.map(([loras, folder]) => m.listFolder(loras, folder)),
  clampStrength: %(clamp_inputs)s.map((v) => m.clampStrength(v)),
  normalizeLoraName: m.normalizeLoraName('a\\\\b\\\\c.safetensors')
}

process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def picker_api(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Runs the probe against the REAL picker.js in a served-layout tmp dir
    (see module docstring) and returns its JSON output."""
    layout = tmp_path_factory.mktemp("web_root")

    module_dir = layout / "extensions" / "comfyui-epsnodes" / "lora_library"
    module_dir.mkdir(parents=True)
    shutil.copyfile(PICKER_JS, module_dir / "picker.js")
    # picker.js imports `./api.js`, which imports `./version.js` and
    # `../../../scripts/api.js` -- copy the real siblings, stub the served
    # ComfyUI scripts exactly as test_checkpoint_switcher_js.py stubs them.
    shutil.copyfile(API_JS, module_dir / "api.js")
    shutil.copyfile(VERSION_JS, module_dir / "version.js")
    # ...and, since M2, `./pll_bridge.js` -- plus, since M4,
    # `./dasiwa_bridge.js` (each tested in its own right by
    # tests/test_pll_bridge_js.py / tests/test_dasiwa_bridge_js.py -- here
    # they only have to resolve).
    shutil.copyfile(BRIDGE_JS, module_dir / "pll_bridge.js")
    shutil.copyfile(DASIWA_JS, module_dir / "dasiwa_bridge.js")

    scripts = layout / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "api.js").write_text("export const api = { fetchApi: () => {} }\n", encoding="utf-8")
    (scripts / "app.js").write_text("export const app = {}\n", encoding="utf-8")

    probe = layout / "probe.mjs"
    probe.write_text(
        PROBE_JS
        % {
            "raw_values": ", ".join(js for js, _ in RAW_VALUE_CASES),
            "serialize_inputs": json.dumps([sel for sel, _ in SERIALIZE_CASES]),
            "list_folder_inputs": json.dumps([list(args) for args, _ in LIST_FOLDER_CASES]),
            "clamp_inputs": json.dumps([value for value, _ in CLAMP_CASES]),
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
    """Raw text of picker.js -- for SOURCE-STRUCTURE assertions about code
    that only runs inside attachPickerPanel() against a real litegraph node
    and a real DOM (the class gate, the restore-race fix, both hide flags,
    the ⚠ conventions, the token guards, the POST paths), which this repo
    has no browser harness to drive -- see the module docstring and
    test_checkpoint_switcher_js.py's identical convention."""
    return PICKER_JS.read_text(encoding="utf-8")


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


def test_picker_js_parses() -> None:
    """`node --check` -- the file must at minimum be valid ES module syntax."""
    result = subprocess.run(
        [NODE, "--check", str(PICKER_JS)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


def test_module_exports_the_entry_point_and_pure_helpers(picker_api: dict) -> None:
    """web/lora_library.js consumes attachPickerPanel(); the pure helpers
    are the seams this file's own probe drives directly."""
    assert picker_api["exports"] == {
        "hasAttachPickerPanel": True,
        "hasSelectionFromWidgetValue": True,
        "hasSerializeSelection": True,
        "hasListFolder": True,
        "hasClampStrength": True,
        "hasNormalizeLoraName": True,
    }


def test_class_id_widget_name_and_route_constants(picker_api: dict) -> None:
    """Pins the exact §5/§6.13 contract given to both the frontend and
    backend implementers -- the GET feed and the three POST routes -- so a
    typo on either side fails loudly instead of silently disagreeing."""
    constants = picker_api["constants"]
    assert constants["classId"] == "EPSLoraPicker"
    assert constants["selectionWidgetName"] == "selection"
    assert constants["route"] == "/lora_library/picker"
    assert constants["routeFavorite"] == "/lora_library/picker/favorite"
    assert constants["routeRecent"] == "/lora_library/picker/recent"
    assert constants["routeClearRecents"] == "/lora_library/picker/clear_recents"


def test_route_strings_appear_verbatim_in_the_source(source: str) -> None:
    assert "'/lora_library/picker'" in source
    assert "'/lora_library/picker/favorite'" in source
    assert "'/lora_library/picker/recent'" in source
    assert "'/lora_library/picker/clear_recents'" in source


def test_every_family_route_is_wired(source: str) -> None:
    """Supersedes M1's test_only_the_m1_routes_are_called: CLEAR_RECENTS
    graduated from declared-but-unused to wired (the M3 Clear-recents
    button), and the three M3 routes each have their one calling site --
    PREVIEW as an <img src> (the one GET that never goes through
    api.getJson), INFO via getJson, FAVORITES_ORDER/CLEAR_RECENTS via
    postJson."""
    assert "postJson(ROUTE_RECENT" in source
    assert "postJson(ROUTE_FAVORITE," in source  # the comma excludes FAVORITES_ORDER
    assert "postJson(ROUTE_CLEAR_RECENTS" in source
    assert "postJson(ROUTE_FAVORITES_ORDER" in source
    assert "getJson(ROUTE_INFO" in source
    assert "${ROUTE_PREVIEW}?file=${encodeURIComponent(file)}" in source


# --------------------------------------------------- selectionFromWidgetValue


def test_selection_from_widget_value_cases(picker_api: dict) -> None:
    pairs = zip(RAW_VALUE_CASES, picker_api["selectionFromWidgetValue"], strict=True)
    for (raw_js, expected), got in pairs:
        msg = f"selectionFromWidgetValue({raw_js}) -> {got!r}, wanted {expected!r}"
        assert got == expected, msg


def test_selection_from_widget_value_never_throws_on_malformed_input(picker_api: dict) -> None:
    """Every malformed/wrong-shaped case degrades to the EMPTY selection
    rather than the probe process crashing -- §6.13: bad JSON is a console
    warn + empty, never a failure."""
    assert all(got == EMPTY for got in picker_api["selectionFromWidgetValue"][:10])


def test_malformed_selection_warns_to_console(source: str) -> None:
    body = _function_body(source, "selectionFromWidgetValue(raw)")
    assert "api.warn('unparseable picker selection widget; treating as empty', error)" in body
    assert "api.warn('wrong-shaped picker selection widget; treating as empty')" in body


# ---------------------------------------------------------- serialization


def test_serialize_selection_stable_key_order(picker_api: dict) -> None:
    """Byte-exact pins: {"scope", "loras"} order, row key order, and
    strength_clip omitted when null (§6.13's write shape)."""
    pairs = zip(SERIALIZE_CASES, picker_api["serializeSelection"], strict=True)
    for (sel, expected), got in pairs:
        assert got == expected, f"serializeSelection({sel!r}) -> {got!r}, wanted {expected!r}"


def test_scope_and_loras_are_the_serialization_keys(source: str) -> None:
    body = _function_body(source, "serializeSelection(selection)")
    assert "JSON.stringify({ scope, loras })" in body


def test_every_mutation_writes_through_the_one_widget_writer(source: str) -> None:
    body = _function_body(source, "writeSelectionWidget(state)")
    assert "const json = serializeSelection(state.selection)" in body
    assert "state.widget.value = json" in body
    assert "state.widget.callback?.(json)" in body
    assert "state.node.graph?.setDirtyCanvas(true, true)" in body


# ---------------------------------------------------------------- tree derivation


def test_list_folder_cases(picker_api: dict) -> None:
    pairs = zip(LIST_FOLDER_CASES, picker_api["listFolder"], strict=True)
    for ((loras, folder), expected), got in pairs:
        msg = f"listFolder({loras!r}, {folder!r}) -> {got!r}, wanted {expected!r}"
        assert got == expected, msg


def test_backslashes_normalize_to_forward_slashes(picker_api: dict) -> None:
    assert picker_api["normalizeLoraName"] == "a/b/c.safetensors"


def test_clamp_strength_cases(picker_api: dict) -> None:
    pairs = zip(CLAMP_CASES, picker_api["clampStrength"], strict=True)
    for (value, expected), got in pairs:
        assert got == expected, f"clampStrength({value!r}) -> {got!r}, wanted {expected!r}"


# -------------------------------------------------------------- attach gating


def test_attach_gates_on_the_exact_class_id(source: str) -> None:
    body = _function_body(source, "attachPickerPanel(node)")
    assert "if (nodeClassOf(node) !== CLASS_ID) return" in body
    assert "export const CLASS_ID = 'EPSLoraPicker'" in source


def test_attach_guards_against_a_double_nodecreated(source: str) -> None:
    body = _function_body(source, "attachPickerPanel(node)")
    assert "if (attachedNodes.has(node)) return" in body
    assert "attachedNodes.add(node)" in body


def test_attach_never_throws(source: str) -> None:
    body = _function_body(source, "attachPickerPanel(node)")
    assert body.strip().startswith("try {")
    assert "} catch (error) {" in body
    assert "api.warn('attachPickerPanel failed', error)" in body
    assert "loadPicker(state).catch((error) => api.warn('initial picker load failed'," in body


# ---------------------------------------------------- Vue-nodes hide flags


def test_selection_widget_hidden_with_both_flags(source: str) -> None:
    """FORMAT.md §7.5: canvas reads `widget.hidden`, the Vue-nodes renderer
    reads `widget.options.hidden` and ignores the first outright -- both
    must be set or the widget leaks into a Vue node as a raw text field."""
    body = _function_body(source, "hideSelectionWidget(state)")
    assert "widget.hidden = true" in body
    assert "widget.options = { ...(widget.options || {}), hidden: true }" in body
    assert "hideSelectionWidget(state)" in _function_body(source, "attachPickerPanel(node)")


# -------------------------------------------------------- restore-correctness


def test_on_configure_is_chained_never_replaced(source: str) -> None:
    """`onConfigure` is the one hook that fires AFTER widgets_values are
    restored (whole-workflow load AND paste) -- this repo's most-burned
    frontend lesson (§7.2)."""
    assert "wireConfigureReload(state)" in _function_body(source, "attachPickerPanel(node)")
    body = _function_body(source, "wireConfigureReload(state)")
    assert "const originalOnConfigure = node.onConfigure" in body
    assert "node.onConfigure = function (info) {" in body
    assert "originalOnConfigure.apply(this, arguments)" in body
    assert "reloadFromWidget(state)" in body


def test_fetch_and_configure_both_reconcile_through_the_same_resync(source: str) -> None:
    """Whichever of {the initial fetch, onConfigure} finishes LAST produces
    the final render -- both re-derive `state.selection` from the widget's
    CURRENT value via the same function."""
    assert "reloadFromWidget(state)" in _function_body(source, "wireConfigureReload(state)")
    assert "reloadFromWidget(state)" in _function_body(source, "loadPicker(state)")
    reload_body = _function_body(source, "reloadFromWidget(state)")
    assert "selectionFromWidgetValue(state.widget.value)" in reload_body


# ------------------------------------------------------ token-guarded fetches


def test_fetch_is_token_guarded_against_slow_lan_responses(source: str) -> None:
    """A 600ms+ LAN response must never double-render or clobber a newer
    state -- checkpoint_switcher.js's loadToken idiom, plus the same guard
    on the favorite/recent POST round-trips."""
    body = _function_body(source, "loadPicker(state)")
    assert "const token = ++state.loadToken" in body
    assert "if (token !== state.loadToken) return" in body
    assert "const token = ++state.favoriteToken" in _function_body(
        source, "toggleFavorite(state, file, on)"
    )
    assert "const token = ++state.recentToken" in _function_body(
        source, "recordRecent(state, file)"
    )


# --------------------------------------------- error state / value preservation


def test_load_failure_is_a_status_line_error_with_retry(source: str) -> None:
    """§7.2 amendment (v0.52.1): the failed GET shows an error + Retry in
    the status line; the Selected section is never blanked by it."""
    status_body = _function_body(source, "renderStatus(state)")
    assert "state.error" in status_body
    assert "'Retry'" in status_body
    assert "loadPicker(state)" in status_body.split("'Retry'", 1)[1][:300]

    load_body = _function_body(source, "loadPicker(state)")
    assert "state.error = (error && error.message) || 'Failed to load loras'" in load_body


def test_selected_renders_from_the_widget_value_alone(source: str) -> None:
    """renderSelected() reads only `state.selection` (the widget mirror) --
    no served-list membership gate decides whether a row EXISTS, only how
    it is decorated."""
    body = _function_body(source, "renderSelected(state)")
    assert "state.selection.loras" in body
    assert "state.loraSet" not in body


def test_unconfirmed_selected_rows_get_the_warning_marker_but_stay_functional(
    source: str,
) -> None:
    """A selected row the served list can't confirm gets ⚠ + the explaining
    title, only once the list has actually LOADED, and its controls are
    never disabled."""
    body = _function_body(source, "buildSelectedRowEl(state, row)")
    assert "const missing = state.loaded && !state.loraSet.has(row.file)" in body
    assert "⚠" in body
    assert "not found on this machine's lora list" in body
    assert "disabled" not in body


# ---------------------------------------------------------- ghost favorites


def test_ghost_rows_are_dimmed_warned_and_star_only(source: str) -> None:
    """A favorite/recent naming a lora NOT on this machine's served list
    renders dimmed, ⚠-marked, star-only -- visible, never silently dropped
    (§6.13 / §6.12's ⚠ rule)."""
    body = _function_body(source, "buildLoraRowEl(state, file, displayLabel)")
    assert "const ghost = !state.loraSet.has(file)" in body
    assert "⚠" in body
    assert "not installed here" in body
    assert "if (!ghost) {" in body  # the Add button exists only on real rows
    assert "eps-lp-row-ghost" in body


def test_favorite_toggle_is_optimistic_with_revert_and_toast(source: str) -> None:
    body = _function_body(source, "toggleFavorite(state, file, on)")
    assert "const previous = state.favorites.slice()" in body
    assert "state.favorites = previous" in body
    assert "toast('error'," in body


def test_recent_post_is_fire_and_forget(source: str) -> None:
    """The recents stamp must never block or fail an Add -- failure is a
    console warn only (§6.13)."""
    body = _function_body(source, "recordRecent(state, file)")
    expected = ".catch((error) => api.warn('recording recent lora failed (non-blocking)', error))"
    assert expected in body


def test_add_appends_or_flashes_the_existing_row(source: str) -> None:
    body = _function_body(source, "addLora(state, file)")
    assert "flashSelectedRow(state, file)" in body
    expected = "state.selection.loras.push({ file, on: true, strength: 1, strength_clip: null })"
    assert expected in body
    assert "recordRecent(state, file)" in body


# ---------------------------------------------------- window-listener safety


def test_no_window_level_listeners_at_all(source: str) -> None:
    """§7.5: window-level gesture listeners must be capture-phase in Vue
    mode -- this file sidesteps the hazard entirely by adding NONE (every
    interaction is element-level), and the phrase must not appear even in a
    comment so this pin stays trivially auditable."""
    assert "window.addEventListener" not in source


def test_strength_input_keydown_stops_propagation(source: str) -> None:
    """Canvas hotkeys (Delete/Ctrl+C/etc.) must not intercept typing in the
    strength field -- checkpoint_switcher.js's filter input convention."""
    body = _function_body(source, "buildSelectedRowEl(state, row)")
    assert "addEventListener('keydown', (event) => event.stopPropagation())" in body


def test_strength_commits_on_change_and_blur_with_clamping(source: str) -> None:
    body = _function_body(source, "buildSelectedRowEl(state, row)")
    assert "clampStrength(strength.value, row.strength)" in body
    assert "strength.addEventListener('change', commitStrength)" in body
    assert "strength.addEventListener('blur', commitStrength)" in body


# -------------------------------------------------------------- width floor


def test_min_node_width_floor_is_360(source: str) -> None:
    assert "const MIN_NODE_WIDTH = 360" in source
    assert "installMinWidth(state.node, MIN_NODE_WIDTH)" in source


# ----------------------------------------------------------- extension entry


def test_entry_file_wires_the_picker_into_nodecreated(source: str) -> None:
    """The lora_library.js hookup this panel ships with -- the import and
    the safely() call inside nodeCreated, in the entry file's own style."""
    entry = ENTRY_JS.read_text(encoding="utf-8")
    assert "import * as picker from './lora_library/picker.js'" in entry
    assert "safely('picker.attachPickerPanel', () => picker.attachPickerPanel(node))" in entry


class TestReviewFixes20260809:
    """Source pins for the 2026-08-09 review fixes: the mid-typing strength
    commit and api.js's ok-but-not-JSON failure (both un-drivable without a
    browser harness, same convention as the rest of this file)."""

    def test_strength_input_exposes_a_commit_hook(self, source: str) -> None:
        assert "strength._epsCommit = commitStrength" in source

    def test_reload_from_widget_commits_before_the_reparse(self, source: str) -> None:
        body = _function_body(source, "reloadFromWidget(state)")
        commit = body.index("commitActiveStrengthEdit(state)")
        reparse = body.index("selectionFromWidgetValue(state.widget.value)")
        assert commit < reparse, "the commit must land in the widget BEFORE the re-parse"

    def test_render_also_commits_first(self, source: str) -> None:
        body = _function_body(source, "render(state)")
        assert body.index("commitActiveStrengthEdit(state)") < body.index("renderSelected(state)")

    def test_api_js_treats_ok_but_unparseable_as_failure(self) -> None:
        api_source = API_JS.read_text(encoding="utf-8")
        assert "server returned a response that is not JSON" in api_source
        # The throw must be gated on response.ok having passed (a non-ok
        # response keeps its status-derived message).
        assert api_source.index("HTTP ${response.status}") < api_source.index(
            "server returned a response that is not JSON"
        )


# ------------------------------------------------- §6.13 M2: Send to loader


class TestSendToLoaderM2:
    """Source pins for the M2 Send-to-loader row (§6.13): render-time probe
    gating, the id-keyed transient target, the re-probe on click, and the
    §6.3-vocabulary surfaces -- all closure-bound against a live graph, so
    source-pinned like the rest of this file. pll_bridge.js itself (the
    probe/grow/shrink/assign technique and the vocabulary contract against
    controller.js) is covered by tests/test_pll_bridge_js.py."""

    def test_send_row_renders_between_selected_and_browser(self, source: str) -> None:
        assert "state.sendRowEl = el('div', { className: 'eps-lp-send' })" in source
        build = _function_body(source, "buildUi(state)")
        selected = build.index("state.selectedListEl,")
        send = build.index("state.sendRowEl,")
        browser = build.index("browser,")
        assert selected < send < browser, "root order must be Selected -> Send -> browser"

    def test_send_row_rebuilt_on_render_and_on_every_selection_write(self, source: str) -> None:
        """The full render rebuilds it; granular mutations (Add, remove)
        skip the full render, so the one widget-write funnel also
        refreshes the empty-selection gate."""
        assert "renderSend(state)" in _function_body(source, "render(state)")
        assert "renderSend(state)" in _function_body(source, "writeSelectionWidget(state)")

    def test_select_class_option_labels_and_id_values(self, source: str) -> None:
        body = _function_body(source, "renderSend(state)")
        assert "className: 'eps-lp-pll-select'" in body
        assert "`${node.title || node.type} #${node.id}`" in body
        assert "value: String(node.id)" in body

    def test_previous_target_reselected_by_id_when_still_present(self, source: str) -> None:
        body = _function_body(source, "renderSend(state)")
        assert "fresh.some((node) => String(node.id) === state.pllTargetId)" in body
        assert "select.value = state.pllTargetId" in body

    def test_vanished_target_stays_on_placeholder_never_first_option(self, source: str) -> None:
        # Review 2026-08-09: a deleted target must NOT silently retarget to
        # the first PLL -- Send would destructively overwrite a loader the
        # user never chose. The placeholder option makes "no choice"
        # representable; the never-guess branch keeps it selected.
        body = _function_body(source, "renderSend(state)")
        assert "'Pick a loader…'" in body
        assert "select.value = ''" in body
        assert "never guess" in body.lower() or "NEVER fall through" in body

    def test_single_candidate_auto_adopt_gated_on_no_choice(self, source: str) -> None:
        body = _function_body(source, "renderSend(state)")
        assert "state.pllTargetId == null && fresh.length === 1" in body

    def test_options_rebuilt_on_mousedown_and_change_reprobes(self, source: str) -> None:
        # Review 2026-08-09: candidates recompute on open (controller.js's
        # values-function pattern) and a target switch re-probes.
        body = _function_body(source, "renderSend(state)")
        assert "select.addEventListener('mousedown', buildOptions)" in body
        change = body[body.index("select.addEventListener('change'"):]
        assert "renderSend(state)" in change

    def test_probe_failure_blocks_but_does_not_disable_send(self, source: str) -> None:
        # Review 2026-08-09: a DISABLED button can never run the click-time
        # re-probe, so a graph fixed after render would have no recovery
        # path. Probe failure marks the button blocked (styled, message in
        # title + status span) while the click stays live and re-checks.
        body = _function_body(source, "renderSend(state)")
        # M4: the probe is registry-dispatched (family-agnostic null legs in
        # probeSendTarget, per-family gates in the bridges) -- same render-
        # time probe, same blocked-not-disabled posture.
        assert "const probe = probeSendTarget(resolveSendTarget(state))" in body
        gated = body[body.index("if (!probe.ok)"):]
        assert "sendBtn.classList.add('eps-lp-btn-blocked')" in gated
        assert "sendBtn.disabled = true" not in gated[:gated.index("} else if")]
        assert "sendBtn.title = probe.message" in gated
        assert "statusEl.textContent = probe.message" in gated

    def test_send_click_refuses_an_empty_selection_instead_of_truncating(self, source: str) -> None:
        # Reachable via the blocked-not-disabled button: an empty send would
        # SHRINK the target loader to zero rows.
        body = _function_body(source, "sendToPll(state)")
        assert "rows.length === 0" in body
        assert "never truncate" in body

    def test_send_click_adopts_a_lone_late_added_pll(self, source: str) -> None:
        body = _function_body(source, "sendToPll(state)")
        assert "candidates.length === 1" in body
        assert "state.pllTargetId = String(candidates[0].id)" in body

    def test_empty_selection_disables_send_with_its_own_title(self, source: str) -> None:
        body = _function_body(source, "renderSend(state)")
        assert "state.selection.loras.length === 0" in body
        assert "sendBtn.title = 'Nothing selected'" in body

    def test_click_reprobes_then_sends_all_rows(self, source: str) -> None:
        """The target may have been deleted since render -- the click
        re-resolves by id, re-probes, and toasts the failure; success
        writes ALL rows (`on` preserved, §6.13) through the target's own
        family adapter (M4: registry-dispatched, was pll.writeRowsToPll)."""
        body = _function_body(source, "sendToPll(state)")
        assert "const node = resolveSendTarget(state)" in body
        assert "const probe = probeSendTarget(node)" in body
        assert "toast('error', 'EPS LoRA Picker', probe.message)" in body
        assert "const rows = state.selection.loras" in body
        assert "SEND_ADAPTERS[node.type].write(node, rows)" in body

    def test_send_success_toast_names_count_and_target(self, source: str) -> None:
        expected = "`Sent ${rows.length} lora(s) to ${node.title || node.type} #${node.id}`"
        assert expected in _function_body(source, "sendToPll(state)")

    def test_select_keydown_stops_propagation(self, source: str) -> None:
        body = _function_body(source, "renderSend(state)")
        assert "select.addEventListener('keydown', (event) => event.stopPropagation())" in body

    def test_target_choice_is_transient_and_never_serialized(self, source: str) -> None:
        """§6.13 M2 adds no widget: the target id lives in panel state only,
        and the Send row never touches the selection JSON."""
        assert "pllTargetId: null" in _function_body(source, "createState(node, widget)")
        assert "pllTargetId" not in _function_body(source, "serializeSelection(selection)")
        assert "writeSelectionWidget" not in _function_body(source, "renderSend(state)")
        assert "writeSelectionWidget" not in _function_body(source, "sendToPll(state)")

    def test_still_no_window_listeners(self, source: str) -> None:
        assert "window.addEventListener" not in source


# ------------------------------------------- §6.13 M3: search / thumbs / copy /
# clear-recents / favorites drag-reorder

#: (relPath, query, expected) -- loraMatchesSearch's §7.2 semantics: AND of
#: whitespace-separated words, case-insensitive, matched against the path
#: RELATIVE to the current scope (the caller strips the scope prefix).
M3_SEARCH_CASES = [
    ("subdir/MyLora.safetensors", "mylora", True),  # case-insensitive
    ("subdir/MyLora.safetensors", "MYLORA", True),
    ("subdir/MyLora.safetensors", "sub lora", True),  # AND: both words present
    ("subdir/MyLora.safetensors", "lora sub", True),  # ...order-independent
    ("subdir/MyLora.safetensors", "sub missing", False),  # AND: one word absent
    ("subdir/MyLora.safetensors", "dir/my", True),  # a word may span a / boundary
    ("subdir/MyLora.safetensors", "", True),  # empty query matches (the UI gates it)
    ("subdir/MyLora.safetensors", "   ", True),  # whitespace-only likewise
    # Scope-relativity is the CALLER's job: the matcher sees ONLY the
    # relative path, so once "alpha/" is stripped as the scope prefix the
    # scope folder's own name must NOT match a lora inside it.
    ("deep.safetensors", "alpha", False),
]

M3_PROBE_JS = """
import * as m from './extensions/comfyui-epsnodes/lora_library/picker.js'

const out = {
  hasLoraMatchesSearch: typeof m.loraMatchesSearch === 'function',
  routePreview: m.ROUTE_PREVIEW,
  routeInfo: m.ROUTE_INFO,
  routeFavoritesOrder: m.ROUTE_FAVORITES_ORDER,
  search: %(search_inputs)s.map(([rel, query]) => m.loraMatchesSearch(rel, query))
}

process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def m3_api(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """The M3 probe against the REAL picker.js in its own served-layout copy
    (the picker_api fixture's exact recipe) -- separate because that probe
    predates M3 and stays untouched. Module-scope like its sibling (a
    class-scoped instance-method fixture is a PytestRemovedIn10Warning)."""
    layout = tmp_path_factory.mktemp("web_root_m3")
    module_dir = layout / "extensions" / "comfyui-epsnodes" / "lora_library"
    module_dir.mkdir(parents=True)
    shutil.copyfile(PICKER_JS, module_dir / "picker.js")
    shutil.copyfile(API_JS, module_dir / "api.js")
    shutil.copyfile(VERSION_JS, module_dir / "version.js")
    shutil.copyfile(BRIDGE_JS, module_dir / "pll_bridge.js")
    shutil.copyfile(DASIWA_JS, module_dir / "dasiwa_bridge.js")

    scripts = layout / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "api.js").write_text("export const api = { fetchApi: () => {} }\n", encoding="utf-8")
    (scripts / "app.js").write_text("export const app = {}\n", encoding="utf-8")

    probe = layout / "probe.mjs"
    probe.write_text(
        M3_PROBE_JS
        % {"search_inputs": json.dumps([[rel, query] for rel, query, _ in M3_SEARCH_CASES])},
        encoding="utf-8",
    )
    result = subprocess.run(
        [NODE, str(probe)], capture_output=True, text=True, timeout=60, cwd=layout
    )
    assert result.returncode == 0, f"M3 probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


class TestM3:
    """§6.13 M3 pins: the search field (§7.2 semantics), sidecar preview
    thumbnails, trigger-word copy, the armed Clear-recents button, and the
    favorites drag-reorder. Pure logic (loraMatchesSearch + the new route
    constants) is Node-driven via the m3_api probe above; everything
    closure-bound is source-pinned, the file's standing convention."""

    # ------------------------------------------------------------ routes

    def test_m3_route_constants(self, m3_api: dict) -> None:
        """The three M3 rows of routes_lora_picker.py, spelled exactly."""
        assert m3_api["routePreview"] == "/lora_library/picker/preview"
        assert m3_api["routeInfo"] == "/lora_library/picker/info"
        assert m3_api["routeFavoritesOrder"] == "/lora_library/picker/favorites_order"

    # ------------------------------------------------------------ search

    def test_lora_matches_search_truth_table(self, m3_api: dict) -> None:
        assert m3_api["hasLoraMatchesSearch"] is True
        pairs = zip(M3_SEARCH_CASES, m3_api["search"], strict=True)
        for (rel, query, expected), got in pairs:
            msg = f"loraMatchesSearch({rel!r}, {query!r}) -> {got!r}, wanted {expected!r}"
            assert got is expected, msg

    def test_search_input_sits_below_the_breadcrumb(self, source: str) -> None:
        """Owner ask 2026-08-14: the search field moved one section down,
        under the breadcrumb -- and its placeholder tracks the browse
        folder (set in renderBrowser), since the folder IS the corpus."""
        body = _function_body(source, "buildUi(state)")
        assert "placeholder: 'Search loras…'" in body
        assert "[\n    state.crumbsEl,\n    state.searchInputEl,\n    state.listEl\n  ]" in body
        browser = _function_body(source, "renderBrowser(state)")
        assert "state.searchInputEl.placeholder = folder ? `Search ${basename(folder)}…` : 'Search loras…'" in browser

    def test_search_keydown_stops_propagation_and_escape_clears_in_place(
        self, source: str
    ) -> None:
        """§7.5's keydown rule on the new input, and §7.2's Escape-clears --
        both inside the search input's own keydown listener."""
        body = _function_body(source, "buildUi(state)")
        keydown = body[body.index("state.searchInputEl.addEventListener('keydown'") :]
        keydown = keydown[: keydown.index("})")]
        assert "event.stopPropagation()" in keydown
        assert "'Escape'" in keydown
        assert "clearSearch(state)" in keydown

    def test_active_search_hides_folders_and_pseudo_rows(self, source: str) -> None:
        """The search short-circuit returns BEFORE the ★/🕘 pseudo-folder
        appends and the folder listing, and the flat results view builds
        lora rows only."""
        body = _function_body(source, "renderBrowser(state)")
        short_circuit = body.index("renderSearchResults(state, query)")
        assert short_circuit < body.index("state.view === 'favorites'")
        assert short_circuit < body.index("buildPseudoFolderRowEl")
        assert short_circuit < body.index("buildFolderRowEl")
        results = _function_body(source, "renderSearchResults(state, query)")
        assert "buildFolderRowEl" not in results
        assert "buildPseudoFolderRowEl" not in results
        assert "buildLoraRowEl(state, match.file, match.label)" in results

    def test_search_matches_and_labels_are_folder_relative(self, source: str) -> None:
        """Owner ask 2026-08-14: search covers the current browse FOLDER
        (scope + drill-down), not the whole scope -- whole library only at
        the library root."""
        results = _function_body(source, "renderSearchResults(state, query)")
        assert "const folder = currentFolder(state)" in results
        assert "const prefix = folder ? `${folder}/` : ''" in results
        assert "name.slice(prefix.length)" in results
        assert "loraMatchesSearch(rel, query)" in results
        assert "matches.push({ file: name, label: rel })" in results

    def test_zero_matches_renders_the_no_loras_match_row(self, source: str) -> None:
        assert "No loras match" in _function_body(source, "renderSearchResults(state, query)")

    def test_navigation_clears_the_search_the_pinned_choice(self, source: str) -> None:
        """§6.13 M3 allowed either behavior; PINNED: breadcrumb navigation,
        a scope change, and the restore resync all clear the query."""
        crumbs = _function_body(source, "renderCrumbs(state)")
        assert crumbs.count("clearSearch(state)") == 2  # root crumb + tail crumbs
        assert "clearSearch(state)" in _function_body(source, "setScope(state, scopePath)")
        assert "clearSearch(state)" in _function_body(source, "reloadFromWidget(state)")

    # -------------------------------------------------------- thumbnails

    def test_thumb_img_lazy_loading_and_error_hide(self, source: str) -> None:
        """ROUTE_PREVIEW as an <img src> with the encoded file, lazy-loaded;
        the error listener hides the img because a 404 (no sidecar image)
        is the NORMAL case."""
        body = _function_body(source, "buildLoraRowEl(state, file, displayLabel)")
        assert "className: 'eps-lp-thumb'" in body
        # Through api.apiUrl, not a hardcoded root-absolute path (review
        # 2026-08-09) -- see TestM3ReviewFixes for the rationale pin.
        assert "src: api.apiUrl(`${ROUTE_PREVIEW}?file=${encodeURIComponent(file)}`)" in body
        assert "loading: 'lazy'" in body
        error_leg = body[body.index("thumb.addEventListener('error'") :]
        assert "thumb.style.display = 'none'" in error_leg[:200]

    def test_thumb_is_a_fixed_square_that_never_stretches(self, source: str) -> None:
        expected = ".eps-lp-thumb { flex: 0 0 auto; width: 26px; height: 26px; object-fit: cover;"
        assert expected in source

    # -------------------------------------------------- trigger-word copy

    def test_copy_fetches_info_on_demand_and_toasts_the_no_sidecar_case(
        self, source: str
    ) -> None:
        body = _function_body(source, "copyTriggerWords(state, file)")
        assert "api.getJson(ROUTE_INFO, { file })" in body
        expected = (
            "toast('info', 'EPS LoRA Picker', "
            "`No trigger-word sidecar for ${basename(file)}`)"
        )
        assert expected in body

    def test_clipboard_writetext_with_the_image_grid_execcommand_fallback(
        self, source: str
    ) -> None:
        """navigator.clipboard.writeText when available, else the hidden-
        textarea execCommand('copy') degrade -- image_grid.js's
        copyTextViaExecCommand technique, and the source must cite it."""
        body = _function_body(source, "copyTextToClipboard(text)")
        assert "navigator.clipboard?.writeText" in body
        assert "document.execCommand('copy')" in body
        assert "document.createElement('textarea')" in body
        assert "image_grid.js" in source

    def test_success_toast_names_the_words_and_truncates(self, source: str) -> None:
        assert "const COPY_TOAST_MAX_CHARS = 120" in source
        body = _function_body(source, "copyTriggerWords(state, file)")
        assert "words.length > COPY_TOAST_MAX_CHARS" in body
        assert "toast('success', 'EPS LoRA Picker', `Copied: ${shown}`)" in body

    # ------------------------------------------------------ clear recents

    def test_clear_recents_is_armed_two_click_with_both_strings(self, source: str) -> None:
        """controller.js's armed-delete pattern: first click arms ("Really
        clear?"), auto-disarm on timeout (4s) -- and on any re-render, since
        the button is rebuilt fresh -- second click POSTs."""
        body = _function_body(source, "buildClearRecentsRowEl(state)")
        assert "text: 'Clear recents'" in body
        assert "button.textContent = 'Really clear?'" in body
        assert "button._armed = true" in body
        assert "button._armTimer = setTimeout(" in body
        assert "const CLEAR_RECENTS_CONFIRM_MS = 4000" in source

    def test_clear_recents_lives_in_the_recent_view_only(self, source: str) -> None:
        body = _function_body(source, "renderBrowser(state)")
        recent = body.index("state.view === 'recent'")
        clear = body.index("buildClearRecentsRowEl(state)")
        pseudo = body.index("if (state.path.length === 0)")
        assert recent < clear < pseudo, "the Clear-recents row must sit inside the recent branch"
        assert body.count("buildClearRecentsRowEl") == 1

    def test_clear_recents_posts_and_resyncs_from_the_response(self, source: str) -> None:
        body = _function_body(source, "clearRecents(state)")
        assert "api.postJson(ROUTE_CLEAR_RECENTS, {})" in body
        assert "state.recents = sanitizeRecents(data?.recents)" in body
        assert "toast('success'" in body
        assert "toast('error'" in body

    # ------------------------------------------- favorites drag-reorder

    def test_drag_gate_is_favorites_view_not_searching_not_ghost(self, source: str) -> None:
        body = _function_body(source, "buildLoraRowEl(state, file, displayLabel)")
        expected = (
            "const draggable = "
            "!ghost && state.view === 'favorites' && activeSearchQuery(state) === ''"
        )
        assert expected in body
        handle_leg = body[body.index("if (draggable) {") :]
        assert "eps-lp-drag-handle" in handle_leg[:300]
        assert "wireFavoriteDrag(state, handle, file)" in handle_leg[:400]

    def test_drag_uses_pointer_capture_with_element_level_listeners(self, source: str) -> None:
        """The §7.5-safe shape: setPointerCapture retargets the gesture at
        the handle, so its OWN element-level move/up/cancel listeners see
        the whole drag -- no window involvement anywhere."""
        body = _function_body(source, "wireFavoriteDrag(state, handle, file)")
        assert "handle.setPointerCapture(event.pointerId)" in body
        assert "handle.addEventListener('pointermove', onMove)" in body
        assert "handle.addEventListener('pointerup', onUp)" in body
        assert "handle.addEventListener('pointercancel', onCancel)" in body
        assert "handle.releasePointerCapture(drag.pointerId)" in body

    def test_still_zero_window_listeners_after_m3(self, source: str) -> None:
        assert "window.addEventListener" not in source

    def test_dragged_element_is_never_reparented_mid_drag(self, source: str) -> None:
        """Found live on the rig (2026-08-09): `insertBefore` on the dragged
        row itself is a remove+re-insert, and removing the captured handle's
        ancestor implicitly RELEASES pointer capture -- the gesture died on
        the first reorder. The live reorder must move the OTHER rows around
        the stationary dragged element instead."""
        body = _function_body(source, "moveDraggedFavorite(state, drag, clientY)")
        assert "insertBefore(dragged.el" not in body
        assert "insertBefore(row.el, dragged.el)" in body
        assert "anchor.after(row.el)" in body
        assert "IMPLICITLY RELEASES pointer capture" in source

    def test_reorder_posts_the_full_order_and_resyncs_from_the_response(
        self, source: str
    ) -> None:
        """The POST carries the FULL new order, ghosts included (review
        2026-08-09 -- see TestM3ReviewFixes for the teleport rationale);
        the response's authoritative full list re-syncs local state;
        failure reverts + toasts (toggleFavorite's posture)."""
        body = _function_body(source, "finishFavoriteDrag(state, drag)")
        assert "api.postJson(ROUTE_FAVORITES_ORDER, { files: reordered })" in body
        resync = body[body.index("Array.isArray(data?.favorites)") :]
        assert "state.favorites = data.favorites" in resync[:200]
        assert "state.favorites = previous" in body
        assert "toast('error'" in body

    def test_ghosts_stay_in_the_local_order_and_are_never_sent(self, source: str) -> None:
        """`state.favorites` takes the FULL dragged order (ghosts included,
        in their dragged slots) -- only the POSTed `files` filters them
        out, and the response re-sync is what re-homes them."""
        body = _function_body(source, "finishFavoriteDrag(state, drag)")
        assert "const reordered = state.favRowEls.map((row) => row.file)" in body
        assert "state.favorites = reordered" in body


class TestM3ReviewFixes:
    """Pins for the M3 review round's three fixes (2026-08-09)."""

    def test_reorder_posts_the_full_list_ghosts_included(self, source: str) -> None:
        # Filtering ghosts out made the route's missing-names-append rule
        # teleport them to the end of the shared order on every reorder.
        body = _function_body(source, "finishFavoriteDrag(state, drag)")
        assert "{ files: reordered }" in body
        assert ".filter((file) => state.loraSet.has(file))" not in body

    def test_thumbnail_src_goes_through_the_api_base(self, source: str) -> None:
        # A hardcoded root-absolute img src breaks behind a reverse-proxy
        # prefix while every fetchApi call keeps working (image_grid.js's
        # own img-src precedent).
        assert "api.apiUrl(`${ROUTE_PREVIEW}?file=${encodeURIComponent(file)}`)" in source
        api_source = API_JS.read_text(encoding="utf-8")
        assert "export function apiUrl(path)" in api_source
        assert "api.apiURL" in api_source

    def test_every_mutation_success_invalidates_in_flight_loads(self, source: str) -> None:
        # A GET computed server-side BEFORE a confirmed mutation must not
        # land afterwards and visibly revert it.
        assert "function invalidateInFlightLoad(state)" in source
        for fn in (
            "recordRecent(state, file)",
            "toggleFavorite(state, file, on)",
            "clearRecents(state)",
            "finishFavoriteDrag(state, drag)",
        ):
            assert "invalidateInFlightLoad(state)" in _function_body(source, fn), fn


# --------------------------------------- §6.13 M4: send-adapter TARGET REGISTRY


def _send_adapters_block(source: str) -> str:
    """The SEND_ADAPTERS object literal, up to its closing brace at column 0."""
    start = source.index("const SEND_ADAPTERS = {")
    end = source.index("\n}\n", start)
    return source[start:end]


class TestSendRegistryM4:
    """§6.13 M4 pins: the Send row's candidates come from a comfyClass-keyed
    adapter registry (Lora-Manager's NODE_EXTRACTORS pattern) with two
    entries -- the M2 rgthree adapter (pll_bridge.js, unchanged) and
    dasiwa_bridge.js -- merged ascending id ACROSS families; the
    no-target-in-graph message names both loaders; single-candidate
    auto-adopt spans both families in the render AND click paths; a lossy
    DaSiWa send appends loud notes to the success toast. Every M2
    never-guess pin above still passes against the registry-driven code --
    only its dispatch literals changed. dasiwa_bridge.js itself is covered
    by tests/test_dasiwa_bridge_js.py."""

    def test_registry_maps_both_loader_types(self, source: str) -> None:
        """comfyClass-keyed: each bridge's exported type constant is the key,
        so the registry can never drift from what find() returns."""
        assert "import * as dasiwa from './dasiwa_bridge.js'" in source
        registry = _send_adapters_block(source)
        assert "[pll.POWER_LORA_LOADER_TYPE]: {" in registry
        assert "[dasiwa.DASIWA_LOADER_TYPE]: {" in registry

    def test_registry_entries_carry_the_full_adapter_surface(self, source: str) -> None:
        """Both entries: {label, find, probe, write} -- the whole per-family
        surface the row plumbing dispatches through."""
        registry = _send_adapters_block(source)
        for key in ("label:", "find:", "probe:", "write:"):
            assert registry.count(key) == 2, key
        assert "find: pll.findPllNodes" in registry
        assert "write: pll.writeRowsToPll" in registry
        assert "find: dasiwa.findDasiwaNodes" in registry
        assert "write: dasiwa.writeRowsToDasiwa" in registry

    def test_candidates_merge_across_families_ascending_id(self, source: str) -> None:
        body = _function_body(source, "findSendCandidates()")
        assert "Object.values(SEND_ADAPTERS)" in body
        assert "adapter.find()" in body
        assert "sort((a, b) => a.id - b.id)" in body

    def test_no_target_message_is_family_agnostic_naming_both_loaders(self, source: str) -> None:
        """The M2 message shape, composed from every adapter's label -- so it
        names 'Power Lora Loader (rgthree)' and 'DaSiWa Advanced LoRA
        Loader' today and any third family automatically."""
        registry = _send_adapters_block(source)
        assert "label: 'Power Lora Loader (rgthree)'" in registry
        assert "label: 'DaSiWa Advanced LoRA Loader'" in registry
        assert ".map((adapter) => adapter.label)" in source
        assert "Optional — the picker applies its loras itself" in source
        assert "node, then pick it here.`" in source

    def test_probe_dispatches_by_comfy_class_with_family_agnostic_null_legs(
        self, source: str
    ) -> None:
        """M2's two null-target codes now span both families; a real
        candidate gets its own family's §6.3-style probe."""
        body = _function_body(source, "probeSendTarget(node)")
        assert "SEND_ADAPTERS[node.type]" in body
        assert "'no-target-selected'" in body
        assert "'no-target-in-graph'" in body
        assert "adapter.probe(node)" in body

    def test_target_resolution_spans_both_families(self, source: str) -> None:
        body = _function_body(source, "resolveSendTarget(state)")
        assert "findSendCandidates().find((node) => String(node.id) === state.pllTargetId)" in body

    def test_auto_adopt_universe_is_cross_family_in_render_and_click_paths(
        self, source: str
    ) -> None:
        """§6.13 M4: "single-candidate auto-adopt means a single candidate
        ACROSS both families" -- both the combo rebuild and the click-time
        late-adopt draw from the merged candidate list."""
        render = _function_body(source, "renderSend(state)")
        assert "const fresh = findSendCandidates()" in render
        send = _function_body(source, "sendToPll(state)")
        assert "const candidates = findSendCandidates()" in send

    def test_dasiwa_send_toast_appends_the_loud_lossy_notes(self, source: str) -> None:
        """Success toast unchanged for rgthree (its write returns nothing);
        a DaSiWa write's {flattened, clamped} basenames append the two owner-
        decided notes -- lossy edges are loud, never silent."""
        body = _function_body(source, "sendToPll(state)")
        flattened_note = (
            "model strength used for ${result.flattened.length} row(s) "
            "with a different clip strength: ${result.flattened.join(', ')}"
        )
        assert flattened_note in body
        assert "clamped to ±5: ${result.clamped.join(', ')}" in body
        assert "notes.length ? `${sent} — ${notes.join('; ')}` : sent" in body
        assert "`Sent ${rows.length} lora(s) to ${node.title || node.type} #${node.id}`" in body

    def test_registry_never_touches_the_selection_widget(self, source: str) -> None:
        """M4 adds no persistence: the registry plumbing stays out of the
        selection JSON exactly as M2's transient-target rule demands."""
        for fn in ("findSendCandidates()", "probeSendTarget(node)", "resolveSendTarget(state)"):
            assert "writeSelectionWidget" not in _function_body(source, fn), fn

    def test_still_no_window_listeners_after_m4(self, source: str) -> None:
        assert "window.addEventListener" not in source


class TestUiRound20260814:
    """Owner UI round 2026-08-14 (v0.61.1): the standalone scope bar is
    gone (clear-scope lives in the breadcrumb row), the standing lora
    total moved to the `library_loras` property + root-crumb tooltip
    (status bar collapses once loaded), and the Selected list always
    shows every row -- the NODE grows instead of the list cropping."""

    @pytest.fixture(scope="class")
    def source(self) -> str:
        return PICKER_JS.read_text(encoding="utf-8")

    def test_scope_bar_is_gone_and_clear_scope_moved_into_the_crumbs(self, source: str) -> None:
        assert "renderScope" not in source
        assert "scopeRowEl" not in source
        crumbs = _function_body(source, "renderCrumbs(state)")
        assert "Clear scope — browse the whole library again" in crumbs
        assert "setScope(state, '')" in crumbs

    def test_status_bar_collapses_once_loaded_and_count_moves_to_property(self, source: str) -> None:
        status = _function_body(source, "renderStatus(state)")
        assert "eps-lp-status-hidden" in status
        assert "loras on this machine" not in status  # the standing total is gone from the bar
        load = _function_body(source, "async function loadPicker(state)".replace("async function ", ""))
        assert "state.node.properties.library_loras = state.loras.length" in source
        assert "node.addProperty('library_loras', 0, 'number')" in source
        # ...but the total is still discoverable in the root crumb's tooltip
        assert "loras on this machine" in _function_body(source, "renderCrumbs(state)")
        assert load is not None

    def test_selected_list_grows_the_node_instead_of_cropping(self, source: str) -> None:
        assert "max-height: 132px" not in source
        grow = _function_body(source, "syncSelectedGrowth(state)")
        assert "(count - previous) * SELECTED_ROW_PX" in grow
        assert "Math.max(node.size[1] + delta, floor)" in grow
        assert "syncSelectedGrowth(state)" in _function_body(source, "renderSelected(state)")
        # the widget floor agrees, so a manual shrink can't crop either
        assert "MIN_WIDGET_HEIGHT + state.selection.loras.length * SELECTED_ROW_PX" in source

    def test_send_row_says_it_is_optional(self, source: str) -> None:
        send = _function_body(source, "renderSend(state)")
        assert "text: 'Send to'" in send
        assert "The picker itself already applies its loras" in send
