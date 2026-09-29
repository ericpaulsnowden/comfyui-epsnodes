"""Frontend tests for EPS Bypass (FORMAT.md section 6.18) --
``web/eps_image/bypass.js``.

The node's whole job is done by the frontend: switching OFF remembers the
output's targets and UNPLUGS them, switching ON replays them, and a
required-input guard refuses the off when unplugging would fail the run. That
is wiring, so most of these tests drive the REAL, unmodified ``attach()``
export end to end against a small fake litegraph (nodes, real link objects,
``connect``/``disconnectInput`` that fire ``onConnectionsChange`` the way
litegraph does, a widget callback fired the way ``BaseWidget.setValue`` fires
it) under Node -- the same approach ``tests/test_number_controller_js.py``
takes for the rebuild law, extended to a whole graph. There is no jsdom in
this repo and none is needed: this node has no DOM at all.

The pure decisions (`inputVerdict`, `parseMemory`, `refusalMessage`, ...) are
exported and probed directly. The remaining structural rules -- imported (not
copied) shared helpers, "loading never rewires", "auto re-enable only from
the connection hook", read-only ``settle`` -- are pinned as source assertions
(``tests/test_frame_saver_paste_js.py``'s convention for code with no browser
harness).

Skips cleanly when Node isn't installed. What this CANNOT cover, and the rig
must: a real litegraph ``connect``/``isValidConnection`` type refusal, the Vue
renderer writing its model around the toggle callback, real ``nodeData`` off a
real ``/object_info``, and ``graphToPrompt`` actually omitting the input.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB = REPO_ROOT / "web"
BYPASS_JS = WEB / "eps_image" / "bypass.js"
NUMBER_CONTROLLER_JS = WEB / "eps_image" / "number_controller.js"
DISTRIBUTOR_JS = WEB / "eps_image" / "distributor.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

# ------------------------------------------------------------- pure cases

_MEMORY_OWNER_10 = '{"owner": 10, "links": [{"node": 5, "input": "audio"}]}'
_AUDIO_TARGET = {"node": 5, "input": "audio"}

#: (raw widget string, owner id, expected parseMemory()).
PARSE_MEMORY_CASES = [
    (_MEMORY_OWNER_10, 10, [_AUDIO_TARGET]),
    # An id that got stringified by a hand edit / foreign tool still matches.
    (_MEMORY_OWNER_10.replace(": 10,", ': "10",'), 10, [_AUDIO_TARGET]),
    (_MEMORY_OWNER_10, "10", [_AUDIO_TARGET]),
    # A pasted copy has a NEW id: the memory names the ORIGINAL's consumers.
    (_MEMORY_OWNER_10, 11, []),
    # Memory this file did not write (no owner) is not trusted.
    ('{"links": [{"node": 5, "input": "audio"}]}', 10, []),
    (_MEMORY_OWNER_10.replace(": 10,", ": null,"), 10, []),
    (_MEMORY_OWNER_10, None, []),
    # Junk degrades to "nothing remembered", never throws.
    ("{}", 10, []),
    ("", 10, []),
    (None, 10, []),
    ("not json", 10, []),
    ("[]", 10, []),
    ("[1, 2]", 10, []),
    ("null", 10, []),
    ('"a string"', 10, []),
    ('{"owner": 10}', 10, []),
    ('{"owner": 10, "links": "nope"}', 10, []),
    # One bad item never poisons its neighbours (normalizeRememberedLinks).
    (
        '{"owner": 10, "links": [{"node": 1, "input": "a"}, "junk", {"node": 2, "input": ""},'
        ' {"node": 3, "input": "c"}]}',
        10,
        [{"node": 1, "input": "a"}, {"node": 3, "input": "c"}],
    ),
]

#: (owner id, links, expected serializeMemory()).
SERIALIZE_MEMORY_CASES = [
    (10, [], "{}"),
    (10, None, "{}"),
    (10, "nope", "{}"),
    (10, [{"node": 1, "input": ""}], "{}"),  # wholly invalid -> no residue
    (10, [{"node": 5, "input": "audio"}], '{"owner":10,"links":[{"node":5,"input":"audio"}]}'),
    (
        10,
        [{"node": 5, "input": "audio"}, "junk", {"node": 6, "input": "mask"}],
        '{"owner":10,"links":[{"node":5,"input":"audio"},{"node":6,"input":"mask"}]}',
    ),
]

#: (value, expected isBypassEnabled()).
IS_ENABLED_CASES = [
    (True, True),
    (False, False),
    (None, True),  # no value yet reads ON -- the backend's own rule
    (0, False),
    (1, True),
    ("", False),
]

#: (type, expected labelForType()).
LABEL_FOR_TYPE_CASES = [
    ("AUDIO", "AUDIO"),
    ("IMAGE", "IMAGE"),
    ("*", "any"),
    ("", "any"),
    (None, "any"),
    (0, "any"),
    ("STRING,INT", "STRING,INT"),
]

#: (a, b, expected sameTarget()) -- ids compare as strings.
SAME_TARGET_CASES = [
    ({"node": 5, "input": "audio"}, {"node": 5, "input": "audio"}, True),
    ({"node": 5, "input": "audio"}, {"node": "5", "input": "audio"}, True),
    ({"node": 5, "input": "audio"}, {"node": 6, "input": "audio"}, False),
    ({"node": 5, "input": "audio"}, {"node": 5, "input": "mask"}, False),
]

#: (label, target class nodeData JS, slot JS, expected {safe, why}) for
#: inputVerdict() -- the required-input guard's per-input decision.
VERDICT_CASES = [
    (
        "V1 optional",
        "{ input: { required: { images: ['IMAGE', {}] }, optional: { audio: ['AUDIO', {}] } } }",
        "{ name: 'audio', type: 'AUDIO', link: null, shape: 7 }",
        {"safe": True, "why": "optional"},
    ),
    (
        "V1 required socket",
        "{ input: { required: { audio: ['AUDIO', {}] } } }",
        "{ name: 'audio', type: 'AUDIO', link: null }",
        {"safe": False, "why": "required"},
    ),
    (
        "V1 required but WIDGET-backed (KSampler.cfg): its own value takes over",
        "{ input: { required: { cfg: ['FLOAT', {}] } } }",
        "{ name: 'cfg', type: 'FLOAT', link: null, widget: { name: 'cfg' } }",
        {"safe": True, "why": "widget"},
    ),
    (
        "widget-backed and NOT in the definition at all",
        "{ input: {} }",
        "{ name: 'x', type: 'INT', link: null, widget: { name: 'x' } }",
        {"safe": True, "why": "widget"},
    ),
    (
        "V2 only, optional",
        "{ inputs: { audio: { isOptional: true } } }",
        "{ name: 'audio', type: 'AUDIO', link: null }",
        {"safe": True, "why": "optional"},
    ),
    (
        "V2 only, required",
        "{ inputs: { audio: { isOptional: false } } }",
        "{ name: 'audio', type: 'AUDIO', link: null }",
        {"safe": False, "why": "required"},
    ),
    (
        "V2 only, isOptional absent means required",
        "{ inputs: { audio: { type: 'AUDIO' } } }",
        "{ name: 'audio', type: 'AUDIO', link: null }",
        {"safe": False, "why": "required"},
    ),
    (
        "the definition beats a contradicting shape (declared required)",
        "{ input: { required: { audio: ['AUDIO', {}] } } }",
        "{ name: 'audio', type: 'AUDIO', link: null, shape: 7 }",
        {"safe": False, "why": "required"},
    ),
    (
        "a dynamic socket the definition does not list, hollow circle = optional",
        "{ input: { optional: { image_1: ['IMAGE', {}] } } }",
        "{ name: 'image_7', type: 'IMAGE', link: null, shape: 7 }",
        {"safe": True, "why": "shape"},
    ),
    (
        "unlisted and not hollow: cannot tell, so refuse",
        "{ input: { optional: { image_1: ['IMAGE', {}] } } }",
        "{ name: 'mystery', type: 'IMAGE', link: null }",
        {"safe": False, "why": "unknown"},
    ),
    (
        "no definition at all (a frontend-only node), not hollow",
        "undefined",
        "{ name: 'x', type: '*', link: null }",
        {"safe": False, "why": "unknown"},
    ),
    (
        "a prototype-chain name is not a declared input",
        "{ input: { required: {}, optional: {} } }",
        "{ name: 'constructor', type: 'IMAGE', link: null }",
        {"safe": False, "why": "unknown"},
    ),
    ("no slot", "{ input: {} }", "null", {"safe": False, "why": "unknown"}),
]

PROBE_JS = r"""
import * as bp from './extensions/comfyui-epsnodes/eps_image/bypass.js'
import * as nc from './extensions/comfyui-epsnodes/eps_image/number_controller.js'
import * as dist from './extensions/comfyui-epsnodes/eps_image/distributor.js'
import { app } from './scripts/app.js'

const out = { pure: {}, g: {} }
const tick = () => new Promise((resolve) => setTimeout(resolve, 5))
const toasts = (globalThis.__toasts = [])
const resetToasts = () => { toasts.length = 0 }
globalThis.LGraphCanvas = { link_type_colors: { AUDIO: '#audio', IMAGE: '#image' } }

// ------------------------------------------------------------------ pure

