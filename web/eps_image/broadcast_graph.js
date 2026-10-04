/**
 * @file EPS Run Multiplier "broadcast" -- the LIVE half (FORMAT.md §6.10
 * "Broadcast (v1)"): the snapshot adapter, the applier, "Remove broadcast
 * wires", live-output withdraw/restore, record reconcile, the one-undo-step
 * wrapper and the exported link-index API for the rendering stage. The pure
 * decisions live in `broadcast_plan.js`; the DOM lives in `broadcast_ui.js`;
 * `broadcast.js` wires it all to the node.
 *
 * **Only core primitives, only on the live graph.** Every wire is made with
 * core `node.connect(...)` (the call EPS Bypass and the Number Controller
 * already use daily) and every inner link with core `SubgraphInput.connect(
 * slot, node)` -- the exact object litegraph itself uses when you drag from a
 * subgraph's input panel to an inner node. Nothing here replaces a link
 * (`connect` would quietly disconnect an occupied input, so every step
 * re-checks emptiness first), draws, polls or patches `graphToPrompt`: the
 * wires are REAL (owner decision 2026-10-03), so execution, save, undo, the
 * run-count readout and every other graph-reading feature need no changes.
 *
 * **Shared code, deliberately imported (ROADMAP-shared-panel-code.md).**
 * `inputVerdict` (required / optional / widget per input) is imported from
 * bypass.js, never copied: it is the one place the pack decides "is
 * unplugging/filling this input safe", and a hand copy is exactly the
 * duplicated-helper hazard this pack has shipped three fixes to miss
 * siblings of. The adapter folds its verdict into the snapshot so the planner
 * stays litegraph-free. The graph walkers (`walkGraphs`, `walkLiveNodes`) are
 * lora_library/api.js's, unchanged.
 *
 * **Undo (rig-UNCONFIRMED; derived from the 1.52.7 source and bundle).**
 * `scripts/changeTracker.ts` is SNAPSHOT based: `captureCanvasState()`
 * serialises the whole root graph and pushes the previous state when it
 * differs. It runs on mouseup / keyup / `promptQueued` / canvas pointer-up --
 * and NOT from `node.connect`: `LGraph.beforeChange()/afterChange()` only
 * call the optional `canvas.onBeforeChange/onAfterChange` hooks, which core
 * never assigns, so a programmatic batch is captured at the NEXT user event
 * (folded into whatever that event is). The transaction API the canvas itself
 * uses is `canvas.emitBeforeChange()/emitAfterChange()`, which dispatch the
 * `litegraph:canvas` DOM event the tracker's document listener turns into
 * `changeCount++` / `--changeCount || captureCanvasState()`. So the batch is
 * wrapped: commit anything pending (`captureCanvasState()`), open a
 * transaction (capture suppressed while `changeCount > 0`), do the work,
 * close it (ONE capture). One Ctrl+Z then reloads the pre-batch state
 * (`app.loadGraphData`, which also restores subgraph DEFINITIONS and
 * `node.properties.Broadcast` -- both are part of the serialised state).
 */

import { app } from '../../../scripts/app.js'
import { nodesOfGraph, walkGraphs, walkLiveNodes } from '../lora_library/api.js'
import { inputVerdict } from './bypass.js'
import {
  MULTIPLIER_CLASS_ID,
  OUTPUT_BY_NAME,
  PROPERTY_KEY,
  ROOT_GRAPH_ID,
  SUBGRAPH_INPUT_ID,
  buildLinkIndex,
  negativeFedTextKeys,
  normalizeConfig,
  planBroadcast,
  reconcileConfig,
  serializeConfig
} from './broadcast_plan.js'

const PREFIX = '[eps_image:broadcast]'

// ---------------------------------------------------------------------------
// Own-change guard: our connects/disconnects fire the same hooks a user's do.
// ---------------------------------------------------------------------------

let applyingDepth = 0

/** True while THIS module is mutating the graph. The graph watch and the
 * connection hooks check it so our own disconnects are never mistaken for a
 * user's manual unplug ("leave alone"), and our own connects never re-plan. */
