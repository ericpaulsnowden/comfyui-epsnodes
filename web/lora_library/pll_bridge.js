/**
 * @file Power Lora Loader bridge for the EPS LoRA Picker's Send-to-loader
 * row: adapted from controller.js's §6.3 technique;
 * deliberately duplicated, not imported — see FORMAT.md §6.13 M2
 * (controller.js is owner-validated code, and the duplication is the
 * cheaper risk).
 * Probe-first feature detection, grow via rgthree's own
 * addNewLoraWidget(), shrink from the tail via removeWidget() with the
 * splice fallback, whole-object `.value` assignment — controller.js's
 * probeTarget()/applySetToTarget(), minimally re-scoped to the picker's
 * `{file, on, strength, strength_clip?}` row shape. The MSG_* strings
 * below are byte-identical copies of controller.js's §6.3 message
 * vocabulary, kept in sync by hand (tests/test_pll_bridge_js.py pins them
 * against BOTH files).
 */

import { app } from '../../../scripts/app.js'

/** Exact rgthree type/title/comfyClass string — constants.js addRgthree("Power Lora Loader"). */
export const POWER_LORA_LOADER_TYPE = 'Power Lora Loader (rgthree)'
/** Per-node property (not per-row) that picks single vs dual strength mode. */
const PROP_SHOW_STRENGTHS = 'Show Strengths'
const PROP_SHOW_STRENGTHS_DUAL = 'Separate Model & Clip'
/** rgthree row widgets are named lora_<counter>; counter never reuses numbers. */
const LORA_ROW_NAME_RE = /^lora_\d+$/
/** Belt-and-suspenders cap so a future rgthree shape change can never spin us forever. */
const MAX_ROW_ADJUST_STEPS = 500

const MSG_NO_RGTHREE = 'Install rgthree-comfy, or use EPS Apply LoRA Set instead'
const MSG_SHAPE_DRIFT = 'Power Lora Loader internals changed — controller disabled (v-check)'
const MSG_NO_TARGET_IN_GRAPH =
  'No Power Lora Loader (rgthree) or EPS LoRA Picker node in this workflow yet — add one, then pick it above.'
const MSG_NO_TARGET_SELECTED = 'Pick a target loader node above.'

/** Every live `Power Lora Loader (rgthree)` instance in the current graph,
 * ascending node id — §6.13 M2's target-combo order. */
export function findPllNodes() {
  const nodes = app.graph?._nodes || app.graph?.nodes || []
  return nodes
    .filter((node) => node && node.type === POWER_LORA_LOADER_TYPE)
    .sort((a, b) => a.id - b.id)
}

/** rgthree registers the PLL type with LiteGraph iff it's installed and loaded. */
function isRgthreeInstalled() {
  return (
    typeof LiteGraph !== 'undefined' &&
    !!(LiteGraph.registered_node_types && LiteGraph.registered_node_types[POWER_LORA_LOADER_TYPE])
  )
}

/**
 * Scan a target's widgets for lora rows — controller.js's scanLoraRows():
 * `named` = everything row-shaped by name, `rows` = the subset whose
 * `.value` looks like rgthree's row object. `named.length !== rows.length`
 * is the shape-drift signal; zero rows is a normal, healthy "empty PLL",
 * not drift.
 */
function scanLoraRows(node) {
  const named = []
  const rows = []
  const widgets = (node && node.widgets) || []
  for (const widget of widgets) {
    if (!widget || typeof widget.name !== 'string' || !LORA_ROW_NAME_RE.test(widget.name)) continue
    named.push(widget)
    const v = widget.value
    if (v && typeof v === 'object' && 'lora' in v) rows.push(widget)
  }
  return { named, rows }
}

/**
 * Single feature-detection gate every rgthree interaction goes through —
 * controller.js's probeTarget() shape (§6.3: "probe first, mutate after";
 * feature detection, not version pinning). Only reads; same codes, same
 * message texts.
 */
