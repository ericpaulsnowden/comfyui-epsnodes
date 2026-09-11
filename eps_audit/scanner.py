"""Static scan of installed pack source for a few recurring risky idioms.

Written after auditing four third-party packs by hand (2026-08-15 → 08-28)
and finding, across them, not four unrelated bugs but the SAME two mistakes
repeated: a path resolver that falls back to joining a caller-supplied
string onto a base directory (an absolute string then replaces that
directory outright), and an unauthenticated route that forwards a
caller-supplied URL — in one pack carrying an API key from the environment
to whatever host the caller named. One pack had inherited the first from
another by being a fork of it. Idioms recur because packs are forked,
copy-pasted, and written from the same handful of examples, so a scan for
the idiom finds the next occurrence before anyone reads that pack.

**These are LEADS, not verdicts.** The rules are regex heuristics over
source text with no dataflow analysis behind them, tuned to over-report
rather than miss: ``EPS Frame Saver`` in this very pack trips
``route.body_path`` and is perfectly correct, because it validates the path
it is handed. A finding means "a human should look at this line", which is
exactly the triage step that otherwise never happens. The report says so in
its own footer; do not let this file's output be quoted as a vulnerability
count.

Pure: takes text (or a directory) and returns findings, imports nothing from
ComfyUI, and is tested directly.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

#: Ranked worst-first for reporting. Not a CVSS — just the order a human
#: should read them in.
SEVERITY_ORDER = {"high": 0, "medium": 1, "note": 2}

#: Files bigger than this are skipped: a multi-megabyte .py is generated or
#: vendored, and scanning it costs more than it tells anyone.
MAX_FILE_BYTES = 2_000_000

#: Never descended into — vendored/third-party trees whose findings are not
#: the pack author's code and not actionable by the user.
SKIP_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", "venv", ".venv", "env",
    "site-packages", "dist-packages", ".mypy_cache", ".pytest_cache", "build", "dist",
    # Test trees are not imported by ComfyUI, so they are not attack surface —
    # and a test suite for a scanner like this one is FULL of deliberately
    # written bad idioms, which is how the first live run flagged this pack's
    # own test fixtures as high findings (2026-08-28).
    "tests", "test",
})


@dataclass(frozen=True)
class Rule:
    """One idiom worth a human's attention.

    *needs* are substrings that must appear ANYWHERE in the same file for
    the rule to fire — the cheap stand-in for dataflow this module does not
    do. ``route.body_url`` only matters in a file that both registers a
    route and makes an outbound call, and requiring both in the file keeps
    the rule off the many places a ``url`` key is read for innocent reasons.
    """

    id: str
    severity: str
    title: str
    why: str
    pattern: re.Pattern
    needs: tuple[str, ...] = ()
    #: A line that also matches this is NOT a finding — the cheap way to
    #: carve one well-understood benign shape out of an otherwise useful
    #: rule, rather than dropping the rule or drowning it in noise.
    exclude: re.Pattern | None = None
    #: Skip a join, INSIDE the body of a loop over a directory LISTING, whose
    #: joined name is that loop's variable and has not been reassigned since.
    #: `exclude` only sees one line, but the listing is routinely the line
    #: above:
    #:     for name in sorted(os.listdir(root)):
    #:         adir = os.path.join(root, name)
    #: which is enumeration, not resolution — the names came from `root`.
    #: (ComfyUI-VDN-H3 spec.py:323, 2026-09-09.) Scoped by `_ListingScopes`:
    #: never file-wide, because `name` is reused everywhere.
    skip_listing_vars: bool = False


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: str
    title: str
    why: str
    pack: str
    path: str
    line: int
    excerpt: str


#: The rule table. Ordered high → note purely for readability.
RULES: tuple[Rule, ...] = (
    Rule(
        id="path.unclamped_join",
        severity="high",
        title="Path joined onto a base directory without clamping",
        why=(
            "os.path.join(base, value) returns VALUE ALONE when value is absolute, and "
            "silently escapes with '..' — so a caller-supplied string can address any "
            "file on the machine. Clamp with realpath + os.path.commonpath."
        ),
        pattern=re.compile(
            r"os\.path\.join\(\s*[A-Za-z_][\w.]*(?:dir|dir_|root|base|folder)\w*\s*,"
            r"\s*(?!os\.path\.basename)[A-Za-z_][\w.]*\s*\)"
        ),
        # Two shapes that look identical to a regex but cannot escape:
        #   - joining each name from a LISTING back onto the directory it was
        #     listed from (the names came from that directory);
        #   - joining an ALL-CAPS CONSTANT, Python's convention for a literal
        #     fixed at import — `os.path.join(models_dir, _MODEL_FOLDER)` is
        #     how a pack registers its own model directory, and flagging it
        #     was this rule's last false-positive class (2026-09-02).
        exclude=re.compile(
            r"os\.listdir\(|os\.scandir\(|glob\.|\.iterdir\(|"
            r"os\.path\.join\([^,]+,\s*_?[A-Z][A-Z0-9_]*\s*\)"
        ),
        skip_listing_vars=True,
    ),
    Rule(
        id="path.startswith_check",
        severity="high",
        title="Directory containment checked with startswith()",
        why=(
            "'/data/input_backup' startswith '/data/input', so a sibling directory whose "
            "name merely shares the prefix passes the check. Use os.path.commonpath, or "
            "compare with a trailing separator appended."
        ),
        pattern=re.compile(
            r"\.startswith\(\s*(?:os\.path\.(?:abs|real)path\(|[A-Za-z_][\w.]*"
            r"(?:dir|root|base|folder)\w*\s*\))"
        ),
        # `os.pardir` ends in "dir" but names "..", and `os.sep` a separator:
        # checking a string against either is not a containment check at all.
        exclude=re.compile(r"startswith\(\s*os\.(?:pardir|sep|curdir|extsep|altsep)"),
    ),
    Rule(
        id="route.body_url",
        severity="high",
        title="Route forwards a caller-supplied URL",
        why=(
            "An unauthenticated endpoint that fetches a URL from the request body is a "
            "server-side request proxy: anyone who can reach this server can use it to "
            "probe hosts it can reach and read the replies. Restrict to an allowlist."
        ),
        # The RECEIVER must look like request data, not just any dict with a
        # `server`/`host`/`url` key. Without this the rule fired on
        # `fields.get("server")` in this pack's own `_mount_label()` -- a pure
        # string formatter that parses a GVFS mount name
        # (`smb-share:server=HOST,share=NAME`) and never touches the network.
        # `needs` gates on the FILE registering a route and importing aiohttp,
        # which routes.py legitimately does, so the file-level gate can't tell
        # a local dict apart from a parsed body; only the receiver's name can.
        # Verified false positive, 2026-09-02 -- it was the single finding
        # painting this pack red in the §9 summary, which is exactly the kind
        # of miscalibration that makes a red mark stop meaning anything.
        pattern=re.compile(
            r"(?:body|payload|data|params|json|req|request|args|form|kwargs)\w*"
            r"\.get\(\s*[\"'](?:base_url|url|endpoint|host|server|target)[\"']"
        ),
        needs=("routes.post", "aiohttp"),
    ),
    Rule(
        id="secret.env_to_dynamic_host",
        severity="high",
        title="Environment secret sent to a non-literal host",
        why=(
            "An API key read from the environment and attached as a Bearer token to a "
            "request whose base URL is a variable can be delivered to whatever host that "
            "variable names. Bind the credential to its own provider's host."
        ),
        pattern=re.compile(r"[\"']Authorization[\"']\s*:\s*f?[\"']Bearer"),
        needs=("os.environ", "base_url"),
    ),
    Rule(
        id="exec.dynamic",
        severity="high",
        title="Code executed from a string or a file path",
        why=(
            "eval/exec/pickle/marshal and spec_from_file_location run code chosen at "
            "runtime. Sometimes legitimate (loading a sibling pack's module); always "
            "worth reading, because it is how untrusted input becomes execution."
        ),
        pattern=re.compile(
            r"(?<![\w.])(?:eval|exec)\(|pickle\.loads?\(|marshal\.loads\(|"
            r"spec_from_file_location\("
        ),
    ),
    Rule(
        id="route.body_path",
        severity="medium",
        title="Route takes a filesystem path from the request",
        why=(
            "Fine when the path is clamped to a known directory before use — worth "
            "confirming that it is. Read together with any path.* finding in the same "
            "file, which is the pair that turns this into a file-read primitive."
        ),
        pattern=re.compile(
            r"\.get\(\s*[\"'](?:file|path|filename|filepath|audio|video|image|dir)[\"']"
        ),
        needs=("routes.post", "routes.get"),
    ),
    Rule(
        id="write.binary",
        severity="medium",
        title="Binary file write",
        why=(
            "Check what constrains the destination name and extension, and whether an "
            "existing file is silently overwritten."
        ),
        pattern=re.compile(r"open\(\s*[^,\n]+,\s*[\"'](?:wb|ab|w\+b)[\"']"),
        needs=("routes.post",),
    ),
    Rule(
        id="proc.spawn",
        severity="medium",
        title="Child process spawned",
        why="Check that no part of the command line comes from a request or a workflow.",
        pattern=re.compile(r"\bsubprocess\.|\bos\.system\(|\bos\.popen\("),
    ),
    Rule(
        id="net.client",
        severity="note",
        title="Outbound HTTP client",
        why=(
            "Informational: this pack can talk to the network. The egress lens shows "
            "where it actually went since this server started."
        ),
        pattern=re.compile(
            r"aiohttp\.ClientSession|requests\.(?:get|post|put|Session)\(|"
            r"urllib\.request\.urlopen\("
        ),
    ),
    Rule(
        id="route.registers",
        severity="note",
        title="Registers an HTTP route",
        why=(
            "Informational: this pack adds endpoints to the ComfyUI server, live for "
            "anyone who can reach it. The routes lens lists them as actually registered."
        ),
        pattern=re.compile(r"routes\.(?:get|post|put|delete|patch)\(|add_route\("),
    ),
)


#: A `for` STATEMENT over a directory listing -- the binding that makes a join
#: over its variable, inside its body, pure enumeration. Matched against the
#: stripped line, so a comprehension mid-line never opens a scope (its variable
#: does not leak, and a one-line comprehension join already hits `exclude`).
#: Deliberately loose about what sits between the `in` and the call.
_LISTING_LOOP_RE = re.compile(
    r"(?:async\s+)?for\s+([A-Za-z_]\w*)\s+in\s+[^\n:]*"
    r"(?:os\.listdir\(|os\.scandir\(|glob\.glob\(|glob\.iglob\(|\.iterdir\()"
)

#: The targets of any `for` statement, listing or not.
_FOR_TARGETS_RE = re.compile(r"(?:async\s+)?for\s+(.+?)\s+in\b")

#: A nested function or class has its own names, so no enclosing loop's
#: variable reaches a join inside it.
_NEW_NAMESPACE_RE = re.compile(r"(?:async\s+)?def\s|class\s")

#: A join whose second argument is a plain identifier, matched against ONE
#: rule match so a line holding two joins is judged join by join.
_JOIN_SECOND_ARG_RE = re.compile(r"os\.path\.join\([^,]+,\s*([A-Za-z_]\w*)\s*\)")


def _indent_of(line: str) -> int:
    expanded = line.expandtabs()
    return len(expanded) - len(expanded.lstrip())


def _rebind_re(var: str) -> re.Pattern:
    """A line that gives *var* a new value, so it no longer holds a listed name."""
    v = re.escape(var)
    return re.compile(
        # Plain, chained, tuple-unpacking, annotated or augmented assignment.
        # Anchored to the statement start so `f(name=x)` is not a rebind.
        rf"^(?:[\w.\[\]'\"]+\s*=(?!=)\s*)*\(?\s*(?:\*?[\w.]+\s*,\s*)*\*?{v}\s*"
        rf"(?:,\s*\*?[\w.]+\s*)*\)?\s*(?::[^=]*)?(?:[-+*/%&|^@]|//|\*\*|>>|<<)?=(?!=)"
        rf"|\b{v}\s*:="    # walrus
        rf"|\bas\s+{v}\b"  # with / except / import ... as
    )


@dataclass
class _Scope:
    """One open block that changes what a join inside it means.

    *var* is the listing loop's variable, or None for a nested def/class,
    which hides every enclosing loop's variable.
    """

    indent: int
    var: str | None
    rebind: re.Pattern | None = None
    rebound: bool = False


class _ListingScopes:
    """Which names are, right now, a name read out of a directory listing.

    The exemption is scoped to the loop that did the listing: it holds only
    on the loop's indented body and ends the moment the name is given any
    other value. The first version collected loop names FILE-WIDE, so any
    later join on a reused identifier (`name` is everywhere) went silent,
    including a request handler's parameter in a different function
    (lead review, 2026-09-10). Wherever the shape is in doubt (a dedented
    continuation line, a keyword argument that looks like an assignment),
    the skip is dropped and the join is reported. For a scanner, a spare
    finding is the safe way to be wrong.
    """

    def __init__(self) -> None:
        self._stack: list[_Scope] = []

    def enter_line(self, line: str) -> None:
        """Close every block this line has dedented out of."""
        if not self._stack:
            return  # the common case: no listing loop open, nothing to measure
        indent = _indent_of(line)
        while self._stack and self._stack[-1].indent >= indent:
            self._stack.pop()

    def is_listed_name(self, var: str) -> bool:
        """Whether a join over *var*, on the current line, is enumeration."""
        for scope in reversed(self._stack):
            if scope.var is None:
                return False  # inside a def/class nested in the loop
            if scope.var == var:
                return not scope.rebound
        return False

    def leave_line(self, line: str, stripped: str) -> None:
        """Apply what this line binds, after its joins were judged.

        Rebinding is applied AFTER the check because Python evaluates the
        right-hand side first: in `f = os.path.join(folder, f)` the join
        still sees the listed name, and that is a common, safe idiom.
        """
        if self._stack:
            loop_targets = _FOR_TARGETS_RE.match(stripped)
            # A trailing comma marks an argument/element continuation line
            # (`name=value,` inside a call), never a plausible assignment.
            assigns = not stripped.endswith(",")
            for scope in self._stack:
                if scope.var is None or scope.rebound:
                    continue
                if (
                    (loop_targets and re.search(rf"\b{scope.var}\b", loop_targets.group(1)))
                    or (assigns and scope.rebind.search(stripped))
                ):
                    scope.rebound = True
        listing = _LISTING_LOOP_RE.match(stripped)
        if listing is not None:
            var = listing.group(1)
            self._stack.append(_Scope(_indent_of(line), var, _rebind_re(var)))
        elif self._stack and _NEW_NAMESPACE_RE.match(stripped):
            self._stack.append(_Scope(_indent_of(line), None))


def _is_finding(rule: Rule, line: str, scopes: _ListingScopes | None) -> bool:
    if scopes is None or "lambda" in line:
        # A lambda's parameters shadow the loop's name on its own line.
        return rule.pattern.search(line) is not None
    for match in rule.pattern.finditer(line):
        joined = _JOIN_SECOND_ARG_RE.fullmatch(match.group(0))
        if joined is None or not scopes.is_listed_name(joined.group(1)):
            return True
    return False


def scan_text(text: str, *, pack: str = "", path: str = "", rules=RULES) -> list[Finding]:
    """Findings for one file's *text*, in file order.

    ``needs`` is evaluated once against the whole text before any line is
    examined, so a rule whose prerequisites are absent costs one substring
    search rather than a match per line.
    """
    active = [rule for rule in rules if all(need in text for need in rule.needs)]
    if not active:
        return []
    scopes = _ListingScopes() if any(rule.skip_listing_vars for rule in active) else None
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue  # a comment quoting an idiom is not that idiom
        if scopes is not None:
            scopes.enter_line(line)
        for rule in active:
            if rule.exclude is not None and rule.exclude.search(line):
                continue
            if _is_finding(rule, line, scopes if rule.skip_listing_vars else None):
                findings.append(Finding(
                    rule_id=rule.id, severity=rule.severity, title=rule.title,
                    why=rule.why, pack=pack, path=path, line=number,
                    excerpt=stripped[:160],
                ))
        if scopes is not None:
            scopes.leave_line(line, stripped)
    return findings


def scan_pack(pack_dir: str, *, pack: str = "", rules=RULES) -> list[Finding]:
    """Findings for every ``.py`` under *pack_dir*, skipping vendored trees.

    Unreadable files are skipped silently rather than failing the scan: one
    permission error in one pack must not cost the report every other pack.
    """
    pack = pack or os.path.basename(os.path.normpath(pack_dir))
    findings: list[Finding] = []
    for dirpath, dirnames, filenames in os.walk(pack_dir):
        # Hidden directories are skipped WHOLESALE, not just the named ones:
        # ComfyUI does not import them, and the first live run found 14
        # "high" findings inside ComfyUI-DaSiWa-Nodes/.tests/ alone.
        dirnames[:] = [
            d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
        ]
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            full = os.path.join(dirpath, name)
            try:
                if os.path.getsize(full) > MAX_FILE_BYTES:
                    continue
                with open(full, "r", encoding="utf-8", errors="replace") as handle:
                    text = handle.read()
            except OSError:
                continue
            relative = os.path.relpath(full, pack_dir)
            findings.extend(scan_text(text, pack=pack, path=relative, rules=rules))
    return findings


def sort_findings(findings) -> list[Finding]:
    """Worst first, then by pack, file and line — the reading order."""
    return sorted(findings, key=lambda f: (
        SEVERITY_ORDER.get(f.severity, 9), f.pack, f.path, f.line,
    ))


def count_by_severity(findings) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    return counts
