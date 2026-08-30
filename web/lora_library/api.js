/**
 * @file Fetch helpers + logging for the lora_library frontend (FORMAT.md §5).
 * Every module goes through these so error shape and the `[lora_library]`
 * log prefix stay uniform.
 */

import { app } from '../../../scripts/app.js'
import { api } from '../../../scripts/api.js'

export { FRONTEND_VERSION } from './version.js'

const PREFIX = '[lora_library]'

export function warn(message, error) {
  if (error !== undefined) console.warn(PREFIX, message, error)
  else console.warn(PREFIX, message)
}

export function log(message) {
  console.log(PREFIX, message)
}

/**
 * Absolute URL for a lora_library route -- for element attributes (an
 * `<img src>`) that bypass fetchApi. `api.apiURL` carries ComfyUI's api
 * base/path prefix; a hardcoded root-absolute path breaks behind a
 * reverse-proxy prefix while every fetchApi call keeps working (review
 * 2026-08-09; image_grid.js's own img-src precedent).
 * @param {string} path - e.g. `/lora_library/picker/preview?file=x`
 */
export function apiUrl(path) {
  return typeof api.apiURL === 'function' ? api.apiURL(path) : path
}

/**
 * GET a lora_library route (FORMAT.md §5). Resolves to parsed JSON.
 * Rejects with an Error whose message is the server's `error` field when
 * the response is non-2xx.
 * @param {string} path - e.g. `/lora_library/sets`
 * @param {Record<string, string>} [params]
 * @param {{timeoutMs?: number}} [options] - opt-in request timeout (finding
 * 6, 2026-08-26 responsiveness round) — see fetchWithTimeout() below.
 */
export async function getJson(path, params, options) {
  const query = params ? `?${new URLSearchParams(params)}` : ''
  const response = await fetchWithTimeout(`${path}${query}`, undefined, options)
  return unwrap(response)
}

/**
 * POST JSON to a lora_library route (FORMAT.md §5).
 * @param {string} path
 * @param {object} body
 * @param {{timeoutMs?: number}} [options] - opt-in request timeout (finding
 * 6, 2026-08-26 responsiveness round) — see fetchWithTimeout() below.
 */
export async function postJson(path, body, options) {
  const response = await fetchWithTimeout(
    path,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body ?? {})
    },
    options
  )
  return unwrap(response)
}

/**
 * `api.fetchApi()` with an OPT-IN timeout (finding 6, 2026-08-26
 * responsiveness round, owner-adjacent report: a save/move/delete can wedge
 * the Notebook's `state.busy` forever behind a hung fetch when the
 * ComfyUI server is GIL-busy running a workflow, or the library sits on a
 * slow/dropped NAS mount). `options.timeoutMs`, when a number, aborts the
 * in-flight request after that many ms via `AbortController` and rejects
 * with an `Error` carrying `.timeout = true`, so a caller can tell a
 * timeout apart from a genuine server error (`.status`) or a network
 * failure (neither set) — see notebook.js's `recoverFromWriteTimeout()`.
 *
 * DEFAULT BEHAVIOR IS UNCHANGED (controller.js also imports `getJson`/
 * `postJson`, and this file is strictly additive/opt-in for that reason —
 * see the file header): every existing call site passes no third argument,
 * `options` is `undefined`, `timeoutMs` is not a number, and this function
 * calls `api.fetchApi(path, fetchOptions)` -- IDENTICAL to what `getJson`/
 * `postJson` called directly before this round, byte-for-byte, no
 * `AbortController` ever constructed, no options object ever added or
 * changed.
 * @param {string} path
 * @param {RequestInit} [fetchOptions]
 * @param {{timeoutMs?: number}} [options]
 */
async function fetchWithTimeout(path, fetchOptions, options) {
  const timeoutMs = options?.timeoutMs
  if (typeof timeoutMs !== 'number') return api.fetchApi(path, fetchOptions)
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    return await api.fetchApi(path, { ...fetchOptions, signal: controller.signal })
  } catch (error) {
    if (controller.signal.aborted) {
      const timeoutError = new Error(`request to ${path} timed out after ${timeoutMs}ms`)
      timeoutError.timeout = true
      throw timeoutError
    }
    throw error
  } finally {
    clearTimeout(timer)
  }
}

async function unwrap(response) {
  let data = null
  let parsed = true
  try {
    data = await response.json()
  } catch {
    // Non-JSON body (proxy error page etc.) — fall through to status check.
    parsed = false
  }
  if (!response.ok) {
    const message = data && data.error ? data.error : `HTTP ${response.status}`
    const error = new Error(message)
    error.status = response.status
    error.data = data
    throw error
  }
  // A 200 whose body isn't JSON is a FAILURE, not an empty payload (review
  // 2026-08-09): returning null here made the picker read a truncated/proxy-
  // mangled response as a successfully-empty lora library — the "failed" and
  // "empty install" states must never collapse into each other.
  if (!parsed) {
    const error = new Error('server returned a response that is not JSON')
    error.status = response.status
    throw error
  }
  return data
}

