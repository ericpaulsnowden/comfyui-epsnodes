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
    """Raised when a create is attempted with a blank/whitespace-only name."""


class InvalidEntryTextError(MarkdownStoreError):
    """Entry text contains a line that would be read back as a heading
    (FORMAT.md §3.4's "cannot be represented" rule)."""


class NameCollisionError(MarkdownStoreError):
    """``rename_to`` names a different entry that already exists."""


class EntryNotFoundError(MarkdownStoreError):
    """``move_entry``'s *name* or *before* names no existing addressable
    entry (FORMAT.md §3.2) — the route layer maps this to 404."""


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


def entry_text(entry: Entry) -> str:
    """*entry*'s body minus leading/trailing blank lines, joined with ``\\n``
    (FORMAT.md §3.3 — this exact string is the node's STRING output)."""
    body = entry.body
    start, end = 0, len(body)
    while start < end and body[start].strip() == "":
        start += 1
    while end > start and body[end - 1].strip() == "":
        end -= 1
    return "\n".join(body[start:end])


def find_unrepresentable_heading_line(text: str) -> int | None:
    """1-based line number of the first line in *text* that would be read
    back as an H1/H2 heading outside a fence, or ``None`` if *text* is safe
    to store as an entry body (FORMAT.md §3.4)."""
    for line_no, _line, level in _iter_lines_with_heading_level(text):
        if level:
            return line_no
    return None


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


# ------------------------------------------------------------------ mutation

def upsert_entry(
    parsed: ParsedNotebook,
    name: str,
    text: str,
    *,
    category: str | None = None,
    rename_to: str | None = None,
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

    Raises :class:`InvalidEntryTextError` if *text* contains an
    unrepresentable heading-looking line, :class:`InvalidEntryNameError` for
    a blank *name* on create, or :class:`NameCollisionError` if *rename_to*
    already names a different entry.
    """
    name = (name or "").strip()
    category = (category or "").strip()
    rename_to = (rename_to or "").strip()

    bad_line = find_unrepresentable_heading_line(text)
    if bad_line is not None:
        raise InvalidEntryTextError(
            f"line {bad_line} of the entry text starts with '#'/'##', which would be "
            "read back as a heading; use '###', indentation, or a code fence instead "
            "— FORMAT.md §3.4"
        )
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
        return {"name": entry.name, "category": block.name}

    if not name:
        raise InvalidEntryNameError("entry name must not be empty — FORMAT.md §3.2")

    new_entry = Entry(name=name, heading_line=f"## {name}", body=body_lines, addressable=True)
    target = _find_last_block(parsed, category) if category else parsed.blocks[-1]
    if target is None:
        target = CategoryBlock(name=category, heading_line=f"# {category}")
        parsed.blocks.append(target)
    target.entries.append(new_entry)
    return {"name": name, "category": target.name}


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
