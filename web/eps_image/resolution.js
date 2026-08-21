/**
 * @file EPS Resolution frontend (FORMAT.md §6.5). M1 = hideable outputs.
 * M2 adds the size-grid DOM widget and flips both hideable-output
 * properties' default to OFF. M3 (this round) adds server-side size
 * PRESETS -- a `preset` combo + Save/Delete buttons over the backend's
 * hidden `presets` JSON-array-of-names widget, built against the parallel
 * backend contract in `eps_image/nodes_resolution.py`/
 * `routes_resolution_presets.py`. See the "M3: size presets" section
 * (search for that heading) for the full design, including a verified,
 * deliberate divergence from the literal brief's widget ordering (a
 * correctness constraint, not a style choice) and the ContextMenu/Vue-mode
 * verification trail.
 *
 * ---- Hideable outputs: how, and why it's two different mechanisms ----
 *
 * FORMAT.md §6.5 says "Frontend does the hide (litegraph output `hidden`
 * flag)". VERIFIED against the frontend source checked out at
 * `.../scratchpad/ComfyUI_frontend` and its extracted litegraph types
 * (`LGraphNode.ts`, `LGraphCanvas.ts`): there is NO such flag. Widget
 * *inputs* have a real, load-bearing `.hidden` (filtered by
 * `isWidgetVisible()` in `computeSize()`/`_arrangeWidgets()`,
 * LGraphNode.ts ~3935-3946), but plain OUTPUT slots have no equivalent —
 * `drawSlots()` (LGraphNode.ts ~4107-4137) draws every entry of
 * `_concreteOutputs` unconditionally (the visibility gate there is only
 * about *widget-input* slots), and `computeSize()`'s row count
 * (`Math.max(inputs..., outputs.length, 1)`, ~1758-1761) counts every
 * output with no hidden-filter either. So a bare `.hidden = true` on an
 * output slot would do nothing.
 *
 * The only way to genuinely remove an output's row is `LGraphNode.
 * removeOutput(slot)` / `addOutput(name, type)` (LGraphNode.ts ~1622-1685) —
 * the same category of technique FORMAT.md §6.4 already sanctions for EPS
 * Switcher's growing INPUT sockets. But it comes with a sharp constraint for
 * a real (executing) node: ComfyUI's prompt serializer
 * (`ComfyUI_frontend/src/utils/executionUtil.ts` ~131-135) records a link's
 * source as a bare positional index — `[origin_id, origin_slot]` — with NO
 * name lookup, and that index is resolved against the BACKEND's fixed
 * `RETURN_TYPES` tuple order at execution time, which never changes.
 * `removeOutput()` itself decrements `origin_slot` on every link whose slot
 * comes AFTER the removed one (LGraphNode.ts ~1670-1685) to keep the
 * FRONTEND's array self-consistent — but the backend tuple doesn't shift to
 * match. So removing anything other than the true TAIL of `node.outputs`
 * would silently repoint any live wire on a LATER output (e.g. `width`,
 * `height`) at the wrong backend value. Concretely: `original_width` /
 * `original_height` (RETURN_NAMES' last two entries) sit at the tail, so
 * removing/restoring that pair (LIFO) is 100% safe — nothing ever sits after
 * them to desync. `image` (the passthrough) is RETURN_NAMES[0], with
 * `resized_image`/`width`/`height` always after it, so removing it for real
 * would corrupt any of THEIR existing links. There is no reordering trick
 * that fixes this (the backend order is frozen, FORMAT.md §6.5/§8).
 *
 * So: "Show original size" uses REAL removeOutput/addOutput (space is
 * genuinely reclaimed). "Show passthrough image" uses a purely COSMETIC,
 * data-model-untouched suppression instead — it monkeypatches just that one
 * slot's own `draw()` to a no-op for the duration of a single synchronous
 * `drawSlots()` call (LGraphNode.ts ~4107), then restores it immediately.
 * `node.outputs`/`_concreteOutputs` membership, order, and every index are
 * never touched, so there is zero risk to link correctness — the tradeoff is
 * that the passthrough's row stays reserved (a blank row) rather than the
 * node shrinking. Documented here rather than silently shipping a "hidden"
 * flag that does nothing.
 *
 * ---- Defaults flipped to OFF (2026-07-20, this round) ----
 *
 * Owner, after validating the mechanism above: "That works. Let's have those
 * off by default." A fresh node now shows only `resized_image`/`width`/
 * `height` (the passthrough's row 0 stays reserved-but-blank per the cosmetic
 * mechanism above; the original-size pair is genuinely absent).
 *
 * Reload semantics (why flipping the *seed* is safe): `addProperty()`
 * (LGraphNode.ts ~1624-1638) is a plain, unconditional `this.properties[name]
 * = default_value` — it never fires `onPropertyChanged`, so seeding `false`
 * here does nothing by itself; `attach()` below calls
 * `applyPassthroughVisibility`/`applyOriginalSizeVisibility` once, right
 * after seeding, to make a FRESH node's outputs actually match the new
 * default. A RELOADED node gets the exact same two calls first (harmless —
 * both functions are idempotent), because `nodeCreated` (this file's
 * `attach()`) always runs BEFORE `LGraphNode.configure()` for a saved
 * workflow — confirmed live and in `LGraphNode.ts` (`configure()`'s
 * properties loop: `for (const k in info.properties) { this.properties[k] =
 * info.properties[k]; this.onPropertyChanged?.(k, info.properties[k]) }`,
 * ~842-849). Since `attach()` already replaced `node.onPropertyChanged`
 * before `configure()` ever runs, that loop calls the SAME wrapped handler
 * below, with whatever the FILE says (`true` for a still-all-visible
 * v0.14.0 workflow, `false` for one saved after this change) — the saved
 * value always wins last. `configure()`'s generic per-key loop separately
 * clones `info.outputs` wholesale into `node.outputs` (arrays have no
 * `.configure()` method, so they fall to `LiteGraph.cloneObject`, ~862-870)
 * regardless of key order relative to `properties` — either order converges
 * on the file's true saved shape, because every step here is idempotent
 * (`outputIndexByName` checks before add/remove) and the wholesale outputs
 * clone is authoritative for link data no synthetic `addOutput()` call could
 * reconstruct (e.g. `links`). Verified live both directions — see the round
 * report.
 *
 * ---- M2: the size-grid DOM widget ----
 *
 * FORMAT.md §6.5 M2 mandates a DOM widget (`addDOMWidget`), not a litegraph
 * `draw()`/`mouse()` custom widget — the pack's proven `Prompt Notebook`
 * (`web/lora_library/notebook.js`) and premiere-bridge button-bar
 * (`comfyui-premiere-bridge/web/cprb/nodes.js`) pattern, which renders
 * correctly under BOTH the classic LiteGraph canvas AND the Vue-node
 * renderer with one implementation — sidestepping the dual-backend risk a
 * canvas custom-widget would carry.
 *
 * Sizing started from the premiere-bridge lesson (`nodes.js`'s
 * `BAR_HEIGHT`/`attachBarWidget`, verified live there 2026-07-19):
 * `getMinHeight`/`getMaxHeight` ALONE are ignored for a small standalone DOM
 * widget on at least one rendering path, collapsing it to a ~7px sliver. The
 * robust fix sets all of: `domWidget.computeSize`, `domWidget.computedHeight`,
 * AND the element's own `style.height`/`minHeight` — belt-and-suspenders, all
 * four (plus `getMinHeight`/`getMaxHeight` closures, kept for the
 * classic-canvas `computeLayoutSize()` path in `scripts/domWidget.ts`).
 *
 * ---- M2 sizing v3: FULL-WIDTH SQUARE, height follows width ----
 * ---- (2026-07-21, owner-reported; supersedes v0.19.3's fill-taller) ----
 *
 * v0.16.0 (M2's initial ship) used the lesson above to build a FIXED pad;
 * v0.19.3 made it fill whatever extra height a manual resize added ("drag
 * taller to grow"). Owner, on that model: "It grows, but awkwardly. There
 * should never be space to the right of the square, it should be locked to
 * the left/right side of the node. The min height of the node should be
 * determined by the width ... right now if you drag the node down and make
 * it taller vertically, you can't reduce the height." The stuck-tall bug
 * was v0.19.3's own grow-never-shrink machinery (`getMaxHeight → Infinity`,
 * a drag-height baseline, and a grow-only reload path) — all deleted.
 * FORMAT.md §6.5 M2 now specifies the replacement, implemented here:
 *
 * The pad is a true SQUARE spanning the node's full content width, locked
 * to the left/right edges (no centered plot region, no horizontal
 * letterbox), with the two readout lines directly below it. Its height is
 * therefore a pure function of node WIDTH:
 *
 *   widget height  (litegraph) = node.size[0] + TEXT_STRIP_H     (0 hidden)
 *   element height (CSS)       = (node.size[0] - 2*margin) + TEXT_STRIP_H
 *
 * The two differ by exactly the DOM widget's own 2*margin because the
 * frontend boxes the element at [node.width - margin*2, computedHeight -
 * margin*2] (DomWidgets.vue's overlay, verified in the frontend checkout) —
 * so reporting node-width-plus-strip as the WIDGET height is precisely what
 * makes the element box come out square-plus-strip. The element's own
 * inline height is set to the same answer for whichever path honors element
 * style directly (the ~7px-sliver finding above). All four sliver-proofing
 * knobs stay, all now reporting this ONE width-derived number:
 * `domWidget.computeSize` (a live closure over node.size[0] — litegraph
 * calls it argument-less from `_arrangeWidgets` and with the node's MIN
 * width from `computeSize()`, so it must not trust its argument),
 * `domWidget.computedHeight`, the element's inline height/minHeight
 * (`applyGridHeight()`), and `getMinHeight`/`getMaxHeight` — now BOTH the
 * exact derived height (min == max: the widget is precisely that tall;
 * with computeSize/computedHeight also maintained, this is not premiere's
 * collapse-implicated shape, where those two were left unset).
 *
 * Node-size enforcement — why the node cannot get stuck tall: every path
 * that changes node size funnels through `applyWidthDrivenNodeSize()`,
 * which ASSIGNS `node.size[1] = node.computeSize()[1]` — never max()es
 * against the current height, so there is no independent tall state to
 * preserve. Because the grid widget's computeSize closure reads
 * node.size[0] live, `node.computeSize()[1]` already IS "everything else +
 * the width-derived pad" — v0.19.3's pinned re-measure pass
 * (computeNaturalSize) is unnecessary and gone. The hooks: `node.onResize`
 * (LGraphCanvas's resize drag calls setSize() every drag frame, which fires
 * this — dragging taller snaps straight back, narrowing shrinks the square
 * and the node in the same frame), `onConfigure` (configure() restores the
 * saved size BEFORE calling onConfigure last, so a reloaded workflow keeps
 * its SAVED width and gets its height recomputed from it — a file saved
 * stuck-tall by v0.19.3 loads normalized), attach (fresh nodes), the
 * ResizeObserver (any path that resizes the element without litegraph
 * noticing, e.g. Vue-nodes layout; every write in the chain is
 * change-guarded so the observer converges instead of looping), and
 * `resyncSize()` (property toggles, unchanged semantics). Inside onResize
 * the assignment uses the `size` ACCESSOR (`node.size = [w, h]`), not
 * setSize(): setSize() is what invokes onResize, so calling it there would
 * recurse — the accessor performs the same _size write (plus the frontend's
 * layout-store mutation) without re-entering the callback.
 *
 * Why the assignment can't fight litegraph's own widget auto-grow (the one
 * place core grows a node for widgets, `_arrangeWidgets`'s tail: `if (y >
 * bodyHeight) setSize([w, y])`): computeSize()'s height carries +8
 * (widget-list tail pad) +6 (bottom margin) over the arrange loop's final y
 * for the same widget stack (verified in LGraphNode.ts: H = rows*SLOT_H +
 * Σ(h+4) + 14 vs y_end = slotsBottom + 2 + Σ(h+4) with slotsBottom =
 * (rows-0.3)*SLOT_H + 10, so H - y_end = 8). A node sitting at
 * computeSize()[1] always satisfies arrange, the grow branch stays
 * quiescent, and there is no setSize <-> onResize oscillation.
 *
 * The node's minimum WIDTH (LiteGraph.NODE_WIDTH * 1.5 with widgets ≈ 210)
 * is what floors the square now (~190px content) — GRID_MIN_H is gone; the
 * width IS the floor, per the spec ("min height ... determined by width").
 *
 * Pointer handling mirrors `notebook.js`'s `wireSplitter`/row-drag
 * (pointerdown → best-effort `setPointerCapture` in a try/catch →
 * window-level `pointermove`/`pointerup`/`pointercancel` listeners, torn
 * down on `pointerup`/`pointercancel` AND on node removal). That file's own
 * header explains why this is safe against the underlying graph canvas at
 * all: DOM widgets render as DOM SIBLINGS of `<canvas id="graph-canvas">`,
 * never nested inside it, so a pointerdown targeting our element structurally
 * cannot reach litegraph's capture-phase canvas listener (capture phase only
 * sees descendants). `stopPropagation()`/`preventDefault()` here are
 * defensive anyway (per the round brief) since bubble-phase listeners
 * further up the DOM tree are a separate question from that capture-phase
 * one, and behavior is explicitly a thing to re-verify on Eric's 0.28.1
 * frontend build, not just this rig's 1.45.21.
 *
 * Widget-value writes use the exact idiom `notebook.js`'s `syncEntryWidget()`
 * documents as mirroring ComfyUI's own `scripts/widgets.ts`
 * (`applyWidgetControl`): `widget.value = next; widget.callback?.(next)` —
 * this is what actually updates serialization (widgets serialize `.value`
 * directly) and notifies anything else listening via the widget's callback.
 * Plain `INT` widgets' restore path during `configure()` (`widget.value =
 * info.widgets_values[i++]`, LGraphNode.ts ~933) is a bare assignment with NO
 * callback — confirmed in `LGraphNode.ts` — so a reloaded workflow's
 * width/height never fires our wrapped callback either; `onConfigure` is
 * chained separately below specifically to repaint after a reload.
 */

import { app } from '../../../scripts/app.js'
import { api } from '../../../scripts/api.js'

const NODE_TYPE = 'EPSResolution'
const NODE_TITLE = 'EPS Resolution'
const PREFIX = '[eps_image/resolution]'

const PROP_SHOW_PASSTHROUGH = 'Show passthrough image'
const PROP_SHOW_ORIGINAL_SIZE = 'Show original size'
const PROP_SHOW_GRID = 'Show grid'
const PROP_GRID_MAX = 'Grid max'
const PROP_PRESETS_ENABLED = 'Presets'

/** eps_image/nodes_resolution.py RETURN_NAMES — the one hideable leading
 * output, and the hideable trailing pair (order matters, see file header). */
const PASSTHROUGH_NAME = 'image'
const ORIGINAL_SIZE_NAMES = ['original_width', 'original_height']
const ORIGINAL_SIZE_TYPE = 'INT'

// --------------------------------------------------------------- utilities

function outputIndexByName(node, name) {
  return (node.outputs || []).findIndex((output) => output?.name === name)
}

