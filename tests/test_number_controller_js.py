"""Frontend tests for the EPS Number Controller frontend (FORMAT.md proposed
section 6.17).

``web/eps_image/number_controller.js`` factors its values-map/row-count/
type-adoption contract into PURE exported functions (`parseValues`,
`serializeValues`, `slotCarriesContent`, `computeVisibleRowCount`,
`labelForSlot`, `parseNumericInput`, `roundHalfAwayFromZero`,
`displayValueFor`, `isAllowedType`, `resolveAdoptedType`, `jsonTypeFor`,
`typeBadgeFor`, `isOutputConnected`, `slotName`/`parseSlotNumber`) precisely
so this file can drive them under Node without a litegraph node stub --
``tests/test_distributor_js.py``'s exact convention (that file's own
docstring explains the served-layout mirroring this fixture copies).

The DOM/closure-bound machinery (row-element diffing, the focus-guarded
repaint, the litegraph hook chaining) has no browser harness in this repo,
so most of it is pinned via SOURCE-TEXT assertions --
``test_frame_saver_paste_js.py``'s established convention for that class of
code, restated by ``test_distributor_js.py``'s own "source structure"
section.

**One exception, by explicit instruction:** the re-render law (a tab
switch/undo/redo/workflow-reload tears down and rebuilds every node's DOM
widget -- FORMAT.md's owner rule, "Just switching between workflows
shouldn't ever reset anything in our nodes") is verified with an ACTUAL
Node-level simulation, not just a source pin, since it is the exact bug
class v0.87.3 shipped a fix for elsewhere in this pack. This repo has no
jsdom dependency, so the probe script below builds the handful of DOM/
litegraph primitives `number_controller.js` actually touches
(`document.createElement`, element `.value`/`.className`/event listeners,
`document.activeElement`, a fake litegraph node with `addOutput`/
`removeOutput`/`addDOMWidget`) itself, then drives the REAL, unmodified
`attach()` export against it end to end -- no new export was added to make
this possible; the DOM tree handed to `addDOMWidget` is intercepted by the
fake and walked directly. See `test_rebuild_then_repaint_preserves_a_mid_edit_field`.

Skips cleanly when Node isn't installed; the LIVE mechanics (an actual
connection reaching `onConnectOutput`, a real tab switch, the property
panel) are verified on the rig, not here.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
NUMBER_CONTROLLER_JS = REPO_ROOT / "web" / "eps_image" / "number_controller.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

#: (rawValue, expected parsed map). parseValues must never throw.
PARSE_VALUES_CASES = [
    ("{}", {}),
    (
        '{"num_1": {"name": "steps", "value": 30, "type": "INT"}}',
        {"num_1": {"name": "steps", "value": 30, "type": "INT"}},
    ),
    ("not json", {}),
    ("[]", {}),
    ("null", {}),
    ("", {}),
    ('"a string"', {}),
    ("42", {}),
]

#: (entry, expected slotCarriesContent()). The literal contract rule: a
#: non-empty (trimmed) name OR a nonzero finite value counts as content; a
#: deliberately-typed 0 with no name reads the same as untouched (module
#: docstring's documented trade-off).
SLOT_CARRIES_CONTENT_CASES = [
    (None, False),
    ({}, False),
    ({"name": "steps"}, True),
    ({"name": "  "}, False),
    ({"name": "  steps  "}, True),
    ({"value": 5}, True),
    ({"value": -3}, True),
    ({"value": 0}, False),
    ({"name": "", "value": 0}, False),
    ({"value": "5"}, False),  # a JSON string, not a number -- never content
    ({"value": None}, False),
    ("not an object", False),
    # The checkbox feature (owner ask): an explicitly disabled row is
    # meaningful state on its own -- it carries remembered links to restore
    # on re-check -- even with a blank name/0 value, so it must not vanish.
    ({"enabled": False}, True),
    ({"name": "", "value": 0, "enabled": False}, True),
    ({"enabled": True}, False),  # explicit true is still just the default
    ({"enabled": 0}, False),  # anything but the literal boolean false -> no effect
]

#: (entry, expected isRowEnabled()). distributor.js's isSlotEnabled rule --
#: enabled unless the literal boolean false.
IS_ROW_ENABLED_CASES = [
    (None, True),
    ({}, True),
    ({"enabled": True}, True),
    ({"enabled": False}, False),
    ({"enabled": 0}, True),
    ({"enabled": None}, True),
    ({"enabled": "false"}, True),
]

#: (raw, expected normalizeRememberedLinks()). Fail-soft sanitizing of the
#: checkbox feature's remembered fan-out targets -- one bad item never
#: poisons its neighbours.
NORMALIZE_REMEMBERED_LINKS_CASES = [
    (None, []),
    ([], []),
    ("not an array", []),
    ([{"node": 12, "input": "cfg"}], [{"node": 12, "input": "cfg"}]),
    ([{"node": "abc", "input": "cfg"}], [{"node": "abc", "input": "cfg"}]),  # string id tolerated
    ([{"node": 12, "input": ""}], []),  # empty input name dropped
    ([{"node": 12}], []),  # missing input dropped
    ([{"input": "cfg"}], []),  # missing node dropped
    ([None, "garbage", 5], []),
    ([{"node": True, "input": "cfg"}], []),  # bool is not a valid node id
    (
        [{"node": 1, "input": "a"}, "junk", {"node": 2, "input": "b"}],
        [{"node": 1, "input": "a"}, {"node": 2, "input": "b"}],
    ),
]

#: (valuesMap, highestWiredIndex, expected computeVisibleRowCount()). The
#: growth rule and the never-shrink-below-wired invariant, generalized from
#: distributor.js's own GROW_VISIBLE_CASES/CLAMP_VISIBLE_CASES shape to this
#: node's single pure formula.
ROW_COUNT_CASES = [
    ({}, 0, 1),  # fresh node: exactly one blank spare row
    ({}, None, 1),
    ({}, "abc", 1),  # non-finite wired index -> treated as 0
    ({"num_1": {"value": 5}}, 0, 2),  # content grows the panel
    ({"num_5": {"name": "x"}}, 0, 6),
    ({}, 10, 11),  # never shrinks below a wired slot
    ({}, 20, 16),  # wired index itself clamps at the ceiling
    ({"num_16": {"value": 1}}, 0, 16),  # content at the ceiling can't overflow it
    ({"num_9": {"value": 1}}, 12, 13),  # wired floor wins over a lower content floor
    ({"num_1": {"name": "a"}, "num_3": {"value": 2}}, 0, 4),  # highest of several wins
]

#: (name, n, expected labelForSlot()). Falls back to num_N when unnamed.
LABEL_FOR_SLOT_CASES = [
    ("", 3, "num_3"),
    ("   ", 3, "num_3"),
    ("steps", 3, "steps"),
    ("  steps  ", 3, "steps"),
    (None, 7, "num_7"),
    ("cfg", 16, "cfg"),
]

#: (text, expected parseNumericInput()). `null` means "don't commit yet" --
#: an incomplete number must never overwrite the last complete one.
PARSE_NUMERIC_INPUT_CASES = [
    ("30", 30),
    ("7.5", 7.5),
    ("-3", -3),
    ("", None),
    ("   ", None),
    ("-", None),
    (".", None),
    ("3.", 3),  # JS's lenient Number() parse -- a complete number already
    ("abc", None),
    ("1e2", 100),
    ("  12  ", 12),
    (30, 30),  # already a number
]

#: (value, expected roundHalfAwayFromZero()). Backend rig finding: halves
#: round AWAY from zero, symmetrically for negatives -- NOT Math.round's
#: round-half-to-even-ish behavior on negatives.
ROUND_HALF_AWAY_CASES = [
    (2.5, 3),
    (-2.5, -3),
    (0.5, 1),
    (-0.5, -1),
    (2.4, 2),
    (-2.4, -2),
    (0, 0),
    (3, 3),
    (-3, -3),
    (1.5, 2),
    (-1.5, -2),
]

#: ((value, jsonType), expected displayValueFor()). The panel-display-only
#: snap: INT rounds, everything else passes through untouched.
DISPLAY_VALUE_CASES = [
    ((2.5, "INT"), 3),
    ((-2.5, "INT"), -3),
    ((2.5, "FLOAT"), 2.5),
    ((2.5, "*"), 2.5),
    ((30, "INT"), 30),
    ((None, "FLOAT"), 0),
    (("nan-ish", "INT"), 0),
]

#: (type, expected isAllowedType()). Only INT/FLOAT are concrete-allowed;
#: litegraph's generic forms always pass.
IS_ALLOWED_TYPE_CASES = [
    ("", True),
    ("*", True),
    (0, True),
    (None, True),
    ("INT", True),
    ("float", True),  # case-insensitive
    ("INT,STRING", True),  # comma-union: one allowed member is enough
    ("STRING", False),
    ("IMAGE", False),
    ("MODEL", False),
    ("COMBO", False),
]

#: (candidates, expected {type, mixed}). One slot's own links only -- the
#: caller (collectSlotLinkTypes) never pools two different output slots'
#: candidates together (module docstring's per-slot paragraph).
RESOLVE_ADOPTED_TYPE_CASES = [
    ([], {"type": "*", "mixed": False}),  # disconnected -> wildcard
    (["*", "INT"], {"type": "INT", "mixed": False}),
    (["INT", "FLOAT"], {"type": "INT", "mixed": True}),
    (["FLOAT", "FLOAT"], {"type": "FLOAT", "mixed": False}),
    (["INT"], {"type": "INT", "mixed": False}),
]

#: (adoptedType, expected jsonTypeFor()). Defensive fallback to '*' for any
#: foreign concrete type the veto should never have let through.
JSON_TYPE_FOR_CASES = [
    ("INT", "INT"),
    ("FLOAT", "FLOAT"),
    ("*", "*"),
    (None, "*"),
    ("int", "INT"),  # uppercased
    ("STRING", "*"),  # foreign type, defended against
]

#: (jsonType, expected typeBadgeFor()).
TYPE_BADGE_FOR_CASES = [
    ("INT", "INT"),
    ("FLOAT", "FLOAT"),
    ("*", "any"),
    ("STRING", "any"),
]

#: (label, JS expression building the `output`, expected isOutputConnected()).
#: Identical case list to tests/test_distributor_js.py's OUTPUT_LINK_CASES --
#: the two functions are meant to stay in lockstep.
OUTPUT_LINK_CASES = [
    ("null output", "null", False),
    ("undefined output", "undefined", False),
    ("neither field", "{}", False),
    ("empty links array", "{ links: [] }", False),
    ("settled link", "{ links: [7] }", True),
    ("links not an array", "{ links: 3 }", False),
    ("empty floating set", "{ links: [], _floatingLinks: new Set() }", False),
    ("floating link only", "{ links: [], _floatingLinks: new Set([1]) }", True),
    ("floating with no links field", "{ _floatingLinks: new Set([1, 2]) }", True),
    ("floating set is not a Set", "{ links: [], _floatingLinks: {} }", False),
]

PROBE_JS = """
import * as nc from './extensions/comfyui-epsnodes/eps_image/number_controller.js'

