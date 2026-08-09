"""Tests for the FORMAT.md §5 set routes, through
``lora_library.routes.build_routes`` (no ComfyUI) and aiohttp's own test
client (``aiohttp_client``, from the ``pytest-aiohttp`` plugin).

``build_routes`` also tries to import ``routes_notebook`` (a parallel
workstream's file) and, per its own defensive try/except, logs and skips it
if that module isn't present yet or fails to import — expected noise in
this file's test output, not a failure of anything owned here. These tests
never assert on that log one way or the other, so they pass regardless of
whether ``routes_notebook.py`` exists yet.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp import web

from lora_library import sets_store
from lora_library.context import LibraryContext
from lora_library.routes import build_routes


def make_app(context: LibraryContext) -> web.Application:
    app = web.Application()
    app.add_routes(build_routes(context))
    return app


# -------------------------------------------------------------- GET /sets


async def test_get_sets_empty(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.get("/lora_library/sets")
    assert resp.status == 200
    assert await resp.json() == {"sets": []}


async def test_get_sets_reflects_saved_sets_sorted_by_name(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post("/lora_library/set", json={"set": {"name": "Zebra", "loras": []}})
    await client.post("/lora_library/set", json={"set": {"name": "Apple", "loras": []}})

    resp = await client.get("/lora_library/sets")
    body = await resp.json()
    assert [s["name"] for s in body["sets"]] == ["Apple", "Zebra"]


# -------------------------------------------------------------- POST /set


async def test_post_set_creates_and_derives_slug(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/set",
        json={
            "set": {
                "name": "Cinematic portrait",
                "loras": [{"file": "detailer.safetensors", "strength": 0.8}],
                "trigger_words": "cinematic",
            }
        },
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["ok"] is True
    assert body["slug"] == "cinematic-portrait"
    assert [s["slug"] for s in body["sets"]] == ["cinematic-portrait"]


async def test_post_set_repeated_name_gets_collision_suffix(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    first = await client.post("/lora_library/set", json={"set": {"name": "Foo", "loras": []}})
    second = await client.post("/lora_library/set", json={"set": {"name": "Foo", "loras": []}})
    assert (await first.json())["slug"] == "foo"
    assert (await second.json())["slug"] == "foo-2"


async def test_post_set_with_explicit_slug_updates_in_place(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    created = await client.post("/lora_library/set", json={"set": {"name": "Foo", "loras": []}})
    slug = (await created.json())["slug"]

    updated = await client.post(
        "/lora_library/set",
        json={"slug": slug, "set": {"name": "Foo Renamed", "loras": []}},
    )
    assert updated.status == 200
    body = await updated.json()
    assert body["slug"] == slug  # explicit slug is stable across a rename
    assert len(body["sets"]) == 1  # updated in place, not duplicated

    fetched = await client.get("/lora_library/set", params={"slug": slug})
    assert (await fetched.json())["name"] == "Foo Renamed"


async def test_post_set_slug_form_rename_persists_new_name_and_rows_in_place(
    context: LibraryContext, aiohttp_client
) -> None:
    """web/lora_library/controller.js's "Save State" now sends exactly this
    shape for a rename (2026-07-22 reversal, file header section B): a
    slug-form POST whose ``set.name`` differs from the file's current name,
    carrying a freshly re-captured (and here deliberately DIFFERENT) rows
    list in the same request. Unlike
    ``test_post_set_with_explicit_slug_updates_in_place`` above (empty
    ``loras`` throughout, so it can't tell a preserved row from a dropped
    one), this proves the rows travelling alongside the rename are the ones
    that land on disk, under the SAME slug/file — no second file minted.
    """
    client = await aiohttp_client(make_app(context))
    created = await client.post(
        "/lora_library/set",
        json={"set": {"name": "Original", "loras": [{"file": "detailer.safetensors"}]}},
    )
    slug = (await created.json())["slug"]

    renamed = await client.post(
        "/lora_library/set",
        json={
            "slug": slug,
            "set": {
                "name": "Renamed",
                "loras": [
                    {"file": "detailer.safetensors", "strength": 0.8},
                    {"file": "styles/film_grain.safetensors", "strength": 0.4},
                ],
            },
        },
    )
    assert renamed.status == 200
    body = await renamed.json()
    assert body["slug"] == slug  # same slug — no new file minted
    assert len(body["sets"]) == 1
    assert body["sets"][0]["name"] == "Renamed"

    fetched = await client.get("/lora_library/set", params={"slug": slug})
    saved = await fetched.json()
    assert saved["slug"] == slug
    assert saved["name"] == "Renamed"
    assert [row["file"] for row in saved["loras"]] == [
        "detailer.safetensors",
        "styles/film_grain.safetensors",
    ]

    # Exactly one file on disk for this slug — the rename really updated in
    # place rather than minting a sibling (sets_store.save_set's
    # caller-supplied-slug contract, lines 295-297).
    assert [p.stem for p in context.sets_dir().glob("*.json")] == [slug]


async def test_post_set_malformed_json_body_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/set", data="not json", headers={"Content-Type": "application/json"}
    )
    assert resp.status == 400
    assert "error" in await resp.json()


async def test_post_set_non_object_body_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/lora_library/set", json=["not", "an", "object"])
    assert resp.status == 400


async def test_post_set_missing_set_key_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/lora_library/set", json={})
    assert resp.status == 400
    assert "error" in await resp.json()


async def test_post_set_invalid_slug_in_body_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/set",
        json={"slug": "Not A Valid Slug!", "set": {"name": "Foo", "loras": []}},
    )
    assert resp.status == 400
    assert "error" in await resp.json()


async def test_post_set_invalid_set_payload_is_400_with_clear_message(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/lora_library/set", json={"set": {"name": "x", "loras": "nope"}})
    assert resp.status == 400
    body = await resp.json()
    assert "loras" in body["error"]


async def test_post_set_format_too_new_is_400_and_mentions_update_the_pack(
    context: LibraryContext, aiohttp_client
) -> None:
    # FORMAT.md §4.1 (2026-07-20): CURRENT_FORMAT is now 2 (the composite
    # schema), so a plain `"format": 2` payload with no `loaders` key is no
    # longer "too new" — it degrades gracefully to format 1 (see
    # tests/test_sets_store.py's TestFormatValidation for that contract).
    # This test now targets a GENUINELY newer format, one past whatever the
    # pack currently understands, to keep exercising the "reject a real
    # future format" behavior the route is meant to guard.
    too_new = sets_store.CURRENT_FORMAT + 1
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/set", json={"set": {"format": too_new, "name": "x", "loras": []}}
    )
    assert resp.status == 400
    body = await resp.json()
    assert "update the pack" in body["error"]


async def test_post_set_format_2_with_loaders_saves_a_composite_state(
    context: LibraryContext, aiohttp_client
) -> None:
    """FORMAT.md §4.1: the route is a thin pass-through onto
    ``sets_store.save_set``/``normalize_set`` — no route-layer change was
    needed for composite support, so this locks that in against a
    regression at the HTTP boundary specifically (unit coverage of
    ``normalize_set`` itself lives in test_sets_store.py)."""
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/set",
        json={
            "set": {
                "format": 2,
                "name": "WAN hi+lo",
                "loaders": [
                    {"loras": [{"file": "high.safetensors"}]},
                    {"loras": [{"file": "low.safetensors"}]},
                ],
            }
        },
    )
    assert resp.status == 200
    body = await resp.json()

    get_resp = await client.get("/lora_library/set", params={"slug": body["slug"]})
    saved = await get_resp.json()
    assert saved["format"] == 2
    assert len(saved["loaders"]) == 2
    assert saved["loaders"][0]["loras"][0]["file"] == "high.safetensors"
    assert saved["loaders"][1]["loras"][0]["file"] == "low.safetensors"
    assert saved["loras"] == saved["loaders"][0]["loras"]


# --------------------------------------------------------------- GET /set


async def test_get_set_returns_full_shape_plus_slug(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/set",
        json={
            "set": {
                "name": "Foo",
                "loras": [{"file": "detailer.safetensors", "strength": 0.8}],
                "trigger_words": "foo, bar",
                "notes": "hello",
            }
        },
    )
    resp = await client.get("/lora_library/set", params={"slug": "foo"})
    assert resp.status == 200
    body = await resp.json()
    assert body["slug"] == "foo"
    assert body["name"] == "Foo"
    assert body["format"] == 1
    assert body["trigger_words"] == "foo, bar"
    assert body["notes"] == "hello"
    assert body["loras"] == [
        {"file": "detailer.safetensors", "on": True, "strength": 0.8, "strength_clip": None}
    ]


async def test_get_set_unknown_slug_is_404(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.get("/lora_library/set", params={"slug": "does-not-exist"})
    assert resp.status == 404
    assert "error" in await resp.json()


async def test_get_set_invalid_slug_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.get("/lora_library/set", params={"slug": "Not Valid!"})
    assert resp.status == 400


async def test_get_set_missing_slug_query_param_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.get("/lora_library/set")
    assert resp.status == 400


# ---------------------------------------------------------- POST /set/delete


async def test_post_set_delete_removes_and_returns_fresh_list(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post("/lora_library/set", json={"set": {"name": "Foo", "loras": []}})

    resp = await client.post("/lora_library/set/delete", json={"slug": "foo"})
    assert resp.status == 200
    body = await resp.json()
    assert body["ok"] is True
    assert body["sets"] == []

    missing = await client.get("/lora_library/set", params={"slug": "foo"})
    assert missing.status == 404


async def test_post_set_delete_unknown_slug_is_404(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/lora_library/set/delete", json={"slug": "does-not-exist"})
    assert resp.status == 404


async def test_post_set_delete_invalid_slug_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/lora_library/set/delete", json={"slug": "Not Valid!"})
    assert resp.status == 400


async def test_post_set_delete_missing_slug_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/lora_library/set/delete", json={})
    assert resp.status == 400


# -------------------------------------------------- unreachable library dir


@pytest.fixture
def unreachable_context(context: LibraryContext, tmp_path: Path) -> LibraryContext:
    """*context* reconfigured so ``sets_dir()``'s on-demand mkdir raises
    OSError (configured library path's parent is a plain FILE) — the audit
    2026-08-08 unmounted-NAS condition. Every set route must answer with
    real HTTP semantics (200-empty listing, 400 elsewhere), never a raw
    500."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    context.save_config({"library_dir": str(blocker / "library")})
    return context


async def test_get_sets_unreachable_library_folder_degrades_to_empty(
    unreachable_context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(unreachable_context))
    resp = await client.get("/lora_library/sets")
    assert resp.status == 200
    assert await resp.json() == {"sets": []}


async def test_get_set_unreachable_library_folder_is_400(
    unreachable_context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(unreachable_context))
    resp = await client.get("/lora_library/set", params={"slug": "foo"})
    assert resp.status == 400
    body = await resp.json()
    assert "library folder" in body["error"]


async def test_post_set_unreachable_library_folder_is_400_naming_the_folder(
    unreachable_context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(unreachable_context))
    resp = await client.post(
        "/lora_library/set", json={"set": {"name": "Foo", "loras": []}}
    )
    assert resp.status == 400
    body = await resp.json()
    assert "library folder" in body["error"]


async def test_post_set_delete_unreachable_library_folder_is_400_not_404(
    unreachable_context: LibraryContext, aiohttp_client
) -> None:
    # Without the store guard this would read as 404 "no such set" — or
    # 500 on the raw OSError (the original audit finding).
    client = await aiohttp_client(make_app(unreachable_context))
    resp = await client.post("/lora_library/set/delete", json={"slug": "foo"})
    assert resp.status == 400
    body = await resp.json()
    assert "library folder" in body["error"]
