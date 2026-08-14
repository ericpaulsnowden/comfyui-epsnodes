/**
 * @file EPS LoRA Picker frontend panel (FORMAT.md §6.13, M1 + the M2
 * Send-to-loader row + the M3 search/thumbnails/trigger-copy/
 * clear-recents/favorites-reorder round). Exports
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
 * The M3 favorites drag included: pointerdown calls `setPointerCapture` on
 * the row's ≡ handle, which RETARGETS the whole gesture's later
 * pointermove/pointerup at the handle itself -- so element-level listeners
 * on the handle see the drag end even when the pointer leaves the node,
 * and no window listener (capture-phase or otherwise) is ever needed.
 */

import { app } from '../../../scripts/app.js'
import * as api from './api.js'
import * as pll from './pll_bridge.js'
import * as dasiwa from './dasiwa_bridge.js'

/** Frozen once shipped -- mirrors the Python node's class id (§6.13). */
export const CLASS_ID = 'EPSLoraPicker'
export const SELECTION_WIDGET_NAME = 'selection'

/** §5 route family, one block. M1: the GET feed + favorite/recent POSTs.
 * M3 wired the rest: CLEAR_RECENTS (the Recent view's armed Clear-recents
 * button -- no longer unused), PREVIEW (row thumbnails, an <img src>, the
 * one GET here that never goes through api.getJson), INFO (on-demand
 * trigger-word copy), FAVORITES_ORDER (drag-reorder's full-list replace). */
export const ROUTE = '/lora_library/picker'
export const ROUTE_FAVORITE = '/lora_library/picker/favorite'
export const ROUTE_RECENT = '/lora_library/picker/recent'
export const ROUTE_CLEAR_RECENTS = '/lora_library/picker/clear_recents'
export const ROUTE_PREVIEW = '/lora_library/picker/preview'
export const ROUTE_INFO = '/lora_library/picker/info'
export const ROUTE_FAVORITES_ORDER = '/lora_library/picker/favorites_order'

const PANEL_WIDGET_NAME = 'eps_lp_panel'
const PANEL_WIDGET_TYPE = 'eps_lora_picker_panel'

/** Fill-style DOM widget: floor only, no ceiling -- litegraph's
 * widget-arrange pass gives it whatever height is left, matching
 * checkpoint_switcher.js / notebook.js. */
const MIN_WIDGET_HEIGHT = 220

/** One Selected row's height (2px padding + ~22px content) -- the unit
 * both syncSelectedGrowth's node-growth delta and getMinHeight's floor
 * use, so the two can never disagree about the room the list needs. */
const SELECTED_ROW_PX = 26

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

/** §6.13 M3 Clear-recents confirm window (controller.js's armed-delete
 * `DELETE_CONFIRM_MS` precedent): the armed "Really clear?" state
 * auto-disarms after this many ms; any re-render disarms it sooner, since
 * the button is rebuilt fresh (its `_armed`/`_armTimer` die with the old
 * DOM node). */
const CLEAR_RECENTS_CONFIRM_MS = 4000

/** §6.13 M3: the copied-trigger-words success toast truncates past this
 * many chars -- the toast names the words, it doesn't reprint a novel. */
const COPY_TOAST_MAX_CHARS = 120

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

/**
 * §6.13 M3 search predicate -- §7.2's search semantics (notebook.js's
 * `entryMatchesSearch` precedent, the v0.53.0 owner ask): case-insensitive,
 * every whitespace-separated query word must appear somewhere in *relPath*,
 * so multi-word queries narrow (AND across words). *relPath* is the lora's
 * path RELATIVE TO THE CURRENT SCOPE -- the caller strips the scope prefix
 * BEFORE calling, so a scope folder's own name never matches everything
 * inside it. An empty/whitespace query matches everything (`every` over an
 * empty word list), though the UI never calls with one.
 * @param {string} relPath @param {string} query @returns {boolean}
 */
