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
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import tempfile
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
        os.replace(tmp_name, path)
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
    except (OSError, ValueError) as exc:
        logger.warning("eps_image_grid: could not read %s to add (%s)", source_path, exc)
        return list_refs(grid_uuid)

    manifest = _load_manifest(directory)
    frames = manifest["frames"]
    name = _next_frame_filename(frames)
    _atomic_write_bytes(directory / name, png_bytes)
    frames.append(name)
    _save_manifest(directory, manifest)
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
        except (OSError, ValueError) as exc:
            logger.warning("eps_image_grid: skipping unreadable frame %s (%s)", path, exc)
            continue
        tensors.append(torch.from_numpy(array)[None, ...])
    return tensors


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
    return list_refs(dst_uuid)
