// A small FAKE litegraph for the broadcast tests (tests/test_broadcast_*_js.py).
// It mimics exactly the 1.52.7 behaviours the broadcast modules depend on
// (verified against the extracted frontend source, lib/litegraph/src):
//  - LGraphNode.connect: type check, onConnectInput veto, silently REPLACES an
//    occupied input, fires onConnectionsChange on both ends, graph.onAfterChange
//  - LGraphNode.disconnectInput: an origin of -10 in a subgraph routes through
//    the definition input's linkIds
//  - SubgraphInput.connect(slot, node): the inner link, origin -10
//  - Subgraph.addInput -> every SubgraphNode INSTANCE grows an input
//    (input-added); Subgraph.removeInput removes it again (removing-input)
//  - SubgraphSlot.disconnect() iterates linkIds WHILE each removal splices it
//  - NodeId is a branded STRING on 1.52.7 ('-10'), a number on older frontends
let nextLinkId = 1
let nextUuid = 1
export const LEGACY = { ids: false } // true: node ids / sentinel as NUMBERS (old frontends)
const SUB_INPUT = () => (LEGACY.ids ? -10 : '-10')

export class FakeGraph {
  constructor(id) {
    this.id = id
    this._nodes = []
    this._links = new Map()
    this.links = this._links
    this.nodesById = {}
    this.dirty = 0
    this._version = 0
  }
  incrementVersion() { this._version += 1 }
  get rootGraph() { return this._root || this }
  getNodeById(id) { return this.nodesById[id] ?? null }
  getLink(id) { return this._links.get(id) }
  add(node) {
    node.graph = this
    this._nodes.push(node)
    this.nodesById[node.id] = node
    this.incrementVersion()
    this.onNodeAdded?.(node)
    return node
  }
  removeLink(id) {
    const link = this._links.get(id)
    if (!link) return
    this.getNodeById(link.target_id)?.disconnectInput(link.target_slot)
    this._links.delete(id)
  }
  setDirtyCanvas() { this.dirty += 1 }
}

export class FakeSubgraph extends FakeGraph {
  constructor(root, id, name) {
    super(id)
    this._root = root
    this.name = name
    this.inputs = []
    this.outputs = []
    this.inputNode = {}
    this.listeners = {}
    root.subgraphs.set(id, this)
  }
  on(event, fn) { (this.listeners[event] ??= []).push(fn) }
  emit(event, detail) { for (const fn of this.listeners[event] || []) fn(detail) }
  addInput(name, type) {
    const input = new FakeSubInput(this, name, type)
    this.inputs.push(input)
    this.emit('input-added', { input })
    return input
  }
  removeInput(input) {
    const index = this.inputs.indexOf(input)
    if (index === -1) throw new Error('Input not found')
    this.emit('removing-input', { input, index })
    input.disconnect()
    this.inputs.splice(index, 1)
    for (let i = index; i < this.inputs.length; i++) {
      for (const id of this.inputs[i].linkIds) {
        const link = this._links.get(id)
        if (link) link.origin_slot--
      }
    }
  }
}

export class FakeSubInput {
  constructor(subgraph, name, type) {
    this.subgraph = subgraph
    this.id = `uuid-${nextUuid++}`
    this.name = name
    this.type = type
    this.linkIds = []
  }
  connect(slot, node) {
    const index = node.inputs.indexOf(slot)
    if (index < 0) return undefined
    if (node.onConnectInput?.(index, this.type, this, null, -1) === false) return undefined
    if (slot.link != null) node.disconnectInput(index)
    const link = {
      id: nextLinkId++, origin_id: SUB_INPUT(), origin_slot: this.subgraph.inputs.indexOf(this),
      target_id: node.id, target_slot: index, type: slot.type
    }
    this.subgraph._links.set(link.id, link)
    this.linkIds.push(link.id)
    slot.link = link.id
    this.subgraph.incrementVersion()
    node.onConnectionsChange?.(1, index, true, link, slot)
    return link
  }
  /** SubgraphSlot.disconnect(): iterates linkIds while removal splices it. */
  disconnect() {
    for (const id of this.linkIds) this.subgraph.removeLink(id)
    this.linkIds.length = 0
  }
}

const isGeneric = (t) => t == null || t === '' || t === '*'
const typesOk = (a, b) => isGeneric(a) || isGeneric(b) || String(a) === String(b)

export class FakeNode {
  constructor(id, title, cls) {
    this.id = LEGACY.ids ? Number(id) : String(id)
    this.title = title
    this.comfyClass = cls
    this.type = cls
    this.inputs = []
    this.outputs = []
    this.widgets = []
    this.properties = {}
    this.graph = null
    this.size = [200, 100]
    this.mode = 0
  }
  setSize(size) { this.size = [...size] }
  setDirtyCanvas() {}
  computeSize() { return [200, 100] }
  connect(slot, target, targetSlot) {
    const graph = this.graph
    const output = this.outputs[slot]
    const input = target.inputs[targetSlot]
    if (!output || !input) return null
    if (!typesOk(output.type, input.type)) return null
    if (target.onConnectInput?.(targetSlot, output.type, output, this, slot) === false) return null
    if (input.link != null) target.disconnectInput(targetSlot) // core REPLACES
    const link = {
      id: nextLinkId++, origin_id: this.id, origin_slot: slot,
      target_id: target.id, target_slot: targetSlot, type: input.type || output.type
    }
    graph._links.set(link.id, link)
    ;(output.links ??= []).push(link.id)
    input.link = link.id
    graph.incrementVersion()
    this.onConnectionsChange?.(2, slot, true, link, output)
    target.onConnectionsChange?.(1, targetSlot, true, link, input)
    graph.onAfterChange?.(graph)
    return link
  }
  disconnectInput(slot) {
    const input = this.inputs[slot]
    if (!input || input.link == null) return true
    const graph = this.graph
    const link = graph._links.get(input.link)
    input.link = null
    if (link) {
      if (String(link.origin_id) === '-10' && graph.inputs) {
        const sub = graph.inputs[link.origin_slot]
        const i = sub?.linkIds.indexOf(link.id)
        if (sub && i !== -1) sub.linkIds.splice(i, 1)
      } else {
        const origin = graph.getNodeById(link.origin_id)
        const output = origin?.outputs?.[link.origin_slot]
        if (output?.links) {
          const i = output.links.indexOf(link.id)
          if (i !== -1) output.links.splice(i, 1)
        }
        origin?.onConnectionsChange?.(2, link.origin_slot, false, link, output)
      }
      graph._links.delete(link.id)
      graph.incrementVersion()
    }
    this.onConnectionsChange?.(1, slot, false, link, input)
    return true
  }
}

