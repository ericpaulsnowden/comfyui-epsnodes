"""Frontend tests for the EPS Distributor frontend (FORMAT.md section 6.11).

``web/eps_image/distributor.js`` factors its toggle-box geometry and its
toggles/property contract into PURE exported functions (`toggleBoxRect`,
`clampOutputsCount`, `clampVisibleCount`, `parseToggles`, `isSlotEnabled`,
`outputName`/`parseOutputSlot`) precisely so this file can drive them under
Node without a litegraph node stub -- the exact convention
``tests/test_resolution_grid_js.py`` established (that file's own docstring:
the module's only import is ComfyUI's ``scripts/app.js``, resolved against
the served layout, so the fixture mirrors that directory depth in a tmp dir
and byte-copies the real module in). ``distributor.js`` has exactly one
import for the same reason -- ``../../../scripts/app.js``, used solely for
the wired-refusal toast -- so the served-layout mirroring here is genuinely
load-bearing: get the relative depth wrong and Node cannot resolve the module
at all, which makes this fixture a regression test for the import path
itself, not just for the geometry.

The remaining logic -- tail-only removal order, the wired-output refusal
path, the `_floatingLinks`/`getOutputPos` choices, the hidden-widget trick,
and Toggle All's wiring over every visible (not just connected) output -- is
structural/closure-bound (it only runs inside `attach()` against a real
litegraph node) and has no browser harness to drive here, so it is pinned
via SOURCE-TEXT assertions instead, matching
``tests/test_frame_saver_paste_js.py``'s dual-fixture convention (that
file's own `frame_saver_source` fixture) for the same kind of code.

Skips cleanly when Node isn't installed; the LIVE mechanics (an actual
click reaching `onMouseDown`, the property panel, save/reload round-trip)
are verified on the rig, not here.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DISTRIBUTOR_JS = REPO_ROOT / "web" / "eps_image" / "distributor.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

#: Socket-local X positions to probe toggleBoxRect() at -- deliberately
#: spanning well below MIN_NODE_WIDTH (60) through a wide node (1000), since
#: the right-edge inequality below is a CONSTANT-margin guarantee (ROW_GAP >
#: 15 + ROW_BOX), not one that depends on the socket position -- probing a
#: wide matrix is a regression pin on that relationship, not a search for an
#: edge case that could fail it.
PROBE_SOCKET_X = [30, 60, 92, 107, 140, 191, 300, 512, 1000]
PROBE_SOCKET_Y = 48

#: (raw property value, expected clampOutputsCount() result). Mirrors the
#: backend contract: round to nearest int, clamp 1..16, non-finite -> the
#: default (3).
CLAMP_OUTPUTS_CASES = [
    (3, 3),
    (1, 1),
    (16, 16),
    (0, 1),
    (-5, 1),
    (99, 16),
    (16.4, 16),
    (1.4, 1),
    # `Number(None)` coerces to `0` (finite), so this hits the ordinary
    # clamp path -- not the non-finite/NaN fallback -- and clamps up to
    # MIN_OUTPUTS.
    (None, 1),
    # A genuinely non-finite input (a non-numeric string; `Number("abc")` is
    # `NaN`) is the one case that falls back to DEFAULT_VISIBLE_OUTPUTS.
    ("abc", 3),
]

#: (requested, highestWiredIndex, expected clampVisibleCount() result). The
#: clamp-up-to-highest-wired rule: never below the wired floor, but never
#: forced UP when the request is already at or above it.
CLAMP_VISIBLE_CASES = [
    (2, 5, 5),
    (16, 0, 16),
    (20, 0, 16),
    (20, 24, 16),
    (1, None, 1),
    (6, 3, 6),
    (1, 16, 16),
]

#: (current visible count, highestWiredIndex, expected growVisibleCount()).
#: The growing-outputs rule (v0.40.0, owner ask): keep exactly one spare
#: socket below the highest wired one, grow only, cap at MAX_OUTPUTS. Wiring
#: the LAST visible output is the case that has to reveal a new one -- that
#: is the whole feature -- and wiring a middle one must reveal nothing.
GROW_VISIBLE_CASES = [
    # nothing wired -> untouched (a fresh node keeps its default 3)
    (3, 0, 3),
    (1, 0, 1),
    (3, None, 3),
    # the feature: last visible slot just got wired -> one more appears
    (3, 3, 4),
    (1, 1, 2),
    (4, 4, 5),
    # a MIDDLE slot wired -> the spare already exists, nothing to do
    (3, 1, 3),
    (3, 2, 3),
    (8, 5, 8),
    # grows only, never shrinks: unwiring everything leaves the count alone
    (12, 1, 12),
    # a jump (paste of a workflow wired straight to a high slot) catches up
    # in one step rather than one socket at a time
    (3, 9, 10),
    # the ceiling: at MAX_OUTPUTS there is no spare left to reserve, so the
    # last socket is itself wirable rather than permanently empty
    (16, 16, 16),
    (16, 15, 16),
    (15, 15, 16),
    # a hand-edited/garbage property value can't grow past the ceiling
    (99, 16, 16),
]

#: (rawValue, expected parsed map). parseToggles must never throw.
PARSE_TOGGLES_CASES = [
    ("{}", {}),
    ('{"out_3": false}', {"out_3": False}),
    ("not json", {}),
    ("[]", {}),
    ("null", {}),
    ("", {}),
]

#: (label, JS expression building the `output`, expected isOutputConnected()).
#: Mirrors `LGraphCanvas.ts`'s `hasRelevantOutputLinks`, which unions BOTH
#: `output.links` and `output._floatingLinks`. The floating-only rows are the
#: regression pin: a `.links`-only check reads a link that is still mid-drag
#: as UNCONNECTED, and `applyVisibleOutputCount` would then `removeOutput()`
#: the socket under it. Kept identical to tests/test_resolution_grid_js.py's
#: list -- resolution.js carries the same function (ported v0.34.0) and the
#: two are meant to stay in lockstep.
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

#: (map, name, expected enabled). The exact `!== false` rule -- any value
#: other than the literal boolean `false` reads as enabled.
IS_ENABLED_CASES = [
    ({}, "out_1", True),
    ({"out_1": False}, "out_1", False),
    ({"out_1": True}, "out_1", True),
    ({"out_1": 0}, "out_1", True),
    ({"out_1": None}, "out_1", True),
    ({"out_1": "false"}, "out_1", True),
]

#: `applyVisibleOutputCount`'s declaration, as the source-structure tests
#: below have to match it verbatim. `grow` is opt-in per call path (see
#: test_growth_is_opt_in_per_call_path).
APPLY_SIGNATURE = "applyVisibleOutputCount(node, { grow = false } = {})"

#: Custom-label lengths to probe the renamable-output geometry at. 0 and 5
#: are the un-renamed baseline (`out_N`); the long ones are what a real
#: rename ("upscale branch", "final save for the client") looks like.
LABEL_LENGTHS = [0, 5, 11, 12, 20, 40, 120]

PROBE_JS = """
import * as d from './extensions/comfyui-epsnodes/eps_image/distributor.js'

