// Node probe for EPS Frame Saver's NESTED wired-video walk (v1.2.0 nested
// reach, owner ask 2026-10-03, FORMAT.md §6.7 / §7.10). Driven by
// tests/test_frame_saver_nested_js.py through tests/nested_layout.py (the
// served layout copies the real frame_saver.js + lora_library/api.js + this
// fake nested litegraph).
//
// `resolveWiredVideo(saver)` is what `fullResync` calls to decide the source
// the preview/probe/overlay branch on: null (unwired) | {kind:'input_ref',
// ref, title} (a statically knowable core LoadVideo) | {kind:'opaque',
// title}. Each scenario builds a fresh graph, points the app stub's `graph`
// at its ROOT (what ComfyUI's `app.graph` is) and resolves the saver's wire.

import { FakeGraph, FakeNode, FakeSubgraphNode, wire, INPUT, OUTPUT } from './nested_graph.mjs'
import { app } from './scripts/app.js'
import * as fs from './extensions/comfyui-epsnodes/eps_image/frame_saver.js'

// ---- tiny builders -------------------------------------------------------
const loadVideo = (id, file, title = 'Load Video') =>
  new FakeNode({
    id,
    type: 'LoadVideo',
    title,
    outputs: ['VIDEO'],
    widgets: [{ name: 'file', value: file }]
  })
const maker = (id, title = 'Video Maker') =>
  new FakeNode({ id, type: 'SomeVideoMaker', title, outputs: ['VIDEO'] })
const saver = (id) => new FakeNode({ id, type: 'EPSFrameSaver', inputs: ['video'], outputs: ['image'] })
const reroute = (id, type = 'Reroute') => new FakeNode({ id, type, inputs: [''], outputs: [''] })
const sub = (root, id, name = 'Wrapper', ins = ['x'], outs = ['y']) =>
  root.add(new FakeSubgraphNode({ id, name, inputs: ins, outputs: outs, rootGraph: root }))
const live = (root) => {
  app.graph = root
  return root
}
const resolve = (node) => fs.resolveWiredVideo(node)

const out = { exported: typeof fs.resolveWiredVideo === 'function' }

// ---- A. flat graph, live root: byte-identical answers ---------------------
{
  const root = live(new FakeGraph())
  const L = root.add(loadVideo(1, 'clip.mp4'))
  const S = root.add(saver(2))
  wire(root, L, 0, S, 0)
  out.flatLoadVideo = resolve(S)
}
{
  const root = live(new FakeGraph())
  const L = root.add(loadVideo(1, 'clip.mp4'))
  const R1 = root.add(reroute(2))
  const R2 = root.add(reroute(3, 'Reroute (rgthree)'))
  const S = root.add(saver(4))
  wire(root, L, 0, R1, 0)
  wire(root, R1, 0, R2, 0)
  wire(root, R2, 0, S, 0)
  out.flatThroughReroutes = resolve(S)
}
{
  const root = live(new FakeGraph())
  const M = root.add(maker(1))
  const S = root.add(saver(2))
  wire(root, M, 0, S, 0)
  out.flatOpaque = resolve(S)
}
{
  const root = live(new FakeGraph())
  const L = root.add(loadVideo(1, '   '))
  const S = root.add(saver(2))
  wire(root, L, 0, S, 0)
  out.flatLoadVideoNoFile = resolve(S)
}
{
  const root = live(new FakeGraph())
  const S = root.add(saver(2))
  out.flatUnwired = resolve(S)
  const noInput = root.add(new FakeNode({ id: 3, type: 'EPSFrameSaver', inputs: [] }))
  out.noVideoInput = resolve(noInput)
}
{
  // a flat LoadVideo whose `file` socket is driven by a primitive: statically
  // knowable exactly as before (PrimitiveNode keeps the target widget in sync)
  const root = live(new FakeGraph())
  const P = root.add(new FakeNode({ id: 1, type: 'PrimitiveNode', outputs: ['STRING'] }))
  const L = root.add(
    new FakeNode({
      id: 2,
      type: 'LoadVideo',
      title: 'Load Video',
      inputs: [{ name: 'file', widget: { name: 'file' } }],
      outputs: ['VIDEO'],
      widgets: [{ name: 'file', value: 'prim.mp4' }]
    })
  )
  const S = root.add(saver(3))
  wire(root, P, 0, L, 0)
  wire(root, L, 0, S, 0)
  out.flatPrimitiveDrivenFile = resolve(S)
}

