import * as api from './extensions/comfyui-epsnodes/lora_library/api.js'
import { FakeGraph, FakeNode, FakeSubgraphNode } from './nested_graph.mjs'

const out = {}
const HOOKS = ['onNodeAdded', 'onNodeRemoved']
const KEY = '__epsTestWatch'

// ---- install + fire -----------------------------------------------------------
{
  const g = new FakeGraph()
  const seen = []
  let originalCalls = 0
  g.onNodeAdded = function () { originalCalls += 1 }
  api.watchGraphHooks(g, KEY, HOOKS, (graph, hook) => seen.push([graph === g, hook]))
  g.add(new FakeNode({ id: 1 }))
  g.remove(g._nodes[0])
  out.fire = { seen, originalCalls }
}

// ---- idempotent: three installs, still ONE wrapper, ONE event --------------------
{
  const g = new FakeGraph()
  let n = 0
  for (let i = 0; i < 3; i++) api.watchGraphHooks(g, KEY, HOOKS, () => { n += 1 })
  const first = g.onNodeAdded
  api.watchGraphHooks(g, KEY, HOOKS, () => { n += 1 })
  g.add(new FakeNode({ id: 1 }))
  out.idempotent = { sameWrapper: g.onNodeAdded === first, events: n }
}

// ---- the return value says whether anything was (re)installed -------------------
{
  const g = new FakeGraph()
  const first = api.watchGraphHooks(g, KEY, HOOKS, () => {})
  const second = api.watchGraphHooks(g, KEY, HOOKS, () => {})
  g.onNodeRemoved = undefined // core restored ONE of the two hooks
  const third = api.watchGraphHooks(g, KEY, HOOKS, () => {})
  const none = api.watchGraphHooks(null, KEY, HOOKS, () => {})
  const root = new FakeGraph()
  const S = root.add(new FakeSubgraphNode({ id: 3, name: 'W', rootGraph: root }))
  const all1 = api.watchAllGraphs(root, KEY, HOOKS, () => {})
  const all2 = api.watchAllGraphs(root, KEY, HOOKS, () => {})
  S.subgraph.onNodeAdded = undefined
  const all3 = api.watchAllGraphs(root, KEY, HOOKS, () => {})
  out.returns = { first, second, third, none, all1, all2, all3 }
}

// ---- core RESTORES the hook (subgraph enter/exit): re-verified, not one-shot ----
{
  const g = new FakeGraph()
  let n = 0
  const coreOriginal = g.onNodeAdded // undefined, what core captured at ITS install
  api.watchGraphHooks(g, KEY, HOOKS, () => { n += 1 })
  g.onNodeAdded = coreOriginal       // the restore: our wrapper is gone
  g.add(new FakeNode({ id: 1 }))
  const deafEvents = n
  api.watchGraphHooks(g, KEY, HOOKS, () => { n += 1 })   // the next refresh pass
  g.add(new FakeNode({ id: 2 }))
  out.restored = { deafEvents, afterReverify: n }
}

// ---- core WRAPS ours, then later restores to ours: ours is ADOPTED, not stacked ----
{
  const g = new FakeGraph()
  let n = 0
  api.watchGraphHooks(g, KEY, HOOKS, () => { n += 1 })
  const ours = g.onNodeAdded
  g.onNodeAdded = function (...a) { return ours.apply(this, a) }   // core's wrapper
  g.onNodeAdded = ours                                              // core's restore
  api.watchGraphHooks(g, KEY, HOOKS, () => { n += 1 })
  g.add(new FakeNode({ id: 1 }))
  out.adopt = { stillOurs: g.onNodeAdded === ours, events: n }
}

// ---- a hook that throws never breaks the graph or the other watcher ---------------
{
  const g = new FakeGraph()
  let reached = 0
  g.onNodeAdded = () => { throw new Error('original boom') }
  api.watchGraphHooks(g, KEY, HOOKS, () => { reached += 1; throw new Error('watcher boom') })
  let threw = false
  try { g.add(new FakeNode({ id: 1 })) } catch { threw = true }
  out.throws = { threw, reached, added: g._nodes.length }
}

// ---- two features own two keys: neither clobbers the other ------------------------
{
  const g = new FakeGraph()
  const a = [], b = []
  api.watchGraphHooks(g, '__epsOwnerA', HOOKS, () => a.push(1))
  api.watchGraphHooks(g, '__epsOwnerB', HOOKS, () => b.push(1))
  g.add(new FakeNode({ id: 1 }))
  out.twoOwners = { a: a.length, b: b.length }
}

// ---- several features re-verifying each other NEVER stack layers forever --------------
{
  const g = new FakeGraph()
  const counts = { a: 0, b: 0, c: 0 }
  let originalCalls = 0
  g.onNodeAdded = () => { originalCalls += 1 }
  const features = [['__epsA', 'a'], ['__epsB', 'b'], ['__epsC', 'c']]
  const pass = () => features.forEach(([key, name]) => api.watchGraphHooks(g, key, HOOKS, () => { counts[name] += 1 }))
  for (let i = 0; i < 12; i++) pass()                  // twelve refresh passes by three features
  g.add(new FakeNode({ id: 1 }))
  const afterQuiet = { ...counts, originalCalls }
  // core wraps the top, then every feature re-verifies several times
  const top = g.onNodeAdded
  g.onNodeAdded = function (...a) { return top.apply(this, a) }
  for (let i = 0; i < 12; i++) pass()
  const coreWrapped = g.onNodeAdded
  counts.a = counts.b = counts.c = 0; originalCalls = 0
  g.add(new FakeNode({ id: 2 }))
  const afterCoreWrap = { ...counts, originalCalls }
  // core restores (what it captured at its install): the inner chain is adopted again
  g.onNodeAdded = top
  for (let i = 0; i < 12; i++) pass()
  counts.a = counts.b = counts.c = 0; originalCalls = 0
  g.add(new FakeNode({ id: 3 }))
  out.siblings = { afterQuiet, afterCoreWrap, afterRestore: { ...counts, originalCalls }, restoredIsTop: g.onNodeAdded === top }
}

// ---- watchAllGraphs reaches a SUBGRAPH's own hooks ---------------------------------
{
  const root = new FakeGraph()
  const S = root.add(new FakeSubgraphNode({ id: 3, name: 'W', rootGraph: root }))
  const fired = []
  api.watchAllGraphs(root, KEY, HOOKS, (graph) => fired.push(graph === root ? 'root' : graph === S.subgraph ? 'sub' : '?'))
  S.subgraph.add(new FakeNode({ id: 2 }))
  root.add(new FakeNode({ id: 4 }))
  // a subgraph created LATER is picked up by the next refresh pass
  const late = root.add(new FakeSubgraphNode({ id: 5, name: 'Late', rootGraph: root }))
  api.watchAllGraphs(root, KEY, HOOKS, (graph) => fired.push(graph === root ? 'root' : graph === S.subgraph ? 'sub' : 'late'))
  late.subgraph.add(new FakeNode({ id: 6 }))
  out.all = fired
}

console.log(JSON.stringify(out))
