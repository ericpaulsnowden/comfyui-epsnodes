"""On-disk buffer for ``EPSImageGrid`` (FORMAT.md §6.6, roadmap-eps-image-
grid.md "Design guardrails").

A per-node, ever-growing sequence of PNG frames under
``<comfy output dir>/eps_image_grid/<grid_uuid>/`` — one ``NNNN.png`` file
per frame plus an ordered ``manifest.json`` sibling, written atomically
(same-directory temp file + ``os.replace``, the exact pattern
``lora_library/context.py``'s ``_atomic_write_text`` already uses in this
pack; duplicated rather than imported because ``eps_image`` is a sibling
FEATURE FAMILY that deliberately doesn't reach into ``lora_library``, see
FORMAT.md's naming note). The cross-run-durable-accumulation *idea* is the
same one already proven in comfyui-photoshop-bridge's ``cpsb/
compose_psd.py``/``cpsb/handoff.py`` (atomic writes, per-node-id state
surviving restarts) — applied here in the opposite direction: accumulating
INPUTS across separate Runs, instead of composing outputs.

**Why the output directory, not a pack-owned dir**: core's own ``/view``
route resolves ``type: "output"`` against
``folder_paths.get_output_directory()`` and happily serves a *nested*
subfolder under it (verified directly against this repo's ComfyUI checkout,
``server.py``'s ``/view`` handler: it joins ``subfolder`` onto the type's
base dir with a ``commonpath`` containment check, then joins the bare
``os.path.basename(filename)`` — a ``eps_image_grid/<uuid>`` subfolder is
always inside that base dir by construction). That's what makes the
thumbnail grid free (``nodes_image_grid.py`` returns exactly these refs as
``ui.images``). Unlike ComfyUI's *temp* dir (what ``PreviewImage`` uses),
``output/`` is never wiped by ``cleanup_temp()`` on start/stop, so the
buffer survives a restart (roadmap-eps-image-grid.md "Persistence: survive
restart, NO cap").

``folder_paths`` (ComfyUI's own module) is imported lazily, only inside
``_base_dir()``/``_resolve_uploaded_path()``, so this module — and its
manifest/path-validation/clear logic — stays importable and unit-testable
without a real ComfyUI install (see ``tests/test_image_grid_store.py``'s
``fake_folder_paths`` fixture, mirroring this pack's established
``sys.modules`` faking convention). ``torch``/``numpy``/``PIL`` are
imported lazily too, only inside the functions that actually touch
tensors/images (``append_batch``, ``read_all_as_tensors``,
``append_uploaded_image``) — for the same reason; every other function
here never needs them at all.

**M2 addition (FORMAT.md §6.6 "Copy/paste (M2)"):** :func:`append_uploaded_image`
is the Ctrl+V/paste-to-add path's backend half — the frontend uploads a
pasted file through core's own ``POST /upload/image`` first (landing under
``folder_paths``'s ``input``/``output``/``temp`` dirs, NOT our buffer),
then calls this to copy that ONE file into the buffer as the next frame.
Copy/paste OUT (OS clipboard + ComfyUI clipspace) needed no backend
change at all — see ``web/eps_image/image_grid.js``'s module docstring
for why those are already free from ComfyUI core.

**2026-07-20 bug-fix addition (two owner reports):** :func:`clone_buffer`
backs the "copy carries the images, independently" fix — the frontend's
dedup calls it right after minting a fresh uuid for an in-graph duplicate,
so the copy starts with the original's images instead of an empty buffer.
:func:`list_refs` (already existed for M1's own free-grid `ui.images`) is
now ALSO the backend half of the "display reflects the buffer on load" fix,
served fresh over the new ``GET /eps_image_grid/list`` route
(``routes_image_grid.py``) so the frontend can populate `node.imgs` on
attach/reload/undo without waiting for a Run.

**2026-08-21 perf round (owner: "big performance issues" on large
buffers):** two additions, both display-only -- the buffer format, every
existing function's contract and the node's ``ui.images`` shape are
untouched. :func:`with_frame_mtimes` decorates refs with each frame
FILE's own mtime so the frontend can key thumbnail URLs per frame instead
of on :func:`buffer_generation` (the MANIFEST mtime, which moved on every
append and so re-downloaded + re-encoded every UNCHANGED frame after
every Collect run). :func:`thumbnail_path` builds and caches a genuinely
DOWNSCALED ``.webp`` per frame under ``<buffer>/.thumbs/`` for the new
``GET /eps_image_grid/frame?preview=`` route -- core's ``/view?preview=``
re-encodes the FULL-resolution frame (``server.py``: ``Image.open`` +
``img.save``, no resize), so a 100-frame grid was 100 full-res images
drawn per repaint. :func:`frame_path` is the shared manifest-listed-only
gate in front of both.

**2026-08-26 while-running round (mid-run responsiveness audit):**
:func:`with_frame_mtimes` itself was the next O(N) cost -- it stat'd EVERY
frame file on EVERY call, and every ref-returning route calls it, which the
panel hits once per FINISHED RUN during a sweep. It now caches its
decorated output per ``grid_uuid``, keyed on :func:`buffer_generation` (the
same manifest-mtime token every other cache-freshness check here already
uses): while the generation is unchanged the stat loop is skipped
entirely, and every write op (:func:`append_batch`,
:func:`append_uploaded_image`, :func:`remove_frame`, :func:`clear`,
:func:`clone_buffer`) drops that uuid's cache entry so a stale decorated
list can never be served. The cache itself stays small on its own -- see
:data:`_MTIME_CACHE_MAX_UUIDS`. Every route that calls it now does so
through ``asyncio.to_thread`` (``routes_image_grid.py``), matching this
round's other filesystem-touching handlers -- see that module's own dated
docstring section for the HTTP-side half of this round, including
``GET /eps_image_grid/frame``'s non-preview branch, which previously ran
:func:`frame_path` synchronously on the loop even though its ``preview``
sibling was already offloaded.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import tempfile
from collections import OrderedDict
from pathlib import Path
from typing import Any

logger = logging.getLogger("eps_image")

#: Subfolder name under the output dir (also the `ui.images` `subfolder`
#: prefix) and the `type` every ref uses — matches core `SaveImage`'s own
#: ui.images shape (`nodes.py`: `self.type = "output"`), which is what lets
#: core's `/view` route (and thus the free thumbnail grid) serve these.
DIRNAME = "eps_image_grid"
OUTPUT_TYPE = "output"
MANIFEST_FILENAME = "manifest.json"
CURRENT_FORMAT = 1

#: `crypto.randomUUID()` (the frontend's generator, `image_grid.js`) always
#: produces a canonical 36-char UUID4, but this is deliberately a little
#: looser — hex digits and hyphens only, 8..64 long — so a hand-edited
#: workflow's near-miss value isn't needlessly rejected, while anything
#: that could reach outside the buffer root (`..`, `/`, `\`, empty, or
#: anything else not in this charset) is refused before ANY filesystem path
#: is built from it (FORMAT.md §6.6 "Regex-validate the uuid before any fs
#: path use").
_GRID_UUID_RE = re.compile(r"^[0-9a-fA-F-]{8,64}$")


def is_valid_grid_uuid(value: Any) -> bool:
    """Whether *value* is safe to use as a single path segment."""
    return isinstance(value, str) and bool(_GRID_UUID_RE.match(value))


# ------------------------------------------------------------- atomic writes


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """*data* to *path* via a same-directory temp file + ``os.replace``.

    The binary twin of ``lora_library/context.py``'s ``_atomic_write_text``
    (same-directory matters: ``os.replace`` is only atomic within one
    filesystem). Duplicated here rather than imported — see module
    docstring's family-separation note.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        try:
            os.replace(tmp_name, path)
        except FileExistsError:
        # gvfs/FUSE network mounts (owner's Linux box, 2026-08-25:
        # /run/user/1000/gvfs/smb-share:...) reject rename-over-existing
        # with EEXIST even though POSIX rename replaces -- so EVERY save to
        # an already-existing file on such a mount failed. Fall back to
        # unlink + replace: a tiny non-atomic window in which the target is
        # briefly missing, strictly better than the save always failing.
        # No data is at risk in the window -- the temp file next to the
        # target already holds the complete new content, and a crash inside
        # the window leaves that .tmp recoverable in the same directory.
            with contextlib.suppress(FileNotFoundError):
                os.unlink(path)
            try:
                os.replace(tmp_name, path)
            except OSError:
                # The target is unlinked at this point -- if the rename
                # STILL fails, a direct write is the last resort that
                # leaves the target present with the full new content
                # (the outer cleanup would otherwise delete the temp too,
                # losing both versions).
                Path(path).write_bytes(data)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _atomic_write_text(path: Path, text: str) -> None:
    _atomic_write_bytes(path, text.encode("utf-8"))


