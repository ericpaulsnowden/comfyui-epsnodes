"""Parsing/serialization + persistence for notebook markdown files (FORMAT.md
§3). Mirrors ``sets_store.py``'s split: pure functions (``parse``,
``serialize``, ``entry_text``, the ``upsert_entry``/``remove_entry`` mutators)
need no filesystem and are the bulk of what's tested; ``load_notebook``/
``save_notebook`` are the thin I/O layer ``routes_notebook.py`` and
``nodes_notebook.py`` both build on. No ComfyUI imports here — same
importable-without-ComfyUI seam as ``context.py``/``sets_store.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .context import _atomic_write_text


class MarkdownStoreError(Exception):
    """Base for errors meant to reach the HTTP layer as a 4xx ``{"error": ...}``.

    Always carries a human-readable message safe to surface verbatim
    (``routes_notebook.py`` does exactly that), same convention as
    ``sets_store.SetValidationError``.
    """


class InvalidEntryNameError(MarkdownStoreError):
    """Raised when a create is attempted with a blank/whitespace-only name —
    also reused by :func:`create_category` for the same problem with a
    category name (FORMAT.md §3.4), plus one a category name has that an
    entry name doesn't: no embedded newlines (a category name is exactly one
    heading line, like an entry name, per FORMAT.md §3.2)."""


class InvalidEntryTextError(MarkdownStoreError):
    """Entry text contains a line that would be read back as a heading
    (FORMAT.md §3.4's original "cannot be represented" rule). NO LONGER
    RAISED anywhere in this module since v0.48.1 — the write paths demote
    such lines instead (:func:`demote_unrepresentable_headings`). Kept for
    API compatibility with external callers of the strict
    :func:`find_unrepresentable_heading_line` check."""


class NameCollisionError(MarkdownStoreError):
    """``rename_to`` names a different entry that already exists — also
    reused by :func:`create_category` when *name* already names a category
    (FORMAT.md §3.4's "must be unique among categories")."""


class EntryNotFoundError(MarkdownStoreError):
    """``move_entry``'s *name* or *before* names no existing addressable
    entry (FORMAT.md §3.2) — the route layer maps this to 404."""


class CategoryNotFoundError(MarkdownStoreError):
    """``set_category_description``'s *name* addresses no existing category
    (FORMAT.md §3.2's sibling of :class:`EntryNotFoundError` for categories —
    the route layer maps this to 404 the same way) — also reused by
    :func:`set_category_name` for the same problem, and by
    :func:`move_category` for an unknown *name*/*before* AND for the
    uncategorized head region, which is never a movable category."""


class ConflictError(MarkdownStoreError):
    """``base_mtime`` didn't match the file's current mtime (FORMAT.md §3.5).

    Carries the file's *current* mtime so the caller (the route) can put it
    in the 409 body verbatim for the UI's reload-then-reapply flow.
    """

    def __init__(self, current_mtime: float) -> None:
        super().__init__("the file changed on disk since it was loaded — FORMAT.md §3.5")
        self.current_mtime = current_mtime


# ------------------------------------------------------------------- model

@dataclass
class Entry:
    """One ``## Name`` heading + its raw body lines (FORMAT.md §3.1)."""

    name: str
    heading_line: str
    body: list[str] = field(default_factory=list)
    #: False for a duplicate name or an empty heading — still kept here
    #: (structurally, for byte-preserving roundtrips) but excluded from
    #: lookups/listings (FORMAT.md §3.2).
    addressable: bool = False


@dataclass
class CategoryBlock:
    """One ``# Category`` heading and the entries that follow it, up to the
    next H1 (FORMAT.md §3.1). ``heading_line is None`` only for the implicit
    leading block (entries before any H1 — category ``""``), which always
    exists even in an empty file."""

    name: str
    heading_line: str | None
    #: Lines between this block's heading and its first entry (or, for a
    #: category with zero entries, everything up to the next H1/EOF). Plain
    #: prose directly under an H1 isn't part of the §3.1 grammar, but a
    #: hand-edited file can still contain it — parking it here rather than
    #: dropping it keeps roundtrips byte-preserving.
    preamble: list[str] = field(default_factory=list)
    entries: list[Entry] = field(default_factory=list)


@dataclass
class ParsedNotebook:
    """A full notebook file, structured for byte-preserving rewrites."""

    blocks: list[CategoryBlock]
    problems: list[str] = field(default_factory=list)


# ----------------------------------------------------------- line classification

def _heading_level(line: str) -> int:
    """1 for an H1 line, 2 for an H2 line, else 0 — this grammar's whole
    notion of "heading" (H3+ are body text, FORMAT.md §3.1). A line counts
    as a heading when it is exactly ``#``/``##`` or starts with ``# ``/``## ``
    after trimming; this same predicate also drives the §3.4 write-time
    guard against unrepresentable body lines, so "heading" means one thing
    everywhere in this module.
    """
    stripped = line.strip()
    if stripped == "#" or stripped.startswith("# "):
        return 1
    if stripped == "##" or stripped.startswith("## "):
        return 2
    return 0


def _heading_name(stripped_line: str, level: int) -> str:
    """The trimmed text after a heading line's ``#``/``##`` marker (FORMAT.md §3.2)."""
    return stripped_line[level:].strip()


def _iter_lines_with_heading_level(text: str):
    """Yield ``(line_no, line, level)`` for every logical line of *text*.

    *level* is 0/1/2 per :func:`_heading_level`, forced to 0 for any line
    inside a fenced code block (FORMAT.md §3.1: "heading-looking lines
    inside a fence are body text, not boundaries"). Fence state toggles on
    any line whose trimmed text starts with ``` ``` ``` — shared by
    :func:`parse` and :func:`find_unrepresentable_heading_line` so both
    agree on exactly one definition of "heading" and "inside a fence".
    """
    in_fence = False
    for line_no, line in enumerate(text.splitlines(), start=1):
        level = 0 if in_fence else _heading_level(line)
        yield line_no, line, level
        if line.strip().startswith("```"):
            in_fence = not in_fence


# ------------------------------------------------------------------- parsing

def parse(text: str) -> ParsedNotebook:
    """Parse notebook markdown *text* into a :class:`ParsedNotebook`.

    Never raises: a malformed heading just becomes a problem entry (FORMAT.md
    §3.2) rather than failing the whole parse — a hand-edited file with one
    mistake must still show every other entry.
    """
    blocks = [CategoryBlock(name="", heading_line=None)]
    problems: list[str] = []
    seen_names: set[str] = set()
    current_block = blocks[0]
    current_entry: Entry | None = None

    for line_no, line, level in _iter_lines_with_heading_level(text):
        stripped = line.strip()
        if level == 1:
            current_block = CategoryBlock(name=_heading_name(stripped, 1), heading_line=line)
            blocks.append(current_block)
            current_entry = None
        elif level == 2:
            name = _heading_name(stripped, 2)
            addressable = False
            if not name:
                problems.append(f"line {line_no}: empty entry heading skipped — FORMAT.md §3.2")
            elif name in seen_names:
                problems.append(
                    f"line {line_no}: duplicate entry name {name!r} skipped "
                    "(first occurrence is used) — FORMAT.md §3.2"
                )
            else:
                seen_names.add(name)
                addressable = True
            current_entry = Entry(name=name, heading_line=line, addressable=addressable)
            current_block.entries.append(current_entry)
        else:
            target = current_entry.body if current_entry is not None else current_block.preamble
            target.append(line)

    return ParsedNotebook(blocks=blocks, problems=problems)


def serialize(parsed: ParsedNotebook) -> list[str]:
    """The logical lines of *parsed*, in file order, with no line endings
    (FORMAT.md §3.4: writers re-emit the file from the parse)."""
    lines: list[str] = []
    for block in parsed.blocks:
        if block.heading_line is not None:
            lines.append(block.heading_line)
        lines.extend(block.preamble)
        for entry in block.entries:
            lines.append(entry.heading_line)
            lines.extend(entry.body)
    return lines


def _trimmed_text(lines: list[str]) -> str:
    """*lines* minus leading/trailing blank lines, joined with ``\\n`` — the
    FORMAT.md §3.3 entry-text normalization, reused as-is for §3.1 category
    descriptions (:func:`get_category_description` et al.): both are "prose
    between two headings" under the same grammar."""
    start, end = 0, len(lines)
    while start < end and lines[start].strip() == "":
        start += 1
    while end > start and lines[end - 1].strip() == "":
        end -= 1
    return "\n".join(lines[start:end])


def entry_text(entry: Entry) -> str:
    """*entry*'s body minus leading/trailing blank lines, joined with ``\\n``
    (FORMAT.md §3.3 — this exact string is the node's STRING output)."""
    return _trimmed_text(entry.body)


def find_unrepresentable_heading_line(text: str) -> int | None:
    """1-based line number of the first line in *text* that would be read
    back as an H1/H2 heading outside a fence, or ``None`` if *text* is safe
    to store as an entry body (FORMAT.md §3.4).

    Since v0.48.1 the write paths no longer REFUSE such lines — they run
    :func:`demote_unrepresentable_headings` instead (owner ask 2026-08-02:
    LLM-pasted content routinely contains ``#``/``##`` headings, and a
    refused save reads as "it just fails"). Kept public for callers that
    want the strict check.
    """
    for line_no, _line, level in _iter_lines_with_heading_level(text):
        if level:
            return line_no
    return None


def demote_unrepresentable_headings(text: str) -> tuple[str, int]:
    """Return *text* with every line that would read back as an H1/H2
    heading outside a fence demoted BY TWO levels (``# X`` -> ``### X``,
    ``## X`` -> ``#### X``, bare ``#``/``##`` likewise), plus how many
    lines were adjusted (FORMAT.md §3.4, write-time transform).

    Two levels, not a strip: the owner pastes LLM output whose headings
    carry real structure — demotion keeps every heading a heading AND keeps
    h1-vs-h2 hierarchy (h3 vs h4), while landing squarely in the H3+ range
    §3.1 defines as body text, so the result round-trips byte-for-byte
    forever after. Runs on the SAME fence-aware line classifier as
    :func:`parse` (``_iter_lines_with_heading_level``), so "would read back
    as a heading" here and "is a boundary" there can never disagree —
    heading-looking lines inside code fences are already body text and are
    left untouched. The ``##`` marker is inserted after any leading
    whitespace (an indented `` # X`` is a heading per ``_heading_level``'s
    strip, and stays indented after demotion). Everything else is preserved
    byte-for-byte.

    Splits on ``"\\n"`` exactly like the body-storage code in
    :func:`upsert_entry` (NOT ``str.splitlines``, which
    ``_iter_lines_with_heading_level`` uses): rejoining ``splitlines``
    output would silently drop a trailing newline and rewrite ``\\r``/exotic
    separators on EVERY save, adjusted or not. The predicates
    (``_heading_level`` + the ``\\`\\`\\``` fence toggle) are the shared ones,
    so classification still can't drift from :func:`parse` for any text a
    real editor produces.
    """
    adjusted = 0
    out_lines: list[str] = []
    in_fence = False
    for line in text.split("\n"):
        level = 0 if in_fence else _heading_level(line)
        if level:
            indent = len(line) - len(line.lstrip())
            line = line[:indent] + "##" + line[indent:]
            adjusted += 1
        out_lines.append(line)
        if line.strip().startswith("```"):
            in_fence = not in_fence
    return "\n".join(out_lines), adjusted


# -------------------------------------------------------------------- lookup

def _find_addressable(parsed: ParsedNotebook, name: str) -> tuple[CategoryBlock, Entry] | None:
    for block in parsed.blocks:
        for entry in block.entries:
            if entry.addressable and entry.name == name:
                return block, entry
    return None


def _find_last_block(parsed: ParsedNotebook, name: str) -> CategoryBlock | None:
    for block in reversed(parsed.blocks):
        if block.name == name:
            return block
    return None


def list_entries(parsed: ParsedNotebook) -> list[dict]:
    """``[{"name","category"}, ...]`` for every addressable entry, in file
    order (FORMAT.md §5's ``GET /lora_library/notebook``)."""
    return [
        {"name": entry.name, "category": block.name}
        for block in parsed.blocks
        for entry in block.entries
        if entry.addressable
    ]


def get_entry(parsed: ParsedNotebook, name: str) -> dict | None:
    """``{"name","category","text"}`` for the addressable entry *name*, or
    ``None`` (FORMAT.md §5's ``GET /lora_library/notebook/entry``)."""
    found = _find_addressable(parsed, (name or "").strip())
    if found is None:
        return None
    block, entry = found
    return {"name": entry.name, "category": block.name, "text": entry_text(entry)}


def list_categories(parsed: ParsedNotebook) -> list[str]:
    """Every named (``# ...``) category block's name, in file order
    (FORMAT.md §5's ``categories`` field on ``GET /lora_library/notebook`` —
    the one thing ``list_entries`` alone can't reveal: a category with zero
    entries). The always-present implicit leading block (category ``""``,
    FORMAT.md §3.1's "entries before any H1") is deliberately excluded —
    it isn't a "category" in the create/describe sense. A hand-edited file's
    repeated category name is reported once per occurrence, unmerged (there
    is no "addressable" concept here to dedupe against, unlike
    :func:`list_entries`) — :func:`create_category`/
    :func:`set_category_description`/:func:`get_category_description` all
    document how a repeat is targeted.
    """
    return [block.name for block in parsed.blocks if block.heading_line is not None]


def get_category_description(parsed: ParsedNotebook, name: str) -> str | None:
    """FORMAT.md §3.1's description text for the category *name* — the LAST
    block with that name wins when a hand-edited file repeats it (mirrors
    ``move_entry``'s documented "repeated category name -> last one"
    convention). ``None`` means no such category exists — including the
    blank name ``""``, which can only ever address the implicit leading
    block (see :func:`list_categories`), never a describable category.
    ``""`` means the category exists with no description (FORMAT.md §3.1:
    "empty when absent").
    """
    name = (name or "").strip()
    if not name:
        return None
    block = _find_last_block(parsed, name)
    if block is None:
        return None
    return _trimmed_text(block.preamble)


# ------------------------------------------------------------------ mutation

def upsert_entry(
    parsed: ParsedNotebook,
    name: str,
    text: str,
    *,
    category: str | None = None,
    rename_to: str | None = None,
    after: str | None = None,
) -> dict:
    """Create *name* (new) or update it in place (existing), optionally
    renaming it to *rename_to* — the one create/update/rename operation
    behind FORMAT.md §5's ``POST /lora_library/notebook/entry``. Mutates
    *parsed*; returns ``{"name","category"}`` for the resulting entry.

    *category* only affects CREATE placement (FORMAT.md §3.4: appended to
    the named category, else to the end of the file). An UPDATE keeps its
    existing position — §3.4 promises only "replace in place (and/or rename
    the heading)", never a category move — so *category* is silently
    ignored once the entry already exists.

    *after*, when given, names an existing addressable entry (FORMAT.md
    §3.4 Create after): on CREATE, the new entry is inserted immediately
    BELOW it, in that entry's own block — taking priority over *category*,
    which is not consulted once *after* resolves (this naturally lands at a
    category boundary too, when *after* names the last entry in its block:
    inserting after the last element of a list is just an append). *after*
    naming an unknown entry, or omitted, falls back to the *category*/
    end-of-file placement above. Like *category*, *after* is CREATE-ONLY —
    silently ignored once *name* already exists, same "update never moves
    the entry" rule.

    Heading-looking lines in *text* are DEMOTED, not refused, since
    v0.48.1 (:func:`demote_unrepresentable_headings`); the stored text and
    the adjusted-line count ride back in the return dict. Raises
    :class:`InvalidEntryNameError` for a blank *name* on create, or
    :class:`NameCollisionError` if *rename_to* already names a different
    entry.
    """
    name = (name or "").strip()
    category = (category or "").strip()
    rename_to = (rename_to or "").strip()
    after = (after or "").strip()

    # §3.4 write-time transform (was a refusal until v0.48.1): heading-
    # looking lines are DEMOTED two levels rather than 400'd -- see
    # demote_unrepresentable_headings' docstring for the owner ask and why
    # two levels. The stored text + count ride back in the return dict so
    # the route can tell the editor what actually landed on disk.
    text, adjusted_headings = demote_unrepresentable_headings(text)
    body_lines = text.split("\n")

    existing = _find_addressable(parsed, name) if name else None
    if existing is not None:
        block, entry = existing
        if rename_to and rename_to != name:
            if _find_addressable(parsed, rename_to) is not None:
                raise NameCollisionError(
                    f"an entry named {rename_to!r} already exists — FORMAT.md §3.2"
                )
            entry.name = rename_to
            entry.heading_line = f"## {rename_to}"
        entry.body = body_lines
        return {
            "name": entry.name,
            "category": block.name,
            "text": text,
            "adjusted_headings": adjusted_headings,
        }

    if not name:
        raise InvalidEntryNameError("entry name must not be empty — FORMAT.md §3.2")

    new_entry = Entry(name=name, heading_line=f"## {name}", body=body_lines, addressable=True)
    after_target = _find_addressable(parsed, after) if after else None
    if after_target is not None:
        after_block, after_entry = after_target
        after_block.entries.insert(after_block.entries.index(after_entry) + 1, new_entry)
        return {
            "name": name,
            "category": after_block.name,
            "text": text,
            "adjusted_headings": adjusted_headings,
        }

    target = _find_last_block(parsed, category) if category else parsed.blocks[-1]
    if target is None:
        target = CategoryBlock(name=category, heading_line=f"# {category}")
        parsed.blocks.append(target)
    target.entries.append(new_entry)
    return {
        "name": name,
        "category": target.name,
        "text": text,
        "adjusted_headings": adjusted_headings,
    }


def remove_entry(parsed: ParsedNotebook, name: str) -> bool:
    """Delete the addressable entry *name*. ``True`` if one was removed.

    Only the entry's heading+body are removed from its block — the block
    itself (and its category heading) stays, even left with zero entries
    (FORMAT.md §3.4: "categories are the user's prose, not derived state").
    """
    found = _find_addressable(parsed, (name or "").strip())
    if found is None:
        return False
    block, entry = found
    block.entries.remove(entry)
    return True


def create_category(
    parsed: ParsedNotebook,
    name: str,
    description: str = "",
    *,
    after: str | None = None,
) -> dict:
    """Append a brand-new ``# name`` heading, with *description* as its
    FORMAT.md §3.1 description block — the CREATE-ONLY primitive behind
    §3.4's "Create category" (``POST /lora_library/notebook/category``,
    FORMAT.md §5, is the create-OR-describe composition of this and
    :func:`set_category_description`; the route picks one by checking
    whether *name* is already in :func:`list_categories`). Mutates
    *parsed*; returns ``{"name","description"}``.

    *name* must be unique among existing categories, non-empty after
    trimming, and free of embedded newlines (a category name is exactly one
    heading line, like an entry name — FORMAT.md §3.2). A non-empty
    *description* is stored like an entry's body (:func:`upsert_entry`'s
    ``text.split("\\n")``, no pre-trimming); an EMPTY one stores ZERO
    preamble lines (unlike an entry body's degenerate one-blank-line case)
    so "no description" never leaves a stray blank line dangling after the
    heading — FORMAT.md §3.1's "empty when absent", taken literally.

    *after*, when given, positions the new heading right after an existing
    entry OR category (FORMAT.md §3.4/§5 — the "New while a category is
    active" case) instead of appending at end-of-file. A CATEGORY name
    match (checked first; a hand-edited duplicate targets the LAST one,
    same convention as :func:`set_category_description`) is a whole-block
    insertion — the matched block itself is untouched. An ENTRY name match
    SPLITS that entry's block right after it: FORMAT.md's "the file is the
    truth" rule means whatever entries textually followed it in the old
    block (if any) now follow the new heading instead, so they become the
    new category's own entries — those :class:`Entry` objects are relocated
    by reference, never rebuilt, so their bodies travel byte-identically.
    *after* naming neither an entry nor a category, or omitted, falls back
    to the end-of-file append.

    Heading-looking lines in *description* are DEMOTED, not refused, since
    v0.48.1 (:func:`demote_unrepresentable_headings`); the count rides back
    in the return dict. Raises :class:`InvalidEntryNameError` for a blank
    or newline-containing *name*, or :class:`NameCollisionError` if *name*
    already names a category.
    """
    name = (name or "").strip()
    if not name:
        raise InvalidEntryNameError("category name must not be empty — FORMAT.md §3.2")
    if "\n" in name:
        raise InvalidEntryNameError("category name must not contain newlines — FORMAT.md §3.2")
    if any(block.heading_line is not None and block.name == name for block in parsed.blocks):
        raise NameCollisionError(f"a category named {name!r} already exists — FORMAT.md §3.4")

    description = description or ""
    # Same §3.4 demote-don't-refuse transform as upsert_entry — see
    # demote_unrepresentable_headings.
    description, adjusted_headings = demote_unrepresentable_headings(description)

    block = CategoryBlock(
        name=name, heading_line=f"# {name}", preamble=description.split("\n") if description else []
    )

    after = (after or "").strip()
    after_category = _find_last_block(parsed, after) if after else None
    if after_category is not None:
        parsed.blocks.insert(parsed.blocks.index(after_category) + 1, block)
    else:
        after_entry = _find_addressable(parsed, after) if after else None
        if after_entry is not None:
            src_block, entry = after_entry
            split_at = src_block.entries.index(entry) + 1
            block.entries = src_block.entries[split_at:]
            del src_block.entries[split_at:]
            parsed.blocks.insert(parsed.blocks.index(src_block) + 1, block)
        else:
            parsed.blocks.append(block)
    return {
        "name": name,
        "description": _trimmed_text(block.preamble),
        "adjusted_headings": adjusted_headings,
    }


def set_category_description(parsed: ParsedNotebook, name: str, description: str) -> dict:
    """Replace the FORMAT.md §3.1 description block under the category
    *name* — the UPDATE-ONLY primitive behind §3.4's "Set category
    description" (see :func:`create_category`'s docstring for how the §5
    route composes the two). A repeated category name targets the LAST
    block with that name, same convention as :func:`create_category`'s
    uniqueness check and ``move_entry``'s category placement. Mutates
    *parsed*; returns ``{"name","description","adjusted_headings"}`` —
    heading-looking description lines are demoted, not refused, since
    v0.48.1 (:func:`demote_unrepresentable_headings`).

    Raises :class:`CategoryNotFoundError` if no category named *name*
    exists.
    """
    description = description or ""
    # Same §3.4 demote-don't-refuse transform as upsert_entry — see
    # demote_unrepresentable_headings.
    description, adjusted_headings = demote_unrepresentable_headings(description)

    name = (name or "").strip()
    block = _find_last_block(parsed, name) if name else None
    if block is None:
        raise CategoryNotFoundError(f"no such category {name!r} — FORMAT.md §3.2")

    # Same "empty stores zero lines" rule as create_category — see its
    # docstring.
    block.preamble = description.split("\n") if description else []
    return {
        "name": name,
        "description": _trimmed_text(block.preamble),
        "adjusted_headings": adjusted_headings,
    }


def set_category_name(parsed: ParsedNotebook, name: str, new_name: str) -> dict:
    """Rename category *name*'s heading to *new_name* — the RENAME-ONLY
    primitive behind FORMAT.md §3.4/§5's category ``rename_to`` (a third
    primitive the ``POST /lora_library/notebook/category`` route composes
    alongside :func:`set_category_description` on a KNOWN *name*, per
    :func:`create_category`'s docstring). A repeated category name targets
    the LAST block with that name, same convention as
    :func:`set_category_description`. Mutates *parsed*; returns
    ``{"name","description"}`` for the renamed block.

    Renaming to *name*'s own current value (after trimming) is a
    documented plain no-op — same convention as :func:`upsert_entry`'s
    entry rename — so resaving an unchanged name from the §7.2 editor's
    name field never raises.

    Raises :class:`CategoryNotFoundError` if no category named *name*
    exists, :class:`InvalidEntryNameError` for a blank or newline-containing
    *new_name*, or :class:`NameCollisionError` if *new_name* already names a
    DIFFERENT existing category (FORMAT.md §3.4's "unique among
    categories").
    """
    name = (name or "").strip()
    block = _find_last_block(parsed, name) if name else None
    if block is None:
        raise CategoryNotFoundError(f"no such category {name!r} — FORMAT.md §3.2")

    new_name = (new_name or "").strip()
    if not new_name:
        raise InvalidEntryNameError("category name must not be empty — FORMAT.md §3.2")
    if "\n" in new_name:
        raise InvalidEntryNameError("category name must not contain newlines — FORMAT.md §3.2")
    if new_name != name and any(
        b.heading_line is not None and b.name == new_name for b in parsed.blocks
    ):
        raise NameCollisionError(f"a category named {new_name!r} already exists — FORMAT.md §3.4")

    block.name = new_name
    block.heading_line = f"# {new_name}"
    return {"name": new_name, "description": _trimmed_text(block.preamble)}


def move_entry(
    parsed: ParsedNotebook,
    name: str,
    *,
    before: str | None = None,
    category: str | None = None,
) -> dict:
    """Relocate the addressable entry *name* — FORMAT.md §3.4 Move, the
    primitive behind §5's ``POST /lora_library/notebook/move`` and §7.2's
    drag-reorder. Mutates *parsed* in place; returns ``{"name","category"}``
    for the entry's new position.

    Exactly one of *before*/*category* must be given (the route validates
    this and 400s before calling here; this is just the precondition):

    - *before*: move *name* to just before the named sibling entry. Moving
      an entry before itself (``before == name``) is a documented no-op —
      returned unchanged rather than treated as an error, matching a
      drag-reorder that drops a row back on itself.
    - *category*: move *name* to the END of that category's entries,
      creating the category heading at end-of-file when *category* names
      one that doesn't exist yet (mirrors :func:`upsert_entry`'s create
      placement — a repeated category name lands in the LAST one). ``""``
      targets ``parsed.blocks[0]`` specifically — the always-present
      implicit leading block (FORMAT.md's "uncategorized head region") —
      rather than searching by name, so it always means "just before the
      file's first H1, or end of file if there is none," never a
      coincidentally empty-titled ``#`` heading elsewhere in a hand-edited
      file.

    The moved :class:`Entry` object is relocated by reference (removed from
    one block's ``entries`` list, inserted/appended into another's) — never
    rebuilt — so its heading line and body travel byte-identically.

    Raises :class:`EntryNotFoundError` if *name* or *before* doesn't name
    an existing addressable entry.
    """
    if (before is None) == (category is None):
        raise ValueError("move_entry requires exactly one of 'before' or 'category'")

    name = (name or "").strip()
    found = _find_addressable(parsed, name)
    if found is None:
        raise EntryNotFoundError(f"no such entry {name!r} — FORMAT.md §3.2")
    src_block, entry = found

    if before is not None:
        before = before.strip()
        if before == entry.name:
            return {"name": entry.name, "category": src_block.name}
        target = _find_addressable(parsed, before)
        if target is None:
            raise EntryNotFoundError(
                f"no such entry {before!r} to move before — FORMAT.md §3.2"
            )
        dst_block, sibling = target
        src_block.entries.remove(entry)
        dst_block.entries.insert(dst_block.entries.index(sibling), entry)
        return {"name": entry.name, "category": dst_block.name}

    category = (category or "").strip()
    dst_block = parsed.blocks[0] if category == "" else _find_last_block(parsed, category)
    if dst_block is None:
        dst_block = CategoryBlock(name=category, heading_line=f"# {category}")
        parsed.blocks.append(dst_block)
    src_block.entries.remove(entry)
    dst_block.entries.append(entry)
    return {"name": entry.name, "category": dst_block.name}


def move_category(
    parsed: ParsedNotebook,
    name: str,
    *,
    before: str | None = None,
) -> dict:
    """Relocate the whole :class:`CategoryBlock` named *name* — FORMAT.md
    §3.4 Move category, the primitive behind §5's ``POST
    /lora_library/notebook/move_category`` and §7.2's drag-a-category-
    header. Mutates *parsed* in place; returns ``{"name"}``.

    - *before* omitted (``None``): move *name* to the end of the file.
    - *before* given: move *name* to just before that named category.
      Moving a category before itself (``before == name``, after trimming)
      is a documented no-op, same convention as :func:`move_entry`'s
      ``before == entry.name``. An explicitly blank *before* (``""``, as
      opposed to omitted — the route tells the two apart the same way
      ``post_notebook_move`` already does for entries) is looked up like
      any other name and fails as "not found", the same "blank is never a
      valid name" outcome :func:`move_entry` gets for free from
      ``_find_addressable`` never matching an empty name.

    The moved :class:`CategoryBlock` object is relocated by reference
    (removed from and reinserted into ``parsed.blocks``) — never rebuilt —
    so its heading, §3.1 description, and every entry's body travel
    byte-identically, and the entries' relative order inside the block
    (left untouched) is preserved.

    A repeated category name targets the LAST block with that name for
    both *name* and *before*, same convention as
    :func:`set_category_description`.

    The uncategorized head block (``""``, FORMAT.md §3.1's implicit leading
    region) is not a "category" in this sense and can never be moved — a
    blank/whitespace-only *name* always raises, regardless of any
    hand-edited blank-titled ``#`` heading elsewhere in the file (the same
    blanket rule :func:`get_category_description` uses for the same
    reason).

    Raises :class:`CategoryNotFoundError` if *name* is blank or names no
    existing category, or if a given *before* names no existing category.
    """
    name = (name or "").strip()
    if not name:
        raise CategoryNotFoundError(
            "the uncategorized head region is not a movable category — FORMAT.md §3.4"
        )
    block = _find_last_block(parsed, name)
    if block is None:
        raise CategoryNotFoundError(f"no such category {name!r} — FORMAT.md §3.2")

    if before is None:
        parsed.blocks.remove(block)
        parsed.blocks.append(block)
        return {"name": name}

    before = before.strip()
    if before == name:
        return {"name": name}
    target = _find_last_block(parsed, before) if before else None
    if target is None:
        raise CategoryNotFoundError(f"no such category {before!r} to move before — FORMAT.md §3.2")
    parsed.blocks.remove(block)
    parsed.blocks.insert(parsed.blocks.index(target), block)
    return {"name": name}


# --------------------------------------------------------------------- I/O

def detect_line_ending(text: str) -> str:
    """The dominant line ending in *text*: ``"\\r\\n"`` if CRLF pairs outnumber
    lone ``"\\n"``s, else ``"\\n"`` (also the default for new/empty text,
    FORMAT.md §3.6)."""
    crlf = text.count("\r\n")
    lf_only = text.count("\n") - crlf
    return "\r\n" if crlf > lf_only else "\n"


def load_notebook(path: Path) -> tuple[ParsedNotebook, float | None, str]:
    """Parse *path*. Returns ``(parsed, mtime, line_ending)``; a missing file
    yields an empty notebook, ``mtime=None``, and the §3.6 new-file default
    ``"\\n"`` — not an error (FORMAT.md §5: ``GET /notebook`` reports
    ``exists: false``). Reading with ``newline=""`` (no universal-newline
    translation) so :func:`detect_line_ending` sees the file's real bytes.
    """
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return parse(""), None, "\n"
    # Audit 2026-08-21: a file that EXISTS but cannot be read as a UTF-8
    # text file -- a directory named as the file (IsADirectoryError), a
    # notes file saved as UTF-16/CP-1252 (UnicodeDecodeError, a plain
    # ValueError), a permission blip -- used to escape as a 500 with a
    # traceback out of every notebook route. It is the store's own error
    # family now, which every mutating route already maps to a clean 400
    # and the read routes map below.
    except (OSError, ValueError) as exc:
        raise MarkdownStoreError(f"could not read {path.name}: {exc}") from exc
    mtime = path.stat().st_mtime
    return parse(raw), mtime, detect_line_ending(raw)


def save_notebook(path: Path, parsed: ParsedNotebook, line_ending: str = "\n") -> float:
    """Serialize *parsed* and atomically write it to *path*, using
    *line_ending* throughout (FORMAT.md §3.6). Returns the new mtime.

    Trailing blank lines are collapsed into the single guaranteed trailing
    newline (§3.4's one named exception to byte-for-byte preservation).
    """
    lines = serialize(parsed)
    while lines and lines[-1] == "":
        lines.pop()
    text = (line_ending.join(lines) + line_ending) if lines else ""
    _atomic_write_text(path, text)
    return path.stat().st_mtime


def check_conflict(base_mtime: float | None, current_mtime: float | None) -> None:
    """Raise :class:`ConflictError` per FORMAT.md §3.5, else return ``None``.

    A missing file (``current_mtime is None``) has nothing to conflict
    with — treated as "first save to a brand-new file" even if a stale
    *base_mtime* was sent, same as omitting *base_mtime* outright.
    """
    if base_mtime is None or current_mtime is None:
        return
    if abs(current_mtime - base_mtime) > 1e-6:
        raise ConflictError(current_mtime)
