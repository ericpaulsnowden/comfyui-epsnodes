/**
 * @file EPS Number Controller frontend (FORMAT.md proposed section 6.17).
 * Exports the `init()`/`attach(node)` hooks `web/eps_image.js` calls;
 * `attach` no-ops for every node type other than `EPSNumberController`.
 *
 * Owner's ask, verbatim: "It could have any number of outputs. Each output
 * would have a field attached to it where a user could type in a number.
 * This would allow all numerical values for a workflow to be saved in one
 * place, and for those values to all be controlled at the same time with
 * our universal state controller."
 *
 * Contract (backend built in parallel against this exact shape -- frozen,
 * do not deviate): the backend declares `MAX_SLOTS` (16) fixed outputs,
 * `RETURN_NAMES` = `num_1`..`num_16`, every one typed the wildcard `*`.
 * Because `RETURN_NAMES` is a class-level tuple it can never carry a
 * per-instance user name, so THIS file owns naming: each visible socket's
 * `.label` is set from the row's own name field. The one real, serialized
 * widget is `values` -- a hidden STRING holding a JSON OBJECT keyed
 * `num_N`, e.g. `{"num_1": {"name": "steps", "value": 30, "type": "INT"},
 * "num_2": {"name": "cfg", "value": 7.5, "type": "FLOAT"}}`. `name` is a
 * possibly-empty string, `value` a JSON number, `type` one of
 * `"INT"`/`"FLOAT"`/`"*"` (unwired) -- this file writes `type` so the
 * backend can coerce without re-deriving it. A slot with nothing worth
 * remembering (unwired, unnamed, value at the blank default, never
 * disabled) is simply ABSENT from the map -- the same sparse convention
 * `distributor.js`'s `toggles` widget uses, kept minimal on purpose. Two
 * MORE fields, both owner ask (below): `enabled` (bool, default/absent =
 * `true`) and `links` (array of `{"node": <id>, "input": "<name>"}`, the
 * remembered fan-out targets a disabled row detached). The backend never
 * reads either -- `_coerce_slot` only ever looks at `value`/`type` -- so no
 * backend change was needed to add them; the §6.16 state registry's
 * `json_object` kind already covers arbitrary extra keys, which is exactly
 * what lets the Universal State Controller save/recall which rows are on
 * for free.
 *
 * **Per-row enable checkbox (owner ask, verbatim: "each item should also
 * have a checkbox that can be turned off to allow the thing it is
 * connected to to use it's original value").** Rig-verified: ComfyUI has
 * no "pass through, ignore me" execution primitive -- an `ExecutionBlocker`
 * kills the whole downstream branch, the opposite of the ask -- and a
 * target's own widget VALUE is untouched by a link the entire time it is
 * connected (steps=42, wire num_1 into it, steps is still 42 underneath;
 * `targetNode.disconnectInput(slot)` then leaves `inputs[slot].link = null`
 * with that same 42 now live again). So **the only lever is the wire
 * itself**: unchecking a row remembers every current target
 * (`collectOutputTargets`, keyed by `{node id, input NAME}` -- id survives
 * a same-workflow save/reload exactly the way link `origin_id`/`target_id`
 * already have to; name survives the "inputs restore by name" law even if
 * a slot index ever shifts) and detaches them all
 * (`disconnectAllTargets`, one `target.disconnectInput(...)` per
 * remembered link -- the exact rig-verified call). Rechecking replays the
 * remembered list (`reconnectRememberedTargets`, via `node.connect(...)`,
 * the same validated path a manual drag uses -- `wireTypeVeto` still
 * applies), FAILING SOFT per attempt: a target node no longer on the
 * graph, an input that no longer exists, or a slot another link has since
 * claimed are each skipped quietly -- never thrown, never stomping someone
 * else's connection. Dragging a brand-new wire onto a disabled row is
 * unambiguous intent (owner ask 5) and auto-re-enables it
 * (`autoReenableNewlyWiredRows`, run ONLY from the live connection hook --
 * see that function's own docstring for why it must not run from the
 * general sync path). A disabled row's type naturally reverts to `*` once
 * detached (nothing left to derive it from) and recovers on reconnect
 * without any separate "remembered type" bookkeeping -- type adoption
 * already always re-derives from LIVE links. Disabled rows render dimmed
 * (`epsnc-row-disabled`) so the state reads at a glance without needing
 * the checkbox's own state consulted first.
 *
 * **Universal State Controller Apply must re-wire, not just repaint**
 * (owner ask 4). An Apply writes this node's `values` widget directly with
 * nothing else in the loop -- the SAME race the "Universal State
 * Controller compatibility" paragraph below already covers for repainting
 * checkboxes/fields, but flipping `enabled` in the JSON alone changes
 * nothing about the actual graph. `applyEnabledStateToWiring` (run every
 * `syncNode` pass, from every trigger) diffs each row's STORED `enabled`
 * against its LIVE wiring and corrects the graph to match: stored OFF but
 * still wired -> detach (capturing fresh live targets, since whatever
 * `links` the applied JSON carried may be stale); stored ON but not wired
 * -> attempt reconnect from `links`. Idempotent by construction (a row
 * already in agreement is a no-op), which is what lets it run
 * unconditionally on every pass rather than only from a detected external
 * write.
 *
 * **No canvas drawing, by construction.** Every other hand-drawn EPS
 * control (Switcher's/Distributor's per-row toggles) has to carry a
 * `warnIfVueNodesMode` entry in `web/eps_image.js` because
 * `onDrawForeground`/`onMouseDown` never run under ComfyUI's Vue node
 * renderer (FORMAT.md section 7.5). This node has no on-canvas control at
 * all -- one `addDOMWidget` panel, exactly resolution.js's size-grid /
 * notebook.js's two-pane editor -- so it needs no entry in that set and no
 * escape-hatch toast; DOM widgets render identically in both renderers.
 *
 * **Per-SLOT type adoption, not per-node (the load-bearing difference from
 * `distributor.js`).** Distributor adopts ONE type for the whole node from
 * its single `image` input plus every `out_N` fan-out link combined --
 * `resolveAdoptedType` there is called ONCE per node, over every candidate
 * pooled together. This node has no input at all; each `num_N` output is
 * wired to a DIFFERENT downstream target independently, so `reconcileNode`
 * below calls the SAME pure `resolveAdoptedType` helper ONCE PER VISIBLE
 * SLOT, over that slot's own links only (`collectSlotLinkTypes(node,
 * idx)`). Wire row 1 to KSampler's `steps` -> row 1 alone becomes INT; wire
 * row 2 to `cfg` -> row 2 alone becomes FLOAT; disconnect row 1 -> it alone
 * reverts to `*`. Nothing about one row's wiring ever touches another row's
 * adopted type.
 *
 * **The allowlist must ACTIVELY REFUSE, not just label (rig finding,
 * ComfyUI v0.31.1 against the real frontend + shipped backend).** Every
 * `num_N` output is declared `*`, and litegraph's own `isValidConnection`
 * already accepts anything against a wildcard slot -- confirmed on the rig:
 * with no veto installed, `num_N` connected successfully to
 * `steps`(INT)/`cfg`(FLOAT) **and also** `sampler_name`(COMBO)/
 * `model`(MODEL)/`images`(IMAGE)/`filename_prefix`(STRING), no error, the
 * link just took the target's type. So `ALLOWED_TYPES = ['INT', 'FLOAT']`
 * is enforced the same way `distributor.js`'s mechanism 4 enforces its own
 * allowlist: `wireTypeVeto` below installs `node.onConnectOutput`, which
 * `LGraphNode.ts`'s `connectSlots` calls right after `isValidConnection`
 * passes and aborts the connection the instant it returns exactly `false`
 * -- quiet (a toast, no exception) and non-destructive (the link simply
 * never forms). Also confirmed on the rig: KSampler's `seed`/`steps`/
 * `cfg`/`denoise` are already real `node.inputs` entries with a concrete
 * `.type` from the start (`isWidget: true`) -- there is no
 * "convert-widget-to-input" step to special-case; `collectSlotLinkTypes`
 * just reads the target input's live `.type` (falling back to the link's
 * own recorded `.type` for the same restore-ordering reason
 * `distributor.js`'s `collectLinkTypes` documents).
 *
 * **INT rounds half-away-from-zero (backend v-next, rig-verified: 2.5 ->
 * 3).** `Math.round` does NOT do this for negatives (`Math.round(-2.5)` is
 * `-2`, the backend says `-3`) -- `roundHalfAwayFromZero` below matches the
 * backend exactly. The STORED JSON value is left exactly as typed (a
 * user's original `2.5` survives if the row is later rewired to FLOAT or
 * disconnected); only the DISPLAYED number in an INT-adopted row's box is
 * snapped, via `displayValueFor`, so the panel can never show `2.5` while
 * the socket actually sends `3`. This is a pure, idempotent, re-derived-
 * every-render transform of the stored value -- not a second place a
 * number is held -- so it does not conflict with the re-render law below.
 *
 * **No `Outputs` property, unlike `distributor.js`.** Distributor lets the
 * user hand-set a visible-output COUNT via a right-click property, which is
 * why it needs a whole refuse-and-clamp-back-up mechanism (a typed number
 * can disagree with the wiring). This node has no such property: visible
 * row count is ALWAYS the pure derived value
 * `max(highestWiredSlot, highestSlotCarryingNameOrValue) + 1`, clamped
 * `[MIN_SLOTS, MAX_SLOTS]` (`computeVisibleRowCount`) -- there is always
 * exactly one blank spare row at the bottom to type into, and the node
 * grows as it fills. Because the formula itself takes the wired floor,
 * `applyVisibleRowCount` can never be asked to remove a wired slot, so
 * (unlike Distributor) no separate refusal path is needed -- one fewer
 * moving part.
 *
 * **A live edit must never delete the row out from under the cursor.**
 * Discovered while designing this file, not on the rig: naively re-running
 * `computeVisibleRowCount` on every keystroke means clearing a row's name
 * back to blank (a normal thing to do mid-retype) can make it stop
 * "carrying content" and shrink the panel -- which, if that row happened to
 * be the highest one, would `removeOutput()` the very socket the user's
 * cursor is sitting in. `applyVisibleRowCount` therefore ALSO floors the
 * desired count at whichever row currently has focus
 * (`focusedSlotFloor`), so an in-progress edit always keeps its own row
 * visible regardless of what it currently contains; the floor lifts the
 * moment focus leaves (an `input`'s `blur` handler re-syncs), letting a
 * genuinely abandoned edit shrink the panel back down then.
 *
 * **THE RE-RENDER LAW (owner rule, verbatim: "Just switching between
 * workflows shouldn't ever reset anything in our nodes").** A tab switch /
 * undo / redo / workflow reload tears down and rebuilds every node's DOM
 * widget -- `checkpoint_switcher.js`'s file header traces the exact
 * mechanics: `nodeCreated` (this file's `attach()`) fires again from the
 * fresh node's constructor, BEFORE `configure()` restores `widgets_values`
 * onto it. Three separate guarantees, together, are what make this safe:
 *   1. The `values` widget is the ONLY durable store. `renderRows` always
 *      repaints FROM `state.widget.value` (freshly parsed every call) --
 *      never from a cached JS-side copy of a row's name/value -- so a
 *      rebuilt node's fresh `attach()` call and the later `onConfigure`
 *      call (once `configure()` has actually restored the real JSON) both
 *      converge on the same picture regardless of which ran first. This is
 *      `checkpoint_switcher.js`'s own "whichever of {the fetch, onConfigure}
 *      finishes LAST produces the final render" argument, adapted: here the
 *      two race legs are attach()'s initial paint (covers a value somehow
 *      already on the widget by attach time, e.g. a pasted node's clone)
 *      and the `onConfigure` wrap (covers the ordinary reload case, where
 *      the real value arrives strictly after attach's first paint).
 *   2. `renderRows` NEVER writes to `state.widget` -- reading and rendering
 *      is one direction only; every widget write happens from an explicit
 *      user action (`writeSlotField`, `reconcileNode`'s type-only updates).
 *      A repaint can therefore run any number of times, from any hook, with
 *      no side effect on the stored JSON.
 *   3. Per-FIELD, not per-row, `document.activeElement` guards
 *      (`web/lora_library/notebook.js`'s `populateEditor`'s
 *      `nameMidEdit`/`textMidEdit` pattern, generalized to two independent
 *      inputs per row instead of one field per pane): `renderRows` only
 *      ever writes `row.nameInput.value`/`row.valueInput.value` when that
 *      exact element is NOT the focused one. A rebuild-then-repaint that
 *      lands mid-keystroke therefore never overwrites the field being
 *      typed into, while every OTHER field (including the other one on the
 *      SAME row) still repaints normally.
 * Verified directly, not just argued: `tests/test_number_controller_js.py`
 * simulates a rebuild (a fresh `state`/DOM built against a widget that
 * already carries saved JSON, i.e. `onConfigure` already ran) with a
 * stubbed `document.activeElement` pinned to one row's name input holding
 * unsaved text, and asserts a `renderRows` pass leaves that one input's
 * `.value` untouched while every other field converges on the widget.
 * `values`/`enabled`/`links` all live in the SAME one widget, so the three
 * guarantees above cover the checkbox/remembered-links state exactly as
 * they cover name/value -- there is no separate store for it that could
 * fall out of step across a rebuild.
 *
 * **Universal State Controller compatibility (FORMAT.md section 6.16).**
 * The backend already declares `EPS_STATE_WIDGETS` for `values`
 * (`json_object`, key pattern `^num_\d+$`), so an Apply from that
 * controller writes this node's `values` widget directly
 * (`widget.value = ...; widget.callback?.()`) with nothing else on this
 * node in the loop. FORMAT.md section 6.16's v0.84.0 fix is exactly this
 * situation pack-wide: "the DOM-panel nodes hold their own render state and
 * never re-read a programmatically written widget, so the visible UI
 * stayed stale." `installExternalWriteSubscription` below subscribes ONCE
 * (module-scope guard, shared across every attached node) to `api.js`'s
 * `announceWidgetsChangedExternally`, routing by node identity through a
 * `__epsNcReload` seam stamped on the node at attach time -- this node's
 * own version of every other DOM-panel module's identical fix
 * (checkpoint_switcher.js's `installExternalWriteSubscription`, this
 * pack's established shape for it).
 *
 * Fail-soft throughout (FORMAT.md section 7/8): `parseValues` never throws
 * on a malformed `values` string -- it degrades to `{}`, which
 * `computeVisibleRowCount` turns into exactly one blank spare row, never an
 * exception and never zero rows. `attach()` and every hook it installs are
 * wrapped in try/catch -> `console.warn`.
 */

