/**
 * @file EPS LoRA Picker frontend panel (FORMAT.md §6.13, M1 scope). Exports
 * `attachPickerPanel(node)`, called from `web/lora_library.js`'s
 * `nodeCreated`; no-ops for every node type other than `EPSLoraPicker`.
 *
 * Contract (backend built in parallel against this exact shape): the node's
 * one real, serialized widget is `selection` -- a STRING holding
 * `{"scope": "<folder, forward slashes, "" = whole library>", "loras":
 * [{"file", "on", "strength", "strength_clip"?}]}` (§6.13's single-JSON
 * restore-proof widget). This file hides that widget (both the canvas
 * `.hidden` flag and the Vue-nodes `options.hidden` mirror -- FORMAT.md
 * §7.5) and replaces it with a scope chip, a Selected list, a
 * folder-drilldown browser with ★ Favorites / 🕘 Recent pseudo-folders, and
 * a §7.2-style status line.
 *
 * **Selected renders FROM THE WIDGET VALUE ALONE** (the v0.52.1
 * "load-failure is a value-preserving error state" amendment, §7.2, as a
 * design rule): a failed `GET /lora_library/picker` shows its error + Retry
 * in the status line while the saved selection keeps rendering untouched.
 * A selected row the served list can't confirm gets a ⚠ marker (§6.12's
 * convention) but stays fully functional -- toggle, strength, remove.
 *
 * **The folder tree is derived client-side** from the flat `GET picker`
 * list: backslashes normalized to forward slashes (§4's separator rule),
 * split on `/` -- no tree route, matching how rgthree's own chooser gets
 * nested paths. Drill-down position and the Favorites/Recent views are
 * transient view state; only `selection.scope` persists (§6.13: scope is
 * per-workflow, execution ignores it).
 *
 * **Restore-correctness** (this repo's most-burned lesson --
 * `web/lora_library/notebook.js`'s `wireConfigureReload` header, FORMAT.md
 * §7.2): litegraph restores `widgets_values` AFTER attach runs and assigns
 * `widget.value` directly -- no callback fires. `wireConfigureReload` wraps
 * `node.onConfigure` (which fires after that restore, for a workflow load
 * AND a paste) and re-parses `selection` then. That same re-parse
 * (`reloadFromWidget`) is ALSO what the initial fetch runs on resolve, so
 * whichever of {fetch, onConfigure} finishes LAST produces the final
 * render -- neither ordering leaves stale rows on screen. The fetch itself
 * is token-guarded (checkpoint_switcher.js's `loadToken` idiom) so a slow
 * LAN response can never double-render or clobber a newer state.
 *
 * No window-level listeners: every interaction is a plain element-level
 * click/change/input, so §7.5's capture-phase requirement never comes up.
 */

import { app } from '../../../scripts/app.js'
import * as api from './api.js'

/** Frozen once shipped -- mirrors the Python node's class id (§6.13). */
export const CLASS_ID = 'EPSLoraPicker'
export const SELECTION_WIDGET_NAME = 'selection'

/** §5 route family. CLEAR_RECENTS is §6.13-M3's Clear-recents button --
 * declared with its siblings so the family lives in one block, unused by
 * this M1 panel. */
export const ROUTE = '/lora_library/picker'
export const ROUTE_FAVORITE = '/lora_library/picker/favorite'
export const ROUTE_RECENT = '/lora_library/picker/recent'
export const ROUTE_CLEAR_RECENTS = '/lora_library/picker/clear_recents'

const PANEL_WIDGET_NAME = 'eps_lp_panel'
const PANEL_WIDGET_TYPE = 'eps_lora_picker_panel'

/** Fill-style DOM widget: floor only, no ceiling -- litegraph's
 * widget-arrange pass gives it whatever height is left, matching
 * checkpoint_switcher.js / notebook.js. */
const MIN_WIDGET_HEIGHT = 220

/** ~360px floor (§6.13) -- same Linux-font-overflow rationale as every
 * DOM-widget node in this pack (FORMAT.md §7.2); self-contained
 * installMinWidth copy below, own guard flag. */
const MIN_NODE_WIDTH = 360

const STYLE_TAG_ID = 'eps-lora-picker-styles'

const STRENGTH_MIN = -10
const STRENGTH_MAX = 10

/** Client mirror of the store's newest-first cap (§6.13) -- only the
 * optimistic local update uses it; the server list wins when it lands. */
const RECENTS_CAP = 30

/** Nodes we've already attached to -- guards a double `nodeCreated`. */
const attachedNodes = new WeakSet()

// --- Pure helpers -- exported so tests can drive them under Node instead of
// source-grepping (checkpoint_switcher.js's selectionFromWidgetValue
// precedent). ---