# ------------------------------------------------------------------- paths


def _base_dir() -> Path:
    """``<comfy output dir>/eps_image_grid/``, created."""
    import folder_paths  # ComfyUI's own module; only importable inside ComfyUI

    base = Path(folder_paths.get_output_directory()) / DIRNAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def buffer_dir(grid_uuid: str) -> Path | None:
    """``<output>/eps_image_grid/<grid_uuid>/``, or ``None`` for an invalid uuid.

    Every function below funnels through this, so a malformed/empty/hostile
    ``grid_uuid`` (a hand-edited workflow, a stale or not-yet-generated
    widget value) can never reach the filesystem at all — let alone escape
    the buffer root.
    """
    if not is_valid_grid_uuid(grid_uuid):
        return None
    return _base_dir() / grid_uuid


def _manifest_path(directory: Path) -> Path:
    return directory / MANIFEST_FILENAME


# ---------------------------------------------------------------- manifest


def _empty_manifest() -> dict:
    return {"format": CURRENT_FORMAT, "frames": []}


def _load_manifest(directory: Path) -> dict:
    """The ordered ``{"format", "frames": [filenames, ...]}`` at *directory*.

    Missing, unreadable, or malformed → an empty manifest rather than an
    exception (FORMAT.md §6.6 "Never crash on a missing/malformed dir") —
    logged only when it's genuinely unexpected (a corrupt/foreign file);
    silent when the buffer simply hasn't been written to yet.
    """
    path = _manifest_path(directory)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return _empty_manifest()
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "eps_image_grid: unreadable manifest %s (%s); treating as empty", path, exc
        )
        return _empty_manifest()
    if not isinstance(data, dict) or not isinstance(data.get("frames"), list):
        logger.warning("eps_image_grid: malformed manifest %s; treating as empty", path)
        return _empty_manifest()
    frames = [name for name in data["frames"] if isinstance(name, str)]
    return {"format": CURRENT_FORMAT, "frames": frames}


