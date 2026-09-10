"""``EPSFrameSaver`` (FORMAT.md §6.7, display: "EPS Frame Saver") — pick a
video by path, scrub to a frame in-node, output that exact frame + its size.

Owner decisions locked (FORMAT.md §6.7):

- **PATH source, never a copy.** ``video_path`` is chosen via a server-side
  Browse dialog (``web/eps_image/frame_saver.js``, reusing the pack's
  ``/lora_library/fs/list`` fs-browse standard with a video ext allowlist) —
  unlike core's own ``LoadImage``/VHS's ``VHS_LoadVideo``, the file is never
  copied into ComfyUI's ``input/`` directory.
- **Single-frame output, not a list.** ``OUTPUT_IS_LIST`` is deliberately
  ABSENT — this matches the sibling pack's ``PremiereShotFrame`` (FORMAT.md
  §6.7's own citation). Multi-frame extraction is an explicitly deferred
  future sibling node.
- **"Close-enough preview, EXACT frame on output."** The frontend player only
  ever drives an *approximate* ``<video>``-element preview off the probed
  fps/frame_count; THIS node's :meth:`EPSFrameSaver.run` always re-decodes
  the exact requested frame straight from the source file at execution
  time, completely independent of whatever the preview happened to show.

No torch/av/ComfyUI import anywhere at module scope — :meth:`run` only
reaches into :mod:`eps_image.frame_saver_video` (which itself lazily imports
``av``/``torch``, see that module's docstring), so this file stays importable
in a plain test environment with neither installed — same convention as
every other node in this pack (``eps_image/nodes_resolution.py``,
``eps_image/nodes_image_grid.py``). :mod:`eps_image.routes_frame_saver` is
ALSO safe to import at module scope for the identical reason — it only
lazily imports ComfyUI's own ``folder_paths`` inside
:func:`routes_frame_saver._resolve_input_ref` and ``server.PromptServer``
inside its own ``register()`` (never called from here) — see
:func:`_resolve_execution_path` below.

Owner report 2026-09-10 ("[EPS Frame Saver] only lets you select a frame
from a host machine, and not from a machine that is connected to it (like
one of my macs) ... this makes the component mostly unusable for me"):
``video_path`` now holds one of TWO shapes — an ABSOLUTE PATH (Browse/paste,
unchanged, byte-identical to every workflow saved before this feature
existed) or an ANNOTATED INPUT REF (``web/eps_image/frame_saver.js``'s new
"Upload…" button/drag-drop writes one, e.g. ``clip.mp4 [input]`` — the exact
form ``folder_paths.get_annotated_filepath`` resolves).
:func:`_resolve_execution_path` disambiguates the two exactly the way the
frontend's own ``isInputRefVideoPath`` does (client and server must never
disagree): an
absolute path is opened literally; anything else is resolved via
``routes_frame_saver._resolve_input_ref`` — the SAME resolver + extension
allowlist the HTTP routes' ungated ``input_ref`` preview mode already uses,
reused rather than reimplemented (FORMAT.md §6.7).
"""

from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path
from typing import Any, ClassVar

# Plain module name, not the old `as video` alias: run()'s new `video`
# PARAMETER (the §6.7 v0.60.0 wired input) would shadow it.
from . import frame_saver_video, routes_frame_saver

logger = logging.getLogger("eps_image")

CATEGORY_NAME = "EPSNodes/Images"

#: A generous static ceiling for the `frame` widget's declared INT range.
#: `INPUT_TYPES` is evaluated once at class-registration time, long before
#: any particular `video_path` is known, so it can never reflect a REAL
#: video's actual frame count -- that's the frontend's job, per-node-
#: instance, once `GET /eps_frame_saver/probe` returns one (FORMAT.md §6.7).
#: This is just wide enough to never clip a legitimate request; `extract_frame`
#: clamps a too-large index down to the video's last frame regardless (it
#: never errors purely for running past the end -- see that function's
#: docstring), so this ceiling is a UI nicety, not a correctness boundary.
MAX_FRAME_WIDGET_VALUE = 2**31 - 1


