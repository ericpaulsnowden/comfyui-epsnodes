"""Frontend tests for `web/eps_image/save_image.js` (FORMAT.md §6.14,
provenance M2): the filename-token fallback's pure helpers are driven under
Node via a served-layout probe (test_cross_sweep_js.py's convention; this
module imports `../../../scripts/app.js` and `../lora_library/api.js`, so
the fixture mirrors that depth and byte-copies the REAL api.js), and the
`app.handleFile` wrap is pinned by source text."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SAVE_IMAGE_JS = REPO_ROOT / "web" / "eps_image" / "save_image.js"
API_JS = REPO_ROOT / "web" / "lora_library" / "api.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

#: (file name, expected token) -- the M1 grammar read from the END of the stem.
TOKEN_CASES = [
    ("Portrait_m2_i1_t3_00001_.png", "m2_i1_t3"),
    ("shot_2_m1_p1_00001_.png", "m1_p1"),
    ("pair_01_m1_v2_p1_00003_.png", "m1_v2_p1"),
    ("Neon City_i2_t1_00001_.png", "i2_t1"),
    ("Alpha_t1_00001_.webp", "t1"),
    ("plain_00001_.png", None),
    ("ComfyUI_00001_.png", None),
    ("m2_i1_t3.png", None),  # no counter: not a Save Image file name
    ("", None),
]

#: (token, multipliers [{soloValue}], expected verdict)
DECIDE_CASES = [
    ("m1_p1", [{"soloValue": ""}], "apply"),
    ("m1_p1", [{"soloValue": "m1_p1"}], "baked"),  # a baked file: leave it
    ("m1_p1", [{"soloValue": ""}, {"soloValue": ""}], "ambiguous"),
    ("m1_p1", [{"soloValue": "m9_p9"}], "none"),  # already soloed elsewhere: hands off
    ("m1_p1", [{"soloValue": ""}, {"soloValue": "m1_p1"}], "baked"),
    ("m1_p1", [], "none"),
    ("", [{"soloValue": ""}], "none"),
]

PROBE_JS = """
import * as m from './extensions/comfyui-epsnodes/eps_image/save_image.js'
const out = {
  exports: {
    hasInit: typeof m.init === 'function',
    hasTokenFromFileName: typeof m.tokenFromFileName === 'function',
    hasDecideFilenameSolo: typeof m.decideFilenameSolo === 'function'
  },
  tokens: %(token_inputs)s.map((name) => m.tokenFromFileName(name)),
  verdicts: %(decide_inputs)s.map(([token, mults]) => m.decideFilenameSolo(token, mults))
}
process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def save_image_api(tmp_path_factory: pytest.TempPathFactory) -> dict:
    layout = tmp_path_factory.mktemp("web_root")
    pack = layout / "extensions" / "comfyui-epsnodes"
    (pack / "eps_image").mkdir(parents=True)
    (pack / "lora_library").mkdir(parents=True)
    shutil.copyfile(SAVE_IMAGE_JS, pack / "eps_image" / "save_image.js")
    shutil.copyfile(API_JS, pack / "lora_library" / "api.js")
    # api.js imports its sibling version.js -- copy the real one too.
    shutil.copyfile(API_JS.parent / "version.js", pack / "lora_library" / "version.js")
    scripts = layout / "scripts"
    scripts.mkdir()
    (scripts / "app.js").write_text(
        "export const app = { graph: { _nodes: [] } }\n", encoding="utf-8"
    )
    (scripts / "api.js").write_text(
        "export const api = { fetchApi: () => {}, addEventListener: () => {} }\n", encoding="utf-8"
    )
    probe = layout / "probe.mjs"
    probe.write_text(
        PROBE_JS
        % {
            "token_inputs": json.dumps([name for name, _ in TOKEN_CASES]),
            "decide_inputs": json.dumps([[t, mults] for t, mults, _ in DECIDE_CASES]),
        },
        encoding="utf-8",
    )
    result = subprocess.run(
        [NODE, str(probe)], capture_output=True, text=True, timeout=60, cwd=layout
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def source() -> str:
    return SAVE_IMAGE_JS.read_text(encoding="utf-8")


def test_parses() -> None:
    result = subprocess.run(
        [NODE, "--check", str(SAVE_IMAGE_JS)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


def test_exports(save_image_api: dict) -> None:
    assert save_image_api["exports"] == {
        "hasInit": True,
        "hasTokenFromFileName": True,
        "hasDecideFilenameSolo": True,
    }


def test_token_from_file_name_cases(save_image_api: dict) -> None:
    for (name, expected), got in zip(TOKEN_CASES, save_image_api["tokens"], strict=True):
        assert got == expected, f"tokenFromFileName({name!r}) -> {got!r}, wanted {expected!r}"


def test_decide_filename_solo_cases(save_image_api: dict) -> None:
    for (token, mults, expected), got in zip(DECIDE_CASES, save_image_api["verdicts"], strict=True):
        assert got == expected, (
            f"decideFilenameSolo({token!r}, {mults!r}) -> {got!r}, wanted {expected!r}"
        )


def test_handle_file_is_wrapped_once_chained_and_image_only(source: str) -> None:
    """§7.5: wrap, never replace; the original runs first (the frontend loads
    the workflow), then the fallback reads the loaded graph; only image files;
    a baked file (solo already set) is left alone; ambiguity toasts instead
    of guessing."""
    assert "const original = app?.handleFile" in source
    assert "app.handleFile = async function (file, ...rest) {" in source
    assert "const result = await original.call(this, file, ...rest)" in source
    assert "applyFilenameSolo(file)" in source
    assert "if (installed) return" in source
    assert "/^image\\//i.test(file.type || '')" in source
    assert "verdict === 'apply'" in source and "verdict === 'ambiguous'" in source
    assert "widget.callback?.(token)" in source
    # no drop-path interception, no window listeners
    assert not re.search(r"window\.addEventListener", source)
    assert "onDragDrop" not in source
