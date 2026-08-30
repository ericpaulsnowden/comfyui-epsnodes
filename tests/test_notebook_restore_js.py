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

import json
import shutil
import subprocess
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


# --------------- 2026-08-29 bugfix round: Universal State Controller Apply


def test_external_write_resync_branches_on_whether_file_changed(source: str) -> None:
    """Owner report: "applying any of the sets won't change anything" --
    an Apply writing this node's `entry` widget directly (`widget.value =
    x; widget.callback?.()`) updates the node but never touches this
    panel's own rendered DOM. `resyncAfterExternalWrite` is the fix: a
    changed `file` needs the SAME real reload `onFileWidgetChanged()`/
    `wireConfigureReload()` already use (a different file's entries were
    never fetched); an unchanged `file` is a cache-only resync reusing
    `restoreSelectionFromWidget()` plus the exact render calls
    `applyNotebookPayload()` runs after every load -- never a parallel
    render path."""
    block = source.split("function resyncAfterExternalWrite(state)", 1)[1]
    block = block.split("\nfunction ", 1)[0]
    assert "restored !== state.file || state.loadError" in block
    assert "reloadNow(state)" in block
    assert "restoreSelectionFromWidget(state)" in block
    assert "renderList(state)" in block
    assert "updateDeleteButtonEnabled(state)" in block
    assert "updateSelectionHint(state)" in block
    assert "updateModeHint(state)" in block
    assert "loadActiveEditor(state)" in block


def test_attach_publishes_the_reload_seam_and_installs_the_subscription(source: str) -> None:
    """Mirrors picker.js's pre-existing `__epsLpReload` seam (controller.js's
    Push State already pokes it) -- api.js's shared
    `announceWidgetsChangedExternally()`/`subscribeWidgetsChangedExternally()`
    routes by NODE IDENTITY, so this file only needs to stamp its own
    reload function onto the node and make sure the ONE shared subscription
    (idempotent module flag, installed from the first attach) is live."""
    attach = source.split("export function attachNotebookWidget(node)", 1)[1]
    attach = attach.split("\n}\n", 1)[0]
    assert "node.__epsNotebookReload = () => resyncAfterExternalWrite(state)" in attach
    assert "installExternalWriteSubscription()" in attach
    install = source.split("function installExternalWriteSubscription()", 1)[1]
    install = install.split("\n}\n", 1)[0]
    assert "if (externalWriteSubscribed) return" in install
    assert "externalWriteSubscribed = true" in install
    assert "api.subscribeWidgetsChangedExternally((entries) => {" in install
    assert "entry?.node?.__epsNotebookReload?.()" in install


def test_same_value_reselect_reloads_instead_of_no_oping(source: str) -> None:
    """Defect 2's pin: an equal-value pick must still reload when the panel
    is displaying a DIFFERENT file. Without this, Browse…-picking the path
    already in the widget is a dead click — the loop the owner hit."""
    block = source.split("function setFileWidgetValue(state, rawValue)", 1)[1]
    block = block.split("\n// ---", 1)[0]
    assert "if (widget.value === value)" in block
    assert "state.file !== value" in block
    assert "reloadNow(state)" in block


def test_same_value_reselect_still_avoids_pointless_refetches(source: str) -> None:
    # When the panel already shows that file, the equal-value path must stay
    # a no-op — otherwise every redundant pick hits the network.
    block = source.split("function setFileWidgetValue(state, rawValue)", 1)[1]
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
    entry's body with a DIFFERENT entry's text. Finding 2 (2026-08-26
    responsiveness round) reads that text from the include_text=1 cache
    (`state.entryTextByName`/`state.categoryDescriptionByName`) instead of a
    fresh GET before the POST -- same disk-loaded-not-live-textarea
    guarantee, one request instead of two."""
    block = source.split("async function renameEntryRequest(", 1)[1]
    block = block.split("\n/**", 1)[0]
    assert "state.entryTextByName[name] ?? ''" in block, "must read the entry's own cached text"
    assert "state.textarea" not in block, "the textarea must never be the source"
    assert "fetchEntry" not in block, "finding 2: no GET before the rename POST"
    cat = source.split("async function renameCategoryRequest(", 1)[1].split("\n/**", 1)[0]
    assert "state.categoryDescriptionByName[name] ?? ''" in cat
    assert "fetchCategory" not in cat, "finding 2: no GET before the rename POST"


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


# ---------------------------------------------------------------------------
# Library-on-a-NAS round (owner 2026-08-22: "Sometimes the Notebook looks
# broken but it just takes over a minute to load, even when just tabbing
# between open workflows"). A tab switch RECREATES the node, and the panel
# sat on an empty list until a fresh full GET (whole-file parse, full text)
# came back over the NAS. Now a module-level session cache paints what the
# previous incarnation loaded INSTANTLY, and `known_mtime` lets the backend
# answer `unchanged` so the refresh is a stat, not a parse (FORMAT.md
# §5/§6.1/§7.2).
#
# Two layers, the sibling files' conventions: a Node probe of the PURE
# exported helpers (test_m3_pinning_js.py's served-layout fixture --
# notebook.js imports ./api.js -> ../../../scripts/api.js, so the layout
# mirrors that depth and stubs the two core scripts), and source pins for
# the closure-bound reload path.
# ---------------------------------------------------------------------------

WEB = REPO_ROOT / "web"
API_JS = WEB / "lora_library" / "api.js"
VERSION_JS = WEB / "lora_library" / "version.js"
NODE = shutil.which("node")

CACHE_PROBE_JS = """
import * as nb from './extensions/comfyui-epsnodes/lora_library/notebook.js'

const out = {
  exports: {
    notebookCacheGet: typeof nb.notebookCacheGet === 'function',
    notebookCacheSet: typeof nb.notebookCacheSet === 'function',
    isUnchangedResponse: typeof nb.isUnchangedResponse === 'function',
    parseCollapsedSections: typeof nb.parseCollapsedSections === 'function',
    toggleCollapsedSection: typeof nb.toggleCollapsedSection === 'function',
    isSectionCollapsed: typeof nb.isSectionCollapsed === 'function'
  }
}
out.missBeforeSet = nb.notebookCacheGet('a.md')
nb.notebookCacheSet('a.md', { entries: [{ name: 'x' }], mtime: 5 }, 5)
nb.notebookCacheSet('/nas/b.md', { entries: [] }, null)
out.hitA = nb.notebookCacheGet('a.md')
out.hitB = nb.notebookCacheGet('/nas/b.md')
out.missOtherFile = nb.notebookCacheGet('c.md')
out.missNonString = nb.notebookCacheGet(null)
nb.notebookCacheSet('a.md', { entries: [{ name: 'y' }], mtime: 9 }, 9)
out.overwrittenA = nb.notebookCacheGet('a.md')
nb.notebookCacheSet('a.md', null, 10)
out.nullPayloadIgnored = nb.notebookCacheGet('a.md')
nb.notebookCacheSet('a.md', { entries: [] }, 'not-a-number')
out.nonNumericMtimeIsNull = nb.notebookCacheGet('a.md')
out.unchanged = [
  nb.isUnchangedResponse({ ok: true, unchanged: true, mtime: 1.5, exists: true, file: '/x' }),
  nb.isUnchangedResponse({ unchanged: true }),
  nb.isUnchangedResponse({ ok: true, entries: [], mtime: 1 }),
  nb.isUnchangedResponse({ unchanged: 'yes' }),
  nb.isUnchangedResponse({ unchanged: 1 }),
  nb.isUnchangedResponse(null),
  nb.isUnchangedResponse(undefined),
  nb.isUnchangedResponse('unchanged')
]
// Browse… round (2026-08-22): the folder picker is exported and never
// rejects -- null with no DOM (here) and null for a remote viewer without
// touching the DOM at all.
out.pickServerFolder = [
  typeof nb.pickServerFolder,
  await nb.pickServerFolder({ title: 'x', startDir: '/nowhere' }),
  await nb.pickServerFolder({ isLocal: false }),
  await nb.pickServerFolder()
]

