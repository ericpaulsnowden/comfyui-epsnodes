"""Frontend tests for the EPS Resolution "see through pass-through nodes"
fix (owner report 2026-08-28: "If an image is plugged into a switcher, and
then into a resolution node, the app can't tell what the resolution is"),
added to ``web/eps_image/resolution.js`` alongside the pre-existing M1-M4
code the sibling ``test_resolution_*_js.py`` files already cover.

``readIncomingImageSize`` was deliberately ONE HOP -- its own docstring
warned "a wrong number here would be worse than no number." This widens the
walk (`collectIncomingImageSizes`) to step through KNOWN pass-through
classes (`EPSSwitcher`, `EPSDistributor`, core `Reroute`/rgthree's
`Reroute (rgthree)`) while keeping that exact caution: an unfamiliar class
is always a WALL, never assumed to forward anything. The DECISION over
what was found (`summarizeIncomingSizes`) is pure and tested here without
any graph at all; the WALK itself needs fake node/graph objects, driven
headlessly under Node the same served-layout-probe way the sibling files
drive their own pure exports (their docstrings, this file's identical
convention) -- `collectIncomingImageSizes` only touches plain object
shapes (`.inputs`, `.getInputNode`, `.comfyClass`/`.type`, `.imgs`,
`.imageIndex`, `.id`), never real litegraph/DOM, so a full probe run is
possible for this one (unlike the M3/M4 attach()-only code, which the
sibling files pin via source text instead).

Skips cleanly when Node isn't installed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RESOLUTION_JS = REPO_ROOT / "web" / "eps_image" / "resolution.js"
API_JS = REPO_ROOT / "web" / "lora_library" / "api.js"
VERSION_JS = REPO_ROOT / "web" / "lora_library" / "version.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

# --------------------------------------------------------------------- probe
#
# Fake node/graph fixtures are built directly as JS object literals (raw JS,
# not JSON) so `getInputNode` can be a real closure. Each fixture in
# FIXTURES_JS defines a small graph and returns the ENTRY node (the
# "EPSResolution" stand-in) that `collectIncomingImageSizes` is called on.

FIXTURES_JS = """
// A "wall" node: something that displays a real image (a LoadImage/preview
// stand-in). No comfyClass the walk recognizes as a pass-through, so it is
// always a wall by construction.
function leaf(width, height, id) {
  return { id, type: 'LoadImage', imgs: [{ naturalWidth: width, naturalHeight: height }] }
}

// A wall node with nothing decoded yet (still loading, or displays nothing).
function emptyLeaf(id) {
  return { id, type: 'LoadImage', imgs: [] }
}

// An unrecognized node class -- also a wall, and must NOT be treated as a
// pass-through just because it happens to have exactly one input.
function unknownWall(id) {
  return { id, type: 'SomeThirdPartyNode', comfyClass: 'SomeThirdPartyNode', inputs: [] }
}

// entryNode(imageUpstream) -- the EPSResolution stand-in: one `image`
// input wired to whatever *imageUpstream* is (or unwired if null).
function entryNode(imageUpstream, id) {
  const inputs = [{ name: 'image', link: imageUpstream ? 1 : null }]
  return {
    id: id ?? 'entry',
    inputs,
    getInputNode(slot) {
      return slot === 0 ? imageUpstream : null
    }
  }
}

// rerouteNode(upstream, typeName) -- a pass-through with ONE unnamed input
// at slot 0 (core Reroute's own shape).
function rerouteNode(upstream, id, typeName) {
  return {
    id,
    type: typeName || 'Reroute',
    inputs: [{ name: '', link: upstream ? 1 : null }],
    getInputNode(slot) {
      return slot === 0 ? upstream : null
    }
  }
}

// distributorNode(upstream, id) -- EPSDistributor's shape: ONE real `image`
// input regardless of how many of its own (irrelevant here) outputs exist.
function distributorNode(upstream, id) {
  return {
    id,
    comfyClass: 'EPSDistributor',
    inputs: [{ name: 'image', link: upstream ? 1 : null }],
    getInputNode(slot) {
      return slot === 0 ? upstream : null
    }
  }
}

