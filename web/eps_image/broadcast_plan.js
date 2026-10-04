/**
 * @file EPS Run Multiplier "broadcast" -- the PURE planner (FORMAT.md §6.10
 * "Broadcast (v1)", owner ask 2026-10-03: "this node broadcasts values across
 * a workflow so you don't have to manually hook up nodes over and over
 * again ... build into the nodes we have, specifically as an option for the
 * EPS Run Multiplier"). Plan: `research/roadmap-eps-broadcast.md` §3-§4.
 *
 * **What it decides.** Given a plain-object SNAPSHOT of the whole workflow
 * (the root graph plus every subgraph DEFINITION) and one multiplier's
 * pathId, `planBroadcast()` returns which REAL wires the multiplier should
 * make into which EMPTY inputs, which it deliberately did not make and why,
 * and which it refused because another multiplier could feed the same input.
 * It never touches litegraph, the DOM or the setting store -- same posture as
 * cross_sweep.js's `estimateRuns` (exported-pure + a thin live adapter), so
 * tests/test_broadcast_plan_js.py drives it under Node with fake snapshots.
 * The live adapter (`broadcast_graph.js`) is the only place `inputVerdict()`
 * (bypass.js, imported there, never copied) is called: it folds the verdict
 * into the snapshot as `input.verdict` so this module needs no litegraph.
 *
 * **Real wires, not virtual links (owner decision 2026-10-03).** The
 * multiplier makes ordinary links (core `node.connect`), so every graph-
 * reading feature in the pack -- the run-count readout, Bypass, Image Grid
 * Collect-only, the Distributor, the Save Image baked workflow -- keeps
 * working unchanged. That is why a proposal is a LINK description and the
 * apply step lives elsewhere.
 *
 * **Snapshot shape** (every id a STRING; the adapter stringifies):
 *   {
 *     graphs: {
 *       root: { id: 'root', nodes: {id: Node}, inputs: [], outputs: [], groups: [{key, title}] },
 *       '<subgraph uuid>': {
 *         id, name, nodes: {id: Node}, groups: [{key, title}],
 *         inputs:  [{id: '<slot uuid>', name, type}],            // definition inputs
 *         outputs: [{name, type, origin: {originId, originSlot} | null}]
 *       }
 *     }
 *   }
 *   Node = {
 *     id, classType, title, mode,
 *     subgraphId: null | '<uuid>',          // set on a SubgraphNode INSTANCE
 *     groups: [groupKey],                   // which of ITS graph's groups hold the node
 *     broadcast: null | <config>,           // multiplier nodes only
 *     inputs:  [{name, type, widget, verdict, defIndex?,
 *                link: null | {id, originId, originSlot}}],
 *     outputs: [{name, type, links: [{targetId, targetInput}]}]
 *   }
 *   `verdict` is bypass.js's `inputVerdict().why`: 'required' | 'optional' |
 *   'shape' | 'widget' | 'unknown'. A link whose origin is the subgraph's own
 *   input node has `originId === '-10'` and `originSlot` = the definition
 *   input index; an instance input's `defIndex` is that same index (falls
 *   back to its array position).
 *
 * **pathId** = the execution-id shape (`walkLiveNodes`): "12" at the root,
 * "3:5" for node 5 inside SubgraphNode 3. A definition used by two instances
 * has the same inner node under two pathIds; ancestry is therefore decided
 * per FLATTENED path, exactly the way core flattens the prompt.
 *
 * **The rules** (plan §3 principles 1-7, plus the owner's later decisions):
 *   1. Fill only EMPTY inputs. Never replace a wire (the user's wire wins).
 *   2. Type-matched outputs (model, clip, vae, model_low) go only to
 *      REQUIRED, non-widget inputs of exactly that type -- an optional input
 *      is a deliberate "use it if you want", and growing-slot nodes (the
 *      Switcher's `image_N`) would grow a fresh empty slot after every wire.
 *   3. Exact-name outputs go only to an input with exactly that NAME and TYPE
 *      -- save_prefix -> `filename_prefix`, run_info -> `run_info`, and
 *      (behind ONE ComfyUI setting, off by default, decided 2026-10-03)
 *      text -> `text`, image -> `image`, label -> `label` -- and MAY be
 *      widget-backed or optional.
 *   4. Never the multiplier itself or any ANCESTOR of it (a DFS over the
 *      flattened links, across subgraph boundaries both ways).
 *   5. Only LIVE outputs: an output whose backing input is unwired would, if
 *      consumed, fail the run (nodes_cross_sweep.py v0.51.0
 *      `_consumed_output_slots` guard). text/save_prefix/run_info are always
 *      live; model/clip/image/label/vae/model_low are live iff wired.
 *   6. Fail closed on ambiguity: another multiplier that could feed the same
 *      input means NEITHER does (conflict). Never touch another multiplier's
 *      inputs.
 *   7. Negative-prompt guard (owner decision 2026-10-03: ON): never feed a
 *      `text` input whose node's output feeds (directly) an input named
 *      `negative` -- CLIP Text Encode names positive AND negative `text`.
 *   8. WAN pairs (owner decision 2026-10-03): when `model_low` is wired,
 *      model_low goes to the MODEL input of a node whose TITLE contains
 *      "low" (case-insensitive) and `model` skips those; no such node = skip
 *      with a reason, never a guess.
 *   9. Muted/bypassed nodes are linked like any other (cheap, harmless;
 *      Use Everywhere's default).
 *  10. Reach (FORMAT.md §6.10 "Reach", owner plan M3 2026-10-03): with
 *      `config.scope === 'group'` a multiplier only claims targets inside a
 *      GROUP that contains it, in its own graph; a target inside a subgraph is
 *      decided by the SubgraphNode instance's membership in the multiplier's
 *      graph. Pure over `node.groups` (the live adapter computes membership
 *      with core's own centre-containment rule), so it needs no litegraph.
 *
 * **Nested delivery (owner requirement 2026-10-03, overriding the plan's
 * "one graph only").** Targets inside subgraphs at any depth below the
 * multiplier's graph:
 *   - Tier 1 (preferred, no definition change): an EMPTY input on a
 *     SubgraphNode instance whose definition input already feeds valid inner
 *     targets -> `via-existing-subgraph-input` (one wire, the existing inner
 *     links already carry it on).
 *   - Tier 2 (definition change): no such path -> `via-new-subgraph-input`:
 *     add a subgraph input of the output's type to the definition, link it
 *     to every valid inner target, wire every instance. FAIL CLOSED: only
 *     when EVERY instance of that definition in the workflow lives in ONE
 *     graph that this plan feeds (the multiplier's graph, or a definition
 *     that itself gets the new input); anything else -> skip "subgraph used
 *     elsewhere -- wire by hand" (a definition shared with an unfed instance
 *     would leave that instance's required input empty and fail its run).
 *     Recurses for deeper nesting; each level chains through its
 *     SubgraphNode instance.
 *   Mixed routes (an existing input at one level feeding a NEW input deeper
 *   down) are deliberately not planned -- see the Tier-1 leaf resolver.
 *
 * Proposal / record vocabulary is shared with `broadcast_graph.js` (apply +
 * reconcile) and `broadcast.js` (UI); FORMAT.md §6.10 "Broadcast (v1)" is
 * the written contract.
 */

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** The multiplier's frozen class id (FORMAT.md §6.10 / §8). */
export const MULTIPLIER_CLASS_ID = 'EPSCrossSweep'

/** `node.properties[...]` key the records live under -- stamped ONLY on the
 * multiplier (Use Everywhere stamps EVERY node: research §10 lesson 12). */
export const PROPERTY_KEY = 'Broadcast'

/** Records format version (FORMAT.md §6.10 "Broadcast (v1)"). Bump only with
 * a migration; an unknown NEWER version is read as empty rather than guessed. */
export const RECORD_VERSION = 1

/** Graph key of the root graph in snapshots and records. */
export const ROOT_GRAPH_ID = 'root'