export function isApplying() {
  return applyingDepth > 0
}

function guarded(fn) {
  applyingDepth += 1
  try {
    return fn()
  } finally {
    applyingDepth -= 1
  }
}

// ---------------------------------------------------------------------------
// Small live-graph helpers
// ---------------------------------------------------------------------------

function nodeClassOf(node) {
  if (!node) return null
  if (node.comfyClass) return node.comfyClass
  if (node.constructor && node.constructor.comfyClass) return node.constructor.comfyClass
  return null
}

/** An LLink by id, tolerant of `graph.links` being a Map-proxy or a plain
 * object (cross_sweep.js's / bypass.js's identical lookup). */
function linkById(graph, linkId) {
  if (linkId == null || !graph) return null
  return graph.links?.get?.(linkId) ?? graph.links?.[linkId] ?? graph._links?.get?.(linkId) ?? null
}

/** Graph key used by snapshots and records: 'root' for the root graph, the
 * definition's uuid for a subgraph. */
export function graphKeyOf(rootGraph, graph) {
  return graph === rootGraph ? ROOT_GRAPH_ID : String(graph?.id)
}

/** The live graph for *key*, or null (a definition deleted since). */
export function graphByKey(rootGraph, key) {
  if (key === ROOT_GRAPH_ID) return rootGraph
  const fromMap = rootGraph?.subgraphs?.get?.(key)
  if (fromMap) return fromMap
  return walkGraphs(rootGraph).find((graph) => graph !== rootGraph && String(graph.id) === key) || null
}

/** The root graph a node lives under. */
export function rootGraphOf(node) {
  const graph = node?.graph
  return graph?.rootGraph || graph || app?.graph || null
}

/** The execution-id style pathId of *node* ("3:5"), the first one found. */
export function pathIdOf(rootGraph, node) {
  for (const entry of walkLiveNodes(rootGraph)) if (entry.node === node) return entry.pathId
  return null
}

function findInputIndex(node, name) {
  return (node?.inputs || []).findIndex((input) => input?.name === name)
}

function isSubgraphInstance(node) {
  return Boolean(node?.subgraph && node.subgraph.inputNode !== undefined && Array.isArray(node.subgraph.inputs))
}

/** The definition-input index an instance input maps to (the instance's
 * `_subgraphSlot` is the definition's own SubgraphInput; array position is
 * the fallback -- the instance's inputs are built in definition order). */
function definitionIndexOf(subgraph, input, fallbackIndex) {
  const slot = input?._subgraphSlot
  const index = slot ? subgraph.inputs.indexOf(slot) : -1
  return index >= 0 ? index : fallbackIndex
}

// ---------------------------------------------------------------------------
// Records on the multiplier
// ---------------------------------------------------------------------------

/** The multiplier's normalised config (never throws). */
export function readConfig(node) {
  return normalizeConfig(node?.properties?.[PROPERTY_KEY])
}

/** Stores *cfg* on the multiplier. An all-default config REMOVES the
 * property so a node that never used broadcast stays byte-identical in the
 * workflow file (old workflows load and save exactly as before). */
export function writeConfig(node, cfg) {
  const serial = serializeConfig(cfg)
  if (!node.properties) node.properties = {}
  if (serial === null) delete node.properties[PROPERTY_KEY]
  else node.properties[PROPERTY_KEY] = serial
}

// ---------------------------------------------------------------------------
// Snapshot adapter (the planner's only bridge to litegraph)
// ---------------------------------------------------------------------------

