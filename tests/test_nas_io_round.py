"""NAS round 2026-08-22 (owner: the library folder is on a NAS; the Prompt
Notebook "sometimes looks broken but just takes over a minute to load,
even when just tabbing between open workflows", usually from a remote
browser) -- the backend half:

1. every library-folder read/write a route handler performs runs OFF the
   event loop (``asyncio.to_thread``), with the handlers' payloads and
   error mapping unchanged;
2. ``GET /lora_library/notebook?known_mtime=`` short-circuits to a one-stat
   ``unchanged`` answer (FORMAT.md §5);
3. ``LibraryContext.library_dir()/sets_dir()`` memoize their ``mkdir`` for
   ``ENSURED_DIR_TTL_S``;
4. ``sets_store.list_sets`` caches on the directory's mtime_ns with a
   per-file (mtime_ns, size) second layer; ``load_layout`` caches the
   layout file by its mtime and rides the cached listing;
5. ``POST /lora_library/sets/open_folder`` + the new ``sets_dir``/
   ``is_default_library`` fields on ``GET /lora_library/sets``.

Uses the shared ``context``/``library_dir`` fixtures (conftest.py) and
``pytest-aiohttp``'s ``aiohttp_client``.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest
from aiohttp import web

from eps_image import resolution_presets_store
from eps_image.routes_resolution_presets import build_routes as build_presets_routes
from lora_library import context as context_module
from lora_library import (
    lora_picker_store,
    markdown_store,
    routes_lora_picker,
    routes_sets,
    sets_store,
)
from lora_library import routes as lora_routes
from lora_library.context import ENSURED_DIR_TTL_S, LibraryContext, _atomic_write_text
from lora_library.routes import build_routes

REMOTE = {"X-Forwarded-For": "203.0.113.5"}


def make_app(context: LibraryContext) -> web.Application:
    app = web.Application()
    app.add_routes(build_routes(context))
    return app


def make_presets_app(context: LibraryContext) -> web.Application:
    app = web.Application()
    app.add_routes(build_presets_routes(context))
    return app


@pytest.fixture(autouse=True)
def _fresh_store_caches():
    sets_store.clear_caches()
    routes_lora_picker._previews_cache_clear()
    yield
    sets_store.clear_caches()
    routes_lora_picker._previews_cache_clear()


class _ThreadRecorder:
    """Wraps a store function: records which thread ran it, delegates."""

    def __init__(self, original) -> None:
        self.original = original
        self.threads: list[int] = []

    def __call__(self, *args, **kwargs):
        self.threads.append(threading.get_ident())
        return self.original(*args, **kwargs)


# =========================================================== 1. off the loop


async def test_notebook_get_and_post_run_their_store_calls_off_the_loop_thread(
    context: LibraryContext, aiohttp_client, monkeypatch
) -> None:
    loop_thread = threading.get_ident()
    load = _ThreadRecorder(markdown_store.load_notebook)
    save = _ThreadRecorder(markdown_store.save_notebook)
    monkeypatch.setattr(markdown_store, "load_notebook", load)
    monkeypatch.setattr(markdown_store, "save_notebook", save)
    client = await aiohttp_client(make_app(context))

    resp = await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "Alpha", "text": "a prompt"},
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["ok"] is True
    assert body["entries"] == [{"name": "Alpha", "category": ""}]

    resp = await client.get("/lora_library/notebook", params={"file": "loras.md"})
    assert resp.status == 200
    body = await resp.json()
    assert body["exists"] is True
    assert body["entries"] == [{"name": "Alpha", "category": ""}]
    assert body["categories"] == []
    assert body["problems"] == []

    assert load.threads and all(t != loop_thread for t in load.threads)
    assert save.threads and all(t != loop_thread for t in save.threads)


async def test_notebook_remote_guard_still_403s_before_any_read(
    context: LibraryContext, tmp_path: Path, aiohttp_client, monkeypatch
) -> None:
    load = _ThreadRecorder(markdown_store.load_notebook)
    monkeypatch.setattr(markdown_store, "load_notebook", load)
    client = await aiohttp_client(make_app(context))
    outside = tmp_path / "elsewhere" / "notes.md"
    resp = await client.get(
        "/lora_library/notebook",
        params={"file": str(outside), "known_mtime": "1.0"},
        headers=REMOTE,
    )
    assert resp.status == 403
    assert "FORMAT.md §2" in (await resp.json())["error"]
    assert load.threads == []


async def test_notebook_store_errors_map_exactly_as_before(
    context: LibraryContext, library_dir: Path, aiohttp_client
) -> None:
    # A directory where the file should be -> MarkdownStoreError -> 400.
    (library_dir / "dir.md").mkdir()
    client = await aiohttp_client(make_app(context))
    resp = await client.get("/lora_library/notebook", params={"file": "dir.md"})
    assert resp.status == 400
    assert "could not read dir.md" in (await resp.json())["error"]
    # §3.5 conflict -> 409 with the current mtime.
    await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "A", "text": "x"}
    )
    resp = await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "B", "text": "y", "base_mtime": 1.0},
    )
    assert resp.status == 409
    assert "mtime" in await resp.json()


async def test_sets_routes_run_their_store_calls_off_the_loop_thread(
    context: LibraryContext, aiohttp_client, monkeypatch
) -> None:
    loop_thread = threading.get_ident()
    recorders = {
        name: _ThreadRecorder(getattr(sets_store, name))
        for name in (
            "list_sets",
            "load_set",
            "save_set",
            "delete_set",
            "load_layout",
            "save_layout",
        )
    }
    for name, recorder in recorders.items():
        monkeypatch.setattr(sets_store, name, recorder)
    client = await aiohttp_client(make_app(context))

    resp = await client.post("/lora_library/set", json={"set": {"name": "Alpha", "loras": []}})
    assert resp.status == 200
    body = await resp.json()
    assert body["ok"] is True and body["slug"] == "alpha"
    assert body["sets"] == [{"slug": "alpha", "name": "Alpha", "count": 0}]

    resp = await client.get("/lora_library/sets")
    assert (await resp.json())["sets"] == [{"slug": "alpha", "name": "Alpha", "count": 0}]

    resp = await client.get("/lora_library/set", params={"slug": "alpha"})
    assert (await resp.json())["name"] == "Alpha"

    resp = await client.get("/lora_library/sets_layout")
    assert (await resp.json())["layout"]["order"][""] == ["alpha"]

    resp = await client.post("/lora_library/sets_layout", json={"layout": {"categories": ["X"]}})
    assert (await resp.json())["layout"]["categories"] == ["X"]

    resp = await client.post("/lora_library/set/delete", json={"slug": "alpha"})
    assert (await resp.json()) == {"ok": True, "sets": []}

    for name, recorder in recorders.items():
        assert recorder.threads, name
        assert all(t != loop_thread for t in recorder.threads), name


async def test_sets_store_errors_map_exactly_as_before(
    context: LibraryContext, tmp_path: Path, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/lora_library/set", json={"set": {"name": "X", "loras": "nope"}})
    assert resp.status == 400
    assert "FORMAT.md §4" in (await resp.json())["error"]
    resp = await client.get("/lora_library/set", params={"slug": "missing"})
    assert resp.status == 404
    resp = await client.post("/lora_library/set/delete", json={"slug": "missing"})
    assert resp.status == 404
    # unreachable library: 400 naming the folder on the write routes,
    # 200-empty on the listing (audit 2026-08-08 posture, unchanged)
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    context.save_config({"library_dir": str(blocker / "library")})
    resp = await client.post("/lora_library/set", json={"set": {"name": "X", "loras": []}})
    assert resp.status == 400
    assert "unreachable" in (await resp.json())["error"]
    resp = await client.get("/lora_library/sets")
    assert resp.status == 200
    assert (await resp.json())["sets"] == []


async def test_picker_routes_run_their_store_calls_off_the_loop_thread(
    context: LibraryContext, aiohttp_client, monkeypatch
) -> None:
    loop_thread = threading.get_ident()
    recorders = {
        name: _ThreadRecorder(getattr(lora_picker_store, name))
        for name in (
            "load_state",
            "toggle_favorite",
            "record_recents",
            "clear_recents",
            "reorder_favorites",
        )
    }
    for name, recorder in recorders.items():
        monkeypatch.setattr(lora_picker_store, name, recorder)
    client = await aiohttp_client(make_app(context))

    resp = await client.post(
        "/lora_library/picker/favorite", json={"file": "detailer.safetensors", "on": True}
    )
    assert (await resp.json())["favorites"] == ["detailer.safetensors"]
    resp = await client.post(
        "/lora_library/picker/recent", json={"files": ["detailer.safetensors"]}
    )
    assert [r["file"] for r in (await resp.json())["recents"]] == ["detailer.safetensors"]
    resp = await client.post(
        "/lora_library/picker/favorites_order", json={"files": ["detailer.safetensors"]}
    )
    assert (await resp.json())["favorites"] == ["detailer.safetensors"]
    resp = await client.post("/lora_library/picker/clear_recents", json={})
    assert (await resp.json())["recents"] == []
    resp = await client.get("/lora_library/picker")
    body = await resp.json()
    assert body["favorites"] == ["detailer.safetensors"]
    assert body["recents"] == []
    assert body["loras"] == context.list_loras()

    for name, recorder in recorders.items():
        assert recorder.threads, name
        assert all(t != loop_thread for t in recorder.threads), name

    # error mapping unchanged: a structurally bad body is the route's 400
    resp = await client.post("/lora_library/picker/favorite", json={"file": "", "on": True})
    assert resp.status == 400


async def test_presets_routes_run_their_store_calls_off_the_loop_thread(
    context: LibraryContext, aiohttp_client, monkeypatch
) -> None:
    loop_thread = threading.get_ident()
    recorders = {
        name: _ThreadRecorder(getattr(resolution_presets_store, name))
        for name in ("load_presets", "save_preset", "delete_preset")
    }
    for name, recorder in recorders.items():
        monkeypatch.setattr(resolution_presets_store, name, recorder)
    client = await aiohttp_client(make_presets_app(context))
    values = {
        "width": 1024,
        "height": 768,
        "resize_method": "crop to fill",
        "interpolation": "lanczos",
        "multiple_of": 8,
    }
    resp = await client.post(
        "/eps_resolution/presets/save", json={"name": "Wide", "values": values}
    )
    assert resp.status == 200, await resp.text()
    body = await resp.json()
    assert body["presets"] == {"Wide": values}
    resp = await client.get("/eps_resolution/presets")
    assert (await resp.json())["presets"] == {"Wide": values}
    resp = await client.post("/eps_resolution/presets/delete", json={"name": "Wide"})
    assert (await resp.json())["presets"] == {}
    # error mapping unchanged: unknown name -> 404, stale base_mtime -> 409
    resp = await client.post("/eps_resolution/presets/delete", json={"name": "Wide"})
    assert resp.status == 404
    resp = await client.post(
        "/eps_resolution/presets/save", json={"name": "W2", "values": values, "base_mtime": 1.0}
    )
    assert resp.status == 409

    for name, recorder in recorders.items():
        assert recorder.threads, name
        assert all(t != loop_thread for t in recorder.threads), name


async def test_config_and_fs_list_run_their_disk_work_off_the_loop_thread(
    context: LibraryContext, library_dir: Path, aiohttp_client, monkeypatch
) -> None:
    loop_thread = threading.get_ident()
    check = _ThreadRecorder(lora_routes._check_library_dir)
    scan = _ThreadRecorder(lora_routes._scan_directory)
    monkeypatch.setattr(lora_routes, "_check_library_dir", check)
    monkeypatch.setattr(lora_routes, "_scan_directory", scan)
    (library_dir / "notes.md").write_text("# x\n", encoding="utf-8")
    client = await aiohttp_client(make_app(context))

    resp = await client.get("/lora_library/config")
    body = await resp.json()
    assert body["library_dir"] == str(library_dir)
    assert body["is_local"] is True
    assert body["library_dir_exists"] is True
    assert body["library_dir_note"] == ""
    assert body["configured"] is False
    assert body["remote_dirs"] == []
    body = await (await client.get("/lora_library/config", headers=REMOTE)).json()
    assert body["is_local"] is False

    resp = await client.get("/lora_library/fs/list")
    body = await resp.json()
    assert body["dir"] == str(library_dir)
    assert [f["name"] for f in body["files"]] == ["notes.md"]

    assert check.threads and all(t != loop_thread for t in check.threads)
    assert scan.threads and all(t != loop_thread for t in scan.threads)


async def test_post_config_probe_and_remote_dirs_keep_their_semantics(
    context: LibraryContext, tmp_path: Path, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    target = tmp_path / "new-library"
    resp = await client.post("/lora_library/config", json={"library_dir": str(target)})
    assert resp.status == 200
    assert (await resp.json()) == {"ok": True, "library_dir": str(target)}
    assert target.is_dir() and not (target / ".lora_library_write_probe").exists()
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    resp = await client.post("/lora_library/config", json={"library_dir": str(blocker / "lib")})
    assert resp.status == 400
    assert "not writable" in (await resp.json())["error"]
    resp = await client.post("/lora_library/config", json={"library_dir": ""})
    assert (await resp.json()) == {"ok": True, "library_dir": str(context.default_library_dir)}

    shared = tmp_path / "shared"
    shared.mkdir()
    resp = await client.post("/lora_library/remote_dirs", json={"dir": str(shared)})
    assert (await resp.json()) == {"ok": True, "remote_dirs": [str(shared)]}
    resp = await client.post(
        "/lora_library/remote_dirs", json={"dir": str(shared / "."), "allow": True}
    )
    assert (await resp.json())["remote_dirs"] == [str(shared)]  # resolved containment: no dupe
    resp = await client.post("/lora_library/remote_dirs", json={"dir": str(shared), "allow": False})
    assert (await resp.json()) == {"ok": True, "remote_dirs": []}


# ============================================= 2. notebook known_mtime short-circuit


def _write_notebook(
    library_dir: Path, text: str = "## Alpha\nfirst\n\n# Cat\n## Beta\nsecond\n"
) -> Path:
    path = library_dir / "loras.md"
    path.write_text(text, encoding="utf-8")
    return path


async def test_known_mtime_match_returns_the_unchanged_payload_and_nothing_else(
    context: LibraryContext, library_dir: Path, aiohttp_client, monkeypatch
) -> None:
    path = _write_notebook(library_dir)
    client = await aiohttp_client(make_app(context))
    full = await (
        await client.get("/lora_library/notebook", params={"file": "loras.md", "include_text": "1"})
    ).json()
    assert full["entries"][0]["text"] == "first"
    assert "unchanged" not in full

    load = _ThreadRecorder(markdown_store.load_notebook)
    monkeypatch.setattr(markdown_store, "load_notebook", load)
    resp = await client.get(
        "/lora_library/notebook",
        params={"file": "loras.md", "include_text": "1", "known_mtime": repr(full["mtime"])},
    )
    assert resp.status == 200
    assert await resp.json() == {
        "ok": True,
        "unchanged": True,
        "mtime": path.stat().st_mtime,
        "exists": True,
        "file": full["file"],
    }
    assert load.threads == []  # one stat, no read/parse


async def test_known_mtime_mismatch_missing_or_malformed_returns_the_full_payload(
    context: LibraryContext, library_dir: Path, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    # missing file + hint -> the full (exists: false) payload, not an error
    body = await (
        await client.get(
            "/lora_library/notebook", params={"file": "loras.md", "known_mtime": "123.5"}
        )
    ).json()
    assert body["exists"] is False and body["entries"] == [] and "unchanged" not in body

    _write_notebook(library_dir)
    full = await (await client.get("/lora_library/notebook", params={"file": "loras.md"})).json()
    expected_keys = {"file", "exists", "mtime", "entries", "categories", "problems"}
    assert set(full) == expected_keys

    stale = await (
        await client.get(
            "/lora_library/notebook",
            params={"file": "loras.md", "known_mtime": repr(full["mtime"] - 1.0)},
        )
    ).json()
    assert stale == full

    junk = await (
        await client.get(
            "/lora_library/notebook", params={"file": "loras.md", "known_mtime": "not-a-number"}
        )
    ).json()
    assert junk == full
    blank = await (
        await client.get("/lora_library/notebook", params={"file": "loras.md", "known_mtime": ""})
    ).json()
    assert blank == full


async def test_known_mtime_sees_an_external_edit(
    context: LibraryContext, library_dir: Path, aiohttp_client
) -> None:
    path = _write_notebook(library_dir)
    client = await aiohttp_client(make_app(context))
    full = await (await client.get("/lora_library/notebook", params={"file": "loras.md"})).json()
    # another machine edits the file: bump the mtime explicitly so this
    # can't depend on filesystem timestamp granularity
    path.write_text("## Gamma\nthird\n", encoding="utf-8")
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000_000))
    body = await (
        await client.get(
            "/lora_library/notebook",
            params={"file": "loras.md", "known_mtime": repr(full["mtime"])},
        )
    ).json()
    assert "unchanged" not in body
    assert [e["name"] for e in body["entries"]] == ["Gamma"]


# ================================================ 3. ensured-directory memo


class _MkdirCounter:
    def __init__(self, monkeypatch) -> None:
        self.calls: list[Path] = []
        original = Path.mkdir

        def counting(path_self, *args, **kwargs):
            self.calls.append(Path(path_self))
            return original(path_self, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", counting)


def test_library_dir_and_sets_dir_mkdir_once_per_ttl(context: LibraryContext, monkeypatch) -> None:
    # parents pre-created: pathlib's mkdir(parents=True) re-enters itself
    # for a missing parent, which would double-count the very first call
    context.default_library_dir.parent.mkdir(parents=True, exist_ok=True)
    counter = _MkdirCounter(monkeypatch)
    first = context.library_dir()
    assert context.library_dir() == first
    assert context.library_dir() == first
    assert counter.calls.count(first) == 1
    sets_dir = context.sets_dir()
    context.sets_dir()
    assert sets_dir == first / "sets"
    assert counter.calls.count(sets_dir) == 1
    assert counter.calls.count(first) == 1  # sets_dir() rode the memo too

    # the memo expires after ENSURED_DIR_TTL_S
    now = time.monotonic()
    monkeypatch.setattr(context_module.time, "monotonic", lambda: now + ENSURED_DIR_TTL_S + 1)
    assert context.library_dir() == first
    assert counter.calls.count(first) == 2


def test_save_config_and_forget_ensured_dirs_invalidate_the_memo(
    context: LibraryContext, monkeypatch
) -> None:
    context.default_library_dir.parent.mkdir(parents=True, exist_ok=True)
    counter = _MkdirCounter(monkeypatch)
    first = context.library_dir()
    context.forget_ensured_dirs()
    context.library_dir()
    assert counter.calls.count(first) == 2
    context.save_config({})  # same library, but the config changed -> verify again
    context.library_dir()
    assert counter.calls.count(first) == 3


def test_a_failed_mkdir_is_never_memoized(
    context: LibraryContext, tmp_path: Path, monkeypatch
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    configured = blocker / "library"
    context.save_config({"library_dir": str(configured)})
    counter = _MkdirCounter(monkeypatch)
    with pytest.raises(OSError):
        context.library_dir()
    with pytest.raises(OSError):
        context.library_dir()
    assert counter.calls.count(configured) == 2
    # the pure helpers still answer while the folder is unreachable
    assert context.configured_library_dir() == configured
    assert context.is_default_library() is False


def test_configured_library_dir_is_pure_and_is_default_library_compares_paths(
    context: LibraryContext, tmp_path: Path, monkeypatch
) -> None:
    context.user_dir.mkdir(parents=True, exist_ok=True)  # save_config's own mkdir target
    counter = _MkdirCounter(monkeypatch)
    assert context.configured_library_dir() == context.default_library_dir
    assert context.is_default_library() is True
    assert counter.calls == []
    context.save_config({"library_dir": str(tmp_path / "elsewhere")})
    assert context.configured_library_dir() == tmp_path / "elsewhere"
    assert context.is_default_library() is False
    context.save_config({"library_dir": str(context.default_library_dir)})
    assert context.is_default_library() is True
    assert [c for c in counter.calls if c != context.user_dir] == []


# ================================================== 4. sets listing caches


class _ParseCounter:
    """Counts real set-file parses by wrapping ``sets_store.load_set``
    (which ``_scan_sets`` calls through the module global)."""

    def __init__(self, monkeypatch) -> None:
        self.slugs: list[str] = []
        original = sets_store.load_set

        def counting(context, slug):
            self.slugs.append(slug)
            return original(context, slug)

        monkeypatch.setattr(sets_store, "load_set", counting)


def _three_sets(context: LibraryContext) -> None:
    for name in ("Zebra", "Apple", "Mango"):
        sets_store.save_set(context, {"name": name, "loras": [{"file": "a.safetensors"}]})


def test_list_sets_parses_once_then_answers_from_the_cache(
    context: LibraryContext, monkeypatch
) -> None:
    _three_sets(context)
    counter = _ParseCounter(monkeypatch)
    first = sets_store.list_sets(context)
    assert [e["name"] for e in first] == ["Apple", "Mango", "Zebra"]
    assert sorted(counter.slugs) == ["apple", "mango", "zebra"]
    second = sets_store.list_sets(context)
    assert second == first
    assert len(counter.slugs) == 3  # no new parse
    # the result is a copy: mutating it can't poison the cache
    second[0]["name"] = "MUTATED"
    assert sets_store.list_sets(context)[0]["name"] == "Apple"


def test_save_and_delete_in_this_process_invalidate_and_reparse_only_the_changed_file(
    context: LibraryContext, monkeypatch
) -> None:
    _three_sets(context)
    counter = _ParseCounter(monkeypatch)
    sets_store.list_sets(context)
    counter.slugs.clear()
    sets_store.save_set(context, {"name": "Kiwi", "loras": []})
    listed = sets_store.list_sets(context)
    assert [e["slug"] for e in listed] == ["apple", "kiwi", "mango", "zebra"]
    assert counter.slugs == ["kiwi"]  # per-file layer: the three others were not re-read
    counter.slugs.clear()
    sets_store.save_set(context, {"name": "Apple renamed", "loras": []}, slug="apple")
    listed = sets_store.list_sets(context)
    assert next(e["name"] for e in listed if e["slug"] == "apple") == "Apple renamed"
    assert counter.slugs == ["apple"]
    counter.slugs.clear()
    assert sets_store.delete_set(context, "zebra") is True
    assert [e["slug"] for e in sets_store.list_sets(context)] == ["apple", "kiwi", "mango"]
    assert counter.slugs == []


def test_temp_plus_replace_by_another_writer_changes_the_dir_mtime_and_is_seen_at_once(
    context: LibraryContext, monkeypatch
) -> None:
    """The claim the LISTING layer rests on, verified on this filesystem:
    an atomic temp+replace save (what every pack build does, from any
    machine) changes the sets DIRECTORY's mtime, so the one stat the fast
    path costs notices another process's save without any TTL."""
    _three_sets(context)
    sets_dir = context.sets_dir()
    counter = _ParseCounter(monkeypatch)
    sets_store.list_sets(context)
    before = sets_dir.stat().st_mtime_ns
    time.sleep(0.02)  # coarse-timestamp filesystems (ext4 jiffies) need daylight
    payload = json.dumps({"format": 1, "name": "Foreign", "loras": []})
    _atomic_write_text(sets_dir / "foreign.json", payload)  # NOT through save_set
    assert sets_dir.stat().st_mtime_ns != before
    counter.slugs.clear()
    listed = sets_store.list_sets(context)
    assert "foreign" in [e["slug"] for e in listed]
    assert counter.slugs == ["foreign"]


