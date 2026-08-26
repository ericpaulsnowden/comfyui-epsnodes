"""Tests for `GET /eps_ckpt/checkpoints` (`eps_image/routes_checkpoint_switcher.py`).

Mirrors `tests/test_routes_image_grid.py`'s conventions: a plain
`aiohttp.web.Application` wrapping `routes_checkpoint_switcher.build_routes()`
directly (no ComfyUI, no PromptServer needed either way), and a fake
`folder_paths` module installed into `sys.modules` the same way that file's
own `fake_folder_paths` fixture does for its route.
"""

from __future__ import annotations

import sys
import threading
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


class _ThreadRecorder:
    """Wraps a function, recording which OS thread ran each call before
    delegating to the original -- ``test_nas_io_round.py``'s identical
    helper, duplicated here rather than imported since that module isn't
    one of this pack's ``eps_image`` route tests and shouldn't gain a
    cross-file dependency for one small helper."""

    def __init__(self, original) -> None:
        self.original = original
        self.threads: list[int] = []

    def __call__(self, *args, **kwargs):
        self.threads.append(threading.get_ident())
        return self.original(*args, **kwargs)


class TestOffLoop:
    """2026-08-26 while-running round: ``folder_paths.get_filename_list``
    does a real scandir/stat pass, not a cached lookup -- synchronously on
    the event loop it competes with every other coroutine while the server
    is GIL-busy mid-run. Now wrapped in ``asyncio.to_thread`` (the pack's
    established idiom, ``routes_image_grid.py``'s preview branch)."""

    async def test_get_filename_list_runs_off_the_event_loop_thread(
        self, monkeypatch: pytest.MonkeyPatch, aiohttp_client
    ) -> None:
        loop_thread = threading.get_ident()
        checkpoints = ["a.safetensors", "styles/b.safetensors"]
        recorder = _ThreadRecorder(
            lambda folder: list(checkpoints) if folder == "checkpoints" else []
        )
        fake_module = types.ModuleType("folder_paths")
        fake_module.get_filename_list = recorder
        monkeypatch.setitem(sys.modules, "folder_paths", fake_module)
        app = web.Application()
        app.add_routes(routes_checkpoint_switcher.build_routes())
        client = await aiohttp_client(app)

        response = await client.get("/eps_ckpt/checkpoints")

        assert response.status == 200
        assert (await response.json()) == {"checkpoints": checkpoints}
        # Byte-identical response, just not built on the loop thread.
        assert recorder.threads and all(t != loop_thread for t in recorder.threads)


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
