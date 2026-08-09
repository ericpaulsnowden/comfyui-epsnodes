"""Tests for the EPSLoraPicker favorites/recents routes
(lora_library/routes_lora_picker.py), through
``routes_lora_picker.build_routes`` (no ComfyUI) and aiohttp's own test
client (``aiohttp_client``, from ``pytest-aiohttp``).

Mirrors ``tests/test_routes_resolution_presets.py``'s ``make_app`` shape and
its ``REMOTE``/unreachable-library-folder conventions: FORMAT.md §5's
``GET/POST /lora_library/picker*`` rows have NO loopback gate at all (the
same §2 locality rationale, restated in ``routes_lora_picker.py``'s own
module docstring) -- the remote test below proves that directly, the exact
way ``test_routes_resolution_presets.py``'s own
``test_post_save_remote_caller_succeeds`` proves it for the presets routes.

``context.list_loras()`` here always returns ``tests/conftest.py``'s
``FAKE_LORAS`` verbatim -- ``["detailer.safetensors",
"styles/film_grain.safetensors", "styles/cinematic.safetensors"]`` -- a
deliberately NOT-alphabetized list, so a route that resorted it would be
caught immediately.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp import web

from lora_library import nodes_picker
from lora_library.context import LibraryContext
from lora_library.routes_lora_picker import build_routes

REMOTE = {"X-Forwarded-For": "203.0.113.5"}

FAKE_LORAS = [
    "detailer.safetensors",
    "styles/film_grain.safetensors",
    "styles/cinematic.safetensors",
]


def make_app(context: LibraryContext) -> web.Application:
    app = web.Application()
    app.add_routes(build_routes(context))
    return app


# --------------------------------------------------------- GET /picker


async def test_get_picker_empty_state(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.get("/lora_library/picker")
    assert resp.status == 200
    body = await resp.json()
    assert body == {"loras": FAKE_LORAS, "favorites": [], "recents": [], "mtime": None}


async def test_get_picker_loras_order_is_untouched_list_order(
    context: LibraryContext, aiohttp_client
) -> None:
    # FAKE_LORAS is deliberately not alphabetized (film_grain before
    # cinematic) -- a route that sorted it would fail this.
    client = await aiohttp_client(make_app(context))
    body = await (await client.get("/lora_library/picker")).json()
    assert body["loras"] == FAKE_LORAS


async def test_get_picker_remote_caller_succeeds(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.get("/lora_library/picker", headers=REMOTE)
    assert resp.status == 200


# --------------------------------------------------------- POST /picker/favorite


async def test_post_favorite_on_then_reflected_in_get(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/picker/favorite",
        json={"file": "detailer.safetensors", "on": True},
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["ok"] is True
    assert body["favorites"] == ["detailer.safetensors"]
    assert isinstance(body["mtime"], float)

    fetched = await (await client.get("/lora_library/picker")).json()
    assert fetched["favorites"] == ["detailer.safetensors"]


async def test_post_favorite_off_removes_it(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/picker/favorite",
        json={"file": "detailer.safetensors", "on": True},
    )
    resp = await client.post(
        "/lora_library/picker/favorite",
        json={"file": "detailer.safetensors", "on": False},
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["favorites"] == []


async def test_post_favorite_round_trip_on_off_on(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    for on in (True, False, True):
        resp = await client.post(
            "/lora_library/picker/favorite",
            json={"file": "detailer.safetensors", "on": on},
        )
        assert resp.status == 200
    body = await (await client.get("/lora_library/picker")).json()
    assert body["favorites"] == ["detailer.safetensors"]


async def test_post_favorite_normalizes_backslashes_to_forward_slashes(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/picker/favorite",
        json={"file": "styles\\cinematic.safetensors", "on": True},
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["favorites"] == ["styles/cinematic.safetensors"]


async def test_post_favorite_not_installed_still_succeeds(
    context: LibraryContext, aiohttp_client
) -> None:
    # FORMAT.md §6.13: unstarring/starring a favorite that only exists on
    # another machine must work -- it is NOT required to be installed here.
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/picker/favorite",
        json={"file": "only_on_the_other_machine.safetensors", "on": True},
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["favorites"] == ["only_on_the_other_machine.safetensors"]


async def test_post_favorite_remote_caller_succeeds(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/picker/favorite",
        json={"file": "detailer.safetensors", "on": True},
        headers=REMOTE,
    )
    assert resp.status == 200


# --------------------------------------------------- POST /picker/favorite: 400s


async def test_post_favorite_missing_file_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/lora_library/picker/favorite", json={"on": True})
    assert resp.status == 400
    assert "error" in await resp.json()


async def test_post_favorite_blank_file_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/picker/favorite", json={"file": "   ", "on": True}
    )
    assert resp.status == 400


async def test_post_favorite_non_string_file_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/picker/favorite", json={"file": 123, "on": True}
    )
    assert resp.status == 400


async def test_post_favorite_missing_on_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/picker/favorite", json={"file": "detailer.safetensors"}
    )
    assert resp.status == 400


async def test_post_favorite_non_bool_on_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/picker/favorite",
        json={"file": "detailer.safetensors", "on": "true"},
    )
    assert resp.status == 400


async def test_post_favorite_int_on_is_400(context: LibraryContext, aiohttp_client) -> None:
    # bool is a subclass of int in Python, but not vice versa -- 1/0 must
    # not silently pass as True/False.
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/picker/favorite", json={"file": "detailer.safetensors", "on": 1}
    )
    assert resp.status == 400


async def test_post_favorite_malformed_json_body_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/picker/favorite",
        data="not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400


async def test_post_favorite_non_object_body_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/lora_library/picker/favorite", json=["not", "an", "object"])
    assert resp.status == 400


# --------------------------------------------------------- POST /picker/recent


async def test_post_recent_front_inserts_and_is_visible_through_get(
    context: LibraryContext, aiohttp_client
) -> None:
    # files[0] ends up frontmost (lora_picker_store.record_recents's own
    # documented contract: "files[0] is the MOST RECENT entry").
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/picker/recent", json={"files": ["a.safetensors", "b.safetensors"]}
    )
    assert resp.status == 200
    body = await resp.json()
    assert [row["file"] for row in body["recents"]] == ["a.safetensors", "b.safetensors"]
    assert isinstance(body["mtime"], float)

    fetched = await (await client.get("/lora_library/picker")).json()
    assert [row["file"] for row in fetched["recents"]] == ["a.safetensors", "b.safetensors"]


async def test_post_recent_reusing_a_name_moves_it_to_front(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/picker/recent", json={"files": ["a.safetensors", "b.safetensors"]}
    )
    # "a" is already frontmost; re-recording "b" alone must move IT to
    # front instead, ahead of "a" -- proves this is a real move, not a
    # coincidence of already-front-most ordering.
    resp = await client.post("/lora_library/picker/recent", json={"files": ["b.safetensors"]})
    body = await resp.json()
    assert [row["file"] for row in body["recents"]] == ["b.safetensors", "a.safetensors"]


async def test_post_recent_capped_through_the_route(
    context: LibraryContext, aiohttp_client
) -> None:
    # files[0] is the most recent, so a single over-cap POST keeps the
    # FRONT 30 (files[0..29]) and drops the tail (files[30..34]).
    client = await aiohttp_client(make_app(context))
    files = [f"lora_{i}.safetensors" for i in range(35)]
    resp = await client.post("/lora_library/picker/recent", json={"files": files})
    body = await resp.json()
    assert len(body["recents"]) == 30
    assert body["recents"][0]["file"] == "lora_0.safetensors"
    assert body["recents"][-1]["file"] == "lora_29.safetensors"


async def test_post_recent_empty_list_is_a_no_op(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    created = await client.post(
        "/lora_library/picker/recent", json={"files": ["a.safetensors"]}
    )
    before = (await created.json())["mtime"]

    resp = await client.post("/lora_library/picker/recent", json={"files": []})
    assert resp.status == 200
    body = await resp.json()
    assert [row["file"] for row in body["recents"]] == ["a.safetensors"]
    assert body["mtime"] == before


async def test_post_recent_remote_caller_succeeds(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/picker/recent", json={"files": ["a.safetensors"]}, headers=REMOTE
    )
    assert resp.status == 200


# ---------------------------------------------------- POST /picker/recent: 400s


async def test_post_recent_non_list_files_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/picker/recent", json={"files": "a.safetensors"}
    )
    assert resp.status == 400
    assert "error" in await resp.json()


async def test_post_recent_missing_files_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/lora_library/picker/recent", json={})
    assert resp.status == 400


async def test_post_recent_non_string_entry_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/picker/recent", json={"files": ["a.safetensors", 42]}
    )
    assert resp.status == 400


async def test_post_recent_blank_entry_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/lora_library/picker/recent", json={"files": ["  "]})
    assert resp.status == 400


async def test_post_recent_malformed_json_body_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/picker/recent",
        data="not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400


async def test_post_recent_non_object_body_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/lora_library/picker/recent", json=["not", "an", "object"])
    assert resp.status == 400


# --------------------------------------------------- POST /picker/clear_recents


async def test_post_clear_recents_empties_recents_keeps_favorites(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/picker/favorite",
        json={"file": "detailer.safetensors", "on": True},
    )
    await client.post(
        "/lora_library/picker/recent", json={"files": ["a.safetensors"]}
    )

    resp = await client.post("/lora_library/picker/clear_recents", json={})
    assert resp.status == 200
    body = await resp.json()
    assert body["ok"] is True
    assert body["recents"] == []
    assert isinstance(body["mtime"], float)

    fetched = await (await client.get("/lora_library/picker")).json()
    assert fetched["recents"] == []
    assert fetched["favorites"] == ["detailer.safetensors"]


async def test_post_clear_recents_tolerates_an_absent_body(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post("/lora_library/picker/recent", json={"files": ["a.safetensors"]})
    resp = await client.post("/lora_library/picker/clear_recents")
    assert resp.status == 200
    body = await resp.json()
    assert body["recents"] == []


async def test_post_clear_recents_tolerates_a_malformed_body(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/picker/clear_recents",
        data="not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 200


async def test_post_clear_recents_remote_caller_succeeds(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/lora_library/picker/clear_recents", json={}, headers=REMOTE)
    assert resp.status == 200


# --------------------------------------------------------- GET /picker/preview
# --------------------------------------------------------- GET /picker/info


@pytest.fixture
def _resolved_context(context: LibraryContext, tmp_path: Path) -> Path:
    """Points ``resolve_lora_path`` at real files under *tmp_path* for
    every ``FAKE_LORAS`` entry -- ``test_nodes_picker.py``'s own
    ``_sidecar_context`` technique, copied here so the preview/info route
    tests can write real sibling image/sidecar files and exercise real
    filesystem behavior end to end, not a fake in-memory stand-in."""
    lora_dir = tmp_path / "loras"
    lora_dir.mkdir()

    def resolve(name: str) -> str | None:
        mapping = {
            "detailer.safetensors": str(lora_dir / "detailer.safetensors"),
            "styles/film_grain.safetensors": str(lora_dir / "styles" / "film_grain.safetensors"),
            "styles/cinematic.safetensors": str(lora_dir / "styles" / "cinematic.safetensors"),
        }
        return mapping.get(name)

    context.resolve_lora_path = resolve
    return lora_dir


async def test_preview_sibling_png_found(
    context: LibraryContext, _resolved_context: Path, aiohttp_client
) -> None:
    png_bytes = b"\x89PNG\r\n\x1a\nfake-png-bytes"
    (_resolved_context / "detailer.png").write_bytes(png_bytes)
    client = await aiohttp_client(make_app(context))
    resp = await client.get(
        "/lora_library/picker/preview", params={"file": "detailer.safetensors"}
    )
    assert resp.status == 200
    assert await resp.read() == png_bytes


async def test_preview_dot_preview_jpg_found_when_plain_missing(
    context: LibraryContext, _resolved_context: Path, aiohttp_client
) -> None:
    jpg_bytes = b"\xff\xd8\xff\xe0fake-jpg-bytes"
    (_resolved_context / "detailer.preview.jpg").write_bytes(jpg_bytes)
    client = await aiohttp_client(make_app(context))
    resp = await client.get(
        "/lora_library/picker/preview", params={"file": "detailer.safetensors"}
    )
    assert resp.status == 200
    assert await resp.read() == jpg_bytes


async def test_preview_extension_priority_png_beats_jpg(
    context: LibraryContext, _resolved_context: Path, aiohttp_client
) -> None:
    png_bytes = b"this-is-the-png"
    jpg_bytes = b"this-is-the-jpg"
    (_resolved_context / "detailer.png").write_bytes(png_bytes)
    (_resolved_context / "detailer.jpg").write_bytes(jpg_bytes)
    client = await aiohttp_client(make_app(context))
    resp = await client.get(
        "/lora_library/picker/preview", params={"file": "detailer.safetensors"}
    )
    assert await resp.read() == png_bytes


async def test_preview_404_when_no_sibling_image(
    context: LibraryContext, _resolved_context: Path, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.get(
        "/lora_library/picker/preview", params={"file": "detailer.safetensors"}
    )
    assert resp.status == 404
    assert "error" in await resp.json()


async def test_preview_404_unresolvable_name(
    context: LibraryContext, _resolved_context: Path, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.get(
        "/lora_library/picker/preview", params={"file": "ghost.safetensors"}
    )
    assert resp.status == 404


async def test_preview_400_blank_file(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.get("/lora_library/picker/preview", params={"file": "   "})
    assert resp.status == 400
    assert "error" in await resp.json()


async def test_preview_400_missing_file(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.get("/lora_library/picker/preview")
    assert resp.status == 400


async def test_preview_backslash_spelling_resolves(
    context: LibraryContext, _resolved_context: Path, aiohttp_client
) -> None:
    png_bytes = b"film-grain-preview"
    style_dir = _resolved_context / "styles"
    style_dir.mkdir()
    (style_dir / "film_grain.png").write_bytes(png_bytes)
    client = await aiohttp_client(make_app(context))
    resp = await client.get(
        "/lora_library/picker/preview",
        params={"file": "styles\\film_grain.safetensors"},
    )
    assert resp.status == 200
    assert await resp.read() == png_bytes


async def test_preview_remote_caller_succeeds(
    context: LibraryContext, _resolved_context: Path, aiohttp_client
) -> None:
    (_resolved_context / "detailer.png").write_bytes(b"x")
    client = await aiohttp_client(make_app(context))
    resp = await client.get(
        "/lora_library/picker/preview",
        params={"file": "detailer.safetensors"},
        headers=REMOTE,
    )
    assert resp.status == 200


async def test_info_sidecar_text_round_trip(
    context: LibraryContext, _resolved_context: Path, aiohttp_client
) -> None:
    (_resolved_context / "detailer.txt").write_text("  detail, sharp  ", encoding="utf-8")
    client = await aiohttp_client(make_app(context))
    resp = await client.get("/lora_library/picker/info", params={"file": "detailer.safetensors"})
    assert resp.status == 200
    body = await resp.json()
    assert body == {
        "trigger_words": nodes_picker._sidecar_trigger_text(context, "detailer.safetensors")
    }
    assert body["trigger_words"] == "detail, sharp"


async def test_info_sidecar_text_capped_pinned_to_sidecar_trigger_text(
    context: LibraryContext, _resolved_context: Path, aiohttp_client
) -> None:
    (_resolved_context / "detailer.txt").write_text("x" * 5000, encoding="utf-8")
    client = await aiohttp_client(make_app(context))
    resp = await client.get("/lora_library/picker/info", params={"file": "detailer.safetensors"})
    body = await resp.json()
    assert body["trigger_words"] == nodes_picker._sidecar_trigger_text(
        context, "detailer.safetensors"
    )
    assert len(body["trigger_words"]) == 4096


async def test_info_no_sidecar_is_empty_string(
    context: LibraryContext, _resolved_context: Path, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.get("/lora_library/picker/info", params={"file": "detailer.safetensors"})
    assert resp.status == 200
    body = await resp.json()
    assert body == {"trigger_words": ""}


async def test_info_404_unresolvable_name(
    context: LibraryContext, _resolved_context: Path, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.get("/lora_library/picker/info", params={"file": "ghost.safetensors"})
    assert resp.status == 404
    assert "error" in await resp.json()


async def test_info_400_blank_file(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.get("/lora_library/picker/info", params={"file": ""})
    assert resp.status == 400


async def test_info_remote_caller_succeeds(
    context: LibraryContext, _resolved_context: Path, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.get(
        "/lora_library/picker/info",
        params={"file": "detailer.safetensors"},
        headers=REMOTE,
    )
    assert resp.status == 200


# --------------------------------------------------- POST /picker/favorites_order


async def test_post_favorites_order_round_trip(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/picker/favorite", json={"file": "a.safetensors", "on": True}
    )
    await client.post(
        "/lora_library/picker/favorite", json={"file": "b.safetensors", "on": True}
    )
    await client.post(
        "/lora_library/picker/favorite", json={"file": "c.safetensors", "on": True}
    )

    resp = await client.post(
        "/lora_library/picker/favorites_order",
        json={"files": ["c.safetensors", "a.safetensors", "b.safetensors"]},
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["ok"] is True
    assert body["favorites"] == ["c.safetensors", "a.safetensors", "b.safetensors"]
    assert isinstance(body["mtime"], float)

    fetched = await (await client.get("/lora_library/picker")).json()
    assert fetched["favorites"] == ["c.safetensors", "a.safetensors", "b.safetensors"]


async def test_post_favorites_order_unknown_names_dropped(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/picker/favorite", json={"file": "a.safetensors", "on": True}
    )
    resp = await client.post(
        "/lora_library/picker/favorites_order",
        json={"files": ["ghost.safetensors", "a.safetensors"]},
    )
    body = await resp.json()
    assert body["favorites"] == ["a.safetensors"]


async def test_post_favorites_order_missing_favorite_appended_at_end(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/picker/favorite", json={"file": "a.safetensors", "on": True}
    )
    await client.post(
        "/lora_library/picker/favorite", json={"file": "b.safetensors", "on": True}
    )
    resp = await client.post(
        "/lora_library/picker/favorites_order", json={"files": ["b.safetensors"]}
    )
    body = await resp.json()
    assert body["favorites"] == ["b.safetensors", "a.safetensors"]


async def test_post_favorites_order_remote_caller_succeeds(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/picker/favorites_order", json={"files": []}, headers=REMOTE
    )
    assert resp.status == 200


async def test_post_favorites_order_non_list_files_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/picker/favorites_order", json={"files": "a.safetensors"}
    )
    assert resp.status == 400
    assert "error" in await resp.json()


async def test_post_favorites_order_missing_files_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/lora_library/picker/favorites_order", json={})
    assert resp.status == 400


async def test_post_favorites_order_non_string_entry_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/picker/favorites_order", json={"files": ["a.safetensors", 42]}
    )
    assert resp.status == 400


async def test_post_favorites_order_blank_entry_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/lora_library/picker/favorites_order", json={"files": ["  "]})
    assert resp.status == 400


async def test_post_favorites_order_malformed_json_body_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/picker/favorites_order",
        data="not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400


async def test_post_favorites_order_non_object_body_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/picker/favorites_order", json=["not", "an", "object"]
    )
    assert resp.status == 400


# -------------------------------------------------- unreachable library dir


@pytest.fixture
def unreachable_context(context: LibraryContext, tmp_path: Path) -> LibraryContext:
    """*context* reconfigured so ``library_dir()``'s on-demand mkdir raises
    OSError (configured path's parent is a plain FILE) -- the same
    unmounted-NAS condition ``test_routes_resolution_presets.py`` and
    ``test_lora_picker_store.py`` each exercise the identical way."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    context.save_config({"library_dir": str(blocker / "library")})
    return context


async def test_get_picker_unreachable_library_folder_degrades_to_empty(
    unreachable_context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(unreachable_context))
    resp = await client.get("/lora_library/picker")
    assert resp.status == 200
    body = await resp.json()
    # The installed-lora list doesn't live under library_dir -- it survives
    # even while the picker's own favorites/recents file is unreachable.
    assert body["loras"] == FAKE_LORAS
    assert body["favorites"] == []
    assert body["recents"] == []
    assert body["mtime"] is None


async def test_post_favorite_unreachable_library_folder_is_400_naming_the_folder(
    unreachable_context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(unreachable_context))
    resp = await client.post(
        "/lora_library/picker/favorite",
        json={"file": "detailer.safetensors", "on": True},
    )
    assert resp.status == 400
    body = await resp.json()
    assert "library folder" in body["error"]


async def test_post_recent_unreachable_library_folder_is_400_naming_the_folder(
    unreachable_context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(unreachable_context))
    resp = await client.post(
        "/lora_library/picker/recent", json={"files": ["detailer.safetensors"]}
    )
    assert resp.status == 400
    body = await resp.json()
    assert "library folder" in body["error"]


async def test_post_clear_recents_unreachable_library_folder_is_400_naming_the_folder(
    unreachable_context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(unreachable_context))
    resp = await client.post("/lora_library/picker/clear_recents", json={})
    assert resp.status == 400
    body = await resp.json()
    assert "library folder" in body["error"]


async def test_post_favorites_order_unreachable_library_folder_is_400_naming_the_folder(
    unreachable_context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(unreachable_context))
    resp = await client.post(
        "/lora_library/picker/favorites_order", json={"files": ["detailer.safetensors"]}
    )
    assert resp.status == 400
    body = await resp.json()
    assert "library folder" in body["error"]
