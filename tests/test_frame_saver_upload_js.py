"""Frontend tests for EPS Frame Saver's "Upload…" button / drag-and-drop
(owner report 2026-09-10: "[EPS Frame Saver] only lets you select a frame
from a host machine, and not from a machine that is connected to it (like
one of my macs) ... this makes the component mostly unusable for me").

Mirrors ``test_frame_saver_paste_js.py``'s two-pronged approach exactly:

- **Pure, exported helpers** (``isInputRefVideoPath``, ``annotatedInputRef``,
  ``displayPathText``, ``sizeWarningMessage``, ``oversizeUploadMessage``) are
  driven under Node via the same served-layout probe-script fixture that
  file established (stub ``scripts/api.js``/``scripts/app.js``, the real
  module byte-copied in at the right relative depth).
- **Non-pure logic** (``effectiveSource``, ``refreshVideoSource``,
  ``applyGating``, ``buildPathBar``, ``startUpload``, ``wireNodeCleanup``,
  ``attach``, ``installVideoDragAndDrop``) only runs against a real DOM
  widget / litegraph node, which this repo has no browser harness for -- so
  those are pinned via SOURCE-TEXT assertions against the raw file, the
  same ``frame_saver_source``/``_function_body`` idiom
  ``test_frame_saver_paste_js.py`` uses (duplicated here rather than
  imported -- this repo's test files, like its ``eps_image/*.js`` modules,
  are self-contained by convention).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FRAME_SAVER_JS = REPO_ROOT / "web" / "eps_image" / "frame_saver.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

# --------------------------------------------------------------------- cases

#: isInputRefVideoPath -- absolute paths (any OS shape) are PATH mode;
#: everything else (including a bare filename, as an unwired core LoadVideo
#: leaves it) is treated as an annotated input ref.
INPUT_REF_CASES = [
    ("", False),
    ("/Users/eric/clip.mp4", False),
    ("C:\\clips\\a.mp4", False),
    ("D:/clips/a.mp4", False),
    ("\\\\nas\\share\\a.mp4", False),
    ("clip.mp4", True),  # bare filename -- e.g. an unwired core LoadVideo ref
    ("clip.mp4 [input]", True),
    ("shots/clip.mp4 [input]", True),
]

#: annotatedInputRef -- core's `/upload/image` response shape -> the exact
#: annotated string `folder_paths.get_annotated_filepath` resolves.
ANNOTATED_REF_CASES = [
    ({"name": "clip.mp4", "subfolder": "", "type": "input"}, "clip.mp4 [input]"),
    ({"name": "clip.mp4", "subfolder": "shots", "type": "input"}, "shots/clip.mp4 [input]"),
    ({"name": "clip.mp4"}, "clip.mp4 [input]"),  # type defaults to input
    ({"name": "clip.mp4", "type": "temp"}, "clip.mp4 [temp]"),
    ({"name": "a b.mov", "subfolder": "my clips"}, "my clips/a b.mov [input]"),
]

#: displayPathText -- strips a trailing annotation bracket for DISPLAY only;
#: a literal path (no bracket) passes through unchanged.
DISPLAY_PATH_CASES = [
    ("clip.mp4 [input]", "clip.mp4"),
    ("shots/clip.mp4 [input]", "shots/clip.mp4"),
    ("clip.mp4 [output]", "clip.mp4"),
    ("clip.mp4 [temp]", "clip.mp4"),
    ("clip.mp4", "clip.mp4"),
    ("/Users/eric/clip.mp4", "/Users/eric/clip.mp4"),
    ("", ""),
]

PROBE_JS = """
import * as fs from './extensions/comfyui-epsnodes/eps_image/frame_saver.js'

const out = {
  inputRefs: %(input_refs)s.map((path) => fs.isInputRefVideoPath(path)),
  annotatedRefs: %(annotated_refs)s.map((uploaded) => fs.annotatedInputRef(uploaded)),
  displayPaths: %(display_paths)s.map((path) => fs.displayPathText(path)),
  sizeWarning100: fs.sizeWarningMessage(100 * 1024 * 1024),
  sizeWarning150: fs.sizeWarningMessage(150 * 1024 * 1024),
  sizeWarningKnown: fs.sizeWarningMessage(340 * 1024 * 1024, 100 * 1024 * 1024),
  oversize: fs.oversizeUploadMessage(),
  oversizeKnown: fs.oversizeUploadMessage(200 * 1024 * 1024)
}

