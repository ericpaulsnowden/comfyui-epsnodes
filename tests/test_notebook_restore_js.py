"""Regression pins for the Prompt Notebook's workflow-RESTORE path
(FORMAT.md §7.2 — owner report 2026-07-27: "Every time I load a workflow on
my Linux machine I have to re-select the location of my Notebook .md file on
the server. And it doesn't take until after recreate node > reset widget
values").

Two defects, one vicious loop, both reproduced live on the real
``app.loadGraphData`` path before fixing:

1. **The panel loaded the wrong file.** ``attachNotebookWidget`` fires
   ``reloadNow()`` immediately, but litegraph restores ``widgets_values``
   LAST — after the node is constructed and added — so at attach time the
   ``file`` widget still held its backend DEFAULT. The panel therefore
   loaded the default file's entries, and ``configure()`` then wrote the
   saved path into the widget *without* firing its callback (litegraph
   assigns ``widget.value`` directly). Result: ``state.file`` (what the
   panel shows) and ``fileWidget.value`` (what a Run actually reads)
   disagreed permanently — the node ran the right file while displaying the
   wrong one.

2. **Re-picking the same path did nothing**, which is what made it a loop
   rather than an annoyance: ``setFileWidgetValue`` early-returned whenever
   the chosen path equalled the widget's current value — and after a load it
   always did. The only escape was recreating the node so the values
   differed again, exactly the workaround the owner found.

``notebook.js`` is DOM/closure-bound (no pure seam to drive under Node the
way ``test_distributor_js.py`` drives ``toggleBoxRect``), so these are
SOURCE-TEXT pins in the convention ``test_frame_saver_paste_js.py``
established for exactly that case. The live behavior is verified on the rig.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
NOTEBOOK_JS = REPO_ROOT / "web" / "lora_library" / "notebook.js"


@pytest.fixture(scope="module")
def source() -> str:
    return NOTEBOOK_JS.read_text(encoding="utf-8")


def test_attach_installs_the_post_configure_reload(source: str) -> None:
    # Without this call the restore path has nothing that re-reads the file
    # after `configure()` lands — defect 1 returns silently.
    assert "wireConfigureReload(state)" in source
    assert "function wireConfigureReload(state)" in source


def test_post_configure_reload_wraps_on_configure(source: str) -> None:
    """`onConfigure` is the only hook that fires AFTER widgets_values are
    restored (for both a whole-workflow load and a pasted/cloned node)."""
    block = source.split("function wireConfigureReload(state)", 1)[1]
    block = block.split("\nfunction ", 1)[0]
    assert "node.onConfigure" in block
    # Must chain, never replace: other wiring (and core) may already own it.
    assert "originalOnConfigure" in block
    assert "reloadNow(state)" in block


def test_post_configure_reload_is_conditional_on_a_real_mismatch(source: str) -> None:
    # Reloading unconditionally on every configure would re-fetch on paste,
    # undo, and every workflow load even when nothing changed.
    block = source.split("function wireConfigureReload(state)", 1)[1]
    block = block.split("\nfunction ", 1)[0]
    assert "restored !== state.file" in block


def test_post_configure_reload_resyncs_the_remote_guard_baseline(source: str) -> None:
    """`wireFileWidget` captures `lastKnownFileValue` at ATTACH time (the
    default). For a remote viewer (`isLocal === false`) the read-only guard
    reverts edits back to that captured value — so leaving it stale would let
    a remote browser silently rewrite a loaded workflow's saved path back to
    the default."""
    block = source.split("function wireConfigureReload(state)", 1)[1]
    block = block.split("\nfunction ", 1)[0]
    assert "state.lastKnownFileValue = restored" in block


def test_same_value_reselect_reloads_instead_of_no_oping(source: str) -> None:
    """Defect 2's pin: an equal-value pick must still reload when the panel
    is displaying a DIFFERENT file. Without this, Browse…-picking the path
    already in the widget is a dead click — the loop the owner hit."""
    block = source.split("function setFileWidgetValue(state, value)", 1)[1]
    block = block.split("\n// ---", 1)[0]
    assert "if (widget.value === value)" in block
    assert "state.file !== value" in block
    assert "reloadNow(state)" in block


def test_same_value_reselect_still_avoids_pointless_refetches(source: str) -> None:
    # When the panel already shows that file, the equal-value path must stay
    # a no-op — otherwise every redundant pick hits the network.
    block = source.split("function setFileWidgetValue(state, value)", 1)[1]
    block = block.split("\n// ---", 1)[0]
    # the early return survives; it's now guarded, not removed
    assert "return" in block.split("state.file !== value", 1)[1][:400]


# ---------------------------------------------------------------------------
# Rename in place (owner ask 2026-07-29: "i'm having trouble renaming items in
# notebooks. my expectation is i can either double click a name to rename in
# place, or if I change the name at the top of the notebook the item becomes
# savable and I can save with the new name")
#
# BOTH paths must exist. The name-field path shipped in v0.12.0 and is
# exercised live on the rig; the double-click path is new (v0.10.0 had one, it
# was reported not working, and v0.12.0 removed it instead of root-causing it
# — so this is the second time the request has arrived). These pin the two
# structural properties that made the original fragile, plus the data-safety
# rule that a rename must not write the body.
# ---------------------------------------------------------------------------


def test_double_click_opens_an_inline_rename_on_the_row(source: str) -> None:
    """The owner's expectation #1, literally: the editor opens AT the row.
    Focusing the far-away name field instead is the FALLBACK, kept only for
    when no row is rendered to edit (a collapsed category's entry)."""
    block = source.split("function onEntryDoubleClick(state, event, name)", 1)[1]
    block = block.split("\n// ---", 1)[0]
    assert "beginInlineRename(state, 'entry', name)" in block
    assert "focusNameField(state)" in block, "the fallback must survive"
    assert "function beginInlineRename(state, kind, name)" in source
    # Category headers get the same gesture.
    assert "beginInlineRename(state, 'category', category)" in source


def test_inline_rename_survives_a_re_render(source: str) -> None:
    """THE pin for why the v0.10.0 inline rename failed: its editor lived only
    in the DOM, so any rebuild under it (poll refresh, late fetch, collapse
    toggle, drag) silently restored the plain label and threw away the typed
    text — indistinguishable from "renaming doesn't work". The live text now
    lives in state, and renderList() re-establishes the editor."""
    assert "restoreInlineRename(state)" in source
    assert "function restoreInlineRename(state)" in source
    render = source.split("function renderList(state)", 1)[1].split("\n/**", 1)[0]
    assert "restoreInlineRename(state)" in render, "renderList must re-establish it"
    mount = source.split("function mountInlineRename(state, row)", 1)[1].split("\n/**", 1)[0]
    assert "state.inlineRename.value = input.value" in mount, "typed text must be kept in state"


def test_inline_rename_is_rename_only_and_never_writes_the_body(source: str) -> None:
    """The §5 routes always rewrite the body, so a rename has to send the
    target's CURRENT ON-DISK text back. Taking it from the textarea would
    commit unsaved body edits the user never asked to save — and, when the
    renamed row is not the one loaded in the editor, would clobber that
    entry's body with a DIFFERENT entry's text."""
    block = source.split("async function renameEntryRequest(", 1)[1]
    block = block.split("\n/**", 1)[0]
    assert "await fetchEntry(state, name)" in block, "must read the entry's own text"
    assert "text: current?.text ?? ''" in block
    assert "state.textarea" not in block, "the textarea must never be the source"
    cat = source.split("async function renameCategoryRequest(", 1)[1].split("\n/**", 1)[0]
    assert "await fetchCategory(state, name)" in cat
    assert "description: current?.description ?? ''" in cat


def test_inline_rename_conflict_retry_actually_drops_the_stale_mtime(source: str) -> None:
    """`force` has to be captured BEFORE the editor is closed. Reading it back
    off `state.inlineRename` after that null-out yields undefined, so the 409
    Overwrite retry would re-send the same stale base_mtime and 409 forever."""
    block = source.split("async function commitInlineRename(state)", 1)[1]
    block = block.split("\n/**", 1)[0]
    assert "const force = Boolean(active.force)" in block
    # Anchored on the NORMAL-path close (its comment), not the first
    # `state.inlineRename = null` -- the busy-collision guard added
    # 2026-07-30 legitimately closes the editor earlier in the function.
    close_at = block.index("// Close the editor first")
    capture_at = block.index("const force = Boolean(active.force)")
    assert capture_at < close_at, "force must be captured before the editor is closed"
    # ...and it must reach the request functions as an argument.
    assert "renameEntryRequest(state, name, requested, force)" in block
    assert "renameCategoryRequest(state, name, requested, force)" in block


def test_inline_rename_refuses_duplicates_and_blanks(source: str) -> None:
    block = source.split("async function commitInlineRename(state)", 1)[1]
    block = block.split("\n/**", 1)[0]
    assert "already exists" in block
    assert "state.categories.includes(requested)" in block
    assert "state.entries.some((entry) => entry.name === requested)" in block
    assert "if (requested === name)" in block, "a no-op rename must not hit the network"


def test_rename_result_updates_every_home_of_the_old_name(source: str) -> None:
    """A name lives in more places than is obvious here; a miss leaves the UI
    pointing at a name the file no longer has. The collapse Set is the easiest
    to forget — it is keyed by NAME, so a collapsed category would spring open
    (or a renamed one silently render collapsed) without the key migration."""
    block = source.split("function applyRenameResult(", 1)[1]
    block = block.split("\n// ---", 1)[0]
    assert "state.collapsedCategories.delete(name)" in block
    assert "state.collapsedCategories.add(renameTo)" in block
    assert "state.activeCategory = renameTo" in block
    assert "state.nameFieldEl.value = renameTo" in block
    assert "state.lastSavedName = renameTo" in block
    assert "setSelection(state, nextSelection, nextActive)" in block, "syncs the entry widget"


def test_selecting_a_category_does_not_hide_its_entries(source: str) -> None:
    """Found on the rig 2026-07-29 while chasing the rename report. Selecting a
    category is the only way to get its name into the editor, and folding
    collapse into that same tap meant "click the category you want to rename"
    also hid every entry inside it — the rename worked, but it read as the
    entries having been eaten. The FIRST tap on a not-yet-active header must
    only select; once active, taps toggle collapse as before."""
    block = source.split("function toggleCategoryCollapse(state, category)", 1)[1]
    block = block.split("\n/**", 1)[0]
    assert "if (state.activeCategory !== category)" in block
    # On the selecting tap it may only ever REVEAL, never hide. Scoped to that
    # branch alone (up to its `return`) — the already-active branch below it
    # legitimately still calls .add().
    selecting = block.split("if (state.activeCategory !== category)", 1)[1]
    selecting = selecting.split("return", 1)[0]
    assert "state.collapsedCategories.delete(category)" in selecting
    assert "add(category)" not in selecting, "the selecting tap must never collapse"
    # And the already-active branch must keep the real toggle.
    active_branch = block.split("return", 1)[1]
    assert "state.collapsedCategories.add(category)" in active_branch


def test_collapse_toggle_still_runs_before_selection(source: str) -> None:
    """The fix above reads `state.activeCategory` as the PREVIOUS value, so
    the toggle must keep running before selectCategory() updates it. Both call
    sites (the header's keydown and the pointer tap) must preserve that order."""
    for anchor in ("headerEl.addEventListener('keydown'", "// Single-tap collapse"):
        assert anchor in source
    for chunk in source.split("toggleCategoryCollapse(state, category)")[1:]:
        head = chunk[:200]
        if "selectCategory" in head:
            assert head.index("selectCategory") >= 0
    # Neither call site may select first.
    assert "selectCategory(state, category)\n      toggleCategoryCollapse" not in source


# ---------------------------------------------------------------------------
# FORMAT.md §2 share toggle (owner report 2026-07-29) — the frontend half.
# The security decision lives on the server (tests/test_routes_notebook.py);
# these pin that the UI can't misrepresent it.
# ---------------------------------------------------------------------------


def test_share_toggle_is_offered_only_to_a_local_viewer(source: str) -> None:
    """A remote viewer must never be shown this control: the route behind it is
    loopback-only, so for them it could only ever fail — and offering it would
    imply a remote browser can widen its own access, which is the opposite of
    what §2 guarantees."""
    block = source.split("function updateShareToggle(state)", 1)[1]
    block = block.split("\n/**", 1)[0]
    assert "state.isLocal !== false" in block


def test_share_toggle_is_offered_only_when_the_path_is_actually_unreachable(
    source: str,
) -> None:
    """Otherwise it would sit there on every notebook inside the library
    folder, inviting a click that grants access nobody needed."""
    block = source.split("function updateShareToggle(state)", 1)[1]
    block = block.split("\n/**", 1)[0]
    assert "state.libraryDir" in block and "state.remoteDirs" in block
    assert "pathIsInsideAny(" in block
    assert "!reachable" in block


def test_share_toggle_posts_the_parent_folder_not_the_file(source: str) -> None:
    block = source.split("async function onShareToggleChange(state)", 1)[1]
    block = block.split("\n// ---", 1)[0]
    assert "parentDirOf(state.resolvedFile" in block
    assert "'/lora_library/remote_dirs'" in block
    # The cached /config payload carries remote_dirs and is shared by every
    # attached node, so a stale cache would leave the toggle lying.
    assert "invalidateConfigCache()" in block
    assert "refreshRemoteGating(state)" in block


def test_share_toggle_reverts_its_checkbox_when_the_post_fails(source: str) -> None:
    """A checkbox that stays ticked after a failed write claims access the
    server never granted."""
    block = source.split("async function onShareToggleChange(state)", 1)[1]
    block = block.split("\n// ---", 1)[0]
    assert "state.shareToggleEl.checked = !allow" in block


def test_path_containment_check_is_segment_wise_not_a_string_prefix(source: str) -> None:
    """`/nas/docs` must not match `/nas/docs-private`. This one only decides
    whether to OFFER the toggle — the server re-derives the real check — but a
    frontend that disagrees with the server misinforms the user about what is
    already shared."""
    block = source.split("function pathIsInsideAny(fullPath, roots)", 1)[1]
    block = block.split("\nfunction ", 1)[0]
    assert "'/'" in block and "'\\\\'" in block, "must require a separator after the root"
    assert "value === r" in block, "the root itself counts as inside"


def test_share_toggle_is_re_evaluated_when_the_resolved_path_changes(source: str) -> None:
    """Its entire condition is "is THIS path reachable remotely", so pointing
    the node at a different file has to re-decide it."""
    assert source.count("updateShareToggle(state)") >= 3
    # ...including from the load path that assigns resolvedFile.
    load = source.split("state.resolvedFile = typeof data.file === 'string'", 1)[1][:400]
    assert "updateShareToggle(state)" in load


# ---------------------------------------------------------------------------
# v0.68.1 perf/bug round (audit 2026-08-20): a restored node loaded the
# notebook TWICE with full text on every workflow load / tab switch, and a
# double-click rename on a not-yet-active category header collapsed it.
# ---------------------------------------------------------------------------


def test_attach_time_load_is_deferred_and_stands_down_after_configure(source: str) -> None:
    """`nodeCreated` fires from the node's constructor, before `configure()`
    restores widgets_values, so `attach()`'s immediate reloadNow fetched the
    backend-DEFAULT file with full text and wireConfigureReload fetched the
    saved one right after -- the first was discarded by loadToken but fully
    paid (whole-file parse + a LAN round trip, per node). litegraph creates
    AND configures every node of a load synchronously, so a one-tick
    deferral lands after onConfigure: restored nodes load once (configure),
    fresh nodes load once (the timer)."""
    attach = source.split("export function attachNotebookWidget(node)", 1)[1]
    attach = attach.split("\n}\n", 1)[0]
    assert "reloadNow(state).catch((error) => api.warn('initial notebook load failed', error))" in attach
    assert "state.attachLoadTimer = setTimeout(() => {" in attach
    deferred = attach.split("state.attachLoadTimer = setTimeout(() => {", 1)[1]
    assert "if (state.configureReloaded) return" in deferred
    assert deferred.index("if (state.configureReloaded) return") < deferred.index("reloadNow(state)")
    assert "}, 0)" in deferred
    # no bare, immediate attach-time load survives
    assert "\n    reloadNow(state).catch((error) => api.warn('initial notebook load failed'" not in attach


def test_configure_reload_raises_the_flag_before_it_reloads(source: str) -> None:
    block = source.split("function wireConfigureReload(state)", 1)[1]
    block = block.split("\nfunction ", 1)[0]
    assert "state.configureReloaded = true" in block
    assert block.index("state.configureReloaded = true") < block.index("reloadNow(state)")
    # still conditional on the real mismatch (the existing pin) -- the flag
    # sits INSIDE that branch, so an unchanged value never sets it
    gate = block.split("if (restored !== state.file || state.loadError) {", 1)[1]
    assert "state.configureReloaded = true" in gate[:200]


def test_teardown_cancels_the_deferred_attach_load(source: str) -> None:
    """A node removed before its first tick (undo right after paste) must
    not fetch for a dead panel."""
    block = source.split("function teardown(state)", 1)[1].split("\n}\n", 1)[0]
    assert "if (state.attachLoadTimer) clearTimeout(state.attachLoadTimer)" in block
    assert "configureReloaded: false," in source and "attachLoadTimer: null," in source


def test_double_click_rename_restores_the_pre_pair_collapse_state(source: str) -> None:
    """Since the 2026-07-29 rule the FIRST tap on a not-yet-active header
    only selects/expands and the SECOND toggles -- so the two taps that
    precede a dblclick leave the header COLLAPSED and the rename editor
    opened on a header whose entries had just vanished: the exact symptom
    that rule exists to prevent. The dblclick handler now puts the collapse
    state back to what it was before the first tap of the pair."""
    header = source.split("function buildCategoryHeaderRow(state, category)", 1)[1]
    header = header.split("\n}\n", 1)[0]
    dbl = header.split("headerEl.addEventListener('dblclick'", 1)[1].split("})", 1)[0]
    assert "restoreCategoryCollapseAfterDoubleClick(state, category)" in dbl
    assert dbl.index("restoreCategoryCollapseAfterDoubleClick") < dbl.index(
        "beginInlineRename(state, 'category', category)"
    ), "restore first, so the editor mounts on the freshly rendered row"
    # the stale claim that the two taps net to zero is gone
    assert "two preceding taps have each toggled collapse" not in source


def test_first_tap_of_a_pair_notes_the_collapse_state(source: str) -> None:
    assert "const CATEGORY_DBLCLICK_WINDOW_MS = 1000" in source
    down = source.split("function onCategoryPointerDown(state, event, category)", 1)[1]
    down = down.split("\n}\n", 1)[0]
    assert "state.categoryTapMemo = { category, collapsed: state.collapsedCategories.has(category), at: now }" in down
    # a second pointerdown on the SAME header inside the window keeps the
    # first tap's note; any other header, or a later tap, starts fresh
    assert "prior.category !== category || now - prior.at > CATEGORY_DBLCLICK_WINDOW_MS" in down
    # and the note is taken BEFORE the tap resolves (the pointerup toggle)
    assert down.index("state.categoryTapMemo =") < down.index("const drag = {")
    restore = source.split("function restoreCategoryCollapseAfterDoubleClick(state, category)", 1)[1]
    restore = restore.split("\n}\n", 1)[0]
    assert "state.categoryTapMemo = null" in restore, "the note is consumed"
    assert "if (!memo || memo.category !== category) return" in restore
    assert "if (memo.collapsed) state.collapsedCategories.add(category)" in restore
    assert "else state.collapsedCategories.delete(category)" in restore
    assert "renderList(state)" in restore
    assert "categoryTapMemo: null," in source
