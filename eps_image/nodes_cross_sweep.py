"""``EPSCrossSweep`` (FORMAT.md §6.10, display: "EPS Run Multiplier") — run a
whole lora sweep across a whole set of image/text pairs, organized.

Owner request (2026-07-23, the follow-up to §6.9's Cross Product): "if we
then wanted to run a lora or multiple loras at multiple strengths across all
of those images" — i.e. EPS LoRA Iterator's fan-out TIMES EPS Cross Product's
fan-out. Wiring both into one sampler ZIPS them instead (core list
execution, the same `slice_dict` repeat-last behavior §6.9 documents), so a
sweep of 11 steps and 8 image/prompt pairs yields 11 runs, not 88. This
node is the multiplier: it crosses the sweep GROUP (model/clip/label, three
index-aligned lists, plus optionally vae -- v0.46.0, wired from EPS
Checkpoint Switcher so each run carries its checkpoint's own VAE) with the
pair GROUP (image/text, plus optionally name; image optional since v0.46.0
-- unwired means TEXT-ONLY pairs for txt2img iteration, with the image
output emitting a per-run blocker so only its own consumers skip)
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
    # `vae` is TAIL-APPENDED (v0.46.0): outputs resolve positionally against
    # this tuple (a saved link records [origin_id, origin_slot]), so appending
    # is the only §8-safe way to add one -- inserting next to `clip` would
    # silently repoint every saved workflow's image/text/save_prefix wires.
    RETURN_TYPES = ("MODEL", "CLIP", "IMAGE", "STRING", "STRING", "STRING", "VAE")
    RETURN_NAMES = ("model", "clip", "image", "text", "save_prefix", "label", "vae")
    OUTPUT_IS_LIST = (True, True, True, True, True, True, True)
    INPUT_IS_LIST = True
    OUTPUT_TOOLTIPS = (
        "This run's patched model.",
        "This run's patched CLIP.",
        "This run's image.",
        "This run's text.",
        "A ready-to-wire Save Image filename_prefix for this run: "
        "base_folder/<sweep label>/<pair name>.",
        "This run's strength label, unchanged from the sweep.",
        "This run's VAE, index-aligned with model/clip -- only when the "
        "optional vae input is wired (e.g. from EPS Checkpoint Switcher); "
        "unwired, this output blocks whatever consumes it.",
    )
    FUNCTION = "run"
    DESCRIPTION = (
        "Runs a whole EPS LoRA Iterator across a whole set of EPS Cross "
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
                            "wire from EPS LoRA Iterator's model output."
                        ),
                    },
                ),
                "clip": (
                    "CLIP",
                    {
                        "tooltip": (
                            "The swept CLIPs, one per strength step -- "
                            "wire from EPS LoRA Iterator's clip output."
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
                # v0.46.0: image moved REQUIRED -> OPTIONAL (loosens
                # validation only; saved workflows and API callers are
                # unaffected). Unwired = text-only mode: pairs are just the
                # texts (txt2img iteration -- e.g. Checkpoint Switcher x a
                # multi-select Prompt Notebook, no input images anywhere),
                # and the `image` OUTPUT emits one ExecutionBlocker per run
                # so only nodes wired to IT skip.
                "image": (
                    "IMAGE",
                    {
                        "tooltip": (
                            "The images to pair with the sweep -- wire "
                            "from EPS Cross Product's image output. "
                            "Optional: leave unwired for text-only "
                            "(txt2img) iteration; pairs are then just the "
                            "texts, and this node's image output blocks "
                            "whatever consumes it."
                        ),
                    },
                ),
                # v0.46.0: the sweep group's optional fourth list -- wire
                # from EPS Checkpoint Switcher's vae output so each run
                # carries its checkpoint's OWN VAE. Index-aligned with
                # model/clip/label; unwired, the vae OUTPUT blocks whatever
                # consumes it (there is no sensible fallback VAE).
                "vae": (
                    "VAE",
                    {
                        "tooltip": (
                            "Optional per-step VAEs, index-aligned with "
                            "model/clip/label -- wire from EPS Checkpoint "
                            "Switcher's vae output. Leave unwired when the "
                            "sweep side has no VAE (EPS LoRA Iterator); the "
                            "vae output then blocks whatever consumes it."
                        ),
                    },
                ),
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
        vae: Any = None,
    ) -> tuple[list[Any], ...]:
        models = _as_clean_list(model)
        clips = _as_clean_list(clip)
        labels = _as_clean_list(label)
        images = _as_clean_list(image)
        texts = _as_clean_list(text)
        names = _as_clean_list(name)
        vaes = _as_clean_list(vae)
        base_parts = _safe_base(_unwrap_scalar(base_folder, ""))

        # `vae is None` distinguishes UNWIRED (emit blockers on the vae
        # output; steps unaffected) from wired-but-empty (a real upstream
        # emitted nothing -> steps clamps to 0 -> the whole-node blocker
        # path, same as an empty model list).
        vae_wired = vae is not None
        # image is None likewise = text-only mode (v0.46.0): pairs are the
        # texts alone and the image OUTPUT blocks its consumers per run.
        text_only = image is None

        sweep_lengths = [len(models), len(clips), len(labels)]
        if vae_wired:
            sweep_lengths.append(len(vaes))
        steps = min(sweep_lengths)
        pairs = len(texts) if text_only else min(len(images), len(texts))
        if len(set(sweep_lengths)) > 1:
            logger.warning(
                "EPS Run Multiplier: sweep-side lists disagree (model=%d, clip=%d, "
                "label=%d%s) -- using the first %d step(s). Wire all of them "
                "from the SAME sweep-side node (EPS LoRA Iterator or EPS "
                "Checkpoint Switcher).",
                len(models), len(clips), len(labels),
                f", vae={len(vaes)}" if vae_wired else "",
                steps,
            )
        if not text_only and len(images) != len(texts):
            logger.warning(
                "EPS Run Multiplier: pair-side lists disagree (image=%d, text=%d) "
                "-- using the first %d pair(s). Wire both from the SAME "
                "EPS Cross Product node.",
                len(images), len(texts), pairs,
            )

        if steps == 0 or pairs == 0:
            # §6.4/§6.9's empty-safety pattern: nothing to run means the
            # branch silently skips; the queue succeeds.
            logger.info(
                "EPS Run Multiplier: %d sweep step(s) x %d pair(s) -- nothing to "
                "run; returning an execution blocker so downstream is "
                "silently skipped",
                steps, pairs,
            )
            from comfy_execution.graph import ExecutionBlocker

            blocked = [ExecutionBlocker(None)]
            return (blocked, blocked, blocked, blocked, blocked, blocked, blocked)

        # Unwired optional OUTPUTS emit one silent blocker PER RUN, keeping
        # every output list the same length (index alignment is this node's
        # whole contract). A blocker only skips the nodes wired to THAT
        # output; a None would crash them and an empty list breaks
        # slice_dict (§6.9). Imported lazily, once, only when needed.
        run_blocker = None
        if text_only or not vae_wired:
            from comfy_execution.graph import ExecutionBlocker

            run_blocker = ExecutionBlocker(None)

        out: dict[str, list[Any]] = {k: [] for k in self.RETURN_NAMES}
        for s in range(steps):  # strength-major: sweep step is the OUTER loop
            label_component = _safe_component(labels[s]) or f"step_{s + 1:02d}"
            for p in range(pairs):
                pair_component = (
                    _safe_component(names[p]) if p < len(names) else ""
                ) or f"pair_{p + 1:02d}"
                out["model"].append(models[s])
                out["clip"].append(clips[s])
                out["image"].append(run_blocker if text_only else images[p])
                out["text"].append(texts[p])
                out["label"].append(labels[s])
                out["save_prefix"].append(
                    "/".join([*base_parts, label_component, pair_component])
                )
                out["vae"].append(vaes[s] if vae_wired else run_blocker)
        return tuple(out[k] for k in self.RETURN_NAMES)