/** §4 separator rule: a selection saved on Windows must match on POSIX.
 * @param {string} name @returns {string} */
export function normalizeLoraName(name) {
  return name.replace(/\\/g, '/')
}

/**
 * Clamps to the §6.13 strength range. Non-numeric / non-finite input
 * degrades to *fallback* rather than throwing or writing NaN into the
 * widget JSON.
 * @param {unknown} value
 * @param {number} [fallback]
 * @returns {number}
 */
export function clampStrength(value, fallback = 1) {
  // Only a real number or a non-blank numeric string counts -- Number(null)
  // is 0, which would silently zero a null strength instead of defaulting.
  const num =
    typeof value === 'number'
      ? value
      : typeof value === 'string' && value.trim() !== ''
        ? Number(value)
        : NaN
  if (!Number.isFinite(num)) return fallback
  return Math.min(STRENGTH_MAX, Math.max(STRENGTH_MIN, num))
}

/**
 * Parses the `selection` widget's raw string into `{scope, loras}`. Never
 * throws: malformed JSON or a wrong-shaped value degrades to the empty
 * selection with a console warn (§6.13: never fail on view state). Rows
 * are name-normalized, deduped by file (first occurrence wins), `on`
 * defaults true, strengths clamped, `strength_clip` kept only when it is a
 * finite number (null = "follow strength", the M1 default).
 * @param {unknown} raw
 * @returns {{scope: string, loras: Array<{file: string, on: boolean, strength: number, strength_clip: number|null}>}}
 */
export function selectionFromWidgetValue(raw) {
  const empty = { scope: '', loras: [] }
  if (typeof raw !== 'string' || raw.trim() === '') return empty
  let parsed
  try {
    parsed = JSON.parse(raw)
  } catch (error) {
    api.warn('unparseable picker selection widget; treating as empty', error)
    return empty
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    api.warn('wrong-shaped picker selection widget; treating as empty')
    return empty
  }
  const scope = typeof parsed.scope === 'string' ? normalizeLoraName(parsed.scope) : ''
  const rows = Array.isArray(parsed.loras) ? parsed.loras : []
  const seen = new Set()
  const loras = []
  for (const row of rows) {
    if (!row || typeof row !== 'object' || typeof row.file !== 'string' || row.file === '') continue
    const file = normalizeLoraName(row.file)
    if (seen.has(file)) continue
    seen.add(file)
    loras.push({
      file,
      on: row.on !== false,
      strength: clampStrength(row.strength),
      strength_clip:
        typeof row.strength_clip === 'number' && Number.isFinite(row.strength_clip)
          ? clampStrength(row.strength_clip)
          : null
    })
  }
  return { scope, loras }
}

/**
 * Serializes with STABLE key order -- `{scope, loras}`, each row
 * `{file, on, strength, strength_clip?}` with `strength_clip` OMITTED when
 * null (§6.13; the M1 UI never sets it, but a row restored with one keeps
 * it). Stable order keeps the saved workflow diff-clean across edits.
 * @param {{scope?: unknown, loras?: unknown}} selection
 * @returns {string}
 */
export function serializeSelection(selection) {
  const scope = typeof selection?.scope === 'string' ? selection.scope : ''
  const rows = Array.isArray(selection?.loras) ? selection.loras : []
  const loras = rows.map((row) => {
    const out = { file: row.file, on: row.on !== false, strength: clampStrength(row.strength) }
    if (typeof row.strength_clip === 'number' && Number.isFinite(row.strength_clip)) {
      out.strength_clip = clampStrength(row.strength_clip)
    }
    return out
  })
  return JSON.stringify({ scope, loras })
}

/** Case-insensitive A-Z with a deterministic case-sensitive tiebreak. */
function compareCi(a, b) {
  const al = a.toLowerCase()
  const bl = b.toLowerCase()
  if (al < bl) return -1
  if (al > bl) return 1
  if (a < b) return -1
  if (a > b) return 1
  return 0
}

/**
 * One level of the client-derived tree (file header): the direct
 * subfolders of *folder* (with RECURSIVE lora counts) and the loras
 * directly inside it. Folders first A-Z case-insensitive, then loras A-Z
 * (§6.13's listing order). *folder* `''` = the whole-library root.
 * @param {string[]} loras - normalized served names
 * @param {string} folder - `''` or `a/b` (no trailing slash)
 * @returns {{folders: Array<{name: string, count: number, path: string}>, loras: Array<{file: string, label: string}>}}
 */
