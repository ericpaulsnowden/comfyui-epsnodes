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
from pathlib import Path

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
        path = Path(value)
        if not path.is_absolute():
            path = self.library_dir() / path
        return path


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
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
