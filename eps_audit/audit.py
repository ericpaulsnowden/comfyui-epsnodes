"""The one file in this family that touches ComfyUI, and the single entry
point both surfaces call (FORMAT.md §9).

``lora_library/context.py``'s seam, applied to a smaller problem: every
other module here is pure and tested without ComfyUI, so the imports of
``folder_paths``/``server`` are concentrated in this one place and mocked in
exactly one place.
"""

from __future__ import annotations

import logging
import os

from .attribution import normalize_root
from .observers import EGRESS, install_egress_hooks, inventory_routes
from .report import build_report
from .scanner import scan_pack

logger = logging.getLogger("eps_audit")

SCOPE_EVERYTHING = "everything"
SCOPE_ROUTES = "routes only"
SCOPE_EGRESS = "egress only"
SCOPE_SCAN = "source scan only"
SCOPES = [SCOPE_EVERYTHING, SCOPE_ROUTES, SCOPE_EGRESS, SCOPE_SCAN]


def this_pack_name() -> str:
    """This pack's own folder name — the one the report can exclude.

    ``abspath``, not ``resolve``, for the same reason as
    :func:`custom_nodes_roots`: through a symlinked install the resolved
    name is the working copy's folder name, which need not match the name
    ComfyUI actually loaded (and which attribution reports)."""
    return os.path.basename(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def custom_nodes_roots() -> list[str]:
    """Every ``custom_nodes`` directory ComfyUI is loading packs from.

    ``get_folder_paths`` because a ComfyUI install can legitimately have
    several (``--extra-model-paths-config``), and a report that knew about
    only one would quietly under-count the very thing it exists to inventory.

    The own-parent fallback is used ONLY when ComfyUI names no roots at all
    (outside a server, or a build without that folder key). It is not merely
    redundant otherwise, it is WRONG: this pack is symlinked into the rig's
    ``custom_nodes`` from a Dropbox working copy, so appending it
    unconditionally added the working copy's PARENT as a second root and the
    first live run duly reported a sibling docs folder (``research``) as an
    installed node pack. For the same reason the fallback is derived from
    ``abspath``, not ``resolve``: through a symlinked install ``resolve``
    lands in the working copy, while the literal path is the real
    ``custom_nodes`` directory ComfyUI loaded from.
    """
    roots: list[str] = []
    try:
        import folder_paths

        for root in folder_paths.get_folder_paths("custom_nodes") or []:
            if root and os.path.isdir(root):
                normalized = normalize_root(root)
                if normalized not in roots:
                    roots.append(normalized)
    except Exception:
        logger.debug("EPSNodes audit: folder_paths had no custom_nodes roots", exc_info=True)
    if not roots:
        fallback = os.path.normcase(
            os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
        )
        if os.path.isdir(fallback):
            roots.append(fallback)
    return roots


def installed_packs(roots) -> dict[str, str]:
    """``{pack name: absolute dir}`` for every pack directory under *roots*.

    Dot- and underscore-prefixed folders are skipped (``.disabled``,
    ``__pycache__``): ComfyUI does not load them, so neither should the
    report claim they are installed.
    """
    packs: dict[str, str] = {}
    for root in roots:
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            continue
        for name in entries:
            if name.startswith((".", "_")):
                continue
            full = os.path.join(root, name)
            if os.path.isdir(full) and name not in packs:
                packs[name] = full
    return packs


def live_router():
    """ComfyUI's aiohttp router, or ``None`` outside a running server."""
    try:
        from server import PromptServer

        return PromptServer.instance.app.router
    except Exception:
        return None


def run_audit(scope: str = SCOPE_EVERYTHING, include_this_pack: bool = True) -> dict:
    """Run the requested lenses and return the report dict.

    A lens the scope excludes contributes an empty result rather than being
    absent, so both surfaces can render the same shape unconditionally.
    """
    roots = custom_nodes_roots()
    packs = installed_packs(roots)
    if not include_this_pack:
        packs.pop(this_pack_name(), None)

    routes: list[dict] = []
    if scope in (SCOPE_EVERYTHING, SCOPE_ROUTES):
        router = live_router()
        if router is not None:
            routes = inventory_routes(router, roots)
            if not include_this_pack:
                routes = [row for row in routes if row["pack"] != this_pack_name()]

    findings: list = []
    scanned: list[str] = []
    if scope in (SCOPE_EVERYTHING, SCOPE_SCAN):
        for name, directory in sorted(packs.items()):
            try:
                findings.extend(scan_pack(directory, pack=name))
                scanned.append(name)
            except Exception:  # one unreadable pack must not lose the report
                logger.exception("EPSNodes audit: scan failed for pack %s", name)

    return build_report(
        routes=routes,
        egress=EGRESS,
        findings=findings,
        packs=list(packs),
        roots=roots,
        hooks=_hook_status(),
        scanned_packs=scanned,
    )


#: Hook status is captured when the pack installs them at startup; the audit
#: only READS it (installing on demand would mean an audit run silently
#: changed the process it is reporting on).
_hook_status_cache: dict[str, bool] = {}


def _hook_status() -> dict[str, bool]:
    return dict(_hook_status_cache)


def install_hooks_at_startup() -> dict[str, bool]:
    """Called once from the pack's ``__init__``. Records what attached so
    the report can say plainly which libraries it is NOT watching."""
    global _hook_status_cache
    _hook_status_cache = install_egress_hooks(custom_nodes_roots())
    return dict(_hook_status_cache)
