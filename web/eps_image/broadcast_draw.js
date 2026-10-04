/**
 * @file EPS Run Multiplier "broadcast" -- the RENDERING stage (FORMAT.md §6.10
 * "Tucked wires", plan `research/roadmap-eps-broadcast.md` §1a and M3, owner
 * ask 2026-10-03: "get rid of the spaghetti" without giving up real wires).
 *
 * **What it does.** Broadcast makes REAL wires (owner decision 2026-10-03), so
 * a busy workflow would grow one wire per target. By default (`look:
 * 'tucked'`) those wires are simply NOT DRAWN: the link still exists, so
 * execution, save, undo, the run-count readout, Bypass, the Distributor, Image
 * Grid Collect-only and Save Image's baked workflow are all unaffected -- only
 * the pixels change. Each fed input gets a short stub and a small 📡 marker so
 * it stays legible. Three things bring a wire back: the owning multiplier is
 * selected, the wire's TARGET node is selected, or the session-only "Show all
 * broadcast wires" switch is on (canvas right-click menu).
 *
 * **Why this is Nodes 2.0-safe (plan §1a, frontend-contract §1 and §2.4).**
 * Nodes 2.0 replaces how NODES are drawn. Links are still drawn by the classic
 * canvas in BOTH renderers: `LGraphCanvas.drawConnections` ->
 * `_renderAllLinkSegments` -> `renderLink` (`LinkOverlayCanvas` is only the
 * drag-preview layer). So the one thing hooked here is the LINK-level
 * `LGraphCanvas.prototype.renderLink` -- no `onDrawForeground`, no node mouse
 * hook, no per-node DOM listener, no right-click > Properties. (This is why
 * the drawing lives in its own module: the pin tests keep `broadcast.js` and
 * `broadcast_ui.js` free of any `ctx.` use.) Checked against the extracted
 * frontend source for 1.52.7, 1.53.10 and 1.54.12: `renderLink(ctx, a, b,
 * link, skip_border, flow, color, start_dir, end_dir, {startControl,
 * endControl, reroute, num_sublines, disabled})` is identical in all three and
 * has had the same first four parameters since the original litegraph.js. This
 * module reads only `link` (and `b`, `flow`, `end_dir`, `reroute` for the stub).
 *
 * **The hook (install once, never throws).** `installRenderLinkHook` wraps the
 * prototype's `renderLink` ONCE (idempotent: a flag on our wrapper and a
 * WeakSet of prototypes), only when `renderLink` exists (feature-detected: a
 * frontend that renames it simply shows the wires -- the canvas gets busy, but
 * nothing breaks). The wrapper catches EVERYTHING of ours, warns once per
 * cause and falls through to the original call, so a bug here can only ever
 * mean "the wire is drawn". Errors from core's own drawing are not swallowed.
 *
 * **Per link per frame must be O(1).** `broadcastLinkOwner(graph, linkId)`
 * (broadcast_graph.js) is two Map lookups against an index cached per root
 * graph until `bumpBroadcastEpoch()`; a workflow with no broadcast records
 * short-circuits on a shared empty index. The reveal test is a property lookup
 * on `canvas.selected_nodes`. Nothing here polls, allocates per frame on the
 * non-broadcast path, or walks the graph.
 *
 * **Reveal.** Selection is read from `canvas.selected_nodes` (litegraph's
 * long-standing id -> node dictionary). Verified in the 1.52.7 source that
 * Vue-mode selection is mirrored there: the Vue node handlers
 * (`useNodeEventHandlers.ts`) call `canvas.select(node)` / `deselect(node)` /
 * `deselectAll()`, and `select`/`deselect` write `selected_nodes[item.id]`
 * (LGraphCanvas.ts, `select()` / `deselect()`), while `deselectAll` resets it.
 * One gap found in the same read: selecting a GROUP with "select group
 * children" marks its child nodes `selected = true` (and adds them to
 * `selectedItems`) without writing them into `selected_nodes`, so the per-node
 * `selected` flag is read too (`isNodeSelected`). The multiplier counts only
 * when it lives in the SAME graph the link is drawn in: node ids are unique per
 * graph, so inside a subgraph a same-numbered inner node must never read as
 * "the multiplier is selected".
 *
 * **Repainting on selection (no polling).** The wires are drawn on the BACKGROUND
 * canvas, and a node click only marks the foreground dirty (`processSelect` ->
 * `setDirty(true)`), so a tucked wire would not reappear until something else
 * repainted. `canvas.onSelectionChange` alone is NOT enough on 1.52.7's Vue
 * mode (found by reading the source, rig-UNCONFIRMED): `handleNodeSelect`
 * does `deselectAll()` then `select(node)`, and `deselectAll()` returns early
 * -- without firing `onSelectionChange` -- when nothing was selected, while
 * `select()`/`deselect()` never fire it themselves (the Vue handlers instead
 * call the Pinia store's `updateSelectedItems()`, which an extension cannot
 * reach). So the repaint is hung on the three canvas methods that mutate the
 * selection -- `select`, `deselect`, `deselectAll` (feature-detected, idempotent,
 * each calls the original FIRST) -- plus a chained `onSelectionChange` for older
 * frontends that lack those names. They call `setDirty(true, true)`, which only
 * sets two flags; the draw happens on the next frame, after the whole click has
 * settled, so firing early (at `deselectAll`, before `select`) is harmless.
 *
 * **Hit-testing stays sane.** The canvas hit-tests a link through
 * `canvas.renderedPaths` (every link `_renderAllLinkSegments` visited, drawn or
 * not) using `link._pos` (the centre marker's click target: opens the link menu
 * and starts a drag) and `link.path` (the `isPointInStroke` fallback used by
 * shift/alt-click). Skipping the draw would leave BOTH stale -- worse, a link
 * never drawn since load still has the constructor's `_pos = [0, 0]`, a click
 * target at graph (0, 0). So a tucked wire's `_pos` is set to NaN in place
 * (every `isInRectangle` against NaN is false, and the array stays a valid
 * `Point`, unlike `undefined`) and `path` is cleared; the next real draw
 * rewrites both. (1.52.7 builds its link renderer with
 * `LitegraphLinkAdapter(false)`, so the layout store holds no link layouts to
 * go stale -- `_pos` and `path` are the whole hit-test state.) The stub is
 * decoration only: it is never added to `renderedPaths`, so nothing about a
 * normal link's hit-testing changes.
 *
 * **Dim look.** Scales `ctx.globalAlpha` around the original call and restores
 * it in a `finally`. It deliberately does NOT write `link.color`: that property
 * is not serialized, and Distributor, Image Grid and Bypass own it through
 * `LINK_COLOR_OWNER_KEY` / `LINK_COLOR_RESYNC_HOOK` (distributor.js). This
 * module never writes any link property except the hit-test fields above on a
 * link it is hiding.
 */

