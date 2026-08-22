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