/** `config.scope` (FORMAT.md §6.10 "Reach"): 'graph' = the whole workflow
 * (the default, what v1.3.0 always did), 'group' = only targets inside a group
 * that contains the multiplier. */
export const SCOPES = Object.freeze(['graph', 'group'])

/** `config.look` (FORMAT.md §6.10 "Tucked wires"): how broadcast wires are
 * DRAWN. Read by the rendering stage (broadcast_draw.js), never by the planner. */
export const LOOKS = Object.freeze(['tucked', 'dim', 'normal'])

/** litegraph's `SUBGRAPH_INPUT_ID` / `SUBGRAPH_OUTPUT_ID` (constants.ts),
 * stringified: the "node ids" of a definition's own input / output panels. */
export const SUBGRAPH_INPUT_ID = '-10'
export const SUBGRAPH_OUTPUT_ID = '-20'

/** Nesting ceiling and flattened-context ceiling. The frontend itself throws
 * RecursionError on cyclic definitions; these keep a pathological workflow
 * from stalling a UI-thread walk. */
export const MAX_NEST_DEPTH = 8
export const MAX_CONTEXTS = 256
export const MAX_ENTRIES = 64
export const MAX_OTHER_MULTIPLIERS = 16

/** The nine outputs, positionally off nodes_cross_sweep.py's RETURN_NAMES
 * (tests/test_broadcast_plan_js.py pins the order against the backend).
 * `rule`: 'type' = any REQUIRED input of exactly `type`; 'type-low' = same
 * plus the WAN "low" title rule; 'exact' = an input named `exactName` of
 * exactly `type` (widget-backed / optional allowed). `liveInput` = the
 * multiplier input that must be wired for the output to carry values (null =
 * always live). `gated` = governed by the one ComfyUI setting (decided
 * 2026-10-03: text, image and label are OFF by default). */
export const BROADCAST_OUTPUTS = Object.freeze([
  { name: 'model', index: 0, type: 'MODEL', rule: 'type', liveInput: 'model', gated: false },
  { name: 'clip', index: 1, type: 'CLIP', rule: 'type', liveInput: 'clip', gated: false },
  { name: 'image', index: 2, type: 'IMAGE', rule: 'exact', exactName: 'image', liveInput: 'image', gated: true },
  { name: 'text', index: 3, type: 'STRING', rule: 'exact', exactName: 'text', liveInput: null, gated: true },
  { name: 'save_prefix', index: 4, type: 'STRING', rule: 'exact', exactName: 'filename_prefix', liveInput: null, gated: false },
  { name: 'label', index: 5, type: 'STRING', rule: 'exact', exactName: 'label', liveInput: 'label', gated: true },
  { name: 'vae', index: 6, type: 'VAE', rule: 'type', liveInput: 'vae', gated: false },
  { name: 'model_low', index: 7, type: 'MODEL', rule: 'type-low', liveInput: 'model_low', gated: false },
  { name: 'run_info', index: 8, type: 'STRING', rule: 'exact', exactName: 'run_info', liveInput: null, gated: false }
])

/** Output name -> spec, for lookups by record key. */
export const OUTPUT_BY_NAME = Object.freeze(
  Object.fromEntries(BROADCAST_OUTPUTS.map((spec) => [spec.name, spec]))
)

/** The skip codes (stable strings: the preview dialog groups on them, the
 * tests pin them). `reason` text is the human sentence and may be reworded. */
export const SKIP_CODES = Object.freeze({
  OUTPUT_OFF: 'output-off',
  SETTING_OFF: 'setting-off',
  OUTPUT_DEAD: 'output-dead',
  ALREADY_WIRED: 'already-wired',
  LOOP: 'loop',
  OPTIONAL: 'optional',
  UNKNOWN_REQUIRED: 'unknown-required',
  OTHER_MULTIPLIER: 'other-multiplier',
  NEGATIVE_GUARD: 'negative-guard',
  WAN_LOW: 'wan-low',
  WAN_AMBIGUOUS: 'wan-ambiguous',
  WAN_UNRESOLVED: 'wan-unresolved',
  LEFT_ALONE: 'left-alone',
  SUBGRAPH_SHARED: 'subgraph-shared',
  SUBGRAPH_MIXED: 'subgraph-mixed',
  SUBGRAPH_TOO_DEEP: 'subgraph-too-deep',
  DOUBLE_CLAIM: 'double-claim',
  NO_GROUP: 'no-group',
  SCOPE_PARTIAL: 'scope-partial'
})

/** Proposal kinds (the spec's names, verbatim). */
export const KINDS = Object.freeze({
  DIRECT: 'direct',
  EXISTING: 'via-existing-subgraph-input',
  NEW: 'via-new-subgraph-input'
})

// ---------------------------------------------------------------------------
// Records (`node.properties.Broadcast`) -- pure normalise / serialise
// ---------------------------------------------------------------------------

/** A fresh, empty config. `look` ('tucked' | 'dim' | 'normal') is read by the
 * rendering stage; `scope` ('graph' | 'group') by the planner. */
export function defaultConfig() {
  return { v: RECORD_VERSION, outputs: {}, keep: false, scope: 'graph', look: 'tucked', wired: [], skip: [] }
}

function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function cleanLinkRecord(raw) {
  if (!isPlainObject(raw)) return null
  const { g, n, i, o } = raw
  if (typeof g !== 'string' || g === '') return null
  if (n == null || String(n) === '') return null
  if (typeof i !== 'string' || i === '') return null
  if (!isPlainObject(o)) return null
  if (typeof o.m === 'string' && o.m !== '') return { g, n: String(n), i, o: { m: o.m } }
  if (typeof o.s === 'string' && o.s !== '') return { g, n: String(n), i, o: { s: o.s } }
  return null
}

function cleanMadeRecord(raw) {
  if (!isPlainObject(raw)) return null
  const { g, id, name, type } = raw
  if (typeof g !== 'string' || g === '' || typeof id !== 'string' || id === '') return null
  if (typeof name !== 'string' || name === '') return null
  return { g, id, name, type: typeof type === 'string' ? type : '' }
}

function cleanWiredEntry(raw) {
  if (!isPlainObject(raw)) return null
  const { key, out, kind, to, input } = raw
  if (typeof key !== 'string' || key === '' || !OUTPUT_BY_NAME[out]) return null
  if (!Object.values(KINDS).includes(kind)) return null
  const links = Array.isArray(raw.links) ? raw.links.map(cleanLinkRecord).filter(Boolean) : []
  // A record with no link to verify or remove is meaningless -- drop it.
  if (links.length === 0) return null
  const made = Array.isArray(raw.made) ? raw.made.map(cleanMadeRecord).filter(Boolean) : []
  const entry = {
    key,
    out,
    kind,
    to: to == null ? '' : String(to),
    input: typeof input === 'string' ? input : '',
    links,
    made
  }
  if (raw.withdrawn === true) entry.withdrawn = true
  return entry
}

/**
 * Parses `node.properties.Broadcast` defensively. A missing / malformed /
 * NEWER-versioned value reads as an empty config (never throws, never
 * guesses) -- old workflows have no property at all and behave exactly as
 * before (broadcast is opt-in; nothing wires on load).
 * @param {unknown} raw @returns {ReturnType<typeof defaultConfig>}
 */
export function normalizeConfig(raw) {
  const cfg = defaultConfig()
  if (!isPlainObject(raw)) return cfg
  if (typeof raw.v === 'number' && raw.v > RECORD_VERSION) return cfg
  if (isPlainObject(raw.outputs)) {
    for (const spec of BROADCAST_OUTPUTS) {
      if (typeof raw.outputs[spec.name] === 'boolean') cfg.outputs[spec.name] = raw.outputs[spec.name]
    }
  }
  cfg.keep = raw.keep === true
  if (SCOPES.includes(raw.scope)) cfg.scope = raw.scope
  if (LOOKS.includes(raw.look)) cfg.look = raw.look
  if (Array.isArray(raw.wired)) cfg.wired = raw.wired.map(cleanWiredEntry).filter(Boolean)
  if (Array.isArray(raw.skip)) {
    cfg.skip = [...new Set(raw.skip.filter((k) => typeof k === 'string' && k !== ''))]
  }
  return cfg
}

