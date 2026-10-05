import { app } from './scripts/app.js'
import { FakeGraph, FakeNode, FakeSubgraphNode } from './nested_graph.mjs'
import * as m from './extensions/comfyui-epsnodes/eps_image/save_image.js'

const toasts = []
app.extensionManager = { toast: { add: (t) => toasts.push(t.severity) } }
app.handleFile = async () => {}
m.init()

const multiplier = (id, solo = '') => {
  const node = new FakeNode({ id, type: 'EPSCrossSweep', widgets: [{ name: 'solo_run', value: solo, callback: () => {} }] })
  return node
}
const png = { type: 'image/png', name: 'Portrait_m2_i1_t3_00001_.png' }
const out = {}
const run = async (label, build, file = png) => {
  toasts.length = 0
  const root = new FakeGraph()
  app.graph = root
  const probes = build(root)
  await app.handleFile(file)
  out[label] = { solos: probes.map((n) => n.widgets[0].value), toasts: [...toasts] }
}

// 1. the ONLY multiplier lives inside a subgraph: it is found and soloed
await run('nested', (root) => {
  const S = root.add(new FakeSubgraphNode({ id: 3, name: 'W', rootGraph: root }))
  const mult = S.subgraph.add(multiplier(2))
  return [mult]
})

// 2. ONE multiplier inside a definition shared by TWO SubgraphNodes: the same
//    node object under two path ids is still ONE multiplier (apply, not ambiguous)
await run('shared', (root) => {
  const S1 = root.add(new FakeSubgraphNode({ id: 3, name: 'W', rootGraph: root }))
  const S2 = root.add(new FakeSubgraphNode({ id: 4, name: 'W', rootGraph: root }))
  S2.subgraph = S1.subgraph
  const mult = S1.subgraph.add(multiplier(2))
  return [mult]
})

// 3. two DISTINCT unsoloed multipliers (one root, one nested): ambiguous, hands off
await run('ambiguous', (root) => {
  const a = root.add(multiplier(1))
  const S = root.add(new FakeSubgraphNode({ id: 3, name: 'W', rootGraph: root }))
  const b = S.subgraph.add(multiplier(2))
  return [a, b]
})

// 4. a BAKED file: the nested multiplier already carries the token
await run('baked', (root) => {
  const S = root.add(new FakeSubgraphNode({ id: 3, name: 'W', rootGraph: root }))
  const mult = S.subgraph.add(multiplier(2, 'm2_i1_t3'))
  return [mult]
})

// 5-7. (2026-10-04 formats) the file TYPE decides what the fallback does with
//    the same file name stem: an AVIF is read like a PNG (the frontend loads
//    its workflow from the Exif item), an EXR never loads a workflow so it
//    must not solo the CURRENT canvas whatever MIME the browser gave it
const single = (root) => [root.add(multiplier(1))]
await run('avif', single, { type: 'image/avif', name: 'Portrait_m2_i1_t3_00001_.avif' })
await run('exr_x', single, { type: 'image/x-exr', name: 'Portrait_m2_i1_t3_00001_.exr' })
await run('exr_blank', single, { type: '', name: 'Portrait_m2_i1_t3_00001_.exr' })
await run('exr_upper', single, { type: 'image/exr', name: 'PORTRAIT_m2_i1_t3_00001_.EXR' })

console.log(JSON.stringify(out))
