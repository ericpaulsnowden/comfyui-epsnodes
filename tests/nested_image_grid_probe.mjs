// Node probe for EPS Image Grid's v1.2.0 NESTED REACH (owner ask 2026-10-03,
// FORMAT.md §7.10) -- driven by tests/test_image_grid_js.py through
// nested_layout.build_layout/run_probe. Runs the REAL web/eps_image/image_grid.js
// against the shared fake nested litegraph (nested_graph.mjs): a grid inside a
// SubgraphNode's definition, a definition shared by several SubgraphNodes, and
// wires that cross the subgraph boundary as two separate links.
//
// What this cannot cover (the rig must): the real LGraph/LLink classes, a real
// run's execution ids, core's Pinia output store and canvas repaint.

import { app } from './scripts/app.js'
import { api } from './scripts/api.js'
import * as grid from './extensions/comfyui-epsnodes/eps_image/image_grid.js'
import { walkLiveNodes } from './extensions/comfyui-epsnodes/lora_library/api.js'
import {
  FakeGraph,
  FakeNode,
  FakeSubgraphNode,
  wire,
  INPUT,
  OUTPUT
} from './nested_graph.mjs'

// ---- environment ----------------------------------------------------------
globalThis.Image = class {
  set src(value) { this._src = value }
  get src() { return this._src }
}

// Timers are captured, never really waited on: attach()'s deferred dedup is a
// 0ms timeout and the settled-collision sweep a 1500ms one.
const timers = []
let timerSeq = 0
globalThis.setTimeout = (fn, ms) => {
  const id = ++timerSeq
  timers.push({ id, fn, ms })
  return id
}
globalThis.clearTimeout = (id) => {
  const at = timers.findIndex((t) => t.id === id)
  if (at >= 0) timers.splice(at, 1)
}
const runTimers = async () => {
  const due = timers.splice(0)
  for (const t of due) await t.fn()
}
const flush = async () => {
  for (let i = 0; i < 6; i++) await new Promise((resolve) => setImmediate(resolve))
}

const visibilityHandlers = []
globalThis.document = {
  hidden: false,
  addEventListener: (name, fn) => {
    if (name === 'visibilitychange') visibilityHandlers.push(fn)
  }
}

const calls = []
api.fetchApi = async (route, opts) => {
  calls.push({ route, body: opts && opts.body ? JSON.parse(opts.body) : null })
  return { ok: true, json: async () => ({ ok: true, refs: [], images: [] }) }
}
const handlers = {}
api.addEventListener = (name, fn) => {
  handlers[name] = fn
}
const toasts = []
app.extensionManager = { toast: { add: (t) => toasts.push(t) } }
app.nodeOutputs = {}
app.configuringGraph = false

const realLog = console.log
const realWarn = console.warn
const logs = []
const warns = []
console.log = (...a) => logs.push(a.join(' '))
console.warn = (...a) => warns.push(a.map(String).join(' '))

grid.init() // installs the module-scope progress_state + visibilitychange listeners

// ---- fakes ----------------------------------------------------------------
const UUID = (n) => `${String(n).padStart(8, '0')}-0000-4000-8000-000000000000`
const R = (name) => ({ filename: name, subfolder: '', type: 'output' })

class FakeGrid extends FakeNode {
  constructor({ id, uuid = '', mode = 'Collect' }) {
    super({
      id,
      type: 'EPSImageGrid',
      inputs: ['image'],
      outputs: ['image', 'width', 'height'],
      widgets: [
        { name: 'mode', value: mode },
        { name: 'grid_uuid', value: uuid },
        { name: 'focus', value: '' }
      ]
    })
    this.properties = uuid ? { uuid } : {}
  }
  addWidget(type, name, value, callback, options) {
    const widget = { type, name, value, callback, options }
    this.widgets.push(widget)
    return widget
  }
}

const listCalls = (uuid) =>
  calls.filter((c) => c.route.startsWith('/eps_image_grid/list') && c.route.includes(uuid)).length
const cloneCalls = () => calls.filter((c) => c.route === '/eps_image_grid/clone')
const reset = () => {
  timers.length = 0
  calls.length = 0
  logs.length = 0
  toasts.length = 0
  document.hidden = false
}
const uuidOf = (node) => node.properties.uuid
const widgetUuidOf = (node) => node.widgets.find((w) => w.name === 'grid_uuid').value

const out = {}

