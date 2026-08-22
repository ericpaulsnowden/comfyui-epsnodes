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
``eps_image/nodes_image_grid.py``).
"""

from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path
from typing import Any

# Plain module name, not the old `as video` alias: run()'s new `video`
# PARAMETER (the §6.7 v0.60.0 wired input) would shadow it.
from . import frame_saver_video

logger = logging.getLogger("eps_image")

CATEGORY_NAME = "EPSNodes"

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
        "Needs a video file the ComfyUI machine can reach: this node reads "
        "it in place by path and never copies it into ComfyUI's input "
        "folder. Scrub to a single frame right on the node with the "
        "play/pause/step controls or by typing a frame number; running the "
        "node then outputs that exact frame as an image, along with its "
        "width and height. Browse works only in a browser on the ComfyUI "
        "machine -- from another computer, select the node and paste the "
        "full path (Ctrl/Cmd+V). The in-node preview is an "
        "approximation for scrubbing; the output frame is always decoded "
        "fresh from the source file, so what you get matches the file, not "
        "the preview. Or skip paths entirely: the optional video INPUT "
        "takes any VIDEO wire (Load Video, Video Slice, a generated clip) "
        "and the wire wins over the browsed path -- a wired Load Video "
        "even scrubs from another machine's browser."
    )

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
        inputs only when nothing path-like is in play."""
        if video is not None:
            return float("nan")
        path = str(video_path or "").strip()
        if not path:
            return "missing"
        try:
            stat = Path(path).stat()
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
                "EPS Frame Saver: no video chosen yet -- click Browse on the "
                "node, paste a full path onto it (Ctrl/Cmd+V) if you are "
                "working from another machine, or wire a video into the "
                "video input."
            )
        tensor, width, height = frame_saver_video.extract_frame(path, int(frame))
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
