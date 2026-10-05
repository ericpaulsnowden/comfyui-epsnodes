// Node probe for EPS Resolution's v1.6 round (owner asks 2026-10-05): the
// typed `custom…` ratio, the `rotate` button, and the two side-by-side button
// rows. Driven by tests/test_resolution_rows_js.py through a served layout
// (the real resolution.js + button_row.js + lora_library/api.js, the stubs for
// scripts/app.js and scripts/api.js, tests/fake_dom.mjs and the fake node in
// tests/fake_resolution_node.mjs).
//
// Every scenario builds a fresh node, runs the REAL attach() against it, drives
// it the way a user (or the frontend) would, and records plain data; the
// Python test asserts on it. The scenarios are grouped by letter.

import {
  FakeWidget,
  installEnvironment,
  makeNode,
  makeBackendWidgets,
  classicPick,
  vuePick,
  widgetNamed,
  rowButton,
  click,
  settle
} from './fake_resolution_node.mjs'

installEnvironment()
const { app } = await import('./scripts/app.js')
const { api } = await import('./scripts/api.js')
const m = await import('./extensions/comfyui-epsnodes/eps_image/resolution.js')

// ---- shared recording stubs ------------------------------------------------
const toasts = []
const undo = { captures: 0 }
const dialogCalls = []
const canvasPrompts = []
const windowPrompts = []
const posts = []
let dialogAnswer = null // what dialog.prompt resolves with; undefined = reject
let canvasAnswer = '__none__' // what canvas.prompt's callback is fed (sentinel = never calls back)
let windowAnswer = null

app.extensionManager = {
  toast: { add: (t) => toasts.push(t) },
  workflow: { activeWorkflow: { changeTracker: { captureCanvasState: () => undo.captures++ } } },
  dialog: {
    prompt: async (options) => {
      dialogCalls.push(options)
      if (dialogAnswer === undefined) throw new Error('dialog broke')
      return dialogAnswer
    }
  }
}
app.canvas = {
  prompt(title, value, callback, event) {
    canvasPrompts.push({ title, value, event })
    if (canvasAnswer !== '__none__') callback(canvasAnswer)
  }
}
globalThis.window.prompt = (title, value) => {
  windowPrompts.push({ title, value })
  return windowAnswer
}
globalThis.__fetch = async (route, init) => {
  if (init?.method === 'POST') {
    posts.push({ route, body: JSON.parse(init.body) })
    return {
      ok: true,
      status: 200,
      json: async () => ({ presets: { ...PRESET_STORE, [posts.at(-1).body.name]: posts.at(-1).body.values }, mtime: 2 })
    }
  }
  return { ok: true, status: 200, json: async () => ({ presets: PRESET_STORE, mtime: 1 }) }
}
const PRESET_STORE = {
  Portrait: { width: 832, height: 1216, resize_method: 'stretch', interpolation: 'bilinear', multiple_of: 0 },
  Wide: { width: 1344, height: 768, resize_method: 'crop to fill', interpolation: 'lanczos', multiple_of: 64 }
}

const resetRecords = () => {
  toasts.length = 0
  undo.captures = 0
  dialogCalls.length = 0
  canvasPrompts.length = 0
  windowPrompts.length = 0
  posts.length = 0
  dialogAnswer = null
  canvasAnswer = '__none__'
  windowAnswer = null
}

const rafQueue = []
globalThis.requestAnimationFrame = (fn) => {
  rafQueue.push(fn)
  return rafQueue.length
}
const flushRaf = () => {
  while (rafQueue.length) rafQueue.shift()()
}

async function fresh(options) {
  const node = makeNode(options)
  m.attach(node)
  await settle()
  return node
}

const val = (node, name) => widgetNamed(node, name).value
const dims = (node) => [val(node, 'width'), val(node, 'height')]
const detail = () => toasts.map((t) => `${t.severity}: ${t.detail}`)
const out = {}

