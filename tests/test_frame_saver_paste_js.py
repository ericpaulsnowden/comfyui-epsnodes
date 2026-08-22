"""Pure-logic tests for the EPS Frame Saver paste-a-path helpers (FORMAT.md
§6.7 — the 2026-07-21 "paste a video path onto the node" feature).

``web/eps_image/frame_saver.js`` factors the paste pipeline's decision logic
into PURE exported functions (``stripWrappingQuotes``/``fileUrlToPath`` and
their composite ``cleanPastedVideoPath``, plus ``pathExtension``/
``looksAbsolutePath``/``isTextEntryElement`` and the whole-decision
``evaluatePastedText`` verdict) so the clipboard-text contract is testable
without a browser. The module's imports are ComfyUI's
``../../../scripts/api.js`` and ``../../../scripts/app.js``, resolved against
the served layout (``<web root>/extensions/<pack>/eps_image/frame_saver.js``
-> ``<web root>/scripts/*.js``), so the fixture mirrors that exact directory
depth in a tmp dir with stub modules, byte-copies the real module in
unchanged, and evaluates one probe script under Node (same runtime family as
the browser) — the exact pattern ``test_resolution_grid_js.py`` established.
This doubles as a regression test that the relative import depth itself is
correct. Skips cleanly when Node isn't installed; the LIVE event mechanics
(sole-selection gating, focused-field bail-out, capture-phase consumption,
listener removal on node delete) are verified on the rig, not here.

Also covers the 2026-07-26 five-button transport strip + press-and-hold
auto-repeat (owner ask: "+5/-5 and a jump-to-start button; holding
-5/-1/+1/+5 should keep moving you through the timeline"). None of THAT
code is pure/exported -- it only runs inside ``attach()``/
``buildControlStrip()`` against a real DOM widget, which this repo has no
browser harness to simulate -- so those assertions match directly on the
module's SOURCE TEXT (button order/glyphs, which buttons are wired to the
hold-repeat helper, every teardown path, and the ``MIN_NODE_WIDTH`` floor)
via the ``frame_saver_source`` fixture below, robust to incidental
whitespace but strict on identifiers/structure. The live event mechanics
(an actual press-and-hold, pointer capture, window blur) are verified on
the rig, not here -- see this change's manual-verification notes.
"""

# ruff: noqa: E501, RUF001, RUF002, RUF003 — case tables and descriptive
# assert messages read better unwrapped (E501); several assertions below
# also hold the EXACT glyphs frame_saver.js's buttons use verbatim -- e.g.
# the real U+2212 MINUS SIGN in '−1'/'−5', matching the button text itself,
# not an accidental look-alike (RUF001-3). All four scoped to this file.

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FRAME_SAVER_JS = REPO_ROOT / "web" / "eps_image" / "frame_saver.js"
ROUTES_FRAME_SAVER_PY = REPO_ROOT / "eps_image" / "routes_frame_saver.py"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

# --------------------------------------------------------------------- cases
# Each list pairs (clipboard/pasted input, expected helper output). The
# inputs are shipped into the probe below; the expectations stay here so a
# failure names the exact case in pytest's diff.

#: stripWrappingQuotes — exactly ONE pair of matching wrapping quotes comes
#: off (Explorer's "Copy as path" double-quotes; shells single-quote), with
#: trimming; everything else survives untouched.
QUOTE_CASES = [
    ('"C:\\clips\\take 1.mp4"', "C:\\clips\\take 1.mp4"),
    ("'/Users/eric/clip.mp4'", "/Users/eric/clip.mp4"),
    ('  "D:\\v\\a.mp4"  ', "D:\\v\\a.mp4"),
    ('"unbalanced', '"unbalanced'),  # no matching pair -> unchanged
    ("it's fine.mp4", "it's fine.mp4"),  # inner apostrophe survives
    ('""', ""),
    ("", ""),
]

#: fileUrlToPath — file:// URLs decode to plain paths (percent-escapes,
#: the /C:/ drive form, localhost, and a real host -> UNC); non-URLs pass
#: through; a malformed %-escape keeps the raw pathname (fails soft).
FILE_URL_CASES = [
    ("file:///Users/eric/My%20Videos/clip.mp4", "/Users/eric/My Videos/clip.mp4"),
    ("file:///C:/clips/video.mp4", "C:/clips/video.mp4"),
    ("file://localhost/Users/eric/clip.mp4", "/Users/eric/clip.mp4"),
    ("file://nas/share/clip.mp4", "\\\\nas\\share\\clip.mp4"),
    ("FILE:///tmp/clip.mp4", "/tmp/clip.mp4"),  # scheme is case-insensitive
    ("/already/plain.mp4", "/already/plain.mp4"),
    ("file:///Users/eric/100%.mp4", "/Users/eric/100%.mp4"),
]

