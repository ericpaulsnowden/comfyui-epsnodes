"""Tests for the EPS Node Audit family (FORMAT.md §9).

Everything except the route is pure, so it is driven directly with strings
and fakes; the route is exercised the way ``test_routes_list_flags.py``
does it — ``build_routes()`` inside a plain aiohttp app, no ComfyUI.

The scanner tests deliberately use the REAL idioms found by hand in four
third-party packs between 2026-08-15 and 08-28 (see ``scanner.py``'s
docstring). If a future rewrite of the rules stops matching these exact
lines, the scan has lost the cases it was built for.
"""

from __future__ import annotations

import os
import sys
import types

import pytest
from aiohttp import web

from eps_audit import attribution, audit, observers, report, scanner
from eps_audit.routes_audit import build_routes

# ------------------------------------------------------------- attribution


class TestAttribution:
    def test_file_maps_to_the_installable_folder_not_the_deepest_package(self, tmp_path) -> None:
        root = attribution.normalize_root(str(tmp_path))
        deep = tmp_path / "somepack" / "js" / "sub" / "mod.py"
        deep.parent.mkdir(parents=True)
        deep.write_text("x", encoding="utf-8")
        assert attribution.pack_of_path(str(deep), [root]) == "somepack"

    def test_paths_outside_every_root_are_unattributed(self, tmp_path) -> None:
        root = attribution.normalize_root(str(tmp_path / "custom_nodes"))
        (tmp_path / "custom_nodes").mkdir()
        outside = tmp_path / "elsewhere" / "core.py"
        outside.parent.mkdir()
        outside.write_text("x", encoding="utf-8")
        assert attribution.pack_of_path(str(outside), [root]) is None

    def test_a_symlinked_pack_still_attributes(self, tmp_path) -> None:
        # The owner's rig symlinks this very repo into custom_nodes, so a
        # non-resolving comparison would report None for the whole pack.
        real = tmp_path / "real_repo"
        (real / "pkg").mkdir(parents=True)
        module = real / "pkg" / "mod.py"
        module.write_text("x", encoding="utf-8")
        nodes_dir = tmp_path / "custom_nodes"
        nodes_dir.mkdir()
        try:
            os.symlink(real, nodes_dir / "linked_pack")
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unavailable on this platform/user")
        root = attribution.normalize_root(str(nodes_dir))
        assert attribution.pack_of_path(str(nodes_dir / "linked_pack" / "pkg" / "mod.py"), [root]) \
            == "linked_pack"

    @pytest.mark.parametrize("value", ["", None])
    def test_empty_paths_are_safe(self, value) -> None:
        assert attribution.pack_of_path(value, []) is None


# ----------------------------------------------------------------- scanner