// ============================================================ A. layout + flags
{
  const node = await fresh()
  const frontendOnly = (w) => w.serialize === false
  const rowWidgets = node.widgets.filter((w) => w.type === 'eps_button_row')
  const ownShadow = (w) =>
    Object.prototype.hasOwnProperty.call(w, 'computeLayoutSize') && w.computeLayoutSize === undefined
  out.layout = {
    names: node.widgets.map((w) => w.name),
    serializeFlags: Object.fromEntries(node.widgets.map((w) => [w.name, w.serialize])),
    optionSerializeFlags: Object.fromEntries(node.widgets.map((w) => [w.name, w.options?.serialize])),
    frontendOnlyNames: node.widgets.filter(frontendOnly).map((w) => w.name),
    savedValues: node.serialize().widgets_values,
    namedValues: node.serialize().widgets_values_named,
    // graphToPrompt's loop: every widget with a name whose options.serialize
    // is not false becomes an API-prompt input.
    promptInputNames: node.widgets.filter((w) => w.name && w.options?.serialize !== false).map((w) => w.name),
    rows: Object.fromEntries(
      rowWidgets.map((w) => [
        w.name,
        {
          type: w.type,
          hideInPanel: w.options.hideInPanel,
          optionSerialize: w.options.serialize,
          serialize: w.serialize,
          serializeValue: typeof w.serializeValue === 'function' ? String(w.serializeValue()) : 'none',
          ownComputeLayoutSizeShadow: ownShadow(w),
          prototypeStillHasMethod: typeof Object.getPrototypeOf(w).computeLayoutSize === 'function',
          typeofComputeLayoutSize: typeof w.computeLayoutSize,
          computeSize: w.computeSize(210),
          computedHeight: w.computedHeight,
          margin: w.margin,
          getMinHeight: w.options.getMinHeight(),
          getMaxHeight: w.options.getMaxHeight(),
          elementStyle: { ...w.element.style },
          buttons: w.element.children.map((b) => ({
            label: b.textContent,
            disabled: b.disabled,
            title: b.title,
            type: b.type,
            className: b.className
          })),
          rowClass: w.element.className
        }
      ])
    ),
    // where the node-height budget goes: a row costs reported + 4 (stride).
    nodeComputeSize: node.computeSize(),
    padIsLast: node.widgets.at(-1).name === 'eps_resolution_grid'
  }
}

// ======================= B. widgets_values: byte-identical to v1.5.0, both ways
// "The v1.5.0 build" is modelled by its widget list: ONE leading button widget
// (copy from image) + the seven backend widgets + preset combo + Save + Delete +
// the pad, every frontend widget `serialize:false`, and the same compaction
// hook. The new build must write the same bytes and read the same bytes.
{
  const V150_COMPACT = function (info) {
    const values = info?.widgets_values
    if (Array.isArray(values)) info.widgets_values = values.filter((_, i) => i in values)
  }
  const frontendStub = (name, type) => {
    const w = new FakeWidget(type, name, null, { serialize: false })
    w.serialize = false
    return w
  }
  const v150Node = () => {
    const widgets = [
      frontendStub('copy from image', 'button'),
      ...makeBackendWidgets(),
      frontendStub('preset', 'combo'),
      frontendStub('Save', 'button'),
      frontendStub('Delete', 'button'),
      frontendStub('eps_resolution_grid', 'eps_resolution_grid')
    ]
    const node = makeNode({ widgets })
    node.onSerialize = V150_COMPACT
    return node
  }
  // Arrays a PANEL could have saved: a locked ratio / multiple_of is always
  // satisfied by the numbers next to it (the panel keeps them in step on every
  // edit), so the load-time reconcile -- which runs in the real build and is
  // not part of this bare widget-list model of v1.5.0 -- has nothing to change.
  const workflowArrays = {
    freshDefaults: [1024, 1024, 'stretch', 'bilinear', 0, '[]', 'none'],
    plainEdited: [333, 777, 'crop to fill', 'lanczos', 0, '["Portrait"]', 'none'],
    lockedAndSnapped: [576, 1024, 'pad', 'area', 64, '[]', '16:9'],
    portraitPreset: [1280, 960, 'stretch', 'bilinear', 0, '[]', '3:4'],
    typedRatio: [418, 1000, 'stretch', 'bilinear', 0, '[]', '2.39:1'],
    // saved before `ratio` existed (6 values) and before `presets` (5 values)
    beforeRatio: [512, 768, 'stretch', 'bilinear', 0, '[]'],
    beforePresets: [512, 768, 'stretch', 'bilinear', 0]
  }
  const readAll = (node) => node.widgets.filter((w) => w.serialize !== false).map((w) => [w.name, w.value])
  out.compat = {}
  for (const [label, array] of Object.entries(workflowArrays)) {
    const info = () => ({
      widgets_values: array.slice(),
      properties: { eps_res_widget_layout: 2 } // the stamp every v0.61+ save carries
    })
    const fresh150 = v150Node()
    fresh150.configure(info())
    const neu = await fresh()
    neu.configure(info())
    const saved150 = fresh150.serialize().widgets_values
    const savedNew = neu.serialize().widgets_values
    // a workflow SAVED by the new build, loaded by the v1.5.0 build
    const back150 = v150Node()
    back150.configure({ widgets_values: savedNew, properties: { eps_res_widget_layout: 2 } })
    out.compat[label] = {
      input: array,
      loadedNew: readAll(neu),
      loaded150: readAll(fresh150),
      savedNew,
      saved150,
      savedNewJson: JSON.stringify(savedNew),
      saved150Json: JSON.stringify(saved150),
      newSavedLoadedBy150: readAll(back150)
    }
  }
  // A pre-v0.61 workflow (no layout stamp): width-first values, swapped back
  // by name exactly as before.
  const old = await fresh()
  old.configure({ widgets_values: [640, 480, 'stretch', 'bilinear', 0, '[]', 'none'], properties: {} })
  out.compat.preHeightFirst = { height: val(old, 'height'), width: val(old, 'width') }
}

