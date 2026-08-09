"""Source-text pins for the Prompt Notebook's search field (FORMAT.md §7.2,
owner ask 2026-08-08: "a search field at the top of the left row that matches
search words with words either in the title or body of the prompt").

``notebook.js`` is DOM/closure-bound, so these are SOURCE-TEXT pins in the
convention ``test_notebook_restore_js.py`` established; live behavior is
verified on the rig. Each pin names the invariant it protects.
"""

from pathlib import Path

SRC = (
    Path(__file__).resolve().parents[1] / "web" / "lora_library" / "notebook.js"
).read_text(encoding="utf-8")


def test_search_corpus_rides_the_reload_cycle() -> None:
    # include_text=1 on the ONE list fetch keeps the search corpus in
    # lockstep with what the list itself shows -- no separate fetch to race.
    assert "{ file, include_text: '1' }" in SRC
    assert "state.entryTextByName = Object.fromEntries(" in SRC


def test_matcher_is_and_of_words_over_name_plus_body() -> None:
    assert "function entryMatchesSearch(name, text, query)" in SRC
    assert ".every((word) => haystack.includes(word))" in SRC


def test_filtering_is_a_view_that_disarms_drag() -> None:
    # Rows are not pushed to dragRows while filtering (drag-reorder against
    # a partial view would reorder the file in ways the view can't show);
    # collapse state is ignored so matches inside collapsed categories show.
    assert "if (!filtering) {" in SRC and "state.dragRows.push({ el: row" in SRC
    assert "const collapsed = !filtering && state.collapsedCategories.has(category)" in SRC


def test_search_keystrokes_never_reach_canvas_hotkeys() -> None:
    # The §7.5-adjacent rule: panel inputs stop propagation; Escape clears
    # the query in place.
    assert "state.searchInputEl.addEventListener('keydown', (event) => {" in SRC
    pin = SRC.index("state.searchInputEl.addEventListener('keydown'")
    block = SRC[pin : pin + 400]
    assert "event.stopPropagation()" in block and "'Escape'" in block