// ---------------------------------------------------------------------------
// Part 1: pure-helper probing (no DOM involved).
// ---------------------------------------------------------------------------

const out = { pure: {}, dom: {} }

out.pure.exports = {
  hasInit: typeof nc.init === 'function',
  hasAttach: typeof nc.attach === 'function'
}

out.pure.constants = {
  classId: nc.CLASS_ID,
  valuesWidgetName: nc.VALUES_WIDGET_NAME,
  maxSlots: nc.MAX_SLOTS,
  minSlots: nc.MIN_SLOTS,
  wildcard: nc.WILDCARD,
  allowedTypes: nc.ALLOWED_TYPES,
  minNodeWidth: nc.MIN_NODE_WIDTH
}

out.pure.slotNames = Array.from({ length: nc.MAX_SLOTS }, (_, i) => nc.slotName(i + 1))
out.pure.parseSlotRoundTrip = [1, 2, 16].map((n) => nc.parseSlotNumber(nc.slotName(n)))
out.pure.parseSlotInvalid = ['image_1', 'num_', 'numx', '', null, undefined, 5, 'num_0x1'].map(
  (v) => nc.parseSlotNumber(v)
)

out.pure.parseValues = %(parse_values_inputs)s.map((v) => nc.parseValues(v))
out.pure.serializeRoundTrip = %(round_trip_maps)s.map((m) => nc.parseValues(nc.serializeValues(m)))

out.pure.slotCarriesContent = %(slot_content_inputs)s.map((v) => nc.slotCarriesContent(v))
out.pure.isRowEnabled = %(is_row_enabled_inputs)s.map((v) => nc.isRowEnabled(v))
out.pure.normalizeRememberedLinks = %(normalize_links_inputs)s
  .map((v) => nc.normalizeRememberedLinks(v))

out.pure.rowCount = %(row_count_inputs)s
  .map(([map, wired]) => nc.computeVisibleRowCount(map, wired))

out.pure.labelForSlot = %(label_inputs)s.map(([name, n]) => nc.labelForSlot(name, n))

out.pure.parseNumericInput = %(parse_numeric_inputs)s.map((v) => nc.parseNumericInput(v))

out.pure.roundHalfAway = %(round_half_inputs)s.map((v) => nc.roundHalfAwayFromZero(v))

out.pure.displayValue = %(display_value_inputs)s.map(([v, t]) => {
  const value = v === 'nan-ish' ? NaN : v
  return nc.displayValueFor(value, t)
})

out.pure.isAllowed = %(is_allowed_inputs)s.map((v) => nc.isAllowedType(v))
out.pure.resolveAdopted = %(resolve_adopted_inputs)s.map((v) => nc.resolveAdoptedType(v))
out.pure.jsonTypeFor = %(json_type_inputs)s.map((v) => nc.jsonTypeFor(v))
out.pure.typeBadgeFor = %(type_badge_inputs)s.map((v) => nc.typeBadgeFor(v))
out.pure.linkChecks = [%(link_cases)s].map((output) => nc.isOutputConnected(output))

// ---------------------------------------------------------------------------
// Part 2: a from-scratch DOM/litegraph-node stub, just enough for the REAL
// attach() export to run end to end. No jsdom in this repo (test_distributor_js.py's
// own docstring notes the same constraint for its own served-layout fixture).
// ---------------------------------------------------------------------------

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
    computeSize() { return [300, 40 + this.outputs.length * 26] },
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

const dom = out.dom

// --- Test A: a fresh node attaches to exactly one blank row. ---
{
  const node = makeFakeNode('{}')
  nc.attach(node)
  const rows = rowsOf(node)
  dom.freshRowCount = rows.length
  dom.freshNameValue = rows[0].nameInput.value
  dom.freshValueValue = rows[0].valueInput.value
  dom.freshTypeBadge = rows[0].typeEl.textContent
  dom.widgetHidden = node.widgets[0].hidden
  dom.widgetOptionsHidden = node.widgets[0].options.hidden
}

// --- Test B: a node "restored" with saved content grows to the right row
// count and shows the saved name/value (the ordinary, non-racing restore
// path -- attach() then onConfigure(), exactly litegraph's real order). ---
{
  const saved = JSON.stringify({
    num_1: { name: 'steps', value: 30, type: '*' },
    num_2: { name: 'cfg', value: 7.5, type: '*' }
  })
  const node = makeFakeNode('{}') // attach() sees the FRESH default first...
  nc.attach(node)
  const beforeConfigure = rowsOf(node).length
  node.widgets[0].value = saved // ...then configure() restores the real value...
  node.onConfigure({}) // ...and fires onConfigure, exactly like litegraph does.
  const rows = rowsOf(node)
  dom.restoreBeforeConfigureRowCount = beforeConfigure
  dom.restoreRowCount = rows.length
  dom.restoreRow1Name = rows[0].nameInput.value
  dom.restoreRow1Value = rows[0].valueInput.value
  dom.restoreRow2Name = rows[1].nameInput.value
  dom.restoreRow2Value = rows[1].valueInput.value
}