def _is_foreign_absolute(path: str) -> bool:
    """Whether *path* is absolute in the OTHER platform's syntax -- a Windows
    drive-letter or UNC path on this POSIX server, or a ``/...`` path on a
    Windows one.

    Without it, :func:`_resolve_execution_path` and
    ``web/eps_image/frame_saver.js``'s ``looksAbsolutePath`` DISAGREE. The
    JS check treats ``C:\\...``, ``C:/...`` and ``\\\\server\\...`` as
    absolute on every OS; ``Path(...).is_absolute()`` answers in the LOCAL
    platform's syntax, so on the owner's Linux box a path saved on his
    Windows PC is "not absolute" -- backslash and colon are ordinary
    filename characters there. The browser would treat it as a PATH while
    the server pushed it through input-ref resolution, failing with a
    misleading "invalid input_ref" instead of the plain missing-file error
    it really is (lead review 2026-09-10, correcting the upload round's
    claim that client and server "can never disagree").

    Reuses ``lora_library.context.is_foreign_absolute`` -- the pack's
    existing answer to exactly this (2026-08-28) -- rather than a second
    copy that could drift. Imported lazily in ``nodes_save_image.py``'s
    two-branch form (nested package under ComfyUI, flat under pytest); if
    neither import works it fails soft to "not foreign", which is precisely
    the behaviour before this helper existed.
    """
    try:
        from ..lora_library.context import is_foreign_absolute
    except ImportError:
        try:
            from lora_library.context import is_foreign_absolute
        except ImportError:
            return False
    try:
        return bool(is_foreign_absolute(path))
    except Exception:  # fail soft: a path check must never break a run
        return False


def _resolve_execution_path(path: str) -> str:
    """*path* (already stripped, non-empty) -> a real filesystem path for
    :meth:`EPSFrameSaver.run`/:meth:`EPSFrameSaver.IS_CHANGED` to open.

    Owner report 2026-09-10 (module docstring): `video_path` now holds
    either an ABSOLUTE path (Browse/paste — returned unchanged, so every
    workflow saved before the Upload button existed keeps working
    byte-identically) or an ANNOTATED INPUT REF (the Upload button/
    drag-drop writes one, e.g. `"clip.mp4 [input]"`) — resolved via
    `routes_frame_saver._resolve_input_ref`, the SAME resolver + extension
    allowlist the HTTP routes' `input_ref` preview mode already uses, so a
    file that scrubs in-node also runs (never a second, drifting resolver).

    Absolute in EITHER platform's syntax (`Path.is_absolute()` OR
    `_is_foreign_absolute()` -- see there for why both) is the disambiguation
    `web/eps_image/frame_saver.js`'s `isInputRefVideoPath` mirrors
    client-side — every `video_path` this pack has ever WRITTEN is either
    empty, or an absolute path (Browse always lists full paths; paste only
    accepts absolute-looking text), so "not absolute" is a safe, unambiguous
    signal for "this is an annotated ref."

    Raises:
        ValueError: the ref doesn't resolve (missing, wrong extension, a
            traversal attempt, or no ComfyUI `folder_paths` to resolve
            against) — always a clean message naming the problem, never a
            raw exception; :meth:`run` lets this propagate as the node's
            queue error, :meth:`IS_CHANGED` catches it and degrades to
            `"missing"` instead (this pack's fail-soft convention).
    """
    if Path(path).is_absolute() or _is_foreign_absolute(path):
        return path
    resolved, error = routes_frame_saver._resolve_input_ref(path)
    if error is not None:
        raise ValueError(f"EPS Frame Saver: {error}")
    return str(resolved)


