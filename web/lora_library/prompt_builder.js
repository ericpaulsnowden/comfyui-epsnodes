/**
 * @file EPS Prompt Builder DOM widget (companion to the EPS Prompt Notebook,
 * FORMAT.md §7.2-family conventions) — attaches to `EPSPromptBuilder` nodes.
 * LEFT pane: a read-only, searchable mirror of a Notebook's `.md` file (same
 * order/names the Notebook shows, GROUPED under the same category headers —
 * no add/rename/delete here, all editing stays in the Notebook). RIGHT pane:
 * an ordered list of BLOCKS — live references to prompt NAMES, appended by
 * double-clicking a left row, drag-reordered, and removed with ✕ (which only
 * splices this list, never touches the file). The combined text is assembled
 * by the BACKEND at run time (`separator`-joined); this panel only ever
 * writes the two hidden widgets `file` and `blocks`.
 *
 * Owner ask 2026-09-01: "you should not be able to add a prompt more than
 * once. The left column should have the same groups as the notebook it is
 * mirroring." Two changes, both load-bearing:
 *  - A name already in `blocks` reads as added (dimmed row + badge) and its
 *    left row's double-click listener is never attached — see the `added`
 *    branch in renderLeftPane() below. The actual guard lives one layer
 *    down, in appendBlock() itself, so it holds regardless of which gesture
 *    tries to add a name a second time (today only double-click; a restored
 *    `blocks` value containing a repeat is guarded separately, in
 *    parseBlocks() — see its own doc).
 *  - renderLeftPane() groups `state.entries` by `.category` (already on
 *    every entry markdown_store.py's `list_entries()` returns — no backend
 *    change needed) via groupEntriesByCategory() below, and renders a
 *    collapsible header per named group exactly like notebook.js's own left
 *    column: the leading, un-headed `''` category never gets a header of
 *    its own, and collapse persists in the same-named `Collapsed sections`
 *    node PROPERTY (never a widget — pure view state, §7.9/§8) via
 *    notebook.js's own exported pure parse/toggle/query helpers, reused
 *    verbatim rather than reinvented.
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
 *  - Collapsed groups (notebook.js's `PROP_COLLAPSED_SECTIONS` section):
 *    its exported `parseCollapsedSections`/`toggleCollapsedSection`/
 *    `isSectionCollapsed` are pure and already do exactly what this panel's
 *    read-only category headers need, so they're imported rather than
 *    re-written — only the property NAME (`'Collapsed sections'`, a local
 *    const here, same posture as `NOTEBOOK_CLASS_ID` below) is duplicated,
 *    since each node owns its own `properties` object.
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
import { notebookCacheGet, notebookCacheSet, isUnchangedResponse, parseCollapsedSections, toggleCollapsedSection, isSectionCollapsed } from './notebook.js'

/** FORMAT.md — frozen once shipped. */
export const CLASS_ID = 'EPSPromptBuilder'

/** The Notebook's own class id (notebook.js's `NODE_CLASS`, not exported —
 * deliberately duplicated here, same posture as pll_bridge.js duplicating
 * controller.js's PROP_* constants: a small, stable, cross-file constant is
 * cheaper to keep byte-identical by inspection than to import. */
const NOTEBOOK_CLASS_ID = 'LoraLibraryNotebook'

const WIDGET_NAME = 'prompt_builder'
const WIDGET_TYPE = 'eps_prompt_builder'

/** FORMAT.md §6.15/§8 (unsaved-edit drafts, owner report 2026-09-02: "if you
 * have a prompt that is edited but not saved, but have it loaded via the
 * prompt builder instead of directly via the node itself, the saved version
 * fires and not the edited version"). The backend's TAIL STRING widget
 * (hidden, default `"{}"`) that this panel keeps mirroring from whichever
 * Notebook candidate's `file` matches ours (`syncMirroredDrafts` below) --
 * looked up by NAME, same as `file`/`blocks`, so a backend that predates it
 * simply leaves every draft path a no-op. notebook.js's own
 * `DRAFTS_WIDGET_NAME` constant, duplicated rather than imported -- same
 * posture as `NOTEBOOK_CLASS_ID` above (a small, stable, cross-file
 * constant is cheaper to keep byte-identical by inspection). */
