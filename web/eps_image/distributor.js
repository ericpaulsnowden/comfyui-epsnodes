/**
 * @file EPS Distributor frontend (FORMAT.md section 6.11; roadmap:
 * `research/roadmap-eps-distributor.md`). Exports the `init()`/`attach(node)`
 * hooks `web/eps_image.js` calls; `attach` no-ops for every node type other
 * than `EPSDistributor`.
 *
 * The roadmap frames this node as "EPS Image Switcher pointed backwards": one
 * `image` input fans out to fixed `out_1`..`out_16` (MAX_OUTPUTS) IMAGE
 * outputs, each independently gated by a hidden `toggles` JSON widget the
 * backend reads (`{"out_3": false}` = off; absent key = on). All three
 * mechanisms below are structural ports of an existing eps_image file's
 * INPUT-side machinery, pointed at outputs instead -- which is where every
 * genuinely new bit lives, since litegraph and ComfyUI both treat the two
 * sides less symmetrically than they look:
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
 *        `ctx.fillText(text, pos[0] - 10, pos[1] + 5)`). The default
 *        `out_1`..`out_16` names are 5-6 characters, which `ROW_GAP`'s fixed
 *        margin clears on its own -- but the per-output RENAME feature
 *        (`wireOutputRename` below, v0.35.0) makes labels unbounded, so
 *        `drawRowToggles` DOES measure `label || name` dynamically, exactly
 *        the way switcher.js does for its own `image_N` rows. It computes ONE
 *        shared reach across every visible row rather than per-row, so a node
 *        with mixed-length labels still draws a single aligned toggle column
 *        (owner report 2026-07-27).
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
 *    `1..16`), that drives how many of the fixed `out_1`..`out_16` backend
 *    outputs stay visible -- and which `wireOutputGrowth` (item 3) raises on
 *    its own as the user wires up. Same hard rule resolution.js's file header
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
 *    is still hidden. E.g. dropping from 16 to 2 while `out_5` is wired hides
 *    `out_6`..`out_16` (safe, unwired) and clamps the property back to `5`
 *    (not `16`), pure-factored as `clampVisibleCount(requested,
 *    highestWiredIndex)` below so the rule is testable headlessly. "Wired"
 *    checks BOTH `output.links` (settled) and `output._floatingLinks` (a
 *    link mid-drag, not yet dropped) -- `isOutputConnected` below mirrors
 *    `LGraphCanvas.ts`'s own `hasRelevantOutputLinks` guard (the function
 *    right above `_processNodeClick`'s outputs loop) exactly for this,
 *    because a miss is destructive (`removeOutput` would run on a socket the
 *    user still has a link on). resolution.js's `isOutputConnected` was
 *    `.links`-only until v0.34.0 and is now this same function -- the two
 *    refusal paths are kept in lockstep, one headless case list each.
 *    Surfaced with BOTH `console.warn` and an on-node toast when the refusal
 *    actually changes the outcome versus a plain range clamp -- deliberately
 *    louder than resolution.js: the property number visibly snaps back, and a
 *    log-only message reads exactly like the silent refusal the owner
 *    originally reported as a bug.
 * 3. **Growing outputs** -- `wireOutputGrowth`/`growVisibleCount` below
 *    (owner ask 2026-07-29: "EPS Distributor should have more than three
 *    outputs. Just like EPS Image Switcher the number of nodes needs to be able to
 *    grow"). Wiring the last visible output reveals the next one, so there is
 *    always exactly one spare socket below the highest wired one -- the same
 *    feel as switcher.js's `convergeImageInputs`, and a structural port of its
 *    `wireImageInputGrowth` hook pair (`configure` guard + deferred
 *    `onConnectionsChange`; see that function's docstring for the two
 *    litegraph findings both files depend on).
 *
 *    Two deliberate differences from the switcher, both forced by outputs
 *    being a fundamentally different resource than inputs:
 *      - **Bounded, not unbounded.** The switcher's `image_N` inputs can grow
 *        forever because ComfyUI resolves inputs BY NAME through
 *        `INPUT_TYPES`' dict-like proxy, so a socket the class never declared
 *        still binds. Outputs resolve POSITIONALLY: a link serializes as
 *        `[origin_id, origin_slot]` and core indexes that straight into the
 *        class's `RETURN_TYPES` tuple, read ONCE at registration. So the
 *        backend must declare every socket up front and `MAX_OUTPUTS` is a
 *        real ceiling, raised 8 -> 16 alongside this feature. Raising it is
 *        append-only and therefore safe for saved workflows (every existing
 *        `origin_slot` still points at the same output); LOWERING it would
 *        silently repoint live links, so it must never happen.
 *      - **Grows only, never shrinks.** The switcher CONVERGES: it also
 *        removes surplus trailing empties. Here a socket the user has already
 *        seen stays put, because unlike an input an output can carry a
 *        user-typed rename (`wireOutputRename`) that removal would discard,
 *        and because `Outputs` is a hand-editable property whose value would
 *        otherwise be fought over. Shrinking stays fully available, just
 *        explicitly: set `Outputs` down by hand, subject to item 2's
 *        refuse-if-wired rule.
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
/**
 * Backend's fixed `RETURN_NAMES` shape (`out_1`..`out_MAX_OUTPUTS`). MUST
 * equal `nodes_distributor.py`'s `MAX_OUTPUTS` -- that module is the source
 * of truth (it declares the actual sockets) and this is the frontend's
 * mirror; a smaller number here would cap auto-growth below sockets the
 * backend really has, a larger one would reveal sockets that don't exist.
 * Raising it is append-only, so saved workflows are unaffected: see that
 * module's own `MAX_OUTPUTS` comment for why the ceiling exists at all
 * (outputs resolve POSITIONALLY, so unlike the Switcher's name-resolved
 * inputs they cannot be unbounded).
 */
