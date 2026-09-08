"""EPS Node Audit — the node surface over :mod:`eps_audit.audit`.

Runs the lenses and hands back one plain-text report as a STRING, so it can
be wired into a Save Text / preview node or simply read on the node itself
(``OUTPUT_NODE`` puts the text on the node's own body). The identical report
is available as JSON at ``GET /eps/audit`` for reading without queueing a
graph — see ``routes_audit.py``.
"""

from __future__ import annotations

import logging

from .audit import SCOPE_EVERYTHING, SCOPES, run_audit
from .observers import EGRESS
from .report import render

logger = logging.getLogger("eps_audit")


class EPSNodeAudit:
    """Inventory of what the other installed packs can reach."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "scope": (SCOPES, {
                    "default": SCOPE_EVERYTHING,
                    "tooltip": "Which lenses to run. 'source scan only' is the one that "
                               "reads files, and the only one with a real cost on a big "
                               "install.",
                }),
                "include_this_pack": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Include EPSNodes itself. On by default: its routes and "
                               "its path handling are worth the same scrutiny as anyone "
                               "else's, and a self-audit that skipped itself would be "
                               "worth less.",
                }),
                "verbose": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "Off: a short plain-language summary with a green / "
                               "yellow / red read per pack — no rule ids, file paths "
                               "or line numbers. On: the full technical report, for "
                               "reading with the source open.",
                }),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("report",)
    FUNCTION = "execute"
    CATEGORY = "EPSNodes/Utilities"
    OUTPUT_NODE = True

    DESCRIPTION = (
        "Inventories what the OTHER custom-node packs on this server can reach: every "
        "HTTP route they register, every outbound destination they have called since "
        "startup, and a scan of their source for a few recurring risky idioms (paths "
        "joined without clamping, routes forwarding a caller-supplied URL, secrets sent "
        "to a variable host). Observe-only — it never blocks or changes another pack's "
        "behaviour, and its findings are leads to review, not verdicts. Defaults to a "
        "plain-language green/yellow/red summary; turn on 'verbose' for the full "
        "technical report."
    )

    @classmethod
    def IS_CHANGED(cls, scope, include_this_pack, verbose=False, **kwargs):
        # Content-derived, not a timestamp (this pack's 2026-08-24 rule): the
        # answer only moves when the route table grows or another outbound
        # call happens, and both are counters already in memory. Cheap enough
        # to evaluate on every queue. `verbose` is folded in too, so flipping
        # the checkbox alone still counts as a change and repaints the node.
        from .audit import live_router

        router = live_router()
        try:
            route_count = len(list(router.routes())) if router is not None else 0
        except Exception:
            route_count = -1
        return f"{scope}:{include_this_pack}:{verbose}:{route_count}:{EGRESS.total_calls}"

    def execute(self, scope=SCOPE_EVERYTHING, include_this_pack=True, verbose=False):
        try:
            report = run_audit(scope, bool(include_this_pack))
            text = render(report, verbose=bool(verbose))
        except Exception as error:  # a failed audit must not fail the queue
            logger.exception("EPSNodes: audit failed")
            text = f"EPS NODE AUDIT FAILED: {type(error).__name__}: {error}"
        return {"ui": {"text": [text]}, "result": (text,)}
