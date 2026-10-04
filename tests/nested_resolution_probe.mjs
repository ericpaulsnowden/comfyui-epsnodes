// Node probe for EPS Resolution's NESTED incoming-size walk (v1.2.0 nested
// reach, owner ask 2026-10-03, FORMAT.md §6.5 / §7.10). Driven by
// tests/test_resolution_passthrough_js.py::TestNestedIncomingSizeWalk through
// tests/nested_layout.py (the served layout copies the real resolution.js +
// lora_library/api.js + this fake nested litegraph).
//
// Every scenario builds a fresh graph with the shared fake (nested_graph.mjs),
// points the app stub's `graph` at its ROOT (what ComfyUI's `app.graph` is),
// and reads `collectIncomingImageSizes(R)` -- the same walk the source line,
// `copy from image` and the readout height all share.

import { FakeGraph, FakeNode, FakeSubgraphNode, wire, INPUT, OUTPUT } from './nested_graph.mjs'
import { app } from './scripts/app.js'
import * as m from './extensions/comfyui-epsnodes/eps_image/resolution.js'

// ---- tiny builders -------------------------------------------------------
const wall = (id, w, h) => {
  const n = new FakeNode({ id, type: 'LoadImage', outputs: ['IMAGE'] })
  n.imgs = [{ naturalWidth: w, naturalHeight: h }]
  return n
}
const resolutionNode = (id) =>
  new FakeNode({ id, type: 'EPSResolution', inputs: ['image'], outputs: ['image'] })
const reroute = (id, type = 'Reroute') =>
  new FakeNode({ id, type, inputs: [''], outputs: [''] })
const distributor = (id) =>
  new FakeNode({ id, type: 'EPSDistributor', inputs: ['image'], outputs: ['image_1'] })
const switcher = (id, n, toggles) =>
  new FakeNode({
    id,
    type: 'EPSSwitcher',
    inputs: Array.from({ length: n }, (_, i) => `image_${i + 1}`),
    outputs: ['image'],
    widgets: toggles === undefined ? [] : [{ name: 'toggles', value: toggles }]
  })
const sub = (root, id, name = 'Sub', ins = ['x'], outs = ['y']) =>
  root.add(new FakeSubgraphNode({ id, name, inputs: ins, outputs: outs, rootGraph: root }))

const live = (root) => {
  app.graph = root
  return root
}
const sizes = (node) => m.collectIncomingImageSizes(node)
const summary = (node) => m.summarizeIncomingSizes(m.collectIncomingImageSizes(node))
const wh = (list) => list.map((s) => `${s.width}x${s.height}`)

const out = {}

// ---- A. image comes OUT of a subgraph (the SubgraphNode is the native upstream)
{
  const root = live(new FakeGraph())
  const S = sub(root, 10)
  const W = S.subgraph.add(wall(1, 800, 600))
  wire(S.subgraph, W, 0, OUTPUT, 0)
  const R = root.add(resolutionNode(5))
  wire(root, S, 0, R, 0)
  out.outOfSubgraph = {
    nativeIsSubgraphNode: R.getInputNode(0) === S,
    sizes: sizes(R),
    summary: summary(R),
    sourceLine: m.sourceLineForSummary(summary(R))
  }
}

// ---- B. image goes IN through a subgraph input node (native upstream: null)
{
  const root = live(new FakeGraph())
  const A = root.add(wall(1, 640, 480))
  const S = sub(root, 10)
  const R = S.subgraph.add(resolutionNode(2))
  wire(root, A, 0, S, 0)
  wire(S.subgraph, INPUT, 0, R, 0)
  out.inThroughInput = { nativeIsNull: R.getInputNode(0) === null, sizes: sizes(R) }
}

// ---- C. a switcher INSIDE a subgraph, fed from outside through two inputs
for (const [label, toggles] of [
  ['switcherInside', undefined],
  ['switcherInsideOneDisabled', '{"image_2": false}']
]) {
  const root = live(new FakeGraph())
  const A = root.add(wall(1, 640, 480))
  const B = root.add(wall(2, 320, 240))
  const S = sub(root, 10, 'Sub', ['a', 'b'], ['y'])
  const SW = S.subgraph.add(switcher(3, 2, toggles))
  const R = S.subgraph.add(resolutionNode(4))
  wire(root, A, 0, S, 0)
  wire(root, B, 0, S, 1)
  wire(S.subgraph, INPUT, 0, SW, 0)
  wire(S.subgraph, INPUT, 1, SW, 1)
  wire(S.subgraph, SW, 0, R, 0)
  out[label] = summary(R)
}

