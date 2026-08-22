/**
 * @file EPS Apply LoRA Set frontend behavior (FORMAT.md §7.4): keeps every
 * `LoraLibraryApplySet` node's `set` combo fresh without a page reload.
 *
 * Mechanism: each ApplySet node's combo gets `options.values` swapped for a
 * FUNCTION returning a module-level cache — the same (deprecated-but-
 * supported) dynamic-combo pattern controller.js uses, chosen deliberately
 * so both files ride the same litegraph code path and age together. The
 * values function also kicks a THROTTLED async refetch, so the flow is:
 * open the dropdown → see the cache (usually current) → cache refreshes in
 * the background → the next open is exact. No cross-module coupling with
 * controller.js: its CRUD lands in the same backend the refetch reads.
 *
 * Server-side `VALIDATE_INPUTS` already returns True for unseen values
 * (FORMAT.md §6.2), so a *stale* combo can still queue a just-created set;
 * this module only closes the UX gap, not a correctness one.
 *
 * 2026-07-19c addition: FORMAT.md §6.2's `mirrors loader` tag (owner:
 * "set different EPS Apply LoRA Set nodes to different Power Lora Loaders as
 * targets") — `attachMirrorsWidget()`. A second frontend-only combo per
 * Apply node, appended AFTER the server widgets (`set`, `strength_scale`)
 * on every `nodeCreated`. It is a pure GROUPING TAG read by controller.js's
 * selective Push State (`selectPushTargets()`/`mirrorsTagMatches()` there)
 * — it never changes what this node executes, and the server never sees it
 * (not a Python-declared widget, so it never appears in `INPUT_TYPES` or
 * the API prompt).
 *
 * Positional-restore safety ("append LAST", FORMAT.md §6.2): litegraph
 * saves `widgets_values[i]` at each widget's own array INDEX and restores
 * by walking a plain counter over `node.widgets` in order (verified once,
 * in detail, against this exact rig's frontend bundle — see controller.js's
 * file header's "save/restore ordering hazard" citation for the concrete
 * lines; not re-derived here to avoid duplicating that citation across two
 * files that already don't import each other). The practical consequence
 * for THIS widget: as long as it's appended after every widget ComfyUI's
 * own Python `INPUT_TYPES` already created for the node — true on every
 * `nodeCreated` call, fresh-add or workflow-load alike, since the server
 * widgets are already in `node.widgets` by the time this extension's
 * `nodeCreated` hook runs (the SAME assumption `attachApplySetBehavior()`
 * below already relies on to find the existing `set` widget by name) — its
 * position stays the LAST slot in both `node.widgets` and `widgets_values`
 * on every save/restore, so a plain by-index restore never misaligns it
 * against the server widgets.
 *
 * Self-healing staleness ("tolerates the id disappearing", FORMAT.md §6.2):
 * if the tagged PLL id no longer resolves to a live node, the widget resets
 * to "(any)". Until v0.68.0 that check lived INSIDE the widget's
 * `options.values` function, which litegraph evaluated on EVERY canvas draw
 * (`ComboWidget._displayValue` calls `values()` whenever no
 * `options.getOptionLabel` is installed -- read from the installed bundle)
 * -- so every Apply node walked the whole workflow per frame, and wrote a
 * widget value from inside a getter. v0.68.1: `values()` is PURE and only
 * runs on dropdown open (both renderers; the Vue select re-evaluates
 * `options.values()` in its open handler), an identity `getOptionLabel`
 * keeps the per-draw display off the graph walk, and the heal moved to
 * `healMirrorsTags()`, driven by a chained, install-once `onNodeRemoved`
 * watch on every graph (`installMirrorsGraphWatch()`, controller.js's
 * `installGraphNodeWatch` idiom by hand) plus one deferred pass after each
 * `nodeCreated` (a workflow load restores the tag value AFTER this hook).
 *
 * 2026-07-20 addition: FORMAT.md §6.2's `strength_scale` hide-by-default
 * (owner: "this should be turned off by default ... by default the
 * strength should pass through what is set in the loader ... it's an edge
 * case") — `applyStrengthScaleVisibility()`. Two mechanisms combine, each
 * borrowed from an existing file rather than invented fresh:
 *  - The HIDE ITSELF mirrors controller.js's `Show status` handling
 *    exactly: `widget.hidden = true` is a real litegraph layout primitive
 *    for widget INPUT slots (not a value-blanking trick) — controller.js's
 *    file header cites `LGraphNode.isWidgetVisible()`/`getLayoutWidgets()`
 *    branching on `.hidden`, and `computeSize()`/`_arrangeWidgets()`
 *    building exclusively off that filtered list, so a hidden widget is
 *    removed from drawing AND layout AND size — later widgets shift up, the
 *    node visibly shrinks. `drawNode()` calls `node.arrange()`
 *    unconditionally every frame, so `setDirtyCanvas(true, true)` is the
 *    only extra step needed, exactly as controller.js's own
 *    `onPropertyChanged` for `Show status` does it (no manual
 *    computeSize/setSize bookkeeping). The widget's VALUE is untouched
 *    either way — hiding is purely cosmetic, so a hidden `strength_scale`
 *    still serializes and still feeds `apply()` at whatever it's set to
 *    (default 1.0, i.e. pass-through).
 *  - The ATTACH-TIME WIRING (property + wrapped `onPropertyChanged` + one
 *    explicit initial apply call) mirrors `eps_image/resolution.js`'s
 *    `attach()` instead of controller.js: controller.js owns its ENTIRE
 *    node class and can define `onPropertyChanged` as a genuine class
 *    method, but this file — like resolution.js — only gets a per-instance
 *    `nodeCreated` hook on a node class it does NOT own (the real,
 *    Python-declared `LoraLibraryApplySet`), so it must wrap whatever
 *    `node.onPropertyChanged` already is (there is none today, but wrapping
 *    is still the defensive, no-cross-module-assumption move) rather than
 *    assign a class method. Same reasoning resolution.js's file header
 *    documents in detail ("Defaults flipped to OFF"): `addProperty()` is a
 *    silent `this.properties[name] = default_value` — it never fires
 *    `onPropertyChanged` and never touches the widget — so a fresh node
 *    needs one explicit `applyStrengthScaleVisibility()` call right after
 *    wiring. A RELOADED node gets that same explicit call too (harmless —
 *    idempotent), because `nodeCreated` always runs BEFORE
 *    `LGraphNode.configure()` for a saved workflow; `configure()`'s own
 *    properties-merge loop runs immediately after and calls the wrapped
 *    `onPropertyChanged` for whatever the saved file actually has, so the
 *    saved value always wins last regardless of call order.
 *
 * 2026-07-20 (§4.1 composite fix) addition: FORMAT.md §6.2/§4.1's
 * `loader_slot` hide-by-default — `applyLoaderSlotVisibility()`. Exactly
 * mirrors `strength_scale`/`applyStrengthScaleVisibility()` immediately
 * above: same `.hidden` mechanism, same attach-time wiring (property +
 * shared wrapped `onPropertyChanged` + one explicit initial apply call), a
 * separate property (`Show loader slot`) so the two widgets can be revealed
 * independently. A fresh Apply node therefore shows NEITHER `strength_scale`
 * NOR `loader_slot`; revealing `Show loader slot` in the node's right-click
 * Properties shows only that one widget.
 *
 * Pinned state (FORMAT.md §6.2, provenance M3 — owner 2026-08-18/21: the
 * captured OLD values must be visible, with a one-click clear). The backend
 * declares a TAIL STRING widget `pinned_state` (hidden in both renderers:
 * `options.hidden` from INPUT_TYPES + the canvas `widget.hidden` set here —
 * `hidePinnedStateWidget()`). `""` = live; otherwise JSON `{format, slug,
 * name, set: {<full §4 set dict>}, source: {token, captured}}` written by
 * EPS Save Image into a baked per-image workflow; while pinned the backend
 * applies the pinned set and ignores the `set` dropdown. NOTHING here
 * creates a pin — it arrives through `configure()` (a dropped image / a
 * saved pinned workflow) and the chained `onConfigure` reconcile
 * (`wirePinConfigureSync` → `syncPinFromWidget`) paints it without a click.
 * RENDERING CHOICE: a compact display-only `addDOMWidget` row
 * (`attachPinBadge()`), cross_sweep.js's readout idiom verbatim — it is the
 * only affordance this file can add that works under BOTH renderers (Vue
 * nodes paint no canvas controls, §7.5) without a canvas draw hook, and it
 * sizes itself by the §7.2 DOM-widget laws (`computeSize` + `computedHeight`
 * + an explicit element height; every reported height = text + 2·margin
 * because the overlay's visible box is computedHeight − 2·margin). It is
 * appended LAST, after `mirrors loader` — litegraph's save leaves a HOLE at a
 * `serialize:false` widget's index while restore walks a counter that SKIPS
 * such widgets (controller.js's save/restore ordering citation), so a non-
 * serialized widget must follow every serialized one — and hidden (both
 * flags + zero height) until a pin arrives. Pinned, it shows "📌 Pinned
 * state: <name> — captured from image <token> — <verdict>", a one-line
 * summary of the pinned rows (`stem strength · stem strength`, or per-loader
 * `L0 … / L1 …` for a §4.1 format-2 state) and an **Unpin** button; the
 * verdict comes from ONE `GET /lora_library/set?slug=` (`fetchPinDrift`,
 * token-guarded): "matches current state" / "differs from current state" /
 * "state no longer exists" (404) / "current state unavailable" (other
 * failure) / "checking current state…" while in flight. The `set` combo
 * stays visible but is marked inactive while pinned (`markSetComboInactive`:
 * label "set (ignored while pinned)", disabled, tooltip "ignored while
 * pinned") and restored on unpin. Unpin writes `""` through the widget's
 * value + callback, toasts "Unpinned — back to the live state", and the
 * callback chain repaints. No window listeners, no canvas drawing. The pure
 * halves (`parsePinnedState`, `pinnedRowsSummary`, `comparePinnedSet`,
 * `pinnedStateBadgeText`) are exported for tests/test_m3_pinning_js.py.
 * One more M3 consequence, healed here (`migrateLegacyMirrorsValue` pure +
 * `healLegacyWidgetShift` on configure): the new Python widget lands BEFORE
 * the frontend-appended `mirrors loader` in `node.widgets` (which MUST stay
 * last), so a pre-M3 workflow's positional `widgets_values` put its mirrors
 * value ("(any)", "12", "12:3", a "<title> #<id>" label) INTO `pinned_state`
 * and left the tag at its default; on configure, a non-empty `pinned_state`
 * that is not pin JSON, while the mirrors combo is still at its default, is
 * moved into the combo (value + callback) and `pinned_state` is blanked --
 * a one-time silent migration, logged once per node to console.info.
 */

