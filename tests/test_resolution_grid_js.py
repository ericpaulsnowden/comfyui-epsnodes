"""Frontend tests for the EPS Resolution M2 size grid (FORMAT.md §6.5 M2 —
the 2026-07-21 full-width-square / height-follows-width fix), plus the
hideable-output wired-refusal guard (§6.5, ``isOutputConnected``).

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

#: (label, JS expression building the `output`, expected isOutputConnected()).
#: Mirrors `LGraphCanvas.ts`'s `hasRelevantOutputLinks`, which unions BOTH
#: `output.links` and `output._floatingLinks`. The floating-only rows are the
#: regression pin: a `.links`-only check (this file's own version until
#: v0.34.0) reads a link that is still mid-drag as UNCONNECTED, so both
#: "never leave a dangling wire" refusals would let go — and the original-size
#: one really `removeOutput()`s the socket under it. Kept identical to
#: tests/test_distributor_js.py's list for the same function there.
OUTPUT_LINK_CASES = [
    ("null output", "null", False),
    ("undefined output", "undefined", False),
    ("neither field", "{}", False),
    ("empty links array", "{ links: [] }", False),
    ("settled link", "{ links: [7] }", True),
    ("links not an array", "{ links: 3 }", False),
    ("empty floating set", "{ links: [], _floatingLinks: new Set() }", False),
    ("floating link only", "{ links: [], _floatingLinks: new Set([1]) }", True),
    ("floating with no links field", "{ _floatingLinks: new Set([1, 2]) }", True),
    ("floating set is not a Set", "{ links: [], _floatingLinks: {} }", False),
]

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
    sourceLineH: grid.SOURCE_LINE_H,
    gridMinSize: grid.GRID_MIN_SIZE,
    readoutFontSize: grid.READOUT_FONT_SIZE,
    readoutFont: grid.READOUT_FONT,
    readoutFontStrong: grid.READOUT_FONT_STRONG
  },
  linkChecks: [%(link_cases)s].map((output) => grid.isOutputConnected(output)),
  plots: widths.map((w) => ({ w, ...grid.getPlotRect(w) })),
  widgetHeights: widths.map((w) => ({ w, h: grid.computeGridWidgetHeight(w) })),
  widgetHeightRepeat: [grid.computeGridWidgetHeight(300), grid.computeGridWidgetHeight(300)],
  elementHeights: widths.map((w) => ({ w, h: grid.computeGridElementHeight(w, margin) })),
  // --- incoming-image ("in") readout line, 2026-07-29 owner ask ---
  sourceHeights: widths.map((w) => ({
    w,
    widgetOneLine: grid.computeGridWidgetHeight(w),
    widgetTwoLine: grid.computeGridWidgetHeight(w, true),
    elementOneLine: grid.computeGridElementHeight(w, margin),
    elementTwoLine: grid.computeGridElementHeight(w, margin, true)
  })),
  sourceLines: [
    [1920, 1080], [512, 512], [1000, 1], [0, 100], [100, 0], [-5, 5], [null, null]
  ].map(([w, h]) => grid.getSourceReadoutLine(w, h)),
  sourceMatchesTargetFormat: (() => {
    // "Display in a similar format" -- the source line must produce the
    // SAME field shape/formatting the target readout does for equal dims.
    const target = grid.getReadoutLines({
      rawW: 1920, rawH: 1080, dispW: 1920, dispH: 1080, wAuto: false, hAuto: false
    })
    const source = grid.getSourceReadoutLine(1920, 1080)
    return {
      dims: target.dims === source.dims,
      aspect: target.aspect === source.aspect,
      mp: target.mp === source.mp
    }
  })(),
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
    # M3 (size presets) added a second static import, `../../../scripts/
    # api.js` -- ES modules resolve every static import eagerly regardless
    # of whether this probe's own calls ever reach it, so it must exist too
    # (identical stub to test_checkpoint_switcher_js.py's own `api.js`,
    # which needed this for the same reason).
    (scripts / "api.js").write_text("export const api = { fetchApi: () => {} }\n", encoding="utf-8")

    module_dir = layout / "extensions" / "comfyui-epsnodes" / "eps_image"
    module_dir.mkdir(parents=True)
    shutil.copyfile(RESOLUTION_JS, module_dir / "resolution.js")

    probe = layout / "probe.mjs"
    probe.write_text(
        PROBE_JS
        % {
            "widths": json.dumps(PROBE_WIDTHS),
            "margin": DOM_WIDGET_MARGIN,
            # Built as raw JS, not JSON: `_floatingLinks` is a real `Set`, so
            # the case inputs have to be constructed in the probe itself.
            "link_cases": ", ".join(js for _, js, _ in OUTPUT_LINK_CASES),
        },
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


# ---------------------------------------------- the wired-output refusal


def test_is_output_connected_counts_floating_links_too(grid_api: dict) -> None:
    """Both hideable-output paths (§6.5) refuse to hide a WIRED output rather
    than leave a dangling wire — `Show original size` because it really
    `removeOutput()`s the pair, `Show passthrough image` because the cosmetic
    hide would leave the wire drawn to an invisible dot. "Wired" therefore has
    to mean what the frontend itself means by it: `LGraphCanvas.ts`'s
    `hasRelevantOutputLinks` unions `output.links` with `output._floatingLinks`
    (a link mid-drag), so a `.links`-only check would silently drop a link
    the user still has hold of. Same case list as distributor.js's copy of
    this function, which the two are kept in lockstep with."""
    for (label, _js, expected), got in zip(
        OUTPUT_LINK_CASES, grid_api["linkChecks"], strict=True
    ):
        assert got is expected, f"isOutputConnected({label}) -> {got!r}, wanted {expected!r}"


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


def test_readout_lines_expose_dims_mp_and_aspect(grid_api: dict) -> None:
    """getReadoutLines exposes the three fields the (now single-line) readout
    draws: dims (left), the reduced aspect right beside it (owner ask
    2026-07-21 — next to the dimensions, no longer its own line below), and
    megapixels right-aligned. This asserts the DATA; the one-line layout is a
    draw concern verified live on the rig."""
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


# ---- incoming-image readout line (2026-07-29 owner ask) --------------------


def test_source_line_only_adds_height_when_present(grid_api: dict) -> None:
    """The strip is ONE line until there's a real incoming size to show, so
    an unconnected node keeps exactly the geometry every earlier fix
    settled. `withSourceLine` defaults false -- that default is what let 13
    pre-existing geometry tests keep passing untouched."""
    extra = grid_api["constants"]["sourceLineH"]
    assert isinstance(extra, int) and extra > 0
    for entry in grid_api["sourceHeights"]:
        assert entry["widgetTwoLine"] - entry["widgetOneLine"] == extra
        assert entry["elementTwoLine"] - entry["elementOneLine"] == extra


def test_source_line_formats_like_the_target_line(grid_api: dict) -> None:
    """Eric asked for "a similar format" -- pin that it's the SAME
    formatting, not a lookalike that could drift apart later."""
    assert grid_api["sourceMatchesTargetFormat"] == {"dims": True, "aspect": True, "mp": True}


def test_source_line_content(grid_api: dict) -> None:
    lines = grid_api["sourceLines"]
    assert lines[0] == {"dims": "1920 x 1080", "mp": "2.1 MP", "aspect": "16:9"}
    assert lines[1] == {"dims": "512 x 512", "mp": "0.26 MP", "aspect": "1:1"}
    assert lines[2]["aspect"] == "1000:1"  # degenerate but real


def test_source_line_is_null_when_there_is_nothing_trustworthy(grid_api: dict) -> None:
    """A zero/negative/missing dimension means the upstream image hasn't
    decoded yet (or there is none) -- draw nothing rather than "0 x 0", and
    keep the strip one line tall so text can't land outside the element."""
    for entry in grid_api["sourceLines"][3:]:
        assert entry is None


