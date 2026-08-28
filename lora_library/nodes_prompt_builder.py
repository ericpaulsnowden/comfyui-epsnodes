"""``EPSPromptBuilder`` (FORMAT.md §6.15, display: "EPS Prompt Builder") --
compose a prompt from named EPS Prompt Notebook entries plus optional
piped-in text.

A companion to ``nodes_notebook.py``'s ``LoraLibraryNotebook``: the panel
shows the SAME notebook file as a left list (read-only here -- all editing
still happens on the Notebook node) and a right list of named "blocks" the
user builds up by clicking entries, in composition order. At run time this
node combines them into one prompt: whatever text arrives piped in (if
anything) goes FIRST, then every named block's text, in order, joined by
``separator``. A companion ``name`` output does the same for names, joined
by ``+``, so a save path built from it stays readable.

**Why one output per incoming text (owner ask, 2026-08-23).** A multi-select
Prompt Notebook wired straight into ``text`` emits a LIST -- one string per
selected entry, that being the whole point of its multi-select fan-out into
EPS Run Multiplier. This node has to keep that axis alive rather than
collapsing it: with ``text`` wired, ``build`` returns one combined result
PER incoming text (the blocks are appended to every one of them), so a
five-entry multi-select Notebook feeding this node still drives five
downstream runs, each carrying the same blocks tacked onto its own piped
text. ``text`` unwired is the degenerate case -- a single output made from
the blocks alone (no incoming part at all) -- which is why
``INPUT_IS_LIST``/``OUTPUT_IS_LIST`` are both declared on this node exactly
like ``EPSCrossSweep`` (FORMAT.md §6.10): every input (including the
otherwise-scalar ``file``/``blocks``/``separator`` widgets) arrives already
list-wrapped, and small local unwrap helpers below undo that -- adapted
from, not imported from, ``eps_image/nodes_cross_sweep.py``'s
``_unwrap_scalar`` (own-your-helpers precedent: this module owns its own
tiny helpers rather than reaching into a sibling family's module for
conceptually identical ones).

**Live-by-name, not frozen copies (owner decision, 2026-08-23).** A block
is a NAME, not a snapshot: every ``build`` call re-parses the notebook file
and re-resolves each named block's text via ``markdown_store.get_entry``
fresh, exactly like the Notebook's own "the file is the truth" posture
(FORMAT.md §6). Editing a block's text in the Notebook and re-running this
node picks up the edit automatically -- there is no separate baked copy to
go stale. ``IS_CHANGED`` folds the notebook file's on-disk state (mtime +
size, or a "missing" token) into the cache key for exactly this reason: if
the file changes underneath an unchanged set of widget/link values, this
node must still re-execute, or the "live" promise above would be a lie the
very first time nothing else about the node changed. Every other declared
input (``blocks``/``separator``/``text``/``name``) is already part of
ComfyUI's own default input-hash cache key, so ``IS_CHANGED`` here folds in
ONLY the extra thing that key can't see on its own: the file's bytes on
disk.

**Loud missing-name posture, same as the Notebook's own (FORMAT.md §6.1).**
``blocks`` records entry NAMES, not positions -- renaming or deleting an
entry in the Notebook after it was added to a Builder's block list leaves a
stale name sitting in a saved workflow. Because blocks resolve LIVE (not
frozen), a stale name is exactly the situation where "quietly drop it" would
produce a wrong prompt with no sign anything was missing. So ANY block name
this node can't find raises a queue-time ``ValueError`` naming EVERY missing
name plus the file -- never a partial or silently-shrunk composition --
mirroring ``EPSCrossSweep``'s ``solo_run`` no-match error and the Notebook's
own missing-entry error. This is the ONE loud failure mode; malformed
``blocks`` JSON itself is the opposite case (see below) and degrades
quietly, because a corrupt/stale WIDGET value is not the same problem as a
named entry that no longer exists.

**Frontend contract (pre-agreed, do not deviate).** ``INPUT_TYPES``
declares, in this exact order (``widgets_values`` restores positionally):
``file`` (STRING, default ``"loras.md"``, hidden -- the frontend mirrors a
Notebook's ``file`` widget into this one), ``blocks`` (STRING, default
``"[]"``, hidden -- a JSON array of entry NAME strings, in composition
order, e.g. ``["Portrait", "Cinematic"]``; malformed/non-array JSON parses
to ``[]`` plus a logged warning, NEVER an exception at parse time -- only a
name that fails to RESOLVE, later, is the loud error above), then
``separator`` (STRING, default ``", "``, a visible widget -- ``\\n``/``\\t``/
``\\\\`` are decoded to a real newline/tab/backslash before use; an empty
separator is a valid plain concatenation). ``text``/``name`` are optional,
``forceInput``-only STRING inputs (no widget) for the piped-in prompt(s)/
name(s).

No torch/ComfyUI import at module scope, same importable-without-ComfyUI
seam as ``nodes_notebook.py`` and ``markdown_store.py`` -- this node never
touches model/clip weights, only markdown text.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, ClassVar

from . import markdown_store
from .context import DEFAULT_NOTEBOOK_FILENAME, LibraryContext

logger = logging.getLogger("lora_library")

_context: LibraryContext | None = None

#: INPUT_TYPES' own defaults -- named here so IS_CHANGED/build's unwrap
#: fallbacks can never drift from what the widgets themselves declare.
DEFAULT_FILE = "loras.md"
DEFAULT_BLOCKS = "[]"
DEFAULT_SEPARATOR = ", "

#: The literal escapes `separator` decodes -- a small, fixed vocabulary
#: (module docstring), not general Python string-escape decoding.
_SEPARATOR_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\"}


def set_context(context: LibraryContext | None) -> None:
    """Wire the shared :class:`LibraryContext` into this module.

    Called once from the pack's ``__init__.py`` (real runs); tests call it
    directly against a fake context. Accepts ``None`` so tests can reset the
    module-level global between cases without leaking state -- verbatim the
    same contract as ``nodes_notebook.set_context``.
    """
    global _context
    _context = context


# --------------------------------------------------------- INPUT_IS_LIST unwrap
#
# Adapted from (not imported from) eps_image/nodes_cross_sweep.py's helpers
# of the same names -- own-your-helpers precedent (module docstring).


def _unwrap_scalar(value: Any, default: str) -> str:
    """First element of an ``INPUT_IS_LIST``-wrapped widget value, tolerating
    the bare (already-scalar) form too -- e.g. a test or direct caller that
    passes ``"loras.md"`` straight instead of ``["loras.md"]``."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else default
    if value is None:
        return default
    return str(value)


