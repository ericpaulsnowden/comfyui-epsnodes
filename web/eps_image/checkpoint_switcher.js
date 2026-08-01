/**
 * @file EPS Checkpoint Switcher frontend. Exports the `init()`/`attach(node)`
 * hooks `web/eps_image.js` calls; `attach` no-ops for every node type other
 * than `EPSCheckpointSwitcher`.
 *
 * Contract (backend built in parallel against this exact shape): the node's
 * one real, serialized widget is `selection` -- a STRING holding a JSON
 * ARRAY of checkpoint filenames (forward-slash relative paths, exactly as
 * `GET /eps_ckpt/checkpoints` -> `{"checkpoints": [...]}` lists them),
 * default `"[]"`. This file hides that widget (both the canvas `.hidden`
 * flag and the Vue-nodes `options.hidden` mirror -- FORMAT.md §7.5, the same
 * belt-and-suspenders every pack-internal plumbing widget uses) and replaces
 * it with a scrollable, filterable, folder-grouped checkbox list DOM widget.
 *
 * **Order is the fetched list's order, not click order.** A toggle never
 * just appends the clicked name onto whatever was there -- every write
 * (`writeSelectionWidget`) re-derives the array by filtering the FETCHED
 * list (server order) down to whatever is currently checked, so the saved
 * `selection` array is deterministic regardless of click order (the node
 * contract's "order = emission order"). A selection entry the fetched list
 * doesn't know about (deleted/renamed on disk since save) is appended after
 * the known ones rather than dropped -- see "Missing entries" below.
 *
 * **Restore-correctness** (this repo's most-burned lesson --
 * `web/lora_library/notebook.js`'s `wireConfigureReload` header, FORMAT.md
 * §7.2): litegraph restores `widgets_values` AFTER `attach()` runs, and
 * assigns `widget.value` directly -- no callback fires. Reading `selection`
 * synchronously at attach time would therefore only ever see the widget's
 * just-constructed default (`"[]"`), not a loaded workflow's real value.
 * `wireConfigureReload` wraps `node.onConfigure` (the hook litegraph calls
 * AFTER that restore, for both a whole-workflow load and a paste) and
 * re-parses `selection` from the widget then.
 *
 * That same re-parse-from-widget step (`reloadFromWidget`) is ALSO what the
 * initial checkpoint fetch runs once it resolves -- so whichever of {the
 * fetch, `onConfigure`} finishes LAST produces the final render:
 * `state.selection` is never assumed fresh from one path alone, it is always
 * re-derived from the widget's current value. Neither ordering leaves stale
 * rows on screen.
 *
 * **Missing entries.** A name in `state.selection` absent from the fetched
 * list renders as its own row -- struck through, ⚠-prefixed, checkbox still
 * fully interactive and still checked -- rather than vanishing from the JSON
 * the moment the panel repaints. Unchecking it is the only way to remove it.
 *
 * No window-level listeners: every interaction here is a plain element-level
 * click/change/input, so the FORMAT.md §7.5 capture-phase requirement for
 * window-level gesture listeners does not come up -- there are none.
 */

import { api } from '../../../scripts/api.js'

/** Frozen once shipped -- mirrors the Python node's class id. */
export const CLASS_ID = 'EPSCheckpointSwitcher'
export const SELECTION_WIDGET_NAME = 'selection'
export const ROUTE = '/eps_ckpt/checkpoints'

const PREFIX = '[eps_ckpt]'
const PANEL_WIDGET_NAME = 'eps_ckpt_panel'
const PANEL_WIDGET_TYPE = 'eps_ckpt_switcher_panel'

/** Fill-style DOM widget: floor only, no ceiling -- litegraph's
 * widget-arrange pass gives it whatever height is left, matching
 * notebook.js's attachDomWidget(). */
const MIN_WIDGET_HEIGHT = 150

/** ~320px floor (task brief) -- same Linux-font-overflow rationale as every
 * DOM-widget node in this pack (FORMAT.md §7.2); self-contained per module
 * like distributor.js's own installMinWidth copy (own guard flag). */
const MIN_NODE_WIDTH = 320

const STYLE_TAG_ID = 'eps-ckpt-switcher-styles'

/** Nodes we've already attached to -- guards a double `nodeCreated`. */
const attachedNodes = new WeakSet()

// --- Pure helpers -- exported so tests can drive them under Node instead of
// source-grepping (distributor.js's toggleBoxRect/parseToggles precedent). ---