/**
 * Whether *output* currently carries a real link — checks BOTH `.links`
 * (settled connections) and `._floatingLinks` (a link mid-drag, not yet
 * dropped), mirroring `LGraphCanvas.ts`'s own `hasRelevantOutputLinks` (the
 * guard right above `_processNodeClick`'s outputs loop) in this rig's
 * installed comfyui_frontend_package 1.45.21:
 * `[...(output.links ?? []), ...[...(output._floatingLinks ?? new Set())]]`.
 * The `.links`-only version replaced here in v0.34.0 missed the mid-drag case,
 * which BOTH callers below treat as destructive: `applyOriginalSizeVisibility`
 * would `removeOutput()` a socket that still has a link on it, and
 * `applyPassthroughVisibility` would leave that link drawn to an invisible
 * (but still hit-testable) dot. Identical to distributor.js's function of the
 * same name — the two refusal paths are deliberately kept in lockstep.
 * Exported so tests/test_resolution_grid_js.py can drive it headlessly.
 */
export function isOutputConnected(output) {
  if (!output) return false
  if (Array.isArray(output.links) && output.links.length > 0) return true
  const floating = output._floatingLinks
  if (floating && typeof floating.size === 'number' && floating.size > 0) return true
  return false
}

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

/** Recompute layout after an outputs-array change: grow the width to fit if
 * needed, and set the height ABSOLUTELY (arrange() on its own only grows).
 * The reset every property toggle in this file uses. With the M2 grid
 * attached, computeSize()[1] already contains the width-derived pad height
 * (the grid widget's computeSize closure reads node.size[0] — see file
 * header), so this is automatically the M2-correct answer too; setSize()
 * then fires the node's onResize hook, which re-normalizes against the
 * (possibly grown) width. The grid callee is hoisted, so the forward
 * reference from here is safe. */
function resyncSize(node) {
  const computed = node.computeSize()
  node.setSize([Math.max(node.size[0], computed[0]), computed[1]])
  node.setDirtyCanvas(true, true)
  if (node._epsGrid) applyGridHeight(node)
}

function widgetByName(node, name) {
  return node.widgets?.find((widget) => widget && widget.name === name)
}

// ------------------------------------------------- "Show original size"

/** COSMETIC since v0.61.0 (FORMAT.md §6.5): the pair sits at slots 4-5
 * with the multi-image `resized_N` outputs BEHIND it, so the old REAL
 * `removeOutput()` mechanism became structurally unsafe -- removing a
 * non-tail output decrements every later link's `origin_slot`, the exact
 * hazard the file header documents. Hiding now uses the passthrough
 * output's draw-suppression (shared `hiddenOutputNames`/`drawSlots`
 * patch); the pair keeps its slots, its space, and every wire index.
 * `ensureOriginalSizeOutputs` handles pre-v0.61.0 saves that serialized
 * WITHOUT the pair. Idempotent, as before. */
function applyOriginalSizeVisibility(node) {
  ensureOriginalSizeOutputs(node)
  const hide = node.properties?.[PROP_SHOW_ORIGINAL_SIZE] === false
  if (hide) {
    const wired = ORIGINAL_SIZE_NAMES.some((name) => {
      const idx = outputIndexByName(node, name)
      return idx !== -1 && isOutputConnected(node.outputs[idx])
    })
    if (wired) {
      // Never leave a wire drawn to an invisible dot -- same refusal as
      // the passthrough hide.
      node.properties[PROP_SHOW_ORIGINAL_SIZE] = true
      toast(node, 'warn', 'Unwire the original-size outputs before hiding them.')
    }
  }
  node.setDirtyCanvas(true, true) // cosmetic-only: no layout change needed
}

/** Re-APPEND the original-size pair when a pre-v0.61.0 save serialized the
 * node without it (the old hide really removed the sockets). Append-only
 * and canonical by construction: such saves have exactly the four leading
 * outputs, so the pair lands back at its declared slots 4-5, and existing
 * links' indices are untouched. */
function ensureOriginalSizeOutputs(node) {
  let appended = false
  for (const name of ORIGINAL_SIZE_NAMES) {
    if (outputIndexByName(node, name) === -1) {
      node.addOutput(name, ORIGINAL_SIZE_TYPE)
      appended = true
    }
  }
  if (appended) resyncSize(node)
}

// ------------------------------------------------- "Show passthrough image"

/** Cosmetic-only suppression of the `image` output's dot + label. Installed
 * once per node instance; reads the live property on every draw rather than
 * baking a decision in, so toggling the property redraws correctly with no
 * further wiring needed. */
/** Guard the cosmetic passthrough hide the same way applyOriginalSizeVisibility
 * guards its real removal: refuse to hide while the `image` output is wired.
 * The cosmetic patch only suppresses slot.draw -- LGraphCanvas.drawConnections
 * and getSlotInPosition ignore it, so a hidden-but-connected output would leave
 * a wire dangling to an invisible, still-hit-testable dot (looks broken). */
function applyPassthroughVisibility(node) {
  const hide = node.properties?.[PROP_SHOW_PASSTHROUGH] === false
  if (hide) {
    const idx = outputIndexByName(node, PASSTHROUGH_NAME)
    const out = idx !== -1 ? node.outputs[idx] : null
    if (isOutputConnected(out)) {
      node.properties[PROP_SHOW_PASSTHROUGH] = true // never leave a dangling wire
      toast(node, 'warn', 'Unwire the passthrough image output before hiding it.')
    }
  }
  node.setDirtyCanvas(true, true) // cosmetic-only: no layout change needed
}

/** Every output name currently cosmetically hidden -- the passthrough
 * (its original mechanism) and, since v0.61.0, the original-size pair
 * (whose hide converted from real removal to this same suppression --
 * see applyOriginalSizeVisibility). */
function hiddenOutputNames(node) {
  const names = []
  if (node.properties?.[PROP_SHOW_PASSTHROUGH] === false) names.push(PASSTHROUGH_NAME)
  if (node.properties?.[PROP_SHOW_ORIGINAL_SIZE] === false) names.push(...ORIGINAL_SIZE_NAMES)
  return names
}

function installPassthroughVisibility(node) {
  if (node._epsPassthroughPatched) return
  node._epsPassthroughPatched = true

  const originalDrawSlots = node.drawSlots
  if (typeof originalDrawSlots !== 'function') return // defensive: unrecognized litegraph build

  node.drawSlots = function (ctx, options) {
    const slots = []
    for (const name of hiddenOutputNames(this)) {
      const idx = outputIndexByName(this, name)
      const slot = idx !== -1 ? this._concreteOutputs?.[idx] : null
      if (slot && typeof slot.draw === 'function') slots.push(slot)
    }

    if (slots.length === 0) {
      originalDrawSlots.call(this, ctx, options)
      return
    }

    // Patch just these slots' own draw() for this single synchronous
    // call, then put each back exactly as found (own-property vs.
    // inherited — see file header: never leave an own `undefined`
    // shadowing the prototype's real draw()).
    const restores = slots.map((slot) => ({
      slot,
      hadOwnDraw: Object.prototype.hasOwnProperty.call(slot, 'draw'),
      original: slot.draw
    }))
    for (const { slot } of restores) slot.draw = () => {}
    try {
      originalDrawSlots.call(this, ctx, options)
    } finally {
      for (const { slot, hadOwnDraw, original } of restores) {
        if (hadOwnDraw) slot.draw = original
        else delete slot.draw
      }
    }
  }
}

// --------------------------------------------------------------- M2: the size grid
//
// A <canvas> DOM widget acting as a 2D size pad. x -> `width`, y -> `height`,
// linear over [GRID_MIN_SIZE, Grid max]. See file header for the sizing +
// pointer-event + widget-sync rationale; this section is the implementation.

const GRID_WIDGET_NAME = 'eps_resolution_grid'
const GRID_WIDGET_TYPE = 'eps_resolution_grid'

// No GRID_MIN_H anymore (owner fix 2026-07-21): the pad is a full-width
// square, so the node's minimum WIDTH is what floors it — see file header.
export const GRID_MIN_SIZE = 64 // pad's logical minimum on both axes
const GRID_MAX_DEFAULT = 2048 // node property seed; FORMAT.md §6.5 M2 (owner ask 2026-07-20: "make 2048 the
// default max size" — was 4096 in v0.15.0. NEW nodes only: attach() seeds this via addProperty(), which is a
// silent this.properties[name] = default assignment (see file header) never touching an EXISTING node's already-
// serialized property value — an old workflow's saved "Grid max" keeps whatever it was, by design, no migration.
const GRID_MAX_FLOOR = 256 // "Grid max" property clamp: sane lower bound
const GRID_MAX_CEILING = 16384 // matches width/height widgets' own INPUT_TYPES max
const SNAP_FALLBACK = 64 // used when `multiple_of` is 0 (off)
const GRIDLINE_STEP = 512
const DEFAULT_ANCHOR = 1024 // plotting anchor when BOTH axes are 0 (matches the backend's own INPUT_TYPES default)
const ACCENT_COLOR = 'rgb(66, 133, 244)' // house accent, lora_library/notebook.js's selection color

// Readout strip under the square: two lines of the SAME small size (owner
// bug 2026-07-21 — the dimension line was too large, and megapixels wrapped
// onto its own line). Line 1 = dims (left) + MP (right-aligned, same line);
// line 2 = reduced aspect, muted. Exported constants are consumed by
// tests/test_resolution_grid_js.py; the app entry uses only init()/attach().
export const TEXT_STRIP_H = 22 // total strip height, CSS px — ONE readout line
/** Extra strip height when the incoming-image line is also drawn (2026-07-29).
 * Added to TEXT_STRIP_H — never a second magic total — so the one-line
 * geometry every earlier fix settled stays exactly as it was. */
export const SOURCE_LINE_H = 15
/** The source line's baseline, below line 1's. */
const READOUT_LINE2_BASELINE = 15 + SOURCE_LINE_H
/** Muted prefix marking the second line as the INPUT, so the two lines can
 * never be confused for each other at a glance. */
const SOURCE_PREFIX = 'in'
const SOURCE_PREFIX_GAP = 6
export const READOUT_FONT_SIZE = 11 // px — the ONE size the whole readout shares
export const READOUT_FONT = `${READOUT_FONT_SIZE}px ui-monospace, "SF Mono", Menlo, Consolas, monospace`
export const READOUT_FONT_STRONG = `600 ${READOUT_FONT}`
const READOUT_LINE1_BASELINE = 15 // px below the square's bottom edge
const READOUT_ASPECT_GAP = 8 // px between the dims and the aspect on the one line
const READOUT_INSET_X = 4 // text inset from the pad's flush left/right edges
const DOM_WIDGET_MARGIN_FALLBACK = 10 // BaseDOMWidgetImpl.DEFAULT_MARGIN (frontend scripts/domWidget.ts)

const GRID_STYLE_TAG_ID = 'eps-resolution-grid-style'
let gridStylesInjected = false

// The Notebook's CSS (web/lora_library/notebook.js CSS_TEXT) is the house
// reference palette: dark panel bg / muted border / two text tones, all
// theme-CSS-variables-with-fallback so it reads on both Comfy themes.
const GRID_CSS_TEXT = `
.eps-res-grid-canvas {
  display: block;
  width: 100%;
  box-sizing: border-box;
  background: var(--comfy-input-bg, #1e1e1e);
  border: 1px solid var(--border-color, #444);
  border-radius: 4px;
  cursor: crosshair;
  touch-action: none;
  user-select: none;
}
.eps-res-prompt-overlay {
  position: fixed; inset: 0; z-index: 10000;
  display: flex; align-items: center; justify-content: center;
  background: rgba(0, 0, 0, 0.45);
}
.eps-res-prompt {
  min-width: 260px; padding: 14px;
  background: var(--comfy-menu-bg, #262626);
  border: 1px solid var(--border-color, #444);
  border-radius: 6px;
  display: flex; flex-direction: column; gap: 10px;
  font-family: inherit; font-size: 12px;
  color: var(--input-text, #ccc);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5);
}
.eps-res-prompt-input {
  width: 100%; box-sizing: border-box; padding: 5px 6px;
  background: var(--comfy-input-bg, #1e1e1e);
  border: 1px solid var(--border-color, #444);
  border-radius: 3px; color: var(--input-text, #ccc);
  font-family: inherit; font-size: 12px;
}
.eps-res-prompt-row { display: flex; gap: 8px; justify-content: flex-end; }
.eps-res-prompt-row button {
  padding: 4px 12px; cursor: pointer;
  background: var(--comfy-input-bg, #1e1e1e);
  border: 1px solid var(--border-color, #444);
  border-radius: 3px; color: var(--input-text, #ccc);
  font-family: inherit; font-size: 12px;
}
`

function injectGridStyles() {
  if (gridStylesInjected) return
  gridStylesInjected = true
  if (document.getElementById(GRID_STYLE_TAG_ID)) return
  const style = document.createElement('style')
  style.id = GRID_STYLE_TAG_ID
  style.textContent = GRID_CSS_TEXT
  document.head.appendChild(style)
}

function clamp(value, lo, hi) {
  return Math.min(hi, Math.max(lo, value))
}

function clamp01(value) {
  return clamp(value, 0, 1)
}

function gcdInt(a, b) {
  a = Math.round(Math.abs(a))
  b = Math.round(Math.abs(b))
  while (b) {
    const t = b
    b = a % b
    a = t
  }
  return a || 1
}

/** "3:2"-style reduced aspect ratio via gcd. Exported for tests. */
export function formatAspect(w, h) {
  const g = gcdInt(w, h)
  return `${Math.round(w / g)}:${Math.round(h / g)}`
}

/** "0.52 MP" / "2.1 MP" / "12 MP" — precision tapers as the number grows.
 * Exported for tests. */
export function formatMegapixels(w, h) {
  const mp = (w * h) / 1_000_000
  const decimals = mp >= 10 ? 0 : mp >= 1 ? 1 : 2
  return `${mp.toFixed(decimals)} MP`
}

/**
 * The readout strip's text (FORMAT.md §6.5 M2, owner fix 2026-07-21):
 * line 1 is `dims` on the left with `mp` RIGHT-ALIGNED on the SAME line
 * (megapixels never wrap onto their own line anymore); line 2 is `aspect`,
 * muted. Both lines render at the one READOUT_FONT_SIZE. Pure over
 * computeDisplayWH()'s result; exported for tests.
 */
export function getReadoutLines(disp) {
  const wLabel = disp.wAuto ? 'auto' : String(Math.round(disp.rawW))
  const hLabel = disp.hAuto ? 'auto' : String(Math.round(disp.rawH))
  return {
    dims: `${wLabel} x ${hLabel}`,
    mp: formatMegapixels(disp.dispW, disp.dispH),
    aspect: formatAspect(disp.dispW, disp.dispH)
  }
}

/**
 * The SOURCE line's text — the incoming image's own size, in the same
 * shape `getReadoutLines` produces for the target (owner ask 2026-07-29:
 * "show the width/height/ratio of the incoming image (if the input is
 * hooked up) at the bottom of the panel in addition to the info for the
 * grid. Display in a similar format").
 *
 * `null` when there is nothing trustworthy to show (no link, an upstream
 * that displays no image, an image element that hasn't finished decoding
 * yet so its natural size still reads 0) — the caller then draws no second
 * line and the strip stays one line tall, so an unconnected node looks
 * exactly as it did before this feature.
 *
 * Pure over plain numbers; exported for tests.
 */
export function getSourceReadoutLine(width, height) {
  const w = Math.round(Number(width) || 0)
  const h = Math.round(Number(height) || 0)
  if (!(w > 0 && h > 0)) return null
  return {
    dims: `${w} x ${h}`,
    mp: formatMegapixels(w, h),
    aspect: formatAspect(w, h)
  }
}

