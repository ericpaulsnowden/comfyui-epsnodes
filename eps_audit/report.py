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


# =====================================================================
# Plain-language view (the node's `verbose=False` default).
#
# `render_text` above is the expert read: rule ids, file paths, line
# numbers. Most people who install a ComfyUI node pack are artists, not
# programmers, and that text is close to unreadable to them — hence this
# second renderer, chosen by the node's `verbose` widget instead of a
# second node or a second output.
#
# The hard constraint here is honesty, not readability. Every rule below
# is a regex over source text with no idea what the code actually does —
# this scanner's own history is the proof: a live run once produced 50
# "high" findings that tuning cut to 5, because `model.eval()` matches a
# bare `eval(` rule and `os.path.join(dir, name)` looks like a traversal
# until you notice `name` came from that same directory's own listing.
# So the colours below answer "how much attention is this worth", never
# "is this pack dangerous" — green means the scan found nothing to flag,
# not that a pack was checked and cleared, and nothing here ever tells
# the reader to distrust or remove a pack. See RULE_COPY's docstring for
# the specific vocabulary this file refuses to use and why.
# =====================================================================

#: Plain-language copy for each scanner rule id, written for someone who
#: is skilled with images and tools but has never read Python. Deliberately
#: keyed by id rather than position, so a rule can be renamed, reworded or
#: retuned in scanner.py without this table silently going stale — an id
#: this table does not recognise (a brand-new rule, or one mid-rename while
#: another session tunes RULES) falls through to _GENERIC_COPY instead of
#: printing a bare rule id or raising.
#:
#: Wording rules, enforced by a test so they cannot erode one wording pass
#: at a time: never call a pack malicious, unsafe, compromised or "a
#: threat"; never say "risk", "vulnerability" or "danger" where "worth a
#: look" says the same thing without the accusation; never suggest
#: uninstalling a pack or distrusting its author — the only actions here
#: are "look at this" and "ask the author". No jargon a working artist
#: would have to look up: say what a thing DOES, not its technical name
#: (so: "reaches outside its own folder", never "traversal"; "visits a web
#: address" or "asks another computer", never "SSRF"; "watches for" or
#: "connects to", never "sink" or "egress").
RULE_COPY: dict[str, dict[str, str]] = {
    "path.unclamped_join": {
        "what": (
            "It builds a file location by joining a folder it trusts with a name it "
            "was simply handed, without checking that the result is still inside "
            "that folder."
        ),
        "meaning": (
            "If that handed-in name ever came from outside the pack itself — a "
            "workflow file someone shared, a request over the network — this could "
            "let it reach files well outside the folder it was meant to stay in."
        ),
        "action": "Worth finding out where that name actually comes from.",
    },
    "path.startswith_check": {
        "what": (
            "It decides whether a file lives 'inside its own folder' by checking "
            "whether the file's location starts with the same letters as that "
            "folder's name."
        ),
        "meaning": (
            "A completely different folder that just happens to start with the same "
            "letters could pass that check by mistake, even though it isn't really "
            "inside."
        ),
        "action": "Worth a look at how that check is written.",
    },
    "route.body_url": {
        "what": (
            "It takes a web address from whoever is talking to it over the network, "
            "then goes and visits that address itself."
        ),
        "meaning": (
            "If nothing limits which addresses it's willing to visit, anyone who can "
            "reach this ComfyUI server could use this feature to make it contact "
            "other computers on your behalf, without you seeing it happen."
        ),
        "action": "Worth a look, or worth asking the pack's author what this is for.",
    },
    "secret.env_to_dynamic_host": {
        "what": (
            "It sends a saved key or password along with a request whose destination "
            "isn't fixed in the pack's own code."
        ),
        "meaning": (
            "If that destination can be changed by anything outside the pack, the "
            "key could end up going somewhere other than the service it belongs to."
        ),
        "action": "Worth checking exactly where that key is being sent.",
    },
    "exec.dynamic": {
        "what": (
            "It runs a piece of code that isn't written out in the file itself — "
            "which code runs is decided while the program is already going."
        ),
        "meaning": (
            "This is sometimes an ordinary way to load an add-on or a saved model. "
            "It is also, in general, how a program ends up running something its "
            "author didn't write."
        ),
        "action": "Worth a look at where that code comes from.",
    },
    "route.body_path": {
        "what": "It accepts a file name from whoever is talking to it over the network.",
        "meaning": (
            "Ordinarily fine, as long as the pack keeps that name inside its own "
            "folder — this is worth a look mainly alongside a folder-location note "
            "for the same pack, if one appears below."
        ),
        "action": "Worth a quick look, especially together with any note just above.",
    },
    "write.binary": {
        "what": "It saves a file to disk in response to something it was asked to do.",
        "meaning": (
            "Worth knowing what decides that file's name and location, and whether "
            "it could overwrite something already there."
        ),
        "action": "Worth a look if you'd like to know exactly what gets written.",
    },
    "proc.spawn": {
        "what": "It can start another program running on this computer.",
        "meaning": "Worth knowing whether any part of what it runs could come from outside "
                   "the pack itself.",
        "action": "Worth a look if you're curious which program it starts, and why.",
    },
    "net.client": {
        "what": "It can talk to other computers over the internet.",
        "meaning": (
            "Plenty of packs do this on purpose — checking for updates, fetching a "
            "model, or reaching an online service they're built around."
        ),
        "action": (
            "Nothing to do unless you're curious; the routes-and-connections part of "
            "this report (or verbose mode) shows where it has actually gone."
        ),
    },
    "route.registers": {
        "what": (
            "It adds a new address that other things on your network could reach on "
            "this ComfyUI server."
        ),
        "meaning": "Normal for a pack that adds its own web-based feature.",
        "action": "Nothing to do unless you're curious which address it added.",
    },
}

