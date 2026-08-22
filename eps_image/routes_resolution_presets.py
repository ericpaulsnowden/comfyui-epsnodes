"""HTTP routes for EPSResolution's server-side size presets -- the shared
STORE (``resolution_presets_store.py``) that lets a size preset travel
across machines exactly like the Prompt Notebook's file and LoRA sets
already do.

Same layering ``lora_library/routes_sets.py`` uses for LoRA sets:
:func:`register` takes the injected ``LibraryContext`` AND a route table
(mirrors that module's own ``register(context, routes)`` name/shape
exactly, so the two read as the identical pattern applied to a second
feature) -- thin aiohttp handlers doing only HTTP-shaped work (status
codes, body parsing, the 5-field ``values`` validation against
``nodes_resolution``'s own widget constraints), delegating persistence to
``resolution_presets_store``.

§2 locality note: like ``routes_sets.py``'s set slugs (never
``routes_notebook.py``'s arbitrary ``file`` paths), these routes have NO
client-supplied path at all -- the presets file always resolves to
``context.library_dir() / resolution_presets_store.PRESETS_FILENAME``, so
there is no "elsewhere" a caller could point it. There is therefore no
``request_is_loopback`` gate anywhere in this file: the file already lives
inside ``library_dir``, which FORMAT.md §2 already lets non-loopback
callers read AND write. Do not add one -- see
``resolution_presets_store.py``'s own docstring for why this feature
deliberately reaches into ``lora_library.context`` in the first place.
"""

from __future__ import annotations

import logging

from aiohttp import web

from . import nodes_resolution
from . import resolution_presets_store as store

logger = logging.getLogger("eps_image")


def error_response(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status)


def _validate_values(raw: object) -> tuple[dict | None, str | None]:
    """The route layer's half of preset validation (see
    ``resolution_presets_store.InvalidPresetValuesError``'s docstring for
    the split): every one of the 5 fields must be present with the right
    JSON type AND satisfy the SAME bounds ``EPSResolution``'s own widgets
    enforce (``nodes_resolution.WIDTH_MIN``/``WIDTH_MAX`` etc.), or be one
    of its own ``RESIZE_METHODS``/``INTERPOLATIONS`` enum values. Returns
    ``(normalized_values, None)`` on success, or ``(None, message)`` naming
    the first problem found.
    """
    if not isinstance(raw, dict):
        return None, "'values' must be an object"

    def _int_field(field: str, minimum: int, maximum: int) -> tuple[int | None, str | None]:
        value = raw.get(field)
        if not isinstance(value, int) or isinstance(value, bool):
            return None, f"'values.{field}' must be an integer"
        if not (minimum <= value <= maximum):
            return None, f"'values.{field}' must be between {minimum} and {maximum}"
        return value, None

    width, err = _int_field("width", nodes_resolution.WIDTH_MIN, nodes_resolution.WIDTH_MAX)
    if err:
        return None, err
    height, err = _int_field("height", nodes_resolution.HEIGHT_MIN, nodes_resolution.HEIGHT_MAX)
    if err:
        return None, err
    multiple_of, err = _int_field(
        "multiple_of", nodes_resolution.MULTIPLE_OF_MIN, nodes_resolution.MULTIPLE_OF_MAX
    )
    if err:
        return None, err

    resize_method = raw.get("resize_method")
    if resize_method not in nodes_resolution.RESIZE_METHODS:
        return None, "'values.resize_method' must be one of " + ", ".join(
            nodes_resolution.RESIZE_METHODS
        )
    interpolation = raw.get("interpolation")
    if interpolation not in nodes_resolution.INTERPOLATIONS:
        return None, "'values.interpolation' must be one of " + ", ".join(
            nodes_resolution.INTERPOLATIONS
        )

    return {
        "width": width,
        "height": height,
        "resize_method": resize_method,
        "interpolation": interpolation,
        "multiple_of": multiple_of,
    }, None