import { app } from '../../../scripts/app.js'
import { subscribeWidgetsChangedExternally } from '../lora_library/api.js'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

export const CLASS_ID = 'EPSNumberController'
const PREFIX = '[eps_image:number_controller]'
const NODE_TITLE = 'EPS Number Controller'

/** The one real, serialized widget (module docstring). */
export const VALUES_WIDGET_NAME = 'values'

/** Backend's fixed `RETURN_NAMES` ceiling/floor -- see nodes_number_controller.py's
 * own `MAX_SLOTS`; the two must agree (mirrors distributor.js's
 * `test_frontend_ceiling_matches_backend` precedent). */
export const MAX_SLOTS = 16
export const MIN_SLOTS = 1

/** Litegraph's own "matches anything" wildcard, and this node's own
 * unwired-slot JSON type. */
export const WILDCARD = '*'

/** The only two concrete types a row may adopt (owner: "one type per node,
 * just do text for now" was Distributor's call for images/text; the owner's
 * equivalent instruction here is numbers only). Enforced by `wireTypeVeto`
 * below -- see module docstring's rig-finding paragraph for why this must
 * be an active refusal, not just a label. */
export const ALLOWED_TYPES = ['INT', 'FLOAT']

/** `num_${n}` -- the backend's RETURN_NAMES naming rule. Exported for
 * tests, mirrors distributor.js's `outputName`. */
export function slotName(n) {
  return `num_${n}`
}

const OUTPUT_NAME_RE = /^num_(\d+)$/

/** Inverse of slotName(): the slot number for a name matching `num_<N>`, or
 * null for anything else. Exported for tests. */
export function parseSlotNumber(name) {
  const match = typeof name === 'string' ? OUTPUT_NAME_RE.exec(name) : null
  return match ? Number(match[1]) : null
}

/** DOM widget name/type for `addDOMWidget` -- notebook.js's/checkpoint_switcher.js's
 * own `WIDGET_NAME`/`WIDGET_TYPE` twin. */
const PANEL_WIDGET_NAME = 'eps_nc_panel'
const PANEL_WIDGET_TYPE = 'eps_number_controller_panel'

/** Width floor (FORMAT.md section 7.2's house rule) -- wide enough for the
 * enable checkbox, a name field, a value field, and a type badge without
 * wrapping at the platform's default UI font. */
export const MIN_NODE_WIDTH = 290

/** Panel height constants -- `panelHeightFor` below; kept small since this
 * is a plain list of rows, not a scrollable editor. */
const HINT_HEIGHT = 20
const ROW_HEIGHT = 26
const PANEL_PADDING = 10

/** Nodes we've already wired, guarding against a double `nodeCreated`
 * (distributor.js's/switcher.js's identical guard). */
const attachedNodes = new WeakSet()

// ---------------------------------------------------------------------------
// Pure helpers -- exported so tests/test_number_controller_js.py can drive
// the values-map/row-count/type-adoption contract under Node without a
// litegraph node stub. No node/ctx/DOM in any of these signatures.
// ---------------------------------------------------------------------------

/**
 * Parses the `values` widget's raw string into a plain object. Never
 * throws -- a malformed value (never expected from this file's own writes,
 * but a hand-edited workflow is always possible) degrades to "no rows have
 * content", matching distributor.js's `parseToggles` fallback shape
 * exactly (module docstring's fail-soft law). Exported for tests.
 */
export function parseValues(rawValue) {
  try {
    const parsed = JSON.parse(rawValue || '{}')
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {}
  } catch (error) {
    return {}
  }
}

/** Inverse of parseValues() -- exported so the round-trip is itself a
 * driveable, pure test rather than an assumption. */
export function serializeValues(map) {
  return JSON.stringify(map || {})
}

