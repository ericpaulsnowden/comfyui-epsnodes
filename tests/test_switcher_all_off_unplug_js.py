"""All-off switchers UNPLUG their optional targets (owner report
2026-11-06: "I'm having the image switcher node go into a Text Encode Qwen
Image node. The connector goes gray when everything is deselected (good),
but the workflow still doesn't run.").

Core's ``TextEncodeQwenImageEdit`` has an OPTIONAL ``image`` input; an
all-off switcher emits ``ExecutionBlocker(None)`` (FORMAT §6.4, unchanged)
and core skips ANY node that receives a blocker on ANY input. A core node
has no lazy skip, so ``web/eps_image/switcher.js`` now does what EPS Bypass
does (§6.18): while at least one row is wired and every wired row is off,
it disconnects every output target whose input is safe to leave empty
(bypass.js ``inputVerdict``) on a node that does not handle an all-off
upstream itself, remembers them in the ``EPSSwitcher.unplugged`` node
property, and replays that memory the moment a row comes back on.

Driven end to end through the REAL ``attach()`` under Node, with a fake
graph whose ``disconnectInput``/``connect`` fire ``onConnectionsChange`` on
BOTH ends the way litegraph does -- so the re-entrancy guard is exercised,
and a runaway loop trips the fake's depth limit instead of hanging.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from subgraph_paste import SUBGRAPH_PASTE_JS

REPO_ROOT = Path(__file__).resolve().parent.parent
SWITCHER_JS = REPO_ROOT / "web" / "eps_image" / "switcher.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

DIM = "rgba(128,128,128,0.25)"
PROP = "EPSSwitcher.unplugged"

PROBE_JS = r"""
import * as s from './extensions/comfyui-epsnodes/eps_image/switcher.js'
import { app } from './scripts/app.js'

const INPUT = 1
const OUTPUT = 2
const tick = () => new Promise((resolve) => setTimeout(resolve, 0))
const settle = async () => { await tick(); await tick(); await tick() }

globalThis.LiteGraph = { LGraphCanvas: { prototype: { renderLink() {} } } }

//__SUBGRAPH_PASTE__

let nextLinkId = 1
let depth = 0
let maxDepth = 0
let changeCalls = 0

function fire(node, ...args) {
  changeCalls += 1
  depth += 1
  maxDepth = Math.max(maxDepth, depth)
  if (depth > 25) throw new Error('onConnectionsChange recursion runaway')
  try { node.onConnectionsChange?.(...args) } finally { depth -= 1 }
}

function makeGraph(id) {
  const graph = { links: new Map(), _nodes: [], dirty: 0 }
  if (id !== undefined) graph.id = id
  graph.setDirtyCanvas = () => { graph.dirty += 1 }
  graph.add = (node) => { graph._nodes.push(node); node.graph = graph; return node }
  graph.remove = (node) => {
    for (let i = 0; i < (node.inputs || []).length; i++) node.disconnectInput(i)
    for (const out of node.outputs || []) {
      for (const id of [...(out.links || [])]) {
        const link = graph.links.get(id)
        const target = link && graph.getNodeById(link.target_id)
        if (target) target.disconnectInput(link.target_slot)
      }
    }
    graph._nodes = graph._nodes.filter((n) => n !== node)
    node.graph = null
  }
  graph.getNodeById = (id) => graph._nodes.find((n) => n.id === id) || null
  return graph
}

// litegraph's shapes: disconnectInput / connect notify BOTH ends.
function linkMethods(node) {
  node.disconnectInput = function (slot) {
    const input = this.inputs[slot]
    if (!input || input.link == null) return false
    const link = this.graph?.links.get(input.link)
    input.link = null
    if (link) {
      this.graph.links.delete(link.id)
      const origin = this.graph.getNodeById(link.origin_id)
      const out = origin?.outputs?.[link.origin_slot]
      if (out?.links) out.links = out.links.filter((id) => id !== link.id)
      fire(this, INPUT, slot, false, link, input)
      if (origin) fire(origin, OUTPUT, link.origin_slot, false, link, out)
    }
    return true
  }
  node.connect = function (slot, target, targetSlot) {
    const out = this.outputs?.[slot]
    const input = target?.inputs?.[targetSlot]
    if (!out || !input) return null
    if (input.link != null) target.disconnectInput(targetSlot)
    const link = {
      id: nextLinkId++, origin_id: this.id, origin_slot: slot,
      target_id: target.id, target_slot: targetSlot, type: out.type
    }
    this.graph.links.set(link.id, link)
    out.links = [...(out.links || []), link.id]
    input.link = link.id
    fire(this, OUTPUT, slot, true, link, out)
    fire(target, INPUT, targetSlot, true, link, input)
    return link
  }
  return node
}

function makeLoader(graph, id, type = 'IMAGE') {
  return graph.add(linkMethods({
    id, comfyClass: 'LoadImage', inputs: [], widgets: [],
    outputs: [{ name: type, type, links: [] }]
  }))
}

/** A target node of *comfyClass*; *inputs* are `[name, type, kind]` with
 * kind 'required' | 'optional' | 'widget' | 'shape' | 'undeclared'. */