export function listFolder(loras, folder) {
  const prefix = folder ? `${folder}/` : ''
  const folderCounts = new Map()
  const files = []
  for (const name of loras) {
    if (prefix && !name.startsWith(prefix)) continue
    const rest = name.slice(prefix.length)
    const slash = rest.indexOf('/')
    if (slash === -1) {
      files.push({ file: name, label: rest })
    } else {
      const head = rest.slice(0, slash)
      folderCounts.set(head, (folderCounts.get(head) || 0) + 1)
    }
  }
  const folders = [...folderCounts.entries()]
    .map(([name, count]) => ({ name, count, path: `${prefix}${name}` }))
    .sort((a, b) => compareCi(a.name, b.name))
  files.sort((a, b) => compareCi(a.label, b.label))
  return { folders, loras: files }
}

/** @param {string} name @returns {string} */
function basename(name) {
  const idx = name.lastIndexOf('/')
  return idx === -1 ? name : name.slice(idx + 1)
}

// --- Tiny DOM builder (identical helper to every sibling web module --
// controller.js's el() header explains the duplicated-by-hand convention). ---

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

/**
 * A ComfyUI toast -- local copy of notebook.js's `toast()` / controller.js's
 * `_toast()` (same "duplicated by hand, no cross-module coupling"
 * convention as `el()` above). Fails soft: an older frontend without
 * extensionManager.toast simply logs.
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

// --- Styles -- one injected <style> tag, guarded against duplicate
// injection; same ComfyUI theme variables + literal fallbacks as
// checkpoint_switcher.js, under the `eps-lp-` prefix. ---

let stylesInjected = false

const CSS_TEXT = `
.eps-lp-root { display: flex; flex-direction: column; width: 100%; height: 100%; box-sizing: border-box; overflow: hidden; background: var(--comfy-input-bg, #1e1e1e); border: 1px solid var(--border-color, #444); border-radius: 4px; font-family: inherit; font-size: 11px; color: var(--input-text, #ccc); }
.eps-lp-scope { flex: 0 0 auto; display: flex; align-items: center; gap: 6px; padding: 4px 6px; border-bottom: 1px solid var(--border-color, #444); }
.eps-lp-scope-chip { flex: 1 1 auto; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--descrip-text, #999); }
.eps-lp-scope-chip-active { color: var(--input-text, #ccc); }
.eps-lp-icon-btn { flex: 0 0 auto; background: none; border: none; color: var(--descrip-text, #999); cursor: pointer; padding: 0 3px; font-size: 12px; line-height: 1; font-family: inherit; }
.eps-lp-icon-btn:hover { color: var(--input-text, #ccc); }
.eps-lp-section-header { flex: 0 0 auto; padding: 4px 6px 2px; font-size: 9.5px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; color: var(--descrip-text, #999); }
.eps-lp-selected-list { flex: 0 1 auto; max-height: 132px; overflow-y: auto; overflow-x: hidden; padding: 0 3px 4px; border-bottom: 1px solid var(--border-color, #444); }
.eps-lp-row { display: flex; align-items: center; gap: 6px; padding: 2px 6px; border-radius: 3px; user-select: none; }
.eps-lp-row:hover { background: var(--content-hover-bg, #2a2a2a); }
.eps-lp-row input[type="checkbox"] { flex: 0 0 auto; margin: 0; cursor: pointer; }
.eps-lp-row-label { flex: 1 1 auto; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.eps-lp-row-missing { color: var(--error-text, #ff4444); font-style: italic; }
.eps-lp-row-ghost { opacity: 0.55; }
.eps-lp-strength { flex: 0 0 auto; width: 56px; box-sizing: border-box; background: var(--comfy-menu-bg, #262626); border: 1px solid var(--border-color, #444); color: var(--input-text, #ccc); border-radius: 3px; padding: 1px 3px; font-size: 11px; font-family: inherit; }
.eps-lp-browser { flex: 1 1 auto; display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
.eps-lp-crumbs { flex: 0 0 auto; display: flex; align-items: center; flex-wrap: wrap; gap: 2px; padding: 3px 6px; border-bottom: 1px solid var(--border-color, #444); color: var(--descrip-text, #999); }
.eps-lp-crumb { background: none; border: none; padding: 0 2px; color: var(--input-text, #ccc); cursor: pointer; font-size: 11px; font-family: inherit; }
.eps-lp-crumb:hover { text-decoration: underline; }
.eps-lp-crumb-sep { color: var(--descrip-text, #999); }
.eps-lp-list { flex: 1 1 auto; min-height: 0; overflow-y: auto; overflow-x: hidden; padding: 2px 3px 4px; }
.eps-lp-folder-row { cursor: pointer; }
.eps-lp-count { flex: 0 0 auto; color: var(--descrip-text, #999); font-size: 10px; }
.eps-lp-btn { flex: 0 0 auto; background: var(--comfy-menu-bg, #262626); border: 1px solid var(--border-color, #444); color: var(--input-text, #ccc); border-radius: 3px; padding: 1px 6px; font-size: 10px; font-family: inherit; cursor: pointer; white-space: nowrap; }
.eps-lp-btn:hover { background: var(--content-hover-bg, #2a2a2a); }
.eps-lp-star { flex: 0 0 auto; background: none; border: none; cursor: pointer; font-size: 12px; line-height: 1; padding: 0 2px; color: var(--descrip-text, #999); font-family: inherit; }
.eps-lp-star-on { color: #f0c420; }
.eps-lp-empty { padding: 8px 7px; color: var(--descrip-text, #999); font-style: italic; }
.eps-lp-status { flex: 0 0 auto; display: flex; align-items: center; gap: 8px; padding: 3px 6px; border-top: 1px solid var(--border-color, #444); min-height: 20px; box-sizing: border-box; }
.eps-lp-status-text { flex: 1 1 auto; min-width: 0; color: var(--descrip-text, #999); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.eps-lp-status-error { color: var(--error-text, #ff4444); }
.eps-lp-flash { animation: eps-lp-flash 0.9s ease-out; }
@keyframes eps-lp-flash { from { background: rgba(66, 133, 244, 0.45); } to { background: transparent; } }
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
// controller.js's identical-shape installMinWidth for the house pattern). ---

function installMinWidth(node, minWidth) {
  if (!node || node.__epsLpMinWidthInstalled) return
  node.__epsLpMinWidthInstalled = true
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
    selection: { scope: '', loras: [] }, // source of truth mirrored from the widget
    loras: [], // served list, normalized -- [] until the first load resolves
    loraSet: new Set(),
    favorites: [], // store order (§6.13)
    recents: [], // newest first (§6.13)
    loaded: false,
    error: null,
    view: 'browse', // 'browse' | 'favorites' | 'recent' -- transient, never serialized
    path: [], // drill-down segments below the scope root -- transient (§6.13)
    loadToken: 0, // guards a stale/superseded fetch from clobbering fresher state
    favoriteToken: 0, // same guard for the optimistic favorite round-trips
    recentToken: 0, // and for the fire-and-forget recents stamps
    root: null,
    scopeRowEl: null,
    selectedHeaderEl: null,
    selectedListEl: null,
    crumbsEl: null,
    listEl: null,
    statusTextEl: null,
    statusActionsEl: null,
    selectedRowEls: new Map() // file -> row element, for duplicate-Add scroll+flash
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

function buildUi(state) {
  injectStyles()
  state.scopeRowEl = el('div', { className: 'eps-lp-scope' })
  state.selectedHeaderEl = el('div', { className: 'eps-lp-section-header' })
  state.selectedListEl = el('div', { className: 'eps-lp-selected-list' })
  state.crumbsEl = el('div', { className: 'eps-lp-crumbs' })
  state.listEl = el('div', { className: 'eps-lp-list' })
  state.statusTextEl = el('div', { className: 'eps-lp-status-text' })
  state.statusActionsEl = el('div', { className: 'eps-lp-status-actions' })
  const browser = el('div', { className: 'eps-lp-browser' }, [state.crumbsEl, state.listEl])
  const status = el('div', { className: 'eps-lp-status' }, [state.statusTextEl, state.statusActionsEl])
  state.root = el('div', { className: 'eps-lp-root' }, [
    state.scopeRowEl,
    state.selectedHeaderEl,
    state.selectedListEl,
    browser,
    status
  ])
  attachDomWidget(state)
  render(state) // initial paint -- "Loading loras…" until the fetch resolves
}

/**
 * Wraps `node.addDOMWidget`. Fill-style (getMinHeight only, no
 * getMaxHeight) -- checkpoint_switcher.js's attachDomWidget(), the shape
 * this panel's machinery is modeled on.
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

// --- Data flow -- widget <-> state, and the restore/fetch race (file header) ---

/** Re-derives `state.selection` from the widget's CURRENT value and
 * repaints -- the shared reconciliation step both the fetch-completion path
 * and wireConfigureReload call. */
