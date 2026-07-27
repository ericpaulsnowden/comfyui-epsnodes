"""``EPSCrossSweep`` (FORMAT.md §6.10, display: "EPS Cross Sweep") — run a
whole lora sweep across a whole set of image/text pairs, organized.

Owner request (2026-07-23, the follow-up to §6.9's Cross Product): "if we
then wanted to run a lora or multiple loras at multiple strengths across all
of those images" — i.e. EPS LoRA Sweep's fan-out TIMES EPS Cross Product's
fan-out. Wiring both into one sampler ZIPS them instead (core list
execution, the same `slice_dict` repeat-last behavior §6.9 documents), so a
sweep of 11 steps and 8 image/prompt pairs yields 11 runs, not 88. This
node is the multiplier: it crosses the sweep GROUP (model/clip/label, three
index-aligned lists) with the pair GROUP (image/text, plus optionally name)
while keeping each group internally aligned — something two chained
Cross Products cannot express, because a model is only meaningful alongside
ITS clip and label.

**Strength-major on purpose (owner decision 2026-07-23):** the outer loop
is the sweep step, the inner loop the pairs — all pairs at strength 0.0,
then all pairs at 0.1, … — so each strength's results land together, which
reads naturally in a contact sheet or an appended PSD.

**`save_prefix` is the organization half of the ask** ("hopefully these
images are landing in folders that make sense… pass in a name for the
folder from the other nodes"): a ready-to-wire `SaveImage.filename_prefix`
list shaped `<base_folder>/<sweep label>/<pair name>` — ComfyUI's own
filename_prefix treats `/` as subfolders under the output dir, so a run
lands as e.g. `output/shoot42/my_great_lora_0.5/PortraitA_00001_.png`:
one folder per strength (strength-major again), files named by the pair.
`base_folder` is a plain widget (may be empty, may contain `/` for
nesting); the pair name comes from Cross Product's `name` output (wire the
Prompt Notebook's `name` into Cross Product's `names` input) and falls
back to a stable `pair_NN` when absent. All components are sanitized for
filesystem use (path separators and other hostile characters become `_`;
`..` segments are dropped) — SaveImage gets organization, never traversal.

No torch/ComfyUI import at module scope: every element (model, clip,
image) is treated as an opaque value, exactly like §6.4/§6.9.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("eps_image")

#: Characters replaced with ``_`` inside a single path component: path
#: separators, Windows-reserved punctuation, and control characters.
_HOSTILE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _as_clean_list(value: Any) -> list[Any]:
    """*value* as a list with ``None`` elements dropped (§6.9's tolerance:
    bare non-list values from direct callers become one-element lists)."""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        return [value]
    return [element for element in value if element is not None]


def _unwrap_scalar(value: Any, default: str) -> str:
    """First element of an ``INPUT_IS_LIST``-wrapped widget value, tolerating
    the bare form (same idiom as ``nodes_switcher._unwrap_toggles``)."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else default
    if value is None:
        return default
    return str(value)


def _safe_component(value: Any) -> str:
    """*value* as a single, filesystem-safe path component ('' if nothing
    survives). Hostile characters become ``_``; whitespace collapses; a
    component of only dots (``.``/``..``) is rejected outright."""
    text = _HOSTILE.sub("_", str(value))
    text = re.sub(r"\s+", " ", text).strip(" .")
    if not text or set(text) == {"."}:
        return ""
    return text


def _safe_base(value: str) -> list[str]:
    """*value* as a list of sanitized path components — ``/`` is ALLOWED in
    the base folder (deliberate nesting, e.g. ``shoots/today``); empty and
    dot-only segments are dropped."""
    return [c for c in (_safe_component(part) for part in value.split("/")) if c]


