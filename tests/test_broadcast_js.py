"""Glue + UI tests for EPS Run Multiplier BROADCAST (FORMAT.md §6.10
"Broadcast (v1)") -- ``web/eps_image/broadcast.js`` and
``web/eps_image/broadcast_ui.js``.

``broadcast.js`` is the node glue: the row mounted INSIDE the readout element,
the ⋯ popover, the preview dialog, the menu items, the Keep-wired graph watch
and the Use Everywhere veto. Everything here runs the REAL modules under Node
against the shared fake litegraph (``tests/fake_litegraph.mjs``) and a tiny
fake DOM (``tests/fake_dom.mjs`` -- there is no jsdom in this repo). Timers are
real: the Keep-wired pass is one short debounce, so scenarios ``settle()``
past it.

What this CANNOT cover, and the rig must: real pixels (the row's layout in
both renderers), the Vue node pane's event routing, a real context menu, real
``litegraph:canvas`` events from a real drag, the real ComfyUI setting store.
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
from served_layout import CROSS_SWEEP_MODULES, build_served_layout

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB = REPO_ROOT / "web"
BROADCAST_JS = WEB / "eps_image" / "broadcast.js"
UI_JS = WEB / "eps_image" / "broadcast_ui.js"
CROSS_SWEEP_JS = WEB / "eps_image" / "cross_sweep.js"
ENTRY_JS = WEB / "eps_image.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

PROBE_JS = r"""
import * as bc from './extensions/comfyui-epsnodes/eps_image/broadcast.js'
import * as ui from './extensions/comfyui-epsnodes/eps_image/broadcast_ui.js'
import * as plan from './extensions/comfyui-epsnodes/eps_image/broadcast_plan.js'
import { app } from './scripts/app.js'
import * as fl from './fake_litegraph.mjs'
import * as fd from './fake_dom.mjs'

const out = {}
const { body } = fd.installFakeDom()
const { build, ksampler, decode, saver, wiredFrom, props, makeNode, FakeSubgraph, FakeSubgraphNode, linkOf } = fl

const toasts = []
const store = {}
app.configuringGraph = false
app.canvas = { emitBeforeChange() {}, emitAfterChange() {} }
app.extensionManager = {
  toast: { add(t) { toasts.push(t.summary); (globalThis.__toastSeverities ??= []).push(t.severity); (globalThis.__toastDetails ??= []).push(t.detail) } },
  setting: { get: (id) => store[id] },
  workflow: { activeWorkflow: { changeTracker: { captureCanvasState() {} } } }
}
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const settle = () => sleep(320)
const resetToasts = () => { toasts.length = 0 }
const mkReadout = (node) => ({
  node, rootEl: new fd.FakeEl('div'), textHeight: 22, outerHeight: 42,
  domWidget: { computedHeight: 42 }, extraHeight: 0
})
const dialogEl = () => fd.byClass(body, 'eps-bc-dialog')[0] || null
const popEl = () => fd.byClass(body, 'eps-bc-pop')[0] || null
const itemBoxes = (root) => fd.findAll(root, (el) => el.tag === 'input' && el.attrs.type === 'checkbox')
const tick = (box, on) => { box.checked = on; fd.fire(box, 'change') }

bc.init()
bc.init() // idempotent

// ============================================================ 1. attach
{
  const { root, m } = build()
  const calls = []
  m.onConnectionsChange = function () { calls.push('orig'); return 'orig-result' }
  const ro = mkReadout(m)
  bc.attach(m, ro)
  bc.attach(m, ro) // a double nodeCreated is a no-op
  const row = fd.byClass(ro.rootEl, 'eps-bc-row')
  out.attach = {
    rows: row.length,
    rootClass: ro.rootEl.className,
    label: fd.byClass(ro.rootEl, 'eps-bc-label')[0]?.textContent,
    summary: fd.byClass(ro.rootEl, 'eps-bc-summary')[0]?.textContent,
    buttons: fd.findAll(ro.rootEl, (e) => e.tag === 'button').map((b) => b.textContent),
    extraHeight: ro.extraHeight, outerHeight: ro.outerHeight, rootHeight: ro.rootEl.style.height,
    computedHeight: ro.domWidget.computedHeight,
    rowHeightConst: ui.BROADCAST_ROW_HEIGHT,
    chained: m.onConnectionsChange(1, 0, true) === 'orig-result' && calls[0] === 'orig',
    state: typeof m.__epsBcState,
    ue: Object.fromEntries(['model', 'clip', 'vae', 'label', 'model_low', 'text', 'image', 'name', 'base_folder', 'solo_run']
      .map((name) => [name, m.reject_ue_connection({ name })])),
    ueNoInput: m.reject_ue_connection(undefined), ueNull: m.reject_ue_connection(null)
  }
  // a multiplier with NO readout DOM still gets the UE veto and works
  const bare = fl.makeMultiplier(root, 77)
  bc.attach(bare, undefined)
  out.attach.bareUe = bare.reject_ue_connection({ name: 'model' })
  out.attach.bareState = typeof bare.__epsBcState
}

// =========================================================== 2. menu items
{
  const { root, m } = build()
  bc.attach(m, mkReadout(m))
  const items = bc.getNodeMenuItems(m)
  const other = ksampler(root, 10)
  out.menu = {
    contents: items.map((i) => i.content),
    callbacks: items.map((i) => typeof i.callback),
    other: bc.getNodeMenuItems(other),
    nothing: bc.getNodeMenuItems(null)
  }
  items[2].callback() // keep wired
  await sleep(20)
  out.menu.afterKeep = bc.getNodeMenuItems(m).map((i) => i.content)
  out.menu.keepProp = props(m).keep
  bc.getNodeMenuItems(m)[2].callback()
  out.menu.afterKeepOff = bc.getNodeMenuItems(m).map((i) => i.content)
  await settle()
}

// ================================================ 3. the legacy menu fallback
{
  class T { getExtraMenuOptions(canvas, options) { options.push({ content: 'orig' }); return 'ret' } }
  delete app.collectNodeMenuItems
  bc.installLegacyMenuFallback(T, { name: 'EPSCrossSweep' })
  bc.installLegacyMenuFallback(T, { name: 'EPSCrossSweep' }) // never double-patched
  const { m } = build()
  const options = [{ content: 'pre' }]
  const ret = T.prototype.getExtraMenuOptions.call(m, {}, options)
  class U { getExtraMenuOptions() { return 'u' } }
  bc.installLegacyMenuFallback(U, { name: 'KSampler' })
  class V { getExtraMenuOptions() { return 'v' } }
  app.collectNodeMenuItems = () => []
  bc.installLegacyMenuFallback(V, { name: 'EPSCrossSweep' })
  delete app.collectNodeMenuItems
  out.legacy = {
    ret, contents: options.map((o) => (o === null ? '---' : o.content)),
    otherClassUntouched: U.prototype.getExtraMenuOptions.call({}, {}, []) === 'u' && !U.prototype.__epsBroadcastMenuPatched,
    modernFrontendUntouched: !V.prototype.__epsBroadcastMenuPatched
  }
}

// ============================================================= 4. settings
{
  const [setting] = bc.SETTINGS
  out.settings = {
    count: bc.SETTINGS.length, id: setting.id, type: setting.type, defaultValue: setting.defaultValue,
    category: setting.category, name: setting.name, hasTooltip: setting.tooltip.length > 40,
    onChange: typeof setting.onChange,
    readOff: bc.readSettings(),
  }
  store[bc.BROADCAST_SETTING_ID] = true
  out.settings.readOn = bc.readSettings()
  store[bc.BROADCAST_SETTING_ID] = 'yes'
  out.settings.readJunk = bc.readSettings()
  delete store[bc.BROADCAST_SETTING_ID]
  out.settings.constId = bc.BROADCAST_SETTING_ID
  out.settings.ueList = [...bc.UE_REJECTED_INPUTS]
}

