// A tiny fake DOM for the broadcast UI/glue tests (there is no jsdom in this
// repo and none is needed): just enough of `document` / elements for
// broadcast_ui.js's builders and broadcast.js's row, popover and dialog to
// run under Node, with `fire()` to deliver events and `findAll()` to look
// things up. Events do NOT bubble: handlers in the code under test are
// attached on the element they act on, plus `document` (capture) listeners
// which `fireDocument()` delivers.

export class FakeText {
  constructor(text) {
    this.nodeType = 3
    this.textContent = String(text)
    this.parentNode = null
  }
}

export class FakeEl {
  constructor(tag) {
    this.tag = tag
    this.children = []
    this.parentNode = null
    this.style = {}
    this.className = ''
    this.attrs = {}
    this.listeners = {}
    this._text = ''
    this.checked = false
    this.disabled = false
    this.title = ''
    this.id = ''
    const self = this
    this.classList = {
      add(name) { if (!self.className.split(/\s+/).includes(name)) self.className = `${self.className} ${name}`.trim() },
      contains(name) { return self.className.split(/\s+/).includes(name) }
    }
  }
  get textContent() {
    if (this.children.length === 0) return this._text
    return this.children.map((child) => child.textContent).join('')
  }
  set textContent(value) {
    this.children = []
    this._text = String(value)
  }
  setAttribute(name, value) { this.attrs[name] = String(value) }
  getAttribute(name) { return this.attrs[name] ?? null }
  appendChild(child) {
    if (child.parentNode) child.parentNode.children = child.parentNode.children.filter((c) => c !== child)
    child.parentNode = this
    this.children.push(child)
    return child
  }
  remove() {
    if (this.parentNode) this.parentNode.children = this.parentNode.children.filter((c) => c !== this)
    this.parentNode = null
  }
  contains(other) {
    for (let node = other; node; node = node.parentNode) if (node === this) return true
    return false
  }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn) }
  removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn) }
  focus() { globalThis.document.activeElement = this }
  getBoundingClientRect() { return { left: 100, right: 300, top: 50, bottom: 74, width: 200, height: 24 } }
  get offsetWidth() { return 0 }
  get offsetHeight() { return 0 }
  get scrollHeight() { return 14 }
  get clientWidth() { return 300 }
}

const docListeners = []

export function installFakeDom() {
  const head = new FakeEl('head')
  const body = new FakeEl('body')
  const document = {
    head,
    body,
    activeElement: null,
    createElement: (tag) => new FakeEl(tag),
    createTextNode: (text) => new FakeText(text),
    getElementById: (id) => findAll(head, (el) => el.id === id)[0] || null,
    addEventListener: (type, fn, capture) => docListeners.push({ type, fn, capture }),
    removeEventListener: (type, fn) => {
      const i = docListeners.findIndex((l) => l.type === type && l.fn === fn)
      if (i !== -1) docListeners.splice(i, 1)
    }
  }
  globalThis.document = document
  globalThis.window = { innerWidth: 1200, innerHeight: 800 }
  return { document, head, body }
}

/** Every descendant (and `root` itself) matching *pred*, depth first. */
export function findAll(root, pred) {
  const found = []
  const visit = (node) => {
    if (!node || node.nodeType === 3) return
    if (pred(node)) found.push(node)
    for (const child of node.children || []) visit(child)
  }
  visit(root)
  return found
}

export const byClass = (root, cls) => findAll(root, (el) => (el.className || '').split(/\s+/).includes(cls))
export const byText = (root, text) => findAll(root, (el) => el.tag === 'button' && el.textContent === text)

/** Delivers an event to *el*'s own listeners. */
export function fire(el, type, extra = {}) {
  const event = {
    type, target: el, defaultPrevented: false, stopped: false,
    stopPropagation() { this.stopped = true },
    preventDefault() { this.defaultPrevented = true },
    ...extra
  }
  for (const fn of [...(el.listeners[type] || [])]) fn(event)
  return event
}

/** Delivers an event to the registered `document` listeners of *type*. */
export function fireDocument(type, extra = {}) {
  const event = {
    type, target: extra.target ?? null, defaultPrevented: false, stopped: false,
    stopPropagation() { this.stopped = true },
    preventDefault() { this.defaultPrevented = true },
    ...extra
  }
  for (const l of [...docListeners].filter((x) => x.type === type)) l.fn(event)
  return event
}

export const documentListenerCount = (type) => docListeners.filter((l) => l.type === type).length