function makeTarget(graph, id, comfyClass, inputs, extra = {}) {
  const nodeData = { input: { required: {}, optional: {} } }
  const slots = inputs.map(([name, type, kind]) => {
    if (kind === 'required' || kind === 'widget') nodeData.input.required[name] = [type]
    if (kind === 'optional') nodeData.input.optional[name] = [type]
    const slot = { name, type, link: null }
    if (kind === 'widget') slot.widget = { name }
    if (kind === 'shape') slot.shape = 7
    return slot
  })
  return graph.add(linkMethods({
    id, comfyClass, inputs: slots, outputs: [], widgets: [],
    constructor: { nodeData }, ...extra
  }))
}

function makeSwitcher(graph, id) {
  const node = linkMethods({
    id, comfyClass: 'EPSSwitcher', pos: [0, 0], size: [270, 100],
    flags: {}, properties: {},
    inputs: [
      { name: 'image_1', type: 'IMAGE', link: null },
      { name: 'toggles', type: 'STRING', link: null, widget: { name: 'toggles' } },
      { name: 'pass_masks', type: 'BOOLEAN', link: null, widget: { name: 'pass_masks' } }
    ],
    outputs: [
      { name: 'images', type: 'IMAGE', links: null },
      { name: 'masks', type: 'MASK', links: null }
    ],
    widgets: [
      { name: 'toggles', type: 'text', value: '{}', options: {} },
      { name: 'pass_masks', type: 'toggle', value: true, options: {} }
    ],
    addProperty(name, value) { this.properties[name] = value },
    setSize(size) { this.size = size },
    computeSize() { return [270, 100] },
    setDirtyCanvas() {},
    addInput(name, type, extra) {
      const input = { name, type, link: null, ...(extra || {}) }
      this.inputs.push(input)
      return input
    },
    removeInput(slot) {
      this.disconnectInput(slot)
      this.inputs.splice(slot, 1)
      for (let i = slot; i < this.inputs.length; i++) {
        const link = this.graph?.links.get(this.inputs[i].link)
        if (link) link.target_slot = i
      }
    },
    addOutput(name, type) { this.outputs.push({ name, type, links: null }) },
    removeOutput(idx) { this.outputs.splice(idx, 1) },
    addCustomWidget(widget) { this.widgets.push(widget); return widget },
    getConnectionPos(isInput, slot) { return [0, 10 + slot * 20] },
    // LGraphNode.configure's order: properties, then the per-slot
    // onConnectionsChange restore loop (isConnected hardcoded true, toggles
    // NOT yet restored), then widgets_values, then onConfigure(info).
    configure(info) {
      if (info.properties) this.properties = { ...info.properties }
      for (let i = 0; i < this.inputs.length; i++) {
        if (this.inputs[i].link != null) fire(this, INPUT, i, true, null, this.inputs[i])
      }
      if (info.widgets_values) {
        let n = 0
        for (const widget of this.widgets) {
          if (widget.serialize === false) continue
          if (n >= info.widgets_values.length) break
          widget.value = info.widgets_values[n++]
        }
      }
      this.onConfigure?.(info)
    }
  })
  return graph.add(node)
}

function makeCtx() {
  const noop = () => {}
  return {
    save: noop, restore: noop, beginPath: noop, roundRect: noop,
    rect: noop, fill: noop, stroke: noop, moveTo: noop, lineTo: noop, fillText: noop
  }
}

function clickRow(node, name) {
  node.onDrawForeground(makeCtx(), null, null)
  const rect = node.__epsSwitcherRowRects.find((r) => r.name === name)
  if (!rect) throw new Error(`no toggle box drawn for ${name}`)
  node.onMouseDown({}, [rect.x + 1, rect.y + 1], null)
}

function clickHeader(node) {
  const header = node.widgets.find((w) => w.name === '__eps_switcher_toggle_all')
  header.mouse({ type: 'pointerdown' }, [10, 10], node)
}

function applyToggles(node, value) {
  const widget = node.widgets.find((w) => w.name === 'toggles')
  widget.value = value
  widget.callback?.(value, null, node)
}

const inputSlot = (node, name) => node.inputs.findIndex((i) => i.name === name)
const plug = (origin, slot, target, name) => origin.connect(slot, target, inputSlot(target, name))
const fedBy = (target, name) => {
  const id = target.inputs[inputSlot(target, name)]?.link
  if (id == null) return null
  const link = target.graph.links.get(id)
  return link ? [link.origin_id, link.origin_slot] : 'dangling'
}
const memory = (sw) => {
  const raw = sw.properties[ 'EPSSwitcher.unplugged' ]
  return raw == null ? null : JSON.parse(raw)
}
const colourOf = (target, name) => {
  const link = target.graph.links.get(target.inputs[inputSlot(target, name)]?.link)
  return link ? (link.color ?? null) : 'unwired'
}