import { app } from '../../../scripts/app.js'
import * as api from './api.js'

const NODE_CLASS = 'LoraLibraryApplySet'
const WIDGET_NAME = 'set'

/** Exact rgthree type string — same literal as controller.js's `POWER_LORA_LOADER_TYPE` (constants.js `addRgthree("Power Lora Loader")`); duplicated by hand rather than imported, per this file's no-cross-module-coupling design (see above). */
const POWER_LORA_LOADER_TYPE = 'Power Lora Loader (rgthree)'

/** FORMAT.md §6.2 `mirrors loader` tag — widget name + its "no PLL selected" default value. controller.js's `MIRRORS_WIDGET_NAME`/`MIRRORS_ANY_VALUE` mirror these by hand (same convention as NODE_CLASS/WIDGET_NAME above). */
const MIRRORS_WIDGET_NAME = 'mirrors loader'
const MIRRORS_ANY_VALUE = '(any)'

/** FORMAT.md §6.2 (2026-07-20): the `strength_scale` widget name (a real,
 * Python-declared widget — lora_library/nodes_sets.py's INPUT_TYPES) + the
 * node property that reveals it, default false. See file header for why
 * this hides via controller.js's `.hidden` pattern but wires up via
 * resolution.js's per-instance attach()/onPropertyChanged idiom. */
const STRENGTH_SCALE_WIDGET_NAME = 'strength_scale'
const PROP_SHOW_STRENGTH_SCALE = 'Show strength scale'

/** FORMAT.md §6.2/§4.1 (2026-07-20 composite fix): the `loader_slot` widget
 * name (a real, Python-declared widget — lora_library/nodes_sets.py's
 * INPUT_TYPES) + the node property that reveals it, default false. Exact
 * mirror of the `strength_scale`/`Show strength scale` pair above — same
 * hide mechanism (`applyLoaderSlotVisibility()` below), same attach-time
 * wiring, own property so the two widgets reveal independently. */
const LOADER_SLOT_WIDGET_NAME = 'loader_slot'
const PROP_SHOW_LOADER_SLOT = 'Show loader slot'

/** FORMAT.md §6.2 (provenance M3): the backend's TAIL STRING widget holding
 * the pinned state JSON, or "" for live (file header "Pinned state"). Looked
 * up by NAME, so a backend that hasn't shipped it leaves every pin path a
 * no-op. */
const PINNED_STATE_WIDGET_NAME = 'pinned_state'
/** The pin badge DOM widget (display-only, never serialized). */
const PIN_WIDGET_NAME = 'eps_apply_set_pin'
const PIN_WIDGET_TYPE = 'eps_apply_set_pin_badge'
/** Text height of the two-line pin row. Every height REPORTED to litegraph
 * is this + 2*margin -- the overlay's visible box is computedHeight minus
 * 2*margin (cross_sweep.js's readout lesson, owner report 2026-08-14). */
const PIN_ROW_HEIGHT = 36
/** `BaseDOMWidgetImpl.DEFAULT_MARGIN` on the installed frontend; only used
 * when the widget instance doesn't expose `margin`. */
const DOM_WIDGET_MARGIN_FALLBACK = 10
const PIN_STYLE_TAG_ID = 'eps-apply-set-pin-styles'
/** The `set` combo's painted label/tooltip while pinned (restored on unpin). */
const SET_COMBO_PINNED_LABEL = 'set (ignored while pinned)'
const SET_COMBO_PINNED_TOOLTIP = 'ignored while pinned'

