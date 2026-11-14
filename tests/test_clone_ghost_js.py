"""LGraphCanvas copy (Ctrl+C) and Alt-drag call ``node.clone()``, which builds
a node through ``createNode`` (so ``nodeCreated`` -> this pack's ``attach()``
fires), ``configure()``s it from the original's data, and throws it away: the
ghost never joins a graph (``node.graph`` stays null). Anything ``attach()``
does to the OUTSIDE world for such a node leaks or corrupts state:

* Image Grid: the ghost carries the original's uuid, collides with it, mints a
  fresh uuid and POSTs ``/eps_image_grid/clone`` -- the server copies the whole
  frame buffer into an orphan directory on every Ctrl+C.
* Frame Saver: window ``blur`` and document ``paste`` listeners that only
  ``onRemoved`` (never reached by a ghost) would remove.

Drives the REAL attach() of both modules under Node against a fake DOM and a
recording api stub. A real paste joins a graph before its deferred pass runs
and must keep working.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB = REPO_ROOT / "web" / "eps_image"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

PROBE = r"""
import { calls } from './scripts/api.js'
import { app } from './scripts/app.js'

const stub = (name) => new Proxy(function () {}, {
  get: (t, p) => p === Symbol.toPrimitive ? () => ''
    : p === 'style' ? new Proxy({}, { set: () => true, get: () => '' })
    : p === 'classList' ? { add() {}, remove() {}, toggle() {} }
    : p === 'children' ? [] : stub(p),
  set: () => true, apply: () => stub('r'),
})
const listeners = { window: [], document: [] }
globalThis.document = {
  createElement: () => stub('el'), getElementById: () => null, head: stub('head'),
  body: stub('body'), createTextNode: () => stub('t'),
  addEventListener: (type) => listeners.document.push('+' + type),
  removeEventListener: (type) => listeners.document.push('-' + type),
}
globalThis.window = {
  isSecureContext: true,
  addEventListener: (type) => listeners.window.push('+' + type),
  removeEventListener: (type) => listeners.window.push('-' + type),
}
globalThis.Node = class {}
const attachFailures = []
const realWarn = console.warn
console.warn = (...a) => { if (String(a[1]).includes('attach failed')) attachFailures.push(a.join(' ')); realWarn(...a) }
globalThis.localStorage = { getItem: () => null, setItem() {} }

const grid = await import('./extensions/comfyui-epsnodes/eps_image/image_grid.js')
const saver = await import('./extensions/comfyui-epsnodes/eps_image/frame_saver.js')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const UUID = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
const out = {}

const makeGraph = () => ({ _nodes: [], setDirtyCanvas() {}, links: new Map() })
const makeGridNode = (id, uuid) => ({
  id, comfyClass: 'EPSImageGrid', properties: uuid ? { uuid } : {}, inputs: [{ name: 'image', link: null }], outputs: [],
  widgets: [{ name: 'grid_uuid', value: uuid || '', options: {} }, { name: 'mode', value: 'Collect', options: {} }, { name: 'focus', value: '', options: {} }],
  graph: null,
  addWidget(type, name, v, cb, opts) { const w = { type, name, value: v, callback: cb, options: opts || {} }; this.widgets.push(w); return w },
  addProperty() {}, setDirtyCanvas() {}, size: [300, 300], setSize() {}, computeSize: () => [300, 300],
})
const join = (node, graph) => { node.graph = graph; graph._nodes.push(node) }
const postedClones = () => calls.filter((c) => String(c[0]).includes('/clone'))

// The original grid node, already in the live graph.
const graph = makeGraph()
app.graph = graph
const original = makeGridNode(7, UUID)
join(original, graph)
grid.init?.()
grid.attach(original)
await sleep(50)

// ---- grid: ghost whose uuid is already present at attach() time ----
calls.length = 0
const ghostA = makeGridNode(8, UUID)
grid.attach(ghostA)
await sleep(60)
out.ghostAttachOrder = { clones: postedClones().length, totalCalls: calls.length, uuid: ghostA.properties.uuid }

// ---- grid: ghost whose uuid arrives via configure() AFTER attach() ----
calls.length = 0
const ghostB = makeGridNode(9, '')
grid.attach(ghostB)
ghostB.properties.uuid = UUID
ghostB.widgets[0].value = UUID
await sleep(60)
out.ghostConfigureOrder = { clones: postedClones().length, totalCalls: calls.length, uuid: ghostB.properties.uuid }

// ---- grid: a real paste joins the graph before the deferred pass ----
calls.length = 0
const pasted = makeGridNode(10, UUID)
grid.attach(pasted)
join(pasted, graph)
await sleep(60)
const pc = postedClones()
out.paste = {
  clones: pc.length,
  body: pc.length ? JSON.parse(pc[0][1].body) : null,
  uuid: pasted.properties.uuid,
}

