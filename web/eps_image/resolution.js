/**
 * @file EPS Resolution frontend (FORMAT.md §6.5). M1 = hideable outputs only,
 * via two SELF-CONTAINED node properties (no shared/global settings entry —
 * this file doesn't own `web/eps_image.js` or any settings registration).
 * The canvas grid is M2 and is NOT built here.
 *
 * ---- Hideable outputs: how, and why it's two different mechanisms ----
 *
 * FORMAT.md §6.5 says "Frontend does the hide (litegraph output `hidden`
 * flag)". VERIFIED against the frontend source checked out at
 * `.../scratchpad/ComfyUI_frontend` and its extracted litegraph types
 * (`LGraphNode.ts`, `LGraphCanvas.ts`): there is NO such flag. Widget
 * *inputs* have a real, load-bearing `.hidden` (filtered by
 * `isWidgetVisible()` in `computeSize()`/`_arrangeWidgets()`,
 * LGraphNode.ts ~3935-3946), but plain OUTPUT slots have no equivalent —
 * `drawSlots()` (LGraphNode.ts ~4107-4137) draws every entry of
 * `_concreteOutputs` unconditionally (the visibility gate there is only
 * about *widget-input* slots), and `computeSize()`'s row count
 * (`Math.max(inputs..., outputs.length, 1)`, ~1758-1761) counts every
 * output with no hidden-filter either. So a bare `.hidden = true` on an
 * output slot would do nothing.
 *
 * The only way to genuinely remove an output's row is `LGraphNode.
 * removeOutput(slot)` / `addOutput(name, type)` (LGraphNode.ts ~1622-1685) —
 * the same category of technique FORMAT.md §6.4 already sanctions for EPS
 * Switcher's growing INPUT sockets. But it comes with a sharp constraint for
 * a real (executing) node: ComfyUI's prompt serializer
 * (`ComfyUI_frontend/src/utils/executionUtil.ts` ~131-135) records a link's
 * source as a bare positional index — `[origin_id, origin_slot]` — with NO
 * name lookup, and that index is resolved against the BACKEND's fixed
 * `RETURN_TYPES` tuple order at execution time, which never changes.
 * `removeOutput()` itself decrements `origin_slot` on every link whose slot
 * comes AFTER the removed one (LGraphNode.ts ~1670-1685) to keep the
 * FRONTEND's array self-consistent — but the backend tuple doesn't shift to
 * match. So removing anything other than the true TAIL of `node.outputs`
 * would silently repoint any live wire on a LATER output (e.g. `width`,
 * `height`) at the wrong backend value. Concretely: `original_width` /
 * `original_height` (RETURN_NAMES' last two entries) sit at the tail, so
 * removing/restoring that pair (LIFO) is 100% safe — nothing ever sits after
 * them to desync. `image` (the passthrough) is RETURN_NAMES[0], with
 * `resized_image`/`width`/`height` always after it, so removing it for real
 * would corrupt any of THEIR existing links. There is no reordering trick
 * that fixes this (the backend order is frozen, FORMAT.md §6.5/§8).
 *
 * So: "Show original size" uses REAL removeOutput/addOutput (space is
 * genuinely reclaimed). "Show passthrough image" uses a purely COSMETIC,
 * data-model-untouched suppression instead — it monkeypatches just that one
 * slot's own `draw()` to a no-op for the duration of a single synchronous
 * `drawSlots()` call (LGraphNode.ts ~4107), then restores it immediately.
 * `node.outputs`/`_concreteOutputs` membership, order, and every index are
 * never touched, so there is zero risk to link correctness — the tradeoff is
 * that the passthrough's row stays reserved (a blank row) rather than the
 * node shrinking. Documented here rather than silently shipping a "hidden"
 * flag that does nothing.
 */

import { app } from '../../../scripts/app.js'

const NODE_TYPE = 'EPSResolution'
const NODE_TITLE = 'EPS Resolution'
const PREFIX = '[eps_image/resolution]'

const PROP_SHOW_PASSTHROUGH = 'Show passthrough image'
const PROP_SHOW_ORIGINAL_SIZE = 'Show original size'

/** eps_image/nodes_resolution.py RETURN_NAMES — the one hideable leading
 * output, and the hideable trailing pair (order matters, see file header). */
const PASSTHROUGH_NAME = 'image'
const ORIGINAL_SIZE_NAMES = ['original_width', 'original_height']
const ORIGINAL_SIZE_TYPE = 'INT'

// --------------------------------------------------------------- utilities

function outputIndexByName(node, name) {
  return (node.outputs || []).findIndex((output) => output?.name === name)
}

function isOutputConnected(output) {
  return !!(output && Array.isArray(output.links) && output.links.length > 0)
}

function toast(node, severity, detail) {
  try {
    app.extensionManager?.toast?.add?.({
      severity,
      summary: node.title || NODE_TITLE,
      detail,
      life: severity === 'error' ? 6000 : 3000
    })
  } catch (error) {
    console.warn(PREFIX, 'toast failed', error)
  }
}

/** Recompute layout after an outputs-array change: grow freely, but also
 * allow the height to shrink back down (arrange() on its own only grows). */
function resyncSize(node) {
  const computed = node.computeSize()
  node.setSize([Math.max(node.size[0], computed[0]), computed[1]])
  node.setDirtyCanvas(true, true)
}

// ------------------------------------------------- "Show original size"

/** REAL removeOutput/addOutput, tail-only (see file header for why that's
 * the safety boundary). Idempotent: safe to call redundantly from
 * onPropertyChanged regardless of whether `configure()` already applied the
 * saved outputs array for a reloaded workflow. */
