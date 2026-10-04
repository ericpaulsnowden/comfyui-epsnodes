"""Frontend tests for the EPS Run Multiplier BROADCAST planner (FORMAT.md
§6.10 "Broadcast (v1)", owner ask 2026-10-03) -- ``web/eps_image/
broadcast_plan.js``.

The planner is PURE on purpose (cross_sweep.js's ``estimateRuns`` precedent):
it takes a plain snapshot of the whole workflow (root graph + every subgraph
DEFINITION), one multiplier's pathId, its ``properties.Broadcast`` config and
the ONE ComfyUI setting, and returns proposals / skips / conflicts. So this
file drives the REAL, unmodified module under Node with hand-built snapshots
-- no litegraph, no DOM -- following ``tests/test_cross_sweep_js.py``'s
served-layout fixture (the module is byte-copied into a tmp dir that mirrors
the served import depth).

The snapshot DSL below mirrors what ``broadcast_graph.js``'s live adapter
produces (including the folded-in ``inputVerdict`` -- bypass.js is imported
by the ADAPTER, never copied; that pin lives in test_broadcast_graph_js.py).
What this CANNOT cover, and the rig must: real ``node.connect`` /
``SubgraphInput.connect`` behaviour, real ``nodeData`` verdicts, real
undo -- see the applier tests for the fakes and the final report for the
UNCONFIRMED list.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PLAN_JS = REPO_ROOT / "web" / "eps_image" / "broadcast_plan.js"
BACKEND_PY = REPO_ROOT / "eps_image" / "nodes_cross_sweep.py"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

# ---------------------------------------------------------------- snapshot DSL

_LINK_IDS = iter(range(1, 100000))


def lk(origin: str | int, slot: int = 0) -> dict:
    """A snapshot input link `{id, originId, originSlot}`."""
    return {"id": next(_LINK_IDS), "originId": str(origin), "originSlot": slot}


def inp(
    name: str,
    type_: str,
    *,
    link: dict | None = None,
    verdict: str = "required",
    widget: bool = False,
    def_index: int | None = None,
) -> dict:
    entry = {
        "name": name,
        "type": type_,
        "link": link,
        "widget": widget,
        "verdict": "widget" if widget else verdict,
    }
    if def_index is not None:
        entry["defIndex"] = def_index
    return entry


def out(name: str, type_: str, links: list[tuple[str, str]] | None = None) -> dict:
    """An output with `(targetId, targetInputName)` links."""
    return {
        "name": name,
        "type": type_,
        "links": [{"targetId": t, "targetInput": i} for t, i in (links or [])],
    }


def node(
    nid: str | int,
    cls: str,
    title: str | None = None,
    inputs: list[dict] | None = None,
    outputs: list[dict] | None = None,
    *,
    subgraph: str | None = None,
    broadcast: dict | None = None,
    mode: int = 0,
) -> dict:
    return {
        "id": str(nid),
        "classType": cls,
        "title": title or cls,
        "mode": mode,
        "subgraphId": subgraph,
        "broadcast": broadcast,
        "inputs": inputs or [],
        "outputs": outputs or [],
    }


#: nodes_cross_sweep.py RETURN_NAMES / RETURN_TYPES, positionally.
M_OUTPUTS = [
    ("model", "MODEL"),
    ("clip", "CLIP"),
    ("image", "IMAGE"),
    ("text", "STRING"),
    ("save_prefix", "STRING"),
    ("label", "STRING"),
    ("vae", "VAE"),
    ("model_low", "MODEL"),
    ("run_info", "STRING"),
]


def multiplier(
    nid: str | int = 1,
    wired: tuple[str, ...] = ("model", "clip", "vae"),
    *,
    broadcast: dict | None = None,
    title: str = "EPS Run Multiplier",
) -> dict:
    """An EPS Run Multiplier with the named sweep inputs wired from node 900
    (a stand-in loader); `text` is always wired (required)."""
    types = {
        "text": "STRING",
        "model": "MODEL",
        "model_low": "MODEL",
        "clip": "CLIP",
        "label": "STRING",
        "image": "IMAGE",
        "vae": "VAE",
    }
    inputs = [
        inp(
            name,
            type_,
            link=lk(900) if (name in wired or name == "text") else None,
            verdict="required" if name == "text" else "optional",
        )
        for name, type_ in types.items()
    ]
    inputs.append(inp("name", "STRING", verdict="optional"))
    inputs.append(inp("base_folder", "STRING", widget=True))
    return node(
        nid,
        "EPSCrossSweep",
        title,
        inputs,
        [out(n, t) for n, t in M_OUTPUTS],
        broadcast=broadcast,
    )


def ksampler(nid, title="KSampler", *, model=None, positive_link=None) -> dict:
    return node(
        nid,
        "KSampler",
        title,
        [
            inp("model", "MODEL", link=model),
            inp("positive", "CONDITIONING", link=positive_link),
            inp("seed", "INT", widget=True),
        ],
    )


def vae_decode(nid, *, vae=None) -> dict:
    return node(
        nid, "VAEDecode", "VAE Decode", [inp("samples", "LATENT"), inp("vae", "VAE", link=vae)]
    )


def save_image(nid, title="Save Image") -> dict:
    return node(
        nid,
        "SaveImage",
        title,
        [inp("images", "IMAGE", link=lk(900)), inp("filename_prefix", "STRING", widget=True)],
    )


def encoder(nid, title="CLIP Text Encode", *, to=None) -> dict:
    return node(
        nid,
        "CLIPTextEncode",
        title,
        [inp("text", "STRING", widget=True), inp("clip", "CLIP")],
        [out("CONDITIONING", "CONDITIONING", to or [])],
    )


def graph(nodes: list[dict], *, gid="root", name="", inputs=None, outputs=None) -> dict:
    return {
        "id": gid,
        "name": name,
        "nodes": {n["id"]: n for n in nodes},
        "inputs": inputs or [],
        "outputs": outputs or [],
    }


def snapshot(root: list[dict], defs: dict[str, dict] | None = None) -> dict:
    graphs = {"root": graph(root)}
    graphs.update(defs or {})
    return {"graphs": graphs}


def sub_instance(nid, definition: str, title: str, inputs: list[dict]) -> dict:
    return node(nid, "Subgraph", title, inputs, [], subgraph=definition)


def sub_def(gid: str, name: str, nodes: list[dict], inputs: list[dict], outputs=None) -> dict:
    return graph(nodes, gid=gid, name=name, inputs=inputs, outputs=outputs)


def def_input(uuid: str, name: str, type_: str) -> dict:
    return {"id": uuid, "name": name, "type": type_}


# ------------------------------------------------------------------- probe

PROBE_JS = r"""
import * as plan from './extensions/comfyui-epsnodes/eps_image/broadcast_plan.js'

const cases = __CASES__
const out = { plans: [], misc: {} }
for (const c of cases) {
  out.plans.push(plan.planBroadcast(c.snapshot, c.mpath, c.config ?? null, c.settings ?? {}))
}
const misc = __MISC__
out.misc.exports = Object.keys(plan).sort()
out.misc.outputs = plan.BROADCAST_OUTPUTS.map((o) => [o.name, o.type, o.index])
out.misc.constants = {
  classId: plan.MULTIPLIER_CLASS_ID, prop: plan.PROPERTY_KEY, version: plan.RECORD_VERSION,
  root: plan.ROOT_GRAPH_ID, kinds: plan.KINDS, codes: plan.SKIP_CODES
}
out.misc.normalize = misc.normalize.map((raw) => plan.normalizeConfig(raw))
out.misc.serialize = misc.serialize.map((raw) => plan.serializeConfig(raw))
out.misc.low = misc.low.map((t) => plan.titleIsLow(t))
out.misc.negfed = misc.negfed.map((c) => plan.negativeFedTextKeys(c.snapshot, c.config))
out.misc.reconcile = misc.reconcile.map((c) =>
  plan.reconcileConfig(c.snapshot, c.mpath, c.config, { manual: c.manual }))