import { app } from '../../../scripts/app.js'
import { broadcastLinkOwner, broadcastLinkOwners } from './broadcast_graph.js'

const PREFIX = '[eps_image:broadcast]'

/** Alpha of the dim look (FORMAT.md §6.10: "about 0.25"). */
export const DIM_ALPHA = 0.25

/** Length of the tucked stub in graph px (FORMAT.md §6.10: "about 16-20"). */
export const STUB_LENGTH = 18

/** The marker drawn at the stub's end. */
export const STUB_MARKER = '📡'

/** litegraph's `LinkDirection` (globalEnums.ts 1.52.7; the original
 * litegraph.js `LiteGraph.UP/DOWN/LEFT/RIGHT` have the same values). */
const DIRECTION = Object.freeze({ UP: 1, DOWN: 2, LEFT: 3, RIGHT: 4 })

// ---------------------------------------------------------------------------
// FUTURE NATIVE ROUTE -- the one hook point (deliberately NOT implemented)
// ---------------------------------------------------------------------------

/**
 * Core's own hidden-link badges (ComfyUI frontend >= 1.55.9) do what the
 * tucked look does natively. They are NOT in this repo's rig frontend
 * (1.52.7) nor in 1.53.10 / 1.54.12, so nothing here uses them, and this
 * returns false until a frontend that ships them is on the bench.
 *
 * When that day comes: detect the feature HERE (e.g. a hidden-link flag on
 * the link, or `LGraphCanvas.prototype.<hiddenLinkApi>`), make it return true,
 * and `installRenderLinkHook` below stands down so core draws its own badge --
 * then set that flag from the multiplier's records instead of wrapping
 * `renderLink`, and drop this module's hook. FORMAT.md §6.10 "Tucked wires"
 * records the same plan.
 */
