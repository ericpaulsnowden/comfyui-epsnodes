"""On-disk storage for LoRA sets (FORMAT.md §4/§4.1).

One JSON file per set under ``context.sets_dir()``. This module owns the
whole §4 file lifecycle (slug derivation, validation/defaults, atomic
save/load/list/delete), the §4.1 composite multi-loader schema, and the §4
lora-resolution rule; ``routes_sets.py`` and ``nodes_sets.py`` both build on
it and never touch the filesystem directly. No ComfyUI imports here — same
importable-without-ComfyUI seam as ``context.py`` (see its module docstring).
"""

from __future__ import annotations

import copy
import fnmatch
import json
import logging
import os
import re
import stat as stat_module
import threading
import time
from pathlib import Path

from .context import LibraryContext, _atomic_write_text

logger = logging.getLogger("lora_library")

#: FORMAT.md §4/§4.1 — the highest ``format`` value this reader understands.
#: Bumped 1 -> 2 for the §4.1 composite multi-loader schema (owner ask
#: 2026-07-20). Note that this ceiling only ever rejects a GENUINELY newer
#: format: a file *labeled* ``"format": 2`` (or higher) but missing a usable
#: ``loaders`` key still degrades gracefully to format 1 — see
#: :func:`normalize_set`.
CURRENT_FORMAT = 2

#: Characters kept by :func:`slugify`; everything else is dropped outright
#: (v1 deliberately does not transliterate unicode/emoji — FORMAT.md §4).
_SLUG_DISALLOWED_RE = re.compile(r"[^a-z0-9\-_]")
_WHITESPACE_RE = re.compile(r"\s+")

#: What counts as a valid on-disk slug (FORMAT.md §4). Mirrors
#: ``routes.SLUG_RE`` and MUST stay in lockstep with it — duplicated here
#: (rather than imported) because the layering runs the other way: the HTTP
#: layer builds on this store, and the store must stay importable without it.
_VALID_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-_]*$")


class SetValidationError(ValueError):
    """Raised when a set payload/file doesn't match FORMAT.md §4.

    Always carries a human-readable message safe to surface verbatim in an
    HTTP ``{"error": ...}`` body (``routes_sets.py`` does exactly that).
    """


# ------------------------------------------------------------------- slugify

def slugify(name: str) -> str:
    """Filename stem for a set's JSON file, derived from its display *name*.

    Per FORMAT.md §4: lowercase; whitespace runs collapse to a single ``-``;
    anything outside ``[a-z0-9-_]`` is stripped. A leading run of ``-``/``_``
    is additionally trimmed so the result always satisfies
    ``routes.SLUG_RE`` (which requires an alphanumeric first character) —
    without this, an all-emoji or ``"_foo"`` name could otherwise slugify to
    something the routes would then refuse to serve. A trailing run is
    trimmed too, purely for cosmetics (SLUG_RE doesn't constrain the last
    character): a name bracketed by stripped characters on both ends, e.g.
    ``"\U0001f3a8 Style \U0001f3a8"``, would otherwise leave a dangling
    ``"style-"``. Collision numbering (``-2``, ``-3``, …) is NOT this
    function's job: it alone can't know what else is already on disk, so
    callers needing a unique slug (only :func:`save_set`, for a brand-new
    set) handle that separately.
    """
    slug = (name or "").strip().lower()
    slug = _WHITESPACE_RE.sub("-", slug)
    slug = _SLUG_DISALLOWED_RE.sub("", slug)
    slug = slug.strip("-_")
    return slug or "set"


def set_path(context: LibraryContext, slug: str) -> Path:
    """``<sets_dir>/<slug>.json`` — the single source of truth for the name.

    ``context.sets_dir()`` creates the folder on demand, so this raises
    ``OSError`` when the configured library folder is unreachable (an
    unmounted NAS — the same failure ``routes.py``'s ``get_config``/
    ``notebook_path_error`` guard against). Store entry points that feed
    HTTP routes call :func:`_require_sets_dir` first so that condition
    surfaces as a 4xx-able :class:`SetValidationError`; ``nodes_sets.py``'s
    ``_set_file_token`` deliberately keeps catching the raw ``OSError``.
    """
    return context.sets_dir() / f"{slug}.json"


def _require_sets_dir(context: LibraryContext) -> Path:
    """``context.sets_dir()``, with an unreachable library folder surfaced
    as :class:`SetValidationError` — which the set routes turn into a 400
    ``{"error": ...}`` — instead of a raw ``OSError`` that would 500 the
    whole route (audit 2026-08-08; the OSError's own text names the folder
    that could not be created/reached)."""
    try:
        return context.sets_dir()
    except OSError as exc:
        raise SetValidationError(
            f"the library folder is unreachable ({exc}); LoRA sets live "
            "inside it -- fix or remount the configured library folder and "
            "retry"
        ) from exc


def _unique_slug(context: LibraryContext, base: str) -> str:
    """*base*, or ``<base>-2``, ``<base>-3``, … — whichever isn't on disk yet."""
    candidate = base
    suffix = 2
    while set_path(context, candidate).exists():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


