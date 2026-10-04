"""EPS Frame Saver's wired-video walk across SUBGRAPH boundaries (v1.2.0
nested reach, owner ask 2026-10-03: "Make sure all of the nodes that can
control other nodes also looks into nested nodes"; FORMAT.md §6.7 / §7.10).

``resolveWiredVideo(node)`` follows the saver's ``video`` input through
reroutes to a core ``LoadVideo`` (statically knowable ``file`` widget) else
``opaque``. It used to hop with ``graph.getNodeById(link.origin_id)``, which
stops at a SubgraphNode and finds nothing at a subgraph input node -- a
LoadVideo inside a subgraph, or one outside feeding a subgraph input the saver
reads, read "opaque" and never previewed. Every hop (the saver's own input AND
each reroute) now crosses boundaries through ``lora_library/api.js``'s
resolvers.

Executed for real under Node (``tests/nested_frame_saver_probe.mjs`` over the
shared fake nested litegraph ``tests/nested_graph.mjs``, which models the
1.52.7 subgraph shapes). What this cannot cover (the rig must): the real
LGraph/LLink classes, a promoted widget's real host-side store, and the real
``<video>`` preview that the resolved ``input_ref`` then drives.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from nested_layout import NODE, build_layout, run_probe

TESTS = Path(__file__).resolve().parent
REPO_ROOT = TESTS.parent
FRAME_SAVER_JS = REPO_ROOT / "web" / "eps_image" / "frame_saver.js"
RESOLUTION_JS = REPO_ROOT / "web" / "eps_image" / "resolution.js"
PROBE_JS = (TESTS / "nested_frame_saver_probe.mjs").read_text(encoding="utf-8")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

SEP = "\u203a"  # describePath's "Subgraph name > Node title" separator


def _ref(ref: str, title: str = "Load Video") -> dict:
    return {"kind": "input_ref", "ref": ref, "title": title}


def _opaque(title: str) -> dict:
    return {"kind": "opaque", "title": title}


@pytest.fixture(scope="module")
def nested(tmp_path_factory: pytest.TempPathFactory) -> dict:
    layout = build_layout(
        tmp_path_factory.mktemp("nested_frame_saver"), eps_image=("frame_saver.js",)
    )
    return run_probe(layout, PROBE_JS)


@pytest.fixture(scope="module")
def source() -> str:
    return FRAME_SAVER_JS.read_text(encoding="utf-8")


def _function_body(source_text: str, signature: str) -> str:
    start_match = re.search(re.escape(f"function {signature} {{") + r"\n", source_text)
    assert start_match, f"function {signature} {{ not found"
    start = start_match.end()
    end_match = re.search(r"\n\}\n", source_text[start:])
    assert end_match, f"end of {signature} not found"
    return source_text[start : start + end_match.start()]


class TestFlatGraphIsUnchanged:
    """With a LIVE root the flat answers are byte-identical to before."""

    def test_exported_for_tests(self, nested: dict) -> None:
        assert nested["exported"] is True

    def test_load_video_directly_wired(self, nested: dict) -> None:
        assert nested["flatLoadVideo"] == _ref("clip.mp4")

    def test_through_core_and_rgthree_reroutes(self, nested: dict) -> None:
        assert nested["flatThroughReroutes"] == _ref("clip.mp4")

    def test_any_other_source_is_opaque(self, nested: dict) -> None:
        assert nested["flatOpaque"] == _opaque("Video Maker")

    def test_load_video_with_no_file_is_opaque(self, nested: dict) -> None:
        assert nested["flatLoadVideoNoFile"] == _opaque("Load Video")

    def test_unwired_and_no_video_input_are_null(self, nested: dict) -> None:
        assert nested["flatUnwired"] is None
        assert nested["noVideoInput"] is None

    def test_a_primitive_driven_file_socket_stays_knowable_when_flat(self, nested: dict) -> None:
        assert nested["flatPrimitiveDrivenFile"] == _ref("prim.mp4")


class TestAcrossSubgraphBoundaries:
    def test_load_video_inside_a_subgraph(self, nested: dict) -> None:
        got = nested["loadVideoInside"]
        # The premise: the old hop saw the SubgraphNode, not the LoadVideo.
        assert got["native"].startswith("uuid-")
        assert got["resolved"] == _ref("inner.mp4", f"Wrapper {SEP} Load Video")

    def test_load_video_outside_feeding_a_subgraph_input(self, nested: dict) -> None:
        got = nested["outsideThroughInput"]
        assert got["native"] is None  # getNodeById(-10): nothing
        assert got["resolved"] == _ref("outer.mp4")  # a root node keeps its bare title

    def test_reroute_pairs_straddling_the_boundary(self, nested: dict) -> None:
        assert nested["rerouteAcrossIn"] == _ref("x.mp4")
        assert nested["rerouteAcrossOut"] == _ref("y.mp4", f"Wrapper {SEP} Load Video")

    def test_two_levels_of_nesting(self, nested: dict) -> None:
        assert nested["twoLevelsIn"] == _ref("deep.mp4")
        assert nested["twoLevelsOut"] == _ref("deepout.mp4", f"Outer {SEP} Inner {SEP} Load Video")

    def test_a_subgraph_that_only_forwards_its_input_is_transparent(self, nested: dict) -> None:
        assert nested["forwardingSubgraph"] == _ref("fwd.mp4")

    def test_an_opaque_source_across_the_boundary_names_where_it_lives(self, nested: dict) -> None:
        assert nested["opaqueOutsideIn"] == _opaque("Video Maker")
        assert nested["opaqueInsideOut"] == _opaque(f"Wrapper {SEP} Video Maker")

    def test_dangling_boundaries_read_as_unwired(self, nested: dict) -> None:
        assert nested["danglingInput"] is None
        assert nested["danglingOutput"] is None

    def test_a_promoted_file_widget_is_opaque_never_the_stale_interior_value(
        self, nested: dict
    ) -> None:
        """Core keeps a promoted widget's value in a host-side store COPY, so
        the interior widget's value is only what it was when promoted;
        previewing that file would show a video the run does not use."""
        assert nested["promotedFileWidget"] == _opaque(f"Wrapper {SEP} Load Video")


class TestSharedDefinitionAmbiguity:
    """One definition, two SubgraphNode instances: sources that disagree are
    opaque (never a pick); sources that agree are that answer."""

    def test_instances_fed_different_videos_are_opaque(self, nested: dict) -> None:
        assert nested["sharedDifferentVideos"] == _opaque("(several sources)")

    def test_instances_fed_the_same_file_preview_it(self, nested: dict) -> None:
        got = nested["sharedSameFileTwoNodes"]
        assert (got["kind"], got["ref"]) == ("input_ref", "same.mp4")

    def test_instances_fed_one_and_the_same_source(self, nested: dict) -> None:
        assert nested["sharedSameSingleSource"] == _ref("one.mp4")

    def test_a_known_and_an_opaque_source_disagree(self, nested: dict) -> None:
        assert nested["sharedKnownVersusOpaque"] == _opaque("(several sources)")

    def test_two_opaque_sources_keep_a_shared_title_else_say_several(self, nested: dict) -> None:
        assert nested["sharedTwoOpaqueSameTitle"] == _opaque("Gen")
        assert nested["sharedTwoOpaqueDifferentTitles"] == _opaque("(several sources)")

    def test_each_hop_stays_on_its_own_instance_lane(self, nested: dict) -> None:
        """A saver fed through instance A must see ONLY A's video even though
        the reroute between them is the same object under both instances'
        paths -- re-resolving it by node object would fan out to both and
        read every shared definition as ambiguous."""
        lanes = nested["sharedLaneExact"]
        assert lanes["viaA"] == _ref("a.mp4")
        assert lanes["viaB"] == _ref("b.mp4")


class TestLoopGuardAndFallback:
    def test_the_32_hop_guard_is_exact_in_both_paths(self, nested: dict) -> None:
        # 31 reroutes still resolve, 32 hit the guard -- live root AND legacy,
        # byte-identical to the pre-v1.2.0 loop.
        for key in ("chain31Live", "chain31Legacy"):
            assert nested[key] == _ref("far.mp4")
        for key in ("chain32Live", "chain32Legacy"):
            assert nested[key] == _opaque("(reroute loop)")

    def test_a_real_reroute_cycle_ends_as_a_loop(self, nested: dict) -> None:
        assert nested["rerouteLoop"] == _opaque("(reroute loop)")

    def test_no_live_root_uses_the_single_graph_read_exactly_as_before(self, nested: dict) -> None:
        assert nested["legacyFlat"] == _ref("legacy.mp4")
        assert nested["legacyNestedBlind"] is None  # blind at the boundary, as it always was

    def test_a_node_outside_the_live_root_with_a_plain_object_link_table(
        self, nested: dict
    ) -> None:
        assert nested["bareFakeObjectLinks"] == _ref("plain.mp4")


class TestSourcePins:
    """Pins for the parts only a real node/DOM runs (this file's convention)."""

    def test_the_old_blind_hop_is_confined_to_the_fallback(self, source: str) -> None:
        # `getNodeById(link.origin_id)` is the boundary-blind read; it must
        # live only inside the single-graph fallback, not beside the walk.
        assert len(re.findall(r"getNodeById\?\.\(link\.origin_id\)", source)) == 1
        assert "getNodeById?.(link.origin_id)" in _function_body(
            source, "nativeUpstreamAt(node, slotIndex)"
        )

    def test_every_hop_goes_through_the_one_primitive(self, source: str) -> None:
        body = _function_body(source, "resolveWiredVideo(node)")
        assert "upstreamSources(root, { node }, slot)" in body  # the saver's own input
        assert "upstreamSources(root, source, 0)" in body  # each reroute hop
        hop = _function_body(source, "upstreamSources(root, item, slotIndex)")
        # the lane-aware resolution is the SHARED api.js helper (folded in
        # from this file's and resolution.js's hand-kept twins, v1.2.0)
        assert "resolveSourcesAt(root, item, slotIndex)" in hop
        assert "if (root && isSubgraphNode(native)) return []" in hop

    def test_ambiguity_is_opaque_and_the_guard_is_kept(self, source: str) -> None:
        body = _function_body(source, "resolveWiredVideo(node)")
        assert "hops < 32" in body
        assert "'(reroute loop)'" in body
        assert "settleWiredAnswers(answers)" in body
        settle = _function_body(source, "settleWiredAnswers(answers)")
        assert "'(several sources)'" in settle

    def test_the_promoted_widget_guard_reads_the_subgraph_input_id(self, source: str) -> None:
        assert "SUBGRAPH_INPUT_ID" in _function_body(source, "fileDrivenFromSubgraphInput(source)")
        assert "!fileDrivenFromSubgraphInput(source)" in _function_body(
            source, "resolveWiredVideo(node)"
        )

    def test_the_sole_selected_check_compares_node_identity_in_the_viewed_graph(
        self, source: str
    ) -> None:
        """Classification pin (not a behavior change): `app.canvas.selected_nodes`
        holds the node OBJECTS of the graph on screen, so inside a subgraph the
        saver is found by identity just like at the root -- no id comparison
        that nested ids could collide on."""
        body = _function_body(source, "isSoleSelectedNode(node)")
        assert "nodes.length === 1 && nodes[0] === node" in body

    def test_the_walk_helpers_are_shared_not_hand_kept_twins(self, source: str) -> None:
        """`liveRootOf` and the lane-aware hop (`resolveSourcesAt`) live in
        api.js and are IMPORTED by both this file and resolution.js. They were
        hand-kept twins pinned equal; a fix to one that missed the other is this
        pack's recurring bug class, so the lead folded them into the shared
        module and this pin fails if a local copy ever comes back."""
        resolution = RESOLUTION_JS.read_text(encoding="utf-8")
        for text in (source, resolution):
            assert "function liveRootFor" not in text
            assert "function parentPrefixOf" not in text
            api_import = text.split("from '../lora_library/api.js'", 1)[0].rsplit("import {", 1)[1]
            assert "liveRootOf" in api_import and "resolveSourcesAt" in api_import
