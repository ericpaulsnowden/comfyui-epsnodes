/**
 * @file EPS Group Bypass frontend (FORMAT.md section 6.20). Exports the
 * standard `init()`/`attach(node)` pair; `attach` is a no-op for every node
 * type other than `EPSGroupBypass`.
 *
 * The backend (`nodes_group_bypass.py`) never runs -- ComfyUI fixes modes
 * before a run starts, so a group can only be switched here, on the canvas.
 * This file:
 *
 * - fills the `group` combo from the node's own graph's groups (the option
 *   list is a FUNCTION, resolution.js's `ratio` precedent, so it is always
 *   current and a stale value is never flagged), and keeps the hidden
 *   `group_id` pointing at the chosen group's litegraph id -- the id is the
 *   real target, so renaming the group or giving two groups one title never
 *   retargets the switch. A duplicate title is shown as `Title [#id]`.
 *
 * - works out the WANTED mode: `enabled` on -> ALWAYS; off -> BYPASS, or
 *   NEVER (muted) with `mute` on. Either switch may be driven by a wire into
 *   its own widget socket: the value is read off the canvas at the other end
 *   -- an EPS Number Controller row (`values[num_N].value`, non-zero = on,
 *   the backend's own `value != 0` rule), or any node with a `value` widget
 *   (core's Boolean primitive, a legacy PrimitiveNode), through Reroute
 *   nodes and subgraph boundaries (`resolveSourcesAt`). Anything else
 *   (a value only a run would produce) cannot be read in advance: the
 *   switch's label says `?` and the group is left as it is.
 *
 * - applies the wanted mode to every node inside the group ONLY WHEN IT
 *   CHANGES (or the target group changes: the old group is put back on
 *   first). It never re-asserts: a node you set by hand inside the group
 *   keeps your setting until the switch next moves. A load, paste, undo or
 *   tab switch ADOPTS the current wanted state without writing anything --
 *   the saved modes are already right, and the driver may not have restored
 *   its own value yet when this node's `onConfigure` runs.
 *
 * - never switches off itself or the node(s) driving it, even when they sit
 *   inside the group (otherwise the switch could never be turned back on).
 *
 * One shared 200 ms poll checks every attached node. It covers every way a
 * driver can change (typing in a Number Controller row, a Universal State
 * apply, a primitive toggle) without hooking each of them, and it writes
 * nothing unless the wanted state moved.
 *
 * Known limit, stated: a node dragged INTO the group while it is switched
 * off is not switched off until the switch next changes (rgthree's Fast
 * Groups Bypasser behaves the same way). Group membership is litegraph's
 * own rule (`recomputeInsideNodes`: the node's centre is inside the group).
 */

import { app } from '../../../scripts/app.js'
import { resolveInputSources, resolveSourcesAt, rootGraphOf } from '../lora_library/api.js'
import { hideValuesWidget, parseValues, slotName } from './number_controller.js'
import { captureUndoState } from './button_row.js'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** FORMAT.md section 6.20 -- frozen once shipped (section 8). */
export const CLASS_ID = 'EPSGroupBypass'
const PREFIX = '[eps_image:group_bypass]'

export const ENABLED_WIDGET_NAME = 'enabled'
export const MUTE_WIDGET_NAME = 'mute'
export const GROUP_WIDGET_NAME = 'group'
export const GROUP_ID_WIDGET_NAME = 'group_id'

/** nodes_group_bypass.py `NO_GROUP`. */
export const NO_GROUP = '(none)'

/** litegraph `LGraphEventMode`: ALWAYS (normal), NEVER (muted), BYPASS. */
export const MODE_ALWAYS = 0
export const MODE_NEVER = 2
export const MODE_BYPASS = 4

const NUMBER_CONTROLLER_CLASS = 'EPSNumberController'
const NC_OUTPUT_RE = /^num_(\d+)$/
const POLL_MS = 200

