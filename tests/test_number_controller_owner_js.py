"""Number Controller's per-row unplug memory is stamped with the id of the
node that wrote it (the bypass.js / switcher.js owner convention): a pasted
copy carries the `values` JSON verbatim, and must not act on the original's
remembered consumers.  Driven through the real ``attach()`` under Node with a
fake graph that creates real link objects."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

PROBE_JS = r"""
import * as nc from './extensions/comfyui-epsnodes/eps_image/number_controller.js'
class FakeNode {}
class FakeElement extends FakeNode {
  constructor(tag) {
    super()
    this.tagName = tag
    this.children = []
    this.attributes = {}
    this._value = ''
    this.checked = false
    this.className = ''
    this.textContent = ''
    this.title = ''
    this._listeners = {}
    this.parentNode = null
  }
  setAttribute(k, v) { this.attributes[k] = String(v) }
  getAttribute(k) { return this.attributes[k] }
  append(...nodes) {
    for (const n of nodes) { if (n) { this.children.push(n); n.parentNode = this } }
  }
  appendChild(n) { this.children.push(n); n.parentNode = this; return n }
  remove() {
    if (this.parentNode) {
      const idx = this.parentNode.children.indexOf(this)
      if (idx !== -1) this.parentNode.children.splice(idx, 1)
    }
    this.parentNode = null
  }
  addEventListener(type, handler) {
    ;(this._listeners[type] ??= []).push(handler)
  }
  dispatch(type, evt = {}) {
    for (const h of this._listeners[type] || []) h({ target: this, ...evt })
  }
  get value() { return this._value }
  set value(v) { this._value = v }
  blur() {
    // Real browsers update `document.activeElement` BEFORE dispatching
    // `blur` (it reflects the new focus target, or `body`, by the time a
    // blur handler runs) -- this ordering matters here because
    // number_controller.js's own blur handler calls syncNode(), which must
    // see the field as no longer focused to repaint it.
    if (fakeDocument.activeElement === this) fakeDocument.activeElement = fakeDocument.body
    this.dispatch('blur')
  }
}

const fakeDocument = {
  activeElement: null,
  head: new FakeElement('head'),
  body: new FakeElement('body'),
  createElement: (tag) => new FakeElement(tag),
  createTextNode: (text) => {
    const n = new FakeElement('#text')
    n.textContent = text
    return n
  },
  getElementById: () => null
}
fakeDocument.head.appendChild = (n) => { fakeDocument.head.children.push(n); return n }

globalThis.document = fakeDocument
globalThis.Node = FakeNode

// ---------------------------------------------------------------------------
// A minimal fake litegraph GRAPH -- multiple nodes, real link objects, and
// enough of `connect`/`disconnectInput` to drive the checkbox feature's
// remember/detach/reconnect round trip end to end (not just via source
// pins). Deliberately does NOT simulate litegraph's own isValidConnection
// veto plumbing inside `connect` -- that this node's OWN onConnectOutput
// hook gets consulted at all is covered separately by the source-pin tests
// (`test_type_veto_is_installed_and_consults_is_allowed_type`); this fake
// is only responsible for making a real link exist (or not) so this file's
// own detach/remember/reconnect logic has something real to operate on.
// ---------------------------------------------------------------------------

let nextFakeLinkId = 1

function makeFakeGraph() {
  const nodesById = new Map()
  const links = new Map()
  return {
    nodesById,
    links,
    getNodeById(id) {
      return nodesById.get(id) ?? null
    },
    register(node) {
      nodesById.set(node.id, node)
      node.graph = this
    },
    // number_controller.js calls `node.graph?.setDirtyCanvas(...)` after
    // most mutations -- a real litegraph Graph/Canvas has this; the fake
    // just needs to not throw.
    setDirtyCanvas() {}
  }
}

/** A bare-bones downstream node (an INT input, the common case) -- just
 * enough surface for collectSlotLinkTypes/collectOutputTargets/
 * reconnectRememberedTargets to do their real work against. */
function makeFakeTargetNode(id, inputName, inputType) {
  const node = {
    id,
    inputs: [{ name: inputName, type: inputType, link: null }],
    disconnectInput(slot) {
      const input = this.inputs[slot]
      if (!input || input.link == null) return
      const linkId = input.link
      const link = this.graph?.links?.get(linkId)
      input.link = null
      if (!link) return
      this.graph.links.delete(linkId)
      const origin = this.graph.getNodeById(link.origin_id)
      const originOutput = origin?.outputs?.[link.origin_slot]
      if (originOutput && Array.isArray(originOutput.links)) {
        const i = originOutput.links.indexOf(linkId)
        if (i !== -1) originOutput.links.splice(i, 1)
      }
    }
  }
  return node
}

