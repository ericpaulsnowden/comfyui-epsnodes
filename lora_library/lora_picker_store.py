"""On-disk storage for EPSLoraPicker's favorites/recents (FORMAT.md §6.13,
research round 2026-08-09, roadmap ``docs/ROADMAP-lora-picker.md``).

ONE JSON file, ``lora_picker.json``, directly inside ``context.library_dir()``
(no subdirectory — the same "exactly one file, not one per record" placement
``resolution_presets_store.py`` uses and documents, this module's own closest
sibling)::

    {
      "format": 1,
      "favorites": ["<name>", ...],
      "recents": [{"file": "<name>", "ts": <float>}, ...]
    }

Mirrors ``resolution_presets_store.py`` verbatim: same error family
(:class:`PickerStoreError` base safe to surface in an HTTP ``{"error": ...}``
body, :class:`ConflictError` carrying the file's current mtime), same
degrade-on-read/raise-on-write posture, same atomic write via
``lora_library.context._atomic_write_text``, same
:func:`check_conflict`/``base_mtime`` shape borrowed from ``markdown_store``'s
§3.5 two-machine convention. FORMAT.md §6.13 is explicit that the
favorite/recent HTTP routes never send ``base_mtime`` — a star click or a
recents stamp must not 409, the read-modify-write race between two machines
inside the same second is accepted and self-heals on the next write — but the
parameter stays wired through here exactly like the presets store's, since
nothing about the store layer itself should assume that.

This module lives INSIDE ``lora_library``, unlike
``resolution_presets_store.py`` (which lives in the sibling ``eps_image``
package and therefore needs the relative-then-absolute import dance its own
docstring explains): the ``.context`` import below is a plain relative
import, resolving the same way under real ComfyUI and under pytest.

No ComfyUI import of any kind — same importable-without-ComfyUI seam as
``context.py``/``sets_store.py``/``markdown_store.py``: the injected
``LibraryContext`` is the only thing this module needs to find its file.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .context import LibraryContext, _atomic_write_text

logger = logging.getLogger("lora_library")

#: The file's own name, directly inside context.library_dir() (module
#: docstring).
PICKER_FILENAME = "lora_picker.json"

#: The "format" value this writer emits (resolution_presets_store.py's
#: identical convention) -- there is only one shape today, and load_state
#: does not gate on it (see its own docstring): a hand-edited file missing
#: the field entirely reads back exactly like one that declares it.
CURRENT_FORMAT = 1

#: FORMAT.md §6.13 -- both favorites and recents are capped/deduped by
#: file; this is the recents cap. Applied on every write AND on read (a
#: hand-edited file with more than this many rows degrades to the newest
#: this many rather than growing the list further).
RECENTS_CAP = 30


class PickerStoreError(ValueError):
    """Base for errors meant to reach the HTTP layer as a 4xx
    ``{"error": ...}`` (mirrors ``resolution_presets_store.PresetStoreError``/
    ``sets_store.SetValidationError``'s identical convention). Always
    carries a human-readable message safe to surface verbatim.
    """


class ConflictError(PickerStoreError):
    """*base_mtime* didn't match the file's current mtime -- the identical
    FORMAT.md §3.5 convention ``markdown_store.ConflictError``/
    ``resolution_presets_store.ConflictError`` implement, applied here to
    the picker file. Carries the file's CURRENT mtime so a caller that DOES
    choose to send ``base_mtime`` (the store supports it even though
    FORMAT.md §6.13 says the favorite/recent routes themselves never will)
    can echo it in a 409 body for a reload-then-reapply flow.
    """

    def __init__(self, current_mtime: float | None) -> None:
        super().__init__(
            "the picker file changed on disk since it was loaded -- same "
            "two-machine conflict FORMAT.md §3.5 describes for the notebook"
        )
        self.current_mtime = current_mtime


def picker_path(context: LibraryContext) -> Path:
    """``<library_dir>/lora_picker.json`` -- the single source of truth
    (module docstring).

    ``context.library_dir()`` creates the folder on demand, so this raises
    ``OSError`` when the configured library folder is unreachable (an
    unmounted NAS -- the same failure ``resolution_presets_store.
    presets_path`` guards against). Callers that must never crash use the
    guarded wrapper below instead of calling this raw.
    """
    return context.library_dir() / PICKER_FILENAME


def _require_picker_path(context: LibraryContext) -> Path:
    """:func:`picker_path`, with an unreachable library folder surfaced as
    :class:`PickerStoreError` -- which the picker routes already turn into a
    400 ``{"error": ...}`` -- instead of a raw ``OSError`` that would 500 the
    whole route (audit 2026-08-08 posture, applied here)."""
    try:
        return picker_path(context)
    except OSError as exc:
        raise PickerStoreError(
            f"the library folder is unreachable ({exc}); the LoRA picker's "
            "favorites/recents live inside it -- fix or remount the "
            "configured library folder and retry"
        ) from exc


def _clean_file_name(value: object) -> str | None:
    """Forward-slash-normalized, stripped form of *value*, or ``None`` if
    *value* isn't usable as a name (not a string, or blank once cleaned).

    FORMAT.md §6.13: names are stored forward-slash normalized so a set
    written on Windows matches on POSIX (the §4 separator rule, reused
    verbatim here). Applied on write AND on read -- a hand-edited file may
    carry backslashes or stray whitespace just as easily as a fresh API
    call does.
    """
    if not isinstance(value, str):
        return None
    cleaned = value.replace("\\", "/").strip()
    return cleaned or None


def _require_file_name(value: object) -> str:
    """:func:`_clean_file_name`, raising :class:`PickerStoreError` instead
    of degrading -- for the write-side entry points, where a caller-supplied
    *value* that isn't a usable name is the caller's bug, not a hand-edited
    file to tolerate."""
    cleaned = _clean_file_name(value)
    if cleaned is None:
        raise PickerStoreError("file must be a non-empty string")
    return cleaned


def _parse_favorites(raw: object, path: Path) -> list[str]:
    """Best-effort parse of the on-disk ``favorites`` list: a non-list (or
    missing) key degrades to no favorites with a warning; each entry is
    cleaned via :func:`_clean_file_name`, with a non-string/blank entry
    dropped (warned) without discarding its siblings -- the same per-entry
    tolerance ``resolution_presets_store.load_presets`` applies to its own
    hand-edited-input case. Duplicate names collapse to their first (most
    senior) occurrence -- FORMAT.md §6.13's "dedup-by-file" rule, since a
    normal write can never produce one but a hand-edited file might.
    """
    if not isinstance(raw, list):
        logger.warning(
            "EPSNodes: %s has no usable 'favorites' list (%r); using empty favorites",
            path,
            raw,
        )
        return []
    favorites: list[str] = []
    for entry in raw:
        name = _clean_file_name(entry)
        if name is None:
            logger.warning("EPSNodes: %s has a malformed favorite %r; skipping it", path, entry)
            continue
        if name not in favorites:
            favorites.append(name)
    return favorites


def _parse_stored_recent(raw: object) -> dict | None:
    """One on-disk ``recents[]`` row, validated: ``{"file": <name>, "ts":
    <int|float, not bool>}``. Returns ``None`` on any problem -- the caller
    logs and drops it, siblings kept, same posture as
    :func:`_parse_favorites`.
    """
    if not isinstance(raw, dict):
        return None
    file_name = _clean_file_name(raw.get("file"))
    if file_name is None:
        return None
    ts = raw.get("ts")
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        return None
    return {"file": file_name, "ts": float(ts)}


def _parse_recents(raw: object, path: Path) -> list[dict]:
    """Best-effort parse of the on-disk ``recents`` list, newest-first order
    preserved from the file (never re-sorted by ``ts`` -- list position IS
    the order, same as ``resolution_presets_store``'s "FILE order" reading).
    A non-list (or missing) key degrades to no recents with a warning; each
    row is validated via :func:`_parse_stored_recent`, a malformed one
    dropped (warned) with siblings kept; duplicate ``file`` values collapse
    to their first (most recent) occurrence (FORMAT.md §6.13 dedup rule);
    the result is capped at :data:`RECENTS_CAP` even on read, so a
    hand-edited file that grew past the cap degrades rather than persisting
    an over-long list.
    """
    if not isinstance(raw, list):
        logger.warning(
            "EPSNodes: %s has no usable 'recents' list (%r); using empty recents", path, raw
        )
        return []
    recents: list[dict] = []
    seen: set[str] = set()
    for entry in raw:
        row = _parse_stored_recent(entry)
        if row is None:
            logger.warning(
                "EPSNodes: %s has a malformed recents entry %r; skipping it", path, entry
            )
            continue
        if row["file"] in seen:
            continue
        seen.add(row["file"])
        recents.append(row)
    return recents[:RECENTS_CAP]


def load_state(context: LibraryContext) -> tuple[dict, float | None]:
    """``{"favorites": [...], "recents": [...]}`` plus the file's mtime
    (``None`` if it doesn't exist yet -- not an error, same "first use"
    reading as ``resolution_presets_store.load_presets``).

    A missing file is silent (a brand-new install/library folder); a
    present-but-unreadable or malformed one logs a WARNING and degrades to
    an empty state rather than raising -- never crash on a hand-edited or
    corrupted file. An UNREACHABLE library folder (:func:`picker_path`'s own
    OSError case) degrades the same warned way -- reads never raise here.
    ``favorites``/``recents`` are validated independently, so a malformed
    one still returns the other intact. The returned mtime is the file's
    REAL mtime whenever the file could be opened at all, even if its
    contents turned out to be malformed -- "the file is the truth" applies
    to conflict detection even for a currently-unreadable file.
    """
    try:
        path = picker_path(context)
    except OSError as exc:
        logger.warning(
            "EPSNodes: library folder is unreachable (%s); using empty picker state", exc
        )
        return {"favorites": [], "recents": []}, None
    try:
        with open(path, encoding="utf-8") as fh:
            raw_text = fh.read()
    except FileNotFoundError:
        return {"favorites": [], "recents": []}, None
    # UnicodeDecodeError included (review 2026-08-09): it is a ValueError,
    # not an OSError, so a hand-edited file saved as UTF-16 used to escape
    # this guard and 500 every picker route instead of degrading.
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning(
            "EPSNodes: could not read %s (%s); using empty picker state", path, exc
        )
        return {"favorites": [], "recents": []}, None

    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        logger.warning("EPSNodes: %s is not valid JSON (%s); using empty picker state", path, exc)
        return {"favorites": [], "recents": []}, mtime

    if not isinstance(data, dict):
        logger.warning(
            "EPSNodes: %s does not contain a JSON object; using empty picker state", path
        )
        return {"favorites": [], "recents": []}, mtime

    favorites = _parse_favorites(data.get("favorites"), path)
    recents = _parse_recents(data.get("recents"), path)
    return {"favorites": favorites, "recents": recents}, mtime


def _write(context: LibraryContext, state: dict) -> float:
    """Atomically write *state* (already-normalized) and return the new
    mtime. An unreachable library folder raises :class:`PickerStoreError`
    (via :func:`_require_picker_path`); a mid-write failure (e.g. a failed
    atomic replace) still raises its raw ``OSError`` -- a genuinely
    exceptional disk fault, not the known unmounted-folder condition."""
    path = _require_picker_path(context)
    payload = {
        "format": CURRENT_FORMAT,
        "favorites": state["favorites"],
        "recents": state["recents"],
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    _atomic_write_text(path, text)
    return path.stat().st_mtime


def check_conflict(base_mtime: float | None, current_mtime: float | None) -> None:
    """Raise :class:`ConflictError` per FORMAT.md §3.5's convention, else
    return ``None``. A missing file (*current_mtime* is ``None``) has
    nothing to conflict with -- treated as "first save to a brand-new file"
    even if a stale *base_mtime* was sent, same as omitting *base_mtime*
    outright (verbatim copy of ``resolution_presets_store.check_conflict``'s
    own rule).
    """
    if base_mtime is None or current_mtime is None:
        return
    if abs(current_mtime - base_mtime) > 1e-6:
        raise ConflictError(current_mtime)


def toggle_favorite(
    context: LibraryContext,
    file: object,
    on: object,
    *,
    base_mtime: float | None = None,
) -> tuple[dict, float]:
    """Star (``on`` truthy) or unstar (``on`` falsy) *file*.

    Both directions are IDEMPOTENT: starring an already-favorited name, or
    unstarring one that isn't there, succeeds without error and without
    disturbing the rest of the list. Starring APPENDS at the end (favorites
    stay in insertion order, FORMAT.md §6.13); unstarring removes it
    wherever it sits. *file* is cleaned via :func:`_require_file_name`
    (forward-slash normalized, stripped) before anything else -- checked
    BEFORE touching the file at all, same ordering
    ``resolution_presets_store.save_preset`` uses for its own name/values
    validation.

    Raises :class:`PickerStoreError` for a structurally bad *file*, or when
    the library folder itself is unreachable
    (:func:`_require_picker_path`); :class:`ConflictError` if *base_mtime*
    is given and stale (FORMAT.md §6.13: the routes themselves never send
    one). Returns ``(fresh state, new mtime)``.
    """
    clean_file = _require_file_name(file)

    _require_picker_path(context)
    state, current_mtime = load_state(context)
    check_conflict(base_mtime, current_mtime)

    favorites = list(state["favorites"])
    if on:
        if clean_file not in favorites:
            favorites.append(clean_file)
    else:
        favorites = [name for name in favorites if name != clean_file]

    new_state = {"favorites": favorites, "recents": state["recents"]}
    new_mtime = _write(context, new_state)
    return new_state, new_mtime


def record_recents(
    context: LibraryContext,
    files: object,
    *,
    base_mtime: float | None = None,
) -> tuple[dict, float]:
    """Move every name in *files* to the front of ``recents``, freshly
    server-stamped, capped at :data:`RECENTS_CAP`.

    *files* is a list; ``files[0]`` is the MOST RECENT entry once this
    returns -- i.e. it ends up frontmost, ahead of ``files[1]``, and so on.
    To get there, each name is processed in REVERSE order: remove any
    existing ``recents`` row for it, then insert the row at the front (a
    fresh ``ts = time.time()``). Because the reversed loop re-inserts
    ``files[0]`` LAST, it lands closest to the front -- and a name repeated
    within *files* itself collapses the same way a pre-existing recents row
    for it would, since every earlier insertion of that name gets removed
    again before the final one lands.

    An EMPTY *files* is a no-op: it returns the CURRENT state (via
    :func:`load_state`) without writing at all -- the mtime is therefore
    unchanged, and no conflict is possible since nothing is written.
    Otherwise, every name is cleaned via :func:`_require_file_name` before
    any disk I/O, same "validate before touching the file" ordering
    :func:`toggle_favorite` uses.

    Raises :class:`PickerStoreError` for a structurally bad entry in
    *files*, or when the library folder itself is unreachable
    (:func:`_require_picker_path`); :class:`ConflictError` if *base_mtime*
    is given and stale (FORMAT.md §6.13: the routes themselves never send
    one). Returns ``(fresh state, new mtime)``.
    """
    if not files:
        return load_state(context)

    clean_files = [_require_file_name(file) for file in files]

    _require_picker_path(context)
    state, current_mtime = load_state(context)
    check_conflict(base_mtime, current_mtime)

    recents = list(state["recents"])
    now = time.time()
    for file in reversed(clean_files):
        recents = [row for row in recents if row["file"] != file]
        recents.insert(0, {"file": file, "ts": now})
    recents = recents[:RECENTS_CAP]

    new_state = {"favorites": state["favorites"], "recents": recents}
    new_mtime = _write(context, new_state)
    return new_state, new_mtime


def clear_recents(
    context: LibraryContext,
    *,
    base_mtime: float | None = None,
) -> tuple[dict, float]:
    """Empty ``recents`` entirely; ``favorites`` is untouched.

    Raises :class:`PickerStoreError` when the library folder itself is
    unreachable (:func:`_require_picker_path`); :class:`ConflictError` if
    *base_mtime* is given and stale (FORMAT.md §6.13: the routes themselves
    never send one). Returns ``(fresh state, new mtime)``.
    """
    _require_picker_path(context)
    state, current_mtime = load_state(context)
    check_conflict(base_mtime, current_mtime)

    new_state = {"favorites": state["favorites"], "recents": []}
    new_mtime = _write(context, new_state)
    return new_state, new_mtime