# --------------------------------------------------------------- validation

def _coerce_float(value: object, field_name: str) -> float:
    # bool is a subclass of int in Python; a stray `"on": true` must not be
    # silently accepted as a strength of 1.0 if it ends up in the wrong key.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SetValidationError(f"{field_name} must be a number — FORMAT.md §4")
    return float(value)


def _normalize_row(index: int, row: object, label: str = "loras") -> dict:
    """One ``loras[]`` entry, validated. *label* is the name used in error
    messages (default ``"loras"`` for the top-level list — every existing
    §4 message is byte-identical to before this parameter existed; §4.1
    composite entries pass ``"loaders[i].loras"`` so a bad row inside a
    specific loader names that loader).
    """
    if not isinstance(row, dict):
        raise SetValidationError(f"{label}[{index}] must be an object — FORMAT.md §4")
    file = row.get("file")
    if not isinstance(file, str) or not file:
        raise SetValidationError(f"{label}[{index}] is missing a 'file' — FORMAT.md §4")
    strength_clip_raw = row.get("strength_clip")
    strength_clip = (
        None
        if strength_clip_raw is None
        else _coerce_float(strength_clip_raw, f"{label}[{index}].strength_clip")
    )
    return {
        "file": file,
        "on": bool(row.get("on", True)),
        "strength": _coerce_float(row.get("strength", 1.0), f"{label}[{index}].strength"),
        "strength_clip": strength_clip,
    }


def _normalize_loras_list(loras_raw: object, label: str = "loras") -> list[dict]:
    """A whole ``loras[]`` array, validated row by row. *label* — see
    :func:`_normalize_row`; also used in the "must be a list" message so it
    reads e.g. ``set 'loaders[0].loras' must be a list``.
    """
    if not isinstance(loras_raw, list):
        raise SetValidationError(f"set '{label}' must be a list — FORMAT.md §4")
    return [_normalize_row(i, row, label) for i, row in enumerate(loras_raw)]


def normalize_set(raw: object) -> dict:
    """Validate *raw* (parsed JSON or a request body's ``set``) into a
    canonical FORMAT.md §4/§4.1 dict, applying every documented default.

    The OUTPUT format is derived STRUCTURALLY from whether *raw* has a
    usable ``loaders`` key — never copied from (or defaulted to) whatever
    ``format`` int *raw* declares or omits:

    - A ``loaders`` key that is a non-empty list normalizes to the §4.1
      composite shape: ``{"format": 2, ..., "loaders": [...], "loras":
      <copy of loaders[0].loras>}``. The top-level ``loras`` mirror is
      ALWAYS recomputed from ``loaders[0]`` here, regardless of whatever
      *raw*'s own top-level ``loras`` said, so the two can never drift.
    - Anything else — no ``loaders`` key at all, or one that is present but
      malformed (not a list, or an empty list) — normalizes to the plain
      format-1 shape (no ``loaders`` key in the result), using the
      top-level ``loras``. A malformed-but-present ``loaders`` is logged
      and degraded rather than rejected ("never crash on malformed"); this
      is also FORMAT.md §4.1's documented graceful-degrade rule for a
      hand-edited file: "no loaders key" (functionally) is format 1
      regardless of the declared ``format`` int.

    Raises :class:`SetValidationError` — never a bare ``KeyError``/``TypeError``
    — so callers (routes, the loader below) can surface one clear message.
    This still only covers the same classes of malformed input it always
    did (non-dict payload/row, bad field types, a ``format`` newer than this
    pack understands); §4.1's ``loaders[i]`` entries follow the identical
    posture (a non-object entry, or a bad row inside one, raises exactly
    like a bad top-level row always has).
    """
    if not isinstance(raw, dict):
        raise SetValidationError("a set must be a JSON object — FORMAT.md §4")

    fmt = raw.get("format", 1)
    if not isinstance(fmt, int) or isinstance(fmt, bool):
        raise SetValidationError("set 'format' must be an integer — FORMAT.md §4")
    if fmt > CURRENT_FORMAT:
        raise SetValidationError(
            f"this set was saved by a newer version of the pack (format {fmt}); "
            "update the pack — FORMAT.md §4"
        )

    name = raw.get("name", "")
    if not isinstance(name, str):
        raise SetValidationError("set 'name' must be a string — FORMAT.md §4")

    trigger_words = raw.get("trigger_words", "")
    if not isinstance(trigger_words, str):
        raise SetValidationError("set 'trigger_words' must be a string — FORMAT.md §4")

    notes = raw.get("notes", "")
    if not isinstance(notes, str):
        raise SetValidationError("set 'notes' must be a string — FORMAT.md §4")

    loaders_raw = raw.get("loaders")
    if isinstance(loaders_raw, list) and loaders_raw:
        loaders = []
        for i, loader_raw in enumerate(loaders_raw):
            if not isinstance(loader_raw, dict):
                raise SetValidationError(f"loaders[{i}] must be an object — FORMAT.md §4.1")
            loras = _normalize_loras_list(loader_raw.get("loras", []), f"loaders[{i}].loras")
            loaders.append({"loras": loras})
        return {
            "format": 2,
            "name": name,
            "loaders": loaders,
            # §4.1: ALWAYS kept in sync with loaders[0] — never trust *raw*'s
            # own top-level `loras`, so the mirror can never drift.
            "loras": [dict(row) for row in loaders[0]["loras"]],
            "trigger_words": trigger_words,
            "notes": notes,
        }

    if loaders_raw is not None:
        # Present but malformed (wrong type, or a genuinely empty list) —
        # FORMAT.md §4.1 "never crash on malformed": log and degrade to a
        # single-loader (format 1) set from the top-level `loras`, rather
        # than rejecting the whole set outright.
        logger.warning(
            "EPSNodes: set %r has a malformed/empty 'loaders' (%r); "
            "degrading to a single-loader (format 1) set — FORMAT.md §4.1",
            name,
            loaders_raw,
        )

    loras = _normalize_loras_list(raw.get("loras", []))
    return {
        "format": 1,
        "name": name,
        "loras": loras,
        "trigger_words": trigger_words,
        "notes": notes,
    }