class TestScannerRules:
    def test_finds_the_unclamped_join_that_shipped_in_two_packs(self) -> None:
        text = "import os\ndef _resolve_path(rel):\n    p = os.path.join(input_dir, rel)\n    return p\n"
        ids = {f.rule_id for f in scanner.scan_text(text)}
        assert "path.unclamped_join" in ids

    def test_a_basenamed_join_is_not_flagged(self) -> None:
        text = "p = os.path.join(input_dir, os.path.basename(rel))\n"
        assert [f for f in scanner.scan_text(text) if f.rule_id == "path.unclamped_join"] == []

    def test_finds_the_startswith_containment_check(self) -> None:
        text = "safe = os.path.abspath(os.path.join(base_dir, name))\n" \
               "if not safe.startswith(os.path.abspath(base_dir)):\n    return None\n"
        ids = {f.rule_id for f in scanner.scan_text(text)}
        assert "path.startswith_check" in ids

    def test_body_url_needs_a_route_and_a_client_in_the_same_file(self) -> None:
        bare = 'base_url = (data.get("base_url") or "").rstrip("/")\n'
        assert [f for f in scanner.scan_text(bare) if f.rule_id == "route.body_url"] == []
        with_context = (
            "import aiohttp\n"
            '@routes.post("/x")\n'
            "async def handler(request):\n"
            '    base_url = (data.get("base_url") or "").rstrip("/")\n'
        )
        ids = {f.rule_id for f in scanner.scan_text(with_context)}
        assert "route.body_url" in ids

    def test_finds_a_secret_bound_to_a_variable_host(self) -> None:
        text = (
            "api_key = os.environ.get('GEMINI_API_KEY')\n"
            "base_url = data.get('base_url')\n"
            'headers = {"Authorization": f"Bearer {api_key}"}\n'
        )
        ids = {f.rule_id for f in scanner.scan_text(text)}
        assert "secret.env_to_dynamic_host" in ids

    def test_startswith_against_os_pardir_is_not_a_containment_check(self) -> None:
        # `os.pardir` ends in "dir" but names ".."; this pack's own
        # attribution.py tripped the rule on the first live run.
        text = "if relative.startswith(os.pardir) or os.path.isabs(relative):\n"
        assert [f for f in scanner.scan_text(text) if f.rule_id == "path.startswith_check"] == []

    def test_finds_dynamic_execution(self) -> None:
        text = 'spec = importlib.util.spec_from_file_location(key, os.path.join(d, "learned.py"))\n'
        assert {f.rule_id for f in scanner.scan_text(text)} == {"exec.dynamic"}

    def test_model_eval_is_not_dynamic_execution(self) -> None:
        # First live run (2026-08-28): `\beval\(` matched `model.eval()`,
        # which is in every ML codebase, and produced 25 bogus high findings
        # across the owner's own installed packs.
        assert scanner.scan_text("model = build_model().eval()\n") == []
        assert scanner.scan_text("net.eval()\nother.exec()\n") == []

    def test_a_real_eval_is_still_caught(self) -> None:
        assert {f.rule_id for f in scanner.scan_text("out = eval(user_string)\n")} == {"exec.dynamic"}

    def test_joining_names_from_a_listing_is_not_a_traversal(self) -> None:
        # The names came from that directory, so they cannot escape it.
        text = "files = [f for f in os.listdir(input_dir) if os.path.isfile(os.path.join(input_dir, f))]\n"
        assert [f for f in scanner.scan_text(text) if f.rule_id == "path.unclamped_join"] == []

    def test_a_commented_out_idiom_is_not_a_finding(self) -> None:
        text = "# p = os.path.join(input_dir, rel)  <- the bug we fixed\n"
        assert scanner.scan_text(text) == []

    def test_findings_carry_pack_file_and_line(self) -> None:
        text = "\n\np = os.path.join(input_dir, rel)\n"
        found = scanner.scan_text(text, pack="somepack", path="mod.py")
        assert (found[0].pack, found[0].path, found[0].line) == ("somepack", "mod.py", 3)

    def test_sorting_is_worst_first(self) -> None:
        text = (
            "import aiohttp\n"
            "p = os.path.join(input_dir, rel)\n"
            "session = aiohttp.ClientSession()\n"
        )
        ordered = scanner.sort_findings(scanner.scan_text(text))
        assert ordered[0].severity == "high"
        assert ordered[-1].severity == "note"


class TestScanPack:
    def test_walks_a_directory_and_skips_vendored_trees(self, tmp_path) -> None:
        pack = tmp_path / "apack"
        (pack / "sub").mkdir(parents=True)
        (pack / "sub" / "a.py").write_text("p = os.path.join(input_dir, rel)\n", encoding="utf-8")
        vendored = pack / "node_modules"
        vendored.mkdir()
        (vendored / "b.py").write_text("p = os.path.join(input_dir, rel)\n", encoding="utf-8")
        findings = scanner.scan_pack(str(pack))
        assert [f.path for f in findings] == [os.path.join("sub", "a.py")]
        assert findings[0].pack == "apack"

    def test_test_trees_are_skipped(self, tmp_path) -> None:
        # A scanner's own test suite is full of deliberately bad idioms, and
        # test code is not imported by ComfyUI, so it is not attack surface.
        pack = tmp_path / "apack"
        (pack / "tests").mkdir(parents=True)
        (pack / "tests" / "t.py").write_text("p = os.path.join(input_dir, rel)\n", encoding="utf-8")
        assert scanner.scan_pack(str(pack)) == []

    def test_hidden_directories_are_skipped_wholesale(self, tmp_path) -> None:
        # ComfyUI-DaSiWa-Nodes keeps its suite in `.tests/`, which the plain
        # "tests" name missed — 14 bogus high findings on the first live run.
        pack = tmp_path / "apack"
        (pack / ".tests").mkdir(parents=True)
        (pack / ".tests" / "t.py").write_text("out = eval(s)\n", encoding="utf-8")
        assert scanner.scan_pack(str(pack)) == []

    def test_non_python_and_oversized_files_are_ignored(self, tmp_path) -> None:
        pack = tmp_path / "apack"
        pack.mkdir()
        (pack / "notes.md").write_text("p = os.path.join(input_dir, rel)\n", encoding="utf-8")
        assert scanner.scan_pack(str(pack)) == []