// --- Test C: THE RE-RENDER LAW. A field the user is actively (mid-keystroke,
// uncommitted) editing must survive an external repaint -- simulating
// Universal State Controller's Apply writing this node's `values` widget
// directly while the panel is already live and focused (module docstring's
// "widget arriving after the panel exists" race leg; announceWidgetsChangedExternally
// is what would trigger this for real, routed through the `__epsNcReload` seam
// attach() stamps on the node -- invoked directly here since the pub/sub
// wiring itself is a one-line stub in this harness, not the thing under test). ---
{
  const node = makeFakeNode(JSON.stringify({ num_1: { name: 'steps', value: 30, type: '*' } }))
  nc.attach(node)
  const rows = rowsOf(node)
  const valueInput = rows[0].valueInput

  // User focuses row 1's value box and types "-" (an INCOMPLETE number --
  // parseNumericInput('-') is null, so buildRowEl's own handler will not
  // commit it; this is exactly the uncommitted state the guard exists for).
  fakeDocument.activeElement = valueInput
  valueInput.value = '-'
  valueInput.dispatch('input')
  dom.midEditUncommittedValueAfterOwnInput = valueInput.value // must stay '-'
  dom.midEditWidgetAfterOwnInput = node.widgets[0].value // must be untouched

  // Now an EXTERNAL write lands on the SAME widget -- Apply from the
  // Universal State Controller, indistinguishable here from a direct widget
  // write -- while the user's cursor is still sitting in that same box.
  node.widgets[0].value = JSON.stringify({ num_1: { name: 'steps', value: 42, type: '*' } })
  node.__epsNcReload()

  dom.midEditValueAfterExternalWrite = valueInput.value // must STILL be '-'
  const nameInput = rows[0].nameInput
  dom.midEditNameAfterExternalWrite = nameInput.value // untouched field converges normally

  // Leaving the field (blur) is the commit point for an abandoned edit --
  // the display now reverts to the last COMMITTED number (42, from the
  // external write), never to something invented from the abandoned '-'.
  valueInput.blur()
  dom.afterBlurValue = valueInput.value
  dom.afterBlurWidget = node.widgets[0].value
}

// --- Test D: growth-while-typing -- filling the last (blank spare) row's
// value field reveals a new spare row immediately, live, not just on blur. ---
{
  const node = makeFakeNode('{}')
  nc.attach(node)
  const row1 = rowsOf(node)[0]
  row1.valueInput.value = '5'
  row1.valueInput.dispatch('input')
  dom.growthAfterTypingRowCount = rowsOf(node).length // expect 2
}

// --- Test E: clearing a MIDDLE row's name mid-retype must not delete the
// row the cursor is sitting in, even though the pure content-based floor
// alone would otherwise have shrunk the panel to BELOW that row (module
// docstring's focusedSlotFloor paragraph). Row 1 is deliberately NOT the
// focused one: LIFO tail-only removal can never touch the lowest-numbered
// row anyway, so a test focused there would never actually exercise this
// protection -- row 3 (the middle one) is the row whose OWN socket would
// be removed without the fix. ---
{
  const node = makeFakeNode(
    JSON.stringify({
      num_1: { name: 'foo', value: 0, type: '*' },
      num_3: { name: 'bar', value: 0, type: '*' }
    })
  )
  nc.attach(node)
  const rowsBefore = rowsOf(node)
  // expect 4: rows 1-3 have/reach content, row 4 is the spare
  dom.focusRowCountBefore = rowsBefore.length
  const nameInput = rowsBefore[2].nameInput // row 3
  fakeDocument.activeElement = nameInput
  nameInput.value = ''
  // Clears row 3's only content -- the pure content floor alone would drop
  // to 2 (only row 1 still has content), which would try to remove BOTH
  // row 4 (the now-stale spare) AND row 3 itself (the row under the
  // cursor). The focus floor must keep row 3 (but not stop row 4 from
  // going, since row 4 was never focused).
  nameInput.dispatch('input')
  const rowsDuring = rowsOf(node)
  dom.focusRowCountDuring = rowsDuring.length // row 3 must still exist -> 3, not 2
  // rowsOf() builds a fresh wrapper object on every call -- compare the
  // underlying DOM element itself (`.el`), not the wrapper, or this would
  // always read false regardless of whether the row element persisted.
  dom.focusRow3StillPresent = rowsDuring[2].el === rowsBefore[2].el
}

// --- Test F: THE CHECKBOX ROUND TRIP (owner ask). enable(default) -> wire
// -> disable (link gone, the target's own value is untouched -- simulated
// here by there simply being nothing else FOR the link to have touched) ->
// re-enable (reconnects to the SAME remembered target). No setTimeout
// needed: setRowEnabled's own graph mutations are synchronous, and this
// test drives the checkbox directly rather than through the connection-
// change hook (that path is Test H, below). ---
{
  const graph = makeFakeGraph()
  const node = makeFakeNode('{}', 101)
  graph.register(node)
  const target = makeFakeTargetNode(102, 'steps', 'INT')
  graph.register(target)

  nc.attach(node)
  node.connect(0, target, 0) // row 1 wired to an INT input, as if by a drag
  node.__epsNcReload() // settle: row 1 adopts INT, panel grows to 2 rows

  const row1 = rowsOf(node)[0]
  const result = {
    wiredAfterConnect: target.inputs[0].link != null,
    typeBadgeAfterWire: row1.typeEl.textContent,
    checkboxBeforeDisable: row1.enabledInput.checked
  }

  row1.enabledInput.checked = false
  row1.enabledInput.dispatch('change')
  const mapAfterDisable = JSON.parse(node.widgets[0].value)
  result.wiredAfterDisable = target.inputs[0].link != null
  result.checkboxAfterDisable = row1.enabledInput.checked
  result.rowDimmedAfterDisable = row1.el.className.includes('epsnc-row-disabled')
  result.storedEnabledAfterDisable = nc.isRowEnabled(mapAfterDisable.num_1)
  result.storedLinksAfterDisable = nc.normalizeRememberedLinks(mapAfterDisable.num_1?.links)

  row1.enabledInput.checked = true
  row1.enabledInput.dispatch('change')
  const mapAfterReenable = JSON.parse(node.widgets[0].value)
  result.wiredAfterReenable = target.inputs[0].link != null
  result.checkboxAfterReenable = row1.enabledInput.checked
  result.storedEnabledAfterReenable = nc.isRowEnabled(mapAfterReenable.num_1)
  result.storedLinksAfterReenable = nc.normalizeRememberedLinks(mapAfterReenable.num_1?.links)
  result.reconnectedToSameTarget =
    target.inputs[0].link != null &&
    graph.links.get(target.inputs[0].link)?.origin_id === node.id &&
    graph.links.get(target.inputs[0].link)?.target_id === target.id

  dom.enableRoundTrip = result
}

// --- Test G: THE RE-RENDER LAW, extended to the checkbox. A disabled row's
// `enabled`/`links` state survives a rebuild (a brand-new node OBJECT with
// the same saved id, litegraph's real tab-switch/undo/redo mechanics --
// module docstring), the checkbox/dimming repaint correctly from it, and a
// re-check on the REBUILT node object still reconnects to the original
// target. ---
{
  const graph = makeFakeGraph()
  const node1 = makeFakeNode('{}', 201)
  graph.register(node1)
  const target = makeFakeTargetNode(202, 'cfg', 'FLOAT')
  graph.register(target)

  nc.attach(node1)
  node1.connect(0, target, 0)
  node1.__epsNcReload()
  const row1a = rowsOf(node1)[0]
  row1a.enabledInput.checked = false
  row1a.enabledInput.dispatch('change') // disable + detach + remember, on node1

  const savedValue = node1.widgets[0].value // what a real save/restore would carry

  // Rebuild: a fresh node object, same id (a genuine same-workflow reload
  // keeps ids stable -- module docstring's checkbox paragraph), attach()ed
  // BEFORE its widget is restored (the ordinary race order), then
  // configure()-equivalent restore.
  graph.nodesById.delete(201)
  const node2 = makeFakeNode('{}', 201)
  graph.register(node2)
  nc.attach(node2)
  node2.widgets[0].value = savedValue
  node2.onConfigure({})

  const row1b = rowsOf(node2)[0]
  const result = {
    checkboxAfterRebuild: row1b.enabledInput.checked,
    rowDimmedAfterRebuild: row1b.el.className.includes('epsnc-row-disabled'),
    linksPreservedAfterRebuild: nc.normalizeRememberedLinks(
      JSON.parse(node2.widgets[0].value).num_1?.links
    )
  }

  row1b.enabledInput.checked = true
  row1b.enabledInput.dispatch('change')
  result.reconnectsAfterRebuild = target.inputs[0].link != null

  dom.rebuildPreservesEnabled = result
}

