"""The ``LoraLibraryNotebook`` ComfyUI node (FORMAT.md §6.1, display: "LoRA
Notebook").

Unlike ``nodes_sets.py``, this node never touches model/clip weights, so
there's no lazy ``comfy.*`` import seam to mirror — no ComfyUI import
appears anywhere in this module, and it is importable in a plain test
environment as-is.

**Provenance M3 (v0.71.0, FORMAT.md §6.1 ``pinned``):** a TAIL-appended,
hidden ``pinned`` STRING widget. Empty = live (today's behavior: the file
is re-read on every run). Non-empty = the pin JSON EPS Save Image (§6.14)
captured when an image was made -- ``{"format": 1, "entries": [{"name",
"text"}, ...], "source": {"file", "token", "captured"}}`` -- and
``read_entry`` outputs THOSE entries' text/name lists, in that order,
without touching the file (which may be gone). That is what makes a
dropped image recreate byte-for-byte after the notebook was edited; the
frontend shows the pinned (old) text on the node with a one-click unpin.
A malformed pin, or one with no entries, logs a warning and falls back to
live rather than failing the queue.

**Unsaved-edit drafts (v0.86.0, owner ask 2026-08-28: "if you change a
prompt that is selected and run it without saving, it should run the
changed prompt").** A second TAIL-appended, hidden ``drafts`` STRING
widget (default ``"{}"``), a JSON object mapping selected entry name ->
unsaved text. ``resolve_selection`` -- the shared LIVE path ``read_entry``
and §6.14's pin capture both call -- applies a draft ON TOP OF the file's
text for any selected name ``drafts`` names, text only, never position
(FILE order, v0.85.0, is unaffected). A draft naming an unselected or
nonexistent entry is silently ignored (never an error -- like ``pinned``,
this is a scratch buffer the panel maintains, not a contract the queue can
enforce), and a malformed ``drafts`` value degrades to "no drafts" with a
logged warning, same philosophy as :func:`parse_pinned`. Pinned mode wins
outright: while ``pinned`` holds a valid pin, drafts are never consulted
(the pin already IS the frozen text a run used). ``IS_CHANGED`` folds the
effective (draft-overridden) text into its content digest, so a changed
draft alone re-executes the node.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, ClassVar

from . import markdown_store
from .context import (
    DEFAULT_NOTEBOOK_FILENAME,
    LibraryContext,
    foreign_absolute_note,
    heal_foreign_absolute,
    is_foreign_absolute,
)

logger = logging.getLogger("lora_library")

_context: LibraryContext | None = None


def set_context(context: LibraryContext | None) -> None:
    """Wire the shared :class:`LibraryContext` into this module.

    Called once from the pack's ``__init__.py`` (real runs); tests call it
    directly against a fake context. Accepts ``None`` so tests can reset the
    module-level global between cases without leaking state (mirrors
    ``nodes_sets.set_context``).
    """
    global _context
    _context = context


def _file_token(path: Path) -> str:
    """*path*'s mtime+size as a cache-busting token, or a missing-marker
    (FORMAT.md §6.1's ``IS_CHANGED``: an on-disk edit from the other machine
    — or the file disappearing — must force a re-execution)."""
    try:
        stat = path.stat()
    except OSError:
        return "missing"
    return f"{stat.st_mtime}:{stat.st_size}"


def _notebook_token(context: LibraryContext | None, file: str, entry: str) -> str:
    if context is None:
        return f"no-context:{file}:{entry}"
    path = context.resolve_notebook_file(file)
    return f"{path}:{_file_token(path)}:{entry}"


#: ``markdown_store.load_notebook`` results memoized by (mtime_ns, size) --
#: v0.80.0 sweep-performance round: ``IS_CHANGED`` and ``read_entry`` both
#: parse the file each queue; the memo makes that one parse, re-validated
#: against a fresh ``stat()`` on every call so an on-disk edit (either
#: machine) is never missed. Parsed notebooks are only ever READ by their
#: consumers (``get_entry``/``list_entries``), so sharing one object is
#: safe. Tiny by design: cleared wholesale past 16 files.
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


def _selection_token(
    context: LibraryContext | None, file: str, entry: str, pinned: str, drafts: str = "{}"
) -> str:
    """Content-derived ``IS_CHANGED`` token (v0.80.0 sweep-performance
    round). Through v0.79.0 this was the whole file's mtime+size -- so
    editing ANY entry in a big library file, even one this node never
    selected, changed the token, which cascaded through core's recursive
    node signatures (``comfy_execution/caching.py`` ``get_node_signature``)
    and re-ran ENTIRE downstream sweeps. The token now derives from what
    this node actually EMITS:

    - a valid pin -> the constant ``"pinned"``: the pin JSON itself is a
      widget already inside core's input-hash key, and while pinned the
      FILE is irrelevant to the output -- so file edits (and drafts) must
      not re-run a pinned node at all;
    - live -> a sha1 over the resolved path plus each SELECTED entry's
      name and CURRENT EFFECTIVE text (a v0.86.0 ``drafts`` override when
      one names that entry, else the file's text; ``<missing>`` for an
      absent name -- a draft never rescues one, matching
      :func:`resolve_selection`), so an entry appearing/disappearing OR a
      draft being typed/cleared all flip the token. Unselected entries can
      change freely without invalidating a thing.

    Missing file / unreachable dir / no context degrade to coarse string
    tokens that still change when that situation changes. Belt-and-braces
    only: ``drafts`` is itself a WIDGET, already inside core's own
    input-hash key, so a changed draft re-executes regardless of this
    token -- see the module docstring's "Unsaved-edit drafts" paragraph."""
    if parse_pinned(pinned) is not None:
        return "pinned"
    if context is None:
        return f"no-context:{file}:{entry}"
    try:
        path = context.resolve_notebook_file(file)
    except OSError:
        path = _peek_resolved_path(context, file)
    parsed, mtime, _line_ending = _load_notebook_cached(path)
    if mtime is None:
        return f"missing:{path}"
    draft_map = parse_drafts(drafts)
    digest = hashlib.sha1(str(path).encode("utf-8", "replace"))
    for name in _selected_names(entry):
        found = markdown_store.get_entry(parsed, name)
        if found is None:
            text: Any = "\x00<missing>"
        else:
            text = draft_map.get(name, found["text"])
        digest.update(b"\x1f")
        digest.update(name.encode("utf-8", "replace"))
        digest.update(b"\x1e")
        digest.update(str(text).encode("utf-8", "replace"))
    return digest.hexdigest()