// ============================================ C. phantom inputs after real use
{
  resetRecords()
  const node = await fresh()
  classicPick(node, 'preset', 'Portrait')
  rowButton(widgetNamed(node, 'eps_resolution_copy_row'), 'rotate')
  click(rowButton(widgetNamed(node, 'eps_resolution_copy_row'), 'rotate'))
  out.phantom = {
    promptInputNames: node.widgets.filter((w) => w.name && w.options?.serialize !== false).map((w) => w.name),
    anyForbidden: node.widgets
      .filter((w) => w.name && w.options?.serialize !== false)
      .map((w) => w.name)
      .filter((n) => ['rotate', 'Save', 'Delete', 'copy from image', 'eps_resolution_copy_row', 'eps_resolution_preset_row', 'preset'].includes(n))
  }
}

// ======================================================= D. the ratio widget
{
  resetRecords()
  const node = await fresh()
  const ratio = widgetNamed(node, 'ratio')
  const options = ratio.options
  const listBefore = options.values()
  ratio.value = '21:9'
  const listWithCustom = options.values()
  ratio.value = '16:9'
  const listWithPreset = options.values()
  ratio.value = 'banana'
  const listWithGarbage = options.values()
  ratio.value = 'custom…'
  const listWhileSentinel = options.values()
  ratio.value = 'none'
  out.ratioOptions = {
    valuesIsFunction: typeof options.values === 'function',
    sameOptionsObject: options === ratio.options,
    listBefore,
    listWithCustom,
    listWithPreset,
    listWithGarbage,
    listWhileSentinel,
    argumentsIgnored: JSON.stringify(options.values(ratio, node)) === JSON.stringify(options.values())
  }
}

