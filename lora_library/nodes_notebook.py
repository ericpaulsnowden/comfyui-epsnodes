"""The ``LoraLibraryNotebook`` ComfyUI node (FORMAT.md §6.1, display: "LoRA
Notebook").

Unlike ``nodes_sets.py``, this node never touches model/clip weights, so
there's no lazy ``comfy.*`` import seam to mirror — no ComfyUI import
appears anywhere in this module, and it is importable in a plain test
environment as-is.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from . import markdown_store
from .context import LibraryContext

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


def _selected_names(entry: str) -> list[str]:
    """FORMAT.md §6.1: ``entry`` holds one selected name per line, in
    selection order; blank/whitespace-only lines are skipped (a single
    name is just the degenerate one-line case)."""
    return [line.strip() for line in entry.split("\n") if line.strip()]


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
    FUNCTION = "read_entry"

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "file": ("STRING", {"default": "loras.md"}),
                "entry": ("STRING", {"default": ""}),
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
    def IS_CHANGED(cls, file: str, entry: str) -> str:
        return _notebook_token(_context, file, entry)

    def read_entry(self, file: str, entry: str) -> tuple[list[str], list[str]]:
        context = _context
        if context is None:
            raise RuntimeError("lora_library: LoRA Notebook has no context configured")

        path = context.resolve_notebook_file(file)
        parsed, mtime, _line_ending = markdown_store.load_notebook(path)
        if mtime is None:
            raise ValueError(
                f"LoRA Notebook: file {file!r} does not exist (resolved: {path})"
            )

        names = _selected_names(entry)
        if not names:
            raise ValueError(
                f"LoRA Notebook: no entry selected in {file!r} (resolved: {path})"
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
                f"LoRA Notebook: no entry named {missing!r} in {file!r} (resolved: {path})"
            )

        return (texts, result_names)