# ------------------------------------------------------------------- roots


class TestCustomNodesRoots:
    """The own-parent fallback is a LAST resort, not an addition.

    Regression pin for the first live run (2026-08-28): this pack is
    symlinked into the rig's custom_nodes from a Dropbox working copy, and
    an unconditional fallback appended that working copy's PARENT as a
    second root — so a sibling docs folder was reported as an installed
    node pack, and its source was scanned.
    """

    def test_fallback_is_skipped_when_comfyui_names_roots(self, monkeypatch, tmp_path) -> None:
        real = tmp_path / "custom_nodes"
        real.mkdir()
        fake = types.SimpleNamespace(get_folder_paths=lambda key: [str(real)])
        monkeypatch.setitem(sys.modules, "folder_paths", fake)
        assert audit.custom_nodes_roots() == [attribution.normalize_root(str(real))]

    def test_fallback_is_used_when_comfyui_names_none(self, monkeypatch) -> None:
        fake = types.SimpleNamespace(get_folder_paths=lambda key: [])
        monkeypatch.setitem(sys.modules, "folder_paths", fake)
        roots = audit.custom_nodes_roots()
        assert len(roots) == 1
        # ...and it is this pack's own parent, i.e. a real custom_nodes dir.
        assert os.path.basename(roots[0]) != "eps_audit"

    def test_duplicate_roots_are_collapsed(self, monkeypatch, tmp_path) -> None:
        real = tmp_path / "custom_nodes"
        real.mkdir()
        fake = types.SimpleNamespace(get_folder_paths=lambda key: [str(real), str(real)])
        monkeypatch.setitem(sys.modules, "folder_paths", fake)
        assert len(audit.custom_nodes_roots()) == 1


# --------------------------------------------------------------- observers


class TestEgressLog:
    def test_tallies_by_pack_and_host(self) -> None:
        log = observers.EgressLog()
        log.record("packA", "https", "example.com", "get")
        log.record("packA", "https", "example.com", "POST")
        log.record("packB", "http", "127.0.0.1", "GET")
        rows = log.snapshot()
        assert log.total_calls == 3
        assert rows[0] == {"pack": "packA", "scheme": "https", "host": "example.com",
                           "count": 2, "methods": ["GET", "POST"]}
        assert rows[1]["pack"] == "packB"

    def test_unattributed_calls_are_labelled_not_dropped(self) -> None:
        log = observers.EgressLog()
        log.record(None, "https", "example.com", "GET")
        assert log.snapshot()[0]["pack"] == "(not a custom node)"

    def test_key_cap_sets_truncated_instead_of_growing_forever(self, monkeypatch) -> None:
        monkeypatch.setattr(observers, "MAX_EGRESS_KEYS", 2)
        log = observers.EgressLog()
        for index in range(5):
            log.record("p", "https", f"host{index}", "GET")
        assert len(log.snapshot()) == 2
        assert log.truncated is True
        assert log.total_calls == 5  # every call still counted


class TestRouteInventory:
    def _router(self):
        app = web.Application()

        async def handler(request):  # defined in THIS file, so it attributes here
            return web.Response()

        app.router.add_get("/thing", handler)
        app.router.add_post("/thing", handler)
        return app.router

    def test_lists_method_and_path_and_attributes_the_handler(self, tmp_path) -> None:
        root = attribution.normalize_root(str(tmp_path))
        rows = observers.inventory_routes(self._router(), [root])
        assert {(row["method"], row["path"]) for row in rows} == {("GET", "/thing"), ("POST", "/thing")}
        assert all(row["pack"] is None for row in rows)  # this test file is outside the root

    def test_attributes_a_handler_living_under_a_root(self) -> None:
        # tests/ is not a pack, so point a root at the repo's parent: this
        # file then resolves to the repo folder, which IS the pack name.
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        root = attribution.normalize_root(os.path.dirname(here))
        rows = observers.inventory_routes(self._router(), [root])
        assert rows and rows[0]["pack"] == os.path.basename(here)

    def test_a_broken_router_yields_nothing_instead_of_raising(self) -> None:
        class Broken:
            def routes(self):
                raise RuntimeError("nope")

        assert observers.inventory_routes(Broken(), []) == []


