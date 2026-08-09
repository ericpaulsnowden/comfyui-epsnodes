"""Tests for the FORMAT.md §5 notebook routes, through
``lora_library.routes.build_routes`` (no ComfyUI) and aiohttp's own test
client (``aiohttp_client``, from ``pytest-aiohttp``).

``build_routes`` also tries to import ``routes_sets`` (a parallel
workstream's file) and, per its own defensive try/except, logs and skips it
if broken — expected noise here, not a failure of anything owned by this
file.

Remote (non-loopback) callers are simulated with the ``X-Forwarded-For``
header (FORMAT.md §2 / ``routes.request_is_loopback``'s own contract: any
forwarded request is treated as non-loopback regardless of its value).
"""

from __future__ import annotations

from pathlib import Path

from aiohttp import web

from lora_library import routes_notebook
from lora_library.context import LibraryContext
from lora_library.routes import build_routes

REMOTE = {"X-Forwarded-For": "203.0.113.5"}


def make_app(context: LibraryContext) -> web.Application:
    app = web.Application()
    app.add_routes(build_routes(context))
    return app


# --------------------------------------------------------- GET /notebook


async def test_get_notebook_missing_file_is_not_an_error(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.get("/lora_library/notebook", params={"file": "loras.md"})
    assert resp.status == 200
    body = await resp.json()
    assert body["exists"] is False
    assert body["mtime"] is None
    assert body["entries"] == []
    assert body["problems"] == []


async def test_get_notebook_defaults_to_loras_md(
    context: LibraryContext, library_dir: Path, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.get("/lora_library/notebook")
    body = await resp.json()
    assert body["file"] == str(library_dir / "loras.md")


async def test_get_notebook_lists_entries_and_reports_problems(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "Foo", "text": "body", "category": "Cat A"},
    )
    resp = await client.get("/lora_library/notebook", params={"file": "loras.md"})
    body = await resp.json()
    assert body["exists"] is True
    assert isinstance(body["mtime"], float)
    assert body["entries"] == [{"name": "Foo", "category": "Cat A"}]


async def test_get_notebook_remote_caller_outside_library_dir_is_403(
    context: LibraryContext, tmp_path: Path, aiohttp_client
) -> None:
    outside = tmp_path / "elsewhere.md"
    outside.write_text("## Secret\nnope\n", encoding="utf-8")
    client = await aiohttp_client(make_app(context))
    resp = await client.get(
        "/lora_library/notebook", params={"file": str(outside)}, headers=REMOTE
    )
    assert resp.status == 403
    assert "error" in await resp.json()


async def test_get_notebook_loopback_caller_may_read_outside_library_dir(
    context: LibraryContext, tmp_path: Path, aiohttp_client
) -> None:
    outside = tmp_path / "elsewhere.md"
    outside.write_text("## Secret\nshh\n", encoding="utf-8")
    client = await aiohttp_client(make_app(context))
    resp = await client.get("/lora_library/notebook", params={"file": str(outside)})
    assert resp.status == 200
    body = await resp.json()
    assert body["entries"] == [{"name": "Secret", "category": ""}]


async def test_get_notebook_non_string_file_query_is_still_a_string_from_query_params(
    context: LibraryContext, aiohttp_client
) -> None:
    # Query params are always strings; this just documents that an absent
    # `file` behaves like an empty string (falls back to the default).
    client = await aiohttp_client(make_app(context))
    resp = await client.get("/lora_library/notebook")
    assert resp.status == 200


async def test_get_notebook_missing_file_categories_is_empty_list(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.get("/lora_library/notebook", params={"file": "loras.md"})
    assert (await resp.json())["categories"] == []


async def test_get_notebook_categories_in_file_order(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "E1", "text": "b1", "category": "Cat A"},
    )
    await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "E2", "text": "b2", "category": "Cat B"},
    )
    resp = await client.get("/lora_library/notebook", params={"file": "loras.md"})
    assert (await resp.json())["categories"] == ["Cat A", "Cat B"]


async def test_get_notebook_categories_includes_an_empty_category(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/category", json={"file": "loras.md", "name": "Empty Cat"}
    )
    resp = await client.get("/lora_library/notebook", params={"file": "loras.md"})
    body = await resp.json()
    assert body["categories"] == ["Empty Cat"]
    assert body["entries"] == []


# --------------------------------------------------- GET /notebook/category