function reloadFromWidget(state) {
  // BEFORE the re-parse, not just in render(): the commit must land in the
  // widget JSON so the fresh parse below picks the typed value up -- the
  // in-render commit alone would mutate a row object this line discards.
  commitActiveStrengthEdit(state)
  state.selection = selectionFromWidgetValue(state.widget.value)
  state.path = [] // a restored scope invalidates any drill-down into the old one
  state.view = 'browse'
  render(state)
}

/** Serializes `state.selection` back into the hidden widget -- every
 * mutation (toggle, strength, remove, Add, scope pin/clear) funnels
 * through here so the widget and the panel can never drift apart. */
function writeSelectionWidget(state) {
  const json = serializeSelection(state.selection)
  state.widget.value = json
  state.widget.callback?.(json)
  state.node.graph?.setDirtyCanvas(true, true)
}

async function loadPicker(state) {
  state.error = null
  const token = ++state.loadToken
  render(state) // shows "Loading loras…" (state.loaded may already be true on a retry)

  try {
    const data = await api.getJson(ROUTE)
    if (token !== state.loadToken) return // superseded by a newer fetch

    state.loras = (Array.isArray(data?.loras) ? data.loras : [])
      .filter((entry) => typeof entry === 'string')
      .map(normalizeLoraName)
    state.loraSet = new Set(state.loras)
    state.favorites = (Array.isArray(data?.favorites) ? data.favorites : [])
      .filter((entry) => typeof entry === 'string')
      .map(normalizeLoraName)
    state.recents = sanitizeRecents(data?.recents)
    state.loaded = true
    state.error = null
    reloadFromWidget(state) // race-safe reconcile -- see file header
  } catch (error) {
    if (token !== state.loadToken) return
    state.error = (error && error.message) || 'Failed to load loras'
    api.warn('picker feed fetch failed', error)
    render(state)
  }
}