# ------------------------------------------------- egress hooks (live call)


class TestEgressHooksIntercept:
    """The wrapper has to actually sit in front of a real call.

    Everything else about egress is unit-tested against EgressLog directly,
    which would keep passing even if the wrapping were broken — so this one
    makes a genuine aiohttp request through a local test server and asserts
    the tally moved. Observe-only, so the request must still succeed.
    """

    async def test_a_real_aiohttp_call_is_recorded_and_still_succeeds(self, aiohttp_client) -> None:
        async def handler(request):
            return web.json_response({"ok": True})

        app = web.Application()
        app.router.add_get("/ping", handler)
        client = await aiohttp_client(app)

        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        root = attribution.normalize_root(os.path.dirname(repo))
        observers._reset_hooks_for_tests()
        status = observers.install_egress_hooks([root])
        assert status["aiohttp"] is True

        before = observers.EGRESS.total_calls
        resp = await client.get("/ping")
        assert resp.status == 200                      # never breaks the call
        assert await resp.json() == {"ok": True}       # nor its result
        assert observers.EGRESS.total_calls > before   # ...and it was seen

        rows = [row for row in observers.EGRESS.snapshot() if row["host"] == "127.0.0.1"]
        assert rows, observers.EGRESS.snapshot()
        assert "GET" in rows[0]["methods"]
        # Attributed to the caller's pack (this repo), not to aiohttp itself.
        assert rows[0]["pack"] == os.path.basename(repo)


# ------------------------------------------------------------------ report


def _report(**overrides):
    base = dict(
        routes=[{"method": "POST", "path": "/x", "pack": "packA", "file": "f.py"}],
        egress=observers.EgressLog(),
        findings=scanner.scan_text("p = os.path.join(input_dir, rel)\n", pack="packA", path="m.py"),
        packs=["packA"],
        roots=["/root"],
        hooks={"aiohttp": True, "requests": False},
        scanned_packs=["packA"],
    )
    base.update(overrides)
    return report.build_report(**base)


class TestReport:
    def test_text_always_carries_the_not_a_verdict_caveat(self) -> None:
        text = report.render_text(_report())
        assert "TRIAGE, NOT A VERDICT" in text
        assert "Nothing here blocks, patches or changes" in text

    def test_names_the_libraries_it_is_not_watching(self) -> None:
        # The fail-loud rule: a hook that did not attach must be visible in
        # the output, not merely absent from it.
        text = report.render_text(_report())
        assert "NOT WATCHING: requests" in text

    def test_says_so_when_no_egress_has_been_seen(self) -> None:
        assert "only sees calls made since startup" in report.render_text(_report())

    def test_truncation_states_the_real_total(self, monkeypatch) -> None:
        monkeypatch.setattr(report, "MAX_LISTED_FINDINGS", 1)
        many = []
        for index in range(4):
            many.extend(scanner.scan_text(
                "p = os.path.join(input_dir, rel)\n", pack="p", path=f"{index}.py"))
        text = report.render_text(_report(findings=many))
        assert "and 3 more finding(s) not printed" in text

    def test_route_rows_are_grouped_under_their_pack(self) -> None:
        text = report.render_text(_report())
        assert "packA  (1)" in text
        assert "POST   /x" in text


# ------------------------------------------------------------------- route


@pytest.fixture
async def client(aiohttp_client):
    app = web.Application()
    app.add_routes(build_routes())
    return await aiohttp_client(app)


class TestAuditRoute:
    async def test_local_request_gets_the_report(self, client) -> None:
        resp = await client.get("/eps/audit?scope=routes%20only")
        assert resp.status == 200
        body = await resp.json()
        assert set(body) >= {"routes", "egress", "scan", "packs", "custom_nodes_roots"}

    async def test_unknown_scope_is_400(self, client) -> None:
        resp = await client.get("/eps/audit?scope=everything%20plus%20magic")
        assert resp.status == 400
