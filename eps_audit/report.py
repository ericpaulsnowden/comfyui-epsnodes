"""Assembles the three lenses into one dict, and renders that dict as the
plain-text report the node outputs.

Split from the lenses so it stays pure: the JSON route returns
:func:`build_report`'s dict as-is and the node returns
:func:`render_text` of the same dict, which is what keeps the two surfaces
from drifting into two different answers.

The footer is not decoration. Everything above it is heuristic — regex over
source text, plus whatever happened to have been called since this process
started — and a reader who takes a finding count as a vulnerability count
has been misled by this file. So the caveats ship WITH the output, every
time, rather than living in documentation nobody re-reads.
"""

from __future__ import annotations

from .scanner import count_by_severity, sort_findings

#: Findings printed in full before the report truncates. A first run against
#: a big install can produce hundreds; the count line always states the real
#: total and how many were withheld, so a cap never reads as "that's all".
MAX_LISTED_FINDINGS = 40


def build_report(*, routes, egress, findings, packs, roots, hooks, scanned_packs) -> dict:
    """One dict holding every lens's result plus the context to read it in."""
    findings = sort_findings(findings)
    third_party_routes = [row for row in routes if row["pack"]]
    return {
        "custom_nodes_roots": list(roots),
        "packs": sorted(packs),
        "routes": {
            "total": len(routes),
            "from_packs": len(third_party_routes),
            "rows": third_party_routes,
        },
        "egress": {
            "destinations": egress.snapshot(),
            "total_calls": egress.total_calls,
            "truncated": egress.truncated,
            "hooks": dict(hooks or {}),
        },
        "scan": {
            "packs_scanned": sorted(scanned_packs),
            "counts": count_by_severity(findings),
            "findings": [
                {
                    "rule_id": f.rule_id, "severity": f.severity, "title": f.title,
                    "why": f.why, "pack": f.pack, "path": f.path, "line": f.line,
                    "excerpt": f.excerpt,
                }
                for f in findings
            ],
        },
    }


def _routes_section(report, lines) -> None:
    routes = report["routes"]
    lines.append(
        f"ROUTES — {routes['total']} registered on this server, "
        f"{routes['from_packs']} of them from custom-node packs"
    )
    if not routes["rows"]:
        lines.append("  (no custom-node routes found)")
    by_pack: dict[str, list] = {}
    for row in routes["rows"]:
        by_pack.setdefault(row["pack"], []).append(row)
    for pack in sorted(by_pack):
        rows = by_pack[pack]
        lines.append(f"  {pack}  ({len(rows)})")
        for row in rows:
            lines.append(f"      {row['method']:6} {row['path']}")
    core = routes["total"] - routes["from_packs"]
    if core > 0:
        lines.append(f"  ({core} more belong to ComfyUI core or its libraries — not listed)")
    lines.append("")


def _egress_section(report, lines) -> None:
    egress = report["egress"]
    detached = [name for name, ok in egress["hooks"].items() if not ok]
    lines.append(
        f"EGRESS — {len(egress['destinations'])} destination(s) seen, "
        f"{egress['total_calls']} outbound call(s) since this server started"
    )
    for row in egress["destinations"]:
        methods = ",".join(row["methods"]) or "?"
        target = f"{row['scheme']}://{row['host']}" if row["scheme"] else row["host"]
        lines.append(f"  {row['pack']}  ->  {target}   {row['count']}x  {methods}")
    if not egress["destinations"]:
        lines.append("  (nothing observed yet — this lens only sees calls made since startup)")
    if egress["truncated"]:
        lines.append("  NOTE: destination list hit its cap; some pairs were dropped.")
    if detached:
        lines.append(
            "  NOT WATCHING: " + ", ".join(sorted(detached))
            + " — calls through those libraries are invisible to this report."
        )
    lines.append("")


def _scan_section(report, lines) -> None:
    scan = report["scan"]
    counts = scan["counts"]
    summary = ", ".join(
        f"{counts.get(level, 0)} {level}" for level in ("high", "medium", "note")
    )
    lines.append(
        f"SOURCE SCAN — {len(scan['packs_scanned'])} pack(s) scanned: {summary}"
    )
    listed = scan["findings"][:MAX_LISTED_FINDINGS]
    for finding in listed:
        lines.append(
            f"  [{finding['severity']}] {finding['pack']}/{finding['path']}:{finding['line']}"
            f"  ({finding['rule_id']})"
        )
        lines.append(f"        {finding['excerpt']}")
        lines.append(f"        why: {finding['why']}")
    withheld = len(scan["findings"]) - len(listed)
    if withheld > 0:
        lines.append(
            f"  ... and {withheld} more finding(s) not printed here "
            f"(cap is {MAX_LISTED_FINDINGS}) — the JSON route has all of them."
        )
    if not scan["findings"]:
        lines.append("  (no idioms matched)")
    lines.append("")


def render_text(report) -> str:
    """The node's STRING output: the whole report, footer included."""
    lines: list[str] = []
    packs = report["packs"]
    lines.append(f"EPS NODE AUDIT — {len(packs)} installed pack(s)")
    for root in report["custom_nodes_roots"]:
        lines.append(f"  custom_nodes: {root}")
    lines.append("")
    _routes_section(report, lines)
    _egress_section(report, lines)
    _scan_section(report, lines)
    lines.append(
        "READ THIS AS TRIAGE, NOT A VERDICT. The scan is regex over source text with no "
        "dataflow behind it, so correct code that validates its inputs still matches — a "
        "finding means 'a human should read this line', nothing more. The egress lens "
        "shows only what was called since startup, so silence is not proof of silence. "
        "Nothing here blocks, patches or changes any pack's behaviour."
    )
    return "\n".join(lines)
