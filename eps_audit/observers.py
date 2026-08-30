"""The two LIVE lenses: what routes exist right now, and where packs have
actually called out to since this server started.

Both are read-only in the sense that matters: the route lens only walks
aiohttp's own table, and the egress lens records a destination and then
calls straight through. Nothing here blocks a request, rewrites an argument,
or changes a return value. That is a deliberate first milestone — an
inventory you can trust is the prerequisite for any policy you might want
later, and a guardrail that silently changed another pack's behaviour would
be far harder to reason about than the packs themselves.

**Why the route lens reads the table instead of wrapping registration.**
Packs register their routes at IMPORT time and ComfyUI imports
``custom_nodes`` in directory order, so a wrapper installed by this pack
would only ever see packs that sort after it — it would have missed
``ComfyUI-DaSiWa-Nodes`` on the owner's own rig. The finished table has
every route regardless of who loaded when, so it is both simpler and
strictly more complete.

**Why the egress lens CAN hook late.** It wraps the client libraries rather
than the callers, and the calls it cares about happen at render time, long
after every pack has imported — so load order does not matter. Attribution
walks the stack at call time to find the innermost pack frame
(:func:`attribution.pack_of_frame`).

**Fail loud.** :func:`install_egress_hooks` returns exactly which hooks
attached, and the pack logs that at startup: an interposition that quietly
stops matching its target is worse than none, because you keep believing
you are covered.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

from .attribution import pack_of_frame, pack_of_path

logger = logging.getLogger("eps_audit")

#: Hard cap on distinct (pack, host) pairs kept. A pack in a retry loop
#: against many hosts must not grow this without bound inside a long-lived
#: server; past the cap new pairs are dropped and `truncated` is set.
MAX_EGRESS_KEYS = 500


@dataclass
class EgressRecord:
    pack: str
    host: str
    scheme: str
    count: int = 0
    methods: set = field(default_factory=set)


class EgressLog:
    """Thread-safe tally of outbound destinations, never payloads.

    Deliberately records only ``(pack, scheme, host, method, count)``. A URL
    path or body would turn this inventory into its own privacy problem —
    the question it exists to answer is "who is this pack talking to", which
    the host alone answers.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[tuple, EgressRecord] = {}
        self.truncated = False
        self.total_calls = 0

    def record(self, pack: str | None, scheme: str, host: str, method: str) -> None:
        key = (pack or "(not a custom node)", scheme or "", host or "")
        with self._lock:
            self.total_calls += 1
            record = self._records.get(key)
            if record is None:
                if len(self._records) >= MAX_EGRESS_KEYS:
                    self.truncated = True
                    return
                record = EgressRecord(pack=key[0], host=key[2], scheme=key[1])
                self._records[key] = record
            record.count += 1
            if method:
                record.methods.add(method.upper())

    def snapshot(self) -> list[dict]:
        with self._lock:
            records = list(self._records.values())
        return sorted(
            ({"pack": r.pack, "scheme": r.scheme, "host": r.host,
              "count": r.count, "methods": sorted(r.methods)} for r in records),
            key=lambda row: (row["pack"], row["host"]),
        )


#: Process-wide log; the hooks below write into it and the report reads it.
EGRESS = EgressLog()

#: Set once hooks are installed, so a second call is a no-op rather than a
#: double-wrap (which would double-count every call).
_hooks_installed: dict[str, bool] | None = None


def _reset_hooks_for_tests() -> None:
    """Test-only: forget that hooks were installed so the next call re-runs
    the wrapping. Production never calls this — the guard exists so a second
    install cannot double-wrap (and therefore double-count) a client."""
    global _hooks_installed
    _hooks_installed = None


def _split_url(url) -> tuple[str, str]:
    """(scheme, host) for whatever the client library was handed — a str, a
    yarl.URL, or a urllib Request. Never raises: a destination we cannot
    parse is recorded as unknown rather than losing the call."""
    try:
        text = getattr(url, "full_url", None) or str(url)
        from urllib.parse import urlsplit

        parts = urlsplit(text)
        return parts.scheme or "", (parts.hostname or "")
    except Exception:
        return "", "?"