_SOURCE = (REPO_ROOT / "web" / "eps_image" / "resolution.js").read_text(encoding="utf-8")


def test_height_and_draw_agree_on_one_gate() -> None:
    # Both the height math and the draw must ask the same question, or the
    # second line can render outside the element box.
    assert "hasSourceLine(node)" in _SOURCE
    assert "readIncomingImageSize(node) !== null" in _SOURCE


def test_size_is_read_from_the_upstream_nodes_own_image() -> None:
    assert "getInputNode" in _SOURCE
    assert "naturalWidth" in _SOURCE and "naturalHeight" in _SOURCE


def test_connection_changes_and_slow_decodes_both_repaint() -> None:
    # Wiring/unwiring flips the line; a just-wired image is usually still
    # decoding when that fires, hence the self-cancelling probe.
    assert "onConnectionsChange" in _SOURCE
    assert "scheduleSourceProbe" in _SOURCE
    assert "clearTimeout(node._epsGrid.sourceProbe)" in _SOURCE


def _function_body(source_text: str, signature: str) -> str:
    """Body of a top-level ``function <signature> {`` up to its column-0
    closing brace -- the sibling JS test files' identical helper."""
    import re as _re

    start_match = _re.search(_re.escape(f"function {signature} {{") + r"\n", source_text)
    assert start_match, f"function {signature} {{ not found"
    start = start_match.end()
    end_match = _re.search(r"\n\}\n", source_text[start:])
    assert end_match, f"end of {signature} not found"
    return source_text[start : start + end_match.start()]