def test_in_place_edit_leaves_the_dir_mtime_alone_and_is_caught_by_the_rescan_layer(
    context: LibraryContext, monkeypatch
) -> None:
    """The SMB/NFS caveat, pinned: an in-place rewrite (an editor that
    truncates + writes, no rename) does NOT change the directory's mtime,
    so the listing layer alone would never notice it. The per-file
    (mtime_ns, size) layer catches it on the next rescan, which happens
    at most LISTING_RESCAN_S after the cached scan."""
    _three_sets(context)
    sets_dir = context.sets_dir()
    counter = _ParseCounter(monkeypatch)
    sets_store.list_sets(context)
    before = sets_dir.stat().st_mtime_ns
    time.sleep(0.02)
    target = sets_dir / "apple.json"
    with open(target, "w", encoding="utf-8") as fh:  # in place, no rename
        fh.write(json.dumps({"format": 1, "name": "Apple edited elsewhere", "loras": []}))
    assert sets_dir.stat().st_mtime_ns == before
    counter.slugs.clear()
    stale = sets_store.list_sets(context)
    assert next(e["name"] for e in stale if e["slug"] == "apple") == "Apple"  # stale, by design
    assert counter.slugs == []
    now = time.monotonic()
    monkeypatch.setattr(sets_store.time, "monotonic", lambda: now + sets_store.LISTING_RESCAN_S + 1)
    fresh = sets_store.list_sets(context)
    assert next(e["name"] for e in fresh if e["slug"] == "apple") == "Apple edited elsewhere"
    assert counter.slugs == ["apple"]  # only the changed file was re-read