out.pure.exports = {
  init: typeof bp.init, attach: typeof bp.attach,
  classId: bp.CLASS_ID, enabledWidget: bp.ENABLED_WIDGET_NAME, linksWidget: bp.LINKS_WIDGET_NAME,
  inputName: bp.INPUT_NAME, outputIndex: bp.OUTPUT_INDEX, minWidth: bp.MIN_NODE_WIDTH,
  offLabel: bp.OFF_LABEL, anyLabel: bp.ANY_LABEL, hollow: bp.HOLLOW_CIRCLE_SHAPE
}
out.pure.parseMemory = __PARSE_MEMORY__.map(([raw, owner]) => bp.parseMemory(raw, owner))
out.pure.serializeMemory = __SERIALIZE_MEMORY__.map(
  ([owner, links]) => bp.serializeMemory(owner, links))
out.pure.isEnabled = __IS_ENABLED__.map(([v]) => bp.isBypassEnabled(v))
out.pure.labelForType = __LABEL_FOR_TYPE__.map(([t]) => bp.labelForType(t))
out.pure.outputLabel = {
  onAudio: bp.outputLabelFor(true, 'AUDIO'), onAny: bp.outputLabelFor(true, '*'),
  offAudio: bp.outputLabelFor(false, 'AUDIO'), offAny: bp.outputLabelFor(false, '*')
}
out.pure.sameTarget = __SAME_TARGET__.map(([a, b]) => bp.sameTarget(a, b))
out.pure.verdicts = [
__VERDICT_CASES__
].map(([nodeData, slot]) => {
  class T { static nodeData = nodeData }
  return bp.inputVerdict(new T(), slot)
})
out.pure.refusal = {
  required1: bp.refusalMessage([{ label: 'Save Audio #5 (audio)', why: 'required' }]),
  required2: bp.refusalMessage([
    { label: 'A', why: 'required' }, { label: 'B', why: 'required' }]),
  required5: bp.refusalMessage(
    ['A', 'B', 'C', 'D', 'E'].map((label) => ({ label, why: 'required' }))),
  mixedPrefersRequired: bp.refusalMessage([
    { label: 'U', why: 'unknown' }, { label: 'R', why: 'required' }]),
  unrestorable: bp.refusalMessage([{ label: 'Reroute #9', why: 'unrestorable' }]),
  unknown1: bp.refusalMessage([{ label: 'X #1 (y)', why: 'unknown' }]),
  unknown2: bp.refusalMessage([{ label: 'X', why: 'unknown' }, { label: 'Y', why: 'unknown' }]),
  empty: bp.refusalMessage([])
}

// -------------------------------------------------- a small fake litegraph

let nextLinkId = 1
let nextNodeId = 100

const isGeneric = (t) => t == null || t === '' || t === '*' || t === 0
const typesCompatible = (a, b) => isGeneric(a) || isGeneric(b) || String(a) === String(b)

function makeGraph() {
  return {
    nodesById: new Map(),
    links: new Map(),
    dirty: 0,
    getNodeById(id) { return this.nodesById.get(id) ?? null },
    add(node) { this.nodesById.set(node.id, node); node.graph = this; return node },
    setDirtyCanvas() { this.dirty += 1 }
  }
}

/** litegraph's `connect`: type check, the origin's veto hook, replace an
 * occupied input, make the link, fire onConnectionsChange on BOTH ends. */
function wireLinks(origin, slot, target, targetSlot) {
  const graph = origin.graph
  const output = origin.outputs[slot]
  const input = target.inputs[targetSlot]
  if (!output || !input) return null
  if (!typesCompatible(output.type, input.type)) return null
  if (origin.onConnectOutput?.(slot, input.type, input, target, targetSlot) === false) return null
  if (input.link != null) target.disconnectInput(targetSlot)
  const link = {
    id: nextLinkId++, origin_id: origin.id, origin_slot: slot,
    target_id: target.id, target_slot: targetSlot, type: input.type || output.type
  }
  graph.links.set(link.id, link)
  ;(output.links ??= []).push(link.id)
  input.link = link.id
  origin.onConnectionsChange?.(2, slot, true, link, output)
  target.onConnectionsChange?.(1, targetSlot, true, link, input)
  return link
}

class FakeNode {
  constructor(id, title) {
    this.id = id ?? nextNodeId++
    this.title = title
    this.inputs = []
    this.outputs = []
    this.widgets = []
    this.graph = null
    this.size = [140, 80]
  }
  setSize(size) { this.size = [...size]; this.onResize?.(this.size) }
  setDirtyCanvas() {}
  connect(slot, target, targetSlot) { return wireLinks(this, slot, target, targetSlot) }
  disconnectInput(slot) {
    const input = this.inputs[slot]
    if (!input || input.link == null) return true
    const graph = this.graph
    const link = graph.links.get(input.link)
    input.link = null
    if (link) {
      graph.links.delete(link.id)
      const origin = graph.getNodeById(link.origin_id)
      const output = origin?.outputs?.[link.origin_slot]
      if (output?.links) {
        const i = output.links.indexOf(link.id)
        if (i !== -1) output.links.splice(i, 1)
      }
      origin?.onConnectionsChange?.(2, link.origin_slot, false, link, output)
    }
    this.onConnectionsChange?.(1, slot, false, link, input)
    return true
  }
  /** litegraph's configure: widgets_values assigned DIRECTLY (no callback),
   * then onConfigure at the very end. */
  configure(info) {
    if (info?.widgets_values) {
      this.widgets.forEach((w, i) => {
        if (i in info.widgets_values) w.value = info.widgets_values[i]
      })
    }
    this.onConfigure?.(info)
  }
}

class BypassNode extends FakeNode { static comfyClass = 'EPSBypass' }

function makeBypass(graph, id) {
  const node = new BypassNode(id, 'EPS Bypass')
  node.comfyClass = 'EPSBypass'
  node.inputs = [
    { name: 'enabled', type: 'BOOLEAN', link: null, widget: { name: 'enabled' } },
    { name: 'value', type: '*', link: null, shape: 7 },
    { name: 'links', type: 'STRING', link: null, widget: { name: 'links' } }
  ]
  node.outputs = [{ name: 'output', type: '*', links: null }]
  node.widgets = [
    {
      name: 'enabled', type: 'toggle', value: true,
      options: { on: 'on', off: 'off' }, callback: () => {}
    },
    { name: 'links', type: 'text', value: '{}', options: {}, callback: null }
  ]
  graph.add(node)
  return node
}

/** A downstream node whose class carries *nodeData* (the frontend's copy of
 * /object_info) and the given live inputs. */
function makeTarget(graph, title, nodeData, inputs, extra = {}) {
  class T extends FakeNode { static nodeData = nodeData }
  const node = new T(undefined, title)
  node.inputs = inputs.map((i) => ({ link: null, ...i }))
  Object.assign(node, extra)
  graph.add(node)
  return node
}

const VIDEO_DEF = {
  input: { required: { images: ['IMAGE', {}] }, optional: { audio: ['AUDIO', {}] } }
}
const videoInputs = () => [
  { name: 'images', type: 'IMAGE' },
  { name: 'audio', type: 'AUDIO', shape: 7 }
]
const makeVideo = (graph, title = 'Create Video') =>
  makeTarget(graph, title, VIDEO_DEF, videoInputs())
const makeSaveAudio = (graph) => makeTarget(
  graph, 'Save Audio', { input: { required: { audio: ['AUDIO', {}] } } },
  [{ name: 'audio', type: 'AUDIO' }])

function makeOrigin(graph, type = 'AUDIO') {
  const node = new FakeNode(undefined, 'Load Audio')
  node.outputs = [{ name: type, type, links: null }]
  graph.add(node)
  return node
}

const widgetOf = (node, name) => node.widgets.find((w) => w.name === name)

/** litegraph's BaseWidget.setValue: assign, THEN the callback. */
function clickToggle(node) {
  const w = widgetOf(node, 'enabled')
  const v = !w.value
  w.value = v
  w.callback?.(v, {}, node)
}

/** The Universal State Controller's write loop: value, callback, then the
 * announce (api.js flushes it a tick later; here it is delivered directly). */
function applyState(node, value, { callback = true, announce = true } = {}) {
  const w = widgetOf(node, 'enabled')
  w.value = value
  if (callback) w.callback?.(value, {}, node)
  if (announce) {
    globalThis.__externalHandler([
      { node, pathId: String(node.id), class: 'EPSBypass', widgets: ['enabled'] }
    ])
  }
}

const memoryOf = (node) => JSON.parse(widgetOf(node, 'links').value)
const linkOfInput = (node, name) => node.inputs.find((i) => i.name === name)?.link
const isWired = (target, name) => linkOfInput(target, name) != null
const linkObj = (target, name) => target.graph.links.get(linkOfInput(target, name))
const inputOfBp = (node) => node.inputs.find((i) => i.name === 'value')

/** origin -> bypass -> [targets], settled, the ordinary "audio into a
 * video node" build. */
async function build(makeTargets = (g) => [makeVideo(g)], { wireInput = true } = {}) {
  const graph = makeGraph()
  const origin = makeOrigin(graph)
  const node = makeBypass(graph, 10)
  bp.attach(node)
  const targets = makeTargets(graph)
  if (wireInput) origin.connect(0, node, 1)
  for (const t of targets) {
    const wireable = ['audio', 'cfg', 'image_7', 'mystery', '']
    const slot = t.inputs.findIndex((i) => wireable.includes(i.name))
    node.connect(0, t, slot)
  }
  await tick()
  resetToasts()
  return { graph, origin, node, targets }
}