// ============ E. the custom… flow (classic click event, then Nodes 2.0, no event)
{
  resetRecords()
  const node = await fresh()
  const ratio = widgetNamed(node, 'ratio')
  // --- E1 classic, extensionManager.dialog present, a good answer
  dialogAnswer = ' 2.39 : 1 '
  const clickEvent = { clientX: 11, clientY: 22, type: 'click' }
  classicPick(node, 'ratio', 'custom…', clickEvent)
  const valueRightAfterThePick = ratio.value // before the async dialog answers
  await settle()
  out.custom = {
    e1: {
      valueRightAfterThePick,
      dialogCalls: structuredClone(dialogCalls),
      valueAfter: ratio.value,
      widthHeight: dims(node),
      listAfter: ratio.options.values(),
      canvasPromptCalls: canvasPrompts.length,
      undoCaptures: undo.captures,
      toasts: detail()
    }
  }
  // --- E2 now a custom ratio is current: picking custom… again prefills it
  resetRecords()
  dialogAnswer = null // Cancel
  vuePick(node, 'ratio', 'custom…')
  const valueAfterVuePick = ratio.value
  await settle()
  out.custom.e2 = {
    valueAfterVuePick,
    prefill: dialogCalls[0]?.defaultValue,
    valueAfterCancel: ratio.value,
    toasts: detail(),
    widthHeight: dims(node)
  }
  // --- E3 invalid text: toast, previous kept
  for (const [label, typed] of [['letters', 'banana'], ['zero', '0:5'], ['empty', ''], ['negative', '-4:3'], ['three', '1:2:3']]) {
    resetRecords()
    dialogAnswer = typed
    vuePick(node, 'ratio', 'custom…')
    await settle()
    out.custom[`invalid_${label}`] = { value: ratio.value, toasts: detail(), widthHeight: dims(node) }
  }
  // --- E4 spellings normalise
  out.custom.spellings = {}
  for (const typed of ['21x9', '21 / 9', '21X9', '21×9', '2.390 : 1.0', '1.85:1', '16:9', 'none', 'NONE']) {
    resetRecords()
    dialogAnswer = typed
    vuePick(node, 'ratio', 'custom…')
    await settle()
    out.custom.spellings[typed] = ratio.value
  }
  // --- E5 retyping the current ratio is a quiet no-op (no callback storm)
  resetRecords()
  vuePick(node, 'ratio', '16:9')
  const sizeBefore = dims(node)
  dialogAnswer = '16:9'
  const callbackCalls = []
  const original = ratio.callback
  ratio.callback = function (...args) {
    callbackCalls.push(args[0])
    return original.apply(this, args)
  }
  vuePick(node, 'ratio', 'custom…')
  await settle()
  ratio.callback = original
  out.custom.retype = { callbackCalls, value: ratio.value, sizeUnchanged: JSON.stringify(sizeBefore) === JSON.stringify(dims(node)) }
}

// ----- E6 tiers: no dialog -> canvas.prompt (event forwarded in classic, null in Nodes 2.0)
{
  resetRecords()
  const node = await fresh()
  const dialog = app.extensionManager.dialog
  delete app.extensionManager.dialog
  const ratio = widgetNamed(node, 'ratio')
  canvasAnswer = '3:2'
  const clickEvent = { clientX: 99, clientY: 77, type: 'click' }
  classicPick(node, 'ratio', 'custom…', clickEvent)
  const classicPrompt = canvasPrompts[0]
  const afterClassic = ratio.value
  resetRecords()
  canvasAnswer = '5:3'
  vuePick(node, 'ratio', 'custom…')
  const vuePrompt = canvasPrompts[0]
  // tier 3: canvas.prompt missing -> window.prompt
  const canvas = app.canvas
  app.canvas = null
  resetRecords()
  windowAnswer = ' 7 / 5 '
  vuePick(node, 'ratio', 'custom…')
  const afterWindow = ratio.value
  const windowPrompt = windowPrompts[0]
  // tier 4: window.prompt throws -> the built-in DOM dialog (never a silent no-op)
  const savedWindowPrompt = globalThis.window.prompt
  globalThis.window.prompt = () => {
    throw new Error('prompt() is not supported')
  }
  resetRecords()
  vuePick(node, 'ratio', 'custom…')
  const overlays = globalThis.document.body.children.filter((c) => c.className === 'eps-res-prompt-overlay')
  out.custom.tiers = {
    classicPromptTitle: classicPrompt?.title,
    classicPromptValue: classicPrompt?.value,
    classicEventForwarded: classicPrompt?.event === clickEvent,
    afterClassic,
    vuePromptEvent: vuePrompt?.event,
    windowAnswerApplied: afterWindow,
    windowPromptValue: windowPrompt?.value,
    domDialogOpened: overlays.length === 1,
    valueWhileDomDialogOpen: ratio.value
  }
  globalThis.window.prompt = savedWindowPrompt
  app.canvas = canvas
  app.extensionManager.dialog = dialog
}

// ---- E7: a dialog that throws / rejects falls through to canvas.prompt
{
  resetRecords()
  const node = await fresh()
  dialogAnswer = undefined // dialog.prompt rejects
  canvasAnswer = '9:5'
  vuePick(node, 'ratio', 'custom…')
  await settle()
  out.custom.dialogRejects = { value: val(node, 'ratio'), canvasPromptCalls: canvasPrompts.length }
}