function snapshotNode(graph, node) {
  const classType = nodeClassOf(node) || node.type || ''
  const instance = isSubgraphInstance(node)
  const inputs = (node.inputs || []).map((input, index) => {
    const link = linkById(graph, input?.link)
    const entry = {
      name: typeof input?.name === 'string' ? input.name : '',
      type: typeof input?.type === 'string' ? input.type : '',
      widget: Boolean(input?.widget),
      // bypass.js's verdict, folded in so the planner stays litegraph-free.
      // A SubgraphNode instance's own verdict is never read (its leaves are).
      verdict: instance ? 'unknown' : inputVerdict(node, input).why,
      link: link
        ? { id: link.id, originId: String(link.origin_id), originSlot: link.origin_slot }
        : null
    }
    if (instance) entry.defIndex = definitionIndexOf(node.subgraph, input, index)
    return entry
  })
  const outputs = (node.outputs || []).map((output) => ({
    name: typeof output?.name === 'string' ? output.name : '',
    type: typeof output?.type === 'string' ? output.type : '',
    links: (Array.isArray(output?.links) ? output.links : [])
      .map((linkId) => {
        const link = linkById(graph, linkId)
        if (!link) return null
        const target = graph.getNodeById?.(link.target_id)
        return {
          targetId: String(link.target_id),
          targetInput: target?.inputs?.[link.target_slot]?.name
        }
      })
      .filter(Boolean)
  }))
  return {
    id: String(node.id),
    classType,
    title: node.title || '',
    mode: node.mode ?? 0,
    subgraphId: instance ? String(node.subgraph.id) : null,
    broadcast: classType === MULTIPLIER_CLASS_ID ? (node.properties?.[PROPERTY_KEY] ?? null) : null,
    inputs,
    outputs
  }
}

function snapshotGraph(rootGraph, graph) {
  const key = graphKeyOf(rootGraph, graph)
  const nodes = {}
  // Per graph, driven by walkGraphs (snapshot keys each graph separately):
  // api.nodesOfGraph is the one sanctioned single-graph accessor (v1.3.0).
  for (const node of nodesOfGraph(graph)) {
    if (!node || node.id == null) continue
    nodes[String(node.id)] = snapshotNode(graph, node)
  }
  const out = { id: key, name: graph.name || '', nodes, inputs: [], outputs: [] }
  if (graph !== rootGraph) {
    out.inputs = (graph.inputs || []).map((slot) => ({
      id: String(slot.id),
      name: slot.name,
      type: typeof slot.type === 'string' ? slot.type : ''
    }))
    out.outputs = (graph.outputs || []).map((slot) => {
      const link = linkById(graph, slot.linkIds?.[0])
      return {
        name: slot.name,
        type: typeof slot.type === 'string' ? slot.type : '',
        origin: link ? { originId: String(link.origin_id), originSlot: link.origin_slot } : null
      }
    })
  }
  return out
}

/**
 * A PLAIN snapshot of *rootGraph* and every reachable subgraph definition in
 * the planner's shape (broadcast_plan.js header). Each definition appears
 * ONCE (`walkGraphs` dedupes by identity), however many instances exist.
 * @param {object} rootGraph @returns {{graphs: Record<string, object>}}
 */
export function snapshotFromRoot(rootGraph) {
  const graphs = {}
  for (const graph of walkGraphs(rootGraph)) {
    const snap = snapshotGraph(rootGraph, graph)
    graphs[snap.id] = snap
  }
  return { graphs }
}

/**
 * Snapshot + plan for the multiplier *mnode*. Returns `{plan, snapshot,
 * pathId, config}`; `plan.error` is set when the node can't be located.
 * @param {object} mnode @param {{exactNames?: boolean}} settings
 */
export function planFor(mnode, settings) {
  const rootGraph = rootGraphOf(mnode)
  const snapshot = snapshotFromRoot(rootGraph)
  const pathId = pathIdOf(rootGraph, mnode)
  const config = readConfig(mnode)
  const plan =
    pathId === null
      ? { multiplier: '', proposals: [], skips: [], conflicts: [], outputs: [], error: 'multiplier not found in the graph' }
      : planBroadcast(snapshot, pathId, config, settings)
  return { plan, snapshot, pathId, config }
}

// ---------------------------------------------------------------------------
// One undo step for a whole batch
// ---------------------------------------------------------------------------

/** The active workflow's ChangeTracker (frontend 1.52.7:
 * `useWorkflowStore().activeWorkflow.changeTracker`, exposed to extensions as
 * `app.extensionManager.workflow`). Null when unreachable. */
