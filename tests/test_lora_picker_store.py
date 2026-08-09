"""Tests for lora_library.lora_picker_store (EPSLoraPicker M1 --
favorites/recents store, FORMAT.md §6.13).

Uses the same `context`/`library_dir` fixtures every other store test in
this suite builds on (tests/conftest.py) -- a real LibraryContext over a
throwaway tmp_path, no ComfyUI anywhere.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lora_library import lora_picker_store as store
from lora_library.context import LibraryContext

# ------------------------------------------------------------------- loading


class TestLoadState:
    def test_missing_file_returns_empty_state_and_none_mtime(
        self, context: LibraryContext
    ) -> None:
        state, mtime = store.load_state(context)
        assert state == {"favorites": [], "recents": []}
        assert mtime is None

    def test_missing_file_logs_no_warning(
        self, context: LibraryContext, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("WARNING"):
            store.load_state(context)
        assert caplog.records == []

    def test_reads_back_a_saved_favorite(self, context: LibraryContext) -> None:
        store.toggle_favorite(context, "a.safetensors", True)
        state, mtime = store.load_state(context)
        assert state == {"favorites": ["a.safetensors"], "recents": []}
        assert isinstance(mtime, float)

    def test_malformed_json_returns_empty_and_warns(
        self, context: LibraryContext, library_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        (library_dir / store.PICKER_FILENAME).write_text("not json{{{", encoding="utf-8")
        with caplog.at_level("WARNING"):
            state, mtime = store.load_state(context)
        assert state == {"favorites": [], "recents": []}
        assert isinstance(mtime, float)  # the file DOES exist -- real mtime, not None
        assert any("not valid JSON" in r.message for r in caplog.records)

    def test_non_object_top_level_returns_empty_and_warns(
        self, context: LibraryContext, library_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        (library_dir / store.PICKER_FILENAME).write_text("[1, 2, 3]", encoding="utf-8")
        with caplog.at_level("WARNING"):
            state, mtime = store.load_state(context)
        assert state == {"favorites": [], "recents": []}
        assert isinstance(mtime, float)
        assert any("JSON object" in r.message for r in caplog.records)

    def test_missing_favorites_key_degrades_and_warns(
        self, context: LibraryContext, library_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        (library_dir / store.PICKER_FILENAME).write_text(
            json.dumps({"format": 1, "recents": []}), encoding="utf-8"
        )
        with caplog.at_level("WARNING"):
            state, _mtime = store.load_state(context)
        assert state["favorites"] == []
        assert any("favorites" in r.message for r in caplog.records)

    def test_missing_recents_key_degrades_and_warns(
        self, context: LibraryContext, library_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        (library_dir / store.PICKER_FILENAME).write_text(
            json.dumps({"format": 1, "favorites": []}), encoding="utf-8"
        )
        with caplog.at_level("WARNING"):
            state, _mtime = store.load_state(context)
        assert state["recents"] == []
        assert any("recents" in r.message for r in caplog.records)

    def test_one_malformed_favorite_is_skipped_but_siblings_survive(
        self, context: LibraryContext, library_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        on_disk = {
            "format": 1,
            "favorites": ["Good.safetensors", 123, "", "AlsoGood.safetensors"],
            "recents": [],
        }
        (library_dir / store.PICKER_FILENAME).write_text(json.dumps(on_disk), encoding="utf-8")
        with caplog.at_level("WARNING"):
            state, _mtime = store.load_state(context)
        assert state["favorites"] == ["Good.safetensors", "AlsoGood.safetensors"]
        assert any("malformed favorite" in r.message for r in caplog.records)

    def test_duplicate_favorites_collapse_to_first_occurrence(
        self, context: LibraryContext, library_dir: Path
    ) -> None:
        on_disk = {"format": 1, "favorites": ["a.safetensors", "a.safetensors"], "recents": []}
        (library_dir / store.PICKER_FILENAME).write_text(json.dumps(on_disk), encoding="utf-8")
        state, _mtime = store.load_state(context)
        assert state["favorites"] == ["a.safetensors"]

    def test_one_malformed_recent_row_is_skipped_but_siblings_survive(
        self, context: LibraryContext, library_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        on_disk = {
            "format": 1,
            "favorites": [],
            "recents": [
                {"file": "good.safetensors", "ts": 100.0},
                {"file": "missing-ts.safetensors"},
                {"ts": 100.0},
                "not-a-dict",
                {"file": "bad-ts.safetensors", "ts": "nope"},
                {"file": "bool-ts.safetensors", "ts": True},
                {"file": "also-good.safetensors", "ts": 50},
            ],
        }
        (library_dir / store.PICKER_FILENAME).write_text(json.dumps(on_disk), encoding="utf-8")
        with caplog.at_level("WARNING"):
            state, _mtime = store.load_state(context)
        assert [row["file"] for row in state["recents"]] == [
            "good.safetensors",
            "also-good.safetensors",
        ]
        assert state["recents"][1]["ts"] == 50.0  # int ts coerced to float
        assert any("malformed recents entry" in r.message for r in caplog.records)

    def test_duplicate_recents_collapse_to_first_occurrence(
        self, context: LibraryContext, library_dir: Path
    ) -> None:
        on_disk = {
            "format": 1,
            "favorites": [],
            "recents": [
                {"file": "a.safetensors", "ts": 200.0},
                {"file": "a.safetensors", "ts": 100.0},
            ],
        }
        (library_dir / store.PICKER_FILENAME).write_text(json.dumps(on_disk), encoding="utf-8")
        state, _mtime = store.load_state(context)
        assert len(state["recents"]) == 1
        assert state["recents"][0]["ts"] == 200.0

    def test_recents_over_cap_on_disk_degrades_to_the_cap(
        self, context: LibraryContext, library_dir: Path
    ) -> None:
        on_disk = {
            "format": 1,
            "favorites": [],
            "recents": [{"file": f"f{i}.safetensors", "ts": float(i)} for i in range(40)],
        }
        (library_dir / store.PICKER_FILENAME).write_text(json.dumps(on_disk), encoding="utf-8")
        state, _mtime = store.load_state(context)
        assert len(state["recents"]) == store.RECENTS_CAP
        # file order preserved, not re-sorted
        assert state["recents"][0]["file"] == "f0.safetensors"

    def test_backslash_names_normalize_to_forward_slash_on_read(
        self, context: LibraryContext, library_dir: Path
    ) -> None:
        on_disk = {
            "format": 1,
            "favorites": ["styles\\film_grain.safetensors"],
            "recents": [{"file": "styles\\cinematic.safetensors", "ts": 1.0}],
        }
        (library_dir / store.PICKER_FILENAME).write_text(json.dumps(on_disk), encoding="utf-8")
        state, _mtime = store.load_state(context)
        assert state["favorites"] == ["styles/film_grain.safetensors"]
        assert state["recents"][0]["file"] == "styles/cinematic.safetensors"


# ---------------------------------------------------------------- favorites


class TestToggleFavorite:
    def test_star_appends_new_favorite(self, context: LibraryContext) -> None:
        state, _mtime = store.toggle_favorite(context, "a.safetensors", True)
        assert state["favorites"] == ["a.safetensors"]

    def test_star_preserves_insertion_order(self, context: LibraryContext) -> None:
        store.toggle_favorite(context, "b.safetensors", True)
        state, _mtime = store.toggle_favorite(context, "a.safetensors", True)
        assert state["favorites"] == ["b.safetensors", "a.safetensors"]

    def test_star_already_favorited_is_idempotent(self, context: LibraryContext) -> None:
        store.toggle_favorite(context, "a.safetensors", True)
        state, _mtime = store.toggle_favorite(context, "a.safetensors", True)
        assert state["favorites"] == ["a.safetensors"]  # not duplicated

    def test_unstar_removes_it(self, context: LibraryContext) -> None:
        store.toggle_favorite(context, "a.safetensors", True)
        state, _mtime = store.toggle_favorite(context, "a.safetensors", False)
        assert state["favorites"] == []

    def test_unstar_something_never_favorited_is_a_no_error_no_op(
        self, context: LibraryContext
    ) -> None:
        state, _mtime = store.toggle_favorite(context, "ghost.safetensors", False)
        assert state["favorites"] == []

    def test_unstar_preserves_order_of_the_rest(self, context: LibraryContext) -> None:
        store.toggle_favorite(context, "a.safetensors", True)
        store.toggle_favorite(context, "b.safetensors", True)
        store.toggle_favorite(context, "c.safetensors", True)
        state, _mtime = store.toggle_favorite(context, "b.safetensors", False)
        assert state["favorites"] == ["a.safetensors", "c.safetensors"]

    def test_name_normalized_to_forward_slashes_on_write(self, context: LibraryContext) -> None:
        state, _mtime = store.toggle_favorite(context, "styles\\film_grain.safetensors", True)
        assert state["favorites"] == ["styles/film_grain.safetensors"]

    def test_name_stripped_on_write(self, context: LibraryContext) -> None:
        state, _mtime = store.toggle_favorite(context, "  a.safetensors  ", True)
        assert state["favorites"] == ["a.safetensors"]

    def test_recents_untouched(self, context: LibraryContext) -> None:
        store.record_recents(context, ["r.safetensors"])
        state, _mtime = store.toggle_favorite(context, "a.safetensors", True)
        assert [row["file"] for row in state["recents"]] == ["r.safetensors"]

    def test_non_string_file_raises(self, context: LibraryContext) -> None:
        with pytest.raises(store.PickerStoreError):
            store.toggle_favorite(context, None, True)

    def test_blank_file_raises(self, context: LibraryContext) -> None:
        with pytest.raises(store.PickerStoreError):
            store.toggle_favorite(context, "   ", True)

    def test_stale_base_mtime_raises_conflict_and_leaves_file_untouched(
        self, context: LibraryContext
    ) -> None:
        _state, real_mtime = store.toggle_favorite(context, "a.safetensors", True)
        with pytest.raises(store.ConflictError) as excinfo:
            store.toggle_favorite(context, "b.safetensors", True, base_mtime=real_mtime - 100.0)
        assert excinfo.value.current_mtime == real_mtime
        state, _mtime = store.load_state(context)
        assert state["favorites"] == ["a.safetensors"]  # b never got written

    def test_matching_base_mtime_succeeds(self, context: LibraryContext) -> None:
        _state, real_mtime = store.toggle_favorite(context, "a.safetensors", True)
        state, _mtime = store.toggle_favorite(
            context, "b.safetensors", True, base_mtime=real_mtime
        )
        assert state["favorites"] == ["a.safetensors", "b.safetensors"]

    def test_base_mtime_against_a_brand_new_file_never_conflicts(
        self, context: LibraryContext
    ) -> None:
        state, _mtime = store.toggle_favorite(
            context, "a.safetensors", True, base_mtime=12345.0
        )
        assert state["favorites"] == ["a.safetensors"]


# ------------------------------------------------------------------- recents


class TestRecordRecents:
    def test_records_a_single_file(self, context: LibraryContext) -> None:
        state, _mtime = store.record_recents(context, ["a.safetensors"])
        assert [row["file"] for row in state["recents"]] == ["a.safetensors"]
        assert isinstance(state["recents"][0]["ts"], float)

    def test_first_arg_ends_up_frontmost(self, context: LibraryContext) -> None:
        state, _mtime = store.record_recents(
            context, ["a.safetensors", "b.safetensors", "c.safetensors"]
        )
        assert [row["file"] for row in state["recents"]] == [
            "a.safetensors",
            "b.safetensors",
            "c.safetensors",
        ]

    def test_previously_recorded_file_moves_to_front_deduped(
        self, context: LibraryContext
    ) -> None:
        store.record_recents(context, ["a.safetensors", "b.safetensors", "c.safetensors"])
        state, _mtime = store.record_recents(context, ["c.safetensors"])
        assert [row["file"] for row in state["recents"]] == [
            "c.safetensors",
            "a.safetensors",
            "b.safetensors",
        ]

    def test_duplicate_within_one_call_collapses_to_first_arg_position(
        self, context: LibraryContext
    ) -> None:
        state, _mtime = store.record_recents(
            context, ["a.safetensors", "b.safetensors", "a.safetensors"]
        )
        assert [row["file"] for row in state["recents"]] == ["a.safetensors", "b.safetensors"]

    def test_cap_applied_within_a_single_call(self, context: LibraryContext) -> None:
        files = [f"f{i}.safetensors" for i in range(store.RECENTS_CAP + 5)]
        state, _mtime = store.record_recents(context, files)
        assert len(state["recents"]) == store.RECENTS_CAP
        assert [row["file"] for row in state["recents"]] == files[: store.RECENTS_CAP]

    def test_cap_applied_across_calls_dropping_the_oldest(self, context: LibraryContext) -> None:
        state = None
        for i in range(store.RECENTS_CAP + 5):
            state, _mtime = store.record_recents(context, [f"f{i}.safetensors"])
        assert state is not None
        assert len(state["recents"]) == store.RECENTS_CAP
        newest = f"f{store.RECENTS_CAP + 4}.safetensors"
        oldest_kept = "f5.safetensors"  # f0..f4 pushed out
        assert state["recents"][0]["file"] == newest
        assert state["recents"][-1]["file"] == oldest_kept

    def test_empty_list_is_a_no_op_and_does_not_write(
        self, context: LibraryContext, library_dir: Path
    ) -> None:
        store.record_recents(context, ["a.safetensors"])
        assert (library_dir / store.PICKER_FILENAME).exists()
        before_mtime = (library_dir / store.PICKER_FILENAME).stat().st_mtime
        state, mtime = store.record_recents(context, [])
        assert [row["file"] for row in state["recents"]] == ["a.safetensors"]
        assert mtime == before_mtime

    def test_empty_list_on_a_never_created_file_is_a_no_op(
        self, context: LibraryContext, library_dir: Path
    ) -> None:
        state, mtime = store.record_recents(context, [])
        assert state == {"favorites": [], "recents": []}
        assert mtime is None
        assert not (library_dir / store.PICKER_FILENAME).exists()

    def test_names_normalized_to_forward_slashes(self, context: LibraryContext) -> None:
        state, _mtime = store.record_recents(context, ["styles\\film_grain.safetensors"])
        assert state["recents"][0]["file"] == "styles/film_grain.safetensors"

    def test_favorites_untouched(self, context: LibraryContext) -> None:
        store.toggle_favorite(context, "f.safetensors", True)
        state, _mtime = store.record_recents(context, ["a.safetensors"])
        assert state["favorites"] == ["f.safetensors"]

    def test_non_string_entry_raises_before_touching_disk(
        self, context: LibraryContext, library_dir: Path
    ) -> None:
        with pytest.raises(store.PickerStoreError):
            store.record_recents(context, ["a.safetensors", 123])
        assert not (library_dir / store.PICKER_FILENAME).exists()

    def test_stale_base_mtime_raises_conflict_and_leaves_file_untouched(
        self, context: LibraryContext
    ) -> None:
        _state, real_mtime = store.record_recents(context, ["a.safetensors"])
        with pytest.raises(store.ConflictError) as excinfo:
            store.record_recents(
                context, ["b.safetensors"], base_mtime=real_mtime - 100.0
            )
        assert excinfo.value.current_mtime == real_mtime
        state, _mtime = store.load_state(context)
        assert [row["file"] for row in state["recents"]] == ["a.safetensors"]

    def test_matching_base_mtime_succeeds(self, context: LibraryContext) -> None:
        _state, real_mtime = store.record_recents(context, ["a.safetensors"])
        state, _mtime = store.record_recents(
            context, ["b.safetensors"], base_mtime=real_mtime
        )
        assert [row["file"] for row in state["recents"]] == [
            "b.safetensors",
            "a.safetensors",
        ]


class TestClearRecents:
    def test_empties_recents_leaves_favorites(self, context: LibraryContext) -> None:
        store.toggle_favorite(context, "f.safetensors", True)
        store.record_recents(context, ["a.safetensors", "b.safetensors"])
        state, _mtime = store.clear_recents(context)
        assert state == {"favorites": ["f.safetensors"], "recents": []}

    def test_on_a_never_created_file_still_succeeds(self, context: LibraryContext) -> None:
        state, mtime = store.clear_recents(context)
        assert state == {"favorites": [], "recents": []}
        assert isinstance(mtime, float)

    def test_stale_base_mtime_raises_conflict_and_leaves_file_untouched(
        self, context: LibraryContext
    ) -> None:
        _state, real_mtime = store.record_recents(context, ["a.safetensors"])
        with pytest.raises(store.ConflictError) as excinfo:
            store.clear_recents(context, base_mtime=real_mtime - 100.0)
        assert excinfo.value.current_mtime == real_mtime
        state, _mtime = store.load_state(context)
        assert [row["file"] for row in state["recents"]] == ["a.safetensors"]

    def test_matching_base_mtime_succeeds(self, context: LibraryContext) -> None:
        _state, real_mtime = store.record_recents(context, ["a.safetensors"])
        state, _mtime = store.clear_recents(context, base_mtime=real_mtime)
        assert state["recents"] == []


# ----------------------------------------------------- favorites reorder (M3)


class TestReorderFavorites:
    def test_pure_reorder_round_trip(self, context: LibraryContext) -> None:
        store.toggle_favorite(context, "a.safetensors", True)
        store.toggle_favorite(context, "b.safetensors", True)
        store.toggle_favorite(context, "c.safetensors", True)
        state, _mtime = store.reorder_favorites(
            context, ["c.safetensors", "a.safetensors", "b.safetensors"]
        )
        assert state["favorites"] == ["c.safetensors", "a.safetensors", "b.safetensors"]

    def test_unknown_names_are_dropped_not_added(self, context: LibraryContext) -> None:
        store.toggle_favorite(context, "a.safetensors", True)
        state, _mtime = store.reorder_favorites(context, ["ghost.safetensors", "a.safetensors"])
        assert state["favorites"] == ["a.safetensors"]

    def test_missing_favorites_are_appended_in_stable_order(
        self, context: LibraryContext
    ) -> None:
        store.toggle_favorite(context, "a.safetensors", True)
        store.toggle_favorite(context, "b.safetensors", True)
        store.toggle_favorite(context, "c.safetensors", True)
        # Only "b" is named in the reorder -- "a" and "c" (unmentioned) land
        # after it, in their EXISTING relative order (a before c).
        state, _mtime = store.reorder_favorites(context, ["b.safetensors"])
        assert state["favorites"] == ["b.safetensors", "a.safetensors", "c.safetensors"]

    def test_duplicate_entries_in_files_dedup_to_first_occurrence(
        self, context: LibraryContext
    ) -> None:
        store.toggle_favorite(context, "a.safetensors", True)
        store.toggle_favorite(context, "b.safetensors", True)
        state, _mtime = store.reorder_favorites(
            context, ["b.safetensors", "a.safetensors", "b.safetensors"]
        )
        assert state["favorites"] == ["b.safetensors", "a.safetensors"]

    def test_names_normalized_to_forward_slashes(self, context: LibraryContext) -> None:
        store.toggle_favorite(context, "styles/film_grain.safetensors", True)
        state, _mtime = store.reorder_favorites(context, ["styles\\film_grain.safetensors"])
        assert state["favorites"] == ["styles/film_grain.safetensors"]

    def test_recents_untouched(self, context: LibraryContext) -> None:
        store.toggle_favorite(context, "a.safetensors", True)
        store.record_recents(context, ["r.safetensors"])
        state, _mtime = store.reorder_favorites(context, ["a.safetensors"])
        assert [row["file"] for row in state["recents"]] == ["r.safetensors"]

    def test_non_list_files_raises(self, context: LibraryContext) -> None:
        with pytest.raises(store.PickerStoreError):
            store.reorder_favorites(context, "a.safetensors")

    def test_non_string_entry_raises_naming_the_index(self, context: LibraryContext) -> None:
        with pytest.raises(store.PickerStoreError, match=r"files\[1\]"):
            store.reorder_favorites(context, ["a.safetensors", 123])

    def test_blank_entry_raises(self, context: LibraryContext) -> None:
        with pytest.raises(store.PickerStoreError):
            store.reorder_favorites(context, ["   "])

    def test_stale_base_mtime_raises_conflict_and_leaves_file_untouched(
        self, context: LibraryContext
    ) -> None:
        store.toggle_favorite(context, "a.safetensors", True)
        _state, real_mtime = store.toggle_favorite(context, "b.safetensors", True)
        with pytest.raises(store.ConflictError) as excinfo:
            store.reorder_favorites(
                context, ["b.safetensors", "a.safetensors"], base_mtime=real_mtime - 100.0
            )
        assert excinfo.value.current_mtime == real_mtime
        state, _mtime = store.load_state(context)
        assert state["favorites"] == ["a.safetensors", "b.safetensors"]  # untouched

    def test_matching_base_mtime_succeeds(self, context: LibraryContext) -> None:
        store.toggle_favorite(context, "a.safetensors", True)
        _state, real_mtime = store.toggle_favorite(context, "b.safetensors", True)
        state, _mtime = store.reorder_favorites(
            context, ["b.safetensors", "a.safetensors"], base_mtime=real_mtime
        )
        assert state["favorites"] == ["b.safetensors", "a.safetensors"]


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


# -------------------------------------------------- unreachable library dir


@pytest.fixture
def unreachable_context(context: LibraryContext, tmp_path: Path) -> LibraryContext:
    """*context* reconfigured so ``library_dir()``'s on-demand mkdir raises
    OSError: the configured path's parent is a plain FILE -- the portable
    test stand-in for an unmounted/unwritable NAS library folder (mirrors
    ``test_resolution_presets_store.py``'s identical fixture)."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    context.save_config({"library_dir": str(blocker / "library")})
    return context


class TestUnreachableLibraryFolder:
    """picker_path() resolves through context.library_dir(), whose mkdir
    raises when the configured folder is unreachable. Reads must degrade
    (warned, like the unreadable-file branch); writes must raise
    PickerStoreError so the routes answer 400, never 500."""

    def test_load_state_degrades_to_empty_with_a_warning(
        self, unreachable_context: LibraryContext, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("WARNING"):
            state, mtime = store.load_state(unreachable_context)
        assert state == {"favorites": [], "recents": []}
        assert mtime is None
        assert any("unreachable" in r.message for r in caplog.records)

    def test_toggle_favorite_raises_picker_store_error_naming_the_folder(
        self, unreachable_context: LibraryContext
    ) -> None:
        with pytest.raises(store.PickerStoreError, match="library folder"):
            store.toggle_favorite(unreachable_context, "a.safetensors", True)

    def test_record_recents_raises_picker_store_error_naming_the_folder(
        self, unreachable_context: LibraryContext
    ) -> None:
        with pytest.raises(store.PickerStoreError, match="library folder"):
            store.record_recents(unreachable_context, ["a.safetensors"])

    def test_clear_recents_raises_picker_store_error_naming_the_folder(
        self, unreachable_context: LibraryContext
    ) -> None:
        with pytest.raises(store.PickerStoreError, match="library folder"):
            store.clear_recents(unreachable_context)

    def test_reorder_favorites_raises_picker_store_error_naming_the_folder(
        self, unreachable_context: LibraryContext
    ) -> None:
        with pytest.raises(store.PickerStoreError, match="library folder"):
            store.reorder_favorites(unreachable_context, ["a.safetensors"])

    def test_toggle_favorite_still_validates_name_first(
        self, unreachable_context: LibraryContext
    ) -> None:
        # A structurally bad name is the caller's bug regardless of disk
        # state -- it must keep winning over the folder diagnosis.
        with pytest.raises(store.PickerStoreError):
            store.toggle_favorite(unreachable_context, "   ", True)

    def test_reorder_favorites_still_validates_files_first(
        self, unreachable_context: LibraryContext
    ) -> None:
        # Same rationale: a structurally bad `files` entry is the caller's
        # bug regardless of disk state -- it must keep winning over the
        # folder diagnosis.
        with pytest.raises(store.PickerStoreError):
            store.reorder_favorites(unreachable_context, ["   "])


# ------------------------------------------------------------- atomic writes


class TestAtomicWrites:
    def test_toggle_favorite_leaves_no_temp_files_behind(
        self, context: LibraryContext, library_dir: Path
    ) -> None:
        store.toggle_favorite(context, "a.safetensors", True)
        assert list(library_dir.glob("*.tmp")) == []

    def test_record_recents_leaves_no_temp_files_behind(
        self, context: LibraryContext, library_dir: Path
    ) -> None:
        store.record_recents(context, ["a.safetensors"])
        assert list(library_dir.glob("*.tmp")) == []

    def test_failed_replace_leaves_original_file_untouched(
        self, context: LibraryContext, library_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store.toggle_favorite(context, "a.safetensors", True)
        original_text = (library_dir / store.PICKER_FILENAME).read_text(encoding="utf-8")

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise OSError("simulated disk failure")

        monkeypatch.setattr(os, "replace", _boom)
        with pytest.raises(OSError):
            store.toggle_favorite(context, "b.safetensors", True)

        assert (
            library_dir / store.PICKER_FILENAME
        ).read_text(encoding="utf-8") == original_text
        assert list(library_dir.glob("*.tmp")) == []


# ------------------------------------------------------------------- shape


class TestOnDiskShape:
    def test_matches_documented_shape(self, context: LibraryContext, library_dir: Path) -> None:
        store.toggle_favorite(context, "a.safetensors", True)
        store.record_recents(context, ["b.safetensors"])
        on_disk = json.loads((library_dir / store.PICKER_FILENAME).read_text(encoding="utf-8"))
        assert on_disk["format"] == 1
        assert on_disk["favorites"] == ["a.safetensors"]
        assert len(on_disk["recents"]) == 1
        assert on_disk["recents"][0]["file"] == "b.safetensors"
        assert isinstance(on_disk["recents"][0]["ts"], float)


# -------------------------------------------------- non-UTF-8 files (review)


class TestNonUtf8Files:
    """Review 2026-08-09: UnicodeDecodeError is a ValueError, not an OSError,
    so a non-UTF-8 picker file (or config.json) used to escape the degrade
    guards and 500 every picker route. Reads must degrade, writes must work."""

    def test_utf16_picker_file_degrades_to_empty_with_a_warning(
        self, context: LibraryContext, library_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        payload = json.dumps({"format": 1, "favorites": ["a.safetensors"], "recents": []})
        (library_dir / store.PICKER_FILENAME).write_bytes(payload.encode("utf-16"))
        with caplog.at_level("WARNING"):
            state, mtime = store.load_state(context)
        assert state == {"favorites": [], "recents": []}
        # The decode failure takes the could-not-READ branch, which returns
        # before the stat -- so no mtime, same as an unreadable file.
        assert mtime is None
        assert any("could not read" in r.message for r in caplog.records)

    def test_utf16_config_json_degrades_reads_and_still_allows_writes(
        self, context: LibraryContext, caplog: pytest.LogCaptureFixture
    ) -> None:
        # context.load_config() must swallow the UnicodeDecodeError and fall
        # back to defaults (context.py fix), so library_dir() resolves to the
        # default folder instead of propagating out of every store call.
        context.user_dir.mkdir(parents=True, exist_ok=True)
        (context.user_dir / "config.json").write_bytes(
            json.dumps({"library_dir": "/nowhere"}).encode("utf-16")
        )
        with caplog.at_level("WARNING"):
            state, _ = store.load_state(context)
        assert state == {"favorites": [], "recents": []}

        new_state, mtime = store.toggle_favorite(context, "a.safetensors", True)
        assert new_state["favorites"] == ["a.safetensors"]
        assert isinstance(mtime, float)
        assert (context.default_library_dir / store.PICKER_FILENAME).is_file()