def _save_manifest(directory: Path, manifest: dict) -> None:
    _atomic_write_text(_manifest_path(directory), json.dumps(manifest, indent=2) + "\n")


def _next_frame_filename(frames: list[str]) -> str:
    """``NNNN.png``, one past the highest existing index.

    Never reuses a name, even across a future per-image delete (M3) — a
    stale on-disk file from an earlier state of the buffer can then never
    collide with a freshly appended one.
    """
    highest = 0
    for name in frames:
        stem = Path(name).stem
        if stem.isdigit():
            highest = max(highest, int(stem))
    return f"{highest + 1:04d}.png"


def _refs_for(grid_uuid: str, frames: list[str]) -> list[dict]:
    """The core ``SaveImage``/``PreviewImage`` ``ui.images`` ref shape
    (roadmap-eps-image-grid.md "Grid display is FREE") — ``subfolder`` is
    relative to the output dir, exactly what core's ``/view`` route expects.
    """
    subfolder = f"{DIRNAME}/{grid_uuid}"
    return [{"filename": name, "subfolder": subfolder, "type": OUTPUT_TYPE} for name in frames]


# ------------------------------------------------------------------ append


def append_batch(grid_uuid: str, image_batch: Any) -> list[dict]:
    """Append every frame of a ``[B,H,W,C]`` tensor batch as its own PNG.

    Returns the refs for the WHOLE buffer after appending, in append order
    (FORMAT.md §6.6 "ALWAYS read the whole buffer" — callers never need a
    separate :func:`list_refs` call right after this one). A no-op that
    returns ``[]`` for an invalid *grid_uuid* — never raises: a malformed or
    not-yet-generated uuid must not crash a Run just because Collect mode
    happened to have an image wired.
    """
    directory = buffer_dir(grid_uuid)
    if directory is None:
        logger.warning("eps_image_grid: refusing to append -- invalid grid_uuid %r", grid_uuid)
        return []

    import numpy as np

    manifest = _load_manifest(directory)
    frames = manifest["frames"]

    batch_len = int(image_batch.shape[0])
    for i in range(batch_len):
        # Mirrors core `SaveImage.save_images`'s own tensor->PNG conversion
        # exactly (ComfyUI `nodes.py`): scale 0..1 floats to 0..255 bytes,
        # clip defensively against a slightly-out-of-range upstream tensor.
        frame = image_batch[i]
        array = (255.0 * frame.detach().cpu().numpy()).clip(0, 255).astype(np.uint8)
        name = _next_frame_filename(frames)
        _atomic_write_bytes(directory / name, _encode_png(array))
        frames.append(name)

    _save_manifest(directory, manifest)
    _mtime_cache_invalidate(grid_uuid)  # 2026-08-26: a write must drop any cached decoration
    return _refs_for(grid_uuid, frames)


def _encode_png(array: Any) -> bytes:
    """A ``[H,W,C]`` ``uint8`` numpy array -> PNG bytes."""
    from PIL import Image

    return _encode_png_image(Image.fromarray(array))


def _encode_png_image(pil_image: Any) -> bytes:
    """A PIL ``Image`` -> PNG bytes. Shared tail of :func:`_encode_png`
    (M1, tensor batches) and :func:`append_uploaded_image` (M2, an
    already-on-disk file opened directly) — both funnel through this so
    the buffer's on-disk shape is identical either way."""
    import io

    buffer = io.BytesIO()
    pil_image.save(buffer, format="PNG")
    return buffer.getvalue()


# --------------------------------------------------------- append (M2: add)


def _resolve_uploaded_path(filename: str, subfolder: str, source_type: str) -> Path | None:
    """``<folder_paths dir for source_type>/<subfolder>/<filename>``,
    contained -- mirrors core's own ``/upload/image``+``/view`` resolution
    (``server.py``) exactly, INCLUDING its containment check, so a hostile
    filename/subfolder can never escape *source_type*'s base directory.
    ``None`` for an unresolvable type or a path that would resolve outside
    it -- callers treat that exactly like "file not found".
    """
    if not isinstance(filename, str) or not filename:
        return None
    if filename[0] in ("/", "\\") or ".." in filename:
        return None
    if not isinstance(subfolder, str) or ".." in subfolder:
        return None

    import folder_paths  # ComfyUI's own module; only importable inside ComfyUI

    base = folder_paths.get_directory_by_type(source_type)
    if base is None:
        return None

    full_dir = os.path.join(base, subfolder) if subfolder else base
    full_dir_abs = os.path.abspath(full_dir)
    if os.path.commonpath((full_dir_abs, base)) != base:
        return None
    return Path(full_dir_abs) / filename