function activeChangeTracker() {
  try {
    return app.extensionManager?.workflow?.activeWorkflow?.changeTracker ?? null
  } catch {
    return null
  }
}

/**
 * Runs *fn* as ONE undo step (see the file header: the investigation this is
 * built on). Commits pending state first so earlier edits are not folded into
 * the batch, opens a canvas change transaction, runs, and closes it (one
 * capture). Falls back to explicit `captureCanvasState()` before/after when
 * the transaction API is absent. Never swallows *fn*'s error.
 * @template T @param {() => T} fn @returns {T}
 */
export function runAsOneUndoStep(fn) {
  const tracker = activeChangeTracker()
  try {
    tracker?.captureCanvasState?.()
  } catch (error) {
    console.warn(PREFIX, 'could not commit pending state before the batch', error)
  }
  const canvas = app.canvas
  const transaction =
    typeof canvas?.emitBeforeChange === 'function' && typeof canvas?.emitAfterChange === 'function'
  if (transaction) canvas.emitBeforeChange()
  try {
    return fn()
  } finally {
    try {
      if (transaction) canvas.emitAfterChange()
      else tracker?.captureCanvasState?.()
    } catch (error) {
      console.warn(PREFIX, 'could not close the undo transaction', error)
    }
  }
}

// ---------------------------------------------------------------------------
// Link index for the rendering stage (FORMAT.md §6.10 "Broadcast link index")
// ---------------------------------------------------------------------------

let indexEpoch = 0
const indexCache = new WeakMap()

/** Invalidates the cached link index. Called by this module after every
 * apply / remove / withdraw / restore / reconcile, by broadcast.js on
 * configure and node removal; the rendering stage may call it too. */
export function bumpBroadcastEpoch() {
  indexEpoch += 1
}

/**
 * `Map<graphKey, Set<linkId>>` over every multiplier's recorded wires that
 * STILL verify (a user's own wire onto the same input is never included):
 * the multiplier's outer links in its graph AND the inner links this feature
 * created inside subgraph definitions. graphKey is 'root' or the
 * definition's uuid. Cached per root graph until `bumpBroadcastEpoch()`.
 * @param {object} rootGraph @returns {Map<string, Set<number>>}
 */
export function broadcastLinkIndex(rootGraph) {
  if (!rootGraph) return new Map()
  const cached = indexCache.get(rootGraph)
  if (cached && cached.epoch === indexEpoch) return cached.index
  let index
  try {
    index = buildLinkIndex(snapshotFromRoot(rootGraph))
  } catch (error) {
    console.warn(PREFIX, 'link index failed', error)
    index = new Map()
  }
  indexCache.set(rootGraph, { epoch: indexEpoch, index })
  return index
}

/** Whether link *linkId* of *graph* (root or subgraph) is a broadcast wire.
 * O(1) once the index is cached -- safe to call per link per frame. */
export function isBroadcastLink(graph, linkId) {
  if (!graph || linkId == null) return false
  const root = graph.rootGraph || graph
  return broadcastLinkIndex(root).get(graphKeyOf(root, graph))?.has(linkId) === true
}

// ---------------------------------------------------------------------------
// Applying proposals
// ---------------------------------------------------------------------------

/** Verifies that the link on `node.inputs[slotIndex]` is the one we just made
 * from the expected origin; returns the LLink or null. */
function verifyMadeLink(graph, node, slotIndex, link, expectedOrigin) {
  if (!link) return null
  if (node.inputs?.[slotIndex]?.link !== link.id) return null
  const live = linkById(graph, link.id)
  if (!live) return null
  if (expectedOrigin.kind === 'multiplier') {
    if (String(live.origin_id) !== String(expectedOrigin.nodeId) || live.origin_slot !== expectedOrigin.slot) return null
  } else if (String(live.origin_id) !== SUBGRAPH_INPUT_ID || live.origin_slot !== expectedOrigin.slot) {
    // `String()`: 1.52.7's NodeId is a branded STRING ('-10'); older
    // frontends carried the number -10. Compare as strings, always.
    return null
  }
  return live
}

