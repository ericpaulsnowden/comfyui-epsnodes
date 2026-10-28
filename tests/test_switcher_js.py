"""Frontend tests for EPS Image Switcher's generalization across four sibling
classes (FORMAT.md section 6.4's `EPSSwitcher`, plus `EPSModelSwitcher`,
`EPSClipSwitcher`, `EPSVaeSwitcher` -- backend classes landing in a parallel
change, out of scope here).

``web/eps_image/switcher.js`` now exports a module-level registry --
``SWITCHER_CLASSES`` -- as a plain constant precisely so this file can drive
it under Node without a litegraph node stub, the exact convention
``tests/test_distributor_js.py``/``tests/test_resolution_grid_js.py``
established (those files' own docstrings: the module's only import is
ComfyUI's ``scripts/app.js``, resolved against the served layout, so the
fixture mirrors that directory depth in a tmp dir and byte-copies the real
module in). ``switcher.js`` has exactly one import for the same reason --
``../../../scripts/app.js`` -- so the served-layout mirroring here is
genuinely load-bearing, matching the sibling files' fixtures exactly.

Everything else switcher.js does (growing sockets, per-row toggle geometry,
header tri-state, rename, the `toggles` JSON bridge) is structural/
closure-bound -- it only runs inside `attach()` against a real litegraph
node -- and has no browser harness to drive here, so it was never unit
tested from JS before this file (only pinned indirectly: the BACKEND side in
tests/test_switcher.py, and a couple of structural facts in
tests/test_vue_nodes_compat.py). This file does not attempt to backfill that
-- it pins ONLY the registry-generalization this session added, via
SOURCE-TEXT assertions for the closure-bound pieces (matching
tests/test_frame_saver_paste_js.py's dual-fixture convention for the same
class of code).

Skips cleanly when Node isn't installed; the LIVE mechanics (an actual
click, drag, rename, save/reload round-trip, on any of the four classes)
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
SWITCHER_JS = REPO_ROOT / "web" / "eps_image" / "switcher.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

#: The registry's ground truth (mirrors the task's mandatory shape exactly):
#: backend class id -> its growing-socket prefix / default input type.
PREFIXES: dict[str, str] = {
    "EPSSwitcher": "image",
    "EPSModelSwitcher": "model",
    "EPSClipSwitcher": "clip",
    "EPSVaeSwitcher": "vae",
}
TYPES: dict[str, str] = {
    "EPSSwitcher": "IMAGE",
    "EPSModelSwitcher": "MODEL",
    "EPSClipSwitcher": "CLIP",
    "EPSVaeSwitcher": "VAE",
}

#: (class name, candidate input name, expected inputRe.test() result) --
#: three ACCEPT shapes (low/typical/multi-digit N, plus the zero edge, which
#: the bare `\d+` group has always accepted -- unchanged by this rewrite)
#: and enough REJECT shapes to pin the exact `^<prefix>_(\d+)$` anchoring
#: (no bare prefix, no trailing underscore, non-digit suffix, empty string,
#: wrong case -- this pattern has never had the `i` flag -- and no
#: leading/trailing whitespace) that switcher.js's old single IMAGE_INPUT_RE
#: always enforced, now reproduced identically per class.
REGEX_CASES: list[tuple[str, str, bool]] = [
    case
    for class_name, prefix in PREFIXES.items()
    for case in (
        (class_name, f"{prefix}_1", True),
        (class_name, f"{prefix}_2", True),
        (class_name, f"{prefix}_37", True),
        (class_name, f"{prefix}_0", True),
        (class_name, prefix, False),
        (class_name, f"{prefix}_", False),
        (class_name, f"{prefix}_x", False),
        (class_name, "", False),
        (class_name, f"{prefix.upper()}_1", False),
        (class_name, f" {prefix}_1", False),
        (class_name, f"{prefix}_1 ", False),
    )
]

#: A class's regex must never accept a SIBLING class's own prefix -- e.g.
#: EPSModelSwitcher's `model_1` must not satisfy EPSSwitcher's `image_N`
#: pattern. Every ordered pair of distinct classes -- guards against a
#: copy-paste registry entry sharing another class's regex.
CROSS_PREFIX_CASES: list[tuple[str, str, bool]] = [
    (class_name, f"{other_prefix}_1", False)
    for class_name in PREFIXES
    for other_class_name, other_prefix in PREFIXES.items()
    if other_class_name != class_name
]

PROBE_JS = """
import * as s from './extensions/comfyui-epsnodes/eps_image/switcher.js'

const classNames = Object.keys(s.SWITCHER_CLASSES)
const registry = {}
for (const name of classNames) {
  const spec = s.SWITCHER_CLASSES[name]
  registry[name] = { prefix: spec.prefix, type: spec.type }
}

const REGEX_PROBES = %(regex_probes)s
const regexResults = REGEX_PROBES.map(([className, candidate]) => {
  const spec = s.SWITCHER_CLASSES[className]
  return spec ? spec.inputRe.test(candidate) : null
})

const out = {
  exports: {
    hasInit: typeof s.init === 'function',
    hasAttach: typeof s.attach === 'function',
    hasSwitcherClasses: typeof s.SWITCHER_CLASSES === 'object' && s.SWITCHER_CLASSES !== null
  },
  classNames,
  registry,
  regexResults
}