/** @param {unknown} raw @returns {Array<{file: string, ts: number}>} */
function sanitizeRecents(raw) {
  if (!Array.isArray(raw)) return []
  const out = []
  for (const entry of raw) {
    if (!entry || typeof entry !== 'object' || typeof entry.file !== 'string') continue
    out.push({ file: normalizeLoraName(entry.file), ts: typeof entry.ts === 'number' ? entry.ts : 0 })
  }
  return out
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
        api.warn('original onConfigure threw', error)
      }
    }
    try {
      reloadFromWidget(state)
    } catch (error) {
      api.warn('post-configure resync failed', error)
    }
    return result
  }
}

// --- Mutations ---

/** Sets the per-workflow scope (§6.13: pure view state, serialized with
 * the workflow, execution ignores it) and resets the browse position. */
function setScope(state, scopePath) {
  state.selection = { ...state.selection, scope: scopePath }
  state.path = []
  state.view = 'browse'
  writeSelectionWidget(state)
  render(state)
}

/**
 * `＋ Add`: appends `{file, on: true, strength: 1}` if absent -- a
 * duplicate Add scrolls to and flashes the existing Selected row instead
 * (§6.13). Recents record at SELECTION time (the roadmap's decided M1
 * recording point), fire-and-forget.
 */
function addLora(state, file) {
  const existing = state.selection.loras.find((row) => row.file === file)
  if (existing) {
    flashSelectedRow(state, file)
    return
  }
  state.selection.loras.push({ file, on: true, strength: 1, strength_clip: null })
  writeSelectionWidget(state)
  renderSelected(state)
  recordRecent(state, file)
}

function flashSelectedRow(state, file) {
  const rowEl = state.selectedRowEls.get(file)
  if (!rowEl) return
  rowEl.scrollIntoView({ block: 'nearest' })
  rowEl.classList.remove('eps-lp-flash')
  void rowEl.offsetWidth // restart the animation on a repeat flash
  rowEl.classList.add('eps-lp-flash')
}

/**
 * Fire-and-forget recents stamp (§6.13): the local list is updated
 * optimistically so the `🕘 Recent (n)` count is right immediately, the
 * POST failure is a console warn only -- it never blocks or reverts the
 * Add itself. Token-guarded so a slow response can't clobber a newer stamp.
 */
function recordRecent(state, file) {
  state.recents = [
    { file, ts: Date.now() / 1000 },
    ...state.recents.filter((entry) => entry.file !== file)
  ].slice(0, RECENTS_CAP)
  renderBrowser(state)
  const token = ++state.recentToken
  api
    .postJson(ROUTE_RECENT, { files: [file] })
    .then((data) => {
      if (token !== state.recentToken) return
      const recents = sanitizeRecents(data?.recents)
      if (recents.length) {
        state.recents = recents
        renderBrowser(state)
      }
    })
    .catch((error) => api.warn('recording recent lora failed (non-blocking)', error))
}

/**
 * ★/☆: optimistic flip + POST, revert + toast on failure (§6.13).
 * Token-guarded: a stale response (or a stale failure's revert) after a
 * newer click is dropped rather than clobbering the newer state.
 */
