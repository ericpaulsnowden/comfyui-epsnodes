import * as api from './extensions/comfyui-epsnodes/lora_library/api.js'
import { app } from './scripts/app.js'
import { FakeGraph, FakeNode, FakeSubgraphNode, wire, INPUT, OUTPUT, simpleNested } from './nested_graph.mjs'

const ids = (list) => list.map((e) => `${e.pathId}#${e.slot}`).sort()
const out = {}

// ---- 1. the canonical crossing: A -> S -> inner -> S -> C -------------------
{
  const { root, A, S, C, inner } = simpleNested()
  out.simple = {
    // litegraph's own getInputNode stops dead at both boundaries
    nativeInner: inner.getInputNode(0),
    nativeC: C.getInputNode(0)?.type ?? null,
    innerUp: ids(api.resolveInputSources(root, inner, 0)),
    cDown: ids(api.resolveInputSources(root, C, 0)),
    aOut: ids(api.resolveOutputTargets(root, A, 0)),
    innerOut: ids(api.resolveOutputTargets(root, inner, 0)),
    // every source/target names the REAL node object, not the SubgraphNode
    cDownType: api.resolveInputSources(root, C, 0)[0].node.type,
    aOutType: api.resolveOutputTargets(root, A, 0)[0].node.type,
    pathIds: { A: api.pathIdsOfNode(root, A), inner: api.pathIdsOfNode(root, inner), S: api.pathIdsOfNode(root, S) },
    located: (() => { const l = api.locateByPathId(root, '10:2'); return { type: l.node.type, prefix: l.prefix, isSub: l.graph === S.subgraph } })(),
    locatedRoot: (() => { const l = api.locateByPathId(root, '3'); return { type: l.node.type, prefix: l.prefix, isRoot: l.graph === root } })(),
    locatedStale: api.locateByPathId(root, '10:99'),
    locatedJunk: api.locateByPathId(root, ''),
    hopsOfAOut: api.resolveOutputTargets(root, A, 0)[0].hops.length,
  }
}

// ---- 2. two levels deep -----------------------------------------------------
{
  const root = new FakeGraph()
  const A = root.add(new FakeNode({ id: 1, type: 'Src', outputs: ['o'] }))
  const outer = root.add(new FakeSubgraphNode({ id: 5, name: 'Outer', inputs: ['x'], outputs: ['y'], rootGraph: root }))
  const Z = root.add(new FakeNode({ id: 9, type: 'Sink', inputs: ['i'] }))
  const mid = outer.subgraph
  const inner = mid.add(new FakeSubgraphNode({ id: 7, name: 'Inner', inputs: ['x'], outputs: ['y'], rootGraph: root }))
  const leaf = inner.subgraph.add(new FakeNode({ id: 2, type: 'Leaf', inputs: ['i'], outputs: ['o'] }))
  wire(root, A, 0, outer, 0)
  wire(mid, INPUT, 0, inner, 0)
  wire(inner.subgraph, INPUT, 0, leaf, 0)
  wire(inner.subgraph, leaf, 0, OUTPUT, 0)
  wire(mid, inner, 0, OUTPUT, 0)
  wire(root, outer, 0, Z, 0)
  out.deep = {
    leafUp: ids(api.resolveInputSources(root, leaf, 0)),
    zDown: ids(api.resolveInputSources(root, Z, 0)),
    aOut: ids(api.resolveOutputTargets(root, A, 0)),
    leafOut: ids(api.resolveOutputTargets(root, leaf, 0)),
    leafPath: api.pathIdsOfNode(root, leaf),
    label: api.describePath(root, '5:7:2'),
    labelRoot: api.describePath(root, '1'),
    labelStale: api.describePath(root, '5:99'),
  }
}

// ---- 3. dangling boundaries degrade to nothing ------------------------------
{
  const root = new FakeGraph()
  const S = root.add(new FakeSubgraphNode({ id: 10, name: 'W', inputs: ['x'], outputs: ['y'], rootGraph: root }))
  const C = root.add(new FakeNode({ id: 3, type: 'Sink', inputs: ['in'] }))
  const inner = S.subgraph.add(new FakeNode({ id: 2, type: 'Inner', inputs: ['i'], outputs: ['o'] }))
  wire(S.subgraph, INPUT, 0, inner, 0)         // fed from an UNCONNECTED subgraph input
  wire(root, S, 0, C, 0)                        // S.y has no inner source
  out.dangling = {
    innerUp: api.resolveInputSources(root, inner, 0).length,
    cDown: api.resolveInputSources(root, C, 0).length,
    innerOut: api.resolveOutputTargets(root, inner, 0).length,
    noSuchSlot: api.resolveInputSources(root, C, 5).length,
  }
}

