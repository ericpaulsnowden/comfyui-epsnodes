/**
 * fs-browse dialog v1 — synced from STANDARD-fs-browse.md
 *
 * @file EPS Prompt Notebook two-pane DOM widget (FORMAT.md §7.2) — attaches to
 * `LoraLibraryNotebook` nodes. Left pane: a scrollable, category-grouped,
 * multi-selectable, drag-to-reorder (including a multiselect dragged as one
 * block, and a whole category dragged by its header) entry list — with
 * CLICKABLE, COLLAPSIBLE category headers (single tap toggles collapse AND
 * selects; incl. empty categories — see "Categories" below) and a ＋ New
 * control that creates either an entry (landing directly below the active
 * one — "New-below" below) or (given a `#`-prefixed name) a category, plus
 * 🗑 Delete (entry-only). Right pane: a NAME field (the primary rename
 * control — see "Rename via the editor's name field" below) above a
 * `<textarea>` editor, a Save button, and a status line (conflict
 * resolution per §3.5 lands there too) — both fields are CONTEXTUAL, entry
 * body/name or category description/name, per whichever was last clicked,
 * with a mode hint saying which. Above both panes, a file panel — now the
 * ONLY visible file control, full-width — shows the notebook's RESOLVED
 * absolute path plus Browse…/Open folder buttons; the raw `file` STRING
 * widget itself is hidden outright (`.hidden`, not merely read-only) since
 * this panel replaces it, and both panel buttons hide (and the panel
 * becomes read-only in effect) for a remote (`is_local: false`) viewer, whose
 * host-machine notice lives on its own line, never inline. The node's own
 * `file`/`entry` STRING widgets stay the serialized truth (§6.1/§7.2) — this
 * DOM widget only ever *reads* `file` and *writes* `entry`/`file` through
 * their normal widget setters; it never serializes itself, and neither does
 * the left list's collapse state (session/per-node UI only — see "Single-tap
 * collapse" below).
 *
 * Multi-select (FORMAT.md §6.1/§7.2, owner amendment 2026-07-18): ctrl/
 * cmd+click toggles one entry in/out of the selection, shift+click extends
 * the visible range from the ACTIVE entry (the most recently clicked one),
 * and a plain click collapses to a single selection. `entry` serializes the
 * whole selection as one name per line, in selection order (§6.1) — the
 * ACTIVE entry alone drives the editor pane, dirty tracking, Save, Delete,
 * and conflict handling, exactly like the single-select behavior this file
 * always had. See the "Selection model" section below the state helpers.
 *
 * Drag-to-reorder (FORMAT.md §3.4/§5/§7.2, owner amendment 2026-07-18):
 * pointer-based row dragging (deliberately not HTML5 drag-and-drop — see the
 * pointer-events bullet below) with an insertion-line marker, committed as
 * one `POST /lora_library/notebook/move`. See the "Drag-to-reorder" section
 * below the selection helpers.
 *
 * Frontend APIs relied on here (verified against a `Comfy-Org/ComfyUI_frontend`
 * checkout — see the notebook-frontend handoff notes for exact file:line
 * references):
 *  - `LGraphNode.prototype.addDOMWidget(name, type, element, options)` —
 *    present for both the legacy canvas renderer and the Vue-node renderer
 *    (`scripts/domWidget.ts`), which is why it is used instead of any
 *    renderer-specific API.
 *  - `options.getMinHeight` and the *absence* of `options.getMaxHeight` —
 *    litegraph's widget-arrange pass (`LGraphNode._arrangeWidgets`) gives
 *    DOM widgets whatever vertical space is left after fixed-height widgets
 *    (`file`, `entry`) via `distributeSpace()`; an unset max means "take all
 *    remaining space", which is exactly "the widget fills available height".
 *  - `node.comfyClass` — ComfyUI's node-registration step
 *    (`services/litegraphService.ts`) sets `comfyClass` on *both* the node
 *    class's `.prototype` and the class itself, specifically so extensions
 *    can feature-detect a node's Python class id from `nodeCreated`; this is
 *    the same mechanism core extensions (e.g. `extensions/core/load3d.ts`)
 *    use.
 *  - Excluding a DOM widget from serialization has two independent knobs:
 *    `widget.serialize = false` (workflow JSON — checked by
 *    `LGraphNode.serialize`/`.configure`) and `widget.options.serialize =
 *    false` / `widget.serializeValue = () => undefined` (the API prompt sent
 *    for execution — checked by `utils/executionUtil.ts`). All three are set
 *    below so the widget never serializes under either mechanism, matching
 *    core's own idiom (e.g. `extensions/core/webcamCapture.ts`:
 *    `btn.serializeValue = () => undefined`).
 *  - Pointer events over a DOM widget are NOT swallowed by the litegraph
 *    canvas underneath it, which is why drag-to-reorder below uses plain
 *    pointerdown/move/up instead of HTML5 DnD (owner's ask, since native DnD
 *    is known to fight canvas-level handlers):
 *    `src/components/graph/GraphCanvas.vue` mounts `<canvas
 *    id="graph-canvas">` (line 58) and `<DomWidgets>` (line 113) as DOM
 *    SIBLINGS in the same template, never nested. A pointerdown that targets
 *    our widget's elements therefore never passes through the `<canvas>`
 *    element at all, so litegraph's own capture-phase handler
 *    (`LGraphCanvas.ts:2026`: `canvas.addEventListener('pointerdown',
 *    this._mousedown_callback, true)`) structurally cannot see it — capture
 *    phase only intercepts events whose target is a descendant of the
 *    listener's element, and a DOM sibling is not a descendant. On top of
 *    that, `src/components/graph/widgets/DomWidget.vue` (lines 109-113)
 *    inline-styles `pointerEvents: 'auto'` on the widget wrapper whenever it
 *    is visible, not read-only, and not disabled (the normal editing state)
 *    — that's what makes the browser hit-test to our DOM content instead of
 *    falling through to the canvas visually underneath it. (The only other
 *    capture-phase `document`-level pointerdown listener found,
 *    `useNodeDragToCanvas.ts:125`, only activates mid "drag a new node from
 *    the library onto the canvas" and no-ops otherwise, so it doesn't
 *    interfere either.) This file's pre-existing pane-splitter drag
 *    (`wireSplitter`, unchanged below) already exercised this exact
 *    pointer-event path live before today's change — drag-to-reorder reuses
 *    the identical technique.
 *
 * Multi-delete (FORMAT.md §7.2 amendment, owner 2026-07-18c): Delete now
 * removes EVERY selected entry, not just the active one. The confirm label
 * shows the count when >1 ("Are you sure? (3)"); deletion is sequential
 * over the existing single-entry §5 delete route (one request per name, in
 * selection order), refreshing `base_mtime` from each response so later
 * requests in the same run check against the file's latest state. A
 * mid-run 409 stops the run and surfaces the same Reload/Overwrite
 * conflict UI Save/Move already use; Overwrite resumes the run from the
 * failed name with that one request forced (base_mtime omitted), then
 * continues normally. See performDeleteRun() below.
 *
 * Renaming — TWO paths, both of them the owner's stated expectation
 * (2026-07-29: "my expectation is i can either double click a name to rename
 * in place, or if I change the name at the top of the notebook the item
 * becomes savable and I can save with the new name"). The name field below is
 * the second; the first is the row-level inline editor (beginInlineRename()/
 * commitInlineRename(), in the "Rename in place" section further down).
 * v0.10.0 shipped an inline rename, it was reported not working, and v0.12.0
 * REMOVED it in favour of the name field alone rather than root-causing it —
 * which is why the same request arrived a second time. See
 * onEntryDoubleClick()'s history note for the two structural reasons that
 * original was fragile and how the new one addresses each.
 *
 * Rename via the editor's name field (FORMAT.md §7.2 amendment, owner ask
 * 2026-07-19): a NAME field sits at the top of the right pane,
 * above the mode hint (buildUi()'s `state.nameFieldEl`). It always shows
 * the currently-active item's name (entry or category — populateEditor()
 * sets it alongside the textarea, so the two never drift apart) and is
 * plain-editable text; editing it dirties the SAME Save button the
 * textarea does (refreshDirty() ORs both), and Save (performSave()/
 * performSaveCategory()) sends the field's value as `rename_to` in the
 * very same request as the body/description write WHEN it differs from
 * the active name — one request does both, atomically, whenever both
 * changed. Duplicate names are refused client-side first (checked against
 * `state.entries`/`state.categories`), server authoritative same as every
 * other write in this file. Double-clicking a row opens the inline editor on
 * that row (2026-07-29); focusNameField() is the fallback for when there is
 * no rendered row to edit. Both rely on the SAME click-vs-drag-vs-double-click
 * disambiguation already documented for onEntryPointerDown below: the two
 * single-clicks that precede a dblclick resolve to a selection change first,
 * so the row is already the active item by the time either editor opens.
 *
 * File panel (FORMAT.md §7.2 amendment, reworked 2026-07-19 — "why trim at
 * all, make it full width; what's the point of the file field at top,
 * replace it with this"): the raw `file` STRING widget is hidden outright
 * via `widget.hidden = true` (hideFileWidget(), called once at attach) —
 * the same first-class litegraph layout primitive controller.js's "Show
 * status" toggle uses (see that file's header for the citations backing
 * this: `.hidden` pulls a widget out of drawing AND layout AND size, unlike
 * `.disabled`, which on this fork blanks a disabled TEXT widget's value
 * outright instead of graying it out — wrong for a widget that must keep
 * serializing the node's real value). This panel — a muted bar between the
 * node's own widgets and the two panes — is what replaces it as the visible
 * file control: a full-width row shows the notebook's RESOLVED absolute
 * path (the `file` field of every `GET /notebook` response — NOT the
 * `file` WIDGET's possibly-relative value) plus `Browse…`/`Open folder`;
 * updateFilePanelPath() sets the FULL path first and only front-truncates
 * (`frontTruncate()`, unchanged — keeps the tail, usually the filename,
 * visible instead of the head) once a real DOM overflow check
 * (`scrollWidth > clientWidth`) says it genuinely doesn't fit at the bar's
 * CURRENT width, re-checked on resize via a `ResizeObserver` — never at a
 * fixed character budget regardless of the node's actual size. The full
 * path always sits in `title`. `Browse…` opens a small modal file picker
 * (attached to `document.body`, not nested inside this widget's own root —
 * see openBrowsePicker()'s doc comment for why) walking `GET /fs/list`;
 * `Open folder` fires `POST /notebook/open_folder` and reports failure on
 * the status line.
 *
 * Remote gating: `GET /config`'s `is_local` (fetched once per attach,
 * cached at MODULE scope with a short TTL so N attached nodes share one
 * fetch) hides both file-panel buttons and makes the panel's path
 * effectively read-only for a remote (non-loopback) viewer — the panel is
 * a `<div>`, not an input, so "read-only" here just means there's no
 * control left that could change `file`. The host-machine notice
 * ("the host controls which file this node reads") lives on its OWN line
 * below the path/buttons row (`state.filePanelNoteEl`, its own block, not
 * squeezed inline the way it used to be) and is populated ONLY when
 * `is_local === false` — the element is empty (and `:empty { display:
 * none }` collapses it to zero height) on every local load, never shown
 * "just in case." The `file` widget's callback (already wrapped by
 * wireFileWidget below) additionally reverts any programmatic edit back to
 * the last known-good value for a remote viewer and posts that same calm
 * status note — belt-and-suspenders now that hideFileWidget() means no UI
 * surface should be able to trigger that edit at all. Every other feature
 * in this file (browsing/editing/saving/deleting/renaming/reordering
 * entries) stays fully functional for a remote viewer — only the FILE the
 * node points at is host-controlled.
 *
 * New-below (owner ask 2026-07-19 "New makes an entry right below the
 * selected one"): confirmNewEntry() passes `after: state.activeName` to
 * `POST /notebook/entry` whenever an ENTRY is active (category mode off);
 * with nothing active, or a CATEGORY active, it keeps the old end-of-file/
 * end-of-category append (the server falls back to that same append on an
 * omitted/unresolvable `after` regardless, so this is a request-shaping
 * choice, not a safety one).
 *
 * Browse picker drive/UNC + path input (FORMAT.md §5 fix, owner's NAS
 * case): the picker's `..` row already forwards whatever `parent` the
 * server reports, which — since routes.py's `fs/list` now reports
 * `parent: "ROOTS"` at a Windows drive root and `parent: null` only at an
 * actual top (no sibling to climb to, e.g. a UNC share root or POSIX `/`)
 * — already climbs correctly and already hides itself at a true top; the
 * one real bug fixed here is navigating a drive-list ENTRY (`C:\`, listed
 * when `dir` is the `FS_ROOTS` sentinel): that name is already a complete
 * root, so it must be opened AS-IS, never joined onto the literal `"ROOTS"`
 * string like a normal child (joinServerPath() would produce the nonsense
 * path `"ROOTS/C:\"` — see renderPickerDialog()). A "type or paste a path"
 * input pinned above the listing (openBrowsePicker()) accepts any absolute
 * path, including a UNC share (`\\server\share`), on Enter/Go, and a
 * failed lookup (400) reports inline right under that input — via its own
 * `pathErrorEl`, never by blanking the whole dialog — leaving the picker
 * open so the user can just fix the path and retry; every OTHER navigation
 * (a folder row, the back row, the drive list) now reports through that
 * same inline slot for the same reason, unifying what used to be a
 * separate whole-dialog error view.
 *
 * STANDARDIZED 2026-07-19 (../../STANDARD-fs-browse.md, the cross-plugin
 * "server filesystem Browse" contract shared with cpsb's/cprb's own
 * pickers): `GET /lora_library/fs/list` reshaped its response to NAMES-ONLY
 * `dirs`/`files` entries (`{"name"}` / `{"name","size","mtime"}`) plus a
 * `sep` field — this picker now joins `dir` + `sep` + `name` itself
 * (`joinServerPath()`, now preferring the server-reported `sep` over its old
 * heuristic) instead of receiving bare path strings. The `ROOTS` sentinel's
 * listing keeps its drive-list role but now ALSO includes this pack's own
 * default library dir and "Home" (labeled, `{"name","path"}` entries —
 * STANDARD-fs-browse.md's documented ROOTS extension) ahead of the platform
 * drives/volumes, and (2026-07-19) is synthesized on macOS/POSIX too, not
 * just Windows. Locality: epsnodes' `FS_LIST_LOCAL_ONLY` build-time flag
 * stays `True` (unchanged loopback-only posture, FORMAT.md §5).
 *
 * Categories (FORMAT.md §7.2 amendment, owner ask 2026-07-19): typing a name
 * STARTING WITH `#` into the ＋ New row creates a CATEGORY instead of an
 * entry (POST `/notebook/category`; the `#`s + surrounding whitespace are
 * stripped from the stored name — see isCategoryNameInput()/
 * categoryNameFromInput()). Category headers, rendered from the §5
 * `categories` list rather than derived from `entries` (so an EMPTY
 * category still shows — see renderList()'s two-pointer merge of
 * `categories` and `entries`, both already in file order), are CLICKABLE:
 * selecting one (selectCategory()) enters "category mode" —
 * `state.activeCategory` holds its name, the header highlights, and the
 * SAME editor pane/textarea/Save button/dirty-tracking/base_mtime-conflict
 * machinery entry-editing already used now targets that category's §3.1
 * description (GET/POST `/notebook/category`) instead — see
 * performSaveCategory(), the category-mode sibling of performSave().
 * `state.modeHintEl` (a muted line directly above the textarea) always says
 * which of the two the editor currently targets. Category mode is
 * deliberately UI-only: it is never allowed to touch `state.selection`,
 * `state.activeName`, the `entry` widget, or multi-select — clicking an
 * entry always exits it (chooseSelection() clears `activeCategory`, and
 * reloads the entry pane even if the clicked entry was already the
 * "active" one underneath category mode) and clicking a header always
 * enters it, but neither path ever calls setSelection()/syncEntryWidget().
 * Delete is CONTEXTUAL, the one exception to category mode's "UI-only"
 * rule and the only §3.4 write that can remove a category heading (owner
 * report 2026-08-25, "you can't delete section headers"): with a header
 * active, Delete deletes THAT HEADER over §5's `delete_category` route
 * (performDeleteCategory()) instead of the entry selection, which it never
 * reads. It is deliberately non-destructive — the heading line goes and its
 * §3.1 description plus every entry it held merge into the block above it
 * (the uncategorized head region when it was the first category), so the
 * gesture is "merge this section upward", never "delete these entries".
 * That is what makes it safe to give a two-click confirm and no more; the
 * armed status line names the destination and the count first
 * (describePendingCategoryDelete()), and the result line repeats them from
 * the server's own answer (deletedCategoryStatus()). It exists because
 * nothing else could remove a heading: deleting a category's last ENTRY
 * deliberately leaves the emptied heading in place (FORMAT.md §3.4,
 * "categories are the user's prose, not derived state"), which left a
 * header the panel could never get rid of afterwards. Double-clicking a
 * header still opens the inline rename editor on the header itself, exactly
 * like double-clicking an entry row does (see "Renaming — TWO paths"
 * above).
 *
 * A SECOND entry point onto that same performDeleteCategory() sits on the
 * header itself (owner report 2026-08-25, UI parity with the Lora State
 * Controller's group headers: "When a group is closed in the state
 * controller it has an x to delete and shows a number of items in the
 * group. This should be consistent in the prompt library as well."):
 * buildCategoryHeaderRow() shows `(N)` — categoryEntryCount() — next to a
 * collapsed header's name, and buildCategoryDeleteButton() adds an inline ✕
 * with its own local two-click arm/confirm (independent of the bottom
 * Delete button's `state.deleteConfirmActive`), matching
 * controller.js's `_buildCategoryHeader()` down to the class names
 * (`llnb-category-label`/`-delete`/`-delete-armed`, that file's
 * `llsc-` twins) and the CATEGORY_DELETE_CONFIRM_MS/DELETE_CONFIRM_MS
 * timeout (both 4000ms). Confirming it sets `state.activeCategory` to that
 * header's category and calls performDeleteCategory() directly — no second
 * request path, no divergent wording.
 *
 * Single-tap collapse (owner ask 2026-07-19 "single tap category name to
 * collapse category"): a plain tap on a header now does TWO things at once
 * — toggleCategoryCollapse() flips its membership in
 * `state.collapsedCategories` (a `Set<string>`, created once per node in
 * createState() -- the fast render-time lookup, `.has()` per category per
 * renderList() call) and selectCategory() still enters category mode,
 * exactly as before. ONE exception, added 2026-07-29 after the rename
 * report: the tap that first SELECTS a header only ever expands, never
 * collapses, because selecting is the only way to get a category's name
 * into the editor and hiding all of its entries in the same gesture read as
 * the entries having been deleted. Taps on an ALREADY-ACTIVE header toggle
 * collapse exactly as before, so the feature he asked for is intact — see
 * toggleCategoryCollapse()'s own comment. A collapsed category's entries are
 * simply absent from `state.dragRows` too, so they're inert (no click, no
 * drag source) until expanded again; the header itself stays a valid drop
 * TARGET either way (computeDropTarget()'s category-append geometry
 * degrades to "append after the header" when there's nothing visible
 * under it, the same fallback an actually-empty category already used).
 *
 * Collapsed sections persist WITH THE WORKFLOW (owner ask 2026-08-23,
 * superseding the "never a node property, resets on reload" design above):
 * `state.collapsedCategories` is now a CACHE the `Collapsed sections` node
 * property (`PROP_COLLAPSED_SECTIONS`) drives, not the source of truth
 * itself. registerCollapsedSectionsProperty() (called once from attach,
 * right after buildUi() so `state.listEl` exists) wires the property the
 * same way this pack's other per-instance UI-toggle properties do
 * (resolution.js/sets.js/picker.js/switcher.js's `addProperty()` +
 * wrapped `onPropertyChanged` idiom, cited in full at
 * PROP_COLLAPSED_SECTIONS's own declaration): `addProperty()` seeds a
 * fresh node's default (`[]`) but never fires `onPropertyChanged`, so
 * `registerCollapsedSectionsProperty()` also applies it once explicitly;
 * a RESTORED node's `configure()` runs immediately after attach (always,
 * per the other property files' citations) and its properties-merge loop
 * both sets `node.properties[PROP_COLLAPSED_SECTIONS]` to the SAVED array
 * and fires the wrapped `onPropertyChanged` for it -- which re-applies the
 * Set from that saved value and repaints -- before this panel's first
 * ENTRIES-populated render ever runs (that render is always async, off a
 * fetch or a cached paint, both scheduled no earlier than the SAME tick
 * `onConfigure` runs in -- see wireConfigureReload()/reloadNow()). Sections
 * therefore render collapsed from the first paint that has anything to
 * show, no flash-open-then-close. Every place that used to ONLY mutate the
 * Set -- toggleCategoryCollapse(), restoreCategoryCollapseAfterDoubleClick(),
 * and the two rename migrations in applyRenameResult()/
 * performSaveCategory() that move a collapsed key from the old category
 * name to the new one -- now also calls syncCollapsedSectionsProperty(),
 * which writes `Array.from(state.collapsedCategories)` back into the
 * property and dirties the canvas (`node.graph?.setDirtyCanvas(true,
 * true)`) so the workflow's next save captures it. A collapsed name the
 * file no longer has (a deleted/renamed-away category) is simply never
 * looked up by renderList()'s per-category loop -- it stays in the
 * property harmlessly until the user next collapses/expands something
 * else, at which point the fresh `Array.from()` write drops it; no
 * separate pruning pass. A hand-edit of the property via the node's
 * right-click Properties panel repaints the section headers through the
 * same onPropertyChanged path, for free. The pure halves --
 * parseCollapsedSections() (tolerant: the canonical array-of-strings shape,
 * a JSON-encoded string of the same for a hand-edit, anything else folds to
 * `[]`), toggleCollapsedSection() (pure array in, new array out), and
 * isSectionCollapsed() -- are exported for tests/test_notebook_restore_js.py.
 *
 * Drag a category header (owner ask 2026-07-19 "drag category and
 * everything in it"): a header is now ALSO a drag SOURCE, not just a drop
 * target — onCategoryPointerDown() is the header's sibling of
 * onEntryPointerDown() below, sharing the same pointerdown/move/up
 * threshold-disambiguation gesture (beginDrag/endDragVisuals/positionMarker
 * are kind-agnostic; updateDrag()/finishDrag() branch on `drag.kind`).
 * Below the threshold it resolves to the tap behavior above (toggle
 * collapse + selectCategory); at/past it, it commits to relocating the
 * WHOLE category block via `POST /notebook/move_category`
 * (performMoveCategory(), computeCategoryDropTarget() — valid targets are
 * only "before another category header" or "end of file", never "into"
 * anything, since §3.4 Move category has no such primitive).
 *
 * Multiselect drag into a category (owner ask 2026-07-19): dragging any
 * ENTRY that's part of a 2+ selection moves the whole `state.selection`,
 * in selection order, to wherever that one drag's pointer lands —
 * dragMoveNames() decides the moving set, computeDropTarget() excludes all
 * of them (not just the grabbed row) from the drop geometry, and
 * performMoveRun() (performDeleteRun()'s sibling) sends one
 * `/notebook/move` per name, refreshing `base_mtime` from each response so
 * the run can't self-conflict, re-using the SAME resolved target for every
 * entry — which is what keeps the moved block's relative order intact,
 * since inserting each subsequent name "before the same sibling" (or
 * "onto the same category's current end") naturally stacks them in
 * selection order. See performMoveRun()'s own doc for the geometry
 * argument in full. A single-entry drag is unaffected — it still resolves
 * to plain performMove().
 *
 * Pinned values (FORMAT.md §6.1/§7.2, provenance M3 — owner 2026-08-18/21:
 * "will the user be able to see what the old values are? That would be
 * important", plus a one-click clear). The backend declares a TAIL STRING
 * widget `pinned` (hidden in both renderers: `options.hidden` from
 * INPUT_TYPES + the canvas `widget.hidden` set here, exactly like `file`/
 * `entry`). `""` = live; otherwise JSON `{format, entries: [{name, text}],
 * source: {file, token, captured}}` written by EPS Save Image into a baked
 * per-image workflow — NOTHING in this UI ever creates a pin; it arrives
 * through `configure()` (a dropped image / a saved pinned workflow) and
 * wireConfigureReload()'s reconcile (`syncPinnedFromWidget`) paints it
 * without a click. While pinned the backend outputs the pinned entries and
 * ignores the file, so the panel says so: a badge row at the top
 * (`renderPinBar`: "📌 Pinned — captured from image <token> — matches
 * library" / "— differs from current library" / "— library not loaded") with
 * an Unpin button; the entry LIST shows the PINNED entries, read-only, each
 * compared against the live library (`pinnedDrift`: same name → text compare,
 * name gone → "not in the library anymore"; drifted rows carry a "≠" marker
 * whose title shows the current text's first line); the editor pane shows
 * the clicked pinned entry's OLD text read-only (`paintPinnedEditor` —
 * readOnly, not disabled, so it stays legible and copyable). Every mutation
 * path (New/Delete/Save/rename/drag/move, keyboard flows included) is gated
 * on `isPinned(state)`; the `entry`/`file` widgets are never written while
 * pinned, so a dropped workflow keeps its saved selection. Unpin writes `""`
 * through the widget's value + callback (the file's write idiom), toasts
 * "Unpinned — back to the live notebook", and reloads the live view. The
 * bar is a row inside the FILL widget (§7.2 sizing laws: getMinHeight grows
 * by the bar while pinned and the node is lifted once so nothing crops);
 * the live library still loads underneath (that is what drift compares
 * against), and the `loadToken`/restore-race guards are untouched. The
 * pure halves (`parsePinned`, `pinnedDrift`, `pinnedBadgeText`) are
 * exported for tests/test_m3_pinning_js.py.
 *
 * Unsaved-edit drafts (v0.86.0, owner ask 2026-08-28: "if you change a
 * prompt that is selected and run it without saving, it should run the
 * changed prompt"). A second TAIL STRING widget `drafts` (same both-ways
 * hide flag as `pinned`, default `"{}"`) holds a JSON object mapping
 * selected entry name -> unsaved text; `resolve_selection` (the backend's
 * shared live path) applies it on top of the file's text for whatever it
 * names, so a queued run picks up an edit that was never saved. The panel
 * keeps it in sync with whatever the textarea shows for the ACTIVE entry:
 * `commitDraftForActiveEntry` writes (`setDraft`) when the text differs
 * from `state.lastSavedText` (the disk baseline) or clears (`clearDraft`)
 * when it matches -- clearing is always immediate (Save landing, a Reload/
 * discard, or typing back to the original all run through the shared
 * `refreshDirty`, which clears at once), writing is debounced
 * (`scheduleDraftSync`, `DRAFT_SYNC_DEBOUNCE_MS` — never on every
 * keystroke) with an immediate `flushDraftSync` fired from
 * `populateEditor` right before the editor pane shows something else, so a
 * fast "type then click a different row" never loses the edit to a timer
 * that would otherwise fire against the NEW active entry instead. A draft
 * for an entry that falls out of the selection is pruned at the same
 * `setSelection` choke point every selection change already goes through
 * (`pruneDraftsToSelection`) -- `drafts` only ever carries what a run
 * would actually use. Nothing here ever shows draft text differently from
 * live text in the editor pane itself; visibility is the muted selection
 * hint instead (`updateSelectionHint`: "N unsaved edit(s) — runs as
 * edited", never shown while pinned, since a pin ignores drafts outright).
 * `wireConfigureReload`'s reconcile (`syncDraftsFromWidget`, mirroring
 * `syncPinnedFromWidget`) picks up a `drafts` value restored by
 * `configure()` (a workflow saved mid-audition) so the hint is right
 * without a click. Pure halves (`parseDraftsWidgetValue`) are exported;
 * pins in tests/test_notebook_restore_js.py.
 *
 * Session cache + `known_mtime` (library-on-a-NAS round, owner 2026-08-22:
 * "Sometimes the Notebook looks broken but it just takes over a minute to
 * load, even when just tabbing between open workflows"). A tab switch
 * RECREATES the node, and the panel used to sit on an empty list until a
 * fresh full `GET /notebook` (whole-file parse, `include_text=1`) came back
 * over the NAS. Now a module-level `Map` (`notebookCache`, keyed by the
 * `file` value exactly as the panel sends it -- never cross-file) remembers
 * `{payload, mtime}` of the last successful load of each file in this
 * browser session, and every mutating POST response folds into it
 * (`syncNotebookCache`, with the entry text the panel knows --
 * `noteEntryText`/`forgetEntryText`/`renameEntryText` keep the search corpus
 * current too). `reloadNow()` paints a cached payload IMMEDIATELY through
 * the very same `applyNotebookPayload()` a successful fetch uses (a subtle
 * "cached — refreshing…" mark in the status row says so), then runs the
 * normal fetch in the background with `known_mtime=<the mtime THIS panel
 * last painted for the file>` (`state.paintedMtime`, per node -- two
 * Notebooks on one file must not vouch for each other). The backend may
 * answer `{"ok": true, "unchanged": true, "mtime", "exists", "file"}`
 * (FORMAT.md §5/§6.1) -- "keep what's painted": the hint clears, nothing
 * re-renders, no entry resets; any payload WITHOUT `unchanged: true` is the
 * full payload as before, and an older backend that ignores the param
 * simply returns it. The v0.68.1 single-load-per-restore logic
 * (`reloadNow`/`wireConfigureReload`/`configureReloaded`/`loadToken`) and
 * the M3 pin reconcile are untouched -- the cache only changes what is on
 * screen while the fetch is in flight; a failed fetch still lands in the
 * §7.2 error state. Pure halves (`notebookCacheGet`/`notebookCacheSet`/
 * `isUnchangedResponse`) are exported for tests/test_notebook_restore_js.py.
 *
 * Vanilla ES modules, no build step — DOM nodes are built with
 * `document.createElement` (see the local `el()` helper) rather than any
 * templating, matching this pack's other frontend modules.
 */

import * as api from './api.js'

/** FORMAT.md §6.1 — frozen once shipped. */
const NODE_CLASS = 'LoraLibraryNotebook'

const WIDGET_NAME = 'notebook'
const WIDGET_TYPE = 'lora_library_notebook'

/** FORMAT.md §6.1 (provenance M3): the backend's TAIL STRING widget holding
 * the pinned values JSON, or "" for live. Looked up by NAME (`findWidget`),
 * so a backend that hasn't shipped it yet simply leaves every pin path a
 * no-op. */
const PINNED_WIDGET_NAME = 'pinned'
/** Height of the §7.2 pin badge row (`.llnb-pinbar`), added to
 * getMinHeight while pinned so the panel makes room and nothing crops. */
const PIN_BAR_HEIGHT = 26

/** FORMAT.md §6.1 (unsaved-edit drafts, v0.86.0, owner ask 2026-08-28: "if
 * you change a prompt that is selected and run it without saving, it
 * should run the changed prompt"). The backend's TAIL STRING widget
 * (default `"{}"`) holding a JSON object of entry name -> unsaved text.
 * Looked up by NAME, same as `pinned` -- a backend that predates it simply
 * leaves every draft path below a no-op. */
const DRAFTS_WIDGET_NAME = 'drafts'
/** Debounce for writing the active entry's edit into the `drafts` widget
 * (owner ask, same file: "do NOT write on every keystroke") -- coalesces a
 * typing burst into one widget write / canvas repaint, same idiom as
 * FILE_CHANGE_DEBOUNCE_MS/SEARCH_DEBOUNCE_MS above. Going CLEAN (text back
 * to what's on disk) is never debounced -- see refreshDirty() -- only
 * writing a new/changed draft is. */
const DRAFT_SYNC_DEBOUNCE_MS = 400

/** Reserved keyspace for a CATEGORY description's unsaved draft, stored in
 * the same `drafts` widget JSON object as entry drafts (v0.87.4, same
 * owner report as the entry fix, extended to category mode: "an unsaved
 * category-description edit is destroyed by a tab switch exactly as an
 * entry edit was"). Nested inside the SAME flat `drafts` object rather
 * than a second widget -- a `\u0000`-led prefix (a literal NUL byte,
 * never typeable into a markdown heading line, so no legal category OR
 * entry name can ever start with it) keeps a category draft's key
 * disjoint from every entry-draft key. Backward compatible with the
 * v0.87.0 flat `{name: text}` shape already saved in users' workflows:
 * an old entry-only `drafts` value has no key starting with this prefix,
 * so it round-trips unchanged. Forward compatible with the Python side
 * too, without touching it: `resolve_selection`/`parse_drafts`
 * (lora_library/nodes_notebook.py) only ever look up keys that appear in
 * the `entry` widget's SELECTED names, which never contain a NUL byte --
 * a category-draft key is silently invisible to that lookup, exactly
 * right, since a category description is never part of a run's prompt
 * text. */
const CATEGORY_DRAFT_PREFIX = '\u0000category\u0000'

/** The `drafts` object key a category description's draft is stored under
 * -- see `CATEGORY_DRAFT_PREFIX`. */
export function categoryDraftKey(name) {
  return CATEGORY_DRAFT_PREFIX + name
}

/** FORMAT.md §7.2: "resizable via getMinHeight (~180)". */
const MIN_WIDGET_HEIGHT = 180

/**
 * The narrowest this node may be (2026-07-26, owner report from Linux:
 * "it is possible for the container to be smaller than the content and for
 * the text boxes or columns to break out and be wider than the containers
 * they are in").
 *
 * Every DOM widget in this pack clamped HEIGHT (`getMinHeight` above) but
 * nothing ever clamped WIDTH, so a node could be dragged — or restored from
 * a saved workflow — narrower than its two-pane layout can physically
 * render. It looked survivable on macOS/Windows because their default UI
 * fonts are narrow; Linux ships wider ones, so the same width that used to
 * just fit now squeezes the list column, the editor, and the file bar past
 * the point where their contents fit. The root has `overflow: hidden`, so
 * the result reads as truncation and collision rather than a clean layout.
 *
 * 320 = the 40% list column at a readable width plus an editor pane wide
 * enough for a line of prompt text, with margin for a wider font. Enforced
 * in `installMinWidth` below, which only ever GROWS a node to the floor.
 */
const MIN_WIDGET_WIDTH = 320

/**
 * Stops *node* from being sized below *minWidth* (see MIN_WIDGET_WIDTH).
 *
 * Wraps `onResize` the same way this pack's other per-node installers wrap
 * their hooks (guard flag + call-through), and also lifts the CURRENT width,
 * so a saved workflow that was already too narrow opens correctly rather
 * than waiting for the user to nudge the node. Purely additive: a node at or
 * above the floor is untouched, and the floor never shrinks anything.
 */
function installMinWidth(node, minWidth) {
  if (!node || node.__epsMinWidthInstalled) return
  node.__epsMinWidthInstalled = true
  const originalOnResize = node.onResize
  node.onResize = function (size) {
    if (size && size[0] < minWidth) size[0] = minWidth
    return originalOnResize?.call(this, size)
  }
  // v0.68.1 / ported here 2026-09-08: `node.size` is a PROXY over a typed
  // array, so the `Array.isArray(node.size)` guard this replaced was always
  // false -- a node created narrower than `minWidth` never had its width
  // lifted at all (only the `onResize` wrap above protected LATER resizes).
  // The fix reached five of this helper's nine copies in v0.68.1 and missed
  // the four biggest panels for months; see docs/ROADMAP-shared-panel-code.md
  // M0, and grep `web/` for this helper before touching it again. Lift
  // through `setSize` so litegraph's own size mirror runs.
  if (node.size && node.size[0] < minWidth && typeof node.setSize === 'function') {
    node.setSize([minWidth, node.size[1]])
  }
}

/**
 * Finding 6 (2026-08-26 responsiveness round, api.js's opt-in
 * `getJson`/`postJson` `timeoutMs`): every notebook WRITE (entry/category
 * create-or-update, move, delete) passes this so a GIL-busy ComfyUI server
 * or a dropped/slow NAS mount can't wedge `state.busy` (and the Save
 * button) behind a fetch that never settles -- see
 * recoverFromWriteTimeout(). 30s is deliberately generous: it has to
 * comfortably outlast a real, merely-slow write (the whole point is only
 * catching a request that will never come back), never race one that's
 * still legitimately in flight.
 */
const WRITE_TIMEOUT_MS = 30000

/** How long the Delete button stays in "Are you sure?" mode. */
const DELETE_CONFIRM_MS = 4000

/** Debounce for reloading after the `file` widget's value changes. */
const FILE_CHANGE_DEBOUNCE_MS = 250

/** v0.68.1 (perf round): the §7.2 search field repaints on a short debounce
 * instead of per keystroke -- every keystroke used to rebuild the whole list
 * after matching every entry. Escape/clear stays instant (it cancels the
 * pending repaint). Same value as picker.js's SEARCH_DEBOUNCE_MS. */
const SEARCH_DEBOUNCE_MS = 120

/**
 * Pointer-movement distance (px) before a row pointerdown "becomes" a drag
 * instead of a click (owner ask: "~4px"). Below this, pointerup resolves as
 * a plain/ctrl/shift click; at or past it, the gesture commits to reordering
 * and the click never fires — see onEntryPointerDown().
 */
const DRAG_THRESHOLD_PX = 4

/** v0.68.1: how long the note taken on a category header's FIRST tap stays
 * the note for a following tap on the same header -- i.e. "these two taps
 * are one double-click". Covers every OS double-click setting that can
 * still produce a `dblclick` (Windows caps at 900ms); a slower pair simply
 * re-notes on the second tap, which degrades to "expanded" -- the common
 * case -- never to the hidden-entries bug. See onCategoryPointerDown(). */
const CATEGORY_DBLCLICK_WINDOW_MS = 1000