/**
 * The incoming image's natural pixel size, read LIVE off whatever the
 * upstream node is already displaying — `{width, height}` or `null`.
 *
 * Why the upstream's rendered `<img>` and not a backend value: this has to
 * be useful BEFORE a Run (choosing a target size is the thing you do
 * first), and core already loads the real file for any node that shows a
 * preview — `LoadImage` from the moment a file is picked, a decode/grid
 * node after its own run. `naturalWidth/Height` on those elements is the
 * true source resolution, not the on-canvas thumbnail size.
 *
 * Deliberately shallow (no walking further up a chain of pass-through
 * nodes): one hop is what the owner's wiring is, and a wrong number here
 * would be worse than no number. Everything is optional-chained — a
 * missing graph, an unlinked slot, a `getInputNode` that a fork renamed,
 * or an upstream mid-load all degrade to `null`.
 */
function readIncomingImageSize(node) {
  try {
    const slot = imageInputSlot(node)
    if (slot < 0) return null
    const upstream =
      typeof node.getInputNode === 'function' ? node.getInputNode(slot) : null
    const imgs = upstream?.imgs
    if (!Array.isArray(imgs) || !imgs.length) return null
    // The focused cell when the upstream is showing one (a grid pager), else
    // its first image -- the same "which image is this node about" rule the
    // rest of the pack uses for `imgs`/`imageIndex`.
    const index =
      typeof upstream.imageIndex === 'number' && upstream.imageIndex >= 0
        ? upstream.imageIndex
        : 0
    const img = imgs[index] || imgs[0]
    const w = Number(img?.naturalWidth) || 0
    const h = Number(img?.naturalHeight) || 0
    if (!(w > 0 && h > 0)) return null
    return { width: w, height: h }
  } catch (error) {
    console.warn(PREFIX, 'could not read the incoming image size', error)
    return null
  }
}

/** Index of this node's `image` input, or -1. Name-based, never positional:
 * §6.5's input ORDER is not frozen the way its output order is. */
function imageInputSlot(node) {
  const inputs = node?.inputs
  if (!Array.isArray(inputs)) return -1
  return inputs.findIndex((input) => input && input.name === 'image')
}

/** Whether the source line should be drawn right now (and therefore
 * whether the readout strip is two lines tall). One place, so the draw
 * code and every height calculation can never disagree. */
function hasSourceLine(node) {
  return readIncomingImageSize(node) !== null
}

//: How long to keep watching for a just-wired upstream image to finish
//: decoding, and how often. 250ms x 12 = 3s, which covers a NAS/LAN load
//: without leaving a timer running behind a node nobody is looking at.
const SOURCE_PROBE_INTERVAL_MS = 250
const SOURCE_PROBE_MAX_TRIES = 12

/**
 * Re-checks for an incoming image size shortly after a repaint that found
 * none, and repaints once it appears.
 *
 * The race this closes: setting an `<img>`'s `src` starts an async
 * fetch+decode, so a node wired to a fresh LoadImage reports
 * `naturalWidth === 0` for the first frames. Without this the source line
 * would stay hidden until some UNRELATED repaint happened to land after
 * the decode — the same slow-load shape as EPS Image Grid's own 2026-07-21
 * bug, and worse over a LAN.
 *
 * Self-cancelling: stops the moment a size resolves (the repaint it
 * triggers is the last one), after SOURCE_PROBE_MAX_TRIES, or if the node
 * is torn down. At most one probe per node — a repeat call while one is
 * already pending is a no-op, so the per-frame draw path can call this
 * unconditionally.
 */
function scheduleSourceProbe(node) {
  const state = node._epsGrid
  if (!state || state.sourceProbe) return
  let tries = 0
  const tick = () => {
    if (!node._epsGrid || node._epsGrid !== state) return // node gone/replaced
    state.sourceProbe = null
    if (!state.canvas?.isConnected) return
    if (readIncomingImageSize(node)) {
      applyGridHeight(node)
      applyWidthDrivenNodeSize(node)
      renderGrid(node)
      return
    }
    if (++tries >= SOURCE_PROBE_MAX_TRIES) return
    state.sourceProbe = setTimeout(tick, SOURCE_PROBE_INTERVAL_MS)
  }
  state.sourceProbe = setTimeout(tick, SOURCE_PROBE_INTERVAL_MS)
}

function getGridMax(node) {
  const raw = Number(node.properties?.[PROP_GRID_MAX])
  const value = Number.isFinite(raw) && raw > 0 ? raw : GRID_MAX_DEFAULT
  return clamp(Math.round(value), GRID_MAX_FLOOR, GRID_MAX_CEILING)
}

/** Snap unit for a drag: the `multiple_of` widget's value when it's > 0
 * (FORMAT.md §6.5 M2), else the pad's own 64 fallback. */
function getSnapUnit(node) {
  const widget = widgetByName(node, 'multiple_of')
  const value = widget ? Number(widget.value) : 0
  return value > 0 ? value : SNAP_FALLBACK
}

function snapTo(value, unit) {
  if (!(unit > 0)) return value
  return Math.round(value / unit) * unit
}

/**
 * Reads the live `width`/`height` widgets and derives what the pad should
 * PLOT. Never returns a 0 — an axis at 0 (derive mode) is "mirrored" from
 * the other axis purely for plotting (both 0 falls back to DEFAULT_ANCHOR on
 * both axes), so the dot always lands somewhere meaningful instead of
 * pinned at the pad's origin corner. `wAuto`/`hAuto` say which axis (if any)
 * is really in derive mode, for the "auto" readout label.
 */
function computeDisplayWH(node) {
  const rawW = Number(widgetByName(node, 'width')?.value) || 0
  const rawH = Number(widgetByName(node, 'height')?.value) || 0
  const wAuto = rawW <= 0
  const hAuto = rawH <= 0

  let dispW = rawW
  let dispH = rawH
  if (wAuto && hAuto) {
    dispW = DEFAULT_ANCHOR
    dispH = DEFAULT_ANCHOR
  } else if (wAuto) {
    dispW = rawH
  } else if (hAuto) {
    dispH = rawW
  }

  return { rawW, rawH, dispW, dispH, wAuto, hAuto }
}

/** Resolves theme colors through actual computed CSS custom properties —
 * Canvas2D's fillStyle/strokeStyle do not understand `var(...)` themselves,
 * so these must be read via getComputedStyle on a connected element first. */
function readThemeColors(el) {
  const cs = getComputedStyle(el)
  const pick = (name, fallback) => cs.getPropertyValue(name).trim() || fallback
  return {
    panelBg: pick('--comfy-input-bg', '#1e1e1e'),
    border: pick('--border-color', '#444'),
    text: pick('--input-text', '#ccc'),
    muted: pick('--descrip-text', '#999')
  }
}

/** `widget.value = value; widget.callback?.(value)` — see file header
 * ("Widget-value writes"). No-ops when the value hasn't actually changed, to
 * avoid firing a widget callback (which may mark the graph dirty / touch
 * undo history) on every no-op pointermove tick during a drag. */
function setWidgetValue(widget, value) {
  if (!widget || widget.value === value) return
  widget.value = value
  try {
    widget.callback?.(value)
  } catch (error) {
    console.warn(PREFIX, 'width/height widget callback threw', error)
  }
}

/** Writes both axes as real numbers (never 0 — FORMAT.md §6.5 M2) and
 * repaints. This is the ONLY function that turns a drag into widget state. */
function writeSize(node, width, height) {
  setWidgetValue(widgetByName(node, 'width'), width)
  setWidgetValue(widgetByName(node, 'height'), height)
  renderGrid(node)
}

function isGridVisible(node) {
  return node.properties?.[PROP_SHOW_GRID] !== false
}

/** Toggles just the DOM-level show/hide (element display + widget.hidden).
 * Split out from resync so both the property-toggle path
 * (applyGridVisibility, below — an ABSOLUTE resync) and the attach/reload
 * path (applyWidthDrivenNodeSize, below) can share it without pulling in
 * each other's resync semantics. */
function applyGridShowHide(node) {
  const state = node._epsGrid
  if (!state) return
  const show = isGridVisible(node)
  state.canvas.style.display = show ? '' : 'none'
  state.domWidget.hidden = !show
}

/**
 * The LITEGRAPH-reported height of the grid widget for a node *nodeWidth*
 * wide: the full-width square plus the readout strip. The square's drawn
 * side is the ELEMENT width (nodeWidth - 2*margin), and the frontend boxes
 * the element at (reported height - 2*margin) — see file header — so
 * reporting nodeWidth + TEXT_STRIP_H is exactly what makes the element box
 * come out square-plus-strip. Pure; exported for tests.
 */
export function computeGridWidgetHeight(nodeWidth, withSourceLine = false) {
  return (
    Math.max(1, Number(nodeWidth) || 0) + TEXT_STRIP_H + (withSourceLine ? SOURCE_LINE_H : 0)
  )
}

/** The element's own inline CSS height for a node *nodeWidth* wide: the
 * square's side (the content width) plus the readout strip. Pure; exported
 * for tests. */
export function computeGridElementHeight(nodeWidth, margin, withSourceLine = false) {
  const m = Number.isFinite(margin) ? margin : DOM_WIDGET_MARGIN_FALLBACK
  const side = Math.max(1, (Number(nodeWidth) || 0) - 2 * m)
  return side + TEXT_STRIP_H + (withSourceLine ? SOURCE_LINE_H : 0)
}

/** computeGridWidgetHeight() gated on `Show grid` — the live number every
 * litegraph-facing sizing knob reports (hidden collapses to a hard 0). */
function gridWidgetHeightFor(node) {
  return isGridVisible(node) ? computeGridWidgetHeight(node.size[0], hasSourceLine(node)) : 0
}

/**
 * Pushes the CURRENT width-derived answer into the two sizing knobs that
 * need live maintenance: `domWidget.computedHeight` (read directly by the
 * DOM overlay between arranges; also re-derived by `_arrangeWidgets` from
 * the computeSize closure) and the element's own inline height/minHeight
 * (the knob the ~7px-sliver finding says at least one build treats as
 * authoritative — file header). `domWidget.computeSize` and
 * `getMinHeight`/`getMaxHeight` are live closures over the same number,
 * installed once in attachSizeGrid(). Write-guarded so the ResizeObserver
 * can call this without style churn (its own feedback loop must converge).
 * Never touches node.size — applyWidthDrivenNodeSize() owns that.
 */
function applyGridHeight(node) {
  const state = node._epsGrid
  if (!state) return
  const px = isGridVisible(node)
    ? `${Math.round(
        computeGridElementHeight(
          node.size[0],
          Number(state.domWidget?.margin),
          hasSourceLine(node)
        )
      )}px`
    : '0px'
  if (state.canvas.style.height !== px) state.canvas.style.height = px
  if (state.canvas.style.minHeight !== px) state.canvas.style.minHeight = px
  state.domWidget.computedHeight = gridWidgetHeightFor(node)
}

/**
 * HEIGHT FOLLOWS WIDTH (FORMAT.md §6.5 M2, owner fix 2026-07-21): assigns
 * the node's height from node.computeSize()[1] — which already includes
 * the width-derived pad, because the grid widget's computeSize closure
 * reads node.size[0] live. An ASSIGNMENT, never a max() against the
 * current height: that discipline is the whole stuck-tall fix. There is no
 * independent tall state to preserve, so a taller drag snaps back and a
 * narrower node shrinks, in the same frame (file header has the onResize
 * and no-oscillation analysis). No-op while the grid is hidden (a grid-less
 * node sizes like any stock node) or when the grid never attached
 * (fail-soft path). `growWidthToMinimum` additionally raises WIDTH to
 * litegraph's own minimum (attach/reload) — it never narrows, so a
 * reloaded workflow keeps its saved width and only has its height
 * recomputed from it.
 *
 * Sizes via the `size` ACCESSOR, not setSize(): setSize() is the caller of
 * onResize, where this runs on every resize-drag frame — the accessor does
 * the same _size write (plus the frontend's layout-store mutation) without
 * re-entering the callback.
 */
function applyWidthDrivenNodeSize(node, { growWidthToMinimum = false } = {}) {
  if (!node._epsGrid || !isGridVisible(node)) return
  let computed = node.computeSize()
  if (growWidthToMinimum && computed[0] > node.size[0]) {
    node.size = [computed[0], node.size[1]]
    computed = node.computeSize() // height depends on width — remeasure at the grown width
  }
  const height = computed[1]
  if (Number.isFinite(height) && node.size[1] !== height) {
    node.size = [node.size[0], height]
    node.setDirtyCanvas(true, true)
  }
}

/** Property-toggle path (Show grid flips; also reused, via resyncSize(),
 * whenever an outputs-array change fires applyOriginalSizeVisibility/
 * applyPassthroughVisibility): an ABSOLUTE resync, matching every other
 * property toggle in this file — hiding the grid reclaims its space;
 * re-showing recomputes the pad from the node's current width
 * (height-follows-width has no other state to restore, an improvement on
 * v0.19.3's land-back-on-the-floor tradeoff). */
function applyGridVisibility(node) {
  if (!node._epsGrid) return
  applyGridShowHide(node)
  resyncSize(node)
  if (isGridVisible(node)) renderGrid(node)
}

/**
 * FULL-WIDTH SQUARE plot region (FORMAT.md §6.5 M2, owner fix 2026-07-21 —
 * supersedes the centered `min(availW, availH)` letterbox of v0.17-v0.19):
 * the square IS the widget's full width, locked to the left and right
 * edges — plotX is always 0 and side == cssW, so there is structurally
 * never empty space beside the pad. Square cells (owner bug 2026-07-20)
 * stay automatic: mapX/mapY share the ONE side/span scale, which is now
 * simply the content width. Shared by drawGrid() (so the visual gridlines/
 * diagonal/crosshair are square) AND attachGridDrag()'s pointer mapping (so
 * a drag along the visible diagonal actually produces width == height) —
 * one function, two callers, so they can never drift apart into "looks
 * square but drags rectangular" or vice versa. Pure; exported for tests.
 */
export function getPlotRect(cssW) {
  return { plotX: 0, plotY: 0, side: Math.max(1, Number(cssW) || 0) }
}

/** Target value (GRID_MIN_SIZE..gridMax) -> px offset within the plot
 * square. The single shared scale both axes use. Pure; exported for
 * tests. */
export function valueToPlot(value, side, gridMax) {
  const span = Math.max(1, gridMax - GRID_MIN_SIZE)
  return clamp01((value - GRID_MIN_SIZE) / span) * side
}

/** px offset within the plot square -> target value; valueToPlot()'s
 * inverse (before snapping/rounding). A pointer past the square's edges
 * clamps to the edge value, exactly like the visual pad edge. Pure;
 * exported for tests. */
export function plotToValue(px, side, gridMax) {
  const span = Math.max(1, gridMax - GRID_MIN_SIZE)
  return GRID_MIN_SIZE + clamp01(px / Math.max(1, side)) * span
}

/**
 * Draws the pad's contents: gridlines every GRIDLINE_STEP, a faint 1:1
 * diagonal, a crosshair + dot at the current target, and the two-line
 * readout below. All colors come from readThemeColors() so the pad reads
 * on both Comfy themes without any light/dark branching. The plot is the
 * FULL-WIDTH square from getPlotRect() — mapX/mapY share valueToPlot()'s
 * one scale, so a 1000x1000 target sits on the true 45° diagonal and every
 * gridline cell is visually square, not just numerically square.
 */
