"""Tests for EPS Group Bypass (FORMAT.md section 6.20) -- the backend
declaration (``eps_image/nodes_group_bypass.py``) and the frontend that does
the work (``web/eps_image/group_bypass.js``).

The frontend half drives the REAL, unmodified module under Node against a
small hand-built graph: nodes with widgets and modes, a link table, groups
with litegraph's ``recomputeInsideNodes``/``nodes`` shape. Each scenario
calls ``attach()`` and then ``refreshAll()`` (what the 200 ms poll calls) and
reads back the members' modes.

What this CANNOT cover, and the rig must: a real Number Controller row
adopting BOOLEAN when wired into the switch's socket, the label showing in
both renderers, and core's real group membership.
"""

# ruff: noqa: E501 -- the embedded Node harness (HARNESS) is JavaScript in a raw
# string; wrapping it would only hurt readability.

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from eps_image.nodes_group_bypass import NO_GROUP, EPSGroupBypass
from tests.served_layout import build_served_layout

NODE = shutil.which("node")

GROUP_BYPASS_MODULES = ("group_bypass.js", "number_controller.js", "button_row.js")

# ------------------------------------------------------------------ backend


def test_widget_order_is_the_persistence_contract() -> None:
    spec = EPSGroupBypass.INPUT_TYPES()
    assert list(spec["required"]) == ["enabled", "mute", "group"]
    assert list(spec["optional"]) == ["group_id"]
    assert spec["optional"]["group_id"][1]["hidden"] is True


def test_defaults_leave_a_fresh_node_harmless() -> None:
    spec = EPSGroupBypass.INPUT_TYPES()["required"]
    assert spec["enabled"][1]["default"] is True
    assert spec["mute"][1]["default"] is False
    assert spec["group"][0] == [NO_GROUP]
    assert spec["group"][1]["default"] == NO_GROUP


def test_node_never_runs() -> None:
    # No outputs and not an output node: core's validate_prompt (which walks
    # only from output nodes) never reaches it, so the dynamic group names
    # are never checked against the one-entry backend list.
    assert EPSGroupBypass.RETURN_TYPES == ()
    assert not getattr(EPSGroupBypass, "OUTPUT_NODE", False)
    assert EPSGroupBypass().noop(enabled=False) == ()


# ----------------------------------------------------------------- frontend

HARNESS = r"""
import * as gb from './extensions/comfyui-epsnodes/eps_image/group_bypass.js'

const out = {}
let nextId = 1
function widget(name, value) { return { name, value, options: {}, callback: null } }
function makeNode(type, widgets = [], extra = {}) {
  const node = { id: nextId++, type, comfyClass: type, mode: 0, widgets, inputs: [], outputs: [], ...extra }
  return node
}
function makeGroup(id, title, members) {
  return { id, title, _members: members, nodes: [], recomputeInsideNodes() { this.nodes = [...this._members] } }
}
function makeGraph(nodes, groups) {
  const graph = { _nodes: nodes, links: new Map(), groups, setDirtyCanvas() {}, getNodeById(id) { return nodes.find((n) => n.id == id) || null } }
  for (const n of nodes) n.graph = graph
  return graph
}
function link(graph, origin, originSlot, target, targetSlot) {
  const id = graph.links.size + 100
  graph.links.set(id, { id, origin_id: origin.id, origin_slot: originSlot, target_id: target.id, target_slot: targetSlot })
  target.inputs[targetSlot].link = id
  ;(origin.outputs[originSlot].links ??= []).push(id)
}
function makeSwitch(enabled = true, mute = false, group = '(none)', groupId = '') {
  const node = makeNode('EPSGroupBypass', [
    widget('enabled', enabled), widget('mute', mute), widget('group', group), widget('group_id', groupId)
  ])
  node.inputs = [
    { name: 'enabled', type: 'BOOLEAN', widget: { name: 'enabled' }, link: null },
    { name: 'mute', type: 'BOOLEAN', widget: { name: 'mute' }, link: null },
  ]
  return node
}
const w = (node, name) => node.widgets.find((x) => x.name === name)

// 1. Manual switch: off bypasses, mute mutes, on restores; self is never touched.
{
  const a = makeNode('KSampler'), b = makeNode('VAEDecode')
  const sw = makeSwitch(true, false, 'Upscale', '7')
  const g = makeGroup(7, 'Upscale', [a, b, sw])
  makeGraph([a, b, sw], [g])
  gb.attach(sw)
  gb.refreshAll()
  out.adoptWritesNothing = [a.mode, b.mode]
  w(sw, 'enabled').value = false; gb.refreshAll()
  out.offBypasses = [a.mode, b.mode, sw.mode]
  w(sw, 'mute').value = true; gb.refreshAll()
  out.muteMutes = [a.mode, b.mode, sw.mode]
  a.mode = 0; gb.refreshAll()
  out.handSetKept = a.mode
  w(sw, 'enabled').value = true; gb.refreshAll()
  out.onRestores = [a.mode, b.mode]
  out.options = w(sw, 'group').options.values()
}

// 2. Driven by a Number Controller row; the driver inside the group is kept on.
{
  const nc = makeNode('EPSNumberController', [widget('values', JSON.stringify({ num_1: { value: 1, type: 'BOOLEAN' } }))])
  nc.outputs = [{ name: 'num_1', type: '*', links: [] }]
  const a = makeNode('KSampler')
  const sw = makeSwitch(true, false, 'Main', '3')
  const g = makeGroup(3, 'Main', [a, nc])
  const graph = makeGraph([nc, a, sw], [g])
  link(graph, nc, 0, sw, 0)
  gb.attach(sw)
  gb.refreshAll()
  out.drivenLabelOn = w(sw, 'enabled').label
  w(nc, 'values').value = JSON.stringify({ num_1: { value: 0, type: 'BOOLEAN' } }); gb.refreshAll()
  out.drivenOff = [a.mode, nc.mode, w(sw, 'enabled').label, w(sw, 'enabled').value]
  w(nc, 'values').value = JSON.stringify({ num_1: { value: 'abc' } }); gb.refreshAll()
  out.unreadable = [a.mode, w(sw, 'enabled').label]
}

// 3. Retargeting while off puts the old group back on.
{
  const a = makeNode('A'), b = makeNode('B')
  const sw = makeSwitch(false, false, 'One', '1')
  const g1 = makeGroup(1, 'One', [a]), g2 = makeGroup(2, 'Two', [b])
  makeGraph([a, b, sw], [g1, g2])
  gb.attach(sw)
  gb.refreshAll()                       // adopt: nothing written
  out.adoptOff = [a.mode, b.mode]
  w(sw, 'mute').value = true; gb.refreshAll()
  out.firstWrite = [a.mode, b.mode]
  w(sw, 'group').value = 'Two'; w(sw, 'group').callback('Two')
  out.retarget = [a.mode, b.mode, w(sw, 'group_id').value]
}

// 4. A renamed group keeps the target; a label-only save adopts the id.
{
  const a = makeNode('A')
  const sw = makeSwitch(true, false, 'Old name', '5')
  const g = makeGroup(5, 'New name', [a])
  makeGraph([a, sw], [g])
  gb.attach(sw); gb.refreshAll()
  out.renamed = w(sw, 'group').value
  const sw2 = makeSwitch(true, false, 'New name', '')
  sw2.graph = g && sw.graph; sw.graph._nodes.push(sw2)
  gb.attach(sw2); gb.refreshAll()
  out.labelAdopted = w(sw2, 'group_id').value
}

out.pure = {
  wanted: [gb.wantedMode(true, null), gb.wantedMode(false, false), gb.wantedMode(false, true), gb.wantedMode(null, false), gb.wantedMode(false, null)],
  coerce: [true, 0, 1, -2, 'on', 'no', '0', '', 'x', NaN, null].map(gb.coerceBoolean),
  options: gb.groupOptions([{ id: 1, title: 'A' }, { id: 2, title: 'A' }, { id: 3, title: 'B' }, { id: 4, title: '' }]),
  findByLabel: gb.findTargetGroup([{ id: 1, title: 'A' }, { id: 2, title: 'B' }], '', 'B')?.id,
  findMissingId: gb.findTargetGroup([{ id: 1, title: 'A' }], '9', 'Gone'),
}
console.log(JSON.stringify(out))
process.exit(0)
"""