#: cleanPastedVideoPath — the composite: first non-empty line (Finder
#: multi-select copies one path per line), quotes, file://, trim.
CLEAN_CASES = [
    ('"D:\\video\\clip.mp4"\r\n', "D:\\video\\clip.mp4"),
    ("\n\n/Users/eric/clip.mp4\n/Users/eric/other.mp4\n", "/Users/eric/clip.mp4"),
    ("   /Users/eric/a.mp4   ", "/Users/eric/a.mp4"),
    ("'file:///tmp/a%20b.mp4'", "/tmp/a b.mp4"),  # quotes first, THEN file://
    ("", ""),
    ("   \n  \n", ""),
]

#: pathExtension — mirrors Python ``Path.suffix`` semantics, lowercased
#: (last dot of the LAST component; leading dot (`.hidden`) / trailing dot
#: (`name.`) is NO extension). Expectations are hardcoded rather than
#: computed from the local Python on purpose: any drift on the exotic
#: shapes is harmless (the client ACCEPTS no-extension paths and lets the
#: probe route be the authority), while the common shapes below are the
#: real contract.
EXTENSION_CASES = [
    ("/a/b/clip.mp4", ".mp4"),
    ("C:\\clips\\CLIP.MOV", ".mov"),
    ("/a/.hidden", ""),
    ("/a/name.", ""),
    ("/a/noext", ""),
    ("/a/archive.tar.gz", ".gz"),
    ("\\\\nas\\share\\clip.webm", ".webm"),
]

#: looksAbsolutePath — POSIX, Windows drive (either slash), and UNC are
#: path-shaped; relative/homedir/empty are not.
ABSOLUTE_CASES = [
    ("/Users/eric/clip.mp4", True),
    ("C:\\clips\\a.mp4", True),
    ("D:/clips/a.mp4", True),
    ("\\\\nas\\share\\a.mp4", True),
    ("clips/a.mp4", False),
    ("~/clips/a.mp4", False),
    ("", False),
    ("file.mp4", False),
]

#: evaluatePastedText — (input, expected action, expected path). 'ignore'
#: means the paste event is NOT consumed (core's pipeline still sees it);
#: 'reject' consumes but leaves the current video alone; 'accept' routes
#: to chooseVideoPath (the Browse code path).
VERDICT_CASES = [
    ("", "ignore", ""),
    ("hello world", "ignore", ""),
    ('{"nodes": [], "links": []}', "ignore", ""),  # workflow JSON stays core's
    ("relative/clip.mp4", "ignore", ""),
    ('"C:\\clips\\shot 01.mp4"', "accept", "C:\\clips\\shot 01.mp4"),
    ("file:///Users/eric/My%20Videos/clip.webm", "accept", "/Users/eric/My Videos/clip.webm"),
    ("/Users/eric/pic.png", "reject", "/Users/eric/pic.png"),
    ("/Users/eric/no_extension", "accept", "/Users/eric/no_extension"),  # probe decides
    ("/Users/eric/clip.MOV", "accept", "/Users/eric/clip.MOV"),  # case survives; POSIX is case-sensitive
    ("/" + "a" * 5000 + ".mp4", "ignore", ""),  # over-long: a document, not a path
]

#: isTextEntryElement — duck-typed DOM stand-ins (what the real handler
#: checks event.target/document.activeElement against).
TEXT_ENTRY_EXPECTED = [False, True, True, True, False, True, False]