function makeFakeNode(valuesJson, id) {
  const outputs = []
  for (let n = 1; n <= nc.MAX_SLOTS; n++) {
    outputs.push({ name: nc.slotName(n), type: nc.WILDCARD, links: null })
  }
  const widget = {
    name: nc.VALUES_WIDGET_NAME, value: valuesJson,
    hidden: false, options: {}, callback: null,
  }
  const node = {
    id: id ?? 1,
    comfyClass: nc.CLASS_ID,
    outputs,
    widgets: [widget],
    graph: null,
    size: [300, 100],
    // Driven by the DOM PANEL's own reported getMinHeight() once attached,
    // not by raw `outputs.length` (file header: this node's actual visual
    // footprint is the DOM panel, "No canvas drawing, by construction" --
    // unlike distributor.js's identical-shaped fake, which has no DOM
    // widget and genuinely depends on socket count alone). This also
    // sidesteps a transient-state trap: `node.outputs` briefly holds the
    // backend's full 16 raw sockets before `applyVisibleRowCount`'s very
    // first prune, and a socket-count-driven computeSize() would read a
    // wildly inflated floor during that split second, before
    // growNodeToFitRows's own once-only gating could ever correct it back
    // down (Math.max never shrinks).
    computeSize() {
      const domWidget = this.widgets.find(
        (w) => w.options && typeof w.options.getMinHeight === 'function'
      )
      const widgetHeight = domWidget ? domWidget.options.getMinHeight() : 0
      return [300, 40 + widgetHeight]
    },
    setSize(s) { this.size = s },
    setDirtyCanvas() {},
    addOutput(name, type) { this.outputs.push({ name, type, links: null }) },
    removeOutput(idx) { this.outputs.splice(idx, 1) },
    addDOMWidget(name, type, el, opts) {
      const w = { name, type, element: el, options: opts }
      this.widgets.push(w)
      this.__panelRoot = el
      return w
    },
    // The real litegraph API this file's reconnectRememberedTargets calls
    // (module docstring: "the same validated connection path a manual drag
    // uses"). Creates a real link object shared by both ends, exactly the
    // shape collectSlotLinkTypes/collectOutputTargets read.
    connect(slot, targetNode, targetSlot) {
      const output = this.outputs[slot]
      const input = targetNode.inputs[targetSlot]
      if (!output || !input) return null
      const linkId = nextFakeLinkId++
      const link = {
        id: linkId,
        origin_id: this.id,
        origin_slot: slot,
        target_id: targetNode.id,
        target_slot: targetSlot,
        type: input.type
      }
      this.graph.links.set(linkId, link)
      if (!Array.isArray(output.links)) output.links = []
      output.links.push(linkId)
      input.link = linkId
      return link
    }
  }
  return node
}

// Row elements live at `panelRoot.children[1]` (the list div, after the hint
// div) -- `.children[i]` for row i (0-based), each row's own children being
// [enabled checkbox, index, nameInput, valueInput, typeEl] per buildRowEl()'s
// own append order.
function rowsOf(node) {
  const listEl = node.__panelRoot.children[1]
  return listEl.children.map((rowEl) => ({
    el: rowEl,
    enabledInput: rowEl.children[0],
    nameInput: rowEl.children[2],
    valueInput: rowEl.children[3],
    typeEl: rowEl.children[4]
  }))
}

const tick = () => new Promise((r) => setTimeout(r, 0))
const out = {}
const graph = makeFakeGraph()
const consumer = makeFakeTargetNode(102, 'steps', 'INT'); graph.register(consumer)
const original = makeFakeNode('{}', 101); graph.register(original)
nc.attach(original)
original.connect(0, consumer, 0); original.__epsNcReload()
let row = rowsOf(original)[0]
row.enabledInput.checked = false; row.enabledInput.dispatch('change')
const savedValues = original.widgets[0].value
out.consumerWiredAfterOff = consumer.inputs[0].link != null

// paste: same widget JSON, new id, no links
const copy = makeFakeNode(savedValues, 205); graph.register(copy)
nc.attach(copy)
copy.onConfigure?.({ widgets_values: [savedValues] })
await tick(); await tick()
row = rowsOf(copy)[0]
row.enabledInput.checked = true; row.enabledInput.dispatch('change')
out.consumerWiredAfterCopyOn = consumer.inputs[0].link != null
out.copyOutputLinks = copy.outputs[0].links

row = rowsOf(original)[0]
row.enabledInput.checked = true; row.enabledInput.dispatch('change')
const l = consumer.inputs[0].link != null ? graph.links.get(consumer.inputs[0].link) : null
out.consumerOriginAfterOriginalOn = l && l.origin_id

// legacy: memory with NO owner stamp, saved by node 301, reloaded as 301
const g2 = makeFakeGraph()
const c2 = makeFakeTargetNode(302, 'cfg', 'FLOAT'); g2.register(c2)
const legacy = JSON.stringify({ num_1: { name: '', value: 0, type: 'FLOAT', enabled: false, links: [{ node: 302, input: 'cfg' }] } })
const n2 = makeFakeNode(legacy, 301); g2.register(n2)
nc.attach(n2)
n2.onConfigure?.({ widgets_values: [legacy] })
await tick(); await tick()
out.legacyStamped = JSON.parse(n2.widgets[0].value).num_1.owner
row = rowsOf(n2)[0]
row.enabledInput.checked = true; row.enabledInput.dispatch('change')
const l2 = c2.inputs[0].link != null ? g2.links.get(c2.inputs[0].link) : null
out.legacyReconnectedTo = l2 && l2.origin_id
console.log(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def result(tmp_path_factory: pytest.TempPathFactory, install_switcher_js) -> dict:
    layout = tmp_path_factory.mktemp("nc_owner")
    install_switcher_js(layout)
    (layout / "probe.mjs").write_text(PROBE_JS, encoding="utf-8")
    run = subprocess.run(
        [NODE, str(layout / "probe.mjs")],
        capture_output=True,
        encoding="utf-8",
        timeout=60,
        cwd=layout,
    )
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout.strip().splitlines()[-1])


def test_ticking_a_pasted_copy_on_does_not_wire_it_into_the_originals_consumers(
    result: dict,
) -> None:
    assert result["consumerWiredAfterOff"] is False
    assert result["consumerWiredAfterCopyOn"] is False
    assert not result["copyOutputLinks"]


def test_the_original_still_gets_its_wires_back_after_the_copy_was_ticked(result: dict) -> None:
    assert result["consumerOriginAfterOriginalOn"] == 101


def test_unstamped_memory_from_an_older_save_is_adopted_by_the_node_that_reads_it(
    result: dict,
) -> None:
    assert result["legacyStamped"] == 301
    assert result["legacyReconnectedTo"] == 301
