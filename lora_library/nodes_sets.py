"""The ``LoraLibraryApplySet`` ComfyUI node (FORMAT.md §6.2, display: "EPS Apply LoRA Set").

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
        logger.exception("EPSNodes: could not list sets for the Apply Set combo")
        return ["None"]
    return ["None", *slugs]


def _format_strength(value: float) -> str:
    """Compact strength for §6.2 tags: ``0.8`` not ``0.8000``, ``1`` not ``1.0``."""
    return f"{value:g}"


def _loras_text(stack: list[tuple[str, float, float]]) -> str:
    """FORMAT.md §6.2 ``loras_text``: normalized ``stem_strength`` tokens.

    Owner format (2026-07-18c) — filename/caption-friendly, no ``<>``/``:``
    punctuation: ``MYLORA_HIGH_1``, ``detailer_0.8`` (dual strengths append
    both: ``detailer_0.8_0.4``), space-joined. ``stem`` = basename without
    extension, tolerant of either path separator (the stack may carry this
    machine's spelling of a set written on the other OS, FORMAT.md §4).
    """
    tokens = []
    for file, strength_model, strength_clip in stack:
        stem = file.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
        token = f"{stem}_{_format_strength(strength_model)}"
        if strength_clip != strength_model:
            token += f"_{_format_strength(strength_clip)}"
        tokens.append(token)
    return " ".join(tokens)


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

    A §4.1 COMPOSITE (format-2) set holds one row-list per loader
    (``loaders[i].loras``); ``loader_slot`` (default 0) picks which one this
    node applies, via :func:`sets_store.loras_for_slot`. A plain format-1 set
    ignores ``loader_slot`` and always applies its single ``loras`` — so two
    ``EPS Apply LoRA Set`` nodes pointed at the SAME composite state but
    different ``loader_slot`` values apply different loras and report
    different ``loras_text`` (the fix for the owner's "both Apply nodes show
    the same loras_text" report).
    """

    CATEGORY = "EPSNodes/LoRA"
    RETURN_TYPES = ("MODEL", "CLIP", "LORA_STACK", "STRING", "STRING")
    RETURN_NAMES = ("model", "clip", "lora_stack", "trigger_words", "loras_text")
    OUTPUT_TOOLTIPS = (
        "The input model, patched with this set's enabled loras -- or "
        "passed through unchanged if model isn't wired, or the set is "
        "empty/None.",
        "The input CLIP, patched with this set's enabled loras -- or passed "
        "through unchanged under the same conditions as model.",
        "This set's enabled loras as a LORA_STACK, ready to wire into EPS "
        "LoRA Iterator or another stack-consuming node.",
        "The trigger words saved with this set, as one string.",
        "A compact, filename-safe summary of the applied loras and "
        "strengths, e.g. 'detailer_0.8 style_1' -- handy for Save Image's "
        "filename_prefix.",
    )
    FUNCTION = "apply"
    DESCRIPTION = (
        "Applies a saved LoRA configuration -- which loras, in what order, "
        "on or off, at what strengths -- to a model and CLIP in one step. "
        "Pick a set from the dropdown; with neither model nor clip wired, "
        "the node instead acts as a pure LORA_STACK and trigger-word source "
        "you can feed into other nodes. Re-reads the saved set file on "
        "every run, so edits made in the set editor apply the next time you "
        "queue. A missing or empty set passes model/clip through unchanged "
        "rather than failing the run."
    )

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "set": (
                    _slug_options(),
                    {
                        "default": "None",
                        "tooltip": (
                            "Which saved LoRA configuration to apply. "
                            "Choose None to pass model/clip through "
                            "untouched and output an empty stack."
                        ),
                    },
                ),
            },
            "optional": {
                # FORMAT.md §6.2 (2026-07-20 amendment): optional, not
                # required — owner ask: strength_scale is an edge-case
                # override the frontend hides by default (sets.js), and a
                # hand-built /prompt that omits it must get the apply()/
                # IS_CHANGED() default (1.0 = clean pass-through of the
                # set's own stored strengths) instead of a "required input
                # missing" rejection.
                "strength_scale": (
                    "FLOAT",
                    {
                        "default": 1.0,
                        "min": 0.0,
                        "max": 2.0,
                        "step": 0.05,
                        "tooltip": (
                            "Multiplies every lora's strength in this set by "
                            "this factor. 1.0 applies the set's saved "
                            "strengths unchanged, 0.5 halves them all, 0 "
                            "disables them without editing the set itself. "
                            "Hidden by default -- enable it from this "
                            "node's Properties panel ('Show strength "
                            "scale')."
                        ),
                    },
                ),
                # FORMAT.md §6.2/§4.1 (2026-07-20 composite fix): which
                # loader's slice of a §4.1 COMPOSITE (format-2) state to
                # apply — 0 = the first loader (and the whole config for a
                # plain format-1 state, where this is ignored). Same
                # optional/default-kwarg rationale as strength_scale above:
                # HIDDEN by default behind a `Show loader slot` node
                # property (sets.js), so most single-loader users never see
                # it, and a hand-built /prompt that omits it must still
                # queue and get slot 0 rather than a rejection.
                "loader_slot": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 63,
                        "step": 1,
                        "tooltip": (
                            "For a set saved from multiple loaders: which "
                            "loader's row of loras to apply here (0 = the "
                            "first). Ignored by a set saved from a single "
                            "loader. Hidden by default -- enable it from "
                            "this node's Properties panel ('Show loader "
                            "slot')."
                        ),
                    },
                ),
                "model": (
                    "MODEL",
                    {
                        "tooltip": (
                            "The model to patch with this set's loras. "
                            "Leave unwired to use this node purely as a "
                            "LORA_STACK / trigger-word source."
                        ),
                    },
                ),
                "clip": (
                    "CLIP",
                    {
                        "tooltip": (
                            "The CLIP to patch with this set's loras. Leave "
                            "unwired along with model to use this node "
                            "purely as a LORA_STACK / trigger-word source."
                        ),
                    },
                ),
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
        strength_scale: float = 1.0,
        loader_slot: int = 0,
        model: Any = None,
        clip: Any = None,
    ) -> str:
        # loader_slot is included so switching slots on the SAME composite
        # state (same file, same mtime/size) still re-executes — the file
        # token alone can't see that (FORMAT.md §6.2/§4.1).
        return f"{set}:{_set_file_token(_context, set)}:{strength_scale}:{loader_slot}"

    def apply(
        self,
        set: str,
        strength_scale: float = 1.0,
        loader_slot: int = 0,
        model: Any = None,
        clip: Any = None,
    ) -> tuple[Any, Any, list[tuple[str, float, float]], str, str]:
        if set in ("None", ""):
            return model, clip, [], "", ""

        context = _context
        if context is None:
            logger.warning(
                "EPSNodes: EPS Apply LoRA Set has no context configured; passthrough"
            )
            return model, clip, [], "", ""

        try:
            set_data = sets_store.load_set(context, set)
        except sets_store.SetValidationError as exc:
            logger.warning("EPSNodes: set %r could not be loaded (%s); passthrough", set, exc)
            return model, clip, [], "", ""
        if set_data is None:
            logger.warning("EPSNodes: set %r has no file on disk; passthrough", set)
            return model, clip, [], "", ""

        # FORMAT.md §4.1/§6.2: picks the loader_slot-th loader's rows for a
        # composite (format-2) state; a plain format-1 state ignores
        # loader_slot entirely and returns its single `loras` unchanged —
        # this is the exact fix for "two EPS Apply LoRA Set nodes on the same
        # composite state show identical loras_text" (they now select
        # different slices below, so the loras_text built from them differs
        # too).
        stack: list[tuple[str, float, float]] = []
        for row in sets_store.loras_for_slot(set_data, loader_slot):
            if not row["on"]:
                continue
            resolved = sets_store.resolve_lora(context, row["file"])
            if resolved is None:
                logger.warning(
                    "EPSNodes: lora %r in set %r could not be resolved; skipping",
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

        return model, clip, stack, set_data["trigger_words"], _loras_text(stack)

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
                    "EPSNodes: lora %r resolved by name but has no on-disk path; skipping",
                    file,
                )
                continue
            lora_sd = comfy.utils.load_torch_file(path, safe_load=True)
            model, clip = comfy.sd.load_lora_for_models(
                model, clip, lora_sd, strength_model, strength_clip
            )
        return model, clip
