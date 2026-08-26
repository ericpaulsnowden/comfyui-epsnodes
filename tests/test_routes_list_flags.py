"""Tests for `GET /eps/list_flags` (`eps_image/routes_list_flags.py`) -- the
Run Multiplier estimator's list-semantics feed. Mirrors
`tests/test_routes_checkpoint_switcher.py`: a plain `aiohttp` app wrapping
`build_routes()` with a fake `nodes` module installed into `sys.modules`."""

from __future__ import annotations

import sys
import threading
import types

import pytest
from aiohttp import web

from eps_image import routes_list_flags
from eps_image.routes_list_flags import collect_list_flags


@pytest.fixture(autouse=True)
def _reset_list_flags_cache():
    """The route's response is memoized at MODULE scope, process-lifetime
    by design (2026-08-26 while-running round -- see routes_list_flags.py's
    ``_cached_response`` docstring) -- reset it around every test in this
    file so one test's fake ``nodes`` module can never leak its cached
    answer into the next."""
    routes_list_flags._reset_cache()
    yield
    routes_list_flags._reset_cache()


class _Plain:
    RETURN_TYPES = ("MODEL", "CLIP")


class _Flattener:
    INPUT_IS_LIST = True
    RETURN_TYPES = ("IMAGE",)


class _Fanner:
    RETURN_TYPES = ("STRING", "STRING")
    OUTPUT_IS_LIST = (True, False)


class _Raises:
    @property
    def RETURN_TYPES(self):  # mimics a misbehaving third-party class
        raise RuntimeError("boom")


class _RaisesOnType(type):
    def __getattribute__(cls, name):
        if name == "INPUT_IS_LIST":
            raise RuntimeError("boom")
        return super().__getattribute__(name)


class _Broken(metaclass=_RaisesOnType):
    RETURN_TYPES = ("X",)


class TestCollect:
    def test_defaults_mirror_execution_py(self) -> None:
        flags = collect_list_flags({"Plain": _Plain})
        assert flags == {"Plain": {"input_is_list": False, "output_is_list": [False, False]}}

    def test_flattener_and_fanner(self) -> None:
        flags = collect_list_flags({"F": _Flattener, "O": _Fanner})
        assert flags["F"] == {"input_is_list": True, "output_is_list": [False]}
        assert flags["O"] == {"input_is_list": False, "output_is_list": [True, False]}

    def test_a_broken_class_is_skipped_not_fatal(self) -> None:
        flags = collect_list_flags({"Broken": _Broken, "Plain": _Plain})
        assert "Broken" not in flags
        assert "Plain" in flags

    def test_non_mapping_degrades_to_empty(self) -> None:
        assert collect_list_flags(None) == {}
        assert collect_list_flags(42) == {}


@pytest.fixture
def fake_nodes(monkeypatch: pytest.MonkeyPatch):
    module = types.ModuleType("nodes")
    module.NODE_CLASS_MAPPINGS = {"Plain": _Plain, "Flattener": _Flattener, "Fanner": _Fanner}
    monkeypatch.setitem(sys.modules, "nodes", module)
    return module


@pytest.fixture
async def client(fake_nodes, aiohttp_client):
    app = web.Application()
    app.add_routes(routes_list_flags.build_routes())
    return await aiohttp_client(app)


class TestRoute:
    async def test_returns_every_class_with_both_flags(self, client) -> None:
        response = await client.get("/eps/list_flags")
        assert response.status == 200
        body = await response.json()
        assert set(body) == {"classes"}
        assert body["classes"]["Plain"] == {
            "input_is_list": False, "output_is_list": [False, False],
        }
        assert body["classes"]["Flattener"]["input_is_list"] is True
        assert body["classes"]["Fanner"]["output_is_list"] == [True, False]

    async def test_route_constant_matches(self) -> None:
        assert routes_list_flags.ROUTE == "/eps/list_flags"


# --------------------------------------------------- process-lifetime cache
# (2026-08-26 while-running round: the answer is static for the process's
# whole life, so it's walked at most once -- see routes_list_flags.py's
# ``_cached_response`` docstring for the staleness-until-restart decision.)