/** The JSON-safe object to store back on `node.properties`. An all-default
 * config serialises to `null` so a multiplier that never used broadcast
 * carries NO property (old workflows stay byte-identical). */
export function serializeConfig(cfg) {
  const clean = normalizeConfig(cfg)
  const isDefault =
    Object.keys(clean.outputs).length === 0 &&
    !clean.keep &&
    clean.wired.length === 0 &&
    clean.skip.length === 0 &&
    clean.look === 'tucked' &&
    clean.scope === 'graph'
  return isDefault ? null : clean
}

/** Whether output *name* is enabled in *cfg* under *settings*: the three
 * gated outputs need the setting ON; everything honours the per-output
 * toggle (default ON). */
export function outputEnabled(cfg, spec, settings) {
  if (spec.gated && settings?.exactNames !== true) return false
  return cfg?.outputs?.[spec.name] !== false
}

/** `key` of a proposal / record / leave-alone entry. Direct and existing
 * proposals are keyed by their landing input; a NEW-input proposal is keyed
 * by the DEFINITION so it survives deleting/adding instances. */
export function proposalKey(output, pathId, inputName) {
  return `${output}|${pathId}|${inputName}`
}
export function newInputKey(output, definitionId) {
  return `${output}|def:${definitionId}|new`
}

// ---------------------------------------------------------------------------
// Path / context helpers
// ---------------------------------------------------------------------------

/** Joins path segments with ':' and DROPS empty ones (an empty entry /
 * suffix must never leave a dangling separator -- "7:" is not a pathId). */
function joinSegments(...parts) {
  return parts
    .filter((part) => part !== '' && part !== null && part !== undefined)
    .map(String)
    .join(':')
}
const joinPath = joinSegments

function numericOrString(a, b) {
  const na = Number(a)
  const nb = Number(b)
  if (Number.isFinite(na) && Number.isFinite(nb) && na !== nb) return na - nb
  return String(a) < String(b) ? -1 : String(a) > String(b) ? 1 : 0
}

function nodesOf(graph) {
  return Object.values(graph?.nodes || {}).sort((a, b) => numericOrString(a.id, b.id))
}

/** A flattened graph CONTEXT: which graph definition, under which instance
 * path. `parent`/`inst` let `resolveOrigin` climb out through a definition's
 * input node. */
function rootContext() {
  return { gid: ROOT_GRAPH_ID, prefix: '', parent: null, inst: null }
}
function childContext(ctx, instanceNode) {
  return { gid: instanceNode.subgraphId, prefix: joinPath(ctx.prefix, instanceNode.id), parent: ctx, inst: String(instanceNode.id) }
}

/**
 * Resolves *pathId* ("3:5") to `{ctx, node, nodeId, pathId, gid, prefix}`, or
 * null when any segment is missing / a non-final segment is not a subgraph
 * instance.
 * @param {object} snapshot @param {string} pathId
 */
export function locate(snapshot, pathId) {
  const segments = String(pathId ?? '').split(':')
  let ctx = rootContext()
  let node = null
  for (let i = 0; i < segments.length; i++) {
    const graph = snapshot?.graphs?.[ctx.gid]
    node = graph?.nodes?.[segments[i]] || null
    if (!node) return null
    if (i < segments.length - 1) {
      if (!node.subgraphId || !snapshot.graphs[node.subgraphId]) return null
      ctx = childContext(ctx, node)
    }
  }
  return { ctx, node, nodeId: String(node.id), pathId: String(pathId), gid: ctx.gid, prefix: ctx.prefix }
}

/**
 * Follows *link* (`{originId, originSlot}`) in context *ctx* to the real,
 * non-subgraph origin node, crossing subgraph boundaries BOTH ways: an origin
 * that is a SubgraphNode instance is entered through its definition's output
 * panel; an origin that is the definition's own input panel climbs out
 * through the instance's matching input. Returns `{ctx, node, nodeId, slot,
 * pathId}` or null for an unwired link / dead end / loop. Hop-capped.
 */
export function resolveOrigin(snapshot, ctx, link) {
  let cur = ctx
  let lk = link
  for (let hop = 0; hop < 256; hop++) {
    if (!lk || lk.originId == null) return null
    const originId = String(lk.originId)
    const graph = snapshot.graphs[cur.gid]
    if (originId === SUBGRAPH_INPUT_ID) {
      if (!cur.parent) return null
      const instance = snapshot.graphs[cur.parent.gid]?.nodes?.[cur.inst]
      const slot = (instance?.inputs || []).find(
        (input, index) => (input.defIndex ?? index) === lk.originSlot
      )
      lk = slot?.link ?? null
      cur = cur.parent
      continue
    }
    const node = graph?.nodes?.[originId]
    if (!node) return null
    if (node.subgraphId) {
      const definition = snapshot.graphs[node.subgraphId]
      const panel = definition?.outputs?.[lk.originSlot]
      lk = panel?.origin ?? null
      cur = childContext(cur, node)
      continue
    }
    return { ctx: cur, node, nodeId: originId, slot: lk.originSlot, pathId: joinPath(cur.prefix, originId) }
  }
  return null
}

/**
 * Every ancestor of the multiplier as a Set of flattened pathIds (the
 * multiplier itself included): BFS over every input link, through reroutes,
 * switchers and subgraph boundaries. Feeding any node in this set would
 * close a cycle (Use Everywhere removed its loop check in 2025 and relies on
 * the backend's DependencyCycleError at queue time -- research §5.1 / §10.10;
 * here the loop is ruled out BEFORE the queue).
 */
export function computeAncestors(snapshot, loc) {
  const seen = new Set([loc.pathId])
  const queue = [{ ctx: loc.ctx, node: loc.node }]
  let guard = 0
  while (queue.length && guard++ < 50000) {
    const { ctx, node } = queue.shift()
    for (const input of node.inputs || []) {
      if (!input.link) continue
      const origin = resolveOrigin(snapshot, ctx, input.link)
      if (!origin || seen.has(origin.pathId)) continue
      seen.add(origin.pathId)
      queue.push({ ctx: origin.ctx, node: origin.node })
    }
  }
  return seen
}

/** Every flattened context under the root (root included), breadth-first,
 * capped -- used to enumerate OTHER multipliers for the conflict rule. */
function allContexts(snapshot) {
  const out = []
  const queue = [rootContext()]
  while (queue.length && out.length < MAX_CONTEXTS) {
    const ctx = queue.shift()
    out.push(ctx)
    for (const node of nodesOf(snapshot.graphs[ctx.gid])) {
      if (node.subgraphId && snapshot.graphs[node.subgraphId]) queue.push(childContext(ctx, node))
    }
  }
  return out
}

/**
 * Every node's flattened pathId across every context (a definition used by
 * two instances contributes its nodes under BOTH paths) -- the set Keep wired
 * diffs between passes to tell a NEW node from one that was always there
 * (FORMAT.md §6.10 "Keep wired": only fresh nodes are auto-wired, so an undo,
 * a tab switch or a deliberate unplug never makes the multiplier fight back).
 * @param {object} snapshot @returns {Set<string>}
 */
export function allNodePaths(snapshot) {
  const paths = new Set()
  for (const ctx of allContexts(snapshot)) {
    for (const node of nodesOf(snapshot.graphs[ctx.gid])) paths.add(joinPath(ctx.prefix, node.id))
  }
  return paths
}

// ---------------------------------------------------------------------------
// Small predicates
// ---------------------------------------------------------------------------

/** The WAN rule (owner decision 2026-10-03): a title that contains "low",
 * case-insensitive -- as a WORD (v1.3.0 review): "KSampler (low noise)",
 * "Low Noise", "low_noise", "sampler-low" match; "Flow Match", "Slow",
 * "Below", "Lowpass" do NOT (a plain substring test caught every "Flow"
 * node, and a stray high/low pairing silently wastes an overnight run). Any
 * non-letter counts as a boundary, so `_` and `-` separate words too. */
