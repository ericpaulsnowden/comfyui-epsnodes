"""Core route rows (FORMAT.md §5): config is_local + the fs/list picker feed.

Version/loras rows are exercised implicitly all over the suite; these tests
pin the two host-machine-awareness behaviors the §7.2 file panel depends on.

fs/list reshaped 2026-07-19 to STANDARD-fs-browse.md, the shared cross-plugin
"server filesystem Browse" contract with cpsb/cprb: names-only `{"name": ...}`
(`{"name", "size", "mtime"}` for files) entries, a `sep` field, a `truncated`
cap, dotfile-skipping, and a `ROOTS` sentinel that now returns a labeled
roots list (default dir + Home + platform roots) on every platform, not just
Windows.

2026-07-26: the POSIX platform tail split in two -- macOS keeps its exact
pre-existing `/Volumes` behavior, while Linux (a real ComfyUI-on-Linux bug
report: the picker couldn't reach a NAS mount, `/Volumes` doesn't exist
there) gets its own conventional-mount-parent tail (`/`, `/mnt`, `/media`,
the per-user auto-mount dirs, `/srv`, GVFS), exercised here the same way the
Windows/macOS branches always have been: monkeypatching the `_is_linux`
seam plus a fake candidate set, from this (macOS) test machine. Also new:
`fs/list` now 400s a typed `scheme://` value (e.g. `smb://...`) with a
message explaining it's a network address, not a file path, instead of the
generic "must be an absolute path" 400.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lora_library import routes as lora_routes
from lora_library.context import LibraryContext
from lora_library.routes import build_routes

REMOTE_HEADERS = {"X-Forwarded-For": "192.168.1.50"}


@pytest.fixture
async def client(context: LibraryContext, aiohttp_client):
    app = web.Application()
    app.add_routes(build_routes(context))
    return await aiohttp_client(app)


async def test_config_reports_is_local_true_for_loopback(client) -> None:
    data = await (await client.get("/lora_library/config")).json()
    assert data["is_local"] is True


async def test_config_reports_is_local_false_for_forwarded_caller(client) -> None:
    data = await (await client.get("/lora_library/config", headers=REMOTE_HEADERS)).json()
    assert data["is_local"] is False


async def test_fs_list_defaults_to_library_dir_and_filters_md(
    client, context: LibraryContext
) -> None:
    library = context.library_dir()
    (library / "prompts.md").write_text("## A\n", encoding="utf-8")
    (library / "notes.txt").write_text("x", encoding="utf-8")
    (library / "sub").mkdir()
    response = await client.get("/lora_library/fs/list")
    data = await response.json()
    assert data["dir"] == str(library)
    assert data["sep"] == os.sep
    assert data["truncated"] is False
    assert {"name": "sub"} in data["dirs"]
    assert len(data["files"]) == 1
    assert data["files"][0]["name"] == "prompts.md"
    assert data["files"][0]["size"] > 0
    assert isinstance(data["files"][0]["mtime"], float)
    assert data["parent"] == str(library.parent)


async def test_fs_list_skips_dotfiles(client, context: LibraryContext) -> None:
    library = context.library_dir()
    (library / "prompts.md").write_text("## A\n", encoding="utf-8")
    (library / ".hidden.md").write_text("## B\n", encoding="utf-8")
    (library / ".hidden_dir").mkdir()
    response = await client.get("/lora_library/fs/list")
    data = await response.json()
    assert [entry["name"] for entry in data["files"]] == ["prompts.md"]
    assert [entry["name"] for entry in data["dirs"]] == []


async def test_fs_list_ext_param_narrows_the_default_allowlist(
    client, context: LibraryContext
) -> None:
    library = context.library_dir()
    (library / "a.md").write_text("## A\n", encoding="utf-8")
    response = await client.get("/lora_library/fs/list", params={"ext": ".md"})
    data = await response.json()
    assert [entry["name"] for entry in data["files"]] == ["a.md"]


async def test_fs_list_navigates_an_explicit_absolute_dir(client, tmp_path: Path) -> None:
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "lib.md").write_text("## B\n", encoding="utf-8")
    response = await client.get(
        "/lora_library/fs/list", params={"dir": str(tmp_path / "elsewhere")}
    )
    data = await response.json()
    assert [entry["name"] for entry in data["files"]] == ["lib.md"]


async def test_fs_list_sorted_case_insensitively_dirs_then_files(
    client, context: LibraryContext
) -> None:
    library = context.library_dir()
    (library / "zeta.md").write_text("## Z\n", encoding="utf-8")
    (library / "Alpha.md").write_text("## A\n", encoding="utf-8")
    (library / "Zeta").mkdir()
    (library / "alpha_dir").mkdir()
    response = await client.get("/lora_library/fs/list")
    data = await response.json()
    assert [entry["name"] for entry in data["dirs"]] == ["alpha_dir", "Zeta"]
    assert [entry["name"] for entry in data["files"]] == ["Alpha.md", "zeta.md"]


async def test_fs_list_truncates_over_500_entries(client, context: LibraryContext) -> None:
    library = context.library_dir()
    for index in range(501):
        (library / f"{index:04d}.md").touch()
    response = await client.get("/lora_library/fs/list")
    data = await response.json()
    assert data["truncated"] is True
    assert len(data["files"]) == 500


async def test_fs_list_is_loopback_only(client) -> None:
    response = await client.get("/lora_library/fs/list", headers=REMOTE_HEADERS)
    assert response.status == 403


async def test_fs_list_rejects_relative_dir(client) -> None:
    response = await client.get("/lora_library/fs/list", params={"dir": "not/absolute"})
    assert response.status == 400


async def test_fs_list_rejects_a_file_path(client, tmp_path: Path) -> None:
    a_file = tmp_path / "not_a_directory.md"
    a_file.write_text("## A\n", encoding="utf-8")
    response = await client.get("/lora_library/fs/list", params={"dir": str(a_file)})
    assert response.status == 400


async def test_fs_list_unreadable_dir_is_400(client, tmp_path: Path) -> None:
    response = await client.get(
        "/lora_library/fs/list", params={"dir": str(tmp_path / "nope" / "missing")}
    )
    assert response.status == 400


# ------------------------------------------------ fs/list: ROOTS & drive roots
#
# 2026-07-19 fix: the §7.2 picker could reach the top of C:\ but no further.
# Drive enumeration and `os.name` aren't real on this (macOS) test machine,
# so every Windows-flavored case goes through the `_is_windows` /
# `_list_windows_drives` monkeypatch seams (routes.py §"fs/list: ROOTS &
# drives") rather than touching real drives.


async def test_fs_list_roots_sentinel_lists_windows_drives_when_monkeypatched(
    client, monkeypatch, context: LibraryContext
) -> None:
    monkeypatch.setattr(lora_routes, "_is_windows", lambda: True)
    monkeypatch.setattr(lora_routes, "_list_windows_drives", lambda: ["C:\\", "D:\\", "U:\\"])
    response = await client.get("/lora_library/fs/list", params={"dir": "ROOTS"})
    data = await response.json()
    assert data["dir"] == "ROOTS"
    assert data["parent"] is None
    assert data["sep"] == os.sep
    assert data["files"] == []
    assert data["truncated"] is False

    by_name = {entry["name"]: entry["path"] for entry in data["dirs"]}
    assert by_name["Library Folder"] == str(context.library_dir().resolve())
    assert by_name["Home"] == str(Path.home().resolve())
    assert by_name["C:"] == "C:\\"
    assert by_name["D:"] == "D:\\"
    assert by_name["U:"] == "U:\\"
    # Standard's declared ROOTS order: default dir, Home, then platform roots.
    assert [entry["name"] for entry in data["dirs"]] == [
        "Library Folder", "Home", "C:", "D:", "U:"
    ]


async def test_fs_list_roots_sentinel_is_still_loopback_only(client, monkeypatch) -> None:
    monkeypatch.setattr(lora_routes, "_is_windows", lambda: True)
    response = await client.get(
        "/lora_library/fs/list", params={"dir": "ROOTS"}, headers=REMOTE_HEADERS
    )
    assert response.status == 403


async def test_fs_list_roots_sentinel_includes_default_dir_and_home_on_posix(
    client, context: LibraryContext
) -> None:
    response = await client.get("/lora_library/fs/list", params={"dir": "ROOTS"})
    data = await response.json()
    assert data["dir"] == "ROOTS"
    assert data["parent"] is None

    by_name = {entry["name"]: entry["path"] for entry in data["dirs"]}
    assert by_name["Library Folder"] == str(context.library_dir().resolve())
    assert by_name["Home"] == str(Path.home().resolve())


async def test_fs_list_roots_sentinel_lists_macos_volumes_when_monkeypatched(
    client, monkeypatch
) -> None:
    monkeypatch.setattr(
        lora_routes, "_list_macos_volumes", lambda: ["/Volumes/Macintosh HD", "/Volumes/Backup"]
    )
    response = await client.get("/lora_library/fs/list", params={"dir": "ROOTS"})
    data = await response.json()
    by_name = {entry["name"]: entry["path"] for entry in data["dirs"]}
    assert by_name["Macintosh HD"] == "/Volumes/Macintosh HD"
    assert by_name["Backup"] == "/Volumes/Backup"


# --------------------------------------------------- fs/list: Linux ROOTS tail
#
# 2026-07-26 fix: on a Linux ComfyUI host, `/Volumes` never exists, so the
# pre-fix code (which called `_list_macos_volumes` for "any non-Windows
# host") silently degraded the whole ROOTS tail to `[]` -- the owner could
# not browse to `/mnt`, a NAS mount, or anything but the library dir/Home.
# Real Linux mounts aren't available on this (macOS) test machine, so every
# case below drives it through the `_is_linux` seam plus a fake
# `_linux_mount_root_candidates` set (routes.py's factored-out-so-tests-can-
# replace-it-wholesale pattern, same as `_list_macos_volumes`/
# `_list_windows_drives`) -- exactly parallel to the Windows tests above.


async def test_fs_list_roots_sentinel_lists_linux_mount_roots_when_monkeypatched(
    client, monkeypatch, context: LibraryContext, tmp_path: Path
) -> None:
    fake_root = tmp_path / "fakeroot"
    fake_mnt = tmp_path / "mnt"
    fake_media = tmp_path / "media"
    fake_media_user = tmp_path / "media_user"
    fake_run_media_user = tmp_path / "run_media_user"
    fake_srv = tmp_path / "srv"
    fake_gvfs = tmp_path / "gvfs"
    for directory in (
        fake_root,
        fake_mnt,
        fake_media,
        fake_media_user,
        fake_run_media_user,
        fake_srv,
        fake_gvfs,
    ):
        directory.mkdir()

    monkeypatch.setattr(lora_routes, "_is_linux", lambda: True)
    monkeypatch.setattr(
        lora_routes,
        "_linux_mount_root_candidates",
        lambda: [
            ("Filesystem root", str(fake_root)),
            ("mnt", str(fake_mnt)),
            ("media", str(fake_media)),
            ("Removable media", str(fake_media_user)),
            ("Removable media (run)", str(fake_run_media_user)),
            ("srv", str(fake_srv)),
            ("Network shares (GVFS)", str(fake_gvfs)),
        ],
    )
    response = await client.get("/lora_library/fs/list", params={"dir": "ROOTS"})
    data = await response.json()
    assert data["dir"] == "ROOTS"
    assert data["parent"] is None
    assert data["sep"] == os.sep
    assert data["files"] == []
    assert data["truncated"] is False

    by_name = {entry["name"]: entry["path"] for entry in data["dirs"]}
    assert by_name["Library Folder"] == str(context.library_dir().resolve())
    assert by_name["Home"] == str(Path.home().resolve())
    assert by_name["Filesystem root"] == str(fake_root)
    assert by_name["mnt"] == str(fake_mnt)
    assert by_name["media"] == str(fake_media)
    assert by_name["Removable media"] == str(fake_media_user)
    assert by_name["Removable media (run)"] == str(fake_run_media_user)
    assert by_name["srv"] == str(fake_srv)
    assert by_name["Network shares (GVFS)"] == str(fake_gvfs)
    # Standard's declared ROOTS order: default dir, Home, then platform
    # roots -- and the platform tail itself keeps the candidates' own order.
    assert [entry["name"] for entry in data["dirs"]] == [
        "Library Folder",
        "Home",
        "Filesystem root",
        "mnt",
        "media",
        "Removable media",
        "Removable media (run)",
        "srv",
        "Network shares (GVFS)",
    ]


async def test_fs_list_roots_sentinel_skips_nonexistent_linux_candidates(
    client, monkeypatch, tmp_path: Path
) -> None:
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    monkeypatch.setattr(lora_routes, "_is_linux", lambda: True)
    monkeypatch.setattr(
        lora_routes,
        "_linux_mount_root_candidates",
        lambda: [
            ("Filesystem root", str(real_dir)),
            ("mnt", str(tmp_path / "does-not-exist")),
        ],
    )
    response = await client.get("/lora_library/fs/list", params={"dir": "ROOTS"})
    data = await response.json()
    names = [entry["name"] for entry in data["dirs"]]
    assert "Filesystem root" in names
    assert "mnt" not in names


async def test_fs_list_roots_sentinel_skips_unreadable_linux_candidate_not_fatal(
    client, monkeypatch, tmp_path: Path
) -> None:
    unreadable = tmp_path / "unreadable"
    unreadable.mkdir()
    readable = tmp_path / "readable"
    readable.mkdir()
    monkeypatch.setattr(lora_routes, "_is_linux", lambda: True)
    monkeypatch.setattr(
        lora_routes,
        "_linux_mount_root_candidates",
        lambda: [("bad", str(unreadable)), ("good", str(readable))],
    )

    real_is_dir = Path.is_dir

    def flaky_is_dir(self):
        if str(self) == str(unreadable):
            raise PermissionError("denied")
        return real_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", flaky_is_dir)
    response = await client.get("/lora_library/fs/list", params={"dir": "ROOTS"})
    # A stat failure on one candidate must not take down the whole request.
    assert response.status == 200
    data = await response.json()
    names = [entry["name"] for entry in data["dirs"]]
    assert "bad" not in names
    assert "good" in names


async def test_fs_list_roots_sentinel_dedupes_duplicate_linux_candidate_paths(
    client, monkeypatch, tmp_path: Path
) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setattr(lora_routes, "_is_linux", lambda: True)
    monkeypatch.setattr(
        lora_routes,
        "_linux_mount_root_candidates",
        lambda: [("First label", str(shared)), ("Second label", str(shared))],
    )
    response = await client.get("/lora_library/fs/list", params={"dir": "ROOTS"})
    data = await response.json()
    matches = [entry for entry in data["dirs"] if entry["path"] == str(shared)]
    assert len(matches) == 1
    assert matches[0]["name"] == "First label"


async def test_fs_list_roots_sentinel_does_not_duplicate_home_when_a_linux_root_coincides(
    client, monkeypatch
) -> None:
    home = str(Path.home().resolve())
    monkeypatch.setattr(lora_routes, "_is_linux", lambda: True)
    monkeypatch.setattr(
        lora_routes, "_linux_mount_root_candidates", lambda: [("Home again", home)]
    )
    response = await client.get("/lora_library/fs/list", params={"dir": "ROOTS"})
    data = await response.json()
    matches = [entry for entry in data["dirs"] if entry["path"] == home]
    assert len(matches) == 1
    # First occurrence (the default "Home" entry) wins over the platform
    # tail's later duplicate.
    assert matches[0]["name"] == "Home"


# ------------------------------------------------- fs/list: URI-style `dir` 400
#
# 2026-07-26 owner report: typing an `smb://...` address into the picker's
# manual-path field "doesn't work" -- correctly (GIO/GVFS addresses aren't
# filesystem paths), but the generic "dir must be an absolute path" 400
# didn't say why. `fs/list` now recognizes ANY `scheme://` value and 400s
# with a plain-language explanation instead.


async def test_fs_list_rejects_a_uri_style_dir_with_a_helpful_message(client) -> None:
    response = await client.get(
        "/lora_library/fs/list",
        params={"dir": "smb://dxp4800pro-ring.local/personal_folder/docs/loras.md"},
    )
    assert response.status == 400
    data = await response.json()
    assert data["error"].startswith("smb://")
    assert "network address" in data["error"]
    assert "not a file path" in data["error"]
    assert "absolute path" not in data["error"]
    assert "/mnt" in data["error"]
    assert "gvfs" in data["error"]


async def test_fs_list_uri_style_error_names_the_actual_scheme(client) -> None:
    # Scheme-agnostic: whatever scheme the caller used gets echoed back, not
    # a hardcoded "smb".
    response = await client.get("/lora_library/fs/list", params={"dir": "nfs://nas.local/share"})
    assert response.status == 400
    data = await response.json()
    assert data["error"].startswith("nfs://")


async def test_fs_list_drive_root_parent_climbs_to_roots_under_monkeypatched_windows(
    client, monkeypatch
) -> None:
    # "/" doubles as our only real, listable filesystem root on this test
    # machine — `_is_windows` alone decides which label a root's parent
    # gets, so patching just that seam is enough to exercise the branch.
    monkeypatch.setattr(lora_routes, "_is_windows", lambda: True)
    response = await client.get("/lora_library/fs/list", params={"dir": "/"})
    data = await response.json()
    assert data["dir"] == "/"
    assert data["parent"] == "ROOTS"


async def test_fs_list_posix_root_parent_is_null_without_monkeypatching(client) -> None:
    response = await client.get("/lora_library/fs/list", params={"dir": "/"})
    data = await response.json()
    assert data["dir"] == "/"
    assert data["parent"] is None


def test_is_unc_share_root_detects_a_share_root() -> None:
    assert lora_routes._is_unc_share_root(Path(r"\\server\share")) is True


def test_is_unc_share_root_rejects_a_drive_root() -> None:
    assert lora_routes._is_unc_share_root(Path("C:\\")) is False


def test_fs_root_parent_is_roots_for_a_drive_root_on_windows() -> None:
    assert lora_routes._fs_root_parent(Path("C:\\"), windows=True) == "ROOTS"


def test_fs_root_parent_is_null_for_a_unc_share_root_even_on_windows() -> None:
    # No portable way to enumerate a server's other shares (FORMAT.md §5) —
    # a share root has nothing to climb to, unlike a drive root.
    assert lora_routes._fs_root_parent(Path(r"\\server\share"), windows=True) is None


def test_fs_root_parent_is_null_on_posix() -> None:
    assert lora_routes._fs_root_parent(Path("/"), windows=False) is None


# ------------------------------------------------ fs/list: small pure helpers
#
# 2026-07-26 fix: direct unit coverage for the small helpers behind the
# Linux ROOTS tail and the `://` 400, mirroring how `_is_unc_share_root`/
# `_fs_root_parent` above get their own direct tests alongside the
# HTTP-level ones.


def test_dedupe_root_entries_keeps_first_occurrence_of_a_repeated_path() -> None:
    entries = [
        {"name": "First", "path": "/same"},
        {"name": "Second", "path": "/same"},
        {"name": "Third", "path": "/different"},
    ]
    assert lora_routes._dedupe_root_entries(entries) == [
        {"name": "First", "path": "/same"},
        {"name": "Third", "path": "/different"},
    ]


def test_dedupe_root_entries_is_a_no_op_when_every_path_is_unique() -> None:
    entries = [{"name": "A", "path": "/a"}, {"name": "B", "path": "/b"}]
    assert lora_routes._dedupe_root_entries(entries) == entries


def test_uri_style_path_error_names_the_scheme_and_explains_the_fix() -> None:
    message = lora_routes._uri_style_path_error("smb://nas.local/share")
    assert message.startswith("smb://")
    assert "network address" in message
    assert "not a file path" in message
    assert "/mnt" in message
    assert "gvfs" in message


def test_uri_style_path_error_is_scheme_agnostic() -> None:
    message = lora_routes._uri_style_path_error("nfs://nas.local/share")
    assert message.startswith("nfs://")


def test_safe_current_username_returns_a_string_or_none_on_this_real_machine() -> None:
    # Never raises (2026-07-26 contract) -- exercised for real, unmocked,
    # against whatever this actual machine's home directory is.
    username = lora_routes._safe_current_username()
    assert username is None or isinstance(username, str)


def test_safe_current_uid_returns_an_int_on_this_real_posix_machine() -> None:
    uid = lora_routes._safe_current_uid()
    assert isinstance(uid, int)


def test_linux_mount_root_candidates_keeps_the_documented_order() -> None:
    # Pins the REAL (unmocked) candidate function's own internal order --
    # the HTTP-level ROOTS tests above replace this function wholesale with
    # a fake candidate set, so they cannot catch an ordering bug inside its
    # real implementation (caught once already during review: `/srv` was
    # briefly assembled BEFORE the per-user media dirs instead of after).
    # Documented order: `/`, `/mnt`, `/media`, then (when a username is
    # resolvable) `/media/<user>` and `/run/media/<user>`, then `/srv`, then
    # (when a uid is resolvable) the GVFS dir.
    candidates = lora_routes._linux_mount_root_candidates()
    paths = [path for _label, path in candidates]
    assert paths[0] == "/"
    assert paths[1] == "/mnt"
    assert paths[2] == "/media"
    assert "/srv" in paths
    srv_index = paths.index("/srv")

    username = lora_routes._safe_current_username()
    if username:
        assert paths[3:srv_index] == [f"/media/{username}", f"/run/media/{username}"]

    uid = lora_routes._safe_current_uid()
    if uid is not None:
        assert paths[srv_index + 1] == f"/run/user/{uid}/gvfs"
        assert srv_index + 1 == len(paths) - 1  # GVFS is the last candidate


def test_list_linux_mount_roots_runs_cleanly_on_this_real_machine() -> None:
    # Never invoked in production on macOS (gated by `_is_linux` in
    # `_platform_root_entries`), but must still not raise if called
    # directly -- a real-machine smoke test on top of the monkeypatched-
    # candidate HTTP-level tests above.
    roots = lora_routes._list_linux_mount_roots()
    assert isinstance(roots, list)
    for label, path in roots:
        assert isinstance(label, str)
        assert isinstance(path, str)


# ------------------------------------------------- config: library_dir diagnosis
#
# 2026-07-19 fix: a configured library_dir the server machine can't resolve
# (an unmounted NAS share, or a path shaped for the wrong OS) used to be
# invisible until a node errored at run time. `_diagnose_library_dir` takes
# `is_windows` as a parameter (not a bare `os.name` read) specifically so
# both OS-mismatch directions are exercisable from this (macOS) test
# machine.


def test_diagnose_library_dir_flags_a_posix_path_on_a_windows_server() -> None:
    note = lora_routes._diagnose_library_dir("/Volumes/NAS/comfy-library", is_windows=True)
    assert "macOS/Linux" in note
    assert "Windows" in note
    assert "set the folder from the machine ComfyUI runs on" in note


def test_diagnose_library_dir_flags_a_windows_path_on_a_posix_server() -> None:
    note = lora_routes._diagnose_library_dir(r"C:\comfy-library", is_windows=False)
    assert "Windows" in note
    assert "macOS/Linux" in note
    assert "set the folder from the machine ComfyUI runs on" in note


def test_diagnose_library_dir_flags_a_unc_path_on_a_posix_server() -> None:
    note = lora_routes._diagnose_library_dir(r"\\nas\share\comfy-library", is_windows=False)
    assert "Windows" in note


def test_diagnose_library_dir_flags_a_mnt_path_on_a_windows_server() -> None:
    note = lora_routes._diagnose_library_dir("/mnt/nas/comfy-library", is_windows=True)
    assert "macOS/Linux" in note


def test_diagnose_library_dir_is_generic_when_the_shape_matches_but_its_unreachable() -> None:
    # A perfectly ordinary POSIX path, but on a POSIX server — no OS-shape
    # mismatch, so this falls through to the generic "can't reach it" note
    # (the owner's actual 2026-07-19 case: an unmounted NAS mount point).
    note = lora_routes._diagnose_library_dir("/Users/eric/nas-library", is_windows=False)
    assert "macOS/Linux" not in note
    assert "Windows" not in note
    assert "can't reach this folder" in note
    assert "NAS mounted" in note


def test_check_library_dir_is_fine_when_the_configured_dir_exists(
    context: LibraryContext, tmp_path: Path
) -> None:
    real_dir = tmp_path / "real-library"
    real_dir.mkdir()
    context.save_config({"library_dir": str(real_dir)})
    exists, note = lora_routes._check_library_dir(context)
    assert exists is True
    assert note == ""


def test_check_library_dir_is_fine_when_unconfigured(context: LibraryContext) -> None:
    # Nothing configured ⇒ the pack's own default dir — always trivially
    # fine, nothing to diagnose (FORMAT.md §5).
    exists, note = lora_routes._check_library_dir(context)
    assert exists is True
    assert note == ""


def test_check_library_dir_flags_a_bogus_unreachable_path(
    context: LibraryContext, tmp_path: Path
) -> None:
    bogus = tmp_path / "does" / "not" / "exist"
    context.save_config({"library_dir": str(bogus)})
    exists, note = lora_routes._check_library_dir(context)
    assert exists is False
    assert note != ""
    assert not bogus.exists()


async def test_config_reports_library_dir_exists_true_for_a_real_directory(
    client, context: LibraryContext, tmp_path: Path
) -> None:
    real_dir = tmp_path / "real-library"
    real_dir.mkdir()
    context.save_config({"library_dir": str(real_dir)})
    data = await (await client.get("/lora_library/config")).json()
    assert data["library_dir_exists"] is True
    assert data["library_dir_note"] == ""


async def test_config_reports_library_dir_exists_false_with_a_note_for_a_bogus_path(
    client, context: LibraryContext, tmp_path: Path
) -> None:
    bogus = tmp_path / "does" / "not" / "exist"
    context.save_config({"library_dir": str(bogus)})
    data = await (await client.get("/lora_library/config")).json()
    assert data["library_dir_exists"] is False
    assert data["library_dir_note"] != ""
    assert data["library_dir"] == str(bogus)


async def test_config_check_does_not_create_a_bogus_configured_directory(
    client, context: LibraryContext, tmp_path: Path
) -> None:
    bogus = tmp_path / "does" / "not" / "exist"
    context.save_config({"library_dir": str(bogus)})
    response = await client.get("/lora_library/config")
    assert response.status == 200
    assert not bogus.exists()
    assert not bogus.parent.exists()


async def test_config_unconfigured_library_dir_reports_exists_true(client) -> None:
    data = await (await client.get("/lora_library/config")).json()
    assert data["configured"] is False
    assert data["library_dir_exists"] is True
    assert data["library_dir_note"] == ""
