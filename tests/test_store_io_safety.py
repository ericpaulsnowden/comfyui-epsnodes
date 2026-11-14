"""Data-safety regressions for the file-store I/O layer (FORMAT.md §3.5/§3.6):

* an atomic save must never destroy the last complete copy of the data;
* ``load_*`` must return contents and mtime of the SAME file version;
* ``path_locks.lock_for`` must give one lock per FILE, not per spelling;
* a failed notebook save is a JSON ``{"error"}`` 5xx, not a bare 500.

Fault injection is by monkeypatch; nothing here needs a flaky share.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from aiohttp import web

from lora_library import context as context_module
from lora_library import lora_picker_store, markdown_store, path_locks
from lora_library.context import LibraryContext
from lora_library.routes import build_routes

# --------------------------------------------------------------- atomic save


def _bytes_writer():
    pytest.importorskip("torch")  # image_grid_store imports it at module level
    from eps_image import image_grid_store

    return image_grid_store._atomic_write_bytes, lambda p: p.read_bytes()


def _text_writer():
    return context_module._atomic_write_text, lambda p: p.read_text(encoding="utf-8")


@pytest.fixture(params=["lora_library_text", "image_grid_bytes"])
def writer(request):
    """(write(path, "new"), read(path), label) for each deliberate twin."""
    if request.param == "lora_library_text":
        write, read = _text_writer()
        return (lambda p, s: write(p, s)), read
    write, read = _bytes_writer()
    return (lambda p, s: write(p, s.encode("utf-8"))), (
        lambda p: read(p).decode("utf-8")
    )


def _gvfs_replace(real_replace):
    """os.replace as a gvfs/FUSE mount behaves: EEXIST over an existing file."""

    def replace(src, dst):
        if os.path.exists(dst):
            raise FileExistsError(17, "File exists", str(dst))
        return real_replace(src, dst)

    return replace


def _surviving_texts(directory: Path, read) -> set[str]:
    return {read(p) for p in directory.iterdir() if p.is_file()}


def test_gvfs_rename_over_existing_still_saves_and_leaves_no_litter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer
) -> None:
    write, read = writer
    target = tmp_path / "notes.md"
    target.write_text("old", encoding="utf-8")
    monkeypatch.setattr(os, "replace", _gvfs_replace(os.replace))

    write(target, "new")

    assert read(target) == "new"
    assert [p.name for p in tmp_path.iterdir()] == ["notes.md"]


def test_no_complete_version_is_lost_when_every_replace_and_direct_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer
) -> None:
    """The audit scenario: a NAS disconnecting mid-save. Whatever the save
    does, 'old' or the complete 'new' must still be on disk afterwards."""
    write, read = writer
    target = tmp_path / "notes.md"
    target.write_text("old", encoding="utf-8")

    def always_exists(src, dst):
        raise FileExistsError(17, "File exists", str(dst))

    def disconnected(self, *args, **kwargs):
        raise OSError("NAS disconnected")

    monkeypatch.setattr(os, "replace", always_exists)
    monkeypatch.setattr(Path, "write_text", disconnected)
    monkeypatch.setattr(Path, "write_bytes", disconnected)

    with pytest.raises(OSError):
        write(target, "new")

    assert _surviving_texts(tmp_path, read) & {"old", "new"}
    assert read(target) == "old"  # the failed save left the original in place


def test_unrestorable_original_keeps_both_copies_and_names_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer
) -> None:
    """If putting the new file in place fails AND the original cannot be moved
    back, both complete copies stay on disk and the error says where."""
    write, read = writer
    target = tmp_path / "notes.md"
    target.write_text("old", encoding="utf-8")
    real_rename = os.rename

    def always_exists(src, dst):
        raise FileExistsError(17, "File exists", str(dst))

    def no_restore(src, dst):
        if str(dst) == str(target):
            raise OSError("NAS disconnected")
        return real_rename(src, dst)

    def disconnected(self, *args, **kwargs):
        raise OSError("NAS disconnected")

    monkeypatch.setattr(os, "replace", always_exists)
    monkeypatch.setattr(os, "rename", no_restore)
    monkeypatch.setattr(Path, "write_text", disconnected)
    monkeypatch.setattr(Path, "write_bytes", disconnected)

    with pytest.raises(OSError) as excinfo:
        write(target, "new")

    leftovers = {p.name: read(p) for p in tmp_path.iterdir()}
    assert sorted(leftovers.values()) == ["new", "old"]
    for name in leftovers:
        assert name in str(excinfo.value)


# ------------------------------------------------- load: contents == mtime


def _replace_after_read(monkeypatch, module, path: Path, new_text: str, new_mtime: int):
    """Make *module*'s ``open`` swap in a different file version right after
    the first ``read()`` -- the atomic save landing between read and stat."""
    real_open = open
    done = []

    class Handle:
        def __init__(self, fh):
            self._fh = fh

        def __enter__(self):
            self._fh.__enter__()
            return self

        def __exit__(self, *exc):
            return self._fh.__exit__(*exc)

        def __getattr__(self, name):
            return getattr(self._fh, name)

        def read(self, *args):
            data = self._fh.read(*args)
            if not done:
                done.append(True)
                tmp = path.with_name(path.name + ".swap")
                tmp.write_text(new_text, encoding="utf-8")
                os.utime(tmp, (new_mtime, new_mtime))
                os.replace(tmp, path)
            return data

    def fake_open(file, *args, **kwargs):
        fh = real_open(file, *args, **kwargs)
        return Handle(fh) if Path(file) == path else fh

    monkeypatch.setattr(module, "open", fake_open, raising=False)


needs_posix_rename = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows refuses to rename over a file held open, so the race "
    "cannot be staged there",
)

OLD_MTIME = 1_600_000_000
NEW_MTIME = 1_700_000_000


@needs_posix_rename
def test_load_notebook_mtime_belongs_to_the_text_it_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "notes.md"
    path.write_text("## Old\nold body\n", encoding="utf-8")
    os.utime(path, (OLD_MTIME, OLD_MTIME))
    _replace_after_read(monkeypatch, markdown_store, path, "## New\nnew body\n", NEW_MTIME)

    parsed, mtime, _ending = markdown_store.load_notebook(path)

    assert [e["name"] for e in markdown_store.list_entries(parsed)] == ["Old"]
    assert mtime == OLD_MTIME


@needs_posix_rename
def test_picker_load_state_mtime_belongs_to_the_text_it_read(
    context: LibraryContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = lora_picker_store.picker_path(context)
    path.write_text(
        json.dumps({"format": 1, "favorites": ["old.safetensors"], "recents": []}),
        encoding="utf-8",
    )
    os.utime(path, (OLD_MTIME, OLD_MTIME))
    new_text = json.dumps({"format": 1, "favorites": ["new.safetensors"], "recents": []})
    _replace_after_read(monkeypatch, lora_picker_store, path, new_text, NEW_MTIME)

    state, mtime = lora_picker_store.load_state(context)

    assert state["favorites"] == ["old.safetensors"]
    assert mtime == OLD_MTIME


@needs_posix_rename
def test_presets_load_mtime_belongs_to_the_text_it_read(
    context: LibraryContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = pytest.importorskip("eps_image.resolution_presets_store")
    path = store.presets_path(context)
    path.write_text(json.dumps({"format": 1, "presets": {}}), encoding="utf-8")
    os.utime(path, (OLD_MTIME, OLD_MTIME))
    new_text = json.dumps({"format": 1, "presets": {"x": {"junk": True}}})
    _replace_after_read(monkeypatch, store, path, new_text, NEW_MTIME)

    _presets, mtime = store.load_presets(context)

    assert mtime == OLD_MTIME


# ---------------------------------------------------------------- path locks


def _is_case_insensitive(directory: Path) -> bool:
    probe = directory / "CaseProbe.tmp"
    probe.write_text("x", encoding="utf-8")
    try:
        return (directory / "caseprobe.tmp").exists()
    finally:
        probe.unlink()


def test_differently_cased_spellings_of_one_file_share_a_lock(tmp_path: Path) -> None:
    if not _is_case_insensitive(tmp_path):
        pytest.skip("case-sensitive filesystem: the two spellings are two files")
    (tmp_path / "Loras.md").write_text("x", encoding="utf-8")

    assert path_locks.lock_for(tmp_path / "Loras.md") is path_locks.lock_for(
        tmp_path / "loras.md"
    )


def test_lock_survives_the_file_being_replaced_by_a_save(tmp_path: Path) -> None:
    """An atomic save swaps the inode; the file must keep its lock."""
    target = tmp_path / "notes.md"
    target.write_text("a", encoding="utf-8")
    before = path_locks.lock_for(target)

    context_module._atomic_write_text(target, "b")

    assert path_locks.lock_for(target) is before


def test_lock_taken_before_the_file_existed_is_the_one_used_after(tmp_path: Path) -> None:
    target = tmp_path / "fresh.md"
    before = path_locks.lock_for(target)
    target.write_text("a", encoding="utf-8")

    assert path_locks.lock_for(target) is before


def test_hard_links_share_a_lock(tmp_path: Path) -> None:
    target = tmp_path / "notes.md"
    target.write_text("a", encoding="utf-8")
    try:
        os.link(target, tmp_path / "alias.md")
    except OSError:
        pytest.skip("hard links unsupported here")

    assert path_locks.lock_for(target) is path_locks.lock_for(tmp_path / "alias.md")


# ------------------------------------------------- routes: write failure JSON

SEED = "# C1\n\n## A\nbody a\n\n# C2\n\n## B\nbody b\n"

MUTATIONS = [
    ("/lora_library/notebook/entry", {"name": "A", "text": "changed"}),
    ("/lora_library/notebook/category", {"name": "C1", "description": "d"}),
    ("/lora_library/notebook/delete", {"name": "A"}),
    ("/lora_library/notebook/move", {"name": "A", "before": "B"}),
    ("/lora_library/notebook/move_category", {"name": "C2", "before": "C1"}),
    ("/lora_library/notebook/delete_category", {"name": "C1"}),
]


@pytest.mark.parametrize(("url", "body"), MUTATIONS, ids=[m[0].rsplit("/", 1)[1] for m in MUTATIONS])
async def test_failed_notebook_save_is_a_json_5xx_naming_file_and_cause(
    context: LibraryContext,
    library_dir: Path,
    aiohttp_client,
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    body: dict,
) -> None:
    notebook = library_dir / "loras.md"
    notebook.write_text(SEED, encoding="utf-8")
    app = web.Application()
    app.add_routes(build_routes(context))
    client = await aiohttp_client(app)

    def read_only_share(path, text):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(markdown_store, "_atomic_write_text", read_only_share)

    resp = await client.post(url, json={"file": "loras.md", **body})

    assert resp.status == 500
    payload = await resp.json()
    assert "loras.md" in payload["error"]
    assert "Permission denied" in payload["error"]
    assert notebook.read_text(encoding="utf-8") == SEED
