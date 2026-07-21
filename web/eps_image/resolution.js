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
export const TEXT_STRIP_H = 34 // total strip height, CSS px
export const READOUT_FONT_SIZE = 11 // px — the ONE size both lines share
export const READOUT_FONT = `${READOUT_FONT_SIZE}px ui-monospace, "SF Mono", Menlo, Consolas, monospace`
export const READOUT_FONT_STRONG = `600 ${READOUT_FONT}`
const READOUT_LINE1_BASELINE = 15 // px below the square's bottom edge
const READOUT_LINE2_BASELINE = 29
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
export function computeGridWidgetHeight(nodeWidth) {
  return Math.max(1, Number(nodeWidth) || 0) + TEXT_STRIP_H
}

/** The element's own inline CSS height for a node *nodeWidth* wide: the
 * square's side (the content width) plus the readout strip. Pure; exported
 * for tests. */
export function computeGridElementHeight(nodeWidth, margin) {
  const m = Number.isFinite(margin) ? margin : DOM_WIDGET_MARGIN_FALLBACK
  const side = Math.max(1, (Number(nodeWidth) || 0) - 2 * m)
  return side + TEXT_STRIP_H
}

/** computeGridWidgetHeight() gated on `Show grid` — the live number every
 * litegraph-facing sizing knob reports (hidden collapses to a hard 0). */
function gridWidgetHeightFor(node) {
  return isGridVisible(node) ? computeGridWidgetHeight(node.size[0]) : 0
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
    ? `${Math.round(computeGridElementHeight(node.size[0], Number(state.domWidget?.margin)))}px`
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

  // Readout: two lines of the SAME small size (owner fix 2026-07-21 — the
  // 13px dimension line was too large). Line 1: "1024 x 512" (or "auto"
  // per axis) left + "0.52 MP" RIGHT-ALIGNED on the same line, never
  // wrapped onto its own line. Line 2: the reduced aspect, muted. The
  // strip sits directly below the square (which ends at plotY + side) and
  // spans the pad's full width, matching the square's flush edges.
  const lines = getReadoutLines(disp)
  const textBaseY = plotY + side
  ctx.save()
  ctx.textBaseline = 'alphabetic'
  ctx.textAlign = 'left'
  ctx.fillStyle = colors.text
  ctx.font = READOUT_FONT_STRONG
  ctx.fillText(lines.dims, READOUT_INSET_X, textBaseY + READOUT_LINE1_BASELINE)
  ctx.fillStyle = colors.muted
  ctx.font = READOUT_FONT
  ctx.textAlign = 'right'
  ctx.fillText(lines.mp, cssW - READOUT_INSET_X, textBaseY + READOUT_LINE1_BASELINE)
  ctx.textAlign = 'left'
  ctx.fillText(lines.aspect, READOUT_INSET_X, textBaseY + READOUT_LINE2_BASELINE)
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
      cancelDrag: null
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
