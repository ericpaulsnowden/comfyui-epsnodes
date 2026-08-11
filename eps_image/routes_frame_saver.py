"""HTTP routes for ``EPSFrameSaver`` (FORMAT.md §6.7): a metadata probe and a
byte-range-capable file stream, both feeding ``web/eps_image/frame_saver.js``'s
player.

Both routes are **loopback-only**, mirroring ``lora_library/routes.py``'s
``request_is_loopback`` guard (FORMAT.md §2's rule) — reimplemented locally
rather than imported, since ``eps_image/`` is a sibling FEATURE FAMILY
(FORMAT.md's naming note: "future non-lora features arrive as sibling
modules ... without repo churn") that must not reach into ``lora_library/``'s
internals; ``eps_image/routes_image_grid.py`` makes the identical choice to
stay self-contained. Self-containment cuts both ways: this copy MISSED the
canonical guard's 2026-07-30 same-machine-via-LAN fix until 2026-08-02, so
when the canonical ``request_is_loopback``/``_machine_owns_address`` pair
changes, change this one too. Unlike the NOTEBOOK routes'
``notebook_path_error`` carve-out (``lora_library/routes.py``: a remote
caller may still touch paths inside ``library_dir``/``remote_dirs``),
there is no carve-out here: a video's path is arbitrary filesystem, never
confined to one shared folder, so FORMAT.md §6.7 is unconditionally
loopback-only for BOTH routes -- NOT VHS's permissive default. (Do not
"restore" one when porting guard changes: ``/lora_library/fs/list`` itself
has no carve-out either -- it is unconditionally loopback-gated via
``FS_LIST_LOCAL_ONLY``.)

Registered directly onto ``PromptServer.instance.routes`` -- never raw
``app.add_routes`` (invisible to the frontend; the same finding
``lora_library/routes.py`` and ``eps_image/routes_image_grid.py`` document in
their own module docstrings). Split the same way those two split
``register``/``build_routes``: :func:`register_routes` attaches to any
``web.RouteTableDef`` (used by :func:`register` for the live server, and
directly by tests against a throwaway ``aiohttp.web.Application`` -- no
ComfyUI needed either way).
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from pathlib import Path
from typing import Any

from aiohttp import web

from . import frame_saver_video as video

logger = logging.getLogger("eps_image")

#: FORMAT.md §6.7's video ext allowlist. `web/eps_image/frame_saver.js`'s
#: Browse picker passes this SAME list (as a literal -- JS has no way to
#: import a Python module) to `/lora_library/fs/list`'s `ext` query param, so
#: a file the picker lets you choose is always one these routes will accept.
VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".ogv")


def request_is_loopback(request: web.Request) -> bool:
    """True when *request* comes from this machine (FORMAT.md §2's rule,
    reimplemented here -- see module docstring for why not imported from
    ``lora_library.routes``, which holds the CANONICAL copy of this pair of
    functions; keep them in lockstep).

    A forwarded request (``X-Forwarded-For``) is never loopback -- the proxy
    hop hides the real origin, so it gets the restricted tier. ``remote``
    being absent (unix sockets, aiohttp test clients) counts as loopback:
    both mean "not a foreign machine".

    **Same-machine-via-LAN-address counts as local** (the canonical copy's
    2026-07-30 fix, back-ported 2026-08-02 -- this copy predated it and
    wrongly 403'd the host): a literal ``is_loopback`` check classifies a
    browser on the HOST machine that reaches ComfyUI via the machine's own
    LAN address (``--listen`` + ``http://192.168.x.x:8188``) as REMOTE --
    breaking Frame Saver's probe/stream player on the very machine the
    video sits on. A hostname can't answer "is this browser on my machine"
    (the same URL is used from both machines); the OS can: a process can
    only ``bind()`` a socket to an address this machine owns, so a
    throwaway UDP bind to the PEER address is a deterministic ownership
    test. Loopback stays the fast path; forwarded requests stay remote
    (the peer is the proxy, not the client).
    """
    if "X-Forwarded-For" in request.headers:
        return False
    remote = request.remote
    if remote is None:
        return True
    try:
        if ipaddress.ip_address(remote.partition("%")[0]).is_loopback:
            return True
    except ValueError:
        return False
    return _machine_owns_address(remote)


def _machine_owns_address(address: str) -> bool:
    """Throwaway UDP bind test: True only if this machine owns *address*.

    Ported verbatim from ``lora_library/routes.py``'s ``_machine_owns_address``
    (the canonical copy; itself cpsb/locality.py's ``_can_bind``, adapted):
    binding to any address the machine does not own fails with ``OSError`` on
    every platform (Windows surfaces ``WSAEADDRNOTAVAIL`` as a plain
    ``OSError`` too, so no branching). An IPv6 zone id (``fe80::1%en0``) is
    passed as the scoped 4-tuple's ``scope_id`` -- folded into the address
    string it is rejected. IPv4-mapped IPv6 peers (``::ffff:192.0.2.7``, from
    a dual-stack socket) are reduced to plain IPv4 first so the bind uses the
    right family.
    """
    lowered = address.lower()
    if lowered.startswith("::ffff:") and "." in address:
        address = address[7:]
    bare, _, zone = address.partition("%")
    family = socket.AF_INET6 if ":" in bare else socket.AF_INET
    scope_id = 0
    if zone:
        try:
            scope_id = int(zone)
        except ValueError:
            try:
                scope_id = socket.if_nametoindex(zone)
            except OSError:
                return False
    sock_address = (bare, 0, 0, scope_id) if family == socket.AF_INET6 else (bare, 0)
    sock = socket.socket(family, socket.SOCK_DGRAM)
    try:
        sock.bind(sock_address)
    except OSError:
        return False
    else:
        return True
    finally:
        sock.close()


def error_response(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status)


def _validate_video_path(raw: str) -> tuple[Path | None, str | None]:
    """FORMAT.md §6.7's shared path validation for both routes below.

    Returns ``(resolved_path, None)`` on success, or ``(None, message)`` on
    failure -- NEVER raises, so a handler can turn *message* straight into a
    400 without a try/except of its own (FORMAT.md §6.7 "never 500 on bad
    input").

    Requires an ABSOLUTE path: a relative one is ambiguous (it would resolve
    against the SERVER PROCESS's own cwd, not anything the caller can see),
    so it's rejected outright rather than guessed at. ``Path.resolve()``
    normalizes any ``.``/``..`` segments BEFORE the extension check and
    before anything ever touches the filesystem, so what gets validated is
    exactly what gets opened -- no traversal-by-encoding trick can present a
    resolved path other than its own true target. The extension allowlist
    (:data:`VIDEO_EXTENSIONS`) is the second half of "path-validated" per
    FORMAT.md §6.7.

    ``Path.resolve()``/``Path.is_absolute()`` can themselves raise for a
    sufficiently hostile *raw* (confirmed live: an embedded NUL byte makes
    ``resolve()`` raise a bare ``ValueError`` from the underlying ``lstat``
    call, an absolute-looking string a caller could still send even though
    it can never name a real file) -- caught here and turned into the same
    400-worthy message shape as every other rejection, rather than
    propagating into a 500.
    """
    trimmed = (raw or "").strip()
    if not trimmed:
        return None, "missing 'path' query parameter"
    try:
        path = Path(trimmed)
        if not path.is_absolute():
            return None, f"path must be an absolute path (got {trimmed!r})"
        resolved = path.resolve()
    except (OSError, ValueError) as exc:
        return None, f"invalid path ({exc})"
    if resolved.suffix.lower() not in VIDEO_EXTENSIONS:
        allowed = ", ".join(VIDEO_EXTENSIONS)
        return None, f"unsupported video extension {resolved.suffix!r} (allowed: {allowed})"
    return resolved, None


#: Test seam for :func:`_resolve_input_ref` -- tests set a fake module here
#: (`exists_annotated_filepath` + `get_annotated_filepath`) since ComfyUI's
#: real `folder_paths` is absent under pytest.
_FOLDER_PATHS_OVERRIDE: Any = None


def _resolve_input_ref(ref: str) -> tuple[Path | None, str | None]:
    """FORMAT.md §6.7 v0.60.0 `input_ref` mode: resolve an ANNOTATED input
    filename (the exact value core `LoadVideo`'s `file` combo holds) with
    the exact validation core applies to it -- `exists_annotated_filepath`
    then `get_annotated_filepath` -- plus this module's own extension
    allowlist. Returns ``(resolved, None)`` or ``(None, message)``.

    Requires ComfyUI's `folder_paths` (lazy import, injectable for tests via
    :data:`_FOLDER_PATHS_OVERRIDE`); a non-ComfyUI process gets a clean 400,
    never a crash.
    """
    trimmed = (ref or "").strip()
    if not trimmed:
        return None, "missing 'input_ref' query parameter"
    folder_paths = _FOLDER_PATHS_OVERRIDE
    if folder_paths is None:
        try:
            import folder_paths  # type: ignore[no-redef]  # ComfyUI's own module
        except ImportError:
            return None, "input_ref resolution requires a running ComfyUI"
    try:
        if not folder_paths.exists_annotated_filepath(trimmed):
            return None, f"no such input video: {trimmed!r}"
        resolved = Path(folder_paths.get_annotated_filepath(trimmed)).resolve()
    except (OSError, ValueError) as exc:
        return None, f"invalid input_ref ({exc})"
    if resolved.suffix.lower() not in VIDEO_EXTENSIONS:
        allowed = ", ".join(VIDEO_EXTENSIONS)
        return None, f"unsupported video extension {resolved.suffix!r} (allowed: {allowed})"
    if not resolved.is_file():
        return None, f"not a file: {resolved}"
    return resolved, None


def _resolve_request_source(request: web.Request) -> tuple[Path | None, str | None, bool]:
    """Shared param handling for both routes: exactly one of `path` (the
    original mode) or `input_ref` (v0.60.0). Returns
    ``(resolved, error, needs_loopback)`` -- `path` mode keeps §6.7's
    unconditional loopback gate; `input_ref` mode is deliberately ungated
    (an input-dir file is already exposed to every viewer by core's own
    `/view`, so §2's arbitrary-file-read rationale does not apply)."""
    raw_path = request.query.get("path", "")
    raw_ref = request.query.get("input_ref", "")
    if raw_path and raw_ref:
        return None, "pass either 'path' or 'input_ref', not both", False
    if raw_ref:
        resolved, error = _resolve_input_ref(raw_ref)
        return resolved, error, False
    resolved, error = _validate_video_path(raw_path)
    if error is None and not resolved.is_file():
        return None, f"not a file: {resolved}", True
    return resolved, error, True


def register_routes(routes: web.RouteTableDef) -> None:
    """Attach the probe + stream routes to *routes* (FORMAT.md §6.7)."""

    @routes.get("/eps_frame_saver/probe")
    async def get_probe(request: web.Request) -> web.Response:
        resolved, error, needs_loopback = _resolve_request_source(request)
        if needs_loopback and not request_is_loopback(request):
            return error_response(
                403,
                "reading a video file only works in a browser on the machine "
                "ComfyUI runs on",
            )
        if error is not None:
            return error_response(400, error)
        try:
            info = video.probe(str(resolved))
        except ValueError as exc:
            # video.probe() wraps every av/ffmpeg failure into a ValueError
            # naming the path (module docstring) -- a bad/unreadable/
            # codec-less file is always a clean 400 here, never a 500.
            return error_response(400, str(exc))
        return web.json_response(info)

    @routes.get("/eps_frame_saver/stream")
    async def get_stream(request: web.Request) -> web.Response:
        resolved, error, needs_loopback = _resolve_request_source(request)
        if needs_loopback and not request_is_loopback(request):
            return error_response(
                403,
                "the video preview only works in a browser on the machine "
                "ComfyUI runs on",
            )
        if error is not None:
            return error_response(400, error)
        # aiohttp's FileResponse handles Range/If-Modified-Since/ETag itself
        # -- this is what gives the frontend's <video> element real 206 seek
        # support for free (FORMAT.md §6.7), no custom byte-range code here.
        return web.FileResponse(resolved)


def build_routes() -> web.RouteTableDef:
    """A standalone table with just this module's routes -- used by tests
    (wrapped in a plain ``aiohttp.web.Application``, no ComfyUI needed) and,
    indirectly, by :func:`register`."""
    routes = web.RouteTableDef()
    register_routes(routes)
    return routes


def register() -> None:
    """Attach this module's routes to ComfyUI's live server.

    Only function in this module that touches ``PromptServer`` -- called
    once from the pack's ``__init__.py`` (mirrors
    ``eps_image.routes_image_grid.register``).
    """
    from server import PromptServer  # ComfyUI's own module; import only inside ComfyUI

    register_routes(PromptServer.instance.routes)
