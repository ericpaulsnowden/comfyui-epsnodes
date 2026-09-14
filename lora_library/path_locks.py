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

#: One lock per distinct resolved path, created lazily. Never evicted: the
#: number of distinct notebook/sidecar files a running server ever touches
#: is small and bounded by what's actually on disk, so trading a few bytes
#: of never-freed bookkeeping per file for simplicity is the right call --
#: there is no plausible workload where this dict's size becomes a problem.
_locks: dict[str, threading.Lock] = {}

#: Guards ``_locks`` itself. Two threads racing to create the FIRST lock
#: for the same key must not each win with a different ``Lock`` object --
#: that would defeat the whole point of keying by path. This lock is held
#: only for the couple of dict operations below, never across a caller's
#: own (potentially slow) transaction.
_registry_guard = threading.Lock()


def _key_for(path: Path | str) -> str:
    """The registry key for *path*.

    ``os.path.realpath`` resolves symlinks (and ``..``/``.`` segments) so
    two different spellings of the same file map to the same lock;
    ``os.path.normcase`` additionally folds case on the case-insensitive
    filesystems these packs actually run on (Windows, and macOS's default
    APFS), so ``Loras.md`` and ``loras.md`` -- the same inode there --
    serialize against each other too, matching the filesystem's own
    notion of "the same file" rather than a stricter one that would let
    two differently-cased requests slip past each other unlocked.
    """
    return os.path.normcase(os.path.realpath(str(path)))


def lock_for(path: Path | str) -> threading.Lock:
    """The process-wide :class:`threading.Lock` guarding *path*.

    Creates the lock on first use; every later call for the same
    (resolved) path returns the SAME object, so ``with lock_for(path):``
    around a load->check_conflict->mutate->save sequence really does
    serialize every caller that names this file, however they spell it.

    ``os.path.realpath`` can be a real filesystem round trip (a NAS mount,
    a chain of symlinks), so THIS FUNCTION MUST ALWAYS BE CALLED FROM A
    WORKER THREAD (``asyncio.to_thread``), never directly on the asyncio
    event loop -- exactly the same discipline ``routes_notebook.py``'s
    module docstring already documents for ``load_notebook``/
    ``save_notebook`` themselves. Calling it on the loop would block every
    other request on the server for as long as the resolve takes.
    """
    key = _key_for(path)
    with _registry_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock
