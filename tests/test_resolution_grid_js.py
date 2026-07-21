"""Frontend geometry tests for the EPS Resolution M2 size grid (FORMAT.md
§6.5 M2 — the 2026-07-21 full-width-square / height-follows-width fix).

``web/eps_image/resolution.js`` deliberately factors the grid's geometry into
PURE exported functions (``getPlotRect``, ``valueToPlot``/``plotToValue``,
``computeGridWidgetHeight``/``computeGridElementHeight``,
``getReadoutLines``, the formatters) so the mapping contract is testable
without a browser. The module's single import is ComfyUI's
``../../../scripts/app.js``, resolved against the served layout
(``<web root>/extensions/<pack>/eps_image/resolution.js`` ->
``<web root>/scripts/app.js``), so the fixture mirrors that exact directory
depth in a tmp dir with a stub ``app.js``, byte-copies the real module in
unchanged, and evaluates one probe script under Node (same runtime family as
the browser). This doubles as a regression test that the relative import
depth itself is correct. Skips cleanly when Node isn't installed; the rig's
live behavior (litegraph resize lifecycle) is verified there, not here.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RESOLUTION_JS = REPO_ROOT / "web" / "eps_image" / "resolution.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

#: Node widths the geometry matrix is probed at — litegraph's practical
#: minimum for a widget node (~210), a few mid sizes, and a large one.
PROBE_WIDTHS = [190, 210, 300, 512, 1000]

#: The DOM widget's default margin (BaseDOMWidgetImpl.DEFAULT_MARGIN) — the
#: element is boxed at node width minus 2x this on both verified render
#: paths, making it the square's side. Mirrored in the probe script.
DOM_WIDGET_MARGIN = 10

PROBE_JS = """
import * as grid from './extensions/comfyui-epsnodes/eps_image/resolution.js'

const widths = %(widths)s
const margin = %(margin)d
const gridMax = 2048

const out = {
  exports: {
    hasInit: typeof grid.init === 'function',
    hasAttach: typeof grid.attach === 'function'
  },
  constants: {
    textStripH: grid.TEXT_STRIP_H,
    gridMinSize: grid.GRID_MIN_SIZE,
    readoutFontSize: grid.READOUT_FONT_SIZE,
    readoutFont: grid.READOUT_FONT,
    readoutFontStrong: grid.READOUT_FONT_STRONG
  },
  plots: widths.map((w) => ({ w, ...grid.getPlotRect(w) })),
  widgetHeights: widths.map((w) => ({ w, h: grid.computeGridWidgetHeight(w) })),
  widgetHeightRepeat: [grid.computeGridWidgetHeight(300), grid.computeGridWidgetHeight(300)],
  elementHeights: widths.map((w) => ({ w, h: grid.computeGridElementHeight(w, margin) })),
  square1000: (() => {
    const { plotX, plotY, side } = grid.getPlotRect(400)
    const px = plotX + grid.valueToPlot(1000, side, gridMax)
    const py = plotY + grid.valueToPlot(1000, side, gridMax)
    const roundtrip = grid.plotToValue(grid.valueToPlot(1000, side, gridMax), side, gridMax)
    return { px, py, side, roundtrip }
  })(),
  edgeValues: {
    origin: grid.plotToValue(0, 400, gridMax),
    farCorner: grid.plotToValue(400, 400, gridMax),
    belowSquare: grid.plotToValue(430, 400, gridMax)
  },
  readout: {
    landscape: grid.getReadoutLines(
      { rawW: 1024, rawH: 512, dispW: 1024, dispH: 512, wAuto: false, hAuto: false }),
    square: grid.getReadoutLines(
      { rawW: 1000, rawH: 1000, dispW: 1000, dispH: 1000, wAuto: false, hAuto: false }),
    autoWidth: grid.getReadoutLines(
      { rawW: 0, rawH: 512, dispW: 512, dispH: 512, wAuto: true, hAuto: false })
  }
}

