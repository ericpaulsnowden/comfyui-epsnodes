"""The Lora Loader State Controller's `target` combo value is SAVED STATE
(widgets_values). `_refreshTargetCombo` is a view refresh run on load,
heartbeat and every graph change; it must not rewrite what the user chose
because the graph momentarily holds no loaders (configure order, undo/redo,
subgraph enter/exit, delete + re-add). Drives the REAL method of the REAL
registered class in Node against a fake graph."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = REPO_ROOT / "web" / "lora_library"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

PROBE = r"""
globalThis.LGraphNode = class {}
let Cls = null
globalThis.LiteGraph = { registered_node_types: {}, registerNodeType: (n, c) => { Cls = c } }
const { app } = await import('./scripts/app.js')
const m = await import('./extensions/comfyui-epsnodes/lora_library/controller.js')
m.registerControllerNode()
const refresh = (widget) => Cls.prototype._refreshTargetCombo.call({ _w: { target: widget } })

const pll = (id, title) => ({ id, type: 'Power Lora Loader (rgthree)', title })
const graphOf = (...nodes) => ({ _nodes: nodes })
const out = {}

// Saved target = B among {A, B}. Transient empty graph in between.
const widget = { value: 'Power Lora Loader (rgthree) #2' }
const history = []
app.graph = graphOf(pll(1, 'Power Lora Loader (rgthree)'), pll(2, 'Power Lora Loader (rgthree)'))
refresh(widget); history.push(widget.value)
app.graph = graphOf()
refresh(widget); history.push(widget.value)
app.graph = graphOf(pll(1, 'Power Lora Loader (rgthree)'), pll(2, 'Power Lora Loader (rgthree)'))
refresh(widget); history.push(widget.value)
out.transient = history

// "All (2)" sticky mode survives the same blip.
const all = { value: 'All Power Lora Loaders (2)' }
const hist2 = []
app.graph = graphOf()
refresh(all); hist2.push(all.value)
app.graph = graphOf(pll(1, 'Power Lora Loader (rgthree)'), pll(2, 'Power Lora Loader (rgthree)'))
refresh(all); hist2.push(all.value)
out.allMode = hist2

// Legitimate: the target is truly deleted, loaders A and C remain -> stale
// value left alone (probe reports "not found"), never guessed.
const stale = { value: 'Power Lora Loader (rgthree) #2' }
app.graph = graphOf(pll(1, 'Power Lora Loader (rgthree)'), pll(3, 'Power Lora Loader (rgthree)'))
refresh(stale)
out.deletedAmongTwo = stale.value

// Legitimate: exactly one loader left that is not the saved one -> adopt it.
const one = { value: 'Power Lora Loader (rgthree) #2' }
app.graph = graphOf(pll(1, 'Power Lora Loader (rgthree)'))
refresh(one)
out.deletedLeavesOne = one.value

// Fresh controller (empty value) with no loaders shows the placeholder.
const fresh = { value: '' }
app.graph = graphOf()
refresh(fresh)
out.fresh = fresh.value
process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def result(tmp_path_factory: pytest.TempPathFactory) -> dict:
    layout = tmp_path_factory.mktemp("web_root")
    mod = layout / "extensions" / "comfyui-epsnodes"
    shutil.copytree(LIB, mod / "lora_library")
    scripts = layout / "scripts"
    scripts.mkdir()
    (scripts / "api.js").write_text("export const api = { fetchApi: () => {} }\n", encoding="utf-8")
    (scripts / "app.js").write_text("export const app = {}\n", encoding="utf-8")
    (layout / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")
    (layout / "probe.mjs").write_text(PROBE, encoding="utf-8")
    run = subprocess.run(
        [NODE, "probe.mjs"], capture_output=True, encoding="utf-8", timeout=60, cwd=layout
    )
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


B = "Power Lora Loader (rgthree) #2"


def test_empty_graph_blip_does_not_rewrite_the_saved_target(result: dict) -> None:
    # The value must be B before, DURING and after the zero-loader refresh.
    assert result["transient"] == [B, B, B]


def test_all_loaders_choice_survives_the_blip(result: dict) -> None:
    assert result["allMode"][-1].startswith("All")
    assert result["allMode"][0].startswith("All")


def test_genuinely_deleted_target_is_left_stale_not_guessed(result: dict) -> None:
    assert result["deletedAmongTwo"] == B


def test_sole_remaining_loader_is_adopted(result: dict) -> None:
    assert result["deletedLeavesOne"] == "Power Lora Loader (rgthree) #1"


def test_fresh_controller_with_no_loader_shows_placeholder(result: dict) -> None:
    assert result["fresh"] == "(none found)"
