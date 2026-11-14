"""EPS Resolution `revealExtraOutputs` (FORMAT.md §6.5): the resized_N tail
must stay contiguous and in backend order, because litegraph's
LGraphNode.removeOutput splices the outputs array and decrements
`origin_slot` on every later link -- trimming an unwired resized_3 BELOW a
wired resized_4 silently re-points the consumer at the backend's resized_3.

Runs the REAL function text under Node against a fake node whose
addOutput/removeOutput follow litegraph, and reports which BACKEND output
each link now reads (backend order = RETURN_TYPES order)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RESOLUTION_JS = REPO_ROOT / "web" / "eps_image" / "resolution.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

PROBE = r"""
import fs from 'node:fs'
const src = fs.readFileSync(process.argv[2], 'utf8').replace(/\r\n/g, '\n') // CRLF checkouts (Windows CI)
const grab = (re) => { const m = re.exec(src); if (!m) throw new Error('missing ' + re); return m[0] }
const body = [
  grab(/const ORIGINAL_SIZE_NAMES = [^\n]*/), grab(/const ORIGINAL_SIZE_TYPE = [^\n]*/),
  grab(/const EXTRA_IMAGE_RE = [^\n]*/), grab(/const MAX_IMAGES = [^\n]*/), grab(/const RESIZED_PREFIX = [^\n]*/),
  grab(/function outputIndexByName[\s\S]*?\n}\n/),
  grab(/export function isOutputConnected[\s\S]*?\n}\n/).replace('export ', ''),
  grab(/const RESIZED_NAME_RE = [^\n]*/), grab(/const NODE_TYPE = [^\n]*/), grab(/const savedWireIntent = [^\n]*/), grab(/const savedFloatingIntent = [^\n]*/),
  grab(/function recordIntentFromScope[\s\S]*?\n}\n/), grab(/export function beforeConfigureGraph[\s\S]*?\n}\n/).replace('export ', ''),
  grab(/function applySavedOutputIntent[\s\S]*?\n}\n/), grab(/const PREFIX = [^\n]*/),
  grab(/function ensureOriginalSizeOutputs[\s\S]*?\n}\n/), grab(/function revealExtraOutputs[\s\S]*?\n}\n/),
  'function resyncSize(){}', 'return { revealExtraOutputs, beforeConfigureGraph, applySavedOutputIntent, savedWireIntent, savedFloatingIntent }'
].join('\n')
const { revealExtraOutputs, beforeConfigureGraph, applySavedOutputIntent, savedWireIntent, savedFloatingIntent } = new Function(body)()

const BACKEND = ['image','resized_image','width','height','original_width','original_height',
  'resized_2','resized_3','resized_4','resized_5','resized_6','resized_7','resized_8']
const BASE = BACKEND.slice(0, 6)
globalThis.LiteGraph = { registered_node_types: { EPSResolution: { nodeData: { output_name: BACKEND } } } }

function makeNode(resized, wiredImages) {
  const links = new Map()
  const mk = (name) => ({ name, type: 'IMAGE', links: [] })
  const node = {
    id: 5,
    graph: { _links: links, isRootGraph: true },
    graphLinks: links,
    inputs: [{ name: 'image', link: 1 }, ...[2,3,4,5,6,7,8].map((n) => (
      { name: 'image_' + n, link: wiredImages.includes(n) ? 100 + n : null }))],
    outputs: [...BASE, ...resized.map((n) => 'resized_' + n)].map(mk),
    addOutput(name, type) { this.outputs.push({ name, type, links: [] }) },
    // LGraphNode.removeOutput: splice, then origin_slot-- on every later link
    removeOutput(slot) {
      this.outputs.splice(slot, 1)
      for (let i = slot; i < this.outputs.length; i++)
        for (const id of this.outputs[i].links) links.get(id).origin_slot--
    },
    wire(name, id) {
      const slot = this.outputs.findIndex((o) => o.name === name)
      links.set(id, { id, origin_slot: slot }); this.outputs[slot].links.push(id)
    },
    setImage(n, linked) { this.inputs.find((i) => i.name === 'image_' + n).link = linked ? 100 + n : null },
    // which BACKEND output a consumer link reads
    reads(id) { return BACKEND[links.get(id).origin_slot] },
    order() { return this.outputs.slice(6).map((o) => o.name) },
  }
  return node
}