/**
 * Whether *entry* (one `values[num_N]` record, or undefined) is worth
 * keeping a row visible for on its own -- a non-empty `name`, a `value`
 * that differs from the blank-row default of `0`, OR the row being
 * explicitly DISABLED (`enabled === false`, the checkbox row-enable
 * feature -- module docstring). This is the literal meaning of the
 * contract's "highest slot carrying a name or a value", extended by one
 * case: a deliberately unchecked row is meaningful state (it carries
 * remembered links to restore on re-check) even when its name/value are
 * still blank, so it must not silently vanish from the map/panel the
 * moment it stops being wired. (Documented trade-off, unchanged from
 * before: a deliberately-typed `0` with no name and nothing disabled reads
 * the same as never having typed anything -- accepted for a "just a plain
 * number box" node with no separate touched/blank flag.) Exported for
 * tests.
 */
export function slotCarriesContent(entry) {
  if (!entry || typeof entry !== 'object') return false
  if (entry.enabled === false) return true
  const name = typeof entry.name === 'string' ? entry.name.trim() : ''
  if (name !== '') return true
  const value = entry.value
  return typeof value === 'number' && Number.isFinite(value) && value !== 0
}

/**
 * Whether a row reads as enabled: enabled unless *entry* explicitly records
 * the literal boolean `false` -- distributor.js's `isSlotEnabled`
 * (`!== false`, not `!== false && 'enabled' in entry`) applied to this
 * node's own per-row checkbox, so any other value (`true`, `0`, `null`,
 * missing) still reads as ON. Absent means ON, matching "Default ON for a
 * new row" (owner ask). Exported for tests.
 */
export function isRowEnabled(entry) {
  return entry?.enabled !== false
}

/**
 * Sanitizes a `values[num_N].links` value -- the remembered fan-out targets
 * a checkbox-off row detached, restored on re-check. Never throws: not an
 * array degrades to `[]`; each item must be a plain object with a
 * non-empty string `input` (the target's input NAME -- inputs restore by
 * name, module docstring) and a `node` id that is a number or string
 * (litegraph node ids are numeric, but a hand-edited/foreign workflow could
 * plausibly stringify one); anything else is dropped, one bad item never
 * poisoning its neighbours. Exported for tests.
 */
export function normalizeRememberedLinks(raw) {
  if (!Array.isArray(raw)) return []
  const out = []
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue
    const nodeId = item.node
    const input = item.input
    if (typeof input !== 'string' || input === '') continue
    if (typeof nodeId !== 'number' && typeof nodeId !== 'string') continue
    out.push({ node: nodeId, input })
  }
  return out
}

/**
 * The visible row count: `max(highestWiredIndex, highest slot carrying
 * content) + 1`, clamped `[MIN_SLOTS, MAX_SLOTS]` -- there is always
 * exactly one blank spare row at the bottom to type into, and the node
 * grows as it fills (module docstring). Because the wired index is one of
 * the two terms taken the MAX over, this can never fall below
 * `highestWiredIndex` -- the "never shrink below a wired slot" rule holds
 * by construction, with no separate refusal path needed (contrast
 * distributor.js's `clampVisibleCount`, which needs one because it also has
 * to satisfy a user-typed property). *highestWiredIndex* of `0`/`null`/
 * non-finite means "nothing wired". Pure; exported for tests -- this is the
 * function the real `applyVisibleRowCount` (node-bound, adds a focus floor
 * on top -- module docstring) actually calls.
 */
export function computeVisibleRowCount(valuesMap, highestWiredIndex) {
  let highestContent = 0
  if (valuesMap && typeof valuesMap === 'object') {
    for (const key of Object.keys(valuesMap)) {
      const n = parseSlotNumber(key)
      if (n == null) continue
      if (slotCarriesContent(valuesMap[key])) highestContent = Math.max(highestContent, n)
    }
  }
  const wiredRaw = Number(highestWiredIndex)
  const wired = Number.isFinite(wiredRaw) ? Math.max(0, Math.round(wiredRaw)) : 0
  const highest = Math.max(wired, highestContent)
  return Math.min(MAX_SLOTS, Math.max(MIN_SLOTS, highest + 1))
}

/** Display label for a row given its user-typed *name* -- falls back to
 * `num_N` when unnamed (blank/whitespace-only), exactly like the socket's
 * own `.label` (contract: "every row carries a name" but naming is
 * optional per-row). Exported for tests. */
export function labelForSlot(name, n) {
  const trimmed = typeof name === 'string' ? name.trim() : ''
  return trimmed || slotName(n)
}

/**
 * Lenient numeric parse for the value input's live `input` event: `null`
 * for anything not a complete, finite number (empty string, a bare `-`, a
 * trailing `.`, `NaN`/`Infinity` spellings) so the caller knows NOT to
 * commit yet -- module docstring's "never clobber mid-edit" reasoning
 * applied to values, not just focus: an incomplete number must not
 * overwrite the last COMPLETE one a user typed. Exported for tests.
 */
export function parseNumericInput(text) {
  if (typeof text === 'number') return Number.isFinite(text) ? text : null
  if (typeof text !== 'string') return null
  const trimmed = text.trim()
  if (trimmed === '') return null
  const n = Number(trimmed)
  return Number.isFinite(n) ? n : null
}

/**
 * Rounds half-away-from-zero, matching the backend's INT coercion exactly
 * (rig-verified: `2.5 -> 3`). `Math.round` alone disagrees on negatives
 * (`Math.round(-2.5) === -2`, backend says `-3`) -- module docstring.
 * Exported for tests.
 */
export function roundHalfAwayFromZero(value) {
  return Math.sign(value) * Math.round(Math.abs(value))
}

/**
 * The number to SHOW in an INT-adopted row's box -- the stored *value*
 * unchanged for FLOAT/unwired rows, but snapped via
 * `roundHalfAwayFromZero` for an INT-adopted one, so the panel can never
 * display `2.5` while the socket actually sends `3` (module docstring).
 * Pure and re-derived on every render -- the STORED JSON value is never
 * mutated by this. Exported for tests.
 */
export function displayValueFor(value, jsonType) {
  const n = typeof value === 'number' && Number.isFinite(value) ? value : 0
  return jsonType === 'INT' ? roundHalfAwayFromZero(n) : n
}

// ---------------------------------------------------------------------------
// Type adoption -- pure helpers. No node/graph/DOM in any signature, so
// tests/test_number_controller_js.py drives these directly. Structurally
// identical to distributor.js's own mechanism-4 helpers (same algorithm),
// duplicated rather than imported -- "each eps_image/*.js module keeps its
// own copy" (distributor.js's file header states this house rule
// explicitly for its own verbatim-copied helpers).
// ---------------------------------------------------------------------------

/** Litegraph's own "matches anything" set -- mirrors `isValidConnection`'s
 * generic check. Not exported: an internal detail of the type-adoption
 * helpers, not part of the tested contract (distributor.js's identical
 * choice for its own `isGenericSlotType`). */
function isGenericSlotType(type) {
  return type === '' || type === '*' || type === 0 || type === null || type === undefined
}

/**
 * Whether *type* is compatible with `ALLOWED_TYPES`: true for litegraph's
 * generic forms, true when any comma-separated member of *type*
 * (case-insensitive) is `INT` or `FLOAT`, else false. This is the function
 * `wireTypeVeto` actually calls to decide whether to abort a connection --
 * see module docstring's rig-finding paragraph for why that veto has to
 * exist at all. Exported for tests.
 */
export function isAllowedType(type) {
  if (isGenericSlotType(type)) return true
  const members = String(type).split(',')
  return members.some((member) => ALLOWED_TYPES.includes(member.trim().toUpperCase()))
}

/**
 * The type one slot should adopt, given every currently-relevant candidate
 * type for THAT SLOT alone (module docstring's per-slot-not-per-node
 * paragraph -- the caller, `collectSlotLinkTypes`, only ever gathers one
 * output's own links). Generic entries are ignored; returns the first
 * CONCRETE type encountered, or the wildcard if none is concrete. `mixed`
 * is true when more than one DISTINCT concrete type is present (multiple
 * links off the same slot disagreeing) -- warned about, never acted on by
 * disconnecting anything. Pure; exported for tests.
 */
export function resolveAdoptedType(candidates) {
  const distinct = []
  for (const candidate of candidates) {
    if (isGenericSlotType(candidate)) continue
    const value = String(candidate)
    if (!distinct.includes(value)) distinct.push(value)
  }
  if (distinct.length === 0) return { type: WILDCARD, mixed: false }
  return { type: distinct[0], mixed: distinct.length > 1 }
}

/**
 * Maps an adopted type (from `resolveAdoptedType`, which is NOT restricted
 * to `ALLOWED_TYPES` on its own -- it just picks the first concrete
 * candidate) down to exactly what the wire contract allows in the JSON:
 * `'INT'`/`'FLOAT'`, or `'*'` for the wildcard AND for any foreign concrete
 * type that should never reach here in practice (the veto blocks it at
 * connect time) but is defended against anyway -- a hand-edited workflow
 * can always produce a link this file never approved. Exported for tests.
 */
