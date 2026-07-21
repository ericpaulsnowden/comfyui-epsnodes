"""Mathematical regression test for ``LoraLibraryApplySet`` (FORMAT.md §6.2).

Every other test in this suite (``test_nodes_sets.py``) fakes ``comfy.sd``/
``comfy.utils`` via ``sys.modules`` and asserts on the *arguments* the node
would have handed to ``comfy.sd.load_lora_for_models`` -- it proves the
node's own strength arithmetic (``row.strength * strength_scale``, the
``strength_clip`` fallback) but never proves that applying a set through the
node actually produces the mathematically correct PATCHED WEIGHTS once real
comfy code runs. This module closes that gap: it drives the real node
(``LoraLibraryApplySet.apply`` -> ``_apply_stack``) against a real, minimal
``comfy.model_patcher.ModelPatcher`` and a real (``no_init=True``)
``comfy.sd.CLIP``, with a hand-crafted ``.safetensors`` LoRA fixture on
disk, and checks the FINAL patched tensors against an expectation computed
independently with plain ``torch`` -- never by calling ``comfy.lora``/
``comfy.sd`` helpers, since a check that mirrors the implementation would
prove nothing. The formula under test, for one stacked LoRA row:

    W_patched = W_base + strength_model * (alpha / rank) * (up @ down)

verified against ``comfy/weight_adapter/lora.py``'s ``LoRAAdapter.
calculate_weight`` (the code that actually runs): ``alpha = v[2] /
mat2.shape[0]`` where ``mat2`` is the down matrix (so ``alpha/rank``), and
``weight += function(((strength * alpha) * lora_diff).type(weight.dtype))``
where ``lora_diff = mat1 @ mat2`` (``up @ down``) and ``strength`` is the
per-patch strength ComfyUI's own ``ModelPatcher.add_patches`` recorded --
i.e. exactly the node's ``strength_model``/``strength_clip`` stack values.

Environment reality
--------------------
Neither ``torch`` nor ``comfy`` is available in this pack's own dev venv
(``comfyui-epsnodes/venv``) -- ``pytest.importorskip("torch")`` below
degrades this whole module to a single clean SKIP there, the same
convention ``tests/test_frame_saver.py`` uses for ``av``/``torch``. Getting
from "torch is installed" to "comfy is importable" needs one more step,
since ComfyUI is a checkout, not a pip package: this module tries a bare
``import comfy.sd`` first (works if the runner already put a ComfyUI root
on ``sys.path``), then falls back to the ``EPS_COMFYUI_ROOT`` env var
(point it at a ComfyUI checkout's root -- the directory containing
``comfy/``, ``folder_paths.py``, etc. -- and this module adds it to
``sys.path`` itself), and only then skips, with a message saying exactly
how to enable it. This is intentionally portable: Eric's Windows PC has its
own real ComfyUI install, and this module should run there the same way it
runs on the dev rig, via the same env var.

Coverage honesty
----------------
MODEL side: full numeric weight-patch depth, via a real ``ModelPatcher``
wrapping a minimal fake ``diffusion_model`` and a real
``.patch_model(device_to="cpu")`` call (the same method ComfyUI calls
before running a KSampler).

CLIP side: ALSO full numeric weight-patch depth, not just the stack-level
strengths the task's own instructions treat as an acceptable fallback.
``comfy.sd.CLIP`` supports a ``no_init=True`` constructor (comfy/sd.py,
used internally by ``CLIP.clone()`` itself) that skips tokenizer/transformer
loading entirely; setting ``.cond_stage_model``/``.patcher`` by hand on the
result gives a real ``CLIP`` object whose ``.add_patches()`` is the actual
``self.patcher.add_patches(...)`` passthrough -- no reimplemented patching
logic anywhere in this file.

What this does NOT cover (so this claim stays honest): only the standard
kohya-style up/down/alpha ("regular_lora") adapter path is exercised -- no
LoHa/LoKr/OFT/BOFT adapters, no ``dora_scale``, no plain ``diff``/``set``
patch types, no conv-shaped weights (only a plain ``Linear``), no lowvram
partial-offload path, and no GPU/fp8/bf16 dtype-cast path (float32 CPU
throughout, where ``comfy.float.stochastic_rounding`` is a documented
no-op cast, so nothing there can introduce nondeterminism). Those are all
real ComfyUI code paths this test simply doesn't drive.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

pytest.importorskip("torch")
pytest.importorskip("safetensors")

import torch

try:
    import comfy.sd
except ImportError:
    _eps_comfyui_root = os.environ.get("EPS_COMFYUI_ROOT")
    if not _eps_comfyui_root:
        pytest.skip(
            "comfy is not importable and EPS_COMFYUI_ROOT is not set -- this "
            "module needs a real ComfyUI checkout to verify actual patched "
            "LoRA weights (mocks would prove nothing here). Set "
            "EPS_COMFYUI_ROOT=/path/to/ComfyUI (the directory containing "
            "comfy/, folder_paths.py, etc.) and re-run, or run from an "
            "environment where `import comfy` already works.",
            allow_module_level=True,
        )
    # Only touch sys.path if it isn't already there, and undo it as soon as
    # `comfy.sd` is safely cached in sys.modules (whether that import
    # succeeds or fails below). `comfy`'s OWN submodules (comfy.model_patcher
    # etc., imported next) resolve via the now-cached `comfy` package's own
    # __path__, not a fresh sys.path scan, so removing this entry doesn't
    # break anything this module still needs to import. Leaving ComfyUI's
    # root on sys.path for the rest of the pytest session would instead
    # silently break OTHER modules' own sanity checks that unrelated ComfyUI
    # packages (e.g. comfy_execution) are genuinely NOT importable in a
    # plain test env -- test_image_grid.py's
    # test_does_not_raise_without_the_execution_blocker_fixture is exactly
    # that check, confirmed by tripping it during this module's own
    # development (see the task report).
    _path_already_present = _eps_comfyui_root in sys.path
    if not _path_already_present:
        sys.path.insert(0, _eps_comfyui_root)
    try:
        import comfy.sd
    except ImportError as exc:
        pytest.skip(
            f"EPS_COMFYUI_ROOT={_eps_comfyui_root!r} is set but `import "
            f"comfy.sd` still fails ({exc}) -- check the path points at a "
            "ComfyUI checkout's root (the directory containing comfy/, not "
            "comfy/ itself).",
            allow_module_level=True,
        )
    finally:
        if not _path_already_present and _eps_comfyui_root in sys.path:
            sys.path.remove(_eps_comfyui_root)

import comfy.model_patcher
from safetensors.torch import save_file

from lora_library import nodes_sets, sets_store
from lora_library.context import LibraryContext

#: Shape of the single fake weight every fixture lora targets (task's own
#: suggested minimal shape). Small and non-square on purpose: a transposed
#: up/down multiply would produce a shape error, not a silently-wrong
#: number.
OUT_FEATURES = 8
IN_FEATURES = 6


# ------------------------------------------------------------- fake comfy model


class _FakeLinear(torch.nn.Module):
    """A single ``Linear(in=6, out=8, bias=False)`` with deterministic
    seeded weights. Used directly as a minimal CLIP ``cond_stage_model``,
    and (wrapped by :class:`_FakeUnetModel`) as a minimal UNet
    ``diffusion_model``.
    """

    def __init__(self, out_features: int, in_features: int, seed: int) -> None:
        super().__init__()
        self.proj = torch.nn.Linear(in_features, out_features, bias=False)
        with torch.no_grad():
            self.proj.weight.copy_(
                torch.randn(
                    out_features, in_features, generator=torch.Generator().manual_seed(seed)
                )
            )


class _FakeModelConfig:
    """Stands in for ``comfy.model_base.BaseModel.model_config``.

    Only ``.unet_config`` is ever read, by ``model_lora_keys_unet``
    (comfy/lora.py:203, ``comfy.utils.unet_to_diffusers(model.model_config.
    unet_config)``) -- an empty dict short-circuits that call cleanly
    (comfy/utils.py:337-338: ``if "num_res_blocks" not in unet_config:
    return {}``), confirmed empirically on the rig (see this module's test
    run in the task report) before this fixture was relied on.
    """

    def __init__(self) -> None:
        self.unet_config: dict = {}


class _FakeUnetModel(torch.nn.Module):
    """Stands in for ``comfy.model_base.BaseModel``.

    A real ``nn.Module`` whose ``.diffusion_model`` submodule makes
    ``state_dict()`` naturally yield a ``diffusion_model.proj.weight`` key
    -- exactly the prefix ``model_lora_keys_unet`` looks for
    (comfy/lora.py:191-195): ``key_lora = k[len("diffusion_model."):
    -len(".weight")].replace(".", "_"); key_map["lora_unet_{}".format(
    key_lora)] = k``. For this fixture's single-segment name "proj" (no
    dots), that is exactly ``lora_unet_proj -> diffusion_model.proj.weight``
    -- verified empirically on the rig (see task report), not guessed.

    Not an instance of ``comfy.model_base.StableCascade_C``/``SD3``/
    ``AuraFlow``/``PixArt``, so ``model_lora_keys_unet``'s extra
    architecture-specific branches never fire -- exactly the "minimal"
    ComfyUI already documents this call requires nothing else.
    """

    def __init__(self, out_features: int, in_features: int, seed: int) -> None:
        super().__init__()
        self.diffusion_model = _FakeLinear(out_features, in_features, seed)
        self.model_config = _FakeModelConfig()


def _cpu_model_patcher(model: torch.nn.Module) -> comfy.model_patcher.ModelPatcher:
    return comfy.model_patcher.ModelPatcher(
        model, load_device=torch.device("cpu"), offload_device=torch.device("cpu")
    )


def _no_init_clip(cond_stage_model: torch.nn.Module) -> comfy.sd.CLIP:
    """A real ``comfy.sd.CLIP`` built via its own ``no_init=True`` escape
    hatch (comfy/sd.py:227-229; the same one ``CLIP.clone()`` itself uses,
    comfy/sd.py:292-293) instead of the full constructor, which would load
    a real tokenizer + text-encoder transformer -- disproportionate for a
    unit test whose only concern is LoRA weight-patch arithmetic. Every
    attribute ``CLIP.clone()``/``CLIP.add_patches()`` touch is set by hand
    below so those real (unmodified) methods work exactly as they do on a
    fully-constructed CLIP.
    """
    clip = comfy.sd.CLIP(no_init=True)
    clip.cond_stage_model = cond_stage_model
    clip.patcher = _cpu_model_patcher(cond_stage_model)
    clip.tokenizer = None
    clip.layer_idx = None
    clip.tokenizer_options = {}
    clip.use_clip_schedule = False
    clip.apply_hooks_to_conds = None
    return clip


# ------------------------------------------------------------------ lora fixture


@dataclass
class _LoraFixture:
    """One crafted ``.safetensors`` lora file plus the raw tensors used to
    build it, for computing the independent (plain-torch) expectation.
    """

    path: Path
    unet_up: torch.Tensor
    unet_down: torch.Tensor
    clip_up: torch.Tensor
    clip_down: torch.Tensor
    alpha: float
    rank: int

    @property
    def unet_delta(self) -> torch.Tensor:
        """``(alpha / rank) * (up @ down)`` -- first-principles LoRA delta
        for the UNet side, computed here with plain torch only (never via
        ``comfy.lora``/``comfy.sd``)."""
        return (self.alpha / self.rank) * (self.unet_up @ self.unet_down)

    @property
    def clip_delta(self) -> torch.Tensor:
        """Same formula, CLIP side, from this file's separate (seeded
        differently) up/down tensors -- proves the two sides are patched
        from their own tensors, not cross-contaminated."""
        return (self.alpha / self.rank) * (self.clip_up @ self.clip_down)


def _make_lora_file(path: Path, *, rank: int, alpha: float, seed_base: int) -> _LoraFixture:
    """Writes a real ``.safetensors`` file with kohya/"regular_lora"-style
    keys for BOTH the UNet and text-encoder sides of one lora, matching the
    naming ``model_lora_keys_unet``/``model_lora_keys_clip`` produce for
    this fixture's fake models (see the docstrings above and the task
    report for the empirical verification on the rig):

    - ``lora_unet_proj.lora_up.weight`` / ``.lora_down.weight`` / ``.alpha``
    - ``text_encoders.proj.lora_up.weight`` / ``.lora_down.weight`` /
      ``.alpha``

    ``alpha`` is deliberately independent of ``rank`` (the task's own
    concern: alpha == rank would silently pass even if the code forgot to
    divide by rank at all, since e.g. 2/2 == 1). Four distinctly-seeded
    tensors (unet up/down, clip up/down) keep every number in this fixture
    numerically distinguishable from every other, so a swapped tensor or a
    model/clip cross-wire would show up as a wrong number, not an
    accidental match.
    """

    def seeded_randn(*shape: int, seed: int) -> torch.Tensor:
        return torch.randn(*shape, generator=torch.Generator().manual_seed(seed))

    unet_up = seeded_randn(OUT_FEATURES, rank, seed=seed_base + 1)
    unet_down = seeded_randn(rank, IN_FEATURES, seed=seed_base + 2)
    clip_up = seeded_randn(OUT_FEATURES, rank, seed=seed_base + 3)
    clip_down = seeded_randn(rank, IN_FEATURES, seed=seed_base + 4)
    tensors = {
        "lora_unet_proj.lora_up.weight": unet_up,
        "lora_unet_proj.lora_down.weight": unet_down,
        "lora_unet_proj.alpha": torch.tensor(alpha),
        "text_encoders.proj.lora_up.weight": clip_up,
        "text_encoders.proj.lora_down.weight": clip_down,
        "text_encoders.proj.alpha": torch.tensor(alpha),
    }
    save_file(tensors, str(path))
    return _LoraFixture(path, unet_up, unet_down, clip_up, clip_down, alpha, rank)


#: FORMAT.md §4 row filenames used throughout this module.
LORA_A = "test_lora_a.safetensors"
LORA_B = "test_lora_b.safetensors"
LORA_C_DISABLED = "test_lora_c.safetensors"

#: The scenario's strengths (module-level so both the fixture and the
#: independent-expectation math below read the same numbers). Non-trivial
#: and asymmetric on purpose (task's own instruction): strength_scale=0.5,
#: row A's strength_clip is None (falls back to strength, THEN scaled), row
#: B's strength_clip (0.6) differs from its strength (1.0) even before
#: scaling -- ordering/multiplication errors can't cancel these out.
STRENGTH_SCALE = 0.5
ROW_A_STRENGTH = 0.8
ROW_B_STRENGTH = 1.0
ROW_B_STRENGTH_CLIP = 0.6


@pytest.fixture
def lora_fixtures(tmp_path: Path) -> dict[str, _LoraFixture]:
    """Three real on-disk lora files: A and B (rank/alpha deliberately
    different from each other -- rank=2/alpha=1.0 vs rank=3/alpha=3.0, so
    two distinct alpha/rank factors are exercised, 0.5 and 1.0) and a
    DISABLED-row lora C whose alpha (50.0) is huge specifically so that if
    a future regression ever applied it by mistake, the resulting weight
    would be wildly, unmissably wrong rather than close-but-off.
    """
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()
    return {
        LORA_A: _make_lora_file(fixtures_dir / LORA_A, rank=2, alpha=1.0, seed_base=100),
        LORA_B: _make_lora_file(fixtures_dir / LORA_B, rank=3, alpha=3.0, seed_base=200),
        LORA_C_DISABLED: _make_lora_file(
            fixtures_dir / LORA_C_DISABLED, rank=2, alpha=50.0, seed_base=300
        ),
    }


@pytest.fixture
def wired_context(
    context: LibraryContext, lora_fixtures: dict[str, _LoraFixture]
) -> LibraryContext:
    """The shared ``context`` fixture (tests/conftest.py), re-pointed at
    THIS module's real fixture files instead of the shared fake names --
    same "mutate the dataclass instance" pattern ``test_nodes_sets.py``'s
    own ``_wire_context`` fixture uses for ``resolve_lora_path``. Wires and
    unwires ``nodes_sets``' module-level context around the test, same as
    that fixture.
    """
    names = list(lora_fixtures.keys())
    context.list_loras = lambda: list(names)
    context.resolve_lora_path = lambda name: str(lora_fixtures[name].path)
    nodes_sets.set_context(context)
    yield context
    nodes_sets.set_context(None)


def _save_math_set(context: LibraryContext) -> str:
    """The FORMAT.md §4 set this module drives through the real node: row A
    (single strength, falls back for clip), row B (dual strength, enabled),
    row C (disabled -- must never be resolved, loaded, or patched)."""
    slug, _ = sets_store.save_set(
        context,
        {
            "name": "Math Set",
            "loras": [
                {"file": LORA_A, "on": True, "strength": ROW_A_STRENGTH, "strength_clip": None},
                {
                    "file": LORA_B,
                    "on": True,
                    "strength": ROW_B_STRENGTH,
                    "strength_clip": ROW_B_STRENGTH_CLIP,
                },
                {
                    "file": LORA_C_DISABLED,
                    "on": False,
                    "strength": 1.0,
                    "strength_clip": 1.0,
                },
            ],
            "trigger_words": "mathtest",
            "notes": "",
        },
    )
    return slug


# --------------------------------------------------------------- the scenario


@dataclass
class _AppliedScenario:
    w_model_final: torch.Tensor
    w_clip_final: torch.Tensor
    expected_model: torch.Tensor
    expected_clip: torch.Tensor
    stack: list[tuple[str, float, float]]
    trigger_words: str
    loras_text: str
    row_c: _LoraFixture


@pytest.fixture
def applied_scenario(
    wired_context: LibraryContext, lora_fixtures: dict[str, _LoraFixture]
) -> _AppliedScenario:
    """Drives the REAL node end to end and materializes REAL patched
    weights on both sides, once, for every test below to assert different
    facets of:

    1. Build a fresh base UNet model + a fresh (no_init) CLIP, both with
       known, captured-before-any-patching base weights.
    2. Save the set (row A, row B, disabled row C) and call
       ``LoraLibraryApplySet().apply(...)`` -- the exact node/method under
       test -- with real ``model``/``clip`` objects wired.
    3. Materialize the patches into final tensors via
       ``ModelPatcher.patch_model(device_to="cpu")`` -- the same call
       ComfyUI itself makes before sampling -- on both the returned model
       and the returned clip's patcher.
    4. Compute the expectation independently: base weight plus each
       ENABLED row's ``strength_side * (alpha/rank) * (up @ down)``, pure
       torch, no comfy helpers.
    """
    slug = _save_math_set(wired_context)

    base = _FakeUnetModel(OUT_FEATURES, IN_FEATURES, seed=1234)
    w_base_expected = base.diffusion_model.proj.weight.detach().clone()
    model_patcher = _cpu_model_patcher(base)

    clip_inner = _FakeLinear(OUT_FEATURES, IN_FEATURES, seed=4321)
    w_clip_base_expected = clip_inner.proj.weight.detach().clone()
    clip = _no_init_clip(clip_inner)

    node = nodes_sets.LoraLibraryApplySet()
    model_out, clip_out, stack, trigger_words, loras_text = node.apply(
        set=slug, strength_scale=STRENGTH_SCALE, model=model_patcher, clip=clip
    )
    assert model_out is not None
    assert clip_out is not None

    final_model = model_out.patch_model(device_to=torch.device("cpu"))
    w_model_final = final_model.diffusion_model.proj.weight.detach().clone()

    clip_out.patcher.patch_model(device_to=torch.device("cpu"))
    w_clip_final = clip_out.cond_stage_model.proj.weight.detach().clone()

    row_a = lora_fixtures[LORA_A]
    row_b = lora_fixtures[LORA_B]

    strength_model_a = ROW_A_STRENGTH * STRENGTH_SCALE
    strength_clip_a = ROW_A_STRENGTH * STRENGTH_SCALE  # strength_clip None -> falls back
    strength_model_b = ROW_B_STRENGTH * STRENGTH_SCALE
    strength_clip_b = ROW_B_STRENGTH_CLIP * STRENGTH_SCALE

    expected_model = (
        w_base_expected + strength_model_a * row_a.unet_delta + strength_model_b * row_b.unet_delta
    )
    expected_clip = (
        w_clip_base_expected
        + strength_clip_a * row_a.clip_delta
        + strength_clip_b * row_b.clip_delta
    )

    return _AppliedScenario(
        w_model_final=w_model_final,
        w_clip_final=w_clip_final,
        expected_model=expected_model,
        expected_clip=expected_clip,
        stack=stack,
        trigger_words=trigger_words,
        loras_text=loras_text,
        row_c=lora_fixtures[LORA_C_DISABLED],
    )


# ----------------------------------------------------------------------- tests


def test_patched_model_weights_match_independent_torch_expectation(
    applied_scenario: _AppliedScenario,
) -> None:
    """The headline assertion: real ``comfy.sd.load_lora_for_models`` +
    ``ModelPatcher.patch_model()`` weights, reached through the real node,
    equal ``W_base + strength_model*(alpha/rank)*(up@down)`` summed over
    both enabled stacked rows -- computed independently with plain torch.
    """
    torch.testing.assert_close(
        applied_scenario.w_model_final, applied_scenario.expected_model, rtol=1e-5, atol=1e-5
    )


def test_patched_clip_weights_match_independent_torch_expectation(
    applied_scenario: _AppliedScenario,
) -> None:
    """Same formula, CLIP side, via a real (no_init) ``comfy.sd.CLIP`` --
    see the module docstring's "coverage honesty" section: this is full
    weight-level depth, not just the stack-level strengths."""
    torch.testing.assert_close(
        applied_scenario.w_clip_final, applied_scenario.expected_clip, rtol=1e-5, atol=1e-5
    )


def test_disabled_row_contributes_no_weight_delta(applied_scenario: _AppliedScenario) -> None:
    """Proves the disabled row (C) is excluded at the ACTUAL WEIGHT level,
    not just absent from a mock's call log: if C's huge-alpha delta were
    wrongly folded in, the result would be wildly off from what was
    actually produced (asserted with a coarse threshold since the point is
    "would be unmistakable", not a tight numeric match)."""
    row_c = applied_scenario.row_c
    wrongly_included = applied_scenario.expected_model + 1.0 * row_c.unet_delta
    diff_if_wrongly_included = (applied_scenario.w_model_final - wrongly_included).abs().max()
    assert diff_if_wrongly_included.item() > 1.0

    # And the correctly-excluded expectation is what the real weights
    # actually match (belt-and-suspenders with the test above).
    torch.testing.assert_close(
        applied_scenario.w_model_final, applied_scenario.expected_model, rtol=1e-5, atol=1e-5
    )


def test_disabled_row_absent_from_returned_stack(applied_scenario: _AppliedScenario) -> None:
    assert [row[0] for row in applied_scenario.stack] == [LORA_A, LORA_B]


def test_stack_strengths_include_strength_scale_and_dual_clip_strength(
    applied_scenario: _AppliedScenario,
) -> None:
    (_, strength_model_a, strength_clip_a), (_, strength_model_b, strength_clip_b) = (
        applied_scenario.stack
    )
    assert strength_model_a == pytest.approx(0.4)  # 0.8 * 0.5
    assert strength_clip_a == pytest.approx(0.4)  # fallback (0.8) * 0.5
    assert strength_model_b == pytest.approx(0.5)  # 1.0 * 0.5
    assert strength_clip_b == pytest.approx(0.3)  # dual strength (0.6) * 0.5, differs from model


def test_loras_text_reflects_scaled_strengths_despite_real_weight_fixtures(
    applied_scenario: _AppliedScenario,
) -> None:
    """Sanity only (task's own instruction: cheap check that this module's
    unusual real-weight fixtures didn't perturb unrelated string-building
    logic already covered thoroughly by test_nodes_sets.py)."""
    assert applied_scenario.loras_text == "test_lora_a_0.4 test_lora_b_0.5_0.3"
    assert applied_scenario.trigger_words == "mathtest"


def test_strength_scale_omitted_matches_explicit_one_point_zero_in_patched_weights(
    wired_context: LibraryContext,
) -> None:
    """FORMAT.md §6.2 (2026-07-20 amendment): strength_scale is optional
    with Python default 1.0. test_nodes_sets.py already proves the
    recorded mock call args match between omitted/explicit; this proves it
    holds all the way down to the real patched weight, not just the
    strength number a mock recorded. Two fresh same-seed base models keep
    this independent of the aliasing behavior of ModelPatcher.clone()
    (which shares the underlying nn.Module rather than deep-copying it --
    fine for the single-call scenario above, but two apply() calls reusing
    one base model would see the first call's materialized patch as the
    second call's "base", which is not what this comparison wants).
    """
    slug, _ = sets_store.save_set(
        wired_context,
        {
            "name": "Single Row",
            "loras": [
                {"file": LORA_A, "on": True, "strength": ROW_A_STRENGTH, "strength_clip": None}
            ],
            "trigger_words": "",
            "notes": "",
        },
    )

    def patched_weight(**apply_kwargs: object) -> torch.Tensor:
        base = _FakeUnetModel(OUT_FEATURES, IN_FEATURES, seed=1234)
        model_patcher = _cpu_model_patcher(base)
        node = nodes_sets.LoraLibraryApplySet()
        model_out, *_rest = node.apply(set=slug, model=model_patcher, **apply_kwargs)
        patched = model_out.patch_model(device_to=torch.device("cpu"))
        return patched.diffusion_model.proj.weight.detach().clone()

    w_omitted = patched_weight()  # strength_scale intentionally omitted
    w_explicit = patched_weight(strength_scale=1.0)
    torch.testing.assert_close(w_omitted, w_explicit, rtol=0, atol=0)
