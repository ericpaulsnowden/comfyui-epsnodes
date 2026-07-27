/**
 * @file EPS Distributor frontend (FORMAT.md section 6.11; roadmap:
 * `research/roadmap-eps-distributor.md`). Exports the `init()`/`attach(node)`
 * hooks `web/eps_image.js` calls; `attach` no-ops for every node type other
 * than `EPSDistributor`.
 *
 * The roadmap frames this node as "EPS Switcher pointed backwards": one
 * `image` input fans out to fixed `out_1`..`out_8` (MAX_OUTPUTS) IMAGE
 * outputs, each independently gated by a hidden `toggles` JSON widget the
 * backend reads (`{"out_3": false}` = off; absent key = on). Two pieces of
 * this file are direct ports, one is genuinely new:
 *
 * 1. **Per-slot toggle draw + hit-test** -- a structural port of
 *    `eps_image/switcher.js`'s `onDrawForeground`/`onMouseDown` hand-drawn
 *    toggle boxes and its `toggles` JSON bridge (`readToggles`/`writeToggles`/
 *    `isRowEnabled`/`setRowEnabled`/`pruneToggles`), pointed at OUTPUT slots
 *    instead of INPUT slots. This redirection is the one genuinely new bit
 *    (roadmap's "Reuse inventory"), and the geometry is NOT a simple mirror
 *    of switcher.js's input-side math -- verified directly against this
 *    rig's installed `comfyui_frontend_package` 1.45.21 TS source (pulled
 *    from its own `api-*.js.map` `sourcesContent`, the same technique
 *    switcher.js/resolution.js's headers describe), not assumed:
 *      - The function that actually decides whether a canvas click reaches
 *        `node.onMouseDown` at all is `LGraphCanvas.ts`'s
 *        `_processNodeClick` (collapse-toggle -> outputs loop -> inputs
 *        loop -> `getWidgetOnPos` -> title buttons -> badges ->
 *        `node.onMouseDown` -> resize). Its OUTPUTS loop is a FIXED box,
 *        independent of the output's name/label length:
 *          ```
 *          for (const [i, output] of outputs.entries()) {
 *            const link_pos = node.getOutputPos(i)
 *            if (isInRectangle(x, y, link_pos[0] - 15, link_pos[1] - 10, 30, 20)) {
 *              linkConnector.dragNewFromOutput(graph, node, output)
 *              ...
 *              return   // <-- node.onMouseDown is never reached for this click
 *            }
 *          }
 *          ```
 *        This is NOT switcher.js's input-side situation: the SAME
 *        function's INPUTS loop conditionally uses a label-width-aware
 *        `input.boundingRect` when the slot is a real `NodeInputSlot`
 *        instance (`isInSlot = input instanceof NodeInputSlot ?
 *        isInRect(x, y, input.boundingRect) : isInRectangle(...)`), which is
 *        what justifies switcher.js's `20 + nameLength*7` label-width
 *        estimate on that side -- but the OUTPUTS loop has no such
 *        instanceof branch, ever. So porting that label-width heuristic to
 *        this side would solve a problem the output hit-test does not have.
 *        The one hard, load-bearing constraint for a working toggle here is
 *        clearing `link_pos[0] - 15` (the box's right edge) -- `ROW_GAP`
 *        below is a FIXED margin (not name-length-dependent) that clears it
 *        with room to spare, pinned as a regression test in
 *        `tests/test_distributor_js.py` (`toggleBoxRect`'s right edge must
 *        stay strictly left of `socketLocalX - 15` at every probed width).
 *      - `node.getOutputPos(i)` (not `getConnectionPos`) is what
 *        `_processNodeClick` itself calls, and `LGraphNode.ts` documents it
 *        as "preferred over the legacy getConnectionPos method" -- so this
 *        file calls the SAME method, guaranteeing the toggle geometry can
 *        never disagree with where the real hit-test boundary sits, with a
 *        `getConnectionPos(false, idx)` fallback (switcher.js's identical
 *        defensive-API-access posture) for a fork where `getOutputPos` is
 *        absent.
 *      - Visual (not click-blocking) label collision is a separate, softer
 *        concern: `node/NodeSlot.ts`'s shared `draw()` -- used by both
 *        `NodeInputSlot`/`NodeOutputSlot` -- right-aligns an output's name
 *        ending at `pos[0] - 10`, growing LEFTWARD (`NodeOutputSlot.draw()`
 *        sets `ctx.textAlign = 'right'`, `labelPosition: Left`, then
 *        `ctx.fillText(text, pos[0] - 10, pos[1] + 5)`). `out_1`..`out_8`
 *        are all 5 characters, so `ROW_GAP`'s fixed margin clears this too
 *        without needing to measure text at all; a future per-output rename
 *        feature (this node does not have one) would need to revisit this
 *        the way switcher.js's `drawRowToggles` measures `label || name`
 *        dynamically for its own (unbounded-length) `image_N` rows.
 *      - `getWidgetOnPos` runs BEFORE `node.onMouseDown` in the same
 *        function, and is mutually exclusive with it (a widget hit calls
 *        `processWidgetClick` and never falls through) -- verified this
 *        cannot swallow a toggle click: `LGraphNode.ts`'s `getWidgetOnPos`
 *        skips any widget where `!isWidgetVisible(widget)`, and
 *        `isWidgetVisible` returns `false` whenever `widget.hidden` is
 *        truthy. `hideTogglesWidget` below sets exactly that flag, so the
 *        (hidden) `toggles` widget has ZERO hit area and can never
 *        intercept a click meant for a row toggle, regardless of where the
 *        two might otherwise overlap vertically.
 *    **Tri-state "Toggle All" header** also ports cleanly: PLL's legacy
 *    custom-widget contract (`addCustomWidget`, a plain `{type:'custom',
 *    draw, mouse}` object) that switcher.js already uses doesn't care
 *    whether the rows behind it are inputs or outputs -- it lands in the
 *    same widget stack either way (`LGraphNode.ts` always lays widgets out
 *    below every socket, input or output). One behavioral difference from
 *    switcher's version, not a porting obstacle: switcher's header only
 *    counts CONNECTED `image_N` rows (an unwired input has no data to
 *    gate), but here EVERY visible `out_N` is a meaningful gate whether or
 *    not it is wired yet -- the backend emits an image-or-`ExecutionBlocker`
 *    per slot regardless of downstream connection (roadmap: "unwired slots'
 *    blockers are harmless"), so this file's header and per-row toggles
 *    operate over every currently VISIBLE output, with no connected-only
 *    filter.
 * 2. **Trailing-output hide/reveal** -- a structural port of
 *    `eps_image/resolution.js`'s Properties-driven `removeOutput`/
 *    `addOutput` mechanism (litegraph has no output-slot `hidden` flag; see
 *    that file's header for the citations), generalized from resolution.js's
 *    fixed PAIR (`original_width`/`original_height`, an on/off boolean) to a
 *    single right-click Property, `Outputs` (a number, default
 *    `DEFAULT_VISIBLE_OUTPUTS = 3`, clamped `MIN_OUTPUTS..MAX_OUTPUTS` =
 *    `1..8`), that drives how many of the fixed `out_1`..`out_8` backend
 *    outputs stay visible. Same hard rule resolution.js's file header
 *    derives from ComfyUI's own link-serialization contract (a link's source
 *    is resolved by bare positional index against the backend's FIXED
 *    `RETURN_TYPES` tuple, so only genuine TAIL removal is safe, and it must
 *    be strictly LIFO from the end so no index shifts under a link that
 *    survives): this file only ever adds/removes at the tail
 *    (`applyVisibleOutputCount` below, `toRemove` sorted highest-array-index
 *    first), and refuses to remove a slot that is currently wired.
 *    Generalized for the N-count case: resolution.js's boolean refusal has
 *    only two states to revert to (`true`/`false`), but a COUNT has a whole
 *    range, so "refuse and revert" here means clamping the target back UP
 *    to the highest wired slot -- not all the way back to whatever the
 *    count was before the edit -- and everything strictly above that (which
 *    is provably unwired, or the request would have clamped higher still)
 *    is still hidden. E.g. dropping from 8 to 2 while `out_5` is wired hides
 *    `out_6`..`out_8` (safe, unwired) and clamps the property back to `5`
 *    (not `8`), pure-factored as `clampVisibleCount(requested,
 *    highestWiredIndex)` below so the rule is testable headlessly. "Wired"
 *    checks BOTH `output.links` (settled) and `output._floatingLinks` (a
 *    link mid-drag, not yet dropped) -- `isOutputConnected` below mirrors
 *    `LGraphCanvas.ts`'s own `hasRelevantOutputLinks` guard (the function
 *    right above `_processNodeClick`'s outputs loop) exactly for this,
 *    because a miss is destructive (`removeOutput` would run on a socket the
 *    user still has a link on). resolution.js's `isOutputConnected` was
 *    `.links`-only until v0.34.0 and is now this same function -- the two
 *    refusal paths are kept in lockstep, one headless case list each.
 *    Surfaced with `console.warn` only (no toast in this file) when the
 *    refusal actually changes the outcome versus a plain range clamp.
 *
 * **`toggles` lockstep + pruning**: this file's `pruneToggles` mirrors
 * switcher.js's own pruning, but the criterion that keeps a key is VISIBILITY
 * (`node.outputs` still has that `out_N`) rather than switcher's CONNECTION
 * state -- the output-side analogue of "no longer exists", since a hidden
 * `out_N` here is genuinely removed from `node.outputs` (resolution.js's
 * real-`removeOutput` mechanism, not its cosmetic-draw-suppression one), so
 * "hidden" and "does not exist" are the same condition for this file, exactly
 * as switcher.js's own comment describes for a disconnected/removed
 * `image_N`. This keeps a later re-reveal of a previously-hidden slot always
 * starting ENABLED (absent key), never silently resurrecting a stale `false`
 * -- the same anti-surprise rationale switcher.js's `pruneToggles` docstring
 * gives for its own disconnect case. The `toggles` widget itself is hidden
 * on canvas but real (`.hidden = true` + a genuine serialized `.value`),
 * FORMAT.md section 7.2's established trick, identical to switcher.js's own
 * `hideTogglesWidget` -- and, per the `getWidgetOnPos` finding above, that
 * same flag is what guarantees the widget has no leftover hit area either.
 *
 * **Width floor**: `installMinWidth` below is a self-contained copy of the
 * same additive `onResize`-wrap pattern `eps_image/frame_saver.js` uses
 * (own guard flag, own constant -- each module in this pack keeps its own
 * copy rather than sharing one), per the v0.30.3 "no hard-coded pixel
 * widths for text" house rule (FORMAT.md section 7.2). `MIN_NODE_WIDTH` here
 * reuses switcher.js's own validated `200` rather than re-deriving new
 * pixel math: the two files' widest drawn text is the same shape (a
 * `"Toggle All  (N/N enabled)"` header row -- switcher.js counts images,
 * this file counts outputs, but the STRING LENGTH is comparable), and this
 * file's per-row text (`out_1`..`out_8`) is shorter than switcher's own
 * (`image_N`, N unbounded), so switcher's floor is, if anything, more
 * conservative than this file strictly needs. Not independently
 * pixel-verified live against a real canvas font metrics query (neither
 * switcher.js's original 200 was derived that way; canvas `fillText` sizing
 * generally isn't practical to assert on outside a real renderer) -- see the
 * round's verification notes for what WAS checked live.
 *
 * **Testability**: the geometry (`toggleBoxRect`) and the count/toggle logic
 * (`clampOutputsCount`, `clampVisibleCount`, `parseToggles`, `isSlotEnabled`,
 * `outputName`/`parseOutputSlot`) are factored into PURE functions -- no
 * node/ctx/DOM in their signatures -- following `resolution.js`'s own
 * established convention (its `getPlotRect`/`valueToPlot`/
 * `computeGridWidgetHeight` etc.) so `tests/test_distributor_js.py` can
 * drive them directly under Node, the same served-directory-layout /
 * stub-`app.js` fixture `tests/test_resolution_grid_js.py` uses. The
 * remaining structural, non-pure pieces (tail-only removal order, the
 * wired-refusal path, the hidden-widget trick, Toggle All's wiring) are
 * pinned there via source-text assertions instead, matching
 * `tests/test_frame_saver_paste_js.py`'s dual-fixture convention for the
 * same kind of DOM/closure-bound code that has no browser harness to drive.
 *
 * **Fails soft everywhere**: every one-shot setup path is wrapped by
 * `attach()`'s own try/catch (matching switcher.js's shape); the two
 * per-frame/per-click hooks (`onDrawForeground`, `onMouseDown`) each carry
 * their own try/catch so a draw or hit-test error degrades that one frame/
 * click rather than wedging the node; `onPropertyChanged` is ALSO wrapped
 * (switcher.js/resolution.js don't individually guard theirs, but this
 * file's property handler does real structural surgery -- add/removeOutput
 * -- across up to eight slots, and FORMAT.md's "never throw during graph
 * load" requirement is unconditional for a property `configure()` replays
 * on every reload, so the extra guard here is deliberate, not a gap in the
 * ported pattern). No litegraph API is assumed present without a feature
 * check (`typeof node.addCustomWidget === 'function'`, etc.), matching the
 * rest of `eps_image/*.js`.
 *
 * This file has no backend coupling beyond the contract above (widget name
 * `toggles`, output names `out_1`..`out_8`, type `IMAGE`) -- it does not
 * assume `eps_image/nodes_distributor.py` exists or is registered, matching
 * how this pack's frontend and backend modules are built independently
 * against a shared, pre-agreed contract. Its ONLY import is ComfyUI's
 * `scripts/app.js`, and solely for `toast()` below -- no canvas lookup, no
 * other app surface -- so a Node probe needs just the one stub
 * (`tests/test_distributor_js.py`, mirroring
 * `tests/test_resolution_grid_js.py`'s fixture).
 *
 * Why that import is worth it: the wired-output refusal MUST be visible. A
 * `console.warn` alone means lowering `Outputs` past a wired socket makes the
 * number silently snap back with no explanation anywhere the user is looking
 * -- the same silent-refusal failure the owner reported as a bug on the
 * notebook's file picker ("selecting it does nothing", 2026-07-26), which
 * v0.33.1 existed to fix. section 6.5's `applyOriginalSizeVisibility` set the
 * precedent for exactly this situation with a toast ("Unwire the
 * original-size outputs before hiding them"); this file matches it.
 */