out.misc.index = misc.index.map((c) => {
  const idx = plan.buildLinkIndex(c.snapshot)
  return Object.fromEntries([...idx.entries()].map(([k, v]) => [k, [...v].sort((a, b) => a - b)]))
})
out.misc.describe = plans_describe(out.plans)
function plans_describe(plans) {
  return plans.map((p) => p.proposals.map((x) => plan.describeProposal(x)))
}
process.stdout.write(JSON.stringify(out))
"""

_CASES: list[dict] = []
_MISC: dict = {
    "normalize": [],
    "serialize": [],
    "low": [],
    "reconcile": [],
    "index": [],
    "negfed": [],
}


def case(
    name: str,
    snap: dict,
    mpath: str = "1",
    config: dict | None = None,
    settings: dict | None = None,
) -> int:
    """Registers a planner case and returns its index into the probe output."""
    _CASES.append(
        {"name": name, "snapshot": snap, "mpath": mpath, "config": config, "settings": settings}
    )
    return len(_CASES) - 1


# ---- cases -----------------------------------------------------------------

#: 1. The bread-and-butter workflow: 2 samplers + a decode + 2 savers.
BASIC = case(
    "basic",
    snapshot(
        [
            multiplier(1, wired=("model", "clip", "vae")),
            ksampler(10),
            ksampler(11, "KSampler B"),
            encoder(12),
            vae_decode(13),
            save_image(14),
        ]
    ),
)

#: 2. Required vs optional vs widget vs wired.
ROLES = case(
    "roles",
    snapshot(
        [
            multiplier(1, wired=("model",)),
            node(20, "Required", "Required", [inp("model", "MODEL", verdict="required")]),
            node(21, "Optional", "Optional", [inp("model", "MODEL", verdict="optional")]),
            node(22, "Hollow", "Hollow", [inp("model", "MODEL", verdict="shape")]),
            node(23, "Unknown", "Unknown", [inp("model", "MODEL", verdict="unknown")]),
            node(24, "Wired", "Wired", [inp("model", "MODEL", link=lk(900), verdict="required")]),
            node(25, "Mystery", "Mystery", [inp("anything", "*", verdict="required")]),
            node(26, "Muted", "Muted", [inp("model", "MODEL", verdict="required")], mode=2),
            node(27, "Bypassed", "Bypassed", [inp("model", "MODEL", verdict="required")], mode=4),
        ]
    ),
)

#: 3. A growing-slot node (a Model Switcher): empty OPTIONAL `model_N` inputs
#: must NEVER be fed (they would grow a fresh empty slot after every wire).
SWITCHER = case(
    "switcher",
    snapshot(
        [
            multiplier(1, wired=("model",)),
            node(
                30,
                "EPSModelSwitcher",
                "EPS Model Switcher",
                [
                    inp("model_1", "MODEL", link=lk(900), verdict="shape"),
                    inp("model_2", "MODEL", verdict="shape"),
                    inp("model_3", "MODEL", verdict="optional"),
                ],
            ),
        ]
    ),
)

#: 4. Dead outputs: only `model` is wired; clip/vae are not live.
DEAD = case(
    "dead outputs",
    snapshot(
        [
            multiplier(1, wired=("model",)),
            ksampler(10),
            node(11, "CLIPTextEncode", "enc", [inp("clip", "CLIP")]),
            vae_decode(12),
        ]
    ),
)

#: 5. Exact-name outputs with the setting OFF (default) and ON.
EXACT_SNAP = snapshot(
    [
        multiplier(1, wired=("image", "label")),
        encoder(40, "Positive", to=[("43", "positive")]),
        encoder(41, "Negative", to=[("43", "negative")]),
        node(
            42,
            "ImageScale",
            "Image Scale",
            [inp("image", "IMAGE"), inp("pixels", "IMAGE"), inp("images", "IMAGE")],
        ),
        node(
            43,
            "KSampler",
            "KSampler",
            [
                inp("positive", "CONDITIONING", link=lk(40)),
                inp("negative", "CONDITIONING", link=lk(41)),
            ],
        ),
        node(44, "LabelThing", "Label thing", [inp("label", "STRING", widget=True)]),
        node(45, "NotText", "Not text", [inp("text", "INT", widget=True)]),
        save_image(46),
        node(
            47,
            "EPSSaveImage",
            "EPS Save Image",
            [
                inp("images", "IMAGE", link=lk(900)),
                inp("filename_prefix", "STRING", widget=True),
                inp("run_info", "STRING", verdict="optional"),
            ],
        ),
    ]
)
EXACT_OFF = case("exact names, setting off", EXACT_SNAP, settings={"exactNames": False})
EXACT_ON = case("exact names, setting on", EXACT_SNAP, settings={"exactNames": True})

#: 6. WAN pair: model_low wired; samplers titled high/low.
WAN_SNAP = snapshot(
    [
        multiplier(1, wired=("model", "model_low", "vae")),
        ksampler(50, "KSampler (high noise)"),
        ksampler(51, "KSampler (LOW noise)"),
        ksampler(52, "KSampler (already wired low)", model=lk(900)),
    ]
)
WAN = case("WAN pair", WAN_SNAP)
WAN_NO_LOW = case(
    "WAN pair, no low sampler",
    snapshot(
        [
            multiplier(1, wired=("model", "model_low")),
            ksampler(50, "KSampler A"),
            ksampler(51, "KSampler B"),
        ]
    ),
)
WAN_AMBIGUOUS = case(
    "WAN pair, low node with two MODEL inputs",
    snapshot(
        [
            multiplier(1, wired=("model", "model_low")),
            ksampler(50, "KSampler (high)"),
            node(51, "Dual", "Low blend", [inp("model_a", "MODEL"), inp("model_b", "MODEL")]),
        ]
    ),
)
#: model_low UNWIRED: "low" nodes are ordinary targets for `model`.
WAN_UNWIRED = case(
    "model_low unwired: titles do not matter",
    snapshot([multiplier(1, wired=("model",)), ksampler(50, "KSampler (low)"), ksampler(51)]),
)

#: 7. Ancestors: node 60 feeds the multiplier's `model`; 61 is an unrelated
#: loader-fed sampler; 62 is upstream through a REROUTE chain.
ANCESTORS = case(
    "ancestors",
    snapshot(
        [
            node(
                1,
                "EPSCrossSweep",
                "EPS Run Multiplier",
                [
                    inp("text", "STRING", link=lk(900)),
                    inp("model", "MODEL", link=lk(62), verdict="optional"),
                    inp("clip", "CLIP", verdict="optional"),
                ],
                [out(n, t) for n, t in M_OUTPUTS],
            ),
            node(
                62,
                "Reroute",
                "Reroute",
                [inp("", "*", link=lk(60), verdict="required")],
                [out("", "MODEL")],
            ),
            node(
                60,
                "LoraLoader",
                "Lora Loader",
                [inp("model", "MODEL"), inp("clip", "CLIP", verdict="required")],
                [out("MODEL", "MODEL")],
            ),
            ksampler(61),
        ]
    ),
)

#: 8. Another multiplier's inputs are never touched.
OTHER_M = case(
    "other multiplier is skipped",
    snapshot(
        [
            multiplier(1, wired=("model",)),
            multiplier(2, wired=(), broadcast={"outputs": {"model": False}}),
            ksampler(10),
        ]
    ),
)

#: 9. Two multipliers that could feed the same sampler: neither does.
TWO_M = case(
    "two multipliers conflict",
    snapshot(
        [
            multiplier(1, wired=("model",)),
            multiplier(2, wired=("model",)),
            ksampler(10),
            ksampler(11),
        ]
    ),
)
TWO_M_OTHER_OFF = case(
    "two multipliers, the other has model switched off",
    snapshot(
        [
            multiplier(1, wired=("model",)),
            multiplier(2, wired=("model",), broadcast={"outputs": {"model": False}}),
            ksampler(10),
        ]
    ),
)
TWO_M_DISJOINT_LIVE = case(
    "two multipliers, only one has vae live",
    snapshot([multiplier(1, wired=("vae",)), multiplier(2, wired=("model",)), vae_decode(10)]),
)

#: 10. Left alone / leave-alone list.
LEFT_ALONE = case(
    "left alone",
    snapshot([multiplier(1, wired=("model",)), ksampler(10), ksampler(11)]),
    config={"skip": ["model|10|model"]},
)


#: 11. A SubgraphNode ancestor: the multiplier's model comes OUT of subgraph
#: 3, so inner node 5 (which feeds that output) is an ancestor and must not
#: be fed; inner node 6 (unrelated) is fine -- ancestry is decided per
#: flattened path across the boundary.
def _ancestor_across_boundary() -> dict:
    inner = sub_def(
        "sg-a",
        "Loader group",
        [
            node(
                5,
                "LoraLoader",
                "Lora (feeds out)",
                [inp("model", "MODEL", verdict="required")],
                [out("MODEL", "MODEL")],
            ),
            node(6, "KSampler", "Inner sampler", [inp("model", "MODEL", verdict="required")]),
        ],
        inputs=[],
        outputs=[{"name": "model", "type": "MODEL", "origin": {"originId": "5", "originSlot": 0}}],
    )
    root = [
        node(
            1,
            "EPSCrossSweep",
            "EPS Run Multiplier",
            [
                inp("text", "STRING", link=lk(900)),
                inp("model", "MODEL", link=lk(3), verdict="optional"),
                inp("clip", "CLIP", verdict="optional"),
            ],
            [out(n, t) for n, t in M_OUTPUTS],
        ),
        sub_instance(3, "sg-a", "Loader group", []),
    ]
    return snapshot(root, {"sg-a": inner})


ANCESTOR_BOUNDARY = case("ancestor across a subgraph boundary", _ancestor_across_boundary())


# --- nested: Tier 1 / Tier 2 -------------------------------------------------


def _tier1() -> dict:
    """Instance 3 (def sg-1) has an EMPTY `model` input whose definition input
    already feeds inner KSampler 5's required `model`."""
    definition = sub_def(
        "sg-1",
        "Sampler group",
        [
            node(
                5,
                "KSampler",
                "Inner KSampler",
                [inp("model", "MODEL", link=lk(-10, 0), verdict="required")],
            ),
        ],
        inputs=[def_input("u-model", "model", "MODEL")],
    )
    root = [
        multiplier(1, wired=("model",)),
        sub_instance(3, "sg-1", "Sampler group", [inp("model", "MODEL", def_index=0)]),
    ]
    return snapshot(root, {"sg-1": definition})


TIER1 = case("tier 1: existing subgraph input", _tier1())