// ============================ F. programmatic writes (Universal State Apply path)
{
  resetRecords()
  const node = await fresh()
  const ratio = widgetNamed(node, 'ratio')
  vuePick(node, 'width', 1200)
  // Apply writes `widget.value = v; widget.callback(v, canvas, node)`
  vuePick(node, 'ratio', '2.39:1')
  const applied = { value: ratio.value, size: dims(node) }
  vuePick(node, 'ratio', '16x9') // another spelling arrives
  const respelled = ratio.value
  vuePick(node, 'ratio', 'banana') // garbage is refused
  out.apply = { applied, respelled, afterGarbage: ratio.value, toasts: detail() }
}

// ====================================== G. configure restores + re-syncs
{
  resetRecords()
  const node = await fresh()
  node.configure({ widgets_values: [800, 1000, 'stretch', 'bilinear', 0, '[]', '21x9'], properties: { eps_res_widget_layout: 2 } })
  const canonicalised = val(node, 'ratio')
  // now the committed ratio must be 21:9: custom… then Cancel puts THAT back
  dialogAnswer = null
  vuePick(node, 'ratio', 'custom…')
  await settle()
  const afterCancel = val(node, 'ratio')
  const node2 = await fresh()
  node2.configure({ widgets_values: [800, 1000, 'stretch', 'bilinear', 0, '[]', 'zzz'], properties: { eps_res_widget_layout: 2 } })
  out.restore = {
    canonicalised,
    afterCancel,
    garbageKept: val(node2, 'ratio'),
    garbageToasts: detail(),
    sentinelRestored: (() => {
      const n = node2
      n.configure({ widgets_values: [800, 1000, 'stretch', 'bilinear', 0, '[]', 'custom…'], properties: { eps_res_widget_layout: 2 } })
      return val(n, 'ratio')
    })()
  }
}

// ======================================================== H. rotate
async function rotateScenario(setup) {
  resetRecords()
  const node = await fresh()
  await setup(node)
  const row = widgetNamed(node, 'eps_resolution_copy_row')
  flushRaf()
  rafQueue.length = 0
  const before = { dims: dims(node), ratio: val(node, 'ratio'), presets: val(node, 'presets') }
  const prevCaptures = undo.captures
  click(rowButton(row, 'rotate'))
  const rafPending = rafQueue.length
  flushRaf()
  return {
    before,
    after: { dims: dims(node), ratio: val(node, 'ratio'), presets: val(node, 'presets') },
    comboValue: widgetNamed(node, 'preset').value,
    toasts: detail(),
    undoCapturesForClick: undo.captures - prevCaptures,
    repaintRequested: rafPending > 0
  }
}
out.rotate = {}
out.rotate.noRatio = await rotateScenario(async (n) => {
  vuePick(n, 'height', 720)
  vuePick(n, 'width', 1280)
})
out.rotate.lock169 = await rotateScenario(async (n) => {
  vuePick(n, 'ratio', '16:9')
  vuePick(n, 'width', 1024)
})
out.rotate.lock34 = await rotateScenario(async (n) => {
  vuePick(n, 'ratio', '3:4')
  vuePick(n, 'width', 768)
})
out.rotate.customDecimal = await rotateScenario(async (n) => {
  vuePick(n, 'ratio', '2.39:1')
  vuePick(n, 'width', 1000)
})
out.rotate.customTwice = await (async () => {
  // rotate, then rotate again: the ratio and the pair come back
  resetRecords()
  const node = await fresh()
  vuePick(node, 'ratio', '2.39:1')
  vuePick(node, 'width', 1000)
  const row = widgetNamed(node, 'eps_resolution_copy_row')
  const start = { dims: dims(node), ratio: val(node, 'ratio') }
  click(rowButton(row, 'rotate'))
  const once = { dims: dims(node), ratio: val(node, 'ratio') }
  click(rowButton(row, 'rotate'))
  return { start, once, twice: { dims: dims(node), ratio: val(node, 'ratio') } }
})()
out.rotate.square11 = await rotateScenario(async (n) => {
  vuePick(n, 'ratio', '1:1')
  vuePick(n, 'width', 640)
})
out.rotate.squareNone = await rotateScenario(async (n) => {
  vuePick(n, 'width', 512)
  vuePick(n, 'height', 512)
})
out.rotate.zeroAxis = await rotateScenario(async (n) => {
  vuePick(n, 'width', 0)
  vuePick(n, 'height', 800)
})
out.rotate.withPreset = await rotateScenario(async (n) => {
  classicPick(n, 'preset', 'Portrait') // applies 832 x 1216, selects it
})
out.rotate.withPresetAndLock = await rotateScenario(async (n) => {
  classicPick(n, 'preset', 'Portrait')
  vuePick(n, 'ratio', '4:5')
})
out.rotate.multipleOfNotResnapped = await rotateScenario(async (n) => {
  vuePick(n, 'multiple_of', 64)
  // bypass the snap with a bare write: 1000 x 500 is NOT a multiple of 64
  widgetNamed(n, 'width').value = 1000
  widgetNamed(n, 'height').value = 500
})
out.rotate.multipleOfWithLock = await rotateScenario(async (n) => {
  vuePick(n, 'multiple_of', 64)
  vuePick(n, 'ratio', '16:9')
  vuePick(n, 'width', 1024)
})
out.rotate.snapWrapperRestored = await (async () => {
  resetRecords()
  const node = await fresh()
  vuePick(node, 'height', 768)
  vuePick(node, 'width', 1280)
  vuePick(node, 'multiple_of', 64)
  click(rowButton(widgetNamed(node, 'eps_resolution_copy_row'), 'rotate'))
  const rotated = dims(node)
  vuePick(node, 'width', 1000) // a typed edit AFTER a rotate must still snap
  return { rotated, width: val(node, 'width'), suppressFlag: node._epsSuppressMultipleOfSnap }
})()

