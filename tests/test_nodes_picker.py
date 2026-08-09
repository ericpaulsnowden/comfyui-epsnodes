"""Tests for lora_library.nodes_picker (FORMAT.md §6.13, M1 scope only).

No ``comfy.*`` anywhere: ``LoraLibraryApplySet._apply_stack`` is
monkeypatched at the seam rather than faked via ``sys.modules`` (unlike
``test_nodes_sets.py``'s ``fake_comfy`` fixture) -- this module never calls
``comfy.*`` directly, it only ever calls through ``_apply_stack``, so
patching that one staticmethod is the whole story for verifying whether/how
it was invoked.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from lora_library import nodes_picker, nodes_sets
from lora_library.context import LibraryContext


class FakeModel:
    def __init__(self, tag: str = "model") -> None:
        self.tag = tag


class FakeClip:
    def __init__(self, tag: str = "clip") -> None:
        self.tag = tag


@pytest.fixture(autouse=True)
def _wire_context(context: LibraryContext):
    # Identity resolve_lora_path by default (readable assertions on the
    # resolved name itself); sidecar tests override it per-test to point at
    # real tmp_path files.
    context.resolve_lora_path = lambda name: name
    nodes_picker.set_context(context)
    yield
    nodes_picker.set_context(None)


@pytest.fixture
def fake_apply_stack(monkeypatch: pytest.MonkeyPatch):
    """Replaces ``LoraLibraryApplySet._apply_stack`` with a recorder.

    Returns the *calls* list: one ``(context, model, clip, stack)`` tuple
    per invocation, in call order. The fake returns tagged sentinels so
    tests can also tell "patched" output apart from a bare passthrough.
    """
    calls: list[tuple] = []

    def fake(context, model, clip, stack):
        calls.append((context, model, clip, stack))
        patched_model = FakeModel(f"{model.tag}+patched") if model is not None else None
        patched_clip = FakeClip(f"{clip.tag}+patched") if clip is not None else None
        return patched_model, patched_clip

    monkeypatch.setattr(nodes_sets.LoraLibraryApplySet, "_apply_stack", staticmethod(fake))
    return calls


def _selection(loras: list[dict], scope: str = "") -> str:
    return json.dumps({"scope": scope, "loras": loras})


# --------------------------------------------------------------- _parse_selection


def test_parse_empty_selection_returns_no_rows() -> None:
    assert nodes_picker._parse_selection("") == []


def test_parse_malformed_json_degrades_to_empty_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="lora_library"):
        rows = nodes_picker._parse_selection("{not json")
    assert rows == []
    assert any("not valid JSON" in r.message for r in caplog.records)


def test_parse_non_object_selection_degrades_to_empty_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="lora_library"):
        rows = nodes_picker._parse_selection(json.dumps([1, 2, 3]))
    assert rows == []
    assert any("not a JSON object" in r.message for r in caplog.records)


def test_parse_missing_loras_key_degrades_to_empty_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="lora_library"):
        rows = nodes_picker._parse_selection(json.dumps({"scope": "styles"}))
    assert rows == []
    assert any("selection.loras" in r.message for r in caplog.records)


def test_parse_loras_not_a_list_degrades_to_empty_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="lora_library"):
        rows = nodes_picker._parse_selection(json.dumps({"scope": "", "loras": "not-a-list"}))
    assert rows == []
    assert any("selection.loras" in r.message for r in caplog.records)


def test_parse_non_dict_row_is_skipped_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    payload = _selection(["not-a-dict", {"file": "detailer.safetensors"}])
    with caplog.at_level(logging.WARNING, logger="lora_library"):
        rows = nodes_picker._parse_selection(payload)
    assert [r["file"] for r in rows] == ["detailer.safetensors"]
    assert any("is not an object" in r.message for r in caplog.records)


def test_parse_row_missing_file_is_skipped_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    payload = _selection([{"strength": 1.0}, {"file": "detailer.safetensors"}])
    with caplog.at_level(logging.WARNING, logger="lora_library"):
        rows = nodes_picker._parse_selection(payload)
    assert [r["file"] for r in rows] == ["detailer.safetensors"]
    assert any("missing a 'file'" in r.message for r in caplog.records)


def test_parse_bool_strength_is_rejected_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    payload = _selection(
        [
            {"file": "ghost.safetensors", "strength": True},
            {"file": "detailer.safetensors", "strength": 0.5},
        ]
    )
    with caplog.at_level(logging.WARNING, logger="lora_library"):
        rows = nodes_picker._parse_selection(payload)
    assert [r["file"] for r in rows] == ["detailer.safetensors"]
    assert any("non-numeric strength" in r.message for r in caplog.records)


def test_parse_bool_strength_clip_is_rejected_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = _selection(
        [{"file": "detailer.safetensors", "strength": 1.0, "strength_clip": False}]
    )
    with caplog.at_level(logging.WARNING, logger="lora_library"):
        rows = nodes_picker._parse_selection(payload)
    assert rows == []
    assert any("non-numeric strength_clip" in r.message for r in caplog.records)


def test_parse_string_strength_coerces_via_float() -> None:
    payload = _selection([{"file": "detailer.safetensors", "strength": "0.75"}])
    rows = nodes_picker._parse_selection(payload)
    assert rows[0]["strength"] == pytest.approx(0.75)


def test_parse_dedup_by_file_first_wins() -> None:
    payload = _selection(
        [
            {"file": "detailer.safetensors", "strength": 0.5},
            {"file": "detailer.safetensors", "strength": 0.9},
        ]
    )
    rows = nodes_picker._parse_selection(payload)
    assert len(rows) == 1
    assert rows[0]["strength"] == pytest.approx(0.5)


def test_parse_missing_strength_clip_defaults_to_none() -> None:
    payload = _selection([{"file": "detailer.safetensors", "strength": 1.0}])
    rows = nodes_picker._parse_selection(payload)
    assert rows[0]["strength_clip"] is None


def test_parse_on_defaults_true_and_coerces_to_bool() -> None:
    payload = _selection(
        [
            {"file": "detailer.safetensors", "strength": 1.0},
            {"file": "ghost.safetensors", "strength": 1.0, "on": 0},
        ]
    )
    rows = {r["file"]: r for r in nodes_picker._parse_selection(payload)}
    assert rows["detailer.safetensors"]["on"] is True
    assert rows["ghost.safetensors"]["on"] is False


# --------------------------------------------------------------------------- build()


def test_empty_selection_is_a_passthrough_with_empty_outputs(context: LibraryContext) -> None:
    node = nodes_picker.EPSLoraPicker()
    model_out, clip_out, stack, trigger_words, loras_text = node.build(selection="")
    assert model_out is None
    assert clip_out is None
    assert stack == []
    assert trigger_words == ""
    assert loras_text == ""


def test_off_row_is_skipped_never_resolved_or_stacked(context: LibraryContext) -> None:
    payload = _selection([{"file": "detailer.safetensors", "on": False, "strength": 1.0}])
    node = nodes_picker.EPSLoraPicker()
    _, _, stack, trigger_words, loras_text = node.build(selection=payload)
    assert stack == []
    assert trigger_words == ""
    assert loras_text == ""


def test_unresolvable_lora_is_skipped_with_warning(
    context: LibraryContext, caplog: pytest.LogCaptureFixture
) -> None:
    payload = _selection([{"file": "ghost.safetensors", "on": True, "strength": 1.0}])
    node = nodes_picker.EPSLoraPicker()
    with caplog.at_level(logging.WARNING, logger="lora_library"):
        _, _, stack, _, _ = node.build(selection=payload)
    assert stack == []
    assert any("ghost.safetensors" in r.message for r in caplog.records)


def test_enabled_rows_build_a_stack_with_strength_clip_defaulting_to_strength(
    context: LibraryContext,
) -> None:
    payload = _selection(
        [
            {"file": "detailer.safetensors", "on": True, "strength": 0.8},
            {
                "file": "styles/film_grain.safetensors",
                "on": True,
                "strength": 0.4,
                "strength_clip": 0.6,
            },
        ]
    )
    node = nodes_picker.EPSLoraPicker()
    _, _, stack, _, loras_text = node.build(selection=payload)
    assert stack == [
        ("detailer.safetensors", pytest.approx(0.8), pytest.approx(0.8)),
        ("styles/film_grain.safetensors", pytest.approx(0.4), pytest.approx(0.6)),
    ]
    assert loras_text == "detailer_0.8 film_grain_0.4_0.6"


def test_backslash_selection_resolves_against_forward_slash_installed_list(
    context: LibraryContext,
) -> None:
    """FORMAT.md §4/§6.13 cross-machine resolution: a selection saved on
    Windows (backslash separators) must still resolve against this fake
    "installed" list, which -- like the real one on macOS/Linux -- uses
    forward slashes (tests/conftest.py's FAKE_LORAS)."""
    payload = _selection([{"file": "styles\\film_grain.safetensors", "on": True, "strength": 1.0}])
    node = nodes_picker.EPSLoraPicker()
    _, _, stack, _, _ = node.build(selection=payload)
    assert stack == [("styles/film_grain.safetensors", 1.0, 1.0)]


def test_no_context_configured_is_a_passthrough_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    nodes_picker.set_context(None)
    node = nodes_picker.EPSLoraPicker()
    model_in, clip_in = FakeModel(), FakeClip()
    with caplog.at_level(logging.WARNING, logger="lora_library"):
        model_out, clip_out, stack, trigger_words, loras_text = node.build(
            selection=_selection([{"file": "detailer.safetensors"}]), model=model_in, clip=clip_in
        )
    assert model_out is model_in
    assert clip_out is clip_in
    assert stack == []
    assert trigger_words == ""
    assert loras_text == ""
    assert any("no context configured" in r.message for r in caplog.records)


# ----------------------------------------------------------------------- loras_text


def test_loras_text_passes_through_nodes_sets_formatting(context: LibraryContext) -> None:
    payload = _selection([{"file": "detailer.safetensors", "on": True, "strength": 0.8}])
    node = nodes_picker.EPSLoraPicker()
    *_, loras_text = node.build(selection=payload)
    assert loras_text == nodes_sets._loras_text([("detailer.safetensors", 0.8, 0.8)])


# ------------------------------------------------------------------------ _apply_stack


def test_apply_stack_called_when_model_wired(context: LibraryContext, fake_apply_stack) -> None:
    payload = _selection([{"file": "detailer.safetensors", "on": True, "strength": 1.0}])
    node = nodes_picker.EPSLoraPicker()
    model_in = FakeModel()
    model_out, clip_out, *_ = node.build(selection=payload, model=model_in, clip=None)
    assert len(fake_apply_stack) == 1
    called_context, called_model, called_clip, called_stack = fake_apply_stack[0]
    assert called_context is context
    assert called_model is model_in
    assert called_clip is None
    assert called_stack == [("detailer.safetensors", 1.0, 1.0)]
    assert model_out is not None and model_out is not model_in
    assert clip_out is None


def test_apply_stack_called_when_only_clip_wired(context: LibraryContext, fake_apply_stack) -> None:
    node = nodes_picker.EPSLoraPicker()
    clip_in = FakeClip()
    node.build(selection="", model=None, clip=clip_in)
    assert len(fake_apply_stack) == 1


def test_apply_stack_not_called_when_neither_wired(
    context: LibraryContext, fake_apply_stack
) -> None:
    payload = _selection([{"file": "detailer.safetensors", "on": True, "strength": 1.0}])
    node = nodes_picker.EPSLoraPicker()
    node.build(selection=payload)
    assert fake_apply_stack == []


# ------------------------------------------------------------------------- trigger words


@pytest.fixture
def _sidecar_context(context: LibraryContext, tmp_path: Path):
    """Points ``resolve_lora_path`` at real files under *tmp_path* so
    trigger-word sidecar reads exercise real filesystem behavior."""
    lora_dir = tmp_path / "loras"
    lora_dir.mkdir()

    def resolve(name: str) -> str | None:
        mapping = {
            "detailer.safetensors": str(lora_dir / "detailer.safetensors"),
            "styles/film_grain.safetensors": str(lora_dir / "styles" / "film_grain.safetensors"),
        }
        return mapping.get(name)

    context.resolve_lora_path = resolve
    return lora_dir


def test_trigger_words_read_from_sidecar_txt_and_joined(
    context: LibraryContext, _sidecar_context: Path
) -> None:
    (_sidecar_context / "detailer.txt").write_text("  detail, sharp  ", encoding="utf-8")
    payload = _selection([{"file": "detailer.safetensors", "on": True, "strength": 1.0}])
    node = nodes_picker.EPSLoraPicker()
    *_, trigger_words, _ = node.build(selection=payload)
    assert trigger_words == "detail, sharp"


def test_trigger_words_from_multiple_rows_are_joined_with_comma_space(
    context: LibraryContext, _sidecar_context: Path
) -> None:
    (_sidecar_context / "detailer.txt").write_text("alpha", encoding="utf-8")
    subdir = _sidecar_context / "styles"
    subdir.mkdir()
    (subdir / "film_grain.txt").write_text("beta", encoding="utf-8")
    payload = _selection(
        [
            {"file": "detailer.safetensors", "on": True, "strength": 1.0},
            {"file": "styles/film_grain.safetensors", "on": True, "strength": 1.0},
        ]
    )
    node = nodes_picker.EPSLoraPicker()
    *_, trigger_words, _ = node.build(selection=payload)
    assert trigger_words == "alpha, beta"


def test_missing_sidecar_is_quiet_no_warning_logged(
    context: LibraryContext, _sidecar_context: Path, caplog: pytest.LogCaptureFixture
) -> None:
    payload = _selection([{"file": "detailer.safetensors", "on": True, "strength": 1.0}])
    node = nodes_picker.EPSLoraPicker()
    with caplog.at_level(logging.WARNING, logger="lora_library"):
        *_, trigger_words, _ = node.build(selection=payload)
    assert trigger_words == ""
    assert not any(
        r.levelno >= logging.WARNING and "sidecar" in r.message.lower() for r in caplog.records
    )


def test_sidecar_text_is_capped_at_4096_chars(
    context: LibraryContext, _sidecar_context: Path
) -> None:
    (_sidecar_context / "detailer.txt").write_text("x" * 5000, encoding="utf-8")
    payload = _selection([{"file": "detailer.safetensors", "on": True, "strength": 1.0}])
    node = nodes_picker.EPSLoraPicker()
    *_, trigger_words, _ = node.build(selection=payload)
    assert len(trigger_words) == 4096


# ---------------------------------------------------------------------------- IS_CHANGED


def test_is_changed_ignores_scope() -> None:
    payload_a = _selection([{"file": "detailer.safetensors", "strength": 1.0}], scope="")
    payload_b = _selection([{"file": "detailer.safetensors", "strength": 1.0}], scope="styles")
    assert nodes_picker.EPSLoraPicker.IS_CHANGED(
        selection=payload_a
    ) == nodes_picker.EPSLoraPicker.IS_CHANGED(selection=payload_b)


def test_is_changed_changes_with_strength(context: LibraryContext) -> None:
    a = _selection([{"file": "detailer.safetensors", "strength": 1.0}])
    b = _selection([{"file": "detailer.safetensors", "strength": 0.5}])
    assert nodes_picker.EPSLoraPicker.IS_CHANGED(
        selection=a
    ) != nodes_picker.EPSLoraPicker.IS_CHANGED(selection=b)


def test_is_changed_changes_with_on(context: LibraryContext) -> None:
    a = _selection([{"file": "detailer.safetensors", "strength": 1.0, "on": True}])
    b = _selection([{"file": "detailer.safetensors", "strength": 1.0, "on": False}])
    assert nodes_picker.EPSLoraPicker.IS_CHANGED(
        selection=a
    ) != nodes_picker.EPSLoraPicker.IS_CHANGED(selection=b)


def test_is_changed_changes_with_file(context: LibraryContext) -> None:
    a = _selection([{"file": "detailer.safetensors", "strength": 1.0}])
    b = _selection([{"file": "styles/film_grain.safetensors", "strength": 1.0}])
    assert nodes_picker.EPSLoraPicker.IS_CHANGED(
        selection=a
    ) != nodes_picker.EPSLoraPicker.IS_CHANGED(selection=b)


def test_is_changed_changes_with_sidecar_mtime(
    context: LibraryContext, _sidecar_context: Path
) -> None:
    payload = _selection([{"file": "detailer.safetensors", "on": True, "strength": 1.0}])
    token_before = nodes_picker.EPSLoraPicker.IS_CHANGED(selection=payload)
    sidecar = _sidecar_context / "detailer.txt"
    sidecar.write_text("hello", encoding="utf-8")
    os_stat = sidecar.stat()
    # Force a distinct mtime even on filesystems with coarse mtime
    # resolution -- the token compares string mtimes verbatim.
    import os

    os.utime(sidecar, (os_stat.st_atime + 5, os_stat.st_mtime + 5))
    token_after = nodes_picker.EPSLoraPicker.IS_CHANGED(selection=payload)
    assert token_before != token_after


def test_is_changed_returns_a_string(context: LibraryContext) -> None:
    assert isinstance(nodes_picker.EPSLoraPicker.IS_CHANGED(selection=""), str)


# ----------------------------------------------------------------- VALIDATE_INPUTS


def test_validate_inputs_always_true() -> None:
    assert nodes_picker.EPSLoraPicker.VALIDATE_INPUTS(selection="anything") is True


# --------------------------------------------------------------------------- shape


def test_class_shape_matches_format_md_section_6_13() -> None:
    cls = nodes_picker.EPSLoraPicker
    assert cls.CATEGORY == "EPSNodes/LoRA"
    assert cls.RETURN_TYPES == ("MODEL", "CLIP", "LORA_STACK", "STRING", "STRING")
    assert cls.RETURN_NAMES == ("model", "clip", "lora_stack", "trigger_words", "loras_text")
    assert cls.FUNCTION == "build"


def test_input_types_selection_widget_is_hidden_with_empty_default() -> None:
    input_types = nodes_picker.EPSLoraPicker.INPUT_TYPES()
    assert input_types["required"] == {}
    widget_type, spec = input_types["optional"]["selection"]
    assert widget_type == "STRING"
    assert spec["default"] == ""
    assert spec["hidden"] is True


def test_input_types_model_and_clip_are_optional() -> None:
    input_types = nodes_picker.EPSLoraPicker.INPUT_TYPES()
    assert input_types["optional"]["model"][0] == "MODEL"
    assert input_types["optional"]["clip"][0] == "CLIP"
