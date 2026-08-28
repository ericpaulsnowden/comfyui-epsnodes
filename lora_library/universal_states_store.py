"""On-disk storage for UNIVERSAL STATES (FORMAT.md §4.3) -- the M1 backend
for the "EPS Universal State Controller": named snapshots of many nodes'
widget values, saved one JSON file per state under
``context.library_dir() / "states"`` (a sibling of the existing
``library/sets/`` -- FORMAT.md §4), plus a §4.2-style layout sidecar
(``universal_states_layout.json``) for the panel's GROUPS.

Modeled directly on ``sets_store.py`` -- read that module's own docstring
and section comments first, they explain every mechanism copy-adapted
here: slug derivation/collision numbering, atomic writes (via
``context._atomic_write_text``, gvfs/FUSE EEXIST fallback included), the
three-layer LISTING/per-file-ENTRY/per-file-LOAD cache with the
NAS-round-2026-08-22 trust window, the 2026-08-26 while-running round's
save/delete SPLICE-not-forget cache warming (``_splice_listing_cache``,
sharing ``_listing_sort_key`` with a full rescan for provable order
parity), and the §4.2 layout sidecar's self-healing load/save. Function
names match ``sets_store.py``'s wherever the concept matches, so the two
modules read as one family; they diverge only where Universal States
genuinely differ from LoRA sets (registry-validated widgets instead of
lora rows, a "foreign class" tolerance list, no §4.1 composite schema).

No ComfyUI imports at module scope -- same importable-without-ComfyUI seam
``context.py`` and ``sets_store.py`` already keep. Registry validation
needs ComfyUI's live node classes (to read their ``EPS_STATE_WIDGETS``
class attribute, see :func:`_state_registry`), so that ONE lookup lazily
imports ComfyUI's own ``nodes`` module -- inside the function that needs
it, never at module scope, mirroring
``eps_image/nodes_save_image.py``'s ``_pinnable_class`` -- but reaches for
``nodes.NODE_CLASS_MAPPINGS`` (the same global registry
``eps_image/routes_list_flags.py``'s list-flags feed reads) rather than a
hand-picked handful of this pack's own classes: a Universal State can
capture ANY state-bearing node on the canvas (FORMAT.md §6.16's "the 14
core nodes" today, more later), not just this pack's own.
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
from datetime import datetime, timezone
from pathlib import Path

from .context import LibraryContext, _atomic_write_text

logger = logging.getLogger("lora_library")

#: FORMAT.md §4.3 -- the highest ``format`` value this reader understands.
CURRENT_FORMAT = 1

#: ``<library_dir>/states`` -- a sibling of ``sets_store.py``'s own
#: ``SETS_DIRNAME``. ``context.py`` only defines ``sets_dir()`` itself (no
#: ``states_dir()``); rather than touch that shared module, this store
#: builds its own directory the same way, reusing ``context._ensure_dir``'s
#: existing NAS-friendly at-most-once-per-``ENSURED_DIR_TTL_S`` mkdir memo
#: (see :func:`states_dir`) instead of re-inventing that TTL logic here.
STATES_DIRNAME = "states"

#: Characters kept by :func:`slugify` -- identical rule to
#: ``sets_store.slugify`` (FORMAT.md §4), copied verbatim so a state slug
#: and a set slug are governed by exactly the same collision-free naming
#: scheme.
_SLUG_DISALLOWED_RE = re.compile(r"[^a-z0-9\-_]")
_WHITESPACE_RE = re.compile(r"\s+")

#: What counts as a valid on-disk slug -- mirrors ``routes.SLUG_RE`` (must
#: stay in lockstep with it; duplicated rather than imported for the same
#: layering reason ``sets_store.py`` documents: the HTTP layer builds on
#: this store, not the other way around).
_VALID_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-_]*$")

#: The closed set of widget "kind"s a registry entry may declare
#: (FORMAT.md §4.3 / §6.16's M0 registry). ``choice`` and ``lines`` accept
#: any string -- see :func:`_check_kind`'s docstring for why.
_SCALAR_KINDS = frozenset({"string", "lines", "choice", "int", "float"})


class StateValidationError(ValueError):
    """Raised when a universal-state payload/file doesn't match
    FORMAT.md §4.3.

    Always carries a human-readable message safe to surface verbatim in an
    HTTP ``{"error": ...}`` body (``routes_universal_states.py`` does
    exactly that) -- mirrors ``sets_store.SetValidationError``'s posture.
    """


# ------------------------------------------------------------------- slugify

def slugify(name: str) -> str:
    """Filename stem for a state's JSON file, derived from its display
    *name*. Byte-identical rule to ``sets_store.slugify`` -- see that
    function's docstring for the full rationale (unicode/emoji dropped
    rather than transliterated, leading/trailing ``-``/``_`` trimmed so the
    result always satisfies ``routes.SLUG_RE``). Collision numbering is NOT
    this function's job -- see :func:`_unique_slug`.
    """
    slug = (name or "").strip().lower()
    slug = _WHITESPACE_RE.sub("-", slug)
    slug = _SLUG_DISALLOWED_RE.sub("", slug)
    slug = slug.strip("-_")
    return slug or "state"


def states_dir(context: LibraryContext) -> Path:
    """``<library_dir>/states`` (FORMAT.md §4.3), created.

    ``context.py`` doesn't define this itself (only ``sets_dir()``) --
    rather than add one more method to a file shared with concurrent
    work, this calls the SAME ``_ensure_dir`` memo ``sets_dir()`` already
    uses internally, so this store gets the identical
    created-at-most-once-per-``ENSURED_DIR_TTL_S`` NAS behavior without
    duplicating that TTL bookkeeping here. Raises ``OSError`` when the
    configured library folder is unreachable, exactly like ``sets_dir()``.
    """
    directory = context.library_dir() / STATES_DIRNAME
    context._ensure_dir(directory)
    return directory


def state_path(context: LibraryContext, slug: str) -> Path:
    """``<states_dir>/<slug>.json`` -- the single source of truth for the
    name (mirrors ``sets_store.set_path``)."""
    return states_dir(context) / f"{slug}.json"


def _require_states_dir(context: LibraryContext) -> Path:
    """``states_dir(context)``, with an unreachable library folder surfaced
    as :class:`StateValidationError` (a 400 at the route) instead of a raw
    ``OSError`` (a 500) -- mirrors ``sets_store._require_sets_dir``."""
    try:
        return states_dir(context)
    except OSError as exc:
        raise StateValidationError(
            f"the library folder is unreachable ({exc}); Universal States "
            "live inside it -- fix or remount the configured library "
            "folder and retry"
        ) from exc


def _unique_slug(context: LibraryContext, base: str) -> str:
    """*base*, or ``<base>-2``, ``<base>-3``, … -- whichever isn't on disk
    yet (mirrors ``sets_store._unique_slug``)."""
    candidate = base
    suffix = 2
    while state_path(context, candidate).exists():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _utc_now() -> str:
    """ISO-8601 UTC, second precision, ``Z`` suffix -- a state's default
    ``captured`` when the caller/frontend didn't stamp one itself."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------- §6.16 registry (M0, v0.83.0)