const out = {}
{ // consumer on resized_4; unplug image_4 then image_3; then re-wire image_3
  const node = makeNode([2,3,4], [2,3,4]); node.wire('resized_4', 99)
  node.setImage(4, false); node.setImage(3, false); revealExtraOutputs(node)
  out.afterUnplug = { reads: node.reads(99), order: node.order() }
  node.setImage(3, true); revealExtraOutputs(node)
  out.afterRewire = { reads: node.reads(99), order: node.order() }
}
{ // plain unwired tail still trims
  const node = makeNode([2,3,4,5], [2,3,4,5]); node.wire('resized_2', 98)
  for (const n of [5,4,3]) node.setImage(n, false)
  revealExtraOutputs(node)
  out.plainTrim = { reads: node.reads(98), order: node.order() }
}
{ // growing appends in order
  const node = makeNode([2], [2,3,4]); revealExtraOutputs(node)
  out.grow = { order: node.order() }
}
{ // wired resized_3 holds everything below it; only the unwired tail above goes
  const node = makeNode([2,3,4,5], [2,3,4,5]); node.wire('resized_3', 97)
  for (const n of [5,4,3,2]) node.setImage(n, false)
  revealExtraOutputs(node)
  out.heldBelowWired = { reads: node.reads(97), order: node.order() }
}
// ---- saved-intent restore: a fake whose configure behaves like the real one ----
// LGraphNode.configure on a fresh node: the constructor built the canonical
// outputs, saved entries' `links` are applied BY INDEX, names stay canonical,
// then onConfigure(info) runs; links keep their saved origin_slot.
function forgedNode(savedResized, nodeId = 5) {
  const outputs = [...BASE, ...savedResized.map(([name]) => name)].map((name) => ({ name, links: null }))
  savedResized.forEach(([, ids], i) => { outputs[6 + i].links = ids })
  return { id: nodeId, type: 'EPSResolution', outputs }
}
function loadSaved(savedResized, linksById, wiredImages,
    { subgraph = false, floating = [], loadedId = 5 } = {}) {
  const saved = forgedNode(savedResized)
  const nodes = subgraph ? [] : [saved]
  const graphData = subgraph
    ? { nodes, definitions: { subgraphs: [{ id: 'sg-1', nodes: [saved], floatingLinks: floating }] } }
    : { nodes, floatingLinks: floating }
  beforeConfigureGraph(graphData) // sees the RAW names
  // ComfyUI normalizes, litegraph applies saved links BY INDEX: names stay canonical
  const node = makeNode([2,3,4,5,6,7,8], wiredImages)
  node.id = loadedId // the frontend may de-duplicate node ids AFTER the hook ran
  if (subgraph) node.graph.isRootGraph = false, node.graph.id = 'sg-1'
  saved.outputs.forEach((entry, i) => { if (entry.links) node.outputs[i].links = [...entry.links] })
  for (const [id, slot] of Object.entries(linksById)) node.graphLinks.set(Number(id), { id: Number(id), origin_slot: slot })
  // saved floating wires restore onto the output at their saved slot (by index)
  node.floatingRestored = floating.map((f) => ({ ...f }))
  for (const f of node.floatingRestored) (node.outputs[f.origin_slot]._floatingLinks ??= new Set()).add(f)
  return node
}
function deferredPass(node) { revealExtraOutputs(node); applySavedOutputIntent(node) }
const warnings = []
console.warn = (...args) => warnings.push(args.join(' '))
{ // pre-fix save [resized_2, resized_4], consumer link 10 at slot 7 (saved name resized_4)
  const node = loadSaved([['resized_2', null], ['resized_4', [10]]], { 10: 7 }, [2, 3, 4])
  out.intentBefore = node.reads(10) // loads on the canonical resized_3
  deferredPass(node)
  out.intentRestored = { reads: node.reads(10), order: node.order(),
    listedOn: node.outputs.filter((o) => o.links.includes(10)).map((o) => o.name) }
}
{ // a canonical save is untouched
  const node = loadSaved([['resized_2', null], ['resized_3', [11]]], { 11: 7 }, [2, 3])
  deferredPass(node)
  out.canonical = { reads: node.reads(11), order: node.order(), recorded: savedWireIntent.size + savedFloatingIntent.size }
}
{ // the same forged save, node inside a subgraph definition
  const node = loadSaved([['resized_2', null], ['resized_4', [13]]], { 13: 7 }, [2, 3, 4], { subgraph: true })
  deferredPass(node)
  out.subgraph = { reads: node.reads(13) }
}
{ // frontend dedup renames the node (5 -> 7) between record and apply
  const node = loadSaved([['resized_2', null], ['resized_4', [14]]], { 14: 7 }, [2, 3, 4],
    { subgraph: true, loadedId: 7 })
  deferredPass(node)
  out.dedup = { reads: node.reads(14), id: node.id }
}
{ // saved floating wire from the displaced resized_4 (slot 7)
  const node = loadSaved([['resized_2', null], ['resized_4', null]], {}, [2, 3, 4],
    { floating: [{ id: 300, origin_id: 5, origin_slot: 7 }] })
  const [wire] = node.floatingRestored
  const before = node.outputs[wire.origin_slot].name
  deferredPass(node)
  out.floating = { before, name: node.outputs[wire.origin_slot].name, slot: wire.origin_slot,
    oldHolds: node.outputs[7]._floatingLinks.has(wire) }
}
{ // intended output not wired: link stays, one warning
  const node = loadSaved([['resized_2', null], ['resized_4', [12]]], { 12: 7 }, [2, 3])
  deferredPass(node)
  out.unwired = { reads: node.reads(12), warnings: warnings.length }
}
process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def probe(tmp_path_factory: pytest.TempPathFactory) -> dict:
    script = tmp_path_factory.mktemp("reveal") / "probe.mjs"
    script.write_text(PROBE, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(script), str(RESOLUTION_JS)], capture_output=True, encoding="utf-8", timeout=60
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_unplugging_below_a_wired_output_keeps_the_consumer_on_its_output(probe: dict) -> None:
    got = probe["afterUnplug"]
    assert got["reads"] == "resized_4"
    assert got["order"] == ["resized_2", "resized_3", "resized_4"]