// ---- 4. ONE definition, TWO instances: up fans out, down stays specific ------
{
  const root = new FakeGraph()
  const A1 = root.add(new FakeNode({ id: 1, type: 'Src1', outputs: ['o'] }))
  const A2 = root.add(new FakeNode({ id: 2, type: 'Src2', outputs: ['o'] }))
  const S1 = root.add(new FakeSubgraphNode({ id: 10, name: 'W', inputs: ['x'], outputs: ['y'], rootGraph: root }))
  const S2 = root.add(new FakeSubgraphNode({ id: 11, name: 'W', inputs: ['x'], outputs: ['y'], rootGraph: root }))
  S2.subgraph = S1.subgraph                      // shared definition, like a pasted SubgraphNode
  const C1 = root.add(new FakeNode({ id: 3, type: 'Sink1', inputs: ['in'] }))
  const C2 = root.add(new FakeNode({ id: 4, type: 'Sink2', inputs: ['in'] }))
  const inner = S1.subgraph.add(new FakeNode({ id: 20, type: 'Inner', inputs: ['i'], outputs: ['o'] }))
  wire(root, A1, 0, S1, 0)
  wire(root, A2, 0, S2, 0)
  wire(S1.subgraph, INPUT, 0, inner, 0)
  wire(S1.subgraph, inner, 0, OUTPUT, 0)
  wire(root, S1, 0, C1, 0)
  wire(root, S2, 0, C2, 0)
  out.shared = {
    innerPaths: api.pathIdsOfNode(root, inner).sort(),
    innerUp: ids(api.resolveInputSources(root, inner, 0)),     // BOTH instances' sources
    innerOut: ids(api.resolveOutputTargets(root, inner, 0)),   // BOTH instances' consumers
    c1Down: api.resolveInputSources(root, C1, 0).map((e) => e.pathId),
    c2Down: api.resolveInputSources(root, C2, 0).map((e) => e.pathId),
    walked: api.walkLiveNodes(root).map((e) => e.pathId),
  }
}

// ---- 5. pass-through (subgraph input wired straight to its output) + cycle ---
{
  const root = new FakeGraph()
  const A = root.add(new FakeNode({ id: 1, type: 'Src', outputs: ['o'] }))
  const S = root.add(new FakeSubgraphNode({ id: 10, name: 'Pass', inputs: ['x'], outputs: ['y'], rootGraph: root }))
  const C = root.add(new FakeNode({ id: 3, type: 'Sink', inputs: ['in'] }))
  wire(root, A, 0, S, 0)
  wire(S.subgraph, INPUT, 0, OUTPUT, 0)
  wire(root, S, 0, C, 0)
  out.passthrough = {
    cDown: ids(api.resolveInputSources(root, C, 0)),
    aOut: ids(api.resolveOutputTargets(root, A, 0)),
  }
  // A pathological definition that feeds its own output back in terminates.
  const loop = new FakeGraph()
  const T = loop.add(new FakeSubgraphNode({ id: 1, name: 'Loop', inputs: ['x'], outputs: ['y'], rootGraph: loop }))
  const K = loop.add(new FakeNode({ id: 2, type: 'Sink', inputs: ['in'] }))
  wire(T.subgraph, INPUT, 0, OUTPUT, 0)
  wire(loop, T, 0, T, 0)
  wire(loop, T, 0, K, 0)
  out.cycle = {
    src: api.resolveInputSources(loop, K, 0).length,
    tgt: api.resolveOutputTargets(loop, T, 0).length,
  }
}

// ---- 6. stopAtSubgraphInput: report the SubgraphNode itself -----------------
{
  const { root, A, S } = simpleNested()
  const stop = api.resolveOutputTargets(root, A, 0, { stopAtSubgraphInput: () => true })
  const go = api.resolveOutputTargets(root, A, 0, { stopAtSubgraphInput: () => false })
  out.stop = {
    stopped: stop.map((e) => [e.pathId, !!e.boundary, e.node === S]),
    descended: go.map((e) => [e.pathId, !!e.boundary, e.node.type]),
  }
}

