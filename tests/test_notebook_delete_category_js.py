"""The Prompt Notebook's DELETE SECTION HEADER path (FORMAT.md §3.4 Delete
category / §7.2, owner report 2026-08-25: "You can't delete section headers
in the lora library").

Delete used to be entry-only and disabled outright in category mode, and
``remove_entry`` deliberately leaves an emptied ``# heading`` in place
(§3.4, "categories are the user's prose, not derived state") — so a header
whose entries were gone could never be removed from the panel at all. Delete
is now CONTEXTUAL, the mirror of Save/``performSaveCategory``: with a header
active it deletes THAT HEADER over §5 ``/delete_category`` and never reads
the entry selection. It is non-destructive by construction — the heading
line goes and everything it held merges into the block above — which is the
whole reason a two-click confirm is enough.

Two layers, the sibling files' conventions: a Node probe of the PURE
exported wording helpers (the served-layout fixture from
``test_notebook_restore_js.py``/``test_m3_pinning_js.py``), then source-text
pins for the closure-bound button wiring. Live behavior is verified on the
rig; the backend halves are covered in ``test_markdown_store.py``
(``TestDeleteCategory``) and ``test_routes_notebook.py``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB = REPO_ROOT / "web"
NOTEBOOK_JS = WEB / "lora_library" / "notebook.js"
API_JS = WEB / "lora_library" / "api.js"
VERSION_JS = WEB / "lora_library" / "version.js"
NODE = shutil.which("node")

SRC = NOTEBOOK_JS.read_text(encoding="utf-8")

PROBE_JS = """
import * as nb from './extensions/comfyui-epsnodes/lora_library/notebook.js'

const state = {
  categories: ['Alpha', 'Beta', 'Gamma'],
  entries: [
    { name: 'a1', category: 'Alpha' },
    { name: 'b1', category: 'Beta' },
    { name: 'b2', category: 'Beta' },
    { name: 'g1', category: 'Gamma' }
  ]
}

