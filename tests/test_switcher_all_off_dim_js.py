"""All-off switchers LOOK unplugged (owner report 2026-11-05: "if all of the
toggles are off it should look like nothing is connected").

``web/eps_image/switcher.js`` dims every OUTPUT link of a switcher whose
every wired row is toggled off (or that has nothing wired) to §6.6
"Collect only"'s grey, through the shared ``owned_link_colour.js``
``renderLink`` hook -- a BY-HAND copy of image_grid.js's / save_image.js's
``reconcileLinkDimming`` (owner tag, exact restore, resync hook, cleanup on
removal), never unplugging anything.

The pure decision (``isSwitcherAllOff``) and the reconcile are exported, so
this file drives them -- and ``attach()`` end to end (row click, header
toggle-all, the ``toggles`` widget callback, connect/disconnect on inputs
and outputs, configure, removal) -- under Node with a small litegraph-shaped
fake, in tests/test_switcher_js.py's served layout (``switcher.js`` imports
``../../../scripts/app.js`` and ``./owned_link_colour.js``). Pixels on a
real canvas are for the rig.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
EPS_WEB = REPO_ROOT / "web" / "eps_image"
SWITCHER_JS = EPS_WEB / "switcher.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

DIM = "rgba(128,128,128,0.25)"
OWNER = "EPSSwitcher:all-off"

PROBE_JS = r"""
import * as s from './extensions/comfyui-epsnodes/eps_image/switcher.js'

const INPUT = 1
const OUTPUT = 2
const tick = () => new Promise((resolve) => setTimeout(resolve, 0))
const settle = async () => { await tick(); await tick() }

// A canvas class for ensureOwnedLinkColourHook() to find and wrap.
globalThis.LiteGraph = { LGraphCanvas: { prototype: { renderLink() {} } } }

let nextLinkId = 1

function makeGraph() {
  const graph = { links: new Map(), _nodes: [], dirty: 0 }
  graph.setDirtyCanvas = () => { graph.dirty += 1 }
  graph.add = (node) => { graph._nodes.push(node); node.graph = graph }
  graph.getNodeById = (id) => graph._nodes.find((n) => n.id === id) || null
  return graph
}

function linkUp(graph, origin, slot, target, targetSlot) {
  const idx = typeof targetSlot === 'string'
    ? target.inputs.findIndex((i) => i.name === targetSlot)
    : targetSlot
  const input = target.inputs[idx]
  const link = {
    id: nextLinkId++, origin_id: origin.id, origin_slot: slot,
    target_id: target.id, target_slot: idx, type: input.type
  }
  graph.links.set(link.id, link)
  const out = origin.outputs[slot]
  out.links = [...(out.links || []), link.id]
  input.link = link.id
  origin.onConnectionsChange?.(OUTPUT, slot, true, link, out)
  target.onConnectionsChange?.(INPUT, idx, true, link, input)
  return link
}

function makeLoader(id) {
  return {
    id, comfyClass: 'LoadImage', inputs: [], widgets: [],
    outputs: [{ name: 'IMAGE', type: 'IMAGE', links: [] }]
  }
}

// A downstream consumer with distributor.js's optional resync hook.
function makeTarget(id) {
  const node = {
    id, comfyClass: 'PreviewImage', widgets: [], outputs: [], resyncs: 0,
    inputs: [
      { name: 'images', type: 'IMAGE', link: null },
      { name: 'masks', type: 'MASK', link: null },
      { name: 'extra', type: 'IMAGE', link: null }
    ]
  }
  node.__epsResyncLinkColors = () => { node.resyncs += 1 }
  return node
}