# A KNOWN class declares ``EPS_STATE_WIDGETS = {"format": 1, "widgets":
# {name: {"kind": ..., ...constraints}}, "excluded": {name: "reason"}}`` as
# a class attribute (see e.g. lora_library/nodes_sets.py's
# ``LoraLibraryApplySet.EPS_STATE_WIDGETS``). This store never hand-parses
# a node's own shape -- it always asks the class itself.

def _state_registry(class_type: str) -> dict | None:
    """The ``EPS_STATE_WIDGETS`` class attribute declared by *class_type*'s
    LIVE node class, or ``None`` when the class isn't loaded, isn't found,
    or doesn't declare one -- ALL three read identically here ("unknown
    class"): a future EPSNodes build, a third-party pack, or simply a
    class this process hasn't loaded yet are indistinguishable from here,
    and FORMAT.md §4.3's answer is the same for all three ("foreign":
    tolerate on load, warn on save -- see :func:`normalize_state`).

    Lazy, never at module scope, no torch (mirrors
    ``eps_image/nodes_save_image.py``'s ``_pinnable_class``) -- but reaches
    for ComfyUI's OWN global node registry (``nodes.NODE_CLASS_MAPPINGS``,
    the same source ``eps_image/routes_list_flags.py``'s list-flags feed
    reads) rather than this pack's own three hand-picked classes, because a
    Universal State can name ANY state-bearing node on the canvas.
    """
    try:
        import nodes  # ComfyUI's own module; only importable inside ComfyUI
    except ImportError:
        return None
    mappings = getattr(nodes, "NODE_CLASS_MAPPINGS", None)
    if not isinstance(mappings, dict):
        return None
    cls = mappings.get(class_type)
    if cls is None:
        return None
    try:
        registry = getattr(cls, "EPS_STATE_WIDGETS", None)
    except Exception:  # a broken third-party class must not break validation
        logger.debug(
            "EPSNodes: could not read EPS_STATE_WIDGETS from %r", class_type
        )
        return None
    return registry if isinstance(registry, dict) else None