def test_list_sets_still_skips_bad_files_and_invalid_slugs_under_the_cache(
    context: LibraryContext, library_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    sets_dir = context.sets_dir()
    (sets_dir / "broken.json").write_text("{not json", encoding="utf-8")
    (sets_dir / "My Set.json").write_text(json.dumps({"format": 1, "name": "x", "loras": []}))
    sets_store.save_set(context, {"name": "Good", "loras": []})
    with caplog.at_level(logging.WARNING, logger="lora_library"):
        listed = sets_store.list_sets(context)
    assert [e["slug"] for e in listed] == ["good"]
    assert any("broken" in r.message for r in caplog.records)
    assert any("My Set" in r.message and "rename" in r.message for r in caplog.records)
    assert sets_store.list_sets(context) == listed


def test_vanished_sets_dir_is_recreated_and_the_ensured_memo_forgotten(
    context: LibraryContext, monkeypatch
) -> None:
    _three_sets(context)
    sets_dir = context.sets_dir()
    sets_store.list_sets(context)
    import shutil

    shutil.rmtree(sets_dir)
    assert sets_store.list_sets(context) == []
    assert sets_dir.is_dir()  # re-created: the stale "ensured" memo was dropped


def test_load_layout_reads_the_file_once_per_mtime_and_rides_the_cached_listing(
    context: LibraryContext, monkeypatch
) -> None:
    _three_sets(context)
    reads: list[Path] = []
    original_read_text = Path.read_text

    def counting(path_self, *args, **kwargs):
        if path_self.name == sets_store.LAYOUT_FILENAME:
            reads.append(Path(path_self))
        return original_read_text(path_self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting)
    parses = _ParseCounter(monkeypatch)

    assert sets_store.load_layout(context)["order"][""] == ["apple", "mango", "zebra"]
    assert reads == []  # no file yet
    saved = sets_store.save_layout(context, {"categories": ["Pets"], "order": {"Pets": ["zebra"]}})
    assert saved["order"] == {"": ["apple", "mango"], "Pets": ["zebra"]}
    parses.slugs.clear()
    assert sets_store.load_layout(context) == saved
    assert sets_store.load_layout(context) == saved
    assert len(reads) == 1
    assert parses.slugs == []  # healing reused the cached listing

    # an edit by another machine (new mtime/size) is re-read
    time.sleep(0.02)
    layout_file = sets_store.layout_path(context)
    layout_file.write_text(json.dumps({"categories": ["Other"], "order": {"Other": ["apple"]}}))
    os.utime(
        layout_file,
        ns=(layout_file.stat().st_atime_ns, layout_file.stat().st_mtime_ns + 1_000_000_000),
    )
    assert sets_store.load_layout(context)["categories"] == ["Other"]
    assert len(reads) == 2


def test_clear_caches_forgets_everything(context: LibraryContext, monkeypatch) -> None:
    _three_sets(context)
    counter = _ParseCounter(monkeypatch)
    sets_store.list_sets(context)
    sets_store.clear_caches()
    sets_store.list_sets(context)
    assert len(counter.slugs) == 6


# ============================== 5. sets open_folder + sets_dir/is_default_library


async def test_get_sets_reports_where_the_states_live(
    context: LibraryContext, tmp_path: Path, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    body = await (await client.get("/lora_library/sets")).json()
    assert body == {
        "sets": [],
        "sets_dir": str(context.default_library_dir / "sets"),
        "is_default_library": True,
    }
    custom = tmp_path / "nas-library"
    context.save_config({"library_dir": str(custom)})
    body = await (await client.get("/lora_library/sets")).json()
    assert body["sets_dir"] == str(custom / "sets")
    assert body["is_default_library"] is False


async def test_post_sets_open_folder_reveals_the_sets_dir(
    context: LibraryContext, aiohttp_client, monkeypatch
) -> None:
    calls: list[Path] = []
    monkeypatch.setattr(routes_sets, "_reveal_folder", calls.append)
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/lora_library/sets/open_folder")  # no body needed
    assert resp.status == 200
    assert await resp.json() == {"ok": True, "path": str(context.sets_dir())}
    assert calls == [context.sets_dir()]
    assert context.sets_dir().is_dir()


async def test_post_sets_open_folder_remote_is_403_with_the_notebook_wording(
    context: LibraryContext, aiohttp_client, monkeypatch
) -> None:
    calls: list[Path] = []
    monkeypatch.setattr(routes_sets, "_reveal_folder", calls.append)
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/lora_library/sets/open_folder", json={}, headers=REMOTE)
    assert resp.status == 403
    assert (await resp.json())["error"] == (
        "opening a folder only works in a browser on the machine ComfyUI runs on"
    )
    assert calls == []


async def test_post_sets_open_folder_reveal_failure_is_500(
    context: LibraryContext, aiohttp_client, monkeypatch
) -> None:
    def boom(_path: Path) -> None:
        raise RuntimeError("no file manager found")

    monkeypatch.setattr(routes_sets, "_reveal_folder", boom)
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/lora_library/sets/open_folder", json={})
    assert resp.status == 500
    assert (await resp.json())["error"] == "no file manager found"


async def test_post_sets_open_folder_unreachable_library_is_400_naming_the_folder(
    context: LibraryContext, tmp_path: Path, aiohttp_client, monkeypatch
) -> None:
    calls: list[Path] = []
    monkeypatch.setattr(routes_sets, "_reveal_folder", calls.append)
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    context.save_config({"library_dir": str(blocker / "library")})
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/lora_library/sets/open_folder", json={})
    assert resp.status == 400
    body = await resp.json()
    assert "unreachable" in body["error"] and "blocker" in body["error"]
    assert calls == []