function applyOriginalSizeVisibility(node) {
  const show = node.properties?.[PROP_SHOW_ORIGINAL_SIZE] !== false
  const [widthName, heightName] = ORIGINAL_SIZE_NAMES

  if (show) {
    if (outputIndexByName(node, widthName) === -1) node.addOutput(widthName, ORIGINAL_SIZE_TYPE)
    if (outputIndexByName(node, heightName) === -1) node.addOutput(heightName, ORIGINAL_SIZE_TYPE)
    resyncSize(node)
    return
  }

  const widthIdx = outputIndexByName(node, widthName)
  const heightIdx = outputIndexByName(node, heightName)
  if (widthIdx === -1 && heightIdx === -1) return // already hidden

  const widthOut = widthIdx !== -1 ? node.outputs[widthIdx] : null
  const heightOut = heightIdx !== -1 ? node.outputs[heightIdx] : null
  if (isOutputConnected(widthOut) || isOutputConnected(heightOut)) {
    // Never silently sever an existing wire — restore the property instead.
    node.properties[PROP_SHOW_ORIGINAL_SIZE] = true
    toast(node, 'warn', 'Unwire the original-size outputs before hiding them.')
    return
  }

  // True tail removal, LIFO: height (the last RETURN_NAMES entry) first,
  // then width becomes the new tail.
  if (heightIdx !== -1) node.removeOutput(heightIdx)
  const widthIdxAfter = outputIndexByName(node, widthName)
  if (widthIdxAfter !== -1) node.removeOutput(widthIdxAfter)
  resyncSize(node)
}

// ------------------------------------------------- "Show passthrough image"

/** Cosmetic-only suppression of the `image` output's dot + label. Installed
 * once per node instance; reads the live property on every draw rather than
 * baking a decision in, so toggling the property redraws correctly with no
 * further wiring needed. */
/** Guard the cosmetic passthrough hide the same way applyOriginalSizeVisibility
 * guards its real removal: refuse to hide while the `image` output is wired.
 * The cosmetic patch only suppresses slot.draw -- LGraphCanvas.drawConnections
 * and getSlotInPosition ignore it, so a hidden-but-connected output would leave
 * a wire dangling to an invisible, still-hit-testable dot (looks broken). */
function applyPassthroughVisibility(node) {
  const hide = node.properties?.[PROP_SHOW_PASSTHROUGH] === false
  if (hide) {
    const idx = outputIndexByName(node, PASSTHROUGH_NAME)
    const out = idx !== -1 ? node.outputs[idx] : null
    if (isOutputConnected(out)) {
      node.properties[PROP_SHOW_PASSTHROUGH] = true // never leave a dangling wire
      toast(node, 'warn', 'Unwire the passthrough image output before hiding it.')
    }
  }
  node.setDirtyCanvas(true, true) // cosmetic-only: no layout change needed
}

function installPassthroughVisibility(node) {
  if (node._epsPassthroughPatched) return
  node._epsPassthroughPatched = true

  const originalDrawSlots = node.drawSlots
  if (typeof originalDrawSlots !== 'function') return // defensive: unrecognized litegraph build

  node.drawSlots = function (ctx, options) {
    const hide = this.properties?.[PROP_SHOW_PASSTHROUGH] === false
    const idx = hide ? outputIndexByName(this, PASSTHROUGH_NAME) : -1
    const slot = idx !== -1 ? this._concreteOutputs?.[idx] : null

    if (!slot || typeof slot.draw !== 'function') {
      originalDrawSlots.call(this, ctx, options)
      return
    }

    // Patch just this one slot's own draw() for this single synchronous
    // call, then put it back exactly as found (own-property vs. inherited —
    // see file header: never leave an own `undefined` shadowing the
    // prototype's real draw()).
    const hadOwnDraw = Object.prototype.hasOwnProperty.call(slot, 'draw')
    const original = slot.draw
    slot.draw = () => {}
    try {
      originalDrawSlots.call(this, ctx, options)
    } finally {
      if (hadOwnDraw) slot.draw = original
      else delete slot.draw
    }
  }
}

// --------------------------------------------------------------- lifecycle

/** Frontend-only one-time setup. M1 has nothing global to register (no
 * canvas widget, no settings entry) — kept for symmetry with the other
 * eps_image sub-features and as the natural home for M2's grid setup. */
export function init() {
  // Nothing to do for M1.
}

/** Per-node-instance attach; no-op unless node is EPSResolution. */
export function attach(node) {
  if (node.comfyClass !== NODE_TYPE) return

  node.addProperty(PROP_SHOW_PASSTHROUGH, true, 'boolean')
  node.addProperty(PROP_SHOW_ORIGINAL_SIZE, true, 'boolean')

  installPassthroughVisibility(node)

  const originalOnPropertyChanged = node.onPropertyChanged
  node.onPropertyChanged = function (name, value, prevValue) {
    const result = originalOnPropertyChanged?.call(this, name, value, prevValue)
    if (name === PROP_SHOW_PASSTHROUGH) {
      applyPassthroughVisibility(this)
    } else if (name === PROP_SHOW_ORIGINAL_SIZE) {
      applyOriginalSizeVisibility(this)
    }
    return result
  }

  // A freshly created node already matches its just-seeded defaults (both
  // true = nothing hidden); a reloaded node's saved value arrives via
  // configure()'s own onPropertyChanged calls (see file header) and is
  // handled above, not here.
}