// Collapsed sections persist with the workflow (owner ask 2026-08-23) --
// the pure array helpers behind the `Collapsed sections` node property.
out.collapsedParse = [
  nb.parseCollapsedSections(['A', 'B']),
  nb.parseCollapsedSections(['A', 1, null, 'B', {}]), // non-strings dropped
  nb.parseCollapsedSections('["A","B"]'), // a hand-edited JSON string round-trips
  nb.parseCollapsedSections('not json'), // malformed JSON folds to []
  nb.parseCollapsedSections('[1,2]'), // a JSON array of non-strings folds to []
  nb.parseCollapsedSections(''), // blank string
  nb.parseCollapsedSections('   '), // whitespace-only string
  nb.parseCollapsedSections(null),
  nb.parseCollapsedSections(undefined),
  nb.parseCollapsedSections(42),
  nb.parseCollapsedSections({ A: true }) // a plain object, not an array
]
const onceCollapsed = nb.toggleCollapsedSection([], 'A')
const twiceCollapsed = nb.toggleCollapsedSection(onceCollapsed, 'A')
const addInput = ['A']
const addResult = nb.toggleCollapsedSection(addInput, 'B')
const removeInput = ['A', 'B']
const removeResult = nb.toggleCollapsedSection(removeInput, 'A')
out.collapsedToggle = {
  onceCollapsed,
  twiceCollapsed, // toggling twice round-trips to empty
  addInput, // the original array must be untouched (pure)
  addResult,
  removeInput,
  removeResult,
  nonArrayInput: nb.toggleCollapsedSection(null, 'A')
}
out.collapsedIsSection = [
  nb.isSectionCollapsed(['A', 'B'], 'A'),
  nb.isSectionCollapsed(['A', 'B'], 'C'),
  nb.isSectionCollapsed([], 'A'),
  nb.isSectionCollapsed(null, 'A'),
  nb.isSectionCollapsed(undefined, 'A')
]
out.emptyCategoryInsert = (() => {
  const E = (...xs) => xs.map(([n, c]) => ({ name: n, category: c }))
  const cats = ['Empty Top', 'Group Two', 'Group Three']
  const list = E(['A', 'Group Two'], ['B', 'Group Two'], ['C', 'Group Three'])
  return {
    emptyTop: nb.emptyCategoryInsertIndex(list, 'Empty Top', cats),
    emptyMiddle: nb.emptyCategoryInsertIndex(
      E(['A', 'Empty Top'], ['C', 'Group Three']), 'Group Two', cats
    ),
    emptyLast: nb.emptyCategoryInsertIndex(
      E(['A', 'Empty Top'], ['B', 'Group Two']), 'Group Three', cats
    ),
    headRegion: nb.emptyCategoryInsertIndex(list, '', cats),
    unknown: nb.emptyCategoryInsertIndex(list, 'Ghost', cats),
    noCategoryList: nb.emptyCategoryInsertIndex(list, 'Empty Top', null)
  }
})()
out.orderByList = {
  reordered: nb.orderNamesByList(['B', 'A', 'C'], [{ name: 'A' }, { name: 'B' }, { name: 'C' }]),
  subset: nb.orderNamesByList(['C', 'A'], [{ name: 'A' }, { name: 'B' }, { name: 'C' }]),
  single: nb.orderNamesByList(['B'], [{ name: 'A' }, { name: 'B' }]),
  empty: nb.orderNamesByList([], [{ name: 'A' }]),
  unknownGoesLast: nb.orderNamesByList(
    ['B', 'Ghost', 'A'],
    [{ name: 'A' }, { name: 'B' }]
  ),
  noEntries: nb.orderNamesByList(['B', 'A'], null),
  nonArray: nb.orderNamesByList(null, [{ name: 'A' }])
}
out.relativize = {
  inside: nb.relativizeToLibrary('/lib/docs/x.md', '/lib/docs'),
  nested: nb.relativizeToLibrary('/lib/docs/sub/x.md', '/lib/docs'),
  siblingPrefix: nb.relativizeToLibrary('/lib2/x.md', '/lib'),
  outside: nb.relativizeToLibrary('/elsewhere/x.md', '/lib/docs'),
  windows: nb.relativizeToLibrary('Z:\\\\docs\\\\x.md', 'Z:\\\\docs'),
  trailingSlashBase: nb.relativizeToLibrary('/lib/docs/x.md', '/lib/docs/'),
  dirItself: nb.relativizeToLibrary('/lib/docs', '/lib/docs'),
  alreadyRelative: nb.relativizeToLibrary('x.md', '/lib/docs'),
  noBase: nb.relativizeToLibrary('/lib/docs/x.md', null),
  empty: nb.relativizeToLibrary('', '/lib')
}
process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def cache_api(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Runs CACHE_PROBE_JS against the REAL notebook.js in a served-layout
    tmp dir (importing the module under Node is itself a regression test --
    see test_m3_pinning_js.py's docstring)."""
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
    probe.write_text(CACHE_PROBE_JS, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(probe)], capture_output=True, text=True, timeout=60, cwd=layout
    )
    assert result.returncode == 0, f"probe failed (notebook.js must IMPORT under Node):\n{result.stderr}"
    return json.loads(result.stdout)


def _body(source_text: str, signature: str) -> str:
    """Body of a top-level ``function <signature> {`` (an `export`/`async`
    prefix is not part of *signature*) up to its column-0 closing brace --
    the sibling JS test files' identical helper."""
    head = f"function {signature} {{\n"
    assert head in source_text, f"{head!r} not found"
    start = source_text.index(head) + len(head)
    end = source_text.index("\n}\n", start)
    return source_text[start:end]


def test_cache_helpers_are_pure_per_file_and_last_write_wins(cache_api: dict) -> None:
    assert cache_api["exports"] == {k: True for k in cache_api["exports"]}
    assert cache_api["missBeforeSet"] is None
    assert cache_api["hitA"] == {"payload": {"entries": [{"name": "x"}], "mtime": 5}, "mtime": 5}
    # a file that does not exist yet caches its (empty) payload with no mtime
    assert cache_api["hitB"] == {"payload": {"entries": []}, "mtime": None}
    # per file, never cross-file
    assert cache_api["missOtherFile"] is None
    assert cache_api["missNonString"] is None
    assert cache_api["overwrittenA"]["mtime"] == 9
    assert cache_api["overwrittenA"]["payload"]["entries"] == [{"name": "y"}]
    # defensive: a null payload is ignored, a non-numeric mtime stores as null
    assert cache_api["nullPayloadIgnored"]["mtime"] == 9
    assert cache_api["nonNumericMtimeIsNull"] == {"payload": {"entries": []}, "mtime": None}


def test_unchanged_is_exactly_unchanged_true(cache_api: dict) -> None:
    """CONTRACT: `{"ok": true, "unchanged": true, ...}` = keep what's painted;
    ANY payload without `unchanged: true` is the full payload (an older
    backend ignores `known_mtime` and returns it)."""
    assert cache_api["unchanged"] == [True, True, False, False, False, False, False, False]


# ------------------------------------------------ Browse… round (2026-08-22)
# The Notebook's Browse… picker became the shared `openPickerDialog()` core
# plus an exported FOLDER picker (`pickServerFolder`) for the State
# Controller's Library-folder Browse… (FORMAT.md §6.3). The Notebook's own
# file-picking flow must be unchanged.


def _picker_section(source: str) -> str:
    return source.split("const PICKER_OVERLAY_ID = 'llnb-picker-overlay'", 1)[1].split(
        "function setFileWidgetValue(state, rawValue)", 1
    )[0]


def test_folder_picker_is_exported_and_never_rejects(cache_api: dict) -> None:
    assert cache_api["pickServerFolder"] == ["function", None, None, None]


def test_notebook_file_picking_flow_is_unchanged_by_the_refactor(source: str) -> None:
    """openBrowsePicker(state) is now a FILE-mode spec over the shared
    dialog: the Notebook `state` stays the picker session, a clicked .md row
    still closes the picker, acknowledges the click on the status line and
    writes the `file` widget through setFileWidgetValue()."""
    body = _body(source, "openBrowsePicker(state)")
    assert "mode: 'file'," in body
    assert "startDir: dirnameOfServerPath(state.resolvedFile)," in body
    assert "setStatus(state, `Opening ${chosen}...`)" in body
    assert "setFileWidgetValue(state, chosen)" in body
    assert "openPickerDialog(state)" in body
    render = _body(source, "renderPickerDialog(session, dialog, contentEl, pathErrorEl, data)")
    assert "closeBrowsePicker(session)\n      spec.onPickFile?.(chosen)" in render
    assert "const chosen = joinServerPath(data.dir, file.name, data.sep)" in render
    # file mode keeps its footer (Cancel only) and its empty-listing wording
    footer = _body(source, "buildPickerFooter(session, data)")
    assert (
        "if (spec.mode !== 'folder') {\n"
        "    return el('div', { className: 'llnb-picker-footer' }, [cancelBtn])"
        in footer
    )
    assert "'No subfolders or .md files here.'" in render
    # teardown still tears the picker down (a node removed mid-picker)
    teardown = _body(source, "teardown(state)")
    assert "closeBrowsePicker(state)" in teardown
    assert "pickerSpec: null," in source  # the state carries the spec slot


def test_picker_escape_listener_stays_singular_and_capture_phase(source: str) -> None:
    """§7.5: the picker's ONE window listener -- capture-phase keydown for
    Escape, removed with the same flag -- and the one-picker-at-a-time rule
    now spans both callers through `activePickerSession`. No other window
    listener was added by the refactor."""
    section = _picker_section(source)
    assert section.count("window.addEventListener(") == 1
    assert (
        "window.addEventListener('keydown', session.pickerKeydownHandler, { capture: true })"
        in section
    )
    assert section.count("window.removeEventListener(") == 1
    assert (
        "window.removeEventListener('keydown', session.pickerKeydownHandler, { capture: true })"
        in section
    )
    assert "let activePickerSession = null" in section
    open_dialog = _body(source, "openPickerDialog(session)")
    assert (
        "if (activePickerSession && activePickerSession !== session) "
        "closeBrowsePicker(activePickerSession)"
        in open_dialog
    )
    assert "closeBrowsePicker(session)\n  activePickerSession = session" in open_dialog
    assert "injectStyles()" in open_dialog  # a controller may open it before any Notebook attached
    close = _body(source, "closeBrowsePicker(session)")
    # ownership (rig 2026-08-22): the overlay is removed and the CANCEL hook
    # fired only for the session that owns the open picker -- the open-time
    # self-close must not resolve pickServerFolder() null, and a Notebook
    # teardown must not pull a controller's open picker down
    assert close.lstrip().startswith("const owns = activePickerSession === session")
    assert (
        "if (owns) {\n    document.getElementById(PICKER_OVERLAY_ID)?.remove()\n"
        "    activePickerSession = null\n  }"
        in close
    )
    assert "if (!owns) return" in close
    assert close.index("session.pickerKeydownHandler = null") < close.index("if (!owns) return")
    assert (
        "const onClose = session.pickerOnClose\n  session.pickerOnClose = null\n  onClose?.()"
        in close
    )


def test_folder_mode_lists_folders_and_confirms_the_listed_folder(source: str) -> None:
    """pickServerFolder(): directory-oriented -- files dimmed as context,
    the confirm button picks the folder currently LISTED (disabled at the
    Top Level and after a failed listing), an optional in-dialog second
    step (`confirmPrompt`) for machine-wide actions, and a deliberate pick
    is not a cancel (the close hook is cleared before the teardown)."""
    assert "export function pickServerFolder(options = {})" in source
    pick = _body(source, "pickServerFolder(options = {})")
    assert "if (isLocal === false) {" in pick and "return Promise.resolve(null)" in pick
    assert (
        "if (typeof document === 'undefined' || !document.body) return Promise.resolve(null)"
        in pick
    )
    assert "pickerOnClose: () => finish(null)," in pick
    assert "mode: 'folder'," in pick
    assert "onPickFolder: (path) => finish(path)" in pick
    render = _body(source, "renderPickerDialog(session, dialog, contentEl, pathErrorEl, data)")
    assert "if (folderMode) {" in render
    assert "className: 'llnb-picker-row llnb-picker-row-dim'" in render
    assert "folderMode ? 'No subfolders here.' : 'No subfolders or .md files here.'" in render
    footer = _body(source, "buildPickerFooter(session, data)")
    assert "data.dir !== FS_ROOTS ? data.dir : null" in footer
    assert "text: spec.confirmLabel || 'Use this folder'" in footer
    assert "if (!candidate) useBtn.disabled = true" in footer
    assert (
        "if (typeof spec.confirmPrompt !== 'function') {\n"
        "      commitPickedFolder(session, candidate)"
        in footer
    )
    assert "text: spec.confirmPrompt(candidate)" in footer
    assert (
        "backBtn.addEventListener('click', () => strip.replaceWith(buildPickerFooter(session, "
        "data)))"
        in footer
    )
    commit = _body(source, "commitPickedFolder(session, path)")
    assert "session.pickerOnClose = null\n  closeBrowsePicker(session)\n  onPick?.(path)" in commit
    load = _body(source, "loadPickerDir(session, dialog, contentEl, pathErrorEl, dir)")
    assert "buildPickerFooter(session, null)" in load  # failed listing: nothing to confirm
    # the dialog's optional caption + the confirm strip have CSS of their own
    css = source.split("const CSS_TEXT = `", 1)[1].split("\n`\n", 1)[0]
    for cls in (
        ".llnb-picker-title",
        ".llnb-picker-row-dim",
        ".llnb-picker-confirm",
        ".llnb-picker-confirm-buttons",
    ):
        assert cls + " {" in css, cls


def test_session_cache_is_module_level_and_survives_teardown(source: str) -> None:
    """The whole point: a node recreated by a tab switch must find what the
    previous incarnation loaded, so the Map lives at module scope and
    teardown() (node removal) never clears it."""
    assert "const notebookCache = new Map()" in source
    assert "export function notebookCacheGet(file)" in source
    assert "export function notebookCacheSet(file, payload, mtime)" in source
    assert "notebookCache.clear(" not in source
    assert "notebookCache.delete(" not in source
    teardown = _body(source, "teardown(state)")
    assert "notebookCache" not in teardown


def test_reload_paints_the_cached_payload_before_the_fetch_through_the_load_path(
    source: str,
) -> None:
    """Instant paint: cached payload -> the SAME applyNotebookPayload() the
    fresh fetch uses, synchronously BEFORE the await, marked in the status
    row; the fetch then reconciles. Keyed by the `file` value as SENT."""
    reload = _body(source, "reloadNow(state)")
    assert "const file = state.fileWidget.value ?? ''" in reload
    assert "const cached = notebookCacheGet(file)" in reload
    paint = "applyNotebookPayload(state, file, cached.payload)"
    fetch = "await api.getJson('/lora_library/notebook', params)"
    assert paint in reload and fetch in reload
    assert reload.index("const cached = notebookCacheGet(file)") < reload.index(paint) < reload.index(fetch)
    # only when the panel is not already showing that exact snapshot
    assert "if (cached && (!showing || state.paintedMtime !== cached.mtime)) {" in reload
    assert "const showing = state.file === file && !state.loadError" in reload
    assert "paintedFromCache = true" in reload
    assert "setCacheHint(state, CACHE_HINT_REFRESHING)" in reload
    assert "const CACHE_HINT_REFRESHING = 'cached — refreshing…'" in source
    # the fresh path lands through the very same function
    assert "await applyNotebookPayload(state, file, data)" in reload
    assert "async function applyNotebookPayload(state, file, data)" in source
    # ...and the token/restore discipline around it is untouched
    assert reload.startswith("  const token = ++state.loadToken")
    assert "if (token !== state.loadToken) return" in reload


def test_known_mtime_rides_the_reload_get_only_for_what_this_panel_painted(source: str) -> None:
    """CONTRACT: `known_mtime=<the mtime THIS panel last painted for this
    file>` -- per node (two Notebooks on one file must not vouch for each
    other), only while the panel is showing the file, never for a file the
    panel has not painted (or painted as the error state)."""
    reload = _body(source, "reloadNow(state)")
    assert "const params = { file, include_text: '1' }" in reload  # the search-corpus literal survives
    assert "if ((showing || paintedFromCache) && typeof state.paintedMtime === 'number') {" in reload
    assert "params.known_mtime = String(state.paintedMtime)" in reload
    assert "paintedMtime: null," in source
    apply = _body(source, "applyNotebookPayload(state, file, data)")
    assert "state.paintedMtime = typeof data.mtime === 'number' ? data.mtime : null" in apply
    # the error path stops vouching (the list shows the error, not the file)
    error_branch = reload.split("} catch (error) {", 1)[1].split("if (isUnchangedResponse(data))", 1)[0]
    assert "state.paintedMtime = null" in error_branch
    sync = _body(source, "syncNotebookCache(state, data)")
    assert "state.paintedMtime = mtime" in sync  # a mutation's response mtime is what the panel now shows


def test_unchanged_answer_keeps_what_is_painted(source: str) -> None:
    """CONTRACT: `unchanged: true` => clear the hint, keep the mtime, no
    re-render, no entry reset; only an explicit reload on an ALREADY-showing
    file (conflict Reload, unpin) re-runs the editor half, never the list."""
    reload = _body(source, "reloadNow(state)")
    block = reload.split("if (isUnchangedResponse(data)) {", 1)[1].split("\n  }\n", 1)[0]
    assert "return" in block
    for forbidden in ("renderList(", "applyNotebookPayload(", "notebookCacheSet(", "restoreSelectionFromWidget("):
        assert forbidden not in block, forbidden
    assert "if (!paintedFromCache) await loadActiveEditor(state)" in block
    # the hint clears BEFORE the branch, on success and on failure alike
    success_tail = reload.split("if (token !== state.loadToken) return\n  setCacheHint(state, '')", 1)
    assert len(success_tail) == 2
    assert reload.count("setCacheHint(state, '')") >= 2
    # the full-payload path caches AFTER the unchanged early return
    assert reload.index("if (isUnchangedResponse(data)) {") < reload.index(
        "notebookCacheSet(file, data, typeof data.mtime === 'number' ? data.mtime : null)"
    )
    assert "export function isUnchangedResponse(data)" in source
    assert "data.unchanged === true" in _body(source, "isUnchangedResponse(data)")


def test_every_successful_load_and_mutating_response_updates_the_cache(source: str) -> None:
    reload = _body(source, "reloadNow(state)")
    assert "notebookCacheSet(file, data, typeof data.mtime === 'number' ? data.mtime : null)" in reload
    for signature in (
        "performSave(state, { force = false } = {})",
        "performSaveCategory(state, { force = false } = {})",
        "confirmNewEntry(state, rawName)",
        "confirmNewCategory(state, name)",
        "performDeleteRun(state, names, { force = false } = {})",
        "performMove(state, name, target, { force = false } = {})",
        "performMoveRun(state, names, target, { force = false } = {})",
        "performMoveCategory(state, category, target, { force = false } = {})",
        "applyRenameResult(state, kind, name, renameTo, data)",
    ):
        assert "syncNotebookCache(state, data)" in _body(source, signature), signature
    sync = _body(source, "syncNotebookCache(state, data)")
    # the fold is keyed by the panel's file, from the panel's fresh state +
    # the response mtime; a missing mtime stops the cache vouching
    assert "const file = state.file" in sync
    assert "const mtime = typeof data?.mtime === 'number' ? data.mtime : null" in sync
    assert "notebookCacheSet(file, payload, mtime)" in sync
    assert "text: state.entryTextByName[entry.name] ?? ''" in sync
    assert "if (typeof file !== 'string' || !file || state.loadError) return" in sync


def test_cache_hint_is_its_own_subtle_status_row_element(source: str) -> None:
    assert "state.cacheHintEl = el('div', { className: 'llnb-status-cached' })" in source
    assert ".llnb-status-cached:empty { display: none; }" in source
    assert "cacheHintEl: null," in source
    hint = _body(source, "setCacheHint(state, text)")
    assert "state.cacheHintEl.textContent = text || ''" in hint


def test_single_load_per_restore_logic_is_intact_around_the_cache(source: str) -> None:
    """The cache only changes what is on screen while the fetch is in
    flight: the v0.68.1 deferred attach load / configureReloaded stand-down
    and the M3 reconcile in wireConfigureReload are byte-for-byte as before."""
    attach = source.split("export function attachNotebookWidget(node)", 1)[1].split("\n}\n", 1)[0]
    assert "if (state.configureReloaded) return" in attach
    assert "notebookCache" not in attach  # no attach-time paint of a not-yet-restored file value
    wire = _body(source, "wireConfigureReload(state)")
    assert "state.configureReloaded = true" in wire
    assert "syncPinnedFromWidget(state)" in wire
    assert "notebookCache" not in wire


# ---------------------------------------------------------------------------
# Collapsed sections persist with the workflow (owner ask 2026-08-23: "I
# often group by type of workflow so I never want to see specific prompts
# in specific workflows but they keep opening up and making the list too
# long"). Before this round, `state.collapsedCategories` was session/
# per-node UI state that reset on every page reload (the old "Single-tap
# collapse" design, still described -- historically -- earlier in the file
# header). Now a `Collapsed sections` node PROPERTY (array of collapsed
# category names) is the source of truth, restored the same way this pack's
# other per-instance UI-toggle properties are (resolution.js's Show original
# size, sets.js's Show strength scale, picker.js's Auto-grow with selection,
# switcher.js's High/low pairs): `addProperty()` at attach + a wrapped
# `onPropertyChanged` + one explicit initial apply call. `state.
# collapsedCategories` is now a CACHE over that property, not its own source
# of truth -- see registerCollapsedSectionsProperty()/
# applyCollapsedSectionsFromProperty()/syncCollapsedSectionsProperty().
# ---------------------------------------------------------------------------


def test_collapsed_sections_pure_helpers_are_exported(cache_api: dict) -> None:
    assert cache_api["exports"]["parseCollapsedSections"] is True
    assert cache_api["exports"]["toggleCollapsedSection"] is True
    assert cache_api["exports"]["isSectionCollapsed"] is True


def test_parse_collapsed_sections_is_tolerant(cache_api: dict) -> None:
    """CONTRACT: the canonical array-of-strings shape, and a JSON-encoded
    string of the same (a hand-edit through the node's Properties panel
    round-trips as a string) both parse; everything else -- malformed JSON,
    a JSON array of non-strings, blank/whitespace, null, undefined, a bare
    number, a plain object -- folds to `[]` rather than throwing."""
    parsed = cache_api["collapsedParse"]
    assert parsed[0] == ["A", "B"]
    assert parsed[1] == ["A", "B"]  # non-string array entries dropped
    assert parsed[2] == ["A", "B"]  # JSON-encoded string round-trips
    assert parsed[3] == []  # malformed JSON
    assert parsed[4] == []  # JSON array of non-strings
    assert parsed[5] == []  # blank string
    assert parsed[6] == []  # whitespace-only string
    assert parsed[7] == []  # null
    assert parsed[8] == []  # undefined
    assert parsed[9] == []  # a bare number
    assert parsed[10] == []  # a plain object, not an array


def test_toggle_collapsed_section_round_trips_and_never_mutates_its_input(
    cache_api: dict,
) -> None:
    toggle = cache_api["collapsedToggle"]
    assert toggle["onceCollapsed"] == ["A"]
    assert toggle["twiceCollapsed"] == []  # toggling twice round-trips to empty
    assert toggle["addInput"] == ["A"], "the original array must be untouched"
    assert toggle["addResult"] == ["A", "B"]
    assert toggle["removeInput"] == ["A", "B"], "the original array must be untouched"
    assert toggle["removeResult"] == ["B"]
    assert toggle["nonArrayInput"] == ["A"], "a non-array list reads as empty, not a throw"


def test_is_section_collapsed_is_tolerant(cache_api: dict) -> None:
    assert cache_api["collapsedIsSection"] == [True, False, False, False, False]


def test_collapsed_sections_property_is_registered_right_after_build_ui(source: str) -> None:
    """Must run before `attachNotebookWidget` returns, and specifically
    after `buildUi(state)` (state.listEl has to exist for the property
    wiring's render calls), so the wrapped onPropertyChanged is already in
    place before ComfyUI's next `node.configure()` call for a restored
    node."""
    attach = source.split("export function attachNotebookWidget(node)", 1)[1].split("\n}\n", 1)[0]
    assert "registerCollapsedSectionsProperty(state)" in attach
    assert attach.index("buildUi(state)") < attach.index("registerCollapsedSectionsProperty(state)")


def test_collapsed_sections_property_follows_the_packs_property_idiom(source: str) -> None:
    """Same shape as resolution.js/sets.js/picker.js/switcher.js's own
    per-instance UI-toggle properties: addProperty() (a silent seed that
    never fires onPropertyChanged) + a CHAINED onPropertyChanged (never
    replaced -- other wiring may already own it) + one explicit initial
    apply call so a fresh node (no configure() coming) is synced too."""
    assert "const PROP_COLLAPSED_SECTIONS = 'Collapsed sections'" in source
    reg = source.split("function registerCollapsedSectionsProperty(state)", 1)[1]
    reg = reg.split("\n}\n", 1)[0]
    assert "node.addProperty(PROP_COLLAPSED_SECTIONS, [], 'array')" in reg
    assert "const original = node.onPropertyChanged" in reg
    assert "node.onPropertyChanged = function (name, value, prevValue) {" in reg
    assert "original?.call(this, name, value, prevValue)" in reg
    assert "if (name === PROP_COLLAPSED_SECTIONS) {" in reg
    assert "applyCollapsedSectionsFromProperty(state)" in reg
    assert "renderList(state)" in reg
    # the explicit initial apply call sits OUTSIDE (after) the wrapper --
    # it must run unconditionally, not only when a property change fires.
    # The wrapper's own closing brace is the LAST "}" in `reg` (nothing after
    # it but that trailing call), so rsplit's second half is "after it".
    after_wrapper = reg.rsplit("}", 1)[1]
    assert reg.count("applyCollapsedSectionsFromProperty(state)") == 2
    assert "applyCollapsedSectionsFromProperty(state)" in after_wrapper


def test_apply_collapsed_sections_from_property_reads_through_the_tolerant_parser(
    source: str,
) -> None:
    apply_fn = _body(source, "applyCollapsedSectionsFromProperty(state)")
    assert "parseCollapsedSections(state.node.properties?.[PROP_COLLAPSED_SECTIONS])" in apply_fn
    assert "state.collapsedCategories = new Set(names)" in apply_fn


def test_sync_collapsed_sections_property_writes_and_dirties_the_canvas(source: str) -> None:
    sync = _body(source, "syncCollapsedSectionsProperty(state)")
    assert (
        "node.properties[PROP_COLLAPSED_SECTIONS] = Array.from(state.collapsedCategories)" in sync
    )
    assert "node.graph?.setDirtyCanvas(true, true)" in sync


def test_every_collapse_mutation_site_writes_through_the_property(source: str) -> None:
    """Both toggleCategoryCollapse() branches (the selecting tap that may
    reveal a collapsed header, and the already-active toggle), the
    double-click restore, and the two rename migrations (applyRenameResult's
    category branch, performSaveCategory's rename branch) all mutate
    `state.collapsedCategories` -- every one of them must also call
    syncCollapsedSectionsProperty() or a toggle would be forgotten on the
    next workflow save."""
    toggle = source.split("function toggleCategoryCollapse(state, category)", 1)[1]
    toggle = toggle.split("\n/**", 1)[0]
    assert toggle.count("syncCollapsedSectionsProperty(state)") == 2

    restore = source.split(
        "function restoreCategoryCollapseAfterDoubleClick(state, category)", 1
    )[1]
    restore = restore.split("\n}\n", 1)[0]
    assert "syncCollapsedSectionsProperty(state)" in restore

    rename_result = source.split("function applyRenameResult(", 1)[1].split("\n// ---", 1)[0]
    assert "syncCollapsedSectionsProperty(state)" in rename_result
    # still pins the two literal lines test_rename_result_updates_every_home_of_the_old_name
    # depends on -- the write-through is additive, not a replacement.
    assert "state.collapsedCategories.delete(name)" in rename_result
    assert "state.collapsedCategories.add(renameTo)" in rename_result

    save_category_start = source.index(
        "async function performSaveCategory(state, { force = false } = {})"
    )
    save_category_slice = source[save_category_start : save_category_start + 3000]
    assert "if (renameTo && state.collapsedCategories.delete(name)) {" in save_category_slice
    assert "state.collapsedCategories.add(renameTo)" in save_category_slice
    assert "syncCollapsedSectionsProperty(state)" in save_category_slice


def test_collapse_state_is_a_cache_over_the_property_not_its_own_source(source: str) -> None:
    """`state.collapsedCategories` must never be treated as authoritative on
    its own any more -- createState()'s own comment (and the file header)
    say so, and the render-time reads (renderList/buildCategoryHeaderRow)
    stay untouched `.has()` calls, unaffected by where the Set's contents
    actually come from."""
    create_state = source.split("collapsedCategories: new Set(),", 1)[0][-900:]
    assert "render-time CACHE over that property now" in create_state
    assert "applyCollapsedSectionsFromProperty()" in create_state


def test_collapse_persistence_never_reaches_for_localstorage(source: str) -> None:
    """The owner ask is specifically to persist WITH THE WORKFLOW (a node
    property, serialized with the graph) -- not a browser-local stash that
    would desync between machines or a re-imported workflow. This file must
    not introduce ANY localStorage usage to get there."""
    assert "localStorage" not in source


def test_collapsed_sections_export_list_is_additive(source: str) -> None:
    """Every export this file shipped before this round must still be
    there -- prompt_builder.js and controller.js import a subset of these
    by name (tests/test_prompt_builder_js.py, tests/test_pll_bridge_js.py)
    -- and the three collapse helpers are exported alongside them, plus the
    two delete-a-section-header wording helpers added 2026-08-25
    (tests/test_notebook_delete_category_js.py drives those under Node)."""
    pre_existing = (
        "export function attachNotebookWidget(node)",
        "export function pickServerFolder(options = {})",
        "export function notebookCacheGet(file)",
        "export function notebookCacheSet(file, payload, mtime)",
        "export function isUnchangedResponse(data)",
        "export function parsePinned(raw)",
        "export function pinnedDrift(pin, entryTextByName, libraryLoaded = true)",
        "export function pinnedBadgeText(pin, status)",
    )
    for signature in pre_existing:
        assert signature in source, signature
    added_this_round = (
        "export function parseCollapsedSections(raw)",
        "export function toggleCollapsedSection(list, name)",
        "export function isSectionCollapsed(list, name)",
        "export function describePendingCategoryDelete(state, category)",
        "export function deletedCategoryStatus(data, category)",
        "export function relativizeToLibrary(value, libraryDir)",
        "export function orderNamesByList(names, entries)",
        "export function emptyCategoryInsertIndex(entries, category, categories)",
    )
    for signature in added_this_round:
        assert signature in source, signature
    assert source.count("\nexport function ") == len(pre_existing) + len(added_this_round)


# ---------------------------------------------------------------------------
# 2026-08-26 responsiveness round -- an audited set of mid-run round-trip
# fixes: entry/category clicks now populate from the include_text=1 cache
# instead of re-fetching (findings 1/5), drag-to-reorder is optimistic
# (finding 3), multi-move/multi-delete send ONE batch request (finding 4,
# its backend half in test_routes_notebook.py), every write carries an
# opt-in api.js timeout so it can't wedge `state.busy` forever (finding 6),
# and the Open folder click acknowledges at once (finding 7).
# ---------------------------------------------------------------------------


def test_finding1_entry_click_is_cache_served(source: str) -> None:
    """HIGH: a plain click used to GET an entry it already had the text for
    (the SAME include_text=1 list load that painted the row), and the row
    highlight (synchronous) visibly outran the textarea (the round trip)
    every time. loadEntryText() now populates synchronously from that same
    cache whenever the name is already known; the GET survives only as a
    fallback for a name genuinely absent from it."""
    assert "function hasCachedEntryText(state, name) {" in source
    assert (
        "Object.prototype.hasOwnProperty.call(state.entryTextByName, name)"
        in _body(source, "hasCachedEntryText(state, name)")
    )
    body = _body(source, "loadEntryText(state, name)")
    assert "if (hasCachedEntryText(state, name)) {" in body
    cached = body.split("if (hasCachedEntryText(state, name)) {", 1)[1].split("\n  }", 1)[0]
    assert (
        "populateEditor(state, state.entryTextByName[name], state.paintedMtime, name)" in cached
    )
    assert "return 'ok'" in cached
    assert "await fetchEntry(state, name)" in body, "the GET must survive as a fallback"


def test_finding5_category_click_is_cache_served(source: str) -> None:
    """`category_descriptions` now rides the same include_text=1 payload
    (routes_notebook.py), and loadCategoryDescription() reads it the exact
    same cache-first way loadEntryText() reads entry bodies."""
    assert "function hasCachedCategoryDescription(state, name) {" in source
    body = _body(source, "loadCategoryDescription(state, name)")
    assert "if (hasCachedCategoryDescription(state, name)) {" in body
    cached = body.split("if (hasCachedCategoryDescription(state, name)) {", 1)[1]
    cached = cached.split("\n  }", 1)[0]
    assert (
        "populateEditor(state, state.categoryDescriptionByName[name], state.paintedMtime, name)"
        in cached
    )
    assert "await fetchCategory(state, name)" in body, "the GET must survive as a fallback"


def test_finding5_category_descriptions_ride_the_include_text_payload(source: str) -> None:
    apply_body = _body(source, "applyNotebookPayload(state, file, data)")
    assert "data.category_descriptions" in apply_body
    assert "state.categoryDescriptionByName =" in apply_body
    assert "categoryDescriptionByName: {}," in source  # createState() default


def test_finding5_category_cache_stays_current_across_writes(source: str) -> None:
    # Mirrors the entry-text-cache discipline test_notebook_search_js.py
    # already pins (test_mutations_keep_the_search_corpus_current) -- the
    # description cache must never go stale relative to what was written.
    assert "noteCategoryDescription(state, name, '')" in _body(
        source, "confirmNewCategory(state, name)"
    )
    assert "forgetCategoryDescription(state, category)" in _body(
        source, "performDeleteCategory(state, { force = false } = {})"
    )
    save_cat = _body(source, "performSaveCategory(state, { force = false } = {})")
    assert "noteCategoryDescription(state, renameTo || name, storedDescription)" in save_cat
    assert "renameCategoryDescription(state, name, renameTo)" in _body(
        source, "applyRenameResult(state, kind, name, renameTo, data)"
    )


def test_finding2_rename_reads_the_cache_not_a_fresh_get(source: str) -> None:
    # Companion to test_inline_rename_is_rename_only_and_never_writes_the_body
    # above -- pinned here as its own finding: one request, not two.
    entry_req = _body(source, "renameEntryRequest(state, name, renameTo, force)")
    assert "state.entryTextByName[name] ?? ''" in entry_req
    assert "fetchEntry" not in entry_req
    assert "state.paintedMtime === 'number'" in entry_req
    cat_req = _body(source, "renameCategoryRequest(state, name, renameTo, force)")
    assert "state.categoryDescriptionByName[name] ?? ''" in cat_req
    assert "fetchCategory" not in cat_req


def test_finding3_move_is_optimistic_before_the_request(source: str) -> None:
    """HIGH: a drag-drop used to sit at its OLD position until the response
    answered ("snapped back until the server answers"). The panel's own
    `entries`/`categories` are now spliced to the intended result BEFORE
    `state.busy`/the request, and renderList() repaints at once."""
    cases = (
        (
            "performMove(state, name, target, { force = false } = {})",
            "state.entries = reorderEntriesLocally(state.entries, name, target, state.categories)",
        ),
        (
            "performMoveRun(state, names, target, { force = false } = {})",
            "state.entries = reorderEntriesLocallyMany(state.entries, names, target, state.categories)",
        ),
        (
            "performMoveCategory(state, category, target, { force = false } = {})",
            "state.categories = reorderCategoriesLocally(state.categories, category, target)",
        ),
    )
    for signature, reorder_call in cases:
        block = _body(source, signature)
        assert reorder_call in block, signature
        reorder_at = block.index(reorder_call)
        render_at = block.index("renderList(state)")
        busy_at = block.index("state.busy = true")
        assert reorder_at < render_at < busy_at, signature


def test_finding3_rollback_reuses_the_existing_reload_paths(source: str) -> None:
    """No NEW rollback logic: a failed move already fell back to reloadNow()
    (the non-409 branch) or the Reload/Overwrite conflict UI (the 409
    branch) before this round; both still restore disk truth over the
    optimistic guess -- see the finding's "on failure roll back through the
    existing conflict/error reload paths" instruction."""
    for signature in (
        "performMove(state, name, target, { force = false } = {})",
        "performMoveRun(state, names, target, { force = false } = {})",
        "performMoveCategory(state, category, target, { force = false } = {})",
    ):
        block = _body(source, signature)
        assert "onReload: () => reloadNow(state)" in block, signature
        assert "await reloadNow(state)" in block, signature


def test_finding3_category_move_relocates_its_entries_block_too(source: str) -> None:
    # renderList() walks `entries` in lockstep with `categories`' own order,
    # so reordering `categories` alone (without moving that category's own
    # entries) would visually scramble the list until the response landed.
    body = _body(source, "reorderCategoryEntriesLocally(entries, category, target)")
    assert "entries.filter((entry) => (entry.category || '') === category)" in body
    assert "if (!moved.length) return entries" in body


def test_finding4_move_run_sends_one_batch_request(source: str) -> None:
    """performMoveRun used to loop `api.postJson` once per name; it now
    sends ONE request carrying the whole `names` array (the backend batch
    half is in test_routes_notebook.py)."""
    body = _body(source, "performMoveRun(state, names, target, { force = false } = {})")
    assert "const body = { file: state.file, names }" in body
    assert body.count("api.postJson(") == 1


def test_finding4_delete_run_sends_one_batch_request(source: str) -> None:
    body = _body(source, "performDeleteRun(state, names, { force = false } = {})")
    assert "const body = { file: state.file, names }" in body
    assert body.count("api.postJson(") == 1


def test_finding4_no_resume_at_index_left_anywhere(source: str) -> None:
    # All-or-nothing batch semantics mean there is nothing left to "resume
    # at index" -- Overwrite just retries the SAME full `names` list.
    move_run = _body(source, "performMoveRun(state, names, target, { force = false } = {})")
    assert "onOverwrite: () => performMoveRun(state, names, target, { force: true })" in move_run
    delete_run = _body(source, "performDeleteRun(state, names, { force = false } = {})")
    assert "onOverwrite: () => performDeleteRun(state, names, { force: true })" in delete_run
    assert "startIndex" not in source


def test_finding4_call_sites_no_longer_pass_a_start_index(source: str) -> None:
    assert "performMoveRun(state, names, target).catch(" in source
    assert "performMoveRun(state, names, target, 0)" not in source
    assert "performDeleteRun(state, [...state.selection]).catch(" in source
    assert "performDeleteRun(state, [...state.selection], 0)" not in source


def test_finding6_write_timeout_constant_and_recovery_helper(source: str) -> None:
    assert "const WRITE_TIMEOUT_MS = 30000" in source
    body = _body(source, "recoverFromWriteTimeout(state, error)")
    assert "if (!error?.timeout) return false" in body
    assert "await reloadNow(state)" in body
    assert "return true" in body


def test_finding6_every_write_path_passes_the_timeout_and_checks_it_first(source: str) -> None:
    # Every notebook WRITE must (a) pass { timeoutMs: WRITE_TIMEOUT_MS } to
    # its api.postJson call and (b) check recoverFromWriteTimeout() in its
    # catch block, ahead of the usual 409 branch.
    for signature in (
        "performSave(state, { force = false } = {})",
        "performSaveCategory(state, { force = false } = {})",
        "confirmNewEntry(state, rawName)",
        "confirmNewCategory(state, name)",
        "performDeleteRun(state, names, { force = false } = {})",
        "performMove(state, name, target, { force = false } = {})",
        "performMoveRun(state, names, target, { force = false } = {})",
        "performMoveCategory(state, category, target, { force = false } = {})",
        "performDeleteCategory(state, { force = false } = {})",
        "renameEntryRequest(state, name, renameTo, force)",
        "renameCategoryRequest(state, name, renameTo, force)",
    ):
        block = _body(source, signature)
        assert "timeoutMs: WRITE_TIMEOUT_MS" in block, signature
    # The rename request functions don't handle their own errors -- their
    # shared caller does, in ONE catch block covering both.
    commit = _body(source, "commitInlineRename(state)")
    assert "recoverFromWriteTimeout(state, error)" in commit
    for signature in (
        "performSave(state, { force = false } = {})",
        "performSaveCategory(state, { force = false } = {})",
        "confirmNewEntry(state, rawName)",
        "confirmNewCategory(state, name)",
        "performDeleteRun(state, names, { force = false } = {})",
        "performMove(state, name, target, { force = false } = {})",
        "performMoveRun(state, names, target, { force = false } = {})",
        "performMoveCategory(state, category, target, { force = false } = {})",
        "performDeleteCategory(state, { force = false } = {})",
    ):
        assert "recoverFromWriteTimeout(state, error)" in _body(source, signature), signature


def test_finding6_timeout_check_precedes_the_409_branch(source: str) -> None:
    # An aborted fetch never carries `.status`, so the timeout check has to
    # run BEFORE the 409 branch or a timeout could fall through and be
    # misreported as a generic failure.
    for signature in (
        "performSave(state, { force = false } = {})",
        "performMove(state, name, target, { force = false } = {})",
        "performDeleteRun(state, names, { force = false } = {})",
    ):
        block = _body(source, signature)
        timeout_at = block.index("recoverFromWriteTimeout(state, error)")
        conflict_at = block.index("error?.status === 409")
        assert timeout_at < conflict_at, signature


def test_finding7_open_folder_acknowledges_the_click_at_once(source: str) -> None:
    body = _body(source, "onOpenFolderClick(state)")
    assert "setStatus(state, 'Opening folder…')" in body
    status_at = body.index("setStatus(state, 'Opening folder…')")
    request_at = body.index("api.postJson('/lora_library/notebook/open_folder'")
    assert status_at < request_at


# --------------------------------------- api.js: opt-in timeout (finding 6)


API_SOURCE = API_JS.read_text(encoding="utf-8")

API_TIMEOUT_PROBE_JS = """
import { getJson, postJson } from './extensions/comfyui-epsnodes/lora_library/api.js'
import { api } from './scripts/api.js'

const calls = []

function abortableHang(path, options) {
  calls.push({ path, options })
  return new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => {
      const err = new Error('The operation was aborted')
      err.name = 'AbortError'
      reject(err)
    })
  })
}

function instantOk(path, options) {
  calls.push({ path, options })
  return Promise.resolve({ ok: true, status: 200, json: async () => ({ ok: true }) })
}

const out = {}

// Default path, no options: byte-identical call shape to before this round.
api.fetchApi = instantOk
calls.length = 0
await getJson('/x', { a: '1' })
out.getDefaultArgCount = calls[0].options === undefined ? 1 : 2
out.getDefaultHasSignal = calls[0].options !== undefined && 'signal' in calls[0].options

calls.length = 0
await postJson('/y', { b: 2 })
out.postDefaultOptions = {
  method: calls[0].options.method,
  hasHeaders: !!calls[0].options.headers,
  hasBody: typeof calls[0].options.body === 'string',
  hasSignal: 'signal' in calls[0].options
}

// Opt-in timeout: a hanging fetchApi eventually rejects with .timeout = true,
// and the underlying call DID carry an AbortSignal.
api.fetchApi = abortableHang
calls.length = 0
try {
  await postJson('/z', { c: 3 }, { timeoutMs: 30 })
  out.postTimeoutResult = 'resolved (BUG)'
} catch (error) {
  out.postTimeoutResult = { timeout: error.timeout === true, hasMessage: !!error.message }
}
out.postTimeoutCallHadSignal = calls[0].options.signal instanceof AbortSignal

calls.length = 0
try {
  await getJson('/w', undefined, { timeoutMs: 20 })
  out.getTimeoutResult = 'resolved (BUG)'
} catch (error) {
  out.getTimeoutResult = { timeout: error.timeout === true }
}

// A NON-timeout error (e.g. a genuine network failure) must not be
// mislabeled -- .timeout stays unset/false.
api.fetchApi = () => Promise.reject(new Error('network down'))
try {
  await postJson('/v', {}, { timeoutMs: 5000 })
  out.genuineErrorResult = 'resolved (BUG)'
} catch (error) {
  out.genuineErrorResult = { timeout: Boolean(error.timeout), message: error.message }
}

process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def api_timeout_probe(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Runs API_TIMEOUT_PROBE_JS against the REAL api.js in a served-layout
    tmp dir (same convention as `cache_api` above) -- finding 6's opt-in
    timeout, exercised for real under Node rather than only source-pinned."""
    if NODE is None:
        pytest.skip("node (JS runtime) not installed")
    layout = tmp_path_factory.mktemp("api_timeout_web_root")
    module_dir = layout / "extensions" / "comfyui-epsnodes" / "lora_library"
    module_dir.mkdir(parents=True)
    for src in (API_JS, VERSION_JS):
        shutil.copyfile(src, module_dir / src.name)
    scripts = layout / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "api.js").write_text(
        "export const api = { fetchApi: () => {}, apiURL: (p) => p, addEventListener: () => {} }\n",
        encoding="utf-8",
    )
    (scripts / "app.js").write_text("export const app = {}\n", encoding="utf-8")
    probe = layout / "probe.mjs"
    probe.write_text(API_TIMEOUT_PROBE_JS, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(probe)], capture_output=True, text=True, timeout=60, cwd=layout
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