def _as_list(value: Any) -> list[Any]:
    """*value* as a plain list -- the shape an ``INPUT_IS_LIST`` LINK input
    already arrives in (the full upstream list, not sliced per-index).
    Tolerates a bare scalar (direct/test callers) and ``None`` (callers
    handle "unwired" themselves before this is ever reached; here it is
    just "nothing to list")."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


# ------------------------------------------------------------- separator decode


def _decode_separator(raw: str) -> str:
    """Decode ``\\n``/``\\t``/``\\\\`` escapes in *raw* into real
    newline/tab/backslash characters (module docstring: a plain STRING
    widget can't hold a literal newline, so ``\\n`` is how the user asks for
    one). Only these three escapes are recognized; any other backslash
    sequence -- ``\\d``, a trailing lone ``\\`` -- passes through UNCHANGED
    rather than being swallowed or raising. Empty input decodes to empty
    output (a valid plain-concatenation separator)."""
    out: list[str] = []
    i = 0
    length = len(raw)
    while i < length:
        ch = raw[i]
        if ch == "\\" and i + 1 < length and raw[i + 1] in _SEPARATOR_ESCAPES:
            out.append(_SEPARATOR_ESCAPES[raw[i + 1]])
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


# --------------------------------------------------------------- blocks parsing


def _parse_blocks(raw: str) -> list[str]:
    """*raw* (the ``blocks`` widget's JSON text) as a list of entry name
    strings, in composition order. Tolerant, never raises: malformed JSON,
    or JSON that doesn't parse to an array, degrades to ``[]`` plus a
    logged warning -- a stale/corrupt WIDGET value must never crash the
    queue at parse time (module docstring: only a name that fails to
    RESOLVE, later, is the loud failure). A blank/whitespace-only element
    is dropped (there is no such thing as a block named ``""``); any other
    element is stringified rather than dropped, so a hand-edited JSON array
    of e.g. numbers still produces lookups (which then fail loudly, as
    they should, if nothing matches)."""
    if not raw or not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError) as exc:
        logger.warning(
            "EPS Prompt Builder: malformed `blocks` value (%s); treating as no blocks",
            exc,
        )
        return []
    if not isinstance(parsed, list):
        logger.warning(
            "EPS Prompt Builder: `blocks` was not a JSON array (got %r); treating as no blocks",
            type(parsed).__name__,
        )
        return []
    names: list[str] = []
    for item in parsed:
        text = item if isinstance(item, str) else str(item)
        if text.strip():
            names.append(text)
    return names


# ---------------------------------------------------------------- file resolving


def _peek_resolved_path(context: LibraryContext, file_value: str) -> Path:
    """Best-effort mirror of :meth:`LibraryContext.resolve_notebook_file`
    that never creates anything -- adapted from
    ``nodes_notebook._peek_resolved_path`` (own-your-helpers precedent) so
    an unreachable configured ``library_dir`` (its ``mkdir`` raising
    ``OSError`` before a path is ever produced) still lets the
    missing-block error below name a path, instead of leaking a raw
    traceback. See that function's docstring for the full owner-report
    rationale (2026-07-19); only the relative-``file`` branch ever consults
    this -- absolute values pass through untouched either way."""
    value = (file_value or "").strip() or DEFAULT_NOTEBOOK_FILENAME
    path = Path(value)
    if path.is_absolute():
        return path
    configured = context.load_config().get("library_dir")
    base = Path(configured) if configured else context.default_library_dir
    return base / path


def _resolve_blocks(
    context: LibraryContext | None, file: str, block_names: list[str]
) -> list[tuple[str, str]]:
    """Resolve *block_names* (in order, duplicates allowed) against
    notebook *file*'s LIVE addressable entries -- the module's
    live-by-name contract (module docstring). Returns ``[(name, text),
    ...]`` in the SAME order as *block_names*.

    Zero *block_names* is always a no-op success -- ``[]`` -- without ever
    touching the file or requiring a context: an empty block list needs
    nothing resolved, so a brand-new node with nothing configured yet must
    still work (module contract's "unwired text + zero blocks" case).

    Raises :class:`RuntimeError` if *block_names* is non-empty and no
    context is configured (mirrors ``read_entry``'s no-context posture --
    resolving anything at all needs a context to resolve it with).

    Raises :class:`ValueError` naming EVERY missing name plus *file* the
    instant any named block can't be found -- including when *file* itself
    doesn't exist (an empty parse leaves every lookup missing, which folds
    "file not found" into this very same loud error rather than a second,
    separate one).
    """
    if not block_names:
        return []
    if context is None:
        raise RuntimeError("EPSNodes: EPS Prompt Builder has no context configured")

    try:
        path = context.resolve_notebook_file(file)
    except OSError:
        # Same recovery as nodes_notebook.resolve_selection: an unreachable
        # configured library_dir makes the real resolve's `mkdir` raise
        # before it ever returns a path -- peek the same resolution without
        # creating anything so the error below can still name it.
        path = _peek_resolved_path(context, file)

    parsed, _mtime, _line_ending = markdown_store.load_notebook(path)

    resolved: list[tuple[str, str]] = []
    missing: list[str] = []
    for wanted in block_names:
        found = markdown_store.get_entry(parsed, wanted)
        if found is None:
            missing.append(wanted)
        else:
            resolved.append((found["name"], found["text"]))

    if missing:
        raise ValueError(
            f"EPS Prompt Builder: no block(s) named {missing!r} in {file!r} "
            f"(resolved: {path})"
        )
    return resolved


# -------------------------------------------------------------------- IS_CHANGED


def _file_token(path: Path) -> str:
    """*path*'s mtime+size as a cache-busting token, or a missing-marker --
    verbatim ``nodes_notebook._file_token`` (module docstring: an on-disk
    edit, or the file disappearing, must force a re-execution)."""
    try:
        stat = path.stat()
    except OSError:
        return "missing"
    return f"{stat.st_mtime}:{stat.st_size}"


#: ``markdown_store.load_notebook`` memoized by (mtime_ns, size) --
#: v0.80.0 sweep-performance round, this module's own copy of
#: ``nodes_notebook._load_notebook_cached`` (own-your-helpers precedent).
#: Re-validated against a fresh ``stat()`` every call; consumers only READ
#: the parsed object. Cleared wholesale past 16 files.
_PARSE_CACHE: dict[str, tuple[tuple[int, int], Any, float | None, str]] = {}


def _load_notebook_cached(path: Path) -> tuple[Any, float | None, str]:
    key = str(path)
    try:
        stat = path.stat()
        sig = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        _PARSE_CACHE.pop(key, None)
        return markdown_store.load_notebook(path)
    hit = _PARSE_CACHE.get(key)
    if hit is not None and hit[0] == sig:
        return hit[1], hit[2], hit[3]
    parsed, mtime, line_ending = markdown_store.load_notebook(path)
    if len(_PARSE_CACHE) > 16:
        _PARSE_CACHE.clear()
    _PARSE_CACHE[key] = (sig, parsed, mtime, line_ending)
    return parsed, mtime, line_ending


def _blocks_token(context: LibraryContext | None, file: str, blocks_raw: str) -> str:
    """Content-derived ``IS_CHANGED`` token (v0.80.0 sweep-performance
    round; the notebook's ``_selection_token`` rationale applies verbatim).
    Through v0.79.0 this was the whole file's mtime+size, so editing ANY
    entry -- even one no block references -- invalidated every sweep built
    on this node. The token now hashes only what ``build()`` emits: the
    resolved path plus each BLOCK's name and current text (``<missing>``
    for an absent name). Zero blocks needs no file and no context at all
    (mirrors ``_resolve_blocks``'s zero-blocks shortcut), so it returns a
    constant. Every widget/link input is already inside core's own
    input-hash key -- this only tracks the file CONTENT's contribution."""
    names = _parse_blocks(blocks_raw)
    if not names:
        return "no-blocks"
    if context is None:
        return f"no-context:{file}"
    try:
        path = context.resolve_notebook_file(file)
    except OSError:
        path = _peek_resolved_path(context, file)
    parsed, mtime, _line_ending = _load_notebook_cached(path)
    if mtime is None:
        return f"missing:{path}"
    digest = hashlib.sha1(str(path).encode("utf-8", "replace"))
    for name in names:
        found = markdown_store.get_entry(parsed, name)
        text = (found or {}).get("text", "\x00<missing>")
        digest.update(b"\x1f")
        digest.update(str(name).encode("utf-8", "replace"))
        digest.update(b"\x1e")
        digest.update(str(text).encode("utf-8", "replace"))
    return digest.hexdigest()


class EPSPromptBuilder:
    """Compose a prompt from named EPS Prompt Notebook entries plus
    optional piped-in text (FORMAT.md §6.15). See the module docstring for
    the full contract, the live-by-name decision, and the loud
    missing-name posture.

    ``INPUT_IS_LIST``/``OUTPUT_IS_LIST`` are both declared: with ``text``
    wired to a multi-select Notebook's list output, ``build`` returns one
    combined result PER incoming text (blocks appended to each), keeping
    that list's fan-out alive for whatever runs downstream (e.g. EPS Run
    Multiplier). ``text`` unwired is the degenerate single-result case --
    the blocks alone, nothing piped in.
    """

    CATEGORY = "EPSNodes"
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("text", "name")
    OUTPUT_IS_LIST = (True, True)
    INPUT_IS_LIST = True
    OUTPUT_TOOLTIPS = (
        "The combined prompt: the piped-in text (if wired) first, then every "
        "named block's text, in order, joined by separator. One result per "
        "incoming text when text is wired; a single result made from the "
        "blocks alone when it isn't.",
        "The combined name: the piped-in name (if any) plus every named "
        "block's name, joined by +. Empty when nothing named this pass.",
    )
    FUNCTION = "build"
    DESCRIPTION = (
        "Builds a prompt from an EPS Prompt Notebook's named entries: pick "
        "entries in the right-hand list (the left list mirrors a "
        "Notebook's file, read-only here -- all editing stays on the "
        "Notebook node) and they combine, in order, behind the scenes. "
        "Pipe in text from a Notebook or any STRING source and it comes "
        "FIRST, ahead of the blocks. Wire a multi-select Notebook straight "
        "in and every selected entry still gets its own output, with the "
        "same blocks appended to each -- the sweep axis for EPS Run "
        "Multiplier stays intact. Blocks resolve LIVE every run, so "
        "editing an entry's text on the Notebook and re-running this node "
        "picks up the edit automatically; renaming or deleting an entry "
        "still referenced here fails the queue loudly, naming every "
        "missing block, rather than silently building a wrong prompt."
    )

    #: §6.16 state registry (v0.83.0): the widgets a Universal State
    #: Controller may capture/apply, declared next to the parser that owns
    #: their shape. ``text``/``name`` are wire-only (forceInput, no widget)
    #: and never appear here.
    EPS_STATE_WIDGETS: ClassVar[dict[str, Any]] = {
        "format": 1,
        "widgets": {
            "file": {"kind": "string", "max_len": 10000},
            "blocks": {"kind": "json_array", "items": "string"},
            "separator": {"kind": "string", "max_len": 10000},
        },
    }

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                # "hidden": True is the Vue-nodes ("New node design") hide
                # flag, same as nodes_notebook.py's `file`/`entry` widgets:
                # that renderer decides widget visibility from the input
                # spec's options and ignores litegraph's `widget.hidden`,
                # so this key is what keeps these internal widgets from
                # leaking into Vue nodes as raw editable fields. The
                # classic canvas renderer ignores this key right back.
                "file": ("STRING", {"default": DEFAULT_FILE, "hidden": True}),
                "blocks": (
                    "STRING",
                    {
                        "default": DEFAULT_BLOCKS,
                        "hidden": True,
                        "tooltip": (
                            "JSON array of Notebook entry names, in "
                            "composition order. Filled in automatically as "
                            "you click entries in the right-hand list; you "
                            "don't need to type into it directly."
                        ),
                    },
                ),
                "separator": (
                    "STRING",
                    {
                        "default": DEFAULT_SEPARATOR,
                        "multiline": False,
                        "tooltip": (
                            "Joins the piped-in text (if any) and every "
                            "named block's text, in that order. Type \\n "
                            "for a newline, \\t for a tab, \\\\ for a "
                            "literal backslash. Empty is allowed -- the "
                            "parts are concatenated directly."
                        ),
                    },
                ),
            },
            "optional": {
                "text": (
                    "STRING",
                    {
                        "forceInput": True,
                        "tooltip": (
                            "Optional piped-in prompt text(s) -- wire from "
                            "an EPS Prompt Notebook or any STRING source. "
                            "Always comes FIRST in the combined result. "
                            "With a multi-select Notebook wired in, each "
                            "incoming text becomes its own output, with "
                            "the same blocks appended to every one. "
                            "Unwired: a single output made from the "
                            "blocks alone. Wire-only."
                        ),
                    },
                ),
                "name": (
                    "STRING",
                    {
                        "forceInput": True,
                        "tooltip": (
                            "Optional piped-in name(s), paired "
                            "index-for-index with text (a single name "
                            "broadcasts across every text). Combined with "
                            "the block names into this output's name, "
                            "joined by +. Wire-only."
                        ),
                    },
                ),
            },
        }

    @classmethod
    def IS_CHANGED(
        cls,
        file: Any,
        blocks: Any,
        separator: Any,
        text: Any = None,
        name: Any = None,
    ) -> str:
        # v0.80.0: content-derived, not whole-file mtime -- see
        # _blocks_token. blocks/separator/text/name are already part of
        # ComfyUI's own input-hash cache key; this only has to track what
        # the file's CONTENT contributes through the named blocks.
        file_value = _unwrap_scalar(file, DEFAULT_FILE)
        blocks_value = _unwrap_scalar(blocks, DEFAULT_BLOCKS)
        return _blocks_token(_context, file_value, blocks_value)

    def build(
        self,
        file: Any,
        blocks: Any,
        separator: Any,
        text: Any = None,
        name: Any = None,
    ) -> tuple[list[str], list[str]]:
        file_value = _unwrap_scalar(file, DEFAULT_FILE)
        blocks_value = _unwrap_scalar(blocks, DEFAULT_BLOCKS)
        separator_value = _unwrap_scalar(separator, DEFAULT_SEPARATOR)
        sep = _decode_separator(separator_value)

        block_names = _parse_blocks(blocks_value)
        block_pairs = _resolve_blocks(_context, file_value, block_names)
        block_texts = [block_text for _block_name, block_text in block_pairs]
        block_names_resolved = [block_name for block_name, _block_text in block_pairs]

        # `is not None` distinguishes UNWIRED (a single pass, no incoming
        # part -- module contract) from wired-but-empty (a real upstream
        # emitted nothing -> zero outputs, index alignment preserved).
        text_wired = text is not None
        name_wired = name is not None
        texts = _as_list(text) if text_wired else [None]
        names = _as_list(name) if name_wired else []

        if name_wired and len(names) not in (1, len(texts)):
            logger.warning(
                "EPS Prompt Builder: `name` has %d value(s) but `text` has "
                "%d -- pairing what overlaps and using \"\" past the end "
                "of the shorter list",
                len(names), len(texts),
            )

        out_texts: list[str] = []
        out_names: list[str] = []
        for index, incoming_text in enumerate(texts):
            has_incoming_text = incoming_text is not None and str(incoming_text).strip()
            parts = ([str(incoming_text)] if has_incoming_text else []) + block_texts
            out_texts.append(sep.join(parts))

            incoming_name = ""
            if name_wired:
                if len(names) == 1:
                    incoming_name = names[0]
                elif index < len(names):
                    incoming_name = names[index]
            has_incoming_name = str(incoming_name).strip()
            name_parts = (
                [str(incoming_name)] if has_incoming_name else []
            ) + block_names_resolved
            out_names.append("+".join(name_parts))

        return (out_texts, out_names)