// =============================== 5. Wire now: dialog, ticks, leave alone
{
  const { root, m } = build()
  const ro = mkReadout(m)
  bc.attach(m, ro)
  const k10 = ksampler(root, 10)
  const k11 = ksampler(root, 11)
  const d13 = decode(root, 13)
  await settle()
  resetToasts()
  bc.wireNow(m)
  const dlg = dialogEl()
  const boxes = () => itemBoxes(dlg)
  const connect = () => fd.findAll(dlg, (e) => e.tag === 'button' && /^Connect|^Nothing/.test(e.textContent))[0]
  const first = {
    open: Boolean(dlg), title: dlg.attrs['aria-label'],
    heads: fd.byClass(dlg, 'eps-bc-group-head').map((e) => e.textContent),
    items: fd.byClass(dlg, 'eps-bc-item').length, connectText: connect().textContent,
    skippedSummary: fd.findAll(dlg, (e) => e.tag === 'summary').map((e) => e.textContent),
    capture: dlg.parentNode.attrs?.role ?? null
  }
  // untick k11's model row
  const rows = fd.byClass(dlg, 'eps-bc-item')
  const k11Row = rows.find((r) => r.title.includes('#11'))
  tick(itemBoxes(k11Row)[0], false)
  const afterUntick = connect().textContent
  fd.fire(connect(), 'click')
  await sleep(20)
  out.wireNow = {
    first, afterUntick,
    closed: dialogEl() === null,
    k10: wiredFrom(k10, 'model'), k11: wiredFrom(k11, 'model'), d13: wiredFrom(d13, 'vae'),
    skip: props(m).skip, wired: props(m).wired.map((e) => e.key).sort(),
    toasts: [...toasts],
    summaryAfter: fd.byClass(ro.rootEl, 'eps-bc-summary')[0].textContent
  }
  // re-open: k11 is LEFT ALONE and offered, unticked, to re-include
  bc.wireNow(m)
  const dlg2 = dialogEl()
  const left = fd.byClass(dlg2, 'eps-bc-group-head').map((e) => e.textContent)
  const leftBoxes = itemBoxes(dlg2)
  const reopened = { heads: left, boxCount: leftBoxes.length, boxChecked: leftBoxes.map((b) => b.checked) }
  const leftBox = itemBoxes(fd.byClass(dlg2, 'eps-bc-item').find((r) => r.title.includes('#11')))[0]
  tick(leftBox, true)
  const btn = fd.findAll(dlg2, (e) => e.tag === 'button' && /^Connect|^Nothing/.test(e.textContent))[0]
  reopened.connectText = btn.textContent
  fd.fire(btn, 'click')
  await sleep(20)
  reopened.k11 = wiredFrom(k11, 'model')
  reopened.skip = props(m).skip ?? []
  out.wireNow.reopened = reopened
  // Esc closes and is swallowed; any other key is swallowed but does not close
  bc.wireNow(m)
  const esc = fd.fireDocument('keydown', { key: 'a' })
  const stillOpen = dialogEl() !== null
  const esc2 = fd.fireDocument('keydown', { key: 'Escape' })
  out.wireNow.keys = { otherStopped: esc.stopped, stillOpen, escStopped: esc2.stopped, escPrevented: esc2.defaultPrevented, closed: dialogEl() === null }
  // Cancel does nothing
  bc.wireNow(m)
  const cancel = fd.byText(dialogEl(), 'Cancel')[0]
  fd.fire(cancel, 'click')
  out.wireNow.cancel = { closed: dialogEl() === null }
  // backdrop click closes
  bc.wireNow(m)
  const overlay = fd.byClass(body, 'eps-bc-overlay')[0]
  fd.fire(overlay, 'pointerdown', { target: overlay })
  out.wireNow.backdrop = { closed: dialogEl() === null }
}

// ============================================ 6. nothing to wire / skips list
{
  const { root, m, loader } = build()
  bc.attach(m, mkReadout(m))
  loader.connect(0, ksampler(root, 10), 0) // already wired: nothing to connect
  makeNode(root, 30, 'Opt', 'Optional taker', [{ name: 'model', type: 'MODEL', shape: 7 }])
  bc.wireNow(m)
  const dlg = dialogEl()
  const connect = fd.findAll(dlg, (e) => e.tag === 'button' && /^Connect|^Nothing/.test(e.textContent))[0]
  out.empty = {
    connectText: connect.textContent, disabled: connect.disabled,
    note: fd.byClass(dlg, 'eps-bc-note').map((e) => e.textContent),
    summaries: fd.findAll(dlg, (e) => e.tag === 'summary').map((e) => e.textContent)
  }
  fd.fireDocument('keydown', { key: 'Escape' })
}

// ====================================================== 7. the ⋯ popover
{
  const { root, m } = build()
  const ro = mkReadout(m)
  bc.attach(m, ro)
  const more = fd.byText(ro.rootEl, '⋯')[0]
  fd.fire(more, 'click')
  const pop = popEl()
  const checks = fd.byClass(pop, 'eps-bc-check')
  const names = checks.map((c) => c.children.filter((x) => x.nodeType === 3).map((x) => x.textContent).join(''))
  const cbs = checks.map((c) => itemBoxes(c)[0])
  const info = checks.map((c, i) => ({ name: names[i], checked: cbs[i].checked, disabled: cbs[i].disabled,
    badge: fd.byClass(c, 'eps-bc-badge')[0]?.textContent }))
  out.popover = { open: Boolean(pop), parent: pop.parentNode === body, info, left: pop.style.left, top: pop.style.top }
  // toggle vae off
  const vaeIdx = names.indexOf('vae')
  tick(cbs[vaeIdx], false)
  out.popover.vaeCfg = props(m).outputs
  out.popover.summaryAfterVaeOff = fd.byClass(ro.rootEl, 'eps-bc-summary')[0].textContent
  tick(cbs[vaeIdx], true)
  out.popover.afterVaeOn = { property: props(m), summary: fd.byClass(ro.rootEl, 'eps-bc-summary')[0].textContent }
  // keep wired
  const keepBox = checks.map((c) => itemBoxes(c)[0]).pop() // the keep box is a .eps-bc-check too
  tick(keepBox, true)
  await sleep(20)
  out.popover.keep = props(m).keep
  // settings ON enables the gated three
  store[bc.BROADCAST_SETTING_ID] = true
  bc.SETTINGS[0].onChange(true)
  // outside pointerdown closes; Esc closes
  fd.fireDocument('pointerdown', { target: new fd.FakeEl('div') })
  out.popover.closedByOutside = popEl() === null
  fd.fire(more, 'click')
  const pop2 = popEl()
  const gated = fd.byClass(pop2, 'eps-bc-check').map((c) => [c.children.filter((x) => x.nodeType === 3).map((x) => x.textContent).join(''), itemBoxes(c)[0].disabled])
  out.popover.gatedWhenOn = Object.fromEntries(gated.filter(([n]) => ['text', 'image', 'label'].includes(n)))
  out.popover.summaryWithSetting = fd.byClass(ro.rootEl, 'eps-bc-summary')[0].textContent
  fd.fireDocument('keydown', { key: 'Escape' })
  out.popover.closedByEsc = popEl() === null
  // the buttons
  fd.fire(more, 'click')
  fd.fire(fd.byText(popEl(), 'Wire now…')[0], 'click')
  out.popover.wireFromPopover = { popClosed: popEl() === null, dialogOpen: dialogEl() !== null }
  fd.fireDocument('keydown', { key: 'Escape' })
  // the ⋯ button TOGGLES its popover
  fd.fire(more, 'click')
  const openedOnce = popEl() !== null
  fd.fire(more, 'click')
  out.popover.toggle = { openedOnce, closedBySecondClick: popEl() === null }
  fd.fireDocument('keydown', { key: 'Escape' })
  delete store[bc.BROADCAST_SETTING_ID]
  await settle()
}