def loras_for_slot(state: object, slot: int) -> list[dict]:
    """The lora rows *state* stores for loader index *slot* (FORMAT.md §4.1).

    Format-2 *state* (a ``loaders`` key holding a non-empty list) returns
    ``state["loaders"][clamp(slot, 0, len(loaders) - 1)]["loras"]`` — *slot*
    clamps into range instead of raising, per §4.1's "index out of range
    clamps to the last available loader (never errors)". Anything else
    (format-1, or a *state* whose ``loaders`` is missing/malformed/empty)
    returns ``state["loras"]``.

    NEVER RAISES: *state* not being a dict, ``loaders``/``loras`` not being
    lists, a loader entry not being a dict, or *slot* not being coercible to
    ``int`` all degrade to the safest available fallback (ultimately ``[]``)
    rather than throwing — this is meant to be safe to call from
    ``nodes_sets.py``'s ``apply()`` against a state that came from
    :func:`normalize_set` (already well-shaped) just as readily as from a
    hand-built/legacy dict that never went through it.
    """
    if not isinstance(state, dict):
        return []
    loaders = state.get("loaders")
    if isinstance(loaders, list) and loaders:
        try:
            index = int(slot)
        except (TypeError, ValueError):
            index = 0
        index = max(0, min(index, len(loaders) - 1))
        loader = loaders[index]
        loras = loader.get("loras") if isinstance(loader, dict) else None
        return loras if isinstance(loras, list) else []
    loras = state.get("loras")
    return loras if isinstance(loras, list) else []


# -------------------------------------------------------------- persistence

def load_set(context: LibraryContext, slug: str) -> dict | None:
    """The normalized set at *slug*, or ``None`` if no such file exists.

    A file that exists but fails to parse/validate raises
    :class:`SetValidationError` rather than being treated as missing — a
    corrupt/too-new file is a different situation from "not created yet"
    and callers (routes, nodes) are expected to tell them apart. An
    unreachable library folder raises the same class (via
    :func:`_require_sets_dir`) — every caller already handles it: the
    routes 400, ``nodes_sets`` warns and passes through.

    PER-FILE LOAD cache (audit 2026-08-26 while-running round, finding 2):
    this used to open+parse the file on EVERY call, including the pin-drift
    check every pinned Apply-Set node fires and the controller's own reads
    -- on a NAS mid-run that is a full network round trip plus a JSON parse
    for a file that, most of the time, has not changed since the last read.
    The STAT still happens on every call (never skipped -- that is the
    freshness check); only the read+parse+:func:`normalize_set` validation
    is skipped when the file's ``(mtime_ns, size)`` matches ``_load_cache``.
    Returns a deep copy either way (cache hit or miss), so this keeps the
    same "independent object per call, safe to mutate" contract every
    caller already relies on (:func:`list_sets`' own docstring states the
    identical guarantee for its summaries).
    """
    _require_sets_dir(context)
    path = set_path(context, slug)
    try:
        file_stat = path.stat()
    except FileNotFoundError:
        # A set that isn't there may mean the FOLDER isn't there any more
        # (NAS round 2026-08-22): make the next sets_dir() look for real.
        context.forget_ensured_dirs()
        return None
    except OSError as exc:
        raise SetValidationError(f"could not read set {slug!r}: {exc}") from exc
    file_key = (file_stat.st_mtime_ns, file_stat.st_size)
    cached = _load_cache.get(path)
    if cached is not None and cached[0] == file_key:
        return copy.deepcopy(cached[1])
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        # Vanished between the stat above and this open (a delete racing
        # us) -- same "folder may be gone too" treatment as above.
        context.forget_ensured_dirs()
        return None
    except OSError as exc:
        raise SetValidationError(f"could not read set {slug!r}: {exc}") from exc
    # ValueError, not just JSONDecodeError (audit 2026-08-21): a set file
    # saved as UTF-16/CP-1252 raises UnicodeDecodeError -- a plain
    # ValueError -- which escaped this guard AND nodes_sets.apply()'s
    # SetValidationError catch, crashing the whole run (and, via
    # list_sets, collapsing the Apply-Set dropdown to ["None"]). The
    # picker store and context.load_config were already patched for the
    # same class of failure; JSONDecodeError is a ValueError subclass, so
    # nothing is lost.
    except ValueError as exc:
        raise SetValidationError(f"set {slug!r} could not be read: {exc}") from exc
    normalized = normalize_set(raw)
    _load_cache[path] = (file_key, copy.deepcopy(normalized))
    return normalized