process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def grid_api(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Runs the probe against the REAL resolution.js in a served-layout tmp
    dir (see module docstring) and returns its JSON output."""
    layout = tmp_path_factory.mktemp("web_root")

    scripts = layout / "scripts"
    scripts.mkdir()
    # The module only touches `app` lazily (toast plumbing); a bare object
    # with no extensionManager exercises the same optional-chaining the
    # browser path relies on.
    (scripts / "app.js").write_text("export const app = {}\n", encoding="utf-8")

    module_dir = layout / "extensions" / "comfyui-epsnodes" / "eps_image"
    module_dir.mkdir(parents=True)
    shutil.copyfile(RESOLUTION_JS, module_dir / "resolution.js")

    probe = layout / "probe.mjs"
    probe.write_text(
        PROBE_JS % {"widths": json.dumps(PROBE_WIDTHS), "margin": DOM_WIDGET_MARGIN},
        encoding="utf-8",
    )

    result = subprocess.run(
        [NODE, str(probe)], capture_output=True, text=True, timeout=60, cwd=layout
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


def test_resolution_js_parses() -> None:
    """`node --check` — the file must at minimum be valid ES module syntax."""
    result = subprocess.run(
        [NODE, "--check", str(RESOLUTION_JS)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


def test_module_still_exports_the_extension_entry_points(grid_api: dict) -> None:
    """web/eps_image.js consumes init()/attach(); the test exports must never
    displace them."""
    assert grid_api["exports"] == {"hasInit": True, "hasAttach": True}


# ------------------------------------------------------- full-width square


def test_plot_region_is_the_full_width_with_no_side_margins(grid_api: dict) -> None:
    """FORMAT §6.5 M2 (owner fix 2026-07-21): the square spans the widget's
    full width, locked to the left/right edges — plotX is 0 and side == cssW
    at every width, so there is structurally never empty space beside it
    (the centered min(availW, availH) letterbox is gone)."""
    for plot in grid_api["plots"]:
        assert plot["plotX"] == 0
        assert plot["plotY"] == 0
        assert plot["side"] == plot["w"]


def test_plot_region_no_longer_depends_on_any_height(grid_api: dict) -> None:
    """The square's side is a function of width ALONE — its drawn height
    equals its width by construction, which is what makes the pad a true
    square rather than a fit into leftover vertical space."""
    sides = [plot["side"] for plot in grid_api["plots"]]
    assert sides == PROBE_WIDTHS


# ---------------------------------------------------- height follows width


def test_widget_height_equals_node_width_plus_readout_strip(grid_api: dict) -> None:
    """The litegraph-reported widget height is node width + TEXT_STRIP_H —
    the relation that makes the NODE's minimum height width-determined (the
    frontend boxes the element at [nodeW - 2*margin, reported - 2*margin],
    so this reported value is exactly what yields a square element box)."""
    strip = grid_api["constants"]["textStripH"]
    for entry in grid_api["widgetHeights"]:
        assert entry["h"] == entry["w"] + strip


def test_element_height_is_the_square_side_plus_readout_strip(grid_api: dict) -> None:
    """The element's inline CSS height is (nodeW - 2*margin) + TEXT_STRIP_H:
    the square's side (the content width) over the text strip."""
    strip = grid_api["constants"]["textStripH"]
    for entry in grid_api["elementHeights"]:
        assert entry["h"] == (entry["w"] - 2 * DOM_WIDGET_MARGIN) + strip


def test_height_shrinks_when_width_shrinks_and_carries_no_state(grid_api: dict) -> None:
    """The stuck-tall regression (owner bug 2026-07-21, v0.19.3): height must
    be a PURE, strictly monotonic function of width — a narrower node is
    always a shorter node, and repeated evaluation returns the same answer
    (no drag baseline / grow-never-shrink state to get stuck in)."""
    heights = [entry["h"] for entry in grid_api["widgetHeights"]]
    assert heights == sorted(heights)
    assert len(set(heights)) == len(heights)  # strictly increasing, both directions reversible
    first, second = grid_api["widgetHeightRepeat"]
    assert first == second


# ------------------------------------------------- square mapping contract


def test_1000x1000_plots_on_the_true_diagonal(grid_api: dict) -> None:
    """Owner bug 2026-07-20 (square cells), preserved through the full-width
    change: both axes share one scale, so a 1000x1000 target lands at
    identical x/y offsets — the 45-degree diagonal — and the mapping
    round-trips through its inverse."""
    square = grid_api["square1000"]
    assert square["px"] == square["py"]
    assert 0 < square["px"] < square["side"]
    assert abs(square["roundtrip"] - 1000) < 1e-6


def test_pointer_edges_clamp_to_the_64_to_gridmax_range(grid_api: dict) -> None:
    """The pad's corners are exactly the range ends (64..Grid max), and a
    pointer past the square's bottom edge (over the readout strip) clamps to
    the edge value instead of overshooting."""
    edges = grid_api["edgeValues"]
    assert edges["origin"] == grid_api["constants"]["gridMinSize"] == 64
    assert edges["farCorner"] == 2048
    assert edges["belowSquare"] == 2048


# ----------------------------------------------------------- readout strip


def test_readout_puts_mp_on_line_one_and_aspect_on_line_two(grid_api: dict) -> None:
    """Owner fix 2026-07-21: line 1 = dims (left) + megapixels (right-aligned,
    SAME line — never wrapped to its own line); line 2 = the reduced aspect."""
    landscape = grid_api["readout"]["landscape"]
    assert landscape == {"dims": "1024 x 512", "mp": "0.52 MP", "aspect": "2:1"}
    square = grid_api["readout"]["square"]
    assert square == {"dims": "1000 x 1000", "mp": "1.0 MP", "aspect": "1:1"}


def test_readout_keeps_the_auto_label_for_a_zero_axis(grid_api: dict) -> None:
    """A 0 (derive-mode) axis still reads "auto" — the grid never writes 0,
    but it must keep rendering a typed 0 faithfully."""
    auto = grid_api["readout"]["autoWidth"]
    assert auto["dims"] == "auto x 512"
    assert auto["aspect"] == "1:1"  # mirrored 512x512 for plotting purposes
    assert auto["mp"] == "0.26 MP"


def test_both_readout_lines_share_one_small_font_size(grid_api: dict) -> None:
    """Owner fix 2026-07-21: the dimension line was 13px over an 11px second
    line. Both lines must now share the single READOUT_FONT_SIZE, and the
    strong variant may differ only by weight."""
    constants = grid_api["constants"]
    size = constants["readoutFontSize"]
    assert size <= 12  # "small"
    assert f"{size}px" in constants["readoutFont"]
    assert constants["readoutFontStrong"] == f"600 {constants['readoutFont']}"
