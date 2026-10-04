"""v1.2.0 nested reach (FORMAT.md §7.10) -- the shared boundary-crossing
helpers added to ``web/lora_library/api.js``: ``locateByPathId`` /
``locationsOfNode`` / ``describePath`` / ``resolveInputSources`` /
``resolveOutputTargets`` / ``watchGraphHooks`` / ``watchAllGraphs``.

Executed for real under Node against the shared fake nested litegraph
(``tests/nested_graph.mjs``), which models the 1.52.7 subgraph shapes read
from the frontend source: SubgraphNode ``.subgraph``, index-aligned
inputs/outputs, boundary links with origin id -10 / target id -20 that are NOT
real nodes, and one definition shared by several SubgraphNode instances.

What this cannot cover (the rig must): the real LGraph/LLink classes, a real
``graphToPrompt``, and core's own hook restore on subgraph enter/exit.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from nested_layout import NODE, build_layout, run_probe

TESTS = Path(__file__).resolve().parent
# The probes are plain .mjs files (not strings in this module) so their long
# JS lines stay out of the Python linter and read as the JS they are.
PROBE_JS = (TESTS / "nested_reach_probe.mjs").read_text(encoding="utf-8")
WATCH_PROBE_JS = (TESTS / "nested_watch_probe.mjs").read_text(encoding="utf-8")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")




@pytest.fixture(scope="module")
def reach(tmp_path_factory: pytest.TempPathFactory) -> dict:
    return run_probe(build_layout(tmp_path_factory.mktemp("reach")), PROBE_JS)


@pytest.fixture(scope="module")
def watch(tmp_path_factory: pytest.TempPathFactory) -> dict:
    return run_probe(build_layout(tmp_path_factory.mktemp("watch")), WATCH_PROBE_JS)


def test_native_get_input_node_stops_at_the_boundary_but_the_resolvers_do_not(reach: dict) -> None:
    """The reason these helpers exist: litegraph's getInputNode returns null
    at a subgraph input node and the SubgraphNode itself at an output."""
    s = reach["simple"]
    assert s["nativeInner"] is None
    assert s["nativeC"].startswith("uuid-")  # the SubgraphNode, not the node that feeds it
    assert s["innerUp"] == ["1#0"]  # inner's input really comes from root node 1
    assert s["cDown"] == ["10:2#0"]  # C's input really comes from the node INSIDE the subgraph
    assert s["cDownType"] == "Inner"
    assert s["aOut"] == ["10:2#0"]  # A feeds inner (slot 0 of the inner node), via S
    assert s["aOutType"] == "Inner"
    assert s["innerOut"] == ["3#0"]  # inner feeds root node 3, out through S
    assert s["hopsOfAOut"] == 2  # root link + inner boundary link


def test_path_ids_and_location_of_nodes(reach: dict) -> None:
    s = reach["simple"]
    assert s["pathIds"] == {"A": ["1"], "inner": ["10:2"], "S": ["10"]}
    assert s["located"] == {"type": "Inner", "prefix": "10", "isSub": True}
    assert s["locatedRoot"] == {"type": "Sink", "prefix": "", "isRoot": True}
    assert s["locatedStale"] is None and s["locatedJunk"] is None


def test_two_levels_deep_both_directions(reach: dict) -> None:
    d = reach["deep"]
    assert d["leafUp"] == ["1#0"]
    assert d["zDown"] == ["5:7:2#0"]
    assert d["aOut"] == ["5:7:2#0"]
    assert d["leafOut"] == ["9#0"]
    assert d["leafPath"] == ["5:7:2"]


def test_describe_path_labels_nested_nodes_by_their_subgraph(reach: dict) -> None:
    """'Subgraph name > Node title' so two same-titled nodes read apart; a
    root node is just its title; a stale path labels as itself."""
    d = reach["deep"]
    assert d["label"]["text"] == "Outer \u203a Inner \u203a Leaf"  # the separator is U+203A
    assert d["label"]["trail"] == ["Outer", "Inner"] and d["label"]["title"] == "Leaf"
    assert d["labelRoot"]["text"] == "Src" and d["labelRoot"]["trail"] == []
    assert d["labelStale"] == {"title": "5:99", "trail": [], "text": "5:99"}


def test_dangling_boundaries_resolve_to_nothing_like_the_prompt_flattening(reach: dict) -> None:
    d = reach["dangling"]
    assert d == {"innerUp": 0, "cDown": 0, "innerOut": 0, "noSuchSlot": 0}


def test_a_shared_definition_fans_out_up_and_stays_specific_down(reach: dict) -> None:
    """Two SubgraphNodes instantiating ONE definition share the inner node
    object. Walking UP out of it must report BOTH instances' sources (never
    one guess); walking DOWN from a specific instance's output is exact."""
    s = reach["shared"]
    assert s["innerPaths"] == ["10:20", "11:20"]
    assert s["innerUp"] == ["1#0", "2#0"]
    assert s["innerOut"] == ["3#0", "4#0"]
    assert s["c1Down"] == ["10:20"] and s["c2Down"] == ["11:20"]
    assert s["walked"].count("10:20") == 1 and s["walked"].count("11:20") == 1


def test_pass_through_subgraph_and_cycles_terminate(reach: dict) -> None:
    assert reach["passthrough"] == {"cDown": ["1#0"], "aOut": ["3#0"]}
    # The self-feeding SubgraphNode terminates at the hop cap; its output still
    # genuinely reaches the one real consumer (found once, deduplicated).
    assert reach["cycle"] == {"src": 0, "tgt": 1}


