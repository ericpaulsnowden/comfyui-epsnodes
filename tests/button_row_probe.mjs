// Node probe for web/eps_image/button_row.js -- the shared side-by-side button
// row (owner ask 2026-10-05). Driven by tests/test_button_row_js.py.

import { FakeEl, findAll, fire } from './fake_dom.mjs'
import { installEnvironment, makeNode, FakeWidget } from './fake_resolution_node.mjs'

const dom = installEnvironment()
FakeEl.prototype.blur = function () {
  this.blurred = (this.blurred || 0) + 1
}
const { app } = await import('./scripts/app.js')
const rows = await import('./extensions/comfyui-epsnodes/eps_image/button_row.js')

const out = {}
const calls = []
let captures = 0
app.extensionManager = {
  workflow: { activeWorkflow: { changeTracker: { captureCanvasState: () => captures++ } } }
}

const node = () => makeNode({ widgets: [] })
const click = (button, extra = { clientX: 5, clientY: 6, type: 'click' }) => fire(button, 'click', extra)

// ---- the pure height arithmetic
out.heights = {
  default: rows.buttonRowReportedHeight(rows.BUTTON_ROW_ELEMENT_HEIGHT, rows.BUTTON_ROW_MARGIN),
  tenMargin: rows.buttonRowReportedHeight(22, 10),
  zeroMargin: rows.buttonRowReportedHeight(20, 0),
  floor: rows.buttonRowReportedHeight(0, 0),
  constants: [rows.BUTTON_ROW_ELEMENT_HEIGHT, rows.BUTTON_ROW_MARGIN, rows.BUTTON_ROW_WIDGET_TYPE]
}

// ---- one row, two buttons
const n = node()
const row = rows.addButtonRow(n, 'demo_row', [
  { key: 'a', label: 'Alpha', title: 'first', onClick: (event) => calls.push(['a', event.clientX, event.clientY]) },
  { key: 'b', label: 'Beta', title: 'second', disabled: true, onClick: () => calls.push(['b']) },
  { key: 'c', label: 'Gamma', onClick: () => { throw new Error('boom') } }
])
const w = row.widget
out.structure = {
  widgetName: w.name,
  widgetType: w.type,
  onNodeAtTail: n.widgets.at(-1) === w,
  elementTag: row.element.tag,
  elementClass: row.element.className,
  childTags: row.element.children.map((c) => c.tag),
  labels: row.element.children.map((c) => c.textContent),
  titles: row.element.children.map((c) => c.title),
  buttonTypes: row.element.children.map((c) => c.type),
  disabled: row.element.children.map((c) => c.disabled),
  keys: Object.keys(row.buttons),
  buttonsAreTheSameElements: Object.values(row.buttons).every((b, i) => b === row.element.children[i]),
  style: { ...row.element.style },
  reportedHeight: row.reportedHeight
}
out.flags = {
  serialize: w.serialize,
  optionSerialize: w.options.serialize,
  hideInPanel: w.options.hideInPanel,
  hideOnZoom: w.options.hideOnZoom,
  margin: w.margin,
  serializeValue: String(w.serializeValue()),
  ownLayoutShadow: Object.prototype.hasOwnProperty.call(w, 'computeLayoutSize') && w.computeLayoutSize === undefined,
  typeofLayout: typeof w.computeLayoutSize,
  prototypeHasLayout: typeof Object.getPrototypeOf(w).computeLayoutSize === 'function',
  computeSizeNoArg: w.computeSize(),
  computeSizeArg: w.computeSize(300),
  computedHeight: w.computedHeight,
  getMin: w.options.getMinHeight(),
  getMax: w.options.getMaxHeight()
}
// the stride a row takes in classic layout: _arrangeWidgets adds 4
n.size = [210, 100]
out.nodeBudget = { computeSize: n.computeSize(), arrangeStride: w.computeSize()[1] + 4 }

// ---- clicks
click(row.buttons.a, { clientX: 321, clientY: 123, type: 'click' })
const capturesAfterA = captures
click(row.buttons.b) // disabled -> ignored
click(row.buttons.c) // throws -> contained
out.clicks = {
  calls: structuredClone(calls),
  capturesAfterOneClick: capturesAfterA,
  capturesAfterThree: captures, // disabled click does NOT capture; the throwing one still does
  blurred: Object.fromEntries(Object.entries(row.buttons).map(([k, b]) => [k, b.blurred || 0]))
}
row.buttons.b.disabled = false
click(row.buttons.b)
out.clicks.afterEnable = structuredClone(calls)

// ---- hide / show
const optionsBefore = w.options
row.setHidden(true)
const hidden = { widget: w.hidden, option: w.options.hidden, display: row.element.style.display, sameOptions: w.options === optionsBefore }
row.setHidden(false)
out.hide = { hidden, shown: { widget: w.hidden, option: w.options.hidden, display: row.element.style.display }, sameOptionsAfter: w.options === optionsBefore }

// ---- a frontend that hands back a widget WITHOUT our option bag: flags still land, in place
{
  const bare = node()
  bare.addDOMWidget = function (name, type, element) {
    const widget = new FakeWidget(type, name, undefined, {})
    widget.element = element
    widget.margin = 4
    this.widgets.push(widget)
    return widget
  }
  const r = rows.addButtonRow(bare, 'bare_row', [{ key: 'x', label: 'X', onClick() {} }])
  out.bareOptions = { optionSerialize: r.widget.options.serialize, hideInPanel: r.widget.options.hideInPanel, serialize: r.widget.serialize }
}

// ---- two rows share one stylesheet
rows.addButtonRow(node(), 'second_row', [{ key: 'k', label: 'K', onClick() {} }])
out.styles = {
  tags: findAll(dom.head, (el) => el.tag === 'style' && el.id === 'eps-button-row-style').length,
  css: findAll(dom.head, (el) => el.tag === 'style' && el.id === 'eps-button-row-style')[0]?.textContent ?? ''
}

// ---- no addDOMWidget
{
  const warned = []
  const original = console.warn
  console.warn = (...a) => warned.push(a.join(' '))
  const bare = makeNode({ widgets: [], domWidgets: false })
  const r = rows.addButtonRow(bare, 'no_dom', [{ key: 'x', label: 'X', onClick() {} }])
  console.warn = original
  out.noDom = { result: r, widgets: bare.widgets.length, warned: warned.length }
}

// ---- the undo capture's fallbacks
{
  const original = console.warn
  console.warn = () => {}
  const results = {}
  app.extensionManager = { workflow: { activeWorkflow: { changeTracker: { checkState: () => (results.checkState = true) } } } }
  rows.captureUndoState()
  app.extensionManager = { workflow: { activeWorkflow: null } }
  rows.captureUndoState()
  app.extensionManager = undefined
  rows.captureUndoState()
  app.extensionManager = { workflow: { activeWorkflow: { changeTracker: { captureCanvasState: () => { throw new Error('tracker broke') } } } } }
  rows.captureUndoState()
  results.neverThrew = true
  console.warn = original
  out.undoFallbacks = results
}

process.stdout.write(JSON.stringify(out) + '\n')