def append_uploaded_image(
    grid_uuid: str, filename: str, subfolder: str = "", source_type: str = "input"
) -> list[dict]:
    """Append ONE already-uploaded image to the buffer as the next frame --
    the M2 Ctrl+V/paste-to-add path (FORMAT.md §6.6 "Copy/paste (M2)"): the
    frontend POSTs ``/upload/image`` first (core's own route), then this
    with that response's ``{name, subfolder, type}`` (as ``filename``,
    ``subfolder``, ``source_type``) plus our own *grid_uuid*.

    Re-encodes through PIL (open -> convert RGB -> PNG via
    :func:`_encode_png_image`, the exact tail :func:`append_batch` (M1)
    also uses) so whatever format was actually uploaded (PNG/JPEG/WEBP --
    browsers vary on what a copied image becomes) lands in the buffer as
    the same canonical PNG-on-disk shape, and reuses the exact same
    manifest/atomic-write machinery. Returns the whole buffer's refs, same
    contract as :func:`append_batch`.

    Never raises: an invalid *grid_uuid*, an unresolvable source (bad
    type/traversal attempt), or an unreadable/missing source file are all
    a no-op that returns the CURRENT buffer unchanged (or ``[]`` for an
    invalid uuid) -- a bad paste must not crash the node's next Run.
    """
    directory = buffer_dir(grid_uuid)
    if directory is None:
        logger.warning("eps_image_grid: refusing to add -- invalid grid_uuid %r", grid_uuid)
        return []

    source_path = _resolve_uploaded_path(filename, subfolder, source_type)
    if source_path is None:
        logger.warning(
            "eps_image_grid: refusing to add -- unresolvable source "
            "(filename=%r subfolder=%r type=%r)",
            filename,
            subfolder,
            source_type,
        )
        return list_refs(grid_uuid)

    from PIL import Image

    try:
        with Image.open(source_path) as raw:
            png_bytes = _encode_png_image(raw.convert("RGB"))
    except (OSError, ValueError, SyntaxError) as exc:
        # SyntaxError included deliberately (2026-07-29, found live during
        # the per-tile-delete round): PIL's PngImagePlugin raises a PLAIN
        # SyntaxError -- not an OSError/ValueError subclass -- on a
        # truncated/corrupt PNG, which made a corrupt upload 500 instead of
        # honoring this function's own "never raises" contract.
        logger.warning("eps_image_grid: could not read %s to add (%s)", source_path, exc)
        return list_refs(grid_uuid)

    manifest = _load_manifest(directory)
    frames = manifest["frames"]
    name = _next_frame_filename(frames)
    _atomic_write_bytes(directory / name, png_bytes)
    frames.append(name)
    _save_manifest(directory, manifest)
    _mtime_cache_invalidate(grid_uuid)  # 2026-08-26: a write must drop any cached decoration
    return _refs_for(grid_uuid, frames)


# -------------------------------------------------------------- list / read


def list_refs(grid_uuid: str) -> list[dict]:
    """The whole buffer's ``ui.images`` refs, in append order.

    ``[]`` for an invalid uuid or an empty/nonexistent buffer — never
    raises.
    """
    directory = buffer_dir(grid_uuid)
    if directory is None:
        return []
    manifest = _load_manifest(directory)
    return _refs_for(grid_uuid, manifest["frames"])


def remove_frame(grid_uuid: str, filename: str) -> list[dict]:
    """Remove ONE frame from the buffer by its own frame filename
    (``NNNN.png``) -- the owner's un-brick-my-grid ask (2026-07-29: "you
    get a duplicate image and then the grid is useless and you have to
    start fresh"). Returns the whole remaining buffer's refs, same
    contract as :func:`append_uploaded_image`.

    Soft-fail like every sibling: invalid uuid -> ``[]``; a filename not in
    the manifest -> the current buffer unchanged. The frame FILE is deleted
    best-effort (a locked/vanished file still gets its manifest entry
    removed -- the manifest is the truth the node reads; an orphaned file
    on disk is harmless and unreachable). Deleting a frame never touches
    the user's original upload: frames are this store's OWN re-encoded
    PNGs under the buffer dir (:func:`_encode_png_image` writes them), not
    references to ``input/``.

    Frame numbering stays collision-safe afterwards:
    :func:`_next_frame_filename` is one-past-the-highest-EXISTING frame,
    written for exactly this future. The manifest rewrite also advances
    :func:`buffer_generation`, so frontend display URLs refresh on their
    own.
    """
    directory = buffer_dir(grid_uuid)
    if directory is None:
        return []
    manifest = _load_manifest(directory)
    frames = manifest.get("frames", [])
    if filename not in frames:
        return _refs_for(grid_uuid, frames)
    manifest["frames"] = [name for name in frames if name != filename]
    _save_manifest(directory, manifest)
    _mtime_cache_invalidate(grid_uuid)  # 2026-08-26: a write must drop any cached decoration
    try:
        (directory / filename).unlink()
    except OSError:
        logger.warning(
            "eps_image_grid: frame file %s could not be deleted (manifest entry removed)",
            filename,
        )
    # Its cached thumbnail goes too (2026-08-21) -- best-effort, same as the
    # frame: a stale thumb is unreachable anyway (`thumbnail_path` refuses
    # anything the manifest no longer lists) and `clear` rmtrees the lot.
    with contextlib.suppress(OSError):
        _thumb_path_for(directory / filename).unlink()
    return _refs_for(grid_uuid, manifest["frames"])