// ---- D. two levels of nesting, in both directions + pass-through nodes
{
  // IN: A -> S1.in ; S1: INPUT -> S2.in ; S2: INPUT -> R
  const root = live(new FakeGraph())
  const A = root.add(wall(1, 500, 400))
  const S1 = sub(root, 10)
  const S2 = S1.subgraph.add(
    new FakeSubgraphNode({ id: 20, name: 'Inner', inputs: ['x'], outputs: ['y'], rootGraph: root })
  )
  const R = S2.subgraph.add(resolutionNode(30))
  wire(root, A, 0, S1, 0)
  wire(S1.subgraph, INPUT, 0, S2, 0)
  wire(S2.subgraph, INPUT, 0, R, 0)
  out.twoLevelIn = sizes(R)
}
{
  // OUT: R(root) <- S1.out <- S1: S2.out <- S2: wall
  const root = live(new FakeGraph())
  const S1 = sub(root, 10)
  const S2 = S1.subgraph.add(
    new FakeSubgraphNode({ id: 20, name: 'Inner', inputs: ['x'], outputs: ['y'], rootGraph: root })
  )
  const W = S2.subgraph.add(wall(7, 123, 456))
  wire(S2.subgraph, W, 0, OUTPUT, 0)
  wire(S1.subgraph, S2, 0, OUTPUT, 0)
  const R = root.add(resolutionNode(5))
  wire(root, S1, 0, R, 0)
  out.twoLevelOut = sizes(R)
}
{
  // Three boundary crossings must not spend the pass-through depth budget:
  // R <- S1 <- S2 <- S3 <- wall (depth cap is 8 pass-through hops).
  const root = live(new FakeGraph())
  const S1 = sub(root, 10)
  const S2 = S1.subgraph.add(
    new FakeSubgraphNode({ id: 20, name: 'L2', inputs: ['x'], outputs: ['y'], rootGraph: root })
  )
  const S3 = S2.subgraph.add(
    new FakeSubgraphNode({ id: 30, name: 'L3', inputs: ['x'], outputs: ['y'], rootGraph: root })
  )
  const W = S3.subgraph.add(wall(7, 77, 88))
  wire(S3.subgraph, W, 0, OUTPUT, 0)
  wire(S2.subgraph, S3, 0, OUTPUT, 0)
  wire(S1.subgraph, S2, 0, OUTPUT, 0)
  const R = root.add(resolutionNode(5))
  wire(root, S1, 0, R, 0)
  out.threeCrossings = sizes(R)
}
{
  // Pass-through nodes on BOTH sides of a boundary: A -> Reroute -> S1.in ;
  // S1: INPUT -> S2.in ; S2: INPUT -> Distributor -> R.
  const root = live(new FakeGraph())
  const A = root.add(wall(1, 500, 400))
  const RR = root.add(reroute(2))
  const S1 = sub(root, 10)
  const S2 = S1.subgraph.add(
    new FakeSubgraphNode({ id: 20, name: 'Inner', inputs: ['x'], outputs: ['y'], rootGraph: root })
  )
  const D = S2.subgraph.add(distributor(3))
  const R = S2.subgraph.add(resolutionNode(4))
  wire(root, A, 0, RR, 0)
  wire(root, RR, 0, S1, 0)
  wire(S1.subgraph, INPUT, 0, S2, 0)
  wire(S2.subgraph, INPUT, 0, D, 0)
  wire(S2.subgraph, D, 0, R, 0)
  out.passThroughsAcrossBoundaries = sizes(R)
}

{
  // A subgraph that merely forwards its input to its output (INPUT -> OUTPUT):
  // the walk goes down through the output, then straight back out the input.
  const root = live(new FakeGraph())
  const A = root.add(wall(1, 700, 300))
  const S = sub(root, 10)
  wire(S.subgraph, INPUT, 0, OUTPUT, 0)
  const R = root.add(resolutionNode(5))
  wire(root, A, 0, S, 0)
  wire(root, S, 0, R, 0)
  out.forwardingSubgraph = sizes(R)
}

