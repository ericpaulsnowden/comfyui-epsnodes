/**
 * @file EPS Run Multiplier run-count visibility (FORMAT.md §6.10, v0.58.0 --
 * owner ask 2026-08-09: "show the number of runs it is going to make ...
 * before potentially running something that may go overnight and be
 * wrong"). Exports the `init()`/`attach(node)` hooks `web/eps_image.js`
 * calls; `attach` no-ops for every node type other than `EPSCrossSweep`.
 *
 * Two layers, estimate + truth (§6.10's "Run-count visibility" bullet):
 *
 * 1. **On-node readout (pre-queue ESTIMATE).** A display-only DOM line (no
 *    serialized widget -- picker.js's two-flag `serialize: false` +
 *    `serializeValue` block, so nothing enters the workflow file, §8-safe)
 *    showing `Runs: 8 — 4 sweep steps × 2 pairs`, recomputed from the GRAPH
 *    by the pure `estimateRuns()` below: the multiplier's own
 *    `pair_mode`/`sweep_mode` widgets plus an upstream traversal that knows
 *    this pack's fan-out nodes by class id. ANYTHING it cannot count
 *    contributes 1 and downgrades the line to `≥ N`, naming which input is
 *    unknowable -- never false confidence. An aligned-mode length
 *    disagreement or a multiply-mode same-origin vae paints the line as the
 *    ERROR it will become at queue time, BEFORE the queue. Refresh follows
 *    the §6.3 controller idiom through ONE `READOUT_MIN_INTERVAL_MS`
 *    throttle riding three chained triggers (`wrapWithRecompute`): the
 *    `onDrawForeground` wrap as the litegraph catch-all -- which NEVER
 *    fires under the Vue ("New node design") renderer, where the project
 *    rule is no canvas drawing at all -- plus two triggers that exist in
 *    BOTH renderers, a chained `onConnectionsChange` wrap and the two mode
 *    widgets' own callbacks. No polling timers of this file's own, no
 *    window listeners (§7.5).
 *
 * 2. **Execution toast (the TRUTH).** `run()` `send_sync`s
 *    `eps-run-multiplier-count` `{node, steps, pairs, total}` the moment it
 *    executes -- topologically the first seconds of a queue -- and `init()`
 *    toasts the definitive number while cancelling is still cheap
 *    (image_grid.js's `eps-image-grid-collect-skip` listener + toast idiom).
 *
 * **The estimator is PURE on purpose** (picker.js's exported-pure-helpers
 * precedent, so tests/test_cross_sweep_js.py can drive it under Node with
 * no litegraph stub): `estimateRuns(snapshot, nodeId)` operates on a plain
 * graph snapshot -- nodes by id, each `{classType, widgets: {name: value},
 * inputs: {inputName: {originId, originSlot} | null}}` -- built from the
 * live `app.graph` by the thin `snapshotFromGraph(graph)` adapter. The one
 * live-only fact (an Image Grid's buffer count is server state, not graph
 * state) is injected by the ADAPTER as `imageGridCount` from the litegraph
 * node's own `imgs` array; the estimator itself never touches litegraph.
 *
 * Per-source count rules (each verified against the source node's own
 * backend semantics -- see the per-branch comments in `sourceCount()`).
 * List fan-in is propagated, not read off one node in isolation: ComfyUI
 * maps ordinary nodes over input LISTS, and the pack's own list nodes
 * flatten. Concretely: the four §6.4/§6.4b switchers SUM their enabled+
 * wired slots' upstream counts (execute() flattens each slot's list --
 * nodes_switcher.py INPUT_IS_LIST + `enabled_values.extend`), §6.12
 * Checkpoint Switcher (`selection` array length, intrinsic), §6.1
 * Notebook (`entry` non-empty line count, intrinsic), §6.6 Image Grid
 * (Emit ⇒ buffer count as a FLOOR -- the injected `imageGridCount` is a
 * client-side echo of SERVER state; Collect ⇒ 0), §6.8 LoRA Iterator (its
 * own widget step math x the max of its mapped model/clip input counts --
 * nodes_sweep.py has no INPUT_IS_LIST, so core maps it; per-lora mode
 * follows the `lora_stack` wire -- a §6.13 Picker's enabled rows are
 * countable, a §6.2 Apply-Set's file-backed stack is not), a chained
 * §6.10 multiplier (recursive, cycle-guarded), reroutes (followed), and a
 * short allow-list of core single-output nodes that core MAPS over their
 * list inputs (max over wired inputs' counts, floor 1). Anything else is
 * UNKNOWN.
 *
 * **BLOCKED/EMPTY PROPAGATION (round-2 review).** A source counted
 * `{count: 0, atLeast: false}` is KNOWN-BLOCKED: its output is
 * `[ExecutionBlocker]` (the pack's switchers, an empty-selection
 * Checkpoint Switcher, a "nothing to run" chained multiplier), and
 * ComfyUI blocks a consumer when ANY element of ANY consumed input list
 * is a blocker (execution.py `process_inputs`). The ONE exemption is the
 * switchers' own lazy skip (nodes_switcher.py `check_lazy_status` +
 * `_switcher_is_statically_all_off`): a slot whose DIRECT post-reroute
 * upstream is a sibling EPS switcher that is STATICALLY all-off -- every
 * slot it has wired in the serialized prompt literally toggled `false`,
 * or nothing wired at all -- is never requested, so that upstream never
 * runs and never emits its blocker. EVERY OTHER known-zero slot collapses
 * its consuming switcher to `{count: 0, atLeast: false}` outright. The
 * multiplier itself has no lazy inputs, so ANY wired known-zero member --
 * `name` included, even though run() only reads names for save_prefix --
 * collapses the estimate to `Runs: 0`, and that collapse is computed
 * FIRST: a blocked node never executes, the queue SUCCEEDS with 0 runs,
 * so the aligned-conflict/same-origin "queue will fail" paints are
 * suppressed (they would be a lie) and the readout names the
 * empty/blocked input instead. The one paint that still fires is the
 * v0.51.0 dead-output guard mirrored in the chained-multiplier branch:
 * consuming an inner multiplier's model/clip/image/label/vae OUTPUT whose
 * matching INPUT is unwired is a queue-time ValueError on the inner node
 * (nodes_cross_sweep.py `_consumed_output_slots` + its dead_outputs
 * block), painted on THIS consumer's readout.
 */

import { api } from '../../../scripts/api.js'
import { app } from '../../../scripts/app.js'

/** Frozen once shipped -- mirrors the Python node's class id (§6.10/§8). */
export const CLASS_ID = 'EPSCrossSweep'

/** FORMAT.md §6.10 display name -- toast summaries and the readout only. */
const NODE_TITLE = 'EPS Run Multiplier'

/** The backend `send_sync` event name (eps_image/nodes_cross_sweep.py
 * run(), v0.58.0) -- payload `{node, steps, pairs, total}`. */
export const EVENT_NAME = 'eps-run-multiplier-count'

const PREFIX = '[eps_image]'
const READOUT_WIDGET_NAME = 'eps_rc_readout'
const READOUT_WIDGET_TYPE = 'eps_run_count_readout'
const STYLE_TAG_ID = 'eps-run-count-styles'

