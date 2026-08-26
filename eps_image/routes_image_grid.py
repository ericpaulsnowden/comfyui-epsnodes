"""HTTP routes for ``EPSImageGrid`` (FORMAT.md §6.6): ``POST
/eps_image_grid/clear`` (M1, the Clear button), ``POST
/eps_image_grid/add`` (M2, the Ctrl+V/paste-to-add backend half — the
frontend uploads the pasted file through core's own ``POST /upload/image``
first, then calls this one with the uuid + that upload's ``{name,
subfolder, type}``), and two more added for the 2026-07-20 owner-reported
bug fixes:

- ``GET /eps_image_grid/list`` — the whole buffer's refs for a uuid, so the
  frontend can populate ``node.imgs`` on attach/reload/undo WITHOUT a Run
  (FORMAT.md §6.6 "Display reflects the buffer on LOAD").
- ``POST /eps_image_grid/clone`` — copies one uuid's buffer into another's,
  so an in-graph duplicate (paste-collision) keeps its own copy of the
  original's images instead of starting empty (FORMAT.md §6.6 "Copy carries
  the images, independently").

**2026-08-21 perf round** (owner: "big performance issues" on large
buffers; see ``image_grid_store.py``'s dated docstring section):

- every ref-returning route here (``/list``, ``/add``, ``/remove``,
  ``/clone``) now decorates its refs with a per-frame ``"mtime"``
  (``store.with_frame_mtimes`` -- that frame FILE's mtime, int ms), the
  stable per-image cache key the frontend threads into thumbnail URLs
  instead of the buffer-wide ``generation`` (which moved on every append
  and re-downloaded every UNCHANGED frame after every run). ``generation``
  still rides ``/list``/``/add``/``/remove`` unchanged -- it is the fallback
  key for refs that carry no mtime.
- ``GET /eps_image_grid/frame?uuid=&filename=[&preview=...][&v=...]`` —
  serves ONE manifest-listed frame: with ``preview`` (any value; the
  frontend sends core's own ``webp;80`` spelling) a genuinely DOWNSCALED
  disk-cached webp thumbnail (``store.thumbnail_path``, built off the event
  loop via ``asyncio.to_thread``); without it the full PNG frame. The
  ``preview`` toggle deliberately mirrors core's ``/view``: core's own
  Open/Save/Copy Image menu items take ``img.src``, delete ``preview`` and
  re-fetch, so they land on the full frame. ``Cache-Control`` is
  ``immutable`` when the URL carries a ``v`` key, ``no-cache`` (ETag
  revalidation, ``FileResponse``) otherwise. 400 bad uuid / missing
  filename; 404 for anything ``store.frame_path`` refuses (unlisted name,
  traversal, missing file) or a thumbnail PIL can't build -- the frontend
  degrades that one image to core's ``/view?preview=``.

**2026-08-26 while-running round** (mid-run responsiveness audit -- server
is GIL-busy mid-run, and this module's handlers were competing with it on
the loop thread): every store call in this file that touches the
filesystem now runs through ``asyncio.to_thread`` -- ``append_uploaded_image``
(``/add``, a PIL decode/convert/re-encode + atomic write, previously
synchronous even though the bulk-add flow can call it 100+ times in a
row), ``clear`` (``/clear``, ``shutil.rmtree``), ``remove_frame``
(``/remove``), ``clone_buffer`` (``/clone``), and ``with_frame_mtimes``
(every ref-returning route -- see its own per-uuid cache in
``image_grid_store.py``'s dated docstring section, keyed on
``buffer_generation``, so an unchanged buffer skips the underlying stat
loop even before the ``to_thread`` hop). ``GET /eps_image_grid/frame``'s
``preview`` branch (above) was already off-loop before this round; its
OTHER branch -- ``store.frame_path``, the full-PNG path -- was not, despite
this docstring's wording not calling that gap out explicitly. Both
branches now match.

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

import asyncio
import logging

from aiohttp import web

from . import image_grid_store as store

logger = logging.getLogger("eps_image")


def error_response(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status)


def _bad_grid_id(value: object) -> str:
    """The user-facing text for a missing/malformed grid id.

    Rendered verbatim in a ComfyUI toast by ``web/eps_image/image_grid.js``,
    so it names what the person can DO about it -- no internal section
    references (the FORMAT.md §6.6 contract this implements is cited in the
    module docstring instead, where developers read it).
    """
    return (
        f"invalid image buffer id {value!r} -- this EPS Image Grid node lost "
        "its buffer id; reload the workflow, then try again"
    )


#: ``GET /eps_image_grid/frame`` cache policy (2026-08-21): a URL that
#: carries the frontend's ``v=`` key (a frame's own mtime, or the buffer
#: generation) names ONE exact set of bytes -- a rewritten frame gets a new
#: key, never a new body under the old URL -- so a year + ``immutable`` is
#: right and is what makes unchanged frames free after the first view. A
#: keyless URL (core's Open/Save Image strip only ``preview``, keeping
#: ``v``; a hand-typed address) must revalidate: ``FileResponse`` emits
#: ``ETag``/``Last-Modified`` and answers ``If-None-Match`` with 304.
_CACHE_KEYED = "public, max-age=31536000, immutable"
_CACHE_UNKEYED = "no-cache"


def _cache_control_for(request: web.Request) -> str:
    return _CACHE_KEYED if "v" in request.query else _CACHE_UNKEYED


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
            return error_response(400, _bad_grid_id(grid_uuid))

        # 2026-08-26 while-running round: shutil.rmtree on a large buffer is
        # real filesystem work -- off the loop like every other write here.
        cleared = await asyncio.to_thread(store.clear, grid_uuid)
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
            return error_response(400, _bad_grid_id(grid_uuid))

        filename = body.get("filename")
        if not isinstance(filename, str) or not filename:
            return error_response(400, "missing/invalid 'filename'")
        subfolder = body.get("subfolder", "")
        if not isinstance(subfolder, str):
            return error_response(400, "'subfolder' must be a string")
        source_type = body.get("type", "input")
        if not isinstance(source_type, str) or not source_type:
            return error_response(400, "'type' must be a non-empty string")

        # 2026-08-26 while-running round: append_uploaded_image does a PIL
        # decode -> convert -> PNG re-encode -> atomic write, all synchronous
        # -- and the bulk-add flow calls this route up to 100+ times in a
        # row. Off the loop, like every other filesystem/CPU-bound store
        # call in this module.
        images = await asyncio.to_thread(
            store.append_uploaded_image, grid_uuid, filename, subfolder, source_type
        )
        return web.json_response(
            {
                "ok": True,
                "uuid": grid_uuid,
                # Per-frame `mtime` on every ref (2026-08-21 perf round) --
                # the frontend's stable per-image cache key; see
                # store.with_frame_mtimes. Off the loop too (2026-08-26):
                # a cache hit is cheap, but the miss path is the same O(N)
                # stat loop as before.
                "images": await asyncio.to_thread(store.with_frame_mtimes, grid_uuid, images),
                # Cache token for the buffer's contents -- see
                # store.buffer_generation's docstring (2026-07-29 bulk-add).
                "generation": store.buffer_generation(grid_uuid),
            }
        )

    @routes.post("/eps_image_grid/remove")
    async def post_remove(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:  # broad: malformed body is a client error
            return error_response(400, "body must be JSON")
        if not isinstance(body, dict):
            return error_response(400, "body must be a JSON object")

        grid_uuid = body.get("uuid")
        if not store.is_valid_grid_uuid(grid_uuid):
            return error_response(400, _bad_grid_id(grid_uuid))

        filename = body.get("filename")
        if not isinstance(filename, str) or not filename:
            return error_response(400, "missing/invalid 'filename'")

        # 2026-08-26 while-running round: a manifest rewrite + file unlink,
        # off the loop like every other write in this module.
        images = await asyncio.to_thread(store.remove_frame, grid_uuid, filename)
        return web.json_response(
            {
                "ok": True,
                "uuid": grid_uuid,
                "images": await asyncio.to_thread(store.with_frame_mtimes, grid_uuid, images),
                "generation": store.buffer_generation(grid_uuid),
            }
        )

    @routes.get("/eps_image_grid/list")
    async def get_list(request: web.Request) -> web.Response:
        grid_uuid = request.query.get("uuid")
        if not store.is_valid_grid_uuid(grid_uuid):
            return error_response(400, _bad_grid_id(grid_uuid))

        refs = store.list_refs(grid_uuid)
        return web.json_response(
            {
                "ok": True,
                "uuid": grid_uuid,
                # 2026-08-26 while-running round: the panel calls this once
                # per FINISHED RUN during a sweep -- off the loop, see
                # store.with_frame_mtimes's own per-uuid cache (a hit is
                # cheap; the miss path is still the O(N) stat loop).
                "refs": await asyncio.to_thread(store.with_frame_mtimes, grid_uuid, refs),
                "generation": store.buffer_generation(grid_uuid),
            }
        )

    @routes.get("/eps_image_grid/frame")
    async def get_frame(request: web.Request) -> web.StreamResponse:
        """One manifest-listed frame (2026-08-21 perf round): the downscaled
        cached thumbnail with ``preview``, the full PNG without -- see the
        module docstring. Validation is ``store.frame_path``'s (uuid regex,
        bare name, manifest-listed, exists); this handler never builds a
        path of its own."""
        grid_uuid = request.query.get("uuid")
        if not store.is_valid_grid_uuid(grid_uuid):
            return error_response(400, _bad_grid_id(grid_uuid))
        filename = request.query.get("filename")
        if not isinstance(filename, str) or not filename:
            return error_response(400, "missing/invalid 'filename'")

        if "preview" in request.query:
            # PIL decode + resize + encode on a cache miss: off the event
            # loop. A hit is two stats, also fine in a thread.
            path = await asyncio.to_thread(store.thumbnail_path, grid_uuid, filename)
            content_type = "image/webp"
        else:
            # 2026-08-26 while-running round: this branch used to run
            # store.frame_path synchronously on the loop even though its
            # `preview` sibling above was already offloaded -- frame_path
            # loads the manifest (a real file read) on every call, same as
            # thumbnail_path's own cache-hit path, so there was no reason
            # for the two branches to be treated differently.
            path = await asyncio.to_thread(store.frame_path, grid_uuid, filename)
            content_type = "image/png"
        if path is None:
            return error_response(404, "no such frame in this image buffer")
        headers = {
            "Content-Type": content_type,
            "Cache-Control": _cache_control_for(request),
            "X-Content-Type-Options": "nosniff",
        }
        return web.FileResponse(path, headers=headers)

    @routes.post("/eps_image_grid/clone")
    async def post_clone(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:  # broad: malformed body is a client error
            return error_response(400, "body must be JSON")
        if not isinstance(body, dict):
            return error_response(400, "body must be a JSON object")

        src_uuid = body.get("from")
        if not store.is_valid_grid_uuid(src_uuid):
            return error_response(400, _bad_grid_id(src_uuid))
        dst_uuid = body.get("to")
        if not store.is_valid_grid_uuid(dst_uuid):
            return error_response(400, _bad_grid_id(dst_uuid))

        # 2026-08-26 while-running round: O(N) file copies -- off the loop,
        # like every other write in this module.
        refs = await asyncio.to_thread(store.clone_buffer, src_uuid, dst_uuid)
        decorated = await asyncio.to_thread(store.with_frame_mtimes, dst_uuid, refs)
        return web.json_response({"ok": True, "refs": decorated})


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
