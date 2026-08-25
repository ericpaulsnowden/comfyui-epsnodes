/**
 * @file EPS Prompt Builder DOM widget (companion to the EPS Prompt Notebook,
 * FORMAT.md §7.2-family conventions) — attaches to `EPSPromptBuilder` nodes.
 * LEFT pane: a read-only, searchable mirror of a Notebook's `.md` file (same
 * order/names the Notebook shows — no add/rename/delete here, all editing
 * stays in the Notebook). RIGHT pane: an ordered list of BLOCKS — live
 * references to prompt NAMES, appended by double-clicking a left row,
 * drag-reordered, and removed with ✕ (which only splices this list, never
 * touches the file). The combined text is assembled by the BACKEND at run
 * time (`separator`-joined); this panel only ever writes the two hidden
 * widgets `file` and `blocks`.
 *
 * Backend contract (pre-agreed, comfyClass `EPSPromptBuilder`): widgets in
 * order `file` (STRING, hidden), `blocks` (STRING, hidden, JSON array of
 * names, default "[]"), `separator` (STRING, visible — untouched here).
 *
 * Frontend facts relied on here (see notebook.js's own header for the full
 * citations against a `Comfy-Org/ComfyUI_frontend` checkout — duplicated
 * here only where this file's behavior actually depends on them):
 *  - `LGraphNode.prototype.addDOMWidget` works the same under the legacy
 *    canvas renderer and the Vue-node renderer.
 *  - Hiding a widget from BOTH renderers needs two flags: `widget.hidden`
 *    (canvas) and `widget.options.hidden` (Vue nodes read `options.hidden`
 *    and ignore `widget.hidden` outright) — see hideWidgetBothWays() below,
 *    the same pair notebook.js's hideFileWidget()/hidePinnedWidget() set.
 *  - `node.comfyClass` is set on both the instance and its constructor by
 *    ComfyUI's node-registration step, so `nodeCreated` can feature-detect a
 *    node's Python class id — see isPromptBuilderNode().
 *  - `widget.serialize = false` (workflow JSON) and
 *    `domWidget.serializeValue = () => undefined` (API prompt) are both
 *    needed to keep this DOM widget itself out of every serialized form —
 *    notebook.js's attachDomWidget() documents why both are independently
 *    required.
 *  - `api.walkLiveNodes(rootGraph)` (api.js) is the pack's subgraph-aware
 *    node walk (':'-joined path ids) — used here, rooted at `app.graph`
 *    (never `node.graph`, so a builder living INSIDE a subgraph still finds
 *    every Notebook in the whole workflow — picker.js's findSendCandidates()
 *    precedent), to discover every `LoraLibraryNotebook` node for the
 *    selector.
 *  - Workflow-restore race (notebook.js's file-header "Renaming... Restore"
 *    paragraph and its `wireConfigureReload`/`attachLoadTimer` fix, reused
 *    here verbatim): `nodeCreated` fires from the node's CONSTRUCTOR, before
 *    `configure()` restores `widgets_values` — so an attach-time read of
 *    `blocksWidget.value` sees the backend DEFAULT ("[]"), not a saved
 *    workflow's real blocks. `wireConfigureReload` below re-syncs
 *    `state.blocks` from the widget once `onConfigure` actually runs, and
 *    the deferred one-tick `attachLoadTimer` skips the attach-time load
 *    entirely once a configure already ran it (litegraph configures every
 *    node of one load synchronously, so the ordering is reliable).
 *  - Session cache + `known_mtime` (notebook.js's own "Session cache" section):
 *    reused here via its exported `notebookCacheGet`/`notebookCacheSet`/
 *    `isUnchangedResponse` rather than reimplemented, so this panel and any
 *    open Notebook on the SAME file share one browser-session cache.
 *
 * Drag-to-reorder here is DELIBERATELY plain HTML5 `draggable`/dragover/drop
 * — NOT notebook.js's pointer-based technique. notebook.js explains why it
 * avoided native DnD (multi-select blocks, category headers as both source
 * and drop target, a whole second gesture vocabulary); none of that applies
 * to this panel's single flat list of already-unique rows, so the owner's
 * spec calls for the simpler native mechanism instead. `stopPropagation()`
 * on every wired event keeps it from being misread as a canvas gesture, the
 * same defensive habit notebook.js applies to its own listeners (pointer
 * events over a DOM widget are already never seen by the canvas underneath
 * it — DOM siblings, not descendants — this is belt-and-suspenders).
 *
 * Vanilla ES modules, no build step, no templating — `el()` mirrors every
 * other module in this pack.
 */

import { app } from '../../../scripts/app.js'
import * as api from './api.js'
import { walkLiveNodes } from './api.js'
import { notebookCacheGet, notebookCacheSet, isUnchangedResponse } from './notebook.js'

/** FORMAT.md — frozen once shipped. */
export const CLASS_ID = 'EPSPromptBuilder'

