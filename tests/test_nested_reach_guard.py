"""v1.2.0 nested reach -- the REGRESSION GUARD (owner ask 2026-10-03: "make
sure all of the nodes that can control other nodes also looks into nested
nodes"; FORMAT.md §7.10).

This pack has repeatedly shipped fixes that missed hand-copied siblings
(``docs/ROADMAP-shared-panel-code.md``), and "root-only" is exactly that bug
class: a node that finds OTHER nodes with ``app.graph._nodes`` or watches only
its own graph works at the top level and silently does nothing inside a
subgraph. The behavioural tests live next to each node; THIS file is the
cross-cutting tripwire so the next module someone writes cannot reintroduce it
without a conscious, documented exception.

What it pins (source scans over ``web/`` -- the only way to cover code that
runs against a real litegraph + DOM, the pack's pin-test convention):

* no module reads the live node list of the ROOT graph directly
  (``app.graph._nodes`` / ``.nodes``) or of a single ``graph`` -- the shared
  walkers in ``api.js`` are the only way to enumerate nodes;
* no module installs graph hooks by hand (``graph.onNodeAdded = ...``,
  ``graph[hook] = ...``) or with a once-per-graph boolean flag -- core RESTORES
  graph hooks on every subgraph enter/exit and Nodes 2.0 toggle, so every
  watcher goes through ``api.watchGraphHooks`` / ``api.watchAllGraphs``;
* every node that CONTROLS or READS other nodes reaches them through the shared
  helpers (a positive list, so deleting the import is also caught).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB = REPO_ROOT / "web"

#: Comment lines are documentation, not code.
_COMMENT_RE = re.compile(r"^\s*(\*|//|/\*)")


def _code_lines(path: Path) -> list[tuple[int, str]]:
    return [
        (number, line)
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if not _COMMENT_RE.match(line)
    ]


def _web_modules() -> list[Path]:
    return sorted(p for p in WEB.rglob("*.js") if p.name != "api.js")


#: Reads that enumerate nodes of ONE graph. ``api.js`` owns the traversal.
ROOT_ONLY_READ_RE = re.compile(r"app\??\.graph\??\.(?:_nodes|nodes)\b|\bgraph\??\._nodes\b")

#: DELIBERATE exceptions, each with the reason it is not a nested-reach gap.
ROOT_ONLY_READ_ALLOWED = {
    # switcher.js `graphColumnBounds`: aligns the toggle-checkbox COLUMN of the
    # switchers on ONE canvas. A subgraph and its parent are never drawn at the
    # same time, so aligning across graphs would be meaningless ("subgraphs and
    # other tabs stay independent" -- the v0.59.x owner spec, §6.4). Cosmetic,
    # not a node that controls or reads another node.
    ("web/eps_image/switcher.js", "const siblings = graph?._nodes"),
}


def test_no_module_enumerates_a_single_graphs_nodes_directly() -> None:
    offenders = []
    for path in _web_modules():
        rel = path.relative_to(REPO_ROOT).as_posix()
        for number, line in _code_lines(path):
            if ROOT_ONLY_READ_RE.search(line) and (rel, line.strip()) not in ROOT_ONLY_READ_ALLOWED:
                offenders.append(f"{rel}:{number}: {line.strip()}")
    assert not offenders, (
        "enumerate nodes with api.walkLiveNodes / api.walkGraphs (they descend into "
        "subgraphs), not a single graph's _nodes:\n" + "\n".join(offenders)
    )


def test_every_allowed_exception_still_exists() -> None:
    """An allow-list entry for a line that is gone is a stale excuse."""
    for rel, text in ROOT_ONLY_READ_ALLOWED:
        lines = [line.strip() for _n, line in _code_lines(REPO_ROOT / rel)]
        assert text in lines, f"{rel}: allow-listed line vanished: {text!r}"


HOOK_BY_HAND_RE = re.compile(
    r"\.on(?:NodeAdded|NodeRemoved|AfterChange)\s*=(?!=)|\[hook\]\s*=(?!=)"
    r"|__eps\w*(?:NodeWatch|Watch)\s*=\s*true"
)


def test_graph_hooks_are_only_ever_installed_through_the_shared_helper() -> None:
    """Core restores graph hooks on subgraph enter/exit and Nodes 2.0 toggles
    (useGraphNodeManager cleanup / installErrorClearingHooks disposer), so a
    hand-wrapped hook or a once-per-graph flag goes deaf. api.js's
    watchGraphHooks is the stored-and-re-verified implementation."""
    offenders = []
    for path in _web_modules():
        rel = path.relative_to(REPO_ROOT).as_posix()
        for number, line in _code_lines(path):
            if HOOK_BY_HAND_RE.search(line):
                offenders.append(f"{rel}:{number}: {line.strip()}")
    assert not offenders, (
        "install graph hooks with api.watchGraphHooks / api.watchAllGraphs:\n"
        + "\n".join(offenders)
    )


def test_api_owns_the_stored_and_reverified_watch() -> None:
    source = (WEB / "lora_library" / "api.js").read_text(encoding="utf-8")
    assert "export function watchGraphHooks(" in source
    assert "export function watchAllGraphs(" in source
    body = source.split("export function watchGraphHooks(", 1)[1].split("\n}\n", 1)[0]
    assert "if (current && current === stored[hook]) continue" in body
    assert "current.__epsWatchKeys?.has(ownerKey)" in body, "sibling features must not stack layers"
    assert "graph[hook] = wrapper" in body


#: Modules that CONTROL or READ other nodes, and the shared reach they must use.
#: A positive list: deleting the import (back to a hand walk) fails here.
REACHES_THROUGH_SHARED_HELPERS = {
    "web/lora_library/controller.js": ("api.walkLiveNodes", "api.watchAllGraphs"),
    "web/lora_library/universal_controller.js": ("api.walkLiveNodes",),
    "web/lora_library/picker.js": ("walkLiveNodes", "api.watchAllGraphs"),
    "web/lora_library/sets.js": ("api.walkLiveNodes", "api.watchAllGraphs"),
    "web/lora_library/prompt_builder.js": ("walkLiveNodes", "api.describePath"),
    "web/lora_library/pll_bridge.js": ("walkLiveNodes",),
    "web/lora_library/dasiwa_bridge.js": ("walkLiveNodes",),
    "web/eps_image/save_image.js": ("walkLiveNodes",),
    "web/eps_image/cross_sweep.js": ("watchAllGraphs",),
    "web/eps_image/bypass.js": ("resolveLinkTargets", "locationsOfNode"),
    "web/eps_image/number_controller.js": ("SUBGRAPH_OUTPUT_ID",),
    "web/eps_image/image_grid.js": ("walkLiveNodes",),
    "web/eps_image/resolution.js": ("resolveSourcesAt", "liveRootOf"),
    "web/eps_image/frame_saver.js": ("resolveSourcesAt", "liveRootOf"),
}


@pytest.mark.parametrize("rel", sorted(REACHES_THROUGH_SHARED_HELPERS))
def test_controlling_and_reading_modules_reach_through_the_shared_helpers(rel: str) -> None:
    source = (REPO_ROOT / rel).read_text(encoding="utf-8")
    missing = [name for name in REACHES_THROUGH_SHARED_HELPERS[rel] if name not in source]
    assert not missing, f"{rel} no longer uses the shared nested-reach helpers: {missing}"