async def test_get_notebook_category_success(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/category",
        json={"file": "loras.md", "name": "Styles", "description": "Prose about styles."},
    )
    resp = await client.get(
        "/lora_library/notebook/category", params={"file": "loras.md", "name": "Styles"}
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["name"] == "Styles"
    assert body["description"] == "Prose about styles."
    assert isinstance(body["mtime"], float)


async def test_get_notebook_category_with_no_description_is_empty_string(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post("/lora_library/notebook/category", json={"file": "loras.md", "name": "Bare"})
    resp = await client.get(
        "/lora_library/notebook/category", params={"file": "loras.md", "name": "Bare"}
    )
    assert (await resp.json())["description"] == ""


async def test_get_notebook_category_missing_category_is_404(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post("/lora_library/notebook/category", json={"file": "loras.md", "name": "Real"})
    resp = await client.get(
        "/lora_library/notebook/category", params={"file": "loras.md", "name": "does-not-exist"}
    )
    assert resp.status == 404
    assert "error" in await resp.json()


async def test_get_notebook_category_missing_file_is_404(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.get(
        "/lora_library/notebook/category", params={"file": "loras.md", "name": "Styles"}
    )
    assert resp.status == 404


async def test_get_notebook_category_remote_outside_library_dir_is_403(
    context: LibraryContext, tmp_path: Path, aiohttp_client
) -> None:
    outside = tmp_path / "elsewhere.md"
    outside.write_text("# Styles\nprose\n", encoding="utf-8")
    client = await aiohttp_client(make_app(context))
    resp = await client.get(
        "/lora_library/notebook/category",
        params={"file": str(outside), "name": "Styles"},
        headers=REMOTE,
    )
    assert resp.status == 403


# -------------------------------------------------- POST /notebook/category


async def test_post_category_create_unknown_name_with_description(
    context: LibraryContext, library_dir: Path, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/category",
        json={"file": "loras.md", "name": "Styles", "description": "Prose about styles."},
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["ok"] is True
    assert body["categories"] == ["Styles"]
    assert body["entries"] == []
    assert isinstance(body["mtime"], float)
    raw = (library_dir / "loras.md").read_text(encoding="utf-8")
    assert raw == "# Styles\nProse about styles.\n"


async def test_post_category_create_unknown_name_without_description(
    context: LibraryContext, library_dir: Path, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/category", json={"file": "loras.md", "name": "Bare"}
    )
    assert resp.status == 200
    raw = (library_dir / "loras.md").read_text(encoding="utf-8")
    assert raw == "# Bare\n"


async def test_post_category_create_appends_after_existing_entries(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "E1", "text": "b1"}
    )
    resp = await client.post(
        "/lora_library/notebook/category",
        json={"file": "loras.md", "name": "Styles", "description": "d"},
    )
    body = await resp.json()
    assert body["entries"] == [{"name": "E1", "category": ""}]
    assert body["categories"] == ["Styles"]


async def test_post_category_known_name_replaces_description(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/category",
        json={"file": "loras.md", "name": "Styles", "description": "old"},
    )
    resp = await client.post(
        "/lora_library/notebook/category",
        json={"file": "loras.md", "name": "Styles", "description": "new"},
    )
    assert resp.status == 200
    fetched = await client.get(
        "/lora_library/notebook/category", params={"file": "loras.md", "name": "Styles"}
    )
    assert (await fetched.json())["description"] == "new"


async def test_post_category_replace_does_not_disturb_its_entries(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "E1", "text": "body1", "category": "Cat A"},
    )
    resp = await client.post(
        "/lora_library/notebook/category",
        json={"file": "loras.md", "name": "Cat A", "description": "new description"},
    )
    body = await resp.json()
    assert body["entries"] == [{"name": "E1", "category": "Cat A"}]
    fetched = await client.get(
        "/lora_library/notebook/entry", params={"file": "loras.md", "name": "E1"}
    )
    assert (await fetched.json())["text"] == "body1"


async def test_post_category_missing_name_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/lora_library/notebook/category", json={"file": "loras.md"})
    assert resp.status == 400


async def test_post_category_blank_name_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/category", json={"file": "loras.md", "name": "   "}
    )
    assert resp.status == 400


async def test_post_category_non_string_description_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/category",
        json={"file": "loras.md", "name": "Styles", "description": 5},
    )
    assert resp.status == 400


async def test_post_category_description_heading_line_is_demoted_not_400(
    context: LibraryContext, aiohttp_client
) -> None:
    # §3.4 demote-don't-refuse (v0.48.1) -- this used to be a 400.
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/category",
        json={"file": "loras.md", "name": "Styles", "description": "line\n## looks like heading"},
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["adjusted_headings"] == 1
    assert body["description"] == "line\n#### looks like heading"

    read_back = await client.get(
        "/lora_library/notebook/category", params={"file": "loras.md", "name": "Styles"}
    )
    assert (await read_back.json())["description"] == "line\n#### looks like heading"


async def test_post_category_malformed_json_body_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/category",
        data="not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400


async def test_post_category_non_object_body_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/lora_library/notebook/category", json=["nope"])
    assert resp.status == 400


async def test_post_category_requires_md_suffix_even_for_loopback(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/category", json={"file": "notes.txt", "name": "Styles"}
    )
    assert resp.status == 403
    assert "error" in await resp.json()


async def test_post_category_remote_outside_library_dir_is_403(
    context: LibraryContext, tmp_path: Path, aiohttp_client
) -> None:
    outside = tmp_path / "elsewhere.md"
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/category",
        json={"file": str(outside), "name": "Styles"},
        headers=REMOTE,
    )
    assert resp.status == 403
    assert not outside.exists()


async def test_post_category_stale_base_mtime_is_409_and_file_untouched(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    created = await client.post(
        "/lora_library/notebook/category",
        json={"file": "loras.md", "name": "Styles", "description": "orig"},
    )
    real_mtime = (await created.json())["mtime"]

    resp = await client.post(
        "/lora_library/notebook/category",
        json={
            "file": "loras.md",
            "name": "Styles",
            "description": "hijacked",
            "base_mtime": real_mtime - 100.0,
        },
    )
    assert resp.status == 409
    body = await resp.json()
    assert "error" in body
    assert body["mtime"] == real_mtime

    fetched = await client.get(
        "/lora_library/notebook/category", params={"file": "loras.md", "name": "Styles"}
    )
    assert (await fetched.json())["description"] == "orig"


async def test_post_category_matching_base_mtime_succeeds(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    created = await client.post(
        "/lora_library/notebook/category", json={"file": "loras.md", "name": "Styles"}
    )
    real_mtime = (await created.json())["mtime"]
    resp = await client.post(
        "/lora_library/notebook/category",
        json={
            "file": "loras.md",
            "name": "Styles",
            "description": "updated",
            "base_mtime": real_mtime,
        },
    )
    assert resp.status == 200


async def test_post_category_preserves_crlf_line_endings(
    context: LibraryContext, library_dir: Path, aiohttp_client
) -> None:
    (library_dir / "loras.md").write_bytes(b"# Cat A\r\n## E1\r\nB1\r\n")
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/category",
        json={"file": "loras.md", "name": "Cat A", "description": "New."},
    )
    assert resp.status == 200
    # newline="" so Python's own universal-newline translation doesn't hide
    # the very thing being asserted on (see markdown_store.load_notebook's
    # own doc comment for why it reads this way too).
    with open(library_dir / "loras.md", encoding="utf-8", newline="") as fh:
        raw = fh.read()
    assert raw.count("\n") == raw.count("\r\n")
    assert raw == "# Cat A\r\nNew.\r\n## E1\r\nB1\r\n"


async def test_post_category_create_with_after_positions_new_heading(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post("/lora_library/notebook/category", json={"file": "loras.md", "name": "Cat A"})
    await client.post("/lora_library/notebook/category", json={"file": "loras.md", "name": "Cat B"})
    resp = await client.post(
        "/lora_library/notebook/category",
        json={"file": "loras.md", "name": "New Cat", "after": "Cat A"},
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["categories"] == ["Cat A", "New Cat", "Cat B"]


async def test_post_category_rename_changes_heading_and_keeps_entries(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "E1", "text": "body1", "category": "Cat A"},
    )
    resp = await client.post(
        "/lora_library/notebook/category",
        json={"file": "loras.md", "name": "Cat A", "description": "d", "rename_to": "Cat A2"},
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["categories"] == ["Cat A2"]
    assert body["entries"] == [{"name": "E1", "category": "Cat A2"}]
    fetched = await client.get(
        "/lora_library/notebook/entry", params={"file": "loras.md", "name": "E1"}
    )
    assert (await fetched.json())["text"] == "body1"


async def test_post_category_rename_to_duplicate_name_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post("/lora_library/notebook/category", json={"file": "loras.md", "name": "Cat A"})
    await client.post("/lora_library/notebook/category", json={"file": "loras.md", "name": "Cat B"})
    resp = await client.post(
        "/lora_library/notebook/category",
        json={"file": "loras.md", "name": "Cat A", "rename_to": "Cat B"},
    )
    assert resp.status == 400
    assert "error" in await resp.json()
    # Unchanged after the refused rename.
    fetched = await client.get("/lora_library/notebook", params={"file": "loras.md"})
    assert (await fetched.json())["categories"] == ["Cat A", "Cat B"]


async def test_post_category_rename_to_is_ignored_when_creating(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/category",
        json={"file": "loras.md", "name": "Brand New", "rename_to": "Something Else"},
    )
    assert resp.status == 200
    body = await resp.json()
    # `rename_to` only applies to a known (existing) name — on create it's
    # silently ignored, so the category is created under its given name.
    assert body["categories"] == ["Brand New"]


async def test_post_category_non_string_after_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/category",
        json={"file": "loras.md", "name": "Styles", "after": 5},
    )
    assert resp.status == 400


async def test_post_category_non_string_rename_to_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/category", json={"file": "loras.md", "name": "Styles"}
    )
    resp = await client.post(
        "/lora_library/notebook/category",
        json={"file": "loras.md", "name": "Styles", "rename_to": 5},
    )
    assert resp.status == 400


# ----------------------------------------------------- GET /notebook/entry


async def test_get_notebook_entry_success(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "Foo", "text": "hello", "category": "Cat A"},
    )
    resp = await client.get(
        "/lora_library/notebook/entry", params={"file": "loras.md", "name": "Foo"}
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["name"] == "Foo"
    assert body["category"] == "Cat A"
    assert body["text"] == "hello"
    assert isinstance(body["mtime"], float)


async def test_get_notebook_entry_missing_entry_is_404(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "Foo", "text": "x"}
    )
    resp = await client.get(
        "/lora_library/notebook/entry", params={"file": "loras.md", "name": "does-not-exist"}
    )
    assert resp.status == 404
    assert "error" in await resp.json()


