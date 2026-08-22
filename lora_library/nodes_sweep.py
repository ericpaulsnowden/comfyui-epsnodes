"""The ``LoraLibrarySweep`` ComfyUI node (FORMAT.md §6.8, display: "EPS LoRA
Sweep").

Sweeps a ``LORA_STACK``'s strengths across a min/max/increment range and
fans the patched ``model``/``clip`` out one run per step, so auditioning a
new lora (or several) is "wire it in, set a range, queue once" instead of
manually re-running a whole workflow by hand at every strength.

**ONE node, not a producer+applier pair** (``research/roadmap-eps-lora-
sweep.md`` "Architecture decision: ONE node", 2026-07-22): it reuses
:meth:`lora_library.nodes_sets.LoraLibraryApplySet._apply_stack` internally
-- the exact weight-patching path ``tests/test_nodes_sets_weight_math.py``
proved matches ComfyUI's own ``LoraLoader`` to zero floating-point diff --
rather than emitting swept stacks for a separate standalone applier node. A
standalone ``LORA_STACK`` applier would be a genuinely new, MIT-clean tool
(nothing on the owner's rig or in rgthree applies a raw stack today), but it
would only serve a use case the owner doesn't have (applying a stack
OUTSIDE sweeping) at the cost of one more node to wire and maintain -- see
the roadmap's full tradeoff writeup. Because this node's body still calls
the same reusable staticmethod, factoring a standalone applier back out
later, if ever wanted, stays trivial.

Like ``nodes_sets.py``, ``comfy.sd``/``comfy.utils`` are never imported at
module scope anywhere in this file -- they only enter (lazily, inside
``LoraLibraryApplySet._apply_stack`` itself, which this module calls but
never reimplements) at actual sweep-execution time. That keeps this whole
module -- including the node class -- importable in a plain test
environment with neither torch nor ComfyUI installed
(``tests/test_nodes_sweep.py``'s pure-logic half runs in exactly that
environment: this pack's own dev venv, per ``pyproject.toml``'s
``dependencies = []``).

All sweep LOGIC -- step math, the fencepost/precision rules, the two modes'
fan-out, labeling, the empty-stack guard -- lives in the pure, module-level
:func:`build_sweep_plan` (plus its private helpers :func:`_step_values`/
:func:`_decimal_places`), deliberately separated from the
:class:`LoraLibrarySweep` node class itself: plain tuples/lists/strings in,
plain tuples/lists/strings out, no ComfyUI object anywhere near it, so
every one of those decisions is unit-testable directly, with no node
instance, no context, and no faked ``comfy`` module required at all. The
class is then a thin ComfyUI adapter: build the plan, then apply each
planned stack through the proven ``_apply_stack`` staticmethod.
"""

from __future__ import annotations

import logging
from typing import Any

from . import nodes_sets
from .context import LibraryContext

logger = logging.getLogger("lora_library")

_context: LibraryContext | None = None


def set_context(context: LibraryContext | None) -> None:
    """Wire the shared :class:`LibraryContext` into this module.

    Called once from the pack's ``__init__.py`` (real runs, with the SAME
    context instance ``nodes_sets`` itself receives -- both modules read
    their own module-level ``_context`` global, but ``__init__.py``'s
    ``_NODE_SPECS`` loop wires every module that defines ``set_context`` to
    one shared :class:`LibraryContext`, so they always agree); tests call it
    directly against a fake context. Accepts ``None`` so tests can reset the
    module-level global between cases without leaking state (mirrors
    ``nodes_sets.set_context``/``nodes_notebook.set_context``).
    """
    global _context
    _context = context


#: The two ``mode`` COMBO values (FORMAT.md §6.8). Named constants, not
#: inline string literals, so the widget's option list (INPUT_TYPES) and the
#: fan-out branch below (build_sweep_plan) can never drift apart from a typo
#: in one copy of the string.
MODE_INDEPENDENT = "Each lora independently"
MODE_ALL_TOGETHER = "All together"

#: build_sweep_plan's guaranteed-non-empty sentinel (see its docstring's
#: "Empty-plan guard" section) -- a single passthrough entry standing in for
#: a plan that would otherwise contain literally zero (swept_stack, label)
#: pairs.
_EMPTY_PLAN_LABEL = "(no loras to sweep)"