// ---- 7. graphLink shapes ----------------------------------------------------
{
  const link = { id: 4, origin_id: 1 }
  const asMap = { links: new Map([[4, link]]) }
  const asObject = { links: { 4: link } }
  const asArray = { links: [null, null, null, null, link] }
  const asProxy = (() => {
    const m = new Map([[4, link]])
    return { links: new Proxy(m, { get: (t, k) => (k in t ? (typeof t[k] === 'function' ? t[k].bind(t) : t[k]) : t.get(Number(k))) }) }
  })()
  const strKey = { links: new Map([['4', link]]) }
  out.graphLink = {
    map: api.graphLink(asMap, 4) === link, object: api.graphLink(asObject, 4) === link,
    array: api.graphLink(asArray, 4) === link, proxy: api.graphLink(asProxy, 4) === link,
    stringKey: api.graphLink(strKey, 4) === link, stringIdOnMap: api.graphLink(asMap, '4') === link,
    missing: api.graphLink(asMap, 99), nullId: api.graphLink(asMap, null), noGraph: api.graphLink(null, 4),
  }
}

// ---- 8. small path/root helpers ---------------------------------------------------
{
  const { root, A, inner } = simpleNested()
  const stranger = new FakeNode({ id: 99 })               // a node in no graph (a unit-test fake)
  const orphan = new FakeGraph({ name: 'other tab' })
  const foreign = orphan.add(new FakeNode({ id: 1 }))
  app.graph = root
  out.helpers = {
    join: [api.joinPath('', 3), api.joinPath('3', 2), api.joinPath('3:5', '2')],
    parent: [api.parentPrefixOf('2'), api.parentPrefixOf('3:2'), api.parentPrefixOf('3:5:2')],
    liveRoot: {
      root: api.liveRootOf(A) === root,
      nested: api.liveRootOf(inner) === root,       // Subgraph.rootGraph is the root
      stranger: api.liveRootOf(stranger),
      otherTab: api.liveRootOf(foreign),
    },
    rootOf: [api.rootGraphOf(inner.graph) === root, api.rootGraphOf(root) === root, api.rootGraphOf(null)],
    compare: [['2', '10'], ['3', '3:2'], ['3:2', '3:10'], ['10', '9']].map(([a, b]) => Math.sign(api.comparePathIds(a, b))),
  }
  app.graph = null
  out.helpers.liveRootWithoutApp = api.liveRootOf(A)
}

// ---- 9. resolveSourcesAt: a hop stays on its LANE through a shared definition ------
{
  const root = new FakeGraph()
  const A1 = root.add(new FakeNode({ id: 1, type: 'Src1', outputs: ['o'] }))
  const A2 = root.add(new FakeNode({ id: 2, type: 'Src2', outputs: ['o'] }))
  const S1 = root.add(new FakeSubgraphNode({ id: 10, name: 'W', inputs: ['x'], rootGraph: root }))
  const S2 = root.add(new FakeSubgraphNode({ id: 11, name: 'W', inputs: ['x'], rootGraph: root }))
  S2.subgraph = S1.subgraph
  const R = S1.subgraph.add(new FakeNode({ id: 20, type: 'Reroute', inputs: ['i'], outputs: ['o'] }))
  const Q = S1.subgraph.add(new FakeNode({ id: 21, type: 'Wall', inputs: ['i'] }))
  wire(root, A1, 0, S1, 0)
  wire(root, A2, 0, S2, 0)
  wire(S1.subgraph, INPUT, 0, R, 0)
  wire(S1.subgraph, R, 0, Q, 0)
  const entry = api.resolveSourcesAt(root, { node: Q }, 0)              // both lanes: R under 10 and under 11
  const lane10 = api.resolveSourcesAt(root, entry.find((e) => e.pathId === '10:20'), 0)
  const lane11 = api.resolveSourcesAt(root, entry.find((e) => e.pathId === '11:20'), 0)
  out.lanes = {
    entry: ids(entry),
    lane10: ids(lane10),                                  // ONLY instance 10's upstream
    lane11: ids(lane11),                                  // ONLY instance 11's upstream
    byObject: ids(api.resolveInputSources(root, R, 0)),   // re-resolving the shared OBJECT fans out to both
    noItem: api.resolveSourcesAt(root, null, 0).length,
    badSlot: api.resolveSourcesAt(root, { node: Q }, -1).length,
  }
}

console.log(JSON.stringify(out))
