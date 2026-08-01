"""HTTP route for ``EPSCheckpointSwitcher``'s panel: ``GET
/eps_ckpt/checkpoints`` -- the live checkpoint filename list, so the panel
can populate its tick-boxes without waiting on (or re-deriving from) a full
``/object_info`` payload.

No loopback gate, unlike ``eps_image/routes_frame_saver.py``'s probe/stream
routes: this returns exactly the same
``folder_paths.get_filename_list("checkpoints")`` list every viewer already
receives, unfiltered, through core's own ``/object_info`` (it backs
``CheckpointLoaderSimple``'s own combo) -- there is no filesystem path or
any other information here that a remote browser couldn't already see by
loading the graph editor.

Registered directly onto ``PromptServer.instance.routes`` -- never raw
``app.add_routes`` (invisible to the frontend; see
``eps_image/routes_image_grid.py``'s own module docstring for the same
finding, verified there against this pack's rig). Split the same way that
module (and ``routes_frame_saver.py``) split ``register``/``build_routes``:
:func:`register_routes` attaches to any ``web.RouteTableDef`` (used by
:func:`register` for the live server, and directly by tests against a
throwaway ``aiohttp.web.Application`` -- no ComfyUI needed either way).
"""

from __future__ import annotations

import logging

from aiohttp import web

logger = logging.getLogger("eps_image")


def register_routes(routes: web.RouteTableDef) -> None:
    """Attach the checkpoint-listing route to *routes*."""

    @routes.get("/eps_ckpt/checkpoints")
    async def get_checkpoints(request: web.Request) -> web.Response:
        import folder_paths  # ComfyUI's own module; only importable inside ComfyUI

        return web.json_response({"checkpoints": folder_paths.get_filename_list("checkpoints")})


def build_routes() -> web.RouteTableDef:
    """A standalone table with just this module's route -- used by tests
    (wrapped in a plain ``aiohttp.web.Application``, no ComfyUI needed) and,
    indirectly, by :func:`register`."""
    routes = web.RouteTableDef()
    register_routes(routes)
    return routes


def register() -> None:
    """Attach this module's route to ComfyUI's live server.

    Only function in this module that touches ``PromptServer`` -- called
    once from the pack's ``__init__.py`` (mirrors
    ``eps_image.routes_image_grid.register``).
    """
    from server import PromptServer  # ComfyUI's own module; import only inside ComfyUI

    register_routes(PromptServer.instance.routes)