class TestV0610MultiImageAndLayout:
    """v0.61.0 source pins (FORMAT.md §6.5): the height-first widget
    migration, the cosmetic original-size hide, and the multi-image
    converge/reveal -- all closure-bound against a real litegraph node, so
    source-text pins per this file's convention."""

    @pytest.fixture(scope="class")
    def source(self) -> str:
        return (REPO_ROOT / "web" / "eps_image" / "resolution.js").read_text(encoding="utf-8")

    def test_migration_decides_from_the_incoming_file(self, source: str) -> None:
        body = _function_body(source, "attach(node)")
        # the stamp is set on every node, but the DECISION reads info --
        # deciding off node.properties would skip every migration, since
        # attach pre-stamps before configure runs.
        assert "node.properties[WIDGET_LAYOUT_PROP] = WIDGET_LAYOUT_CURRENT" in body
        decide = body.index("info?.properties?.[WIDGET_LAYOUT_PROP] !== WIDGET_LAYOUT_CURRENT")
        run_original = body.index("originalOnConfigureV61?.apply(this, arguments)")
        assert decide < run_original, "the file must be read BEFORE configure merges properties"
        assert "widthWidget.value = heightWidget.value" in body

    def test_original_size_hide_is_cosmetic_not_removal(self, source: str) -> None:
        body = _function_body(source, "applyOriginalSizeVisibility(node)")
        assert "removeOutput" not in body  # v0.61.0: real removal became unsafe
        assert "ensureOriginalSizeOutputs(node)" in body
        ensure = _function_body(source, "ensureOriginalSizeOutputs(node)")
        assert "node.addOutput(name, ORIGINAL_SIZE_TYPE)" in ensure
        # the shared draw-suppression now covers the pair too
        hidden = _function_body(source, "hiddenOutputNames(node)")
        assert "ORIGINAL_SIZE_NAMES" in hidden

    def test_converge_is_deferred_and_capped(self, source: str) -> None:
        schedule = _function_body(source, "scheduleImageConverge(node)")
        assert "setTimeout(" in schedule and "_epsResConvergeQueued" in schedule
        converge = _function_body(source, "convergeExtraImageInputs(node)")
        assert "Math.min(highest + 1, MAX_IMAGES)" in converge
        assert "node.inputs[idx].link == null" in converge  # only unwired extras removed

    def test_reveal_orders_pair_first_and_never_removes_wired(self, source: str) -> None:
        reveal = _function_body(source, "revealExtraOutputs(node)")
        assert reveal.index("ensureOriginalSizeOutputs(node)") < reveal.index("addOutput")
        assert "!isOutputConnected(node.outputs[idx])" in reveal