// --- Test H: wiring a brand-new link onto a disabled row auto re-enables
// it (owner ask 5) -- driven through the REAL connection-change hook, which
// defers to the next macrotask (wireRowSync's own docstring), hence the
// await below. ---
{
  const graph = makeFakeGraph()
  const node = makeFakeNode(
    JSON.stringify({ num_1: { name: '', value: 0, type: '*', enabled: false } }),
    301
  )
  graph.register(node)
  const target = makeFakeTargetNode(302, 'denoise', 'FLOAT')
  graph.register(target)

  nc.attach(node)
  const checkboxBeforeWire = rowsOf(node)[0].enabledInput.checked

  const link = node.connect(0, target, 0)
  node.onConnectionsChange('output', 0, true, link, node.outputs[0])
  await new Promise((resolve) => setTimeout(resolve, 0))

  const mapAfterWire = JSON.parse(node.widgets[0].value)
  dom.autoReenableOnNewWire = {
    checkboxBeforeWire,
    wiredAfterWire: target.inputs[0].link != null,
    checkboxAfterWire: rowsOf(node)[0].enabledInput.checked,
    storedEnabledAfterWire: nc.isRowEnabled(mapAfterWire.num_1)
  }
}

// --- Test I: owner ask 4 -- a Universal State Controller Apply (simulated
// as the real subscriber would act: write the `values` widget directly,
// then invoke the `__epsNcReload` seam) must actually DETACH the graph's
// stale link, not just repaint an unchecked box over a wire that is still
// live. ---
{
  const graph = makeFakeGraph()
  const node = makeFakeNode('{}', 401)
  graph.register(node)
  const target = makeFakeTargetNode(402, 'steps', 'INT')
  graph.register(target)

  nc.attach(node)
  node.connect(0, target, 0)
  node.__epsNcReload() // settle: ordinary wired+enabled row, exactly like Test F up to here

  const wiredBeforeApply = target.inputs[0].link != null

  node.widgets[0].value = JSON.stringify({
    num_1: { name: '', value: 0, type: 'INT', enabled: false }
  })
  node.__epsNcReload()

  const mapAfterApply = JSON.parse(node.widgets[0].value)
  dom.applyMustRewire = {
    wiredBeforeApply,
    wiredAfterApply: target.inputs[0].link != null,
    checkboxAfterApply: rowsOf(node)[0].enabledInput.checked,
    rememberedAfterApply: nc.normalizeRememberedLinks(mapAfterApply.num_1?.links)
  }
}