// switcherNode(slots, togglesValue, id) -- EPSSwitcher's shape: `image_N`
// inputs (1-indexed) each wired to `slots[N-1]` (or unwired if that entry
// is null/undefined), plus a `toggles` widget carrying the JSON string.
function switcherNode(slots, togglesValue, id) {
  const inputs = slots.map((upstream, i) => ({
    name: `image_${i + 1}`,
    link: upstream ? 1 : null
  }))
  return {
    id,
    comfyClass: 'EPSSwitcher',
    inputs,
    widgets: togglesValue === undefined ? [] : [{ name: 'toggles', value: togglesValue }],
    getInputNode(slot) {
      return slots[slot] ?? null
    }
  }
}
"""

PROBE_JS = (
    """
import * as m from './extensions/comfyui-epsnodes/eps_image/resolution.js'

"""
    + FIXTURES_JS
    + """

function sizesOf(node) {
  return m.collectIncomingImageSizes(node)
}

const out = {
  exports: {
    hasCollect: typeof m.collectIncomingImageSizes === 'function',
    hasSummarize: typeof m.summarizeIncomingSizes === 'function',
    hasSourceLineForSummary: typeof m.sourceLineForSummary === 'function'
  },

  // ---- collectIncomingImageSizes: the walk itself ----

  unwired: sizesOf(entryNode(null)),
  directWall: sizesOf(entryNode(leaf(800, 600, 'a'))),
  throughOneReroute: sizesOf(entryNode(rerouteNode(leaf(640, 480, 'a'), 'r1'))),
  throughRgthreeReroute: sizesOf(
    entryNode(rerouteNode(leaf(640, 480, 'a'), 'r1', 'Reroute (rgthree)'))
  ),
  unknownClassIsAWall: sizesOf(entryNode(unknownWall('u1'))),
  emptyLeafContributesNothing: sizesOf(entryNode(emptyLeaf('a'))),

  switcherAllEnabledSameSize: sizesOf(
    entryNode(
      switcherNode([leaf(512, 512, 'a'), leaf(512, 512, 'b')], undefined, 'sw1')
    )
  ),
  switcherOneDisabledSlotExcluded: sizesOf(
    entryNode(
      switcherNode(
        [leaf(1024, 1024, 'a'), leaf(999, 999, 'b')],
        '{"image_2": false}',
        'sw1'
      )
    )
  ),
  switcherAbsentTogglesKeyMeansEnabled: sizesOf(
    entryNode(
      switcherNode([leaf(1024, 1024, 'a'), leaf(1024, 1024, 'b')], '{}', 'sw1')
    )
  ),
  switcherMalformedTogglesDegradesToAllEnabled: sizesOf(
    entryNode(
      switcherNode([leaf(300, 200, 'a'), leaf(300, 200, 'b')], 'not json', 'sw1')
    )
  ),
  switcherUnwiredSlotSkipped: sizesOf(
    entryNode(switcherNode([leaf(400, 300, 'a'), null], undefined, 'sw1'))
  ),
  switcherMixedSizes: sizesOf(
    entryNode(
      switcherNode([leaf(1024, 1024, 'a'), leaf(832, 1216, 'b')], undefined, 'sw1')
    )
  ),

  nestedSwitcherThenDistributor: sizesOf(
    entryNode(
      switcherNode(
        [distributorNode(leaf(640, 480, 'leaf1'), 'd1')],
        undefined,
        'sw1'
      )
    )
  ),

  nestedDistributorThenSwitcher: sizesOf(
    entryNode(
      distributorNode(
        switcherNode([leaf(200, 100, 'a'), leaf(200, 100, 'b')], undefined, 'sw1'),
        'd1'
      )
    )
  )
}