def _check_range(label: str, spec: dict, value: float) -> None:
    """``min``/``max`` bounds check shared by the ``int``/``float`` kinds."""
    minimum = spec.get("min")
    maximum = spec.get("max")
    if isinstance(minimum, (int, float)) and not isinstance(minimum, bool) and value < minimum:
        raise StateValidationError(f"{label} must be >= {minimum} — FORMAT.md §4.3")
    if isinstance(maximum, (int, float)) and not isinstance(maximum, bool) and value > maximum:
        raise StateValidationError(f"{label} must be <= {maximum} — FORMAT.md §4.3")


def _check_kind(label: str, spec: dict, value: object) -> object:
    """Validate *value* against registry *spec*'s ``kind`` (FORMAT.md
    §4.3's closed kind set), returning the value to store. ONE small,
    dependency-free dispatcher -- every kind is checked with a plain
    ``isinstance``/range test, no schema library:

    - ``string``: any string, optionally capped by ``spec["max_len"]``.
    - ``lines``/``choice``: any string, unconstrained -- a ``choice``
      widget's valid OPTIONS are machine-specific (installed loras,
      checkpoints, ...), so the registry can't enumerate them here; the
      APPLIER checks against the live widget's options at apply time.
    - ``int``/``float``: a non-bool number of the right kind (``bool`` is
      an ``int`` subclass in Python -- rejected explicitly, same guard
      ``sets_store._coerce_float`` uses), optionally bounded by
      ``spec["min"]``/``spec["max"]``. A ``float`` kind accepts an int
      value too (a JSON ``1`` for a ``1.0`` field) and coerces it, mirroring
      ``sets_store._coerce_float``'s precedent.
    - ``json_array``: a list; if ``spec["items"]`` names one of the
      SCALAR kinds above, every element is checked against it (recursing
      into this same dispatcher) -- an absent/unrecognized ``items`` means
      "any JSON-compatible element, unconstrained".
    - ``json_object``: a dict; if ``spec["key_pattern"]`` is given, every
      key must ``re.fullmatch`` it (a pattern-keyed map, e.g. per-loader
      settings) -- absent means any string keys are fine.

    Raises :class:`StateValidationError` for anything else, including a
    ``spec`` naming a kind this dispatcher doesn't recognize (a newer
    pack's registry entry that this build doesn't understand yet).
    """
    kind = spec.get("kind") if isinstance(spec, dict) else None
    if kind in ("string", "lines", "choice"):
        if not isinstance(value, str):
            raise StateValidationError(f"{label} must be a string — FORMAT.md §4.3")
        max_len = spec.get("max_len")
        if kind == "string" and isinstance(max_len, int) and len(value) > max_len:
            raise StateValidationError(
                f"{label} must be at most {max_len} characters — FORMAT.md §4.3"
            )
        return value
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise StateValidationError(f"{label} must be an integer — FORMAT.md §4.3")
        _check_range(label, spec, value)
        return value
    if kind == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise StateValidationError(f"{label} must be a number — FORMAT.md §4.3")
        value = float(value)
        _check_range(label, spec, value)
        return value
    if kind == "json_array":
        if not isinstance(value, list):
            raise StateValidationError(f"{label} must be a list — FORMAT.md §4.3")
        items_kind = spec.get("items")
        if items_kind in _SCALAR_KINDS:
            for index, item in enumerate(value):
                _check_kind(f"{label}[{index}]", {"kind": items_kind}, item)
        return list(value)
    if kind == "json_object":
        if not isinstance(value, dict):
            raise StateValidationError(f"{label} must be an object — FORMAT.md §4.3")
        key_pattern = spec.get("key_pattern")
        if isinstance(key_pattern, str) and key_pattern:
            compiled = re.compile(key_pattern)
            for key in value:
                if not isinstance(key, str) or not compiled.fullmatch(key):
                    raise StateValidationError(
                        f"{label}: key {key!r} does not match the registry's "
                        f"key_pattern {key_pattern!r} — FORMAT.md §4.3"
                    )
        return dict(value)
    raise StateValidationError(f"{label}: unsupported registry kind {kind!r} — FORMAT.md §4.3")