import { app } from '../../../scripts/app.js'

/** FORMAT.md section 6.11 -- frozen once shipped. */
export const CLASS_ID = 'EPSDistributor'
const PREFIX = '[eps_image:distributor]'
/** Toast summary fallback when a node has no custom title (resolution.js's
 * identical convention). */
const NODE_TITLE = 'EPS Distributor'

const OUTPUT_TYPE = 'IMAGE'
/** Backend's fixed `RETURN_NAMES` shape (`out_1`..`out_MAX_OUTPUTS`). */
export const MAX_OUTPUTS = 8
export const MIN_OUTPUTS = 1
const OUTPUT_NAME_RE = /^out_(\d+)$/

/** Right-click Property controlling how many of the fixed outputs show. */
export const PROP_OUTPUTS = 'Outputs'
export const DEFAULT_VISIBLE_OUTPUTS = 3

/** The hidden JSON bridge to nodes_distributor.py's `distribute()` -- same
 * name/shape/semantics as switcher.js's identical widget, reused verbatim:
 * a key present and literally `false` means disabled; an absent key (or any
 * other value) means enabled. */
export const TOGGLES_WIDGET_NAME = 'toggles'

const HEADER_WIDGET_NAME = '__eps_distributor_toggle_all'

// --------------------------------------------------------- draw geometry
// See file header's "Per-slot toggle draw + hit-test" section for the
// `_processNodeClick` citation these numbers clear.
export const ROW_BOX = 12 // same size as switcher.js's toggle box
/**
 * Fixed margin from an output socket's own LOCAL x to the toggle box's
 * RIGHT edge -- deliberately NOT name-length-dependent (see file header:
 * `_processNodeClick`'s output hit-test is a fixed 30x20 box, unlike its
 * conditionally label-width-aware input hit-test). 92 clears the hard
 * `socketLocalX - 15` click boundary with ~77px to spare and comfortably
 * clears the drawn label's visual reach for this node's fixed 5-character
 * `out_N` names -- reused from switcher.js's own validated `ROW_MIN_X`
 * rather than a freshly-tuned number.
 */