async def test_get_notebook_entry_missing_file_is_404(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.get(
        "/lora_library/notebook/entry", params={"file": "loras.md", "name": "Foo"}
    )
    assert resp.status == 404


async def test_get_notebook_entry_remote_outside_library_dir_is_403(
    context: LibraryContext, tmp_path: Path, aiohttp_client
) -> None:
    outside = tmp_path / "elsewhere.md"
    outside.write_text("## Foo\nbody\n", encoding="utf-8")
    client = await aiohttp_client(make_app(context))
    resp = await client.get(
        "/lora_library/notebook/entry",
        params={"file": str(outside), "name": "Foo"},
        headers=REMOTE,
    )
    assert resp.status == 403


# ---------------------------------------------------- POST /notebook/entry


async def test_post_entry_creates_new_file_and_entry(
    context: LibraryContext, library_dir: Path, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "Foo", "text": "hello"},
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["ok"] is True
    assert body["entries"] == [{"name": "Foo", "category": ""}]
    assert (library_dir / "loras.md").exists()


async def test_post_entry_create_appends_new_category(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "E1", "text": "a"}
    )
    resp = await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "E2", "text": "b", "category": "Cat A"},
    )
    body = await resp.json()
    assert body["entries"] == [
        {"name": "E1", "category": ""},
        {"name": "E2", "category": "Cat A"},
    ]


async def test_post_entry_update_replaces_text_in_place(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "Foo", "text": "old"}
    )
    resp = await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "Foo", "text": "new"}
    )
    assert resp.status == 200
    fetched = await client.get(
        "/lora_library/notebook/entry", params={"file": "loras.md", "name": "Foo"}
    )
    assert (await fetched.json())["text"] == "new"


async def test_post_entry_rename(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "Old", "text": "body"}
    )
    resp = await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "Old", "text": "body", "rename_to": "New"},
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["entries"] == [{"name": "New", "category": ""}]
    missing = await client.get(
        "/lora_library/notebook/entry", params={"file": "loras.md", "name": "Old"}
    )
    assert missing.status == 404


async def test_post_entry_rename_collision_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "A", "text": "a"}
    )
    await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "B", "text": "b"}
    )
    resp = await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "A", "text": "a", "rename_to": "B"},
    )
    assert resp.status == 400
    assert "error" in await resp.json()


async def test_post_entry_blank_name_on_create_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "   ", "text": "x"}
    )
    assert resp.status == 400


async def test_post_entry_heading_line_is_demoted_not_400(
    context: LibraryContext, aiohttp_client
) -> None:
    # §3.4 demote-don't-refuse (v0.48.1) -- this used to be a 400, which
    # surfaced as "notebooks can't save when content has # in the body".
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "Foo", "text": "line\n# looks like a heading"},
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["adjusted_headings"] == 1
    assert body["text"] == "line\n### looks like a heading"

    read_back = await client.get(
        "/lora_library/notebook/entry", params={"file": "loras.md", "name": "Foo"}
    )
    assert (await read_back.json())["text"] == "line\n### looks like a heading"


async def test_post_entry_without_headings_reports_zero_and_omits_text(
    context: LibraryContext, aiohttp_client
) -> None:
    # The ordinary save's wire shape: the counter rides every response, the
    # stored-text echo only when something was actually adjusted.
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "Foo", "text": "plain ### body"},
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["adjusted_headings"] == 0
    assert "text" not in body


async def test_post_entry_missing_name_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "text": "x"}
    )
    assert resp.status == 400


async def test_post_entry_non_string_text_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "Foo", "text": 5}
    )
    assert resp.status == 400


async def test_post_entry_malformed_json_body_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/entry",
        data="not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400


async def test_post_entry_non_object_body_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/lora_library/notebook/entry", json=["nope"])
    assert resp.status == 400


async def test_post_entry_requires_md_suffix_even_for_loopback(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/entry", json={"file": "notes.txt", "name": "Foo", "text": "x"}
    )
    assert resp.status == 403
    assert "error" in await resp.json()


async def test_post_entry_remote_outside_library_dir_is_403(
    context: LibraryContext, tmp_path: Path, aiohttp_client
) -> None:
    outside = tmp_path / "elsewhere.md"
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/entry",
        json={"file": str(outside), "name": "Foo", "text": "x"},
        headers=REMOTE,
    )
    assert resp.status == 403
    assert not outside.exists()