function makeSwitcher(id) {
  return {
    id, comfyClass: 'EPSSwitcher', pos: [0, 0], size: [270, 100],
    flags: {}, properties: {},
    inputs: [
      { name: 'image_1', type: 'IMAGE', link: null },
      { name: 'toggles', type: 'STRING', link: null, widget: { name: 'toggles' } },
      { name: 'pass_masks', type: 'BOOLEAN', link: null, widget: { name: 'pass_masks' } }
    ],
    outputs: [
      { name: 'images', type: 'IMAGE', links: null },
      { name: 'masks', type: 'MASK', links: null }
    ],
    widgets: [
      { name: 'toggles', type: 'text', value: '{}', options: {} },
      { name: 'pass_masks', type: 'toggle', value: true, options: {} }
    ],
    addProperty(name, value) { this.properties[name] = value },
    setSize(size) { this.size = size },
    computeSize() { return [270, 100] },
    setDirtyCanvas() {},
    addInput(name, type, extra) {
      const input = { name, type, link: null, ...(extra || {}) }
      this.inputs.push(input)
      return input
    },
    removeInput(slot) {
      this.disconnectInput(slot)
      this.inputs.splice(slot, 1)
      for (let i = slot; i < this.inputs.length; i++) {
        const link = this.graph?.links.get(this.inputs[i].link)
        if (link) link.target_slot = i
      }
    },
    disconnectInput(slot) {
      const input = this.inputs[slot]
      if (!input || input.link == null) return false
      const link = this.graph?.links.get(input.link)
      input.link = null
      if (link) {
        this.graph.links.delete(link.id)
        const origin = this.graph.getNodeById(link.origin_id)
        const out = origin?.outputs?.[link.origin_slot]
        if (out?.links) out.links = out.links.filter((linkId) => linkId !== link.id)
      }
      this.onConnectionsChange?.(INPUT, slot, false, link, input)
      return true
    },
    addOutput(name, type) { this.outputs.push({ name, type, links: null }) },
    removeOutput(idx) { this.outputs.splice(idx, 1) },
    addCustomWidget(widget) { this.widgets.push(widget); return widget },
    getConnectionPos(isInput, slot) { return [0, 10 + slot * 20] },
    // LGraphNode.configure's tail: widgets_values restored, then
    // onConfigure(info) -- the hook the dim re-derives from.
    configure(info) {
      if (info.widgets_values) {
        let n = 0
        for (const widget of this.widgets) {
          if (widget.serialize === false) continue
          if (n >= info.widgets_values.length) break
          widget.value = info.widgets_values[n++]
        }
      }
      this.onConfigure?.(info)
    }
  }
}

function makeCtx() {
  const noop = () => {}
  return {
    texts: [], save: noop, restore: noop, beginPath: noop, roundRect: noop,
    rect: noop, fill: noop, stroke: noop, moveTo: noop, lineTo: noop, fillText: noop
  }
}

function clickRow(node, name) {
  node.onDrawForeground(makeCtx(), null, null)
  const rect = node.__epsSwitcherRowRects.find((r) => r.name === name)
  if (!rect) throw new Error(`no toggle box drawn for ${name}`)
  node.onMouseDown({}, [rect.x + 1, rect.y + 1], null)
}

function clickHeader(node) {
  const header = node.widgets.find((w) => w.name === '__eps_switcher_toggle_all')
  header.mouse({ type: 'pointerdown' }, [10, 10], node)
}

const outLinks = (sw) =>
  sw.outputs.flatMap((o) => (o.links || []).map((id) => sw.graph.links.get(id)))
const view = (sw) => outLinks(sw).map((l) => ({
  id: l.id, color: l.color ?? null, owner: l.__epsLinkColorOwner ?? null
}))

async function build() {
  const graph = makeGraph()
  const sw = makeSwitcher(1)
  const a = makeLoader(10)
  const b = makeLoader(11)
  const t = makeTarget(20)
  for (const n of [sw, a, b, t]) graph.add(n)
  s.attach(sw)
  linkUp(graph, a, 0, sw, 'image_1')
  await settle()
  linkUp(graph, b, 0, sw, 'image_2')
  await settle()
  const imagesLink = linkUp(graph, sw, 0, t, 'images')
  const masksLink = linkUp(graph, sw, 1, t, 'masks')
  return { graph, sw, a, b, t, imagesLink, masksLink }
}

const out = {}