class EPSFrameSaver:
    """Load-video-by-path frame picker (FORMAT.md §6.7).

    Re-opens and re-decodes `video_path` on every execution — there is no
    persisted state to go stale. Mirrors this pack's other file-path nodes'
    convention of re-reading the source of truth every run
    (`LoraLibraryNotebook`, `EPSImageGrid`): the FILE is the truth, the node
    (and its frontend player) are just a view onto it.
    """

    CATEGORY = CATEGORY_NAME
    RETURN_TYPES = ("IMAGE", "INT", "INT")
    RETURN_NAMES = ("image", "width", "height")
    OUTPUT_TOOLTIPS = (
        "The decoded frame, as a single image.",
        "The frame's width in pixels.",
        "The frame's height in pixels.",
    )
    FUNCTION = "run"
    DESCRIPTION = (
        "Pick a video by path (Browse, on the ComfyUI machine only, or "
        "paste a full path onto the node with Ctrl/Cmd+V) -- read in place, "
        "never copied. From a DIFFERENT computer, use Upload… (or drag a "
        "video onto the node) instead: it sends the file from THIS "
        "browser's machine into ComfyUI's own input folder, and scrubbing "
        "then works exactly like a local file. Scrub to a single frame "
        "with the play/pause/step controls or by typing a frame number; "
        "running the node outputs that exact frame as an image, along with "
        "its width and height. The in-node preview is an approximation for "
        "scrubbing; the output frame is always decoded fresh from the "
        "source file, so what you get matches the file, not the preview. "
        "Or skip files entirely: the optional video INPUT takes any VIDEO "
        "wire (Load Video, Video Slice, a generated clip) and the wire "
        "wins over the browsed/uploaded path -- a wired Load Video even "
        "scrubs from another machine's browser."
    )

    #: §6.16 state registry (v0.83.0): the widgets a Universal State
    #: Controller may capture/apply, declared next to the parser that owns
    #: their shape. Out of the universal-state scope by owner decision
    #: 2026-08-26 -- every widget here is excluded, none captured/applied.
    EPS_STATE_WIDGETS: ClassVar[dict[str, Any]] = {
        "format": 1,
        "widgets": {},
        "excluded": {
            "video_path": "out of the universal-state scope by owner decision 2026-08-26",
            "frame": "out of the universal-state scope by owner decision 2026-08-26",
        },
    }

    # `video_path` and `frame` below have no `tooltip`: both widgets are
    # hidden serialized bridges (`web/eps_image/frame_saver.js`'s "Two
    # widgets, hidden" -- the on-node scrubber/player is the entire visible
    # surface for both), so a Python tooltip on either would never be seen.
    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                # "hidden": True is the VUE-nodes ("New node design") hide
                # flag (2026-07-29): that renderer decides widget visibility
                # from the input spec's options (`options.hidden`,
                # useProcessedWidgets.ts) and IGNORES the litegraph
                # `widget.hidden` the frontend sets -- without this, this
                # internal widget leaked into Vue nodes as a raw editable
                # field. The classic canvas renderer ignores this key right
                # back, so it changes nothing there.
                "video_path": ("STRING", {"default": "", "multiline": False, "hidden": True}),
                "frame": (
                    "INT",
                    # Same Vue-nodes hide flag as `video_path` above.
                    {
                        "default": 0,
                        "min": 0,
                        "max": MAX_FRAME_WIDGET_VALUE,
                        "step": 1,
                        "hidden": True,
                    },
                ),
            },
            "optional": {
                # v0.60.0 (FORMAT.md §6.7, owner ask 2026-08-09): a video
                # already IN the workflow, frame-picked without touching
                # disk paths. Additive + §8-safe exactly like v0.46.0's vae
                # precedent (inputs resolve by NAME).
                "video": (
                    "VIDEO",
                    {
                        "tooltip": (
                            "Optional: a video from elsewhere in the "
                            "workflow (Load Video, Video Slice, ...). When "
                            "wired it takes over completely -- the browsed "
                            "path is ignored. Wire from a Load Video node "
                            "and the on-node scrubber works exactly as for "
                            "a browsed file; other video sources arrive at "
                            "run time, so pick the frame by typing its "
                            "number."
                        ),
                    },
                ),
            },
        }

    @classmethod
    def IS_CHANGED(cls, video_path: str = "", frame: int = 0, video: Any = None) -> Any:
        """Cache key (audit 2026-08-21): this node had NO IS_CHANGED, so with
        an unchanged `video_path`/`frame` core served run 1's frame forever
        even after the file on disk was re-rendered -- the class docstring's
        "re-opens and re-decodes on every execution" only held for the first
        one. Core's own LoadVideo fingerprints the file's mtime; the pack's
        Notebook uses mtime+size (`_file_token`) for the same reason. A wired
        `video` input is a tensor whose own upstream cache key already
        covers it (path mode is ignored then, mirroring run()), so NaN keeps
        the default never-cache posture core applies to unfingerprinted
        inputs only when nothing path-like is in play.

        Owner report 2026-09-10: `video_path` may now be an annotated input
        ref (the Upload button/drag-drop) rather than a literal path --
        `_resolve_execution_path` resolves it first, and the stat below runs
        against the RESOLVED file so re-uploading (a fresh name via core's
        own dedup) or editing the underlying input file still re-runs, not
        just an edit to a literal absolute path."""
        if video is not None:
            return float("nan")
        path = str(video_path or "").strip()
        if not path:
            return "missing"
        try:
            resolved_path = _resolve_execution_path(path)
        except ValueError:
            return "missing"
        try:
            stat = Path(resolved_path).stat()
        except OSError:
            return "missing"
        return f"{stat.st_mtime}:{stat.st_size}:{frame}"

    def run(self, video_path: str, frame: int = 0, video: Any = None) -> tuple[Any, int, int]:
        # WIRED WINS (FORMAT.md §6.7 v0.60.0): an explicit wire beats a
        # stale widget, unconditionally.
        if video is not None:
            return self._run_from_video_input(video, int(frame))

        path = str(video_path or "").strip()
        if not path:
            raise ValueError(
                "EPS Frame Saver: no video chosen yet -- click Browse or "
                "Upload on the node, paste a full path onto it (Ctrl/Cmd+V) "
                "if you are working from another machine, or wire a video "
                "into the video input."
            )
        # Owner report 2026-09-10: `path` may be a literal absolute path
        # (Browse/paste, unchanged) or an annotated input ref (Upload/
        # drag-drop) -- see `_resolve_execution_path`'s own docstring.
        resolved_path = _resolve_execution_path(path)
        tensor, width, height = frame_saver_video.extract_frame(resolved_path, int(frame))
        return (tensor, width, height)

    @staticmethod
    def _run_from_video_input(video_input: Any, frame: int) -> tuple[Any, int, int]:
        """Extract *frame* from a wired ``VIDEO`` object (FORMAT.md §6.7).

        Duck-typed against ``comfy_api``'s ``VideoInput`` rather than
        imported (the pack's no-ComfyUI-import-at-module-scope seam, and
        third-party packs ship their own "VIDEO" objects):

        - ``get_stream_source()`` -> a path or file-like, both exactly what
          ``av.open`` accepts -- the zero-copy fast path.
        - ``get_active_trim_window()`` (``VideoFromFile``) -> honored, so a
          ``VideoSlice`` output frames as the user sees it.
        - Neither, but ``save_to(path)`` -> encode to a temp file, extract,
          delete in ``finally`` (logged -- it is the slow path).
        - None of the above -> a ValueError naming the type.
        """
        label = f"wired video ({type(video_input).__name__})"

        trim_start, trim_duration = 0.0, 0.0
        get_trim = getattr(video_input, "get_active_trim_window", None)
        if callable(get_trim):
            try:
                window = get_trim()
                if isinstance(window, (tuple, list)) and len(window) == 2:
                    trim_start, trim_duration = float(window[0]), float(window[1])
            except Exception:  # a trim probe must never sink the extract
                logger.exception("EPSNodes: EPS Frame Saver could not read the trim window")

        get_source = getattr(video_input, "get_stream_source", None)
        if callable(get_source):
            try:
                source = get_source()
            except Exception as exc:
                raise ValueError(
                    f"EPS Frame Saver: the {label} could not provide its "
                    f"stream ({exc})"
                ) from exc
            return frame_saver_video.extract_frame(
                source,
                frame,
                trim_start=trim_start,
                trim_duration=trim_duration,
                label=label,
            )

        save_to = getattr(video_input, "save_to", None)
        if callable(save_to):
            import tempfile

            logger.info(
                "EPSNodes: EPS Frame Saver encoding a %s to a temp file "
                "(no get_stream_source on this object -- the slow path)",
                type(video_input).__name__,
            )
            fd, tmp_name = tempfile.mkstemp(suffix=".mp4", prefix="eps_frame_saver_")
            os.close(fd)
            try:
                save_to(tmp_name)
                return frame_saver_video.extract_frame(tmp_name, frame, label=label)
            finally:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_name)

        raise ValueError(
            "EPS Frame Saver: the wired video input is a "
            f"{type(video_input).__name__}, which offers neither "
            "get_stream_source() nor save_to() -- this node can only read "
            "ComfyUI VIDEO objects (Load Video, Video Slice, and "
            "compatibles)."
        )
