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
 * Rename via the editor's name field (FORMAT.md §7.2 amendment, owner ask
 * 2026-07-19 — supersedes the double-click-inline-input scheme this
 * paragraph used to describe, removed outright, "the old inline rename was
 * reported not working"): a NAME field sits at the top of the right pane,
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
 * other write in this file. Double-clicking a row no longer opens an
 * inline editor — it just moves focus (+select()) to this field via
 * focusNameField(), relying on the SAME click-vs-drag-vs-double-click
 * disambiguation already documented for onEntryPointerDown below (both
 * single-clicks that precede a dblclick resolve to a selection change
 * first, which is what makes the name field already show the right item's
 * name by the time focus lands on it — modulo the harmless brief race
 * described at focusNameField()'s own definition).
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
 * Delete is entry-only and disabled outright in category mode
 * (updateDeleteButtonEnabled()); double-clicking a header focuses the name
 * field exactly like double-clicking an entry row does (see "Rename via
 * the editor's name field" above) — it never opens anything header-local.
 *
 * Single-tap collapse (owner ask 2026-07-19 "single tap category name to
 * collapse category"): a plain tap on a header now does TWO things at once
 * — toggleCategoryCollapse() flips its membership in
 * `state.collapsedCategories` (a plain `Set<string>`, created once per
 * node in createState() and never read by anything outside this file) and
 * selectCategory() still enters category mode, exactly as before. Collapse
 * state is deliberately NOT a node property and never touches
 * `entry`/`file` — it lives only on this in-memory `Set`, so it is pure
 * per-node, per-session UI state: it survives any number of renderList()
 * redraws (renderList() reads the Set fresh every call and skips a
 * collapsed category's entry rows, still rendering the header itself) but
 * resets on a page reload, and — critically, given the file header's
 * opening promise that "only `file` + `entry` persist" — it is NEVER
 * serialized into the workflow. A collapsed category's entries are simply
 * absent from `state.dragRows` too, so they're inert (no click, no drag
 * source) until expanded again; the header itself stays a valid drop
 * TARGET either way (computeDropTarget()'s category-append geometry
 * degrades to "append after the header" when there's nothing visible
 * under it, the same fallback an actually-empty category already used).
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
 * Vanilla ES modules, no build step — DOM nodes are built with
 * `document.createElement` (see the local `el()` helper) rather than any
 * templating, matching this pack's other frontend modules.
 */

import * as api from './api.js'

/** FORMAT.md §6.1 — frozen once shipped. */
const NODE_CLASS = 'LoraLibraryNotebook'

const WIDGET_NAME = 'notebook'
const WIDGET_TYPE = 'lora_library_notebook'

/** FORMAT.md §7.2: "resizable via getMinHeight (~180)". */
const MIN_WIDGET_HEIGHT = 180

/** How long the Delete button stays in "Are you sure?" mode. */
const DELETE_CONFIRM_MS = 4000

/** Debounce for reloading after the `file` widget's value changes. */
const FILE_CHANGE_DEBOUNCE_MS = 250

/**
 * Pointer-movement distance (px) before a row pointerdown "becomes" a drag
 * instead of a click (owner ask: "~4px"). Below this, pointerup resolves as
 * a plain/ctrl/shift click; at or past it, the gesture commits to reordering
 * and the click never fires — see onEntryPointerDown().
 */
const DRAG_THRESHOLD_PX = 4

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
}
.llnb-category:hover { background: var(--content-hover-bg, #2a2a2a); }
.llnb-category:focus-visible { box-shadow: inset 0 0 0 1px var(--border-color, #444); }
.llnb-category-active,
.llnb-category-active:hover {
  background: rgba(66, 133, 244, 0.22);
  color: var(--input-text, #ccc);
}
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

    attachedNodes.add(node)

    const state = createState(node, fileWidget, entryWidget)
    buildUi(state)
    hideFileWidget(state)
    wireFileWidget(state)
    wireNodeCleanup(state)

    // FORMAT.md §7.2 amendment: one `/config` check per attach (cached at
    // module scope — see "Remote gating" below) to gate the file panel's
    // buttons and the `file` widget's edit-guard.
    refreshRemoteGating(state).catch((error) => api.warn('initial config load failed', error))
    reloadNow(state).catch((error) => api.warn('initial notebook load failed', error))
  } catch (error) {
    api.warn('attachNotebookWidget failed', error)
  }
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

function createState(node, fileWidget, entryWidget) {
  return {
    node,
    fileWidget,
    entryWidget,
    file: null,
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
    // Single-tap collapse (FORMAT.md §7.2 amendment, owner ask 2026-07-19)
    // — category names currently collapsed in the left list. Pure UI/
    // session state: never read outside this file, never serialized, reset
    // on reload of the page (not on reloadNow()/renderList(), which read it
    // fresh every call — see toggleCategoryCollapse()).
    collapsedCategories: new Set(),
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
    isLocal: null,
    // The file WIDGET's last known-good value — wireFileWidget() reverts to
    // this when a remote viewer edits a read-only `file` widget.
    lastKnownFileValue: null,
    // The Browse… picker's window-level Escape-key listener while open (the
    // picker lives on document.body, not inside this widget's own DOM — see
    // openBrowsePicker()).
    pickerKeydownHandler: null,
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

  state.listEl = el('div', { className: 'llnb-list' })
  state.footerEl = el('div', { className: 'llnb-footer' })
  state.leftPane = el('div', { className: 'llnb-pane llnb-pane-left' }, [state.listEl, state.footerEl])

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
  state.saveBtn = el('button', { className: 'llnb-btn llnb-btn-save', text: 'Save' })
  state.statusTextEl = el('div', { className: 'llnb-status-text' })
  state.statusActionsEl = el('div', { className: 'llnb-status-actions' })
  state.statusHintEl = el('div', { className: 'llnb-status-hint' })
  const statusRow = el('div', { className: 'llnb-status' }, [
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
  state.root = el('div', { className: 'llnb-root' }, [filePanel, panesRow])

  state.nameFieldEl.addEventListener('input', () => refreshDirty(state))
  state.nameFieldEl.addEventListener('keydown', (event) => {
    event.stopPropagation()
    if (event.key === 'Enter') {
      event.preventDefault()
      performSave(state).catch((error) => api.warn('save failed', error))
    }
  })
  state.textarea.addEventListener('input', () => refreshDirty(state))
  state.textarea.addEventListener('keydown', (event) => event.stopPropagation())
  state.saveBtn.addEventListener('click', () => {
    performSave(state).catch((error) => api.warn('save failed', error))
  })

  wireSplitter(state, splitter)

  renderFooter(state)
  clearEditor(state)

  attachDomWidget(state.node, state.root)
}

/**
 * Wraps `node.addDOMWidget` — kept as its own function so the three
 * non-serialization flags (see file header) sit next to the call that needs
 * them, instead of being scattered across `buildUi`.
 */
function attachDomWidget(node, rootEl) {
  const domWidget = node.addDOMWidget(WIDGET_NAME, WIDGET_TYPE, rootEl, {
    // Same default litegraph itself applies (`scripts/domWidget.ts`); kept
    // explicit for readability.
    hideOnZoom: true,
    // Excludes the widget from the API prompt (utils/executionUtil.ts).
    serialize: false,
    getMinHeight: () => MIN_WIDGET_HEIGHT
  })
  // Excludes the widget from the workflow JSON (LGraphNode.serialize /
  // .configure check `widget.serialize`, a *different* flag from
  // `options.serialize` above — see file header).
  domWidget.serialize = false
  domWidget.serializeValue = () => undefined
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
    window.removeEventListener('pointermove', onPointerMove)
    window.removeEventListener('pointerup', stopDragging)
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
    window.addEventListener('pointermove', onPointerMove)
    window.addEventListener('pointerup', stopDragging)
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
  state.fileWidget.hidden = true
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
    if (state.isLocal === false && value !== state.lastKnownFileValue) {
      widget.value = state.lastKnownFileValue
      setStatus(state, 'The host machine controls which file this node reads.')
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

function onFileWidgetChanged(state) {
  if (state.fileChangeDebounceTimer) clearTimeout(state.fileChangeDebounceTimer)
  state.fileChangeDebounceTimer = setTimeout(() => {
    state.fileChangeDebounceTimer = null
    if (state.fileWidget.value === state.file) return
    reloadNow(state).catch((error) => api.warn('notebook reload after file change failed', error))
  }, FILE_CHANGE_DEBOUNCE_MS)
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
  updateRemoteGatingUi(state)
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

  const actions = el('div', { className: 'llnb-filepanel-actions' }, [state.browseBtn, state.openFolderBtn])
  // Reworked 2026-07-19: path + buttons share one row (full-width path
  // control, "what's the point of the file field at top, replace it with
  // this"); the host-machine note (populated only when remote — see
  // updateRemoteGatingUi()) gets its OWN row below, never squeezed inline.
  const row = el('div', { className: 'llnb-filepanel-row' }, [state.filePanelPathEl, actions])
  const panel = el('div', { className: 'llnb-filepanel' }, [row, state.filePanelNoteEl])

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
  const path = state.resolvedFile || ''
  const pathEl = state.filePanelPathEl
  pathEl.title = path
  pathEl.textContent = path
  if (!path || pathEl.scrollWidth <= pathEl.clientWidth + 1) return // fits (or empty) as-is
  const budget = Math.max(8, Math.floor(pathEl.clientWidth / FILEPANEL_PATH_AVG_CHAR_PX))
  pathEl.textContent = frontTruncate(path, budget)
}

async function onOpenFolderClick(state) {
  if (!state.resolvedFile) return
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

function closeBrowsePicker(state) {
  document.getElementById(PICKER_OVERLAY_ID)?.remove()
  if (state.pickerKeydownHandler) {
    window.removeEventListener('keydown', state.pickerKeydownHandler)
    state.pickerKeydownHandler = null
  }
}

/**
 * FORMAT.md §7.2's Browse… dialog. Deliberately attached to `document.body`
 * rather than nested inside this widget's own root: the DOM widget's box is
 * only ever as tall as the node currently is (as small as
 * MIN_WIDGET_HEIGHT), and litegraph can reposition/clip it during pan/zoom
 * (see `hideOnZoom` on attachDomWidget() above) — a file browser confined to
 * that box would be cramped on a small node and would fight the same
 * clipping. A fixed, centered overlay on `document.body` stays a
 * comfortable, constant size regardless of the node's size/position, at the
 * cost of managing its own teardown by hand (closeBrowsePicker(), called
 * from here, Escape, a backdrop click, and this node's own teardown()).
 */
function openBrowsePicker(state) {
  closeBrowsePicker(state) // only one picker at a time, ever

  const backdrop = el('div', { className: 'llnb-picker-backdrop', attrs: { id: PICKER_OVERLAY_ID } })
  const dialog = el('div', { className: 'llnb-picker' })
  backdrop.append(dialog)
  backdrop.addEventListener('mousedown', (event) => {
    if (event.target === backdrop) closeBrowsePicker(state)
  })
  dialog.addEventListener('mousedown', (event) => event.stopPropagation())
  document.body.append(backdrop)

  state.pickerKeydownHandler = (event) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      closeBrowsePicker(state)
    }
  }
  window.addEventListener('keydown', state.pickerKeydownHandler)

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
    loadPickerDir(state, dialog, contentEl, pathErrorEl, typed)
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
  dialog.append(pathRow, pathErrorEl, contentEl)

  loadPickerDir(state, dialog, contentEl, pathErrorEl, dirnameOfServerPath(state.resolvedFile))
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
async function loadPickerDir(state, dialog, contentEl, pathErrorEl, dir) {
  pathErrorEl.textContent = ''
  contentEl.replaceChildren(el('div', { className: 'llnb-picker-status', text: 'Loading…' }))
  let data
  try {
    data = await api.getJson('/lora_library/fs/list', dir ? { dir } : undefined)
  } catch (error) {
    api.warn('fs/list failed', error)
    pathErrorEl.textContent = error.message || 'Could not list that path.'
    contentEl.replaceChildren(
      el('div', { className: 'llnb-picker-header', text: 'Browse' }),
      buildPickerFooter(state)
    )
    return
  }
  renderPickerDialog(state, dialog, contentEl, pathErrorEl, data)
}

function renderPickerDialog(state, dialog, contentEl, pathErrorEl, data) {
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
    upRow.addEventListener('click', () => loadPickerDir(state, dialog, contentEl, pathErrorEl, data.parent))
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
    row.addEventListener('click', () => loadPickerDir(state, dialog, contentEl, pathErrorEl, target))
    list.append(row)
  }
  for (const file of data.files || []) {
    const row = el('div', { className: 'llnb-picker-row', text: `📄 ${file.name}` })
    row.addEventListener('click', () => {
      closeBrowsePicker(state)
      setFileWidgetValue(state, joinServerPath(data.dir, file.name, data.sep))
    })
    list.append(row)
  }
  if (!data.parent && !(data.dirs || []).length && !(data.files || []).length) {
    list.append(el('div', { className: 'llnb-picker-empty', text: 'No subfolders or .md files here.' }))
  }

  contentEl.replaceChildren(header, list, buildPickerFooter(state))
}

function buildPickerFooter(state) {
  const cancelBtn = el('button', { className: 'llnb-btn llnb-btn-small', text: 'Cancel' })
  cancelBtn.addEventListener('click', () => closeBrowsePicker(state))
  return el('div', { className: 'llnb-picker-footer' }, [cancelBtn])
}

/** Writes `value` through the `file` widget's real setter+callback — the
 * exact same pattern syncEntryWidget() uses for `entry` — so picking a file
 * here behaves exactly like typing it in, including the debounced reload
 * (onFileWidgetChanged, via wireFileWidget) and that same wrapper's §7.2
 * read-only guard (moot in practice, since Browse… is itself hidden for a
 * remote caller — belt-and-suspenders all the same). */
function setFileWidgetValue(state, value) {
  const widget = state.fileWidget
  if (widget.value === value) return
  widget.value = value
  try {
    widget.callback?.(value)
  } catch (error) {
    api.warn('file widget callback threw', error)
  }
  state.node.graph?.setDirtyCanvas(true, true)
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
  let data
  try {
    data = await api.getJson('/lora_library/notebook', { file })
  } catch (error) {
    if (token !== state.loadToken) return
    api.warn('failed to load notebook list', error)
    setStatus(state, `Could not load notebook: ${error.message}`)
    return
  }
  if (token !== state.loadToken) return

  state.file = file
  state.entries = Array.isArray(data.entries) ? data.entries : []
  // FORMAT.md §5/§7.2: named categories in file order, incl. empty ones —
  // see renderList()'s merge of this against `entries`.
  state.categories = Array.isArray(data.categories) ? data.categories : []
  state.exists = data.exists !== false
  // FORMAT.md §7.2 file panel: the RESOLVED absolute path, distinct from
  // the (possibly relative) `file` WIDGET value above.
  state.resolvedFile = typeof data.file === 'string' ? data.file : null
  updateFilePanelPath(state)
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
  // Category mode survives a reload the same way entry selection does
  // (above): kept only if the category is still there, dropped silently
  // otherwise (FORMAT.md §7.2 amendment) — independent of the entry
  // selection restore, per the file header's "never touches `selection`"
  // rule for category mode.
  if (state.activeCategory != null && !state.categories.includes(state.activeCategory)) {
    state.activeCategory = null
  }
  renderList(state)
  updateDeleteButtonEnabled(state)
  updateSelectionHint(state)
  updateModeHint(state)

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
  setDirty(state, false)
}

/** Initial, pre-load editor state (buildUi() only — nothing has been
 * fetched yet, so there is nothing to preserve in the entry widget). */
function clearEditor(state) {
  state.selection = []
  state.activeName = null
  state.activeCategory = null
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

/** Dumb setter: replace the selection + active entry, sync the `entry`
 * widget, and re-render. Does not touch the editor pane — callers that
 * change the ACTIVE entry are responsible for loading (or clearing) it. */
function setSelection(state, names, active) {
  state.selection = names
  state.activeName = active
  syncEntryWidget(state)
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
 * name field themselves right after (confirmNewEntry()'s inline path). */
function populateEditor(state, text, mtime, name) {
  state.textarea.value = text ?? ''
  state.lastSavedText = state.textarea.value
  state.baseMtime = typeof mtime === 'number' ? mtime : null
  state.textarea.disabled = false
  if (name !== undefined) {
    state.nameFieldEl.value = name ?? ''
    state.lastSavedName = currentNameFieldValue(state)
    state.nameFieldEl.disabled = false
  }
  setDirty(state, false)
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

/** Category-mode sibling of loadEntryText() above — same token-guard/return
 * contract, fetching the §5 category route instead (FORMAT.md §7.2
 * amendment). */
async function loadCategoryDescription(state, name) {
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
  if (modifiers.shiftKey) selectRange(state, name)
  else if (modifiers.toggleKey) toggleEntry(state, name)
  else selectSingle(state, name)
}

/** Muted status-area hint (owner ask): visible only for 2+ selected, since
 * that's when OUTPUT_IS_LIST fan-out (§6.1) actually changes queue behavior.
 * Lives in its own element (not statusTextEl) so Saving…/Deleted…/conflict
 * messages never clobber it and vice versa. */
function updateSelectionHint(state) {
  if (!state.statusHintEl) return
  const count = state.selection.length
  state.statusHintEl.textContent =
    count >= 2 ? `${count} prompts selected — queue runs once per prompt.` : ''
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
  renderList(state)
  updateDeleteButtonEnabled(state)
  updateModeHint(state)

  const result = await loadCategoryDescription(state, name)
  if (result === 'failed') {
    // Roll back exactly like chooseSelection() does on a failed entry load —
    // the editor's own content was never touched by the failed fetch, so
    // restoring just the pointer is enough to undo the click.
    state.activeCategory = previousCategory
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

  const categories = Array.isArray(state.categories) ? state.categories : []
  const entries = state.entries

  if (!entries.length && !categories.length) {
    state.listEl.append(
      el('div', {
        className: 'llnb-empty',
        text: state.exists ? 'No entries yet.' : 'File not found yet.'
      })
    )
    return
  }

  let entryIndex = 0
  const appendEntry = (entry) => {
    const row = buildEntryRow(state, entry)
    state.listEl.append(row)
    state.dragRows.push({ el: row, kind: 'entry', name: entry.name, category: entry.category || '' })
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
    state.dragRows.push({ el: headerEl, kind: 'header', category })

    // Single-tap collapse (FORMAT.md §7.2 amendment): a collapsed
    // category's entries are skipped entirely — not rendered, not added to
    // `dragRows` — so they're visually gone AND inert (no click, no drag
    // source) until expanded again; the header row itself still renders
    // and stays a valid drop target either way.
    const collapsed = state.collapsedCategories.has(category)
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
  row.addEventListener('pointerdown', (event) => onEntryPointerDown(state, event, entry.name))
  row.addEventListener('dblclick', (event) => onEntryDoubleClick(state, event, entry.name))
  row.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return
    event.preventDefault()
    handleEntryClick(state, entry.name, { shiftKey: event.shiftKey, toggleKey: event.ctrlKey || event.metaKey })
  })
  return row
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

  const headerEl = el('div', {
    className: classes.join(' '),
    text: `${collapsed ? '▸' : '▾'} ${category}`,
    attrs: { tabindex: '0', title: category }
  })
  headerEl.addEventListener('pointerdown', (event) => onCategoryPointerDown(state, event, category))
  headerEl.addEventListener('dblclick', (event) => {
    event.preventDefault()
    event.stopPropagation()
    focusNameField(state)
  })
  headerEl.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return
    event.preventDefault()
    toggleCategoryCollapse(state, category)
    selectCategory(state, category).catch((error) => api.warn('select category failed', error))
  })
  return headerEl
}

/** Single-tap collapse (FORMAT.md §7.2 amendment, owner ask 2026-07-19) —
 * flips `category`'s membership in the session-only `state.collapsedCategories`
 * Set and re-renders; see the file header for why this never touches
 * serialization. Called unconditionally on every tap (see
 * onCategoryPointerDown()/the header's own keydown handler above) — even
 * when the category is already active, since re-selecting it is a no-op
 * for selectCategory() but the collapse toggle must still happen. */
function toggleCategoryCollapse(state, category) {
  if (state.collapsedCategories.has(category)) {
    state.collapsedCategories.delete(category)
  } else {
    state.collapsedCategories.add(category)
  }
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
    window.removeEventListener('pointermove', onMove)
    window.removeEventListener('pointerup', onUp)
    window.removeEventListener('pointercancel', onCancel)
  }
  // Escape hatch for teardown() — a node removal mid-drag has no pointerup
  // of its own, so it must be able to detach + restore visuals itself.
  drag.cleanup = () => {
    detach()
    if (drag.active) cancelDrag(state, drag)
  }

  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', onUp)
  window.addEventListener('pointercancel', onCancel)
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
    performMoveRun(state, names, target, 0).catch((error) => api.warn('move failed', error))
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
 * Commits one drag-drop as a single §5 `/notebook/move`. A 409 surfaces
 * through the same conflict UI Save/Delete use (Reload / Overwrite, where
 * Overwrite retries this exact move with the mtime check skipped); any
 * other error reports on the status line and falls back to a full reload
 * (§3.5 notwithstanding, a move failure means we no longer trust our
 * in-memory ordering).
 */
async function performMove(state, name, target, { force = false } = {}) {
  if (state.busy) return
  state.busy = true
  updateSaveButtonEnabled(state)
  updateDeleteButtonEnabled(state)
  setStatus(state, 'Moving…')
  try {
    const body = { file: state.file, name }
    if (target.kind === 'before') body.before = target.before
    else body.category = target.category
    if (!force && typeof state.baseMtime === 'number') body.base_mtime = state.baseMtime

    const data = await api.postJson('/lora_library/notebook/move', body)
    state.busy = false
    state.entries = Array.isArray(data.entries) ? data.entries : state.entries
    // The move just wrote the file, advancing its mtime — the active
    // entry's own content didn't change, but a stale baseMtime here would
    // make the NEXT save/delete/move spuriously 409 against this move's own
    // write (§3.5's conflict check is file-wide, not per-entry).
    state.baseMtime = typeof data.mtime === 'number' ? data.mtime : state.baseMtime
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
    if (error?.status === 409) {
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
 * unit — `names` (selection order) each get their own §5 `/notebook/move`
 * against the SAME `target`, sequentially, refreshing `base_mtime` between
 * requests so the run can't self-conflict. Re-using one `target` for every
 * entry — rather than recomputing it per-step — is what keeps the moved
 * block's relative order intact: a `before` target lands each subsequent
 * entry immediately ahead of that same sibling (so the entry moved LAST
 * ends up closest to it), and a `category` target appends each subsequent
 * entry after the previous one's new position — either way, processing in
 * selection order reproduces selection order at the destination. Mirrors
 * performDeleteRun()'s sequential-with-conflict-resume shape: a 409 stops
 * the run exactly where it is (everything moved so far stays moved) and
 * shows the standard Reload/Overwrite conflict UI, Overwrite resuming at
 * the failed index with that one request's `base_mtime` check skipped.
 */
async function performMoveRun(state, names, target, startIndex, { force = false } = {}) {
  state.busy = true
  updateSaveButtonEnabled(state)
  updateDeleteButtonEnabled(state)
  setStatus(state, `Moving ${names.length} entries…`)

  for (let index = startIndex; index < names.length; index++) {
    const name = names[index]
    let data
    try {
      const body = { file: state.file, name }
      if (target.kind === 'before') body.before = target.before
      else body.category = target.category
      const skipCheck = force && index === startIndex
      if (!skipCheck && typeof state.baseMtime === 'number') body.base_mtime = state.baseMtime
      data = await api.postJson('/lora_library/notebook/move', body)
    } catch (error) {
      state.busy = false
      updateSaveButtonEnabled(state)
      updateDeleteButtonEnabled(state)
      if (error?.status === 409) {
        showConflict(state, 'File changed on disk', {
          onReload: () => reloadNow(state),
          onOverwrite: () => performMoveRun(state, names, target, index, { force: true })
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
      return
    }
    state.entries = Array.isArray(data.entries) ? data.entries : state.entries
    state.baseMtime = typeof data.mtime === 'number' ? data.mtime : state.baseMtime
  }

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
  if (state.busy) return
  state.busy = true
  updateSaveButtonEnabled(state)
  updateDeleteButtonEnabled(state)
  setStatus(state, 'Moving category…')
  try {
    const body = { file: state.file, name: category }
    if (target.kind === 'before') body.before = target.before
    if (!force && typeof state.baseMtime === 'number') body.base_mtime = state.baseMtime

    const data = await api.postJson('/lora_library/notebook/move_category', body)
    state.busy = false
    state.entries = Array.isArray(data.entries) ? data.entries : state.entries
    state.categories = Array.isArray(data.categories) ? data.categories : state.categories
    state.baseMtime = typeof data.mtime === 'number' ? data.mtime : state.baseMtime
    renderList(state)
    updateSaveButtonEnabled(state)
    updateDeleteButtonEnabled(state)
    setStatus(state, 'Moved category.')
  } catch (error) {
    state.busy = false
    updateSaveButtonEnabled(state)
    updateDeleteButtonEnabled(state)
    if (error?.status === 409) {
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
  cancelDeleteConfirm(state)

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
    window.removeEventListener('pointermove', onMove)
    window.removeEventListener('pointerup', onUp)
    window.removeEventListener('pointercancel', onCancel)
  }
  drag.cleanup = () => {
    detach()
    if (drag.active) cancelDrag(state, drag)
  }

  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', onUp)
  window.addEventListener('pointercancel', onCancel)
}

// ---------------------------------------------------------------------------
// Rename via the editor's name field (FORMAT.md §7.2 amendment) — double-
// click a row to focus it; see the file header's "Rename via the editor's
// name field" paragraph for the full writeup, and performSave()/
// performSaveCategory() (below, "Save") for where the actual rename
// request gets sent.
// ---------------------------------------------------------------------------

/** Focuses (+selects) the name field, unless there's nothing loaded into
 * it yet (disabled — see resetEditorDom()/populateEditor()). */
function focusNameField(state) {
  if (!state.nameFieldEl || state.nameFieldEl.disabled) return
  state.nameFieldEl.focus()
  state.nameFieldEl.select()
}

/**
 * Rename via the editor's name field (FORMAT.md §7.2 amendment): native
 * `dblclick` only ever follows two complete click cycles on the same
 * element — see the file header — so by the time this fires, `name`'s row
 * has already become `state.activeName` via the two preceding single-
 * clicks (selectSingle()/chooseSelection()), which is what makes "focus
 * the name field" land on the right item without this handler needing to
 * touch selection itself.
 */
function onEntryDoubleClick(state, event, _name) {
  event.preventDefault()
  event.stopPropagation()
  // Belt-and-suspenders: a real drag can't produce a dblclick (a drag
  // commits via finishDrag()/pointerup, never a click), but a stray
  // in-flight drag object from some other pointer sequence should never
  // survive past this point.
  state.drag?.cleanup?.()
  state.drag = null
  focusNameField(state)
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
    const newBtn = el('button', { className: 'llnb-btn', text: '＋ New' })
    const deleteBtn = el('button', { className: 'llnb-btn', text: '🗑 Delete' })
    newBtn.addEventListener('click', () => openNewEntryRow(state))
    deleteBtn.addEventListener('click', () => onDeleteClick(state))

    state.footerEl.append(newBtn, deleteBtn)
    state.deleteBtn = deleteBtn
    updateDeleteButtonEnabled(state)
  }
}

function openNewEntryRow(state) {
  if (state.busy || state.creatingNew) return
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
    const data = await api.postJson('/lora_library/notebook/entry', body)
    state.busy = false
    state.entries = Array.isArray(data.entries) ? data.entries : state.entries
    state.exists = true
    closeNewEntryRow(state)

    // A new entry is created empty and already known (no need to re-fetch
    // it) — becomes the sole active selection, replacing whatever
    // multi-selection existed before. Also exits category mode (FORMAT.md
    // §7.2 amendment): the newly created entry is what the editor shows now.
    state.activeCategory = null
    setSelection(state, [name], name)
    state.textarea.value = ''
    state.lastSavedText = ''
    state.nameFieldEl.value = name
    state.lastSavedName = name
    state.baseMtime = typeof data.mtime === 'number' ? data.mtime : null
    state.textarea.disabled = false
    state.nameFieldEl.disabled = false
    setDirty(state, false)
    updateModeHint(state)
    setStatus(state, `Created "${name}".`)
  } catch (error) {
    state.busy = false
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
    const data = await api.postJson('/lora_library/notebook/category', {
      file: state.file,
      name,
      description: ''
    })
    state.busy = false
    state.entries = Array.isArray(data.entries) ? data.entries : state.entries
    state.categories = Array.isArray(data.categories) ? data.categories : state.categories
    state.exists = true
    closeNewEntryRow(state)

    // A new category is created with an empty description and already
    // known (no need to re-fetch it) — enters category mode for it,
    // untouched entry selection and all (see the file header).
    state.activeCategory = name
    renderList(state)
    updateDeleteButtonEnabled(state)
    populateEditor(state, '', data.mtime, name)
    updateModeHint(state)
    setStatus(state, `Created category "${name}".`)
  } catch (error) {
    state.busy = false
    api.warn('failed to create notebook category', error)
    setStatus(state, `Could not create category "${name}": ${error.message}`)
  }
}

// ---------------------------------------------------------------------------
// Delete (two-step inline confirm)
// ---------------------------------------------------------------------------

function onDeleteClick(state) {
  // Delete is entry-only — disabled outright in category mode
  // (updateDeleteButtonEnabled()); this is belt-and-suspenders against any
  // path that could invoke the handler despite that (FORMAT.md §7.2
  // amendment).
  if (!state.selection.length || state.busy || state.activeCategory != null) return

  if (!state.deleteConfirmActive) {
    state.deleteConfirmActive = true
    if (state.deleteBtn) {
      state.deleteBtn.textContent = deleteConfirmLabel(state.selection.length)
      state.deleteBtn.classList.add('llnb-btn-danger')
    }
    state.deleteConfirmTimer = setTimeout(() => cancelDeleteConfirm(state), DELETE_CONFIRM_MS)
    return
  }

  cancelDeleteConfirm(state)
  performDeleteRun(state, [...state.selection], 0).catch((error) => api.warn('delete failed', error))
}

/** "Are you sure?" (one entry) or "Are you sure? (3)" (owner amendment
 * 2026-07-18c) — the plain, not-yet-armed button label never changes. */
function deleteConfirmLabel(count) {
  return count > 1 ? `Are you sure? (${count})` : 'Are you sure?'
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
 * Deletes `names` sequentially over the single-entry §5 delete route,
 * starting at `startIndex` (>0 only on a post-conflict Overwrite resume —
 * see below). Each successful response's `mtime` becomes the NEXT
 * request's `base_mtime`, and each deleted name is dropped from the
 * selection right away using the exact rule this file always used for a
 * single delete ("Delete acts on the ACTIVE entry only": hand `active` to
 * the last other still-selected name, or clear it if none remain) —
 * applied once per name here, which naturally converges to "clear
 * selection" by the time the whole batch is gone, since nothing outside
 * this run ever ADDS to `state.selection` while it's in flight (see the
 * file header's "Multi-delete" paragraph).
 *
 * A 409 stops the run right where it is — everything deleted so far stays
 * deleted and is already reflected in `state.selection`/the `entry` widget
 * — and shows the same Reload/Overwrite conflict UI Save/Move already use;
 * Overwrite re-enters this same function at the failed index with
 * `force: true` (that ONE request skips `base_mtime`), then continues
 * normally through the rest of `names`.
 */
async function performDeleteRun(state, names, startIndex, { force = false } = {}) {
  state.busy = true
  updateSaveButtonEnabled(state)
  updateDeleteButtonEnabled(state)
  setStatus(state, names.length > 1 ? `Deleting ${names.length} entries…` : 'Deleting…')

  for (let index = startIndex; index < names.length; index++) {
    const name = names[index]
    let data
    try {
      const body = { file: state.file, name }
      const skipCheck = force && index === startIndex
      if (!skipCheck && typeof state.baseMtime === 'number') body.base_mtime = state.baseMtime
      data = await api.postJson('/lora_library/notebook/delete', body)
    } catch (error) {
      state.busy = false
      updateSaveButtonEnabled(state)
      updateDeleteButtonEnabled(state)
      if (error?.status === 409) {
        showConflict(state, 'File changed on disk', {
          onReload: () => reloadNow(state),
          onOverwrite: () => performDeleteRun(state, names, index, { force: true })
        })
      } else {
        api.warn('failed to delete notebook entry', error)
        setStatus(state, `Delete failed: ${error.message}`)
      }
      return
    }

    state.entries = Array.isArray(data.entries) ? data.entries : state.entries
    state.baseMtime = typeof data.mtime === 'number' ? data.mtime : state.baseMtime

    const previousActive = state.activeName
    const nextSelection = state.selection.filter((n) => n !== name)
    const nextActive = previousActive === name ? lastOrNull(nextSelection) : previousActive
    setSelection(state, nextSelection, nextActive)

    if (nextActive !== previousActive) {
      if (nextActive == null) {
        resetEditorDom(state)
      } else {
        const result = await loadEntryText(state, nextActive)
        if (result === 'failed') resetEditorDom(state)
      }
    }
  }

  state.busy = false
  updateDeleteButtonEnabled(state)
  setStatus(state, names.length > 1 ? `Deleted ${names.length} entries.` : 'Deleted.')
}

// ---------------------------------------------------------------------------
// Save
// ---------------------------------------------------------------------------

async function performSave(state, { force = false } = {}) {
  // The editor is contextual (FORMAT.md §7.2 amendment): category mode owns
  // Save whenever it's active, entirely independent of `activeName` (which
  // may still name an entry underneath — see the file header). This is the
  // ONE branch point between the two; everything else about category-mode
  // saving lives in performSaveCategory() below.
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

    const data = await api.postJson('/lora_library/notebook/entry', body)
    state.busy = false
    if (state.activeName !== name) {
      // Selection moved on while the request was in flight; nothing left to
      // reconcile against the (now stale) textarea/name-field contents.
      updateSaveButtonEnabled(state)
      return
    }
    state.lastSavedText = text
    state.baseMtime = typeof data.mtime === 'number' ? data.mtime : state.baseMtime
    state.entries = Array.isArray(data.entries) ? data.entries : state.entries
    if (renameTo) {
      const nextSelection = state.selection.map((n) => (n === name ? renameTo : n))
      setSelection(state, nextSelection, renameTo) // also re-renders the list
      state.nameFieldEl.value = renameTo
    } else {
      renderList(state)
    }
    state.lastSavedName = currentNameFieldValue(state)
    refreshDirty(state)
    updateModeHint(state)
    setStatus(state, renameTo ? `Saved. Renamed to "${renameTo}".` : 'Saved.')
  } catch (error) {
    state.busy = false
    updateSaveButtonEnabled(state)
    if (error?.status === 409) {
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

    const data = await api.postJson('/lora_library/notebook/category', body)
    state.busy = false
    if (state.activeCategory !== name) {
      updateSaveButtonEnabled(state)
      return
    }
    state.lastSavedText = description
    state.baseMtime = typeof data.mtime === 'number' ? data.mtime : state.baseMtime
    state.entries = Array.isArray(data.entries) ? data.entries : state.entries
    state.categories = Array.isArray(data.categories) ? data.categories : state.categories
    if (renameTo) {
      state.activeCategory = renameTo
      state.nameFieldEl.value = renameTo
      // Single-tap collapse (FORMAT.md §7.2 amendment) tracks collapse by
      // NAME (state.collapsedCategories is a bare Set<string>) — without
      // this, a renamed category that was collapsed would silently render
      // expanded post-rename, since the Set still holds the OLD string.
      if (state.collapsedCategories.delete(name)) {
        state.collapsedCategories.add(renameTo)
      }
    }
    state.lastSavedName = currentNameFieldValue(state)
    refreshDirty(state)
    renderList(state)
    updateModeHint(state)
    setStatus(state, renameTo ? `Saved. Renamed to "${renameTo}".` : 'Saved.')
  } catch (error) {
    state.busy = false
    updateSaveButtonEnabled(state)
    if (error?.status === 409) {
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
 * enable it. Called from both fields' `input` listeners (buildUi()). */
function refreshDirty(state) {
  const textChanged = state.textarea.value !== state.lastSavedText
  const nameChanged = currentNameFieldValue(state) !== state.lastSavedName
  setDirty(state, textChanged || nameChanged)
}

function updateSaveButtonEnabled(state) {
  if (!state.saveBtn) return
  // FORMAT.md §7.2 amendment: Save targets whichever of the two contextual
  // modes is active (category mode or entry mode — see performSave()).
  const hasTarget = state.activeCategory != null || Boolean(state.activeName)
  state.saveBtn.disabled = state.busy || !hasTarget || !state.dirty
}

function updateDeleteButtonEnabled(state) {
  if (!state.deleteBtn) return
  // Delete stays entry-only — disabled outright in category mode (FORMAT.md
  // §7.2 amendment).
  state.deleteBtn.disabled =
    state.busy || state.selection.length === 0 || state.activeCategory != null
}

// ---------------------------------------------------------------------------
// Status line + conflict UI (FORMAT.md §3.5)
// ---------------------------------------------------------------------------

function setStatus(state, text) {
  state.statusTextEl.textContent = text || ''
  state.statusActionsEl.replaceChildren()
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