def _decimal_places(value: float, cap: int = 6) -> int:
    """Decimal places in *value*'s own shortest ``repr()``, capped at *cap*.

    Drives the rounding in :func:`_step_values` so e.g. ``increment=0.1``
    yields steps like ``0.1``, ``0.2``, … rather than a float-noise sibling
    like ``0.30000000000000004``. This works because Python's ``repr()`` of
    a float is always the SHORTEST decimal string that round-trips back to
    that exact float (CPython's float repr since 3.1) -- so counting digits
    after its ``.`` recovers "how many decimal places did the user actually
    dial into the widget", not an artifact of binary-float storage.
    Scientific notation is a defensive fallback only (the node's own
    ``increment`` widget floors at 0.01 and ``min``/``max`` step at 0.05, so
    a repr like ``1e-05`` should never actually reach this from the UI) --
    it degrades to *cap* rather than attempting to parse an exponent.
    """
    text = repr(value)
    if "e" in text or "E" in text:
        return cap
    if "." not in text:
        return 0
    return min(len(text.split(".", 1)[1]), cap)


def _stem(file: str) -> str:
    """Basename of *file* with directory and extension stripped, tolerant of
    either path separator -- the same normalization
    :func:`nodes_sets._loras_text` uses, so a lora referenced by a set
    written on the other OS still labels cleanly (FORMAT.md §4).
    """
    return file.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]


def _sweep_label(name: str, value: float, ndigits: int) -> str:
    """One run's filename-safe label: ``<name>_<value>`` (owner ask
    2026-07-22 -- "name files by exactly the lora + strength under test", e.g.
    ``my_great_lora_0.5``).

    *value* is formatted to a CONSTANT number of decimals -- *ndigits*, the
    increment's own precision (:func:`_decimal_places`) -- so a whole sweep's
    filenames line up and sort naturally (``my_great_lora_0.0``,
    ``my_great_lora_0.5``, ``my_great_lora_1.0`` for a 0.1 sweep), never the
    ragged ``_0`` / ``_0.5`` / ``_1`` mix a ``%g``-style format gives at the
    round-number endpoints. Deliberately NOT
    :func:`nodes_sets._loras_text`: that lists the WHOLE stack -- every HELD
    lora too, space-joined -- which buries the one value actually being swept
    among constants; fine for Apply LoRA Set's static ``loras_text`` output,
    wrong for a per-run sweep filename.
    """
    return f"{name}_{value:.{ndigits}f}"


def _step_values(min_v: float, max_v: float, increment: float) -> list[float]:
    """The swept FLOAT values for one lora, both endpoints inclusive.

    ``n = round((max_v - min_v) / increment) + 1`` -- round-to-nearest step
    count, so a range that doesn't divide evenly by *increment* still lands
    on a sane step count instead of silently truncating; ``max_v`` is
    therefore a documented CEILING TARGET, not a hard clamp (``research/
    roadmap-eps-lora-sweep.md``: "max documented as a ceiling when the range
    doesn't divide evenly"). Each value is computed FRESH from its index
    (``min_v + i*increment``, then rounded to *increment*'s own decimal
    precision via :func:`_decimal_places`) rather than accumulated in a
    running total -- accumulating ``running += increment`` across many steps
    compounds binary-float error (the textbook ``0.1 + 0.1 + 0.1 != 0.3``
    pattern, verified during this feature's own research), whereas computing
    fresh from ``i`` bounds each value's raw error to a single multiply,
    which the trailing ``round()`` then wipes out completely for any
    sanely-dialed-in increment -- e.g. 0.0 -> 1.0 step 0.1 must land on the
    exact fencepost ``[0.0, 0.1, 0.2, …, 1.0]`` (11 values), never a
    ``0.30000000000000004``-style sibling.

    Degenerate inputs -- a non-positive *increment*, or ``max_v < min_v`` --
    are not errors: they collapse to a single-element sweep at *min_v* (a
    zero-width/zero-step "sweep" is still a valid, if trivial, one-run plan,
    matching a bare min==max: that case reaches the SAME single-value result
    through the normal formula below, since ``max_v >= min_v`` already holds
    when they're equal).
    """
    if increment <= 0 or max_v < min_v:
        return [min_v]
    ndigits = _decimal_places(increment)
    steps = max(1, round((max_v - min_v) / increment) + 1)
    return [round(min_v + i * increment, ndigits) for i in range(steps)]


