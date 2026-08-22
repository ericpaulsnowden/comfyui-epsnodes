/**
 * @file DaSiWa Advanced LoRA Loader bridge for the EPS LoRA Picker's
 * Send-to-loader row (FORMAT.md §6.13 M4) — the second entry in
 * picker.js's comfyClass-keyed send-adapter registry, mirroring
 * pll_bridge.js's surface exactly: find / probe / pure row mapping /
 * write.
 *
 * ATTRIBUTION — SCHEMA PROVENANCE, DATA ONLY (FORMAT.md §6.13 M4): the
 * target node is ComfyUI-DaSiWa-Nodes' `DaSiWa_LTX2LoraLoader`
 * ("DaSiWa Advanced LoRA Loader"), a GPL-3.0-licensed pack. This file is
 * NEW code that speaks that node's DATA format only; no code was copied
 * from it. The schema facts recorded here were learned by READING its
 * sources (nodes/nodes_ltx2_loader.py and js/ltx2_dynamic_ui.js):
 *   - the `stack_data` widget/property holds a JSON array of rows
 *     `{on, lora, str, vs, as}` (apply_stack's parse loop);
 *   - `str` is the master strength, DaSiWa's own range is ±5 (the node
 *     docstring's "-5.0 to 5.0"; the UI's own clamp on hand entry);
 *   - `vs`/`as` are the video/audio branch multipliers, default 1.0 —
 *     applied as `str * vs` / `str * as`, so they are TUNING the owner
 *     set in DaSiWa's own UI, never something the picker should reset;
 *   - the node's canvas UI treats `properties.stack_data` as the live
 *     source of truth and mirrors it into the `stack_data` widget on
 *     every draw (its sync() step), so a write must set BOTH to the
 *     same string or the next draw fights the stale one;
 *   - row-height math: see DASIWA_ROW_START/DASIWA_ROW_H below.
 *
 * Unlike pll_bridge.js there is no widget surgery here: the whole write
 * is one JSON string into one property + one widget, plus a grow-only
 * height fit and a redraw. LOSSY EDGES ARE LOUD, NEVER SILENT (owner
 * decisions 2026-08-09): the picker's dual-strength rows flatten to
 * DaSiWa's single `str` (the MODEL strength wins) and out-of-range
 * strengths clamp to ±5 — both are reported back to the caller as
 * `{flattened, clamped}` basename lists so picker.js's success toast can
 * name every affected row.
 */

import { app } from '../../../scripts/app.js'

/** Exact ComfyUI type/comfyClass string — the pack's NODE_CLASS_MAPPINGS key. */
export const DASIWA_LOADER_TYPE = 'DaSiWa_LTX2LoraLoader'

/** DaSiWa's own master-strength range (see the header's provenance notes) —
 * NOT the picker's ±10: the narrower target range wins, loudly. */
const DASIWA_STR_MIN = -5
const DASIWA_STR_MAX = 5

/**
 * DaSiWa's row-layout math, DERIVED BY READING (never copying)
 * ltx2_dynamic_ui.js — beforeRegisterNodeDef's compact-layout constants,
 * lines 174–178 of the audited clone:
 *   ROW_H = 28; BTN_H = 16; BTN_Y = 40;
 *   ROW_START = BTN_Y + BTN_H + 3;            // = 59
 *   calcHeight = n => ROW_START + n * ROW_H + 2;
 * writeRowsToDasiwa grows node.size[1] to calcHeight(rows.length) so the
 * node the picker just filled shows every row without a hand resize.
 */
const DASIWA_ROW_START = 59
const DASIWA_ROW_H = 28

/** §6.3-STYLE message vocabulary (pll_bridge.js's shapes, DaSiWa-specific
 * text) — same codes, same tone, same title+status surfaces in picker.js. */
const MSG_NO_TARGET_SELECTED = 'Pick a target DaSiWa Advanced LoRA Loader node above.'
const MSG_SHAPE_DRIFT =
  'DaSiWa Advanced LoRA Loader internals changed — send disabled (v-check)'

