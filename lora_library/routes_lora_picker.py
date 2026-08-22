"""HTTP routes for EPSLoraPicker's favorites/recents feed and M3
search/preview/reorder bullets -- the seven ``/lora_library/picker*`` rows
of FORMAT.md §5 (feature spec §6.13).

Same layering ``lora_library/routes_sets.py`` and
``eps_image/routes_resolution_presets.py`` use: :func:`register` takes the
injected ``LibraryContext`` AND a route table (identical ``register(context,
routes)`` name/shape), thin aiohttp handlers doing only HTTP-shaped work
(status codes, body parsing, the ``file``/``on``/``files`` structural
checks), delegating persistence to ``lora_picker_store``.

§2 locality note (``routes_resolution_presets.py``'s own rationale,
verbatim): the favorites/recents routes have NO client-supplied path at
all -- the picker file always resolves to ``context.library_dir() /
lora_picker_store.PICKER_FILENAME``, so there is no "elsewhere" a caller
could point it. There is therefore no ``request_is_loopback`` gate anywhere
in this file: the file already lives inside ``library_dir``, which
FORMAT.md §2 already lets non-loopback callers read AND write -- the same
rationale FORMAT.md §5's ``GET /lora_library/picker`` row states verbatim
(the lora LIST itself is the same one ``/object_info`` already exposes to
every viewer, local or remote). ``preview``/``info`` reach the same "no
gate" conclusion by a different road: their ``file`` value never becomes a
path directly -- it only ever resolves through ``folder_paths``' own
allow-listed lora dirs (via ``sets_store.resolve_lora`` +
``context.resolve_lora_path``), so a remote caller can request a
thumbnail/trigger-words for an INSTALLED lora only, never an arbitrary
path -- the identical "no elsewhere to point it" shape, one level of
indirection removed. See each handler's own comment below.

FORMAT.md §6.13 / ``lora_picker_store.ConflictError``'s own docstring: the
store supports an optional ``base_mtime`` for a two-machine conflict check,
but these routes deliberately never send one -- a star click, a recents
stamp, or a favorites drag-reorder must not 409; the read-modify-write race
between two machines inside the same second is accepted and self-heals on
the next write. So ``store.ConflictError`` is never expected to reach any
handler below, and none of them catch it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

from aiohttp import web

from . import lora_picker_store as store
from . import nodes_picker, sets_store
from .context import LibraryContext
from .routes import error_response

logger = logging.getLogger("lora_library")

#: FORMAT.md §6.13 M3 preview route -- extensions tried IN THIS ORDER (png
#: beats jpg beats jpeg beats webp when more than one sibling exists); two
#: spellings per extension (see :func:`_find_preview_image`).
_PREVIEW_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")


def _clean_recent_files(raw: object) -> tuple[list[str] | None, str | None]:
    """The route layer's validation for ``POST picker/recent``'s ``files``:
    must be a list, each entry a non-empty (post-strip) string. Returns
    ``(files, None)`` on success or ``(None, message)`` naming the first
    problem -- ``lora_picker_store.record_recents`` does its own cleaning
    too, but a structurally bad entry here is the caller's bug, not
    something to silently drop (same split ``_validate_values`` documents
    in ``routes_resolution_presets.py``)."""
    if not isinstance(raw, list):
        return None, "'files' must be a list"
    files: list[str] = []
    for entry in raw:
        if not isinstance(entry, str) or not entry.strip():
            return None, "'files' entries must be non-empty strings"
        files.append(entry)
    return files, None


def _resolve_installed_lora(context: LibraryContext, file_value: object) -> str | None:
    """Resolve *file_value* to an installed lora's NAME, exactly the way
    ``nodes_picker.EPSLoraPicker.build`` resolves each enabled selection
    row: §4-lenient name match via ``sets_store.resolve_lora``
    (separator-insensitive, unique-basename-tolerant), THEN
    ``context.resolve_lora_path`` to confirm it has a real path on this
    machine. Returns the resolved (installed) spelling when both steps
    succeed, or ``None`` when either fails -- an unknown name and an
    installed-list name with no on-disk path on this machine both read the
    same way to ``preview``/``info`` below: not found, not a 400 (FORMAT.md
    §6.13 M3: "an unknown name is NOT an error worth 400").
    """
    if not isinstance(file_value, str):
        return None
    resolved_name = sets_store.resolve_lora(context, file_value)
    if resolved_name is None:
        return None
    if context.resolve_lora_path(resolved_name) is None:
        return None
    return resolved_name


def _find_preview_image(resolved: Path) -> Path | None:
    """The first sibling preview image next to *resolved* (an installed
    lora's own on-disk path), or ``None`` when none exist (FORMAT.md §6.13
    M3).

    Extensions are tried in :data:`_PREVIEW_EXTENSIONS` order (the
    priority order -- a ``.png`` sibling wins over a ``.jpg`` one even
    when both exist); for EACH extension, two spellings are tried, plain
    first: ``<resolved with its own extension replaced>`` (e.g.
    ``detailer.png``) and ``<resolved's stem>.preview.<ext>`` (e.g.
    ``detailer.preview.png``) -- the two conventions lora-preview tooling
    in the wild actually writes. First hit wins.
    """
    for ext in _PREVIEW_EXTENSIONS:
        plain = resolved.with_suffix(ext)
        if plain.is_file():
            return plain
        dotted = resolved.with_suffix(f".preview{ext}")
        if dotted.is_file():
            return dotted
    return None


def _loras_with_previews(context: LibraryContext, loras: list[str]) -> list[str]:
    """The subset of *loras* (served names, feed order) whose sidecar
    preview image exists on this machine -- the same two spellings per
    extension :func:`_find_preview_image` checks, but computed with ONE
    directory listing per unique folder instead of eight stat calls per
    lora, so a large library stays cheap even on network storage.

    This feeds the panel its thumbnail verdicts UP FRONT (owner report
    2026-08-14): probing availability client-side with per-lora ``<img>``
    404s painted a placeholder square on every row that then vanished one
    by one as each 404 landed -- LAN-staggered, re-laying the list out
    every time. Names are compared case-insensitively: the stat-based
    route tolerates any casing on the case-insensitive filesystems the
    packs actually run on (Windows/macOS), and a Linux false positive
    merely degrades to the old hide-on-404 path for that one row.
    """
    listings: dict[Path, set[str]] = {}
    out: list[str] = []
    for name in loras:
        path_str = context.resolve_lora_path(name)
        if path_str is None:
            continue
        resolved = Path(path_str)
        parent = resolved.parent
        entries = listings.get(parent)
        if entries is None:
            try:
                entries = {entry.lower() for entry in os.listdir(parent)}
            except OSError:
                entries = set()
            listings[parent] = entries
        stem = resolved.stem.lower()
        if any(
            f"{stem}{ext}" in entries or f"{stem}.preview{ext}" in entries
            for ext in _PREVIEW_EXTENSIONS
        ):
            out.append(name)
    return out


#: (loras-list key, monotonic time, previews) -- see `_previews_cached`.
_previews_cache: tuple[tuple[str, ...], float, list[str]] | None = None
_PREVIEWS_TTL_S = 20.0


def _previews_cache_clear() -> None:
    """Test seam / explicit invalidation."""
    global _previews_cache
    _previews_cache = None


async def _previews_cached(context: LibraryContext, loras: list[str]) -> list[str]:
    """`_loras_with_previews` with two audit-2026-08-21 fixes: it runs in a
    worker thread (the sweep is one `folder_paths.get_full_path` stat per
    lora plus one listdir per folder -- on a NAS library that blocked the
    whole event loop, queue submissions included, on every feed request),
    and its result is reused for `_PREVIEWS_TTL_S` while the lora list is
    byte-identical (a tab switch re-creates every picker node and each one
    fetched the feed). A preview image added on disk shows up within the
    TTL; a lora added/removed changes the key and refreshes at once."""
    global _previews_cache
    key = tuple(loras)
    now = time.monotonic()
    cached = _previews_cache
    if cached is not None and cached[0] == key and now - cached[1] < _PREVIEWS_TTL_S:
        return list(cached[2])
    previews = await asyncio.to_thread(_loras_with_previews, context, loras)
    _previews_cache = (key, now, list(previews))
    return previews


def register(context: LibraryContext, routes: web.RouteTableDef) -> None:
    """Attach the seven §5 ``/lora_library/picker*`` rows to *routes*."""

    @routes.get("/lora_library/picker")
    async def get_picker(_request: web.Request) -> web.Response:
        loras = context.list_loras()
        state, mtime = store.load_state(context)
        return web.json_response(
            {
                "loras": loras,
                "previews": await _previews_cached(context, loras),
                "favorites": state["favorites"],
                "recents": state["recents"],
                "mtime": mtime,
            }
        )

    @routes.post("/lora_library/picker/favorite")
    async def post_picker_favorite(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:  # broad: malformed body is a client error
            return error_response(400, "body must be JSON")
        if not isinstance(body, dict):
            return error_response(400, "body must be a JSON object")

        file = body.get("file")
        if not isinstance(file, str) or not file.strip():
            return error_response(400, "'file' must be a non-empty string")
        on = body.get("on")
        if not isinstance(on, bool):
            return error_response(400, "'on' must be a boolean")

        try:
            state, mtime = store.toggle_favorite(context, file, on)
        except store.PickerStoreError as exc:
            return error_response(400, str(exc))
        return web.json_response({"ok": True, "favorites": state["favorites"], "mtime": mtime})

    @routes.post("/lora_library/picker/recent")
    async def post_picker_recent(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:  # broad: malformed body is a client error
            return error_response(400, "body must be JSON")
        if not isinstance(body, dict):
            return error_response(400, "body must be a JSON object")

        files, err = _clean_recent_files(body.get("files"))
        if err:
            return error_response(400, err)

        try:
            state, mtime = store.record_recents(context, files)
        except store.PickerStoreError as exc:
            return error_response(400, str(exc))
        return web.json_response({"ok": True, "recents": state["recents"], "mtime": mtime})

    @routes.post("/lora_library/picker/clear_recents")
    async def post_picker_clear_recents(_request: web.Request) -> web.Response:
        # FORMAT.md §5: no body fields required -- an empty/absent/malformed
        # JSON body is fine, since nothing in it is ever read.
        try:
            state, mtime = store.clear_recents(context)
        except store.PickerStoreError as exc:
            return error_response(400, str(exc))
        return web.json_response({"ok": True, "recents": state["recents"], "mtime": mtime})

    @routes.get("/lora_library/picker/preview")
    async def get_picker_preview(request: web.Request) -> web.Response:
        # FORMAT.md §6.13 M3 / this module's own docstring: NO
        # `request_is_loopback` gate -- `file` never becomes a path
        # directly, it only ever resolves through `folder_paths`' own
        # allow-listed lora dirs (`sets_store.resolve_lora` +
        # `context.resolve_lora_path`, below), so a remote LAN browser can
        # and must load thumbnails the same way it already loads the lora
        # list itself.
        file_value = request.query.get("file", "")
        if not file_value.strip():
            return error_response(400, "missing 'file' query parameter")

        resolved_name = _resolve_installed_lora(context, file_value)
        if resolved_name is None:
            return error_response(404, f"no installed lora named {file_value!r}")

        # _resolve_installed_lora already confirmed resolve_lora_path
        # succeeds for resolved_name -- re-checked (rather than trusted)
        # since a host callable could in principle answer differently
        # across two calls; None here still means "not found", never a 500.
        resolved_path = context.resolve_lora_path(resolved_name)
        if resolved_path is None:
            return error_response(404, f"no installed lora named {file_value!r}")
        preview = _find_preview_image(Path(resolved_path))
        if preview is None:
            return error_response(404, f"no preview image for {file_value!r}")

        # web.FileResponse sets Content-Type from the extension and gives
        # Range/If-Modified-Since/ETag support for free (same rationale
        # eps_frame_saver's own `/stream` route documents).
        return web.FileResponse(preview)

    @routes.get("/lora_library/picker/info")
    async def get_picker_info(request: web.Request) -> web.Response:
        # Same no-loopback-gate rationale as `preview` above.
        file_value = request.query.get("file", "")
        if not file_value.strip():
            return error_response(400, "missing 'file' query parameter")

        resolved_name = _resolve_installed_lora(context, file_value)
        if resolved_name is None:
            return error_response(404, f"no installed lora named {file_value!r}")

        # Reuses nodes_picker's own sidecar reader (not copied) -- same
        # UTF-8-best-effort, stripped, 4096-char-capped text the node's
        # `trigger_words` output already reads for this exact lora.
        trigger_words = nodes_picker._sidecar_trigger_text(context, resolved_name)
        return web.json_response({"trigger_words": trigger_words})

    @routes.post("/lora_library/picker/favorites_order")
    async def post_picker_favorites_order(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:  # broad: malformed body is a client error
            return error_response(400, "body must be JSON")
        if not isinstance(body, dict):
            return error_response(400, "body must be a JSON object")

        # Reuses `_clean_recent_files` as-is -- FORMAT.md §6.13 M3 wants
        # this route's `files` validated "the recent route's exact
        # validation style" (list of non-empty strings), and that is
        # exactly what it already does; its name is a `POST .../recent`
        # holdover, not a behavioral mismatch for this route.
        files, err = _clean_recent_files(body.get("files"))
        if err:
            return error_response(400, err)

        try:
            state, mtime = store.reorder_favorites(context, files)
        except store.PickerStoreError as exc:
            return error_response(400, str(exc))
        return web.json_response({"ok": True, "favorites": state["favorites"], "mtime": mtime})


def build_routes(context: LibraryContext) -> web.RouteTableDef:
    """A standalone table with just this module's routes, bound to
    *context* -- the tests' entry point (wrapped in a plain
    ``aiohttp.web.Application``, no ComfyUI needed), mirroring
    ``eps_image/routes_resolution_presets.py``'s own ``build_routes(context)``
    shape.
    """
    routes = web.RouteTableDef()
    register(context, routes)
    return routes