// ================================================ 8. Remove broadcast wires
{
  const { root, m } = build()
  bc.attach(m, mkReadout(m))
  const k10 = ksampler(root, 10)
  await settle()
  bc.wireNow(m)
  fd.fire(fd.findAll(dialogEl(), (e) => e.tag === 'button' && /^Connect/.test(e.textContent))[0], 'click')
  await sleep(20)
  const wired = wiredFrom(k10, 'model')
  resetToasts()
  bc.removeWires(m)
  out.remove = { wired, after: wiredFrom(k10, 'model'), property: props(m), toasts: [...toasts] }
  resetToasts()
  bc.removeWires(m)
  out.remove.second = [...toasts]
  await settle()
}

// ============================================================ 9. Keep wired
{
  const { root, loader, m, inputOf } = build()
  const ro = mkReadout(m)
  bc.attach(m, ro)
  const existing = ksampler(root, 10) // present BEFORE keep: Wire now's job, not keep's
  await settle()
  bc.setKeep(m, true)
  await settle()
  resetToasts()
  const k20 = ksampler(root, 20)
  const d21 = decode(root, 21)
  const s22 = saver(root, 22)
  await settle()
  const afterAdd = {
    existing: wiredFrom(existing, 'model'),
    k20: wiredFrom(k20, 'model'), d21: wiredFrom(d21, 'vae'), s22: wiredFrom(s22, 'filename_prefix'),
    toast: toasts.filter((t) => /wired/.test(t)),
    recorded: props(m).wired.map((e) => e.key).sort()
  }
  // canvas after-change events that did not touch the structure (a node move)
  // do no work at all: no snapshot, no plan
  const statsBefore = { ...m.__epsBcState.stats }
  for (let i = 0; i < 3; i++) fd.fireDocument('litegraph:canvas', { detail: { subType: 'after-change' } })
  await settle()
  const quiet = { snapshots: m.__epsBcState.stats.snapshots - statsBefore.snapshots,
    skipped: m.__epsBcState.stats.skipped - statsBefore.skipped }
  // a USER unplugs a recorded wire -> leave alone, never re-wired
  k20.disconnectInput(0)
  resetToasts()
  fd.fireDocument('litegraph:canvas', { detail: { subType: 'after-change' } })
  await settle()
  const unplug = {
    k20: wiredFrom(k20, 'model'), skip: props(m).skip, toasts: [...toasts],
    recorded: props(m).wired.map((e) => e.key).sort()
  }
  // the user's OWN wire always wins
  const k23 = ksampler(root, 23)
  loader.connect(0, k23, 0)
  const own = wiredFrom(k23, 'model')
  await settle()
  const ownAfter = wiredFrom(k23, 'model')
  // live-output tracking: unwire the multiplier's vae input
  resetToasts()
  m.disconnectInput(inputOf('vae'))
  await settle()
  const paused = {
    d21: wiredFrom(d21, 'vae'), toasts: [...toasts],
    withdrawn: props(m).wired.filter((e) => e.withdrawn).map((e) => e.key),
    skipNoVae: !(props(m).skip || []).some((k) => k.startsWith('vae')),
    modelStillWired: wiredFrom(existing, 'model') === null && wiredFrom(root.nodesById[20], 'model') === null
  }
  // ... and rewiring restores them
  resetToasts()
  loader.connect(2, m, inputOf('vae'))
  await settle()
  const restored = { d21: wiredFrom(d21, 'vae'), toasts: [...toasts], withdrawn: props(m).wired.filter((e) => e.withdrawn).length }
  out.keep = { afterAdd, quiet, unplug, own, ownAfter, paused, restored }

  // a reload (onConfigure) re-baselines: existing empty inputs are NEVER wired
  const k30 = ksampler(root, 30)
  m.onConfigure({})
  await settle()
  out.keep.reload = { k30: wiredFrom(k30, 'model') }
  // keep OFF: new nodes are left alone
  bc.setKeep(m, false)
  await settle()
  const k31 = ksampler(root, 31)
  await settle()
  out.keep.off = { k31: wiredFrom(k31, 'model') }
  // a graph mid-configure is never planned against
  bc.setKeep(m, true)
  await settle()
  app.configuringGraph = true
  const k32 = ksampler(root, 32)
  await sleep(250)
  app.configuringGraph = false
  out.keep.configuring = { k32: wiredFrom(k32, 'model') }
}

// =================== 10. stale records on a PASTED multiplier are dropped
{
  const { root, m } = build()
  const k10 = ksampler(root, 10)
  m.properties.Broadcast = {
    v: 1,
    wired: [{ key: 'model|10|model', out: 'model', kind: 'direct', to: '10', input: 'model',
      links: [{ g: 'root', n: '10', i: 'model', o: { m: 'model' } }], made: [] }],
    skip: ['model|77|model']
  }
  resetToasts()
  bc.attach(m, mkReadout(m))
  await settle()
  out.paste = { wired: props(m)?.wired ?? [], skip: props(m)?.skip ?? [], k10: wiredFrom(k10, 'model'), toasts: [...toasts] }
}

// ============================ 11. keep: subgraph instances (existing input)
{
  const { root, m } = build(['model'])
  const def = new FakeSubgraph(root, 'sg-a', 'Group')
  const input = def.addInput('model', 'MODEL')
  const inner = ksampler(def, 5, 'Inner')
  input.connect(inner.inputs[0], inner)
  const inst1 = root.add(new FakeSubgraphNode(3, 'Group', def))
  bc.attach(m, mkReadout(m))
  await settle()
  bc.setKeep(m, true)
  await settle()
  resetToasts()
  const inst2 = root.add(new FakeSubgraphNode(4, 'Group copy', def)) // a FRESH instance of an existing group
  await settle()
  // a fresh INNER node in a shared definition: never auto-wired (needs Wire now)
  const def2 = new FakeSubgraph(root, 'sg-b', 'Other')
  const root2inst = root.add(new FakeSubgraphNode(6, 'Other', def2))
  await settle()
  resetToasts()
  const innerNew = ksampler(def2, 9, 'Fresh inner')
  def2.onNodeAdded?.(innerNew)
  await settle()
  out.keepSub = {
    inst1: wiredFrom(inst1, 'model'), inst2: wiredFrom(inst2, 'model'),
    innerNew: wiredFrom(innerNew, 'model'), defInputs: def2.inputs.length,
    toasts: [...toasts]
  }
}

// =========================================== 12. no row when it can't mount
{
  const { m } = build()
  const ro = { node: m, rootEl: null }
  bc.attach(m, ro)
  out.noRow = { state: typeof m.__epsBcState, ue: m.reject_ue_connection({ name: 'vae' }) }
}

// ================================================= 13. exports for rendering
out.exports = Object.keys(bc).sort()

// ============================================ 14. pure UI helpers
out.ui = {
  groupSkips: ui.groupSkips([
    { code: 'output-dead', reason: 'a', output: 'vae' }, { code: 'left-alone', key: 'k', proposal: { key: 'k' } },
    { code: 'already-wired', reason: 'x', targetPathId: '1' }, { code: 'loop', reason: 'y', targetPathId: '2' },
    { code: 'already-wired', reason: 'x', targetPathId: '3' }, { code: 'wan-unresolved', reason: 'z' }
  ]),
  selection: ui.selectionOutcome(
    { proposals: [{ key: 'a' }, { key: 'b' }, { key: 'c' }],
      skips: [{ code: 'left-alone', key: 'd', proposal: { key: 'd' } }, { code: 'left-alone', key: 'e', proposal: { key: 'e' } }] },
    ['a', 'c', 'e']),
  byOutput: ui.groupByOutput([{ output: 'vae', key: '1' }, { output: 'model', key: '2' }, { output: 'vae', key: '3' }]),
  rowSummary: [
    ui.rowSummary({ live: ['model', 'clip'], wired: 0, paused: 0, keep: false }),
    ui.rowSummary({ live: [], wired: 0, paused: 0, keep: false }),
    ui.rowSummary({ live: ['vae'], wired: 3, paused: 1, keep: true })
  ],
  toastText: ui.wiredToastText(
    [{ key: 'a' }, { key: 'b' }, { key: 'c' }, { key: 'd' }, { key: 'e' }],
    new Map([
      ['a', { targetTitle: 'KSampler', targetPathId: '1', output: 'model' }],
      ['b', { targetTitle: 'KSampler', targetPathId: '1', output: 'vae' }],
      ['c', { targetTitle: 'Save', targetPathId: '2', output: 'save_prefix' }],
      ['d', { targetTitle: 'X', targetPathId: '3', output: 'clip' }],
      ['e', { targetTitle: 'Y', targetPathId: '4', output: 'clip' }]
    ])
  )
}

