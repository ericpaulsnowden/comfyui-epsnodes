"""HTTP routes for LoRA sets — the four ``/lora_library/set*`` rows of
FORMAT.md §5 (``GET /lora_library/loras`` already lives in ``routes.py``'s
core registrar; not this module's concern).

FORMAT.md §2's remote-caller boundary is "stay inside ``library_dir``"; a
set slug can only ever resolve to ``context.sets_dir() / f"{slug}.json"``
(:func:`sets_store.set_path`), which is always inside it by construction —
there is no "elsewhere" a validated slug could point to. So, unlike the
notebook routes (arbitrary ``file`` paths) or ``POST /config`` (moves the
boundary itself), none of the set rows below need a
``request_is_loopback`` check: the ``SLUG_RE`` format check is the whole
guard, for both local and remote callers. The one exception is
``POST /lora_library/sets/open_folder`` (NAS round 2026-08-22), which
drives the SERVER machine's desktop file manager and is therefore
loopback-only exactly like the notebook's ``open_folder``.

Every store call runs via ``asyncio.to_thread`` (same round, see
``routes_notebook.py``'s module docstring): ``list_sets``/``load_set``/
``save_set``/``delete_set``/``load_layout``/``save_layout`` are network
round trips on a NAS library, and they used to run INSIDE the aiohttp
handler, stalling every request on the server while one slow mount
answered. The error mapping around each call is unchanged.
"""

from __future__ import annotations

import asyncio
import logging

from aiohttp import web

from . import sets_store
from .context import SETS_DIRNAME, LibraryContext
from .routes import SLUG_RE, error_response, request_is_loopback
from .routes_notebook import _reveal_folder

logger = logging.getLogger("lora_library")


def _bad_set_id(value: object) -> str:
    """The user-facing text for a set id that fails ``SLUG_RE``.

    Shown verbatim in the set editor, so it names the rule rather than
    the FORMAT.md §4 section that specifies it (cited in the module
    docstring, where developers read it)."""
    return (
        f"invalid set id {value!r} — a set id may use only lowercase "
        "letters, digits, - and _, and must start with a letter or digit"
    )


def _sets_payload(context: LibraryContext) -> dict:
    """``GET /lora_library/sets``'s body (FORMAT.md §5): the cached listing
    plus, since the NAS round (2026-08-22), WHERE the sets live --
    ``sets_dir`` (the folder's path, named purely from the config: no
    mkdir, so an unreachable library still reports the path it should be
    at) and ``is_default_library`` (the library is the pack's own default
    folder rather than a configured one) -- so the frontend can tell the
    user which machine/share their states are on. Runs in a worker thread
    (``list_sets`` is disk I/O)."""
    return {
        "sets": sets_store.list_sets(context),
        "sets_dir": str(context.configured_library_dir() / SETS_DIRNAME),
        "is_default_library": context.is_default_library(),
    }