async function rig(graphId) {
  const graph = makeGraph(graphId)
  const sw = makeSwitcher(graph, 1)
  s.attach(sw)
  const a = makeLoader(graph, 10)
  const b = makeLoader(graph, 11)
  plug(a, 0, sw, 'image_1')
  await settle()
  plug(b, 0, sw, 'image_2')
  await settle()
  const qwen = makeTarget(graph, 20, 'TextEncodeQwenImageEdit', [
    ['clip', 'CLIP', 'required'], ['vae', 'VAE', 'optional'], ['image', 'IMAGE', 'optional']
  ])
  const encode = makeTarget(graph, 21, 'VAEEncode', [
    ['pixels', 'IMAGE', 'required'], ['vae', 'VAE', 'required']
  ])
  const maskUser = makeTarget(graph, 22, 'MaskUser', [['mask', 'MASK', 'optional']])
  plug(sw, 0, qwen, 'image')
  plug(sw, 0, encode, 'pixels')
  plug(sw, 1, maskUser, 'mask')
  return { graph, sw, a, b, qwen, encode, maskUser }
}

const out = {}

// --- pure memory helpers ------------------------------------------------------
{
  const good = JSON.stringify({ owner: 7, outputs: { images: [{ node: 3, input: 'image' }, { bad: 1 }], masks: [] } })
  out.pure = {
    prop: s.UNPLUGGED_PROPERTY,
    parsed: s.parseUnplugMemory(good, { id: 7 }),
    parsedStringId: s.parseUnplugMemory(good, { id: '7' }),
    foreign: s.parseUnplugMemory(good, { id: 8 }),
    noOwner: s.parseUnplugMemory('{"outputs": {"images": [{"node": 3, "input": "image"}]}}', { id: 7 }),
    malformed: s.parseUnplugMemory('not json', { id: 7 }),
    object: s.parseUnplugMemory({ owner: 7, outputs: { images: [{ node: 3, input: 'x' }] } }, { id: 7 }),
    serialized: s.serializeUnplugMemory({ id: 7 }, { images: [{ node: 3, input: 'image' }], masks: [] }),
    serializedEmpty: s.serializeUnplugMemory({ id: 7 }, { images: [] }),
    exempt: [...s.UNPLUG_EXEMPT_CLASSES].sort()
  }
}

// --- the owner's report: last row off unplugs, one row on replugs ------------
{
  const { sw, qwen, encode, maskUser } = await rig()
  const steps = {}
  clickRow(sw, 'image_1')
  steps.oneOff = { qwen: fedBy(qwen, 'image'), memory: memory(sw) }
  changeCalls = 0
  maxDepth = 0
  clickRow(sw, 'image_2')
  steps.allOff = {
    qwen: fedBy(qwen, 'image'),
    encode: fedBy(encode, 'pixels'),
    encodeColour: colourOf(encode, 'pixels'),
    maskUser: fedBy(maskUser, 'mask'),
    memory: memory(sw),
    rewiringFlag: Boolean(sw.__epsSwitcherRewiring),
    changeCalls,
    maxDepth,
    images: (sw.outputs[0].links || []).length,
    masks: (sw.outputs[1].links || []).length
  }
  clickRow(sw, 'image_2')
  steps.oneBack = {
    qwen: fedBy(qwen, 'image'),
    encode: fedBy(encode, 'pixels'),
    encodeColour: colourOf(encode, 'pixels'),
    maskUser: fedBy(maskUser, 'mask'),
    memory: memory(sw),
    hasProp: 'EPSSwitcher.unplugged' in sw.properties,
    rewiringFlag: Boolean(sw.__epsSwitcherRewiring)
  }
  out.report = steps
}

// --- Toggle All and a Universal State Apply both unplug and replug ----------
{
  const { sw, qwen } = await rig()
  clickHeader(sw)
  const headerOff = fedBy(qwen, 'image')
  clickHeader(sw)
  const headerOn = fedBy(qwen, 'image')
  applyToggles(sw, '{"image_1": false, "image_2": false}')
  const applyOff = fedBy(qwen, 'image')
  applyToggles(sw, '{"image_2": false}')
  const applyOn = fedBy(qwen, 'image')
  out.otherRoutes = { headerOff, headerOn, applyOff, applyOn, memory: memory(sw) }
}

// --- excluded classes, unrestorable targets, and the safe input kinds -------
{
  const { graph, sw } = await rig()
  const keep = {}
  const exempt = [
    'EPSCrossSweep', 'EPSResolution', 'EPSImageGrid', 'EPSCheckpointSwitcher',
    'EPSBypass', 'EPSDistributor', 'EPSSwitcher', 'EPSModelSwitcher',
    'EPSClipSwitcher', 'EPSVaeSwitcher', 'EPSLatentSwitcher'
  ]
  let id = 100
  for (const cls of exempt) {
    const t = makeTarget(graph, id++, cls, [['image', 'IMAGE', 'optional']])
    plug(sw, 0, t, 'image')
    keep[cls] = t
  }
  const reroute = makeTarget(graph, id++, 'Reroute', [['', '*', 'shape']], { type: 'Reroute' })
  sw.connect(0, reroute, 0)
  const unknown = makeTarget(graph, id++, 'Mystery', [['image', 'IMAGE', 'undeclared']])
  plug(sw, 0, unknown, 'image')
  const widgetBacked = makeTarget(graph, id++, 'WidgetUser', [['image', 'IMAGE', 'widget']])
  plug(sw, 0, widgetBacked, 'image')
  const dynamic = makeTarget(graph, id++, 'DynamicUser', [['image_7', 'IMAGE', 'shape']])
  plug(sw, 0, dynamic, 'image_7')
  clickHeader(sw) // all off
  const kept = {}
  for (const [cls, t] of Object.entries(keep)) kept[cls] = fedBy(t, 'image') !== null
  out.classes = {
    kept,
    reroute: reroute.inputs[0].link != null,
    unknown: fedBy(unknown, 'image') !== null,
    widgetBacked: fedBy(widgetBacked, 'image') !== null,
    dynamic: fedBy(dynamic, 'image_7') !== null,
    remembered: (memory(sw)?.outputs?.images || []).map((t) => t.node).sort((x, y) => x - y)
  }
}