process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def number_controller_api(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Runs the probe against the REAL number_controller.js in a served-layout
    tmp dir (tests/test_distributor_js.py's own docstring explains why the
    layout depth matters: get it wrong and Node cannot resolve the module's
    imports at all)."""
    layout = tmp_path_factory.mktemp("web_root")

    module_dir = layout / "extensions" / "comfyui-epsnodes" / "eps_image"
    module_dir.mkdir(parents=True)
    shutil.copyfile(NUMBER_CONTROLLER_JS, module_dir / "number_controller.js")

    scripts = layout / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "app.js").write_text("export const app = {}\n", encoding="utf-8")

    # number_controller.js's second import -- a stub sufficient to resolve
    # the module; the real pub/sub behavior is not what Test C drives (it
    # invokes the `__epsNcReload` seam directly, same as the real subscriber
    # callback would).
    lora_library = layout / "extensions" / "comfyui-epsnodes" / "lora_library"
    lora_library.mkdir(parents=True, exist_ok=True)
    (lora_library / "api.js").write_text(
        "export function subscribeWidgetsChangedExternally(handler) {}\n", encoding="utf-8"
    )

    probe = layout / "probe.mjs"
    probe.write_text(
        PROBE_JS
        % {
            "parse_values_inputs": json.dumps([v for v, _ in PARSE_VALUES_CASES]),
            "round_trip_maps": json.dumps(
                [expected for _, expected in PARSE_VALUES_CASES if expected]
            ),
            "slot_content_inputs": json.dumps([v for v, _ in SLOT_CARRIES_CONTENT_CASES]),
            "is_row_enabled_inputs": json.dumps([v for v, _ in IS_ROW_ENABLED_CASES]),
            "normalize_links_inputs": json.dumps([v for v, _ in NORMALIZE_REMEMBERED_LINKS_CASES]),
            "row_count_inputs": json.dumps([[m, w] for m, w, _ in ROW_COUNT_CASES]),
            "label_inputs": json.dumps([[n, i] for n, i, _ in LABEL_FOR_SLOT_CASES]),
            "parse_numeric_inputs": json.dumps([v for v, _ in PARSE_NUMERIC_INPUT_CASES]),
            "round_half_inputs": json.dumps([v for v, _ in ROUND_HALF_AWAY_CASES]),
            "display_value_inputs": json.dumps([list(v) for v, _ in DISPLAY_VALUE_CASES]),
            "is_allowed_inputs": json.dumps([v for v, _ in IS_ALLOWED_TYPE_CASES]),
            "resolve_adopted_inputs": json.dumps([v for v, _ in RESOLVE_ADOPTED_TYPE_CASES]),
            "json_type_inputs": json.dumps([v for v, _ in JSON_TYPE_FOR_CASES]),
            "type_badge_inputs": json.dumps([v for v, _ in TYPE_BADGE_FOR_CASES]),
            "link_cases": ", ".join(js for _, js, _ in OUTPUT_LINK_CASES),
        },
        encoding="utf-8",
    )

    result = subprocess.run(
        [NODE, str(probe)], capture_output=True, text=True, timeout=60, cwd=layout
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def source() -> str:
    return NUMBER_CONTROLLER_JS.read_text(encoding="utf-8")


def _function_body(source: str, signature: str) -> str:
    """The body of a top-level ``function <signature> {`` declaration, up to
    its closing brace at column 0 -- test_distributor_js.py's identical
    helper (this file's own top-level functions follow the same 2-space
    internal indent / unindented closing brace convention)."""
    import re

    start_match = re.search(re.escape(f"function {signature} {{") + r"\n", source)
    assert start_match, f"function {signature} {{ not found"
    start = start_match.end()
    end_match = re.search(r"\n\}\n", source[start:])
    assert end_match, f"function {signature}'s closing brace not found"
    return source[start : start + end_match.start()]


# --------------------------------------------------------------- parses


def test_number_controller_js_parses() -> None:
    """`node --check` -- the file must at minimum be valid ES module syntax."""
    result = subprocess.run(
        [NODE, "--check", str(NUMBER_CONTROLLER_JS)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


def test_module_exports_the_extension_entry_points(number_controller_api: dict) -> None:
    assert number_controller_api["pure"]["exports"] == {"hasInit": True, "hasAttach": True}


# --------------------------------------------------------------- constants


def test_constants(number_controller_api: dict) -> None:
    constants = number_controller_api["pure"]["constants"]
    assert constants["classId"] == "EPSNumberController"
    assert constants["valuesWidgetName"] == "values"
    assert constants["maxSlots"] == 16
    assert constants["minSlots"] == 1
    assert constants["wildcard"] == "*"
    assert constants["allowedTypes"] == ["INT", "FLOAT"]
    assert constants["minNodeWidth"] >= 150


def test_frontend_ceiling_matches_backend(number_controller_api: dict) -> None:
    """`MAX_SLOTS`/`MAX_OUTPUTS` are declared in TWO places -- the backend
    (which builds the real RETURN_TYPES/RETURN_NAMES tuples) and this file
    (which decides how far growth may reveal). They must agree, exactly
    distributor.js's `test_frontend_ceiling_matches_backend` precedent."""
    from eps_image.nodes_number_controller import MAX_OUTPUTS as BACKEND_MAX

    assert number_controller_api["pure"]["constants"]["maxSlots"] == BACKEND_MAX


def test_slot_names_match_backend_return_names(number_controller_api: dict) -> None:
    from eps_image.nodes_number_controller import EPSNumberController

    assert number_controller_api["pure"]["slotNames"] == list(EPSNumberController.RETURN_NAMES)


# ------------------------------------------------------------ slot naming


def test_parse_slot_number_round_trips(number_controller_api: dict) -> None:
    assert number_controller_api["pure"]["parseSlotRoundTrip"] == [1, 2, 16]


def test_parse_slot_number_rejects_non_matching_names(number_controller_api: dict) -> None:
    assert number_controller_api["pure"]["parseSlotInvalid"] == [None] * 8


# --------------------------------------------------------- values JSON


def test_parse_values_never_throws_and_degrades_to_empty(number_controller_api: dict) -> None:
    pairs = zip(PARSE_VALUES_CASES, number_controller_api["pure"]["parseValues"], strict=True)
    for (given, expected), got in pairs:
        assert got == expected, f"parseValues({given!r}) -> {got!r}, wanted {expected!r}"


def test_serialize_values_round_trips(number_controller_api: dict) -> None:
    maps = [expected for _, expected in PARSE_VALUES_CASES if expected]
    pairs = zip(maps, number_controller_api["pure"]["serializeRoundTrip"], strict=True)
    for given, got in pairs:
        assert got == given, f"round-trip of {given!r} -> {got!r}"


# ------------------------------------------------------- content / row count


def test_slot_carries_content(number_controller_api: dict) -> None:
    pairs = zip(
        SLOT_CARRIES_CONTENT_CASES, number_controller_api["pure"]["slotCarriesContent"], strict=True
    )
    for (given, expected), got in pairs:
        assert got is expected, f"slotCarriesContent({given!r}) -> {got!r}, wanted {expected!r}"


def test_is_row_enabled(number_controller_api: dict) -> None:
    """distributor.js's isSlotEnabled rule, applied to the checkbox feature:
    enabled unless the entry EXPLICITLY records the literal boolean
    false."""
    pairs = zip(IS_ROW_ENABLED_CASES, number_controller_api["pure"]["isRowEnabled"], strict=True)
    for (given, expected), got in pairs:
        assert got is expected, f"isRowEnabled({given!r}) -> {got!r}, wanted {expected!r}"


def test_normalize_remembered_links(number_controller_api: dict) -> None:
    """Fail-soft sanitizing of the checkbox feature's remembered fan-out
    targets: never throws, one bad item never poisons its neighbours."""
    pairs = zip(
        NORMALIZE_REMEMBERED_LINKS_CASES,
        number_controller_api["pure"]["normalizeRememberedLinks"],
        strict=True,
    )
    for (given, expected), got in pairs:
        assert got == expected, (
            f"normalizeRememberedLinks({given!r}) -> {got!r}, wanted {expected!r}"
        )


def test_compute_visible_row_count(number_controller_api: dict) -> None:
    """The contract's growth rule: `max(highestWiredIndex, highest slot
    carrying content) + 1`, clamped [1, 16]."""
    pairs = zip(ROW_COUNT_CASES, number_controller_api["pure"]["rowCount"], strict=True)
    for (values_map, wired, expected), got in pairs:
        msg = f"computeVisibleRowCount({values_map!r}, {wired!r}) -> {got!r}, wanted {expected!r}"
        assert got == expected, msg


def test_compute_visible_row_count_never_shrinks_below_wired(number_controller_api: dict) -> None:
    """Stated as an invariant over the whole case list, independent of the
    hand-written expectations above (distributor.js's
    `test_grow_visible_count_never_shrinks` precedent): the result is never
    below `wired + 1` when wired is a genuine positive number."""
    for (_values_map, wired, _expected), got in zip(
        ROW_COUNT_CASES, number_controller_api["pure"]["rowCount"], strict=True
    ):
        if isinstance(wired, int | float) and wired and wired == wired:  # finite, truthy
            floor = min(16, max(1, round(wired)) + 1)
            assert got >= floor, (
                f"computeVisibleRowCount(..., {wired!r}) fell below the wired floor"
            )


def test_compute_visible_row_count_stays_within_bounds(number_controller_api: dict) -> None:
    for got in number_controller_api["pure"]["rowCount"]:
        assert 1 <= got <= 16


# --------------------------------------------------------------- labels


def test_label_for_slot_falls_back_to_num_n(number_controller_api: dict) -> None:
    pairs = zip(LABEL_FOR_SLOT_CASES, number_controller_api["pure"]["labelForSlot"], strict=True)
    for (name, n, expected), got in pairs:
        assert got == expected, f"labelForSlot({name!r}, {n!r}) -> {got!r}, wanted {expected!r}"


# --------------------------------------------------------- numeric parsing


def test_parse_numeric_input(number_controller_api: dict) -> None:
    pairs = zip(
        PARSE_NUMERIC_INPUT_CASES, number_controller_api["pure"]["parseNumericInput"], strict=True
    )
    for (given, expected), got in pairs:
        assert got == expected, f"parseNumericInput({given!r}) -> {got!r}, wanted {expected!r}"


def test_round_half_away_from_zero(number_controller_api: dict) -> None:
    """Backend rig finding: halves round AWAY from zero -- symmetric for
    negatives, unlike `Math.round` alone."""
    pairs = zip(ROUND_HALF_AWAY_CASES, number_controller_api["pure"]["roundHalfAway"], strict=True)
    for (given, expected), got in pairs:
        assert got == expected, f"roundHalfAwayFromZero({given!r}) -> {got!r}, wanted {expected!r}"


def test_display_value_for(number_controller_api: dict) -> None:
    pairs = zip(DISPLAY_VALUE_CASES, number_controller_api["pure"]["displayValue"], strict=True)
    for (given, expected), got in pairs:
        assert got == expected, f"displayValueFor{given!r} -> {got!r}, wanted {expected!r}"


# --------------------------------------------------------- type adoption


def test_is_allowed_type(number_controller_api: dict) -> None:
    """Only INT/FLOAT are concrete-allowed -- deliberately narrower than
    distributor.js's IMAGE/STRING allowlist (rig finding: without this,
    every wildcard num_N output connects to ANY target type)."""
    pairs = zip(IS_ALLOWED_TYPE_CASES, number_controller_api["pure"]["isAllowed"], strict=True)
    for (given, expected), got in pairs:
        assert got is expected, f"isAllowedType({given!r}) -> {got!r}, wanted {expected!r}"


def test_resolve_adopted_type(number_controller_api: dict) -> None:
    """The first CONCRETE candidate among ONE SLOT's own links wins; `mixed`
    flags disagreement without ever picking anything else."""
    pairs = zip(
        RESOLVE_ADOPTED_TYPE_CASES, number_controller_api["pure"]["resolveAdopted"], strict=True
    )
    for (given, expected), got in pairs:
        assert got == expected, f"resolveAdoptedType({given!r}) -> {got!r}, wanted {expected!r}"


def test_resolve_adopted_type_disconnect_reverts_to_wildcard(number_controller_api: dict) -> None:
    """No candidates (every link removed from a slot) -> that slot alone
    reverts to '*' -- the disconnect case, called out explicitly."""
    empty_case_index = next(i for i, (c, _) in enumerate(RESOLVE_ADOPTED_TYPE_CASES) if c == [])
    assert number_controller_api["pure"]["resolveAdopted"][empty_case_index] == {
        "type": "*",
        "mixed": False,
    }


def test_json_type_for(number_controller_api: dict) -> None:
    pairs = zip(JSON_TYPE_FOR_CASES, number_controller_api["pure"]["jsonTypeFor"], strict=True)
    for (given, expected), got in pairs:
        assert got == expected, f"jsonTypeFor({given!r}) -> {got!r}, wanted {expected!r}"


def test_type_badge_for(number_controller_api: dict) -> None:
    pairs = zip(TYPE_BADGE_FOR_CASES, number_controller_api["pure"]["typeBadgeFor"], strict=True)
    for (given, expected), got in pairs:
        assert got == expected, f"typeBadgeFor({given!r}) -> {got!r}, wanted {expected!r}"


def test_is_output_connected_counts_floating_links_too(number_controller_api: dict) -> None:
    """Identical case list/function to distributor.js's/resolution.js's own
    `isOutputConnected` -- the three are meant to stay in lockstep."""
    pairs = zip(OUTPUT_LINK_CASES, number_controller_api["pure"]["linkChecks"], strict=True)
    for (label, _js, expected), got in pairs:
        assert got is expected, f"isOutputConnected({label}) -> {got!r}, wanted {expected!r}"


# --------------------------------------------------- DOM simulation (attach())


def test_fresh_node_shows_one_blank_row(number_controller_api: dict) -> None:
    dom = number_controller_api["dom"]
    assert dom["freshRowCount"] == 1
    assert dom["freshNameValue"] == ""
    assert dom["freshValueValue"] == "0"
    assert dom["freshTypeBadge"] == "any"


def test_values_widget_is_hidden_both_flags(number_controller_api: dict) -> None:
    dom = number_controller_api["dom"]
    assert dom["widgetHidden"] is True
    assert dom["widgetOptionsHidden"] is True


def test_restore_path_attach_then_configure(number_controller_api: dict) -> None:
    """attach() runs against the fresh default (one blank row); configure()
    restoring the real saved JSON and firing onConfigure must grow the panel
    and show the saved content -- litegraph's real ordering
    (checkpoint_switcher.js's file header), reproduced here rather than
    assumed."""
    dom = number_controller_api["dom"]
    assert dom["restoreBeforeConfigureRowCount"] == 1
    assert dom["restoreRowCount"] == 3  # 2 saved rows + one spare
    assert dom["restoreRow1Name"] == "steps"
    assert dom["restoreRow1Value"] == "30"
    assert dom["restoreRow2Name"] == "cfg"
    assert dom["restoreRow2Value"] == "7.5"


def test_rebuild_then_repaint_preserves_a_mid_edit_field(number_controller_api: dict) -> None:
    """THE RE-RENDER LAW, verified directly (module docstring's own promise):
    a field the user is actively (uncommitted) editing survives an external
    repaint -- here, a Universal State Controller Apply writing this node's
    `values` widget directly while the user's cursor sits in a DIFFERENT,
    incomplete edit. The focused field must be untouched by the repaint; an
    unrelated field (the name box on the same row) converges normally; and
    once the user leaves the field (blur), the display reverts to the real,
    externally-applied number -- nothing is silently lost OR corrupted."""
    dom = number_controller_api["dom"]
    assert dom["midEditUncommittedValueAfterOwnInput"] == "-"
    assert json.loads(dom["midEditWidgetAfterOwnInput"]) == {
        "num_1": {"name": "steps", "value": 30, "type": "*"}
    }
    assert dom["midEditValueAfterExternalWrite"] == "-", (
        "the focused field was clobbered by an external repaint"
    )
    assert dom["midEditNameAfterExternalWrite"] == "steps", (
        "an unrelated field should still repaint normally"
    )
    assert dom["afterBlurValue"] == "42", "blur should reveal the real, externally-applied value"
    assert json.loads(dom["afterBlurWidget"])["num_1"]["value"] == 42


def test_growth_while_typing_is_live_not_just_on_blur(number_controller_api: dict) -> None:
    dom = number_controller_api["dom"]
    assert dom["growthAfterTypingRowCount"] == 2


def test_focused_row_survives_clearing_its_own_content(number_controller_api: dict) -> None:
    """Discovered while designing this file (module docstring): clearing a
    MIDDLE row's name back to blank while the cursor is still in it must
    not `removeOutput()` that row's own socket, even though the pure
    content-based floor alone would otherwise have shrunk the panel to
    below it. Row 3, not row 1: LIFO tail-only removal can never touch the
    lowest-numbered row regardless of focus, so only a middle row actually
    exercises this protection."""
    dom = number_controller_api["dom"]
    assert dom["focusRowCountBefore"] == 4
    assert dom["focusRowCountDuring"] == 3, "row 3's own socket must not be removed"
    assert dom["focusRow3StillPresent"] is True


# ------------------------------------------------------- checkbox feature
# (owner ask: "each item should also have a checkbox that can be turned off
# to allow the thing it is connected to to use it's original value") --
# driven through a from-scratch fake litegraph GRAPH (multiple nodes, real
# link objects, a working connect()/disconnectInput()), not just a single
# fake node, since this feature's entire point is genuine graph rewiring.


def test_checkbox_round_trip_detaches_and_reconnects(number_controller_api: dict) -> None:
    """enable(default) -> wire -> disable -> re-enable, end to end against a
    real (faked) litegraph link: unchecking must actually remove the link
    (rig-verified mechanism: the target's own value was never touched by
    the link, so removing it is what restores it), and re-checking must
    reconnect to the SAME remembered target -- both directions through the
    checkbox's own change handler, not the external-write path (that is
    test_universal_state_apply_detaches_a_stale_link below)."""
    r = number_controller_api["dom"]["enableRoundTrip"]
    assert r["wiredAfterConnect"] is True
    assert r["typeBadgeAfterWire"] == "INT"
    assert r["checkboxBeforeDisable"] is True, "a fresh row must default ON"

    assert r["wiredAfterDisable"] is False, "unchecking must detach the live link"
    assert r["checkboxAfterDisable"] is False
    assert r["rowDimmedAfterDisable"] is True
    assert r["storedEnabledAfterDisable"] is False
    assert r["storedLinksAfterDisable"] == [{"node": 102, "input": "steps"}]

    assert r["wiredAfterReenable"] is True, "re-checking must reconnect the remembered target"
    assert r["checkboxAfterReenable"] is True
    assert r["storedEnabledAfterReenable"] is True
    assert r["reconnectedToSameTarget"] is True
    assert r["storedLinksAfterReenable"] == [], (
        "the remembered links must be cleared after one reconnect attempt -- "
        "otherwise a later manual unplug (unrelated to the checkbox) would "
        "silently reconnect itself again on the next sync pass"
    )


def test_checkbox_state_and_links_survive_a_rebuild(number_controller_api: dict) -> None:
    """THE RE-RENDER LAW, extended to the checkbox (module docstring): a
    disabled row's `enabled` flag and remembered `links` must survive a
    tab-switch-style rebuild (a brand-new node object, same saved id), the
    checkbox must repaint unchecked-and-dimmed from the restored JSON alone
    (no live link exists to infer it from), and re-checking on the REBUILT
    node object must still reconnect to the original target -- proving the
    remembered link data itself, not just the boolean, survived."""
    r = number_controller_api["dom"]["rebuildPreservesEnabled"]
    assert r["checkboxAfterRebuild"] is False
    assert r["rowDimmedAfterRebuild"] is True
    assert r["linksPreservedAfterRebuild"] == [{"node": 202, "input": "cfg"}]
    assert r["reconnectsAfterRebuild"] is True


def test_new_wire_onto_disabled_row_reenables_it(number_controller_api: dict) -> None:
    """Owner ask 5: dragging a brand-new wire onto a disabled row is
    unambiguous intent and must flip it back on automatically -- driven
    through the real connection-change hook (deferred past the current call
    frame, hence this being the one DOM test that awaits a macrotask)."""
    r = number_controller_api["dom"]["autoReenableOnNewWire"]
    assert r["checkboxBeforeWire"] is False
    assert r["wiredAfterWire"] is True
    assert r["checkboxAfterWire"] is True, "a new wire onto a disabled row must re-enable it"
    assert r["storedEnabledAfterWire"] is True


def test_universal_state_apply_detaches_a_stale_link(number_controller_api: dict) -> None:
    """Owner ask 4: an Apply that writes `enabled: false` into the `values`
    widget directly (nothing else in the loop -- no connection event fires)
    must still cause the panel to genuinely DETACH the graph's now-stale
    live link, not just repaint an unchecked box over a wire that is still
    doing something. `applyEnabledStateToWiring` is the mechanism; this
    drives it through the real `__epsNcReload` seam, the same one
    `announceWidgetsChangedExternally` would call in production."""
    r = number_controller_api["dom"]["applyMustRewire"]
    assert r["wiredBeforeApply"] is True
    assert r["wiredAfterApply"] is False, "the Apply must actually detach the graph link"
    assert r["checkboxAfterApply"] is False
    assert r["rememberedAfterApply"] == [{"node": 402, "input": "steps"}], (
        "the CURRENT live target must be captured fresh, not trusted from "
        "whatever (possibly stale) links the applied JSON itself carried"
    )


# ------------------------------------------------------- source structure
# The pieces below only run inside attach() against a real litegraph node's
# live connection/configure hooks -- pinned via SOURCE-TEXT assertions,
# tests/test_distributor_js.py's/test_frame_saver_paste_js.py's established
# convention for that class of code.


def test_per_slot_adoption_loops_independently(source: str) -> None:
    """The load-bearing difference from distributor.js (module docstring):
    `resolveAdoptedType` must be called ONCE PER VISIBLE SLOT inside
    `reconcileNode`'s own per-entry loop -- never once for the whole node."""
    body = _function_body(source, "reconcileNode(state)")
    assert "for (const entry of outputEntries(node))" in body
    assert "collectSlotLinkTypes(node, entry.idx)" in body
    assert "resolveAdoptedType(candidates)" in body


def test_type_veto_is_installed_and_consults_is_allowed_type(source: str) -> None:
    """The rig-finding fix: without an active refusal, every wildcard num_N
    output connects to any target type. wireTypeVeto must be installed from
    attach() and its hook must actually consult isAllowedType, not just
    exist as a stub."""
    assert "wireTypeVeto(node)" in source, "not installed from attach()"
    body = _function_body(source, "wireTypeVeto(node)")
    assert "node.onConnectOutput = function" in body
    assert "isAllowedType(inputType)" in body
    assert "return false" in body


def test_veto_returns_false_not_throws(source: str) -> None:
    """Quiet and non-destructive (module docstring): the veto must decline
    via a return value, never an exception."""
    body = _function_body(source, "wireTypeVeto(node)")
    assert "throw" not in body


def test_no_widget_to_convert_dance(source: str) -> None:
    """Rig finding 2: KSampler's INT/FLOAT inputs already exist with a
    concrete `.type` from construction -- there is no 'convert widget to
    input' step this file needs to special-case. Pinned as an absence: no
    code here searches for or manufactures a widget-to-input conversion."""
    assert "convertToInput" not in source
    assert "widgetToInput" not in source


def test_tail_only_removal_is_strictly_lifo(source: str) -> None:
    """applyVisibleRowCount must only ever remove entries strictly ABOVE the
    desired count, sorted highest-array-index first -- distributor.js's/
    resolution.js's shared 'removeOutput splices by position' rule."""
    assert "entries.filter((entry) => entry.n > desired)" in source
    assert ".sort((a, b) => b.idx - a.idx)" in source


def test_never_shrinks_a_wired_or_focused_slot(source: str) -> None:
    """The two floors that together make an unsafe removal impossible: the
    pure row-count formula's wired floor, and the DOM-bound focus floor
    (module docstring's 'never delete the row out from under the cursor').
    Both live in the ONE shared `desiredRowCount` formula (a bug fix found
    while testing, source-pinned separately below) -- `applyVisibleRowCount`
    just calls it."""
    body = _function_body(source, "applyVisibleRowCount(state)")
    assert "const desired = desiredRowCount(state)" in body
    formula_body = _function_body(source, "desiredRowCount(state)")
    assert "computeVisibleRowCount(map, wired)" in formula_body
    assert "focusedSlotFloor(state)" in formula_body
    assert "Math.max(computeVisibleRowCount(map, wired), focusedSlotFloor(state))" in formula_body


def test_row_count_formula_is_shared_not_duplicated(source: str) -> None:
    """The bug this fixes (found while testing, not on the rig): renderRows
    used to compute its own row count WITHOUT the focus floor, so it could
    rebuild the DOM smaller than applyVisibleRowCount had just correctly
    kept, ripping the focused row's `<input>` elements out from under the
    cursor even though the underlying output socket survived. One formula,
    every caller that needs a row count uses it -- pinned as: exactly one
    definition of the Math.max(...) formula in the whole file."""
    pinned = "Math.max(computeVisibleRowCount(map, wired), focusedSlotFloor(state))"
    assert source.count(pinned) == 1
    render_body = _function_body(source, "renderRows(state)")
    assert "desiredRowCount(state)" in render_body
    assert "computeVisibleRowCount(map, wired)" not in render_body
    deferred_body = _function_body(source, "wireRowSync(state)")
    assert "desiredRowCount(state)" in deferred_body


def test_spare_socket_starts_wildcard_not_inherited(source: str) -> None:
    """Unlike distributor.js's spares (which inherit the node's single
    adopted type), a freshly revealed num_N here always starts at WILDCARD
    -- there is no 'the node's type' when every slot adopts independently."""
    body = _function_body(source, "applyVisibleRowCount(state)")
    assert "node.addOutput(slotName(n), WILDCARD)" in body


def test_render_rows_never_writes_to_the_widget(source: str) -> None:
    """The re-render law's second guarantee (module docstring): renderRows
    is read-only with respect to the widget -- every widget write happens
    from writeSlotField/reconcileNode, never from the repaint path."""
    body = _function_body(source, "renderRows(state)")
    assert "widget.value =" not in body
    assert ".callback?.(" not in body


def test_render_rows_guards_each_field_independently(source: str) -> None:
    """Per-FIELD (not per-row) activeElement guards -- generalized from
    notebook.js's populateEditor() single-field guard to this node's two
    independent inputs per row."""
    body = _function_body(source, "renderRows(state)")
    assert "activeElement !== row.nameInput" in body
    assert "activeElement !== row.valueInput" in body


def test_write_slot_field_is_the_sole_user_mutation_point(source: str) -> None:
    """The durable-store law: writeSlotField is the only place a user
    keystroke ever reaches the widget. It merges an arbitrary `patch`
    (`{name}`/`{value}` from typing, `{enabled}`/`{links}` from the
    checkbox) onto the existing entry via a plain spread -- the only
    fallback default (used when the slot has no entry yet) supplies a
    `type`, but nothing here ever independently DECIDES a type from patch
    data; only reconcileNode (a separate, source-pinned function) does."""
    body = _function_body(source, "writeSlotField(node, n, patch)")
    assert "widget.value = json" in body
    assert "map[key] = { ...existing, ...patch }" in body
    assert body.count("type") == 1, "the only type-shaped default may live in the fallback object"


def test_value_input_skips_incomplete_numbers(source: str) -> None:
    """An unparseable value (empty, a bare '-', a trailing letter) must
    never overwrite the last complete committed number."""
    body = _function_body(source, "buildRowEl(state, n)")
    assert "parseNumericInput(valueInput.value)" in body
    assert "if (parsed === null) return" in body


# --------------------------------------------------------- checkbox feature
# The DOM/live-connection round trip is verified directly above
# (test_checkbox_round_trip_detaches_and_reconnects et al.); these pin the
# structural rules that are hard to observe from outside the panel.


def test_disconnect_uses_the_rig_verified_call(source: str) -> None:
    """The owner's rig verification is the entire justification for this
    feature's mechanism (module docstring): `target.disconnectInput(slot)`
    is the exact call that leaves the target's own widget value untouched
    while removing the link."""
    body = _function_body(source, "disconnectAllTargets(node, idx)")
    assert "target.disconnectInput(link.target_slot)" in body


def test_reconnect_goes_through_the_validated_connect_path(source: str) -> None:
    """Reconnecting uses `node.connect(...)`, the same path a manual drag
    uses -- so wireTypeVeto still applies to a checkbox-driven reconnect,
    not a second, unvetted way to make a link."""
    body = _function_body(source, "reconnectRememberedTargets(node, idx, remembered)")
    assert "node.connect(idx, target, targetSlot)" in body


def test_reconnect_fails_soft_and_never_stomps(source: str) -> None:
    """Every one of the three fail-soft cases the owner's brief calls out
    by name: a target node no longer on the graph, an input that no longer
    exists by that name, and a slot another link has since claimed."""
    body = _function_body(source, "reconnectRememberedTargets(node, idx, remembered)")
    assert "if (!target) continue" in body
    assert "if (targetSlot === -1) continue" in body
    assert "if (target.inputs[targetSlot].link != null) continue" in body
    assert "throw" not in body


def test_remembered_links_keyed_by_name_not_slot_index(source: str) -> None:
    """Inputs restore BY NAME (this pack's non-negotiable law) -- the
    remembered target must be looked up by matching `.name`, never by
    trusting a slot INDEX to still mean the same thing after a reload."""
    body = _function_body(source, "reconnectRememberedTargets(node, idx, remembered)")
    assert "(target.inputs || []).findIndex((inp) => inp?.name === item?.input)" in body


def test_disable_captures_fresh_targets_not_stale_ones(source: str) -> None:
    """collectOutputTargets reads the LIVE graph, never a previously-stored
    `links` list -- what gets remembered on disable is always whatever is
    ACTUALLY about to be removed."""
    body = _function_body(source, "collectOutputTargets(node, idx)")
    assert "output?.links" in body
    assert "graph?.getNodeById?.(link.target_id)" in body


def test_slot_carries_content_folds_in_disabled(source: str) -> None:
    """A disabled row must not vanish from the map/panel just because its
    name/value happen to be blank -- slotCarriesContent's own rule, pinned
    at the call site too since this is the row-count/prune floor."""
    body = _function_body(source, "slotCarriesContent(entry)")
    assert "if (entry.enabled === false) return true" in body


def test_apply_enabled_state_to_wiring_is_called_from_sync_node(source: str) -> None:
    """Owner ask 4: syncNode -- the one orchestrator every hook uses -- must
    run applyEnabledStateToWiring on every pass, not only from a specially
    detected external write (its own idempotency, pinned separately below,
    is what makes running it unconditionally safe)."""
    body = _function_body(source, "syncNode(state)")
    assert "applyEnabledStateToWiring(state)" in body
    apply_at = body.index("applyEnabledStateToWiring(")
    count_at = body.index("applyVisibleRowCount(")
    assert apply_at < count_at, "wiring must be reconciled before the row count is derived from it"


def test_apply_enabled_state_to_wiring_diffs_both_directions(source: str) -> None:
    body = _function_body(source, "applyEnabledStateToWiring(state)")
    assert "!storedEnabled && entry.wired" in body
    assert "disconnectAllTargets(node, entry.idx)" in body
    assert "storedEnabled && !entry.wired" in body
    assert "reconnectRememberedTargets(node, entry.idx, remembered)" in body


def test_auto_reenable_runs_only_from_the_connection_hook(source: str) -> None:
    """The ambiguity this function's own docstring explains: 'stored off,
    still wired' looks identical whether a user just dragged a new wire
    (should re-enable) or an Apply hasn't caught up yet (should disconnect)
    -- only the CALLER's context resolves it. Pinned as: exactly one call
    site (wireRowSync's deferred pass), and it is NOT called from syncNode
    or applyEnabledStateToWiring themselves."""
    total = source.count("autoReenableNewlyWiredRows(state)")
    declaration = source.count("function autoReenableNewlyWiredRows(state)")
    assert total - declaration == 1, "must be CALLED from exactly one place"
    sync_body = _function_body(source, "syncNode(state)")
    assert "autoReenableNewlyWiredRows" not in sync_body
    apply_body = _function_body(source, "applyEnabledStateToWiring(state)")
    assert "autoReenableNewlyWiredRows" not in apply_body


def test_auto_reenable_runs_before_the_general_sync(source: str) -> None:
    """Inside the deferred connect-triggered pass, the auto-reenable flip
    must land BEFORE syncNode (and therefore before
    applyEnabledStateToWiring) gets a chance to read the row and treat it
    as a stale-wiring mismatch instead."""
    body = _function_body(source, "wireRowSync(state)")
    reenable_at = body.index("autoReenableNewlyWiredRows(state)")
    reconcile_at = body.index("reconcileNode(state)")
    assert reenable_at < reconcile_at


def test_reconnect_attempts_are_forgotten_not_retried_forever(source: str) -> None:
    """Both places that attempt a reconnect from remembered `links` clear
    that memory afterward (`links: undefined` -- JSON.stringify drops it) --
    otherwise a row a user later unplugs BY HAND, with nothing to do with
    the checkbox, would silently reconnect itself again on the very next
    sync pass, since applyEnabledStateToWiring runs on every one of them."""
    apply_body = _function_body(source, "applyEnabledStateToWiring(state)")
    assert "writeSlotField(node, entry.n, { links: undefined })" in apply_body
    setrow_body = _function_body(source, "setRowEnabled(state, n, checked)")
    assert "links: undefined" in setrow_body
    reenable_body = _function_body(source, "autoReenableNewlyWiredRows(state)")
    assert "links: undefined" in reenable_body


def test_checkbox_change_handler_calls_set_row_enabled(source: str) -> None:
    body = _function_body(source, "buildRowEl(state, n)")
    assert "setRowEnabled(state, n, enabledInput.checked)" in body


def test_disabled_row_renders_dimmed(source: str) -> None:
    """Owner ask 6: a disabled row must be visibly off at a glance."""
    body = _function_body(source, "renderRows(state)")
    assert "epsnc-row-disabled" in body
    assert "row.enabledInput.checked = enabled" in body


# ------------------------------------------------------------- FORMAT §7/§8


def test_no_canvas_drawing(source: str) -> None:
    """This node has no on-canvas control at all -- one addDOMWidget panel,
    per module docstring's 'no canvas drawing, by construction' paragraph.
    The module docstring itself discusses these two hooks in prose (why
    this file doesn't need them), so the pin looks for an ASSIGNMENT to
    either, not just the bare word anywhere in the file."""
    assert "node.onDrawForeground" not in source
    assert "node.onMouseDown" not in source
    assert ".onDrawForeground =" not in source
    assert ".onMouseDown =" not in source


def test_no_per_tick_repaint_loop(source: str) -> None:
    """Change-gated repaints only -- never a per-tick setDirtyCanvas loop
    (the pack's 1Hz-poller lesson)."""
    assert "setInterval(" not in source
    assert "requestAnimationFrame(" not in source


def test_no_window_or_document_level_listeners(source: str) -> None:
    """Every listener this file installs is scoped to one input element
    (keydown stopPropagation / blur) -- there is no window/document-level
    listener that would need the capture-phase law FORMAT.md §7/§8
    otherwise requires."""
    assert "window.addEventListener" not in source
    assert "document.addEventListener" not in source


def test_hidden_widget_sets_both_flags(source: str) -> None:
    """FORMAT.md §7.5: canvas reads widget.hidden, Vue-nodes reads
    widget.options.hidden -- both must be set or the widget leaks into one
    renderer or the other."""
    body = _function_body(source, "hideValuesWidget(node, widget)")
    assert "widget.hidden = true" in body
    assert "widget.options = " in body
    assert "hidden: true" in body


def test_min_width_uses_set_size_not_a_dead_array_guard(source: str) -> None:
    """v0.68.1 lesson (distributor.js's own pin): `node.size` is a Proxy,
    never an Array -- the lift must go through setSize()."""
    assert "Array.isArray(node.size)" not in source
    body = _function_body(source, "installMinWidth(node, minWidth)")
    assert "if (node.size && node.size[0] < minWidth)" in body
    assert "node.setSize([minWidth, node.size[1]])" in body


def test_min_width_guard_flag_is_namespaced(source: str) -> None:
    assert "__epsNumberControllerMinWidthInstalled" in source


def test_attach_never_throws(source: str) -> None:
    """FORMAT.md §7/§8's fail-soft law: attach() wraps its entire body in
    try/catch -> console.warn."""
    body = _function_body(source, "attach(node)")
    assert "try {" in body
    assert "console.warn(PREFIX, 'attach failed', error)" in source


def test_sync_node_never_throws(source: str) -> None:
    body = _function_body(source, "syncNode(state)")
    assert "try {" in body
    assert "catch (error)" in body


def test_deferred_hook_guards_restore_and_missing_graph(source: str) -> None:
    """distributor.js's wireOutputGrowth's two litegraph findings, ported:
    the restoring flag (don't mutate under configure()'s live restore-loop
    iterator) and the graph-presence check (a node removed from the graph
    meanwhile needs no pass)."""
    body = _function_body(source, "wireRowSync(state)")
    assert "node.configure = function" in body
    assert "node.onConnectionsChange = function" in body
    assert "hook.restoring = true" in body
    assert "hook.restoring = false" in body
    assert "if (hook.restoring || !target.graph) return" in body


def test_growth_defers_past_the_litegraph_call_frame(source: str) -> None:
    body = _function_body(source, "wireRowSync(state)")
    assert "setTimeout(" in body
    assert "hook.scheduled" in body