process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def upload_api(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Runs the probe against the REAL frame_saver.js in a served-layout tmp
    dir (see module docstring / test_frame_saver_paste_js.py's identical
    fixture) and returns its JSON output."""
    layout = tmp_path_factory.mktemp("web_root")

    scripts = layout / "scripts"
    scripts.mkdir()
    (scripts / "api.js").write_text("export const api = {}\n", encoding="utf-8")
    (scripts / "app.js").write_text("export const app = {}\n", encoding="utf-8")

    module_dir = layout / "extensions" / "comfyui-epsnodes" / "eps_image"
    module_dir.mkdir(parents=True)
    shutil.copyfile(FRAME_SAVER_JS, module_dir / "frame_saver.js")

    probe = layout / "probe.mjs"
    probe.write_text(
        PROBE_JS
        % {
            "input_refs": json.dumps([path for path, _ in INPUT_REF_CASES]),
            "annotated_refs": json.dumps([uploaded for uploaded, _ in ANNOTATED_REF_CASES]),
            "display_paths": json.dumps([path for path, _ in DISPLAY_PATH_CASES]),
        },
        encoding="utf-8",
    )

    result = subprocess.run(
        [NODE, str(probe)], capture_output=True, text=True, timeout=60, cwd=layout
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def frame_saver_source() -> str:
    """Raw text of frame_saver.js -- see module docstring's "non-pure
    logic" section for why source-text assertions are this repo's answer
    for anything that only runs against a real DOM widget."""
    return FRAME_SAVER_JS.read_text(encoding="utf-8")


def _function_body(source: str, signature: str) -> str:
    """Duplicated from test_frame_saver_paste_js.py's identical helper (see
    that file's own docstring for the exact contract) -- this repo's test
    files, like its eps_image/*.js modules, are self-contained by
    convention rather than importing from a sibling."""
    start_match = re.search(re.escape(f"function {signature} {{") + r"\n", source)
    assert start_match, f"function {signature} {{ not found"
    start = start_match.end()
    end_match = re.search(r"\n\}\n", source[start:])
    assert end_match, f"function {signature}'s closing brace not found"
    return source[start : start + end_match.start()]


# --------------------------------------------------------------- pure helpers


def test_is_input_ref_video_path(upload_api: dict) -> None:
    """An absolute path (ANY OS shape) is PATH mode; anything else --
    including a bare filename, exactly what an unwired core LoadVideo's
    `file` combo holds -- is treated as an annotated input ref. Mirrors
    `nodes_frame_saver.py`'s `_resolve_execution_path`
    (`Path(value).is_absolute()`) bit-for-bit."""
    for (given, expected), got in zip(INPUT_REF_CASES, upload_api["inputRefs"], strict=True):
        assert got is expected, f"isInputRefVideoPath({given!r}) -> {got!r}, wanted {expected!r}"


def test_annotated_input_ref_matches_get_annotated_filepath_shape(upload_api: dict) -> None:
    """core's `/upload/image` response -> the exact annotated string
    `folder_paths.get_annotated_filepath`/`exists_annotated_filepath`
    resolve (bracket suffix + optional subfolder prefix)."""
    cases = zip(ANNOTATED_REF_CASES, upload_api["annotatedRefs"], strict=True)
    for (given, expected), got in cases:
        assert got == expected, f"annotatedInputRef({given!r}) -> {got!r}, wanted {expected!r}"


def test_display_path_text_strips_the_annotation_only(upload_api: dict) -> None:
    """Cosmetic only -- a literal path (Browse/paste, no bracket) is
    returned byte-identical."""
    for (given, expected), got in zip(DISPLAY_PATH_CASES, upload_api["displayPaths"], strict=True):
        assert got == expected, f"displayPathText({given!r}) -> {got!r}, wanted {expected!r}"


def test_size_warning_names_the_actual_size_and_the_default_ceiling(upload_api: dict) -> None:
    """Task: warn BEFORE uploading when the file is over the DEFAULT
    ceiling, naming both the file's own size and ComfyUI's default limit +
    how to raise it -- never a silent decision to try anyway."""
    message = upload_api["sizeWarning150"]
    assert "150 MB" in message
    assert "100 MB" in message
    assert "--max-upload-size" in message
    assert "may be rejected" in message


def test_oversize_upload_message_names_the_flag_and_default(upload_api: dict) -> None:
    """Task: on HTTP 413, say PLAINLY the video is over the server's limit
    and name the exact flag (`--max-upload-size <MB>`) and its default."""
    message = upload_api["oversize"]
    assert "upload limit" in message
    assert "--max-upload-size <MB>" in message
    assert "default 100" in message


def test_size_warning_with_the_servers_reported_limit_states_it_plainly(
    upload_api: dict,
) -> None:
    """Lead refinement 2026-09-10: ComfyUI REPORTS its real ceiling at
    `GET /features` -> `max_upload_size` (bytes; rig-verified 104857600), so
    when it is known the warning names that figure and says the upload will
    likely be refused rather than hedging about a default. The owner runs
    ComfyUI Desktop, where the limit is a settings field, so the message
    must point there as well as at the CLI flag."""
    message = upload_api["sizeWarningKnown"]
    assert "340 MB" in message
    assert "up to 100 MB" in message
    assert "likely be refused" in message
    assert "Server-Config" in message and "Maximum Upload Size" in message
    assert "--max-upload-size <MB>" in message
    assert "did not report" not in message  # the fallback's hedge must not leak in


def test_oversize_message_names_the_reported_limit_and_the_desktop_setting(
    upload_api: dict,
) -> None:
    message = upload_api["oversizeKnown"]
    assert "200 MB upload limit" in message
    assert "Server-Config" in message and "Maximum Upload Size" in message
    assert "restart" in message


# ------------------------------------------------------- non-pure: source text


class TestUploadFrontend:
    """Everything below only runs against a real DOM widget / litegraph
    node -- source-text pins, per module docstring."""

    def test_upload_button_visible_local_and_remote_browse_stays_host_only(
        self, frame_saver_source: str
    ) -> None:
        gating = _function_body(frame_saver_source, "applyGating(state)")
        assert "state.browseBtn" in gating
        assert "uploadBtn" not in gating  # never gated -- the actual fix for the report
        bar = _function_body(frame_saver_source, "buildPathBar(state)")
        assert "state.uploadBtn = el('button'" in bar
        assert "state.browseBtn," in bar and "state.uploadBtn," in bar

    def test_upload_button_doubles_as_cancel(self, frame_saver_source: str) -> None:
        bar = _function_body(frame_saver_source, "buildPathBar(state)")
        assert "if (state.upload) {" in bar
        assert "state.upload.abort()" in bar
        assert "ensureUploadInput(state).click()" in bar

    def test_effective_source_wired_always_wins(self, frame_saver_source: str) -> None:
        body = _function_body(frame_saver_source, "effectiveSource(state)")
        # wired-wins is the FIRST check -- an uploaded/annotated video_path
        # must never override an actual wire.
        assert body.strip().startswith("if (state.wired) return state.wired")
        assert "isInputRefVideoPath(state.path)" in body
        assert "{ kind: 'input_ref', ref: state.path }" in body
        assert "{ kind: 'path', path: state.path }" in body
        assert "{ kind: 'none' }" in body

    def test_refresh_video_source_routes_through_effective_source(
        self, frame_saver_source: str
    ) -> None:
        # An unwired, input-ref-shaped video_path (an upload) must reach the
        # SAME ungated ?input_ref= stream/probe branch a wired LoadVideo
        # already used -- not a parallel preview path (task item 4).
        body = _function_body(frame_saver_source, "refreshVideoSource(state)")
        assert "const source = effectiveSource(state)" in body
        assert "source.kind === 'input_ref'" in body
        assert body.count("api.apiURL(`/eps_frame_saver/stream?input_ref=") == 1
        assert body.count("api.apiURL(`/eps_frame_saver/stream?path=") == 1

    def test_overlay_does_not_claim_host_only_for_an_uploaded_path(
        self, frame_saver_source: str
    ) -> None:
        overlay = _function_body(frame_saver_source, "currentOverlayMessage(state)")
        assert "isInputRefVideoPath(state.path)" in overlay
        assert "Preview + probing require the machine running ComfyUI" in overlay

    def test_ensure_upload_input_narrows_accept_to_the_shared_ext_list(
        self, frame_saver_source: str
    ) -> None:
        body = _function_body(frame_saver_source, "ensureUploadInput(state)")
        assert "input.type = 'file'" in body
        assert "input.accept = VIDEO_EXT_PARAM" in body
        # single-file (this node holds exactly one video) -- unlike
        # image_grid.js's `multiple = true` picker.
        assert "input.multiple" not in body

    def test_upload_never_sends_a_subfolder_lands_in_the_input_root(
        self, frame_saver_source: str
    ) -> None:
        # So the file also shows up in core's OWN Load Video dropdown
        # (task item 2: "least surprise") -- LoadVideo's combo only lists
        # files directly in the input ROOT, not subfolders.
        body = _function_body(frame_saver_source, "uploadVideoFile(file, onProgress)")
        assert "formData.append('image', file, file.name)" in body
        assert "subfolder" not in body

    def test_upload_uses_xhr_not_fetch_for_progress(self, frame_saver_source: str) -> None:
        body = _function_body(frame_saver_source, "uploadVideoFile(file, onProgress)")
        assert "new XMLHttpRequest()" in body
        assert "xhr.upload.addEventListener('progress'" in body
        assert "event.lengthComputable" in body
        return_stmt = re.search(r"return \{ promise, abort:.*\}", body)
        assert return_stmt, "uploadVideoFile must return {promise, abort} for Cancel"

    def test_upload_rejection_carries_a_status_for_413_branching(
        self, frame_saver_source: str
    ) -> None:
        body = _function_body(frame_saver_source, "uploadVideoFile(file, onProgress)")
        assert "error.status = xhr.status" in body
        assert "error.cancelled = true" in body

    def test_start_upload_shows_precheck_warning_before_uploading_oversize_files(
        self, frame_saver_source: str
    ) -> None:
        body = _function_body(frame_saver_source, "startUpload(state, file)")
        # The ceiling is now the server's REPORTED limit, defaulting to 100 MB
        # only when /features can't be read (lead refinement 2026-09-10).
        assert "const oversizeAlready = file.size > limitBytes" in body
        assert "reportedLimit ?? DEFAULT_MAX_UPLOAD_MB * 1024 * 1024" in body
        assert "sizeWarningMessage(file.size, reportedLimit)" in body
        # still uploads anyway -- never a hard block (task item 7)
        assert "uploadVideoFile(file," in body

    def test_start_upload_413_and_silent_oversize_failure_both_get_the_loud_message(
        self, frame_saver_source: str
    ) -> None:
        body = _function_body(frame_saver_source, "startUpload(state, file)")
        assert "error?.status === 413" in body
        assert "oversizeAlready" in body
        assert "oversizeUploadMessage(reportedLimit)" in body

    def test_start_upload_claims_the_busy_slot_before_awaiting_the_limit(
        self, frame_saver_source: str
    ) -> None:
        """Reading the server's limit is async; the slot must be claimed
        first or a double click/drop in that gap starts two uploads -- and a
        Cancel during the wait must stop it from proceeding at all."""
        body = _function_body(frame_saver_source, "startUpload(state, file)")
        claim = body.index("state.upload = {")
        wait = body.index("await fetchServerUploadLimitBytes()")
        assert claim < wait
        assert "if (!state.upload) return" in body[wait:]

    def test_server_limit_is_read_from_features_and_never_rejects(
        self, frame_saver_source: str
    ) -> None:
        body = _function_body(frame_saver_source, "fetchServerUploadLimitBytes()")
        assert "api.fetchApi('/features')" in body
        assert "max_upload_size" in body
        assert "catch" in body and "return null" in body
        # a failed read is not cached forever
        assert "serverUploadLimitPromise = null" in body

    def test_limit_cache_is_warmed_when_the_node_attaches(self, frame_saver_source: str) -> None:
        call = frame_saver_source.index("installVideoDragAndDrop(state)\n")
        assert "fetchServerUploadLimitBytes()" in frame_saver_source[call : call + 400]

    def test_start_upload_busy_guard_mirrors_image_grid(self, frame_saver_source: str) -> None:
        body = _function_body(frame_saver_source, "startUpload(state, file)")
        assert body.strip().startswith("if (state.upload) {")
        assert "ignoring the new file" in body

    def test_uploaded_selection_persists_through_the_same_widget_write_path(
        self, frame_saver_source: str
    ) -> None:
        """FORMAT.md §7.9: an uploaded selection must survive a tab-switch/
        rebuild exactly like a browsed/pasted one -- because success routes
        through the SAME chooseVideoPath() -> writeWidgetValue() write to
        the REAL video_path widget, never a DOM-only/side-channel
        assignment."""
        upload_body = _function_body(frame_saver_source, "startUpload(state, file)")
        assert "chooseVideoPath(state, annotatedInputRef(uploaded))" in upload_body
        assert "state.path =" not in upload_body  # only chooseVideoPath -> onPathChanged does that
        write_widget = _function_body(frame_saver_source, "writeWidgetValue(widget, node, value)")
        assert "widget.value = value" in write_widget

    def test_teardown_aborts_an_in_flight_upload(self, frame_saver_source: str) -> None:
        body = _function_body(frame_saver_source, "wireNodeCleanup(state)")
        assert "state.upload.abort()" in body

    def test_attach_installs_drag_and_drop(self, frame_saver_source: str) -> None:
        body = _function_body(frame_saver_source, "attach(node)")
        assert "installVideoDragAndDrop(state)" in body

    def test_drag_drop_reuses_start_upload_not_a_second_implementation(
        self, frame_saver_source: str
    ) -> None:
        body = _function_body(frame_saver_source, "installVideoDragAndDrop(state)")
        assert "node.onDragOver = isDraggingVideoFile" in body
        assert "void startUpload(state, file)" in body
        assert "isAllowedVideoFile" in body

    def test_created_state_declares_the_new_upload_fields(self, frame_saver_source: str) -> None:
        state = _function_body(frame_saver_source, "createState(node, pathWidget, frameWidget)")
        assert "uploadBtn: null" in state
        assert "uploadInputEl: null" in state
        assert "upload: null" in state