export const MAX_OUTPUTS = 16
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
/** Where `NodeSlot.draw()` puts an output label's RIGHT edge, relative to the
 * socket dot: `ctx.fillText(text, pos[0] - 10, ...)` with
 * `textAlign = 'right'` (module docstring), so the text grows LEFTWARD from
 * here. Only matters now that outputs are RENAMABLE. */
const ROW_TEXT_ANCHOR = 10
/** measureSlots.ts's own ~7px/char stand-in (`20 + nameLength*7`), the
 * proxy this pack already treats as validated for "how wide does litegraph
 * render this slot name". */
const ROW_CHAR_WIDTH = 7
/** Clearance between a label's left edge and the toggle box's right edge. */
const ROW_LABEL_PAD = 12
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
/**
 * The visible-output count after AUTO-GROWTH: one spare socket always sits
 * below the highest wired one, so wiring the last visible output reveals
 * the next — EPSSwitcher's growing-inputs feel, applied to outputs (owner
 * ask 2026-07-29: "EPS Distributor should have more than three outputs.
 * Just like EPS Image Switcher the number of nodes needs to be able to grow").
 *
 * Grows only, never shrinks: a socket the user has already seen (or set via
 * the `Outputs` property) stays put, exactly as Switcher never renumbers a
 * connected row. `MAX_OUTPUTS` is the hard ceiling — outputs are resolved
 * POSITIONALLY against the backend's `RETURN_TYPES`, so unlike Switcher's
 * name-resolved inputs they cannot be unbounded (see the backend module's
 * `MAX_OUTPUTS` note). At the ceiling this returns the ceiling, so the last
 * socket is wirable rather than reserved as a permanent spare.
 *
 * Pure; exported for tests.
 */
export function growVisibleCount(current, highestWiredIndex) {
  const now = clampOutputsCount(current)
  const wired = Math.round(Number(highestWiredIndex))
  if (!Number.isFinite(wired) || wired <= 0) return now
  const wantSpare = Math.min(MAX_OUTPUTS, wired + 1)
  return Math.max(now, wantSpare)
}

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
 * How far an output's DRAWN label reaches leftward from its socket dot.
 * `NodeSlot.draw()` right-aligns the text ending at `socketX - 10`, so the
 * reach is that anchor plus the rendered width (measureSlots.ts's own
 * ~7px/char proxy). Pure; exported for tests.
 * @param {string} text
 * @returns {number}
 */
export function outputTextReach(text) {
  return ROW_TEXT_ANCHOR + String(text ?? '').length * ROW_CHAR_WIDTH
}

