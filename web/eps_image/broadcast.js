/**
 * @file EPS Run Multiplier "broadcast" -- the per-node wiring (FORMAT.md §6.10
 * "Broadcast (v1)", owner ask 2026-10-03: "this node broadcasts values across
 * a workflow so you don't have to manually hook up nodes over and over
 * again"). `cross_sweep.js` calls `attach(node, readoutState)` from its own
 * `attach()`; `web/eps_image.js` calls `init()`, `getNodeMenuItems(node)`,
 * `installLegacyMenuFallback(...)` and registers `SETTINGS`.
 *
 * The three layers (so a fix lands in the right one):
 *   - `broadcast_plan.js`   PURE: what to wire, what not and why.
 *   - `broadcast_graph.js`  LIVE: snapshot, apply, remove, withdraw/restore,
 *                           reconcile, one-undo-step, the link-index API.
 *   - `broadcast_ui.js`     DOM: the row, the ⋯ popover, the preview dialog.
 *   - this file             the node glue: row mount, menu, settings, the
 *                           Keep-wired graph watch, the Use Everywhere hook.
 *
 * **Opt-in, never on load.** A multiplier that never used broadcast has no
 * `properties.Broadcast`, and NOTHING wires on load, paste, undo or a tab
 * switch: the first pass after a rebuild only BASELINES (it records which
 * nodes exist) and drops records that no longer verify. Old workflows load
 * and behave exactly as before; workflows saved with broadcast load on older
 * EPS as ordinary wires plus an unused property.
 *
 * **Keep wired (S3).** Default OFF, per multiplier. The graph watch re-plans
 * after node adds/removes and after any canvas change, but only wires NEW
 * nodes -- ones that did not exist at the previous pass -- whose proposal is
 * direct or through an existing subgraph input. (Adding an input to a
 * subgraph DEFINITION is never automatic: it is Wire now's job, behind the
 * preview.) A wire the user unplugs or replaces becomes "leave alone"; our
 * own disconnects carry the `isApplying()` guard so they never count; the
 * user's own wire is never replaced. Unwiring the multiplier's `vae` input
 * withdraws its `vae` wires (toast) and rewiring restores them -- a wire
 * from a dead output would otherwise fail the run (nodes_cross_sweep.py
 * v0.51.0 guard). No polling: events only, coalesced through ONE short
 * debounce (`PASS_DEBOUNCE_MS`).
 *
 * **Hooks (graph watch).** Installed on every graph via `walkGraphs`
 * (subgraph events fire on the SUBGRAPH, never the root) and RE-VERIFIED on
 * every `ensureWatch()` -- cross_sweep.js's stored-and-re-verified idiom,
 * never a one-shot flag: core's own hooks restore `graph.onNodeAdded/
 * onNodeRemoved` on every subgraph enter/exit and silently drop a wrapper
 * installed after them (v0.68.1). Plus ONE `litegraph:canvas` after-change
 * listener on `document` (capture phase): a user's link edit goes through
 * the canvas pointer handlers, which emit it, while `onAfterChange` is
 * unreliable for disconnects (rig-probed 2026-08-14).
 *
 * **Tucked wires + Reach (plan M3, FORMAT.md §6.10).** The drawing hook lives
 * in `broadcast_draw.js` (link-level, installed here from `init` / `setup` /
 * `attach` / every pass -- all idempotent); this file owns the per-multiplier
 * `look` and `scope` setters, the canvas right-click item "Broadcast: show all
 * wires" (`getCanvasMenuItems`, with a `getCanvasMenuOptions` fallback for
 * frontends without the hook) and re-exports the draw module's session switch.
 * Reach ('group') is a planner decision: `planFor` already hands it the
 * snapshot with group membership folded in.
 *
 * **Use Everywhere good citizen (S4, plan M4).** `reject_ue_connection` is
 * UE's documented per-node veto hook (research §3.5). The multiplier's
 * optional sweep inputs return true, so a UE broadcaster can't silently turn
 * the sweep side on by feeding an empty `model`/`clip`/`vae`/`label`/
 * `model_low` at queue time (our readout could not even see it: UE's wires
 * exist only during the queue).
 */

import { app } from '../../../scripts/app.js'
import { walkGraphs, watchGraphHooks } from '../lora_library/api.js'
import {
  BROADCAST_OUTPUTS,
  KINDS,
  LOOKS,
  MULTIPLIER_CLASS_ID,
  SCOPES,
  allNodePaths,
  outputEnabled,
  planBroadcast
} from './broadcast_plan.js'
import {
  applyProposals,
  broadcastLinkIndex,
  broadcastLinkOwner,
  broadcastLinkOwners,
  bumpBroadcastEpoch,
  enforceNegativeGuard,
  findMultipliers,
  groupsHolding,
  isApplying,
  isBroadcastLink,
  pathIdOf,
  planFor,
  readConfig,
  reconcileMultiplier,
  removeBroadcastWires,
  restoreOutputWires,
  rootGraphOf,
  runAsOneUndoStep,
  snapshotFromRoot,
  withdrawOutputWires,
  writeConfig
} from './broadcast_graph.js'
import { ensureDrawHooks, getShowAllWires, setShowAllWires } from './broadcast_draw.js'
import {
  BROADCAST_ROW_HEIGHT,
  buildBroadcastRow,
  closeDialog,
  closePopover,
  isPopoverOpenFor,
  openPopover,
  openPreviewDialog,
  rowSummary,
  wiredToastText
} from './broadcast_ui.js'