export function jsonTypeFor(adoptedType) {
  if (isGenericSlotType(adoptedType)) return WILDCARD
  const upper = String(adoptedType).toUpperCase()
  return ALLOWED_TYPES.includes(upper) ? upper : WILDCARD
}

/** Short badge text for a row's type indicator -- `'INT'`/`'FLOAT'` as-is,
 * `'any'` for the wildcard (lowercase, matching distributor.js's own `'any'`
 * label for its unwired `image` input). Exported for tests. */
export function typeBadgeFor(jsonType) {
  return jsonType === 'INT' || jsonType === 'FLOAT' ? jsonType : 'any'
}

// ---------------------------------------------------------------------------
// Node / widget lookups (distributor.js's/checkpoint_switcher.js's identical
// helpers, copied verbatim -- each eps_image/*.js module keeps its own
// copy).
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

function outputIndexByName(node, name) {
  return (node.outputs || []).findIndex((output) => output?.name === name)
}

/** Looks up an LLink object by id, tolerant of either shape this
 * frontend's forks have used for `graph.links` (a plain object/array
 * indexed by id, or a `Map`) -- distributor.js's/frame_saver.js's identical
 * defensive lookup. Returns null if unreachable. */
function linkById(graph, linkId) {
  if (linkId == null || !graph) return null
  return graph.links?.[linkId] ?? graph.links?.get?.(linkId) ?? null
}

/** The colour to paint a link carrying *type* -- Reroute's own rule
 * (distributor.js's mechanism 4): `LGraphCanvas.link_type_colors[type]`, or
 * `undefined` for the wildcard (falls back to litegraph's default link
 * colour). Guarded: `LGraphCanvas` may not be a global on every fork. */
function linkColorFor(type) {
  if (typeof LGraphCanvas === 'undefined') return undefined
  return LGraphCanvas.link_type_colors?.[type]
}

/**
 * Whether *output* currently carries a real link -- checks BOTH `.links`
 * (settled) and `._floatingLinks` (a link mid-drag, not yet dropped),
 * mirroring `LGraphCanvas.ts`'s own `hasRelevantOutputLinks`
 * (distributor.js's/resolution.js's identical function, kept in lockstep
 * across all three files -- a `.links`-only check would let
 * `applyVisibleRowCount` `removeOutput()` a socket the user still has hold
 * of). Exported for tests.
 */
export function isOutputConnected(output) {
  if (!output) return false
  if (Array.isArray(output.links) && output.links.length > 0) return true
  const floating = output._floatingLinks
  if (floating && typeof floating.size === 'number' && floating.size > 0) return true
  return false
}

/** distributor.js's `toast` verbatim -- the only reason this file imports
 * `app` (module docstring). Never throws: a frontend without the toast
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

// --- Node width floor -- self-contained copy, own guard flag (distributor.js's
// identical-shape installMinWidth is the house pattern). ---

function installMinWidth(node, minWidth) {
  if (!node || node.__epsNumberControllerMinWidthInstalled) return
  node.__epsNumberControllerMinWidthInstalled = true
  const originalOnResize = node.onResize
  node.onResize = function (size) {
    if (size && size[0] < minWidth) size[0] = minWidth
    return originalOnResize?.call(this, size)
  }
  // `node.size` is a Proxy over a typed-array view (never an Array) on this
  // frontend -- distributor.js's v0.68.1 fix, ported verbatim. setSize()
  // runs litegraph's `_sizeUpdated` (the Vue layout mirror) and the
  // onResize wrap.
  if (node.size && node.size[0] < minWidth) {
    if (typeof node.setSize === 'function') node.setSize([minWidth, node.size[1]])
    else node.size[0] = minWidth
  }
}

/**
 * Both hide flags, deliberately (FORMAT.md section 7.5): litegraph's canvas
 * renderer hides on `widget.hidden`, but the Vue-nodes renderer decides
 * visibility from `widget.options.hidden` and IGNORES `widget.hidden`
 * outright -- so without the second flag this internal widget would leak
 * into a Vue node as a raw editable text field. Canvas mode ignores
 * `options.hidden` right back, so setting both is safe everywhere.
 */
function hideValuesWidget(node, widget) {
  widget.hidden = true
  widget.options = { ...(widget.options || {}), hidden: true }
  node.graph?.setDirtyCanvas(true, true)
}

// Universal State Controller Apply compatibility (module docstring). One
// shared subscription to api.js's `announceWidgetsChangedExternally()`
// serves every attached number controller -- installed once, idempotently,
// from the first `attach()` call. Routes by NODE IDENTITY (the announce
// entry's own `.node` reference against the `__epsNcReload` seam stamped
// on each attached node below) -- checkpoint_switcher.js's identical
// precedent: no network, just re-derives everything from the widget's
// CURRENT value via the same `syncNode` every other write path uses.
let externalWriteSubscribed = false

function installExternalWriteSubscription() {
  if (externalWriteSubscribed) return
  externalWriteSubscribed = true
  subscribeWidgetsChangedExternally((entries) => {
    for (const entry of entries || []) entry?.node?.__epsNcReload?.()
  })
}

// ---------------------------------------------------------------------------
// Row model -- every currently-visible `num_N` output, node-bound.
// ---------------------------------------------------------------------------

/** @returns {{idx:number, n:number, name:string, output:object, wired:boolean}[]}
 * every currently-visible `num_N` output, in array order (contiguous
 * 1..currentCount -- this file only ever adds/removes at the tail, so a
 * gap can never occur, exactly distributor.js's `outputEntries`
 * invariant). */
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
 * wired -- feeds `computeVisibleRowCount`'s floor. */
function highestWiredSlot(node) {
  let max = 0
  for (const entry of outputEntries(node)) {
    if (entry.wired && entry.n > max) max = entry.n
  }
  return max
}

/**
 * Every slot type currently relevant to ONE output's own adoption --
 * module docstring's per-slot paragraph: called once PER VISIBLE SLOT, not
 * once for the whole node. For each of *idx*'s own links: the TARGET
 * node's declared input type (read live, so a rewired downstream is always
 * current), falling back to the link's own recorded `.type` when the
 * target node can't be found yet (mid-`configure()`, the target may
 * restore AFTER this node -- distributor.js's identical restore-ordering
 * caveat). Tolerant of a missing graph/links throughout -- always returns
 * an array, never throws.
 */
function collectSlotLinkTypes(node, idx) {
  const types = []
  try {
    const graph = node?.graph
    const output = node.outputs?.[idx]
    const links = output?.links
    if (Array.isArray(links)) {
      for (const linkId of links) {
        const link = linkById(graph, linkId)
        if (!link) continue
        const target = graph?.getNodeById?.(link.target_id)
        const targetInput = target?.inputs?.[link.target_slot]
        types.push(targetInput?.type, link.type)
      }
    }
  } catch (error) {
    console.warn(PREFIX, 'collectSlotLinkTypes failed', error)
    return []
  }
  return types
}

/**
 * Every current target of output *idx*, as `{node: id, input: name}` pairs
 * -- what a checkbox-off row needs to remember before detaching (module
 * docstring's checkbox paragraph). Read live from the graph, never from a
 * previously-stored `links` list, so what gets remembered is always
 * whatever is ACTUALLY about to be removed. Tolerant of a missing graph/
 * links/target -- always returns an array, never throws.
 */
function collectOutputTargets(node, idx) {
  const targets = []
  try {
    const graph = node?.graph
    const output = node.outputs?.[idx]
    const links = output?.links
    if (Array.isArray(links)) {
      for (const linkId of links) {
        const link = linkById(graph, linkId)
        if (!link) continue
        const target = graph?.getNodeById?.(link.target_id)
        const targetInput = target?.inputs?.[link.target_slot]
        if (target && targetInput?.name) targets.push({ node: link.target_id, input: targetInput.name })
      }
    }
  } catch (error) {
    console.warn(PREFIX, 'collectOutputTargets failed', error)
  }
  return targets
}

/**
 * Detaches every current link off output *idx* -- the checkbox-off
 * mechanism's actual effect (module docstring): `target.disconnectInput(
 * targetSlot)` per link, the exact call the owner's rig verification used
 * ("Called disconnectInput(slot) -> inputs[slot].link becomes null and the
 * widget value is still 42"). Snapshots `output.links` before iterating --
 * disconnecting mutates that same live array out from under a direct
 * for-of. Never throws: one link failing to resolve does not block the
 * rest.
 */
function disconnectAllTargets(node, idx) {
  try {
    const graph = node?.graph
    const output = node.outputs?.[idx]
    const links = Array.isArray(output?.links) ? [...output.links] : []
    for (const linkId of links) {
      const link = linkById(graph, linkId)
      if (!link) continue
      const target = graph?.getNodeById?.(link.target_id)
      if (!target || typeof target.disconnectInput !== 'function') continue
      target.disconnectInput(link.target_slot)
    }
  } catch (error) {
    console.warn(PREFIX, 'disconnectAllTargets failed', error)
  }
}

/**
 * Replays *remembered* (a `normalizeRememberedLinks`-sanitized list) onto
 * output *idx*, via `node.connect(idx, target, targetSlot)` -- the same
 * validated connection path a manual drag uses, so `wireTypeVeto` still
 * applies. FAILS SOFT per item, exactly the checkbox contract (module
 * docstring): a target node no longer on the graph, an input that no
 * longer exists by that name, or a slot another link has since claimed are
 * each skipped quietly -- this function never throws and never disconnects
 * anything on its own (only `node.connect`'s own veto can decline a
 * reconnect, which it does by simply not connecting, not by touching
 * anything else).
 */