/**
 * Collapsed sections persist with the workflow (owner ask 2026-08-23: "I
 * often group by type of workflow so I never want to see specific prompts
 * in specific workflows but they keep opening up and making the list too
 * long"). A node PROPERTY, not a widget -- same naming convention as this
 * pack's other per-instance UI-toggle properties (controller.js's `Show
 * status`, sets.js's `Show strength scale`/`Show loader slot`, picker.js's
 * `Auto-grow with selection`, switcher.js's `High/low pairs`): properties
 * serialize with the workflow (`LGraphNode.serialize`) with none of the §8
 * positional-`widgets_values` hazard a tail STRING widget would carry, and
 * (unlike the old `state.collapsedCategories` Set alone) they survive a
 * save/reload. Value: an array of collapsed category NAMES -- see
 * "Single-tap collapse" below for the registration/read/write helpers. */
const PROP_COLLAPSED_SECTIONS = 'Collapsed sections'

/**
 * The open category persists across a REBUILD, not just a data reload
 * (owner rule, verbatim: "Just switching between workflows shouldn't ever
 * reset anything in our nodes" — chasing the "editor comes back blank
 * after a tab switch" report: the unsaved DRAFT under a category
 * description already survives that via the `drafts` widget, but the VIEW
 * pointing at it did not). A ComfyUI tab switch tears the panel down and
 * rebuilds it from scratch — attach() constructs a brand new `state`
 * object every time, so `state.activeCategory` (a plain field on that
 * object) cannot survive it on its own, unlike a plain data RELOAD, which
 * keeps reusing the same `state` (see applyNotebookPayload()'s comment,
 * where entry selection restores from the `entry` widget the same way).
 * A node PROPERTY does survive a rebuild: it serializes with the workflow
 * (`LGraphNode.serialize`), same as `PROP_COLLAPSED_SECTIONS` above, with
 * none of the §8 positional-`widgets_values` hazard a tail STRING widget
 * would carry. Value: the open category's NAME, or `''` for "nothing
 * open" — see registerOpenCategoryProperty()/syncOpenCategoryProperty()
 * ("Open category persists" section, right after the collapsed-sections
 * property helpers) for the registration/read/write halves. Note the read
 * half is deliberately NOT the same "apply on every property change"
 * idiom PROP_COLLAPSED_SECTIONS uses top to bottom — see
 * applyOpenCategoryFromProperty()'s own doc for why. */
const PROP_OPEN_CATEGORY = 'Open category'

/** STANDARD-fs-browse.md's `fs/list` sentinel for "the top level" — this
 * pack's own default library dir (labeled) + Home, then every Windows drive
 * or every macOS `/Volumes` mount — mirrors routes.py's own `ROOTS`. */
const FS_ROOTS = 'ROOTS'

const STYLE_TAG_ID = 'lora-library-notebook-styles'

/** Nodes we've already attached to — guards against a double `nodeCreated`. */
const attachedNodes = new WeakSet()

// ---------------------------------------------------------------------------
// Styles — one injected <style> tag, guarded so re-registration (hot reload,
// multiple nodes) never duplicates it. Uses ComfyUI's own theme variables
// (verified against Comfy-Org/ComfyUI_frontend's `assets/palettes/dark.json`
// / `light.json`) with literal fallbacks so the widget still looks
// intentional on a frontend old enough not to define them.
// ---------------------------------------------------------------------------

let stylesInjected = false

const CSS_TEXT = `
.llnb-root {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  box-sizing: border-box;
  overflow: hidden;
  background: var(--comfy-input-bg, #1e1e1e);
  border: 1px solid var(--border-color, #444);
  border-radius: 4px;
  font-family: inherit;
  font-size: 11px;
  color: var(--input-text, #ccc);
}
.llnb-filepanel {
  /* Reworked 2026-07-19: a COLUMN of two rows now, not one — the path/
     buttons row, then the host-machine note on its own full-width line
     (only ever present when remote — see the file header). */
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  padding: 3px 6px;
  border-bottom: 1px solid var(--border-color, #444);
  background: var(--comfy-menu-bg, #262626);
}
.llnb-filepanel-row {
  display: flex;
  align-items: center;
  gap: 6px;
}
.llnb-filepanel-path {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  /* Front-truncation is done in JS (see frontTruncate/updateFilePanelPath)
     only once a real overflow is measured — the CSS ellipsis here is a
     tail-truncation safety net, not the primary mechanism; the CSS
     direction:rtl hack this used to lean on was defeated by unicode-bidi. */
  color: var(--descrip-text, #999);
  font-size: 10px;
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
.llnb-filepanel-note {
  flex: 0 0 auto;
  padding-top: 2px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: var(--descrip-text, #999);
  font-size: 10px;
  font-style: italic;
}
.llnb-filepanel-note:empty { display: none; }
.llnb-filepanel-actions { flex: 0 0 auto; display: flex; gap: 4px; }
.llnb-pinbar {
  /* Provenance M3 pin badge (file header "Pinned values"): one row between
     the file panel and the panes, present only while the 'pinned' widget
     holds a pin (renderPinBar empties it otherwise, and :empty hides it).
     No backticks in this block: CSS_TEXT is a template literal. */
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 6px;
  border-bottom: 1px solid var(--border-color, #444);
  background: var(--comfy-menu-bg, #262626);
  color: var(--input-text, #ccc);
  font-size: 11px;
}
.llnb-pinbar:empty { display: none; }
.llnb-pinbar-text {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-weight: 600;
}
.llnb-pinbar-differs { color: var(--error-text, #ff9f43); }
.llnb-pinbar-unknown { font-style: italic; }
.llnb-btn-unpin { flex: 0 0 auto; padding: 2px 8px; }
.llnb-entry-pinned { cursor: default; }
.llnb-entry-drift { display: flex; align-items: center; gap: 4px; }
.llnb-entry-drift-name { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.llnb-drift {
  flex: 0 0 auto;
  font-weight: 700;
  color: var(--error-text, #ff9f43);
  cursor: help;
}
.llnb-textarea-pinned { border-color: var(--error-text, #ff9f43); opacity: 1; }
.llnb-panes {
  display: flex;
  flex-direction: row;
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.llnb-pane {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.llnb-pane-left { flex: 0 0 40%; }
.llnb-pane-right { flex: 1 1 60%; }
.llnb-splitter {
  flex: 0 0 5px;
  cursor: col-resize;
  background: var(--border-color, #444);
  opacity: 0.6;
}
.llnb-splitter:hover { opacity: 1; }
.llnb-search {
  flex: 0 0 auto;
  margin: 0 0 6px;
  padding: 5px 8px;
  border: 1px solid var(--border-color, #444);
  border-radius: 4px;
  background: var(--comfy-input-bg, #1c1c1c);
  color: var(--input-text, #ddd);
  font-size: 12px;
  outline: none;
  min-width: 0;
}
.llnb-search:focus { border-color: rgb(66, 133, 244); }
.llnb-search::placeholder { color: var(--descrip-text, #808080); }

.llnb-list {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 3px;
}
.llnb-category {
  padding: 4px 6px 2px;
  margin-top: 4px;
  font-size: 9.5px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--descrip-text, #999);
  user-select: none;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  /* Categories in the UI (FORMAT.md §7.2 amendment): headers are clickable
     (selectCategory()) to enter "category mode" — same affordance language
     as an entry row below. */
  cursor: pointer;
  border-radius: 3px;
  outline: none;
  display: flex;
  align-items: center;
  gap: 4px;
}
.llnb-category:hover { background: var(--content-hover-bg, #2a2a2a); }
.llnb-category:focus-visible { box-shadow: inset 0 0 0 1px var(--border-color, #444); }
.llnb-category-active,
.llnb-category-active:hover {
  background: rgba(66, 133, 244, 0.22);
  color: var(--input-text, #ccc);
}
/* Owner report 2026-08-25 ("consistent with the state controller"): the
   count-when-collapsed + armed X mirror controller.js's .llsc-category-*
   trio exactly, llnb- prefixed. */
.llnb-category-label { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.llnb-category-delete {
  flex: 0 0 auto; background: none; border: none; cursor: pointer; padding: 0 2px;
  color: var(--descrip-text, #999); font-size: 10px; line-height: 1; font-family: inherit;
  visibility: hidden;
}
.llnb-category:hover .llnb-category-delete { visibility: visible; }
.llnb-category-delete-armed { color: #ff6b6b; visibility: visible; }
.llnb-entry {
  padding: 3px 7px;
  margin: 1px 0;
  border-radius: 3px;
  border-left: 3px solid transparent;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  outline: none;
  user-select: none;
  touch-action: none;
}
.llnb-entry:hover { background: var(--content-hover-bg, #2a2a2a); }
.llnb-entry:focus-visible { box-shadow: inset 0 0 0 1px var(--border-color, #444); }
.llnb-entry-selected,
.llnb-entry-selected:hover {
  background: rgba(66, 133, 244, 0.22);
  border-left-color: rgba(66, 133, 244, 0.9);
}
/* Active = most recently clicked among the selected rows (§7.2); it alone
   drives the editor, so it gets a visibly stronger treatment than a plain
   multi-selected row. Declared after .llnb-entry-selected so it wins on the
   properties they share (equal specificity, later rule wins). */
.llnb-entry-active,
.llnb-entry-active:hover {
  background: rgba(66, 133, 244, 0.38);
  border-left-color: rgba(66, 133, 244, 1);
  font-weight: 600;
  box-shadow: inset 0 0 0 1px rgba(66, 133, 244, 0.55);
}
.llnb-entry-dragging { opacity: 0.4; }
.llnb-drag-marker {
  height: 2px;
  margin: 3px 4px;
  border-radius: 1px;
  background: rgba(66, 133, 244, 0.9);
  pointer-events: none;
}
.llnb-empty {
  padding: 6px 7px;
  color: var(--descrip-text, #999);
  font-style: italic;
}
.llnb-footer {
  flex: 0 0 auto;
  display: flex;
  gap: 4px;
  padding: 4px;
  border-top: 1px solid var(--border-color, #444);
}
.llnb-btn {
  flex: 1 1 auto;
  min-width: 0;
  background: var(--comfy-menu-bg, #262626);
  border: 1px solid var(--border-color, #444);
  color: var(--input-text, #ccc);
  border-radius: 4px;
  padding: 3px 4px;
  font-size: 11px;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.llnb-btn:hover:not(:disabled) { background: var(--content-hover-bg, #2a2a2a); }
.llnb-btn:disabled { opacity: 0.45; cursor: default; }
.llnb-btn-danger { border-color: var(--error-text, #ff4444); color: var(--error-text, #ff4444); }
.llnb-btn-small { flex: 0 0 auto; padding: 2px 8px; }
.llnb-btn-save { flex: 0 0 auto; }
.llnb-input {
  flex: 1 1 auto;
  min-width: 0;
  box-sizing: border-box;
  background: var(--comfy-input-bg, #1e1e1e);
  border: 1px solid var(--border-color, #444);
  color: var(--input-text, #ccc);
  border-radius: 4px;
  padding: 3px 5px;
  font-size: 11px;
}
.llnb-share-toggle {
  /* FORMAT.md §2 share toggle (owner report 2026-07-29) — its own row under
     the path bar, same treatment as .llnb-filepanel-note, so it never
     squeezes the path. Hidden outright unless it applies. */
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 2px 6px 3px;
  font-size: 10px;
  color: var(--descrip-text, #999);
}
.llnb-share-toggle-box { margin: 0; flex: 0 0 auto; cursor: pointer; }
.llnb-share-toggle-label { cursor: pointer; min-width: 0; }
.llnb-inline-rename {
  /* Rename in place (owner ask 2026-07-29, "double click a name to rename in
     place") — replaces a row's text with an input sized to the row, so the
     rename happens where the user is looking instead of only in the editor's
     name field. Deliberately shares .llnb-entry's box metrics (padding/font)
     so the row neither grows nor shifts when the editor opens. */
  display: block;
  width: 100%;
  box-sizing: border-box;
  background: var(--comfy-input-bg, #1e1e1e);
  border: 1px solid rgba(66, 133, 244, 0.9);
  border-radius: 3px;
  color: var(--input-text, #ccc);
  padding: 2px 6px;
  margin: 0;
  font: inherit;
  outline: none;
}
.llnb-inline-rename-host {
  /* The row while it hosts the editor: drop the text-clipping and the
     drag/select affordances so the input behaves like an input. */
  padding: 1px 4px;
  overflow: visible;
  cursor: auto;
  user-select: auto;
  touch-action: auto;
}
.llnb-name-field {
  /* Rename via the editor's name field (FORMAT.md §7.2 amendment, owner ask
     2026-07-19) — sits above .llnb-mode-hint as the editor pane's own
     "title bar"; full-width, no border-radius, so it reads as part of the
     pane's header stack rather than a floating input. */
  flex: 0 0 auto;
  width: 100%;
  box-sizing: border-box;
  border: none;
  border-bottom: 1px solid var(--border-color, #444);
  border-radius: 0;
  background: var(--comfy-input-bg, #1e1e1e);
  color: var(--input-text, #ccc);
  padding: 4px 6px;
  font-size: 11px;
  font-weight: 600;
}
.llnb-name-field:disabled { opacity: 0.5; }
.llnb-name-field::placeholder { color: var(--descrip-text, #999); font-weight: normal; }
.llnb-mode-hint {
  /* Categories in the UI (FORMAT.md §7.2 amendment): "entry selected ⇒
     entry body; category selected ⇒ category description; a visible mode
     hint says which" — updateModeHint(). Sits directly above the textarea
     so it reads as "what Save is about to write", not a status message. */
  flex: 0 0 auto;
  padding: 3px 6px;
  font-size: 10px;
  font-style: italic;
  color: var(--descrip-text, #999);
  border-bottom: 1px solid var(--border-color, #444);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.llnb-mode-hint:empty { display: none; }
.llnb-textarea {
  flex: 1 1 auto;
  min-height: 0;
  width: 100%;
  box-sizing: border-box;
  resize: none;
  background: var(--comfy-input-bg, #1e1e1e);
  color: var(--input-text, #ccc);
  border: none;
  border-bottom: 1px solid var(--border-color, #444);
  padding: 6px;
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 11px;
  line-height: 1.4;
}
.llnb-textarea:disabled { opacity: 0.5; }
.llnb-textarea::placeholder { color: var(--descrip-text, #999); }
.llnb-bottom-row {
  /* One row: Save left, status right-justified (owner ask 2026-07-18 —
     the stacked layout wasted vertical space). Wraps only when cramped. */
  flex: 0 0 auto;
  display: flex;
  flex-direction: row;
  align-items: center;
  flex-wrap: wrap;
  gap: 3px 8px;
  padding: 4px 6px;
}
.llnb-status {
  flex: 1 1 auto;
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  min-width: 0;
}
.llnb-status-text {
  color: var(--descrip-text, #999);
  font-size: 10px;
  text-align: right;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.llnb-status-text:empty { display: none; }
.llnb-status-actions { display: flex; flex: 0 0 auto; gap: 4px; }
.llnb-status-actions:empty { display: none; }
.llnb-status-hint {
  color: var(--descrip-text, #999);
  font-size: 10px;
  font-style: italic;
  text-align: right;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.llnb-status-hint:empty { display: none; }
.llnb-status-cached {
  /* Session cache (file header): the subtle "cached — refreshing…" mark
     while a cached paint waits for its refresh fetch. */
  color: var(--descrip-text, #999);
  font-size: 10px;
  font-style: italic;
  opacity: 0.75;
  white-space: nowrap;
  flex: 0 0 auto;
}
.llnb-status-cached:empty { display: none; }
.llnb-picker-backdrop {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.5);
  z-index: 10000;
}
.llnb-picker {
  display: flex;
  flex-direction: column;
  width: min(480px, 90vw);
  max-height: min(520px, 80vh);
  background: var(--comfy-menu-bg, #262626);
  border: 1px solid var(--border-color, #444);
  border-radius: 6px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
  overflow: hidden;
  font-family: inherit;
  font-size: 11px;
  color: var(--input-text, #ccc);
}
.llnb-picker-pathrow {
  /* Type-or-paste-a-path input (FORMAT.md §5/§7.2, owner's NAS fix) — sits
     above the listing, persistent across navigation (unlike .llnb-picker-
     content below, which loadPickerDir() replaces on every navigation). */
  flex: 0 0 auto;
  display: flex;
  gap: 6px;
  padding: 8px 10px 0;
}
.llnb-picker-patherror {
  flex: 0 0 auto;
  padding: 4px 10px 0;
  color: var(--error-text, #ff4444);
  font-size: 10.5px;
}
.llnb-picker-patherror:empty { display: none; }
.llnb-picker-content {
  /* Everything loadPickerDir()/renderPickerDialog() replace wholesale on
     each navigation — kept out of the persistent path-input row above so
     a failed lookup never wipes out what the user just typed. */
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
  overflow: hidden;
}
.llnb-picker-header {
  flex: 0 0 auto;
  padding: 8px 10px;
  border-bottom: 1px solid var(--border-color, #444);
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 10.5px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--descrip-text, #999);
}
.llnb-picker-list {
  flex: 1 1 auto;
  min-height: 120px;
  overflow-y: auto;
  padding: 4px;
}
.llnb-picker-row {
  padding: 5px 8px;
  border-radius: 3px;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.llnb-picker-row:hover { background: var(--content-hover-bg, #2a2a2a); }
.llnb-picker-status,
.llnb-picker-empty {
  padding: 10px;
  color: var(--descrip-text, #999);
  font-style: italic;
}
.llnb-picker-footer {
  flex: 0 0 auto;
  display: flex;
  justify-content: flex-end;
  gap: 6px;
  padding: 6px 8px;
  border-top: 1px solid var(--border-color, #444);
}
.llnb-picker-title {
  /* pickServerFolder(): an optional caption above the path row naming
     WHAT is being picked (the Notebook's file picker has none). */
  flex: 0 0 auto;
  padding: 8px 10px 0;
  font-size: 11.5px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.llnb-picker-title:empty { display: none; }
.llnb-picker-row-dim {
  /* folder mode: files are context, not choices */
  opacity: 0.45;
  cursor: default;
  pointer-events: none;
}
.llnb-picker-confirm {
  /* folder mode's second step (spec.confirmPrompt): the question replaces
     the footer; Back restores it. */
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 6px 8px;
  border-top: 1px solid var(--border-color, #444);
}
.llnb-picker-confirm-text {
  white-space: normal;
  overflow-wrap: anywhere;
  color: var(--input-text, #ccc);
}
.llnb-picker-confirm-buttons {
  display: flex;
  justify-content: flex-end;
  gap: 6px;
}
`

function injectStyles() {
  if (stylesInjected) return
  stylesInjected = true
  if (document.getElementById(STYLE_TAG_ID)) return
  const style = document.createElement('style')
  style.id = STYLE_TAG_ID
  style.textContent = CSS_TEXT
  document.head.appendChild(style)
}

// ---------------------------------------------------------------------------
// Tiny DOM builder — this pack is vanilla JS with no templating engine.
// ---------------------------------------------------------------------------

/**
 * @param {string} tag
 * @param {{className?: string, text?: string, attrs?: Record<string,string>}} [options]
 * @param {(Node|string)[]} [children]
 * @returns {HTMLElement}
 */
function el(tag, options = {}, children = []) {
  const node = document.createElement(tag)
  if (options.className) node.className = options.className
  if (options.text !== undefined) node.textContent = options.text
  if (options.attrs) {
    for (const [key, value] of Object.entries(options.attrs)) {
      node.setAttribute(key, value)
    }
  }
  for (const child of children) {
    if (child == null) continue
    node.append(child instanceof Node ? child : document.createTextNode(String(child)))
  }
  return node
}

// ---------------------------------------------------------------------------
// Node / widget lookups
// ---------------------------------------------------------------------------

/**
 * @param {object} node
 * @returns {boolean}
 */
function isNotebookNode(node) {
  if (!node) return false
  if (node.comfyClass === NODE_CLASS) return true
  if (node.constructor && node.constructor.comfyClass === NODE_CLASS) return true
  return false
}

/**
 * @param {object} node
 * @param {string} name
 */
function findWidget(node, name) {
  return node.widgets?.find((w) => w && w.name === name)
}

// ---------------------------------------------------------------------------
// Public entry point
// ---------------------------------------------------------------------------

/**
 * Attach the two-pane editor to *node* when it is a LoraLibraryNotebook;
 * no-op for every other node type. Never throws — every failure is logged
 * via `api.warn` and leaves the node's plain `file`/`entry` widgets fully
 * functional on their own (FORMAT.md §7.2).
 * @param {object} node - LiteGraph node instance.
 */