def _tier1_mixed() -> dict:
    """The existing input also feeds an OPTIONAL inner input: fail closed."""
    definition = sub_def(
        "sg-1",
        "Sampler group",
        [
            node(
                5,
                "KSampler",
                "Inner KSampler",
                [inp("model", "MODEL", link=lk(-10, 0), verdict="required")],
            ),
            node(
                6,
                "Opt",
                "Optional taker",
                [inp("model", "MODEL", link=lk(-10, 0), verdict="optional")],
            ),
        ],
        inputs=[def_input("u-model", "model", "MODEL")],
    )
    root = [
        multiplier(1, wired=("model",)),
        sub_instance(3, "sg-1", "Sampler group", [inp("model", "MODEL", def_index=0)]),
    ]
    return snapshot(root, {"sg-1": definition})


TIER1_MIXED = case("tier 1: input also feeds an optional input", _tier1_mixed())


def _tier1_wired_instance() -> dict:
    definition = sub_def(
        "sg-1",
        "Sampler group",
        [
            node(
                5, "KSampler", "Inner", [inp("model", "MODEL", link=lk(-10, 0), verdict="required")]
            )
        ],
        inputs=[def_input("u-model", "model", "MODEL")],
    )
    root = [
        multiplier(1, wired=("model",)),
        sub_instance(
            3, "sg-1", "Sampler group", [inp("model", "MODEL", link=lk(900), def_index=0)]
        ),
    ]
    return snapshot(root, {"sg-1": definition})


TIER1_WIRED = case("tier 1: instance input already wired", _tier1_wired_instance())


def _tier1_exact() -> dict:
    """save_prefix through an existing STRING subgraph input feeding a
    widget-backed filename_prefix (promoted widget)."""
    definition = sub_def(
        "sg-s",
        "Saver group",
        [
            node(
                5,
                "SaveImage",
                "Inner Save",
                [
                    inp("images", "IMAGE"),
                    inp("filename_prefix", "STRING", link=lk(-10, 0), widget=True),
                ],
            )
        ],
        inputs=[def_input("u-fp", "filename_prefix", "STRING")],
    )
    root = [
        multiplier(1, wired=()),
        sub_instance(3, "sg-s", "Saver group", [inp("filename_prefix", "STRING", def_index=0)]),
    ]
    return snapshot(root, {"sg-s": definition})


TIER1_EXACT = case("tier 1: save_prefix into a promoted widget input", _tier1_exact())


def _tier2() -> dict:
    """No definition input at all: a KSampler with an empty required model
    inside def sg-2; the only instance is in the multiplier's graph."""
    definition = sub_def(
        "sg-2",
        "Sampler group",
        [
            node(5, "KSampler", "Inner KSampler", [inp("model", "MODEL", verdict="required")]),
            node(6, "VAEDecode", "Inner decode", [inp("vae", "VAE", verdict="required")]),
        ],
        inputs=[def_input("u-user", "model", "MODEL")],  # a USER input already called `model`
    )
    root = [
        multiplier(1, wired=("model", "vae")),
        sub_instance(3, "sg-2", "Sampler group", [inp("model", "MODEL", def_index=0)]),
    ]
    return snapshot(root, {"sg-2": definition})


TIER2 = case("tier 2: new subgraph input", _tier2())


def _tier2_two_instances() -> dict:
    definition = sub_def(
        "sg-2",
        "Sampler group",
        [node(5, "KSampler", "Inner KSampler", [inp("model", "MODEL", verdict="required")])],
        inputs=[],
    )
    root = [
        multiplier(1, wired=("model",)),
        sub_instance(3, "sg-2", "Sampler group A", []),
        sub_instance(4, "sg-2", "Sampler group B", []),
    ]
    return snapshot(root, {"sg-2": definition})


TIER2_TWO = case("tier 2: two instances in the multiplier's graph", _tier2_two_instances())


def _tier2_shared() -> dict:
    """Definition sg-2 is instantiated in the root AND inside another
    definition: broadcasting into it would leave the second use unfed."""
    sampler_def = sub_def(
        "sg-2",
        "Sampler group",
        [node(5, "KSampler", "Inner KSampler", [inp("model", "MODEL", verdict="required")])],
        inputs=[],
    )
    other_def = sub_def(
        "sg-9", "Other group", [sub_instance(7, "sg-2", "Sampler group (again)", [])], inputs=[]
    )
    root = [
        multiplier(1, wired=("model",)),
        sub_instance(3, "sg-2", "Sampler group", []),
        sub_instance(4, "sg-9", "Other group", []),
    ]
    return snapshot(root, {"sg-2": sampler_def, "sg-9": other_def})


TIER2_SHARED = case(
    "tier 2: definition shared with an unfed instance fails closed", _tier2_shared()
)


def _tier2_shared_by_unrelated() -> dict:
    """Instance in the root + an instance in a DIFFERENT def that is itself
    not reachable from the multiplier's graph via the new-input route."""
    sampler_def = sub_def(
        "sg-2",
        "Sampler group",
        [node(5, "KSampler", "Inner KSampler", [inp("model", "MODEL", verdict="required")])],
        inputs=[],
    )
    elsewhere = sub_def("sg-8", "Elsewhere", [sub_instance(7, "sg-2", "again", [])], inputs=[])
    root = [multiplier(1, wired=("model",)), sub_instance(3, "sg-2", "Sampler group", [])]
    # sg-8 has no instance anywhere: it only exists as a definition.
    return snapshot(root, {"sg-2": sampler_def, "sg-8": elsewhere})


TIER2_SHARED_ORPHAN_DEF = case(
    "tier 2: a second instance inside an unreachable definition", _tier2_shared_by_unrelated()
)


def _tier2_nested() -> dict:
    """Root -> instance 3 (def d1) -> instance 8 (def d2) -> KSampler 5."""
    d2 = sub_def(
        "d2",
        "Inner group",
        [node(5, "KSampler", "Deep KSampler", [inp("model", "MODEL", verdict="required")])],
        inputs=[],
    )
    d1 = sub_def(
        "d1",
        "Outer group",
        [
            sub_instance(8, "d2", "Inner group", []),
            node(6, "KSampler", "Shallow KSampler", [inp("model", "MODEL", verdict="required")]),
        ],
        inputs=[],
    )
    root = [multiplier(1, wired=("model",)), sub_instance(3, "d1", "Outer group", [])]
    return snapshot(root, {"d1": d1, "d2": d2})


TIER2_NESTED = case("tier 2: nested two levels deep", _tier2_nested())


def _tier2_nested_shared_inner() -> dict:
    """d2 is ALSO instantiated at the root: the deep route must fail closed
    (d2's instances live in two different graphs)."""
    d2 = sub_def(
        "d2",
        "Inner group",
        [node(5, "KSampler", "Deep KSampler", [inp("model", "MODEL", verdict="required")])],
        inputs=[],
    )
    d1 = sub_def("d1", "Outer group", [sub_instance(8, "d2", "Inner group", [])], inputs=[])
    root = [
        multiplier(1, wired=("model",)),
        sub_instance(3, "d1", "Outer group", []),
        sub_instance(4, "d2", "Inner (root)", []),
    ]
    return snapshot(root, {"d1": d1, "d2": d2})


TIER2_NESTED_SHARED = case(
    "tier 2: nested definition also used at the top fails closed", _tier2_nested_shared_inner()
)


def _tier2_ancestor_inside() -> dict:
    """Instance 3's inner node 5 feeds the subgraph OUTPUT that the
    multiplier's model comes from: feeding it would close a loop."""
    definition = sub_def(
        "sg-2",
        "Loader group",
        [
            node(
                5,
                "LoraLoader",
                "Lora",
                [inp("model", "MODEL", verdict="required")],
                [out("MODEL", "MODEL")],
            )
        ],
        inputs=[],
        outputs=[{"name": "m", "type": "MODEL", "origin": {"originId": "5", "originSlot": 0}}],
    )
    root = [
        node(
            1,
            "EPSCrossSweep",
            "EPS Run Multiplier",
            [
                inp("text", "STRING", link=lk(900)),
                inp("model", "MODEL", link=lk(3), verdict="optional"),
            ],
            [out(n, t) for n, t in M_OUTPUTS],
        ),
        sub_instance(3, "sg-2", "Loader group", []),
    ]
    return snapshot(root, {"sg-2": definition})


TIER2_LOOP = case("tier 2: inner ancestor is never fed", _tier2_ancestor_inside())


def _tier2_reuse() -> dict:
    """A previous broadcast already made `model` on sg-2; a NEW empty inner
    KSampler appears -> reuse the recorded input, add nothing."""
    definition = sub_def(
        "sg-2",
        "Sampler group",
        [
            node(
                5, "KSampler", "Old", [inp("model", "MODEL", link=lk(-10, 0), verdict="required")]
            ),
            node(6, "KSampler", "New", [inp("model", "MODEL", verdict="required")]),
        ],
        inputs=[def_input("u-made", "model", "MODEL")],
    )
    root = [
        multiplier(1, wired=("model",)),
        sub_instance(
            3, "sg-2", "Sampler group", [inp("model", "MODEL", link=lk(900), def_index=0)]
        ),
    ]
    return snapshot(root, {"sg-2": definition})


