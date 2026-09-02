"""Tests for the plain-language ("verbose=False") view of the EPS Node Audit.

`report.render_text` is the pre-existing expert view (rule ids, file paths,
line numbers) and must not move a single byte — that's pinned here, not in
`test_eps_audit.py`, so this file can evolve the readable view without ever
touching the file the other concurrent session owns.

Findings are built directly as `scanner.Finding` instances rather than by
running the scanner over real source text: `scanner.RULES` is being tuned
concurrently by another session, so a test that depended on a specific regex
matching a specific line would be coupled to work-in-progress that has
nothing to do with this file's job (turning a finding into words, given one
already exists). `build_report` only ever reads attributes off a Finding —
rule_id, severity, title, why, pack, path, line, excerpt — so handing it
literal, made-up values exercises the presentation layer in isolation.
"""

from __future__ import annotations

from eps_audit import observers, report
from eps_audit.nodes_audit import EPSNodeAudit
from eps_audit.scanner import Finding


def _finding(rule_id="path.unclamped_join", severity="high", pack="packA", **overrides):
    fields = dict(
        rule_id=rule_id, severity=severity, title="a title", why="a why",
        pack=pack, path="m.py", line=7, excerpt="p = os.path.join(input_dir, rel)",
    )
    fields.update(overrides)
    return Finding(**fields)


def _build(findings=(), packs=None, scanned_packs=None, routes=None, hooks=None):
    """A `build_report` call with sensible defaults, so each test only
    states the part of the shape it actually cares about."""
    findings = list(findings)
    if packs is None:
        packs = sorted({f.pack for f in findings}) or ["solo_pack"]
    if scanned_packs is None:
        scanned_packs = list(packs)  # the common case: the scan lens ran
    return report.build_report(
        routes=routes or [],
        egress=observers.EgressLog(),
        findings=findings,
        packs=packs,
        roots=["/root"],
        hooks=hooks or {},
        scanned_packs=scanned_packs,
    )


# ------------------------------------------------------------- verbose=True


class TestVerboseIsUnchanged:
    """The whole point of `verbose`: flipping it on must reproduce exactly
    what this node has always printed, for every shape the report can take."""

    def test_empty_report(self) -> None:
        rep = _build(packs=[], scanned_packs=[])
        assert report.render(rep, verbose=True) == report.render_text(rep)

    def test_report_with_findings_across_severities(self) -> None:
        findings = [
            _finding(rule_id="path.unclamped_join", severity="high", pack="packA"),
            _finding(rule_id="route.body_path", severity="medium", pack="packA"),
            _finding(rule_id="net.client", severity="note", pack="packB"),
        ]
        rep = _build(findings)
        assert report.render(rep, verbose=True) == report.render_text(rep)

    def test_report_with_an_unrecognised_rule_id(self) -> None:
        rep = _build([_finding(rule_id="brand.new.rule", severity="high")])
        assert report.render(rep, verbose=True) == report.render_text(rep)

    def test_report_with_routes_and_hooks(self) -> None:
        rep = _build(
            routes=[{"method": "POST", "path": "/x", "pack": "packA", "file": "f.py"}],
            hooks={"aiohttp": True, "requests": False},
        )
        assert report.render(rep, verbose=True) == report.render_text(rep)


# ------------------------------------------------------------ verbose=False


class TestDefaultViewIsPlainLanguage:
    def test_no_rule_ids_paths_or_line_numbers(self) -> None:
        rep = _build([_finding(
            rule_id="path.unclamped_join", severity="high", pack="packA",
            path="super_secret_module.py", line=4242,
            excerpt="p = os.path.join(input_dir, rel)",
        )])
        text = report.render(rep, verbose=False)
        assert "path.unclamped_join" not in text
        assert "super_secret_module.py" not in text
        assert "4242" not in text
        assert "os.path.join(input_dir, rel)" not in text

    def test_zero_findings_render_a_clean_green_pack(self) -> None:
        rep = _build([], packs=["only_pack"])
        text = report.render(rep, verbose=False)
        assert "only_pack" in text
        assert "\U0001f7e2" in text  # green dot somewhere
        assert "\U0001f534" not in text and "\U0001f7e1" not in text

    def test_zero_packs_render_without_crashing(self) -> None:
        rep = _build([], packs=[], scanned_packs=[])
        text = report.render(rep, verbose=False)
        assert "nothing to check" in text.lower()

    def test_scan_not_run_this_pass_is_stated_plainly(self) -> None:
        # scope="routes only" (say) leaves packs_scanned empty even though
        # packs are installed — the report must say so, not claim green.
        rep = _build([], packs=["packA"], scanned_packs=[])
        text = report.render(rep, verbose=False)
        assert "didn't run this pass" in text
        assert "\U0001f534" not in text and "\U0001f7e1" not in text and \
            "\U0001f7e2" not in text