// ---------------------------------------------------------------------------
// 1. identity collision on paste (attach -> deferred ensureUniqueUuid)
// ---------------------------------------------------------------------------
{
  // 1a. a copy pasted INTO a subgraph next to a root grid
  reset()
  const root = new FakeGraph()
  app.graph = root
  const rootGrid = root.add(new FakeGrid({ id: 1, uuid: UUID(1) }))
  const S = root.add(new FakeSubgraphNode({ id: 3, name: 'W', rootGraph: root }))
  const pasted = S.subgraph.add(new FakeGrid({ id: 2, uuid: UUID(1) }))
  grid.attach(pasted)
  await runTimers()
  await flush()
  out.pasteNested = {
    nestedMinted: uuidOf(pasted) !== UUID(1) && widgetUuidOf(pasted) === uuidOf(pasted),
    rootKept: uuidOf(rootGrid) === UUID(1),
    cloned: cloneCalls().map((c) => ({ from: c.body.from, toIsNew: c.body.to === uuidOf(pasted) })),
    refreshedNewUuid: listCalls(uuidOf(pasted)) >= 1,
    logLine: logs.find((l) => l.includes('collided with a live sibling')) || null
  }

  // 1b. the reverse: a root grid pasted next to an existing NESTED grid
  reset()
  const root2 = new FakeGraph()
  app.graph = root2
  const S2 = root2.add(new FakeSubgraphNode({ id: 3, name: 'W', rootGraph: root2 }))
  const nested = S2.subgraph.add(new FakeGrid({ id: 2, uuid: UUID(2) }))
  const pastedRoot = root2.add(new FakeGrid({ id: 9, uuid: UUID(2) }))
  grid.attach(pastedRoot)
  await runTimers()
  await flush()
  out.pasteRootNextToNested = {
    rootMinted: uuidOf(pastedRoot) !== UUID(2),
    nestedKept: uuidOf(nested) === UUID(2),
    cloneCount: cloneCalls().length,
    logLine: logs.find((l) => l.includes('collided with a live sibling')) || null
  }

  // 1b-2. two grids in two DIFFERENT subgraph definitions (a cloned subgraph):
  // neither is in the root list, so a root-only walk never saw the pair
  reset()
  const root4 = new FakeGraph()
  app.graph = root4
  const SA = root4.add(new FakeSubgraphNode({ id: 3, name: 'A', rootGraph: root4 }))
  const SB = root4.add(new FakeSubgraphNode({ id: 4, name: 'B', rootGraph: root4 }))
  const inA = SA.subgraph.add(new FakeGrid({ id: 2, uuid: UUID(4) }))
  const inB = SB.subgraph.add(new FakeGrid({ id: 2, uuid: UUID(4) }))
  grid.attach(inB)
  await runTimers()
  await flush()
  out.pasteNestedNextToNested = {
    pastedMinted: uuidOf(inB) !== UUID(4),
    otherKept: uuidOf(inA) === UUID(4),
    cloneCount: cloneCalls().length,
    logLine: logs.find((l) => l.includes('collided with a live sibling')) || null
  }

  // 1c. a grid in a definition SHARED by two SubgraphNodes is one object under
  // two path ids -- never its own sibling
  reset()
  const root3 = new FakeGraph()
  app.graph = root3
  const A = root3.add(new FakeSubgraphNode({ id: 10, name: 'W', rootGraph: root3 }))
  const B = root3.add(new FakeSubgraphNode({ id: 11, name: 'W', rootGraph: root3 }))
  B.subgraph = A.subgraph
  const shared = A.subgraph.add(new FakeGrid({ id: 2, uuid: UUID(3) }))
  grid.attach(shared)
  await runTimers()
  await flush()
  out.pasteShared = {
    uuidUnchanged: uuidOf(shared) === UUID(3),
    cloneCount: cloneCalls().length,
    logged: logs.some((l) => l.includes('collided')),
    // the same OBJECT really is reported under both path ids
    pathsSeen: walkLiveNodes(root3).filter((e) => e.node === shared).map((e) => e.pathId).sort()
  }
}