// ======================= 15. tucked wires + reach: the popover's two choices
{
  const { root, m } = build()
  const k10 = ksampler(root, 10)
  const ro = mkReadout(m)
  bc.attach(m, ro)
  await settle()
  bc.wireNow(m)
  fd.fire(fd.findAll(dialogEl(), (e) => e.tag === 'button' && /^Connect/.test(e.textContent))[0], 'click')
  await sleep(20)
  const linkId = k10.inputs[0].link
  const more = fd.byText(ro.rootEl, '⋯')[0]
  fd.fire(more, 'click')
  const pop = popEl()
  const selects = fd.findAll(pop, (e) => e.tag === 'select')
  const labels = fd.byClass(pop, 'eps-bc-field').map((f) => f.children[0].textContent)
  const field = {
    count: selects.length, labels,
    looks: selects[0].children.map((o) => [o.attrs.value, o.textContent]), look: selects[0].value,
    scopes: selects[1].children.map((o) => [o.attrs.value, o.textContent]), scope: selects[1].value,
    aria: selects.map((e) => e.attrs['aria-label']),
    // the output checkboxes are untouched by the new fields
    checks: fd.byClass(pop, 'eps-bc-check').length
  }
  const before = bc.broadcastLinkOwner(root, linkId)
  // choose "Dim": stored, and the cached owner's look follows (the epoch was bumped)
  selects[0].value = 'dim'
  fd.fire(selects[0], 'change')
  const dim = { look: props(m).look, owner: bc.broadcastLinkOwner(root, linkId)?.look }
  selects[0].value = 'normal'
  fd.fire(selects[0], 'change')
  const normal = { look: props(m).look, owner: bc.broadcastLinkOwner(root, linkId)?.look }
  // back to the default: stored as nothing (the wires keep the property alive, `look` is absent)
  selects[0].value = 'tucked'
  fd.fire(selects[0], 'change')
  const tucked = { look: props(m).look, hasProperty: props(m) !== null, owner: bc.broadcastLinkOwner(root, linkId)?.look }
  // a junk value is refused
  bc.setLook(m, 'sparkly')
  bc.setScope(m, 'planet')
  const junk = { look: props(m).look, scope: props(m).scope }
  // Reach: no group yet -> a warning toast, the row says "group only"
  resetToasts()
  selects[1].value = 'group'
  fd.fire(selects[1], 'change')
  const noGroup = {
    scope: props(m).scope, summary: fd.byClass(ro.rootEl, 'eps-bc-summary')[0].textContent,
    toasts: [...toasts], severity: (globalThis.__toastSeverities ?? []).slice(-1)
  }
  // the multiplier is now put INSIDE a group -> an info toast names it
  m.pos = [100, 100]
  root._groups = [{ title: 'Pipeline A', _bounding: [0, 0, 600, 600] }]
  resetToasts()
  bc.setScope(m, 'graph')
  bc.setScope(m, 'group')
  const inGroup = { toasts: [...toasts], detail: (globalThis.__toastDetails ?? []).slice(-1)[0] }
  // back to the default: nothing stored for scope
  bc.setScope(m, 'graph')
  const back = { scope: props(m).scope }
  fd.fireDocument('keydown', { key: 'Escape' })
  // Remove + defaults: a multiplier that is all-default saves with NO property
  bc.removeWires(m)
  const clean = { property: props(m) }
  out.lookScope = { field, before, dim, normal, tucked, junk, noGroup, inGroup, back, clean }
  await settle()
}

// ================== 16. canvas menu: "Broadcast: show all wires" (session-only)
{
  const { root, m } = build()
  const k10 = ksampler(root, 10)
  const ro = mkReadout(m)
  bc.attach(m, ro)
  app.rootGraph = root
  const emptyMenu = bc.getCanvasMenuItems({})
  bc.wireNow(m)
  fd.fire(fd.findAll(dialogEl(), (e) => e.tag === 'button' && /^Connect/.test(e.textContent))[0], 'click')
  await sleep(20)
  const items = bc.getCanvasMenuItems({})
  const off = { count: items.length, content: items[0]?.content, cb: typeof items[0]?.callback, on: bc.getShowAllWires() }
  items[0].callback()
  const on = { on: bc.getShowAllWires(), content: bc.getCanvasMenuItems({})[0]?.content }
  items[0].callback()
  const offAgain = { on: bc.getShowAllWires(), content: bc.getCanvasMenuItems({})[0]?.content }
  // the switch is offered while ON even with no wires, so it can always be turned off
  bc.setShowAllWires(true)
  bc.removeWires(m)
  const stuck = bc.getCanvasMenuItems({}).map((i) => i.content)
  bc.setShowAllWires(false)
  const none = bc.getCanvasMenuItems({}).length
  const noRoot = (() => { app.rootGraph = null; app.graph = null; return bc.getCanvasMenuItems({}).length })()
  app.rootGraph = root
  out.canvasMenu = { emptyMenu, off, on, offAgain, stuck, none, noRoot }
  app.rootGraph = undefined
  await settle()
}

// ================== 17. the legacy canvas-menu fallback (no declarative hook)
{
  const { root, m } = build()
  ksampler(root, 10)
  bc.attach(m, mkReadout(m))
  app.rootGraph = root
  bc.wireNow(m)
  fd.fire(fd.findAll(dialogEl(), (e) => e.tag === 'button' && /^Connect/.test(e.textContent))[0], 'click')
  await sleep(20)
  class OldCanvas { getCanvasMenuOptions() { return [{ content: 'orig' }] } }
  class ModernCanvas { getCanvasMenuOptions() { return [] } }
  class NoMethod {}
  delete app.collectCanvasMenuItems
  const first = bc.installLegacyCanvasMenuFallback(OldCanvas)
  const second = bc.installLegacyCanvasMenuFallback(OldCanvas) // never double-patched
  const wrapped = OldCanvas.prototype.getCanvasMenuOptions
  const withWires = wrapped.call({}).map((o) => (o === null ? '---' : o.content))
  bc.removeWires(m)
  const without = wrapped.call({}).map((o) => (o === null ? '---' : o.content))
  app.collectCanvasMenuItems = () => []
  const modern = bc.installLegacyCanvasMenuFallback(ModernCanvas)
  delete app.collectCanvasMenuItems
  out.legacyCanvas = {
    first, second, withWires, without, modern,
    modernUntouched: !ModernCanvas.prototype.__epsBroadcastCanvasMenuPatched,
    noMethod: bc.installLegacyCanvasMenuFallback(NoMethod), noClass: bc.installLegacyCanvasMenuFallback(undefined)
  }
  app.rootGraph = undefined
  await settle()
}