def register(context: LibraryContext, routes: web.RouteTableDef) -> None:
    """Attach the §5 set rows to *routes*."""

    @routes.get("/lora_library/sets")
    async def get_sets(_request: web.Request) -> web.Response:
        return web.json_response(await asyncio.to_thread(_sets_payload, context))

    @routes.post("/lora_library/sets/open_folder")
    async def post_sets_open_folder(request: web.Request) -> web.Response:
        """FORMAT.md §5 (NAS round 2026-08-22): reveal ``<library>/sets`` in
        the OS file manager ON THE SERVER MACHINE -- the notebook's
        ``open_folder`` for the states folder, same loopback-only wording,
        same ``_reveal_folder`` technique (imported, not copied). No body
        fields are read, so none are required. An unreachable library
        folder is the 400 every other set route answers with
        (``sets_store._require_sets_dir``'s wording)."""
        if not request_is_loopback(request):
            return error_response(
                403,
                "opening a folder only works in a browser on the machine "
                "ComfyUI runs on",
            )
        try:
            folder = await asyncio.to_thread(sets_store._require_sets_dir, context)
        except sets_store.SetValidationError as exc:
            return error_response(400, str(exc))
        try:
            _reveal_folder(folder)
        except Exception as exc:  # broad: any spawn failure surfaces to the caller
            return error_response(500, str(exc))
        return web.json_response({"ok": True, "path": str(folder)})

    # ---- §4.2 sets layout (v0.65.0): categories + display order for the
    # controller's left pane. No loopback gate -- the file lives inside
    # library_dir, which §2 already grants remote read+write (the picker
    # feed's exact rationale). POST is a full-replace, healed server-side
    # (unknown slugs dropped, missing sets appended uncategorized), so a
    # stale client can never vanish a set from the pane.

    @routes.get("/lora_library/sets_layout")
    async def get_sets_layout(_request: web.Request) -> web.Response:
        layout = await asyncio.to_thread(sets_store.load_layout, context)
        return web.json_response({"layout": layout})

    @routes.post("/lora_library/sets_layout")
    async def post_sets_layout(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:  # broad: malformed body is a client error
            return error_response(400, "body must be JSON")
        if not isinstance(body, dict) or not isinstance(body.get("layout"), dict):
            return error_response(400, "'layout' must be an object")
        try:
            layout = await asyncio.to_thread(sets_store.save_layout, context, body["layout"])
        except OSError as exc:
            return error_response(500, f"could not write the sets layout: {exc}")
        return web.json_response({"ok": True, "layout": layout})

    @routes.get("/lora_library/set")
    async def get_set(request: web.Request) -> web.Response:
        slug = request.query.get("slug", "")
        if not SLUG_RE.match(slug):
            return error_response(400, _bad_set_id(slug))
        try:
            data = await asyncio.to_thread(sets_store.load_set, context, slug)
        except sets_store.SetValidationError as exc:
            return error_response(400, str(exc))
        if data is None:
            return error_response(404, f"no such set {slug!r}")
        return web.json_response({**data, "slug": slug})

    @routes.post("/lora_library/set")
    async def post_set(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:  # broad: malformed body is a client error
            return error_response(400, "body must be JSON")
        if not isinstance(body, dict):
            return error_response(400, "body must be a JSON object")

        raw_slug = body.get("slug")
        slug: str | None
        if raw_slug in (None, ""):
            slug = None  # derive it from set.name (FORMAT.md §4)
        elif isinstance(raw_slug, str) and SLUG_RE.match(raw_slug):
            slug = raw_slug
        else:
            return error_response(400, _bad_set_id(raw_slug))

        try:
            saved_slug, _normalized = await asyncio.to_thread(
                sets_store.save_set, context, body.get("set"), slug=slug
            )
        except sets_store.SetValidationError as exc:
            return error_response(400, str(exc))
        sets = await asyncio.to_thread(sets_store.list_sets, context)
        return web.json_response({"ok": True, "slug": saved_slug, "sets": sets})

    @routes.post("/lora_library/set/delete")
    async def post_set_delete(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:  # broad: malformed body is a client error
            return error_response(400, "body must be JSON")
        if not isinstance(body, dict):
            return error_response(400, "body must be a JSON object")
        slug = body.get("slug")
        if not isinstance(slug, str) or not SLUG_RE.match(slug):
            return error_response(400, _bad_set_id(slug))
        try:
            deleted = await asyncio.to_thread(sets_store.delete_set, context, slug)
        except sets_store.SetValidationError as exc:
            # An unreachable library folder (sets_store._require_sets_dir,
            # audit 2026-08-08) -- a 400 naming the folder, never a raw 500.
            return error_response(400, str(exc))
        if not deleted:
            return error_response(404, f"no such set {slug!r}")
        sets = await asyncio.to_thread(sets_store.list_sets, context)
        return web.json_response({"ok": True, "sets": sets})