// ---------------------------------------------------------------------------
// 2. settled-collision sweep: whole workflow, lowest PATH id keeps the identity
// ---------------------------------------------------------------------------
const settle = async (anyNode) => {
  grid.loadedGraphNode(anyNode) // arms the sweep (and refreshes)
  await flush()
  await runTimers() // the 1500ms sweep itself
  await flush()
}
{
  // 2a. root #1 vs nested "3:2": the NESTED duplicate loses
  reset()
  const root = new FakeGraph()
  app.graph = root
  const keeper = root.add(new FakeGrid({ id: 1, uuid: UUID(10) }))
  const S = root.add(new FakeSubgraphNode({ id: 3, name: 'W', rootGraph: root }))
  const dup = S.subgraph.add(new FakeGrid({ id: 2, uuid: UUID(10) }))
  await settle(keeper)
  out.sweepNestedLoses = {
    keeperKept: uuidOf(keeper) === UUID(10),
    dupMinted: uuidOf(dup) !== UUID(10),
    cloneCount: cloneCalls().length,
    logLine: logs.find((l) => l.startsWith('[eps_image:image_grid] settled duplicate')) || null
  }

  // 2b. root #5 vs nested "3:2": the lower PATH wins, so the ROOT grid loses --
  // ordering is by path, not "root first"
  reset()
  const root2 = new FakeGraph()
  app.graph = root2
  const rootHigh = root2.add(new FakeGrid({ id: 5, uuid: UUID(11) }))
  const S2 = root2.add(new FakeSubgraphNode({ id: 3, name: 'W', rootGraph: root2 }))
  const nestedLow = S2.subgraph.add(new FakeGrid({ id: 2, uuid: UUID(11) }))
  await settle(rootHigh)
  out.sweepPathOrdering = {
    nestedKept: uuidOf(nestedLow) === UUID(11),
    rootMinted: uuidOf(rootHigh) !== UUID(11),
    logLine: logs.find((l) => l.includes('settled duplicate')) || null
  }

  // 2c. three-way: root 12, "3:2", "10:1" -- numeric per segment, not lexical:
  // "3:2" < "10:1" < "12"
  reset()
  const root3 = new FakeGraph()
  app.graph = root3
  const r12 = root3.add(new FakeGrid({ id: 12, uuid: UUID(12) }))
  const S3 = root3.add(new FakeSubgraphNode({ id: 3, name: 'W3', rootGraph: root3 }))
  const S10 = root3.add(new FakeSubgraphNode({ id: 10, name: 'W10', rootGraph: root3 }))
  const n32 = S3.subgraph.add(new FakeGrid({ id: 2, uuid: UUID(12) }))
  const n101 = S10.subgraph.add(new FakeGrid({ id: 1, uuid: UUID(12) }))
  await settle(r12)
  out.sweepNumericSegments = {
    keeper: [uuidOf(r12), uuidOf(n32), uuidOf(n101)].map((u) => u === UUID(12)),
    cloneCount: cloneCalls().length,
    logs: logs.filter((l) => l.includes('settled duplicate'))
  }

  // 2d. a shared-definition grid (one object, paths "10:2" and "11:2") alone:
  // not a duplicate of itself, no remint, no clone
  reset()
  const root4 = new FakeGraph()
  app.graph = root4
  const A = root4.add(new FakeSubgraphNode({ id: 10, name: 'W', rootGraph: root4 }))
  const B = root4.add(new FakeSubgraphNode({ id: 11, name: 'W', rootGraph: root4 }))
  B.subgraph = A.subgraph
  const sharedAlone = A.subgraph.add(new FakeGrid({ id: 2, uuid: UUID(13) }))
  await settle(sharedAlone)
  out.sweepSharedAlone = {
    unchanged: uuidOf(sharedAlone) === UUID(13),
    cloneCount: cloneCalls().length,
    logged: logs.some((l) => l.includes('settled duplicate'))
  }

  // 2e. root #5 + the shared object under "10:2"/"11:2": the shared grid is ONE
  // duplicate (minted once, cloned once), not one per path id
  reset()
  const root5 = new FakeGraph()
  app.graph = root5
  const rootKeeper = root5.add(new FakeGrid({ id: 5, uuid: UUID(14) }))
  const A5 = root5.add(new FakeSubgraphNode({ id: 10, name: 'W', rootGraph: root5 }))
  const B5 = root5.add(new FakeSubgraphNode({ id: 11, name: 'W', rootGraph: root5 }))
  B5.subgraph = A5.subgraph
  const sharedDup = A5.subgraph.add(new FakeGrid({ id: 2, uuid: UUID(14) }))
  await settle(rootKeeper)
  out.sweepSharedDuplicate = {
    rootKept: uuidOf(rootKeeper) === UUID(14),
    sharedMinted: uuidOf(sharedDup) !== UUID(14),
    cloneCount: cloneCalls().length,
    logs: logs.filter((l) => l.includes('settled duplicate'))
  }
}

