"""Tests for the EPS Resolution size-presets routes
(eps_image/routes_resolution_presets.py), through
``routes_resolution_presets.build_routes`` (no ComfyUI) and aiohttp's own
test client (``aiohttp_client``, from ``pytest-aiohttp``).

Mirrors ``tests/test_routes_sets.py``'s ``make_app`` shape, and
``tests/test_routes_notebook.py``'s ``X-Forwarded-For`` remote-caller
convention -- see REMOTE below. Unlike the notebook routes, these have NO
loopback gate at all (feature contract §2 locality note: the presets file
always lives inside ``library_dir``, which FORMAT.md §2 already lets
non-loopback callers read AND write) -- the remote tests below prove that
directly, the same way ``test_routes_notebook.py``'s own
``test_remote_caller_may_read/write_inside_a_shared_folder`` tests prove
the positive case for the notebook's own allow-listed folders.
"""

from __future__ import annotations

from aiohttp import web

from eps_image import nodes_resolution
from eps_image import resolution_presets_store as store
from eps_image.routes_resolution_presets import build_routes
from lora_library.context import LibraryContext

REMOTE = {"X-Forwarded-For": "203.0.113.5"}

VALUES = {
    "width": 512,
    "height": 768,
    "resize_method": "stretch",
    "interpolation": "bilinear",
    "multiple_of": 64,
}


def _other_values(**overrides: object) -> dict:
    values = dict(VALUES)
    values.update(overrides)
    return values


def make_app(context: LibraryContext) -> web.Application:
    app = web.Application()
    app.add_routes(build_routes(context))
    return app


# ------------------------------------------------------- GET /eps_resolution/presets


async def test_get_presets_empty(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.get("/eps_resolution/presets")
    assert resp.status == 200
    body = await resp.json()
    assert body == {"presets": {}, "mtime": None}


async def test_get_presets_reflects_saved_presets_in_order(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/eps_resolution/presets/save", json={"name": "B", "values": VALUES}
    )
    await client.post(
        "/eps_resolution/presets/save", json={"name": "A", "values": VALUES}
    )
    resp = await client.get("/eps_resolution/presets")
    body = await resp.json()
    assert list(body["presets"].keys()) == ["B", "A"]
    assert isinstance(body["mtime"], float)


async def test_get_presets_remote_caller_succeeds(
    context: LibraryContext, aiohttp_client
) -> None:
    # §2 locality note: no loopback gate here at all -- the file already
    # lives inside library_dir.
    client = await aiohttp_client(make_app(context))
    resp = await client.get("/eps_resolution/presets", headers=REMOTE)
    assert resp.status == 200


# ------------------------------------------------------ POST .../presets/save


async def test_post_save_creates_and_returns_fresh_list(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/eps_resolution/presets/save", json={"name": "Portrait", "values": VALUES}
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["ok"] is True
    assert body["presets"] == {"Portrait": VALUES}
    assert isinstance(body["mtime"], float)


async def test_post_save_update_in_place_does_not_reorder(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post("/eps_resolution/presets/save", json={"name": "A", "values": VALUES})
    await client.post("/eps_resolution/presets/save", json={"name": "B", "values": VALUES})
    resp = await client.post(
        "/eps_resolution/presets/save",
        json={"name": "A", "values": _other_values(width=999)},
    )
    body = await resp.json()
    assert list(body["presets"].keys()) == ["A", "B"]
    assert body["presets"]["A"]["width"] == 999


async def test_post_save_malformed_json_body_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/eps_resolution/presets/save",
        data="not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400
    assert "error" in await resp.json()


async def test_post_save_non_object_body_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/eps_resolution/presets/save", json=["not", "an", "object"])
    assert resp.status == 400


async def test_post_save_missing_name_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/eps_resolution/presets/save", json={"values": VALUES})
    assert resp.status == 400


async def test_post_save_blank_name_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/eps_resolution/presets/save", json={"name": "   ", "values": VALUES}
    )
    assert resp.status == 400


async def test_post_save_name_too_long_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/eps_resolution/presets/save",
        json={"name": "x" * (store.MAX_NAME_LENGTH + 1), "values": VALUES},
    )
    assert resp.status == 400


async def test_post_save_missing_values_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/eps_resolution/presets/save", json={"name": "A"})
    assert resp.status == 400
    assert "error" in await resp.json()


async def test_post_save_non_int_width_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/eps_resolution/presets/save",
        json={"name": "A", "values": _other_values(width="512")},
    )
    assert resp.status == 400


async def test_post_save_bool_width_is_400(context: LibraryContext, aiohttp_client) -> None:
    # bool is a subclass of int in Python -- must not silently pass as a width.
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/eps_resolution/presets/save",
        json={"name": "A", "values": _other_values(width=True)},
    )
    assert resp.status == 400


async def test_post_save_width_over_max_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/eps_resolution/presets/save",
        json={"name": "A", "values": _other_values(width=nodes_resolution.WIDTH_MAX + 1)},
    )
    assert resp.status == 400


async def test_post_save_width_at_max_is_ok(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/eps_resolution/presets/save",
        json={"name": "A", "values": _other_values(width=nodes_resolution.WIDTH_MAX)},
    )
    assert resp.status == 200


async def test_post_save_negative_width_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/eps_resolution/presets/save",
        json={"name": "A", "values": _other_values(width=-1)},
    )
    assert resp.status == 400


