"""HTTP routes for EPSLoraPicker's favorites/recents feed -- the four
``/lora_library/picker*`` rows of FORMAT.md §5 (feature spec §6.13).

Same layering ``lora_library/routes_sets.py`` and
``eps_image/routes_resolution_presets.py`` use: :func:`register` takes the
injected ``LibraryContext`` AND a route table (identical ``register(context,
routes)`` name/shape), thin aiohttp handlers doing only HTTP-shaped work
(status codes, body parsing, the ``file``/``on``/``files`` structural
checks), delegating persistence to ``lora_picker_store``.

§2 locality note (``routes_resolution_presets.py``'s own rationale,
verbatim): these routes have NO client-supplied path at all -- the picker
file always resolves to ``context.library_dir() /
lora_picker_store.PICKER_FILENAME``, so there is no "elsewhere" a caller
could point it. There is therefore no ``request_is_loopback`` gate anywhere
in this file: the file already lives inside ``library_dir``, which
FORMAT.md §2 already lets non-loopback callers read AND write -- the same
rationale FORMAT.md §5's ``GET /lora_library/picker`` row states verbatim
(the lora LIST itself is the same one ``/object_info`` already exposes to
every viewer, local or remote).

FORMAT.md §6.13 / ``lora_picker_store.ConflictError``'s own docstring: the
store supports an optional ``base_mtime`` for a two-machine conflict check,
but these routes deliberately never send one -- a star click or a recents
stamp must not 409; the read-modify-write race between two machines inside
the same second is accepted and self-heals on the next write. So
``store.ConflictError`` is never expected to reach any handler below, and
none of them catch it.
"""

from __future__ import annotations

import logging

from aiohttp import web

from . import lora_picker_store as store
from .context import LibraryContext
from .routes import error_response

logger = logging.getLogger("lora_library")


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


def register(context: LibraryContext, routes: web.RouteTableDef) -> None:
    """Attach the four §5 ``/lora_library/picker*`` rows to *routes*."""

    @routes.get("/lora_library/picker")
    async def get_picker(_request: web.Request) -> web.Response:
        loras = context.list_loras()
        state, mtime = store.load_state(context)
        return web.json_response(
            {
                "loras": loras,
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