export function titleIsLow(title) {
  return typeof title === 'string' && /(^|[^a-z])low([^a-z]|$)/i.test(title)
}

function inputByName(node, name) {
  return (node?.inputs || []).find((input) => input?.name === name) || null
}

/** Whether *node*'s output feeds (directly) an input named `negative` --
 * the negative-prompt guard's whole test (owner decision 2026-10-03). */
export function feedsNegative(node) {
  for (const output of node?.outputs || []) {
    for (const link of output?.links || []) {
      if (link?.targetInput === 'negative') return true
    }
  }
  return false
}

// ---------------------------------------------------------------------------
// Reach (FORMAT.md §6.10 "Reach")
// ---------------------------------------------------------------------------

/**
 * The multiplier's Reach for *cfg*: `{mode, inGroup, groups, keys}`. In 'graph'
 * mode `keys` is null (no filter at all -- exactly the v1.3.0 behaviour). In
 * 'group' mode `keys` is the Set of group keys, in the multiplier's OWN graph,
 * that hold the multiplier (`node.groups`, computed by the live adapter from
 * core's own centre-containment rule); `inGroup` is false when that set is
 * empty ("not inside a group").
 */
export function scopeOf(snapshot, loc, cfg) {
  if (cfg?.scope !== 'group') return { mode: 'graph', inGroup: true, groups: [], keys: null }
  const keys = new Set((Array.isArray(loc.node.groups) ? loc.node.groups : []).map(String))
  const titles = new Map((snapshot.graphs[loc.gid]?.groups || []).map((g) => [String(g.key), g.title || '']))
  return {
    mode: 'group',
    inGroup: keys.size > 0,
    groups: [...keys].map((key) => ({ key, title: titles.get(key) ?? '' })),
    keys
  }
}

/**
 * Whether the node at flattened *pathId* is inside the multiplier's Reach: the
 * decision is made by the node in the MULTIPLIER'S OWN graph that the path
 * leads through -- the target itself when it lives there, the SubgraphNode
 * INSTANCE when it is nested (FORMAT.md §6.10: "the instance's membership in
 * the multiplier's graph decides"). A path that does not run under the
 * multiplier's context is never in scope.
 */
function pathInReach(snapshot, loc, keys, pathId) {
  let rest = pathId
  if (loc.prefix) {
    if (!pathId.startsWith(`${loc.prefix}:`)) return false
    rest = pathId.slice(loc.prefix.length + 1)
  }
  const top = rest.split(':')[0]
  const holder = snapshot.graphs[loc.gid]?.nodes?.[top]
  return (holder?.groups || []).some((key) => keys.has(String(key)))
}

// ---------------------------------------------------------------------------
// The planner
// ---------------------------------------------------------------------------

/** Skip / eligibility outcome helpers. */
const OK = Object.freeze({ outcome: 'ok' })
const SILENT = Object.freeze({ outcome: 'silent' })
function skipOutcome(code, reason) {
  return { outcome: 'skip', code, reason }
}

/**
 * The raw (pre-conflict) plan for one multiplier -- `planBroadcast` wraps it
 * with the two-multiplier rule, and calls it again for every OTHER
 * multiplier to learn which inputs they would claim.
 */
