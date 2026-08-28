"""HTTP routes for UNIVERSAL STATES (FORMAT.md §4.3/§6.16 M1) -- the
``/lora_library/universal_state*`` rows backing the "EPS Universal State
Controller".

Same layering ``eps_image/routes_resolution_presets.py`` uses for a
context-needing feature that is NOT wired through
``lora_library/routes.py``'s ``_register_all`` (this module's own concern,
not that shared file's): :func:`register` takes the injected
``LibraryContext`` AND a route table (mirrors ``routes_sets.register``'s
exact name/shape), :func:`build_routes` wraps a standalone table for
tests, and :func:`register_live` is the outer, ``PromptServer``-touching
entry point ``__init__.py`` calls directly in its own defensive block --
exactly like ``eps_image.routes_resolution_presets.register_live``.

§2 locality note (identical to ``routes_sets.py``'s own): a state slug can
only ever resolve to ``context.library_dir() / "states" / f"{slug}.json"``
(``universal_states_store.state_path``), which is always inside
``library_dir`` by construction -- there is no "elsewhere" a validated
slug could point to. So, unlike the notebook routes (arbitrary ``file``
paths), none of the rows below need a ``request_is_loopback`` check: the
``SLUG_RE`` format check is the whole guard, for both local and remote
callers -- copying ``routes_sets.py``'s posture for its own write routes
exactly (none of ``POST /set``, ``POST /set/delete``, ``POST
/sets_layout`` gate on locality either).

Every store call runs via ``asyncio.to_thread`` (same NAS-round-2026-08-22
discipline ``routes_sets.py`` documents): ``list_states_with_mtime``/
``load_state``/``save_state``/``delete_state``/``load_layout``/
``save_layout`` are network round trips on a NAS library.
"""

from __future__ import annotations

import asyncio
import logging

from aiohttp import web

from . import universal_states_store
from .context import LibraryContext
from .routes import SLUG_RE, error_response

logger = logging.getLogger("lora_library")


def _bad_state_id(value: object) -> str:
    """The user-facing text for a state id that fails ``SLUG_RE`` --
    mirrors ``routes_sets._bad_set_id``."""
    return (
        f"invalid state id {value!r} — a state id may use only lowercase "
        "letters, digits, - and _, and must start with a letter or digit"
    )


def _states_payload(context: LibraryContext) -> dict:
    """``GET /lora_library/universal_states``'s body (FORMAT.md §5): the
    cached listing, WHERE the states live (mirrors
    ``routes_sets._sets_payload``'s ``sets_dir``/``is_default_library``
    fields), the §4.2-style layout sidecar in the SAME response -- one
    round trip for the panel's initial load (the v0.82.0 while-running
    round's lesson: a controller that fetches its listing and its layout
    as two separate requests pays two NAS round trips for one screen) --
    and a bare directory ``mtime`` (float seconds, the same convention
    ``routes_notebook.py``'s per-file conflict-mtimes already use) the
    panel's shared poll can diff against without re-comparing the whole
    ``states`` array by value. Runs in a worker thread."""
    states, mtime = universal_states_store.list_states_with_mtime(context)
    return {
        "ok": True,
        "states": states,
        "states_dir": str(
            context.configured_library_dir() / universal_states_store.STATES_DIRNAME
        ),
        "is_default_library": context.is_default_library(),
        "layout": universal_states_store.load_layout(context),
        "mtime": mtime,
    }


