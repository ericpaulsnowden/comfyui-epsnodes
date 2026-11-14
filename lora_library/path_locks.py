"""Per-path mutual exclusion for lora_library's read-modify-write file
writers (RELEASE-REVIEW-2026-09-13.md finding 3, "Notebook conflict
detection misses overlapping saves").

The problem this closes: ``routes_notebook.py``'s six mutation routes
(and a few other stores' own load-mutate-write helpers) each do
``load`` -> ``check_conflict(base_mtime, ...)`` -> in-memory mutate ->
``save`` as *separate* steps, with an ``await`` between the load and the
save. Two requests loaded from the same ``base_mtime`` can both pass the
conflict check and then both save -- the second write silently discards
the first's change, even though each individual request looked correct
in isolation. FORMAT.md §3.5's mtime check is a real guard for two
*machines* saving the same NAS file at different times; it was never
meant to (and cannot) serialize two requests interleaved *inside one
server process*.

The fix is a plain per-path :class:`threading.Lock`: whichever caller
wraps its own load->check->mutate->save sequence in ``with
lock_for(path):`` is guaranteed no other caller can observe the file
between this one's load and save, for the SAME resolved path. This is a
single-process guard only -- it says nothing about two separate ComfyUI
processes (or two machines) sharing one NAS-mounted library, which is
still exactly the two-machine case FORMAT.md §3.5 already covers via
``base_mtime``.

Callers must run the ENTIRE locked transaction (including acquiring the
lock) inside a worker thread, never on the asyncio event loop -- see
:func:`lock_for`'s own docstring for why.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

#: One lock per CASE-FOLDED path spelling (``realpath`` + ``normcase`` +
#: ``casefold``, on every OS), created lazily. Case must not matter here:
#: on macOS/Windows differently-cased names are the same file and MUST
#: share a lock; on a case-sensitive volume they are different files that
#: merely share a lock, which only over-serializes and is always safe. The
#: spelling key cannot depend on the inode, which an atomic save changes
#: under a lookup that has not seen the new one yet. A spelling is only
#: added by a caller that names it, so the dict is bounded by what the
#: server touches.
_locks: dict[str, threading.Lock] = {}

#: The same locks, found by file identity ``(st_dev, st_ino)`` -- the only
#: key that makes ``Loras.md`` and ``loras.md`` one lock on a
#: case-insensitive volume, where ``os.path.normcase`` does not fold case
#: (macOS). One entry per lock: an atomic save renames a temp over the file,
#: which CHANGES its inode, so the entry follows the file (stale inode keys
#: for the same lock are dropped, see :func:`lock_for`).
_by_inode: dict[tuple[int, int], threading.Lock] = {}

#: Guards both dicts. Two threads racing to create the FIRST lock for the
#: same file must not each win with a different ``Lock`` object. Held only
#: for a few dict operations, never across a caller's own transaction.
_registry_guard = threading.Lock()


def _identity(resolved: str) -> tuple[int, int] | None:
    """``(st_dev, st_ino)`` of *resolved*, or ``None`` when the file does not
    exist (or the filesystem reports no inode, as some Windows network
    shares do with ``st_ino == 0``)."""
    try:
        st = os.stat(resolved)
    except OSError:
        return None
    return (st.st_dev, st.st_ino) if st.st_ino else None


def lock_for(path: Path | str) -> threading.Lock:
    """The process-wide :class:`threading.Lock` guarding *path*.

    Creates the lock on first use. Lookup is by file identity first
    (``st_dev``/``st_ino``: every spelling of an EXISTING file -- other
    case on a case-insensitive volume, hard link, symlink -- is one lock),
    then by the ``realpath``/``normcase`` string, which is what finds the
    lock while the file is momentarily absent (the gvfs save fallback moves
    the original aside for an instant) or not yet created.

    Case never matters to the spelling key (see ``_locks``), so a save that
    swaps the inode, a file that is absent mid-save, or a file not yet
    created cannot give two spellings of one name two locks. The inode map
    adds hard links and Unicode-normalization variants (APFS) on top.
    Residual window, accepted: a hard link or a normalization variant of a
    file that does not exist yet. Cross-process safety is still only
    ``base_mtime`` (FORMAT.md §3.5).

    ``os.path.realpath`` and ``os.stat`` can be real filesystem round trips
    (a NAS mount, a chain of symlinks), so THIS FUNCTION MUST ALWAYS BE
    CALLED FROM A WORKER THREAD (``asyncio.to_thread``), never directly on
    the asyncio event loop -- exactly the same discipline
    ``routes_notebook.py``'s module docstring documents for
    ``load_notebook``/``save_notebook`` themselves.
    """
    resolved = os.path.realpath(str(path))
    spelling = os.path.normcase(resolved).casefold()
    identity = _identity(resolved)
    with _registry_guard:
        lock = _by_inode.get(identity) if identity else None
        if lock is None:
            lock = _locks.get(spelling)
        if lock is None:
            lock = threading.Lock()
        _locks[spelling] = lock
        if identity is not None and _by_inode.get(identity) is not lock:
            for stale in [k for k, v in _by_inode.items() if v is lock]:
                del _by_inode[stale]
            _by_inode[identity] = lock
        return lock