function reconnectRememberedTargets(node, idx, remembered) {
  try {
    const graph = node?.graph
    if (typeof node.connect !== 'function') return
    for (const item of remembered || []) {
      const target = graph?.getNodeById?.(item?.node)
      if (!target) continue // target node no longer exists -- skip quietly
      const targetSlot = (target.inputs || []).findIndex((inp) => inp?.name === item?.input)
      if (targetSlot === -1) continue // input no longer exists -- skip quietly
      if (target.inputs[targetSlot].link != null) continue // already taken -- never stomp
      node.connect(idx, target, targetSlot)
    }
  } catch (error) {
    console.warn(PREFIX, 'reconnectRememberedTargets failed', error)
  }
}

/** Recompute layout after an outputs/rows change: grow the width to fit if
 * needed and set the height ABSOLUTELY -- distributor.js's identical
 * `resyncSize`, minus its no-DOM-widget caveat (this node's DOM widget's
 * own `getMinHeight` is what actually drives the number; this just makes
 * litegraph re-run `computeSize()` against it). */
function resyncSize(node) {
  if (typeof node.computeSize !== 'function' || typeof node.setSize !== 'function') return
  const computed = node.computeSize()
  node.setSize([Math.max(node.size[0], computed[0]), computed[1]])
  node.setDirtyCanvas?.(true, true)
}

/** The slot number of whichever row currently has focus (name OR value
 * input), or `0` if nothing in this panel is focused -- module docstring's
 * "never delete the row out from under the cursor" paragraph. Guarded for a
 * DOM-less environment (never expected in a real browser, but this file is
 * also probed under Node -- FORMAT.md's fail-soft law). */
function focusedSlotFloor(state) {
  if (typeof document === 'undefined') return 0
  const active = document.activeElement
  if (!active) return 0
  for (const row of state.rows) {
    if (row.nameInput === active || row.valueInput === active) return row.n
  }
  return 0
}

/**
 * Reconciles the LIVE graph wiring with each row's STORED `enabled` flag --
 * owner ask 4, "Universal State apply must actually re-wire, not just
 * repaint checkboxes" (module docstring). Runs from `syncNode`, every pass,
 * from every trigger: an ordinary keystroke/blur/restore is a no-op here
 * (stored and live already agree, by construction), and that is exactly
 * what makes it safe to run unconditionally rather than only from a
 * detected external write -- the one case that actually needs it (a
 * Universal State Controller Apply, which only ever touches the `values`
 * JSON, never the graph).
 *
 * Per visible slot: stored OFF but still wired -> the graph hasn't caught
 * up to the newly-applied OFF yet; capture the CURRENT live targets
 * (`collectOutputTargets` -- not whatever `links` the applied JSON
 * happened to carry, which may be stale) and detach them
 * (`disconnectAllTargets`), writing the fresh remembered list back.
 * Stored ON but not wired -> attempt `reconnectRememberedTargets` from the
 * entry's own `links`, skipped entirely when that list is empty (the
 * overwhelmingly common case: an ordinary row that has never been
 * disabled has no `links` to replay). Both directions fail soft by
 * construction (the two helpers they call already do).
 *
 * Deliberately NOT where a wire dragged onto a disabled row gets handled
 * (owner ask 5) -- that is `autoReenableNewlyWiredRows`, run ONLY from the
 * live connection hook, strictly BEFORE this function gets a chance to run
 * on the same pass. See that function's own docstring for why the two
 * must not be merged: this function cannot otherwise tell "a Universal
 * State Apply hasn't caught up yet" (should disconnect) apart from "the
 * user just manually wired this exact row" (should flip the row back on
 * instead) -- both look identical from the data alone (`enabled: false`
 * stored, `wired: true` live).
 */
function applyEnabledStateToWiring(state) {
  const node = state.node
  const widget = state.widget
  if (!widget) return
  const map = parseValues(widget.value)
  for (const entry of outputEntries(node)) {
    const stored = map[entry.name]
    const storedEnabled = isRowEnabled(stored)
    if (!storedEnabled && entry.wired) {
      const remembered = collectOutputTargets(node, entry.idx)
      disconnectAllTargets(node, entry.idx)
      writeSlotField(node, entry.n, { enabled: false, links: remembered })
    } else if (storedEnabled && !entry.wired) {
      const remembered = normalizeRememberedLinks(stored?.links)
      if (remembered.length > 0) {
        reconnectRememberedTargets(node, entry.idx, remembered)
        // ONE attempt, then forget the memory regardless of outcome
        // (`links: undefined` -- JSON.stringify drops it, same as never
        // having been set). Without this, a row a user later unplugs by
        // hand (nothing to do with the checkbox) would silently reconnect
        // itself again on the very next syncNode pass -- this function
        // runs on EVERY pass, including a plain keystroke anywhere in the
        // panel, not just right after a re-enable.
        writeSlotField(node, entry.n, { links: undefined })
      }
    }
  }
}

/**
 * The single formula for "how many rows should be visible RIGHT NOW",
 * shared by every caller that needs it (`applyVisibleRowCount`,
 * `renderRows`, `wireRowSync`'s deferred pass) -- `max(
 * computeVisibleRowCount(map, wired), focusedSlotFloor(state))`. Pulled
 * into one function after a real bug found while testing (not on the rig):
 * `applyVisibleRowCount` and `renderRows` used to compute this SEPARATELY,
 * and `renderRows`'s own copy forgot the focus floor -- so
 * `applyVisibleRowCount` would correctly keep the focused row's OUTPUT
 * SOCKET, but `renderRows` would then rebuild the DOM to a SMALLER count
 * and rip that row's `<input>` elements out from under the user's cursor
 * anyway, even though the underlying socket survived. One formula, called
 * from every site that needs a row count, makes that class of drift
 * impossible.
 */
function desiredRowCount(state) {
  const widget = state.widget
  const map = widget ? parseValues(widget.value) : {}
  const wired = highestWiredSlot(state.node)
  return Math.max(computeVisibleRowCount(map, wired), focusedSlotFloor(state))
}

/**
 * Applies `desiredRowCount(state)` to `node.outputs`: adds missing tail
 * outputs when growing, removes tail outputs when shrinking. Tail-only
 * and strictly LIFO (highest array index first) -- `removeOutput()`
 * splices `node.outputs` by POSITION (distributor.js's/resolution.js's
 * shared finding: outputs resolve POSITIONALLY against the backend's fixed
 * `RETURN_TYPES` tuple, so only genuine tail removal is safe). The pure
 * formula's own wired floor already makes a WIRED removal impossible; the
 * focus floor (module docstring) makes removing the row under active edit
 * impossible too. A freshly revealed spare socket always starts at the
 * WILDCARD type (unlike distributor.js's spares, which inherit the node's
 * single adopted type -- there is no "the node's type" here, every slot is
 * independent).
 */
function applyVisibleRowCount(state) {
  const node = state.node
  const widget = state.widget
  if (!widget) return
  const desired = desiredRowCount(state)

  const entries = outputEntries(node)
  const currentCount = entries.length

  if (desired > currentCount) {
    for (let n = currentCount + 1; n <= desired; n++) {
      if (outputIndexByName(node, slotName(n)) === -1) node.addOutput(slotName(n), WILDCARD)
    }
  } else if (desired < currentCount) {
    const toRemove = entries.filter((entry) => entry.n > desired).sort((a, b) => b.idx - a.idx)
    for (const entry of toRemove) node.removeOutput(entry.idx)
  }

  resyncSize(node)
}

/**
 * Reconciles the `values` map, every visible output's `.type`/`.label`,
 * and its links' colour, with the CURRENT wiring -- one pass, called after
 * `applyEnabledStateToWiring`/`applyVisibleRowCount` have already settled
 * the graph and `node.outputs`' shape. For each visible slot (module
 * docstring's per-slot paragraph):
 *   - `resolveAdoptedType(collectSlotLinkTypes(node, entry.idx))` decides
 *     that ONE slot's type, independently of every other slot.
 *   - The map entry is KEPT (created/updated) when the slot is wired,
 *     carries content, or is explicitly disabled (`slotCarriesContent`,
 *     which folds `enabled === false` into "content" -- module docstring),
 *     else DROPPED entirely -- `name`/`value`/`enabled`/`links` are
 *     preserved from whatever was already stored (never invented here),
 *     only `type` is ever written by this function.
 *   - `output.type`/`output.label` are updated to match (never
 *     `output.name` -- the RETURN_NAMES/map-key contract, distributor.js's
 *     identical rule), and every link off that slot is recoloured to the
 *     adopted type (Reroute's own precedent, distributor.js's mechanism 4).
 *   - A MIXED slot (two links disagreeing on type) never disconnects
 *     anything -- keeps the first candidate's type (already what
 *     `resolveAdoptedType` returns) and warns once per slot per distinct
 *     mismatch, keyed on the node so a fresh mismatch still gets its own
 *     message.
 * Wrapped by every caller in try/catch; this function itself assumes
 * `state.widget` exists (callers all check first).
 */