function collectRaw(snapshot, loc, cfgIn, settings) {
  const cfg = normalizeConfig(cfgIn)
  const mnode = loc.node
  const ancestors = computeAncestors(snapshot, loc)
  const liveInputs = {}
  for (const input of mnode.inputs || []) liveInputs[input.name] = Boolean(input.link)
  const modelLowWired = liveInputs.model_low === true
  const leftAlone = new Set(cfg.skip)
  const madeRecords = cfg.wired.flatMap((entry) => entry.made.map((made) => ({ ...made, out: entry.out })))

  const result = { proposals: [], skips: [], outputs: [] }
  const scope = scopeOf(snapshot, loc, cfg)
  /** Names handed out for NEW subgraph inputs this plan, per definition, so
   * two outputs never collide on one definition. */
  const assignedNames = new Map()
  const claimedKeys = new Set()

  // ---- instance bookkeeping (definition -> where it is instantiated)
  const instancesOfDefinition = new Map()
  for (const graph of Object.values(snapshot.graphs)) {
    for (const node of nodesOf(graph)) {
      if (!node.subgraphId) continue
      const list = instancesOfDefinition.get(node.subgraphId) || []
      list.push({ gid: graph.id, node })
      instancesOfDefinition.set(node.subgraphId, list)
    }
  }

  const entriesMemo = new Map([[loc.gid, [loc.prefix]]])
  const allowMemo = new Map()

  /** Fail-closed: may *definitionId* get a NEW input? Every instance of it
   * must live in ONE graph, and that graph must be the multiplier's graph or
   * an allowed definition itself. Returns `{ok, code?, reason?, container?}`. */
  function allowance(definitionId, depth = 0) {
    if (allowMemo.has(definitionId)) return allowMemo.get(definitionId)
    const instances = instancesOfDefinition.get(definitionId) || []
    let verdict
    if (depth > MAX_NEST_DEPTH) {
      verdict = { ok: false, code: SKIP_CODES.SUBGRAPH_TOO_DEEP, reason: 'is nested too deeply' }
    } else if (instances.length === 0) {
      verdict = { ok: false, code: SKIP_CODES.SUBGRAPH_SHARED, reason: 'has no instance in this workflow' }
    } else {
      const containers = [...new Set(instances.map((entry) => entry.gid))]
      if (containers.length > 1) {
        verdict = {
          ok: false,
          code: SKIP_CODES.SUBGRAPH_SHARED,
          reason: `is also used in ${containers.length - 1} other graph(s)`
        }
      } else if (containers[0] === loc.gid) {
        verdict = { ok: true, container: containers[0] }
      } else {
        const parent = allowance(containers[0], depth + 1)
        verdict = parent.ok
          ? { ok: true, container: containers[0] }
          : { ok: false, code: parent.code, reason: parent.reason || 'sits inside a subgraph that is not fed' }
      }
    }
    allowMemo.set(definitionId, verdict)
    return verdict
  }

  /** Flattened instance prefixes of an ALLOWED *definitionId*; `[]` when the
   * instance fan-out is past MAX_ENTRIES (treated as not allowed). */
  function entriesOf(definitionId) {
    if (entriesMemo.has(definitionId)) return entriesMemo.get(definitionId)
    const verdict = allowance(definitionId)
    let entries = []
    if (verdict.ok) {
      const parentEntries = entriesOf(verdict.container)
      for (const { node } of instancesOfDefinition.get(definitionId) || []) {
        for (const parentEntry of parentEntries) entries.push(joinSegments(parentEntry, node.id))
      }
      if (entries.length > MAX_ENTRIES) entries = []
    }
    entriesMemo.set(definitionId, entries)
    return entries
  }

  function pushSkip(code, reason, extra = {}) {
    result.skips.push({ code, reason, ...extra })
  }

  // ---------------------------------------------------------------- outputs
  // Reach 'group' with the multiplier in NO group: nothing is in scope, so say
  // so once (the dialog shows it as a note) instead of silently planning
  // nothing -- and claim nothing, so it never blocks another multiplier.
  if (scope.keys && !scope.inGroup) {
    pushSkip(
      SKIP_CODES.NO_GROUP,
      'Reach is "only my group" but this multiplier is not inside a group — put it (and the nodes it ' +
        'should feed) in a group, or switch Reach back to the whole workflow'
    )
    for (const spec of BROADCAST_OUTPUTS) result.outputs.push({ output: spec.name, state: 'no-group', reason: 'not inside a group' })
    return { result, loc, cfg, ancestors, scope }
  }
  for (const spec of BROADCAST_OUTPUTS) {
    const status = { output: spec.name, state: 'on', reason: '' }
    result.outputs.push(status)
    if (spec.gated && settings?.exactNames !== true) {
      status.state = 'setting-off'
      status.reason =
        `${spec.name} is only broadcast when Settings › EPSNodes › "Run Multiplier broadcast: also ` +
        'connect text, image and label to inputs with the same name" is on'
      pushSkip(SKIP_CODES.SETTING_OFF, status.reason, { output: spec.name })
      continue
    }
    if (cfg.outputs[spec.name] === false) {
      status.state = 'off'
      status.reason = `${spec.name} is switched off for this multiplier`
      pushSkip(SKIP_CODES.OUTPUT_OFF, status.reason, { output: spec.name })
      continue
    }
    if (spec.liveInput && !liveInputs[spec.liveInput]) {
      status.state = 'dead'
      status.reason =
        `the ${spec.liveInput} input is not wired, so the ${spec.name} output is not live — ` +
        'a wire from it would fail the run'
      pushSkip(SKIP_CODES.OUTPUT_DEAD, status.reason, { output: spec.name })
      continue
    }
    collectOutput(spec)
  }

  // ------------------------------------------------------------- per output
  function collectOutput(spec) {
    const reachMemo = new Map()
    const topProposals = []
    /** model_low only: was ANY low-titled node with a MODEL input seen (in
     * any state)? Distinguishes "none exists" (report it) from "it exists
     * but is already wired / left alone" (say nothing). */
    let lowSeen = false

    /**
     * Evaluate ONE input of ONE node across every flattened path (entry) the
     * node can be reached under. `suffix` = extra instance ids below the
     * entry (nested existing-chain leaves). `assumeEmpty` = the input is a
     * subgraph-input leaf (already wired from -10 by definition). Order is
     * deliberate: rules that make an input "not a candidate at all" come
     * first and stay SILENT; reportable reasons come after, so the preview
     * only lists things the user could reasonably have expected.
     */
    function evalInput(entries, suffix, node, input, { assumeEmpty = false, titles = [] } = {}) {
      // 1. does the input match this output's rule at all?
      if (input.type !== spec.type) return SILENT
      if (spec.rule === 'exact' && input.name !== spec.exactName) return SILENT
      const paths = entries.map((entry) => joinSegments(entry, suffix, node.id))
      // Reach (group scope): EVERY flattened copy of the target must be inside
      // the multiplier's group(s) -- a definition's new input is wired on all
      // of its instances, so a half-inside subgraph can't be fed (below).
      let reach = 'in'
      if (scope.keys) {
        const inside = paths.filter((path) => pathInReach(snapshot, loc, scope.keys, path)).length
        reach = inside === paths.length ? 'in' : inside === 0 ? 'out' : 'partial'
      }
      // 2. other multipliers' inputs are never touched
      if (node.classType === MULTIPLIER_CLASS_ID) {
        if (paths.includes(loc.pathId) || reach === 'out') return SILENT
        return skipOutcome(
          SKIP_CODES.OTHER_MULTIPLIER,
          'another EPS Run Multiplier — broadcast never feeds a multiplier'
        )
      }
      // 3. model_low: only nodes whose TITLE contains "low" are candidates
      const low = titleIsLow(node.title) || titles.some(titleIsLow)
      if (spec.name === 'model_low') {
        if (!low) return SILENT
        lowSeen = true // seen even when outside the group: "no low sampler exists" would be a lie
      }
      // 3b. outside the group: not a candidate at all -> SILENT, so a
      // group-scoped preview lists only what the user could expect (the
      // plan's `scope` carries the one explanatory line).
      if (reach === 'out') return SILENT
      // 4. only EMPTY inputs; the user's wire always wins
      const typeRule = spec.rule !== 'exact'
      const verdict = input.verdict
      if (!assumeEmpty && input.link) {
        return !typeRule || verdict === 'required'
          ? skipOutcome(SKIP_CODES.ALREADY_WIRED, 'already wired')
          : SILENT
      }
      // 5. type-matched outputs: REQUIRED, non-widget inputs only
      if (typeRule) {
        if (verdict === 'widget') return SILENT
        if (verdict === 'optional' || verdict === 'shape') {
          return skipOutcome(
            SKIP_CODES.OPTIONAL,
            'optional input — filling it could silently change what the node does'
          )
        }
        if (verdict !== 'required') {
          return skipOutcome(SKIP_CODES.UNKNOWN_REQUIRED, "can't tell whether this input is required")
        }
      }
      // 5b. half inside the group: only worth saying for an input that would
      // otherwise be fed (an already-wired / optional one was reported above).
      if (reach === 'partial') {
        return skipOutcome(
          SKIP_CODES.SCOPE_PARTIAL,
          'this subgraph is used both inside and outside the group — broadcasting into it would also feed ' +
            'the copy outside, so wire it by hand'
        )
      }
      // 6. ancestors: feeding one closes a cycle
      if (paths.some((path) => ancestors.has(path))) {
        return skipOutcome(SKIP_CODES.LOOP, 'would create a loop (this node feeds the multiplier)')
      }
      // 7. negative-prompt guard (owner decision 2026-10-03: ON)
      if (spec.name === 'text' && feedsNegative(node)) {
        return skipOutcome(
          SKIP_CODES.NEGATIVE_GUARD,
          "this node feeds a negative prompt — the multiplier's text is not wired into it"
        )
      }
      // 8. WAN pairs: `model` skips the low targets once model_low is wired
      if (spec.name === 'model' && modelLowWired && low) {
        return skipOutcome(SKIP_CODES.WAN_LOW, 'its title contains "low" — it gets model_low, not model')
      }
      return OK
    }

    /** Def-level leaves of definition input *defIndex*: the inner inputs it
     * already feeds, following existing chains through nested instances. */
    function leavesOf(definitionId, defIndex, suffix = '', depth = 0) {
      const graph = snapshot.graphs[definitionId]
      const leaves = []
      if (!graph || depth > MAX_NEST_DEPTH) return leaves
      for (const node of nodesOf(graph)) {
        for (let k = 0; k < (node.inputs || []).length; k++) {
          const input = node.inputs[k]
          const link = input?.link
          if (!link || String(link.originId) !== SUBGRAPH_INPUT_ID || link.originSlot !== defIndex) continue
          if (node.subgraphId) {
            leaves.push(...leavesOf(node.subgraphId, input.defIndex ?? k, joinSegments(suffix, node.id), depth + 1))
          } else {
            leaves.push({ gid: definitionId, node, input, suffix })
          }
        }
      }
      return leaves
    }

    function nextName(definitionId, base) {
      const taken = new Set((snapshot.graphs[definitionId]?.inputs || []).map((input) => input.name))
      const handed = assignedNames.get(definitionId) || new Set()
      for (const name of handed) taken.add(name)
      let name = base
      let n = 1
      while (taken.has(name)) name = `${base}_${n++}`
      handed.add(name)
      assignedNames.set(definitionId, handed)
      return name
    }

    /** All endpoints reachable inside graph *gid* (definition-level), as
     * `{kind:'direct'|'existing'|'new', ...}`. Memoised per (gid, entries,
     * probe). A PROBE walk records no skips: it only answers "is there
     * anything here broadcast WOULD feed?" for a definition it may not
     * touch. */
    function reachGraph(gid, entries, depth, probe = false) {
      const memoKey = `${probe ? 'probe' : 'real'}|${gid}|${entries.join(',')}`
      if (reachMemo.has(memoKey)) return reachMemo.get(memoKey)
      const endpoints = []
      reachMemo.set(memoKey, endpoints)
      const graph = snapshot.graphs[gid]
      const attribPath = (node) => joinSegments(entries[0] ?? '', node.id)
      const skip = (code, reason, node, inputName) => {
        if (probe) return
        pushSkip(code, reason, {
          output: spec.name,
          targetPathId: attribPath(node),
          inputName,
          targetTitle: node.title,
          targetClass: node.classType
        })
      }

      for (const node of nodesOf(graph)) {
        if (!node.subgraphId) {
          for (const input of node.inputs || []) {
            const outcome = evalInput(entries, '', node, input)
            if (outcome.outcome === 'ok') endpoints.push({ kind: 'direct', gid, node, input, entries })
            else if (outcome.outcome === 'skip') skip(outcome.code, outcome.reason, node, input.name)
          }
          continue
        }

        // ---- a SubgraphNode instance: Tier 1, then Tier 2
        const definition = snapshot.graphs[node.subgraphId]
        if (!definition) continue
        const instanceEntries = entries.map((entry) => joinSegments(entry, node.id))

        // Tier 1 -- an EMPTY input whose definition input already feeds
        // valid inner targets (no definition change, one wire).
        for (let k = 0; k < (node.inputs || []).length; k++) {
          const slot = node.inputs[k]
          if (slot.link || slot.type !== spec.type) continue
          const leaves = leavesOf(node.subgraphId, slot.defIndex ?? k)
          if (leaves.length === 0) continue
          const outcomes = leaves.map((leaf) => ({
            leaf,
            outcome: evalInput(instanceEntries, leaf.suffix, leaf.node, leaf.input, {
              assumeEmpty: true,
              titles: [node.title]
            })
          }))
          const good = outcomes.filter((entry) => entry.outcome.outcome === 'ok')
          const bad = outcomes.filter((entry) => entry.outcome.outcome === 'skip')
          if (good.length === 0) {
            if (bad[0]) skip(bad[0].outcome.code, bad[0].outcome.reason, node, slot.name)
            continue
          }
          if (good.length < leaves.length) {
            skip(
              SKIP_CODES.SUBGRAPH_MIXED,
              `its "${slot.name}" input also feeds ${leaves.length - good.length} input(s) ` +
                'broadcast must not fill — wire it by hand',
              node,
              slot.name
            )
            continue
          }
          endpoints.push({
            kind: 'existing',
            gid,
            node,
            input: slot,
            leaves: good.map((entry) => entry.leaf),
            entries: instanceEntries
          })
        }

        // Tier 2 -- no path exists: a NEW input on the definition (fail closed).
        if (depth + 1 > MAX_NEST_DEPTH) {
          skip(SKIP_CODES.SUBGRAPH_TOO_DEEP, 'subgraphs nested too deeply — wire by hand', node, undefined)
          continue
        }
        const verdict = probe ? { ok: true } : allowance(node.subgraphId)
        const innerEntries = probe ? instanceEntries : verdict.ok ? entriesOf(node.subgraphId) : []
        if (verdict.ok && innerEntries.length > 0) {
          const inner = reachGraph(node.subgraphId, innerEntries, depth + 1, probe)
          if (inner.length > 0) endpoints.push({ kind: 'new', gid, node, definition, entries: instanceEntries })
          continue
        }
        // Not allowed (or fan-out capped): report it only when it matters.
        const wouldFeed = reachGraph(node.subgraphId, instanceEntries, depth + 1, true).length > 0
        if (wouldFeed) {
          const code = verdict.code === SKIP_CODES.SUBGRAPH_TOO_DEEP ? verdict.code : SKIP_CODES.SUBGRAPH_SHARED
          skip(
            code,
            `subgraph "${definition.name || definition.id}" ${verdict.reason || 'has too many instances'} — ` +
              'broadcasting into it would leave the other uses unfed. Subgraph used elsewhere — wire by hand',
            node,
            undefined
          )
        }
      }
      return endpoints
    }

    function reachDescriptor(node, input, entries, suffix = '') {
      const pathIds = entries.map((entry) => joinSegments(entry, suffix, node.id))
      return {
        pathId: pathIds[0],
        pathIds,
        title: node.title,
        classType: node.classType,
        input: input.name
      }
    }

    function immediateProposal(endpoint) {
      const { node, input } = endpoint
      const link = { op: 'link', graph: endpoint.gid, from: { m: spec.name }, to: { node: String(node.id), input: input.name } }
      if (endpoint.kind === 'direct') {
        const reach = reachDescriptor(node, input, endpoint.entries)
        return {
          key: proposalKey(spec.name, reach.pathId, input.name),
          output: spec.name,
          outputIndex: spec.index,
          kind: KINDS.DIRECT,
          targetPathId: reach.pathId,
          inputName: input.name,
          targetTitle: node.title,
          targetClass: node.classType,
          reaches: [reach],
          reachKeys: reach.pathIds.map((p) => `${p}|${input.name}`),
          instances: [],
          definition: null,
          definitions: [],
          newInputName: null,
          reuse: false,
          steps: [link]
        }
      }
      const reaches = endpoint.leaves.map((leaf) => reachDescriptor(leaf.node, leaf.input, endpoint.entries, leaf.suffix))
      const landingPath = endpoint.entries[0]
      return {
        key: proposalKey(spec.name, landingPath, input.name),
        output: spec.name,
        outputIndex: spec.index,
        kind: KINDS.EXISTING,
        targetPathId: landingPath,
        inputName: input.name,
        targetTitle: node.title,
        targetClass: node.classType,
        reaches,
        reachKeys: reaches.flatMap((reach) => reach.pathIds.map((p) => `${p}|${reach.input}`)),
        instances: [],
        definition: { id: node.subgraphId, name: snapshot.graphs[node.subgraphId]?.name || '' },
        definitions: [],
        newInputName: null,
        reuse: false,
        steps: [link]
      }
    }

    /**
     * Emits the steps for giving *definitionId* a (new or reused) input of
     * this output's type, wired from *sourceOfParent* in each instance's own
     * graph (the multiplier at g0, otherwise the PARENT definition's own
     * input), and linking it to everything inside. Recurses for nested NEW
     * inputs. A REUSED input (recorded in `made`, still on the definition)
     * adds no input and no outer wires -- instances not yet fed surface as
     * Tier-1 proposals of their own.
     */
    function stepsForDefinition(definitionId, sourceOfParent, ctx, depth) {
      const definition = snapshot.graphs[definitionId]
      const made = madeRecords.find(
        (m) => m.g === definitionId && m.out === spec.name && (definition.inputs || []).some((i) => i.id === m.id)
      )
      const reuse = Boolean(made)
      const name = reuse ? made.name : nextName(definitionId, spec.name)
      const ref = reuse ? { sub: made.id } : { ref: `in:${definitionId}:${spec.name}` }
      if (depth === 0) {
        ctx.rootName = name
        ctx.rootReuse = reuse
      }
      ctx.definitions.push({ id: definitionId, name: definition?.name || '', inputName: name, reuse })
      if (!reuse) {
        ctx.steps.push({ op: 'add-input', graph: definitionId, name, valueType: spec.type, ref: ref.ref })
        for (const { gid, node } of instancesOfDefinition.get(definitionId) || []) {
          ctx.steps.push({ op: 'link', graph: gid, from: sourceOfParent, to: { node: String(node.id), input: name } })
        }
      }
      for (const { node } of instancesOfDefinition.get(definitionId) || []) {
        ctx.instances.push({ nodeId: String(node.id), title: node.title })
      }
      const inner = reachGraph(definitionId, entriesOf(definitionId), depth + 1)
      const seenNew = new Set()
      for (const endpoint of inner) {
        if (endpoint.kind === 'new') {
          if (seenNew.has(endpoint.definition.id)) continue
          seenNew.add(endpoint.definition.id)
          stepsForDefinition(endpoint.definition.id, ref, ctx, depth + 1)
          continue
        }
        ctx.steps.push({
          op: 'link',
          graph: definitionId,
          from: ref,
          to: { node: String(endpoint.node.id), input: endpoint.input.name }
        })
        const reaches =
          endpoint.kind === 'direct'
            ? [reachDescriptor(endpoint.node, endpoint.input, endpoint.entries)]
            : endpoint.leaves.map((leaf) => reachDescriptor(leaf.node, leaf.input, endpoint.entries, leaf.suffix))
        for (const reach of reaches) {
          ctx.reaches.push(reach)
          for (const p of reach.pathIds) ctx.reachKeys.push(`${p}|${reach.input}`)
        }
      }
    }

    function newProposal(definitionId, endpoints) {
      const definition = snapshot.graphs[definitionId]
      const first = endpoints[0].node
      const ctx = { reaches: [], reachKeys: [], steps: [], instances: [], definitions: [], rootName: '', rootReuse: false }
      stepsForDefinition(definitionId, { m: spec.name }, ctx, 0)
      return {
        key: newInputKey(spec.name, definitionId),
        output: spec.name,
        outputIndex: spec.index,
        kind: KINDS.NEW,
        targetPathId: endpoints[0].entries[0],
        inputName: ctx.rootName,
        targetTitle: first.title,
        targetClass: first.classType,
        reaches: ctx.reaches,
        reachKeys: ctx.reachKeys,
        instances: ctx.instances,
        definition: { id: definitionId, name: definition?.name || '' },
        definitions: ctx.definitions,
        newInputName: ctx.rootName,
        reuse: ctx.rootReuse,
        steps: ctx.steps
      }
    }

    // ------------------------------------------------ proposals from g0
    const g0Endpoints = reachGraph(loc.gid, [loc.prefix], 0)
    const newByDefinition = new Map()
    for (const endpoint of g0Endpoints) {
      if (endpoint.kind === 'new') {
        const list = newByDefinition.get(endpoint.definition.id) || []
        list.push(endpoint)
        newByDefinition.set(endpoint.definition.id, list)
        continue
      }
      topProposals.push(immediateProposal(endpoint))
    }
    for (const [definitionId, endpoints] of newByDefinition) {
      topProposals.push(newProposal(definitionId, endpoints))
    }

    // ---- WAN: model_low needs ONE home per node; never guess between two.
    if (spec.name === 'model_low') {
      const byNode = new Map()
      for (const proposal of topProposals) {
        byNode.set(proposal.targetPathId, [...(byNode.get(proposal.targetPathId) || []), proposal])
      }
      let ambiguous = false
      for (const [nodeKey, list] of byNode) {
        if (list.length < 2) continue
        ambiguous = true
        for (const proposal of list) topProposals.splice(topProposals.indexOf(proposal), 1)
        pushSkip(
          SKIP_CODES.WAN_AMBIGUOUS,
          `${list[0].targetTitle || nodeKey} has ${list.length} empty MODEL inputs — wire model_low by hand`,
          { output: spec.name, targetPathId: nodeKey, targetTitle: list[0].targetTitle }
        )
      }
      if (!lowSeen && !ambiguous) {
        pushSkip(
          SKIP_CODES.WAN_UNRESOLVED,
          'WAN pair: title one sampler "…low…" (e.g. "KSampler (low noise)") or wire model_low by hand',
          { output: spec.name }
        )
      }
    }

    for (const proposal of topProposals) {
      if (leftAlone.has(proposal.key)) {
        pushSkip(SKIP_CODES.LEFT_ALONE, 'left alone (you unticked or unplugged it earlier)', {
          output: spec.name,
          targetPathId: proposal.targetPathId,
          inputName: proposal.inputName,
          targetTitle: proposal.targetTitle,
          key: proposal.key,
          proposal
        })
        continue
      }
      // A node already claimed by an earlier output of THIS multiplier is
      // never fed twice (the rules are disjoint by design; this is the
      // belt-and-braces for an exotic input name).
      if (proposal.reachKeys.some((reachKey) => claimedKeys.has(reachKey))) {
        pushSkip(SKIP_CODES.DOUBLE_CLAIM, 'already fed by another output of this multiplier', {
          output: spec.name,
          targetPathId: proposal.targetPathId,
          inputName: proposal.inputName,
          targetTitle: proposal.targetTitle
        })
        continue
      }
      for (const reachKey of proposal.reachKeys) claimedKeys.add(reachKey)
      result.proposals.push(proposal)
    }
  }

  return { result, loc, cfg, ancestors, scope }
}