export function attachNotebookWidget(node) {
  try {
    if (!isNotebookNode(node)) return
    if (attachedNodes.has(node)) return
    if (typeof node.addDOMWidget !== 'function') {
      api.warn('this ComfyUI frontend has no addDOMWidget; notebook editor not attached')
      return
    }

    const fileWidget = findWidget(node, 'file')
    const entryWidget = findWidget(node, 'entry')
    if (!fileWidget || !entryWidget) {
      api.warn('LoraLibraryNotebook node is missing its file/entry widgets; notebook editor not attached')
      return
    }
    // Provenance M3 (FORMAT.md §6.1): the backend's tail `pinned` widget --
    // null on a backend that predates it, in which case every pin path
    // below is a no-op and the panel is exactly the pre-M3 panel.
    const pinnedWidget = findWidget(node, PINNED_WIDGET_NAME) || null
    // Unsaved-edit drafts (v0.86.0) -- null on a backend that predates it,
    // same no-op-below convention as `pinnedWidget`.
    const draftsWidget = findWidget(node, DRAFTS_WIDGET_NAME) || null

    attachedNodes.add(node)

    const state = createState(node, fileWidget, entryWidget, pinnedWidget, draftsWidget)
    buildUi(state)
    // Collapsed sections persist with the workflow (file header "Single-tap
    // collapse" -> "Collapsed sections persist WITH THE WORKFLOW"): must run
    // after buildUi() (state.listEl has to exist for the explicit initial
    // apply's eventual renderList() calls) and before this function returns,
    // so the wrapped onPropertyChanged is in place before ComfyUI's next
    // `node.configure()` call for a restored node.
    registerCollapsedSectionsProperty(state)
    // Open category persists across a REBUILD too (PROP_OPEN_CATEGORY's own
    // doc) -- same placement/ordering reason as the call just above.
    registerOpenCategoryProperty(state)
    hideFileWidget(state)
    hidePinnedWidget(state)
    hideDraftsWidget(state)
    wireFileWidget(state)
    wirePinnedWidget(state)
    wireConfigureReload(state)
    wireNodeCleanup(state)
    // Universal State Controller Apply fix (see resyncAfterExternalWrite's
    // own doc comment): publish this node's reload seam and make sure the
    // one shared subscription to the announce event is installed.
    node.__epsNotebookReload = () => resyncAfterExternalWrite(state)
    installExternalWriteSubscription()

    // FORMAT.md §7.2 amendment: one `/config` check per attach (cached at
    // module scope — see "Remote gating" below) to gate the file panel's
    // buttons and the `file` widget's edit-guard.
    refreshRemoteGating(state).catch((error) => api.warn('initial config load failed', error))
    // v0.68.1 (perf round): the attach-time load is DEFERRED one tick and
    // SKIPPED when onConfigure already reloaded. `nodeCreated` fires from
    // the node's constructor, before `configure()` restores
    // `widgets_values`, so on every workflow load / tab switch a RESTORED
    // node fetched the backend-DEFAULT notebook with full text here and
    // then the saved one again from wireConfigureReload -- the first was
    // discarded by `loadToken` but fully paid (the server parsed the whole
    // file; one LAN round trip per node, doubled). litegraph creates AND
    // configures every node of a load (or a paste) synchronously, so by the
    // time this timer fires a restored node's onConfigure has run and set
    // `configureReloaded`; a fresh in-session node has no configure and
    // loads here, once. Cleared in teardown().
    state.attachLoadTimer = setTimeout(() => {
      state.attachLoadTimer = null
      if (state.configureReloaded) return
      reloadNow(state).catch((error) => api.warn('initial notebook load failed', error))
    }, 0)
  } catch (error) {
    api.warn('attachNotebookWidget failed', error)
  }
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

function createState(node, fileWidget, entryWidget, pinnedWidget = null, draftsWidget = null) {
  return {
    node,
    fileWidget,
    entryWidget,
    // Provenance M3 (FORMAT.md §6.1, file header "Pinned values"): the
    // backend's tail `pinned` STRING widget (null on an older backend), the
    // raw value last reconciled from it, its parsed form (`parsePinned`,
    // null = live), the pinned entry currently shown in the editor pane,
    // the badge row element, and whether the node was lifted by the bar's
    // height (so unpin can give it back). The pin is NEVER created here --
    // it arrives via configure() and is only ever shown or cleared.
    pinnedWidget,
    pinnedRaw: '',
    pinned: null,
    pinnedActive: null,
    pinBarEl: null,
    pinGrown: false,
    // Unsaved-edit drafts (v0.86.0, owner ask 2026-08-28): the backend's
    // tail `drafts` STRING widget (null on an older backend) and the
    // panel's in-memory mirror of it -- {name: unsaved text}, one entry per
    // NAME with a dirty edit (in practice at most the ACTIVE entry, since
    // only one entry is ever open in the textarea at a time; the shape is
    // a map so a multi-select audition can carry more than one). The
    // PANEL is the only writer (setDraft/clearDraft/pruneDraftsToSelection
    // -> syncDraftsWidget); configure() is reconciled the other way via
    // syncDraftsFromWidget() (wireConfigureReload), same "assigns
    // widget.value directly, no callback" reason `pinned` needs it. See
    // the file header's "Unsaved-edit drafts" paragraph. `draftSyncTimer`
    // is the pending debounced write (scheduleDraftSync/flushDraftSync).
    draftsWidget,
    drafts: {},
    draftSyncTimer: null,
    file: null,
    // §7.2 "never reset unless the user resets" (owner report 2026-08-03):
    // non-null after a FAILED notebook load -- {file, message}. While set,
    // the panel is an explicit error state: the attempted path stays
    // visible in the file panel, every mutating action refuses (see
    // writesBlocked), and the status line carries a Retry button. Cleared
    // by the next successful load. The widgets are NEVER touched by any of
    // this, so the workflow's saved file/entry values survive a broken
    // connection by construction.
    loadError: null,
    // §7.2 search (owner ask 2026-08-08): the live filter string and the
    // per-entry body text the `include_text=1` list payload delivers --
    // refreshed by every reload, so the search corpus can never lag what
    // the list itself shows. Filtering is a VIEW: it never touches
    // selection, widgets, or the file.
    searchQuery: '',
    entryTextByName: {},
    // Finding 5 (2026-08-26 responsiveness round): category descriptions
    // from the SAME include_text=1 payload (`category_descriptions`),
    // mirroring `entryTextByName` above -- see
    // noteCategoryDescription()/loadCategoryDescription().
    categoryDescriptionByName: {},
    // v0.68.1: name -> lowercased `name\nbody` haystack, built once per load
    // (buildSearchCorpus) so a keystroke doesn't re-lowercase every body;
    // and the pending debounced search repaint (scheduleSearchRender).
    searchCorpus: new Map(),
    searchTimer: null,
    searchInputEl: null,
    exists: true,
    entries: [],
    // Categories in the UI (FORMAT.md §7.2 amendment) — the §5 `categories`
    // list (file order, may include empty/repeated names) and the name of
    // the category currently shown in the editor ("category mode"), or
    // null. Deliberately independent of `selection`/`activeName` below —
    // see the file header's "Categories" paragraph for why entering/exiting
    // category mode must never touch either.
    categories: [],
    activeCategory: null,
    // Rename in place (owner ask 2026-07-29) — the row currently hosting an
    // inline rename editor, or null. `{kind:'entry'|'category', name, value}`;
    // `value` is the live typed text, kept here (not only in the DOM) so
    // renderList() can rebuild the list under an open editor without losing
    // what the user has typed. See beginInlineRename().
    inlineRename: null,
    // Single-tap collapse (FORMAT.md §7.2 amendment, owner ask 2026-07-19)
    // — category names currently collapsed in the left list. Never read
    // outside this file (reloadNow()/renderList() read it fresh every call
    // — see toggleCategoryCollapse()). Replaced wholesale (not mutated
    // key-by-key) by applyCollapsedSectionsFromProperty() whenever the
    // `Collapsed sections` node property changes — see the file header's
    // "Collapsed sections persist WITH THE WORKFLOW" paragraph; it is a
    // render-time CACHE over that property now, not its own source of
    // truth. registerCollapsedSectionsProperty() (attach) seeds it from
    // the (possibly saved) property before this Set is ever read.
    collapsedCategories: new Set(),
    // v0.68.1: the collapse state of a category header as of the FIRST tap
    // of a would-be double-click pair -- {category, collapsed, at}; see
    // onCategoryPointerDown() and the header's dblclick handler.
    categoryTapMemo: null,
    // Selection model (§6.1/§7.2): `selection` is the ordered list of
    // selected entry names — exactly what gets newline-joined into the
    // `entry` widget. `activeName` is the most-recently-clicked selected
    // entry; it alone drives the editor/dirty/Save/Delete/conflict flow.
    // See the "Selection model" functions below.
    selection: [],
    activeName: null,
    baseMtime: null,
    lastSavedText: '',
    // Rename via the editor's name field (FORMAT.md §7.2 amendment) — the
    // baseline the name field is compared against for dirty-tracking,
    // exactly like `lastSavedText` for the textarea; see refreshDirty().
    lastSavedName: '',
    dirty: false,
    busy: false,
    loadToken: 0,
    selectToken: 0,
    // v0.68.1: set by wireConfigureReload when a configure-driven reload
    // ran, so the deferred attach-time load stands down (attachNotebookWidget);
    // the timer handle lives here so teardown() can cancel it.
    configureReloaded: false,
    attachLoadTimer: null,
    // Session cache (file header): the file mtime of the payload THIS panel
    // last painted (applyNotebookPayload / syncNotebookCache) -- the
    // `known_mtime` reloadNow() sends, per node, never shared between two
    // panels on one file. null = nothing painted / the file did not exist.
    paintedMtime: null,
    creatingNew: false,
    deleteConfirmActive: false,
    deleteConfirmTimer: null,
    fileChangeDebounceTimer: null,
    // Flat, top-to-bottom list of {el, kind: 'header'|'entry', name?,
    // category} rebuilt every renderList() call — the drag hit-testing
    // geometry in "Drag-to-reorder" below walks this instead of re-querying
    // the DOM.
    dragRows: [],
    // In-flight pointer gesture (pointerdown → move → up), or null between
    // gestures. See onEntryPointerDown().
    drag: null,
    // FORMAT.md §7.2 amendment — the file panel's resolved absolute path
    // (the `file` field of the last `GET /notebook` response) and whether
    // THIS browser is local (`GET /config`'s `is_local`; null = not yet
    // known, treated as local — see refreshRemoteGating()).
    resolvedFile: null,
    // FORMAT.md §2 remote allow-list (owner report 2026-07-29), mirrored from
    // /config so updateShareToggle() can decide whether a SECOND machine
    // would be refused this file. Advisory only — the server re-derives and
    // enforces the real check.
    remoteDirs: [],
    libraryDir: null,
    shareToggleWrapEl: null,
    shareToggleEl: null,
    shareToggleLabelEl: null,
    isLocal: null,
    // The file WIDGET's last known-good value — wireFileWidget() reverts to
    // this when a remote viewer edits a read-only `file` widget.
    lastKnownFileValue: null,
    // The Browse… picker's window-level Escape-key listener while open (the
    // picker lives on document.body, not inside this widget's own DOM — see
    // openBrowsePicker()/openPickerDialog(); this `state` IS the picker
    // session, `pickerSpec` (file mode) is set per open).
    pickerKeydownHandler: null,
    pickerSpec: null,
    // Guards the picker's in-flight fs/list fetch (loadPickerDir()): the
    // path bar stays live while a listing is in flight, so a second
    // navigation can lap the first (a slow NAS initial load vs. a typed
    // local path — 600ms LAN latency is a first-class condition for this
    // pack), and without a token the LAST response to land wins the
    // dialog, stale or not. Same idiom as loadToken/selectToken above
    // (2026-08-08 audit).
    pickerNavToken: 0,
    // DOM refs, filled in by buildUi() — only elements later functions need
    // to reach back into are tracked here (e.g. `newBtn` isn't, since
    // nothing but renderFooter() itself ever touches it).
    root: null,
    leftPane: null,
    listEl: null,
    footerEl: null,
    nameFieldEl: null,
    modeHintEl: null,
    textarea: null,
    saveBtn: null,
    statusTextEl: null,
    statusActionsEl: null,
    statusHintEl: null,
    // Session cache (file header): the "cached — refreshing…" mark in the
    // status row while a cached paint waits for its fetch; see setCacheHint().
    cacheHintEl: null,
    deleteBtn: null,
    filePanelPathEl: null,
    filePanelNoteEl: null,
    browseBtn: null,
    openFolderBtn: null,
    // File panel rework (FORMAT.md §7.2 amendment) — re-fits the path bar's
    // front-truncation on a node resize; see updateFilePanelPath(). Torn
    // down in teardown().
    filePanelResizeObserver: null
  }
}

// ---------------------------------------------------------------------------
// UI construction
// ---------------------------------------------------------------------------

function buildUi(state) {
  injectStyles()

  // §7.2 search field (owner ask 2026-08-08) -- top of the left column.
  // Every keystroke stops propagation (the canvas has global hotkeys;
  // same rule as the name field below); Escape CLEARS the query in place.
  state.searchInputEl = el('input', {
    className: 'llnb-search',
    attrs: { type: 'text', placeholder: 'Search title + text\u2026', spellcheck: 'false' }
  })
  state.searchInputEl.addEventListener('input', () => {
    state.searchQuery = state.searchInputEl.value
    scheduleSearchRender(state) // v0.68.1: debounced -- see SEARCH_DEBOUNCE_MS
  })
  state.searchInputEl.addEventListener('keydown', (event) => {
    event.stopPropagation()
    if (event.key === 'Escape') {
      event.preventDefault()
      clearTimeout(state.searchTimer) // v0.68.1: clear is instant; no pending repaint may follow it
      state.searchTimer = null
      state.searchInputEl.value = ''
      state.searchQuery = ''
      renderList(state)
    }
  })

  state.listEl = el('div', { className: 'llnb-list' })
  state.footerEl = el('div', { className: 'llnb-footer' })
  state.leftPane = el('div', { className: 'llnb-pane llnb-pane-left' }, [
    state.searchInputEl,
    state.listEl,
    state.footerEl
  ])

  const splitter = el('div', { className: 'llnb-splitter', attrs: { title: 'Drag to resize' } })

  // Rename via the editor's name field (FORMAT.md §7.2 amendment, owner ask
  // 2026-07-19) — the PRIMARY rename control now; see the file header.
  state.nameFieldEl = el('input', {
    className: 'llnb-name-field',
    attrs: { type: 'text', placeholder: 'Name…', disabled: 'disabled' }
  })
  // Categories in the UI (FORMAT.md §7.2 amendment): a muted line saying
  // which of the two contexts (entry body vs. category description) the
  // textarea/Save below currently target — see updateModeHint().
  state.modeHintEl = el('div', { className: 'llnb-mode-hint' })
  state.textarea = el('textarea', {
    className: 'llnb-textarea',
    attrs: {
      placeholder: 'Select an entry or category on the left, or click ＋ New to create one.',
      spellcheck: 'false'
    }
  })
  // DOM widgets are skipped by ComfyUI's own tooltip layer on purpose
  // ("these use native browser tooltips" -- NodeTooltip.vue), so every
  // control in this panel documents itself with a plain `title`.
  state.saveBtn = el('button', {
    className: 'llnb-btn llnb-btn-save',
    text: 'Save',
    attrs: { title: 'Write your edits back to the Markdown file on disk' }
  })
  state.statusTextEl = el('div', { className: 'llnb-status-text' })
  state.statusActionsEl = el('div', { className: 'llnb-status-actions' })
  state.statusHintEl = el('div', { className: 'llnb-status-hint' })
  // Session cache (file header): empty (and :empty-hidden) except while a
  // cached paint is on screen and its refresh fetch is in flight.
  state.cacheHintEl = el('div', { className: 'llnb-status-cached' })
  const statusRow = el('div', { className: 'llnb-status' }, [
    state.cacheHintEl,
    state.statusTextEl,
    state.statusActionsEl,
    state.statusHintEl
  ])
  const bottomRow = el('div', { className: 'llnb-bottom-row' }, [state.saveBtn, statusRow])
  const rightPane = el('div', { className: 'llnb-pane llnb-pane-right' }, [
    state.nameFieldEl,
    state.modeHintEl,
    state.textarea,
    bottomRow
  ])

  const panesRow = el('div', { className: 'llnb-panes' }, [state.leftPane, splitter, rightPane])
  const filePanel = buildFilePanel(state)
  // Provenance M3 pin badge row -- between the file panel and the panes,
  // empty (and therefore hidden, `.llnb-pinbar:empty`) until a pin arrives;
  // see renderPinBar().
  state.pinBarEl = el('div', { className: 'llnb-pinbar' })
  state.root = el('div', { className: 'llnb-root' }, [filePanel, state.pinBarEl, panesRow])

  state.nameFieldEl.addEventListener('input', () => refreshDirty(state))
  state.nameFieldEl.addEventListener('keydown', (event) => {
    event.stopPropagation()
    if (event.key === 'Enter') {
      event.preventDefault()
      performSave(state).catch((error) => api.warn('save failed', error))
    }
  })
  state.textarea.addEventListener('input', () => {
    refreshDirty(state)
    // Unsaved-edit drafts (v0.86.0): only the textarea's OWN `input` event
    // arms the debounced write -- refreshDirty() itself only ever CLEARS
    // (see its doc comment), so a keystroke never triggers a widget write
    // on its own; this debounce is what coalesces a typing burst into one.
    scheduleDraftSync(state)
  })
  state.textarea.addEventListener('keydown', (event) => event.stopPropagation())
  state.saveBtn.addEventListener('click', () => {
    performSave(state).catch((error) => api.warn('save failed', error))
  })

  wireSplitter(state, splitter)

  renderFooter(state)
  clearEditor(state)

  attachDomWidget(state.node, state.root, state)
}

/**
 * Wraps `node.addDOMWidget` — kept as its own function so the three
 * non-serialization flags (see file header) sit next to the call that needs
 * them, instead of being scattered across `buildUi`.
 */
function attachDomWidget(node, rootEl, state) {
  const domWidget = node.addDOMWidget(WIDGET_NAME, WIDGET_TYPE, rootEl, {
    // Same default litegraph itself applies (`scripts/domWidget.ts`); kept
    // explicit for readability.
    hideOnZoom: true,
    // Excludes the widget from the API prompt (utils/executionUtil.ts).
    serialize: false,
    // Provenance M3: the pin badge row takes a row of the FILL widget, so
    // the floor grows with it while pinned (§7.2: the panel makes room).
    getMinHeight: () => MIN_WIDGET_HEIGHT + (state && isPinned(state) ? PIN_BAR_HEIGHT : 0)
  })
  // Excludes the widget from the workflow JSON (LGraphNode.serialize /
  // .configure check `widget.serialize`, a *different* flag from
  // `options.serialize` above — see file header).
  domWidget.serialize = false
  domWidget.serializeValue = () => undefined
  installMinWidth(node, MIN_WIDGET_WIDTH) // 2026-07-26 Linux fix — see its docstring
  return domWidget
}

function wireSplitter(state, splitter) {
  let dragging = false
  let startX = 0
  let startWidth = 0

  const onPointerMove = (event) => {
    if (!dragging) return
    const rootRect = state.root.getBoundingClientRect()
    const minLeft = 80
    const maxLeft = Math.max(minLeft, rootRect.width - 160)
    const next = Math.min(maxLeft, Math.max(minLeft, startWidth + (event.clientX - startX)))
    state.leftPane.style.flex = `0 0 ${next}px`
  }
  const stopDragging = (event) => {
    if (!dragging) return
    dragging = false
    try {
      splitter.releasePointerCapture(event.pointerId)
    } catch {
      // Not captured, or already released — nothing to do.
    }
    window.removeEventListener('pointermove', onPointerMove, { capture: true })
    window.removeEventListener('pointerup', stopDragging, { capture: true })
  }
  splitter.addEventListener('pointerdown', (event) => {
    dragging = true
    startX = event.clientX
    startWidth = state.leftPane.getBoundingClientRect().width
    try {
      splitter.setPointerCapture(event.pointerId)
    } catch {
      // Best-effort; the window-level listeners below still cover dragging.
    }
  // CAPTURE-phase window listeners, deliberately (2026-07-30, owner's Mac
  // report, reproduced on the rig): under Vue nodes ("New node design") the
  // node's DOM wrapper stops pointer-event propagation on the way UP, so a
  // plain (bubble-phase) window listener never fires and the gesture never
  // commits -- clicks stopped selecting, drags stopped dropping, while
  // element-level listeners (typing, buttons) kept working, which made the
  // panel look half-broken. Capture descends from the window BEFORE any
  // bubble-path stopPropagation can intervene, and nothing above window
  // exists to stop it -- so these fire in BOTH renderers. The matching
  // removeEventListener calls must pass the same capture flag or they
  // silently fail to detach.
    window.addEventListener('pointermove', onPointerMove, { capture: true })
    window.addEventListener('pointerup', stopDragging, { capture: true })
    event.preventDefault()
  })
}

// ---------------------------------------------------------------------------
// file widget chaining + node cleanup
// ---------------------------------------------------------------------------

/**
 * File panel rework (FORMAT.md §7.2 amendment, owner ask 2026-07-19): hides
 * the raw `file` STRING widget's on-canvas row via `.hidden` — a real
 * litegraph layout primitive on this fork (controller.js's header carries
 * the citations backing this: `LGraphNode.isWidgetVisible()`/
 * `getLayoutWidgets()` both filter on it, so it pulls the widget out of
 * drawing AND layout AND size, unlike `.disabled`, which blanks a disabled
 * TEXT widget's value outright instead of graying it out — wrong for a
 * widget that must keep serializing the node's real value). The file panel
 * (buildFilePanel(), already built by buildUi() before this runs) is what
 * replaces it as the visible control. `setDirtyCanvas` is enough to make it
 * take effect immediately — no manual resize bookkeeping needed, since
 * `drawNode()` already calls `node.arrange()` every frame.
 */
function hideFileWidget(state) {
  // BOTH flags, deliberately (2026-07-29, owner's "uptick in issues using my
  // mac" report): litegraph's canvas renderer hides on `widget.hidden`, but
  // the Vue-nodes renderer ("New node design") decides visibility from
  // `widget.options.hidden` (useProcessedWidgets.ts: `options.hidden ?? false`,
  // verified in this rig's frontend source maps) and IGNORES `widget.hidden`
  // -- so with only the canvas flag, this internal widget leaked into the Vue
  // node as a raw editable text field. Canvas mode ignores `options.hidden`
  // right back, so setting both is safe everywhere.
  state.fileWidget.hidden = true
  state.fileWidget.options = { ...(state.fileWidget.options || {}), hidden: true }
  state.node.graph?.setDirtyCanvas(true, true)
}

/**
 * Wraps the `file` widget's callback for two independent reasons that share
 * one seam: (1) the pre-existing debounced-reload-on-change
 * (onFileWidgetChanged), and (2) FORMAT.md §7.2's remote read-only guard —
 * a remote (`is_local: false`) viewer's edit is reverted here instead of
 * via `widget.disabled` (see the file header's "Remote gating" paragraph
 * for why that flag is unusable for this; belt-and-suspenders now that
 * hideFileWidget() above means no UI surface can reach this callback with
 * a changed value at all).
 */
function wireFileWidget(state) {
  const widget = state.fileWidget
  const original = widget.callback
  state.lastKnownFileValue = widget.value
  widget.callback = function (value, ...rest) {
    // 2026-08-03 defense-in-depth: a value arriving via a LIVE LINK on the
    // file widget-input (a Primitive/String node the workflow's author
    // wired on the HOST) is workflow-authored host state, not a remote
    // hand-edit -- reverting it would silently swap in the stale baseline
    // at queue time (graphToPrompt fires applyToGraph -> widget.callback).
    // The revert below is for hand edits only.
    const fileInput = state.node.inputs?.find?.((inp) => inp?.widget?.name === 'file')
    const linkDriven = !!fileInput && fileInput.link != null
    if (state.isLocal === false && value !== state.lastKnownFileValue && !linkDriven) {
      widget.value = state.lastKnownFileValue
      // 2026-07-27 (owner: "I can browse to the file now but selecting it
      // does nothing. It continues to show the local .md file no matter what
      // I do."): this revert is a legitimate rule (FORMAT.md §7.2 -- a remote
      // browser may not repoint a host-machine file) but it used to announce
      // itself ONLY in the small status line, which reads exactly like
      // "nothing happened". A toast makes the refusal, and its reason,
      // impossible to miss.
      setStatus(state, 'The host machine controls which file this node reads.')
      toast(
        'warn',
        'File not changed',
        'This browser is not on the machine running ComfyUI, so it cannot ' +
          'repoint this node at a different file. Open ComfyUI from that ' +
          'machine to change it.'
      )
      state.node.graph?.setDirtyCanvas(true, true)
      return undefined
    }
    state.lastKnownFileValue = value

    let result
    if (typeof original === 'function') {
      try {
        result = original.apply(this, [value, ...rest])
      } catch (error) {
        api.warn('original file widget callback threw', error)
      }
    }
    try {
      onFileWidgetChanged(state)
    } catch (error) {
      api.warn('notebook file-change handler threw', error)
    }
    return result
  }
}

/**
 * Reload the panel AFTER `configure()` restores the saved `file` value --
 * THE fix for the owner's "every time I load a workflow I have to
 * re-select the location of my Notebook .md file" (2026-07-27, reproduced
 * on the real `app.loadGraphData` path).
 *
 * Sequence, before this existed: `attach()` fires `reloadNow()`
 * immediately, and at that moment the `file` widget still holds its BACKEND
 * DEFAULT, because litegraph restores `widgets_values` LAST -- after the
 * node is constructed and added. So the panel loaded the default file's
 * entries; then `configure()` put the saved path into the widget WITHOUT
 * firing its callback (litegraph assigns `widget.value` directly), leaving
 * `state.file` (what the panel shows) and `fileWidget.value` (what the node
 * will actually READ on a Run) permanently disagreeing. The node ran the
 * right file while showing the wrong one -- and every attempt to fix it by
 * re-picking that same path in Browse... hit `setFileWidgetValue`'s
 * equal-value early return and did nothing.
 *
 * `onConfigure` fires at the END of `configure()` (both whole-workflow load
 * and a pasted/cloned node), which is the one hook that can see the
 * restored value. Same lesson, same week, as the EPS Distributor's
 * restore-path bug (FORMAT.md section 6.11): a fresh in-session node is NOT
 * the same code path as one restored from disk, and only the latter is what
 * users actually have.
 *
 * `lastKnownFileValue` is re-synced here too: `wireFileWidget` captured it
 * at attach time (the default), and for a REMOTE viewer
 * (`isLocal === false`) the read-only guard reverts edits back to it -- so
 * a stale capture would have let a remote browser silently rewrite a
 * loaded workflow's saved path back to the default.
 */
function wireConfigureReload(state) {
  const node = state.node
  const originalOnConfigure = node.onConfigure
  node.onConfigure = function (info) {
    let result
    if (typeof originalOnConfigure === 'function') {
      try {
        result = originalOnConfigure.apply(this, arguments)
      } catch (error) {
        api.warn('original onConfigure threw', error)
      }
    }
    try {
      const restored = state.fileWidget.value ?? ''
      state.lastKnownFileValue = restored
      // `|| state.loadError`: a failed load must be re-attempted on the
      // next configure even when the widget value didn't change (2026-08-03).
      if (restored !== state.file || state.loadError) {
        state.configureReloaded = true // v0.68.1: the deferred attach-time load stands down
        reloadNow(state).catch((error) =>
          api.warn('notebook reload after configure failed', error)
        )
      }
      // Provenance M3: a pin arrives the same way the saved file path does
      // -- configure() assigns `widget.value` directly, no callback -- so
      // this is the one reconcile point for a dropped baked image / a
      // saved pinned workflow. Paints the badge without a click; the
      // reload above (or the live entries already loaded) feeds the drift
      // comparison as soon as it lands.
      syncPinnedFromWidget(state)
      // Unsaved-edit drafts (v0.86.0): same "configure() bypasses
      // callbacks" reason, so a workflow saved mid-audition restores its
      // hint without a click.
      syncDraftsFromWidget(state)
    } catch (error) {
      api.warn('post-configure notebook reload failed', error)
    }
    return result
  }
}

function onFileWidgetChanged(state) {
  if (state.fileChangeDebounceTimer) clearTimeout(state.fileChangeDebounceTimer)
  state.fileChangeDebounceTimer = setTimeout(() => {
    state.fileChangeDebounceTimer = null
    if (state.fileWidget.value === state.file) return
    reloadNow(state).catch((error) => api.warn('notebook reload after file change failed', error))
  }, FILE_CHANGE_DEBOUNCE_MS)
}

/** v0.68.1: the debounced §7.2 search repaint -- see SEARCH_DEBOUNCE_MS.
 * Only the `input` listener schedules; Escape repaints at once. */
function scheduleSearchRender(state) {
  clearTimeout(state.searchTimer)
  state.searchTimer = setTimeout(() => {
    state.searchTimer = null
    renderList(state)
  }, SEARCH_DEBOUNCE_MS)
}

function wireNodeCleanup(state) {
  const node = state.node
  const originalOnRemoved = node.onRemoved
  node.onRemoved = function (...args) {
    let result
    if (typeof originalOnRemoved === 'function') {
      try {
        result = originalOnRemoved.apply(this, args)
      } catch (error) {
        api.warn('original node onRemoved threw', error)
      }
    }
    try {
      teardown(state)
    } catch (error) {
      api.warn('notebook teardown failed', error)
    }
    return result
  }
}

function teardown(state) {
  if (state.deleteConfirmTimer) clearTimeout(state.deleteConfirmTimer)
  if (state.fileChangeDebounceTimer) clearTimeout(state.fileChangeDebounceTimer)
  if (state.attachLoadTimer) clearTimeout(state.attachLoadTimer) // v0.68.1
  if (state.searchTimer) clearTimeout(state.searchTimer) // v0.68.1
  if (state.draftSyncTimer) clearTimeout(state.draftSyncTimer) // v0.86.0
  // File panel rework (FORMAT.md §7.2 amendment) — see updateFilePanelPath().
  state.filePanelResizeObserver?.disconnect()
  // A node removal mid-drag (e.g. undo, right-click delete) would otherwise
  // leak the drag's window-level pointermove/pointerup/pointercancel
  // listeners forever — see onEntryPointerDown().
  state.drag?.cleanup?.()
  // The Browse… picker lives on document.body, not inside this node's own
  // DOM — it must be torn down explicitly, or a node removed mid-picker
  // would leak it (and its window-level keydown listener) forever.
  closeBrowsePicker(state)
  // Invalidate any in-flight fetches so their `.then` handlers no-op.
  state.loadToken += 1
  state.selectToken += 1
}

// ---------------------------------------------------------------------------
// Remote gating (FORMAT.md §7.2 amendment) — see the file header's "File
// panel + remote gating" paragraph. `GET /lora_library/config` is cached at
// MODULE scope (every attached LoraLibraryNotebook node shares one fetch)
// with a short TTL, and concurrent callers de-dupe onto one in-flight
// promise.
// ---------------------------------------------------------------------------

const CONFIG_CACHE_TTL_MS = 60000

let cachedConfig = null
let cachedConfigAt = 0
let cachedConfigPromise = null

function fetchConfig() {
  if (cachedConfigPromise) return cachedConfigPromise
  cachedConfigPromise = api
    .getJson('/lora_library/config')
    .then((data) => {
      cachedConfig = data
      cachedConfigAt = Date.now()
      return data
    })
    .finally(() => {
      cachedConfigPromise = null
    })
  return cachedConfigPromise
}

function getConfig() {
  if (cachedConfig && Date.now() - cachedConfigAt < CONFIG_CACHE_TTL_MS) {
    return Promise.resolve(cachedConfig)
  }
  return fetchConfig()
}

/**
 * Refreshes `state.isLocal` from (cached) `/config` and applies it to the
 * file-panel buttons + the `file` widget's edit-guard (wireFileWidget).
 * Never throws: a failed fetch is logged and leaves `state.isLocal`
 * whatever it already was (`null`/unknown reads as "local" everywhere this
 * is checked with `=== false`) — this fails OPEN rather than disabling
 * functionality over a network hiccup, this file's usual posture.
 */
async function refreshRemoteGating(state) {
  let config
  try {
    config = await getConfig()
  } catch (error) {
    api.warn('could not load /lora_library/config; treating this node as local', error)
    return
  }
  state.isLocal = config?.is_local !== false
  state.remoteDirs = Array.isArray(config?.remote_dirs) ? config.remote_dirs : []
  state.libraryDir = typeof config?.library_dir === 'string' ? config.library_dir : null
  updateRemoteGatingUi(state)
}

/** Drops the module-scope /config cache so the next getConfig() re-fetches —
 * needed after the share toggle writes, since `remote_dirs` is part of that
 * cached payload and N attached nodes share it. */
function invalidateConfigCache() {
  cachedConfig = null
  cachedConfigAt = 0
}

/** Rough dirname of a resolved path, for both POSIX and Windows separators.
 * Only used to name the folder in the share toggle — the SERVER re-derives
 * and validates the real parent, so this never has to be exact. */
function parentDirOf(fullPath) {
  const value = String(fullPath || '')
  const cut = Math.max(value.lastIndexOf('/'), value.lastIndexOf('\\'))
  return cut > 0 ? value.slice(0, cut) : ''
}

/** True when `fullPath` sits inside one of `roots` — a plain path-segment
 * prefix test, so `/nas/docs` never matches `/nas/docs-private`. Advisory
 * only: it decides whether to OFFER the toggle. The server's own §2 check is
 * the authority and re-derives everything (see notebook_path_error). */
function pathIsInsideAny(fullPath, roots) {
  const value = String(fullPath || '')
  if (!value) return false
  return (roots || []).some((root) => {
    const r = String(root || '').replace(/[/\\]+$/, '')
    if (!r) return false
    if (value === r) return true
    return value.startsWith(r + '/') || value.startsWith(r + '\\')
  })
}

function updateRemoteGatingUi(state) {
  const remote = state.isLocal === false
  if (state.browseBtn) state.browseBtn.style.display = remote ? 'none' : ''
  if (state.openFolderBtn) state.openFolderBtn.style.display = remote ? 'none' : ''
  if (state.filePanelNoteEl) {
    state.filePanelNoteEl.textContent = remote ? 'Host machine controls this file' : ''
    state.filePanelNoteEl.title = remote
      ? 'The host machine controls which file this node reads.'
      : ''
  }
  updateShareToggle(state)
}

/**
 * The FORMAT.md §2 "Share with remote browsers" toggle (owner report
 * 2026-07-29: a NAS notebook that worked on the Linux box running ComfyUI
 * 403'd from his Mac).
 *
 * Shown ONLY to a local viewer, and only when the current file sits outside
 * everything remote callers can already reach — i.e. exactly when a second
 * machine would be refused. Local-only is not cosmetic: `POST
 * /lora_library/remote_dirs` is loopback-only for the same reason `POST
 * /config` is, because this list IS the boundary §2 enforces, so a remote
 * caller able to extend it could grant itself the arbitrary-file read the
 * boundary denies. Placing the control here means the fix is one click on the
 * machine where the notebook already works.
 */
function updateShareToggle(state) {
  const wrap = state.shareToggleWrapEl
  if (!wrap) return
  const fullPath = state.resolvedFile || ''
  const roots = [state.libraryDir, ...(state.remoteDirs || [])].filter(Boolean)
  const reachable = pathIsInsideAny(fullPath, roots)
  const folder = parentDirOf(fullPath)
  // A remote viewer gets no control (it could not use it) — just the note
  // above, plus whatever §2 error the request itself reported.
  const offer = state.isLocal !== false && fullPath && folder && !reachable
  wrap.style.display = offer ? '' : 'none'
  if (!offer) return
  state.shareToggleEl.checked = false
  state.shareToggleLabelEl.textContent = 'Share this folder with remote browsers'
  state.shareToggleLabelEl.title =
    `Lets a browser on another machine open notebooks in ${folder}. ` +
    'Without this, opening this workflow from another machine reports that ' +
    'the file is outside the shared library folder. Only this machine can ' +
    'grant it.'
}

/** Commits the share toggle. Re-reads /config afterwards so the toggle (and
 * every other attached node's copy) reflects what the server now allows. */
async function onShareToggleChange(state) {
  const folder = parentDirOf(state.resolvedFile || '')
  if (!folder) return
  const allow = Boolean(state.shareToggleEl?.checked)
  try {
    await api.postJson('/lora_library/remote_dirs', { dir: folder, allow })
    invalidateConfigCache()
    await refreshRemoteGating(state)
    setStatus(
      state,
      allow
        ? `Shared ${folder} — this notebook can now be opened from another machine.`
        : `Stopped sharing ${folder}.`
    )
  } catch (error) {
    api.warn('could not update the remote allow-list', error)
    if (state.shareToggleEl) state.shareToggleEl.checked = !allow
    setStatus(state, `Could not share that folder: ${error.message}`)
  }
}

// ---------------------------------------------------------------------------
// File panel: resolved path + Browse…/Open folder (FORMAT.md §7.2 amendment)
// ---------------------------------------------------------------------------

function buildFilePanel(state) {
  state.filePanelPathEl = el('div', { className: 'llnb-filepanel-path' })
  state.filePanelNoteEl = el('div', { className: 'llnb-filepanel-note' })
  state.browseBtn = el('button', {
    className: 'llnb-btn llnb-btn-small',
    text: 'Browse…',
    attrs: { title: 'Pick a notebook .md file on the server' }
  })
  state.openFolderBtn = el('button', {
    className: 'llnb-btn llnb-btn-small',
    text: 'Open folder',
    attrs: { title: "Reveal this file's folder on the server machine" }
  })

  state.browseBtn.addEventListener('click', () => {
    if (state.busy) return
    openBrowsePicker(state)
  })
  state.openFolderBtn.addEventListener('click', () => {
    onOpenFolderClick(state).catch((error) => api.warn('open folder failed', error))
  })

  // FORMAT.md §2 share toggle (owner report 2026-07-29) — hidden by default;
  // updateShareToggle() reveals it only for a LOCAL viewer looking at a file
  // no remote caller could reach. See that function for why local-only.
  state.shareToggleEl = el('input', {
    className: 'llnb-share-toggle-box',
    attrs: { type: 'checkbox', id: `llnb-share-${state.node?.id ?? 'x'}` }
  })
  state.shareToggleLabelEl = el('label', {
    className: 'llnb-share-toggle-label',
    attrs: { for: state.shareToggleEl.id }
  })
  state.shareToggleWrapEl = el('div', { className: 'llnb-share-toggle' }, [
    state.shareToggleEl,
    state.shareToggleLabelEl
  ])
  state.shareToggleWrapEl.style.display = 'none'
  state.shareToggleEl.addEventListener('change', () => {
    onShareToggleChange(state).catch((error) => api.warn('share toggle failed', error))
  })

  const actions = el('div', { className: 'llnb-filepanel-actions' }, [state.browseBtn, state.openFolderBtn])
  // Reworked 2026-07-19: path + buttons share one row (full-width path
  // control, "what's the point of the file field at top, replace it with
  // this"); the host-machine note (populated only when remote — see
  // updateRemoteGatingUi()) gets its OWN row below, never squeezed inline.
  const row = el('div', { className: 'llnb-filepanel-row' }, [state.filePanelPathEl, actions])
  const panel = el('div', { className: 'llnb-filepanel' }, [
    row,
    state.filePanelNoteEl,
    state.shareToggleWrapEl
  ])

  // Re-fit the path's front-truncation on a node resize too — "full width"
  // is a live property of the bar's current size, not just its size at
  // load time. Harmless if this frontend's runtime lacks ResizeObserver
  // (this file's usual "never throw, degrade gracefully" posture) —
  // updateFilePanelPath() just keeps whatever it last computed.
  if (typeof ResizeObserver === 'function') {
    state.filePanelResizeObserver = new ResizeObserver(() => updateFilePanelPath(state))
    state.filePanelResizeObserver.observe(state.filePanelPathEl)
  }

  return panel
}

/**
 * `text` shortened from the FRONT, so the tail — the filename, the part
 * that identifies which notebook this is — survives (FORMAT.md §7.2).
 * `maxChars` is a CHARACTER budget, not a pixel one — updateFilePanelPath()
 * below is what turns the bar's actual pixel width into one.
 *
 * Done in JS rather than with the `direction: rtl` CSS hack this file
 * originally used: that hack was paired with `unicode-bidi: plaintext`,
 * which resolves paragraph direction from the first STRONG character —
 * the Latin letters in any real path — so it silently re-established LTR
 * and put the ellipsis back on the tail, hiding exactly what it was meant
 * to keep. (Found live while porting this bar into comfyui-premiere-bridge.)
 */
function frontTruncate(text, maxChars = 56) {
  const value = String(text ?? '')
  if (value.length <= maxChars) return value
  return `…${value.slice(-(maxChars - 1))}`
}

/** Average glyph width (px) of `.llnb-filepanel-path`'s monospace font at
 * its 10px font-size — enough precision to estimate "how many characters
 * fit," not to hit an exact pixel width. */
const FILEPANEL_PATH_AVG_CHAR_PX = 6.4

/**
 * File panel rework (FORMAT.md §7.2 amendment, owner ask 2026-07-19 "make
 * this full width so it doesn't need to be trimmed"): always sets the FULL
 * resolved path first, then only front-truncates once a real DOM overflow
 * (`scrollWidth > clientWidth`) says it genuinely doesn't fit at the bar's
 * CURRENT width — never at a fixed character budget regardless of the
 * node's actual size. Re-run on every reload (via updateFilePanelPath()'s
 * callers) and on a node resize (the ResizeObserver buildFilePanel() wires
 * up), so "genuinely overflows" stays true to whatever the bar's width
 * actually is right now.
 */
function updateFilePanelPath(state) {
  if (!state.filePanelPathEl) return
  // During a load error there is no server-resolved path; show the
  // ATTEMPTED widget value so the saved path never vanishes from view
  // (the "reset to defaults" illusion the 2026-08-03 report described).
  const path = state.resolvedFile || (state.loadError ? state.loadError.file : '') || ''
  const pathEl = state.filePanelPathEl
  pathEl.title = path
  pathEl.textContent = path
  if (!path || pathEl.scrollWidth <= pathEl.clientWidth + 1) return // fits (or empty) as-is
  const budget = Math.max(8, Math.floor(pathEl.clientWidth / FILEPANEL_PATH_AVG_CHAR_PX))
  pathEl.textContent = frontTruncate(path, budget)
}

async function onOpenFolderClick(state) {
  if (!state.resolvedFile) return
  // Finding 7 (2026-08-26 responsiveness round): acknowledge the click
  // IMMEDIATELY, same idiom as openBrowsePicker()'s onPickFile below ("2026-
  // 07-27... acknowledge the click IMMEDIATELY") -- a GIL-busy server can
  // make this round trip take a while, and without an instant status change
  // a slow-to-resolve click reads exactly like a dead one.
  setStatus(state, 'Opening folder…')
  try {
    await api.postJson('/lora_library/notebook/open_folder', { file: state.resolvedFile })
  } catch (error) {
    api.warn('open folder failed', error)
    setStatus(state, `Could not open folder: ${error.message}`)
  }
}

/**
 * Join of a `GET /fs/list` `dir` + a names-only child entry's `name`.
 * @param {string} dir
 * @param {string} name
 * @param {string} [sep] - STANDARD-fs-browse.md's server-reported `sep`
 * (`GET /lora_library/fs/list`'s response field) — preferred when given,
 * since it's authoritative for the machine actually being browsed. Falls
 * back to the old heuristic (present-backslash-without-forward-slash) only
 * for call sites with no response to read a `sep` from (e.g.
 * `dirnameOfServerPath` below, seeding the picker from the resolved file's
 * own path).
 */
function joinServerPath(dir, name, sep) {
  const separator = sep || (dir.includes('\\') && !dir.includes('/') ? '\\' : '/')
  return dir.endsWith(separator) ? `${dir}${name}` : `${dir}${separator}${name}`
}

/** Best-effort parent-folder guess for seeding the picker at the resolved
 * file's own folder; null (→ server default, the library folder) if it
 * can't tell. */
function dirnameOfServerPath(path) {
  if (!path) return null
  const sep = path.includes('\\') && !path.includes('/') ? '\\' : '/'
  const idx = path.lastIndexOf(sep)
  if (idx <= 0) return null
  return path.slice(0, idx)
}

const PICKER_OVERLAY_ID = 'llnb-picker-overlay'

/** The one open picker's session -- a Notebook `state`, or the private
 * session object pickServerFolder() makes. "Only one picker at a time,
 * ever" now spans BOTH callers: opening either closes the other first, so
 * the other's capture-phase Escape listener (and, for a folder pick, its
 * pending promise -- resolved null) can never dangle behind a removed
 * overlay. */
let activePickerSession = null

/**
 * Tears the picker down: overlay, capture-phase Escape listener (same flag
 * as the registration in openPickerDialog(), or it silently fails to
 * detach -- see the drag listeners' identical note), the active-session
 * mark, and the session's close hook (`pickerOnClose`, pickServerFolder()'s
 * "cancelled" path; a deliberate pick clears it before closing). Called
 * from Cancel/Escape/backdrop, from a file pick, from the next open, and
 * from the Notebook's own teardown(). Safe to call when nothing is open.
 *
 * OWNERSHIP: the overlay is removed and the close hook fired only for the
 * session that actually owns the open picker (`activePickerSession`). Two
 * reasons, both found on the rig 2026-08-22: openPickerDialog() calls this
 * on the session it is about to open (a re-open of the same session must
 * start clean) -- firing the hook there resolved pickServerFolder() null
 * the instant it opened, so a later pick was a no-op; and a Notebook
 * teardown() while a CONTROLLER's picker is open must not pull that other
 * session's overlay out from under it. The keydown listener is removed
 * unconditionally (it is per session; removing a missing one is a no-op).
 * @param {object} session - the Notebook `state` or a folder-pick session
 */
function closeBrowsePicker(session) {
  const owns = activePickerSession === session
  if (owns) {
    document.getElementById(PICKER_OVERLAY_ID)?.remove()
    activePickerSession = null
  }
  if (session.pickerKeydownHandler) {
    window.removeEventListener('keydown', session.pickerKeydownHandler, { capture: true })
    session.pickerKeydownHandler = null
  }
  if (!owns) return
  const onClose = session.pickerOnClose
  session.pickerOnClose = null
  onClose?.()
}

/**
 * FORMAT.md §7.2's Browse… dialog -- the Notebook's FILE picker. The
 * Notebook `state` is the picker session (it carries
 * `pickerKeydownHandler` + `pickerNavToken`); the spec says "file mode,
 * start at the resolved file's own folder, and a clicked .md row becomes
 * the `file` widget's value". Everything else -- the modal, the
 * type-or-paste path row, `fs/list` walking, Escape -- is
 * openPickerDialog(), shared with pickServerFolder() below.
 */
function openBrowsePicker(state) {
  state.pickerSpec = {
    mode: 'file',
    startDir: dirnameOfServerPath(state.resolvedFile),
    onPickFile: (chosen) => {
      // 2026-07-27 (owner: "I can browse to the file now but selecting it
      // does nothing"): acknowledge the click IMMEDIATELY, naming the path.
      // The reload behind it is debounced and async, so without this a
      // selection that is later refused or fails looks like a dead click --
      // and, diagnostically, the ABSENCE of this line tells us the click
      // never reached this handler at all.
      setStatus(state, `Opening ${chosen}...`)
      api.log?.(`notebook: picked ${chosen}`)
      setFileWidgetValue(state, chosen)
    }
  }
  openPickerDialog(state)
}

/**
 * Pick a FOLDER on the server machine -- the reusable half of the
 * Notebook's Browse… picker, for callers that want a directory rather
 * than a .md file (the State Controller's Library-folder Browse…,
 * FORMAT.md §6.3). Same modal, CSS, `GET /lora_library/fs/list` walking
 * (drive list / `..` / typed-or-pasted path row), capture-phase Escape and
 * single-picker-at-a-time rule as the Notebook's; directory-oriented:
 * folders navigate, files are shown dimmed as context (never choices), and
 * the footer gains a confirm button (`confirmLabel`, default "Use this
 * folder") that picks the folder currently LISTED -- disabled at the Top
 * Level (its entries are roots, not a folder) and after a failed listing.
 * When `confirmPrompt` is given, the confirm button first swaps the footer
 * for that question (text for the candidate path) + `confirmFinalLabel` /
 * Back -- the machine-wide-action "ask twice" step, inside the dialog so a
 * 300 px node never has to host a confirm strip of its own.
 *
 * Resolves with the absolute folder path, or null on Cancel / Escape /
 * backdrop click / the next picker opening over it / a remote viewer
 * (`isLocal === false`: the route is loopback-only, FORMAT.md §5, so the
 * dialog never opens) / no DOM (under Node). Never rejects.
 * @param {{title?: string, startDir?: string|null, isLocal?: boolean|null,
 *   confirmLabel?: string, confirmPrompt?: (path: string) => string,
 *   confirmFinalLabel?: string}} [options]
 * @returns {Promise<string|null>}
 */
export function pickServerFolder(options = {}) {
  const { title, startDir, isLocal, confirmLabel, confirmPrompt, confirmFinalLabel } = options || {}
  if (isLocal === false) {
    api.warn('pickServerFolder: fs/list is loopback-only (FORMAT.md §5); not opening for a remote viewer')
    return Promise.resolve(null)
  }
  if (typeof document === 'undefined' || !document.body) return Promise.resolve(null)
  return new Promise((resolve) => {
    let settled = false
    const finish = (value) => {
      if (settled) return
      settled = true
      resolve(value)
    }
    const session = {
      pickerKeydownHandler: null,
      pickerNavToken: 0,
      pickerOnClose: () => finish(null),
      pickerSpec: {
        mode: 'folder',
        title: typeof title === 'string' ? title : 'Choose a folder',
        startDir: typeof startDir === 'string' && startDir ? startDir : null,
        confirmLabel: typeof confirmLabel === 'string' && confirmLabel ? confirmLabel : 'Use this folder',
        confirmPrompt: typeof confirmPrompt === 'function' ? confirmPrompt : null,
        confirmFinalLabel:
          typeof confirmFinalLabel === 'string' && confirmFinalLabel ? confirmFinalLabel : 'Confirm',
        onPickFolder: (path) => finish(path)
      }
    }
    openPickerDialog(session)
  })
}

/**
 * The picker dialog proper, shared by openBrowsePicker() (file mode) and
 * pickServerFolder() (folder mode) -- `session.pickerSpec` says which.
 * Deliberately attached to `document.body` rather than nested inside a
 * widget's own root: a DOM widget's box is only ever as tall as its node
 * currently is (as small as MIN_WIDGET_HEIGHT), and litegraph can
 * reposition/clip it during pan/zoom (see `hideOnZoom` on attachDomWidget()
 * above) — a file browser confined to that box would be cramped on a small
 * node and would fight the same clipping. A fixed, centered overlay on
 * `document.body` stays a comfortable, constant size regardless of the
 * node's size/position, at the cost of managing its own teardown by hand
 * (closeBrowsePicker(), called from here, Escape, a backdrop click, a pick,
 * and the Notebook's own teardown()).
 * @param {object} session - Notebook `state` or a pickServerFolder() session;
 *   carries `pickerKeydownHandler`, `pickerNavToken`, `pickerSpec`
 */
function openPickerDialog(session) {
  injectStyles() // a controller may open this before any Notebook attached
  // only one picker at a time, ever -- across both callers
  if (activePickerSession && activePickerSession !== session) closeBrowsePicker(activePickerSession)
  closeBrowsePicker(session)
  activePickerSession = session
  const spec = session.pickerSpec || {}

  const backdrop = el('div', { className: 'llnb-picker-backdrop', attrs: { id: PICKER_OVERLAY_ID } })
  const dialog = el('div', { className: 'llnb-picker' })
  backdrop.append(dialog)
  backdrop.addEventListener('mousedown', (event) => {
    if (event.target === backdrop) closeBrowsePicker(session)
  })
  dialog.addEventListener('mousedown', (event) => event.stopPropagation())
  document.body.append(backdrop)

  session.pickerKeydownHandler = (event) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      closeBrowsePicker(session)
    }
  }
  // CAPTURE-phase, deliberately (the FORMAT.md §7.5 rule every other
  // window-level listener in this file already follows): the picker's own
  // path input below — and the Notebook's name field/textarea — call
  // stopPropagation() on every keydown, so a bubble-phase listener never
  // sees Escape while any of them has focus, exactly the state the user is
  // in right after typing a path. closeBrowsePicker() must remove with the
  // same flag.
  window.addEventListener('keydown', session.pickerKeydownHandler, { capture: true })

  // Type-or-paste-a-path input (FORMAT.md §5/§7.2, owner's NAS fix) — a
  // PERSISTENT row above the listing (unlike `contentEl` below, which every
  // navigation replaces wholesale), so a failed lookup never wipes out what
  // the user just typed; its error reports into `pathErrorEl`, its own
  // inline slot, right under the input.
  const pathInput = el('input', {
    className: 'llnb-input',
    attrs: { type: 'text', placeholder: 'Type or paste an absolute path (incl. \\\\server\\share)…' }
  })
  const goBtn = el('button', { className: 'llnb-btn llnb-btn-small', text: 'Go' })
  const pathErrorEl = el('div', { className: 'llnb-picker-patherror' })
  const contentEl = el('div', { className: 'llnb-picker-content' })

  const goToTypedPath = () => {
    const typed = pathInput.value.trim()
    if (!typed) return
    loadPickerDir(session, dialog, contentEl, pathErrorEl, typed)
  }
  goBtn.addEventListener('click', goToTypedPath)
  pathInput.addEventListener('keydown', (event) => {
    event.stopPropagation()
    if (event.key === 'Enter') {
      event.preventDefault()
      goToTypedPath()
    }
  })

  const pathRow = el('div', { className: 'llnb-picker-pathrow' }, [pathInput, goBtn])
  const titleEl = el('div', { className: 'llnb-picker-title', text: spec.title || '' })
  dialog.append(titleEl, pathRow, pathErrorEl, contentEl)

  loadPickerDir(session, dialog, contentEl, pathErrorEl, spec.startDir || null)
}

/**
 * Loads `dir` (or the library default, when falsy) into `contentEl` — every
 * navigation in the picker goes through here: the initial load, a folder/
 * drive/`..` row click (renderPickerDialog()), and the path input's Enter/
 * Go (openBrowsePicker()). A failed lookup (400 — an unreadable/nonexistent
 * path, most commonly from the typed-path input) reports INLINE into
 * `pathErrorEl`, right under the path input, rather than blanking the
 * whole dialog — "keeps dialog open" per the owner ask — leaving the path
 * input itself untouched so the user can just fix it and retry.
 */
async function loadPickerDir(session, dialog, contentEl, pathErrorEl, dir) {
  // A lapped response — success OR failure — must neither render nor touch
  // the error slot: the navigation that superseded this one owns the
  // dialog now (see pickerNavToken's state comment; mirrors
  // loadEntryText()'s loadToken guard).
  const navToken = ++session.pickerNavToken
  pathErrorEl.textContent = ''
  contentEl.replaceChildren(el('div', { className: 'llnb-picker-status', text: 'Loading…' }))
  let data
  try {
    data = await api.getJson('/lora_library/fs/list', dir ? { dir } : undefined)
  } catch (error) {
    if (navToken !== session.pickerNavToken) return // superseded by a later navigation
    api.warn('fs/list failed', error)
    pathErrorEl.textContent = error.message || 'Could not list that path.'
    contentEl.replaceChildren(
      el('div', { className: 'llnb-picker-header', text: 'Browse' }),
      buildPickerFooter(session, null) // folder mode: nothing listed, nothing to confirm
    )
    return
  }
  if (navToken !== session.pickerNavToken) return // superseded by a later navigation
  renderPickerDialog(session, dialog, contentEl, pathErrorEl, data)
}

