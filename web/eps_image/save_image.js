/**
 * EPS Save Image -- frontend half (FORMAT.md §6.14, provenance M2).
 *
 * The node itself needs no panel: it is Save Image with provenance baked
 * into the PNG, and a BAKED file loads pre-soloed through ComfyUI's own
 * drop handler. What this file adds is the FILENAME FALLBACK for files
 * that predate M2 but carry the M1 run token in their name (every
 * `save_prefix`-named file since v0.67.0): after the frontend's
 * `app.handleFile` has loaded a dropped/opened image's workflow, if the
 * graph holds exactly one EPS Run Multiplier whose `solo_run` is empty and
 * the file name ends in `<token>_NNNNN_`, the token is written into
 * `solo_run` (value + callback, so the readout recomputes) and a toast
 * says so. A baked file (its multiplier already soloed to that token) is
 * left alone; an ambiguous graph (several unsoloed multipliers) gets a
 * toast naming the token to paste by hand instead of a guess. Nothing
 * here touches the drop path itself -- it only reads the graph after the
 * frontend has finished loading it (wrapped, never replaced; §7.5).
 */

import { app } from '../../../scripts/app.js'
import { walkLiveNodes } from '../lora_library/api.js'

const PREFIX = '[EPSNodes save_image]'
const MULTIPLIER_CLASS = 'EPSCrossSweep'
const SOLO_WIDGET = 'solo_run'

/** The M1 token grammar (nodes_cross_sweep.py `_run_token`): optional
 * `m{N}` (+`_v{M}`), then `p{N}` | `i{N}_t{N}` | `t{N}` -- taken only from
 * the END of the stem, right before Save Image's `_NNNNN_` counter. */
const TOKEN_AT_END_RE = /(?:^|_)((?:m\d+(?:_v\d+)?_)?(?:p\d+|i\d+_t\d+|t\d+))_\d{5}_?$/

/**
 * The run token carried by a saved file's name, or null.
 * `Portrait_m2_i1_t3_00001_.png` -> `m2_i1_t3`; `plain_00001_.png` -> null.
 * @param {string} fileName @returns {string|null}
 */
export function tokenFromFileName(fileName) {
  const stem = String(fileName || '').replace(/\.[^.]+$/, '')
  const match = TOKEN_AT_END_RE.exec(stem)
  return match ? match[1] : null
}

function soloWidgetOf(node) {
  return (node?.widgets || []).find((w) => w && w.name === SOLO_WIDGET) || null
}

function toast(severity, detail) {
  try {
    app.extensionManager?.toast?.add?.({ severity, summary: 'EPS Save Image', detail, life: 6000 })
  } catch {
    // toast is a nicety
  }
}

/**
 * Decide what the fallback should do for *token* against *multipliers*
 * (each `{soloValue}`): 'baked' (one already carries the token -- a baked
 * file, leave it), 'apply' (exactly one unsoloed multiplier), 'ambiguous'
 * (several unsoloed), or 'none'. Pure, for the tests.
 * @param {string} token @param {Array<{soloValue: string}>} multipliers
 */
export function decideFilenameSolo(token, multipliers) {
  if (!token || !Array.isArray(multipliers) || multipliers.length === 0) return 'none'
  if (multipliers.some((m) => (m.soloValue || '').trim() === token)) return 'baked'
  const unsoloed = multipliers.filter((m) => !(m.soloValue || '').trim())
  if (unsoloed.length === 1) return 'apply'
  if (unsoloed.length > 1) return 'ambiguous'
  return 'none'
}

function applyFilenameSolo(file) {
  if (!file || !/^image\//i.test(file.type || '')) return
  const token = tokenFromFileName(file.name)
  if (!token) return
  const found = []
  for (const { node } of walkLiveNodes(app.graph)) {
    const cls = node?.comfyClass || node?.constructor?.comfyClass || node?.type
    if (cls !== MULTIPLIER_CLASS) continue
    const widget = soloWidgetOf(node)
    if (!widget) continue
    found.push({ node, widget, soloValue: String(widget.value ?? '') })
  }
  const verdict = decideFilenameSolo(token, found)
  if (verdict === 'apply') {
    const { widget, node } = found.find((m) => !(m.soloValue || '').trim())
    widget.value = token
    try {
      widget.callback?.(token)
    } catch (error) {
      console.warn(PREFIX, 'solo_run callback threw', error)
    }
    node.graph?.setDirtyCanvas(true, true)
    toast('info', `Soloed the Run Multiplier to ${token} (read from the file name). Clear solo_run to run the whole set.`)
  } else if (verdict === 'ambiguous') {
    toast('warn', `This file names run ${token}, but the workflow has several Run Multipliers -- paste the token into the right one's solo_run.`)
  }
}

let installed = false

/** Wrap `app.handleFile` ONCE (§7.5: chained, never replaced). */
export function init() {
  if (installed) return
  installed = true
  const original = app?.handleFile
  if (typeof original !== 'function') return
  app.handleFile = async function (file, ...rest) {
    const result = await original.call(this, file, ...rest)
    try {
      applyFilenameSolo(file)
    } catch (error) {
      console.warn(PREFIX, 'filename solo fallback failed', error)
    }
    return result
  }
}