// ---- B. a LoadVideo INSIDE a subgraph, the saver outside -------------------
{
  const root = live(new FakeGraph())
  const W = sub(root, 10)
  const L = W.subgraph.add(loadVideo(1, 'inner.mp4'))
  wire(W.subgraph, L, 0, OUTPUT, 0)
  const S = root.add(saver(5))
  wire(root, W, 0, S, 0)
  out.loadVideoInside = { native: S.getInputNode(0)?.type, resolved: resolve(S) }
}

// ---- C. a LoadVideo OUTSIDE feeding a subgraph input the saver reads --------
{
  const root = live(new FakeGraph())
  const L = root.add(loadVideo(1, 'outer.mp4'))
  const W = sub(root, 10)
  const S = W.subgraph.add(saver(2))
  wire(root, L, 0, W, 0)
  wire(W.subgraph, INPUT, 0, S, 0)
  out.outsideThroughInput = { native: S.getInputNode(0), resolved: resolve(S) }
}

// ---- D. reroutes straddling the boundary -----------------------------------
{
  // LoadVideo -> Reroute | boundary | Reroute -> saver
  const root = live(new FakeGraph())
  const L = root.add(loadVideo(1, 'x.mp4'))
  const R1 = root.add(reroute(2))
  const W = sub(root, 10)
  const R2 = W.subgraph.add(reroute(3))
  const S = W.subgraph.add(saver(4))
  wire(root, L, 0, R1, 0)
  wire(root, R1, 0, W, 0)
  wire(W.subgraph, INPUT, 0, R2, 0)
  wire(W.subgraph, R2, 0, S, 0)
  out.rerouteAcrossIn = resolve(S)
}
{
  // LoadVideo -> Reroute -> | boundary out | -> Reroute -> saver
  const root = live(new FakeGraph())
  const W = sub(root, 10)
  const L = W.subgraph.add(loadVideo(1, 'y.mp4'))
  const R1 = W.subgraph.add(reroute(2))
  wire(W.subgraph, L, 0, R1, 0)
  wire(W.subgraph, R1, 0, OUTPUT, 0)
  const R2 = root.add(reroute(3))
  const S = root.add(saver(4))
  wire(root, W, 0, R2, 0)
  wire(root, R2, 0, S, 0)
  out.rerouteAcrossOut = resolve(S)
}
{
  // two levels deep, saver innermost
  const root = live(new FakeGraph())
  const L = root.add(loadVideo(1, 'deep.mp4'))
  const W1 = sub(root, 10, 'Outer')
  const W2 = W1.subgraph.add(
    new FakeSubgraphNode({ id: 20, name: 'Inner', inputs: ['x'], outputs: ['y'], rootGraph: root })
  )
  const S = W2.subgraph.add(saver(30))
  wire(root, L, 0, W1, 0)
  wire(W1.subgraph, INPUT, 0, W2, 0)
  wire(W2.subgraph, INPUT, 0, S, 0)
  out.twoLevelsIn = resolve(S)
}
{
  // two levels deep, LoadVideo innermost; title carries the whole trail
  const root = live(new FakeGraph())
  const W1 = sub(root, 10, 'Outer')
  const W2 = W1.subgraph.add(
    new FakeSubgraphNode({ id: 20, name: 'Inner', inputs: ['x'], outputs: ['y'], rootGraph: root })
  )
  const L = W2.subgraph.add(loadVideo(1, 'deepout.mp4'))
  wire(W2.subgraph, L, 0, OUTPUT, 0)
  wire(W1.subgraph, W2, 0, OUTPUT, 0)
  const S = root.add(saver(5))
  wire(root, W1, 0, S, 0)
  out.twoLevelsOut = resolve(S)
}