class EPSCrossSweep:
    """Sweep group x pair group, strength-major, with per-run save paths."""

    CATEGORY = "EPSNodes"
    RETURN_TYPES = ("MODEL", "CLIP", "IMAGE", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("model", "clip", "image", "text", "save_prefix", "label")
    OUTPUT_IS_LIST = (True, True, True, True, True, True)
    INPUT_IS_LIST = True
    OUTPUT_TOOLTIPS = (
        "This run's patched model.",
        "This run's patched CLIP.",
        "This run's image.",
        "This run's text.",
        "A ready-to-wire Save Image filename_prefix for this run: "
        "base_folder/<sweep label>/<pair name>.",
        "This run's strength label, unchanged from the sweep.",
    )
    FUNCTION = "run"
    DESCRIPTION = (
        "Runs a whole EPS LoRA Sweep across a whole set of EPS Cross "
        "Product pairs: wire the sweep's model, clip, and label outputs "
        "together with Cross Product's image and text (and optionally "
        "name) outputs, then continue the workflow from this node's "
        "outputs instead -- 11 sweep steps across 8 pairs, for example, "
        "means 88 runs. Strength-major order: every pair runs at the first "
        "strength, then every pair at the next, and so on, so results from "
        "the same strength land together. Wire save_prefix into Save "
        "Image's filename_prefix and every strength gets its own folder, "
        "named from the sweep label and the pair name, so a big run stays "
        "organized on disk."
    )

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "model": (
                    "MODEL",
                    {
                        "tooltip": (
                            "The swept models, one per strength step -- "
                            "wire from EPS LoRA Sweep's model output."
                        ),
                    },
                ),
                "clip": (
                    "CLIP",
                    {
                        "tooltip": (
                            "The swept CLIPs, one per strength step -- "
                            "wire from EPS LoRA Sweep's clip output."
                        ),
                    },
                ),
                "label": (
                    "STRING",
                    {
                        "forceInput": True,
                        "tooltip": (
                            "Each step's strength label -- wire from EPS "
                            "LoRA Sweep's label output. Wire-only."
                        ),
                    },
                ),
                "image": (
                    "IMAGE",
                    {
                        "tooltip": (
                            "The images to pair with the sweep -- wire "
                            "from EPS Cross Product's image output."
                        ),
                    },
                ),
                "text": (
                    "STRING",
                    {
                        "forceInput": True,
                        "tooltip": (
                            "The texts to pair with the sweep -- wire from "
                            "EPS Cross Product's text output. Wire-only."
                        ),
                    },
                ),
            },
            "optional": {
                # Cross Product's `name` output (usually the Prompt
                # Notebook entry heading riding through it) -- the
                # human-readable half of save_prefix. Optional: unwired
                # falls back to a stable pair_NN.
                "name": (
                    "STRING",
                    {
                        "forceInput": True,
                        "tooltip": (
                            "Optional short name per pair, wired from EPS "
                            "Cross Product's name output -- used to name "
                            "save_prefix's folders. Falls back to "
                            "pair_01, pair_02, ... when not wired. "
                            "Wire-only."
                        ),
                    },
                ),
                "base_folder": (
                    "STRING",
                    {
                        "default": "",
                        "tooltip": (
                            "An optional folder prefix for save_prefix, "
                            "e.g. shoots/today. May contain / to nest "
                            "folders. Leave empty to start save_prefix at "
                            "the output directory's root."
                        ),
                    },
                ),
            },
        }

    def run(
        self,
        model: Any = None,
        clip: Any = None,
        label: Any = None,
        image: Any = None,
        text: Any = None,
        name: Any = None,
        base_folder: Any = "",
    ) -> tuple[list[Any], ...]:
        models = _as_clean_list(model)
        clips = _as_clean_list(clip)
        labels = _as_clean_list(label)
        images = _as_clean_list(image)
        texts = _as_clean_list(text)
        names = _as_clean_list(name)
        base_parts = _safe_base(_unwrap_scalar(base_folder, ""))

        steps = min(len(models), len(clips), len(labels))
        pairs = min(len(images), len(texts))
        if len({len(models), len(clips), len(labels)}) > 1:
            logger.warning(
                "EPS Cross Sweep: sweep-side lists disagree (model=%d, clip=%d, "
                "label=%d) -- using the first %d step(s). Wire all three from "
                "the SAME EPS LoRA Sweep node.",
                len(models), len(clips), len(labels), steps,
            )
        if len(images) != len(texts):
            logger.warning(
                "EPS Cross Sweep: pair-side lists disagree (image=%d, text=%d) "
                "-- using the first %d pair(s). Wire both from the SAME "
                "EPS Cross Product node.",
                len(images), len(texts), pairs,
            )

        if steps == 0 or pairs == 0:
            # §6.4/§6.9's empty-safety pattern: nothing to run means the
            # branch silently skips; the queue succeeds.
            logger.info(
                "EPS Cross Sweep: %d sweep step(s) x %d pair(s) -- nothing to "
                "run; returning an execution blocker so downstream is "
                "silently skipped",
                steps, pairs,
            )
            from comfy_execution.graph import ExecutionBlocker

            blocked = [ExecutionBlocker(None)]
            return (blocked, blocked, blocked, blocked, blocked, blocked)

        out: dict[str, list[Any]] = {k: [] for k in self.RETURN_NAMES}
        for s in range(steps):  # strength-major: sweep step is the OUTER loop
            label_component = _safe_component(labels[s]) or f"step_{s + 1:02d}"
            for p in range(pairs):
                pair_component = (
                    _safe_component(names[p]) if p < len(names) else ""
                ) or f"pair_{p + 1:02d}"
                out["model"].append(models[s])
                out["clip"].append(clips[s])
                out["image"].append(images[p])
                out["text"].append(texts[p])
                out["label"].append(labels[s])
                out["save_prefix"].append(
                    "/".join([*base_parts, label_component, pair_component])
                )
        return tuple(out[k] for k in self.RETURN_NAMES)
