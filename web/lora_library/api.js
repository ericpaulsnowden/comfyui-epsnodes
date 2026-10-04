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
// v1.2.0 NESTED REACH (owner ask 2026-10-03: "make sure all of the nodes that
// can control other nodes also looks into nested nodes"; FORMAT.md §7.10).
//
// The three walkers above answer "which nodes are in the workflow"; the
// helpers below answer the two questions every controller/reader still had
// to ask one graph at a time: "what does this WIRE really connect to" and
// "is my hook still installed on every graph". Everything is verified
// against the ComfyUI frontend source (lib/litegraph/src/subgraph/*,
// LGraph.ts, utils/executionUtil.ts), not guessed:
//
//  - A SubgraphNode `S` in graph G has `S.subgraph` (an LGraph). `S.inputs[i]`
//    and `S.outputs[j]` are INDEX-ALIGNED with `S.subgraph.inputs[i]` /
//    `.outputs[j]` -- ExecutableNodeDTO.resolveInput reads
//    `subgraphNode.inputs.at(link.origin_slot)` and
//    `resolveSubgraphOutputLink(slot)` reads `outputNode.slots[slot]`.
//  - INSIDE the subgraph, the boundary is two pseudo-nodes that are NOT in
//    `_nodes` / `getNodeById`: a link whose `origin_id` is -10
//    (SUBGRAPH_INPUT_ID) leaves the subgraph's input node (`origin_slot` =
//    the subgraph input's index), and a link whose `target_id` is -20
//    (SUBGRAPH_OUTPUT_ID) enters its output node (`target_slot` = the
//    subgraph output's index). The wire the user sees OUTSIDE is a second,
//    separate link in the PARENT graph's own link table.
//  - The API prompt is built from a flattened resolution of exactly those
//    links (graphToPrompt -> ExecutableNodeDTO), keyed by execution path id
//    ("3:2"); an input with no resolvable source is simply absent.
//  - One subgraph DEFINITION can be instantiated by several SubgraphNodes
//    (they share `.subgraph`, so the inner node OBJECTS are shared too and
//    appear under several path ids -- walkLiveNodes reports each path).
//    Walking DOWN through a specific SubgraphNode is unambiguous; walking UP
//    out of a shared definition fans out to every instance, so the resolvers
//    below return ARRAYS, never a single guess.
//  - Node ids are strings in newer frontends and numbers in older ones, so
//    every id comparison below goes through String().
// ---------------------------------------------------------------------------

/** The pseudo-node id a link carries when it leaves a subgraph's INPUT node
 * (LLink.origin_id; litegraph/constants.ts SUBGRAPH_INPUT_ID). */
export const SUBGRAPH_INPUT_ID = -10

/** The pseudo-node id a link carries when it enters a subgraph's OUTPUT node
 * (LLink.target_id; litegraph/constants.ts SUBGRAPH_OUTPUT_ID). */
export const SUBGRAPH_OUTPUT_ID = -20

const isIoId = (id, expected) => String(id) === String(expected)

/** The live node list of *graph*, whichever of the two names it carries. */
function nodesOf(graph) {
  return graph?._nodes || graph?.nodes || []
}

/** True for a SubgraphNode instance (it carries its definition on `.subgraph`,
 * the same test the three walkers use). */
export function isSubgraphNode(node) {
  return !!(node && node.subgraph && typeof node.subgraph === 'object')
}

/** The node *id* names inside *graph* (a boundary pseudo-node id is NOT a
 * node, so it is null here), or null. */
function nodeIn(graph, id) {
  if (id == null || isIoId(id, SUBGRAPH_INPUT_ID) || isIoId(id, SUBGRAPH_OUTPUT_ID)) return null
  const direct = graph?.getNodeById?.(id)
  if (direct) return direct
  return nodesOf(graph).find((n) => n && String(n.id) === String(id)) || null
}

/**
 * An LLink by id out of *graph*'s own link table, tolerant of every shape
 * this frontend's forks have used for it (a plain object/array indexed by id,
 * a Map, or 1.5x's Proxy that is both). Null when absent.
 * @param {object} graph @param {number|string|null|undefined} linkId
 */
