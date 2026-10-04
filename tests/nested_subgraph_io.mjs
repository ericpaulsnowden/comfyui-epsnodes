// Fake SUBGRAPH IO for the unplug/replug tests (v1.2.0 nested reach, FORMAT.md
// section 7.10) -- used by tests/test_bypass_js.py and
// tests/test_number_controller_js.py, whose probes each carry their OWN small
// fake litegraph (a `FakeNode`/`wireLinks` pair there, a `makeFakeNode` here)
// and only need the one thing neither has: a Subgraph's input/output SLOTS.
//
// What is modelled is what ComfyUI 1.52.7's `lib/litegraph/src/subgraph/
// SubgraphOutput.ts` / `SubgraphInput.ts` do, no more:
//
//  - `Subgraph.outputs[j]` is a SubgraphOutput: `connect(slot, node)`
//    validates the type, runs the origin's `onConnectOutput` veto, REPLACES
//    an existing link (that is what "never stomp" has to guard against),
//    creates a link `{origin_id: node.id, origin_slot, target_id: -20,
//    target_slot: j}` in the SUBGRAPH's own link table, records it in
//    `linkIds` and on the node's output, and fires the node's
//    `onConnectionsChange(OUTPUT, outputIndex, true, link, slot)`.
//  - `disconnect()` removes every linked link, clears the node output's id,
//    clears `linkIds`, and fires `onConnectionsChange(OUTPUT, origin_slot,
//    false, link, <the SubgraphOutput itself>)` -- note the SLOT ARGUMENT is
//    the SubgraphOutput, not the node's own output.
//  - `Subgraph.inputs[i]` is a SubgraphInput; an inner link that leaves it has
//    `origin_id` -10 and `origin_slot` i.
//
// The graph object comes from the caller's harness (any object with `links`
// (Map), `getNodeById` and a node collection); `asSubgraph` only decorates it.

export const SUBGRAPH_INPUT_ID = -10
export const SUBGRAPH_OUTPUT_ID = -20

let nextIoLinkId = 5000

const isGeneric = (t) => t == null || t === '' || t === '*' || t === 0
const typesCompatible = (a, b) => isGeneric(a) || isGeneric(b) || String(a) === String(b)

export class FakeSubgraphOutput {
  constructor(graph, name, type = '*') {
    this.graph = graph
    this.name = name
    this.type = type
    this.linkIds = []
  }
  get isConnected() {
    return this.linkIds.length > 0
  }
  getLinks() {
    return this.linkIds.map((id) => this.graph.links.get(id)).filter(Boolean)
  }
  connect(slot, node) {
    const subgraph = this.graph
    if (!typesCompatible(slot.type, this.type)) return undefined
    const outputIndex = node.outputs.indexOf(slot)
    if (outputIndex === -1) throw new Error('Slot is not an output of the given node')
    if (node.onConnectOutput?.(outputIndex, this.type, this, { id: SUBGRAPH_OUTPUT_ID }, -1) === false) {
      return undefined
    }
    const existing = this.getLinks().at(0)
    if (existing) {
      // core evicts whatever is already linked here
      subgraph.links.delete(existing.id)
      const out = subgraph.getNodeById(existing.origin_id)?.outputs?.[existing.origin_slot]
      if (out?.links) out.links = out.links.filter((id) => id !== existing.id)
    }
    const link = {
      id: nextIoLinkId++, type: slot.type,
      origin_id: node.id, origin_slot: outputIndex,
      target_id: SUBGRAPH_OUTPUT_ID, target_slot: subgraph.outputs.indexOf(this)
    }
    subgraph.links.set(link.id, link)
    this.linkIds[0] = link.id
    ;(slot.links ??= []).push(link.id)
    node.onConnectionsChange?.(2, outputIndex, true, link, slot)
    return link
  }
  disconnect() {
    const subgraph = this.graph
    for (const linkId of this.linkIds) {
      const link = subgraph.links.get(linkId)
      if (!link) continue
      subgraph.links.delete(linkId)
      const origin = subgraph.getNodeById(link.origin_id)
      const output = origin?.outputs?.[link.origin_slot]
      if (output) output.links = output.links?.filter((id) => id !== linkId) ?? null
      origin?.onConnectionsChange?.(2, link.origin_slot, false, link, this)
    }
    this.linkIds.length = 0
  }
}

export class FakeSubgraphInput {
  constructor(name, type = '*') {
    this.name = name
    this.type = type
    this.linkIds = []
  }
}

/**
 * Turns a harness graph into a Subgraph: its own `inputs`/`outputs` slots
 * (`[{name, type}]` specs), a `rootGraph` link to the workflow root, a
 * `name`, and a `_nodes` list (the harness graphs keep nodes in a Map; the
 * pack's walkers read `_nodes`). Returns *graph*.
 */
export function asSubgraph(graph, { name = 'Subgraph', rootGraph, inputs = [], outputs = [] } = {}) {
  graph.name = name
  graph.rootGraph = rootGraph
  graph.inputs = inputs.map((spec) => new FakeSubgraphInput(spec.name, spec.type))
  graph.outputs = outputs.map((spec) => new FakeSubgraphOutput(graph, spec.name, spec.type))
  return withNodeList(graph)
}

/** Gives a harness graph the `_nodes` array the pack's walkers read. */
export function withNodeList(graph) {
  if (!Object.getOwnPropertyDescriptor(graph, '_nodes')) {
    Object.defineProperty(graph, '_nodes', {
      get() {
        return graph.nodesById ? [...graph.nodesById.values()] : []
      }
    })
  }
  return graph
}

/** A wire that leaves subgraph INPUT slot *inputIndex* (origin -10) and ends
 * at *node*'s input *slot*, the way `SubgraphInput.connect` leaves it. *type*
 * is the recorded `link.type` (defaults to the target input's type). */
export function wireFromSubgraphInput(subgraph, inputIndex, node, slot, type) {
  const link = {
    id: nextIoLinkId++, type: type ?? node.inputs[slot].type,
    origin_id: SUBGRAPH_INPUT_ID, origin_slot: inputIndex,
    target_id: node.id, target_slot: slot
  }
  subgraph.links.set(link.id, link)
  subgraph.inputs[inputIndex].linkIds.push(link.id)
  node.inputs[slot].link = link.id
  node.onConnectionsChange?.(1, slot, true, link, node.inputs[slot])
  return link
}