const PREFIX = '[eps_image:broadcast]'
const NODE_TITLE = 'EPS Run Multiplier'

/** Re-exported for the rendering stage (FORMAT.md §6.10 "Broadcast link
 * index"): `broadcastLinkIndex(rootGraph)` -> `Map<graphKey, Set<linkId>>`,
 * `isBroadcastLink(graph, linkId)`, `broadcastLinkOwner(graph, linkId)` (tucked-wires stage:
 * the owning multiplier + its `look`) and `bumpBroadcastEpoch()` to invalidate
 * the cache -- plus the draw module's session switch. */
export { broadcastLinkIndex, broadcastLinkOwner, bumpBroadcastEpoch, getShowAllWires, isBroadcastLink, setShowAllWires }

/** The ONE ComfyUI setting (owner decision 2026-10-03): text, image and label
 * connect only to inputs with exactly the same NAME and TYPE, and only when
 * this is on. Id naming follows `EPSNodes.HealModelPaths` (path_heal.js),
 * registered through the extension's `settings` array like
 * lora_library/settings.js. */
export const BROADCAST_SETTING_ID = 'EPSNodes.BroadcastSameNameInputs'

/** The multiplier's optional SWEEP inputs UE must never fill (S4). */
export const UE_REJECTED_INPUTS = Object.freeze(['model', 'clip', 'vae', 'label', 'model_low'])

/** One short debounce, shared by every trigger of the Keep-wired pass. */
const PASS_DEBOUNCE_MS = 150

/** How often (at most) `ensureWatch` re-walks the graph tree for subgraphs
 * that appeared; the cheap per-graph hook re-verify runs on every call. */
const WATCH_WALK_MIN_MS = 2000

const WATCH_HOOKS = ['onNodeAdded', 'onNodeRemoved', 'onAfterChange']

/** Multipliers attached on the page (a removed node leaves the set via its
 * `onRemoved`, or when its `graph` is null at the next pass). */
const multipliers = new Set()

// ---------------------------------------------------------------------------
// Setting
// ---------------------------------------------------------------------------

/** Re-reads the multiplier rows when the setting flips (their `needs setting`
 * hints and the live list depend on it). */
function onSettingChanged() {
  for (const node of multipliers) refreshRow(node)
}

/** Registered by `web/eps_image.js` through the extension's `settings`. */
export const SETTINGS = [
  {
    id: BROADCAST_SETTING_ID,
    category: ['EPSNodes', 'Run Multiplier', 'Broadcast'],
    name: 'Run Multiplier broadcast: also connect text, image and label to inputs with the same name',
    tooltip:
      'The EPS Run Multiplier’s Broadcast always handles model, clip, vae, model_low, ' +
      'save_prefix and run_info. With this ON it also connects its text, image and label outputs ' +
      '— but ONLY to an input with exactly the same name and type (a CLIP Text Encode’s ' +
      '“text” box, an Image Scale’s “image”). It never feeds a text box ' +
      'whose encoder goes to a sampler’s negative input. Off by default; it only changes what ' +
      'gets proposed — the wires it makes are ordinary wires. Saved per ComfyUI user, so turn it ' +
      'on once on each machine.',
    type: 'boolean',
    defaultValue: false,
    onChange: onSettingChanged
  }
]

/** The settings the planner needs, read fresh on every call (never cached:
 * the setting can flip at any time). A missing store reads as the default. */
