// A fake litegraph NODE good enough to run EPS Resolution's REAL, unmodified
// `attach()` (web/eps_image/resolution.js) under Node, and to model how the
// frontend serialises and restores it. Used by tests/resolution_rows_probe.mjs
// (driven from tests/test_resolution_rows_js.py).
//
// There is no jsdom or browser harness in this repo, so this builds just the
// primitives `attach()` touches. What it models EXACTLY (copied from the
// frontend 1.52.7 sources, `lib/litegraph/src/LGraphNode.ts`, because every
// test here exists to catch a mismatch with them):
//
// - `serialize()`: `widgets_values[i] = value` at the widget's RAW index in
//   `node.widgets`, `continue`-ing past any `widget.serialize === false` -- so
//   a skipped widget before a kept one leaves a HOLE -- plus the by-name copy
//   `widgets_values_named`. `onSerialize(o)` runs last, as in the frontend.
// - `configure(info)`: restores with a SEPARATE counter that skips
//   `serialize === false` widgets (NOT the raw index), a bare assignment with
//   no callback, stopping when the saved array runs out. `onConfigure(info)`
//   is the very last act.
// - `computeSize()` / `_arrangeWidgets`'s height rules: a widget with
//   `computeSize` is fixed at `computeSize()[1] + 4`, else one with
//   `computeLayoutSize` grows, else 20 + 4.
// - A DOM widget (`addDOMWidget`) whose class defines `computeLayoutSize` on
//   its PROTOTYPE (as `DOMWidgetImpl` does) and whose `options` is a fresh
//   copy `{hideOnZoom: true, ...options}`; `margin` is read from `options`.
// - The two renderers' way of writing a picked value: classic
//   (`BaseWidget.setValue`: skip when equal, set, then `callback(value,
//   canvas, node, pos, event)`) and Nodes 2.0 (`createWidgetUpdateHandler`:
//   set, then `callback(value, canvas, node)` -- no event).
//
// What it does NOT model: rendering, hit-testing, the Vue store's reactivity
// (the Vue-side reactivity claims are rig-UNCONFIRMED and listed in the
// round report), or anything litegraph does that `attach()` does not touch.

import { FakeEl, installFakeDom, findAll, fire } from './fake_dom.mjs'

/** The widgets the BACKEND declares, in the order `INPUT_TYPES` yields them
 * (eps_image/nodes_resolution.py): required height, width, resize_method,
 * interpolation, multiple_of; optional presets, ratio. */
export const BACKEND_RATIO_OPTIONS = ['none', '1:1', '5:4', '4:5', '4:3', '3:4', '16:9', '9:16']

export function installEnvironment() {
  const dom = installFakeDom()
  // The three DOM methods the self-owned text dialog uses that the shared fake
  // does not have (added here, not in fake_dom.mjs, so the other suites that
  // share that file are untouched).
  FakeEl.prototype.append ??= function (...children) {
    for (const child of children) this.appendChild(child)
  }
  FakeEl.prototype.select ??= function () {}
  FakeEl.prototype.click ??= function () {
    fire(this, 'click')
  }
  // The grid's pointer drag registers window listeners only on pointerdown;
  // the fake `window` has none, so give it inert ones.
  globalThis.window.addEventListener = () => {}
  globalThis.window.removeEventListener = () => {}
  globalThis.window.prompt = () => null
  globalThis.window.devicePixelRatio = 1
  globalThis.getComputedStyle = () => ({ getPropertyValue: () => '' })
  return dom
}

export class FakeWidget {
  constructor(type, name, value, options = {}, callback) {
    this.type = type
    this.name = name
    this.value = value
    this.options = options
    if (callback) this.callback = callback
    this.y = 0
  }
}

/** Mirrors `DOMWidgetImpl`: `computeLayoutSize` lives on the PROTOTYPE, the
 * options object is a fresh copy, and `margin` comes from it. */
class FakeDOMWidget extends FakeWidget {
  constructor(name, type, element, options) {
    super(type, name, undefined, { hideOnZoom: true, ...options })
    this.element = element
  }
  get margin() {
    return this.options.margin ?? 10
  }
  computeLayoutSize() {
    const minHeight = this.options.getMinHeight?.()
    return { minHeight: Number.isNaN(minHeight) || minHeight == null ? 50 : minHeight, minWidth: 0 }
  }
}

export function makeBackendWidgets() {
  const number = (name, value, extra = {}) =>
    new FakeWidget('number', name, value, { min: 0, max: 16384, step: 10, step2: 1, ...extra })
  return [
    number('height', 1024),
    number('width', 1024),
    new FakeWidget('combo', 'resize_method', 'stretch', {
      values: ['stretch', 'keep aspect (fit)', 'crop to fill', 'pad']
    }),
    new FakeWidget('combo', 'interpolation', 'bilinear', {
      values: ['nearest', 'bilinear', 'bicubic', 'area', 'lanczos']
    }),
    number('multiple_of', 0, { max: 1024 }),
    new FakeWidget('text', 'presets', '[]', { hidden: true }),
    new FakeWidget('combo', 'ratio', 'none', { values: BACKEND_RATIO_OPTIONS.slice() })
  ]
}

/**
 * A fake EPSResolution node, fresh from the constructor (backend widgets and
 * the 13 backend outputs only) -- `attach(node)` is the caller's job, exactly
 * as `nodeCreated` is the frontend's.
 * @param {{widgets?: object[], domWidgets?: boolean}} [opts]
 */