{
  // a subgraph that merely forwards its input to its output
  const root = live(new FakeGraph())
  const L = root.add(loadVideo(1, 'fwd.mp4'))
  const W = sub(root, 10)
  wire(W.subgraph, INPUT, 0, OUTPUT, 0)
  const S = root.add(saver(5))
  wire(root, L, 0, W, 0)
  wire(root, W, 0, S, 0)
  out.forwardingSubgraph = resolve(S)
}

// ---- E. an opaque (non-LoadVideo) source across the boundary ---------------
{
  const root = live(new FakeGraph())
  const M = root.add(maker(1))
  const W = sub(root, 10)
  const S = W.subgraph.add(saver(2))
  wire(root, M, 0, W, 0)
  wire(W.subgraph, INPUT, 0, S, 0)
  out.opaqueOutsideIn = resolve(S)
}
{
  const root = live(new FakeGraph())
  const W = sub(root, 10)
  const M = W.subgraph.add(maker(1))
  wire(W.subgraph, M, 0, OUTPUT, 0)
  const S = root.add(saver(5))
  wire(root, W, 0, S, 0)
  out.opaqueInsideOut = resolve(S)
}

// ---- F. ONE definition, TWO instances ----------------------------------------
const sharedWorld = (srcA, srcB, innerKind = 'saverInside') => {
  const root = live(new FakeGraph())
  const Wa = sub(root, 10, 'Shared')
  const Wb = sub(root, 11, 'Shared')
  Wb.subgraph = Wa.subgraph // one definition, two SubgraphNode instances
  const D = Wa.subgraph
  const a = root.add(srcA)
  const b = srcB === srcA ? a : root.add(srcB)
  wire(root, a, 0, Wa, 0)
  wire(root, b, 0, Wb, 0)
  let S = null
  if (innerKind === 'saverInside') {
    S = D.add(saver(5))
    wire(D, INPUT, 0, S, 0)
  } else {
    const R = D.add(reroute(3))
    wire(D, INPUT, 0, R, 0)
    wire(D, R, 0, OUTPUT, 0)
  }
  return { root, Wa, Wb, D, S }
}
out.sharedDifferentVideos = resolve(sharedWorld(loadVideo(1, 'a.mp4'), loadVideo(2, 'b.mp4')).S)
out.sharedSameFileTwoNodes = resolve(
  sharedWorld(loadVideo(1, 'same.mp4', 'LV A'), loadVideo(2, 'same.mp4', 'LV B')).S
)
{
  const one = loadVideo(1, 'one.mp4')
  out.sharedSameSingleSource = resolve(sharedWorld(one, one).S)
}
out.sharedKnownVersusOpaque = resolve(sharedWorld(loadVideo(1, 'a.mp4'), maker(2)).S)
out.sharedTwoOpaqueSameTitle = resolve(sharedWorld(maker(1, 'Gen'), maker(2, 'Gen')).S)
out.sharedTwoOpaqueDifferentTitles = resolve(sharedWorld(maker(1, 'GenA'), maker(2, 'GenB')).S)
{
  // LANE EXACTNESS: a reroute inside the shared definition forwards each
  // instance's own outer video out; the savers on the outside must each see
  // ONLY their own instance's file, not both (that would read ambiguous).
  const w = sharedWorld(loadVideo(1, 'a.mp4'), loadVideo(2, 'b.mp4'), 'rerouteInside')
  const Sa = w.root.add(saver(40))
  const Sb = w.root.add(saver(41))
  wire(w.root, w.Wa, 0, Sa, 0)
  wire(w.root, w.Wb, 0, Sb, 0)
  out.sharedLaneExact = { viaA: resolve(Sa), viaB: resolve(Sb) }
}