/** The Notebook's own class id (notebook.js's `NODE_CLASS`, not exported —
 * deliberately duplicated here, same posture as pll_bridge.js duplicating
 * controller.js's PROP_* constants: a small, stable, cross-file constant is
 * cheaper to keep byte-identical by inspection than to import. */
const NOTEBOOK_CLASS_ID = 'LoraLibraryNotebook'

const WIDGET_NAME = 'prompt_builder'
const WIDGET_TYPE = 'eps_prompt_builder'

/** §7.2-style floor: no `getMaxHeight` set below, so the widget still takes
 * whatever vertical space litegraph's arrange pass leaves it. */
const MIN_WIDGET_HEIGHT = 180
/** Same floor notebook.js uses (MIN_WIDGET_WIDTH) — a two-pane layout this
 * narrow already reads as "the smallest that still fits both columns". */
const MIN_WIDGET_WIDTH = 320

/** Canvas rescan + entries refresh cadence (owner spec: "~5 s"). Far more
 * conservative than sets.js/controller.js's shared-poller cadence is worth
 * being, but this panel has no equivalent shared-poller infrastructure and
 * the spec forbids touching controller.js, so it's a small per-node
 * interval — one GET (entries, `known_mtime`-gated so an unchanged file
 * costs a stat, not a parse) plus a pure in-memory graph walk per tick. */
const POLL_MS = 5000

/** Row hover tooltip length (owner spec: "first ~200 chars"). */
const TOOLTIP_CHARS = 200

const EMPTY_NO_NOTEBOOK_HINT =
  'Add an EPS Prompt Notebook to choose the library file — this panel mirrors it.'
const EMPTY_NO_BLOCKS_HINT = 'Double-click a prompt on the left to add it.'
const NO_NOTEBOOK_OPTION_TEXT = 'no Prompt Notebook on canvas'

const STYLE_TAG_ID = 'eps-prompt-builder-styles'

/** Nodes we've already attached to — guards against a double `nodeCreated`. */
const attachedNodes = new WeakSet()

// ---------------------------------------------------------------------------
// Pure helpers — no DOM, no node — exported for tests/test_prompt_builder_js.py.
// ---------------------------------------------------------------------------

/**
 * Tolerant parse of the `blocks` widget's raw JSON-array-of-names value.
 * Anything malformed (not JSON, not an array, non-string members) degrades
 * to `[]` rather than throwing — a hand-edited or pre-M0 workflow must never
 * crash the panel.
 * @param {unknown} raw
 * @returns {string[]}
 */
export function parseBlocks(raw) {
  if (typeof raw !== 'string' || !raw) return []
  let parsed
  try {
    parsed = JSON.parse(raw)
  } catch {
    return []
  }
  if (!Array.isArray(parsed)) return []
  return parsed.filter((name) => typeof name === 'string')
}

/**
 * @param {unknown} list
 * @returns {string} JSON array of names — the `blocks` widget's write shape.
 */
export function serializeBlocks(list) {
  const names = Array.isArray(list) ? list.filter((name) => typeof name === 'string') : []
  return JSON.stringify(names)
}

/**
 * Clamp *idx* into `[0, max]`. Non-finite/negative/overflowing input all
 * degrade to a valid in-range index instead of corrupting the array.
 */
function clampIndex(idx, max) {
  const n = Number.isFinite(idx) ? Math.trunc(idx) : 0
  if (n < 0) return 0
  if (n > max) return max
  return n
}

/**
 * Move the item at *fromIdx* so it ends up at *toIdx* in the RESULTING
 * array (i.e. `toIdx` is the moved item's own final index, not an
 * insertion-point-in-the-original-array index — callers translate a DOM
 * drop position into that shape, see insertionIndexFromPoint()/
 * commitReorder() below). Both indices are clamped; an empty list is a
 * no-op. Never mutates *list*.
 * @param {string[]} list @param {number} fromIdx @param {number} toIdx
 * @returns {string[]}
 */
export function reorderBlocks(list, fromIdx, toIdx) {
  const arr = Array.isArray(list) ? list.slice() : []
  const len = arr.length
  if (len === 0) return arr
  const from = clampIndex(fromIdx, len - 1)
  const [item] = arr.splice(from, 1)
  const to = clampIndex(toIdx, arr.length) // arr is now len-1 long; 0..len-1 are all valid inserts
  arr.splice(to, 0, item)
  return arr
}

/**
 * Append *name* to the end of *list* — a double-clicked left row's write.
 * @param {string[]} list @param {string} name @returns {string[]}
 */
export function appendBlock(list, name) {
  const arr = Array.isArray(list) ? list.slice() : []
  if (typeof name === 'string' && name) arr.push(name)
  return arr
}

/**
 * Remove the item at *idx* only — an out-of-range index is a no-op, never a
 * throw or a silent wraparound.
 * @param {string[]} list @param {number} idx @returns {string[]}
 */
