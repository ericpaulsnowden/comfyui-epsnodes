"""Shared served-layout builder for the v1.2.0 nested-reach Node-harness
tests (FORMAT.md §7.10).

The pack's ES modules import ComfyUI's ``scripts/api.js`` / ``scripts/app.js``
by RELATIVE path, so a probe only resolves them inside a tmp dir that mirrors
the served directory depth (``tests/test_lora_walk_js.py``'s technique). This
helper builds that layout once per call: the requested real modules are
byte-copied (never edited), ``lora_library/api.js`` + ``version.js`` are
always present (nearly everything imports them), and ``nested_graph.mjs`` --
the shared fake nested litegraph -- sits next to the probe.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB = REPO_ROOT / "web"
NESTED_GRAPH_MJS = Path(__file__).resolve().parent / "nested_graph.mjs"
NODE = shutil.which("node")

DEFAULT_API_STUB = (
    "export const api = { fetchApi: async () => ({ ok: false }), "
    "addEventListener: () => {}, apiURL: (p) => p }\n"
)
DEFAULT_APP_STUB = "export const app = { graph: null, configuringGraph: false }\n"


def build_layout(
    root: Path,
    *,
    eps_image: tuple[str, ...] = (),
    lora_library: tuple[str, ...] = (),
    api_stub: str = DEFAULT_API_STUB,
    app_stub: str = DEFAULT_APP_STUB,
) -> Path:
    """Build ``root/extensions/comfyui-epsnodes/{eps_image,lora_library}`` +
    ``root/scripts`` and return *root* (the cwd to run a probe from).
    *eps_image* / *lora_library* are bare module file names to copy."""
    ext = root / "extensions" / "comfyui-epsnodes"
    for sub, names in (("eps_image", eps_image), ("lora_library", lora_library)):
        (ext / sub).mkdir(parents=True, exist_ok=True)
        for name in names:
            shutil.copyfile(WEB / sub / name, ext / sub / name)
    for name in ("api.js", "version.js"):
        shutil.copyfile(WEB / "lora_library" / name, ext / "lora_library" / name)
    scripts = root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "api.js").write_text(api_stub, encoding="utf-8")
    (scripts / "app.js").write_text(app_stub, encoding="utf-8")
    shutil.copyfile(NESTED_GRAPH_MJS, root / "nested_graph.mjs")
    return root


def run_probe(root: Path, probe_js: str, *, timeout: int = 60) -> dict:
    """Write *probe_js* to ``root/probe.mjs``, run it under Node and return
    its single-line JSON stdout (the probe ends with ``console.log(JSON...)``)."""
    probe = root / "probe.mjs"
    probe.write_text(probe_js, encoding="utf-8")
    result = subprocess.run(
        [NODE or "node", str(probe)], capture_output=True, text=True, timeout=timeout, cwd=root
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}\n{result.stdout}"
    return json.loads(result.stdout.strip().splitlines()[-1])
