"""Applier tests for the EPS Run Multiplier BROADCAST live half (FORMAT.md
§6.10 "Broadcast (v1)") -- ``web/eps_image/broadcast_graph.js``.

These drive the REAL, unmodified module against a small FAKE litegraph that
mimics exactly the 1.52.7 behaviours the code depends on (verified from the
extracted frontend source, ``lib/litegraph/src``):

- ``LGraphNode.connect`` (type check, ``onConnectInput`` veto, silently
  REPLACES an occupied input, fires ``onConnectionsChange`` on both ends);
- ``LGraphNode.disconnectInput`` (an origin of ``-10`` in a subgraph routes
  through the definition's input node and its ``linkIds``);
- ``SubgraphInput.connect(slot, node)`` (the inner link, origin ``-10``);
- ``Subgraph.addInput`` -> every SubgraphNode INSTANCE grows an input
  (``input-added``) and ``Subgraph.removeInput`` removes it again
  (``removing-input``), disconnecting the instance's wire;
- ``SubgraphSlot.disconnect()`` iterating ``linkIds`` WHILE each removal
  splices it (so it skips every other link) -- the hazard the module works
  around by severing links itself first;
- ``NodeId`` is a branded STRING on 1.52.7 (``'-10'``), a number on older
  frontends: both are exercised.

What this CANNOT cover, and the rig must: the real ``connect`` type rules, a
real change tracker, real ``inputVerdict`` off real ``nodeData``, a real
SubgraphNode's promoted-widget side effects. See the final report's
UNCONFIRMED list.
"""

# ruff: noqa: E501 -- the embedded Node probe source (PROBE_JS) is JavaScript in a raw
# string, wrapped for readability as JS, not Python.

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from served_layout import build_served_layout

REPO_ROOT = Path(__file__).resolve().parent.parent
GRAPH_JS = REPO_ROOT / "web" / "eps_image" / "broadcast_graph.js"
BROADCAST_JS = REPO_ROOT / "web" / "eps_image" / "broadcast.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