// ------------------------------------------- A: a fresh node, and attach()
{
  const graph = makeGraph()
  const node = makeBypass(graph, 10)
  bp.attach(node)
  bp.attach(node) // a double nodeCreated must be a no-op
  const links = widgetOf(node, 'links')
  out.g.fresh = {
    linksHidden: links.hidden, linksOptionsHidden: links.options.hidden,
    enabledHidden: widgetOf(node, 'enabled').hidden ?? false,
    callbackWrapped: typeof widgetOf(node, 'enabled').callback === 'function',
    reloadSeam: typeof node.__epsBypassReload,
    resyncHook: typeof node[dist.LINK_COLOR_RESYNC_HOOK],
    inputLabel: inputOfBp(node).label, outputLabel: node.outputs[0].label,
    width: node.size[0], linksValue: links.value, enabledValue: widgetOf(node, 'enabled').value
  }
  const other = new FakeNode(11, 'Something else'); other.comfyClass = 'KSampler'
  other.widgets = [{ name: 'enabled', value: true }, { name: 'links', value: 'x' }]
  bp.attach(other)
  out.g.otherClassUntouched = {
    hidden: other.widgets[1].hidden ?? false, onConfigure: typeof other.onConfigure
  }
  const partial = new BypassNode(12, 'no widgets'); partial.comfyClass = 'EPSBypass'
  partial.widgets = [{ name: 'enabled', value: true }] // no `links`: fail soft, not attached
  bp.attach(partial)
  out.g.missingLinksWidget = {
    callback: typeof partial.widgets[0].callback, seam: typeof partial.__epsBypassReload
  }
}

// ------------------------------------ B: the core round trip (audio -> video)
{
  const { graph, origin, node, targets: [video] } = await build()
  const before = {
    inputType: inputOfBp(node).type, outputType: node.outputs[0].type,
    inputLabel: inputOfBp(node).label, outputLabel: node.outputs[0].label,
    inLinkColor: graph.links.get(inputOfBp(node).link).color,
    outLinkColor: linkObj(video, 'audio').color,
    wired: isWired(video, 'audio')
  }
  clickToggle(node)
  await tick()
  const off = {
    enabled: widgetOf(node, 'enabled').value,
    videoAudioWired: isWired(video, 'audio'),
    outputLinks: (node.outputs[0].links || []).length,
    graphLinkCount: graph.links.size,
    upstreamStillWired: inputOfBp(node).link != null && graph.links.has(inputOfBp(node).link),
    originLinks: origin.outputs[0].links.length,
    memory: memoryOf(node), memoryRaw: widgetOf(node, 'links').value,
    inputType: inputOfBp(node).type, outputType: node.outputs[0].type,
    outputLabel: node.outputs[0].label, inputLabel: inputOfBp(node).label,
    toasts: toasts.map((t) => t.severity)
  }
  clickToggle(node)
  await tick()
  const on = {
    enabled: widgetOf(node, 'enabled').value,
    wired: isWired(video, 'audio'),
    originId: linkObj(video, 'audio')?.origin_id, targetId: linkObj(video, 'audio')?.target_id,
    memoryRaw: widgetOf(node, 'links').value,
    outputLabel: node.outputs[0].label,
    linkColor: linkObj(video, 'audio')?.color,
    imagesUntouched: !isWired(video, 'images'),
    toasts: toasts.map((t) => t.severity)
  }
  out.g.roundTrip = { videoId: video.id, before, off, on }
}

// ------------------------------------------------ C: several output targets
{
  const { node, targets } = await build(
    (g) => ['Video A', 'Video B', 'Video C'].map((title) => makeVideo(g, title)))
  const ids = targets.map((t) => t.id)
  clickToggle(node)
  await tick()
  const off = { wired: targets.map((t) => isWired(t, 'audio')), memory: memoryOf(node).links }
  clickToggle(node)
  await tick()
  out.g.fanOut = {
    ids, off,
    on: {
      wired: targets.map((t) => isWired(t, 'audio')),
      origins: targets.map((t) => linkObj(t, 'audio')?.origin_id),
      memoryRaw: widgetOf(node, 'links').value, toasts: toasts.length
    }
  }
}

// ---------------------------------------- D: the required-input guard refuses
{
  const { node, targets: [save] } = await build((g) => [makeSaveAudio(g)])
  const wiredBefore = isWired(save, 'audio')
  const linkBefore = linkOfInput(save, 'audio')
  clickToggle(node)
  const immediate = {
    enabled: widgetOf(node, 'enabled').value, wired: isWired(save, 'audio'),
    sameLink: linkOfInput(save, 'audio') === linkBefore
  }
  await tick()
  out.g.refuseRequired = {
    wiredBefore, immediate,
    afterTick: { enabled: widgetOf(node, 'enabled').value, wired: isWired(save, 'audio') },
    memoryRaw: widgetOf(node, 'links').value,
    outputLabel: node.outputs[0].label,
    toasts: toasts.map((t) => (
      { severity: t.severity, summary: t.summary, detail: t.detail, life: t.life })),
    saveId: save.id
  }
  // A later, legitimate toggle must not be overridden by the refusal's
  // second-chance assertion (the token).
  resetToasts()
  clickToggle(node) // false -> refused again (still required)
  await tick()
  out.g.refuseRequired.secondAttempt = {
    enabled: widgetOf(node, 'enabled').value, toasts: toasts.length
  }
}

// The Vue renderer writes its own model AROUND the callback: the refusal must
// still win when the model lands after the callback returned.
{
  const { node, targets: [save] } = await build((g) => [makeSaveAudio(g)])
  const w = widgetOf(node, 'enabled')
  w.value = false
  w.callback(false, {}, node)
  w.value = false // Vue's model write, after the callback
  await tick()
  out.g.refuseVueOrder = { enabled: w.value, wired: isWired(save, 'audio') }
}

// ------------------------------- E: a mixed fan-out refuses WHOLESALE
{
  const { node, targets } = await build((g) => [makeVideo(g, 'Create Video'), makeSaveAudio(g)])
  const [video, save] = targets
  clickToggle(node)
  await tick()
  out.g.refuseMixed = {
    enabled: widgetOf(node, 'enabled').value,
    videoWired: isWired(video, 'audio'), saveWired: isWired(save, 'audio'),
    memoryRaw: widgetOf(node, 'links').value,
    detail: toasts[0]?.detail ?? '',
    toastCount: toasts.length
  }
}

// ------------------- F: widget-backed inputs, dynamic sockets, unknowns
{
  // KSampler.cfg: declared REQUIRED but widget-backed -> safe, like the
  // Number Controller's checkbox.
  const ks = await build(
    (g) => [makeTarget(g, 'KSampler', { input: { required: { cfg: ['FLOAT', {}] } } },
      [{ name: 'cfg', type: 'FLOAT', widget: { name: 'cfg' } }])],
    { wireInput: false })
  clickToggle(ks.node)
  await tick()
  const off = { wired: isWired(ks.targets[0], 'cfg'), memory: memoryOf(ks.node).links }
  clickToggle(ks.node)
  await tick()
  out.g.widgetBacked = { off, onWired: isWired(ks.targets[0], 'cfg'), toasts: toasts.length }

  // A Switcher-style dynamic socket (not in nodeData, hollow circle): safe.
  const sw = await build(
    (g) => [makeTarget(g, 'EPS Image Switcher', { input: { optional: { image_1: ['IMAGE', {}] } } },
      [{ name: 'image_7', type: 'IMAGE', shape: 7 }])])
  clickToggle(sw.node)
  await tick()
  out.g.dynamicSocket = {
    enabled: widgetOf(sw.node, 'enabled').value, wired: isWired(sw.targets[0], 'image_7')
  }

  // An unlisted, non-hollow socket: cannot verify -> refuse.
  const mystery = await build(
    (g) => [makeTarget(
      g, 'Mystery', { input: { optional: {} } }, [{ name: 'mystery', type: 'AUDIO' }])])
  clickToggle(mystery.node)
  await tick()
  out.g.unknownSocket = {
    enabled: widgetOf(mystery.node, 'enabled').value, wired: isWired(mystery.targets[0], 'mystery'),
    detail: toasts[toasts.length - 1]?.detail ?? ''
  }
}

// --------------------------- G: wires that cannot be remembered refuse
{
  // The legacy Reroute NODE: its input is nameless, so collectOutputTargets
  // cannot record it and disconnectAllTargets would sever it for good.
  const rr = await build(
    (g) => [makeTarget(g, 'Reroute', undefined, [{ name: '', type: '*' }])])
  clickToggle(rr.node)
  await tick()
  out.g.rerouteRefuses = {
    enabled: widgetOf(rr.node, 'enabled').value, wired: isWired(rr.targets[0], ''),
    detail: toasts[toasts.length - 1]?.detail ?? ''
  }
  // A link whose target node has vanished from the graph.
  const dangling = await build((g) => [makeVideo(g)])
  dangling.graph.nodesById.delete(dangling.targets[0].id)
  clickToggle(dangling.node)
  await tick()
  out.g.danglingRefuses = {
    enabled: widgetOf(dangling.node, 'enabled').value,
    detail: toasts[toasts.length - 1]?.detail ?? ''
  }
  // A link ending at a subgraph's own output (target_id -20): no node.
  const sub = await build((g) => [makeVideo(g)])
  sub.graph.links.get(linkOfInput(sub.targets[0], 'audio')).target_id = -20
  sub.graph.nodesById.delete(sub.targets[0].id)
  clickToggle(sub.node)
  await tick()
  out.g.subgraphOutputRefuses = {
    enabled: widgetOf(sub.node, 'enabled').value,
    detail: toasts[toasts.length - 1]?.detail ?? ''
  }
}