/** Every live `DaSiWa_LTX2LoraLoader` instance in the current graph,
 * ascending node id — §6.13 M4's cross-family combo order.
 * v0.68.1: UNUSED BY THE PICKER, marked for removal. Since v0.64.0
 * picker.js's findSendCandidates() walks the whole workflow (subgraphs
 * included) against its adapter registry and never calls this (the
 * registry's `find` key is gone as of v0.68.1). Root-only and kept solely
 * because tests/test_dasiwa_bridge_js.py pins the export surface
 * (`hasFindDasiwaNodes`, `findOrder`); remove together with those pins --
 * and with pll_bridge.js's findPllNodes, so the two bridges keep mirroring
 * each other's surface. */
export function findDasiwaNodes() {
  const nodes = app.graph?._nodes || app.graph?.nodes || []
  return nodes
    .filter((node) => node && node.type === DASIWA_LOADER_TYPE)
    .sort((a, b) => a.id - b.id)
}

/** The node's `stack_data` widget, or null — the probe's presence check and
 * the write's second sink. */
function findStackWidget(node) {
  const widgets = (node && node.widgets) || []
  return widgets.find((widget) => widget && widget.name === 'stack_data') || null
}

/** The raw stack value, properties first — `properties.stack_data` is the
 * node's live source of truth (file header), the widget only mirrors it. */
function readStackDataRaw(node) {
  const widget = findStackWidget(node)
  return node.properties?.stack_data ?? (widget ? widget.value : undefined)
}

/**
 * Tolerant parse of the existing stack: `{ok, rows}`. Absent/empty is a
 * healthy empty stack (a fresh node, or one whose UI hasn't run yet);
 * anything present must parse to a JSON array or `ok` is false — the
 * probe's shape-drift signal. The write reads `.rows` regardless (probe
 * already gated drift; a torn value degrades to "no vs/as to preserve",
 * never a throw).
 */
function parseStackRows(raw) {
  if (raw == null) return { ok: true, rows: [] }
  if (typeof raw !== 'string') return { ok: false, rows: [] }
  if (raw.trim() === '') return { ok: true, rows: [] }
  try {
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? { ok: true, rows: parsed } : { ok: false, rows: [] }
  } catch (error) {
    return { ok: false, rows: [] }
  }
}

/**
 * Single feature-detection gate every DaSiWa interaction goes through —
 * pll_bridge.js's probe-first shape (§6.3: "probe first, mutate after"),
 * scoped to exactly what FORMAT.md §6.13 M4 pins: the type, the
 * `stack_data` widget's presence, and the existing value being absent,
 * empty, or a JSON array. Only reads.
 */
export function probeDasiwa(node) {
  if (!node || node.type !== DASIWA_LOADER_TYPE) {
    return { ok: false, code: 'wrong-type', message: MSG_NO_TARGET_SELECTED }
  }
  if (!findStackWidget(node)) {
    return { ok: false, code: 'shape-drift', message: MSG_SHAPE_DRIFT }
  }
  const parsed = parseStackRows(readStackDataRaw(node))
  if (!parsed.ok) {
    return { ok: false, code: 'shape-drift', message: MSG_SHAPE_DRIFT }
  }
  const count = parsed.rows.length
  return {
    ok: true,
    code: 'ok',
    message: `Ready — target has ${count} row${count === 1 ? '' : 's'}.`,
    rowCount: count
  }
}

/** @param {string} name @returns {string} basename of a forward-slash path. */
/** §4 separator rule, applied to MATCHING only -- the written `lora` keeps
 * the picker's spelling (their apply_stack resolves either through
 * folder_paths). */
function normalizeSeparators(name) {
  return String(name).replace(/\\/g, '/')
}

function basename(name) {
  const text = String(name)
  const idx = text.lastIndexOf('/')
  return idx === -1 ? text : text.slice(idx + 1)
}

