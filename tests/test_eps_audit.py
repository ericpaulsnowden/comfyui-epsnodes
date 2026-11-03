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

import asyncio
import concurrent.futures
import os
import sys
import types

import pytest
from aiohttp import web
from conftest import symlink_or_skip

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
        symlink_or_skip(nodes_dir / "linked_pack", real, target_is_directory=True)
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

    def test_body_url_needs_a_request_shaped_receiver(self) -> None:
        """`fields.get("server")` on a local dict is not a caller-supplied URL.

        This pack's own `_mount_label()` parses a GVFS mount name
        (`smb-share:server=HOST,share=NAME`) into something readable and
        never touches the network, but it lives in `routes.py`, which does
        register routes and import aiohttp -- so the rule's file-level
        `needs` gate passes and only the receiver's NAME can tell the two
        apart. This was a confirmed false positive that alone painted this
        pack red in the plain-language summary.
        """
        rule = next(r for r in scanner.RULES if r.id == "route.body_url")
        assert not rule.pattern.search('        server = fields.get("server")')
        assert not rule.pattern.search('    share = fields.get("share")')

    def test_body_url_still_catches_a_real_forwarded_url(self) -> None:
        rule = next(r for r in scanner.RULES if r.id == "route.body_url")
        for real in (
            'url = body.get("url")',
            'target = payload.get("endpoint")',
            'host = data.get("host")',
            'u = request_body.get("base_url")',
        ):
            assert rule.pattern.search(real), real

    def test_joining_an_all_caps_constant_is_not_a_traversal(self) -> None:
        # `os.path.join(models_dir, _MODEL_FOLDER)` is how a pack registers
        # its own model directory; the second argument is a literal fixed at
        # import, not request data (found against
        # Comfyui_Minimax_h3_latent_Upscaler, 2026-09-02).
        text = 'os.path.join(folder_paths.models_dir, _LATENT_UPSCALE_FOLDER)\n'
        assert [f for f in scanner.scan_text(text) if f.rule_id == "path.unclamped_join"] == []

    def test_a_lowercase_variable_join_is_still_a_finding(self) -> None:
        # The guard above must not swallow the real bug it sits next to.
        text = "p = os.path.join(input_dir, rel)\n"
        assert [f for f in scanner.scan_text(text) if f.rule_id == "path.unclamped_join"]

    def test_a_loop_variable_from_a_listing_is_not_a_traversal(self) -> None:
        # The listing binds the name on an EARLIER line, so the one-line
        # `exclude` cannot see it (ComfyUI-VDN-H3 spec.py:323, 2026-09-09).
        text = (
            "for name in sorted(os.listdir(adapters_root)):\n"
            "    adir = os.path.join(adapters_root, name)\n"
        )
        assert [f for f in scanner.scan_text(text) if f.rule_id == "path.unclamped_join"] == []

    def test_a_request_value_join_is_still_a_finding_in_a_file_with_listings(self) -> None:
        # The guard is per-NAME, not per-file: an unrelated listing elsewhere
        # must not launder a genuinely caller-supplied path.
        text = (
            "for name in os.listdir(root):\n"
            "    pass\n"
            "p = os.path.join(input_dir, rel)\n"
        )
        assert [f for f in scanner.scan_text(text) if f.rule_id == "path.unclamped_join"]

    # --- Listing-loop skip must be SCOPED to that loop (lead review 2026-09-10).
    # The first version collected loop names FILE-WIDE, so any later join on
    # the same identifier was skipped -- including caller-supplied values.
    # `adapters_root` is used deliberately: the rule's pattern needs a
    # dir/root/base/folder SUFFIX after at least one character, so a bare
    # `root` never matches and a "still skipped" test built on it would pass
    # for the wrong reason.

    @staticmethod
    def _joins(text: str) -> list[int]:
        return [f.line for f in scanner.scan_text(text) if f.rule_id == "path.unclamped_join"]

    def test_the_same_name_rebound_after_the_loop_is_still_a_finding(self) -> None:
        text = (
            "for name in os.listdir(adapters_root):\n"
            "    pass\n"
            "name = request.query['file']\n"
            "p = os.path.join(base_dir, name)\n"
        )
        assert self._joins(text) == [4]

    def test_the_same_name_in_a_different_function_is_still_a_finding(self) -> None:
        # The dangerous shape: a handler whose PARAMETER merely shares its name
        # with an unrelated listing loop elsewhere in the file.
        text = (
            "def list_it(adapters_root):\n"
            "    for name in os.listdir(adapters_root):\n"
            "        yield name\n"
            "\n"
            "def serve(base_dir, name):\n"
            "    return os.path.join(base_dir, name)\n"
        )
        assert self._joins(text) == [6]

    def test_a_rebind_inside_the_loop_body_cancels_the_skip(self) -> None:
        text = (
            "for name in os.listdir(adapters_root):\n"
            "    name = request.query['file']\n"
            "    p = os.path.join(adapters_root, name)\n"
        )
        assert self._joins(text) == [3]

    def test_the_skip_ends_with_the_loop_body(self) -> None:
        # Python leaks `name` past the loop, but a later join is no longer
        # visibly enumeration -- flag it; that is the conservative direction.
        text = (
            "for name in os.listdir(adapters_root):\n"
            "    pass\n"
            "p = os.path.join(adapters_root, name)\n"
        )
        assert self._joins(text) == [3]

    def test_a_join_nested_deeper_in_the_loop_body_is_still_skipped(self) -> None:
        text = (
            "for name in sorted(os.listdir(adapters_root)):\n"
            "    if name.endswith('.json'):\n"
            "        p = os.path.join(adapters_root, name)\n"
        )
        assert self._joins(text) == []

    def test_blank_lines_and_comments_do_not_end_the_loop_body(self) -> None:
        text = (
            "for name in os.listdir(adapters_root):\n"
            "\n"
            "    # build the path\n"
            "    p = os.path.join(adapters_root, name)\n"
        )
        assert self._joins(text) == []

    def test_a_comparison_is_not_a_rebind(self) -> None:
        text = (
            "for name in os.listdir(adapters_root):\n"
            "    if name == 'skip':\n"
            "        continue\n"
            "    p = os.path.join(adapters_root, name)\n"
        )
        assert self._joins(text) == []

    def test_a_nested_non_listing_loop_rebinding_the_name_cancels_the_skip(self) -> None:
        text = (
            "for name in os.listdir(adapters_root):\n"
            "    for name in request.json['files']:\n"
            "        p = os.path.join(adapters_root, name)\n"
        )
        assert self._joins(text) == [3]

    def test_joining_the_listed_name_onto_itself_is_still_skipped(self) -> None:
        # Python evaluates the right-hand side first, so the join sees the
        # listed name; the rebind only affects LATER lines.
        text = (
            "for f in os.listdir(adapters_root):\n"
            "    f = os.path.join(adapters_root, f)\n"
            "    g = os.path.join(base_dir, f)\n"
        )
        assert self._joins(text) == [3]

    def test_a_second_join_on_the_same_line_is_judged_on_its_own(self) -> None:
        text = (
            "for name in os.listdir(adapters_root):\n"
            "    a, b = os.path.join(adapters_root, name), os.path.join(base_dir, rel)\n"
        )
        assert self._joins(text) == [2]

    def test_a_keyword_argument_is_not_a_rebind(self) -> None:
        text = (
            "for name in os.listdir(adapters_root):\n"
            "    log(name=name)\n"
            "    p = os.path.join(adapters_root, name)\n"
        )
        assert self._joins(text) == []

    def test_a_multi_line_comprehension_clause_is_still_reported(self) -> None:
        # Deliberate, measured gap: a comprehension's `for` clause on its own
        # line opens no indented body (the next clause shares its indent), so
        # the skip never starts. 5 of 628 findings in a 10M-line corpus, all
        # also flagged before the skip existed. Closing it needs bracket
        # counting across lines, which a string holding a bracket throws off.
        # Do NOT "fix" this by collecting names file-wide again.
        text = (
            "names = [\n"
            "    name\n"
            "    for name in os.listdir(adapters_root)\n"
            "    if os.path.isfile(os.path.join(adapters_root, name))\n"
            "]\n"
        )
        assert self._joins(text) == [4]

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