const DRAFTS_WIDGET_NAME = 'drafts'
/** `nodes_prompt_builder.py`'s own `DEFAULT_DRAFTS` -- "no unsaved edits". */
const DEFAULT_DRAFTS_VALUE = '{}'

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

/**
 * 2026-08-26 while-running round (findings 4 + 5): mid-run the server is
 * GIL-busy and the library may sit on a slow NAS mount, so this panel's own
 * `POLL_MS` cadence stacked badly two different ways:
 *  - finding 4: a single node's own tick could start a new `reloadEntries`
 *    while the PREVIOUS tick's fetch for that same node was still
 *    outstanding (a slow server can easily take longer than POLL_MS to
 *    answer), piling concurrent requests from ONE node onto the worst
 *    possible moment;
 *  - finding 5: every Builder node mirroring the SAME notebook file polled
 *    independently, so N nodes on one file fired N near-simultaneous GETs
 *    for identical data every tick.
 * Finding 4 is handled per-node in `onPollTick` (`state.pollReloadInFlight`,
 * below). Finding 5 is handled here: a module-scope single-flight + short
 * TTL cache keyed by `file`, shared by every Builder instance -- each
 * instance still applies/repaints from whatever payload the shared fetch
 * resolves with, exactly as if it had fetched it itself. TTL is short
 * (well under POLL_MS) purely to absorb a same-tick fanout across nodes;
 * it is not a substitute for the server's own `known_mtime` short-circuit,
 * which is still what keeps an UNCHANGED file cheap tick over tick.
 */
const ENTRIES_FETCH_TTL_MS = 1500
/** file -> {data, fetchedAt} -- a fresh successful GET /lora_library/notebook. */
const entriesFetchCache = new Map()
/** file -> in-flight GET promise (resolves/rejects exactly like
 * `api.getJson`) -- joined by every Builder instance sharing that file. */
const entriesFetchInFlight = new Map()

/**
 * GET `/lora_library/notebook` for *file*, shared across every Builder
 * instance mirroring it (finding 5) and across a single node's own
 * overlapping ticks (finding 4, composed with `state.pollReloadInFlight`).
 * *knownMtime* only shapes the request that actually goes OUT -- a caller
 * that joins an in-flight fetch or a fresh TTL hit gets whatever that
 * fetch was already sent with. That is safe here specifically because
 * every caller derives its own `known_mtime` from the SAME shared session
 * cache (`notebookCacheGet`/`notebookCacheSet`, imported from notebook.js)
 * before deciding what to send -- two callers within the TTL window can
 * only disagree on `known_mtime` if one of them is about to read that very
 * cache and land on the same value anyway, so sharing the resolved answer
 * never serves a caller a staler view than it would have painted itself.
 * @param {string} file @param {number|null} knownMtime
 */
function sharedReloadFetch(file, knownMtime) {
  const cached = entriesFetchCache.get(file)
  if (cached && Date.now() - cached.fetchedAt < ENTRIES_FETCH_TTL_MS) {
    return Promise.resolve(cached.data)
  }
  const inFlight = entriesFetchInFlight.get(file)
  if (inFlight) return inFlight
  const params = { file, include_text: '1' }
  if (typeof knownMtime === 'number') params.known_mtime = String(knownMtime)
  const promise = api
    .getJson('/lora_library/notebook', params)
    .then((data) => {
      entriesFetchCache.set(file, { data, fetchedAt: Date.now() })
      return data
    })
    .finally(() => {
      entriesFetchInFlight.delete(file)
    })
  entriesFetchInFlight.set(file, promise)
  return promise
}

// ---------------------------------------------------------------------------
// Pure helpers — no DOM, no node — exported for tests/test_prompt_builder_js.py.
// ---------------------------------------------------------------------------

/**
 * Tolerant parse of the `blocks` widget's raw JSON-array-of-names value.
 * Anything malformed (not JSON, not an array, non-string members) degrades
 * to `[]` rather than throwing — a hand-edited or pre-M0 workflow must never
 * crash the panel. Also DEDUPES (owner ask 2026-09-01: "you should not be
 * able to add a prompt more than once") — a repeat surviving into a saved
 * `blocks` value (a hand edit, or a workflow saved before this guard
 * existed) is silently dropped here rather than rendered as two rows or
 * fixed up loudly; same known/seen membership-before-collect posture as
 * missingBlockNames() below, first occurrence wins.
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
  const seen = new Set()
  const out = []
  for (const name of parsed) {
    if (typeof name !== 'string' || seen.has(name)) continue
    seen.add(name)
    out.push(name)
  }
  return out
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
 * A no-op when *name* is already present (owner ask 2026-09-01: "you should
 * not be able to add a prompt more than once") — this is the single choke
 * point every add path runs through (today only the left row's dblclick;
 * the guard living here rather than only in the UI means any future path —
 * a drag, a hand-triggered call — inherits it too), same membership-check-
 * before-mutating posture missingBlockNames() below already uses.
 * @param {string[]} list @param {string} name @returns {string[]}
 */
