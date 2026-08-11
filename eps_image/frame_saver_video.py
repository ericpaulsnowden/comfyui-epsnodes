"""PyAV probe + frame-extraction helpers for ``EPSFrameSaver`` (FORMAT.md §6.7).

Mirrors two of Eric's own MIT recipes, verified against this pack's sibling
package:

- **Frame extraction** — ``comfyui-premiere-bridge/cprb/frame_extract.py``'s
  ``extract_frame``: seek the container toward the target (a container only
  ever seeks to a keyframe at/before the target, never an arbitrary frame
  exactly), then decode FORWARD from there, keeping the LAST frame decoded
  until one's ``pts`` reaches the target — closest-available, never an error
  purely because the target ran past the end of the stream. Ported here
  almost verbatim; the only real change is the caller's unit: cprb's
  ``extract_frame`` takes ``in_seconds`` directly (a Premiere sequence-time
  value), this node's is a ``frame_index`` (FORMAT.md §6.7: "frame_index/fps
  -> target_seconds" is the one conversion layered on top).
- **Frame-count cascade** — ComfyUI core's ``VideoFromFile.get_frame_count``
  (``comfy_api/latest/_input_impl/video_types.py``): prefer ``stream.frames``
  when it's a positive, directly-reported count; else estimate
  ``round(duration_seconds * fps)`` from whatever duration metadata the
  stream/container exposes; else decode every frame and count them as a last
  resort. :func:`probe`'s :func:`_frame_count_cascade` is the same 3-tier
  order, trimmed to this node's simpler "probe the whole file" case (no
  ``start_time``/``duration`` trim window — that's core's own trimmed-video
  feature, not part of this node).

Both ``av`` and ``torch`` are HARD ComfyUI dependencies (installed as part of
ComfyUI itself, same as every other node in this pack that touches either),
but every import of either is LAZY, inside the function that actually needs
it, never at module scope — this file, and therefore ``nodes_frame_saver.py``
(which imports it at ITS module scope), stays importable under a bare
``pytest`` run on a machine with neither installed. Same convention as
``eps_image/nodes_resolution.py`` and ``comfyui-premiere-bridge/cprb/
probe.py``/``cprb/frame_extract.py``.

Every raised error is a plain :class:`ValueError` naming the offending
*path* — never a raw PyAV/ffmpeg traceback (FORMAT.md §6.7) — so the HTTP
routes (``routes_frame_saver.py``) can turn it straight into a clean 400,
and the node (``nodes_frame_saver.py``) can let it surface as ComfyUI's
normal node-error UI.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import torch

#: Prefix every error message with the node's display name (FORMAT.md §6.7),
#: so a failure surfaces unambiguously in ComfyUI's node-error UI even when
#: several nodes could plausibly touch the same file.
_ERROR_PREFIX = "EPS Frame Saver"


def probe(path: str) -> dict[str, Any]:
    """Probe *path* for the §6.7 frontend counter + output-bound math.

    Returns:
        A plain, JSON-ready dict: ``{"fps": float, "frame_count": int,
        "width": int, "height": int, "duration": float}``. ``duration`` is
        always exactly ``frame_count / fps`` (computed AFTER ``frame_count``
        is resolved by the cascade below) — never an independently-probed
        duration, so it stays internally consistent with the other two
        fields (mirrors ``cprb/probe.py``'s ``MediaInfo.duration_seconds``
        invariant, same rationale).

    Raises:
        ValueError: *path* can't be opened at all (missing file, unreadable,
            unrecognized container); it has no video stream; its frame rate
            is unavailable (``stream.average_rate`` falsy — never papered
            over with a guessed rate); its frame count resolves to zero or
            less; or its dimensions are unusable. Every message names
            *path*.
    """
    import av  # lazy: see module docstring.

    try:
        container = av.open(path)
    except Exception as exc:  # any av/ffmpeg open failure becomes a ValueError.
        raise ValueError(f"{_ERROR_PREFIX}: could not open video file: {path} ({exc})") from exc

    try:
        video_streams = container.streams.video
        if not video_streams:
            raise ValueError(f"{_ERROR_PREFIX}: no video stream found in: {path}")
        stream = video_streams[0]

        # VFR-ish sources still get ONE representative fps from PyAV itself
        # (`average_rate`) -- a missing rate is a hard error, never silently
        # guessed at (mirrors cprb/probe.py's identical stance).
        if not stream.average_rate:
            raise ValueError(f"{_ERROR_PREFIX}: video has no usable frame rate: {path}")
        fps = float(stream.average_rate)

        frame_count = _frame_count_cascade(container, stream, fps)
        if frame_count <= 0:
            raise ValueError(f"{_ERROR_PREFIX}: video has zero effective frames: {path}")

        width, height = stream.width, stream.height
        if not width or not height:
            raise ValueError(f"{_ERROR_PREFIX}: video has no usable dimensions: {path}")

        return {
            "fps": fps,
            "frame_count": frame_count,
            "width": int(width),
            "height": int(height),
            "duration": frame_count / fps,
        }
    except ValueError:
        raise
    except Exception as exc:  # any other av/ffmpeg probing failure becomes a ValueError.
        raise ValueError(f"{_ERROR_PREFIX}: could not probe video file: {path} ({exc})") from exc
    finally:
        container.close()


def _frame_count_cascade(container: Any, stream: Any, fps: float) -> int:
    """ComfyUI core's ``VideoFromFile.get_frame_count`` 3-tier cascade, ported.

    Kept as its own function (rather than inlined in :func:`probe`) so the
    fallback tiers are independently readable/testable, mirroring how
    ``cprb/probe.py`` splits its own duration fallback into
    ``_duration_seconds_from_container``.
    """
    if stream.frames and stream.frames > 0:
        return int(stream.frames)

    duration_seconds = _duration_seconds(container, stream)
    if duration_seconds is not None:
        estimated = round(duration_seconds * fps)
        if estimated > 0:
            return estimated

    # Last resort: decode every frame and count (core's own "last resort" --
    # slow, but the only way some containers -- fragmented/streamed mp4,
    # mpegts -- ever expose a count at all).
    container.seek(0)
    count = 0
    for _frame in container.decode(stream):
        count += 1
    return count


def _duration_seconds(container: Any, stream: Any) -> float | None:
    """Best-effort duration in seconds for the cascade's tier 2 (never raises).

    Prefers the STREAM's own duration (its ``time_base``-scaled value, since
    that's specific to the video stream being probed); falls back to the
    CONTAINER's duration (already in seconds via ``av.time_base``). Mirrors
    ``cprb/probe.py``'s ``_duration_seconds_from_container`` fallback order
    exactly. Returns ``None`` (never raises) when neither is available.
    """
    import av  # lazy: see module docstring.

    if stream.duration is not None and stream.time_base is not None:
        return float(stream.duration * stream.time_base)
    if container.duration is not None:
        return float(container.duration / av.time_base)
    return None


def extract_frame(
    path: Any,
    frame_index: int,
    *,
    trim_start: float = 0.0,
    trim_duration: float = 0.0,
    label: str | None = None,
) -> tuple[torch.Tensor, int, int]:
    """Decode the frame at/after *frame_index* in *path*, as a ComfyUI IMAGE tensor.

    v0.60.0 (FORMAT.md §6.7's optional ``video`` input): *path* may now be a
    FILE-LIKE object as well as a path string — both are exactly what
    ``av.open`` accepts, so a wired ``VideoInput.get_stream_source()`` feeds
    straight in (a ``VideoFromFile`` costs nothing extra; an in-memory video
    decodes from its buffer). *trim_start*/*trim_duration* mirror
    ``VideoInput.get_active_trim_window()``: frame N of a trimmed video
    counts from the trim start (``target = trim_start + N/fps``), and a
    positive *trim_duration* clamps the target inside the window, so a
    ``VideoSlice`` output frames as the user sees it. *label* replaces the
    raw *path* in error messages when the source isn't a string (a BytesIO
    repr helps nobody).

    Converts FORMAT.md §6.7's ``frame_index/fps -> target_seconds``, then
    runs ``cprb/frame_extract.py``'s exact seek+decode-forward recipe:
    seeks the container toward the target (landing on the nearest keyframe
    at/before it — containers don't seek to arbitrary frames), then decodes
    FORWARD, keeping the LAST frame seen until one's ``pts`` reaches the
    target or the stream runs out. A *frame_index* past the last real frame
    therefore never errors — decoding simply reaches the end of the stream
    and the last frame decoded (the closest available) is returned; this is
    the "clamps out-of-range" behavior on the high side. A negative
    *frame_index* clamps to ``0`` on the low side (there is no "before the
    first frame").

    Args:
        path: Absolute path to the video file. Never copied anywhere
            (FORMAT.md §6.7's locked PATH-source decision) — opened in place.
        frame_index: The 0-based frame to extract.

    Returns:
        ``(tensor, width, height)``. ``tensor`` is ``torch.float32``, shape
        ``[1, H, W, 3]`` (batch-of-one, HWC, RGB, values scaled to
        ``[0, 1]``) — the standard ComfyUI IMAGE shape/dtype.  ``width``/
        ``height`` come from the video STREAM's own ``width``/``height``
        (FORMAT.md §6.7), falling back to the decoded frame's own dimensions
        only if the stream doesn't expose them.

    Raises:
        ValueError: *path* can't be opened, has no video stream, has no
            usable frame rate, or no frame could be decoded from it at all.
            Every message names *path*, never a raw PyAV/ffmpeg traceback.
    """
    import av
    import torch

    describe = label if label is not None else str(path)
    try:
        container = av.open(path)
    except Exception as exc:  # any av/ffmpeg open failure becomes a ValueError.
        raise ValueError(f"{_ERROR_PREFIX}: could not open video file: {describe} ({exc})") from exc

    try:
        video_streams = container.streams.video
        if not video_streams:
            raise ValueError(f"{_ERROR_PREFIX}: no video stream found in: {describe}")
        stream = video_streams[0]

        if not stream.average_rate:
            raise ValueError(f"{_ERROR_PREFIX}: video has no usable frame rate: {describe}")
        fps = float(stream.average_rate)
        target_seconds = max(float(trim_start), 0.0) + max(int(frame_index), 0) / fps
        if trim_duration and trim_duration > 0:
            # Clamp INSIDE the trim window: the last representable frame of
            # a trimmed video starts one frame-duration before its end.
            window_end = max(float(trim_start), 0.0) + float(trim_duration)
            target_seconds = min(target_seconds, max(window_end - 1.0 / fps, 0.0))

        target_ts = target_seconds / stream.time_base
        with contextlib.suppress(Exception):
            container.seek(round(target_ts), stream=stream)  # else: decode from the top instead.

        frame = None
        for candidate in container.decode(stream):
            frame = candidate
            if candidate.pts is not None and candidate.pts >= target_ts:
                break
        if frame is None:
            raise ValueError(f"{_ERROR_PREFIX}: could not decode any frame from: {describe}")

        array = frame.to_ndarray(format="rgb24")  # HWC, uint8, RGB.
        width = stream.width or frame.width
        height = stream.height or frame.height
    except ValueError:
        raise
    except Exception as exc:  # any other av/ffmpeg decode failure becomes a ValueError.
        raise ValueError(
            f"{_ERROR_PREFIX}: could not decode video file: {describe} ({exc})"
        ) from exc
    finally:
        container.close()

    tensor = torch.from_numpy(array).float() / 255.0
    return tensor.unsqueeze(0), int(width), int(height)