async def test_post_entry_stale_base_mtime_is_409_and_file_untouched(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    created = await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "Foo", "text": "orig"}
    )
    real_mtime = (await created.json())["mtime"]

    resp = await client.post(
        "/lora_library/notebook/entry",
        json={
            "file": "loras.md",
            "name": "Foo",
            "text": "hijacked",
            "base_mtime": real_mtime - 100.0,
        },
    )
    assert resp.status == 409
    body = await resp.json()
    assert "error" in body
    assert body["mtime"] == real_mtime

    fetched = await client.get(
        "/lora_library/notebook/entry", params={"file": "loras.md", "name": "Foo"}
    )
    assert (await fetched.json())["text"] == "orig"


async def test_post_entry_matching_base_mtime_succeeds(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    created = await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "Foo", "text": "orig"}
    )
    real_mtime = (await created.json())["mtime"]
    resp = await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "Foo", "text": "updated", "base_mtime": real_mtime},
    )
    assert resp.status == 200


async def test_post_entry_omitted_base_mtime_skips_conflict_check(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "Foo", "text": "orig"}
    )
    # No base_mtime at all, even though the file already exists — must not 409.
    resp = await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "Foo", "text": "updated"}
    )
    assert resp.status == 200


async def test_post_entry_create_with_after_inserts_below_named_entry(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    for n in ("E1", "E2"):
        await client.post(
            "/lora_library/notebook/entry",
            json={"file": "loras.md", "name": n, "text": n, "category": "Cat A"},
        )
    resp = await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "New", "text": "n", "after": "E1"},
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["entries"] == [
        {"name": "E1", "category": "Cat A"},
        {"name": "New", "category": "Cat A"},
        {"name": "E2", "category": "Cat A"},
    ]


async def test_post_entry_create_with_unknown_after_falls_back_to_append(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "E1", "text": "b1"}
    )
    resp = await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "New", "text": "n", "after": "does-not-exist"},
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["entries"] == [
        {"name": "E1", "category": ""},
        {"name": "New", "category": ""},
    ]


async def test_post_entry_after_is_ignored_on_update(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "E1", "text": "b1"}
    )
    await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "E2", "text": "b2"}
    )
    resp = await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "E1", "text": "updated", "after": "E2"},
    )
    assert resp.status == 200
    body = await resp.json()
    # E1 already existed: `after` never applies to an update — position is
    # unchanged, only the text changed.
    assert body["entries"] == [
        {"name": "E1", "category": ""},
        {"name": "E2", "category": ""},
    ]
    fetched = await client.get(
        "/lora_library/notebook/entry", params={"file": "loras.md", "name": "E1"}
    )
    assert (await fetched.json())["text"] == "updated"


async def test_post_entry_non_string_after_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "Foo", "text": "x", "after": 5},
    )
    assert resp.status == 400


# --------------------------------------------------- POST /notebook/delete


async def test_post_delete_removes_entry(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "Foo", "text": "x"}
    )
    resp = await client.post(
        "/lora_library/notebook/delete", json={"file": "loras.md", "name": "Foo"}
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["ok"] is True
    assert body["entries"] == []

    missing = await client.get(
        "/lora_library/notebook/entry", params={"file": "loras.md", "name": "Foo"}
    )
    assert missing.status == 404


async def test_post_delete_keeps_emptied_category_heading(
    context: LibraryContext, library_dir: Path, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "Only", "text": "body", "category": "Cat A"},
    )
    await client.post("/lora_library/notebook/delete", json={"file": "loras.md", "name": "Only"})
    raw = (library_dir / "loras.md").read_text(encoding="utf-8")
    assert "# Cat A" in raw
    assert "Only" not in raw


async def test_post_delete_missing_entry_is_404(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "Foo", "text": "x"}
    )
    resp = await client.post(
        "/lora_library/notebook/delete", json={"file": "loras.md", "name": "does-not-exist"}
    )
    assert resp.status == 404


async def test_post_delete_missing_file_is_404(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/delete", json={"file": "loras.md", "name": "Foo"}
    )
    assert resp.status == 404


async def test_post_delete_missing_name_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/lora_library/notebook/delete", json={"file": "loras.md"})
    assert resp.status == 400


async def test_post_delete_malformed_json_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/delete",
        data="not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400


async def test_post_delete_stale_base_mtime_is_409_and_file_untouched(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    created = await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "Foo", "text": "x"}
    )
    real_mtime = (await created.json())["mtime"]
    resp = await client.post(
        "/lora_library/notebook/delete",
        json={"file": "loras.md", "name": "Foo", "base_mtime": real_mtime - 100.0},
    )
    assert resp.status == 409
    body = await resp.json()
    assert body["mtime"] == real_mtime

    fetched = await client.get(
        "/lora_library/notebook/entry", params={"file": "loras.md", "name": "Foo"}
    )
    assert fetched.status == 200  # entry survived the refused delete


async def test_post_delete_remote_outside_library_dir_is_403(
    context: LibraryContext, tmp_path: Path, aiohttp_client
) -> None:
    outside = tmp_path / "elsewhere.md"
    outside.write_text("## Foo\nx\n", encoding="utf-8")
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/delete",
        json={"file": str(outside), "name": "Foo"},
        headers=REMOTE,
    )
    assert resp.status == 403
    assert "Foo" in outside.read_text(encoding="utf-8")


# ----------------------------------------------------- POST /notebook/move


async def test_post_move_before_reorders_within_a_category(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    for n in ("E1", "E2", "E3"):
        await client.post(
            "/lora_library/notebook/entry",
            json={"file": "loras.md", "name": n, "text": n, "category": "Cat A"},
        )
    resp = await client.post(
        "/lora_library/notebook/move", json={"file": "loras.md", "name": "E3", "before": "E1"}
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["ok"] is True
    assert body["entries"] == [
        {"name": "E3", "category": "Cat A"},
        {"name": "E1", "category": "Cat A"},
        {"name": "E2", "category": "Cat A"},
    ]


async def test_post_move_before_moves_entry_into_the_siblings_category(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "E1", "text": "b1", "category": "Cat A"},
    )
    await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "E2", "text": "b2", "category": "Cat B"},
    )
    resp = await client.post(
        "/lora_library/notebook/move", json={"file": "loras.md", "name": "E1", "before": "E2"}
    )
    body = await resp.json()
    assert body["entries"] == [
        {"name": "E1", "category": "Cat B"},
        {"name": "E2", "category": "Cat B"},
    ]


