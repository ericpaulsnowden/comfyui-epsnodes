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

**v0.49.0 — the Run Multiplier absorbs Cross Product (owner ask
2026-08-03: "what about the cross product and cross sweep combination? if
we keep the lora sweep separate?").** Two additive, §8-safe changes:

- The sweep group (``model``/``clip``/``label``) moved REQUIRED ->
  OPTIONAL, each member independently (the same validation-only loosening
  ``image`` already went through in v0.46.0 — saved workflows/API callers
  unaffected, since links and prompt inputs resolve by NAME). ``steps`` is
  the min over the WIRED sweep lists only; each UNWIRED sweep member's
  OUTPUT emits one blocker per run (the established per-run-blocker
  pattern below). With NO sweep member wired at all the node is a pure
  pair multiplier — one "null step", no ``<sweep label>`` level in
  ``save_prefix`` — i.e. exactly Cross Product's job.
- A ``pair_mode`` widget: ``"paired"`` (the DEFAULT — byte-identical to
  every workflow saved before it existed) keeps today's semantics, where
  ``image``/``text`` arrive index-ALIGNED (usually from a Cross Product)
  and ``name`` aligns per PAIR; ``"multiply"`` absorbs §6.9's cross:
  every image x every text, IMAGE-MAJOR in Cross Product's exact emission
  order, with ``name`` aligned per TEXT (the Notebook's ``name`` wired
  straight in, no Cross Product needed). ``multiply`` with a sweep wired
  is the new three-axis capability (sweep x images x texts) that used to
  take both nodes chained.

**v0.57.0 adds ``sweep_mode`` (owner ask 2026-08-09, straight from hitting
the v0.49.1 mismatch error with a 4-model Model Switcher and a 2-VAE VAE
Switcher: "You actually want 4 models x 2 VAEs (8 runs) ... each slot
corresponding to the same slot is not what I'm looking for").**
``"aligned"`` (the DEFAULT — byte-identical to every workflow saved before
it existed) keeps the one-axis zip; ``"multiply"`` splits the sweep side
into TWO axes: ``model``/``clip``/``label`` stay one aligned axis (an
Iterator's or Checkpoint Switcher's members belong together, so their >1
lengths must still agree), and ``vae`` is an independent axis crossed
against it — steps = model-axis steps x vaes, MODEL-MAJOR so each model
loads once and runs every vae before the next model. ``save_prefix``'s
sweep level becomes ``<label>_vaeNN`` per combination. A vae wired from
the SAME node as the model axis in multiply mode is a hard error (those
lists are aligned by construction; crossing them would mismatch
checkpoints) — degrade-if-prompt-unreadable, like the v0.51.0 guard.

``EPSCrossProduct`` was REMOVED outright in v0.50.0 at the owner's
explicit direction ("Delete the old cross product node. I will remove
from old workflows -- I would rather replace with the new one") — a
deliberate §8 exception. Workflows that still contain one will show a
missing-node error until that node is replaced with this one
(`pair_mode: multiply`); the class id ``EPSCrossProduct`` is retired and
must NEVER be reused for a different node.

No torch/ComfyUI import at module scope: every element (model, clip,
image) is treated as an opaque value, exactly like §6.4/§6.9.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("eps_image")

#: Characters replaced with ``_`` inside a single path component: path
#: separators, Windows-reserved punctuation, and control characters.
_HOSTILE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

#: ``pair_mode`` widget values (v0.49.0, module docstring). Stable
#: identifiers -- widget values persist in saved workflows (§8).
PAIR_MODE_PAIRED = "paired"
PAIR_MODE_MULTIPLY = "multiply"
PAIR_MODES = [PAIR_MODE_PAIRED, PAIR_MODE_MULTIPLY]

#: ``sweep_mode`` widget values (v0.57.0, owner ask 2026-08-09: "4 models x
#: 2 VAEs (8 runs) -- each slot corresponding to the same slot is not what
#: I'm looking for"). Same stable-identifier rule as PAIR_MODES.
SWEEP_MODE_ALIGNED = "aligned"
SWEEP_MODE_MULTIPLY = "multiply"
SWEEP_MODES = [SWEEP_MODE_ALIGNED, SWEEP_MODE_MULTIPLY]


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


def _json_text(value: Any) -> str | None:
    """*value* as the string run_info carries for a text-ish slot (name /
    text / label, v0.71.0 provenance M3): ``None`` stays ``None`` (the
    slot's input is unwired or absent), a str passes through byte-exact,
    anything else is stringified -- run_info must always json.dumps."""
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


def _unwrap_hidden(value: Any) -> Any:
    """Undo ``INPUT_IS_LIST``'s wrapping of a hidden input -- verbatim
    ``nodes_switcher._unwrap_hidden`` (see its docstring for the core
    ``get_input_data`` shapes this mirrors)."""
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def _consumed_output_slots(prompt: Any, unique_id: Any) -> set[int]:
    """Which of THIS node's output slots some other node in *prompt*
    consumes -- the same PROMPT-scan technique ``nodes_switcher``'s
    ``_slots_fed_by_an_empty_switcher`` uses, pointed the other way
    (consumers of us, rather than our upstreams). A link reference in a
    prompt is a two-element ``[origin_id, origin_slot]`` list; origin ids
    compare as strings (API prompts key nodes by string). Malformed or
    missing *prompt*/*unique_id* (direct callers, tests, old servers)
    degrades to "nothing known consumed" -- the v0.51.0 guard built on
    this then simply stays out of the way.
    """
    if not isinstance(prompt, dict) or unique_id is None:
        return set()
    uid = str(unique_id)
    consumed: set[int] = set()
    for node in prompt.values():
        inputs = node.get("inputs") if isinstance(node, dict) else None
        if not isinstance(inputs, dict):
            continue
        for value in inputs.values():
            if (
                isinstance(value, (list, tuple))
                and len(value) == 2
                and str(value[0]) == uid
                and isinstance(value[1], int)
            ):
                consumed.add(value[1])
    return consumed


def _input_origin(prompt: Any, unique_id: Any, input_name: str) -> str | None:
    """The node id feeding THIS node's *input_name*, or ``None``.

    Reads the prompt's own ``[origin_id, origin_slot]`` link shape (the
    same encoding :func:`_consumed_output_slots` scans in the other
    direction) for this node's entry. Feeds the v0.57.0 same-origin guard;
    anything malformed or missing degrades to ``None`` -- "origin unknown"
    -- so the guard built on it simply stays out of the way, exactly like
    the v0.51.0 consumed-slots guard.
    """
    if not isinstance(prompt, dict) or unique_id is None:
        return None
    node = prompt.get(str(unique_id))
    inputs = node.get("inputs") if isinstance(node, dict) else None
    if not isinstance(inputs, dict):
        return None
    link = inputs.get(input_name)
    if isinstance(link, (list, tuple)) and len(link) == 2:
        return str(link[0])
    return None


class EPSCrossSweep:
    """Sweep group x pair group, strength-major, with per-run save paths."""

    CATEGORY = "EPSNodes"
    # `vae` is TAIL-APPENDED (v0.46.0): outputs resolve positionally against
    # this tuple (a saved link records [origin_id, origin_slot]), so appending
    # is the only §8-safe way to add one -- inserting next to `clip` would
    # silently repoint every saved workflow's image/text/save_prefix wires.
    # §8 tail-append law: model_low (v0.66.0, WAN pairing) lands LAST.
    # v0.70.0 (provenance M2, FORMAT §6.10/§6.14): `run_info` is TAIL-APPENDED
    # (§8 -- outputs are positional): one JSON per run, index-aligned with
    # every other output, for EPS Save Image to bake a pre-soloed workflow.
    # v0.71.0 (provenance M3): the same JSON also carries name/text/label
    # (null when unwired) -- no new output, the field set just grew.
    RETURN_TYPES = (
        "MODEL", "CLIP", "IMAGE", "STRING", "STRING", "STRING", "VAE", "MODEL", "STRING",
    )
    RETURN_NAMES = (
        "model", "clip", "image", "text", "save_prefix", "label", "vae", "model_low", "run_info",
    )
    OUTPUT_IS_LIST = (True,) * 9
    INPUT_IS_LIST = True
    OUTPUT_TOOLTIPS = (
        "This run's model -- only when the optional model input is wired; "
        "unwired, this output blocks whatever consumes it.",
        "This run's CLIP -- only when the optional clip input is wired; "
        "unwired, this output blocks whatever consumes it.",
        "This run's image.",
        "This run's text.",
        "A ready-to-wire Save Image filename_prefix for this run: "
        "base_folder/<sweep label>/<pair name>_<run token> (no sweep "
        "wired: base_folder/<pair name>_<run token>). The run token "
        "(v0.67.0, e.g. m2_i1_t3) names this exact run and is what "
        "solo_run accepts.",
        "This run's label, unchanged from the sweep side -- only when the "
        "optional label input is wired; unwired, this output blocks "
        "whatever consumes it.",
        "This run's VAE, index-aligned with model/clip -- only when the "
        "optional vae input is wired (e.g. from EPS Checkpoint Switcher); "
        "unwired, this output blocks whatever consumes it.",
        "The LOW-noise partner model for each run, index-aligned with the "
        "model output (WAN-style high/low pairs -- wire from EPS Model "
        "Switcher's models_low). Blocks its consumers on runs where no "
        "low model was wired.",

        "This run's provenance, as JSON: its token, this node's id, its "
        "place in the set, and this run's name/text/label. Wire into EPS "
        "Save Image's run_info input and "
        "every saved file carries a workflow already soloed to the run that "
        "made it -- drop the image to recreate just that one.",
    )
    FUNCTION = "run"
    DESCRIPTION = (
        "Multiplies whatever you wire in into one organized batch of runs. "
        "Wire a sweep side (EPS LoRA Iterator's or EPS Checkpoint "
        "Switcher's model/clip/label, plus vae from the latter) and a pair "
        "side (images and texts), and every sweep step runs every pair -- "
        "11 steps across 8 pairs means 88 runs, grouped step by step. The "
        "pair side multiplies by default -- every image crossed with every "
        "text right here (both mode dropdowns are hidden; reveal them "
        "via the Show mode options property to pick the index-aligned "
        "paired/aligned modes) "
        "-- an Image Grid and a multi-select Prompt Notebook wired "
        "straight in. The sweep side is optional: wire none of "
        "it and this node is a pure image x text multiplier. Wire "
        "save_prefix into Save Image's filename_prefix and runs land in "
        "folders named by step and pair, so big batches stay organized on "
        "disk. sweep_mode=multiply crosses the model/clip/label axis with "
        "an independent vae axis (4 models x 2 VAEs = 8 runs) instead of "
        "pairing them one-to-one. The readout at the bottom of the node "
        "shows how many runs are planned BEFORE you queue, so an "
        "overnight batch can be sanity-checked first. For WAN-style "
        "high/low models, wire model_low alongside model: the pair stays "
        "welded together on every run and never multiplies the count. "
        "Every saved filename ends with a run token like m2_i1_t3 (sweep "
        "step 2, image 1, text 3); paste that token into solo_run to "
        "re-run exactly that one image out of the whole set -- clear it "
        "to run everything again. Wire run_info into EPS Save Image and "
        "every saved file carries its own pre-soloed workflow: drop the "
        "image onto the canvas to recreate just that one."
    )

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "text": (
                    "STRING",
                    {
                        "forceInput": True,
                        "tooltip": (
                            "The texts to run -- wire a multi-select EPS "
                            "Prompt Notebook's text output straight in; "
                            "they are crossed with the images by default "
                            "(pair_mode multiply). Reveal pair_mode via "
                            "the Show mode options property to pick "
                            "paired when image and text are already "
                            "index-aligned. Wire-only."
                        ),
                    },
                ),
            },
            "optional": {
                # v0.49.0: the whole sweep group moved REQUIRED -> OPTIONAL
                # (module docstring) -- wire none of these to use the node
                # as a pure pair multiplier (Cross Product's old job).
                "model": (
                    "MODEL",
                    {
                        "tooltip": (
                            "The swept models, one per step -- wire from "
                            "EPS LoRA Iterator or EPS Checkpoint Switcher. "
                            "Optional: leave the whole sweep side unwired "
                            "to just multiply images x texts; this output "
                            "then blocks whatever consumes it."
                        ),
                    },
                ),
                # v0.66.0 WAN pairing: WELDED to the model axis, never a
                # new one -- run counts are untouched.
                "model_low": (
                    "MODEL",
                    {
                        "tooltip": (
                            "Each step's LOW-noise partner model (WAN-style "
                            "high/low pairs) -- wire from EPS Model "
                            "Switcher's models_low. Travels welded to the "
                            "model input: same length (or a single low to "
                            "broadcast), never a new sweep axis, so run "
                            "counts don't change. In sweep_mode multiply "
                            "the pair stays a pair while vae crosses "
                            "against it."
                        ),
                    },
                ),
                "clip": (
                    "CLIP",
                    {
                        "tooltip": (
                            "The swept CLIPs, one per step, index-aligned "
                            "with model -- same source as model. Optional; "
                            "unwired, this node's clip output blocks "
                            "whatever consumes it."
                        ),
                    },
                ),
                "label": (
                    "STRING",
                    {
                        "forceInput": True,
                        "tooltip": (
                            "Each step's label, index-aligned with model "
                            "-- wire from the same sweep-side node. "
                            "Optional; unwired, save_prefix uses step_01, "
                            "step_02, ... and the label output blocks "
                            "whatever consumes it. Wire-only."
                        ),
                    },
                ),
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
                            "The images to run. With pair_mode paired "
                            "they arrive index-aligned with text; with "
                            "multiply, "
                            "EVERY image is crossed with EVERY text (wire "
                            "an EPS Image Grid or Switcher straight in). "
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
                            "Optional short name used for save_prefix's "
                            "folders. With pair_mode paired it aligns per "
                            "PAIR; with multiply it aligns per TEXT (the "
                            "Prompt Notebook's name output, wired "
                            "straight in). "
                            "Falls back to pair_01, pair_02, ... when not "
                            "wired. Wire-only."
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
                # v0.49.0, APPENDED after base_folder on purpose: widgets_
                # values restores positionally, so a widget added BEFORE
                # base_folder would swallow every saved workflow's folder
                # value (the same tail-append law resolution.js documents
                # for its preset widgets).
                "pair_mode": (
                    PAIR_MODES,
                    {
                        # v0.66.1 (owner): multiply is the default -- "there
                        # are rare occasions when you would want them to be
                        # anything but multiply". NOTE the compat cost,
                        # accepted explicitly: a workflow saved BEFORE this
                        # widget existed now loads as multiply, not paired.
                        "default": PAIR_MODE_MULTIPLY,
                        "tooltip": (
                            "How image and text combine. paired: they "
                            "arrive already index-aligned (one image per "
                            "text) and run as-is. multiply: EVERY image "
                            "is crossed with EVERY text right here "
                            "(image-major), so you can wire an Image "
                            "Grid and a multi-select Prompt Notebook "
                            "straight in. multiply is the default; "
                            "workflows that never saved this widget load "
                            "as multiply too."
                        ),
                    },
                ),
                # v0.57.0, APPENDED after pair_mode: the same tail-append
                # law the pair_mode comment above cites -- a widget added
                # anywhere but the end would positionally swallow saved
                # workflows' pair_mode/base_folder values.
                "sweep_mode": (
                    SWEEP_MODES,
                    {
                        # v0.66.1: multiply by default (same owner decision
                        # and the same accepted pre-widget-save compat cost
                        # as pair_mode above; the same-origin guard keeps a
                        # one-checkpoint-switcher wiring failing LOUDLY with
                        # an actionable message rather than mispairing).
                        "default": SWEEP_MODE_MULTIPLY,
                        "tooltip": (
                            "How the sweep side combines with vae. "
                            "aligned: model/clip/label/vae are one "
                            "index-aligned set (an EPS LoRA Iterator or "
                            "EPS Checkpoint Switcher, where step 3's "
                            "model belongs with step 3's vae). multiply: "
                            "model/clip/label stay one aligned axis, and "
                            "EVERY step of it runs with EVERY vae -- e.g. "
                            "a 4-model Model Switcher x a 2-VAE VAE "
                            "Switcher = 8 runs, model-major so each model "
                            "loads once. multiply is the default; "
                            "workflows that never saved this widget load "
                            "as multiply too."
                        ),
                    },
                ),
                # v0.67.0 provenance M1 (tail-append per §8, after
                # sweep_mode): type one run's token here to re-run JUST
                # that combination.
                "solo_run": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "tooltip": (
                            "Re-run ONE combination from a set: paste the "
                            "run token from a saved file's name (e.g. "
                            "m2_v1_i3_t1) and only that run executes. "
                            "Leave empty for the whole set. A token that "
                            "matches no run fails the queue loudly rather "
                            "than silently doing nothing."
                        ),
                    },
                ),
            },
            # v0.51.0: lets run() see its own consumers (the guard below).
            # Same hidden pair as nodes_switcher/nodes_distributor.
            "hidden": {"prompt": "PROMPT", "unique_id": "UNIQUE_ID"},
        }

    def run(
        self,
        model: Any = None,
        model_low: Any = None,
        clip: Any = None,
        label: Any = None,
        image: Any = None,
        text: Any = None,
        name: Any = None,
        base_folder: Any = "",
        vae: Any = None,
        pair_mode: Any = PAIR_MODE_MULTIPLY,
        sweep_mode: Any = SWEEP_MODE_MULTIPLY,
        solo_run: Any = "",
        prompt: Any = None,
        unique_id: Any = None,
    ) -> tuple[list[Any], ...]:
        models = _as_clean_list(model)
        model_lows = _as_clean_list(model_low)
        clips = _as_clean_list(clip)
        labels = _as_clean_list(label)
        images = _as_clean_list(image)
        texts = _as_clean_list(text)
        names = _as_clean_list(name)
        vaes = _as_clean_list(vae)
        base_parts = _safe_base(_unwrap_scalar(base_folder, ""))
        multiply = _unwrap_scalar(pair_mode, PAIR_MODE_MULTIPLY) == PAIR_MODE_MULTIPLY
        sweep_multiply = (
            _unwrap_scalar(sweep_mode, SWEEP_MODE_MULTIPLY) == SWEEP_MODE_MULTIPLY
        )

        # `x is None` distinguishes UNWIRED (emit blockers on that output;
        # counts unaffected) from wired-but-empty (a real upstream emitted
        # nothing -> the corresponding count clamps to 0 -> the whole-node
        # blocker path). v0.49.0 generalized this from image/vae to every
        # sweep-group member: steps is the min over the WIRED sweep lists
        # only, and each unwired member's output blocks per run.
        model_wired = model is not None
        model_low_wired = model_low is not None
        clip_wired = clip is not None
        label_wired = label is not None
        vae_wired = vae is not None
        # M3 (v0.71.0): run_info carries this run's pair name only when the
        # name input is actually wired -- an unwired name is null there, not
        # the "" the save_prefix fallback uses (EPS Save Image tells the two
        # apart when matching a Notebook's entry to THIS run).
        name_wired = name is not None
        text_only = image is None
        sweep_wired = model_wired or model_low_wired or clip_wired or label_wired or vae_wired

        # v0.51.0 guard (owner report 2026-08-03: a txt2img graph consuming
        # the image OUTPUT with no image INPUT wired "completes immediately,
        # nothing generated" -- every downstream of that wire was silently
        # skipped by the per-run blockers). An output whose backing input is
        # unwired LOOKS wireable but poisons its consumers; if the prompt
        # shows something actually consuming it, that is always a miswire --
        # fail the queue naming both ends instead of silently skipping.
        consumed = _consumed_output_slots(_unwrap_hidden(prompt), _unwrap_hidden(unique_id))
        dead_outputs = []
        if 0 in consumed and not model_wired:
            dead_outputs.append("model (wire a model source into the model input)")
        if 1 in consumed and not clip_wired:
            dead_outputs.append("clip (wire a CLIP source into the clip input)")
        if 2 in consumed and text_only:
            dead_outputs.append(
                "image (wire images into the image input -- or, for text-to-image, "
                "don't consume the image output: drive an Empty Latent Image instead)"
            )
        if 5 in consumed and not label_wired:
            dead_outputs.append("label (wire a label source into the label input)")
        if 6 in consumed and not vae_wired:
            dead_outputs.append("vae (wire a VAE source into the vae input)")
        if 7 in consumed and not model_low_wired:
            # Audit 2026-08-21: slot 7 (v0.66.0) was missing from this guard.
            dead_outputs.append(
                "model_low (wire EPS Model Switcher's models_low into the model_low input)"
            )
        if dead_outputs:
            raise ValueError(
                "EPS Run Multiplier: these outputs are wired downstream but their "
                "matching inputs are not wired, so every run on those wires would "
                "be silently skipped and the workflow would finish without "
                "generating anything: " + "; ".join(dead_outputs)
            )

        wired_sweep = {
            input_name: length
            for input_name, wired, length in (
                ("model", model_wired, len(models)),
                # v0.66.0: model_low is a MEMBER of the aligned model axis
                # (welded, per the owner's WAN spec) -- the existing length
                # rules do exactly the right thing: equal lengths pair,
                # length 1 broadcasts, a disagreement fails loudly naming
                # it, and a wired-but-empty low collapses the node like any
                # other empty sweep input. It is never its own axis, so
                # step math never changes.
                ("model_low", model_low_wired, len(model_lows)),
                ("clip", clip_wired, len(clips)),
                ("label", label_wired, len(labels)),
                ("vae", vae_wired, len(vaes)),
            )
            if wired
        }
        # v0.49.1 sweep-side length rules (owner bug report 2026-08-03: a
        # stale 2-long `label` wire silently clamped a 4-model sweep to 2
        # steps -- the old min()+console-warning was invisible from the
        # browser, and the run just looked wrong):
        #   - length 1 BROADCASTS: a single constant VAE (or label) across
        #     an N-step sweep is legitimate and repeats for every step;
        #   - lengths >1 must AGREE EXACTLY or the queue FAILS, naming
        #     the disagreeing inputs -- a disagreement between fanned
        #     sweep lists is always a miswire, never something to clamp.
        # v0.57.0 (owner ask 2026-08-09, born from hitting exactly that
        # error with model=4/vae=2): `sweep_mode: multiply` splits the
        # sweep side into TWO axes -- model/clip/label stay one aligned
        # axis (an Iterator's or Checkpoint Switcher's members belong
        # together), and vae is an independent axis crossed against it,
        # model-major (each model loads once, then runs every vae).
        axis1 = {k: v for k, v in wired_sweep.items() if k != "vae"}
        checked = axis1 if sweep_multiply else wired_sweep
        multi_lengths = {length for length in checked.values() if length > 1}
        if len(multi_lengths) > 1:
            conflict = ", ".join(f"{k}={v}" for k, v in checked.items() if v > 1)
            # Review 2026-08-09: length-0 is NOT "fine" -- a wired-but-empty
            # list doesn't broadcast, it collapses the whole node to the
            # blocker path, so it gets its own honest clause instead of
            # being lumped in with the harmless length-1 broadcasts.
            fine_items = [f"{k}={v}" for k, v in checked.items() if v == 1]
            empty_items = [f"{k}={v}" for k, v in checked.items() if v == 0]
            notes = []
            if fine_items:
                plural = len(fine_items) > 1
                notes.append(
                    f"the length-1 input{'s' if plural else ''} -- "
                    f"{', '.join(fine_items)} -- broadcast{'' if plural else 's'} "
                    f"fine and {'are' if plural else 'is'} not the problem"
                )
            if empty_items:
                plural = len(empty_items) > 1
                notes.append(
                    f"{', '.join(empty_items)} {'are' if plural else 'is'} wired "
                    "but EMPTY, which would block the whole node on its own"
                )
            fine_note = f" ({'; '.join(notes)})." if notes else "."
            group = (
                "model/model_low/clip/label"
                if sweep_multiply
                else "model/model_low/clip/label (and vae)"
            )
            # The multiply hint ONLY when vae is actually a party to the
            # disagreement (review 2026-08-09: for a model-vs-label conflict
            # the hint was wrong advice -- multiply mode still checks the
            # model axis, so flipping the widget re-fails identically).
            vae_party = not sweep_multiply and checked.get("vae", 0) > 1
            vae_hint = (
                " To run EVERY model with EVERY vae instead, set sweep_mode "
                "to multiply."
                if vae_party
                else ""
            )
            raise ValueError(
                f"EPS Run Multiplier: the sweep-side inputs disagree about how many "
                f"steps there are ({conflict}). Wire {group} from "
                f"the SAME node (EPS LoRA Iterator or EPS Checkpoint Switcher), and "
                f"unwire any of them that came from somewhere else"
                f"{fine_note}{vae_hint}"
            )
        # In multiply mode a vae wired FROM THE SAME NODE as the model axis
        # (one Checkpoint Switcher feeding both) is already index-aligned by
        # construction -- crossing it against itself would pair checkpoint
        # A's model with checkpoint B's vae. Same degrade posture as the
        # v0.51.0 guard: an unreadable prompt just skips the check.
        if sweep_multiply and vae_wired and len(vaes) > 1:
            vae_origin = _input_origin(
                _unwrap_hidden(prompt), _unwrap_hidden(unique_id), "vae"
            )
            if vae_origin is not None:
                for axis1_input in ("model", "model_low", "clip", "label"):
                    origin = _input_origin(
                        _unwrap_hidden(prompt), _unwrap_hidden(unique_id), axis1_input
                    )
                    if origin is not None and origin == vae_origin:
                        raise ValueError(
                            "EPS Run Multiplier: sweep_mode is multiply, but vae "
                            f"and {axis1_input} are wired from the SAME node -- "
                            "those lists are already index-aligned (one entry per "
                            "checkpoint/step), and crossing them would pair one "
                            "step's model with a DIFFERENT step's vae. Use "
                            "sweep_mode aligned for that wiring, or wire vae from "
                            "an independent source (e.g. an EPS VAE Switcher) to "
                            "multiply against."
                        )
        # No sweep at all = ONE null step (a pure pair multiplier -- Cross
        # Product's old job); save_prefix then omits the sweep-label level.
        # A wired-but-EMPTY sweep list still collapses steps to 0 (the
        # whole-node blocker path below), same as before. In multiply mode
        # steps = model-axis steps x vae steps; an unwired vae (or aligned
        # mode) leaves the math exactly as it was.
        vae_axis_len = len(vaes) if (sweep_multiply and vae_wired) else 1
        if not sweep_wired:
            steps = 1
        elif 0 in wired_sweep.values():
            steps = 0
        elif sweep_multiply:
            axis1_len = next(iter(multi_lengths), 1)
            steps = axis1_len * vae_axis_len
        else:
            steps = next(iter(multi_lengths), 1)

        def _sweep_index(length: int, step: int) -> int:
            """Broadcast rule: a length-1 sweep list serves index 0 forever."""
            return step if length > 1 else 0

        # The pair side (v0.49.0, module docstring): "paired" runs the
        # index-aligned lists as-is (min-clamped, warned); "multiply"
        # crosses every image with every text right here, image-major --
        # §6.9's exact emission order, with names aligned per TEXT (the
        # Notebook wired straight in) rather than per pair.
        # v0.67.0 provenance M1: every pair row carries its own pure-index
        # token fragment (owner's pinned naming choice) -- i/t for a
        # multiplied pair, t alone for text-only, p for an aligned pair.
        pair_rows: list[tuple[Any, Any, str, str]]  # (image-or-None, text, name, ptoken)
        if text_only:
            pair_rows = [
                (None, texts[p], names[p] if p < len(names) else "", f"t{p + 1}")
                for p in range(len(texts))
            ]
        elif multiply:
            pair_rows = [
                (img, texts[t], names[t] if t < len(names) else "", f"i{i + 1}_t{t + 1}")
                for i, img in enumerate(images)
                for t in range(len(texts))
            ]
        else:
            pairs = min(len(images), len(texts))
            if len(images) != len(texts):
                logger.warning(
                    "EPS Run Multiplier: pair-side lists disagree (image=%d, text=%d) "
                    "-- using the first %d pair(s). Wire image and text from "
                    "sources that emit index-aligned lists, or set pair_mode "
                    "to multiply to cross them here instead.",
                    len(images), len(texts), pairs,
                )
            pair_rows = [
                (images[p], texts[p], names[p] if p < len(names) else "", f"p{p + 1}")
                for p in range(pairs)
            ]

        if steps == 0 or not pair_rows:
            # §6.4/§6.9's empty-safety pattern: nothing to run means the
            # branch silently skips; the queue succeeds.
            logger.info(
                "EPS Run Multiplier: %d sweep step(s) x %d pair(s) -- nothing to "
                "run; returning an execution blocker so downstream is "
                "silently skipped",
                steps, len(pair_rows),
            )
            from comfy_execution.graph import ExecutionBlocker

            # One blocked list per declared output -- derived from
            # RETURN_NAMES so a future tail append can never drift this
            # (v0.66.0 added model_low and this literal 7-tuple missed it).
            return tuple([ExecutionBlocker(None)] for _ in self.RETURN_NAMES)

        # Unwired optional OUTPUTS emit one silent blocker PER RUN, keeping
        # every output list the same length (index alignment is this node's
        # whole contract). A blocker only skips the nodes wired to THAT
        # output; a None would crash them and an empty list breaks
        # slice_dict (§6.9). Imported lazily, once, only when needed.
        run_blocker = None
        # model_low_wired included (audit 2026-08-21): with the four classic
        # sweep inputs all wired and only model_low unwired, no blocker was
        # built and the model_low output emitted bare None per run -- the
        # exact crash-a-consumer shape this block exists to prevent.
        if text_only or not (
            model_wired and model_low_wired and clip_wired and label_wired and vae_wired
        ):
            from comfy_execution.graph import ExecutionBlocker

            run_blocker = ExecutionBlocker(None)

        # v0.59.0 (owner ask 2026-08-09: "show the number of runs ... before
        # potentially running something that may go overnight and be
        # wrong"): announce the DEFINITIVE count the moment this node
        # executes -- topologically that is the first seconds of a queue,
        # long before samplers grind, so a wrong number can be cancelled
        # cheaply. The on-node readout (web/eps_image/cross_sweep.js) is the
        # pre-queue ESTIMATE; this event is the execution-time truth. Same
        # degrade posture as the §6.6 grid toast: no live server, no event.
        # v0.67.0 provenance M1: the run TOKEN -- pure indices (owner's
        # pinned choice): sweep fragment m{axis1} (+_v{vae} only when vae
        # is an independent axis; in aligned mode vae follows the axis and
        # the folder already says so), then the pair fragment from
        # pair_rows. A file separated from its folders still identifies.
        def _run_token(a1_idx: int, v_idx: int, ptoken: str) -> str:
            parts = []
            if sweep_wired:
                stoken = f"m{a1_idx + 1}"
                if sweep_multiply and vae_axis_len > 1:
                    stoken += f"_v{v_idx + 1}"
                parts.append(stoken)
            parts.append(ptoken)
            return "_".join(parts)

        solo = str(_unwrap_scalar(solo_run, "") or "").strip()
        emission: list[tuple[int, int, int, int, tuple[Any, Any, str, str], str]] = []
        for s in range(steps):
            if sweep_multiply and vae_axis_len > 1:
                a1_idx, v_idx = divmod(s, vae_axis_len)
            else:
                a1_idx = v_idx = s
            for p, row in enumerate(pair_rows):
                token = _run_token(a1_idx, v_idx, row[3])
                if solo and token != solo:
                    continue
                emission.append((s, a1_idx, v_idx, p, row, token))
        if solo and not emission:
            # A typo must never burn a queue as a silent 0-run success.
            raise ValueError(
                f"EPS Run Multiplier: solo_run {solo!r} matches none of the "
                f"{steps * len(pair_rows)} runs in this set (sweep steps "
                f"1..{steps}, pairs 1..{len(pair_rows)} -- e.g. "
                f"{_run_token(0, 0, pair_rows[0][3])!r}). Fix the token or "
                "clear solo_run to run the whole set."
            )

        total = len(emission)
        logger.info(
            "EPS Run Multiplier: %d run(s) -- %d sweep step(s) x %d pair(s)",
            total, steps, len(pair_rows),
        )
        try:
            from server import PromptServer  # ComfyUI-only; absent in tests

            PromptServer.instance.send_sync(
                "eps-run-multiplier-count",
                {
                    "node": str(_unwrap_hidden(unique_id)),
                    "steps": steps,
                    "pairs": len(pair_rows),
                    "total": total,
                },
            )
        except Exception:
            # No live server (tests/direct callers) -- the log line above
            # already carries the count.
            pass

        out: dict[str, list[Any]] = {k: [] for k in self.RETURN_NAMES}
        # strength-major: sweep step is the outer sort of `emission` (built
        # in that order above); v0.57.0's model-major multiply decomposition
        # and the v0.67.0 solo filter both already applied.
        for _s, a1_idx, v_idx, p, row, token in emission:
            pair_image, pair_text, pair_name, _ptoken = row
            label_component = (
                (_safe_component(labels[_sweep_index(len(labels), a1_idx)]) if label_wired else "")
                or f"step_{a1_idx + 1:02d}"
            )
            if sweep_multiply and vae_axis_len > 1:
                # One sortable folder level per (model, vae) combination --
                # the vae axis has no label input, so it tags by position.
                label_component = f"{label_component}_vae{v_idx + 1:02d}"
            # v0.67.0: the FILE name carries the full run token, so a file
            # separated from its folder tree still identifies itself.
            pair_component = _safe_component(pair_name) or f"pair_{p + 1:02d}"
            pair_component = f"{pair_component}_{token}"
            out["model"].append(
                models[_sweep_index(len(models), a1_idx)] if model_wired else run_blocker
            )
            out["model_low"].append(
                model_lows[_sweep_index(len(model_lows), a1_idx)]
                if model_low_wired
                else run_blocker
            )
            out["clip"].append(
                clips[_sweep_index(len(clips), a1_idx)] if clip_wired else run_blocker
            )
            out["image"].append(run_blocker if text_only else pair_image)
            out["text"].append(pair_text)
            out["label"].append(
                labels[_sweep_index(len(labels), a1_idx)] if label_wired else run_blocker
            )
            # No sweep wired at all -> no sweep-label folder level: the
            # save path is just <base>/<pair>, exactly what a plain
            # image x text multiplication wants on disk.
            prefix_parts = (
                [*base_parts, pair_component]
                if not sweep_wired
                else [*base_parts, label_component, pair_component]
            )
            out["save_prefix"].append("/".join(prefix_parts))
            # v0.71.0 (provenance M3, FORMAT §6.10/§6.14): name / text /
            # label ride along so EPS Save Image can match a multi-select
            # Prompt Notebook's entry to THIS run and pin only that entry.
            # null = that input is unwired (name/label) or absent (text).
            out["run_info"].append(
                json.dumps(
                    {
                        "format": 1,
                        "token": token,
                        "node": str(_unwrap_hidden(unique_id)) if unique_id is not None else None,
                        "run": len(out["run_info"]) + 1,
                        "total": total,
                        "steps": steps,
                        "pairs": len(pair_rows),
                        "name": _json_text(pair_name) if name_wired else None,
                        "text": _json_text(pair_text),
                        "label": (
                            _json_text(labels[_sweep_index(len(labels), a1_idx)])
                            if label_wired
                            else None
                        ),
                    }
                )
            )
            out["vae"].append(
                vaes[_sweep_index(len(vaes), v_idx)] if vae_wired else run_blocker
            )
        return tuple(out[k] for k in self.RETURN_NAMES)