def register(context: LibraryContext, routes: web.RouteTableDef) -> None:
    """Attach the §5 universal-state rows to *routes*."""

    @routes.get("/lora_library/universal_states")
    async def get_universal_states(_request: web.Request) -> web.Response:
        return web.json_response(await asyncio.to_thread(_states_payload, context))

    @routes.post("/lora_library/universal_states/layout")
    async def post_universal_states_layout(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:  # broad: malformed body is a client error
            return error_response(400, "body must be JSON")
        if not isinstance(body, dict) or not isinstance(body.get("layout"), dict):
            return error_response(400, "'layout' must be an object")
        try:
            layout = await asyncio.to_thread(
                universal_states_store.save_layout, context, body["layout"]
            )
        except OSError as exc:
            return error_response(500, f"could not write the universal states layout: {exc}")
        return web.json_response({"ok": True, "layout": layout})

    @routes.get("/lora_library/universal_state")
    async def get_universal_state(request: web.Request) -> web.Response:
        slug = request.query.get("slug", "")
        if not SLUG_RE.match(slug):
            return error_response(400, _bad_state_id(slug))
        try:
            data = await asyncio.to_thread(universal_states_store.load_state, context, slug)
        except universal_states_store.StateValidationError as exc:
            return error_response(400, str(exc))
        if data is None:
            return error_response(404, f"no such universal state {slug!r}")
        return web.json_response({**data, "slug": slug})

    @routes.post("/lora_library/universal_state")
    async def post_universal_state(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:  # broad: malformed body is a client error
            return error_response(400, "body must be JSON")
        if not isinstance(body, dict):
            return error_response(400, "body must be a JSON object")

        raw_slug = body.get("slug")
        slug: str | None
        if raw_slug in (None, ""):
            slug = None  # derive it from state.name (FORMAT.md §4.3)
        elif isinstance(raw_slug, str) and SLUG_RE.match(raw_slug):
            slug = raw_slug
        else:
            return error_response(400, _bad_state_id(raw_slug))

        try:
            saved_slug, _normalized, foreign = await asyncio.to_thread(
                universal_states_store.save_state, context, body.get("state"), slug=slug
            )
        except universal_states_store.StateValidationError as exc:
            return error_response(400, str(exc))
        states = await asyncio.to_thread(universal_states_store.list_states, context)
        return web.json_response(
            {"ok": True, "slug": saved_slug, "states": states, "foreign": foreign}
        )

    @routes.post("/lora_library/universal_state/delete")
    async def post_universal_state_delete(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:  # broad: malformed body is a client error
            return error_response(400, "body must be JSON")
        if not isinstance(body, dict):
            return error_response(400, "body must be a JSON object")
        slug = body.get("slug")
        if not isinstance(slug, str) or not SLUG_RE.match(slug):
            return error_response(400, _bad_state_id(slug))
        try:
            deleted = await asyncio.to_thread(universal_states_store.delete_state, context, slug)
        except universal_states_store.StateValidationError as exc:
            # An unreachable library folder (_require_states_dir) -- a 400
            # naming the folder, never a raw 500 (mirrors routes_sets.py).
            return error_response(400, str(exc))
        if not deleted:
            return error_response(404, f"no such universal state {slug!r}")
        states = await asyncio.to_thread(universal_states_store.list_states, context)
        return web.json_response({"ok": True, "states": states})


def build_routes(context: LibraryContext) -> web.RouteTableDef:
    """A standalone table with just this module's routes, bound to
    *context* -- the tests' entry point (wrapped in a plain
    ``aiohttp.web.Application``, no ComfyUI needed), mirroring
    ``eps_image/routes_resolution_presets.build_routes``'s identical
    shape."""
    routes = web.RouteTableDef()
    register(context, routes)
    return routes


def register_live(context: LibraryContext) -> None:
    """Attach this module's routes to ComfyUI's live server -- the OUTER,
    ``PromptServer``-touching entry point ``__init__.py`` calls, exactly
    like ``eps_image.routes_resolution_presets.register_live``. Named
    differently from :func:`register` for the same reason that module's
    docstring gives: avoiding a same-module collision with the context+
    routes INNER function."""
    from server import PromptServer  # ComfyUI's own module; import only inside ComfyUI

    register(context, PromptServer.instance.routes)
    logger.info("EPSNodes: registered /lora_library/universal_state* routes")