def save_set(context: LibraryContext, set_data: dict, slug: str | None = None) -> tuple[str, dict]:
    """Normalize and atomically persist *set_data*.

    When *slug* is omitted (a brand-new set), it is derived from
    ``set_data["name"]`` via :func:`slugify` and de-duplicated against
    what's already on disk (FORMAT.md §4: collision → ``-2``, ``-3``, …).
    A caller-supplied *slug* (updating a known set) is used as-is — renaming
    a set's display name must not move its file out from under saved
    workflows/routes that reference it by slug. Returns ``(slug, normalized)``.

    Raises :class:`SetValidationError` for a payload that fails §4
    validation AND for an unreachable library folder (checked up front, so
    the route 400s naming the folder instead of 500ing on the raw OSError
    — audit 2026-08-08).
    """
    normalized = normalize_set(set_data)
    sets_dir = _require_sets_dir(context)
    if slug is None:
        slug = _unique_slug(context, slugify(normalized["name"]))
    text = json.dumps(normalized, indent=2, ensure_ascii=False) + "\n"
    path = set_path(context, slug)
    _atomic_write_text(path, text)
    # 2026-08-26 while-running round (findings 1 + 2): WARM every cache this
    # write affects instead of just forgetting it. routes_sets.py's
    # POST /lora_library/set handler tails a fresh `list_sets()` onto this
    # call to build its response -- before this round that always meant a
    # cold rescan of every set file (a guaranteed second NAS round trip on
    # top of the write itself), because `_forget_listing` evicted the whole
    # listing memo unconditionally. Now: the per-file ENTRY cache
    # (list_sets' own scan-skip) and the per-file LOAD cache (load_set's
    # parse-skip, finding 2) are both keyed on (mtime_ns, size), which one
    # fresh stat gives us for free right after the write -- and the LISTING
    # cache is patched in place (see _splice_listing_cache) rather than
    # evicted wholesale, so a warm listing STAYS warm across a save.
    summary = {"slug": slug, "name": normalized["name"], "count": len(normalized["loras"])}
    try:
        file_stat = path.stat()
        file_key = (file_stat.st_mtime_ns, file_stat.st_size)
        _entry_cache[path] = (file_key, dict(summary))
        _load_cache[path] = (file_key, copy.deepcopy(normalized))
    except OSError:
        # Vanishingly unlikely (the write above just succeeded) but not
        # impossible on a flaky NAS -- fall back to forgetting both memos
        # outright rather than risk caching a stale/guessed key. The
        # listing splice below still runs off the summary we already know,
        # independent of whether this stat landed.
        _entry_cache.pop(path, None)
        _load_cache.pop(path, None)
    _splice_listing_cache(context, sets_dir, slug, summary)
    return slug, normalized


def delete_set(context: LibraryContext, slug: str) -> bool:
    """Delete the set at *slug*. ``True`` if a file was removed, else
    ``False``. An unreachable library folder raises
    :class:`SetValidationError` (via :func:`_require_sets_dir`) rather than
    reading as a plain "nothing to delete" ``False`` — the route turns it
    into a 400 naming the folder instead of a misleading 404."""
    sets_dir = _require_sets_dir(context)
    path = set_path(context, slug)
    try:
        path.unlink()
    except FileNotFoundError:
        # Listed a moment ago but gone now: the file, or the whole folder
        # (NAS round 2026-08-22) -- forget both memos outright. Unlike the
        # happy path below, there is no freshly-known state to splice in
        # here (we don't know WHY it's gone), so this stays the "forget,
        # don't guess" fallback finding 1's acceptable-alternative wording
        # describes.
        _forget_listing(sets_dir, path)
        context.forget_ensured_dirs()
        return False
    # 2026-08-26 while-running round (finding 1): drop just this slug from
    # the cached listing (splice) instead of evicting the whole thing --
    # same reasoning as save_set's warm-cache treatment just above it.
    _entry_cache.pop(path, None)
    _load_cache.pop(path, None)
    _splice_listing_cache(context, sets_dir, slug, None)
    return True


