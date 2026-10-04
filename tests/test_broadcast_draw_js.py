"""Link-drawing tests for EPS Run Multiplier BROADCAST -- tucked wires (FORMAT.md
§6.10 "Tucked wires", plan ``research/roadmap-eps-broadcast.md`` §1a / M3,
owner ask 2026-10-03) -- ``web/eps_image/broadcast_draw.js``.

These drive the REAL, unmodified module (plus the real ``broadcast_graph.js`` /
``broadcast_plan.js`` that build the link index it reads) against the shared
fake litegraph (``tests/fake_litegraph.mjs``): a ``FakeCanvas`` whose
``renderLink`` is a PROTOTYPE method that writes the hit-test state exactly the
way 1.52.7's ``renderLinkDirect`` does (``link._pos`` / ``link.path``), whose
``drawConnections`` adds every visited link to ``renderedPaths`` whether or not
it was drawn, and a ``FakeCtx`` that records drawing calls and honours
save/restore. Broadcast wires are made by the REAL applier, so the index, the
records and the owner lookup are the production ones.

What this CANNOT cover, and the rig must (see the final report's UNCONFIRMED
list): real pixels (the stub's look at every zoom, the 📡 glyph on each OS),
the real ``renderLink`` -> ``renderLinkDirect`` call chain in Nodes 2.0 mode,
that selecting a node really reaches the wrapped ``select`` / ``deselect`` in
Vue mode, and the real canvas menu.
"""

# ruff: noqa: E501 -- the embedded Node probe source (PROBE_JS) is JavaScript in a raw
# string, wrapped for readability as JS, not Python.

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from served_layout import build_served_layout

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB = REPO_ROOT / "web"
DRAW_JS = WEB / "eps_image" / "broadcast_draw.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