def buffer_generation(grid_uuid: str) -> int:
    """A monotonic-enough cache token for the buffer's CONTENTS: the
    manifest's mtime in integer milliseconds, ``0`` for an invalid uuid or
    a buffer with no manifest yet. Never raises.

    Why this exists (2026-07-29, the bulk-add work): frame files are
    append-only while a buffer lives, but :func:`clear` is an ``rmtree``
    and :func:`_next_frame_filename` restarts from the highest EXISTING
    frame — so after Clear, ``0001.png`` is reused with different pixels at
    the same path. Frontend thumbnails therefore can't use fully stable
    URLs (a cached stale frame would show) nor per-render ``rand=``
    cache-busting (100 buffered images become 100 uncached full fetches per
    refresh). This value changes exactly when the buffer's contents change
    — every append and every clone rewrite the manifest, and after a Clear
    the next append creates a fresh one — so the frontend appends it as a
    ``v=`` param: stable within a generation (cacheable), new after
    anything that could have reused a name.

    mtime, not a stored counter, deliberately: it needs no manifest schema
    change, and a Clear (which deletes the counter's would-be home) can't
    reset it backwards in any way that matters — the next manifest's mtime
    is later than the old one's by wall clock. (A clock jumping backwards
    across a Clear+re-add could in principle repeat a value; accepting that
    beats a schema migration for what is only a cache token.)
    """
    directory = buffer_dir(grid_uuid)
    if directory is None:
        return 0
    try:
        return int(_manifest_path(directory).stat().st_mtime * 1000)
    except OSError:
        return 0


def buffer_token(grid_uuid: str) -> str:
    """The buffer's cache-identity for ``EPSImageGrid.IS_CHANGED``'s
    Emit-mode branch (v0.80.0 sweep-performance round): the manifest's
    ``(mtime_ns, size)``. Every append/clone/delete rewrites the manifest
    and a Clear removes it, so this changes exactly when a re-Emit would
    produce different frames -- and an UNCHANGED buffer lets core's
    prompt-to-prompt cache skip the whole decode (previously ~16 ms/frame,
    every queue, forever). ns resolution rather than
    :func:`buffer_generation`'s ms: this one gates EXECUTION, not just a
    thumbnail URL. Invalid uuid / missing manifest return coarse tokens
    that still change when that situation changes."""
    directory = buffer_dir(grid_uuid)
    if directory is None:
        return f"no-buffer:{grid_uuid!r}"
    try:
        stat = _manifest_path(directory).stat()
    except OSError:
        return f"no-manifest:{directory}"
    return f"{stat.st_mtime_ns}:{stat.st_size}"


# ------------------------------------------- frame files + thumbnails
# (2026-08-21 perf round -- see the module docstring's dated section and
# ``routes_image_grid.py``'s ``GET /eps_image_grid/frame``.)

#: Where a buffer keeps its downscaled display copies: ``<buffer>/.thumbs/
#: <frame filename>.webp``. Dot-prefixed so it can never collide with a
#: frame name (:func:`_next_frame_filename` only ever mints ``NNNN.png``),
#: and invisible to everything else here by construction -- ``list_refs``,
#: ``clone_buffer``, ``read_all_as_tensors`` and ``_next_frame_filename``
#: all walk the MANIFEST, never the directory; ``clear`` is an ``rmtree``
#: (thumbs go with the frames); ``clone_buffer`` copies manifest frames
#: only (a clone regenerates its own thumbs lazily, on first view).
THUMBS_DIRNAME = ".thumbs"
#: Long-edge bound of a thumbnail, in pixels. The on-node grid draws every
#: buffered frame into a cell a few dozen to a couple of hundred px wide
#: (core's ``renderPreview``, ``useImagePreviewWidget.ts``), so 256 keeps a
#: 100-frame grid at ~100 x 65k px of draw work per repaint instead of
#: ~100 x 1-4M. Core's SINGLE-image view never scales UP (``Math.min(scaleX,
#: scaleY, 1)`` there), which is why the frontend swaps the full frame back
#: in for a focused tile rather than this being larger.
THUMB_MAX_EDGE = 256
THUMB_QUALITY = 80


def _is_bare_frame_name(filename: Any) -> bool:
    """Whether *filename* is a plain single path segment -- the only shape a
    manifest frame name ever has (``NNNN.png``). Anything with a separator,
    a ``.``/``..`` segment, a NUL, or a leading dot (``.thumbs`` itself, a
    temp file) is refused before any path is built from it."""
    if not isinstance(filename, str) or not filename:
        return False
    if "/" in filename or "\\" in filename or "\x00" in filename:
        return False
    return filename not in (".", "..") and not filename.startswith(".")


def frame_path(grid_uuid: str, filename: str) -> Path | None:
    """The on-disk path of ONE manifest-listed frame, or ``None``.

    The single gate in front of every route that serves a frame's bytes
    (``GET /eps_image_grid/frame``, ``routes_image_grid.py``): the uuid
    must pass :func:`is_valid_grid_uuid` (via :func:`buffer_dir`), the name
    must be a bare single segment (:func:`_is_bare_frame_name`) AND be
    listed in the manifest -- the same "manifest-listed names only" rule
    :func:`remove_frame` applies -- and the file must exist. So a hostile or
    merely stale ``filename`` can never name anything outside the buffer
    dir, nor anything inside it the manifest doesn't own. Never raises.
    """
    directory = buffer_dir(grid_uuid)
    if directory is None or not _is_bare_frame_name(filename):
        return None
    if filename not in _load_manifest(directory)["frames"]:
        return None
    path = directory / filename
    try:
        if not path.is_file():
            return None
    except OSError:
        return None
    return path