// ---------------------------------------------------------------------------
// 3. post-run refresh: progress_state keyed by EXECUTION PATH id
// ---------------------------------------------------------------------------
const emit = async (nodes) => {
  handlers.progress_state({ detail: { nodes } })
  await flush()
}
{
  // root grid id 3 and a nested grid whose LOCAL id is also 3 (path "4:3")
  reset()
  const root = new FakeGraph()
  app.graph = root
  const rootGrid = root.add(new FakeGrid({ id: 3, uuid: UUID(20) }))
  const S = root.add(new FakeSubgraphNode({ id: 4, name: 'W', rootGraph: root }))
  const nested = S.subgraph.add(new FakeGrid({ id: 3, uuid: UUID(21) }))
  const counts = () => ({ root: listCalls(UUID(20)), nested: listCalls(UUID(21)) })

  await emit({ '4:3': { state: 'running' } })
  const afterRunning = counts()
  await emit({ '4:3': { state: 'finished' } })
  const afterNestedFinish = counts()
  await emit({ '4:3': { state: 'finished' } }) // resent for the rest of the prompt
  const afterResend = counts()
  await emit({ 3: { state: 'finished' } }) // the ROOT "3" -- a different node
  const afterRootKey = counts()
  await emit({ 3: { state: 'finished' } })
  const afterRootResend = counts()
  out.progressNested = { afterRunning, afterNestedFinish, afterResend, afterRootKey, afterRootResend }

  // the BARE local id "3" must never be read as the nested grid's key when no
  // root node owns it: a nested grid only answers to its path id
  reset()
  const root2 = new FakeGraph()
  app.graph = root2
  const S2 = root2.add(new FakeSubgraphNode({ id: 4, name: 'W', rootGraph: root2 }))
  root2.add(new FakeSubgraphNode({ id: 8, name: 'Other', rootGraph: root2 }))
  const lone = S2.subgraph.add(new FakeGrid({ id: 3, uuid: UUID(22) }))
  await emit({ 3: { state: 'finished' }, '8:3': { state: 'finished' } })
  out.progressBareIdIgnored = { refreshes: listCalls(UUID(22)), uuid: uuidOf(lone) }
}
{
  // one definition, two instances: the same grid object under "10:2" and "11:2"
  reset()
  const root = new FakeGraph()
  app.graph = root
  const A = root.add(new FakeSubgraphNode({ id: 10, name: 'W', rootGraph: root }))
  const B = root.add(new FakeSubgraphNode({ id: 11, name: 'W', rootGraph: root }))
  B.subgraph = A.subgraph
  A.subgraph.add(new FakeGrid({ id: 2, uuid: UUID(23) }))
  const n = () => listCalls(UUID(23))
  const steps = []
  await emit({ '10:2': { state: 'finished' }, '11:2': { state: 'running' } })
  steps.push(n()) // 1 -- 10:2 finished
  await emit({ '10:2': { state: 'finished' }, '11:2': { state: 'finished' } })
  steps.push(n()) // 2 -- 11:2's own transition; 10:2 must not re-fire or be masked
  await emit({ '10:2': { state: 'finished' }, '11:2': { state: 'finished' } })
  steps.push(n()) // 2 -- nothing new
  await emit({ '10:2': { state: 'running' }, '11:2': { state: 'running' } })
  steps.push(n()) // 2
  await emit({ '10:2': { state: 'finished' }, '11:2': { state: 'finished' } })
  steps.push(n()) // 3 -- BOTH instances finish in ONE event: one refresh, not two
  out.progressShared = { steps }

  // hidden tab: queued under both path ids, flushed exactly once
  reset()
  document.hidden = true
  await emit({ '10:2': { state: 'running' }, '11:2': { state: 'running' } })
  await emit({ '10:2': { state: 'finished' }, '11:2': { state: 'finished' } })
  const whileHidden = n()
  document.hidden = false
  visibilityHandlers.forEach((h) => h())
  await flush()
  out.progressHidden = { whileHidden, afterVisible: n() }
}
{
  // the post-run empty-buffer toast reads the REAL wiring across the boundary
  reset()
  const root = new FakeGraph()
  app.graph = root
  const A = root.add(new FakeNode({ id: 1, type: 'Src', outputs: ['o'] }))
  const S = root.add(
    new FakeSubgraphNode({ id: 3, name: 'W', inputs: ['x'], outputs: [], rootGraph: root })
  )
  const nested = S.subgraph.add(new FakeGrid({ id: 2, uuid: UUID(24) }))
  wire(S.subgraph, INPUT, 0, nested, 0) // fed from the subgraph input...
  const warned = () => toasts.filter((t) => t.summary === 'EPS Image Grid').length
  await emit({ '3:2': { state: 'finished' } })
  const dangling = warned() // ...which nothing outside feeds: the run sees no input
  await emit({ '3:2': { state: 'running' } })
  wire(root, A, 0, S, 0) // now wired from outside
  await emit({ '3:2': { state: 'finished' } })
  const wired = warned()
  // flat: a plain linked input never warns; an unlinked one still does
  reset()
  const rootFlat = new FakeGraph()
  app.graph = rootFlat
  const Src = rootFlat.add(new FakeNode({ id: 1, type: 'Src', outputs: ['o'] }))
  const linked = rootFlat.add(new FakeGrid({ id: 2, uuid: UUID(25) }))
  const bare = rootFlat.add(new FakeGrid({ id: 3, uuid: UUID(26) }))
  linked.title = 'linked grid'
  bare.title = 'bare grid'
  wire(rootFlat, Src, 0, linked, 0)
  await emit({ 2: { state: 'finished' }, 3: { state: 'finished' } })
  out.progressWarning = {
    danglingBoundaryWarns: dangling,
    wiredBoundaryAddsNoToast: wired - dangling,
    flatWarnedFor: toasts.map((t) => /"([^"]+)"/.exec(t.detail)[1])
  }
}