export function removeBlockAt(list, idx) {
  const arr = Array.isArray(list) ? list.slice() : []
  if (!Number.isInteger(idx) || idx < 0 || idx >= arr.length) return arr
  arr.splice(idx, 1)
  return arr
}

/** `path`'s final `/`- or `\`-separated segment (a bare name if there is
 * no separator at all). */
function basenameOf(path) {
  const normalized = String(path).replace(/\\/g, '/')
  const parts = normalized.split('/')
  return parts[parts.length - 1] || normalized
}

/**
 * Every discovered `LoraLibraryNotebook` node, reduced to the selector's
 * `<option>` list: `label = "<node title> — <file basename>"`,
 * `value = <file string>`, de-duplicated by `file` (first title wins).
 * @param {{title: string, file: string}[]} entries
 * @returns {{label: string, value: string}[]}
 */
export function notebookOptionsOf(entries) {
  const seen = new Set()
  const out = []
  for (const entry of Array.isArray(entries) ? entries : []) {
    if (!entry || typeof entry.file !== 'string') continue
    if (seen.has(entry.file)) continue
    seen.add(entry.file)
    const title = typeof entry.title === 'string' && entry.title ? entry.title : 'Notebook'
    out.push({ label: `${title} — ${basenameOf(entry.file)}`, value: entry.file })
  }
  return out
}

/**
 * Client-side name+text substring filter (case-insensitive) — a VIEW only,
 * never touches selection/widgets. `entries` is the `include_text=1` shape:
 * `[{name, text, ...}]`.
 * @param {{name?: string, text?: string}[]} entries @param {string} query
 * @returns {object[]}
 */
export function filterEntries(entries, query) {
  const list = Array.isArray(entries) ? entries : []
  const q = typeof query === 'string' ? query.trim().toLowerCase() : ''
  if (!q) return list.slice()
  return list.filter((entry) => {
    const name = typeof entry?.name === 'string' ? entry.name.toLowerCase() : ''
    const text = typeof entry?.text === 'string' ? entry.text.toLowerCase() : ''
    return name.includes(q) || text.includes(q)
  })
}

/**
 * Which `blocks` names are NOT among *entryNames* — the pre-queue "missing"
 * badge set (the backend errors loudly on a missing name at run time; the
 * badge is the warning before that happens). Deduplicated, order preserved.
 * @param {string[]} blocks @param {string[]} entryNames @returns {string[]}
 */
export function missingBlockNames(blocks, entryNames) {
  const known = new Set(Array.isArray(entryNames) ? entryNames : [])
  const out = []
  const seen = new Set()
  for (const name of Array.isArray(blocks) ? blocks : []) {
    if (typeof name !== 'string' || known.has(name) || seen.has(name)) continue
    seen.add(name)
    out.push(name)
  }
  return out
}

// ---------------------------------------------------------------------------
// Tiny DOM builder — matches every other module in this pack.
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
// Styles — one injected <style> tag, guarded so re-registration never
// duplicates it. Same theme-variable-with-literal-fallback idiom as
// notebook.js's CSS_TEXT.
// ---------------------------------------------------------------------------

let stylesInjected = false

