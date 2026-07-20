"""HTTP routes for ``EPSImageGrid`` (FORMAT.md §6.6): ``POST
/eps_image_grid/clear`` (M1, the Clear button) and ``POST
/eps_image_grid/add`` (M2, the Ctrl+V/paste-to-add backend half — the
frontend uploads the pasted file through core's own ``POST /upload/image``
first, then calls this one with the uuid + that upload's ``{name,
subfolder, type}``).

Registered directly onto ``PromptServer.instance.routes`` — never raw
``app.add_routes`` (invisible to the frontend; see ``lora_library/
routes.py``'s own module docstring for the same finding, verified there
against this pack's rig). Unlike ``lora_library``'s routes, this module
needs no injected context object: ``image_grid_store`` resolves its own
base directory from ``folder_paths`` lazily, so :func:`register` takes no
arguments.

Split the same way ``lora_library/routes.py`` splits ``register``/
``build_routes``: :func:`register_routes` attaches to any
``web.RouteTableDef`` (used by :func:`register` for the live server, and
directly by tests against a throwaway ``aiohttp.web.Application`` — no
ComfyUI needed either way).
"""

from __future__ import annotations

import logging

from aiohttp import web

from . import image_grid_store as store

logger = logging.getLogger("eps_image")


def error_response(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status)


def register_routes(routes: web.RouteTableDef) -> None:
    """Attach the Clear and Add routes to *routes* (FORMAT.md §6.6)."""

    @routes.post("/eps_image_grid/clear")
    async def post_clear(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:  # broad: malformed body is a client error
            return error_response(400, "body must be JSON")
        if not isinstance(body, dict):
            return error_response(400, "body must be a JSON object")

        grid_uuid = body.get("uuid")
        if not store.is_valid_grid_uuid(grid_uuid):
            return error_response(400, f"invalid grid uuid {grid_uuid!r} -- FORMAT.md §6.6")

        cleared = store.clear(grid_uuid)
        return web.json_response({"ok": True, "uuid": grid_uuid, "cleared": cleared})

    @routes.post("/eps_image_grid/add")
    async def post_add(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:  # broad: malformed body is a client error
            return error_response(400, "body must be JSON")
        if not isinstance(body, dict):
            return error_response(400, "body must be a JSON object")

        grid_uuid = body.get("uuid")
        if not store.is_valid_grid_uuid(grid_uuid):
            return error_response(400, f"invalid grid uuid {grid_uuid!r} -- FORMAT.md §6.6")

        filename = body.get("filename")
        if not isinstance(filename, str) or not filename:
            return error_response(400, "missing/invalid 'filename'")
        subfolder = body.get("subfolder", "")
        if not isinstance(subfolder, str):
            return error_response(400, "'subfolder' must be a string")
        source_type = body.get("type", "input")
        if not isinstance(source_type, str) or not source_type:
            return error_response(400, "'type' must be a non-empty string")

        images = store.append_uploaded_image(grid_uuid, filename, subfolder, source_type)
        return web.json_response({"ok": True, "uuid": grid_uuid, "images": images})


def build_routes() -> web.RouteTableDef:
    """A standalone table with just this module's routes — used by tests
    (wrapped in a plain ``aiohttp.web.Application``, no ComfyUI) and,
    indirectly, by :func:`register`."""
    routes = web.RouteTableDef()
    register_routes(routes)
    return routes


def register() -> None:
    """Attach this module's routes to ComfyUI's live server.

    Only function in this module that touches ``PromptServer`` — called
    once from the pack's ``__init__.py`` (mirrors
    ``lora_library.routes.register``).
    """
    from server import PromptServer  # ComfyUI's own module; import only inside ComfyUI

    register_routes(PromptServer.instance.routes)