async function toggleFavorite(state, file, on) {
  const previous = state.favorites.slice()
  state.favorites = on ? [...previous.filter((name) => name !== file), file] : previous.filter((name) => name !== file)
  renderBrowser(state)
  const token = ++state.favoriteToken
  try {
    const data = await api.postJson(ROUTE_FAVORITE, { file, on })
    if (token !== state.favoriteToken) return
    if (Array.isArray(data?.favorites)) {
      state.favorites = data.favorites.filter((entry) => typeof entry === 'string').map(normalizeLoraName)
      renderBrowser(state)
    }
  } catch (error) {
    if (token !== state.favoriteToken) return
    state.favorites = previous
    renderBrowser(state)
    api.warn('favorite toggle failed', error)
    toast('error', 'EPS LoRA Picker', `Favorite not saved: ${error?.message || error}`)
  }
}

// --- Rendering ---

/** A strength edit in progress when a repaint lands (slow fetch completing,
 * a configure-driven reload) must be committed FIRST: `replaceChildren()`
 * destroys the focused input without firing change OR blur, so the typed
 * value would silently revert (review 2026-08-09). */
function commitActiveStrengthEdit(state) {
  const active = document.activeElement
  if (!active || !state.selectedListEl || !state.selectedListEl.contains(active)) return
  if (typeof active._epsCommit === 'function') active._epsCommit()
}

/** Full repaint -- every section owns its own container, so granular
 * callers (strength commit, star flip) can repaint just theirs. */
function render(state) {
  commitActiveStrengthEdit(state)
  renderScope(state)
  renderSelected(state)
  renderBrowser(state)
  renderStatus(state)
}

function renderScope(state) {
  const scope = state.selection.scope
  state.scopeRowEl.replaceChildren()
  const chip = el('span', {
    className: scope ? 'eps-lp-scope-chip eps-lp-scope-chip-active' : 'eps-lp-scope-chip',
    text: scope ? `📁 ${scope}` : 'Whole library',
    attrs: {
      title: scope
        ? `${scope} — this workflow browses only this folder and its subfolders.`
        : 'No scope set — this workflow browses the whole lora library.'
    }
  })
  state.scopeRowEl.append(chip)
  if (scope) {
    const clearBtn = el('button', {
      className: 'eps-lp-icon-btn',
      text: '✕',
      attrs: { title: 'Clear scope — browse the whole library again' }
    })
    clearBtn.addEventListener('click', () => setScope(state, ''))
    state.scopeRowEl.append(clearBtn)
  }
}

function renderSelected(state) {
  const rows = state.selection.loras
  state.selectedHeaderEl.textContent = `Selected (${rows.length})`
  state.selectedListEl.replaceChildren()
  state.selectedRowEls.clear()
  if (rows.length === 0) {
    state.selectedListEl.append(
      el('div', { className: 'eps-lp-empty', text: 'Nothing selected — Add loras from the browser below.' })
    )
    return
  }
  for (const row of rows) state.selectedListEl.append(buildSelectedRowEl(state, row))
}

/** One Selected row -- rendered from the widget value alone (file header);
 * `missing` only ever set once the served list has actually loaded, since
 * before then nothing can be confirmed OR denied. */
function buildSelectedRowEl(state, row) {
  const missing = state.loaded && !state.loraSet.has(row.file)

  const checkbox = el('input', { attrs: { type: 'checkbox', title: 'On/off — an off row stays saved but is skipped at run time.' } })
  checkbox.checked = row.on
  checkbox.addEventListener('change', () => {
    row.on = checkbox.checked
    writeSelectionWidget(state)
  })

  const label = el('span', {
    className: missing ? 'eps-lp-row-label eps-lp-row-missing' : 'eps-lp-row-label',
    text: missing ? `⚠ ${basename(row.file)}` : basename(row.file),
    attrs: {
      title: missing ? `${row.file} — not found on this machine's lora list` : row.file
    }
  })

  const strength = el('input', {
    className: 'eps-lp-strength',
    attrs: {
      type: 'number',
      min: String(STRENGTH_MIN),
      max: String(STRENGTH_MAX),
      step: '0.05',
      title: 'Model strength'
    }
  })
  strength.value = String(row.strength)
  const commitStrength = () => {
    const clamped = clampStrength(strength.value, row.strength)
    strength.value = String(clamped)
    row.strength = clamped
    writeSelectionWidget(state)
  }
  // Exposed for commitActiveStrengthEdit(): a full repaint may destroy this
  // input while it holds focus, which fires neither change nor blur.
  strength._epsCommit = commitStrength
  strength.addEventListener('change', commitStrength)
  strength.addEventListener('blur', commitStrength)
  // Canvas hotkeys (Delete/Ctrl+C/etc.) must not intercept typing here --
  // checkpoint_switcher.js's filter input does the same.
  strength.addEventListener('keydown', (event) => event.stopPropagation())

  const removeBtn = el('button', {
    className: 'eps-lp-icon-btn',
    text: '✕',
    attrs: { title: 'Remove from the selection' }
  })
  removeBtn.addEventListener('click', () => {
    state.selection.loras = state.selection.loras.filter((entry) => entry !== row)
    writeSelectionWidget(state)
    renderSelected(state)
  })

  const rowEl = el('div', { className: 'eps-lp-row' }, [checkbox, label, strength, removeBtn])
  state.selectedRowEls.set(row.file, rowEl)
  return rowEl
}