const CSS_TEXT = `
.eps-pb-root {
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
.eps-pb-header {
  flex: 0 0 auto;
  display: flex;
  gap: 6px;
  padding: 4px 6px;
  border-bottom: 1px solid var(--border-color, #444);
  background: var(--comfy-menu-bg, #262626);
}
.eps-pb-select {
  flex: 1 1 auto;
  min-width: 0;
  background: var(--comfy-input-bg, #1e1e1e);
  border: 1px solid var(--border-color, #444);
  color: var(--input-text, #ccc);
  border-radius: 4px;
  padding: 3px 4px;
  font-size: 11px;
}
.eps-pb-search {
  flex: 1 1 auto;
  min-width: 0;
  box-sizing: border-box;
  border: 1px solid var(--border-color, #444);
  border-radius: 4px;
  background: var(--comfy-input-bg, #1c1c1c);
  color: var(--input-text, #ddd);
  padding: 3px 6px;
  font-size: 11px;
  outline: none;
}
.eps-pb-search:focus { border-color: rgb(66, 133, 244); }
.eps-pb-search::placeholder { color: var(--descrip-text, #808080); }
.eps-pb-panes {
  display: flex;
  flex-direction: row;
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.eps-pb-pane {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.eps-pb-pane-left { flex: 0 0 40%; border-right: 1px solid var(--border-color, #444); }
.eps-pb-pane-right { flex: 1 1 60%; }
.eps-pb-pane-title {
  flex: 0 0 auto;
  padding: 3px 7px;
  font-size: 9.5px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--descrip-text, #999);
  border-bottom: 1px solid var(--border-color, #444);
}
.eps-pb-list {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 3px;
}
.eps-pb-empty {
  padding: 6px 7px;
  color: var(--descrip-text, #999);
  font-style: italic;
}
.eps-pb-row-left {
  padding: 3px 7px;
  margin: 1px 0;
  border-radius: 3px;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  user-select: none;
}
.eps-pb-row-left:hover { background: var(--content-hover-bg, #2a2a2a); }
.eps-pb-row-right {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 7px;
  margin: 1px 0;
  border-radius: 3px;
  border: 1px solid transparent;
  background: var(--content-hover-bg, #262626);
}
.eps-pb-row-right.eps-pb-row-missing { border-color: var(--error-text, #ff4444); }
.eps-pb-row-dragging { opacity: 0.4; }
.eps-pb-row-right.eps-pb-drop-before { border-top: 2px solid rgba(66, 133, 244, 0.9); }
.eps-pb-row-right.eps-pb-drop-after { border-bottom: 2px solid rgba(66, 133, 244, 0.9); }
.eps-pb-pos {
  flex: 0 0 auto;
  min-width: 14px;
  color: var(--descrip-text, #999);
  font-size: 10px;
  text-align: right;
}
.eps-pb-name {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.eps-pb-handle {
  flex: 0 0 auto;
  cursor: grab;
  color: var(--descrip-text, #999);
  user-select: none;
}
.eps-pb-remove {
  flex: 0 0 auto;
  background: transparent;
  border: none;
  color: var(--descrip-text, #999);
  cursor: pointer;
  font-size: 11px;
  padding: 0 2px;
  line-height: 1;
}
.eps-pb-remove:hover { color: var(--error-text, #ff4444); }
.eps-pb-missing-badge {
  flex: 0 0 auto;
  color: var(--error-text, #ff4444);
  font-size: 9.5px;
  font-weight: 700;
  text-transform: uppercase;
  cursor: help;
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
// Node / widget lookups
// ---------------------------------------------------------------------------

function isPromptBuilderNode(node) {
  if (!node) return false
  if (node.comfyClass === CLASS_ID) return true
  if (node.constructor && node.constructor.comfyClass === CLASS_ID) return true
  return false
}

function isNotebookCanvasNode(node) {
  if (!node) return false
  if (node.comfyClass === NOTEBOOK_CLASS_ID) return true
  if (node.constructor && node.constructor.comfyClass === NOTEBOOK_CLASS_ID) return true
  return false
}

function findWidget(node, name) {
  return node.widgets?.find((w) => w && w.name === name)
}

// ---------------------------------------------------------------------------
// Public entry point
// ---------------------------------------------------------------------------

/**
 * Attach the two-pane panel to *node* when it is an EPSPromptBuilder; no-op
 * for every other node type. Never throws — a failure here leaves the
 * node's plain hidden `file`/`blocks` widgets exactly as the backend set
 * them, so the node still runs.
 * @param {object} node - LiteGraph node instance.
 */
export function attachPromptBuilderPanel(node) {
  try {
    if (!isPromptBuilderNode(node)) return
    if (attachedNodes.has(node)) return
    if (typeof node.addDOMWidget !== 'function') {
      api.warn('this ComfyUI frontend has no addDOMWidget; prompt builder panel not attached')
      return
    }
    const fileWidget = findWidget(node, 'file')
    const blocksWidget = findWidget(node, 'blocks')
    if (!fileWidget || !blocksWidget) {
      api.warn('EPSPromptBuilder node is missing its file/blocks widgets; panel not attached')
      return
    }
    attachedNodes.add(node)

    const state = createState(node, fileWidget, blocksWidget)
    buildUi(state)
    hideWidgetBothWays(fileWidget, node)
    hideWidgetBothWays(blocksWidget, node)
    wireNodeCleanup(state)
    wireConfigureReload(state)
    installPoll(state)

    // Restore-race guard (see file header): deferred one tick, skipped when
    // onConfigure already ran the reload for a workflow-restored node.
    state.attachLoadTimer = setTimeout(() => {
      state.attachLoadTimer = null
      if (state.configureReloaded) return
      rescanNotebooks(state, { force: true })
      reloadEntries(state).catch((error) => api.warn('initial prompt builder load failed', error))
    }, 0)
  } catch (error) {
    api.warn('attachPromptBuilderPanel failed', error)
  }
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

function createState(node, fileWidget, blocksWidget) {
  return {
    node,
    fileWidget,
    blocksWidget,
    // The file currently PAINTED (left pane) — distinct from
    // `fileWidget.value`, which reloadEntries() always re-reads fresh.
    file: null,
    exists: true,
    entries: [], // include_text=1 shape: [{name, text, ...}]
    // The right pane's ordered names — the widget's parsed value, kept in
    // sync by every write helper and re-synced from the widget on configure.
    blocks: parseBlocks(blocksWidget.value),
    searchQuery: '',
    // Selector's current option list + a signature to change-gate rebuilds.
    notebookOptions: [],
    notebookOptionsSignature: '',
    // known_mtime session-cache bookkeeping (notebook.js's own convention).
    paintedMtime: null,
    loadToken: 0,
    configureReloaded: false,
    attachLoadTimer: null,
    pollTimer: null,
    dragFromIndex: null,
    dropMarkerRow: null,
    // DOM refs, filled in by buildUi().
    root: null,
    domWidget: null,
    selectorEl: null,
    searchInputEl: null,
    leftListEl: null,
    rightListEl: null
  }
}

// ---------------------------------------------------------------------------
// UI construction
// ---------------------------------------------------------------------------

function buildUi(state) {
  injectStyles()

  state.selectorEl = el('select', {
    className: 'eps-pb-select',
    attrs: { title: 'Prompt Notebook to mirror' }
  })
  state.searchInputEl = el('input', {
    className: 'eps-pb-search',
    attrs: { type: 'text', placeholder: 'Search prompts…' }
  })
  const header = el('div', { className: 'eps-pb-header' }, [state.selectorEl, state.searchInputEl])

  state.leftListEl = el('div', { className: 'eps-pb-list eps-pb-list-left' })
  state.rightListEl = el('div', { className: 'eps-pb-list eps-pb-list-right' })

  const leftPane = el('div', { className: 'eps-pb-pane eps-pb-pane-left' }, [
    el('div', { className: 'eps-pb-pane-title', text: 'Library' }),
    state.leftListEl
  ])
  const rightPane = el('div', { className: 'eps-pb-pane eps-pb-pane-right' }, [
    el('div', { className: 'eps-pb-pane-title', text: 'Blocks' }),
    state.rightListEl
  ])
  const panes = el('div', { className: 'eps-pb-panes' }, [leftPane, rightPane])

  state.root = el('div', { className: 'eps-pb-root' }, [header, panes])

  wireHeaderEvents(state)
  wireRightListDnD(state)

  renderSelector(state)
  renderLeftPane(state)
  renderRightPane(state)

  state.domWidget = attachDomWidget(state.node, state.root)
  installMinWidth(state.node, MIN_WIDGET_WIDTH)
}

/** Wraps `node.addDOMWidget` — notebook.js's attachDomWidget() explains why
 * BOTH `domWidget.serialize` and `domWidget.serializeValue` are needed. */
function attachDomWidget(node, rootEl) {
  const domWidget = node.addDOMWidget(WIDGET_NAME, WIDGET_TYPE, rootEl, {
    hideOnZoom: true,
    serialize: false,
    getMinHeight: () => MIN_WIDGET_HEIGHT
  })
  domWidget.serialize = false
  domWidget.serializeValue = () => undefined
  return domWidget
}

/** notebook.js's installMinWidth(), unchanged technique: only ever GROWS a
 * node up to the floor, never shrinks one already wider. */
function installMinWidth(node, minWidth) {
  if (!node || node.__epsPromptBuilderMinWidthInstalled) return
  node.__epsPromptBuilderMinWidthInstalled = true
  const originalOnResize = node.onResize
  node.onResize = function (size) {
    if (size && size[0] < minWidth) size[0] = minWidth
    return originalOnResize?.call(this, size)
  }
  if (Array.isArray(node.size) && node.size[0] < minWidth) {
    node.size[0] = minWidth
  }
}

/** Both hide flags — canvas reads `widget.hidden`, Vue nodes read
 * `options.hidden` and ignore `widget.hidden` entirely (notebook.js's
 * hideFileWidget()/hidePinnedWidget(), same pair). */
function hideWidgetBothWays(widget, node) {
  if (!widget) return
  widget.hidden = true
  widget.options = { ...(widget.options || {}), hidden: true }
  node.graph?.setDirtyCanvas(true, true)
}

function wireHeaderEvents(state) {
  // Re-scan on focus (owner spec) — cheap: rescanNotebooks() itself is
  // change-gated and only rebuilds/repaints when the discovered set differs.
  state.selectorEl.addEventListener('focus', () => rescanNotebooks(state))
  state.selectorEl.addEventListener('change', () => {
    const value = state.selectorEl.value
    if (!value) return
    writeFileWidget(state, value)
  })
  state.selectorEl.addEventListener('keydown', (event) => event.stopPropagation())

  state.searchInputEl.addEventListener('input', () => {
    state.searchQuery = state.searchInputEl.value
    renderLeftPane(state)
  })
  state.searchInputEl.addEventListener('keydown', (event) => event.stopPropagation())
}

// ---------------------------------------------------------------------------
// Node cleanup / configure-restore
// ---------------------------------------------------------------------------

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
      api.warn('prompt builder teardown failed', error)
    }
    return result
  }
}

function teardown(state) {
  if (state.pollTimer) clearInterval(state.pollTimer)
  if (state.attachLoadTimer) clearTimeout(state.attachLoadTimer)
  state.loadToken += 1
}

/** Restore-race fix (see file header): `onConfigure` is the only hook that
 * fires AFTER `widgets_values` are restored, for both a whole-workflow load
 * and a pasted/cloned node — chained, never replaced, exactly like
 * notebook.js's own `wireConfigureReload`. */
function wireConfigureReload(state) {
  const node = state.node
  const originalOnConfigure = node.onConfigure
  node.onConfigure = function (...args) {
    let result
    if (typeof originalOnConfigure === 'function') {
      try {
        result = originalOnConfigure.apply(this, args)
      } catch (error) {
        api.warn('original node onConfigure threw', error)
      }
    }
    try {
      state.configureReloaded = true
      state.blocks = parseBlocks(state.blocksWidget.value)
      renderRightPane(state)
      rescanNotebooks(state, { force: true })
      reloadEntries(state).catch((error) =>
        api.warn('post-configure prompt builder reload failed', error)
      )
    } catch (error) {
      api.warn('prompt builder configure reload failed', error)
    }
    return result
  }
}

// ---------------------------------------------------------------------------
// Poll (owner spec: "~5 s", change-gated)
// ---------------------------------------------------------------------------

function installPoll(state) {
  state.pollTimer = setInterval(() => {
    if (document.hidden) return
    onPollTick(state)
  }, POLL_MS)
}

/** Deliberately does nothing unconditional: rescanNotebooks() only rebuilds
 * options (and only then repaints/writes) when the discovered notebook set
 * actually changed, and reloadEntries()'s `known_mtime` short-circuit means
 * an unchanged file never re-renders or dirties the canvas either — no bare
 * per-tick `setDirtyCanvas` call lives in this function. */
function onPollTick(state) {
  try {
    rescanNotebooks(state)
  } catch (error) {
    api.warn('prompt builder canvas rescan failed', error)
  }
  reloadEntries(state).catch((error) => api.warn('prompt builder poll reload failed', error))
}

// ---------------------------------------------------------------------------
// Notebook discovery + selector
// ---------------------------------------------------------------------------

/** Every live `LoraLibraryNotebook` node in the WHOLE workflow, subgraphs
 * included — rooted at `app.graph`, never `state.node.graph`, so a builder
 * living inside a subgraph still finds notebooks anywhere else in it
 * (picker.js's findSendCandidates() precedent). */
function discoverNotebookCandidates() {
  if (!app.graph) return []
  const out = []
  for (const { node } of walkLiveNodes(app.graph)) {
    if (!isNotebookCanvasNode(node)) continue
    const fw = findWidget(node, 'file')
    out.push({
      title: node.title || node.type || 'Notebook',
      file: typeof fw?.value === 'string' ? fw.value : ''
    })
  }
  return out
}

/**
 * Re-scan the canvas for Notebook nodes; change-gated on the built option
 * list's signature, so a tick/focus that finds nothing new touches neither
 * the DOM nor the widgets. `force` (attach, configure) always rebuilds.
 */
function rescanNotebooks(state, { force = false } = {}) {
  const candidates = discoverNotebookCandidates()
  const options = notebookOptionsOf(candidates)
  const signature = JSON.stringify(options)
  if (!force && signature === state.notebookOptionsSignature) return false
  state.notebookOptionsSignature = signature
  state.notebookOptions = options
  renderSelector(state)
  renderLeftPane(state) // the "no notebook on canvas" empty-state depends on this set
  autoSelectNotebookFile(state)
  return true
}

/** Exactly one candidate + our `file` is empty or points at something no
 * longer offered -> adopt it. A file the selector still offers is always
 * kept, whatever the candidate count (owner spec: "previously selected file
 * still present -> keep it"). */
function autoSelectNotebookFile(state) {
  const options = state.notebookOptions
  const current = state.fileWidget.value ?? ''
  if (options.some((opt) => opt.value === current)) return
  if (options.length === 1) writeFileWidget(state, options[0].value)
}

function renderSelector(state) {
  const select = state.selectorEl
  const options = state.notebookOptions
  if (!options.length) {
    select.replaceChildren(
      el('option', {
        text: NO_NOTEBOOK_OPTION_TEXT,
        attrs: { value: '', disabled: 'disabled', selected: 'selected' }
      })
    )
    select.disabled = true
    return
  }
  select.disabled = false
  const current = state.fileWidget.value ?? ''
  select.replaceChildren(
    ...options.map((opt) => el('option', { text: opt.label, attrs: { value: opt.value } }))
  )
  if (options.some((opt) => opt.value === current)) select.value = current
}

/** Writes `value` through the `file` widget's real setter+callback (so the
 * graph marks dirty and the run-count estimator recomputes), then reloads
 * the left pane for the newly-chosen file. A same-value pick is a no-op —
 * this widget is never user-typed, only ever set from the option list, so
 * there is no notebook.js-style "displaying something else" seam to guard. */
function writeFileWidget(state, value) {
  const widget = state.fileWidget
  if (widget.value === value) return
  widget.value = value
  try {
    widget.callback?.(value)
  } catch (error) {
    api.warn('file widget callback threw', error)
  }
  state.node.graph?.setDirtyCanvas(true, true)
  state.paintedMtime = null
  reloadEntries(state).catch((error) => api.warn('reload after file change failed', error))
}

// ---------------------------------------------------------------------------
// Left pane — entries (read-only mirror of the Notebook's file)
// ---------------------------------------------------------------------------

/**
 * GET the current `file` widget's entries, `known_mtime`-gated and
 * session-cache-backed via notebook.js's exported cache helpers (so this
 * panel and any open Notebook on the same file share one cache). Missing
 * badges depend on `entries` regardless of whether any Notebook node is
 * currently on canvas, so this always runs off the widget value, never off
 * `notebookOptions`.
 */
async function reloadEntries(state) {
  const file = state.fileWidget.value ?? ''
  if (!file) {
    state.file = file
    state.exists = false
    state.entries = []
    state.paintedMtime = null
    renderLeftPane(state)
    renderRightPane(state)
    return
  }

  const token = ++state.loadToken
  const cached = notebookCacheGet(file)
  const showing = state.file === file
  let paintedFromCache = false
  if (cached && (!showing || state.paintedMtime !== cached.mtime)) {
    paintedFromCache = true
    applyEntriesPayload(state, file, cached.payload)
  }

  const params = { file, include_text: '1' }
  if ((showing || paintedFromCache) && typeof state.paintedMtime === 'number') {
    params.known_mtime = String(state.paintedMtime)
  }

  let data
  try {
    data = await api.getJson('/lora_library/notebook', params)
  } catch (error) {
    if (token !== state.loadToken) return
    api.warn('failed to load notebook entries for the prompt builder', error)
    return
  }
  if (token !== state.loadToken) return
  if (isUnchangedResponse(data)) return // keep what's painted — see notebook.js's own contract

  applyEntriesPayload(state, file, data)
  notebookCacheSet(file, data, typeof data.mtime === 'number' ? data.mtime : null)
}

function applyEntriesPayload(state, file, data) {
  state.file = file
  state.exists = !!data.exists
  state.entries = Array.isArray(data.entries) ? data.entries : []
  state.paintedMtime = typeof data.mtime === 'number' ? data.mtime : null
  renderLeftPane(state)
  renderRightPane(state) // missing-badge set depends on state.entries too
}

function firstChars(text, n) {
  if (typeof text !== 'string') return ''
  return text.length > n ? text.slice(0, n) : text
}

/** Deliberately no add/rename/delete affordance anywhere in here — every
 * edit stays in the Notebook (owner spec, "read-only here"). */
function renderLeftPane(state) {
  state.leftListEl.replaceChildren()

  if (!state.notebookOptions.length) {
    state.leftListEl.append(el('div', { className: 'eps-pb-empty', text: EMPTY_NO_NOTEBOOK_HINT }))
    return
  }

  const filtered = filterEntries(state.entries, state.searchQuery)
  if (!filtered.length) {
    const text = state.entries.length
      ? 'No prompts match your search.'
      : 'This notebook has no prompts yet.'
    state.leftListEl.append(el('div', { className: 'eps-pb-empty', text }))
    return
  }

  for (const entry of filtered) {
    const name = typeof entry?.name === 'string' ? entry.name : ''
    const row = el('div', {
      className: 'eps-pb-row-left',
      text: name,
      attrs: { tabindex: '0', title: firstChars(entry?.text, TOOLTIP_CHARS) }
    })
    row.addEventListener('dblclick', (event) => {
      event.stopPropagation()
      onEntryDoubleClick(state, name)
    })
    state.leftListEl.append(row)
  }
}

function onEntryDoubleClick(state, name) {
  if (!name) return
  writeBlocksWidget(state, appendBlock(state.blocks, name))
}

// ---------------------------------------------------------------------------
// Right pane — blocks (position, name, drag handle, ✕)
// ---------------------------------------------------------------------------

function writeBlocksWidget(state, list) {
  state.blocks = list
  const raw = serializeBlocks(list)
  const widget = state.blocksWidget
  if (widget.value !== raw) {
    widget.value = raw
    try {
      widget.callback?.(raw)
    } catch (error) {
      api.warn('blocks widget callback threw', error)
    }
    state.node.graph?.setDirtyCanvas(true, true)
  }
  renderRightPane(state)
}

function onRemoveBlockClick(state, idx) {
  writeBlocksWidget(state, removeBlockAt(state.blocks, idx))
}

function renderRightPane(state) {
  state.rightListEl.replaceChildren()
  const blocks = state.blocks

  if (!blocks.length) {
    state.rightListEl.append(el('div', { className: 'eps-pb-empty', text: EMPTY_NO_BLOCKS_HINT }))
    return
  }

  const entryNames = state.entries.map((entry) => entry?.name).filter((n) => typeof n === 'string')
  const missing = new Set(missingBlockNames(blocks, entryNames))

  blocks.forEach((name, idx) => {
    const isMissing = missing.has(name)
    const row = el('div', {
      className: 'eps-pb-row-right' + (isMissing ? ' eps-pb-row-missing' : ''),
      attrs: { draggable: 'true', 'data-index': String(idx) }
    })
    row.append(
      el('span', { className: 'eps-pb-pos', text: String(idx + 1) }),
      el('span', { className: 'eps-pb-name', text: name })
    )
    if (isMissing) {
      row.append(
        el('span', {
          className: 'eps-pb-missing-badge',
          text: 'missing',
          attrs: {
            title:
              `"${name}" is not in the current notebook — the run will fail until it's ` +
              'removed from this list or the entry is restored in the Notebook.'
          }
        })
      )
    }
    row.append(
      el('span', { className: 'eps-pb-handle', text: '⠿', attrs: { title: 'Drag to reorder' } })
    )
    const removeBtn = el('button', {
      className: 'eps-pb-remove',
      text: '✕',
      attrs: { title: 'Remove from this list only — does not delete the prompt' }
    })
    removeBtn.addEventListener('click', (event) => {
      event.stopPropagation()
      onRemoveBlockClick(state, idx)
    })
    row.append(removeBtn)

    wireRowDrag(state, row, idx)
    state.rightListEl.append(row)
  })
}