export function nativeHiddenLinksAvailable() {
  return false
}

// ---------------------------------------------------------------------------
// Pure decisions (unit-tested without a canvas)
// ---------------------------------------------------------------------------

/**
 * What to do with one link: `{mode, draw, alpha, stub}`.
 *  - not a broadcast wire, `normal` look, or REVEALED -> `normal` (draw as core does);
 *  - `dim`    -> draw it with `alpha` (DIM_ALPHA) scaled onto the context;
 *  - `tucked` -> do not draw it, draw a stub + marker at its input end instead.
 * An unrecognised look falls back to `normal`: the wire shows (fail safe).
 * @param {{isBroadcast: boolean, look: string, revealed: boolean}} input
 * @returns {{mode: 'normal'|'dim'|'tucked', draw: boolean, alpha: number, stub: boolean}}
 */
export function broadcastDrawPlan({ isBroadcast, look, revealed }) {
  if (!isBroadcast || revealed || (look !== 'tucked' && look !== 'dim')) {
    return { mode: 'normal', draw: true, alpha: 1, stub: false }
  }
  if (look === 'dim') return { mode: 'dim', draw: true, alpha: DIM_ALPHA, stub: false }
  return { mode: 'tucked', draw: false, alpha: 1, stub: true }
}

/** Whether the wire itself is drawn at all (the boolean face of
 * `broadcastDrawPlan`). */
export function shouldDrawBroadcastLink(input) {
  return broadcastDrawPlan(input).draw
}

const hasOwn = (object, key) =>
  object !== null &&
  object !== undefined &&
  key !== null &&
  key !== undefined &&
  Object.hasOwn(object, key) &&
  object[key] != null

/**
 * Whether the node with *id* is selected: in the `selected_nodes` dictionary
 * (every ordinary click, marquee and Ctrl+A, in both renderers) OR flagged
 * `node.selected` on the node itself. The second source matters: selecting a
 * GROUP (title bar) with "select group children" on marks the child NODES
 * `selected = true` and adds them to `selectedItems` WITHOUT writing them into
 * the deprecated `selected_nodes` dictionary (LGraphCanvas.ts `select()`'s
 * group branch, 1.52.7), so the dictionary alone would miss a multiplier that
 * was selected through its group. `getNodeById` is an O(1) id lookup.
 */
function isNodeSelected(selectedNodes, graph, id) {
  if (id === null || id === undefined) return false
  if (hasOwn(selectedNodes, id)) return true
  return graph?.getNodeById?.(id)?.selected === true
}

/**
 * Why a broadcast wire is shown right now, or null when it is tucked:
 * `'all'` (the session switch), `'target'` (the wire's target node is selected)
 * or `'owner'` (the multiplier is selected AND lives in the graph being drawn).
 * `selectedNodes` is `canvas.selected_nodes` (id -> node); `graph` is the graph
 * being drawn (for the per-node `selected` flag, see `isNodeSelected`).
 * @param {{showAll: boolean, selectedNodes: object|null|undefined, graph?: object|null,
 *   targetId: unknown, ownerId: unknown, ownerInThisGraph: boolean}} input
 * @returns {'all'|'target'|'owner'|null}
 */