#: Shown for a rule id this table doesn't recognise, so a brand-new or
#: renamed rule degrades to something honest rather than a bare id or a
#: crash. Says nothing specific on purpose — specificity for an id we
#: don't have copy for would have to be guessed, and a guess is exactly
#: the kind of overclaim this whole view exists to avoid.
_GENERIC_COPY: dict[str, str] = {
    "what": "The scan noticed a pattern it's been taught to watch for.",
    "meaning": (
        "Ordinary, harmless code trips these checks too, so this alone doesn't say "
        "much either way."
    ),
    "action": "Turn on verbose mode to see exactly what matched and where.",
}

#: How much attention each colour asks for, reused for both the overall
#: verdict and each pack's own line — the words are what carry the "how
#: much attention", the colour is just the at-a-glance version of the same
#: idea. Deliberately never says "safe": green is the absence of a match,
#: not a certificate that nothing is wrong.
_ATTENTION = {
    "red": "a few things worth a closer look",
    "yellow": "a couple of things worth a quick look",
    "green": "nothing stood out",
}

_DOT = {"red": "\U0001f534", "yellow": "\U0001f7e1", "green": "\U0001f7e2", "none": "⚪"}

#: Curated colour per rule id, deliberately separate from the scanner's own
#: `severity`. Severity answers "how bad would this be if it's real";
#: colour answers "how much of a reader's limited attention should this
#: ask for" — and those diverge hard once you know how common an idiom
#: actually is. Curated by hand 2026-09-02 after the owner's own 5-pack
#: install came back with every single pack red or yellow: `proc.spawn`
#: (starts another program) and `route.body_path` (takes a filename over
#: the network) hit nearly every pack in that install — for the Photoshop
#: and Premiere bridges, launching an application IS the pack's purpose —
#: and a scale that colours those red or yellow is the same as telling the
#: owner nothing, because nothing is ever left green. It got worse on
#: inspection, not better: of that install's 5 scanner-`high` findings, 2
#: in this very pack turned out on manual review to be plain false
#: positives (a GVFS-mount-name formatter that merely contains the text
#: "server=", and core SaveImage's own filename pattern) — proof that
#: `severity="high"` alone cannot be trusted to mean "look here first"
#: either. An id neither this table nor `_GENERIC_COPY`'s caller
#: recognises falls back to `_SEVERITY_FALLBACK`, so a brand-new rule
#: still gets a defensible colour before anyone curates it by hand.
ATTENTION_OVERRIDE: dict[str, str] = {
    # Uncommon AND, if the pattern were real, consequential — the small set
    # worth asking for a reader's attention first.
    "route.body_url": "red",
    "secret.env_to_dynamic_host": "red",
    "exec.dynamic": "red",
    # Worth a glance, not an alarm: neither idiom below is rare enough to
    # single a pack out, but neither is so universal that it says nothing.
    "path.unclamped_join": "yellow",
    "path.startswith_check": "yellow",
    "write.binary": "yellow",
    # Near-universal capabilities. Real, worth NAMING (see
    # `_capability_line`), but not worth a colour: almost every pack that
    # shells out to ffmpeg, launches a companion app, or serves a file over
    # its own routes will hit one of these on purpose.
    "proc.spawn": "green",
    "route.body_path": "green",
    "net.client": "green",
    "route.registers": "green",
}

