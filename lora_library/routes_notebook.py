"""HTTP routes for the LoRA notebook — the five ``/lora_library/notebook*``
rows of FORMAT.md §5.

Mirrors ``routes_sets.py``'s shape: thin aiohttp handlers doing only
HTTP-shaped work (status codes, body/query parsing, the §2 path guard),
delegating everything else to ``markdown_store``. Unlike a set slug, a
notebook ``file`` value is an arbitrary path the caller chooses (FORMAT.md
§1/§2) — so, unlike ``routes_sets.py``, every handler here resolves that
path and runs it through ``notebook_path_error`` before touching disk.
"""

from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web

from . import markdown_store
from .context import LibraryContext
from .routes import error_response, notebook_path_error, request_is_loopback

logger = logging.getLogger("lora_library")


def _resolve_path(
    context: LibraryContext, file_value: object
) -> tuple[Path | None, web.Response | None]:
    """``(path, None)`` on success, or ``(None, error_response)`` for a
    non-string *file_value* or one Path can't make sense of (e.g. an
    embedded NUL) — kept out of every handler below."""
    if file_value is not None and not isinstance(file_value, str):
        return None, error_response(400, "'file' must be a string")
    try:
        return context.resolve_notebook_file(file_value or ""), None
    except (OSError, ValueError) as exc:
        return None, error_response(400, f"invalid 'file' path: {exc}")