// ---------------------------------------------------------------------------
// Nested-graph traversal (v0.64.0, owner ask 2026-08-14: "make sure it works
// even when the nodes are nested") -- shared by controller.js, sets.js and
// picker.js so every "find loaders in the workflow" walk agrees on what a
// workflow IS. Rig ground truth 2026-08-14: a SubgraphNode carries
// `.subgraph` (an LGraph subclass with its own `_nodes` and its OWN id
// space), execution flattens ids by joining the containing SubgraphNode
// ids with ':' ("3:2" -- graphToPrompt output keys, probed live), and
// events inside a subgraph fire only that subgraph's own hooks.
// ---------------------------------------------------------------------------

//: Recursion ceiling for nested subgraphs. The frontend itself throws
//: RecursionError on genuinely cyclic definitions; this cap just keeps a
//: pathological workflow from stalling a UI walk.
const MAX_SUBGRAPH_DEPTH = 16

/**
 * Every live node under *rootGraph*, subgraphs included, as
 * `{node, graph, pathId}` -- `pathId` is the execution-id shape
 * ("3:2" for node 2 inside SubgraphNode 3; plain "2" at the root), so a
 * label built from it matches what the API prompt calls the node.
 * @param {object} rootGraph @returns {Array<{node: object, graph: object, pathId: string}>}
 */
export function walkLiveNodes(rootGraph) {
  const out = []
  const visit = (graph, prefix, depth) => {
    if (!graph || depth > MAX_SUBGRAPH_DEPTH) return
    for (const node of graph._nodes || graph.nodes || []) {
      if (!node || node.id == null) continue
      const pathId = prefix ? `${prefix}:${node.id}` : String(node.id)
      out.push({ node, graph, pathId })
      if (node.subgraph) visit(node.subgraph, pathId, depth + 1)
    }
  }
  visit(rootGraph, '', 0)
  return out
}

/**
 * Every graph under *rootGraph* (itself included) -- the install targets
 * for per-graph event watches, since a subgraph's `onNodeAdded` fires on
 * the SUBGRAPH, never the root.
 * @param {object} rootGraph @returns {Array<object>}
 */
export function walkGraphs(rootGraph) {
  const out = []
  const visit = (graph, depth) => {
    if (!graph || depth > MAX_SUBGRAPH_DEPTH || out.includes(graph)) return
    out.push(graph)
    for (const node of graph._nodes || graph.nodes || []) {
      if (node?.subgraph) visit(node.subgraph, depth + 1)
    }
  }
  visit(rootGraph, 0)
  return out
}

/**
 * The live node a `pathId` from `walkLiveNodes` names right now, or null.
 * Resolves segment by segment so a stale tail (deleted node, unpacked
 * subgraph) degrades to null rather than a wrong node.
 * @param {object} rootGraph @param {string} pathId @returns {object|null}
 */
export function findByPathId(rootGraph, pathId) {
  const segments = String(pathId || '').split(':')
  let graph = rootGraph
  let node = null
  for (const segment of segments) {
    if (!graph) return null
    const nodes = graph._nodes || graph.nodes || []
    node = nodes.find((n) => n && String(n.id) === segment) || null
    if (!node) return null
    graph = node.subgraph || null
  }
  return node
}

// ---------------------------------------------------------------------------
// Cross-panel "a widget was written EXTERNALLY" notification (Universal
// State Controller Apply fix, 2026-08-29 round -- owner report: "applying
// any of the sets won't change anything"). Mirrors controller.js's
// `announceSetsChanged()`/`lora_library:sets-changed` idiom (a bare
// `window.dispatchEvent(new CustomEvent(...))`, subscribed with
// `window.addEventListener` -- see sets.js's `initSetsFreshness()`), but
// this one carries a PAYLOAD (which nodes/widgets were written) so a
// subscriber can re-sync ONLY the node(s) an announcement actually names
// instead of doing a blind full reload on every unrelated write.
//
// WHY this exists: a plain litegraph WIDGET redraws every canvas frame
// straight from `widget.value`, so `widget.value = x; widget.callback?.()`
// (Universal State Controller's `_writeApplyPlan()`) is enough on its own
// for a node like EPS Resolution's plain int fields or a Switcher's combo.
// A DOM-PANEL node (the Notebook, the LoRA Picker, the Prompt Builder, the
// Checkpoint Switcher, Resolution's OWN presets `<select>`) holds its own
// rendered state in real DOM elements that only ever repaint from that
// panel's own gestures or reload cycles -- a programmatic widget write
// changes the node's data but never touches that DOM, which is exactly
// what left the Notebook showing "Film Grain" after an Apply had already
// written "Detailer" onto the live `entry` widget (data verified, toast
// said "Applied 3 of 3"). Put here, in api.js, rather than in
// universal_controller.js: every one of the affected panel modules already
// imports this file, so this is the one module every side of the fix can
// share without any of them importing one another.
// ---------------------------------------------------------------------------