// --- nothing wired: a fresh switcher keeps the wire the user just dragged ---
{
  const graph = makeGraph()
  const sw = makeSwitcher(graph, 1)
  s.attach(sw)
  const qwen = makeTarget(graph, 20, 'TextEncodeQwenImageEdit', [['image', 'IMAGE', 'optional']])
  plug(sw, 0, qwen, 'image')
  applyToggles(sw, '{}')
  clickHeader(sw) // no rows: a no-op
  out.nothingWired = {
    allOff: s.isSwitcherAllOff(sw),
    withRows: s.isSwitcherAllOffWithWiredRows(sw),
    qwen: fedBy(qwen, 'image'),
    memory: memory(sw)
  }
}

// --- unwiring every row keeps the memory; re-wiring a row that is ON replugs
{
  const { graph, sw, a, qwen } = await rig()
  clickRow(sw, 'image_1')
  clickRow(sw, 'image_2') // all off -> qwen unplugged
  const unplugged = fedBy(qwen, 'image')
  const c = makeLoader(graph, 12)
  plug(c, 0, sw, 'image_3') // the spare: never toggled, so ON
  await settle()
  const newRowOn = fedBy(qwen, 'image')
  out.newRow = { unplugged, newRowOn, memory: memory(sw) }
}
{
  const { sw, a, qwen } = await rig()
  clickHeader(sw) // all off -> qwen unplugged
  sw.disconnectInput(inputSlot(sw, 'image_1'))
  await settle()
  sw.disconnectInput(inputSlot(sw, 'image_2'))
  await settle()
  const noneWired = { qwen: fedBy(qwen, 'image'), remembered: memory(sw) !== null }
  plug(a, 0, sw, 'image_1') // its `false` is sticky: still all off
  await settle()
  const rewiredOff = fedBy(qwen, 'image')
  clickRow(sw, 'image_1')
  out.rewire = { noneWired, rewiredOff, rowOn: fedBy(qwen, 'image'), memory: memory(sw) }
}

// --- a target vanished / taken before replug is skipped without error -------
{
  const { graph, sw, qwen, maskUser } = await rig()
  const other = makeTarget(graph, 30, 'Other', [['image', 'IMAGE', 'optional']])
  plug(sw, 0, other, 'image')
  clickHeader(sw)
  const before = memory(sw)
  graph.remove(other)
  const thief = makeLoader(graph, 40, 'MASK')
  plug(thief, 0, maskUser, 'mask') // the socket is taken meanwhile
  let error = null
  try { clickHeader(sw) } catch (e) { error = String(e) }
  out.vanished = {
    remembered: (before?.outputs?.images || []).length,
    error,
    qwen: fedBy(qwen, 'image'),
    maskUser: fedBy(maskUser, 'mask'),
    memory: memory(sw)
  }
}

// --- a pasted copy (new id) ignores the inherited memory --------------------
{
  const { graph, qwen } = await rig()
  qwen.disconnectInput(inputSlot(qwen, 'image')) // the original's target, free
  const copy = makeSwitcher(graph, 2)
  s.attach(copy)
  copy.configure({
    id: -1,
    widgets_values: ['{}', true],
    properties: { 'EPSSwitcher.unplugged': JSON.stringify({ owner: 1, outputs: { images: [{ node: 20, input: 'image' }] } }) }
  })
  const src = makeLoader(graph, 50)
  plug(src, 0, copy, 'image_1') // a row on
  await settle()
  out.paste = { qwen: fedBy(qwen, 'image'), memory: memory(copy) }
}

// --- the same node id in ANOTHER workflow is not the same node ---------------
// A node id is only unique inside one graph. Copy the switcher out of
// workflow A and paste it as the first node of an empty workflow B: litegraph
// (`_deserializeItems`) adds it -- it gets id 1 again, so the stamped `owner`
// still matches -- and only THEN configures it with `id: -1` and the copied
// properties. Node 20 of B is an unrelated node.
{
  const a = await rig('graph-A')
  clickHeader(a.sw) // all off: unplugged + remembered
  const copiedProps = { ...a.sw.properties }
  const graphB = makeGraph('graph-B')
  const stranger = makeTarget(graphB, 20, 'SomeOtherNode', [['image', 'IMAGE', 'optional']])
  const pasted = makeSwitcher(graphB, 1) // graph.add: same id as the original
  s.attach(pasted)
  pasted.configure({ id: -1, widgets_values: ['{}', true], properties: copiedProps })
  const memoryAfterConfigure = memory(pasted)
  const src = makeLoader(graphB, 50)
  plug(src, 0, pasted, 'image_1') // a row on -> would replug the memory
  await settle()
  out.crossGraph = { memoryAfterConfigure, stranger: fedBy(stranger, 'image'), memory: memory(pasted) }
}

