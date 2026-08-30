"""``GET /eps/audit`` — the same report as JSON, without queueing a graph.

**Loopback only**, unlike this pack's other read routes. Those hand out node
metadata or thumbnails; this one hands out an inventory of the server's own
attack surface — installed packs, every registered endpoint, and a list of
source lines worth attacking. That is precisely the reconnaissance an
attacker on the LAN would want, so it is gated to the machine itself using
the same ``request_is_loopback`` helper (and therefore the same
same-machine-via-LAN-address rule) as the §2 local-only routes.

Registered through the ``register_routes``/``build_routes``/``register``
split that ``routes_list_flags.py`` established, so the tests drive it in a
plain aiohttp app with no ComfyUI present.
"""

from __future__ import annotations

import asyncio
import logging

from aiohttp import web

from .audit import SCOPE_EVERYTHING, SCOPES, run_audit

logger = logging.getLogger("eps_audit")

ROUTE = "/eps/audit"


def register_routes(routes) -> None:
    @routes.get(ROUTE)
    async def get_audit(request: web.Request) -> web.Response:
        # Two-branch import, this pack's established cross-family idiom
        # (nodes_save_image._lora_library_modules documents it): package
        # context inside ComfyUI, flat when tooling loads the family alone.
        try:
            from ..lora_library.routes import error_response, request_is_loopback
        except ImportError:
            from lora_library.routes import error_response, request_is_loopback

        if not request_is_loopback(request):
            return error_response(403, "the audit report is local-only")

        scope = request.query.get("scope") or SCOPE_EVERYTHING
        if scope not in SCOPES:
            return error_response(400, f"unknown scope {scope!r}; expected one of {SCOPES}")
        include_this_pack = request.query.get("include_this_pack", "1") not in ("0", "false", "no")

        # The scan walks every installed pack's source — real synchronous
        # work, off the event loop like every other non-trivial pass in this
        # pack's routes (routes_list_flags' own cache-miss path).
        report = await asyncio.to_thread(run_audit, scope, include_this_pack)
        return web.json_response(report)


def build_routes() -> web.RouteTableDef:
    routes = web.RouteTableDef()
    register_routes(routes)
    return routes


def register() -> None:
    from server import PromptServer  # ComfyUI's own module

    register_routes(PromptServer.instance.routes)
    logger.info("EPSNodes: registered %s (local-only)", ROUTE)