// ----------- H: a target that will not let go -> all-or-nothing rollback
{
  const { node, targets: [stubborn, video] } = await build((g) => {
    const s = makeVideo(g, 'Stubborn Video')
    s.disconnectInput = () => false // the frontend could not unplug it
    return [s, makeVideo(g, 'Fine Video')]
  })
  clickToggle(node)
  await tick()
  out.g.rollback = {
    enabled: widgetOf(node, 'enabled').value,
    stubbornWired: isWired(stubborn, 'audio'), fineWired: isWired(video, 'audio'),
    memoryRaw: widgetOf(node, 'links').value,
    detail: toasts[toasts.length - 1]?.detail ?? ''
  }
}

// ---------------------- I: stale targets on switch-on are skipped quietly
{
  const { graph, node, targets: [a, b, c] } = await build(
    (g) => [makeVideo(g, 'A'), makeVideo(g, 'B'), makeVideo(g, 'C')])
  clickToggle(node)
  await tick()
  resetToasts()
  graph.nodesById.delete(b.id) // B was deleted while the bypass was off
  c.inputs[1].name = 'audio_renamed' // C's input was renamed
  const other = makeOrigin(graph)
  other.connect(0, a, 1) // the user wired A's socket by hand meanwhile
  const handLink = linkOfInput(a, 'audio')
  clickToggle(node)
  await tick()
  out.g.staleTargets = {
    aStillTheHandWire: linkOfInput(a, 'audio') === handLink,
    aOrigin: linkObj(a, 'audio')?.origin_id, otherId: other.id, bypassId: node.id,
    cWired: c.inputs[1].link != null,
    bypassOutputLinks: (node.outputs[0].links || []).length,
    memoryRaw: widgetOf(node, 'links').value,
    enabled: widgetOf(node, 'enabled').value,
    detail: toasts[toasts.length - 1]?.detail ?? '', toastCount: toasts.length
  }
  // Partial: only the still-valid target comes back.
  const p = await build((g) => [makeVideo(g, 'P1'), makeVideo(g, 'P2')])
  clickToggle(p.node)
  await tick()
  resetToasts()
  p.graph.nodesById.delete(p.targets[1].id)
  clickToggle(p.node)
  await tick()
  out.g.partialRestore = {
    p1Wired: isWired(p.targets[0], 'audio'),
    detail: toasts[toasts.length - 1]?.detail ?? '', memoryRaw: widgetOf(p.node, 'links').value
  }
}

// --------------------- J: the rebuild law (tab switch / undo / reload)
{
  const { graph, origin, node, targets: [video] } = await build()
  clickToggle(node)
  await tick()
  const saved = [widgetOf(node, 'enabled').value, widgetOf(node, 'links').value]

  // A brand-new node OBJECT with the same id: attach() BEFORE configure(),
  // then configure() restores widgets_values and fires onConfigure.
  graph.nodesById.delete(10)
  const node2 = makeBypass(graph, 10)
  bp.attach(node2)
  app.configuringGraph = true // the whole (re)load runs inside LGraph.configure
  origin.connect(0, node2, 1) // litegraph restores the input link
  node2.configure({ widgets_values: saved })
  app.configuringGraph = false
  const syncLabel = node2.outputs[0].label // right after onConfigure, no tick
  await tick()
  const restored = {
    enabled: widgetOf(node2, 'enabled').value, memoryRaw: widgetOf(node2, 'links').value,
    outputLabel: node2.outputs[0].label, syncLabel,
    inputType: inputOfBp(node2).type, inputLabel: inputOfBp(node2).label,
    stayedOff: !isWired(video, 'audio'), toasts: toasts.length
  }
  clickToggle(node2)
  await tick()
  out.g.rebuild = {
    restored,
    replugged: isWired(video, 'audio'), originId: linkObj(video, 'audio')?.origin_id,
    bypassId: node2.id
  }
}
{
  // LOADING NEVER REWIRES. (1) saved OFF + a wired output (only reachable by
  // hand-editing/an API caller): left exactly as it is.
  const { graph, origin, node, targets: [video] } = await build()
  const link = linkOfInput(video, 'audio')
  graph.nodesById.delete(10)
  const node2 = makeBypass(graph, 10)
  bp.attach(node2)
  app.configuringGraph = true
  origin.connect(0, node2, 1)
  // litegraph restores the output link as part of configure: emulate it.
  node2.outputs[0].links = [link]
  graph.links.get(link).origin_id = 10
  app.configuringGraph = true
  node2.configure({ widgets_values: [false, '{}'] })
  app.configuringGraph = false
  await tick()
  await tick()
  out.g.loadNeverRewiresOffWired = {
    enabled: widgetOf(node2, 'enabled').value, stillWired: isWired(video, 'audio'),
    sameLink: linkOfInput(video, 'audio') === link, toasts: toasts.length
  }
}
{
  // (2) saved ON + an unwired output + a (foreign-looking or stale) memory:
  // no reconnect on load.
  const { graph, origin, node, targets: [video] } = await build()
  clickToggle(node)
  await tick()
  const memory = widgetOf(node, 'links').value
  graph.nodesById.delete(10)
  const node2 = makeBypass(graph, 10)
  bp.attach(node2)
  app.configuringGraph = true
  origin.connect(0, node2, 1)
  node2.configure({ widgets_values: [true, memory] })
  app.configuringGraph = false
  await tick()
  await tick()
  out.g.loadNeverRewiresOnUnwired = {
    enabled: widgetOf(node2, 'enabled').value, wired: isWired(video, 'audio'),
    memoryRaw: widgetOf(node2, 'links').value
  }
}

// -------------------------- K: a pasted copy's memory is stale (owner id)
{
  const { graph, origin, node, targets: [video] } = await build()
  clickToggle(node)
  await tick()
  const copyValues = [false, widgetOf(node, 'links').value] // what a paste carries
  const copy = makeBypass(graph, 11) // a NEW id
  bp.attach(copy)
  origin.connect(0, copy, 1)
  copy.configure({ widgets_values: copyValues })
  await tick()
  resetToasts()
  clickToggle(copy) // switch the COPY on
  await tick()
  const afterCopyOn = {
    videoWiredToAnything: isWired(video, 'audio'),
    copyMemoryRaw: widgetOf(copy, 'links').value, toasts: toasts.length
  }
  clickToggle(node) // the ORIGINAL still reconnects its own consumer
  await tick()
  out.g.pastedCopy = {
    afterCopyOn, originalReplugged: isWired(video, 'audio'),
    originalOrigin: linkObj(video, 'audio')?.origin_id, originalId: node.id
  }
}

// -------------------- L: the Universal State Controller apply route
{
  const { node, targets: [video] } = await build()
  // (a) OFF applied the way the controller applies it: value, callback, announce.
  applyState(node, false)
  await tick()
  const off = {
    wired: isWired(video, 'audio'), memory: memoryOf(node).links,
    enabled: widgetOf(node, 'enabled').value, outputLabel: node.outputs[0].label
  }
  // A second announce is an idempotent no-op.
  const rawAfterOff = widgetOf(node, 'links').value
  globalThis.__externalHandler([{ node }])
  await tick()
  const idempotent = {
    rawSame: widgetOf(node, 'links').value === rawAfterOff, wired: isWired(video, 'audio')
  }
  // (b) ON applied.
  applyState(node, true)
  await tick()
  const on = { wired: isWired(video, 'audio'), memoryRaw: widgetOf(node, 'links').value }
  out.g.applyRoute = { off, idempotent, on, toasts: toasts.length }
}
{
  // (c) A writer that sets the value WITHOUT firing the callback: the
  // announce subscription alone must unplug, then replug.
  const { node, targets: [video] } = await build()
  applyState(node, false, { callback: false })
  await tick()
  const off = { wired: isWired(video, 'audio'), memory: memoryOf(node).links }
  applyState(node, true, { callback: false })
  await tick()
  out.g.applyRouteAnnounceOnly = {
    off, onWired: isWired(video, 'audio'), memoryRaw: widgetOf(node, 'links').value
  }
}
{
  // (d) Applying OFF onto a REQUIRED target is refused and reverted.
  const { node, targets: [save] } = await build((g) => [makeSaveAudio(g)])
  applyState(node, false)
  await tick()
  out.g.applyRouteRefused = {
    enabled: widgetOf(node, 'enabled').value, wired: isWired(save, 'audio'),
    detail: toasts[toasts.length - 1]?.detail ?? ''
  }
}
{
  // (e) Applying OFF to a node that is ALREADY off (unwired) keeps its memory.
  const { node, targets: [video] } = await build()
  clickToggle(node)
  await tick()
  const memory = widgetOf(node, 'links').value
  applyState(node, false)
  await tick()
  out.g.applyAlreadyOff = {
    memorySame: widgetOf(node, 'links').value === memory, wired: isWired(video, 'audio')
  }
}
out.g.subscriptionInstalled = typeof globalThis.__externalHandler