/**
 * Plans the broadcast for the EPS Run Multiplier at *multiplierPathId*.
 *
 * @param {object} snapshot - see the file header
 * @param {string} multiplierPathId
 * @param {object} config - the multiplier's `properties.Broadcast` (raw or
 *   normalised); toggles, leave-alone list and recorded wires drive the plan
 * @param {{exactNames?: boolean}} settings - `exactNames` is the ONE ComfyUI
 *   setting that enables text/image/label (decided 2026-10-03, off by default)
 * @returns {{
 *   multiplier: string,
 *   proposals: object[], skips: object[], conflicts: object[], outputs: object[],
 *   scope: {mode: 'graph'|'group', inGroup: boolean, groups: Array<{key: string, title: string}>},
 *   error: string|null
 * }}
 */
export function planBroadcast(snapshot, multiplierPathId, config, settings) {
  const empty = {
    multiplier: String(multiplierPathId),
    proposals: [],
    skips: [],
    conflicts: [],
    outputs: [],
    scope: { mode: 'graph', inGroup: true, groups: [] },
    error: null
  }
  const loc = locate(snapshot, multiplierPathId)
  if (!loc || loc.node.classType !== MULTIPLIER_CLASS_ID) {
    return { ...empty, error: `node ${multiplierPathId} is not an ${MULTIPLIER_CLASS_ID} in this snapshot` }
  }
  const raw = collectRaw(snapshot, loc, config, settings)
  const plan = {
    ...empty,
    proposals: raw.result.proposals,
    skips: raw.result.skips,
    outputs: raw.result.outputs,
    scope: { mode: raw.scope.mode, inGroup: raw.scope.inGroup, groups: raw.scope.groups }
  }

  // ---- the two-multiplier rule: neither feeds an input both could feed
  const claims = new Map() // reachKey -> [otherMultiplierPathId]
  let others = 0
  for (const ctx of allContexts(snapshot)) {
    for (const node of nodesOf(snapshot.graphs[ctx.gid])) {
      if (node.classType !== MULTIPLIER_CLASS_ID) continue
      const pathId = joinPath(ctx.prefix, node.id)
      if (pathId === loc.pathId) continue
      if (++others > MAX_OTHER_MULTIPLIERS) break
      const otherLoc = locate(snapshot, pathId)
      if (!otherLoc) continue
      const otherRaw = collectRaw(snapshot, otherLoc, node.broadcast, settings)
      for (const proposal of otherRaw.result.proposals) {
        for (const reachKey of proposal.reachKeys) {
          claims.set(reachKey, [...(claims.get(reachKey) || []), pathId])
        }
      }
    }
  }
  if (claims.size > 0) {
    const kept = []
    for (const proposal of plan.proposals) {
      const claimants = [...new Set(proposal.reachKeys.flatMap((reachKey) => claims.get(reachKey) || []))]
      if (claimants.length === 0) {
        kept.push(proposal)
        continue
      }
      plan.conflicts.push({
        output: proposal.output,
        key: proposal.key,
        targetPathId: proposal.targetPathId,
        inputName: proposal.inputName,
        targetTitle: proposal.targetTitle,
        claimants,
        reason:
          `${claimants.length + 1} multipliers could feed this input, so none of them does — ` +
          'switch this output off on one of them, or wire it by hand',
        proposal
      })
    }
    plan.proposals = kept
  }
  return plan
}