const socketXs = %(socket_xs)s
const socketY = %(socket_y)s
const invalidNames = ['image_1', 'out_', 'outx', '', null, undefined, 5]

const out = {
  exports: {
    hasInit: typeof d.init === 'function',
    hasAttach: typeof d.attach === 'function',
    hasLoadedGraphNode: typeof d.loadedGraphNode === 'function'
  },
  constants: {
    classId: d.CLASS_ID,
    maxOutputs: d.MAX_OUTPUTS,
    minOutputs: d.MIN_OUTPUTS,
    propOutputs: d.PROP_OUTPUTS,
    defaultVisible: d.DEFAULT_VISIBLE_OUTPUTS,
    togglesWidgetName: d.TOGGLES_WIDGET_NAME,
    rowBox: d.ROW_BOX,
    rowGap: d.ROW_GAP,
    minNodeWidth: d.MIN_NODE_WIDTH
  },
  outputNames: Array.from({ length: d.MAX_OUTPUTS }, (_, i) => d.outputName(i + 1)),
  parseRoundTrip: [1, 2, 16].map((n) => d.parseOutputSlot(d.outputName(n))),
  parseInvalid: invalidNames.map((v) => d.parseOutputSlot(v)),
  clampOutputs: %(clamp_outputs_inputs)s.map((v) => d.clampOutputsCount(v)),
  clampVisible: %(clamp_visible_inputs)s.map(([req, wired]) => d.clampVisibleCount(req, wired)),
  growVisible: %(grow_visible_inputs)s.map(([now, wired]) => d.growVisibleCount(now, wired)),
  parseToggles: %(parse_toggles_inputs)s.map((v) => d.parseToggles(v)),
  isEnabled: %(is_enabled_inputs)s.map(([map, name]) => d.isSlotEnabled(map, name)),
  linkChecks: [%(link_cases)s].map((output) => d.isOutputConnected(output)),
  boxRects: socketXs.map((socketX) => {
    const rect = d.toggleBoxRect(socketX, socketY)
    return { socketX, boxX: rect.x, boxY: rect.y, w: rect.w, h: rect.h }
  }),
  // Renamed outputs: the label is drawn LEFTWARD from the dot, so a long
  // one must push the box further left -- never let it drift right, under
  // the wire-drag box.
  labelledRects: %(label_lengths)s.map((len) => {
    const label = 'x'.repeat(len)
    const reach = d.outputTextReach(label)
    const rect = d.toggleBoxRect(600, socketY, reach)
    return { len, reach, boxX: rect.x, w: rect.w }
  })
}