def install_egress_hooks(roots) -> dict[str, bool]:
    """Wrap the HTTP clients so outbound calls are tallied. Idempotent.

    Returns ``{hook name: attached}``. Every wrapper is written so that a
    failure in the BOOKKEEPING can never fail the call it is observing: the
    recording is inside its own try/except and the original callable is
    invoked either way.
    """
    global _hooks_installed
    if _hooks_installed is not None:
        return dict(_hooks_installed)

    status: dict[str, bool] = {}

    def note(url, method, skip):
        try:
            scheme, host = _split_url(url)
            EGRESS.record(pack_of_frame(roots, skip=skip), scheme, host, method)
        except Exception:  # observation must never break the observed call
            logger.debug("EPSNodes audit: egress bookkeeping failed", exc_info=True)

    try:
        import aiohttp

        original_request = aiohttp.ClientSession._request
        if not getattr(original_request, "_eps_audit_wrapped", False):
            async def _request(self, method, str_or_url, *args, **kwargs):
                note(str_or_url, method, skip=1)
                return await original_request(self, method, str_or_url, *args, **kwargs)

            _request._eps_audit_wrapped = True
            aiohttp.ClientSession._request = _request
        status["aiohttp"] = True
    except Exception:
        status["aiohttp"] = False

    try:
        import urllib.request

        original_urlopen = urllib.request.urlopen
        if not getattr(original_urlopen, "_eps_audit_wrapped", False):
            def _urlopen(url, *args, **kwargs):
                method = getattr(url, "get_method", lambda: "GET")()
                note(url, method, skip=1)
                return original_urlopen(url, *args, **kwargs)

            _urlopen._eps_audit_wrapped = True
            urllib.request.urlopen = _urlopen
        status["urllib"] = True
    except Exception:
        status["urllib"] = False

    try:
        import requests.sessions

        original_send = requests.sessions.Session.request
        if not getattr(original_send, "_eps_audit_wrapped", False):
            def _send(self, method, url, *args, **kwargs):
                note(url, method, skip=1)
                return original_send(self, method, url, *args, **kwargs)

            _send._eps_audit_wrapped = True
            requests.sessions.Session.request = _send
        status["requests"] = True
    except Exception:
        # requests is not a dependency of this pack and need not be present.
        status["requests"] = False

    _hooks_installed = status
    return dict(status)


def _handler_file(handler) -> str:
    """Source file behind an aiohttp handler, through any decorators.

    ``__code__.co_filename`` rather than ``inspect.getfile`` because a
    handler is routinely a closure or a functools-wrapped coroutine, and
    ``getfile`` raises for several of those shapes.
    """
    seen = 0
    while handler is not None and seen < 10:
        wrapped = getattr(handler, "__wrapped__", None)
        if wrapped is None:
            break
        handler, seen = wrapped, seen + 1
    code = getattr(handler, "__code__", None)
    if code is not None:
        return getattr(code, "co_filename", "") or ""
    func = getattr(handler, "func", None) or getattr(handler, "__func__", None)
    code = getattr(func, "__code__", None) if func is not None else None
    return (getattr(code, "co_filename", "") or "") if code is not None else ""


def inventory_routes(router, roots) -> list[dict]:
    """Every registered route as ``{method, path, pack, file}``.

    *router* is any object with aiohttp's ``routes()`` iterator, so the
    tests pass a plain ``web.Application().router``. Routes whose handler
    cannot be attributed to a pack are reported with ``pack: None`` — that
    is ComfyUI core and its libraries, which is exactly the contrast that
    makes the third-party rows worth reading.
    """
    rows: list[dict] = []
    try:
        entries = list(router.routes())
    except Exception:
        logger.exception("EPSNodes audit: could not read the route table")
        return rows
    for route in entries:
        try:
            resource = getattr(route, "resource", None)
            path = getattr(resource, "canonical", None) or str(resource or "")
            method = str(getattr(route, "method", "") or "")
            if method.upper() in ("HEAD", "OPTIONS"):
                continue  # aiohttp's own mirrors of the real routes
            file_path = _handler_file(getattr(route, "handler", None))
            rows.append({
                "method": method.upper(),
                "path": path,
                "pack": pack_of_path(file_path, roots),
                "file": file_path,
            })
        except Exception:
            logger.debug("EPSNodes audit: skipped an unreadable route", exc_info=True)
    rows.sort(key=lambda row: ((row["pack"] or ""), row["path"], row["method"]))
    return rows