// ---------------------------------------------------------------------------
// 4. core output store: the nested locator
// ---------------------------------------------------------------------------
{
  const coreKey = (node) =>
    node.graph.isRootGraph === false ? `${node.graph.id}:${node.id}` : String(node.id)
  const coreWouldRerender = (node) => {
    const output = app.nodeOutputs[coreKey(node)]
    return !!(output && node.images !== output.images)
  }
  reset()
  const root = new FakeGraph()
  app.graph = root
  app.nodeOutputs = {}
  const S = root.add(new FakeSubgraphNode({ id: 3, name: 'W', rootGraph: root }))
  S.subgraph.id = UUID(77)
  S.subgraph.isRootGraph = false
  const nested = S.subgraph.add(new FakeGrid({ id: 2, uuid: UUID(30) }))
  const bystander = root.add(new FakeGrid({ id: 2, uuid: UUID(31) })) // SAME bare id, root graph
  app.nodeOutputs['2'] = { images: ['bystander'] }
  const key = `${UUID(77)}:2`

  grid.setNodeImagesFromRefs(nested, [R('a.png'), R('b.png')])
  const afterSet = {
    storeKeyed: !!app.nodeOutputs[key],
    sharedIdentity: app.nodeOutputs[key]?.images === nested.images,
    coreWouldRerender: coreWouldRerender(nested),
    bystanderUntouched: app.nodeOutputs['2'].images[0] === 'bystander'
  }

  // core's `executed` replaces the entry with just the new ref; the onExecuted
  // merge (installed by attach) puts one shared identity back in the same tick
  grid.attach(nested)
  app.nodeOutputs[key] = { images: [R('c.png')] }
  const clobbered = coreWouldRerender(nested)
  nested.onExecuted({ images: [R('c.png')] })
  const afterMerge = {
    clobberedBefore: clobbered,
    allThree: nested.images.map((r) => r.filename),
    coreWouldRerender: coreWouldRerender(nested),
    storeIdentity: app.nodeOutputs[key].images === nested.images
  }

  grid.setNodeImagesFromRefs(nested, [])
  const afterEmpty = {
    nestedEntryDeleted: !(key in app.nodeOutputs),
    bystanderUntouched: app.nodeOutputs['2'].images[0] === 'bystander'
  }

  // refusals: never guess a key
  const refuse = (mutate) => {
    const root2 = new FakeGraph()
    app.graph = root2
    app.nodeOutputs = {}
    const S2 = root2.add(new FakeSubgraphNode({ id: 3, name: 'W', rootGraph: root2 }))
    S2.subgraph.id = UUID(78)
    S2.subgraph.isRootGraph = false
    const node = S2.subgraph.add(new FakeGrid({ id: 2, uuid: UUID(32) }))
    mutate(S2.subgraph, node, root2)
    grid.setNodeImagesFromRefs(node, [R('a.png')])
    return Object.keys(app.nodeOutputs)
  }
  const refusals = {
    nonUuidSubgraphId: refuse((g) => { g.id = 'not-a-uuid' }),
    staleWorkflow: refuse((g) => { g.rootGraph = new FakeGraph() }),
    idWithColon: refuse((_g, n) => { n.id = '3:2' }),
    notASubgraph: refuse((g) => { delete g.isRootGraph }),
    control: refuse(() => {})
  }
  out.store = { key, afterSet, afterMerge, afterEmpty, refusals }

  // the repaint goes through the NODE's own graph (the root canvas is detached
  // while a subgraph is on screen)
  reset()
  const root3 = new FakeGraph()
  app.graph = root3
  const S3 = root3.add(new FakeSubgraphNode({ id: 3, name: 'W', rootGraph: root3 }))
  const repaintNode = S3.subgraph.add(new FakeGrid({ id: 2, uuid: UUID(33) }))
  grid.attach(repaintNode)
  const clear = repaintNode.widgets.find((w) => w.name === 'Clear')
  const before = { sub: S3.subgraph.dirty, root: root3.dirty }
  await clear.callback()
  await flush()
  out.repaint = {
    subgraphDirtied: S3.subgraph.dirty - before.sub,
    rootDirtied: root3.dirty - before.root
  }
}