function renderPickerDialog(session, dialog, contentEl, pathErrorEl, data) {
  const spec = session.pickerSpec || {}
  const folderMode = spec.mode === 'folder'
  // FS_ROOTS ("ROOTS", STANDARD-fs-browse.md's fs/list sentinel): the
  // synthetic top-level listing (default library dir + Home + platform
  // drives/volumes) — "Top Level" reads better than the raw sentinel string
  // as a header (2026-07-19: no longer just a drive list, so "Drives" alone
  // stopped being accurate).
  const isRootsList = data.dir === FS_ROOTS
  const headerText = isRootsList ? 'Top Level' : data.dir
  const header = el('div', { className: 'llnb-picker-header', text: headerText, attrs: { title: data.dir } })
  const list = el('div', { className: 'llnb-picker-list' })

  if (data.parent) {
    const upRow = el('div', { className: 'llnb-picker-row', text: '.. (parent folder)' })
    upRow.addEventListener('click', () => loadPickerDir(session, dialog, contentEl, pathErrorEl, data.parent))
    list.append(upRow)
  }
  for (const dir of data.dirs || []) {
    const row = el('div', { className: 'llnb-picker-row', text: `📁 ${dir.name}` })
    // At the ROOTS level, each entry is independently rooted (this pack's
    // default library dir, "Home", a drive/volume) and carries its own
    // `path` (STANDARD-fs-browse.md's documented ROOTS extension) — joining
    // it onto the literal "ROOTS" sentinel like a normal child would produce
    // a nonsense path (the original bug this fixed); navigate straight
    // there instead. Everywhere else, entries are names-only: join onto the
    // current `dir` + the server-reported `sep`.
    const target = isRootsList ? dir.path : joinServerPath(data.dir, dir.name, data.sep)
    row.addEventListener('click', () => loadPickerDir(session, dialog, contentEl, pathErrorEl, target))
    list.append(row)
  }
  for (const file of data.files || []) {
    if (folderMode) {
      // context only (a loras.md says "this is a library folder"), never a
      // choice -- dimmed and inert
      list.append(el('div', { className: 'llnb-picker-row llnb-picker-row-dim', text: `📄 ${file.name}` }))
      continue
    }
    const row = el('div', { className: 'llnb-picker-row', text: `📄 ${file.name}` })
    row.addEventListener('click', () => {
      const chosen = joinServerPath(data.dir, file.name, data.sep)
      closeBrowsePicker(session)
      spec.onPickFile?.(chosen)
    })
    list.append(row)
  }
  if (!data.parent && !(data.dirs || []).length && !(data.files || []).length) {
    list.append(
      el('div', {
        className: 'llnb-picker-empty',
        text: folderMode ? 'No subfolders here.' : 'No subfolders or .md files here.'
      })
    )
  }

  contentEl.replaceChildren(header, list, buildPickerFooter(session, data))
}

/**
 * The dialog's footer. File mode: Cancel. Folder mode (pickServerFolder()):
 * the confirm button for the folder currently LISTED (`data.dir`; none at
 * the Top Level or after a failed listing -> disabled) + Cancel; with
 * `spec.confirmPrompt`, the confirm click first swaps this footer for the
 * question + `confirmFinalLabel` / Back (Back rebuilds this footer).
 * @param {object} session @param {object|null} data - the `fs/list` payload
 *   this footer belongs to, or null when the listing failed
 */
function buildPickerFooter(session, data) {
  const spec = session.pickerSpec || {}
  const cancelBtn = el('button', { className: 'llnb-btn llnb-btn-small', text: 'Cancel' })
  cancelBtn.addEventListener('click', () => closeBrowsePicker(session))
  if (spec.mode !== 'folder') {
    return el('div', { className: 'llnb-picker-footer' }, [cancelBtn])
  }
  const candidate = data && typeof data.dir === 'string' && data.dir && data.dir !== FS_ROOTS ? data.dir : null
  const useBtn = el('button', { className: 'llnb-btn llnb-btn-small', text: spec.confirmLabel || 'Use this folder' })
  if (!candidate) useBtn.disabled = true
  const footer = el('div', { className: 'llnb-picker-footer' }, [useBtn, cancelBtn])
  useBtn.addEventListener('click', () => {
    if (!candidate) return
    if (typeof spec.confirmPrompt !== 'function') {
      commitPickedFolder(session, candidate)
      return
    }
    // Ask twice for a machine-wide action: the question replaces the footer.
    const yesBtn = el('button', { className: 'llnb-btn llnb-btn-small', text: spec.confirmFinalLabel || 'Confirm' })
    const backBtn = el('button', { className: 'llnb-btn llnb-btn-small', text: 'Back' })
    const strip = el('div', { className: 'llnb-picker-confirm' }, [
      el('div', { className: 'llnb-picker-confirm-text', text: spec.confirmPrompt(candidate) }),
      el('div', { className: 'llnb-picker-confirm-buttons' }, [yesBtn, backBtn])
    ])
    yesBtn.addEventListener('click', () => commitPickedFolder(session, candidate))
    backBtn.addEventListener('click', () => strip.replaceWith(buildPickerFooter(session, data)))
    footer.replaceWith(strip)
  })
  return footer
}

/** A deliberate folder pick: NOT a cancel, so the session's close hook is
 * cleared before the teardown, then the spec's callback gets the path. */
function commitPickedFolder(session, path) {
  const onPick = session.pickerSpec?.onPickFolder
  session.pickerOnClose = null
  closeBrowsePicker(session)
  onPick?.(path)
}

/**
 * The library-relative form of *value* when it sits INSIDE *libraryDir*,
 * else *value* untouched (v0.84.0, the forward half of the cross-OS path
 * fix — FORMAT.md §2). A path stored relative resolves against whatever
 * `library_dir` the READING machine has, so a workflow picked on the PC
 * opens on the Linux box without any healing at all; an absolute path only
 * survives the trip because `context.heal_foreign_absolute` rescues it.
 * Storing relative is therefore prevention, the healing is the cure.
 *
 * Boundary-safe: the match must land on a separator, so `/lib2/x.md` is
 * NOT inside `/lib`. Separator-insensitive on the comparison (a Windows
 * `Z:\docs` library dir vs a `Z:/docs/x.md` pick), and the result is
 * always POSIX-style — `Path("sub/x.md")` joins correctly on both
 * platforms, while a backslash would not survive a POSIX join.
 */
export function relativizeToLibrary(value, libraryDir) {
  if (typeof value !== 'string' || !value) return value
  if (typeof libraryDir !== 'string' || !libraryDir) return value
  const norm = (s) => s.replace(/\\/g, '/').replace(/\/+$/, '')
  const target = norm(value)
  const base = norm(libraryDir)
  if (!base || target === base) return value
  if (!target.startsWith(base + '/')) return value
  const rel = target.slice(base.length + 1)
  return rel || value
}

/** Writes `value` through the `file` widget's real setter+callback — the
 * exact same pattern syncEntryWidget() uses for `entry` — so picking a file
 * here behaves exactly like typing it in, including the debounced reload
 * (onFileWidgetChanged, via wireFileWidget) and that same wrapper's §7.2
 * read-only guard (moot in practice, since Browse… is itself hidden for a
 * remote caller — belt-and-suspenders all the same). */
function setFileWidgetValue(state, rawValue) {
  const widget = state.fileWidget
  // v0.84.0: a pick inside the library folder is stored RELATIVE, so the
  // workflow stays portable across machines (FORMAT.md §2). Outside the
  // library folder the absolute path is kept as-is -- there is nothing to
  // be relative TO.
  const value = relativizeToLibrary(rawValue, state.libraryDir)
  if (widget.value === value) {
    // Same value already in the widget -- but that does NOT mean the panel
    // is showing it (owner report 2026-07-27: "I have to re-select the
    // location of my Notebook .md file... and it doesn't take"). After a
    // workflow load the widget holds the SAVED path while the panel is
    // still showing whatever `attach()` loaded before `configure()` ran, so
    // picking that same path in Browse... used to early-return here and do
    // literally nothing -- the exact dead-end he was stuck in, escapable
    // only by recreating the node so the values differed again.
    // `state.file` is the file the panel is actually displaying, so a
    // mismatch means "reload", not "no-op".
    if (state.file !== value || state.loadError) {
      reloadNow(state).catch((error) =>
        api.warn('notebook reload after same-value reselect failed', error)
      )
    }
    return
  }
  widget.value = value
  try {
    widget.callback?.(value)
  } catch (error) {
    api.warn('file widget callback threw', error)
  }
  state.node.graph?.setDirtyCanvas(true, true)
}

// ---------------------------------------------------------------------------
// Session cache (file header "Session cache + known_mtime") — module scope,
// so a node recreated by a tab switch paints what the previous incarnation
// loaded. Keyed by the `file` value as the panel SENDS it; per file, never
// cross-file. Pure (no DOM) and exported for tests/test_notebook_restore_js.py.
// ---------------------------------------------------------------------------

/** file value (as sent) -> {payload, mtime} of the last successful load /
 * mutation of that file in this browser session. */
const notebookCache = new Map()

/** The status-row mark while a cached paint waits for its refresh fetch. */
const CACHE_HINT_REFRESHING = 'cached — refreshing…'

/**
 * @param {string} file - the `file` value exactly as sent in `GET /notebook`
 * @returns {{payload: object, mtime: number|null}|null}
 */
export function notebookCacheGet(file) {
  if (typeof file !== 'string') return null
  return notebookCache.get(file) || null
}

/**
 * Remember the last successful full payload for *file*. `mtime` is the
 * file's mtime as the server reported it (null when the file did not
 * exist, in which case reloadNow() sends no `known_mtime`).
 * @param {string} file @param {object} payload @param {number|null} mtime
 */
export function notebookCacheSet(file, payload, mtime) {
  if (typeof file !== 'string' || !payload || typeof payload !== 'object') return
  notebookCache.set(file, { payload, mtime: typeof mtime === 'number' ? mtime : null })
}

/**
 * The `known_mtime` short-circuit (FORMAT.md §5): a `GET /notebook` answer
 * of `{"ok": true, "unchanged": true, ...}` carries no entries and means
 * "what you painted is still the file". Anything else is a full payload.
 * @param {unknown} data
 * @returns {boolean}
 */
export function isUnchangedResponse(data) {
  return !!data && typeof data === 'object' && data.unchanged === true
}

/** Show/clear the subtle cache mark in the status row ('' clears). */
function setCacheHint(state, text) {
  if (!state.cacheHintEl) return
  state.cacheHintEl.textContent = text || ''
  state.cacheHintEl.title = text
    ? 'Showing the copy this browser loaded earlier while the file is re-read from disk.'
    : ''
}

/**
 * Fold a mutating POST's response into the session cache: `entries` /
 * `categories` / `exists` / resolved path from the panel's freshly-updated
 * state, body text from the panel's own corpus (kept current by
 * noteEntryText & co.), mtime from the response. Also advances
 * `state.paintedMtime` -- the panel now shows the file at that mtime -- so
 * the next `known_mtime` vouches for the post-mutation file. A response
 * without a numeric mtime leaves the cache unable to vouch (mtime null), so
 * the next reload fetches in full rather than guessing.
 */
function syncNotebookCache(state, data) {
  const file = state.file
  if (typeof file !== 'string' || !file || state.loadError) return
  const mtime = typeof data?.mtime === 'number' ? data.mtime : null
  const prev = notebookCacheGet(file)
  const payload = {
    ...(prev?.payload || {}),
    file: state.resolvedFile ?? prev?.payload?.file ?? null,
    exists: state.exists,
    mtime,
    entries: (state.entries || []).map((entry) => ({
      ...entry,
      text: state.entryTextByName[entry.name] ?? ''
    })),
    categories: Array.isArray(state.categories) ? state.categories : [],
    // Finding 5 (2026-08-26 responsiveness round): rides along so a cached
    // paint (reloadNow()'s instant repaint from THIS session's own cache)
    // restores category-mode's cache-served read too, same as `entries[].text`
    // above does for entry mode.
    category_descriptions: { ...state.categoryDescriptionByName },
    problems: Array.isArray(prev?.payload?.problems) ? prev.payload.problems : []
  }
  state.paintedMtime = mtime
  notebookCacheSet(file, payload, mtime)
}

/**
 * Adopt *mtime* as the file's CURRENT state after a successful write
 * (v0.85.1). `baseMtime` (what writes send as §3.5's `base_mtime`) and
 * `paintedMtime` (what the cached paint and the inline-rename path read)
 * used to be refreshed by different code paths, so one of the panel's own
 * writes could leave the other stale -- and the next write then 409'd with
 * "file changed on disk" even though nothing but this panel had touched
 * it (owner report 2026-08-28, reproduced: a stale base_mtime is refused
 * verbatim by check_conflict). One helper, both fields, every write.
 */
function noteFileMtime(state, mtime) {
  if (typeof mtime !== 'number') return
  state.baseMtime = mtime
  state.paintedMtime = mtime
}

/** The panel now knows *name*'s body: keep the search corpus (and thereby
 * the next cached paint) current -- also makes "searchable after Save" true
 * without waiting for the next full load. */
function noteEntryText(state, name, text) {
  if (typeof name !== 'string' || !name) return
  const body = typeof text === 'string' ? text : ''
  state.entryTextByName[name] = body
  state.searchCorpus.set(name, searchHaystack(name, body))
}

function forgetEntryText(state, name) {
  delete state.entryTextByName[name]
  state.searchCorpus.delete(name)
}

function renameEntryText(state, from, to) {
  if (from === to) return
  const body = state.entryTextByName[from]
  forgetEntryText(state, from)
  noteEntryText(state, to, body)
}

/** Whether `name`'s body is already known from the last include_text=1 load
 * (finding 1, 2026-08-26 responsiveness round) -- when true, loadEntryText()
 * populates the editor SYNCHRONOUSLY from it instead of round-tripping, so
 * the row highlight and the textarea can never visibly disagree while a
 * fetch is in flight. `hasOwnProperty` (not `name in ...`/truthiness) is
 * deliberate: an entry with an empty body is still a HIT. */
function hasCachedEntryText(state, name) {
  return Object.prototype.hasOwnProperty.call(state.entryTextByName, name)
}

/** noteEntryText()'s category-mode sibling (finding 5, 2026-08-26
 * responsiveness round): keeps `state.categoryDescriptionByName` (populated
 * from the same include_text=1 payload's new `category_descriptions` field
 * -- see applyNotebookPayload()) current across every category write, so a
 * later category click can be cache-served too. */
function noteCategoryDescription(state, name, description) {
  if (typeof name !== 'string' || !name) return
  state.categoryDescriptionByName[name] = typeof description === 'string' ? description : ''
}

function forgetCategoryDescription(state, name) {
  delete state.categoryDescriptionByName[name]
}

function renameCategoryDescription(state, from, to) {
  if (from === to) return
  const description = state.categoryDescriptionByName[from]
  forgetCategoryDescription(state, from)
  noteCategoryDescription(state, to, description)
}

/** hasCachedEntryText()'s category-mode sibling. */
function hasCachedCategoryDescription(state, name) {
  return Object.prototype.hasOwnProperty.call(state.categoryDescriptionByName, name)
}

// ---------------------------------------------------------------------------
// Loading the notebook list + auto-select
// ---------------------------------------------------------------------------

async function reloadNow(state) {
  const token = ++state.loadToken
  cancelDeleteConfirm(state)
  closeNewEntryRow(state)
  clearConflict(state)

  // FORMAT.md §7.2 remote gating: opportunistic re-check on every reload, on
  // top of the initial one at attach — cheap, since getConfig() is
  // module-level cached/de-duped (see "Remote gating" above).
  refreshRemoteGating(state).catch((error) => api.warn('config refresh failed', error))

  const file = state.fileWidget.value ?? ''
  // Session cache (file header): paint the last known payload of THIS file
  // at once -- same code path as a successful fetch -- when the panel is
  // not already showing it (a recreated node, a file switch, a Retry) or
  // shows an older/newer snapshot than the session's latest (two panels on
  // one file). The fetch below reconciles; its token discipline is
  // unchanged, and the cached paint's own editor fetch is superseded by it.
  const cached = notebookCacheGet(file)
  const showing = state.file === file && !state.loadError
  let paintedFromCache = false
  if (cached && (!showing || state.paintedMtime !== cached.mtime)) {
    paintedFromCache = true
    applyNotebookPayload(state, file, cached.payload).catch((error) =>
      api.warn('cached notebook paint failed', error)
    )
    setCacheHint(state, CACHE_HINT_REFRESHING)
  }
  // `known_mtime` (FORMAT.md §5): only what THIS panel is showing for this
  // file right now -- the backend may answer `unchanged` and we keep it.
  const params = { file, include_text: '1' }
  if ((showing || paintedFromCache) && typeof state.paintedMtime === 'number') {
    params.known_mtime = String(state.paintedMtime)
  }
  let data
  try {
    data = await api.getJson('/lora_library/notebook', params)
  } catch (error) {
    if (token !== state.loadToken) return
    setCacheHint(state, '')
    api.warn('failed to load notebook list', error)
    // Owner report 2026-08-03 ("something is making it feel brittle. It
    // should never reset unless the user resets it"): this branch used to
    // bare-return, abandoning the panel in its EMPTY pre-load state --
    // blank path bar, no list, the saved path visible NOWHERE (the file
    // widget is hidden in both renderers) -- which reads exactly like
    // "the node reset to defaults". Worse, `state.file` stayed null, and
    // every later write posted file:null, which the server resolves to the
    // DEFAULT notebook: interacting with the broken panel could genuinely
    // move data into the wrong file and overwrite the saved selection.
    // Now failure is an explicit, recoverable ERROR STATE that preserves
    // everything: the ATTEMPTED path becomes state.file (a write can never
    // again fall back to the default), the file panel keeps showing that
    // path, mutating actions refuse while the error stands (writesBlocked),
    // and the status line carries a Retry button.
    state.loadError = { file, message: error.message }
    state.file = file
    state.resolvedFile = null
    state.paintedMtime = null // the list now shows the error, not the file
    updateFilePanelPath(state)
    renderList(state)
    renderPinBar(state) // M3: a pinned badge now reads "library not loaded"
    updateDeleteButtonEnabled(state)
    showLoadError(state)
    toast(
      'error',
      'Could not open this notebook file',
      `${file} -- ${error.message}. The machine running ComfyUI has to be ` +
        'able to read this path. Nothing was changed: the saved path and ' +
        'selection are intact. Fix the connection (or share the folder on ' +
        'the host) and hit Retry.'
    )
    return
  }
  if (token !== state.loadToken) return
  setCacheHint(state, '')

  if (isUnchangedResponse(data)) {
    // FORMAT.md §5 `known_mtime` short-circuit: the file is still exactly
    // what this panel painted -- keep it (no re-render, no entry reset, the
    // cache entry and paintedMtime stand). Only when nothing was painted by
    // THIS call (an explicit Reload/unpin on an already-showing file) does
    // the editor half re-run, so the active entry's live text lands in the
    // editor again -- the list is untouched either way.
    if (!paintedFromCache) await loadActiveEditor(state)
    return
  }

  notebookCacheSet(file, data, typeof data.mtime === 'number' ? data.mtime : null)
  await applyNotebookPayload(state, file, data)
}

/**
 * Paint one full `GET /notebook` payload (or its cached copy) into the
 * panel: list + file panel + status, then the editor half
 * (loadActiveEditor). The ONE code path for the fresh fetch and the cached
 * instant paint alike (file header "Session cache"), so they can never
 * drift. `state.loadToken` discipline is the caller's: the editor fetch
 * inside captures the current token exactly as before.
 */
async function applyNotebookPayload(state, file, data) {
  state.loadError = null
  state.file = file
  state.paintedMtime = typeof data.mtime === 'number' ? data.mtime : null
  state.entries = Array.isArray(data.entries) ? data.entries : []
  // §7.2 search corpus: body text per entry (include_text=1 above). Built
  // fresh on every successful load, so search always reflects disk truth
  // as of the panel's own last refresh.
  state.entryTextByName = Object.fromEntries(
    state.entries.map((entry) => [entry.name, typeof entry.text === 'string' ? entry.text : ''])
  )
  buildSearchCorpus(state) // v0.68.1: lowercase once per load, not per keystroke
  // Finding 5 (2026-08-26 responsiveness round): category descriptions ride
  // the SAME include_text=1 payload now (routes_notebook.py's
  // `category_descriptions`), so category-mode clicks can be cache-served
  // exactly like entryTextByName above -- a plain object (not present on an
  // older backend / a non-include_text response) folds to `{}`.
  state.categoryDescriptionByName =
    data.category_descriptions && typeof data.category_descriptions === 'object'
      ? { ...data.category_descriptions }
      : {}
  // FORMAT.md §5/§7.2: named categories in file order, incl. empty ones —
  // see renderList()'s merge of this against `entries`.
  state.categories = Array.isArray(data.categories) ? data.categories : []
  state.exists = data.exists !== false
  // FORMAT.md §7.2 file panel: the RESOLVED absolute path, distinct from
  // the (possibly relative) `file` WIDGET value above.
  state.resolvedFile = typeof data.file === 'string' ? data.file : null
  updateFilePanelPath(state)
  // The share toggle's whole condition is "is THIS path reachable remotely",
  // so it has to be re-evaluated whenever the resolved path changes.
  updateShareToggle(state)
  setStatus(state, baselineStatus(state, data.problems))

  // Restore the selection from the entry widget's (possibly multi-line)
  // value (§6.1: one name per line, selection order; §7.2: "missing names
  // silently drop out of the selection, first surviving = active"). This
  // only updates in-memory rendering state — it deliberately does NOT
  // rewrite entryWidget.value, mirroring this file's original single-select
  // behavior (the old clearEditor() never touched the widget on a reload
  // mismatch; only an explicit user action like delete did, via its own
  // widget write). A name merely absent from THIS load stays in the
  // serialized value untouched, so a transient race can't silently truncate
  // a workflow's stored selection — the next real selection change (which
  // only ever adds names backed by a rendered row) is what actually drops
  // it from serialization.
  const survivors = restoreSelectionFromWidget(state)
  state.selection = survivors
  state.activeName = survivors.length ? survivors[0] : null
  // Category mode survives a DATA reload the same way entry selection does
  // (above, from the `entry` widget) — but a tab-switch REBUILD throws
  // `state` itself away, so unlike entry selection there is nothing left
  // in memory to fall back on. Adopt the remembered name from the `Open
  // category` node property FIRST (PROP_OPEN_CATEGORY's own doc) — but
  // ONLY when nothing is already active, so an in-session navigation (the
  // user already clicked a DIFFERENT category before this reload ran) is
  // never clobbered by whatever was last saved. Then the validity check
  // this always had runs unconditionally on WHICHEVER value `state.
  // activeCategory` now holds: kept only if the category is still there,
  // dropped (and the now-stale property written back to `''`, so a
  // corrupt/deleted-category value doesn't linger forever) silently
  // otherwise (FORMAT.md §7.2 amendment) — independent of the entry
  // selection restore, per the file header's "never touches `selection`"
  // rule for category mode. loadActiveEditor() below already handles
  // category mode, so restoring the pointer here is sufficient — the
  // draft (if any) repaints through the existing loadCategoryDescription()
  // -> populateEditor() -> draftTextFor() path.
  if (state.activeCategory == null) {
    state.activeCategory = parseOpenCategoryProperty(state.node.properties?.[PROP_OPEN_CATEGORY])
  }
  if (state.activeCategory != null && !state.categories.includes(state.activeCategory)) {
    state.activeCategory = null
    syncOpenCategoryProperty(state)
  }
  renderList(state)
  updateDeleteButtonEnabled(state)
  updateSelectionHint(state)
  updateModeHint(state)
  await loadActiveEditor(state)
}

/**
 * The editor half of a load: the active item's live text (entry body or
 * category description), or the pinned view. Split from
 * applyNotebookPayload() so the `unchanged` short-circuit can re-run just
 * this part for an explicit reload on an already-showing file.
 */
async function loadActiveEditor(state) {
  // Provenance M3: while pinned the editor shows the PINNED entry's old
  // text (read-only), never the live entry -- the live entries above were
  // still loaded, because that is what the drift comparison (badge + row
  // markers, both repainted here) reads against. Selection state stays as
  // restored, untouched, for the moment the pin is cleared.
  if (isPinned(state)) {
    renderPinBar(state)
    paintPinnedEditor(state)
    return
  }

  if (state.activeCategory != null) {
    const result = await loadCategoryDescription(state, state.activeCategory)
    if (result === 'failed') resetEditorDom(state)
  } else if (state.activeName) {
    const result = await loadEntryText(state, state.activeName)
    if (result === 'failed') resetEditorDom(state)
  } else {
    resetEditorDom(state)
  }
}

/**
 * Shared timeout-recovery branch for every notebook write (finding 6,
 * 2026-08-26 responsiveness round). A write that hits its `WRITE_TIMEOUT_MS`
 * abort (api.js's `postJson(..., { timeoutMs })`) may still have LANDED on
 * disk -- the GIL-busy server or the slow NAS mount can outlive the client
 * abort without the write itself having failed -- so recovery RELOADS
 * instead of assuming failure, unlike every other error branch in this
 * file. Callers check `error?.timeout` FIRST, ahead of the usual `409`
 * branch (an aborted fetch never carries a `.status`), and skip their own
 * error handling on a `true` return; `state.busy`/button state is already
 * cleared by the caller before this runs, same convention every catch block
 * here already follows.
 * @returns {Promise<boolean>} whether *error* was a timeout (already handled)
 */
async function recoverFromWriteTimeout(state, error) {
  if (!error?.timeout) return false
  toast('error', 'Request timed out', 'Timed out — reloading to check what landed.')
  setStatus(state, 'Timed out — reloading to check what landed…')
  try {
    await reloadNow(state)
  } catch (reloadError) {
    api.warn('notebook reload after write timeout failed', reloadError)
  }
  return true
}

/**
 * Parses the entry widget's raw value into candidate selected names: split
 * on any line ending, trim, drop blanks. A single bare name (no newline) is
 * the pre-multiselect degenerate case (§6.1) and parses to a one-element
 * array, so old workflows restore unchanged.
 * @param {string} rawValue
 * @returns {string[]}
 */
