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
    """Two sweep steps x two pairs unless overridden."""
    kwargs = {
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
        models, clips, images, texts, prefixes, labels, _vaes = run()
        assert models == ["m0", "m0", "m1", "m1"]
        assert clips == ["c0", "c0", "c1", "c1"]
        assert images == ["iA", "iB", "iA", "iB"]
        assert texts == ["tA", "tB", "tA", "tB"]
        assert labels == ["lora_0.0", "lora_0.0", "lora_0.5", "lora_0.5"]
        assert prefixes == [
            "lora_0.0/pair_01", "lora_0.0/pair_02",
            "lora_0.5/pair_01", "lora_0.5/pair_02",
        ]

    def test_owner_scale_11_steps_x_8_pairs_is_88(self) -> None:
        models, _clips, images, _texts, prefixes, _labels, _v = run(
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
        _m, _c, _i, _t, prefixes, _l, _v = run(
            name=["Portrait A", "Landscape B"],
            base_folder=["shoot42/tuesday"],
        )
        assert prefixes == [
            "shoot42/tuesday/lora_0.0/Portrait A",
            "shoot42/tuesday/lora_0.0/Landscape B",
            "shoot42/tuesday/lora_0.5/Portrait A",
            "shoot42/tuesday/lora_0.5/Landscape B",
        ]

    def test_hostile_characters_are_sanitized_out_of_paths(self) -> None:
        _m, _c, _i, _t, prefixes, _l, _v = run(
            label=['lo/ra:0*0', "ok"],
            name=['pa\\ir?"one', "x"],
            base_folder=["../weird/../base"],
        )
        first = prefixes[0]
        assert first == "weird/base/lo_ra_0_0/pa_ir__one"
        for bad in ("..", ":", "*", "?", '"', "\\"):
            assert bad not in first

    def test_empty_name_falls_back_to_stable_pair_number(self) -> None:
        _m, _c, _i, _t, prefixes, _l, _v = run(name=["", "RealName"])
        assert prefixes[0].endswith("/pair_01")
        assert prefixes[1].endswith("/RealName")

    def test_mismatched_sweep_side_uses_min_and_survives(self) -> None:
        models, clips, _i, _t, _p, labels, _v = run(model=["m0", "m1", "m2"])
        # clip/label have 2 -> steps = 2
        assert models == ["m0", "m0", "m1", "m1"]
        assert clips == ["c0", "c0", "c1", "c1"]
        assert labels[-1] == "lora_0.5"

    def test_mismatched_pair_side_uses_min_and_survives(self) -> None:
        _m, _c, images, texts, _p, _l, _v = run(image=["iA", "iB", "iC"])
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
        assert len(outputs) == 7
        for lst in outputs:
            assert len(lst) == 1 and isinstance(lst[0], fake_execution_blocker)


class TestClassShape:
    def test_category_and_flags(self) -> None:
        assert EPSCrossSweep.CATEGORY == "EPSNodes"
        assert EPSCrossSweep.INPUT_IS_LIST is True
        assert EPSCrossSweep.OUTPUT_IS_LIST == (True,) * 7

    def test_output_shape(self) -> None:
        assert EPSCrossSweep.RETURN_TYPES == (
            "MODEL", "CLIP", "IMAGE", "STRING", "STRING", "STRING", "VAE"
        )
        assert EPSCrossSweep.RETURN_NAMES == (
            "model",
            "clip",
            "image",
            "text",
            "save_prefix",
            "label",
            "vae",
        )

    def test_inputs(self) -> None:
        spec = EPSCrossSweep.INPUT_TYPES()
        # v0.46.0: `image` moved to optional (text-only mode) and `vae`
        # joined the sweep group. v0.49.0: the whole sweep group
        # (model/clip/label) moved to optional too (no-sweep = pure pair
        # multiplier), and `pair_mode` was added -- all additive, §8-safe.
        assert set(spec["required"]) == {"text"}
        assert set(spec["optional"]) == {
            "model", "clip", "label", "name", "base_folder", "image", "vae", "pair_mode",
        }
        assert spec["optional"]["label"][1]["forceInput"] is True
        assert spec["required"]["text"][1]["forceInput"] is True
        assert spec["optional"]["name"][1]["forceInput"] is True
        # pair_mode must stay the LAST widget: base_folder's saved value
        # restores positionally (see INPUT_TYPES' own comment).
        optional_keys = list(spec["optional"])
        assert optional_keys.index("pair_mode") == len(optional_keys) - 1
        assert optional_keys.index("base_folder") == len(optional_keys) - 2
        assert spec["optional"]["pair_mode"][0] == ["paired", "multiply"]
        assert spec["optional"]["pair_mode"][1]["default"] == "paired"

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
        _m, _c, _i, _t, _p, labels, vaes = run(vae=["v0", "v1"])
        # strength-major: two pairs at step 0, then two at step 1
        assert vaes == ["v0", "v0", "v1", "v1"]
        assert labels == ["lora_0.0", "lora_0.0", "lora_0.5", "lora_0.5"]

    def test_unwired_vae_emits_one_blocker_per_run(
        self, fake_execution_blocker: type
    ) -> None:
        _m, _c, _i, _t, _p, _l, vaes = run()
        assert len(vaes) == 4
        assert all(isinstance(v, fake_execution_blocker) for v in vaes)
        assert all(v.message is None for v in vaes)  # silent skip, no error event

    def test_vae_length_disagree_clamps_and_warns(self, caplog) -> None:
        import logging

        with caplog.at_level(logging.WARNING):
            models, _c, _i, _t, _p, _l, vaes = run(vae=["v0"])  # 1 vae, 2 steps
        assert models == ["m0", "m0"]  # clamped to 1 step x 2 pairs
        assert vaes == ["v0", "v0"]
        assert any("vae=1" in r.message for r in caplog.records)

    def test_wired_but_empty_vae_takes_the_whole_node_blocker_path(
        self, fake_execution_blocker: type
    ) -> None:
        outputs = run(vae=[])
        assert len(outputs) == 7
        for out in outputs:
            assert len(out) == 1
            assert isinstance(out[0], fake_execution_blocker)


class TestTextOnlyPairs:
    """v0.46.0: image unwired = txt2img iteration (Checkpoint Switcher x a
    multi-select Prompt Notebook, no input images anywhere)."""

    def test_pairs_are_the_texts_alone(self) -> None:
        models, _c, _i, texts, prefixes, _l, _v = run(image=None, text=["t0", "t1", "t2"])
        assert len(models) == 6  # 2 steps x 3 texts
        assert texts == ["t0", "t1", "t2", "t0", "t1", "t2"]
        assert len(prefixes) == 6

    def test_image_output_blocks_per_run_not_whole_node(
        self, fake_execution_blocker: type
    ) -> None:
        models, _c, images, texts, _p, _l, _v = run(image=None, text=["t0", "t1"])
        assert models == ["m0", "m0", "m1", "m1"]  # real values still flow
        assert all(isinstance(i, fake_execution_blocker) for i in images)
        assert len(images) == len(texts) == 4  # index alignment preserved

    def test_text_only_with_wired_vae_composes(self) -> None:
        # the headline flow: Checkpoint Switcher (model+clip+vae+label) x
        # Notebook texts, no images
        models, _clips, _i, _texts, prefixes, _labels, vaes = run(
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
        assert prefixes[0] == "ckptA/Portrait"
        assert prefixes[3] == "ckptB/Landscape"

    def test_names_still_shape_save_prefix_in_text_only_mode(self) -> None:
        _m, _c, _i, _t, prefixes, _l, _v = run(image=None, text=["x"], name=[])
        assert prefixes == ["lora_0.0/pair_01", "lora_0.5/pair_01"]


class TestNoSweepPureMultiplier:
    """v0.49.0: no sweep member wired = Cross Product's old job, in here."""

    def test_multiply_matches_cross_product_byte_for_byte(self, fake_execution_blocker) -> None:
        """The golden parity pin: multiply mode with no sweep reproduces
        EPSCrossProduct's image/text/name outputs EXACTLY (same image-major
        order, same names-per-text alignment, same padding)."""
        from eps_image.nodes_cross import EPSCrossProduct

        images = ["i1", "i2"]
        texts = ["tA", "tB", "tC"]
        names = ["A", "B"]  # deliberately short: pads with ""
        cp_images, cp_texts, cp_names = EPSCrossProduct().run(
            images=images, texts=texts, names=names
        )
        models, clips, out_images, out_texts, prefixes, labels, vaes = EPSCrossSweep().run(
            text=texts, image=images, name=names, pair_mode="multiply"
        )
        assert out_images == cp_images
        assert out_texts == cp_texts
        # name doesn't ride out directly -- it becomes save_prefix's pair
        # component; "" falls back to pair_NN exactly like paired mode.
        expected_components = [n or f"pair_{p + 1:02d}" for p, n in enumerate(cp_names)]
        assert prefixes == expected_components
        # Every sweep-side output blocks per run, keeping alignment.
        assert len(models) == len(cp_images)
        assert all(isinstance(m, fake_execution_blocker) for m in models)
        assert all(isinstance(c, fake_execution_blocker) for c in clips)
        assert all(isinstance(lb, fake_execution_blocker) for lb in labels)
        assert all(isinstance(v, fake_execution_blocker) for v in vaes)

    def test_no_sweep_save_prefix_has_no_step_level(self) -> None:
        _m, _c, _i, _t, prefixes, _l, _v = EPSCrossSweep().run(
            text=["tA"], image=["i1"], base_folder=["shoot"], pair_mode="multiply"
        )
        assert prefixes == ["shoot/pair_01"]

    def test_no_sweep_paired_zips_like_before(self) -> None:
        _m, _c, images, texts, prefixes, _l, _v = EPSCrossSweep().run(
            text=["tA", "tB"], image=["i1", "i2"], pair_mode="paired"
        )
        assert images == ["i1", "i2"]
        assert texts == ["tA", "tB"]
        assert prefixes == ["pair_01", "pair_02"]

    def test_no_sweep_empty_pair_side_blocks_whole_node(self, fake_execution_blocker) -> None:
        outputs = EPSCrossSweep().run(text=[], image=[], pair_mode="multiply")
        assert all(
            len(lst) == 1 and isinstance(lst[0], fake_execution_blocker) for lst in outputs
        )


class TestMultiplyWithSweep:
    """v0.49.0's new capability: sweep x images x texts in ONE node."""

    def test_three_axis_counts_and_order(self) -> None:
        models, _clips, images, texts, _prefixes, _labels, _v = run(
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
        _m, _c, _i, _t, prefixes, _l, _v = run(
            image=["i1", "i2"], text=["tA", "tB"], name=["portrait", "landscape"],
            pair_mode="multiply",
        )
        # names follow the TEXT axis: i1/tA, i1/tB, i2/tA, i2/tB per step.
        assert [p.split("/")[-1] for p in prefixes[:4]] == [
            "portrait", "landscape", "portrait", "landscape",
        ]

    def test_default_pair_mode_is_paired(self) -> None:
        """Omitting pair_mode (every workflow saved before v0.49.0) keeps
        the zip semantics -- 2x2 stays 2 pairs, not 4."""
        _m, _c, images, _texts, _p, _l, _v = run()
        assert len(images) == 4  # 2 steps x 2 PAIRS


class TestPartialSweepGroup:
    """v0.49.0: each unwired sweep member blocks its own output only."""

    def test_label_unwired_falls_back_to_step_numbers(self, fake_execution_blocker) -> None:
        models, _clips, _i, _t, prefixes, labels, _v = run(label=None)
        assert models == ["m0", "m0", "m1", "m1"]  # steps from model/clip alone
        assert all(isinstance(lb, fake_execution_blocker) for lb in labels)
        assert [p.split("/")[0] for p in prefixes] == [
            "step_01", "step_01", "step_02", "step_02",
        ]

    def test_model_unwired_blocks_model_output_only(self, fake_execution_blocker) -> None:
        models, clips, _i, _t, _prefixes, labels, _v = run(model=None)
        assert all(isinstance(m, fake_execution_blocker) for m in models)
        assert clips == ["c0", "c0", "c1", "c1"]
        assert labels == ["lora_0.0", "lora_0.0", "lora_0.5", "lora_0.5"]
