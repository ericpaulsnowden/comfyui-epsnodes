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

**2026-08-26 while-running round:** the answer is static for the process's
whole life (see :data:`_cached_response`'s docstring for why), so a
successful walk is memoized at module scope after the first request and
every later request is answered from that cached dict with no walk at all;
the one-time miss path still walks ``NODE_CLASS_MAPPINGS`` off the event
loop via ``asyncio.to_thread`` (this pack's established idiom for a
non-trivial synchronous pass inside an aiohttp handler --
``routes_image_grid.py``'s preview branch).
"""

from __future__ import annotations

import asyncio
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


#: Memoized ``{"classes": {...}}`` body (2026-08-26 while-running round):
#: every request used to re-walk ALL of ``NODE_CLASS_MAPPINGS``
#: synchronously, on the loop, for an answer that's static for the whole
#: life of the process -- a loaded class's ``INPUT_IS_LIST``/
#: ``OUTPUT_IS_LIST``/``RETURN_TYPES`` never change after import, and
#: ``NODE_CLASS_MAPPINGS`` itself only grows new entries at custom-node
#: LOAD time (server startup). This pack has no cheap in-process signal for
#: "a custom node was hot-reloaded without a process restart" -- every
#: reload path this repo has run against (ComfyUI-Manager's Restart,
#: `--reload`, a plain process bounce) re-execs the server, which
#: re-imports this module and drops the cache for free -- so rather than
#: invent a polling/mtime scheme for a signal that doesn't exist, staleness
#: is accepted up to the next restart (documented here rather than gated on
#: anything). Only a SUCCESSFUL walk is cached: a transient failure (an
#: empty/degenerate ``nodes`` module, or the walk raising) must not wedge
#: every later request behind one bad answer.
_cached_response: dict[str, Any] | None = None


def _reset_cache() -> None:
    """Test-only: drop the memoized response so the next request re-walks
    ``NODE_CLASS_MAPPINGS``. Production code never calls this -- see
    :data:`_cached_response`'s docstring for why the process-lifetime cache
    is safe without one."""
    global _cached_response
    _cached_response = None


def register_routes(routes: web.RouteTableDef) -> None:
    """Attach the list-flags route to *routes*."""

    @routes.get(ROUTE)
    async def get_list_flags(request: web.Request) -> web.Response:
        global _cached_response
        if _cached_response is None:
            import nodes  # ComfyUI's own module; only importable inside ComfyUI

            mappings = getattr(nodes, "NODE_CLASS_MAPPINGS", {})
            # One getattr per loaded class, but a large custom-node install
            # can mean hundreds of classes -- off the loop like every other
            # non-trivial CPU pass in this pack's routes (paid at most once
            # per process; see _cached_response above). A concurrent request
            # arriving before this one lands can redundantly kick off its
            # own to_thread walk too (no lock here) -- both compute the same
            # answer, so the only cost is a rare duplicate walk right at
            # warm-up, the same race this pack already accepts in
            # image_grid_store.thumbnail_path's cache-miss path.
            classes = await asyncio.to_thread(collect_list_flags, mappings)
            _cached_response = {"classes": classes}
        return web.json_response(_cached_response)


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