export const ROW_GAP = 92
const HEADER_BOX = 12
const HEADER_ROW_HEIGHT = 20
/** See file header's "Width floor" section for why this reuses switcher.js's
 * own validated constant rather than deriving a new number. */
export const MIN_NODE_WIDTH = 200

/** Nodes we've already wired, guarding against a double `nodeCreated`
 * (switcher.js's identical guard). */
const attachedNodes = new WeakSet()

// ---------------------------------------------------------------------------
// Pure helpers -- exported so tests/test_distributor_js.py can drive the
// toggles/property/geometry contract under Node without a litegraph node
// stub. No node/ctx/DOM in any of these signatures.
// ---------------------------------------------------------------------------

/** `out_${n}` -- the backend's RETURN_NAMES naming rule. Exported for tests. */
export function outputName(n) {
  return `out_${n}`
}

/** Inverse of outputName(): the slot number for a name matching `out_<N>`,
 * or null for anything else. Exported for tests. */
export function parseOutputSlot(name) {
  const match = typeof name === 'string' ? OUTPUT_NAME_RE.exec(name) : null
  return match ? Number(match[1]) : null
}

/**
 * Clamps a candidate `Outputs` property value to `MIN_OUTPUTS..MAX_OUTPUTS`,
 * rounding to the nearest integer and falling back to
 * `DEFAULT_VISIBLE_OUTPUTS` for anything non-finite (a hand-edited workflow,
 * `NaN` from a blank field, etc.). Exported for tests.
 */