/**
 * Parses the `selection` widget's raw string into an array of names. Never
 * throws: malformed JSON, a non-array value, or a missing/non-string *raw*
 * all degrade to `[]` (switcher.js's `readToggles` fallback). Non-string
 * entries are dropped; duplicates collapse to their first occurrence.
 * @param {unknown} raw
 * @returns {string[]}
 */
export function selectionFromWidgetValue(raw) {
  if (typeof raw !== 'string' || raw.trim() === '') return []
  let parsed
  try {
    parsed = JSON.parse(raw)
  } catch {
    return []
  }
  if (!Array.isArray(parsed)) return []
  const seen = new Set()
  const out = []
  for (const entry of parsed) {
    if (typeof entry !== 'string' || seen.has(entry)) continue
    seen.add(entry)
    out.push(entry)
  }
  return out
}

/**
 * Returns a NEW array with *name* added (checked) or removed (unchecked)
 * from *list*, preserving every other entry's relative order and never
 * duplicating. A no-op call (already-checked / already-absent) returns an
 * equal-valued array. This is the row click primitive; the caller
 * (writeSelectionWidget) re-sorts the result to the fetched list's order
 * before it is ever written -- see the file header.
 * @param {string[]} list
 * @param {string} name
 * @param {boolean} checked
 * @returns {string[]}
 */
export function toggleName(list, name, checked) {
  const arr = Array.isArray(list) ? list : []
  const has = arr.includes(name)
  if (checked) return has ? arr.slice() : [...arr, name]
  return has ? arr.filter((entry) => entry !== name) : arr.slice()
}

// --- Tiny DOM builder (identical helper to every sibling web/eps_image/*.js
// and web/lora_library/notebook.js file). ---

function el(tag, options = {}, children = []) {
  const node = document.createElement(tag)
  if (options.className) node.className = options.className
  if (options.text !== undefined) node.textContent = options.text
  if (options.attrs) {
    for (const [key, value] of Object.entries(options.attrs)) node.setAttribute(key, value)
  }
  for (const child of children) {
    if (child == null) continue
    node.append(child instanceof Node ? child : document.createTextNode(String(child)))
  }
  return node
}

// --- Styles -- one injected <style> tag, guarded against duplicate
// injection; reuses this pack's established theme-variable fallbacks. ---

let stylesInjected = false