/** Disconnects `node.inputs[slotIndex]` -- ours only. */
function disconnectSlot(node, slotIndex) {
  try {
    node.disconnectInput(slotIndex)
  } catch (error) {
    console.warn(PREFIX, 'disconnectInput failed', error)
  }
}

/** Removes a subgraph input after severing its inner links one by one:
 * `SubgraphSlot.disconnect()` iterates `linkIds` while each removal splices
 * it, which skips every other link, so we empty the list ourselves first. */
function removeDefinitionInput(subgraph, input) {
  for (const linkId of [...(input.linkIds || [])]) {
    const link = linkById(subgraph, linkId)
    const target = link ? subgraph.getNodeById?.(link.target_id) : null
    if (target) disconnectSlot(target, link.target_slot)
  }
  if ((input.linkIds || []).length > 0) return false
  try {
    subgraph.removeInput(input)
    return true
  } catch (error) {
    console.warn(PREFIX, 'removeInput failed', error)
    return false
  }
}

/** Undoes what one proposal had done so far (journal in creation order). */
function rollback(rootGraph, journal) {
  for (const rec of [...journal.links].reverse()) removeLinkRecord(rootGraph, journal.mnode, rec, rec.out)
  for (const made of [...journal.made].reverse()) {
    const graph = graphByKey(rootGraph, made.g)
    const input = graph?.inputs?.find((slot) => String(slot.id) === made.id)
    if (input) removeDefinitionInput(graph, input)
  }
}

/**
 * Applies ONE proposal's steps in order. Never replaces a link: every target
 * input is re-checked empty first (the graph may have changed since the
 * preview). Any failure rolls the proposal back and returns `{ok: false}`.
 */
function applyOne(rootGraph, mnode, proposal) {
  const journal = { mnode, links: [], made: [] }
  const refs = new Map()
  const fail = (reason) => {
    guarded(() => rollback(rootGraph, journal))
    return { ok: false, reason }
  }
  try {
    for (const step of proposal.steps) {
      const graph = graphByKey(rootGraph, step.graph)
      if (!graph) return fail('a subgraph it needed no longer exists')
      if (step.op === 'add-input') {
        if ((graph.inputs || []).some((slot) => slot.name === step.name)) {
          return fail(`the subgraph already has an input called "${step.name}" — plan again`)
        }
        const input = graph.addInput(step.name, step.valueType)
        refs.set(step.ref, { graph, input })
        journal.made.push({ g: step.graph, id: String(input.id), name: input.name, type: step.valueType })
        continue
      }
      const target = graph.getNodeById?.(step.to.node)
      const slotIndex = findInputIndex(target, step.to.input)
      if (!target || slotIndex < 0) return fail(`input "${step.to.input}" is gone`)
      if (target.inputs[slotIndex].link != null) return fail(`"${step.to.input}" is no longer empty — never replaced`)

      let link = null
      let origin
      let recordOrigin
      if (step.from.m !== undefined) {
        const spec = OUTPUT_BY_NAME[step.from.m]
        if (!spec || target.graph !== mnode.graph) return fail('the multiplier is not in the same graph as the target')
        link = mnode.connect(spec.index, target, slotIndex)
        origin = { kind: 'multiplier', nodeId: mnode.id, slot: spec.index }
        recordOrigin = { m: step.from.m }
      } else {
        const sub = step.from.ref ? refs.get(step.from.ref)?.input : graph.inputs?.find((slot) => String(slot.id) === step.from.sub)
        if (!sub) return fail('a subgraph input it needed no longer exists')
        link = sub.connect(target.inputs[slotIndex], target) || null
        origin = { kind: 'subgraph', slot: graph.inputs.indexOf(sub) }
        recordOrigin = { s: String(sub.id) }
      }
      const verified = verifyMadeLink(graph, target, slotIndex, link, origin)
      if (!verified) {
        // A veto (onConnectInput), a type refusal, or a foreign node that
        // redirected the slot: undo whatever half-landed and say so.
        if (link && target.inputs?.[slotIndex]?.link === link.id) disconnectSlot(target, slotIndex)
        return fail(`ComfyUI refused the connection into "${step.to.input}"`)
      }
      journal.links.push({ g: step.graph, n: String(step.to.node), i: step.to.input, o: recordOrigin, out: proposal.output })
    }
  } catch (error) {
    console.warn(PREFIX, 'applying a proposal failed', error)
    return fail(error?.message || 'unexpected error')
  }
  return {
    ok: true,
    links: journal.links.map(({ g, n, i, o }) => ({ g, n, i, o })),
    made: journal.made
  }
}