export function clampOutputsCount(value) {
  const rounded = Math.round(Number(value))
  if (!Number.isFinite(rounded)) return DEFAULT_VISIBLE_OUTPUTS
  return Math.min(MAX_OUTPUTS, Math.max(MIN_OUTPUTS, rounded))
}

/**
 * The EFFECTIVE visible-output count after applying both the plain
 * `MIN_OUTPUTS..MAX_OUTPUTS` range clamp AND the wired-output floor: never
 * below `MIN_OUTPUTS`, never above `MAX_OUTPUTS`, and never below
 * *highestWiredIndex* (a wired `out_N` can never be hidden -- file header's
 * "Trailing-output hide/reveal" section). *highestWiredIndex* of `0`/
 * `undefined`/non-finite means "nothing wired", matching
 * `highestWiredSlot`'s own contract. Pure; exported for tests -- this is
 * the one function that would need to change if the clamp-up rule ever
 * changed, and the one this file's `applyVisibleOutputCount` actually calls.
 */
export function clampVisibleCount(requested, highestWiredIndex) {
  const rangeClamped = clampOutputsCount(requested)
  const wired = Math.round(Number(highestWiredIndex))
  const wiredFloor = Number.isFinite(wired) && wired > 0 ? Math.min(wired, MAX_OUTPUTS) : 0
  return Math.max(rangeClamped, wiredFloor)
}

/**
 * Parses a `toggles` widget's raw string value into a plain map. Never
 * throws -- a malformed value (never expected from this file's own writes,
 * but a hand-edited workflow is always possible) degrades to "no
 * overrides", matching the backend's own `_parse_toggles`/switcher.js's
 * `readToggles` fallback. Exported for tests.
 */
export function parseToggles(rawValue) {
  try {
    const parsed = JSON.parse(rawValue || '{}')
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {}
  } catch (error) {
    return {}
  }
}

/**
 * Enabled unless *map* explicitly records the literal boolean `false` for
 * *name* -- mirrors switcher.js's `isRowEnabled`/the backend's own rule
 * bit-for-bit (`!== false`, not `!== false && key in map`, so any other
 * value -- `true`, `0`, `null`, missing -- reads as enabled). Exported for
 * tests.
 */
export function isSlotEnabled(map, name) {
  return map[name] !== false
}

/**
 * Pure geometry for one output row's toggle box, given the socket's own
 * LOCAL position (graph position minus `node.pos` -- the frame
 * `node.getOutputPos()`/`getConnectionPos()` both return). No node/ctx/DOM
 * involved, so `tests/test_distributor_js.py` drives this directly under
 * Node. See the module docstring's `_processNodeClick` citation for the
 * `< socketLocalX - 15` right-edge requirement this must clear, and the
 * `ROW_GAP` constant above for why the margin is fixed rather than
 * label-length-dependent.
 * @param {number} socketLocalX
 * @param {number} socketLocalY
 * @returns {{x:number, y:number, w:number, h:number}}
 */
