"""Tests for `GET /eps/list_flags` (`eps_image/routes_list_flags.py`) -- the
Run Multiplier estimator's list-semantics feed. Mirrors
`tests/test_routes_checkpoint_switcher.py`: a plain `aiohttp` app wrapping
`build_routes()` with a fake `nodes` module installed into `sys.modules`."""

from __future__ import annotations

import sys
import types

import pytest
from aiohttp import web

from eps_image import routes_list_flags
from eps_image.routes_list_flags import collect_list_flags


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