export function revealedBy({ showAll, selectedNodes, graph, targetId, ownerId, ownerInThisGraph }) {
  if (showAll) return 'all'
  if (isNodeSelected(selectedNodes, graph, targetId)) return 'target'
  if (ownerInThisGraph && isNodeSelected(selectedNodes, graph, ownerId)) return 'owner'
  return null
}

/**
 * The stub's geometry: from the input slot point *b* straight back along the
 * direction the wire arrives from (`end_dir`: LEFT for an ordinary input), for
 * *length* graph px, with the marker centred just past the stub's end. CENTER /
 * NONE / unknown directions use LEFT, as core's own `end_dir || LEFT` does.
 * @param {ArrayLike<number>} b @param {number} endDir @param {number} [length]
 * @returns {{from: [number, number], to: [number, number], marker: [number, number]}}
 */
export function stubGeometry(b, endDir, length = STUB_LENGTH) {
  let dx = -1
  let dy = 0
  if (endDir === DIRECTION.RIGHT) dx = 1
  else if (endDir === DIRECTION.UP) [dx, dy] = [0, -1]
  else if (endDir === DIRECTION.DOWN) [dx, dy] = [0, 1]
  const to = [b[0] + dx * length, b[1] + dy * length]
  const reach = 5 // the marker sits just beyond the stub's end
  return { from: [b[0], b[1]], to, marker: [to[0] + dx * reach, to[1] + dy * reach] }
}

// ---------------------------------------------------------------------------
// Session state: "Show all broadcast wires"
// ---------------------------------------------------------------------------

let showAllWires = false

/** The session-only "Show all broadcast wires" switch (never saved: a fresh
 * page load tucks again). */
export function getShowAllWires() {
  return showAllWires
}