// ---------------------------------------------------------------------------
// 5. Collect-only link dimming across the subgraph boundary
// ---------------------------------------------------------------------------
const DIM = 'rgba(128,128,128,0.25)'
const OWN = '__epsLinkColorOwner'
const GRID_OWNER = 'EPSImageGrid:collect-only'
const HOOK = '__epsResyncLinkColors'
const setMode = (node, value) => {
  node.widgets.find((w) => w.name === 'mode').value = value
}
{
  // 5a. root grid -> SubgraphNode input -> inner reader
  reset()
  const root = new FakeGraph()
  app.graph = root
  const G = root.add(new FakeGrid({ id: 1, uuid: UUID(40), mode: 'Collect only' }))
  const S = root.add(
    new FakeSubgraphNode({ id: 2, name: 'W', inputs: ['x', 'y'], outputs: ['o'], rootGraph: root })
  )
  let resyncs = 0
  const reader = S.subgraph.add(new FakeNode({ id: 5, type: 'Sink', inputs: ['i'] }))
  reader[HOOK] = () => { resyncs++ }
  const bystander = S.subgraph.add(new FakeNode({ id: 6, type: 'Sink', inputs: ['i'] }))
  const outer = wire(root, G, 0, S, 0)
  const inner = wire(S.subgraph, INPUT, 0, reader, 0)
  const unrelated = wire(S.subgraph, INPUT, 1, bystander, 0)
  inner.color = 'rgb(1,2,3)' // e.g. a colour distributor.js had already painted
  unrelated.color = 'rgb(9,9,9)'
  unrelated[OWN] = 'someone-else'
  const dirty0 = root.dirty + S.subgraph.dirty

  grid.reconcileLinkDimming(G)
  const dimmed = {
    outer: outer.color === DIM,
    inner: inner.color === DIM,
    innerOwned: inner[OWN] === GRID_OWNER,
    unrelatedUntouched: unrelated.color === 'rgb(9,9,9)' && unrelated[OWN] === 'someone-else',
    resyncsWhileDimming: resyncs,
    tracked: G.__epsGridDimmedNestedLinks.size
  }
  const dirtyAfterDim = root.dirty + S.subgraph.dirty - dirty0
  grid.reconcileLinkDimming(G) // nothing moved: change-gated
  const noopRedirty = root.dirty + S.subgraph.dirty - dirty0 - dirtyAfterDim

  setMode(G, 'Collect')
  grid.reconcileLinkDimming(G)
  out.dimOutsideIn = {
    dimmed,
    dirtyAfterDim,
    noopRedirty,
    restoredOuter: outer.color === undefined && !(OWN in outer),
    restoredInnerExactly: inner.color === 'rgb(1,2,3)' && !(OWN in inner),
    unrelatedStillUntouched: unrelated.color === 'rgb(9,9,9)' && unrelated[OWN] === 'someone-else',
    resyncsAfterRestore: resyncs,
    trackedAfter: G.__epsGridDimmedNestedLinks.size
  }

  // 5a-2. someone else took the continuation link over while it was dimmed:
  // the restore leaves their colour and tag alone (same rule as the flat path)
  setMode(G, 'Collect only')
  grid.reconcileLinkDimming(G)
  inner[OWN] = 'distributor-took-it'
  inner.color = 'rgb(5,5,5)'
  resyncs = 0
  setMode(G, 'Collect')
  grid.reconcileLinkDimming(G)
  out.dimTakenOver = {
    colourKept: inner.color === 'rgb(5,5,5)',
    tagKept: inner[OWN] === 'distributor-took-it',
    resyncs,
    dropped: G.__epsGridDimmedNestedLinks.size === 0
  }

  // 5a-3. the user cuts the wire grid -> subgraph: the inner link (still
  // there) is let go, and the reader's resync hook fires once
  delete inner[OWN]
  inner.color = 'rgb(1,2,3)'
  setMode(G, 'Collect only')
  grid.reconcileLinkDimming(G)
  const dimmedAgain = inner.color === DIM
  resyncs = 0
  G.outputs[0].links = []
  root.links.delete(outer.id)
  grid.reconcileLinkDimming(G)
  out.dimCutOuterWire = {
    dimmedAgain,
    innerRestored: inner.color === 'rgb(1,2,3)' && !(OWN in inner),
    resyncs
  }

  // 5a-4. a wire removed INSIDE the subgraph while dimmed: dropped quietly,
  // never a resync, never an error
  reset()
  const root2 = new FakeGraph()
  app.graph = root2
  const G2 = root2.add(new FakeGrid({ id: 1, uuid: UUID(41), mode: 'Collect only' }))
  const S2 = root2.add(new FakeSubgraphNode({ id: 2, name: 'W', inputs: ['x'], rootGraph: root2 }))
  let resyncs2 = 0
  const reader2 = S2.subgraph.add(new FakeNode({ id: 5, type: 'Sink', inputs: ['i'] }))
  reader2[HOOK] = () => { resyncs2++ }
  wire(root2, G2, 0, S2, 0)
  const inner2 = wire(S2.subgraph, INPUT, 0, reader2, 0)
  grid.reconcileLinkDimming(G2)
  const was = inner2.color === DIM
  S2.subgraph.links.delete(inner2.id)
  reader2.inputs[0].link = null
  grid.reconcileLinkDimming(G2)
  out.dimInnerWireRemoved = {
    was,
    dropped: G2.__epsGridDimmedNestedLinks.size === 0,
    resyncs: resyncs2
  }
}
{
  // 5b. a grid INSIDE a subgraph: its output leaves through the output node
  reset()
  const root = new FakeGraph()
  app.graph = root
  const S = root.add(new FakeSubgraphNode({ id: 3, name: 'W', outputs: ['y'], rootGraph: root }))
  const sink = root.add(new FakeNode({ id: 9, type: 'Sink', inputs: ['i'] }))
  let resyncs = 0
  sink[HOOK] = () => { resyncs++ }
  const N = S.subgraph.add(new FakeGrid({ id: 2, uuid: UUID(42), mode: 'Collect only' }))
  const innerLink = wire(S.subgraph, N, 0, OUTPUT, 0)
  const outerLink = wire(root, S, 0, sink, 0)
  outerLink.color = 'rgb(7,7,7)'
  grid.reconcileLinkDimming(N)
  const dimmed = { inner: innerLink.color === DIM, outer: outerLink.color === DIM }
  // onRemoved's forceUndim: the node's own outputs are already torn down, the
  // tracked link OBJECTS are what release the outer wire
  N.outputs.forEach((o) => { o.links = [] })
  grid.reconcileLinkDimming(N, { forceUndim: true })
  out.dimInsideOut = {
    dimmed,
    outerRestoredExactly: outerLink.color === 'rgb(7,7,7)' && !(OWN in outerLink),
    innerRestored: innerLink.color === undefined,
    resyncs
  }

  // 5b-2. the mode callback and onConnectionsChange / onConfigure hooks
  reset()
  const root2 = new FakeGraph()
  app.graph = root2
  const S2 = root2.add(new FakeSubgraphNode({ id: 3, name: 'W', outputs: ['y'], rootGraph: root2 }))
  const sink2 = root2.add(new FakeNode({ id: 9, type: 'Sink', inputs: ['i'] }))
  const N2 = S2.subgraph.add(new FakeGrid({ id: 2, uuid: UUID(43), mode: 'Collect' }))
  wire(S2.subgraph, N2, 0, OUTPUT, 0)
  const outer2 = wire(root2, S2, 0, sink2, 0)
  grid.attach(N2)
  const mode = N2.widgets.find((w) => w.name === 'mode')
  const steps = []
  steps.push(outer2.color === undefined)
  mode.value = 'Collect only'
  mode.callback(mode.value)
  steps.push(outer2.color === DIM) // the mode combo's own callback
  mode.value = 'Collect'
  mode.callback(mode.value)
  steps.push(outer2.color === undefined)
  mode.value = 'Collect only'
  N2.onConfigure({}) // load / undo / paste restore
  steps.push(outer2.color === DIM)
  out.dimHooks = { steps }

  // 5b-3. loadedGraphNode re-derives after the WHOLE graph is configured (a
  // nested grid's own onConfigure runs before the root's nodes exist)
  reset()
  const root3 = new FakeGraph()
  app.graph = root3
  const S3 = root3.add(new FakeSubgraphNode({ id: 3, name: 'W', outputs: ['y'], rootGraph: root3 }))
  const sink3 = root3.add(new FakeNode({ id: 9, type: 'Sink', inputs: ['i'] }))
  const N3 = S3.subgraph.add(new FakeGrid({ id: 2, uuid: UUID(44), mode: 'Collect only' }))
  wire(S3.subgraph, N3, 0, OUTPUT, 0)
  const outer3 = wire(root3, S3, 0, sink3, 0)
  const before = outer3.color
  grid.loadedGraphNode(N3)
  await flush()
  out.dimLoaded = { before: before === undefined, after: outer3.color === DIM }
}
{
  // 5c. two levels deep: grid -> S1 -> (inner link) -> S2 -> (inner link) -> leaf
  reset()
  const root = new FakeGraph()
  app.graph = root
  const G = root.add(new FakeGrid({ id: 1, uuid: UUID(45), mode: 'Collect only' }))
  const S1 = root.add(new FakeSubgraphNode({ id: 2, name: 'A', inputs: ['x'], rootGraph: root }))
  const S2 = S1.subgraph.add(
    new FakeSubgraphNode({ id: 3, name: 'B', inputs: ['x'], rootGraph: root })
  )
  let resyncs = 0
  const leaf = S2.subgraph.add(new FakeNode({ id: 4, type: 'Leaf', inputs: ['i'] }))
  leaf[HOOK] = () => { resyncs++ }
  const l0 = wire(root, G, 0, S1, 0)
  const l1 = wire(S1.subgraph, INPUT, 0, S2, 0)
  const l2 = wire(S2.subgraph, INPUT, 0, leaf, 0)
  grid.reconcileLinkDimming(G)
  const dimmed = [l0, l1, l2].map((l) => l.color === DIM)
  setMode(G, 'Emit')
  grid.reconcileLinkDimming(G)
  out.dimTwoLevels = {
    dimmed,
    restored: [l0, l1, l2].map((l) => l.color === undefined && !(OWN in l)),
    resyncs
  }
}
{
  // 5d. one definition shared by two SubgraphNodes
  reset()
  const root = new FakeGraph()
  app.graph = root
  const G = root.add(new FakeGrid({ id: 1, uuid: UUID(46), mode: 'Collect only' }))
  const other = root.add(new FakeNode({ id: 7, type: 'Src', outputs: ['o'] }))
  const S1 = root.add(new FakeSubgraphNode({ id: 2, name: 'W', inputs: ['x'], rootGraph: root }))
  const S2 = root.add(new FakeSubgraphNode({ id: 3, name: 'W', inputs: ['x'], rootGraph: root }))
  S2.subgraph = S1.subgraph
  const reader = S1.subgraph.add(new FakeNode({ id: 5, type: 'Sink', inputs: ['i'] }))
  const outer1 = wire(root, G, 0, S1, 0)
  const outer2 = wire(root, other, 0, S2, 0)
  const sharedInner = wire(S1.subgraph, INPUT, 0, reader, 0)
  grid.reconcileLinkDimming(G)
  out.dimSharedDefinitionFeed = {
    ownOuterWireDimmed: outer1.color === DIM,
    otherInstanceWireUntouched: outer2.color === undefined,
    sharedInnerLeftAlone: sharedInner.color === undefined && !(OWN in sharedInner),
    nothingTracked: G.__epsGridDimmedNestedLinks === undefined
  }

  // a grid INSIDE the shared definition: both instances' OUTER wires are the
  // grid's own consequence, so both dim
  reset()
  const root2 = new FakeGraph()
  app.graph = root2
  const A = root2.add(new FakeSubgraphNode({ id: 2, name: 'W', outputs: ['y'], rootGraph: root2 }))
  const B = root2.add(new FakeSubgraphNode({ id: 3, name: 'W', outputs: ['y'], rootGraph: root2 }))
  B.subgraph = A.subgraph
  const c1 = root2.add(new FakeNode({ id: 8, type: 'Sink', inputs: ['i'] }))
  const c2 = root2.add(new FakeNode({ id: 9, type: 'Sink', inputs: ['i'] }))
  const N = A.subgraph.add(new FakeGrid({ id: 4, uuid: UUID(47), mode: 'Collect only' }))
  wire(A.subgraph, N, 0, OUTPUT, 0)
  const o1 = wire(root2, A, 0, c1, 0)
  const o2 = wire(root2, B, 0, c2, 0)
  grid.reconcileLinkDimming(N)
  const both = [o1.color === DIM, o2.color === DIM]
  setMode(N, 'Collect')
  grid.reconcileLinkDimming(N)
  out.dimSharedDefinitionGrid = {
    both,
    restored: [o1.color === undefined, o2.color === undefined]
  }
}
{
  // 5e. flat wiring: nothing nested is allocated or resolved
  reset()
  const root = new FakeGraph()
  app.graph = root
  const G = root.add(new FakeGrid({ id: 1, uuid: UUID(48), mode: 'Collect only' }))
  const T = root.add(new FakeNode({ id: 2, type: 'Sink', inputs: ['i'] }))
  const link = wire(root, G, 0, T, 0)
  grid.reconcileLinkDimming(G)
  const dimmed = link.color === DIM
  setMode(G, 'Collect')
  grid.reconcileLinkDimming(G)
  out.dimFlat = {
    dimmed,
    restored: link.color === undefined && !(OWN in link),
    nestedStateNeverAllocated: G.__epsGridDimmedNestedLinks === undefined
  }
}

console.log = realLog
console.warn = realWarn
out.warns = warns
console.log(JSON.stringify(out))