process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def distributor_api(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Runs the probe against the REAL distributor.js in a served-layout tmp
    dir (see module docstring) and returns its JSON output."""
    layout = tmp_path_factory.mktemp("web_root")

    module_dir = layout / "extensions" / "comfyui-epsnodes" / "eps_image"
    module_dir.mkdir(parents=True)
    shutil.copyfile(DISTRIBUTOR_JS, module_dir / "distributor.js")

    # distributor.js's single import -- `../../../scripts/app.js`, used only
    # for its toast. Stubbed exactly as tests/test_resolution_grid_js.py does
    # it, which makes the relative-import DEPTH part of what this test proves:
    # get it wrong and Node fails to resolve the module at all.
    scripts = layout / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "app.js").write_text("export const app = {}\n", encoding="utf-8")

    clamp_visible_inputs = [[req, wired] for req, wired, _ in CLAMP_VISIBLE_CASES]
    grow_visible_inputs = [[now, wired] for now, wired, _ in GROW_VISIBLE_CASES]
    probe = layout / "probe.mjs"
    probe.write_text(
        PROBE_JS
        % {
            "socket_xs": json.dumps(PROBE_SOCKET_X),
            "socket_y": PROBE_SOCKET_Y,
            "clamp_outputs_inputs": json.dumps([v for v, _ in CLAMP_OUTPUTS_CASES]),
            "clamp_visible_inputs": json.dumps(clamp_visible_inputs),
            "grow_visible_inputs": json.dumps(grow_visible_inputs),
            "parse_toggles_inputs": json.dumps([v for v, _ in PARSE_TOGGLES_CASES]),
            "is_enabled_inputs": json.dumps([[m, n] for m, n, _ in IS_ENABLED_CASES]),
            # Built as raw JS, not JSON: `_floatingLinks` is a real `Set`, so
            # the case inputs have to be constructed in the probe itself.
            "link_cases": ", ".join(js for _, js, _ in OUTPUT_LINK_CASES),
            "label_lengths": json.dumps(LABEL_LENGTHS),
        },
        encoding="utf-8",
    )

    result = subprocess.run(
        [NODE, str(probe)], capture_output=True, text=True, timeout=60, cwd=layout
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def distributor_source() -> str:
    """Raw text of distributor.js -- for SOURCE-STRUCTURE assertions about
    code that only runs inside attach() against a real litegraph node
    (tail-only removal order, the wired-refusal path, the hidden-widget
    trick, Toggle All's wiring), which this repo has no browser harness to
    drive -- see module docstring and test_frame_saver_paste_js.py's
    identical convention for the same class of code."""
    return DISTRIBUTOR_JS.read_text(encoding="utf-8")


def _function_body(source: str, signature: str) -> str:
    """The body of a top-level ``function <signature> {`` declaration, up to
    its closing brace at column 0 -- test_frame_saver_paste_js.py's identical
    helper (this file's own top-level functions follow the same 2-space
    internal indent / unindented closing brace convention)."""
    start_match = re.search(re.escape(f"function {signature} {{") + r"\n", source)
    assert start_match, f"function {signature} {{ not found"
    start = start_match.end()
    end_match = re.search(r"\n\}\n", source[start:])
    assert end_match, f"function {signature}'s closing brace not found"
    return source[start : start + end_match.start()]


def test_distributor_js_parses() -> None:
    """`node --check` -- the file must at minimum be valid ES module syntax."""
    result = subprocess.run(
        [NODE, "--check", str(DISTRIBUTOR_JS)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


def test_module_exports_the_extension_entry_points(distributor_api: dict) -> None:
    """web/eps_image.js consumes init()/attach(); no loadedGraphNode is
    exported (by design -- see module docstring's `attach()` section on why
    a right-click Property doesn't need one, unlike Image Grid/Frame Saver)."""
    assert distributor_api["exports"] == {
        "hasInit": True,
        "hasAttach": True,
        "hasLoadedGraphNode": False,
    }


# --------------------------------------------------------------- constants


def test_class_id_and_widget_names(distributor_api: dict) -> None:
    constants = distributor_api["constants"]
    assert constants["classId"] == "EPSDistributor"
    assert constants["togglesWidgetName"] == "toggles"


def test_output_count_property_name_default_and_bounds(distributor_api: dict) -> None:
    """The `Outputs` right-click Property: default 3, clamped 1..16 (FORMAT.md
    section 6.11 / the roadmap's decision). The ceiling went 8 -> 16 in
    v0.40.0 alongside auto-growth; see `test_frontend_ceiling_matches_backend`
    for the constraint that actually pins the number."""
    constants = distributor_api["constants"]
    assert constants["propOutputs"] == "Outputs"
    assert constants["defaultVisible"] == 3
    assert constants["minOutputs"] == 1
    assert constants["maxOutputs"] == 16


def test_frontend_ceiling_matches_backend(distributor_api: dict) -> None:
    """`MAX_OUTPUTS` is declared in TWO places -- nodes_distributor.py (which
    builds the real RETURN_TYPES tuple) and distributor.js (which decides how
    far auto-growth may reveal). They MUST agree: too low in the frontend caps
    growth below sockets that exist, too high reveals sockets the backend never
    declared, and a link into one of those serializes an `origin_slot` core
    cannot resolve. Imported here rather than re-parsed so this fails the
    moment either side moves alone."""
    from eps_image.nodes_distributor import MAX_OUTPUTS as BACKEND_MAX

    assert distributor_api["constants"]["maxOutputs"] == BACKEND_MAX


def test_min_node_width_floor_is_present(distributor_api: dict) -> None:
    """A width floor exists and is a plain positive number (the v0.30.3
    house rule) -- not pinned to switcher.js's exact 200 beyond a sane
    lower bound, so this doesn't need updating if the shared floor ever
    gets retuned."""
    assert isinstance(distributor_api["constants"]["minNodeWidth"], int | float)
    assert distributor_api["constants"]["minNodeWidth"] >= 150


# ---------------------------------------------------------- output naming


def test_output_name_matches_backend_return_names(distributor_api: dict) -> None:
    from eps_image.nodes_distributor import EPSDistributor

    assert distributor_api["outputNames"] == list(EPSDistributor.RETURN_NAMES)


def test_parse_output_slot_round_trips(distributor_api: dict) -> None:
    assert distributor_api["parseRoundTrip"] == [1, 2, 16]


def test_parse_output_slot_rejects_non_matching_names(distributor_api: dict) -> None:
    assert distributor_api["parseInvalid"] == [None] * 7


# -------------------------------------------------------------- the clamp


def test_clamp_outputs_count(distributor_api: dict) -> None:
    pairs = zip(CLAMP_OUTPUTS_CASES, distributor_api["clampOutputs"], strict=True)
    for (given, expected), got in pairs:
        assert got == expected, f"clampOutputsCount({given!r}) -> {got!r}, wanted {expected!r}"


def test_clamp_visible_count_never_hides_a_wired_slot(distributor_api: dict) -> None:
    """The generalized (count-based) version of resolution.js's boolean
    refuse-and-revert: the effective count is never below the highest wired
    slot, but is also never forced UP when the plain request already covers
    it (FORMAT.md section 6.11)."""
    pairs = zip(CLAMP_VISIBLE_CASES, distributor_api["clampVisible"], strict=True)
    for (req, wired, expected), got in pairs:
        msg = f"clampVisibleCount({req!r}, {wired!r}) -> {got!r}, wanted {expected!r}"
        assert got == expected, msg


def test_grow_visible_count_keeps_one_spare_socket(distributor_api: dict) -> None:
    """The growing-outputs rule (v0.40.0): wiring the last visible output
    reveals the next, so there is always one spare socket below the highest
    wired one -- EPS Switcher's feel, applied to outputs. Grow-only (never
    shrinks a socket the user has already seen) and capped at MAX_OUTPUTS,
    where the last socket is itself wirable rather than a permanent spare."""
    pairs = zip(GROW_VISIBLE_CASES, distributor_api["growVisible"], strict=True)
    for (now, wired, expected), got in pairs:
        msg = f"growVisibleCount({now!r}, {wired!r}) -> {got!r}, wanted {expected!r}"
        assert got == expected, msg


def test_grow_visible_count_never_shrinks(distributor_api: dict) -> None:
    """Stated as an invariant over the whole case list, independent of the
    hand-written expectations above: the result is never below the (range-
    clamped) current count. This is what protects a user-typed output rename
    -- shrinking would `removeOutput()` the socket carrying it."""
    for (now, wired, _expected), got in zip(
        GROW_VISIBLE_CASES, distributor_api["growVisible"], strict=True
    ):
        floor = min(16, max(1, round(now))) if isinstance(now, int | float) else 3
        assert got >= floor, f"growVisibleCount({now!r}, {wired!r}) shrank to {got!r}"


def test_grow_visible_count_stays_within_bounds(distributor_api: dict) -> None:
    """Growth can never step outside the property's own declared range."""
    for got in distributor_api["growVisible"]:
        assert 1 <= got <= 16


def test_growth_is_wired_through_both_hooks(distributor_source: str) -> None:
    """`wireOutputGrowth` must be installed from attach() and must chain BOTH
    litegraph hooks the mechanism needs: `configure` (for the `restoring`
    guard -- litegraph's own restore loop dispatches onConnectionsChange from
    inside its `this.outputs.entries()` iteration, so growing there would
    splice under a live iterator) and `onConnectionsChange` (the live path).
    Structural because it only runs against a real litegraph node."""
    assert "wireOutputGrowth(node)" in distributor_source, "not installed from attach()"
    body = _function_body(distributor_source, "wireOutputGrowth(node)")
    assert "node.configure = function" in body
    assert "node.onConnectionsChange = function" in body
    assert "state.restoring = true" in body
    assert "state.restoring = false" in body


def test_growth_defers_past_the_litegraph_call_frame(distributor_source: str) -> None:
    """The live path must SCHEDULE the pass, not run it synchronously:
    `disconnectOutput` dispatches `onConnectionsChange` before it returns, so
    a synchronous `node.outputs` mutation would splice the array while
    litegraph still holds a slot index into it (switcher.js's round-10
    dead-rewire finding, ported). The scheduling flag is what coalesces a
    drag's event burst into one pass."""
    body = _function_body(distributor_source, "wireOutputGrowth(node)")
    assert "setTimeout(" in body, "the live path must defer to the next macrotask"
    assert "state.growScheduled" in body, "burst coalescing flag missing"
    # ...and the deferred run must bail if the node left the graph meanwhile.
    assert "!target.graph" in body


def test_growth_ignores_the_isconnected_argument(distributor_source: str) -> None:
    """litegraph's restore loop passes `isConnected` HARDCODED true, even for
    a slot with no link. This file must therefore never branch on that
    argument -- it recomputes wiring from the slots themselves via
    `highestWiredSlot`, which is why a restore could not misgrow even if one
    got past the `restoring` guard."""
    body = _function_body(distributor_source, "wireOutputGrowth(node)")
    signature_line = next(line for line in body.split("\n") if "onConnectionsChange = " in line)
    assert "isConnected" in signature_line, "argument should still be named for clarity"
    used = [
        line
        for line in body.split("\n")
        if "isConnected" in line and "onConnectionsChange = " not in line
    ]
    assert not used, f"growth must not branch on isConnected: {used}"


def test_growth_never_masks_the_wired_refusal(distributor_source: str) -> None:
    """Order matters inside `applyVisibleOutputCount`, and this exact ordering
    is a REGRESSION PIN -- caught on the rig, not in review.

    Growing BEFORE the clamps looks natural and is wrong: lowering `Outputs`
    to 1 while `out_3` is wired then produced a grown value (4) that already
    covered the wired floor, so the `refused > rangeClamped` comparison came
    out false and the refusal went SILENT -- which is the original
    owner-reported bug. The refusal must therefore be derived from the STORED
    request, with growth applied after it."""
    body = _function_body(distributor_source, APPLY_SIGNATURE)
    assert "clampVisibleCount(stored, wiredMax)" in body, "refusal must read the stored request"
    refuse_at = body.index("clampVisibleCount(")
    grow_at = body.index("growVisibleCount(")
    assert refuse_at < grow_at, "the refusal must be computed before growth can mask it"
    assert "if (refused > rangeClamped)" in body
    assert "clampOutputsCount(stored)" in body, "the range clamp must also read the request"


def test_growth_is_opt_in_per_call_path(distributor_source: str) -> None:
    """Auto-growth applies ONLY to the live connection path. A number typed
    into the `Outputs` panel is an explicit instruction (and growing past it is
    what masked the refusal above), and a loaded workflow's saved output set is
    authoritative -- growing it on load would mutate a graph the user only
    opened. So the parameter defaults to off and exactly one caller sets it."""
    body = _function_body(distributor_source, APPLY_SIGNATURE)
    assert "grow ? growVisibleCount(" in body, "growth must be gated on the flag"
    opted_in = re.findall(r"applyVisibleOutputCount\([^)]*\{\s*grow:\s*true", distributor_source)
    assert len(opted_in) == 1, f"exactly one caller may opt in, found {len(opted_in)}"
    growth_body = _function_body(distributor_source, "wireOutputGrowth(node)")
    assert "applyVisibleOutputCount(target, { grow: true })" in growth_body


def test_apply_visible_output_count_persists_the_result(distributor_source: str) -> None:
    """The write-back must be gated on the STORED property value. Comparing the
    derived numbers to each other instead silently no-ops in the ordinary
    growth case -- they are equal whenever the clamps don't bite -- leaving the
    right-click panel showing a count one short of the sockets on screen."""
    body = _function_body(distributor_source, APPLY_SIGNATURE)
    assert "if (stored !== desired) node.properties[PROP_OUTPUTS] = desired" in body


def test_deferred_growth_skips_no_op_passes(distributor_source: str) -> None:
    """The live hook fires on every connect AND disconnect, but
    `applyVisibleOutputCount` re-derives the node height (`resyncSize`), so an
    unconditional pass would snap back a manual resize each time the user
    wires anything. The deferred run must therefore bail unless growth
    genuinely changes the count."""
    body = _function_body(distributor_source, "wireOutputGrowth(node)")
    guard = next(
        (line for line in body.split("\n") if "growVisibleCount(" in line and "return" in line),
        None,
    )
    assert guard, "deferred run must short-circuit when growth is a no-op"


def test_is_output_connected_counts_floating_links_too(distributor_api: dict) -> None:
    """The clamp above is only as good as what counts as "wired".
    `LGraphCanvas.ts`'s own `hasRelevantOutputLinks` unions `output.links`
    with `output._floatingLinks` (a link mid-drag), and so does this -- a
    `.links`-only check would let `applyVisibleOutputCount` `removeOutput()`
    a socket the user still has hold of. resolution.js's identical function
    is pinned by the same case list in tests/test_resolution_grid_js.py; the
    two stay in lockstep."""
    pairs = zip(OUTPUT_LINK_CASES, distributor_api["linkChecks"], strict=True)
    for (label, _js, expected), got in pairs:
        assert got is expected, f"isOutputConnected({label}) -> {got!r}, wanted {expected!r}"


# ------------------------------------------------------------- toggles map


def test_parse_toggles_never_throws_and_degrades_to_empty(distributor_api: dict) -> None:
    pairs = zip(PARSE_TOGGLES_CASES, distributor_api["parseToggles"], strict=True)
    for (given, expected), got in pairs:
        assert got == expected, f"parseToggles({given!r}) -> {got!r}, wanted {expected!r}"


def test_is_slot_enabled_uses_strict_not_equal_false(distributor_api: dict) -> None:
    """Pins the exact rule the roadmap and switcher.js both use: a slot is
    enabled unless its key is EXPLICITLY the boolean `false` -- any other
    value (including falsy ones like `0`/`null`, or the STRING `"false"`)
    still reads as enabled."""
    pairs = zip(IS_ENABLED_CASES, distributor_api["isEnabled"], strict=True)
    for (given_map, name, expected), got in pairs:
        msg = f"isSlotEnabled({given_map!r}, {name!r}) -> {got!r}, wanted {expected!r}"
        assert got is expected, msg


# --------------------------------------------------- toggle-box geometry
# The single most load-bearing assertion in this file: LGraphCanvas.ts's
# _processNodeClick outputs loop returns BEFORE node.onMouseDown is ever
# reached when `isInRectangle(x, y, socketX - 15, socketY - 10, 30, 20)`
# hits -- verified directly against this rig's installed
# comfyui_frontend_package 1.45.21 TS source (see distributor.js's module
# docstring for the exact quote). A toggle box whose right edge does not
# clear `socketX - 15` would silently start a wire-drag instead of toggling.


def test_toggle_box_clears_output_mousedown_boundary(distributor_api: dict) -> None:
    """The regression pin: at every probed socket X, the toggle box's right
    edge (`boxX + w`) must be strictly less than `socketX - 15` -- the left
    edge of `_processNodeClick`'s fixed output hit-box, which `return`s
    before `node.onMouseDown` is ever called. Pinning the INEQUALITY rather
    than a specific margin, per the round's direction -- ROW_GAP could
    change and this would still have to hold."""
    for entry in distributor_api["boxRects"]:
        right_edge = entry["boxX"] + entry["w"]
        boundary = entry["socketX"] - 15
        assert right_edge < boundary, (
            f"socketX={entry['socketX']}: toggle box right edge {right_edge} "
            f"does not clear the mousedown boundary {boundary} -- a click there "
            "would start a wire-drag instead of toggling"
        )


def test_renamed_output_never_pushes_the_toggle_under_the_wire_drag_box(
    distributor_api: dict,
) -> None:
    """Outputs are renamable (owner ask 2026-07-27), and a label is drawn
    LEFTWARD from the socket dot -- so a long one must move the toggle box
    further LEFT, never right. The hard boundary still applies at every
    length: right edge strictly left of `socketX - 15`."""
    socket_x = 600
    previous_box_x = None
    for entry in distributor_api["labelledRects"]:
        right_edge = entry["boxX"] + entry["w"]
        assert right_edge < socket_x - 15, (
            f"label of {entry['len']} chars: box right edge {right_edge} "
            f"does not clear {socket_x - 15}"
        )
        if previous_box_x is not None:
            assert entry["boxX"] <= previous_box_x, (
                f"a longer label ({entry['len']} chars) moved the box RIGHT "
                f"({previous_box_x} -> {entry['boxX']}), toward the label it "
                "is supposed to be avoiding"
            )
        previous_box_x = entry["boxX"]


def test_mixed_length_labels_share_one_aligned_column() -> None:
    """Owner ask 2026-07-27 ("Can the checkboxes ... line up when the text
    for the outputs are of different lengths"): drawRowToggles now feeds
    every row the LONGEST visible label's reach — one shared reach into the
    same-X sockets means one shared boxX, a straight column. Source-text
    pin, since drawRowToggles itself is canvas-bound; toggleBoxRect's own
    per-reach behavior (including the wire-drag clearance at any reach) is
    already covered by the tests above."""
    source = (REPO_ROOT / "web" / "eps_image" / "distributor.js").read_text(encoding="utf-8")
    assert "if (reach > maxReach) maxReach = reach" in source
    assert "toggleBoxRect(pos[0], pos[1], maxReach)" in source


def test_short_labels_keep_the_plain_row_gap(distributor_api: dict) -> None:
    """Up to the point where a label actually reaches it, the box stays at
    the plain ROW_GAP -- the fixed margin is the floor, not an addition."""
    row_gap = distributor_api["constants"]["rowGap"]
    row_box = distributor_api["constants"]["rowBox"]
    baseline = 600 - row_gap - row_box
    for entry in distributor_api["labelledRects"]:
        if entry["reach"] + 12 <= row_gap:
            assert entry["boxX"] == baseline, (
                f"label of {entry['len']} chars should not have moved the box"
            )


def test_toggle_box_geometry_shape(distributor_api: dict) -> None:
    """Sanity-checks the rect shape itself: fixed box size, vertically
    centered on the socket, independent of socket X."""
    row_box = distributor_api["constants"]["rowBox"]
    for entry in distributor_api["boxRects"]:
        assert entry["w"] == row_box
        assert entry["h"] == row_box
        assert entry["boxY"] == PROBE_SOCKET_Y - row_box / 2


# ------------------------------------------------------- source structure
# The pieces below only run inside attach() against a real litegraph node --
# no browser harness in this repo drives them, so they are pinned via
# SOURCE-TEXT assertions instead (test_frame_saver_paste_js.py's identical
# convention for the same class of code).


def test_tail_only_removal_is_strictly_lifo(distributor_source: str) -> None:
    """applyVisibleOutputCount must only ever remove entries strictly ABOVE
    the desired count (never a non-tail slot), sorted highest-array-index
    first -- resolution.js's/switcher.js's shared "removeOutput splices by
    position" rule (distributor.js's own module docstring)."""
    assert "entries.filter((entry) => entry.n > desired)" in distributor_source
    assert ".sort((a, b) => b.idx - a.idx)" in distributor_source


def test_wired_refusal_path_uses_clamp_visible_count(distributor_source: str) -> None:
    """The wired-output guard must run through clampVisibleCount (the pure,
    numerically-tested function above), not a separate ad-hoc comparison --
    so the rule pinned by test_clamp_visible_count_never_hides_a_wired_slot
    is what actually governs the real add/remove path, and a refusal is
    surfaced both ways (console.warn AND an on-node toast: the property number
    visibly snaps back, and a log-only message reads exactly like the silent
    refusal the owner originally reported as a bug)."""
    body = _function_body(distributor_source, APPLY_SIGNATURE)
    assert "clampVisibleCount(stored, wiredMax)" in body
    assert "refused > rangeClamped" in body
    assert "console.warn(" in body
    assert "toast(node, 'warn'" in body


def test_is_output_connected_checks_floating_links(distributor_source: str) -> None:
    """Mirrors LGraphCanvas's own hasRelevantOutputLinks: a link mid-drag
    (`_floatingLinks`) counts as wired too, not just a settled `.links`
    entry -- a miss here would let removeOutput() run on a socket the user
    still has a link on (deliberately stricter than resolution.js's
    `.links`-only version)."""
    body = _function_body(distributor_source, "isOutputConnected(output)")
    assert "output.links" in body
    assert "_floatingLinks" in body


def test_output_position_prefers_get_output_pos_with_fallback(distributor_source: str) -> None:
    """Uses the exact method _processNodeClick's own hit-test calls
    (`getOutputPos`), with a defensive `getConnectionPos` fallback for a
    fork that lacks it -- switcher.js's identical defensive-API-access
    posture."""
    body = _function_body(distributor_source, "outputLocalPos(node, idx)")
    assert "node.getOutputPos" in body
    assert "node.getConnectionPos(false, idx)" in body


def test_toggles_widget_is_hidden_not_removed(distributor_source: str) -> None:
    """The serialized-but-invisible bridge trick (FORMAT.md section 7.2):
    `.hidden = true`, never deleted/unset, so the value still round-trips
    through save/reload while drawing no on-canvas row -- and (per the
    module docstring's getWidgetOnPos finding) leaves the widget with no
    hit area to swallow a row-toggle click either."""
    body = _function_body(distributor_source, "hideTogglesWidget(node)")
    assert "widget.hidden = true" in body


def test_toggle_all_operates_over_every_visible_output_not_just_wired(
    distributor_source: str,
) -> None:
    """Unlike switcher.js's connected-only header, Toggle All here must
    cover every VISIBLE out_N regardless of wiring (module docstring): both
    allRowsState and toggleAllRows read outputEntries(node) directly, with
    no wired/connected-only filter reintroduced."""
    all_state_body = _function_body(distributor_source, "allRowsState(node)")
    toggle_all_body = _function_body(distributor_source, "toggleAllRows(node)")
    for body in (all_state_body, toggle_all_body):
        assert "outputEntries(node)" in body
        assert ".wired" not in body
        assert "filter" not in body


def test_min_width_guard_flag_present(distributor_source: str) -> None:
    """installMinWidth's own guard flag (frame_saver.js's identical
    self-contained-copy shape) -- namespaced to this module so it can never
    collide with another eps_image/*.js file's copy on the same node."""
    assert "__epsDistributorMinWidthInstalled" in distributor_source