// ---- cycle guard: two switchers pointing at each other ----
{
  const swA = switcherNode([], undefined, 'A')
  const swB = switcherNode([swA], undefined, 'B')
  swA.getInputNode = (slot) => (slot === 0 ? swB : null)
  swA.inputs = [{ name: 'image_1', link: 1 }]
  const entry = entryNode(swA)
  let threw = null
  let result = null
  try {
    result = m.collectIncomingImageSizes(entry)
  } catch (error) {
    threw = String(error)
  }
  out.cycleGuard = { threw, result }
}

// ---- depth cap: a long reroute chain past MAX_INCOMING_WALK_DEPTH ----
{
  let upstream = leaf(1234, 5678, 'deep-leaf')
  for (let i = 0; i < 20; i++) {
    upstream = rerouteNode(upstream, `hop${i}`)
  }
  out.depthCap = sizesOf(entryNode(upstream))
}

// ---- a SHORT reroute chain (well within the cap) still resolves ----
{
  let upstream = leaf(1234, 5678, 'shallow-leaf')
  for (let i = 0; i < 3; i++) {
    upstream = rerouteNode(upstream, `shallow-hop${i}`)
  }
  out.shortChainResolves = sizesOf(entryNode(upstream))
}

// ---- summarizeIncomingSizes: pure decision table ----
out.summarize = {
  none: m.summarizeIncomingSizes([]),
  noneFromInvalidEntries: m.summarizeIncomingSizes([
    { width: 0, height: 500 }, { width: -5, height: 5 }, null, undefined
  ]),
  singleOne: m.summarizeIncomingSizes([{ width: 800, height: 600 }]),
  singleMany: m.summarizeIncomingSizes([
    { width: 800, height: 600 }, { width: 800, height: 600 }, { width: 800, height: 600 }
  ]),
  mixedTwo: m.summarizeIncomingSizes([
    { width: 1024, height: 1024 }, { width: 832, height: 1216 }
  ]),
  mixedDedupes: m.summarizeIncomingSizes([
    { width: 1024, height: 1024 }, { width: 832, height: 1216 }, { width: 1024, height: 1024 }
  ]),
  mixedIgnoresInvalidEntries: m.summarizeIncomingSizes([
    { width: 1024, height: 1024 }, { width: 0, height: 0 }, { width: 832, height: 1216 }
  ])
}

// ---- sourceLineForSummary ----
out.sourceLine = {
  none: m.sourceLineForSummary({ kind: 'none' }),
  single: m.sourceLineForSummary({ kind: 'single', width: 1920, height: 1080 }),
  mixed: m.sourceLineForSummary({
    kind: 'mixed',
    sizes: [{ width: 1024, height: 1024 }, { width: 832, height: 1216 }]
  })
}