/**
 * Picker selection rows -> DaSiWa row objects — the pure half of
 * `writeRowsToDasiwa`, exported so tests can drive the mapping directly
 * (rowsForPll's convention). Each picker row `{file, on, strength,
 * strength_clip}` becomes `{on, lora, str, vs, as}` in picker order:
 * `str` is the MODEL strength clamped to DaSiWa's own ±5, and `vs`/`as`
 * are preserved from the existing DaSiWa row whose `lora` RAW-STRING-equals
 * the file (re-sending must survive tuning done in DaSiWa's own UI —
 * owner decision 2026-08-09), else the schema defaults 1.0/1.0.
 *
 * The two loud-lossy edges come back as basename lists: `flattened` names
 * rows whose finite `strength_clip` differs from `strength` (DaSiWa has
 * one strength; the model strength was used), `clamped` names rows whose
 * strength left ±5.
 */
export function rowsForDasiwa(pickerRows, existingRows) {
  const existing = Array.isArray(existingRows) ? existingRows : []
  const rows = []
  const flattened = []
  const clamped = []
  for (const raw of Array.isArray(pickerRows) ? pickerRows : []) {
    const row = raw || {}
    const strength = row.strength ?? 1
    const str = Math.min(DASIWA_STR_MAX, Math.max(DASIWA_STR_MIN, strength))
    // Separator-INSENSITIVE match, picker spelling written (review
    // 2026-08-09, the §4 rule): DaSiWa's own UI stores folder_paths'
    // NATIVE spellings -- backslashes on Windows -- while picker rows are
    // always forward-slash, so a raw-string compare silently reset the
    // vs/as tuning on exactly the machine (the PC) it mattered most.
    const wanted = normalizeSeparators(row.file)
    const prior = existing.find(
      (entry) =>
        entry &&
        typeof entry === 'object' &&
        typeof entry.lora === 'string' &&
        normalizeSeparators(entry.lora) === wanted
    )
    rows.push({
      on: row.on ?? true,
      lora: row.file,
      str,
      vs: Number.isFinite(prior?.vs) ? prior.vs : 1.0,
      as: Number.isFinite(prior?.as) ? prior.as : 1.0
    })
    if (
      typeof row.strength_clip === 'number' &&
      Number.isFinite(row.strength_clip) &&
      row.strength_clip !== strength
    ) {
      flattened.push(basename(row.file))
    }
    if (str !== strength) clamped.push(basename(row.file))
  }
  return { rows, flattened, clamped }
}

/**
 * Write the picker's selection rows into a probed-ok DaSiWa loader —
 * single-widget JSON, no widget surgery. Returns the `{flattened, clamped}`
 * basename lists for picker.js's loud-lossy success toast.
 */
export function writeRowsToDasiwa(node, pickerRows) {
  const widget = findStackWidget(node)
  // Tolerant existing-rows read (probe already gated drift): the vs/as
  // preservation source.
  const existing = parseStackRows(readStackDataRaw(node)).rows
  const { rows, flattened, clamped } = rowsForDasiwa(pickerRows, existing)
  const json = JSON.stringify(rows)

  // BOTH sinks, the SAME string: `properties.stack_data` is the node's live
  // source of truth (its canvas UI mirrors properties -> widget on every
  // draw — file header) and the widget is what serializes and executes;
  // writing one without the other leaves the next draw to overwrite it.
  node.properties = node.properties || {}
  node.properties.stack_data = json
  if (widget) widget.value = json
  // The node's `model_type` combo (Basic vs LTX-2.3) is the owner's own
  // setting — deliberately never written here.

  // Grow-only height fit: DaSiWa's own calcHeight(n) (see DASIWA_ROW_START's
  // provenance note above) — never shrink a node the owner made taller.
  const fitHeight = DASIWA_ROW_START + rows.length * DASIWA_ROW_H + 2
  node.size[1] = Math.max(node.size[1], fitHeight)
  node.setDirtyCanvas(true, true)
  return { flattened, clamped }
}