function drawGrid(node, ctx, cssW) {
  const canvas = node._epsGrid.canvas
  const colors = readThemeColors(canvas)
  const gridMax = getGridMax(node)
  const disp = computeDisplayWH(node)

  const { plotX, plotY, side } = getPlotRect(cssW)
  const mapX = (v) => plotX + valueToPlot(v, side, gridMax)
  const mapY = (v) => plotY + valueToPlot(v, side, gridMax)

  // Gridlines every 512 units.
  ctx.save()
  ctx.strokeStyle = colors.border
  ctx.globalAlpha = 0.35
  ctx.lineWidth = 1
  ctx.beginPath()
  for (let u = GRIDLINE_STEP; u < gridMax; u += GRIDLINE_STEP) {
    const x = Math.round(mapX(u)) + 0.5
    ctx.moveTo(x, plotY)
    ctx.lineTo(x, plotY + side)
    const y = Math.round(mapY(u)) + 0.5
    ctx.moveTo(plotX, y)
    ctx.lineTo(plotX + side, y)
  }
  ctx.stroke()
  ctx.restore()

  // Faint 1:1 diagonal (w == h locus under this now-uniform mapping — the
  // dot always sits exactly on it when w==h, and now that's visually TRUE,
  // not just numerically true, since mapX/mapY share one scale).
  ctx.save()
  ctx.strokeStyle = ACCENT_COLOR
  ctx.globalAlpha = 0.3
  ctx.setLineDash([4, 4])
  ctx.beginPath()
  ctx.moveTo(mapX(GRID_MIN_SIZE), mapY(GRID_MIN_SIZE))
  ctx.lineTo(mapX(gridMax), mapY(gridMax))
  ctx.stroke()
  ctx.restore()

  // Crosshair — drawn ONLY from the origin edges (top + left; origin =
  // smallest size, since mapX/mapY grow right/down) TO the dot, never past it
  // (owner ask 2026-07-21). The segments beyond the dot (x to its right, y
  // below it) would lie OUTSIDE the target rectangle the user is defining —
  // they'd be sizes LARGER than the chosen W/H — so hiding them leaves the two
  // lines tracing the right + bottom edges of the image rectangle, meeting at
  // the dot (the rectangle's far corner).
  const tx = mapX(disp.dispW)
  const ty = mapY(disp.dispH)
  const txPx = Math.round(tx) + 0.5
  const tyPx = Math.round(ty) + 0.5
  ctx.save()
  ctx.strokeStyle = ACCENT_COLOR
  ctx.globalAlpha = 0.45
  ctx.lineWidth = 1
  ctx.beginPath()
  ctx.moveTo(txPx, plotY) // vertical: top edge down to the dot (hidden below it)
  ctx.lineTo(txPx, tyPx)
  ctx.moveTo(plotX, tyPx) // horizontal: left edge across to the dot (hidden right of it)
  ctx.lineTo(txPx, tyPx)
  ctx.stroke()
  ctx.restore()

  // Dot: a panel-bg "halo" cutout ring, then the solid accent dot on top —
  // reads cleanly against the crosshair/gridlines on either theme.
  ctx.save()
  ctx.beginPath()
  ctx.arc(tx, ty, 7, 0, Math.PI * 2)
  ctx.fillStyle = colors.panelBg
  ctx.fill()
  ctx.beginPath()
  ctx.arc(tx, ty, 5, 0, Math.PI * 2)
  ctx.fillStyle = ACCENT_COLOR
  ctx.fill()
  ctx.restore()

  // Readout: two lines of the SAME small size (owner fix 2026-07-21 — the
  // 13px dimension line was too large). Line 1: "1024 x 512" (or "auto"
  // per axis) left + "0.52 MP" RIGHT-ALIGNED on the same line, never
  // wrapped onto its own line. Line 2: the reduced aspect, muted. The
  // strip sits directly below the square (which ends at plotY + side) and
  // spans the pad's full width, matching the square's flush edges.
  const lines = getReadoutLines(disp)
  const baseY = plotY + side + READOUT_LINE1_BASELINE
  ctx.save()
  ctx.textBaseline = 'alphabetic'
  // ONE line (owner ask 2026-07-21 — the ratio goes NEXT TO the pixel
  // dimensions, not on its own line below): dims (strong) then the reduced
  // aspect (muted) immediately after it on the left; megapixels (muted)
  // right-aligned on the same line.
  ctx.textAlign = 'left'
  ctx.font = READOUT_FONT_STRONG
  ctx.fillStyle = colors.text
  ctx.fillText(lines.dims, READOUT_INSET_X, baseY)
  const dimsWidth = ctx.measureText(lines.dims).width
  ctx.font = READOUT_FONT
  ctx.fillStyle = colors.muted
  ctx.fillText(lines.aspect, READOUT_INSET_X + dimsWidth + READOUT_ASPECT_GAP, baseY)
  ctx.textAlign = 'right'
  ctx.fillText(lines.mp, cssW - READOUT_INSET_X, baseY)

  // Line 2 (2026-07-29 owner ask): the INCOMING image, same shape as line 1
  // -- dims, then the reduced aspect, with megapixels right-aligned -- but
  // entirely muted and prefixed "in", so the target size stays the one
  // thing that reads as the node's own value. Drawn only when there is a
  // real number to show; `hasSourceLine` gates the strip height off the
  // exact same check, so the text can never land outside the element.
  const source = readIncomingImageSize(node)
  const sourceLine = source && getSourceReadoutLine(source.width, source.height)
  if (!sourceLine) scheduleSourceProbe(node)
  if (sourceLine) {
    const baseY2 = plotY + side + READOUT_LINE2_BASELINE
    ctx.font = READOUT_FONT
    ctx.fillStyle = colors.muted
    ctx.textAlign = 'left'
    ctx.fillText(SOURCE_PREFIX, READOUT_INSET_X, baseY2)
    const prefixWidth = ctx.measureText(SOURCE_PREFIX).width
    const dimsX = READOUT_INSET_X + prefixWidth + SOURCE_PREFIX_GAP
    ctx.fillText(sourceLine.dims, dimsX, baseY2)
    const sourceDimsWidth = ctx.measureText(sourceLine.dims).width
    ctx.fillText(sourceLine.aspect, dimsX + sourceDimsWidth + READOUT_ASPECT_GAP, baseY2)
    ctx.textAlign = 'right'
    ctx.fillText(sourceLine.mp, cssW - READOUT_INSET_X, baseY2)
  }
  ctx.restore()
}

/** devicePixelRatio-aware repaint: resizes the canvas's backing store to
 * match its CURRENT CSS size (read fresh every call — the "draw-time width
 * check" that keeps this correct regardless of what triggered the repaint,
 * the ResizeObserver included), then draws. Fails soft: a draw error is
 * logged and never breaks the caller (widget writes already happened by the
 * time this runs — see writeSize()). */
function renderGrid(node) {
  const state = node._epsGrid
  if (!state?.canvas?.isConnected) return
  if (!isGridVisible(node)) return

  const canvas = state.canvas
  // getBoundingClientRect() is SCALED by litegraph's canvas zoom (a CSS
  // `transform: scale()` on an ancestor); divide it out to recover the
  // intrinsic (unzoomed) CSS size. The readout's line baselines (+15/+29) and
  // TEXT_STRIP_H are FIXED px added to `side` (= the draw width): drawing at a
  // zoom-shrunk width while those offsets stayed constant pushed line 2 (the
  // aspect) past the also-shrunk element bottom and clipped it — the
  // owner-reported "second line is cut off", reproducing at any zoom < 100%
  // (the rig defaults to 66%). Unzooming keeps the strip math exact at every
  // zoom, and keeps the backing store full-res (crisper text) when zoomed out.
  // (clientWidth is unusable here — the element's width comes from the
  // transform, not layout, so it reads 0.)
  const zoom = app.canvas?.ds?.scale || 1
  const rect = canvas.getBoundingClientRect()
  const cssW = Math.max(1, Math.round(rect.width / zoom))
  const cssH = Math.max(1, Math.round(rect.height / zoom))
  const dpr = window.devicePixelRatio || 1
  const bufW = Math.max(1, Math.round(cssW * dpr))
  const bufH = Math.max(1, Math.round(cssH * dpr))
  if (canvas.width !== bufW) canvas.width = bufW
  if (canvas.height !== bufH) canvas.height = bufH

  const ctx = canvas.getContext('2d')
  if (!ctx) return

  try {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, cssW, cssH)
    drawGrid(node, ctx, cssW)
  } catch (error) {
    console.warn(PREFIX, 'grid draw failed', error)
  }
}

/**
 * Wires pointerdown/move/up drag -> writeSize(), mirroring notebook.js's
 * wireSplitter (see file header). Returns a `cancel()` the caller stashes
 * for node-removal cleanup (a removed node's pointerup never fires, so
 * in-flight window listeners would otherwise leak).
 *
 * Modifiers (FORMAT.md §6.5 M2, owner ask 2026-07-20 — supersedes v0.15.0's
 * "Shift = free drag / no snap"): Shift constrains the drag to a 1:1
 * square; Ctrl/Cmd constrains to the aspect ratio the box had when THIS
 * drag started. The two are mutually exclusive (Shift wins if somehow both
 * are held — a fixed 1:1 is the more explicit ask, and the two targets
 * would otherwise conflict). Snapping (to `multiple_of`, else 64) now
 * applies under BOTH modifiers and under no modifier at all — there is no
 * more no-snap path. The raw-pointer -> width/height mapping below uses
 * getPlotRect()/plotToValue() — the SAME full-width square and scale
 * drawGrid() paints with — so a drag along the visible 45° diagonal lands
 * on width == height even without Shift, and Shift's forced equality
 * survives snapping exactly (both axes run the identical snapTo(), so
 * equal inputs stay equal).
 */
function attachGridDrag(node, canvasEl) {
  let drag = null // { pointerId, aspect, startX, startY }

  const applyFromEvent = (event) => {
    const rect = canvasEl.getBoundingClientRect()
    const gridMax = getGridMax(node)
    const { plotX, plotY, side } = getPlotRect(rect.width)
    const x = clamp(event.clientX - rect.left, 0, rect.width)
    const y = clamp(event.clientY - rect.top, 0, rect.height)

    // Inverse of drawGrid()'s mapX/mapY via the SAME shared plotToValue()
    // scale for both axes (the square-cells fix). A pointer below the
    // square (over the readout strip) clamps to the bottom edge value,
    // exactly like the visual pad edge.
    let w = plotToValue(x - plotX, side, gridMax)
    let h = plotToValue(y - plotY, side, gridMax)

    if (event.shiftKey) {
      // Constrain to a 1:1 square: whichever axis the pointer has pushed
      // further from the pad's origin drives both. No drag-start state
      // needed (unlike Ctrl's captured aspect), so toggling Shift mid-drag
      // just works.
      const size = Math.max(w, h)
      w = size
      h = size
    } else if (drag && (event.ctrlKey || event.metaKey)) {
      // Lock the aspect captured at drag start; let whichever axis has
      // moved further from the drag's origin drive the other (a plain,
      // predictable rule — this pad is deliberately the ANTI-Resolution-
      // Master, so "width always drives" would be simpler still, but this
      // reads more naturally under a real drag).
      const aspect = drag.aspect > 0 ? drag.aspect : 1
      const dxAbs = Math.abs(x - drag.startX)
      const dyAbs = Math.abs(y - drag.startY)
      if (dyAbs > dxAbs) w = h * aspect
      else h = w / aspect
    }

    // Snapping applies unconditionally now — Shift no longer means
    // "free drag" (FORMAT.md §6.5 M2, supersedes v0.15.0).
    const snap = getSnapUnit(node)
    w = snapTo(w, snap)
    h = snapTo(h, snap)

    w = clamp(Math.round(w), GRID_MIN_SIZE, gridMax)
    h = clamp(Math.round(h), GRID_MIN_SIZE, gridMax)
    writeSize(node, w, h)
  }

  const onMove = (event) => {
    if (!drag || event.pointerId !== drag.pointerId) return
    event.preventDefault()
    applyFromEvent(event)
  }

  function detach() {
    if (drag) {
      try {
        canvasEl.releasePointerCapture(drag.pointerId)
      } catch {
        // Not captured, or already released — nothing to do.
      }
    }
    window.removeEventListener('pointermove', onMove, { capture: true })
    window.removeEventListener('pointerup', endDrag, { capture: true })
    window.removeEventListener('pointercancel', endDrag, { capture: true })
    drag = null
  }

  function endDrag(event) {
    if (!drag || event.pointerId !== drag.pointerId) return
    detach()
  }

  canvasEl.addEventListener('pointerdown', (event) => {
    if (event.button > 0) return // primary mouse button / touch / pen only
    const rect = canvasEl.getBoundingClientRect()
    const disp = computeDisplayWH(node)
    drag = {
      pointerId: event.pointerId,
      aspect: disp.dispH > 0 ? disp.dispW / disp.dispH : 1,
      startX: clamp(event.clientX - rect.left, 0, rect.width),
      startY: clamp(event.clientY - rect.top, 0, rect.height)
    }
    try {
      canvasEl.setPointerCapture(event.pointerId)
    } catch {
      // Best-effort, mirrors notebook.js's wireSplitter — the window-level
      // listeners below still cover the drag either way.
    }
    // Capture phase, deliberately -- notebook.js's 2026-07-30 finding: the
    // Vue-nodes DOM wrapper stops pointer events from BUBBLING to window, so
    // plain window listeners never fire there and the drag never ends.
    // Capture descends from the window first, so it fires in both renderers;
    // the removeEventListener calls above must pass the same flag.
    window.addEventListener('pointermove', onMove, { capture: true })
    window.addEventListener('pointerup', endDrag, { capture: true })
    window.addEventListener('pointercancel', endDrag, { capture: true })
    // Defensive per the round brief — see file header's pointer-event
    // paragraph for why this is (structurally) redundant on THIS frontend's
    // sibling-DOM-widget model, and why it's kept anyway.
    event.preventDefault()
    event.stopPropagation()
    applyFromEvent(event)
  })

  return () => detach()
}

/**
 * Creates and wires the size-grid DOM widget for *node*. Guarded against
 * double-attach; every failure path is caught and logged so a setup error
 * never blocks the rest of attach() — the typed width/height fields keep
 * working regardless (FORMAT.md §6.5 M2's fail-soft requirement).
 */