// -------------------------------------------------------- drag-to-reorder

/** Per-row source wiring — HTML5 native DnD (see file header for why this
 * differs from notebook.js's pointer-based technique). */
function wireRowDrag(state, row, idx) {
  row.addEventListener('dragstart', (event) => {
    event.stopPropagation()
    state.dragFromIndex = idx
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = 'move'
      try {
        event.dataTransfer.setData('text/plain', String(idx))
      } catch {
        // Some browsers refuse setData outside a user gesture context —
        // dragFromIndex above is the actual source of truth either way.
      }
    }
    row.classList.add('eps-pb-row-dragging')
  })
  row.addEventListener('dragend', (event) => {
    event.stopPropagation()
    row.classList.remove('eps-pb-row-dragging')
    state.dragFromIndex = null
    clearDropMarker(state)
  })
}

/** Container-level dragover/drop — computed against the CURRENT row
 * geometry rather than per-row, so dropping below the last row (or above
 * the first) resolves correctly without a dedicated end-of-list target. */
function wireRightListDnD(state) {
  const container = state.rightListEl
  container.addEventListener('dragover', (event) => {
    if (state.dragFromIndex == null) return
    event.preventDefault()
    event.stopPropagation()
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
    const raw = insertionIndexFromPoint(state, event.clientY)
    markDropIndex(state, raw)
  })
  container.addEventListener('drop', (event) => {
    if (state.dragFromIndex == null) return
    event.preventDefault()
    event.stopPropagation()
    const from = state.dragFromIndex
    const raw = insertionIndexFromPoint(state, event.clientY)
    clearDropMarker(state)
    state.dragFromIndex = null
    commitReorder(state, from, raw)
  })
  container.addEventListener('dragleave', (event) => {
    if (event.target === container) clearDropMarker(state)
  })
}