/** One readout line's pixel height -- fixed, not fill-style: unlike the
 * picker/notebook panels this widget must never eat the node's spare
 * height. */
const READOUT_HEIGHT = 22

/** §6.3 controller idiom (controller.js `HEARTBEAT_MIN_MS`): the
 * redraw-driven recompute runs at most once per this many ms, no matter
 * how fast the canvas repaints. */
const READOUT_MIN_INTERVAL_MS = 500

/** Nodes we've already attached to -- guards a double `nodeCreated`. */
const attachedNodes = new WeakSet()

// ---------------------------------------------------------------------------
// Pure estimator -- exported so tests can drive it under Node (see header)
// ---------------------------------------------------------------------------

/** The four §6.4/§6.4b switcher class ids -> their growing-input prefix
 * (switcher.js `SWITCHER_CLASSES`, nodes_switcher.py's slot patterns). */
export const SWITCHER_PREFIXES = {
  EPSSwitcher: 'image',
  EPSModelSwitcher: 'model',
  EPSClipSwitcher: 'clip',
  EPSVaeSwitcher: 'vae'
}

/** Reroute node types followed straight through (core + rgthree). */
export const REROUTE_CLASSES = new Set(['Reroute', 'Reroute (rgthree)'])

/** Core (and pack) nodes whose MODEL/CLIP/VAE/IMAGE/STRING outputs are a
 * single element PER RUN -- and none of them declare INPUT_IS_LIST, so
 * core MAPS them over their list inputs (execution.py's map-over-list /
 * broadcast): fed a 3-long list they run 3 times and emit a 3-long list.
 * `sourceCount` therefore propagates max(1, max over wired inputs'
 * counts) through them instead of hardcoding 1. EXACT class names,
 * deliberately short: anything not matched is UNKNOWN (contributes 1,
 * downgrades the readout to `≥`) rather than guessed at. EPSLoraPicker
 * and LoraLibraryApplySet belong here for their model/clip outputs; a
 * picker's `lora_stack` CONTENTS are counted separately by the Iterator
 * branch below (`pickerEnabledRowCount`), and an Apply-Set's stack lives
 * in a file on disk, so as a stack SOURCE it stays unknown. */
export const CORE_SINGLE_CLASSES = new Set([
  'CheckpointLoaderSimple',
  'UNETLoader',
  'VAELoader',
  'DualCLIPLoader',
  'CLIPLoader',
  'TripleCLIPLoader',
  'LoadImage',
  'LoraLoader',
  'LoraLoaderModelOnly',
  'CLIPTextEncode',
  'PrimitiveStringMultiline',
  'EPSLoraPicker',
  'LoraLibraryApplySet'
])

/** nodes_sweep.py's exact `mode` combo values (§6.8). */
export const ITERATOR_MODE_ALL_TOGETHER = 'All together'

/**
 * A widget value as a finite number, else *fallback* -- clampStrength's
 * coercion posture (Number(null) is 0, which must not silently zero a
 * missing widget).
 * @param {unknown} value @param {number} fallback @returns {number}
 */
function toNumber(value, fallback) {
  const num =
    typeof value === 'number'
      ? value
      : typeof value === 'string' && value.trim() !== ''
        ? Number(value)
        : NaN
  return Number.isFinite(num) ? num : fallback
}

/**
 * The `toggles` widget value as a plain object -- nodes_switcher.py
 * `_parse_toggles` mirrored: malformed/non-object JSON (or anything that
 * isn't a non-empty string) degrades to "no overrides recorded", i.e.
 * every wired slot enabled. Never throws.
 * @param {unknown} raw @returns {object}
 */
function parseToggles(raw) {
  if (typeof raw !== 'string' || raw.trim() === '') return {}
  try {
    const parsed = JSON.parse(raw)
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed
  } catch {
    // malformed -> every connected slot enabled (backend parity)
  }
  return {}
}

/**
 * A switcher's enabled+wired `<prefix>_N` slot links, in slot order --
 * the slots whose upstream lists execute() will flatten into its output.
 * Mirrors nodes_switcher.py exactly: an absent toggles key is ENABLED
 * (`toggle_map.get(key, True) is False` is the only disabled state),
 * malformed/non-object JSON degrades to "no overrides" (all wired
 * enabled), and only WIRED slots count at all.
 * @param {{widgets?: object, inputs?: object}} node @param {string} prefix
 * @returns {Array<{originId: string|number, originSlot?: number}>}
 */
function enabledSlotLinks(node, prefix) {
  const toggles = parseToggles(node.widgets?.toggles)
  const re = new RegExp(`^${prefix}_(\\d+)$`)
  const links = []
  for (const [name, link] of Object.entries(node.inputs || {})) {
    if (!link || !re.test(name)) continue
    if (toggles[name] === false) continue
    links.push(link)
  }
  return links
}

/**
 * A switcher's enabled+wired SLOT count (`enabledSlotLinks` length). NOTE
 * this is the slot count, NOT the emitted-list length: execute() flattens
 * each enabled slot's upstream LIST (nodes_switcher.py INPUT_IS_LIST +
 * `enabled_values.extend`), so the emitted length is the SUM of the
 * per-slot upstream counts -- `sourceCount`'s switcher branch does that
 * propagation; this helper stays for callers that want the slot count.
 * @param {{widgets?: object, inputs?: object}} node @param {string} prefix
 * @returns {number}
 */
export function switcherEnabledCount(node, prefix) {
  return enabledSlotLinks(node, prefix).length
}

/**
 * Checkpoint Switcher's emitted-list length: the `selection` JSON array's
 * string entries (nodes_checkpoint_switcher.py `_parse_selection`: non-
 * string entries dropped, malformed/non-array degrades to empty -- an
 * empty selection is the whole-node blocker path, i.e. 0 runs).
 * @param {unknown} raw @returns {number}
 */
export function checkpointSelectionCount(raw) {
  if (typeof raw !== 'string' || raw.trim() === '') return 0
  let parsed
  try {
    parsed = JSON.parse(raw)
  } catch {
    return 0
  }
  if (!Array.isArray(parsed)) return 0
  return parsed.filter((entry) => typeof entry === 'string').length
}

/**
 * Notebook fan-out length: non-empty lines of the `entry` widget
 * (nodes_notebook.py `_selected_names` -- one selected name per line,
 * blank/whitespace-only lines skipped; zero lines is a queue-time error
 * there, so 0 here honestly reads "nothing to run").
 * @param {unknown} raw @returns {number}
 */
export function notebookEntryCount(raw) {
  if (typeof raw !== 'string') return 0
  return raw.split('\n').filter((line) => line.trim() !== '').length
}

/**
 * Python truthiness over JSON-decodable values -- what
 * `bool(row_raw.get("on", True))` sees in nodes_picker.py
 * `_parse_selection`. Deliberately NOT JS `Boolean()`: an empty array or
 * empty object is FALSY in Python (`bool([]) is False`) but truthy in JS,
 * and `null` maps to Python's `None` (falsy).
 * @param {unknown} value @returns {boolean}
 */
function pythonTruthy(value) {
  if (value === null || value === undefined) return false
  if (Array.isArray(value)) return value.length > 0
  if (typeof value === 'object') return Object.keys(value).length > 0
  return Boolean(value) // booleans, JSON numbers (0/-0 falsy), strings ('' falsy)
}

