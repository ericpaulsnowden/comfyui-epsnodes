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