export function graphLink(graph, linkId) {
  if (linkId == null) return null
  const links = graph?.links ?? graph?._links
  if (!links) return null
  const indexed = links[linkId]
  if (indexed && typeof indexed === 'object') return indexed
  if (typeof links.get === 'function') {
    return links.get(linkId) ?? links.get(Number(linkId)) ?? links.get(String(linkId)) ?? null
  }
  return null
}

/** Every LLink in *graph*'s link table as an array (Map, object or array). */
function graphLinks(graph) {
  const links = graph?.links ?? graph?._links
  if (!links) return []
  const all = typeof links.values === 'function' ? [...links.values()] : Object.values(links)
  return all.filter((l) => l && typeof l === 'object')
}

/** `prefix:id`, or the bare id at the root (the walkLiveNodes path shape).
 * Exported (v1.2.0) so callers building path ids never hand-roll the join. */
export function joinPath(prefix, id) {
  return prefix ? `${prefix}:${id}` : String(id)
}

/** *pathId* minus its last segment ('' for a root node) -- the path of the
 * SubgraphNode that owns the graph the node sits in, which is what the
 * boundary resolvers take as `prefix`. Exported (v1.2.0). */
export function parentPrefixOf(pathId) {
  const at = String(pathId).lastIndexOf(':')
  return at === -1 ? '' : String(pathId).slice(0, at)
}

/**
 * Where the node *pathId* names lives: `{node, graph, prefix}` -- `graph` is
 * the (sub)graph that CONTAINS it and `prefix` the path id of the SubgraphNode
 * that owns that graph ('' at the root). Resolves segment by segment like
 * `findByPathId`, so a stale tail degrades to null.
 * @param {object} rootGraph @param {string} pathId
 * @returns {{node: object, graph: object, prefix: string}|null}
 */
export function locateByPathId(rootGraph, pathId) {
  const segments = String(pathId ?? '').split(':')
  if (!segments[0]) return null
  let graph = rootGraph
  let prefix = ''
  let node = null
  for (let i = 0; i < segments.length; i++) {
    if (!graph) return null
    node = nodeIn(graph, segments[i])
    if (!node) return null
    if (i < segments.length - 1) {
      prefix = joinPath(prefix, segments[i])
      graph = node.subgraph || null
    }
  }
  return { node, graph, prefix }
}

/**
 * Every place the live node object *node* sits in the workflow, as
 * `{graph, prefix, pathId}` -- ONE entry for a root node (cheap, no walk) and
 * one PER INSTANCE for a node inside a subgraph definition that several
 * SubgraphNodes instantiate. This is what turns "my node's own graph" into
 * the execution path(s) the API prompt uses, so a boundary walk knows which
 * SubgraphNode(s) to step out through.
 * @param {object} rootGraph @param {object} node
 * @returns {Array<{graph: object, prefix: string, pathId: string}>}
 */
export function locationsOfNode(rootGraph, node) {
  if (!rootGraph || !node || node.id == null) return []
  if (node.graph === rootGraph || nodesOf(rootGraph).includes(node)) {
    return [{ graph: rootGraph, prefix: '', pathId: String(node.id) }]
  }
  const out = []
  for (const entry of walkLiveNodes(rootGraph)) {
    if (entry.node !== node) continue
    out.push({ graph: entry.graph, prefix: parentPrefixOf(entry.pathId), pathId: entry.pathId })
  }
  return out
}

/** The ROOT graph *graph* belongs to: a Subgraph exposes it as `rootGraph`
 * (LGraph.ts: `Subgraph.rootGraph`), a root graph is its own root. Lets a
 * node inside a subgraph reach the whole workflow from `node.graph` alone
 * (callers still prefer `app.graph`, which IS the root in every release). */
export function rootGraphOf(graph) {
  return graph?.rootGraph || graph || null
}

/**
 * The live workflow ROOT graph *node* sits under, or null when the nested
 * resolvers cannot be trusted to know about it -- no live `app.graph`, a node
 * that is not part of it (a unit-test fake, a node mid-removal, a tab that is
 * not the active workflow). Callers use null to fall back to litegraph's own
 * single-graph reads, exactly their pre-1.0.0 behaviour. O(1): a real node
 * (flat or inside a subgraph: `Subgraph.rootGraph` is the root) is a pointer
 * compare, never a graph walk. v1.2.0, shared by resolution.js and
 * frame_saver.js (each had its own twin).
 * @param {object} node @returns {object|null}
 */