/** Turns the session switch on/off and repaints the link layer. */
export function setShowAllWires(on) {
  showAllWires = Boolean(on)
  repaintLinks(activeCanvas())
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

const warned = new Set()

/** console.warn once per cause: this runs inside the render loop. */
function warnOnce(tag, error) {
  if (warned.has(tag)) return
  warned.add(tag)
  console.warn(PREFIX, `broadcast drawing (${tag}) failed -- the wire is drawn normally`, error)
}

function activeCanvas() {
  try {
    return app?.canvas ?? null
  } catch {
    return null
  }
}

/** Marks the link layer (background) and the foreground dirty. Only sets two
 * flags -- the draw happens on the next frame. */
function repaintLinks(canvas) {
  try {
    canvas?.setDirty?.(true, true)
  } catch (error) {
    warnOnce('repaint', error)
  }
}

/** The LGraphCanvas class: the global `LGraphCanvas` (set by 1.52.7's
 * `useGlobalLitegraph`, and by litegraph.js before it), else the live canvas's
 * constructor. */
function canvasClass() {
  const global = typeof LGraphCanvas === 'undefined' ? undefined : LGraphCanvas
  if (typeof global === 'function' && global.prototype) return global
  const ctor = activeCanvas()?.constructor
  return typeof ctor === 'function' && ctor !== Object && ctor.prototype ? ctor : null
}

// ---------------------------------------------------------------------------
// The decision for one renderLink call
// ---------------------------------------------------------------------------

/** The drawing decision for *link* on *canvas*, or null for "not ours, draw it
 * exactly as core does". O(1): two Map lookups, then a property lookup. */
function decideDraw(canvas, link) {
  if (!link || link.id == null) return null // the drag preview passes null
  const owner = broadcastLinkOwner(canvas?.graph, link.id)
  if (owner === null || owner.look === 'normal') return null
  const reason = revealedBy({
    showAll: showAllWires,
    selectedNodes: canvas.selected_nodes,
    graph: canvas.graph,
    targetId: link.target_id,
    ownerId: owner.ownerId,
    ownerInThisGraph: owner.ownerGraph === owner.linkGraph
  })
  return broadcastDrawPlan({ isBroadcast: true, look: owner.look, revealed: reason !== null })
}

/** Stops the canvas from treating a link it did not draw as a click target
 * (see "Hit-testing stays sane" in the file header). */
function hideFromHitTest(segment) {
  if (!segment) return
  const pos = segment._pos
  if (pos && typeof pos.length === 'number' && pos.length >= 2) {
    pos[0] = Number.NaN
    pos[1] = Number.NaN
  }
  if (segment.path !== undefined) segment.path = undefined
}

/** `link.color`, else the type colour, else the canvas default -- core's own
 * precedence (the Reroute colour line in `_renderAllLinkSegments`). Read-only:
 * `link.color` belongs to Distributor / Image Grid / Bypass. */
function stubColour(canvas, link) {
  const types = canvas?.constructor?.link_type_colors
  return link?.color || types?.[link?.type] || canvas?.default_link_color || '#9A9'
}

/** Draws the stub + marker at the input end *b*. Never leaves context state
 * changed (save/restore). */
function drawStub(canvas, ctx, b, link, endDir) {
  const { from, to, marker } = stubGeometry(b, endDir)
  const colour = stubColour(canvas, link)
  ctx.save()
  try {
    ctx.strokeStyle = colour
    ctx.fillStyle = colour
    ctx.lineWidth = Math.max(2, Number(canvas?.connections_width) || 3)
    ctx.lineCap = 'round'
    ctx.beginPath()
    ctx.moveTo(from[0], from[1])
    ctx.lineTo(to[0], to[1])
    ctx.stroke()
    if (canvas?.low_quality === true) {
      // Zoomed far out: a dot is all there is room for (and fillText is the
      // expensive call on a big graph).
      ctx.beginPath()
      ctx.arc(to[0], to[1], 3, 0, Math.PI * 2)
      ctx.fill()
    } else {
      ctx.font = '11px sans-serif'
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText(STUB_MARKER, marker[0], marker[1])
    }
  } finally {
    ctx.restore()
  }
}

/**
 * The tucked branch: draws nothing for the wire, a stub for the INPUT end. A
 * wire with reroutes is drawn as several `renderLink` calls (one per segment,
 * the reroute segments carry `extras.reroute`, the last one ends at the real
 * input), and the "event flash" is a second call with `flow` set -- so the stub
 * is drawn only on the final, non-flow call and every segment is skipped.
 * Returns true when the call was handled (the original must not run).
 */
function tuckLink(canvas, ctx, b, link, flow, endDir, extras) {
  hideFromHitTest(extras?.reroute ?? link)
  if (!flow && !extras?.reroute && !link._dragging) drawStub(canvas, ctx, b, link, endDir)
  return true
}

/** Runs *original* with the context alpha scaled by *alpha*; restores it even
 * when core's drawing throws (and lets that error propagate untouched). */
function drawDimmed(original, canvas, args, ctx, alpha) {
  const saved = ctx.globalAlpha
  let scaled = false
  try {
    ctx.globalAlpha = saved * alpha
    scaled = true
  } catch (error) {
    warnOnce('dim', error)
  }
  try {
    return original.apply(canvas, args)
  } finally {
    if (scaled) ctx.globalAlpha = saved
  }
}

// ---------------------------------------------------------------------------
// Installing the hooks (idempotent, feature-detected, never throws)
// ---------------------------------------------------------------------------

const RENDER_HOOK_FLAG = '__epsBcRenderLink'
const REPAINT_FLAG = '__epsBcSelectionRepaint'
const SELECTION_METHODS = Object.freeze(['select', 'deselect', 'deselectAll'])

/** Prototypes whose `renderLink` we have wrapped (so a later wrapper by
 * another extension, which hides our flag, never makes us wrap twice). */
const hooked = new WeakSet()

/**
 * Wraps `proto.renderLink` ONCE. Returns true when the hook is (already) in
 * place, false when it deliberately is not (no `renderLink` -- the wires just
 * show; or the future native route took over).
 * @param {object} [proto] defaults to the LGraphCanvas prototype
 */
export function installRenderLinkHook(proto = canvasClass()?.prototype) {
  try {
    if (nativeHiddenLinksAvailable()) return false
    if (!proto || typeof proto.renderLink !== 'function') return false
    if (proto.renderLink[RENDER_HOOK_FLAG] === true || hooked.has(proto)) return true
    const original = proto.renderLink
    const broadcastRenderLink = function (ctx, a, b, link, skipBorder, flow, color, startDir, endDir, extras) {
      let decision = null
      try {
        decision = decideDraw(this, link)
      } catch (error) {
        warnOnce('decide', error)
      }
      if (decision === null || decision.mode === 'normal') return original.apply(this, arguments)
      if (decision.mode === 'dim') return drawDimmed(original, this, arguments, ctx, decision.alpha)
      let handled = false
      try {
        handled = tuckLink(this, ctx, b, link, flow, endDir, extras)
      } catch (error) {
        warnOnce('tuck', error)
      }
      return handled ? undefined : original.apply(this, arguments)
    }
    broadcastRenderLink[RENDER_HOOK_FLAG] = true
    proto.renderLink = broadcastRenderLink
    hooked.add(proto)
    return true
  } catch (error) {
    console.warn(PREFIX, 'renderLink hook not installed -- broadcast wires will simply be drawn', error)
    return false
  }
}

/** Repaint after a selection change, but only when there is something to
 * un/tuck (an ordinary workflow pays nothing). */
function repaintForSelection(canvas) {
  try {
    if (showAllWires) return // everything is shown already: a selection changes nothing
    const root = canvas?.graph?.rootGraph || canvas?.graph
    if (root && broadcastLinkOwners(root).size > 0) repaintLinks(canvas)
  } catch (error) {
    warnOnce('selection', error)
  }
}

/**
 * Hangs the selection repaint on the canvas methods that mutate the selection
 * (see "Repainting on selection" in the file header). Each wrapper calls the
 * original FIRST and never changes its result. Idempotent per method; absent
 * methods (an older frontend) are skipped.
 * @param {object} [proto]
 * @returns {boolean} whether any method is hooked after the call
 */
export function installSelectionRepaint(proto = canvasClass()?.prototype) {
  if (!proto) return false
  let any = false
  for (const name of SELECTION_METHODS) {
    const original = proto[name]
    if (typeof original !== 'function') continue
    if (original[REPAINT_FLAG] === true) {
      any = true
      continue
    }
    const wrapper = function (...args) {
      const result = original.apply(this, args)
      repaintForSelection(this)
      return result
    }
    wrapper[REPAINT_FLAG] = true
    proto[name] = wrapper
    any = true
  }
  return any
}

/**
 * The same repaint through `canvas.onSelectionChange` (a chained callback, the
 * way core's own GraphCanvas.vue chains it) for frontends without the methods
 * above. Re-verified on every call: if something replaced the callback without
 * chaining, it is wrapped again.
 * @param {object} [canvas]
 */
export function installSelectionCallback(canvas = activeCanvas()) {
  if (!canvas) return false
  const current = canvas.onSelectionChange
  if (typeof current === 'function' && current[REPAINT_FLAG] === true) return true
  const chained = function (...args) {
    const result = typeof current === 'function' ? current.apply(this, args) : undefined
    repaintForSelection(canvas)
    return result
  }
  chained[REPAINT_FLAG] = true
  canvas.onSelectionChange = chained
  return true
}

/**
 * Installs every drawing hook (renderLink wrapper, selection repaint) where
 * possible. Safe to call as often as you like -- from `init`, `setup`, a
 * multiplier's `attach` and the graph watch's re-verify; each part is
 * idempotent. Returns what ended up in place.
 * @returns {{renderLink: boolean, selection: boolean}}
 */
export function ensureDrawHooks() {
  const proto = canvasClass()?.prototype
  const result = { renderLink: false, selection: false }
  try {
    result.renderLink = installRenderLinkHook(proto)
    const methods = installSelectionRepaint(proto)
    const callback = installSelectionCallback()
    result.selection = methods || callback
  } catch (error) {
    console.warn(PREFIX, 'draw hooks failed -- broadcast wires will simply be drawn', error)
  }
  return result
}