async def test_post_move_category_creates_new_category_at_end_of_file(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "E1", "text": "b1"}
    )
    resp = await client.post(
        "/lora_library/notebook/move",
        json={"file": "loras.md", "name": "E1", "category": "Brand New"},
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["entries"] == [{"name": "E1", "category": "Brand New"}]


async def test_post_move_category_empty_string_rule(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "Head", "text": "h"}
    )
    await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "E1", "text": "b1", "category": "Cat A"},
    )
    resp = await client.post(
        "/lora_library/notebook/move", json={"file": "loras.md", "name": "E1", "category": ""}
    )
    body = await resp.json()
    assert body["entries"] == [
        {"name": "Head", "category": ""},
        {"name": "E1", "category": ""},
    ]


async def test_post_move_both_before_and_category_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/move",
        json={"file": "loras.md", "name": "E1", "before": "E2", "category": "Cat A"},
    )
    assert resp.status == 400
    assert "error" in await resp.json()


async def test_post_move_neither_before_nor_category_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/move", json={"file": "loras.md", "name": "E1"}
    )
    assert resp.status == 400


async def test_post_move_unknown_name_is_404(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "E1", "text": "b1"}
    )
    resp = await client.post(
        "/lora_library/notebook/move",
        json={"file": "loras.md", "name": "does-not-exist", "category": ""},
    )
    assert resp.status == 404


async def test_post_move_unknown_before_is_404(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "E1", "text": "b1"}
    )
    resp = await client.post(
        "/lora_library/notebook/move",
        json={"file": "loras.md", "name": "E1", "before": "does-not-exist"},
    )
    assert resp.status == 404


async def test_post_move_missing_file_is_404(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/move", json={"file": "loras.md", "name": "E1", "category": ""}
    )
    assert resp.status == 404


async def test_post_move_missing_name_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/move", json={"file": "loras.md", "category": ""}
    )
    assert resp.status == 400


async def test_post_move_malformed_json_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/move",
        data="not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400


async def test_post_move_non_object_body_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/lora_library/notebook/move", json=["nope"])
    assert resp.status == 400


async def test_post_move_requires_md_suffix_even_for_loopback(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/move",
        json={"file": "notes.txt", "name": "E1", "category": ""},
    )
    assert resp.status == 403


async def test_post_move_remote_outside_library_dir_is_403(
    context: LibraryContext, tmp_path: Path, aiohttp_client
) -> None:
    outside = tmp_path / "elsewhere.md"
    outside.write_text("## E1\nb1\n## E2\nb2\n", encoding="utf-8")
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/move",
        json={"file": str(outside), "name": "E1", "before": "E2"},
        headers=REMOTE,
    )
    assert resp.status == 403
    raw = outside.read_text(encoding="utf-8")
    assert raw.index("E1") < raw.index("E2")  # untouched — original order preserved


async def test_post_move_stale_base_mtime_is_409_and_file_untouched(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "E1", "text": "b1"}
    )
    created = await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "E2", "text": "b2"}
    )
    real_mtime = (await created.json())["mtime"]

    resp = await client.post(
        "/lora_library/notebook/move",
        json={
            "file": "loras.md",
            "name": "E2",
            "before": "E1",
            "base_mtime": real_mtime - 100.0,
        },
    )
    assert resp.status == 409
    body = await resp.json()
    assert body["mtime"] == real_mtime

    listing = await (
        await client.get("/lora_library/notebook", params={"file": "loras.md"})
    ).json()
    assert listing["entries"] == [
        {"name": "E1", "category": ""},
        {"name": "E2", "category": ""},
    ]


async def test_post_move_matching_base_mtime_succeeds(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "E1", "text": "b1"}
    )
    created = await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "E2", "text": "b2"}
    )
    real_mtime = (await created.json())["mtime"]
    resp = await client.post(
        "/lora_library/notebook/move",
        json={"file": "loras.md", "name": "E2", "before": "E1", "base_mtime": real_mtime},
    )
    assert resp.status == 200


# --------------------------------------------- POST /notebook/move_category


async def test_post_move_category_before_another_category(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "E1", "text": "b1", "category": "Cat A"},
    )
    await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "E2", "text": "b2", "category": "Cat B"},
    )
    resp = await client.post(
        "/lora_library/notebook/move_category",
        json={"file": "loras.md", "name": "Cat B", "before": "Cat A"},
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["ok"] is True
    assert body["categories"] == ["Cat B", "Cat A"]
    assert body["entries"] == [
        {"name": "E2", "category": "Cat B"},
        {"name": "E1", "category": "Cat A"},
    ]


async def test_post_move_category_to_end_of_file_when_before_omitted(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "E1", "text": "b1", "category": "Cat A"},
    )
    await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "E2", "text": "b2", "category": "Cat B"},
    )
    resp = await client.post(
        "/lora_library/notebook/move_category", json={"file": "loras.md", "name": "Cat A"}
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["categories"] == ["Cat B", "Cat A"]


async def test_post_move_category_description_and_entries_persist_through_disk(
    context: LibraryContext, library_dir: Path, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post("/lora_library/notebook/category", json={"file": "loras.md", "name": "Cat B"})
    await client.post(
        "/lora_library/notebook/category",
        json={"file": "loras.md", "name": "Cat A", "description": "Prose."},
    )
    await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "E1", "text": "body1", "category": "Cat A"},
    )
    resp = await client.post(
        "/lora_library/notebook/move_category",
        json={"file": "loras.md", "name": "Cat A", "before": "Cat B"},
    )
    assert resp.status == 200
    raw = (library_dir / "loras.md").read_text(encoding="utf-8")
    assert raw == "# Cat A\nProse.\n## E1\nbody1\n# Cat B\n"