export function liveRootOf(node) {
  const root = app.graph
  if (!root || !node?.graph) return null
  return rootGraphOf(node.graph) === root ? root : null
}

/**
 * Path-id ordering ("3:2" style, segment-numeric) -- the ascending order a
 * "lowest id wins" rule uses once ids can be nested: root ids sort before
 * subgraph paths with the same head, and each segment compares numerically
 * ("10" > "9", "3:2" > "3"). Moved here from controller.js (v1.2.0) so
 * every nested-aware tie-break shares ONE definition.
 * @param {string|number} a @param {string|number} b @returns {number}
 */
export function comparePathIds(a, b) {
  const as = String(a).split(':').map(Number)
  const bs = String(b).split(':').map(Number)
  const len = Math.max(as.length, bs.length)
  for (let i = 0; i < len; i++) {
    const d = (as[i] ?? -Infinity) - (bs[i] ?? -Infinity)
    if (d) return d
  }
  return 0
}

/** The path id(s) of a live node object -- `locationsOfNode`'s ids only. */
export function pathIdsOfNode(rootGraph, node) {
  return locationsOfNode(rootGraph, node).map((l) => l.pathId)
}

/**
 * A human label for a path id: `{title, trail, text}`. `title` is the node's
 * own title (or type), `trail` the containing SubgraphNodes' titles
 * outermost-first (a SubgraphNode's title, else its definition's name), and
 * `text` is `"Subgraph name › Node title"` ("Node title" alone at the root)
 * -- the shape every nested-aware dropdown/toast uses so two same-titled
 * nodes in different subgraphs read apart. Never throws; an unresolvable
 * path labels as itself.
 * @param {object} rootGraph @param {string} pathId
 * @returns {{title: string, trail: string[], text: string}}
 */
export function describePath(rootGraph, pathId) {
  const raw = String(pathId ?? '')
  const segments = raw.split(':')
  const trail = []
  let graph = rootGraph
  let title = raw
  for (let i = 0; i < segments.length; i++) {
    const node = graph ? nodeIn(graph, segments[i]) : null
    // A stale tail (deleted node, unpacked subgraph) labels as the raw id
    // rather than as a half-resolved trail naming the wrong node.
    if (!node) return { title: raw, trail: [], text: raw }
    title = node.title || node.type || segments[i]
    if (i < segments.length - 1) {
      trail.push(node.title || node.subgraph?.name || node.type || segments[i])
      graph = node.subgraph || null
    }
  }
  return { title, trail, text: [...trail, title].join(' › ') }
}

//: Boundary hops one resolution may take (a SubgraphNode output feeding the
//: next SubgraphNode's input, ...). Deeper than any real nesting; just keeps
//: a malformed (cyclic) definition from looping a UI walk.
const MAX_BOUNDARY_HOPS = 32

/** The inner link(s) of *subgraph* that feed its OUTPUT slot *slot* (a link
 * into the output pseudo-node). Scans the link table rather than the IO
 * slots' own `linkIds` -- the table is what serialisation and the prompt
 * flattening trust, and core repairs stale `linkIds` for that very reason
 * (Subgraph._repairIOSlotLinkIds). */
function innerLinksIntoOutput(subgraph, slot) {
  return graphLinks(subgraph).filter(
    (l) => isIoId(l.target_id, SUBGRAPH_OUTPUT_ID) && String(l.target_slot) === String(slot)
  )
}

/** The inner link(s) of *subgraph* that leave its INPUT slot *slot*. */
function innerLinksFromInput(subgraph, slot) {
  return graphLinks(subgraph).filter(
    (l) => isIoId(l.origin_id, SUBGRAPH_INPUT_ID) && String(l.origin_slot) === String(slot)
  )
}

