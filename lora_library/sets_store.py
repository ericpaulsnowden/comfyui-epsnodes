"""On-disk storage for LoRA sets (FORMAT.md §4/§4.1).

One JSON file per set under ``context.sets_dir()``. This module owns the
whole §4 file lifecycle (slug derivation, validation/defaults, atomic
save/load/list/delete), the §4.1 composite multi-loader schema, and the §4
lora-resolution rule; ``routes_sets.py`` and ``nodes_sets.py`` both build on
it and never touch the filesystem directly. No ComfyUI imports here — same
importable-without-ComfyUI seam as ``context.py`` (see its module docstring).
"""

from __future__ import annotations

import json
import logging
import re
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
    """
    _require_sets_dir(context)
    path = set_path(context, slug)
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
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
    return normalize_set(raw)


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
    _require_sets_dir(context)
    if slug is None:
        slug = _unique_slug(context, slugify(normalized["name"]))
    text = json.dumps(normalized, indent=2, ensure_ascii=False) + "\n"
    _atomic_write_text(set_path(context, slug), text)
    return slug, normalized


def delete_set(context: LibraryContext, slug: str) -> bool:
    """Delete the set at *slug*. ``True`` if a file was removed, else
    ``False``. An unreachable library folder raises
    :class:`SetValidationError` (via :func:`_require_sets_dir`) rather than
    reading as a plain "nothing to delete" ``False`` — the route turns it
    into a 400 naming the folder instead of a misleading 404."""
    _require_sets_dir(context)
    try:
        set_path(context, slug).unlink()
        return True
    except FileNotFoundError:
        return False


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
    """
    summaries = []
    try:
        sets_dir = context.sets_dir()
    except OSError as exc:
        logger.warning(
            "EPSNodes: library sets folder is unreachable (%s); listing no sets", exc
        )
        return summaries
    for path in sets_dir.glob("*.json"):
        slug = path.stem
        if not _VALID_SLUG_RE.match(slug):
            logger.warning(
                "EPSNodes: ignoring %s — %r is not a valid set slug (FORMAT.md §4); "
                "rename the file to a valid slug (lowercase letters/digits/-/_, "
                "starting with a letter or digit) to make it usable",
                path.name,
                slug,
            )
            continue
        try:
            data = load_set(context, slug)
        except SetValidationError as exc:
            logger.warning("EPSNodes: skipping unreadable set %r: %s", slug, exc)
            continue
        if data is None:  # shouldn't happen (we just globbed the file), but be defensive
            continue
        summaries.append({"slug": slug, "name": data["name"], "count": len(data["loras"])})
    summaries.sort(key=lambda entry: (entry["name"].casefold(), entry["slug"]))
    return summaries


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
    empty layout -- healing fills in every set, uncategorized)."""
    try:
        path = layout_path(context)
        if not path.is_file():
            return healed_layout(context, None)
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("EPSNodes: unreadable sets layout (%s); rebuilding", exc)
        raw = None
    return healed_layout(context, raw)


def save_layout(context: LibraryContext, raw: object) -> dict:
    """Normalize + heal *raw*, write atomically, return what was written."""
    layout = healed_layout(context, raw)
    _atomic_write_text(layout_path(context), json.dumps(layout, indent=2))
    return layout
