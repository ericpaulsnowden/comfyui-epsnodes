"""``GET /eps/state_registry`` -- every loaded node class's declarative
``EPS_STATE_WIDGETS`` descriptor (§6.16 M0), for the run-count estimator,
EPS Save Image pinning, and the Universal State Controller alike.

Several EPS nodes keep user state in hidden JSON "bridge" widgets, each with
its own shape (the Prompt Notebook's ``entry``, the Apply Set's
``pinned_state``, a Switcher's ``toggles``, ...). Rather than teach every
consumer each node's shape by hand, the node classes that carry this kind of
state declare it themselves, right next to the ``INPUT_TYPES`` that owns
it, as a small pure-JSON class attribute -- ``EPS_STATE_WIDGETS`` -- naming
each captured widget's ``kind`` (from a small closed set the frontend
implements one generic validator per) plus, for the widgets a node owner
decided NOT to expose, an ``excluded`` map naming why. This route just
collects every class's own descriptor into one place: ``{"format": 1,
"classes": {"<class_id>": {"display": "<display name>", "format", "widgets",
"excluded"?}}}``.

Modeled exactly on ``routes_list_flags.py`` (read its module docstring for
the full rationale this one inherits verbatim): walk ``nodes.
NODE_CLASS_MAPPINGS`` (this pack's own registered classes, same as every
other class this pack loads through ``__init__.py``'s ``_NODE_SPECS``), keep
only classes that actually carry an ``EPS_STATE_WIDGETS`` attribute -- which
future-proofs third-party adopters of the same convention, exactly the way
``list_flags`` future-proofs itself against any class exposing
``INPUT_IS_LIST``/``OUTPUT_IS_LIST`` -- and read each display name from
``NODE_DISPLAY_NAME_MAPPINGS`` when available, else fall back to the class
id itself. A broken third-party class (an attribute that raises, or isn't
JSON-serializable) never breaks the route: per-class failures are skipped
(logged at DEBUG), the rest still count.

No loopback gate: like ``list_flags``, this is class METADATA every viewer
already receives in bulk through ``/object_info`` in substance (the widget
names and their COMBO/STRING/INT/FLOAT kinds) -- this route adds only the
capture-worthiness classification and shape hints on top, nothing a remote
graph-editor tab couldn't already infer from the node's own inputs.

Registered onto ``PromptServer.instance.routes`` through the same
``register``/``build_routes``/``register_routes`` split as
``routes_list_flags.py`` (tests wrap ``build_routes()`` in a plain
``aiohttp`` app with a fake ``nodes`` module -- no ComfyUI needed).

**Process-lifetime memo, exactly like ``list_flags``:** every loaded
class's ``EPS_STATE_WIDGETS`` is a class attribute fixed at import time
(never mutated after registration), so the answer is static for the
process's whole life -- a successful walk is memoized at module scope after
the first request, and every later request is answered from that cached
dict with no walk at all. The one-time miss path still walks
``NODE_CLASS_MAPPINGS`` off the event loop via ``asyncio.to_thread``, this
pack's established idiom for a non-trivial synchronous pass inside an
aiohttp handler. Only a SUCCESSFUL walk is cached: a transient failure (an
empty/degenerate ``nodes`` module, or the walk raising) must not wedge
every later request behind one bad answer -- see ``_reset_cache`` for the
test-only escape hatch.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from aiohttp import web

logger = logging.getLogger("eps_image")

ROUTE = "/eps/state_registry"

#: The registry's own top-level ``format`` -- distinct from each individual
#: class descriptor's own ``format`` (which is that node's own §6.16
#: schema-version number, and travels with the descriptor unchanged).
REGISTRY_FORMAT = 1


def collect_state_registry(mappings: Any, display_names: Any = None) -> dict[str, dict[str, Any]]:
    """``{class id: {"display", **that class's EPS_STATE_WIDGETS}}`` for
    every entry of *mappings* (``nodes.NODE_CLASS_MAPPINGS`` or any mapping
    like it) that carries an ``EPS_STATE_WIDGETS`` class attribute --
    classes without one are silently skipped, not an error (the whole
    point: only state-bearing nodes opt in). *display_names* is
    ``nodes.NODE_DISPLAY_NAME_MAPPINGS`` or any mapping like it (``None``/
    missing entries fall back to the class id itself).

    A class whose attribute raises on access, isn't a ``dict``, or isn't
    JSON-round-trippable (``json.dumps`` it and check) is skipped entirely
    (logged at DEBUG) -- a broken third-party class must never break the
    route for every other class alongside it. The descriptor is copied via
    a JSON round-trip (``loads(dumps(...))``) rather than handed back by
    reference, so nothing here can alias -- let alone let a caller mutate --
    the class's own attribute.
    """
    out: dict[str, dict[str, Any]] = {}
    items = mappings.items() if hasattr(mappings, "items") else []
    names = display_names if hasattr(display_names, "get") else {}
    for class_id, cls in items:
        try:
            descriptor = getattr(cls, "EPS_STATE_WIDGETS", None)
            if descriptor is None:
                continue
            if not isinstance(descriptor, dict):
                raise TypeError(f"EPS_STATE_WIDGETS is not a dict ({type(descriptor).__name__})")
            # Round-trip through JSON: enforces the "pure JSON-serializable,
            # no functions" contract every descriptor promises, and hands
            # back a fresh copy no caller can alias into the live class
            # attribute.
            copied = json.loads(json.dumps(descriptor))
            entry = dict(copied)
            entry["display"] = str(names.get(str(class_id), class_id))
            out[str(class_id)] = entry
        except Exception:  # a broken third-party class must not break the route
            logger.debug(
                "EPSNodes: state_registry skipped class %r (inspection failed)", class_id
            )
    return out


#: Memoized ``{"format": ..., "classes": {...}}`` body (mirrors
#: ``routes_list_flags._cached_response`` exactly -- see that module's
#: docstring for the full staleness-until-restart rationale, which applies
#: here verbatim: a loaded class's ``EPS_STATE_WIDGETS`` never changes after
#: import, and ``NODE_CLASS_MAPPINGS`` itself only grows new entries at
#: custom-node LOAD time). Only a SUCCESSFUL walk is cached.
_cached_response: dict[str, Any] | None = None


def _reset_cache() -> None:
    """Test-only: drop the memoized response so the next request re-walks
    ``NODE_CLASS_MAPPINGS``. Production code never calls this -- see
    :data:`_cached_response`'s docstring for why the process-lifetime cache
    is safe without one."""
    global _cached_response
    _cached_response = None


def register_routes(routes: web.RouteTableDef) -> None:
    """Attach the state-registry route to *routes*."""

    @routes.get(ROUTE)
    async def get_state_registry(request: web.Request) -> web.Response:
        global _cached_response
        if _cached_response is None:
            import nodes  # ComfyUI's own module; only importable inside ComfyUI

            mappings = getattr(nodes, "NODE_CLASS_MAPPINGS", {})
            display_names = getattr(nodes, "NODE_DISPLAY_NAME_MAPPINGS", {})
            # Off the loop, exactly like list_flags: a large custom-node
            # install can mean hundreds of classes to inspect, even though
            # only a handful actually carry EPS_STATE_WIDGETS. A concurrent
            # request arriving before this one lands can redundantly kick
            # off its own to_thread walk too (no lock here) -- the same
            # accepted rare-duplicate-walk-at-warm-up race list_flags takes.
            classes = await asyncio.to_thread(collect_state_registry, mappings, display_names)
            _cached_response = {"format": REGISTRY_FORMAT, "classes": classes}
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
    ``routes_list_flags.register``)."""
    from server import PromptServer  # ComfyUI's own module; only importable inside ComfyUI

    register_routes(PromptServer.instance.routes)
    logger.info("EPSNodes: registered %s", ROUTE)
