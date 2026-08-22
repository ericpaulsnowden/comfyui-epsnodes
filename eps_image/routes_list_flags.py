"""``GET /eps/list_flags`` -- every loaded node class's list flags, for the
EPS Run Multiplier's pre-queue run-count estimator (FORMAT.md §6.10).

Owner ask 2026-08-21: "I'm passing multiple models through a
ComfyUI-Krea2T-Enhancer node before sending them to a multiplier. Is there
a way to get the multiplier to be able to read the number of models?" The
estimator can count any node whose LIST semantics it knows -- and
ComfyUI's own rule is uniform: a class WITHOUT ``INPUT_IS_LIST`` is mapped
over its list inputs (one execution per element of the longest, the rest
broadcast) and each execution emits ONE element per non-``OUTPUT_IS_LIST``
output, so its output length is simply its longest list input's. The
frontend can already see ``output_is_list`` (core's ``/object_info``
exposes it) but NOT ``INPUT_IS_LIST`` -- a flattener (``INPUT_IS_LIST``
true, plain outputs) receives the whole list and emits exactly one, and
guessing wrong would overclaim. This route closes that gap with the
server's own ground truth: ``{"classes": {"<ClassName>": {"input_is_list":
bool, "output_is_list": [bool, ...]}}}`` over ``nodes.NODE_CLASS_MAPPINGS``,
read with ``getattr`` defaults exactly the way ``execution.py`` reads them.

No loopback gate: this is class METADATA every viewer already receives in
bulk through ``/object_info`` (plus one boolean per class that file omits)
-- no paths, no filesystem, nothing a remote graph-editor tab couldn't
already infer. A broken third-party class never breaks the route: per-class
failures are skipped (logged at DEBUG), the rest still count.

Registered onto ``PromptServer.instance.routes`` through the same
``register``/``build_routes``/``register_routes`` split as
``routes_checkpoint_switcher.py`` (tests wrap ``build_routes()`` in a plain
``aiohttp`` app with a fake ``nodes`` module -- no ComfyUI needed).
"""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

logger = logging.getLogger("eps_image")

ROUTE = "/eps/list_flags"


def collect_list_flags(mappings: Any) -> dict[str, dict[str, Any]]:
    """``{class name: {"input_is_list", "output_is_list"}}`` for every entry
    of *mappings* (``nodes.NODE_CLASS_MAPPINGS`` or any mapping like it) --
    ``getattr`` defaults mirror ``execution.py``: no ``INPUT_IS_LIST`` ⇒
    False; no ``OUTPUT_IS_LIST`` ⇒ one False per ``RETURN_TYPES`` entry.
    A class that raises on inspection is skipped, never fatal."""
    out: dict[str, dict[str, Any]] = {}
    items = mappings.items() if hasattr(mappings, "items") else []
    for name, cls in items:
        try:
            input_is_list = bool(getattr(cls, "INPUT_IS_LIST", False))
            return_types = getattr(cls, "RETURN_TYPES", ()) or ()
            raw = getattr(cls, "OUTPUT_IS_LIST", None)
            if raw is None:
                output_is_list = [False] * len(return_types)
            else:
                output_is_list = [bool(flag) for flag in raw]
            out[str(name)] = {"input_is_list": input_is_list, "output_is_list": output_is_list}
        except Exception:  # a broken third-party class must not break the route
            logger.debug("EPSNodes: list_flags skipped class %r (inspection failed)", name)
    return out


def register_routes(routes: web.RouteTableDef) -> None:
    """Attach the list-flags route to *routes*."""

    @routes.get(ROUTE)
    async def get_list_flags(request: web.Request) -> web.Response:
        import nodes  # ComfyUI's own module; only importable inside ComfyUI

        mappings = getattr(nodes, "NODE_CLASS_MAPPINGS", {})
        return web.json_response({"classes": collect_list_flags(mappings)})


def build_routes() -> web.RouteTableDef:
    """A standalone table with just this module's route -- used by tests
    (wrapped in a plain ``aiohttp.web.Application``, no ComfyUI needed) and,
    indirectly, by :func:`register`."""
    routes = web.RouteTableDef()
    register_routes(routes)
    return routes


def register() -> None:
    """Attach this module's route to ComfyUI's live server -- the only
    function here that touches ``PromptServer`` (mirrors
    ``routes_checkpoint_switcher.register``)."""
    from server import PromptServer  # ComfyUI's own module; import only inside ComfyUI

    register_routes(PromptServer.instance.routes)
    logger.info("EPSNodes: registered %s", ROUTE)
