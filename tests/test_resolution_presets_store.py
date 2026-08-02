"""Tests for eps_image.resolution_presets_store (EPS Resolution M3 --
server-side size presets, shared feature contract 2026-08-01).

Uses the same `context`/`library_dir` fixtures every other store test in
this suite builds on (tests/conftest.py) -- a real LibraryContext over a
throwaway tmp_path, no ComfyUI anywhere.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from eps_image import resolution_presets_store as store
from lora_library.context import LibraryContext

VALUES = {
    "width": 512,
    "height": 768,
    "resize_method": "stretch",
    "interpolation": "bilinear",
    "multiple_of": 64,
}


def _other_values(**overrides: object) -> dict:
    values = dict(VALUES)
    values.update(overrides)
    return values


# ------------------------------------------------------------------- loading


class TestLoadPresets:
    def test_missing_file_returns_empty_presets_and_none_mtime(
        self, context: LibraryContext
    ) -> None:
        presets, mtime = store.load_presets(context)
        assert presets == {}
        assert mtime is None

    def test_missing_file_logs_no_warning(
        self, context: LibraryContext, caplog: pytest.LogCaptureFixture
    ) -> None:
        # A brand-new install/library folder is normal, not a problem -- same
        # "first use" reading as LibraryContext.load_config/markdown_store's
        # load_notebook, neither of which warns on a plain missing file.
        with caplog.at_level("WARNING"):
            store.load_presets(context)
        assert caplog.records == []

    def test_reads_back_a_saved_preset(self, context: LibraryContext) -> None:
        store.save_preset(context, "Portrait", VALUES)
        presets, mtime = store.load_presets(context)
        assert presets == {"Portrait": VALUES}
        assert isinstance(mtime, float)

    def test_malformed_json_returns_empty_and_warns(
        self, context: LibraryContext, library_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        (library_dir / store.PRESETS_FILENAME).write_text("not json{{{", encoding="utf-8")
        with caplog.at_level("WARNING"):
            presets, mtime = store.load_presets(context)
        assert presets == {}
        assert isinstance(mtime, float)  # the file DOES exist -- real mtime, not None
        assert any("not valid JSON" in r.message for r in caplog.records)

    def test_non_object_top_level_returns_empty_and_warns(
        self, context: LibraryContext, library_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        (library_dir / store.PRESETS_FILENAME).write_text("[1, 2, 3]", encoding="utf-8")
        with caplog.at_level("WARNING"):
            presets, mtime = store.load_presets(context)
        assert presets == {}
        assert isinstance(mtime, float)
        assert any("JSON object" in r.message for r in caplog.records)

    def test_missing_presets_key_returns_empty_and_warns(
        self, context: LibraryContext, library_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        (library_dir / store.PRESETS_FILENAME).write_text(
            json.dumps({"format": 1}), encoding="utf-8"
        )
        with caplog.at_level("WARNING"):
            presets, mtime = store.load_presets(context)
        assert presets == {}
        assert isinstance(mtime, float)
        assert any("usable 'presets'" in r.message for r in caplog.records)

    def test_one_malformed_entry_is_skipped_but_siblings_survive(
        self, context: LibraryContext, library_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        on_disk = {
            "format": 1,
            "presets": {
                "Good": VALUES,
                "Bad": {"width": "not an int", "height": 1, "resize_method": "stretch",
                        "interpolation": "bilinear", "multiple_of": 0},
                "AlsoGood": _other_values(width=100),
            },
        }
        (library_dir / store.PRESETS_FILENAME).write_text(json.dumps(on_disk), encoding="utf-8")
        with caplog.at_level("WARNING"):
            presets, _mtime = store.load_presets(context)
        assert list(presets.keys()) == ["Good", "AlsoGood"]
        assert any("malformed preset" in r.message for r in caplog.records)

    def test_non_dict_preset_entry_is_skipped(
        self, context: LibraryContext, library_dir: Path
    ) -> None:
        on_disk = {"format": 1, "presets": {"Weird": "not-a-dict", "Good": VALUES}}
        (library_dir / store.PRESETS_FILENAME).write_text(json.dumps(on_disk), encoding="utf-8")
        presets, _mtime = store.load_presets(context)
        assert list(presets.keys()) == ["Good"]

    def test_bool_is_rejected_for_int_fields_even_though_it_is_an_int_subclass(
        self, context: LibraryContext, library_dir: Path
    ) -> None:
        on_disk = {
            "format": 1,
            "presets": {"Bad": _other_values(width=True)},
        }
        (library_dir / store.PRESETS_FILENAME).write_text(json.dumps(on_disk), encoding="utf-8")
        presets, _mtime = store.load_presets(context)
        assert presets == {}


# --------------------------------------------------------------------- names


class TestNormalizeName:
    def test_trims_whitespace(self) -> None:
        assert store.normalize_name("  Portrait  ") == "Portrait"

    def test_empty_after_trim_raises(self) -> None:
        with pytest.raises(store.InvalidPresetNameError):
            store.normalize_name("   ")

    def test_non_string_raises(self) -> None:
        with pytest.raises(store.InvalidPresetNameError):
            store.normalize_name(None)

    def test_at_max_length_is_ok(self) -> None:
        name = "x" * store.MAX_NAME_LENGTH
        assert store.normalize_name(name) == name

    def test_over_max_length_raises(self) -> None:
        with pytest.raises(store.InvalidPresetNameError):
            store.normalize_name("x" * (store.MAX_NAME_LENGTH + 1))


# -------------------------------------------------------------------- values


class TestNormalizeValues:
    def test_missing_field_raises(self) -> None:
        bad = dict(VALUES)
        del bad["width"]
        with pytest.raises(store.InvalidPresetValuesError):
            store._normalize_values(bad)

    def test_non_dict_raises(self) -> None:
        with pytest.raises(store.InvalidPresetValuesError):
            store._normalize_values("nope")

    def test_string_width_raises(self) -> None:
        with pytest.raises(store.InvalidPresetValuesError):
            store._normalize_values(_other_values(width="512"))

    def test_empty_resize_method_raises(self) -> None:
        with pytest.raises(store.InvalidPresetValuesError):
            store._normalize_values(_other_values(resize_method=""))

    def test_canonical_field_order(self) -> None:
        out = store._normalize_values(VALUES)
        assert list(out.keys()) == [
            "width", "height", "resize_method", "interpolation", "multiple_of",
        ]


# -------------------------------------------------------------------- saving


class TestSavePreset:
    def test_creates_file_with_documented_shape(
        self, context: LibraryContext, library_dir: Path
    ) -> None:
        store.save_preset(context, "Portrait", VALUES)
        path = library_dir / store.PRESETS_FILENAME
        assert path.is_file()
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk == {"format": 1, "presets": {"Portrait": VALUES}}

    def test_returns_fresh_presets_and_new_mtime(self, context: LibraryContext) -> None:
        presets, mtime = store.save_preset(context, "Portrait", VALUES)
        assert presets == {"Portrait": VALUES}
        assert isinstance(mtime, float)

    def test_trims_the_name_before_storing(self, context: LibraryContext) -> None:
        presets, _mtime = store.save_preset(context, "  Portrait  ", VALUES)
        assert list(presets.keys()) == ["Portrait"]

    def test_second_create_appends_preserving_order(self, context: LibraryContext) -> None:
        store.save_preset(context, "B", VALUES)
        presets, _mtime = store.save_preset(context, "A", VALUES)
        assert list(presets.keys()) == ["B", "A"]

    def test_update_in_place_does_not_move_it(self, context: LibraryContext) -> None:
        store.save_preset(context, "A", VALUES)
        store.save_preset(context, "B", VALUES)
        presets, _mtime = store.save_preset(context, "A", _other_values(width=999))
        assert list(presets.keys()) == ["A", "B"]  # A stays first
        assert presets["A"]["width"] == 999

    def test_invalid_name_raises_before_touching_disk(
        self, context: LibraryContext, library_dir: Path
    ) -> None:
        with pytest.raises(store.InvalidPresetNameError):
            store.save_preset(context, "   ", VALUES)
        assert not (library_dir / store.PRESETS_FILENAME).exists()

    def test_invalid_values_raises_before_touching_disk(
        self, context: LibraryContext, library_dir: Path
    ) -> None:
        with pytest.raises(store.InvalidPresetValuesError):
            store.save_preset(context, "Portrait", {"width": 1})
        assert not (library_dir / store.PRESETS_FILENAME).exists()

    def test_omitted_base_mtime_skips_conflict_check_even_if_file_exists(
        self, context: LibraryContext
    ) -> None:
        store.save_preset(context, "A", VALUES)
        # No base_mtime at all -- must not raise even though the file exists.
        store.save_preset(context, "B", VALUES)

    def test_stale_base_mtime_raises_conflict_and_leaves_file_untouched(
        self, context: LibraryContext
    ) -> None:
        _presets, real_mtime = store.save_preset(context, "A", VALUES)
        with pytest.raises(store.ConflictError) as excinfo:
            store.save_preset(context, "B", VALUES, base_mtime=real_mtime - 100.0)
        assert excinfo.value.current_mtime == real_mtime
        presets, _mtime = store.load_presets(context)
        assert list(presets.keys()) == ["A"]  # B never got written

    def test_matching_base_mtime_succeeds(self, context: LibraryContext) -> None:
        _presets, real_mtime = store.save_preset(context, "A", VALUES)
        presets, _new_mtime = store.save_preset(context, "B", VALUES, base_mtime=real_mtime)
        assert list(presets.keys()) == ["A", "B"]

    def test_base_mtime_against_a_brand_new_file_never_conflicts(
        self, context: LibraryContext
    ) -> None:
        # First save ever -- current_mtime is None, so any base_mtime (even
        # a stale-looking one) has nothing to conflict with.
        presets, _mtime = store.save_preset(context, "A", VALUES, base_mtime=12345.0)
        assert list(presets.keys()) == ["A"]


# ------------------------------------------------------------------- deleting


class TestDeletePreset:
    def test_removes_and_returns_fresh_dict(self, context: LibraryContext) -> None:
        store.save_preset(context, "A", VALUES)
        store.save_preset(context, "B", VALUES)
        presets, _mtime = store.delete_preset(context, "A")
        assert list(presets.keys()) == ["B"]

    def test_unknown_name_raises_not_found(self, context: LibraryContext) -> None:
        with pytest.raises(store.PresetNotFoundError):
            store.delete_preset(context, "Ghost")

    def test_unknown_name_on_a_never_created_file_raises_not_found(
        self, context: LibraryContext
    ) -> None:
        with pytest.raises(store.PresetNotFoundError):
            store.delete_preset(context, "Anything")

    def test_stale_base_mtime_raises_conflict_and_leaves_file_untouched(
        self, context: LibraryContext
    ) -> None:
        _presets, real_mtime = store.save_preset(context, "A", VALUES)
        with pytest.raises(store.ConflictError) as excinfo:
            store.delete_preset(context, "A", base_mtime=real_mtime - 100.0)
        assert excinfo.value.current_mtime == real_mtime
        presets, _mtime = store.load_presets(context)
        assert list(presets.keys()) == ["A"]  # never deleted

    def test_matching_base_mtime_succeeds(self, context: LibraryContext) -> None:
        _presets, real_mtime = store.save_preset(context, "A", VALUES)
        presets, _mtime = store.delete_preset(context, "A", base_mtime=real_mtime)
        assert presets == {}


# ---------------------------------------------------------------- conflicts


class TestCheckConflict:
    def test_none_base_mtime_never_conflicts(self) -> None:
        store.check_conflict(None, 123.0)  # must not raise

    def test_none_current_mtime_never_conflicts(self) -> None:
        store.check_conflict(123.0, None)  # must not raise

    def test_matching_mtimes_do_not_conflict(self) -> None:
        store.check_conflict(123.456, 123.456)  # must not raise

    def test_within_epsilon_does_not_conflict(self) -> None:
        store.check_conflict(123.456, 123.456 + 1e-9)  # must not raise

    def test_differing_mtimes_raise_with_current_mtime_attached(self) -> None:
        with pytest.raises(store.ConflictError) as excinfo:
            store.check_conflict(100.0, 200.0)
        assert excinfo.value.current_mtime == 200.0


# ------------------------------------------------------------- atomic writes


class TestAtomicWrites:
    def test_save_leaves_no_temp_files_behind(
        self, context: LibraryContext, library_dir: Path
    ) -> None:
        store.save_preset(context, "A", VALUES)
        assert list(library_dir.glob("*.tmp")) == []

    def test_delete_leaves_no_temp_files_behind(
        self, context: LibraryContext, library_dir: Path
    ) -> None:
        store.save_preset(context, "A", VALUES)
        store.delete_preset(context, "A")
        assert list(library_dir.glob("*.tmp")) == []

    def test_failed_replace_leaves_original_file_untouched(
        self, context: LibraryContext, library_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store.save_preset(context, "A", VALUES)
        original_text = (library_dir / store.PRESETS_FILENAME).read_text(encoding="utf-8")

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise OSError("simulated disk failure")

        monkeypatch.setattr(os, "replace", _boom)
        with pytest.raises(OSError):
            store.save_preset(context, "B", VALUES)

        # The original file must survive a failed atomic replace untouched,
        # and no stray temp file should be left behind either.
        assert (library_dir / store.PRESETS_FILENAME).read_text(encoding="utf-8") == original_text
        assert list(library_dir.glob("*.tmp")) == []
