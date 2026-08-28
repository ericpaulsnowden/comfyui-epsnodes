"""Tests for the FORMAT.md §5 universal-state routes
(``lora_library/routes_universal_states.py``), through its own
``build_routes(context)`` (no ComfyUI, no dependency on
``lora_library.routes``'s ``_register_all`` -- this module is wired into
the live server from ``__init__.py`` directly, mirroring
``eps_image/routes_resolution_presets.py``'s identical layering) and
aiohttp's own test client.

Mirrors tests/test_routes_sets.py's structure and conventions throughout.
"""

from __future__ import annotations

import sys
import threading
import types
from pathlib import Path
from typing import Any, ClassVar

import pytest
from aiohttp import web

from lora_library import universal_states_store as store
from lora_library.context import LibraryContext
from lora_library.routes_universal_states import build_routes


def make_app(context: LibraryContext) -> web.Application:
    app = web.Application()
    app.add_routes(build_routes(context))
    return app


@pytest.fixture
def fake_nodes(monkeypatch: pytest.MonkeyPatch):
    module = types.ModuleType("nodes")
    module.NODE_CLASS_MAPPINGS = {}
    monkeypatch.setitem(sys.modules, "nodes", module)
    return module


class _KnownClass:
    EPS_STATE_WIDGETS: ClassVar[dict[str, Any]] = {
        "format": 1,
        "widgets": {"width": {"kind": "int", "min": 1, "max": 8192}},
    }


# -------------------------------------------------------- GET /universal_states


