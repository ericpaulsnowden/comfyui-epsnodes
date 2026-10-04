// Shared fake NESTED litegraph for the Node-harness tests (v1.2.0 nested
// reach, FORMAT.md §7.10). Models exactly what the ComfyUI frontend's
// subgraph code does (lib/litegraph/src/subgraph/*, LGraph.ts), no more:
//
//  - A SubgraphNode carries `.subgraph` -- its own graph with its own node
//    list and link table. SubgraphNode.inputs[i] / .outputs[j] are
//    index-aligned with subgraph.inputs[i] / .outputs[j].
//  - Inside the subgraph, the boundary is two pseudo-nodes that are NOT in
//    `_nodes` / `getNodeById`: a link with origin_id -10 leaves the subgraph
//    INPUT node (origin_slot = the subgraph input's index) and a link with
//    target_id -20 enters its OUTPUT node (target_slot = the output index).
//    The wire visible OUTSIDE is a separate link in the PARENT graph's table.
//  - Links live in `graph.links` (a Map), node ids are numbers here (newer
//    frontends use strings -- nothing the pack does may depend on either).
//
// Tests copy this file into a served-layout tmp dir next to their probe and
// `import { ... } from './nested_graph.mjs'`.

export const SUBGRAPH_INPUT_ID = -10
export const SUBGRAPH_OUTPUT_ID = -20
export const INPUT = 'SUBGRAPH_INPUT' // pseudo-node handle for wire()
export const OUTPUT = 'SUBGRAPH_OUTPUT'

let nextLinkId = 1

export class FakeGraph {
  constructor({ name = 'root', rootGraph = null } = {}) {
    this.name = name
    this._nodes = []
    this.links = new Map()
    this.inputs = [] // Subgraph only: [{name, type}]
    this.outputs = [] // Subgraph only: [{name, type}]
    this.rootGraph = rootGraph || this
    this.dirty = 0
  }
  getNodeById(id) {
    return this._nodes.find((n) => String(n.id) === String(id)) || null
  }
  add(node) {
    node.graph = this
    this._nodes.push(node)
    this.onNodeAdded?.(node)
    return node
  }
  remove(node) {
    this._nodes = this._nodes.filter((n) => n !== node)
    this.onNodeRemoved?.(node)
  }
  setDirtyCanvas() {
    this.dirty += 1
  }
}

export class FakeNode {
  constructor({ id, type = 'Fake', title, inputs = [], outputs = [], widgets = [] } = {}) {
    this.id = id
    this.type = type
    this.comfyClass = type
    this.title = title ?? type
    this.inputs = inputs.map((i) => (typeof i === 'string' ? { name: i } : { ...i }))
    this.inputs.forEach((i) => {
      i.type ??= '*'
      i.link ??= null
    })
    this.outputs = outputs.map((o) => (typeof o === 'string' ? { name: o } : { ...o }))
    this.outputs.forEach((o) => {
      o.type ??= '*'
      o.links ??= []
    })
    this.widgets = widgets
    this.graph = null
    this.size = [200, 100]
  }
  setDirtyCanvas() {}
  setSize(size) {
    this.size = [...size]
  }
  getInputLink(slot) {
    const link = this.inputs[slot]?.link
    return link == null ? null : this.graph.links.get(link) ?? null
  }
  // litegraph's own LGraphNode.getInputNode: getNodeById(origin_id), so it
  // returns null across a boundary (-10 is not a node) -- the very gap the
  // pack's resolvers close.
  getInputNode(slot) {
    const link = this.getInputLink(slot)
    return link ? this.graph.getNodeById(link.origin_id) : null
  }
  connect(slot, target, targetSlot) {
    return wire(this.graph, this, slot, target, targetSlot)
  }
  disconnectInput(slot) {
    const input = this.inputs[slot]
    if (!input || input.link == null) return true
    const link = this.graph.links.get(input.link)
    input.link = null
    if (link) {
      this.graph.links.delete(link.id)
      const origin = this.graph.getNodeById(link.origin_id)
      const out = origin?.outputs?.[link.origin_slot]
      if (out?.links) out.links = out.links.filter((id) => id !== link.id)
    }
    this.onConnectionsChange?.(1, slot, false, link, input)
    return true
  }
}

export class FakeSubgraphNode extends FakeNode {
  constructor({ id, title, name = 'Subgraph', inputs = [], outputs = [], rootGraph }) {
    super({ id, type: `uuid-${name}`, title: title ?? name, inputs, outputs })
    this.subgraph = new FakeGraph({ name, rootGraph })
    this.subgraph.inputs = this.inputs.map((i) => ({ name: i.name, type: i.type }))
    this.subgraph.outputs = this.outputs.map((o) => ({ name: o.name, type: o.type }))
  }
  isSubgraphNode() {
    return true
  }
}

/** Create a link `origin.outputs[slot] -> target.inputs[targetSlot]` inside
 * *graph*. *origin* may be the INPUT handle (the subgraph's input node,
 * slot = a subgraph input index) and *target* the OUTPUT handle (slot = a
 * subgraph output index). Replaces an occupied input like litegraph. */
export function wire(graph, origin, slot, target, targetSlot) {
  const fromBoundary = origin === INPUT
  const toBoundary = target === OUTPUT
  const link = {
    id: nextLinkId++,
    origin_id: fromBoundary ? SUBGRAPH_INPUT_ID : origin.id,
    origin_slot: slot,
    target_id: toBoundary ? SUBGRAPH_OUTPUT_ID : target.id,
    target_slot: targetSlot,
    type: '*'
  }
  if (!toBoundary) {
    const input = target.inputs[targetSlot]
    if (input.link != null) target.disconnectInput(targetSlot)
    input.link = link.id
  }
  if (!fromBoundary) origin.outputs[slot].links.push(link.id)
  graph.links.set(link.id, link)
  if (!toBoundary) target.onConnectionsChange?.(1, targetSlot, true, link, target.inputs[targetSlot])
  return link
}

/** Convenience: build a graph holding *nodes*, returning it. */
export function graphOf(nodes, opts) {
  const g = new FakeGraph(opts)
  for (const n of nodes) g.add(n)
  return g
}

/**
 * The canonical fixture most tests start from:
 *
 *   root:  [A: 1 out]  --->  [S (id 10): in "x" / out "y"]  --->  [C: 1 in]
 *   S.subgraph:  INPUT.x --> [inner (id 2): in "i", out "o"] --> OUTPUT.y
 *
 * `A` is outside, `inner` is inside, and the data path crosses the boundary
 * in both directions. Returns every piece so a test can rewire it.
 */
export function simpleNested() {
  const root = new FakeGraph({ name: 'root' })
  const A = root.add(new FakeNode({ id: 1, type: 'Src', outputs: ['out'] }))
  const S = root.add(new FakeSubgraphNode({ id: 10, name: 'Wrapper', inputs: ['x'], outputs: ['y'], rootGraph: root }))
  const C = root.add(new FakeNode({ id: 3, type: 'Sink', inputs: ['in'] }))
  const sub = S.subgraph
  const inner = sub.add(new FakeNode({ id: 2, type: 'Inner', inputs: ['i'], outputs: ['o'] }))
  wire(root, A, 0, S, 0)
  wire(sub, INPUT, 0, inner, 0)
  wire(sub, inner, 0, OUTPUT, 0)
  wire(root, S, 0, C, 0)
  return { root, A, S, C, sub, inner }
}