/** One human line for a proposal: "model → KSampler #12 (model)". Pure so the
 * dialog wording is testable. */
export function describeProposal(proposal) {
  const reaches = Array.isArray(proposal.reaches) ? proposal.reaches : []
  if (proposal.kind === KINDS.DIRECT) {
    return `${proposal.output} → ${proposal.targetTitle || 'node'} #${proposal.targetPathId} (${proposal.inputName})`
  }
  const into = reaches
    .slice(0, 3)
    .map((reach) => `${reach.title || 'node'} #${reach.pathId}`)
    .join(', ')
  const more = reaches.length > 3 ? ` (+${reaches.length - 3} more)` : ''
  if (proposal.kind === KINDS.EXISTING) {
    return (
      `${proposal.output} → ${proposal.targetTitle || 'subgraph'} #${proposal.targetPathId} ` +
      `(its "${proposal.inputName}" input) → ${into}${more}`
    )
  }
  const via = proposal.reuse ? `its "${proposal.inputName}" input` : `a NEW "${proposal.inputName}" input`
  return (
    `${proposal.output} → ${proposal.definition?.name || proposal.targetTitle || 'subgraph'} ` +
    `(${via} on the subgraph) → ${into}${more}`
  )
}

// ---------------------------------------------------------------------------
// Records: verification, reconcile and the link index (all pure)
// ---------------------------------------------------------------------------