class TestVerdictColors:
    """Colour comes from `report.ATTENTION_OVERRIDE`, a curation on top of
    the scanner's raw `severity` — not severity passed straight through.
    That curation is the whole point of the 2026-09-02 rebalance: the
    owner's own 5-pack install came back all red/yellow off raw severity
    alone, which told him nothing, because `proc.spawn` (starts another
    program) and `route.body_path` (takes a filename over the network)
    are near-universal capabilities, not rare shapes worth a colour."""

    def test_all_green_when_nothing_matched(self) -> None:
        rep = _build([_finding(severity="note", rule_id="net.client")], packs=["packA"])
        text = report.render(rep, verbose=False)
        assert "\U0001f7e2 OVERALL" in text

    def test_near_universal_capabilities_alone_are_green(self) -> None:
        # The exact regression this rebalance exists to prevent: a pack
        # whose ONLY findings are the common, on-purpose capabilities
        # (shells out to a companion app, serves a file over its own
        # routes) must not be painted yellow or red for doing so.
        rep = _build([
            _finding(severity="medium", rule_id="proc.spawn", pack="packA"),
            _finding(severity="medium", rule_id="route.body_path", pack="packA"),
            _finding(severity="note", rule_id="net.client", pack="packA"),
        ])
        text = report.render(rep, verbose=False)
        assert "\U0001f7e2 packA" in text
        assert "\U0001f534" not in text and "\U0001f7e1" not in text
        # Named, not hidden: the capability still shows up as information.
        assert "Also, ordinary for a pack like this" in text
        assert "start another program" in text

    def test_curated_yellow_overrides_a_raw_high_severity(self) -> None:
        # path.unclamped_join is scanner-severity "high" but curated
        # yellow: worth a glance, not the reader's first stop.
        rep = _build([_finding(severity="high", rule_id="path.unclamped_join")])
        text = report.render(rep, verbose=False)
        assert "\U0001f7e1 OVERALL" in text
        assert "\U0001f534 OVERALL" not in text

    def test_red_reserved_for_the_curated_red_rules(self) -> None:
        rep = _build([
            _finding(severity="medium", rule_id="proc.spawn", pack="packA"),
            _finding(severity="high", rule_id="secret.env_to_dynamic_host", pack="packB"),
        ])
        text = report.render(rep, verbose=False)
        assert "\U0001f534 OVERALL" in text

    def test_one_red_pack_does_not_paint_a_clean_pack_red(self) -> None:
        rep = _build([
            _finding(severity="high", rule_id="route.body_url", pack="packBad"),
        ], packs=["packBad", "packClean"])
        text = report.render(rep, verbose=False)
        assert "\U0001f534 packBad" in text
        assert "\U0001f7e2 packClean" in text


class TestUnknownRuleDegrades:
    def test_unrecognised_id_gets_the_generic_phrase_not_the_raw_id(self) -> None:
        rep = _build([_finding(rule_id="totally.made.up.rule.2026", severity="high")])
        text = report.render(rep, verbose=False)
        assert "totally.made.up.rule.2026" not in text
        assert report._GENERIC_COPY["what"] in text

    def test_a_missing_rule_id_also_degrades(self) -> None:
        rep = _build([_finding(rule_id="", severity="high")])
        text = report.render(rep, verbose=False)  # must not raise
        assert report._GENERIC_COPY["what"] in text


