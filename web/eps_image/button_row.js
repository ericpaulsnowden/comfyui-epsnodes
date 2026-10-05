/**
 * @file A row of equal-width buttons, side by side, as ONE node widget.
 *
 * Owner ask 2026-10-05 (EPS Resolution): "Save and Delete should be next to
 * each other not stacked" and "next to the copy from image button there
 * should be a 'rotate' button". A litegraph `button` widget is always one
 * full-width row -- two of them stack, they can never share a line -- so a
 * side-by-side pair has to be a DOM widget holding real HTML buttons. This
 * module is that ONE widget, built once and shared by both rows (EPS
 * Resolution's `Save | Delete` and `copy from image | rotate`), so the two
 * can never drift apart on the details below.
 *
 * ---- What a row is, and what it must NOT be ----
 *
 * - **Never saved, never queued.** Both serialize flags, and they are NOT
 *   interchangeable (`utils/executionUtil.ts` says so in as many words):
 *   `widget.options.serialize === false` keeps the row out of the API PROMPT
 *   (rig-caught 2026-08-14: a button with only the other flag shipped phantom
 *   `"Save"`/`"Delete"` inputs in every queued prompt), and
 *   `widget.serialize === false` keeps it out of the workflow FILE
 *   (`LGraphNode.ts`'s `widgets_values`). `serializeValue` returns
 *   `undefined` as the third belt. `options` is mutated IN PLACE, never
 *   replaced: Nodes 2.0 keeps a reference to the ORIGINAL object (the widget
 *   store's `_state.options`), so a replacement is invisible to it.
 * - **Not in the right-hand Properties panel** (`hideInPanel`, Nodes 2.0
 *   audit V-10) -- it is plumbing, not a parameter.
 * - **A fixed height in BOTH renderers.** Classic: `computeSize` (+
 *   `computedHeight`, `getMinHeight`/`getMaxHeight`) and the element's own
 *   CSS height -- the pack's proven fixed-DOM-row shape (`frame_saver.js`'s
 *   `attachFixedWidget`, `cross_sweep.js`'s readout): `getMinHeight`/
 *   `getMaxHeight` ALONE are ignored for a small standalone DOM widget and
 *   it collapses to a ~7px sliver. Nodes 2.0 ignores all of those knobs and
 *   boxes a DOM widget in a flex column whose children get `flex: 1` -- so
 *   the element carries `flex: 0 0 auto` and an explicit CSS height (else it
 *   stretches to fill the node), and `computeLayoutSize` is SHADOWED to
 *   `undefined` on the widget. That last one is the subtle one: Nodes
 *   2.0 reads `typeof widget.computeLayoutSize === 'function'` to decide
 *   whether a widget's grid row is `auto` (it GROWS to fill spare height) or
 *   `min-content` (it keeps its own height), and `DOMWidgetImpl` defines that
 *   method on its prototype, so every DOM widget would otherwise be an
 *   expanding row (audit V-07). Classic's `_arrangeWidgets` and
 *   `computeSize()` both test `widget.computeSize` FIRST, so the shadow
 *   changes nothing there.
 * - **Clicks use `click`, not `pointerdown`.** Nodes 2.0's `WidgetDOM` stops
 *   `pointerdown`/`pointermove`/`pointerup` from propagating (so they can't
 *   drag the node) but not `click`, and a real button needs `click` for
 *   keyboard activation anyway. The handler gets the real `MouseEvent`:
 *   `canvas.prompt` positions its box off `event.clientX/Y`, and a native
 *   litegraph button callback hands it nothing under Nodes 2.0.
 * - **Hide/show writes every flag.** `widget.hidden` (classic), `widget.
 *   options.hidden` (Nodes 2.0; written in place), AND the element's own
 *   `display` -- Nodes 2.0 1.52.7 does not observe either flag after the
 *   first render (audit V-02 case B), so without the `display` the Presets
 *   property would hide the row in classic only.
 *
 * ---- Undo ----
 *
 * A native litegraph button's click is followed by `LGraphCanvas.
 * processMouseUp`, which the frontend's ChangeTracker wraps to snapshot the
 * graph -- so a button that changes a widget value became one undo step for
 * free. An HTML button's `mouseup` fires BEFORE its `click`, so the
 * snapshot would be taken a moment too early and the change would be folded
 * into whatever the user does next. So after every click handler this asks
 * the active workflow's ChangeTracker to capture NOW (`captureCanvasState`,
 * the same call the pack's `runAsOneUndoStep` makes in `broadcast_graph.js`;
 * `checkState` on older builds): one click, one undo step. That file's
 * helper is not imported because it lives behind a dozen unrelated modules.
 *
 * ---- Height arithmetic (why `reported = element + 2*margin - 4`) ----
 *
 * The classic overlay boxes a DOM widget at `[width - 2*margin, computedHeight
 * - 2*margin]` (DomWidgets.vue), and `_arrangeWidgets` sets `computedHeight =
 * computeSize()[1] + 4`. For the VISIBLE box to equal the element's own CSS
 * height exactly, `computeSize()[1]` must be `element + 2*margin - 4`; the
 * node then reserves `element + 2*margin` for the row. The default margin is
 * 10 (a 22px button would take 42px of node height, nearly double a native
 * widget row), so rows use a small explicit `margin` option -- honoured by
 * the classic overlay, a no-op under Nodes 2.0 (which lays the row out in its
 * own grid).
 *
 * Pure bits (`buttonRowReportedHeight`) are exported for tests; the DOM
 * bits are exercised with `tests/fake_dom.mjs` in `tests/test_button_row_js.py`.
 */