/** Breadcrumb segments for the current view: scope root (basename, or
 * "Library" unscoped) -> drill-down path, or root -> pseudo-folder. */
function renderCrumbs(state) {
  const scope = state.selection.scope
  state.crumbsEl.replaceChildren()
  const rootBtn = el('button', {
    className: 'eps-lp-crumb',
    text: scope ? basename(scope) : 'Library',
    attrs: { title: scope || 'Whole library' }
  })
  rootBtn.addEventListener('click', () => {
    state.path = []
    state.view = 'browse'
    renderBrowser(state)
  })
  state.crumbsEl.append(rootBtn)

  const tail =
    state.view === 'favorites' ? ['★ Favorites'] : state.view === 'recent' ? ['🕘 Recent'] : state.path
  tail.forEach((segment, index) => {
    state.crumbsEl.append(el('span', { className: 'eps-lp-crumb-sep', text: '›' }))
    const crumbBtn = el('button', { className: 'eps-lp-crumb', text: segment })
    crumbBtn.addEventListener('click', () => {
      if (state.view === 'browse') state.path = state.path.slice(0, index + 1)
      renderBrowser(state)
    })
    state.crumbsEl.append(crumbBtn)
  })
}

/** The browse folder the drill-down currently sits in: scope root plus the
 * transient path segments, `''` = whole-library root. */
function currentFolder(state) {
  const segments = []
  if (state.selection.scope) segments.push(state.selection.scope)
  segments.push(...state.path)
  return segments.join('/')
}

function renderBrowser(state) {
  renderCrumbs(state)
  state.listEl.replaceChildren()

  if (state.error) {
    // §7.2 amendment: the error itself lives in the status line (with
    // Retry); the browser just says why it is empty.
    state.listEl.append(el('div', { className: 'eps-lp-empty', text: 'Lora list unavailable — see below.' }))
    return
  }
  if (!state.loaded) {
    state.listEl.append(el('div', { className: 'eps-lp-empty', text: 'Loading loras…' }))
    return
  }

  if (state.view === 'favorites') {
    if (state.favorites.length === 0) {
      state.listEl.append(el('div', { className: 'eps-lp-empty', text: 'No favorites yet — star a lora to keep it here.' }))
      return
    }
    // Store order (§6.13) -- deliberately NOT re-sorted.
    for (const file of state.favorites) state.listEl.append(buildLoraRowEl(state, file, file))
    return
  }

  if (state.view === 'recent') {
    if (state.recents.length === 0) {
      state.listEl.append(el('div', { className: 'eps-lp-empty', text: 'No recently used loras yet.' }))
      return
    }
    // Newest first (§6.13) -- the served order, preserved.
    for (const entry of state.recents) state.listEl.append(buildLoraRowEl(state, entry.file, entry.file))
    return
  }

  if (state.path.length === 0) {
    state.listEl.append(buildPseudoFolderRowEl(state, `★ Favorites (${state.favorites.length})`, 'favorites'))
    state.listEl.append(buildPseudoFolderRowEl(state, `🕘 Recent (${state.recents.length})`, 'recent'))
  }

  const listing = listFolder(state.loras, currentFolder(state))
  if (listing.folders.length === 0 && listing.loras.length === 0) {
    state.listEl.append(el('div', { className: 'eps-lp-empty', text: 'No loras in this folder.' }))
    return
  }
  for (const folder of listing.folders) state.listEl.append(buildFolderRowEl(state, folder))
  for (const lora of listing.loras) state.listEl.append(buildLoraRowEl(state, lora.file, lora.label))
}

/** `★ Favorites (n)` / `🕘 Recent (n)` -- root-only views over ALL
 * favorites/recents, unfiltered by scope (§6.13). */
function buildPseudoFolderRowEl(state, text, view) {
  const label = el('span', { className: 'eps-lp-row-label', text })
  const rowEl = el('div', { className: 'eps-lp-row eps-lp-folder-row' }, [label])
  rowEl.addEventListener('click', () => {
    state.view = view
    renderBrowser(state)
  })
  return rowEl
}