/**
 * Pure geometry for one output row's toggle box, given the socket's own
 * LOCAL position (graph position minus `node.pos` -- the frame
 * `node.getOutputPos()`/`getConnectionPos()` both return). No node/ctx/DOM
 * involved, so `tests/test_distributor_js.py` drives this directly under
 * Node.
 *
 * Two constraints, and the gap is the larger of them:
 *  - HARD: the box's right edge must stay strictly left of
 *    `socketLocalX - 15`, or the click starts a wire drag instead of
 *    toggling (module docstring's `_processNodeClick` citation).
 *  - SOFT: it must not sit under the drawn label. Fixed `out_N` names are
 *    5 characters and `ROW_GAP` covers them, but outputs are RENAMABLE
 *    (owner ask 2026-07-27) and a long label would otherwise be drawn
 *    straight over the box -- so *textReach* pushes the box further left
 *    when needed. Passing 0 (or omitting it) keeps the plain `ROW_GAP`
 *    behavior.
 * @param {number} socketLocalX
 * @param {number} socketLocalY
 * @param {number} [textReach] from `outputTextReach(displayed label)`
 * @returns {{x:number, y:number, w:number, h:number}}
 */
export function toggleBoxRect(socketLocalX, socketLocalY, textReach = 0) {
  const gap = Math.max(ROW_GAP, (Number(textReach) || 0) + ROW_LABEL_PAD)
  const x = socketLocalX - gap - ROW_BOX
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

/** What litegraph actually DRAWS for this output -- `label` wins over
 * `name`, matching `NodeSlot.draw()`'s own `label ?? localized_name ?? name`
 * precedence (switcher.js's identical helper for its input rows). */
function displayText(output) {
  return (output && (output.label || output.name)) || ''
}

/**
 * Sets or clears *output*'s display label. `output.name` -- the backend's
 * `RETURN_NAMES` contract AND the key the `toggles` map is written under --
 * is never touched, so a rename is purely cosmetic and can never repoint a
 * wire or silently disable a slot (switcher.js's identical rule for its
 * input rows, FORMAT.md section 6.4). Empty/whitespace DELETES the property
 * rather than storing `""`, so litegraph's own `label || name` fallback
 * shows `out_N` again immediately.
 */
function setOutputLabel(node, output, label) {
  const trimmed = (label ?? '').trim()
  if (trimmed) output.label = trimmed
  else delete output.label
  node.graph?.setDirtyCanvas(true, true)
}

/** Best-effort active LGraphCanvas for callbacks that don't receive one
 * (switcher.js's identical helper). */
function activeCanvas() {
  return app?.canvas ?? null
}

/**
 * Opens the rename editor for *output*: `LGraphCanvas.prompt` where the fork
 * has it (present on 1.45.21), else `window.prompt`. `window.prompt` returns
 * `null` on Cancel but `""` on an intentional OK-with-empty-field, so only
 * the `null` case is skipped -- clearing the field resets to `out_N`.
 */
function promptForOutputLabel(node, output, canvas, event) {
  const current = output.label || ''
  const commit = (value) => {
    if (value === null || value === undefined) return
    setOutputLabel(node, output, String(value))
  }
  try {
    if (canvas && typeof canvas.prompt === 'function') {
      canvas.prompt('Output name', current, commit, event)
      return
    }
  } catch (error) {
    console.warn(PREFIX, 'canvas.prompt failed; falling back', error)
  }
  commit(window.prompt('Output name', current))
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
 * Reconciles the toggle map with which `out_N` slots are currently VISIBLE:
 * a hidden slot is recorded as `false`, a visible one keeps whatever the
 * user set, and a slot that has just BECOME visible is cleared back to
 * enabled.
 *
 * The hidden-means-`false` half is load-bearing, not bookkeeping (owner
 * question 2026-07-27). The backend has a fixed EIGHT slots and treats an
 * ABSENT key as enabled -- deliberately, so a no-frontend `/prompt` caller
 * who has never heard of this widget still gets every output. But the node
 * only shows `Outputs` of them, so with the default 3 visible and all three
 * switched off, out_4..out_8 were still "enabled" as far as the backend
 * could tell. `check_lazy_status` then still had to request `image`, and the
 * whole upstream chain ran to feed five outputs that do not exist on the
 * node and cannot be wired to anything. Recording hidden slots as `false`
 * makes the serialized state say what the user can actually see, so
 * "everything off" on canvas really is all-off to the backend and the
 * upstream is genuinely skipped. Verified on the rig both ways.
 *
 * The just-became-visible half is switcher.js's own anti-stale rationale
 * (its pruneToggles drops entries for no-longer-CONNECTED `image_N`:
 * "keeping a stale `false` on a disconnected... slot would silently disable
 * a DIFFERENT image later re-wired into that same slot number"). Same
 * hazard here in reverse: without the clear, raising `Outputs` would reveal
 * a socket that is already switched off for no reason the user can see.
 */
function pruneToggles(node) {
  const widget = getTogglesWidget(node)
  if (!widget) return
  const map = readToggles(node)
  const visibleNames = new Set(outputEntries(node).map((entry) => entry.name))
  let changed = false

  for (const key of Object.keys(map)) {
    // A key for a slot that is visible again: clear it, so a revealed
    // socket always starts enabled.
    if (!visibleNames.has(key) && !OUTPUT_NAME_RE.test(key)) {
      delete map[key] // foreign key (hand-edited workflow) -- drop entirely
      changed = true
    }
  }
  for (let n = 1; n <= MAX_OUTPUTS; n++) {
    const name = outputName(n)
    if (visibleNames.has(name)) continue
    if (map[name] !== false) {
      map[name] = false // hidden slot: genuinely off, not merely unmentioned
      changed = true
    }
  }
  if (changed) writeToggles(node, map)
}

/** Clears the toggle entries for slots that just BECAME visible, so a
 * revealed socket starts enabled rather than inheriting the `false`
 * `pruneToggles` wrote while it was hidden. Called by
 * `applyVisibleOutputCount` with the names revealed by THIS change only --
 * a slot the user switched off while it was visible must keep that state. */
function clearTogglesFor(node, names) {
  if (!names.length) return
  const map = readToggles(node)
  let changed = false
  for (const name of names) {
    if (name in map) {
      delete map[name]
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
  // BOTH flags, deliberately (2026-07-29, owner's "uptick in issues using my
  // mac" report): litegraph's canvas renderer hides on `widget.hidden`, but
  // the Vue-nodes renderer ("New node design") decides visibility from
  // `widget.options.hidden` (useProcessedWidgets.ts: `options.hidden ?? false`,
  // verified in this rig's frontend source maps) and IGNORES `widget.hidden`
  // -- so with only the canvas flag, this internal widget leaked into the Vue
  // node as a raw editable text field. Canvas mode ignores `options.hidden`
  // right back, so setting both is safe everywhere.
  widget.hidden = true
  widget.options = { ...(widget.options || {}), hidden: true }
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
 *
 * `grow` (v0.40.0) opts in to the auto-grow spare socket and is passed ONLY
 * by the live connection path (`wireOutputGrowth`). Deliberately NOT set on
 * the property or restore paths:
 *   - A number the user typed into the `Outputs` panel is an explicit
 *     instruction; growing past it would fight the edit. Lowering it while the
 *     last socket is wired must clamp back to exactly the wired floor and say
 *     so -- with growth in that path the clamped-up value already covered the
 *     floor, which made the refusal SILENT again (caught on the rig, and the
 *     silent refusal is the original owner-reported bug).
 *   - A loaded workflow's saved output set is authoritative; growing it on
 *     load would mutate (and dirty) a graph the user only opened. A spare that
 *     was there when they saved is still there; one that wasn't appears the
 *     next time they wire the last socket.
 */
function applyVisibleOutputCount(node, { grow = false } = {}) {
  if (!node.properties) node.properties = {}

  const wiredMax = highestWiredSlot(node)
  const stored = node.properties[PROP_OUTPUTS]
  const rangeClamped = clampOutputsCount(stored)
  // The refusal is computed from what was REQUESTED, before any growth, so
  // that growth can never mask it (see the `grow` note above).
  const refused = clampVisibleCount(stored, wiredMax)
  // AUTO-GROW (2026-07-29 owner ask): keep one spare socket below the highest
  // wired one, so wiring the last visible output reveals the next -- the
  // Switcher's growing feel.
  const desired = grow ? growVisibleCount(refused, wiredMax) : refused

  // Compared against the STORED value, not against the other derived numbers:
  // growth moves the target without touching the property, and `grown ===
  // desired` holds in the ordinary growth case, so comparing those two would
  // silently no-op and leave the panel one short of the sockets on screen.
  if (stored !== desired) node.properties[PROP_OUTPUTS] = desired
  if (refused > rangeClamped) {
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

  const revealed = []
  if (desired > currentCount) {
    for (let n = currentCount + 1; n <= desired; n++) {
      if (outputIndexByName(node, outputName(n)) === -1) {
        node.addOutput(outputName(n), OUTPUT_TYPE)
        revealed.push(outputName(n))
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

  // Order matters: prune first (hidden slots -> `false`), THEN clear the
  // ones this call just revealed, so a newly-visible socket starts enabled
  // instead of inheriting the `false` it carried while hidden.
  pruneToggles(node)
  clearTogglesFor(node, revealed)
  resyncSize(node)
}

/**
 * Chains *node*'s `configure` and `onConnectionsChange` so wiring the last
 * visible output reveals the next one -- switcher.js's `wireImageInputGrowth`,
 * applied to outputs. See that function (and switcher.js's file header) for
 * the two findings this inherits verbatim from litegraph's `LGraphNode.ts`,
 * both re-verified against this rig's frontend before writing this:
 *
 * 1. `configure()`'s restore loop dispatches `onConnectionsChange(OUTPUT, i,
 *    true, ...)` once per restored link, from INSIDE its own
 *    `this.outputs.entries()` iteration. Growing the array from there would
 *    splice under litegraph's live iterator, so a `restoring` flag blanks the
 *    hook for exactly the duration of THIS node's `configure()` call. Nothing
 *    is lost: attach()'s `onConfigure` wrap already re-applies the count at
 *    the very end of `configure()`, once `this.outputs` is stable.
 * 2. The LIVE path defers to the next macrotask rather than growing
 *    synchronously: `disconnectOutput` dispatches `onConnectionsChange`
 *    BEFORE it returns, so a synchronous `node.outputs` mutation would run
 *    while litegraph (and the mouse-gesture code that called it) still holds
 *    a slot index into that array. `growScheduled` coalesces an event burst
 *    -- a single drag fires several -- into one pass.
 *
 * Unlike the switcher this recomputes wiring from the slots themselves
 * (`highestWiredSlot`) and ignores the `isConnected` argument entirely, so
 * the restore loop's hardcoded `true` could not misgrow even if it did get
 * through.
 */
function wireOutputGrowth(node) {
  const state = { restoring: false, growScheduled: false }

  function runDeferredGrow(target) {
    state.growScheduled = false
    // A configure() that started while this was pending runs its own pass in
    // onConfigure; a node removed from the graph meanwhile needs no pass.
    if (state.restoring || !target.graph) return
    try {
      // Bail unless the pass would land somewhere other than the stored
      // value. This hook fires on EVERY connect and disconnect, and
      // `applyVisibleOutputCount` also re-derives the node's height
      // (`resyncSize`), which would otherwise snap back a manual resize each
      // time the user wires anything.
      const stored = target.properties?.[PROP_OUTPUTS]
      const wiredMax = highestWiredSlot(target)
      if (growVisibleCount(clampVisibleCount(stored, wiredMax), wiredMax) === stored) return
      applyVisibleOutputCount(target, { grow: true })
    } catch (error) {
      console.warn(PREFIX, 'applyVisibleOutputCount (deferred) failed', error)
    }
  }

  function scheduleGrow(target) {
    if (state.growScheduled) return
    state.growScheduled = true
    setTimeout(() => runDeferredGrow(target), 0)
  }

  const originalConfigure = node.configure
  node.configure = function (...args) {
    state.restoring = true
    try {
      return originalConfigure?.apply(this, args)
    } finally {
      state.restoring = false
    }
  }

  const originalOnConnectionsChange = node.onConnectionsChange
  node.onConnectionsChange = function (type, index, isConnected, linkInfo, slot) {
    let result
    if (typeof originalOnConnectionsChange === 'function') {
      result = originalOnConnectionsChange.apply(this, arguments)
    }
    // Name-matched rather than type-matched: `out_N` is only ever an output,
    // and this stays correct if litegraph's slot-type enum values ever move.
    if (!state.restoring && OUTPUT_NAME_RE.test(slot?.name || '')) {
      scheduleGrow(this)
    }
    return result
  }
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

  // One ALIGNED column (owner ask 2026-07-27: "Can the checkboxes ... line
  // up when the text for the outputs are of different lengths"): every row
  // uses the LONGEST visible label's reach, so mixed-length names give a
  // straight edge of boxes just left of the longest one, instead of a
  // ragged per-row stagger. To the RIGHT of the text is not an option --
  // that region is inside litegraph's own output wire-drag hit box
  // (`socketX - 15` onward, file header), where a click starts a drag.
  // All output sockets share one X, so one shared reach = one shared boxX.
  const entries = outputEntries(node)
  let maxReach = 0
  for (const entry of entries) {
    const reach = outputTextReach(displayText(entry.output))
    if (reach > maxReach) maxReach = reach
  }

  const rects = []
  for (const entry of entries) {
    const pos = outputLocalPos(node, entry.idx)
    if (!pos) continue
    const rect = toggleBoxRect(pos[0], pos[1], maxReach)
    drawToggleBox(ctx, rect.x, rect.y, rect.w, isRowEnabled(node, entry.name), false)
    rects.push({ name: entry.name, x: rect.x, y: rect.y, w: rect.w, h: rect.h })
  }
  node.__epsDistributorRowRects = rects
}

/**
 * The `out_N` row whose socket sits nearest *localY*, for the
 * double-click-anywhere-in-the-row path. Mirrors switcher.js's `rowAtLocalY`
 * but reads the row rects `drawRowToggles` already cached, so it can never
 * disagree with what was drawn.
 */
function rowAtLocalY(node, localY) {
  for (const entry of outputEntries(node)) {
    const pos = outputLocalPos(node, entry.idx)
    if (pos && Math.abs(localY - pos[1]) <= ROW_BOX) return entry
  }
  return null
}

/**
 * Double-click an output to rename it (owner ask 2026-07-27: "You should be
 * able to name the outputs").
 *
 * Two hooks, because litegraph dispatches the two halves of a row through
 * different branches of `_processNodeClick` (module docstring):
 * `onOutputDblClick(i, e)` fires when the double-click lands in the output's
 * OWN hit region -- the socket dot and the ~30x20 box around it, registered
 * as `pointer.onDoubleClick` in the outputs loop, which `return`s before
 * anything else. `onDblClick(e, pos)` covers the rest of the row, including
 * our toggle box (deliberately drawn outside that hit region). The title bar
 * is excluded via litegraph's own `pos[1] < 0` signal so double-clicking the
 * title still renames the NODE, not an output.
 */
function wireOutputRename(node) {
  const originalOnOutputDblClick = node.onOutputDblClick
  node.onOutputDblClick = function (index, e) {
    let result
    if (typeof originalOnOutputDblClick === 'function') {
      result = originalOnOutputDblClick.apply(this, arguments)
    }
    try {
      const output = this.outputs?.[index]
      if (output && OUTPUT_NAME_RE.test(output.name || '')) {
        promptForOutputLabel(this, output, activeCanvas(), e)
      }
    } catch (error) {
      console.warn(PREFIX, 'onOutputDblClick rename failed', error)
    }
    return result
  }

  const originalOnDblClick = node.onDblClick
  node.onDblClick = function (e, pos, canvas) {
    let result
    if (typeof originalOnDblClick === 'function') {
      result = originalOnDblClick.apply(this, arguments)
    }
    try {
      if (Array.isArray(pos) && pos[1] >= 0) {
        const entry = rowAtLocalY(this, pos[1])
        if (entry) promptForOutputLabel(this, entry.output, canvas || activeCanvas(), e)
      }
    } catch (error) {
      console.warn(PREFIX, 'onDblClick rename failed', error)
    }
    return result
  }
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
    // Hover text. `NodeTooltip.vue` prefers a canvas widget's own
    // `.tooltip` over the node def's, which is the only route open to a
    // frontend-added widget like this one.
    tooltip:
      'Turn every output row on or off at once. A row that is off skips ' +
      'only the branch wired to that socket; the other branches still run, ' +
      'in the same single pass.',
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
    wireOutputRename(node)
    wireOutputGrowth(node)
    addHeaderWidget(node)

    // Re-prune AFTER any restore (owner failure report 2026-07-27, the
    // all-off-still-ran-the-sampler one, reproduced on the real
    // litegraph restore path): `configure()` restores widgets_values LAST
    // -- after properties, after every attach-time prune -- so a workflow
    // saved before hidden slots were recorded as `false` replays a
    // `toggles` that covers only the visible sockets, and out_4..out_8
    // silently read as ENABLED again. `onConfigure` fires at the very end
    // of `configure()` (both whole-workflow load AND a pasted node), which
    // makes it the one hook that can re-assert hidden-slots-off over the
    // restored value. The backend is no longer fooled either way
    // (`_wired_slots` -- unwired sockets never force the upstream), so
    // this is state hygiene: what the widget SAYS should match what the
    // node SHOWS.
    const originalOnConfigure = node.onConfigure
    node.onConfigure = function (info) {
      const result = originalOnConfigure?.apply(this, arguments)
      try {
        applyVisibleOutputCount(this)
      } catch (error) {
        console.warn(PREFIX, 'post-configure re-prune failed', error)
      }
      return result
    }

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