TIER2_REUSE = case(
    "tier 2: reuses an input a previous broadcast made",
    _tier2_reuse(),
    config={
        "wired": [
            {
                "key": "model|def:sg-2|new",
                "out": "model",
                "kind": "via-new-subgraph-input",
                "to": "3",
                "input": "model",
                "links": [{"g": "root", "n": "3", "i": "model", "o": {"m": "model"}}],
                "made": [{"g": "sg-2", "id": "u-made", "name": "model", "type": "MODEL"}],
            }
        ]
    },
)


def _text_negative_via_subgraph() -> dict:
    definition = sub_def(
        "sg-t",
        "Prompt group",
        [
            encoder(5, "Pos", to=[("7", "positive")]),
            encoder(6, "Neg", to=[("7", "negative")]),
        ],
        inputs=[],
    )
    root = [multiplier(1, wired=()), sub_instance(3, "sg-t", "Prompt group", [])]
    return snapshot(root, {"sg-t": definition})


TEXT_NESTED = case(
    "text into a subgraph honours the negative guard",
    _text_negative_via_subgraph(),
    settings={"exactNames": True},
)


def _mixed_everything() -> dict:
    return snapshot(
        [
            multiplier(1, wired=("model", "clip", "vae", "label", "image")),
            ksampler(10),
            encoder(11, "Pos", to=[("10", "positive")]),
            encoder(12, "Neg", to=[("10", "negative")]),
            vae_decode(13),
            save_image(14),
            node(
                15,
                "EPSSaveImage",
                "EPS Save",
                [
                    inp("images", "IMAGE", link=lk(900)),
                    inp("filename_prefix", "STRING", widget=True),
                    inp("run_info", "STRING", verdict="optional"),
                ],
            ),
        ]
    )


MIXED = case("everything on", _mixed_everything(), settings={"exactNames": True})
MIXED_TOGGLED = case(
    "per-output toggles",
    _mixed_everything(),
    settings={"exactNames": True},
    config={"outputs": {"vae": False, "text": False}},
)

# --- extra adversarial cases -------------------------------------------------


def _reuse_plus_unwired_instance() -> dict:
    """A previous broadcast made `model` on sg-2 and fed instance 3, but a
    FRESH instance 4 has the same (empty) input, and a NEW empty inner node 6
    appeared: instance 4 is a tier-1 landing, node 6 reuses the made input."""
    definition = sub_def(
        "sg-2",
        "Sampler group",
        [
            node(
                5, "KSampler", "Old", [inp("model", "MODEL", link=lk(-10, 0), verdict="required")]
            ),
            node(6, "KSampler", "New", [inp("model", "MODEL", verdict="required")]),
        ],
        inputs=[def_input("u-made", "model", "MODEL")],
    )
    root = [
        multiplier(1, wired=("model",)),
        sub_instance(3, "sg-2", "Group A", [inp("model", "MODEL", link=lk(900), def_index=0)]),
        sub_instance(4, "sg-2", "Group B", [inp("model", "MODEL", def_index=0)]),
    ]
    return snapshot(root, {"sg-2": definition})


REUSE_PLUS = case(
    "reuse + a fresh unwired instance",
    _reuse_plus_unwired_instance(),
    config={
        "wired": [
            {
                "key": "model|def:sg-2|new",
                "out": "model",
                "kind": "via-new-subgraph-input",
                "to": "3",
                "input": "model",
                "links": [{"g": "root", "n": "3", "i": "model", "o": {"m": "model"}}],
                "made": [{"g": "sg-2", "id": "u-made", "name": "model", "type": "MODEL"}],
            }
        ]
    },
)


def _multiplier_inside_a_subgraph() -> dict:
    """The multiplier lives INSIDE definition d0 (instance 7). Its scope is d0
    and below: the inner KSampler 5 is reachable; the root's KSampler 20 is not."""
    d0 = sub_def(
        "d0",
        "Pipeline",
        [
            multiplier(12, wired=("model",)),
            node(5, "KSampler", "Inner KSampler", [inp("model", "MODEL", verdict="required")]),
        ],
        inputs=[],
    )
    root = [sub_instance(7, "d0", "Pipeline", []), ksampler(20, "Root KSampler")]
    return snapshot(root, {"d0": d0})


M_IN_SUBGRAPH = case("multiplier inside a subgraph", _multiplier_inside_a_subgraph(), mpath="7:12")


def _titled_low_instance() -> dict:
    """model_low through a tier-1 landing whose INSTANCE title says low."""
    definition = sub_def(
        "sg-1",
        "Sampler",
        [
            node(
                5, "KSampler", "Inner", [inp("model", "MODEL", link=lk(-10, 0), verdict="required")]
            )
        ],
        inputs=[def_input("u-model", "model", "MODEL")],
    )
    root = [
        multiplier(1, wired=("model", "model_low")),
        sub_instance(3, "sg-1", "Sampler (low noise)", [inp("model", "MODEL", def_index=0)]),
        sub_instance(4, "sg-1", "Sampler (high noise)", [inp("model", "MODEL", def_index=0)]),
    ]
    return snapshot(root, {"sg-1": definition})


WAN_TIER1 = case("WAN pair through tier-1 landings", _titled_low_instance())


def _conflict_across_a_boundary() -> dict:
    """M1 (root) reaches inner node 5 through a NEW input; M2 sits INSIDE the
    same definition and claims the same node directly: neither feeds it."""
    d = sub_def(
        "sg-c",
        "Shared inner",
        [
            multiplier(12, wired=("model",)),
            node(5, "KSampler", "Inner", [inp("model", "MODEL", verdict="required")]),
        ],
        inputs=[],
    )
    root = [multiplier(1, wired=("model",)), sub_instance(3, "sg-c", "Shared inner", [])]
    return snapshot(root, {"sg-c": d})


CONFLICT_BOUNDARY = case("conflict across a subgraph boundary", _conflict_across_a_boundary())


def _ancestor_instance_tier1() -> dict:
    """Instance 3's output feeds the multiplier; its definition input also
    feeds an inner node that is upstream of that output: a loop."""
    d = sub_def(
        "sg-1",
        "Loader",
        [
            node(
                5,
                "LoraLoader",
                "Lora",
                [inp("model", "MODEL", link=lk(-10, 0), verdict="required")],
                [out("MODEL", "MODEL")],
            )
        ],
        inputs=[def_input("u", "model", "MODEL")],
        outputs=[{"name": "m", "type": "MODEL", "origin": {"originId": "5", "originSlot": 0}}],
    )
    root = [
        node(
            1,
            "EPSCrossSweep",
            "EPS Run Multiplier",
            [
                inp("text", "STRING", link=lk(900)),
                inp("model", "MODEL", link=lk(3), verdict="optional"),
            ],
            [out(n, t) for n, t in M_OUTPUTS],
        ),
        sub_instance(3, "sg-1", "Loader", [inp("model", "MODEL", def_index=0)]),
    ]
    return snapshot(root, {"sg-1": d})


TIER1_LOOP = case("tier 1: the landing feeds the multiplier", _ancestor_instance_tier1())

LEFT_ALONE_NEW = case(
    "left alone applies to a new-input proposal by definition key",
    _tier2_two_instances(),
    config={"skip": ["model|def:sg-2|new"]},
)

ROOT_AS_TARGET_OF_ITSELF = case(
    "the multiplier's own empty inputs are never targets",
    snapshot([multiplier(1, wired=("model",))]),
)


NOT_A_MULTIPLIER = case("not a multiplier", snapshot([multiplier(1), ksampler(10)]), mpath="10")
MISSING_PATH = case("missing path", snapshot([multiplier(1)]), mpath="99:5")

# ---- misc probe inputs ------------------------------------------------------

NORMALIZE_CASES = [
    None,
    "junk",
    {},
    {"v": 99, "keep": True},
    {
        "v": 1,
        "keep": True,
        "outputs": {"vae": False, "bogus": True, "model": "no"},
        "look": "dim",
        "skip": ["a|1|b", "", 3, "a|1|b"],
    },
    {"wired": "nope"},
    {"wired": [{"key": "k", "out": "model", "kind": "direct", "links": "no"}, {"key": ""}, 5]},
    {
        "wired": [
            {
                "key": "model|10|model",
                "out": "model",
                "kind": "direct",
                "to": 10,
                "input": "model",
                "links": [
                    {"g": "root", "n": 10, "i": "model", "o": {"m": "model"}},
                    {"g": ""},
                    {"g": "root"},
                ],
                "made": [{"g": "sg", "id": "u1", "name": "model", "type": "MODEL"}, {"g": "sg"}],
                "withdrawn": True,
            }
        ]
    },
]
SERIALIZE_CASES = [
    None,
    {},
    {"keep": False},
    {"keep": True},
    {"outputs": {"vae": False}},
    {"skip": ["x|1|y"]},
    {"look": "dim"},
]
LOW_CASES = [
    ("KSampler (low noise)", True),
    ("LOW", True),
    ("Low Noise Sampler", True),
    ("low_noise", True),
    ("sampler-low", True),
    # v1.3.0 review: a WORD, not a substring -- these are not "low" samplers
    ("Flow Match", False),
    ("Slow sampler", False),
    ("Below", False),
    ("Lowpass", False),
    ("KSampler", False),
    ("", False),
    (None, False),
]