/** §7.4 freshness beats thrift here, but don't hammer on every redraw. */
const REFRESH_THROTTLE_MS = 2000

/** Module-level cache shared by every ApplySet node in the graph. */
let cachedValues = ['None']
/** slug -> display NAME (owner report 2026-08-14: a pushed state showed as
 * its slug, "state-1", instead of its name, "State 1"). The combo's VALUE
 * stays the slug — that is what the API prompt executes — only the pixels
 * change, via ComboWidget's own `options.getOptionLabel` seam (it drives
 * both the on-canvas text and the dropdown rows; read from the frontend
 * source, ComboWidget.ts `_displayValue`/`onClick`). */
let cachedNames = new Map()
let lastFetchStarted = 0
/** The in-flight GET's promise, or null (v0.68.1: was a boolean). */
let fetchInFlight = null
/** v0.68.1: a FORCED refresh arrived while a fetch was in flight -- that
 * fetch may predate the CRUD it is meant to reflect, so one more runs. */
let refetchQueued = false

/** One-shot guard for `wrapComfyComboRefresh()`. */
let comboRefreshWrapped = false

async function refreshSetsCache(force = false) {
  if (fetchInFlight) {
    // v0.68.1: `if (fetchInFlight) return` used to come BEFORE `force` was
    // honored, so the CRUD event's forced refresh could no-op against a
    // throttled open-time refetch that was already in flight (and already
    // stale) -- `valuesIncluding`/`installSetValues` then kept serving the
    // old list. A forced caller now waits for that fetch and queues exactly
    // one more; an unforced caller still just leaves.
    if (!force) return
    refetchQueued = true
    return fetchInFlight
  }
  if (!force && Date.now() - lastFetchStarted < REFRESH_THROTTLE_MS) return
  do {
    refetchQueued = false
    lastFetchStarted = Date.now()
    fetchInFlight = fetchSetsOnce()
    await fetchInFlight
    fetchInFlight = null
  } while (refetchQueued)
}

/** One GET of `/lora_library/sets` into the module cache; never throws. */
async function fetchSetsOnce() {
  try {
    const data = await api.getJson('/lora_library/sets')
    const slugs = (data.sets ?? []).map((row) => row.slug)
    cachedValues = ['None', ...slugs]
    cachedNames = new Map((data.sets ?? []).map((row) => [row.slug, row.name || row.slug]))
  } catch (error) {
    api.warn('refreshing set list failed (keeping previous combo values)', error)
  }
}

/** Every `LoraLibraryApplySet` node in the whole workflow — subgraphs
 * included (v0.64.0, owner: nested nodes must work). */
function applySetNodes() {
  return api
    .walkLiveNodes(app.graph)
    .map(({ node }) => node)
    .filter((node) => (node?.comfyClass ?? node?.constructor?.comfyClass) === NODE_CLASS)
}

/**
 * Install the dynamic `values` function on one node's `set` combo.
 *
 * MUST be re-callable, because ComfyUI's own `app.refreshComboInNodes()`
 * OVERWRITES `widget.options.values` with a plain frozen ARRAY (owner report
 * 2026-08-09, root-caused live on the rig: after any such refresh — and the
 * frontend fires them for its own reasons, e.g. the model-refresh command —
 * this combo could never see a newly created set again, in EITHER renderer).
 * `initSetsFreshness()` wraps that method so this runs again after every
 * refresh; the function is therefore the durable state, not a one-shot.
 * @param {object} node
 */
function installSetValues(node) {
  const widget = (node?.widgets ?? []).find((w) => w.name === WIDGET_NAME)
  if (!widget || !widget.options) return
  widget.options.values = () => {
    // Fire-and-forget: today's open shows the cache, the refetch it kicks
    // makes the next open exact (see file header).
    refreshSetsCache()
    return valuesIncluding(widget.value)
  }
  // Display NAMES for slug values (owner report 2026-08-14) -- an unknown
  // slug (pushed before our cache caught up, or another machine's set)
  // falls back to the slug itself: exactly what will execute, never blank.
  widget.options.getOptionLabel = (value) =>
    value == null || value === 'None' ? 'None' : cachedNames.get(String(value)) || String(value)
}

/**
 * The cached list, guaranteed to CONTAIN *current* — a combo cannot display
 * a value that isn't one of its options, so a set pushed from the Controller
 * (or picked before our list caught up, or living only on another machine's
 * copy of a shared library) would otherwise render blank and read as "the
 * push did nothing" (owner report 2026-08-09, the second half). The server
 * accepts unknown values already (`VALIDATE_INPUTS` returns True, §6.2), so
 * showing it is strictly honest: it is exactly what will execute.
 * @param {unknown} current
 * @returns {string[]}
 */
function valuesIncluding(current) {
  if (typeof current !== 'string' || !current) return cachedValues
  return cachedValues.includes(current) ? cachedValues : [...cachedValues, current]
}

/** FORMAT.md §6.2 (v0.64.0): the class id of the OTHER taggable loader
 * family — kept in sync by hand with controller.js's PICKER_NODE_CLASS,
 * same no-cross-import convention as NODE_CLASS above. */
const PICKER_NODE_CLASS = 'EPSLoraPicker'

/** Every taggable loader in the whole WORKFLOW (subgraphs included),
 * labeled "<title> #<pathId>" — the exact label shape controller.js's
 * `target` combo uses (path ids since v0.64.0, e.g. "#3:2" for a loader
 * inside SubgraphNode 3), so a Push State toast and this tag's on-canvas
 * display read the same identity string. Both families count: Power Lora
 * Loader (rgthree) AND EPS LoRA Picker (owner ask 2026-08-14). */
function findPllCandidates() {
  const out = []
  for (const { node, pathId } of api.walkLiveNodes(app.graph)) {
    const cls = node?.comfyClass ?? node?.constructor?.comfyClass ?? node?.type
    if (node?.type !== POWER_LORA_LOADER_TYPE && cls !== PICKER_NODE_CLASS) continue
    out.push({ id: pathId, label: `${node.title || node.type} #${pathId}` })
  }
  return out
}

/** FORMAT.md §6.2: the node PATH id embedded in a "<title> #<pathId>" label ("#2", or "#3:2" for a nested loader), or null for "(any)"/anything else — same regex shape as controller.js's `pllIdFromLabel()` (duplicated by hand, not imported; see file header). */
function pllIdFromLabel(label) {
  const match = /#(-?\d+(?::-?\d+)*)\s*$/.exec(String(label || ''))
  return match ? match[1] : null
}

/**
 * FORMAT.md §6.2 `mirrors loader` tag. Idempotent (checked by widget name)
 * so a double `nodeCreated` fire can never add it twice. See file header
 * for the "append LAST"/self-healing design notes.
 * @param {object} node
 */