def _normalize_node_entry(index: int, entry: object) -> tuple[dict, bool]:
    """One ``nodes[]`` entry, validated -- returns ``(normalized, is_foreign)``.

    A KNOWN class (:func:`_state_registry` returns a dict) has every
    ``widgets`` key checked: a key in the registry's ``excluded`` map, or
    one absent from its ``widgets`` map entirely, is rejected by NAME
    (class + widget) -- the saver is our own frontend, so a widget outside
    the declared scope is a bug, not a hand-edit to tolerate. An UNKNOWN
    class is passed through untouched (its ``widgets`` dict copied as-is,
    no per-key checking possible) and flagged foreign -- FORMAT.md §4.3's
    forward-compat rule: a state saved by a NEWER pack (a node class this
    older build hasn't loaded), or one referencing a third-party node, must
    still round-trip through an older/other build rather than being
    rejected outright.
    """
    if not isinstance(entry, dict):
        raise StateValidationError(f"nodes[{index}] must be an object — FORMAT.md §4.3")
    class_type = entry.get("class")
    if not isinstance(class_type, str) or not class_type:
        raise StateValidationError(f"nodes[{index}] is missing a 'class' — FORMAT.md §4.3")
    node_id = entry.get("id")
    if not isinstance(node_id, str) or not node_id:
        raise StateValidationError(f"nodes[{index}] is missing an 'id' — FORMAT.md §4.3")
    title = entry.get("title", "")
    if not isinstance(title, str):
        raise StateValidationError(f"nodes[{index}].title must be a string — FORMAT.md §4.3")
    widgets_raw = entry.get("widgets")
    if not isinstance(widgets_raw, dict):
        raise StateValidationError(f"nodes[{index}].widgets must be an object — FORMAT.md §4.3")

    registry = _state_registry(class_type)
    if registry is None:
        return {
            "class": class_type,
            "id": node_id,
            "title": title,
            "widgets": dict(widgets_raw),
        }, True

    declared = registry.get("widgets")
    declared = declared if isinstance(declared, dict) else {}
    excluded = registry.get("excluded")
    excluded = excluded if isinstance(excluded, dict) else {}

    widgets_out: dict[str, object] = {}
    for widget_name, value in widgets_raw.items():
        if widget_name in excluded:
            raise StateValidationError(
                f"{class_type}.{widget_name} is excluded from Universal State "
                f"({excluded[widget_name]}) — FORMAT.md §4.3"
            )
        spec = declared.get(widget_name)
        if not isinstance(spec, dict):
            raise StateValidationError(
                f"{class_type}.{widget_name} is not a declared state widget "
                "— FORMAT.md §4.3"
            )
        widgets_out[widget_name] = _check_kind(f"{class_type}.{widget_name}", spec, value)
    return {
        "class": class_type,
        "id": node_id,
        "title": title,
        "widgets": widgets_out,
    }, False