class TestRedCalibration:
    """Red is rare by design, and this scanner's own `high` findings have
    turned out wrong on inspection (a GVFS-mount-name formatter and core
    SaveImage's own filename pattern both tripped `route.body_url` /
    `path.unclamped_join` in this very pack). So a red badge must carry
    its own calibration, right where the reader will see it — not just
    the general "these are leads" note every colour gets."""

    def test_red_note_appears_when_overall_is_red(self) -> None:
        rep = _build([_finding(severity="high", rule_id="route.body_url")])
        text = report.render(rep, verbose=False)
        assert "look at this one first" in text
        assert "not \"something is wrong" in text

    def test_red_note_is_absent_when_nothing_is_red(self) -> None:
        yellow = _build([_finding(severity="high", rule_id="path.unclamped_join")])
        green = _build([_finding(severity="note", rule_id="net.client")])
        assert "look at this one first" not in report.render(yellow, verbose=False)
        assert "look at this one first" not in report.render(green, verbose=False)


class TestWordingGuard:
    """Pins the honesty requirement itself: a red badge must never read as
    an accusation against a real, named third-party project. If a future
    edit reintroduces this vocabulary one word at a time, this is what
    catches it."""

    BANNED = (
        "malicious", "unsafe", "dangerous", "danger", "compromise", "compromised",
        "vulnerab", "threat", "risk", "exploit", "hacker", "attack",
        "ssrf", "travers", "sink", "egress",
    )

    def test_default_view_avoids_accusatory_vocabulary(self) -> None:
        # One finding per severity, spread across two packs, so both the
        # red and yellow branches (and their explanatory blocks) render.
        findings = [
            _finding(rule_id="path.unclamped_join", severity="high", pack="packA"),
            _finding(rule_id="path.startswith_check", severity="high", pack="packA"),
            _finding(rule_id="route.body_url", severity="high", pack="packA"),
            _finding(rule_id="secret.env_to_dynamic_host", severity="high", pack="packA"),
            _finding(rule_id="exec.dynamic", severity="high", pack="packA"),
            _finding(rule_id="route.body_path", severity="medium", pack="packB"),
            _finding(rule_id="write.binary", severity="medium", pack="packB"),
            _finding(rule_id="proc.spawn", severity="medium", pack="packB"),
            _finding(rule_id="net.client", severity="note", pack="packC"),
            _finding(rule_id="route.registers", severity="note", pack="packC"),
            _finding(rule_id="an.unrecognised.rule", severity="high", pack="packD"),
        ]
        text = report.render(_build(findings), verbose=False).lower()
        for word in self.BANNED:
            assert word not in text, f"banned word {word!r} leaked into the default view"

    def test_rule_copy_table_itself_avoids_the_vocabulary(self) -> None:
        # Direct check on the data, not just one rendered sample — so an
        # entry added for a future rule id is guarded even before anything
        # exercises it through render().
        values = []
        for entry in report.RULE_COPY.values():
            values.extend(entry.values())
        values.extend(report._GENERIC_COPY.values())
        values.extend(report._ATTENTION.values())
        values.append(report._HONEST_NOTE)
        blob = " ".join(values).lower()
        for word in self.BANNED:
            assert word not in blob, f"banned word {word!r} in RULE_COPY / shared copy"


# --------------------------------------------------------------- the node


class TestNodeWiring:
    """`EPSNodeAudit` itself: the `verbose` widget must actually select
    between the two renderers, and must move `IS_CHANGED` so toggling the
    checkbox alone still triggers a repaint."""

    def test_execute_defaults_to_the_plain_view(self) -> None:
        result = EPSNodeAudit().execute()
        text = result["result"][0]
        assert "OVERALL" in text or "nothing to check" in text.lower()

    def test_execute_verbose_true_matches_render_text_shape(self) -> None:
        result = EPSNodeAudit().execute(verbose=True)
        text = result["result"][0]
        assert text.startswith("EPS NODE AUDIT —")

    def test_is_changed_moves_with_verbose_alone(self) -> None:
        off = EPSNodeAudit.IS_CHANGED("everything", True, verbose=False)
        on = EPSNodeAudit.IS_CHANGED("everything", True, verbose=True)
        assert off != on