_MISC["normalize"] = NORMALIZE_CASES
_MISC["serialize"] = SERIALIZE_CASES
_MISC["low"] = [t for t, _ in LOW_CASES]

def _text_entry(target: int) -> dict:
    return {
        "key": f"text|{target}|text",
        "out": "text",
        "kind": "direct",
        "to": str(target),
        "input": "text",
        "links": [{"g": "root", "n": str(target), "i": "text", "o": {"m": "text"}}],
        "made": [],
    }


#: (v1.3.0) recorded text wires into the positive (40) and negative (41)
#: encoders of EXACT_SNAP; a config with only the positive one; no records.
_MISC["negfed"] = [
    {"snapshot": EXACT_SNAP, "config": {"wired": [_text_entry(40), _text_entry(41)]}},
    {"snapshot": EXACT_SNAP, "config": {"wired": [_text_entry(40)]}},
    {"snapshot": EXACT_SNAP, "config": {"wired": []}},
]


def _recorded_direct(nid, linkid, out_name="model", input_name="model"):
    return {
        "key": f"{out_name}|{nid}|{input_name}",
        "out": out_name,
        "kind": "direct",
        "to": str(nid),
        "input": input_name,
        "links": [{"g": "root", "n": str(nid), "i": input_name, "o": {"m": out_name}}],
        "made": [],
    }


def _reconcile_snapshots():
    good = ksampler(10, model={"id": 77, "originId": "1", "originSlot": 0})
    snap_ok = snapshot([multiplier(1, wired=("model",), broadcast={"keep": True}), good])
    # the wire came from a DIFFERENT node now
    moved = ksampler(10, model={"id": 78, "originId": "42", "originSlot": 0})
    snap_moved = snapshot(
        [
            multiplier(1, wired=("model",)),
            moved,
            node(42, "Loader", "L", [], [out("MODEL", "MODEL")]),
        ]
    )
    # unplugged by hand
    snap_unplugged = snapshot([multiplier(1, wired=("model",)), ksampler(10)])
    # target deleted
    snap_gone = snapshot([multiplier(1, wired=("model",))])
    # the wire is from the multiplier but a DIFFERENT output slot (clip's slot)
    wrong_slot = ksampler(10, model={"id": 79, "originId": "1", "originSlot": 1})
    snap_slot = snapshot([multiplier(1, wired=("model",)), wrong_slot])
    return snap_ok, snap_moved, snap_unplugged, snap_gone, snap_slot


_ok, _moved, _unplugged, _gone, _slot = _reconcile_snapshots()
_REC = {"wired": [_recorded_direct(10, 77)]}
RECONCILE_CASES = [
    ("intact", _ok, _REC, False),
    ("moved elsewhere on load", _moved, _REC, False),
    ("moved elsewhere in session", _moved, _REC, True),
    ("unplugged on load", _unplugged, _REC, False),
    ("unplugged in session -> leave alone", _unplugged, _REC, True),
    ("target deleted", _gone, _REC, True),
    ("wrong output slot", _slot, _REC, True),
    (
        "withdrawn is kept while the target exists",
        _unplugged,
        {"wired": [{**_recorded_direct(10, 77), "withdrawn": True}]},
        True,
    ),
    (
        "withdrawn is dropped when the target is gone",
        _gone,
        {"wired": [{**_recorded_direct(10, 77), "withdrawn": True}]},
        True,
    ),
]
_MISC["reconcile"] = [
    {"snapshot": s, "mpath": "1", "config": c, "manual": m} for _, s, c, m in RECONCILE_CASES
]


def _index_snapshot():
    inner = sub_def(
        "sg-2",
        "Sampler group",
        [
            node(
                5,
                "KSampler",
                "Inner",
                [
                    inp(
                        "model",
                        "MODEL",
                        link={"id": 31, "originId": "-10", "originSlot": 0},
                        verdict="required",
                    )
                ],
            )
        ],
        inputs=[def_input("u-made", "model", "MODEL")],
    )
    cfg = {
        "wired": [
            _recorded_direct(10, 0),
            {
                "key": "model|def:sg-2|new",
                "out": "model",
                "kind": "via-new-subgraph-input",
                "to": "3",
                "input": "model",
                "links": [
                    {"g": "root", "n": "3", "i": "model", "o": {"m": "model"}},
                    {"g": "sg-2", "n": "5", "i": "model", "o": {"s": "u-made"}},
                ],
                "made": [{"g": "sg-2", "id": "u-made", "name": "model", "type": "MODEL"}],
            },
        ]
    }
    root = [
        multiplier(1, wired=("model",), broadcast=cfg),
        ksampler(10, model={"id": 21, "originId": "1", "originSlot": 0}),
        sub_instance(
            3,
            "sg-2",
            "Sampler group",
            [inp("model", "MODEL", link={"id": 22, "originId": "1", "originSlot": 0}, def_index=0)],
        ),
    ]
    return snapshot(root, {"sg-2": inner})


_MISC["index"] = [{"snapshot": _index_snapshot()}]