function attachSizeGrid(node) {
  if (node._epsGrid) return
  try {
    injectGridStyles()

    if (typeof node.addDOMWidget !== 'function') {
      console.warn(PREFIX, 'this ComfyUI frontend has no addDOMWidget; size grid not attached')
      return
    }

    const canvasEl = document.createElement('canvas')
    canvasEl.className = 'eps-res-grid-canvas'
    // DOM widgets are skipped by ComfyUI's own tooltip layer on purpose
    // ("these use native browser tooltips" -- NodeTooltip.vue), so the pad
    // documents itself with a plain `title`.
    canvasEl.title =
      'Drag inside the pad to set the target size. The readout above shows ' +
      'width x height, the aspect ratio, and the megapixel count. ' +
      'Drag the far edge of the node to make the pad bigger.'

    const domWidget = node.addDOMWidget(GRID_WIDGET_NAME, GRID_WIDGET_TYPE, canvasEl, {
      hideOnZoom: true,
      serialize: false,
      // Both the EXACT width-derived height (min == max) — the widget is
      // precisely that tall, per the height-follows-width model. NOT
      // v0.19.3's floor-with-no-ceiling (`getMaxHeight → Infinity`), which
      // is what let the node hold an independent tall state it could get
      // stuck in (file header). Hidden still collapses to a hard 0/0.
      getMinHeight: () => gridWidgetHeightFor(node),
      getMaxHeight: () => gridWidgetHeightFor(node)
    })
    // Same two independent non-serialization flags as notebook.js's
    // attachDomWidget()/premiere-bridge's attachBarWidget() — see either
    // file's header for why both are needed. Grid state derives entirely
    // from the width/height widgets; nothing new serializes here.
    domWidget.serialize = false
    domWidget.serializeValue = () => undefined

    // The third litegraph-facing sizing knob (file header), installed ONCE:
    // a LIVE closure over the node's current width. litegraph invokes it
    // argument-less from _arrangeWidgets and with the node's MIN width from
    // LGraphNode.computeSize(), so the height deliberately ignores the
    // argument and reads node.size[0] itself — this is also what makes
    // node.computeSize()[1] the complete required-height answer
    // applyWidthDrivenNodeSize() assigns from.
    domWidget.computeSize = (width) => [width ?? node.size[0], gridWidgetHeightFor(node)]

    node._epsGrid = {
      canvas: canvasEl,
      domWidget,
      resizeObserver: null,
      cancelDrag: null,
      // Pending source-size probe timer (2026-07-29) -- see scheduleSourceProbe.
      sourceProbe: null
    }

    node.addProperty(PROP_SHOW_GRID, true, 'boolean')
    node.addProperty(PROP_GRID_MAX, GRID_MAX_DEFAULT, 'number')

    applyGridShowHide(node)
    // Fresh node: raise the width to litegraph's minimum if needed, then
    // derive the height from it (a fresh node's default size predates this
    // widget). The fourth knob (element inline height) follows.
    applyWidthDrivenNodeSize(node, { growWidthToMinimum: true })
    applyGridHeight(node)

    node._epsGrid.cancelDrag = attachGridDrag(node, canvasEl)

    if (typeof ResizeObserver === 'function') {
      const observer = new ResizeObserver(() => {
        // The element's laid-out size changed — including width changes
        // from paths that never fire node.onResize (e.g. Vue-nodes
        // layout). Re-derive the node height from the width, refresh the
        // grid's own knobs, repaint. Every write in this chain is
        // change-guarded, so the observer CONVERGES (one corrective pass,
        // then quiescent) instead of looping on its own feedback.
        applyWidthDrivenNodeSize(node)
        applyGridHeight(node)
        renderGrid(node)
      })
      observer.observe(canvasEl)
      node._epsGrid.resizeObserver = observer
    } // else: renderGrid() always re-reads getBoundingClientRect() at draw
    // time, so anything else that triggers a repaint (widget edits,
    // configure, a resize) still draws at the correct, current size. With it
    // present, this is ALSO what repaints the pad once onResize (below) or
    // applyGridHeight() changes the canvas element's own CSS height —
    // belt-and-suspenders with onResize's own direct renderGrid() call, not
    // a substitute for it (this observer never fires from a change to
    // computeSize/computedHeight alone on a render backend that doesn't
    // reflect those into the element's actual CSS box).

    // The incoming-image line has to appear/disappear as the `image` input
    // is wired and unwired (2026-07-29 owner ask). `onConnectionsChange` is
    // litegraph's own hook for exactly that, and it also fires on
    // disconnect — so both directions are covered by one wrap. The height
    // changes with it (one line becomes two), hence applyGridHeight +
    // applyWidthDrivenNodeSize before the repaint, in the same order every
    // other size-affecting path here uses. Wrap-never-replace, per §7.1.
    const originalOnConnectionsChange = node.onConnectionsChange
    node.onConnectionsChange = function (...args) {
      let result
      try {
        result = originalOnConnectionsChange?.apply(this, args)
      } finally {
        try {
          applyGridHeight(node)
          applyWidthDrivenNodeSize(node)
          renderGrid(node)
        } catch (error) {
          console.warn(PREFIX, 'connection-change grid refresh failed', error)
        }
      }
      return result
    }

    // A freshly-wired upstream image is usually still DECODING when
    // onConnectionsChange fires, so its naturalWidth reads 0 and the line
    // would silently stay hidden until something else repainted (the exact
    // shape of the Image Grid's own 2026-07-21 slow-load bug). One short
    // poll after any repaint that found no size, cheap and self-cancelling:
    // it stops as soon as a size appears or the tries run out.
    // "editing the numbers moves the dot" — wrap width/height so any
    // programmatic OR user-typed change repaints. try/finally (not catch):
    // an error in the pre-existing callback should propagate exactly as it
    // would without this wrapper; our repaint still runs either way.
    for (const name of ['width', 'height']) {
      const widget = widgetByName(node, name)
      if (!widget) continue
      const originalCallback = widget.callback
      widget.callback = function (...args) {
        let result
        try {
          result = originalCallback?.apply(this, args)
        } finally {
          renderGrid(node)
        }
        return result
      }
    }

    // Live resize drag: LGraphCanvas's resize interaction calls setSize()
    // on every drag frame, which fires this AFTER the new size lands.
    // applyWidthDrivenNodeSize() then assigns height = f(width) — dragging
    // the node taller snaps straight back, and narrowing it shrinks the
    // square and the node in the same frame; there is no way to leave the
    // drag with height out of step with width (owner bug 2026-07-21,
    // "can't reduce the height"). The assignment goes through the `size`
    // accessor, not setSize(), so this callback cannot re-enter itself
    // (file header). The grid's own knobs + repaint follow, since nothing
    // re-computes a plain computedHeight field for us on a bare drag.
    const originalOnResize = node.onResize
    node.onResize = function (size) {
      const result = originalOnResize?.call(this, size)
      applyWidthDrivenNodeSize(this)
      applyGridHeight(this)
      renderGrid(this)
      return result
    }

    // configure() restores widgets_values with a bare assignment (no
    // callback — see file header), so a reloaded workflow needs its own
    // repaint hook. configure() also restores the saved size BEFORE calling
    // onConfigure (its last act), so the width seen here is the file's
    // saved width: height is recomputed from it — a workflow saved
    // stuck-tall by v0.19.3 loads normalized at its saved width, and one
    // saved by this version round-trips exactly (its saved height already
    // equals the derived height).
    const originalOnConfigure = node.onConfigure
    node.onConfigure = function (info) {
      let result
      try {
        result = originalOnConfigure?.call(this, info)
      } finally {
        applyGridShowHide(this)
        applyWidthDrivenNodeSize(this, { growWidthToMinimum: true })
        applyGridHeight(this)
        if (isGridVisible(this)) renderGrid(this)
      }
      return result
    }

    const originalOnRemoved = node.onRemoved
    node.onRemoved = function (...args) {
      try {
        node._epsGrid?.resizeObserver?.disconnect()
      } catch (error) {
        console.warn(PREFIX, 'grid resize-observer disconnect failed', error)
      }
      try {
        node._epsGrid?.cancelDrag?.()
      } catch (error) {
        console.warn(PREFIX, 'grid drag cleanup failed', error)
      }
      try {
        // The source-size probe (2026-07-29) is the only timer this widget
        // owns; a deleted node must not leave one ticking.
        if (node._epsGrid?.sourceProbe) {
          clearTimeout(node._epsGrid.sourceProbe)
          node._epsGrid.sourceProbe = null
        }
      } catch (error) {
        console.warn(PREFIX, 'source-probe cleanup failed', error)
      }
      return originalOnRemoved?.apply(this, args)
    }
  } catch (error) {
    console.warn(PREFIX, 'size grid setup failed; typed width/height fields remain usable', error)
  }
}

// --------------------------------------------------------------- M3: size presets
//
// A `preset` COMBO widget (NOT hand-drawn -- Vue nodes render standard
// widget types natively, unlike the M2 grid's onDrawForeground-painted
// readout) plus Save/Delete buttons, reflecting/writing the hidden
// `presets` STRING widget the backend contract defines: JSON array of
// preset NAMES, default `"[]"`. Non-empty selection = fan-out (backend
// runs once per preset, ignoring the five typed fields); empty = classic
// single-run, unchanged from M1/M2. Since v0.67.1 a MANUAL edit of any of
// the five fields clears the selection (`wireManualEditClearsSelection`)
// -- the fields you see are always the fields that run. eps_image/nodes_resolution.py's
// INPUT_TYPES already ships `presets` with `"hidden": True` in its OWN
// options (the §7.5 Vue-mode hide flag) -- this section adds the matching
// canvas-mode `widget.hidden` flag and replaces it with real UI.
//
// ---- Widget-order divergence from the literal brief (verified, not an
// oversight) ----
//
// The task brief calls for the `preset` combo at `node.widgets[0]`, above
// `width`. That is NOT done here -- it would corrupt every reload of
// width/height/resize_method/interpolation/multiple_of/presets for every
// user's EXISTING saved workflow, verified directly against this rig's
// installed `comfyui_frontend_package`'s `LGraphNode.ts`
// (`api-BqIxvqZ8.js.map`, `../../src/lib/litegraph/src/LGraphNode.ts`):
//   - SERIALIZE (`o.widgets_values[i] = val`, ~L972-980) writes at the
//     widget's RAW index in `node.widgets`, `continue`-skipping (leaving a
//     HOLE, which JSON round-trips to `null`) any `widget.serialize ===
//     false` widget it passes over.
//   - RESTORE (`widget.value = info.widgets_values[i++]`, ~L917-924) reads
//     via a SEPARATE counter that only advances past NON-skipped widgets --
//     i.e. it expects the saved array to already be dense/compacted, which
//     the save side never produces once a skip sits before a kept widget.
//   These two are provably NOT symmetric except when every `serialize:
//   false` widget sits at the true TAIL of `node.widgets` (no kept widget
//   after it) -- exactly this file's own `GRID_WIDGET_NAME` DOM widget, and
//   exactly the reasoning `web/eps_image/image_grid.js`'s "Clear button"
//   section and `web/lora_library/controller.js`'s header ALREADY document
//   independently ("Two distinct ... serialize flags" / "declared last...
//   do not reorder without re-checking this") -- this is a previously-
//   verified, load-bearing constraint of this exact codebase, not a novel
//   finding. A leading `preset` combo -- serialized or not -- also breaks
//   EVERY pre-existing saved workflow outright: `configure()`'s restore is
//   purely positional, so inserting anything before `width` shifts every
//   later widget's read by one, silently reassigning width's saved value to
//   the new widget, height's to width, etc. `migrateWidgetsValues`
//   (`litegraphUtil.ts`, wired from `litegraphService.ts`'s node `configure`
//   override) does NOT rescue this -- it only reconciles a widget that
//   toggled `forceInput`, and bails (`return widgetsValues` unchanged)
//   whenever the widget/value counts disagree, which is exactly this case.
//   So instead: `preset`/`Save`/`Delete` are inserted immediately AFTER
//   every real backend widget (i.e. after `presets`) and BEFORE the M2 pad
//   -- the same provably-safe tail region `GRID_WIDGET_NAME` already
//   occupies alone. Verified against a plain trace of both loops for the
//   6-real+4-tail-frontend widget shape this file now has, both for a
//   fresh save/reload round trip and for a pre-this-feature workflow
//   loading (shorter `widgets_values`, no leading hole, the existing
//   `if (i >= values.length) break` early-exit leaves `presets`/`preset`/
//   `Save`/`Delete`/the pad at their fresh-construction defaults -- exactly
//   the graceful degradation every OTHER new field in this file already
//   relies on). "Save + Delete directly above the pad" (the brief's other
//   position requirement) IS satisfied exactly as asked.
//
// ---- Vue-nodes scope boundary (verified, not assumed) ----
//
// The combo itself is a REAL `addWidget('combo', ...)` widget, so Vue nodes
// render and single-pick it natively (confirmed against the installed
// frontend's `WidgetSelectDefault.vue`/`useProcessedWidgets.ts`:
// `createWidgetUpdateHandler` calls `widget.callback?.(newValue)` on a Vue
// pick exactly like `BaseWidget.setValue()` does on canvas -- `combo.
// callback` below is therefore the one write path both renderers share).
// The SHIFT/Ctrl/Cmd multi-select gesture is canvas-only, and this is a
// verified renderer limitation, not a missed case: Vue's combo
// (`WidgetSelectDefault.vue`/`useWidgetSelectActions.ts`) implements its
// OWN dropdown (a reka-ui Combobox) and never calls `widget.onClick` --
// canvas's `LGraphCanvas.processWidgetClick` -> `ComboWidget.onClick` path
// this file shadows below simply never runs under Vue, and there is no
// extension point in the Vue component to intercept a raw pointer event's
// modifier keys from outside core. Multi-select therefore degrades to
// "not reachable" (no error, nothing drawn wrong) under Vue nodes mode,
// consistent with this pack's existing, documented §7.5 Vue-mode gaps
// (hand-drawn controls; see `web/eps_image.js`'s `warnIfVueNodesMode`) --
// worth a follow-up if Eric wants it, not silently pretended away here.
//
// `LiteGraph` itself is referenced BELOW as an ambient global, unimported
// -- `web/lora_library/controller.js`'s header already established (and
// cites rgthree's own shipped `power_lora_loader.js` as prior art) that
// `LiteGraph`/`LGraphNode` are not importable via any stable path from a
// node pack's own web dir on real, currently-shipping ComfyUI frontends.
//
// ---- ContextMenu "stay open on click" verification ----
//
// VERIFIED against the SAME installed frontend's `ContextMenu.ts`
// (`api-BqIxvqZ8.js.map`, `../../src/lib/litegraph/src/ContextMenu.ts`,
// `inner_onclick()` ~L320-380): there is no `keep_open`/`closing` FIELD,
// but the mechanism is real -- an item's own `value.callback(...)`
// (distinct from the menu's top-level `options.callback`) is invoked with
// `this` bound to the clicked row DIV (~L353-362), and `if (r === true)
// close_parent = false` (~L361) means returning `true` from that callback
// stops `that.close()` (~L379) from ever running. `openMultiSelectMenu`
// below uses exactly that: each item's callback toggles the preset, mutates
// `this.textContent` in place (no full menu rebuild needed to show the new
// ✓ state), and returns `true`.
//
// ---- Vue "invalid" ring avoidance ----
//
// The combo's `.value` is never an ad-hoc display string. Canvas's
// `_displayValue`/Vue's `WidgetSelectDefault.vue` both derive the shown
// text from `options.getOptionLabel(value)`, but Vue ALSO cross-checks
// `value` against `options.values` and renders an invalid/error ring
// (`aria-invalid`, `ring-1 ring-destructive-background`) whenever it is
// absent (verified in `WidgetSelectDefault.vue`'s `isInvalid`/
// `selectedLabel`). So `.value` is always one of: `PRESET_NONE_VALUE`
// (0 selected), a real preset NAME (1 selected -- even one that no longer
// exists in the fetched store), or `PRESET_MULTI_VALUE` (2+ selected) --
// and `presetComboValues()` (a LIVE `options.values` function; ComboWidget.
// ts explicitly supports that, deprecation-warning aside -- controller.js's
// header already used this same escape hatch for the same reason) always
// folds the CURRENT value into the list even when it is not a "real"
// pickable option, so it is provably always "known" on both renderers.