export function makeNode(opts = {}) {
  const node = {
    comfyClass: 'EPSResolution',
    type: 'EPSResolution',
    title: 'EPS Resolution',
    properties: {},
    size: [210, 120],
    flags: {},
    inputs: [{ name: 'image', type: 'IMAGE', link: null }],
    outputs: [
      'image',
      'resized_image',
      'width',
      'height',
      'original_width',
      'original_height',
      ...Array.from({ length: 7 }, (_, i) => `resized_${i + 2}`)
    ].map((name) => ({ name, type: name.includes('image') || name.startsWith('resized') ? 'IMAGE' : 'INT', links: null })),
    widgets: opts.widgets ?? makeBackendWidgets(),
    graph: { setDirtyCanvas() {}, _nodes: [] },
    dirty: 0,
    collapsed: false,
    serialize_widgets: true,
    id: 7
  }

  node.addProperty = function (name, value) {
    this.properties[name] = value
  }
  node.setDirtyCanvas = function () {
    this.dirty += 1
  }
  node.addWidget = function (type, name, value, callback, options) {
    const widget = new FakeWidget(type, name, value, { ...(options || {}) }, callback)
    this.widgets.push(widget)
    return widget
  }
  if (opts.domWidgets !== false) {
    node.addDOMWidget = function (name, type, element, options = {}) {
      const widget = new FakeDOMWidget(name, type, element, options)
      this.widgets.push(widget)
      return widget
    }
  }
  node.addOutput = function (name, type) {
    this.outputs.push({ name, type, links: null })
  }
  node.removeOutput = function (index) {
    this.outputs.splice(index, 1)
  }
  node.addInput = function (name, type) {
    this.inputs.push({ name, type, link: null })
  }
  node.removeInput = function (index) {
    this.inputs.splice(index, 1)
  }
  node.setSize = function (size) {
    this.size = [size[0], size[1]]
    this.onResize?.(this.size)
  }
  node.isWidgetVisible = function (widget) {
    return !(this.collapsed || widget.hidden)
  }

  /** `LGraphNode.computeSize()` -- the widget-height rules only (see header). */
  node.computeSize = function () {
    const size = [210, 0]
    const rows = Math.max(this.inputs.length, this.outputs.length, 1)
    size[1] = rows * 20
    let widgetsHeight = 0
    for (const widget of this.widgets) {
      if (!this.isWidgetVisible(widget)) continue
      let height
      if (widget.computeSize) height = widget.computeSize(size[0])[1]
      else if (widget.computeLayoutSize) height = widget.computeLayoutSize(this).minHeight
      else height = 20
      widgetsHeight += height + 4
    }
    widgetsHeight += 8
    size[1] += widgetsHeight
    size[1] += 6
    return size
  }

  /** `LGraphNode.serialize()` -- widgets_values by RAW index (header). */
  node.serialize = function () {
    const out = { id: this.id, type: this.type, properties: JSON.parse(JSON.stringify(this.properties)) }
    const { widgets } = this
    if (widgets?.length && this.serialize_widgets) {
      out.widgets_values = []
      out.widgets_values_named = {}
      for (const [i, widget] of widgets.entries()) {
        if (widget.serialize === false) continue
        const value = widget.value
        const serialised =
          value != null && typeof value === 'object' ? JSON.parse(JSON.stringify(value)) : (value ?? null)
        out.widgets_values[i] = serialised
        out.widgets_values_named[widget.name] = serialised
      }
    }
    if (this.onSerialize?.(out)) {
      console.warn("node onSerialize shouldn't return anything")
    }
    return out
  }

  /** `LGraphNode.configure(info)` -- the restore loop (header). */
  node.configure = function (info) {
    if (info.properties) {
      for (const key of Object.keys(info.properties)) {
        this.properties[key] = info.properties[key]
        this.onPropertyChanged?.(key, info.properties[key])
      }
    }
    if (this.widgets && info.widgets_values) {
      let i = 0
      for (const widget of this.widgets ?? []) {
        if (widget.serialize === false) continue
        if (i >= info.widgets_values.length) break
        widget.value = info.widgets_values[i++]
      }
    }
    this.onConfigure?.(info)
  }

  return node
}

// -------------------------------------------------- how a value gets written

/** Classic canvas: `BaseWidget.setValue` -- no-op when equal, else set and
 * call `callback(value, canvas, node, pos, event)`. */
export function classicPick(node, name, value, event = { clientX: 40, clientY: 60, type: 'click' }) {
  const widget = node.widgets.find((w) => w.name === name)
  if (value === widget.value) return
  widget.value = value
  widget.callback?.(widget.value, null, node, [0, 0], event)
}

/** Nodes 2.0: `createWidgetUpdateHandler` -- set the value, then
 * `callback(value, canvas, node)`: NO event. */
export function vuePick(node, name, value) {
  const widget = node.widgets.find((w) => w.name === name)
  widget.value = value
  widget.callback?.(value, null, node)
}

export const widgetNamed = (node, name) => node.widgets.find((w) => w.name === name)
export const values = (node) => Object.fromEntries(
  node.widgets.filter((w) => w.serialize !== false).map((w) => [w.name, w.value])
)

/** The real `<button>` with this label inside a row widget's element. */
export function rowButton(widget, label) {
  const found = findAll(widget.element, (el) => el.tag === 'button' && el.textContent === label)
  return found[0] ?? null
}

/** A click, delivered to the button's own listeners, carrying a real-looking
 * MouseEvent so the Save dialog test can check it is forwarded. */
export function click(button, extra = { clientX: 321, clientY: 123, type: 'click' }) {
  return fire(button, 'click', extra)
}

/** Lets the setTimeout(0) attach() schedules (image converge) and the preset
 * fetch settle. */
export const settle = () => new Promise((resolve) => setTimeout(resolve, 5))