#: FORMAT.md §6.1 ``pinned`` widget: the pin JSON ``format`` this build
#: writes (EPS Save Image, §6.14) and reads.
PIN_FORMAT = 1

#: The widget's name -- EPS Save Image bakes it into the workflow/prompt
#: chunks by this name (FORMAT.md §6.14), the frontend reads it by this name.
PIN_WIDGET = "pinned"

#: The unsaved-edits widget's name (v0.86.0, FORMAT.md §6.1) -- TAIL-appended
#: after ``pinned`` (§8: widgets_values restores positionally). The panel
#: writes/clears it; nothing else ever should.
DRAFTS_WIDGET = "drafts"


def parse_pinned(raw: Any) -> list[dict[str, str]] | None:
    """The entries a ``pinned`` widget value pins, or ``None`` for LIVE.

    ``""`` / a non-string is live (no log -- the everyday state). Anything
    else must be the §6.1 pin JSON: an object whose ``entries`` is a
    non-empty list of ``{"name", "text"}`` objects with string ``text`` (a
    missing/non-string ``name`` reads as ``""``). A malformed pin, or one
    with zero entries, logs a warning and returns ``None`` so the node
    falls back to the live file rather than failing the queue -- the badge
    on the node is what explains a pin; the backend only has to degrade
    safely. The returned entries are fresh dicts (``{"name", "text"}``
    only), in pin order.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        logger.warning(
            "EPS Prompt Notebook: pinned value is not JSON (%r); reading the live file",
            raw[:80],
        )
        return None
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        logger.warning(
            "EPS Prompt Notebook: pinned value has no entries; reading the live file"
        )
        return None
    out: list[dict[str, str]] = []
    for item in entries:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            logger.warning(
                "EPS Prompt Notebook: pinned entry %r is malformed; reading the live file",
                item,
            )
            return None
        name = item.get("name")
        out.append({"name": name if isinstance(name, str) else "", "text": item["text"]})
    return out


def make_pin(
    entries: list[dict[str, str]],
    file: str,
    token: str | None,
    captured: str,
) -> dict[str, Any]:
    """The §6.1 pin JSON (as a dict -- callers ``json.dumps`` it into the
    widget) for *entries* (``{"name", "text"}`` each, in order) captured
    from notebook *file* while saving run *token* at *captured* (ISO-8601
    UTC). Built by EPS Save Image (§6.14) at save time; parsed back by
    :func:`parse_pinned` when the baked workflow is dropped and queued."""
    return {
        "format": PIN_FORMAT,
        "entries": [{"name": e["name"], "text": e["text"]} for e in entries],
        "source": {"file": file, "token": token, "captured": captured},
    }


def parse_drafts(raw: Any) -> dict[str, str]:
    """The unsaved-edit overrides a ``drafts`` widget value holds (v0.86.0,
    owner ask 2026-08-28: "if you change a prompt that is selected and run
    it without saving, it should run the changed prompt") -- a JSON object
    mapping entry name -> unsaved text.

    ``""`` / a non-string / unparseable JSON / anything that isn't a JSON
    object degrades to ``{}`` (no drafts) with a logged warning -- same
    degrade-not-raise philosophy as :func:`parse_pinned`, since this is a
    scratch buffer the panel maintains, not a contract the queue can
    enforce. A malformed INDIVIDUAL entry (a non-string key or a non-string
    value) is dropped on its own with its own warning; the rest of an
    otherwise-valid object still applies -- one bad key must not blank out
    every other unsaved edit in a multi-select run.
    """
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        logger.warning(
            "EPS Prompt Notebook: drafts value is not JSON (%r); ignoring unsaved edits",
            raw[:80],
        )
        return {}
    if not isinstance(data, dict):
        logger.warning(
            "EPS Prompt Notebook: drafts value is not a JSON object (%r); ignoring unsaved edits",
            type(data).__name__,
        )
        return {}
    out: dict[str, str] = {}
    for name, text in data.items():
        if not isinstance(name, str) or not isinstance(text, str):
            logger.warning(
                "EPS Prompt Notebook: drafts entry %r is malformed; ignoring it", name
            )
            continue
        out[name] = text
    return out


def _selected_names(entry: str) -> list[str]:
    """FORMAT.md §6.1: ``entry`` holds one selected name per line, in
    selection order; blank/whitespace-only lines are skipped (a single
    name is just the degenerate one-line case)."""
    return [line.strip() for line in entry.split("\n") if line.strip()]


def _peek_resolved_path(context: LibraryContext, file_value: str) -> Path:
    """Best-effort mirror of :meth:`LibraryContext.resolve_notebook_file`
    that never creates anything.

    Used only to still name a path when the REAL resolution just raised
    (FORMAT.md §5/§6.1, owner report 2026-07-19): for a relative ``file``,
    ``resolve_notebook_file`` resolves against ``library_dir()``, which
    unconditionally ``mkdir``s — an unreachable configured folder (an
    unmounted NAS, a wrong-OS path) makes that raise an ``OSError`` before
    a path is ever produced, which would otherwise leak a raw, cryptic
    stack instead of a node error naming the file. This resolves the same
    way but reads the configured directory via ``load_config`` (a plain
    read, no filesystem write) instead of calling ``library_dir()``.
    Absolute ``file`` values need no such peek — they already pass through
    untouched, so this is only ever consulted for the relative branch.

    Mirrors :meth:`LibraryContext.resolve_notebook_file`'s 2026-08-28
    foreign-absolute healing too: a value that's absolute for the OTHER
    platform (``is_foreign_absolute``) is neither genuinely relative nor
    locally absolute, so without this it would fall straight into the
    plain ``base / path`` join below and reproduce this bug's own
    ``[Errno 22]`` shape even while merely PEEKING a path for an error
    message. Checked before the local ``is_absolute()`` branch, same
    reasoning as the real method's own docstring: identical real-world
    result, but keeps this peek exercisable under the injectable
    ``_IS_WINDOWS`` seam in tests.
    """
    value = (file_value or "").strip() or DEFAULT_NOTEBOOK_FILENAME
    configured = context.load_config().get("library_dir")
    base = Path(configured) if configured else context.default_library_dir
    if is_foreign_absolute(value):
        return heal_foreign_absolute(value, base)
    path = Path(value)
    if path.is_absolute():
        return path
    return base / path


def _unreachable_library_dir_hint(resolved: Path) -> str:
    """FORMAT.md §6.1/§7.3 hint suffix for a missing-file error: when
    *resolved*'s parent (the library folder) isn't there, name the likely
    cause and point at Settings rather than leaving a bare "does not
    exist" that reads as a typo instead of a NAS/host-machine problem."""
    try:
        parent_reachable = resolved.parent.is_dir()
    except OSError:
        parent_reachable = False
    if parent_reachable:
        return ""
    return (
        " — the library folder isn't reachable from the server machine; "
        "see EPSNodes settings"
    )


def resolve_selection(
    context: LibraryContext, file: str, entry: str, drafts: str = "{}"
) -> tuple[list[str], list[str]]:
    """The LIVE path: resolve *file*, parse it, and return the selected
    entries' ``(texts, names)`` in selection order -- exactly what
    ``read_entry`` returns when nothing is pinned. Missing file, empty
    selection, or ANY missing selected entry raises ``ValueError`` naming
    it (FORMAT.md §6.1's loud queue-time failure). Shared with EPS Save
    Image's pin capture (§6.14), which resolves the same file/selection
    through this very function at save time and skips the node on any
    raise -- so a pin can never name an entry the run itself would not
    have read.

    *drafts* (v0.86.0, :func:`parse_drafts`) overrides each resolved
    entry's TEXT with the panel's unsaved edit, when one names that entry
    -- position (the v0.85.0 FILE-order sort below) is never affected, and
    a draft naming an entry that didn't resolve here (unselected, or not
    found at all) is silently ignored. This is what makes §6.14's pin
    capture -- which calls this exact function -- record the draft text
    the run actually used, not the stale on-disk text, whenever the
    caller passes the widget's current ``drafts`` value through."""
    try:
        path = context.resolve_notebook_file(file)
    except OSError:
        # FORMAT.md §5/§6.1: a configured library_dir the server
        # machine can't reach makes the real resolve's `mkdir` raise
        # before it ever returns a path (owner report 2026-07-19) —
        # peek the same resolution without creating anything so the
        # error below can still name it, instead of leaking a raw
        # OSError.
        path = _peek_resolved_path(context, file)

    parsed, mtime, _line_ending = _load_notebook_cached(path)
    if mtime is None:
        hint = _unreachable_library_dir_hint(path)
        # 2026-08-28: a foreign-absolute `file` that healed to no existing
        # tail names WHAT WAS TRIED instead of the generic "resolved: ..."
        # -- `path` here IS the longest-tail candidate `heal_foreign_
        # absolute` fell back to, so this costs nothing extra to compute.
        detail = (
            foreign_absolute_note(path) if is_foreign_absolute(file) else f"resolved: {path}"
        )
        raise ValueError(f"EPS Prompt Notebook: file {file!r} does not exist ({detail}){hint}")

    names = _selected_names(entry)
    if not names:
        raise ValueError(
            f"EPS Prompt Notebook: no entry selected in {file!r} (resolved: {path})"
        )

    texts: list[str] = []
    result_names: list[str] = []
    missing: list[str] = []
    for selected in names:
        found = markdown_store.get_entry(parsed, selected)
        if found is None:
            missing.append(selected)
        else:
            texts.append(found["text"])
            result_names.append(found["name"])

    if missing:
        raise ValueError(
            f"EPS Prompt Notebook: no entry named {missing!r} in {file!r} (resolved: {path})"
        )

    # v0.85.0 (owner report 2026-08-28: "sometimes the order of images
    # doesn't match the order of list"): emit in FILE order, not the order
    # the user happened to click. `entry` records selection order -- and
    # the panel's own gestures disagree with each other about what that
    # means (ctrl+click APPENDS, shift+click takes a list-ordered slice,
    # toggling an entry off and back on moves it to the end), so a §6.10
    # run token's `t{N}` used to depend on click history rather than on
    # anything visible in the list. Sorting here (not only in the panel)
    # is what fixes ALREADY-SAVED workflows and hand-built /prompt callers
    # too: the widget keeps whatever it holds, the OUTPUT is canonical.
    # Stable sort, so a duplicated name keeps its relative order, and
    # unknown names cannot reach here (the missing check above raises).
    order = {
        item["name"]: index
        for index, item in enumerate(markdown_store.list_entries(parsed))
    }
    ranked = sorted(
        zip(result_names, texts, strict=True),
        key=lambda pair: order.get(pair[0], len(order)),
    )
    result_names = [name for name, _text in ranked]
    texts = [text for _name, text in ranked]

    # v0.86.0: apply unsaved-edit overrides LAST, after the file-order sort
    # -- a draft changes TEXT only, never the position `entry`/the file
    # order already settled above. Names not in `draft_map` (the common
    # case: no draft, or a draft for some OTHER selected name) pass through
    # via `.get(name, text)` unchanged.
    draft_map = parse_drafts(drafts)
    if draft_map:
        texts = [
            draft_map.get(name, text)
            for name, text in zip(result_names, texts, strict=True)
        ]

    return (texts, result_names)


class LoraLibraryNotebook:
    """Reads one or more FORMAT.md §3 notebook entries' text + name.

    Re-reads and re-parses the file on every execution (§6: "the file is
    the truth; the UI is a view") — the two-pane editor (§7.2) is a DOM
    widget that never serializes into the workflow; only the ``file``/
    ``entry`` STRING widgets do, so this is the only place that ever turns
    those two strings into text. A missing file, an empty selection, or ANY
    missing selected entry is a loud node error naming the file and every
    missing entry (queue-time failure, per §6.1) rather than a partial or
    empty-string passthrough — unlike ``LoraLibraryApplySet``'s "missing
    set ⇒ silent passthrough" (FORMAT.md §4/§6.2), a notebook entry's text
    IS this node's entire output.

    Multi-select (§6.1): ``entry`` is one name per line, in selection
    order. Outputs are ``("text","name")``, both declared
    ``OUTPUT_IS_LIST``, with element *i* = the i-th selected entry's §3.3
    text and heading name — a single-line ``entry`` is the degenerate
    one-element case, so every pre-multiselect workflow is unchanged.

    Confirmed against a running ComfyUI's ``execution.py``
    (``_async_map_node_over_list`` / ``merge_result_data``): this node sets
    no ``INPUT_IS_LIST`` and its widgets are scalar, so ``read_entry``
    itself still runs exactly ONCE per queued execution — all the fan-out
    described below is a downstream effect, not a re-invocation of this
    node. ``merge_result_data`` sees ``OUTPUT_IS_LIST[i] is True`` and
    ``extend()``s our one result's per-output lists straight into the
    node's output-slot lists (length = selection count, no wrapping). Any
    ordinary (non-``INPUT_IS_LIST``) downstream node then computes its own
    ``max_len_input`` from those list lengths and calls itself once per
    index via ``slice_dict`` (which repeats the *last* element for any
    shorter co-input rather than erroring, so ``text``/``name`` — always
    equal length here — stay correctly paired at every index) — i.e. one
    queued run fans out into one execution per selected entry downstream,
    each with its matching (text, name) pair. That is exactly FORMAT.md
    §6.1's "one queued run = one generation per selected prompt". A
    single-line ``entry`` yields length-1 lists, so a plain single-
    selection wiring still executes exactly once downstream too.
    """

    CATEGORY = "EPSNodes"
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("text", "name")
    OUTPUT_IS_LIST = (True, True)
    OUTPUT_TOOLTIPS = (
        "The selected entry's prompt text. With more than one entry selected, "
        "this is a list of strings, one per entry, in selection order.",
        "The selected entry's heading (its name in the list) -- handy as a "
        "filename prefix or caption. Paired index-for-index with text.",
    )
    FUNCTION = "read_entry"
    DESCRIPTION = (
        "A library of named text prompts, edited right on the node: a "
        "scrolling list on the left, a text editor on the right, saved to a "
        "plain Markdown file you control, locally or on a shared drive. "
        "Select one entry to output its text as a single string, or select "
        "several with Ctrl/Cmd-click and Shift-click to run the rest of the "
        "workflow once per prompt, in selection order. Organize entries "
        "under category headings created from the same list. The file is "
        "re-read on every run, so edits made outside ComfyUI are picked up "
        "automatically."
    )

    #: §6.16 state registry (v0.83.0): the widgets a Universal State
    #: Controller may capture/apply, declared next to the parser that owns
    #: their shape. ``pinned`` is excluded -- it is baked provenance from
    #: EPS Save Image (§6.14), not something a user picks in the panel.
    #: ``drafts`` (v0.86.0) is excluded for the same reason as `pinned`:
    #: it's mid-edit scratch text the panel maintains, never something a
    #: state save/apply should carry.
    EPS_STATE_WIDGETS: ClassVar[dict[str, Any]] = {
        "format": 1,
        "widgets": {
            "file": {"kind": "string", "max_len": 10000},
            "entry": {"kind": "lines"},
        },
        "excluded": {
            PIN_WIDGET: "provenance from a baked image, not user intent",
            DRAFTS_WIDGET: "unsaved mid-edit scratch text, not user-chosen state",
        },
    }

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                # "hidden": True is the VUE-nodes ("New node design") hide
                # flag (2026-07-29): that renderer decides widget visibility
                # from the input spec's options (`options.hidden`,
                # useProcessedWidgets.ts) and IGNORES the litegraph
                # `widget.hidden` the frontend sets -- without this, this
                # internal widget leaked into Vue nodes as a raw editable
                # field. The classic canvas renderer ignores this key right
                # back, so it changes nothing there.
                "file": ("STRING", {"default": "loras.md", "hidden": True}),
                "entry": (
                    "STRING",
                    {
                        "default": "",
                        # Vue-nodes hide flag, same as `file` above (the panel
                        # owns selection; this widget is serialization
                        # plumbing that leaked into Vue nodes as a raw
                        # editable field). Canvas ignores this key.
                        "hidden": True,
                        "tooltip": (
                            "The selected entry name(s), one per line, in "
                            "selection order. This is normally filled in "
                            "automatically as you click entries in the list; "
                            "you don't need to type into it directly."
                        ),
                    },
                ),
            },
            # Provenance M3 (v0.71.0, FORMAT.md §6.1/§6.14/§8): `pinned` is
            # TAIL-APPENDED -- the only §8-safe place for a new widget
            # (widgets_values restores positionally). `optional` with
            # default "" so every saved workflow and hand-built /prompt that
            # predates it reads LIVE, exactly as before. Same Vue-nodes
            # hide flag as `file`/`entry` (the frontend sets the canvas
            # `widget.hidden`; this key covers the Vue renderer).
            "optional": {
                PIN_WIDGET: (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "hidden": True,
                        "tooltip": (
                            "Filled in automatically when you drop an EPS "
                            "Save Image file onto the canvas: the entry text "
                            "captured when that image was made, so the run "
                            "recreates exactly even if the notebook changed "
                            "since. Empty = read the live notebook. Use the "
                            "node's unpin control to clear it."
                        ),
                    },
                ),
                # Unsaved-edit drafts (v0.86.0, FORMAT.md §6.1/§8, owner ask
                # 2026-08-28): TAIL-APPENDED after `pinned` -- §8 again, this
                # is the only new-widget-safe spot. `optional` with default
                # "{}" so every saved workflow/hand-built /prompt that
                # predates it parses to "no drafts" (parse_drafts) and reads
                # exactly as before. Same both-ways hide flag as `pinned`.
                DRAFTS_WIDGET: (
                    "STRING",
                    {
                        "default": "{}",
                        "multiline": False,
                        "hidden": True,
                        "tooltip": (
                            "Unsaved edits from the panel, applied on top of "
                            "the live file for whatever is selected -- lets "
                            "you audition a changed prompt without saving it "
                            "first. Maintained automatically by the panel; "
                            "you don't need to touch this directly."
                        ),
                    },
                ),
            },
        }

    @classmethod
    def VALIDATE_INPUTS(cls, **_kwargs: Any) -> bool:
        # Entry names are dynamic — created/renamed/deleted/reordered by
        # hand-editing the file or via the widget at any time (FORMAT.md
        # §7.2) — so there is no fixed set of values ComfyUI could check
        # `entry`'s lines against. This is a genuine no-op, not a stand-in
        # for real validation: a bad `entry`/`file` is instead a loud error
        # from read_entry itself, right where FORMAT.md §6.1 wants it (at
        # queue/execution time).
        return True

    @classmethod
    def IS_CHANGED(cls, file: str, entry: str, pinned: str = "", drafts: str = "{}") -> str:
        # v0.80.0: content-derived, not whole-file mtime -- see
        # _selection_token. The pin/entry/file/drafts WIDGET values are
        # already in core's input-hash key, so a selection, pin, or draft
        # change re-executes regardless of this token; this only has to
        # track what the file's (or a draft's) CONTENT contributes to the
        # output.
        return _selection_token(_context, file, entry, pinned, drafts)

    def read_entry(
        self, file: str, entry: str, pinned: str = "", drafts: str = "{}"
    ) -> tuple[list[str], list[str]]:
        # Provenance M3 (FORMAT.md §6.1): a pin wins outright -- the pinned
        # entries' text/name lists come back IN PIN ORDER and the file is
        # never opened (it may have been edited, renamed or deleted since
        # the image was saved; that is the whole point). A malformed pin
        # already warned inside parse_pinned and reads live below. Drafts
        # are never consulted here either -- a pin already IS the frozen
        # text a run used (v0.86.0).
        pinned_entries = parse_pinned(pinned)
        if pinned_entries is not None:
            return (
                [item["text"] for item in pinned_entries],
                [item["name"] for item in pinned_entries],
            )

        context = _context
        if context is None:
            raise RuntimeError("EPSNodes: EPS Prompt Notebook has no context configured")
        return resolve_selection(context, file, entry, drafts)