#: Falls back to the scanner's own severity ONLY for a rule id this pack
#: has not curated yet — mirrors the pre-curation behaviour (high -> red,
#: medium -> yellow, everything else -> green) so a new or renamed rule in
#: `scanner.py` still renders sensibly before anyone updates the table
#: above by hand.
_SEVERITY_FALLBACK = {"high": "red", "medium": "yellow"}

#: Repeated near the verdict on every render (the honesty requirement is
#: the point of this whole file) rather than left to a footer a skimming
#: reader may never reach.
_HONEST_NOTE = (
    "These are patterns worth checking, not proof that anything is wrong — ordinary, "
    "harmless code trips them too, and this doesn't watch what a pack actually does "
    "while it runs. Nothing here changes, blocks or removes anything."
)

#: Shown only when a red badge actually appears somewhere in the report.
#: Red is reserved for the rarest, most consequential shapes, but "rare"
#: is not "reliable" — this scanner's own highs have turned out wrong on
#: this pack's own code (see ATTENTION_OVERRIDE's docstring), so the
#: calibration has to be stated, not just hoped for. Deliberately not
#: softened into "probably nothing, ignore it": the point is what a red
#: mark means, not talking the reader out of looking.
_RED_NOTE = (
    "About the red marks: they mean \"look at this one first\", not \"something is "
    "wrong.\" This is a text search with no understanding of what the code actually "
    "does, and on a normal install, red matches often turn out — on a closer look — "
    "to be ordinary code that merely resembles the pattern being searched for."
)


def _copy_for(rule_id: str) -> dict[str, str]:
    """Plain-language copy for one rule id, degrading rather than crashing.

    Deliberately does not fall back to the finding's own ``title``/``why``:
    those are written for the verbose/expert view (they name functions and
    quote code) and printing them here would reintroduce the jargon this
    view exists to remove.
    """
    return RULE_COPY.get(rule_id, _GENERIC_COPY)


def _findings_by_pack(report) -> dict[str, list[dict]]:
    by_pack: dict[str, list[dict]] = {}
    for finding in (report.get("scan") or {}).get("findings") or []:
        by_pack.setdefault(finding.get("pack") or "", []).append(finding)
    return by_pack


def _attention_tier(finding: dict) -> str:
    """How much attention ONE finding is worth, per ``ATTENTION_OVERRIDE``
    rather than the scanner's raw ``severity`` — see that table's
    docstring for why the two need to diverge."""
    rule_id = finding.get("rule_id") or ""
    if rule_id in ATTENTION_OVERRIDE:
        return ATTENTION_OVERRIDE[rule_id]
    return _SEVERITY_FALLBACK.get(finding.get("severity"), "green")


def _pack_color(findings: list[dict]) -> str:
    """Red if anything curated red matched, yellow if only yellow did,
    green otherwise — including when only near-universal capabilities
    (starts a program, takes a filename over the network, uses the
    network) matched, since those describe what the pack ordinarily does
    rather than anything worth a second look."""
    tiers = {_attention_tier(f) for f in findings}
    if "red" in tiers:
        return "red"
    if "yellow" in tiers:
        return "yellow"
    return "green"


def _grouped_by_tier(findings: list[dict], tier: str) -> dict[str, int]:
    """rule id -> occurrence count, restricted to one attention tier, in
    first-seen order. ``findings`` arrives worst-first (severity, then
    pack/path/line — see ``scanner.sort_findings``), so grouping preserves
    a sensible read order within the tier even though the tier itself no
    longer tracks severity one-for-one."""
    grouped: dict[str, int] = {}
    for finding in findings:
        if _attention_tier(finding) != tier:
            continue
        rule_id = finding.get("rule_id") or ""
        grouped[rule_id] = grouped.get(rule_id, 0) + 1
    return grouped