/** Every attached node's state; the poll walks it and drops removed nodes. */
const states = new Set()
const attachedNodes = new WeakSet()
let pollTimer = null

// ---------------------------------------------------------------------------
// Pure helpers -- exported so tests/test_group_bypass_js.py can drive them.
// ---------------------------------------------------------------------------

/** The mode the group's nodes should have. A `null` (unreadable) switch
 * gives `null` -- leave the group alone -- except that `mute` does not
 * matter while the group is on. */
export function wantedMode(enabled, mute) {
  if (enabled === true) return MODE_ALWAYS
  if (enabled === null || mute === null) return null
  return mute ? MODE_NEVER : MODE_BYPASS
}

/** A driven value as a boolean, or `null` when it is not one: booleans as
 * they are, finite numbers by the backend's `value != 0` rule, and the
 * words the Number Controller's own box accepts. */
export function coerceBoolean(value) {
  if (typeof value === 'boolean') return value
  if (typeof value === 'number') return Number.isFinite(value) ? value !== 0 : null
  if (typeof value === 'string') {
    const text = value.trim().toLowerCase()
    if (['true', 'on', 'yes'].includes(text)) return true
    if (['false', 'off', 'no'].includes(text)) return false
    if (text === '') return null
    const number = Number(text)
    return Number.isFinite(number) ? number !== 0 : null
  }
  return null
}

/** One Number Controller row's value as a boolean, from the raw `values`
 * widget string and the output's NAME (`num_N`); `null` if unreadable. */
export function numberControllerRowValue(rawValues, outputName) {
  const match = typeof outputName === 'string' ? NC_OUTPUT_RE.exec(outputName) : null
  if (!match) return null
  const entry = parseValues(rawValues)[slotName(Number(match[1]))]
  return entry ? coerceBoolean(entry.value) : null
}

/** `[{id, label}]` for a list of groups, in their order, with a duplicated
 * title shown as `Title [#id]` so every label is unique. */
export function groupOptions(groups) {
  const list = (groups || []).filter((g) => g && g.id != null)
  const counts = new Map()
  for (const g of list) {
    const title = String(g.title ?? '')
    counts.set(title, (counts.get(title) || 0) + 1)
  }
  return list.map((g) => {
    const title = String(g.title ?? '')
    const label = counts.get(title) > 1 || title === '' || title === NO_GROUP ? `${title} [#${g.id}]` : title
    return { id: String(g.id), label }
  })
}

/** The group the node targets: by stored id first, else by label (a
 * workflow saved without an id, a hand edit). `null` if neither matches. */
export function findTargetGroup(groups, groupId, label) {
  const list = groups || []
  if (groupId !== '' && groupId != null) {
    const byId = list.find((g) => g && String(g.id) === String(groupId))
    if (byId) return byId
  }
  if (!label || label === NO_GROUP) return null
  const option = groupOptions(list).find((o) => o.label === label)
  return option ? list.find((g) => String(g.id) === option.id) || null : null
}

/** The label to show on a driven switch: `enabled ← on`, `? ` when the
 * driver cannot be read in advance. */
export function drivenLabel(name, value) {
  if (value === null) return `${name} ← ? (not readable before the run)`
  return `${name} ← ${value ? 'on' : 'off'}`
}

// ---------------------------------------------------------------------------
// Graph access
// ---------------------------------------------------------------------------

function nodeClassOf(node) {
  if (!node) return null
  if (node.comfyClass) return node.comfyClass
  if (node.constructor && node.constructor.comfyClass) return node.constructor.comfyClass
  return null
}

function findWidget(node, name) {
  return node.widgets?.find((w) => w && w.name === name)
}

function groupsOf(graph) {
  return graph?.groups || graph?._groups || []
}

/** The input slot index of *widgetName*'s own socket, or -1. */
function widgetInputIndex(node, widgetName) {
  return (node.inputs || []).findIndex((input) => input?.widget?.name === widgetName)
}