function buildFolderRowEl(state, folder) {
  const label = el('span', {
    className: 'eps-lp-row-label',
    text: `📁 ${folder.name}`,
    attrs: { title: folder.path }
  })
  const count = el('span', { className: 'eps-lp-count', text: String(folder.count) })
  const pinBtn = el('button', {
    className: 'eps-lp-btn',
    text: 'Scope',
    attrs: { title: `Restrict this workflow's browsing to ${folder.path} (saved with the workflow)` }
  })
  pinBtn.addEventListener('click', (event) => {
    event.stopPropagation() // the row click underneath drills down instead
    setScope(state, folder.path)
  })
  const rowEl = el('div', { className: 'eps-lp-row eps-lp-folder-row' }, [label, count, pinBtn])
  rowEl.addEventListener('click', () => {
    state.path = [...state.path, folder.name]
    renderBrowser(state)
  })
  return rowEl
}

/**
 * One browser lora row: star toggle + display name (relative to the
 * current folder) + `＋ Add`. A GHOST -- a favorite/recent naming a lora
 * NOT on this machine's served list -- renders dimmed, ⚠-marked, and
 * star-only (§6.13: visible, never silently dropped; unstarring it here
 * is how a cross-machine favorite gets cleaned up).
 */
function buildLoraRowEl(state, file, displayLabel) {
  const ghost = !state.loraSet.has(file)
  const favorite = state.favorites.includes(file)

  const starBtn = el('button', {
    className: favorite ? 'eps-lp-star eps-lp-star-on' : 'eps-lp-star',
    text: favorite ? '★' : '☆',
    attrs: { title: favorite ? 'Unstar' : 'Star as a favorite' }
  })
  starBtn.addEventListener('click', () => {
    toggleFavorite(state, file, !favorite).catch((error) => api.warn('favorite toggle rejected', error))
  })

  const label = el('span', {
    className: ghost ? 'eps-lp-row-label eps-lp-row-missing' : 'eps-lp-row-label',
    text: ghost ? `⚠ ${displayLabel}` : displayLabel,
    attrs: { title: ghost ? `${file} — not installed here` : file }
  })

  const children = [starBtn, label]
  if (!ghost) {
    const addBtn = el('button', { className: 'eps-lp-btn', text: '＋ Add', attrs: { title: 'Add to the selection' } })
    addBtn.addEventListener('click', () => addLora(state, file))
    children.push(addBtn)
  }
  return el('div', { className: ghost ? 'eps-lp-row eps-lp-row-ghost' : 'eps-lp-row' }, children)
}

/** §7.2-style status line: the load error + Retry live HERE, so the
 * Selected section above never has to make room for (or be blanked by)
 * a fetch failure. */
function renderStatus(state) {
  state.statusActionsEl.replaceChildren()
  if (state.error) {
    state.statusTextEl.textContent = `Could not load lora list: ${state.error}`
    state.statusTextEl.classList.add('eps-lp-status-error')
    const retryBtn = el('button', { className: 'eps-lp-btn', text: 'Retry' })
    retryBtn.addEventListener('click', () => {
      loadPicker(state).catch((error) => api.warn('retry load failed', error))
    })
    state.statusActionsEl.append(retryBtn)
    return
  }
  state.statusTextEl.classList.remove('eps-lp-status-error')
  state.statusTextEl.textContent = state.loaded ? `${state.loras.length} loras on this machine` : 'Loading…'
}

// --- Public entry point (called from web/lora_library.js's nodeCreated) ---

/** Per-node-instance attach; no-op unless *node* is an EPSLoraPicker.
 * Never throws -- every failure is logged via api.warn and leaves the
 * node's plain (visible, if hiding failed) `selection` widget functional. */
export function attachPickerPanel(node) {
  try {
    if (!node) return
    if (nodeClassOf(node) !== CLASS_ID) return
    if (attachedNodes.has(node)) return
    if (typeof node.addDOMWidget !== 'function') {
      api.warn('this ComfyUI frontend has no addDOMWidget; lora picker panel not attached')
      return
    }
    const widget = findWidget(node, SELECTION_WIDGET_NAME)
    if (!widget) {
      api.warn('EPSLoraPicker node is missing its `selection` widget; panel not attached')
      return
    }
    attachedNodes.add(node)

    const state = createState(node, widget)
    state.selection = selectionFromWidgetValue(widget.value)
    hideSelectionWidget(state)
    buildUi(state)
    wireConfigureReload(state)

    loadPicker(state).catch((error) => api.warn('initial picker load failed', error))
  } catch (error) {
    api.warn('attachPickerPanel failed', error)
  }
}