// --- Save As / Duplicate: another graph id, the SAME node ids -----------------
// workflowStore.saveAs / workflowService.duplicateWorkflow give the copy a
// new workflow id; every node keeps its id, so the memory is still valid.
{
  const a = await rig('graph-A')
  clickHeader(a.sw)
  const saved = { ...a.sw.properties }
  a.graph.id = 'graph-saved-as'
  a.sw.configure({ id: a.sw.id, widgets_values: ['{"image_2": false}', true], properties: saved })
  await settle()
  out.saveAs = { qwen: fedBy(a.qwen, 'image'), memory: memory(a.sw) }
}

// --- Alt-drag / Ctrl+C ghost: clone() configures with `id: undefined`, off-graph
{
  const a = await rig('graph-A')
  clickHeader(a.sw)
  const saved = { ...a.sw.properties }
  const ghost = makeSwitcher(makeGraph('ghost-graph'), 1)
  s.attach(ghost)
  ghost.graph = null // never joins a graph
  let fetches = 0
  globalThis.fetch = () => { fetches += 1; return Promise.resolve({ ok: true, json: async () => ({}) }) }
  ghost.configure({ id: undefined, widgets_values: ['{}', true], properties: { ...saved } })
  await settle()
  out.ghost = { memory: memory(ghost), fetches }
}

// --- a pasted SUBGRAPH (frontend 1.52.7) ------------------------------------
// Its interior nodes are configured with REAL ids after the clipboard remap,
// and the definition gets a NEW uuid. Interior ids [1, 10, 7, 20] pasted into
// a workflow using 7 and 19 become [1, 10, 20, 21]: the remembered target 20
// now names the decoy, the intended consumer is 21.
const QWEN = [['clip', 'CLIP', 'required'], ['vae', 'VAE', 'optional'], ['image', 'IMAGE', 'optional']]
function subgraphRig(subgraphId, ids) {
  const graph = makeGraph(subgraphId)
  graph.isRootGraph = false
  const sw = makeSwitcher(graph, ids.sw)
  s.attach(sw)
  const loader = makeLoader(graph, ids.loader)
  const decoy = makeTarget(graph, ids.decoy, 'TextEncodeQwenImageEdit', QWEN)
  const intended = makeTarget(graph, ids.intended, 'TextEncodeQwenImageEdit', QWEN)
  plug(loader, 0, sw, 'image_1')
  return { graph, sw, loader, decoy, intended }
}
{
  const src = subgraphRig('sub-1', { sw: 1, loader: 10, decoy: 7, intended: 20 })
  await settle()
  plug(src.sw, 0, src.intended, 'image')
  await settle()
  clickHeader(src.sw) // all off: unplugged + remembered
  const saved = { ...src.sw.properties }
  const [swId, loaderId, decoyId, intendedId] =
    remapPastedInteriorIds([1, 10, 7, 20], [7, 19], 19).map((m) => m.to)
  const dst = subgraphRig('sub-2', { sw: swId, loader: loaderId, decoy: decoyId, intended: intendedId })
  dst.sw.configure({ id: swId, widgets_values: ['{}', true], properties: { ...saved } })
  const memoryAfterConfigure = memory(dst.sw)
  await settle() // the row is on and the memory trusted -> it would replug
  out.subgraphPaste = {
    ids: [swId, loaderId, decoyId, intendedId],
    memoryAfterConfigure,
    decoy: fedBy(dst.decoy, 'image'),
    intended: fedBy(dst.intended, 'image')
  }
  // the SAME definition reloaded (undo / Save As / Duplicate): kept, replugs
  const stamp = memory(src.sw)
  src.sw.configure({ id: 1, widgets_values: ['{}', true], properties: { ...saved } })
  await settle()
  out.subgraphReload = { stamp, intended: fedBy(src.intended, 'image'), decoy: fedBy(src.decoy, 'image') }
}
// --- converted into a subgraph WITH its target: written in the root graph, ----
// configured in a brand-new subgraph -> kept, and restamped for that subgraph
{
  const a = await rig('root')
  clickHeader(a.sw)
  const saved = { ...a.sw.properties }
  out.rootStamp = memory(a.sw)
  const inner = makeGraph('sub-new')
  inner.isRootGraph = false
  for (const node of [a.sw, a.a, a.b, a.qwen, a.encode, a.maskUser]) inner.add(node)
  a.sw.configure({ id: 1, widgets_values: ['{"image_2": false}', true], properties: saved })
  await settle()
  out.converted = { stamp: memory(a.sw), qwen: fedBy(a.qwen, 'image') }
}

// --- configure: memory + a row on replugs once the restore settles ----------
{
  const { sw, qwen, maskUser } = await rig()
  clickHeader(sw) // all off: unplugged + remembered
  const saved = { ...sw.properties }
  // A load/undo of a state where a row is ON again, memory still present.
  sw.configure({ id: sw.id, widgets_values: ['{"image_2": false}', true], properties: saved })
  const sync = fedBy(qwen, 'image') // deferred: not yet
  await settle()
  out.configureReplug = {
    sync,
    qwen: fedBy(qwen, 'image'),
    maskUser: fedBy(maskUser, 'mask'),
    memory: memory(sw)
  }
}

