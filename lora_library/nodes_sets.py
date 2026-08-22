"""The ``LoraLibraryApplySet`` ComfyUI node (FORMAT.md §6.2, display: "EPS Apply LoRA Set").

``comfy.utils``/``comfy.sd`` are imported only inside the one method that
touches actual model/clip weights, never at module level, so this module —
and everything about it that doesn't apply real weights — stays importable
in a plain test environment without either installed (same convention as
comfyui-photoshop-bridge's ``cpsb/nodes.py``; see its module docstring).
ComfyUI always provides both to the node's real runtime.

**Provenance M3 (v0.71.0, FORMAT.md §6.2 ``pinned_state``):** a
TAIL-appended, hidden ``pinned_state`` STRING widget. Empty = live (the
set file is re-read on every run, today's behavior). Non-empty = the pin
JSON EPS Save Image (§6.14) captured when an image was made -- ``{"format":
1, "slug", "name", "set": <the normalized §4/§4.1 set dict exactly as
``sets_store.load_set`` returns it>, "source": {"token", "captured"}}`` --
and ``apply`` runs THAT set dict through the same normalize/apply path as
a loaded file (``loader_slot`` semantics unchanged) without reading the
sets folder. A malformed pin logs a warning and falls back to live; the
frontend shows the pinned rows on the node with a one-click unpin.
"""

from __future__ import annotations

import copy
import json
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


#: FORMAT.md §6.2 ``pinned_state`` widget: the pin JSON ``format`` this
#: build writes (EPS Save Image, §6.14) and reads.
PIN_FORMAT = 1

#: The widget's name -- EPS Save Image bakes it into the workflow/prompt
#: chunks by this name (FORMAT.md §6.14), the frontend reads it by this name.
PIN_WIDGET = "pinned_state"