process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def switcher_api(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Runs the probe against the REAL switcher.js in a served-layout tmp
    dir (see module docstring) and returns its JSON output."""
    layout = tmp_path_factory.mktemp("web_root")

    module_dir = layout / "extensions" / "comfyui-epsnodes" / "eps_image"
    module_dir.mkdir(parents=True)
    shutil.copyfile(SWITCHER_JS, module_dir / "switcher.js")

    # switcher.js's single import -- `../../../scripts/app.js`, used only
    # for `app.canvas` in activeCanvas(). Stubbed exactly as
    # tests/test_distributor_js.py/test_resolution_grid_js.py do it, which
    # makes the relative-import DEPTH part of what this test proves: get it
    # wrong and Node fails to resolve the module at all.
    scripts = layout / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "app.js").write_text("export const app = {}\n", encoding="utf-8")

    regex_probes = [
        [class_name, candidate]
        for class_name, candidate, _expected in REGEX_CASES + CROSS_PREFIX_CASES
    ]
    probe = layout / "probe.mjs"
    probe.write_text(
        PROBE_JS % {"regex_probes": json.dumps(regex_probes)},
        encoding="utf-8",
    )

    result = subprocess.run(
        [NODE, str(probe)], capture_output=True, text=True, timeout=60, cwd=layout
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def switcher_source() -> str:
    """Raw text of switcher.js -- for SOURCE-STRUCTURE assertions about code
    that only runs inside attach() against a real litegraph node, which this
    repo has no browser harness to drive -- see module docstring and
    tests/test_frame_saver_paste_js.py's identical convention for the same
    class of code."""
    return SWITCHER_JS.read_text(encoding="utf-8")


def _function_body(source: str, signature: str) -> str:
    """The body of a top-level ``function <signature> {`` declaration (an
    ``export function ...`` counts too -- the search is substring-based), up
    to its closing brace at column 0. Identical helper to
    tests/test_distributor_js.py's own (this file's top-level functions
    follow the same 2-space internal indent / unindented closing brace
    convention)."""
    start_match = re.search(re.escape(f"function {signature} {{") + r"\n", source)
    assert start_match, f"function {signature} {{ not found"
    start = start_match.end()
    end_match = re.search(r"\n\}\n", source[start:])
    assert end_match, f"function {signature}'s closing brace not found"
    return source[start : start + end_match.start()]


def test_switcher_js_parses() -> None:
    """`node --check` -- the file must at minimum be valid ES module syntax."""
    result = subprocess.run(
        [NODE, "--check", str(SWITCHER_JS)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


def test_module_exports_entry_points_and_registry(switcher_api: dict) -> None:
    """web/eps_image.js consumes init()/attach() unchanged; SWITCHER_CLASSES
    is a new named export added solely so this file can drive it under Node
    -- eps_image.js only ever destructures init/attach, so this is additive
    and does not change what it consumes."""
    assert switcher_api["exports"] == {
        "hasInit": True,
        "hasAttach": True,
        "hasSwitcherClasses": True,
    }


# --------------------------------------------------------------- registry


def test_registry_has_exactly_the_four_classes(switcher_api: dict) -> None:
    assert set(switcher_api["classNames"]) == set(PREFIXES)


def test_registry_prefix_and_type_pairs(switcher_api: dict) -> None:
    expected = {name: {"prefix": PREFIXES[name], "type": TYPES[name]} for name in PREFIXES}
    assert switcher_api["registry"] == expected


def test_the_old_single_class_constants_are_fully_replaced(switcher_source: str) -> None:
    """The mandatory-shape rewrite REPLACES `CLASS_ID` + `IMAGE_INPUT_RE`
    with `SWITCHER_CLASSES` -- pin that the old constants are actually gone
    (not left dangling alongside the registry as dead code, or worse, a
    second source of truth that could drift from it). A couple of comments
    still NAME `IMAGE_INPUT_RE` in prose (explaining what it used to be) --
    checking for the `const` declarations specifically, not the bare
    identifier, avoids a false failure on those."""
    assert "const CLASS_ID" not in switcher_source
    assert "const IMAGE_INPUT_RE" not in switcher_source
    assert "export const SWITCHER_CLASSES = {" in switcher_source


# -------------------------------------------------------- per-prefix regex


def test_per_prefix_regex_accept_reject(switcher_api: dict) -> None:
    """Each class's precompiled `inputRe` accepts `<prefix>_<digits>` and
    rejects every anchoring/case/shape variant the original IMAGE_INPUT_RE
    also rejected -- ported per class, not just for image."""
    got_slice = switcher_api["regexResults"][: len(REGEX_CASES)]
    for (class_name, candidate, expected), got in zip(REGEX_CASES, got_slice, strict=True):
        msg = f"{class_name}'s inputRe.test({candidate!r}) -> {got!r}, wanted {expected!r}"
        assert got is expected, msg


def test_cross_prefix_names_are_rejected(switcher_api: dict) -> None:
    """A class's inputRe must never accept a SIBLING class's own prefix."""
    got_slice = switcher_api["regexResults"][len(REGEX_CASES) :]
    for (class_name, candidate, expected), got in zip(CROSS_PREFIX_CASES, got_slice, strict=True):
        msg = f"{class_name}'s inputRe.test({candidate!r}) -> {got!r}, wanted {expected!r}"
        assert got is expected, msg


def test_frontend_image_pattern_matches_the_backend_pattern() -> None:
    """EPSSwitcher's own `image_N` matching must still agree with the
    backend's `_IMAGE_INPUT_PATTERN` (nodes_switcher.py) after this rewrite
    -- unlike the OTHER three classes (whose own backend modules land in a
    parallel change and are out of scope here), EPSSwitcher's backend
    already exists and is safe to cross-check directly, and this is exactly
    the kind of drift the rewrite must not introduce (hard requirement:
    EPSSwitcher's behavior is byte-for-byte unchanged)."""
    from eps_image.nodes_switcher import _IMAGE_INPUT_PATTERN

    image_cases = [case for case in REGEX_CASES if case[0] == "EPSSwitcher"]
    assert image_cases, "sanity: REGEX_CASES must actually cover EPSSwitcher"
    for _class_name, candidate, expected in image_cases:
        backend_says = bool(_IMAGE_INPUT_PATTERN.fullmatch(candidate))
        assert backend_says == expected, (
            f"backend _IMAGE_INPUT_PATTERN and frontend inputRe disagree on "
            f"{candidate!r}: backend={backend_says}, frontend={expected}"
        )


# ------------------------------------------------------- source structure
# The pieces below only run inside attach() against a real litegraph node --
# no browser harness in this repo drives them, so they are pinned via
# SOURCE-TEXT assertions instead (test_frame_saver_paste_js.py's identical
# convention for the same class of code).


def test_attach_gates_on_the_registry_not_a_hardcoded_class_id(switcher_source: str) -> None:
    body = _function_body(switcher_source, "attach(node)")
    assert "switcherSpecOf(node)" in body
    assert "CLASS_ID" not in body


def test_image_input_entries_gates_on_the_registry(switcher_source: str) -> None:
    """imageInputEntries() is the single most-reused helper (pruneToggles,
    convergeImageInputs, connectedImageEntries, drawRowToggles, rowAtLocalY
    all go through it) -- it must resolve the node's own spec and use its
    precompiled regex, with a null-spec short-circuit rather than crashing
    or falling back to a hardcoded pattern."""
    body = _function_body(switcher_source, "imageInputEntries(node)")
    assert "switcherSpecOf(node)" in body
    assert "spec.inputRe.exec(input.name)" in body
    assert "if (!spec) return entries" in body


def test_add_image_input_names_the_socket_from_the_registry_prefix(switcher_source: str) -> None:
    """Input NAMES stay `<prefix>_N` -- the one hardcoded `` `image_${n}` ``
    template literal this file used to have must now build its prefix from
    the node's own registry entry."""
    body = _function_body(switcher_source, "addImageInput(node, n, template)")
    assert "switcherSpecOf(node)" in body
    assert "`${prefix}_${n}`" in body


def test_add_image_input_falls_back_to_the_registry_type(switcher_source: str) -> None:
    """addImageInput's template fallback -- the one hardcoded `'IMAGE'`
    default this file used to have -- must now come from the node's own
    registry entry (`spec.type`), not a literal `'IMAGE'` used
    unconditionally. The bare `'IMAGE'` literal is still allowed to remain
    as the LAST-RESORT fallback for a node somehow not in the registry
    (defensive parity with the rest of this file's style)."""
    body = _function_body(switcher_source, "addImageInput(node, n, template)")
    assert "spec.type" in body
    assert "spec ? spec.type : 'IMAGE'" in body


def test_growth_live_path_gate_uses_the_registry_regex(switcher_source: str) -> None:
    """The live onConnectionsChange path's own direct IMAGE_INPUT_RE.test
    call -- one of the three direct call sites the old constant had -- must
    also read the node's own registry entry. The pre-existing Round-10
    dead-rewire-fix deferral (setTimeout + restoring guard) must be fully
    intact alongside it -- unchanged by this rewrite per the hard
    byte-for-byte-EPSSwitcher-behavior requirement."""
    body = _function_body(switcher_source, "wireImageInputGrowth(node)")
    assert "switcherSpecOf(node)" in body
    assert "spec.inputRe.test(inputOrOutput?.name" in body
    assert "setTimeout(" in body
    assert "state.restoring = true" in body
    assert "state.restoring = false" in body


def test_row_rename_gate_uses_the_registry_regex(switcher_source: str) -> None:
    """onInputDblClick's rename gate was the other direct IMAGE_INPUT_RE
    call site -- must also read the node's own registry entry."""
    body = _function_body(switcher_source, "wireRowRename(node)")
    assert "switcherSpecOf(node)" in body
    assert "spec.inputRe.test(input.name" in body


def test_toggles_widget_name_is_a_single_shared_constant(switcher_source: str) -> None:
    """The `toggles` widget name stays `'toggles'` for every class -- not
    parameterized per prefix -- per the task's explicit requirement."""
    assert "const TOGGLES_WIDGET_NAME = 'toggles'" in switcher_source
    assert switcher_source.count("TOGGLES_WIDGET_NAME") >= 2  # declared + used


def test_header_toggle_all_label_uses_a_registry_derived_noun(switcher_source: str) -> None:
    """The 'no images connected' header text is class-specific user-visible
    copy -- generalized via the registry's own prefix (naive pluralized) so
    it reads correctly for the three new classes too, while reproducing the
    ORIGINAL literal exactly for EPSSwitcher ('image' + 's' == 'images')."""
    body = _function_body(switcher_source, "addHeaderWidget(node)")
    assert "switcherSpecOf(node)" in body
    assert "`${spec.prefix}s`" in body
    assert "`Toggle All (no ${noun} connected)`" in body


def test_header_label_reproduces_the_original_string_for_eps_switcher() -> None:
    """String-level proof (not just source-text pin) that the naive
    pluralization reproduces the ORIGINAL hardcoded 'images' noun exactly
    for EPSSwitcher -- the one class where byte-for-byte behavior is a hard
    requirement."""
    assert f"{PREFIXES['EPSSwitcher']}s" == "images"


def test_hidden_toggles_warning_names_the_actual_node_class(switcher_source: str) -> None:
    """hideTogglesWidget's console.warn used to hardcode 'EPSSwitcher' --
    it must now name whichever class the node actually is, or an
    EPSModelSwitcher missing its widget would misleadingly warn about
    'EPSSwitcher'. For EPSSwitcher itself the interpolated string still
    reads identically to the old literal (nodeClassOf(node) === 'EPSSwitcher')."""
    body = _function_body(switcher_source, "hideTogglesWidget(node)")
    assert "nodeClassOf(node)" in body
    assert "EPSSwitcher node is missing" not in body


def test_switcher_js_has_no_window_level_listeners_to_convert(switcher_source: str) -> None:
    """FORMAT.md section 7.5's capture-phase rule applies to window-level
    gesture listeners; switcher.js has none (all interaction is dispatched
    through litegraph's own onDrawForeground/onMouseDown/onInputDblClick/
    onDblClick hooks, never a raw DOM listener), unlike notebook.js/
    resolution.js (tests/test_vue_nodes_compat.py). This pins that fact so a
    future window.addEventListener added here doesn't slip in without also
    being reviewed for capture-phase (`{ capture: true }` + matching
    removeEventListener flag)."""
    assert "window.addEventListener(" not in switcher_source


def test_row_toggles_share_one_aligned_column(switcher_source: str) -> None:
    """Owner ask 2026-08-09 ("keep the checkboxes aligned with each other
    ... aligned with the checkbox furthest to the right, not randomly
    spaced based on the string length"): drawRowToggles used to compute
    boxX PER ROW from that row's own displayed-text width, so mixed-length
    socket names staggered. It now takes the MAX required x across the
    drawn rows and draws every box there -- the same one-aligned-column fix
    distributor.js already carries for its output side (its own
    test_mixed_length_labels_share_one_aligned_column). Source-text pin:
    drawRowToggles is canvas-bound, so there is no harness to drive it.

    Taking the max is SAFE, not a compromise: each row's requiredX exists
    only to clear litegraph's own input hit-region for THAT row, and the
    max is >= every row's own requirement."""
    body = _function_body(switcher_source, "drawRowToggles(node, ctx)")
    assert "requiredX" in body
    assert "if (row.requiredX > ownRequiredX) ownRequiredX = row.requiredX" in body
    # ONE boxX resolved before the draw loop, then reused for every row.
    draw_call = body.index("drawToggleBox(ctx, boxX, boxY, ROW_BOX")
    assert body.index("if (row.requiredX > ownRequiredX)") < draw_call
    assert body.index("const boxX = sharedColumnX(node, ownRequiredX)") < draw_call
    # The per-row recompute inside the draw loop is what regressed here.
    assert "const boxX = Math.max(inputHitWidth" not in body


def test_every_switcher_in_the_graph_shares_one_column(switcher_source: str) -> None:
    """Owner ask 2026-08-14, the follow-up to the above: aligning each
    node's OWN rows still left a stack of switchers ragged, because every
    node picked its column from its own longest label (rig-measured: four
    270px-wide switchers at x = 95 / 144 / 186 / 92). Each node now
    publishes its requirement and takes the MAX across every switcher in
    the SAME graph. (v0.68.1: the walk moved into `graphColumnBounds(graph)`,
    memoized per canvas frame -- see
    test_graph_column_walk_is_memoized_per_canvas_frame; sharedColumnX is
    now the two clamps over its node-independent result.)"""
    body = _function_body(switcher_source, "drawRowToggles(node, ctx)")
    assert "const boxX = sharedColumnX(node, ownRequiredX)" in body
    shared = _function_body(switcher_source, "sharedColumnX(node, ownRequiredX)")
    assert "const bounds = graphColumnBounds(node.graph)" in shared
    assert "Math.max(ownRequiredX, bounds.need)" in shared
    walk = _function_body(switcher_source, "graphColumnBounds(graph)")
    # Same graph only -- subgraphs / other tabs must not couple.
    assert "graph?._nodes" in walk
    assert "if (!switcherSpecOf(other)) continue" in walk
    assert "const otherNeed = columnNeedOf(other)" in walk
    assert "if (otherNeed > need) need = otherNeed" in walk


def test_column_requirement_is_pure_so_offscreen_switchers_still_count(
    switcher_source: str,
) -> None:
    """litegraph only calls onDrawForeground for VISIBLE nodes, so a
    publish-as-you-draw scheme would omit every switcher scrolled out of
    view and the column would jump as nodes entered the viewport. The
    requirement is instead derived from labels + wiring alone -- no ctx,
    no draw -- so all switchers agree within a single frame."""
    need = _function_body(switcher_source, "columnNeedOf(node)")
    assert "ctx" not in need
    assert "getConnectionPos" not in need
    assert "if (!entry.connected) continue" in need
    assert "return need" in need
    # ...and no cross-frame state survives to go stale.
    assert "__epsSwitcherColumnNeed" not in switcher_source
    assert "__epsSwitcherColumnDrawn" not in switcher_source


def test_shared_column_yields_to_clearance_and_the_output_edge(
    switcher_source: str,
) -> None:
    """Priority order, both directions: the shared column may never push a
    box past this node's output-label zone (a narrow sibling would land
    under its own outputs), and may never drop below the node's own
    required clearance -- a box inside litegraph's input hit region starts
    a link-drag instead of toggling, so clearance is functional and wins
    over both."""
    shared = _function_body(switcher_source, "sharedColumnX(node, ownRequiredX)")
    assert "return Math.max(ownRequiredX, Math.min(need, ceiling))" in shared
    # The CEILING is shared too: a per-node ceiling puts the boxes back at
    # different x the moment clamping binds (rig-measured: 200/207/214,
    # because each node's outputs are named differently). v0.68.1: the walk
    # that gathers it lives in graphColumnBounds; the node's OWN ceiling is
    # still min'd in here.
    assert "Math.min(outputSafeColumnX(node), bounds.ceiling)" in shared
    walk = _function_body(switcher_source, "graphColumnBounds(graph)")
    assert "if (otherCeiling < ceiling) ceiling = otherCeiling" in walk
    # ...and a switcher with no boxes neither raises nor constrains it.
    assert "if (otherNeed === 0) continue" in walk
    ceiling = _function_body(switcher_source, "outputSafeColumnX(node)")
    # litegraph's own length*7 approximation, NOT ctx.measureText -- the
    # mirror is what keeps the box clear of ITS regions.
    assert "widestLabel * OUTPUT_LABEL_CHAR_W" in ceiling
    assert "measureText" not in ceiling
    assert "Math.max(ROW_MIN_X, width - ROW_BOX - reserve)" in ceiling


def test_unwired_switcher_contributes_nothing_to_the_shared_column(
    switcher_source: str,
) -> None:
    """A switcher with no wired rows draws no boxes and must not drag the
    column: columnNeedOf returns 0 for it by construction."""
    need = _function_body(switcher_source, "columnNeedOf(node)")
    assert "let need = 0" in need
    body = _function_body(switcher_source, "drawRowToggles(node, ctx)")
    assert "if (drawn.length === 0) {" in body


def test_high_low_pairing_frontend_pins(switcher_source: str) -> None:
    """v0.66.0 (owner spec): pairing is a per-node property on the MODEL
    switcher only; lows never match inputRe (no checkbox/rename/column of
    their own -- the row's one toggle governs the pair)."""
    assert "EPSModelSwitcher: { prefix: 'model', type: 'MODEL', pairable: true }" in switcher_source
    assert "const PROP_HIGH_LOW = 'High/low pairs'" in switcher_source
    assert "if (spec.pairable) spec.lowInputRe" in switcher_source
    # every high converge drags the pair machinery with it -- one choke point
    body = _function_body(switcher_source, "convergeImageInputs(node)")
    assert "convergePairInputs(node)" in body
    assert "convergePairOutput(node)" in body


def test_pair_moves_fix_link_slots_and_off_refuses_wired_lows(switcher_source: str) -> None:
    """litegraph stores link targets by POSITION: any insert/move must fix
    target_slot for every later input (removeInput has its own fixup;
    insertion/move have none built in). And the property can never
    silently orphan a wire -- OFF with a wired low reverts + toasts
    (resolution.js's hide-refusal posture)."""
    for fn in ("insertInputAt(node, name, type, at)", "moveInput(node, from, to)"):
        body = _function_body(switcher_source, fn)
        assert "link.target_slot = i" in body
    pair_in = _function_body(switcher_source, "convergePairInputs(node)")
    assert "node.properties[PROP_HIGH_LOW] = true" in pair_in
    assert "Unwire the low-model sockets" in pair_in
    pair_out = _function_body(switcher_source, "convergePairOutput(node)")
    assert "Unwire the models_low output" in pair_out
    # the tail output is a TRUE add/remove -- §8's one safe removal spot
    assert "node.addOutput(LOW_OUTPUT_NAME, spec.type)" in pair_out
    assert "node.removeOutput(idx)" in pair_out


# ---------------------------------------- tab-switch restore-race audit
#
# Owner report (notebook.js v0.87.3, same bug class this session audited
# switcher.js against): "tabbing to another workflow and tabbing back
# erases any changes." A tab switch tears this node down and rebuilds it;
# every piece of per-row state here (the `toggles` widget, the
# `High/low pairs` property, a renamed input's `.label`) is durable --
# restored by litegraph's OWN `configure()`, never held only in a JS
# closure -- so surviving the rebuild is not in question. What IS worth
# pinning is the ORDER: the post-configure reconverge
# (convergeImageInputs, via pruneToggles/convergePairInputs/
# convergePairOutput) must run AFTER that real restore lands, never
# before it -- reading a mid-rebuild snapshot instead of the finished
# restore is exactly the shape that let notebook.js's dirty-check
# overwrite/clear a real edit.


def test_configure_wrap_reconverges_only_after_the_real_restore_completes(
    switcher_source: str,
) -> None:
    """The wrap must call the ORIGINAL configure() (litegraph's own restore
    of widgets_values/properties/inputs/outputs) before its own reconcile
    is even SCHEDULED, and that scheduling must live in the `finally` --
    not a parallel branch a throwing original configure() could skip -- so
    the pass always sees the restore's actual result, never a stale
    pre-restore construction default. v0.100.4: the pass is DEFERRED
    (scheduleConverge, the live path's own macrotask), never run inside
    configure() itself -- litegraph's clone() configures a links-nulled
    snapshot and serializes it in the same tick (copy/paste), and a
    synchronous collapse there emptied the clipboard of every row."""
    body = _function_body(switcher_source, "wireImageInputGrowth(node)")
    assert "} finally {" in body
    restore = body.index("return originalConfigure.apply(this, args)")
    finally_kw = body.index("} finally {")
    reconverge = body.index("scheduleConverge(this)")
    assert restore < finally_kw < reconverge
    # The deferred runner is the one place the pass actually executes.
    assert "convergeImageInputs(this)" not in body
    assert "convergeImageInputs(target)" in body


def test_converge_reaches_toggles_and_pairing_every_pass(switcher_source: str) -> None:
    """convergeImageInputs() is the one post-restore choke point pinned
    above -- it must reach ALL of this node's durable per-row state each
    time it runs (pruneToggles for the toggle overrides, plus both pair
    convergers for WAN high/low), not just the trailing-spare-slot math,
    or a tab switch could silently reconcile against only part of the
    restored node."""
    body = _function_body(switcher_source, "convergeImageInputs(node)")
    assert "pruneToggles(node)" in body
    assert "convergePairInputs(node)" in body
    assert "convergePairOutput(node)" in body


def test_prune_toggles_never_reads_wiring_at_all(switcher_source: str) -> None:
    """The dangerous shape this bug class hunts for is a stale-vs-fresh
    comparison that wipes real data (notebook.js: a repaint's `textarea ===
    lastSavedText` check cleared a draft it hadn't actually repainted yet).
    pruneToggles() used to prune against the live connected-names set --
    not stale, but WRONG in kind (v0.100.3, owner report 2026-10-27): a row
    is unwired for the whole of any gesture that lifts its wire, so the
    converge that ran mid-gesture dropped the row's `false` and the row
    came back ON. The prune now consults the toggles map and the class's
    own `<prefix>_N` key shape only -- never `.connected`, never
    imageInputEntries() -- so no wiring state, live or restored, can ever
    clear a key the user set. The behavioural half (the gestures
    themselves) is driven for real by the attach probe below."""
    body = _function_body(switcher_source, "pruneToggles(node)")
    assert "const map = readToggles(node)" in body
    assert "spec.inputRe.test(key)" in body
    assert "delete map[key]" in body
    assert "connected" not in body
    assert "imageInputEntries" not in body
    assert ".link" not in body
    # a no-op write when nothing is actually stale -- never a blind rewrite.
    assert "if (changed) writeToggles(node, map)" in body


# ------------------------------------------------------------ v0.68.1 pins


def test_graph_column_walk_is_memoized_per_canvas_frame(switcher_source: str) -> None:
    """v0.68.1 perf: the graph-wide column walk used to run inside EVERY
    visible switcher's onDrawForeground -- (switchers x all graph nodes)
    per frame. It now runs once per graph per canvas frame, keyed on
    litegraph's own per-draw() `LGraphCanvas.frame` counter (1.48.7), so a
    memo hit is by construction the SAME frame's answer -- this is NOT the
    cross-frame publish state test_column_requirement_is_pure_... forbids
    (every onDrawForeground in one pass sees one frame value; a
    rename/rewire lands between frames and the next pass recomputes). No
    frame counter = no memo = the old per-call walk."""
    walk = _function_body(switcher_source, "graphColumnBounds(graph)")
    assert "const frame = app?.canvas?.frame" in walk
    assert "typeof frame === 'number'" in walk
    assert "if (cached && cached.frame === frame) return cached" in walk
    assert "columnBoundsCache.set(graph, bounds)" in walk
    # A WeakMap keyed by the graph object, not a property bolted onto it.
    assert "const columnBoundsCache = new WeakMap()" in switcher_source
    # ...and the pure requirement helper is untouched by the memo.
    need = _function_body(switcher_source, "columnNeedOf(node)")
    assert "frame" not in need and "cache" not in need.lower()


def test_draw_paths_parse_toggles_once_per_pass(switcher_source: str) -> None:
    """v0.68.1 perf: isRowEnabled() -> readToggles() -> JSON.parse used to
    run once PER ROW per frame, ~3x over (header allRowsState, header
    filter(isRowEnabled), the row boxes). Each draw path now parses once and
    reads rows through the pure isEnabledIn(map, name)."""
    assert "function isEnabledIn(toggles, name)" in switcher_source
    rows = _function_body(switcher_source, "drawRowToggles(node, ctx)")
    assert "const toggles = readToggles(node)" in rows
    assert "isEnabledIn(toggles, row.entry.name)" in rows
    assert "isRowEnabled(node," not in rows
    header = _function_body(switcher_source, "addHeaderWidget(node)")
    assert "const toggles = readToggles(drawNode)" in header
    assert "allRowsState(drawNode, toggles)" in header
    assert "isEnabledIn(toggles, entry.name)" in header
    assert "isRowEnabled(drawNode," not in header
    # The non-draw callers keep the convenient one-shot form.
    assert "function isRowEnabled(node, name)" in switcher_source


def test_width_floor_lifts_through_set_size_not_a_dead_is_array_guard(
    switcher_source: str,
) -> None:
    """v0.68.1: `LGraphNode.size` is a Proxy over a typed-array view on the
    installed frontend (1.48.7), never an Array -- so `Array.isArray(node.size)`
    was always false: ensureMinNodeWidth's lift never ran, and
    outputSafeColumnX's `Array.isArray(node.size) || node.size` was a no-op
    test by precedence. The lift goes through setSize() (runs litegraph's
    `_sizeUpdated` + onResize); the computeSize wrap floors whatever array
    litegraph hands back (a caller-supplied typed `out` included)."""
    assert "Array.isArray(node.size)" not in switcher_source
    floor = _function_body(switcher_source, "ensureMinNodeWidth(node)")
    assert "if (node.size && node.size[0] < MIN_NODE_WIDTH)" in floor
    assert "node.setSize([MIN_NODE_WIDTH, node.size[1]])" in floor
    assert "if (size) size[0] = Math.max(size[0], MIN_NODE_WIDTH)" in floor
    assert "if (Array.isArray(size))" not in floor
    ceiling = _function_body(switcher_source, "outputSafeColumnX(node)")
    assert "const width = node.size ? node.size[0] : 0" in ceiling


# ------------------------------------------- sticky row toggles (v0.100.3)
#
# Owner report 2026-10-27: "If I have 5 models connected but only model 1
# turned on I still get 2 videos. Also if I drag any video back onto the
# canvas the proper model and the 5th model (last one in the queue) is also
# selected." Root cause: pruneToggles() dropped a row's `false` whenever
# that row was UNWIRED at the moment a converge ran -- and converge runs
# after every link change (deferred one macrotask) and after every
# configure(), so any gesture that lifts a wire for even a moment (picking
# it up and dropping it back, parking it on the canvas, deleting and
# re-adding the loader) silently flipped the row back ON. The queued
# `toggles` then lacked `model_5: false`, the backend ran model 5 too, and
# the embedded workflow in every video showed rows 1 and 5 enabled.
#
# The tests below drive the REAL attach() export against a from-scratch
# litegraph-node stub (tests/test_number_controller_js.py's convention --
# no jsdom in this repo) through exactly those gestures, and read what a
# queue would read: the `toggles` widget value, and the enabled+wired row
# count nodes_switcher.py's execute() derives from it.

ATTACH_PROBE_JS = """
import * as s from './extensions/comfyui-epsnodes/eps_image/switcher.js'

const INPUT = 1 // litegraph NodeSlotType.INPUT
const PROP_HIGH_LOW = 'High/low pairs'
const tick = () => new Promise((resolve) => setTimeout(resolve, 0))
// The live converge switcher.js schedules from onConnectionsChange is a
// setTimeout(fn, 0); two macrotasks cover a re-schedule from its own
// removeInput() dispatch.
const settle = async () => { await tick(); await tick() }

let nextLinkId = 1

function makeGraph() {
  const graph = { links: new Map(), _nodes: [], setDirtyCanvas() {} }
  graph.add = (node) => { graph._nodes.push(node); node.graph = graph }
  graph.getNodeById = (id) => graph._nodes.find((n) => n.id === id) || null
  return graph
}

function makeLoader(id) {
  return {
    id, comfyClass: 'CheckpointLoaderSimple', inputs: [], widgets: [],
    outputs: [{ name: 'MODEL', type: 'MODEL', links: [] }],
    // LGraphNode.connect(): an already-wired target input is disconnected
    // FIRST (its onConnectionsChange fires), then the new link lands and
    // the target hears about that too -- all in one synchronous call.
    connect(slot, target, targetSlot) {
      const idx = typeof targetSlot === 'string'
        ? target.inputs.findIndex((i) => i.name === targetSlot)
        : targetSlot
      const input = target.inputs[idx]
      if (!input) return null
      if (input.link != null) target.disconnectInput(idx)
      const link = {
        id: nextLinkId++, origin_id: this.id, origin_slot: slot,
        target_id: target.id, target_slot: idx, type: input.type
      }
      this.graph.links.set(link.id, link)
      this.outputs[slot].links.push(link.id)
      input.link = link.id
      target.onConnectionsChange?.(INPUT, idx, true, link, input)
      return link
    }
  }
}

function makeSwitcher(id) {
  return {
    id, comfyClass: 'EPSModelSwitcher', pos: [0, 0], size: [270, 100],
    flags: {}, properties: {},
    // The def's own slots (nodes_switcher.py INPUT_TYPES): model_1 plus
    // the hidden `toggles` STRING -- which on frontend 1.52.7 ALSO owns a
    // widget-input slot sitting between model_1 and the grown rows
    // (rig-observed order: model_1, toggles, model_2, ...).
    inputs: [
      { name: 'model_1', type: 'MODEL', link: null },
      { name: 'toggles', type: 'STRING', link: null, widget: { name: 'toggles' } }
    ],
    outputs: [
      { name: 'models', type: 'MODEL', links: null },
      { name: 'models_low', type: 'MODEL', links: null }
    ],
    widgets: [{ name: 'toggles', type: 'text', value: '{}', options: {} }],
    addProperty(name, value) { this.properties[name] = value },
    setSize(size) { this.size = size },
    computeSize() { return [270, 100] },
    setDirtyCanvas() {},
    addInput(name, type, extra) {
      const input = { name, type, link: null, ...(extra || {}) }
      this.inputs.push(input)
      return input
    },
    // LGraphNode.removeInput: disconnectInput() first (which dispatches
    // onConnectionsChange), then splice and re-seat the later links.
    removeInput(slot) {
      this.disconnectInput(slot)
      this.inputs.splice(slot, 1)
      for (let i = slot; i < this.inputs.length; i++) {
        const link = this.graph?.links.get(this.inputs[i].link)
        if (link) link.target_slot = i
      }
    },
    disconnectInput(slot) {
      const input = this.inputs[slot]
      if (!input || input.link == null) return false
      const link = this.graph?.links.get(input.link)
      input.link = null
      if (link) {
        this.graph.links.delete(link.id)
        const origin = this.graph.getNodeById(link.origin_id)
        const out = origin?.outputs?.[link.origin_slot]
        if (out?.links) out.links = out.links.filter((linkId) => linkId !== link.id)
      }
      this.onConnectionsChange?.(INPUT, slot, false, link, input)
      return true
    },
    addOutput(name, type) { this.outputs.push({ name, type, links: null }) },
    removeOutput(idx) { this.outputs.splice(idx, 1) },
    addCustomWidget(widget) { this.widgets.push(widget); return widget },
    // LGraphNode.setProperty (1.52.7): set, notify, then scan EVERY widget
    // for one bound to the property -- `w.options.property`, with no guard
    // on `options` itself. Reached by right-click > Properties and by the
    // properties panel's Assign button.
    setProperty(name, value) {
      if (value === this.properties[name]) return
      const prev = this.properties[name]
      this.properties[name] = value
      if (this.onPropertyChanged?.(name, value, prev) === false) this.properties[name] = prev
      for (const widget of this.widgets) {
        if (widget && widget.options.property == name) { widget.value = value; break }
      }
    },
    // One 20px row per input index -- what drawRowToggles/rowAtLocalY read.
    getConnectionPos(isInput, slot) { return [0, 10 + slot * 20] },
    // LGraphNode.configure (1.52.7): inputs/outputs/properties land first
    // (onPropertyChanged fires per saved property), then every restored
    // input gets onConnectionsChange(..., true, ...) -- `isConnected`
    // HARDCODED true even for an unwired slot -- then widgets_values.
    configure(info) {
      if (info.inputs) {
        // ComfyNode.configure (app.ts, 1.52.7) runs first: the DEF's own
        // slots keep their order and take the saved slot of the same
        // name; every other saved slot follows in file order.
        const saved = new Map(info.inputs.map((input) => [input.name, input]))
        const defSlots = this.inputs.map((input) => ({ ...(saved.get(input.name) ?? input) }))
        const extras = info.inputs
          .filter((input) => !this.inputs.some((current) => current.name === input.name))
          .map((input) => ({ ...input }))
        this.inputs = [...defSlots, ...extras]
      }
      if (info.outputs) this.outputs = info.outputs.map((output) => ({ ...output }))
      for (const [name, value] of Object.entries(info.properties || {})) {
        this.properties[name] = value
        this.onPropertyChanged?.(name, value)
      }
      for (const [i, input] of this.inputs.entries()) {
        const link = input.link != null ? this.graph?.links.get(input.link) : null
        this.onConnectionsChange?.(INPUT, i, true, link, input)
      }
      if (info.widgets_values) {
        let n = 0
        for (const widget of this.widgets) {
          if (widget.serialize === false) continue
          if (n >= info.widgets_values.length) break
          widget.value = info.widgets_values[n++]
        }
      }
    }
  }
}

function makeCtx() {
  const noop = () => {}
  const ctx = {
    texts: [], save: noop, restore: noop, beginPath: noop, roundRect: noop,
    rect: noop, fill: noop, stroke: noop, moveTo: noop, lineTo: noop
  }
  ctx.fillText = (text) => ctx.texts.push(text)
  return ctx
}

const togglesOf = (node) => JSON.parse(node.widgets.find((w) => w.name === 'toggles').value)
const rowsOf = (node) => node.inputs
  .filter((input) => /^model_\\d+(_low)?$/.test(input.name))
  .map((input) => `${input.name}:${input.link == null ? '-' : 'wired'}`)
// What nodes_switcher.py's execute() derives from the same two facts: a
// wired `model_N` runs unless its key is literally false.
const enabledWired = (node) => {
  const toggles = togglesOf(node)
  return node.inputs.filter(
    (input) =>
      /^model_\\d+$/.test(input.name) && input.link != null && toggles[input.name] !== false
  ).length
}
const headerText = (node) => {
  const ctx = makeCtx()
  node.widgets.find((w) => w.name === '__eps_switcher_toggle_all').draw(ctx, node, 270, 0, 20)
  return ctx.texts[0]
}
const snapshot = (node) => ({
  rows: rowsOf(node), toggles: togglesOf(node), enabledWired: enabledWired(node),
  header: headerText(node)
})
// A real row click: draw (caches the hit rects) then onMouseDown inside
// that row's box -- the exact path a canvas click takes.
function clickRow(node, name) {
  node.onDrawForeground(makeCtx(), null, null)
  const rect = node.__epsSwitcherRowRects.find((r) => r.name === name)
  if (!rect) throw new Error(`no toggle box drawn for ${name}`)
  node.onMouseDown({}, [rect.x + 1, rect.y + 1], null)
}

function build() {
  const graph = makeGraph()
  const sw = makeSwitcher(1)
  graph.add(sw)
  const loaders = []
  for (let i = 0; i < 5; i++) {
    const loader = makeLoader(10 + i)
    graph.add(loader)
    loaders.push(loader)
  }
  s.attach(sw)
  return { graph, sw, loaders }
}

async function wireFiveAndSwitchOffTwoToFive() {
  const rig = build()
  for (let i = 0; i < 5; i++) {
    rig.loaders[i].connect(0, rig.sw, `model_${i + 1}`)
    await settle() // the deferred converge grows the next spare
  }
  for (const n of [2, 3, 4, 5]) clickRow(rig.sw, `model_${n}`)
  return rig
}

const out = {}

// --- A: pick the last row's wire up (litegraph disconnects on pick-up),
// let the deferred converge run, then drop it back on the same row. ---
{
  const { sw, loaders } = await wireFiveAndSwitchOffTwoToFive()
  const wired = snapshot(sw)
  sw.disconnectInput(sw.inputs.findIndex((input) => input.name === 'model_5'))
  await settle()
  const lifted = snapshot(sw)
  loaders[4].connect(0, sw, 'model_5')
  await settle()
  const dropped = snapshot(sw)
  out.liftAndDropBack = { wired, lifted, dropped }
}

// --- B: swap the loader by dragging another output onto the WIRED row
// (connect() disconnects + reconnects synchronously). ---
{
  const { sw, loaders } = await wireFiveAndSwitchOffTwoToFive()
  loaders[3].connect(0, sw, 'model_5')
  await settle()
  out.swapInPlace = { rows: rowsOf(sw), toggles: togglesOf(sw), enabledWired: enabledWired(sw) }
}

// --- C: the same lift-and-drop-back with High/low pairs ON. ---
{
  const { sw, loaders } = await wireFiveAndSwitchOffTwoToFive()
  sw.properties[PROP_HIGH_LOW] = true
  sw.onPropertyChanged(PROP_HIGH_LOW, true, false)
  const paired = { rows: rowsOf(sw), toggles: togglesOf(sw) }
  sw.disconnectInput(sw.inputs.findIndex((input) => input.name === 'model_5'))
  await settle()
  const lifted = { rows: rowsOf(sw), toggles: togglesOf(sw) }
  loaders[4].connect(0, sw, 'model_5')
  await settle()
  const dropped = { rows: rowsOf(sw), toggles: togglesOf(sw), enabledWired: enabledWired(sw) }
  out.pairsLiftAndDropBack = { paired, lifted, dropped }
}

// --- D: a workflow restored with a saved GAP (model_3 unwired, switched
// off) -- configure()'s post-restore converge, then re-wiring the gap. ---
{
  const { graph, sw, loaders } = build()
  const wire = (loader, slot) => {
    const link = {
      id: nextLinkId++, origin_id: loader.id, origin_slot: 0,
      target_id: sw.id, target_slot: slot, type: 'MODEL'
    }
    graph.links.set(link.id, link)
    loader.outputs[0].links.push(link.id)
    return link.id
  }
  sw.configure({
    inputs: [
      { name: 'model_1', type: 'MODEL', link: wire(loaders[0], 0) },
      { name: 'toggles', type: 'STRING', link: null, widget: { name: 'toggles' } },
      { name: 'model_2', type: 'MODEL', link: wire(loaders[1], 2) },
      { name: 'model_3', type: 'MODEL', link: null },
      { name: 'model_4', type: 'MODEL', link: wire(loaders[3], 4) },
      { name: 'model_5', type: 'MODEL', link: null }
    ],
    properties: { [PROP_HIGH_LOW]: false },
    widgets_values: ['{"model_3":false,"model_4":false}']
  })
  const restored = { rows: rowsOf(sw), toggles: togglesOf(sw), enabledWired: enabledWired(sw) }
  loaders[2].connect(0, sw, 'model_3')
  await settle()
  const rewired = { rows: rowsOf(sw), toggles: togglesOf(sw), enabledWired: enabledWired(sw) }
  out.configureGap = { restored, rewired }
}

// --- E: keys that cannot name a row of this node are still pruned. ---
{
  const { sw, loaders } = await wireFiveAndSwitchOffTwoToFive()
  sw.widgets[0].value = JSON.stringify({
    junk: false, image_1: false, model_2: false, 'model_': false
  })
  loaders[2].connect(0, sw, 'model_3') // any link change: one deferred converge
  await settle()
  out.foreignKeys = { toggles: togglesOf(sw) }
}

// ---------------------------------------------------------------------------
// Copy / paste / clone -- litegraph 1.52.7's exact order, read from the
// bundle: `_serializeItems` puts `node.clone().serialize()` on the
// clipboard plus the ORIGINAL node's input links (`target_slot` = original
// index); `LGraphNode.clone()` is createNode -> serialize -> null every
// `input.link` -> configure(data), never added to a graph;
// `_deserializeItems` is createNode -> graph.add -> configure(clip) -> for
// each link `origin.connect(origin_slot, pasted, target_slot)` by INDEX.
// Alt-drag is clone() then graph.add().
// ---------------------------------------------------------------------------

// LGraphNode.serialize(): inputs keep `label`; widgets_values skips
// `serialize === false` widgets; properties cloned.
function serializeLike(node) {
  return {
    inputs: node.inputs.map((input) => ({
      name: input.name, type: input.type, link: input.link,
      ...(input.label ? { label: input.label } : {}),
      ...(input.widget ? { widget: { ...input.widget } } : {})
    })),
    outputs: node.outputs.map((output) => ({
      name: output.name, type: output.type, links: output.links ? [...output.links] : null
    })),
    properties: { ...node.properties },
    widgets_values: node.widgets.filter((w) => w.serialize !== false).map((w) => w.value)
  }
}

function cloneLike(node) {
  const data = serializeLike(node)
  for (const input of data.inputs) input.link = null
  for (const output of data.outputs) if (output.links) output.links.length = 0
  const copy = makeSwitcher(node.id)
  s.attach(copy) // nodeCreated
  copy.configure(data) // clone() configures BEFORE any graph.add
  return copy
}

// `_serializeItems` for ONE selected switcher.
function copyLike(node, graph) {
  const clip = { node: serializeLike(cloneLike(node)), links: [] }
  for (const [idx, input] of node.inputs.entries()) {
    if (input.link == null) continue
    const link = graph.links.get(input.link)
    clip.links.push({
      origin_id: link.origin_id, origin_slot: link.origin_slot,
      target_id: node.id, target_slot: idx
    })
  }
  return clip
}

// `_deserializeItems` with connectInputs (Ctrl+Shift+V); plain Ctrl+V of
// the switcher alone is the same call with no links.
async function pasteLike(clip, graph, id, { connectInputs }) {
  const pasted = makeSwitcher(id)
  s.attach(pasted)
  graph.add(pasted)
  pasted.configure(clip.node)
  if (connectInputs) {
    for (const link of clip.links) {
      graph.getNodeById(link.origin_id).connect(link.origin_slot, pasted, link.target_slot)
    }
  }
  await settle()
  return pasted
}

const pasteSnapshot = (node) => ({
  rows: rowsOf(node), toggles: togglesOf(node), enabledWired: enabledWired(node),
  labels: node.inputs.filter((input) => input.label).map((input) => `${input.name}=${input.label}`),
  outputs: node.outputs.map((output) => output.name)
})

// --- F: Ctrl+C then Ctrl+Shift+V of a 5-row switcher, rows 2-5 off, one
// renamed row. ---
{
  const { graph, sw } = await wireFiveAndSwitchOffTwoToFive()
  sw.inputs.find((input) => input.name === 'model_3').label = 'wan high'
  const clip = copyLike(sw, graph)
  const clipboard = {
    rows: clip.node.inputs.filter((input) => /^model_/.test(input.name)).map((input) => input.name),
    labels: clip.node.inputs.filter((input) => input.label).map((input) => input.label),
    widgetsValues: clip.node.widgets_values,
    linkSlots: clip.links.map((link) => link.target_slot)
  }
  const pasted = await pasteLike(clip, graph, 2, { connectInputs: true })
  out.pasteWithLinks = { clipboard, pasted: pasteSnapshot(pasted), original: pasteSnapshot(sw) }
}

// --- G: plain Ctrl+V of the switcher alone (no links come along). ---
{
  const { graph, sw } = await wireFiveAndSwitchOffTwoToFive()
  const clip = copyLike(sw, graph)
  const pasted = await pasteLike(clip, graph, 2, { connectInputs: false })
  out.pastePlain = pasteSnapshot(pasted)
}

// --- H: Alt-drag clone (clone() then graph.add()), and a clone that is
// never added anywhere (what _serializeItems itself makes). ---
{
  const { graph, sw } = await wireFiveAndSwitchOffTwoToFive()
  const copy = cloneLike(sw)
  graph.add(copy)
  await settle()
  const orphan = cloneLike(sw)
  let orphanThrew = null
  try { await settle() } catch (error) { orphanThrew = String(error) }
  out.altDragClone = { added: pasteSnapshot(copy), orphanRows: rowsOf(orphan), orphanThrew }
}

// --- I: High/low pairs ON, highs AND lows wired, Ctrl+C / Ctrl+Shift+V. ---
{
  const { graph, sw, loaders } = await wireFiveAndSwitchOffTwoToFive()
  sw.properties[PROP_HIGH_LOW] = true
  sw.onPropertyChanged(PROP_HIGH_LOW, true, false)
  const lowLoaders = []
  for (let i = 0; i < 5; i++) {
    const loader = makeLoader(20 + i)
    graph.add(loader)
    lowLoaders.push(loader)
    loader.connect(0, sw, `model_${i + 1}_low`)
  }
  await settle()
  const clip = copyLike(sw, graph)
  const pasted = await pasteLike(clip, graph, 2, { connectInputs: true })
  out.pastePairs = { original: pasteSnapshot(sw), pasted: pasteSnapshot(pasted) }
  void loaders
}

// --- J: node.setProperty() -- the right-click > Properties path. ---
{
  const { sw } = await wireFiveAndSwitchOffTwoToFive()
  const header = sw.widgets.find((w) => w.name === '__eps_switcher_toggle_all')
  let threw = null
  try { sw.setProperty(PROP_HIGH_LOW, true) } catch (error) { threw = String(error) }
  await settle()
  out.setProperty = {
    threw, rows: rowsOf(sw), outputs: sw.outputs.map((output) => output.name),
    headerSerialize: header.serialize, headerOptionsSerialize: header.options?.serialize,
    widgetsValues: serializeLike(sw).widgets_values
  }
}

process.stdout.write(JSON.stringify(out))
"""

#: The owner's setup, as the queue reads it: rows 2-5 switched off, model_1
#: (absent key) enabled -- the ONE state every gesture below must preserve.
OFF_2_TO_5 = {"model_2": False, "model_3": False, "model_4": False, "model_5": False}


@pytest.fixture(scope="module")
def switcher_attach_api(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Runs ATTACH_PROBE_JS against the REAL switcher.js in the same
    served-layout tmp dir shape as `switcher_api` (module docstring)."""
    layout = tmp_path_factory.mktemp("web_root_attach")
    module_dir = layout / "extensions" / "comfyui-epsnodes" / "eps_image"
    module_dir.mkdir(parents=True)
    shutil.copyfile(SWITCHER_JS, module_dir / "switcher.js")
    scripts = layout / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "app.js").write_text("export const app = {}\n", encoding="utf-8")
    probe = layout / "probe.mjs"
    probe.write_text(ATTACH_PROBE_JS, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(probe)], capture_output=True, text=True, timeout=60, cwd=layout
    )
    assert result.returncode == 0, f"attach probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


def test_attach_probe_wires_five_rows_and_switches_four_off(switcher_attach_api: dict) -> None:
    """The setup itself, through real row clicks: five wired rows plus the
    trailing spare, rows 2-5 off, one enabled row, and the header agrees."""
    wired = switcher_attach_api["liftAndDropBack"]["wired"]
    assert wired["rows"] == [
        "model_1:wired", "model_2:wired", "model_3:wired", "model_4:wired",
        "model_5:wired", "model_6:-",
    ]
    assert wired["toggles"] == OFF_2_TO_5
    assert wired["enabledWired"] == 1
    assert wired["header"] == "Toggle All  (1/5 enabled)"


def test_lifting_the_last_wire_keeps_that_row_off(switcher_attach_api: dict) -> None:
    """Picking the wire up disconnects the row (litegraph) and the deferred
    converge collapses the trailing spare onto it -- the row's `false` must
    SURVIVE that, not be pruned because the slot is momentarily unwired. The
    header still counts wired rows only, so a sticky key on the now-unwired
    spare never leaks into the count."""
    lifted = switcher_attach_api["liftAndDropBack"]["lifted"]
    assert lifted["rows"] == [
        "model_1:wired", "model_2:wired", "model_3:wired", "model_4:wired", "model_5:-",
    ]
    assert lifted["toggles"]["model_5"] is False
    assert lifted["enabledWired"] == 1
    assert lifted["header"] == "Toggle All  (1/4 enabled)"


def test_dropping_the_wire_back_queues_one_run_not_two(switcher_attach_api: dict) -> None:
    """The owner's two symptoms are one state: after the drop-back the
    queued `toggles` must still switch model_5 off, so the backend runs ONE
    row (one video), and the workflow embedded in that video shows rows
    2-5 off -- not "the proper model and the 5th model"."""
    dropped = switcher_attach_api["liftAndDropBack"]["dropped"]
    assert dropped["rows"] == [
        "model_1:wired", "model_2:wired", "model_3:wired", "model_4:wired",
        "model_5:wired", "model_6:-",
    ]
    assert dropped["toggles"] == OFF_2_TO_5
    assert dropped["enabledWired"] == 1
    assert dropped["header"] == "Toggle All  (1/5 enabled)"


def test_swapping_the_loader_in_place_keeps_the_row_off(switcher_attach_api: dict) -> None:
    """Dragging a different output onto the WIRED row: litegraph disconnects
    and reconnects inside one call, so the deferred converge sees it wired.
    This path was already safe; pinned so the sticky rule covers both."""
    swapped = switcher_attach_api["swapInPlace"]
    assert swapped["toggles"]["model_5"] is False
    assert swapped["enabledWired"] == 1


def test_pairs_lift_and_drop_back_keeps_the_row_off(switcher_attach_api: dict) -> None:
    """WAN high/low pairs ON: the same gesture on the HIGH wire, with the
    `_low` partners interleaved, re-seated and re-collapsed around it."""
    pairs = switcher_attach_api["pairsLiftAndDropBack"]
    assert pairs["paired"]["rows"] == [
        "model_1:wired", "model_1_low:-", "model_2:wired", "model_2_low:-",
        "model_3:wired", "model_3_low:-", "model_4:wired", "model_4_low:-",
        "model_5:wired", "model_5_low:-", "model_6:-", "model_6_low:-",
    ]
    assert pairs["paired"]["toggles"]["model_5"] is False
    assert pairs["lifted"]["toggles"]["model_5"] is False
    assert pairs["dropped"]["toggles"] == OFF_2_TO_5
    assert pairs["dropped"]["enabledWired"] == 1
    assert pairs["dropped"]["rows"] == pairs["paired"]["rows"]


def test_restored_gap_keeps_its_off_state_through_configure(switcher_attach_api: dict) -> None:
    """A workflow saved with a disconnected, switched-off middle row: the
    post-configure converge must not drop `model_3: false`, and wiring a
    model into that gap later leaves the row off -- drawn off, so the user
    sees it, rather than silently running."""
    gap = switcher_attach_api["configureGap"]
    assert gap["restored"]["rows"] == [
        "model_1:wired", "model_2:wired", "model_3:-", "model_4:wired", "model_5:-",
    ]
    assert gap["restored"]["toggles"] == {"model_3": False, "model_4": False}
    assert gap["restored"]["enabledWired"] == 2
    assert gap["rewired"]["rows"] == [
        "model_1:wired", "model_2:wired", "model_3:wired", "model_4:wired", "model_5:-",
    ]
    assert gap["rewired"]["toggles"] == {"model_3": False, "model_4": False}
    assert gap["rewired"]["enabledWired"] == 2


def test_foreign_toggle_keys_are_still_pruned(switcher_attach_api: dict) -> None:
    """Pruning still removes keys that cannot name a row of THIS node (a
    hand-edited workflow, a sibling class's prefix) -- only the
    unwired-means-drop rule is gone."""
    assert switcher_attach_api["foreignKeys"]["toggles"] == {"model_2": False}


# -------------------------------------------- copy / paste / clone (v0.100.4)
#
# Side finding of the 2026-10-27 round: Ctrl+Shift+V of a 5-row switcher
# came back with only model_1 wired. litegraph's `_serializeItems` puts
# `node.clone().serialize()` on the clipboard, and `clone()` nulls every
# input link BEFORE configure(data) -- so the wrapper's synchronous
# post-configure converge collapsed `model_2..6` on the throwaway clone
# before it was serialized. The clipboard node had two slots; the paste
# then connected the original links by `target_slot` index (0, 2, 3, 4, 5)
# into slots that did not exist, and only model_1 landed.

FIVE_WIRED_PLUS_SPARE = [
    "model_1:wired", "model_2:wired", "model_3:wired", "model_4:wired",
    "model_5:wired", "model_6:-",
]


def test_clipboard_carries_every_row_of_the_copied_switcher(switcher_attach_api: dict) -> None:
    """The throwaway clone `_serializeItems` serializes must still hold all
    six slots (five rows + spare), the renamed label and the toggles -- the
    collapse of its unwired slots must not happen before serialize()."""
    clipboard = switcher_attach_api["pasteWithLinks"]["clipboard"]
    assert clipboard["rows"] == ["model_1", "model_2", "model_3", "model_4", "model_5", "model_6"]
    assert clipboard["labels"] == ["wan high"]
    assert clipboard["widgetsValues"] == [json.dumps(OFF_2_TO_5, separators=(",", ":"))]
    assert clipboard["linkSlots"] == [0, 2, 3, 4, 5]  # original indices; toggles sits at 1


def test_paste_with_links_keeps_all_wires_spare_toggles_and_labels(
    switcher_attach_api: dict,
) -> None:
    """Ctrl+Shift+V: five wires land by index, the spare follows, rows 2-5
    stay off and the renamed row keeps its label -- the pasted switcher is
    the original, not a one-row stub."""
    pasted = switcher_attach_api["pasteWithLinks"]["pasted"]
    assert pasted["rows"] == FIVE_WIRED_PLUS_SPARE
    assert pasted["toggles"] == OFF_2_TO_5
    assert pasted["enabledWired"] == 1
    assert pasted["labels"] == ["model_3=wan high"]
    assert pasted["outputs"] == ["models"]
    # ...and the original is untouched by being copied.
    assert switcher_attach_api["pasteWithLinks"]["original"]["rows"] == FIVE_WIRED_PLUS_SPARE


def test_plain_paste_is_a_tidy_one_spare_node_with_toggles(switcher_attach_api: dict) -> None:
    """Ctrl+V of the switcher alone brings no links: the one-trailing-spare
    invariant collapses it to `model_1`, toggles ride along (sticky keys
    are harmless on unwired rows), nothing throws."""
    pasted = switcher_attach_api["pastePlain"]
    assert pasted["rows"] == ["model_1:-"]
    assert pasted["toggles"] == OFF_2_TO_5
    assert pasted["enabledWired"] == 0


def test_alt_drag_clone_is_tidy_and_an_orphan_clone_never_throws(
    switcher_attach_api: dict,
) -> None:
    """Alt-drag = clone() then graph.add(): the deferred converge runs once
    the clone is in a graph and leaves one spare. A clone that is never
    added (what _serializeItems makes) must simply be skipped, not throw."""
    alt = switcher_attach_api["altDragClone"]
    assert alt["added"]["rows"] == ["model_1:-"]
    assert alt["added"]["toggles"] == OFF_2_TO_5
    assert alt["orphanThrew"] is None
    assert alt["orphanRows"][0] == "model_1:-"


def test_pairs_paste_keeps_highs_lows_order_and_low_output(switcher_attach_api: dict) -> None:
    """High/low pairs ON with every high AND low wired: the paste must land
    all ten wires at the interleaved indices (the def-first merge plus the
    pairing re-seat restore the clipboard order before links connect), keep
    the spare pair, the `models_low` output and the toggles."""
    pairs = switcher_attach_api["pastePairs"]
    expected_rows = [
        "model_1:wired", "model_1_low:wired", "model_2:wired", "model_2_low:wired",
        "model_3:wired", "model_3_low:wired", "model_4:wired", "model_4_low:wired",
        "model_5:wired", "model_5_low:wired", "model_6:-", "model_6_low:-",
    ]
    assert pairs["original"]["rows"] == expected_rows
    assert pairs["pasted"]["rows"] == expected_rows
    assert pairs["pasted"]["toggles"] == OFF_2_TO_5
    assert pairs["pasted"]["enabledWired"] == 1
    assert pairs["pasted"]["outputs"] == ["models", "models_low"]


# ------------------------------------------------- setProperty (v0.100.4)


def test_set_property_does_not_throw_on_the_header_widget(switcher_attach_api: dict) -> None:
    """Right-click > Properties > 'High/low pairs' (and the panel's Assign)
    go through LGraphNode.setProperty, which scans `w.options.property` on
    EVERY widget. The header custom widget had no `options` at all, so the
    property applied and then the dialog threw. The widget now carries an
    `options` object; serialization is unchanged on both sides (the
    workflow's widgets_values still skip it via `serialize: false`, the
    prompt skips it via `options.serialize: false`)."""
    result = switcher_attach_api["setProperty"]
    assert result["threw"] is None
    assert result["rows"][:2] == ["model_1:wired", "model_1_low:-"]
    assert result["outputs"] == ["models", "models_low"]
    assert result["headerSerialize"] is False
    assert result["headerOptionsSerialize"] is False
    assert result["widgetsValues"] == [json.dumps(OFF_2_TO_5, separators=(",", ":"))]
