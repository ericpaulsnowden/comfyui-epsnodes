/**
 * @file LoRA Notebook two-pane DOM widget (FORMAT.md §7.2) — attaches to
 * `LoraLibraryNotebook` nodes. Left pane: a scrollable, category-grouped,
 * multi-selectable, drag-to-reorder entry list with New/Delete controls.
 * Right pane: a `<textarea>` editor with a Save button and a status line
 * (conflict resolution per §3.5 lands there too). The node's own
 * `file`/`entry` STRING widgets stay the serialized truth (§6.1/§7.2) — this
 * DOM widget only ever *reads* `file` and *writes* `entry` through its
 * normal widget setter; it never serializes itself.
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

/** How long the Delete button stays in "Really delete?" mode. */
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
  flex-direction: row;
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
.llnb-btn-save { flex: 0 0 auto; align-self: flex-start; margin-bottom: 3px; }
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
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 4px 6px;
}
.llnb-status { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.llnb-status-text {
  color: var(--descrip-text, #999);
  font-size: 10px;
  overflow-wrap: anywhere;
  white-space: normal;
}
.llnb-status-actions { display: flex; gap: 4px; }
.llnb-status-actions:empty { display: none; }
.llnb-status-hint {
  color: var(--descrip-text, #999);
  font-size: 10px;
  font-style: italic;
  overflow-wrap: anywhere;
  white-space: normal;
}
.llnb-status-hint:empty { display: none; }
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
    wireFileWidget(state)
    wireNodeCleanup(state)

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
    // Selection model (§6.1/§7.2): `selection` is the ordered list of
    // selected entry names — exactly what gets newline-joined into the
    // `entry` widget. `activeName` is the most-recently-clicked selected
    // entry; it alone drives the editor/dirty/Save/Delete/conflict flow.
    // See the "Selection model" functions below.
    selection: [],
    activeName: null,
    baseMtime: null,
    lastSavedText: '',
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
    // DOM refs, filled in by buildUi() — only elements later functions need
    // to reach back into are tracked here (e.g. `newBtn` isn't, since
    // nothing but renderFooter() itself ever touches it).
    root: null,
    leftPane: null,
    listEl: null,
    footerEl: null,
    textarea: null,
    saveBtn: null,
    statusTextEl: null,
    statusActionsEl: null,
    statusHintEl: null,
    deleteBtn: null
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

  state.textarea = el('textarea', {
    className: 'llnb-textarea',
    attrs: {
      placeholder: 'Select an entry on the left, or click ＋ New to create one.',
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
  const rightPane = el('div', { className: 'llnb-pane llnb-pane-right' }, [state.textarea, bottomRow])

  state.root = el('div', { className: 'llnb-root' }, [state.leftPane, splitter, rightPane])

  state.textarea.addEventListener('input', () => {
    setDirty(state, state.textarea.value !== state.lastSavedText)
  })
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

function wireFileWidget(state) {
  const widget = state.fileWidget
  const original = widget.callback
  widget.callback = function (value, ...rest) {
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
  // A node removal mid-drag (e.g. undo, right-click delete) would otherwise
  // leak the drag's window-level pointermove/pointerup/pointercancel
  // listeners forever — see onEntryPointerDown().
  state.drag?.cleanup?.()
  // Invalidate any in-flight fetches so their `.then` handlers no-op.
  state.loadToken += 1
  state.selectToken += 1
}

// ---------------------------------------------------------------------------
// Loading the notebook list + auto-select
// ---------------------------------------------------------------------------

async function reloadNow(state) {
  const token = ++state.loadToken
  cancelDeleteConfirm(state)
  closeNewEntryRow(state)
  clearConflict(state)

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
  state.exists = data.exists !== false
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
  renderList(state)
  updateDeleteButtonEnabled(state)
  updateSelectionHint(state)

  if (state.activeName) {
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
  state.baseMtime = null
  state.textarea.disabled = true
  setDirty(state, false)
}

/** Initial, pre-load editor state (buildUi() only — nothing has been
 * fetched yet, so there is nothing to preserve in the entry widget). */
function clearEditor(state) {
  state.selection = []
  state.activeName = null
  resetEditorDom(state)
  renderList(state)
  updateDeleteButtonEnabled(state)
  updateSelectionHint(state)
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

function populateEditor(state, data) {
  state.textarea.value = data.text ?? ''
  state.lastSavedText = state.textarea.value
  state.baseMtime = typeof data.mtime === 'number' ? data.mtime : null
  state.textarea.disabled = false
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
  populateEditor(state, data)
  return 'ok'
}

/**
 * The interactive entry point: apply a new selection immediately, then load
 * the new active entry (only when the active identity actually changed —
 * clicking around a multi-selection must never clobber unsaved edits in the
 * entry that's already open). A failed load rolls back to the selection
 * that was in effect before this call.
 * @param {string[]} names
 * @param {string|null} active
 */
async function chooseSelection(state, names, active) {
  cancelDeleteConfirm(state)
  closeNewEntryRow(state)

  const previousSelection = state.selection
  const previousActive = state.activeName
  setSelection(state, names, active)
  if (active === previousActive) return

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
// Entry list rendering
// ---------------------------------------------------------------------------

function renderList(state) {
  state.listEl.replaceChildren()
  state.dragRows = []

  if (!state.entries.length) {
    state.listEl.append(
      el('div', {
        className: 'llnb-empty',
        text: state.exists ? 'No entries yet.' : 'File not found yet.'
      })
    )
    return
  }

  let lastCategory
  for (const entry of state.entries) {
    const category = entry.category || ''
    if (category !== lastCategory) {
      lastCategory = category
      if (category) {
        const headerEl = el('div', { className: 'llnb-category', text: category })
        state.listEl.append(headerEl)
        state.dragRows.push({ el: headerEl, kind: 'header', category })
      }
    }

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
    row.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' && event.key !== ' ') return
      event.preventDefault()
      handleEntryClick(state, entry.name, { shiftKey: event.shiftKey, toggleKey: event.ctrlKey || event.metaKey })
    })
    state.listEl.append(row)
    state.dragRows.push({ el: row, kind: 'entry', name: entry.name, category })
  }
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
//    plain/ctrl/shift selection logic above.
//  - moved >= DRAG_THRESHOLD_PX at any point → a drag; commits to reorder
//    and the click never fires.
// ---------------------------------------------------------------------------

function onEntryPointerDown(state, event, name) {
  if (event.button !== 0) return // primary button/touch only
  cancelDeleteConfirm(state)

  const drag = {
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
  drag.marker = el('div', { className: 'llnb-drag-marker' })
}

function updateDrag(state, drag, clientY) {
  drag.target = computeDropTarget(state, clientY, drag.name)
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
  drag.marker?.remove()
}

function finishDrag(state, drag) {
  endDragVisuals(drag)
  const target = drag.target
  if (!target) return
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
 * @returns {{kind:'before', before:string, markerBeforeEl:HTMLElement} |
 *           {kind:'category', category:string, markerAfterEl:HTMLElement} | null}
 */
function computeDropTarget(state, clientY, excludeName) {
  const rows = state.dragRows.filter((row) => row.kind !== 'entry' || row.name !== excludeName)
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

// ---------------------------------------------------------------------------
// Footer: New / Delete buttons <-> inline "new entry name" row
// ---------------------------------------------------------------------------

function renderFooter(state) {
  state.footerEl.replaceChildren()

  if (state.creatingNew) {
    const input = el('input', {
      className: 'llnb-input',
      attrs: { type: 'text', placeholder: 'Entry name…' }
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

async function confirmNewEntry(state, rawName) {
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
    const data = await api.postJson('/lora_library/notebook/entry', {
      file: state.file,
      name,
      text: ''
    })
    state.busy = false
    state.entries = Array.isArray(data.entries) ? data.entries : state.entries
    state.exists = true
    closeNewEntryRow(state)

    // A new entry is created empty and already known (no need to re-fetch
    // it) — becomes the sole active selection, replacing whatever
    // multi-selection existed before.
    setSelection(state, [name], name)
    state.textarea.value = ''
    state.lastSavedText = ''
    state.baseMtime = typeof data.mtime === 'number' ? data.mtime : null
    state.textarea.disabled = false
    setDirty(state, false)
    setStatus(state, `Created "${name}".`)
  } catch (error) {
    state.busy = false
    api.warn('failed to create notebook entry', error)
    setStatus(state, `Could not create "${name}": ${error.message}`)
  }
}

// ---------------------------------------------------------------------------
// Delete (two-step inline confirm)
// ---------------------------------------------------------------------------

function onDeleteClick(state) {
  if (!state.activeName || state.busy) return

  if (!state.deleteConfirmActive) {
    state.deleteConfirmActive = true
    if (state.deleteBtn) {
      state.deleteBtn.textContent = 'Really delete?'
      state.deleteBtn.classList.add('llnb-btn-danger')
    }
    state.deleteConfirmTimer = setTimeout(() => cancelDeleteConfirm(state), DELETE_CONFIRM_MS)
    return
  }

  cancelDeleteConfirm(state)
  performDelete(state).catch((error) => api.warn('delete failed', error))
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

async function performDelete(state, { force = false } = {}) {
  const name = state.activeName
  if (!name || state.busy) return

  state.busy = true
  updateDeleteButtonEnabled(state)
  setStatus(state, 'Deleting…')
  try {
    const body = { file: state.file, name }
    if (!force && typeof state.baseMtime === 'number') body.base_mtime = state.baseMtime

    const data = await api.postJson('/lora_library/notebook/delete', body)
    state.busy = false
    state.entries = Array.isArray(data.entries) ? data.entries : state.entries

    // §7.2: "Delete acts on the ACTIVE entry only." Drop it from the
    // selection; if it WAS the active one, hand "active" to the last
    // remaining selected entry (or clear the editor if none remain). If the
    // active entry had already moved on to something else while this
    // request was in flight, leave that alone — just drop the deleted name
    // from the selection set.
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
    setStatus(state, 'Deleted.')
  } catch (error) {
    state.busy = false
    updateDeleteButtonEnabled(state)
    if (error?.status === 409) {
      showConflict(state, 'File changed on disk', {
        onReload: () => reloadNow(state),
        onOverwrite: () => performDelete(state, { force: true })
      })
    } else {
      api.warn('failed to delete notebook entry', error)
      setStatus(state, `Delete failed: ${error.message}`)
    }
  }
}

// ---------------------------------------------------------------------------
// Save
// ---------------------------------------------------------------------------

async function performSave(state, { force = false } = {}) {
  if (!state.activeName || state.busy) return

  const name = state.activeName
  const text = state.textarea.value

  state.busy = true
  updateSaveButtonEnabled(state)
  setStatus(state, 'Saving…')
  try {
    const body = { file: state.file, name, text }
    if (!force && typeof state.baseMtime === 'number') body.base_mtime = state.baseMtime

    const data = await api.postJson('/lora_library/notebook/entry', body)
    state.busy = false
    if (state.activeName !== name) {
      // Selection moved on while the request was in flight; nothing left to
      // reconcile against the (now stale) textarea contents.
      updateSaveButtonEnabled(state)
      return
    }
    state.lastSavedText = text
    state.baseMtime = typeof data.mtime === 'number' ? data.mtime : state.baseMtime
    state.entries = Array.isArray(data.entries) ? data.entries : state.entries
    setDirty(state, state.textarea.value !== state.lastSavedText)
    renderList(state)
    setStatus(state, 'Saved.')
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

// ---------------------------------------------------------------------------
// Dirty / button enablement
// ---------------------------------------------------------------------------

function setDirty(state, value) {
  state.dirty = value
  updateSaveButtonEnabled(state)
}

function updateSaveButtonEnabled(state) {
  if (!state.saveBtn) return
  state.saveBtn.disabled = state.busy || !state.activeName || !state.dirty
}

function updateDeleteButtonEnabled(state) {
  if (!state.deleteBtn) return
  state.deleteBtn.disabled = state.busy || !state.activeName
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