async def test_post_move_category_unknown_name_is_404(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post("/lora_library/notebook/category", json={"file": "loras.md", "name": "Cat A"})
    resp = await client.post(
        "/lora_library/notebook/move_category",
        json={"file": "loras.md", "name": "does-not-exist"},
    )
    assert resp.status == 404


async def test_post_move_category_unknown_before_is_404(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post("/lora_library/notebook/category", json={"file": "loras.md", "name": "Cat A"})
    resp = await client.post(
        "/lora_library/notebook/move_category",
        json={"file": "loras.md", "name": "Cat A", "before": "does-not-exist"},
    )
    assert resp.status == 404


async def test_post_move_category_missing_file_is_404(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/move_category", json={"file": "loras.md", "name": "Cat A"}
    )
    assert resp.status == 404


async def test_post_move_category_missing_name_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/lora_library/notebook/move_category", json={"file": "loras.md"})
    assert resp.status == 400


async def test_post_move_category_blank_name_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    # FORMAT.md §3.4: the uncategorized head region ("") is never a movable
    # category — this is the observable-at-the-route-layer half of that
    # rule (blank required fields are uniformly 400 here, same as every
    # sibling route); markdown_store.move_category's own direct rejection
    # is exercised at the store-test layer.
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/move_category", json={"file": "loras.md", "name": "   "}
    )
    assert resp.status == 400


async def test_post_move_category_non_string_before_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post("/lora_library/notebook/category", json={"file": "loras.md", "name": "Cat A"})
    resp = await client.post(
        "/lora_library/notebook/move_category",
        json={"file": "loras.md", "name": "Cat A", "before": 5},
    )
    assert resp.status == 400


async def test_post_move_category_malformed_json_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/move_category",
        data="not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400


async def test_post_move_category_non_object_body_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/lora_library/notebook/move_category", json=["nope"])
    assert resp.status == 400


async def test_post_move_category_requires_md_suffix_even_for_loopback(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/move_category",
        json={"file": "notes.txt", "name": "Cat A"},
    )
    assert resp.status == 403


async def test_post_move_category_remote_outside_library_dir_is_403(
    context: LibraryContext, tmp_path: Path, aiohttp_client
) -> None:
    outside = tmp_path / "elsewhere.md"
    outside.write_text("# Cat A\n## E1\nb1\n# Cat B\n", encoding="utf-8")
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/move_category",
        json={"file": str(outside), "name": "Cat A", "before": "Cat B"},
        headers=REMOTE,
    )
    assert resp.status == 403
    raw = outside.read_text(encoding="utf-8")
    assert raw.index("Cat A") < raw.index("Cat B")  # untouched — original order preserved


async def test_post_move_category_stale_base_mtime_is_409_and_file_untouched(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post("/lora_library/notebook/category", json={"file": "loras.md", "name": "Cat A"})
    created = await client.post(
        "/lora_library/notebook/category", json={"file": "loras.md", "name": "Cat B"}
    )
    real_mtime = (await created.json())["mtime"]

    resp = await client.post(
        "/lora_library/notebook/move_category",
        json={
            "file": "loras.md",
            "name": "Cat B",
            "before": "Cat A",
            "base_mtime": real_mtime - 100.0,
        },
    )
    assert resp.status == 409
    body = await resp.json()
    assert body["mtime"] == real_mtime

    listing = await (
        await client.get("/lora_library/notebook", params={"file": "loras.md"})
    ).json()
    assert listing["categories"] == ["Cat A", "Cat B"]


async def test_post_move_category_matching_base_mtime_succeeds(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post("/lora_library/notebook/category", json={"file": "loras.md", "name": "Cat A"})
    created = await client.post(
        "/lora_library/notebook/category", json={"file": "loras.md", "name": "Cat B"}
    )
    real_mtime = (await created.json())["mtime"]
    resp = await client.post(
        "/lora_library/notebook/move_category",
        json={
            "file": "loras.md",
            "name": "Cat B",
            "before": "Cat A",
            "base_mtime": real_mtime,
        },
    )
    assert resp.status == 200


# ------------------------------------------------------------- integration


async def test_full_lifecycle_create_read_update_rename_delete(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))

    await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "Portrait", "text": "prompt text", "category": "Style"},
    )
    listing = await (await client.get("/lora_library/notebook", params={"file": "loras.md"})).json()
    assert listing["entries"] == [{"name": "Portrait", "category": "Style"}]

    entry = await (
        await client.get(
            "/lora_library/notebook/entry", params={"file": "loras.md", "name": "Portrait"}
        )
    ).json()
    assert entry["text"] == "prompt text"

    renamed = await (
        await client.post(
            "/lora_library/notebook/entry",
            json={
                "file": "loras.md",
                "name": "Portrait",
                "text": "prompt text v2",
                "rename_to": "Portrait v2",
                "base_mtime": entry["mtime"],
            },
        )
    ).json()
    assert renamed["entries"] == [{"name": "Portrait v2", "category": "Style"}]

    deleted = await (
        await client.post(
            "/lora_library/notebook/delete",
            json={"file": "loras.md", "name": "Portrait v2", "base_mtime": renamed["mtime"]},
        )
    ).json()
    assert deleted["entries"] == []


# ----------------------------------------------------- POST /notebook/open_folder


async def test_post_open_folder_success_reveals_resolved_parent(
    context: LibraryContext, library_dir: Path, aiohttp_client, monkeypatch
) -> None:
    calls: list[Path] = []
    monkeypatch.setattr(routes_notebook, "_reveal_folder", calls.append)
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/open_folder", json={"file": "loras.md"}
    )
    assert resp.status == 200
    assert await resp.json() == {"ok": True}
    assert calls == [library_dir]


async def test_post_open_folder_remote_is_403(
    context: LibraryContext, aiohttp_client, monkeypatch
) -> None:
    monkeypatch.setattr(routes_notebook, "_reveal_folder", lambda _p: None)
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/open_folder", json={"file": "loras.md"}, headers=REMOTE
    )
    assert resp.status == 403
    assert "error" in await resp.json()


async def test_post_open_folder_missing_folder_is_404(
    context: LibraryContext, tmp_path: Path, aiohttp_client, monkeypatch
) -> None:
    monkeypatch.setattr(routes_notebook, "_reveal_folder", lambda _p: None)
    client = await aiohttp_client(make_app(context))
    missing = tmp_path / "does-not-exist" / "notes.md"
    resp = await client.post(
        "/lora_library/notebook/open_folder", json={"file": str(missing)}
    )
    assert resp.status == 404
    body = await resp.json()
    assert "does-not-exist" in body["error"]


async def test_post_open_folder_reveal_failure_is_500(
    context: LibraryContext, aiohttp_client, monkeypatch
) -> None:
    def boom(_path: Path) -> None:
        raise RuntimeError("no file manager found")

    monkeypatch.setattr(routes_notebook, "_reveal_folder", boom)
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/open_folder", json={"file": "loras.md"}
    )
    assert resp.status == 500
    assert (await resp.json())["error"] == "no file manager found"


# ---------------------------------------------------------------------------
# FORMAT.md §2 remote allow-list (owner report 2026-07-29)
#
# A notebook on a NAS mount worked from the Linux box running ComfyUI and 403'd
# from his Mac: §1 explicitly blesses absolute NAS paths, §2 confines remote
# callers to `library_dir`, and together those two right rules made the node
# unusable from a second machine. `remote_dirs` is the host-side allow-list
# that reconciles them.
#
# These are security-boundary tests. The negative cases matter more than the
# positive one: the whole point is that a remote caller can neither read
# outside the list NOR extend the list.
# ---------------------------------------------------------------------------