// --- configure guard: the restore loop never rewires from half state --------
{
  const { sw, qwen } = await rig()
  clickHeader(sw) // all off
  const saved = { ...sw.properties }
  applyToggles(sw, '{}') // replugs; the widget now reads all-on
  qwen.disconnectInput(inputSlot(qwen, 'image')) // as in the saved (all-off) file
  // Restore an all-off save: the loop fires while toggles still read '{}'.
  sw.configure({ id: sw.id, widgets_values: ['{"image_1": false, "image_2": false}', true], properties: saved })
  const duringLoop = fedBy(qwen, 'image')
  await settle()
  out.configureGuard = { duringLoop, settled: fedBy(qwen, 'image'), memory: memory(sw) }
}

// --- a whole-graph load (app.configuringGraph) waits for it to finish -------
{
  const { sw, qwen } = await rig()
  clickHeader(sw)
  const saved = { ...sw.properties }
  app.configuringGraph = true
  sw.configure({ id: sw.id, widgets_values: ['{}', true], properties: saved })
  await settle()
  const whileLoading = fedBy(qwen, 'image')
  app.configuringGraph = false
  await settle()
  out.graphLoad = { whileLoading, after: fedBy(qwen, 'image'), memory: memory(sw) }
}

// --- `toggles` arriving over a wire: never unplugs --------------------------
{
  const { graph, sw, qwen } = await rig()
  const str = graph.add(linkMethods({ id: 60, comfyClass: 'PrimitiveString', inputs: [], widgets: [],
    outputs: [{ name: 'STRING', type: 'STRING', links: [] }] }))
  plug(str, 0, sw, 'toggles')
  applyToggles(sw, '{"image_1": false, "image_2": false}')
  out.togglesWired = { qwen: fedBy(qwen, 'image'), memory: memory(sw) }
}

// --- a link landing while all-off STAYS (dimmed); the next toggle-off unplugs
{
  const { graph, sw, qwen } = await rig()
  clickHeader(sw) // all off: qwen unplugged
  const late = makeTarget(graph, 70, 'Late', [['image', 'IMAGE', 'optional']])
  plug(sw, 0, late, 'image') // dragged onto an already all-off switcher
  const landed = {
    late: fedBy(late, 'image'),
    colour: colourOf(late, 'image'),
    remembered: (memory(sw)?.outputs?.images || []).map((t) => t.node)
  }
  const floating = { id: -1, origin_id: 1, origin_slot: 0 }
  sw.outputs[0]._floatingLinks = new Set([floating])
  clickRow(sw, 'image_1') // a row on: replug qwen (late is untouched)
  const rowOn = { qwen: fedBy(qwen, 'image'), late: fedBy(late, 'image'), memory: memory(sw) }
  clickRow(sw, 'image_1') // off again: the transition unplugs both
  out.midAir = {
    landed,
    rowOn,
    offAgain: {
      qwen: fedBy(qwen, 'image'),
      late: fedBy(late, 'image'),
      remembered: (memory(sw)?.outputs?.images || []).map((t) => t.node).sort((x, y) => x - y)
    },
    floatingIntact: sw.outputs[0]._floatingLinks.size === 1 && sw.outputs[0]._floatingLinks.has(floating)
  }
}

// --- re-entrancy: an onConnectionsChange during the pass is ignored --------
{
  const { sw } = await rig()
  let seenDuring = 0
  const original = sw.disconnectInput
  // Watch our own handler being re-entered while the flag is up.
  const wrapped = sw.onConnectionsChange
  sw.onConnectionsChange = function (...args) {
    if (this.__epsSwitcherRewiring) seenDuring += 1
    return wrapped.apply(this, args)
  }
  changeCalls = 0
  maxDepth = 0
  clickHeader(sw)
  out.reentry = { seenDuring, changeCalls, maxDepth, flag: Boolean(sw.__epsSwitcherRewiring),
    untouched: sw.disconnectInput === original }
}

// --- the Model switcher's models_low tail is unplugged too ------------------
{
  const graph = makeGraph()
  const sw = makeSwitcher(graph, 1)
  sw.comfyClass = 'EPSModelSwitcher'
  sw.inputs = [
    { name: 'model_1', type: 'MODEL', link: null },
    { name: 'toggles', type: 'STRING', link: null, widget: { name: 'toggles' } }
  ]
  sw.outputs = [{ name: 'models', type: 'MODEL', links: null }, { name: 'models_low', type: 'MODEL', links: null }]
  sw.widgets = [{ name: 'toggles', type: 'text', value: '{}', options: {} }]
  s.attach(sw)
  sw.properties['High/low pairs'] = true
  sw.onPropertyChanged?.('High/low pairs', true, false)
  await settle()
  const m = makeLoader(graph, 10, 'MODEL')
  plug(m, 0, sw, 'model_1')
  await settle()
  const t = makeTarget(graph, 20, 'ModelUser', [['model', 'MODEL', 'optional'], ['model_low', 'MODEL', 'optional']])
  sw.connect(0, t, 0)
  sw.connect(sw.outputs.findIndex((o) => o.name === 'models_low'), t, 1)
  const wired = { model: fedBy(t, 'model'), low: fedBy(t, 'model_low') }
  clickHeader(sw)
  const off = { model: fedBy(t, 'model'), low: fedBy(t, 'model_low'), memory: memory(sw) }
  clickHeader(sw)
  out.modelLow = { wired, off, onModel: fedBy(t, 'model') !== null, onLow: fedBy(t, 'model_low') !== null, memory: memory(sw) }
}