function mergeWiredEntry(cfg, proposal, result) {
  const existing = cfg.wired.find((entry) => entry.key === proposal.key)
  const links = [...(existing?.links || [])]
  for (const rec of result.links) {
    if (!links.some((l) => l.g === rec.g && l.n === rec.n && l.i === rec.i)) links.push(rec)
  }
  const made = [...(existing?.made || []), ...result.made]
  const entry = {
    key: proposal.key,
    out: proposal.output,
    kind: proposal.kind,
    to: proposal.targetPathId,
    input: proposal.inputName,
    links,
    made
  }
  cfg.wired = [...cfg.wired.filter((e) => e.key !== proposal.key), entry]
}

/**
 * Applies *proposals* as ONE undo step and records them on the multiplier.
 * `leaveAlone` keys are added to the multiplier's leave-alone list (the
 * dialog's UNTICKED rows); `include` keys are removed from it (a previously
 * left-alone row the user ticked). Returns `{applied: [{key, links, made}],
 * failed: [{key, reason}]}`.
 * @param {object} rootGraph @param {object} mnode @param {object[]} proposals
 * @param {{leaveAlone?: string[], include?: string[]}} [options]
 */
export function applyProposals(rootGraph, mnode, proposals, { leaveAlone = [], include = [] } = {}) {
  const outcome = { applied: [], failed: [] }
  if (!proposals.length && !leaveAlone.length && !include.length) return outcome
  guarded(() => {
    runAsOneUndoStep(() => {
      const cfg = readConfig(mnode)
      for (const proposal of proposals) {
        const result = applyOne(rootGraph, mnode, proposal)
        if (result.ok) {
          mergeWiredEntry(cfg, proposal, result)
          outcome.applied.push({ key: proposal.key, output: proposal.output, links: result.links.length, made: result.made.length })
        } else {
          outcome.failed.push({ key: proposal.key, output: proposal.output, reason: result.reason })
        }
      }
      const appliedKeys = new Set(outcome.applied.map((a) => a.key))
      cfg.skip = [...new Set([...cfg.skip, ...leaveAlone])].filter(
        (key) => !include.includes(key) && !appliedKeys.has(key)
      )
      writeConfig(mnode, cfg)
    })
  })
  bumpBroadcastEpoch()
  mnode.graph?.setDirtyCanvas?.(true, true)
  return outcome
}

// ---------------------------------------------------------------------------
// Removing / withdrawing / restoring
// ---------------------------------------------------------------------------

/**
 * Removes ONE recorded link if (and only if) it still comes from where the
 * record says. Returns true when a wire was actually removed.
 */
function removeLinkRecord(rootGraph, mnode, rec, outputName) {
  const graph = graphByKey(rootGraph, rec.g)
  const node = graph?.getNodeById?.(rec.n)
  const slotIndex = findInputIndex(node, rec.i)
  if (!node || slotIndex < 0) return false
  const link = linkById(graph, node.inputs[slotIndex].link)
  if (!link) return false
  if (rec.o.m !== undefined) {
    const spec = OUTPUT_BY_NAME[rec.o.m] || OUTPUT_BY_NAME[outputName]
    const ours =
      graph === mnode.graph && String(link.origin_id) === String(mnode.id) && spec && link.origin_slot === spec.index
    if (!ours) return false
  } else {
    const slot = graph.inputs?.[link.origin_slot]
    if (String(link.origin_id) !== SUBGRAPH_INPUT_ID || !slot || String(slot.id) !== rec.o.s) return false
  }
  disconnectSlot(node, slotIndex)
  return node.inputs[slotIndex].link == null
}

