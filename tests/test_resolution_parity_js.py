"""Python-vs-JavaScript parity for the EPS Resolution math that is MIRRORED
(not shared) between ``eps_image/nodes_resolution.py`` and
``web/eps_image/resolution.js``: ``conform_to_ratio``/``conformToRatio``,
``round_half_up``/``roundHalfUp``, ``scale_side``/``scaleSide``,
``coerce_scale_multiplier``/``parseScaleMultiplier`` and
``parse_ratio``/``parseRatio``. The panel previews with the JS and the node
runs the Python; a disagreement is a size the user saw but did not get. A
2026-11-13 differential fuzz found exactly that: Python's banker's
``round()`` against ``Math.round`` on .5 ties (1000 wide at 16:9 -> 562 vs
563; 40 at multiple 16 -> 32 vs 48). The same cases now run through both.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from eps_image import nodes_resolution as nr

REPO_ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

RATIOS = ["none", "16:9", "4:3", "1:1", "2:1", "3:2", "21:9", "5:4", "9:16", "0:1", "x", "", " 16:9 ", "3:7"]
CONFORM_CASES = [
    {"w": w, "h": h, "ratio": ratio, "anchor": anchor, "m": m}
    for w in (0, 3, 7, 100, 333, 1000, 1001, 1365, 1920, 2773)
    for h in (0, 3, 64, 100, 548, 768, 1000, 1080)
    for ratio in RATIOS
    for anchor in ("width", "height")
    for m in (0, 1, 8, 16, 64)
]
ROUND_CASES = [2.5, 3.5, 0.5, 562.5, 0.3 * 1005, 123.4999995, 0.4999999, 1e-7, 4095.5, 7.0]
SCALE_CASES = [(size, m) for size in (0, 1, 3, 64, 1005, 1024, 4096) for m in (0.3, 0.5, 1.5, 0.25, 1 / 3, 2 / 3, 0.1)]
MULTIPLIER_CASES = [None, "", "x", "1e400", 0, -1, 0.5, "0.5", True, " 2 ", "2x", 4, "1.5"]
PARSE_RATIO_CASES = [*RATIOS, "16 : 9", "016:09", "1:1:1", 169, None, "4:3 "]

PROBE = """
import { readFileSync } from 'node:fs'
const m = await import('./extensions/comfyui-epsnodes/eps_image/resolution.js')
const cases = JSON.parse(readFileSync(process.argv[2], 'utf8'))
const out = []
for (const c of cases) {
  if (c.fn === 'conform') { const r = m.conformToRatio({ width: c.w, height: c.h }, c.ratio, c.anchor, c.m); out.push([r.width, r.height]) }
  else if (c.fn === 'round') out.push(m.roundHalfUp(c.v))
  else if (c.fn === 'scale') out.push(m.scaleSide(c.size, c.m))
  else if (c.fn === 'mult') out.push(m.parseScaleMultiplier(c.raw))
  else if (c.fn === 'ratio') { const r = m.parseRatio(c.v); out.push(r ? [r.w, r.h] : null) }
}
process.stdout.write(JSON.stringify(out))
"""


def _python(case: dict):
    fn = case["fn"]
    if fn == "conform":
        return list(nr.conform_to_ratio(case["w"], case["h"], case["ratio"], case["m"], case["anchor"]))
    if fn == "round":
        return nr.round_half_up(case["v"])
    if fn == "scale":
        return nr.scale_side(case["size"], case["m"])
    if fn == "mult":
        return nr.coerce_scale_multiplier(case["raw"])
    parsed = nr.parse_ratio(case["v"])
    return list(parsed) if parsed else None


@pytest.fixture(scope="module")
def layout(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("resolution_parity")
    module_dir = root / "extensions" / "comfyui-epsnodes" / "eps_image"
    module_dir.mkdir(parents=True)
    shutil.copyfile(REPO_ROOT / "web" / "eps_image" / "resolution.js", module_dir / "resolution.js")
    lora_library = root / "extensions" / "comfyui-epsnodes" / "lora_library"
    lora_library.mkdir(parents=True)
    for name in ("api.js", "version.js", "panel_helpers.js"):  # the real shared loggerFor/showToast
        shutil.copyfile(REPO_ROOT / "web" / "lora_library" / name, lora_library / name)
    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "app.js").write_text(
        "export const app = { registerExtension() {}, ui: { settings: { getSettingValue() {}, addSetting() {} } } }\n",
        encoding="utf-8",
    )
    (scripts / "api.js").write_text("export const api = { addEventListener() {}, fetchApi: async () => ({}) }\n", encoding="utf-8")
    (root / "probe.mjs").write_text(PROBE, encoding="utf-8")
    return root


def _run_js(layout: Path, cases: list[dict]) -> list:
    cases_path = layout / "cases.json"
    cases_path.write_text(json.dumps(cases), encoding="utf-8")
    result = subprocess.run(
        [NODE, str(layout / "probe.mjs"), str(cases_path)],
        capture_output=True, encoding="utf-8", timeout=60, cwd=layout, check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_every_mirrored_helper_agrees_between_python_and_javascript(layout: Path) -> None:
    cases = (
        [{"fn": "conform", **c} for c in CONFORM_CASES]
        + [{"fn": "round", "v": v} for v in ROUND_CASES]
        + [{"fn": "scale", "size": s, "m": m} for s, m in SCALE_CASES]
        + [{"fn": "mult", "raw": raw} for raw in MULTIPLIER_CASES]
        + [{"fn": "ratio", "v": v} for v in PARSE_RATIO_CASES]
    )
    js = _run_js(layout, cases)
    mismatches = [
        (case, py, j) for case, j in zip(cases, js, strict=True) if (py := _python(case)) != j
    ]
    assert mismatches == [], f"{len(mismatches)} of {len(cases)} cases differ; first: {mismatches[:5]}"


@pytest.mark.parametrize(
    ("width", "height", "ratio", "multiple_of", "anchor", "expected"),
    [
        (1000, 64, "16:9", 0, "width", (1000, 563)),  # 562.5 -> half-up, like the panel
        (1000, 64, "16:9", 1, "width", (1000, 563)),
        (3, 100, "2:1", 16, "height", (208, 100)),  # 200/16 = 12.5 -> 13 multiples
        (1001, 548, "9:16", 8, "width", (1001, 1784)),  # 1779.6 -> 1780 -> 222.5 -> 223 * 8
    ],
)
def test_ties_round_half_up_like_the_panel(width, height, ratio, multiple_of, anchor, expected) -> None:
    assert nr.conform_to_ratio(width, height, ratio, multiple_of, anchor) == expected