def test_rewiring_a_lower_slot_keeps_backend_order(probe: dict) -> None:
    got = probe["afterRewire"]
    assert got["reads"] == "resized_4"
    assert got["order"] == ["resized_2", "resized_3", "resized_4"]


def test_plain_unwired_tail_still_trims(probe: dict) -> None:
    got = probe["plainTrim"]
    assert got["order"] == ["resized_2"]
    assert got["reads"] == "resized_2"


def test_growing_appends_in_order(probe: dict) -> None:
    assert probe["grow"]["order"] == ["resized_2", "resized_3", "resized_4"]


def test_trim_stops_at_first_wired_output_from_the_top(probe: dict) -> None:
    got = probe["heldBelowWired"]
    assert got["order"] == ["resized_2", "resized_3"]
    assert got["reads"] == "resized_3"



def test_prefix_save_loads_wrong_then_deferred_pass_restores_the_saved_name(probe: dict) -> None:
    assert probe["intentBefore"] == "resized_3"  # what litegraph's by-index configure gives
    got = probe["intentRestored"]
    assert got["reads"] == "resized_4"
    assert got["listedOn"] == ["resized_4"]
    assert got["order"] == ["resized_2", "resized_3", "resized_4"]


def test_canonical_save_is_untouched(probe: dict) -> None:
    got = probe["canonical"]
    assert got["recorded"] == 0 and got["reads"] == "resized_3"
    assert got["order"] == ["resized_2", "resized_3"]


def test_unwired_intended_output_keeps_the_link_and_warns_once(probe: dict) -> None:
    got = probe["unwired"]
    assert got["reads"] == "resized_3" and got["warnings"] == 1


def test_configure_defers_the_reveal_so_floating_wires_are_restored_first() -> None:
    """LGraph.configure restores floating links only AFTER node.configure
    returns; the resized_N repair reorders outputs, so the onConfigure hook
    must not run revealExtraOutputs inline (a wire restored afterwards would
    land on the output now at its saved slot). Source-level pin: attach()'s
    onConfigure body schedules the converge and never calls the reveal."""
    source = RESOLUTION_JS.read_text(encoding="utf-8")
    start = source.index("const originalOnConfigureV61")
    end = source.index("scheduleImageConverge(node)\n}", start)
    body = source[start:end]
    assert "scheduleImageConverge(this)" in body
    assert not re.search(r"\brevealExtraOutputs\(|\bconvergeExtraImageInputs\(", body)


def test_save_inside_a_subgraph_definition_is_restored_too(probe: dict) -> None:
    assert probe["subgraph"]["reads"] == "resized_4"


def test_frontend_node_id_dedup_between_record_and_apply_does_not_lose_the_intent(
    probe: dict,
) -> None:
    got = probe["dedup"]
    assert got["id"] == 7 and got["reads"] == "resized_4"


def test_saved_floating_wire_follows_its_saved_output_name(probe: dict) -> None:
    got = probe["floating"]
    assert got["before"] == "resized_3"  # where the by-index restore puts it
    assert got["name"] == "resized_4" and got["slot"] == 8
    assert got["oldHolds"] is False


def test_deferred_converge_applies_the_intent_after_grow_and_trim() -> None:
    source = RESOLUTION_JS.read_text(encoding="utf-8")
    start = source.index("function scheduleImageConverge(node)")
    body = source[start : source.index("\n}\n", start)]
    assert body.index("revealExtraOutputs(node)") < body.index("applySavedOutputIntent(node)")


def test_extension_forwards_the_raw_graph_hooks_to_resolution() -> None:
    entry = (REPO_ROOT / "web" / "eps_image.js").read_text(encoding="utf-8")
    assert "beforeConfigureGraph(graphData)" in entry
    assert "resolution.beforeConfigureGraph?.(graphData)" in entry
    assert "resolution.afterConfigureGraph?.()" in entry