process.stdout.write(JSON.stringify(out))
"""
)


@pytest.fixture(scope="module")
def walk_api(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Runs the probe against the REAL resolution.js in a served-layout tmp
    dir (mirrors the sibling M2/M3/M4 test files' identical fixture)."""
    layout = tmp_path_factory.mktemp("web_root")

    module_dir = layout / "extensions" / "comfyui-epsnodes" / "eps_image"
    module_dir.mkdir(parents=True)
    shutil.copyfile(RESOLUTION_JS, module_dir / "resolution.js")

    lora_dir = layout / "extensions" / "comfyui-epsnodes" / "lora_library"
    lora_dir.mkdir(parents=True)
    shutil.copyfile(API_JS, lora_dir / "api.js")
    shutil.copyfile(VERSION_JS, lora_dir / "version.js")

    scripts = layout / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "app.js").write_text("export const app = {}\n", encoding="utf-8")
    (scripts / "api.js").write_text("export const api = { fetchApi: () => {} }\n", encoding="utf-8")

    probe = layout / "probe.mjs"
    probe.write_text(PROBE_JS, encoding="utf-8")

    result = subprocess.run(
        [NODE, str(probe)], capture_output=True, text=True, timeout=60, cwd=layout
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def source() -> str:
    return RESOLUTION_JS.read_text(encoding="utf-8")


def _function_body(source_text: str, signature: str) -> str:
    start_match = re.search(re.escape(f"function {signature} {{") + r"\n", source_text)
    assert start_match, f"function {signature} {{ not found"
    start = start_match.end()
    end_match = re.search(r"\n\}\n", source_text[start:])
    assert end_match, f"end of {signature} not found"
    return source_text[start : start + end_match.start()]


def _to_size_set(sizes: list[dict]) -> set[tuple[int, int]]:
    return {(s["width"], s["height"]) for s in sizes}


# --------------------------------------------------------------------- exports


def test_walk_helpers_are_exported(walk_api: dict) -> None:
    assert walk_api["exports"] == {
        "hasCollect": True,
        "hasSummarize": True,
        "hasSourceLineForSummary": True,
    }


# ---------------------------------------------------------- the walk itself


class TestCollectIncomingImageSizes:
    def test_unwired_is_empty(self, walk_api: dict) -> None:
        assert walk_api["unwired"] == []

    def test_direct_wall_is_the_baseline_one_hop_case(self, walk_api: dict) -> None:
        assert _to_size_set(walk_api["directWall"]) == {(800, 600)}

    def test_walks_through_a_core_reroute(self, walk_api: dict) -> None:
        assert _to_size_set(walk_api["throughOneReroute"]) == {(640, 480)}

    def test_walks_through_an_rgthree_reroute(self, walk_api: dict) -> None:
        assert _to_size_set(walk_api["throughRgthreeReroute"]) == {(640, 480)}

    def test_unknown_class_is_a_wall_not_a_guessed_passthrough(self, walk_api: dict) -> None:
        # unknownWall has no imgs, so it contributes nothing -- but the
        # point is it must NOT be treated as a pass-through and walked
        # through (there is nothing further upstream of it in the fixture,
        # so any attempt to do so would also just find nothing; this test
        # is here as the code-path pin for "treat an unknown class as a
        # wall, never guess").
        assert walk_api["unknownClassIsAWall"] == []

    def test_a_wall_with_nothing_decoded_yet_contributes_nothing(self, walk_api: dict) -> None:
        assert walk_api["emptyLeafContributesNothing"] == []

    def test_switcher_all_enabled_same_size(self, walk_api: dict) -> None:
        assert _to_size_set(walk_api["switcherAllEnabledSameSize"]) == {(512, 512)}
        assert len(walk_api["switcherAllEnabledSameSize"]) == 2  # both slots counted

    def test_switcher_one_disabled_slot_is_excluded(self, walk_api: dict) -> None:
        assert _to_size_set(walk_api["switcherOneDisabledSlotExcluded"]) == {(1024, 1024)}
        assert len(walk_api["switcherOneDisabledSlotExcluded"]) == 1

    def test_switcher_absent_toggles_key_means_enabled(self, walk_api: dict) -> None:
        assert len(walk_api["switcherAbsentTogglesKeyMeansEnabled"]) == 2

    def test_switcher_malformed_toggles_degrades_to_all_enabled(self, walk_api: dict) -> None:
        assert len(walk_api["switcherMalformedTogglesDegradesToAllEnabled"]) == 2

    def test_switcher_unwired_slot_is_skipped(self, walk_api: dict) -> None:
        assert _to_size_set(walk_api["switcherUnwiredSlotSkipped"]) == {(400, 300)}
        assert len(walk_api["switcherUnwiredSlotSkipped"]) == 1

    def test_switcher_mixed_sizes_both_collected(self, walk_api: dict) -> None:
        assert _to_size_set(walk_api["switcherMixedSizes"]) == {(1024, 1024), (832, 1216)}

    def test_nested_switcher_then_distributor(self, walk_api: dict) -> None:
        assert _to_size_set(walk_api["nestedSwitcherThenDistributor"]) == {(640, 480)}

    def test_nested_distributor_then_switcher(self, walk_api: dict) -> None:
        assert _to_size_set(walk_api["nestedDistributorThenSwitcher"]) == {(200, 100)}
        assert len(walk_api["nestedDistributorThenSwitcher"]) == 2

    def test_cycle_guard_terminates_without_throwing(self, walk_api: dict) -> None:
        guard = walk_api["cycleGuard"]
        assert guard["threw"] is None
        assert guard["result"] == []  # neither switcher in the cycle is a real wall

    def test_depth_cap_stops_before_reaching_a_too_deep_leaf(self, walk_api: dict) -> None:
        assert walk_api["depthCap"] == []

    def test_a_chain_within_the_depth_cap_still_resolves(self, walk_api: dict) -> None:
        assert _to_size_set(walk_api["shortChainResolves"]) == {(1234, 5678)}


# ------------------------------------------------------ summarizeIncomingSizes


class TestSummarizeIncomingSizes:
    def test_empty_is_none(self, walk_api: dict) -> None:
        assert walk_api["summarize"]["none"] == {"kind": "none"}

    def test_only_invalid_entries_is_none(self, walk_api: dict) -> None:
        assert walk_api["summarize"]["noneFromInvalidEntries"] == {"kind": "none"}

    def test_one_source_is_single(self, walk_api: dict) -> None:
        assert walk_api["summarize"]["singleOne"] == {
            "kind": "single",
            "width": 800,
            "height": 600,
        }

    def test_many_agreeing_sources_is_single(self, walk_api: dict) -> None:
        assert walk_api["summarize"]["singleMany"] == {
            "kind": "single",
            "width": 800,
            "height": 600,
        }

    def test_two_differing_sources_is_mixed(self, walk_api: dict) -> None:
        result = walk_api["summarize"]["mixedTwo"]
        assert result["kind"] == "mixed"
        assert result["sizes"] == [
            {"width": 1024, "height": 1024},
            {"width": 832, "height": 1216},
        ]

    def test_mixed_dedupes_repeated_distinct_sizes(self, walk_api: dict) -> None:
        result = walk_api["summarize"]["mixedDedupes"]
        assert result["kind"] == "mixed"
        assert result["sizes"] == [
            {"width": 1024, "height": 1024},
            {"width": 832, "height": 1216},
        ]

    def test_mixed_ignores_invalid_entries(self, walk_api: dict) -> None:
        result = walk_api["summarize"]["mixedIgnoresInvalidEntries"]
        assert result["kind"] == "mixed"
        assert result["sizes"] == [
            {"width": 1024, "height": 1024},
            {"width": 832, "height": 1216},
        ]


# -------------------------------------------------------- sourceLineForSummary


class TestSourceLineForSummary:
    def test_none_draws_nothing(self, walk_api: dict) -> None:
        assert walk_api["sourceLine"]["none"] is None

    def test_single_matches_the_ordinary_readout_shape(self, walk_api: dict) -> None:
        assert walk_api["sourceLine"]["single"] == {
            "dims": "1920 x 1080",
            "mp": "2.1 MP",
            "aspect": "16:9",
        }

    def test_mixed_is_one_combined_string_no_aspect_or_mp_fields(self, walk_api: dict) -> None:
        assert walk_api["sourceLine"]["mixed"] == {"mixed": "mixed: 1024x1024, 832x1216"}


# --------------------------------------------------- copy-from-image refusal
#
# attachCopyFromImage only runs against a real litegraph node/DOM, so
# (matching test_resolution_presets_js.py's/test_resolution_ratio_js.py's
# identical convention for that class of code) this is pinned via
# source-text assertions rather than a probe call.


class TestCopyFromImageRefusesOnMixed:
    def test_mixed_kind_refuses_before_any_write(self, source: str) -> None:
        body = _function_body(source, "attachCopyFromImage(node)")
        mixed_at = body.index("summary.kind === 'mixed'")
        write_at = body.index("writeSize(node, size.width, size.height)")
        assert mixed_at < write_at
        # the refusal branch returns before reaching the writer.
        mixed_branch = body[mixed_at : body.index("if (summary.kind !== 'single')")]
        assert "return" in mixed_branch
        assert "writeSize" not in mixed_branch

    def test_mixed_message_names_every_reported_size(self, source: str) -> None:
        body = _function_body(source, "attachCopyFromImage(node)")
        assert "summary.sizes.map" in body
        assert "different sizes" in body