def _scan_was_run(report) -> bool:
    """Whether the source-scan lens actually ran this pass.

    A narrower ``scope`` (e.g. "routes only") leaves ``findings`` empty for
    a reason that has nothing to do with the installed packs' code — and a
    green verdict in that case would say "nothing found" about a scan that
    never happened. ``packs_scanned`` is only populated when the scan lens
    runs, so its absence (with packs actually installed) is the tell.
    """
    scan = report.get("scan") or {}
    packs = report.get("packs") or []
    return bool(scan.get("packs_scanned")) or not packs


def render_summary(report) -> str:
    """The plain-language view: an overall verdict, one coloured line per
    installed pack, and — for anything not green — a short, jargon-free
    explanation of what was noticed, what it would mean if it turned out
    to matter, and what to do about it. No rule ids, file paths or line
    numbers; ``render_text`` (via ``verbose=True``) is where those live.

    Pure and total: every branch below is reachable with zero packs, zero
    findings, an unrecognised rule id, or a scope that skipped the scan
    entirely, because a report that broke would be worse than one that
    under-informs.
    """
    lines: list[str] = []
    by_pack = _findings_by_pack(report)
    packs = sorted(set(report.get("packs") or []) | set(by_pack))

    if not packs:
        lines.append(f"{_DOT['none']} No other packs are installed — nothing to check.")
        lines.append("")
        lines.append(_HONEST_NOTE)
        return "\n".join(lines)

    if not _scan_was_run(report):
        lines.append(
            f"{_DOT['none']} The code check didn't run this pass — a narrower option "
            "was chosen above. Pick 'everything' or 'source scan only' to see this "
            "summary filled in."
        )
        lines.append("")
        lines.append("Packs installed: " + ", ".join(packs))
        lines.append("")
        lines.append(_HONEST_NOTE)
        return "\n".join(lines)

    colors = {pack: _pack_color(by_pack.get(pack, [])) for pack in packs}
    overall = "red" if "red" in colors.values() else "yellow" if "yellow" in colors.values() \
        else "green"

    lines.append(f"{_DOT[overall]} OVERALL — {_ATTENTION[overall]}.")
    lines.append(_HONEST_NOTE)
    if overall == "red":
        lines.append(_RED_NOTE)
    lines.append("")
    for pack in packs:
        color = colors[pack]
        lines.append(f"{_DOT[color]} {pack} — {_ATTENTION[color]}")
        capabilities = _grouped_by_tier(by_pack.get(pack, []), "green")
        if capabilities:
            # Named, not hidden: a near-universal capability is real
            # information ("this is what the pack does"), it just isn't
            # worth a colour on its own — see ATTENTION_OVERRIDE.
            phrases = " ".join(_copy_for(rule_id)["what"] for rule_id in capabilities)
            lines.append(f"    Also, ordinary for a pack like this: {phrases}")

    attention_needed = [pack for pack in packs if colors[pack] != "green"]
    for pack in attention_needed:
        lines.append("")
        lines.append(f"--- {pack} ---")
        # Red first, then yellow — the curated reading order, which no longer
        # tracks the scanner's raw severity one-for-one (see ATTENTION_OVERRIDE).
        worth_a_look = {
            **_grouped_by_tier(by_pack.get(pack, []), "red"),
            **_grouped_by_tier(by_pack.get(pack, []), "yellow"),
        }
        for rule_id, count in worth_a_look.items():
            copy = _copy_for(rule_id)
            where = "one place" if count == 1 else f"{count} places"
            lines.append(f"  Noticed, in {where}: {copy['what']}")
            lines.append(f"    If that turns out to matter: {copy['meaning']}")
            lines.append(f"    What to do: {copy['action']}")

    lines.append("")
    lines.append(
        "Turn on 'verbose' above for the technical version of this report — exact "
        "files, line numbers, and which rule matched each one."
    )
    return "\n".join(lines)


def render(report, *, verbose: bool = False) -> str:
    """The node's STRING output. Off (default): the plain-language summary
    above. On: today's full technical report, byte-for-byte unchanged —
    pinned by a test so the expert view never quietly drifts while this
    file evolves."""
    return render_text(report) if verbose else render_summary(report)