function reconcileNode(state) {
  const node = state.node
  const widget = state.widget
  if (!widget) return
  const map = parseValues(widget.value)
  const nextMap = {}
  let sideEffectsChanged = false
  const mixedWarnings = node.__epsNcMixedWarnings || (node.__epsNcMixedWarnings = {})

  for (const entry of outputEntries(node)) {
    const key = entry.name
    const candidates = collectSlotLinkTypes(node, entry.idx)
    const { type, mixed } = resolveAdoptedType(candidates)
    const jsonType = jsonTypeFor(type)

    const existing = map[key]
    const name = typeof existing?.name === 'string' ? existing.name : ''
    const rawValue = existing?.value
    const value = typeof rawValue === 'number' && Number.isFinite(rawValue) ? rawValue : 0
    const enabled = isRowEnabled(existing)
    const rememberedLinks = normalizeRememberedLinks(existing?.links)

    if (entry.wired || slotCarriesContent({ name, value, enabled })) {
      const nextEntry = { name, value, type: jsonType }
      // Sparse on purpose (module docstring): omit both unless they carry
      // real information, so an ordinary never-disabled row's JSON stays
      // exactly the shape it was before this feature existed.
      if (!enabled) nextEntry.enabled = false
      if (rememberedLinks.length > 0) nextEntry.links = rememberedLinks
      nextMap[key] = nextEntry
    }

    if (entry.output.type !== type) {
      entry.output.type = type
      sideEffectsChanged = true
    }
    const label = name.trim() ? labelForSlot(name, entry.n) : ''
    if ((entry.output.label || '') !== label) {
      if (label) entry.output.label = label
      else delete entry.output.label
      sideEffectsChanged = true
    }
    const links = entry.output.links
    if (Array.isArray(links)) {
      for (const linkId of links) {
        const link = linkById(node.graph, linkId)
        if (!link) continue
        const color = linkColorFor(type)
        if (link.color !== color) {
          link.color = color
          sideEffectsChanged = true
        }
      }
    }

    if (mixed) {
      const distinct = [...new Set(candidates.filter((c) => !isGenericSlotType(c)).map(String))]
      const signature = distinct.join(',')
      if (mixedWarnings[key] !== signature) {
        mixedWarnings[key] = signature
        console.warn(
          PREFIX,
          `${key} has mismatched wired types (${distinct.join(', ')}); keeping ${type}.`
        )
      }
    } else if (mixedWarnings[key]) {
      delete mixedWarnings[key]
    }
  }

  const nextJson = serializeValues(nextMap)
  if (widget.value !== nextJson) {
    widget.value = nextJson
    widget.callback?.(nextJson)
    sideEffectsChanged = true
  }
  if (sideEffectsChanged) node.graph?.setDirtyCanvas(true, true)
}

/** Writes an arbitrary *patch* (`{name}`/`{value}` from user typing,
 * `{enabled, links}` from the checkbox -- module docstring) into slot *n*'s
 * map entry, preserving whatever else was already stored -- the SOLE
 * mutation point for a direct user action (the "durable store" law: every
 * write happens from an explicit action, never as a repaint side effect).
 * `type` is never patched here; `reconcileNode` (called right after, by
 * every caller) is the only place that ever decides it. */
function writeSlotField(node, n, patch) {
  const widget = findWidget(node, VALUES_WIDGET_NAME)
  if (!widget) return
  const map = parseValues(widget.value)
  const key = slotName(n)
  const existing = map[key] || { name: '', value: 0, type: WILDCARD }
  map[key] = { ...existing, ...patch }
  const json = serializeValues(map)
  if (widget.value === json) return
  widget.value = json
  widget.callback?.(json)
  node.graph?.setDirtyCanvas(true, true)
}

/**
 * The checkbox's own handler (owner ask, module docstring's checkbox
 * paragraph). Unchecking remembers every current target and detaches them
 * (the target's own widget value was never touched by the link, so it is
 * simply what runs again -- rig-verified); checking replays whatever was
 * last remembered, ONE attempt, then forgets the memory regardless of
 * outcome (`applyEnabledStateToWiring`'s identical reasoning: keeping it
 * around would silently reconnect a row the user later unplugs by hand,
 * unrelated to the checkbox, on the very next sync pass). Both directions
 * go through `writeSlotField` for the JSON half and the graph-mutation
 * helpers above for the wiring half, then `syncNode` settles everything
 * else (count/types/labels/repaint) from the result -- this function does
 * not repaint anything itself.
 */
function setRowEnabled(state, n, checked) {
  const node = state.node
  const entry = outputEntries(node).find((e) => e.n === n)
  if (!entry) return
  if (checked) {
    const map = state.widget ? parseValues(state.widget.value) : {}
    const remembered = normalizeRememberedLinks(map[slotName(n)]?.links)
    reconnectRememberedTargets(node, entry.idx, remembered)
    writeSlotField(node, n, { enabled: true, links: undefined })
  } else {
    const remembered = collectOutputTargets(node, entry.idx)
    writeSlotField(node, n, { enabled: false, links: remembered })
    disconnectAllTargets(node, entry.idx)
  }
  syncNode(state)
}

/** The one orchestrator every call site uses: reconcile the graph's
 * wiring against each row's stored enabled state, settle the output
 * COUNT, reconcile TYPES/labels/the map, then repaint the DOM from
 * whatever the widget now holds. Idempotent and safe to call redundantly
 * from any hook (attach, onConfigure, the deferred connect pass, every row
 * field's input/blur handler, the checkbox handler, an external write). */
function syncNode(state) {
  try {
    applyEnabledStateToWiring(state)
    applyVisibleRowCount(state)
    reconcileNode(state)
    renderRows(state)
  } catch (error) {
    console.warn(PREFIX, 'syncNode failed', error)
  }
}

// ---------------------------------------------------------------------------
// DOM panel
// ---------------------------------------------------------------------------

const STYLE_TAG_ID = 'eps-number-controller-styles'
let stylesInjected = false

const CSS_TEXT = `
.epsnc-root { display: flex; flex-direction: column; width: 100%; height: 100%; box-sizing: border-box; overflow: hidden; background: var(--comfy-input-bg, #1e1e1e); border: 1px solid var(--border-color, #444); border-radius: 4px; font-family: inherit; font-size: 11px; color: var(--input-text, #ccc); }
.epsnc-hint { flex: 0 0 auto; padding: 4px 7px 2px; color: var(--descrip-text, #999); font-size: 10px; }
.epsnc-list { flex: 1 1 auto; min-height: 0; overflow-y: auto; overflow-x: hidden; padding: 2px 5px 5px; }
.epsnc-row { display: flex; align-items: center; gap: 5px; padding: 2px 0; opacity: 1; transition: opacity 0.1s ease; }
.epsnc-row-disabled { opacity: 0.45; }
.epsnc-row-enabled { flex: 0 0 auto; margin: 0; cursor: pointer; }
.epsnc-row-index { flex: 0 0 auto; width: 16px; text-align: right; color: var(--descrip-text, #999); font-size: 10px; }
.epsnc-row-name { flex: 1 1 auto; min-width: 0; background: var(--comfy-menu-bg, #262626); border: 1px solid var(--border-color, #444); color: var(--input-text, #ccc); border-radius: 3px; padding: 3px 6px; font-size: 11px; }
.epsnc-row-value { flex: 0 0 auto; width: 64px; text-align: right; background: var(--comfy-menu-bg, #262626); border: 1px solid var(--border-color, #444); color: var(--input-text, #ccc); border-radius: 3px; padding: 3px 6px; font-size: 11px; }
.epsnc-row-type { flex: 0 0 auto; width: 36px; text-align: center; font-size: 9.5px; font-weight: 600; letter-spacing: 0.03em; border-radius: 3px; padding: 3px 0; }
.epsnc-row-type-any { color: var(--descrip-text, #999); border: 1px solid var(--border-color, #444); }
.epsnc-row-type-set { color: #a8dd93; border: 1px solid #4f9a44; background: rgba(79, 154, 68, 0.15); }
`

function injectStyles() {
  if (stylesInjected) return
  stylesInjected = true
  if (typeof document === 'undefined' || document.getElementById(STYLE_TAG_ID)) return
  const style = document.createElement('style')
  style.id = STYLE_TAG_ID
  style.textContent = CSS_TEXT
  document.head.appendChild(style)
}

/** Small DOM-builder, distributor.js's/checkpoint_switcher.js's identical
 * `el()` helper -- each module keeps its own copy. */
function el(tag, options = {}, children = []) {
  const node = document.createElement(tag)
  if (options.className) node.className = options.className
  if (options.text !== undefined) node.textContent = options.text
  if (options.attrs) {
    for (const [key, value] of Object.entries(options.attrs)) node.setAttribute(key, value)
  }
  for (const child of children) {
    if (child == null) continue
    node.append(child instanceof Node ? child : document.createTextNode(String(child)))
  }
  return node
}

function tooltipForType(jsonType) {
  if (jsonType === 'INT') return "Wired as a whole number (INT) -- matches what it's plugged into."
  if (jsonType === 'FLOAT') return "Wired as a decimal (FLOAT) -- matches what it's plugged into."
  return 'Not wired yet. Connect this output to fix it as a whole number or a decimal.'
}