def register(context: LibraryContext, routes: web.RouteTableDef) -> None:
    """Attach the §5 notebook rows to *routes*."""

    @routes.get("/lora_library/notebook")
    async def get_notebook(request: web.Request) -> web.Response:
        path, err = _resolve_path(context, request.query.get("file", ""))
        if err is not None:
            return err
        guard = notebook_path_error(
            context, path, loopback=request_is_loopback(request), writing=False
        )
        if guard:
            return error_response(403, guard)

        parsed, mtime, _line_ending = markdown_store.load_notebook(path)
        return web.json_response(
            {
                "file": str(path),
                "exists": mtime is not None,
                "mtime": mtime,
                "entries": markdown_store.list_entries(parsed),
                "problems": parsed.problems,
            }
        )

    @routes.get("/lora_library/notebook/entry")
    async def get_notebook_entry(request: web.Request) -> web.Response:
        path, err = _resolve_path(context, request.query.get("file", ""))
        if err is not None:
            return err
        guard = notebook_path_error(
            context, path, loopback=request_is_loopback(request), writing=False
        )
        if guard:
            return error_response(403, guard)

        name = request.query.get("name", "")
        parsed, mtime, _line_ending = markdown_store.load_notebook(path)
        entry = markdown_store.get_entry(parsed, name)
        if entry is None:
            return error_response(404, f"no such entry {name!r} in {path}")
        return web.json_response({**entry, "mtime": mtime})

    @routes.post("/lora_library/notebook/entry")
    async def post_notebook_entry(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:  # broad: malformed body is a client error
            return error_response(400, "body must be JSON")
        if not isinstance(body, dict):
            return error_response(400, "body must be a JSON object")

        path, err = _resolve_path(context, body.get("file"))
        if err is not None:
            return err
        guard = notebook_path_error(
            context, path, loopback=request_is_loopback(request), writing=True
        )
        if guard:
            return error_response(403, guard)

        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            return error_response(400, "'name' is required")
        text = body.get("text")
        if not isinstance(text, str):
            return error_response(400, "'text' must be a string")
        category = body.get("category")
        if category is not None and not isinstance(category, str):
            return error_response(400, "'category' must be a string")
        rename_to = body.get("rename_to")
        if rename_to is not None and not isinstance(rename_to, str):
            return error_response(400, "'rename_to' must be a string")
        base_mtime = body.get("base_mtime")
        if base_mtime is not None and not isinstance(base_mtime, (int, float)):
            return error_response(400, "'base_mtime' must be a number")

        parsed, current_mtime, line_ending = markdown_store.load_notebook(path)
        try:
            markdown_store.check_conflict(base_mtime, current_mtime)
        except markdown_store.ConflictError as exc:
            return web.json_response({"error": str(exc), "mtime": exc.current_mtime}, status=409)

        try:
            markdown_store.upsert_entry(
                parsed, name, text, category=category, rename_to=rename_to
            )
        except markdown_store.MarkdownStoreError as exc:
            return error_response(400, str(exc))

        new_mtime = markdown_store.save_notebook(path, parsed, line_ending)
        return web.json_response(
            {"ok": True, "mtime": new_mtime, "entries": markdown_store.list_entries(parsed)}
        )

    @routes.post("/lora_library/notebook/delete")
    async def post_notebook_delete(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:  # broad: malformed body is a client error
            return error_response(400, "body must be JSON")
        if not isinstance(body, dict):
            return error_response(400, "body must be a JSON object")

        path, err = _resolve_path(context, body.get("file"))
        if err is not None:
            return err
        guard = notebook_path_error(
            context, path, loopback=request_is_loopback(request), writing=True
        )
        if guard:
            return error_response(403, guard)

        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            return error_response(400, "'name' is required")
        base_mtime = body.get("base_mtime")
        if base_mtime is not None and not isinstance(base_mtime, (int, float)):
            return error_response(400, "'base_mtime' must be a number")

        parsed, current_mtime, line_ending = markdown_store.load_notebook(path)
        try:
            markdown_store.check_conflict(base_mtime, current_mtime)
        except markdown_store.ConflictError as exc:
            return web.json_response({"error": str(exc), "mtime": exc.current_mtime}, status=409)

        if not markdown_store.remove_entry(parsed, name):
            return error_response(404, f"no such entry {name!r} in {path}")

        new_mtime = markdown_store.save_notebook(path, parsed, line_ending)
        return web.json_response(
            {"ok": True, "mtime": new_mtime, "entries": markdown_store.list_entries(parsed)}
        )

    @routes.post("/lora_library/notebook/move")
    async def post_notebook_move(request: web.Request) -> web.Response:
        """FORMAT.md §5's move row / §3.4 Move — exactly one of ``before``/
        ``category`` (else 400); unknown ``name``/``before`` is 404; §3.5
        conflicts are 409. Same shape as ``post_notebook_entry`` above."""
        try:
            body = await request.json()
        except Exception:  # broad: malformed body is a client error
            return error_response(400, "body must be JSON")
        if not isinstance(body, dict):
            return error_response(400, "body must be a JSON object")

        path, err = _resolve_path(context, body.get("file"))
        if err is not None:
            return err
        guard = notebook_path_error(
            context, path, loopback=request_is_loopback(request), writing=True
        )
        if guard:
            return error_response(403, guard)

        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            return error_response(400, "'name' is required")
        before = body.get("before")
        if before is not None and not isinstance(before, str):
            return error_response(400, "'before' must be a string")
        category = body.get("category")
        if category is not None and not isinstance(category, str):
            return error_response(400, "'category' must be a string")
        if (before is None) == (category is None):
            return error_response(400, "exactly one of 'before' or 'category' is required")
        base_mtime = body.get("base_mtime")
        if base_mtime is not None and not isinstance(base_mtime, (int, float)):
            return error_response(400, "'base_mtime' must be a number")

        parsed, current_mtime, line_ending = markdown_store.load_notebook(path)
        try:
            markdown_store.check_conflict(base_mtime, current_mtime)
        except markdown_store.ConflictError as exc:
            return web.json_response({"error": str(exc), "mtime": exc.current_mtime}, status=409)

        try:
            markdown_store.move_entry(parsed, name, before=before, category=category)
        except markdown_store.EntryNotFoundError as exc:
            return error_response(404, str(exc))

        new_mtime = markdown_store.save_notebook(path, parsed, line_ending)
        return web.json_response(
            {"ok": True, "mtime": new_mtime, "entries": markdown_store.list_entries(parsed)}
        )