// ====================================================== I. copy from image
{
  resetRecords()
  const node = await fresh()
  const row = widgetNamed(node, 'eps_resolution_copy_row')
  click(rowButton(row, 'copy from image'))
  const unwired = detail()
  resetRecords()
  node.inputs[0].link = 5
  const wall = { id: 3, type: 'LoadImage', comfyClass: 'LoadImage', imgs: [{ naturalWidth: 800, naturalHeight: 600 }] }
  node.getInputNode = (slot) => (slot === 0 ? wall : null)
  click(rowButton(row, 'copy from image'))
  const copied = dims(node)
  const copyUndo = undo.captures
  vuePick(node, 'ratio', '4:3') // 800x600 is already 4:3
  resetRecords()
  wall.imgs = [{ naturalWidth: 1000, naturalHeight: 300 }]
  vuePick(node, 'multiple_of', 64)
  click(rowButton(row, 'copy from image'))
  out.copy = { unwired, copied, copyUndo, withLock: dims(node), toastsWithLock: detail() }
}

// ================================================= J. the Save | Delete row
{
  resetRecords()
  const node = await fresh()
  const row = widgetNamed(node, 'eps_resolution_preset_row')
  const saveBtn = rowButton(row, 'Save')
  const deleteBtn = rowButton(row, 'Delete')
  const fresh0 = { saveDisabled: saveBtn.disabled, deleteDisabled: deleteBtn.disabled }
  classicPick(node, 'preset', 'Portrait')
  const afterPick = { deleteDisabled: deleteBtn.disabled }
  // Save forwards the REAL click event to canvas.prompt
  canvasAnswer = '__none__'
  const event = { clientX: 321, clientY: 123, type: 'click' }
  click(saveBtn, event)
  const promptCall = canvasPrompts[0]
  // answer the prompt: Save under a new name
  resetRecords()
  canvasAnswer = 'Square'
  vuePick(node, 'width', 640)
  vuePick(node, 'height', 640)
  const midLabels = []
  const origFetch = globalThis.__fetch
  globalThis.__fetch = async (route, init) => {
    midLabels.push({ save: saveBtn.textContent, saveDisabled: saveBtn.disabled, deleteDisabled: deleteBtn.disabled })
    return origFetch(route, init)
  }
  click(saveBtn, event)
  await settle()
  globalThis.__fetch = origFetch
  const afterSave = {
    saveLabel: saveBtn.textContent,
    saveDisabled: saveBtn.disabled,
    deleteDisabled: deleteBtn.disabled,
    posts: structuredClone(posts),
    presetsWidget: val(node, 'presets'),
    toasts: detail()
  }
  // Delete does nothing while disabled, and works when enabled
  resetRecords()
  vuePick(node, 'width', 1111) // a manual edit clears the selection -> delete disabled
  const deleteWhenNothingSelected = { disabled: deleteBtn.disabled }
  click(deleteBtn)
  const postsAfterDisabledClick = posts.length
  out.presetRow = {
    fresh0,
    afterPick,
    promptTitle: promptCall?.title,
    promptValue: promptCall?.value,
    promptEventClientX: promptCall?.event?.clientX,
    promptEventClientY: promptCall?.event?.clientY,
    midLabels,
    afterSave,
    deleteWhenNothingSelected,
    postsAfterDisabledClick
  }
}

