/**
 * @file EPS Resolution frontend (FORMAT.md §6.5). M1 = hideable outputs.
 * M2 (this round) adds the size-grid DOM widget and flips both hideable-
 * output properties' default to OFF.
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
 * Sizing follows the premiere-bridge lesson exactly (`nodes.js`'s
 * `BAR_HEIGHT`/`attachBarWidget`, verified live there 2026-07-19):
 * `getMinHeight`/`getMaxHeight` ALONE are ignored for a small standalone DOM
 * widget on at least one rendering path, collapsing it to a ~7px sliver. The
 * robust fix sets all of: `domWidget.computeSize = (w) => [w, H]`,
 * `domWidget.computedHeight = H`, AND the element's own `style.height`/
 * `minHeight` — belt-and-suspenders, all four (plus `getMinHeight`/
 * `getMaxHeight` closures, kept for the classic-canvas `computeLayoutSize()`
 * path in `scripts/domWidget.ts`) driven from one `applyGridVisibility()` so
 * "Show grid" off (FORMAT.md's `element display:none` + `computedHeight 0` +
 * resync) reuses the identical plumbing instead of a second code path.
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

const NODE_TYPE = 'EPSResolution'
const NODE_TITLE = 'EPS Resolution'
const PREFIX = '[eps_image/resolution]'

const PROP_SHOW_PASSTHROUGH = 'Show passthrough image'
const PROP_SHOW_ORIGINAL_SIZE = 'Show original size'
const PROP_SHOW_GRID = 'Show grid'
const PROP_GRID_MAX = 'Grid max'

/** eps_image/nodes_resolution.py RETURN_NAMES — the one hideable leading
 * output, and the hideable trailing pair (order matters, see file header). */
const PASSTHROUGH_NAME = 'image'
const ORIGINAL_SIZE_NAMES = ['original_width', 'original_height']
const ORIGINAL_SIZE_TYPE = 'INT'

// --------------------------------------------------------------- utilities

function outputIndexByName(node, name) {
  return (node.outputs || []).findIndex((output) => output?.name === name)
}