def _base_mtime_from_body(body: dict) -> tuple[float | None, web.Response | None]:
    base_mtime = body.get("base_mtime")
    if base_mtime is not None and (
        isinstance(base_mtime, bool) or not isinstance(base_mtime, (int, float))
    ):
        # bool is an int subclass; `true` would flow into abs(mtime - True)
        # and 409 spuriously (audit 2026-08-21; the store rejects bools too).
        return None, error_response(400, "'base_mtime' must be a number")
    return base_mtime, None


def register(context: store.LibraryContext, routes: web.RouteTableDef) -> None:
    """Attach the three ``/eps_resolution/presets*`` rows to *routes* --
    mirrors ``lora_library.routes_sets.register``'s exact name/shape."""

    @routes.get("/eps_resolution/presets")
    async def get_presets(_request: web.Request) -> web.Response:
        presets, mtime = store.load_presets(context)
        return web.json_response({"presets": presets, "mtime": mtime})

    @routes.post("/eps_resolution/presets/save")
    async def post_presets_save(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:  # broad: malformed body is a client error
            return error_response(400, "body must be JSON")
        if not isinstance(body, dict):
            return error_response(400, "body must be a JSON object")

        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            return error_response(400, "'name' is required")

        values, err = _validate_values(body.get("values"))
        if err:
            return error_response(400, err)

        base_mtime, err_resp = _base_mtime_from_body(body)
        if err_resp:
            return err_resp

        try:
            presets, new_mtime = store.save_preset(context, name, values, base_mtime=base_mtime)
        except store.ConflictError as exc:
            return web.json_response({"error": str(exc), "mtime": exc.current_mtime}, status=409)
        except store.PresetStoreError as exc:
            return error_response(400, str(exc))

        return web.json_response({"ok": True, "presets": presets, "mtime": new_mtime})

    @routes.post("/eps_resolution/presets/delete")
    async def post_presets_delete(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:  # broad: malformed body is a client error
            return error_response(400, "body must be JSON")
        if not isinstance(body, dict):
            return error_response(400, "body must be a JSON object")

        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            return error_response(400, "'name' is required")

        base_mtime, err_resp = _base_mtime_from_body(body)
        if err_resp:
            return err_resp

        try:
            presets, new_mtime = store.delete_preset(context, name, base_mtime=base_mtime)
        except store.ConflictError as exc:
            return web.json_response({"error": str(exc), "mtime": exc.current_mtime}, status=409)
        except store.PresetNotFoundError as exc:
            return error_response(404, str(exc))
        except store.PresetStoreError as exc:
            return error_response(400, str(exc))

        return web.json_response({"ok": True, "presets": presets, "mtime": new_mtime})


def build_routes(context: store.LibraryContext) -> web.RouteTableDef:
    """A standalone table with just this module's routes, bound to
    *context* -- the tests' entry point (wrapped in a plain
    ``aiohttp.web.Application``, no ComfyUI needed), mirroring
    ``lora_library/routes.py``'s own ``build_routes(context)`` shape.
    Unlike eps_image's context-FREE ``build_routes()``s (image_grid/
    frame_saver/checkpoint_switcher), this one takes a context -- there is
    no eps_image-wide route aggregator to borrow one from (module
    docstring).
    """
    routes = web.RouteTableDef()
    register(context, routes)
    return routes


def register_live(context: store.LibraryContext) -> None:
    """Attach this module's routes to ComfyUI's live server -- the OUTER,
    PromptServer-touching entry point ``__init__.py`` calls.

    Named differently from :func:`register` above purely to avoid a
    same-module name collision with the context+routes INNER function
    (contrast ``lora_library/routes.py``'s own one-arg
    ``register(context)``, which can reuse that bare name only because it
    lives in a DIFFERENT module from ``routes_sets.register(context,
    routes)``).
    """
    from server import PromptServer  # ComfyUI's own module; import only inside ComfyUI

    register(context, PromptServer.instance.routes)
