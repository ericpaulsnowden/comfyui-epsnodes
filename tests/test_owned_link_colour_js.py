"""Frontend tests for `web/eps_image/owned_link_colour.js` -- the one
`renderLink` wrap that makes frontend 1.52.7 draw a link an EPS file has
claimed (Image Grid's Collect only, Save Image's `image_output` off) in the
claimed colour, since core passes `color = null` for a plain link and then
colours it by type alone. The module has no imports, so it is run straight
from the repo under Node."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_JS = REPO_ROOT / "web" / "eps_image" / "owned_link_colour.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

PROBE_JS = """
import * as m from %(module)s
const GREY = 'rgba(128,128,128,0.25)'
const OWNER = m.LINK_COLOR_OWNER_KEY
const claimed = { id: 1, type: 'IMAGE', color: GREY, [OWNER]: 'EPSSaveImage:image-output-off' }
const unclaimed = { id: 2, type: 'IMAGE', color: '#f00' }
const claimedNoColour = { id: 3, type: 'IMAGE', [OWNER]: 'x' }

const calls = []
class FakeCanvas {
  renderLink(ctx, a, b, link, skipBorder, flow, color, startDir, endDir, extras) {
    calls.push({
      id: link ? link.id : null, color, startDir, endDir,
      extras: extras ?? null, self: this === canvas
    })
  }
}
const canvas = new FakeCanvas()
const out = {}
out.pure = [
  m.ownedLinkColour(claimed, null),
  m.ownedLinkColour(claimed, undefined) ?? 'undef',
  m.ownedLinkColour(claimed, 'white'),
  m.ownedLinkColour(unclaimed, null),
  m.ownedLinkColour(claimedNoColour, null),
  m.ownedLinkColour(null, null)
]
out.noProto = m.installOwnedLinkColourHook(null)
out.noRenderLink = m.installOwnedLinkColourHook({})
out.first = m.installOwnedLinkColourHook(FakeCanvas.prototype)
const wrapped = FakeCanvas.prototype.renderLink
out.second = m.installOwnedLinkColourHook(FakeCanvas.prototype)
out.sameWrapper = FakeCanvas.prototype.renderLink === wrapped
canvas.renderLink('ctx', [0, 0], [1, 1], claimed, false, 0, null, 4, 3, { disabled: true })
canvas.renderLink('ctx', [0, 0], [1, 1], claimed, true, 1, 'white', 4, 3)
canvas.renderLink('ctx', [0, 0], [1, 1], unclaimed, false, 0, null, 4, 3)
canvas.renderLink('ctx', [0, 0], [1, 1], null, false, 0, null, 4, 3)
out.calls = calls
globalThis.LiteGraph = { LGraphCanvas: class { renderLink() {} } }
out.ensure = m.ensureOwnedLinkColourHook()
delete globalThis.LiteGraph
out.ensureMissing = m.ensureOwnedLinkColourHook()
process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def probe(tmp_path_factory: pytest.TempPathFactory) -> dict:
    work = tmp_path_factory.mktemp("owned_link_colour")
    script = work / "probe.mjs"
    script.write_text(PROBE_JS % {"module": json.dumps(MODULE_JS.as_uri())}, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(script)], capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_pure_colour_rule(probe: dict) -> None:
    grey = "rgba(128,128,128,0.25)"
    assert probe["pure"] == [
        grey,  # claimed + core passed null -> our colour
        grey,  # undefined counts as "no colour" too
        "white",  # an explicit colour (the event flash) always wins
        None,  # an unclaimed link keeps core's null -> type colour
        None,  # claimed but no colour set -> nothing to substitute
        None,  # the drag preview passes no link
    ]


def test_install_is_feature_detected_and_idempotent(probe: dict) -> None:
    assert probe["noProto"] is False
    assert probe["noRenderLink"] is False
    assert probe["first"] is True
    assert probe["second"] is True
    assert probe["sameWrapper"] is True  # wrapped once, not twice


def test_wrapper_substitutes_only_for_claimed_links(probe: dict) -> None:
    calls = probe["calls"]
    assert [c["id"] for c in calls] == [1, 1, 2, None]
    assert calls[0]["color"] == "rgba(128,128,128,0.25)"
    assert calls[0]["extras"] == {"disabled": True}  # trailing args forwarded
    assert (calls[0]["startDir"], calls[0]["endDir"]) == (4, 3)
    assert calls[1]["color"] == "white"
    assert calls[2]["color"] is None
    assert calls[3]["color"] is None
    assert all(c["self"] for c in calls)  # `this` is the canvas


def test_ensure_reads_the_live_canvas_class(probe: dict) -> None:
    assert probe["ensure"] is True
    assert probe["ensureMissing"] is False


def test_both_dimming_files_install_the_hook_before_dimming() -> None:
    for name in ("save_image.js", "image_grid.js"):
        source = (REPO_ROOT / "web" / "eps_image" / name).read_text(encoding="utf-8")
        assert "import { ensureOwnedLinkColourHook } from './owned_link_colour.js'" in source
        hook = source.index("if (desired.size) ensureOwnedLinkColourHook()")
        assert hook < source.index("link.color = DIM_LINK_COLOR"), name


def test_module_has_no_imports() -> None:
    """The tests' served layouts copy this one file next to its importers;
    an import here would break them (and the module needs none)."""
    source = MODULE_JS.read_text(encoding="utf-8")
    assert "\nimport " not in source and not source.startswith("import ")
