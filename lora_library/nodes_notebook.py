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


class LoraLibraryNotebook:
    """Reads one FORMAT.md §3 notebook entry's text.

    Re-reads and re-parses the file on every execution (§6: "the file is
    the truth; the UI is a view") — the two-pane editor (§7.2) is a DOM
    widget that never serializes into the workflow; only the ``file``/
    ``entry`` STRING widgets do, so this is the only place that ever turns
    those two strings into text. A missing file or missing entry is a loud
    node error naming which one failed (queue-time failure, per §6.1) rather
    than an empty-string passthrough — unlike ``LoraLibraryApplySet``'s
    "missing set ⇒ silent passthrough" (FORMAT.md §4/§6.2), a notebook
    entry's text IS this node's entire output, so silently returning ""
    would be indistinguishable from a genuinely empty entry.
    """

    CATEGORY = "EPSNodes"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
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
        # Entry names are dynamic — created/renamed/deleted by hand-editing
        # the file or via the widget at any time (FORMAT.md §7.2) — so there
        # is no fixed set of values ComfyUI could check `entry` against.
        # This is a genuine no-op, not a stand-in for real validation: a bad
        # `entry`/`file` is instead a loud error from read_entry itself,
        # right where FORMAT.md §6.1 wants it (at queue/execution time).
        return True

    @classmethod
    def IS_CHANGED(cls, file: str, entry: str) -> str:
        return _notebook_token(_context, file, entry)

    def read_entry(self, file: str, entry: str) -> tuple[str]:
        context = _context
        if context is None:
            raise RuntimeError("lora_library: LoRA Notebook has no context configured")

        path = context.resolve_notebook_file(file)
        parsed, mtime, _line_ending = markdown_store.load_notebook(path)
        if mtime is None:
            raise ValueError(
                f"LoRA Notebook: file {file!r} does not exist (resolved: {path})"
            )

        found = markdown_store.get_entry(parsed, entry)
        if found is None:
            raise ValueError(
                f"LoRA Notebook: no entry named {entry!r} in {file!r} (resolved: {path})"
            )

        return (found["text"],)