/** The real source(s) feeding a widget's socket, through Reroute nodes. */
function widgetSources(node, widgetName) {
  const index = widgetInputIndex(node, widgetName)
  if (index < 0 || node.inputs[index].link == null) return null
  const rootGraph = rootGraphOf(node.graph)
  let sources = resolveInputSources(rootGraph, node, index)
  for (let hop = 0; hop < 16; hop++) {
    const reroute = sources.find((s) => nodeClassOf(s.node) === 'Reroute' || s.node?.type === 'Reroute')
    if (!reroute) break
    sources = sources.filter((s) => s !== reroute).concat(resolveSourcesAt(rootGraph, reroute, 0))
  }
  return sources
}

/** One source's value as a boolean, or `null` if it can't be read now. */
function readSource(source) {
  const origin = source?.node
  if (!origin) return null
  if (nodeClassOf(origin) === NUMBER_CONTROLLER_CLASS) {
    return numberControllerRowValue(findWidget(origin, 'values')?.value, origin.outputs?.[source.slot]?.name)
  }
  const valueWidget = findWidget(origin, 'value') || (origin.widgets?.length === 1 ? origin.widgets[0] : null)
  return valueWidget ? coerceBoolean(valueWidget.value) : null
}

/** `{value, driven, sources}` for one switch: its own widget value, or the
 * value at the other end of the wire into it. */
function readSwitch(node, widgetName) {
  const widget = findWidget(node, widgetName)
  const sources = widgetSources(node, widgetName)
  if (!sources) return { value: coerceBoolean(widget?.value) ?? false, driven: false, sources: [] }
  const values = sources.map(readSource)
  const value = values.length && values.every((v) => v !== null && v === values[0]) ? values[0] : null
  return { value, driven: true, sources }
}

/** Show a driven switch's incoming value in its label; put it back when
 * the wire is gone. The label is display-only (never serialised). */
function showDriven(node, widget, reading) {
  const label = reading.driven ? drivenLabel(widget.name, reading.value) : undefined
  if (widget.label !== label) {
    widget.label = label
    node.setDirtyCanvas?.(true, true)
  }
  if (reading.driven && reading.value !== null && widget.value !== reading.value) widget.value = reading.value
}

// ---------------------------------------------------------------------------
// Applying
// ---------------------------------------------------------------------------

/** Set *mode* on every node inside *group* except the ones in *keep*.
 * Returns how many nodes changed. */
function setGroupMode(group, mode, keep) {
  if (!group) return 0
  try {
    group.recomputeInsideNodes?.()
  } catch (error) {
    console.warn(PREFIX, 'could not read the group members', error)
  }
  let changed = 0
  for (const member of group.nodes || []) {
    if (!member || keep.has(member) || member.mode === mode) continue
    member.mode = mode
    changed++
  }
  return changed
}

function groupKey(group) {
  return group ? String(group.id) : ''
}

/** Re-read everything for one node and apply when the wanted state moved. */
function tick(state) {
  const { node } = state
  const graph = node.graph
  if (!graph) return
  const enabled = readSwitch(node, ENABLED_WIDGET_NAME)
  const mute = readSwitch(node, MUTE_WIDGET_NAME)
  showDriven(node, state.enabledWidget, enabled)
  showDriven(node, state.muteWidget, mute)

  const groups = groupsOf(graph)
  const group = findTargetGroup(groups, state.groupIdWidget.value, state.groupWidget.value)
  syncGroupWidgets(state, groups, group)
  const mode = wantedMode(enabled.value, mute.value)
  if (mode === null) return
  const key = `${groupKey(group)}|${mode}`

  // Loaded, pasted, undone: adopt what is there without writing.
  if (state.last === undefined) {
    state.last = { key, group, mode }
    return
  }
  if (state.last.key === key) return

  const keep = new Set([node, ...enabled.sources.map((s) => s.node), ...mute.sources.map((s) => s.node)])
  let changed = 0
  if (state.last.group && state.last.group !== group && state.last.mode !== MODE_ALWAYS && groups.includes(state.last.group)) {
    changed += setGroupMode(state.last.group, MODE_ALWAYS, keep)
  }
  changed += setGroupMode(group, mode, keep)
  state.last = { key, group, mode }
  if (changed) {
    graph.setDirtyCanvas?.(true, true)
    captureUndoState()
  }
}