PROBE_JS = r"""
import * as draw from './extensions/comfyui-epsnodes/eps_image/broadcast_draw.js'
import * as bg from './extensions/comfyui-epsnodes/eps_image/broadcast_graph.js'
import { app } from './scripts/app.js'
import * as fl from './fake_litegraph.mjs'

const { FakeSubgraph, FakeSubgraphNode, FakeCtx, FakeCanvas, build, ksampler, decode, saver, makeNode, layout } = fl

const out = {}
const warnings = []
console.warn = (...args) => { warnings.push(args.map(String).join(' ').slice(0, 200)) }

app.canvas = { emitBeforeChange() {}, emitAfterChange() {}, setDirty() {} }
app.extensionManager = { toast: { add() {} }, workflow: { activeWorkflow: { changeTracker: { captureCanvasState() {} } } } }

/** A standalone canvas class per scenario (the module hooks a PROTOTYPE, once). */
function freshCanvasClass() {
  class C {
    constructor(graph = null) { Object.assign(this, new FakeCanvas(graph)) }
  }
  for (const name of Object.getOwnPropertyNames(FakeCanvas.prototype)) {
    if (name !== 'constructor') Object.defineProperty(C.prototype, name, Object.getOwnPropertyDescriptor(FakeCanvas.prototype, name))
  }
  C.link_type_colors = FakeCanvas.link_type_colors
  return C
}

/** Root + multiplier (user wires model/clip/vae from a loader) + 4 broadcast wires. */
function scenario({ apply = true } = {}) {
  const { root, m, loader } = build()
  const k10 = ksampler(root, 10)
  const k11 = ksampler(root, 11)
  const d13 = decode(root, 13)
  const s14 = saver(root, 14)
  layout(root)
  if (apply) {
    const { plan } = bg.planFor(m, {})
    bg.applyProposals(root, m, plan.proposals)
  }
  const links = { k10: k10.inputs[0].link, k11: k11.inputs[0].link, d13: d13.inputs[1].link, s14: s14.inputs[1].link }
  const userLinks = ['model', 'clip', 'vae'].map((n) => m.inputs.find((i) => i.name === n).link)
  return { root, m, loader, k10, k11, d13, s14, links, userLinks }
}
const setLook = (m, look) => { const cfg = bg.readConfig(m); cfg.look = look; bg.writeConfig(m, cfg) }
const drawnIds = (canvas) => canvas.drawn.map((d) => d.id)
const frame = (canvas, ctx, graph) => { canvas.drawn.length = 0; ctx.calls.length = 0; canvas.drawConnections(ctx, graph) }
const stubs = (ctx) => ctx.of('fillText').map((c) => [c[1], c[2], c[3]])

// ============================================================ A. pure decisions
{
  const plans = [
    [false, 'tucked', false], [true, 'tucked', false], [true, 'tucked', true],
    [true, 'dim', false], [true, 'dim', true], [true, 'normal', false], [true, 'normal', true], [true, 'bogus', false], [true, undefined, false]
  ].map(([isBroadcast, look, revealed]) => ({ in: [isBroadcast, look, revealed], plan: draw.broadcastDrawPlan({ isBroadcast, look, revealed }) }))
  out.pure = {
    plans,
    shouldDraw: plans.map((p) => draw.shouldDrawBroadcastLink({ isBroadcast: p.in[0], look: p.in[1], revealed: p.in[2] })),
    constants: { dim: draw.DIM_ALPHA, stub: draw.STUB_LENGTH, marker: draw.STUB_MARKER },
    native: draw.nativeHiddenLinksAvailable()
  }
  const sel = (ids) => Object.fromEntries(ids.map((id) => [id, { id }]))
  out.reveal = {
    all: draw.revealedBy({ showAll: true, selectedNodes: {}, targetId: 5, ownerId: '1', ownerInThisGraph: true }),
    target: draw.revealedBy({ showAll: false, selectedNodes: sel([5]), targetId: 5, ownerId: '1', ownerInThisGraph: true }),
    targetStringId: draw.revealedBy({ showAll: false, selectedNodes: sel(['5']), targetId: '5', ownerId: '1', ownerInThisGraph: true }),
    owner: draw.revealedBy({ showAll: false, selectedNodes: sel([1]), targetId: 5, ownerId: '1', ownerInThisGraph: true }),
    ownerOtherGraph: draw.revealedBy({ showAll: false, selectedNodes: sel([1]), targetId: 5, ownerId: '1', ownerInThisGraph: false }),
    none: draw.revealedBy({ showAll: false, selectedNodes: sel([2, 3]), targetId: 5, ownerId: '1', ownerInThisGraph: true }),
    nullSelection: draw.revealedBy({ showAll: false, selectedNodes: null, targetId: 5, ownerId: '1', ownerInThisGraph: true }),
    undefinedTarget: draw.revealedBy({ showAll: false, selectedNodes: sel([5]), targetId: undefined, ownerId: undefined, ownerInThisGraph: true }),
    prototypeKey: draw.revealedBy({ showAll: false, selectedNodes: {}, targetId: 'constructor', ownerId: 'toString', ownerInThisGraph: true })
  }
  // a node selected through its GROUP carries only the per-node flag, not a selected_nodes entry
  const flagged = (flags) => ({ getNodeById: (id) => ({ selected: flags[id] }) })
  out.reveal.flag = {
    target: draw.revealedBy({ showAll: false, selectedNodes: {}, graph: flagged({ 5: true }), targetId: 5, ownerId: '1', ownerInThisGraph: true }),
    owner: draw.revealedBy({ showAll: false, selectedNodes: {}, graph: flagged({ 1: true }), targetId: 5, ownerId: '1', ownerInThisGraph: true }),
    ownerOtherGraph: draw.revealedBy({ showAll: false, selectedNodes: {}, graph: flagged({ 1: true }), targetId: 5, ownerId: '1', ownerInThisGraph: false }),
    deselected: draw.revealedBy({ showAll: false, selectedNodes: {}, graph: flagged({ 5: false, 1: undefined }), targetId: 5, ownerId: '1', ownerInThisGraph: true }),
    notBoolean: draw.revealedBy({ showAll: false, selectedNodes: {}, graph: flagged({ 5: 'yes' }), targetId: 5, ownerId: '1', ownerInThisGraph: true }),
    noLookup: draw.revealedBy({ showAll: false, selectedNodes: {}, graph: {}, targetId: 5, ownerId: '1', ownerInThisGraph: true })
  }
  out.geometry = Object.fromEntries([[3, 'left'], [4, 'right'], [1, 'up'], [2, 'down'], [5, 'center'], [0, 'none'], [undefined, 'undef']]
    .map(([dir, name]) => [name, draw.stubGeometry([100, 50], dir)]))
  out.geometry.short = draw.stubGeometry([0, 0], 3, 10)
}

// ====================================================== B. install: idempotent
{
  const C = freshCanvasClass()
  const proto = C.prototype
  const original = proto.renderLink
  const first = draw.installRenderLinkHook(proto)
  const wrapped = proto.renderLink
  const second = draw.installRenderLinkHook(proto)
  const third = draw.installRenderLinkHook(proto)
  // another extension wraps ON TOP; a later install must not stack a second layer
  const theirs = function (...args) { return wrapped.apply(this, args) }
  proto.renderLink = theirs
  const fourth = draw.installRenderLinkHook(proto)
  const s = scenario()
  const canvas = new C(s.root)
  const ctx = new FakeCtx()
  bg.bumpBroadcastEpoch()
  frame(canvas, ctx, s.root)
  out.install = {
    first, second, third, fourth,
    changedByFirst: wrapped !== original, stableAfterSecond: proto.renderLink === theirs,
    flag: wrapped.__epsBcRenderLink === true,
    // wrapped once underneath their layer: each drawn link reached the original ONCE
    drawn: drawnIds(canvas).length, distinct: new Set(drawnIds(canvas)).size
  }
  // feature detection: no renderLink / not a function -> not installed, no throw, nothing added
  const bare = {}
  const notFn = { renderLink: 'nope' }
  out.install.detect = {
    bare: draw.installRenderLinkHook(bare), bareKeys: Object.keys(bare),
    notFn: draw.installRenderLinkHook(notFn), notFnValue: notFn.renderLink,
    nullProto: draw.installRenderLinkHook(null)
  }
}

// ===================== C. tucked: skipped + stub; reveal by owner / target / all
{
  const C = freshCanvasClass()
  draw.installRenderLinkHook(C.prototype)
  const s = scenario()
  const canvas = new C(s.root)
  const ctx = new FakeCtx()
  bg.bumpBroadcastEpoch()
  app.canvas = canvas

  frame(canvas, ctx, s.root)
  const targets = { k10: s.k10, k11: s.k11, d13: s.d13, s14: s.s14 }
  const slotOf = { k10: 0, k11: 0, d13: 1, s14: 1 }
  const expectB = Object.fromEntries(Object.entries(targets).map(([k, n]) => [k, [n.pos[0], n.pos[1] + 10 + 20 * slotOf[k]]]))
  out.tucked = {
    drawn: drawnIds(canvas), userLinks: s.userLinks, broadcast: Object.values(s.links),
    moveTo: ctx.of('moveTo').map((c) => [c[1], c[2]]), lineTo: ctx.of('lineTo').map((c) => [c[1], c[2]]),
    expectB,
    strokes: ctx.of('stroke').map((c) => [c[1], c[2]]),
    fillText: stubs(ctx),
    saves: ctx.of('save').length, restores: ctx.of('restore').length,
    alphaAfter: ctx.globalAlpha, strokeStyleAfter: ctx.strokeStyle, stackDepth: ctx.stack.length,
    renderedPaths: canvas.renderedPaths.size
  }

  // reveal: the OWNING multiplier selected -> every wire drawn normally, no stubs
  canvas.select(s.m)
  frame(canvas, ctx, s.root)
  out.revealOwner = { drawn: drawnIds(canvas).length, stubs: stubs(ctx).length, alphas: [...new Set(canvas.drawn.map((d) => d.alpha))] }
  canvas.deselect(s.m)

  // reveal: the multiplier selected THROUGH ITS GROUP (flag only, no selected_nodes entry)
  canvas.selectViaGroup(s.m)
  frame(canvas, ctx, s.root)
  out.revealViaGroup = { drawn: drawnIds(canvas).length, stubs: stubs(ctx).length, dictionary: Object.keys(canvas.selected_nodes) }
  canvas.deselect(s.m)
  frame(canvas, ctx, s.root)
  out.revealViaGroup.afterDeselect = drawnIds(canvas).length

  // reveal: a TARGET selected -> only its own wire
  canvas.select(s.k10)
  frame(canvas, ctx, s.root)
  out.revealTarget = { drawnBroadcast: drawnIds(canvas).filter((id) => Object.values(s.links).includes(id)), k10: s.links.k10, stubs: stubs(ctx).length }
  canvas.deselect(s.k10)

  // reveal: the session switch
  const before = canvas.dirtyCalls.length
  draw.setShowAllWires(true)
  out.showAll = { on: draw.getShowAllWires() }
  frame(canvas, ctx, s.root)
  out.showAll.drawn = drawnIds(canvas).length
  out.showAll.stubs = stubs(ctx).length
  draw.setShowAllWires(false)
  out.showAll.off = draw.getShowAllWires()
  out.showAll.repaints = canvas.dirtyCalls.slice(before)
  frame(canvas, ctx, s.root)
  out.showAll.tuckedAgain = drawnIds(canvas).length

  // low quality (zoomed far out): a dot, no glyph
  canvas.low_quality = true
  frame(canvas, ctx, s.root)
  out.lowQuality = { fillText: ctx.of('fillText').length, arcs: ctx.of('arc').length }
  canvas.low_quality = false

  // end direction: RIGHT / UP / DOWN stubs
  const link = s.root._links.get(s.links.k10)
  const dirs = {}
  for (const [name, dir] of [['right', 4], ['up', 1], ['down', 2], ['center', 5]]) {
    ctx.calls.length = 0
    canvas.renderLink(ctx, [0, 0], [100, 100], link, false, 0, null, 4, dir)
    dirs[name] = [ctx.of('moveTo')[0].slice(1), ctx.of('lineTo')[0].slice(1)]
  }
  out.directions = dirs
  // the stub takes the link's own colour when it has one (read, never written)
  link.color = '#123456'
  ctx.calls.length = 0
  canvas.renderLink(ctx, [0, 0], [100, 100], link, false, 0, null, 4, 3)
  out.colour = { custom: ctx.of('stroke')[0][1], writes: link.color }
  delete link.color
  ctx.calls.length = 0
  canvas.renderLink(ctx, [0, 0], [100, 100], link, false, 0, null, 4, 3)
  out.colour.typeColour = ctx.of('stroke')[0][1]
  app.canvas = { emitBeforeChange() {}, emitAfterChange() {}, setDirty() {} }
}

// ============================ D. hit-testing stays sane (centre marker / renderedPaths)
{
  const C = freshCanvasClass()
  draw.installRenderLinkHook(C.prototype)
  const s = scenario()
  const canvas = new C(s.root)
  const ctx = new FakeCtx()
  bg.bumpBroadcastEpoch()
  // draw them once while REVEALED so the real centre marker exists...
  canvas.select(s.m)
  frame(canvas, ctx, s.root)
  const link10 = s.root._links.get(s.links.k10)
  const mid = [...link10._pos]
  const userLink = s.root._links.get(s.userLinks[0])
  const userMid = [...userLink._pos]
  out.hit = { revealedHit: canvas.hitTest(mid[0], mid[1])?.id === link10.id }
  // ...then tuck: the old midpoint must not be a click target any more
  canvas.deselect(s.m)
  frame(canvas, ctx, s.root)
  out.hit.tuckedMid = canvas.hitTest(mid[0], mid[1])
  out.hit.tuckedPos = [link10._pos[0], link10._pos[1]].map((v) => Number.isNaN(v))
  out.hit.tuckedPath = link10.path ?? null
  out.hit.userStillHit = canvas.hitTest(userMid[0], userMid[1])?.id === userLink.id
  out.hit.userPath = userLink.path
  // a wire that was NEVER drawn keeps the constructor's [0, 0]: still no click target there
  const fresh = scenario()
  const canvas2 = new C(fresh.root)
  bg.bumpBroadcastEpoch()
  const l = fresh.root._links.get(fresh.links.s14)
  l._pos = [0, 0] // what the LLink constructor gives a link that has never been drawn
  frame(canvas2, ctx, fresh.root)
  out.hit.originClick = canvas2.hitTest(0, 0)
  // and reveal restores the real centre marker (the draw rewrites _pos/path)
  canvas2.select(fresh.m)
  frame(canvas2, ctx, fresh.root)
  out.hit.afterReveal = { finite: l._pos.every((v) => Number.isFinite(v)), path: l.path }
}

// ============================================================== E. dim / normal
{
  const C = freshCanvasClass()
  draw.installRenderLinkHook(C.prototype)
  const s = scenario()
  const canvas = new C(s.root)
  const ctx = new FakeCtx()
  bg.bumpBroadcastEpoch()
  frame(canvas, ctx, s.root)
  const tuckedCount = drawnIds(canvas).length

  // the look is cached per epoch: writing the property alone changes nothing...
  setLook(s.m, 'dim')
  frame(canvas, ctx, s.root)
  const stale = drawnIds(canvas).length
  // ...bumping the epoch (what broadcast.js's setLook does) applies it
  bg.bumpBroadcastEpoch()
  frame(canvas, ctx, s.root)
  const byId = Object.fromEntries(canvas.drawn.map((d) => [d.id, d.alpha]))
  out.dim = {
    tuckedCount, stale,
    broadcastAlphas: Object.values(s.links).map((id) => byId[id]),
    userAlphas: s.userLinks.map((id) => byId[id]),
    stubs: stubs(ctx).length, alphaAfter: ctx.globalAlpha, stack: ctx.stack.length
  }
  // dim + revealed -> full alpha
  canvas.select(s.k11)
  frame(canvas, ctx, s.root)
  const revealed = Object.fromEntries(canvas.drawn.map((d) => [d.id, d.alpha]))
  out.dim.revealedAlpha = revealed[s.links.k11]
  out.dim.othersStillDim = [s.links.k10, s.links.d13, s.links.s14].map((id) => revealed[id])
  canvas.deselect(s.k11)

  // a throw inside core's own drawing: alpha restored, error propagates
  const C2 = freshCanvasClass()
  C2.prototype.renderLink = function () { throw new Error('core drew badly') }
  draw.installRenderLinkHook(C2.prototype)
  const c2 = new C2(s.root)
  const ctx2 = new FakeCtx()
  let thrown = null
  try { c2.renderLink(ctx2, [0, 0], [10, 10], s.root._links.get(s.links.k10), false, 0, null, 4, 3) } catch (error) { thrown = error.message }
  out.dim.coreError = { thrown, alpha: ctx2.globalAlpha }

  // NORMAL: the wrapper is out of the way entirely
  setLook(s.m, 'normal')
  bg.bumpBroadcastEpoch()
  canvas.low_quality = false
  const linkN = s.root._links.get(s.links.k10)
  linkN._pos = [7, 7]
  linkN.path = 'keep-me'
  frame(canvas, ctx, s.root)
  out.normal = {
    drawn: drawnIds(canvas).length, alphas: [...new Set(canvas.drawn.map((d) => d.alpha))], stubs: stubs(ctx).length,
    sawAll: Object.values(s.links).every((id) => drawnIds(canvas).includes(id)),
    saves: ctx.of('save').length,
    linkPathRewrittenByCore: linkN.path === `path:${linkN.id}`
  }
}

// ================== F. non-broadcast links / drag preview pass through untouched
{
  const C = freshCanvasClass()
  draw.installRenderLinkHook(C.prototype)
  const s = scenario()
  const canvas = new C(s.root)
  const ctx = new FakeCtx()
  bg.bumpBroadcastEpoch()
  frame(canvas, ctx, s.root)
  const userDraws = canvas.drawn.filter((d) => s.userLinks.includes(d.id))
  const preview = canvas.renderLink(ctx, [1, 2], [3, 4], null, false, 0, 'red', 4, 3)
  const noId = canvas.renderLink(ctx, [1, 2], [3, 4], { id: undefined }, false, 0, null, 4, 3)
  out.passthrough = {
    userDrawn: userDraws.length, userAlphas: userDraws.map((d) => d.alpha),
    userArgs: userDraws.map((d) => [d.startDir, d.endDir]),
    previewReturn: preview, noIdReturn: noId,
    lastTwoDrawn: canvas.drawn.slice(-2).map((d) => d.id),
    ctxCallsForUser: 0
  }
  // a workflow with NO broadcast records short-circuits: the owner lookup is empty
  const plain = scenario({ apply: false })
  const canvas3 = new C(plain.root)
  bg.bumpBroadcastEpoch()
  frame(canvas3, ctx, plain.root)
  out.passthrough.plainDrawn = drawnIds(canvas3).length
  out.passthrough.plainTotal = plain.root._links.size
  out.passthrough.owners = bg.broadcastLinkOwners(plain.root).size
}

// ======================================== G. fail safe: never throws into the loop
{
  warnings.length = 0
  // (1) our decision throws (a hostile selected_nodes getter) -> wire drawn by the ORIGINAL
  const C = freshCanvasClass()
  draw.installRenderLinkHook(C.prototype)
  const s = scenario()
  const canvas = new C(s.root)
  Object.defineProperty(canvas, 'selected_nodes', { get() { throw new Error('selection exploded') } })
  const ctx = new FakeCtx()
  bg.bumpBroadcastEpoch()
  let thrown = null
  try { frame(canvas, ctx, s.root) } catch (error) { thrown = error.message }
  out.failSafe = {
    thrown, drawnAll: drawnIds(canvas).length, total: s.root._links.size,
    stubs: stubs(ctx).length, warnings: warnings.length, warnText: warnings[0] || ''
  }
  // (2) the stub drawing throws -> wire drawn by the ORIGINAL, alpha/state intact
  warnings.length = 0
  const s2 = scenario()
  const C2 = freshCanvasClass()
  draw.installRenderLinkHook(C2.prototype)
  const canvas2 = new C2(s2.root)
  const ctx2 = new FakeCtx()
  ctx2.failOn = 'moveTo'
  bg.bumpBroadcastEpoch()
  let thrown2 = null
  try { frame(canvas2, ctx2, s2.root) } catch (error) { thrown2 = error.message }
  out.failSafe.stub = {
    thrown: thrown2, drawnAll: drawnIds(canvas2).length, total: s2.root._links.size,
    warnings: warnings.length, alpha: ctx2.globalAlpha, stack: ctx2.stack.length,
    // the original rewrote the hit-test state, so the click target is real again
    posFinite: s2.root._links.get(s2.links.k10)._pos.every((v) => Number.isFinite(v))
  }
}

// ============================ H. reroute segments + the event-flash overlay
{
  const C = freshCanvasClass()
  draw.installRenderLinkHook(C.prototype)
  const s = scenario()
  const canvas = new C(s.root)
  const ctx = new FakeCtx()
  bg.bumpBroadcastEpoch()
  const link = s.root._links.get(s.links.k10)
  const reroute = { id: 7, _pos: [5, 5], path: 'reroute-path' }
  canvas.renderLink(ctx, [0, 0], [50, 50], link, false, 0, null, 4, 5, { reroute })                 // segment ending at a reroute
  const afterSegment = ctx.of('moveTo').length
  canvas.renderLink(ctx, [50, 50], [100, 100], link, false, 0, null, 5, 3, { startControl: [1, 1] }) // the final segment
  const afterFinal = ctx.of('moveTo').length
  canvas.renderLink(ctx, [0, 0], [100, 100], link, true, 0.6, 'white', 4, 3)                         // flash overlay
  out.reroute = {
    drawn: canvas.drawn.length, afterSegment, afterFinal, afterFlash: ctx.of('moveTo').length,
    reroutePos: reroute._pos.map((v) => Number.isNaN(v)), reroutePath: reroute.path ?? null,
    stubAt: ctx.of('moveTo')[0].slice(1)
  }
  link._dragging = true
  canvas.renderLink(ctx, [0, 0], [100, 100], link, false, 0, null, 4, 3)
  out.reroute.dragging = ctx.of('moveTo').length
}

// ================================ I. selection repaint (no polling, idempotent)
{
  const C = freshCanvasClass()
  const sOn = scenario()
  const canvas = new C(sOn.root)
  const installed = [draw.installSelectionRepaint(C.prototype), draw.installSelectionRepaint(C.prototype)]
  bg.bumpBroadcastEpoch()
  canvas.select(sOn.k10)
  const afterSelect = canvas.dirtyCalls.length
  canvas.deselect(sOn.k10)
  const afterDeselect = canvas.dirtyCalls.length
  canvas.select(sOn.m)
  canvas.deselectAll()
  const returns = [canvas.select(sOn.k11), canvas.deselect(sOn.k11)]
  out.selection = {
    installed, afterSelect, afterDeselect, allDirty: canvas.dirtyCalls.every(([fg, bgc]) => fg && bgc),
    total: canvas.dirtyCalls.length, returns,
    dictionary: Object.keys(canvas.selected_nodes), originalsStillWork: true
  }
  // a workflow with no broadcast wires pays nothing
  const plain = scenario({ apply: false })
  const c2 = new C(plain.root)
  bg.bumpBroadcastEpoch()
  c2.select(plain.k10); c2.deselectAll()
  out.selection.plainDirty = c2.dirtyCalls.length
  // ...and with "show all" on, a selection changes nothing visible -> no repaint
  draw.setShowAllWires(true)
  c2.dirtyCalls.length = 0
  canvas.dirtyCalls.length = 0
  canvas.select(sOn.k10)
  out.selection.showAllDirty = canvas.dirtyCalls.length
  draw.setShowAllWires(false)
  // methods an older frontend does not have are skipped, never invented
  const OldProto = { select() {}, }
  out.selection.partial = { ok: draw.installSelectionRepaint(OldProto), hasDeselect: 'deselect' in OldProto, hasAll: 'deselectAll' in OldProto }
  out.selection.noProto = draw.installSelectionRepaint(null)

  // the chained onSelectionChange (older frontends)
  const chainCalls = []
  const canvas3 = new FakeCanvas(sOn.root)
  canvas3.onSelectionChange = function (selected) { chainCalls.push(Object.keys(selected).length); return 'orig' }
  const cb = [draw.installSelectionCallback(canvas3), draw.installSelectionCallback(canvas3)]
  const first = canvas3.onSelectionChange
  canvas3.selected_nodes = { 10: sOn.k10 }
  const ret = canvas3.onSelectionChange(canvas3.selected_nodes)
  // something replaces it WITHOUT chaining: re-verified on the next call
  canvas3.onSelectionChange = () => 'theirs'
  draw.installSelectionCallback(canvas3)
  const ret2 = canvas3.onSelectionChange({})
  out.selection.callback = {
    installed: cb, stable: canvas3.onSelectionChange !== first, ret, chainCalls, dirty: canvas3.dirtyCalls.length, ret2
  }
}

// ======================= J. inside a subgraph: owner lives elsewhere, ids collide
{
  const { root, m } = build(['model'])
  const defB = new FakeSubgraph(root, 'sg-b', 'New-input group')
  const kB = ksampler(defB, 6, 'Inner B')
  const kB2 = ksampler(defB, 7, 'Inner B2')
  const decoy = makeNode(defB, 1, 'Loader', 'Decoy (same id as the multiplier)', [], [])
  const inst = root.add(new FakeSubgraphNode(4, 'New-input group', defB))
  layout(root); layout(defB)
  const { plan } = bg.planFor(m, {})
  bg.applyProposals(root, m, plan.proposals)
  const C = freshCanvasClass()
  draw.installRenderLinkHook(C.prototype)
  const canvas = new C(root)
  const ctx = new FakeCtx()
  bg.bumpBroadcastEpoch()
  const innerLinks = [kB.inputs[0].link, kB2.inputs[0].link]
  const outerLink = inst.inputs.find((i) => i.name === 'model').link

  frame(canvas, ctx, defB)
  const innerTucked = { drawn: drawnIds(canvas).length, stubs: stubs(ctx).length }
  // the node with the multiplier's ID, INSIDE the subgraph, is not the multiplier
  canvas.select(decoy)
  frame(canvas, ctx, defB)
  const decoySelected = { drawn: drawnIds(canvas).length, stubs: stubs(ctx).length }
  canvas.deselect(decoy)
  canvas.select(kB)
  frame(canvas, ctx, defB)
  const targetSelected = { drawn: drawnIds(canvas), expect: innerLinks[0] }
  canvas.deselect(kB)

  // the OUTER wire (root graph): owner selected / target (the instance) selected
  frame(canvas, ctx, root)
  const outerTucked = !drawnIds(canvas).includes(outerLink)
  canvas.select(m)
  frame(canvas, ctx, root)
  const outerOwner = drawnIds(canvas).includes(outerLink)
  canvas.deselect(m)
  canvas.select(inst)
  frame(canvas, ctx, root)
  const outerTarget = drawnIds(canvas).includes(outerLink)
  out.nested = { innerLinks, outerLink, innerTucked, decoySelected, targetSelected, outerTucked, outerOwner, outerTarget }
  const owner = bg.broadcastLinkOwner(defB, innerLinks[0])
  out.nested.owner = owner && { ...owner }
  out.nested.rootOwner = { ...bg.broadcastLinkOwner(root, outerLink) }
}

// ============================================================ K. ensureDrawHooks
{
  const C = freshCanvasClass()
  delete globalThis.LGraphCanvas
  app.canvas = undefined
  const none = draw.ensureDrawHooks()
  globalThis.LGraphCanvas = C
  const canvas = new C(null)
  app.canvas = canvas
  const a = draw.ensureDrawHooks()
  const wrapped = C.prototype.renderLink
  const b = draw.ensureDrawHooks()
  out.ensure = { none, a, b, stable: C.prototype.renderLink === wrapped, callback: canvas.onSelectionChange?.__epsBcSelectionRepaint === true }
  delete globalThis.LGraphCanvas
  app.canvas = { emitBeforeChange() {}, emitAfterChange() {}, setDirty() {} }
}

process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def probe(tmp_path_factory: pytest.TempPathFactory) -> dict:
    layout = tmp_path_factory.mktemp("web_root")
    build_served_layout(
        layout,
        eps_modules=(
            "broadcast_draw.js",
            "broadcast_graph.js",
            "broadcast_plan.js",
            "bypass.js",
            "number_controller.js",
            "distributor.js",
        ),
        with_fake=True,
    )
    probe_file = layout / "probe.mjs"
    probe_file.write_text(PROBE_JS, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(probe_file)], capture_output=True, text=True, timeout=120, cwd=layout
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def source() -> str:
    return DRAW_JS.read_text(encoding="utf-8")


# ------------------------------------------------------------------- tests


def test_draw_js_parses() -> None:
    result = subprocess.run(
        [NODE, "--check", str(DRAW_JS)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


def test_pure_decision_table(probe: dict) -> None:
    modes = {tuple(p["in"]): p["plan"] for p in probe["pure"]["plans"]}
    normal = {"mode": "normal", "draw": True, "alpha": 1, "stub": False}
    assert modes[(False, "tucked", False)] == normal  # not a broadcast wire: untouched
    assert modes[(True, "tucked", False)] == {
        "mode": "tucked",
        "draw": False,
        "alpha": 1,
        "stub": True,
    }
    assert modes[(True, "tucked", True)] == normal  # revealed -> drawn like any wire
    assert modes[(True, "dim", False)] == {
        "mode": "dim",
        "draw": True,
        "alpha": 0.25,
        "stub": False,
    }
    assert modes[(True, "dim", True)] == normal  # revealed dim wire is full strength
    assert modes[(True, "normal", False)] == modes[(True, "normal", True)] == normal
    assert modes[(True, "bogus", False)] == normal  # unknown look: the wire SHOWS (fail safe)
    assert modes[(True, None, False)] == normal
    assert probe["pure"]["shouldDraw"] == [p["plan"]["draw"] for p in probe["pure"]["plans"]]
    assert probe["pure"]["constants"] == {"dim": 0.25, "stub": 18, "marker": "📡"}


def test_the_future_native_route_is_a_documented_hook_that_is_off(probe: dict, source: str) -> None:
    assert probe["pure"]["native"] is False
    assert "FUTURE NATIVE ROUTE" in source and "1.55.9" in source
    body = source[source.index("export function installRenderLinkHook") :]
    assert body.index("nativeHiddenLinksAvailable()") < body.index("proto.renderLink = ")


def test_reveal_rules(probe: dict) -> None:
    r = probe["reveal"]
    assert r["all"] == "all"
    assert r["target"] == "target" and r["targetStringId"] == "target"
    assert r["owner"] == "owner"
    # a same-numbered node of ANOTHER graph is not the multiplier
    assert r["ownerOtherGraph"] is None
    assert r["none"] is None and r["nullSelection"] is None
    assert r["undefinedTarget"] is None
    assert r["prototypeKey"] is None


def test_a_node_selected_through_its_group_reveals_too(probe: dict) -> None:
    """1.52.7's `select()` group branch marks the child NODES `selected` without
    writing them into the deprecated `selected_nodes` dictionary."""
    f = probe["reveal"]["flag"]
    assert f["target"] == "target" and f["owner"] == "owner"
    assert f["ownerOtherGraph"] is None  # the multiplier must live in the graph being drawn
    assert f["deselected"] is None and f["notBoolean"] is None and f["noLookup"] is None
    g = probe["revealViaGroup"]
    assert g["dictionary"] == [] and g["drawn"] == 7 and g["stubs"] == 0
    assert g["afterDeselect"] == 3  # tucked again once the flag is cleared


def test_stub_geometry_runs_back_along_the_arrival_direction(probe: dict) -> None:
    g = probe["geometry"]
    assert g["left"] == {"from": [100, 50], "to": [82, 50], "marker": [77, 50]}
    assert g["right"] == {"from": [100, 50], "to": [118, 50], "marker": [123, 50]}
    assert g["up"] == {"from": [100, 50], "to": [100, 32], "marker": [100, 27]}
    assert g["down"] == {"from": [100, 50], "to": [100, 68], "marker": [100, 73]}
    # CENTER / NONE / unknown behave like core's `end_dir || LEFT`
    assert g["center"] == g["none"] == g["undef"] == g["left"]
    assert g["short"]["to"] == [-10, 0]


def test_install_is_idempotent_and_never_stacks_a_second_layer(probe: dict) -> None:
    i = probe["install"]
    assert i["first"] is i["second"] is i["third"] is i["fourth"] is True
    assert i["changedByFirst"] and i["flag"]
    # another extension wrapped ON TOP; a later install did not wrap again
    assert i["stableAfterSecond"] is True
    assert i["drawn"] == i["distinct"] == 3  # the three user wires: each reached the original once


def test_install_is_feature_detected(probe: dict) -> None:
    d = probe["install"]["detect"]
    assert d["bare"] is False and d["bareKeys"] == []
    assert d["notFn"] is False and d["notFnValue"] == "nope"
    assert d["nullProto"] is False


def test_tucked_wires_are_skipped_and_each_gets_a_stub_at_its_input_end(probe: dict) -> None:
    t = probe["tucked"]
    # only the three of the user's own wires reached the original renderLink
    assert sorted(t["drawn"]) == sorted(t["userLinks"])
    assert not set(t["drawn"]) & set(t["broadcast"])
    # one stub per tucked wire: from `b` (the input end core computed) 18px back along end_dir
    assert t["moveTo"] == [t["expectB"][k] for k in ("k10", "k11", "d13", "s14")]
    assert t["lineTo"] == [[x - 18, y] for x, y in t["moveTo"]]
    assert [f[0] for f in t["fillText"]] == ["📡"] * 4
    assert [[f[1], f[2]] for f in t["fillText"]] == [[x - 23, y] for x, y in t["moveTo"]]
    # in the LINK'S colour (MODEL, MODEL, VAE, STRING), at the connection width
    assert [s[0] for s in t["strokes"]] == ["#B39DDB", "#B39DDB", "#FF6E6E", "#77ccaa"]
    assert {s[1] for s in t["strokes"]} == {3}
    # the context is left exactly as found
    assert t["saves"] == t["restores"] == 4 and t["stackDepth"] == 0
    assert t["alphaAfter"] == 1 and t["strokeStyleAfter"] == "#000"
    # the stub is decoration: every visited link is still in renderedPaths (core's own bookkeeping)
    assert t["renderedPaths"] == 7


def test_selecting_the_owner_reveals_every_wire(probe: dict) -> None:
    r = probe["revealOwner"]
    assert r["drawn"] == 7 and r["stubs"] == 0 and r["alphas"] == [1]


def test_selecting_a_target_reveals_only_its_own_wire(probe: dict) -> None:
    r = probe["revealTarget"]
    assert r["drawnBroadcast"] == [r["k10"]]
    assert r["stubs"] == 3  # the other three are still tucked, each with its stub


def test_the_session_switch_shows_everything_and_repaints(probe: dict) -> None:
    s = probe["showAll"]
    assert s["on"] is True and s["drawn"] == 7 and s["stubs"] == 0
    assert s["off"] is False and s["tuckedAgain"] == 3
    # turning it on AND off asks the canvas to repaint the link layer (fg + bg)
    assert s["repaints"] == [[True, True], [True, True]]


def test_zoomed_out_a_dot_replaces_the_glyph(probe: dict) -> None:
    assert probe["lowQuality"] == {"fillText": 0, "arcs": 4}


def test_stub_direction_and_colour_follow_the_link(probe: dict) -> None:
    d = probe["directions"]
    assert d["right"] == [[100, 100], [118, 100]]
    assert d["up"] == [[100, 100], [100, 82]]
    assert d["down"] == [[100, 100], [100, 118]]
    assert d["center"] == [[100, 100], [82, 100]]
    # a colour another feature set on the link (Distributor / Image Grid / Bypass own
    # `link.color`) is READ, never written
    assert probe["colour"] == {"custom": "#123456", "writes": "#123456", "typeColour": "#B39DDB"}


def test_a_tucked_wire_is_not_clickable_at_its_old_midpoint(probe: dict) -> None:
    h = probe["hit"]
    assert h["revealedHit"] is True  # while drawn it IS a click target
    assert h["tuckedMid"] is None
    assert h["tuckedPos"] == [True, True]  # NaN in place, never undefined
    assert h["tuckedPath"] is None
    # normal links are unaffected
    assert h["userStillHit"] is True and h["userPath"].startswith("path:")
    # a wire never drawn has the constructor's [0, 0] -- not a click target at the origin
    assert h["originClick"] is None
    # revealing it again rewrites the real centre marker and path
    assert h["afterReveal"]["finite"] is True and h["afterReveal"]["path"].startswith("path:")


def test_dim_scales_the_alpha_and_restores_it(probe: dict) -> None:
    d = probe["dim"]
    assert d["tuckedCount"] == 3
    assert d["stale"] == 3  # the look is cached per epoch: the property alone changes nothing
    assert d["broadcastAlphas"] == [0.25] * 4
    assert d["userAlphas"] == [1, 1, 1]
    assert d["stubs"] == 0  # a dim wire is visible: no stub
    assert d["alphaAfter"] == 1 and d["stack"] == 0
    assert d["revealedAlpha"] == 1 and d["othersStillDim"] == [0.25, 0.25, 0.25]
    # an error inside core's own drawing is NOT swallowed -- and the alpha is still restored
    assert d["coreError"] == {"thrown": "core drew badly", "alpha": 1}


def test_normal_look_is_untouched(probe: dict) -> None:
    n = probe["normal"]
    assert n["drawn"] == 7 and n["alphas"] == [1] and n["stubs"] == 0 and n["sawAll"] is True
    assert n["saves"] == 0  # the module never touched the context
    assert n["linkPathRewrittenByCore"] is True


def test_non_broadcast_links_and_the_drag_preview_pass_straight_through(probe: dict) -> None:
    p = probe["passthrough"]
    assert p["userDrawn"] == 3 and p["userAlphas"] == [1, 1, 1]
    assert p["userArgs"] == [[4, 3]] * 3
    assert p["previewReturn"] == "drawn" and p["noIdReturn"] == "drawn"  # core's return value
    assert p["lastTwoDrawn"] == [None, None]
    # a workflow with no broadcast records pays one empty index, nothing else
    assert p["plainDrawn"] == p["plainTotal"] and p["owners"] == 0


def test_an_exception_in_our_logic_falls_through_to_the_original_once(probe: dict) -> None:
    f = probe["failSafe"]
    assert f["thrown"] is None  # never reaches the render loop
    assert f["drawnAll"] == f["total"] == 7  # every wire shows
    assert f["stubs"] == 0
    assert f["warnings"] == 1 and "decide" in f["warnText"]  # warned ONCE, not per link per frame
    s = f["stub"]
    assert s["thrown"] is None and s["drawnAll"] == s["total"] == 7
    assert s["warnings"] == 1 and s["alpha"] == 1 and s["stack"] == 0
    assert s["posFinite"] is True


def test_reroute_segments_and_the_flash_overlay_draw_no_stub(probe: dict) -> None:
    r = probe["reroute"]
    assert r["drawn"] == 0  # no segment of a tucked wire reaches the original
    assert r["afterSegment"] == 0  # the segment ending at a reroute: no stub
    assert r["afterFinal"] == 1 and r["stubAt"] == [
        100,
        100,
    ]  # the final segment: ONE stub, at the real input
    assert r["afterFlash"] == 1  # the event flash: no second stub, no flash on a hidden wire
    assert (
        r["reroutePos"] == [True, True] and r["reroutePath"] is None
    )  # the SEGMENT's hit-test state
    assert r["dragging"] == 1  # a link being dragged draws no stub either


def test_selection_changes_repaint_the_link_layer_without_polling(probe: dict) -> None:
    s = probe["selection"]
    assert s["installed"] == [True, True]
    # exactly one repaint per mutation (a double install does not double it)
    assert s["afterSelect"] == 1 and s["afterDeselect"] == 2
    assert s["allDirty"] is True
    assert s["total"] == 6  # select, deselect, select, deselectAll, select, deselect
    assert s["returns"] == [None, None]  # the originals' results are passed through
    assert s["dictionary"] == []
    assert s["plainDirty"] == 0  # an ordinary workflow pays nothing
    assert s["showAllDirty"] == 0  # everything is shown already: nothing to repaint
    assert s["partial"] == {"ok": True, "hasDeselect": False, "hasAll": False}
    assert s["noProto"] is False


def test_the_chained_selection_callback_for_older_frontends(probe: dict) -> None:
    c = probe["selection"]["callback"]
    assert c["installed"] == [True, True]
    assert c["ret"] == "orig" and c["chainCalls"] == [1]  # the original ran first, once
    assert c["dirty"] == 1 + 1  # one per event (the re-verified wrapper repaints too)
    assert c["ret2"] == "theirs"  # re-wrapped after something replaced it, chaining theirs


def test_inside_a_subgraph_the_owner_lives_elsewhere_and_ids_collide(probe: dict) -> None:
    n = probe["nested"]
    assert n["innerTucked"] == {"drawn": 0, "stubs": 2}
    # the node with the MULTIPLIER's id (1) inside the subgraph is not the multiplier
    assert n["decoySelected"] == {"drawn": 0, "stubs": 2}
    assert n["targetSelected"]["drawn"] == [n["targetSelected"]["expect"]]
    # the outer wire into the instance: tucked, revealed by the multiplier, by the instance
    assert n["outerTucked"] is True and n["outerOwner"] is True and n["outerTarget"] is True
    assert n["owner"]["ownerGraph"] == "root" and n["owner"]["linkGraph"] == "sg-b"
    assert n["rootOwner"]["ownerGraph"] == n["rootOwner"]["linkGraph"] == "root"
    assert n["owner"]["ownerId"] == n["rootOwner"]["ownerId"] == "1"
    assert n["owner"]["look"] == "tucked"


def test_ensure_draw_hooks_resolves_the_canvas_class_and_is_idempotent(probe: dict) -> None:
    e = probe["ensure"]
    assert e["none"] == {"renderLink": False, "selection": False}
    assert e["a"] == e["b"] == {"renderLink": True, "selection": True}
    assert e["stable"] is True and e["callback"] is True


# --------------------------------------------------------- source structure


def test_source_installs_by_feature_detection_and_never_writes_link_color(source: str) -> None:
    code = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
    # feature-detected: `renderLink` must exist and be a function, else nothing is installed
    assert "typeof proto.renderLink !== 'function'" in code
    assert "if (!proto || typeof proto.renderLink !== 'function') return false" in code
    # `link.color` is READ (the stub colour) and never assigned; Distributor / Image Grid /
    # Bypass own it through LINK_COLOR_OWNER_KEY
    assert not re.search(r"\.color\s*=(?!=)", code)
    assert "LINK_COLOR_OWNER_KEY" not in code and "LINK_COLOR_RESYNC_HOOK" not in code
    assert "link?.color" in code


def test_the_wrapper_catches_everything_of_ours_and_warns_once(source: str) -> None:
    body = source[source.index("const broadcastRenderLink = function") :]
    body = body[: body.index("broadcastRenderLink[RENDER_HOOK_FLAG] = true")]
    assert body.count("try {") == 2 and body.count("warnOnce(") == 2
    assert "return original.apply(this, arguments)" in body
    assert "warned.has(tag)" in source and "warned.add(tag)" in source
    # core's own errors are not swallowed: the dim path only restores in a finally
    dim = source[source.index("function drawDimmed") :]
    dim = dim[: dim.index("\n}\n")]
    assert "} finally {" in dim and "ctx.globalAlpha = saved" in dim


def test_the_per_link_path_is_o1_no_polling_no_walks(source: str) -> None:
    code = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    for banned in (
        "setInterval",
        "setTimeout",
        "requestAnimationFrame",
        "walkLiveNodes",
        "walkGraphs",
        "snapshotFromRoot",
    ):
        assert banned not in code, banned
    body = source[source.index("function decideDraw") :]
    body = body[: body.index("\n}\n")]
    assert "broadcastLinkOwner(canvas?.graph, link.id)" in body


def test_selection_is_read_from_selected_nodes_and_repaint_hangs_on_the_mutators(
    source: str,
) -> None:
    assert "canvas.selected_nodes" in source
    assert (
        "const SELECTION_METHODS = Object.freeze(['select', 'deselect', 'deselectAll'])" in source
    )
    body = source[source.index("export function installSelectionRepaint") :]
    body = body[: body.index("\n}\n")]
    assert body.index("original.apply(this, args)") < body.index("repaintForSelection(this)")
    assert "setDirty?.(true, true)" in source


def test_drawing_is_link_level_only_nodes_2_0_rules(source: str) -> None:
    code = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
    for banned in (
        "onDrawForeground",
        "onDrawBackground",
        "onMouseDown",
        "onMouseEnter",
        "onMouseMove",
        "getContext('2d')",
        "addEventListener",
        "document.",
        "properties_info",
    ):
        assert banned not in code, banned
    # the only canvas method it patches that DRAWS is renderLink
    assert code.count("proto.renderLink = ") == 1


def test_no_broadcast_module_ever_writes_link_color() -> None:
    """`link.color` is not serialized and Distributor / Image Grid / Bypass own it
    through LINK_COLOR_OWNER_KEY: the dim look scales `ctx.globalAlpha` instead."""
    for name in (
        "broadcast_draw.js",
        "broadcast_plan.js",
        "broadcast_graph.js",
        "broadcast.js",
        "broadcast_ui.js",
    ):
        text = (WEB / "eps_image" / name).read_text(encoding="utf-8")
        code = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
        assert not re.search(r"\.color\s*=(?!=)", code), name
        assert "LINK_COLOR_OWNER_KEY" not in code, name


def test_nothing_in_the_planner_or_adapter_draws(source: str) -> None:
    for name in ("broadcast_plan.js", "broadcast_graph.js", "broadcast.js", "broadcast_ui.js"):
        text = (WEB / "eps_image" / name).read_text(encoding="utf-8")
        code = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        for banned in (
            "getContext(",
            "renderLink",
            "fillText",
            "beginPath(",
            "ctx.stroke",
            "globalAlpha",
        ):
            assert banned not in code, f"{name}: {banned}"