// =============== 18. setup()/init() install the drawing hooks on the canvas class
{
  class FakeCanvasClass {
    renderLink() { return 'orig' }
    select() {} deselect() {} deselectAll() {}
    getCanvasMenuOptions() { return [] }
  }
  globalThis.LGraphCanvas = FakeCanvasClass
  delete app.collectCanvasMenuItems
  bc.setup()
  const wrapped = FakeCanvasClass.prototype.renderLink
  bc.setup()
  bc.init()
  out.setup = {
    renderLink: wrapped.__epsBcRenderLink === true, stable: FakeCanvasClass.prototype.renderLink === wrapped,
    select: FakeCanvasClass.prototype.select.__epsBcSelectionRepaint === true,
    deselectAll: FakeCanvasClass.prototype.deselectAll.__epsBcSelectionRepaint === true,
    canvasMenu: FakeCanvasClass.prototype.__epsBroadcastCanvasMenuPatched === true,
    originalStillReturns: new FakeCanvasClass().renderLink({}, [0, 0], [1, 1], null, false, 0, null, 4, 3)
  }
  delete globalThis.LGraphCanvas
}

// ====== 19. Wire now under Reach: the preview names the group / the problem
{
  const { root, m } = build()
  const k10 = ksampler(root, 10)
  const k11 = ksampler(root, 11, 'KSampler outside')
  m.pos = [50, 100]
  k10.pos = [250, 100]
  k11.pos = [1500, 100]
  root._groups = [{ title: 'Pipeline A', _bounding: [0, 0, 800, 600] }]
  bc.attach(m, mkReadout(m))
  await settle()
  bc.setScope(m, 'group')
  bc.wireNow(m)
  let dlg = dialogEl()
  const inGroup = {
    notes: fd.byClass(dlg, 'eps-bc-note').map((e) => e.textContent),
    rows: fd.byClass(dlg, 'eps-bc-item').map((e) => e.title),
    heads: fd.byClass(dlg, 'eps-bc-group-head').map((e) => e.textContent)
  }
  fd.fireDocument('keydown', { key: 'Escape' })
  // the multiplier leaves the group: nothing is in reach, and the preview says why
  m.pos = [3000, 3000]
  bc.wireNow(m)
  dlg = dialogEl()
  const connect = fd.findAll(dlg, (e) => e.tag === 'button' && /^Connect|^Nothing/.test(e.textContent))[0]
  const outside = {
    notes: fd.byClass(dlg, 'eps-bc-note').map((e) => e.textContent),
    rows: fd.byClass(dlg, 'eps-bc-item').length, connectText: connect.textContent
  }
  fd.fireDocument('keydown', { key: 'Escape' })
  // Keep wired under Reach: a node added OUTSIDE the group is left alone
  m.pos = [50, 100]
  bc.setKeep(m, true)
  await settle()
  const kIn = ksampler(root, 20, 'in group'); kIn.pos = [300, 300]
  const kOut = ksampler(root, 21, 'out of group'); kOut.pos = [2500, 300]
  await settle()
  out.reachDialog = { inGroup, outside, keep: { kIn: wiredFrom(kIn, 'model'), kOut: wiredFrom(kOut, 'model') } }
}

// ======= 19b. a workflow loaded onto the SAME root graph drops the cached index
{
  const { root, m } = build()
  const k10 = ksampler(root, 10)
  bc.attach(m, mkReadout(m))
  bc.wireNow(m)
  fd.fire(fd.findAll(dialogEl(), (e) => e.tag === 'button' && /^Connect/.test(e.textContent))[0], 'click')
  await sleep(20)
  const linkId = k10.inputs[0].link
  const before = bc.broadcastLinkOwner(root, linkId)?.ownerId ?? null
  // the "new workflow": no multiplier records at all, link ids restart -- same root object
  delete m.properties.Broadcast
  const stale = bc.broadcastLinkOwner(root, linkId)?.ownerId ?? null
  const dirty = []
  const savedCanvas = app.canvas
  app.canvas = { setDirty(fg, bgc) { dirty.push([fg, bgc]) } }
  bc.afterConfigure()
  app.canvas = savedCanvas
  out.afterConfigure = { before, stale, after: bc.broadcastLinkOwner(root, linkId), repaint: dirty }
  await settle()
}

// ===================================== 20. pure UI helpers added for the new row/popover
out.ui2 = {
  rowGroup: ui.rowSummary({ live: ['vae'], wired: 2, paused: 0, keep: false, scope: 'group' }),
  rowGraph: ui.rowSummary({ live: ['vae'], wired: 2, paused: 0, keep: false, scope: 'graph' }),
  looks: ui.LOOK_CHOICES, scopes: ui.SCOPE_CHOICES,
  reach: [
    ui.reachNote({ mode: 'group', inGroup: true, groups: [{ key: 'g0', title: 'A' }, { key: 'g1', title: '' }] }),
    ui.reachNote({ mode: 'group', inGroup: false, groups: [] }),
    ui.reachNote({ mode: 'graph', inGroup: true, groups: [] }),
    ui.reachNote(undefined)
  ],
  noGroupNote: ui.groupSkips([{ code: 'no-group', reason: 'nope' }, { code: 'scope-partial', reason: 'half', targetPathId: '3' }])
}

process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def probe(tmp_path_factory: pytest.TempPathFactory) -> dict:
    layout = tmp_path_factory.mktemp("web_root")
    build_served_layout(layout, eps_modules=CROSS_SWEEP_MODULES, with_fake=True)
    probe_file = layout / "probe.mjs"
    probe_file.write_text(PROBE_JS, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(probe_file)], capture_output=True, text=True, timeout=120, cwd=layout
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def source() -> str:
    return BROADCAST_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ui_source() -> str:
    return UI_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def sweep_source() -> str:
    return CROSS_SWEEP_JS.read_text(encoding="utf-8")


# ------------------------------------------------------------------- tests