def parse_pinned_state(raw: Any) -> dict[str, Any] | None:
    """The pin a ``pinned_state`` widget value carries, or ``None`` for LIVE.

    ``""`` / a non-string is live (no log -- the everyday state). Anything
    else must be the §6.2 pin JSON: an object whose ``set`` is a §4/§4.1
    set object -- it is run through :func:`sets_store.normalize_set`, the
    SAME validation a loaded file gets, so the returned ``"set"`` is
    exactly the shape ``load_set`` would have produced (format 1 rows or a
    format 2 composite). Anything malformed (not JSON, not an object, no
    ``set`` object, a set that fails validation) logs a warning and
    returns ``None`` so the node reads the live file instead of failing
    the queue. ``slug``/``name`` default to ``""``/the set's own name when
    absent; ``source`` passes through as-is (``{}`` when absent).
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        logger.warning(
            "EPS Apply LoRA Set: pinned_state is not JSON (%r); reading the live set",
            raw[:80],
        )
        return None
    if not isinstance(data, dict) or not isinstance(data.get("set"), dict):
        logger.warning(
            "EPS Apply LoRA Set: pinned_state carries no set object; reading the live set"
        )
        return None
    try:
        set_data = sets_store.normalize_set(data["set"])
    except sets_store.SetValidationError as exc:
        logger.warning(
            "EPS Apply LoRA Set: pinned_state set is invalid (%s); reading the live set", exc
        )
        return None
    slug = data.get("slug")
    name = data.get("name")
    source = data.get("source")
    return {
        "format": data.get("format", PIN_FORMAT),
        "slug": slug if isinstance(slug, str) else "",
        "name": name if isinstance(name, str) else set_data["name"],
        "set": set_data,
        "source": source if isinstance(source, dict) else {},
    }


def make_pin(
    slug: str, set_data: dict[str, Any], token: str | None, captured: str
) -> dict[str, Any]:
    """The §6.2 pin JSON (as a dict -- callers ``json.dumps`` it into the
    widget) for the normalized *set_data* (exactly as ``load_set`` returned
    it) behind *slug*, captured while saving run *token* at *captured*
    (ISO-8601 UTC). Built by EPS Save Image (§6.14) at save time; parsed
    back by :func:`parse_pinned_state` when the baked workflow is dropped
    and queued. The set dict is deep-copied so the pin can never alias a
    store's cached object."""
    return {
        "format": PIN_FORMAT,
        "slug": slug,
        "name": set_data.get("name", "") if isinstance(set_data, dict) else "",
        "set": copy.deepcopy(set_data),
        "source": {"token": token, "captured": captured},
    }


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
        "rather than failing the run. The EPS Lora Loader State "
        "Controller's Push State button can point this node (or every "
        "Apply Set at once) at a state; the node's 'mirrors loader' tag "
        "scopes which pushes it follows."
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
                # Provenance M3 (v0.71.0, FORMAT.md §6.2/§6.14/§8):
                # `pinned_state` is TAIL-APPENDED -- after every existing
                # widget (set, strength_scale, loader_slot), the only
                # §8-safe place (widgets_values restores positionally).
                # Optional with default "" so every saved workflow and
                # hand-built /prompt that predates it reads LIVE, exactly
                # as before. "hidden": True is the Vue-nodes hide flag
                # (the frontend sets the canvas `widget.hidden`).
                PIN_WIDGET: (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "hidden": True,
                        "tooltip": (
                            "Filled in automatically when you drop an EPS "
                            "Save Image file onto the canvas: the set's rows "
                            "captured when that image was made, so the run "
                            "recreates exactly even if the set was edited "
                            "since. Empty = read the live set file. Use the "
                            "node's unpin control to clear it."
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
        pinned_state: str = "",
    ) -> str:
        # loader_slot is included so switching slots on the SAME composite
        # state (same file, same mtime/size) still re-executes — the file
        # token alone can't see that (FORMAT.md §6.2/§4.1). The pin value
        # (M3) is folded in verbatim: setting, changing or clearing a pin
        # re-executes even when nothing else moved.
        return (
            f"{set}:{_set_file_token(_context, set)}:{strength_scale}:{loader_slot}"
            f":{pinned_state}"
        )

    def apply(
        self,
        set: str,
        strength_scale: float = 1.0,
        loader_slot: int = 0,
        model: Any = None,
        clip: Any = None,
        pinned_state: str = "",
    ) -> tuple[Any, Any, list[tuple[str, float, float]], str, str]:
        # Provenance M3 (FORMAT.md §6.2): a pin wins outright -- its set
        # dict (already normalized by parse_pinned_state, the same
        # validation a loaded file gets) goes through the identical
        # loader_slot/resolve/apply path below, and the sets folder is
        # never read (the file may have been edited or deleted since the
        # image was saved; that is the whole point). A malformed pin
        # already warned inside parse_pinned_state and reads live below.
        pin = parse_pinned_state(pinned_state)
        if pin is None and set in ("None", ""):
            return model, clip, [], "", ""

        context = _context
        if context is None:
            logger.warning(
                "EPSNodes: EPS Apply LoRA Set has no context configured; passthrough"
            )
            return model, clip, [], "", ""

        if pin is not None:
            set_data = pin["set"]
            # Warnings below name the set; a pin names the slug it was
            # captured from (falling back to the combo's own value).
            set = pin["slug"] or set
        else:
            try:
                set_data = sets_store.load_set(context, set)
            except sets_store.SetValidationError as exc:
                logger.warning(
                    "EPSNodes: set %r could not be loaded (%s); passthrough", set, exc
                )
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
        lora_cache: dict[str, Any] | None = None,
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
            # Audit 2026-08-21: an optional per-call cache -- the Iterator
            # applies the SAME files at every strength step (N loras x S
            # steps x N rows = N^2*S loads for a plan that needs N), so one
            # dict per sweep() call collapses that to one load per file.
            # Callers that pass nothing keep the one-shot behavior.
            lora_sd = lora_cache.get(path) if lora_cache is not None else None
            if lora_sd is None:
                lora_sd = comfy.utils.load_torch_file(path, safe_load=True)
                if lora_cache is not None:
                    lora_cache[path] = lora_sd
            model, clip = comfy.sd.load_lora_for_models(
                model, clip, lora_sd, strength_model, strength_clip
            )
        return model, clip