@pytest.fixture(scope="module")
def probe(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Runs every registered case against the REAL broadcast_plan.js in a
    served-layout tmp dir (see module docstring) and returns the output."""
    layout = tmp_path_factory.mktemp("web_root")
    module_dir = layout / "extensions" / "comfyui-epsnodes" / "eps_image"
    module_dir.mkdir(parents=True)
    shutil.copyfile(PLAN_JS, module_dir / "broadcast_plan.js")
    source = PROBE_JS.replace("__CASES__", json.dumps(_CASES)).replace(
        "__MISC__", json.dumps(_MISC)
    )
    probe_file = layout / "probe.mjs"
    probe_file.write_text(source, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(probe_file)], capture_output=True, text=True, timeout=60, cwd=layout
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def source() -> str:
    return PLAN_JS.read_text(encoding="utf-8")


# ----------------------------------------------------------------- helpers


def _pairs(plan: dict) -> set[tuple[str, str, str]]:
    """`(output, targetPathId, inputName)` of every proposal."""
    return {(p["output"], p["targetPathId"], p["inputName"]) for p in plan["proposals"]}


def _skips(plan: dict, code: str) -> list[dict]:
    return [s for s in plan["skips"] if s["code"] == code]


def _only(plan: dict, output: str) -> list[dict]:
    return [p for p in plan["proposals"] if p["output"] == output]


# ------------------------------------------------------------------- tests


def test_plan_js_parses() -> None:
    result = subprocess.run(
        [NODE, "--check", str(PLAN_JS)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


def test_outputs_table_matches_the_backend_return_names(probe: dict) -> None:
    """The nine outputs, positionally: the planner's names, types and INDEXES
    are the backend's RETURN_NAMES/RETURN_TYPES (a saved link records
    [origin_id, origin_slot] -- a drift here would wire the wrong output)."""
    from eps_image.nodes_cross_sweep import EPSCrossSweep

    expected = [
        [name, type_, i]
        for i, (name, type_) in enumerate(
            zip(EPSCrossSweep.RETURN_NAMES, EPSCrossSweep.RETURN_TYPES, strict=True)
        )
    ]
    assert probe["misc"]["outputs"] == expected


def test_multiplier_input_names_match_the_backend() -> None:
    """The `liveInput` names the planner reads off the multiplier are real
    inputs on the backend node."""
    from eps_image.nodes_cross_sweep import EPSCrossSweep

    spec = EPSCrossSweep.INPUT_TYPES()
    names = set(spec["required"]) | set(spec["optional"])
    for live in ("model", "clip", "image", "label", "vae", "model_low"):
        assert live in names


def test_constants_and_exports(probe: dict) -> None:
    constants = probe["misc"]["constants"]
    assert constants["classId"] == "EPSCrossSweep"
    assert constants["prop"] == "Broadcast"
    assert constants["version"] == 1
    assert constants["root"] == "root"
    assert constants["kinds"] == {
        "DIRECT": "direct",
        "EXISTING": "via-existing-subgraph-input",
        "NEW": "via-new-subgraph-input",
    }
    for name in (
        "planBroadcast",
        "normalizeConfig",
        "serializeConfig",
        "reconcileConfig",
        "buildLinkIndex",
        "describeProposal",
        "BROADCAST_OUTPUTS",
        "SKIP_CODES",
        "locate",
        "resolveOrigin",
        "computeAncestors",
        "proposalKey",
        "newInputKey",
        "titleIsLow",
        "outputEnabled",
        "verifyLinkRecord",
    ):
        assert name in probe["misc"]["exports"], name


def test_basic_plan_wires_every_live_empty_required_input(probe: dict) -> None:
    plan = probe["plans"][BASIC]
    assert plan["error"] is None
    assert _pairs(plan) == {
        ("model", "10", "model"),
        ("model", "11", "model"),
        ("clip", "12", "clip"),
        ("vae", "13", "vae"),
        ("save_prefix", "14", "filename_prefix"),
    }
    # every proposal is a plain link step off the multiplier
    for p in plan["proposals"]:
        assert p["kind"] == "direct"
        assert p["steps"] == [
            {
                "op": "link",
                "graph": "root",
                "from": {"m": p["output"]},
                "to": {"node": p["targetPathId"], "input": p["inputName"]},
            }
        ]
        assert p["outputIndex"] == [n for n, _ in M_OUTPUTS].index(p["output"])


def test_basic_plan_save_prefix_goes_to_filename_prefix_and_run_info_to_run_info(
    probe: dict,
) -> None:
    """`save_prefix` -> any STRING input named exactly filename_prefix;
    `run_info` -> exactly run_info -- widget-backed or optional, no setting
    needed (owner decision 2026-10-03)."""
    plan = probe["plans"][BASIC]
    # BASIC has a core SaveImage (filename_prefix); no run_info consumer there.
    assert ("save_prefix", "14", "filename_prefix") in _pairs(plan)
    mixed = probe["plans"][MIXED]
    assert ("save_prefix", "15", "filename_prefix") in _pairs(mixed)
    assert ("run_info", "15", "run_info") in _pairs(mixed)


def test_required_optional_widget_wired_roles(probe: dict) -> None:
    plan = probe["plans"][ROLES]
    assert _pairs(plan) == {
        ("model", "20", "model"),
        ("model", "26", "model"),  # muted: linked (cheap, harmless -- UE's default)
        ("model", "27", "model"),  # bypassed: likewise
    }
    by_target = {s["targetPathId"]: s for s in plan["skips"] if s.get("targetPathId")}
    assert by_target["21"]["code"] == "optional"
    assert by_target["22"]["code"] == "optional"  # hollow-circle fallback = optional
    assert by_target["23"]["code"] == "unknown-required"  # cannot tell -> never guess
    assert by_target["24"]["code"] == "already-wired"
    assert "25" not in by_target  # a `*` typed input never matches by type


def test_growing_slot_switcher_is_never_fed(probe: dict) -> None:
    """A Switcher's empty optional `model_N` slots would each grow a fresh
    empty slot after being wired -- an endless loop under Keep wired."""
    plan = probe["plans"][SWITCHER]
    assert plan["proposals"] == []
    codes = {s["inputName"]: s["code"] for s in plan["skips"] if s.get("targetPathId") == "30"}
    # model_1 is already wired and not a candidate kind: not even reported
    assert codes == {"model_2": "optional", "model_3": "optional"}


def test_dead_outputs_are_not_broadcast(probe: dict) -> None:
    """Only `model` is wired: clip/vae are NOT live (a wire from a dead output
    would trip nodes_cross_sweep.py's v0.51.0 consumed-but-unwired guard)."""
    plan = probe["plans"][DEAD]
    assert _pairs(plan) == {("model", "10", "model")}
    dead = {s["output"] for s in _skips(plan, "output-dead")}
    assert dead == {"clip", "vae", "model_low"}
    states = {o["output"]: o["state"] for o in plan["outputs"]}
    assert states["model"] == "on" and states["clip"] == "dead" and states["vae"] == "dead"
    # image / label are ALSO unwired, but the setting (off) is reported first
    assert states["image"] == states["label"] == "setting-off"
    # text / save_prefix / run_info have no backing input: always live
    assert states["save_prefix"] == "on" and states["run_info"] == "on"


def test_exact_name_outputs_are_gated_by_the_setting(probe: dict) -> None:
    off = probe["plans"][EXACT_OFF]
    assert not {o for o, _, _ in _pairs(off)} & {"text", "image", "label"}
    gated = {s["output"] for s in _skips(off, "setting-off")}
    assert gated == {"text", "image", "label"}
    states = {o["output"]: o["state"] for o in off["outputs"]}
    assert states["text"] == states["image"] == states["label"] == "setting-off"
    # save_prefix / run_info are NOT behind the setting
    assert ("save_prefix", "46", "filename_prefix") in _pairs(off)
    assert ("save_prefix", "47", "filename_prefix") in _pairs(off)
    assert ("run_info", "47", "run_info") in _pairs(off)


def test_exact_name_outputs_with_the_setting_on(probe: dict) -> None:
    on = probe["plans"][EXACT_ON]
    pairs = _pairs(on)
    # exact NAME and TYPE, widget-backed allowed
    assert ("text", "40", "text") in pairs
    assert ("image", "42", "image") in pairs  # `pixels` / `images` never match
    assert ("label", "44", "label") in pairs
    assert not any(t == "42" and i in ("pixels", "images") for _, t, i in pairs)
    # a `text` input of the wrong TYPE is never matched
    assert not any(t == "45" for _, t, _ in pairs)
    # the negative-prompt guard (owner decision 2026-10-03: ON)
    assert ("text", "41", "text") not in pairs
    guard = _skips(on, "negative-guard")
    assert [s["targetPathId"] for s in guard] == ["41"]


def test_wan_pair_routes_low_and_high(probe: dict) -> None:
    plan = probe["plans"][WAN]
    assert _only(plan, "model_low")[0]["targetPathId"] == "51"
    assert _pairs(plan) >= {("model_low", "51", "model"), ("model", "50", "model")}
    # `model` skips the low sampler once model_low is wired
    assert ("model", "51", "model") not in _pairs(plan)
    assert [s["targetPathId"] for s in _skips(plan, "wan-low")] == ["51"]
    # the already-wired low sampler is reported once, never as a model_low target
    assert ("model_low", "52", "model") not in _pairs(plan)
    assert not _skips(plan, "wan-unresolved")


def test_wan_pair_without_a_low_sampler_skips_with_a_reason(probe: dict) -> None:
    plan = probe["plans"][WAN_NO_LOW]
    assert _only(plan, "model_low") == []  # never guess which sampler is "low"
    unresolved = _skips(plan, "wan-unresolved")
    assert len(unresolved) == 1 and "low" in unresolved[0]["reason"]
    assert ("model", "50", "model") in _pairs(plan)
    assert ("model", "51", "model") in _pairs(plan)


def test_wan_low_node_with_two_empty_model_inputs_is_ambiguous(probe: dict) -> None:
    plan = probe["plans"][WAN_AMBIGUOUS]
    assert _only(plan, "model_low") == []
    assert [s["targetPathId"] for s in _skips(plan, "wan-ambiguous")] == ["51"]
    assert not _skips(plan, "wan-unresolved")


def test_titles_do_not_matter_while_model_low_is_unwired(probe: dict) -> None:
    plan = probe["plans"][WAN_UNWIRED]
    assert _pairs(plan) == {("model", "50", "model"), ("model", "51", "model")}
    assert not _skips(plan, "wan-low")
    states = {o["output"]: o["state"] for o in plan["outputs"]}
    assert states["model_low"] == "dead"


def test_recorded_text_wires_into_a_since_negative_encoder_are_flagged(probe: dict) -> None:
    """v1.3.0 rig finding: Keep wired fed `text` into a fresh CLIP Text Encode
    BEFORE the user wired it into `negative` (the guard can only see a link
    that exists). negativeFedTextKeys finds every RECORDED text entry whose
    target now feeds `negative` -- the live half withdraws exactly those."""
    assert probe["misc"]["negfed"] == [["text|41|text"], [], []]


def test_title_is_low_is_the_owners_literal_rule(probe: dict) -> None:
    got = probe["misc"]["low"]
    assert got == [expected for _, expected in LOW_CASES]


def test_ancestors_are_never_fed(probe: dict) -> None:
    """Node 60 (a LoRA loader behind a Reroute) feeds the multiplier's model
    input: its own empty required `clip`... hmm -- its empty optional input
    is not a candidate, but a REQUIRED one would be a cycle."""
    plan = probe["plans"][ANCESTORS]
    # 61 is unrelated and fed; the multiplier itself and 60/62 are not.
    assert ("model", "61", "model") in _pairs(plan)
    assert not any(t in ("1", "60", "62") for _, t, _ in _pairs(plan))
    # `clip` is NOT live (unwired): nothing from it either way
    assert not _only(plan, "clip")


LOOP_REPORTED = case(
    "loop reported",
    snapshot(
        [
            node(
                1,
                "EPSCrossSweep",
                "EPS Run Multiplier",
                [
                    inp("text", "STRING", link=lk(900)),
                    inp("model", "MODEL", link=lk(60), verdict="optional"),
                ],
                [out(n, t) for n, t in M_OUTPUTS],
            ),
            node(
                60,
                "LoraLoader",
                "Lora",
                [inp("model", "MODEL", verdict="required")],
                [out("MODEL", "MODEL")],
            ),
            ksampler(61),
        ]
    ),
)


def test_loop_skip_is_reported(probe: dict) -> None:
    plan = probe["plans"][LOOP_REPORTED]
    assert _pairs(plan) == {("model", "61", "model")}
    assert [(s["targetPathId"], s["code"]) for s in _skips(plan, "loop")] == [("60", "loop")]


def test_ancestry_is_decided_across_a_subgraph_boundary(probe: dict) -> None:
    """The multiplier's `model` comes OUT of subgraph 3 (inner node 5 feeds
    the output panel). Inner node 5 is therefore an ancestor -- feeding it
    would close a loop -- while the unrelated inner node 6 is not."""
    plan = probe["plans"][ANCESTOR_BOUNDARY]
    # node 6 (and only it) is reachable, and only through a NEW definition input
    assert [p["kind"] for p in plan["proposals"]] == ["via-new-subgraph-input"]
    reaches = [r["pathId"] for r in plan["proposals"][0]["reaches"]]
    assert reaches == ["3:6"]
    assert [s["targetPathId"] for s in _skips(plan, "loop")] == ["3:5"]


def test_other_multipliers_are_never_touched(probe: dict) -> None:
    plan = probe["plans"][OTHER_M]
    assert _pairs(plan) == {("model", "10", "model")}
    assert not any(t == "2" for _, t, _ in _pairs(plan))
    assert any(s["targetPathId"] == "2" for s in _skips(plan, "other-multiplier"))


def test_two_multipliers_that_could_feed_the_same_input_feed_neither(probe: dict) -> None:
    plan = probe["plans"][TWO_M]
    assert _only(plan, "model") == []
    conflicts = {(c["targetPathId"], c["inputName"]): c for c in plan["conflicts"]}
    assert set(conflicts) == {("10", "model"), ("11", "model")}
    assert all(c["claimants"] == ["2"] for c in conflicts.values())
    assert all("none of them" in c["reason"] for c in conflicts.values())


def test_switching_an_output_off_on_the_other_multiplier_resolves_the_conflict(probe: dict) -> None:
    plan = probe["plans"][TWO_M_OTHER_OFF]
    assert _pairs(plan) == {("model", "10", "model")}
    assert plan["conflicts"] == []


def test_a_multiplier_whose_output_is_dead_does_not_claim(probe: dict) -> None:
    plan = probe["plans"][TWO_M_DISJOINT_LIVE]
    assert _pairs(plan) == {("vae", "10", "vae")}
    assert plan["conflicts"] == []


def test_left_alone_entries_become_skips_with_their_proposal(probe: dict) -> None:
    plan = probe["plans"][LEFT_ALONE]
    assert _pairs(plan) == {("model", "11", "model")}
    left = _skips(plan, "left-alone")
    assert [s["key"] for s in left] == ["model|10|model"]
    assert left[0]["proposal"]["targetPathId"] == "10"


def test_tier1_wires_an_existing_empty_subgraph_input(probe: dict) -> None:
    plan = probe["plans"][TIER1]
    assert [p["kind"] for p in plan["proposals"]] == ["via-existing-subgraph-input"]
    p = plan["proposals"][0]
    assert (p["output"], p["targetPathId"], p["inputName"]) == ("model", "3", "model")
    assert p["steps"] == [
        {
            "op": "link",
            "graph": "root",
            "from": {"m": "model"},
            "to": {"node": "3", "input": "model"},
        }
    ]
    assert [r["pathId"] for r in p["reaches"]] == ["3:5"]
    assert p["definition"]["id"] == "sg-1"
    assert [s["op"] for s in p["steps"]] == ["link"]  # no definition change


def test_tier1_fails_closed_when_the_input_also_feeds_something_it_must_not_fill(
    probe: dict,
) -> None:
    plan = probe["plans"][TIER1_MIXED]
    assert plan["proposals"] == []
    assert [s["targetPathId"] for s in _skips(plan, "subgraph-mixed")] == ["3"]


def test_tier1_leaves_a_wired_instance_input_alone(probe: dict) -> None:
    plan = probe["plans"][TIER1_WIRED]
    assert plan["proposals"] == []


def test_tier1_exact_name_into_a_promoted_widget_input(probe: dict) -> None:
    plan = probe["plans"][TIER1_EXACT]
    assert _pairs(plan) == {("save_prefix", "3", "filename_prefix")}
    assert plan["proposals"][0]["kind"] == "via-existing-subgraph-input"


def test_tier2_adds_a_subgraph_input_when_no_path_exists(probe: dict) -> None:
    plan = probe["plans"][TIER2]
    by_output = {p["output"]: p for p in plan["proposals"]}
    assert set(by_output) == {"model", "vae"}
    model = by_output["model"]
    assert model["kind"] == "via-new-subgraph-input"
    # the user's own `model` input is never touched: the new one takes a suffix
    assert model["newInputName"] == "model_1"
    assert by_output["vae"]["newInputName"] == "vae"
    assert model["key"] == "model|def:sg-2|new"
    assert [s["op"] for s in model["steps"]] == ["add-input", "link", "link"]
    add, outer, inner = model["steps"]
    assert add == {
        "op": "add-input",
        "graph": "sg-2",
        "name": "model_1",
        "valueType": "MODEL",
        "ref": "in:sg-2:model",
    }
    assert outer == {
        "op": "link",
        "graph": "root",
        "from": {"m": "model"},
        "to": {"node": "3", "input": "model_1"},
    }
    assert inner == {
        "op": "link",
        "graph": "sg-2",
        "from": {"ref": "in:sg-2:model"},
        "to": {"node": "5", "input": "model"},
    }
    assert [r["pathId"] for r in model["reaches"]] == ["3:5"]


def test_tier2_wires_every_instance_in_the_multiplier_graph(probe: dict) -> None:
    plan = probe["plans"][TIER2_TWO]
    assert len(plan["proposals"]) == 1
    p = plan["proposals"][0]
    outer = [s for s in p["steps"] if s["op"] == "link" and s["graph"] == "root"]
    assert sorted(s["to"]["node"] for s in outer) == ["3", "4"]
    # ONE reach (the shared inner node) under BOTH flattened paths
    assert [r["pathIds"] for r in p["reaches"]] == [["3:5", "4:5"]]
    assert p["reachKeys"] == ["3:5|model", "4:5|model"]


def test_tier2_fails_closed_when_the_definition_is_shared_with_an_unfed_instance(
    probe: dict,
) -> None:
    plan = probe["plans"][TIER2_SHARED]
    assert plan["proposals"] == []
    shared = _skips(plan, "subgraph-shared")
    assert (
        shared
        and "wire" in shared[0]["reason"].lower()
        and "elsewhere" in shared[0]["reason"].lower()
    )


def test_tier2_fails_closed_for_an_instance_inside_an_unreachable_definition(probe: dict) -> None:
    plan = probe["plans"][TIER2_SHARED_ORPHAN_DEF]
    assert plan["proposals"] == []
    assert _skips(plan, "subgraph-shared")


def test_tier2_recurses_for_deeper_nesting_chaining_through_each_level(probe: dict) -> None:
    plan = probe["plans"][TIER2_NESTED]
    assert len(plan["proposals"]) == 1
    p = plan["proposals"][0]
    assert p["definition"]["id"] == "d1"
    steps = p["steps"]
    # add-input on d1, wire instance 3 from the multiplier, link d1's input to
    # node 6, then recurse: add-input on d2, wire instance 8 (in d1) from d1's
    # NEW input, link d2's input to node 5.
    assert steps[0] == {
        "op": "add-input",
        "graph": "d1",
        "name": "model",
        "valueType": "MODEL",
        "ref": "in:d1:model",
    }
    assert steps[1] == {
        "op": "link",
        "graph": "root",
        "from": {"m": "model"},
        "to": {"node": "3", "input": "model"},
    }
    assert {
        "op": "link",
        "graph": "d1",
        "from": {"ref": "in:d1:model"},
        "to": {"node": "6", "input": "model"},
    } in steps
    assert {
        "op": "add-input",
        "graph": "d2",
        "name": "model",
        "valueType": "MODEL",
        "ref": "in:d2:model",
    } in steps
    assert {
        "op": "link",
        "graph": "d1",
        "from": {"ref": "in:d1:model"},
        "to": {"node": "8", "input": "model"},
    } in steps
    assert {
        "op": "link",
        "graph": "d2",
        "from": {"ref": "in:d2:model"},
        "to": {"node": "5", "input": "model"},
    } in steps
    # ordering: an input exists before anything links to or from it
    first_use = {}
    for i, s in enumerate(steps):
        for ref in (s.get("from", {}).get("ref"), s.get("ref")):
            if ref and ref not in first_use:
                first_use[ref] = i
    add_index = {s["ref"]: i for i, s in enumerate(steps) if s["op"] == "add-input"}
    for ref, i in first_use.items():
        assert add_index[ref] <= i
    assert sorted(r["pathId"] for r in p["reaches"]) == ["3:6", "3:8:5"]
    assert [d["id"] for d in p["definitions"]] == ["d1", "d2"]


def test_tier2_nested_definition_also_used_at_the_top_fails_closed(probe: dict) -> None:
    plan = probe["plans"][TIER2_NESTED_SHARED]
    assert plan["proposals"] == []
    assert _skips(plan, "subgraph-shared")


def test_tier2_never_feeds_an_inner_ancestor(probe: dict) -> None:
    plan = probe["plans"][TIER2_LOOP]
    assert plan["proposals"] == []
    assert [s["targetPathId"] for s in _skips(plan, "loop")] == ["3:5"]


def test_tier2_reuses_an_input_a_previous_broadcast_made(probe: dict) -> None:
    plan = probe["plans"][TIER2_REUSE]
    assert len(plan["proposals"]) == 1
    p = plan["proposals"][0]
    assert p["reuse"] is True and p["newInputName"] == "model"
    assert [s["op"] for s in p["steps"]] == ["link"]  # no add-input, no outer wires
    assert p["steps"][0] == {
        "op": "link",
        "graph": "sg-2",
        "from": {"sub": "u-made"},
        "to": {"node": "6", "input": "model"},
    }


def test_text_into_a_subgraph_honours_the_negative_guard(probe: dict) -> None:
    plan = probe["plans"][TEXT_NESTED]
    assert len(plan["proposals"]) == 1
    assert [r["pathId"] for r in plan["proposals"][0]["reaches"]] == ["3:5"]
    assert [s["targetPathId"] for s in _skips(plan, "negative-guard")] == ["3:6"]


def test_per_output_toggles_and_everything_on(probe: dict) -> None:
    everything = _pairs(probe["plans"][MIXED])
    assert ("vae", "13", "vae") in everything and ("text", "11", "text") in everything
    assert ("text", "12", "text") not in everything  # feeds `negative`
    assert ("image", "14", "images") not in everything
    toggled = _pairs(probe["plans"][MIXED_TOGGLED])
    assert not any(o in ("vae", "text") for o, _, _ in toggled)
    assert ("model", "10", "model") in toggled
    offs = {s["output"] for s in _skips(probe["plans"][MIXED_TOGGLED], "output-off")}
    assert offs == {"vae", "text"}


def test_every_proposal_has_the_documented_shape(probe: dict) -> None:
    for i, plan in enumerate(probe["plans"]):
        for p in plan["proposals"]:
            for key in (
                "key",
                "output",
                "outputIndex",
                "kind",
                "targetPathId",
                "inputName",
                "reaches",
                "reachKeys",
                "steps",
                "instances",
                "definition",
                "newInputName",
                "reuse",
            ):
                assert key in p, (i, key)
            assert p["kind"] in ("direct", "via-existing-subgraph-input", "via-new-subgraph-input")
            assert p["steps"] and all(s["op"] in ("link", "add-input") for s in p["steps"])
            assert p["reachKeys"], (i, p["key"])
        for s in plan["skips"]:
            assert s["code"] in probe["misc"]["constants"]["codes"].values()
            assert s["reason"]


def test_describe_proposal_lines(probe: dict) -> None:
    lines = probe["misc"]["describe"][TIER2]
    assert any("a NEW" in line and "Sampler group" in line for line in lines)
    assert probe["misc"]["describe"][TIER1][0].startswith(
        'model → Sampler group #3 (its "model" input)'
    )
    assert probe["misc"]["describe"][BASIC][0].startswith("model → KSampler #10 (model)")


def test_reuse_and_a_fresh_unwired_instance_each_get_their_own_route(probe: dict) -> None:
    plan = probe["plans"][REUSE_PLUS]
    kinds = {p["kind"]: p for p in plan["proposals"]}
    assert set(kinds) == {"via-existing-subgraph-input", "via-new-subgraph-input"}
    existing = kinds["via-existing-subgraph-input"]
    assert (existing["targetPathId"], existing["inputName"]) == ("4", "model")
    reuse = kinds["via-new-subgraph-input"]
    assert reuse["reuse"] is True and [s["op"] for s in reuse["steps"]] == ["link"]


def test_a_multiplier_inside_a_subgraph_reaches_only_its_own_graph_and_below(probe: dict) -> None:
    plan = probe["plans"][M_IN_SUBGRAPH]
    assert plan["error"] is None
    assert [(p["kind"], p["targetPathId"]) for p in plan["proposals"]] == [("direct", "7:5")]
    step = plan["proposals"][0]["steps"][0]
    assert step["graph"] == "d0" and step["to"] == {"node": "5", "input": "model"}
    # the root's own KSampler is outside the scope (the multiplier's graph and below)
    assert not any(p["targetPathId"] == "20" for p in plan["proposals"])


def test_wan_pair_through_tier1_landings_uses_the_instance_title(probe: dict) -> None:
    plan = probe["plans"][WAN_TIER1]
    assert [(p["output"], p["targetPathId"]) for p in plan["proposals"]] == [
        ("model", "4"),
        ("model_low", "3"),
    ] or sorted((p["output"], p["targetPathId"]) for p in plan["proposals"]) == [
        ("model", "4"),
        ("model_low", "3"),
    ]
    assert [s["targetPathId"] for s in _skips(plan, "wan-low")] == ["3"]


def test_two_multipliers_conflict_across_a_subgraph_boundary(probe: dict) -> None:
    plan = probe["plans"][CONFLICT_BOUNDARY]
    assert plan["proposals"] == []
    assert [(c["targetPathId"], c["claimants"]) for c in plan["conflicts"]] == [("3", ["3:12"])]


def test_a_tier1_landing_that_feeds_the_multiplier_is_a_loop(probe: dict) -> None:
    plan = probe["plans"][TIER1_LOOP]
    assert plan["proposals"] == []
    assert [s["targetPathId"] for s in _skips(plan, "loop")] == ["3"]


def test_left_alone_applies_to_a_new_input_proposal_by_definition_key(probe: dict) -> None:
    plan = probe["plans"][LEFT_ALONE_NEW]
    assert plan["proposals"] == []
    left = _skips(plan, "left-alone")
    assert [s["key"] for s in left] == ["model|def:sg-2|new"]


def test_the_multipliers_own_inputs_are_never_targets(probe: dict) -> None:
    plan = probe["plans"][ROOT_AS_TARGET_OF_ITSELF]
    assert plan["proposals"] == [] and plan["error"] is None
    assert not any(s.get("targetPathId") == "1" for s in plan["skips"])


def test_a_non_multiplier_path_is_an_error_not_a_crash(probe: dict) -> None:
    plan = probe["plans"][NOT_A_MULTIPLIER]
    assert plan["proposals"] == [] and plan["error"] and "not an EPSCrossSweep" in plan["error"]
    missing = probe["plans"][MISSING_PATH]
    assert missing["proposals"] == [] and missing["error"]


def test_normalize_config_is_defensive(probe: dict) -> None:
    got = probe["misc"]["normalize"]
    default = {
        "v": 1,
        "outputs": {},
        "keep": False,
        "scope": "graph",
        "look": "tucked",
        "wired": [],
        "skip": [],
    }
    assert got[0] == got[1] == got[2] == default
    assert got[3] == default  # a NEWER version is read as empty, never guessed
    assert got[4]["keep"] is True
    assert got[4]["outputs"] == {"vae": False}  # unknown names / non-booleans dropped
    assert got[4]["look"] == "dim"
    assert got[4]["skip"] == ["a|1|b"]  # deduped, junk dropped
    assert got[5]["wired"] == [] and got[6]["wired"] == []
    entry = got[7]["wired"][0]
    assert entry["links"] == [{"g": "root", "n": "10", "i": "model", "o": {"m": "model"}}]
    assert entry["made"] == [{"g": "sg", "id": "u1", "name": "model", "type": "MODEL"}]
    assert entry["to"] == "10" and entry["withdrawn"] is True


def test_serialize_config_omits_an_all_default_config(probe: dict) -> None:
    got = probe["misc"]["serialize"]
    assert got[:3] == [None, None, None]  # old workflows stay byte-identical
    assert got[3]["keep"] is True
    assert got[4]["outputs"] == {"vae": False}
    assert got[5]["skip"] == ["x|1|y"]
    assert got[6]["look"] == "dim"


def test_reconcile_config_cases(probe: dict) -> None:
    results = {
        name: r for (name, *_), r in zip(RECONCILE_CASES, probe["misc"]["reconcile"], strict=True)
    }
    key = "model|10|model"
    intact = results["intact"]
    assert [e["key"] for e in intact["config"]["wired"]] == [key] and not intact["dropped"]
    # LOAD: a record whose wire no longer comes from this multiplier is dropped quietly
    for name in ("moved elsewhere on load", "unplugged on load"):
        assert results[name]["config"]["wired"] == [] and results[name]["dropped"] == [key]
        assert results[name]["config"]["skip"] == []
    # SESSION: the user's own edit becomes "leave alone"
    for name in (
        "moved elsewhere in session",
        "unplugged in session -> leave alone",
        "wrong output slot",
    ):
        assert results[name]["config"]["wired"] == []
        assert results[name]["leftAlone"] == [key] and results[name]["config"]["skip"] == [key]
    # a deleted target is NOT a leave-alone (nothing to leave alone)
    assert results["target deleted"]["config"]["skip"] == [] and results["target deleted"][
        "dropped"
    ] == [key]
    assert [
        e["key"] for e in results["withdrawn is kept while the target exists"]["config"]["wired"]
    ] == [key]
    assert results["withdrawn is dropped when the target is gone"]["config"]["wired"] == []


def test_link_index_covers_root_links_and_inner_subgraph_links(probe: dict) -> None:
    """The exported seam the rendering stage uses: per-graph link-id sets that
    include the INNER links this feature creates inside definitions, and only
    links that still verify against their record (the inner link 31 is
    registered on node 5's input; the outer wires are 21 and 22)."""
    index = probe["misc"]["index"][0]
    assert index == {"root": [21, 22], "sg-2": [31]}


# --------------------------------------------------------- source structure


def test_planner_is_pure_no_litegraph_dom_or_imports(source: str) -> None:
    """The planner never touches litegraph, the DOM, `app`, or the setting
    store -- and does not import bypass.js either: the ADAPTER folds
    `inputVerdict` into the snapshot (imported there, never copied)."""
    assert not re.search(r"^import ", source, re.M)
    for forbidden in (
        "document.",
        "window.",
        "app.",
        "LGraph",
        "setTimeout",
        "node.connect(",
        "localStorage",
    ):
        assert forbidden not in source, forbidden


def test_planner_does_not_copy_input_verdict(source: str) -> None:
    assert "function inputVerdict" not in source
    assert "HOLLOW_CIRCLE_SHAPE" not in source