# ------------------------------------- egress attribution (who is blamed)


def _run_as(filename: str, source: str, **namespace) -> dict:
    """Execute *source* as if it lived in the file *filename*.

    Frames report the filename the code was COMPILED under, so this makes a
    function that looks, to the stack walk, like it belongs to any pack (or to
    ComfyUI core) without writing a file. The file need not exist.
    """
    scope = {"__name__": "fake_caller", **namespace}
    exec(compile(source, filename, "exec"), scope)
    return scope


_URLLIB_CALLER = (
    "import urllib.request\n"
    "def fetch(url):\n"
    "    return urllib.request.urlopen(url)\n"
)
_REQUESTS_CALLER = (
    "import requests\n"
    "def fetch(url):\n"
    "    with requests.Session() as session:\n"
    "        return session.request('GET', url)\n"
)
_AIOHTTP_CALLER = (
    "import aiohttp\n"
    "async def fetch(url):\n"
    "    async with aiohttp.ClientSession() as session:\n"
    "        return await session.get(url)\n"
)

def _in_clean_thread(fn, *args):
    """Call ``fn(*args)`` on a worker thread and return its result.

    The attribution walk climbs the WHOLE stack for the innermost pack frame,
    and a test function living in this repo is itself a frame inside a pack
    root. Run from the test thread, a "core" caller would therefore be blamed
    on whichever pack the test file belongs to. On a fresh thread the stack
    above the caller is only the standard library, which is what a call from
    ComfyUI core with no pack node running looks like.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(fn, *args).result()


#: What each stubbed client entry point hands back, so a test can also assert
#: the wrapper passed the real result straight through.
_STUB_RESULT = {
    "urllib": "urllib-result",
    "requests": "requests-result",
    "aiohttp": "aiohttp-result",
}

#: The callers every client is driven from; the keys of ``world.callers``.
_CALLERS = ["another pack", "core comfy/", "core nodes.py", "EPSNodes itself"]


class TestEgressAttribution:
    """The egress lens must blame the CALLER, never its own wrapper.

    The wrappers live inside EPSNodes, so a stack walk that starts at the
    wrapper finds EPSNodes first and reports it for every other pack's
    request. That shipped (found by external review, FORMAT.md §9
    2026-11-03) because the single test of it called from inside EPSNodes,
    where the wrong answer matches the right one. These tests therefore drive
    the REAL wrappers from code compiled under filenames of three different
    owners, and assert all three answers.

    Only the network itself is stubbed: each client's entry point is replaced
    by a stub BEFORE the real ``install_egress_hooks`` wraps it, so the wrapper
    under test is the production one and nothing leaves the machine.
    """

    @pytest.fixture
    def world(self, tmp_path, monkeypatch):
        import urllib.request

        import aiohttp

        # The checkout stands in for the EPSNodes pack: its real folder is
        # `comfyui-epsnodes` in production, and here whatever the checkout is
        # called. The real observers.py sits inside it, which is the whole
        # point -- the wrapper must read as EPSNodes' code to this test.
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        roots = [attribution.normalize_root(os.path.dirname(repo))]
        eps_pack = os.path.basename(repo)
        assert attribution.pack_of_path(observers.__file__, roots) == eps_pack

        # ComfyUI core is anywhere OUTSIDE every custom_nodes root.
        comfy_root = tmp_path / "ComfyUI"
        core_files = [str(comfy_root / "comfy" / "foo.py"), str(comfy_root / "nodes.py")]
        if any(attribution.pack_of_path(path, roots) for path in core_files):
            pytest.skip("checkout sits so near the filesystem root that tmp_path is inside a root")

        stubbed = {}

        def fake_urlopen(url, *args, **kwargs):
            return _STUB_RESULT["urllib"]

        async def fake_aiohttp_request(self, method, url, *args, **kwargs):
            return _STUB_RESULT["aiohttp"]

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(aiohttp.ClientSession, "_request", fake_aiohttp_request)
        stubbed["urllib"] = stubbed["aiohttp"] = True
        try:
            import requests.sessions
        except ImportError:  # not a dependency of this pack
            stubbed["requests"] = False
        else:
            monkeypatch.setattr(
                requests.sessions.Session, "request",
                lambda self, method, url, *args, **kwargs: _STUB_RESULT["requests"],
            )
            stubbed["requests"] = True

        log = observers.EgressLog()
        monkeypatch.setattr(observers, "EGRESS", log)
        monkeypatch.setattr(observers, "_hooks_installed", None)
        status = observers.install_egress_hooks(roots)
        # A client that quietly failed to wrap would leave the tests below
        # asserting against a call nothing observed.
        assert all(status[name] for name, present in stubbed.items() if present), status

        # who is calling -> (a filename to compile the caller under, the pack
        # the report must name for it)
        callers = {
            "another pack": (
                os.path.join(os.path.dirname(repo), "other-pack", "client.py"), "other-pack"),
            "core comfy/": (core_files[0], "(not a custom node)"),
            "core nodes.py": (core_files[1], "(not a custom node)"),
            "EPSNodes itself": (os.path.join(repo, "eps_image", "remote_fetch.py"), eps_pack),
        }
        return types.SimpleNamespace(
            roots=roots, log=log, callers=callers, stubbed=stubbed, eps_pack=eps_pack,
            tmp_path=tmp_path,
        )

    @staticmethod
    def _only_row(log) -> dict:
        rows = log.snapshot()
        assert len(rows) == 1, rows
        assert rows[0]["host"] == "example.com"
        return rows[0]

    @pytest.mark.parametrize("caller", _CALLERS)
    @pytest.mark.parametrize(
        "client, source", [("urllib", _URLLIB_CALLER), ("requests", _REQUESTS_CALLER)])
    def test_sync_clients_blame_the_caller(self, world, client, source, caller) -> None:
        if not world.stubbed[client]:
            pytest.skip(f"{client} is not installed")
        filename, expected = world.callers[caller]
        fetch = _run_as(filename, source)["fetch"]
        result = _in_clean_thread(fetch, "https://example.com/path")
        assert result == _STUB_RESULT[client]              # called straight through
        assert self._only_row(world.log)["pack"] == expected

    @pytest.mark.parametrize("caller", _CALLERS)
    def test_aiohttp_blames_the_caller(self, world, caller) -> None:
        filename, expected = world.callers[caller]
        fetch = _run_as(filename, _AIOHTTP_CALLER)["fetch"]
        result = _in_clean_thread(asyncio.run, fetch("https://example.com/path"))
        assert result == _STUB_RESULT["aiohttp"]
        row = self._only_row(world.log)
        assert row["pack"] == expected
        assert row["methods"] == ["GET"]

    def test_a_client_library_between_the_caller_and_the_wrapper_is_climbed(self, world) -> None:
        # The usual real shape: a pack calls a helper library which calls
        # urllib. The library is neither a pack nor the wrapper, so the walk
        # has to climb through it and land on the pack that asked.
        library = _run_as(
            str(world.tmp_path / "site-packages" / "somelib" / "api.py"),
            "import urllib.request\ndef get(url):\n    return urllib.request.urlopen(url)\n",
        )
        filename, expected = world.callers["another pack"]
        caller_source = "def fetch(url):\n    return lib['get'](url)\n"
        fetch = _run_as(filename, caller_source, lib=library)["fetch"]
        _in_clean_thread(fetch, "https://example.com/path")
        assert self._only_row(world.log)["pack"] == expected

    def test_a_core_call_made_under_pack_code_is_blamed_on_that_pack(self, world) -> None:
        # The deliberate flip side of the core cases above: the walk takes the
        # INNERMOST pack frame anywhere on the stack, so core code that a pack
        # called into is the pack's doing. Only a call with no pack frame
        # above it is unattributed.
        core = _run_as(world.callers["core nodes.py"][0], _URLLIB_CALLER)
        filename, expected = world.callers["another pack"]
        run = _run_as(filename, "def run(url):\n    return core['fetch'](url)\n", core=core)["run"]
        _in_clean_thread(run, "https://example.com/path")
        assert self._only_row(world.log)["pack"] == expected


class TestPackOfFrameSkipFiles:
    """``pack_of_frame`` steps over instrumentation by FILE, never by pack."""

    def _stack(self, tmp_path):
        root = attribution.normalize_root(str(tmp_path / "custom_nodes"))
        wrapper_file = str(tmp_path / "custom_nodes" / "eps-pack" / "wrapper.py")
        sibling_file = str(tmp_path / "custom_nodes" / "eps-pack" / "route.py")
        caller_file = str(tmp_path / "custom_nodes" / "other-pack" / "client.py")
        wrapper = _run_as(
            wrapper_file,
            "def note(pack_of_frame, roots, skip_files):\n"
            "    return pack_of_frame(roots, skip_files=skip_files)\n",
        )
        return root, wrapper_file, sibling_file, caller_file, wrapper

    def test_listed_files_are_stepped_over(self, tmp_path) -> None:
        root, wrapper_file, _, caller_file, wrapper = self._stack(tmp_path)
        caller = _run_as(
            caller_file,
            "def call():\n    return note(pack_of_frame, [root], (wrapper_file,))\n",
            note=wrapper["note"], pack_of_frame=attribution.pack_of_frame,
            root=root, wrapper_file=wrapper_file,
        )
        assert caller["call"]() == "other-pack"

    def test_nothing_is_skipped_unless_asked(self, tmp_path) -> None:
        # With no skip_files the first pack frame wins, which here is the
        # wrapper's own pack. This is the pre-fix behaviour, kept reachable
        # so the contrast stays visible in the tests.
        root, _, _, caller_file, wrapper = self._stack(tmp_path)
        caller = _run_as(
            caller_file,
            "def call():\n    return note(pack_of_frame, [root], ())\n",
            note=wrapper["note"], pack_of_frame=attribution.pack_of_frame, root=root,
        )
        assert caller["call"]() == "eps-pack"

    def test_a_sibling_file_of_the_same_pack_is_not_skipped(self, tmp_path) -> None:
        # EPSNodes' own code that makes a request must still be blamed on
        # EPSNodes: only the listed instrumentation files are stepped over,
        # not everything in the pack that owns them.
        root, wrapper_file, sibling_file, _, wrapper = self._stack(tmp_path)
        route = _run_as(
            sibling_file,
            "def call():\n    return note(pack_of_frame, [root], (wrapper_file,))\n",
            note=wrapper["note"], pack_of_frame=attribution.pack_of_frame,
            root=root, wrapper_file=wrapper_file,
        )
        assert route["call"]() == "eps-pack"

    def test_the_attribution_module_is_never_the_caller(self, tmp_path) -> None:
        # If a helper inside attribution.py ever calls pack_of_frame, that
        # helper is machinery, not the caller. Compile a stand-in under
        # attribution.py's own filename and put it between a pack and the walk.
        repo = os.path.dirname(os.path.dirname(os.path.abspath(attribution.__file__)))
        root = attribution.normalize_root(os.path.dirname(repo))
        assert attribution.pack_of_path(attribution.__file__, [root]) == os.path.basename(repo)
        helper = _run_as(
            attribution.__file__,
            "def helper(roots):\n    return pack_of_frame(roots)\n",
            pack_of_frame=attribution.pack_of_frame,
        )
        client = _run_as(
            os.path.join(os.path.dirname(repo), "other-pack", "client.py"),
            "def call(roots):\n    return helper(roots)\n",
            helper=helper["helper"],
        )
        assert client["call"]([root]) == "other-pack"


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