def normalize_state(raw: object) -> tuple[dict, list[str]]:
    """Validate *raw* (parsed JSON or a request body's ``state``) into a
    canonical FORMAT.md §4.3 dict, applying every documented default.

    Returns ``(normalized, foreign)`` -- a deliberate divergence from
    ``sets_store.normalize_set``'s bare-dict return: *foreign* is the
    de-duplicated, first-seen-order list of node CLASSES in *raw* that had
    no registry entry (:func:`_state_registry` returned ``None``), computed
    in the SAME pass that builds ``normalized["nodes"]`` rather than a
    second walk. ``save_state`` threads it through so the save route can
    echo it as a loud (but non-fatal) warning -- FORMAT.md §4.3's
    documented policy: a state referencing an unknown/third-party/
    not-yet-loaded class is *saved as-is* (never rejected -- a state saved
    by a newer pack build must survive a round trip through an older one)
    but the save response calls it out so the user isn't surprised later
    when that entry silently no-ops on apply.

    Raises :class:`StateValidationError` -- never a bare ``KeyError``/
    ``TypeError`` -- for every other validation failure (non-dict payload,
    bad field types, a ``format`` newer than this pack understands, a
    malformed/rejected node entry).
    """
    if not isinstance(raw, dict):
        raise StateValidationError("a universal state must be a JSON object — FORMAT.md §4.3")

    fmt = raw.get("format", 1)
    if not isinstance(fmt, int) or isinstance(fmt, bool):
        raise StateValidationError("state 'format' must be an integer — FORMAT.md §4.3")
    if fmt > CURRENT_FORMAT:
        raise StateValidationError(
            f"this state was saved by a newer version of the pack (format {fmt}); "
            "update the pack — FORMAT.md §4.3"
        )

    name = raw.get("name", "")
    if not isinstance(name, str):
        raise StateValidationError("state 'name' must be a string — FORMAT.md §4.3")
    if not name.strip():
        raise StateValidationError("state 'name' is required — FORMAT.md §4.3")

    notes = raw.get("notes", "")
    if not isinstance(notes, str):
        raise StateValidationError("state 'notes' must be a string — FORMAT.md §4.3")

    captured = raw.get("captured", "")
    if not isinstance(captured, str):
        raise StateValidationError("state 'captured' must be a string — FORMAT.md §4.3")

    nodes_raw = raw.get("nodes", [])
    if not isinstance(nodes_raw, list):
        raise StateValidationError("state 'nodes' must be a list — FORMAT.md §4.3")

    nodes_out: list[dict] = []
    foreign: list[str] = []
    seen_foreign: set[str] = set()
    for index, entry in enumerate(nodes_raw):
        normalized_entry, is_foreign = _normalize_node_entry(index, entry)
        nodes_out.append(normalized_entry)
        if is_foreign and normalized_entry["class"] not in seen_foreign:
            seen_foreign.add(normalized_entry["class"])
            foreign.append(normalized_entry["class"])

    normalized = {
        "format": CURRENT_FORMAT,
        "name": name,
        "notes": notes,
        "captured": captured or _utc_now(),
        "nodes": nodes_out,
    }
    return normalized, foreign


# -------------------------------------------------------------- persistence

def _state_summary(slug: str, normalized: dict) -> dict:
    return {
        "slug": slug,
        "name": normalized["name"],
        "count": len(normalized["nodes"]),
        "captured": normalized["captured"],
    }


def load_state(context: LibraryContext, slug: str) -> dict | None:
    """The normalized state at *slug*, or ``None`` if no such file exists.

    Mirrors ``sets_store.load_set`` exactly, including its per-file
    ``(mtime_ns, size)`` LOAD cache (NAS round 2026-08-26, finding 2): the
    STAT runs on every call (the freshness check), but the read+parse+
    :func:`normalize_state` validation is skipped on a warm hit. Returns a
    deep copy either way, so callers may mutate the result freely.
    """
    _require_states_dir(context)
    path = state_path(context, slug)
    try:
        file_stat = path.stat()
    except FileNotFoundError:
        context.forget_ensured_dirs()
        return None
    except OSError as exc:
        raise StateValidationError(f"could not read state {slug!r}: {exc}") from exc
    file_key = (file_stat.st_mtime_ns, file_stat.st_size)
    cached = _load_cache.get(path)
    if cached is not None and cached[0] == file_key:
        return copy.deepcopy(cached[1])
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        context.forget_ensured_dirs()
        return None
    except OSError as exc:
        raise StateValidationError(f"could not read state {slug!r}: {exc}") from exc
    except ValueError as exc:  # UnicodeDecodeError is a ValueError subclass
        raise StateValidationError(f"state {slug!r} could not be read: {exc}") from exc
    normalized, _foreign = normalize_state(raw)
    _load_cache[path] = (file_key, copy.deepcopy(normalized))
    return normalized


