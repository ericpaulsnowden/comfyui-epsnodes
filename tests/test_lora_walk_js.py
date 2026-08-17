"""v0.64.0 nested-graph traversal (web/lora_library/api.js): walkLiveNodes /
walkGraphs / findByPathId, executed for real under node against a fake
nested graph -- the shapes were rig-probed 2026-08-14 (SubgraphNode carries
`.subgraph`, an LGraph with its own `_nodes` and id space; execution ids
join the containing SubgraphNode ids with ':')."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
API_JS = REPO_ROOT / "web" / "lora_library" / "api.js"
NODE = shutil.which("node") or "node"

PROBE_JS = """
import { walkLiveNodes, walkGraphs, findByPathId } from './extensions/comfyui-epsnodes/lora_library/api.js'

// Root graph: node 1 (plain), node 3 (SubgraphNode) whose subgraph holds
// node 2 (plain) and node 5 (a DEEPER SubgraphNode) holding node 2 -- the
// inner id deliberately COLLIDES with the mid-level one.
const deep = { _nodes: [{ id: 2, type: 'Deep' }] }
const mid = { _nodes: [{ id: 2, type: 'Mid' }, { id: 5, type: 'SubDeep', subgraph: deep }] }
const root = { _nodes: [{ id: 1, type: 'Root' }, { id: 3, type: 'Sub', subgraph: mid }] }

const walked = walkLiveNodes(root).map((e) => [e.pathId, e.node.type])
const graphs = walkGraphs(root).length
const hits = {
  root: findByPathId(root, '1')?.type ?? null,
  mid: findByPathId(root, '3:2')?.type ?? null,
  deep: findByPathId(root, '3:5:2')?.type ?? null,
  stale: findByPathId(root, '3:9')?.type ?? null,
  nonsense: findByPathId(root, 'x')?.type ?? null,
}
console.log(JSON.stringify({ walked, graphs, hits }))
"""


@pytest.fixture(scope="module")
def walk_api(tmp_path_factory: pytest.TempPathFactory) -> dict:
    layout = tmp_path_factory.mktemp("web_root")
    module_dir = layout / "extensions" / "comfyui-epsnodes" / "lora_library"
    module_dir.mkdir(parents=True)
    shutil.copyfile(API_JS, module_dir / "api.js")
    # api.js imports ComfyUI's scripts/api.js -- stub it (same served-layout
    # technique as test_pll_bridge_js.py's app.js stub).
    scripts = layout / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "api.js").write_text("export const api = {}\n", encoding="utf-8")
    (module_dir / "version.js").write_text("export const FRONTEND_VERSION = 'test'\n", encoding="utf-8")
    probe = layout / "probe.mjs"
    probe.write_text(PROBE_JS, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(probe)], capture_output=True, text=True, timeout=60, cwd=layout
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


def test_walk_visits_every_level_with_execution_shaped_path_ids(walk_api: dict) -> None:
    assert walk_api["walked"] == [
        ["1", "Root"],
        ["3", "Sub"],
        ["3:2", "Mid"],
        ["3:5", "SubDeep"],
        ["3:5:2", "Deep"],
    ]
    assert walk_api["graphs"] == 3


def test_find_by_path_id_resolves_collisions_and_degrades_to_null(walk_api: dict) -> None:
    """Two nodes share bare id 2 at different depths -- the path keeps them
    apart; a stale or nonsense path is null, never a wrong node."""
    hits = walk_api["hits"]
    assert hits == {"root": "Root", "mid": "Mid", "deep": "Deep", "stale": None, "nonsense": None}