export function toggleBoxRect(socketLocalX, socketLocalY) {
  const x = socketLocalX - ROW_GAP - ROW_BOX
  const y = socketLocalY - ROW_BOX / 2
  return { x, y, w: ROW_BOX, h: ROW_BOX }
}

// ---------------------------------------------------------------------------
// Node / widget lookups (switcher.js's identical helpers, copied verbatim --
// each eps_image/*.js module keeps its own copy rather than sharing one)
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

function getTogglesWidget(node) {
  return findWidget(node, TOGGLES_WIDGET_NAME) || null
}

function outputIndexByName(node, name) {
  return (node.outputs || []).findIndex((output) => output?.name === name)
}

/**
 * Whether *output* currently carries a real link -- checks BOTH `.links`
 * (settled connections) and `._floatingLinks` (a link mid-drag, not yet
 * dropped), mirroring `LGraphCanvas.ts`'s own `hasRelevantOutputLinks`
 * (module docstring: verified directly against this rig's installed
 * frontend source). A `.links`-only check misses the mid-drag case, and a
 * miss here is destructive -- `applyVisibleOutputCount` would
 * `removeOutput()` a socket the user still has a link on. resolution.js's
 * `isOutputConnected` is now the identical function (ported there v0.34.0,
 * so both refusal paths agree); keep the two in lockstep. Exported so
 * tests/test_distributor_js.py can drive it headlessly.
 */
export function isOutputConnected(output) {
  if (!output) return false
  if (Array.isArray(output.links) && output.links.length > 0) return true
  const floating = output._floatingLinks
  if (floating && typeof floating.size === 'number' && floating.size > 0) return true
  return false
}

/** resolution.js's `toast` verbatim -- the only reason this file imports
 * `app` (see module docstring). Never throws: a frontend without the toast
 * service still gets the `console.warn` at the call site. */
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

// ---------------------------------------------------------------------------
// `toggles` state -- node-bound wrappers around the pure functions above.
// ---------------------------------------------------------------------------

function readToggles(node) {
  const widget = getTogglesWidget(node)
  if (!widget) return {}
  return parseToggles(widget.value)
}

function writeToggles(node, map) {
  const widget = getTogglesWidget(node)
  if (!widget) return
  widget.value = JSON.stringify(map)
}

function isRowEnabled(node, name) {
  return isSlotEnabled(readToggles(node), name)
}

function setRowEnabled(node, name, enabled) {
  const map = readToggles(node)
  if (enabled) delete map[name] // absent == enabled; keeps the JSON minimal
  else map[name] = false
  writeToggles(node, map)
}

function toggleRowEnabled(node, name) {
  setRowEnabled(node, name, !isRowEnabled(node, name))
}

/**
 * Drops toggle-map entries for `out_N` slots that are no longer VISIBLE --
 * the output-side analogue of switcher.js's own pruneToggles, which drops
 * entries for `image_N` slots no longer CONNECTED (that file's docstring:
 * "keeping a stale `false` on a disconnected... slot would silently disable
 * a DIFFERENT image later re-wired into that same slot number"). Here a
 * hidden `out_N` is genuinely removed from `node.outputs` (real
 * removeOutput, resolution.js's mechanism), so "visible" and "exists" are
 * the same test, and the same anti-stale-surprise rationale applies:
 * revealing a previously-hidden slot later always starts ENABLED.
 */
function pruneToggles(node) {
  const widget = getTogglesWidget(node)
  if (!widget) return
  const map = readToggles(node)
  const visibleNames = new Set(outputEntries(node).map((entry) => entry.name))
  let changed = false
  for (const key of Object.keys(map)) {
    if (!visibleNames.has(key)) {
      delete map[key]
      changed = true
    }
  }
  if (changed) writeToggles(node, map)
}

/** Hides the `toggles` widget's on-canvas row (kept as the serialized value
 * only) -- FORMAT.md section 7.2's `.hidden = true` trick, switcher.js's
 * identical `hideTogglesWidget`. Per the module docstring's `getWidgetOnPos`
 * finding, this ALSO removes the widget's hit area entirely (`hidden`
 * widgets are skipped by `isWidgetVisible`), so it can never swallow a row
 * toggle's click via `processWidgetClick`. */
function hideTogglesWidget(node) {
  const widget = getTogglesWidget(node)
  if (!widget) {
    console.warn(
      PREFIX,
      'EPSDistributor node is missing its `toggles` widget; per-output state will not persist'
    )
    return
  }
  widget.hidden = true
}

// ---------------------------------------------------------------------------
// Trailing-output hide/reveal -- resolution.js's removeOutput/addOutput
// mechanism, generalized from a fixed boolean pair to an `Outputs` COUNT
// property. See module docstring for the tail-only/refuse-if-wired rules
// this inherits and how the "clamp back up" revert generalizes for a range.
// ---------------------------------------------------------------------------

/** @returns {{idx:number, n:number, name:string, output:object, wired:boolean}[]}
 * every currently-visible `out_N` output, in array order (which is always
 * contiguous 1..currentCount -- this file only ever adds/removes at the
 * tail, so a gap can never occur). */