/**
 * Follow *link* (a link in *graph*, whose owning SubgraphNode has path
 * *prefix*) UPSTREAM to the REAL source node(s), stepping through SubgraphNode
 * outputs (down into the definition) and subgraph input nodes (up and out
 * through the owning SubgraphNode, one result per instance). Returns
 * `[{node, graph, pathId, slot}]` -- `slot` is the output slot on the real
 * source. Empty for a dangling boundary (an unconnected subgraph input or
 * output, which is exactly what the prompt flattening drops too).
 * @param {object} rootGraph @param {object} graph @param {string} prefix
 * @param {object} link
 * @returns {Array<{node: object, graph: object, pathId: string, slot: number}>}
 */
export function resolveLinkSources(rootGraph, graph, prefix, link) {
  const out = []
  const visit = (g, pre, l, hops) => {
    if (!l || hops > MAX_BOUNDARY_HOPS) return
    if (isIoId(l.origin_id, SUBGRAPH_INPUT_ID)) {
      // Out through the owning SubgraphNode's input `origin_slot`.
      if (!pre) return
      const owner = locateByPathId(rootGraph, pre)
      if (!owner) return
      const outer = graphLink(owner.graph, owner.node.inputs?.[l.origin_slot]?.link)
      visit(owner.graph, owner.prefix, outer, hops + 1)
      return
    }
    const origin = nodeIn(g, l.origin_id)
    if (!origin) return
    if (isSubgraphNode(origin)) {
      // Down into the definition: whatever feeds its output `origin_slot`.
      for (const inner of innerLinksIntoOutput(origin.subgraph, l.origin_slot)) {
        visit(origin.subgraph, joinPath(pre, origin.id), inner, hops + 1)
      }
      return
    }
    out.push({ node: origin, graph: g, pathId: joinPath(pre, origin.id), slot: l.origin_slot ?? 0 })
  }
  visit(graph, prefix || '', link, 0)
  return out
}

/**
 * One hop of an UPSTREAM walk that may take several hops (a reroute chain, a
 * switcher's slots, ...): the real source(s) of input *slotIndex* of *item*,
 * where *item* is either the entry node (`{node}` -- location unknown, so
 * `resolveInputSources` locates it, one entry per instance, and crosses every
 * boundary) or a PREVIOUS hop's result (`{node, graph, pathId}` from this
 * file's resolvers), which is resolved FROM THAT EXACT PLACE -- the link in
 * `item.graph` followed with the owner prefix of `item.pathId`.
 *
 * The second form is the point (v1.2.0, found by resolution.js's and
 * frame_saver.js's tests): re-resolving a hop's node OBJECT with
 * `resolveInputSources` is wrong for every hop after the first. A node
 * inside a subgraph definition that several SubgraphNodes share is the SAME
 * object under each instance's path, so re-resolving it fans out to ALL
 * instances' outer sources and a walk that came in through instance A would
 * see instance B's upstream too (a false "mixed"). Staying on the lane keeps
 * each hop exact. Never throws; unconnected -> `[]`.
 * @param {object} rootGraph
 * @param {{node: object, graph?: object, pathId?: string}} item
 * @param {number} slotIndex
 * @returns {Array<{node: object, graph: object, pathId: string, slot: number}>}
 */
export function resolveSourcesAt(rootGraph, item, slotIndex) {
  if (!item?.node || slotIndex < 0) return []
  if (item.graph && item.pathId != null) {
    const link = graphLink(item.graph, item.node.inputs?.[slotIndex]?.link)
    return link ? resolveLinkSources(rootGraph, item.graph, parentPrefixOf(item.pathId), link) : []
  }
  return resolveInputSources(rootGraph, item.node, slotIndex)
}

/**
 * The REAL upstream source(s) of *node*'s input *slotIndex* -- the pack's
 * boundary-crossing replacement for `node.getInputNode(slot)` /
 * `graph.getNodeById(link.origin_id)`, which stop at a SubgraphNode or a
 * subgraph input node. A node inside a shared subgraph definition resolves
 * once per instance (see the section header), so the result is an array.
 * Unconnected -> `[]`.
 * @param {object} rootGraph @param {object} node @param {number} slotIndex
 * @returns {Array<{node: object, graph: object, pathId: string, slot: number}>}
 */