# ----------------------------------------- listing caches (NAS round 2026-08-22)
# Owner situation: the library folder is on a NAS, so every file read is a
# network round trip -- and `list_sets` opened + parsed EVERY set file on
# every call, which the State Controller makes every 15 s (its shared poll)
# and the Apply-Set dropdown makes on every open. Two layers now stand in
# front of the parse:
#
#   1. LISTING layer, keyed on (sets_dir, the DIRECTORY's mtime_ns): the
#      finished summaries. A directory's mtime changes whenever an entry is
#      created, deleted or renamed inside it -- which is exactly what the
#      atomic temp+replace save does (verified on APFS by
#      tests/test_nas_io_round.py), so a save/delete from ANOTHER process or
#      machine is noticed by the one `stat` the fast path costs.
#   2. PER-FILE layer, keyed on (path, the FILE's mtime_ns, size): the
#      parsed summary. Consulted during a rescan, so only files that
#      actually changed are re-read.
#
# Why a rescan happens at all while the directory mtime is unchanged: an
# IN-PLACE modification (an editor that truncates and rewrites, rather
# than replacing the file) changes only the file's own mtime/size, and on
# SMB/NFS a client's attribute cache can hand back a stale directory mtime
# for a while. So a cached listing is trusted for at most LISTING_RESCAN_S
# before the per-file layer re-verifies every file (one scandir + one stat
# per file, no reads for unchanged files). A change made through this
# pack's routes is visible immediately; another machine's temp+replace save
# on the next listing (subject to that client's attribute cache); an
# in-place edit within LISTING_RESCAN_S.
#
# Concurrency: route handlers now call this from worker threads
# (`asyncio.to_thread`) alongside the execution thread (Apply Set's
# INPUT_TYPES). The dicts are only ever read/replaced whole under the GIL,
# so a lost race costs a redundant parse, never a torn value; the rescan
# itself is serialized by a lock so a tab-switch burst scans once.
#
# 2026-08-26 while-running round (findings 1 + 2): THIS process's own
# save/delete used to forget the LISTING entry outright (forcing the very
# next `list_sets()` call -- which routes_sets.py's save/delete handlers
# both make, to build their own response -- into a guaranteed full rescan,
# on top of the write that had just happened). `save_set`/`delete_set` now
# SPLICE the one changed slug into an already-cached listing instead
# (`_splice_listing_cache`), re-sorted with the exact same key `_scan_sets`
# uses (`_listing_sort_key`) so the result is provably identical to a fresh
# scan's order -- not merely "close enough" -- whenever a listing was
# already cached. A THIRD layer, `_load_cache`, backs `load_set` itself
# (finding 2: no caller-facing GET had ever been cached before this round).

#: How long a cached listing is trusted without re-stat'ing each file.
LISTING_RESCAN_S = 30.0

#: sets_dir -> (dir mtime_ns, monotonic time scanned, summaries).
_listing_cache: dict[Path, tuple[int, float, list[dict]]] = {}
#: set file path -> ((file mtime_ns, size), summary).
_entry_cache: dict[Path, tuple[tuple[int, int], dict]] = {}
#: set file path -> ((file mtime_ns, size), normalized set dict) -- backs
#: `load_set` (finding 2, 2026-08-26 while-running round). A separate map
#: from `_entry_cache` on purpose: that one holds the small LISTING summary
#: (`{slug, name, count}`), this one the FULL normalized set (`loras`/
#: `loaders`/`trigger_words`/`notes`) `GET /lora_library/set` actually
#: needs -- different shapes, different callers, no reason to force one
#: cache to serve both.
_load_cache: dict[Path, tuple[tuple[int, int], dict]] = {}
#: layout file path -> ((file mtime_ns, size), raw parsed JSON).
_layout_cache: dict[Path, tuple[tuple[int, int], object]] = {}
_rescan_lock = threading.Lock()


def clear_caches() -> None:
    """Forget every listing/per-file/load/layout memo (test seam + explicit
    invalidation)."""
    _listing_cache.clear()
    _entry_cache.clear()
    _load_cache.clear()
    _layout_cache.clear()


def _forget_listing(sets_dir: Path, path: Path | None = None) -> None:
    """This process changed *sets_dir* under anomalous circumstances (a
    delete that found the file already gone -- see :func:`delete_set`):
    drop its listing memo outright, and *path*'s per-file memos (entry +
    load) when given. The happy-path save/delete calls no longer route
    through here (they splice/warm instead -- :func:`_splice_listing_cache`,
    findings 1+2 of the 2026-08-26 while-running round); this stays the
    "forget, don't guess" fallback for when there's nothing accurate to
    preserve.
    """
    _listing_cache.pop(sets_dir, None)
    if path is not None:
        _entry_cache.pop(path, None)
        _load_cache.pop(path, None)


def _copy_summaries(summaries: list[dict]) -> list[dict]:
    return [dict(entry) for entry in summaries]


def _listing_sort_key(entry: dict) -> tuple[str, str]:
    """The listing's sort key: name (casefolded), then slug. Shared between
    :func:`_scan_sets` (a full rescan) and :func:`_splice_listing_cache` (a
    warm-cache patch after this process's own save/delete) so the two can
    never silently drift apart -- finding 1's order-parity guarantee rests
    entirely on both sorting by this ONE function, not two copies of the
    same tuple that could someday diverge.
    """
    return (entry["name"].casefold(), entry["slug"])