def test_api_default_calls_are_byte_identical_to_before_this_round(
    api_timeout_probe: dict,
) -> None:
    """The proof-of-no-default-change: with no `options` argument,
    `getJson` still calls `api.fetchApi(path)` with no second argument at
    all (not even `undefined` wrapped in a new object), and `postJson`
    still sends exactly its old three-key body -- no `signal`."""
    assert api_timeout_probe["getDefaultArgCount"] == 1
    assert api_timeout_probe["getDefaultHasSignal"] is False
    assert api_timeout_probe["postDefaultOptions"] == {
        "method": "POST",
        "hasHeaders": True,
        "hasBody": True,
        "hasSignal": False,
    }


def test_api_opt_in_timeout_aborts_and_marks_the_error(api_timeout_probe: dict) -> None:
    assert api_timeout_probe["postTimeoutResult"] == {"timeout": True, "hasMessage": True}
    assert api_timeout_probe["postTimeoutCallHadSignal"] is True
    assert api_timeout_probe["getTimeoutResult"] == {"timeout": True}


def test_api_genuine_errors_are_never_mislabeled_as_timeouts(api_timeout_probe: dict) -> None:
    result = api_timeout_probe["genuineErrorResult"]
    assert result["timeout"] is False
    assert result["message"] == "network down"