export function resolveInputSources(rootGraph, node, slotIndex) {
  const out = []
  const seen = new Set()
  for (const loc of locationsOfNode(rootGraph, node)) {
    const link = graphLink(loc.graph, node.inputs?.[slotIndex]?.link)
    for (const source of resolveLinkSources(rootGraph, loc.graph, loc.prefix, link)) {
      const key = `${source.pathId}#${source.slot}`
      if (seen.has(key)) continue
      seen.add(key)
      out.push(source)
    }
  }
  return out
}

/**
 * Follow *link* (in *graph*, owner path *prefix*) DOWNSTREAM to the REAL
 * consumer node(s): into a SubgraphNode input (to whatever inside reads that
 * subgraph input) and out through a subgraph output node (to whatever the
 * owning SubgraphNode's output feeds, per instance). Returns
 * `[{node, graph, pathId, slot, link, hops}]` -- `slot` is the consuming
 * INPUT slot index, `link` the final link, `hops` every `{graph, link}` the
 * walk crossed (first = the starting link), so a caller can act on the whole
 * wire (colour it, count it).
 *
 * `options.stopAtSubgraphInput(subgraphNode, slotIndex) -> boolean`: when it
 * returns true the walk does NOT descend into that SubgraphNode input and
 * reports the SubgraphNode itself as the consumer (`boundary: true`) -- how
 * EPS Bypass honours a promoted widget, whose own value takes over when the
 * outer wire is unplugged.
 * @param {object} rootGraph @param {object} graph @param {string} prefix
 * @param {object} link @param {{stopAtSubgraphInput?: Function}} [options]
 */
export function resolveLinkTargets(rootGraph, graph, prefix, link, options) {
  const out = []
  const visit = (g, pre, l, hops, trail) => {
    if (!l || hops > MAX_BOUNDARY_HOPS) return
    const here = [...trail, { graph: g, link: l }]
    if (isIoId(l.target_id, SUBGRAPH_OUTPUT_ID)) {
      // Out through the owning SubgraphNode's output `target_slot`.
      if (!pre) return
      const owner = locateByPathId(rootGraph, pre)
      if (!owner) return
      const ids = owner.node.outputs?.[l.target_slot]?.links
      for (const id of Array.isArray(ids) ? ids : []) {
        visit(owner.graph, owner.prefix, graphLink(owner.graph, id), hops + 1, here)
      }
      return
    }
    const target = nodeIn(g, l.target_id)
    if (!target) return
    if (isSubgraphNode(target)) {
      if (options?.stopAtSubgraphInput?.(target, l.target_slot)) {
        out.push({
          node: target,
          graph: g,
          pathId: joinPath(pre, target.id),
          slot: l.target_slot,
          link: l,
          hops: here,
          boundary: true
        })
        return
      }
      // Down into the definition: whatever reads its input `target_slot`.
      for (const inner of innerLinksFromInput(target.subgraph, l.target_slot)) {
        visit(target.subgraph, joinPath(pre, target.id), inner, hops + 1, here)
      }
      return
    }
    out.push({
      node: target,
      graph: g,
      pathId: joinPath(pre, target.id),
      slot: l.target_slot,
      link: l,
      hops: here
    })
  }
  visit(graph, prefix || '', link, 0, [])
  return out
}

/**
 * Every REAL consumer of *node*'s output *slotIndex* across subgraph
 * boundaries (`resolveLinkTargets` over each link on the slot, once per
 * instance of a node inside a shared definition). The boundary-crossing
 * replacement for walking `output.links` -> `graph.getNodeById(target_id)`.
 * @param {object} rootGraph @param {object} node @param {number} slotIndex
 * @param {{stopAtSubgraphInput?: Function}} [options]
 */
export function resolveOutputTargets(rootGraph, node, slotIndex, options) {
  const out = []
  const seen = new Set()
  for (const loc of locationsOfNode(rootGraph, node)) {
    const ids = node.outputs?.[slotIndex]?.links
    for (const id of Array.isArray(ids) ? ids : []) {
      const link = graphLink(loc.graph, id)
      for (const target of resolveLinkTargets(rootGraph, loc.graph, loc.prefix, link, options)) {
        const key = `${target.pathId}#${target.slot}`
        if (seen.has(key)) continue
        seen.add(key)
        out.push(target)
      }
    }
  }
  return out
}