def _splice_listing_cache(
    context: LibraryContext, sets_dir: Path, slug: str, summary: dict | None
) -> None:
    """Patch an already-cached LISTING in place after THIS process's own
    save/delete, instead of forcing the immediate follow-up ``list_sets()``
    call (routes_sets.py's save/delete handlers both tail one onto their
    response, per FORMAT.md §5) to pay for a full rescan of every set file
    (audit 2026-08-26 while-running round, finding 1: before this,
    ``save_set``/``delete_set`` unconditionally evicted the whole listing
    memo, so that follow-up call was a GUARANTEED cold rescan -- a second
    NAS round trip stacked directly on top of the write itself, on every
    single save/delete).

    *summary* is the freshly-written ``{"slug", "name", "count"}`` for a
    save, or ``None`` for a delete (drop *slug*'s entry, add nothing). The
    result is re-sorted with :func:`_listing_sort_key` -- the exact key
    ``_scan_sets`` sorts its own output with -- so, GIVEN an already-accurate
    cached listing, splicing in the one entry that changed produces a list
    that is byte-for-byte what a fresh full scan would produce, not merely
    a reasonable approximation: every summary besides *slug*'s is untouched
    from a listing that was already correct, and a single deterministic
    sort over the same key can only ever land in one order.

    No-op when nothing is cached yet (cold start, the previous entry aged
    out past ``LISTING_RESCAN_S``, or a dir-mtime mismatch already evicted
    it) — there is no accurate base to splice into, so the next
    ``list_sets()`` call scans fresh exactly as it always did in that case
    (no regression, just no speedup either).
    """
    cached = _listing_cache.get(sets_dir)
    if cached is None:
        return
    _old_dir_key, _scanned_at, summaries = cached
    spliced = [entry for entry in summaries if entry["slug"] != slug]
    if summary is not None:
        spliced.append(dict(summary))
    spliced.sort(key=_listing_sort_key)
    try:
        # The atomic temp+replace write/unlink we're reacting to already
        # touched sets_dir's own mtime (see the section comment above), so
        # this stat is the NEW dir_key -- without re-stamping it here, the
        # very next list_sets() call would see today's mtime disagree with
        # the STALE one still stored alongside our splice and rescan anyway,
        # defeating the whole point.
        dir_key = _sets_dir_mtime_ns(context, sets_dir)
    except OSError:
        # The directory vanished between the write we're reacting to and
        # this stat -- nothing safe to splice; let it go cold so the next
        # list_sets() call's own OSError handling (an empty listing,
        # logged) takes over exactly as if this splice never ran.
        _listing_cache.pop(sets_dir, None)
        return
    _listing_cache[sets_dir] = (dir_key, time.monotonic(), spliced)


def _sets_dir_mtime_ns(context: LibraryContext, sets_dir: Path) -> int:
    """The sets directory's mtime_ns -- the LISTING layer's key. A vanished
    directory (``FileNotFoundError``) forgets the context's ensured-dir memo
    and re-creates it once; any other ``OSError`` propagates."""
    try:
        return sets_dir.stat().st_mtime_ns
    except FileNotFoundError:
        context.forget_ensured_dirs()
        return context.sets_dir().stat().st_mtime_ns


def _scan_sets(context: LibraryContext, sets_dir: Path) -> list[dict]:
    """One full pass over *sets_dir*: every ``*.json`` with a valid slug,
    re-parsed only when its (mtime_ns, size) differs from the PER-FILE
    memo. Returns the name-sorted summaries (the caller memoizes them)."""
    try:
        with os.scandir(sets_dir) as it:
            entries = list(it)
    except FileNotFoundError:
        context.forget_ensured_dirs()
        return []
    except OSError as exc:
        logger.warning("EPSNodes: could not list the sets folder %s (%s)", sets_dir, exc)
        return []

    summaries: list[dict] = []
    seen: set[Path] = set()
    for entry in entries:
        # fnmatch, not endswith: the same case rule Path.glob("*.json")
        # applied before the cache existed (case-insensitive on Windows).
        if not fnmatch.fnmatch(entry.name, "*.json"):
            continue
        slug = entry.name[: -len(".json")]
        if not _VALID_SLUG_RE.match(slug):
            logger.warning(
                "EPSNodes: ignoring %s — %r is not a valid set slug (FORMAT.md §4); "
                "rename the file to a valid slug (lowercase letters/digits/-/_, "
                "starting with a letter or digit) to make it usable",
                entry.name,
                slug,
            )
            continue
        path = sets_dir / entry.name
        seen.add(path)
        try:
            file_stat = entry.stat()
            file_key: tuple[int, int] | None = (file_stat.st_mtime_ns, file_stat.st_size)
        except OSError:
            file_key = None  # let load_set report whatever is wrong with it
        cached = _entry_cache.get(path)
        if file_key is not None and cached is not None and cached[0] == file_key:
            summaries.append(dict(cached[1]))
            continue
        try:
            data = load_set(context, slug)
        except SetValidationError as exc:
            logger.warning("EPSNodes: skipping unreadable set %r: %s", slug, exc)
            continue
        if data is None:  # vanished between scandir and open
            continue
        summary = {"slug": slug, "name": data["name"], "count": len(data["loras"])}
        if file_key is not None:
            _entry_cache[path] = (file_key, summary)
        summaries.append(dict(summary))
    # Prune memos for files that are no longer in this folder.
    for stale in [p for p in _entry_cache if p.parent == sets_dir and p not in seen]:
        _entry_cache.pop(stale, None)
    # Shared with _splice_listing_cache's warm-cache patch (findings 1 + 2,
    # 2026-08-26 while-running round) -- see _listing_sort_key's docstring
    # for why that sharing is the whole basis of its order-parity guarantee.
    summaries.sort(key=_listing_sort_key)
    return summaries