def test_api_source_states_the_no_default_change_guarantee(api_timeout_probe: dict) -> None:
    # Belt-and-braces source pin alongside the behavioral probe above.
    assert "DEFAULT BEHAVIOR IS UNCHANGED" in API_SOURCE
    assert "if (typeof timeoutMs !== 'number') return api.fetchApi(path, fetchOptions)" in (
        API_SOURCE
    )


# ---------------- v0.84.0: picks inside the library are stored RELATIVE


class TestRelativizeToLibrary:
    """The forward half of the cross-OS path fix (FORMAT.md §2): a file
    picked inside the library folder is stored relative, so the workflow
    opens on the other machine with no healing needed at all."""

    def test_paths_inside_the_library_become_relative(self, cache_api: dict) -> None:
        r = cache_api["relativize"]
        assert r["inside"] == "x.md"
        assert r["nested"] == "sub/x.md"
        assert r["windows"] == "x.md"
        assert r["trailingSlashBase"] == "x.md"

    def test_paths_outside_the_library_are_untouched(self, cache_api: dict) -> None:
        r = cache_api["relativize"]
        assert r["outside"] == "/elsewhere/x.md"
        # boundary safety: /lib2 is NOT inside /lib
        assert r["siblingPrefix"] == "/lib2/x.md"
        assert r["dirItself"] == "/lib/docs"

    def test_degenerate_inputs_pass_through(self, cache_api: dict) -> None:
        r = cache_api["relativize"]
        assert r["alreadyRelative"] == "x.md"
        assert r["noBase"] == "/lib/docs/x.md"
        assert r["empty"] == ""

    def test_the_pick_path_relativizes_before_storing(self, source: str) -> None:
        assert "function setFileWidgetValue(state, rawValue)" in source
        assert "relativizeToLibrary(rawValue, state.libraryDir)" in source