// ---- grid: a brand-new node in the graph mints a uuid, no clone ----
calls.length = 0
const fresh = makeGridNode(11, '')
grid.attach(fresh)
join(fresh, graph)
await sleep(60)
out.fresh = { clones: postedClones().length, uuid: fresh.properties.uuid }

// ---- frame saver ----
const makeSaverNode = () => {
  const node = {
    id: 20, comfyClass: 'EPSFrameSaver', properties: {}, inputs: [], outputs: [], graph: null,
    widgets: [{ name: 'video_path', value: '', options: {} }, { name: 'frame', value: 0, options: {} }],
    addDOMWidget(name, type, el, opts) { const w = { name, type, element: el, options: opts || {} }; this.widgets.push(w); return w },
    addWidget(type, name, v, cb, opts) { const w = { type, name, value: v, callback: cb, options: opts || {} }; this.widgets.push(w); return w },
    setDirtyCanvas() {}, size: [300, 300], setSize() {}, computeSize: () => [300, 300],
  }
  return node
}
const snap = () => ({ window: listeners.window.slice(), document: listeners.document.slice() })
const reset = () => { listeners.window.length = 0; listeners.document.length = 0 }

reset()
const ghostS = makeSaverNode()
saver.attach(ghostS)
await sleep(30)
out.saverGhost = snap()

reset()
const realS = makeSaverNode()
saver.attach(realS)
const beforeAdd = snap()
const g2 = makeGraph()
realS.graph = g2
realS.onAdded?.(g2)
const afterAdd = snap()
realS.onRemoved?.()
out.saverReal = { beforeAdd, afterAdd, afterRemove: snap() }

// a node that already has a graph at attach() time (no onAdded to come)
reset()
const preS = makeSaverNode()
preS.graph = makeGraph()
saver.attach(preS)
const preAttached = snap()
preS.onRemoved?.()
out.saverPreAttached = { attached: preAttached, afterRemove: snap() }

out.attachFailures = attachFailures
process.stdout.write('\nRESULT:' + JSON.stringify(out))
process.exit(0)
"""


@pytest.fixture(scope="module")
def probe(tmp_path_factory: pytest.TempPathFactory) -> dict:
    layout = tmp_path_factory.mktemp("ghost_layout")
    module_dir = layout / "extensions" / "comfyui-epsnodes" / "eps_image"
    module_dir.mkdir(parents=True)
    for name in ("image_grid.js", "frame_saver.js", "owned_link_colour.js"):
        shutil.copyfile(WEB / name, module_dir / name)
    scripts = layout / "scripts"
    scripts.mkdir()
    (scripts / "app.js").write_text(
        "export const app = {}\nexport class ComfyApp {}\n", encoding="utf-8"
    )
    (scripts / "api.js").write_text(
        "export const calls = []\n"
        "export const api = {\n"
        "  apiURL: (p) => p,\n"
        "  fetchApi: async (url, opts) => { calls.push([url, opts]); "
        "return { ok: true, json: async () => ({}) } },\n"
        "  addEventListener: () => {}\n"
        "}\n",
        encoding="utf-8",
    )
    (layout / "package.json").write_text('{"type":"module"}', encoding="utf-8")
    (layout / "probe.mjs").write_text(PROBE, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(layout / "probe.mjs")],
        capture_output=True,
        encoding="utf-8",
        cwd=layout,
        timeout=60,
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout.rsplit("\nRESULT:", 1)[1])


UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def test_grid_ghost_with_uuid_at_attach_makes_no_server_call(probe: dict) -> None:
    got = probe["ghostAttachOrder"]
    assert got["clones"] == 0
    assert got["totalCalls"] == 0
    assert got["uuid"] == UUID  # untouched: nothing minted


def test_grid_ghost_whose_uuid_arrives_via_configure_makes_no_server_call(probe: dict) -> None:
    got = probe["ghostConfigureOrder"]
    assert got["clones"] == 0
    assert got["totalCalls"] == 0
    assert got["uuid"] == UUID


def test_grid_real_paste_still_mints_a_uuid_and_clones_the_buffer(probe: dict) -> None:
    got = probe["paste"]
    assert got["clones"] == 1
    assert got["body"]["from"] == UUID
    assert got["body"]["to"] == got["uuid"] != UUID


def test_grid_new_node_in_graph_mints_without_cloning(probe: dict) -> None:
    got = probe["fresh"]
    assert got["clones"] == 0
    assert got["uuid"] and got["uuid"] != UUID


def test_frame_saver_ghost_registers_no_global_listeners(probe: dict) -> None:
    assert probe["attachFailures"] == []  # the mock really reaches the listener code
    assert probe["saverGhost"] == {"window": [], "document": []}


def test_frame_saver_registers_listeners_on_add_and_removes_on_remove(probe: dict) -> None:
    got = probe["saverReal"]
    assert got["beforeAdd"] == {"window": [], "document": []}
    assert got["afterAdd"] == {"window": ["+blur"], "document": ["+paste"]}
    assert got["afterRemove"] == {"window": ["+blur", "-blur"], "document": ["+paste", "-paste"]}


def test_frame_saver_node_already_in_a_graph_registers_at_attach(probe: dict) -> None:
    got = probe["saverPreAttached"]
    assert got["attached"] == {"window": ["+blur"], "document": ["+paste"]}
    assert got["afterRemove"] == {"window": ["+blur", "-blur"], "document": ["+paste", "-paste"]}


PB_PROBE = r"""
const stub = (name) => new Proxy(function () {}, {
  get: (t, p) => p === Symbol.toPrimitive ? () => ''
    : p === 'style' ? new Proxy({}, { set: () => true, get: () => '' })
    : p === 'classList' ? { add() {}, remove() {}, toggle() {} }
    : p === 'children' ? [] : stub(p),
  set: () => true, apply: () => stub('r'),
})
const events = []
globalThis.document = {
  hidden: false, getElementById: () => null, createElement: () => stub('el'), body: stub('body'), head: stub('head'),
  createTextNode: () => stub('t'),
  addEventListener: (type) => events.push('doc+' + type),
  removeEventListener: (type) => events.push('doc-' + type),
}
globalThis.window = { isSecureContext: true, addEventListener() {}, removeEventListener() {} }
globalThis.Node = class {}
globalThis.localStorage = { getItem: () => null, setItem() {} }
const realSetInterval = globalThis.setInterval
const realClearInterval = globalThis.clearInterval
let live = 0
globalThis.setInterval = (fn, ms) => { live++; events.push('interval+'); return realSetInterval(() => {}, 1e9) }
globalThis.clearInterval = (t) => { live--; events.push('interval-'); realClearInterval(t) }