import { app } from '../../../scripts/app.js'

/** Widget `type` for a row. Deliberately NOT one of core's aliased types
 * (`button`, `combo`, `text`, ... -- the Nodes 2.0 registry would then
 * render core's own Vue component INSTEAD of this element); any other type
 * string falls through to `WidgetDOM`, which mounts our element. */
export const BUTTON_ROW_WIDGET_TYPE = 'eps_button_row'

/** The buttons' own height, in CSS px. A native litegraph widget is 20px
 * tall inside a 24px stride; 22 keeps an 11px label (the pad readout's own
 * size -- and small enough that `copy from image` fits half a minimum-width
 * node) legible without making a row feel heavier than the combo above it. */
export const BUTTON_ROW_ELEMENT_HEIGHT = 22

/** DOM-widget margin for rows (classic overlay only, see header). */
export const BUTTON_ROW_MARGIN = 4

/** `_arrangeWidgets` adds this to `computeSize()[1]` (LGraphNode.ts: `height
 * = w.computeSize()[1] + 4`); the reported height pre-compensates. */
const ARRANGE_PAD = 4

const GAP_PX = 4
const STYLE_TAG_ID = 'eps-button-row-style'
let stylesInjected = false

// House look: the same theme CSS variables (with fallbacks) the pad, the
// Notebook and the pack's other DOM controls use, so it reads on both Comfy
// themes without any light/dark branching.
const CSS_TEXT = `
.eps-btn-row {
  display: flex;
  flex: 0 0 auto;
  align-items: stretch;
  gap: ${GAP_PX}px;
  width: 100%;
  box-sizing: border-box;
}
.eps-btn-row-btn {
  flex: 1 1 0;
  min-width: 0;
  box-sizing: border-box;
  margin: 0;
  padding: 0 4px;
  font-family: inherit;
  font-size: 11px;
  line-height: 1;
  color: var(--input-text, #ddd);
  background: var(--comfy-input-bg, #2a2a2a);
  border: 1px solid var(--border-color, #4e4e4e);
  border-radius: 4px;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.eps-btn-row-btn:hover:not(:disabled) { filter: brightness(1.3); }
.eps-btn-row-btn:active:not(:disabled) { filter: brightness(0.85); }
.eps-btn-row-btn:disabled { opacity: 0.45; cursor: default; }
`

/** Injects the row stylesheet once per page. Safe to call repeatedly. */
export function injectButtonRowStyles() {
  if (stylesInjected) return
  stylesInjected = true
  if (document.getElementById(STYLE_TAG_ID)) return
  const style = document.createElement('style')
  style.id = STYLE_TAG_ID
  style.textContent = CSS_TEXT
  document.head.appendChild(style)
}

/**
 * The height a row reports to litegraph for an element *elementHeight* px
 * tall inside a widget with *margin* (header, "Height arithmetic"). Pure;
 * exported for tests.
 * @param {number} elementHeight @param {number} margin @returns {number}
 */
export function buttonRowReportedHeight(elementHeight, margin) {
  return Math.max(1, elementHeight + 2 * margin - ARRANGE_PAD)
}

/**
 * Asks the active workflow's ChangeTracker to snapshot the graph now (header,
 * "Undo"). Never throws: no tracker, an older frontend, or a tracker that
 * refuses simply means the change is captured by the next user event instead,
 * which is what every programmatic widget write in this pack already does.
 */
export function captureUndoState() {
  try {
    const tracker = app?.extensionManager?.workflow?.activeWorkflow?.changeTracker
    if (!tracker) return
    if (typeof tracker.captureCanvasState === 'function') tracker.captureCanvasState()
    else if (typeof tracker.checkState === 'function') tracker.checkState()
  } catch (error) {
    console.warn('[eps_image/button_row] undo capture failed', error)
  }
}

/**
 * @typedef {object} ButtonSpec
 * @property {string} key  Stable id the returned `buttons` map is keyed by.
 * @property {string} label  Visible text.
 * @property {string} [title]  Native tooltip (DOM widgets are skipped by
 *   ComfyUI's own tooltip layer, which reads node-def tooltips only).
 * @property {boolean} [disabled]  Initial disabled state.
 * @property {(event: MouseEvent) => unknown} onClick  Called with the REAL
 *   click event. Not called while the button is disabled.
 */

