"""The ``EPSLoraPicker`` ComfyUI node (FORMAT.md §6.13, display: "EPS LoRA
Picker") -- the backend half: execution + ``IS_CHANGED``. The panel
(browse/favorites/recents, §6.13 M2 "Send to loader", M3 search/preview/
reorder, M4 controller integration -- all shipped v0.54-0.64) lives in
``web/lora_library/picker.js``.

Deliberately NOT a loader rebuild (§6.13): this node emits the exact same
``(model, clip, lora_stack, trigger_words, loras_text)`` shape
``nodes_sets.LoraLibraryApplySet`` does, and composes with it rather than
reimplementing it -- ``_apply_stack``/``_loras_text`` are imported and
called, never copied, so a weight-patching or tag-formatting fix made in
``nodes_sets.py`` covers this node too. Same importable-without-ComfyUI
seam as every other module here: ``comfy.*`` only ever enters (lazily)
inside ``LoraLibraryApplySet._apply_stack`` itself, which this module calls
through but never touches directly.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from . import nodes_sets, sets_store
from .context import LibraryContext

logger = logging.getLogger("lora_library")

_context: LibraryContext | None = None

#: Sidecar text is capped per file (FORMAT.md §6.13) -- one runaway .txt
#: must not blow out `trigger_words` for the whole selection.
_SIDECAR_CAP = 4096


def set_context(context: LibraryContext | None) -> None:
    """Wire the shared :class:`LibraryContext` into this module.

    Called once from the pack's ``__init__.py`` (real runs); tests call it
    directly against a fake context. Accepts ``None`` so tests can reset the
    module-level global between cases without leaking state.
    """
    global _context
    _context = context


def _coerce_strength(value: object) -> float | None:
    """*value* as ``float``, or ``None`` when it can't be coerced.

    ``bool`` is rejected explicitly before the ``float()`` attempt (FORMAT.md
    §6.13: "bool is not a number") -- Python's bool-is-an-int would otherwise
    let a stray ``"on": true`` sitting in a strength field silently become
    ``1.0`` instead of degrading the row.
    """
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_selection(selection: str) -> list[dict[str, Any]]:
    """Parse the ``selection`` widget value into a list of lora rows.

    FORMAT.md §6.13: ``selection`` is a JSON object with a ``scope`` string
    (per-workflow view state, deliberately not returned here -- execution
    ignores it and it is excluded from :meth:`EPSLoraPicker.IS_CHANGED`) and
    a ``loras`` list of ``{"file", "on", "strength", "strength_clip"?}``
    rows, unique by ``file``.

    NEVER RAISES: unparseable JSON, a non-object value, a missing/non-list
    ``loras``, a non-object row, a row missing ``file``, or a row whose
    ``strength``/``strength_clip`` can't coerce to a number all degrade --
    the whole value to ``[]``, or just that one row -- each with a logged
    warning naming the problem, never an exception. A queue must never fail
    on view state (§6.13). Rows are deduplicated by ``file``, first
    (successfully parsed) occurrence wins -- a duplicate that failed to
    parse doesn't block a later, valid row for the same file from being
    kept.
    """
    if not selection:
        return []
    try:
        parsed = json.loads(selection)
    except (TypeError, ValueError) as exc:
        logger.warning(
            "EPSNodes: EPS LoRA Picker `selection` is not valid JSON (%s); "
            "treating as empty",
            exc,
        )
        return []
    if not isinstance(parsed, dict):
        logger.warning(
            "EPSNodes: EPS LoRA Picker `selection` is not a JSON object "
            "(got %s); treating as empty",
            type(parsed).__name__,
        )
        return []
    loras_raw = parsed.get("loras")
    if not isinstance(loras_raw, list):
        logger.warning(
            "EPSNodes: EPS LoRA Picker `selection.loras` is not a list "
            "(got %s); treating as empty",
            type(loras_raw).__name__,
        )
        return []

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row_raw in enumerate(loras_raw):
        if not isinstance(row_raw, dict):
            logger.warning(
                "EPSNodes: EPS LoRA Picker selection.loras[%d] is not an "
                "object; skipping",
                index,
            )
            continue
        file = row_raw.get("file")
        if not isinstance(file, str) or not file:
            logger.warning(
                "EPSNodes: EPS LoRA Picker selection.loras[%d] is missing "
                "a 'file'; skipping",
                index,
            )
            continue
        if file in seen:
            continue

        strength = _coerce_strength(row_raw.get("strength", 1.0))
        if strength is None:
            logger.warning(
                "EPSNodes: EPS LoRA Picker selection.loras[%d] (%r) has a "
                "non-numeric strength; skipping",
                index,
                file,
            )
            continue
        strength_clip_raw = row_raw.get("strength_clip")
        strength_clip = None
        if strength_clip_raw is not None:
            strength_clip = _coerce_strength(strength_clip_raw)
            if strength_clip is None:
                logger.warning(
                    "EPSNodes: EPS LoRA Picker selection.loras[%d] (%r) has "
                    "a non-numeric strength_clip; skipping",
                    index,
                    file,
                )
                continue

        seen.add(file)
        rows.append(
            {
                "file": file,
                "on": bool(row_raw.get("on", True)),
                "strength": strength,
                "strength_clip": strength_clip,
            }
        )
    return rows


def _sidecar_path(context: LibraryContext, resolved: str) -> Path | None:
    """``<resolved lora's on-disk path with extension replaced by .txt>``.

    ``None`` when *resolved* has no on-disk path on this machine (FORMAT.md
    §6.13) -- an installed-list name that ``resolve_lora_path`` can't map,
    the same edge case ``LoraLibraryApplySet._apply_stack`` already guards.
    """
    path = context.resolve_lora_path(resolved)
    if path is None:
        return None
    return Path(path).with_suffix(".txt")


def _sidecar_trigger_text(context: LibraryContext, resolved: str) -> str:
    """*resolved*'s sidecar trigger-word text, or ``""``.

    FORMAT.md §6.13: read UTF-8 best-effort (``errors="replace"``), stripped,
    capped at :data:`_SIDECAR_CAP` chars. No sidecar file is the NORMAL case
    (most loras don't ship one) -- every filesystem error, including a plain
    missing file, is swallowed at DEBUG level, never a warning.
    """
    sidecar = _sidecar_path(context, resolved)
    if sidecar is None:
        return ""
    try:
        text = sidecar.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.debug(
            "EPSNodes: EPS LoRA Picker sidecar for %r not read (%s)", resolved, exc
        )
        return ""
    return text.strip()[:_SIDECAR_CAP]


def _sidecar_mtime_token(context: LibraryContext, resolved: str) -> str:
    """*resolved*'s sidecar mtime as a cache-busting token, or ``"-"``.

    Feeds :meth:`EPSLoraPicker.IS_CHANGED` (FORMAT.md §6.13): an edited
    trigger-word file must re-run the node even though ``selection`` itself
    didn't change; a missing/unresolvable sidecar is a stable ``"-"`` rather
    than a value that would itself keep changing.
    """
    sidecar = _sidecar_path(context, resolved)
    if sidecar is None:
        return "-"
    try:
        return str(sidecar.stat().st_mtime)
    except OSError:
        return "-"


class EPSLoraPicker:
    """Browse/favorite/recall loras from a panel and build a stack (§6.13).

    The picker's own panel (``web/lora_library/picker.js``, not this module)
    writes the hidden ``selection`` STRING widget -- a JSON ``{"scope",
    "loras"}`` object -- as the user ticks loras on/off and adjusts
    strengths; :func:`_parse_selection` reads it back here, tolerant of any
    malformed shape (view state must never fail a queue). ``scope`` is
    view-only per-workflow folder restriction and never affects execution.

    Every enabled row resolves via :func:`sets_store.resolve_lora` -- the
    same separator-insensitive, cross-machine-lenient match
    ``LoraLibraryApplySet`` uses -- so a selection saved on one machine
    still resolves on another; an unresolvable row is skipped with a logged
    warning rather than failing the run. With neither ``model`` nor
    ``clip`` wired this node is a pure ``LORA_STACK``/trigger-word source,
    exactly like ``LoraLibraryApplySet`` with neither wired.
    """

    CATEGORY = "EPSNodes/LoRA"
    RETURN_TYPES = ("MODEL", "CLIP", "LORA_STACK", "STRING", "STRING")
    RETURN_NAMES = ("model", "clip", "lora_stack", "trigger_words", "loras_text")
    OUTPUT_TOOLTIPS = (
        "The input model, patched with the picker's enabled loras -- or "
        "passed through unchanged if model isn't wired, or nothing is "
        "selected.",
        "The input CLIP, patched with the picker's enabled loras -- or "
        "passed through unchanged under the same conditions as model.",
        "The picker's enabled loras as a LORA_STACK, ready to wire into "
        "EPS LoRA Iterator or EPS Apply LoRA Set.",
        "Trigger words read from each enabled lora's sidecar .txt file "
        "(same basename, .txt extension), joined into one string.",
        "A compact, filename-safe summary of the picked loras and "
        "strengths, e.g. 'detailer_0.8 style_1' -- handy for Save Image's "
        "filename_prefix.",
    )
    FUNCTION = "build"
    DESCRIPTION = (
        "Pick loras from a folder-scoped browser panel -- star favorites, "
        "pull from recently used, restrict the browser to one folder -- "
        "instead of hunting a flat dropdown or saving a set first. Emits "
        "the same outputs EPS Apply LoRA Set does, so it drops in "
        "anywhere that node does: wire model/clip to patch them directly, "
        "or leave both unwired and use this purely as a LORA_STACK / "
        "trigger-word source feeding EPS LoRA Iterator or another stack-"
        "consuming node. trigger_words is read live from each lora's "
        "sidecar .txt file; loras_text is ready for Save Image's "
        "filename_prefix. Click a lora once to select it and again to "
        "load it. The EPS Lora Loader State Controller can capture this "
        "node's selection as a named state and apply states back into it "
        "-- including when either node sits inside a subgraph."
    )

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {},
            "optional": {
                # Server-authoritative selection bridge -- the picker panel
                # owns this widget's value (FORMAT.md §6.13's single-JSON-
                # widget pattern, same rationale as EPSCheckpointSwitcher's
                # own `selection` -- see eps_image/nodes_checkpoint_switcher.
                # py's INPUT_TYPES for the identical hide mechanism). In
                # `optional`, not `required`, so a hand-built /prompt that
                # omits it entirely still queues, via `build`'s own
                # `selection=""` default, rather than being rejected before
                # it ever runs.
                #
                # "hidden": True is the Vue-nodes ("New node design") hide
                # flag (FORMAT.md §7.5): that renderer decides widget
                # visibility from the input spec's OPTIONS
                # (`options.hidden`) and ignores the litegraph
                # `widget.hidden` the frontend sets at attach time -- without
                # this, this internal plumbing widget would leak into Vue
                # nodes as a raw editable text field.
                "selection": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "hidden": True,
                        "tooltip": (
                            "JSON selection maintained by this node's own "
                            "panel (folder scope plus the picked loras, "
                            "on/off and strengths) -- you don't need to "
                            "type into it directly."
                        ),
                    },
                ),
                "model": (
                    "MODEL",
                    {
                        "tooltip": (
                            "The model to patch with the picker's enabled "
                            "loras. Leave unwired to use this node purely "
                            "as a LORA_STACK / trigger-word source."
                        ),
                    },
                ),
                "clip": (
                    "CLIP",
                    {
                        "tooltip": (
                            "The CLIP to patch with the picker's enabled "
                            "loras. Leave unwired along with model to use "
                            "this node purely as a LORA_STACK / trigger-"
                            "word source."
                        ),
                    },
                ),
            },
        }

    @classmethod
    def VALIDATE_INPUTS(cls, **_kwargs: Any) -> bool:
        # Same rationale as LoraLibraryApplySet's own dynamic `set` combo
        # (FORMAT.md §6.2): the installed lora list changes at runtime (a
        # lora added after this workflow was saved must still queue), and an
        # unresolvable row is already handled inside build() -- a logged
        # warning + skip (FORMAT.md §4's missing-lora posture) -- rather
        # than by rejecting the whole queue here.
        return True

    @classmethod
    def IS_CHANGED(cls, selection: str = "", model: Any = None, clip: Any = None) -> str:
        """Canonical, ``scope``-blind cache-busting token (FORMAT.md §6.13).

        A re-scope (moving the browser's folder restriction) must not
        re-run the graph -- ``scope`` never reaches :func:`_parse_selection`
        in the first place, so it can't leak in here either. Beyond the
        parsed rows themselves, each ENABLED row's sidecar ``.txt`` mtime is
        folded in (``"-"`` when absent/unresolvable/no context) so an edited
        trigger-word file re-runs the node even though the rows didn't
        change.
        """
        rows = _parse_selection(selection)
        context = _context
        tokens: list[dict[str, Any]] = []
        for row in rows:
            token = dict(row)
            resolved = (
                sets_store.resolve_lora(context, row["file"])
                if row["on"] and context is not None
                else None
            )
            token["sidecar_mtime"] = (
                "-" if resolved is None else _sidecar_mtime_token(context, resolved)
            )
            tokens.append(token)
        return json.dumps(tokens, sort_keys=True)

    def build(
        self,
        selection: str = "",
        model: Any = None,
        clip: Any = None,
    ) -> tuple[Any, Any, list[tuple[str, float, float]], str, str]:
        context = _context
        if context is None:
            logger.warning(
                "EPSNodes: EPS LoRA Picker has no context configured; passthrough"
            )
            return model, clip, [], "", ""

        stack: list[tuple[str, float, float]] = []
        trigger_texts: list[str] = []
        for row in _parse_selection(selection):
            if not row["on"]:
                continue
            resolved = sets_store.resolve_lora(context, row["file"])
            if resolved is None:
                logger.warning(
                    "EPSNodes: lora %r in EPS LoRA Picker selection could "
                    "not be resolved; skipping",
                    row["file"],
                )
                continue
            strength_clip = (
                row["strength"] if row["strength_clip"] is None else row["strength_clip"]
            )
            stack.append((resolved, row["strength"], strength_clip))
            trigger_texts.append(_sidecar_trigger_text(context, resolved))

        if model is not None or clip is not None:
            model, clip = nodes_sets.LoraLibraryApplySet._apply_stack(
                context, model, clip, stack
            )

        trigger_words = ", ".join(text for text in trigger_texts if text)
        return model, clip, stack, trigger_words, nodes_sets._loras_text(stack)