const pb = await import('./extensions/comfyui-epsnodes/lora_library/prompt_builder.js')
const makeNode = () => ({
  id: 1, comfyClass: 'EPSPromptBuilder', properties: {}, inputs: [], outputs: [], graph: null,
  widgets: [{ name: 'file', value: '', options: {} }, { name: 'blocks', value: '[]', options: {} }],
  addDOMWidget(name, type, el, opts) { const w = { name, type, element: el, options: opts || {} }; this.widgets.push(w); return w },
  addWidget(type, name, v, cb, opts) { const w = { type, name, value: v, callback: cb, options: opts || {} }; this.widgets.push(w); return w },
  setDirtyCanvas() {}, size: [300, 300], setSize() {}, computeSize: () => [300, 300],
})
const out = {}
const warns = []
console.warn = (...a) => warns.push(a.join(' '))

const ghost = makeNode()
pb.attachPromptBuilderPanel(ghost)
out.ghost = { events: events.splice(0), live }

const real = makeNode()
pb.attachPromptBuilderPanel(real)
const before = events.splice(0)
real.graph = {}
real.onAdded?.({})
const added = events.splice(0)
real.onRemoved()
out.real = { before, added, removed: events.splice(0), live }
out.warns = warns.filter((w) => w.includes('attachPromptBuilderPanel failed'))
process.stdout.write('\nRESULT:' + JSON.stringify(out))
process.exit(0)
"""


@pytest.fixture(scope="module")
def pb_probe(tmp_path_factory: pytest.TempPathFactory) -> dict:
    layout = tmp_path_factory.mktemp("pb_ghost_layout")
    shutil.copytree(
        REPO_ROOT / "web" / "lora_library",
        layout / "extensions" / "comfyui-epsnodes" / "lora_library",
    )
    scripts = layout / "scripts"
    scripts.mkdir()
    (scripts / "app.js").write_text("export const app = {}\n", encoding="utf-8")
    (scripts / "api.js").write_text(
        "export const api = { apiURL: (p) => p, fetchApi: async () => ({ ok: true, json: async () => ({}) }),"
        " addEventListener() {} }\n",
        encoding="utf-8",
    )
    (layout / "package.json").write_text('{"type":"module"}', encoding="utf-8")
    (layout / "probe.mjs").write_text(PB_PROBE, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(layout / "probe.mjs")],
        capture_output=True,
        encoding="utf-8",
        cwd=layout,
        timeout=60,
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout.rsplit("\nRESULT:", 1)[1])


def test_prompt_builder_ghost_starts_no_poll_and_no_visibility_listener(pb_probe: dict) -> None:
    assert pb_probe["warns"] == []  # the mock really reaches the poll code
    assert pb_probe["ghost"] == {"events": [], "live": 0}


def test_prompt_builder_polls_once_added_and_stops_on_remove(pb_probe: dict) -> None:
    got = pb_probe["real"]
    assert got["before"] == []
    assert got["added"] == ["interval+", "doc+visibilitychange"]
    assert got["removed"] == ["interval-", "doc-visibilitychange"]
    assert got["live"] == 0
