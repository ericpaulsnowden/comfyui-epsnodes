"""On-disk storage for EPSResolution's server-side size presets (feature
contract 2026-08-01) — a shared JSON file inside the SAME library folder
``lora_library``'s Prompt Notebook and LoRA sets already live in, so a
preset travels across machines exactly like those two do (FORMAT.md §6.5's
own roadmap line: "Deferred (M3-M4): NAS presets (reuse lora_library
context/sets_store/settings)" — this module is that reuse).

ONE JSON file, ``resolution_presets.json``, directly inside
``context.library_dir()`` (no subdirectory, unlike LoRA sets' own ``sets/``
— FORMAT.md §4 — since there is exactly one file here, not one per
preset)::

    {
      "format": 1,
      "presets": {
        "<name>": {
          "width": int, "height": int, "resize_method": str,
          "interpolation": str, "multiple_of": int
        }
      }
    }

Mirrors ``lora_library/sets_store.py``'s own shape (validate → atomic
write, ``PresetStoreError`` subclasses safe to surface verbatim in an HTTP
``{"error": ...}`` body, exactly like ``sets_store.SetValidationError``) and
borrows ``lora_library/markdown_store.py``'s §3.5 two-machine conflict
convention verbatim (:func:`check_conflict`/:class:`ConflictError`, same
``base_mtime``-vs-current-mtime shape, same "a missing file has nothing to
conflict with" rule) — read both modules' docstrings for the conventions
this one continues.

Deliberately reaches into ``lora_library.context`` for ``LibraryContext``/
``_atomic_write_text`` — a DELIBERATE, contract-anticipated exception to
``eps_image``'s usual self-containment (see e.g. ``image_grid_store.py``'s
own docstring: "a sibling FEATURE FAMILY that deliberately doesn't reach
into lora_library"). That isolation exists so an eps_image feature doesn't
NEED lora_library; this one's whole point is the OPPOSITE — presets are
worthless unless they live in the exact folder the Notebook/sets already
share, so importing the real ``LibraryContext`` (rather than re-inventing a
second, incompatible one) is the correct call here. The import itself is
wrapped in the SAME relative-then-absolute try/except this pack's own
``__init__.py`` uses at its top (see its module docstring/comments): under
real ComfyUI, ``eps_image``/``lora_library`` are both sub-packages of one
ComfyUI-assigned top package, reachable only two dots up from here; under
pytest (``tests/conftest.py`` puts the repo root on ``sys.path``), they are
each their own top-level package with no shared parent, so only the
absolute form resolves. Both branches are exercised by this pack's own
test suite (the second one, every time ``pytest`` runs).

No ComfyUI import of any kind — same importable-without-ComfyUI seam as
``context.py``/``sets_store.py``/``markdown_store.py`` (see their module
docstrings): the injected ``LibraryContext`` is the only thing this module
needs to find its file.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

try:
    # Real ComfyUI: this pack loads as one nested package, so eps_image and
    # lora_library are both sub-packages of it -- two dots up from here
    # (eps_image.resolution_presets_store's own enclosing package is
    # eps_image, so ".." reaches its parent) lands on that shared top
    # package, then back down into lora_library.context.
    from ..lora_library.context import LibraryContext, _atomic_write_text
except ImportError:
    # Flat import context (pytest's rootdir setup, tests/conftest.py: the
    # repo root goes straight onto sys.path, so lora_library and eps_image
    # are each their own top-level package with no common parent -- the
    # relative form above raises "attempted relative import beyond
    # top-level package", and this absolute form is what actually
    # resolves).
    from lora_library.context import LibraryContext, _atomic_write_text

logger = logging.getLogger("eps_image")

#: The file's own name, directly inside context.library_dir() (module
#: docstring).
PRESETS_FILENAME = "resolution_presets.json"

#: The highest "format" value this reader understands (sets_store.py's
#: identical convention) -- there is only one shape today.
CURRENT_FORMAT = 1

#: A preset name's max length after trimming (feature contract).
MAX_NAME_LENGTH = 200

#: The 5 fields every preset value must carry. Split into int-typed and
#: str-typed groups since each needs a different isinstance check -- both
#: consulted by :func:`_normalize_values`, which builds the canonical
#: output dict in this exact field order.
_INT_FIELDS = ("width", "height", "multiple_of")
_STR_FIELDS = ("resize_method", "interpolation")


class PresetStoreError(ValueError):
    """Base for errors meant to reach the HTTP layer as a 4xx
    ``{"error": ...}`` (mirrors ``sets_store.SetValidationError``/
    ``markdown_store.MarkdownStoreError``'s identical convention). Always
    carries a human-readable message safe to surface verbatim.
    """


class InvalidPresetNameError(PresetStoreError):
    """*name* is blank/whitespace-only after trimming, or exceeds
    :data:`MAX_NAME_LENGTH`."""


class InvalidPresetValuesError(PresetStoreError):
    """*values* is missing one of the 5 required fields, or one has the
    wrong JSON-primitive type.

    TYPE-only: range checks (width/height/multiple_of bounds) and enum
    checks (resize_method/interpolation membership) are the ROUTE layer's
    job (``routes_resolution_presets.py``) -- it alone imports
    ``nodes_resolution`` for the widgets' own bounds/allowed values, and
    this module stays free of that dependency. Calling :func:`save_preset`
    directly (bypassing the route -- e.g. from a test, or a future script)
    therefore still gets a safe, well-typed on-disk file; it just won't
    reject an in-range-shaped but semantically bogus value (an
    out-of-bounds width, or an unknown resize_method string) the way the
    route's own validation does.
    """


class PresetNotFoundError(PresetStoreError):
    """:func:`delete_preset`'s *name* doesn't exist."""


class ConflictError(PresetStoreError):
    """*base_mtime* didn't match the file's current mtime -- the identical
    FORMAT.md §3.5 convention ``markdown_store.ConflictError`` implements
    for the notebook, applied here to the presets file. Carries the file's
    CURRENT mtime so the route can echo it in the 409 body for the
    caller's reload-then-reapply flow.
    """

    def __init__(self, current_mtime: float | None) -> None:
        super().__init__(
            "the presets file changed on disk since it was loaded -- same "
            "two-machine conflict FORMAT.md §3.5 describes for the notebook"
        )
        self.current_mtime = current_mtime


def presets_path(context: LibraryContext) -> Path:
    """``<library_dir>/resolution_presets.json`` -- the single source of
    truth (module docstring).

    ``context.library_dir()`` creates the folder on demand, so this raises
    ``OSError`` when the configured library folder is unreachable (an
    unmounted NAS -- the exact failure ``lora_library/routes.py``'s
    ``get_config``/``notebook_path_error`` guard against, owner audit
    2026-08-08). Callers that must never crash use the guarded wrappers
    below instead of calling this raw.
    """
    return context.library_dir() / PRESETS_FILENAME


def _require_presets_path(context: LibraryContext) -> Path:
    """:func:`presets_path`, with an unreachable library folder surfaced as
    :class:`PresetStoreError` -- which the routes already turn into a 400
    ``{"error": ...}`` -- instead of a raw ``OSError`` that would 500 the
    whole route (audit 2026-08-08; the OSError's own text names the folder
    that could not be created/reached)."""
    try:
        return presets_path(context)
    except OSError as exc:
        raise PresetStoreError(
            f"the library folder is unreachable ({exc}); resolution presets "
            "live inside it -- fix or remount the configured library folder "
            "and retry"
        ) from exc


def normalize_name(name: object) -> str:
    """Trim *name* and validate it: non-empty, at most
    :data:`MAX_NAME_LENGTH` characters. Returns the trimmed form (the
    canonical on-disk key). Raises :class:`InvalidPresetNameError`
    otherwise.
    """
    if not isinstance(name, str):
        raise InvalidPresetNameError("preset name must be a string")
    trimmed = name.strip()
    if not trimmed:
        raise InvalidPresetNameError("preset name must not be empty")
    if len(trimmed) > MAX_NAME_LENGTH:
        raise InvalidPresetNameError(
            f"preset name must be at most {MAX_NAME_LENGTH} characters (got {len(trimmed)})"
        )
    return trimmed


def _normalize_values(raw: object) -> dict:
    """Validate *raw* has exactly the 5 required fields with the right
    JSON-primitive types; returns a freshly built dict in canonical field
    order (width, height, resize_method, interpolation, multiple_of).
    Raises :class:`InvalidPresetValuesError` naming the first problem found.

    ``bool`` is deliberately rejected for the int fields even though it is
    a subclass of ``int`` in Python -- the same guard
    ``sets_store._coerce_float`` uses for the identical reason: a stray
    ``true``/``false`` must never silently pass as a dimension.
    """
    if not isinstance(raw, dict):
        raise InvalidPresetValuesError("preset 'values' must be an object")

    out: dict[str, object] = {}
    for field in _INT_FIELDS:
        value = raw.get(field)
        if not isinstance(value, int) or isinstance(value, bool):
            raise InvalidPresetValuesError(f"preset '{field}' must be an integer")
        out[field] = value
    for field in _STR_FIELDS:
        value = raw.get(field)
        if not isinstance(value, str) or not value:
            raise InvalidPresetValuesError(f"preset '{field}' must be a non-empty string")
        out[field] = value

    return {
        "width": out["width"],
        "height": out["height"],
        "resize_method": out["resize_method"],
        "interpolation": out["interpolation"],
        "multiple_of": out["multiple_of"],
    }


def _parse_stored_preset(raw: object) -> dict | None:
    """Best-effort parse of one on-disk preset entry: like
    :func:`_normalize_values` but never raises -- a hand-edited/corrupted
    file must degrade ONE entry at a time (logged, by the caller), never
    take the whole file down. Returns ``None`` on any problem.
    """
    try:
        return _normalize_values(raw)
    except InvalidPresetValuesError:
        return None


def load_presets(context: LibraryContext) -> tuple[dict[str, dict], float | None]:
    """Every preset in the store, in FILE order, plus the file's mtime
    (``None`` if it doesn't exist yet -- not an error, same "first use"
    reading as ``markdown_store.load_notebook``/``LibraryContext.
    load_config``).

    A missing file is silent (a brand-new install/library folder); a
    present-but-unreadable or malformed one logs a WARNING and degrades to
    empty presets rather than raising -- never crash on a hand-edited or
    corrupted file. An UNREACHABLE library folder (``presets_path``'s own
    OSError case) degrades the same warned way -- reads never raise here.
    An individual malformed preset entry inside an
    otherwise-good file is dropped (logged) without discarding its
    siblings -- the same per-entry tolerance ``sets_store.list_sets``/
    ``nodes_checkpoint_switcher._parse_selection`` already apply to their
    own hand-edited-input cases. The returned mtime is the file's REAL
    mtime whenever the file could be opened at all, even if its contents
    turned out to be malformed -- "the file is the truth" applies to
    conflict detection even for a currently-unreadable file.
    """
    # GUARDED (audit 2026-08-08): presets_path() resolves through
    # context.library_dir(), whose on-demand mkdir raises before the try
    # below ever runs -- an unreachable configured folder (unmounted NAS)
    # must degrade exactly like the present-but-unreadable branch, never
    # take the GET route down with it.
    try:
        path = presets_path(context)
    except OSError as exc:
        logger.warning(
            "EPSNodes: library folder is unreachable (%s); using empty presets", exc
        )
        return {}, None
    try:
        with open(path, encoding="utf-8") as fh:
            raw_text = fh.read()
    except FileNotFoundError:
        return {}, None
    except OSError as exc:
        logger.warning("EPSNodes: could not read %s (%s); using empty presets", path, exc)
        return {}, None

    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        logger.warning("EPSNodes: %s is not valid JSON (%s); using empty presets", path, exc)
        return {}, mtime

    if not isinstance(data, dict):
        logger.warning("EPSNodes: %s does not contain a JSON object; using empty presets", path)
        return {}, mtime

    raw_presets = data.get("presets")
    if not isinstance(raw_presets, dict):
        logger.warning("EPSNodes: %s has no usable 'presets' object; using empty presets", path)
        return {}, mtime

    presets: dict[str, dict] = {}
    for name, raw_value in raw_presets.items():
        if not isinstance(name, str) or not name:
            logger.warning(
                "EPSNodes: %s has a non-string/empty preset key %r; skipping it", path, name
            )
            continue
        normalized = _parse_stored_preset(raw_value)
        if normalized is None:
            logger.warning("EPSNodes: %s has a malformed preset %r; skipping it", path, name)
            continue
        presets[name] = normalized
    return presets, mtime


def _write(context: LibraryContext, presets: dict[str, dict]) -> float:
    """Atomically write *presets* (already-normalized) and return the new
    mtime. An unreachable library folder raises :class:`PresetStoreError`
    (via :func:`_require_presets_path`); a mid-write failure (e.g. a failed
    atomic replace) still raises its raw ``OSError`` -- that is a genuinely
    exceptional disk fault, not the known unmounted-folder condition."""
    path = _require_presets_path(context)
    payload = {"format": CURRENT_FORMAT, "presets": presets}
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    _atomic_write_text(path, text)
    return path.stat().st_mtime


def check_conflict(base_mtime: float | None, current_mtime: float | None) -> None:
    """Raise :class:`ConflictError` per FORMAT.md §3.5's convention, else
    return ``None``. A missing file (*current_mtime* is ``None``) has
    nothing to conflict with -- treated as "first save to a brand-new
    file" even if a stale *base_mtime* was sent, same as omitting
    *base_mtime* outright (verbatim copy of ``markdown_store.
    check_conflict``'s own rule).
    """
    if base_mtime is None or current_mtime is None:
        return
    if abs(current_mtime - base_mtime) > 1e-6:
        raise ConflictError(current_mtime)


def save_preset(
    context: LibraryContext,
    name: object,
    values: object,
    *,
    base_mtime: float | None = None,
) -> tuple[dict[str, dict], float]:
    """Create *name* (new) or overwrite it in place (existing) with *values*.

    Preserves insertion order for every OTHER preset: a create appends at
    the end; an update keeps its existing position -- the same "update
    never moves it" convention ``markdown_store.upsert_entry`` documents
    for notebook entries.

    Raises :class:`InvalidPresetNameError`/:class:`InvalidPresetValuesError`
    for a structurally bad *name*/*values* (checked BEFORE touching the
    file at all -- range/enum validation against ``nodes_resolution``'s own
    constants is the ROUTE layer's job, see :class:`InvalidPresetValuesError`),
    a plain :class:`PresetStoreError` when the library folder itself is
    unreachable (:func:`_require_presets_path`), or :class:`ConflictError`
    if *base_mtime* is given and stale. Returns ``(fresh presets dict, new
    mtime)``.
    """
    clean_name = normalize_name(name)
    clean_values = _normalize_values(values)

    # Surfaced BEFORE the load: load_presets deliberately degrades an
    # unreachable library folder to empty presets (fine for a read), but a
    # WRITE against that state must fail loudly with the folder named --
    # not silently target a file that cannot be written (audit 2026-08-08).
    _require_presets_path(context)

    presets, current_mtime = load_presets(context)
    check_conflict(base_mtime, current_mtime)

    presets[clean_name] = clean_values
    new_mtime = _write(context, presets)
    return presets, new_mtime


def delete_preset(
    context: LibraryContext,
    name: object,
    *,
    base_mtime: float | None = None,
) -> tuple[dict[str, dict], float]:
    """Delete *name*.

    Raises :class:`InvalidPresetNameError` for a structurally bad *name*,
    a plain :class:`PresetStoreError` when the library folder itself is
    unreachable (checked before everything file-shaped -- see the comment
    below), :class:`ConflictError` if *base_mtime* is given and stale
    (checked BEFORE existence, same order ``markdown_store``'s delete route
    uses), or :class:`PresetNotFoundError` if no such preset exists.
    Returns ``(fresh presets dict, new mtime)``.
    """
    clean_name = normalize_name(name)

    # Same pre-load guard as save_preset -- and it must run before the
    # existence check: against an unreachable folder load_presets degrades
    # to empty presets, which would misreport the failure as a 404 "no such
    # preset" instead of naming the real problem (audit 2026-08-08).
    _require_presets_path(context)

    presets, current_mtime = load_presets(context)
    check_conflict(base_mtime, current_mtime)

    if clean_name not in presets:
        raise PresetNotFoundError(f"no such preset {clean_name!r}")
    del presets[clean_name]
    new_mtime = _write(context, presets)
    return presets, new_mtime