async def test_remote_caller_may_read_inside_a_shared_folder(
    context: LibraryContext, tmp_path: Path, aiohttp_client
) -> None:
    """The owner's case, end to end: the host shares the folder, the remote
    machine can then open the notebook that lives in it."""
    shared = tmp_path / "nas" / "docs"
    shared.mkdir(parents=True)
    book = shared / "loras.md"
    book.write_text("## Portrait\nsoft light\n", encoding="utf-8")
    context.save_config({"remote_dirs": [str(shared)]})

    client = await aiohttp_client(make_app(context))
    resp = await client.get(
        "/lora_library/notebook", params={"file": str(book)}, headers=REMOTE
    )
    assert resp.status == 200
    assert (await resp.json())["entries"] == [{"name": "Portrait", "category": ""}]


async def test_remote_caller_may_write_inside_a_shared_folder(
    context: LibraryContext, tmp_path: Path, aiohttp_client
) -> None:
    shared = tmp_path / "nas" / "docs"
    shared.mkdir(parents=True)
    book = shared / "loras.md"
    book.write_text("## Portrait\nsoft light\n", encoding="utf-8")
    context.save_config({"remote_dirs": [str(shared)]})

    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/entry",
        json={"file": str(book), "name": "Portrait", "text": "harsh light"},
        headers=REMOTE,
    )
    assert resp.status == 200
    assert "harsh light" in book.read_text(encoding="utf-8")


async def test_sharing_one_folder_does_not_share_its_siblings(
    context: LibraryContext, tmp_path: Path, aiohttp_client
) -> None:
    """The list must grant exactly what it names — not its parent, and not a
    sibling that merely shares a name PREFIX (the classic `is_relative_to`
    substring trap: `/nas/docs-private` must not match `/nas/docs`)."""
    shared = tmp_path / "nas" / "docs"
    shared.mkdir(parents=True)
    context.save_config({"remote_dirs": [str(shared)]})

    sibling = tmp_path / "nas" / "docs-private"
    sibling.mkdir()
    secret = sibling / "secret.md"
    secret.write_text("## Secret\nnope\n", encoding="utf-8")
    parent_secret = tmp_path / "nas" / "parent.md"
    parent_secret.write_text("## Secret\nnope\n", encoding="utf-8")

    client = await aiohttp_client(make_app(context))
    for target in (secret, parent_secret):
        resp = await client.get(
            "/lora_library/notebook", params={"file": str(target)}, headers=REMOTE
        )
        assert resp.status == 403, f"{target} must not be reachable"


async def test_shared_folder_does_not_let_traversal_escape_it(
    context: LibraryContext, tmp_path: Path, aiohttp_client
) -> None:
    shared = tmp_path / "nas" / "docs"
    shared.mkdir(parents=True)
    context.save_config({"remote_dirs": [str(shared)]})
    outside = tmp_path / "outside.md"
    outside.write_text("## Secret\nnope\n", encoding="utf-8")

    client = await aiohttp_client(make_app(context))
    resp = await client.get(
        "/lora_library/notebook",
        params={"file": str(shared / ".." / ".." / "outside.md")},
        headers=REMOTE,
    )
    assert resp.status == 403


async def test_a_symlink_out_of_a_shared_folder_is_still_refused(
    context: LibraryContext, tmp_path: Path, aiohttp_client
) -> None:
    """Both sides are fully resolved, so a symlink planted inside a shared
    folder resolves to its real target and fails the check. This is also why
    "symlink the NAS file into the library folder" is not a workaround."""
    shared = tmp_path / "nas" / "docs"
    shared.mkdir(parents=True)
    context.save_config({"remote_dirs": [str(shared)]})
    secret = tmp_path / "secret.md"
    secret.write_text("## Secret\nnope\n", encoding="utf-8")
    link = shared / "innocent.md"
    link.symlink_to(secret)

    client = await aiohttp_client(make_app(context))
    resp = await client.get(
        "/lora_library/notebook", params={"file": str(link)}, headers=REMOTE
    )
    assert resp.status == 403


async def test_remote_refusal_names_the_folder_to_share(
    context: LibraryContext, tmp_path: Path, aiohttp_client
) -> None:
    """The old message stopped at "not allowed", which is a dead end for the
    one case that legitimately hits it. It must name the folder and say where
    to go — the owner pasted this exact error with nothing to act on."""
    outside = tmp_path / "nas" / "docs" / "loras.md"
    outside.parent.mkdir(parents=True)
    outside.write_text("## X\ny\n", encoding="utf-8")

    client = await aiohttp_client(make_app(context))
    resp = await client.get(
        "/lora_library/notebook", params={"file": str(outside)}, headers=REMOTE
    )
    assert resp.status == 403
    error = (await resp.json())["error"]
    assert str(outside.parent) in error, "must name the folder to share"
    assert "Share with remote browsers" in error, "must name the control"
    assert "machine running it" in error, "must say WHERE to do it"


async def test_remote_caller_cannot_extend_the_allow_list(
    context: LibraryContext, tmp_path: Path, aiohttp_client
) -> None:
    """The one that would undo the whole boundary: if a remote caller could
    add a folder, it could grant itself the arbitrary-file read §2 denies."""
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/remote_dirs", json={"dir": str(tmp_path), "allow": True}, headers=REMOTE
    )
    assert resp.status == 403
    assert context.remote_dirs() == []


async def test_local_caller_can_add_and_remove_a_shared_folder(
    context: LibraryContext, tmp_path: Path, aiohttp_client
) -> None:
    shared = tmp_path / "nas" / "docs"
    shared.mkdir(parents=True)
    client = await aiohttp_client(make_app(context))

    resp = await client.post("/lora_library/remote_dirs", json={"dir": str(shared)})
    assert resp.status == 200
    assert (await resp.json())["remote_dirs"] == [str(shared)]
    assert context.remote_dirs() == [shared]

    # Idempotent: adding the same folder (or one already covered) is a no-op.
    await client.post("/lora_library/remote_dirs", json={"dir": str(shared)})
    await client.post("/lora_library/remote_dirs", json={"dir": str(shared / "nested")})
    assert context.remote_dirs() == [shared]

    resp = await client.post(
        "/lora_library/remote_dirs", json={"dir": str(shared), "allow": False}
    )
    assert resp.status == 200
    assert (await resp.json())["remote_dirs"] == []
    assert context.remote_dirs() == []