/** Whether any instance of *subgraph* has a live wire on the slot that
 * mirrors *input* (a wire we did not record: the user's own). */
function instanceSlotInUse(rootGraph, subgraph, input) {
  for (const { node } of walkLiveNodes(rootGraph)) {
    if (node.subgraph !== subgraph) continue
    const slot = (node.inputs || []).find((candidate) => candidate._subgraphSlot === input)
    if (slot && slot.link != null) return true
  }
  return false
}

/**
 * "Remove broadcast wires": deletes ONLY the wires the multiplier recorded
 * that still run from it (inner links first, outer wires last), and the
 * subgraph inputs it created -- but only when nothing else uses them. One
 * undo step. Returns `{removed, keptInputs: [name...]}`.
 * @param {object} rootGraph @param {object} mnode
 */
/** Disconnects one recorded entry's wires (innermost last-made first) and
 * deletes the subgraph inputs it made once nothing else uses them. Shared by
 * "Remove broadcast wires" and the continuous negative-prompt guard. */
function removeEntryWires(rootGraph, mnode, entry, result) {
  for (const rec of [...entry.links].reverse()) {
    if (removeLinkRecord(rootGraph, mnode, rec, entry.out)) result.removed += 1
  }
  for (const made of [...entry.made].reverse()) {
    const graph = graphByKey(rootGraph, made.g)
    const input = graph?.inputs?.find((slot) => String(slot.id) === made.id)
    if (!input) continue
    if ((input.linkIds || []).length > 0 || instanceSlotInUse(rootGraph, graph, input)) {
      result.keptInputs.push(made.name)
      continue
    }
    if (removeDefinitionInput(graph, input)) result.removedInputs += 1
    else result.keptInputs.push(made.name)
  }
}

export function removeBroadcastWires(rootGraph, mnode) {
  const result = { removed: 0, keptInputs: [], removedInputs: 0 }
  guarded(() => {
    runAsOneUndoStep(() => {
      const cfg = readConfig(mnode)
      for (const entry of [...cfg.wired].reverse()) removeEntryWires(rootGraph, mnode, entry, result)
      cfg.wired = []
      writeConfig(mnode, cfg)
    })
  })
  bumpBroadcastEpoch()
  mnode.graph?.setDirtyCanvas?.(true, true)
  return result
}

/**
 * The continuous negative-prompt guard (v1.3.0, see
 * broadcast_plan.js `negativeFedTextKeys`): withdraws every recorded `text`
 * wire whose encoder now feeds a `negative` input and puts those inputs on
 * the leave-alone list, so neither Keep wired nor Wire now re-offers them.
 * Deliberately NOT its own undo step: it is a consequence of the user's own
 * edit (wiring the encoder into `negative`), so the change tracker folds it
 * into that edit and one Ctrl+Z restores both together -- a separate step
 * would let Ctrl+Z bring the text wire back only for this guard to remove it
 * again. Guarded, so the watch never reads it as a manual unplug.
 * @returns {string[]} the withdrawn entries' target path ids
 */
export function enforceNegativeGuard(rootGraph, mnode, snapshot) {
  const cfg = readConfig(mnode)
  const keys = new Set(negativeFedTextKeys(snapshot, cfg))
  if (keys.size === 0) return []
  const result = { removed: 0, keptInputs: [], removedInputs: 0 }
  const withdrawn = []
  guarded(() => {
    for (const entry of cfg.wired.filter((e) => keys.has(e.key))) {
      removeEntryWires(rootGraph, mnode, entry, result)
      withdrawn.push(entry.to)
    }
    cfg.wired = cfg.wired.filter((e) => !keys.has(e.key))
    cfg.skip = [...new Set([...cfg.skip, ...keys])]
    writeConfig(mnode, cfg)
  })
  bumpBroadcastEpoch()
  mnode.graph?.setDirtyCanvas?.(true, true)
  return withdrawn
}