process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def probe(tmp_path_factory: pytest.TempPathFactory, install_switcher_js) -> dict:
    layout = tmp_path_factory.mktemp("web_root_all_off_unplug")
    install_switcher_js(layout)
    (layout / "probe.mjs").write_text(
        PROBE_JS.replace("//__SUBGRAPH_PASTE__", SUBGRAPH_PASTE_JS), encoding="utf-8"
    )
    result = subprocess.run(
        [NODE, str(layout / "probe.mjs")],
        capture_output=True,
        encoding="utf-8",
        timeout=60,
        cwd=layout,
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


def _mem(owner: int, **outputs: list[tuple[int, str]]) -> dict:
    return {
        "owner": owner,
        "outputs": {name: [{"node": n, "input": i} for n, i in items] for name, items in outputs.items()},
    }


def test_pure_memory_helpers(probe: dict) -> None:
    pure = probe["pure"]
    assert pure["prop"] == PROP
    assert pure["parsed"] == {"images": [{"node": 3, "input": "image"}]}
    assert pure["parsedStringId"] == pure["parsed"]
    assert pure["foreign"] == {}
    assert pure["noOwner"] == {}
    assert pure["malformed"] == {}
    assert pure["object"] == {"images": [{"node": 3, "input": "x"}]}
    assert json.loads(pure["serialized"]) == _mem(7, images=[(3, "image")])
    assert pure["serializedEmpty"] is None
    assert pure["exempt"] == sorted(
        [
            "EPSBypass",
            "EPSCheckpointSwitcher",
            "EPSClipSwitcher",
            "EPSCrossSweep",
            "EPSDistributor",
            "EPSImageGrid",
            "EPSLatentSwitcher",
            "EPSModelSwitcher",
            "EPSResolution",
            "EPSSwitcher",
            "EPSVaeSwitcher",
        ]
    )


def test_all_off_unplugs_optional_targets_and_one_row_back_replugs(probe: dict) -> None:
    steps = probe["report"]
    # One row still on: nothing happens.
    assert steps["oneOff"] == {"qwen": [1, 0], "memory": None}
    off = steps["allOff"]
    # The owner's case: the Qwen encoder's optional `image` is unplugged...
    assert off["qwen"] is None
    # ...the masks tail's optional target too...
    assert off["maskUser"] is None
    # ...a REQUIRED input keeps its wire, dimmed (the backend blocker skips it).
    assert off["encode"] == [1, 0]
    assert off["encodeColour"] == DIM
    assert off["memory"] == _mem(1, images=[(20, "image")], masks=[(22, "mask")])
    assert (off["images"], off["masks"]) == (1, 0)
    assert off["rewiringFlag"] is False
    back = steps["oneBack"]
    assert back["qwen"] == [1, 0]
    assert back["maskUser"] == [1, 1]
    assert back["encode"] == [1, 0]
    assert back["encodeColour"] is None
    assert back["memory"] is None
    assert back["hasProp"] is False
    assert back["rewiringFlag"] is False


def test_toggle_all_and_state_apply_unplug_and_replug(probe: dict) -> None:
    routes = probe["otherRoutes"]
    assert routes["headerOff"] is None
    assert routes["headerOn"] == [1, 0]
    assert routes["applyOff"] is None
    assert routes["applyOn"] == [1, 0]
    assert routes["memory"] is None


def test_exempt_classes_unknown_and_unrestorable_targets_keep_their_wire(probe: dict) -> None:
    classes = probe["classes"]
    assert classes["kept"] == {name: True for name in classes["kept"]}
    assert len(classes["kept"]) == 11
    assert classes["reroute"] is True  # a nameless legacy Reroute NODE input
    assert classes["unknown"] is True  # not declared anywhere: treated as required
    # Safe kinds go: optional (qwen #20), widget-backed, hollow-circle dynamic.
    assert classes["widgetBacked"] is False
    assert classes["dynamic"] is False
    assert classes["remembered"] == [20, 113, 114]


def test_nothing_wired_unplugs_nothing(probe: dict) -> None:
    assert probe["nothingWired"] == {
        "allOff": True,
        "withRows": False,
        "qwen": [1, 0],
        "memory": None,
    }


def test_unwiring_every_row_keeps_memory_and_a_rewired_row_replugs(probe: dict) -> None:
    new_row = probe["newRow"]
    assert new_row == {"unplugged": None, "newRowOn": [1, 0], "memory": None}
    rewire = probe["rewire"]
    # No row wired: nothing to unplug, nothing to replug -- memory kept.
    assert rewire["noneWired"] == {"qwen": None, "remembered": True}
    assert rewire["rewiredOff"] is None
    assert rewire["rowOn"] == [1, 0]
    assert rewire["memory"] is None


def test_vanished_or_taken_targets_are_skipped_without_error(probe: dict) -> None:
    vanished = probe["vanished"]
    assert vanished["remembered"] == 2
    assert vanished["error"] is None
    assert vanished["qwen"] == [1, 0]
    assert vanished["maskUser"] == [40, 0]  # never stomped
    assert vanished["memory"] is None


def test_pasted_copy_ignores_inherited_memory(probe: dict) -> None:
    assert probe["paste"] == {"qwen": None, "memory": None}


def test_a_pasted_copy_in_another_workflow_never_wires_an_unrelated_node(probe: dict) -> None:
    # Same node id (1), same target id (20), different graph: the copy is
    # configured without a real id, so it owns no memory at all.
    assert probe["crossGraph"] == {"memoryAfterConfigure": None, "stranger": None, "memory": None}


def test_a_pasted_subgraphs_interior_never_wires_an_unrelated_node(probe: dict) -> None:
    # The copy-id check cannot see this: the interior is configured with real
    # ids, and the clipboard remap makes the remembered target 20 the decoy.
    p = probe["subgraphPaste"]
    assert p["ids"] == [1, 10, 20, 21]
    assert p["decoy"] is None  # the harm: an unrelated node gets wired
    assert p["intended"] is None
    assert p["memoryAfterConfigure"] is None


def test_a_subgraph_reload_keeps_the_memory_and_replugs(probe: dict) -> None:
    p = probe["subgraphReload"]
    assert p["stamp"]["owner"] == 1
    assert p["stamp"]["graph"] == "sub-1"
    assert p["intended"] == [1, 0]
    assert p["decoy"] is None


def test_converting_a_node_into_a_subgraph_with_its_target_keeps_the_memory(probe: dict) -> None:
    assert "graph" not in probe["rootStamp"]
    assert probe["converted"]["qwen"] == [1, 0]


def test_save_as_keeps_every_node_id_so_the_memory_still_replugs(probe: dict) -> None:
    assert probe["saveAs"] == {"qwen": [1, 0], "memory": None}


def test_a_clone_ghost_drops_the_memory_and_makes_no_server_call(probe: dict) -> None:
    assert probe["ghost"] == {"memory": None, "fetches": 0}


def test_configure_with_memory_and_a_row_on_replugs_after_settling(probe: dict) -> None:
    result = probe["configureReplug"]
    assert result["sync"] is None
    assert result["qwen"] == [1, 0]
    assert result["maskUser"] == [1, 1]
    assert result["memory"] is None


def test_configure_restore_loop_never_rewires_from_half_state(probe: dict) -> None:
    result = probe["configureGuard"]
    assert result["duringLoop"] is None
    assert result["settled"] is None
    assert result["memory"] == _mem(1, images=[(20, "image")], masks=[(22, "mask")])


def test_whole_graph_load_waits_until_configuring_ends(probe: dict) -> None:
    assert probe["graphLoad"] == {"whileLoading": None, "after": [1, 0], "memory": None}


def test_wired_toggles_never_unplug(probe: dict) -> None:
    assert probe["togglesWired"] == {"qwen": [1, 0], "memory": None}


def test_late_link_stays_dimmed_until_the_next_toggle_off(probe: dict) -> None:
    """A wire dragged onto an already all-off switcher never vanishes under
    the cursor: it stays, dimmed, NOT remembered; the next transition into
    all-off (row on, then off again) unplugs and remembers it. A link still
    being dragged (`_floatingLinks`) is never touched."""
    mid = probe["midAir"]
    assert mid["landed"] == {"late": [1, 0], "colour": DIM, "remembered": [20]}
    assert mid["rowOn"] == {"qwen": [1, 0], "late": [1, 0], "memory": None}
    assert mid["offAgain"] == {"qwen": None, "late": None, "remembered": [20, 70]}
    assert mid["floatingIntact"] is True


def test_reentrancy_guard_bounds_the_pass(probe: dict) -> None:
    reentry = probe["reentry"]
    # Our own disconnects re-enter onConnectionsChange (images + masks) and
    # are ignored; nothing recurses.
    assert reentry["seenDuring"] == 2
    assert reentry["maxDepth"] <= 2
    assert reentry["flag"] is False
    assert reentry["untouched"] is True


def test_models_low_tail_is_unplugged_and_replugged(probe: dict) -> None:
    result = probe["modelLow"]
    assert result["wired"] == {"model": [1, 0], "low": [1, 1]}
    assert result["off"] == {
        "model": None,
        "low": None,
        "memory": _mem(1, models=[(20, "model")], models_low=[(20, "model_low")]),
    }
    assert result["onModel"] is True
    assert result["onLow"] is True
    assert result["memory"] is None


# ------------------------------------------------------------ source pins


@pytest.fixture(scope="module")
def source() -> str:
    return SWITCHER_JS.read_text(encoding="utf-8")


def test_shared_machinery_is_imported_not_copied(source: str) -> None:
    assert "} from './number_controller.js'" in source
    for name in ("configuredAsCopy", "normalizeRememberedLinks", "reconnectRememberedTargets"):
        assert re.search(rf"import \{{[^}}]*\b{name}\b[^}}]*\}} from './number_controller.js'", source), name
    assert "import { inputVerdict, sameTarget } from './bypass.js'" in source
    for name in (
        "collectOutputTargets",
        "disconnectAllTargets",
        "reconnectRememberedTargets",
        "normalizeRememberedLinks",
        "configuredAsCopy",
        "inputVerdict",
        "sameTarget",
    ):
        assert f"function {name}(" not in source, name