// --- pure decision ---------------------------------------------------------
{
  const graph = makeGraph()
  const sw = makeSwitcher(1)
  graph.add(sw)
  const pure = {}
  pure.nothingWired = s.isSwitcherAllOff(sw)
  sw.inputs[0].link = 99
  pure.oneWiredOn = s.isSwitcherAllOff(sw)
  sw.widgets[0].value = '{"image_1": false}'
  pure.oneWiredOff = s.isSwitcherAllOff(sw)
  sw.inputs.push({ name: 'image_2', type: 'IMAGE', link: 98 })
  pure.oneOfTwoOff = s.isSwitcherAllOff(sw)
  sw.widgets[0].value = '{"image_1": false, "image_2": false}'
  pure.bothOff = s.isSwitcherAllOff(sw)
  sw.inputs[1].link = 97 // `toggles` arrives over a wire: not knowable
  pure.togglesWired = s.isSwitcherAllOff(sw)
  sw.inputs[1].link = null
  sw.inputs.push({ name: 'mask_3', type: 'MASK', link: 96 }) // a partner is not a row
  pure.partnerOnlyDoesNotCount = s.isSwitcherAllOff(sw)
  sw.widgets[0].value = 'not json'
  pure.malformedTogglesMeansAllOn = s.isSwitcherAllOff(sw)
  pure.otherClass = s.isSwitcherAllOff({ comfyClass: 'PreviewImage', inputs: [] })
  pure.nullNode = s.isSwitcherAllOff(null)
  out.pure = pure
  out.owner = s.LINK_COLOR_OWNER_SWITCHER
}

// --- row clicks: dim on the last row off, undim on the first row back -------
{
  const rig = await build()
  const { sw, t, imagesLink } = rig
  imagesLink.color = '#abcdef' // a prior colour another file set
  const steps = { wired: view(sw) }
  clickRow(sw, 'image_1')
  steps.oneOff = view(sw)
  clickRow(sw, 'image_2')
  steps.allOff = view(sw)
  steps.hookInstalled = globalThis.LiteGraph.LGraphCanvas.prototype.renderLink.__epsOwnedLinkColour === true
  steps.stillPlugged = {
    links: rig.graph.links.size,
    images: sw.outputs[0].links.length,
    masks: sw.outputs[1].links.length,
    targetImages: t.inputs[0].link === imagesLink.id
  }
  clickRow(sw, 'image_2')
  steps.oneBack = view(sw)
  steps.resyncs = t.resyncs
  out.rowClicks = steps
}

// --- header toggle-all ------------------------------------------------------
{
  const { sw } = await build()
  const steps = {}
  clickHeader(sw) // all on -> all off
  steps.afterAllOff = view(sw)
  clickHeader(sw) // all off -> all on
  steps.afterAllOn = view(sw)
  out.header = steps
}

// --- the `toggles` widget's own callback (Universal State Apply) -------------
{
  const { sw } = await build()
  const widget = sw.widgets.find((w) => w.name === 'toggles')
  widget.value = '{"image_1": false, "image_2": false}'
  widget.callback?.(widget.value, null, sw)
  const dimmed = view(sw)
  widget.value = '{}'
  widget.callback?.(widget.value, null, sw)
  out.widgetCallback = { dimmed, restored: view(sw) }
}

// --- wiring: unwiring every row dims; a new output link lands dimmed --------
{
  const rig = await build()
  const { sw, graph, t, a } = rig
  const steps = {}
  sw.disconnectInput(sw.inputs.findIndex((i) => i.name === 'image_1'))
  await settle()
  steps.oneUnwired = view(sw)
  sw.disconnectInput(sw.inputs.findIndex((i) => i.name === 'image_2'))
  await settle()
  steps.noneWired = view(sw)
  const late = linkUp(graph, sw, 0, t, 'extra')
  steps.lateLink = { color: late.color ?? null, owner: late.__epsLinkColorOwner ?? null }
  linkUp(graph, a, 0, sw, 'image_1')
  await settle()
  steps.rewired = view(sw)
  out.wiring = steps
}