function parseSelectionValue(rawValue) {
  if (!rawValue) return []
  return String(rawValue)
    .split(/\r\n|\r|\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
}

/**
 * @returns {string[]} the entry widget's requested names, deduped, filtered
 * to those that exist in `state.entries` right now, in their original order.
 */
function restoreSelectionFromWidget(state) {
  const requested = parseSelectionValue(state.entryWidget.value)
  const seen = new Set()
  const survivors = []
  for (const name of requested) {
    if (seen.has(name)) continue
    if (!state.entries.some((entry) => entry.name === name)) continue
    seen.add(name)
    survivors.push(name)
  }
  return survivors
}

/**
 * Universal State Controller Apply fix (2026-08-29, owner report:
 * "applying any of the sets won't change anything") -- api.js's
 * `announceWidgetsChangedExternally()` calls this (via
 * `node.__epsNotebookReload`, below) after a programmatic
 * `widget.value = x; widget.callback?.()` write to this node's `file` or
 * `entry` widget (`nodes_notebook.py`'s `EPS_STATE_WIDGETS` declares
 * both). That write updates the NODE correctly, but this panel's own
 * rendered DOM (the entry list, the editor pane) only ever repaints from
 * its own gestures or reload cycles -- exactly the bug reproduced on the
 * rig (a Notebook whose `entry` widget flipped from "Film Grain" to
 * "Detailer" kept showing "Film Grain" selected).
 *
 * `file` changed -> the entries for a DIFFERENT file were never fetched by
 * this panel; fall through to `reloadNow()`, the SAME real reload
 * `onFileWidgetChanged()`/`wireConfigureReload()` already use for exactly
 * this case (one GET). `file` unchanged, only `entry` changed -> a pure
 * re-derivation of the selection against what is ALREADY loaded --
 * `restoreSelectionFromWidget()` plus the identical render calls
 * `applyNotebookPayload()` runs after every load (`renderList`,
 * `updateDeleteButtonEnabled`, `updateSelectionHint`, `updateModeHint`,
 * `loadActiveEditor`). `loadActiveEditor()` -> `loadEntryText()`'s own
 * cache-first path (`hasCachedEntryText`) means this branch never issues a
 * network request for an entry the CURRENT file already loaded, which is
 * the only case an Apply from the SAME workflow can produce -- satisfying
 * "never fires a network request when the panel can repaint from cached
 * data." Idempotent either way: `populateEditor()`'s own mid-edit guard
 * (`document.activeElement` check) already refuses to clobber unsaved
 * typing, and `renderList()` already preserves scroll/focus/collapse
 * state on every one of its many other call sites, so calling it again
 * here needs no special-casing.
 */
function resyncAfterExternalWrite(state) {
  const restored = state.fileWidget?.value ?? ''
  if (restored !== state.file || state.loadError) {
    reloadNow(state).catch((error) =>
      api.warn('notebook reload after external widget change failed', error)
    )
    return
  }
  const survivors = restoreSelectionFromWidget(state)
  state.selection = survivors
  state.activeName = survivors.length ? survivors[0] : null
  renderList(state)
  updateDeleteButtonEnabled(state)
  updateSelectionHint(state)
  updateModeHint(state)
  loadActiveEditor(state).catch((error) =>
    api.warn('notebook external resync editor load failed', error)
  )
}

// One shared subscription to api.js's `announceWidgetsChangedExternally()`
// serves every attached Notebook node -- installed once, idempotently,
// from the first `attachNotebookWidget()` call (this file's
// `comboRefreshWrapped`-style module-flag idiom, see sets.js). Routes by
// NODE IDENTITY (the announce entry's own `.node` reference against the
// `__epsNotebookReload` seam stamped on each attached node -- picker.js's
// `__epsLpReload` precedent, already used by controller.js's Push State),
// never by a class-name string, so this file never needs to know what
// Universal State Controller (or any future caller of the same shared
// event) calls this node's class.
let externalWriteSubscribed = false

function installExternalWriteSubscription() {
  if (externalWriteSubscribed) return
  externalWriteSubscribed = true
  api.subscribeWidgetsChangedExternally((entries) => {
    for (const entry of entries || []) entry?.node?.__epsNotebookReload?.()
  })
}

function baselineStatus(state, problems) {
  const parts = []
  if (!state.exists) parts.push('File does not exist yet — it will be created on first save.')
  const list = Array.isArray(problems) ? problems : []
  if (list.length) {
    parts.push(`${list.length} problem${list.length === 1 ? '' : 's'}: ${list.join(' · ')}`)
  }
  return parts.join(' ')
}

// ---------------------------------------------------------------------------
// Selection model (FORMAT.md §6.1/§7.2)
//
// `state.selection` is the ordered list of selected entry names — exactly
// what gets newline-joined into the `entry` widget. `state.activeName` is
// the most-recently-clicked selected entry; it alone drives the editor pane,
// dirty tracking, Save, Delete, and conflict handling — the single-select
// behavior this file always had, just decoupled from "what's highlighted."
//
// Two layers:
//  - setSelection() is a dumb setter: replace selection+active, sync the
//    widget, re-render. It never touches the editor pane.
//  - chooseSelection() is the interactive entry point (click/ctrl+click/
//    shift+click): applies immediately for a responsive list, then loads
//    the new active entry's text; a failed load rolls the WHOLE selection
//    back to what it was before the click — the click "didn't happen" —
//    matching this file's original single-select failure behavior.
// Callers that already know the target is valid and don't want rollback
// semantics (delete's post-delete reassignment, move, reload-restore) call
// setSelection() directly and load the active entry themselves.
// ---------------------------------------------------------------------------

function isSelected(state, name) {
  return state.selection.includes(name)
}

function lastOrNull(items) {
  return items.length ? items[items.length - 1] : null
}

/** Resets the editor DOM/dirty state only — no rendering, no widget sync.
 * Shared by every path that ends up with no (or no-longer-loadable) active
 * entry. */
function resetEditorDom(state) {
  state.textarea.value = ''
  state.lastSavedText = ''
  state.nameFieldEl.value = ''
  state.lastSavedName = ''
  state.baseMtime = null
  state.textarea.disabled = true
  state.nameFieldEl.disabled = true
  clearPinnedEditorLook(state) // M3: the pinned read-only look never outlives the pin
  setDirty(state, false)
}

/** Initial, pre-load editor state (buildUi() only — nothing has been
 * fetched yet, so there is nothing to preserve in the entry widget). */
function clearEditor(state) {
  state.selection = []
  state.activeName = null
  state.activeCategory = null
  // Runs before registerOpenCategoryProperty() (buildUi() -> clearEditor()
  // happens first in attach()) -- harmless: this defensively seeds
  // node.properties the same way syncOpenCategoryProperty() always does,
  // and the value written ('') matches what addProperty()'s own default
  // will be a moment later.
  syncOpenCategoryProperty(state)
  resetEditorDom(state)
  renderList(state)
  updateDeleteButtonEnabled(state)
  updateSelectionHint(state)
  updateModeHint(state)
}

/**
 * Writes the full multi-select `entry` STRING widget value (§6.1: one name
 * per line, in selection order) through the widget's real setter + callback
 * ("Selection writes the entry STRING widget so serialization needs no
 * custom code") and nudges the canvas to redraw so the change is visible
 * immediately. Mirrors the pattern ComfyUI's own `scripts/widgets.ts`
 * (`applyWidgetControl`) uses to drive one widget's value from other logic:
 * `targetWidget.value = next; targetWidget.callback?.(next)`.
 */
function syncEntryWidget(state) {
  const widget = state.entryWidget
  const next = state.selection.join('\n')
  if (widget.value === next) return
  widget.value = next
  try {
    widget.callback?.(next)
  } catch (error) {
    api.warn('entry widget callback threw', error)
  }
  state.node.graph?.setDirtyCanvas(true, true)
}

/**
 * *names* reordered to match the entry LIST (v0.85.0, owner report
 * 2026-08-28: "sometimes the order of images doesn't match the order of
 * list"). The panel's own gestures disagreed about what "selection order"
 * meant — ctrl+click APPENDS, shift+click takes a list-ordered slice, and
 * toggling an entry off then on moved it to the end — so the `entry`
 * widget (and every §6.10 run token `t{N}` derived from it) depended on
 * click history rather than on anything visible. Canonical order is what
 * the list shows.
 *
 * Stable: names absent from *entries* (a stale selection mid-reload) keep
 * their relative order at the END rather than being dropped — this is a
 * display-order helper, never a filter. `read_entry` sorts the same way
 * server-side, which is what fixes workflows saved before v0.85.0.
 */
export function orderNamesByList(names, entries) {
  if (!Array.isArray(names) || names.length < 2) return Array.isArray(names) ? names : []
  const rank = new Map()
  if (Array.isArray(entries)) {
    entries.forEach((entry, index) => {
      const name = entry && entry.name
      if (typeof name === 'string' && !rank.has(name)) rank.set(name, index)
    })
  }
  const fallback = rank.size
  return names
    .map((name, index) => ({ name, index, rank: rank.has(name) ? rank.get(name) : fallback }))
    .sort((a, b) => a.rank - b.rank || a.index - b.index)
    .map((item) => item.name)
}

/** Dumb setter: replace the selection + active entry, sync the `entry`
 * widget, and re-render. Does not touch the editor pane — callers that
 * change the ACTIVE entry are responsible for loading (or clearing) it.
 *
 * Unsaved-edit drafts (v0.86.0): flushes any pending debounced draft-sync
 * for the OUTGOING active entry BEFORE reassigning `state.activeName` --
 * commitDraftForActiveEntry() reads `state.activeName`/the textarea, so it
 * MUST run while both still describe the entry being left, not the one
 * about to become active (a flush from inside populateEditor(), which
 * runs later, would already be too late here since this function is what
 * moves `state.activeName` in the first place). Guarded on an actual
 * identity change -- ctrl-click toggling a DIFFERENT, non-active row
 * leaves `active` unchanged and must never touch the entry that's
 * currently open (same rule chooseSelection()'s own early-return
 * documents). Prunes any draft that falls out of the NEW selection right
 * after -- the one choke point every selection change already goes
 * through. */
function setSelection(state, names, active) {
  if (active !== state.activeName) flushDraftSync(state)
  // v0.85.0: store in LIST order, never click order (orderNamesByList).
  state.selection = orderNamesByList(names, state.entries)
  state.activeName = active
  syncEntryWidget(state)
  pruneDraftsToSelection(state)
  renderList(state)
  updateDeleteButtonEnabled(state)
  updateSelectionHint(state)
}

function fetchEntry(state, name) {
  return api.getJson('/lora_library/notebook/entry', { file: state.file, name })
}

function fetchCategory(state, name) {
  return api.getJson('/lora_library/notebook/category', { file: state.file, name })
}

/** Shared by entry mode and category mode (FORMAT.md §7.2 amendment): both
 * are "one editable text blob + an mtime for the §3.5 conflict check", so
 * one function populates the shared textarea/dirty/baseMtime state for
 * either — callers just pass the right pair. `name` (the entry or category
 * name this text belongs to) also seeds the name field (FORMAT.md §7.2
 * rename-via-header amendment) — omitted only by callers that manage the
 * name field themselves right after (confirmNewEntry()'s inline path).
 *
 * **A field the user is MID-EDIT in is never overwritten** (2026-07-30,
 * owner's Mac report, reproduced on the rig with 600ms of injected fetch
 * latency): this function used to rewrite both fields and hard-reset dirty
 * unconditionally, which is invisible on loopback (the click→populate window
 * is ~1ms — nobody can type inside it) but on a LAN round-trip to another
 * machine the window is wide open: click a row, start typing the new name,
 * and the LATE load lands and wipes the typing AND disables Save — his
 * exact "changing the title text does not enable the save button". The
 * focus check is what keeps ordinary selection changes unaffected: clicking
 * a row moves focus to the row itself, so a load that lands after a normal
 * click always sees the field unfocused and populates as before. Baselines
 * (`lastSaved*`, `baseMtime`) always update — they describe the DISK — and
 * `refreshDirty` (not a blanket `setDirty(false)`) then re-derives dirty, so
 * preserved mid-edit typing correctly re-enables Save against the fresh
 * baseline.
 *
 * Unsaved-edit drafts (v0.86.0): flushes any pending debounced draft-sync
 * FIRST, before anything below reassigns `lastSavedText`/the textarea --
 * this is the category-mode transition's flush point (entering/leaving
 * category mode never touches `state.activeName`, so setSelection()'s own
 * flush never fires for it; reading `state.activeName` here is still
 * correct since it hasn't moved). Redundant-but-harmless for an ordinary
 * entry-to-entry switch, where setSelection() already flushed against the
 * OUTGOING name before this function was ever called for the incoming
 * one -- flushDraftSync() no-ops with nothing pending. */
function populateEditor(state, text, mtime, name) {
  flushDraftSync(state)
  const nameMidEdit =
    document.activeElement === state.nameFieldEl &&
    currentNameFieldValue(state) !== state.lastSavedName
  const textMidEdit =
    document.activeElement === state.textarea &&
    state.textarea.value !== state.lastSavedText
  // v0.87.3 (owner report 2026-08-28: "tabbing to another workflow and
  // tabbing back erases any changes"). A tab switch tears the panel down
  // and rebuilds it, so this ran with the FILE's text and painted it over
  // an unsaved edit -- and because refreshDirty() below then saw
  // textarea === lastSavedText, it CLEARED the draft too. The edit was not
  // just hidden, it was destroyed, by a repaint the user never asked for.
  //
  // The draft is the text to SHOW; the file text stays `lastSavedText`,
  // the baseline every dirty/Save comparison is against. Restoring the two
  // separately is what makes an audition survive a repaint while Save
  // still knows exactly what changed. Only a real user action (selecting
  // another entry, saving, or typing back to the saved text) clears a
  // draft -- never a re-render.
  const draft = draftTextFor(state, name)
  const shown = draft !== null ? draft : text ?? ''
  if (!textMidEdit) state.textarea.value = shown
  state.lastSavedText = text ?? ''
  state.baseMtime = typeof mtime === 'number' ? mtime : null
  state.textarea.disabled = false
  clearPinnedEditorLook(state) // M3: a live entry is editable again
  if (name !== undefined) {
    if (!nameMidEdit) state.nameFieldEl.value = name ?? ''
    state.lastSavedName = (name ?? '').trim()
    state.nameFieldEl.disabled = false
  }
  refreshDirty(state)
  updateDeleteButtonEnabled(state)
  clearConflict(state)
}

/**
 * Fetches `name`'s text and populates the editor. Token-guarded against
 * races with a later reload/select/teardown. Returns `'ok'`, `'failed'`
 * (fetch/parse error — already reported via setStatus), or `'stale'` (a
 * newer load/select superseded this one before it resolved — caller should
 * no-op either way, distinguished from `'failed'` only so a caller COULD
 * tell them apart if it ever needed to).
 * @returns {Promise<'ok'|'failed'|'stale'>}
 */
async function loadEntryText(state, name) {
  // Cache-served fast path (finding 1, 2026-08-26 responsiveness round,
  // audited HIGH: "entry click round trip is redundant AND paints
  // inconsistently"). `state.entryTextByName`/`state.paintedMtime` already
  // hold this exact name's text/mtime from the include_text=1 list load
  // that populated `state.entries` -- a GET here was pure latency, and
  // worse, the row highlight painted at once (setSelection, synchronous)
  // while the textarea visibly lagged the round trip. Populating
  // synchronously from the SAME data both fixes the round trip and makes
  // the two paints structurally unable to disagree. The GET below survives
  // ONLY as a fallback for a name genuinely absent from the cache -- should
  // never happen (every name in `state.entries` got a `text` field), but a
  // stale/hand-built state deserves a real load over a silent no-op.
  if (hasCachedEntryText(state, name)) {
    ++state.selectToken // invalidate any older in-flight fetch, same as below
    populateEditor(state, state.entryTextByName[name], state.paintedMtime, name)
    return 'ok'
  }
  const loadToken = state.loadToken
  const selectToken = ++state.selectToken
  let data
  try {
    data = await fetchEntry(state, name)
  } catch (error) {
    if (loadToken !== state.loadToken || selectToken !== state.selectToken) return 'stale'
    api.warn('failed to load notebook entry', error)
    setStatus(state, `Could not load "${name}": ${error.message}`)
    return 'failed'
  }
  if (loadToken !== state.loadToken || selectToken !== state.selectToken) return 'stale'
  populateEditor(state, data.text, data.mtime, name)
  return 'ok'
}

/** Category-mode sibling of loadEntryText() above — same cache-first fast
 * path (finding 5, 2026-08-26 responsiveness round: `category_descriptions`
 * now rides the same include_text=1 payload — see applyNotebookPayload()),
 * same token-guard/return contract, falling back to the §5 category route
 * only for a name genuinely absent from the cache. */
async function loadCategoryDescription(state, name) {
  if (hasCachedCategoryDescription(state, name)) {
    ++state.selectToken
    populateEditor(state, state.categoryDescriptionByName[name], state.paintedMtime, name)
    return 'ok'
  }
  const loadToken = state.loadToken
  const selectToken = ++state.selectToken
  let data
  try {
    data = await fetchCategory(state, name)
  } catch (error) {
    if (loadToken !== state.loadToken || selectToken !== state.selectToken) return 'stale'
    api.warn('failed to load category description', error)
    setStatus(state, `Could not load category "${name}": ${error.message}`)
    return 'failed'
  }
  if (loadToken !== state.loadToken || selectToken !== state.selectToken) return 'stale'
  populateEditor(state, data.description, data.mtime, name)
  return 'ok'
}

/**
 * The interactive entry point: apply a new selection immediately, then load
 * the new active entry (only when the active identity actually changed —
 * clicking around a multi-selection must never clobber unsaved edits in the
 * entry that's already open). A failed load rolls back to the selection
 * that was in effect before this call.
 *
 * Also the ONE place that exits category mode on behalf of an entry click
 * (FORMAT.md §7.2 amendment, file header's "Categories" paragraph): clearing
 * `activeCategory` here — never touching `selection`/`activeName` to do it —
 * is what makes "clicking any entry exits category mode" true regardless of
 * which of selectSingle/toggleEntry/selectRange dispatched here. Because
 * category mode is independent of `activeName`, exiting it can require a
 * reload even when `active === previousActive` (the entry that was already
 * "active" underneath category mode) — `wasInCategoryMode` covers exactly
 * that one case.
 * @param {string[]} names
 * @param {string|null} active
 */
async function chooseSelection(state, names, active) {
  cancelDeleteConfirm(state)
  closeNewEntryRow(state)

  const wasInCategoryMode = state.activeCategory != null
  state.activeCategory = null
  syncOpenCategoryProperty(state)

  const previousSelection = state.selection
  const previousActive = state.activeName
  setSelection(state, names, active)
  updateModeHint(state)
  if (active === previousActive && !wasInCategoryMode) return

  if (active == null) {
    resetEditorDom(state)
    updateDeleteButtonEnabled(state)
    return
  }

  const result = await loadEntryText(state, active)
  if (result === 'failed') {
    setSelection(state, previousSelection, previousActive)
  }
}

/** Plain click: collapse to a single selection. */
function selectSingle(state, name) {
  chooseSelection(state, [name], name).catch((error) => api.warn('select entry failed', error))
}

/** ctrl/cmd+click: toggle membership. Toggling the active entry off hands
 * "active" to the last-remaining selected entry (or clears it). Toggling
 * any entry on makes it active — it's the one just clicked. */
function toggleEntry(state, name) {
  if (isSelected(state, name)) {
    const nextSelection = state.selection.filter((n) => n !== name)
    const nextActive = state.activeName === name ? lastOrNull(nextSelection) : state.activeName
    chooseSelection(state, nextSelection, nextActive).catch((error) => api.warn('select entry failed', error))
  } else {
    chooseSelection(state, [...state.selection, name], name).catch((error) => api.warn('select entry failed', error))
  }
}

/** shift+click: replace the selection with the visible range between the
 * current active entry (the anchor) and the clicked one, inclusive, in
 * top-to-bottom list order — order = selection order (§6.1), so a shift-
 * range runs prompts top-to-bottom. No anchor yet (nothing active) falls
 * back to a plain single-select. */
function selectRange(state, name) {
  const anchorName = state.activeName
  if (!anchorName) {
    selectSingle(state, name)
    return
  }
  const anchorIndex = state.entries.findIndex((entry) => entry.name === anchorName)
  const clickIndex = state.entries.findIndex((entry) => entry.name === name)
  if (anchorIndex === -1 || clickIndex === -1) {
    selectSingle(state, name)
    return
  }
  const lo = Math.min(anchorIndex, clickIndex)
  const hi = Math.max(anchorIndex, clickIndex)
  const names = state.entries.slice(lo, hi + 1).map((entry) => entry.name)
  chooseSelection(state, names, name).catch((error) => api.warn('select entry failed', error))
}

/** Dispatches a resolved (non-drag) pointer gesture to the right selection
 * mode, mirroring standard list-box modifier conventions. */
function handleEntryClick(state, name, modifiers) {
  if (isPinned(state)) return // M3: the live selection (and the `entry` widget) is frozen while pinned -- keyboard flow included
  if (modifiers.shiftKey) selectRange(state, name)
  else if (modifiers.toggleKey) toggleEntry(state, name)
  else selectSingle(state, name)
}

/** Muted status-area hint (owner ask): visible only for 2+ selected, since
 * that's when OUTPUT_IS_LIST fan-out (§6.1) actually changes queue behavior.
 * Lives in its own element (not statusTextEl) so Saving…/Deleted…/conflict
 * messages never clobber it and vice versa.
 *
 * Unsaved-edit drafts (v0.86.0, owner ask 2026-08-28): also where auditioning
 * an unsaved edit is surfaced ("must not be invisible") -- a factual count,
 * appended after the fan-out hint rather than replacing it, since both can
 * be true at once (a multi-select run with one of its prompts edited but
 * unsaved). Never shown while pinned: a pin ignores drafts outright (M3
 * wins), so a leftover draft from before the pin arrived would be actively
 * misleading there. */
function updateSelectionHint(state) {
  if (!state.statusHintEl) return
  // M3: while pinned the node outputs the PINNED entries, so the fan-out
  // hint counts those, not the (ignored) live selection.
  if (isPinned(state)) {
    const pinnedCount = state.pinned.entries.length
    state.statusHintEl.textContent =
      pinnedCount >= 2 ? `${pinnedCount} pinned prompts — queue runs once per prompt.` : ''
    return
  }
  const parts = []
  const count = state.selection.length
  if (count >= 2) parts.push(`${count} prompts selected — queue runs once per prompt.`)
  // Intersected with the CURRENT selection defensively (pruneDraftsToSelection
  // already keeps `state.drafts` a subset of it), so a stale entry can never
  // inflate this count even for one repaint.
  const draftCount = Object.keys(state.drafts).filter((n) => state.selection.includes(n)).length
  if (draftCount >= 1) {
    parts.push(
      draftCount === 1
        ? '1 unsaved edit — runs as edited.'
        : `${draftCount} unsaved edits — run as edited.`
    )
  }
  state.statusHintEl.textContent = parts.join(' ')
}

// ---------------------------------------------------------------------------
// Categories in the UI (FORMAT.md §7.2 amendment) — see the file header's
// "Categories" paragraph for the overall design. This is the category-mode
// counterpart of the "Selection model" section above: selectCategory() is
// its chooseSelection() — the interactive "click a header" entry point
// (confirmNewCategory() also sets `state.activeCategory` directly, for the
// "just created it" case, same relationship confirmNewEntry() has to
// setSelection()). Neither ever calls setSelection()/syncEntryWidget(),
// which is what keeps category mode from touching the entry selection or
// the `entry` widget.
// ---------------------------------------------------------------------------

/**
 * Clicking a category header: enters "category mode" for *name*, loading its
 * §3.1 description into the shared editor pane. Deliberately mirrors
 * chooseSelection()'s shape (immediate UI update, then an async load, then a
 * rollback-on-failure so a failed click reads as "didn't happen") but never
 * touches `state.selection`/`state.activeName`/the `entry` widget — see the
 * file header. Re-clicking the already-active category is a no-op (nothing
 * changed, so nothing to reload).
 */
async function selectCategory(state, name) {
  if (state.busy) return
  cancelDeleteConfirm(state)
  closeNewEntryRow(state)
  if (state.activeCategory === name) return

  const previousCategory = state.activeCategory
  state.activeCategory = name
  syncOpenCategoryProperty(state)
  renderList(state)
  updateDeleteButtonEnabled(state)
  updateModeHint(state)

  const result = await loadCategoryDescription(state, name)
  if (result === 'failed') {
    // Roll back exactly like chooseSelection() does on a failed entry load —
    // the editor's own content was never touched by the failed fetch, so
    // restoring just the pointer is enough to undo the click.
    state.activeCategory = previousCategory
    syncOpenCategoryProperty(state)
    renderList(state)
    updateDeleteButtonEnabled(state)
    updateModeHint(state)
  }
}

/** FORMAT.md §7.2 amendment: "the editor is contextual … a visible mode
 * hint says which" — updates `state.modeHintEl`, directly above the
 * textarea. Category mode wins when both are technically set (selection
 * survives entering category mode — see the file header), since it's what
 * the editor is actually showing. */
function updateModeHint(state) {
  if (!state.modeHintEl) return
  if (isPinned(state)) {
    // M3: the editor shows the OLD (pinned) text, read-only.
    const name = pinnedActiveEntry(state)?.name
    state.modeHintEl.textContent = name
      ? `Pinned entry: ${name} — read-only, captured from the image. Unpin to edit the live notebook.`
      : 'Pinned — read-only, captured from the image. Unpin to edit the live notebook.'
    return
  }
  if (state.activeCategory != null) {
    state.modeHintEl.textContent = `Editing category description: ${state.activeCategory}`
  } else if (state.activeName) {
    state.modeHintEl.textContent = `Editing entry: ${state.activeName}`
  } else {
    state.modeHintEl.textContent = ''
  }
}

// ---------------------------------------------------------------------------
// Entry list rendering
// ---------------------------------------------------------------------------

/**
 * Renders headers from `state.categories` (FORMAT.md §5's file-order list,
 * NOT derived from `entries`) merged with `state.entries` by a single
 * forward walk over both — both arrays are already in file order from the
 * same parse, so this is a two-pointer merge, not a group-by-name: entries
 * are appended while they keep matching the CURRENT category, and a header
 * with nothing following it (an empty category) still renders. This is what
 * lets an empty category show at all, and keeps a hand-edited file's
 * repeated category name as two separate headers rather than one merged
 * group (see list_categories()'s doc comment on the Python side).
 */
/**
 * §7.2 search predicate (owner ask 2026-08-08: "matches search words with
 * words either in the title or body"): case-insensitive, every
 * whitespace-separated query word must appear somewhere in the entry's
 * NAME or BODY. Multi-word queries therefore narrow (AND), matching how
 * the Checkpoint Switcher's filter box already behaves.
 *
 * v0.68.1 (perf round): split into its three pure parts so the expensive
 * one runs once per LOAD, not once per entry per keystroke --
 * searchHaystack() lowercases one entry's `name\nbody`, buildSearchCorpus()
 * does that for every entry when the include_text payload lands,
 * searchWords() splits the query once per render, and entryMatchesSearch()
 * is the AND over a prebuilt haystack. Semantics are byte-identical to the
 * old per-call entryMatchesSearch(name, text, query).
 */
function entryMatchesSearch(haystack, words) {
  return words.every((word) => haystack.includes(word))
}

/** One entry's lowercase haystack: `name\nbody`. */
function searchHaystack(name, text) {
  return `${name}\n${text || ''}`.toLowerCase()
}

/** The query's lowercase words (AND across them); `[]` matches everything. */
function searchWords(query) {
  return query
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
}

/** Rebuilt by reloadNow() right after `entryTextByName` -- the two always
 * describe the same load. Keyed by name; a name the map doesn't know (an
 * entry renamed/created since the load) is computed on the fly from
 * `entryTextByName` in renderList(), exactly what the old matcher saw. */
function buildSearchCorpus(state) {
  state.searchCorpus = new Map(
    state.entries.map((entry) => [entry.name, searchHaystack(entry.name, state.entryTextByName[entry.name])])
  )
}

function renderList(state) {
  // 2026-07-24 (owner report: "the left column should not scroll to the
  // top of the list"): `replaceChildren()` resets the list's scrollTop to
  // 0, and renderList() runs on every selection click, save, poll refresh,
  // drag-reorder, collapse toggle, ... -- so any interaction anywhere in a
  // long list yanked the view back to the top. Capture the scroll position
  // before the rebuild and restore it after (the browser clamps it if the
  // list shrank). Restoring is skipped only when there's nothing to scroll
  // (the empty-state early return below).
  const previousScrollTop = state.listEl.scrollTop
  state.listEl.replaceChildren()
  state.dragRows = []

  // Provenance M3: while pinned the list shows the PINNED entries (what the
  // node will actually output), read-only -- no live rows, no headers, no
  // drag sources (dragRows stays empty, so drag-reorder is structurally
  // inert), each row marked against the live library. See renderPinnedList.
  if (isPinned(state)) {
    renderPinnedList(state)
    state.listEl.scrollTop = previousScrollTop
    return
  }

  // §7.2 search: with a query active, render a FILTERED VIEW -- matching
  // entries only, categories that retain at least one match, collapse
  // state ignored (a match hidden inside a collapsed category would read
  // as "search is broken"). Rows are NOT pushed to dragRows while
  // filtering (see the pushes below), so drag-reorder is inert -- dropping
  // "after" a row with unseen entries between them would reorder the file
  // in ways the view can't show. Clicks, selection, and double-click
  // rename all live on the rows themselves and keep working.
  const searchQuery = (state.searchQuery || '').trim()
  const filtering = searchQuery.length > 0
  let categories = Array.isArray(state.categories) ? state.categories : []
  let entries = state.entries
  if (filtering) {
    const words = searchWords(searchQuery) // once per render (v0.68.1)
    entries = entries.filter((entry) =>
      entryMatchesSearch(
        state.searchCorpus.get(entry.name) ?? searchHaystack(entry.name, state.entryTextByName[entry.name]),
        words
      )
    )
    const categoriesWithMatches = new Set(entries.map((entry) => entry.category || ''))
    categories = categories.filter((category) => categoriesWithMatches.has(category))
    if (!entries.length) {
      state.listEl.append(
        el('div', { className: 'llnb-empty', text: `No prompts match "${searchQuery}".` })
      )
      return
    }
  }

  if (!entries.length && !categories.length) {
    state.listEl.append(
      el('div', {
        className: 'llnb-empty',
        text: state.loadError
          ? 'Could not load this notebook -- the saved file and selection are untouched. Fix the connection and hit Retry (status bar below).'
          : state.exists
            ? 'No entries yet.'
            : 'File not found yet.'
      })
    )
    return
  }

  let entryIndex = 0
  const appendEntry = (entry) => {
    const row = buildEntryRow(state, entry)
    state.listEl.append(row)
    if (!filtering) {
      state.dragRows.push({ el: row, kind: 'entry', name: entry.name, category: entry.category || '' })
    }
    entryIndex += 1
  }

  // The leading, un-headed "" region (FORMAT.md §3.1: entries before the
  // first H1) never gets a category row of its own, so it's never
  // collapsible either.
  while (entryIndex < entries.length && (entries[entryIndex].category || '') === '') {
    appendEntry(entries[entryIndex])
  }

  for (const category of categories) {
    const headerEl = buildCategoryHeaderRow(state, category)
    state.listEl.append(headerEl)
    if (!filtering) state.dragRows.push({ el: headerEl, kind: 'header', category })

    // Single-tap collapse (FORMAT.md §7.2 amendment): a collapsed
    // category's entries are skipped entirely — not rendered, not added to
    // `dragRows` — so they're visually gone AND inert (no click, no drag
    // source) until expanded again; the header row itself still renders
    // and stays a valid drop target either way.
    const collapsed = !filtering && state.collapsedCategories.has(category)
    while (entryIndex < entries.length && (entries[entryIndex].category || '') === category) {
      if (collapsed) entryIndex += 1
      else appendEntry(entries[entryIndex])
    }
  }

  // Defensive fallback: an entry reporting a category `categories` didn't
  // list (shouldn't happen — both come from the same parse) still renders,
  // just without a header of its own, rather than silently vanishing.
  while (entryIndex < entries.length) {
    appendEntry(entries[entryIndex])
  }

  // Rename in place (owner ask 2026-07-29): an open editor has to survive the
  // rebuild above. This is what makes a poll refresh / late fetch / collapse
  // toggle invisible to someone mid-rename, and it is the specific failure
  // that sank the v0.10.0 inline rename — see onEntryDoubleClick's history
  // note. Last, so the editor mounts onto the final rows.
  restoreInlineRename(state)

  // See the capture at the top of this function (2026-07-24 owner fix).
  state.listEl.scrollTop = previousScrollTop
}

/** The normal (non-renaming) row: click/ctrl/shift-click selection,
 * pointer-drag reorder (onEntryPointerDown below), double-click to rename
 * (onEntryDoubleClick, in the "Rename" section further down). */
function buildEntryRow(state, entry) {
  const selected = isSelected(state, entry.name)
  const active = entry.name === state.activeName
  const classes = ['llnb-entry']
  if (selected) classes.push('llnb-entry-selected')
  if (active) classes.push('llnb-entry-active')

  const row = el('div', {
    className: classes.join(' '),
    text: entry.name,
    attrs: { tabindex: '0', title: entry.name }
  })
  // Rename in place (owner ask 2026-07-29): the name this row stands for, so
  // inlineRenameRow() can find it again after a rebuild without depending on
  // the row's text (which is the input's, not the name's, while editing).
  row.__llnbName = entry.name
  row.addEventListener('pointerdown', (event) => onEntryPointerDown(state, event, entry.name))
  row.addEventListener('dblclick', (event) => onEntryDoubleClick(state, event, entry.name))
  row.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return
    event.preventDefault()
    handleEntryClick(state, entry.name, { shiftKey: event.shiftKey, toggleKey: event.ctrlKey || event.metaKey })
  })
  return row
}

/** Number of entries currently under `category` (§3.4) — the source for the
 * collapsed header's `(N)` badge below and for
 * describePendingCategoryDelete()'s own count further down. Client-side
 * only, off the already-loaded `state.entries` — no fetch (owner report
 * 2026-08-25: "consistent with the state controller", which counts its own
 * collapsed groups the same way, off its already-loaded layout). */
function categoryEntryCount(state, category) {
  return state.entries.filter((entry) => entry.category === category).length
}

/**
 * The header's inline ✕ (owner report 2026-08-25, UI parity with the Lora
 * State Controller's group headers — see controller.js's
 * `_buildCategoryHeader()`: "▸ name (N)" collapsed, plus an armed two-click
 * ✕ that removes the GROUP only). An armed two-click confirm LOCAL to this
 * one button (`deleteBtn._armed`/`_armTimer`), entirely independent of the
 * bottom Delete button's `state.deleteConfirmActive` — same as the
 * controller's per-button arm state, so arming one header's ✕ never disarms
 * another header's, or the bottom button's. Same DELETE_CONFIRM_MS window
 * the bottom button already uses (also the controller's own
 * `CATEGORY_DELETE_CONFIRM_MS` — both 4000ms).
 *
 * Confirming does not open a second request path: it enters category mode
 * on `category` (the same `state.activeCategory` assignment selectCategory()
 * makes, minus its async description fetch — nothing here needs the editor
 * pane painted first) and calls the EXISTING performDeleteCategory(), which
 * already posts §5's `/delete_category` route, drives the standard 409
 * conflict UI, drops the name from `state.collapsedCategories` +
 * `Collapsed sections`, and reports the merge from the server's own answer
 * (deletedCategoryStatus()) — see the file header's "Delete is CONTEXTUAL"
 * paragraph and performDeleteCategory()'s own doc.
 */
function buildCategoryDeleteButton(state, category) {
  const deleteBtn = el('button', {
    className: 'llnb-category-delete',
    text: '✕',
    attrs: {
      type: 'button',
      title: 'Remove this heading — its entries move to the block above. Click twice.'
    }
  })
  deleteBtn.addEventListener('click', (event) => {
    event.stopPropagation()
    if (isPinned(state) || state.busy) return // M3 / mid-request guard
    if (!deleteBtn._armed) {
      deleteBtn._armed = true
      deleteBtn.classList.add('llnb-category-delete-armed')
      deleteBtn.textContent = 'sure?'
      clearTimeout(deleteBtn._armTimer)
      deleteBtn._armTimer = setTimeout(() => {
        deleteBtn._armed = false
        deleteBtn.classList.remove('llnb-category-delete-armed')
        deleteBtn.textContent = '✕'
      }, DELETE_CONFIRM_MS)
      // Same reasoning as the bottom Delete button's own arm step (see
      // onDeleteClick): the button is too narrow to say what a header
      // delete does to the entries under it, so the status line spells the
      // merge out here, before the second click.
      setStatus(state, describePendingCategoryDelete(state, category))
      return
    }
    clearTimeout(deleteBtn._armTimer)
    state.activeCategory = category
    syncOpenCategoryProperty(state)
    performDeleteCategory(state).catch((error) => api.warn('category delete failed', error))
  })
  return deleteBtn
}

/**
 * A category header row (FORMAT.md §7.2 amendment): a tap toggles collapse
 * AND enters category mode (see the file header's "Single-tap collapse"
 * paragraph); a drag relocates the whole category (see "Drag a category
 * header"). Both share onCategoryPointerDown()'s threshold-disambiguation
 * gesture below, the header's sibling of buildEntryRow()'s pointerdown —
 * headers are now a drag SOURCE as well as a drop TARGET
 * (computeDropTarget()/computeCategoryDropTarget() both read this row's
 * geometry back out of `state.dragRows`).
 */
function buildCategoryHeaderRow(state, category) {
  const classes = ['llnb-category']
  if (state.activeCategory === category) classes.push('llnb-category-active')
  const collapsed = state.collapsedCategories.has(category)

  // Count-when-collapsed + armed ✕ (owner report 2026-08-25: "consistent
  // with the state controller") — see buildCategoryDeleteButton()'s doc.
  const labelEl = el('span', {
    className: 'llnb-category-label',
    text: collapsed
      ? `▸ ${category} (${categoryEntryCount(state, category)})`
      : `▾ ${category}`
  })
  const deleteBtn = buildCategoryDeleteButton(state, category)

  const headerEl = el(
    'div',
    { className: classes.join(' '), attrs: { tabindex: '0', title: category } },
    [labelEl, deleteBtn]
  )
  headerEl.__llnbName = category
  headerEl.addEventListener('pointerdown', (event) => {
    // The ✕ has its own click handler (buildCategoryDeleteButton) — it must
    // never also arm the header's drag/collapse-toggle gesture below,
    // exactly like controller.js's _buildCategoryHeader() target check.
    if (event.target === deleteBtn) return
    onCategoryPointerDown(state, event, category)
  })
  headerEl.addEventListener('dblclick', (event) => {
    // Same reasoning as the pointerdown guard above: a fast double-click
    // that lands on the ✕ must arm/confirm the delete, never open the
    // rename editor underneath it.
    if (event.target === deleteBtn) return
    event.preventDefault()
    event.stopPropagation()
    state.drag?.cleanup?.()
    state.drag = null
    // Rename in place (owner ask 2026-07-29) — same gesture as an entry row.
    // v0.68.1: the two taps that precede this do NOT net to zero on a header
    // that wasn't active yet -- since the 2026-07-29 rule the first tap only
    // selects/expands and the SECOND toggles, so the rename editor opened on
    // a freshly COLLAPSED header: every entry vanished, the very symptom
    // that rule exists to prevent (the old comment here assumed the pre-rule
    // toggle/toggle pair). Put the collapse state back to what it was before
    // the first tap (noted in onCategoryPointerDown), then open the editor
    // on the freshly rendered row.
    restoreCategoryCollapseAfterDoubleClick(state, category)
    if (!beginInlineRename(state, 'category', category)) focusNameField(state)
  })
  headerEl.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return
    event.preventDefault()
    toggleCategoryCollapse(state, category)
    selectCategory(state, category).catch((error) => api.warn('select category failed', error))
  })
  return headerEl
}

// ---------------------------------------------------------------------------
// Collapsed sections persist with the workflow (owner ask 2026-08-23) — the
// pure array helpers, then the node-property registration/read/write halves
// that keep `state.collapsedCategories` (the render-time cache) and
// `node.properties[PROP_COLLAPSED_SECTIONS]` (the source of truth) in sync.
// See the file header's "Single-tap collapse" section for the full design.
// ---------------------------------------------------------------------------

/**
 * Tolerant parse of the `Collapsed sections` node property into an array of
 * category-name strings. Accepts the canonical shape (an array, non-string
 * entries dropped), a JSON-encoded string of the same (what a hand-edit
 * through the node's Properties panel round-trips as), or anything else --
 * `undefined`/`null`/malformed JSON/a non-array all fold to `[]`. Never
 * throws.
 * @param {unknown} raw
 * @returns {string[]}
 */
export function parseCollapsedSections(raw) {
  if (Array.isArray(raw)) return raw.filter((name) => typeof name === 'string')
  if (typeof raw === 'string') {
    const trimmed = raw.trim()
    if (!trimmed) return []
    let parsed
    try {
      parsed = JSON.parse(trimmed)
    } catch {
      return []
    }
    return Array.isArray(parsed) ? parsed.filter((name) => typeof name === 'string') : []
  }
  return []
}

/**
 * Returns a NEW array with `name` toggled in/out of `list` -- pure, never
 * mutates its argument (this file's other list-transform helpers, e.g.
 * `parseSelectionValue`'s callers, follow the same rule).
 * @param {string[]} list
 * @param {string} name
 * @returns {string[]}
 */
export function toggleCollapsedSection(list, name) {
  const current = Array.isArray(list) ? list : []
  return current.includes(name) ? current.filter((entry) => entry !== name) : [...current, name]
}

/**
 * Whether `name` is collapsed per `list` -- tolerant, a non-array `list`
 * reads as "nothing collapsed" rather than throwing.
 * @param {string[]} list
 * @param {string} name
 * @returns {boolean}
 */
export function isSectionCollapsed(list, name) {
  return Array.isArray(list) && list.includes(name)
}

/**
 * Registers the `Collapsed sections` property and wires it live -- called
 * once from attach(), right after buildUi() (state.listEl must exist for
 * the render calls below). Mirrors this pack's existing per-instance
 * UI-toggle-property idiom (resolution.js's Show original size/Show
 * passthrough, sets.js's Show strength scale/Show loader slot, picker.js's
 * Auto-grow with selection, switcher.js's High/low pairs): `addProperty()`
 * is a silent, unconditional `node.properties[name] = default` -- it never
 * fires `onPropertyChanged` -- so a FRESH node needs the explicit
 * `applyCollapsedSectionsFromProperty()` call below it. A RESTORED node's
 * `configure()` runs immediately after attach() returns (every one of the
 * files above cites the same LGraphNode.ts ordering) and its
 * properties-merge loop both overwrites `node.properties[...]` with the
 * FILE's saved array and fires the wrapped `onPropertyChanged` for it --
 * which re-applies the Set and repaints -- so the saved value always wins
 * last, before any entries-populated render exists to show a flash.
 */
function registerCollapsedSectionsProperty(state) {
  const node = state.node
  if (typeof node.addProperty === 'function') {
    node.addProperty(PROP_COLLAPSED_SECTIONS, [], 'array')
  } else {
    node.properties = node.properties || {}
    if (!(PROP_COLLAPSED_SECTIONS in node.properties)) node.properties[PROP_COLLAPSED_SECTIONS] = []
  }
  const original = node.onPropertyChanged
  node.onPropertyChanged = function (name, value, prevValue) {
    const result = original?.call(this, name, value, prevValue)
    if (name === PROP_COLLAPSED_SECTIONS) {
      // Covers BOTH configure()'s restore (the saved array lands here) and a
      // live hand-edit through the node's right-click Properties panel --
      // the latter is the nice-to-have "a hand-edited property repaints the
      // section headers" (owner ask's implementation note).
      applyCollapsedSectionsFromProperty(state)
      renderList(state)
    }
    return result
  }
  // addProperty() alone never fires onPropertyChanged (see above) -- sync a
  // FRESH node's Set explicitly now; a RESTORED node's configure() does this
  // again momentarily with the real saved value via the wrapper just above.
  applyCollapsedSectionsFromProperty(state)
}

/**
 * READ half: replaces the contents of `state.collapsedCategories` (the
 * render-time cache -- unchanged `.has()` calls throughout renderList()/
 * buildCategoryHeaderRow()) with whatever the node property currently says.
 * Never writes the property or dirties the canvas -- see
 * syncCollapsedSectionsProperty() for the write half.
 */
function applyCollapsedSectionsFromProperty(state) {
  const names = parseCollapsedSections(state.node.properties?.[PROP_COLLAPSED_SECTIONS])
  state.collapsedCategories = new Set(names)
}

/**
 * WRITE half: folds the CURRENT `state.collapsedCategories` Set back into
 * the node property and dirties the canvas so the workflow's next save
 * captures it -- called from every place that mutates the Set (the toggle
 * below, the double-click restore, and the two rename migrations that move
 * a collapsed key from the old category name to the new one).
 */
function syncCollapsedSectionsProperty(state) {
  const node = state.node
  node.properties = node.properties || {}
  node.properties[PROP_COLLAPSED_SECTIONS] = Array.from(state.collapsedCategories)
  node.graph?.setDirtyCanvas(true, true)
}

// ---------------------------------------------------------------------------
// Open category persists across a REBUILD (see PROP_OPEN_CATEGORY's own doc
// comment for the owner rule and the rebuild-vs-reload distinction). The
// pure parser, then the property registration/read/write halves. The READ
// half that actually matters — restoring the pointer after a tab-switch
// rebuild — is deliberately NOT here: it lives in applyNotebookPayload(),
// right alongside the entry selection's own restore, because only that
// reload cycle has a freshly-fetched `state.categories` to validate the
// remembered name against. What lives here is registration plus the live
// "hand-edit the Properties panel" counterpart — see
// registerOpenCategoryProperty()'s doc for exactly where the line falls.
// ---------------------------------------------------------------------------

/**
 * Tolerant parse of the `Open category` node property into a category NAME
 * or `null` ("nothing open") — mirrors parseCollapsedSections()'s
 * never-throws contract, one value instead of a list: a non-string (a
 * hand-edit through the node's Properties panel that types a
 * number/object/etc, or simply `undefined`/`null` on a node whose property
 * hasn't been registered yet) and the empty string (the property's own
 * default, and what syncOpenCategoryProperty() writes for "nothing open")
 * both fold to `null`. Deliberately does NOT check the name against
 * `state.categories` — neither of this function's callers can, at the
 * moment they call it, always know what the CURRENT set of categories is;
 * they each do that validation themselves, against whatever `state.
 * categories` they actually have in hand (registerOpenCategoryProperty()'s
 * wrapper vs. applyNotebookPayload()'s own read of this property).
 * @param {unknown} raw
 * @returns {string|null}
 */
export function parseOpenCategoryProperty(raw) {
  if (typeof raw !== 'string') return null
  return raw.length ? raw : null
}

/**
 * Registers the `Open category` property and wires it live — called once
 * from attach(), right after buildUi(), same placement and same reason as
 * registerCollapsedSectionsProperty() immediately above: `configure()`
 * runs right after attach() returns, and its properties-merge loop both
 * overwrites `node.properties[PROP_OPEN_CATEGORY]` with the FILE's saved
 * name and fires the wrapped `onPropertyChanged` below for it. Same
 * addProperty()-vs-fallback branch, same "addProperty() never fires
 * onPropertyChanged, so a FRESH node needs one explicit apply call too"
 * reasoning as the collapsed-sections property just above.
 *
 * Where this ACTUALLY diverges from that property: at the instant either
 * the fresh-node explicit call below or configure()'s properties-merge
 * fires the wrapper, `state.categories` is always still empty — attach()
 * runs before this panel has fetched anything — so
 * applyOpenCategoryFromProperty() can only ever degrade to "nothing open"
 * from either of those two call sites, by design, never a bug. The REAL
 * tab-switch-rebuild restore this property exists for happens moments
 * later, once reloadNow()'s applyNotebookPayload() has a real `state.
 * categories` to validate the saved name against (see its own comment,
 * right where it restores the entry selection the same way) — that path
 * reads `node.properties[PROP_OPEN_CATEGORY]` directly, independent of
 * whether this wrapper ever ran or what it did. What THIS wrapper is
 * actually for: the same nice-to-have registerCollapsedSectionsProperty()'s
 * own wrapper has — a LIVE hand-edit of "Open category" through the node's
 * right-click Properties panel, made after the panel already has real
 * categories loaded, opens (or closes) that category the same way a click
 * would.
 */
function registerOpenCategoryProperty(state) {
  const node = state.node
  if (typeof node.addProperty === 'function') {
    node.addProperty(PROP_OPEN_CATEGORY, '', 'string')
  } else {
    node.properties = node.properties || {}
    if (!(PROP_OPEN_CATEGORY in node.properties)) node.properties[PROP_OPEN_CATEGORY] = ''
  }
  const original = node.onPropertyChanged
  node.onPropertyChanged = function (name, value, prevValue) {
    const result = original?.call(this, name, value, prevValue)
    if (name === PROP_OPEN_CATEGORY) {
      applyOpenCategoryFromProperty(state)
    }
    return result
  }
  // addProperty() alone never fires onPropertyChanged (see above) -- apply
  // explicitly now for a FRESH node too. A no-op in practice (state.
  // activeCategory is already `null` and state.categories is still empty
  // this early in attach()), same as the wrapper's own no-op moment above
  // -- kept for the same completeness reason
  // registerCollapsedSectionsProperty() keeps its own explicit call.
  applyOpenCategoryFromProperty(state)
}