@pytest.fixture(scope="module")
def result(tmp_path_factory: pytest.TempPathFactory) -> dict:
    if NODE is None:
        pytest.skip("node (JS runtime) not installed")
    layout = tmp_path_factory.mktemp("group_bypass")
    build_served_layout(layout, eps_modules=GROUP_BYPASS_MODULES)
    script = layout / "harness.mjs"
    script.write_text(HARNESS, encoding="utf-8")
    proc = subprocess.run(
        [NODE, str(script)], capture_output=True, text=True, timeout=60, cwd=layout
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_attach_adopts_without_writing(result: dict) -> None:
    assert result["adoptWritesNothing"] == [0, 0]
    assert result["adoptOff"] == [0, 0]


def test_off_bypasses_mute_mutes_and_self_is_kept(result: dict) -> None:
    assert result["offBypasses"] == [4, 4, 0]
    assert result["muteMutes"] == [2, 2, 0]


def test_never_reasserts_a_hand_set_node(result: dict) -> None:
    assert result["handSetKept"] == 0


def test_on_restores_every_member(result: dict) -> None:
    assert result["onRestores"] == [0, 0]


def test_combo_lists_none_then_groups(result: dict) -> None:
    assert result["options"] == [NO_GROUP, "Upscale"]


def test_number_controller_row_drives_the_switch(result: dict) -> None:
    assert result["drivenLabelOn"] == "enabled ← on"
    a_mode, nc_mode, label, value = result["drivenOff"]
    assert (a_mode, nc_mode) == (4, 0)  # the driver inside the group stays on
    assert label == "enabled ← off"
    assert value is False


def test_unreadable_driver_leaves_the_group_alone(result: dict) -> None:
    a_mode, label = result["unreadable"]
    assert a_mode == 4
    assert label.startswith("enabled ← ?")


def test_retarget_while_off_restores_the_old_group(result: dict) -> None:
    assert result["firstWrite"] == [2, 0]
    assert result["retarget"] == [0, 2, "2"]


def test_rename_and_label_only_saves(result: dict) -> None:
    assert result["renamed"] == "New name"
    assert result["labelAdopted"] == "5"


def test_pure_helpers(result: dict) -> None:
    pure = result["pure"]
    assert pure["wanted"] == [0, 4, 2, None, None]
    assert pure["coerce"] == [True, False, True, True, True, False, False, None, None, None, None]
    assert [o["label"] for o in pure["options"]] == ["A [#1]", "A [#2]", "B", " [#4]"]
    assert pure["findByLabel"] == 2
    assert pure["findMissingId"] is None