PROBE_JS = """
import * as fs from './extensions/comfyui-epsnodes/eps_image/frame_saver.js'

const CASES = %(cases)s

const out = {
  exports: {
    hasInit: typeof fs.init === 'function',
    hasAttach: typeof fs.attach === 'function',
    hasLoadedGraphNode: typeof fs.loadedGraphNode === 'function'
  },
  videoExtList: fs.VIDEO_EXT_LIST,
  quotes: CASES.quotes.map((text) => fs.stripWrappingQuotes(text)),
  fileUrls: CASES.fileUrls.map((text) => fs.fileUrlToPath(text)),
  cleaned: CASES.cleaned.map((text) => fs.cleanPastedVideoPath(text)),
  extensions: CASES.extensions.map((path) => fs.pathExtension(path)),
  absolutes: CASES.absolutes.map((path) => fs.looksAbsolutePath(path)),
  verdicts: CASES.verdicts.map((text) => fs.evaluatePastedText(text)),
  textEntry: [
    fs.isTextEntryElement(null),
    fs.isTextEntryElement({ tagName: 'INPUT' }),
    fs.isTextEntryElement({ tagName: 'textarea' }),
    fs.isTextEntryElement({ tagName: 'SELECT' }),
    fs.isTextEntryElement({ tagName: 'CANVAS' }),
    fs.isTextEntryElement({ tagName: 'DIV', isContentEditable: true }),
    fs.isTextEntryElement({ tagName: 'DIV' })
  ]
}

process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def paste_api(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Runs the probe against the REAL frame_saver.js in a served-layout tmp
    dir (see module docstring) and returns its JSON output."""
    layout = tmp_path_factory.mktemp("web_root")

    scripts = layout / "scripts"
    scripts.mkdir()
    # The module touches `api`/`app` only lazily (fetches, canvas selection,
    # toast plumbing); bare objects exercise the same optional-chaining the
    # browser path relies on.
    (scripts / "api.js").write_text("export const api = {}\n", encoding="utf-8")
    (scripts / "app.js").write_text("export const app = {}\n", encoding="utf-8")

    module_dir = layout / "extensions" / "comfyui-epsnodes" / "eps_image"
    module_dir.mkdir(parents=True)
    shutil.copyfile(FRAME_SAVER_JS, module_dir / "frame_saver.js")

    cases = {
        "quotes": [text for text, _ in QUOTE_CASES],
        "fileUrls": [text for text, _ in FILE_URL_CASES],
        "cleaned": [text for text, _ in CLEAN_CASES],
        "extensions": [path for path, _ in EXTENSION_CASES],
        "absolutes": [path for path, _ in ABSOLUTE_CASES],
        "verdicts": [text for text, _, _ in VERDICT_CASES],
    }
    probe = layout / "probe.mjs"
    probe.write_text(PROBE_JS % {"cases": json.dumps(cases)}, encoding="utf-8")

    result = subprocess.run(
        [NODE, str(probe)], capture_output=True, text=True, timeout=60, cwd=layout
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def frame_saver_source() -> str:
    """The raw text of frame_saver.js -- for assertions about SOURCE
    STRUCTURE (transport-button order/wiring/teardown/layout constants) that
    can't be driven through the Node probe above, since none of it is
    pure/exported: it only runs inside ``attach()``/``buildControlStrip()``
    against a real DOM widget, which this repo has no browser harness to
    simulate. Kept as its own fixture (rather than folded into ``paste_api``)
    so a failure here reads as "the file's TEXT doesn't say what we expect",
    not "the runtime behavior differs" -- two different failure modes worth
    telling apart.
    """
    return FRAME_SAVER_JS.read_text(encoding="utf-8")


def _function_body(source: str, signature: str) -> str:
    """The body of a top-level ``function <signature> {`` declaration, up to
    its closing brace at column 0. This file's top-level functions are
    always 2-space-indented internally with the closing ``}`` alone on its
    own unindented line, so a bare ``\\n}\\n`` reliably marks the end --
    used below to scope assertions to one function's body rather than
    matching anywhere in the file.
    """
    start_match = re.search(re.escape(f"function {signature} {{") + r"\n", source)
    assert start_match, f"function {signature} {{ not found"
    start = start_match.end()
    end_match = re.search(r"\n\}\n", source[start:])
    assert end_match, f"function {signature}'s closing brace not found"
    return source[start : start + end_match.start()]


def _button_glyph(source: str, state_field: str) -> str:
    """The literal ``text:`` glyph assigned to ``state.<state_field>`` in
    buildControlStrip -- e.g. ``_button_glyph(src, "jumpStartBtn")`` -> '⏮'.
    """
    pattern = re.compile(
        r"state\." + re.escape(state_field) + r"\s*=\s*el\('button',\s*\{.*?text:\s*'([^']*)'",
        re.DOTALL,
    )
    match = pattern.search(source)
    assert match, f"could not find state.{state_field}'s button literal"
    return match.group(1)


def test_frame_saver_js_parses() -> None:
    """`node --check` — the file must at minimum be valid ES module syntax."""
    result = subprocess.run(
        [NODE, "--check", str(FRAME_SAVER_JS)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


def test_module_still_exports_the_extension_entry_points(paste_api: dict) -> None:
    """web/eps_image.js consumes init()/attach()/loadedGraphNode(); the test
    exports must never displace them."""
    assert paste_api["exports"] == {
        "hasInit": True,
        "hasAttach": True,
        "hasLoadedGraphNode": True,
    }


# ------------------------------------------------------------- path cleanup


def test_strip_wrapping_quotes(paste_api: dict) -> None:
    """Explorer's "Copy as path" double-quotes and shell single-quotes come
    off (one pair, trimmed); unbalanced or inner quotes survive."""
    for (given, expected), got in zip(QUOTE_CASES, paste_api["quotes"], strict=True):
        assert got == expected, f"stripWrappingQuotes({given!r}) -> {got!r}, wanted {expected!r}"


def test_file_url_decoding(paste_api: dict) -> None:
    """file:// URLs decode to plain server-openable paths: percent-escapes,
    the /C:/ Windows-drive form, localhost, host -> UNC; non-URLs pass
    through; malformed escapes fail soft to the raw pathname."""
    for (given, expected), got in zip(FILE_URL_CASES, paste_api["fileUrls"], strict=True):
        assert got == expected, f"fileUrlToPath({given!r}) -> {got!r}, wanted {expected!r}"


def test_clean_pasted_video_path_composite(paste_api: dict) -> None:
    """The full cleanup pipeline: first non-empty line, quotes, file://,
    trim — the exact transform the paste handler applies to clipboard
    text before judging it."""
    for (given, expected), got in zip(CLEAN_CASES, paste_api["cleaned"], strict=True):
        assert got == expected, f"cleanPastedVideoPath({given!r}) -> {got!r}, wanted {expected!r}"


# --------------------------------------------------------- path classifying


def test_path_extension_mirrors_python_suffix_semantics(paste_api: dict) -> None:
    """Lowercased last-dot-of-last-component, with `.hidden`/`name.` as NO
    extension — the same verdict routes_frame_saver.py's Path.suffix check
    reaches, so client and server never disagree on the common shapes."""
    for (given, expected), got in zip(EXTENSION_CASES, paste_api["extensions"], strict=True):
        assert got == expected, f"pathExtension({given!r}) -> {got!r}, wanted {expected!r}"


def test_looks_absolute_path(paste_api: dict) -> None:
    """POSIX roots, Windows drives (either slash), and UNC shares are
    path-shaped; relative/homedir/empty text is not (and so is never
    consumed by the paste handler)."""
    for (given, expected), got in zip(ABSOLUTE_CASES, paste_api["absolutes"], strict=True):
        assert got is expected, f"looksAbsolutePath({given!r}) -> {got!r}, wanted {expected!r}"


# ------------------------------------------------------------ whole verdict


def test_evaluate_pasted_text_verdicts(paste_api: dict) -> None:
    """The composite decision: non-paths are ignored (event NOT consumed —
    workflow JSON etc. stays core's), wrong-extension paths reject without
    clobbering the current video, video/no-extension absolute paths accept
    into the Browse code path with their original casing intact."""
    for (given, action, path), got in zip(VERDICT_CASES, paste_api["verdicts"], strict=True):
        assert got["action"] == action, f"evaluatePastedText({given!r}) -> {got}, wanted {action}"
        assert got["path"] == path, f"evaluatePastedText({given!r}) path {got['path']!r} != {path!r}"


def test_reject_reason_names_the_extension_and_the_allowlist(paste_api: dict) -> None:
    """The one reject case's user-facing reason must say WHAT was wrong
    (.png) and what would be accepted (the allowlist), matching the
    server's own rejection vocabulary."""
    rejects = [v for v in paste_api["verdicts"] if v["action"] == "reject"]
    assert len(rejects) == 1
    reason = rejects[0]["reason"]
    assert ".png" in reason
    assert ".mp4" in reason and ".mov" in reason  # allowlist is spelled out


def test_video_ext_list_matches_backend_allowlist(paste_api: dict) -> None:
    """frame_saver.js documents VIDEO_EXT_LIST as hand-lockstepped with
    routes_frame_saver.py's VIDEO_EXTENSIONS — machine-check it, so the
    paste early-reject can never disagree with what probe/stream accept."""
    source = ROUTES_FRAME_SAVER_PY.read_text(encoding="utf-8")
    match = re.search(r"VIDEO_EXTENSIONS\s*=\s*(\([^)]*\))", source)
    assert match, "VIDEO_EXTENSIONS tuple not found in routes_frame_saver.py"
    backend = list(ast.literal_eval(match.group(1)))
    assert paste_api["videoExtList"] == backend


# ------------------------------------------------------------- typing guard


def test_is_text_entry_element_duck_typing(paste_api: dict) -> None:
    """input/textarea/select (any casing) and contenteditable hosts are
    typing surfaces the paste handler must never hijack; canvas/div/null
    are fair game."""
    assert paste_api["textEntry"] == TEXT_ENTRY_EXPECTED


# ------------------------------------------- transport strip + hold-repeat
# 2026-07-26 owner ask: "+5/-5 and a jump-to-start button; holding down the
# -5/-1/+1/+5 should keep moving you through the timeline". Source-text
# assertions only -- see frame_saver_source's docstring for why.

#: state field name -> its documented button glyph (buildControlStrip).
#: Proper Unicode minus for negative deltas (matching the pre-existing −1,
#: NOT an ASCII hyphen), plain ASCII plus for positive (matching +1) --
#: this file's own established convention, not a new one.
BUTTON_GLYPHS = {
    "jumpStartBtn": "⏮",
    "stepBack5Btn": "−5",
    "stepBackBtn": "−1",
    "stepFwdBtn": "+1",
    "stepFwd5Btn": "+5",
}


def test_transport_button_glyphs(frame_saver_source: str) -> None:
    """Compact, unambiguous glyphs matching the file's existing convention
    (real Unicode transport glyphs -- ▶/⏸ already shipped -- not ASCII
    approximations or an icon font)."""
    for field, expected in BUTTON_GLYPHS.items():
        assert _button_glyph(frame_saver_source, field) == expected, (
            f"state.{field}'s button glyph changed"
        )


def test_control_strip_five_buttons_in_documented_order(frame_saver_source: str) -> None:
    """buildControlStrip's returned epsfs-strip children must read, left to
    right: jump-to-start, −5, −1, PLAY/PAUSE, +1, +5, then the frame field
    and the counter.

    Owner ask 2026-07-26 added the five transport buttons; play/pause keeps
    the middle position it has always had, so the strip reads symmetrically
    (big-step / small-step / play / small-step / big-step) and the control
    the owner already reaches for does not move."""
    match = re.search(
        r"return el\('div',\s*\{\s*className:\s*'epsfs-strip'\s*\},\s*\[(.*?)\]\s*\)\s*\n\}",
        frame_saver_source,
        re.DOTALL,
    )
    assert match, "could not find buildControlStrip's returned epsfs-strip children array"
    children = [c.strip() for c in match.group(1).split(",")]
    children = [c for c in children if c]
    assert children == [
        "state.jumpStartBtn",
        "state.stepBack5Btn",
        "state.stepBackBtn",
        "state.playPauseBtn",
        "state.stepFwdBtn",
        "state.stepFwd5Btn",
        "state.frameInputEl",
        "state.counterEl",
    ]


def test_four_step_buttons_wired_to_hold_repeat_helper(frame_saver_source: str) -> None:
    """−5/−1/+1/+5 must each be wired through attachStepButton (the shared
    click + press-and-hold-repeat helper), with the matching delta -- "the
    new ones are the same operation with a different delta"."""
    for field, delta in [
        ("stepBack5Btn", "-5"),
        ("stepBackBtn", "-1"),
        ("stepFwdBtn", "1"),
        ("stepFwd5Btn", "5"),
    ]:
        pattern = rf"attachStepButton\(state,\s*state\.{field},\s*{re.escape(delta)}\)"
        assert re.search(pattern, frame_saver_source), (
            f"state.{field} must be wired via attachStepButton(state, state.{field}, {delta})"
        )


def test_jump_start_button_excluded_from_hold_repeat(frame_saver_source: str) -> None:
    """Jump-to-start is idempotent -- it must be driven by a plain click
    only, never routed through attachStepButton()/the repeat machinery."""
    assert "attachStepButton(state, state.jumpStartBtn" not in frame_saver_source
    assert re.search(
        r"state\.jumpStartBtn\.addEventListener\('click',.*?setFrame\(state,\s*0\)",
        frame_saver_source,
        re.DOTALL,
    ), "jumpStartBtn must plain-click straight to setFrame(state, 0)"


def test_step_repeaters_are_exactly_the_four_step_buttons(frame_saver_source: str) -> None:
    """state.stepRepeaters (what the window-blur handler and onRemoved both
    iterate to force-stop) must hold exactly the four step buttons'
    stopRepeat -- jump-to-start and play/pause are not hold-repeat controls
    and must never end up in this list."""
    match = re.search(r"state\.stepRepeaters\s*=\s*\[(.*?)\]", frame_saver_source, re.DOTALL)
    assert match, "state.stepRepeaters assignment not found"
    listed = match.group(1)
    for field in ("stepBack5Btn", "stepBackBtn", "stepFwdBtn", "stepFwd5Btn"):
        assert field in listed, f"state.{field} missing from state.stepRepeaters"
    for excluded in ("jumpStartBtn", "playPauseBtn"):
        assert excluded not in listed, f"state.{excluded} must not be in state.stepRepeaters"


def test_step_frame_is_the_single_shared_clamp_path(frame_saver_source: str) -> None:
    """attachStepButton must never clamp or commit on its own -- every
    trigger (the pointerdown's own first step, every repeat tick, and the
    keyboard-click guard) reaches the frame only through stepFrame(), which
    itself goes through the exact same clampFrame()/setFrame() every other
    frame change already uses (no bypass of the total-frames clamp)."""
    body = _function_body(frame_saver_source, "attachStepButton(state, button, delta)")
    assert "clampFrame(" not in body, "attachStepButton must not clamp directly -- go through stepFrame()"
    assert "setFrame(" not in body, "attachStepButton must not call setFrame directly -- go through stepFrame()"
    call_count = body.count("stepFrame(state, delta)")
    assert call_count == 3, (
        "expected exactly 3 `stepFrame(state, delta)` call sites in "
        f"attachStepButton (pointerdown's press, repeatTick, the keyboard "
        f"click guard); found {call_count}"
    )


def test_step_frame_skips_redundant_setframe_at_clamp_boundary(frame_saver_source: str) -> None:
    """stepFrame() must clamp via clampFrame() and bail out BEFORE calling
    setFrame() (which seeks the preview) when a step would not actually
    move the frame -- no redundant seek at the boundary, and the
    return-false lets the repeat loop stop itself."""
    body = _function_body(frame_saver_source, "stepFrame(state, delta)")
    assert "clampFrame(state," in body
    bail_pos = body.index("if (next === current) return false")
    set_frame_pos = body.index("setFrame(state, next)")
    assert bail_pos < set_frame_pos, "the no-op-boundary bailout must come BEFORE setFrame() is called"


def test_repeat_interval_guarded_against_boundary_hit_at_initial_delay(frame_saver_source: str) -> None:
    """If the very FIRST repeat tick (fired when the initial delay elapses)
    already lands on a clamp boundary -- e.g. holding −1 starting at frame
    0 -- that tick's stepFrame() return routes through stopRepeat() (which
    clears activePointerId) before the interval would otherwise be created.
    Creating `repeatInterval` must be conditioned on the gesture STILL being
    active at that point, not run unconditionally right after the first
    tick -- otherwise a boundary hit exactly at the delay would still arm
    one dead interval tick before self-cancelling on its own next firing,
    instead of never being created at all. Caught live on the rig: holding
    −1 from frame 0 was confirmed to stay at 0 for the whole hold, but the
    source still needed this guard to avoid that one extra wasted tick."""
    body = _function_body(frame_saver_source, "attachStepButton(state, button, delta)")
    hold_timer_match = re.search(
        r"holdTimer = setTimeout\(\(\) => \{(.*?)\}, HOLD_INITIAL_DELAY_MS\)", body, re.DOTALL
    )
    assert hold_timer_match, "holdTimer's setTimeout callback not found"
    callback_body = hold_timer_match.group(1)
    assert "repeatTick()" in callback_body
    guarded = re.search(
        r"if \(activePointerId !== null\)\s*\{\s*"
        r"repeatInterval = setInterval\(repeatTick, HOLD_REPEAT_INTERVAL_MS\)\s*\}",
        callback_body,
    )
    assert guarded, "setInterval(repeatTick, ...) must be guarded by `if (activePointerId !== null)`"
    assert callback_body.index("repeatTick()") < callback_body.index("activePointerId !== null"), (
        "the guard must check the state repeatTick() just set, so it must come AFTER repeatTick()"
    )


def test_attach_step_button_stops_on_pointer_release_events(frame_saver_source: str) -> None:
    """pointerup/pointercancel/pointerleave must all reach the same release
    (-> stopRepeat) handler -- gallery.js's attachHoldToCompare's identical
    three-event trio, so a hold that drifts off or releases outside the
    button still stops cleanly."""
    body = _function_body(frame_saver_source, "attachStepButton(state, button, delta)")
    for event_name in ("pointerup", "pointercancel", "pointerleave"):
        assert re.search(rf"button\.addEventListener\('{event_name}',\s*release\)", body), (
            f"attachStepButton must stop the repeat on {event_name}"
        )
    assert re.search(r"const release = .*?stopRepeat\(\)", body, re.DOTALL), (
        "release must actually call stopRepeat()"
    )


def test_hold_repeat_stops_on_window_blur(frame_saver_source: str) -> None:
    """Losing the window mid-hold must force every button's stopRepeat --
    otherwise a hold with no pointerup (e.g. Cmd/Alt-Tab away) leaks an
    interval forever."""
    assert re.search(
        r"state\.windowBlurHandler\s*=\s*\(\)\s*=>\s*\{\s*"
        r"for\s*\(const stop of state\.stepRepeaters\)\s*stop\(\)\s*\}",
        frame_saver_source,
    ), "windowBlurHandler must stop every button's repeat"
    assert "window.addEventListener('blur', state.windowBlurHandler)" in frame_saver_source


def test_hold_repeat_stops_on_node_removed(frame_saver_source: str) -> None:
    """wireNodeCleanup's onRemoved wrap must stop every in-flight repeat AND
    remove the window blur listener -- matching this file's existing
    cleanup discipline for the picker's keydown listener and the paste
    handler (both torn down in the same wrap)."""
    body = _function_body(frame_saver_source, "wireNodeCleanup(state)")
    assert "for (const stop of state.stepRepeaters) stop()" in body, (
        "onRemoved must force-stop every button's in-flight repeat"
    )
    assert "window.removeEventListener('blur', state.windowBlurHandler)" in body, (
        "onRemoved must remove the window blur listener"
    )


def test_min_node_width_meets_derived_floor(frame_saver_source: str) -> None:
    """MIN_NODE_WIDTH must be at least the CSS-exact sum the six-button
    strip needs: 6 buttons x 48px (30px content min-width + 16px padding +
    2px border, per `.epsfs-btn.epsfs-btn-icon`) + the frame field's 56px
    border-box floor + 7 gaps x 6px (8 flex children) + the strip's own 14px
    padding/border (12px + 2px, also border-box) = 400px -- see the
    constant's own docstring for the full derivation. Not pinned to an EXACT
    value on purpose: any width at/above the derived floor is a legitimate
    layout choice; below it is a guaranteed overflow."""
    match = re.search(r"const MIN_NODE_WIDTH = (\d+)", frame_saver_source)
    assert match, "MIN_NODE_WIDTH declaration not found"
    derived_floor = 6 * 48 + 56 + 7 * 6 + 14  # == 400
    assert int(match.group(1)) >= derived_floor, (
        f"MIN_NODE_WIDTH ({match.group(1)}) is below the derived floor ({derived_floor})"
    )


def test_no_hardcoded_pixel_width_in_css(frame_saver_source: str) -> None:
    """No `width: <N>px` on any CSS rule (min-width/max-width floors are
    fine and expected) -- the v0.30.3 Linux-overflow lesson this change must
    not regress: size to content with a min-width FLOOR, never a hard
    width, for anything that holds text."""
    css_match = re.search(r"const CSS_TEXT = `(.*?)`\n", frame_saver_source, re.DOTALL)
    assert css_match, "CSS_TEXT template literal not found"
    css = css_match.group(1)
    hardcoded = re.findall(r"(?<!min-)(?<!max-)\bwidth:\s*[\d.]+px", css)
    assert hardcoded == [], f"hardcoded pixel width(s) found in CSS: {hardcoded}"


class TestWiredVideoInput:
    """v0.60.0 (FORMAT.md §6.7): the optional wired `video` input's frontend
    half -- source pins, since everything here runs against a real litegraph
    node (the same convention as the rest of this file)."""

    def test_wired_state_has_three_shapes(self, frame_saver_source: str) -> None:
        body = _function_body(frame_saver_source, "resolveWiredVideo(node)")
        assert "'LoadVideo'" in body
        assert "{ kind: 'input_ref', ref, title }" in body
        assert "{ kind: 'opaque', title }" in body
        # reroutes are followed, with a hop cap (a loop must not hang attach)
        assert "'Reroute'" in body
        assert "hops < 32" in body

    def test_full_resync_rederives_the_wire_first(self, frame_saver_source: str) -> None:
        body = _function_body(frame_saver_source, "fullResync(state)")
        assert body.index("state.wired = resolveWiredVideo(state.node)") < body.index(
            "onPathChanged(state, state.pathWidget.value)"
        )

    def test_input_ref_streams_ungated_and_probes_by_ref(self, frame_saver_source: str) -> None:
        body = _function_body(frame_saver_source, "refreshVideoSource(state)")
        assert "input_ref=${encodeURIComponent(state.wired.ref)}" in body
        assert "startProbe(state, { input_ref: state.wired.ref })" in body
        # the input_ref branch must come BEFORE the isLocal gate branch --
        # a remote viewer gets the full scrubber for LoadVideo sources.
        assert body.index("state.wired?.kind === 'input_ref'") < body.index(
            "state.isLocal === false"
        )

    def test_probe_403_adoption_is_path_mode_only(self, frame_saver_source: str) -> None:
        body = _function_body(frame_saver_source, "startProbe(state, params)")
        assert "error?.status === 403 && params.path" in body

    def test_opaque_wire_keeps_the_frame_field_live(self, frame_saver_source: str) -> None:
        controls = _function_body(frame_saver_source, "updateControlsEnabled(state)")
        assert "Boolean(state.path) || Boolean(state.wired)" in controls
        overlay = _function_body(frame_saver_source, "currentOverlayMessage(state)")
        assert "when the workflow runs" in overlay
        assert "type a frame number" in overlay

    def test_wire_changes_resync_through_the_standard_wrap(self, frame_saver_source: str) -> None:
        body = _function_body(frame_saver_source, "attach(node)")
        assert "node.onConnectionsChange = function (...args)" in body
        assert "fullResync(state)" in body

    def test_path_bar_names_the_wire_not_the_stale_path(self, frame_saver_source: str) -> None:
        body = _function_body(frame_saver_source, "updatePathBarText(state)")
        assert "⇐ wired:" in body


# ------------------------------------------------------------ v0.68.1 pins


class TestV0681ResyncIsChangeGuarded:
    """v0.68.1 (2026-08-21): fullResync used to reload the <video> and
    re-probe UNCONDITIONALLY from every restore hook and every
    onConnectionsChange (output-side rewires included), and configure()'s
    per-link replay fired it once per saved link. Source pins, since all of
    it runs against a real litegraph node (this file's convention)."""

    def test_full_resync_compares_the_source_key_before_reloading(
        self, frame_saver_source: str
    ) -> None:
        body = _function_body(frame_saver_source, "fullResync(state)")
        # wire first (unchanged), then the compare, then the reload path
        assert body.index("state.wired = resolveWiredVideo(state.node)") < body.index(
            "sourceKeyOf(state, state.pathWidget.value) === state.sourceKey"
        )
        assert body.index("=== state.sourceKey") < body.index(
            "onPathChanged(state, state.pathWidget.value)"
        )
        # the no-change branch still re-syncs the path bar, and only that:
        # no direct refreshVideoSource call anywhere in fullResync (the only
        # reload path is onPathChanged, behind the early return).
        assert "updatePathBarText(state)" in body
        assert "refreshVideoSource(" not in body

    def test_source_key_covers_everything_refresh_video_source_branches_on(
        self, frame_saver_source: str
    ) -> None:
        key = _function_body(frame_saver_source, "sourceKeyOf(state, rawPath)")
        assert "state.wired?.kind ?? null" in key
        assert "state.wired?.ref ?? null" in key
        assert "String(rawPath || '').trim()" in key
        assert "state.isLocal === false" in key
        refresh = _function_body(frame_saver_source, "refreshVideoSource(state)")
        assert "state.sourceKey = sourceKeyOf(state, state.path)" in refresh
        # the stale-probe discard is untouched
        assert "state.probeToken += 1" in refresh

    def test_gating_flip_reloads_only_when_the_verdict_changes_the_picture(
        self, frame_saver_source: str
    ) -> None:
        body = _function_body(frame_saver_source, "applyGating(state)")
        assert "if (sourceKeyOf(state, state.path) !== state.sourceKey) refreshVideoSource(state)" in body

    def test_a_deliberate_pick_is_not_guarded(self, frame_saver_source: str) -> None:
        """§7.2's equal-value re-pick lesson: Browse/paste of the SAME file
        must still reload -- chooseVideoPath goes straight to onPathChanged,
        which stays unconditional."""
        pick = _function_body(frame_saver_source, "chooseVideoPath(state, path)")
        assert "onPathChanged(state, path)" in pick
        changed = _function_body(frame_saver_source, "onPathChanged(state, rawPath)")
        assert "refreshVideoSource(state)" in changed
        assert "sourceKey" not in changed

    def test_configure_replay_is_skipped_via_a_restoring_flag(
        self, frame_saver_source: str
    ) -> None:
        wire = _function_body(frame_saver_source, "wireConfigureResync(state)")
        assert "const originalConfigure = node.configure" in wire
        assert "state.restoring = true" in wire
        assert "state.restoring = false" in wire
        assert wire.index("try {") < wire.index("} finally {")
        attach = _function_body(frame_saver_source, "attach(node)")
        assert "if (!state.restoring) fullResync(state)" in attach
        # both fields are declared on the state, with the rest of it
        state = _function_body(frame_saver_source, "createState(node, pathWidget, frameWidget)")
        assert "sourceKey: null" in state
        assert "restoring: false" in state


def test_width_floor_lifts_through_set_size_not_a_dead_is_array_guard(
    frame_saver_source: str,
) -> None:
    """v0.68.1: `LGraphNode.size` is a Proxy over a typed-array view on the
    installed frontend (1.48.7), never an Array, so installMinWidth's old
    `Array.isArray(node.size)` lift never ran (FORMAT.md §7.2's "lifts the
    CURRENT width" half); only the onResize wrap ever floored anything. The
    lift goes through setSize() (litegraph's `_sizeUpdated` + onResize)."""
    assert "Array.isArray(node.size)" not in frame_saver_source
    body = _function_body(frame_saver_source, "installMinWidth(node, minWidth)")
    assert "if (node.size && node.size[0] < minWidth)" in body
    assert "node.setSize([minWidth, node.size[1]])" in body