// --- toggles wired: never dimmed, even with every row off -------------------
{
  const rig = await build()
  const { sw, graph } = rig
  const src = { id: 30, comfyClass: 'PrimitiveString', inputs: [], widgets: [],
    outputs: [{ name: 'STRING', type: 'STRING', links: [] }] }
  graph.add(src)
  sw.widgets[0].value = '{"image_1": false, "image_2": false}'
  linkUp(graph, src, 0, sw, 'toggles')
  out.togglesWired = view(sw)
}

// --- configure (load / undo / paste) re-derives from the restored toggles ---
{
  const rig = await build()
  const { sw } = rig
  // A load hands out FRESH, undimmed links; configure restores the toggles.
  sw.configure({ widgets_values: ['{"image_1": false, "image_2": false}', true] })
  const dimmed = view(sw)
  sw.configure({ widgets_values: ['{}', true] })
  out.configure = { dimmed, restored: view(sw) }
}

// --- another owner's claim is left alone; removal restores ours -------------
{
  const rig = await build()
  const { sw, masksLink, imagesLink } = rig
  masksLink.color = '#123456'
  masksLink.__epsLinkColorOwner = 'EPSImageGrid:collect-only'
  clickHeader(sw)
  const dimmed = view(sw)
  sw.onRemoved?.()
  out.ownersAndRemoval = {
    dimmed,
    afterRemove: view(sw),
    imagesColor: imagesLink.color ?? null
  }
}

process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def probe(tmp_path_factory: pytest.TempPathFactory, install_switcher_js) -> dict:
    layout = tmp_path_factory.mktemp("web_root_all_off")
    install_switcher_js(layout)
    (layout / "probe.mjs").write_text(PROBE_JS, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(layout / "probe.mjs")],
        capture_output=True,
        encoding="utf-8",
        timeout=60,
        cwd=layout,
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


def _colours(view: list[dict]) -> list[tuple]:
    return [(link["color"], link["owner"]) for link in view]


def test_pure_all_off_decision(probe: dict) -> None:
    """Mirrors nodes_switcher.py `_switcher_is_statically_all_off`: nothing
    wired or every WIRED row literally off; never while `toggles` is wired;
    a partner socket is not a row; malformed toggles = all on."""
    assert probe["pure"] == {
        "nothingWired": True,
        "oneWiredOn": False,
        "oneWiredOff": True,
        "oneOfTwoOff": False,
        "bothOff": True,
        "togglesWired": False,
        "partnerOnlyDoesNotCount": True,
        "malformedTogglesMeansAllOn": False,
        "otherClass": False,
        "nullNode": False,
    }
    assert probe["owner"] == OWNER


def test_last_row_off_dims_every_output_and_one_back_restores_exactly(probe: dict) -> None:
    steps = probe["rowClicks"]
    assert _colours(steps["wired"]) == [("#abcdef", None), (None, None)]
    assert _colours(steps["oneOff"]) == [("#abcdef", None), (None, None)]
    # images AND masks both dim the moment the last row goes off.
    assert _colours(steps["allOff"]) == [(DIM, OWNER), (DIM, OWNER)]
    assert steps["hookInstalled"] is True
    # Never unplugged: every link still exists and is still seated.
    assert steps["stillPlugged"] == {"links": 4, "images": 1, "masks": 1, "targetImages": True}
    # One row back on: the EXACT prior colours, tags cleared, target resynced.
    assert _colours(steps["oneBack"]) == [("#abcdef", None), (None, None)]
    assert steps["resyncs"] == 2


def test_header_toggle_all_dims_and_restores(probe: dict) -> None:
    assert _colours(probe["header"]["afterAllOff"]) == [(DIM, OWNER), (DIM, OWNER)]
    assert _colours(probe["header"]["afterAllOn"]) == [(None, None), (None, None)]


def test_toggles_widget_callback_rederives_the_dim(probe: dict) -> None:
    assert _colours(probe["widgetCallback"]["dimmed"]) == [(DIM, OWNER), (DIM, OWNER)]
    assert _colours(probe["widgetCallback"]["restored"]) == [(None, None), (None, None)]