/** The "insert before original index N" position implied by *clientY*,
 * against the rows as currently rendered (the dragged row is still among
 * them until the render that follows the drop). */
function insertionIndexFromPoint(state, clientY) {
  const rows = Array.from(state.rightListEl.querySelectorAll('.eps-pb-row-right'))
  for (let i = 0; i < rows.length; i++) {
    const rect = rows[i].getBoundingClientRect()
    if (clientY < rect.top + rect.height / 2) return i
  }
  return rows.length
}

function markDropIndex(state, rawInsertion) {
  clearDropMarker(state)
  const rows = Array.from(state.rightListEl.querySelectorAll('.eps-pb-row-right'))
  if (rawInsertion < rows.length) {
    rows[rawInsertion].classList.add('eps-pb-drop-before')
    state.dropMarkerRow = rows[rawInsertion]
  } else if (rows.length) {
    rows[rows.length - 1].classList.add('eps-pb-drop-after')
    state.dropMarkerRow = rows[rows.length - 1]
  }
}

function clearDropMarker(state) {
  state.dropMarkerRow?.classList.remove('eps-pb-drop-before', 'eps-pb-drop-after')
  state.dropMarkerRow = null
}

/**
 * Translate a DOM "insert before original index N" position into
 * reorderBlocks()'s "final index" shape (removing the dragged item shifts
 * every later index down by one), then commit through the pure helper.
 */
function commitReorder(state, from, rawInsertion) {
  const to = rawInsertion > from ? rawInsertion - 1 : rawInsertion
  writeBlocksWidget(state, reorderBlocks(state.blocks, from, to))
}
