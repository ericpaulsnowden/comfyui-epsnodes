"""Tests for lora_library.nodes_sweep (FORMAT.md §6.8, `LoraLibrarySweep`).

Two deliberately separated halves, mirroring the split the task/roadmap
calls for:

1. Everything above ``# ---- torch-gated`` -- pure ``build_sweep_plan``
   logic, ``LoraLibrarySweep`` class shape, and the no-context passthrough
   -- needs neither torch nor comfy and MUST pass in this pack's own dev
   venv (``pyproject.toml``'s ``dependencies = []``; no ``import torch``/
   ``import comfy`` anywhere in ``lora_library/nodes_sweep.py`` at module
   scope, mirrored here: no such import anywhere in THIS file at module
   scope either).
2. The ONE weight-equivalence test at the bottom, which needs both. Its
   ``pytest.importorskip``/``EPS_COMFYUI_ROOT`` dance -- copied faithfully
   from ``test_nodes_sets_weight_math.py``'s own module-level preamble --
   runs INSIDE that one test function instead of at this file's module
   scope. That relocation is the whole trick: in
   ``test_nodes_sets_weight_math.py`` the dance sits at true module level,
   so a torchless run skips that ENTIRE separate file (a clean, correct
   outcome for a module that has nothing else in it). Here it would be the
   WRONG outcome -- it would skip every pure test above it too, and the
   task's own instruction is that those must actually PASS in a torchless
   venv, not skip along for the ride. Scoping the dance to a single test
   function means only that one test's collection/skip decision is
   deferred to its own execution, so pytest still collects and runs every
   pure test in this file regardless of whether torch is installed.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import pytest

from lora_library import nodes_sets, nodes_sweep
from lora_library.context import LibraryContext
from lora_library.nodes_sweep import (
    MODE_ALL_TOGETHER,
    MODE_INDEPENDENT,
    LoraLibrarySweep,
    build_sweep_plan,
)


@pytest.fixture(autouse=True)
def _clean_sweep_context():
    """Every test starts and ends with ``nodes_sweep._context`` reset to
    ``None`` -- most tests in this file never touch it at all (they call
    ``build_sweep_plan`` directly, a free function with no context
    dependency), but this guarantees test-order independence for the
    handful that do wire it (mirrors ``test_nodes_sets.py``'s autouse
    ``_wire_context`` fixture, minus the sets_store setup this module
    doesn't need)."""
    nodes_sweep.set_context(None)
    yield
    nodes_sweep.set_context(None)


def _stack(*rows: tuple[str, float, float]) -> list[tuple[str, float, float]]:
    """Shorthand for a LORA_STACK literal -- ``_stack(("a.safetensors", 0.5, 0.5))``."""
    return list(rows)


# --------------------------------------------------------- _decimal_places


class TestDecimalPlaces:
    def test_one_decimal(self) -> None:
        assert nodes_sweep._decimal_places(0.1) == 1

    def test_two_decimals(self) -> None:
        assert nodes_sweep._decimal_places(0.05) == 2
        assert nodes_sweep._decimal_places(0.25) == 2

    def test_whole_number_reprs_with_one_trailing_zero_decimal(self) -> None:
        # Python's float repr always carries at least one digit after the
        # dot ("1.0", "10.0", never "1"/"10"), so a whole-number increment
        # is 1 decimal place, not 0 -- harmless either way (round(x, 1) on
        # an already-whole value is a no-op), but the actual behavior is
        # asserted here rather than an assumption.
        assert nodes_sweep._decimal_places(1.0) == 1
        assert nodes_sweep._decimal_places(10.0) == 1

    def test_capped_at_six(self) -> None:
        assert nodes_sweep._decimal_places(0.1234567) == 6

    def test_scientific_notation_falls_back_to_cap(self) -> None:
        # Not reachable from the node's own widget bounds (increment floors
        # at 0.01), but _step_values must not crash if it ever were.
        assert nodes_sweep._decimal_places(1e-05) == 6


# ------------------------------------------------------------- _step_values


class TestStepValuesFencepostAndPrecision:
    def test_fencepost_zero_to_one_by_tenth_is_eleven_exact_values(self) -> None:
        values = nodes_sweep._step_values(0.0, 1.0, 0.1)
        assert values == [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
        assert len(values) == 11

    def test_fencepost_i_equals_three_has_no_float_dirt(self) -> None:
        # The textbook case this whole rounding rule exists to prevent:
        # naive (unrounded) 3 * 0.1 is 0.30000000000000004, not 0.3.
        values = nodes_sweep._step_values(0.0, 1.0, 0.1)
        assert values[3] == 0.3
        assert repr(values[3]) == "0.3"

    def test_no_value_in_the_fencepost_sweep_has_float_dirt(self) -> None:
        values = nodes_sweep._step_values(0.0, 1.0, 0.1)
        for value in values:
            # Every clean one-decimal value reprs in 3 characters ("0.0" ..
            # "1.0"); any float-dirt sibling would repr much longer.
            assert len(repr(value)) <= 3, repr(value)

    def test_increment_0_05_precision(self) -> None:
        values = nodes_sweep._step_values(0.0, 1.0, 0.05)
        assert len(values) == 21
        assert values[0] == 0.0
        assert values[1] == 0.05
        assert values[-1] == 1.0
        assert all(len(repr(v)) <= 4 for v in values)  # "0.05" is the longest

    def test_increment_0_25_precision(self) -> None:
        values = nodes_sweep._step_values(0.0, 1.0, 0.25)
        assert values == [0.0, 0.25, 0.5, 0.75, 1.0]

    def test_non_zero_min_offset(self) -> None:
        values = nodes_sweep._step_values(0.5, 1.0, 0.1)
        assert values == [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]


class TestStepValuesDegenerateCases:
    @pytest.mark.parametrize("bad_increment", [0.0, -0.1, -5.0])
    def test_non_positive_increment_collapses_to_single_min_value(
        self, bad_increment: float
    ) -> None:
        assert nodes_sweep._step_values(0.0, 1.0, bad_increment) == [0.0]

    def test_max_less_than_min_collapses_to_single_min_value(self) -> None:
        assert nodes_sweep._step_values(1.0, 0.0, 0.1) == [1.0]

    def test_min_equals_max_is_a_single_step(self) -> None:
        assert nodes_sweep._step_values(0.5, 0.5, 0.1) == [0.5]

    def test_step_values_never_returns_an_empty_list(self) -> None:
        # Load-bearing invariant for build_sweep_plan's "All together" mode
        # always emitting >=1 entry, degenerate inputs included.
        cases = [(0.0, 1.0, 0.1), (0.0, 1.0, 0.0), (1.0, 0.0, 0.1), (0.5, 0.5, 0.1)]
        for min_v, max_v, increment in cases:
            assert len(nodes_sweep._step_values(min_v, max_v, increment)) >= 1


# --------------------------------------------------------- build_sweep_plan


class TestBuildSweepPlanAllTogetherMode:
    def test_count_equals_n_steps_regardless_of_lora_count(self) -> None:
        stack = _stack(("a.safetensors", 0.5, 0.5), ("b.safetensors", 0.8, 0.3))
        plan = build_sweep_plan(stack, 0.0, 1.0, 0.5, MODE_ALL_TOGETHER)
        assert len(plan) == 3  # n_steps = 3 (0.0, 0.5, 1.0); n_loras is irrelevant

    def test_every_row_in_every_step_gets_the_shared_value(self) -> None:
        stack = _stack(("a.safetensors", 0.5, 0.5), ("b.safetensors", 0.8, 0.3))
        plan = build_sweep_plan(stack, 0.0, 1.0, 0.5, MODE_ALL_TOGETHER)
        for value, (swept_stack, _label) in zip([0.0, 0.5, 1.0], plan, strict=True):
            assert swept_stack == [("a.safetensors", value, value), ("b.safetensors", value, value)]

    def test_single_lora_stack(self) -> None:
        stack = _stack(("a.safetensors", 0.5, 0.5))
        plan = build_sweep_plan(stack, 0.0, 1.0, 0.25, MODE_ALL_TOGETHER)
        assert len(plan) == 5
        assert [s for s, _label in plan] == [
            [("a.safetensors", 0.0, 0.0)],
            [("a.safetensors", 0.25, 0.25)],
            [("a.safetensors", 0.5, 0.5)],
            [("a.safetensors", 0.75, 0.75)],
            [("a.safetensors", 1.0, 1.0)],
        ]

    def test_empty_stack_still_produces_n_steps_entries_of_empty_lists(self) -> None:
        # Distinguishes this from the EMPTY-PLAN guard below: "All together"
        # always has n_steps >= 1 values to iterate, so it never hits the
        # zero-entries case even with nothing to sweep -- it just emits
        # n_steps passthrough-shaped (empty swept_stack) entries instead.
        plan = build_sweep_plan([], 0.0, 1.0, 0.5, MODE_ALL_TOGETHER)
        assert len(plan) == 3
        assert all(swept_stack == [] for swept_stack, _label in plan)
        assert all(label == "" for _swept_stack, label in plan)  # _loras_text([]) == ""


class TestBuildSweepPlanIndependentMode:
    def test_count_equals_n_loras_times_n_steps(self) -> None:
        stack = _stack(("a.safetensors", 0.5, 0.5), ("b.safetensors", 0.8, 0.3))
        plan = build_sweep_plan(stack, 0.0, 1.0, 0.5, MODE_INDEPENDENT)
        assert len(plan) == 6  # 2 loras * 3 steps

    def test_default_mode_string_is_independent(self) -> None:
        # INPUT_TYPES' declared default value must actually mean this mode.
        stack = _stack(("a.safetensors", 0.5, 0.5), ("b.safetensors", 0.8, 0.3))
        default_mode = LoraLibrarySweep.INPUT_TYPES()["required"]["mode"][1]["default"]
        assert build_sweep_plan(stack, 0.0, 1.0, 0.5, default_mode) == build_sweep_plan(
            stack, 0.0, 1.0, 0.5, MODE_INDEPENDENT
        )

    def test_sweeps_exactly_one_row_holding_all_others_unchanged(self) -> None:
        stack = _stack(
            ("a.safetensors", 0.5, 0.5),
            ("b.safetensors", 0.8, 0.3),
            ("c.safetensors", 1.0, 1.0),
        )
        plan = build_sweep_plan(stack, 0.0, 1.0, 0.5, MODE_INDEPENDENT)
        swept_stacks = [swept for swept, _label in plan]

        # Row 0 ("a") swept across all 3 values; b/c hold their own rows.
        assert swept_stacks[0:3] == [
            [("a.safetensors", 0.0, 0.0), ("b.safetensors", 0.8, 0.3), ("c.safetensors", 1.0, 1.0)],
            [("a.safetensors", 0.5, 0.5), ("b.safetensors", 0.8, 0.3), ("c.safetensors", 1.0, 1.0)],
            [("a.safetensors", 1.0, 1.0), ("b.safetensors", 0.8, 0.3), ("c.safetensors", 1.0, 1.0)],
        ]
        # Row 1 ("b") swept; a/c hold.
        assert swept_stacks[3:6] == [
            [("a.safetensors", 0.5, 0.5), ("b.safetensors", 0.0, 0.0), ("c.safetensors", 1.0, 1.0)],
            [("a.safetensors", 0.5, 0.5), ("b.safetensors", 0.5, 0.5), ("c.safetensors", 1.0, 1.0)],
            [("a.safetensors", 0.5, 0.5), ("b.safetensors", 1.0, 1.0), ("c.safetensors", 1.0, 1.0)],
        ]

    def test_sm_ne_sc_row_is_swept_on_both_sides_to_the_same_value(self) -> None:
        # Locked default (roadmap): a row whose stored strengths differ is
        # swept to (v, v) on BOTH sides, not just the model side.
        stack = _stack(("a.safetensors", 0.8, 0.3))
        plan = build_sweep_plan(stack, 0.6, 0.6, 0.1, MODE_INDEPENDENT)
        assert plan == [([("a.safetensors", 0.6, 0.6)], "a_0.6")]

    def test_held_rows_are_the_exact_original_tuples(self) -> None:
        stack = _stack(("a.safetensors", 0.5, 0.5), ("b.safetensors", 0.8, 0.3))
        plan = build_sweep_plan(stack, 0.0, 0.0, 0.1, MODE_INDEPENDENT)
        # Sweeping row 0 at a single value: row 1 ("b") must equal the
        # ORIGINAL tuple exactly, not a reconstructed/rounded lookalike.
        _swept_a, _label_a = plan[0]
        assert plan[0][0][1] == stack[1]
        _swept_b, _label_b = plan[1]
        assert plan[1][0][0] == stack[0]


class TestBuildSweepPlanModeFallback:
    def test_unrecognized_mode_string_falls_back_to_independent_count(self) -> None:
        stack = _stack(("a.safetensors", 0.5, 0.5), ("b.safetensors", 0.8, 0.3))
        plan = build_sweep_plan(stack, 0.0, 1.0, 0.5, "some garbage the combo never offers")
        assert len(plan) == 6  # matches MODE_INDEPENDENT's n_loras * n_steps

    def test_empty_mode_string_falls_back_to_independent(self) -> None:
        stack = _stack(("a.safetensors", 0.5, 0.5))
        plan = build_sweep_plan(stack, 0.0, 1.0, 0.5, "")
        assert len(plan) == 3  # 1 lora * 3 steps


# ----------------------------------------------- build_sweep_plan: labels


class TestBuildSweepPlanLabels:
    def test_every_label_matches_loras_text_of_its_own_swept_stack(self) -> None:
        # The task's own definition of label correctness: not a hardcoded
        # string, but agreement with the shared _loras_text helper.
        stack = _stack(("a.safetensors", 0.5, 0.5), ("styles/b.safetensors", 0.8, 0.3))
        for mode in (MODE_INDEPENDENT, MODE_ALL_TOGETHER):
            plan = build_sweep_plan(stack, 0.0, 1.0, 0.5, mode)
            for swept_stack, label in plan:
                assert label == nodes_sets._loras_text(swept_stack)

    def test_label_is_filename_safe_and_self_identifying(self) -> None:
        stack = _stack(("styles/detailer.safetensors", 0.8, 0.3))
        plan = build_sweep_plan(stack, 0.6, 0.6, 0.1, MODE_ALL_TOGETHER)
        [(_swept_stack, label)] = plan
        assert label == "detailer_0.6"  # basename only, dir stripped, dual->single (sm==sc==v)
        assert "<" not in label and ":" not in label and "\\" not in label


# ---------------------------------------------- build_sweep_plan: empty guard


class TestBuildSweepPlanEmptyStackGuard:
    def test_empty_stack_independent_mode_returns_single_sentinel(self) -> None:
        plan = build_sweep_plan([], 0.0, 1.0, 0.1, MODE_INDEPENDENT)
        assert plan == [([], "(no loras to sweep)")]

    def test_sentinel_still_holds_under_degenerate_step_math(self) -> None:
        # Belt-and-suspenders: the guard must fire from "0 loras", not from
        # some accidental interaction with a degenerate min/max/increment.
        plan = build_sweep_plan([], 5.0, -5.0, -1.0, MODE_INDEPENDENT)
        assert plan == [([], "(no loras to sweep)")]

    def test_the_outer_plan_is_never_literally_empty(self) -> None:
        # The actual crash this guard prevents (ComfyUI's slice_dict on an
        # empty list input) -- assert the invariant directly, across a
        # matrix that includes the one known zero-entries combination.
        stack_variants = [[], _stack(("a.safetensors", 0.5, 0.5))]
        modes = [MODE_INDEPENDENT, MODE_ALL_TOGETHER]
        increments = [0.1, 0.0, -1.0]
        for stack in stack_variants:
            for mode in modes:
                for increment in increments:
                    plan = build_sweep_plan(stack, 0.0, 1.0, increment, mode)
                    assert len(plan) >= 1


def test_build_sweep_plan_never_mutates_its_lora_stack_argument() -> None:
    stack = _stack(("a.safetensors", 0.5, 0.5), ("b.safetensors", 0.8, 0.3))
    original = list(stack)
    build_sweep_plan(stack, 0.0, 1.0, 0.5, MODE_INDEPENDENT)
    build_sweep_plan(stack, 0.0, 1.0, 0.5, MODE_ALL_TOGETHER)
    assert stack == original


# ------------------------------------------------ LoraLibrarySweep: class shape


class TestClassShapeMatchesFormatMdSection6_8:
    def test_category(self) -> None:
        assert LoraLibrarySweep.CATEGORY == "EPSNodes"

    def test_return_types_and_names(self) -> None:
        assert LoraLibrarySweep.RETURN_TYPES == ("MODEL", "CLIP", "STRING")
        assert LoraLibrarySweep.RETURN_NAMES == ("model", "clip", "label")

    def test_output_is_list_flags_all_three_outputs(self) -> None:
        assert LoraLibrarySweep.OUTPUT_IS_LIST == (True, True, True)

    def test_function_name_matches_the_declared_entry_point(self) -> None:
        assert LoraLibrarySweep.FUNCTION == "sweep"
        assert callable(getattr(LoraLibrarySweep(), LoraLibrarySweep.FUNCTION))

    def test_no_is_changed_override(self) -> None:
        # FORMAT.md §6.8: lora_stack is an ordinary hashed input (not a file
        # re-read like Apply LoRA Set's `set`), so default caching is
        # already correct -- this node must NOT define its own IS_CHANGED.
        assert "IS_CHANGED" not in LoraLibrarySweep.__dict__

    def test_no_validate_inputs_override(self) -> None:
        # Every widget is a plain bounded FLOAT/COMBO, never a dynamic
        # set-of-names list -- no VALIDATE_INPUTS escape hatch needed.
        assert "VALIDATE_INPUTS" not in LoraLibrarySweep.__dict__

    def test_required_inputs_are_model_lora_stack_and_the_four_widgets(self) -> None:
        required = LoraLibrarySweep.INPUT_TYPES()["required"]
        assert set(required.keys()) == {
            "model",
            "lora_stack",
            "min",
            "max",
            "increment",
            "mode",
        }
        # `model` stays REQUIRED (a lora *tester* needs a model to patch -- no
        # pure-stack-source mode). `clip` is OPTIONAL (owner ask 2026-07-22):
        # many models have no text encoder, and load_lora_for_models patches
        # the model only when clip is None (like core's LoraLoaderModelOnly).
        assert required["model"] == ("MODEL",)
        assert required["lora_stack"] == ("LORA_STACK",)
        assert "clip" not in required

    def test_clip_is_optional_not_required(self) -> None:
        types = LoraLibrarySweep.INPUT_TYPES()
        assert "clip" not in types["required"]
        assert types.get("optional", {}).get("clip") == ("CLIP",)

    def test_min_max_increment_widget_specs(self) -> None:
        required = LoraLibrarySweep.INPUT_TYPES()["required"]
        min_type, min_spec = required["min"]
        max_type, max_spec = required["max"]
        inc_type, inc_spec = required["increment"]
        assert (min_type, max_type, inc_type) == ("FLOAT", "FLOAT", "FLOAT")
        assert min_spec == {"default": 0.0, "min": -10.0, "max": 10.0, "step": 0.05}
        assert max_spec == {"default": 1.0, "min": -10.0, "max": 10.0, "step": 0.05}
        assert inc_spec == {"default": 0.1, "min": 0.01, "max": 10.0, "step": 0.01}

    def test_mode_combo_options_and_default(self) -> None:
        options, spec = LoraLibrarySweep.INPUT_TYPES()["required"]["mode"]
        assert options == [MODE_INDEPENDENT, MODE_ALL_TOGETHER]
        assert spec == {"default": MODE_INDEPENDENT}

    def test_description_documents_the_locked_caveats(self) -> None:
        # FORMAT.md §6.8 / task instruction: the DESCRIPTION must state
        # inclusive endpoints, the run-count formula, seed-repeat, whole-
        # sweep recache, and unclamped values -- check each is present
        # rather than asserting exact wording (wording may evolve).
        text = LoraLibrarySweep.DESCRIPTION.lower()
        assert "11 runs" in text  # inclusive-endpoints fencepost callout
        assert "n_loras" in text and "n_steps" in text  # run-count formula
        assert "seed" in text and "repeat" in text  # seed-repeat caveat
        assert "whole sweep" in text  # all-or-nothing node caching
        assert "unclamped" in text  # values apply unclamped


def test_module_never_imports_comfy_or_torch_at_module_scope() -> None:
    import inspect

    assert "comfy" not in nodes_sweep.__dict__
    assert "torch" not in nodes_sweep.__dict__
    source = inspect.getsource(sys.modules[nodes_sweep.__name__])
    assert "import comfy" not in source
    assert "import torch" not in source


# --------------------------------------------- LoraLibrarySweep: no-context


class TestSweepWithNoContextConfigured:
    """Mirrors nodes_sets.LoraLibraryApplySet.apply's own no-context
    posture -- a single passthrough, never a crash. This path never calls
    build_sweep_plan or _apply_stack, so it needs no torch/comfy either
    (nodes_sweep.set_context(None) is this file's default state via the
    autouse fixture above)."""

    def test_returns_one_element_passthrough_lists(self) -> None:
        node = LoraLibrarySweep()
        model_sentinel = object()
        clip_sentinel = object()
        models, clips, labels = node.sweep(
            model=model_sentinel,
            clip=clip_sentinel,
            lora_stack=_stack(("a.safetensors", 0.5, 0.5)),
            min=0.0,
            max=1.0,
            increment=0.1,
            mode=MODE_INDEPENDENT,
        )
        assert models == [model_sentinel]
        assert clips == [clip_sentinel]
        assert labels == ["(no context configured)"]

    def test_clip_omitted_defaults_to_none(self) -> None:
        # An unwired optional CLIP input means ComfyUI never passes `clip`, so
        # the signature default (None) must apply and the clip output be [None]
        # -- the owner's "clip should be optional" case, exercised without
        # torch via the no-context passthrough.
        node = LoraLibrarySweep()
        model_sentinel = object()
        models, clips, labels = node.sweep(
            model=model_sentinel,
            lora_stack=_stack(("a.safetensors", 0.5, 0.5)),
            min=0.0,
            max=1.0,
            increment=0.1,
            mode=MODE_INDEPENDENT,
        )
        assert models == [model_sentinel]
        assert clips == [None]
        assert labels == ["(no context configured)"]

    def test_logs_a_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        node = LoraLibrarySweep()
        with caplog.at_level(logging.WARNING, logger="lora_library"):
            node.sweep(
                model=None,
                clip=None,
                lora_stack=[],
                min=0.0,
                max=1.0,
                increment=0.1,
                mode=MODE_INDEPENDENT,
            )
        assert any("no context configured" in record.message for record in caplog.records)


# ---- torch-gated: the ONE weight-equivalence test (guards itself; see the
# ---- module docstring for why this dance lives inside the test function
# ---- rather than at module scope like test_nodes_sets_weight_math.py's).


def test_sweep_step_patches_weights_identically_to_direct_apply_stack(
    context: LibraryContext, tmp_path: Path
) -> None:
    """The one torch-gated proof this module carries. The underlying LoRA
    weight MATH is already exhaustively, permanently proven correct by
    ``test_nodes_sets_weight_math.py`` (kohya up/down/alpha, both MODEL and
    CLIP sides, stacked rows, a disabled row, strength_scale) -- this test's
    only job is to show the SWEEP path doesn't perturb that math, it only
    chooses which strengths to feed it. Small on purpose (task instruction):
    ONE lora, MODEL side only (clip=None throughout -- ``comfy.sd.
    load_lora_for_models`` verified to tolerate a None clip, only patching
    the side that's actually wired), a single swept step (min==max so
    ``build_sweep_plan`` emits exactly one value).

    Drives ONE swept step through the real ``LoraLibrarySweep.sweep()`` and,
    independently, the exact same effective stack straight through
    ``LoraLibraryApplySet._apply_stack`` on a freshly-seeded, IDENTICAL base
    model -- then asserts the two resulting patched weight tensors are
    bit-for-bit IDENTICAL (``rtol=0, atol=0``): both call paths run the
    literal same ``_apply_stack`` code with the literal same numeric inputs,
    so anything short of an exact match would mean the sweep path is
    somehow transforming the stack before applying it.
    """
    pytest.importorskip("torch")
    pytest.importorskip("safetensors")

    try:
        import comfy.sd
    except ImportError:
        eps_comfyui_root = os.environ.get("EPS_COMFYUI_ROOT")
        if not eps_comfyui_root:
            pytest.skip(
                "comfy is not importable and EPS_COMFYUI_ROOT is not set -- this "
                "test needs a real ComfyUI checkout to verify actual patched LoRA "
                "weights (mocks would prove nothing here). Set "
                "EPS_COMFYUI_ROOT=/path/to/ComfyUI (the directory containing "
                "comfy/, folder_paths.py, etc.) and re-run, or run from an "
                "environment where `import comfy` already works -- see "
                "test_nodes_sets_weight_math.py's module docstring."
            )
        path_already_present = eps_comfyui_root in sys.path
        if not path_already_present:
            sys.path.insert(0, eps_comfyui_root)
        try:
            import comfy.sd
        except ImportError as exc:
            pytest.skip(
                f"EPS_COMFYUI_ROOT={eps_comfyui_root!r} is set but `import "
                f"comfy.sd` still fails ({exc}) -- check the path points at a "
                "ComfyUI checkout's root (the directory containing comfy/, not "
                "comfy/ itself)."
            )
        finally:
            if not path_already_present and eps_comfyui_root in sys.path:
                sys.path.remove(eps_comfyui_root)

    import comfy.model_patcher
    import torch
    from safetensors.torch import save_file

    out_features, in_features, rank, alpha = 4, 3, 2, 1.0

    def seeded(*shape: int, seed: int) -> torch.Tensor:
        return torch.randn(*shape, generator=torch.Generator().manual_seed(seed))

    class _FakeProj(torch.nn.Module):
        """A single ``Linear(in=3, out=4, bias=False)`` at ``.proj`` -- the
        submodule NESTING matters: it's what makes the real state-dict key
        ``diffusion_model.proj.weight``, matching this fixture's lora file
        keys (``lora_unet_proj.*``) via ``model_lora_keys_unet``'s naming
        rule (strip ``diffusion_model.``/``.weight``, dot-join, prefix
        ``lora_unet_``). A bare ``Linear`` with no ``.proj`` wrapper would
        key as ``lora_unet_`` (empty) and silently fail to match -- proven
        the hard way while developing this test; keep the wrapper.
        """

        def __init__(self, seed: int) -> None:
            super().__init__()
            self.proj = torch.nn.Linear(in_features, out_features, bias=False)
            with torch.no_grad():
                self.proj.weight.copy_(seeded(out_features, in_features, seed=seed))

    class _FakeModelConfig:
        def __init__(self) -> None:
            self.unet_config: dict = {}

    class _FakeUnet(torch.nn.Module):
        def __init__(self, seed: int) -> None:
            super().__init__()
            self.diffusion_model = _FakeProj(seed)
            self.model_config = _FakeModelConfig()

    def fresh_patcher() -> comfy.model_patcher.ModelPatcher:
        # A FRESH base model per call (never shared/reused): ModelPatcher's
        # own .clone() shares the underlying nn.Module rather than deep-
        # copying it, so re-using one base across the two call paths below
        # would let the first patch leak into the second's "base".
        return comfy.model_patcher.ModelPatcher(
            _FakeUnet(seed=99),
            load_device=torch.device("cpu"),
            offload_device=torch.device("cpu"),
        )

    unet_up = seeded(out_features, rank, seed=1)
    unet_down = seeded(rank, in_features, seed=2)
    lora_path = tmp_path / "sweep_test_lora.safetensors"
    save_file(
        {
            "lora_unet_proj.lora_up.weight": unet_up,
            "lora_unet_proj.lora_down.weight": unet_down,
            "lora_unet_proj.alpha": torch.tensor(alpha),
        },
        str(lora_path),
    )

    context.resolve_lora_path = lambda _name: str(lora_path)
    nodes_sweep.set_context(context)

    node = LoraLibrarySweep()
    models, _clips, labels = node.sweep(
        model=fresh_patcher(),
        clip=None,
        lora_stack=[("sweep_test_lora.safetensors", 0.5, 0.5)],
        min=0.7,
        max=0.7,
        increment=0.1,
        mode=MODE_INDEPENDENT,
    )
    assert len(models) == 1
    assert labels == ["sweep_test_lora_0.7"]

    w_via_sweep = (
        models[0]
        .patch_model(device_to=torch.device("cpu"))
        .diffusion_model.proj.weight.detach()
        .clone()
    )

    direct_model, _direct_clip = nodes_sets.LoraLibraryApplySet._apply_stack(
        context, fresh_patcher(), None, [("sweep_test_lora.safetensors", 0.7, 0.7)]
    )
    w_via_direct = (
        direct_model.patch_model(device_to=torch.device("cpu"))
        .diffusion_model.proj.weight.detach()
        .clone()
    )

    torch.testing.assert_close(w_via_sweep, w_via_direct, rtol=0, atol=0)

    # Sanity: prove this actually patched something, rather than both sides
    # trivially matching because neither applied the lora at all.
    base_weight = fresh_patcher().model.diffusion_model.proj.weight.detach()
    assert (w_via_sweep - base_weight).abs().max().item() > 1e-6