function panelHeightFor(state) {
  const rows = state.rows ? state.rows.length : MIN_SLOTS
  return HINT_HEIGHT + rows * ROW_HEIGHT + PANEL_PADDING
}

/**
 * Builds one row's DOM: an enable checkbox, an index label, a name text
 * input, a value text input, and a read-only type badge.
 * `type="text"` (not `type="number"`) on purpose -- a native number
 * input's scroll-wheel-over-focused-field behavior IS a scrub control, and
 * the owner's brief is explicit: no step/scrub, "just a plain number box".
 * `inputmode="decimal"` still gets a numeric-friendly on-screen keyboard
 * on touch devices without any of that baggage.
 *
 * Both text inputs commit on every `input` event (module docstring's
 * "durable store" law -- a tab switch mid-keystroke should lose at most
 * the ONE incomplete keystroke, never a previously-committed value): the
 * name field always commits (any string, including empty, is a valid
 * name); the value field commits only when `parseNumericInput` returns a
 * complete number, so a mid-typing state like `"-"` or `"3."` never
 * overwrites the last valid number. `blur` re-syncs the whole panel
 * (`syncNode`) once the field is no longer being actively edited -- the
 * point at which an abandoned invalid edit should revert its DISPLAY to
 * the last committed value, and at which a just-cleared row is safe to let
 * shrink the panel (the focus floor no longer protects it).
 *
 * The checkbox is not a text field -- no mid-edit/focus concern applies to
 * it (`renderRows` always sets `.checked` unconditionally) -- its `change`
 * handler goes straight to `setRowEnabled`, the owner's checkbox feature
 * (module docstring).
 */
function buildRowEl(state, n) {
  const enabledInput = el('input', {
    className: 'epsnc-row-enabled',
    attrs: {
      type: 'checkbox',
      title:
        "On: this number overrides whatever it's wired to. Off: disconnected -- " +
        'the thing it was plugged into goes back to using its own value.'
    }
  })
  const indexEl = el('span', { className: 'epsnc-row-index', text: String(n) })
  const nameInput = el('input', {
    className: 'epsnc-row-name',
    attrs: {
      type: 'text',
      placeholder: slotName(n),
      title:
        "What this number is for (optional) -- shown as the wire's label. " +
        `Leave blank to keep ${slotName(n)}.`
    }
  })
  const valueInput = el('input', {
    className: 'epsnc-row-value',
    attrs: {
      type: 'text',
      inputmode: 'decimal',
      spellcheck: 'false',
      title: 'The number this output sends. Type a whole number or a decimal.'
    }
  })
  const typeEl = el('span', { className: 'epsnc-row-type' })

  enabledInput.addEventListener('change', () => {
    setRowEnabled(state, n, enabledInput.checked)
  })
  nameInput.addEventListener('input', () => {
    writeSlotField(state.node, n, { name: nameInput.value })
    syncNode(state)
  })
  valueInput.addEventListener('input', () => {
    const parsed = parseNumericInput(valueInput.value)
    if (parsed === null) return // incomplete number -- wait for more keystrokes
    writeSlotField(state.node, n, { value: parsed })
    syncNode(state)
  })
  for (const input of [nameInput, valueInput]) {
    // Canvas hotkeys (Delete/Ctrl+C/etc.) must not intercept typing here --
    // checkpoint_switcher.js's filter input / notebook.js's textarea do the
    // same.
    input.addEventListener('keydown', (event) => {
      event.stopPropagation()
      if (event.key === 'Enter') input.blur()
    })
    input.addEventListener('blur', () => syncNode(state))
  }

  const rowEl = el('div', { className: 'epsnc-row', attrs: { 'data-slot': String(n) } }, [
    enabledInput,
    indexEl,
    nameInput,
    valueInput,
    typeEl
  ])
  return { n, el: rowEl, enabledInput, nameInput, valueInput, typeEl }
}

/** Adds/removes row ELEMENTS (never their content) to make `state.rows`
 * exactly *count* long -- a structural diff, not a full teardown, so an
 * unrelated row-count change never destroys a DIFFERENT row's focused
 * input (distinct from checkpoint_switcher.js's `renderList`, which can
 * safely `replaceChildren()` every pass because checkboxes have no
 * mid-edit state to lose; this node's rows do). */
function ensureRowElements(state, count) {
  while (state.rows.length > count) {
    const row = state.rows.pop()
    row.el.remove()
  }
  while (state.rows.length < count) {
    const n = state.rows.length + 1
    const row = buildRowEl(state, n)
    state.rows.push(row)
    state.listEl.append(row.el)
  }
}

/**
 * Single entry point that repaints the whole panel FROM `state.widget`'s
 * current value -- the re-render law's core function (module docstring).
 * Structural (row count) and content (name/value/enabled/type per row) all
 * re-derive from the widget every call; the ONLY thing this function never
 * overwrites is a name/value input that is `document.activeElement` right
 * now (module docstring's per-field guard) -- the checkbox has no mid-edit
 * concept and is always set unconditionally. Never mutates the widget,
 * never touches `node.outputs` or the graph's wiring -- purely a DOM sync
 * (`applyEnabledStateToWiring`/`applyVisibleRowCount`/`reconcileNode` own
 * those).
 */
function renderRows(state) {
  const widget = state.widget
  const map = widget ? parseValues(widget.value) : {}
  // desiredRowCount(), not a fresh local computeVisibleRowCount() call --
  // see that function's own docstring for the bug this fixes (a focused
  // row's DOM getting rebuilt smaller than the socket count
  // applyVisibleRowCount had just correctly kept).
  ensureRowElements(state, desiredRowCount(state))

  const activeElement = typeof document !== 'undefined' ? document.activeElement : null

  for (const row of state.rows) {
    const key = slotName(row.n)
    const entry = map[key]
    const name = typeof entry?.name === 'string' ? entry.name : ''
    const rawValue =
      typeof entry?.value === 'number' && Number.isFinite(entry.value) ? entry.value : 0
    const jsonType = entry?.type === 'INT' || entry?.type === 'FLOAT' ? entry.type : WILDCARD
    const shownValue = displayValueFor(rawValue, jsonType)
    const enabled = isRowEnabled(entry)

    if (activeElement !== row.nameInput) row.nameInput.value = name
    if (activeElement !== row.valueInput) row.valueInput.value = String(shownValue)
    row.enabledInput.checked = enabled

    row.typeEl.textContent = typeBadgeFor(jsonType)
    row.typeEl.title = tooltipForType(jsonType)
    row.typeEl.className =
      jsonType === WILDCARD ? 'epsnc-row-type epsnc-row-type-any' : 'epsnc-row-type epsnc-row-type-set'

    // Dimmed row = "off at a glance" (owner ask 6) without needing the
    // checkbox's own state consulted first.
    row.el.className = enabled ? 'epsnc-row' : 'epsnc-row epsnc-row-disabled'
  }

  resyncSize(state.node)
}

function buildUi(state) {
  injectStyles()
  const hint = el('div', {
    className: 'epsnc-hint',
    text: 'One row per number. Name it if you like -- wire an output to fix it as a whole number or a decimal.'
  })
  state.listEl = el('div', { className: 'epsnc-list' })
  state.root = el('div', { className: 'epsnc-root' }, [hint, state.listEl])
  attachDomWidget(state)
  renderRows(state) // initial paint -- whatever the widget already holds at attach time
}

/**
 * Wraps `node.addDOMWidget`. Both `getMinHeight`/`getMaxHeight` return the
 * SAME value (resolution.js's "exact height" shape for its size grid) --
 * this panel is precisely as tall as its current row count, no free
 * resizing to get stuck in.
 */
function attachDomWidget(state) {
  installMinWidth(state.node, MIN_NODE_WIDTH)
  const domWidget = state.node.addDOMWidget(PANEL_WIDGET_NAME, PANEL_WIDGET_TYPE, state.root, {
    hideOnZoom: true,
    serialize: false, // excludes from the API prompt (utils/executionUtil.ts)
    getMinHeight: () => panelHeightFor(state),
    getMaxHeight: () => panelHeightFor(state)
  })
  // Excludes from the workflow JSON -- a DIFFERENT flag from options.serialize
  // above (notebook.js's attachDomWidget() header explains why both exist).
  // Nothing new serializes here regardless: this panel's whole state lives
  // in the `values` widget, which serializes itself normally.
  domWidget.serialize = false
  domWidget.serializeValue = () => undefined
  return domWidget
}

// ---------------------------------------------------------------------------
// Litegraph hooks
// ---------------------------------------------------------------------------