function outputEntries(node) {
  const entries = []
  const outputs = node.outputs || []
  for (let idx = 0; idx < outputs.length; idx++) {
    const output = outputs[idx]
    const match = output && OUTPUT_NAME_RE.exec(output.name)
    if (!match) continue
    entries.push({
      idx,
      n: Number(match[1]),
      name: output.name,
      output,
      wired: isOutputConnected(output)
    })
  }
  return entries
}

/** Highest slot number among currently-wired outputs, or 0 if none are
 * wired -- feeds `clampVisibleCount`'s floor. */
function highestWiredSlot(node) {
  let max = 0
  for (const entry of outputEntries(node)) {
    if (entry.wired && entry.n > max) max = entry.n
  }
  return max
}

/** Recompute layout after an outputs-array change: grow the width to fit if
 * needed and set the height ABSOLUTELY (arrange() on its own only grows) --
 * resolution.js's identical `resyncSize`, minus its grid-widget tail (this
 * node has no DOM widget). Defensive typeof guards beyond resolution.js's
 * own version: FORMAT.md's "never throw during graph load" requirement is
 * unconditional here since this runs from onPropertyChanged, which
 * `configure()` replays on every reload. */
function resyncSize(node) {
  if (typeof node.computeSize !== 'function' || typeof node.setSize !== 'function') return
  const computed = node.computeSize()
  node.setSize([Math.max(node.size[0], computed[0]), computed[1]])
  node.setDirtyCanvas?.(true, true)
}

/**
 * Applies the `Outputs` property to `node.outputs`: adds missing tail
 * outputs when growing, removes tail outputs when shrinking -- refusing
 * (and clamping the property back UP, via `clampVisibleCount`) to remove
 * any output that is currently wired. See module docstring for why "clamp
 * back up" means "up to the highest wired slot", not "back to the
 * pre-edit count". Idempotent: safe to call redundantly from
 * onPropertyChanged/attach() regardless of whether `configure()` already
 * applied the saved outputs array for a reloaded workflow (resolution.js's
 * identical idempotency argument for its own two-mechanism hide/reveal
 * applies here unchanged -- every add/remove below is guarded by a fresh
 * name lookup, and `configure()`'s own wholesale `node.outputs` clone is
 * authoritative for link data no synthetic addOutput() call could
 * reconstruct).
 */
function applyVisibleOutputCount(node) {
  if (!node.properties) node.properties = {}

  const rawValue = node.properties[PROP_OUTPUTS]
  const wiredMax = highestWiredSlot(node)
  const rangeClamped = clampOutputsCount(rawValue)
  const desired = clampVisibleCount(rawValue, wiredMax)

  if (rawValue !== desired) node.properties[PROP_OUTPUTS] = desired
  if (desired > rangeClamped) {
    // The plain range clamp alone would have hidden a wired output --
    // refused, clamped back up to the highest wired slot instead
    // (resolution.js's "never leave a dangling wire" precedent, generalized
    // for a range). Toasted as well as logged, deliberately: the property
    // number visibly snaps back, and without a message that reads exactly
    // like the silent refusal the owner reported as a bug (module docstring).
    const message =
      `${outputName(wiredMax)} is wired -- Outputs can't go below ` +
      `${wiredMax} until it's unwired.`
    console.warn(PREFIX, message)
    toast(node, 'warn', message)
  }

  const entries = outputEntries(node)
  const currentCount = entries.length

  if (desired > currentCount) {
    for (let n = currentCount + 1; n <= desired; n++) {
      if (outputIndexByName(node, outputName(n)) === -1) {
        node.addOutput(outputName(n), OUTPUT_TYPE)
      }
    }
  } else if (desired < currentCount) {
    // Highest array index first: removeOutput() splices node.outputs by
    // POSITION (switcher.js's convergeImageInputs/resolution.js's own
    // removal both document this), so removing high-to-low from one
    // upfront snapshot -- strictly LIFO from the end -- keeps every other
    // queued index valid and never repoints a surviving link.
    const toRemove = entries.filter((entry) => entry.n > desired).sort((a, b) => b.idx - a.idx)
    for (const entry of toRemove) node.removeOutput(entry.idx)
  }

  pruneToggles(node)
  resyncSize(node)
}

// ---------------------------------------------------------------------------
// Header tri-state ("toggle all") logic -- switcher.js's mechanism, but
// over EVERY visible output (wired or not) rather than connected-only
// inputs. See module docstring for why that filter is dropped here.
// ---------------------------------------------------------------------------

/** @returns {true|false|null} true=all on, false=all off (or no outputs
 * visible -- practically unreachable since MIN_OUTPUTS is 1), null=mixed */
function allRowsState(node) {
  const entries = outputEntries(node)
  if (entries.length === 0) return false
  let allOn = true
  let allOff = true
  for (const entry of entries) {
    const on = isRowEnabled(node, entry.name)
    allOn = allOn && on
    allOff = allOff && !on
  }
  if (allOn) return true
  if (allOff) return false
  return null
}

/** rgthree `toggleAllLoras` semantics (switcher.js's identical rule):
 * anything but "all on" -> turn all on; all on -> turn all off. */
function toggleAllRows(node) {
  const entries = outputEntries(node)
  if (entries.length === 0) return
  const target = allRowsState(node) !== true
  for (const entry of entries) setRowEnabled(node, entry.name, target)
}

// ---------------------------------------------------------------------------
// Drawing (switcher.js's drawToggleBox, copied verbatim -- each eps_image/
// module keeps its own copy)
// ---------------------------------------------------------------------------