def list_sets(context: LibraryContext) -> list[dict]:
    """``[{"slug", "name", "count"}, …]`` for every set, sorted by name.

    A single unreadable/invalid file is logged and skipped rather than
    failing the whole listing — the same "one bad thing must not take down
    the rest" posture ``routes.build_routes`` and the pack's ``__init__.py``
    already use for feature modules/nodes. That includes a hand-created file
    whose stem isn't a valid slug (e.g. ``My Set.json``): listing it would
    advertise a slug every other route then 400s on, so it is skipped with
    a rename hint instead. The same never-crash posture covers the
    DIRECTORY itself: an unreachable library folder (``sets_dir()``'s
    on-demand mkdir raising OSError — an unmounted NAS) degrades to an
    empty listing with a logged warning rather than 500ing every route
    that tails a fresh listing onto its response (audit 2026-08-08).

    Cached (NAS round 2026-08-22, see the section comment above): the
    common call costs one ``stat`` of the sets directory; a rescan costs one
    ``scandir`` plus one ``stat`` per file; only CHANGED files are re-read.
    Always returns fresh copies -- callers may mutate the result freely.
    """
    try:
        sets_dir = context.sets_dir()
        dir_key = _sets_dir_mtime_ns(context, sets_dir)
    except OSError as exc:
        logger.warning(
            "EPSNodes: library sets folder is unreachable (%s); listing no sets", exc
        )
        return []
    cached = _listing_cache.get(sets_dir)
    if (
        cached is not None
        and cached[0] == dir_key
        and time.monotonic() - cached[1] < LISTING_RESCAN_S
    ):
        return _copy_summaries(cached[2])
    with _rescan_lock:
        cached = _listing_cache.get(sets_dir)  # a concurrent rescan may have landed
        if (
            cached is not None
            and cached[0] == dir_key
            and time.monotonic() - cached[1] < LISTING_RESCAN_S
        ):
            return _copy_summaries(cached[2])
        summaries = _scan_sets(context, sets_dir)
        _listing_cache[sets_dir] = (dir_key, time.monotonic(), summaries)
        return _copy_summaries(summaries)


# ------------------------------------------------------------- lora lookup

def _normalize_separators(value: str) -> str:
    """*value* with every ``\\`` flipped to ``/`` (FORMAT.md §4).

    ComfyUI's ``folder_paths.get_filename_list`` uses the OS's NATIVE
    separator, so the same subfoldered lora lists as
    ``styles\\film_grain.safetensors`` on the owner's Windows PC and
    ``styles/film_grain.safetensors`` on the Mac — and set files are shared
    between exactly those two machines. All comparisons in this section
    happen in this normalized form; the values *returned* to callers are
    always the installed originals.
    """
    return value.replace("\\", "/")


def _basename(value: str) -> str:
    """Last path segment of *value*, splitting across EITHER separator."""
    return _normalize_separators(value).rsplit("/", 1)[-1]


def resolve_lora(context: LibraryContext, file: str) -> str | None:
    """Resolve *file* against the installed lora list (FORMAT.md §4).

    SEPARATOR-INSENSITIVE, returning the INSTALLED spelling for this
    machine (never the set file's stored spelling — a set written on
    Windows carries ``\\`` and must still resolve here, and vice versa).
    Exact match after normalizing both sides' separators first — the common
    case, since ``file`` is normally written by this very resolution at
    save time. Otherwise, a *unique* basename match tolerates cross-machine
    subfolder differences (rgthree-style leniency). An AMBIGUOUS basename
    (two+ installed loras share it) is deliberately treated the same as
    "not found" rather than picking one arbitrarily — but it logs its own
    warning naming the candidates, so a user staring at a skipped lora can
    tell "ambiguous" apart from "truly missing" (the latter is silent here;
    the generic "could not resolve" warning belongs to the caller, e.g.
    ``nodes_sets.py``, per FORMAT.md §4's skip-with-logged-warning rule).
    """
    installed = context.list_loras()
    normalized_file = _normalize_separators(file)
    for candidate in installed:
        if _normalize_separators(candidate) == normalized_file:
            return candidate
    basename = _basename(file)
    matches = [candidate for candidate in installed if _basename(candidate) == basename]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        logger.warning(
            "EPSNodes: %r matches multiple installed loras by basename (%s); "
            "skipping rather than guessing — FORMAT.md §4",
            file,
            ", ".join(matches),
        )
    return None