/** Python `float(<str>)` grammar: optional sign; inf/infinity/nan; or a
 * decimal with optional single underscores BETWEEN digits and an optional
 * exponent -- what nodes_picker.py `_coerce_strength`'s `float()` call
 * accepts from a string, after whitespace stripping. */
const PY_FLOAT_RE = new RegExp(
  '^[+-]?(?:inf(?:inity)?|nan' +
    '|(?:\\d(?:_?\\d)*)?\\.\\d(?:_?\\d)*(?:[eE][+-]?\\d(?:_?\\d)*)?' +
    '|\\d(?:_?\\d)*\\.?(?:[eE][+-]?\\d(?:_?\\d)*)?)$',
  'i'
)

/**
 * nodes_picker.py `_coerce_strength` mirrored: a number or `null` when
 * *value* cannot coerce (the row is then SKIPPED upstream). `bool` is
 * rejected explicitly (Python's bool-is-an-int would otherwise coerce);
 * JSON numbers always coerce; strings coerce per Python `float()`'s own
 * grammar (whitespace-stripped, inf/infinity/nan and digit-separating
 * underscores included -- `Number()` alone would wrongly reject 'inf' and
 * wrongly accept ''); anything else (null, arrays, objects) is Python
 * `float()`'s TypeError, i.e. `null` here.
 * @param {unknown} value @returns {number|null}
 */
function coerceStrength(value) {
  if (typeof value === 'boolean') return null
  if (typeof value === 'number') return value
  if (typeof value !== 'string') return null
  const trimmed = value.trim()
  if (!PY_FLOAT_RE.test(trimmed)) return null
  const plain = trimmed.replace(/_/g, '').toLowerCase()
  if (plain.endsWith('nan')) return NaN
  if (plain.endsWith('inf') || plain.endsWith('infinity')) {
    return plain.startsWith('-') ? -Infinity : Infinity
  }
  return Number(plain)
}

/**
 * Picker `lora_stack` row count: enabled rows of the `selection` JSON,
 * mirroring nodes_picker.py `_parse_selection` byte-for-byte -- that
 * (not picker.js's view-side normalization) is what executes:
 * - the whole value degrades to 0 rows on malformed JSON, a non-object
 *   root, or a non-list `loras`;
 * - a non-object row, a row without a non-empty string `file`, a row
 *   whose `strength` can't coerce (`coerceStrength`, bool rejected), and
 *   a row whose PRESENT non-null `strength_clip` can't coerce are each
 *   SKIPPED -- and a skipped row never enters the dedupe set, so a later
 *   valid row for the same file still counts;
 * - dedupe is on the RAW `file` string (first parsed occurrence wins) --
 *   NO separator normalization: 'sub\\a.st' and 'sub/a.st' are two rows;
 * - `on` defaults true when ABSENT; when present it is Python truthiness
 *   (`pythonTruthy`): 0 / null / '' / [] / {} all mean DISABLED.
 * @param {unknown} raw @returns {number}
 */