/** Keep the combo label and the hidden id in step with the live group:
 * a renamed group updates the label, a label-only match records the id. */
function syncGroupWidgets(state, groups, group) {
  if (!group) return
  const option = groupOptions(groups).find((o) => o.id === String(group.id))
  if (option && state.groupWidget.value !== option.label) state.groupWidget.value = option.label
  if (String(state.groupIdWidget.value) !== String(group.id)) state.groupIdWidget.value = String(group.id)
}

/** Re-read every attached node now (the poll calls this; so do the
 * switches, so a click answers at once). Exported for tests. */
export function refreshAll() {
  if (app.configuringGraph) return
  for (const state of states) {
    if (!state.node.graph) {
      if (state.removed) states.delete(state)
      continue
    }
    state.removed = false
    try {
      tick(state)
    } catch (error) {
      console.warn(PREFIX, 'update failed', error)
    }
  }
  if (!states.size && pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function ensurePoll() {
  if (!pollTimer) pollTimer = setInterval(refreshAll, POLL_MS)
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

function wireGroupCombo(state) {
  const { node, groupWidget, groupIdWidget } = state
  groupWidget.options = groupWidget.options || {}
  groupWidget.options.values = () => [NO_GROUP, ...groupOptions(groupsOf(node.graph)).map((o) => o.label)]
  const originalCallback = groupWidget.callback
  groupWidget.callback = function (value) {
    const result = originalCallback?.apply(this, arguments)
    const option = groupOptions(groupsOf(node.graph)).find((o) => o.label === value)
    groupIdWidget.value = option ? option.id : ''
    refreshAll()
    return result
  }
}

/** Run the poll right away after a click, so the group answers instantly. */
function wireSwitch(widget) {
  const originalCallback = widget.callback
  widget.callback = function () {
    const result = originalCallback?.apply(this, arguments)
    setTimeout(refreshAll, 0)
    return result
  }
}

export function init() {}

export function attach(node) {
  try {
    if (!node || nodeClassOf(node) !== CLASS_ID) return
    if (attachedNodes.has(node)) return
    const enabledWidget = findWidget(node, ENABLED_WIDGET_NAME)
    const muteWidget = findWidget(node, MUTE_WIDGET_NAME)
    const groupWidget = findWidget(node, GROUP_WIDGET_NAME)
    const groupIdWidget = findWidget(node, GROUP_ID_WIDGET_NAME)
    if (!enabledWidget || !muteWidget || !groupWidget || !groupIdWidget) {
      console.warn(PREFIX, 'EPSGroupBypass node is missing a widget; not attached')
      return
    }
    attachedNodes.add(node)
    hideValuesWidget(node, groupIdWidget)

    const state = { node, enabledWidget, muteWidget, groupWidget, groupIdWidget, last: undefined, removed: false }
    wireGroupCombo(state)
    wireSwitch(enabledWidget)
    wireSwitch(muteWidget)

    const originalOnConfigure = node.onConfigure
    node.onConfigure = function () {
      const result = originalOnConfigure?.apply(this, arguments)
      state.last = undefined
      return result
    }
    const originalOnRemoved = node.onRemoved
    node.onRemoved = function () {
      // Dropped by the next poll unless it is re-added (moved into a
      // subgraph) first.
      state.removed = true
      return originalOnRemoved?.apply(this, arguments)
    }

    states.add(state)
    ensurePoll()
  } catch (error) {
    console.warn(PREFIX, 'attach failed', error)
  }
}