/**
 * Flips a row's stored `enabled` back to `true` the instant a NEW live
 * link lands on it while the JSON still says it is off -- owner ask 5,
 * "Wiring a new link into a disabled row re-enables it", unambiguous user
 * intent the moment it happens.
 *
 * Deliberately called ONLY from `wireRowSync`'s connection-change-triggered
 * deferred pass, never from the general `syncNode`/`applyEnabledStateToWiring`
 * path (module docstring's checkbox paragraph explains why the two must
 * stay separate): from data alone, "stored off, but live-wired" is
 * IDENTICAL in two situations --
 *   (a) the user just dragged a brand-new wire onto this exact row (should
 *       flip the row back on, keeping the wire), and
 *   (b) a Universal State Controller Apply set `enabled: false` in the
 *       JSON while the graph itself hasn't caught up yet (should DETACH
 *       the stale wire to honor the newly-applied off state).
 * Only the CALLER knows which one just happened -- (a) is reachable only
 * through a real `onConnectionsChange` firing on this node's own output,
 * (b) only through an external widget write with no connection event at
 * all -- so running this here, strictly BEFORE `applyEnabledStateToWiring`
 * gets a chance to run on the SAME pass, resolves the ambiguity by
 * construction: by the time the general reconciliation looks, case (a)'s
 * row already reads `enabled: true` and matches its (now correctly kept)
 * wiring, so there is nothing left to "fix".
 */
function autoReenableNewlyWiredRows(state) {
  const node = state.node
  const widget = state.widget
  if (!widget) return
  const map = parseValues(widget.value)
  let changed = false
  for (const entry of outputEntries(node)) {
    const stored = map[entry.name]
    if (entry.wired && stored?.enabled === false) {
      // Clears any remembered `links` too, not just the flag -- the wire
      // the user just made supersedes whatever an EARLIER disable had
      // remembered; keeping the old memory around could otherwise
      // reconnect to a stale target the next time this row is unplugged
      // by hand (applyEnabledStateToWiring's identical "one attempt, then
      // forget" reasoning).
      map[entry.name] = { ...stored, enabled: true, links: undefined }
      changed = true
    }
  }
  if (!changed) return
  const json = serializeValues(map)
  if (widget.value === json) return
  widget.value = json
  widget.callback?.(json)
  node.graph?.setDirtyCanvas(true, true)
}

/**
 * Chains *node*'s `configure` and `onConnectionsChange` so wiring/unwiring
 * any `num_N` output re-syncs this node -- distributor.js's
 * `wireOutputGrowth`, structurally ported (both litegraph findings that
 * function's own docstring cites apply unchanged: growing/shrinking
 * `node.outputs` from inside `configure()`'s own restore-loop iteration
 * would splice under a live iterator, and the live path must defer past
 * the current call frame since `disconnectOutput` dispatches
 * `onConnectionsChange` before it returns).
 *
 * `state.restoring` blanks scheduling for exactly the duration of THIS
 * node's `configure()` call; nothing is lost, because `attach()`'s
 * `onConfigure` wrap calls `syncNode(state)` directly (not through this
 * deferred path) once `configure()` has fully settled `node.outputs` AND
 * restored `widget.value` -- the one place in this file that is allowed to
 * assume both are stable.
 *
 * The deferred pass runs `autoReenableNewlyWiredRows` FIRST -- see that
 * function's own docstring for why a live connection change is the one
 * place it is safe to run, and why it must run before anything else on
 * this pass -- then always re-derives TYPES (`reconcileNode`, unconditional
 * -- a type can change on a MIDDLE slot without moving the total count) and
 * always repaints the DOM, but only calls the count-changing
 * `applyVisibleRowCount` when the derived count actually differs from what
 * is already on screen -- an unconditional pass would otherwise re-run
 * `resyncSize` (and so fight a manual node resize) on every single connect
 * and disconnect, distributor.js's identical `wireOutputGrowth` lesson.
 * Deliberately does NOT call `applyEnabledStateToWiring` (unlike the
 * general `syncNode`) -- this pass IS the live connection event; running
 * the external-write reconciler here too would risk exactly the ambiguity
 * `autoReenableNewlyWiredRows`'s docstring describes, and it isn't needed:
 * the auto-reenable step above already resolves the one case this pass
 * actually needs to handle.
 */
function wireRowSync(state) {
  const node = state.node
  const hook = { restoring: false, scheduled: false }

  function runDeferred(target) {
    hook.scheduled = false
    // A configure() that started while this was pending runs its own pass
    // in onConfigure; a node removed from the graph meanwhile needs none.
    if (hook.restoring || !target.graph) return
    try {
      autoReenableNewlyWiredRows(state)
      reconcileNode(state)
      if (desiredRowCount(state) !== outputEntries(target).length) applyVisibleRowCount(state)
      renderRows(state)
    } catch (error) {
      console.warn(PREFIX, 'deferred sync failed', error)
    }
  }

  function schedule(target) {
    if (hook.scheduled) return
    hook.scheduled = true
    setTimeout(() => runDeferred(target), 0)
  }

  const originalConfigure = node.configure
  node.configure = function (...args) {
    hook.restoring = true
    try {
      return originalConfigure?.apply(this, args)
    } finally {
      hook.restoring = false
    }
  }

  const originalOnConnectionsChange = node.onConnectionsChange
  node.onConnectionsChange = function (type, index, isConnected, linkInfo, slot) {
    let result
    if (typeof originalOnConnectionsChange === 'function') {
      result = originalOnConnectionsChange.apply(this, arguments)
    }
    // Name-matched (`num_N` is only ever an output on this node) rather
    // than slot-type-matched -- distributor.js's identical reasoning. This
    // deliberately never branches on `isConnected`: litegraph's restore
    // loop passes it hardcoded `true` even for a slot with no link, and
    // `highestWiredSlot`/`slotCarriesContent` both recompute wiring/content
    // from the slots and the map themselves, so a stale argument can never
    // misdrive this.
    if (!hook.restoring && OUTPUT_NAME_RE.test(slot?.name || '')) schedule(this)
    return result
  }
}

/**
 * Installs the frontend-only `ALLOWED_TYPES` allowlist as litegraph's own
 * connection-veto hook -- module docstring's rig-finding paragraph:
 * without this, every `num_N` output (declared `*`) connects successfully
 * to ANY target type. `LGraphNode.ts`'s `connectSlots` calls
 * `isValidConnection` first (already permissive for a wildcard slot), and
 * only if that passes calls `sourceNode.onConnectOutput?.(...)`, aborting
 * the connection the instant it returns exactly `false` -- quiet (a toast,
 * no exception) and non-destructive (the link simply never forms).
 * Chained with whatever else installed `onConnectOutput` first.
 */
function wireTypeVeto(node) {
  const original = node.onConnectOutput
  node.onConnectOutput = function (outputIndex, inputType, input, targetNode, inputIndex) {
    const output = this.outputs?.[outputIndex]
    if (output && OUTPUT_NAME_RE.test(output.name || '') && !isAllowedType(inputType)) {
      toast(this, 'warn', `EPS Number Controller only carries whole numbers or decimals -- not ${inputType}.`)
      return false
    }
    if (typeof original === 'function') return original.apply(this, arguments)
    return undefined
  }
}

// ---------------------------------------------------------------------------
// Public entry points (called from web/eps_image.js)
// ---------------------------------------------------------------------------

/** Frontend-only one-time setup. EPSNumberController is a real backend node
 * (no frontend-only type registration needed) -- everything here is
 * per-instance, done in attach(). Kept as an export because eps_image.js
 * calls it unconditionally, matching every sibling module's identical
 * no-op init(). */
export function init() {}

/**
 * Per-node-instance attach; no-op unless *node* is an EPSNumberController.
 * Never throws (FORMAT.md section 7/8) -- every failure is logged via
 * `console.warn` and leaves the node's plain (visible, unstyled) `values`
 * widget fully functional on its own.
 */
export function attach(node) {
  try {
    if (!node) return
    if (nodeClassOf(node) !== CLASS_ID) return
    if (attachedNodes.has(node)) return
    if (typeof node.addDOMWidget !== 'function') {
      console.warn(PREFIX, 'this ComfyUI frontend has no addDOMWidget; number controller panel not attached')
      return
    }
    const widget = findWidget(node, VALUES_WIDGET_NAME)
    if (!widget) {
      console.warn(
        PREFIX,
        'EPSNumberController node is missing its `values` widget; panel not attached'
      )
      return
    }
    attachedNodes.add(node)

    hideValuesWidget(node, widget)

    const state = { node, widget, rows: [], root: null, listEl: null }
    buildUi(state)
    wireRowSync(state)
    wireTypeVeto(node)

    // Universal State Controller Apply fix (module docstring): publish this
    // node's reload seam and make sure the one shared subscription to the
    // announce event is installed.
    node.__epsNcReload = () => syncNode(state)
    installExternalWriteSubscription()

    // The re-render law's other race leg (module docstring): `onConfigure`
    // is the one hook that fires AFTER `configure()` has restored
    // `widget.value` for both a whole-workflow load and a paste
    // (distributor.js's/checkpoint_switcher.js's identical citation of this
    // ordering). Chained, never replaced.
    const originalOnConfigure = node.onConfigure
    node.onConfigure = function (info) {
      const result = originalOnConfigure?.apply(this, arguments)
      try {
        syncNode(state)
      } catch (error) {
        console.warn(PREFIX, 'post-configure sync failed', error)
      }
      return result
    }

    // Settle once now: covers a FRESH node (nothing to restore -- collapses
    // to one blank row) and the other race order, a value already sitting
    // on the widget by attach time (module docstring). A genuine reload's
    // real value still arrives correctly via the onConfigure wrap above,
    // strictly after this.
    syncNode(state)
  } catch (error) {
    console.warn(PREFIX, 'attach failed', error)
  }
}
