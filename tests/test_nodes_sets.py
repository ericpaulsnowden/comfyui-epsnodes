"""Tests for lora_library.nodes_sets (FORMAT.md §6.2).

``comfy.utils``/``comfy.sd`` are faked via ``sys.modules`` (this package
must stay importable and testable without a real ComfyUI/torch install —
see nodes_sets.py's module docstring). The fakes are recorders: every call
into the faked ``load_lora_for_models`` is appended to a shared list in call
order, so tests can assert ordering, scaled strengths, and None-passthrough
directly.
"""

from __future__ import annotations

import json
import logging
import sys
import types

import pytest

from lora_library import nodes_sets, sets_store
from lora_library.context import LibraryContext


class FakeModel:
    def __init__(self, tag: str = "model") -> None:
        self.tag = tag


class FakeClip:
    def __init__(self, tag: str = "clip") -> None:
        self.tag = tag


@pytest.fixture(autouse=True)
def _wire_context(context: LibraryContext):
    # Identity resolve_lora_path keeps assertions readable: the "path" the
    # faked comfy.utils.load_torch_file receives is exactly the resolved
    # lora name (the conftest `context` fixture's default resolve_lora_path
    # always returns None, which would make every row unresolvable).
    context.resolve_lora_path = lambda name: name
    nodes_sets.set_context(context)
    yield
    nodes_sets.set_context(None)


@pytest.fixture
def fake_comfy(monkeypatch: pytest.MonkeyPatch):
    """Installs recorder fakes for ``comfy.utils``/``comfy.sd``.

    Returns ``(calls, loaded_files)``: *calls* collects one
    ``(path, model, clip, strength_model, strength_clip)`` tuple per
    ``load_lora_for_models`` call, in call order; *loaded_files* collects
    every path handed to ``load_torch_file``.
    """
    calls: list[tuple] = []
    loaded_files: list[str] = []

    fake_utils = types.ModuleType("comfy.utils")

    def load_torch_file(path, safe_load=True):
        assert safe_load is True
        loaded_files.append(path)
        return {"path": path}

    fake_utils.load_torch_file = load_torch_file

    fake_sd = types.ModuleType("comfy.sd")

    def load_lora_for_models(model, clip, lora_sd, strength_model, strength_clip):
        calls.append((lora_sd["path"], model, clip, strength_model, strength_clip))
        new_model = FakeModel(f"{model.tag}+{lora_sd['path']}") if model is not None else None
        new_clip = FakeClip(f"{clip.tag}+{lora_sd['path']}") if clip is not None else None
        return new_model, new_clip

    fake_sd.load_lora_for_models = load_lora_for_models

    fake_comfy_pkg = types.ModuleType("comfy")
    fake_comfy_pkg.utils = fake_utils
    fake_comfy_pkg.sd = fake_sd

    monkeypatch.setitem(sys.modules, "comfy", fake_comfy_pkg)
    monkeypatch.setitem(sys.modules, "comfy.utils", fake_utils)
    monkeypatch.setitem(sys.modules, "comfy.sd", fake_sd)
    return calls, loaded_files


def _make_set(context: LibraryContext, **overrides) -> str:
    payload = {
        "name": "Test Set",
        "loras": [
            {"file": "detailer.safetensors", "on": True, "strength": 0.8, "strength_clip": None},
            {
                "file": "styles/cinematic.safetensors",
                "on": False,
                "strength": 1.0,
                "strength_clip": 0.5,
            },
            {
                "file": "styles/film_grain.safetensors",
                "on": True,
                "strength": 0.4,
                "strength_clip": 0.6,
            },
        ],
        "trigger_words": "cinematic, detailed",
        "notes": "",
    }
    payload.update(overrides)
    slug, _ = sets_store.save_set(context, payload)
    return slug


def calls_paths(calls: list[tuple]) -> list[str]:
    """The resolved-file element of each recorded ``load_lora_for_models`` call."""
    return [c[0] for c in calls]


# ----------------------------------------------------------------- "None" / missing


