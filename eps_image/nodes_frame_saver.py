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

from typing import Any

from . import frame_saver_video as video

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
        "Picks a video file by path and scrubs to a single frame, right on "
        "the node, using play/pause/step controls or by typing a frame "
        "number. Running the node outputs that exact frame as an image, "
        "along with its width and height. The video file itself is never "
        "copied into ComfyUI's input folder. The in-node preview is an "
        "approximation for scrubbing; the output frame is always decoded "
        "fresh from the source file, so what you get matches the file, not "
        "the preview."
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
        }

    def run(self, video_path: str, frame: int = 0) -> tuple[Any, int, int]:
        path = str(video_path or "").strip()
        if not path:
            raise ValueError(
                "EPS Frame Saver: no video_path set -- Browse for a video file first."
            )
        tensor, width, height = video.extract_frame(path, int(frame))
        return (tensor, width, height)