def test_modules_parse() -> None:
    for path in (BROADCAST_JS, UI_JS):
        result = subprocess.run(
            [NODE, "--check", str(path)], capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0, result.stderr


def test_row_is_mounted_inside_the_readout_element_not_as_a_widget(probe: dict) -> None:
    a = probe["attach"]
    assert a["rows"] == 1  # a double nodeCreated adds nothing
    assert "eps-rc-has-bc" in a["rootClass"]
    assert a["label"] == "📡 Broadcast"
    assert a["buttons"] == ["Wire now", "⋯"]
    # enabled AND live: save_prefix / run_info are always live
    assert a["summary"] == "model · clip · save_prefix · vae · run_info"
    # the readout box grows by exactly the row's height, in every number it reports
    assert a["extraHeight"] == a["rowHeightConst"] == 24
    assert a["outerHeight"] == 42 + 24
    assert a["computedHeight"] == 42 + 24
    assert a["rootHeight"] == "46px"  # 22 text + 24 row
    assert (
        a["chained"] is True
    )  # the original onConnectionsChange still runs, its result passes through


def test_use_everywhere_veto_covers_exactly_the_optional_sweep_inputs(probe: dict) -> None:
    ue = probe["attach"]["ue"]
    assert {k for k, v in ue.items() if v} == {"model", "clip", "vae", "label", "model_low"}
    assert probe["attach"]["ueNoInput"] is False and probe["attach"]["ueNull"] is False
    assert probe["settings"]["ueList"] == ["model", "clip", "vae", "label", "model_low"]
    # works even with no readout DOM to mount a row into
    assert probe["attach"]["bareUe"] is True and probe["attach"]["bareState"] == "object"
    assert probe["noRow"] == {"state": "object", "ue": True}


def test_menu_items_via_get_node_menu_items(probe: dict) -> None:
    m = probe["menu"]
    assert m["contents"] == [
        "Broadcast: wire now…",
        "Broadcast: remove broadcast wires",
        "Broadcast: keep wired",
    ]
    assert m["callbacks"] == ["function"] * 3
    assert m["other"] == [] and m["nothing"] == []
    assert m["afterKeep"][2] == "Broadcast: keep wired ✓" and m["keepProp"] is True
    assert m["afterKeepOff"][2] == "Broadcast: keep wired"


def test_legacy_menu_fallback_only_on_frontends_without_the_modern_hook(probe: dict) -> None:
    lg = probe["legacy"]
    assert lg["ret"] == "ret"
    assert lg["contents"] == [
        "pre",
        "orig",
        "---",
        "Broadcast: wire now…",
        "Broadcast: remove broadcast wires",
        "Broadcast: keep wired",
    ]
    assert lg["otherClassUntouched"] is True
    assert lg["modernFrontendUntouched"] is True


def test_the_one_setting(probe: dict) -> None:
    s = probe["settings"]
    assert s["count"] == 1
    assert s["id"] == s["constId"] == "EPSNodes.BroadcastSameNameInputs"
    assert s["type"] == "boolean" and s["defaultValue"] is False  # OFF by default
    assert s["category"][0] == "EPSNodes"
    assert "text, image and label" in s["name"] and "same name" in s["name"]
    assert s["hasTooltip"] and s["onChange"] == "function"
    assert s["readOff"] == {"exactNames": False}
    assert s["readOn"] == {"exactNames": True}
    assert s["readJunk"] == {"exactNames": False}  # only a real `true` turns it on


def test_wire_now_dialog_ticks_apply_and_unticked_rows_become_leave_alone(probe: dict) -> None:
    w = probe["wireNow"]
    assert w["first"]["open"] and "EPS Run Multiplier #1" in w["first"]["title"]
    assert w["first"]["items"] == 3
    assert w["first"]["connectText"] == "Connect 3"
    assert w["first"]["heads"][:2] == ["model → 2 inputs", "vae → 1 input"]
    assert w["first"]["heads"][2:] in ([], ["Not connected"])
    assert w["afterUntick"] == "Connect 2"
    assert w["closed"] is True
    assert w["k10"] == {"origin": "1", "slot": 0}
    assert w["k11"] is None  # unticked: NOT connected
    assert w["d13"] == {"origin": "1", "slot": 6}
    assert w["skip"] == ["model|11|model"]  # ...and remembered as "leave alone"
    assert w["wired"] == ["model|10|model", "vae|13|vae"]
    assert any("connected 2" in t for t in w["toasts"])
    assert "2 wired" in w["summaryAfter"]


def test_a_left_alone_row_is_offered_unticked_and_can_be_re_included(probe: dict) -> None:
    r = probe["wireNow"]["reopened"]
    assert any(h.startswith("Left alone (1)") for h in r["heads"])
    assert r["boxChecked"] == [False]  # nothing else to tick; the left-alone row starts unticked
    assert r["connectText"] == "Connect 1"
    assert r["k11"] == {"origin": "1", "slot": 0}
    assert r["skip"] == []


def test_dialog_is_modal_for_the_keyboard_and_closes_every_way(probe: dict) -> None:
    keys = probe["wireNow"]["keys"]
    assert keys["otherStopped"] is True  # bare keys never reach ComfyUI's global shortcuts
    assert keys["stillOpen"] is True
    assert keys["escStopped"] is True and keys["escPrevented"] is True and keys["closed"] is True
    assert probe["wireNow"]["cancel"]["closed"] is True
    assert probe["wireNow"]["backdrop"]["closed"] is True


def test_dialog_with_nothing_to_connect_explains_why(probe: dict) -> None:
    e = probe["empty"]
    assert e["connectText"] == "Nothing to connect" and e["disabled"] is True
    assert any("Nothing to connect" in n for n in e["note"])
    assert any(s.startswith("Optional input") for s in e["summaries"])
    assert any(s.startswith("Already wired") for s in e["summaries"])


def test_popover_is_portaled_to_body_with_toggles_and_gated_outputs(probe: dict) -> None:
    p = probe["popover"]
    assert p["open"] and p["parent"] is True  # document.body, never inside the node subtree
    assert p["left"].endswith("px") and p["top"].endswith("px")  # from getBoundingClientRect
    by_name = {i["name"]: i for i in p["info"]}
    assert [i["name"] for i in p["info"][:9]] == [
        "model",
        "clip",
        "image",
        "text",
        "save_prefix",
        "label",
        "vae",
        "model_low",
        "run_info",
    ]
    for gated in ("image", "text", "label"):
        assert by_name[gated]["disabled"] is True and by_name[gated]["badge"] == "needs setting"
    assert by_name["vae"]["badge"] == "live" and by_name["model_low"]["badge"] == "not live"
    assert p["vaeCfg"] == {"vae": False}
    assert "vae" not in p["summaryAfterVaeOff"] and p["summaryAfterVaeOff"].startswith(
        "model · clip"
    )
    assert p["afterVaeOn"]["property"] is None  # back to the default: no property at all
    assert p["keep"] is True
    assert p["closedByOutside"] is True and p["closedByEsc"] is True
    assert p["gatedWhenOn"] == {"text": False, "image": False, "label": False}
    assert p["wireFromPopover"] == {"popClosed": True, "dialogOpen": True}
    assert p["toggle"] == {"openedOnce": True, "closedBySecondClick": True}


def test_remove_deletes_the_recorded_wires_and_says_so(probe: dict) -> None:
    r = probe["remove"]
    assert r["wired"] == {"origin": "1", "slot": 0} and r["after"] is None
    assert r["property"] is None
    assert any("removed" in t for t in r["toasts"])
    assert any("no broadcast wires" in t for t in r["second"])


def test_keep_wired_wires_only_new_nodes_and_tells_you(probe: dict) -> None:
    k = probe["keep"]["afterAdd"]
    assert k["existing"] is None  # present before keep: Wire now's job
    assert k["k20"] == {"origin": "1", "slot": 0}
    assert k["d21"] == {"origin": "1", "slot": 6}
    assert k["s22"] == {"origin": "1", "slot": 4}
    assert k["recorded"] == ["model|20|model", "save_prefix|22|filename_prefix", "vae|21|vae"]
    assert k["toast"] and "wired" in k["toast"][0]


def test_after_change_events_that_did_not_touch_the_structure_do_no_work(probe: dict) -> None:
    q = probe["keep"]["quiet"]
    assert q["snapshots"] == 0 and q["skipped"] >= 1


def test_a_manual_unplug_becomes_leave_alone_and_is_never_rewired(probe: dict) -> None:
    u = probe["keep"]["unplug"]
    assert u["k20"] is None
    assert u["skip"] == ["model|20|model"]
    assert "model|20|model" not in u["recorded"]
    assert any("leaving 1 alone" in t for t in u["toasts"])


def test_the_users_own_wire_always_wins(probe: dict) -> None:
    k = probe["keep"]
    assert k["own"] == k["ownAfter"] and k["own"]["origin"] == "900"


def test_live_output_tracking_pauses_and_restores_wires(probe: dict) -> None:
    p = probe["keep"]["paused"]
    assert p["d21"] is None
    assert any("paused 1 vae" in t for t in p["toasts"])
    assert p["withdrawn"] == ["vae|21|vae"]
    assert p["skipNoVae"] is True  # our OWN disconnects never count as "leave alone"
    r = probe["keep"]["restored"]
    assert r["d21"] == {"origin": "1", "slot": 6}
    assert any("restored 1 vae" in t for t in r["toasts"]) and r["withdrawn"] == 0


def test_a_reload_re_baselines_and_never_rewires(probe: dict) -> None:
    assert probe["keep"]["reload"] == {"k30": None}


def test_keep_off_and_mid_configure_never_wire(probe: dict) -> None:
    assert probe["keep"]["off"] == {"k31": None}
    assert probe["keep"]["configuring"] == {"k32": None}


def test_stale_records_on_a_pasted_multiplier_are_dropped_not_acted_on(probe: dict) -> None:
    p = probe["paste"]
    assert p["wired"] == [] and p["skip"] == ["model|77|model"]
    assert p["k10"] is None  # loading / pasting never rewires
    assert not any("leaving" in t for t in p["toasts"])  # load semantics: dropped quietly


def test_keep_wires_a_fresh_instance_through_an_existing_subgraph_input_only(probe: dict) -> None:
    k = probe["keepSub"]
    assert k["inst1"] is None  # the pre-existing instance is Wire now's job
    assert k["inst2"] == {"origin": "1", "slot": 0}  # the FRESH instance is wired
    # a new node INSIDE a definition would need a new subgraph input: never automatic
    assert k["innerNew"] is None and k["defInputs"] == 0
    assert any("inside a subgraph" in t for t in k["toasts"])


def test_pure_ui_helpers(probe: dict) -> None:
    u = probe["ui"]
    groups = u["groupSkips"]
    assert [n["code"] for n in groups["notes"]] == ["output-dead", "wan-unresolved"]
    assert [s["key"] for s in groups["leftAlone"]] == ["k"]
    assert [(g["code"], len(g["items"])) for g in groups["groups"]] == [
        ("loop", 1),
        ("already-wired", 2),
    ]
    s = u["selection"]
    assert [p["key"] for p in s["toApply"]] == [
        "a",
        "c",
        "e",
    ]  # ticked + a re-included left-alone row
    assert s["leaveAlone"] == ["b"] and s["include"] == ["e"]
    assert [g["output"] for g in u["byOutput"]] == ["model", "vae"]
    assert [len(g["items"]) for g in u["byOutput"]] == [1, 2]
    assert u["rowSummary"] == ["model · clip", "nothing live", "vae · 3 wired · 1 paused · keep ✓"]
    assert u["toastText"] == "KSampler #1 (model, vae); Save #2 (save_prefix); X #3 (clip); +1 more"


def test_exports_for_the_rendering_stage(probe: dict) -> None:
    for name in (
        "broadcastLinkIndex",
        "isBroadcastLink",
        "bumpBroadcastEpoch",
        "attach",
        "init",
        "getNodeMenuItems",
        "installLegacyMenuFallback",
        "ensureWatch",
        "SETTINGS",
        "BROADCAST_SETTING_ID",
        "UE_REJECTED_INPUTS",
        "wireNow",
        "removeWires",
        # tucked wires + reach (the stage after v1.3.0)
        "broadcastLinkOwner",
        "getShowAllWires",
        "setShowAllWires",
        "setLook",
        "setScope",
        "getCanvasMenuItems",
        "installLegacyCanvasMenuFallback",
        "setup",
        "afterConfigure",
    ):
        assert name in probe["exports"], name


def test_popover_has_the_wires_and_reach_choices(probe: dict) -> None:
    f = probe["lookScope"]["field"]
    assert f["count"] == 2 and f["labels"] == ["Wires", "Reach"]
    assert f["looks"] == [["tucked", "Tucked (default)"], ["dim", "Dim"], ["normal", "Normal"]]
    assert f["scopes"] == [["graph", "Whole workflow (default)"], ["group", "Only my group"]]
    assert f["look"] == "tucked" and f["scope"] == "graph"  # both start at the stored default
    assert all(f["aria"])
    assert f["checks"] == 10  # the nine output checkboxes + Keep wired: untouched by the new fields


def test_choosing_a_look_is_stored_and_reaches_the_renderers_cached_owner(probe: dict) -> None:
    """The link index caches each wire's owner AND its look, so setLook must
    bump the epoch -- without it the old look would keep drawing."""
    ls = probe["lookScope"]
    assert ls["before"] == {"ownerId": "1", "ownerGraph": "root", "look": "tucked", "linkGraph": "root"}
    assert ls["dim"] == {"look": "dim", "owner": "dim"}
    assert ls["normal"] == {"look": "normal", "owner": "normal"}
    assert ls["tucked"]["look"] == "tucked" and ls["tucked"]["owner"] == "tucked"
    # a value that is not a look / scope is refused, never stored
    assert ls["junk"] == {"look": "tucked", "scope": "graph"}


def test_reach_group_toasts_when_nothing_is_in_reach_and_names_the_group_otherwise(
    probe: dict,
) -> None:
    ls = probe["lookScope"]
    assert ls["noGroup"]["scope"] == "group"
    assert ls["noGroup"]["toasts"] == ["EPS Run Multiplier: not inside a group"]
    assert ls["noGroup"]["severity"] == ["warn"]
    assert ls["noGroup"]["summary"].endswith("group only")  # the row shows the config
    assert ls["inGroup"]["toasts"] == ["EPS Run Multiplier: reach is “only my group”"]
    assert "“Pipeline A”" in ls["inGroup"]["detail"] and "Wires already made stay" in ls["inGroup"]["detail"]
    assert ls["back"]["scope"] == "graph"
    # all-default again (no wires, tucked, whole workflow) -> no property at all
    assert ls["clean"]["property"] is None


def test_the_canvas_menu_item_is_only_offered_when_there_is_something_to_show(
    probe: dict,
) -> None:
    c = probe["canvasMenu"]
    assert c["emptyMenu"] == []  # no broadcast wires: every other canvas menu stays clean
    assert c["off"] == {
        "count": 1,
        "content": "Broadcast: show all wires",
        "cb": "function",
        "on": False,
    }
    assert c["on"] == {"on": True, "content": "Broadcast: show all wires ✓"}
    assert c["offAgain"] == {"on": False, "content": "Broadcast: show all wires"}
    # ON stays offered even with no wires left, so it can always be turned off
    assert c["stuck"] == ["Broadcast: show all wires ✓"]
    assert c["none"] == 0 and c["noRoot"] == 0


def test_legacy_canvas_menu_fallback_only_on_frontends_without_the_hook(probe: dict) -> None:
    lc = probe["legacyCanvas"]
    assert lc["first"] is True and lc["second"] is True  # idempotent
    assert lc["withWires"] == ["orig", "---", "Broadcast: show all wires"]
    assert lc["without"] == ["orig"]
    # the modern frontend invokes BOTH getCanvasMenuItems and the legacy wrapper: never both
    assert lc["modern"] is False and lc["modernUntouched"] is True
    assert lc["noMethod"] is False and lc["noClass"] is False


def test_setup_and_init_install_the_drawing_hooks_idempotently(probe: dict) -> None:
    s = probe["setup"]
    assert s["renderLink"] is True and s["stable"] is True
    assert s["select"] is True and s["deselectAll"] is True
    assert s["canvasMenu"] is True
    assert s["originalStillReturns"] == "orig"


def test_wire_now_preview_names_the_group_and_explains_an_empty_reach(probe: dict) -> None:
    r = probe["reachDialog"]
    ing = r["inGroup"]
    assert ing["notes"][0] == "Reach: only my group (“Pipeline A”) — inputs outside it are not considered."
    assert ing["rows"] == ["model → KSampler #10 (model)"]  # the outside sampler is not even listed
    out = r["outside"]
    assert out["connectText"] == "Nothing to connect" and out["rows"] == 0
    assert any("not inside a group" in n for n in out["notes"])
    # Keep wired under Reach feeds a new node in the group and leaves the outside one alone
    assert r["keep"]["kIn"] == {"origin": "1", "slot": 0} and r["keep"]["kOut"] is None


def test_a_reload_onto_the_same_root_graph_drops_the_cached_link_index(probe: dict) -> None:
    """Link ids restart at 1 in a new workflow and ComfyUI re-configures the
    SAME root graph object: a stale cached "link N is a broadcast wire" would
    tuck an innocent wire, so afterConfigureGraph invalidates the cache."""
    a = probe["afterConfigure"]
    assert a["before"] == "1"
    assert a["stale"] == "1"  # why the hook exists: nothing else told the cache
    assert a["after"] is None
    assert a["repaint"] == [[True, True]]


def test_new_pure_ui_helpers(probe: dict) -> None:
    u = probe["ui2"]
    assert u["rowGroup"] == "vae · 2 wired · group only" and u["rowGraph"] == "vae · 2 wired"
    assert u["reach"] == [
        "Reach: only my group (“A”, “untitled group”) — inputs outside it are not considered.",
        "",
        "",
        "",
    ]
    assert [n["code"] for n in u["noGroupNote"]["notes"]] == ["no-group"]
    assert [g["code"] for g in u["noGroupNote"]["groups"]] == ["scope-partial"]


# --------------------------------------------------------- source structure


def test_one_shot_graph_flags_are_gone_hooks_are_stored_and_re_verified(source: str) -> None:
    """cross_sweep.js's v0.68.1 lesson: core RESTORES graph.onNodeAdded/
    onNodeRemoved on every subgraph enter/exit and drops a later wrapper, so a
    boolean "already installed" flag goes deaf. Store each wrapper and
    re-verify it on every call; adopt a surviving older wrapper of ours."""
    # v1.3.0 merge with the v1.2.0 nested-reach round: the stored-and-
    # re-verified installer is the SHARED api.watchGraphHooks (sibling-key
    # aware, so this watch and the readout's never stack layers on each
    # other); its own semantics are pinned in tests/test_nested_reach_js.py.
    body = source[source.index("function installWatchOn(graph)") :]
    body = body[: body.index("\n}\n")]
    assert "watchGraphHooks(graph, WATCH_KEY, WATCH_HOOKS, onWatchedGraphEvent)" in body
    assert "const WATCH_KEY = '__epsBcNodeWatch'" in source
    assert "watchGraphHooks" in source.split("from '../lora_library/api.js'")[0]
    assert re.search(r"graph\.__epsBc\w* = true", source) is None, "no one-shot boolean flag"
    assert "WATCH_HOOKS = ['onNodeAdded', 'onNodeRemoved', 'onAfterChange']" in source
    # every graph, via the shared walker
    assert "walkGraphs(root)" in source


def test_no_polling_and_one_coalescing_debounce(source: str, ui_source: str) -> None:
    for text in (source, ui_source):
        assert "setInterval" not in text
        assert "window.addEventListener" not in text
    assert source.count("setTimeout(") == 1
    assert "const PASS_DEBOUNCE_MS = 150" in source
    body = source[source.index("export function schedulePass()") :]
    body = body[: body.index("\n}\n")]
    assert "if (passTimer !== null) return" in body, "coalesced"


def test_the_canvas_listener_is_one_capture_phase_document_listener(source: str) -> None:
    assert source.count("document.addEventListener(") == 1
    init = source[source.index("export function init()") :]
    init = init[: init.index("\n}\n")]
    assert "'litegraph:canvas'" in init and "'after-change'" in init
    assert "true\n  )" in init, "capture phase"


def test_own_changes_are_guarded_so_they_never_look_like_manual_edits(source: str) -> None:
    assert "isApplying()" in source
    # connection hook + canvas listener test `!isApplying()`; the graph-hook
    # event handler (v1.3.0, via api.watchGraphHooks) returns early on it.
    assert source.count("!isApplying()") >= 2
    handler = source[source.index("function onWatchedGraphEvent(") :]
    handler = handler[: handler.index("\n}\n")]
    assert "if (isApplying()) return" in handler


def test_keep_only_wires_new_nodes_and_never_adds_subgraph_inputs_automatically(
    source: str,
) -> None:
    body = source[source.index("function processMultiplier(node)") :]
    body = body[: body.index("\n}\n")]
    assert "state.known.has(path)" in body
    assert "p.kind === KINDS.DIRECT || p.kind === KINDS.EXISTING" in body
    assert "p.kind === KINDS.NEW" in body  # reported, never applied
    # first pass after a rebuild only baselines
    assert body.index("if (first) {") < body.index("applyProposals(")


def test_the_menu_uses_get_node_menu_items_with_a_legacy_fallback(source: str) -> None:
    entry = ENTRY_JS.read_text(encoding="utf-8")
    assert "getNodeMenuItems(node) {" in entry
    assert "broadcast.getNodeMenuItems(node)" in entry
    assert "beforeRegisterNodeDef(nodeType, nodeData) {" in entry
    assert "broadcast.installLegacyMenuFallback(nodeType, nodeData)" in entry
    assert "settings: broadcast.SETTINGS" in entry
    assert "safely('broadcast.init', () => broadcast.init?.())" in entry
    assert "typeof app.collectNodeMenuItems === 'function'" in source
    # nothing depends on right-click > Properties
    assert "properties_info" not in source and "addProperty" not in source


def test_no_new_serialized_widget_the_row_lives_in_the_readout_element(
    source: str, ui_source: str, sweep_source: str
) -> None:
    for text in (source, ui_source):
        assert (
            "addDOMWidget" not in text and "addWidget" not in text and "addCustomWidget" not in text
        )
    attach = sweep_source[sweep_source.index("export function attach(node)") :]
    assert attach.count("node.addDOMWidget(") == 1
    assert "broadcast.attach(node, state)" in attach
    # records live on node.properties, only on the multiplier
    graph = (WEB / "eps_image" / "broadcast_graph.js").read_text(encoding="utf-8")
    assert "node.properties[PROPERTY_KEY] = serial" in graph
    assert "hideInPanel: true" in sweep_source


def test_nodes_2_0_rules_no_canvas_drawing_no_node_mouse_hooks(source: str, ui_source: str) -> None:
    for text in (source, ui_source):
        for banned in (
            "onDrawForeground",
            "onDrawBackground",
            "onMouseDown",
            "onMouseEnter",
            "getContext('2d')",
            "ctx.",
        ):
            assert banned not in text, banned
    # popover + dialog portal to <body> and use getBoundingClientRect
    assert "document.body.appendChild(pop)" in ui_source
    assert "document.body.appendChild(overlay)" in ui_source
    assert "getBoundingClientRect()" in ui_source
    assert "data-capture-wheel" in ui_source
    assert "document.addEventListener('keydown', handler, true)" in ui_source
    assert "position: fixed" in ui_source and ".eps-bc-pop {" in ui_source


def test_tucked_wires_wiring_is_idempotent_feature_detected_and_nodes_2_0_safe(
    source: str, ui_source: str
) -> None:
    entry = ENTRY_JS.read_text(encoding="utf-8")
    # the CANVAS menu goes through the declarative hook, with the legacy fallback beside it
    assert "getCanvasMenuItems(canvas) {" in entry and "broadcast.getCanvasMenuItems(canvas)" in entry
    assert "typeof app.collectCanvasMenuItems === 'function'" in source
    assert "LEGACY_CANVAS_PATCH_FLAG" in source and "proto.getCanvasMenuOptions" in source
    assert "setup() {" in entry and "broadcast.setup" in entry
    assert "afterConfigureGraph() {" in entry and "broadcast.afterConfigure" in entry
    # the drawing lives in its own module, imported and installed from here
    assert "from './broadcast_draw.js'" in source
    assert source.count("ensureDrawHooks()") >= 4  # init, setup, attach, every pass
    # the look change invalidates the cached owner record
    body = source[source.index("export function setLook(") :]
    body = body[: body.index("\n}\n")]
    assert body.index("writeConfig(node, cfg)") < body.index("bumpBroadcastEpoch()")
    # both new choices are plain DOM selects portaled with the popover; no node mouse hooks
    assert "choiceField('Wires'" in ui_source and "choiceField('Reach'" in ui_source
    assert "'select'" in ui_source


def test_ids_and_class_ids_match_the_backend() -> None:
    from eps_image.nodes_cross_sweep import EPSCrossSweep

    assert EPSCrossSweep.__name__ == "EPSCrossSweep"
    # the node DESCRIPTION carries the one-sentence pointer to broadcast
    assert "Broadcast" in EPSCrossSweep.DESCRIPTION
    assert "real" in EPSCrossSweep.DESCRIPTION and "undo" in EPSCrossSweep.DESCRIPTION
    plan = (WEB / "eps_image" / "broadcast_plan.js").read_text(encoding="utf-8")
    assert "export const MULTIPLIER_CLASS_ID = 'EPSCrossSweep'" in plan
    assert "export const PROPERTY_KEY = 'Broadcast'" in plan