/**
 * Validates the `Open category` property against `state.categories` and
 * applies it to `state.activeCategory` if it changed — see
 * registerOpenCategoryProperty()'s doc for why this only ever does
 * anything real off a LIVE hand-edit (categories already loaded), never
 * off the configure()-time firing (categories always still empty then).
 * Guards the same corruption cases parseOpenCategoryProperty() already
 * tolerates PLUS a name the file doesn't have — both degrade to leaving
 * `state.activeCategory` exactly as it was, never a throw.
 */
function applyOpenCategoryFromProperty(state) {
  const name = parseOpenCategoryProperty(state.node.properties?.[PROP_OPEN_CATEGORY])
  if (name != null && !state.categories.includes(name)) return
  if (state.activeCategory === name) return
  state.activeCategory = name
  renderList(state)
  updateModeHint(state)
}

/**
 * WRITE half: folds the CURRENT `state.activeCategory` into the node
 * property (`''` for "nothing open") and dirties the canvas so the
 * workflow's next save captures it — called from every place that assigns
 * `state.activeCategory` (selectCategory() and its rollback branch,
 * chooseSelection()'s category-mode exit, confirmNewEntry()'s exit /
 * confirmNewCategory()'s enter, the header ✕'s enter-then-delete,
 * performDeleteCategory()'s exit, the two rename migrations,
 * clearEditor()'s initial null, and the reload-path validity check that
 * prunes a stale name) — same idiom as syncCollapsedSectionsProperty()
 * above.
 */
function syncOpenCategoryProperty(state) {
  const node = state.node
  node.properties = node.properties || {}
  node.properties[PROP_OPEN_CATEGORY] = state.activeCategory ?? ''
  node.graph?.setDirtyCanvas(true, true)
}

/** Single-tap collapse (FORMAT.md §7.2 amendment, owner ask 2026-07-19) —
 * flips `category`'s membership in `state.collapsedCategories` and
 * re-renders; the Set is now a cache over the `Collapsed sections` node
 * property (see the file header) rather than session-only state, so every
 * mutation here also calls syncCollapsedSectionsProperty() to write it
 * through and dirty the canvas. Called unconditionally on every tap (see
 * onCategoryPointerDown()/the header's own keydown handler above) — even
 * when the category is already active, since re-selecting it is a no-op
 * for selectCategory() but the collapse toggle must still happen.
 *
 * EXCEPT on the tap that first SELECTS the category (2026-07-29, found on the
 * rig while chasing the owner's rename report). Selecting a category is the
 * only way to get its name into the editor, and folding collapse into that
 * same tap meant "click the category you want to rename" also hid every entry
 * inside it — the rename worked, but it looked like the entries had been
 * eaten. So the FIRST tap on a not-yet-active header only selects (leaving it
 * expanded, or expanding it if it was collapsed); once it IS the active
 * category, taps toggle collapse exactly as before. Every entry stays reachable
 * at the moment you go to rename its category. */
function toggleCategoryCollapse(state, category) {
  if (state.activeCategory !== category) {
    // The selecting tap: never hide the contents, but do reveal them if this
    // header was sitting collapsed.
    if (state.collapsedCategories.delete(category)) syncCollapsedSectionsProperty(state)
    renderList(state)
    return
  }
  if (state.collapsedCategories.has(category)) {
    state.collapsedCategories.delete(category)
  } else {
    state.collapsedCategories.add(category)
  }
  syncCollapsedSectionsProperty(state)
  renderList(state)
}

/**
 * v0.68.1 -- see the header's dblclick handler. The memo is the collapse
 * state as of the pair's FIRST pointerdown (onCategoryPointerDown); this
 * puts it back and repaints so beginInlineRename() mounts on the fresh row.
 * No memo for this header (keyboard taps, a stale note from another
 * header) means nothing to restore. Also writes through the property (owner
 * ask 2026-08-23) since this is a genuine collapse-state mutation, same as
 * toggleCategoryCollapse() above.
 */
function restoreCategoryCollapseAfterDoubleClick(state, category) {
  const memo = state.categoryTapMemo
  state.categoryTapMemo = null
  if (!memo || memo.category !== category) return
  if (state.collapsedCategories.has(category) === memo.collapsed) return
  if (memo.collapsed) state.collapsedCategories.add(category)
  else state.collapsedCategories.delete(category)
  syncCollapsedSectionsProperty(state)
  renderList(state)
}

// ---------------------------------------------------------------------------
// Drag-to-reorder (FORMAT.md §3.4/§5/§7.2)
//
// Pointer-based (see the file header's pointer-events citation), mirroring
// this file's own pane-splitter drag: capture the pointer on the row that
// started the gesture, but listen on `window` so movement outside the row's
// (or even the list's) bounds still tracks. A single pointerdown starts a
// tentative gesture that resolves ONE of two ways on pointerup:
//  - moved < DRAG_THRESHOLD_PX the whole time → a click; dispatched to the
//    plain/ctrl/shift selection logic above (entries) or the collapse-
//    toggle+select logic (categories, "Categories in the UI" above).
//  - moved >= DRAG_THRESHOLD_PX at any point → a drag; commits to reorder
//    and the click never fires.
//
// Two drag SOURCES share this gesture — an entry row (onEntryPointerDown,
// `drag.kind === 'entry'`) and a category header (onCategoryPointerDown,
// `drag.kind === 'category'`, FORMAT.md §7.2 amendment "drag a category
// header"). beginDrag()/endDragVisuals()/positionMarker()/cancelDrag() are
// kind-agnostic (pure pointer-capture/visual bookkeeping); updateDrag()/
// finishDrag() branch on `drag.kind` for the parts that actually differ:
// which geometry function computes a drop target, and which §5 route
// commits it.
// ---------------------------------------------------------------------------

function onEntryPointerDown(state, event, name) {
  if (event.button !== 0) return // primary button/touch only
  if (isPinned(state)) return // M3: no selection change / drag start while pinned (live rows aren't rendered then; belt-and-braces)
  cancelDeleteConfirm(state)

  const drag = {
    kind: 'entry',
    pointerId: event.pointerId,
    name,
    startX: event.clientX,
    startY: event.clientY,
    active: false,
    modifiers: { shiftKey: event.shiftKey, toggleKey: event.ctrlKey || event.metaKey },
    rowEl: event.currentTarget,
    marker: null,
    target: null
  }
  state.drag = drag

  const onMove = (moveEvent) => {
    if (moveEvent.pointerId !== drag.pointerId) return
    if (!drag.active) {
      if (state.busy) return // don't start reordering mid-save/delete/move
      const dx = moveEvent.clientX - drag.startX
      const dy = moveEvent.clientY - drag.startY
      if (Math.hypot(dx, dy) < DRAG_THRESHOLD_PX) return
      beginDrag(state, drag)
    }
    updateDrag(state, drag, moveEvent.clientY)
  }
  const onUp = (upEvent) => {
    if (upEvent.pointerId !== drag.pointerId) return
    detach()
    if (drag.active) {
      finishDrag(state, drag)
    } else {
      handleEntryClick(state, name, drag.modifiers)
    }
    state.drag = null
  }
  const onCancel = (cancelEvent) => {
    if (cancelEvent.pointerId !== drag.pointerId) return
    detach()
    if (drag.active) cancelDrag(state, drag)
    state.drag = null
  }
  function detach() {
    window.removeEventListener('pointermove', onMove, { capture: true })
    window.removeEventListener('pointerup', onUp, { capture: true })
    window.removeEventListener('pointercancel', onCancel, { capture: true })
  }
  // Escape hatch for teardown() — a node removal mid-drag has no pointerup
  // of its own, so it must be able to detach + restore visuals itself.
  drag.cleanup = () => {
    detach()
    if (drag.active) cancelDrag(state, drag)
  }

// CAPTURE-phase window listeners, deliberately (2026-07-30, owner's Mac
  // report, reproduced on the rig): under Vue nodes ("New node design") the
  // node's DOM wrapper stops pointer-event propagation on the way UP, so a
  // plain (bubble-phase) window listener never fires and the gesture never
  // commits -- clicks stopped selecting, drags stopped dropping, while
  // element-level listeners (typing, buttons) kept working, which made the
  // panel look half-broken. Capture descends from the window BEFORE any
  // bubble-path stopPropagation can intervene, and nothing above window
  // exists to stop it -- so these fire in BOTH renderers. The matching
  // removeEventListener calls must pass the same capture flag or they
  // silently fail to detach.
  window.addEventListener('pointermove', onMove, { capture: true })
  window.addEventListener('pointerup', onUp, { capture: true })
  window.addEventListener('pointercancel', onCancel, { capture: true })
}

function beginDrag(state, drag) {
  drag.active = true
  try {
    drag.rowEl.setPointerCapture(drag.pointerId)
  } catch {
    // Best-effort, mirrors wireSplitter() — the window-level listeners still
    // cover the drag either way.
  }
  drag.rowEl.classList.add('llnb-entry-dragging')
  // Multiselect drag into a category (FORMAT.md §7.2 amendment): dim every
  // OTHER selected row too, so the drag visually reads as "this whole
  // group is moving" — dragMoveNames() is the same decision finishDrag()
  // uses for the actual move. Tracked on `drag` itself (not looked up
  // again) so endDragVisuals() can undo exactly these, no more, no less.
  drag.dimmedEls = []
  if (drag.kind === 'entry') {
    for (const name of dragMoveNames(state, drag)) {
      if (name === drag.name) continue
      const row = state.dragRows.find((r) => r.kind === 'entry' && r.name === name)
      if (row) {
        row.el.classList.add('llnb-entry-dragging')
        drag.dimmedEls.push(row.el)
      }
    }
  }
  drag.marker = el('div', { className: 'llnb-drag-marker' })
}

function updateDrag(state, drag, clientY) {
  drag.target =
    drag.kind === 'category'
      ? computeCategoryDropTarget(state, clientY, drag.category)
      : computeDropTarget(state, clientY, dragMoveNames(state, drag))
  positionMarker(drag)
}

function positionMarker(drag) {
  drag.marker.remove()
  const target = drag.target
  if (!target) return
  if (target.kind === 'before') {
    target.markerBeforeEl.before(drag.marker)
  } else {
    target.markerAfterEl.after(drag.marker)
  }
}

function endDragVisuals(drag) {
  try {
    drag.rowEl.releasePointerCapture(drag.pointerId)
  } catch {
    // Already released, or never captured.
  }
  drag.rowEl.classList.remove('llnb-entry-dragging')
  for (const dimmedEl of drag.dimmedEls || []) dimmedEl.classList.remove('llnb-entry-dragging')
  drag.marker?.remove()
}

/** The full set of names one drag gesture is moving (FORMAT.md §7.2
 * amendment, "Multiselect drag into a category") — the WHOLE selection, in
 * selection order, when the dragged row is itself part of a 2+ selection;
 * otherwise just the single dragged row, exactly like before multiselect
 * drag existed. Shared by updateDrag()'s drop-target exclusion and
 * finishDrag()'s move dispatch, so the two always agree on what's moving. */
function dragMoveNames(state, drag) {
  if (state.selection.length >= 2 && isSelected(state, drag.name)) return state.selection
  return [drag.name]
}

function finishDrag(state, drag) {
  endDragVisuals(drag)
  const target = drag.target
  if (!target) return
  if (drag.kind === 'category') {
    if (isNoopCategoryMove(state, drag.category, target)) return
    performMoveCategory(state, drag.category, target).catch((error) => api.warn('move category failed', error))
    return
  }
  const names = dragMoveNames(state, drag)
  if (names.length > 1) {
    performMoveRun(state, names, target).catch((error) => api.warn('move failed', error))
    return
  }
  if (isNoopMove(state, drag.name, target)) return
  performMove(state, drag.name, target).catch((error) => api.warn('move failed', error))
}

function cancelDrag(state, drag) {
  endDragVisuals(drag)
}

/**
 * Hit-tests `clientY` against the rendered rows (headers + entries, minus
 * the row being dragged) and returns the §5 `/notebook/move` target it
 * corresponds to, or null if there's nothing to hit (empty list).
 *
 * Model: find the row whose vertical midpoint is nearest `clientY`.
 *  - Nearest is an ENTRY and clientY is above its midpoint → before that
 *    entry.
 *  - Nearest is an ENTRY and clientY is at/below its midpoint → before the
 *    NEXT entry if the next row is an entry, else append to THIS entry's
 *    category (the next row is a different category's header, or there is
 *    no next row at all — either way this entry is the last of its run).
 *  - Nearest is a category HEADER (clientY landed anywhere near it, above
 *    or below) → append to that category, regardless of pointer side: §3.4
 *    has no "before a category heading" primitive (`before` always names a
 *    sibling ENTRY), so a header can only ever mean "append to this
 *    category's end" — the marker is placed at that category's actual last
 *    row so it never visually promises a landing spot other than where the
 *    entry will really go.
 * `excludeNames` (FORMAT.md §7.2 amendment: multiselect drag into a
 * category) leaves out every row currently being dragged, not just one —
 * see dragMoveNames() — so a multi-drag can never resolve to "before" a
 * row that's part of the same moving group.
 * @returns {{kind:'before', before:string, markerBeforeEl:HTMLElement} |
 *           {kind:'category', category:string, markerAfterEl:HTMLElement} | null}
 */
function computeDropTarget(state, clientY, excludeNames) {
  const excluded = new Set(excludeNames)
  const rows = state.dragRows.filter((row) => row.kind !== 'entry' || !excluded.has(row.name))
  if (!rows.length) return null

  let bestIndex = -1
  let bestMid = 0
  let bestDist = Infinity
  for (let i = 0; i < rows.length; i++) {
    const rect = rows[i].el.getBoundingClientRect()
    const mid = rect.top + rect.height / 2
    const dist = Math.abs(clientY - mid)
    if (dist < bestDist) {
      bestDist = dist
      bestIndex = i
      bestMid = mid
    }
  }
  const best = rows[bestIndex]

  if (best.kind === 'header') {
    return { kind: 'category', category: best.category, markerAfterEl: lastRowElOfCategory(rows, bestIndex) }
  }

  const above = clientY < bestMid
  if (above) {
    return { kind: 'before', before: best.name, markerBeforeEl: best.el }
  }
  const next = rows[bestIndex + 1]
  if (next && next.kind === 'entry') {
    return { kind: 'before', before: next.name, markerBeforeEl: next.el }
  }
  return { kind: 'category', category: best.category, markerAfterEl: best.el }
}

/** Walks forward from a header row to the last entry belonging to it
 * (falling back to the header itself for an empty category). `rows` is
 * already exclude-filtered, so this naturally treats "only the dragged
 * entry was in this category" as empty too. */
function lastRowElOfCategory(rows, headerIndex) {
  let lastEl = rows[headerIndex].el
  for (let i = headerIndex + 1; i < rows.length; i++) {
    if (rows[i].kind === 'header') break
    lastEl = rows[i].el
  }
  return lastEl
}

/**
 * computeDropTarget()'s sibling for a whole-category drag (FORMAT.md §3.4
 * Move category, §7.2 amendment "drag a category header"): valid targets
 * are only "before another category header" or "end of file" — §3.4 has no
 * "into"/"before an entry" primitive for a category block, unlike an
 * entry's drop geometry above. Hit-tests against category headers ONLY
 * (excluding the one being dragged); past the last header, or when there's
 * no other category at all, falls to "end", anchored at the actual last
 * row so the marker never promises a landing spot other than where the
 * block will really go (same reasoning lastRowElOfCategory() documents for
 * an empty category above).
 * @returns {{kind:'before', before:string, markerBeforeEl:HTMLElement} |
 *           {kind:'end', markerAfterEl:HTMLElement} | null}
 */
function computeCategoryDropTarget(state, clientY, excludeCategory) {
  const rows = state.dragRows
  if (!rows.length) return null
  const headers = rows.filter((row) => row.kind === 'header' && row.category !== excludeCategory)
  if (!headers.length) {
    return { kind: 'end', markerAfterEl: rows[rows.length - 1].el }
  }

  let bestIndex = -1
  let bestMid = 0
  let bestDist = Infinity
  for (let i = 0; i < headers.length; i++) {
    const rect = headers[i].el.getBoundingClientRect()
    const mid = rect.top + rect.height / 2
    const dist = Math.abs(clientY - mid)
    if (dist < bestDist) {
      bestDist = dist
      bestIndex = i
      bestMid = mid
    }
  }
  const best = headers[bestIndex]
  if (clientY < bestMid) {
    return { kind: 'before', before: best.category, markerBeforeEl: best.el }
  }
  const next = headers[bestIndex + 1]
  if (next) {
    return { kind: 'before', before: next.category, markerBeforeEl: next.el }
  }
  return { kind: 'end', markerAfterEl: rows[rows.length - 1].el }
}

/** isNoopMove()'s sibling for a whole-category drag: true when `target`
 * already describes where `draggedCategory` sits (adjacent, in file
 * order). Like isNoopMove(), this is purely an optimization — a drop that
 * turns out to be a no-op position is otherwise harmless to send anyway. */
function isNoopCategoryMove(state, draggedCategory, target) {
  const categories = state.categories
  const index = categories.indexOf(draggedCategory)
  if (index === -1) return false
  const next = categories[index + 1]
  if (target.kind === 'before') return !!next && next === target.before
  return next == null
}

/** True when `target` describes the position `draggedName` is already in —
 * skips a pointless request + conflict round-trip for a drop back onto its
 * own slot. */
function isNoopMove(state, draggedName, target) {
  const entries = state.entries
  const index = entries.findIndex((entry) => entry.name === draggedName)
  if (index === -1) return false
  const currentCategory = entries[index].category || ''
  const next = entries[index + 1]

  if (target.kind === 'before') {
    return !!next && next.name === target.before
  }
  const isLastOfOwnCategory = !next || (next.category || '') !== currentCategory
  return currentCategory === target.category && isLastOfOwnCategory
}

/**
 * Client-side mirror of markdown_store.move_entry's before/category
 * placement, applied to the panel's own `entries` array ({name, category}
 * objects, file order) — finding 3 (2026-08-26 responsiveness round): a
 * drop used to sit at its OLD position until the round trip answered,
 * which read as "snapped back until the server answers." Called BEFORE
 * the request so the row visibly lands in its new slot at once; the
 * response (or, on failure, the existing conflict/error reload paths —
 * they already restore) reconciles with disk truth afterward. Returns a
 * NEW array (never mutates `entries`); falls back to `entries` UNCHANGED
 * when `name`/`target.before` can't be found locally (shouldn't happen —
 * the drop target came from a rendered row — but the request/response
 * cycle still lands the true result either way).
 */
function reorderEntriesLocally(entries, name, target, categories) {
  const index = entries.findIndex((entry) => entry.name === name)
  if (index === -1) return entries
  const next = entries.slice()
  const [moved] = next.splice(index, 1)
  if (target.kind === 'before') {
    const beforeIndex = next.findIndex((entry) => entry.name === target.before)
    if (beforeIndex === -1) return entries
    next.splice(beforeIndex, 0, { ...moved, category: next[beforeIndex].category })
    return next
  }
  // 'category': append to the END of that category's run of entries
  // (mirrors markdown_store.move_entry's dst_block.entries.append).
  //
  // v0.85.1 (owner report 2026-08-28: "if the top group has nothing in it
  // you can't drag new items into it -- they always end up in the last
  // group"): when the target category is EMPTY here, there is no run to
  // append after, and falling back to `next.length` put the entry at the
  // end of the WHOLE list -- i.e. inside whatever group renders last.
  // That is a pure optimistic-paint bug (the server's move_entry finds the
  // block by name and appends correctly), which is why it looked
  // position-dependent and why the "move the group, drag, move it back"
  // dance appeared to work. The empty case now derives the insertion point
  // from the CATEGORY ORDER: the first entry belonging to a category that
  // sorts AFTER the target, else the end of the list when the target is
  // genuinely last. `categories` is the panel's own ordered name list;
  // without it (a caller that has none) the old end-of-list fallback
  // stands, which is correct for a last-position target either way.
  const targetCategory = target.category || ''
  let insertAt = -1
  for (let i = next.length - 1; i >= 0; i--) {
    if ((next[i].category || '') === targetCategory) {
      insertAt = i + 1
      break
    }
  }
  if (insertAt === -1) {
    insertAt = emptyCategoryInsertIndex(next, targetCategory, categories)
  }
  next.splice(insertAt, 0, { ...moved, category: targetCategory })
  return next
}

/**
 * Where a run of *category*'s entries BELONGS in an entry list that
 * currently holds none of them (v0.85.1 -- see reorderEntriesLocally's
 * 'category' branch). Ranks by position in *categories*, the panel's
 * ordered category-name list; the uncategorized head region ('') always
 * ranks first, and a name missing from the list ranks last. Returns the
 * index of the first entry whose category ranks strictly AFTER the target,
 * else the list length.
 */
export function emptyCategoryInsertIndex(entries, category, categories) {
  const order = Array.isArray(categories) ? categories : []
  const rankOf = (name) => {
    const value = name || ''
    if (value === '') return -1 // the head region precedes every '#' block
    const index = order.indexOf(value)
    return index === -1 ? order.length : index
  }
  const targetRank = rankOf(category)
  for (let i = 0; i < entries.length; i++) {
    if (rankOf(entries[i].category) > targetRank) return i
  }
  return entries.length
}

/** reorderEntriesLocally()'s sibling for a multiselect drag
 * (performMoveRun): applies the SAME target to every name in selection
 * order, mirroring both the panel's own drag semantics and the finding-4
 * batch route's own per-name loop (routes_notebook.py's
 * post_notebook_move), so the optimistic order matches what the batch
 * response will confirm. */
function reorderEntriesLocallyMany(entries, names, target, categories) {
  let next = entries
  for (const name of names) next = reorderEntriesLocally(next, name, target, categories)
  return next
}

/** reorderEntriesLocally()'s category-block sibling for
 * performMoveCategory: relocates a WHOLE category's entries together with
 * its heading. renderList() renders a category's rows by walking `entries`
 * in LOCKSTEP with `categories`' own order (each category consumes its
 * contiguous run before the next begins) — reordering `categories` alone
 * without moving this block would visually scramble the list until the
 * response landed. `target.kind` is `'before'`/`'end'` here
 * (computeCategoryDropTarget()'s shape), unlike an entry target's
 * `'before'`/`'category'`. */
function reorderCategoryEntriesLocally(entries, category, target) {
  const moved = entries.filter((entry) => (entry.category || '') === category)
  if (!moved.length) return entries // nothing to relocate -- categories reorder alone reads fine
  const rest = entries.filter((entry) => (entry.category || '') !== category)
  let insertAt = rest.length
  if (target.kind === 'before') {
    const beforeIndex = rest.findIndex((entry) => (entry.category || '') === target.before)
    if (beforeIndex !== -1) insertAt = beforeIndex
  }
  const next = rest.slice()
  next.splice(insertAt, 0, ...moved)
  return next
}

/** reorderEntriesLocally()'s sibling over `state.categories` (a flat name
 * list) for performMoveCategory — mirrors markdown_store.move_category's
 * before/end placement. A repeated category name (hand-edited file) targets
 * the LAST occurrence, same convention the store itself documents. */
function reorderCategoriesLocally(categories, name, target) {
  const index = categories.lastIndexOf(name)
  if (index === -1) return categories
  const next = categories.slice()
  next.splice(index, 1)
  if (target.kind === 'before') {
    const beforeIndex = next.lastIndexOf(target.before)
    if (beforeIndex === -1) return categories
    next.splice(beforeIndex, 0, name)
    return next
  }
  next.push(name)
  return next
}

/**
 * Commits one drag-drop as a single §5 `/notebook/move`. A 409 surfaces
 * through the same conflict UI Save/Delete use (Reload / Overwrite, where
 * Overwrite retries this exact move with the mtime check skipped); any
 * other error reports on the status line and falls back to a full reload
 * (§3.5 notwithstanding, a move failure means we no longer trust our
 * in-memory ordering).
 */
async function performMove(state, name, target, { force = false } = {}) {
  if (pinnedRefuse(state)) return // M3
  if (writesBlocked(state)) {
    showLoadError(state)
    return
  }
  if (state.busy) return
  // Optimistic reorder (finding 3): land the row at its new slot BEFORE the
  // round trip -- see reorderEntriesLocally()'s own doc for the rollback story.
  const entriesBeforeMove = state.entries
  state.entries = reorderEntriesLocally(state.entries, name, target, state.categories)
  renderList(state)
  state.busy = true
  updateSaveButtonEnabled(state)
  updateDeleteButtonEnabled(state)
  setStatus(state, 'Moving…')
  try {
    const body = { file: state.file, name }
    if (target.kind === 'before') body.before = target.before
    else body.category = target.category
    if (!force && typeof state.baseMtime === 'number') body.base_mtime = state.baseMtime

    const data = await api.postJson('/lora_library/notebook/move', body, {
      timeoutMs: WRITE_TIMEOUT_MS
    })
    state.busy = false
    state.entries = Array.isArray(data.entries) ? data.entries : state.entries
    // The move just wrote the file, advancing its mtime — the active
    // entry's own content didn't change, but a stale baseMtime here would
    // make the NEXT save/delete/move spuriously 409 against this move's own
    // write (§3.5's conflict check is file-wide, not per-entry).
    noteFileMtime(state, data.mtime)
    syncNotebookCache(state, data) // session cache (file header)
    // A move only reorders/recategorizes — it never adds or removes
    // entries — so the current selection/active stay exactly as they were;
    // just re-render against the fresh order.
    setSelection(state, state.selection, state.activeName)
    updateSaveButtonEnabled(state)
    setStatus(state, 'Moved.')
  } catch (error) {
    state.busy = false
    updateSaveButtonEnabled(state)
    updateDeleteButtonEnabled(state)
    if (await recoverFromWriteTimeout(state, error)) {
      // handled: reloaded to check what landed
    } else if (error?.status === 409) {
      // v0.85.1: undo the optimistic reorder FIRST. The server refused, so
      // leaving the row at its new slot showed a move that never happened
      // (owner report 2026-08-28: the conflict banner appeared AND the
      // entry appeared moved -- two lies at once). Restore the pre-drag
      // order, then let the banner offer Reload/Overwrite over the truth.
      state.entries = entriesBeforeMove
      renderList(state)
      showConflict(state, 'File changed on disk', {
        onReload: () => reloadNow(state),
        onOverwrite: () => performMove(state, name, target, { force: true })
      })
    } else {
      api.warn('failed to move notebook entry', error)
      try {
        await reloadNow(state)
      } catch (reloadError) {
        api.warn('notebook reload after move failure failed', reloadError)
      }
      setStatus(state, `Move failed: ${error.message}`)
    }
  }
}

/**
 * Multiselect drag into a category (FORMAT.md §7.2 amendment, owner ask
 * 2026-07-19): performMove()'s sibling for moving MULTIPLE entries as one
 * unit. Finding 4 (2026-08-26 responsiveness round): ONE §5 batch
 * `/notebook/move` request now moves every name in `names` (selection
 * order) to the SAME `target`, replacing what used to be N sequential
 * single-name POSTs — one disk write, one mtime bump, applying the SAME
 * `target` to every name in order (routes_notebook.py's post_notebook_move
 * batch docstring does the identical per-name loop server-side now, so the
 * result is byte-identical to the old sequential run). The store's
 * single-write nature makes a failure ALL-OR-NOTHING: unlike the old
 * sequential run, a 409 or an unknown name means NOTHING moved, so there is
 * no "resume at index" left to do — Overwrite just retries the SAME full
 * `names` list with the mtime check skipped. Finding 3: the panel's own
 * `entries` order is spliced to the intended result BEFORE the request (see
 * reorderEntriesLocallyMany()), so the drop lands visually at once instead
 * of snapping back until the response arrives.
 */
async function performMoveRun(state, names, target, { force = false } = {}) {
  if (pinnedRefuse(state)) return // M3
  if (writesBlocked(state)) {
    showLoadError(state)
    return
  }
  const entriesBeforeMove = state.entries
  state.entries = reorderEntriesLocallyMany(state.entries, names, target, state.categories)
  renderList(state)
  state.busy = true
  updateSaveButtonEnabled(state)
  updateDeleteButtonEnabled(state)
  setStatus(state, `Moving ${names.length} entries…`)

  let data
  try {
    const body = { file: state.file, names }
    if (target.kind === 'before') body.before = target.before
    else body.category = target.category
    if (!force && typeof state.baseMtime === 'number') body.base_mtime = state.baseMtime
    data = await api.postJson('/lora_library/notebook/move', body, {
      timeoutMs: WRITE_TIMEOUT_MS
    })
  } catch (error) {
    state.busy = false
    updateSaveButtonEnabled(state)
    updateDeleteButtonEnabled(state)
    if (await recoverFromWriteTimeout(state, error)) return
    if (error?.status === 409) {
      // v0.85.1: see performMove() -- undo the optimistic reorder before
      // showing the banner, so the panel never displays a refused move.
      state.entries = entriesBeforeMove
      renderList(state)
      showConflict(state, 'File changed on disk', {
        onReload: () => reloadNow(state),
        onOverwrite: () => performMoveRun(state, names, target, { force: true })
      })
    } else {
      api.warn('failed to move notebook entries', error)
      try {
        await reloadNow(state)
      } catch (reloadError) {
        api.warn('notebook reload after move failure failed', reloadError)
      }
      setStatus(state, `Move failed: ${error.message}`)
    }
    return
  }

  state.entries = Array.isArray(data.entries) ? data.entries : state.entries
  noteFileMtime(state, data.mtime)
  syncNotebookCache(state, data) // session cache (file header)

  state.busy = false
  // Same reasoning as performMove()'s own tail: a move never adds/removes
  // entries, so selection/active stay as they were — just re-render.
  setSelection(state, state.selection, state.activeName)
  updateSaveButtonEnabled(state)
  setStatus(state, `Moved ${names.length} entries.`)
}

/**
 * Drag a category header (FORMAT.md §3.4 Move category, §7.2 amendment):
 * performMove()'s sibling for relocating a WHOLE category block via §5
 * `/notebook/move_category`. Same conflict/force-retry shape; unlike an
 * entry move, `state.categories`/`state.entries` both come back fresh in
 * one response (the block's entries don't change identity, just position),
 * and neither `state.selection` nor `state.activeName`/`activeCategory`
 * need reconciling — names are untouched by a move, only their position.
 */
async function performMoveCategory(state, category, target, { force = false } = {}) {
  if (pinnedRefuse(state)) return // M3
  if (writesBlocked(state)) {
    showLoadError(state)
    return
  }
  if (state.busy) return
  // Optimistic reorder (finding 3, 2026-08-26 responsiveness round): move
  // the header AND its entries' block together, before the round trip --
  // see reorderCategoryEntriesLocally()'s doc for why both have to move
  // together for renderList() to stay coherent while the request is in
  // flight. On failure, the existing conflict/error reload paths below
  // already restore disk truth.
  state.categories = reorderCategoriesLocally(state.categories, category, target)
  state.entries = reorderCategoryEntriesLocally(state.entries, category, target)
  renderList(state)
  state.busy = true
  updateSaveButtonEnabled(state)
  updateDeleteButtonEnabled(state)
  setStatus(state, 'Moving category…')
  try {
    const body = { file: state.file, name: category }
    if (target.kind === 'before') body.before = target.before
    if (!force && typeof state.baseMtime === 'number') body.base_mtime = state.baseMtime

    const data = await api.postJson('/lora_library/notebook/move_category', body, {
      timeoutMs: WRITE_TIMEOUT_MS
    })
    state.busy = false
    state.entries = Array.isArray(data.entries) ? data.entries : state.entries
    state.categories = Array.isArray(data.categories) ? data.categories : state.categories
    noteFileMtime(state, data.mtime)
    syncNotebookCache(state, data) // session cache (file header)
    renderList(state)
    updateSaveButtonEnabled(state)
    updateDeleteButtonEnabled(state)
    setStatus(state, 'Moved category.')
  } catch (error) {
    state.busy = false
    updateSaveButtonEnabled(state)
    updateDeleteButtonEnabled(state)
    if (await recoverFromWriteTimeout(state, error)) {
      // handled: reloaded to check what landed
    } else if (error?.status === 409) {
      showConflict(state, 'File changed on disk', {
        onReload: () => reloadNow(state),
        onOverwrite: () => performMoveCategory(state, category, target, { force: true })
      })
    } else {
      api.warn('failed to move notebook category', error)
      try {
        await reloadNow(state)
      } catch (reloadError) {
        api.warn('notebook reload after move-category failure failed', reloadError)
      }
      setStatus(state, `Move failed: ${error.message}`)
    }
  }
}

/**
 * onEntryPointerDown()'s sibling for a category header (FORMAT.md §7.2
 * amendment "drag a category header"): same threshold-disambiguation
 * gesture, `drag.kind = 'category'` instead of `'entry'` so updateDrag()/
 * finishDrag() route to the category-shaped geometry/commit. Below the
 * drag threshold, pointerup resolves to the header's tap behavior (toggle
 * collapse + selectCategory) — see buildCategoryHeaderRow()'s doc.
 */
function onCategoryPointerDown(state, event, category) {
  if (event.button !== 0) return // primary button/touch only
  if (isPinned(state)) return // M3: no category tap/drag while pinned (headers aren't rendered then; belt-and-braces)
  cancelDeleteConfirm(state)

  // v0.68.1: note the collapse state as of the FIRST tap of a would-be
  // double-click pair -- the header's dblclick handler restores it. A second
  // pointerdown on the same header inside CATEGORY_DBLCLICK_WINDOW_MS keeps
  // the first tap's note; anything else starts a fresh one.
  const now = Date.now()
  const prior = state.categoryTapMemo
  if (!prior || prior.category !== category || now - prior.at > CATEGORY_DBLCLICK_WINDOW_MS) {
    state.categoryTapMemo = { category, collapsed: state.collapsedCategories.has(category), at: now }
  }

  const drag = {
    kind: 'category',
    pointerId: event.pointerId,
    category,
    startX: event.clientX,
    startY: event.clientY,
    active: false,
    rowEl: event.currentTarget,
    marker: null,
    target: null
  }
  state.drag = drag

  const onMove = (moveEvent) => {
    if (moveEvent.pointerId !== drag.pointerId) return
    if (!drag.active) {
      if (state.busy) return // don't start reordering mid-save/delete/move
      const dx = moveEvent.clientX - drag.startX
      const dy = moveEvent.clientY - drag.startY
      if (Math.hypot(dx, dy) < DRAG_THRESHOLD_PX) return
      beginDrag(state, drag)
    }
    updateDrag(state, drag, moveEvent.clientY)
  }
  const onUp = (upEvent) => {
    if (upEvent.pointerId !== drag.pointerId) return
    detach()
    if (drag.active) {
      finishDrag(state, drag)
    } else {
      toggleCategoryCollapse(state, category)
      selectCategory(state, category).catch((error) => api.warn('select category failed', error))
    }
    state.drag = null
  }
  const onCancel = (cancelEvent) => {
    if (cancelEvent.pointerId !== drag.pointerId) return
    detach()
    if (drag.active) cancelDrag(state, drag)
    state.drag = null
  }
  function detach() {
    window.removeEventListener('pointermove', onMove, { capture: true })
    window.removeEventListener('pointerup', onUp, { capture: true })
    window.removeEventListener('pointercancel', onCancel, { capture: true })
  }
  drag.cleanup = () => {
    detach()
    if (drag.active) cancelDrag(state, drag)
  }

// CAPTURE-phase window listeners, deliberately (2026-07-30, owner's Mac
  // report, reproduced on the rig): under Vue nodes ("New node design") the
  // node's DOM wrapper stops pointer-event propagation on the way UP, so a
  // plain (bubble-phase) window listener never fires and the gesture never
  // commits -- clicks stopped selecting, drags stopped dropping, while
  // element-level listeners (typing, buttons) kept working, which made the
  // panel look half-broken. Capture descends from the window BEFORE any
  // bubble-path stopPropagation can intervene, and nothing above window
  // exists to stop it -- so these fire in BOTH renderers. The matching
  // removeEventListener calls must pass the same capture flag or they
  // silently fail to detach.
  window.addEventListener('pointermove', onMove, { capture: true })
  window.addEventListener('pointerup', onUp, { capture: true })
  window.addEventListener('pointercancel', onCancel, { capture: true })
}

// ---------------------------------------------------------------------------
// Rename via the editor's name field (FORMAT.md §7.2 amendment) — double-
// click a row to focus it; see the file header's "Rename via the editor's
// name field" paragraph for the full writeup, and performSave()/
// performSaveCategory() (below, "Save") for where the actual rename
// request gets sent.
// ---------------------------------------------------------------------------

/** Focuses (+selects) the name field, unless there's nothing loaded into
 * it yet (disabled — see resetEditorDom()/populateEditor()). Still the
 * fallback when an inline rename can't open (see onEntryDoubleClick). */
function focusNameField(state) {
  if (!state.nameFieldEl || state.nameFieldEl.disabled) return
  state.nameFieldEl.focus()
  state.nameFieldEl.select()
}

/**
 * Rename in place (owner ask 2026-07-29: "my expectation is i can either
 * double click a name to rename in place, or if I change the name at the top
 * of the notebook the item becomes savable and I can save with the new
 * name"). Both paths now exist and share one commit function: this one edits
 * AT the row, performSave()/performSaveCategory() commit the name field.
 *
 * History worth keeping: v0.10.0 shipped a double-click inline rename, it was
 * reported not working, and v0.12.0 REMOVED it in favour of the name field
 * rather than root-causing it — so the same request is arriving a second time.
 * This implementation is deliberately not a revival of that code; the two
 * things that made the original fragile are addressed head-on:
 *   - **renderList() destroys rows.** The old editor lived only in the DOM, so
 *     any re-render under it (a poll refresh, a late `selectCategory` fetch, a
 *     collapse toggle, a drag) silently threw away what the user had typed and
 *     put the plain label back — indistinguishable from "renaming doesn't
 *     work". The editor's live text now lives in `state.inlineRename.value`,
 *     and renderList() re-establishes the editor after every rebuild
 *     (restoreInlineRename()), so a rebuild is invisible to the user.
 *   - **The gesture overlaps selection AND drag.** A dblclick only ever
 *     follows two complete click cycles, so the row is already selected by the
 *     time this runs (see the file header) — but a stray in-flight drag object
 *     from the same pointer sequence must not survive, and the row's own
 *     pointerdown handler must not re-arm a drag from inside the input.
 */