// ------------ M: a wire dragged onto an OFF node switches it back on
{
  const { graph, node, targets: [video] } = await build()
  clickToggle(node)
  await tick()
  // Our OWN disconnect events must not read as the user wiring something on.
  const stillOffAfterOwnUnplug = widgetOf(node, 'enabled').value === false
  resetToasts()
  const fresh = makeVideo(graph, 'New Video')
  node.connect(0, fresh, 1) // the user drags a NEW wire out of the off node
  await tick()
  out.g.autoReenable = {
    stillOffAfterOwnUnplug,
    enabled: widgetOf(node, 'enabled').value, memoryRaw: widgetOf(node, 'links').value,
    freshWired: isWired(fresh, 'audio'),
    oldRestored: isWired(video, 'audio'), // the OLD memory is superseded, NOT replayed
    toasts: toasts.map((t) => t.severity)
  }
}
{
  // ...but never while the graph is being (re)configured.
  const { graph, node } = await build()
  clickToggle(node)
  await tick()
  app.configuringGraph = true
  const fresh = makeVideo(graph, 'New Video')
  node.connect(0, fresh, 1)
  await tick()
  app.configuringGraph = false
  out.g.noReenableWhileConfiguring = { enabled: widgetOf(node, 'enabled').value }
}

// ---------------------------------------------- N: type adoption + labels
{
  const graph = makeGraph()
  const node = makeBypass(graph, 10)
  bp.attach(node)
  const video = makeVideo(graph)
  node.connect(0, video, 1) // OUTPUT side only
  await tick()
  const fromOutput = {
    inputType: inputOfBp(node).type, outputType: node.outputs[0].type,
    inputLabel: inputOfBp(node).label, outputLabel: node.outputs[0].label,
    linkColor: linkObj(video, 'audio').color
  }
  video.disconnectInput(1)
  await tick()
  out.g.adoption = {
    fromOutput,
    afterUnwire: {
      inputType: inputOfBp(node).type, outputType: node.outputs[0].type,
      inputLabel: inputOfBp(node).label, outputLabel: node.outputs[0].label
    }
  }
}
{
  // The FIRST thing wired sets the type; from then on the sockets carry it,
  // so litegraph's OWN isValidConnection refuses a mismatch (no veto hook of
  // ours -- see the source pin). The fake mirrors that check.
  const graph = makeGraph()
  const node = makeBypass(graph, 10)
  bp.attach(node)
  const image = makeOrigin(graph, 'IMAGE')
  const audio = makeOrigin(graph, 'AUDIO')
  const first = image.connect(0, node, 1)
  await tick()
  const second = audio.connect(0, node, 1) // AUDIO into the now-IMAGE input
  const toOutput = node.connect(0, makeVideo(graph), 1) // IMAGE output into an AUDIO socket
  out.g.mismatchRefused = {
    firstAccepted: first != null, adopted: inputOfBp(node).type,
    secondAccepted: second != null, outputMismatchAccepted: toOutput != null,
    upstreamLinkKept: inputOfBp(node).link === first?.id
  }
}
{
  // The link-colour ownership convention shared with distributor.js/image_grid.js.
  const graph = makeGraph()
  const node = makeBypass(graph, 10)
  bp.attach(node)
  const v1 = makeVideo(graph, 'V1')
  const v2 = makeVideo(graph, 'V2')
  node.connect(0, v1, 1)
  node.connect(0, v2, 1)
  await tick()
  const l1 = linkObj(v1, 'audio')
  const l2 = linkObj(v2, 'audio')
  l1[dist.LINK_COLOR_OWNER_KEY] = 'grid'
  l1.color = '#dim'
  l2.color = '#stale'
  node[dist.LINK_COLOR_RESYNC_HOOK]()
  const whileTagged = { tagged: l1.color, untagged: l2.color }
  delete l1[dist.LINK_COLOR_OWNER_KEY]
  node[dist.LINK_COLOR_RESYNC_HOOK]()
  out.g.linkColorOwnership = { whileTagged, afterRelease: l1.color }
}

// ------------------------------------- O: switching off with nothing wired
{
  const graph = makeGraph()
  const node = makeBypass(graph, 10)
  bp.attach(node)
  resetToasts()
  clickToggle(node)
  await tick()
  out.g.offUnwired = {
    enabled: widgetOf(node, 'enabled').value, memoryRaw: widgetOf(node, 'links').value,
    outputLabel: node.outputs[0].label, toasts: toasts.length
  }
  clickToggle(node)
  await tick()
  out.g.offUnwired.backOn = {
    enabled: widgetOf(node, 'enabled').value, outputLabel: node.outputs[0].label
  }
}

// ---------------- P: the toggle callback chains, never replaces
{
  const graph = makeGraph()
  const node = makeBypass(graph, 10)
  const calls = []
  widgetOf(node, 'enabled').callback = (v) => { calls.push(v); return 'orig' }
  bp.attach(node)
  const ret = widgetOf(node, 'enabled').callback(false, {}, node)
  out.g.callbackChain = { calls, ret }
}