export const WIDGETS_CHANGED_EXTERNALLY_EVENT = 'lora_library:widgets-changed-externally'

let pendingExternalWidgetChanges = null
let externalWidgetChangeFlushQueued = false

/**
 * Tell every subscriber that *entries* worth of nodes just had one or more
 * widgets written PROGRAMMATICALLY -- i.e. NOT through that node's own
 * panel gesture. `entries`: `[{node, pathId, class, widgets}]` -- `node` is
 * the LIVE node reference (so a subscriber never needs its own lookup by
 * id), `pathId`/`class` are carried for logging, `widgets` is the array of
 * widget NAMES that changed on that node. A falsy/empty *entries* is a
 * silent no-op (an Apply that wrote nothing has nothing to announce).
 *
 * Calls COALESCE to at most one dispatched event per tick -- this pack's
 * `setTimeout(fn, 0)` one-tick-coalescer idiom (`sets.js`'s
 * `scheduleMirrorsHeal()`, `path_heal.js`'s load coalescer,
 * `universal_controller.js`'s own `scheduleUniversalKick()`): several
 * calls landing in the same synchronous pass (or the same macrotask queue
 * turn) merge their entries into ONE flushed event instead of firing once
 * per call, so a subscriber's re-sync work is naturally batched too. The
 * flush additionally SKIPS while a whole-graph rebuild is in progress
 * (`app.configuringGraph` -- a private counter core increments for the
 * exact duration of `LGraph.prototype.configure()`; see
 * `eps_image/image_grid.js`'s `isGraphConfiguring()` for the full citation
 * and the live-verification story, duplicated here rather than imported
 * per this pack's no-cross-import-for-one-line-helpers convention): a
 * load/undo/redo/tab-switch is synchronous and always finishes -- flag
 * back to `false` -- before this `setTimeout(fn, 0)` macrotask gets a
 * turn, so this check reliably catches a call that landed mid-rebuild.
 * Every panel is about to repaint itself from its OWN restore path in that
 * case (`onConfigure`/`configure()`), so a stale external announce landing
 * in that same window would be redundant at best, and unsafe at worst for
 * a panel not yet fully constructed.
 *
 * Never throws -- this is a nicety, never load-bearing for the write that
 * triggered it.
 * @param {Array<{node: object, pathId: string, class: string, widgets: string[]}>} entries
 */
export function announceWidgetsChangedExternally(entries) {
  try {
    if (!Array.isArray(entries) || !entries.length) return
    pendingExternalWidgetChanges = [...(pendingExternalWidgetChanges || []), ...entries]
    if (externalWidgetChangeFlushQueued) return
    externalWidgetChangeFlushQueued = true
    setTimeout(() => {
      externalWidgetChangeFlushQueued = false
      const flushed = pendingExternalWidgetChanges || []
      pendingExternalWidgetChanges = null
      if (!flushed.length) return
      if (app.configuringGraph) return // graph load/undo/tab-switch storm -- panels reload themselves
      try {
        window.dispatchEvent(
          new CustomEvent(WIDGETS_CHANGED_EXTERNALLY_EVENT, { detail: { entries: flushed } })
        )
      } catch {
        // Announcement is a nicety; the write that triggered it must not depend on it.
      }
    }, 0)
  } catch {
    // Announcement is a nicety; the write that triggered it must not depend on it.
  }
}

/**
 * Subscribe to `announceWidgetsChangedExternally()`. *handler* is called
 * with the flushed `entries` array for every announcement. Returns an
 * unsubscribe function (mirrors the pack's `window.addEventListener`/
 * `removeEventListener` pairing convention, e.g.
 * `universal_controller.js`'s own `_subscribeStatesChanged`/
 * `_unsubscribeStatesChanged`), though every current caller subscribes
 * once for the page's lifetime and never calls it, the same as
 * `sets.js`'s `initSetsFreshness()` listener.
 *
 * *handler* is wrapped in its own try/catch here -- NOT the caller's job --
 * so one panel module's subscriber throwing can never suppress delivery to
 * every OTHER panel subscribed to the same event (requirement: "guarded
 * per panel so one panel's failure can't break the others").
 * @param {(entries: Array<{node: object, pathId: string, class: string, widgets: string[]}>) => void} handler
 * @returns {() => void} unsubscribe
 */
export function subscribeWidgetsChangedExternally(handler) {
  const listener = (event) => {
    try {
      handler(event?.detail?.entries || [])
    } catch (error) {
      warn('a widgets-changed-externally subscriber threw', error)
    }
  }
  window.addEventListener(WIDGETS_CHANGED_EXTERNALLY_EVENT, listener, { capture: true })
  return () => window.removeEventListener(WIDGETS_CHANGED_EXTERNALLY_EVENT, listener, { capture: true })
}