class TestCopyFromImageV0630:
    """v0.63.0 (owner ask 2026-08-14): a `copy from image` button above the
    size fields that writes the wired image's own pixel size into
    width/height -- and the serialization guard that makes a NON-TAIL
    widget safe to add at all."""

    @pytest.fixture(scope="class")
    def source(self) -> str:
        return (REPO_ROOT / "web" / "eps_image" / "resolution.js").read_text(encoding="utf-8")

    def test_button_label_is_the_owners_wording(self, source: str) -> None:
        assert "const COPY_FROM_IMAGE_LABEL = 'copy from image'" in source
        body = _function_body(source, "attachCopyFromImage(node)")
        assert "node.addWidget('button', COPY_FROM_IMAGE_LABEL" in body

    def test_button_sits_above_the_size_fields(self, source: str) -> None:
        body = _function_body(source, "attachCopyFromImage(node)")
        assert "node.widgets.unshift(button)" in body
        # ...and it is attached last, so the unshift lands above every
        # widget rather than racing the presets cluster.
        attach = _function_body(source, "attach(node)")
        assert attach.index("attachPresetsUi(node)") < attach.index("attachCopyFromImage(node)")

    def test_copy_reuses_the_live_source_read_and_the_one_size_writer(
        self, source: str
    ) -> None:
        """Same read the source line uses (so the two can never disagree),
        and the same writeSize() every drag goes through."""
        body = _function_body(source, "attachCopyFromImage(node)")
        assert "readIncomingImageSize(node)" in body
        assert "writeSize(node, size.width, size.height)" in body
        # EXACT pixels -- "copy" means copy; multiple_of still rounds at run
        # time exactly as it would for a hand-typed size.
        assert "multiple_of" not in body

    def test_both_failure_modes_toast_differently(self, source: str) -> None:
        """§6.3 never-silent: nothing wired vs wired-but-not-decoded need
        different fixes, so they get different messages."""
        body = _function_body(source, "attachCopyFromImage(node)")
        assert "Wire an image into this node first." in body
        assert "hasn't loaded yet" in body
        assert "link != null" in body

    def test_non_tail_widget_is_made_format_safe_by_compacting_holes(
        self, source: str
    ) -> None:
        """THE reason §8 bans non-tail frontend widgets: litegraph
        SERIALIZES by array index (skipping serialize:false widgets, which
        leaves a hole) but CONFIGURES with a compacted counter. Rig-proven
        2026-08-14: a leading skipped widget turned [333, 777, ...] into
        [null, 333, 777, ...] and shifted every value on reload. Compacting
        the hole on the way out makes the saved array byte-identical to a
        button-less build's -- old saves load here, and saves made here
        still load on an older build."""
        body = _function_body(source, "attachCopyFromImage(node)")
        # BOTH flags: options.serialize gates the API PROMPT, widget.serialize
        # gates the WORKFLOW file (executionUtil.ts says so in as many words).
        # Rig-caught: with only the latter, every queued prompt carried a
        # phantom `"copy from image": null` input.
        assert "button.serialize = false" in body
        assert "button.options = { ...(button.options || {}), serialize: false }" in body
        assert "const originalOnSerialize = node.onSerialize" in body
        assert "originalOnSerialize?.apply(this, arguments)" in body
        # `i in values` distinguishes a HOLE from a genuinely stored null.
        assert "values.filter((_, i) => i in values)" in body