async def test_post_save_multiple_of_over_max_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/eps_resolution/presets/save",
        json={
            "name": "A",
            "values": _other_values(multiple_of=nodes_resolution.MULTIPLE_OF_MAX + 1),
        },
    )
    assert resp.status == 400


async def test_post_save_unknown_resize_method_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/eps_resolution/presets/save",
        json={"name": "A", "values": _other_values(resize_method="explode")},
    )
    assert resp.status == 400
    body = await resp.json()
    assert "resize_method" in body["error"]


async def test_post_save_unknown_interpolation_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/eps_resolution/presets/save",
        json={"name": "A", "values": _other_values(interpolation="magic")},
    )
    assert resp.status == 400
    body = await resp.json()
    assert "interpolation" in body["error"]


async def test_post_save_every_resize_method_and_interpolation_is_accepted(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    for method in nodes_resolution.RESIZE_METHODS:
        for interp in nodes_resolution.INTERPOLATIONS:
            resp = await client.post(
                "/eps_resolution/presets/save",
                json={
                    "name": f"{method}-{interp}",
                    "values": _other_values(resize_method=method, interpolation=interp),
                },
            )
            assert resp.status == 200, (method, interp, await resp.json())


async def test_post_save_bad_base_mtime_type_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/eps_resolution/presets/save",
        json={"name": "A", "values": VALUES, "base_mtime": "not a number"},
    )
    assert resp.status == 400


async def test_post_save_stale_base_mtime_is_409_and_file_untouched(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    created = await client.post(
        "/eps_resolution/presets/save", json={"name": "A", "values": VALUES}
    )
    real_mtime = (await created.json())["mtime"]

    resp = await client.post(
        "/eps_resolution/presets/save",
        json={
            "name": "A",
            "values": _other_values(width=1),
            "base_mtime": real_mtime - 100.0,
        },
    )
    assert resp.status == 409
    body = await resp.json()
    assert "error" in body
    assert body["mtime"] == real_mtime

    fetched = await client.get("/eps_resolution/presets")
    assert (await fetched.json())["presets"]["A"]["width"] == VALUES["width"]


async def test_post_save_matching_base_mtime_succeeds(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    created = await client.post(
        "/eps_resolution/presets/save", json={"name": "A", "values": VALUES}
    )
    real_mtime = (await created.json())["mtime"]
    resp = await client.post(
        "/eps_resolution/presets/save",
        json={"name": "A", "values": _other_values(width=1), "base_mtime": real_mtime},
    )
    assert resp.status == 200


async def test_post_save_omitted_base_mtime_skips_conflict_check(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post("/eps_resolution/presets/save", json={"name": "A", "values": VALUES})
    resp = await client.post(
        "/eps_resolution/presets/save", json={"name": "A", "values": _other_values(width=1)}
    )
    assert resp.status == 200


async def test_post_save_remote_caller_succeeds(
    context: LibraryContext, aiohttp_client
) -> None:
    # §2 locality note: remote callers may WRITE here too -- this is what
    # makes presets usable from the owner's Mac against his PC/Linux.
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/eps_resolution/presets/save",
        json={"name": "A", "values": VALUES},
        headers=REMOTE,
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["ok"] is True


# ---------------------------------------------------- POST .../presets/delete


async def test_post_delete_removes_and_returns_fresh_list(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post("/eps_resolution/presets/save", json={"name": "A", "values": VALUES})
    await client.post("/eps_resolution/presets/save", json={"name": "B", "values": VALUES})

    resp = await client.post("/eps_resolution/presets/delete", json={"name": "A"})
    assert resp.status == 200
    body = await resp.json()
    assert body["ok"] is True
    assert list(body["presets"].keys()) == ["B"]


async def test_post_delete_unknown_name_is_404(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/eps_resolution/presets/delete", json={"name": "Ghost"})
    assert resp.status == 404
    assert "error" in await resp.json()


async def test_post_delete_missing_name_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/eps_resolution/presets/delete", json={})
    assert resp.status == 400


async def test_post_delete_malformed_json_body_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/eps_resolution/presets/delete",
        data="not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400


async def test_post_delete_stale_base_mtime_is_409_and_file_untouched(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    created = await client.post(
        "/eps_resolution/presets/save", json={"name": "A", "values": VALUES}
    )
    real_mtime = (await created.json())["mtime"]

    resp = await client.post(
        "/eps_resolution/presets/delete",
        json={"name": "A", "base_mtime": real_mtime - 100.0},
    )
    assert resp.status == 409
    body = await resp.json()
    assert body["mtime"] == real_mtime

    fetched = await client.get("/eps_resolution/presets")
    assert "A" in (await fetched.json())["presets"]


async def test_post_delete_matching_base_mtime_succeeds(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    created = await client.post(
        "/eps_resolution/presets/save", json={"name": "A", "values": VALUES}
    )
    real_mtime = (await created.json())["mtime"]
    resp = await client.post(
        "/eps_resolution/presets/delete", json={"name": "A", "base_mtime": real_mtime}
    )
    assert resp.status == 200


async def test_post_delete_remote_caller_succeeds(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post("/eps_resolution/presets/save", json={"name": "A", "values": VALUES})
    resp = await client.post(
        "/eps_resolution/presets/delete", json={"name": "A"}, headers=REMOTE
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["presets"] == {}
