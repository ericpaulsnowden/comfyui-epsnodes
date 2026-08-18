"""Tests for ``eps_image.nodes_cross_sweep`` (FORMAT.md §6.10, "EPS Run Multiplier").

Pure-Python contract tests: sweep/pair elements are opaque sentinels."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from eps_image.nodes_cross_sweep import EPSCrossSweep


@pytest.fixture(autouse=True)
def fake_execution_blocker(monkeypatch: pytest.MonkeyPatch):
    """Same fixture convention as ``test_switcher.py``/``test_cross.py``.

    AUTOUSE since v0.46.0: with `vae` unwired (every legacy call in this
    file), run() emits per-run blockers on the vae output, so the lazy
    `comfy_execution` import now happens on the happy path too -- every
    test needs the fake installed, not just the empty-input ones."""

    class FakeExecutionBlocker:
        def __init__(self, message):
            self.message = message

    import types

    graph_mod = types.ModuleType("comfy_execution.graph")
    graph_mod.ExecutionBlocker = FakeExecutionBlocker
    pkg_mod = types.ModuleType("comfy_execution")
    pkg_mod.graph = graph_mod
    monkeypatch.setitem(sys.modules, "comfy_execution", pkg_mod)
    monkeypatch.setitem(sys.modules, "comfy_execution.graph", graph_mod)
    return FakeExecutionBlocker


def run(**overrides):
    """Two sweep steps x two pairs unless overridden.

    v0.66.1: the NODE's defaults flipped to multiply/multiply (owner
    decision); this helper pins the classic paired/aligned modes
    EXPLICITLY so every behavioral test of those modes stays a test of
    those modes -- the new defaults get their own dedicated pins below.
    """
    kwargs = {
        "pair_mode": "paired",
        "sweep_mode": "aligned",
        "model": ["m0", "m1"],
        "clip": ["c0", "c1"],
        "label": ["lora_0.0", "lora_0.5"],
        "image": ["iA", "iB"],
        "text": ["tA", "tB"],
    }
    kwargs.update(overrides)
    return EPSCrossSweep().run(**kwargs)


class TestCrossSweep:
    def test_strength_major_order_owner_decision(self) -> None:
        """Outer loop = sweep step: all pairs at step 0, then all at step 1."""
        models, clips, images, texts, prefixes, labels, _vaes, _mlow1 = run()
        assert models == ["m0", "m0", "m1", "m1"]
        assert clips == ["c0", "c0", "c1", "c1"]
        assert images == ["iA", "iB", "iA", "iB"]
        assert texts == ["tA", "tB", "tA", "tB"]
        assert labels == ["lora_0.0", "lora_0.0", "lora_0.5", "lora_0.5"]
        # v0.67.0: the file-level component carries the run token.
        assert prefixes == [
            "lora_0.0/pair_01_m1_p1", "lora_0.0/pair_02_m1_p2",
            "lora_0.5/pair_01_m2_p1", "lora_0.5/pair_02_m2_p2",
        ]

    def test_owner_scale_11_steps_x_8_pairs_is_88(self) -> None:
        models, _clips, images, _texts, prefixes, _labels, _v, _mlow2 = run(
            model=[f"m{s}" for s in range(11)],
            clip=[f"c{s}" for s in range(11)],
            label=[f"lora_{s / 10:.1f}" for s in range(11)],
            image=[f"i{p}" for p in range(8)],
            text=[f"t{p}" for p in range(8)],
        )
        assert len(models) == len(images) == len(prefixes) == 88
        # first block is step 0 across all 8 pairs
        assert models[:8] == ["m0"] * 8
        assert images[:8] == [f"i{p}" for p in range(8)]

    def test_names_and_base_folder_shape_the_save_prefix(self) -> None:
        _m, _c, _i, _t, prefixes, _l, _v, _mlow3 = run(
            name=["Portrait A", "Landscape B"],
            base_folder=["shoot42/tuesday"],
        )
        assert prefixes == [
            "shoot42/tuesday/lora_0.0/Portrait A_m1_p1",
            "shoot42/tuesday/lora_0.0/Landscape B_m1_p2",
            "shoot42/tuesday/lora_0.5/Portrait A_m2_p1",
            "shoot42/tuesday/lora_0.5/Landscape B_m2_p2",
        ]

    def test_hostile_characters_are_sanitized_out_of_paths(self) -> None:
        _m, _c, _i, _t, prefixes, _l, _v, _mlow4 = run(
            label=['lo/ra:0*0', "ok"],
            name=['pa\\ir?"one', "x"],
            base_folder=["../weird/../base"],
        )
        first = prefixes[0]
        assert first == "weird/base/lo_ra_0_0/pa_ir__one_m1_p1"
        for bad in ("..", ":", "*", "?", '"', "\\"):
            assert bad not in first

    def test_empty_name_falls_back_to_stable_pair_number(self) -> None:
        _m, _c, _i, _t, prefixes, _l, _v, _mlow5 = run(name=["", "RealName"])
        assert prefixes[0].endswith("/pair_01_m1_p1")
        assert prefixes[1].endswith("/RealName_m1_p2")

    def test_mismatched_sweep_side_fails_loudly_naming_lengths(self) -> None:
        """v0.49.1 (owner bug 2026-08-03): a stale 2-long label wire clamped
        a 4-model sweep to 2 steps with only a console warning -- invisible
        from the browser. Disagreeing >1 lengths now FAIL the queue."""
        with pytest.raises(ValueError) as excinfo:
            run(model=["m0", "m1", "m2"])
        message = str(excinfo.value)
        assert "model=3" in message and "clip=2" in message and "label=2" in message

    def test_length_one_sweep_inputs_broadcast_across_steps(self) -> None:
        """A single constant VAE/label across an N-step sweep is legitimate:
        length-1 sweep inputs repeat for every step instead of clamping."""
        models, _c, _i, _t, prefixes, labels, vaes, _mlow6 = run(
            label=["shared"], vae=["v_shared"]
        )
        assert models == ["m0", "m0", "m1", "m1"]  # steps still 2
        assert labels == ["shared"] * 4
        assert vaes == ["v_shared"] * 4
        assert prefixes[0].startswith("shared/")

    def test_owner_report_4_models_x_2_images_x_2_texts_is_16(self) -> None:
        """The 2026-08-03 report's EXPECTED shape, pinned: only model wired
        sweep-side, multiply mode -> 4 x (2 x 2) = 16 runs, every model
        distinct in strength-major blocks of 4."""
        models, _c, images, texts, _p, _l, _v, _mlx1 = EPSCrossSweep().run(
            model=["m1", "m2", "m3", "m4"],
            image=["iA", "iB"], text=["p1", "p2"], pair_mode="multiply",
        )
        assert len(models) == 16
        assert models == [m for m in ["m1", "m2", "m3", "m4"] for _ in range(4)]
        assert images[:4] == ["iA", "iA", "iB", "iB"]
        assert texts[:4] == ["p1", "p2", "p1", "p2"]

    def test_mismatched_pair_side_uses_min_and_survives(self) -> None:
        _m, _c, images, texts, _p, _l, _v, _mlow7 = run(image=["iA", "iB", "iC"])
        assert images == ["iA", "iB", "iA", "iB"]
        assert texts == ["tA", "tB", "tA", "tB"]

    @pytest.mark.parametrize(
        "overrides",
        [
            {"model": []},
            {"image": []},
            {"text": None},
            {"model": [None]},
        ],
    )
    def test_empty_side_returns_blocker_six(self, overrides, fake_execution_blocker) -> None:
        outputs = run(**overrides)
        # v0.67.0: derived from RETURN_NAMES (the v0.66.0 literal 7-tuple
        # missed the model_low tail -- a real latent bug this fixed).
        assert len(outputs) == len(EPSCrossSweep.RETURN_NAMES)
        for lst in outputs:
            assert len(lst) == 1 and isinstance(lst[0], fake_execution_blocker)


class TestClassShape:
    def test_category_and_flags(self) -> None:
        assert EPSCrossSweep.CATEGORY == "EPSNodes"
        assert EPSCrossSweep.INPUT_IS_LIST is True
        assert EPSCrossSweep.OUTPUT_IS_LIST == (True,) * 8

    def test_output_shape(self) -> None:
        assert EPSCrossSweep.RETURN_TYPES == (
            "MODEL", "CLIP", "IMAGE", "STRING", "STRING", "STRING", "VAE", "MODEL"
        )
        assert EPSCrossSweep.RETURN_NAMES == (
            "model",
            "clip",
            "image",
            "text",
            "save_prefix",
            "label",
            "vae",
            "model_low",
        )

    def test_inputs(self) -> None:
        spec = EPSCrossSweep.INPUT_TYPES()
        # v0.46.0: `image` moved to optional (text-only mode) and `vae`
        # joined the sweep group. v0.49.0: the whole sweep group
        # (model/clip/label) moved to optional too (no-sweep = pure pair
        # multiplier), and `pair_mode` was added -- all additive, §8-safe.
        assert set(spec["required"]) == {"text"}
        assert set(spec["optional"]) == {
            "model", "model_low", "clip", "label", "name", "base_folder", "image", "vae", "pair_mode",
            "sweep_mode", "solo_run",
        }
        assert spec["optional"]["label"][1]["forceInput"] is True
        assert spec["required"]["text"][1]["forceInput"] is True
        assert spec["optional"]["name"][1]["forceInput"] is True
        # Widget tail order is FROZEN once shipped: widgets_values restores
        # positionally, so every new widget appends at the END (see
        # INPUT_TYPES' own comments). v0.49.0 tail was ...base_folder,
        # pair_mode; v0.57.0 appended sweep_mode after pair_mode.
        optional_keys = list(spec["optional"])
        # v0.67.0 appended solo_run after sweep_mode (same tail law).
        assert optional_keys.index("solo_run") == len(optional_keys) - 1
        assert optional_keys.index("sweep_mode") == len(optional_keys) - 2
        assert optional_keys.index("pair_mode") == len(optional_keys) - 3
        assert optional_keys.index("base_folder") == len(optional_keys) - 4
        assert spec["optional"]["pair_mode"][0] == ["paired", "multiply"]
        # v0.66.1 (owner): multiply is the default for both modes.
        assert spec["optional"]["pair_mode"][1]["default"] == "multiply"

    def test_function_entry_point(self) -> None:
        assert callable(getattr(EPSCrossSweep, EPSCrossSweep.FUNCTION))


def test_module_never_imports_comfy_or_torch() -> None:
    repo = Path(__file__).resolve().parents[1]
    code = (
        "import sys; sys.path.insert(0, r'" + str(repo) + "'); "
        "import eps_image.nodes_cross_sweep; "
        "bad = [m for m in sys.modules if m == 'torch' or m.startswith('torch.') "
        "or m == 'comfy' or m.startswith('comfy.') or m.startswith('comfy_execution')]; "
        "assert not bad, bad"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


class TestVaePassthrough:
    """v0.46.0: the sweep group's optional fourth list (FORMAT.md §6.10),
    wired from EPS Checkpoint Switcher's vae output."""

    def test_wired_vae_rides_index_aligned_with_its_step(self) -> None:
        _m, _c, _i, _t, _p, labels, vaes, _mlow8 = run(vae=["v0", "v1"])
        # strength-major: two pairs at step 0, then two at step 1
        assert vaes == ["v0", "v0", "v1", "v1"]
        assert labels == ["lora_0.0", "lora_0.0", "lora_0.5", "lora_0.5"]

    def test_unwired_vae_emits_one_blocker_per_run(
        self, fake_execution_blocker: type
    ) -> None:
        _m, _c, _i, _t, _p, _l, vaes, _mlow9 = run()
        assert len(vaes) == 4
        assert all(isinstance(v, fake_execution_blocker) for v in vaes)
        assert all(v.message is None for v in vaes)  # silent skip, no error event

    def test_single_vae_broadcasts_across_the_sweep(self) -> None:
        """v0.49.1 supersedes the v0.46 clamp-and-warn: ONE constant VAE
        across an N-step sweep is the legitimate common case (a lora sweep
        doesn't change the VAE), so length-1 broadcasts instead of
        clamping the whole sweep to one step."""
        models, _c, _i, _t, _p, _l, vaes, _mlow10 = run(vae=["v0"])  # 1 vae, 2 steps
        assert models == ["m0", "m0", "m1", "m1"]  # steps stay 2
        assert vaes == ["v0"] * 4

    def test_multi_vae_length_disagree_fails_loudly(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            run(vae=["v0", "v1", "v2"])  # 3 vaes vs 2-step sweep
        assert "vae=3" in str(excinfo.value)

    def test_wired_but_empty_vae_takes_the_whole_node_blocker_path(
        self, fake_execution_blocker: type
    ) -> None:
        outputs = run(vae=[])
        assert len(outputs) == len(EPSCrossSweep.RETURN_NAMES)
        for out in outputs:
            assert len(out) == 1
            assert isinstance(out[0], fake_execution_blocker)


class TestTextOnlyPairs:
    """v0.46.0: image unwired = txt2img iteration (Checkpoint Switcher x a
    multi-select Prompt Notebook, no input images anywhere)."""

    def test_pairs_are_the_texts_alone(self) -> None:
        models, _c, _i, texts, prefixes, _l, _v, _mlow11 = run(image=None, text=["t0", "t1", "t2"])
        assert len(models) == 6  # 2 steps x 3 texts
        assert texts == ["t0", "t1", "t2", "t0", "t1", "t2"]
        assert len(prefixes) == 6

    def test_image_output_blocks_per_run_not_whole_node(
        self, fake_execution_blocker: type
    ) -> None:
        models, _c, images, texts, _p, _l, _v, _mlow12 = run(image=None, text=["t0", "t1"])
        assert models == ["m0", "m0", "m1", "m1"]  # real values still flow
        assert all(isinstance(i, fake_execution_blocker) for i in images)
        assert len(images) == len(texts) == 4  # index alignment preserved

    def test_text_only_with_wired_vae_composes(self) -> None:
        # the headline flow: Checkpoint Switcher (model+clip+vae+label) x
        # Notebook texts, no images
        models, _clips, _i, _texts, prefixes, _labels, vaes, _mlow13 = run(
            model=["mA", "mB"],
            clip=["cA", "cB"],
            vae=["vA", "vB"],
            label=["ckptA", "ckptB"],
            image=None,
            text=["portrait", "landscape"],
            name=["Portrait", "Landscape"],
        )
        assert models == ["mA", "mA", "mB", "mB"]
        assert vaes == ["vA", "vA", "vB", "vB"]
        assert prefixes[0] == "ckptA/Portrait_m1_t1"
        assert prefixes[3] == "ckptB/Landscape_m2_t2"

    def test_names_still_shape_save_prefix_in_text_only_mode(self) -> None:
        _m, _c, _i, _t, prefixes, _l, _v, _mlow14 = run(image=None, text=["x"], name=[])
        assert prefixes == ["lora_0.0/pair_01_m1_t1", "lora_0.5/pair_01_m2_t1"]


class TestNoSweepPureMultiplier:
    """v0.49.0: no sweep member wired = Cross Product's old job, in here."""

    def test_multiply_matches_cross_product_byte_for_byte(self, fake_execution_blocker) -> None:
        """The golden parity pin, now against an INLINE expectation:
        EPSCrossProduct was removed in v0.50.0 (owner direction), so the
        cross it used to compute -- image-major, names aligned per TEXT,
        short name lists padded with "" -- is spelled out here verbatim.
        This is the contract multiply mode must keep forever."""
        images = ["i1", "i2"]
        texts = ["tA", "tB", "tC"]
        names = ["A", "B"]  # deliberately short: pads with ""
        cp_images = ["i1", "i1", "i1", "i2", "i2", "i2"]
        cp_texts = ["tA", "tB", "tC", "tA", "tB", "tC"]
        cp_names = ["A", "B", "", "A", "B", ""]
        models, clips, out_images, out_texts, prefixes, labels, vaes, _mlx2 = EPSCrossSweep().run(
            text=texts, image=images, name=names, pair_mode="multiply"
        )
        assert out_images == cp_images
        assert out_texts == cp_texts
        # name doesn't ride out directly -- it becomes save_prefix's pair
        # component; "" falls back to pair_NN exactly like paired mode.
        # v0.67.0: the pair component now carries the i/t run token.
        n_texts = 3
        expected_components = [
            f"{n or f'pair_{p + 1:02d}'}_i{p // n_texts + 1}_t{p % n_texts + 1}"
            for p, n in enumerate(cp_names)
        ]
        assert prefixes == expected_components
        # Every sweep-side output blocks per run, keeping alignment.
        assert len(models) == len(cp_images)
        assert all(isinstance(m, fake_execution_blocker) for m in models)
        assert all(isinstance(c, fake_execution_blocker) for c in clips)
        assert all(isinstance(lb, fake_execution_blocker) for lb in labels)
        assert all(isinstance(v, fake_execution_blocker) for v in vaes)

    def test_no_sweep_save_prefix_has_no_step_level(self) -> None:
        _m, _c, _i, _t, prefixes, _l, _v, _mlx3 = EPSCrossSweep().run(
            text=["tA"], image=["i1"], base_folder=["shoot"], pair_mode="multiply"
        )
        assert prefixes == ["shoot/pair_01_i1_t1"]

    def test_no_sweep_paired_zips_like_before(self) -> None:
        _m, _c, images, texts, prefixes, _l, _v, _mlx4 = EPSCrossSweep().run(
            text=["tA", "tB"], image=["i1", "i2"], pair_mode="paired"
        )
        assert images == ["i1", "i2"]
        assert texts == ["tA", "tB"]
        assert prefixes == ["pair_01_p1", "pair_02_p2"]

    def test_no_sweep_empty_pair_side_blocks_whole_node(self, fake_execution_blocker) -> None:
        outputs = EPSCrossSweep().run(text=[], image=[], pair_mode="multiply")
        assert all(
            len(lst) == 1 and isinstance(lst[0], fake_execution_blocker) for lst in outputs
        )


class TestMultiplyWithSweep:
    """v0.49.0's new capability: sweep x images x texts in ONE node."""

    def test_three_axis_counts_and_order(self) -> None:
        models, _clips, images, texts, _prefixes, _labels, _v, _mlow15 = run(
            image=["i1", "i2"], text=["tA", "tB", "tC"], pair_mode="multiply"
        )
        # 2 steps x (2 images x 3 texts) = 12 runs, strength-major outer,
        # image-major inside each step (Cross Product's order).
        assert len(models) == 12
        assert models == ["m0"] * 6 + ["m1"] * 6
        assert images[:6] == ["i1", "i1", "i1", "i2", "i2", "i2"]
        assert texts[:6] == ["tA", "tB", "tC", "tA", "tB", "tC"]
        assert images[6:] == images[:6]

    def test_multiply_names_align_per_text_not_per_pair(self) -> None:
        _m, _c, _i, _t, prefixes, _l, _v, _mlow16 = run(
            image=["i1", "i2"], text=["tA", "tB"], name=["portrait", "landscape"],
            pair_mode="multiply",
        )
        # names follow the TEXT axis: i1/tA, i1/tB, i2/tA, i2/tB per step.
        assert [p.split("/")[-1] for p in prefixes[:4]] == [
            "portrait_m1_i1_t1", "landscape_m1_i1_t2",
            "portrait_m1_i2_t1", "landscape_m1_i2_t2",
        ]

    def test_default_pair_mode_is_paired(self) -> None:
        """Omitting pair_mode (every workflow saved before v0.49.0) keeps
        the zip semantics -- 2x2 stays 2 pairs, not 4."""
        _m, _c, images, _texts, _p, _l, _v, _mlow17 = run()
        assert len(images) == 4  # 2 steps x 2 PAIRS


class TestPartialSweepGroup:
    """v0.49.0: each unwired sweep member blocks its own output only."""

    def test_label_unwired_falls_back_to_step_numbers(self, fake_execution_blocker) -> None:
        models, _clips, _i, _t, prefixes, labels, _v, _mlow18 = run(label=None)
        assert models == ["m0", "m0", "m1", "m1"]  # steps from model/clip alone
        assert all(isinstance(lb, fake_execution_blocker) for lb in labels)
        assert [p.split("/")[0] for p in prefixes] == [
            "step_01", "step_01", "step_02", "step_02",
        ]

    def test_model_unwired_blocks_model_output_only(self, fake_execution_blocker) -> None:
        models, clips, _i, _t, _prefixes, labels, _v, _mlow19 = run(model=None)
        assert all(isinstance(m, fake_execution_blocker) for m in models)
        assert clips == ["c0", "c0", "c1", "c1"]
        assert labels == ["lora_0.0", "lora_0.0", "lora_0.5", "lora_0.5"]


class TestConsumedButUnwiredGuard:
    """v0.51.0 (owner report 2026-08-03): consuming an output whose backing
    input is unwired used to silently skip everything downstream ("completes
    immediately, nothing generated"). With the prompt visible, it errors."""

    @staticmethod
    def _prompt_consuming(slot, uid="7"):
        return {
            "9": {"class_type": "SaveImage", "inputs": {"images": [uid, slot]}},
            uid: {"class_type": "EPSCrossSweep", "inputs": {}},
        }

    def test_image_output_consumed_in_text_only_mode_errors_with_guidance(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            run(image=None, prompt=[self._prompt_consuming(2)], unique_id=["7"])
        message = str(excinfo.value)
        assert "image" in message and "Empty Latent" in message

    def test_vae_output_consumed_while_unwired_errors(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            run(prompt=[self._prompt_consuming(6)], unique_id=["7"])
        assert "vae" in str(excinfo.value)

    def test_consumed_and_wired_is_fine(self) -> None:
        models, *_ = run(
            vae=["v0"], prompt=[self._prompt_consuming(6)], unique_id=["7"]
        )
        assert len(models) == 4

    def test_unwired_but_unconsumed_keeps_blocker_behavior(self, fake_execution_blocker) -> None:
        # vae unwired, only the IMAGE output (slot 2, which IS wired via the
        # image input) is consumed -> no error, vae emits blockers as before.
        *_, vaes = run(prompt=[self._prompt_consuming(2)], unique_id=["7"])
        assert all(isinstance(v, fake_execution_blocker) for v in vaes)

    def test_no_prompt_available_stays_out_of_the_way(self, fake_execution_blocker) -> None:
        *_, vaes = run()  # direct call, no prompt/unique_id -- exactly the old behavior
        assert all(isinstance(v, fake_execution_blocker) for v in vaes)


class TestSweepModeMultiply:
    """v0.57.0 `sweep_mode` (owner ask 2026-08-09: 4 models x 2 VAEs = 8
    runs): "multiply" crosses the model/clip/label axis against the vae
    axis, model-major; "aligned" (the default) is byte-identical to before
    the widget existed."""

    def test_widget_defaults_to_aligned(self) -> None:
        spec = EPSCrossSweep.INPUT_TYPES()
        values, options = spec["optional"]["sweep_mode"]
        assert values == ["aligned", "multiply"]
        # v0.66.1 (owner): multiply by default.
        assert options["default"] == "multiply"

    def test_four_models_times_two_vaes_is_eight_model_major_steps(self) -> None:
        outputs = run(
            model=["m0", "m1", "m2", "m3"],
            clip=["c"],
            label=["A", "B", "C", "D"],
            vae=["v0", "v1"],
            image=["i"],
            text=["t"],
            sweep_mode="multiply",
        )
        models, clips, _images, _texts, prefixes, labels, vaes = (
            outputs[0], outputs[1], outputs[2], outputs[3], outputs[4], outputs[5], outputs[6]
        )
        assert models == ["m0", "m0", "m1", "m1", "m2", "m2", "m3", "m3"]
        assert vaes == ["v0", "v1"] * 4
        assert clips == ["c"] * 8  # length-1 broadcasts across every combination
        assert labels == ["A", "A", "B", "B", "C", "C", "D", "D"]
        assert prefixes[:4] == [
            "A_vae01/pair_01_m1_v1_p1", "A_vae02/pair_01_m1_v2_p1",
            "B_vae01/pair_01_m2_v1_p1", "B_vae02/pair_01_m2_v2_p1",
        ]

    def test_vae_length_one_multiplies_to_the_plain_sweep(self) -> None:
        aligned = run(vae=["v"], sweep_mode="aligned")
        multiplied = run(vae=["v"], sweep_mode="multiply")
        assert aligned == multiplied
        assert len(multiplied[0]) == 4  # 2 steps x 2 pairs, unchanged

    def test_vae_unwired_multiply_matches_aligned(self) -> None:
        assert run(sweep_mode="multiply")[4] == run(sweep_mode="aligned")[4]

    def test_axis1_disagreement_still_fails_in_multiply(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            run(
                model=["m0", "m1", "m2", "m3"],
                label=["A", "B"],
                vae=["v0", "v1"],
                sweep_mode="multiply",
            )
        message = str(excinfo.value)
        assert "model=4" in message and "label=2" in message
        assert "vae=2" not in message  # the vae axis is legitimately independent here

    def test_aligned_error_names_only_conflicts_and_hints_multiply(self) -> None:
        # The owner's exact 2026-08-09 report shape: model=4, clip=1, vae=2.
        with pytest.raises(ValueError) as excinfo:
            run(
                model=["m0", "m1", "m2", "m3"],
                clip=["c"],
                label=None,
                vae=["v0", "v1"],
            )
        message = str(excinfo.value)
        assert "model=4, vae=2" in message  # the actual disagreement, alone
        assert "clip=1" not in message.split("--")[0]  # not listed as a suspect
        assert "broadcasts fine" in message and "clip=1" in message
        assert "sweep_mode" in message and "multiply" in message  # the discovery hint

    def test_same_origin_vae_is_refused_in_multiply(self) -> None:
        prompt = {
            "5": {
                "class_type": "EPSCrossSweep",
                "inputs": {"model": ["2", 0], "clip": ["2", 1], "vae": ["2", 2]},
            },
        }
        with pytest.raises(ValueError) as excinfo:
            run(vae=["v0", "v1"], sweep_mode="multiply", prompt=[prompt], unique_id=["5"])
        assert "SAME node" in str(excinfo.value)

    def test_independent_origin_vae_passes_the_guard(self) -> None:
        prompt = {
            "5": {
                "class_type": "EPSCrossSweep",
                "inputs": {"model": ["2", 0], "clip": ["2", 1], "vae": ["3", 0]},
            },
        }
        outputs = run(vae=["v0", "v1"], sweep_mode="multiply", prompt=[prompt], unique_id=["5"])
        assert len(outputs[0]) == 8  # 2 models x 2 vaes x 2 pairs

    def test_guard_degrades_without_a_prompt(self) -> None:
        outputs = run(vae=["v0", "v1"], sweep_mode="multiply")
        assert len(outputs[0]) == 8

    def test_wired_empty_vae_still_blocks_the_whole_node(
        self, fake_execution_blocker: type
    ) -> None:
        outputs = run(vae=[], sweep_mode="multiply")
        for out in outputs:
            assert len(out) == 1
            assert isinstance(out[0], fake_execution_blocker)

    def test_only_vae_wired_multiply_steps_per_vae(self) -> None:
        outputs = run(
            model=None, clip=None, label=None,
            vae=["v0", "v1", "v2"],
            image=["i"], text=["t"],
            sweep_mode="multiply",
        )
        assert outputs[6] == ["v0", "v1", "v2"]
        assert outputs[4] == [
            "step_01_vae01/pair_01_m1_v1_p1",
            "step_01_vae02/pair_01_m1_v2_p1",
            "step_01_vae03/pair_01_m1_v3_p1",
        ]

    def test_input_is_list_wrapped_widget_form(self) -> None:
        # Real /prompt execution wraps EVERY input in a list (INPUT_IS_LIST)
        # -- the widget arrives as ["multiply"], not "multiply".
        outputs = run(
            model=["m0", "m1"], clip=["c"], label=["A", "B"],
            vae=["v0", "v1"], image=["i"], text=["t"],
            sweep_mode=["multiply"],
        )
        assert len(outputs[0]) == 4  # 2 models x 2 vaes x 1 pair
        assert outputs[0] == ["m0", "m0", "m1", "m1"]

    def test_hint_only_when_vae_is_a_party(self) -> None:
        # Review 2026-08-09: for a model-vs-label conflict the multiply hint
        # was wrong advice (multiply still checks the model axis).
        with pytest.raises(ValueError) as excinfo:
            run(model=["m"] * 4, label=["A", "B"], vae=None)
        assert "sweep_mode" not in str(excinfo.value)
        with pytest.raises(ValueError) as excinfo:
            run(model=["m"] * 4, clip=["c"], label=None, vae=["v0", "v1"])
        assert "set sweep_mode" in str(excinfo.value)

    def test_wired_empty_input_is_not_called_fine(self) -> None:
        # Review 2026-08-09: vae=[] landed in the "broadcast fine" bucket --
        # but a wired-empty list blocks the whole node, the opposite of fine.
        with pytest.raises(ValueError) as excinfo:
            run(model=["m"] * 4, clip=["c0", "c1"], label=None, vae=[])
        message = str(excinfo.value)
        assert "vae=0" in message
        assert "wired but EMPTY" in message
        assert "vae=0 -- broadcast" not in message

    def test_fine_note_grammar_singular(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            run(model=["m"] * 4, clip=["c"], label=None, vae=["v0", "v1"])
        assert "broadcasts fine and is not the problem" in str(excinfo.value)


class TestRunCountAnnouncement:
    """v0.58.0: run() announces its definitive count via send_sync the
    moment it executes -- the overnight-safety half of the owner's
    "show the number of runs" ask (the on-node readout is the estimate)."""

    @staticmethod
    def _fake_prompt_server(monkeypatch):
        import types

        sent = []

        class _Instance:
            def send_sync(self, event, payload):
                sent.append((event, payload))

        class PromptServer:
            instance = _Instance()

        server_mod = types.ModuleType("server")
        server_mod.PromptServer = PromptServer
        monkeypatch.setitem(sys.modules, "server", server_mod)
        return sent

    def test_count_event_carries_steps_pairs_total(self, monkeypatch) -> None:
        sent = self._fake_prompt_server(monkeypatch)
        run(
            model=["m0", "m1", "m2", "m3"], clip=["c"], label=None,
            vae=["v0", "v1"], image=["i"], text=["t"],
            sweep_mode="multiply", unique_id=["185"],
        )
        events = [payload for event, payload in sent if event == "eps-run-multiplier-count"]
        assert events == [{"node": "185", "steps": 8, "pairs": 1, "total": 8}]

    def test_no_server_module_degrades_silently(self, monkeypatch) -> None:
        monkeypatch.setitem(sys.modules, "server", None)
        outputs = run()  # must not raise despite the unimportable server
        assert len(outputs[0]) == 4

    def test_blocker_path_does_not_announce(self, monkeypatch) -> None:
        sent = self._fake_prompt_server(monkeypatch)
        run(model=[])
        assert sent == []


class TestModelLowWeldedV0660:
    """v0.66.0 WAN pairing: model_low travels WELDED to the model axis --
    same indexing, never a new axis, run counts untouched."""

    def test_welded_indexing_and_no_extra_runs(self) -> None:
        out = EPSCrossSweep().run(
            model=["h1", "h2"],
            model_low=["l1", "l2"],
            label=["a", "b"],
            text=["t"],
        )
        models, lows = out[0], out[7]
        assert len(models) == 2  # 2 steps x 1 pair -- model_low added none
        assert models == ["h1", "h2"]
        assert lows == ["l1", "l2"]

    def test_single_low_broadcasts(self) -> None:
        out = EPSCrossSweep().run(model=["h1", "h2"], model_low=["lo"], text=["t"])
        assert out[7] == ["lo", "lo"]

    def test_length_mismatch_fails_loudly_naming_model_low(self) -> None:
        with pytest.raises(ValueError, match=r"model_low=2"):
            EPSCrossSweep().run(
                model=["h1", "h2", "h3"], model_low=["l1", "l2"], text=["t"]
            )

    def test_multiply_mode_keeps_the_pair_welded_while_vae_crosses(self) -> None:
        out = EPSCrossSweep().run(
            model=["h1", "h2"],
            model_low=["l1", "l2"],
            vae=["vA", "vB"],
            text=["t"],
            sweep_mode="multiply",
        )
        models, vaes, lows = out[0], out[6], out[7]
        assert len(models) == 4  # 2 models x 2 vaes; model_low added nothing
        assert models == ["h1", "h1", "h2", "h2"]
        assert lows == ["l1", "l1", "l2", "l2"]  # welded to the model index
        assert vaes == ["vA", "vB", "vA", "vB"]

    def test_unwired_low_blocks_only_its_output(self) -> None:
        out = EPSCrossSweep().run(model=["h1"], text=["t"])
        assert out[0] == ["h1"]
        assert "ExecutionBlocker" in type(out[7][0]).__name__


class TestMultiplyDefaultsV0661:
    """v0.66.1 (owner: "make both pair and sweep modes multiply by
    default ... there are rare occasions when you would want them to be
    anything but multiply"). The accepted compat cost is explicit: a
    workflow saved BEFORE these widgets existed now loads as multiply."""

    def test_omitted_modes_run_as_multiply(self) -> None:
        out = EPSCrossSweep().run(image=["i1", "i2"], text=["tA", "tB"])
        # multiply: 2 images x 2 texts = 4 pairs (paired would zip to 2)
        assert len(out[3]) == 4

    def test_omitted_sweep_mode_multiplies_vae(self) -> None:
        out = EPSCrossSweep().run(model=["m1", "m2"], vae=["vA", "vB"], text=["t"])
        # multiply: 2 models x 2 vaes = 4 runs (aligned would pair to 2)
        assert len(out[0]) == 4


class TestRunTokensV0670:
    """v0.67.0 provenance M1 (roadmap docs/ROADMAP-run-provenance.md).

    Every emitted filename prefix ends with a run TOKEN built from pure
    indices (owner's pinned choice over readable names): sweep fragment
    ``m{N}`` (plus ``_v{M}`` ONLY when vae is an independent multiply
    axis), then the pair fragment (``p{N}`` paired, ``i{N}_t{N}``
    multiply, ``t{N}`` text-only). The token is the per-run identity a
    file keeps even when separated from its folders -- M2 resolves it
    back to a baked workflow, so its composition is CONTRACT, not
    cosmetics."""

    def test_paired_aligned_tokens(self) -> None:
        prefixes = run()[4]
        assert [p.rsplit("/", 1)[-1] for p in prefixes] == [
            "pair_01_m1_p1", "pair_02_m1_p2",
            "pair_01_m2_p1", "pair_02_m2_p2",
        ]

    def test_aligned_vae_never_gets_a_v_fragment(self) -> None:
        # In aligned mode vae FOLLOWS the sweep axis -- it is not an
        # independent axis, so m{N} alone already pins the vae. A _v
        # fragment here would imply a choice that doesn't exist.
        prefixes = run(vae=["vA", "vB"])[4]
        assert all("_v" not in p.rsplit("/", 1)[-1] for p in prefixes)

    def test_multiply_sweep_tokens_carry_the_vae_axis(self) -> None:
        out = run(sweep_mode="multiply", vae=["vA", "vB"],
                  image=["i"], text=["t"])
        tokens = [p.rsplit("/", 1)[-1] for p in out[4]]
        # model-major (divmod over the vae axis), matching the outputs.
        assert tokens == [
            "pair_01_m1_v1_p1", "pair_01_m1_v2_p1",
            "pair_01_m2_v1_p1", "pair_01_m2_v2_p1",
        ]

    def test_no_sweep_token_is_pair_only(self) -> None:
        out = EPSCrossSweep().run(
            image=["i1"], text=["tA", "tB"], pair_mode="multiply",
            sweep_mode="aligned",
        )
        assert out[4] == ["pair_01_i1_t1", "pair_02_i1_t2"]

    def test_tokens_are_unique_across_the_set(self) -> None:
        out = EPSCrossSweep().run(
            model=["m1", "m2"], clip=["c"], label=["A", "B"],
            vae=["v1", "v2"], image=["i1", "i2"], text=["t1", "t2", "t3"],
            pair_mode="multiply", sweep_mode="multiply",
        )
        # 2 models x 2 vaes x (2 images x 3 texts) = 24 distinct runs,
        # each with a distinct full prefix (folders + token).
        assert len(out[4]) == 24
        assert len(set(out[4])) == 24
        # ...and the TOKENS alone are unique too (a file separated from
        # its folders still identifies its run -- the M1 point).
        tokens = [p.rsplit("/", 1)[-1] for p in out[4]]
        assert len(set(tokens)) == 24


class TestSoloRunV0670:
    """v0.67.0 provenance M1: ``solo_run`` re-runs exactly ONE member of
    a set. Paste a token from a filename, queue, get that file again --
    the whole point of the tokens. A typo raises loudly (a silent 0-run
    "success" would burn a queue and teach the user nothing)."""

    def test_empty_solo_runs_the_whole_set(self) -> None:
        assert len(run(solo_run="")[4]) == 4
        assert len(run(solo_run="   ")[4]) == 4  # whitespace = unset

    def test_solo_selects_exactly_one_run(self) -> None:
        out = run(solo_run="m2_p1")
        models, _c, images, texts, prefixes = out[0], out[1], out[2], out[3], out[4]
        assert models == ["m1"]
        assert images == ["iA"]
        assert texts == ["tA"]
        assert prefixes == ["lora_0.5/pair_01_m2_p1"]

    def test_solo_multiply_pins_model_and_vae(self) -> None:
        out = run(sweep_mode="multiply", vae=["vA", "vB"],
                  image=["i"], text=["t"], solo_run="m2_v1_p1")
        assert out[0] == ["m1"]   # models[1] -- token indices are 1-based
        assert out[6] == ["vA"]
        assert len(out[4]) == 1 and out[4][0].endswith("_m2_v1_p1")

    def test_solo_pair_multiply_token(self) -> None:
        out = EPSCrossSweep().run(
            image=["i1", "i2"], text=["tA", "tB"], pair_mode="multiply",
            sweep_mode="aligned", solo_run="i2_t1",
        )
        assert out[2] == ["i2"]
        assert out[3] == ["tA"]

    def test_solo_no_match_raises_with_an_example_token(self) -> None:
        with pytest.raises(ValueError) as exc:
            run(solo_run="m9_p9")
        msg = str(exc.value)
        assert "m9_p9" in msg
        assert "4 runs" in msg          # steps x pairs of the helper set
        assert "m1_p1" in msg           # a real token to copy from
