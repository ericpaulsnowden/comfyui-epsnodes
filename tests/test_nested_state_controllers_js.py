"""v1.2.0 nested reach (owner ask 2026-10-03, FORMAT.md §7.10): the
Universal State Controller's capture / match / apply over a workflow whose
state-bearing nodes sit inside subgraphs.

Drives the REAL ``discoverStateNodes`` / ``buildLiveIndex`` /
``buildStatePayload`` / ``applyPlan`` (``universal_controller.js``) and the real
``api.js`` walkers against the shared fake nested litegraph. The write loop
itself (``_writeApplyPlan``) is a method of a registered LGraphNode and is
covered by source pins in ``test_universal_controller_js.py``; what is checked
here is everything that decides WHICH nodes are read and written.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from nested_layout import NODE, build_layout, run_probe

TESTS = Path(__file__).resolve().parent
PROBE_JS = (TESTS / "nested_state_probe.mjs").read_text(encoding="utf-8")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")


@pytest.fixture(scope="module")
def state(tmp_path_factory: pytest.TempPathFactory) -> dict:
    root = build_layout(
        tmp_path_factory.mktemp("usc"), lora_library=("universal_controller.js", "search.js")
    )
    return run_probe(root, PROBE_JS)


def test_discovery_walks_into_subgraphs_with_execution_shaped_path_ids(state: dict) -> None:
    """Root node 1; "3:2" and "3:4" inside SubgraphNode 3; "6:2" inside 6 --
    inner id 2 appears twice and only the path keeps the two apart."""
    assert state["discovered"] == [
        "1|EPSResolution|Size",
        "3:2|EPSResolution|Size",
        "3:4|EPSSwitcher|Look picker",
        "6:2|EPSResolution|Upscale size",
    ]


def test_capture_stores_nested_nodes_by_path_id(state: dict) -> None:
    assert state["captured"] == [
        ["1", "EPSResolution", "Size", 512],
        ["3:2", "EPSResolution", "Size", 768],
        ["3:4", "EPSSwitcher", "Look picker", None],
        ["6:2", "EPSResolution", "Upscale size", 2048],
    ]
    assert state["warnings"] == []


def test_apply_on_the_same_canvas_matches_every_node_by_exact_path_id(state: dict) -> None:
    assert state["sameCanvas"] == [
        ["1", "id", '[{"name":"width","value":513}]'],
        ["3:2", "id", '[{"name":"width","value":769}]'],
        ["3:4", "id", '[{"name":"toggles","value":"{\\"image_1\\":false}"}]'],
        ["6:2", "id", '[{"name":"width","value":2049}]'],
    ]
    assert state["sameCanvasMissing"] == 0


def test_cross_machine_apply_survives_subgraph_renumbering(state: dict) -> None:
    """Another machine rebuilt the workflow: SubgraphNode 3 became 8 and 6
    became 9, so no stored id matches. Unique class+title pairs resolve by
    title; the two "Size" Resolutions (root "1" matches by id; "3:2" -> "8:2"
    is the only unclaimed "Size" left) resolve too -- nothing is missing."""
    by_saved = {row[0]: row for row in state["renumbered"]}
    assert by_saved["1"][1:] == ["1", "id"]
    assert by_saved["3:2"][1:] == ["8:2", "title"]
    assert by_saved["3:4"][1:] == ["8:4", "title"]
    assert by_saved["6:2"][1:] == ["9:2", "title"]
    assert state["renumberedMissing"] == 0


def test_exclusions_key_by_the_live_path_id(state: dict) -> None:
    """Excluding "3:2" skips exactly that nested node; the root Resolution
    with the same title and the other subgraph's "6:2" still apply."""
    assert state["excluded"] == [["3:2", "excluded"]]
    assert state["excludedStillMatched"] == ["1", "3:4", "6:2"]
    assert state["toggleAfter"]["nodes"] == {"3:2": False}


def test_a_shared_definition_is_listed_once_per_instance_path(state: dict) -> None:
    """Two SubgraphNodes instantiating one definition share the inner node
    OBJECT, so it is discovered under both "3:2" and "4:2". Capture records
    identical widgets under each and apply writes idempotently -- harmless,
    and deliberately not collapsed (a state saved elsewhere may name either)."""
    assert state["sharedPaths"] == ["3:2", "4:2"]