# ---------------- v0.85.0: selection is stored in LIST order


class TestOrderNamesByList:
    """Owner report 2026-08-28: run tokens followed click order, so the
    image order didn't match the list. The panel now stores the selection
    in list order (and `read_entry` sorts server-side for already-saved
    workflows)."""

    def test_click_order_becomes_list_order(self, cache_api: dict) -> None:
        o = cache_api["orderByList"]
        assert o["reordered"] == ["A", "B", "C"]
        assert o["subset"] == ["A", "C"]

    def test_degenerate_inputs_are_safe(self, cache_api: dict) -> None:
        o = cache_api["orderByList"]
        assert o["single"] == ["B"]
        assert o["empty"] == []
        assert o["nonArray"] == []
        assert o["noEntries"] == ["B", "A"]  # nothing to rank against

    def test_unknown_names_are_kept_last_never_dropped(self, cache_api: dict) -> None:
        # A stale selection mid-reload must not silently lose entries.
        assert cache_api["orderByList"]["unknownGoesLast"] == ["A", "B", "Ghost"]

    def test_set_selection_orders_before_storing(self, source: str) -> None:
        assert "state.selection = orderNamesByList(names, state.entries)" in source


# ---------------- v0.85.1: dropping into an EMPTY group


class TestEmptyCategoryDropPlacement:
    """Owner report 2026-08-28: "if the top group has nothing in it you
    can't drag new items into it -- they always end up in the last group."
    The optimistic paint fell back to the end of the whole list whenever
    the target category had no entries to append after; the server always
    placed it correctly, which is why the workaround (move the group down,
    drag, move it back) appeared to help."""

    def test_empty_group_gets_its_own_slot_not_the_end(self, cache_api: dict) -> None:
        i = cache_api["emptyCategoryInsert"]
        assert i["emptyTop"] == 0  # the bug: this used to be 3 (end of list)
        assert i["emptyMiddle"] == 1
        assert i["emptyLast"] == 2

    def test_head_region_sorts_first_and_unknowns_last(self, cache_api: dict) -> None:
        i = cache_api["emptyCategoryInsert"]
        assert i["headRegion"] == 0
        assert i["unknown"] == 3
        assert i["noCategoryList"] == 3  # no order to reason about -> old fallback

    def test_move_paths_pass_the_category_order(self, source: str) -> None:
        assert (
            "reorderEntriesLocally(state.entries, name, target, state.categories)" in source
        )
        assert (
            "reorderEntriesLocallyMany(state.entries, names, target, state.categories)"
            in source
        )

    def test_a_refused_move_rolls_the_optimistic_reorder_back(self, source: str) -> None:
        # A 409 used to leave the row sitting at its new slot while the
        # banner said the file changed -- the panel showing a move the
        # server had refused.
        assert source.count("const entriesBeforeMove = state.entries") == 2
        assert source.count("state.entries = entriesBeforeMove") == 2

    def test_every_write_response_refreshes_both_mtimes(self, source: str) -> None:
        # baseMtime (what writes send) and paintedMtime (what the cached
        # paint reads) drifting apart is what produced spurious
        # "file changed on disk" refusals.
        assert "function noteFileMtime(state, mtime)" in source
        assert "state.baseMtime = mtime" in source
        assert "state.paintedMtime = mtime" in source
        assert "state.baseMtime = typeof data.mtime" not in source