def test_unwiring_every_row_dims_and_a_late_output_link_lands_dimmed(probe: dict) -> None:
    steps = probe["wiring"]
    assert _colours(steps["oneUnwired"]) == [(None, None), (None, None)]
    assert _colours(steps["noneWired"]) == [(DIM, OWNER), (DIM, OWNER)]
    assert steps["lateLink"] == {"color": DIM, "owner": OWNER}
    assert {link["color"] for link in steps["rewired"]} == {None}
    assert {link["owner"] for link in steps["rewired"]} == {None}


def test_wired_toggles_input_is_never_dimmed(probe: dict) -> None:
    assert _colours(probe["togglesWired"]) == [(None, None), (None, None)]


def test_configure_rederives_from_the_restored_toggles(probe: dict) -> None:
    assert _colours(probe["configure"]["dimmed"]) == [(DIM, OWNER), (DIM, OWNER)]
    assert _colours(probe["configure"]["restored"]) == [(None, None), (None, None)]


def test_other_owner_left_alone_and_removal_restores(probe: dict) -> None:
    result = probe["ownersAndRemoval"]
    assert _colours(result["dimmed"]) == [
        (DIM, OWNER),
        ("#123456", "EPSImageGrid:collect-only"),
    ]
    assert _colours(result["afterRemove"]) == [
        (None, None),
        ("#123456", "EPSImageGrid:collect-only"),
    ]
    assert result["imagesColor"] is None


# ------------------------------------------------------------ source pins


@pytest.fixture(scope="module")
def source() -> str:
    return SWITCHER_JS.read_text(encoding="utf-8")


def test_by_hand_copy_not_an_import_of_image_grid(source: str) -> None:
    """docs/ROADMAP-shared-panel-code.md: helpers are duplicated per file;
    only the renderLink hook is shared (it must wrap exactly once)."""
    assert "from './image_grid.js'" not in source
    assert "import { ensureOwnedLinkColourHook } from './owned_link_colour.js'" in source
    assert "const DIM_LINK_COLOR = 'rgba(128,128,128,0.25)'" in source
    assert "const LINK_COLOR_OWNER_KEY = '__epsLinkColorOwner'" in source
    assert f"export const LINK_COLOR_OWNER_SWITCHER = '{OWNER}'" in source


def test_every_trigger_rederives_and_attach_installs_them(source: str) -> None:
    # Every trigger goes through `syncAllOff`: unplug/replug FIRST, then the
    # dim (which only colours the links that remain).
    assert "syncAllOff(node, 'row click')" in source
    assert "syncAllOff(mouseNode, 'toggle all')" in source
    assert "syncAllOff(node, 'toggles callback')" in source
    assert "syncAllOff(this, 'onConnectionsChange')" in source
    # An OUTPUT link landing only dims -- it is never unplugged on arrival.
    conn = source.split("function installLinkDimConnectionsSync(node) {", 1)[1]
    conn = conn.split("\n}\n", 1)[0]
    assert "type === LINK_SLOT_OUTPUT && isConnected" in conn
    assert "syncLinkDimming(this, 'output link landed')" in conn
    sync = source.split("function syncAllOff(node, where) {", 1)[1].split("\n}\n", 1)[0]
    assert sync.index("reconcileUnplug(node)") < sync.index("syncLinkDimming(node, where)")
    configure = source.split("function installLinkDimConfigureSync(node) {", 1)[1]
    configure = configure.split("\n}\n", 1)[0]
    assert "syncLinkDimming(this, 'onConfigure')" in configure
    assert "scheduleUnplugReconcile(this)" in configure
    attach = source.split("export function attach(node) {", 1)[1]
    for call in (
        "installUnplugConfigureGuard(node)",
        "installTogglesDimSync(node)",
        "installLinkDimConnectionsSync(node)",
        "installLinkDimConfigureSync(node)",
        "installLinkDimCleanup(node)",
        "reconcileLinkDimming(node)",
    ):
        assert call in attach, call


def test_no_toast_on_the_dim_path(source: str) -> None:
    section = source.split("export function isSwitcherAllOff(node) {", 1)[1]
    section = section.split("// Node width floor", 1)[0]
    assert "toast" not in section.lower()