export function appendBlock(list, name) {
  const arr = Array.isArray(list) ? list.slice() : []
  if (typeof name !== 'string' || !name) return arr
  if (arr.includes(name)) return arr
  arr.push(name)
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

/**
 * Groups *entries* (the `include_text=1` shape:
 * `[{name, category, text, ...}]` — `list_entries()`'s own field, arriving
 * unchanged) into contiguous runs by `.category`, in FILE ORDER — never
 * re-sorted, mirroring notebook.js's own left column exactly (owner ask
 * 2026-09-01: "the left column should have the same groups as the notebook
 * it is mirroring"). A missing/non-string `.category` degrades to `''`,
 * markdown_store.py's own convention for the notebook's leading, un-headed
 * region (FORMAT.md §3.1) — notebook.js's renderList() never renders a
 * header for that region either, and renderLeftPane() below matches that.
 * Never throws: a non-array *entries*, or a non-object member, degrades to
 * `[]`/skipped rather than crashing the panel.
 * @param {{name?: string, category?: string}[]} entries
 * @returns {{category: string, entries: object[]}[]}
 */
export function groupEntriesByCategory(entries) {
  const list = Array.isArray(entries) ? entries : []
  const groups = []
  let current = null
  for (const entry of list) {
    if (!entry || typeof entry !== 'object') continue
    const category = typeof entry.category === 'string' ? entry.category : ''
    if (!current || current.category !== category) {
      current = { category, entries: [] }
      groups.push(current)
    }
    current.entries.push(entry)
  }
  return groups
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
.eps-pb-row-left-added {
  opacity: 0.5;
  cursor: default;
}
.eps-pb-row-left-added:hover { background: transparent; }
.eps-pb-added-badge {
  margin-left: 6px;
  color: var(--descrip-text, #999);
  font-size: 9px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}
.eps-pb-group-header {
  padding: 3px 7px;
  margin: 3px 0 1px;
  border-radius: 3px;
  font-size: 9.5px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--descrip-text, #999);
  cursor: pointer;
  user-select: none;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.eps-pb-group-header:hover { background: var(--content-hover-bg, #2a2a2a); }
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
    // Unsaved-edit drafts (owner report 2026-09-02) -- null on a backend
    // that predates it, same no-op-below convention as notebook.js's own
    // pinned/drafts widget lookups: every draft path degrades to nothing
    // rather than refusing to attach.
    const draftsWidget = findWidget(node, DRAFTS_WIDGET_NAME) || null
    attachedNodes.add(node)

    const state = createState(node, fileWidget, blocksWidget, draftsWidget)
    buildUi(state)
    // Collapsed groups persist WITH THE WORKFLOW (§7.9) — must run after
    // buildUi() (state.leftListEl has to exist for the wrapped
    // onPropertyChanged's eventual renderLeftPane() calls) and before this
    // function returns, so it's in place before ComfyUI's next
    // node.configure() call for a restored node — notebook.js's own
    // registerCollapsedSectionsProperty() placement, verbatim.
    registerCollapsedSectionsProperty(state)
    hideWidgetBothWays(fileWidget, node)
    hideWidgetBothWays(blocksWidget, node)
    hideWidgetBothWays(draftsWidget, node)
    wireNodeCleanup(state)
    wireConfigureReload(state)
    // Universal State Controller Apply fix (see resyncAfterExternalWrite's
    // own doc comment): publish this node's reload seam and make sure the
    // one shared subscription is installed.
    node.__epsPromptBuilderReload = () => resyncAfterExternalWrite(state)
    installExternalWriteSubscription()
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

function createState(node, fileWidget, blocksWidget, draftsWidget = null) {
  return {
    node,
    fileWidget,
    blocksWidget,
    // Unsaved-edit drafts (owner report 2026-09-02) -- null on a backend
    // that predates the widget, in which case syncMirroredDrafts() is
    // always a no-op (mirrors notebook.js's own draftsWidget field).
    draftsWidget,
    // The file currently PAINTED (left pane) — distinct from
    // `fileWidget.value`, which reloadEntries() always re-reads fresh.
    file: null,
    exists: true,
    entries: [], // include_text=1 shape: [{name, text, ...}]
    // The right pane's ordered names — the widget's parsed value, kept in
    // sync by every write helper and re-synced from the widget on configure.
    blocks: parseBlocks(blocksWidget.value),
    searchQuery: '',
    // Left-column category collapse — the render-time cache backing the
    // `Collapsed sections` node PROPERTY (registerCollapsedSectionsProperty
    // below); a fresh node's default until that registration (or a
    // restored node's configure()) applies the real saved value.
    collapsedSections: [],
    // Selector's current option list + a signature to change-gate rebuilds.
    notebookOptions: [],
    notebookOptionsSignature: '',
    // known_mtime session-cache bookkeeping (notebook.js's own convention).
    paintedMtime: null,
    loadToken: 0,
    configureReloaded: false,
    attachLoadTimer: null,
    pollTimer: null,
    // Finding 4 (2026-08-26 while-running round): the in-flight
    // reloadEntries() promise a POLL tick started, or null — onPollTick
    // skips a tick outright while this is set rather than stacking a
    // second concurrent request. Not touched by non-poll callers
    // (attach/configure/manual selection), which always run their own
    // reload regardless of a poll's own outstanding fetch.
    pollReloadInFlight: null,
    // The `visibilitychange` listener installed by installPoll(), so
    // teardown() can remove it (finding 4's "flush one on visibilitychange").
    visibilityHandler: null,
    dragFromIndex: null,
    dropMarkerRow: null,
    // Finding 6: a poll landed while a native HTML5 block drag was in
    // progress and renderRightPane() deferred itself — flushed once on
    // dragend (wireRowDrag below).
    rightPaneRenderPending: false,
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
  // v0.68.1 / ported here 2026-09-08: `node.size` is a PROXY over a typed
  // array, so the guard this replaced (an isArray check) was always false --
  // a node created narrower than `minWidth` never had its width lifted at
  // all (only the `onResize` wrap above protected LATER resizes). The fix
  // reached five of this helper's nine copies in v0.68.1 and missed the four
  // biggest panels for months; see docs/ROADMAP-shared-panel-code.md M0, and
  // grep `web/` for this helper before touching it again. Lift through
  // `setSize` so litegraph's own size mirror runs.
  if (node.size && node.size[0] < minWidth && typeof node.setSize === 'function') {
    node.setSize([minWidth, node.size[1]])
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
  if (state.visibilityHandler) {
    document.removeEventListener('visibilitychange', state.visibilityHandler)
  }
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

/**
 * Universal State Controller Apply fix (2026-08-29, owner report:
 * "applying any of the sets won't change anything") -- api.js's
 * `announceWidgetsChangedExternally()` calls this (via
 * `node.__epsPromptBuilderReload`, below) after a programmatic
 * `widget.value = x; widget.callback?.()` write to this node's `file` or
 * `blocks` widget (`nodes_prompt_builder.py`'s `EPS_STATE_WIDGETS` declares
 * both, plus `separator` — a plain VISIBLE text widget this panel never
 * reads, so it needs no resync here: it repaints itself every canvas
 * frame like any other litegraph widget).
 *
 * Deliberately NOT `wireConfigureReload()`'s full body: that function
 * ALWAYS calls `reloadEntries()`, which issues a real GET even when the
 * cache already has the answer (its own `known_mtime` short-circuit only
 * makes that GET cheap, never absent) — fine for an actual configure-time
 * restore, but a violation of "never fires a network request when the
 * panel can repaint from cached data" for a `blocks`-only Apply that never
 * touched `file` at all. So: `file` changed -> a DIFFERENT file's entries
 * were never fetched, fall through to the real reload; `file` unchanged,
 * only `blocks` (block membership/order) changed -> re-parse `blocks` off
 * the widget and repaint the right pane alone, purely from `state.entries`
 * already cached by the last load -- no network. `renderRightPane()`
 * already guards against clobbering a native HTML5 drag in progress
 * (deferring the repaint until it ends), so this needs no extra
 * idempotency handling of its own.
 */
function resyncAfterExternalWrite(state) {
  state.blocks = parseBlocks(state.blocksWidget.value)
  const fileChanged = (state.fileWidget.value ?? '') !== state.file
  if (fileChanged) {
    reloadEntries(state).catch((error) =>
      api.warn('prompt builder reload after external widget change failed', error)
    )
    return
  }
  renderRightPane(state)
}

// One shared subscription to api.js's `announceWidgetsChangedExternally()`
// serves every attached Prompt Builder node -- installed once,
// idempotently, from the first `attachPromptBuilderPanel()` call. Routes
// by NODE IDENTITY (the announce entry's own `.node` reference against the
// `__epsPromptBuilderReload` seam stamped on each attached node -- picker.js's
// `__epsLpReload` precedent).
let externalWriteSubscribed = false

function installExternalWriteSubscription() {
  if (externalWriteSubscribed) return
  externalWriteSubscribed = true
  api.subscribeWidgetsChangedExternally((entries) => {
    for (const entry of entries || []) entry?.node?.__epsPromptBuilderReload?.()
  })
}

// ---------------------------------------------------------------------------
// Poll (owner spec: "~5 s", change-gated)
// ---------------------------------------------------------------------------

function installPoll(state) {
  state.pollTimer = setInterval(() => {
    if (document.hidden) return
    onPollTick(state)
  }, POLL_MS)
  // Finding 4 (2026-08-26 while-running round): ticks are skipped outright
  // while the tab is hidden (above) — a background tab must not add to a
  // GIL-busy server's load. That leaves the panel showing whatever was last
  // painted for up to POLL_MS after the tab comes back; flush ONE reload
  // the moment it's visible again instead of waiting for the next tick.
  // `onPollTick`'s own `pollReloadInFlight` guard covers a flush racing an
  // already-scheduled tick, so this never doubles up.
  state.visibilityHandler = () => {
    if (document.hidden) return
    onPollTick(state)
  }
  document.addEventListener('visibilitychange', state.visibilityHandler)
}

/** Deliberately does nothing unconditional: rescanNotebooks() only rebuilds
 * options (and only then repaints/writes) when the discovered notebook set
 * actually changed, and reloadEntries()'s `known_mtime` short-circuit means
 * an unchanged file never re-renders or dirties the canvas either — no bare
 * per-tick `setDirtyCanvas` call lives in this function. */
function onPollTick(state) {
  // Finding 4: a previous tick's (or the visibilitychange flush's) reload
  // hasn't landed yet — mid-run the server can easily take longer than
  // POLL_MS to answer, so skip rather than stack a second concurrent
  // request; the next tick (or a later flush) tries again.
  if (state.pollReloadInFlight) return
  try {
    rescanNotebooks(state)
  } catch (error) {
    api.warn('prompt builder canvas rescan failed', error)
  }
  state.pollReloadInFlight = reloadEntries(state)
    .catch((error) => api.warn('prompt builder poll reload failed', error))
    .finally(() => {
      state.pollReloadInFlight = null
    })
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
    // Unsaved-edit drafts (owner report 2026-09-02): riding the SAME
    // discovery walk that already builds the selector's option list --
    // `null` on a Notebook whose own backend predates the widget, which
    // mirroredDraftsRaw() below treats as "nothing to mirror" rather than
    // a crash.
    const dw = findWidget(node, DRAFTS_WIDGET_NAME)
    out.push({
      title: node.title || node.type || 'Notebook',
      file: typeof fw?.value === 'string' ? fw.value : '',
      draftsRaw: typeof dw?.value === 'string' ? dw.value : null
    })
  }
  return out
}

/**
 * The raw `drafts` widget value belonging to whichever discovered
 * candidate mirrors *file* -- first match wins, the SAME tie-break
 * notebookOptionsOf()'s own file-based dedupe already uses when two
 * Notebook nodes happen to point at the same file (a known, pre-existing
 * ambiguity of the file-based mirroring scheme this panel already has;
 * unsaved-edit drafts inherit it rather than solving it). An empty *file*,
 * no matching candidate, or a matching candidate with no drafts widget of
 * its own (`draftsRaw` is `null`) all fall back to `DEFAULT_DRAFTS_VALUE`
 * ("no unsaved edits"). Pure -- no DOM/graph access -- so this is driven
 * directly by tests/test_prompt_builder_js.py.
 * @param {{file?: string, draftsRaw?: string|null}[]} candidates
 * @param {string} file
 * @returns {string}
 */
export function mirroredDraftsRaw(candidates, file) {
  if (!file) return DEFAULT_DRAFTS_VALUE
  for (const candidate of Array.isArray(candidates) ? candidates : []) {
    if (candidate && candidate.file === file) {
      return typeof candidate.draftsRaw === 'string' ? candidate.draftsRaw : DEFAULT_DRAFTS_VALUE
    }
  }
  return DEFAULT_DRAFTS_VALUE
}

/**
 * Copies the mirrored Notebook's raw `drafts` value onto our OWN hidden
 * `drafts` widget (owner report 2026-09-02 -- see the file header/
 * DRAFTS_WIDGET_NAME's own doc for the bug this fixes: a block resolved
 * from an entry with an unsaved edit used to always run the SAVED text,
 * because this panel re-resolves each block straight from the FILE and
 * never saw the Notebook's `drafts` widget at all). The backend
 * (`_resolve_blocks`) applies it over the file text per named block,
 * exactly like `resolve_selection` already does for the Notebook's own
 * output.
 *
 * No-op on a backend that predates the widget (`state.draftsWidget` is
 * null) or when the mirrored value already matches what we're holding --
 * same write-if-different + callback + dirty-canvas idiom as
 * writeFileWidget()/writeBlocksWidget() above, so a call that finds
 * nothing changed never dirties the canvas.
 *
 * Deliberately NOT driven by its own timer (owner instruction: reuse the
 * panel's existing cadence). Called from rescanNotebooks() -- attach,
 * configure, selector focus, and every ~5s poll tick (POLL_MS) already run
 * that function -- but UNCONDITIONALLY, before rescanNotebooks()'s own
 * change-gate on the built selector OPTIONS signature: a draft can change
 * on the mirrored Notebook while the SET of notebooks on canvas (and thus
 * that signature) stays exactly the same, so this must not be skipped by
 * that early return. Also called from writeFileWidget() so switching which
 * Notebook this panel mirrors adopts its drafts immediately rather than
 * waiting for the next tick.
 *
 * A NUL-prefixed key in the mirrored JSON object (notebook.js's
 * `categoryDraftKey()` keyspace for an unsaved CATEGORY-description edit)
 * is copied along untouched -- this function only ever moves the raw
 * STRING value, never parses it. That is safe: `_resolve_blocks` (the
 * backend consumer) only ever looks up a block by its plain entry NAME,
 * which can never start with a NUL byte, so a category draft riding along
 * in the same object is inert cargo, never mistaken for a block's text.
 */
function syncMirroredDrafts(state, candidates) {
  const widget = state.draftsWidget
  if (!widget) return
  const raw = mirroredDraftsRaw(candidates, state.fileWidget.value ?? '')
  if (widget.value === raw) return
  widget.value = raw
  try {
    widget.callback?.(raw)
  } catch (error) {
    api.warn('drafts widget callback threw', error)
  }
  state.node.graph?.setDirtyCanvas(true, true)
}

/**
 * Re-scan the canvas for Notebook nodes; change-gated on the built option
 * list's signature, so a tick/focus that finds nothing new touches neither
 * the DOM nor the widgets. `force` (attach, configure) always rebuilds.
 */
function rescanNotebooks(state, { force = false } = {}) {
  const candidates = discoverNotebookCandidates()
  // Unsaved-edit drafts: deliberately BEFORE the options-signature early
  // return below -- a draft can change while the discovered notebook SET
  // stays identical, so this must run on every rescan, not only the ones
  // that go on to rebuild the selector (syncMirroredDrafts's own doc).
  syncMirroredDrafts(state, candidates)
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
  // Adopt the newly-mirrored Notebook's drafts immediately rather than
  // waiting for the next poll tick -- cheap (in-memory graph walk only).
  syncMirroredDrafts(state, discoverNotebookCandidates())
  reloadEntries(state).catch((error) => api.warn('reload after file change failed', error))
}

// ---------------------------------------------------------------------------
// Collapsed groups — a node PROPERTY, not a widget (§7.9: pure view state
// carries no §8 positional `widgets_values` hazard). Reuses notebook.js's
// own pure parse/toggle/query helpers verbatim, under the SAME property
// NAME (`Collapsed sections`), so this mirrors its `PROP_COLLAPSED_SECTIONS`
// exactly rather than inventing a second, parallel convention.
// ---------------------------------------------------------------------------

/** Same string notebook.js's own (un-exported) `PROP_COLLAPSED_SECTIONS`
 * uses — duplicated rather than imported, same posture as
 * `NOTEBOOK_CLASS_ID` above (a small, stable, cross-file constant is
 * cheaper to keep byte-identical by inspection than to import a private
 * one — each node owns its own `properties` object regardless). */
const PROP_COLLAPSED_SECTIONS = 'Collapsed sections'

/**
 * Registers the property and wires it live — called once from
 * attachPromptBuilderPanel(), right after buildUi(). notebook.js's own
 * registerCollapsedSectionsProperty(), verbatim: `addProperty()` never
 * fires `onPropertyChanged`, so a FRESH node needs the explicit
 * applyCollapsedSectionsFromProperty() call at the end; a RESTORED node's
 * `configure()` (which runs right after this function returns) overwrites
 * the property with the FILE's saved array and fires the wrapped
 * `onPropertyChanged` for it, which re-applies and repaints — so the saved
 * value always wins last, before any entries-populated render exists to
 * show a flash.
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
      // Covers BOTH configure()'s restore and a live hand-edit through the
      // node's right-click Properties panel.
      applyCollapsedSectionsFromProperty(state)
      renderLeftPane(state)
    }
    return result
  }
  // addProperty() alone never fires onPropertyChanged (see above) — sync a
  // FRESH node's list explicitly now; a RESTORED node's configure() does
  // this again momentarily with the real saved value via the wrapper above.
  applyCollapsedSectionsFromProperty(state)
}

/** READ half — replaces `state.collapsedSections` with whatever the node
 * property currently says. Never writes the property or dirties the
 * canvas — see syncCollapsedSectionsProperty() for the write half. */
function applyCollapsedSectionsFromProperty(state) {
  state.collapsedSections = parseCollapsedSections(state.node.properties?.[PROP_COLLAPSED_SECTIONS])
}

/** WRITE half — folds the CURRENT `state.collapsedSections` back into the
 * node property and dirties the canvas so the workflow's next save
 * captures it — called from toggleGroupCollapse() below. */
function syncCollapsedSectionsProperty(state) {
  const node = state.node
  node.properties = node.properties || {}
  node.properties[PROP_COLLAPSED_SECTIONS] = state.collapsedSections
  node.graph?.setDirtyCanvas(true, true)
}

/** A tap on a category header only ever toggles collapse — no category-mode
 * selection, no rename, no delete: every edit still stays in the Notebook
 * (owner spec, "read-only here"), unlike notebook.js's own dual-purpose
 * header tap. */
function toggleGroupCollapse(state, category) {
  state.collapsedSections = toggleCollapsedSection(state.collapsedSections, category)
  syncCollapsedSectionsProperty(state)
  renderLeftPane(state)
}

/**
 * A collapsible category header for the left column: arrow + name + a
 * per-group entry count (owner spec), shown whether collapsed or not.
 * Mirrors notebook.js's own header affordance, reduced to this panel's
 * read-only posture — see toggleGroupCollapse()'s own doc.
 */
function buildGroupHeaderRow(state, category, count) {
  const collapsed = isSectionCollapsed(state.collapsedSections, category)
  const row = el('div', {
    className: 'eps-pb-group-header',
    text: `${collapsed ? '▸' : '▾'} ${category} (${count})`,
    attrs: { tabindex: '0', title: category }
  })
  row.addEventListener('click', (event) => {
    event.stopPropagation()
    toggleGroupCollapse(state, category)
  })
  return row
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

  const knownMtime =
    (showing || paintedFromCache) && typeof state.paintedMtime === 'number' ? state.paintedMtime : null

  let data
  try {
    // Finding 5: shared across every Builder node mirroring `file`, plus
    // finding 4's own per-node in-flight guard around the poll path (see
    // onPollTick) — see sharedReloadFetch's own header for why joining a
    // fetch sent with a slightly different `known_mtime` is still safe.
    data = await sharedReloadFetch(file, knownMtime)
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
 * edit stays in the Notebook (owner spec, "read-only here"). Groups by
 * category (groupEntriesByCategory(), owner ask 2026-09-01) and marks a row
 * already present in `blocks` as added — dimmed, badged, and with no
 * dblclick listener at all, so the gesture that normally adds a name simply
 * has nothing to call for a row already added (appendBlock() itself is the
 * data-layer backstop for every OTHER path in — see its own doc). */
function renderLeftPane(state) {
  state.leftListEl.replaceChildren()

  if (!state.notebookOptions.length) {
    state.leftListEl.append(el('div', { className: 'eps-pb-empty', text: EMPTY_NO_NOTEBOOK_HINT }))
    return
  }

  const filtering = (state.searchQuery || '').trim().length > 0
  const filtered = filterEntries(state.entries, state.searchQuery)
  if (!filtered.length) {
    const text = state.entries.length
      ? 'No prompts match your search.'
      : 'This notebook has no prompts yet.'
    state.leftListEl.append(el('div', { className: 'eps-pb-empty', text }))
    return
  }

  const blockSet = new Set(state.blocks)
  for (const group of groupEntriesByCategory(filtered)) {
    // The leading, un-headed "" region (FORMAT.md §3.1) never gets a header
    // row of its own — notebook.js's renderList() convention, matched here
    // so this column reads exactly like the notebook it mirrors. A group
    // that produced no entries never reaches this loop at all (it simply
    // isn't in groupEntriesByCategory()'s output for the FILTERED list), so
    // a search that empties a category hides its header too.
    if (group.category) {
      state.leftListEl.append(buildGroupHeaderRow(state, group.category, group.entries.length))
      // §7.2 search (notebook.js's own renderList() rule, matched here): a
      // collapsed category still shows its matching entries WHILE
      // filtering — a match hidden inside a collapsed group would read as
      // "search is broken". Collapse only actually hides entries once the
      // search box is empty again.
      if (!filtering && isSectionCollapsed(state.collapsedSections, group.category)) continue
    }
    for (const entry of group.entries) {
      const name = typeof entry?.name === 'string' ? entry.name : ''
      const added = blockSet.has(name)
      const row = el('div', {
        className: 'eps-pb-row-left' + (added ? ' eps-pb-row-left-added' : ''),
        text: name,
        attrs: {
          tabindex: '0',
          title: added
            ? `"${name}" is already in the Blocks list on the right.`
            : firstChars(entry?.text, TOOLTIP_CHARS)
        }
      })
      if (added) {
        row.append(el('span', { className: 'eps-pb-added-badge', text: 'added' }))
      } else {
        row.addEventListener('dblclick', (event) => {
          event.stopPropagation()
          onEntryDoubleClick(state, name)
        })
      }
      state.leftListEl.append(row)
    }
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
  // The left column's added-badge/dblclick-gate (renderLeftPane's own doc)
  // depends on `state.blocks` too — a block removed here must re-enable its
  // left row immediately, not on the next unrelated repaint.
  renderLeftPane(state)
}

function onRemoveBlockClick(state, idx) {
  writeBlocksWidget(state, removeBlockAt(state.blocks, idx))
}

function renderRightPane(state) {
  if (state.dragFromIndex != null) {
    // Finding 6 (2026-08-26 while-running round): a poll-triggered reload
    // landing mid-drag would otherwise replaceChildren() the very row the
    // user has a native HTML5 drag on, killing the gesture. Defer — the
    // left pane still updates freely (reloadEntries() -> applyEntriesPayload
    // calls renderLeftPane() unconditionally, only this call is gated) —
    // and wireRowDrag's `dragend` handler flushes exactly one render below
    // once the drag ends, so nothing painted during the drag is lost, only
    // delayed. The DROP path itself is unaffected: the container's own
    // `drop` handler clears `dragFromIndex` BEFORE calling commitReorder()
    // (-> writeBlocksWidget -> this function), so that render always runs
    // immediately, never deferred.
    state.rightPaneRenderPending = true
    return
  }
  state.rightPaneRenderPending = false
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
    // Finding 6: flush a render renderRightPane() deferred while this drag
    // was active (a poll landed mid-drag, or a drop elsewhere never fired —
    // dragend always fires regardless of whether drop did). A successful
    // DROP already rendered synchronously before dragend runs (see
    // commitReorder's call chain), so this is normally a no-op then.
    if (state.rightPaneRenderPending) renderRightPane(state)
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