export function readSettings() {
  let exactNames = false
  try {
    exactNames = app.extensionManager?.setting?.get?.(BROADCAST_SETTING_ID) === true
  } catch {
    exactNames = false
  }
  return { exactNames }
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

function nodeClassOf(node) {
  if (!node) return null
  if (node.comfyClass) return node.comfyClass
  if (node.constructor && node.constructor.comfyClass) return node.constructor.comfyClass
  return null
}

function toast(node, severity, summaryText, detail, life) {
  try {
    app.extensionManager?.toast?.add?.({
      severity,
      summary: summaryText || NODE_TITLE,
      detail,
      life: life ?? (severity === 'error' ? 8000 : 5000)
    })
  } catch (error) {
    console.warn(PREFIX, 'toast failed', error)
  }
}

function redraw(node) {
  try {
    node.graph?.setDirtyCanvas?.(true, true)
  } catch {
    // repaint is a nicety
  }
}

/** The input names currently wired on *node*. */
function wiredInputNames(node) {
  return new Set((node.inputs || []).filter((input) => input?.link != null).map((input) => input.name))
}

/** Which outputs are enabled AND live right now, from the live node only
 * (no snapshot: cheap enough for every refresh). */
function liveOutputNames(node, cfg, settings) {
  const wired = wiredInputNames(node)
  return BROADCAST_OUTPUTS.filter(
    (spec) => outputEnabled(cfg, spec, settings) && (!spec.liveInput || wired.has(spec.liveInput))
  ).map((spec) => spec.name)
}

/** `{model: true, vae: false, ...}` -- each output's liveness, for tracking
 * transitions between passes. */
function liveMap(node) {
  const wired = wiredInputNames(node)
  const map = {}
  for (const spec of BROADCAST_OUTPUTS) if (spec.liveInput) map[spec.name] = wired.has(spec.liveInput)
  return map
}

// ---------------------------------------------------------------------------
// The row (inside the readout element)
// ---------------------------------------------------------------------------

/** Repaints the row's text from the live node + records (cheap; change-gated
 * so a busy canvas never thrashes the DOM). */
export function refreshRow(node) {
  const state = node?.__epsBcState
  if (!state?.summaryEl) return
  try {
    const cfg = readConfig(node)
    const text = rowSummary({
      live: liveOutputNames(node, cfg, readSettings()),
      wired: cfg.wired.filter((entry) => !entry.withdrawn).length,
      paused: cfg.wired.filter((entry) => entry.withdrawn).length,
      keep: cfg.keep,
      scope: cfg.scope
    })
    if (text === state.lastRowText) return
    state.lastRowText = text
    state.summaryEl.textContent = text
    state.summaryEl.title = text
  } catch (error) {
    console.warn(PREFIX, 'row refresh failed', error)
  }
}

/** Grows the node to its content floor ONCE (an old workflow's saved size has
 * no room for the new row; a bigger size the user chose is never shrunk). */
function ensureNodeFloor(node) {
  try {
    if (typeof node.computeSize !== 'function' || typeof node.setSize !== 'function' || !node.size) return
    const floor = node.computeSize()[1]
    if (node.size[1] < floor) {
      node.setSize([node.size[0], floor])
      redraw(node)
    }
  } catch (error) {
    console.warn(PREFIX, 'node floor failed', error)
  }
}

function mountRow(state, readout) {
  const { node } = state
  const { rowEl, summaryEl } = buildBroadcastRow({
    onWire: () => wireNow(node),
    onMore: (anchorEl) => openOptions(node, anchorEl)
  })
  state.summaryEl = summaryEl
  readout.rootEl.classList.add('eps-rc-has-bc')
  readout.rootEl.appendChild(rowEl)
  // cross_sweep.js budgets `extraHeight` into every height it reports to
  // litegraph (sizeToContent + computeSize), so the row is never cropped.
  readout.extraHeight = BROADCAST_ROW_HEIGHT
  readout.outerHeight += BROADCAST_ROW_HEIGHT
  readout.rootEl.style.height = `${readout.textHeight + BROADCAST_ROW_HEIGHT}px`
  if (readout.domWidget) readout.domWidget.computedHeight = readout.outerHeight
  refreshRow(node)
}

// ---------------------------------------------------------------------------
// Actions: wire now / remove / toggles
// ---------------------------------------------------------------------------

/** Opens the preview for *node* (also the menu item). */
export function wireNow(node) {
  const { plan } = planFor(node, readSettings())
  openPreviewDialog({
    title: `Broadcast from ${node.title || NODE_TITLE} #${node.id}`,
    plan,
    onConnect: (outcome) => connectOutcome(node, plan, outcome)
  })
}

function connectOutcome(node, plan, outcome) {
  const rootGraph = rootGraphOf(node)
  const byKey = new Map([...plan.proposals, ...outcome.toApply].map((proposal) => [proposal.key, proposal]))
  const result = applyProposals(rootGraph, node, outcome.toApply, {
    leaveAlone: outcome.leaveAlone,
    include: outcome.include
  })
  const state = node.__epsBcState
  if (state) state.known = null // re-baseline: what exists NOW is not "new"
  refreshRow(node)
  schedulePass()
  if (result.applied.length) {
    const made = result.applied.reduce((n, a) => n + a.made, 0)
    toast(
      node,
      'success',
      `${NODE_TITLE}: connected ${result.applied.length}`,
      wiredToastText(result.applied, byKey) + (made ? ` — added ${made} subgraph input(s)` : '') + '. Ctrl+Z undoes the whole batch.'
    )
  } else if (outcome.leaveAlone.length) {
    toast(node, 'info', `${NODE_TITLE}: nothing connected`, `${outcome.leaveAlone.length} left alone.`)
  }
  if (result.failed.length) {
    toast(
      node,
      'warn',
      `${NODE_TITLE}: ${result.failed.length} could not be connected`,
      result.failed.map((f) => `${f.output}: ${f.reason}`).slice(0, 3).join('; '),
      9000
    )
  }
}

/** "Remove broadcast wires": only wires this multiplier recorded and that
 * still run from it (menu item + popover button). One undo step. */
export function removeWires(node) {
  const rootGraph = rootGraphOf(node)
  const result = removeBroadcastWires(rootGraph, node)
  const state = node.__epsBcState
  if (state) state.known = null
  refreshRow(node)
  redraw(node)
  if (result.removed === 0 && result.removedInputs === 0) {
    toast(node, 'info', `${NODE_TITLE}: no broadcast wires to remove`)
    return
  }
  const kept = result.keptInputs.length
    ? ` Kept subgraph input(s) ${result.keptInputs.map((n) => `"${n}"`).join(', ')} — something else still uses them.`
    : ''
  toast(
    node,
    'info',
    `${NODE_TITLE}: removed ${result.removed} broadcast wire(s)`,
    (result.removedInputs ? `Removed ${result.removedInputs} subgraph input(s) it had added. ` : '') + `Ctrl+Z brings them back.${kept}`
  )
}

/** Per-output toggle (popover). One undo step; the default (ON) stores nothing. */
export function setOutputEnabled(node, name, on) {
  runAsOneUndoStep(() => {
    const cfg = readConfig(node)
    if (on) delete cfg.outputs[name]
    else cfg.outputs[name] = false
    writeConfig(node, cfg)
  })
  refreshRow(node)
}

/** Keep wired on/off (popover + menu). Turning it ON re-baselines (existing
 * empty inputs are Wire now's job; Keep wired only handles what you add). */
export function setKeep(node, on) {
  runAsOneUndoStep(() => {
    const cfg = readConfig(node)
    cfg.keep = Boolean(on)
    writeConfig(node, cfg)
  })
  const state = node.__epsBcState
  if (state) {
    state.known = null
    state.liveSeen = liveMap(node)
  }
  refreshRow(node)
  schedulePass()
  toast(
    node,
    'info',
    on ? `${NODE_TITLE}: keep wired is ON` : `${NODE_TITLE}: keep wired is off`,
    on
      ? 'Nodes you add or paste from now on are wired automatically. A wire you unplug stays unplugged.'
      : 'Nothing is wired automatically any more. Existing broadcast wires stay.'
  )
}

/** Repaints the link layer (the canvas, not just this node: the wires are
 * drawn on the background layer). */
function repaintCanvas(node) {
  try {
    const canvas = app.canvas
    if (typeof canvas?.setDirty === 'function') canvas.setDirty(true, true)
    else node.graph?.setDirtyCanvas?.(true, true)
  } catch (error) {
    console.warn(PREFIX, 'repaint failed', error)
  }
}

/**
 * How broadcast wires are DRAWN (FORMAT.md §6.10 "Tucked wires"): `tucked`
 * (default: not drawn, a stub + 📡 at each fed input), `dim` or `normal`. One
 * undo step; the default stores nothing (an all-default config removes the
 * property). The link index caches each wire's owner AND its look, so the cache
 * is invalidated here -- without it the old look would keep drawing.
 */
export function setLook(node, look) {
  if (!LOOKS.includes(look)) return
  runAsOneUndoStep(() => {
    const cfg = readConfig(node)
    cfg.look = look
    writeConfig(node, cfg)
  })
  bumpBroadcastEpoch()
  ensureDrawHooks()
  refreshRow(node)
  repaintCanvas(node)
}

/**
 * Reach (FORMAT.md §6.10 "Reach"): `graph` (the whole workflow, default) or
 * `group` (only nodes inside a group that contains this multiplier). One undo
 * step. Wires already made are NEVER removed by changing Reach -- "Remove
 * broadcast wires" is the tool for that -- so a switch is always safe; it only
 * changes what Wire now / Keep wired will propose next. Toasts when "only my
 * group" has nothing in reach yet.
 */
export function setScope(node, scope) {
  if (!SCOPES.includes(scope)) return
  runAsOneUndoStep(() => {
    const cfg = readConfig(node)
    cfg.scope = scope
    writeConfig(node, cfg)
  })
  refreshRow(node)
  if (scope !== 'group') return
  const holding = groupsHolding(node)
  if (holding.length === 0) {
    toast(
      node,
      'warn',
      `${NODE_TITLE}: not inside a group`,
      'Reach is “only my group”, but this multiplier is not in a group, so nothing is in reach yet. ' +
        'Select it and the nodes it should feed and press Ctrl+G, or switch Reach back to the whole workflow.',
      9000
    )
  } else {
    toast(
      node,
      'info',
      `${NODE_TITLE}: reach is “only my group”`,
      `Broadcast now feeds only nodes inside ${holding.map((g) => `“${g.title || 'untitled group'}”`).join(', ')}. ` +
        'Wires already made stay. Wire now previews what would be added.'
    )
  }
}

function openOptions(node, anchorEl) {
  if (isPopoverOpenFor(anchorEl)) {
    closePopover() // the ⋯ button toggles
    return
  }
  const cfg = readConfig(node)
  const settings = readSettings()
  const wired = wiredInputNames(node)
  openPopover({
    anchorEl,
    outputs: BROADCAST_OUTPUTS.map((spec) => ({
      name: spec.name,
      enabled: cfg.outputs[spec.name] !== false,
      live: !spec.liveInput || wired.has(spec.liveInput),
      gated: spec.gated,
      settingOn: settings.exactNames
    })),
    keep: cfg.keep,
    look: cfg.look,
    scope: cfg.scope,
    onToggleOutput: (name, on) => setOutputEnabled(node, name, on),
    onToggleKeep: (on) => setKeep(node, on),
    onSetLook: (look) => setLook(node, look),
    onSetScope: (scope) => setScope(node, scope),
    onWire: () => wireNow(node),
    onRemove: () => removeWires(node)
  })
}

// ---------------------------------------------------------------------------
// Context menu (Nodes 2.0-safe: getNodeMenuItems, closures, no event/pos)
// ---------------------------------------------------------------------------

/** The three menu items (FORMAT.md §6.10). Closures over *node*; nothing here
 * reads an event or a position, so it works from Vue nodes' native menu. */
export function getNodeMenuItems(node) {
  if (nodeClassOf(node) !== MULTIPLIER_CLASS_ID) return []
  const keep = readConfig(node).keep
  return [
    { content: 'Broadcast: wire now…', callback: () => wireNow(node) },
    { content: 'Broadcast: remove broadcast wires', callback: () => removeWires(node) },
    { content: keep ? 'Broadcast: keep wired ✓' : 'Broadcast: keep wired', callback: () => setKeep(node, !readConfig(node).keep) }
  ]
}

const LEGACY_PATCH_FLAG = '__epsBroadcastMenuPatched'

/**
 * Fallback for frontends WITHOUT the declarative `getNodeMenuItems` hook (the
 * Photoshop pack's cpsb/menu.js pattern): the modern frontend invokes BOTH
 * `getExtraMenuOptions` and every extension's `getNodeMenuItems`, so
 * registering both would duplicate every item. `app.collectNodeMenuItems`
 * exists only on frontends that support the hook, which is the gate.
 */
export function installLegacyMenuFallback(nodeType, nodeData) {
  if (nodeData?.name !== MULTIPLIER_CLASS_ID) return
  if (typeof app.collectNodeMenuItems === 'function') return
  const proto = nodeType?.prototype
  if (!proto || proto[LEGACY_PATCH_FLAG]) return
  proto[LEGACY_PATCH_FLAG] = true
  const original = proto.getExtraMenuOptions
  proto.getExtraMenuOptions = function (canvas, options) {
    const result = original?.call(this, canvas, options)
    const items = getNodeMenuItems(this)
    if (items.length && Array.isArray(options)) {
      if (options.length) options.push(null)
      options.push(...items)
    }
    return result
  }
}

// ---------------------------------------------------------------------------
// Canvas context menu: "Broadcast: show all wires" (session-only)
// ---------------------------------------------------------------------------

/** The root graph the editor shows right now, or null. */
function currentRoot() {
  try {
    return app.rootGraph ?? app.graph?.rootGraph ?? app.graph ?? null
  } catch {
    return null
  }
}

/**
 * The canvas right-click item (extension hook `getCanvasMenuItems(canvas)`,
 * the Nodes 2.0-safe route: no event, no position, closures only). One item,
 * "Broadcast: show all wires" (`✓` suffix while on): the SESSION-ONLY switch
 * that draws every tucked wire (FORMAT.md §6.10 "Tucked wires"; never saved --
 * a fresh page tucks again). It is offered only while there is something to
 * show (a workflow with broadcast wires), or while the switch is already on,
 * so it can always be turned off again; every other canvas menu stays clean.
 * @param {object} [_canvas] the canvas the menu opened on (unused)
 */
export function getCanvasMenuItems(_canvas) {
  const on = getShowAllWires()
  const root = currentRoot()
  if (!on && !(root && broadcastLinkOwners(root).size > 0)) return []
  return [
    {
      content: on ? 'Broadcast: show all wires ✓' : 'Broadcast: show all wires',
      callback: () => setShowAllWires(!getShowAllWires())
    }
  ]
}

const LEGACY_CANVAS_PATCH_FLAG = '__epsBroadcastCanvasMenuPatched'

/**
 * Fallback for frontends WITHOUT the declarative `getCanvasMenuItems` hook --
 * the same pattern as `installLegacyMenuFallback` above and the Photoshop
 * pack's cpsb/menu.js: wrap `LGraphCanvas.prototype.getCanvasMenuOptions`.
 * `app.collectCanvasMenuItems` exists only on frontends that support the hook
 * (1.52.7's `useContextMenuTranslation` calls it AND the legacy wrapper, so
 * registering both would duplicate the item), which is the gate. Idempotent.
 * @param {Function} [canvasClass] defaults to the global `LGraphCanvas`
 * @returns {boolean} whether the wrapper is installed after the call
 */
export function installLegacyCanvasMenuFallback(canvasClass) {
  if (typeof app.collectCanvasMenuItems === 'function') return false
  const cls = canvasClass ?? (typeof LGraphCanvas === 'undefined' ? undefined : LGraphCanvas)
  const proto = cls?.prototype
  if (!proto || typeof proto.getCanvasMenuOptions !== 'function') return false
  if (proto[LEGACY_CANVAS_PATCH_FLAG]) return true
  proto[LEGACY_CANVAS_PATCH_FLAG] = true
  const original = proto.getCanvasMenuOptions
  proto.getCanvasMenuOptions = function () {
    const options = original.apply(this, arguments)
    try {
      const items = getCanvasMenuItems(this)
      if (items.length && Array.isArray(options)) {
        if (options.length) options.push(null)
        options.push(...items)
      }
    } catch (error) {
      console.warn(PREFIX, 'canvas menu item failed', error)
    }
    return options
  }
  return true
}

// ---------------------------------------------------------------------------
// Graph watch + the Keep-wired pass
// ---------------------------------------------------------------------------

let passTimer = null

/** Coalesces every trigger into ONE pass after a short debounce. */
export function schedulePass() {
  if (passTimer !== null) return
  passTimer = setTimeout(() => {
    passTimer = null
    try {
      runPass()
    } catch (error) {
      console.warn(PREFIX, 'pass failed', error)
    }
  }, PASS_DEBOUNCE_MS)
}

//: Owner key for api.watchGraphHooks (v1.3.0: the shared stored-and-
//: re-verified installer from the v1.2.0 nested-reach round -- wrappers carry
//: their siblings' owner keys, so the readout, controller, picker, Apply Set
//: and this watch re-verifying each other never stack layers).
const WATCH_KEY = '__epsBcNodeWatch'

function onWatchedGraphEvent(_graph, hook) {
  if (isApplying()) return
  if (hook === 'onNodeRemoved') bumpBroadcastEpoch()
  schedulePass()
}

function installWatchOn(graph) {
  watchGraphHooks(graph, WATCH_KEY, WATCH_HOOKS, onWatchedGraphEvent)
}

/** root graph -> `{graphs: Set, at: ms}`: the graphs the watch covers, per
 * root (a tab switch brings a new root; the old entry just goes unreferenced). */
const watched = new WeakMap()

/**
 * A cheap fingerprint of "did the graph STRUCTURE change": the per-graph
 * `_version` litegraph bumps on every node add/remove and link connect/
 * disconnect (and NOT on a node move). Every drag emits a canvas after-change;
 * without this each one would re-snapshot the whole workflow for a multiplier
 * that keeps wired. Returns null (= always process) when any graph lacks a
 * numeric `_version`, so a frontend that renames it degrades to "process every
 * time", never to "miss a change".
 */
function structureKey(root) {
  const entry = watched.get(root)
  const graphs = entry && entry.graphs.size ? [...entry.graphs] : [root]
  const parts = []
  for (const graph of graphs) {
    if (typeof graph?._version !== 'number') return null
    parts.push(graph._version)
  }
  return parts.join(',')
}

/**
 * (Re-)verifies the graph-watch hooks on the root graph and every subgraph.
 * The per-graph verify is three compares; the walk that discovers NEW
 * subgraphs is throttled (`force` skips the throttle: the Keep-wired pass does,
 * since it is already doing real work). Call it from anything that already runs
 * per change (cross_sweep.js's recompute does).
 */
export function ensureWatch(node, force = false) {
  const root = rootGraphOf(node)
  if (!root) return
  const now = Date.now()
  let entry = watched.get(root)
  if (!entry || force || now - entry.at > WATCH_WALK_MIN_MS) {
    entry = { graphs: new Set(walkGraphs(root)), at: now }
    watched.set(root, entry)
  }
  for (const graph of entry.graphs) installWatchOn(graph)
}

/** Whether *root* is still the graph the editor shows. A tab switch / undo /
 * reload configures a NEW node set into the same app; the old multipliers are
 * left behind without an `onRemoved`, and must not keep being planned. */
function isCurrentRoot(root) {
  try {
    const current = app.rootGraph ?? app.graph?.rootGraph ?? app.graph
    return !current || current === root
  } catch {
    return true
  }
}

function runPass() {
  if (isApplying()) return
  if (app.configuringGraph) return // a load / undo / tab switch: attach() schedules its own baseline
  ensureDrawHooks() // re-verify (the selection callback can be replaced by another extension)
  for (const node of [...multipliers]) {
    if (!node.graph || !isCurrentRoot(rootGraphOf(node))) {
      multipliers.delete(node)
      continue
    }
    try {
      processMultiplier(node)
    } catch (error) {
      console.warn(PREFIX, 'processing a multiplier failed', error)
    }
  }
}

function processMultiplier(node) {
  const state = node.__epsBcState
  if (!state) return
  const rootGraph = rootGraphOf(node)
  if (!rootGraph) return
  const first = state.known === null
  const cfg0 = readConfig(node)
  // Nothing recorded, not keeping: nothing to reconcile or track. (The cheap
  // exit that keeps a busy canvas free -- every drag emits an after-change.)
  if (!first && !cfg0.keep && cfg0.wired.length === 0) return
  // ...and an after-change that did not touch the structure (a node move)
  // has nothing new to reconcile, track or wire.
  const before = structureKey(rootGraph)
  if (!first && before !== null && before === state.structureKey) {
    state.stats.skipped += 1
    return
  }

  state.stats.snapshots += 1
  const snapshot = snapshotFromRoot(rootGraph)
  const pathId = pathIdOf(rootGraph, node)
  if (pathId === null) return
  // A real pass: re-walk the graph tree now (cheap next to the snapshot), so a
  // subgraph created since the last walk is hooked AND counted by the
  // structure fingerprint.
  ensureWatch(node, true)

  // 1. reconcile: load/paste (first pass) drops stale records quietly; in
  // session a wire the USER removed or replaced becomes "leave alone".
  const reconciled = reconcileMultiplier(rootGraph, node, { manual: !first }, snapshot, pathId)
  if (reconciled.leftAlone.length) {
    toast(node, 'info', `${NODE_TITLE}: leaving ${reconciled.leftAlone.length} alone`, 'You unplugged or replaced a broadcast wire, so it will not be re-wired.')
  }
  // 1b. the negative-prompt guard, continuously (v1.3.0 rig finding): a
  // `text` wire made into an encoder that has SINCE been wired into a
  // sampler's `negative` is withdrawn and left alone -- Keep wired can only
  // see a negative link that already exists when it wires a new node.
  const unNegated = enforceNegativeGuard(rootGraph, node, snapshot)
  if (unNegated.length) {
    toast(node, 'info', `${NODE_TITLE}: took text off ${unNegated.length} negative prompt(s)`, 'That CLIP Text Encode now feeds a negative input, so the multiplier\'s text is no longer wired into it (it keeps its own text).')
  }
  const cfg = readConfig(node)
  const nodePaths = allNodePaths(snapshot)

  if (first) {
    state.known = nodePaths
    state.liveSeen = liveMap(node)
    state.structureKey = structureKey(rootGraph)
    refreshRow(node)
    return
  }

  // 2. live-output tracking (Keep wired only)
  let rewired = false
  if (cfg.keep) {
    const now = liveMap(node)
    for (const spec of BROADCAST_OUTPUTS) {
      if (!spec.liveInput) continue
      const was = state.liveSeen?.[spec.name]
      if (was === true && now[spec.name] === false) {
        const n = withdrawOutputWires(rootGraph, node, spec.name)
        if (n > 0) {
          rewired = true
          toast(node, 'info', `${NODE_TITLE}: paused ${n} ${spec.name} wire(s)`, `The ${spec.liveInput} input is unwired, so ${spec.name} is not live. Rewire it and they come back.`)
        }
      } else if (was === false && now[spec.name] === true) {
        const r = restoreOutputWires(rootGraph, node, spec.name)
        if (r.restored > 0) {
          rewired = true
          toast(node, 'info', `${NODE_TITLE}: restored ${r.restored} ${spec.name} wire(s)`, `${spec.liveInput} is wired again.`)
        }
      }
    }
  }
  state.liveSeen = liveMap(node)

  // 3. wire NEW nodes (Keep wired only)
  if (cfg.keep) {
    const fresh = new Set([...nodePaths].filter((path) => !state.known.has(path)))
    if (fresh.size > 0) {
      const plan = rewired
        ? planFor(node, readSettings()).plan
        : planBroadcast(snapshot, pathId, readConfig(node), readSettings())
      const auto = plan.proposals.filter(
        (p) => (p.kind === KINDS.DIRECT || p.kind === KINDS.EXISTING) && fresh.has(p.targetPathId)
      )
      const nested = plan.proposals.filter(
        (p) => p.kind === KINDS.NEW && p.reaches.some((reach) => reach.pathIds.some((path) => fresh.has(path)))
      )
      if (auto.length) {
        const result = applyProposals(rootGraph, node, auto)
        if (result.applied.length) {
          const byKey = new Map(auto.map((p) => [p.key, p]))
          toast(node, 'success', `${NODE_TITLE} wired ${wiredToastText(result.applied, byKey)}`, 'Keep wired. Ctrl+Z undoes it; unplug a wire and it stays unplugged.')
        }
        if (result.failed.length) {
          toast(node, 'warn', `${NODE_TITLE}: ${result.failed.length} could not be connected`, result.failed.map((f) => `${f.output}: ${f.reason}`).slice(0, 3).join('; '), 9000)
        }
      }
      if (nested.length) {
        toast(node, 'info', `${NODE_TITLE}: ${nested.length} target(s) inside a subgraph`, 'Adding an input to a subgraph is never automatic — open Wire now to review it.')
      }
      const contested = plan.conflicts.filter((c) => fresh.has(c.targetPathId))
      if (contested.length) {
        toast(
          node,
          'info',
          `${NODE_TITLE}: ${contested.length} new input(s) left unwired`,
          'Another EPS Run Multiplier could feed the same input, so neither does. Switch that output off on one of them, or wire it by hand.'
        )
      }
    }
  }
  state.known = nodePaths
  // Fingerprint AFTER our own wiring, so our own changes never re-trigger us.
  state.structureKey = structureKey(rootGraph)
  refreshRow(node)
}

// ---------------------------------------------------------------------------
// Entry points
// ---------------------------------------------------------------------------

let initialised = false

/** One-time, page-level: the single canvas after-change listener. */
export function init() {
  // Idempotent and cheap, so it also runs when init() itself is called again:
  // the canvas may not have existed the first time (older frontends create it
  // after `init`), and `setup()` / every multiplier's `attach` call it too.
  ensureDrawHooks()
  installLegacyCanvasMenuFallback()
  if (initialised) return
  initialised = true
  if (typeof document === 'undefined') return
  // Capture phase on `document`: the canvas dispatches `litegraph:canvas`
  // from its <canvas> element with bubbles:true; capture sees it first and
  // can never be starved by a stopPropagation further down (§7.5 / Vue mode).
  document.addEventListener(
    'litegraph:canvas',
    (event) => {
      if (event?.detail?.subType === 'after-change' && !isApplying()) schedulePass()
    },
    true
  )
}

/** Extension `setup()` (the canvas certainly exists): (re)installs the link
 * drawing hooks and the legacy canvas-menu fallback. Idempotent. */
export function setup() {
  ensureDrawHooks()
  installLegacyCanvasMenuFallback()
}

/**
 * Extension `afterConfigureGraph()`: a whole workflow was just loaded (open,
 * undo/redo, tab switch, drop). The link index is cached per ROOT graph object,
 * and ComfyUI re-configures the SAME root graph for a new workflow while link
 * ids restart at 1 -- so a workflow with no multiplier at all (nothing here
 * would otherwise be told) must still drop the previous workflow's cached
 * records, or a stale "link 7 is a broadcast wire" would TUCK an innocent wire
 * of the new workflow. A multiplier's own `onConfigure` already bumps; this
 * covers the graphs that have none.
 */
export function afterConfigure() {
  bumpBroadcastEpoch()
  ensureDrawHooks()
  try {
    app.canvas?.setDirty?.(true, true)
  } catch (error) {
    console.warn(PREFIX, 'repaint failed', error)
  }
}

/**
 * Per-multiplier attach (called from cross_sweep.js's `attach` after the
 * readout exists). Never throws out: a failure leaves a plain, working
 * multiplier with the readout alone.
 * @param {object} node - the EPSCrossSweep litegraph node
 * @param {object} readout - cross_sweep.js's readout `state` (rootEl,
 *   textHeight, outerHeight, domWidget, extraHeight)
 */
export function attach(node, readout) {
  try {
    if (!node || node.__epsBcState) return
    // `stats`: how many full snapshots this multiplier's passes took vs skipped
    // because the structure had not changed (read by the tests).
    const state = {
      node,
      known: null,
      liveSeen: null,
      structureKey: null,
      summaryEl: null,
      lastRowText: null,
      stats: { snapshots: 0, skipped: 0 }
    }
    node.__epsBcState = state

    // S4: UE's documented veto hook -- defined first, it needs no DOM.
    node.reject_ue_connection = function (input) {
      return UE_REJECTED_INPUTS.includes(input?.name)
    }

    multipliers.add(node)
    ensureDrawHooks() // a multiplier exists: the canvas does too
    if (readout?.rootEl) mountRow(state, readout)

    const originalConnections = node.onConnectionsChange
    node.onConnectionsChange = function () {
      const result = typeof originalConnections === 'function' ? originalConnections.apply(this, arguments) : undefined
      try {
        if (!isApplying() && !app.configuringGraph) {
          refreshRow(this)
          schedulePass()
        }
      } catch (error) {
        console.warn(PREFIX, 'connection hook failed', error)
      }
      return result
    }

    const originalConfigure = node.onConfigure
    node.onConfigure = function () {
      const result = typeof originalConfigure === 'function' ? originalConfigure.apply(this, arguments) : undefined
      try {
        // A whole-graph load / undo / paste: records may be stale, the
        // baseline is gone -- re-derive, never rewire (loading never does).
        bumpBroadcastEpoch()
        state.known = null
        refreshRow(this)
        ensureNodeFloor(this)
        schedulePass()
      } catch (error) {
        console.warn(PREFIX, 'post-configure failed', error)
      }
      return result
    }

    const originalRemoved = node.onRemoved
    node.onRemoved = function () {
      multipliers.delete(node)
      bumpBroadcastEpoch()
      closePopover()
      closeDialog()
      return typeof originalRemoved === 'function' ? originalRemoved.apply(this, arguments) : undefined
    }

    ensureNodeFloor(node)
    schedulePass()
  } catch (error) {
    console.warn(PREFIX, 'broadcast attach failed', error)
  }
}

/** Every multiplier under the root graph (for tests and the rendering stage). */
export { findMultipliers }