// ---- J2: Delete while one preset is selected sends the delete, with the label restored
{
  resetRecords()
  const node = await fresh()
  const row = widgetNamed(node, 'eps_resolution_preset_row')
  const deleteBtn = rowButton(row, 'Delete')
  classicPick(node, 'preset', 'Wide')
  click(deleteBtn)
  await settle()
  out.presetRow.delete = { posts: structuredClone(posts), label: deleteBtn.textContent }
}

// ====================================== K. the Presets property hides the row
{
  resetRecords()
  const node = await fresh()
  const row = widgetNamed(node, 'eps_resolution_preset_row')
  const combo = widgetNamed(node, 'preset')
  const rowOptions = row.options
  const comboOptions = combo.options
  const heightBefore = node.computeSize()[1]
  classicPick(node, 'preset', 'Portrait')
  node.properties.Presets = false
  node.onPropertyChanged('Presets', false)
  const hidden = {
    row: { widget: row.hidden, option: row.options.hidden, display: row.element.style.display },
    combo: { widget: combo.hidden, option: combo.options.hidden },
    rowOptionsSameObject: row.options === rowOptions,
    comboOptionsSameObject: combo.options === comboOptions,
    selectionCleared: val(node, 'presets'),
    heightShrank: node.computeSize()[1] < heightBefore
  }
  node.properties.Presets = true
  node.onPropertyChanged('Presets', true)
  out.presetsProperty = {
    hidden,
    shown: {
      row: { widget: row.hidden, option: row.options.hidden, display: row.element.style.display },
      combo: { widget: combo.hidden, option: combo.options.hidden }
    },
    heightRestored: node.computeSize()[1] === heightBefore
  }
}

// ====================================== L. no addDOMWidget: fails soft
{
  resetRecords()
  const node = makeNode({ domWidgets: false })
  let threw = null
  try {
    m.attach(node)
    await settle()
  } catch (error) {
    threw = String(error)
  }
  out.noDomWidgets = {
    threw,
    names: node.widgets.map((w) => w.name),
    savedValues: node.serialize().widgets_values
  }
}

// ============ N. the lock does not walk away from what was typed (2026-10-05)
// v1.5.0: 16:9, type width 1000 -> 1001 x 563 (the lock's own derived write fired
// the other axis' callback, which derived back). A typed ratio makes inexact
// derivations the common case, so each edit must land exactly where typed.
{
  resetRecords()
  const node = await fresh()
  const steps = {}
  vuePick(node, 'ratio', '16:9')
  steps.pick169At1024 = dims(node)
  vuePick(node, 'width', 1000)
  steps.typed1000 = dims(node) // 1000*9/16 = 562.5 -> half-even 562, same as the backend
  vuePick(node, 'width', 1001)
  steps.typed1001 = dims(node)
  vuePick(node, 'height', 563)
  steps.typedHeight563 = dims(node) // anchors on the field typed: height kept, width derived
  vuePick(node, 'ratio', '2.39:1')
  steps.pick239 = dims(node)
  vuePick(node, 'width', 1000)
  steps.typed1000At239 = dims(node)
  vuePick(node, 'height', 418)
  steps.typedHeight418At239 = dims(node)
  out.lockStability = steps
}
// the pad drag / copy write go through writeSize: also exact
{
  resetRecords()
  const node = await fresh()
  const wall = { id: 3, type: 'LoadImage', comfyClass: 'LoadImage', imgs: [{ naturalWidth: 1000, naturalHeight: 1000 }] }
  node.inputs[0].link = 5
  node.getInputNode = (slot) => (slot === 0 ? wall : null)
  vuePick(node, 'ratio', '2.39:1')
  click(rowButton(widgetNamed(node, 'eps_resolution_copy_row'), 'copy from image'))
  out.lockStability.copyAt239 = dims(node)
}

process.stdout.write(JSON.stringify(out) + '\n')
