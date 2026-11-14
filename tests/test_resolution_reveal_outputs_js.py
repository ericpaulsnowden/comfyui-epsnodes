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
  grab(/const RESIZED_NAME_RE = [^\n]*/), grab(/function repairResizedOrder[\s\S]*?\n}\n/),
  grab(/function ensureOriginalSizeOutputs[\s\S]*?\n}\n/), grab(/function revealExtraOutputs[\s\S]*?\n}\n/),
  'function resyncSize(){}', 'return revealExtraOutputs'
].join('\n')
const revealExtraOutputs = new Function(body)()

const BACKEND = ['image','resized_image','width','height','original_width','original_height',
  'resized_2','resized_3','resized_4','resized_5','resized_6','resized_7','resized_8']
const BASE = BACKEND.slice(0, 6)

function makeNode(resized, wiredImages) {
  const links = new Map()
  const mk = (name) => ({ name, type: 'IMAGE', links: [] })
  const node = {
    graph: { _links: links },
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
{ // pre-fix saved file: outputs 2,4,3 with a consumer on the label resized_4
  const node = makeNode([2,4,3], [2,3,4]); node.wire('resized_4', 96)
  revealExtraOutputs(node)
  out.repairShuffled = { reads: node.reads(96), order: node.order() }
}
{ // pre-fix saved file with a hole: 2,4 (consumer on resized_4, image_3/4 wired)
  const node = makeNode([2,4], [2,3,4]); node.wire('resized_4', 95)
  revealExtraOutputs(node)
  out.repairHole = { reads: node.reads(95), order: node.order() }
}
{ // floating wire restored onto the resized_4 output of a saved 2,4,3 set
  const node = makeNode([2,4,3], [2,3,4])
  const floating = { id: 1, origin_slot: 7 } // saved slot of resized_4 in the old file
  node.outputs[7]._floatingLinks = new Set([floating])
  revealExtraOutputs(node)
  out.floatingMoved = { name: node.outputs[floating.origin_slot].name, slot: floating.origin_slot }
}
{ // floating wire on the output that moves DOWN (resized_3, slot 8 -> 7) plus a settled one
  const node = makeNode([2,4,3], [2,3,4]); node.wire('resized_4', 94)
  const floating = { id: 2, origin_slot: 8 }
  node.outputs[8]._floatingLinks = new Set([floating])
  revealExtraOutputs(node)
  out.floatingOther = { name: node.outputs[floating.origin_slot].name, slot: floating.origin_slot,
    settled: node.reads(94) }
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



def test_shuffled_saved_set_is_repaired_keeping_the_wired_label(probe: dict) -> None:
    got = probe["repairShuffled"]
    assert got["order"] == ["resized_2", "resized_3", "resized_4"]
    assert got["reads"] == "resized_4"


def test_holey_saved_set_is_filled_keeping_the_wired_label(probe: dict) -> None:
    got = probe["repairHole"]
    assert got["order"] == ["resized_2", "resized_3", "resized_4"]
    assert got["reads"] == "resized_4"


def test_floating_wire_follows_its_output_when_the_repair_moves_it(probe: dict) -> None:
    got = probe["floatingMoved"]
    assert got == {"name": "resized_4", "slot": 8}


def test_floating_wire_on_the_output_moving_down_keeps_its_label(probe: dict) -> None:
    got = probe["floatingOther"]
    assert got["name"] == "resized_3" and got["slot"] == 7
    assert got["settled"] == "resized_4"