function onEntryDoubleClick(state, event, name) {
  event.preventDefault()
  event.stopPropagation()
  if (pinnedRefuse(state)) return // M3: no rename while pinned
  // Belt-and-suspenders: a real drag can't produce a dblclick (a drag
  // commits via finishDrag()/pointerup, never a click), but a stray
  // in-flight drag object from some other pointer sequence should never
  // survive past this point.
  state.drag?.cleanup?.()
  state.drag = null
  if (!beginInlineRename(state, 'entry', name)) focusNameField(state)
}

// ---------------------------------------------------------------------------
// Rename in place (owner ask 2026-07-29) — the row-level editor
// ---------------------------------------------------------------------------

/** The rendered row for an inline-renameable target, or null. */
function inlineRenameRow(state, kind, name) {
  const selector = kind === 'category' ? '.llnb-category' : '.llnb-entry'
  for (const row of state.listEl.querySelectorAll(selector)) {
    if (row.__llnbName === name) return row
  }
  return null
}

/**
 * Opens the inline editor on *name*'s row. Returns false when it can't open
 * (no such row rendered — e.g. a collapsed category's entry), so the caller
 * can fall back to the name field rather than leaving the gesture dead.
 */
function beginInlineRename(state, kind, name) {
  if (state.busy) return false
  if (pinnedRefuse(state)) return false // M3: read-only while pinned
  const row = inlineRenameRow(state, kind, name)
  if (!row) return false
  // Committing the previous editor first (rather than cancelling it) keeps a
  // rename the user has already typed from being lost by moving on.
  if (state.inlineRename && state.inlineRename.name !== name) {
    commitInlineRename(state).catch((error) => api.warn('inline rename failed', error))
  }
  state.inlineRename = { kind, name, value: name }
  mountInlineRename(state, row)
  return true
}

/** Builds the input into *row* and focuses it. Split from
 * beginInlineRename() because renderList() re-mounts onto a FRESH row
 * without restarting the rename. */
function mountInlineRename(state, row) {
  const active = state.inlineRename
  if (!active) return
  const input = el('input', {
    className: 'llnb-inline-rename',
    attrs: { type: 'text', value: active.value, spellcheck: 'false' }
  })
  input.value = active.value
  row.replaceChildren(input)
  row.classList.add('llnb-inline-rename-host')

  input.addEventListener('input', () => {
    if (state.inlineRename) state.inlineRename.value = input.value
  })
  // Every key stops here: the canvas has global shortcuts (Delete removes the
  // selected NODE) and this file's own row handlers listen for Enter/space.
  input.addEventListener('keydown', (event) => {
    event.stopPropagation()
    if (event.key === 'Enter') {
      event.preventDefault()
      commitInlineRename(state).catch((error) => api.warn('inline rename failed', error))
    } else if (event.key === 'Escape') {
      event.preventDefault()
      cancelInlineRename(state)
    }
  })
  // A pointerdown inside the input must not reach the row's own handler,
  // which would start a selection change or arm a drag mid-edit.
  for (const type of ['pointerdown', 'mousedown', 'dblclick', 'click']) {
    input.addEventListener(type, (event) => event.stopPropagation())
  }
  // Clicking away commits, matching the name field's "Save is the commit"
  // feel rather than silently discarding. `relatedTarget === null` (the whole
  // widget losing focus, e.g. the canvas being clicked) counts too.
  input.addEventListener('blur', () => {
    if (!state.inlineRename) return
    commitInlineRename(state).catch((error) => api.warn('inline rename failed', error))
  })

  input.focus()
  input.select()
  state.inlineRename.inputEl = input
}

/** Re-establishes an open editor after renderList() rebuilt the rows —
 * without this, any re-render under the editor would discard what the user
 * had typed (the original v0.10.0 inline rename's core failure). */
function restoreInlineRename(state) {
  const active = state.inlineRename
  if (!active) return
  const row = inlineRenameRow(state, active.kind, active.name)
  if (!row) {
    // The row is gone (collapsed, filtered, or deleted) — nothing to edit.
    state.inlineRename = null
    return
  }
  mountInlineRename(state, row)
}

/** Closes the editor and puts the plain row back. */
function cancelInlineRename(state) {
  if (!state.inlineRename) return
  state.inlineRename = null
  renderList(state)
}

/**
 * Commits the open inline editor. Shares every rule the name field's Save
 * path uses (trim, empty refused, client-side duplicate check, server
 * authoritative, §3.5 conflict UI) — see performSave().
 *
 * Rename-ONLY on purpose: the §5 entry/category routes always rewrite the
 * body, so this sends the target's CURRENT ON-DISK text straight back
 * (fetched here, not taken from the textarea). That matters for two reasons:
 * the row being renamed is not necessarily the one loaded in the editor, so
 * the textarea's contents may belong to a different entry entirely; and even
 * when it is the same one, unsaved body edits must not be silently committed
 * by what the user asked to be a rename.
 */
async function commitInlineRename(state) {
  if (pinnedRefuse(state)) {
    state.inlineRename = null
    renderList(state)
    return
  }
  if (writesBlocked(state)) {
    state.inlineRename = null
    renderList(state)
    showLoadError(state)
    return
  }
  const active = state.inlineRename
  if (!active) return
  if (state.busy) {
    // 2026-07-30 (LAN-latency audit): a bare return here left the editor
    // OPEN in state -- and since renderList() re-mounts an open editor after
    // every rebuild (restoreInlineRename) and mounting ends in
    // focus()+select(), a blur-commit that collided with a busy window (a
    // save/move/delete round trip -- much longer over a LAN) left a zombie
    // editor stealing keyboard focus on every subsequent list render. The
    // typed name can't be committed mid-busy (the §3.5 mtime baseline is in
    // flux), so close the editor and SAY so rather than haunting the panel.
    state.inlineRename = null
    setStatus(state, 'Still saving -- rename again in a moment.')
    renderList(state)
    return
  }
  const { kind, name } = active
  const requested = (active.value || '').trim()
  // Captured BEFORE the editor is closed below: `force` is set only by the
  // 409 Overwrite retry, and reading it back off `state.inlineRename` after
  // that null-out would always be undefined — the retry would re-send the
  // stale base_mtime and 409 forever.
  const force = Boolean(active.force)

  // Close the editor first: every path below either finishes or reports, and
  // leaving a live input over an in-flight request invites a second commit
  // from its own blur.
  state.inlineRename = null

  if (!requested) {
    setStatus(state, kind === 'category' ? 'Enter a name for this category.' : 'Enter a name for this entry.')
    renderList(state)
    return
  }
  if (requested === name) {
    renderList(state)
    return
  }
  const taken =
    kind === 'category'
      ? state.categories.includes(requested)
      : state.entries.some((entry) => entry.name === requested)
  if (taken) {
    const what = kind === 'category' ? 'category' : 'entry'
    setStatus(state, `An ${what} named "${requested}" already exists.`)
    renderList(state)
    return
  }

  // busy=true is released in `finally`, and the status/renderList calls sit
  // INSIDE the try (2026-07-30): with them outside, any throw between
  // busy=true and the request left busy stuck true forever -- which
  // permanently disables Save and silently no-ops every later performSave,
  // with every caller swallowing the evidence via .catch(api.warn).
  state.busy = true
  try {
    updateSaveButtonEnabled(state)
    setStatus(state, 'Renaming…')
    renderList(state)
    const data =
      kind === 'category'
        ? await renameCategoryRequest(state, name, requested, force)
        : await renameEntryRequest(state, name, requested, force)
    state.busy = false
    applyRenameResult(state, kind, name, requested, data)
    setStatus(state, `Renamed to "${requested}".`)
  } catch (error) {
    state.busy = false
    updateSaveButtonEnabled(state)
    if (await recoverFromWriteTimeout(state, error)) {
      // no-op: recoverFromWriteTimeout() already reloaded to check what landed
    } else if (error?.status === 409) {
      // Same §3.5 surface Save/Move use. Overwrite re-runs the rename with
      // the mtime check dropped; the editor is already closed, so the retry
      // carries the name through explicitly rather than re-reading the DOM.
      showConflict(state, 'File changed on disk', {
        onReload: () => reloadNow(state),
        onOverwrite: () => {
          state.inlineRename = { kind, name, value: requested, force: true }
          commitInlineRename(state).catch((err) => api.warn('inline rename failed', err))
        }
      })
    } else {
      api.warn('failed to rename', error)
      setStatus(state, `Rename failed: ${error.message}`)
    }
  } finally {
    // Both branches above already clear busy on their own paths; this is the
    // backstop for a throw INSIDE those handlers (a renderList error, a
    // toast error) so busy can never wedge true -- see the comment above the
    // try.
    if (state.busy) {
      state.busy = false
      updateSaveButtonEnabled(state)
    }
  }
}

/**
 * POSTs an entry rename, writing the entry's own on-disk text back
 * unchanged — see commitInlineRename()'s "Rename-ONLY on purpose". ONE
 * request (finding 2, 2026-08-26 responsiveness round: this used to GET the
 * entry first purely to read back the text it was about to resend right
 * back). `state.entryTextByName[name]` already holds it — the same
 * include_text=1 cache loadEntryText()'s own fast path reads (finding 1) —
 * and `name` here is a ROW being renamed, not necessarily the editor's
 * active entry, so `state.baseMtime` (which only ever describes the ACTIVE
 * entry) would be the wrong mtime source; `state.paintedMtime` is the whole
 * FILE's own mtime as of the last list load, which is what every entry's
 * §3.5 check actually needs to cover the window since that load — same
 * reasoning performSave()'s one-request Save path already established.
 */
async function renameEntryRequest(state, name, renameTo, force) {
  const body = {
    file: state.file,
    name,
    text: state.entryTextByName[name] ?? '',
    rename_to: renameTo
  }
  if (!force && typeof state.paintedMtime === 'number') body.base_mtime = state.paintedMtime
  return api.postJson('/lora_library/notebook/entry', body, { timeoutMs: WRITE_TIMEOUT_MS })
}

/** Category sibling of renameEntryRequest(): the §5 category route's field
 * is `description` rather than `text`, read from the finding-5 cache
 * (`state.categoryDescriptionByName`) instead of a GET. */
async function renameCategoryRequest(state, name, renameTo, force) {
  const body = {
    file: state.file,
    name,
    description: state.categoryDescriptionByName[name] ?? '',
    rename_to: renameTo
  }
  if (!force && typeof state.paintedMtime === 'number') body.base_mtime = state.paintedMtime
  return api.postJson('/lora_library/notebook/category', body, { timeoutMs: WRITE_TIMEOUT_MS })
}

/**
 * Folds a successful rename back into every place the OLD name was held.
 * Deliberately exhaustive — a name lives in more places in this file than is
 * obvious, and a miss leaves the UI pointing at a name the file no longer
 * has: the entry/category lists, the multi-select, the active entry/category,
 * the serialized `entry` widget, the collapse Set (keyed by name), the
 * editor's own name field + its saved baseline, and the mode hint.
 */
function applyRenameResult(state, kind, name, renameTo, data) {
  if (Array.isArray(data?.entries)) state.entries = data.entries
  if (Array.isArray(data?.categories)) state.categories = data.categories
  noteFileMtime(state, data?.mtime)
  // Session cache (file header): the body/description travels with the
  // renamed entry/category (finding 5, 2026-08-26 responsiveness round adds
  // the category half -- see noteCategoryDescription()'s call sites).
  if (kind === 'entry') renameEntryText(state, name, renameTo)
  else renameCategoryDescription(state, name, renameTo)
  syncNotebookCache(state, data)

  if (kind === 'category') {
    // Collapse is tracked by NAME, so the key has to move with the rename or
    // a collapsed category springs open (and vice versa); the persisted
    // property (file header "Collapsed sections persist...") has to move
    // with it too, or the workflow's next save loses the migration.
    if (state.collapsedCategories.delete(name)) {
      state.collapsedCategories.add(renameTo)
      syncCollapsedSectionsProperty(state)
    }
    if (state.activeCategory === name) {
      state.activeCategory = renameTo
      // The persisted `Open category` property has to move with the
      // rename too, same reasoning as the collapsed-sections key just
      // above — otherwise a workflow save right after this rename would
      // point the property at a name the file no longer has.
      syncOpenCategoryProperty(state)
      state.nameFieldEl.value = renameTo
      state.lastSavedName = renameTo
    }
    renderList(state)
  } else {
    const nextSelection = state.selection.map((n) => (n === name ? renameTo : n))
    const nextActive = state.activeName === name ? renameTo : state.activeName
    if (state.activeName === name) {
      state.nameFieldEl.value = renameTo
      state.lastSavedName = renameTo
    }
    // setSelection() re-renders and re-syncs the `entry` widget, so the
    // serialized value never keeps a name the file no longer has.
    setSelection(state, nextSelection, nextActive)
  }
  refreshDirty(state)
  updateSaveButtonEnabled(state)
  updateModeHint(state)
  clearConflict(state)
}

// ---------------------------------------------------------------------------
// Footer: New / Delete buttons <-> inline "new entry name" row
// ---------------------------------------------------------------------------

function renderFooter(state) {
  state.footerEl.replaceChildren()

  if (state.creatingNew) {
    const input = el('input', {
      className: 'llnb-input',
      attrs: { type: 'text', placeholder: 'Entry name… (or #Category name)' }
    })
    const confirmBtn = el('button', {
      className: 'llnb-btn llnb-btn-small',
      text: '✓',
      attrs: { title: 'Create' }
    })
    const cancelBtn = el('button', {
      className: 'llnb-btn llnb-btn-small',
      text: '✕',
      attrs: { title: 'Cancel' }
    })

    const submit = () => {
      confirmNewEntry(state, input.value).catch((error) => api.warn('create entry failed', error))
    }
    input.addEventListener('keydown', (event) => {
      event.stopPropagation()
      if (event.key === 'Enter') {
        event.preventDefault()
        submit()
      } else if (event.key === 'Escape') {
        event.preventDefault()
        closeNewEntryRow(state)
      }
    })
    confirmBtn.addEventListener('click', submit)
    cancelBtn.addEventListener('click', () => closeNewEntryRow(state))

    state.footerEl.append(input, confirmBtn, cancelBtn)
    state.deleteBtn = null
    requestAnimationFrame(() => input.focus())
  } else {
    const newBtn = el('button', {
      className: 'llnb-btn',
      text: '＋ New',
      attrs: { title: 'Add a new prompt entry, or a new category heading, to this file' }
    })
    const deleteBtn = el('button', {
      className: 'llnb-btn',
      text: '🗑 Delete',
      attrs: { title: 'Delete the selected entry from the file. Click twice to confirm.' }
    })
    // Provenance M3: the notebook is read-only while pinned -- both
    // buttons are disabled AND their handlers gate (belt-and-braces), and
    // the title says why so a dead button never reads as "broken".
    if (isPinned(state)) {
      newBtn.disabled = true
      newBtn.title = 'Unavailable while pinned — Unpin (above) to edit the live notebook'
      deleteBtn.title = 'Unavailable while pinned — Unpin (above) to edit the live notebook'
    }
    newBtn.addEventListener('click', () => {
      if (pinnedRefuse(state)) return
      if (writesBlocked(state)) {
        setStatus(state, 'Fix the notebook connection first (Retry above) -- nothing can be added while the file cannot be loaded.')
        showLoadError(state)
        return
      }
      openNewEntryRow(state)
    })
    deleteBtn.addEventListener('click', () => {
      if (pinnedRefuse(state)) return
      if (writesBlocked(state)) {
        showLoadError(state)
        return
      }
      onDeleteClick(state)
    })

    state.footerEl.append(newBtn, deleteBtn)
    state.deleteBtn = deleteBtn
    updateDeleteButtonEnabled(state)
  }
}

function openNewEntryRow(state) {
  if (state.busy || state.creatingNew) return
  if (pinnedRefuse(state)) return // M3
  cancelDeleteConfirm(state)
  state.creatingNew = true
  renderFooter(state)
}

function closeNewEntryRow(state) {
  if (!state.creatingNew) return
  state.creatingNew = false
  renderFooter(state)
}

/** A ＋ New input starting with `#` (after trim) creates a CATEGORY instead
 * of an entry (FORMAT.md §7.2 amendment, owner ask 2026-07-19). */
function isCategoryNameInput(rawName) {
  return (rawName || '').trim().startsWith('#')
}