/**
 * Whether one recorded link still exists and still comes from where the
 * record says. `mloc` is the multiplier's `locate()` result; for a
 * multiplier-origin record the link must live in the multiplier's own graph,
 * leave the multiplier's node at its recorded output slot, and land on the
 * recorded input. A subgraph-origin record must leave definition input `s`.
 * Returns `{exists, ok, linkId, nodeExists, graphId}`.
 */
export function verifyLinkRecord(snapshot, mloc, record, outputName) {
  const graph = snapshot?.graphs?.[record.g]
  const node = graph?.nodes?.[record.n]
  const out = { exists: false, ok: false, linkId: null, nodeExists: Boolean(node), graphId: record.g }
  if (!node) return out
  const input = inputByName(node, record.i)
  const link = input?.link
  if (!link) return out
  out.exists = true
  if (record.o.m !== undefined) {
    const spec = OUTPUT_BY_NAME[record.o.m] || OUTPUT_BY_NAME[outputName]
    const fromMultiplier =
      record.g === mloc.gid &&
      String(link.originId) === String(mloc.nodeId) &&
      spec !== undefined &&
      link.originSlot === spec.index
    out.ok = fromMultiplier
  } else {
    const slot = (graph.inputs || [])[link.originSlot]
    out.ok = String(link.originId) === SUBGRAPH_INPUT_ID && slot?.id === record.o.s
  }
  out.linkId = out.ok ? (link.id ?? null) : null
  return out
}

/**
 * Reconciles *cfg*'s recorded wires against *snapshot*. `manual: true` is the
 * in-session pass (a recorded wire the USER removed or replaced becomes
 * "leave alone"); `manual: false` is load / paste (a record whose wire no
 * longer comes from this multiplier is simply dropped -- FORMAT.md §6.10).
 * Withdrawn entries (live-output tracking took their wires off on purpose)
 * are kept as long as their targets still exist. Returns
 * `{config, dropped: string[], leftAlone: string[], changed: boolean}`.
 */
export function reconcileConfig(snapshot, multiplierPathId, cfgIn, { manual = false } = {}) {
  const cfg = normalizeConfig(cfgIn)
  const mloc = locate(snapshot, multiplierPathId)
  const dropped = []
  const leftAlone = []
  if (!mloc) return { config: cfg, dropped, leftAlone, changed: false }
  const wired = []
  for (const entry of cfg.wired) {
    const checks = entry.links.map((record) => verifyLinkRecord(snapshot, mloc, record, entry.out))
    const targetsExist = checks.length > 0 && checks.every((check) => check.nodeExists)
    const okCount = checks.filter((check) => check.ok).length
    if (entry.withdrawn) {
      // Our own doing: the outer wires are gone ON PURPOSE. Keep while every
      // recorded target is still on the canvas.
      if (targetsExist) wired.push(entry)
      else dropped.push(entry.key)
      continue
    }
    if (checks.length > 0 && okCount === checks.length) {
      wired.push(entry)
      continue
    }
    if (!targetsExist) {
      dropped.push(entry.key)
      continue
    }
    if (manual) leftAlone.push(entry.key)
    if (okCount === 0) {
      dropped.push(entry.key)
      continue
    }
    wired.push({ ...entry, links: entry.links.filter((_, i) => checks[i].ok) })
  }
  // `made` records pointing at definition inputs that no longer exist go.
  const cleaned = wired.map((entry) => ({
    ...entry,
    made: entry.made.filter((made) => (snapshot.graphs[made.g]?.inputs || []).some((i) => i.id === made.id))
  }))
  const skip = [...new Set([...cfg.skip, ...leftAlone])]
  const next = { ...cfg, wired: cleaned, skip }
  const changed = JSON.stringify(next) !== JSON.stringify(cfg)
  return { config: next, dropped, leftAlone, changed }
}

/**
 * The negative-prompt guard, enforced CONTINUOUSLY (v1.3.0 rig finding,
 * 2026-10-03): the guard can only see a `negative` link that EXISTS, and a
 * freshly added CLIP Text Encode has none yet -- so Keep wired (or Wire now,
 * before the user finished wiring) can legitimately feed its `text`, and the
 * very next thing the user does is wire it into a sampler's `negative`. This
 * returns the keys of every RECORDED `text` entry whose target node now feeds
 * an input named `negative`; the live half withdraws those wires and leaves the
 * inputs alone, so the multiplier's prompt never overwrites a negative one.
 * Pure. @param {object} snapshot @param {object} config @returns {string[]}
 */
export function negativeFedTextKeys(snapshot, config) {
  const keys = []
  for (const entry of config?.wired || []) {
    if (entry?.out !== 'text') continue
    const loc = locate(snapshot, entry.to)
    if (loc && feedsNegative(loc.node)) keys.push(entry.key)
  }
  return keys
}

/**
 * The broadcast link OWNERS for the rendering stage (FORMAT.md §6.10 "Tucked
 * wires"): `Map<graphKey, Map<linkId, {ownerId, ownerGraph, look, linkGraph}>>` over
 * EVERY multiplier in the snapshot -- the outer links in the multiplier's graph AND
 * the inner links this feature created inside subgraph definitions (graphKey
 * 'root' or the definition's uuid). `ownerId` is the multiplier's node id in
 * ITS graph `ownerGraph` (a node id is only unique per graph, so the renderer
 * must compare both before treating "the multiplier is selected" as true);
 * `look` is that multiplier's `look` (tucked | dim | normal); `linkGraph` is
 * the key the link itself lives under (so a renderer can tell whether the
 * multiplier sits in the graph it is drawing without re-deriving the key). Only links that
 * still verify against their record are included, so a user's own wire onto
 * the same input is never mislabelled. Withdrawn entries have no outer wire.
 */
export function buildLinkOwners(snapshot) {
  const owners = new Map()
  const add = (gid, linkId, owner) => {
    if (linkId == null) return
    const map = owners.get(gid) || new Map()
    map.set(linkId, owner)
    owners.set(gid, map)
  }
  for (const ctx of allContexts(snapshot)) {
    for (const node of nodesOf(snapshot.graphs[ctx.gid])) {
      if (node.classType !== MULTIPLIER_CLASS_ID || !node.broadcast) continue
      const mloc = locate(snapshot, joinPath(ctx.prefix, node.id))
      if (!mloc) continue
      const cfg = normalizeConfig(node.broadcast)
      for (const entry of cfg.wired) {
        if (entry.withdrawn) continue
        for (const record of entry.links) {
          const check = verifyLinkRecord(snapshot, mloc, record, entry.out)
          if (check.ok) {
            add(record.g, check.linkId, {
              ownerId: String(node.id),
              ownerGraph: ctx.gid,
              look: cfg.look,
              linkGraph: record.g
            })
          }
        }
      }
    }
  }
  return owners
}

/**
 * The broadcast link index (FORMAT.md §6.10): `Map<graphKey, Set<linkId>>`,
 * the id-only view of `buildLinkOwners` (the shape `isBroadcastLink` and the
 * v1.3.0 consumers already use).
 */
export function buildLinkIndex(snapshot) {
  const index = new Map()
  for (const [gid, map] of buildLinkOwners(snapshot)) index.set(gid, new Set(map.keys()))
  return index
}
