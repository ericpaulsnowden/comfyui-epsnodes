"""Option/Alt-click on a section arrow opens or closes EVERY section (owner
ask 2026-11-02). One shared pure helper, ``api.js`` ``collapseAllFrom``,
driven here under Node; the four panels that draw section arrows must all
call it (source assertions -- the shared-panel-code rule: a hand copy in one
panel is how the siblings drift).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.served_layout import build_served_layout

REPO_ROOT = Path(__file__).resolve().parent.parent
LORA = REPO_ROOT / "web" / "lora_library"
NODE = shutil.which("node")

PANELS = ("notebook.js", "controller.js", "universal_controller.js", "prompt_builder.js")


@pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")
def test_collapse_all_from(tmp_path: Path) -> None:
    build_served_layout(tmp_path, eps_modules=())
    script = tmp_path / "probe.mjs"
    script.write_text(
        "import { collapseAllFrom, sectionHeaderTitle } from "
        "'./extensions/comfyui-epsnodes/lora_library/api.js'\n"
        "const cats = ['A', 'B', 'C']\n"
        "console.log(JSON.stringify({\n"
        "  openClicked: [...collapseAllFrom(new Set(['B']), cats, 'A')],\n"
        "  closedClicked: [...collapseAllFrom(new Set(['B']), cats, 'B')],\n"
        "  noList: [...collapseAllFrom(new Set(), undefined, 'A')],\n"
        "  title: sectionHeaderTitle('Faces'),\n"
        "}))\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [NODE, str(script)], capture_output=True, text=True, timeout=30, cwd=tmp_path
    )
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)
    assert got["openClicked"] == ["A", "B", "C"]  # an open one closes all
    assert got["closedClicked"] == []  # a closed one opens all
    assert got["noList"] == []
    assert got["title"].startswith("Faces\n") and "Option/Alt-click" in got["title"]


@pytest.mark.parametrize("panel", PANELS)
def test_every_section_panel_uses_the_shared_helper(panel: str) -> None:
    source = (LORA / panel).read_text(encoding="utf-8")
    assert "api.collapseAllFrom(" in source
    assert "altKey" in source
    assert "api.sectionHeaderTitle(category)" in source


@pytest.mark.parametrize("panel", ("notebook.js", "controller.js", "universal_controller.js"))
def test_option_double_click_never_opens_rename(panel: str) -> None:
    source = (LORA / panel).read_text(encoding="utf-8")
    blocks = [b[:2000] for b in source.split("addEventListener('dblclick', (event) => {")[1:]]
    header_blocks = [
        b for b in blocks if "CategoryRename(" in b or "beginInlineRename(state, 'category'" in b
    ]
    assert header_blocks, "section-header dblclick handler not found"
    for block in header_blocks:
        assert "if (event.altKey) return" in block
