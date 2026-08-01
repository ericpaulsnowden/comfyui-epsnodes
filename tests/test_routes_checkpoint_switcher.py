"""Tests for `GET /eps_ckpt/checkpoints` (`eps_image/routes_checkpoint_switcher.py`).

Mirrors `tests/test_routes_image_grid.py`'s conventions: a plain
`aiohttp.web.Application` wrapping `routes_checkpoint_switcher.build_routes()`
directly (no ComfyUI, no PromptServer needed either way), and a fake
`folder_paths` module installed into `sys.modules` the same way that file's
own `fake_folder_paths` fixture does for its route.
"""

from __future__ import annotations

import sys
import types

import pytest
from aiohttp import web

from eps_image import routes_checkpoint_switcher


@pytest.fixture
def fake_folder_paths(monkeypatch: pytest.MonkeyPatch):
    """Installs a fake `folder_paths` module exposing a fixed
    `get_filename_list` result. Returns the fixed checkpoint list so a test
    can assert the route echoes it verbatim.
    """
    checkpoints = ["a.safetensors", "styles/b.safetensors", "c.ckpt"]
    fake_module = types.ModuleType("folder_paths")
    fake_module.get_filename_list = (
        lambda folder: list(checkpoints) if folder == "checkpoints" else []
    )
    monkeypatch.setitem(sys.modules, "folder_paths", fake_module)
    return checkpoints


@pytest.fixture
async def client(fake_folder_paths: list[str], aiohttp_client):
    """A plain aiohttp test client wired to just this module's route (no
    ComfyUI) -- mirrors `test_routes_image_grid.py`'s own `client` fixture.
    """
    app = web.Application()
    app.add_routes(routes_checkpoint_switcher.build_routes())
    return await aiohttp_client(app)


class TestCheckpointsRouteHappyPath:
    async def test_returns_the_checkpoint_list_verbatim(
        self, client, fake_folder_paths: list[str]
    ) -> None:
        response = await client.get("/eps_ckpt/checkpoints")
        assert response.status == 200
        body = await response.json()
        assert body == {"checkpoints": fake_folder_paths}

    async def test_response_key_is_checkpoints(self, client) -> None:
        body = await (await client.get("/eps_ckpt/checkpoints")).json()
        assert set(body.keys()) == {"checkpoints"}

    async def test_empty_checkpoints_folder_returns_empty_list(
        self, monkeypatch: pytest.MonkeyPatch, aiohttp_client
    ) -> None:
        fake_module = types.ModuleType("folder_paths")
        fake_module.get_filename_list = lambda folder: []
        monkeypatch.setitem(sys.modules, "folder_paths", fake_module)
        app = web.Application()
        app.add_routes(routes_checkpoint_switcher.build_routes())
        local_client = await aiohttp_client(app)

        response = await local_client.get("/eps_ckpt/checkpoints")
        assert response.status == 200
        assert (await response.json()) == {"checkpoints": []}


class TestCheckpointsRouteNoLoopbackGate:
    """Contract: "No loopback gate -- it exposes the same filename list
    /object_info already gives every viewer." Unlike
    `routes_frame_saver.py`'s probe/stream routes, a forwarded (non-local)
    caller must get the SAME 200, not a 403.
    """

    async def test_forwarded_caller_still_gets_200(self, client) -> None:
        response = await client.get(
            "/eps_ckpt/checkpoints", headers={"X-Forwarded-For": "192.168.1.50"}
        )
        assert response.status == 200


class TestBuildRoutesAndRegister:
    def test_build_routes_returns_a_route_table_with_the_one_route(self) -> None:
        routes = routes_checkpoint_switcher.build_routes()
        paths = {route.path for route in routes}
        assert "/eps_ckpt/checkpoints" in paths

    def test_register_attaches_to_the_live_prompt_server(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Mirrors test_routes_image_grid.py-style register() coverage: fake
        # ComfyUI's `server` module so `register()` can run with no real
        # ComfyUI install, and confirm it reaches PromptServer.instance.routes
        # (never raw app.add_routes -- module docstring's "invisible to the
        # frontend" finding).
        added_routes = web.RouteTableDef()

        class FakePromptServer:
            routes = added_routes

        fake_server_module = types.ModuleType("server")
        fake_server_module.PromptServer = types.SimpleNamespace(instance=FakePromptServer())
        monkeypatch.setitem(sys.modules, "server", fake_server_module)

        routes_checkpoint_switcher.register()

        paths = {route.path for route in added_routes}
        assert "/eps_ckpt/checkpoints" in paths