function attachMirrorsWidget(node) {
  if ((node.widgets ?? []).some((w) => w.name === MIRRORS_WIDGET_NAME)) return
  const widget = node.addWidget('combo', MIRRORS_WIDGET_NAME, MIRRORS_ANY_VALUE, () => {}, {
    values: [MIRRORS_ANY_VALUE] // placeholder; replaced below once `widget` exists — same two-step idiom attachApplySetBehavior() already uses for the `set` combo.
  })
  // v0.68.1: PURE -- rebuilt on dropdown open (both renderers), no writes.
  // The vanished-loader self-heal that used to live here moved to
  // `healMirrorsTags()` (file header): a values() that wrote `widget.value`
  // ran on every canvas draw, because...
  widget.options.values = () => [MIRRORS_ANY_VALUE, ...findPllCandidates().map((c) => c.label)]
  // ...ComboWidget's `_displayValue` evaluates `values()` per draw when no
  // `getOptionLabel` is installed (installed bundle, read 2026-08-21). The
  // value IS the label here, so an identity mapper keeps the per-draw
  // display O(1) and leaves the graph walk to the open path only.
  widget.options.getOptionLabel = (value) => (value == null ? '' : String(value))
}

/** v0.68.1: the `mirrors loader` self-heal (FORMAT.md §6.2 "tolerates the
 * id disappearing") -- every Apply node whose tag names a loader that no
 * longer resolves falls back to "(any)". One workflow walk for ALL Apply
 * nodes, run from the graph watch below, never from a draw. */
function healMirrorsTags() {
  const liveIds = new Set(findPllCandidates().map((c) => String(c.id)))
  for (const node of applySetNodes()) {
    const widget = (node.widgets ?? []).find((w) => w.name === MIRRORS_WIDGET_NAME)
    if (!widget) continue
    const id = pllIdFromLabel(widget.value)
    if (id != null && !liveIds.has(id)) widget.value = MIRRORS_ANY_VALUE
  }
}

/** One-tick coalescer for `healMirrorsTags()` -- `onNodeRemoved` fires once
 * per node, and a workflow switch removes every node of the old graph. */
let mirrorsHealQueued = false
function scheduleMirrorsHeal() {
  if (mirrorsHealQueued) return
  mirrorsHealQueued = true
  setTimeout(() => {
    mirrorsHealQueued = false
    try {
      if (app.graph) healMirrorsTags()
    } catch (error) {
      api.warn('mirrors-tag heal failed', error)
    }
  }, 0)
}

/** Chained, install-once `onNodeRemoved` wrap on every graph in the
 * workflow (subgraphs included -- their add/remove hooks fire only on the
 * subgraph itself). controller.js's `installGraphNodeWatch` technique,
 * duplicated by hand per this file's no-cross-import rule; its own flag so
 * the two never collide. Re-armed from every `nodeCreated` so a subgraph
 * created later is watched before a loader inside it can vanish. */
function installMirrorsGraphWatch() {
  if (!app.graph || typeof api.walkGraphs !== 'function') return
  for (const graph of api.walkGraphs(app.graph)) {
    if (!graph || graph.__epsSetsMirrorsWatch) continue
    graph.__epsSetsMirrorsWatch = true
    const original = graph.onNodeRemoved
    graph.onNodeRemoved = function (...args) {
      let result
      try {
        result = original?.apply(this, args)
      } catch (error) {
        api.warn('original onNodeRemoved threw', error)
      }
      scheduleMirrorsHeal()
      return result
    }
  }
}

/**
 * FORMAT.md §6.2 (2026-07-20): hide/show `strength_scale` per the node's
 * `Show strength scale` property. Safe to call redundantly (idempotent —
 * just re-derives `.hidden` from the current property value each time), so
 * both the one-time attach call and every live `onPropertyChanged` fire can
 * share this one function. No-ops quietly if the widget isn't found (e.g. a
 * future backend rename) rather than throwing.
 * @param {object} node
 */
function applyStrengthScaleVisibility(node) {
  const widget = (node.widgets ?? []).find((w) => w.name === STRENGTH_SCALE_WIDGET_NAME)
  if (!widget) return
  // See file header: `.hidden` is a real litegraph layout primitive here
  // (controller.js's `Show status` uses the identical mechanism) — it drops
  // the row from drawing/layout/size, it does not just blank the value, and
  // the value itself keeps flowing to apply()/IS_CHANGED() while hidden.
  widget.hidden = node.properties?.[PROP_SHOW_STRENGTH_SCALE] !== true
  // Vue nodes decide visibility from options.hidden and ignore the litegraph
  // flag (FORMAT.md section 7.5) -- keep both in lockstep, both directions.
  widget.options = { ...(widget.options || {}), hidden: widget.hidden }
  node.setDirtyCanvas(true, true)
}

/**
 * FORMAT.md §6.2/§4.1 (2026-07-20 composite fix): hide/show `loader_slot`
 * per the node's `Show loader slot` property. Exact mirror of
 * `applyStrengthScaleVisibility()` immediately above — same `.hidden`
 * primitive, same idempotent-safe-to-call-redundantly design, same silent
 * no-op if the widget isn't found (e.g. a future backend rename). The
 * widget's VALUE is untouched either way — hiding is purely cosmetic, so a
 * hidden `loader_slot` still serializes and still feeds `apply()`/
 * `IS_CHANGED()` at whatever it's set to (default 0).
 * @param {object} node
 */
function applyLoaderSlotVisibility(node) {
  const widget = (node.widgets ?? []).find((w) => w.name === LOADER_SLOT_WIDGET_NAME)
  if (!widget) return
  widget.hidden = node.properties?.[PROP_SHOW_LOADER_SLOT] !== true
  // Same Vue-nodes lockstep as applyStrengthScaleVisibility() above.
  widget.options = { ...(widget.options || {}), hidden: widget.hidden }
  node.setDirtyCanvas(true, true)
}

// ---------------------------------------------------------------------------
// Pinned state (FORMAT.md §6.2, provenance M3) -- see the file header's
// "Pinned state" paragraph. Pure helpers first (exported for
// tests/test_m3_pinning_js.py), then the badge row + wiring.
// ---------------------------------------------------------------------------

/**
 * Parse the `pinned_state` widget's raw value. `""` / non-string /
 * unparseable / anything without a `set` object carrying `loras` or
 * `loaders` -> null (= live); otherwise `{format, slug, name, set, source:
 * {token, captured}}` (strings coerced, missing -> ''; `name` falls back to
 * the set's own name, then the slug). Lenient on the `format` number on
 * purpose -- the UI's job is to SHOW whatever the backend pinned.
 * @param {unknown} raw
 */