def save_state(
    context: LibraryContext, state_data: dict, slug: str | None = None
) -> tuple[str, dict, list[str]]:
    """Normalize and atomically persist *state_data*. Returns ``(slug,
    normalized, foreign)`` -- *foreign* is :func:`normalize_state`'s own
    return, threaded through so the route can echo it.

    *slug* omitted (a brand-new state) derives one from
    ``state_data["name"]`` (:func:`slugify`), de-duplicated against what's
    on disk; a caller-supplied *slug* is used as-is (a rename must not move
    the file out from under a saved reference). Every warm-cache-splice
    mechanic mirrors ``sets_store.save_set`` verbatim, including WHY
    (2026-08-26 while-running round, findings 1+2): warming the per-file
    ENTRY/LOAD caches from the write's own fresh stat, and splicing the
    listing cache in place, so the save route's own follow-up ``list_states``
    call (building its response) costs neither a rescan nor a re-parse.
    """
    normalized, foreign = normalize_state(state_data)
    states_dir_path = _require_states_dir(context)
    if slug is None:
        slug = _unique_slug(context, slugify(normalized["name"]))
    text = json.dumps(normalized, indent=2, ensure_ascii=False) + "\n"
    path = state_path(context, slug)
    _atomic_write_text(path, text)
    summary = _state_summary(slug, normalized)
    try:
        file_stat = path.stat()
        file_key = (file_stat.st_mtime_ns, file_stat.st_size)
        _entry_cache[path] = (file_key, dict(summary))
        _load_cache[path] = (file_key, copy.deepcopy(normalized))
    except OSError:
        _entry_cache.pop(path, None)
        _load_cache.pop(path, None)
    _splice_listing_cache(context, states_dir_path, slug, summary)
    return slug, normalized, foreign


def delete_state(context: LibraryContext, slug: str) -> bool:
    """Delete the state at *slug*. ``True`` if a file was removed, else
    ``False``. Mirrors ``sets_store.delete_set`` exactly, splice included."""
    states_dir_path = _require_states_dir(context)
    path = state_path(context, slug)
    try:
        path.unlink()
    except FileNotFoundError:
        _forget_listing(states_dir_path, path)
        context.forget_ensured_dirs()
        return False
    _entry_cache.pop(path, None)
    _load_cache.pop(path, None)
    _splice_listing_cache(context, states_dir_path, slug, None)
    return True


# ----------------------------------------------------- listing caches (2026-08-22)
# Identical three-layer scheme to sets_store.py's own section comment
# (LISTING keyed on the directory's mtime_ns, per-file ENTRY summaries,
# per-file LOAD -- see that module for the full NAS-round rationale). Kept
# as a SEPARATE set of module-level dicts (not shared with sets_store's own
# caches) -- different directory, different file shape, no reason to force
# one cache to serve both stores.

#: How long a cached listing is trusted without re-stat'ing each file --
#: same trust window as ``sets_store.LISTING_RESCAN_S``, for the same
#: reasoning (a tab-switch burst collapses to one syscall; a NAS library
#: unmounted underneath a running server is still noticed promptly).
LISTING_RESCAN_S = 30.0

_listing_cache: dict[Path, tuple[int, float, list[dict]]] = {}
_entry_cache: dict[Path, tuple[tuple[int, int], dict]] = {}
_load_cache: dict[Path, tuple[tuple[int, int], dict]] = {}
_layout_cache: dict[Path, tuple[tuple[int, int], object]] = {}
_rescan_lock = threading.Lock()


def clear_caches() -> None:
    """Forget every listing/per-file/load/layout memo (test seam + explicit
    invalidation) -- mirrors ``sets_store.clear_caches``."""
    _listing_cache.clear()
    _entry_cache.clear()
    _load_cache.clear()
    _layout_cache.clear()


def _forget_listing(states_dir_path: Path, path: Path | None = None) -> None:
    """Mirrors ``sets_store._forget_listing`` -- the "forget, don't guess"
    fallback for a delete that found the file already gone."""
    _listing_cache.pop(states_dir_path, None)
    if path is not None:
        _entry_cache.pop(path, None)
        _load_cache.pop(path, None)


def _copy_summaries(summaries: list[dict]) -> list[dict]:
    return [dict(entry) for entry in summaries]


def _listing_sort_key(entry: dict) -> tuple[str, str]:
    """The listing's sort key: name (casefolded), then slug -- shared
    between :func:`_scan_states` (a full rescan) and
    :func:`_splice_listing_cache` (a warm-cache patch) so the two can never
    silently drift apart, exactly ``sets_store._listing_sort_key``'s own
    order-parity guarantee."""
    return (entry["name"].casefold(), entry["slug"])