def test_stop_at_subgraph_input_reports_the_subgraph_node_itself(reach: dict) -> None:
    stop = reach["stop"]
    assert stop["stopped"] == [["10", True, True]]
    assert stop["descended"] == [["10:2", False, "Inner"]]


def test_graph_link_tolerates_every_link_table_shape(reach: dict) -> None:
    g = reach["graphLink"]
    assert g == {
        "map": True,
        "object": True,
        "array": True,
        "proxy": True,
        "stringKey": True,
        "stringIdOnMap": True,
        "missing": None,
        "nullId": None,
        "noGraph": None,
    }


# ------------------------------------------------------------ watchGraphHooks


def test_watch_fires_after_the_original_hook(watch: dict) -> None:
    assert watch["fire"]["seen"] == [[True, "onNodeAdded"], [True, "onNodeRemoved"]]
    assert watch["fire"]["originalCalls"] == 1


def test_watch_is_idempotent(watch: dict) -> None:
    assert watch["idempotent"] == {"sameWrapper": True, "events": 1}


def test_watch_survives_core_restoring_the_hook(watch: dict) -> None:
    """The one-shot-flag bug (v0.68.1): core restores graph.onNodeAdded on a
    subgraph enter/exit, our wrapper vanishes, and a boolean flag refused to
    re-install. The stored-and-re-verified pattern re-wraps on the next pass."""
    assert watch["restored"] == {"deafEvents": 0, "afterReverify": 1}


def test_watch_adopts_a_surviving_wrapper_instead_of_stacking(watch: dict) -> None:
    assert watch["adopt"] == {"stillOurs": True, "events": 1}


def test_watch_errors_never_escape_into_the_graph(watch: dict) -> None:
    assert watch["throws"] == {"threw": False, "reached": 1, "added": 1}


def test_two_owner_keys_do_not_clobber_each_other(watch: dict) -> None:
    assert watch["twoOwners"] == {"a": 1, "b": 1}


def test_watch_all_graphs_reaches_subgraph_hooks_and_late_subgraphs(watch: dict) -> None:
    assert watch["all"][:2] == ["sub", "root"]
    assert watch["all"][-1] == "late"


def test_watch_reports_whether_it_had_to_install_anything(watch: dict) -> None:
    """The return value lets a poll-driven caller repaint once after it had to
    put a hook back (an event may have fired into the gap)."""
    assert watch["returns"] == {
        "first": True,
        "second": False,
        "third": True,
        "none": False,
        "all1": True,
        "all2": False,
        "all3": True,
    }


def test_sibling_features_never_stack_wrapper_layers_unboundedly(watch: dict) -> None:
    """Controller, picker, Apply Set and the Run Multiplier all watch the same
    hooks and each re-verifies on its own schedule. Every wrapper inherits the
    owner-key set of the wrapper beneath it, so a sibling's top function that
    still contains ours is ADOPTED, not wrapped again: twelve passes by three
    features leave each firing exactly once per event and the original hook
    called exactly once. Only a core wrapper on top (no key set) costs one
    extra layer per feature, and core's restore cuts it off again."""
    s = watch["siblings"]
    assert s["afterQuiet"] == {"a": 1, "b": 1, "c": 1, "originalCalls": 1}
    # core's wrapper on top: each feature re-wrapped it ONCE (not twelve times),
    # so each fires at most twice per event (coalesced by every real caller)
    assert all(1 <= s["afterCoreWrap"][k] <= 2 for k in ("a", "b", "c"))
    assert s["afterCoreWrap"]["originalCalls"] == 1
    # after core restores its captured value the original chain is adopted and
    # firing is back to exactly once
    assert s["afterRestore"] == {"a": 1, "b": 1, "c": 1, "originalCalls": 1}
    assert s["restoredIsTop"] is True


def test_small_path_and_root_helpers(reach: dict) -> None:
    h = reach["helpers"]
    assert h["join"] == ["3", "3:2", "3:5:2"]
    assert h["parent"] == ["", "3", "3:5"]
    # liveRootOf: O(1) and only for nodes under the app's live root (a fake or
    # another tab's node reads null, which sends callers down the native path)
    assert h["liveRoot"] == {"root": True, "nested": True, "stranger": None, "otherTab": None}
    assert h["liveRootWithoutApp"] is None
    assert h["rootOf"] == [True, True, None]
    # segment-numeric ordering: 2 < 10, root 3 < nested 3:2, 3:2 < 3:10, 10 > 9
    assert h["compare"] == [-1, -1, -1, 1]


def test_resolve_sources_at_keeps_each_hop_on_its_instance_lane(reach: dict) -> None:
    """The bug resolution.js's and frame_saver.js's tests found: after the
    first hop, re-resolving a node OBJECT inside a shared definition fans out
    to every instance's outer source (a walk that came in through instance A
    would see instance B's upstream: a false "mixed"). `resolveSourcesAt`
    resolves a previous hop's result from the place it was found."""
    lanes = reach["lanes"]
    assert lanes["entry"] == ["10:20#0", "11:20#0"]
    assert lanes["lane10"] == ["1#0"]
    assert lanes["lane11"] == ["2#0"]
    assert lanes["byObject"] == ["1#0", "2#0"]  # the wrong answer for hop 2+
    assert lanes["noItem"] == 0 and lanes["badSlot"] == 0