const PRESETS_WIDGET_NAME = 'presets'
const PRESET_COMBO_NAME = 'preset'
const PRESET_COMBO_LABEL = 'preset'
const PRESETS_ROUTE = '/eps_resolution/presets'
const PRESETS_SAVE_ROUTE = '/eps_resolution/presets/save'
const PRESETS_DELETE_ROUTE = '/eps_resolution/presets/delete'

/** eps_image/nodes_resolution.py's 5-field preset "values" shape -- the
 * fields Save reads off the visible widgets and a picked preset applies
 * back onto them. */
const PRESET_FIELD_NAMES = ['width', 'height', 'resize_method', 'interpolation', 'multiple_of']

/** Sentinel `combo.value` tokens for the 0-/2+-selected states -- long,
 * double-underscore-namespaced strings a user-typed preset name is
 * exceedingly unlikely to collide with, and even if one somehow did,
 * `presetComboValues()`'s function form always folds the CURRENT value in,
 * so a collision would only ever cost a label, never a crash. See "Vue
 * invalid ring avoidance" above for why real, addressable tokens matter at
 * all instead of an arbitrary display string. */
const PRESET_NONE_VALUE = '__eps_resolution_preset_none__'
const PRESET_MULTI_VALUE = '__eps_resolution_preset_multi__'

// --------------------------------------------------- M3: pure helpers (tested under Node)

/** Parses the `presets` widget's raw JSON-array-of-names string. Identical
 * degrade-never-throw contract to `checkpoint_switcher.js`'s
 * `selectionFromWidgetValue` (this file's sibling precedent for a
 * JSON-array selection widget) -- malformed JSON, a non-array value, or a
 * missing/non-string *raw* all degrade to `[]`; non-string entries drop;
 * duplicates collapse to their first occurrence. (The backend's own
 * `_parse_preset_names`, `eps_image/nodes_resolution.py`, does NOT dedupe --
 * a deliberate divergence: this function feeds UI STATE, and this file's
 * own write path, `normalizeSelectionOrder`, can never itself produce a
 * duplicate, so the two never disagree about what actually runs.) */
export function selectionFromWidgetValue(raw) {
  if (typeof raw !== 'string' || raw.trim() === '') return []
  let parsed
  try {
    parsed = JSON.parse(raw)
  } catch {
    return []
  }
  if (!Array.isArray(parsed)) return []
  const seen = new Set()
  const out = []
  for (const entry of parsed) {
    if (typeof entry !== 'string' || seen.has(entry)) continue
    seen.add(entry)
    out.push(entry)
  }
  return out
}

/** Adds/removes *name* from *list*, preserving every other entry's relative
 * order and never duplicating -- the ContextMenu multi-select toggle
 * primitive. Identical shape to `checkpoint_switcher.js`'s `toggleName`. */
export function toggleSelection(list, name, checked) {
  const arr = Array.isArray(list) ? list : []
  const has = arr.includes(name)
  if (checked) return has ? arr.slice() : [...arr, name]
  return has ? arr.filter((entry) => entry !== name) : arr.slice()
}

/** Re-sorts *selection* to the FETCHED list's own order (checkpoint_switcher
 * .js's established "order is the fetched list's order, not click order"
 * convention -- this file's `EPSCheckpointSwitcher` sibling), with any
 * still-selected name absent from *fetchedNames* ("missing" -- deleted or
 * renamed elsewhere) appended after, in ITS prior relative order rather
 * than dropped (req. 5: "stays in the selection... server errors loudly at
 * run time"). This is the ONE function every write path funnels through
 * (`commitSelection`) and every read path re-derives through
 * (`reconcilePresetsUi`), so the widget's serialized order is always
 * deterministic regardless of pick order or reload timing. Pure; exported
 * for tests. */
export function normalizeSelectionOrder(selection, fetchedNames) {
  const rawList = Array.isArray(selection) ? selection : []
  const names = Array.isArray(fetchedNames) ? fetchedNames : []
  const knownSet = new Set(names)
  const selectedSet = new Set(rawList.filter((name) => typeof name === 'string'))
  const known = names.filter((name) => selectedSet.has(name))
  const missing = []
  const seenMissing = new Set()
  for (const name of rawList) {
    if (typeof name !== 'string' || knownSet.has(name) || seenMissing.has(name)) continue
    seenMissing.add(name)
    missing.push(name)
  }
  return [...known, ...missing]
}

/** The combo's closed-box text for a given selection: "(none)" / the sole
 * name / "N presets" (req. 2's exact three-state contract). Pure and
 * state-free (no "(missing)" annotation -- that needs the fetched-name
 * SET, layered on top by `labelForToken` below); exported for tests. */
export function dropdownLabelFor(selection) {
  const list = Array.isArray(selection) ? selection : []
  if (list.length === 0) return '(none)'
  if (list.length === 1) return list[0]
  return `${list.length} presets`
}

/** Index of the widget named *name* in a plain widgets array -- the pure
 * core of the "find the pad widget's position and splice before it"
 * insertion arithmetic (`relocateBeforePad`), factored out so the index
 * math is directly Node-probeable without a real litegraph node. Pure;
 * exported for tests. */
export function presetRowIndexFor(widgets, name) {
  if (!Array.isArray(widgets)) return -1
  return widgets.findIndex((widget) => widget && widget.name === name)
}

// ------------------------------------------------------- M3: state + lookups

function presetsState(node) {
  return node._epsPresets || null
}

function presetExists(state, name) {
  return !!(state.presetsById && Object.prototype.hasOwnProperty.call(state.presetsById, name))
}

/** `combo.value` for the current selection -- the ONE place the 0/1/2+
 * cases map to a token (see "Vue invalid ring avoidance" above for why
 * these are real tokens, not ad-hoc strings). */
function currentComboValue(selection) {
  if (!selection || selection.length === 0) return PRESET_NONE_VALUE
  if (selection.length === 1) return selection[0]
  return PRESET_MULTI_VALUE
}

/** `options.getOptionLabel`'s implementation -- both renderers call this
 * for EVERY row (list build) and for the closed-box value alike (BaseWidget
 * ._displayValue / WidgetSelectDefault.vue's selectedLabel), so one token ->
 * one label, uniformly. Reuses `dropdownLabelFor` for the none/multi cases
 * so the closed-box text and a hypothetical multi-row's own label can never
 * drift apart into two different strings for the same state. */
function labelForToken(state, token) {
  if (token === PRESET_NONE_VALUE) return dropdownLabelFor([])
  if (token === PRESET_MULTI_VALUE) return dropdownLabelFor(state.selection)
  return presetExists(state, token) ? token : `${token} (missing)`
}

/** Live `options.values` for the combo -- `(none)` + every fetched name,
 * PLUS the combo's own CURRENT value if it isn't already one of those (the
 * multi-sentinel, or a single missing/deleted name) so Vue's
 * known-option check never flags it invalid. Deliberately a FUNCTION, not a
 * static array: `ComboWidget.ts`'s `getValues()` explicitly supports this
 * (deprecated-but-functional, per `lora_library/controller.js`'s own
 * documented use of the identical escape hatch), and it has to be live
 * anyway since the fetched list and the current value both change after
 * construction. */
function presetComboValues(state) {
  const values = [PRESET_NONE_VALUE, ...state.presetNames]
  const current = state.combo ? state.combo.value : PRESET_NONE_VALUE
  if (!values.includes(current)) values.push(current)
  return values
}

// ------------------------------------------------------- M3: selection commit

/**
 * v0.67.1 (owner report 2026-08-18: "if you have a preset set, and then
 * change any of the properties manually, the preset menu should reset to
 * none and there should be no preset applied. Currently it looks like a
 * preset is still selected ... and the preset is overriding the manual
 * size"). Root cause: `applyPresetValues` copies a preset onto the five
 * fields as a courtesy preview, but the hidden `presets` array stays
 * set -- and the BACKEND ignores the fields whenever that array is
 * non-empty (nodes_resolution.py's contract), so a hand-typed width was
 * silently overridden at run time by the still-selected preset. The
 * rule now: a MANUAL edit of any preset field clears the selection, so
 * what you see in the fields is what runs. Pure predicate so the probe
 * can pin it: clear iff a selection exists and the edit is not our own
 * preset-apply writing the fields (the reentrancy guard -- without it
 * picking a preset would clear itself on its first field write).
 * @param {{applying?: boolean, selection?: string[]}} state
 * @returns {boolean}
 */
export function clearsPresetOnManualEdit(state) {
  if (!state || state.applying) return false
  return Array.isArray(state.selection) && state.selection.length > 0
}

/** Wraps the five preset fields' widget callbacks (the one write path both
 * renderers share -- canvas `BaseWidget.setValue()` and Vue's
 * `createWidgetUpdateHandler` both call `widget.callback`, per the M3
 * header) so a manual edit clears the selection through `commitSelection`
 * -- combo to "(none)", hidden `presets` to "[]". try/finally, chaining
 * the original (the M2 grid's own width/height repaint wrap sits
 * underneath) -- the file's established wrap idiom. `copy from image` and
 * a grid drag both write through these same callbacks, so they count as
 * manual edits too: the owner's "change any of the properties". */
function wireManualEditClearsSelection(node, state) {
  for (const field of PRESET_FIELD_NAMES) {
    const widget = widgetByName(node, field)
    if (!widget) continue
    const originalCallback = widget.callback
    widget.callback = function (...args) {
      let result
      try {
        result = originalCallback?.apply(this, args)
      } finally {
        if (clearsPresetOnManualEdit(state)) commitSelection(node, [])
      }
      return result
    }
  }
}

/** Copies preset *name*'s five stored values onto the visible width/height/
 * resize_method/interpolation/multiple_of widgets, through the file's own
 * `setWidgetValue` (value + callback, so serialization and the M2 grid's
 * width/height callback-wrap both see it -- `renderGrid` below is a second,
 * explicit repaint in case a field's own value didn't actually change and
 * `setWidgetValue`'s equal-value guard no-opped it). A courtesy preview
 * only -- once selection is non-empty the BACKEND ignores these fields
 * entirely and resolves straight from the store (nodes_resolution.py's own
 * docstring) -- but showing what a preset contains, and leaving sane values
 * behind if the user picks back to "(none)", is worth the two-line cost.
 * Missing/garbled preset data (deleted elsewhere between fetch and click) is
 * silently a no-op here; the backend is what "errors loudly at run time"
 * (req. 5), not this preview path. */
function applyPresetValues(node, name) {
  const state = presetsState(node)
  if (!state) return
  const values = state.presetsById && state.presetsById[name]
  if (!values || typeof values !== 'object') return
  // v0.67.1: these field writes are OURS, not a manual edit -- the guard
  // `wireManualEditClearsSelection` reads (try/finally so a throwing
  // widget callback can never leave the flag stuck on).
  state.applying = true
  try {
    for (const field of PRESET_FIELD_NAMES) {
      if (!(field in values)) continue
      setWidgetValue(widgetByName(node, field), values[field])
    }
  } finally {
    state.applying = false
  }
  renderGrid(node)
}

/**
 * THE single write path for the selection -- every picker (plain combo
 * pick, "(none)", the multi-select menu's toggles, Save, Delete) funnels
 * through this. Normalizes to fetched order (`normalizeSelectionOrder`),
 * writes the hidden `presets` widget as JSON via value + callback (this
 * file's established `widget.value = x; widget.callback?.(x)` idiom, file
 * header), applies the sole preset's values when the result is exactly one
 * name (req. 2/req. "consistent regardless of the path that produced it" --
 * a multi-select toggle that nets down to one name applies exactly like a
 * plain pick), and re-renders the combo + Delete's enabled state.
 */
function commitSelection(node, nextSelection) {
  const state = presetsState(node)
  if (!state) return
  const ordered = normalizeSelectionOrder(nextSelection, state.presetNames)
  const json = JSON.stringify(ordered)
  state.selection = ordered
  if (state.widget.value !== json) {
    state.widget.value = json
    state.widget.callback?.(json)
  }
  if (ordered.length === 1) applyPresetValues(node, ordered[0])
  renderPresetCombo(node)
  node.graph?.setDirtyCanvas(true, true)
}

function renderPresetCombo(node) {
  const state = presetsState(node)
  if (!state) return
  state.combo.value = currentComboValue(state.selection)
  updateDeleteEnabled(node)
  node.setDirtyCanvas(true, true)
}

/** Delete's disabled guard (req. 3): enabled only when exactly one preset is
 * selected -- that is this file's definition of "ACTIVE preset", derived
 * from `state.selection` rather than tracked as separate mutable state, so
 * the two can never drift apart. */
function updateDeleteEnabled(node) {
  const state = presetsState(node)
  if (!state || !state.deleteBtn) return
  state.deleteBtn.disabled = state.selection.length !== 1
}

/**
 * The shared reconciliation function -- checkpoint_switcher.js's
 * `reloadFromWidget` pattern (file brief's own pointer): re-derives
 * `state.selection` from the hidden widget's CURRENT value rather than
 * trusting either caller's private snapshot, so whichever of {the initial
 * fetch, `onConfigure`} finishes LAST produces the final, correct render
 * (`wireConfigureReload`'s header, `web/eps_image/checkpoint_switcher.js`).
 * Also enforces the "Presets off -> selection empty" invariant on every
 * reconcile, not just on the property's own toggle handler -- a saved file
 * with `Presets:false` and a stale non-empty `presets` array (should not
 * normally happen; `applyPresetsPropertyVisibility` clears on toggle-off)
 * self-corrects here too, and the correction is written BACK to the widget
 * so a Run never silently sees a selection the UI disagrees with.
 */
function reconcilePresetsUi(node) {
  const state = presetsState(node)
  if (!state) return
  const raw = selectionFromWidgetValue(state.widget.value)
  const enabled = node.properties?.[PROP_PRESETS_ENABLED] !== false
  if (!enabled && raw.length > 0) {
    state.selection = []
    const json = '[]'
    if (state.widget.value !== json) {
      state.widget.value = json
      state.widget.callback?.(json)
    }
  } else {
    state.selection = normalizeSelectionOrder(raw, state.presetNames)
  }
  renderPresetCombo(node)
}

// ------------------------------------------------------------- M3: fetch (GET)

function applyPresetsPayload(node, data) {
  const state = presetsState(node)
  if (!state) return
  const presetsObj =
    data && typeof data.presets === 'object' && data.presets !== null ? data.presets : {}
  state.presetsById = presetsObj
  // Object.keys() preserves JSON insertion order for non-numeric-looking
  // string keys (every preset name here) -- confirmed against the backend's
  // own test (`test_get_presets_reflects_saved_presets_in_order`): this IS
  // "the fetched list's order" checkpoint_switcher.js's convention means.
  state.presetNames = Object.keys(presetsObj)
  if (typeof data?.mtime === 'number') state.mtime = data.mtime
  state.loaded = true
}

async function loadPresets(node) {
  const state = presetsState(node)
  if (!state) return
  const token = ++state.loadToken
  try {
    const response = await api.fetchApi(PRESETS_ROUTE)
    if (token !== state.loadToken) return // superseded by a newer fetch
    let data = null
    try {
      data = await response.json()
    } catch {
      throw new Error(`Unexpected response (HTTP ${response.status})`)
    }
    if (!response.ok) {
      throw new Error((data && typeof data.error === 'string' && data.error) || `HTTP ${response.status}`)
    }
    if (token !== state.loadToken) return
    applyPresetsPayload(node, data)
    reconcilePresetsUi(node) // race-safe reconcile -- see file header
  } catch (error) {
    if (token !== state.loadToken) return
    console.warn(PREFIX, 'preset fetch failed', error)
    toast(node, 'error', `Could not load presets: ${(error && error.message) || error}`)
  }
}