// ---- E. ONE definition, TWO instances (shared inner node objects)
const sharedWorld = (sizeA, sizeB, innerKind) => {
  const root = live(new FakeGraph())
  const A = root.add(wall(1, sizeA[0], sizeA[1]))
  const B = root.add(wall(2, sizeB[0], sizeB[1]))
  const Sa = sub(root, 10, 'Shared')
  const Sb = sub(root, 11, 'Shared')
  Sb.subgraph = Sa.subgraph // one definition, two SubgraphNode instances
  const D = Sa.subgraph
  wire(root, A, 0, Sa, 0)
  wire(root, B, 0, Sb, 0)
  let R = null
  if (innerKind === 'resolutionInside') {
    R = D.add(resolutionNode(5))
    wire(D, INPUT, 0, R, 0)
  } else {
    // a switcher inside the definition forwarding the subgraph input out
    const SW = D.add(switcher(3, 1))
    wire(D, INPUT, 0, SW, 0)
    wire(D, SW, 0, OUTPUT, 0)
  }
  return { root, A, B, Sa, Sb, D, R }
}
{
  const w = sharedWorld([1024, 1024], [832, 1216], 'resolutionInside')
  out.sharedDifferent = summary(w.R)
}
{
  const w = sharedWorld([1024, 1024], [1024, 1024], 'resolutionInside')
  out.sharedSame = summary(w.R)
}
{
  // LANE EXACTNESS: two roots Resolutions, one per instance; each must see
  // ONLY its own instance's outer image, never both (re-resolving the shared
  // inner switcher by node object would fan out to both instances).
  const w = sharedWorld([1024, 1024], [832, 1216], 'switcherInside')
  const Ra = w.root.add(resolutionNode(40))
  const Rb = w.root.add(resolutionNode(41))
  wire(w.root, w.Sa, 0, Ra, 0)
  wire(w.root, w.Sb, 0, Rb, 0)
  out.sharedLaneExact = { viaA: summary(Ra), viaB: summary(Rb) }
}

// ---- F. dangling boundaries read as nothing
{
  const root = live(new FakeGraph())
  const S = sub(root, 10)
  const R = S.subgraph.add(resolutionNode(2))
  wire(S.subgraph, INPUT, 0, R, 0) // nothing feeds S.in0 outside
  out.danglingInput = sizes(R)
}
{
  const root = live(new FakeGraph())
  const S = sub(root, 10) // no inner link into the subgraph OUTPUT
  const R = root.add(resolutionNode(5))
  wire(root, S, 0, R, 0)
  out.danglingOutput = { sizes: sizes(R), summary: summary(R) }
}
{
  const root = live(new FakeGraph())
  const R = root.add(resolutionNode(5))
  out.unwiredWithLiveRoot = sizes(R)
}

// ---- G. a flat graph with a LIVE root reads exactly as before
{
  const root = live(new FakeGraph())
  const W = root.add(wall(1, 640, 480))
  const RR = root.add(reroute(2, 'Reroute (rgthree)'))
  const R = root.add(resolutionNode(5))
  wire(root, W, 0, RR, 0)
  wire(root, RR, 0, R, 0)
  out.flatReroute = sizes(R)
}
{
  const root = live(new FakeGraph())
  const A = root.add(wall(1, 1024, 1024))
  const B = root.add(wall(2, 832, 1216))
  const SW = root.add(switcher(3, 2))
  const R = root.add(resolutionNode(5))
  wire(root, A, 0, SW, 0)
  wire(root, B, 0, SW, 1)
  wire(root, SW, 0, R, 0)
  out.flatSwitcherMixed = summary(R)
}
{
  const root = live(new FakeGraph())
  const SwA = root.add(switcher('A', 1))
  const SwB = root.add(switcher('B', 1))
  const R = root.add(resolutionNode(5))
  wire(root, SwB, 0, SwA, 0)
  wire(root, SwA, 0, SwB, 0)
  wire(root, SwA, 0, R, 0)
  let threw = null
  let result = null
  try {
    result = sizes(R)
  } catch (error) {
    threw = String(error)
  }
  out.flatCycle = { threw, result }
}
{
  const root = live(new FakeGraph())
  let up = root.add(wall(1, 1234, 5678))
  for (let i = 0; i < 20; i++) {
    const hop = root.add(reroute(100 + i))
    wire(root, up, 0, hop, 0)
    up = hop
  }
  const R = root.add(resolutionNode(5))
  wire(root, up, 0, R, 0)
  out.flatDepthCap = sizes(R)
}
{
  const root = live(new FakeGraph())
  let up = root.add(wall(1, 1234, 5678))
  for (let i = 0; i < 3; i++) {
    const hop = root.add(reroute(100 + i))
    wire(root, up, 0, hop, 0)
    up = hop
  }
  const R = root.add(resolutionNode(5))
  wire(root, up, 0, R, 0)
  out.flatShortChain = sizes(R)
}

