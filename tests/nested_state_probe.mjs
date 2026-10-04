// v1.2.0 nested reach: EPS Universal State Controller capture/apply across
// subgraphs, against the REAL discoverStateNodes/buildStatePayload/applyPlan
// and the REAL api.js walkers over a fake nested graph.
import { app } from './scripts/app.js'
import * as m from './extensions/comfyui-epsnodes/lora_library/universal_controller.js'
import { FakeGraph, FakeNode, FakeSubgraphNode } from './nested_graph.mjs'

const registry = m.normalizeRegistry({
  classes: {
    EPSResolution: { display: 'Resolution', widgets: { width: { kind: 'int', min: 0, max: 16384 } } },
    EPSSwitcher: { display: 'Switcher', widgets: { toggles: { kind: 'json_object' } } }
  }
})
const widgetsOf = (w, t = '{}') => [
  { name: 'width', value: w },
  { name: 'toggles', value: t }
]

// root: Resolution #1 (width 512), SubgraphNode #3 "Looks" holding Resolution #2 (width 768)
// and a Switcher #4, plus a SECOND SubgraphNode #6 "Upscale" holding a Resolution that
// COLLIDES with the first inner id (#2).
function build({ looksId = 3, upscaleId = 6 } = {}) {
  const root = new FakeGraph()
  const rootRes = root.add(new FakeNode({ id: 1, type: 'EPSResolution', title: 'Size', widgets: widgetsOf(512) }))
  const looks = root.add(new FakeSubgraphNode({ id: looksId, name: 'Looks', rootGraph: root }))
  const innerRes = looks.subgraph.add(new FakeNode({ id: 2, type: 'EPSResolution', title: 'Size', widgets: widgetsOf(768) }))
  const innerSw = looks.subgraph.add(new FakeNode({ id: 4, type: 'EPSSwitcher', title: 'Look picker', widgets: widgetsOf(0, '{"image_1":false}') }))
  const up = root.add(new FakeSubgraphNode({ id: upscaleId, name: 'Upscale', rootGraph: root }))
  const upRes = up.subgraph.add(new FakeNode({ id: 2, type: 'EPSResolution', title: 'Upscale size', widgets: widgetsOf(2048) }))
  return { root, rootRes, innerRes, innerSw, upRes, looks, up }
}

const out = {}

// ---- 1. discovery + capture see every nested node, keyed by PATH id ---------
{
  const { root } = build()
  app.graph = root
  const discovered = m.discoverStateNodes(registry)
  out.discovered = discovered.map((d) => `${d.pathId}|${d.class}|${d.title}`)
  const info = discovered.map((d) => ({
    pathId: d.pathId, class: d.class, title: d.title,
    widgetValues: Object.fromEntries(d.node.widgets.map((w) => [w.name, w.value]))
  }))
  const payload = m.buildStatePayload(info, registry, { nodes: {}, classes: {} })
  out.captured = payload.nodes.map((n) => [n.id, n.class, n.title, n.widgets.width ?? null])
  out.warnings = payload.warnings
  out.state = payload.nodes // reused below as "the saved state"
}

// ---- 2. apply on the SAME canvas: exact path-id match, nested and root ------
{
  const { root, rootRes, innerRes, upRes } = build()
  app.graph = root
  const live = m.buildLiveIndex(m.discoverStateNodes(registry))
  const edited = out.state.map((n) => ({ ...n, widgets: n.class === 'EPSResolution' ? { width: n.widgets.width + 1 } : n.widgets }))
  const plan = m.applyPlan(edited, live, registry, { nodes: {}, classes: {} })
  out.sameCanvas = plan.matched.map((p) => [p.id, p.how, JSON.stringify(p.writes)])
  out.sameCanvasMissing = plan.missing.length
  out.rootAndInnerAreDistinct = [rootRes, innerRes, upRes].length
}

// ---- 3. apply on ANOTHER machine where the subgraph nodes were renumbered ----
//        (3 -> 8, 6 -> 9): ids no longer match, the matcher falls back to
//        class+title (unique) and the ambiguous Size pair to class order.
{
  const { root } = build({ looksId: 8, upscaleId: 9 })
  app.graph = root
  const discovered = m.discoverStateNodes(registry)
  const live = m.buildLiveIndex(discovered)
  const plan = m.applyPlan(out.state, live, registry, { nodes: {}, classes: {} })
  out.renumbered = plan.matched.map((p) => [p.savedId, p.id, p.how])
  out.renumberedMissing = plan.missing.length
}

// ---- 4. exclusions key by the LIVE path id: excluding "3:2" skips only it ----
{
  const { root } = build()
  app.graph = root
  const live = m.buildLiveIndex(m.discoverStateNodes(registry))
  const plan = m.applyPlan(out.state, live, registry, { nodes: { '3:2': false }, classes: {} })
  out.excluded = plan.skipped.map((s) => [s.id, s.reason])
  out.excludedStillMatched = plan.matched.map((p) => p.id)
  out.toggleAfter = m.exclusionsAfterToggle({ nodes: {}, classes: {} }, { type: 'node', pathId: '3:2', included: false })
}

// ---- 5. a shared definition shows up once per instance path ------------------
{
  const root = new FakeGraph()
  const a = root.add(new FakeSubgraphNode({ id: 3, name: 'Looks', rootGraph: root }))
  const b = root.add(new FakeSubgraphNode({ id: 4, name: 'Looks', rootGraph: root }))
  b.subgraph = a.subgraph
  a.subgraph.add(new FakeNode({ id: 2, type: 'EPSResolution', title: 'Size', widgets: widgetsOf(640) }))
  app.graph = root
  out.sharedPaths = m.discoverStateNodes(registry).map((d) => d.pathId)
}

console.log(JSON.stringify(out))