const CSS_TEXT = `
.epscs-root { display: flex; flex-direction: column; width: 100%; height: 100%; box-sizing: border-box; overflow: hidden; background: var(--comfy-input-bg, #1e1e1e); border: 1px solid var(--border-color, #444); border-radius: 4px; font-family: inherit; font-size: 11px; color: var(--input-text, #ccc); }
.epscs-filter { flex: 0 0 auto; padding: 4px; border-bottom: 1px solid var(--border-color, #444); }
.epscs-filter-input { width: 100%; box-sizing: border-box; background: var(--comfy-menu-bg, #262626); border: 1px solid var(--border-color, #444); color: var(--input-text, #ccc); border-radius: 4px; padding: 3px 6px; font-size: 11px; }
.epscs-count { flex: 0 0 auto; padding: 3px 7px; color: var(--descrip-text, #999); font-size: 10px; }
.epscs-count:empty { display: none; }
.epscs-list { flex: 1 1 auto; min-height: 0; overflow-y: auto; overflow-x: hidden; padding: 2px 3px 4px; }
.epscs-group-header { padding: 4px 6px 2px; margin-top: 4px; font-size: 9.5px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; color: var(--descrip-text, #999); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.epscs-row { display: flex; align-items: center; gap: 6px; padding: 2px 6px; border-radius: 3px; cursor: pointer; user-select: none; }
.epscs-row:hover { background: var(--content-hover-bg, #2a2a2a); }
.epscs-row input[type="checkbox"] { flex: 0 0 auto; margin: 0; cursor: pointer; }
.epscs-row-label { flex: 1 1 auto; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.epscs-row-missing { color: var(--error-text, #ff4444); font-style: italic; text-decoration: line-through; }
.epscs-empty { padding: 8px 7px; color: var(--descrip-text, #999); font-style: italic; }
.epscs-error-row { display: flex; align-items: center; gap: 8px; padding: 8px 7px; }
.epscs-error-text { flex: 1 1 auto; min-width: 0; color: var(--error-text, #ff4444); }
.epscs-retry-btn { flex: 0 0 auto; background: var(--comfy-menu-bg, #262626); border: 1px solid var(--border-color, #444); color: var(--input-text, #ccc); border-radius: 4px; padding: 3px 8px; font-size: 11px; cursor: pointer; }
.epscs-retry-btn:hover { background: var(--content-hover-bg, #2a2a2a); }
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

// --- Node / widget lookups ---

/** @param {object} node @returns {string|null} */
function nodeClassOf(node) {
  if (!node) return null
  if (node.comfyClass) return node.comfyClass
  if (node.constructor && node.constructor.comfyClass) return node.constructor.comfyClass
  return null
}

function findWidget(node, name) {
  return node.widgets?.find((w) => w && w.name === name)
}

// --- Node width floor -- self-contained copy, own guard flag (see
// distributor.js's identical-shape installMinWidth for the house pattern). ---

function installMinWidth(node, minWidth) {
  if (!node || node.__epsCkptMinWidthInstalled) return
  node.__epsCkptMinWidthInstalled = true
  const originalOnResize = node.onResize
  node.onResize = function (size) {
    if (size && size[0] < minWidth) size[0] = minWidth
    return originalOnResize?.call(this, size)
  }
  if (Array.isArray(node.size) && node.size[0] < minWidth) node.size[0] = minWidth
}

// --- State ---

function createState(node, widget) {
  return {
    node,
    widget,
    checkpoints: [], // fetched list, server order -- [] until the first load resolves
    checkpointSet: new Set(),
    selection: [], // current checked names, source of truth mirrored from the widget
    loaded: false,
    error: null,
    filterText: '',
    loadToken: 0, // guards a stale/superseded fetch from clobbering fresher state
    root: null,
    filterInput: null,
    countEl: null,
    listEl: null
  }
}

/**
 * BOTH flags, deliberately (FORMAT.md §7.5): litegraph's canvas renderer
 * hides on `widget.hidden`, but the Vue-nodes renderer decides visibility
 * from `widget.options.hidden` and ignores `widget.hidden` outright -- so
 * without the second flag this widget would leak into a Vue node as a raw
 * editable text field. Canvas ignores `options.hidden` right back.
 */
function hideSelectionWidget(state) {
  const widget = state.widget
  widget.hidden = true
  widget.options = { ...(widget.options || {}), hidden: true }
  state.node.graph?.setDirtyCanvas(true, true)
}

// --- UI construction ---

function buildFilterRow(state) {
  state.filterInput = el('input', {
    className: 'epscs-filter-input',
    attrs: { type: 'text', placeholder: 'Filter…' }
  })
  state.filterInput.addEventListener('input', () => {
    state.filterText = state.filterInput.value
    renderList(state)
  })
  // Canvas hotkeys (Delete/Ctrl+C/etc.) must not intercept typing here --
  // notebook.js's textarea/name-field keydown handlers do the same.
  state.filterInput.addEventListener('keydown', (event) => event.stopPropagation())
  return el('div', { className: 'epscs-filter' }, [state.filterInput])
}

function buildUi(state) {
  injectStyles()
  state.countEl = el('div', { className: 'epscs-count' })
  state.listEl = el('div', { className: 'epscs-list' })
  const filterRow = buildFilterRow(state)
  state.root = el('div', { className: 'epscs-root' }, [filterRow, state.countEl, state.listEl])
  attachDomWidget(state)
  renderList(state) // initial paint -- "Loading checkpoints…" until the fetch resolves
}

/**
 * Wraps `node.addDOMWidget`. Fill-style (getMinHeight only, no
 * getMaxHeight): litegraph's widget-arrange pass gives this widget whatever
 * vertical space remains -- notebook.js's own single DOM widget, the file
 * this panel's machinery is modeled on, uses the identical shape.
 */
function attachDomWidget(state) {
  installMinWidth(state.node, MIN_NODE_WIDTH)
  const domWidget = state.node.addDOMWidget(PANEL_WIDGET_NAME, PANEL_WIDGET_TYPE, state.root, {
    hideOnZoom: true,
    serialize: false, // excludes from the API prompt (utils/executionUtil.ts)
    getMinHeight: () => MIN_WIDGET_HEIGHT
  })
  // Excludes from the workflow JSON -- a DIFFERENT flag from options.serialize
  // above (notebook.js's attachDomWidget() header explains why both exist).
  domWidget.serialize = false
  domWidget.serializeValue = () => undefined
  return domWidget
}

// --- Row model -- folder split, filter, grouping ---

/** @param {string} name @returns {{folder: string, base: string}} */
function splitFolder(name) {
  const idx = name.lastIndexOf('/')
  if (idx === -1) return { folder: '', base: name }
  return { folder: name.slice(0, idx), base: name.slice(idx + 1) }
}

/** Filename with its final extension segment removed (the title tooltip
 * keeps the full relative path -- only the on-row label is shortened). */
function stripExtension(base) {
  const idx = base.lastIndexOf('.')
  return idx <= 0 ? base : base.slice(0, idx)
}

/** One row per fetched checkpoint, PLUS one row per selected-but-missing
 * name (file header). Order: fetched (server) order first, then any missing
 * entries in `state.selection`'s own order. */
function buildRows(state) {
  const checkedSet = new Set(state.selection)
  const rows = state.checkpoints.map((name) => ({
    name,
    ...splitFolder(name),
    missing: false,
    checked: checkedSet.has(name)
  }))
  for (const name of state.selection) {
    if (!state.checkpointSet.has(name)) rows.push({ name, ...splitFolder(name), missing: true, checked: true })
  }
  return rows
}

function filterRows(rows, filterText) {
  const needle = filterText.trim().toLowerCase()
  return needle ? rows.filter((row) => row.name.toLowerCase().includes(needle)) : rows
}

/** Buckets *rows* by folder, preserving each folder's FIRST-appearance order
 * (the fetched list's own order, not alphabetical); a flat (no-folder) row
 * is its own ungrouped bucket ('') and renders without a header, giving the
 * "flat list otherwise" behavior for free. */
function groupRows(rows) {
  const order = []
  const groups = new Map()
  for (const row of rows) {
    if (!groups.has(row.folder)) {
      groups.set(row.folder, [])
      order.push(row.folder)
    }
    groups.get(row.folder).push(row)
  }
  return order.map((folder) => ({ folder, rows: groups.get(folder) }))
}

// --- Rendering ---

function buildRowEl(state, row) {
  const checkbox = el('input', { attrs: { type: 'checkbox' } })
  checkbox.checked = row.checked
  checkbox.addEventListener('change', () => onRowToggle(state, row.name, checkbox.checked))
  const label = el('span', {
    className: row.missing ? 'epscs-row-label epscs-row-missing' : 'epscs-row-label',
    text: row.missing ? `⚠ ${stripExtension(row.base)}` : stripExtension(row.base)
  })
  return el('label', { className: 'epscs-row', attrs: { title: row.name } }, [checkbox, label])
}

function renderCount(state) {
  if (!state.loaded || state.error) {
    state.countEl.textContent = ''
    return
  }
  const validSelected = state.selection.filter((name) => state.checkpointSet.has(name)).length
  const missing = state.selection.length - validSelected
  const suffix = missing > 0 ? ` (${missing} missing)` : ''
  state.countEl.textContent = `${validSelected} of ${state.checkpoints.length} selected${suffix}`
}

/** Single entry point that owns `state.listEl`'s entire content -- loading,
 * error+Retry, empty, no-matches, or the grouped checkbox rows -- so every
 * caller (filter input, toggle, fetch resolve/fail, onConfigure) can just
 * call this and get a fully consistent repaint. */
function renderList(state) {
  state.listEl.replaceChildren()

  if (state.error) {
    const message = el('div', { className: 'epscs-error-text', text: state.error })
    const retryBtn = el('button', { className: 'epscs-retry-btn', text: 'Retry' })
    retryBtn.addEventListener('click', () => {
      loadCheckpoints(state).catch((error) => console.warn(PREFIX, 'retry failed', error))
    })
    state.listEl.append(el('div', { className: 'epscs-error-row' }, [message, retryBtn]))
    renderCount(state)
    return
  }

  if (!state.loaded) {
    state.listEl.append(el('div', { className: 'epscs-empty', text: 'Loading checkpoints…' }))
    renderCount(state)
    return
  }

  const allRows = buildRows(state)
  if (allRows.length === 0) {
    state.listEl.append(el('div', { className: 'epscs-empty', text: 'No checkpoints found' }))
    renderCount(state)
    return
  }

  const rows = filterRows(allRows, state.filterText)
  if (rows.length === 0) {
    state.listEl.append(el('div', { className: 'epscs-empty', text: 'No matching checkpoints' }))
  } else {
    for (const group of groupRows(rows)) {
      if (group.folder) state.listEl.append(el('div', { className: 'epscs-group-header', text: group.folder }))
      for (const row of group.rows) state.listEl.append(buildRowEl(state, row))
    }
  }
  renderCount(state)
}

// --- Data flow -- widget <-> state, and the restore/fetch race (file header) ---

/** Re-derives `state.selection` from the widget's CURRENT value and
 * repaints -- the shared reconciliation step both the fetch-completion path
 * and wireConfigureReload call. */
function reloadFromWidget(state) {
  state.selection = selectionFromWidgetValue(state.widget.value)
  renderList(state)
}

/**
 * Writes *nextSelection* to the `selection` widget, but NOT as-is: it is
 * first re-sorted to the fetched list's own order (known names filtered
 * from `state.checkpoints`, in that order), with any still-checked but
 * unknown ("missing") names appended after, preserving their prior relative
 * order -- see the file header. `state.selection` is updated to match what
 * was written so the two never drift apart.
 */
function writeSelectionWidget(state, nextSelection) {
  const checkedSet = new Set(nextSelection)
  const known = state.checkpoints.filter((name) => checkedSet.has(name))
  const missing = nextSelection.filter((name) => !state.checkpointSet.has(name))
  const ordered = [...known, ...missing]
  state.selection = ordered
  const json = JSON.stringify(ordered)
  state.widget.value = json
  state.widget.callback?.(json)
  state.node.graph?.setDirtyCanvas(true, true)
}

function onRowToggle(state, name, checked) {
  writeSelectionWidget(state, toggleName(state.selection, name, checked))
  renderList(state)
}

async function loadCheckpoints(state) {
  state.error = null
  const token = ++state.loadToken
  renderList(state) // shows "Loading checkpoints…" (state.loaded may already be true on a retry)

  try {
    const response = await api.fetchApi(ROUTE)
    if (token !== state.loadToken) return // superseded by a newer fetch

    let data = null
    try {
      data = await response.json()
    } catch {
      throw new Error(`Unexpected response (HTTP ${response.status})`)
    }
    if (!response.ok) {
      throw new Error(data && typeof data.error === 'string' ? data.error : `HTTP ${response.status}`)
    }

    const list = Array.isArray(data?.checkpoints)
      ? data.checkpoints.filter((entry) => typeof entry === 'string')
      : []
    if (token !== state.loadToken) return

    state.checkpoints = list
    state.checkpointSet = new Set(list)
    state.loaded = true
    state.error = null
    reloadFromWidget(state) // race-safe reconcile -- see file header
  } catch (error) {
    if (token !== state.loadToken) return
    state.error = (error && error.message) || 'Failed to load checkpoints'
    console.warn(PREFIX, 'checkpoint fetch failed', error)
    renderList(state)
  }
}

/**
 * Wraps `node.onConfigure` -- chained, never replaced, since core or another
 * extension may already own it. THE fix for the restore race: see the file
 * header's "Restore-correctness" paragraph.
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
        console.warn(PREFIX, 'original onConfigure threw', error)
      }
    }
    try {
      reloadFromWidget(state)
    } catch (error) {
      console.warn(PREFIX, 'post-configure resync failed', error)
    }
    return result
  }
}

// --- Public entry points (called from web/eps_image.js) ---

/** Frontend-only one-time setup; kept as an export because eps_image.js
 * calls it unconditionally, matching switcher.js/distributor.js's identical
 * no-op init(). Everything else here is per-instance, done in attach(). */
export function init() {}

/** Per-node-instance attach; no-op unless *node* is an EPSCheckpointSwitcher.
 * Never throws -- every failure is logged via console.warn and leaves the
 * node's plain (visible, if hiding failed) `selection` widget functional. */
export function attach(node) {
  try {
    if (!node) return
    if (nodeClassOf(node) !== CLASS_ID) return
    if (attachedNodes.has(node)) return
    if (typeof node.addDOMWidget !== 'function') {
      console.warn(PREFIX, 'this ComfyUI frontend has no addDOMWidget; checkpoint panel not attached')
      return
    }
    const widget = findWidget(node, SELECTION_WIDGET_NAME)
    if (!widget) {
      console.warn(PREFIX, 'EPSCheckpointSwitcher node is missing its `selection` widget; panel not attached')
      return
    }
    attachedNodes.add(node)

    const state = createState(node, widget)
    state.selection = selectionFromWidgetValue(widget.value)
    hideSelectionWidget(state)
    buildUi(state)
    wireConfigureReload(state)

    loadCheckpoints(state).catch((error) => console.warn(PREFIX, 'initial checkpoint load failed', error))
  } catch (error) {
    console.warn(PREFIX, 'attach failed', error)
  }
}