export function probePll(node) {
  if (!isRgthreeInstalled()) {
    return { ok: false, code: 'no-rgthree', message: MSG_NO_RGTHREE }
  }
  if (!node) {
    const hasAny = findPllNodes().length > 0
    return {
      ok: false,
      code: hasAny ? 'no-target-selected' : 'no-target-in-graph',
      message: hasAny ? MSG_NO_TARGET_SELECTED : MSG_NO_TARGET_IN_GRAPH
    }
  }
  if (node.type !== POWER_LORA_LOADER_TYPE) {
    return { ok: false, code: 'wrong-type', message: MSG_NO_TARGET_SELECTED }
  }
  if (
    typeof node.addNewLoraWidget !== 'function' ||
    typeof node.removeWidget !== 'function' ||
    typeof node.computeSize !== 'function' ||
    !Array.isArray(node.widgets)
  ) {
    return { ok: false, code: 'shape-drift', message: MSG_SHAPE_DRIFT }
  }
  const { named, rows } = scanLoraRows(node)
  if (named.length !== rows.length) {
    return { ok: false, code: 'shape-drift', message: MSG_SHAPE_DRIFT }
  }
  return {
    ok: true,
    code: 'ok',
    message: `Ready — target has ${rows.length} row${rows.length === 1 ? '' : 's'}.`,
    rowCount: rows.length
  }
}

/**
 * Picker selection rows -> rgthree row `.value` objects — the pure half of
 * `writeRowsToPll`, exported so tests can drive the mapping directly. Same
 * per-row shape as controller.js's applySetToTarget() assignment:
 * `strengthTwo` carries `strength_clip` (falling back to `strength`) only
 * in dual mode, `null` otherwise.
 */
export function rowsForPll(rows, dualMode) {
  return (Array.isArray(rows) ? rows : []).map((raw) => {
    const row = raw || {}
    return {
      on: row.on ?? true,
      lora: row.file,
      strength: row.strength ?? 1,
      strengthTwo: dualMode ? (row.strength_clip ?? row.strength ?? 1) : null
    }
  })
}

/**
 * Write the picker's selection rows into a probed-ok PLL — controller.js's
 * applySetToTarget() step for step: grow, shrink from the tail,
 * whole-object assignment, then the resize/redraw block.
 */
export function writeRowsToPll(node, rows) {
  const dualMode = !!(node.properties && node.properties[PROP_SHOW_STRENGTHS] === PROP_SHOW_STRENGTHS_DUAL)
  const values = rowsForPll(rows, dualMode)

  let { rows: current } = scanLoraRows(node)
  let steps = 0
  // Grow: rgthree's own addNewLoraWidget() both creates the widget AND
  // repositions it just before the "+ Add Lora" button spacer.
  while (current.length < values.length && steps++ < MAX_ROW_ADJUST_STEPS) {
    node.addNewLoraWidget()
    current = scanLoraRows(node).rows
  }
  // Shrink: remove extras from the tail via the official LGraphNode API,
  // which takes a WIDGET REFERENCE (not an index). Every remaining row's
  // `.value` gets fully overwritten below, so tail-removal is simplest.
  steps = 0
  while (current.length > values.length && steps++ < MAX_ROW_ADJUST_STEPS) {
    const widget = current.pop()
    try {
      node.removeWidget(widget)
    } catch (error) {
      const idx = node.widgets.indexOf(widget)
      if (idx !== -1) node.widgets.splice(idx, 1)
    }
  }

  // Whole-object assignment through the widget's `value` setter — the same
  // pattern rgthree's own configure() uses, so getLoraInfo() etc. fire
  // exactly as they would on workflow load.
  current = scanLoraRows(node).rows
  for (let i = 0; i < values.length; i++) {
    current[i].value = values[i]
  }

  // Redraw/resize exactly as rgthree's own onNodeCreated.
  const computed = node.computeSize()
  node.size[0] = Math.max(node.size[0], computed[0])
  node.size[1] = Math.max(node.size[1], computed[1])
  node.setDirtyCanvas(true, true)
}