process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def probe(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Runs the probe against the REAL bypass.js (and the real
    number_controller.js / distributor.js it imports) in a served-layout tmp
    dir -- ``tests/test_number_controller_js.py``'s fixture, with the extra
    modules copied alongside and ``app.js``/``api.js`` stubbed so the probe can
    read every toast and deliver a Universal State announce."""
    layout = tmp_path_factory.mktemp("web_root")
    module_dir = layout / "extensions" / "comfyui-epsnodes" / "eps_image"
    module_dir.mkdir(parents=True)
    for source in (BYPASS_JS, NUMBER_CONTROLLER_JS, DISTRIBUTOR_JS):
        shutil.copyfile(source, module_dir / source.name)

    scripts = layout / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "app.js").write_text(
        "export const app = {\n"
        "  configuringGraph: false,\n"
        "  extensionManager: { toast: { add(t) { globalThis.__toasts.push(t) } } }\n"
        "}\n",
        encoding="utf-8",
    )
    lora_library = layout / "extensions" / "comfyui-epsnodes" / "lora_library"
    lora_library.mkdir(parents=True)
    (lora_library / "api.js").write_text(
        # The real api.js hands every subscriber the flushed `entries`; the
        # probe delivers them straight to the (single, module-guarded) handler.
        "export function subscribeWidgetsChangedExternally(handler) {\n"
        "  globalThis.__externalHandler = handler\n"
        "}\n",
        encoding="utf-8",
    )

    source = (
        PROBE_JS.replace("__PARSE_MEMORY__", json.dumps([[r, o] for r, o, _ in PARSE_MEMORY_CASES]))
        .replace(
            "__SERIALIZE_MEMORY__",
            json.dumps([[o, links] for o, links, _ in SERIALIZE_MEMORY_CASES]),
        )
        .replace("__IS_ENABLED__", json.dumps([[v] for v, _ in IS_ENABLED_CASES]))
        .replace("__LABEL_FOR_TYPE__", json.dumps([[t] for t, _ in LABEL_FOR_TYPE_CASES]))
        .replace("__SAME_TARGET__", json.dumps([[a, b] for a, b, _ in SAME_TARGET_CASES]))
        .replace(
            "__VERDICT_CASES__",
            ",\n".join(f"  [{node_data}, {slot}]" for _label, node_data, slot, _ in VERDICT_CASES),
        )
    )
    probe_file = layout / "probe.mjs"
    probe_file.write_text(source, encoding="utf-8")

    result = subprocess.run(
        [NODE, str(probe_file)], capture_output=True, text=True, timeout=60, cwd=layout
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def source() -> str:
    return BYPASS_JS.read_text(encoding="utf-8")


def _function_body(text: str, signature: str) -> str:
    """The body of a top-level ``function <signature> {`` (``export`` prefix
    allowed), up to its closing brace at column 0 -- the pack's convention
    (tests/test_number_controller_js.py)."""
    start_match = re.search(re.escape(f"function {signature} {{") + r"\n", text)
    assert start_match, f"function {signature} {{ not found"
    start = start_match.end()
    end_match = re.search(r"\n\}\n", text[start:])
    assert end_match, f"function {signature}'s closing brace not found"
    return text[start : start + end_match.start()]


# -------------------------------------------------------------- parses/API


def test_bypass_js_parses() -> None:
    result = subprocess.run(
        [NODE, "--check", str(BYPASS_JS)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


def test_exports_and_constants_match_the_backend_contract(probe: dict) -> None:
    from eps_image.nodes_bypass import DEFAULT_LINKS, EPSBypass

    exported = probe["pure"]["exports"]
    assert exported["init"] == "function"
    assert exported["attach"] == "function"
    assert exported["classId"] == "EPSBypass"
    assert exported["enabledWidget"] == "enabled"
    assert exported["linksWidget"] == "links"
    assert exported["inputName"] == "value"
    assert exported["outputIndex"] == 0
    assert exported["hollow"] == 7  # litegraph RenderShape.HollowCircle
    assert exported["minWidth"] >= 200
    # The frontend's names must be the backend's names (inputs restore BY
    # NAME, sockets by position -- FORMAT.md section 8).
    spec = EPSBypass.INPUT_TYPES()
    assert exported["enabledWidget"] in spec["required"]
    assert exported["linksWidget"] in spec["optional"]
    assert exported["inputName"] in spec["optional"]
    assert DEFAULT_LINKS == "{}"
    # The output's OFF label is the toggle's own off text (one wording).
    assert spec["required"]["enabled"][1]["label_off"] == exported["offLabel"]


# ------------------------------------------------------------- pure helpers


def test_parse_memory(probe: dict) -> None:
    got = probe["pure"]["parseMemory"]
    for (raw, owner, expected), actual in zip(PARSE_MEMORY_CASES, got, strict=True):
        assert actual == expected, f"parseMemory({raw!r}, owner={owner!r}) -> {actual!r}"


def test_serialize_memory(probe: dict) -> None:
    got = probe["pure"]["serializeMemory"]
    for (owner, links, expected), actual in zip(SERIALIZE_MEMORY_CASES, got, strict=True):
        assert actual == expected, f"serializeMemory({owner!r}, {links!r}) -> {actual!r}"


def test_memory_round_trips(probe: dict) -> None:
    """serializeMemory -> parseMemory is the identity for the owner."""
    assert json.loads(probe["pure"]["serializeMemory"][4]) == {
        "owner": 10,
        "links": [{"node": 5, "input": "audio"}],
    }


def test_is_bypass_enabled(probe: dict) -> None:
    for (value, expected), actual in zip(IS_ENABLED_CASES, probe["pure"]["isEnabled"], strict=True):
        assert actual is expected, f"isBypassEnabled({value!r}) -> {actual!r}"


def test_label_for_type(probe: dict) -> None:
    for (value, expected), actual in zip(
        LABEL_FOR_TYPE_CASES, probe["pure"]["labelForType"], strict=True
    ):
        assert actual == expected, f"labelForType({value!r}) -> {actual!r}"


def test_output_label_is_the_type_on_and_the_off_text_off(probe: dict) -> None:
    labels = probe["pure"]["outputLabel"]
    assert labels["onAudio"] == "AUDIO"
    assert labels["onAny"] == "any"
    assert labels["offAudio"] == labels["offAny"] == probe["pure"]["exports"]["offLabel"]


def test_same_target(probe: dict) -> None:
    got = probe["pure"]["sameTarget"]
    for (a, b, expected), actual in zip(SAME_TARGET_CASES, got, strict=True):
        assert actual is expected, f"sameTarget({a!r}, {b!r}) -> {actual!r}"


def test_input_verdict(probe: dict) -> None:
    """The required-input guard's per-input decision, case by case."""
    for (label, _data, _slot, expected), actual in zip(
        VERDICT_CASES, probe["pure"]["verdicts"], strict=True
    ):
        assert actual == expected, f"{label}: {actual!r}, wanted {expected!r}"


def test_refusal_message_wording(probe: dict) -> None:
    r = probe["pure"]["refusal"]
    assert r["required1"] == (
        "Can't switch off: Save Audio #5 (audio) is a required input, so unplugging it would "
        'fail the run with "Required input is missing". It stays on. EPS Bypass only works '
        "into optional inputs."
    )
    assert "A, B are required inputs, so unplugging them" in r["required2"]
    # Three named, the rest counted -- grammar agrees with the list.
    assert "A, B, C (+2 more) are required inputs" in r["required5"]
    # The sentence is about ONE reason: the most decisive one present.
    assert "R is a required input" in r["mixedPrefersRequired"]
    assert "U" not in r["mixedPrefersRequired"].replace("Required", "").replace("unplugging", "")
    assert "legacy Reroute node" in r["unrestorable"]
    assert "a subgraph output" in r["unrestorable"]
    assert "lose the wire" in r["unrestorable"]
    assert "couldn't confirm that X #1 (y) is optional" in r["unknown1"]
    assert "couldn't confirm that X, Y are optional" in r["unknown2"]
    assert r["empty"].startswith("Can't switch off:")
    for text in r.values():
        assert text.endswith("It stays on. EPS Bypass only works into optional inputs.")


# ----------------------------------------------------------------- attach


def test_attach_hides_links_wires_the_seams_and_seeds_labels(probe: dict) -> None:
    fresh = probe["g"]["fresh"]
    # BOTH hide flags (FORMAT.md section 7.5): canvas reads widget.hidden,
    # Vue reads options.hidden.
    assert fresh["linksHidden"] is True
    assert fresh["linksOptionsHidden"] is True
    assert fresh["enabledHidden"] is False  # the toggle stays visible
    assert fresh["callbackWrapped"] is True
    assert fresh["reloadSeam"] == "function"
    assert fresh["resyncHook"] == "function"
    assert fresh["inputLabel"] == "any"
    assert fresh["outputLabel"] == "any"
    assert fresh["width"] >= probe["pure"]["exports"]["minWidth"]
    assert fresh["linksValue"] == "{}"
    assert fresh["enabledValue"] is True


def test_attach_is_a_noop_for_other_classes_and_fails_soft_without_widgets(probe: dict) -> None:
    assert probe["g"]["otherClassUntouched"] == {"hidden": False, "onConfigure": "undefined"}
    # No `links` widget: not attached, and the (unwrapped) toggle still works.
    assert probe["g"]["missingLinksWidget"] == {"callback": "undefined", "seam": "undefined"}


# ----------------------------------------------------- the core round trip


def test_toggle_off_remembers_the_target_and_unplugs_it(probe: dict) -> None:
    rt = probe["g"]["roundTrip"]
    assert rt["before"]["wired"] is True
    off = rt["off"]
    assert off["enabled"] is False
    # The consumer's optional input is now UNCONNECTED -- the link is gone
    # from the graph, not merely dimmed.
    assert off["videoAudioWired"] is False
    assert off["outputLinks"] == 0
    assert off["memory"] == {"owner": 10, "links": [{"node": rt["videoId"], "input": "audio"}]}
    # Only the ONE wire to the consumer went; the upstream wire stays.
    assert off["upstreamStillWired"] is True
    assert off["originLinks"] == 1
    assert off["graphLinkCount"] == 1
    assert off["toasts"] == []


def test_toggle_on_reconnects_to_exactly_the_remembered_target(probe: dict) -> None:
    rt = probe["g"]["roundTrip"]
    on = rt["on"]
    assert on["enabled"] is True
    assert on["wired"] is True
    assert on["targetId"] == rt["videoId"]
    assert on["originId"] == 10  # the bypass node, not the upstream origin
    assert on["imagesUntouched"] is True  # nothing else on the consumer moved
    # The memory is forgotten after ONE attempt: no residue in the workflow.
    assert on["memoryRaw"] == "{}"
    assert on["toasts"] == []


def test_the_node_adopts_the_wired_type_and_says_off_on_itself(probe: dict) -> None:
    rt = probe["g"]["roundTrip"]
    before = rt["before"]
    assert before["inputType"] == before["outputType"] == "AUDIO"
    assert before["inputLabel"] == before["outputLabel"] == "AUDIO"
    # Both links are recoloured to the adopted type (Reroute's precedent).
    assert before["inLinkColor"] == before["outLinkColor"] == "#audio"
    off = rt["off"]
    # Off: the OUTPUT socket says so; the input keeps its type (the wire is
    # still there), and the type is not lost by unplugging the output.
    assert off["outputLabel"] == probe["pure"]["exports"]["offLabel"]
    assert off["inputLabel"] == "AUDIO"
    assert off["inputType"] == off["outputType"] == "AUDIO"
    # On again: label and colour come back.
    assert rt["on"]["outputLabel"] == "AUDIO"
    assert rt["on"]["linkColor"] == "#audio"


def test_our_own_unplug_does_not_read_as_a_new_wire(probe: dict) -> None:
    """switchOff's own disconnects fire onConnectionsChange; the deferred
    pass they schedule must not flip the toggle back on."""
    assert probe["g"]["autoReenable"]["stillOffAfterOwnUnplug"] is True


# ------------------------------------------------------------- fan-out


def test_every_output_link_is_remembered_and_restored(probe: dict) -> None:
    fan = probe["g"]["fanOut"]
    assert fan["off"]["wired"] == [False, False, False]
    assert fan["off"]["memory"] == [{"node": i, "input": "audio"} for i in fan["ids"]]
    assert fan["on"]["wired"] == [True, True, True]
    assert fan["on"]["origins"] == [10, 10, 10]
    assert fan["on"]["memoryRaw"] == "{}"
    assert fan["on"]["toasts"] == 0


# ---------------------------------------------------------- the guard


def test_required_input_refuses_and_reverts_with_a_toast(probe: dict) -> None:
    r = probe["g"]["refuseRequired"]
    assert r["wiredBefore"] is True
    # Synchronously: back ON, the very same wire still there.
    assert r["immediate"] == {"enabled": True, "wired": True, "sameLink": True}
    assert r["afterTick"] == {"enabled": True, "wired": True}
    assert r["memoryRaw"] == "{}"  # nothing was remembered: nothing was touched
    assert r["outputLabel"] == "any" or r["outputLabel"] == "AUDIO"  # never the OFF text
    assert r["outputLabel"] != probe["pure"]["exports"]["offLabel"]
    assert len(r["toasts"]) == 1
    toast = r["toasts"][0]
    assert toast["severity"] == "warn"
    assert toast["summary"] == "EPS Bypass"
    assert "Save Audio" in toast["detail"]  # names the node
    assert f"#{r['saveId']}" in toast["detail"]
    assert "(audio)" in toast["detail"]  # and the input
    assert "Required input is missing" in toast["detail"]  # and why
    assert toast["life"] >= 6000  # long enough to read
    assert r["secondAttempt"] == {"enabled": True, "toasts": 1}


def test_refusal_wins_even_when_the_vue_model_lands_after_the_callback(probe: dict) -> None:
    assert probe["g"]["refuseVueOrder"] == {"enabled": True, "wired": True}


def test_a_mixed_fan_out_refuses_wholesale(probe: dict) -> None:
    """One required target among optional ones: NOTHING is unplugged. A
    half-unplugged node would read OFF with a wire still attached, and the
    backend fallback would then block that consumer -- a silent skip."""
    m = probe["g"]["refuseMixed"]
    assert m["enabled"] is True
    assert m["videoWired"] is True  # the optional one was NOT unplugged either
    assert m["saveWired"] is True
    assert m["memoryRaw"] == "{}"
    assert m["toastCount"] == 1
    assert "Save Audio" in m["detail"]
    assert "Create Video" not in m["detail"]  # only the blocker is named


def test_a_widget_backed_required_input_is_safe_to_unplug(probe: dict) -> None:
    """KSampler.cfg is declared required but is a widget: unplugging hands
    its own value back (the Number Controller's checkbox mechanism)."""
    w = probe["g"]["widgetBacked"]
    assert w["off"]["wired"] is False
    assert len(w["off"]["memory"]) == 1
    assert w["onWired"] is True
    assert w["toasts"] == 0


def test_a_dynamic_optional_socket_is_safe_by_its_shape(probe: dict) -> None:
    assert probe["g"]["dynamicSocket"] == {"enabled": False, "wired": False}


def test_an_input_that_cannot_be_verified_refuses(probe: dict) -> None:
    u = probe["g"]["unknownSocket"]
    assert u["enabled"] is True
    assert u["wired"] is True
    assert "couldn't confirm" in u["detail"]


def test_wires_that_cannot_be_remembered_refuse_instead_of_being_lost(probe: dict) -> None:
    """disconnectAllTargets severs EVERY link; one collectOutputTargets could
    not record (nameless Reroute input, a vanished target) would be lost for
    good -- so the guard refuses first."""
    rr = probe["g"]["rerouteRefuses"]
    assert rr["enabled"] is True
    assert rr["wired"] is True
    assert "legacy Reroute node" in rr["detail"]
    dangling = probe["g"]["danglingRefuses"]
    assert dangling["enabled"] is True
    assert "no longer exists" in dangling["detail"]
    # A wire into a subgraph's own output has no node behind it either, and
    # is named for what it is rather than as a "missing node".
    sub = probe["g"]["subgraphOutputRefuses"]
    assert sub["enabled"] is True
    assert "the subgraph's output" in sub["detail"]


def test_a_wire_that_survives_the_unplug_rolls_everything_back(probe: dict) -> None:
    r = probe["g"]["rollback"]
    assert r["enabled"] is True
    assert r["stubbornWired"] is True
    assert r["fineWired"] is True  # the one that DID unplug was put back
    assert r["memoryRaw"] == "{}"
    assert "Couldn't unplug every wire" in r["detail"]


# ----------------------------------------------- stale / hand-made wires


def test_stale_targets_are_skipped_quietly_and_a_claimed_socket_is_never_stomped(
    probe: dict,
) -> None:
    s = probe["g"]["staleTargets"]
    # A's socket was claimed by a hand-made wire meanwhile: kept, not stomped.
    assert s["aStillTheHandWire"] is True
    assert s["aOrigin"] == s["otherId"]
    assert s["cWired"] is False  # renamed input: skipped
    assert s["bypassOutputLinks"] == 0  # nothing at all came back
    assert s["memoryRaw"] == "{}"  # ONE attempt, then forgotten
    assert s["enabled"] is True
    assert "Reconnected 0 of 3 wires" in s["detail"]
    assert s["toastCount"] == 1


def test_a_partial_restore_says_how_many_came_back(probe: dict) -> None:
    p = probe["g"]["partialRestore"]
    assert p["p1Wired"] is True
    assert "Reconnected 1 of 2 wires" in p["detail"]
    assert p["memoryRaw"] == "{}"


# ------------------------------------------------------- the rebuild law


def test_a_rebuilt_node_restores_off_from_its_widgets_alone(probe: dict) -> None:
    """Tab switch / undo / redo / reload: a brand-new node object, attach()
    before configure(), everything re-derived from the saved widgets."""
    r = probe["g"]["rebuild"]["restored"]
    assert r["enabled"] is False
    assert r["memoryRaw"] != "{}"  # the memory came back with the workflow
    assert r["outputLabel"] == r["syncLabel"] == probe["pure"]["exports"]["offLabel"]
    assert r["inputType"] == "AUDIO"
    assert r["inputLabel"] == "AUDIO"
    assert r["stayedOff"] is True  # loading did NOT replug
    assert r["toasts"] == 0


def test_switching_on_after_a_rebuild_reconnects_the_original_target(probe: dict) -> None:
    rb = probe["g"]["rebuild"]
    assert rb["replugged"] is True
    assert rb["originId"] == rb["bypassId"] == 10


def test_loading_never_rewires_an_off_node_that_loads_wired(probe: dict) -> None:
    """Only a user action, a state Apply or a wire dragged on changes the
    graph. The backend's blocker fallback keeps 'off' meaning off meanwhile."""
    r = probe["g"]["loadNeverRewiresOffWired"]
    assert r["enabled"] is False
    assert r["stillWired"] is True
    assert r["sameLink"] is True
    assert r["toasts"] == 0


def test_loading_never_reconnects_from_a_memory(probe: dict) -> None:
    r = probe["g"]["loadNeverRewiresOnUnwired"]
    assert r["enabled"] is True
    assert r["wired"] is False
    assert r["memoryRaw"] != "{}"  # left alone, not silently consumed


def test_a_pasted_copys_memory_is_stale_and_never_wires_the_original_consumers(
    probe: dict,
) -> None:
    p = probe["g"]["pastedCopy"]
    after = p["afterCopyOn"]
    assert after["videoWiredToAnything"] is False  # the copy did NOT grab it
    assert after["copyMemoryRaw"] == "{}"  # stale memory dropped
    assert after["toasts"] == 0
    # ...and the original still reconnects its own consumer.
    assert p["originalReplugged"] is True
    assert p["originalOrigin"] == p["originalId"]


# ------------------------------------------ the Universal State Controller


def test_apply_route_performs_the_same_unplug_and_replug_as_a_click(probe: dict) -> None:
    a = probe["g"]["applyRoute"]
    assert a["off"]["wired"] is False
    assert a["off"]["enabled"] is False
    assert len(a["off"]["memory"]) == 1
    assert a["off"]["outputLabel"] == probe["pure"]["exports"]["offLabel"]
    assert a["on"]["wired"] is True
    assert a["on"]["memoryRaw"] == "{}"
    assert a["toasts"] == 0


def test_the_announce_pass_after_a_callback_is_an_idempotent_noop(probe: dict) -> None:
    idem = probe["g"]["applyRoute"]["idempotent"]
    assert idem["rawSame"] is True
    assert idem["wired"] is False


def test_the_announce_alone_unplugs_and_replugs(probe: dict) -> None:
    """A writer that sets widget.value without firing the callback still gets
    the wiring, through api.js's announceWidgetsChangedExternally."""
    a = probe["g"]["applyRouteAnnounceOnly"]
    assert a["off"]["wired"] is False
    assert len(a["off"]["memory"]) == 1
    assert a["onWired"] is True
    assert a["memoryRaw"] == "{}"


def test_applying_off_onto_a_required_target_is_refused_and_reverted(probe: dict) -> None:
    r = probe["g"]["applyRouteRefused"]
    assert r["enabled"] is True
    assert r["wired"] is True
    assert "Required input is missing" in r["detail"]


def test_applying_off_to_an_already_off_node_keeps_its_memory(probe: dict) -> None:
    assert probe["g"]["applyAlreadyOff"] == {"memorySame": True, "wired": False}


def test_the_external_write_subscription_is_installed_once(probe: dict) -> None:
    assert probe["g"]["subscriptionInstalled"] == "function"


# --------------------------------------------- a wire dragged on while off


def test_a_new_wire_on_an_off_node_switches_it_back_on_and_forgets_the_memory(
    probe: dict,
) -> None:
    r = probe["g"]["autoReenable"]
    assert r["enabled"] is True
    assert r["memoryRaw"] == "{}"
    assert r["freshWired"] is True  # the user's wire is kept
    assert r["oldRestored"] is False  # the superseded memory is NOT replayed
    assert r["toasts"] == ["info"]


def test_no_auto_reenable_while_the_graph_is_being_configured(probe: dict) -> None:
    assert probe["g"]["noReenableWhileConfiguring"] == {"enabled": False}


# ------------------------------------------------ adoption / labels / colour


def test_type_is_adopted_from_the_output_side_and_reverts_when_unwired(probe: dict) -> None:
    a = probe["g"]["adoption"]
    out = a["fromOutput"]
    assert out["inputType"] == out["outputType"] == "AUDIO"
    assert out["inputLabel"] == out["outputLabel"] == "AUDIO"
    assert out["linkColor"] == "#audio"
    gone = a["afterUnwire"]
    assert gone["inputType"] == gone["outputType"] == "*"
    assert gone["inputLabel"] == gone["outputLabel"] == "any"


def test_adopting_the_first_type_is_what_makes_litegraph_refuse_a_mismatch(probe: dict) -> None:
    """Once the first wire has set the sockets' concrete type, litegraph's
    OWN isValidConnection refuses a mismatched later connection -- to the
    input and to the output. That is the whole 'refuse mismatched later
    connections' mechanism; no veto hook of ours is involved."""
    m = probe["g"]["mismatchRefused"]
    assert m["firstAccepted"] is True
    assert m["adopted"] == "IMAGE"
    assert m["secondAccepted"] is False
    assert m["outputMismatchAccepted"] is False
    assert m["upstreamLinkKept"] is True  # the refusal destroyed nothing


def test_link_colour_honours_another_modules_ownership_tag(probe: dict) -> None:
    c = probe["g"]["linkColorOwnership"]
    assert c["whileTagged"] == {"tagged": "#dim", "untagged": "#audio"}
    assert c["afterRelease"] == "#audio"


def test_off_with_nothing_wired_does_nothing_but_say_so(probe: dict) -> None:
    o = probe["g"]["offUnwired"]
    assert o["enabled"] is False
    assert o["memoryRaw"] == "{}"
    assert o["outputLabel"] == probe["pure"]["exports"]["offLabel"]
    assert o["toasts"] == 0
    assert o["backOn"] == {"enabled": True, "outputLabel": "any"}


def test_the_toggle_callback_is_chained_never_replaced(probe: dict) -> None:
    assert probe["g"]["callbackChain"] == {"calls": [False], "ret": "orig"}


# --------------------------------------------------- source-level structure


def test_shared_helpers_are_imported_not_copied(source: str) -> None:
    """ROADMAP-shared-panel-code.md: the unplug/replug machinery and the
    adopted-type rule are IMPORTED under their existing bare names. A hand
    copy here is the drift this pack has been bitten by three times."""
    nc_import = re.search(r"import \{([^}]*)\} from './number_controller\.js'", source)
    assert nc_import, "bypass.js must import from number_controller.js"
    imported = {n.strip() for n in nc_import.group(1).split(",") if n.strip()}
    assert imported >= {
        "collectOutputTargets",
        "disconnectAllTargets",
        "reconnectRememberedTargets",
        "normalizeRememberedLinks",
        "hideValuesWidget",
        "installMinWidth",
        "isOutputConnected",
    }
    dist_import = re.search(r"import \{([^}]*)\} from './distributor\.js'", source)
    assert dist_import, "bypass.js must import from distributor.js"
    dist_names = {n.strip() for n in dist_import.group(1).split(",") if n.strip()}
    assert dist_names >= {"resolveAdoptedType", "LINK_COLOR_OWNER_KEY", "LINK_COLOR_RESYNC_HOOK"}
    for name in (*imported, *dist_names):
        assert not re.search(rf"^(export )?(function|const) {name}\b", source, re.M), (
            f"{name} is imported AND redeclared in bypass.js"
        )


def test_the_shared_exports_exist_and_name_this_file(source: str) -> None:
    """The other half of the twin rule: the exporting files carry the
    export AND a comment naming bypass.js, so the next person to touch them
    finds the second caller."""
    nc = NUMBER_CONTROLLER_JS.read_text(encoding="utf-8")
    for name in (
        "collectOutputTargets(node, idx)",
        "disconnectAllTargets(node, idx)",
        "reconnectRememberedTargets(node, idx, remembered)",
        "hideValuesWidget(node, widget)",
        "installMinWidth(node, minWidth)",
        "normalizeRememberedLinks(raw)",
    ):
        assert f"export function {name} {{" in nc, name
    assert nc.count("bypass.js") >= 4
    dist = DISTRIBUTOR_JS.read_text(encoding="utf-8")
    assert "export const LINK_COLOR_OWNER_KEY = '__epsLinkColorOwner'" in dist
    assert "export const LINK_COLOR_RESYNC_HOOK = '__epsResyncLinkColors'" in dist
    assert "bypass.js" in dist


def test_nothing_is_hand_drawn(source: str) -> None:
    """No canvas drawing => no VUE_AFFECTED_CLASSES entry needed
    (tests/test_bypass.py pins the other half)."""
    for hook in ("onDrawForeground", "onMouseDown", "onDblClick", "drawWidget"):
        assert hook not in source.split("*/", 1)[1], hook


def test_loading_never_rewires_by_construction(source: str) -> None:
    """onConfigure and the post-load pass may repaint; they may not call
    anything that can switch a wire."""
    attach = _function_body(source, "attach(node)")
    on_configure = attach.split("node.onConfigure = function (info) {", 1)[1].split(
        "\n    }\n", 1
    )[0]
    for wiring in ("switchOff(", "switchOn(", "reconcile(", "onEnabledChanged(", "autoReenable"):
        assert wiring not in on_configure, wiring
    assert "schedule(false)" in on_configure  # the post-load pass never re-enables


def test_auto_reenable_runs_only_from_the_connection_hook(source: str) -> None:
    """'stored off, still wired' is identical for a wire just dragged on
    (re-enable) and a state Apply that has not caught up (unplug): only the
    caller can tell, so exactly ONE caller may re-enable."""
    calls = source.count("autoReenableIfWiredWhileOff(state)")
    declarations = source.count("function autoReenableIfWiredWhileOff(state)")
    assert calls - declarations == 1
    wiring = _function_body(source, "wireConnectionSync(state)")
    assert "if (reenable) autoReenableIfWiredWhileOff(state)" in wiring
    # ...and a connection event during a whole-graph load is not a user wire.
    # The flag is read at EVENT time: by the time the deferred pass runs a
    # synchronous load has finished and the flag is back to false.
    assert "schedule(!isGraphConfiguring())" in wiring
    for name in ("reconcile(state)", "onEnabledChanged(state)", "switchOff(state)"):
        assert "autoReenable" not in _function_body(source, name), name


def test_settle_and_sync_types_never_write_a_widget(source: str) -> None:
    """The re-render law (FORMAT.md section 7.9): repainting is read-only
    with respect to every widget."""
    for signature in ("settle(state)", "syncTypes(node)"):
        body = _function_body(source, signature)
        assert not re.search(r"\.value\s*=[^=]", body), f"{signature} assigns a widget value"
        assert "writeMemory(" not in body
        assert ".callback" not in body


def test_the_memory_is_written_from_one_place(source: str) -> None:
    body = _function_body(source, "writeMemory(state, links)")
    assert "serializeMemory(state.node.id, links)" in body
    assert "state.linksWidget.value = json" in body
    assert source.count("linksWidget.value = ") == 1


def test_switch_off_remembers_before_it_unplugs(source: str) -> None:
    body = _function_body(source, "switchOff(state)")
    assert body.index("writeMemory(state, targets)") < body.index(
        "disconnectAllTargets(node, OUTPUT_INDEX)"
    )
    # The guard runs before ANY mutation.
    assert body.index("collectTargetVerdicts(node)") < body.index("writeMemory(state, targets)")
    assert "refuseOff(state, blockers)" in body


def test_switch_on_replays_through_the_shared_validated_path(source: str) -> None:
    body = _function_body(source, "switchOn(state)")
    assert "reconnectRememberedTargets(node, OUTPUT_INDEX, remembered)" in body
    assert "writeMemory(state, [])" in body  # one attempt, then forgotten


def test_refusal_reverts_directly_and_asserts_again_next_tick(source: str) -> None:
    body = _function_body(source, "refuseOff(state, blockers)")
    assert "enabledWidget.value = true" in body
    assert ".callback" not in body  # never re-enters the toggle handler
    assert "setTimeout(" in body
    assert "state.refusalToken" in body


def test_the_hooks_chain_the_originals(source: str) -> None:
    assert "const original = enabledWidget.callback" in source
    assert "original.apply(this, args)" in source
    assert "const originalConfigure = node.configure" in source
    assert "originalConfigure?.apply(this, args)" in source
    assert "const originalOnConnectionsChange = node.onConnectionsChange" in source
    assert "originalOnConnectionsChange.apply(this, arguments)" in source
    assert "const originalOnConfigure = node.onConfigure" in source
    assert "originalOnConfigure?.apply(this, arguments)" in source


def test_only_our_own_sockets_schedule_a_pass(source: str) -> None:
    body = _function_body(source, "wireConnectionSync(state)")
    assert "const ours = slot === outputOf(this) || slot?.name === INPUT_NAME" in body


def test_no_type_veto_hook_is_installed(source: str) -> None:
    """Any type may be carried; once both sockets are concrete litegraph's
    own isValidConnection refuses a mismatch."""
    assert "onConnectOutput" not in source.split("*/", 1)[1]
    assert "onConnectInput" not in source.split("*/", 1)[1]