def _frame_mtime_ms(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns // 1_000_000
    except OSError:
        return 0


# ------------------------------------------- with_frame_mtimes cache (2026-08-26)
# (mid-run responsiveness audit -- see the module docstring's dated section.)

#: How many distinct grid_uuids' decorated-ref lists to keep at once. Small
#: on purpose: one entry is one buffer's worth of refs (a handful of dicts,
#: not the frames themselves), and a session rarely has more than a few
#: EPSImageGrid nodes open across however many workflows were touched --
#: this just stops an unbounded server session from accumulating one entry
#: per grid_uuid ever seen forever. Eviction is plain LRU (:class:`OrderedDict`,
#: oldest-untouched-first) via :func:`_mtime_cache_put`.
_MTIME_CACHE_MAX_UUIDS = 64

#: grid_uuid -> (generation, [filename, ...], decorated refs). The filename
#: list is a cheap (no filesystem) sanity check that the cached decoration
#: was built from the SAME frame sequence a same-generation caller is now
#: asking about, on top of the generation match itself.
_mtime_cache: OrderedDict[str, tuple[int, list[Any], list[dict]]] = OrderedDict()


def _mtime_cache_get(grid_uuid: str, generation: int, names: list[Any]) -> list[dict] | None:
    entry = _mtime_cache.get(grid_uuid)
    if entry is None or entry[0] != generation or entry[1] != names:
        return None
    _mtime_cache.move_to_end(grid_uuid)
    return entry[2]


def _mtime_cache_put(
    grid_uuid: str, generation: int, names: list[Any], decorated: list[dict]
) -> None:
    _mtime_cache[grid_uuid] = (generation, names, decorated)
    _mtime_cache.move_to_end(grid_uuid)
    while len(_mtime_cache) > _MTIME_CACHE_MAX_UUIDS:
        _mtime_cache.popitem(last=False)  # evict the least-recently-used uuid


def _mtime_cache_invalidate(grid_uuid: str) -> None:
    """Drop *grid_uuid*'s cached decoration, if any -- called from every
    write op (append/remove/clear/clone) so a stale entry can never survive
    past the write that made it stale. Belt-and-suspenders on top of the
    generation-keying above (a write always advances the manifest mtime
    too, so a lookup after one would miss on its own) -- explicit here so
    the "write ops invalidate" contract doesn't depend on wall-clock
    resolution at all, matching :func:`buffer_generation`'s own documented
    ms-collision caveat rather than trusting it never bites this cache too.
    """
    _mtime_cache.pop(grid_uuid, None)


def _mtime_cache_clear() -> None:
    """Test-only: drop every cached decoration for every uuid.

    Production code never calls this -- individual write ops invalidate
    just their own uuid (:func:`_mtime_cache_invalidate`). This exists
    purely for test isolation: the cache is process-lifetime by design, and
    different tests reusing the same ``grid_uuid`` string (this pack's own
    ``VALID_UUID``/``OTHER_VALID_UUID`` fixtures) against DIFFERENT
    throwaway buffers could otherwise share a stale entry if two fast tests
    land on the same millisecond-resolution generation.
    """
    _mtime_cache.clear()


def with_frame_mtimes(grid_uuid: str, refs: list[dict]) -> list[dict]:
    """*refs* (the ``ui.images`` triples :func:`_refs_for` builds) with a
    per-frame ``"mtime"`` added -- that frame FILE's mtime in integer
    milliseconds (``0`` when it can't be stat'ed), the same unit
    :func:`buffer_generation` uses. New dicts; *refs* is never mutated.

    Why (2026-08-21 perf round): the frontend keyed every thumbnail URL on
    :func:`buffer_generation` -- the MANIFEST mtime, which moves on every
    append/remove/clone -- so after each Collect run all N UNCHANGED frames
    got a brand-new URL: N re-downloads + N full-resolution re-encodes
    (core's ``/view?preview=`` does not resize) per run, scaling with
    buffer size. A frame file's own mtime changes only when THAT file is
    (re)written -- a fresh append, or a post-Clear re-add that reuses its
    name (the very case the generation token existed for) -- so keying on
    it keeps unchanged frames' URLs stable across appends and the browser
    cache serves them. Rides every route that returns refs (``/list``,
    ``/add``, ``/remove``, ``/clone``); NOT the node's ``ui.images`` (core's
    shape, untouched) -- the frontend adopts the key from the next
    ``/list`` without a reload.

    **2026-08-26 while-running round:** this was itself an O(N) filesystem
    pass -- one ``stat()`` per frame, on EVERY call, and every ref-returning
    route calls it, which the panel hits once per FINISHED RUN during a
    sweep. Now cached per *grid_uuid*, keyed on :func:`buffer_generation`
    (plus a cheap filename-list check, see :func:`_mtime_cache_get`): while
    the buffer's generation hasn't moved, the cached decorated list from the
    previous call is returned outright and the stat loop never runs. Every
    write op invalidates its own uuid's entry (:func:`_mtime_cache_invalidate`),
    so a stale decoration can never be served. Callers on the event loop
    should still run this through ``asyncio.to_thread`` on a miss (the
    cache lookup itself is cheap, but the miss path is the same stat loop
    as before) -- see ``routes_image_grid.py``.
    """
    generation = buffer_generation(grid_uuid)
    names = [ref.get("filename") if isinstance(ref, dict) else None for ref in refs]
    cached = _mtime_cache_get(grid_uuid, generation, names)
    if cached is not None:
        return cached

    directory = buffer_dir(grid_uuid)
    decorated: list[dict] = []
    for ref, name in zip(refs, names, strict=True):
        mtime = 0
        if directory is not None and _is_bare_frame_name(name):
            mtime = _frame_mtime_ms(directory / name)
        decorated.append({**ref, "mtime": mtime})

    _mtime_cache_put(grid_uuid, generation, names, decorated)
    return decorated


def _thumb_path_for(frame: Path) -> Path:
    return frame.parent / THUMBS_DIRNAME / f"{frame.name}.webp"


def thumbnail_path(
    grid_uuid: str, filename: str, *, max_edge: int = THUMB_MAX_EDGE
) -> Path | None:
    """The cached DOWNSCALED copy of one manifest-listed frame --
    ``<buffer>/.thumbs/<frame>.webp``, long edge at most *max_edge* px
    (aspect kept), webp quality :data:`THUMB_QUALITY` -- generated (or
    regenerated) on demand. ``None`` when the frame can't be resolved
    (:func:`frame_path`'s rules) or the thumbnail can't be produced (PIL
    missing/failing, an unreadable frame, a read-only dir) -- the route then
    404s and the frontend degrades to core's ``/view?preview=`` for that
    one frame. Never raises.

    Staleness: the thumb is stamped with the SOURCE frame's exact
    ``mtime_ns`` (``os.utime``) and reused only while the two still match.
    Equality, not "thumb newer than source", on purpose -- a post-Clear
    re-add reuses the frame NAME with new pixels (:func:`_next_frame_filename`
    restarts from the highest existing frame), and a future-dated source
    (clock skew on a synced/NAS output dir) would otherwise regenerate on
    every single request. Written with the same temp + ``os.replace`` as
    every other file here, so two concurrent first requests for one frame
    simply both produce the same bytes. CPU work -- ``GET /eps_image_grid/
    frame``'s ``preview`` branch runs THIS CALL off the event loop
    (``asyncio.to_thread``). (2026-08-26: that route's OTHER branch --
    :func:`frame_path`, the full-PNG path -- used to run synchronously on
    the loop despite this docstring's wording reading as if the whole route
    were already covered; it is now offloaded too, symmetrically -- see
    ``routes_image_grid.py``.)
    """
    source = frame_path(grid_uuid, filename)
    if source is None:
        return None
    try:
        src_stat = source.stat()
    except OSError:
        return None
    thumb = _thumb_path_for(source)
    try:
        thumb_stat = thumb.stat()
        if thumb_stat.st_size > 0 and thumb_stat.st_mtime_ns == src_stat.st_mtime_ns:
            return thumb
    except OSError:
        pass  # no cached thumb yet (or unreadable) -- build it below

    try:
        import io

        from PIL import Image

        with Image.open(source) as raw:
            image = raw.convert("RGB")
            image.thumbnail((max_edge, max_edge))
            buffer = io.BytesIO()
            image.save(buffer, format="WEBP", quality=THUMB_QUALITY)
        _atomic_write_bytes(thumb, buffer.getvalue())
        os.utime(thumb, ns=(src_stat.st_atime_ns, src_stat.st_mtime_ns))
    except Exception as exc:  # broad on purpose -- display-only; the route must never 500
        # Broad on purpose (unlike the sibling PIL catches): a thumbnail is a
        # pure display optimization with a documented fallback, so ANY
        # failure here -- PIL absent, no webp codec, a corrupt frame, a
        # read-only dir -- must degrade, never surface.
        logger.warning("eps_image_grid: could not build thumbnail for %s (%s)", source, exc)
        return None
    return thumb


def read_all_as_tensors(grid_uuid: str) -> list:
    """Every buffered frame, decoded fresh from disk, as its own
    ``[1,H,W,C]`` float tensor (FORMAT.md §6.6: "NEVER stacked — buffered
    images may differ in size"). Nothing is cached between calls — this IS
    the lazy decode the docs ask for.

    ``[]`` for an invalid uuid or an empty buffer. A frame file that's
    missing, truncated, or otherwise unreadable is logged and skipped
    rather than raising — one corrupt/hand-deleted PNG must not sink the
    whole emit.
    """
    directory = buffer_dir(grid_uuid)
    if directory is None:
        return []

    import numpy as np
    import torch
    from PIL import Image, ImageOps

    manifest = _load_manifest(directory)
    tensors = []
    for name in manifest["frames"]:
        path = directory / name
        try:
            with Image.open(path) as raw:
                # Mirrors core `LoadImage`'s own PNG->tensor conversion
                # (ComfyUI `nodes.py`), restricted to the plain-RGB case —
                # `append_batch` above never writes anything else.
                pil_image = ImageOps.exif_transpose(raw)
                pil_image = pil_image.convert("RGB")
                array = np.array(pil_image).astype(np.float32) / 255.0
        except (OSError, ValueError, SyntaxError) as exc:
            # Same PIL SyntaxError gap as append_uploaded_image's catch --
            # one corrupt frame must skip, not sink the whole Emit.
            logger.warning("eps_image_grid: skipping unreadable frame %s (%s)", path, exc)
            continue
        tensors.append(torch.from_numpy(array)[None, ...])
    return tensors


def read_frame_as_tensor(grid_uuid: str, filename: str) -> Any | None:
    """Decode ONE manifest-listed frame fresh from disk, as its own
    ``[1,H,W,C]`` float tensor -- the single-frame counterpart to
    :func:`read_all_as_tensors`, added for ``EPSImageGrid``'s Emit-mode
    ``focus`` narrowing (``nodes_image_grid.py``'s ``run()``, owner ask
    2026-08-23: "the widget should only output that one image"). Decoding
    the WHOLE buffer just to pick one frame back out of it would be needless
    work on top of a buffer that already has no size cap.

    Reuses :func:`frame_path` as its one gate (uuid regex, bare single-
    segment name, MANIFEST-listed, exists) — the same validation
    :func:`remove_frame`/the ``GET /eps_image_grid/frame`` route already
    trust — so a hostile or merely stale *filename* (a frame deleted since
    the caller last read the buffer) can never reach a filesystem path here
    at all. Returns ``None``, never raises, for either that case or a
    decode failure (missing/truncated/corrupt PNG) — the SAME per-frame
    tolerance :func:`read_all_as_tensors` already applies to one bad frame
    among many; the caller (``nodes_image_grid.py``) treats a ``None`` here
    as "this focus no longer resolves" and degrades to emitting the whole
    buffer instead, logging its own warning.
    """
    path = frame_path(grid_uuid, filename)
    if path is None:
        return None

    import numpy as np
    import torch
    from PIL import Image, ImageOps

    try:
        with Image.open(path) as raw:
            # Mirrors read_all_as_tensors' own conversion exactly (same
            # plain-RGB case; append_batch never writes anything else).
            pil_image = ImageOps.exif_transpose(raw)
            pil_image = pil_image.convert("RGB")
            array = np.array(pil_image).astype(np.float32) / 255.0
    except (OSError, ValueError, SyntaxError) as exc:
        # Same PIL SyntaxError gap as append_uploaded_image's catch.
        logger.warning("eps_image_grid: skipping unreadable frame %s (%s)", path, exc)
        return None
    return torch.from_numpy(array)[None, ...]


# --------------------------------------------------------------------- clear


def clear(grid_uuid: str) -> bool:
    """Wipe the buffer dir (manifest + every frame).

    ``True`` iff a buffer directory existed and was removed; ``False`` for
    an invalid uuid or an already-empty/nonexistent buffer — both are a
    successful no-op, not an error (FORMAT.md §6.6 "Never crash on a
    missing/malformed dir").
    """
    directory = buffer_dir(grid_uuid)
    if directory is None or not directory.exists():
        return False
    shutil.rmtree(directory, ignore_errors=True)
    _mtime_cache_invalidate(grid_uuid)  # 2026-08-26: a write must drop any cached decoration
    return True


# --------------------------------------------------------------------- clone


def clone_buffer(src_uuid: str, dst_uuid: str) -> list[dict]:
    """Copy *src_uuid*'s whole buffer (manifest + every ``NNNN.png`` frame)
    into *dst_uuid*'s buffer dir, so the two uuids end up as INDEPENDENT
    copies (FORMAT.md §6.6 "Copy carries the images, independently") — a
    later append/clear against either uuid never touches the other.

    This is the backend half of the "images didn't travel to a copy" fix:
    the frontend (`image_grid.js`'s `ensureUniqueUuid`) calls this right
    after minting a fresh uuid for an in-graph duplicate (a genuine
    live-sibling collision), so the duplicate starts with its own copy of
    the original's images instead of an empty buffer.

    Returns the refs for *dst_uuid*'s buffer after the copy — same
    ``list_refs`` shape every other function here returns, so a caller never
    needs a separate follow-up call. A safe no-op that returns ``[]`` and
    touches NOTHING on disk (does not even create *dst_uuid*'s directory)
    when: either uuid fails :func:`is_valid_grid_uuid`, *src_uuid* has no
    buffer directory yet, or its buffer is empty (nothing to clone) — never
    raises. An individual frame that's missing or unreadable is logged and
    skipped rather than aborting the whole clone (mirrors
    :func:`read_all_as_tensors`'s per-frame tolerance); only frames actually
    copied are recorded in *dst_uuid*'s manifest.
    """
    src_dir = buffer_dir(src_uuid)
    dst_dir = buffer_dir(dst_uuid)
    if src_dir is None or dst_dir is None:
        logger.warning(
            "eps_image_grid: refusing to clone -- invalid uuid(s) src=%r dst=%r",
            src_uuid,
            dst_uuid,
        )
        return []
    if not src_dir.exists():
        return []

    manifest = _load_manifest(src_dir)
    copied: list[str] = []
    for name in manifest["frames"]:
        src_path = src_dir / name
        try:
            data = src_path.read_bytes()
        except OSError as exc:
            logger.warning(
                "eps_image_grid: clone skipping unreadable frame %s (%s)", src_path, exc
            )
            continue
        _atomic_write_bytes(dst_dir / name, data)
        copied.append(name)

    if not copied:
        return []

    _save_manifest(dst_dir, {"format": CURRENT_FORMAT, "frames": copied})
    _mtime_cache_invalidate(dst_uuid)  # 2026-08-26: a write must drop any cached decoration
    return list_refs(dst_uuid)
