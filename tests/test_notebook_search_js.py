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
    # v0.68.1: the predicate is the AND over a PREBUILT lowercase haystack
    # (name + "\n" + body, built once per load -- see the perf pins below);
    # the old per-call entryMatchesSearch(name, text, query) re-lowercased
    # every body on every keystroke. Same semantics, split into pure parts.
    assert "function entryMatchesSearch(haystack, words)" in SRC
    assert ".every((word) => haystack.includes(word))" in SRC
    assert "function searchHaystack(name, text)" in SRC
    assert "return `${name}\\n${text || ''}`.toLowerCase()" in SRC
    assert "function searchWords(query)" in SRC


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


# ---------------------------------------------------------------------------
# v0.68.1 perf round (audit 2026-08-20): the search was un-debounced and
# re-lowercased every entry's body per keystroke.
# ---------------------------------------------------------------------------


def test_search_corpus_is_prebuilt_once_per_load() -> None:
    # Built right after entryTextByName in reloadNow -- the two always
    # describe the same load -- and read by renderList instead of
    # re-lowercasing per entry per keystroke.
    assert "function buildSearchCorpus(state)" in SRC
    load = SRC.split("state.entryTextByName = Object.fromEntries(", 1)[1][:400]
    assert "buildSearchCorpus(state)" in load
    assert "state.searchCorpus = new Map(" in SRC
    render = SRC.split("function renderList(state)", 1)[1].split("\n/**", 1)[0]
    assert "const words = searchWords(searchQuery)" in render
    assert "state.searchCorpus.get(entry.name) ?? searchHaystack(entry.name, state.entryTextByName[entry.name])" in render
    assert "searchCorpus: new Map()," in SRC


def test_search_input_is_debounced_and_escape_stays_instant() -> None:
    assert "const SEARCH_DEBOUNCE_MS = 120" in SRC
    on_input = SRC.split("state.searchInputEl.addEventListener('input'", 1)[1].split("})", 1)[0]
    assert "scheduleSearchRender(state)" in on_input
    assert "renderList(state)" not in on_input
    sched = SRC.split("function scheduleSearchRender(state)", 1)[1].split("\n}\n", 1)[0]
    assert "clearTimeout(state.searchTimer)" in sched
    assert "}, SEARCH_DEBOUNCE_MS)" in sched
    keydown = SRC.split("state.searchInputEl.addEventListener('keydown'", 1)[1][:500]
    assert "clearTimeout(state.searchTimer)" in keydown
    assert "renderList(state)" in keydown, "Escape repaints at once"
    teardown = SRC.split("function teardown(state)", 1)[1].split("\n}\n", 1)[0]
    assert "if (state.searchTimer) clearTimeout(state.searchTimer)" in teardown


# ---------------------------------------------------------------------------
# Library-on-a-NAS round (2026-08-22): the session cache paints the last
# payload instantly, so the corpus it carries has to be CURRENT -- every
# mutation now keeps entryTextByName/searchCorpus in step (which also makes
# "searchable after Save" true without waiting for the next full load).
# ---------------------------------------------------------------------------


def _body(signature: str) -> str:
    head = f"function {signature} {{\n"
    start = SRC.index(head) + len(head)
    return SRC[start : SRC.index("\n}\n", start)]


def test_mutations_keep_the_search_corpus_current() -> None:
    note = _body("noteEntryText(state, name, text)")
    assert "state.entryTextByName[name] = body" in note
    assert "state.searchCorpus.set(name, searchHaystack(name, body))" in note
    forget = _body("forgetEntryText(state, name)")
    assert "delete state.entryTextByName[name]" in forget and "state.searchCorpus.delete(name)" in forget
    assert "noteEntryText(state, renameTo || name, typeof data.text === 'string' ? data.text : text)" in _body(
        "performSave(state, { force = false } = {})"
    )
    assert "noteEntryText(state, name, '')" in _body("confirmNewEntry(state, rawName)")
    assert "forgetEntryText(state, name)" in _body(
        "performDeleteRun(state, names, startIndex, { force = false } = {})"
    )
    assert "if (kind === 'entry') renameEntryText(state, name, renameTo)" in _body(
        "applyRenameResult(state, kind, name, renameTo, data)"
    )


def test_cached_paint_rebuilds_the_corpus_like_a_fresh_load() -> None:
    # reloadNow's cached paint and the fresh fetch both land in
    # applyNotebookPayload, which builds the corpus right after the text map
    # -- the one-load pin above (`buildSearchCorpus` within 400 chars of the
    # entryTextByName assignment) therefore covers the cached paint too.
    apply = _body("applyNotebookPayload(state, file, data)")
    assert "state.entryTextByName = Object.fromEntries(" in apply
    assert "buildSearchCorpus(state)" in apply
    reload = _body("reloadNow(state)")
    assert "applyNotebookPayload(state, file, cached.payload)" in reload
    assert "await applyNotebookPayload(state, file, data)" in reload