// ---- G. a promoted `file` widget is not statically knowable -------------------
{
  // The interior LoadVideo's `file` socket is fed by its subgraph's INPUT node
  // (core keeps a COPY of the value host-side), so the interior widget's value
  // is stale by construction -> opaque, never that stale file.
  const root = live(new FakeGraph())
  const W = sub(root, 10)
  const L = W.subgraph.add(
    new FakeNode({
      id: 1,
      type: 'LoadVideo',
      title: 'Load Video',
      inputs: [{ name: 'file', widget: { name: 'file' } }],
      outputs: ['VIDEO'],
      widgets: [{ name: 'file', value: 'stale-default.mp4' }]
    })
  )
  wire(W.subgraph, INPUT, 0, L, 0)
  wire(W.subgraph, L, 0, OUTPUT, 0)
  const S = root.add(saver(5))
  wire(root, W, 0, S, 0)
  out.promotedFileWidget = resolve(S)
}

// ---- H. dangling boundaries read as unwired -----------------------------------
{
  const root = live(new FakeGraph())
  const W = sub(root, 10)
  const S = W.subgraph.add(saver(2))
  wire(W.subgraph, INPUT, 0, S, 0) // nothing feeds W.in0 outside
  out.danglingInput = resolve(S)
}
{
  const root = live(new FakeGraph())
  const W = sub(root, 10) // no inner link into the subgraph OUTPUT
  const S = root.add(saver(5))
  wire(root, W, 0, S, 0)
  out.danglingOutput = resolve(S)
}

// ---- I. the 32-hop loop guard and '(reroute loop)' ----------------------------
const chain = (n, liveRoot) => {
  const root = liveRoot ? live(new FakeGraph()) : new FakeGraph()
  if (!liveRoot) app.graph = null
  let up = root.add(loadVideo(1, 'far.mp4'))
  for (let i = 0; i < n; i++) {
    const r = root.add(reroute(100 + i))
    wire(root, up, 0, r, 0)
    up = r
  }
  const S = root.add(saver(5))
  wire(root, up, 0, S, 0)
  return resolve(S)
}
out.chain31Live = chain(31, true)
out.chain32Live = chain(32, true)
out.chain31Legacy = chain(31, false)
out.chain32Legacy = chain(32, false)
{
  const root = live(new FakeGraph())
  const R1 = root.add(reroute(1))
  const R2 = root.add(reroute(2))
  const S = root.add(saver(3))
  wire(root, R2, 0, R1, 0)
  wire(root, R1, 0, R2, 0)
  wire(root, R1, 0, S, 0)
  out.rerouteLoop = resolve(S)
}

// ---- J. fallback to the single-graph read ---------------------------------------
{
  // app.graph absent: the pre-v1.2.0 read answers, byte for byte
  const root = new FakeGraph()
  app.graph = null
  const L = root.add(loadVideo(1, 'legacy.mp4'))
  const S = root.add(saver(2))
  wire(root, L, 0, S, 0)
  out.legacyFlat = resolve(S)
}
{
  // ...and across a boundary it stays blind (the very gap the walk closes)
  const root = new FakeGraph()
  app.graph = null
  const L = root.add(loadVideo(1, 'legacy.mp4'))
  const W = sub(root, 10)
  const S = W.subgraph.add(saver(2))
  wire(root, L, 0, W, 0)
  wire(W.subgraph, INPUT, 0, S, 0)
  out.legacyNestedBlind = resolve(S)
}
{
  // a bare fake whose graph is a plain-object link table (an older shape),
  // with a live root it is not part of
  live(new FakeGraph())
  const L = loadVideo(1, 'plain.mp4')
  const graph = { links: { 7: { origin_id: 1 } }, getNodeById: (id) => (id === 1 ? L : null) }
  const S = { id: 2, inputs: [{ name: 'video', link: 7 }], graph: { ...graph, rootGraph: null } }
  out.bareFakeObjectLinks = resolve(S)
}

process.stdout.write(JSON.stringify(out) + '\n')