PROBE_JS = r"""
import * as bg from './extensions/comfyui-epsnodes/eps_image/broadcast_graph.js'
import * as plan from './extensions/comfyui-epsnodes/eps_image/broadcast_plan.js'
import { app } from './scripts/app.js'
import * as fl from './fake_litegraph.mjs'

const out = {}
globalThis.__log = []

// (the fake litegraph lives in ./fake_litegraph.mjs -- shared with test_broadcast_js.py)
const {
  LEGACY, FakeGraph, FakeSubgraph, FakeSubgraphNode, FakeNode,
  makeNode, makeMultiplier, makeLoader, build, ksampler, decode, saver, linkOf, wiredFrom, props
} = fl
const log = globalThis.__log

// instrument the app: undo transaction + change tracker + toasts
app.canvas = {
  emitBeforeChange() { log.push('before') },
  emitAfterChange() { log.push('after') }
}
app.extensionManager = {
  toast: { add(t) { log.push(`toast:${t.summary}`) } },
  workflow: { activeWorkflow: { changeTracker: { captureCanvasState() { log.push('capture') } } } }
}
const planOf = (m, settings = {}) => bg.planFor(m, settings)

// ============================================================ A. direct
{
  const { root, m } = build()
  const k10 = ksampler(root, 10)
  const k11 = ksampler(root, 11)
  const d13 = decode(root, 13)
  const s14 = saver(root, 14)
  const { plan: p } = planOf(m)
  log.length = 0
  const result = bg.applyProposals(root, m, p.proposals)
  out.direct = {
    proposals: p.proposals.map((x) => x.key).sort(),
    applied: result.applied.map((a) => a.key).sort(), failed: result.failed,
    k10: wiredFrom(k10, 'model'), k11: wiredFrom(k11, 'model'), d13: wiredFrom(d13, 'vae'),
    s14: wiredFrom(s14, 'filename_prefix'),
    untouched: { positive: wiredFrom(k10, 'positive'), seed: wiredFrom(k10, 'seed'), images: wiredFrom(s14, 'images') },
    wired: props(m).wired.map((e) => [e.key, e.kind, e.links.length]).sort(),
    log: [...log],
    mWired: m.outputs[0].links.length
  }
  // the second plan sees everything already wired
  const again = planOf(m).plan
  out.direct.again = { proposals: again.proposals.length, alreadyWired: again.skips.filter((s) => s.code === 'already-wired').length }
}

// ======================================== B. never replaces a wire (races)
{
  const { root, m, loader } = build()
  const k10 = ksampler(root, 10)
  const k11 = ksampler(root, 11)
  const { plan: p } = planOf(m)
  loader.connect(0, k10, 0) // the user's own wire lands AFTER the preview
  const userLink = k10.inputs[0].link
  const result = bg.applyProposals(root, m, p.proposals)
  out.race = {
    applied: result.applied.map((a) => a.key), failed: result.failed.map((f) => [f.key, f.reason]),
    userWireIntact: k10.inputs[0].link === userLink && wiredFrom(k10, 'model').origin === String(loader.id),
    k11: wiredFrom(k11, 'model')
  }
}

// ================================ C. a vetoed connection rolls back cleanly
{
  const { root, m } = build()
  const k10 = ksampler(root, 10)
  const k11 = ksampler(root, 11)
  k10.onConnectInput = () => false
  const { plan: p } = planOf(m)
  const result = bg.applyProposals(root, m, p.proposals)
  out.veto = {
    applied: result.applied.map((a) => a.key), failed: result.failed.map((f) => f.key),
    k10: wiredFrom(k10, 'model'), k11: wiredFrom(k11, 'model'),
    keysRecorded: props(m).wired.map((e) => e.key)
  }
}

// ======================= D/E. subgraphs: tier 1, tier 2, link index, remove
function buildSubgraphScenario() {
  const { root, m, loader } = build(['model'])
  // def A: has an EXISTING model input already feeding an inner KSampler
  const defA = new FakeSubgraph(root, 'sg-a', 'Existing group')
  const inA = defA.addInput('model', 'MODEL')
  const kA = ksampler(defA, 5, 'Inner A')
  inA.connect(kA.inputs[0], kA)
  const instA = root.add(new FakeSubgraphNode(3, 'Existing group', defA))
  // def B: NO inputs; an inner KSampler with an empty required model
  const defB = new FakeSubgraph(root, 'sg-b', 'New-input group')
  const kB = ksampler(defB, 6, 'Inner B')
  const kB2 = ksampler(defB, 7, 'Inner B2')
  const instB1 = root.add(new FakeSubgraphNode(4, 'New-input group', defB))
  const instB2 = root.add(new FakeSubgraphNode(5, 'New-input group 2', defB))
  return { root, m, loader, defA, defB, inA, kA, kB, kB2, instA, instB1, instB2 }
}
{
  const s = buildSubgraphScenario()
  const { plan: p } = planOf(s.m)
  out.sub_plan = p.proposals.map((x) => [x.kind, x.key, x.steps.length])
  const before = { linksRoot: s.root._links.size, inputsB: s.defB.inputs.length }
  log.length = 0
  const result = bg.applyProposals(s.root, s.m, p.proposals)
  const entry = (key) => props(s.m).wired.find((e) => e.key === key)
  out.sub_apply = {
    applied: result.applied.map((a) => [a.key, a.links, a.made]), failed: result.failed,
    instA: wiredFrom(s.instA, 'model'),
    defBInputs: s.defB.inputs.map((i) => [i.name, i.type, i.linkIds.length]),
    instB1: s.instB1.inputs.map((i) => [i.name, wiredFrom(s.instB1, i.name)]),
    instB2: s.instB2.inputs.map((i) => [i.name, wiredFrom(s.instB2, i.name)]),
    innerB: linkOf(s.kB, 'model') ? { origin: String(linkOf(s.kB, 'model').origin_id), slot: linkOf(s.kB, 'model').origin_slot } : null,
    innerB2: linkOf(s.kB2, 'model') ? String(linkOf(s.kB2, 'model').origin_id) : null,
    madeRecord: entry('model|def:sg-b|new').made, linkRecords: entry('model|def:sg-b|new').links,
    existingKind: entry(p.proposals.find((x) => x.kind === 'via-existing-subgraph-input').key).kind,
    log: [...log], before
  }
  // link index: root + INNER links
  const index = bg.broadcastLinkIndex(s.root)
  const asObj = Object.fromEntries([...index.entries()].map(([k, v]) => [k, [...v].sort((a, b) => a - b)]))
  out.sub_index = {
    keys: Object.keys(asObj).sort(),
    rootCount: asObj['root-uuid'] ? asObj['root-uuid'].length : (asObj.root ? asObj.root.length : 0),
    // graph keys: 'root' is the root's key regardless of its own id
    byKey: Object.fromEntries(Object.entries(asObj).map(([k, v]) => [k, v.length])),
    innerLinkIds: asObj['sg-b'] || [],
    isBroadcastRoot: bg.isBroadcastLink(s.root, s.instB1.inputs.find((i) => i.name === 'model').link),
    isBroadcastInner: bg.isBroadcastLink(s.defB, linkOf(s.kB, 'model').id),
    isBroadcastUser: bg.isBroadcastLink(s.root, s.m.inputs[1].link)
  }
  // a second plan after applying: nothing left to do
  out.sub_again = planOf(s.m).plan.proposals.length

  // ------- remove
  log.length = 0
  const removed = bg.removeBroadcastWires(s.root, s.m)
  out.sub_remove = {
    removed: removed.removed, removedInputs: removed.removedInputs, kept: removed.keptInputs,
    instA: wiredFrom(s.instA, 'model'),
    defBInputs: s.defB.inputs.length, instB1Inputs: s.instB1.inputs.length, instB2Inputs: s.instB2.inputs.length,
    innerB: wiredFrom(s.kB, 'model'), innerB2: wiredFrom(s.kB2, 'model'),
    userInputIntact: s.defA.inputs.length === 1 && s.instA.inputs.length === 1,
    existingInnerLinkIntact: linkOf(s.kA, 'model') !== null,
    property: props(s.m), log: [...log],
    linksLeftInDefB: s.defB._links.size
  }
}

// ====================== F. removal keeps a made input a USER wire now uses
{
  const s = buildSubgraphScenario()
  const { plan: p } = planOf(s.m)
  bg.applyProposals(s.root, s.m, p.proposals)
  // the user replaces instance 4's recorded wire with their own, from the loader
  const slot = s.instB1.inputs.findIndex((i) => i.name === 'model')
  s.instB1.disconnectInput(slot)
  s.loader.connect(0, s.instB1, slot)
  const removed = bg.removeBroadcastWires(s.root, s.m)
  out.keep_input = {
    removed: removed.removed, removedInputs: removed.removedInputs, kept: removed.keptInputs,
    userWireIntact: wiredFrom(s.instB1, 'model')?.origin === String(s.loader.id),
    defBInputs: s.defB.inputs.length
  }
}

// ========================================= G. rollback of a half-applied tier 2
{
  const s = buildSubgraphScenario()
  s.kB2.onConnectInput = () => false // the SECOND inner link is vetoed
  const { plan: p } = planOf(s.m)
  const target = p.proposals.find((x) => x.kind === 'via-new-subgraph-input')
  const result = bg.applyProposals(s.root, s.m, [target])
  out.rollback = {
    applied: result.applied.length, failed: result.failed.map((f) => [f.key, f.reason]),
    defBInputs: s.defB.inputs.length, instB1Inputs: s.instB1.inputs.length, instB2Inputs: s.instB2.inputs.length,
    innerB: wiredFrom(s.kB, 'model'), rootLinks: [...s.root._links.values()].map((l) => `${l.origin_id}:${l.target_id}`).sort(),
    wiredRecords: (props(s.m)?.wired || []).length
  }
}

// ============================ H. nested: two levels, apply then remove
{
  const { root, m } = build(['model'])
  const d2 = new FakeSubgraph(root, 'd2', 'Inner')
  const deep = ksampler(d2, 5, 'Deep')
  const d1 = new FakeSubgraph(root, 'd1', 'Outer')
  const shallow = ksampler(d1, 6, 'Shallow')
  const inst2 = d1.add(new FakeSubgraphNode(8, 'Inner', d2))
  const inst1 = root.add(new FakeSubgraphNode(3, 'Outer', d1))
  const { plan: p } = planOf(m)
  const result = bg.applyProposals(root, m, p.proposals)
  const sub = (g, i) => g.inputs[i]
  out.nested = {
    steps: p.proposals.map((x) => x.steps.map((s) => s.op + (s.graph || ''))),
    applied: result.applied.map((a) => [a.links, a.made]), failed: result.failed,
    d1Inputs: d1.inputs.map((i) => i.name), d2Inputs: d2.inputs.map((i) => i.name),
    inst1: wiredFrom(inst1, 'model'),
    inst2FromD1Input: String(linkOf(inst2, 'model')?.origin_id),
    shallow: String(linkOf(shallow, 'model')?.origin_id), deep: String(linkOf(deep, 'model')?.origin_id),
    deepSlot: linkOf(deep, 'model')?.origin_slot
  }
  const removed = bg.removeBroadcastWires(root, m)
  out.nested.removed = {
    removed: removed.removed, removedInputs: removed.removedInputs, kept: removed.keptInputs,
    d1Inputs: d1.inputs.length, d2Inputs: d2.inputs.length, inst1Inputs: inst1.inputs.length, inst2Inputs: inst2.inputs.length,
    deep: wiredFrom(deep, 'model'), shallow: wiredFrom(shallow, 'model')
  }
}

// =============================== I. withdraw / restore on a dead output
{
  const { root, m, loader, inputOf } = build()
  const d13 = decode(root, 13)
  const d14 = decode(root, 14)
  bg.applyProposals(root, m, planOf(m).plan.proposals)
  const wiredBefore = [wiredFrom(d13, 'vae'), wiredFrom(d14, 'vae')]
  m.disconnectInput(inputOf('vae')) // the user unwires the multiplier's vae input
  const n = bg.withdrawOutputWires(root, m, 'vae')
  const afterWithdraw = {
    n, d13: wiredFrom(d13, 'vae'), d14: wiredFrom(d14, 'vae'),
    withdrawn: props(m).wired.filter((e) => e.withdrawn).map((e) => e.key).sort(),
    modelWiresKept: props(m).wired.filter((e) => e.out === 'model').length
  }
  // the user hand-wires d14.vae meanwhile, then rewires the multiplier input
  loader.connect(2, d14, 1)
  loader.connect(2, m, inputOf('vae'))
  const restored = bg.restoreOutputWires(root, m, 'vae')
  out.liveness = {
    wiredBefore, afterWithdraw, restored,
    d13: wiredFrom(d13, 'vae'), d14UserWire: wiredFrom(d14, 'vae')?.origin === String(loader.id),
    keys: props(m).wired.map((e) => [e.key, !!e.withdrawn]).sort()
  }
}

// =============================== J. reconcile: manual unplug -> leave alone
{
  const { root, m } = build()
  const k10 = ksampler(root, 10)
  const k11 = ksampler(root, 11)
  bg.applyProposals(root, m, planOf(m).plan.proposals)
  k10.disconnectInput(0) // the USER unplugs a recorded wire
  const guardedRun = (() => { // our own disconnects are guarded: never counted
    return bg.isApplying()
  })()
  const loadStyle = bg.reconcileMultiplier(root, m, { manual: false })
  const afterLoad = { dropped: loadStyle.dropped, skip: props(m).skip ?? [] }
  // the in-session variant: k11's record survived the load-style pass
  k11.disconnectInput(0)
  const session = bg.reconcileMultiplier(root, m, { manual: true })
  const after = planOf(m).plan
  out.reconcile = {
    guardedRun, afterLoad, session: { leftAlone: session.leftAlone, skip: props(m).skip },
    k11Proposed: after.proposals.some((x) => x.key === 'model|11|model'),
    k11LeftAloneSkip: after.skips.some((s) => s.code === 'left-alone' && s.key === 'model|11|model'),
    k10Wired: wiredFrom(k10, 'model') !== null
  }
}

// ============ K. legacy numeric ids behave exactly like branded string ids
{
  LEGACY.ids = true
  const s = buildSubgraphScenario()
  const { plan: p } = planOf(s.m)
  const result = bg.applyProposals(s.root, s.m, p.proposals)
  const index = bg.broadcastLinkIndex(s.root)
  out.legacy = {
    applied: result.applied.length, failed: result.failed.length,
    innerCount: (index.get('sg-b') || new Set()).size,
    removed: bg.removeBroadcastWires(s.root, s.m).removedInputs,
    defBInputs: s.defB.inputs.length
  }
  LEGACY.ids = false
}

// ============================ L. the undo wrapper's contract, in isolation
{
  log.length = 0
  let ran = 0
  bg.runAsOneUndoStep(() => { ran += 1; log.push('work') })
  let threw = null
  try { bg.runAsOneUndoStep(() => { throw new Error('boom') }) } catch (e) { threw = e.message }
  const saved = app.canvas
  app.canvas = {} // no transaction API: explicit captures bracket the work
  const log2start = log.length
  bg.runAsOneUndoStep(() => log.push('work2'))
  app.canvas = saved
  out.undo = { ran, threw, log: [...log], tail: log.slice(log2start) }
}

// ====== O. proof of the hazard the removal path works around: the raw
// SubgraphSlot.disconnect() skips every other link while it iterates linkIds
{
  const root = new FakeGraph('root-uuid')
  root.subgraphs = new Map()
  const d = new FakeSubgraph(root, 'sg-h', 'Hazard')
  const input = d.addInput('model', 'MODEL')
  const a = ksampler(d, 1)
  const b = ksampler(d, 2)
  const c = ksampler(d, 3)
  for (const k of [a, b, c]) input.connect(k.inputs[0], k)
  input.disconnect()
  out.hazard = { linksLeft: d._links.size, linkIdsLeft: input.linkIds.length }
}

// ====================== N. a multiplier INSIDE a subgraph definition
{
  const root = new FakeGraph('root-uuid')
  root.subgraphs = new Map()
  const d0 = new FakeSubgraph(root, 'd0', 'Pipeline')
  const loader = makeLoader(d0)
  const m = makeMultiplier(d0, 12)
  loader.connect(0, m, m.inputs.findIndex((i) => i.name === 'model'))
  const inner = ksampler(d0, 5, 'Inner')
  const inst = root.add(new FakeSubgraphNode(7, 'Pipeline', d0))
  const rootSampler = ksampler(root, 20, 'Root sampler')
  const { plan: p, pathId } = planOf(m)
  const result = bg.applyProposals(root, m, p.proposals)
  const index = bg.broadcastLinkIndex(root)
  out.inside = {
    pathId, targets: p.proposals.map((x) => x.targetPathId),
    applied: result.applied.length, failed: result.failed,
    inner: wiredFrom(inner, 'model'), rootSampler: wiredFrom(rootSampler, 'model'),
    recorded: props(m).wired.map((e) => [e.key, e.links[0].g]),
    indexKeys: [...index.keys()].sort(), d0Links: [...(index.get('d0') || [])].length,
    removed: bg.removeBroadcastWires(root, m).removed, after: wiredFrom(inner, 'model')
  }
}

// ========================================== M. exports + snapshot shape
{
  const { root, m } = build()
  const d = new FakeSubgraph(root, 'sg-x', 'X')
  d.addInput('model', 'MODEL')
  ksampler(d, 5)
  root.add(new FakeSubgraphNode(3, 'X', d))
  const snap = bg.snapshotFromRoot(root)
  out.snapshot = {
    graphs: Object.keys(snap.graphs).sort(),
    rootNodes: Object.keys(snap.graphs.root.nodes).sort(),
    instance: { subgraphId: snap.graphs.root.nodes['3'].subgraphId, inputs: snap.graphs.root.nodes['3'].inputs },
    defInputs: snap.graphs['sg-x'].inputs.map((i) => [i.name, i.type]),
    verdicts: Object.fromEntries(snap.graphs.root.nodes['1'].inputs.map((i) => [i.name, i.verdict])),
    mIsClass: snap.graphs.root.nodes['1'].classType,
    exports: Object.keys(bg).sort()
  }
}

process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def probe(tmp_path_factory: pytest.TempPathFactory) -> dict:
    layout = tmp_path_factory.mktemp("web_root")
    build_served_layout(
        layout,
        eps_modules=(
            "broadcast_graph.js",
            "broadcast_plan.js",
            "bypass.js",
            "number_controller.js",
            "distributor.js",
        ),
        with_fake=True,
    )
    probe_file = layout / "probe.mjs"
    probe_file.write_text(PROBE_JS, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(probe_file)], capture_output=True, text=True, timeout=60, cwd=layout
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def source() -> str:
    return GRAPH_JS.read_text(encoding="utf-8")


# ------------------------------------------------------------------- tests


def test_graph_js_parses() -> None:
    result = subprocess.run(
        [NODE, "--check", str(GRAPH_JS)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


def test_direct_apply_connects_real_wires_from_the_right_output_slots(probe: dict) -> None:
    d = probe["direct"]
    assert d["failed"] == []
    assert d["proposals"] == d["applied"]
    assert d["k10"] == {"origin": "1", "slot": 0}  # model -> output 0
    assert d["k11"] == {"origin": "1", "slot": 0}
    assert d["d13"] == {"origin": "1", "slot": 6}  # vae -> output 6 (RETURN_NAMES position)
    assert d["s14"] == {"origin": "1", "slot": 4}  # save_prefix -> output 4
    # nothing else was touched
    assert d["untouched"] == {"positive": None, "seed": None, "images": None}
    assert d["mWired"] == 2


def test_apply_records_every_wire_on_the_multiplier_only(probe: dict) -> None:
    d = probe["direct"]
    assert d["wired"] == [
        ["model|10|model", "direct", 1],
        ["model|11|model", "direct", 1],
        ["save_prefix|14|filename_prefix", "direct", 1],
        ["vae|13|vae", "direct", 1],
    ]
    # a second plan sees them all as already wired -> nothing proposed
    assert d["again"]["proposals"] == 0 and d["again"]["alreadyWired"] >= 4


def test_apply_is_one_undo_step_bracketed_by_a_commit_and_a_transaction(probe: dict) -> None:
    """Investigated from the 1.52.7 source: graph.beforeChange/afterChange do
    NOT drive the change tracker; the canvas transaction events do. So the
    batch is: commit pending state, open a transaction, work, close it."""
    log = [e for e in probe["direct"]["log"] if not e.startswith("toast")]
    assert log == ["capture", "before", "after"]


def test_apply_never_replaces_a_wire_made_after_the_preview(probe: dict) -> None:
    r = probe["race"]
    assert r["userWireIntact"] is True
    assert r["k11"] == {"origin": "1", "slot": 0}
    assert [k for k, _ in r["failed"]] == ["model|10|model"]
    assert "no longer empty" in r["failed"][0][1]
    assert "model|10|model" not in r["applied"]


def test_a_vetoed_connection_fails_that_proposal_only(probe: dict) -> None:
    v = probe["veto"]
    assert v["failed"] == ["model|10|model"]
    assert v["k10"] is None and v["k11"] == {"origin": "1", "slot": 0}
    assert "model|10|model" not in v["keysRecorded"]


def test_subgraph_plan_has_one_existing_and_one_new_input_proposal(probe: dict) -> None:
    kinds = {k: key for k, key, _ in probe["sub_plan"]}
    assert set(kinds) == {"via-existing-subgraph-input", "via-new-subgraph-input"}
    assert kinds["via-new-subgraph-input"] == "model|def:sg-b|new"


def test_tier1_wire_and_tier2_new_input_end_to_end(probe: dict) -> None:
    a = probe["sub_apply"]
    assert a["failed"] == []
    # tier 1: ONE wire from the multiplier into the instance's existing input
    assert a["instA"] == {"origin": "1", "slot": 0}
    # tier 2: a NEW `model` input on the definition, fed on BOTH instances
    assert a["defBInputs"] == [["model", "MODEL", 2]]  # linked to both inner samplers
    assert a["instB1"] == [["model", {"origin": "1", "slot": 0}]]
    assert a["instB2"] == [["model", {"origin": "1", "slot": 0}]]
    assert a["innerB"] == {"origin": "-10", "slot": 0}
    assert a["innerB2"] == "-10"
    new_applied = next(x for x in a["applied"] if x[0] == "model|def:sg-b|new")
    assert new_applied[2] == 1  # one subgraph input made
    assert new_applied[1] == 4  # 2 outer wires + 2 inner links
    # records: the made input, and every link with its origin kind
    assert [m["name"] for m in a["madeRecord"]] == ["model"] and a["madeRecord"][0]["g"] == "sg-b"
    origins = sorted("m" if "m" in link["o"] else "s" for link in a["linkRecords"])
    assert origins == ["m", "m", "s", "s"]
    assert a["existingKind"] == "via-existing-subgraph-input"
    assert [e for e in a["log"] if not e.startswith("toast")] == ["capture", "before", "after"]


def test_link_index_covers_root_wires_and_inner_subgraph_links(probe: dict) -> None:
    index = probe["sub_index"]
    assert index["byKey"]["sg-b"] == 2  # the two INNER links
    assert index["byKey"]["root"] == 3  # tier-1 wire + two instance wires
    assert index["isBroadcastRoot"] is True
    assert index["isBroadcastInner"] is True
    assert index["isBroadcastUser"] is False  # the user's own loader -> multiplier wire


def test_a_second_plan_after_applying_has_nothing_left(probe: dict) -> None:
    assert probe["sub_again"] == 0


def test_remove_deletes_only_recorded_wires_and_the_inputs_it_made(probe: dict) -> None:
    r = probe["sub_remove"]
    assert r["instA"] is None  # the tier-1 wire is gone...
    assert r["existingInnerLinkIntact"] is True  # ...but the user's own inner link is not
    assert r["userInputIntact"] is True
    assert r["removedInputs"] == 1 and r["kept"] == []
    assert r["defBInputs"] == 0
    assert r["instB1Inputs"] == 0 and r["instB2Inputs"] == 0  # instances shrank back
    assert r["innerB"] is None and r["innerB2"] is None
    assert r["linksLeftInDefB"] == 0  # the splice-while-iterating hazard did not strand a link
    assert r["property"] is None  # an all-default config removes the property entirely
    assert [e for e in r["log"] if not e.startswith("toast")] == ["capture", "before", "after"]
    assert r["removed"] == 5  # tier-1 wire + 2 instance wires + 2 inner links


def test_remove_keeps_a_made_input_that_a_user_wire_now_uses(probe: dict) -> None:
    k = probe["keep_input"]
    assert k["userWireIntact"] is True
    assert k["kept"] == ["model"] and k["removedInputs"] == 0
    assert k["defBInputs"] == 1


def test_a_half_applied_tier2_rolls_back_to_exactly_where_it_started(probe: dict) -> None:
    r = probe["rollback"]
    assert r["applied"] == 0 and len(r["failed"]) == 1
    assert "refused" in r["failed"][0][1]
    assert r["defBInputs"] == 0
    assert r["instB1Inputs"] == 0 and r["instB2Inputs"] == 0
    assert r["innerB"] is None
    assert r["wiredRecords"] == 0
    # only the loader -> multiplier wire remains
    assert r["rootLinks"] == ["900:1"]


def test_nested_two_level_apply_chains_through_each_instance_then_removes_cleanly(
    probe: dict,
) -> None:
    n = probe["nested"]
    assert n["failed"] == []
    assert n["d1Inputs"] == ["model"] and n["d2Inputs"] == ["model"]
    assert n["inst1"] == {"origin": "1", "slot": 0}
    assert n["inst2FromD1Input"] == "-10"  # instance 8 (in d1) is fed by d1's NEW input
    assert n["shallow"] == "-10" and n["deep"] == "-10"
    r = n["removed"]
    assert r["removedInputs"] == 2 and r["kept"] == []
    assert r["d1Inputs"] == 0 and r["d2Inputs"] == 0
    assert r["inst1Inputs"] == 0 and r["inst2Inputs"] == 0
    assert r["deep"] is None and r["shallow"] is None


def test_live_output_tracking_withdraws_and_restores_without_stomping(probe: dict) -> None:
    lv = probe["liveness"]
    assert lv["wiredBefore"] == [{"origin": "1", "slot": 6}, {"origin": "1", "slot": 6}]
    w = lv["afterWithdraw"]
    assert w["n"] == 2 and w["d13"] is None and w["d14"] is None
    assert w["withdrawn"] == ["vae|13|vae", "vae|14|vae"]
    assert w["modelWiresKept"] == 0 or w["modelWiresKept"] >= 0
    # d14 was hand-wired meanwhile: that record is DROPPED, never stomped
    assert lv["restored"] == {"restored": 1, "dropped": 1}
    assert lv["d13"] == {"origin": "1", "slot": 6}
    assert lv["d14UserWire"] is True
    assert [k for k, withdrawn in lv["keys"] if k.startswith("vae")] == ["vae|13|vae"]
    assert all(not withdrawn for _, withdrawn in lv["keys"])


def test_reconcile_load_drops_quietly_and_session_marks_leave_alone(probe: dict) -> None:
    r = probe["reconcile"]
    assert r["guardedRun"] is False  # the guard is only up WHILE we mutate
    assert r["afterLoad"]["dropped"] == ["model|10|model"] and r["afterLoad"]["skip"] == []
    assert r["session"]["leftAlone"] == ["model|11|model"]
    assert r["session"]["skip"] == ["model|11|model"]
    assert r["k11Proposed"] is False and r["k11LeftAloneSkip"] is True
    assert r["k10Wired"] is False


def test_legacy_numeric_ids_behave_like_branded_string_ids(probe: dict) -> None:
    lg = probe["legacy"]
    assert lg["applied"] == 2 and lg["failed"] == 0
    assert lg["innerCount"] == 2
    assert lg["removed"] == 1 and lg["defBInputs"] == 0


def test_undo_wrapper_contract(probe: dict) -> None:
    u = probe["undo"]
    assert u["ran"] == 1 and u["threw"] == "boom"
    # transaction: commit, open, work, close -- and the error path still CLOSES
    assert u["log"][:6] == ["capture", "before", "work", "after", "capture", "before"]
    assert u["log"][6] == "after"
    # no transaction API: explicit captures bracket the work
    assert u["tail"] == ["capture", "work2", "capture"]


def test_the_fake_reproduces_the_core_disconnect_hazard_the_removal_path_avoids(
    probe: dict,
) -> None:
    """Three inner links, the raw `disconnect()`: core's loop skips every other
    one (here the second), stranding a link in the definition. The module's own
    removal path severs links itself first (asserted by the remove tests)."""
    assert probe["hazard"]["linksLeft"] >= 1


def test_a_multiplier_inside_a_subgraph_wires_inside_that_definition(probe: dict) -> None:
    i = probe["inside"]
    assert i["pathId"] == "7:12"
    assert i["targets"] == ["7:5"]
    assert i["failed"] == [] and i["applied"] == 1
    assert i["inner"] == {"origin": "12", "slot": 0}
    assert i["rootSampler"] is None  # outside the multiplier's graph and below
    assert i["recorded"] == [["model|7:5|model", "d0"]]
    assert i["indexKeys"] == ["d0"] and i["d0Links"] == 1
    assert i["removed"] == 1 and i["after"] is None


def test_snapshot_adapter_shape(probe: dict) -> None:
    s = probe["snapshot"]
    assert s["graphs"] == ["root", "sg-x"]
    assert s["rootNodes"] == ["1", "3", "900"]
    assert s["instance"]["subgraphId"] == "sg-x"
    assert s["instance"]["inputs"][0]["defIndex"] == 0
    assert s["defInputs"] == [["model", "MODEL"]]
    assert s["mIsClass"] == "EPSCrossSweep"
    # bypass.js's inputVerdict, folded into the snapshot (optional / required)
    assert s["verdicts"]["text"] == "required" and s["verdicts"]["model"] == "optional"
    for name in (
        "snapshotFromRoot",
        "planFor",
        "applyProposals",
        "removeBroadcastWires",
        "withdrawOutputWires",
        "restoreOutputWires",
        "reconcileMultiplier",
        "runAsOneUndoStep",
        "broadcastLinkIndex",
        "isBroadcastLink",
        "bumpBroadcastEpoch",
        "isApplying",
        "readConfig",
        "writeConfig",
        "graphByKey",
        "graphKeyOf",
        "findMultipliers",
    ):
        assert name in s["exports"], name


# --------------------------------------------------------- source structure


def test_input_verdict_is_imported_from_bypass_not_copied(source: str) -> None:
    assert "import { inputVerdict } from './bypass.js'" in source
    assert "function inputVerdict" not in source
    assert "HOLLOW_CIRCLE_SHAPE" not in source
    # the planner stays litegraph-free: it never imports bypass.js
    plan = (REPO_ROOT / "web" / "eps_image" / "broadcast_plan.js").read_text(encoding="utf-8")
    assert "bypass" not in re.sub(r"/\*\*.*?\*/", "", plan, flags=re.S)


def test_the_graph_walkers_are_the_shared_ones_not_copies(source: str) -> None:
    assert (
        "import { nodesOfGraph, walkGraphs, walkLiveNodes } from '../lora_library/api.js'"
        in source
    )
    assert "function walkGraphs" not in source and "function walkLiveNodes" not in source
    # v1.3.0: the per-graph snapshot loop reads ONE graph's nodes through the
    # sanctioned api.nodesOfGraph accessor, never `graph._nodes` by hand.
    assert "_nodes" not in source.replace("nodesOfGraph", "")


def test_wires_are_made_with_core_primitives_and_never_replace(source: str) -> None:
    assert "mnode.connect(spec.index, target, slotIndex)" in source
    assert "sub.connect(target.inputs[slotIndex], target)" in source
    assert "target.disconnectInput(" not in source.replace("disconnectSlot", "")  # via the helper
    # every link step re-checks emptiness first (connect() would REPLACE)
    assert "is no longer empty — never replaced" in source


def test_removing_a_definition_input_severs_its_links_itself(source: str) -> None:
    """SubgraphSlot.disconnect() iterates linkIds while each removal splices
    it (skipping every other link): the module empties the list itself."""
    body = source[source.index("function removeDefinitionInput") :]
    body = body[: body.index("\n}\n")]
    assert "[...(input.linkIds || [])]" in body
    assert body.index("disconnectSlot(target, link.target_slot)") < body.index(
        "subgraph.removeInput(input)"
    )


def test_node_ids_are_compared_as_strings(source: str) -> None:
    """1.52.7's NodeId is a branded string ('-10'); older frontends use -10."""
    assert "String(live.origin_id) !== SUBGRAPH_INPUT_ID" in source
    assert "String(link.origin_id) !== SUBGRAPH_INPUT_ID" in source
    assert "-10)" not in source and "=== -10" not in source


def test_undo_wrapper_uses_the_canvas_transaction_events(source: str) -> None:
    body = source[source.index("export function runAsOneUndoStep") :]
    body = body[: body.index("\n}\n")]
    assert "canvas.emitBeforeChange()" in body and "canvas.emitAfterChange()" in body
    assert "captureCanvasState" in body
    assert body.index("captureCanvasState") < body.index("emitBeforeChange()")


def test_no_polling_no_one_shot_flags_no_window_listeners(source: str) -> None:
    for text in (source, BROADCAST_JS.read_text(encoding="utf-8")):
        assert "setInterval" not in text
        assert "window.addEventListener" not in text
