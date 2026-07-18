"""The ``LoraLibraryApplySet`` ComfyUI node (FORMAT.md §6.2, display: "Apply
LoRA Set").

``comfy.utils``/``comfy.sd`` are imported only inside the one method that
touches actual model/clip weights, never at module level, so this module —
and everything about it that doesn't apply real weights — stays importable
in a plain test environment without either installed (same convention as
comfyui-photoshop-bridge's ``cpsb/nodes.py``; see its module docstring).
ComfyUI always provides both to the node's real runtime.
"""

from __future__ import annotations

import logging
from typing import Any

from . import sets_store
from .context import LibraryContext

logger = logging.getLogger("lora_library")

_context: LibraryContext | None = None


def set_context(context: LibraryContext | None) -> None:
    """Wire the shared :class:`LibraryContext` into this module.

    Called once from the pack's ``__init__.py`` (real runs); tests call it
    directly against a fake context. Accepts ``None`` so tests can reset the
    module-level global between cases without leaking state.
    """
    global _context
    _context = context


def _slug_options() -> list[str]:
    """``["None"] + sorted slugs`` for the ``set`` COMBO (FORMAT.md §6.2).

    Runs at ``INPUT_TYPES()`` time, which ComfyUI-adjacent tooling can call
    before :func:`set_context` — e.g. during node-list probing with no live
    server — so a missing context or a broken sets directory degrades to
    ``["None"]`` instead of raising.
    """
    if _context is None:
        return ["None"]
    try:
        slugs = sorted(row["slug"] for row in sets_store.list_sets(_context))
    except Exception:  # broad: node registration must not crash on this
        logger.exception("lora_library: could not list sets for the Apply Set combo")
        return ["None"]
    return ["None", *slugs]


def _set_file_token(context: LibraryContext | None, slug: str) -> str:
    """The set file's mtime+size as a cache-busting token, or a missing-marker."""
    if context is None or slug in ("None", ""):
        return "no-set"
    try:
        stat = sets_store.set_path(context, slug).stat()
    except OSError:
        return "missing"
    return f"{stat.st_mtime}:{stat.st_size}"


class LoraLibraryApplySet:
    """Applies a saved FORMAT.md §4 LoRA set to ``model``/``clip``.

    Re-reads the set file on every execution (§6: "the file is the truth;
    the UI is a view"). With neither ``model`` nor ``clip`` wired, this node
    is a pure ``LORA_STACK``/``trigger_words`` source (efficiency-nodes-
    compatible) — no ``comfy.*`` import is even attempted in that mode.
    ``"None"``, a set with no on-disk file, or a set that fails to parse all
    behave the same way: a logged warning (except plain ``"None"``, which is
    the expected idle state) and a passthrough of ``model``/``clip`` with an
    empty stack and empty trigger words — a set that briefly doesn't resolve
    must not fail the whole prompt (same posture as an individual missing
    lora, FORMAT.md §4).
    """

    CATEGORY = "EPSNodes"
    RETURN_TYPES = ("MODEL", "CLIP", "LORA_STACK", "STRING")
    RETURN_NAMES = ("model", "clip", "lora_stack", "trigger_words")
    FUNCTION = "apply"

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "set": (_slug_options(), {"default": "None"}),
                "strength_scale": (
                    "FLOAT",
                    {"default": 1.0, "min": 0.0, "max": 2.0, "step": 0.05},
                ),
            },
            "optional": {
                "model": ("MODEL",),
                "clip": ("CLIP",),
            },
        }

    @classmethod
    def VALIDATE_INPUTS(cls, **_kwargs: Any) -> bool:
        # The combo is dynamic (sets are created/renamed/deleted at runtime,
        # FORMAT.md §7.4) — ComfyUI's own combo-membership check would reject
        # a just-created set the widget cache hasn't refreshed yet.
        return True

    @classmethod
    def IS_CHANGED(
        cls,
        set: str,
        strength_scale: float,
        model: Any = None,
        clip: Any = None,
    ) -> str:
        return f"{set}:{_set_file_token(_context, set)}:{strength_scale}"

    def apply(
        self,
        set: str,
        strength_scale: float,
        model: Any = None,
        clip: Any = None,
    ) -> tuple[Any, Any, list[tuple[str, float, float]], str]:
        if set in ("None", ""):
            return model, clip, [], ""

        context = _context
        if context is None:
            logger.warning("lora_library: Apply LoRA Set has no context configured; passthrough")
            return model, clip, [], ""

        try:
            set_data = sets_store.load_set(context, set)
        except sets_store.SetValidationError as exc:
            logger.warning("lora_library: set %r could not be loaded (%s); passthrough", set, exc)
            return model, clip, [], ""
        if set_data is None:
            logger.warning("lora_library: set %r has no file on disk; passthrough", set)
            return model, clip, [], ""

        stack: list[tuple[str, float, float]] = []
        for row in set_data["loras"]:
            if not row["on"]:
                continue
            resolved = sets_store.resolve_lora(context, row["file"])
            if resolved is None:
                logger.warning(
                    "lora_library: lora %r in set %r could not be resolved; skipping",
                    row["file"],
                    set,
                )
                continue
            strength_model = row["strength"] * strength_scale
            base_clip_strength = (
                row["strength"] if row["strength_clip"] is None else row["strength_clip"]
            )
            stack.append((resolved, strength_model, base_clip_strength * strength_scale))

        if model is not None or clip is not None:
            model, clip = self._apply_stack(context, model, clip, stack)

        return model, clip, stack, set_data["trigger_words"]

    @staticmethod
    def _apply_stack(
        context: LibraryContext,
        model: Any,
        clip: Any,
        stack: list[tuple[str, float, float]],
    ) -> tuple[Any, Any]:
        """Patch *model*/*clip* with every stack row, in order.

        Mirrors core's ``LoraLoader.load_lora`` exactly (verified against
        ComfyUI's ``nodes.py``): lazy ``comfy.utils``/``comfy.sd`` imports,
        ``load_torch_file(path, safe_load=True)``, then
        ``load_lora_for_models`` — which itself already handles a ``None``
        model or clip (only patches the side that's actually wired). A
        strength-0/0 row is skipped before even loading the file, same as
        core.
        """
        if not stack:
            return model, clip

        import comfy.sd
        import comfy.utils

        for file, strength_model, strength_clip in stack:
            if strength_model == 0 and strength_clip == 0:
                continue
            path = context.resolve_lora_path(file)
            if path is None:
                logger.warning(
                    "lora_library: lora %r resolved by name but has no on-disk path; skipping",
                    file,
                )
                continue
            lora_sd = comfy.utils.load_torch_file(path, safe_load=True)
            model, clip = comfy.sd.load_lora_for_models(
                model, clip, lora_sd, strength_model, strength_clip
            )
        return model, clip