/**
 * Install (and RE-VERIFY) chained graph-event hooks on one graph -- the
 * stored-and-re-verified pattern from cross_sweep.js's
 * `installGraphNodeWatch` (v0.68.1), shared so every watcher in the pack
 * survives the same two things (FORMAT.md §7.10):
 *
 *  - Core's `useGraphNodeManager` cleanup and `installErrorClearingHooks`
 *    disposer RESTORE `graph.onNodeAdded`/`onNodeRemoved` to the values they
 *    captured at THEIR install whenever the active graph changes (every
 *    subgraph enter/exit) and on a Nodes 2.0 toggle, dropping any wrapper
 *    installed after them. A boolean "installed" flag then refuses to
 *    re-install and the watcher goes deaf -- so the installed wrapper is
 *    STORED per hook under *ownerKey* and every call re-verifies that the
 *    hook still IS ours, re-wrapping the CURRENT value when not. A surviving
 *    older wrapper of ours (core wrapped it, then restored it) is adopted
 *    rather than re-wrapped, so the chain stays bounded.
 *  - A subgraph's hooks fire ONLY on that subgraph, so callers loop this
 *    over `walkGraphs(root)` (see `watchAllGraphs`) every time they refresh.
 *  - SEVERAL features of this pack watch the same hook (controller, picker,
 *    Apply Set, Run Multiplier). Each wrapper therefore carries the SET of
 *    owner keys of every wrapper of ours beneath it (`__epsWatchKeys`,
 *    inherited from the function it wraps): when a sibling feature wrapped
 *    after us, our key is still in the top function's set and we ADOPT it
 *    instead of re-wrapping. Without that, N features re-verifying each
 *    other's tops would each stack one more layer on every pass, forever
 *    (unbounded); with it a layer is added only when core's own wrapper (no
 *    key set) sits on top, and core's restore cuts that back off.
 *
 * Call it from every refresh pass, not once: three compares per hook, cheap.
 * *onEvent* runs AFTER the original hook, with the CLOSURE's graph (a core
 * wrapper chaining to us may call without a receiver); its errors never
 * escape into the graph. Never throws. Returns true when it (re)wrapped at
 * least one hook -- a poll-driven caller uses that to repaint once, since an
 * event may have fired into the gap while the hook was not ours.
 * @param {object} graph
 * @param {string} ownerKey - a per-feature property name, e.g. `__epsCtrlNodeWatch`
 * @param {string[]} hooks - e.g. `['onNodeAdded', 'onNodeRemoved']`
 * @param {(graph: object, hook: string, args: unknown[]) => void} onEvent
 * @returns {boolean} whether any hook was (re)installed on this call
 */
export function watchGraphHooks(graph, ownerKey, hooks, onEvent) {
  if (!graph) return false
  let installed = false
  try {
    const stored = graph[ownerKey] || (graph[ownerKey] = {})
    for (const hook of hooks) {
      const current = graph[hook]
      if (current && current === stored[hook]) continue
      if (current && (current[ownerKey] || current.__epsWatchKeys?.has(ownerKey))) {
        stored[hook] = current // ours (or a sibling's wrapper that still contains ours)
        continue
      }
      const original = current
      const wrapper = function (...args) {
        let result
        try {
          result = original?.apply(this, args)
        } catch (error) {
          warn(`original ${hook} threw`, error)
        }
        try {
          onEvent(graph, hook, args)
        } catch (error) {
          warn(`${hook} watcher threw`, error)
        }
        return result
      }
      wrapper[ownerKey] = true
      wrapper.__epsWatchKeys = new Set([...(original?.__epsWatchKeys || []), ownerKey])
      stored[hook] = wrapper
      graph[hook] = wrapper
      installed = true
    }
  } catch (error) {
    warn('graph watch install failed', error)
  }
  return installed
}

/** `watchGraphHooks` on EVERY graph under *rootGraph* (subgraphs included,
 * because their add/remove hooks fire only on the subgraph itself). Returns
 * true when any graph needed a (re)install. */
export function watchAllGraphs(rootGraph, ownerKey, hooks, onEvent) {
  let installed = false
  for (const graph of walkGraphs(rootGraph)) {
    if (watchGraphHooks(graph, ownerKey, hooks, onEvent)) installed = true
  }
  return installed
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