def test_none_set_passes_through_with_empty_stack_and_no_comfy_calls(
    context: LibraryContext, fake_comfy
) -> None:
    node = nodes_sets.LoraLibraryApplySet()
    model_in, clip_in = FakeModel(), FakeClip()
    model_out, clip_out, stack, trigger_words = node.apply(
        set="None", strength_scale=1.0, model=model_in, clip=clip_in
    )
    assert model_out is model_in
    assert clip_out is clip_in
    assert stack == []
    assert trigger_words == ""
    calls, _ = fake_comfy
    assert calls == []


def test_missing_set_file_logs_warning_and_passes_through(
    context: LibraryContext, fake_comfy, caplog: pytest.LogCaptureFixture
) -> None:
    node = nodes_sets.LoraLibraryApplySet()
    with caplog.at_level(logging.WARNING, logger="lora_library"):
        model_out, clip_out, stack, trigger_words = node.apply(
            set="does-not-exist", strength_scale=1.0, model=None, clip=None
        )
    assert model_out is None
    assert clip_out is None
    assert stack == []
    assert trigger_words == ""
    assert any("does-not-exist" in r.message for r in caplog.records)
    calls, _ = fake_comfy
    assert calls == []


def test_corrupt_set_file_degrades_like_missing_rather_than_raising(
    context: LibraryContext, fake_comfy, caplog: pytest.LogCaptureFixture
) -> None:
    bad_path = sets_store.set_path(context, "broken")
    bad_path.write_text(json.dumps({"format": 99, "loras": []}), encoding="utf-8")
    node = nodes_sets.LoraLibraryApplySet()
    with caplog.at_level(logging.WARNING, logger="lora_library"):
        model_out, _, stack, trigger_words = node.apply(set="broken", strength_scale=1.0)
    assert model_out is None
    assert stack == []
    assert trigger_words == ""


# ------------------------------------------------------------- core apply behavior


def test_apply_with_model_and_clip_scales_strengths_and_preserves_order(
    context: LibraryContext, fake_comfy
) -> None:
    slug = _make_set(context)
    node = nodes_sets.LoraLibraryApplySet()
    model_in, clip_in = FakeModel("m0"), FakeClip("c0")

    model_out, clip_out, stack, trigger_words = node.apply(
        set=slug, strength_scale=0.5, model=model_in, clip=clip_in
    )
    calls, loaded_files = fake_comfy

    # The middle row is off -> excluded entirely (never resolved, never loaded).
    assert [c[0] for c in calls] == ["detailer.safetensors", "styles/film_grain.safetensors"]
    assert loaded_files == calls_paths(calls)

    # strength_clip None -> falls back to strength; both scaled by 0.5.
    assert calls[0][3] == pytest.approx(0.4)  # 0.8 * 0.5
    assert calls[0][4] == pytest.approx(0.4)  # (fallback) 0.8 * 0.5
    assert calls[1][3] == pytest.approx(0.2)  # 0.4 * 0.5
    assert calls[1][4] == pytest.approx(0.3)  # 0.6 * 0.5

    assert stack == [
        ("detailer.safetensors", pytest.approx(0.4), pytest.approx(0.4)),
        ("styles/film_grain.safetensors", pytest.approx(0.2), pytest.approx(0.3)),
    ]
    assert trigger_words == "cinematic, detailed"
    assert model_out is not None and model_out is not model_in
    assert clip_out is not None and clip_out is not clip_in


def test_strength_scale_multiplies_both_model_and_clip_strengths(
    context: LibraryContext, fake_comfy
) -> None:
    slug = _make_set(
        context,
        loras=[{"file": "detailer.safetensors", "on": True, "strength": 0.6, "strength_clip": 0.2}],
    )
    node = nodes_sets.LoraLibraryApplySet()
    node.apply(set=slug, strength_scale=1.5, model=FakeModel(), clip=FakeClip())
    calls, _ = fake_comfy
    assert calls[0][3] == pytest.approx(0.9)  # 0.6 * 1.5
    assert calls[0][4] == pytest.approx(0.3)  # 0.2 * 1.5


def test_no_model_or_clip_wired_is_a_pure_stack_and_trigger_source(
    context: LibraryContext, fake_comfy
) -> None:
    slug = _make_set(context)
    node = nodes_sets.LoraLibraryApplySet()
    model_out, clip_out, stack, trigger_words = node.apply(set=slug, strength_scale=1.0)
    calls, _ = fake_comfy
    assert calls == []  # comfy.* never touched when neither model nor clip is wired
    assert model_out is None
    assert clip_out is None
    assert len(stack) == 2
    assert trigger_words == "cinematic, detailed"