// --------------------------------------------------------- M3: Save (POST)

/** `LGraphCanvas.prompt` where present, else `window.prompt` -- identical
 * fallback shape to `distributor.js`/`switcher.js`'s established
 * `promptForOutputLabel`. `window.prompt` returns `null` on Cancel (skip)
 * but `""` on an intentional OK-with-empty-field; either way an
 * empty/whitespace name is refused client-side (the backend would 400 it
 * anyway, but there is nothing useful to POST for an empty name). */
function promptPresetName(node, prefill, onCommit, event) {
  const canvas = app?.canvas ?? null
  const commit = (value) => {
    if (value === null || value === undefined) return
    const trimmed = String(value).trim()
    if (!trimmed) return
    onCommit(trimmed)
  }
  // The EVENT is passed through from the button widget's own callback
  // (owner report 2026-08-09: "clicking save ... doesn't seem to do
  // anything"): this file used to hand `canvas.prompt` a null event --
  // the only such call site in the pack, unlike distributor.js/
  // switcher.js, which pass theirs. `LGraphCanvas.prompt` reads
  // `LGraphCanvas.active_canvas` and positions off the event; with
  // neither it throws, and the window.prompt fallback below USED TO SIT
  // OUTSIDE this try, so a browser that also refuses window.prompt
  // (unsupported, or dialogs suppressed after a user checks "prevent
  // additional dialogs") killed the click with no dialog and no message.
  try {
    if (canvas && typeof canvas.prompt === 'function') {
      canvas.prompt('Preset name', prefill || '', commit, event ?? null)
      return
    }
  } catch (error) {
    console.warn(PREFIX, 'canvas.prompt failed; falling back', error)
  }
  try {
    commit(window.prompt('Preset name', prefill || ''))
    return
  } catch (error) {
    console.warn(PREFIX, 'window.prompt unavailable; using the built-in dialog', error)
  }
  // Last resort, owned entirely by this file so Save can NEVER be a silent
  // no-op: a minimal DOM dialog over the canvas.
  promptPresetNameFallback(node, prefill, commit)
}

/** Self-owned name dialog -- no litegraph, no window.prompt. Kept
 * deliberately plain (one input, OK/Cancel, Enter/Escape) since it only
 * ever runs when both platform prompts have failed. */
function promptPresetNameFallback(node, prefill, commit) {
  try {
    const overlay = document.createElement('div')
    overlay.className = 'eps-res-prompt-overlay'
    const box = document.createElement('div')
    box.className = 'eps-res-prompt'
    const label = document.createElement('div')
    label.className = 'eps-res-prompt-label'
    label.textContent = 'Preset name'
    const input = document.createElement('input')
    input.type = 'text'
    input.className = 'eps-res-prompt-input'
    input.value = prefill || ''
    const row = document.createElement('div')
    row.className = 'eps-res-prompt-row'
    const cancel = document.createElement('button')
    cancel.textContent = 'Cancel'
    const ok = document.createElement('button')
    ok.textContent = 'Save'
    row.append(cancel, ok)
    box.append(label, input, row)
    overlay.appendChild(box)

    const close = () => {
      overlay.remove()
    }
    cancel.addEventListener('click', close)
    ok.addEventListener('click', () => {
      const value = input.value
      close()
      commit(value)
    })
    input.addEventListener('keydown', (keyEvent) => {
      keyEvent.stopPropagation() // canvas hotkeys must not eat the typing
      if (keyEvent.key === 'Enter') ok.click()
      else if (keyEvent.key === 'Escape') close()
    })
    document.body.appendChild(overlay)
    input.focus()
    input.select()
  } catch (error) {
    console.warn(PREFIX, 'built-in preset-name dialog failed', error)
    toast(node, 'error', 'Could not open the preset-name dialog.')
  }
}

/** Save's entry point: prefilled with the ACTIVE preset's name (exactly one
 * selected) -- that's "update"; blank otherwise -- "create new" (req. 3). */
function openSaveDialog(node, event) {
  const state = presetsState(node)
  if (!state) return
  const prefill = state.selection.length === 1 ? state.selection[0] : ''
  promptPresetName(node, prefill, (name) => performSave(node, name), event)
}

async function performSave(node, name) {
  const state = presetsState(node)
  if (!state) return
  const values = {}
  for (const field of PRESET_FIELD_NAMES) {
    const widget = widgetByName(node, field)
    if (widget) values[field] = widget.value
  }
  const body = { name, values }
  // Omitting base_mtime entirely skips the backend's conflict check
  // (routes_resolution_presets.py/test_routes_resolution_presets.py's own
  // "omitted base_mtime skips conflict check" case) -- correct here too:
  // with no fetch/save/delete having resolved yet, there is no baseline to
  // conflict against.
  if (typeof state.mtime === 'number') body.base_mtime = state.mtime
  try {
    const response = await api.fetchApi(PRESETS_SAVE_ROUTE, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    })
    let data = null
    try {
      data = await response.json()
    } catch {
      // handled by the status checks below
    }
    if (response.status === 409) {
      // Divergence from the Notebook's fuller Reload/Overwrite UI
      // (`web/lora_library/notebook.js` §3.5/§7.2): a 5-field record has no
      // meaningful partial-merge story, so last-writer-wins-after-a-refresh
      // is the whole story here -- toast + re-fetch, no Reload/Overwrite
      // dialog, per the task brief's own explicit call.
      toast(node, 'warn', `"${name}" changed elsewhere -- refreshing presets.`)
      await loadPresets(node)
      return
    }
    if (!response.ok) {
      throw new Error((data && typeof data.error === 'string' && data.error) || `HTTP ${response.status}`)
    }
    applyPresetsPayload(node, data)
    commitSelection(node, [name]) // saved preset becomes active + selected (req. 3)
    toast(node, 'success', `Saved preset "${name}".`)
  } catch (error) {
    console.warn(PREFIX, 'preset save failed', error)
    toast(node, 'error', `Could not save preset: ${(error && error.message) || error}`)
  }
}

// ------------------------------------------------------- M3: Delete (POST)

async function performDelete(node) {
  const state = presetsState(node)
  if (!state) return
  const active = state.selection.length === 1 ? state.selection[0] : null
  if (!active) return // belt-and-suspenders with widget.disabled -- see createDeleteButton
  const body = { name: active }
  if (typeof state.mtime === 'number') body.base_mtime = state.mtime
  try {
    const response = await api.fetchApi(PRESETS_DELETE_ROUTE, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    })
    let data = null
    try {
      data = await response.json()
    } catch {
      // handled by the status checks below
    }
    if (response.status === 409) {
      toast(node, 'warn', `"${active}" changed elsewhere -- refreshing presets.`)
      await loadPresets(node)
      return
    }
    if (!response.ok) {
      // Covers the store's 404 (already gone) the same as any other
      // failure -- a clear error toast; no confirm dialog either direction,
      // matching the pack's low-friction style (task brief: "deletion is
      // recoverable by re-saving").
      throw new Error((data && typeof data.error === 'string' && data.error) || `HTTP ${response.status}`)
    }
    applyPresetsPayload(node, data)
    commitSelection(node, state.selection.filter((entry) => entry !== active))
    toast(node, 'success', `Deleted preset "${active}".`)
  } catch (error) {
    console.warn(PREFIX, 'preset delete failed', error)
    toast(node, 'error', `Could not delete preset: ${(error && error.message) || error}`)
  }
}

// -------------------------------------------------- M3: multi-select menu

/** SHIFT/Ctrl/Cmd + click on the combo: a checkbox-style `LiteGraph.
 * ContextMenu` (canvas-only -- see file header's Vue scope-boundary note).
 * Each row's own `value.callback` toggles membership through the pure
 * `toggleSelection` helper, commits via `commitSelection` (the one write
 * path), repaints just its own row text in place, and returns `true` so
 * the menu stays open across repeated toggles (verified mechanism -- file
 * header's ContextMenu.ts citation). */
function openMultiSelectMenu(node, opts) {
  const state = presetsState(node)
  if (!state) return
  const names = state.presetNames
  if (!names.length) {
    toast(node, 'info', 'No saved presets to select.')
    return
  }
  const event = opts && opts.e
  const canvas = (opts && opts.canvas) || app.canvas
  const scale = Math.max(1, (canvas && canvas.ds && canvas.ds.scale) || 1)

  const rowLabel = (name, checked) => `${checked ? '✓ ' : '  '}${name}`

  const items = names.map((name) => ({
    title: rowLabel(name, state.selection.includes(name)),
    value: name,
    callback: function presetRowClicked() {
      const nextChecked = !state.selection.includes(name)
      commitSelection(node, toggleSelection(state.selection, name, nextChecked))
      this.textContent = rowLabel(name, nextChecked)
      return true // keep the menu open -- see ContextMenu.ts citation above
    }
  }))

  new LiteGraph.ContextMenu(items, {
    title: 'Select presets (click to toggle)',
    event,
    className: 'dark',
    scale
  })
}

// --------------------------------------------------- M3: widget construction

/** BOTH hide flags (§7.5, req. 1) -- canvas reads `widget.hidden`; Vue reads
 * `widget.options.hidden` and ignores the first outright. The backend
 * already ships `options.hidden: true` in INPUT_TYPES (nodes_resolution.py)
 * for the Vue-mode half; this sets it again anyway (idempotent, matches
 * checkpoint_switcher.js's identical belt-and-suspenders) since a frontend
 * cannot assume any particular backend already did its half. */
function hidePresetsWidget(widget) {
  widget.hidden = true
  widget.options = { ...(widget.options || {}), hidden: true }
}

function createPresetCombo(node, state) {
  // Plain pick (both renderers -- see file header: Vue's updateHandler
  // calls `widget.callback?.(newValue)` exactly like canvas's setValue()
  // does). "(none)" clears; the synthetic multi-sentinel is never itself
  // "pickable" (no-op guard); any real name becomes the sole selection.
  const onPick = (value) => {
    if (value === PRESET_MULTI_VALUE) return
    commitSelection(node, value === PRESET_NONE_VALUE ? [] : [String(value)])
  }
  const combo = node.addWidget('combo', PRESET_COMBO_NAME, PRESET_NONE_VALUE, onPick, {
    values: () => presetComboValues(state),
    getOptionLabel: (raw) => labelForToken(state, raw == null ? PRESET_NONE_VALUE : String(raw)),
    serialize: false // excludes from the API prompt (utils/executionUtil.ts) -- see options.serialize note above
  })
  combo.label = PRESET_COMBO_LABEL
  combo.serialize = false // the workflow.json / widgets_values flag -- see file header's widget-order section
  // Hover text. `NodeTooltip.vue` shows a canvas widget's own `.tooltip`
  // ahead of the node def's (`widget.tooltip ?? translatedTooltip`), which
  // is the only route open to a frontend-added widget -- the backend has no
  // input by this name to hang a tooltip on.
  combo.tooltip =
    'Pick a saved size preset to fill in the fields below. ' +
    'Shift/Ctrl/Cmd-click instead to tick SEVERAL presets: one Run then ' +
    'resizes once per ticked preset, in the order you ticked them.'

  // SHIFT/Ctrl/Cmd -> the multi-select ContextMenu; plain click falls
  // through to the REAL stock ComboWidget.onClick, captured BEFORE
  // shadowing. `LGraphNode.addWidget()`/`toConcreteWidget` hands back the
  // actual ComboWidget INSTANCE (verified precedent:
  // `lora_library/controller.js`'s 2026-07-19c section, `_hookSetWidgetMenu`
  // -- since superseded there for unrelated reasons, but the shadowing
  // technique itself was independently verified stable), so shadowing
  // `.onClick` as an own property is a stable override, canvas-only by
  // construction (Vue never reads `.onClick` at all -- file header).
  const stockOnClick = typeof combo.onClick === 'function' ? combo.onClick.bind(combo) : null
  combo.onClick = function presetComboOnClick(opts) {
    const event = opts && opts.e
    const multi = !!(event && (event.shiftKey || event.ctrlKey || event.metaKey))
    if (multi) {
      openMultiSelectMenu(node, opts)
      return
    }
    if (stockOnClick) stockOnClick(opts)
  }
  return combo
}

/** Plain button widgets, matching `image_grid.js`'s `addClearButton`
 * idiom exactly: an empty options bag, then the TOP-LEVEL `.serialize =
 * false` (the flag `LGraphNode.ts` actually checks for `widgets_values` --
 * `options.serialize` is a different, API-prompt-only flag; see that
 * file's "Clear button" section and this file's widget-order note above). */
function createSaveButton(node) {
  // litegraph hands a button callback (value, canvas, node, pos, event) --
  // the event is what canvas.prompt needs (see promptPresetName).
  const btn = node.addWidget(
    'button',
    'Save',
    null,
    (_value, _canvas, _node, _pos, event) => openSaveDialog(node, event),
    // `options.serialize: false` keeps this button out of the API PROMPT --
    // a DIFFERENT flag from `btn.serialize` below, which keeps it out of the
    // workflow FILE (executionUtil.ts vs LGraphNode.ts). Rig-caught
    // 2026-08-14 alongside the new copy button: every queued prompt was
    // carrying phantom `"Save"`/`"Delete"` inputs for this node.
    { serialize: false }
  )
  btn.serialize = false
  btn.tooltip =
    'Save the fields below as a named size preset, stored in your library ' +
    'folder so every machine sharing it sees the same presets. Saving over ' +
    'an existing name replaces it.'
  return btn
}

function createDeleteButton(node, state) {
  const btn = node.addWidget(
    'button',
    'Delete',
    null,
    () => {
      // Belt-and-suspenders no-op guard (req. 3) alongside widget.disabled
      // -- disabled excludes the widget from getWidgetOnPos() hit-testing
      // on this rig's installed frontend (LGraphNode.ts, verified), but a
      // future/forked build's click plumbing is not this file's to trust
      // blindly.
      if (!state.deleteBtn || state.deleteBtn.disabled) return
      performDelete(node)
    },
    { serialize: false } // out of the API prompt -- see createSaveButton
  )
  btn.serialize = false
  btn.tooltip =
    'Delete the currently picked preset from your library folder. ' +
    'Available only when exactly one preset is picked.'
  btn.disabled = true // no active preset yet -- updateDeleteEnabled() maintains this from here on
  return btn
}

/** Moves *widget* to immediately before the M2 pad widget (req. 3's "find
 * the pad widget's position... and splice before it"), or to the tail if
 * the pad hasn't attached (fail-soft -- still provably safe per the
 * widget-order section above, just not adjacent to a pad that doesn't
 * exist). Called once per new widget, in [combo, Save, Delete] order, so
 * each relocation's "insert right before the pad" naturally stacks them in
 * that same order immediately above it. */
function relocateBeforePad(node, widget) {
  const widgets = node.widgets
  if (!widgets || !widget) return
  const currentIndex = widgets.indexOf(widget)
  if (currentIndex !== -1) widgets.splice(currentIndex, 1)
  const padIndex = presetRowIndexFor(widgets, GRID_WIDGET_NAME)
  const insertAt = padIndex === -1 ? widgets.length : padIndex
  widgets.splice(insertAt, 0, widget)
}

// ----------------------------------------------- M3: "Presets" property toggle

