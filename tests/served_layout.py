"""A served-layout tmp dir for the frontend JS tests that import the
broadcast modules (``tests/test_cross_sweep_js.py``'s convention, shared).

ComfyUI serves a pack's ``web/`` tree at ``extensions/<pack>/`` next to the
frontend's own ``scripts/``; the pack's modules import ``../../../scripts/
app.js`` relative to that depth. ``build_served_layout`` byte-copies the real
modules into a tmp dir mirroring it, and writes small stubs for the two
frontend scripts -- so a wrong import depth is as fatal in the test as it
would be in the browser.

``cross_sweep.js`` imports ``broadcast.js`` (v1.3.0), which pulls in the
planner, the live adapter, the UI, the link-drawing stage (``broadcast_draw.js``,
the tucked-wires stage), ``bypass.js`` (for ``inputVerdict`` -- the
broadcast adapter IMPORTS it, never copies it) and, through bypass.js,
``number_controller.js``, ``distributor.js`` and ``lora_library/api.js``. Any
test that loads ``cross_sweep.js`` therefore needs all of them.
"""

from __future__ import annotations

import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB = REPO_ROOT / "web"

#: Every eps_image module ``cross_sweep.js`` can reach.
CROSS_SWEEP_MODULES = (
    "cross_sweep.js",
    "broadcast.js",
    "broadcast_plan.js",
    "broadcast_graph.js",
    "broadcast_draw.js",
    "broadcast_ui.js",
    "bypass.js",
    "number_controller.js",
    "distributor.js",
)

#: The lora_library modules those reach.
LORA_LIBRARY_MODULES = ("api.js", "version.js")

DEFAULT_APP_STUB = (
    "export const app = {\n"
    "  configuringGraph: false,\n"
    "  extensionManager: { toast: { add(t) { (globalThis.__toasts ??= []).push(t) } } }\n"
    "}\n"
)
DEFAULT_API_STUB = "export const api = { fetchApi: () => {}, addEventListener: () => {} }\n"


def build_served_layout(
    layout: Path,
    eps_modules: tuple[str, ...] = CROSS_SWEEP_MODULES,
    lora_modules: tuple[str, ...] = LORA_LIBRARY_MODULES,
    app_stub: str = DEFAULT_APP_STUB,
    api_stub: str = DEFAULT_API_STUB,
    with_fake: bool = False,
) -> Path:
    """Creates ``layout/extensions/comfyui-epsnodes/{eps_image,lora_library}``
    with the named real modules, plus ``layout/scripts/{app,api}.js`` stubs.
    ``with_fake`` also drops ``fake_litegraph.mjs`` and ``fake_dom.mjs`` at the
    layout root.
    Returns the ``eps_image`` module dir."""
    pack = layout / "extensions" / "comfyui-epsnodes"
    eps_dir = pack / "eps_image"
    eps_dir.mkdir(parents=True, exist_ok=True)
    for name in eps_modules:
        shutil.copyfile(WEB / "eps_image" / name, eps_dir / name)
    if lora_modules:
        lora_dir = pack / "lora_library"
        lora_dir.mkdir(parents=True, exist_ok=True)
        for name in lora_modules:
            shutil.copyfile(WEB / "lora_library" / name, lora_dir / name)
    scripts = layout / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "app.js").write_text(app_stub, encoding="utf-8")
    (scripts / "api.js").write_text(api_stub, encoding="utf-8")
    if with_fake:
        # tests/fake_litegraph.mjs: the shared fake litegraph (nodes, links,
        # subgraph definitions + instances) the broadcast probes import.
        here = Path(__file__).resolve().parent
        for name in ("fake_litegraph.mjs", "fake_dom.mjs"):
            shutil.copyfile(here / name, layout / name)
    return eps_dir