def test_clip_none_is_passed_through_and_propagates_from_the_recorder(
    context: LibraryContext, fake_comfy
) -> None:
    slug = _make_set(
        context,
        loras=[
            {"file": "detailer.safetensors", "on": True, "strength": 1.0, "strength_clip": None}
        ],
    )
    node = nodes_sets.LoraLibraryApplySet()
    model_in = FakeModel()
    model_out, clip_out, _, _ = node.apply(set=slug, strength_scale=1.0, model=model_in, clip=None)
    calls, _ = fake_comfy
    assert calls[0][2] is None  # clip argument load_lora_for_models saw was None
    assert clip_out is None  # and the fake's None-clip handling propagated back out
    assert model_out is not None


def test_model_none_is_passed_through_when_only_clip_is_wired(
    context: LibraryContext, fake_comfy
) -> None:
    slug = _make_set(
        context,
        loras=[
            {"file": "detailer.safetensors", "on": True, "strength": 1.0, "strength_clip": None}
        ],
    )
    node = nodes_sets.LoraLibraryApplySet()
    clip_in = FakeClip()
    model_out, clip_out, _, _ = node.apply(set=slug, strength_scale=1.0, model=None, clip=clip_in)
    calls, _ = fake_comfy
    assert calls[0][1] is None
    assert model_out is None
    assert clip_out is not None


def test_disabled_row_is_never_resolved_or_applied(context: LibraryContext, fake_comfy) -> None:
    slug = _make_set(
        context,
        loras=[
            {"file": "detailer.safetensors", "on": False, "strength": 1.0, "strength_clip": None}
        ],
    )
    node = nodes_sets.LoraLibraryApplySet()
    _, _, stack, _ = node.apply(set=slug, strength_scale=1.0, model=FakeModel(), clip=FakeClip())
    calls, _ = fake_comfy
    assert calls == []
    assert stack == []


def test_unresolvable_lora_is_skipped_with_warning_but_rest_of_set_still_applies(
    context: LibraryContext, fake_comfy, caplog: pytest.LogCaptureFixture
) -> None:
    slug = _make_set(
        context,
        loras=[
            {"file": "ghost.safetensors", "on": True, "strength": 1.0, "strength_clip": None},
            {"file": "detailer.safetensors", "on": True, "strength": 1.0, "strength_clip": None},
        ],
    )
    node = nodes_sets.LoraLibraryApplySet()
    with caplog.at_level(logging.WARNING, logger="lora_library"):
        _, _, stack, _ = node.apply(
            set=slug, strength_scale=1.0, model=FakeModel(), clip=FakeClip()
        )
    calls, _ = fake_comfy
    assert calls_paths(calls) == ["detailer.safetensors"]
    assert stack == [("detailer.safetensors", 1.0, 1.0)]
    assert any("ghost.safetensors" in r.message for r in caplog.records)


# ------------------------------------------------------------------- skip-on-zero


def test_zero_strength_row_is_not_loaded_but_still_reported_in_the_stack(
    context: LibraryContext, fake_comfy
) -> None:
    slug = _make_set(
        context,
        loras=[
            {"file": "detailer.safetensors", "on": True, "strength": 0.0, "strength_clip": 0.0}
        ],
    )
    node = nodes_sets.LoraLibraryApplySet()
    _, _, stack, _ = node.apply(set=slug, strength_scale=1.0, model=FakeModel(), clip=FakeClip())
    calls, loaded_files = fake_comfy
    assert calls == []
    assert loaded_files == []
    assert stack == [("detailer.safetensors", 0.0, 0.0)]


def test_strength_scale_zero_skips_loading_every_row(
    context: LibraryContext, fake_comfy
) -> None:
    slug = _make_set(context)  # rows have nonzero base strengths
    node = nodes_sets.LoraLibraryApplySet()
    node.apply(set=slug, strength_scale=0.0, model=FakeModel(), clip=FakeClip())
    calls, loaded_files = fake_comfy
    assert calls == []
    assert loaded_files == []