def build_sweep_plan(
    lora_stack: list[tuple[str, float, float]],
    min_v: float,
    max_v: float,
    increment: float,
    mode: str,
) -> list[tuple[list[tuple[str, float, float]], str]]:
    """The PURE core of EPS LoRA Iterator (FORMAT.md §6.8): every
    ``(swept_stack, label)`` pair the node will apply, in emission order. No
    torch, no comfy, no I/O -- plain tuples/lists/strings in, plain
    tuples/lists/strings out -- so this is exhaustively unit-testable
    without a ComfyUI or torch install (``tests/test_nodes_sweep.py``'s
    pure-logic half).

    *lora_stack* is the same ``[(file, strength_model, strength_clip), …]``
    shape :meth:`nodes_sets.LoraLibraryApplySet._apply_stack` consumes
    (FORMAT.md §6.2) -- whatever ``LORA_STACK`` producer is wired in,
    "activated" is however many rows it already contains (a producer like
    Apply LoRA Set already excludes its disabled rows; this function never
    re-filters, and assumes every row is exactly a 3-tuple in that shape --
    a stack from some OTHER, non-Apply-LoRA-Set producer that emits a
    differently-shaped row is not defended against here, see the task
    report).

    **Mode "All together"**: one swept_stack per swept value, EVERY row's
    strength_model/strength_clip replaced by that value. ``n_steps`` entries
    total, regardless of how many (if any) loras are in *lora_stack*.

    **Mode "Each lora independently"** (default, and the fallback for any
    *mode* string that isn't exactly :data:`MODE_ALL_TOGETHER` -- a
    defensive default beats a crash on a stray combo value from a
    hand-edited workflow; ComfyUI's own static-COMBO validation already
    keeps a normal queue to exactly the two real values): for every row
    index, sweep THAT row across every value while every OTHER row holds
    its own configured strengths unchanged. ``n_loras * n_steps`` entries
    total. A row whose stored ``strength_model``/``strength_clip`` differ is
    swept on BOTH sides to the same value -- the locked default (roadmap:
    "sm != sc rows: sweep both to v (default) vs hold sc -- default
    sweep-both, revisit on feedback") -- one knob per lora, one mental
    model, matching what the label then shows.

    **Label** (per entry, :func:`_sweep_label`): ``<lora>_<value>`` naming
    ONLY the lora being swept and its value, at the increment's decimal
    precision -- e.g. ``my_great_lora_0.5``. In "Each lora independently"
    that's the swept row's stem; in "All together" it's the lone lora's stem
    (1-lora stack) or ``all`` (≥2). NOT the whole-stack
    :func:`nodes_sets._loras_text` dump: for a per-run filename the held
    loras are constant noise, so the label is just the lora + strength under
    test (owner ask 2026-07-22).

    **Empty-plan guard**: the only way this loop produces literally ZERO
    entries is "Each lora independently" over an EMPTY *lora_stack*
    (``for index in range(0)`` never iterates) -- "All together" always
    emits at least one entry even for an empty stack, since
    :func:`_step_values` never returns fewer than one value. So this guard
    exists purely for that one independent-mode/empty-stack combination. An
    empty OUTER list here is not merely uninteresting: it actively crashes
    ComfyUI's own list fan-out (``execution.py``'s ``slice_dict`` indexes
    the LAST element of each list input; an empty list has none) the moment
    this node's output feeds any downstream node that also has an ordinary,
    non-list input -- the exact lesson already learned and documented for
    ``EPSSwitcher``'s all-off case and ``EPSImageGrid``'s empty buffer
    (FORMAT.md §6.4/§6.6). THERE the fix is an ``ExecutionBlocker``
    (deliberately skip the downstream branch) -- HERE a base-model
    passthrough is more useful than a block, since nothing about the inputs
    is actually wrong, there's just nothing to sweep -- so the guard instead
    returns one sentinel passthrough entry (empty swept_stack, a
    human-readable label) rather than either an empty list or a block.
    """
    values = _step_values(min_v, max_v, increment)
    ndigits = _decimal_places(increment)

    plan: list[tuple[list[tuple[str, float, float]], str]] = []
    if mode == MODE_ALL_TOGETHER:
        # Every lora moves to the same value, so no single lora is "the" one
        # being swept: label by the shared value under a name that says so --
        # the lone lora's stem when there's exactly one (so a 1-lora sweep
        # reads identically in either mode), else "all" (e.g. all_0.5). An
        # empty stack falls to "all" too (len != 1), never indexing [0].
        name = _stem(lora_stack[0][0]) if len(lora_stack) == 1 else "all"
        for value in values:
            swept_stack = [(file, value, value) for file, _sm, _sc in lora_stack]
            plan.append((swept_stack, _sweep_label(name, value, ndigits)))
    else:
        for index in range(len(lora_stack)):
            # The label names ONLY the one lora this block sweeps, at each
            # value -- the held loras are constant across the whole sweep, so
            # leaving them out of the per-run filename is the point (owner ask
            # 2026-07-22: the filename should be the lora + strength under
            # test, e.g. my_great_lora_0.5, not the whole stack space-joined).
            name = _stem(lora_stack[index][0])
            for value in values:
                swept_stack = [
                    (file, value, value) if row_index == index else (file, sm, sc)
                    for row_index, (file, sm, sc) in enumerate(lora_stack)
                ]
                plan.append((swept_stack, _sweep_label(name, value, ndigits)))

    if not plan:
        return [([], _EMPTY_PLAN_LABEL)]
    return plan


