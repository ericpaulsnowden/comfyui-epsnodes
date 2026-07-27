"""HTTP route registrar + shared route helpers (FORMAT.md §2/§5).

Layout keeps parallel workstreams out of each other's files: this module
owns the CORE routes (version/config/loras) and the shared security
helpers; ``routes_notebook.py`` and ``routes_sets.py`` each expose
``register(context, routes)`` and are wired in here. Handlers close over
the injected :class:`~lora_library.context.LibraryContext`, so tests build
an ``aiohttp.web.Application`` from these registrars directly — no ComfyUI.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import string
import sys
from pathlib import Path, PureWindowsPath

from aiohttp import web

from .context import LibraryContext
from .version import __version__

logger = logging.getLogger("lora_library")

#: FORMAT.md §4 — set slugs; also the only characters accepted in URLs.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-_]*$")

#: ../../STANDARD-fs-browse.md's `dir` sentinel meaning "the virtual top
#: level": this pack's own default library directory (labeled), "Home", and
#: every drive on Windows / every `/Volumes` mount on macOS / the existing
#: conventional mount parents on Linux (2026-07-26 fix, see
#: `_platform_root_entries` -- the standard doc itself only itemizes the
#: Windows/macOS cases today; this pack's Linux tail is a documented,
#: additive extension of it, same spirit as `_fs_entry`'s `path` field).
ROOTS = "ROOTS"

#: STANDARD-fs-browse.md's locality policy for THIS pack, as an explicit,
#: documented, build-time flag (not a request-time param -- flipping this via
#: a query string would let any caller downgrade their own security posture).
#: `True` here is epsnodes' pre-existing posture (FORMAT.md §5: file browsing
#: is host-machine-only) -- porting to the shared contract must never
#: silently flip a pack's posture. Contrast cpsb's own (deliberately `False`)
#: flag for the same route shape.
FS_LIST_LOCAL_ONLY: bool = True

#: STANDARD-fs-browse.md's `ext` query param default/allowlist for this
#: pack's picker -- Prompt Notebook files are always `.md` (FORMAT.md §2).
DEFAULT_EXTENSIONS = (".md",)

#: STANDARD-fs-browse.md ROOTS listing label for this pack's own default
#: fs/list directory (`context.library_dir()` -- this pack's OWN folder, user
#: -configurable, unlike cprb's ComfyUI-owned `output_dir`; FORMAT.md §5).
_FS_LIST_DEFAULT_DIR_LABEL = "Library Folder"

#: Same for the user's home directory (always present, regardless of platform).
_FS_LIST_HOME_LABEL = "Home"

#: Cap on combined `dirs` + `files` entries returned by one
#: `/lora_library/fs/list` listing (root or directory) -- so a directory with
#: an enormous number of children can't turn one request into a
#: multi-megabyte response. Counts only entries actually emitted -- a huge
#: pile of hidden dotfiles or extension-filtered-out files never consumes a
#: slot.
_FS_LIST_MAX_ENTRIES = 500

#: FORMAT.md §5 ``library_dir_note`` -- the OS-shape mismatch message
#: (owner report 2026-07-19: a library folder set from the wrong machine's
#: perspective, e.g. a macOS `/Volumes/...` path pasted into a Windows
#: ComfyUI). ``{other}``/``{this}`` are filled with the two OS labels.
_OS_MISMATCH_NOTE = (
    "This looks like a {other} path, but ComfyUI is running on {this} — "
    "set the folder from the machine ComfyUI runs on."
)

#: Same section -- the generic "can't reach it" message when the configured
#: path is shaped fine for this OS but just isn't there right now (the
#: owner's actual 2026-07-19 case: a NAS mount that isn't mounted on the
#: server machine).
_UNREACHABLE_NOTE = (
    "The machine ComfyUI runs on can't reach this folder right now "
    "(is the NAS mounted there?)."
)


# --------------------------------------------------------------------- guards

def request_is_loopback(request: web.Request) -> bool:
    """True when *request* comes from this machine (FORMAT.md §2).

    A forwarded request (``X-Forwarded-For``) is never loopback — the proxy
    hop hides the real origin, so it gets the restricted tier. ``remote``
    being absent (unix sockets, aiohttp test clients) counts as loopback:
    both mean "not a foreign machine".
    """
    if "X-Forwarded-For" in request.headers:
        return False
    remote = request.remote
    if remote is None:
        return True
    try:
        return ipaddress.ip_address(remote).is_loopback
    except ValueError:
        return False


def notebook_path_error(
    context: LibraryContext, path: Path, *, loopback: bool, writing: bool
) -> str | None:
    """FORMAT.md §2 violation message for a notebook *path*, else None."""
    if writing and path.suffix.lower() != ".md":
        return f"notebook files must end in .md (got {path.name!r}) — FORMAT.md §2"
    if not loopback:
        try:
            inside = path.resolve().is_relative_to(context.library_dir().resolve())
        except OSError:
            inside = False
        if not inside:
            return (
                "remote (non-loopback) requests may only touch paths inside the "
                "library folder — FORMAT.md §2"
            )
    return None


def error_response(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status)


# --------------------------------------------------- fs/list: ROOTS & drives
#
# 2026-07-19 fix: the §7.2 picker could reach the top of C:\ but no further
# — drive roots reported `parent: null`, which reads as "nothing above
# here" and traps the user instead of offering a way to another drive or a
# NAS/UNC path. The pieces below are their own functions (rather than
# inlined in the handler) specifically so tests can monkeypatch the
# Windows-only bits from macOS/Linux CI — real drive enumeration and real
# `os.name` are both unavailable there.
#
# 2026-07-26 fix: on a Linux ComfyUI host, `_platform_root_entries` fell
# through to `_list_macos_volumes`, which only ever looks at `/Volumes` — a
# path that doesn't exist on Linux — so the ROOTS listing silently degraded
# to just the library dir + Home, with no way to browse to `/mnt`, a NAS
# mount, or anything else outside those two (owner report: "If I try and
# browse to a drive I can't go deeper than the root ... so I can't connect
# to the nas"). `_is_linux` is the same kind of seam as `_is_windows` below,
# for the same reason: real Linux mounts aren't available on this (macOS)
# dev/CI machine either.


def _is_windows() -> bool:
    """True on a real Windows host (FORMAT.md §5's drive-letter world).

    A seam, not a bare ``os.name`` check inlined in the handler, so tests
    can monkeypatch it to exercise the Windows branches (drive enumeration,
    drive-root → "ROOTS" parent) without depending on the dev/CI machine's
    own platform.
    """
    return os.name == "nt"


def _is_linux() -> bool:
    """True on a Linux host (this fix's conventional-mount-parent world).

    A seam like :func:`_is_windows`, so tests can exercise the Linux
    ROOTS branch (:func:`_list_linux_mount_roots`) from macOS/CI without a
    real Linux host. Checked via ``sys.platform`` rather than ``os.name`` --
    ``os.name`` is just ``"posix"`` for both macOS and Linux, so it can't
    tell them apart; ``sys.platform`` is the one stdlib signal that can.
    """
    return sys.platform.startswith("linux")


def _list_windows_drives() -> list[str]:
    """Every existing drive's root, e.g. ``["C:\\\\", "D:\\\\"]``.

    Backs the ``dir="ROOTS"`` sentinel's platform tail on Windows
    (:func:`_platform_root_entries`). Probes A-Z with ``Path.exists()`` —
    factored out to its own function so tests replace it wholesale instead of
    needing real drives to exist.
    """
    return [
        f"{letter}:\\" for letter in string.ascii_uppercase if Path(f"{letter}:\\").exists()
    ]


def _list_macos_volumes() -> list[str]:
    """Every mounted volume under ``/Volumes`` on a macOS host (also the
    fallback for any other non-Linux POSIX host, e.g. a BSD -- Linux has its
    own dedicated :func:`_list_linux_mount_roots`, since `/Volumes` never
    exists there).

    Backs the ``dir="ROOTS"`` sentinel's platform tail on POSIX
    (:func:`_platform_root_entries`) -- ``/Volumes`` always contains at least
    a symlink back to the boot volume (e.g. ``Macintosh HD``) plus one entry
    per externally-mounted disk/network share, exactly the set a user reaches
    for when they mean "browse by volume" the way Finder's own sidebar does.
    Hidden entries are skipped (same convention the directory-listing branch
    of ``get_fs_list`` uses) and a stat failure on any one entry is skipped
    rather than aborting the whole root listing. Factored out to its own
    function, like :func:`_list_windows_drives`, so tests replace it wholesale.
    """
    volumes_dir = Path("/Volumes")
    if not volumes_dir.is_dir():
        return []
    try:
        entries = sorted(volumes_dir.iterdir(), key=lambda p: p.name.casefold())
    except OSError:
        return []
    volumes = []
    for entry in entries:
        if entry.name.startswith("."):
            continue
        try:
            is_dir = entry.is_dir()
        except OSError:
            continue
        if is_dir:
            volumes.append(str(entry))
    return volumes


#: Static conventional Linux mount parents that come BEFORE the per-user
#: candidates in this fix's documented order: the filesystem root, the
#: traditional `/mnt`, and the modern desktop-automount `/media` PARENT dir
#: itself (distinct from `/media/<user>`, computed below). Labeled per this
#: fix's spec: "Filesystem root" for `/` (it has no meaningful bare name),
#: the path's own name otherwise (matches `_list_macos_volumes`'s
#: bare-volume-name convention).
_LINUX_MOUNT_PARENTS_BEFORE_USER: tuple[tuple[str, str], ...] = (
    ("Filesystem root", "/"),
    ("mnt", "/mnt"),
    ("media", "/media"),
)

#: `/srv` (network-share convention) comes AFTER the per-user candidates and
#: BEFORE GVFS in the documented order -- kept as its own constant (not
#: folded into :data:`_LINUX_MOUNT_PARENTS_BEFORE_USER`) specifically so
#: that ordering is structural rather than something a future edit could
#: silently reshuffle.
_LINUX_SRV_MOUNT_PARENT: tuple[str, str] = ("srv", "/srv")


def _safe_current_username() -> str | None:
    """Best-effort current username, or None -- never raises.

    Used only to build the per-user Linux mount-parent candidates
    (``/media/<user>``, ``/run/media/<user>``). Reads the home directory's
    own leaf name (the same source :data:`_FS_LIST_HOME_LABEL`'s path
    already relies on) rather than :func:`os.getlogin`/the ``pwd`` module --
    both can raise in contexts with no controlling terminal or password-db
    entry (services, containers), exactly the kind of failure this whole
    fix must degrade gracefully from (skip the candidate, don't 500).
    """
    try:
        name = Path.home().name
    except RuntimeError:
        return None
    return name or None


def _safe_current_uid() -> int | None:
    """Best-effort current numeric uid, or None -- never raises.

    Used only to build the GVFS mount-parent candidate
    (``/run/user/<uid>/gvfs``). ``os.getuid`` only exists on POSIX, but this
    is only ever called from the Linux branch (:func:`_is_linux` gates
    :func:`_list_linux_mount_roots`), which is only ever True on a real
    POSIX host.
    """
    try:
        return os.getuid()
    except AttributeError:
        return None


def _linux_mount_root_candidates() -> list[tuple[str, str]]:
    """Every candidate Linux mount-parent, as ``(label, path)``, UNFILTERED
    -- existence/readability is checked later, by
    :func:`_list_linux_mount_roots`.

    Factored out to its own function, like :func:`_list_macos_volumes`/
    :func:`_list_windows_drives`, so tests replace it WHOLESALE with a fake
    candidate set instead of depending on the test machine's real mounts --
    the only POSIX-y filesystem feature every dev/CI box (including macOS)
    is guaranteed to have is `/` itself; `/mnt`, the per-user media dirs, and
    GVFS are real only on an actual Linux host.

    Assembled in this fix's documented order: :data:`_LINUX_MOUNT_PARENTS_BEFORE_USER`
    (``/``, ``/mnt``, ``/media``), then two computed per-user families --
    the auto-mount dirs both desktop conventions use (``/media/<user>``,
    the newer ``/run/media/<user>``) -- then :data:`_LINUX_SRV_MOUNT_PARENT`
    (``/srv``), then the GVFS directory (``/run/user/<uid>/gvfs``) -- where a
    desktop file manager's mounted network share (``smb://``, ``ftp://``,
    ...) actually lives on disk once mounted. That last one is almost
    certainly the owner's actual NAS case (2026-07-26 report): typing the
    `smb://` address itself (correctly) doesn't work as a filesystem path
    (:func:`_uri_style_path_error`), but browsing to its GVFS mountpoint
    after mounting it from a file manager would. The username/uid lookups
    are best-effort (:func:`_safe_current_username`/:func:`_safe_current_uid`):
    either being unavailable just drops its candidate(s) WITHOUT disturbing
    the position of any other candidate, same as any other
    unreadable/nonexistent candidate below -- never raises.
    """
    candidates = list(_LINUX_MOUNT_PARENTS_BEFORE_USER)

    username = _safe_current_username()
    if username:
        candidates.append(("Removable media", f"/media/{username}"))
        candidates.append(("Removable media (run)", f"/run/media/{username}"))

    candidates.append(_LINUX_SRV_MOUNT_PARENT)

    uid = _safe_current_uid()
    if uid is not None:
        candidates.append(("Network shares (GVFS)", f"/run/user/{uid}/gvfs"))

    return candidates


def _is_listable_dir(path: Path) -> bool:
    """True when THIS SERVER PROCESS can actually list *path*.

    2026-07-26, owner report verbatim: ``could not list /root: [Errno 13]
    Permission denied: '/root'``. ``Path.is_dir()`` alone is not enough --
    ``/root`` exists and IS a directory, it just isn't readable by a non-root
    process, so every root entry built on ``is_dir()`` could still be a
    guaranteed 400. Read+execute is what listing a directory actually needs.

    Uses :func:`os.access` (a single syscall) rather than probing with
    ``iterdir()``: a dead or unreachable network mount would make a probe
    hang, and this runs while assembling the ROOTS listing.
    """
    try:
        return path.is_dir() and os.access(path, os.R_OK | os.X_OK)
    except OSError:
        return False


#: Pseudo/virtual filesystems that are never a place a user keeps files, so
#: they are filtered out of the mount listing (:func:`_list_mounted_filesystems`).
_MOUNT_SKIP_FSTYPES = frozenset(
    {
        "autofs", "binfmt_misc", "bpf", "cgroup", "cgroup2", "configfs",
        "debugfs", "devpts", "devtmpfs", "efivarfs", "fusectl", "hugetlbfs",
        "mqueue", "nsfs", "overlay", "pstore", "proc", "ramfs", "rpc_pipefs",
        "securityfs", "selinuxfs", "squashfs", "sysfs", "tmpfs", "tracefs",
    }
)

#: Mount points under these are machinery, not user storage (snap packages,
#: container layers, the EFI partition, runtime dirs) -- filtered out with
#: their whole subtrees. ``/run`` is deliberately NOT here: GVFS mounts a
#: user's network shares under ``/run/user/<uid>/gvfs``.
_MOUNT_SKIP_PREFIXES = (
    "/proc", "/sys", "/dev", "/boot/efi", "/snap", "/var/snap",
    "/var/lib/docker", "/var/lib/snapd", "/run/lock", "/run/credentials",
)

#: Filesystem types that mean "this is a network share" -- surfaced FIRST in
#: the listing, since a NAS is what a user browsing for one is looking for.
_MOUNT_NETWORK_FSTYPES = frozenset(
    {
        "afpfs", "cifs", "davfs", "davfs2", "fuse.davfs2", "fuse.gvfsd-fuse",
        "fuse.sshfs", "fuse.smbnetfs", "nfs", "nfs4", "smb3", "smbfs",
    }
)

#: A GVFS mount point's basename encodes the share, e.g.
#: ``smb-share:server=dxp4800pro-ring.local,share=personal_folder``.
_GVFS_FIELD_RE = re.compile(r"(server|share)=([^,]+)")

#: ``/proc/mounts`` octal escapes (the kernel escapes these in mount paths).
_MOUNT_ESCAPES = (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\"))


def _unescape_mount_path(raw: str) -> str:
    """Undo the kernel's octal escaping of a mount path (a share called
    ``My Files`` appears as ``My\\040Files``)."""
    for escape, plain in _MOUNT_ESCAPES:
        raw = raw.replace(escape, plain)
    return raw


def _read_mount_table() -> list[tuple[str, str]]:
    """``(mountpoint, fstype)`` for every filesystem the OS reports mounted.

    The test seam for :func:`_list_mounted_filesystems` (replaced wholesale,
    like :func:`_list_windows_drives`) -- a dev/CI box's real mounts are not
    something tests can depend on.

    Linux exposes this at ``/proc/self/mounts`` (the caller's namespace view,
    which is the correct one inside a container) with ``/proc/mounts`` and
    ``/etc/mtab`` as fallbacks. Other platforms have no such file, so this
    returns ``[]`` there and the mount listing simply contributes nothing --
    macOS already surfaces its mounts through ``/Volumes``.
    """
    for candidate in ("/proc/self/mounts", "/proc/mounts", "/etc/mtab"):
        try:
            text = Path(candidate).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rows: list[tuple[str, str]] = []
        for line in text.splitlines():
            fields = line.split()
            if len(fields) < 3:
                continue
            rows.append((_unescape_mount_path(fields[1]), fields[2]))
        return rows
    return []


def _mount_label(mountpoint: str, fstype: str) -> str:
    """A human label for a mounted filesystem.

    A GVFS share's own mount-point name is machine-readable rather than
    readable (``smb-share:server=HOST,share=NAME``), so it is rewritten as
    ``NAME on HOST``. Everything else is ``<folder name> (<fstype>)``, with
    network types collapsed to the word "network" -- ``nas (network)`` says
    more to a non-developer than ``nas (cifs)``.
    """
    name = mountpoint.rstrip("/").rpartition("/")[2] or mountpoint
    fields = dict(match.groups() for match in _GVFS_FIELD_RE.finditer(name))  # type: ignore[misc]
    share = fields.get("share")
    if share:
        server = fields.get("server")
        return f"{share} on {server}" if server else share
    kind = "network" if fstype in _MOUNT_NETWORK_FSTYPES else fstype
    return f"{name} ({kind})"


def _list_mounted_filesystems() -> list[tuple[str, str]]:
    """``(label, path)`` for every mount worth offering, network shares first.

    **This is the answer to "how do I get to my shared drives?" (owner,
    2026-07-26).** Guessing at the conventional mount PARENTS (``/mnt``,
    ``/media``, ...) only helps when a share happens to live in one of them;
    it does not help a share mounted at ``/srv/photos``, at
    ``/run/user/1000/gvfs/smb-share:...`` by a file manager, or anywhere else
    an admin chose. Reading the OS's own mount table means ANY filesystem the
    machine already has mounted shows up by itself, wherever it is and
    however it got there -- the user never has to know or type a path.

    Filtered: pseudo filesystems (:data:`_MOUNT_SKIP_FSTYPES`), machinery
    subtrees (:data:`_MOUNT_SKIP_PREFIXES`), ``/`` itself (already offered as
    "Filesystem root"), and anything this process cannot actually list
    (:func:`_is_listable_dir`). Never raises: a table that can't be read
    contributes nothing.
    """
    seen: set[str] = set()
    network: list[tuple[str, str]] = []
    local: list[tuple[str, str]] = []
    for mountpoint, fstype in _read_mount_table():
        if not mountpoint.startswith("/") or mountpoint == "/":
            continue
        if fstype in _MOUNT_SKIP_FSTYPES:
            continue
        if any(
            mountpoint == prefix or mountpoint.startswith(prefix + "/")
            for prefix in _MOUNT_SKIP_PREFIXES
        ):
            continue
        if mountpoint in seen:
            continue
        if not _is_listable_dir(Path(mountpoint)):
            continue
        seen.add(mountpoint)
        entry = (_mount_label(mountpoint, fstype), mountpoint)
        (network if fstype in _MOUNT_NETWORK_FSTYPES else local).append(entry)
    network.sort(key=lambda pair: pair[0].casefold())
    local.sort(key=lambda pair: pair[0].casefold())
    return network + local


def _list_linux_mount_roots() -> list[tuple[str, str]]:
    """Every :func:`_linux_mount_root_candidates` entry that actually exists
    (and is a readable directory) on this host right now, as ``(label,
    path)``, in the documented order.

    Backs the ``dir="ROOTS"`` sentinel's platform tail on Linux
    (:func:`_platform_root_entries`). A stat/permission failure on any one
    candidate is skipped -- exactly :func:`_list_macos_volumes`'s per-entry
    ``except OSError`` -- one bad candidate must never abort the whole ROOTS
    listing.

    2026-07-26 (later, same owner round): filters on
    :func:`_is_listable_dir`, not bare ``is_dir()`` -- an existing but
    unreadable directory (his ``/root``) is a guaranteed 400, so it must not
    be offered.
    """
    return [
        (label, raw)
        for label, raw in _linux_mount_root_candidates()
        if _is_listable_dir(Path(raw))
    ]


def _fs_entry(name: str, path: Path) -> dict[str, str]:
    """A labeled, directly-navigable ROOTS entry: ``{"name", "path"}``.

    STANDARD-fs-browse.md's general contract is names-only (the client joins
    ``dir``+``sep``+``name`` for a REAL directory listing), but a ROOTS entry
    (this pack's default library dir, "Home", a `/Volumes` mount, a Windows
    drive) has no single parent directory to join against -- each one is
    independently rooted, so the server hands back its actual absolute path
    directly. A deliberate, documented, additive extension of the base
    schema: any consumer that only reads ``name`` still gets a sensible label.
    """
    return {"name": name, "path": str(path)}


def _platform_root_entries(windows: bool) -> list[dict[str, str]]:
    """STANDARD-fs-browse.md ROOTS listing's platform-specific tail.

    Every existing drive letter on Windows (:func:`_list_windows_drives`,
    labeled by its short drive-letter form, e.g. ``"C:"``); every mounted
    ``/Volumes`` entry on macOS/other non-Linux POSIX
    (:func:`_list_macos_volumes`, labeled by its bare volume name, e.g.
    ``"Macintosh HD"``); or, on Linux (:func:`_is_linux`, 2026-07-26 fix),
    every conventional mount parent that actually exists on this host
    (:func:`_list_linux_mount_roots`, labeled per
    :func:`_linux_mount_root_candidates`) -- `/Volumes` never exists on
    Linux, so before this fix `_list_macos_volumes` always returned `[]`
    there and the picker had no way to reach `/mnt`, a NAS mount, or
    anything outside the library dir/Home (owner report 2026-07-26).
    """
    if windows:
        return [_fs_entry(raw.rstrip("\\"), Path(raw)) for raw in _list_windows_drives()]
    if _is_linux():
        # 2026-07-26 (owner: "How do I get to my shared drives? Mounting
        # can't be the answer - that's not something I can ask every user to
        # do."): the OS's own mount table comes FIRST, so any share the
        # machine already has mounted -- wherever it lives -- is one click
        # away without the user knowing a path. The conventional parents
        # (`/`, /mnt, /media, ...) follow as a fallback for browsing to
        # somewhere not yet mounted. _dedupe_root_entries drops the overlap.
        entries = [
            _fs_entry(label, Path(raw)) for label, raw in _list_mounted_filesystems()
        ]
        entries.extend(
            _fs_entry(label, Path(raw)) for label, raw in _list_linux_mount_roots()
        )
        return entries
    return [_fs_entry(Path(raw).name, Path(raw)) for raw in _list_macos_volumes()]


def _dedupe_root_entries(entries: list[dict[str, str]]) -> list[dict[str, str]]:
    """Drop any ROOTS *entries* whose ``path`` repeats an earlier one,
    keeping the FIRST occurrence -- preserves the documented order (default
    dir, Home, then platform tail).

    2026-07-26: needed once the platform tail could itself contain more than
    one path that might coincide with another entry -- e.g. Linux's
    ``/media/<user>`` and ``/media`` are both real, separate entries (no
    dedup between those two -- both are legitimately useful), but a
    library_dir or Home that happens to sit exactly on one of the platform
    tail's own paths must not be listed twice under two labels.
    """
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for entry in entries:
        path = entry["path"]
        if path in seen:
            continue
        seen.add(path)
        deduped.append(entry)
    return deduped


def _fs_list_roots(context: LibraryContext, *, windows: bool) -> list[dict[str, str]]:
    """The top-level entries for ``dir="ROOTS"`` (STANDARD-fs-browse.md).

    Always: this pack's own default fs/list directory (labeled
    :data:`_FS_LIST_DEFAULT_DIR_LABEL`) and the user's home directory, then
    :func:`_platform_root_entries`'s platform-specific tail -- the standard's
    exact ROOTS ordering ("the pack's default dir first (labeled) ... 'Home',
    then platform roots"). 2026-07-19: previously POSIX's ``ROOTS`` resolved
    straight to a real listing of ``/``; this labeled-roots shape (already
    used on Windows) now applies uniformly on every platform. The assembled
    list is de-duplicated by resolved path (:func:`_dedupe_root_entries`,
    2026-07-26) before it goes out, so an accidental path collision between
    the default dir/Home and a platform-tail entry never surfaces twice.

    2026-07-26: Home (and every platform-tail entry) is now dropped when the
    SERVER can't actually list it -- owner report, verbatim: ``could not list
    /root: [Errno 13] Permission denied: '/root'``. His ComfyUI runs with
    ``HOME=/root`` while NOT being the root user, so the "Home" entry was a
    guaranteed dead end: visible, clickable, and a 400 every time. Offering a
    root we already know will fail is worse than omitting it. The library
    folder is exempt from that check -- it is this pack's own default, it is
    created on demand by ``library_dir()``, and Settings has its own separate
    warning for an unreachable one, so hiding it would only be confusing.
    """
    roots = [_fs_entry(_FS_LIST_DEFAULT_DIR_LABEL, context.library_dir().resolve())]
    home = Path.home().resolve()
    if _is_listable_dir(home):
        roots.append(_fs_entry(_FS_LIST_HOME_LABEL, home))
    else:
        logger.info(
            "EPSNodes: fs/list ROOTS is omitting Home (%s) -- this server "
            "process cannot list it, so offering it would only 400",
            home,
        )
    roots.extend(_platform_root_entries(windows))
    return _dedupe_root_entries(roots)


def _parse_extensions(raw: str) -> tuple[str, ...]:
    """STANDARD-fs-browse.md's `ext` query param: a comma-separated,
    case-insensitive extension filter.

    Entries may be given with or without the leading dot. An empty/blank
    value means "the default allowlist" (:data:`DEFAULT_EXTENSIONS`, just
    ``.md`` -- this is a Prompt Notebook file picker, not a general file
    browser) -- mirrors cprb's identical ``_parse_extensions`` helper (same
    contract, independently implemented per pack per STANDARD-fs-browse.md).
    """
    parts = [part.strip().lower() for part in (raw or "").split(",")]
    cleaned = tuple(f".{p.lstrip('.')}" for p in parts if p.strip(". "))
    return cleaned or DEFAULT_EXTENSIONS


def _is_unc_share_root(directory: Path) -> bool:
    """True when *directory* is a UNC share root (``\\\\server\\share``).

    Re-parsed with ``PureWindowsPath`` rather than trusting the ambient
    ``Path`` flavor, so this is exercisable on any platform: a share root's
    "drive" is the ``\\\\server\\share`` string itself (no drive LETTER),
    which is exactly what distinguishes it from a real drive root like
    ``C:\\`` — the two need different ``parent`` answers (see below).
    """
    drive = PureWindowsPath(str(directory)).drive
    return bool(drive) and not (len(drive) == 2 and drive[1] == ":")


def _fs_root_parent(directory: Path, *, windows: bool) -> str | None:
    """FORMAT.md §5 ``parent`` for a *directory* that IS a filesystem root.

    - Windows drive root (``C:\\``): climbs to the drive list (``"ROOTS"``)
      — the fix. Today this reported ``null`` and trapped the user.
    - UNC share root (``\\\\server\\share``): reports ``null`` even on
      Windows — there is no portable way to enumerate a server's other
      shares, so there is nothing to climb to.
    - POSIX root (``/``): climbs to ``"ROOTS"`` too (2026-07-26 fix).

    **2026-07-26 (owner: "I can no longer access root on linux when I
    browse").** POSIX ``/`` used to report ``null`` here, with the reasoning
    "it has no sibling to climb to". That was true only while ``ROOTS`` on
    POSIX resolved straight to a listing of ``/``. Since 2026-07-19 ``ROOTS``
    is a LABELED LIST (library folder, Home, volumes/mounts), so ``/`` very
    much does have somewhere to climb back to — and reporting ``null`` left
    the picker with no way out of ``/`` once you entered it: the identical
    dead end that was fixed for Windows drive roots in the same July round,
    just never applied to POSIX.
    """
    if windows and _is_unc_share_root(directory):
        return None
    return ROOTS


def _uri_style_path_error(raw: str) -> str:
    """FORMAT.md §5 400 message for a ``dir`` value that's a URI
    (``scheme://...``) rather than a filesystem path.

    2026-07-26 owner report: typing an ``smb://`` address (e.g.
    ``smb://dxp4800pro-ring.local/personal_folder/docs/loras.md``) into the
    picker's manual-path field "doesn't work" -- correctly, since that's a
    GIO/GVFS address (the kind a GUI file manager's address bar accepts),
    not something Python's ``open``/``Path`` can ever resolve -- but the
    generic "dir must be an absolute path" 400 doesn't explain WHY a value
    that reads like a location fails for a non-developer. Names the
    caller's own scheme back at them (so this reads
    right for ``smb://``, ``nfs://``, ``afp://``, ... -- the check itself is
    scheme-agnostic, see the caller) while keeping one concrete, illustrative
    mount point: the GVFS path (:func:`_linux_mount_root_candidates`) is
    where a desktop file manager's mounted network share actually ends up on
    disk, and by far the most likely NAS-from-Linux-desktop case.
    """
    scheme = raw.split("://", 1)[0] + "://"
    return (
        f"{scheme} is a network address, not a file path. Mount the share "
        "first, then browse to its mount point (commonly /mnt/... , "
        "/media/<you>/... , or /run/user/<uid>/gvfs/smb-share:server=...)."
    )


# --------------------------------------------------- library_dir diagnosis
#
# 2026-07-19 fix (owner report: a NAS-backed library_dir the server machine
# can't resolve was invisible until a node errored). `_diagnose_library_dir`
# is its own function -- not inlined in `get_config` -- specifically so tests
# can exercise both OS-mismatch directions from a single (macOS) dev/CI
# machine via the `is_windows` parameter, the same seam `_is_windows()`
# already provides for the fs/list ROOTS branches above.


def _looks_like_windows_path(raw: str) -> bool:
    """True for a drive-letter (``C:\\``) or UNC (``\\\\server\\share``)
    shape -- the signal that *raw* was set from a Windows machine's
    perspective, regardless of what OS is asking."""
    return bool(re.match(r"^[A-Za-z]:[\\/]", raw)) or raw.startswith("\\\\")


def _looks_like_posix_path(raw: str) -> bool:
    """True for a macOS/Linux mount-style shape (``/Volumes/...``,
    ``/mnt/...``) -- the signal that *raw* was set from a POSIX machine's
    perspective, regardless of what OS is asking."""
    normalized = raw.replace("\\", "/").lower()
    return normalized.startswith("/volumes/") or normalized.startswith("/mnt/")


def _diagnose_library_dir(raw: str, *, is_windows: bool) -> str:
    """FORMAT.md §5's ``library_dir_note``: a one-line human diagnosis for a
    configured ``library_dir`` value *raw* that the caller has already
    confirmed doesn't resolve on this server. *is_windows* is the server's
    own platform (:func:`_is_windows`, passed in rather than read here so
    tests can exercise both directions from any host OS).

    An OS-shape mismatch is checked first: it names the likely mistake
    directly ("set from the wrong machine") and is a stronger, more
    actionable signal than the generic unreachable-path message, so it wins
    when *raw* happens to look foreign-shaped. Otherwise the path is shaped
    fine for this OS but just isn't reachable right now (the owner's actual
    2026-07-19 case: an unmounted NAS share).
    """
    if is_windows and _looks_like_posix_path(raw):
        return _OS_MISMATCH_NOTE.format(other="macOS/Linux", this="Windows")
    if not is_windows and _looks_like_windows_path(raw):
        return _OS_MISMATCH_NOTE.format(other="Windows", this="macOS/Linux")
    return _UNREACHABLE_NOTE


def _check_library_dir(context: LibraryContext) -> tuple[bool, str]:
    """FORMAT.md §5 ``(library_dir_exists, library_dir_note)`` for THIS
    request, computed WITHOUT the side effect of creating anything.

    Reads the raw configured value straight from :meth:`LibraryContext.
    load_config` rather than calling :meth:`LibraryContext.library_dir`,
    which unconditionally ``mkdir``s -- exactly the wrong thing to do here:
    that call is *why* a bad NAS path used to surface only as a crash deep
    in some other route/node instead of a clean diagnosis (owner report
    2026-07-19). An unconfigured library_dir (the pack's own default,
    under ComfyUI's own user dir) is always trivially fine -- there is
    nothing to diagnose, so this only inspects a value the user actually
    set.
    """
    configured = context.load_config().get("library_dir")
    if not configured:
        return True, ""
    try:
        exists = Path(configured).is_dir()
    except OSError:
        exists = False
    if exists:
        return True, ""
    return False, _diagnose_library_dir(configured, is_windows=_is_windows())


# ---------------------------------------------------------------- core routes

def register_core(context: LibraryContext, routes: web.RouteTableDef) -> None:
    """Attach the §5 core rows (version/config/loras) to *routes*."""

    @routes.get("/lora_library/version")
    async def get_version(_request: web.Request) -> web.Response:
        return web.json_response({"version": __version__})

    @routes.get("/lora_library/config")
    async def get_config(request: web.Request) -> web.Response:
        configured_raw = context.load_config().get("library_dir")
        configured = bool(configured_raw)
        library_dir_exists, library_dir_note = _check_library_dir(context)
        # 2026-07-19: when unreachable (unmounted NAS, wrong-OS shape),
        # report the raw configured value as-is rather than calling
        # `context.library_dir()` — its `mkdir` would take this whole route
        # down; `library_dir_exists`/`library_dir_note` explain why it's
        # stale (FORMAT.md §5/§7.3). The common (working) case still
        # resolves through the real method, matching prior behavior.
        library_dir = str(context.library_dir()) if library_dir_exists else configured_raw
        return web.json_response(
            {
                "library_dir": library_dir,
                "default_library_dir": str(context.default_library_dir),
                "configured": configured,
                # §2 verdict for THIS caller — lets the frontend gate the
                # host-machine-only affordances (§7.2 file panel buttons).
                "is_local": request_is_loopback(request),
                "library_dir_exists": library_dir_exists,
                "library_dir_note": library_dir_note,
            }
        )

    @routes.get("/lora_library/fs/list")
    async def get_fs_list(request: web.Request) -> web.Response:
        """STANDARD-fs-browse.md's shared cross-plugin contract. Gated by
        :data:`FS_LIST_LOCAL_ONLY` (``True`` for epsnodes -- FORMAT.md §5: a
        remote browser defers to the host for file selection, so it never
        needs to walk the server's filesystem). The loopback check runs
        before ROOTS/absolute-path handling below: ROOTS is an exception to
        the absolute-path requirement, never to this one.

        Query params:
            dir: Optional. Empty/omitted resolves to this pack's own default
                directory (``context.library_dir()``); the literal ``"ROOTS"``
                (:data:`ROOTS`) returns the virtual top-level listing
                (:func:`_fs_list_roots`). Any other value MUST be an absolute
                path naming an existing, listable directory.
            ext: Optional, comma-separated, case-insensitive
                (:func:`_parse_extensions`) -- defaults to
                :data:`DEFAULT_EXTENSIONS` (``.md``).

        Returns 200 with ``{"dir", "parent", "sep", "dirs", "files",
        "truncated"}`` (STANDARD-fs-browse.md) -- names-only ``dirs``/
        ``files`` entries for a real directory listing (the client joins
        with ``dir``+``sep``); ROOTS entries additionally carry ``path``
        (:func:`_fs_entry`). 403 when :data:`FS_LIST_LOCAL_ONLY` and the
        caller isn't loopback; 400 for a relative/non-existent/non-directory
        ``dir``, or one that's a URI (``scheme://...``, e.g. a typed
        ``smb://`` address -- :func:`_uri_style_path_error`, 2026-07-26 fix)
        rather than an actual filesystem path.
        """
        if FS_LIST_LOCAL_ONLY and not request_is_loopback(request):
            return error_response(403, "file browsing is host-machine-only — FORMAT.md §5")
        raw = (request.query.get("dir") or "").strip()
        windows = _is_windows()
        if raw == ROOTS:
            return web.json_response(
                {
                    "dir": ROOTS,
                    "parent": None,
                    "sep": os.sep,
                    "dirs": _fs_list_roots(context, windows=windows),
                    "files": [],
                    "truncated": False,
                }
            )
        # 2026-07-26 fix: a typed `scheme://...` value (owner's actual case:
        # `smb://dxp4800pro-ring.local/...`) is never a valid absolute path on
        # ANY OS, but the generic "must be an absolute path" 400 below doesn't
        # say why -- catch it first with a message that names the mistake in
        # plain language (:func:`_uri_style_path_error`).
        if "://" in raw:
            return error_response(400, _uri_style_path_error(raw))
        directory = Path(raw) if raw else context.library_dir()
        if not directory.is_absolute():
            return error_response(400, f"dir must be an absolute path (got {raw!r})")
        extensions = _parse_extensions(request.query.get("ext", ""))
        try:
            entries = sorted(directory.iterdir(), key=lambda p: p.name.casefold())
        except OSError as exc:
            return error_response(400, f"could not list {directory}: {exc}")

        dirs: list[dict[str, str]] = []
        files: list[dict[str, object]] = []
        count = 0
        truncated = False
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                is_dir = entry.is_dir()
            except OSError:
                continue
            if is_dir:
                if count >= _FS_LIST_MAX_ENTRIES:
                    truncated = True
                    break
                dirs.append({"name": entry.name})
                count += 1
                continue
            if entry.suffix.lower() not in extensions:
                continue
            try:
                stat_result = entry.stat()
            except OSError:
                continue
            if count >= _FS_LIST_MAX_ENTRIES:
                truncated = True
                break
            files.append(
                {"name": entry.name, "size": stat_result.st_size, "mtime": stat_result.st_mtime}
            )
            count += 1

        at_root = directory.parent == directory
        parent = _fs_root_parent(directory, windows=windows) if at_root else str(directory.parent)
        return web.json_response(
            {
                "dir": str(directory),
                "parent": parent,
                "sep": os.sep,
                "dirs": dirs,
                "files": files,
                "truncated": truncated,
            }
        )

    @routes.post("/lora_library/config")
    async def post_config(request: web.Request) -> web.Response:
        # Changing library_dir moves the very boundary §2 enforces for
        # remote callers, so only the local machine may change it.
        if not request_is_loopback(request):
            return error_response(403, "library folder can only be changed locally — FORMAT.md §2")
        try:
            body = await request.json()
        except Exception:
            return error_response(400, "body must be JSON")
        raw = str(body.get("library_dir") or "").strip()
        config = context.load_config()
        if not raw:
            config.pop("library_dir", None)
            context.save_config(config)
            return web.json_response({"ok": True, "library_dir": str(context.library_dir())})
        path = Path(raw)
        if not path.is_absolute():
            return error_response(400, f"library folder must be an absolute path (got {raw!r})")
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".lora_library_write_probe"
            probe.write_text("", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            return error_response(400, f"library folder is not writable: {exc}")
        config["library_dir"] = str(path)
        context.save_config(config)
        return web.json_response({"ok": True, "library_dir": str(path)})

    @routes.get("/lora_library/loras")
    async def get_loras(_request: web.Request) -> web.Response:
        return web.json_response({"loras": context.list_loras()})


def _register_all(context: LibraryContext, routes: web.RouteTableDef) -> None:
    """Core + every feature route module onto *routes*. Feature modules are
    optional (same defensive posture as ``__init__.py``): a missing/broken
    one logs and is skipped."""
    register_core(context, routes)
    for module_name in ("routes_notebook", "routes_sets"):
        try:
            module = __import__(f"{__package__}.{module_name}", fromlist=["register"])
            module.register(context, routes)
        except Exception:
            logger.exception("EPSNodes: route module %s failed to load", module_name)


def build_routes(context: LibraryContext) -> web.RouteTableDef:
    """Every lora_library route on a fresh table — the tests' entry point."""
    routes = web.RouteTableDef()
    _register_all(context, routes)
    return routes


def register(context: LibraryContext) -> None:
    """Attach all routes to the running ComfyUI server (called from
    ``__init__.py``; only function in the pack that touches PromptServer).

    Registers onto ``PromptServer.instance.routes`` — NOT directly onto the
    aiohttp app — because ComfyUI mirrors exactly that table under the
    ``/api`` prefix at startup (server.py: "Prefix every route with /api"),
    and the frontend's ``api.fetchApi`` calls ``/api/lora_library/...``.
    Direct ``app.add_routes`` registration works for curl but is invisible
    to the frontend (learned the hard way: HTTP 405s from settings.js).
    """
    from server import PromptServer  # ComfyUI's module; import only inside ComfyUI

    _register_all(context, PromptServer.instance.routes)
    logger.info("EPSNodes: routes registered")
