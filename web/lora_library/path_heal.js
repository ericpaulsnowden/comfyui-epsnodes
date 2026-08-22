/**
 * @file Cross-OS model-path healing on load (FORMAT.md §7.6).
 *
 * The problem (owner, 2026-08-22): a workflow saved on the Windows PC stores
 * every model combo's value with Windows separators
 * (`styles\film_grain.safetensors`); opened on the Linux box (or the Mac),
 * `folder_paths.get_filename_list` spells the same file
 * `styles/film_grain.safetensors`, so the saved value is simply NOT IN the
 * combo's list -- core paints it as missing and the queue rejects "value
 * not in list" -- and every model has to be re-picked by hand. And the
 * reverse when the workflow travels back. This pack's own LoRA nodes already
 * store forward-slash names and resolve separator-insensitively (FORMAT.md
 * §4); this module extends the same courtesy to EVERY OTHER combo: core's
 * checkpoint / UNET / CLIP / VAE / LoRA / ControlNet / upscale loaders,
 * LoadImage subfolders, third-party loaders -- anything whose option list
 * contains a path.
 *
 * **What it does, and deliberately nothing more.** For each combo widget
 * whose options contain at least one string with a separator, a value that
 * is NOT in the options is replaced by the ONE option that equals it once
 * both sides' separators are normalized to `/` (and surrounding whitespace
 * trimmed). Zero matches (the file really is missing here) or several
 * matches (two local files differing only by separator -- legal on Linux,
 * where `\` is an ordinary filename character) leave the value untouched:
 * the heal never guesses. Case is NOT folded -- it can be significant on
 * Linux, and a guess there would silently load the wrong file. A value that
 * IS in the list is never touched, even if another option also normalizes
 * to it.
 *
 * **Restore-time correction, not a user edit.** `healNode` writes
 * `widget.value` directly and never fires `widget.callback` (a callback
 * would run every "on change" side effect -- filename-prefix rewrites,
 * downstream combo refreshes -- for what is merely a spelling fix);
 * `node.setDirtyCanvas` is called once per healed node so the canvas
 * repaints. On the installed frontend (1.48.7) `widget.value` is a
 * getter/setter over the widget-state store, so a direct assignment is
 * exactly what core's own load loop does (`app.ts`: `widget.value =
 * values[0]` for a null combo value) and reaches the Vue renderer too.
 *
 * **When it runs.** The REQUIRED path is the extension's `loadedGraphNode`
 * hook: `app.ts`'s `loadGraphData` calls `forEachNode(rootGraph, node =>
 * { ...; invokeExtensions('loadedGraphNode', node) })` -- once per node,
 * subgraphs included, AFTER `graph.configure` has restored every widget
 * value and AFTER core's own combo fix-ups (which only reset a value that
 * is `null`, or -- `reset_invalid_values` -- for the DEFAULT graph; a real
 * workflow's out-of-list value survives to this hook, verified in the
 * 1.48.7 bundle's source map). That covers every workflow load, image /
 * JSON drop, template, and undo/redo (the ChangeTracker replays through
 * `loadGraphData`). Paste gets a cheap second path: `attachConfigureHeal`
 * (from `nodeCreated`) chains `node.onConfigure`, the hook litegraph fires
 * after a pasted node's widget values are restored; on a whole-workflow
 * load it simply fires first and `loadedGraphNode` finds nothing left to
 * do. Both paths feed ONE toast: counts accumulate and a `setTimeout(0)`
 * coalescer (armed by the first heal, fired after the synchronous load loop
 * settles) reports "healed N model path(s) for this machine" once per load,
 * never once per node.
 *
 * **Gate.** The boolean setting `EPSNodes.HealModelPaths` (registered in
 * `settings.js`, default ON) is read through `app.extensionManager.setting
 * .get` -- the accessor the rest of the pack uses -- on every hook call, so
 * flipping it needs no reload; an unreachable settings store reads as ON.
 *
 * Never throws out of a hook: every entry point is `try/catch` +
 * `api.warn`, and the module registers no window-level listeners (FORMAT.md
 * §7.5 trivially satisfied).
 */

