"""HTTP routes for LoRA sets — the four ``/lora_library/set*`` rows of
FORMAT.md §5 (``GET /lora_library/loras`` already lives in ``routes.py``'s
core registrar; not this module's concern).

FORMAT.md §2's remote-caller boundary is "stay inside ``library_dir``"; a
set slug can only ever resolve to ``context.sets_dir() / f"{slug}.json"``
(:func:`sets_store.set_path`), which is always inside it by construction —
there is no "elsewhere" a validated slug could point to. So, unlike the
notebook routes (arbitrary ``file`` paths) or ``POST /config`` (moves the
boundary itself), none of the four routes below need a
``request_is_loopback`` check: the ``SLUG_RE`` format check is the whole
guard, for both local and remote callers.
"""

from __future__ import annotations

import logging

from aiohttp import web

from . import sets_store
from .context import LibraryContext
from .routes import SLUG_RE, error_response

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


def register(context: LibraryContext, routes: web.RouteTableDef) -> None:
    """Attach the §5 set rows to *routes*."""

    @routes.get("/lora_library/sets")
    async def get_sets(_request: web.Request) -> web.Response:
        return web.json_response({"sets": sets_store.list_sets(context)})

    @routes.get("/lora_library/set")
    async def get_set(request: web.Request) -> web.Response:
        slug = request.query.get("slug", "")
        if not SLUG_RE.match(slug):
            return error_response(400, _bad_set_id(slug))
        try:
            data = sets_store.load_set(context, slug)
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
            saved_slug, _normalized = sets_store.save_set(context, body.get("set"), slug=slug)
        except sets_store.SetValidationError as exc:
            return error_response(400, str(exc))
        return web.json_response(
            {"ok": True, "slug": saved_slug, "sets": sets_store.list_sets(context)}
        )

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
        if not sets_store.delete_set(context, slug):
            return error_response(404, f"no such set {slug!r}")
        return web.json_response({"ok": True, "sets": sets_store.list_sets(context)})
