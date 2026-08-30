"""Dependency-injection seam: everything ComfyUI-specific enters through here.

The rest of ``lora_library/`` (stores, nodes, routes) receives a
:class:`LibraryContext` and never imports ComfyUI modules itself, so the whole
package stays importable — and therefore testable — without ComfyUI. The real
context is built exactly once, in the pack's ``__init__.py``; tests build fake
ones over ``tmp_path`` (see ``tests/conftest.py``). Same pattern as
comfyui-photoshop-bridge's ``cpsb/context.py``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

logger = logging.getLogger("lora_library")

CONFIG_FILENAME = "config.json"
DEFAULT_NOTEBOOK_FILENAME = "loras.md"
SETS_DIRNAME = "sets"

#: How long :meth:`LibraryContext.library_dir` / :meth:`LibraryContext.sets_dir`
#: trust a directory they already created/verified before issuing another
#: ``mkdir`` (NAS round 2026-08-22). Every call used to ``mkdir(parents=True,
#: exist_ok=True)`` -- one network round trip per request on a NAS library,
#: and several per request for the routes that resolve the folder more than
#: once (``list_sets`` resolves it once per set file). Thirty seconds is
#: long enough to collapse a tab-switch burst (every node re-fetching at
#: once) into ONE syscall and short enough that a library folder deleted or
#: unmounted underneath a running server is noticed promptly; ``save_config``
#: and the stores' directory-level ``FileNotFoundError`` paths forget the
#: cache outright (:meth:`LibraryContext.forget_ensured_dirs`).
ENSURED_DIR_TTL_S = 30.0

#: True on a real Windows host. A module-level CONSTANT, not a bare
#: ``os.name`` check inlined at each call site, so tests can monkeypatch it
#: (``monkeypatch.setattr(context, "_IS_WINDOWS", True)``) to exercise the
#: opposite platform's branch of :func:`is_foreign_absolute` (and everything
#: built on it) without a real Windows/POSIX host. Same seam shape as
#: ``lora_library/routes.py``'s ``_is_windows()`` function seam, but a
#: constant here since every reader just needs the current verdict, never a
#: callable.
_IS_WINDOWS: bool = os.name == "nt"


def is_windows() -> bool:
    """Public read of :data:`_IS_WINDOWS`, for a caller OUTSIDE this module
    that needs to build its own message off the SAME platform verdict
    :func:`is_foreign_absolute` used -- e.g. ``lora_library/routes.py``'s
    ``_foreign_absolute_path_error``, which names "the other platform" for
    a value THIS module already flagged. Deliberately not routes.py's own,
    separate ``_is_windows()`` function seam: the two happen to always
    agree in a real run (both read the real ``os.name`` once, at import
    time here vs. per-call there), but a caller that needs to describe
    ``is_foreign_absolute``'s OWN verdict should read the SAME flag it
    used, not a second independent seam that a test could in principle
    monkeypatch out of sync with this one.
    """
    return _IS_WINDOWS


def is_foreign_absolute(value: str) -> bool:
    """True when *value* is an absolute path for the OTHER platform's path
    syntax but not this one -- a Windows drive-letter (``X:\\...``,
    ``X:/...``) or UNC (``\\\\server\\share\\...``) path seen on a POSIX
    server, or a POSIX-root (``/...``) path seen on a Windows server.

    The bug this exists to catch (owner report 2026-08-28: Linux box, library
    on a gvfs SMB mount, workflow saved on the Windows PC). Every
    ``is_absolute()`` call in this pack was answered by the LOCAL platform's
    own concrete :class:`Path` flavor, so
    ``Path("Z:\\docs\\short_prompts.md").is_absolute()`` is ``False`` on
    POSIX -- backslash and colon are ordinary filename characters there --
    which is indistinguishable, to the code that asked, from an ACTUAL
    relative name like ``"loras.md"``. Both used to fall into the same
    "join it under ``library_dir``" branch, producing an unopenable path
    like ``<library_dir>/docs/Z:\\docs\\short_prompts.md`` and a cryptic
    ``[Errno 22] Invalid argument`` once the mount rejected it. Symmetric in
    reverse: ``PureWindowsPath("/mnt/nas/x.md").is_absolute()`` is also
    ``False``, so a Linux-saved absolute path joins under ``library_dir`` on
    Windows the same way.

    This function is the one place that tells "foreign-absolute" apart from
    "genuinely relative", by asking BOTH :class:`PureWindowsPath` and
    :class:`PurePosixPath` explicitly -- never a bare :class:`Path`, which
    only ever answers for the platform actually running -- and checking for
    exactly the disagreement that means "foreign, not relative": absolute
    under the OTHER flavor, not absolute under this one.

    Does NOT flag:

    - a relative name (``"loras.md"``, ``"sub/loras.md"``) -- neither
      flavor considers it absolute, so the "absolute under the other
      flavor" half is already false;
    - a path already absolute on THIS platform -- the "not absolute under
      this flavor" half is false;
    - a ``scheme://...`` value -- guarded out explicitly. ``resolve_
      notebook_file`` raises its own :class:`ValueError` for that shape
      BEFORE this check ever runs (a network address is neither a
      foreign-absolute path nor a local one), and this guard keeps every
      OTHER caller's answer consistent with that even if one calls this
      function directly without checking ``"://"`` first.

    Exported for :mod:`lora_library.nodes_notebook` and
    :mod:`lora_library.nodes_prompt_builder` (their ``_peek_resolved_path``
    twins) and :mod:`lora_library.routes` (the fs-browse ``is_absolute()``
    sites) to import and call directly -- genuinely SHARED, unlike this
    pack's usual own-your-helpers convention (see ``nodes_prompt_builder.
    _peek_resolved_path``'s docstring), because a disagreement between call
    sites about what counts as foreign-absolute would BE the bug all over
    again. ``eps_image/routes_frame_saver.py`` keeps its own verbatim copy
    instead of importing this one, per that module's own established
    self-containment rule for anything reaching into ``lora_library/``
    (its module docstring's ``request_is_loopback``/``_machine_owns_
    address`` precedent) -- kept in sync by hand, flagged in its copy's
    docstring.
    """
    if not value or "://" in value:
        return False
    if _IS_WINDOWS:
        return PurePosixPath(value).is_absolute() and not PureWindowsPath(value).is_absolute()
    return PureWindowsPath(value).is_absolute() and not PurePosixPath(value).is_absolute()


def foreign_absolute_note(candidate: Path) -> str:
    """``"tried as a Windows path from another machine: <candidate>"`` (or
    its POSIX counterpart) -- the phrase every heal-on-read call site
    splices into its own "does not exist" / 400 message once :func:`is_
    foreign_absolute` has flagged the ORIGINAL value, so every site names
    the same thing the same way instead of re-deriving which platform is
    "foreign" at each call site. The flavor named is always the complement
    of :data:`_IS_WINDOWS` -- the only platform :func:`is_foreign_absolute`
    could have flagged *candidate*'s source value against.
    """
    flavor = "POSIX" if _IS_WINDOWS else "Windows"
    return f"tried as a {flavor} path from another machine: {candidate}"


def _foreign_path_segments(value: str) -> list[str]:
    """*value*'s path components after its foreign root/drive, e.g.
    ``["docs", "short_prompts.md"]`` for ``"Z:\\docs\\short_prompts.md"``
    seen from POSIX, or ``["mnt", "nas", "x.md"]`` for ``"/mnt/nas/x.md"``
    seen from Windows. Only ever called after :func:`is_foreign_absolute`
    has confirmed *value* is absolute for the OTHER platform, so its
    ``.parts`` always starts with a root/drive component to drop; the
    ``or`` fallback only matters for the degenerate case of a bare
    root/drive with nothing after it.
    """
    flavor = PurePosixPath if _IS_WINDOWS else PureWindowsPath
    parsed = flavor(value)
    segments = list(parsed.parts[1:])
    return segments or [parsed.name or DEFAULT_NOTEBOOK_FILENAME]


def heal_foreign_absolute(value: str, base: Path) -> Path:
    """Resolve a foreign-absolute *value* (:func:`is_foreign_absolute`
    already ``True``) to a path under *base* by trying progressively
    shorter TAILS of its segments, longest first: for
    ``"Z:\\docs\\short_prompts.md"`` that's ``<base>/docs/short_prompts.md``,
    then ``<base>/short_prompts.md``. Returns the first candidate that
    EXISTS; if none does, returns the longest-tail candidate, so a "missing
    file" error still names something sane and a first save lands
    somewhere sensible under the library folder instead of under a literal
    drive-letter/backslash tree that can never exist on this OS.

    UNCONDITIONAL and NO rewriting: nothing on disk changes here, and this
    runs every time :func:`is_foreign_absolute` says yes -- safe to do
    unconditionally (unlike the gated ``web/lora_library/path_heal.js``
    COMBO-value healing, FORMAT.md §7.6, which rewrites values that might
    legitimately differ) because it only ever activates on a value that
    CANNOT resolve locally as given; healing is strictly better than
    failing, and no stored data is touched.

    Every candidate is built by joining plain segment STRINGS onto *base*
    with :meth:`Path.joinpath` -- the foreign path's own separators are
    discarded the moment *value* is split into segments
    (:func:`_foreign_path_segments`), so no candidate this function returns
    can ever contain a ``\\`` or a drive colon.
    """
    segments = _foreign_path_segments(value)
    candidates = [base.joinpath(*segments[i:]) for i in range(len(segments))]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


@dataclass
class LibraryContext:
    """Paths + host-app callables for one running lora_library instance.

    Args:
        user_dir: Directory for this pack's own persistent state (the
            ``config.json`` holding ``library_dir``). Under ComfyUI this is
            ``<user dir>/lora_library``; under tests, a tmp dir.
        default_library_dir: Where the library lives when the user has not
            configured one (FORMAT.md §1). Created lazily on first use.
        list_loras: Returns the installed lora filenames exactly as ComfyUI's
            own lora loaders present them (``folder_paths.get_filename_list``
            values, forward-slash relative paths). Injected so tests can fake
            the model folder.
        resolve_lora_path: Maps one of those filenames to an absolute path
            (``folder_paths.get_full_path``), or None when it doesn't exist.
    """

    user_dir: Path
    default_library_dir: Path
    list_loras: Callable[[], list[str]] = field(default=lambda: [])
    resolve_lora_path: Callable[[str], str | None] = field(default=lambda _name: None)
    #: ``load_config``'s ``((mtime_ns, size), data)`` memo (v0.69.0) -- a
    #: whole tuple replaced at once, so concurrent readers (route worker
    #: threads + the execution thread, NAS round 2026-08-22) only ever see a
    #: complete old or new value; a lost race costs one redundant re-read.
    _config_cache: tuple[tuple[int, int], dict] | None = field(
        default=None, init=False, repr=False, compare=False
    )
    #: :meth:`_ensure_dir`'s memo: directory -> ``time.monotonic()`` of the
    #: last successful ``mkdir``. Same concurrency posture: plain dict
    #: get/set under the GIL, a lost race costs one redundant ``mkdir``.
    _ensured_dirs: dict[Path, float] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    # ------------------------------------------------------------------ config

    @property
    def _config_path(self) -> Path:
        return self.user_dir / CONFIG_FILENAME

    def load_config(self) -> dict:
        """The persisted pack config (currently only ``library_dir``).

        Missing or unreadable config is not an error — it simply means
        defaults (a fresh install, or a hand-deleted file).

        Cached on the file's mtime+size (audit 2026-08-21): ``library_dir()``
        re-parsed ``config.json`` on EVERY call, and ``list_sets`` resolves
        the dir once per set file -- N JSON parses per ``/object_info``.
        One ``stat`` now answers the common unchanged case; an edit (or a
        ``save_config`` write) changes the mtime and re-reads. A shallow
        copy is returned so a caller that mutates its dict before
        ``save_config`` can never poison the cache for another caller.
        """
        try:
            stat = self._config_path.stat()
            key = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            key = None
        cached = self._config_cache
        if key is not None and cached is not None and cached[0] == key:
            return dict(cached[1])
        try:
            with open(self._config_path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return {}
        # ValueError, not just JSONDecodeError (review 2026-08-09): a
        # config.json saved as UTF-16 raises UnicodeDecodeError — a plain
        # ValueError — which used to escape this guard and propagate out of
        # library_dir() into every store and route that resolves it.
        # JSONDecodeError is itself a ValueError subclass, so nothing is lost.
        except (OSError, ValueError) as exc:
            logger.warning(
                "EPSNodes: unreadable %s (%s); using defaults", self._config_path, exc
            )
            return {}
        data = data if isinstance(data, dict) else {}
        if key is not None:
            self._config_cache = (key, dict(data))
        return data

    def save_config(self, config: dict) -> None:
        """Atomically persist *config* (FORMAT.md §1).

        Also forgets every directory :meth:`_ensure_dir` has verified: the
        config is where ``library_dir`` lives, so a save may point the
        library somewhere new, and the next :meth:`library_dir` call must
        create/verify THAT folder for real.
        """
        self.user_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(self._config_path, json.dumps(config, indent=2) + "\n")
        self.forget_ensured_dirs()

    # ------------------------------------------------------------- library dir

    def configured_library_dir(self) -> Path:
        """The library directory the config names (else the default) --
        PURE: no ``mkdir``, no stat, never raises (NAS round 2026-08-22).

        For callers that only need to NAME the folder (``GET /sets``'s
        ``sets_dir``/``is_default_library`` fields, the §5 config
        diagnosis) without paying a network round trip to create it --
        :meth:`library_dir` is the same path, created.
        """
        configured = self.load_config().get("library_dir")
        return Path(configured) if configured else self.default_library_dir

    def is_default_library(self) -> bool:
        """True when the library lives at :attr:`default_library_dir`
        (unconfigured, or configured to exactly that path) -- pure."""
        return self.configured_library_dir() == self.default_library_dir

    def library_dir(self) -> Path:
        """The active library directory (configured, else default), created.

        "Created" is memoized for :data:`ENSURED_DIR_TTL_S` per path
        (:meth:`_ensure_dir`): the return value is identical, but on a NAS
        library only the first call in each window pays the ``mkdir``
        round trip. A ``mkdir`` that FAILS (unreachable/unwritable folder)
        is never memoized -- every call keeps raising its ``OSError``, as
        before, so the stores' unreachable-folder handling is unchanged.
        """
        directory = self.configured_library_dir()
        self._ensure_dir(directory)
        return directory

    def sets_dir(self) -> Path:
        """``<library_dir>/sets`` (FORMAT.md §4), created (memoized like
        :meth:`library_dir`)."""
        directory = self.library_dir() / SETS_DIRNAME
        self._ensure_dir(directory)
        return directory

    def _ensure_dir(self, directory: Path) -> None:
        """``directory.mkdir(parents=True, exist_ok=True)``, at most once per
        :data:`ENSURED_DIR_TTL_S` per path. Raises the ``mkdir``'s own
        ``OSError`` (and memoizes nothing) when it fails."""
        now = time.monotonic()
        stamp = self._ensured_dirs.get(directory)
        if stamp is not None and now - stamp < ENSURED_DIR_TTL_S:
            return
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            self._ensured_dirs.pop(directory, None)
            raise
        self._ensured_dirs[directory] = now

    def forget_ensured_dirs(self) -> None:
        """Drop :meth:`_ensure_dir`'s memo so the next :meth:`library_dir` /
        :meth:`sets_dir` call verifies the folder on disk again. Called by
        :meth:`save_config`, and by the stores when they meet a
        directory-level ``FileNotFoundError`` (the sets folder vanished
        between two listings, a set file listed a moment ago is gone) --
        the one signal that the folder itself may no longer be there."""
        self._ensured_dirs.clear()

    def remote_dirs(self) -> list[Path]:
        """Folders OUTSIDE ``library_dir`` that non-loopback callers may also
        touch (FORMAT.md §2), newest last. Empty by default.

        Owner report 2026-07-29: a notebook on a NAS mount
        (``/run/user/1000/gvfs/smb-share:…/docs/loras.md``) worked from the
        Linux box running ComfyUI and 403'd from his Mac, because §2 confines
        remote callers to ``library_dir`` and §1 explicitly blesses absolute
        NAS paths — two rules that were each right and together made the node
        unusable remotely. This list is the reconciliation: the HOST names the
        extra folders it is willing to expose, exactly as it already names
        ``library_dir``.

        Why an allow-list and not "let remote reads through": the ``file``
        value arrives in the request, so the server cannot tell a path the
        host's workflow chose from one a caller invented — the workflow lives
        in the browser. Without a host-side list, permitting an arbitrary
        remote ``file`` is an arbitrary-file-read on the host, which is the
        one thing §2 exists to prevent. Written only through the
        loopback-only route, same as ``library_dir``.

        Deliberately NOT created (no ``mkdir``, unlike :meth:`library_dir`):
        an entry for a NAS that happens to be unmounted must stay a dormant
        allow-list entry, not conjure an empty local directory that then
        shadows the real mount point.
        """
        raw = self.load_config().get("remote_dirs")
        if not isinstance(raw, list):
            return []
        out: list[Path] = []
        for item in raw:
            if isinstance(item, str) and item.strip():
                out.append(Path(item.strip()))
        return out

    def resolve_notebook_file(self, file_value: str) -> Path:
        """Resolve a node/route ``file`` value to an absolute ``.md`` path.

        Relative values resolve against :meth:`library_dir`; absolute values
        (including Windows UNC ``\\\\server\\share`` paths) pass through
        untouched — pointing the notebook at a NAS is the design center, not
        an edge case (FORMAT.md §1/§2). No existence check here: readers
        surface "missing file" themselves so a brand-new path can be created
        by the first save.

        A ``scheme://…`` value raises :class:`ValueError` (2026-07-26 owner
        report, found while fixing the Linux picker): ``Path("smb://host/x")``
        COLLAPSES to ``smb:/host/x`` — a single slash, which is not absolute
        on POSIX — so a typed network address used to sail past the
        ``is_absolute()`` branch below and get joined UNDER the library
        folder. That failed silently in the worst way: the node reported a
        missing file (looking like an empty notebook), and the first SAVE
        would ``mkdir -p`` a bogus ``smb:/host/…`` directory tree inside the
        user's real library folder (``:`` is a legal POSIX filename char).
        Raising here is caught by ``routes_notebook._resolve_path`` (→ a 400
        naming the problem) and surfaces loudly at queue time from the node,
        which is where FORMAT.md §6.1 wants bad ``file`` values to land.

        A FOREIGN-ABSOLUTE value (:func:`is_foreign_absolute`; 2026-08-28
        owner report: ``Z:\\docs\\short_prompts.md`` from a Windows-saved
        workflow, read on a Linux box) is checked BEFORE the plain
        ``is_absolute()`` branch below, which would otherwise answer
        ``False`` for it — indistinguishable from a genuinely relative name
        — and join the WHOLE foreign path under ``library_dir``, producing
        an unopenable path and a bare ``[Errno 22]``. Instead it's healed
        (:func:`heal_foreign_absolute`) against tails of its own segments
        under :meth:`library_dir`: never rewritten on disk, always applied,
        because it only ever fires on a value that cannot resolve locally
        as given. Checking it first rather than nested inside ``not path.
        is_absolute()`` changes nothing about real behavior — by
        :func:`is_foreign_absolute`'s own definition, a value it flags is
        NEVER locally absolute on a host where :data:`_IS_WINDOWS` matches
        the real platform — but it keeps this method fully exercisable
        under the injectable ``_IS_WINDOWS`` seam in tests, where the real
        concrete :class:`Path` class stays bound to the actual test
        machine's OS regardless of what the seam claims.
        """
        value = (file_value or "").strip() or DEFAULT_NOTEBOOK_FILENAME
        if "://" in value:
            scheme = value.split("://", 1)[0] or "that"
            raise ValueError(
                f"{scheme}:// is a network address, not a file path. Mount the "
                "share first, then point this at its mount point (e.g. "
                "/mnt/nas/loras.md, or /run/user/1000/gvfs/smb-share:server=… "
                "for a share mounted from your file manager)."
            )
        if is_foreign_absolute(value):
            return heal_foreign_absolute(value, self.library_dir())
        path = Path(value)
        if not path.is_absolute():
            path = self.library_dir() / path
        return path

    def relativize_library_path(self, path: Path | str) -> str:
        """*path* as a POSIX-style string relative to the library folder
        when it's inside it (``"short_prompts.md"``, ``"sub/x.md"``); the
        absolute string UNCHANGED otherwise.

        FORMAT.md §2/§7.6: the fix that stops NEW workflows acquiring an
        unportable absolute ``file`` value in the first place (2026-08-28
        cross-OS path fix) — a resolved-path-to-client seam should
        relativize here before that path round-trips into a ``file`` widget
        and gets saved absolute into a workflow that may next open on a
        different OS or mount point.

        Compared against :meth:`configured_library_dir` — the same path
        :meth:`library_dir` returns, without paying its ``mkdir`` — via
        :meth:`Path.relative_to`, never a raw string prefix check: a
        sibling folder that merely shares ``library_dir``'s string prefix
        (``/lib2`` vs ``/lib``) must NOT be misread as "inside" it, and
        ``relative_to`` compares path SEGMENTS so it can't make that
        mistake the way ``str.startswith`` would.
        """
        candidate = Path(path)
        try:
            rel = candidate.relative_to(self.configured_library_dir())
        except ValueError:
            return str(candidate)
        return rel.as_posix()


def _atomic_write_text(path: Path, text: str) -> None:
    """Write *text* to *path* via a same-directory temp file + ``os.replace``.

    Same-directory matters: ``os.replace`` is only atomic within one
    filesystem, and the library may live on a NAS mount distinct from the
    system temp dir. Callers own error handling; a failed write must never
    leave a half-written target behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        try:
            os.replace(tmp_name, path)
        except FileExistsError:
        # gvfs/FUSE network mounts (owner's Linux box, 2026-08-25:
        # /run/user/1000/gvfs/smb-share:...) reject rename-over-existing
        # with EEXIST even though POSIX rename replaces -- so EVERY save to
        # an already-existing file on such a mount failed. Fall back to
        # unlink + replace: a tiny non-atomic window in which the target is
        # briefly missing, strictly better than the save always failing.
        # No data is at risk in the window -- the temp file next to the
        # target already holds the complete new content, and a crash inside
        # the window leaves that .tmp recoverable in the same directory.
            with contextlib.suppress(FileNotFoundError):
                os.unlink(path)
            try:
                os.replace(tmp_name, path)
            except OSError:
                # The target is unlinked at this point -- if the rename
                # STILL fails, a direct write is the last resort that
                # leaves the target present with the full new content
                # (the outer cleanup would otherwise delete the temp too,
                # losing both versions).
                Path(path).write_text(text, encoding="utf-8", newline="")
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