import { app } from '../../../scripts/app.js'
import * as api from './api.js'

/** The FORMAT.md §7.6 gate -- registered as a boolean in `settings.js`,
 * default `true`. Owned here so the hook and the registration can never
 * disagree on the id. */
export const HEAL_SETTING_ID = 'EPSNodes.HealModelPaths'

const SEPARATOR_RE = /[\\/]/

/**
 * *value* with every `\` flipped to `/` -- the §4 rule, the same one
 * `picker.js`'s `normalizeLoraName` and `sets_store.py`'s
 * `_normalize_separators` apply. Non-strings come back unchanged.
 * @param {unknown} value
 */
export function normalizeSeparators(value) {
  return typeof value === 'string' ? value.replace(/\\/g, '/') : value
}

/** The comparison key: trimmed, separators normalized. Case is NOT folded. */
function comparableKey(value) {
  return normalizeSeparators(value.trim())
}

/**
 * Pure matcher. When *value* is a string NOT present in *options* and
 * exactly ONE option equals it after normalizing separators (both sides to
 * `/`) and trimming whitespace, returns that option; otherwise the input
 * comes back unhealed. Never guesses: zero or several distinct matches
 * both mean "leave it alone". Case-sensitive on purpose (file header).
 * @param {unknown} value - the widget's current value
 * @param {unknown} options - the combo's option list (an ARRAY of strings;
 *   evaluate a function-valued `widget.options.values` first -- see
 *   `comboValuesOf`)
 * @returns {{healed: boolean, value: unknown}}
 */
export function healComboValue(value, options) {
  const untouched = { healed: false, value }
  if (typeof value !== 'string' || !Array.isArray(options)) return untouched
  if (options.includes(value)) return untouched
  const wanted = comparableKey(value)
  if (wanted === '') return untouched
  let match = null
  for (const option of options) {
    if (typeof option !== 'string' || comparableKey(option) !== wanted) continue
    // Two DISTINCT options collapsing onto the same key = a collision;
    // the same string listed twice is still one candidate.
    if (match !== null && match !== option) return untouched
    match = option
  }
  return match === null ? untouched : { healed: true, value: match }
}

/**
 * The combo's option list as an array, however the widget carries it: an
 * array as-is, a function (legacy widgets, widget-converted inputs)
 * evaluated as `values(widget, node)` -- the same call `ComboWidget.ts`'s
 * own `getValues` makes -- or an object's keys (`litegraphUtil.ts`'s
 * `resolveComboValues` shape). A throwing function yields `[]`.
 * @param {object} widget @param {object} [node] @returns {unknown[]}
 */
export function comboValuesOf(widget, node) {
  const values = widget?.options?.values
  if (typeof values === 'function') {
    try {
      const out = values(widget, node)
      return Array.isArray(out) ? out : []
    } catch (error) {
      api.warn(`combo values() threw for widget ${widget?.name}`, error)
      return []
    }
  }
  if (Array.isArray(values)) return values
  if (values && typeof values === 'object') return Object.keys(values)
  return []
}

/** A combo: litegraph's `type === 'combo'`, or anything carrying an
 * option list (`options.values`) -- the second clause catches custom combo
 * widget types third-party packs register under their own type name. */
function isComboWidget(widget) {
  if (!widget || typeof widget !== 'object') return false
  if (widget.type === 'combo') return true
  return widget.options != null && widget.options.values != null
}

function nodeLabel(node) {
  const title = node?.title || node?.type || node?.comfyClass || 'node'
  return node?.id != null ? `${title}#${node.id}` : title
}

/**
 * Heals every path-combo on *node* in place (file header for the rules):
 * only combos whose options contain at least one string with a separator
 * are considered; each healed widget gets `widget.value` written directly,
 * NO `callback`; `node.setDirtyCanvas(true, true)` once if anything
 * changed. Each heal is logged (old → new) so a surprised user can see
 * exactly what moved.
 * @param {object} node - a litegraph node (anything without `widgets` is a no-op)
 * @returns {number} how many widget values were healed
 */