export class FakeSubgraphNode extends FakeNode {
  constructor(id, title, subgraph) {
    super(id, title, 'Subgraph')
    this.subgraph = subgraph
    this.inputs = subgraph.inputs.map((sub) => ({ name: sub.name, type: sub.type, link: null, _subgraphSlot: sub }))
    subgraph.on('input-added', ({ input }) => {
      this.inputs.push({ name: input.name, type: input.type, link: null, _subgraphSlot: input })
    })
    subgraph.on('removing-input', ({ index }) => {
      this.disconnectInput(index)
      this.inputs.splice(index, 1)
      for (let i = index; i < this.inputs.length; i++) {
        const link = this.graph?._links.get(this.inputs[i].link)
        if (link) link.target_slot--
      }
    })
  }
}

export const NODEDATA = {
  KSampler: { input: { required: { model: ['MODEL'], positive: ['CONDITIONING'] } } },
  VAEDecode: { input: { required: { samples: ['LATENT'], vae: ['VAE'] } } },
  SaveImage: { input: { required: { images: ['IMAGE'], filename_prefix: ['STRING'] } } },
  Loader: { input: { required: {} } },
  Mult: { input: { required: { text: ['STRING'] }, optional: { model: ['MODEL'], clip: ['CLIP'], vae: ['VAE'] } } }
}

export function makeNode(graph, id, cls, title, inputs, outputs = [], extra = {}) {
  class T extends FakeNode { static nodeData = NODEDATA[cls] }
  const node = new T(id, title, cls)
  node.inputs = inputs.map((i) => ({ link: null, ...i }))
  node.outputs = outputs.map((o) => ({ links: null, ...o }))
  Object.assign(node, extra)
  graph.add(node)
  return node
}

export const OUTS = [
  ['model', 'MODEL'], ['clip', 'CLIP'], ['image', 'IMAGE'], ['text', 'STRING'], ['save_prefix', 'STRING'],
  ['label', 'STRING'], ['vae', 'VAE'], ['model_low', 'MODEL'], ['run_info', 'STRING']
]

export function makeMultiplier(graph, id = 1) {
  const m = makeNode(
    graph, id, 'EPSCrossSweep', 'EPS Run Multiplier',
    [
      { name: 'text', type: 'STRING' }, { name: 'model', type: 'MODEL', shape: 7 },
      { name: 'model_low', type: 'MODEL', shape: 7 }, { name: 'clip', type: 'CLIP', shape: 7 },
      { name: 'label', type: 'STRING', shape: 7 }, { name: 'image', type: 'IMAGE', shape: 7 },
      { name: 'vae', type: 'VAE', shape: 7 }
    ],
    OUTS.map(([name, type]) => ({ name, type }))
  )
  m.constructor.nodeData = NODEDATA.Mult
  return m
}

export function makeLoader(graph, id = 900) {
  return makeNode(graph, id, 'Loader', 'Loader', [], [
    { name: 'MODEL', type: 'MODEL' }, { name: 'CLIP', type: 'CLIP' }, { name: 'VAE', type: 'VAE' }
  ])
}

/** root + multiplier fed from a loader (model, clip, vae wired). */
export function build(wired = ['model', 'clip', 'vae']) {
  const root = new FakeGraph('root-uuid')
  root.subgraphs = new Map()
  const loader = makeLoader(root)
  const m = makeMultiplier(root)
  const slotOf = { model: 0, clip: 1, vae: 2 }
  const inputOf = (name) => m.inputs.findIndex((i) => i.name === name)
  for (const name of wired) loader.connect(slotOf[name], m, inputOf(name))
  return { root, loader, m, inputOf }
}

export const ksampler = (g, id, title = 'KSampler') =>
  makeNode(g, id, 'KSampler', title, [
    { name: 'model', type: 'MODEL' }, { name: 'positive', type: 'CONDITIONING' }, { name: 'seed', type: 'INT', widget: { name: 'seed' } }
  ])
export const decode = (g, id) =>
  makeNode(g, id, 'VAEDecode', 'VAE Decode', [{ name: 'samples', type: 'LATENT' }, { name: 'vae', type: 'VAE' }])
export const saver = (g, id) =>
  makeNode(g, id, 'SaveImage', 'Save Image', [
    { name: 'images', type: 'IMAGE' }, { name: 'filename_prefix', type: 'STRING', widget: { name: 'filename_prefix' } }
  ])

export const linkOf = (node, name) => {
  const input = node.inputs.find((i) => i.name === name)
  return input?.link == null ? null : node.graph._links.get(input.link) ?? null
}
export const wiredFrom = (node, name) => {
  const l = linkOf(node, name)
  return l ? { origin: String(l.origin_id), slot: l.origin_slot } : null
}
export const props = (m) => m.properties.Broadcast ?? null