/**
 * Live-output tracking (S3): the multiplier's *outputName* just went dead
 * (its backing input was unwired). Disconnects the OUTER wires (those that
 * leave the multiplier itself) of every recorded entry for that output and
 * marks the entries withdrawn -- inner subgraph links stay. Guarded, so the
 * graph watch never reads it as a manual unplug. Returns the count.
 */
export function withdrawOutputWires(rootGraph, mnode, outputName) {
  let count = 0
  guarded(() => {
    runAsOneUndoStep(() => {
      const cfg = readConfig(mnode)
      for (const entry of cfg.wired) {
        if (entry.out !== outputName || entry.withdrawn) continue
        for (const rec of entry.links) {
          if (rec.o.m === undefined) continue
          if (removeLinkRecord(rootGraph, mnode, rec, entry.out)) count += 1
        }
        entry.withdrawn = true
      }
      writeConfig(mnode, cfg)
    })
  })
  bumpBroadcastEpoch()
  return count
}

/**
 * The output is live again: reconnects the withdrawn entries' outer wires
 * (never stomping an input that has since been wired by hand -- that record
 * is dropped instead). Returns `{restored, dropped}`.
 */
export function restoreOutputWires(rootGraph, mnode, outputName) {
  const result = { restored: 0, dropped: 0 }
  guarded(() => {
    runAsOneUndoStep(() => {
      const cfg = readConfig(mnode)
      const kept = []
      for (const entry of cfg.wired) {
        if (entry.out !== outputName || !entry.withdrawn) {
          kept.push(entry)
          continue
        }
        const links = []
        for (const rec of entry.links) {
          if (rec.o.m === undefined) {
            links.push(rec)
            continue
          }
          const graph = graphByKey(rootGraph, rec.g)
          const node = graph?.getNodeById?.(rec.n)
          const slotIndex = findInputIndex(node, rec.i)
          const spec = OUTPUT_BY_NAME[rec.o.m]
          if (!node || slotIndex < 0 || !spec || node.inputs[slotIndex].link != null || graph !== mnode.graph) {
            result.dropped += 1
            continue
          }
          const link = mnode.connect(spec.index, node, slotIndex)
          if (verifyMadeLink(graph, node, slotIndex, link, { kind: 'multiplier', nodeId: mnode.id, slot: spec.index })) {
            links.push(rec)
            result.restored += 1
          } else {
            result.dropped += 1
          }
        }
        if (links.some((rec) => rec.o.m !== undefined)) {
          const { withdrawn: _gone, ...rest } = entry
          kept.push({ ...rest, links })
        }
      }
      cfg.wired = kept
      writeConfig(mnode, cfg)
    })
  })
  bumpBroadcastEpoch()
  return result
}

/**
 * Reconciles the multiplier's records against the live graph (FORMAT.md
 * §6.10): `manual: false` on load / paste (a record whose wire no longer
 * comes from this multiplier is dropped), `manual: true` in session (a wire
 * the user removed or replaced becomes "leave alone"). Writes only when
 * something changed. A pass that already holds a snapshot/pathId passes them
 * in (no second walk). Returns reconcileConfig's `{dropped, leftAlone, changed}`.
 */
export function reconcileMultiplier(rootGraph, mnode, { manual = false } = {}, snapshot = null, pathId = null) {
  const path = pathId ?? pathIdOf(rootGraph, mnode)
  if (path === null) return { dropped: [], leftAlone: [], changed: false }
  const snap = snapshot ?? snapshotFromRoot(rootGraph)
  const result = reconcileConfig(snap, path, readConfig(mnode), { manual })
  if (result.changed) {
    writeConfig(mnode, result.config)
    bumpBroadcastEpoch()
  }
  return { dropped: result.dropped, leftAlone: result.leftAlone, changed: result.changed }
}

/** Every EPS Run Multiplier under *rootGraph* (subgraphs included). */
export function findMultipliers(rootGraph) {
  return walkLiveNodes(rootGraph)
    .filter((entry) => nodeClassOf(entry.node) === MULTIPLIER_CLASS_ID)
    .map((entry) => entry.node)
}