export function loraMatchesSearch(relPath, query) {
  const haystack = relPath.toLowerCase()
  return query
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((word) => haystack.includes(word))
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
.eps-lp-icon-btn { flex: 0 0 auto; background: none; border: none; color: var(--descrip-text, #999); cursor: pointer; padding: 0 3px; font-size: 12px; line-height: 1; font-family: inherit; }
.eps-lp-icon-btn:hover { color: var(--input-text, #ccc); }
.eps-lp-crumb-clear { margin-left: auto; }
.eps-lp-section-header { flex: 0 0 auto; padding: 4px 6px 2px; font-size: 9.5px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; color: var(--descrip-text, #999); }
.eps-lp-selected-list { flex: 0 0 auto; padding: 0 3px 4px; border-bottom: 1px solid var(--border-color, #444); }
.eps-lp-send { flex: 0 0 auto; display: flex; align-items: center; gap: 6px; padding: 4px 6px; border-bottom: 1px solid var(--border-color, #444); }
.eps-lp-send-label { flex: 0 0 auto; color: var(--descrip-text, #999); cursor: help; }
.eps-lp-pll-select { flex: 1 1 auto; min-width: 0; background: var(--comfy-menu-bg, #262626); border: 1px solid var(--border-color, #444); color: var(--input-text, #ccc); border-radius: 3px; padding: 1px 3px; font-size: 11px; font-family: inherit; }
.eps-lp-send-status { flex: 0 1 auto; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--descrip-text, #999); font-style: italic; }
.eps-lp-row { display: flex; align-items: center; gap: 6px; padding: 2px 6px; border-radius: 3px; user-select: none; }
.eps-lp-row:hover { background: var(--content-hover-bg, #2a2a2a); }
.eps-lp-row input[type="checkbox"] { flex: 0 0 auto; margin: 0; cursor: pointer; }
.eps-lp-row-label { flex: 1 1 auto; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.eps-lp-row-missing { color: var(--error-text, #ff4444); font-style: italic; }
.eps-lp-row-ghost { opacity: 0.55; }
.eps-lp-strength { flex: 0 0 auto; width: 56px; box-sizing: border-box; background: var(--comfy-menu-bg, #262626); border: 1px solid var(--border-color, #444); color: var(--input-text, #ccc); border-radius: 3px; padding: 1px 3px; font-size: 11px; font-family: inherit; }
.eps-lp-browser { flex: 1 1 auto; display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
.eps-lp-search { flex: 0 0 auto; box-sizing: border-box; width: calc(100% - 12px); margin: 4px 6px 2px; background: var(--comfy-menu-bg, #262626); border: 1px solid var(--border-color, #444); color: var(--input-text, #ccc); border-radius: 3px; padding: 2px 5px; font-size: 11px; font-family: inherit; }
.eps-lp-thumb { flex: 0 0 auto; width: 26px; height: 26px; object-fit: cover; border-radius: 3px; background: var(--comfy-menu-bg, #262626); }
.eps-lp-drag-handle { flex: 0 0 auto; cursor: grab; touch-action: none; user-select: none; color: var(--descrip-text, #999); padding: 0 2px; font-size: 12px; line-height: 1; }
.eps-lp-drag-handle:active { cursor: grabbing; }
.eps-lp-row-dragging { opacity: 0.6; background: var(--content-hover-bg, #2a2a2a); }
.eps-lp-crumbs { flex: 0 0 auto; display: flex; align-items: center; flex-wrap: wrap; gap: 2px; padding: 3px 6px; border-bottom: 1px solid var(--border-color, #444); color: var(--descrip-text, #999); }
.eps-lp-crumb { background: none; border: none; padding: 0 2px; color: var(--input-text, #ccc); cursor: pointer; font-size: 11px; font-family: inherit; }
.eps-lp-crumb:hover { text-decoration: underline; }
.eps-lp-crumb-sep { color: var(--descrip-text, #999); }
.eps-lp-list { flex: 1 1 auto; min-height: 0; overflow-y: auto; overflow-x: hidden; padding: 2px 3px 4px; }
.eps-lp-folder-row { cursor: pointer; }
.eps-lp-count { flex: 0 0 auto; color: var(--descrip-text, #999); font-size: 10px; }
.eps-lp-btn { flex: 0 0 auto; background: var(--comfy-menu-bg, #262626); border: 1px solid var(--border-color, #444); color: var(--input-text, #ccc); border-radius: 3px; padding: 1px 6px; font-size: 10px; font-family: inherit; cursor: pointer; white-space: nowrap; }
.eps-lp-btn-blocked { opacity: 0.55; cursor: help; }
.eps-lp-btn:hover { background: var(--content-hover-bg, #2a2a2a); }
.eps-lp-star { flex: 0 0 auto; background: none; border: none; cursor: pointer; font-size: 12px; line-height: 1; padding: 0 2px; color: var(--descrip-text, #999); font-family: inherit; }
.eps-lp-star-on { color: #f0c420; }
.eps-lp-empty { padding: 8px 7px; color: var(--descrip-text, #999); font-style: italic; }
.eps-lp-status { flex: 0 0 auto; display: flex; align-items: center; gap: 8px; padding: 3px 6px; border-top: 1px solid var(--border-color, #444); min-height: 20px; box-sizing: border-box; }
.eps-lp-status-hidden { display: none; }
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
    pllTargetId: null, // Send-to-loader target node id -- transient, M2 adds no widget (§6.13)
    searchQuery: '', // §6.13 M3 view-only filter -- transient, never serialized
    favDrag: null, // in-flight M3 favorites drag -- element-level, pointer-captured
    loadToken: 0, // guards a stale/superseded fetch from clobbering fresher state
    favoriteToken: 0, // same guard for favorites round-trips (star toggle + M3 reorder)
    recentToken: 0, // and for the recents stamps + the M3 Clear-recents POST
    root: null,
    searchInputEl: null,
    favRowEls: [], // favorites-view row order (file+el) for the M3 drag -- rebuilt each render
    lastSelectedCount: null, // last painted Selected row count -- drives the node-growth delta
    selectedHeaderEl: null,
    selectedListEl: null,
    sendRowEl: null,
    crumbsEl: null,
    listEl: null,
    statusRowEl: null,
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
  state.selectedHeaderEl = el('div', { className: 'eps-lp-section-header' })
  state.selectedListEl = el('div', { className: 'eps-lp-selected-list' })
  state.sendRowEl = el('div', { className: 'eps-lp-send' })
  state.crumbsEl = el('div', { className: 'eps-lp-crumbs' })
  state.listEl = el('div', { className: 'eps-lp-list' })
  state.statusTextEl = el('div', { className: 'eps-lp-status-text' })
  state.statusActionsEl = el('div', { className: 'eps-lp-status-actions' })

  // §6.13 M3 search field, under the breadcrumb (owner ask 2026-08-14) --
  // notebook.js's §7.2 search input (v0.53.0), same rules: every keystroke
  // stops propagation (canvas hotkeys), Escape CLEARS the query in place,
  // pure view filter. The placeholder tracks the browse folder (set in
  // renderBrowser) because the search corpus IS the current folder now.
  state.searchInputEl = el('input', {
    className: 'eps-lp-search',
    attrs: {
      type: 'text',
      placeholder: 'Search loras…',
      spellcheck: 'false',
      title: 'Searches the folder shown in the breadcrumb, subfolders included.'
    }
  })
  state.searchInputEl.addEventListener('input', () => {
    state.searchQuery = state.searchInputEl.value
    renderBrowser(state)
  })
  state.searchInputEl.addEventListener('keydown', (event) => {
    event.stopPropagation()
    if (event.key === 'Escape') {
      event.preventDefault()
      clearSearch(state)
      renderBrowser(state)
    }
  })

  const browser = el('div', { className: 'eps-lp-browser' }, [
    state.crumbsEl,
    state.searchInputEl,
    state.listEl
  ])
  state.statusRowEl = el('div', { className: 'eps-lp-status' }, [state.statusTextEl, state.statusActionsEl])
  state.root = el('div', { className: 'eps-lp-root' }, [
    state.selectedHeaderEl,
    state.selectedListEl,
    state.sendRowEl,
    browser,
    state.statusRowEl
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
    // The floor grows with the Selected list (owner ask 2026-08-14: the
    // full list must always be visible) -- see syncSelectedGrowth.
    getMinHeight: () => MIN_WIDGET_HEIGHT + state.selection.loras.length * SELECTED_ROW_PX
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
  clearSearch(state) // ...and any search typed against the old scope (§6.13 M3)
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
  // Granular repaints (Add, remove) skip the full render, so the Send row's
  // empty-selection gate refreshes here, at the one funnel every mutation hits.
  renderSend(state)
}

/** Invalidates any in-flight `GET picker` feed. A fetch computed
 * server-side BEFORE a just-confirmed mutation (star, reorder, recent,
 * clear) would land AFTER it and visibly revert the confirmed state on
 * screen -- the load token and the mutation tokens never see each other
 * otherwise (review 2026-08-09). Called from every mutation success path;
 * the next explicit load re-syncs everything. */
function invalidateInFlightLoad(state) {
  state.loadToken++
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
    // The library total lives in the right-click Properties panel now
    // (owner ask 2026-08-14) -- per-machine info, refreshed on every load.
    if (state.node.properties) state.node.properties.library_loras = state.loras.length
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

/** The trimmed live M3 query -- `''` means "search inactive". */
function activeSearchQuery(state) {
  return (state.searchQuery || '').trim()
}

/** Clears the M3 query in place -- state and input element together, so
 * they can never drift. Callers: Escape, breadcrumb navigation, a scope
 * change, and the restore/reconfigure resync. */
function clearSearch(state) {
  state.searchQuery = ''
  if (state.searchInputEl) state.searchInputEl.value = ''
}

/** Sets the per-workflow scope (§6.13: pure view state, serialized with
 * the workflow, execution ignores it) and resets the browse position.
 * Clears an active search too -- the scope DEFINES the search corpus
 * (§6.13 M3: matching is scope-relative), so a re-scope restarts it. */
function setScope(state, scopePath) {
  state.selection = { ...state.selection, scope: scopePath }
  state.path = []
  state.view = 'browse'
  clearSearch(state)
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
      invalidateInFlightLoad(state)
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
    invalidateInFlightLoad(state)
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
  renderSelected(state)
  renderSend(state)
  renderBrowser(state)
  renderStatus(state)
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
  } else {
    for (const row of rows) state.selectedListEl.append(buildSelectedRowEl(state, row))
  }
  syncSelectedGrowth(state)
}

/**
 * Owner ask 2026-08-14: the Selected section always shows every row --
 * no crop, no inner scroll. The list itself sizes to content (CSS `flex:
 * 0 0 auto`, no max-height); THIS grows the node to make the room, by the
 * row-count delta, so the browser below keeps its share and a user's own
 * manual resize (extra height) is preserved. getMinHeight() includes the
 * same per-row term, so litegraph won't let a manual shrink crop the list
 * either.
 */
function syncSelectedGrowth(state) {
  const count = state.selection.loras.length
  const previous = state.lastSelectedCount
  state.lastSelectedCount = count
  if (previous === count) return
  const node = state.node
  // node.size is a Float32Array in current frontends -- never Array.isArray it.
  if (!node?.size || typeof node.setSize !== 'function') return
  const floor = typeof node.computeSize === 'function' ? node.computeSize()[1] : 0
  const delta = previous == null ? 0 : (count - previous) * SELECTED_ROW_PX
  node.setSize([node.size[0], Math.max(node.size[1] + delta, floor)])
  node.graph?.setDirtyCanvas(true, true)
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
 * "Library" unscoped) -> drill-down path, or root -> pseudo-folder. Since
 * the standalone scope bar's removal (owner ask 2026-08-14) this row also
 * carries the clear-scope ✕ (right edge, scoped only) and the library
 * total lives in the root crumb's tooltip. */
function renderCrumbs(state) {
  const scope = state.selection.scope
  state.crumbsEl.replaceChildren()
  const totalNote = state.loaded ? ` — ${state.loras.length} loras on this machine` : ''
  const rootBtn = el('button', {
    className: 'eps-lp-crumb',
    text: scope ? basename(scope) : 'Library',
    attrs: {
      title: scope
        ? `${scope} — this workflow browses only this folder and its subfolders.${totalNote}`
        : `Whole library${totalNote}`
    }
  })
  rootBtn.addEventListener('click', () => {
    // §6.13 M3's pinned choice: navigating via the breadcrumb CLEARS an
    // active search (the contract allowed either; this one is pinned).
    clearSearch(state)
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
      clearSearch(state) // same pinned navigate-clears-search choice as rootBtn
      if (state.view === 'browse') state.path = state.path.slice(0, index + 1)
      renderBrowser(state)
    })
    state.crumbsEl.append(crumbBtn)
  })

  if (scope) {
    const clearBtn = el('button', {
      className: 'eps-lp-icon-btn eps-lp-crumb-clear',
      text: '✕',
      attrs: { title: 'Clear scope — browse the whole library again' }
    })
    clearBtn.addEventListener('click', () => setScope(state, ''))
    state.crumbsEl.append(clearBtn)
  }
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
  // The search corpus is the current browse folder, so the placeholder
  // names it -- at the library root it stays the generic M3 wording.
  if (state.searchInputEl) {
    const folder = currentFolder(state)
    state.searchInputEl.placeholder = folder ? `Search ${basename(folder)}…` : 'Search loras…'
  }
  state.listEl.replaceChildren()
  state.favRowEls = []

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

  // §6.13 M3 search: while a query is active the browser is a FLAT list of
  // matching loras across ALL subfolders of the current scope, labeled by
  // their scope-relative paths. Folders and the ★/🕘 pseudo-folder rows are
  // hidden (this return sits above every one of them), and the favorites
  // drag never engages (the favorites view is never rendered while
  // searching -- plus buildLoraRowEl's own gate).
  const query = activeSearchQuery(state)
  if (query) {
    renderSearchResults(state, query)
    return
  }

  if (state.view === 'favorites') {
    if (state.favorites.length === 0) {
      state.listEl.append(el('div', { className: 'eps-lp-empty', text: 'No favorites yet — star a lora to keep it here.' }))
      return
    }
    // Store order (§6.13) -- deliberately NOT re-sorted. Rows are tracked
    // in favRowEls for the M3 drag-reorder (installed rows get a ≡ handle;
    // ghosts render as before and are never draggable).
    for (const file of state.favorites) {
      const rowEl = buildLoraRowEl(state, file, file)
      state.favRowEls.push({ file, el: rowEl })
      state.listEl.append(rowEl)
    }
    return
  }

  if (state.view === 'recent') {
    if (state.recents.length === 0) {
      state.listEl.append(el('div', { className: 'eps-lp-empty', text: 'No recently used loras yet.' }))
      return
    }
    // §6.13 M3: Clear recents lives in THIS view only, above the rows.
    state.listEl.append(buildClearRecentsRowEl(state))
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

/**
 * §6.13 M3: the flat search view -- every lora under the current browse
 * FOLDER (scope + drill-down, owner ask 2026-08-14: "only search the
 * selected folder... unless library is selected") whose folder-relative
 * path matches the query (loraMatchesSearch), in the served list's own
 * order, labeled by that same folder-relative path. No folder rows, no
 * pseudo-folder rows, no drag. The folder prefix is stripped BEFORE
 * matching, so the folder's own name doesn't match every lora inside it.
 */
function renderSearchResults(state, query) {
  const folder = currentFolder(state)
  const prefix = folder ? `${folder}/` : ''
  const matches = []
  for (const name of state.loras) {
    if (prefix && !name.startsWith(prefix)) continue
    const rel = name.slice(prefix.length)
    if (loraMatchesSearch(rel, query)) matches.push({ file: name, label: rel })
  }
  if (matches.length === 0) {
    state.listEl.append(
      el('div', { className: 'eps-lp-empty', text: `No loras match "${query}".` })
    )
    return
  }
  for (const match of matches) state.listEl.append(buildLoraRowEl(state, match.file, match.label))
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
 * One browser lora row: [≡ handle (M3, favorites view only)] + star toggle
 * + preview thumbnail (M3) + display name (relative to the current folder,
 * or scope-relative in search results) + `📋` trigger-word copy (M3) +
 * `＋ Add`. A GHOST -- a favorite/recent naming a lora NOT on this
 * machine's served list -- renders dimmed, ⚠-marked, and star-only (§6.13:
 * visible, never silently dropped; unstarring it here is how a
 * cross-machine favorite gets cleaned up). Ghosts are also never draggable
 * (§6.13 M3) -- they keep this dimmed rendering and survive a reorder
 * server-side (the route appends favorites missing from the posted list).
 */
function buildLoraRowEl(state, file, displayLabel) {
  const ghost = !state.loraSet.has(file)
  const favorite = state.favorites.includes(file)
  // §6.13 M3 drag gate: the ★ Favorites view only, never while searching,
  // never a ghost. The searching leg looks redundant (renderBrowser never
  // renders the favorites view with a query active) but keeps the rule
  // local and auditable rather than an emergent property of render order.
  const draggable = !ghost && state.view === 'favorites' && activeSearchQuery(state) === ''

  const starBtn = el('button', {
    className: favorite ? 'eps-lp-star eps-lp-star-on' : 'eps-lp-star',
    text: favorite ? '★' : '☆',
    attrs: { title: favorite ? 'Unstar' : 'Star as a favorite' }
  })
  starBtn.addEventListener('click', () => {
    toggleFavorite(state, file, !favorite).catch((error) => api.warn('favorite toggle rejected', error))
  })

  // §6.13 M3 thumbnail: the sidecar preview image, served by ROUTE_PREVIEW.
  // loading="lazy" keeps a 1000-lora folder from firing 1000 eager fetches;
  // the error listener hides the img because a 404 (no sidecar image) is
  // the NORMAL case, not a failure worth any pixels or noise.
  const thumb = el('img', {
    className: 'eps-lp-thumb',
    attrs: {
      src: api.apiUrl(`${ROUTE_PREVIEW}?file=${encodeURIComponent(file)}`),
      loading: 'lazy',
      alt: ''
    }
  })
  thumb.addEventListener('error', () => {
    thumb.style.display = 'none'
  })

  const label = el('span', {
    className: ghost ? 'eps-lp-row-label eps-lp-row-missing' : 'eps-lp-row-label',
    text: ghost ? `⚠ ${displayLabel}` : displayLabel,
    attrs: { title: ghost ? `${file} — not installed here` : file }
  })

  const children = [starBtn, thumb, label]
  if (draggable) {
    const handle = el('span', {
      className: 'eps-lp-drag-handle',
      text: '≡',
      attrs: { title: 'Drag to reorder favorites' }
    })
    wireFavoriteDrag(state, handle, file)
    children.unshift(handle)
  }
  if (!ghost) {
    const copyBtn = el('button', {
      className: 'eps-lp-icon-btn',
      text: '📋',
      attrs: { title: 'Copy this lora’s trigger words (from its .txt sidecar)' }
    })
    copyBtn.addEventListener('click', () => {
      copyTriggerWords(state, file).catch((error) => api.warn('trigger-word copy rejected', error))
    })
    const addBtn = el('button', { className: 'eps-lp-btn', text: '＋ Add', attrs: { title: 'Add to the selection' } })
    addBtn.addEventListener('click', () => addLora(state, file))
    children.push(copyBtn, addBtn)
  }
  return el('div', { className: ghost ? 'eps-lp-row eps-lp-row-ghost' : 'eps-lp-row' }, children)
}

// --- §6.13 M3: Clear recents (armed two-click) ---

/**
 * The Recent view's Clear-recents row -- controller.js's ARMED two-click
 * pattern (`_onDeleteClick`'s shape, the armed-delete precedent): the first
 * click arms the button ("Really clear?"), which auto-disarms after
 * CLEAR_RECENTS_CONFIRM_MS -- or sooner, on ANY re-render, because the
 * button is rebuilt fresh each renderBrowser() and its `_armed`/`_armTimer`
 * die with the old DOM node (the disarm-on-rerender is free here, where
 * controller.js has to deliberately NOT rebuild to keep its armed state).
 * The second click POSTs ROUTE_CLEAR_RECENTS and re-syncs from the
 * response.
 */
function buildClearRecentsRowEl(state) {
  const button = el('button', {
    className: 'eps-lp-btn',
    text: 'Clear recents',
    attrs: { title: 'Forget the recently-used list — asks once before clearing' }
  })
  button.addEventListener('click', () => {
    if (!button._armed) {
      button._armed = true
      button.textContent = 'Really clear?'
      clearTimeout(button._armTimer)
      button._armTimer = setTimeout(() => {
        button._armed = false
        button.textContent = 'Clear recents'
      }, CLEAR_RECENTS_CONFIRM_MS)
      return
    }
    clearTimeout(button._armTimer)
    button._armed = false
    clearRecents(state).catch((error) => api.warn('clear recents rejected', error))
  })
  return el('div', { className: 'eps-lp-row' }, [button])
}

/** The armed second click (§6.13 M3): POST + re-sync `state.recents` from
 * the response's authoritative (empty) list. Token-guarded alongside the
 * recents stamps so a slow in-flight stamp can't resurrect cleared rows. */
async function clearRecents(state) {
  const token = ++state.recentToken
  try {
    const data = await api.postJson(ROUTE_CLEAR_RECENTS, {})
    if (token !== state.recentToken) return
    invalidateInFlightLoad(state)
    state.recents = sanitizeRecents(data?.recents)
    renderBrowser(state)
    toast('success', 'EPS LoRA Picker', 'Recents cleared.')
  } catch (error) {
    if (token !== state.recentToken) return
    api.warn('clear recents failed', error)
    toast('error', 'EPS LoRA Picker', `Recents not cleared: ${error?.message || error}`)
  }
}

// --- §6.13 M3: trigger-word copy ---

/**
 * The 📋 click: on-demand `GET picker/info` (never prefetched -- a sidecar
 * read per rendered row would hammer the store for a hover-rare action),
 * then clipboard + a success toast NAMING the words. No sidecar is an info
 * toast, not an error -- most loras simply don't have one (§6.13 M3).
 */
async function copyTriggerWords(state, file) {
  let data
  try {
    data = await api.getJson(ROUTE_INFO, { file })
  } catch (error) {
    api.warn('trigger-word info fetch failed', error)
    toast('error', 'EPS LoRA Picker', `Could not read trigger words: ${error?.message || error}`)
    return
  }
  const words = typeof data?.trigger_words === 'string' ? data.trigger_words : ''
  if (!words) {
    toast('info', 'EPS LoRA Picker', `No trigger-word sidecar for ${basename(file)}`)
    return
  }
  if (!(await copyTextToClipboard(words))) {
    toast('error', 'EPS LoRA Picker', 'Could not copy to the clipboard.')
    return
  }
  const shown =
    words.length > COPY_TOAST_MAX_CHARS ? `${words.slice(0, COPY_TOAST_MAX_CHARS)}…` : words
  toast('success', 'EPS LoRA Picker', `Copied: ${shown}`)
}

/**
 * `navigator.clipboard.writeText` when available, else the hidden-textarea
 * `document.execCommand('copy')` degrade -- web/eps_image/image_grid.js's
 * `copyTextViaExecCommand` technique, copied here rather than imported
 * (the house no-cross-module-coupling convention): the whole
 * `navigator.clipboard` object is [SecureContext], and ComfyUI over LAN
 * http -- this pack's first-class remote case -- is NOT a secure context,
 * so the fallback is the working path there, not an edge case. See
 * image_grid.js's doc comment for the full story (it in turn mirrors
 * ComfyUI core's own `legacyCopy()`). Returns whether the copy succeeded.
 * @param {string} text @returns {Promise<boolean>}
 */
async function copyTextToClipboard(text) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch (error) {
      // Present but refused (permission denied etc.) -- fall through to the
      // legacy path rather than giving up.
      api.warn('navigator.clipboard.writeText failed; trying the execCommand fallback', error)
    }
  }
  const textarea = document.createElement('textarea')
  textarea.setAttribute('readonly', '')
  textarea.value = text
  textarea.style.position = 'fixed'
  textarea.style.left = '-9999px'
  textarea.style.top = '-9999px'
  document.body.appendChild(textarea)
  textarea.select()
  try {
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    textarea.remove()
  }
}

// --- §6.13 M3: favorites drag-reorder ---

/**
 * Wires the ≡ handle of one INSTALLED favorites-view row (buildLoraRowEl's
 * `draggable` gate). The §7.5-safe shape: pointerdown calls
 * `setPointerCapture(handle)`, which retargets EVERY later
 * pointermove/pointerup/pointercancel for that pointer at the handle
 * itself -- so plain element-level listeners see the whole gesture,
 * wherever the pointer wanders, and no window-level listener
 * (capture-phase or otherwise) is ever involved. The Vue-nodes wrapper's
 * bubble-path stopPropagation (§7.5's gesture-killer) therefore never gets
 * a say. `lostpointercapture` is the escape hatch: if a mid-drag re-render
 * detaches the row (implicitly releasing capture), the gesture cancels
 * cleanly instead of leaving `state.favDrag` stuck forever.
 */
function wireFavoriteDrag(state, handle, file) {
  handle.addEventListener('pointerdown', (event) => {
    if (event.button > 0) return // primary button / touch / pen only
    if (state.favDrag) return // one gesture at a time
    event.preventDefault()
    event.stopPropagation()
    try {
      handle.setPointerCapture(event.pointerId)
    } catch (error) {
      // Without capture the element-level move/up listeners would go blind
      // the moment the pointer left the handle -- no capture, no drag.
      api.warn('favorites drag: setPointerCapture failed; drag unavailable', error)
      return
    }
    const start = state.favRowEls.find((row) => row.file === file)
    if (!start) return
    const drag = { pointerId: event.pointerId, file }
    state.favDrag = drag
    start.el.classList.add('eps-lp-row-dragging')

    const onMove = (moveEvent) => {
      if (moveEvent.pointerId !== drag.pointerId) return
      moveDraggedFavorite(state, drag, moveEvent.clientY)
    }
    const onUp = (upEvent) => {
      if (upEvent.pointerId !== drag.pointerId) return
      detach()
      finishFavoriteDrag(state, drag).catch((error) => api.warn('favorites reorder rejected', error))
    }
    const onCancel = (cancelEvent) => {
      if (cancelEvent.pointerId !== drag.pointerId) return
      detach()
      cancelFavoriteDrag(state)
    }
    const onLost = () => {
      // Implicit capture release (row detached by a mid-drag re-render):
      // treat as cancel. Our own detach() removes this listener BEFORE
      // releasing, so a normal finish never double-fires.
      detach()
      cancelFavoriteDrag(state)
    }
    function detach() {
      handle.removeEventListener('pointermove', onMove)
      handle.removeEventListener('pointerup', onUp)
      handle.removeEventListener('pointercancel', onCancel)
      handle.removeEventListener('lostpointercapture', onLost)
      try {
        handle.releasePointerCapture(drag.pointerId)
      } catch {
        // Already released, or never captured.
      }
    }
    handle.addEventListener('pointermove', onMove)
    handle.addEventListener('pointerup', onUp)
    handle.addEventListener('pointercancel', onCancel)
    handle.addEventListener('lostpointercapture', onLost)
  })
}

/**
 * Live reorder while the pointer moves: the dragged row slots in wherever
 * *clientY* sits among the OTHER rows' midpoints -- both the DOM and the
 * working `state.favRowEls` order move together, so the drop commit just
 * reads the array. `state.favorites` itself is NOT touched until the drop
 * (cancelFavoriteDrag restores the view from it). Ghost rows shift aside
 * visually like any other row; only the POST filters them out.
 *
 * THE DRAGGED ELEMENT IS NEVER REPARENTED -- only the other rows move
 * around it (found live on the rig, 2026-08-09): `insertBefore` on the
 * dragged row itself is a remove+re-insert, and removing the captured
 * handle's ancestor from the document IMPLICITLY RELEASES pointer capture
 * -- `lostpointercapture` fired on the very first reorder and the gesture
 * died mid-drag. Repositioning the OTHER rows around the stationary
 * dragged element produces the identical visible order with the capture
 * intact.
 */
function moveDraggedFavorite(state, drag, clientY) {
  const rows = state.favRowEls
  const fromIndex = rows.findIndex((row) => row.file === drag.file)
  if (fromIndex === -1) return // mid-drag re-render replaced the rows
  const dragged = rows[fromIndex]
  const others = rows.filter((_, index) => index !== fromIndex)
  let insertAt = others.length
  for (let i = 0; i < others.length; i++) {
    const rect = others[i].el.getBoundingClientRect()
    if (clientY < rect.top + rect.height / 2) {
      insertAt = i
      break
    }
  }
  if (insertAt === fromIndex) return
  state.favRowEls = [...others.slice(0, insertAt), dragged, ...others.slice(insertAt)]
  // Rows now ABOVE the dragged one: re-inserted before it, in order...
  for (const row of others.slice(0, insertAt)) state.listEl.insertBefore(row.el, dragged.el)
  // ...rows now BELOW it: chained after it, each becoming the next anchor.
  let anchor = dragged.el
  for (const row of others.slice(insertAt)) {
    anchor.after(row.el)
    anchor = row.el
  }
}

/**
 * Drop commit (§6.13 M3): optimistic local reorder (state.favorites takes
 * the dragged order the DOM already shows), then POST the full new order
 * with INSTALLED favorites only -- ghosts are never sent AND never dropped
 * locally: the route itself appends favorites missing from the posted
 * list, and the response's `favorites` is the authoritative full list
 * (ghosts included), which re-syncs local state on success. Failure
 * reverts to the pre-drag order + toasts (toggleFavorite's exact posture);
 * token-guarded alongside the star toggles so the two mutation families
 * can't clobber each other's responses.
 */
async function finishFavoriteDrag(state, drag) {
  state.favDrag = null
  const previous = state.favorites.slice()
  const reordered = state.favRowEls.map((row) => row.file)
  const changed =
    reordered.length !== previous.length || reordered.some((file, i) => file !== previous[i])
  state.favorites = reordered
  renderBrowser(state) // rebuild also drops the eps-lp-row-dragging styling
  if (!changed) return
  const token = ++state.favoriteToken
  try {
    // The FULL reordered list, ghosts included (review 2026-08-09): a ghost
    // is a KNOWN favorite (just not installed here), and posting only the
    // installed subset made the route's missing-names-append rule teleport
    // every ghost to the end of the shared order on ANY reorder -- losing
    // the other machine's positions. The route ignores genuinely unknown
    // names, so sending everything is safe and keeps the optimistic order
    // identical to the server's.
    const data = await api.postJson(ROUTE_FAVORITES_ORDER, { files: reordered })
    if (token !== state.favoriteToken) return
    invalidateInFlightLoad(state)
    if (Array.isArray(data?.favorites)) {
      state.favorites = data.favorites
        .filter((entry) => typeof entry === 'string')
        .map(normalizeLoraName)
      renderBrowser(state)
    }
  } catch (error) {
    if (token !== state.favoriteToken) return
    state.favorites = previous
    renderBrowser(state)
    api.warn('favorites reorder failed', error)
    toast('error', 'EPS LoRA Picker', `Order not saved: ${error?.message || error}`)
  }
}

/** pointercancel / lost capture: abandon the gesture. The visible order is
 * rebuilt from `state.favorites`, which a drag never touches before its
 * drop -- so cancel is just a repaint. */
function cancelFavoriteDrag(state) {
  state.favDrag = null
  renderBrowser(state)
}

/** §7.2-style status line: the load error + Retry live HERE, so the
 * Selected section above never has to make room for (or be blanked by)
 * a fetch failure. Once loaded cleanly the whole bar COLLAPSES (owner ask
 * 2026-08-14: the standing total wasted real estate) -- the count moved
 * to the `library_loras` property and the root crumb's tooltip. */
function renderStatus(state) {
  state.statusActionsEl.replaceChildren()
  state.statusRowEl?.classList.remove('eps-lp-status-hidden')
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
  if (state.loaded) {
    state.statusTextEl.textContent = ''
    state.statusRowEl?.classList.add('eps-lp-status-hidden')
    return
  }
  state.statusTextEl.textContent = 'Loading…'
}

// --- Send to loader (§6.13 M2 + the M4 target registry) -- each
// *_bridge.js owns one loader family's technique; this file only owns the
// row and the family-agnostic target plumbing. ---

/**
 * §6.13 M4: the comfyClass-keyed send-adapter registry -- the ecosystem's
 * one proven multi-target pattern (Lora-Manager's NODE_EXTRACTORS dict).
 * Each entry owns one loader family: `find()` lists its live graph nodes,
 * `probe(node)` is its §6.3-style feature-detection gate, `write(node,
 * rows)` performs the send and may return `{flattened, clamped}` basename
 * lists for the loud-lossy success toast (the rgthree write is never lossy
 * and returns nothing), and `label` is the family's human name for the
 * family-agnostic messages below. Candidate order and the single-candidate
 * auto-adopt are computed ACROSS families; every per-family rule stays in
 * its bridge.
 */
const SEND_ADAPTERS = {
  [pll.POWER_LORA_LOADER_TYPE]: {
    label: 'Power Lora Loader (rgthree)',
    find: pll.findPllNodes,
    probe: pll.probePll,
    write: pll.writeRowsToPll
  },
  [dasiwa.DASIWA_LOADER_TYPE]: {
    label: 'DaSiWa Advanced LoRA Loader',
    find: dasiwa.findDasiwaNodes,
    probe: dasiwa.probeDasiwa,
    write: dasiwa.writeRowsToDasiwa
  }
}

/** §6.13 M4: the no-target-in-graph message is FAMILY-AGNOSTIC -- composed
 * from every adapter's label so it names both supported loaders (and any
 * future third automatically), in M2's own message shape. */
const SEND_FAMILY_LABELS = Object.values(SEND_ADAPTERS).map((adapter) => adapter.label)
const MSG_NO_SEND_TARGET_IN_GRAPH = `Optional — the picker applies its loras itself via its model/clip outputs. To copy the list into a loader node instead, add a ${SEND_FAMILY_LABELS.join(
  ' or '
)} node, then pick it here.`
const MSG_NO_SEND_TARGET_SELECTED = 'Pick a target loader node above.'

/** Every adapter family's live candidates, merged ascending id ACROSS
 * families (§6.13 M4) -- the combo's order AND the auto-adopt universe. */
function findSendCandidates() {
  const candidates = []
  for (const adapter of Object.values(SEND_ADAPTERS)) candidates.push(...adapter.find())
  return candidates.sort((a, b) => a.id - b.id)
}

/** The live loader the select currently names, or null -- resolved BY ID at
 * use time, since the node may have been deleted since the last render. */
function resolveSendTarget(state) {
  if (state.pllTargetId == null) return null
  return findSendCandidates().find((node) => String(node.id) === state.pllTargetId) || null
}

/**
 * The family-agnostic half of the probe: a null/unknown target resolves
 * here (M2's two null-target codes, now spanning both families -- no
 * candidates at all vs candidates present but unpicked); a real candidate
 * dispatches by comfyClass to its own family's §6.3-style probe.
 */
function probeSendTarget(node) {
  const adapter = node ? SEND_ADAPTERS[node.type] : null
  if (!adapter) {
    const hasAny = findSendCandidates().length > 0
    return {
      ok: false,
      code: hasAny ? 'no-target-selected' : 'no-target-in-graph',
      message: hasAny ? MSG_NO_SEND_TARGET_SELECTED : MSG_NO_SEND_TARGET_IN_GRAPH
    }
  }
  return adapter.probe(node)
}

/**
 * The Send-to-loader row (§6.13 M2, targets registry-driven since M4): a
 * target combo of every supported loader in the graph (ascending id ACROSS
 * adapter families) + Send. Rebuilt on every render; the previously chosen
 * target is re-selected by id when still present -- transient state only,
 * M2 adds no widget. A failed probe (no rgthree, no loader in the graph,
 * shape drift) disables Send and shows the owning family's §6.3-style
 * message vocabulary in both the button title and the muted status span;
 * an empty selection disables it with its own title.
 */
function renderSend(state) {
  state.sendRowEl.replaceChildren()

  const select = el('select', {
    className: 'eps-lp-pll-select',
    attrs: { title: 'Target loader node' }
  })
  // Options are rebuilt IN PLACE on every open (mousedown), not just per
  // render -- controller.js's values-function pattern: a loader added after
  // the last render must be pickable without any other mutation first
  // (review 2026-08-09).
  const buildOptions = () => {
    const fresh = findSendCandidates()
    select.replaceChildren(
      el('option', {
        text: fresh.length ? 'Pick a loader…' : 'No loader in graph',
        attrs: { value: '' }
      }),
      ...fresh.map((node) =>
        el('option', { text: `${node.title || node.type} #${node.id}`, attrs: { value: String(node.id) } })
      )
    )
    if (state.pllTargetId != null && fresh.some((node) => String(node.id) === state.pllTargetId)) {
      select.value = state.pllTargetId
    } else if (state.pllTargetId == null && fresh.length === 1) {
      // No explicit choice + exactly one candidate ACROSS families (§6.13
      // M4): adopt it (controller.js's "picked automatically when there is
      // exactly one" behavior).
      state.pllTargetId = String(fresh[0].id)
      select.value = state.pllTargetId
    } else {
      // The chosen target is GONE (deleted since it was picked): stay on the
      // placeholder and keep remembering the id (an undo brings it back) --
      // NEVER fall through to the first option; adopting a loader the user
      // didn't choose makes Send destructively overwrite it (§6.3 "never
      // guess"; review 2026-08-09).
      select.value = ''
    }
  }
  buildOptions()
  select.addEventListener('mousedown', buildOptions)
  select.addEventListener('change', () => {
    state.pllTargetId = select.value || null
    // Re-probe with the fresh choice -- storing the id alone left the button
    // frozen in the previous target's probe verdict (review 2026-08-09).
    renderSend(state)
  })
  // Canvas hotkeys must not intercept the combo -- the strength input's rule.
  select.addEventListener('keydown', (event) => event.stopPropagation())

  const sendLabel = el('span', {
    className: 'eps-lp-send-label',
    text: 'Send to',
    attrs: {
      title:
        'Optional: copy the selection into a Power Lora Loader (rgthree) or ' +
        'DaSiWa loader node. The picker itself already applies its loras — ' +
        'wire model/clip through it, or use its lora_stack output.'
    }
  })
  const statusEl = el('span', { className: 'eps-lp-send-status' })
  const sendBtn = el('button', { className: 'eps-lp-btn', text: 'Send' })
  const probe = probeSendTarget(resolveSendTarget(state))
  if (!probe.ok) {
    // Marked blocked but NOT disabled: a disabled button could never run the
    // click-time re-probe, so a graph fixed after this render (a loader
    // added, rgthree finished loading) would have no recovery path (review
    // 2026-08-09). The click either proceeds against the healed graph or
    // toasts this same message.
    sendBtn.classList.add('eps-lp-btn-blocked')
    sendBtn.title = probe.message
    statusEl.textContent = probe.message
    statusEl.title = probe.message
  } else if (state.selection.loras.length === 0) {
    sendBtn.disabled = true
    sendBtn.title = 'Nothing selected'
  } else {
    sendBtn.title = `Write the selection's ${state.selection.loras.length} row(s) into the target loader`
  }
  sendBtn.addEventListener('click', () => sendToPll(state))

  state.sendRowEl.append(sendLabel, select, sendBtn, statusEl)
}

/**
 * Send click: re-resolve and RE-PROBE by id -- the render-time probe can
 * be stale (the target may have been deleted since), so failure here is a
 * toast, not a silent no-op. Writes ALL selection rows (`on` preserved --
 * §6.13) through the target's own family adapter (§6.13 M4). The success
 * toast is unchanged for rgthree; a lossy DaSiWa write (dual strengths
 * flattened to the model strength, strengths clamped to DaSiWa's ±5)
 * appends LOUD notes naming every affected row -- owner decision
 * 2026-08-09: lossy edges are loud, never silent.
 */
function sendToPll(state) {
  // Same single-candidate adoption the render path applies -- a loader
  // added AFTER the last render must make a plain Send click work without
  // first touching the combo (review 2026-08-09). Never adopts among
  // several; "single candidate" spans BOTH adapter families (§6.13 M4).
  if (state.pllTargetId == null) {
    const candidates = findSendCandidates()
    if (candidates.length === 1) state.pllTargetId = String(candidates[0].id)
  }
  const node = resolveSendTarget(state)
  const probe = probeSendTarget(node)
  if (!probe.ok) {
    toast('error', 'EPS LoRA Picker', probe.message)
    renderSend(state)
    return
  }
  const rows = state.selection.loras
  if (rows.length === 0) {
    // Reachable via the blocked-not-disabled button: an empty send would
    // SHRINK the target loader to zero rows -- refuse, never truncate.
    toast('warn', 'EPS LoRA Picker', 'Nothing selected — Add loras before sending.')
    renderSend(state)
    return
  }
  let result
  try {
    result = SEND_ADAPTERS[node.type].write(node, rows)
  } catch (error) {
    api.warn('send to loader failed', error)
    toast('error', 'EPS LoRA Picker', `Send failed: ${error?.message || error}`)
    return
  }
  const sent = `Sent ${rows.length} lora(s) to ${node.title || node.type} #${node.id}`
  const notes = []
  if (result?.flattened?.length) {
    notes.push(
      `model strength used for ${result.flattened.length} row(s) with a different clip strength: ${result.flattened.join(', ')}`
    )
  }
  if (result?.clamped?.length) {
    notes.push(`clamped to ±5: ${result.clamped.join(', ')}`)
  }
  toast('success', 'EPS LoRA Picker', notes.length ? `${sent} — ${notes.join('; ')}` : sent)
  renderSend(state)
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
    // Read-only info surfaced in right-click -> Properties (owner ask
    // 2026-08-14, replacing the status bar's standing count); refreshed
    // from the feed on every load. addProperty every attach is the
    // resolution.js pattern -- a saved value wins later via configure.
    if (typeof node.addProperty === 'function') node.addProperty('library_loras', 0, 'number')
    hideSelectionWidget(state)
    buildUi(state)
    wireConfigureReload(state)

    loadPicker(state).catch((error) => api.warn('initial picker load failed', error))
  } catch (error) {
    api.warn('attachPickerPanel failed', error)
  }
}