export function parsePinnedState(raw) {
  if (typeof raw !== 'string' || raw.trim() === '') return null
  let parsed
  try {
    parsed = JSON.parse(raw)
  } catch {
    return null
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null
  const set = parsed.set
  if (!set || typeof set !== 'object' || Array.isArray(set)) return null
  if (!Array.isArray(set.loras) && !Array.isArray(set.loaders)) return null
  const source = parsed.source && typeof parsed.source === 'object' ? parsed.source : {}
  const str = (value) => (value == null ? '' : String(value))
  return {
    format: typeof parsed.format === 'number' ? parsed.format : null,
    slug: str(parsed.slug),
    name: str(parsed.name) || str(set.name) || str(parsed.slug),
    set,
    source: { token: str(source.token), captured: str(source.captured) }
  }
}

/** Basename without extension across either separator -- the same stem
 * `loras_text` uses (nodes_sets.py). */
export function loraStem(file) {
  const base = String(file ?? '').split(/[\\/]/).pop() ?? ''
  return base.replace(/\.[^.]*$/, '')
}

/**
 * One §4 `loras` list, normalized for display/compare: rows with a
 * non-empty string `file`, in order, as `{file, on, strength,
 * strength_clip}` -- `on` defaults true, `strength` to 1, `strength_clip`
 * to null unless it is a finite number (§4: null = "use strength for
 * both"). Separators are normalized to `/` so a set re-saved on another
 * OS (§4 separator-insensitive resolve) does not read as drift.
 * @param {unknown} loras
 */
export function normalizedRows(loras) {
  const out = []
  for (const row of Array.isArray(loras) ? loras : []) {
    if (!row || typeof row !== 'object' || typeof row.file !== 'string' || !row.file) continue
    const strengthRaw = row.strength
    const strength =
      strengthRaw == null || strengthRaw === '' || !Number.isFinite(Number(strengthRaw)) ? 1 : Number(strengthRaw)
    const clipRaw = row.strength_clip
    const strength_clip =
      clipRaw == null || clipRaw === '' || !Number.isFinite(Number(clipRaw)) ? null : Number(clipRaw)
    out.push({ file: row.file.replace(/\\/g, '/'), on: row.on === undefined ? true : !!row.on, strength, strength_clip })
  }
  return out
}

/** Per-loader row lists of a set: `loaders[i].loras` for a §4.1 format-2
 * state, else the single `loras` list. */
function loaderRowLists(set) {
  if (Array.isArray(set?.loaders) && set.loaders.length) {
    return set.loaders.map((loader) => normalizedRows(loader?.loras))
  }
  return [normalizedRows(set?.loras)]
}

/** `%g`-ish strength text: 0.8 -> "0.8", 1 -> "1", 0.33333 -> "0.3333". */
function formatStrength(value) {
  return String(Math.round(Number(value) * 10000) / 10000)
}

/**
 * One-line summary of what a pinned set APPLIES: enabled rows in order as
 * `stem strength` (a distinct clip strength appends `/clip`), joined by
 * ` · `; a §4.1 format-2 state lists per loader as `L0 … / L1 …`; no
 * enabled rows reads "no enabled loras".
 * @param {unknown} set
 */
export function pinnedRowsSummary(set) {
  const one = (rows) => {
    const enabled = rows.filter((row) => row.on)
    if (!enabled.length) return 'no enabled loras'
    return enabled
      .map((row) => {
        const main = `${loraStem(row.file)} ${formatStrength(row.strength)}`
        return row.strength_clip == null || row.strength_clip === row.strength
          ? main
          : `${main}/${formatStrength(row.strength_clip)}`
      })
      .join(' · ')
  }
  const lists = loaderRowLists(set)
  if (Array.isArray(set?.loaders) && set.loaders.length) {
    return lists.map((rows, index) => `L${index} ${one(rows)}`).join(' / ')
  }
  return one(lists[0])
}

/**
 * Drift verdict between the pinned set dict and the CURRENT set (the
 * `GET /lora_library/set` payload): 'match' when every loader's normalized
 * rows (file, on, strength, strength_clip, in order) and the trigger words
 * agree, else 'differs'. Name/notes are display-only and ignored; a
 * missing/non-object current set is 'differs' (the route's 404 is handled
 * by the caller as "state no longer exists").
 */
export function comparePinnedSet(pinnedSet, currentSet) {
  if (!currentSet || typeof currentSet !== 'object') return 'differs'
  const signature = (set) =>
    JSON.stringify({ loaders: loaderRowLists(set), trigger: String(set?.trigger_words ?? '').trim() })
  return signature(pinnedSet) === signature(currentSet) ? 'match' : 'differs'
}

/** The badge text for a pin + verdict. The variants are a contract with
 * tests/test_m3_pinning_js.py (and README/FORMAT §6.2). */
export function pinnedStateBadgeText(pin, status) {
  const name = pin?.name || pin?.slug || 'state'
  const token = pin?.source?.token
  const origin = token ? `captured from image ${token}` : 'captured from a saved image'
  const verdict =
    status === 'match'
      ? 'matches current state'
      : status === 'differs'
        ? 'differs from current state'
        : status === 'missing'
          ? 'state no longer exists'
          : status === 'error'
            ? 'current state unavailable'
            : 'checking current state…'
  return `📌 Pinned state: ${name} — ${origin} — ${verdict}`
}

const PIN_CSS_TEXT = `
.eps-asp-root {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 2px;
  width: 100%;
  box-sizing: border-box;
  padding: 2px 6px;
  overflow: hidden;
  border: 1px solid var(--border-color, #444);
  border-radius: 4px;
  background: var(--comfy-input-bg, #1e1e1e);
  color: var(--input-text, #ccc);
  font-family: inherit;
  font-size: 11px;
}
.eps-asp-line { display: flex; align-items: center; gap: 6px; min-width: 0; }
.eps-asp-badge {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-weight: 600;
}
.eps-asp-badge-differs { color: var(--error-text, #ff9f43); }
.eps-asp-badge-unknown { font-style: italic; }
.eps-asp-rows {
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: var(--descrip-text, #999);
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 10px;
}
.eps-asp-btn {
  flex: 0 0 auto;
  background: var(--comfy-menu-bg, #262626);
  border: 1px solid var(--border-color, #444);
  color: var(--input-text, #ccc);
  border-radius: 4px;
  padding: 1px 8px;
  font-size: 11px;
  cursor: pointer;
}
.eps-asp-btn:hover { background: var(--content-hover-bg, #2a2a2a); }
`

let pinStylesInjected = false
function injectPinStyles() {
  if (pinStylesInjected) return
  pinStylesInjected = true
  if (document.getElementById(PIN_STYLE_TAG_ID)) return
  const style = document.createElement('style')
  style.id = PIN_STYLE_TAG_ID
  style.textContent = PIN_CSS_TEXT
  document.head.appendChild(style)
}

function findWidgetByName(node, name) {
  return (node?.widgets ?? []).find((w) => w && w.name === name) || null
}

/** Best-effort toast (cross_sweep.js `toastInfo` idiom); never throws. */
function toastInfo(summary, detail) {
  try {
    if (app.extensionManager?.toast?.add) {
      app.extensionManager.toast.add({ severity: 'info', summary, detail, life: 5000 })
      return
    }
  } catch (error) {
    api.warn('toast failed', error)
  }
  api.log(`${summary}: ${detail}`)
}

/**
 * Build the pin badge row ONCE per node (idempotent via `node.__epsSetsPin`)
 * as a display-only DOM widget appended LAST -- see the file header for why
 * last, and why a DOM widget at all. Hidden (both flags + zero height)
 * until a pin arrives; `applyPinView()` shows/hides it.
 * @param {object} node @returns {object|null} the pin state, or null when
 *   this frontend has no addDOMWidget.
 */
function attachPinBadge(node) {
  if (node.__epsSetsPin) return node.__epsSetsPin
  if (typeof node.addDOMWidget !== 'function') return null
  injectPinStyles()
  const badgeEl = document.createElement('div')
  badgeEl.className = 'eps-asp-badge'
  const unpinBtn = document.createElement('button')
  unpinBtn.className = 'eps-asp-btn'
  unpinBtn.textContent = 'Unpin'
  unpinBtn.title = 'Go back to the live dropdown — the node applies the selected state again'
  const line = document.createElement('div')
  line.className = 'eps-asp-line'
  line.append(badgeEl, unpinBtn)
  const rowsEl = document.createElement('div')
  rowsEl.className = 'eps-asp-rows'
  const root = document.createElement('div')
  root.className = 'eps-asp-root'
  root.append(line, rowsEl)
  // The element is sized to the TEXT half only; the reported heights carry
  // the 2*margin budget (see PIN_ROW_HEIGHT).
  root.style.height = `${PIN_ROW_HEIGHT}px`
  const state = {
    node,
    root,
    badgeEl,
    rowsEl,
    unpinBtn,
    domWidget: null,
    visible: false,
    outerHeight: PIN_ROW_HEIGHT + 2 * DOM_WIDGET_MARGIN_FALLBACK,
    pinnedRaw: '',
    pin: null,
    driftStatus: 'pending',
    driftCurrent: null,
    driftToken: 0,
    setWidgetOriginal: null,
    grown: false
  }
  node.__epsSetsPin = state
  const domWidget = node.addDOMWidget(PIN_WIDGET_NAME, PIN_WIDGET_TYPE, root, {
    hideOnZoom: true,
    serialize: false, // excludes from the API prompt (utils/executionUtil.ts)
    getMinHeight: () => (state.visible ? state.outerHeight : 0),
    getMaxHeight: () => (state.visible ? state.outerHeight : 0)
  })
  const margin = typeof domWidget.margin === 'number' ? domWidget.margin : DOM_WIDGET_MARGIN_FALLBACK
  state.outerHeight = PIN_ROW_HEIGHT + 2 * margin
  domWidget.computeSize = (width) => [width, state.visible ? state.outerHeight : 0]
  domWidget.computedHeight = 0
  // Excludes from the workflow JSON -- a DIFFERENT flag from options.serialize
  // above (notebook.js's attachDomWidget() header explains why both exist).
  domWidget.serialize = false
  domWidget.serializeValue = () => undefined
  state.domWidget = domWidget
  unpinBtn.addEventListener('click', () => unpinState(state))
  setPinRowVisible(state, false)
  return state
}

/** Show/hide the badge row: BOTH hide flags (canvas `hidden`, Vue
 * `options.hidden` -- §7.5) plus a zero reported height and `display:none`
 * on the element, so a renderer that lays hidden DOM widgets out anyway
 * still gives it no room. */
function setPinRowVisible(state, visible) {
  const widget = state.domWidget
  state.visible = !!visible
  state.root.style.display = visible ? '' : 'none'
  if (!widget) return
  widget.hidden = !visible
  widget.options = { ...(widget.options || {}), hidden: !visible }
  widget.computedHeight = visible ? state.outerHeight : 0
}

/** Both hide flags on the backend's `pinned_state` widget -- exactly the
 * `strength_scale`/`loader_slot` pair above, minus the reveal property
 * (a pin is shown by the badge row, never by this raw JSON). No-op when
 * the backend hasn't shipped the widget. */
function hidePinnedStateWidget(node) {
  const widget = findWidgetByName(node, PINNED_STATE_WIDGET_NAME)
  if (!widget) return
  widget.hidden = true
  widget.options = { ...(widget.options || {}), hidden: true }
}

/** Chain the `pinned_state` widget's callback so a value arriving THROUGH
 * the callback (a live link at queue time, an extension, our own unpin)
 * reconciles the view; configure() bypasses callbacks and is covered by
 * wirePinConfigureSync(). Wrapped, never replaced. */
function wirePinnedStateWidget(node, state) {
  const widget = findWidgetByName(node, PINNED_STATE_WIDGET_NAME)
  if (!widget) return
  const original = widget.callback
  widget.callback = function (value, ...rest) {
    let result
    if (typeof original === 'function') {
      try {
        result = original.apply(this, [value, ...rest])
      } catch (error) {
        api.warn('original pinned_state callback threw', error)
      }
    }
    try {
      syncPinFromWidget(state)
    } catch (error) {
      api.warn('pinned_state sync threw', error)
    }
    return result
  }
}

/** `onConfigure` fires at the END of configure() -- the one hook that sees
 * restored widgets_values, for a whole-workflow load (an image drop), a
 * paste and a clone alike (notebook.js's wireConfigureReload lesson). Heals
 * the pre-M3 positional shift first, then reconciles the pin. Chained. */
function wirePinConfigureSync(node, state) {
  const original = node.onConfigure
  node.onConfigure = function (...args) {
    let result
    if (typeof original === 'function') {
      try {
        result = original.apply(this, args)
      } catch (error) {
        api.warn('original onConfigure threw', error)
      }
    }
    try {
      healLegacyWidgetShift(node)
      syncPinFromWidget(state)
    } catch (error) {
      api.warn('pinned_state sync after configure failed', error)
    }
    return result
  }
}

/**
 * Pre-M3 positional shift, decided PURELY (exported for the Node probe in
 * tests/test_m3_pinning_js.py). Pre-M3 workflows saved `widgets_values` =
 * [set, strength_scale, loader_slot, <mirrors loader value>]; with the
 * backend's `pinned_state` now sitting BEFORE the frontend-appended
 * `mirrors loader` in `node.widgets`, litegraph's positional restore lands
 * that old mirrors value ("(any)", a bare id "12", a path id "12:3", or a
 * "<title> #<id>" label) IN `pinned_state` and leaves the tag at its
 * default. A pin is always JSON carrying `format`/`set`, so a `pinned_state`
 * that is non-empty and NOT pin JSON, while the mirrors combo is still at
 * its default, is certainly the shifted value: move it into the mirrors
 * slot and blank `pinned_state`. A non-default mirrors value means this
 * node was already migrated (or hand-set) -- leave everything alone.
 * @param {unknown} pinnedRaw the `pinned_state` widget's value
 * @param {unknown} mirrorsValue the `mirrors loader` widget's value
 * @returns {{pinned: string, mirrors: unknown, migrated: boolean}}
 */
export function migrateLegacyMirrorsValue(pinnedRaw, mirrorsValue) {
  const untouched = { pinned: typeof pinnedRaw === 'string' ? pinnedRaw : '', mirrors: mirrorsValue, migrated: false }
  if (typeof pinnedRaw !== 'string' || pinnedRaw === '') return untouched
  if (mirrorsValue != null && mirrorsValue !== MIRRORS_ANY_VALUE) return untouched
  if (parsePinnedState(pinnedRaw)) return untouched
  let looksLikePinJson = false
  try {
    const parsed = JSON.parse(pinnedRaw)
    looksLikePinJson =
      !!parsed && typeof parsed === 'object' && !Array.isArray(parsed) && ('format' in parsed || 'set' in parsed)
  } catch {
    looksLikePinJson = false
  }
  if (looksLikePinJson) return untouched // a (malformed) pin is the backend's to reject, not ours to move
  return { pinned: '', mirrors: pinnedRaw, migrated: true }
}

/** Apply `migrateLegacyMirrorsValue()` to the live widgets on configure:
 * the mirrors combo is written through value + callback (the pack's
 * widget-write idiom), `pinned_state` is blanked, and the migration is
 * logged ONCE per node to console.info -- silent otherwise, no toast.
 * Returns true when it migrated. */
function healLegacyWidgetShift(node) {
  const pinnedWidget = findWidgetByName(node, PINNED_STATE_WIDGET_NAME)
  const mirrorsWidget = findWidgetByName(node, MIRRORS_WIDGET_NAME)
  if (!pinnedWidget || !mirrorsWidget) return false
  const verdict = migrateLegacyMirrorsValue(pinnedWidget.value, mirrorsWidget.value)
  if (!verdict.migrated) return false
  mirrorsWidget.value = verdict.mirrors
  try {
    mirrorsWidget.callback?.(verdict.mirrors)
  } catch (error) {
    api.warn('mirrors loader callback threw during the legacy migration', error)
  }
  pinnedWidget.value = verdict.pinned
  if (!node.__epsSetsLegacyMigrated) {
    node.__epsSetsLegacyMigrated = true
    console.info(
      '[lora_library] EPS Apply LoRA Set: moved a pre-M3 "mirrors loader" value',
      verdict.mirrors,
      'out of the new pinned_state widget (one-time positional migration, node',
      node.id,
      ')'
    )
  }
  return true
}

/**
 * Reconcile the badge with the `pinned_state` widget's CURRENT value --
 * idempotent (raw-string compare first), so it is safe from every hook that
 * can carry a pin: onConfigure, the widget callback, unpin, attach. A new
 * pin kicks ONE drift fetch. Returns true when the pin state changed.
 */
function syncPinFromWidget(state) {
  const widget = findWidgetByName(state.node, PINNED_STATE_WIDGET_NAME)
  if (!widget) return false
  const raw = typeof widget.value === 'string' ? widget.value : ''
  if (raw === state.pinnedRaw) return false
  state.pinnedRaw = raw
  state.pin = parsePinnedState(raw)
  state.driftStatus = 'pending'
  state.driftCurrent = null
  state.driftToken += 1 // an older fetch, if any, lands on nothing
  applyPinView(state)
  if (state.pin) {
    fetchPinDrift(state).catch((error) => api.warn('pin drift check failed', error))
  }
  return true
}

/** Repaint the badge row from `state.pin` + `state.driftStatus`: show/hide,
 * text, summary line (full text in the tooltip, the current state's rows
 * alongside once fetched), the `set` combo's inactive look, and the node's
 * height (lifted by the row once on pin, given back on unpin, never below
 * litegraph's computed floor). */
function applyPinView(state) {
  const pinned = !!state.pin
  setPinRowVisible(state, pinned)
  markSetComboInactive(state, pinned)
  if (pinned) {
    const status = state.driftStatus
    state.badgeEl.textContent = pinnedStateBadgeText(state.pin, status)
    state.badgeEl.className =
      'eps-asp-badge' +
      (status === 'differs' || status === 'missing'
        ? ' eps-asp-badge-differs'
        : status === 'match'
          ? ''
          : ' eps-asp-badge-unknown')
    const captured = state.pin.source.captured ? `Captured ${state.pin.source.captured}.\n` : ''
    state.badgeEl.title =
      `${captured}The node applies these pinned rows (not the dropdown's state) until you Unpin.`
    const summary = pinnedRowsSummary(state.pin.set)
    state.rowsEl.textContent = summary
    state.rowsEl.title =
      `Pinned rows (applied while pinned):\n${summary}` +
      (state.driftCurrent ? `\nCurrent state:\n${pinnedRowsSummary(state.driftCurrent)}` : '')
  }
  syncPinNodeHeight(state, pinned)
  state.node.graph?.setDirtyCanvas(true, true)
}

/** ONE `GET /lora_library/set?slug=` per pin (token-guarded against a newer
 * pin / unpin landing first): 404 -> "state no longer exists", any other
 * failure -> "current state unavailable" (logged), success -> compare. */
async function fetchPinDrift(state) {
  const token = state.driftToken
  const slug = state.pin?.slug
  if (!slug) {
    state.driftStatus = 'error'
    applyPinView(state)
    return
  }
  try {
    const data = await api.getJson('/lora_library/set', { slug })
    if (token !== state.driftToken) return
    state.driftCurrent = data
    state.driftStatus = comparePinnedSet(state.pin.set, data)
  } catch (error) {
    if (token !== state.driftToken) return
    state.driftStatus = error?.status === 404 ? 'missing' : 'error'
    if (error?.status !== 404) api.warn('fetching the current set for the pin badge failed', error)
  }
  applyPinView(state)
}

/** The `set` combo stays visible but reads inactive while pinned -- label
 * "set (ignored while pinned)", disabled (greyed, not clickable: its value
 * would not execute anyway), tooltip "ignored while pinned"; everything is
 * put back exactly on unpin. Label/disabled are litegraph widget fields the
 * Vue renderer reads too; the combo's VALUE is never touched. */
function markSetComboInactive(state, pinned) {
  const widget = findWidgetByName(state.node, WIDGET_NAME)
  if (!widget) return
  if (pinned) {
    if (!state.setWidgetOriginal) {
      state.setWidgetOriginal = { label: widget.label, disabled: widget.disabled, tooltip: widget.tooltip }
    }
    widget.label = SET_COMBO_PINNED_LABEL
    widget.disabled = true
    widget.tooltip = SET_COMBO_PINNED_TOOLTIP
  } else if (state.setWidgetOriginal) {
    widget.label = state.setWidgetOriginal.label
    widget.disabled = state.setWidgetOriginal.disabled
    widget.tooltip = state.setWidgetOriginal.tooltip
    state.setWidgetOriginal = null
  }
}

/** Lift the node by the row once when a pin appears; give it back on unpin;
 * never below `computeSize()`'s floor (which already counts the visible
 * row). `node.size` is a Float32Array -- never Array.isArray it (§7.2). */
function syncPinNodeHeight(state, pinned) {
  if (pinned === state.grown) return
  state.grown = pinned
  const node = state.node
  if (!node?.size || typeof node.setSize !== 'function') return
  const floor = typeof node.computeSize === 'function' ? node.computeSize()[1] : 0
  const delta = pinned ? state.outerHeight : -state.outerHeight
  node.setSize([node.size[0], Math.max(node.size[1] + delta, floor)])
  node.graph?.setDirtyCanvas(true, true)
}

/** One-click back to the live dropdown: write "" through the `pinned_state`
 * widget's value + callback (the pack's widget-write idiom), let the
 * callback chain reconcile (a direct, idempotent reconcile follows in case
 * something upstream swallowed it), toast. */
function unpinState(state) {
  const widget = findWidgetByName(state.node, PINNED_STATE_WIDGET_NAME)
  if (!widget || !state.pin) return
  widget.value = ''
  try {
    widget.callback?.('')
  } catch (error) {
    api.warn('pinned_state callback threw', error)
  }
  syncPinFromWidget(state)
  state.node.graph?.setDirtyCanvas(true, true)
  toastInfo('Unpinned — back to the live state', 'The node applies the state selected in the dropdown again.')
}

/** Per-node M3 wiring (called from attachApplySetBehavior AFTER the
 * `mirrors loader` tag so the DOM row lands last). Everything is a no-op on
 * a backend without `pinned_state`. */
function attachPinBehavior(node) {
  if (!findWidgetByName(node, PINNED_STATE_WIDGET_NAME)) return
  const state = attachPinBadge(node)
  if (!state) return
  hidePinnedStateWidget(node)
  wirePinnedStateWidget(node, state)
  wirePinConfigureSync(node, state)
  // A fresh node holds "" (no-op); a restored node's value lands in
  // configure() and the chained onConfigure above reconciles it.
  syncPinFromWidget(state)
}

/**
 * Per-instance hook (called from lora_library.js `nodeCreated`); no-op for
 * every node type except LoraLibraryApplySet.
 * @param {object} node
 */
export function attachApplySetBehavior(node) {
  const comfyClass = node?.comfyClass ?? node?.constructor?.comfyClass
  if (comfyClass !== NODE_CLASS) return
  installSetValues(node)
  // 2026-07-19c: append the `mirrors loader` tag AFTER the `set` combo wiring
  // above so it always lands after every server widget in `node.widgets`
  // (file header "append LAST" note) — this call only ADDS a widget, it
  // doesn't touch the `set` combo, so the relative order between the two
  // blocks above/below doesn't itself matter, only that this runs after
  // ComfyUI's own Python-declared widgets already exist, which is always
  // true by the time `nodeCreated` fires.
  attachMirrorsWidget(node)
  // Provenance M3 (FORMAT.md §6.2): the pin badge row -- a display-only
  // DOM widget that must land AFTER `mirrors loader` (file header: save
  // leaves a hole at a serialize:false widget's index, restore skips it),
  // hidden until a pin arrives via configure(). No-op without the backend's
  // `pinned_state` widget.
  try {
    attachPinBehavior(node)
  } catch (error) {
    api.warn('pinned_state badge attach failed', error)
  }
  // v0.68.1: the heal that used to ride on every draw now rides on node
  // removal, plus one deferred pass per created node -- `nodeCreated` runs
  // BEFORE `configure()` restores the tag value on a workflow load, so the
  // tick lands after the whole (synchronous) load, loaders included.
  try {
    installMirrorsGraphWatch()
    scheduleMirrorsHeal()
  } catch (error) {
    api.warn('mirrors-tag watch install failed', error)
  }

  // FORMAT.md §6.2 (2026-07-20): `Show strength scale` node property, default
  // false — right-click Properties reveals the widget. addProperty() alone
  // only seeds `node.properties`; it never fires onPropertyChanged and never
  // touches the widget (see file header's "2026-07-20 addition" section for
  // why this file wraps onPropertyChanged, resolution.js-style, rather than
  // defining it as a class method the way controller.js does), so an
  // explicit applyStrengthScaleVisibility() call right after wiring is what
  // actually hides it on a fresh node.
  node.addProperty(PROP_SHOW_STRENGTH_SCALE, false, 'boolean')
  // FORMAT.md §6.2/§4.1 (2026-07-20 composite fix): `Show loader slot` —
  // exact mirror of `Show strength scale` just above, added in this same
  // attach pass so both properties already exist before the shared
  // onPropertyChanged wrapper below needs to branch on either name.
  node.addProperty(PROP_SHOW_LOADER_SLOT, false, 'boolean')

  const originalOnPropertyChanged = node.onPropertyChanged
  node.onPropertyChanged = function (name, value, prevValue) {
    const result = originalOnPropertyChanged?.call(this, name, value, prevValue)
    if (name === PROP_SHOW_STRENGTH_SCALE) applyStrengthScaleVisibility(this)
    if (name === PROP_SHOW_LOADER_SLOT) applyLoaderSlotVisibility(this)
    return result
  }

  applyStrengthScaleVisibility(node)
  applyLoaderSlotVisibility(node)
}

/** One-time wiring at extension setup: seed the cache so the first dropdown
 * open is already current, and refresh immediately whenever the controller
 * announces a CRUD (`lora_library:sets-changed`, see controller.js) so the
 * FIRST open after a capture/update/delete is already exact — the throttled
 * open-time refetch is only the fallback for out-of-band changes (another
 * machine editing the shared library, curl, etc.). */
export function initSetsFreshness() {
  window.addEventListener('lora_library:sets-changed', async () => {
    // v0.68.1 (owner: "saving a state is slow / non-responsive"): this
    // handler used to ALSO await `app.refreshComboInNodes()` -- which on the
    // installed frontend (1.48.7, `reloadNodeDefs`) re-fetches /object_info
    // (every pack's INPUT_TYPES re-run server-side), re-registers EVERY node
    // def, rewrites every combo of every node, rebuilds the Vue node-def
    // store and raises two toasts -- on the main thread, right after Save.
    // Its premise ("the Vue renderer builds its selects from the node
    // DEFINITIONS") is false on this frontend: the Vue select evaluates
    // `widget.options.values()` on open (WidgetSelect `handleOpenChange` ->
    // `refreshOptions` -> `resolveRawValues`), i.e. THIS module's function
    // and THIS cache. The forced cache refresh below is the whole job.
    await refreshSetsCache(true)
  })
  // `app.graph` exists by `setup()`; arm the mirrors-tag watch on the root
  // (and any subgraphs already present) before any node can be removed.
  try {
    installMirrorsGraphWatch()
  } catch (error) {
    api.warn('mirrors-tag watch install failed', error)
  }
  wrapComfyComboRefresh()
  refreshSetsCache(true)
}

/**
 * Wrap `app.refreshComboInNodes` ONCE so our dynamic `values` functions are
 * re-installed after ANY refresh — ours or the frontend's own (the model
 * refresh command, an extension, a future core call). Without this the very
 * first frontend-initiated refresh silently froze this combo for the rest of
 * the session (owner report 2026-08-09). Wrapped, never replaced, and
 * idempotent via a module flag so a double `setup()` cannot double-wrap.
 */
function wrapComfyComboRefresh() {
  if (comboRefreshWrapped) return
  if (typeof app.refreshComboInNodes !== 'function') return
  comboRefreshWrapped = true
  const original = app.refreshComboInNodes.bind(app)
  app.refreshComboInNodes = async function (...args) {
    const result = await original(...args)
    try {
      for (const node of applySetNodes()) installSetValues(node)
    } catch (error) {
      api.warn('re-installing set combo values after a refresh failed', error)
    }
    return result
  }
}