function isOutputConnected(output) {
  return !!(output && Array.isArray(output.links) && output.links.length > 0)
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

/** Recompute layout after an outputs-array change: grow freely, but also
 * allow the height to shrink back down (arrange() on its own only grows). */
function resyncSize(node) {
  const computed = node.computeSize()
  node.setSize([Math.max(node.size[0], computed[0]), computed[1]])
  node.setDirtyCanvas(true, true)
}

function widgetByName(node, name) {
  return node.widgets?.find((widget) => widget && widget.name === name)
}

// ------------------------------------------------- "Show original size"

/** REAL removeOutput/addOutput, tail-only (see file header for why that's
 * the safety boundary). Idempotent: safe to call redundantly from
 * onPropertyChanged regardless of whether `configure()` already applied the
 * saved outputs array for a reloaded workflow. */
function applyOriginalSizeVisibility(node) {
  const show = node.properties?.[PROP_SHOW_ORIGINAL_SIZE] !== false
  const [widthName, heightName] = ORIGINAL_SIZE_NAMES

  if (show) {
    if (outputIndexByName(node, widthName) === -1) node.addOutput(widthName, ORIGINAL_SIZE_TYPE)
    if (outputIndexByName(node, heightName) === -1) node.addOutput(heightName, ORIGINAL_SIZE_TYPE)
    resyncSize(node)
    return
  }

  const widthIdx = outputIndexByName(node, widthName)
  const heightIdx = outputIndexByName(node, heightName)
  if (widthIdx === -1 && heightIdx === -1) return // already hidden

  const widthOut = widthIdx !== -1 ? node.outputs[widthIdx] : null
  const heightOut = heightIdx !== -1 ? node.outputs[heightIdx] : null
  if (isOutputConnected(widthOut) || isOutputConnected(heightOut)) {
    // Never silently sever an existing wire — restore the property instead.
    node.properties[PROP_SHOW_ORIGINAL_SIZE] = true
    toast(node, 'warn', 'Unwire the original-size outputs before hiding them.')
    return
  }

  // True tail removal, LIFO: height (the last RETURN_NAMES entry) first,
  // then width becomes the new tail.
  if (heightIdx !== -1) node.removeOutput(heightIdx)
  const widthIdxAfter = outputIndexByName(node, widthName)
  if (widthIdxAfter !== -1) node.removeOutput(widthIdxAfter)
  resyncSize(node)
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

function installPassthroughVisibility(node) {
  if (node._epsPassthroughPatched) return
  node._epsPassthroughPatched = true

  const originalDrawSlots = node.drawSlots
  if (typeof originalDrawSlots !== 'function') return // defensive: unrecognized litegraph build

  node.drawSlots = function (ctx, options) {
    const hide = this.properties?.[PROP_SHOW_PASSTHROUGH] === false
    const idx = hide ? outputIndexByName(this, PASSTHROUGH_NAME) : -1
    const slot = idx !== -1 ? this._concreteOutputs?.[idx] : null

    if (!slot || typeof slot.draw !== 'function') {
      originalDrawSlots.call(this, ctx, options)
      return
    }

    // Patch just this one slot's own draw() for this single synchronous
    // call, then put it back exactly as found (own-property vs. inherited —
    // see file header: never leave an own `undefined` shadowing the
    // prototype's real draw()).
    const hadOwnDraw = Object.prototype.hasOwnProperty.call(slot, 'draw')
    const original = slot.draw
    slot.draw = () => {}
    try {
      originalDrawSlots.call(this, ctx, options)
    } finally {
      if (hadOwnDraw) slot.draw = original
      else delete slot.draw
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

const GRID_H = 210 // fixed pad height, CSS px (FORMAT.md §6.5 M2: 200-220)
const GRID_MIN_SIZE = 64 // pad's logical minimum on both axes
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
const PLOT_PAD = 10
const TEXT_STRIP_H = 36

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

/** "3:2"-style reduced aspect ratio via gcd. */
function formatAspect(w, h) {
  const g = gcdInt(w, h)
  return `${Math.round(w / g)}:${Math.round(h / g)}`
}

/** "0.52 MP" / "2.1 MP" / "12 MP" — precision tapers as the number grows. */
function formatMegapixels(w, h) {
  const mp = (w * h) / 1_000_000
  const decimals = mp >= 10 ? 0 : mp >= 1 ? 1 : 2
  return `${mp.toFixed(decimals)} MP`
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

/** Applies "Show grid": element display + computeSize/computedHeight (all
 * four knobs from the file header's sizing lesson) + node resync. Also the
 * one-shot call that establishes those knobs right after the widget is
 * created, whether or not the property is actually changing. */
function applyGridVisibility(node) {
  const state = node._epsGrid
  if (!state) return
  const show = isGridVisible(node)
  const height = show ? GRID_H : 0

  state.canvas.style.display = show ? '' : 'none'
  state.canvas.style.height = `${height}px`
  state.canvas.style.minHeight = `${height}px`
  state.domWidget.computeSize = (width) => [width, height]
  state.domWidget.computedHeight = height
  state.domWidget.hidden = !show

  resyncSize(node)
  if (show) renderGrid(node)
}

/**
 * Centered SQUARE plot region within the widget (FORMAT.md §6.5 M2
 * square-cells fix, owner bug 2026-07-20: "a 1000 x 1000 grid is displayed
 * incorrectly as a rectangle because the grid elements it is made up of are
 * rectangles"). `side = min(availW, availH)` so mapX/mapY below can share
 * ONE scale (`side / span`) instead of the old two independent per-axis
 * scales — the fix, in full, IS this shared side value. The square anchors
 * to the top-left inset and is letterboxed (centered) across any LEFTOVER
 * horizontal space, since `availW > availH` is the common case (a wide
 * node, GRID_H's fixed 210px height budget). Shared by drawGrid() (so the
 * visual gridlines/diagonal/crosshair are square) AND attachGridDrag()'s
 * pointer mapping (so a drag along the visible diagonal actually produces
 * width == height) — one function, two callers, so they can never drift
 * apart into "looks square but drags rectangular" or vice versa.
 */
function getPlotRect(cssW, cssH) {
  const availW = Math.max(1, cssW - PLOT_PAD * 2)
  const availH = Math.max(1, cssH - PLOT_PAD * 2 - TEXT_STRIP_H)
  const side = Math.max(1, Math.min(availW, availH))
  return {
    plotX: PLOT_PAD + (availW - side) / 2, // letterbox: center the square in the leftover width
    plotY: PLOT_PAD, // anchored to the top; the readout strip stays directly below (TEXT_STRIP_H)
    side
  }
}

/**
 * Draws the pad's contents: gridlines every GRIDLINE_STEP, a faint 1:1
 * diagonal, a crosshair + dot at the current target, and a compact
 * "W x H" / "aspect · MP" readout. All colors come from readThemeColors()
 * so the pad reads on both Comfy themes without any light/dark branching.
 * The plot itself is the centered SQUARE from getPlotRect() — mapX/mapY
 * share one scale, so a 1000x1000 target sits on the true 45° diagonal and
 * every gridline cell is visually square, not just numerically square.
 */
function drawGrid(node, ctx, cssW, cssH) {
  const canvas = node._epsGrid.canvas
  const colors = readThemeColors(canvas)
  const gridMax = getGridMax(node)
  const disp = computeDisplayWH(node)

  const { plotX, plotY, side } = getPlotRect(cssW, cssH)
  const span = Math.max(1, gridMax - GRID_MIN_SIZE)
  const mapX = (v) => plotX + clamp01((v - GRID_MIN_SIZE) / span) * side
  const mapY = (v) => plotY + clamp01((v - GRID_MIN_SIZE) / span) * side

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

  // Crosshair through the current target.
  const tx = mapX(disp.dispW)
  const ty = mapY(disp.dispH)
  ctx.save()
  ctx.strokeStyle = ACCENT_COLOR
  ctx.globalAlpha = 0.45
  ctx.lineWidth = 1
  ctx.beginPath()
  ctx.moveTo(Math.round(tx) + 0.5, plotY)
  ctx.lineTo(Math.round(tx) + 0.5, plotY + side)
  ctx.moveTo(plotX, Math.round(ty) + 0.5)
  ctx.lineTo(plotX + side, Math.round(ty) + 0.5)
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

  // Compact readout: "1024 x 512" (or "auto" per axis), then a muted
  // "2:1 · 0.52 MP" line. Anchored at PLOT_PAD (the widget's own left
  // inset), NOT the letterboxed square's `plotX` — the strip reads as a
  // stable full-width bar directly below the square regardless of how much
  // horizontal margin centering leaves on a wide node.
  const wLabel = disp.wAuto ? 'auto' : String(Math.round(disp.rawW))
  const hLabel = disp.hAuto ? 'auto' : String(Math.round(disp.rawH))
  const textBaseY = plotY + side
  ctx.save()
  ctx.textBaseline = 'alphabetic'
  ctx.fillStyle = colors.text
  ctx.font = '600 13px ui-monospace, "SF Mono", Menlo, Consolas, monospace'
  ctx.fillText(`${wLabel} x ${hLabel}`, PLOT_PAD, textBaseY + 18)
  ctx.fillStyle = colors.muted
  ctx.font = '11px ui-monospace, "SF Mono", Menlo, Consolas, monospace'
  ctx.fillText(
    `${formatAspect(disp.dispW, disp.dispH)}  ·  ${formatMegapixels(disp.dispW, disp.dispH)}`,
    PLOT_PAD,
    textBaseY + 33
  )
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
  const rect = canvas.getBoundingClientRect()
  const cssW = Math.max(1, Math.round(rect.width))
  const cssH = Math.max(1, Math.round(rect.height))
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
    drawGrid(node, ctx, cssW, cssH)
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
 * getPlotRect() — the SAME centered-square region drawGrid() paints — so a
 * drag along the visible 45° diagonal lands on width == height even
 * without Shift, and Shift's forced equality survives snapping exactly
 * (both axes run the identical snapTo(), so equal inputs stay equal).
 */
function attachGridDrag(node, canvasEl) {
  let drag = null // { pointerId, aspect, startX, startY }

  const applyFromEvent = (event) => {
    const rect = canvasEl.getBoundingClientRect()
    const gridMax = getGridMax(node)
    const span = Math.max(1, gridMax - GRID_MIN_SIZE)
    const { plotX, plotY, side } = getPlotRect(rect.width, rect.height)
    const x = clamp(event.clientX - rect.left, 0, rect.width)
    const y = clamp(event.clientY - rect.top, 0, rect.height)

    // Inverse of drawGrid()'s mapX/mapY: same plot origin, same single
    // side/span scale for both axes (the square-cells fix) — a pointer
    // position outside the letterboxed square (in its margin) clamps to
    // the nearest edge value via clamp01, exactly like the visual pad edge.
    let w = GRID_MIN_SIZE + clamp01((x - plotX) / side) * span
    let h = GRID_MIN_SIZE + clamp01((y - plotY) / side) * span

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
    window.removeEventListener('pointermove', onMove)
    window.removeEventListener('pointerup', endDrag)
    window.removeEventListener('pointercancel', endDrag)
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
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', endDrag)
    window.addEventListener('pointercancel', endDrag)
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

    const domWidget = node.addDOMWidget(GRID_WIDGET_NAME, GRID_WIDGET_TYPE, canvasEl, {
      hideOnZoom: true,
      serialize: false,
      getMinHeight: () => (isGridVisible(node) ? GRID_H : 0),
      getMaxHeight: () => (isGridVisible(node) ? GRID_H : 0)
    })
    // Same two independent non-serialization flags as notebook.js's
    // attachDomWidget()/premiere-bridge's attachBarWidget() — see either
    // file's header for why both are needed. Grid state derives entirely
    // from the width/height widgets; nothing new serializes here.
    domWidget.serialize = false
    domWidget.serializeValue = () => undefined

    node._epsGrid = { canvas: canvasEl, domWidget, resizeObserver: null, cancelDrag: null }

    node.addProperty(PROP_SHOW_GRID, true, 'boolean')
    node.addProperty(PROP_GRID_MAX, GRID_MAX_DEFAULT, 'number')

    applyGridVisibility(node) // establishes computeSize/computedHeight/element height up front

    node._epsGrid.cancelDrag = attachGridDrag(node, canvasEl)

    if (typeof ResizeObserver === 'function') {
      const observer = new ResizeObserver(() => renderGrid(node))
      observer.observe(canvasEl)
      node._epsGrid.resizeObserver = observer
    } // else: renderGrid() always re-reads getBoundingClientRect() at draw
    // time, so anything else that triggers a repaint (widget edits,
    // configure) still draws at the correct, current width.

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

    // configure() restores widgets_values with a bare assignment (no
    // callback — see file header), so a reloaded workflow needs its own
    // repaint hook; also re-applies Show grid/Grid max defensively in case
    // ordering ever left them stale.
    const originalOnConfigure = node.onConfigure
    node.onConfigure = function (info) {
      let result
      try {
        result = originalOnConfigure?.call(this, info)
      } finally {
        applyGridVisibility(this)
        renderGrid(this)
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
      return originalOnRemoved?.apply(this, args)
    }
  } catch (error) {
    console.warn(PREFIX, 'size grid setup failed; typed width/height fields remain usable', error)
  }
}

// --------------------------------------------------------------- lifecycle

/** Frontend-only one-time setup: inject the grid's stylesheet once. */
export function init() {
  injectGridStyles()
}

/** Per-node-instance attach; no-op unless node is EPSResolution. */
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
}