class TestCaching:
    async def test_second_call_reuses_the_same_cached_object_no_second_walk(
        self, fake_nodes, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[object] = []
        original = routes_list_flags.collect_list_flags

        def counting(mappings):
            calls.append(mappings)
            return original(mappings)

        monkeypatch.setattr(routes_list_flags, "collect_list_flags", counting)

        first_body = await (await client.get("/eps/list_flags")).json()
        cached_after_first = routes_list_flags._cached_response
        assert cached_after_first is not None

        second_body = await (await client.get("/eps/list_flags")).json()
        cached_after_second = routes_list_flags._cached_response

        assert first_body == second_body
        assert cached_after_first is cached_after_second  # SAME object, not just equal
        assert len(calls) == 1  # collect_list_flags walked exactly once, on the first call

    async def test_a_class_added_after_the_first_call_is_not_picked_up(
        self, fake_nodes, client
    ) -> None:
        """Documents the accepted trade-off: a class registered into
        ``NODE_CLASS_MAPPINGS`` after this process's first successful
        answer is invisible to every later request until a restart. There
        is no in-process reload signal to gate on instead (see the
        docstring) -- staleness-until-restart is the deliberate choice."""
        first = await (await client.get("/eps/list_flags")).json()
        assert "NewOne" not in first["classes"]

        fake_nodes.NODE_CLASS_MAPPINGS["NewOne"] = _Plain

        second = await (await client.get("/eps/list_flags")).json()
        assert "NewOne" not in second["classes"]  # still the stale cached answer

    async def test_reset_cache_forces_a_fresh_walk(self, fake_nodes, client) -> None:
        """``_reset_cache`` is the test-only escape hatch this file's own
        autouse fixture relies on -- pinned directly so a future refactor
        of the caching mechanism can't silently drop it."""
        await client.get("/eps/list_flags")
        fake_nodes.NODE_CLASS_MAPPINGS["NewOne"] = _Plain
        routes_list_flags._reset_cache()

        after_reset = await (await client.get("/eps/list_flags")).json()
        assert "NewOne" in after_reset["classes"]

    async def test_a_failed_walk_is_never_cached(
        self, fake_nodes, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A transient failure (here: the walk itself raising) must not
        wedge every later request behind one bad answer -- only a
        SUCCESSFUL walk is memoized."""

        original = routes_list_flags.collect_list_flags

        def boom(mappings):
            raise RuntimeError("transient failure")

        monkeypatch.setattr(routes_list_flags, "collect_list_flags", boom)
        # aiohttp turns an unhandled handler exception into a 500, it does
        # not propagate out of the test client -- assert on the status, not
        # a raised exception.
        failed = await client.get("/eps/list_flags")
        assert failed.status == 500
        assert routes_list_flags._cached_response is None

        # Restore just this one attribute -- NOT monkeypatch.undo(), which
        # would also revert the `fake_nodes` fixture's own sys.modules
        # patch (same fixture instance) and reintroduce the module-not-
        # found error this test isn't exercising.
        monkeypatch.setattr(routes_list_flags, "collect_list_flags", original)
        recovered = await (await client.get("/eps/list_flags")).json()
        assert "Plain" in recovered["classes"]


class TestOffLoop:
    async def test_the_uncached_walk_runs_off_the_event_loop_thread(
        self, fake_nodes, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loop_thread = threading.get_ident()
        recorded: list[int] = []
        original = routes_list_flags.collect_list_flags

        def recording(mappings):
            recorded.append(threading.get_ident())
            return original(mappings)

        monkeypatch.setattr(routes_list_flags, "collect_list_flags", recording)

        response = await client.get("/eps/list_flags")

        assert response.status == 200
        assert recorded and all(t != loop_thread for t in recorded)

    async def test_a_cache_hit_skips_the_walk_entirely(
        self, fake_nodes, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await client.get("/eps/list_flags")  # primes the cache

        calls: list[object] = []
        monkeypatch.setattr(
            routes_list_flags, "collect_list_flags", lambda mappings: calls.append(mappings)
        )

        response = await client.get("/eps/list_flags")
        assert response.status == 200
        assert calls == []  # never called -- the cached response answered it