def _splice_listing_cache(
    context: LibraryContext, states_dir_path: Path, slug: str, summary: dict | None
) -> None:
    """Patch an already-cached LISTING in place after THIS process's own
    save/delete -- mirrors ``sets_store._splice_listing_cache`` verbatim
    (see its docstring for the full order-parity proof). *summary* is the
    freshly-written summary for a save, or ``None`` for a delete. A no-op
    when nothing is cached yet -- the next ``list_states()`` call scans
    fresh exactly as it always did."""
    cached = _listing_cache.get(states_dir_path)
    if cached is None:
        return
    _old_dir_key, _scanned_at, summaries = cached
    spliced = [entry for entry in summaries if entry["slug"] != slug]
    if summary is not None:
        spliced.append(dict(summary))
    spliced.sort(key=_listing_sort_key)
    try:
        dir_key = _states_dir_mtime_ns(context, states_dir_path)
    except OSError:
        _listing_cache.pop(states_dir_path, None)
        return
    _listing_cache[states_dir_path] = (dir_key, time.monotonic(), spliced)


def _states_dir_mtime_ns(context: LibraryContext, states_dir_path: Path) -> int:
    """The states directory's mtime_ns -- the LISTING layer's key. Mirrors
    ``sets_store._sets_dir_mtime_ns``."""
    try:
        return states_dir_path.stat().st_mtime_ns
    except FileNotFoundError:
        context.forget_ensured_dirs()
        return states_dir(context).stat().st_mtime_ns


def _scan_states(context: LibraryContext, states_dir_path: Path) -> list[dict]:
    """One full pass over *states_dir_path*: every ``*.json`` with a valid
    slug, re-parsed only when its ``(mtime_ns, size)`` differs from the
    PER-FILE memo. Mirrors ``sets_store._scan_sets`` exactly."""
    try:
        with os.scandir(states_dir_path) as it:
            entries = list(it)
    except FileNotFoundError:
        context.forget_ensured_dirs()
        return []
    except OSError as exc:
        logger.warning(
            "EPSNodes: could not list the universal states folder %s (%s)",
            states_dir_path,
            exc,
        )
        return []

    summaries: list[dict] = []
    seen: set[Path] = set()
    for entry in entries:
        if not fnmatch.fnmatch(entry.name, "*.json"):
            continue
        slug = entry.name[: -len(".json")]
        if not _VALID_SLUG_RE.match(slug):
            logger.warning(
                "EPSNodes: ignoring %s — %r is not a valid state slug "
                "(FORMAT.md §4.3); rename the file to a valid slug "
                "(lowercase letters/digits/-/_, starting with a letter or "
                "digit) to make it usable",
                entry.name,
                slug,
            )
            continue
        path = states_dir_path / entry.name
        seen.add(path)
        try:
            file_stat = entry.stat()
            file_key: tuple[int, int] | None = (file_stat.st_mtime_ns, file_stat.st_size)
        except OSError:
            file_key = None  # let load_state report whatever is wrong with it
        cached = _entry_cache.get(path)
        if file_key is not None and cached is not None and cached[0] == file_key:
            summaries.append(dict(cached[1]))
            continue
        try:
            data = load_state(context, slug)
        except StateValidationError as exc:
            logger.warning("EPSNodes: skipping unreadable universal state %r: %s", slug, exc)
            continue
        if data is None:  # vanished between scandir and open
            continue
        summary = _state_summary(slug, data)
        if file_key is not None:
            _entry_cache[path] = (file_key, summary)
        summaries.append(dict(summary))
    for stale in [p for p in _entry_cache if p.parent == states_dir_path and p not in seen]:
        _entry_cache.pop(stale, None)
    summaries.sort(key=_listing_sort_key)
    return summaries