# ------------------------------------------------- §4.2 sets LAYOUT (v0.65.0)
# Categories + display order for the State Controller's left pane (owner ask
# 2026-08-14: "the same ability to add a # to the left row and create groups
# as the lora notebooks"). The layout lives in its OWN file so set files (§4)
# never change shape: an older build neither reads nor writes it, so nothing
# is lost on downgrade -- the exact reasoning that kept picker favorites out
# of the workflow file. Self-healing on every read, like the picker's
# favorites_order: unknown slugs are dropped, sets missing from the layout
# are appended (name-sorted) to the UNCATEGORIZED tail, and duplicate slugs
# keep their first appearance. Two machines' concurrent writes are an
# accepted read-modify-write race that heals on the next write (the picker
# store's documented posture).

#: The file's own name, directly inside context.library_dir() -- a sibling
#: of lora_picker.json, deliberately NOT inside sets_dir() (list_sets globs
#: *.json there and would warn about it on every listing).
LAYOUT_FILENAME = "lora_sets_layout.json"

#: Uncategorized entries render before any category header (the Notebook's
#: own rule for entries above the first `#` heading).
UNCATEGORIZED = ""


def layout_path(context: LibraryContext) -> Path:
    return context.library_dir() / LAYOUT_FILENAME


def normalize_layout(raw: object) -> dict:
    """Coerce *raw* into ``{"categories": [str...], "order": {cat: [slugs]}}``.

    Tolerant, never raises: a malformed file/body degrades to an empty
    layout (healing then rebuilds it from the sets on disk). Category names
    are stripped strings, deduplicated case-sensitively, with the empty
    (uncategorized) name excluded from ``categories`` -- it is implicit and
    always first. Order lists keep only string slugs.
    """
    categories: list[str] = []
    order: dict[str, list[str]] = {}
    if isinstance(raw, dict):
        raw_categories = raw.get("categories")
        if isinstance(raw_categories, list):
            for entry in raw_categories:
                if not isinstance(entry, str):
                    continue
                name = entry.strip()
                if name and name not in categories:
                    categories.append(name)
        raw_order = raw.get("order")
        if isinstance(raw_order, dict):
            for key, slugs in raw_order.items():
                if not isinstance(key, str) or not isinstance(slugs, list):
                    continue
                name = key.strip()
                if name and name not in categories:
                    categories.append(name)
                order[name] = [s for s in slugs if isinstance(s, str)]
    for name in categories:
        order.setdefault(name, [])
    order.setdefault(UNCATEGORIZED, [])
    return {"categories": categories, "order": order}


def healed_layout(context: LibraryContext, raw: object) -> dict:
    """*raw* normalized, then reconciled against the sets actually on disk:
    every existing slug appears exactly ONCE (first appearance wins), slugs
    with no set file are dropped, and sets absent from the layout append to
    the uncategorized tail in name order -- so a set saved on another
    machine (or by an older build that never writes layouts) always shows
    up rather than silently vanishing from the pane."""
    layout = normalize_layout(raw)
    entries = list_sets(context)  # ONE scan (audit 2026-08-21: it was two per call)
    existing = {entry["slug"] for entry in entries}
    seen: set[str] = set()
    for name in [UNCATEGORIZED, *layout["categories"]]:
        kept = []
        for slug in layout["order"].get(name, []):
            if slug not in existing or slug in seen:
                continue
            seen.add(slug)
            kept.append(slug)
        layout["order"][name] = kept
    missing = [e["slug"] for e in entries if e["slug"] not in seen]
    layout["order"][UNCATEGORIZED].extend(missing)
    return layout


def load_layout(context: LibraryContext) -> dict:
    """The healed layout currently on disk (a missing/unreadable file is an
    empty layout -- healing fills in every set, uncategorized).

    The file's parsed JSON is memoized on its (mtime_ns, size) (NAS round
    2026-08-22): one ``stat`` answers the unchanged case, and healing
    itself rides the cached :func:`list_sets`, so the controller's 15 s
    layout poll normally costs two ``stat``s and no reads.
    """
    try:
        path = layout_path(context)
        try:
            file_stat = path.stat()
        except FileNotFoundError:
            return healed_layout(context, None)
        if not stat_module.S_ISREG(file_stat.st_mode):
            return healed_layout(context, None)
        key = (file_stat.st_mtime_ns, file_stat.st_size)
        cached = _layout_cache.get(path)
        if cached is not None and cached[0] == key:
            raw = cached[1]
        else:
            raw = json.loads(path.read_text(encoding="utf-8"))
            _layout_cache[path] = (key, raw)
    except (OSError, ValueError) as exc:
        logger.warning("EPSNodes: unreadable sets layout (%s); rebuilding", exc)
        raw = None
    return healed_layout(context, raw)


def save_layout(context: LibraryContext, raw: object) -> dict:
    """Normalize + heal *raw*, write atomically, return what was written."""
    layout = healed_layout(context, raw)
    path = layout_path(context)
    _atomic_write_text(path, json.dumps(layout, indent=2))
    _layout_cache.pop(path, None)
    return layout