/** The stored category name: leading `#`s + whitespace stripped. */
function categoryNameFromInput(rawName) {
  return (rawName || '').trim().replace(/^#+\s*/, '').trim()
}

async function confirmNewEntry(state, rawName) {
  if (pinnedRefuse(state)) return // M3
  if (writesBlocked(state)) {
    showLoadError(state)
    return
  }
  if (isCategoryNameInput(rawName)) {
    await confirmNewCategory(state, categoryNameFromInput(rawName))
    return
  }

  const name = (rawName || '').trim()
  if (!name) {
    setStatus(state, 'Enter a name for the new entry.')
    return
  }
  if (state.entries.some((entry) => entry.name === name)) {
    setStatus(state, `An entry named "${name}" already exists.`)
    return
  }

  state.busy = true
  setStatus(state, 'Creating…')
  try {
    const body = { file: state.file, name, text: '' }
    // New-below (FORMAT.md §3.4/§7.2, owner ask 2026-07-19): with an ENTRY
    // active (category mode off), the new one lands directly below it via
    // `after`, same category. Nothing active, or only a category active,
    // keeps the old end-of-file/end-of-category append — see the file
    // header's "New-below" paragraph.
    if (state.activeCategory == null && state.activeName) {
      body.after = state.activeName
    }
    const data = await api.postJson('/lora_library/notebook/entry', body, {
      timeoutMs: WRITE_TIMEOUT_MS
    })
    state.busy = false
    state.entries = Array.isArray(data.entries) ? data.entries : state.entries
    state.exists = true
    noteEntryText(state, name, '') // session cache (file header): created empty
    syncNotebookCache(state, data)
    closeNewEntryRow(state)

    // A new entry is created empty and already known (no need to re-fetch
    // it) — becomes the sole active selection, replacing whatever
    // multi-selection existed before. Also exits category mode (FORMAT.md
    // §7.2 amendment): the newly created entry is what the editor shows now.
    state.activeCategory = null
    syncOpenCategoryProperty(state)
    setSelection(state, [name], name)
    state.textarea.value = ''
    state.lastSavedText = ''
    state.nameFieldEl.value = name
    state.lastSavedName = name
    noteFileMtime(state, data.mtime)
    state.textarea.disabled = false
    state.nameFieldEl.disabled = false
    setDirty(state, false)
    updateModeHint(state)
    setStatus(state, `Created "${name}".`)
  } catch (error) {
    state.busy = false
    if (await recoverFromWriteTimeout(state, error)) return
    api.warn('failed to create notebook entry', error)
    setStatus(state, `Could not create "${name}": ${error.message}`)
  }
}

/**
 * ＋ New with a `#`-prefixed name (FORMAT.md §7.2 amendment): creates a
 * category via the §5 category route instead of an entry. Mirrors
 * confirmNewEntry() above closely, including skipping `base_mtime` (a
 * create is additive, never destructive, so — like confirmNewEntry() — it
 * doesn't defend against a concurrent edit elsewhere). On success the newly
 * created (empty-description) category becomes the active one, entering
 * category mode — the category-mode equivalent of confirmNewEntry()'s
 * "becomes the sole active selection".
 */
async function confirmNewCategory(state, name) {
  if (!name) {
    setStatus(state, 'Enter a name for the new category (after the "#").')
    return
  }
  if (state.categories.includes(name)) {
    setStatus(state, `A category named "${name}" already exists.`)
    return
  }

  state.busy = true
  setStatus(state, 'Creating category…')
  try {
    const data = await api.postJson(
      '/lora_library/notebook/category',
      { file: state.file, name, description: '' },
      { timeoutMs: WRITE_TIMEOUT_MS }
    )
    state.busy = false
    state.entries = Array.isArray(data.entries) ? data.entries : state.entries
    state.categories = Array.isArray(data.categories) ? data.categories : state.categories
    state.exists = true
    noteCategoryDescription(state, name, '') // session cache (file header): created empty
    syncNotebookCache(state, data)
    closeNewEntryRow(state)

    // A new category is created with an empty description and already
    // known (no need to re-fetch it) — enters category mode for it,
    // untouched entry selection and all (see the file header).
    state.activeCategory = name
    syncOpenCategoryProperty(state)
    renderList(state)
    updateDeleteButtonEnabled(state)
    populateEditor(state, '', data.mtime, name)
    updateModeHint(state)
    setStatus(state, `Created category "${name}".`)
  } catch (error) {
    state.busy = false
    if (await recoverFromWriteTimeout(state, error)) return
    api.warn('failed to create notebook category', error)
    setStatus(state, `Could not create category "${name}": ${error.message}`)
  }
}

// ---------------------------------------------------------------------------
// Delete (two-step inline confirm)
// ---------------------------------------------------------------------------

function onDeleteClick(state) {
  // Delete is contextual (see updateDeleteButtonEnabled()): category mode
  // owns it whenever it is active — deleting the HEADER, never an entry —
  // the same single branch point performSave() has for performSaveCategory().
  if (pinnedRefuse(state)) return // M3
  if (state.busy) return
  const category = state.activeCategory
  if (category == null && !state.selection.length) return

  if (!state.deleteConfirmActive) {
    state.deleteConfirmActive = true
    if (state.deleteBtn) {
      state.deleteBtn.textContent =
        category != null ? 'Are you sure?' : deleteConfirmLabel(state.selection.length)
      state.deleteBtn.classList.add('llnb-btn-danger')
    }
    // The button is far too narrow to say what a header delete does to the
    // entries under it, so the status line spells the merge out here —
    // BEFORE the second click, not after it.
    if (category != null) setStatus(state, describePendingCategoryDelete(state, category))
    state.deleteConfirmTimer = setTimeout(() => cancelDeleteConfirm(state), DELETE_CONFIRM_MS)
    return
  }

  cancelDeleteConfirm(state)
  if (category != null) {
    performDeleteCategory(state).catch((error) => api.warn('category delete failed', error))
    return
  }
  performDeleteRun(state, [...state.selection]).catch((error) => api.warn('delete failed', error))
}

/** "Are you sure?" (one entry) or "Are you sure? (3)" (owner amendment
 * 2026-07-18c) — the plain, not-yet-armed button label never changes. */
function deleteConfirmLabel(count) {
  return count > 1 ? `Are you sure? (${count})` : 'Are you sure?'
}

/**
 * What the armed second click is about to do, for the status line in
 * category mode (owner report 2026-08-25). The section ABOVE is predicted
 * from `state.categories` file order — index 0 meaning the implicit
 * uncategorized head region (FORMAT.md §3.1) — purely to word this one
 * sentence; the SERVER's own `merged_into` is what the after-the-fact
 * message reports, so a hand-edited file repeating a heading can never make
 * the promise and the outcome disagree. Exported (with
 * deletedCategoryStatus() below) for
 * tests/test_notebook_delete_category_js.py, same as the collapse helpers.
 */
export function describePendingCategoryDelete(state, category) {
  const held = state.entries.filter((entry) => entry.category === category).length
  if (!held) return `Delete the "${category}" heading? It holds no entries.`
  const index = state.categories.indexOf(category)
  const above = index > 0 ? `"${state.categories[index - 1]}"` : 'the top of the notebook'
  const what = held === 1 ? 'its 1 entry' : `its ${held} entries`
  return `Delete the "${category}" heading? Nothing is deleted with it — ${what} move into ${above}.`
}

/** The after-the-fact half of describePendingCategoryDelete(), worded from
 * the server's `merged_into`/`entries_moved` instead of a prediction. */
export function deletedCategoryStatus(data, category) {
  const moved = typeof data?.entries_moved === 'number' ? data.entries_moved : 0
  const into = typeof data?.merged_into === 'string' ? data.merged_into : ''
  if (!moved) return `Deleted the "${category}" heading.`
  const what = moved === 1 ? 'Its entry' : `Its ${moved} entries`
  const where = into ? `"${into}"` : 'the top of the notebook'
  return `Deleted the "${category}" heading. ${what} moved into ${where}.`
}

function cancelDeleteConfirm(state) {
  if (state.deleteConfirmTimer) {
    clearTimeout(state.deleteConfirmTimer)
    state.deleteConfirmTimer = null
  }
  state.deleteConfirmActive = false
  if (state.deleteBtn) {
    state.deleteBtn.textContent = '🗑 Delete'
    state.deleteBtn.classList.remove('llnb-btn-danger')
  }
}

/**
 * Deletes every name in `names` in ONE §5 batch delete request (finding 4,
 * 2026-08-26 responsiveness round: this used to be N sequential single-name
 * POSTs, one disk write + one mtime bump each; now one disk write total —
 * see routes_notebook.py's post_notebook_delete batch docstring). The
 * store's single-write nature makes a failure ALL-OR-NOTHING: unlike the
 * old sequential run, a 409 or an unknown name means NOTHING was deleted,
 * so there is no "resume at index" left to do — Overwrite just retries the
 * SAME full `names` list with the mtime check skipped.
 *
 * On success, every deleted name is dropped from the selection at once
 * using the exact rule this file always used for a single delete ("Delete
 * acts on the ACTIVE entry only": hand `active` to the last other
 * still-selected name, or clear it if none remain) — see the file header's
 * "Multi-delete" paragraph.
 */
async function performDeleteRun(state, names, { force = false } = {}) {
  state.busy = true
  updateSaveButtonEnabled(state)
  updateDeleteButtonEnabled(state)
  setStatus(state, names.length > 1 ? `Deleting ${names.length} entries…` : 'Deleting…')

  let data
  try {
    const body = { file: state.file, names }
    if (!force && typeof state.baseMtime === 'number') body.base_mtime = state.baseMtime
    data = await api.postJson('/lora_library/notebook/delete', body, {
      timeoutMs: WRITE_TIMEOUT_MS
    })
  } catch (error) {
    state.busy = false
    updateSaveButtonEnabled(state)
    updateDeleteButtonEnabled(state)
    if (await recoverFromWriteTimeout(state, error)) return
    if (error?.status === 409) {
      showConflict(state, 'File changed on disk', {
        onReload: () => reloadNow(state),
        onOverwrite: () => performDeleteRun(state, names, { force: true })
      })
    } else {
      api.warn('failed to delete notebook entries', error)
      setStatus(state, `Delete failed: ${error.message}`)
    }
    return
  }

  state.entries = Array.isArray(data.entries) ? data.entries : state.entries
  noteFileMtime(state, data.mtime)
  for (const name of names) forgetEntryText(state, name) // session cache (file header)
  syncNotebookCache(state, data)

  const deleted = new Set(names)
  const previousActive = state.activeName
  const nextSelection = state.selection.filter((n) => !deleted.has(n))
  const nextActive = deleted.has(previousActive) ? lastOrNull(nextSelection) : previousActive
  setSelection(state, nextSelection, nextActive)

  if (nextActive !== previousActive) {
    if (nextActive == null) {
      resetEditorDom(state)
    } else {
      const result = await loadEntryText(state, nextActive)
      if (result === 'failed') resetEditorDom(state)
    }
  }

  state.busy = false
  updateDeleteButtonEnabled(state)
  setStatus(state, names.length > 1 ? `Deleted ${names.length} entries.` : 'Deleted.')
}

/**
 * Deletes the ACTIVE CATEGORY's heading over §5's ``delete_category`` route
 * (owner report 2026-08-25, "you can't delete section headers"): the one
 * heading line goes and its contents merge into the block above — no entry
 * is ever removed by this path, which is why it needs none of
 * performDeleteRun()'s per-name sequencing and takes the shape of a single
 * write like performSaveCategory() instead.
 *
 * A 409 shows the same Reload/Overwrite UI every other write here uses;
 * Overwrite re-enters with `force: true` (that request skips `base_mtime`).
 * On success the panel LEAVES category mode: the deleted header cannot stay
 * selected, so the editor falls back to whatever entry was selected
 * underneath all along (category mode never touched `state.selection` —
 * file header) via loadActiveEditor(), or clears when there is none. The
 * collapse key goes with the header, persisted property included, or a
 * later category reusing that name would come back mysteriously collapsed.
 */
async function performDeleteCategory(state, { force = false } = {}) {
  const category = state.activeCategory
  if (!category || state.busy) return

  state.busy = true
  updateSaveButtonEnabled(state)
  updateDeleteButtonEnabled(state)
  setStatus(state, 'Deleting…')

  let data
  try {
    const body = { file: state.file, name: category }
    if (!force && typeof state.baseMtime === 'number') body.base_mtime = state.baseMtime
    data = await api.postJson('/lora_library/notebook/delete_category', body, {
      timeoutMs: WRITE_TIMEOUT_MS
    })
  } catch (error) {
    state.busy = false
    updateSaveButtonEnabled(state)
    updateDeleteButtonEnabled(state)
    if (await recoverFromWriteTimeout(state, error)) {
      // handled: reloaded to check what landed
    } else if (error?.status === 409) {
      showConflict(state, 'File changed on disk', {
        onReload: () => reloadNow(state),
        onOverwrite: () => performDeleteCategory(state, { force: true })
      })
    } else {
      api.warn('failed to delete notebook category', error)
      setStatus(state, `Delete failed: ${error.message}`)
    }
    return
  }

  state.busy = false
  // Disk truth first, unconditionally -- performSave()'s 2026-07-30 rule.
  noteFileMtime(state, data.mtime)
  state.entries = Array.isArray(data.entries) ? data.entries : state.entries
  state.categories = Array.isArray(data.categories) ? data.categories : state.categories
  forgetCategoryDescription(state, category) // session cache (file header): the heading is gone
  syncNotebookCache(state, data)
  // The heading is gone -- any unsaved draft for it (v0.87.4) would never
  // be shown again (nothing can re-enter category mode for a name that no
  // longer exists), so it would otherwise linger in the `drafts` widget
  // forever. Same cleanup performSaveCategory() does on success, here for
  // the "deleted instead of saved" outcome.
  clearDraft(state, categoryDraftKey(category))

  if (state.collapsedCategories.delete(category)) syncCollapsedSectionsProperty(state)

  state.activeCategory = null
  renderList(state)
  syncOpenCategoryProperty(state)
  updateSaveButtonEnabled(state)
  updateDeleteButtonEnabled(state)
  updateSelectionHint(state)
  updateModeHint(state)
  await loadActiveEditor(state)
  setStatus(state, deletedCategoryStatus(data, category))
}

// ---------------------------------------------------------------------------
// Save
// ---------------------------------------------------------------------------

/**
 * Status-line suffix for the §3.4 demote-don't-refuse transform (v0.48.1):
 * the server DEMOTES pasted `#`/`##` heading lines two levels (`# X` ->
 * `### X`) instead of failing the save -- the owner's "can't save when
 * content has # in the body" report, hit constantly when pasting LLM
 * output. Empty string for the ordinary zero-adjustment save so the
 * everyday "Saved." message stays untouched.
 */
function adjustedHeadingsSuffix(data) {
  const n = typeof data?.adjusted_headings === 'number' ? data.adjusted_headings : 0
  if (n <= 0) return ''
  const noun = n === 1 ? '1 pasted heading line' : `${n} pasted heading lines`
  return ` ${noun} demoted (# → ###) so they stay inside this entry.`
}

async function performSave(state, { force = false } = {}) {
  // The editor is contextual (FORMAT.md §7.2 amendment): category mode owns
  // Save whenever it's active, entirely independent of `activeName` (which
  // may still name an entry underneath — see the file header). This is the
  // ONE branch point between the two; everything else about category-mode
  // saving lives in performSaveCategory() below.
  if (pinnedRefuse(state)) return // M3: Save (button, Enter in the name field) is inert while pinned
  if (writesBlocked(state)) {
    // §7.2 error-state gate: a save during a broken connection would post
    // against a file the server cannot read (or, pre-2026-08-03, against
    // file:null = the DEFAULT notebook). Refuse loudly instead.
    showLoadError(state)
    return
  }
  if (state.activeCategory != null) {
    await performSaveCategory(state, { force })
    return
  }
  if (!state.activeName || state.busy) return

  const name = state.activeName
  const text = state.textarea.value
  // Rename via the editor's name field (FORMAT.md §7.2 amendment): Save
  // commits a rename in the SAME request whenever the name field's value
  // differs from the active entry's current name — client-side duplicate
  // check first, server authoritative.
  const requestedName = currentNameFieldValue(state)
  if (!requestedName) {
    setStatus(state, 'Enter a name for this entry.')
    return
  }
  let renameTo = null
  if (requestedName !== name) {
    if (state.entries.some((entry) => entry.name === requestedName)) {
      setStatus(state, `An entry named "${requestedName}" already exists.`)
      return
    }
    renameTo = requestedName
  }

  state.busy = true
  updateSaveButtonEnabled(state)
  setStatus(state, 'Saving…')
  try {
    const body = { file: state.file, name, text }
    if (renameTo) body.rename_to = renameTo
    if (!force && typeof state.baseMtime === 'number') body.base_mtime = state.baseMtime

    const data = await api.postJson('/lora_library/notebook/entry', body, {
      timeoutMs: WRITE_TIMEOUT_MS
    })
    state.busy = false
    // The DISK-truth parts of the response are folded in UNCONDITIONALLY --
    // before the selection-moved-on early return below (2026-07-30, LAN-
    // latency audit): the file HAS changed on the server whether or not the
    // user clicked elsewhere during the round trip, and skipping this left
    // the list showing pre-rename names (a later save/delete addressed at
    // the old name would then re-CREATE it -- the server's upsert treats an
    // unknown name as a create).
    noteFileMtime(state, data.mtime)
    state.entries = Array.isArray(data.entries) ? data.entries : state.entries
    // Session cache (file header): the body the server STORED, under the
    // committed name, then the fold -- before the moved-on early return,
    // since the file changed either way.
    if (renameTo) forgetEntryText(state, name)
    noteEntryText(state, renameTo || name, typeof data.text === 'string' ? data.text : text)
    syncNotebookCache(state, data)
    // Unsaved-edit drafts (v0.86.0): the save landed under `name` (whatever
    // was active when Save was clicked) -- clear its draft unconditionally,
    // ahead of the "selection moved on" branch below, since that branch
    // returns early and never reaches refreshDirty()'s own clear (which
    // covers the ordinary, still-active-entry continuation for free).
    clearDraft(state, name)
    if (state.activeName !== name) {
      // Selection moved on while the request was in flight. The EDITOR
      // bookkeeping below (field values, lastSaved* baselines) belongs to the
      // now-displayed entry and must not be touched -- but the rename still
      // has to be reflected in the selection + entry widget, or they keep a
      // name the file no longer has.
      if (renameTo) {
        const remapped = state.selection.map((n) => (n === name ? renameTo : n))
        setSelection(state, remapped, state.activeName)
      } else {
        renderList(state)
      }
      updateSaveButtonEnabled(state)
      return
    }
    // §3.4 demote-don't-refuse (v0.48.1): when the server demoted pasted
    // heading lines, `data.text` carries the STORED body. Fold it into the
    // editor -- but never over TYPING that happened while the save was in
    // flight (populateEditor's mid-edit rule): the textarea is replaced
    // only while it still shows exactly what was sent. The baseline below
    // becomes disk truth EITHER way, so mid-flight typing stays flagged
    // dirty (vs the sent-value baseline it would otherwise match).
    const storedText = typeof data.text === 'string' ? data.text : text
    if (storedText !== text && state.textarea.value === text) {
      state.textarea.value = storedText
    }
    state.lastSavedText = storedText
    if (renameTo) {
      const nextSelection = state.selection.map((n) => (n === name ? renameTo : n))
      setSelection(state, nextSelection, renameTo) // also re-renders the list
      // Show the committed name -- but never over TYPING that happened while
      // the save was in flight (the same mid-edit rule populateEditor
      // documents; on a LAN the flight is long enough to type in).
      if (currentNameFieldValue(state) === renameTo || currentNameFieldValue(state) === name) {
        state.nameFieldEl.value = renameTo
      }
    } else {
      renderList(state)
    }
    // Baseline = what was SENT, never the live field (2026-07-30, owner's
    // Mac report): reading the DOM at RESPONSE time absorbed anything typed
    // during the round trip into the "saved" baseline, so refreshDirty saw
    // nameChanged=false and Save went (and stayed) grey with unsaved typing
    // sitting right there in the field -- his "changing the title text does
    // not enable the save button", amplified by LAN latency. With the sent
    // value as baseline, refreshDirty below re-flags mid-flight typing.
    // (Since v0.48.1 "what was sent" is refined to "what the server STORED
    // for what was sent" -- identical except when headings were demoted.)
    state.lastSavedName = renameTo || name
    refreshDirty(state)
    updateModeHint(state)
    setStatus(
      state,
      (renameTo ? `Saved. Renamed to "${renameTo}".` : 'Saved.') + adjustedHeadingsSuffix(data)
    )
  } catch (error) {
    state.busy = false
    updateSaveButtonEnabled(state)
    if (await recoverFromWriteTimeout(state, error)) {
      // handled: reloaded to check what landed
    } else if (error?.status === 409) {
      showConflict(state, 'File changed on disk', {
        onReload: () => reloadNow(state),
        onOverwrite: () => performSave(state, { force: true })
      })
    } else {
      api.warn('failed to save notebook entry', error)
      setStatus(state, `Save failed: ${error.message}`)
    }
  }
}

/**
 * Category-mode sibling of performSave() above (FORMAT.md §7.2 amendment):
 * saves `state.activeCategory`'s description through the §5 category
 * route, sharing the same textarea/dirty/baseMtime/busy/conflict-UI
 * machinery entry-saving already used — only the endpoint and the field
 * name (`description` vs. `text`) differ. Always the "known name" branch of
 * that route: `state.activeCategory` only ever holds a category that's
 * either already in `state.categories` (clicked from the rendered list) or
 * was just created by confirmNewCategory(), so it never hits the create
 * branch here.
 */
async function performSaveCategory(state, { force = false } = {}) {
  const name = state.activeCategory
  if (!name || state.busy) return

  const description = state.textarea.value
  // Rename via the editor's name field (FORMAT.md §7.2 amendment) — same
  // one-request rule as performSave() above, against `state.categories`
  // instead of `state.entries`.
  const requestedName = currentNameFieldValue(state)
  if (!requestedName) {
    setStatus(state, 'Enter a name for this category.')
    return
  }
  let renameTo = null
  if (requestedName !== name) {
    if (state.categories.includes(requestedName)) {
      setStatus(state, `A category named "${requestedName}" already exists.`)
      return
    }
    renameTo = requestedName
  }

  state.busy = true
  updateSaveButtonEnabled(state)
  setStatus(state, 'Saving…')
  try {
    const body = { file: state.file, name, description }
    if (renameTo) body.rename_to = renameTo
    if (!force && typeof state.baseMtime === 'number') body.base_mtime = state.baseMtime

    const data = await api.postJson('/lora_library/notebook/category', body, {
      timeoutMs: WRITE_TIMEOUT_MS
    })
    state.busy = false
    // Disk truth first, unconditionally -- performSave()'s 2026-07-30 rule.
    noteFileMtime(state, data.mtime)
    state.entries = Array.isArray(data.entries) ? data.entries : state.entries
    state.categories = Array.isArray(data.categories) ? data.categories : state.categories
    // §3.4 demote-don't-refuse fold, same rules as performSave() above --
    // `data.description` is the STORED (demoted, trimmed) text when any
    // heading line was adjusted. Computed here (ahead of the moved-on early
    // return below) since the description cache (finding 5, 2026-08-26
    // responsiveness round) has to stay current either way -- a later
    // category click reads it whether or not THIS category is still active.
    const storedDescription = typeof data.description === 'string' ? data.description : description
    if (renameTo) renameCategoryDescription(state, name, renameTo)
    noteCategoryDescription(state, renameTo || name, storedDescription)
    syncNotebookCache(state, data) // session cache (file header)
    // Unsaved-edit drafts (v0.87.4): the save landed under `name` (whatever
    // was active when Save was clicked) -- clear its draft unconditionally,
    // same rule/placement as performSave()'s entry-mode clearDraft() above.
    clearDraft(state, categoryDraftKey(name))
    if (renameTo && state.collapsedCategories.delete(name)) {
      // Collapse tracks by NAME -- migrate the key whether or not the user
      // moved on mid-flight, or the renamed category springs open. The
      // persisted property has to move with it too (file header "Collapsed
      // sections persist...").
      state.collapsedCategories.add(renameTo)
      syncCollapsedSectionsProperty(state)
    }
    if (state.activeCategory !== name) {
      renderList(state)
      updateSaveButtonEnabled(state)
      return
    }
    if (storedDescription !== description && state.textarea.value === description) {
      state.textarea.value = storedDescription
    }
    state.lastSavedText = storedDescription
    if (renameTo) {
      state.activeCategory = renameTo
      // The persisted `Open category` property has to move with the
      // rename too — same reasoning as applyRenameResult()'s category
      // branch, this route's sibling for a rename typed straight into the
      // editor's own name field.
      syncOpenCategoryProperty(state)
      // Same mid-edit rule as performSave(): never over in-flight typing.
      if (currentNameFieldValue(state) === renameTo || currentNameFieldValue(state) === name) {
        state.nameFieldEl.value = renameTo
      }
    }
    // Baseline = what was SENT, never the live field (see performSave()).
    state.lastSavedName = renameTo || name
    refreshDirty(state)
    renderList(state)
    updateModeHint(state)
    setStatus(
      state,
      (renameTo ? `Saved. Renamed to "${renameTo}".` : 'Saved.') + adjustedHeadingsSuffix(data)
    )
  } catch (error) {
    state.busy = false
    updateSaveButtonEnabled(state)
    if (await recoverFromWriteTimeout(state, error)) {
      // handled: reloaded to check what landed
    } else if (error?.status === 409) {
      showConflict(state, 'File changed on disk', {
        onReload: () => reloadNow(state),
        onOverwrite: () => performSaveCategory(state, { force: true })
      })
    } else {
      api.warn('failed to save category description', error)
      setStatus(state, `Save failed: ${error.message}`)
    }
  }
}

// ---------------------------------------------------------------------------
// Dirty / button enablement
// ---------------------------------------------------------------------------

function setDirty(state, value) {
  state.dirty = value
  updateSaveButtonEnabled(state)
}

/** Trimmed current value of the editor's name field (FORMAT.md §7.2
 * amendment) — shared by dirty-tracking and Save's rename detection. */
function currentNameFieldValue(state) {
  return (state.nameFieldEl.value || '').trim()
}

/** Recomputes `state.dirty` from BOTH the textarea (body/description) and
 * the name field — Save now commits whichever of the two changed, in one
 * request (performSave()/performSaveCategory()), so either one alone must
 * enable it. Called from both fields' `input` listeners (buildUi()).
 *
 * Unsaved-edit drafts (v0.86.0): also the ONE place that clears a draft the
 * instant the textarea matches disk again -- a successful Save (the
 * baseline becomes what was stored, so this fires right after), a Reload/
 * discard (populateEditor() repaints the disk text, same reasoning), or
 * simply typing back to the original all funnel through here. This never
 * WRITES a new/changed draft -- that stays the debounced path
 * (scheduleDraftSync(), armed only by the textarea's own `input` listener)
 * so a keystroke alone never triggers a widget write. Category mode gets
 * the identical clear, in its own branch below, over its own
 * `categoryDraftKey()` keyspace (v0.87.4) -- a description IS a
 * draft-worthy edit now, just never a run's prompt text. Pinned mode never
 * lets typing reach here in the first place (readOnly). */
function refreshDirty(state) {
  const textChanged = state.textarea.value !== state.lastSavedText
  const nameChanged = currentNameFieldValue(state) !== state.lastSavedName
  setDirty(state, textChanged || nameChanged)
  if (!textChanged && !isPinned(state) && state.activeCategory == null && state.activeName) {
    if (state.draftSyncTimer) {
      clearTimeout(state.draftSyncTimer)
      state.draftSyncTimer = null
    }
    clearDraft(state, state.activeName)
  }
  if (!textChanged && !isPinned(state) && state.activeCategory != null) {
    if (state.draftSyncTimer) {
      clearTimeout(state.draftSyncTimer)
      state.draftSyncTimer = null
    }
    clearDraft(state, categoryDraftKey(state.activeCategory))
  }
}

function updateSaveButtonEnabled(state) {
  if (!state.saveBtn) return
  // FORMAT.md §7.2 amendment: Save targets whichever of the two contextual
  // modes is active (category mode or entry mode — see performSave()).
  const hasTarget = state.activeCategory != null || Boolean(state.activeName)
  // M3: nothing can be saved while pinned (the editor shows the pinned text).
  state.saveBtn.disabled = state.busy || !hasTarget || !state.dirty || isPinned(state)
}

function updateDeleteButtonEnabled(state) {
  if (!state.deleteBtn) return
  // Delete is CONTEXTUAL, exactly like Save (owner report 2026-08-25, "you
  // can't delete section headers"): in category mode it deletes the active
  // HEADER, so it enables on that alone and ignores the entry selection
  // underneath — which category mode never touches — while outside it the
  // entry rule is the one this button always had. Disabled outright while
  // pinned (M3) or busy either way.
  const categoryMode = state.activeCategory != null
  state.deleteBtn.disabled =
    state.busy || isPinned(state) || (!categoryMode && state.selection.length === 0)
  if (isPinned(state)) return // the pinned title (set in buildUi) says why
  state.deleteBtn.title = categoryMode
    ? `Delete the "${state.activeCategory}" heading. Its entries move into the section `
      + 'above — nothing is deleted with it. Click twice to confirm.'
    : 'Delete the selected entry from the file. Click twice to confirm.'
}

// ---------------------------------------------------------------------------
// Unsaved-edit drafts (FORMAT.md §6.1, v0.86.0) -- see the file header's
// "Unsaved-edit drafts" paragraph. Nothing here ever shows draft text
// differently in the editor pane itself (it's always just whatever the
// textarea holds) -- this section only ever WRITES/CLEARS the `drafts`
// widget to match, and surfaces the count via updateSelectionHint().
// ---------------------------------------------------------------------------

/**
 * Parse the `drafts` widget's raw value: a JSON object mapping entry name
 * -> unsaved text. `""` / non-string / unparseable / non-object / an array
 * all degrade to `{}` (= no drafts) -- same lenient-degrade shape as
 * parsePinned, mirrored on the PYTHON side by
 * nodes_notebook.parse_drafts (which additionally logs a warning; this
 * side stays silent, matching parsePinned's own convention). A non-string
 * value for a given name is dropped -- that one entry only, never thrown.
 * @param {unknown} raw
 * @returns {Record<string, string>}
 */
export function parseDraftsWidgetValue(raw) {
  if (typeof raw !== 'string' || raw.trim() === '') return {}
  let parsed
  try {
    parsed = JSON.parse(raw)
  } catch {
    return {}
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {}
  const out = {}
  for (const [name, text] of Object.entries(parsed)) {
    if (typeof text === 'string') out[name] = text
  }
  return out
}

/**
 * The unsaved text this panel is holding for *name*, or `null` when there
 * is none (v0.87.3, category descriptions added v0.87.4). `null` rather
 * than `''` on purpose: an empty string is a legitimate draft (the user
 * cleared the box and hasn't saved), and collapsing the two would silently
 * discard exactly that edit.
 *
 * *name* is an ENTRY name when `state.activeCategory` is `null` and a
 * CATEGORY name otherwise — exactly what `populateEditor`'s caller (
 * `loadEntryText`/`loadCategoryDescription`) already passes, and exactly
 * what `state.activeCategory` reflects by the time either runs (set
 * synchronously by `selectCategory`/`confirmNewCategory` before the load
 * starts). A category lookup goes through `categoryDraftKey()` -- its own
 * keyspace inside the same `drafts` object, never the entry one -- so it
 * can never read (or be shadowed by) an entry's draft of the same name.
 *
 * Never consulted for a pinned panel: the pin overrides everything shown.
 */
export function draftTextFor(state, name) {
  if (typeof name !== 'string' || !name) return null
  if (isPinned(state)) return null
  const drafts = state.drafts
  if (!drafts) return null
  const key = state.activeCategory != null ? categoryDraftKey(name) : name
  if (!Object.prototype.hasOwnProperty.call(drafts, key)) return null
  const value = drafts[key]
  return typeof value === 'string' ? value : null
}

/** Writes `state.drafts` into the `drafts` widget (JSON) through the
 * widget's real setter + callback -- same idiom as syncEntryWidget -- and
 * refreshes the muted hint (updateSelectionHint), since the unsaved-edit
 * count just changed. No-op on a backend that predates the widget (null),
 * though `state.drafts` itself is still tracked either way so the hint
 * stays honest even without one. */
function syncDraftsWidget(state) {
  const widget = state.draftsWidget
  if (!widget) {
    updateSelectionHint(state)
    return
  }
  const next = JSON.stringify(state.drafts)
  if (widget.value === next) {
    updateSelectionHint(state)
    return
  }
  widget.value = next
  try {
    widget.callback?.(next)
  } catch (error) {
    api.warn('drafts widget callback threw', error)
  }
  state.node.graph?.setDirtyCanvas(true, true)
  updateSelectionHint(state)
}

/** Records *text* as *name*'s unsaved draft. No-op (no widget write, no
 * repaint) when the value is already exactly this -- mirrors
 * syncEntryWidget's equal-value guard. */
function setDraft(state, name, text) {
  if (state.drafts[name] === text) return
  state.drafts = { ...state.drafts, [name]: text }
  syncDraftsWidget(state)
}

/** Removes *name*'s draft, if it has one. No-op otherwise -- callers (the
 * debounce, flush, and prune below) call this unconditionally rather than
 * checking first, so the no-op guard is what keeps that cheap. */
function clearDraft(state, name) {
  if (!(name in state.drafts)) return
  const next = { ...state.drafts }
  delete next[name]
  state.drafts = next
  syncDraftsWidget(state)
}

/**
 * A draft for an entry that is no longer selected must not linger (design
 * law: `drafts` only ever carries what a run would actually use) --
 * called from setSelection(), the ONE choke point every selection change
 * (click, ctrl/shift-click, delete/move reassignment, rename remap, the
 * §7.2 restore/reconcile paths) already goes through. No-op when nothing
 * needs pruning, so calling it unconditionally on every selection change
 * costs nothing extra on the common "nothing to prune" path.
 */
function pruneDraftsToSelection(state) {
  const keep = new Set(state.selection)
  let changed = false
  const next = {}
  for (const [name, text] of Object.entries(state.drafts)) {
    // Category drafts (v0.87.4, CATEGORY_DRAFT_PREFIX) live in a keyspace
    // this ENTRY-selection prune has no business touching -- an entry
    // click/ctrl-click/delete/move must never sweep away an unsaved
    // category description sitting untouched in the same `drafts` object.
    // They are pruned on their own terms elsewhere (save, delete-category).
    if (name.startsWith(CATEGORY_DRAFT_PREFIX) || keep.has(name)) next[name] = text
    else changed = true
  }
  if (changed) {
    state.drafts = next
    syncDraftsWidget(state)
  }
}

/** Writes (or clears) the ACTIVE item's draft to match the textarea's
 * CURRENT value against the CURRENT `lastSavedText` baseline -- the entry
 * body when `state.activeCategory` is `null`, the category description
 * otherwise (v0.87.4, same owner report as the entry fix extended to
 * category mode). No-op without an active entry/category, or while pinned
 * (typing can't reach here then -- the textarea is `readOnly` -- but a
 * timer armed just before a pin arrived could otherwise still fire).
 * Called from the debounce timer (scheduleDraftSync) and from
 * flushDraftSync (the immediate, no-wait variant used right before the
 * editor pane shows something else). Name kept as-is (not renamed to
 * "ActiveItem") -- this is still the ONE function both call sites and
 * every doc reference already point at; only its body grew a second
 * branch. */
function commitDraftForActiveEntry(state) {
  if (isPinned(state)) return
  if (state.activeCategory != null) {
    const key = categoryDraftKey(state.activeCategory)
    if (state.textarea.value !== state.lastSavedText) {
      setDraft(state, key, state.textarea.value)
    } else {
      clearDraft(state, key)
    }
    return
  }
  if (!state.activeName) return
  if (state.textarea.value !== state.lastSavedText) {
    setDraft(state, state.activeName, state.textarea.value)
  } else {
    clearDraft(state, state.activeName)
  }
}

/** Debounced write path (owner ask 2026-08-28: "if you change a prompt
 * that is selected and run it without saving, it should run the changed
 * prompt") -- schedules commitDraftForActiveEntry() instead of running it
 * per keystroke, same idiom as scheduleSearchRender()/
 * onFileWidgetChanged(). Armed ONLY by the textarea's `input` listener;
 * flushDraftSync() below is the immediate variant used everywhere else a
 * pending write needs to land right away. */
function scheduleDraftSync(state) {
  if (state.draftSyncTimer) clearTimeout(state.draftSyncTimer)
  state.draftSyncTimer = setTimeout(() => {
    state.draftSyncTimer = null
    commitDraftForActiveEntry(state)
  }, DRAFT_SYNC_DEBOUNCE_MS)
}

/**
 * Commits any pending debounced draft-sync AT ONCE, using whatever the
 * textarea/`state.activeName` hold RIGHT NOW -- called from
 * populateEditor() right before it replaces the editor pane's content, so
 * a fast "type, then click a different row/category" never loses the edit
 * to a timer that would otherwise fire later against whatever became
 * active BY THEN (the timer reads `state.activeName` fresh at fire time,
 * which a switch changes out from under it). No-op absent a pending timer
 * -- in which case there is nothing to flush; the debounce already landed,
 * or nothing was ever dirty.
 */
function flushDraftSync(state) {
  if (!state.draftSyncTimer) return
  clearTimeout(state.draftSyncTimer)
  state.draftSyncTimer = null
  commitDraftForActiveEntry(state)
}

/** Both hide flags, exactly hideFileWidget()'s pair (§7.5): canvas reads
 * `widget.hidden`, Vue nodes read `options.hidden`. No-op without the
 * widget (a backend that predates v0.86.0). */
function hideDraftsWidget(state) {
  const widget = state.draftsWidget
  if (!widget) return
  widget.hidden = true
  widget.options = { ...(widget.options || {}), hidden: true }
  state.node.graph?.setDirtyCanvas(true, true)
}

/**
 * Reconcile `state.drafts` with the `drafts` widget's CURRENT value --
 * the same "configure() assigns widget.value directly, no callback" reason
 * `pinned` needs syncPinnedFromWidget (wireConfigureReload calls this right
 * alongside it), so a workflow saved mid-audition restores its unsaved-
 * edit hint without a click. Deliberately does NOT prune against
 * `state.selection` here (unlike every other write path above) -- at this
 * exact point in a restore the selection may not have caught up yet, and
 * trusting what was serialized (which was written FROM a consistent
 * selection) is safer than risking a wrongful prune; the ordinary
 * setSelection()-driven prune takes over the moment the user changes
 * anything. No-op on a backend that predates the widget. */
function syncDraftsFromWidget(state) {
  const widget = state.draftsWidget
  if (!widget) return
  state.drafts = parseDraftsWidgetValue(widget.value)
  updateSelectionHint(state)
  restoreDraftIntoEditor(state)
}

/**
 * Put the active entry's (or, v0.87.4, active category's) draft back into
 * the textarea (v0.87.3, the second half of the tab-switch fix).
 *
 * `populateEditor` consulting `draftTextFor` is not enough on its own: on
 * a restore, `reloadNow`'s INSTANT CACHED PAINT can reach the editor
 * before `syncDraftsFromWidget` has parsed the widget, so it paints the
 * file's text while `state.drafts` is still empty. Re-applying here covers
 * that order; `populateEditor` covers the other (a network reload landing
 * after the sync). Both are idempotent, so whichever runs second is a
 * no-op.
 *
 * `state.activeCategory` wins when both happen to be set, mirroring
 * `updateModeHint()`'s own rule (category mode is what the editor is
 * actually showing) -- `draftTextFor()` needs the same name either way to
 * pick the right keyspace.
 *
 * Never fights a live cursor: if the user is typing in the textarea right
 * now, their keystrokes are the truth and the debounced sync will catch
 * up.
 */
function restoreDraftIntoEditor(state) {
  const name = state.activeCategory != null ? state.activeCategory : state.activeName
  if (!name) return // selection not restored yet -- populateEditor covers it
  const draft = draftTextFor(state, name)
  if (draft === null) return
  if (document.activeElement === state.textarea) return
  if (state.textarea.value === draft) return
  state.textarea.value = draft
  refreshDirty(state)
}

// ---------------------------------------------------------------------------
// Pinned values (FORMAT.md §6.1/§7.2, provenance M3) -- see the file header's
// "Pinned values" paragraph. Pure helpers first (exported for
// tests/test_m3_pinning_js.py), then the panel wiring. Nothing here ever
// CREATES a pin: it arrives through configure() from a baked image's
// workflow; this code shows it, compares it, and clears it.
// ---------------------------------------------------------------------------

/**
 * Parse the `pinned` widget's raw value. `""` / non-string / unparseable /
 * any shape without at least one `{name}` entry -> null (= live); otherwise
 * `{format, entries: [{name, text}], source: {file, token, captured}}` with
 * every field coerced to a string (missing -> ''). Lenient on purpose: the
 * pin is the BACKEND's mechanism and this panel's job is to SHOW it -- so an
 * unknown `format` still renders (`entries` is the stable core); only a
 * value that cannot name a single entry reads as "no pin".
 * @param {unknown} raw
 * @returns {{format: number|null, entries: Array<{name: string, text: string}>, source: {file: string, token: string, captured: string}}|null}
 */
export function parsePinned(raw) {
  if (typeof raw !== 'string' || raw.trim() === '') return null
  let parsed
  try {
    parsed = JSON.parse(raw)
  } catch {
    return null
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null
  if (!Array.isArray(parsed.entries)) return null
  const entries = []
  for (const item of parsed.entries) {
    if (!item || typeof item !== 'object' || typeof item.name !== 'string') continue
    entries.push({
      name: item.name,
      text: typeof item.text === 'string' ? item.text : String(item.text ?? '')
    })
  }
  if (!entries.length) return null
  const source = parsed.source && typeof parsed.source === 'object' ? parsed.source : {}
  const str = (value) => (value == null ? '' : String(value))
  return {
    format: typeof parsed.format === 'number' ? parsed.format : null,
    entries,
    source: { file: str(source.file), token: str(source.token), captured: str(source.captured) }
  }
}

/** Line-ending / trailing-whitespace-insensitive text for the drift compare
 * (a CRLF notebook vs the LF the pin was captured with is not drift). */
function normalizePinnedText(text) {
  return String(text ?? '')
    .replace(/\r\n?/g, '\n')
    .replace(/[ \t]+$/gm, '')
    .trimEnd()
}

/**
 * Compare a parsed pin against the live library. `entryTextByName` is the
 * panel's include_text map (name -> body); `libraryLoaded` false means
 * there is nothing to compare against yet (pre-load, or a failed load) and
 * every row is `unknown`. Returns `{status, rows}`: status `match` when
 * every pinned entry still exists with identical text, `differs` when any
 * text differs OR any name is gone ("not in the library anymore"),
 * `unknown` when the library isn't loaded; `rows` carry
 * `{name, kind: 'same'|'differs'|'missing'|'unknown', current}` in pin
 * order (`current` = the live text, or null).
 * @param {ReturnType<typeof parsePinned>} pin
 * @param {Record<string, string>} entryTextByName
 * @param {boolean} [libraryLoaded]
 */
export function pinnedDrift(pin, entryTextByName, libraryLoaded = true) {
  const rows = []
  let status = libraryLoaded ? 'match' : 'unknown'
  const map = entryTextByName && typeof entryTextByName === 'object' ? entryTextByName : {}
  for (const entry of pin?.entries ?? []) {
    if (!libraryLoaded) {
      rows.push({ name: entry.name, kind: 'unknown', current: null })
      continue
    }
    if (!Object.prototype.hasOwnProperty.call(map, entry.name)) {
      rows.push({ name: entry.name, kind: 'missing', current: null })
      status = 'differs'
      continue
    }
    const current = typeof map[entry.name] === 'string' ? map[entry.name] : ''
    if (normalizePinnedText(current) !== normalizePinnedText(entry.text)) {
      rows.push({ name: entry.name, kind: 'differs', current })
      status = 'differs'
    } else {
      rows.push({ name: entry.name, kind: 'same', current })
    }
  }
  return { status, rows }
}

/** The badge row's text for a pin + drift status. The three variants are a
 * contract with tests/test_m3_pinning_js.py (and README/FORMAT §6.1). */
export function pinnedBadgeText(pin, status) {
  const token = pin?.source?.token
  const origin = token ? `captured from image ${token}` : 'captured from a saved image'
  const drift =
    status === 'differs'
      ? 'differs from current library'
      : status === 'match'
        ? 'matches library'
        : 'library not loaded'
  return `📌 Pinned — ${origin} — ${drift}`
}

/** First non-blank line of *text* (a drift marker's tooltip), capped. */
function firstLineOf(text, max = 80) {
  const line =
    String(text ?? '')
      .replace(/\r\n?/g, '\n')
      .split('\n')
      .find((candidate) => candidate.trim() !== '') ?? ''
  return line.length > max ? `${line.slice(0, max - 1)}…` : line
}

function isPinned(state) {
  return !!state?.pinned
}

/** The pinned entry the editor pane shows: the clicked one, else the first. */
function pinnedActiveEntry(state) {
  if (!isPinned(state)) return null
  const entries = state.pinned.entries
  return entries.find((entry) => entry.name === state.pinnedActive) ?? entries[0] ?? null
}

/** Both hide flags, exactly hideFileWidget()'s pair: canvas reads
 * `widget.hidden`, Vue nodes read `options.hidden` (§7.5). No-op without
 * the widget (a backend that predates M3). */
function hidePinnedWidget(state) {
  const widget = state.pinnedWidget
  if (!widget) return
  widget.hidden = true
  widget.options = { ...(widget.options || {}), hidden: true }
  state.node.graph?.setDirtyCanvas(true, true)
}

/** Chain the `pinned` widget's callback so a value arriving THROUGH the
 * callback (a live link on the widget-input at queue time, an extension,
 * our own unpin) reconciles the view. configure() bypasses callbacks and is
 * covered by wireConfigureReload() instead. Wrapped, never replaced. */
function wirePinnedWidget(state) {
  const widget = state.pinnedWidget
  if (!widget) return
  const original = widget.callback
  widget.callback = function (value, ...rest) {
    let result
    if (typeof original === 'function') {
      try {
        result = original.apply(this, [value, ...rest])
      } catch (error) {
        api.warn('original pinned widget callback threw', error)
      }
    }
    try {
      syncPinnedFromWidget(state)
    } catch (error) {
      api.warn('pinned widget sync threw', error)
    }
    return result
  }
}

/**
 * Reconcile the panel with the `pinned` widget's CURRENT value. Cheap and
 * idempotent (raw-string compare first), so it is safe from every hook that
 * might carry a pin: onConfigure (workflow load / paste / image drop), the
 * widget callback, unpin. Returns true when the pin state changed.
 */
function syncPinnedFromWidget(state) {
  const widget = state.pinnedWidget
  if (!widget) return false
  const raw = typeof widget.value === 'string' ? widget.value : ''
  if (raw === state.pinnedRaw) return false
  const wasPinned = isPinned(state)
  state.pinnedRaw = raw
  state.pinned = parsePinned(raw)
  state.pinnedActive = state.pinned ? state.pinned.entries[0].name : null
  if (state.pinned && !wasPinned) {
    // Entering the pinned view: a mid-flight ＋ New row / delete-confirm /
    // inline rename / drag would target a list that no longer renders.
    cancelDeleteConfirm(state)
    closeNewEntryRow(state)
    state.inlineRename = null
    state.drag?.cleanup?.()
    state.drag = null
  }
  applyPinnedView(state)
  return true
}

/**
 * Repaint everything the pin touches: badge row, list, footer buttons,
 * hints, editor pane, and the node's height (§7.2: the bar's row is given
 * to the node once on pin and taken back on unpin, never below the floor).
 * Leaving the pinned view hands the editor back to the live entry through
 * reloadNow() -- the one path that restores selection text + mtime from
 * the file -- when the panel has loaded before (a fresh, never-loaded node
 * is still waiting on its deferred attach-time load, which lands alone).
 */
function applyPinnedView(state) {
  renderPinBar(state)
  renderList(state)
  renderFooter(state)
  updateDeleteButtonEnabled(state)
  updateSelectionHint(state)
  updateModeHint(state)
  syncPinnedNodeHeight(state)
  if (isPinned(state)) {
    paintPinnedEditor(state)
  } else if (state.file != null) {
    reloadNow(state).catch((error) => api.warn('reload after unpin failed', error))
  }
}

/** Lift the node by the badge row once when a pin appears; give it back on
 * unpin. Never below litegraph's computed floor (getMinHeight already
 * includes the bar while pinned), never touches width. `node.size` is a
 * Float32Array on current frontends -- never Array.isArray it (§7.2). */
function syncPinnedNodeHeight(state) {
  const node = state.node
  const pinned = isPinned(state)
  if (pinned === state.pinGrown) return
  state.pinGrown = pinned
  if (!node?.size || typeof node.setSize !== 'function') return
  const floor = typeof node.computeSize === 'function' ? node.computeSize()[1] : 0
  const delta = pinned ? PIN_BAR_HEIGHT : -PIN_BAR_HEIGHT
  node.setSize([node.size[0], Math.max(node.size[1] + delta, floor)])
  node.graph?.setDirtyCanvas(true, true)
}

/** The badge row: drift-aware text + Unpin. Empties the row when not
 * pinned (`.llnb-pinbar:empty` hides it). Re-run on every load so the
 * verdict tracks the live library. */
function renderPinBar(state) {
  const bar = state.pinBarEl
  if (!bar) return
  if (!isPinned(state)) {
    bar.replaceChildren()
    return
  }
  const pin = state.pinned
  const drift = pinnedDrift(pin, state.entryTextByName, state.file != null && !state.loadError)
  const text = el('span', {
    className:
      'llnb-pinbar-text' +
      (drift.status === 'differs'
        ? ' llnb-pinbar-differs'
        : drift.status === 'unknown'
          ? ' llnb-pinbar-unknown'
          : ''),
    text: pinnedBadgeText(pin, drift.status)
  })
  const details = []
  if (pin.source.captured) details.push(`Captured ${pin.source.captured}`)
  if (pin.source.file) details.push(`from ${pin.source.file}`)
  text.title =
    (details.length ? `${details.join(' ')}.\n` : '') +
    'The node outputs these pinned values (not the live notebook file) until you Unpin.'
  const unpinBtn = el('button', {
    className: 'llnb-btn llnb-btn-unpin',
    text: 'Unpin',
    attrs: { title: 'Go back to the live notebook — the node reads the current file again' }
  })
  unpinBtn.addEventListener('click', () => unpin(state))
  bar.replaceChildren(text, unpinBtn)
}

/** renderList()'s pinned branch: every pinned entry, all reading as
 * selected (the node outputs each one), the active one highlighted; click =
 * show that entry's OLD text in the editor; no drag source, no rename, no
 * modifiers; search still filters the view. Drifted rows carry a "≠"
 * marker whose title shows the current library text's first line, or that
 * the entry is not in the library anymore. */
function renderPinnedList(state) {
  const pin = state.pinned
  const drift = pinnedDrift(pin, state.entryTextByName, state.file != null && !state.loadError)
  const byName = new Map(drift.rows.map((row) => [row.name, row]))
  const searchQuery = (state.searchQuery || '').trim()
  const words = searchQuery ? searchWords(searchQuery) : null
  let shown = 0
  for (const entry of pin.entries) {
    if (words && !entryMatchesSearch(searchHaystack(entry.name, entry.text), words)) continue
    shown += 1
    state.listEl.append(buildPinnedEntryRow(state, entry, byName.get(entry.name)))
  }
  if (!shown) {
    state.listEl.append(
      el('div', {
        className: 'llnb-empty',
        text: searchQuery ? `No pinned prompts match "${searchQuery}".` : 'No pinned entries.'
      })
    )
  }
}

function buildPinnedEntryRow(state, entry, driftRow) {
  const active = entry.name === pinnedActiveEntry(state)?.name
  const classes = ['llnb-entry', 'llnb-entry-selected', 'llnb-entry-pinned', 'llnb-entry-drift']
  if (active) classes.push('llnb-entry-active')
  const kind = driftRow?.kind ?? 'unknown'
  const children = [el('span', { className: 'llnb-entry-drift-name', text: entry.name })]
  let title = `${entry.name} — pinned from the image`
  if (kind === 'missing') {
    title = `${entry.name} — not in the library anymore`
    children.push(
      el('span', {
        className: 'llnb-drift',
        text: '≠',
        attrs: { title: 'Not in the library anymore — no entry by this name in the current notebook' }
      })
    )
  } else if (kind === 'differs') {
    title = `${entry.name} — pinned text differs from the current library`
    children.push(
      el('span', {
        className: 'llnb-drift',
        text: '≠',
        attrs: {
          title: `Differs from the current library.\nCurrent text starts: ${firstLineOf(driftRow.current) || '(empty)'}`
        }
      })
    )
  } else if (kind === 'same') {
    title = `${entry.name} — pinned text matches the current library`
  }
  const row = el('div', { className: classes.join(' '), attrs: { tabindex: '0', title } }, children)
  row.__llnbName = entry.name
  const pick = () => selectPinnedEntry(state, entry.name)
  row.addEventListener('click', pick)
  row.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return
    event.preventDefault()
    event.stopPropagation()
    pick()
  })
  return row
}

/** Show one pinned entry's OLD text in the editor (read-only). UI-only:
 * never touches `selection`, the `entry` widget, or the file. */
function selectPinnedEntry(state, name) {
  if (!isPinned(state)) return
  state.pinnedActive = name
  renderList(state)
  updateModeHint(state)
  paintPinnedEditor(state)
}

/** Editor pane in the pinned view: the active pinned entry's name + text,
 * READ-ONLY (readOnly, not disabled -- the owner's whole point is to SEE
 * the old values, and a disabled textarea is half-transparent and can't be
 * copied from). Baselines equal the shown text so dirty stays false and
 * Save stays dark; performSave() is gated regardless. */
function paintPinnedEditor(state) {
  const entry = pinnedActiveEntry(state)
  if (!entry) return
  state.textarea.value = entry.text
  state.lastSavedText = entry.text
  state.nameFieldEl.value = entry.name
  state.lastSavedName = entry.name.trim()
  state.baseMtime = null
  state.textarea.disabled = false
  state.textarea.readOnly = true
  state.textarea.classList.add('llnb-textarea-pinned')
  state.textarea.title = 'Pinned text captured from the image — read-only. Unpin to edit the live notebook.'
  state.nameFieldEl.disabled = false
  state.nameFieldEl.readOnly = true
  setDirty(state, false)
  clearConflict(state)
}

/** Undo paintPinnedEditor()'s read-only look -- resetEditorDom() and
 * populateEditor() both call it, so the look never outlives the pin. */
function clearPinnedEditorLook(state) {
  if (!state.textarea) return
  state.textarea.readOnly = false
  state.textarea.classList.remove('llnb-textarea-pinned')
  state.textarea.removeAttribute('title')
  if (state.nameFieldEl) state.nameFieldEl.readOnly = false
}

/** One-click back to the live notebook: write "" through the `pinned`
 * widget's value + callback (the file's widget-write idiom --
 * setFileWidgetValue()/syncEntryWidget()), toast, and let the callback
 * chain (wirePinnedWidget -> syncPinnedFromWidget) repaint and reload the
 * live view; a direct reconcile follows in case something upstream
 * swallowed the callback (idempotent). */
function unpin(state) {
  const widget = state.pinnedWidget
  if (!widget || !isPinned(state)) return
  widget.value = ''
  try {
    widget.callback?.('')
  } catch (error) {
    api.warn('pinned widget callback threw', error)
  }
  syncPinnedFromWidget(state)
  state.node.graph?.setDirtyCanvas(true, true)
  toast('info', 'Unpinned — back to the live notebook', 'The node reads the current notebook file again.')
}

/** Mutation gate for the pinned view: true (and says why in the status
 * line) when an edit was attempted while pinned. */
function pinnedRefuse(state) {
  if (!isPinned(state)) return false
  setStatus(state, 'Read-only while pinned — click Unpin (above) to edit the live notebook.')
  return true
}

// ---------------------------------------------------------------------------
// Status line + conflict UI (FORMAT.md §3.5)
// ---------------------------------------------------------------------------

/**
 * §7.2 error-state gate (2026-08-03): true while the panel must refuse
 * MUTATING actions -- after a failed load (loadError set), or if state.file
 * somehow isn't a usable string (a null/empty file would be resolved to the
 * DEFAULT notebook server-side, silently writing into the wrong file).
 * Read paths (list rendering, selection clicks that only load text) are
 * not gated -- only writes are dangerous.
 */
function writesBlocked(state) {
  return !!state.loadError || typeof state.file !== 'string' || state.file === ''
}

/** Status line for the load-error state: the message + a Retry button
 * (mirrors showConflict's action-button idiom). */
function showLoadError(state) {
  const err = state.loadError
  if (!err) return
  state.statusTextEl.textContent = `Could not load ${err.file}: ${err.message}`
  const retryBtn = el('button', { className: 'llnb-btn llnb-btn-small', text: 'Retry' })
  retryBtn.addEventListener('click', () => {
    retryBtn.disabled = true
    reloadNow(state).catch((error) => api.warn('retry load failed', error))
  })
  state.statusActionsEl.replaceChildren(retryBtn)
}

function setStatus(state, text) {
  state.statusTextEl.textContent = text || ''
  state.statusActionsEl.replaceChildren()
}

/**
 * A ComfyUI toast (same idiom as controller.js's `_toast`).
 *
 * 2026-07-27: added because the panel's small status line is too quiet for a
 * REFUSAL. The owner picked a file in Browse..., the node kept the old one,
 * and the only trace was a sentence in the status strip -- indistinguishable
 * from "the click did nothing". Anything that rejects or fails a file change
 * now also toasts, so the reason is unmissable. Fails soft: an older
 * frontend without extensionManager.toast simply logs.
 */
function toast(severity, summary, detail) {
  try {
    const add = app.extensionManager?.toast?.add
    if (typeof add === 'function') {
      add.call(app.extensionManager.toast, {
        severity,
        summary,
        detail,
        life: severity === 'error' ? 8000 : 5000
      })
      return
    }
  } catch (error) {
    api.warn('toast failed', error)
  }
  api.warn(`${summary}: ${detail}`)
}

function clearConflict(state) {
  state.statusActionsEl.replaceChildren()
}

/**
 * @param {{onReload: () => Promise<void>, onOverwrite: () => Promise<void>}} actions
 */
function showConflict(state, message, actions) {
  state.statusTextEl.textContent = message

  const reloadBtn = el('button', { className: 'llnb-btn llnb-btn-small', text: 'Reload' })
  const overwriteBtn = el('button', {
    className: 'llnb-btn llnb-btn-small llnb-btn-danger',
    text: 'Overwrite'
  })
  const disableBoth = () => {
    reloadBtn.disabled = true
    overwriteBtn.disabled = true
  }
  reloadBtn.addEventListener('click', () => {
    disableBoth()
    Promise.resolve(actions.onReload()).catch((error) => api.warn('reload (conflict) failed', error))
  })
  overwriteBtn.addEventListener('click', () => {
    disableBoth()
    Promise.resolve(actions.onOverwrite()).catch((error) => api.warn('overwrite (conflict) failed', error))
  })

  state.statusActionsEl.replaceChildren(reloadBtn, overwriteBtn)
}