class LoraLibrarySweep:
    """Sweeps a ``LORA_STACK``'s strengths across a min/max/increment range,
    applying each step to ``model``/``clip`` and fanning the results out as
    three parallel ``OUTPUT_IS_LIST`` lists (FORMAT.md §6.8, display: "EPS
    LoRA Iterator").

    All the actual sweep-plan logic lives in the pure, torch-free
    :func:`build_sweep_plan` above; this class is a thin ComfyUI adapter:
    build the plan, then apply each planned stack via the proven
    ``_apply_stack`` staticmethod (never reimplemented here -- see the
    module docstring). ``_apply_stack`` clones a fresh patcher per call, so
    the N patched models/clips this node produces are fully independent of
    each other and of the original ``model``/``clip`` inputs; it does so
    CHEAPLY, since a ``ModelPatcher`` is a patch-LIST over a shared base
    tensor set, not a weight copy -- actual weight materialization stays
    lazy until ``patch_model()`` runs at sample time. Producing N patched
    models up front is not N times the VRAM.

    No ``IS_CHANGED`` override: unlike ``LoraLibraryApplySet``, this node
    reads no file off disk -- its ``lora_stack`` is an ordinary hashed
    INPUT (whatever upstream node produced it is what re-executes on a file
    change, not this node), already covered by ComfyUI's own input-hash
    caching of every required input plus the three FLOAT/COMBO widgets. No
    ``VALIDATE_INPUTS`` either: every widget here is a plain, statically-
    bounded FLOAT/COMBO, never a dynamic list of names (like Apply LoRA
    Set's ``set`` combo) that would need one.

    Caching caveat worth knowing: because caching is keyed on the whole
    node, not per swept step, changing ANY widget (even just ``mode``)
    re-renders the WHOLE sweep on the next queue -- there's no such thing
    as "only the newly-added steps re-run".
    """

    CATEGORY = "EPSNodes/LoRA"
    RETURN_TYPES = ("MODEL", "CLIP", "STRING")
    RETURN_NAMES = ("model", "clip", "label")
    OUTPUT_IS_LIST = (True, True, True)
    OUTPUT_TOOLTIPS = (
        "The patched model for this step -- one per swept value, as a list.",
        "The patched CLIP for this step, or None where clip was left "
        "unwired -- one per swept value, as a list.",
        "This step's lora and strength as a filename-safe string, e.g. "
        "my_lora_0.5 -- wire it into Save Image's filename_prefix.",
    )
    FUNCTION = "sweep"
    DESCRIPTION = (
        "Auditions a LORA_STACK's strengths without manually re-running the "
        "workflow at every value: set a min, max, and increment, and get "
        "back one patched model per step. Both ends are included -- 0.0 to "
        "1.0 at 0.1 is 11 runs, not 10. 'Each lora independently' (default) "
        "sweeps one lora at a time while the rest hold their own saved "
        "strengths; 'All together' moves every lora to the same value at "
        "once, in fewer total runs. CLIP is optional -- leave it unwired to "
        "patch the model only, for models with no text encoder. Every run "
        "shares this queue's seed, so a fixed seed repeats identically "
        "across every strength for a clean comparison; wire the label "
        "output into Save Image's filename_prefix so each saved file says "
        "which lora and strength produced it."
    )

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "model": (
                    "MODEL",
                    {"tooltip": "The model to patch at every swept strength."},
                ),
                "lora_stack": (
                    "LORA_STACK",
                    {
                        "tooltip": (
                            "The loras to sweep, in order -- usually wired "
                            "from EPS Apply LoRA Set's lora_stack output."
                        ),
                    },
                ),
                "min": (
                    "FLOAT",
                    {
                        "default": 0.0,
                        "min": -10.0,
                        "max": 10.0,
                        "step": 0.05,
                        "tooltip": (
                            "Lowest strength to test, inclusive. Not "
                            "clamped to 0-1, so testing weaker-than-normal "
                            "or negative strengths is fine."
                        ),
                    },
                ),
                "max": (
                    "FLOAT",
                    {
                        "default": 1.0,
                        "min": -10.0,
                        "max": 10.0,
                        "step": 0.05,
                        "tooltip": (
                            "Highest strength to test, inclusive. Not "
                            "clamped to 0-1, so testing beyond full "
                            "strength is fine."
                        ),
                    },
                ),
                "increment": (
                    "FLOAT",
                    {
                        "default": 0.1,
                        "min": 0.01,
                        "max": 10.0,
                        "step": 0.01,
                        "tooltip": (
                            "Step size between strengths. A smaller "
                            "increment means more runs; if the min-to-max "
                            "range doesn't divide evenly by it, the step "
                            "count rounds to the nearest whole step."
                        ),
                    },
                ),
                "mode": (
                    [MODE_INDEPENDENT, MODE_ALL_TOGETHER],
                    {
                        "default": MODE_INDEPENDENT,
                        "tooltip": (
                            "Each lora independently sweeps one lora at a "
                            "time while the others hold their saved "
                            "strengths. All together moves every lora to "
                            "the same value at once, in fewer total runs."
                        ),
                    },
                ),
            },
            "optional": {
                # CLIP is OPTIONAL (owner ask 2026-07-22): plenty of models
                # neither require nor ship a text-encoder CLIP, and a LoRA's
                # MODEL-side weights sweep perfectly well without one. When it's
                # unwired, `_apply_stack` -> `load_lora_for_models` patches the
                # MODEL only (exactly core's `LoraLoaderModelOnly`; the LoRA's
                # clip-side weights, if any, are simply skipped) and the `clip`
                # OUTPUT passes through `None`. `model` stays REQUIRED -- a
                # strength tester with no model to patch has nothing to sweep.
                "clip": (
                    "CLIP",
                    {
                        "tooltip": (
                            "The CLIP to patch at every swept strength. "
                            "Leave unwired to patch the model only -- "
                            "useful for models with no text encoder."
                        ),
                    },
                ),
            },
        }

    def sweep(
        self,
        model: Any,
        lora_stack: list[tuple[str, float, float]],
        min: float,
        max: float,
        increment: float,
        mode: str,
        clip: Any = None,
    ) -> tuple[list[Any], list[Any], list[str]]:
        context = _context
        if context is None:
            # Mirrors nodes_sets.LoraLibraryApplySet.apply's exact posture
            # for a not-yet-wired context (e.g. this node probed by tooling
            # before __init__.py's set_context loop has run): a single
            # passthrough, not a crash and not an attempt to build/apply
            # anything. Wrapped in one-element lists -- every output here is
            # OUTPUT_IS_LIST, so a bare (model, clip, label) tuple would
            # break every downstream node expecting a list to fan over.
            logger.warning("EPSNodes: EPS LoRA Iterator has no context configured; passthrough")
            return [model], [clip], ["(no context configured)"]

        plan = build_sweep_plan(lora_stack, min, max, increment, mode)

        models: list[Any] = []
        clips: list[Any] = []
        labels: list[str] = []
        lora_cache: dict[str, Any] = {}  # one load per file per sweep() (audit 2026-08-21)
        for swept_stack, label in plan:
            # The proven weight-patching path (test_nodes_sets_weight_math.py):
            # reused verbatim, never reimplemented here. An empty swept_stack
            # (build_sweep_plan's own sentinel, or a genuinely empty row in
            # "All together" mode) short-circuits inside _apply_stack itself
            # (`if not stack: return model, clip`) -- correct base-model
            # passthrough for that one step, no special-casing needed here.
            patched_model, patched_clip = nodes_sets.LoraLibraryApplySet._apply_stack(
                context, model, clip, swept_stack, lora_cache=lora_cache
            )
            models.append(patched_model)
            clips.append(patched_clip)
            labels.append(label)
        return models, clips, labels