/**
 * @typedef {object} ButtonRow
 * @property {object} widget  The DOM widget (already on `node.widgets`, at
 *   the tail -- the caller moves it where it belongs).
 * @property {HTMLElement} element  The row container.
 * @property {Record<string, HTMLButtonElement>} buttons  Buttons by `key`.
 * @property {number} reportedHeight  What `widget.computeSize()` reports.
 * @property {(hidden: boolean) => void} setHidden  Hide/show the whole row.
 */

/**
 * Adds a row of equal-width buttons to *node* as one DOM widget (see the file
 * header for everything this guarantees). Returns `null` when this frontend
 * has no `addDOMWidget`, so the caller can degrade (the pack's fail-soft
 * posture: a missing row must never break the node).
 *
 * @param {object} node  The litegraph node.
 * @param {string} name  The widget's name -- must be UNIQUE on the node
 *   (Nodes 2.0 1.53+ auto-renames duplicates to `name#1`).
 * @param {ButtonSpec[]} buttons
 * @param {{elementHeight?: number, margin?: number}} [options]
 * @returns {ButtonRow | null}
 */
export function addButtonRow(node, name, buttons, options = {}) {
  if (typeof node?.addDOMWidget !== 'function') {
    console.warn('[eps_image/button_row] this ComfyUI frontend has no addDOMWidget; row not added')
    return null
  }
  injectButtonRowStyles()

  const elementHeight = options.elementHeight ?? BUTTON_ROW_ELEMENT_HEIGHT
  const margin = options.margin ?? BUTTON_ROW_MARGIN
  const reportedHeight = buttonRowReportedHeight(elementHeight, margin)

  const element = document.createElement('div')
  element.className = 'eps-btn-row'
  // Nodes 2.0: an explicit height AND `flex: 0 0 auto`, or `WidgetDOM`'s
  // `*:flex-1` stretches this to fill the node's spare height (header).
  element.style.height = `${elementHeight}px`
  element.style.minHeight = `${elementHeight}px`
  element.style.flex = '0 0 auto'

  const made = {}
  for (const spec of buttons) {
    const button = document.createElement('button')
    button.type = 'button'
    button.className = 'eps-btn-row-btn'
    button.textContent = spec.label
    if (spec.title) button.title = spec.title
    button.disabled = !!spec.disabled
    button.addEventListener('click', (event) => {
      if (button.disabled) return
      try {
        spec.onClick?.(event)
      } catch (error) {
        console.warn('[eps_image/button_row]', `"${spec.label}" click handler threw`, error)
      }
      captureUndoState()
      // A focused button would send bare keystrokes (a stray "r" is the
      // frontend's Refresh shortcut) to the button instead of the canvas --
      // a native litegraph button never holds focus, so neither may this.
      if (typeof button.blur === 'function') button.blur()
    })
    element.appendChild(button)
    made[spec.key] = button
  }

  const widget = node.addDOMWidget(name, BUTTON_ROW_WIDGET_TYPE, element, {
    hideOnZoom: true,
    // Nodes 2.0: the right-hand Properties panel lists every widget not
    // flagged hideInPanel (frontend-contract §2.2 / audit-epsnodes V-10).
    hideInPanel: true,
    // The API-PROMPT flag (executionUtil.ts). See the header: not the same
    // as `widget.serialize` below.
    serialize: false,
    margin,
    getMinHeight: () => reportedHeight,
    getMaxHeight: () => reportedHeight
  })
  if (!widget) return null

  // The workflow-FILE flag (LGraphNode.ts), and the value hook as a third
  // belt. `options` is mutated in place (never replaced -- header) in case a
  // frontend build handed back an options object without our bag.
  widget.serialize = false
  widget.serializeValue = () => undefined
  if (widget.options) {
    widget.options.serialize = false
    widget.options.hideInPanel = true
  }

  // Classic fixed height (header, "Height arithmetic") ...
  widget.computeSize = (width) => [width ?? node.size?.[0] ?? 0, reportedHeight]
  widget.computedHeight = reportedHeight + ARRANGE_PAD
  // ... and Nodes 2.0's: shadow the prototype's method with an OWN property
  // whose value is undefined, so `typeof widget.computeLayoutSize` is not
  // 'function' and the row is `min-content`, not an expanding `auto` row.
  widget.computeLayoutSize = undefined

  const setHidden = (hidden) => {
    const flag = !!hidden
    widget.hidden = flag
    if (widget.options) widget.options.hidden = flag // IN PLACE -- header
    element.style.display = flag ? 'none' : ''
  }

  return { widget, element, buttons: made, reportedHeight, setHidden }
}