async def test_remote_dirs_route_rejects_junk(
    context: LibraryContext, tmp_path: Path, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    for body, why in [
        ({}, "missing dir"),
        ({"dir": "   "}, "blank dir"),
        ({"dir": "relative/path"}, "not absolute"),
        ({"dir": "smb://host/share"}, "network address, not a path"),
        ({"dir": str(tmp_path), "allow": "yes"}, "allow must be a bool"),
    ]:
        resp = await client.post("/lora_library/remote_dirs", json=body)
        assert resp.status == 400, f"{why} should be a 400"


async def test_config_reports_the_shared_folders(
    context: LibraryContext, tmp_path: Path, aiohttp_client
) -> None:
    shared = tmp_path / "nas" / "docs"
    shared.mkdir(parents=True)
    context.save_config({"remote_dirs": [str(shared)]})
    client = await aiohttp_client(make_app(context))
    resp = await client.get("/lora_library/config")
    assert (await resp.json())["remote_dirs"] == [str(shared)]


async def test_unmounted_shared_folder_does_not_break_the_check(
    context: LibraryContext, tmp_path: Path, aiohttp_client
) -> None:
    """A stale entry for a NAS that isn't mounted must not match, and must not
    take the guard down either — and it must never be CREATED by being listed
    (a conjured empty dir would shadow the real mount point)."""
    missing = tmp_path / "not-mounted" / "docs"
    context.save_config({"remote_dirs": [str(missing)]})
    assert not missing.exists()

    inside_library = context.library_dir() / "fine.md"
    inside_library.write_text("## Ok\nyes\n", encoding="utf-8")
    client = await aiohttp_client(make_app(context))
    resp = await client.get(
        "/lora_library/notebook", params={"file": str(inside_library)}, headers=REMOTE
    )
    assert resp.status == 200, "a stale entry must not break unrelated requests"
    assert not missing.exists(), "listing a folder must never create it"


async def test_library_dir_remains_allowed_without_any_shared_folders(
    context: LibraryContext, aiohttp_client
) -> None:
    """The pre-existing §2 rule is untouched: library_dir works for a remote
    caller with an empty allow-list."""
    assert context.remote_dirs() == []
    book = context.library_dir() / "loras.md"
    book.write_text("## Ok\nyes\n", encoding="utf-8")
    client = await aiohttp_client(make_app(context))
    resp = await client.get(
        "/lora_library/notebook", params={"file": str(book)}, headers=REMOTE
    )
    assert resp.status == 200


async def test_unreachable_library_dir_degrades_to_403_not_500(
    context: LibraryContext, tmp_path: Path, aiohttp_client
) -> None:
    """v0.42.0 regression, live-verified before fixing: `notebook_path_error`
    called `context.library_dir()` unguarded, and its on-demand mkdir THROWS
    when the configured library folder is unreachable (unmounted NAS) -- so
    every non-loopback request 500'd, including files inside a validly shared
    folder, while loopback callers (who skip the guard) kept working. An
    unreachable library must contribute no root: shared folders still work,
    and a miss is the clean 403."""
    blocker = tmp_path / "blocker"
    blocker.write_text("a FILE where the library's parent should be", encoding="utf-8")
    shared = tmp_path / "nas" / "docs"
    shared.mkdir(parents=True)
    book = shared / "loras.md"
    book.write_text("## Ok\nyes\n", encoding="utf-8")
    context.save_config(
        {"library_dir": str(blocker / "lib"), "remote_dirs": [str(shared)]}
    )

    client = await aiohttp_client(make_app(context))
    resp = await client.get(
        "/lora_library/notebook", params={"file": str(book)}, headers=REMOTE
    )
    assert resp.status == 200, await resp.text()
    outside = tmp_path / "outside.md"
    outside.write_text("## Secret\nnope\n", encoding="utf-8")
    resp = await client.get(
        "/lora_library/notebook", params={"file": str(outside)}, headers=REMOTE
    )
    assert resp.status == 403


def test_machine_owns_address_bind_test() -> None:
    """The cpsb-ported locality test (2026-07-30): a browser on the HOST that
    reaches ComfyUI via the machine's own LAN address must classify as LOCAL
    (it used to read as remote, which made the loopback-only share toggle
    unreachable from every browser -- a catch-22), while a genuinely foreign
    address stays remote."""
    import socket as socket_mod

    from lora_library.routes import _machine_owns_address

    probe = socket_mod.socket(socket_mod.AF_INET, socket_mod.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))  # UDP connect sends nothing
        own_ip = probe.getsockname()[0]
    finally:
        probe.close()
    assert _machine_owns_address(own_ip) is True
    assert _machine_owns_address("::ffff:127.0.0.1") is True
    assert _machine_owns_address("192.0.2.1") is False  # TEST-NET-1: never ours


async def test_write_routes_refuse_empty_or_missing_file(
    context: LibraryContext, aiohttp_client
) -> None:
    """v0.52.1 (owner's 'reset to defaults' report, 2026-08-03): an
    empty/absent 'file' used to resolve to the DEFAULT notebook, silently
    landing writes from a broken panel in the wrong file. Writes now 400;
    READS keep the default-resolution behavior (fresh nodes list it)."""
    client = await aiohttp_client(make_app(context))
    for route, body in [
        ("/lora_library/notebook/entry", {"name": "X", "text": "t"}),
        ("/lora_library/notebook/delete", {"name": "X"}),
        ("/lora_library/notebook/category", {"name": "C"}),
        ("/lora_library/notebook/move", {"name": "X", "category": ""}),
    ]:
        for file_value in (None, "", "   "):
            payload = dict(body)
            if file_value is not None:
                payload["file"] = file_value
            resp = await client.post(route, json=payload)
            assert resp.status == 400, (route, file_value, resp.status)
            assert "required for writes" in (await resp.json())["error"]

    # The read route still resolves "" to the default notebook (fresh node).
    resp = await client.get("/lora_library/notebook", params={"file": ""})
    assert resp.status == 200


async def test_get_notebook_include_text_adds_bodies_opt_in(
    context: LibraryContext, aiohttp_client
) -> None:
    """v0.53.0 (the notebook search field): `include_text=1` adds each
    entry's body text to its list row; without it the payload keeps its
    original shape byte-for-byte."""
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "Alpha", "text": "cinematic light"},
    )
    await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "Beta", "text": "studio portrait"},
    )

    plain = await (await client.get("/lora_library/notebook", params={"file": "loras.md"})).json()
    assert all("text" not in e for e in plain["entries"])

    rich = await (
        await client.get(
            "/lora_library/notebook", params={"file": "loras.md", "include_text": "1"}
        )
    ).json()
    by_name = {e["name"]: e for e in rich["entries"]}
    assert by_name["Alpha"]["text"] == "cinematic light"
    assert by_name["Beta"]["text"] == "studio portrait"