// ---- H. inner node ids may collide with root ids: the guard keys PATH ids
{
  // root wall id 1 (640x480) and the INNER wall id 1 (100x100) both feed one
  // inner switcher; keyed by node id the second would be dropped as "seen".
  const root = live(new FakeGraph())
  const A = root.add(wall(1, 640, 480))
  const S = sub(root, 10)
  const X = S.subgraph.add(wall(1, 100, 100))
  const SW = S.subgraph.add(switcher(2, 2))
  wire(root, A, 0, S, 0)
  wire(S.subgraph, INPUT, 0, SW, 0)
  wire(S.subgraph, X, 0, SW, 1)
  wire(S.subgraph, SW, 0, OUTPUT, 0)
  const R = root.add(resolutionNode(5))
  wire(root, S, 0, R, 0)
  out.idCollision = summary(R)
}

// ---- I. fallback to litegraph's own getInputNode ----------------------------
{
  // A bare fake (no `.graph`) -- every pre-v1.2.0 unit test's shape -- with a
  // LIVE app.graph: not located, so the native read answers.
  live(new FakeGraph())
  const w = wall(1, 800, 600)
  const R = {
    id: 'entry',
    inputs: [{ name: 'image', link: 1 }],
    getInputNode: (slot) => (slot === 0 ? w : null)
  }
  out.bareFakeWithLiveRoot = sizes(R)
}
{
  // app.graph absent (null): the module must not touch the resolvers at all.
  app.graph = null
  const w = wall(1, 800, 600)
  const R = {
    id: 'entry',
    inputs: [{ name: 'image', link: 1 }],
    getInputNode: (slot) => (slot === 0 ? w : null)
  }
  out.noRootAtAll = sizes(R)
}
{
  // An odd host: the node IS under the root but the resolver finds nothing
  // (link id missing from the table); litegraph's own read still answers.
  const root = live(new FakeGraph())
  const w = wall(1, 800, 600)
  const R = root.add(resolutionNode(5))
  R.inputs[0].link = 999
  R.getInputNode = () => w
  out.oddHostLinkTable = sizes(R)
}
{
  // ...but a SubgraphNode from that native read is never taken for a wall
  // when a root is live (it is the dangling-output case, not an image source).
  const root = live(new FakeGraph())
  const S = sub(root, 10)
  S.imgs = [{ naturalWidth: 50, naturalHeight: 50 }] // would read as a wall
  const R = root.add(resolutionNode(5))
  wire(root, S, 0, R, 0)
  out.subgraphNodeIsNeverAWall = sizes(R)
}

// ---- J. the FLAT case does not walk the whole graph ---------------------------
{
  // Root nodes are found in O(1); an O(1) getNodeById makes any full-graph
  // walk (walkLiveNodes reads every node's `id`) show up as decoy id reads.
  const root = live(new FakeGraph())
  const byId = new Map()
  const add = (node) => {
    root.add(node)
    byId.set(String(node.id), node)
    return node
  }
  root.getNodeById = (id) => byId.get(String(id)) || null
  let decoyReads = 0
  for (let i = 0; i < 50; i++) {
    const d = new FakeNode({ id: 1000 + i, type: 'Decoy' })
    Object.defineProperty(d, 'id', {
      get() {
        decoyReads++
        return 1000 + i
      }
    })
    root._nodes.push(d)
    d.graph = root
  }
  const W = add(wall(1, 640, 480))
  const RR = add(reroute(2))
  const SW = add(switcher(3, 1))
  const R = add(resolutionNode(5))
  wire(root, W, 0, RR, 0)
  wire(root, RR, 0, SW, 0)
  wire(root, SW, 0, R, 0)
  decoyReads = 0
  const result = sizes(R)
  out.flatNoFullWalk = { result, decoyReads }
}

process.stdout.write(JSON.stringify(out) + '\n')