async def test_get_universal_states_empty(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.get("/lora_library/universal_states")
    assert resp.status == 200
    body = await resp.json()
    assert set(body) == {"ok", "states", "states_dir", "is_default_library", "layout", "mtime"}
    assert body["ok"] is True
    assert body["states"] == []
    assert body["states_dir"] == str(context.default_library_dir / "states")
    assert body["is_default_library"] is True
    assert body["layout"] == {"categories": [], "order": {"": []}}
    assert isinstance(body["mtime"], (int, float))


async def test_get_universal_states_reflects_saved_states_sorted_by_name(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/universal_state", json={"state": {"name": "Zebra", "nodes": []}}
    )
    await client.post(
        "/lora_library/universal_state", json={"state": {"name": "Apple", "nodes": []}}
    )

    resp = await client.get("/lora_library/universal_states")
    body = await resp.json()
    assert [s["name"] for s in body["states"]] == ["Apple", "Zebra"]


async def test_get_universal_states_includes_the_layout_in_one_round_trip(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    created = await client.post(
        "/lora_library/universal_state", json={"state": {"name": "Alpha", "nodes": []}}
    )
    slug = (await created.json())["slug"]
    await client.post(
        "/lora_library/universal_states/layout",
        json={"layout": {"categories": ["Grp"], "order": {"Grp": [slug]}}},
    )

    resp = await client.get("/lora_library/universal_states")
    body = await resp.json()
    assert body["layout"] == {"categories": ["Grp"], "order": {"": [], "Grp": [slug]}}


# ------------------------------------------------------- POST /universal_state


async def test_post_universal_state_creates_and_derives_slug(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/universal_state",
        json={"state": {"name": "Portrait pass", "nodes": []}},
    )
    assert resp.status == 200
    body = await resp.json()
    assert set(body) == {"ok", "slug", "states", "foreign"}
    assert body["ok"] is True
    assert body["slug"] == "portrait-pass"
    assert body["foreign"] == []
    assert [s["slug"] for s in body["states"]] == ["portrait-pass"]


async def test_post_universal_state_repeated_name_gets_collision_suffix(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    first = await client.post(
        "/lora_library/universal_state", json={"state": {"name": "Foo", "nodes": []}}
    )
    second = await client.post(
        "/lora_library/universal_state", json={"state": {"name": "Foo", "nodes": []}}
    )
    assert (await first.json())["slug"] == "foo"
    assert (await second.json())["slug"] == "foo-2"


async def test_post_universal_state_with_explicit_slug_updates_in_place(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    created = await client.post(
        "/lora_library/universal_state", json={"state": {"name": "Foo", "nodes": []}}
    )
    slug = (await created.json())["slug"]

    updated = await client.post(
        "/lora_library/universal_state",
        json={"slug": slug, "state": {"name": "Foo Renamed", "nodes": []}},
    )
    assert updated.status == 200
    body = await updated.json()
    assert body["slug"] == slug
    assert len(body["states"]) == 1

    fetched = await client.get("/lora_library/universal_state", params={"slug": slug})
    assert (await fetched.json())["name"] == "Foo Renamed"


async def test_post_universal_state_echoes_foreign_class_warnings(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/universal_state",
        json={
            "state": {
                "name": "Mixed",
                "nodes": [{"class": "SomeThirdPartyNode", "id": "1", "widgets": {"x": 1}}],
            }
        },
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["foreign"] == ["SomeThirdPartyNode"]


async def test_post_universal_state_registry_validated_payload_saves_clean(
    context: LibraryContext, aiohttp_client, fake_nodes
) -> None:
    fake_nodes.NODE_CLASS_MAPPINGS["Known"] = _KnownClass
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/universal_state",
        json={
            "state": {
                "name": "Known state",
                "nodes": [{"class": "Known", "id": "1", "widgets": {"width": 1024}}],
            }
        },
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["foreign"] == []


async def test_post_universal_state_registry_rejects_unknown_widget_400(
    context: LibraryContext, aiohttp_client, fake_nodes
) -> None:
    fake_nodes.NODE_CLASS_MAPPINGS["Known"] = _KnownClass
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/universal_state",
        json={
            "state": {
                "name": "Bad",
                "nodes": [{"class": "Known", "id": "1", "widgets": {"bogus": 1}}],
            }
        },
    )
    assert resp.status == 400
    body = await resp.json()
    assert "Known.bogus" in body["error"]


async def test_post_universal_state_malformed_json_body_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/universal_state",
        data="not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400
    assert "error" in await resp.json()


async def test_post_universal_state_non_object_body_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/lora_library/universal_state", json=["not", "an", "object"])
    assert resp.status == 400


async def test_post_universal_state_missing_state_key_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/lora_library/universal_state", json={})
    assert resp.status == 400
    assert "error" in await resp.json()


async def test_post_universal_state_invalid_slug_in_body_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/universal_state",
        json={"slug": "Not A Valid Slug!", "state": {"name": "Foo", "nodes": []}},
    )
    assert resp.status == 400
    assert "error" in await resp.json()


async def test_post_universal_state_invalid_payload_is_400_with_clear_message(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/universal_state", json={"state": {"name": "", "nodes": []}}
    )
    assert resp.status == 400
    body = await resp.json()
    assert "name" in body["error"]


async def test_post_universal_state_format_too_new_is_400_and_mentions_update_the_pack(
    context: LibraryContext, aiohttp_client
) -> None:
    too_new = store.CURRENT_FORMAT + 1
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/universal_state",
        json={"state": {"format": too_new, "name": "x", "nodes": []}},
    )
    assert resp.status == 400
    body = await resp.json()
    assert "update the pack" in body["error"]


# --------------------------------------------------------- GET /universal_state


async def test_get_universal_state_returns_full_shape_plus_slug(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/universal_state",
        json={
            "state": {
                "name": "Foo",
                "notes": "hello",
                "nodes": [{"class": "A", "id": "1", "widgets": {"x": 1}}],
            }
        },
    )
    resp = await client.get("/lora_library/universal_state", params={"slug": "foo"})
    assert resp.status == 200
    body = await resp.json()
    assert body["slug"] == "foo"
    assert body["name"] == "Foo"
    assert body["format"] == 1
    assert body["notes"] == "hello"
    assert body["nodes"] == [{"class": "A", "id": "1", "title": "", "widgets": {"x": 1}}]


async def test_get_universal_state_unknown_slug_is_404(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.get("/lora_library/universal_state", params={"slug": "does-not-exist"})
    assert resp.status == 404
    assert "error" in await resp.json()


async def test_get_universal_state_invalid_slug_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.get("/lora_library/universal_state", params={"slug": "Not Valid!"})
    assert resp.status == 400


async def test_get_universal_state_missing_slug_query_param_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.get("/lora_library/universal_state")
    assert resp.status == 400


# ---------------------------------------------------- POST /universal_state/delete


async def test_post_universal_state_delete_removes_and_returns_fresh_list(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post("/lora_library/universal_state", json={"state": {"name": "Foo", "nodes": []}})

    resp = await client.post("/lora_library/universal_state/delete", json={"slug": "foo"})
    assert resp.status == 200
    body = await resp.json()
    assert set(body) == {"ok", "states"}
    assert body["ok"] is True
    assert body["states"] == []

    missing = await client.get("/lora_library/universal_state", params={"slug": "foo"})
    assert missing.status == 404


async def test_post_universal_state_delete_unknown_slug_is_404(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/universal_state/delete", json={"slug": "does-not-exist"}
    )
    assert resp.status == 404


async def test_post_universal_state_delete_invalid_slug_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/universal_state/delete", json={"slug": "Not Valid!"}
    )
    assert resp.status == 400


async def test_post_universal_state_delete_missing_slug_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/lora_library/universal_state/delete", json={})
    assert resp.status == 400


# ---------------------------- warm-cache response parity (mirrors routes_sets)


async def test_post_universal_state_response_matches_a_forced_fresh_listing(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/universal_state", json={"state": {"name": "Zebra", "nodes": []}}
    )
    await client.post(
        "/lora_library/universal_state", json={"state": {"name": "Apple", "nodes": []}}
    )
    resp = await client.post(
        "/lora_library/universal_state", json={"state": {"name": "Mango", "nodes": []}}
    )
    assert resp.status == 200
    body = await resp.json()

    store.clear_caches()
    forced = store.list_states(context)
    assert body["states"] == forced
    assert [s["name"] for s in body["states"]] == ["Apple", "Mango", "Zebra"]


async def test_post_universal_state_delete_response_matches_a_forced_fresh_listing(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/universal_state", json={"state": {"name": "Alpha", "nodes": []}}
    )
    created = await client.post(
        "/lora_library/universal_state", json={"state": {"name": "Bravo", "nodes": []}}
    )
    await client.post(
        "/lora_library/universal_state", json={"state": {"name": "Charlie", "nodes": []}}
    )
    slug = (await created.json())["slug"]

    resp = await client.post("/lora_library/universal_state/delete", json={"slug": slug})
    assert resp.status == 200
    body = await resp.json()

    store.clear_caches()
    forced = store.list_states(context)
    assert body["states"] == forced
    assert [s["name"] for s in body["states"]] == ["Alpha", "Charlie"]


async def test_post_universal_state_skips_the_full_rescan_when_the_listing_was_already_warm(
    context: LibraryContext, aiohttp_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.get("/lora_library/universal_states")  # warms the listing cache

    calls: list[int] = []
    original = store._scan_states

    def counting(ctx: LibraryContext, states_dir_path):
        calls.append(1)
        return original(ctx, states_dir_path)

    monkeypatch.setattr(store, "_scan_states", counting)

    resp = await client.post(
        "/lora_library/universal_state", json={"state": {"name": "Fresh", "nodes": []}}
    )
    assert resp.status == 200
    assert calls == []


async def test_get_universal_state_repeated_reads_do_not_reparse_an_unchanged_file(
    context: LibraryContext, aiohttp_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post("/lora_library/universal_state", json={"state": {"name": "Foo", "nodes": []}})
    warm = await client.get("/lora_library/universal_state", params={"slug": "foo"})
    assert warm.status == 200

    calls: list[int] = []
    original = store.normalize_state

    def counting(raw: object):
        calls.append(1)
        return original(raw)

    monkeypatch.setattr(store, "normalize_state", counting)

    second = await client.get("/lora_library/universal_state", params={"slug": "foo"})
    third = await client.get("/lora_library/universal_state", params={"slug": "foo"})
    assert second.status == third.status == 200
    assert calls == []


# --------------------------------------------------------------------- off-loop


class _ThreadRecorder:
    def __init__(self, fn):
        self._fn = fn
        self.threads: list[int] = []

    def __call__(self, *args, **kwargs):
        self.threads.append(threading.get_ident())
        return self._fn(*args, **kwargs)


async def test_universal_state_routes_run_their_store_calls_off_the_loop_thread(
    context: LibraryContext, aiohttp_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop_thread = threading.get_ident()
    recorders = {
        name: _ThreadRecorder(getattr(store, name))
        for name in (
            "list_states_with_mtime",
            "list_states",
            "load_state",
            "save_state",
            "delete_state",
            "load_layout",
            "save_layout",
        )
    }
    for name, recorder in recorders.items():
        monkeypatch.setattr(store, name, recorder)
    client = await aiohttp_client(make_app(context))

    resp = await client.post(
        "/lora_library/universal_state", json={"state": {"name": "Alpha", "nodes": []}}
    )
    assert resp.status == 200
    slug = (await resp.json())["slug"]

    await client.get("/lora_library/universal_states")
    await client.get("/lora_library/universal_state", params={"slug": slug})
    await client.post(
        "/lora_library/universal_states/layout", json={"layout": {"categories": [], "order": {}}}
    )
    await client.post("/lora_library/universal_state/delete", json={"slug": slug})

    for name, recorder in recorders.items():
        assert recorder.threads, name
        assert all(t != loop_thread for t in recorder.threads), name


# -------------------------------------------------- unreachable library dir


@pytest.fixture
def unreachable_context(context: LibraryContext, tmp_path: Path) -> LibraryContext:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    context.save_config({"library_dir": str(blocker / "library")})
    return context


async def test_get_universal_states_unreachable_library_folder_degrades_to_empty(
    unreachable_context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(unreachable_context))
    resp = await client.get("/lora_library/universal_states")
    assert resp.status == 200
    body = await resp.json()
    assert body["states"] == []
    assert body["states_dir"].endswith("states")
    assert body["is_default_library"] is False


async def test_get_universal_state_unreachable_library_folder_is_400(
    unreachable_context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(unreachable_context))
    resp = await client.get("/lora_library/universal_state", params={"slug": "foo"})
    assert resp.status == 400
    body = await resp.json()
    assert "library folder" in body["error"]


async def test_post_universal_state_unreachable_library_folder_is_400_naming_the_folder(
    unreachable_context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(unreachable_context))
    resp = await client.post(
        "/lora_library/universal_state", json={"state": {"name": "Foo", "nodes": []}}
    )
    assert resp.status == 400
    body = await resp.json()
    assert "library folder" in body["error"]


async def test_post_universal_state_delete_unreachable_library_folder_is_400_not_404(
    unreachable_context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(unreachable_context))
    resp = await client.post("/lora_library/universal_state/delete", json={"slug": "foo"})
    assert resp.status == 400
    body = await resp.json()
    assert "library folder" in body["error"]


# ------------------------------------------------ universal_states/layout


async def test_layout_post_full_replaces_and_is_reflected_on_next_get(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    first = await client.post(
        "/lora_library/universal_state", json={"state": {"name": "alpha", "nodes": []}}
    )
    second = await client.post(
        "/lora_library/universal_state", json={"state": {"name": "bravo", "nodes": []}}
    )
    slug_a = (await first.json())["slug"]
    slug_b = (await second.json())["slug"]

    resp = await client.post(
        "/lora_library/universal_states/layout",
        json={"layout": {"categories": ["Grp"], "order": {"Grp": [slug_b, "ghost"], "": [slug_a]}}},
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["ok"] is True
    assert body["layout"] == {"categories": ["Grp"], "order": {"": [slug_a], "Grp": [slug_b]}}

    listing = await (await client.get("/lora_library/universal_states")).json()
    assert listing["layout"] == body["layout"]


async def test_layout_post_rejects_malformed_bodies(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/lora_library/universal_states/layout", data=b"not json")
    assert resp.status == 400
    resp = await client.post("/lora_library/universal_states/layout", json={"layout": "nope"})
    assert resp.status == 400


# --------------------------------------------------------- import hygiene


def test_routes_universal_states_never_imports_comfy_at_module_scope() -> None:
    from lora_library import routes_universal_states

    assert "torch" not in vars(routes_universal_states)
    assert "comfy" not in vars(routes_universal_states)
    assert "server" not in vars(routes_universal_states)