export function pickerEnabledRowCount(raw) {
  if (typeof raw !== 'string' || raw === '') return 0
  let parsed
  try {
    parsed = JSON.parse(raw)
  } catch {
    return 0
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return 0
  if (!Array.isArray(parsed.loras)) return 0
  const seen = new Set()
  let count = 0
  for (const row of parsed.loras) {
    if (!row || typeof row !== 'object' || Array.isArray(row)) continue
    const file = row.file
    if (typeof file !== 'string' || file === '') continue
    if (seen.has(file)) continue
    const strength = coerceStrength(
      Object.prototype.hasOwnProperty.call(row, 'strength') ? row.strength : 1.0
    )
    if (strength === null) continue
    const strengthClipRaw = row.strength_clip
    if (strengthClipRaw !== null && strengthClipRaw !== undefined) {
      if (coerceStrength(strengthClipRaw) === null) continue
    }
    seen.add(file)
    const on = Object.prototype.hasOwnProperty.call(row, 'on') ? row.on : true
    if (pythonTruthy(on)) count += 1
  }
  return count
}

/**
 * Python's builtin `round()` to an integer -- round-half-to-EVEN, the
 * rounding nodes_sweep.py `_step_values` actually uses. NOT `Math.round`
 * (half toward +Infinity): `(2.5 - 0) / 1` is exactly 2.5 and Python
 * rounds it to 2 (=> 3 steps) where Math.round says 3 (=> 4 steps).
 * Exact for the doubles this sees: `x - Math.floor(x)` is exact in
 * IEEE-754, so the halfway comparison never suffers rounding error of
 * its own, and JS and Python share IEEE-754 arithmetic bit for bit.
 * @param {number} x @returns {number}
 */
function roundHalfToEven(x) {
  const floor = Math.floor(x)
  const diff = x - floor
  if (diff > 0.5) return floor + 1
  if (diff < 0.5) return floor
  return floor % 2 === 0 ? floor : floor + 1
}

/**
 * EPS LoRA Iterator swept-VALUE count -- nodes_sweep.py `_step_values`
 * mirrored: degenerate inputs (non-positive increment, max < min) collapse
 * to a single value; otherwise both endpoints inclusive with a step count
 * from Python `round()` (`roundHalfToEven`, bit-identical to the
 * backend's `round((max_v - min_v) / increment) + 1`).
 * @param {number} minV @param {number} maxV @param {number} increment
 * @returns {number}
 */
export function iteratorValueCount(minV, maxV, increment) {
  if (!(increment > 0) || maxV < minV) return 1
  return Math.max(1, roundHalfToEven((maxV - minV) / increment) + 1)
}

/** First non-null link among *inputs* -- reroutes' single input has no
 * stable name across implementations, so position is the seam. */
function firstLink(inputs) {
  for (const link of Object.values(inputs || {})) {
    if (link) return link
  }
  return null
}

/**
 * Follows *link* through any chain of reroute nodes to the real source.
 * Returns `{id, node, slot}` (node null when the origin id is missing from
 * the snapshot -- counted as unknown by the caller), or null when the
 * chain dead-ends at an unwired reroute (the serialized prompt then simply
 * has no link, i.e. the input is effectively UNWIRED).
 */
function resolveSource(snapshot, link) {
  let current = link
  const seen = new Set()
  while (current && current.originId != null) {
    const id = String(current.originId)
    if (seen.has(id)) return null // a reroute loop carries nothing
    const node = snapshot.nodes?.[id]
    if (!node) return { id, node: null, slot: current.originSlot ?? 0 }
    if (!REROUTE_CLASSES.has(node.classType)) return { id, node, slot: current.originSlot ?? 0 }
    seen.add(id)
    current = firstLink(node.inputs)
  }
  return null
}

/**
 * nodes_switcher.py `_switcher_is_statically_all_off` mirrored onto the
 * snapshot: true when *node* is one of the four EPS switcher classes that
 * PROVABLY emits nothing -- every `<prefix>_N` slot that would survive
 * into the serialized prompt is literally toggled `false`, or nothing is
 * wired at all. "Survives into the prompt" is the load-bearing clause:
 * the backend reads the PROMPT, where reroutes are already collapsed, so
 * a slot whose reroute chain DEAD-ENDS carries no link there and must
 * not count as wired here either (`resolveSource` null). A `toggles`
 * arriving as a LINK (or as anything but a string) is not statically
 * knowable -- false, same as the backend: unknown means "assume it
 * produces something". This is exactly the case -- and the ONLY case --
 * a consuming switcher's check_lazy_status skips, so the all-off
 * upstream is never requested, never runs, and never emits its blocker.
 * @param {{nodes: object}} snapshot
 * @param {{classType: string, widgets?: object, inputs?: object}} node
 * @returns {boolean}
 */
function switcherIsStaticallyAllOff(snapshot, node) {
  const prefix = SWITCHER_PREFIXES[node.classType]
  if (!prefix) return false
  if (node.inputs?.toggles) return false // wired, not a literal widget value
  const raw = node.widgets?.toggles
  if (raw !== undefined && raw !== null && typeof raw !== 'string') return false
  const toggles = parseToggles(raw)
  const re = new RegExp(`^${prefix}_(\\d+)$`)
  for (const [name, link] of Object.entries(node.inputs || {})) {
    if (!link || !re.test(name)) continue
    if (!resolveSource(snapshot, link)) continue // dead-end reroute: no link in the prompt
    if (toggles[name] !== false) return false // a wired slot not literally toggled off
  }
  return true
}

/** Chained-multiplier OUTPUT slot -> the INPUT that must be wired for that
 * output to carry values, positionally off nodes_cross_sweep.py's
 * RETURN_NAMES ('model','clip','image','text','save_prefix','label','vae')
 * -- text (3) and save_prefix (4) are always live. Consuming any of THESE
 * outputs while the matching input is unwired trips run()'s v0.51.0
 * dead-output ValueError (`_consumed_output_slots` + the dead_outputs
 * block): every run on that wire would be a silent blocker, so the
 * backend fails the queue naming both ends. */
const DEAD_OUTPUT_INPUTS = { 0: 'model', 1: 'clip', 2: 'image', 5: 'label', 6: 'vae' }

/**
 * How many list elements the node behind *link* emits -- the per-source
 * count rules from the file header, PROPAGATED: list lengths flow through
 * switchers (sum), map-over-list nodes (max), and the Iterator (multiply),
 * so a 3-fan switcher two hops upstream still counts 3 here. Returns null
 * for an unwired input (null *link*, or a reroute chain that dead-ends);
 * otherwise `{count, atLeast, srcId}` where `atLeast: true` means *count*
 * is a floor (an UNKNOWN source participating as 1, §6.10's
 * never-false-confidence rule) and `srcId` is the RESOLVED (post-reroute)
 * origin node id -- the same origin the backend's v0.57.0 same-origin
 * guard reads from the serialized prompt, where reroutes no longer exist.
 * A result of `{count: 0, atLeast: false}` means KNOWN-BLOCKED (the
 * header's BLOCKED/EMPTY PROPAGATION rules): the upstream's output is
 * `[ExecutionBlocker]`, which blocks any consumer outright. An `error`
 * property, when present, is a queue-time failure detected at the
 * consumption site (the chained-multiplier dead-output guard) and is
 * propagated verbatim up every recursing branch for the readout to paint.
 * @param {{nodes: object}} snapshot
 * @param {{originId: string|number, originSlot?: number}|null} link
 * @param {Set<string>} path - node ids already on this recursion path (the
 *   cycle guard for EVERY recursing branch, not just chained multipliers)
 */
function sourceCount(snapshot, link, path) {
  if (!link) return null
  const resolved = resolveSource(snapshot, link)
  if (!resolved) return null
  const { id, node } = resolved
  if (!node) return { count: 1, atLeast: true, srcId: id }

  const type = node.classType
  const prefix = SWITCHER_PREFIXES[type]
  if (prefix) {
    // §6.4/§6.4b: execute() FLATTENS each enabled slot's upstream LIST
    // into the output (nodes_switcher.py INPUT_IS_LIST +
    // `enabled_values.extend`), so the emitted length is the SUM of the
    // enabled+wired slots' upstream counts -- NOT the slot count: a slot
    // fed by a 3-fan sibling switcher contributes 3, an ordinary loader
    // contributes 1. atLeast propagates from any slot; zero enabled slots
    // stays the whole-node blocker path (count 0).
    //
    // A KNOWN-zero slot (header: BLOCKED/EMPTY PROPAGATION) only
    // "contributes nothing" in ONE shape -- the DIRECT post-reroute
    // upstream is a sibling EPS switcher that is STATICALLY all-off, the
    // exact case check_lazy_status never requests, so that upstream never
    // runs and never emits its blocker. EVERY OTHER known-zero upstream
    // (an empty-selection Checkpoint Switcher, a NESTED-empty switcher
    // whose own wired slots merely lead nowhere, a blocked mapped loader)
    // DOES run -- or has its blocker propagated to it -- and one blocker
    // element in any consumed input list blocks the whole consumer
    // (execution.py process_inputs): this entire switcher collapses to
    // {count: 0, atLeast: false}.
    if (path.has(id)) return { count: 1, atLeast: true, srcId: id }
    const sub = new Set([...path, id])
    let count = 0
    let atLeast = false
    for (const slotLink of enabledSlotLinks(node, prefix)) {
      const inner = sourceCount(snapshot, slotLink, sub)
      if (!inner) continue // dead-end reroute: the prompt has no link, slot unwired
      if (inner.error) return { count: 1, atLeast: true, srcId: id, error: inner.error }
      if (inner.count === 0 && !inner.atLeast) {
        const direct = resolveSource(snapshot, slotLink)
        if (direct?.node && switcherIsStaticallyAllOff(snapshot, direct.node)) {
          continue // check_lazy_status's lazy skip: never requested, never blocks
        }
        return { count: 0, atLeast: false, srcId: id }
      }
      count += inner.count
      atLeast = atLeast || inner.atLeast
    }
    return { count, atLeast, srcId: id }
  }
  if (type === 'EPSCheckpointSwitcher') {
    // §6.12: every output is selection-length, in selection order --
    // intrinsic (name widgets in, no wired list inputs to propagate).
    return { count: checkpointSelectionCount(node.widgets?.selection), atLeast: false, srcId: id }
  }
  if (type === 'LoraLibraryNotebook') {
    // §6.1: text/name are one element per selected entry line -- intrinsic.
    // ZERO lines is NOT the blocked/empty family (round-3 review): the
    // notebook's read_entry RAISES "no entry selected" the moment it
    // executes, so the real queue FAILS -- statically knowable from the
    // entry widget, painted as the error it is.
    const lines = notebookEntryCount(node.widgets?.entry)
    if (lines === 0) {
      return {
        count: 0,
        atLeast: false,
        srcId: id,
        error: 'EPS Prompt Notebook has no entry selected — the queue will fail'
      }
    }
    return { count: lines, atLeast: false, srcId: id }
  }
  if (type === 'EPSImageGrid') {
    // §6.6: Collect passes ONLY the wired image through and a Run
    // Multiplier fed mid-collection is counted per the §6.10 contract as
    // 0; Emit fans the whole buffer out -- SERVER state. The adapter's
    // injected `imageGridCount` (the live node's own imgs preview) is a
    // client-side ECHO of that server state -- it can lag or lie, so it
    // upgrades the count from "1, unknowable" to "N, still a floor":
    // atLeast stays true and the readout keeps its `≥`.
    if (node.widgets?.mode === 'Collect') return { count: 0, atLeast: false, srcId: id }
    if (Number.isFinite(node.imageGridCount)) {
      return { count: node.imageGridCount, atLeast: true, srcId: id }
    }
    return { count: 1, atLeast: true, srcId: id }
  }
  if (type === 'LoraLibrarySweep') {
    // None-passthrough (round-3 review): the iterator's clip output just
    // passes its clip INPUT through -- unwired, that output is a list of
    // None, which run()'s _as_clean_list strips to empty: steps=0, a
    // silent 0-run queue, not a live list.
    if (resolved.slot === 1) {
      const clipLink = node.inputs?.clip ?? null
      if (!clipLink || !resolveSource(snapshot, clipLink)) {
        return { count: 0, atLeast: false, srcId: id }
      }
    }
    // §6.8, two factors multiplied:
    // 1. The PLAN length from the node's own min/max/increment/mode
    //    widgets. "All together" is n_steps regardless of the stack;
    //    per-lora (the default, and nodes_sweep.py's fallback for any
    //    other mode string) is n_loras x n_steps, so the stack length
    //    matters: follow the lora_stack wire -- a Picker's enabled rows
    //    are countable from its selection widget, anything else
    //    (Apply-Set reads a file on disk) is UNKNOWABLE. For an
    //    unknowable stack the only count build_sweep_plan GUARANTEES is
    //    its empty-stack sentinel's single passthrough (>= 1 always) --
    //    an actually-empty stack sweeps ONCE, not `values` times -- so
    //    the plan floors at 1, atLeast: advertising `values x pairs` as
    //    a floor would overclaim (round-2 review). An EMPTY per-lora
    //    Picker stack is that same sentinel, i.e. exactly 1, never 0.
    // 2. The MAP factor: nodes_sweep.py has no INPUT_IS_LIST (only
    //    OUTPUT_IS_LIST), so core maps sweep() over its model/clip list
    //    inputs and CONCATENATES the per-call plans -- max over the wired
    //    model/clip counts, floor 1. (lora_stack maps too in principle,
    //    but its producers -- Picker, Apply-Set -- emit ONE stack, so it
    //    contributes no fan; the widgets never map.)
    if (path.has(id)) return { count: 1, atLeast: true, srcId: id }
    const sub = new Set([...path, id])
    const w = node.widgets || {}
    const values = iteratorValueCount(toNumber(w.min, 0), toNumber(w.max, 1), toNumber(w.increment, 0.1))
    let plan = values
    let planAtLeast = false
    if (w.mode !== ITERATOR_MODE_ALL_TOGETHER) {
      const stack = resolveSource(snapshot, node.inputs?.lora_stack ?? null)
      if (stack?.node?.classType === 'EPSLoraPicker') {
        const rows = pickerEnabledRowCount(stack.node.widgets?.selection)
        plan = rows === 0 ? 1 : rows * values
      } else {
        plan = 1 // build_sweep_plan's sentinel is the only guaranteed floor
        planAtLeast = true
      }
    }
    let mapLen = 1
    let mapAtLeast = false
    for (const name of ['model', 'clip']) {
      const inner = sourceCount(snapshot, node.inputs?.[name] ?? null, sub)
      if (!inner) continue
      if (inner.error) return { count: 1, atLeast: true, srcId: id, error: inner.error }
      if (inner.count === 0 && !inner.atLeast) {
        // A blocked/empty upstream (all-off switcher's ExecutionBlocker)
        // blocks this whole node -- it emits nothing.
        return { count: 0, atLeast: false, srcId: id }
      }
      mapLen = Math.max(mapLen, inner.count)
      mapAtLeast = mapAtLeast || inner.atLeast
    }
    return { count: plan * mapLen, atLeast: planAtLeast || mapAtLeast, srcId: id }
  }
  if (type === CLASS_ID) {
    // A chained §6.10 multiplier. FIRST, the v0.51.0 dead-output guard
    // mirrored at the consumption site (nodes_cross_sweep.py
    // `_consumed_output_slots` + the dead_outputs block): consuming its
    // model/clip/image/label/vae OUTPUT while the matching INPUT is
    // unwired (no link, or a reroute chain that dead-ends -- the prompt
    // then has no link either) makes the INNER node's run() raise, so the
    // queue WILL fail -- painted on THIS consumer's readout, before the
    // queue. Checked before the cycle guard: it is a static consumption
    // property, no recursion involved.
    const backing = DEAD_OUTPUT_INPUTS[resolved.slot]
    let deadOutputError = null
    if (backing !== undefined) {
      const backingLink = node.inputs?.[backing] ?? null
      if (!backingLink || !resolveSource(snapshot, backingLink)) {
        deadOutputError =
          `chained ${NODE_TITLE}: its ${backing} output is consumed but its ` +
          `${backing} input is not wired, so every run on that wire would be ` +
          'silently skipped — the queue will fail'
      }
    }
    // Recursion, with the path as cycle guard -- a multiplier already on
    // this path is unknowable (contributes 1, floors the count) instead
    // of recursing forever; an upstream that will itself ERROR at queue
    // time is equally unknowable from here (it paints its own readout
    // red).
    if (path.has(id)) {
      return deadOutputError
        ? { count: 1, atLeast: true, srcId: id, error: deadOutputError }
        : { count: 1, atLeast: true, srcId: id }
    }
    const est = estimateInner(snapshot, id, new Set([...path, id]))
    // A KNOWN-blocked inner never runs, so its v0.51.0 ValueError never
    // raises and the real queue SUCCEEDS with 0 runs -- zero-collapse
    // outranks the dead-output fail paint, the same suppress-lying-
    // fail-paints rule the header states (round-3 review). An UNCERTAIN
    // zero (atLeast) keeps the error: the inner may run and raise.
    if (est.total === 0 && !est.atLeast && !est.error) {
      return { count: 0, atLeast: false, srcId: id }
    }
    if (deadOutputError) return { count: 1, atLeast: true, srcId: id, error: deadOutputError }
    if (est.error) return { count: 1, atLeast: true, srcId: id }
    if (est.total === 0) return { count: 0, atLeast: false, srcId: id }
    return { count: est.total, atLeast: est.atLeast, srcId: id }
  }
  // None-passthrough outputs (round-3 review): EPSLoraPicker and
  // LoraLibraryApplySet PASS their model/clip inputs through -- consumed
  // with the matching input unwired, the output list is [None], which
  // run()'s _as_clean_list strips to empty: wired-but-empty steps=0, a
  // silent 0-run queue. Counting those as live overclaimed with full
  // confidence.
  if (
    (type === 'EPSLoraPicker' || type === 'LoraLibraryApplySet') &&
    (resolved.slot === 0 || resolved.slot === 1)
  ) {
    const backing = resolved.slot === 0 ? 'model' : 'clip'
    const backingLink = node.inputs?.[backing] ?? null
    if (!backingLink || !resolveSource(snapshot, backingLink)) {
      return { count: 0, atLeast: false, srcId: id }
    }
  }
  if (CORE_SINGLE_CLASSES.has(type)) {
    // Map-over-list (see CORE_SINGLE_CLASSES' own comment): none of these
    // declare INPUT_IS_LIST, so core runs them once per element of their
    // LONGEST list input, broadcasting the rest (execution.py's
    // map-over-list), and each run emits ONE element -- output length =
    // max(1, max over wired inputs' counts). atLeast propagates. A
    // 0-count wired input is a blocker upstream: core blocks THIS node
    // outright, so it emits nothing.
    if (path.has(id)) return { count: 1, atLeast: true, srcId: id }
    const sub = new Set([...path, id])
    let count = 1
    let atLeast = false
    for (const inputLink of Object.values(node.inputs || {})) {
      const inner = sourceCount(snapshot, inputLink ?? null, sub)
      if (!inner) continue
      if (inner.error) return { count: 1, atLeast: true, srcId: id, error: inner.error }
      if (inner.count === 0 && !inner.atLeast) return { count: 0, atLeast: false, srcId: id }
      count = Math.max(count, inner.count)
      atLeast = atLeast || inner.atLeast
    }
    return { count, atLeast, srcId: id }
  }
  // Anything else: UNKNOWN -- contributes 1 and downgrades to `≥`.
  return { count: 1, atLeast: true, srcId: id }
}

/** Pluralizes for the readout line ("1 pair" / "2 pairs"). */
function plural(count, noun) {
  return `${count} ${noun}${count === 1 ? '' : 's'}`
}

/**
 * The pre-queue run-count estimate for the `EPSCrossSweep` node *nodeId*
 * in *snapshot* -- the multiplier's own math from nodes_cross_sweep.py
 * `run()` mirrored over per-source counts (file header).
 *
 * @param {{nodes: Record<string, {classType: string, widgets?: object,
 *   inputs?: object, imageGridCount?: number}>}} snapshot
 * @param {string|number} nodeId
 * @returns {{total: number, atLeast: boolean, steps: number,
 *   stepsAtLeast: boolean, pairs: number, pairsAtLeast: boolean,
 *   unknowns: string[], error: string|null, breakdown: string}}
 */
export function estimateRuns(snapshot, nodeId) {
  return estimateInner(snapshot, String(nodeId), new Set([String(nodeId)]))
}

function estimateInner(snapshot, nodeId, path) {
  const result = {
    total: 0,
    atLeast: false,
    steps: 0,
    stepsAtLeast: false,
    pairs: 0,
    pairsAtLeast: false,
    unknowns: [],
    error: null,
    breakdown: ''
  }
  const node = snapshot?.nodes?.[String(nodeId)]
  if (!node || node.classType !== CLASS_ID) {
    result.error = `node ${nodeId} is not an ${CLASS_ID} in this snapshot`
    return result
  }
  const widgets = node.widgets || {}
  const inputs = node.inputs || {}
  const pairMultiply = widgets.pair_mode === 'multiply'
  const sweepMultiply = widgets.sweep_mode === 'multiply'

  const unknowns = new Set()

  // --- Every consumed input's source count FIRST: the sweep members
  // (run()'s v0.49.1/v0.57.0 rules), the pair side, AND `name`. run()
  // only ever reads names for save_prefix, so `name` never enters the
  // pair MATH below -- but it IS a consumed input list, and one
  // ExecutionBlocker element in ANY consumed list blocks this whole node
  // (header: BLOCKED/EMPTY PROPAGATION), so its zero/blocked state and
  // its atLeast floor must propagate like every other member's.
  const members = {}
  for (const name of ['model', 'clip', 'label', 'vae']) {
    members[name] = sourceCount(snapshot, inputs[name] ?? null, path)
    if (members[name]?.atLeast) unknowns.add(name)
  }
  const textSrc = sourceCount(snapshot, inputs.text ?? null, path)
  const imageSrc = sourceCount(snapshot, inputs.image ?? null, path)
  const nameSrc = sourceCount(snapshot, inputs.name ?? null, path)
  if (textSrc?.atLeast) unknowns.add('text')
  if (imageSrc?.atLeast) unknowns.add('image')
  if (nameSrc?.atLeast) unknowns.add('name')
  const sources = { ...members, text: textSrc, image: imageSrc, name: nameSrc }

  // A chained multiplier's dead-output consumption (sourceCount's
  // CLASS_ID branch) is a queue-time ValueError raised the moment the
  // INNER node runs -- it outranks every paint below.
  for (const src of Object.values(sources)) {
    if (src?.error) {
      result.error = src.error
      return result
    }
  }

  // --- ZERO-COLLAPSE FIRST (the ordering IS the contract -- round-2
  // review): a wired input whose source is KNOWN-zero means its upstream
  // emits [ExecutionBlocker] (or a genuinely empty list), so this node
  // NEVER RUNS -- core blocks it, the queue SUCCEEDS with 0 runs, and
  // run()'s aligned-conflict/same-origin ValueErrors are never raised.
  // Painting "the queue will fail" over that state would be a lie: both
  // checks below are suppressed, and the totals block at the bottom
  // names the empty/blocked input(s) instead.
  const zeroNames = Object.entries(sources)
    .filter(([, src]) => src && src.count === 0 && !src.atLeast)
    .map(([name]) => name)

  const wired = Object.entries(members).filter(([, src]) => src)
  const sweepWired = wired.length > 0
  const vae = members.vae

  if (zeroNames.length === 0) {
    // Aligned agreement check, run()'s exact scope: ALL wired members in
    // aligned mode, model/clip/label only in multiply mode (vae is then
    // its own independent axis). Length 1 broadcasts; only KNOWN lengths
    // > 1 can disagree (an unknown participates as 1 and can never
    // conflict).
    const checked = wired.filter(([name]) => !sweepMultiply || name !== 'vae')
    const knownMulti = checked.filter(([, src]) => !src.atLeast && src.count > 1)
    if (new Set(knownMulti.map(([, src]) => src.count)).size > 1) {
      const conflict = knownMulti.map(([name, src]) => `${name}=${src.count}`).join(' vs ')
      // Short form of run()'s v0.49.1 conflict error -- same failure, on
      // the node BEFORE the queue.
      result.error = `sweep lengths disagree: ${conflict} — the queue will fail`
      return result
    }

    // run()'s v0.57.0 same-origin guard: in multiply mode a >1 vae wired
    // from the SAME node as any model-axis member is a hard error (those
    // lists are index-aligned by construction). Origins compare
    // post-reroute -- exactly what the serialized prompt's link origins
    // resolve to.
    if (sweepMultiply && vae && vae.count > 1 && vae.srcId != null) {
      for (const name of ['model', 'clip', 'label']) {
        const member = members[name]
        if (member && member.srcId != null && member.srcId === vae.srcId) {
          result.error =
            `sweep_mode multiply: vae and ${name} are wired from the same node — the queue will fail`
          return result
        }
      }
    }
  }

  // Steps -- run()'s exact ladder: no sweep wired = one null step; any
  // wired-but-EMPTY list collapses to 0 (the whole-node blocker path);
  // multiply = model-axis steps x vaes; aligned = the agreed length
  // (broadcasting 1s never raise it, the agreement check above already
  // ruled out competing >1s, and an unknown's floor keeps `≥` honest).
  result.stepsAtLeast = wired.some(([, src]) => src.atLeast)
  let steps
  if (!sweepWired) {
    steps = 1
  } else if (wired.some(([, src]) => !src.atLeast && src.count === 0)) {
    steps = 0
  } else if (sweepMultiply) {
    const axis1Len = wired
      .filter(([name]) => name !== 'vae')
      .reduce((best, [, src]) => Math.max(best, src.count), 1)
    const vaeLen = vae ? Math.max(vae.count, 1) : 1
    steps = axis1Len * vaeLen
  } else {
    steps = wired.reduce((best, [, src]) => Math.max(best, src.count), 1)
  }

  // --- Pair side: run()'s v0.49.0 modes. text is the one REQUIRED input;
  // unwired image = text-only pairs; multiply = every image x every text;
  // paired = index-aligned (min-clamped, exactly what run() then uses).
  // (textSrc/imageSrc were counted with every other input, above.)
  result.pairsAtLeast = Boolean(textSrc?.atLeast || imageSrc?.atLeast)
  let pairs
  if (!textSrc) {
    pairs = 0 // text unwired: nothing to pair (and validation would fail)
  } else if (!imageSrc) {
    pairs = textSrc.count
  } else if (pairMultiply) {
    pairs = imageSrc.count * textSrc.count
  } else {
    pairs = Math.min(imageSrc.count, textSrc.count)
  }

  result.steps = steps
  result.pairs = pairs
  result.unknowns = [...unknowns]
  if (zeroNames.length > 0 || steps === 0 || pairs === 0) {
    // Zero runs, queue SUCCEEDS -- two roads in: a KNOWN-blocked member
    // (zeroNames non-empty) means core blocks this node before run() ever
    // executes, so the readout names the culprit input(s); otherwise
    // run()'s own empty-safety path returns blockers (steps/pairs 0).
    result.total = 0
    result.atLeast = false
    result.breakdown =
      zeroNames.length > 0
        ? `nothing to run (${zeroNames.join(', ')} input${
            zeroNames.length === 1 ? ' is' : 's are'
          } empty/blocked)`
        : 'nothing to run'
    return result
  }
  result.total = steps * pairs
  // `name` never enters the steps/pairs math, but an unknowable name
  // source still floors the TOTAL (it could yet turn out blocked), so its
  // atLeast propagates like either group's.
  result.atLeast = result.stepsAtLeast || result.pairsAtLeast || Boolean(nameSrc?.atLeast)
  result.breakdown = `${plural(steps, 'sweep step')} × ${plural(pairs, 'pair')}`
  return result
}

/**
 * The readout line for an `estimateRuns()` result: `{text, cls}` where
 * *cls* is `''` (plain), `'eps-rc-warn'` (a `≥` floor or a zero-run
 * setup), or `'eps-rc-error'` (the queue-time failure, painted early).
 * A zero-run line carries the estimate's own breakdown, which names the
 * empty/blocked input when a KNOWN-blocked member caused the collapse
 * (estimateInner's zero-collapse) -- plain 'nothing to run' otherwise.
 * @param {ReturnType<typeof estimateRuns>} est
 * @returns {{text: string, cls: string}}
 */
export function formatReadout(est) {
  if (est.error) return { text: est.error, cls: 'eps-rc-error' }
  if (est.total === 0) {
    return { text: `Runs: 0 — ${est.breakdown || 'nothing to run'}`, cls: 'eps-rc-warn' }
  }
  const base = `${plural(est.steps, 'sweep step')} × ${plural(est.pairs, 'pair')}`
  if (est.atLeast) {
    const who = est.unknowns.length ? ` — ${est.unknowns.join(', ')} source unknown` : ''
    return { text: `Runs: ≥ ${est.total} — ${base}${who}`, cls: 'eps-rc-warn' }
  }
  return { text: `Runs: ${est.total} — ${base}`, cls: '' }
}

// ---------------------------------------------------------------------------
// Live-graph adapter (the estimator's only bridge to litegraph)
// ---------------------------------------------------------------------------

/** One link's `{originId, originSlot}` from the graph's link table --
 * tolerant of both the classic `links[id]` object map and a Map-shaped
 * store on newer frontends. */
function resolveGraphLink(graph, linkId) {
  if (linkId == null) return null
  const links = graph?.links ?? graph?._links
  if (!links) return null
  const link = typeof links.get === 'function' ? links.get(linkId) : links[linkId]
  if (!link || link.origin_id == null) return null
  return { originId: String(link.origin_id), originSlot: link.origin_slot ?? 0 }
}

/**
 * A PLAIN snapshot of *graph* in `estimateRuns()`'s shape (file header) --
 * classType, widget values by name, and input links by input name, plus
 * the one live-only injection: an Image Grid node's `imgs` length as
 * `imageGridCount` (the buffer preview the frontend already holds), so an
 * Emit-mode grid counts exactly instead of downgrading to `≥`.
 * @param {object} graph - `app.graph` (or a node's own `.graph`)
 * @returns {{nodes: Record<string, object>}}
 */
export function snapshotFromGraph(graph) {
  const snapshot = { nodes: {} }
  const nodes = graph?._nodes || graph?.nodes || []
  for (const node of nodes) {
    if (!node || node.id == null) continue
    const classType =
      node.comfyClass || (node.constructor && node.constructor.comfyClass) || node.type || ''
    const widgets = {}
    for (const widget of node.widgets || []) {
      if (widget && typeof widget.name === 'string') widgets[widget.name] = widget.value
    }
    const inputs = {}
    for (const input of node.inputs || []) {
      if (!input || typeof input.name !== 'string') continue
      inputs[input.name] = resolveGraphLink(graph, input.link)
    }
    const entry = { classType, widgets, inputs }
    if (classType === 'EPSImageGrid' && Number.isFinite(node.imgs?.length)) {
      entry.imageGridCount = node.imgs.length
    }
    snapshot.nodes[String(node.id)] = entry
  }
  return snapshot
}

// ---------------------------------------------------------------------------
// Toast listener -- the execution-time TRUTH (§6.10 layer 2)
// ---------------------------------------------------------------------------

/** Best-effort toast via this pack's established `app.extensionManager?.
 * toast?.add?.(...)` convention (image_grid.js `notifyClipboard`) -- never
 * throws, falls back to `console.info` when the toast surface is missing. */
function toastInfo(summary) {
  try {
    if (app.extensionManager?.toast?.add) {
      app.extensionManager.toast.add({ severity: 'info', summary, life: 8000 })
      return
    }
  } catch (error) {
    console.warn(PREFIX, 'toast failed', error)
  }
  console.info(PREFIX, summary)
}

//: Guards init()'s module-scope listener so it is only ever attached once,
//: no matter how many times init() runs (image_grid.js's
//: `executionRefreshListenerInstalled` idiom).
let countListenerInstalled = false

/**
 * Installs ONE module-scope listener for the backend's execution-time
 * count event and toasts the definitive number -- run() sends it the
 * moment the multiplier executes, long before samplers grind, so a
 * wrong number can be cancelled cheaply.
 */
export function init() {
  if (countListenerInstalled) return
  countListenerInstalled = true
  api.addEventListener(EVENT_NAME, (event) => {
    const detail = event?.detail || {}
    const total = detail.total
    const steps = detail.steps
    const pairs = detail.pairs
    if (![total, steps, pairs].every(Number.isFinite)) return
    toastInfo(`${NODE_TITLE}: ${total} run(s) (${steps} sweep step(s) × ${pairs} pair(s))`)
  })
}

// ---------------------------------------------------------------------------
// On-node readout -- the pre-queue ESTIMATE (§6.10 layer 1)
// ---------------------------------------------------------------------------

let stylesInjected = false

const CSS_TEXT = `
.eps-rc-root { display: flex; align-items: center; width: 100%; height: 100%; box-sizing: border-box; overflow: hidden; }
.eps-rc-line { flex: 1 1 auto; min-width: 0; font-family: inherit; font-size: 11px; color: var(--descrip-text, #999); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; padding: 0 4px; }
.eps-rc-warn { color: var(--warning-text, #e6a23c); }
.eps-rc-error { color: var(--error-text, #ff4444); }
`

function injectStyles() {
  if (stylesInjected) return
  stylesInjected = true
  if (document.getElementById(STYLE_TAG_ID)) return
  const style = document.createElement('style')
  style.id = STYLE_TAG_ID
  style.textContent = CSS_TEXT
  document.head.appendChild(style)
}

/** @param {object} node @returns {string|null} */
function nodeClassOf(node) {
  if (!node) return null
  if (node.comfyClass) return node.comfyClass
  if (node.constructor && node.constructor.comfyClass) return node.constructor.comfyClass
  return null
}

/** Re-estimates from the live graph and repaints the line -- only when the
 * text actually changed, so a busy canvas never thrashes the DOM. */
function recompute(state) {
  const graph = state.node.graph || app.graph
  if (!graph) return
  const est = estimateRuns(snapshotFromGraph(graph), String(state.node.id))
  const view = formatReadout(est)
  if (view.text === state.lastText && view.cls === state.lastCls) return
  state.lastText = view.text
  state.lastCls = view.cls
  state.lineEl.className = view.cls ? `eps-rc-line ${view.cls}` : 'eps-rc-line'
  state.lineEl.textContent = view.text
  state.lineEl.title = view.text
}

/** The §6.3 throttle: at most one recompute per READOUT_MIN_INTERVAL_MS,
 * shared by ALL of the refresh triggers below (controller.js
 * `_heartbeat()`). */
function maybeRecompute(state) {
  const now = Date.now()
  if (now - state.lastStamp < READOUT_MIN_INTERVAL_MS) return
  state.lastStamp = now
  recompute(state)
}

/**
 * Chains a throttled recompute AFTER *original* -- the one wrapper every
 * refresh trigger goes through, so they all share the SAME
 * `maybeRecompute` throttle. Wrap, never replace: *original* (a litegraph
 * hook or a widget callback, possibly undefined) keeps running first with
 * its own `this`/arguments and its return value is passed through; the
 * recompute itself can never throw out of the caller (try/catch).
 * @param {Function|undefined} original @param {object} state
 * @returns {Function}
 */
function wrapWithRecompute(original, state) {
  return function () {
    const result = typeof original === 'function' ? original.apply(this, arguments) : undefined
    try {
      maybeRecompute(state)
    } catch (error) {
      console.warn(PREFIX, 'run-count recompute failed', error)
    }
    return result
  }
}

/** Per-node-instance attach; no-op unless *node* is an EPSCrossSweep. */
export function attach(node) {
  try {
    if (!node) return
    if (nodeClassOf(node) !== CLASS_ID) return
    if (attachedNodes.has(node)) return
    attachedNodes.add(node)
    if (typeof node.addDOMWidget !== 'function') {
      console.warn(PREFIX, 'this ComfyUI frontend has no addDOMWidget; run-count readout not attached')
      return
    }
    injectStyles()

    const lineEl = document.createElement('div')
    lineEl.className = 'eps-rc-line'
    const root = document.createElement('div')
    root.className = 'eps-rc-root'
    root.appendChild(lineEl)

    // Display-only: nothing enters the API prompt OR the workflow file --
    // picker.js attachDomWidget's exact two-flag block (§8-safe: a widget
    // value here would positionally shift widgets_values on downgrade).
    // A COMPACT STANDALONE DOM widget must size itself through computeSize
    // + computedHeight + an explicit element height -- getMinHeight/
    // getMaxHeight ALONE are ignored for this shape and the row collapses
    // to ~7px with the text clipped (owner report 2026-08-09: "cropped ...
    // about 1/3 the space it needs"). This is the exact cprb v0.5.0
    // root-cause: the notebook/picker panels never hit it because they are
    // FILL widgets riding the node's spare height. getMinHeight/
    // getMaxHeight stay as the belt-and-braces for renderers that DO honor
    // them.
    root.style.height = `${READOUT_HEIGHT}px`
    const domWidget = node.addDOMWidget(READOUT_WIDGET_NAME, READOUT_WIDGET_TYPE, root, {
      hideOnZoom: true,
      serialize: false, // excludes from the API prompt (utils/executionUtil.ts)
      getMinHeight: () => READOUT_HEIGHT,
      getMaxHeight: () => READOUT_HEIGHT
    })
    domWidget.computeSize = (width) => [width, READOUT_HEIGHT]
    domWidget.computedHeight = READOUT_HEIGHT
    // Excludes from the workflow JSON -- a DIFFERENT flag from
    // options.serialize above (notebook.js's attachDomWidget() header
    // explains why both exist).
    domWidget.serialize = false
    domWidget.serializeValue = () => undefined

    const state = { node, lineEl, lastStamp: 0, lastText: null, lastCls: null }

    // Refresh triggers (§6.3's controller idiom -- wrap, never replace,
    // never throw out of the caller; ONE shared throttle via
    // wrapWithRecompute -> maybeRecompute). No timers of this file's own,
    // no window listeners (§7.5). THREE triggers because the renderers
    // differ: onDrawForeground is the litegraph catch-all (a graph edit
    // dirties the canvas, the repaint lands here) but NEVER fires under
    // the Vue "New node design" renderer (project rule: no canvas drawing
    // there), so two triggers that exist in BOTH renderers ride along --
    // onConnectionsChange (a rewire is exactly what changes the count)
    // and the two mode widgets' own callbacks (pair_mode/sweep_mode flips
    // change the math with no rewire at all).
    node.onDrawForeground = wrapWithRecompute(node.onDrawForeground, state)
    node.onConnectionsChange = wrapWithRecompute(node.onConnectionsChange, state)
    for (const name of ['pair_mode', 'sweep_mode']) {
      const widget = (node.widgets || []).find((w) => w && w.name === name)
      if (widget) widget.callback = wrapWithRecompute(widget.callback, state)
    }

    // First paint now rather than on the first redraw -- a restored
    // workflow's widgets land before nodeCreated returns control, and the
    // next redraw-driven pass reconciles anything later (paste, undo).
    try {
      recompute(state)
    } catch (error) {
      console.warn(PREFIX, 'initial run-count estimate failed', error)
    }
  } catch (error) {
    console.warn(PREFIX, 'cross_sweep attach failed', error)
  }
}