/** The "Presets" node property (req. 4, default true -- addProperty seeds
 * this silently, see file header's "Defaults flipped to OFF" for why a
 * caller must still apply it explicitly once). When false: the combo +
 * Save + Delete are hidden (BOTH flags each, same as `hidePresetsWidget`)
 * and the selection is forced empty so the backend runs classic mode
 * (`commitSelection(node, [])`), matching req. 7's "byte-identical when
 * unused" bar -- with an empty selection, `applyPresetValues` never fires
 * and every M1/M2 code path is untouched by this file's own construction
 * (no shared mutable state outside `node._epsPresets`). */
function applyPresetsPropertyVisibility(node) {
  const state = presetsState(node)
  if (!state) return
  const enabled = node.properties?.[PROP_PRESETS_ENABLED] !== false
  for (const widget of [state.combo, state.saveBtn, state.deleteBtn]) {
    if (!widget) continue
    widget.hidden = !enabled
    widget.options = { ...(widget.options || {}), hidden: !enabled }
  }
  if (!enabled && state.selection.length > 0) commitSelection(node, [])
  resyncSize(node)
  node.graph?.setDirtyCanvas(true, true)
}

// --------------------------------------------------------- M3: attach entry

/**
 * Per-node-instance presets UI setup. Guarded against a double call (the
 * `node._epsPresets` check, mirroring `attachSizeGrid`'s `node._epsGrid`
 * guard) and wrapped in try/catch so a setup failure never blocks M1/M2 --
 * called from `attach()` AFTER `attachSizeGrid(node)` so the pad widget
 * already exists for `relocateBeforePad`'s splice target (still fail-soft
 * if it doesn't -- see that function's own doc).
 */
function attachPresetsUi(node) {
  if (node._epsPresets) return
  try {
    const widget = widgetByName(node, PRESETS_WIDGET_NAME)
    if (!widget) {
      console.warn(PREFIX, 'EPSResolution node is missing its `presets` widget; preset UI not attached')
      return
    }
    hidePresetsWidget(widget)

    const state = {
      widget,
      combo: null,
      saveBtn: null,
      deleteBtn: null,
      presetsById: {},
      presetNames: [],
      mtime: null,
      selection: selectionFromWidgetValue(widget.value),
      loaded: false,
      loadToken: 0,
      applying: false // v0.67.1: true only while applyPresetValues writes the fields
    }
    node._epsPresets = state
    wireManualEditClearsSelection(node, state)

    state.combo = createPresetCombo(node, state)
    state.saveBtn = createSaveButton(node)
    state.deleteBtn = createDeleteButton(node, state)
    relocateBeforePad(node, state.combo)
    relocateBeforePad(node, state.saveBtn)
    relocateBeforePad(node, state.deleteBtn)

    node.addProperty(PROP_PRESETS_ENABLED, true, 'boolean')
    applyPresetsPropertyVisibility(node) // seeding alone fires no callback -- apply once explicitly (file header)
    renderPresetCombo(node)
    resyncSize(node)

    // THE fix for the restore race (checkpoint_switcher.js's
    // wireConfigureReload, cited in the task brief): onConfigure fires
    // AFTER widgets_values are restored, for both a whole-workflow load
    // and a paste, so this is what makes a reloaded selection actually
    // show up in the combo (widgets_values restore itself is a bare
    // assignment with no callback -- file header, "Widget-value writes").
    // Wrapped defensively (own try/catch), distinct from attachSizeGrid's
    // own onConfigure wrap already installed earlier in attach() -- chains
    // on top of it rather than replacing it.
    const originalOnConfigure = node.onConfigure
    node.onConfigure = function presetsOnConfigure(info) {
      let result
      try {
        result = originalOnConfigure?.call(this, info)
      } finally {
        try {
          reconcilePresetsUi(this)
        } catch (error) {
          console.warn(PREFIX, 'preset post-configure resync failed', error)
        }
      }
      return result
    }

    loadPresets(node).catch((error) => console.warn(PREFIX, 'initial preset load failed', error))
  } catch (error) {
    console.warn(PREFIX, 'preset UI setup failed', error)
  }
}

// --------------------------------------------------------------- lifecycle

/** Frontend-only one-time setup: inject the grid's stylesheet once. */
export function init() {
  injectGridStyles()
}

/** Per-node-instance attach; no-op unless node is EPSResolution. */
// ------------------------------------- v0.61.0: multi-image + widget layout

/** FORMAT.md §6.5 multi-image: the extras' name shape and the 8-image
 * ceiling (paired resized_N outputs are positional -- §8). */
const EXTRA_IMAGE_RE = /^image_([2-8])$/
const MAX_IMAGES = 8
const RESIZED_PREFIX = 'resized_'

/** The properties marker resolving the v0.61.0 HEIGHT-FIRST widget swap
 * (FORMAT.md §6.5): files WITHOUT it predate the swap, so their
 * positionally-restored width/height VALUES arrive transposed and get
 * swapped back by name. The DECISION always reads the incoming file --
 * never node.properties, which attach pre-stamps on every node. */
const WIDGET_LAYOUT_PROP = 'eps_res_widget_layout'
const WIDGET_LAYOUT_CURRENT = 2

/** Grow/shrink the image_N inputs (§6.4's converge idea, local minimal
 * form): keep exactly one unwired trailing extra once the chain has
 * started, capped at image_8. Deferred callers coalesce via
 * scheduleImageConverge -- never splice inputs from inside litegraph's own
 * connection dispatch (the v0.16.0 reentrancy lesson). */
function convergeExtraImageInputs(node) {
  const inputs = node.inputs || []
  const baseWired = inputs.find((input) => input?.name === 'image')?.link != null
  let highest = 1
  for (const input of inputs) {
    const match = EXTRA_IMAGE_RE.exec(input?.name || '')
    if (match && input.link != null) highest = Math.max(highest, Number(match[1]))
  }
  // One empty trailing slot once the chain is active; none before.
  const wantThrough = baseWired || highest >= 2 ? Math.min(highest + 1, MAX_IMAGES) : 1

  for (let n = 2; n <= wantThrough; n++) {
    if (!inputs.some((input) => input?.name === `image_${n}`)) {
      node.addInput(`image_${n}`, 'IMAGE')
    }
  }
  for (let n = MAX_IMAGES; n > wantThrough && n >= 2; n--) {
    const idx = (node.inputs || []).findIndex((input) => input?.name === `image_${n}`)
    if (idx !== -1 && node.inputs[idx].link == null) node.removeInput(idx)
  }
}

/** Reveal resized_N tail outputs through the highest wired image_N -- TRUE
 * add/remove is safe here because they are the genuine tail (appended
 * after original_height, §8). A wired resized_N is never removed. */
function revealExtraOutputs(node) {
  ensureOriginalSizeOutputs(node) // the pair must occupy slots 4-5 FIRST
  let highest = 1
  for (const input of node.inputs || []) {
    const match = EXTRA_IMAGE_RE.exec(input?.name || '')
    if (match && input.link != null) highest = Math.max(highest, Number(match[1]))
  }
  for (let n = 2; n <= highest; n++) {
    if (outputIndexByName(node, `${RESIZED_PREFIX}${n}`) === -1) {
      node.addOutput(`${RESIZED_PREFIX}${n}`, 'IMAGE')
    }
  }
  for (let n = MAX_IMAGES; n > highest && n >= 2; n--) {
    const idx = outputIndexByName(node, `${RESIZED_PREFIX}${n}`)
    if (idx !== -1 && !isOutputConnected(node.outputs[idx])) node.removeOutput(idx)
  }
  resyncSize(node)
}

/** setTimeout(0)-deferred, coalesced converge+reveal -- the same deferral
 * shape switcher.js's live converge uses (never mutate the inputs array
 * from inside litegraph's in-flight connection dispatch). */
function scheduleImageConverge(node) {
  if (node._epsResConvergeQueued) return
  node._epsResConvergeQueued = true
  setTimeout(() => {
    node._epsResConvergeQueued = false
    try {
      convergeExtraImageInputs(node)
      revealExtraOutputs(node)
    } catch (error) {
      console.warn(PREFIX, 'image-input converge failed', error)
    }
  }, 0)
}

//: The v0.63.0 one-click "make the target match what's wired in" button
//: (owner ask 2026-08-14). Label is the owner's own wording.
const COPY_FROM_IMAGE_LABEL = 'copy from image'

/**
 * `copy from image`: writes the incoming image's own pixel size into
 * `width`/`height` (owner ask 2026-08-14, "sets the width/height to the
 * width/height of the input image with one click"). Sits ABOVE the size
 * fields, which is where the ask puts it and where it reads as a heading
 * for the two numbers it fills in.
 *
 * The size comes from `readIncomingImageSize` -- the same live read the
 * source line uses, so the button is right whenever that line is (and the
 * two can never disagree). EXACT pixels, deliberately not snapped to
 * `multiple_of`: "copy" means copy, and a set `multiple_of` then rounds at
 * run time exactly as it would for a hand-typed size.
 *
 * Failure is never silent (§6.3): nothing wired and "wired but no decoded
 * image yet" are DIFFERENT toasts, because they need different fixes.
 */
function attachCopyFromImage(node) {
  const button = node.addWidget('button', COPY_FROM_IMAGE_LABEL, null, () => {
    const size = readIncomingImageSize(node)
    if (!size) {
      const wired = imageInputSlot(node) >= 0 && node.inputs?.[imageInputSlot(node)]?.link != null
      toast(
        node,
        'warn',
        wired
          ? "The wired image hasn't loaded yet — run the loader once (or wait for its preview), then try again."
          : 'Wire an image into this node first.'
      )
      return
    }
    writeSize(node, size.width, size.height)
    node.graph?.setDirtyCanvas(true, true)
  })
  // BOTH flags, and they are NOT interchangeable (executionUtil.ts says so
  // in as many words): `options.serialize` gates the API PROMPT,
  // `widget.serialize` gates the WORKFLOW file. Rig-caught 2026-08-14 --
  // with only the latter set, every queued prompt carried a phantom
  // `"copy from image": null` input for this node.
  button.serialize = false
  button.options = { ...(button.options || {}), serialize: false }
  // Move it above the size fields. A non-tail widget is normally forbidden
  // (FORMAT.md §8: `widgets_values` restores POSITIONALLY), and the reason
  // is real -- litegraph SERIALIZES by array index, skipping
  // `serialize:false` widgets and leaving a HOLE, but CONFIGURES with a
  // compacted counter that skips them again. Rig-proven 2026-08-14: a
  // leading skipped widget turned [333, 777, ...] into [null, 333, 777,
  // ...] and every value shifted by one on the next load.
  const index = node.widgets.indexOf(button)
  if (index !== -1) node.widgets.splice(index, 1)
  node.widgets.unshift(button)

  // ...which is why the hole is compacted back out on the way to disk (see
  // above). The saved `widgets_values` is then byte-identical to what a
  // button-less build writes, so old workflows load here AND workflows
  // saved here still load on an older build -- no migration shim, no
  // downgrade hazard. Chained, never replaced.
  const originalOnSerialize = node.onSerialize
  node.onSerialize = function (info) {
    const result = originalOnSerialize?.apply(this, arguments)
    try {
      const values = info?.widgets_values
      // `i in values` is the point: it is false exactly for the holes a
      // skipped widget leaves, and true for a real stored `null`.
      if (Array.isArray(values)) {
        info.widgets_values = values.filter((_, i) => i in values)
      }
    } catch (error) {
      console.warn(PREFIX, 'widgets_values compaction failed', error)
    }
    return result
  }
}

export function attach(node) {
  if (node.comfyClass !== NODE_TYPE) return

  node.addProperty(PROP_SHOW_PASSTHROUGH, false, 'boolean')
  node.addProperty(PROP_SHOW_ORIGINAL_SIZE, false, 'boolean')

  installPassthroughVisibility(node)

  const originalOnPropertyChanged = node.onPropertyChanged
  node.onPropertyChanged = function (name, value, prevValue) {
    const result = originalOnPropertyChanged?.call(this, name, value, prevValue)
    if (name === PROP_SHOW_PASSTHROUGH) {
      applyPassthroughVisibility(this)
    } else if (name === PROP_SHOW_ORIGINAL_SIZE) {
      applyOriginalSizeVisibility(this)
    } else if (name === PROP_SHOW_GRID) {
      applyGridVisibility(this)
    } else if (name === PROP_GRID_MAX) {
      renderGrid(this)
    } else if (name === PROP_PRESETS_ENABLED) {
      applyPresetsPropertyVisibility(this)
    }
    return result
  }

  // 2026-07-20 owner ask: hidden BY DEFAULT now (M1 shipped shown-by-
  // default). A freshly created node's just-seeded properties are both
  // `false`, but seeding alone doesn't remove anything — onPropertyChanged
  // only fires on a *change*, and addProperty() is a silent assignment (see
  // file header, "Defaults flipped to OFF"). So apply the hidden state once,
  // explicitly, right here. A RELOADED node gets these same two calls too
  // (harmless — both are idempotent); configure()'s own properties-merge
  // loop runs immediately after and fires onPropertyChanged for every
  // property the saved file actually has, landing on the SAME handler above
  // — the file's saved value always wins last regardless of call order.
  applyPassthroughVisibility(node)
  applyOriginalSizeVisibility(node)

  attachSizeGrid(node)
  attachPresetsUi(node)
  // Last, so the unshift lands above widgets that are all already present.
  attachCopyFromImage(node)

  // v0.61.0 (FORMAT.md §6.5): height-first layout stamp + old-save value
  // migration, multi-image converge/reveal. The stamp is set on EVERY node
  // here (fresh saves must carry it); the migration DECISION reads the
  // incoming file inside the wrap below, so pre-stamping cannot skip it.
  node.properties[WIDGET_LAYOUT_PROP] = WIDGET_LAYOUT_CURRENT

  const originalOnConnectionsChange = node.onConnectionsChange
  node.onConnectionsChange = function (...args) {
    let result
    if (typeof originalOnConnectionsChange === 'function') {
      try {
        result = originalOnConnectionsChange.apply(this, args)
      } catch (error) {
        console.warn(PREFIX, 'original onConnectionsChange threw', error)
      }
    }
    scheduleImageConverge(this)
    return result
  }

  const originalOnConfigureV61 = node.onConfigure
  node.onConfigure = function (info) {
    // Read BEFORE the original runs -- configure merges info.properties
    // into node.properties, after which the file's absence of the stamp
    // is no longer observable.
    const fromOldLayout = info?.properties?.[WIDGET_LAYOUT_PROP] !== WIDGET_LAYOUT_CURRENT
    const result = originalOnConfigureV61?.apply(this, arguments)
    try {
      if (fromOldLayout) {
        // Old file: widgets_values was saved width-first and restored
        // positionally onto the new height-first widget order -- the two
        // VALUES arrived transposed. Swap them back, by name.
        const widthWidget = widgetByName(this, 'width')
        const heightWidget = widgetByName(this, 'height')
        if (widthWidget && heightWidget) {
          const carried = widthWidget.value
          widthWidget.value = heightWidget.value
          heightWidget.value = carried
        }
      }
      this.properties[WIDGET_LAYOUT_PROP] = WIDGET_LAYOUT_CURRENT
      convergeExtraImageInputs(this)
      revealExtraOutputs(this)
    } catch (error) {
      console.warn(PREFIX, 'v0.61.0 post-configure migration failed', error)
    }
    return result
  }

  // A FRESH node gets neither configure nor a connection event, so the
  // backend's 13 declared outputs would all stay visible. One deferred
  // converge trims to the base shape; for a reloaded node it coalesces
  // harmlessly after configure's own converge above.
  scheduleImageConverge(node)
}