function drawToggleBox(ctx, x, y, size, enabled, mixed) {
  ctx.save()
  ctx.beginPath()
  if (typeof ctx.roundRect === 'function') ctx.roundRect(x, y, size, size, 3)
  else ctx.rect(x, y, size, size)
  ctx.fillStyle = mixed ? '#8a7a3a' : enabled ? '#4f9a44' : '#3a3a3a'
  ctx.strokeStyle = mixed ? '#d7c37a' : enabled ? '#a8dd93' : '#777777'
  ctx.lineWidth = 1
  ctx.fill()
  ctx.stroke()

  if (mixed) {
    ctx.beginPath()
    ctx.strokeStyle = '#2a2410'
    ctx.lineWidth = 2
    ctx.moveTo(x + size * 0.22, y + size * 0.5)
    ctx.lineTo(x + size * 0.78, y + size * 0.5)
    ctx.stroke()
  } else if (enabled) {
    ctx.beginPath()
    ctx.strokeStyle = '#153a10'
    ctx.lineWidth = 2
    ctx.lineJoin = 'round'
    ctx.lineCap = 'round'
    ctx.moveTo(x + size * 0.2, y + size * 0.55)
    ctx.lineTo(x + size * 0.42, y + size * 0.8)
    ctx.lineTo(x + size * 0.82, y + size * 0.22)
    ctx.stroke()
  }
  ctx.restore()
}

/**
 * *idx*'s output socket position in NODE-LOCAL coordinates (graph position
 * minus `node.pos`). Prefers `getOutputPos` -- the exact method
 * `_processNodeClick`'s own hit-test calls, and the one `LGraphNode.ts`
 * itself documents as "preferred over the legacy getConnectionPos method"
 * -- with a `getConnectionPos` fallback for an older/different fork
 * (switcher.js's identical defensive-API-access posture). Returns null
 * (fail soft) if neither exists or either throws.
 */
function outputLocalPos(node, idx) {
  try {
    let pos
    if (typeof node.getOutputPos === 'function') {
      pos = node.getOutputPos(idx)
    } else if (typeof node.getConnectionPos === 'function') {
      pos = node.getConnectionPos(false, idx)
    } else {
      return null
    }
    return [pos[0] - node.pos[0], pos[1] - node.pos[1]]
  } catch (error) {
    console.warn(PREFIX, 'could not resolve output position for slot', idx, error)
    return null
  }
}

/**
 * Recomputes and draws every visible out_N's toggle box, caching hit rects
 * on the node for wireRowToggleClicks() to consume. Mirrors switcher.js's
 * drawRowToggles; the geometry itself is the pure toggleBoxRect() above.
 */
function drawRowToggles(node, ctx) {
  if (node.flags?.collapsed) return

  const rects = []
  for (const entry of outputEntries(node)) {
    const pos = outputLocalPos(node, entry.idx)
    if (!pos) continue
    const rect = toggleBoxRect(pos[0], pos[1])
    drawToggleBox(ctx, rect.x, rect.y, rect.w, isRowEnabled(node, entry.name), false)
    rects.push({ name: entry.name, x: rect.x, y: rect.y, w: rect.w, h: rect.h })
  }
  node.__epsDistributorRowRects = rects
}

function wireRowToggleDrawing(node) {
  const original = node.onDrawForeground
  node.onDrawForeground = function (ctx, canvas, canvasEl) {
    let result
    if (typeof original === 'function') result = original.apply(this, arguments)
    try {
      drawRowToggles(this, ctx)
    } catch (error) {
      console.warn(PREFIX, 'drawRowToggles failed', error)
    }
    return result
  }
}

/** @returns {boolean} true if the click hit a row toggle (and was handled). */
function handleRowToggleClick(node, localPos) {
  const rects = node.__epsDistributorRowRects || []
  const [x, y] = localPos
  for (const rect of rects) {
    if (x >= rect.x && x <= rect.x + rect.w && y >= rect.y && y <= rect.y + rect.h) {
      toggleRowEnabled(node, rect.name)
      node.graph?.setDirtyCanvas(true, true)
      return true
    }
  }
  return false
}

function wireRowToggleClicks(node) {
  const original = node.onMouseDown
  node.onMouseDown = function (e, pos, canvas) {
    try {
      if (handleRowToggleClick(this, pos)) return true
    } catch (error) {
      console.warn(PREFIX, 'handleRowToggleClick failed', error)
    }
    if (typeof original === 'function') return original.apply(this, arguments)
    return false
  }
}

// ---------------------------------------------------------------------------
// Header "toggle all" widget (legacy custom-widget contract; switcher.js's
// mechanism, ported -- see module docstring for why the connected-only
// filter is dropped here)
// ---------------------------------------------------------------------------