# ---------------------------------------------------------------------- IS_CHANGED


def test_is_changed_changes_when_the_set_file_is_rewritten(context: LibraryContext) -> None:
    slug = _make_set(context)
    token1 = nodes_sets.LoraLibraryApplySet.IS_CHANGED(set=slug, strength_scale=1.0)
    sets_store.save_set(
        context, {"name": "Test Set", "loras": [{"file": "detailer.safetensors"}]}, slug=slug
    )
    token2 = nodes_sets.LoraLibraryApplySet.IS_CHANGED(set=slug, strength_scale=1.0)
    assert token1 != token2


def test_is_changed_changes_with_strength_scale(context: LibraryContext) -> None:
    slug = _make_set(context)
    token1 = nodes_sets.LoraLibraryApplySet.IS_CHANGED(set=slug, strength_scale=1.0)
    token2 = nodes_sets.LoraLibraryApplySet.IS_CHANGED(set=slug, strength_scale=0.5)
    assert token1 != token2


def test_is_changed_is_stable_across_calls_when_nothing_changed(
    context: LibraryContext,
) -> None:
    slug = _make_set(context)
    token1 = nodes_sets.LoraLibraryApplySet.IS_CHANGED(set=slug, strength_scale=1.0)
    token2 = nodes_sets.LoraLibraryApplySet.IS_CHANGED(set=slug, strength_scale=1.0)
    assert token1 == token2


def test_is_changed_uses_a_missing_token_for_a_nonexistent_set(context: LibraryContext) -> None:
    token = nodes_sets.LoraLibraryApplySet.IS_CHANGED(set="does-not-exist", strength_scale=1.0)
    assert isinstance(token, str)
    assert "missing" in token


def test_is_changed_handles_none_without_raising(context: LibraryContext) -> None:
    token = nodes_sets.LoraLibraryApplySet.IS_CHANGED(set="None", strength_scale=1.0)
    assert isinstance(token, str)


# ------------------------------------------------------- VALIDATE_INPUTS / INPUT_TYPES


def test_validate_inputs_always_true_even_for_an_unknown_set() -> None:
    assert nodes_sets.LoraLibraryApplySet.VALIDATE_INPUTS(set="not-yet-created") is True


def test_input_types_combo_lists_none_plus_sorted_slugs(context: LibraryContext) -> None:
    sets_store.save_set(context, {"name": "Zebra Set", "loras": []})
    sets_store.save_set(context, {"name": "Apple Set", "loras": []})
    input_types = nodes_sets.LoraLibraryApplySet.INPUT_TYPES()
    options = input_types["required"]["set"][0]
    assert options == ["None", "apple-set", "zebra-set"]


def test_input_types_default_is_none(context: LibraryContext) -> None:
    input_types = nodes_sets.LoraLibraryApplySet.INPUT_TYPES()
    assert input_types["required"]["set"][1]["default"] == "None"


def test_input_types_strength_scale_widget_matches_format_md(context: LibraryContext) -> None:
    input_types = nodes_sets.LoraLibraryApplySet.INPUT_TYPES()
    widget_type, spec = input_types["required"]["strength_scale"]
    assert widget_type == "FLOAT"
    assert spec == {"default": 1.0, "min": 0.0, "max": 2.0, "step": 0.05}


def test_input_types_model_and_clip_are_optional(context: LibraryContext) -> None:
    input_types = nodes_sets.LoraLibraryApplySet.INPUT_TYPES()
    assert input_types["optional"]["model"] == ("MODEL",)
    assert input_types["optional"]["clip"] == ("CLIP",)


def test_input_types_without_context_falls_back_to_none_only() -> None:
    nodes_sets.set_context(None)
    input_types = nodes_sets.LoraLibraryApplySet.INPUT_TYPES()
    assert input_types["required"]["set"][0] == ["None"]


def test_class_shape_matches_format_md_section_6_2() -> None:
    cls = nodes_sets.LoraLibraryApplySet
    assert cls.CATEGORY == "EPSNodes"
    assert cls.RETURN_TYPES == ("MODEL", "CLIP", "LORA_STACK", "STRING")
    assert cls.RETURN_NAMES == ("model", "clip", "lora_stack", "trigger_words")