def list_states_with_mtime(context: LibraryContext) -> tuple[list[dict], float]:
    """``(list_states(context), the states directory's own mtime in float
    seconds)``, computed from a SINGLE stat.

    ``routes_universal_states.py``'s GET listing response wants both the
    listing itself and a raw freshness ``mtime`` for the panel's shared
    poll (FORMAT.md §5) -- stat'ing the directory twice per request (once
    here, once inside a plain ``list_states`` call's own cache-key check)
    would cost a NAS-backed library a redundant round trip on every single
    poll, so this is the one entry point that does both from one stat.
    :func:`list_states` is a thin wrapper around this for every OTHER
    caller that doesn't need the mtime.
    """
    try:
        states_dir_path = states_dir(context)
        dir_stat = states_dir_path.stat()
    except OSError as exc:
        logger.warning(
            "EPSNodes: library states folder is unreachable (%s); listing no states", exc
        )
        return [], 0.0
    dir_key = dir_stat.st_mtime_ns
    cached = _listing_cache.get(states_dir_path)
    if (
        cached is not None
        and cached[0] == dir_key
        and time.monotonic() - cached[1] < LISTING_RESCAN_S
    ):
        return _copy_summaries(cached[2]), dir_stat.st_mtime
    with _rescan_lock:
        cached = _listing_cache.get(states_dir_path)  # a concurrent rescan may have landed
        if (
            cached is not None
            and cached[0] == dir_key
            and time.monotonic() - cached[1] < LISTING_RESCAN_S
        ):
            return _copy_summaries(cached[2]), dir_stat.st_mtime
        summaries = _scan_states(context, states_dir_path)
        _listing_cache[states_dir_path] = (dir_key, time.monotonic(), summaries)
        return _copy_summaries(summaries), dir_stat.st_mtime


def list_states(context: LibraryContext) -> list[dict]:
    """``[{"slug", "name", "count", "captured"}, …]`` for every state,
    sorted by name. Mirrors ``sets_store.list_sets``'s degrade-on-error
    posture (an unreadable file, or an unreachable folder, is logged and
    skipped/empty rather than failing the whole listing). A thin wrapper
    around :func:`list_states_with_mtime` for callers that don't need the
    directory mtime."""
    return list_states_with_mtime(context)[0]


# ---------------------------------------- §4.2-style layout sidecar (v0.83.0)
# Categories + display order for the Universal State Controller's left
# pane -- byte-for-byte the same mechanism as sets_store.py's own §4.2
# section (self-healing, own file so state files never change shape). See
# that module's section comment for the full rationale; only the filename
# and the backing listing function (`list_states` instead of `list_sets`)
# differ.

#: The file's own name, directly inside ``context.library_dir()`` -- a
#: sibling of ``lora_sets_layout.json``, deliberately NOT inside
#: ``states_dir()`` (``list_states`` globs ``*.json`` there and would warn
#: about it on every listing).
LAYOUT_FILENAME = "universal_states_layout.json"

#: Uncategorized entries render before any category header.
UNCATEGORIZED = ""


def layout_path(context: LibraryContext) -> Path:
    return context.library_dir() / LAYOUT_FILENAME


def normalize_layout(raw: object) -> dict:
    """Coerce *raw* into ``{"categories": [str...], "order": {cat:
    [slugs]}}``. Byte-identical logic to ``sets_store.normalize_layout`` --
    tolerant, never raises; a malformed file/body degrades to an empty
    layout (healing then rebuilds it from the states on disk)."""
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
    """*raw* normalized, then reconciled against the states actually on
    disk -- byte-identical logic to ``sets_store.healed_layout`` (every
    existing slug appears exactly once, unknown slugs dropped, missing
    states appended uncategorized in name order)."""
    layout = normalize_layout(raw)
    entries = list_states(context)  # ONE scan, same lesson as sets_store's own
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
    """The healed layout currently on disk -- byte-identical mechanism to
    ``sets_store.load_layout`` (memoized on the file's ``(mtime_ns,
    size)``; a missing/unreadable file heals to an empty layout)."""
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
        logger.warning("EPSNodes: unreadable universal states layout (%s); rebuilding", exc)
        raw = None
    return healed_layout(context, raw)


def save_layout(context: LibraryContext, raw: object) -> dict:
    """Normalize + heal *raw*, write atomically, return what was written --
    mirrors ``sets_store.save_layout`` (a full-replace, healed
    server-side, so a stale client can never vanish a state from the
    pane)."""
    layout = healed_layout(context, raw)
    path = layout_path(context)
    _atomic_write_text(path, json.dumps(layout, indent=2))
    _layout_cache.pop(path, None)
    return layout