function addHeaderWidget(node) {
  if (findWidget(node, HEADER_WIDGET_NAME)) return
  if (typeof node.addCustomWidget !== 'function') {
    console.warn(PREFIX, 'node.addCustomWidget is unavailable; header toggle-all not added')
    return
  }

  const widget = {
    name: HEADER_WIDGET_NAME,
    type: 'custom',
    value: null,
    // Presentation-only control -- never persisted, never sent to the
    // backend (the real enabled-set lives in the `toggles` widget above).
    serialize: false,
    serializeValue: () => undefined,
    computeSize(width) {
      return [width ?? 0, HEADER_ROW_HEIGHT]
    },
    draw(ctx, drawNode, widgetWidth, y, height) {
      const entries = outputEntries(drawNode)
      const state = allRowsState(drawNode)
      const boxX = 8
      const boxY = y + (height - HEADER_BOX) / 2
      drawToggleBox(ctx, boxX, boxY, HEADER_BOX, state === true, state === null)

      ctx.save()
      const textColor =
        (typeof LiteGraph !== 'undefined' && LiteGraph.WIDGET_SECONDARY_TEXT_COLOR) || '#999999'
      ctx.fillStyle = textColor
      ctx.font = '11px sans-serif'
      ctx.textAlign = 'left'
      ctx.textBaseline = 'middle'
      const enabledCount = entries.filter((entry) => isRowEnabled(drawNode, entry.name)).length
      const label =
        entries.length === 0
          ? 'Toggle All (no outputs visible)'
          : `Toggle All  (${enabledCount}/${entries.length} enabled)`
      ctx.fillText(label, boxX + HEADER_BOX + 8, y + height / 2)
      ctx.restore()
    },
    mouse(event, pos, mouseNode) {
      // processWidgetClick() (LGraphCanvas.ts) replays this on mouseup too
      // (switcher.js's file header) -- only react to the actual
      // mousedown/pointerdown to avoid a toggle-then-untoggle no-op per click.
      const type = event && event.type
      if (typeof type !== 'string' || !type.endsWith('down')) return false
      toggleAllRows(mouseNode)
      mouseNode.graph?.setDirtyCanvas(true, true)
      return true
    }
  }

  try {
    node.addCustomWidget(widget)
  } catch (error) {
    console.warn(PREFIX, 'addCustomWidget (header) failed', error)
  }
}

// ---------------------------------------------------------------------------
// Node width floor -- frame_saver.js's installMinWidth, self-contained copy
// (own guard flag; see module docstring's "Width floor" section for
// MIN_NODE_WIDTH)
// ---------------------------------------------------------------------------

function installMinWidth(node, minWidth) {
  if (!node || node.__epsDistributorMinWidthInstalled) return
  node.__epsDistributorMinWidthInstalled = true
  const originalOnResize = node.onResize
  node.onResize = function (size) {
    if (size && size[0] < minWidth) size[0] = minWidth
    return originalOnResize?.call(this, size)
  }
  if (Array.isArray(node.size) && node.size[0] < minWidth) {
    node.size[0] = minWidth
  }
}

// ---------------------------------------------------------------------------
// Public entry points (called from web/eps_image.js)
// ---------------------------------------------------------------------------

/** Frontend-only one-time setup. EPSDistributor is a real backend node (no
 * frontend-only type registration needed) -- everything here is
 * per-instance, done in attach(). Kept as an export because eps_image.js
 * calls it unconditionally, matching switcher.js's identical no-op init(). */
export function init() {}

/**
 * Per-node-instance attach; no-op unless *node* is an EPSDistributor. No
 * `loadedGraphNode` export: unlike EPS Image Grid/Frame Saver (which need a
 * post-whole-graph-load resync because they read WIDGET VALUES that
 * `configure()` restores via a bare assignment with no callback), this
 * file's state lives in a right-click PROPERTY, and `configure()`'s own
 * properties loop calls `this.onPropertyChanged?.(k, info.properties[k])`
 * for every saved property key (resolution.js's file header verifies this
 * against LGraphNode.ts directly) -- through the SAME wrapped handler
 * installed below, since `attach()` (nodeCreated) always runs before
 * `configure()` for a saved workflow. So a reloaded workflow's `Outputs`
 * value reaches `applyVisibleOutputCount` on its own, with no defer needed
 * -- resolution.js's identical (non-deferred) attach() shape for its own
 * two hideable-output properties.
 */
export function attach(node) {
  try {
    if (!node) return
    if (nodeClassOf(node) !== CLASS_ID) return
    if (attachedNodes.has(node)) return
    attachedNodes.add(node)

    hideTogglesWidget(node)
    installMinWidth(node, MIN_NODE_WIDTH)

    node.addProperty(PROP_OUTPUTS, DEFAULT_VISIBLE_OUTPUTS, 'number')

    const originalOnPropertyChanged = node.onPropertyChanged
    node.onPropertyChanged = function (name, value, prevValue) {
      const result = originalOnPropertyChanged?.call(this, name, value, prevValue)
      if (name === PROP_OUTPUTS) {
        try {
          applyVisibleOutputCount(this)
        } catch (error) {
          console.warn(PREFIX, 'applyVisibleOutputCount failed', error)
        }
      }
      return result
    }

    wireRowToggleDrawing(node)
    wireRowToggleClicks(node)
    addHeaderWidget(node)

    // Fresh node: hide down to the just-seeded default immediately (backend
    // declares all MAX_OUTPUTS outputs up front, per resolution.js's
    // identical "addProperty() doesn't fire onPropertyChanged" reasoning).
    // Reloaded node: idempotent re-application ahead of configure()'s own
    // properties loop, which will call this again with the saved value and
    // win last regardless of call order (resolution.js's file header).
    applyVisibleOutputCount(node)
  } catch (error) {
    console.warn(PREFIX, 'attach failed', error)
  }
}