export function healNode(node) {
  if (!node || !Array.isArray(node.widgets)) return 0
  let healed = 0
  for (const widget of node.widgets) {
    if (!isComboWidget(widget) || typeof widget.value !== 'string') continue
    const values = comboValuesOf(widget, node)
    if (!values.some((option) => typeof option === 'string' && SEPARATOR_RE.test(option))) continue
    const result = healComboValue(widget.value, values)
    if (!result.healed) continue
    api.log(
      `healed ${nodeLabel(node)} › ${widget.name}: ` +
        `${JSON.stringify(widget.value)} → ${JSON.stringify(result.value)}`
    )
    widget.value = result.value // restore-time correction: no callback (file header)
    healed += 1
  }
  if (healed > 0 && typeof node.setDirtyCanvas === 'function') node.setDirtyCanvas(true, true)
  return healed
}

/**
 * The `EPSNodes.HealModelPaths` gate, read fresh on every call through the
 * pack's usual accessor. Unknown / unreachable store ⇒ ON (the default).
 * @returns {boolean}
 */
export function isHealEnabled() {
  try {
    const value = app.extensionManager?.setting?.get?.(HEAL_SETTING_ID)
    return value !== false
  } catch {
    return true
  }
}

// --- One toast per load: the setTimeout(0) coalescer (file header) ---

let pendingHealed = 0
let toastTimer = null

/** Adds *count* to the per-load tally and arms the coalescer if it isn't
 * already armed; the toast fires once the current synchronous load loop
 * has settled, whatever number of nodes fed it. */
function recordHealed(count) {
  if (!(count > 0)) return
  pendingHealed += count
  if (toastTimer !== null) return
  toastTimer = setTimeout(flushHealToast, 0)
}

function flushHealToast() {
  const count = pendingHealed
  pendingHealed = 0
  toastTimer = null
  if (!(count > 0)) return
  const noun = count === 1 ? 'model path' : 'model paths'
  try {
    app.extensionManager?.toast?.add?.({
      severity: 'info',
      summary: `EPSNodes: healed ${count} ${noun} for this machine`,
      detail:
        'Model names saved on another operating system were re-pointed at ' +
        'the matching local files (Windows ↔ Linux/macOS path separators). ' +
        'Only values that were missing here and match exactly one local ' +
        'file were touched. Settings › EPSNodes › "Heal model paths across ' +
        'operating systems on load" turns this off.',
      life: 8000
    })
  } catch (error) {
    api.warn('heal toast failed', error)
  }
}

// --- Extension hooks (wired in web/lora_library.js) ---

/**
 * `loadedGraphNode` hook body -- THE path (file header "When it runs").
 * Gated on the setting; never throws.
 * @param {object} node
 */
export function loadedGraphNode(node) {
  try {
    if (!isHealEnabled()) return
    recordHealed(healNode(node))
  } catch (error) {
    api.warn('path heal (loadedGraphNode) failed', error)
  }
}

/** Nodes whose `onConfigure` is already chained -- guards a double
 * `nodeCreated`. */
const wrappedNodes = new WeakSet()

/**
 * `nodeCreated` hook body: chains (never replaces) `node.onConfigure` so a
 * PASTED node -- which never passes through `loadGraphData` -- is healed
 * right after litegraph restores its widget values. The original hook
 * (core's, another extension's, or this pack's own per-node wrap) runs
 * first and its return value is preserved; our step is its own try/catch.
 * Cheap: one property per node, and on a whole-workflow load it merely
 * heals a moment earlier than `loadedGraphNode` would have (same tally,
 * same single toast).
 * @param {object} node
 */
export function attachConfigureHeal(node) {
  if (!node || typeof node !== 'object' || wrappedNodes.has(node)) return
  wrappedNodes.add(node)
  const originalOnConfigure = node.onConfigure
  node.onConfigure = function (info) {
    let result
    if (typeof originalOnConfigure === 'function') {
      result = originalOnConfigure.apply(this, arguments)
    }
    try {
      if (isHealEnabled()) recordHealed(healNode(this))
    } catch (error) {
      api.warn('path heal (onConfigure) failed', error)
    }
    return result
  }
}