const out = {
  exports: {
    describePendingCategoryDelete: typeof nb.describePendingCategoryDelete === 'function',
    deletedCategoryStatus: typeof nb.deletedCategoryStatus === 'function'
  },
  pending: {
    withEntries: nb.describePendingCategoryDelete(state, 'Beta'),
    firstCategory: nb.describePendingCategoryDelete(state, 'Alpha'),
    empty: nb.describePendingCategoryDelete(
      { categories: ['Alpha', 'Empty'], entries: [{ name: 'a1', category: 'Alpha' }] },
      'Empty'
    ),
    singular: nb.describePendingCategoryDelete(
      { categories: ['Alpha', 'One'], entries: [{ name: 'x', category: 'One' }] },
      'One'
    )
  },
  done: {
    moved: nb.deletedCategoryStatus({ merged_into: 'Alpha', entries_moved: 2 }, 'Beta'),
    movedOne: nb.deletedCategoryStatus({ merged_into: 'Alpha', entries_moved: 1 }, 'Beta'),
    uncategorized: nb.deletedCategoryStatus({ merged_into: '', entries_moved: 3 }, 'Alpha'),
    none: nb.deletedCategoryStatus({ merged_into: 'Alpha', entries_moved: 0 }, 'Leftover'),
    garbage: nb.deletedCategoryStatus({}, 'Leftover'),
    nullish: nb.deletedCategoryStatus(null, 'Leftover')
  }
}
process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def wording(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Runs PROBE_JS against the REAL notebook.js in a served-layout tmp dir
    (importing the module under Node is itself a regression test — see
    test_m3_pinning_js.py's docstring)."""
    if NODE is None:
        pytest.skip("node (JS runtime) not installed")
    layout = tmp_path_factory.mktemp("web_root")
    module_dir = layout / "extensions" / "comfyui-epsnodes" / "lora_library"
    module_dir.mkdir(parents=True)
    for src in (NOTEBOOK_JS, API_JS, VERSION_JS):
        shutil.copyfile(src, module_dir / src.name)
    scripts = layout / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "api.js").write_text(
        "export const api = { fetchApi: () => {}, apiURL: (p) => p, addEventListener: () => {} }\n",
        encoding="utf-8",
    )
    (scripts / "app.js").write_text("export const app = {}\n", encoding="utf-8")
    probe = layout / "probe.mjs"
    probe.write_text(PROBE_JS, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(probe)], capture_output=True, text=True, timeout=60, cwd=layout
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


# ------------------------------------------------------- armed-confirm wording


def test_helpers_are_exported(wording: dict) -> None:
    assert wording["exports"] == {
        "describePendingCategoryDelete": True,
        "deletedCategoryStatus": True,
    }


def test_armed_confirm_promises_the_merge_before_the_second_click(wording: dict) -> None:
    # The whole safety argument for a two-click confirm: the user is told
    # the entries SURVIVE, how many there are, and where they land, while
    # the delete is still one click away.
    message = wording["pending"]["withEntries"]
    assert 'Delete the "Beta" heading?' in message
    assert "Nothing is deleted with it" in message
    assert "its 2 entries move into" in message
    assert '"Alpha"' in message


def test_armed_confirm_names_the_head_region_for_the_first_category(wording: dict) -> None:
    # Deleting the FIRST category uncategorizes its entries (§3.1 head
    # region) -- "the top of the notebook" in user words, never `""`.
    assert "the top of the notebook" in wording["pending"]["firstCategory"]
    assert '""' not in wording["pending"]["firstCategory"]


def test_armed_confirm_says_so_when_the_header_holds_nothing(wording: dict) -> None:
    # The reported case (an emptied heading): no merge sentence to make.
    assert wording["pending"]["empty"] == 'Delete the "Empty" heading? It holds no entries.'


def test_armed_confirm_is_singular_for_one_entry(wording: dict) -> None:
    assert "its 1 entry move" in wording["pending"]["singular"]


# ---------------------------------------------------------- result-line wording


def test_result_line_reports_the_servers_own_answer(wording: dict) -> None:
    assert wording["done"]["moved"] == (
        'Deleted the "Beta" heading. Its 2 entries moved into "Alpha".'
    )
    assert wording["done"]["movedOne"] == (
        'Deleted the "Beta" heading. Its entry moved into "Alpha".'
    )
    assert wording["done"]["uncategorized"] == (
        'Deleted the "Alpha" heading. Its 3 entries moved into the top of the notebook.'
    )


def test_result_line_degrades_without_claiming_a_merge(wording: dict) -> None:
    # A response missing the fields must never invent a destination.
    for key in ("none", "garbage", "nullish"):
        assert wording["done"][key] == 'Deleted the "Leftover" heading.'


# ----------------------------------------------------------------- source pins


def test_delete_button_is_contextual_not_entry_only() -> None:
    # The defect itself: `state.activeCategory != null` used to force the
    # button disabled. It must now ENABLE on the header alone, and the
    # entry-selection rule must apply only outside category mode.
    assert "const categoryMode = state.activeCategory != null" in SRC
    assert (
        "state.busy || isPinned(state) || (!categoryMode && state.selection.length === 0)" in SRC
    )
    assert "state.busy || state.selection.length === 0 || state.activeCategory != null" not in SRC


def test_click_handler_routes_category_mode_to_the_category_delete() -> None:
    assert "performDeleteCategory(state).catch(" in SRC
    # ...and no longer refuses outright when a category is active.
    assert "if (!state.selection.length || state.busy || state.activeCategory != null) return" not in SRC


def test_category_delete_posts_the_dedicated_route_with_a_conflict_guard() -> None:
    assert "'/lora_library/notebook/delete_category'" in SRC
    assert "if (!force && typeof state.baseMtime === 'number') body.base_mtime = state.baseMtime" in SRC
    assert "onOverwrite: () => performDeleteCategory(state, { force: true })" in SRC


def test_deleting_a_header_leaves_category_mode_and_drops_its_collapse_key() -> None:
    # A deleted header cannot stay selected, and its collapse key must not
    # outlive it in the persisted `Collapsed sections` property -- a later
    # category reusing the name would come back mysteriously collapsed.
    assert "if (state.collapsedCategories.delete(category)) syncCollapsedSectionsProperty(state)" in SRC
    assert "state.activeCategory = null\n  renderList(state)" in SRC


# ------------------------------------------------------- header UI parity
# (owner report 2026-08-25, "When a group is closed in the state controller
# it has an x to delete and shows a number of items in the group. This
# should be consistent in the prompt library as well." — see
# controller.js's `_buildCategoryHeader()`, which this section mirrors.)


def test_collapsed_header_shows_the_live_entry_count() -> None:
    # Count comes from the already-loaded state.entries -- no new fetch --
    # and only appears while collapsed, exactly like the controller's own
    # `${collapsed ? '▸' : '▾'} ${category}${collapsed ? ` (${count})` : ''}`.
    assert "function categoryEntryCount(state, category) {" in SRC
    assert "return state.entries.filter((entry) => entry.category === category).length" in SRC
    assert "? `▸ ${category} (${categoryEntryCount(state, category)})`" in SRC
    assert ": `▾ ${category}`" in SRC


def test_delete_button_is_a_dedicated_child_not_the_header_text() -> None:
    # The header used to be one plain text node; the label and the ✕ are now
    # two separate children so the ✕ can be hit-tested/targeted on its own.
    assert "className: 'llnb-category-label'" in SRC
    assert "const deleteBtn = buildCategoryDeleteButton(state, category)" in SRC
    assert "[labelEl, deleteBtn]" in SRC


def test_header_delete_button_arms_on_first_click_with_the_shared_timeout() -> None:
    # Same visual arming idiom and the same DELETE_CONFIRM_MS window the
    # bottom Delete button already uses (also the controller's own
    # CATEGORY_DELETE_CONFIRM_MS -- both 4000ms) -- but a SEPARATE, local
    # arm/timer per button (`deleteBtn._armed`/`_armTimer`), never the
    # bottom button's `state.deleteConfirmActive`.
    assert "className: 'llnb-category-delete'" in SRC
    assert "if (!deleteBtn._armed) {" in SRC
    assert "deleteBtn._armed = true" in SRC
    assert "deleteBtn.classList.add('llnb-category-delete-armed')" in SRC
    assert "deleteBtn.textContent = 'sure?'" in SRC
    assert "deleteBtn._armTimer = setTimeout(() => {" in SRC
    assert "}, DELETE_CONFIRM_MS)" in SRC
    assert "setStatus(state, describePendingCategoryDelete(state, category))" in SRC


def test_header_delete_button_confirms_into_the_existing_category_delete() -> None:
    # The second click does not open a new request path -- it enters
    # category mode on THIS header's category (the same assignment
    # selectCategory() makes) and calls the one performDeleteCategory()
    # this whole file already ships, so the §5 route, the 409 conflict UI,
    # and the collapse-key cleanup are exercised exactly once, from one
    # place.
    assert "clearTimeout(deleteBtn._armTimer)\n    state.activeCategory = category" in SRC
    assert (
        "performDeleteCategory(state).catch((error) => api.warn('category delete failed', error))"
        in SRC
    )


def test_header_delete_button_is_gated_the_same_as_the_bottom_delete_button() -> None:
    assert "if (isPinned(state) || state.busy) return // M3 / mid-request guard" in SRC


def test_delete_button_click_never_reaches_the_headers_own_handlers() -> None:
    # A click/pointerdown/dblclick that lands on the ✕ must arm/confirm the
    # delete only -- never the header's tap-to-collapse/select drag gesture
    # or the header's dblclick rename -- exactly like controller.js's own
    # `if (event.target === deleteBtn) return` guard.
    assert "event.stopPropagation()" in SRC
    assert (
        "  headerEl.addEventListener('pointerdown', (event) => {\n"
        "    // The ✕ has its own click handler (buildCategoryDeleteButton) — it must\n"
        "    // never also arm the header's drag/collapse-toggle gesture below,\n"
        "    // exactly like controller.js's _buildCategoryHeader() target check.\n"
        "    if (event.target === deleteBtn) return\n"
        "    onCategoryPointerDown(state, event, category)\n"
        "  })" in SRC
    )
    assert (
        "  headerEl.addEventListener('dblclick', (event) => {\n"
        "    // Same reasoning as the pointerdown guard above: a fast double-click\n"
        "    // that lands on the ✕ must arm/confirm the delete, never open the\n"
        "    // rename editor underneath it.\n"
        "    if (event.target === deleteBtn) return\n" in SRC
    )


def test_m3_pinned_mode_still_never_renders_category_headers() -> None:
    # The new header UI is built entirely inside buildCategoryHeaderRow(),
    # which renderList() only ever calls from the categories loop AFTER its
    # early `isPinned` return -- so pinned mode (renderPinnedList()) simply
    # never constructs a header, count or ✕ included. Belt-and-braces pin
    # for the M3 guard itself, same as the other M3 call sites in this file.
    assert "if (isPinned(state)) {\n    renderPinnedList(state)" in SRC


def test_category_css_trio_mirrors_the_controllers_class_names() -> None:
    assert ".llnb-category-label { flex: 1 1 auto; min-width: 0; overflow: hidden;" in SRC
    assert "text-overflow: ellipsis; }" in SRC
    assert ".llnb-category:hover .llnb-category-delete { visibility: visible; }" in SRC
    assert ".llnb-category-delete-armed { color: #ff6b6b; visibility: visible; }" in SRC
