"""Tests for lora_library.sets_store (FORMAT.md §4).

Uses the shared ``context``/``library_dir`` fixtures from conftest.py
(``FAKE_LORAS``: ``detailer.safetensors``, ``styles/film_grain.safetensors``,
``styles/cinematic.safetensors``).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from lora_library import sets_store
from lora_library.context import LibraryContext
from lora_library.routes import SLUG_RE

# 2026-08-26 while-running round test helpers: monkeypatch-based call
# counters shared by TestSaveDeleteWarmCacheSplice (finding 1) and
# TestLoadSetCache (finding 2), each scoped to exactly the seam the
# corresponding cache is meant to bypass on a warm hit.


def _scan_counter(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Counts real `_scan_sets` calls -- the full directory rescan
    finding 1's listing splice is meant to make unnecessary on a warm hit."""
    calls: list[int] = []
    original = sets_store._scan_sets

    def counting(context: LibraryContext, sets_dir: Path) -> list[dict]:
        calls.append(1)
        return original(context, sets_dir)

    monkeypatch.setattr(sets_store, "_scan_sets", counting)
    return calls


def _parse_counter(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Counts real `normalize_set` calls -- the read+parse+validate work
    finding 2's load cache is meant to skip on a warm (mtime_ns, size) hit.
    """
    calls: list[int] = []
    original = sets_store.normalize_set

    def counting(raw: object) -> dict:
        calls.append(1)
        return original(raw)

    monkeypatch.setattr(sets_store, "normalize_set", counting)
    return calls


def _stat_counter(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Counts real `Path.stat` calls -- finding 2's freshness check must
    NEVER be skipped, only the parse behind it."""
    calls: list[int] = []
    original = Path.stat

    def counting(self: Path, *args: object, **kwargs: object) -> object:
        calls.append(1)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", counting)
    return calls

# ------------------------------------------------------------------- slugify


class TestSlugify:
    def test_lowercases_and_turns_spaces_into_hyphens(self) -> None:
        assert sets_store.slugify("Cinematic Portrait") == "cinematic-portrait"

    def test_collapses_whitespace_runs_to_one_hyphen(self) -> None:
        assert sets_store.slugify("Multi   Word   Name") == "multi-word-name"

    def test_strips_characters_outside_allowed_set(self) -> None:
        assert sets_store.slugify("Foo! Bar?") == "foo-bar"

    def test_unicode_letters_are_stripped_not_transliterated(self) -> None:
        # v1 deliberately drops non-ASCII rather than transliterating.
        assert sets_store.slugify("Café Style") == "caf-style"

    def test_emoji_only_name_falls_back_to_set(self) -> None:
        assert sets_store.slugify("\U0001f3a8\U0001f3a8\U0001f3a8") == "set"

    def test_mixed_emoji_and_words_keeps_the_words(self) -> None:
        assert sets_store.slugify("\U0001f3a8 Style \U0001f3a8") == "style"

    def test_empty_or_blank_name_falls_back_to_set(self) -> None:
        assert sets_store.slugify("") == "set"
        assert sets_store.slugify("   ") == "set"

    def test_all_punctuation_name_falls_back_to_set(self) -> None:
        assert sets_store.slugify("---") == "set"
        assert sets_store.slugify("!!!") == "set"

    def test_leading_underscore_is_trimmed_so_result_satisfies_slug_re(self) -> None:
        slug = sets_store.slugify("_leading")
        assert slug == "leading"
        assert SLUG_RE.match(slug)

    def test_result_always_satisfies_slug_re_for_a_sample_of_tricky_names(self) -> None:
        for name in ["", "   ", "---", "_x", "\U0001f3a8", "Café", "!!!leading punctuation"]:
            slug = sets_store.slugify(name)
            assert SLUG_RE.match(slug), f"{name!r} -> {slug!r} does not satisfy SLUG_RE"


class TestSlugCollision:
    def test_repeated_name_gets_dash_two_dash_three(self, context: LibraryContext) -> None:
        slug1, _ = sets_store.save_set(context, {"name": "Foo", "loras": []})
        slug2, _ = sets_store.save_set(context, {"name": "Foo", "loras": []})
        slug3, _ = sets_store.save_set(context, {"name": "Foo", "loras": []})
        assert (slug1, slug2, slug3) == ("foo", "foo-2", "foo-3")

    def test_collision_numbering_fills_from_what_is_actually_on_disk(
        self, context: LibraryContext
    ) -> None:
        sets_store.save_set(context, {"name": "Foo", "loras": []}, slug="foo")
        sets_store.save_set(context, {"name": "Foo", "loras": []}, slug="foo-2")
        # Neither prior save went through derivation, but derivation must
        # still see both files and land on foo-3.
        slug, _ = sets_store.save_set(context, {"name": "Foo", "loras": []})
        assert slug == "foo-3"

    def test_explicit_slug_bypasses_collision_numbering_and_overwrites(
        self, context: LibraryContext
    ) -> None:
        slug, _ = sets_store.save_set(context, {"name": "Foo", "loras": []}, slug="foo")
        assert slug == "foo"
        slug_again, data = sets_store.save_set(
            context, {"name": "Foo Updated", "loras": []}, slug="foo"
        )
        assert slug_again == "foo"
        assert data["name"] == "Foo Updated"
        assert sets_store.list_sets(context) == [{"slug": "foo", "name": "Foo Updated", "count": 0}]


# -------------------------------------------------------------- format field


class TestFormatValidation:
    def test_format_1_is_accepted(self) -> None:
        result = sets_store.normalize_set({"format": 1, "name": "x", "loras": []})
        assert result["format"] == 1

    def test_missing_format_on_a_single_loader_payload_defaults_to_format_1(self) -> None:
        """FORMAT.md §4.1: the OUTPUT format is derived from whether
        `loaders` is present — never copied from (or defaulted to) whatever
        `format` int the payload declares or omits. A plain single-loader
        payload always normalizes to format 1, even now that
        ``CURRENT_FORMAT`` (the highest format this reader UNDERSTANDS) is
        2 — that ceiling is not the same thing as "the default format for a
        payload that doesn't look composite"."""
        result = sets_store.normalize_set({"name": "x", "loras": []})
        assert result["format"] == 1

    def test_format_greater_than_current_is_rejected_with_update_the_pack_message(self) -> None:
        too_new = sets_store.CURRENT_FORMAT + 1
        with pytest.raises(sets_store.SetValidationError, match="update the pack"):
            sets_store.normalize_set({"format": too_new, "name": "x", "loras": []})

    def test_non_int_format_is_rejected(self) -> None:
        with pytest.raises(sets_store.SetValidationError, match="format"):
            sets_store.normalize_set({"format": "1", "name": "x", "loras": []})

    def test_bool_format_is_rejected(self) -> None:
        with pytest.raises(sets_store.SetValidationError, match="format"):
            sets_store.normalize_set({"format": True, "name": "x", "loras": []})

    def test_load_set_with_a_too_new_format_on_disk_raises_on_load(
        self, context: LibraryContext, library_dir: Path
    ) -> None:
        too_new = sets_store.CURRENT_FORMAT + 1
        path = library_dir / "sets" / "future.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"format": too_new, "name": "x", "loras": []}), encoding="utf-8")
        with pytest.raises(sets_store.SetValidationError, match="update the pack"):
            sets_store.load_set(context, "future")

    def test_format_2_labeled_file_with_no_loaders_key_degrades_to_format_1(self) -> None:
        """FORMAT.md §4.1: "no loaders key" is format 1 regardless of the
        declared `format` int — this is the NEW behavior that supersedes
        the pre-§4.1 contract (where any `format` greater than 1 was
        rejected outright): a hand-edited file, or a `format: 2` file that
        just never got a composite capture, degrades gracefully instead of
        being refused."""
        result = sets_store.normalize_set(
            {"format": 2, "name": "x", "loras": [{"file": "a.safetensors"}]}
        )
        assert result["format"] == 1
        assert "loaders" not in result
        assert result["loras"] == [
            {"file": "a.safetensors", "on": True, "strength": 1.0, "strength_clip": None}
        ]


# --------------------------------------------------------- format 2 composite


class TestFormat2Composite:
    """FORMAT.md §4.1 — the composite multi-loader state schema."""

    @staticmethod
    def _composite_payload(**overrides: object) -> dict:
        payload = {
            "format": 2,
            "name": "WAN hi+lo",
            "loaders": [
                {"loras": [{"file": "detailer.safetensors", "strength": 0.8}]},
                {"loras": [{"file": "styles/film_grain.safetensors", "strength": 0.3}]},
            ],
            "trigger_words": "",
            "notes": "",
        }
        payload.update(overrides)
        return payload

    def test_format_2_with_loaders_normalizes_to_format_2(self) -> None:
        result = sets_store.normalize_set(self._composite_payload())
        assert result["format"] == 2
        assert len(result["loaders"]) == 2

    def test_top_level_loras_mirrors_loaders_0(self) -> None:
        result = sets_store.normalize_set(self._composite_payload())
        assert result["loras"] == result["loaders"][0]["loras"]
        assert result["loras"][0]["file"] == "detailer.safetensors"

    def test_top_level_loras_is_recomputed_even_if_raw_payload_disagreed(self) -> None:
        # A stale/hand-edited top-level `loras` must never win over
        # loaders[0] — FORMAT.md §4.1: "ALWAYS keep top-level loras ==
        # loaders[0].loras in sync."
        payload = self._composite_payload(loras=[{"file": "totally-different.safetensors"}])
        result = sets_store.normalize_set(payload)
        assert result["loras"] == result["loaders"][0]["loras"]
        assert result["loras"][0]["file"] == "detailer.safetensors"

    def test_each_loader_keeps_its_own_distinct_rows(self) -> None:
        result = sets_store.normalize_set(self._composite_payload())
        assert result["loaders"][0]["loras"][0]["file"] == "detailer.safetensors"
        assert result["loaders"][0]["loras"][0]["strength"] == 0.8
        assert result["loaders"][1]["loras"][0]["file"] == "styles/film_grain.safetensors"
        assert result["loaders"][1]["loras"][0]["strength"] == 0.3

    def test_loader_entry_that_is_not_an_object_is_rejected(self) -> None:
        payload = self._composite_payload(loaders=["nope", {"loras": []}])
        with pytest.raises(sets_store.SetValidationError, match=r"loaders\[0\]"):
            sets_store.normalize_set(payload)

    def test_bad_row_inside_a_loader_is_rejected_with_a_loader_scoped_message(self) -> None:
        payload = self._composite_payload(
            loaders=[{"loras": [{"on": True}]}, {"loras": []}]  # row 0 is missing 'file'
        )
        with pytest.raises(sets_store.SetValidationError, match=r"loaders\[0\]\.loras\[0\]"):
            sets_store.normalize_set(payload)

    def test_empty_loaders_list_degrades_to_format_1_and_logs_a_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        payload = self._composite_payload(loaders=[], loras=[{"file": "detailer.safetensors"}])
        with caplog.at_level(logging.WARNING, logger="lora_library"):
            result = sets_store.normalize_set(payload)
        assert result["format"] == 1
        assert "loaders" not in result
        assert result["loras"][0]["file"] == "detailer.safetensors"
        assert any("loaders" in r.message for r in caplog.records)

    def test_non_list_loaders_degrades_to_format_1_and_logs_a_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        payload = self._composite_payload(loaders="nope", loras=[{"file": "detailer.safetensors"}])
        with caplog.at_level(logging.WARNING, logger="lora_library"):
            result = sets_store.normalize_set(payload)
        assert result["format"] == 1
        assert result["loras"][0]["file"] == "detailer.safetensors"

    def test_save_then_load_round_trips_a_composite_state(self, context: LibraryContext) -> None:
        slug, saved = sets_store.save_set(context, self._composite_payload())
        loaded = sets_store.load_set(context, slug)
        assert loaded == saved
        assert loaded["format"] == 2
        assert len(loaded["loaders"]) == 2
        assert loaded["loaders"][0]["loras"][0]["file"] == "detailer.safetensors"
        assert loaded["loaders"][1]["loras"][0]["file"] == "styles/film_grain.safetensors"
        assert loaded["loras"] == loaded["loaders"][0]["loras"]

    def test_saved_composite_file_on_disk_has_format_2_and_a_loaders_array(
        self, context: LibraryContext, library_dir: Path
    ) -> None:
        slug, _ = sets_store.save_set(context, self._composite_payload())
        path = library_dir / "sets" / f"{slug}.json"
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk["format"] == 2
        assert len(on_disk["loaders"]) == 2
        assert on_disk["loras"] == on_disk["loaders"][0]["loras"]

    def test_plain_format_1_save_is_unaffected_by_the_format_2_addition(
        self, context: LibraryContext
    ) -> None:
        slug, saved = sets_store.save_set(context, {"name": "Plain", "loras": []})
        assert saved["format"] == 1
        assert "loaders" not in saved
        loaded = sets_store.load_set(context, slug)
        assert loaded["format"] == 1
        assert "loaders" not in loaded

    def test_list_sets_count_reflects_loaders_0_row_count_for_a_composite_state(
        self, context: LibraryContext
    ) -> None:
        sets_store.save_set(context, self._composite_payload())
        listed = sets_store.list_sets(context)
        assert listed[0]["count"] == 1  # loaders[0] has exactly one row


# --------------------------------------------------------------- loras_for_slot


class TestLorasForSlot:
    """FORMAT.md §4.1: the per-slot slice helper `nodes_sets.py`'s Apply
    `loader_slot` (and controller.js's hand-kept-in-sync JS mirror,
    `lorasForLoaderIndex()`) both rely on. Never raises."""

    def test_format_1_state_returns_loras_regardless_of_slot(self) -> None:
        state = {"format": 1, "loras": [{"file": "a.safetensors"}]}
        assert sets_store.loras_for_slot(state, 0) == state["loras"]
        assert sets_store.loras_for_slot(state, 5) == state["loras"]

    @staticmethod
    def _composite_state() -> dict:
        return sets_store.normalize_set(
            {
                "format": 2,
                "loaders": [
                    {"loras": [{"file": "high.safetensors"}]},
                    {"loras": [{"file": "low.safetensors"}]},
                ],
            }
        )

    def test_format_2_slot_0_returns_loaders_0(self) -> None:
        state = self._composite_state()
        assert sets_store.loras_for_slot(state, 0)[0]["file"] == "high.safetensors"

    def test_format_2_slot_1_returns_loaders_1_and_differs_from_slot_0(self) -> None:
        state = self._composite_state()
        slot0 = sets_store.loras_for_slot(state, 0)
        slot1 = sets_store.loras_for_slot(state, 1)
        assert slot0 != slot1
        assert slot1[0]["file"] == "low.safetensors"

    def test_out_of_range_slot_clamps_to_the_last_loader(self) -> None:
        state = self._composite_state()
        assert sets_store.loras_for_slot(state, 99)[0]["file"] == "low.safetensors"

    def test_negative_slot_clamps_to_zero(self) -> None:
        state = self._composite_state()
        assert sets_store.loras_for_slot(state, -3)[0]["file"] == "high.safetensors"

    def test_non_dict_state_returns_empty_list_without_raising(self) -> None:
        assert sets_store.loras_for_slot(None, 0) == []  # type: ignore[arg-type]
        assert sets_store.loras_for_slot("nope", 0) == []  # type: ignore[arg-type]

    def test_empty_state_returns_empty_list(self) -> None:
        assert sets_store.loras_for_slot({}, 0) == []

    def test_malformed_loaders_falls_back_to_top_level_loras(self) -> None:
        # Defensive against hand-built dicts that bypassed normalize_set —
        # loras_for_slot never trusts `loaders` being well-shaped.
        state = {"loaders": "nope", "loras": [{"file": "a.safetensors"}]}
        assert sets_store.loras_for_slot(state, 0) == state["loras"]

    def test_empty_loaders_list_falls_back_to_top_level_loras(self) -> None:
        state = {"loaders": [], "loras": [{"file": "a.safetensors"}]}
        assert sets_store.loras_for_slot(state, 0) == state["loras"]

    def test_non_int_slot_falls_back_to_zero_without_raising(self) -> None:
        state = self._composite_state()
        assert sets_store.loras_for_slot(state, "not-a-number")[0]["file"] == "high.safetensors"


# --------------------------------------------------------- shape validation


class TestShapeValidation:
    def test_non_dict_payload_is_rejected(self) -> None:
        with pytest.raises(sets_store.SetValidationError):
            sets_store.normalize_set(["not", "a", "dict"])  # type: ignore[arg-type]

    def test_non_list_loras_is_rejected_with_clear_message(self) -> None:
        with pytest.raises(sets_store.SetValidationError, match="loras"):
            sets_store.normalize_set({"format": 1, "name": "x", "loras": "nope"})

    def test_non_dict_lora_row_is_rejected_with_clear_message(self) -> None:
        with pytest.raises(sets_store.SetValidationError, match=r"loras\[0\]"):
            sets_store.normalize_set({"format": 1, "name": "x", "loras": ["nope"]})

    def test_row_missing_file_is_rejected(self) -> None:
        with pytest.raises(sets_store.SetValidationError, match="file"):
            sets_store.normalize_set({"format": 1, "name": "x", "loras": [{"on": True}]})

    def test_row_with_non_string_file_is_rejected(self) -> None:
        with pytest.raises(sets_store.SetValidationError, match="file"):
            sets_store.normalize_set({"format": 1, "name": "x", "loras": [{"file": 5}]})

    def test_string_strength_is_rejected_with_clear_message(self) -> None:
        with pytest.raises(sets_store.SetValidationError, match="strength"):
            sets_store.normalize_set(
                {"format": 1, "name": "x", "loras": [{"file": "a.safetensors", "strength": "0.8"}]}
            )

    def test_string_strength_clip_is_rejected(self) -> None:
        with pytest.raises(sets_store.SetValidationError, match="strength_clip"):
            sets_store.normalize_set(
                {
                    "format": 1,
                    "name": "x",
                    "loras": [{"file": "a.safetensors", "strength_clip": "0.8"}],
                }
            )


class TestRowDefaults:
    def test_missing_optional_row_fields_get_documented_defaults(self) -> None:
        result = sets_store.normalize_set(
            {"format": 1, "name": "x", "loras": [{"file": "a.safetensors"}]}
        )
        assert result["loras"][0] == {
            "file": "a.safetensors",
            "on": True,
            "strength": 1.0,
            "strength_clip": None,
        }

    def test_missing_trigger_words_and_notes_default_to_empty_string(self) -> None:
        result = sets_store.normalize_set({"format": 1, "name": "x", "loras": []})
        assert result["trigger_words"] == ""
        assert result["notes"] == ""

    def test_missing_name_defaults_to_empty_string(self) -> None:
        result = sets_store.normalize_set({"format": 1, "loras": []})
        assert result["name"] == ""

    def test_int_strength_is_coerced_to_float(self) -> None:
        result = sets_store.normalize_set(
            {"format": 1, "name": "x", "loras": [{"file": "a.safetensors", "strength": 1}]}
        )
        strength = result["loras"][0]["strength"]
        assert strength == 1.0
        assert isinstance(strength, float)

    def test_explicit_strength_clip_null_is_preserved_as_none(self) -> None:
        result = sets_store.normalize_set(
            {
                "format": 1,
                "name": "x",
                "loras": [{"file": "a.safetensors", "strength_clip": None}],
            }
        )
        assert result["loras"][0]["strength_clip"] is None

    def test_off_rows_are_kept_not_dropped(self) -> None:
        result = sets_store.normalize_set(
            {"format": 1, "name": "x", "loras": [{"file": "a.safetensors", "on": False}]}
        )
        assert result["loras"][0]["on"] is False


# -------------------------------------------------------------- persistence


class TestRoundTrip:
    def test_save_then_load_preserves_row_order_and_fields(self, context: LibraryContext) -> None:
        payload = {
            "format": 1,
            "name": "Cinematic portrait",
            "loras": [
                {
                    "file": "subdir/detailer.safetensors",
                    "on": True,
                    "strength": 0.8,
                    "strength_clip": None,
                },
                {
                    "file": "film_grain.safetensors",
                    "on": False,
                    "strength": 1.0,
                    "strength_clip": 0.5,
                },
            ],
            "trigger_words": "cinematic, film grain",
            "notes": "",
        }
        slug, saved = sets_store.save_set(context, payload)
        assert slug == "cinematic-portrait"
        loaded = sets_store.load_set(context, slug)
        assert loaded == saved
        assert [row["file"] for row in loaded["loras"]] == [
            "subdir/detailer.safetensors",
            "film_grain.safetensors",
        ]

    def test_saved_file_is_utf8_json_under_sets_dir(
        self, context: LibraryContext, library_dir: Path
    ) -> None:
        slug, _ = sets_store.save_set(context, {"name": "Foo", "loras": []})
        path = library_dir / "sets" / f"{slug}.json"
        assert path.exists()
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk["format"] == 1

    def test_load_missing_slug_returns_none(self, context: LibraryContext) -> None:
        assert sets_store.load_set(context, "does-not-exist") is None

    def test_delete_set_true_then_false(self, context: LibraryContext) -> None:
        slug, _ = sets_store.save_set(context, {"name": "Temp", "loras": []})
        assert sets_store.delete_set(context, slug) is True
        assert sets_store.load_set(context, slug) is None
        assert sets_store.delete_set(context, slug) is False


class TestListSets:
    def test_sorted_by_name_with_row_counts(self, context: LibraryContext) -> None:
        sets_store.save_set(context, {"name": "Zebra", "loras": [{"file": "a.safetensors"}]})
        sets_store.save_set(context, {"name": "Apple", "loras": []})
        listed = sets_store.list_sets(context)
        assert [entry["name"] for entry in listed] == ["Apple", "Zebra"]
        zebra = next(entry for entry in listed if entry["name"] == "Zebra")
        assert zebra["count"] == 1

    def test_empty_library_has_no_sets(self, context: LibraryContext) -> None:
        assert sets_store.list_sets(context) == []

    def test_skips_unreadable_file_and_logs_a_warning(
        self,
        context: LibraryContext,
        library_dir: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        bad = library_dir / "sets" / "broken.json"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("{not json", encoding="utf-8")
        sets_store.save_set(context, {"name": "Good", "loras": []})

        with caplog.at_level(logging.WARNING, logger="lora_library"):
            listed = sets_store.list_sets(context)

        assert [entry["name"] for entry in listed] == ["Good"]
        assert any("broken" in record.message for record in caplog.records)

    def test_skips_file_whose_stem_is_not_a_valid_slug(
        self,
        context: LibraryContext,
        library_dir: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # A hand-created file: valid §4 JSON, but the stem would be a slug
        # every set route 400s on — listing it would advertise a dead entry.
        bad = library_dir / "sets" / "My Set.json"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text(
            json.dumps({"format": 1, "name": "My Set", "loras": []}), encoding="utf-8"
        )
        sets_store.save_set(context, {"name": "Good", "loras": []})

        with caplog.at_level(logging.WARNING, logger="lora_library"):
            listed = sets_store.list_sets(context)

        assert [entry["slug"] for entry in listed] == ["good"]
        assert any(
            "My Set" in record.message and "rename" in record.message
            for record in caplog.records
        )


# -------------------------------------------------------------- lora lookup


class TestResolveLora:
    def test_exact_top_level_match(self, context: LibraryContext) -> None:
        assert sets_store.resolve_lora(context, "detailer.safetensors") == "detailer.safetensors"

    def test_exact_match_with_subdir(self, context: LibraryContext) -> None:
        assert (
            sets_store.resolve_lora(context, "styles/cinematic.safetensors")
            == "styles/cinematic.safetensors"
        )

    def test_unique_basename_match_resolves_across_subdir(self, context: LibraryContext) -> None:
        # FAKE_LORAS only has this file as styles/film_grain.safetensors; a
        # bare-basename request should still resolve (FORMAT.md §4:
        # "exact match first, then unique basename match").
        assert (
            sets_store.resolve_lora(context, "film_grain.safetensors")
            == "styles/film_grain.safetensors"
        )

    def test_no_match_at_all_returns_none(self, context: LibraryContext) -> None:
        assert sets_store.resolve_lora(context, "nope.safetensors") is None

    def test_no_match_at_all_does_not_log_an_ambiguous_warning(
        self, context: LibraryContext, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="lora_library"):
            result = sets_store.resolve_lora(context, "nope.safetensors")
        assert result is None
        assert not any("ambiguous" in r.message.lower() for r in caplog.records)
        assert not any("multiple" in r.message.lower() for r in caplog.records)

    def test_ambiguous_basename_is_skipped_rather_than_guessed(
        self, context: LibraryContext
    ) -> None:
        context.list_loras = lambda: ["a/dup.safetensors", "b/dup.safetensors"]
        assert sets_store.resolve_lora(context, "dup.safetensors") is None

    def test_ambiguous_basename_logs_a_reason_distinct_from_not_found(
        self, context: LibraryContext, caplog: pytest.LogCaptureFixture
    ) -> None:
        context.list_loras = lambda: ["a/dup.safetensors", "b/dup.safetensors"]
        with caplog.at_level(logging.WARNING, logger="lora_library"):
            result = sets_store.resolve_lora(context, "dup.safetensors")
        assert result is None
        messages = [r.message for r in caplog.records]
        assert any("multiple" in m.lower() for m in messages)
        assert any("a/dup.safetensors" in m and "b/dup.safetensors" in m for m in messages)


class TestResolveLoraCrossOS:
    """FORMAT.md §4: resolution is SEPARATOR-INSENSITIVE and returns the
    INSTALLED spelling — the pack's headline scenario is one library shared
    between a Windows PC (``folder_paths`` lists ``styles\\x``) and a Mac
    (``styles/x``)."""

    def test_windows_stored_value_exact_matches_posix_installed(
        self, context: LibraryContext
    ) -> None:
        # Set saved on the Windows PC, applied on the Mac (FAKE_LORAS are
        # posix-style). Same folder, same file — only the separator differs.
        assert (
            sets_store.resolve_lora(context, "styles\\film_grain.safetensors")
            == "styles/film_grain.safetensors"
        )

    def test_posix_stored_value_exact_matches_windows_installed(
        self, context: LibraryContext
    ) -> None:
        # The reverse trip: set saved on the Mac, applied on the Windows PC.
        # The returned value is the INSTALLED spelling, not the stored one.
        context.list_loras = lambda: [
            "detailer.safetensors",
            "styles\\film_grain.safetensors",
            "styles\\cinematic.safetensors",
        ]
        assert (
            sets_store.resolve_lora(context, "styles/film_grain.safetensors")
            == "styles\\film_grain.safetensors"
        )

    def test_windows_stored_subfolder_falls_back_to_basename_across_separators(
        self, context: LibraryContext
    ) -> None:
        # Different subfolder on each machine AND windows separators in the
        # stored value: exact-after-normalize fails, basename must still hit
        # (a `/`-only split would keep `old\location\` glued to the name).
        assert (
            sets_store.resolve_lora(context, "old\\location\\film_grain.safetensors")
            == "styles/film_grain.safetensors"
        )

    def test_posix_stored_subfolder_matches_windows_installed_by_basename(
        self, context: LibraryContext
    ) -> None:
        context.list_loras = lambda: ["styles\\film_grain.safetensors"]
        assert (
            sets_store.resolve_lora(context, "elsewhere/film_grain.safetensors")
            == "styles\\film_grain.safetensors"
        )

    def test_ambiguity_that_only_appears_after_separator_normalization(
        self, context: LibraryContext, caplog: pytest.LogCaptureFixture
    ) -> None:
        # A `/`-only basename split would read `a\dup.safetensors` as one
        # flat name (no basename collision → false unique match on b/);
        # separator-aware splitting sees the true two-way collision.
        context.list_loras = lambda: ["a\\dup.safetensors", "b/dup.safetensors"]
        with caplog.at_level(logging.WARNING, logger="lora_library"):
            result = sets_store.resolve_lora(context, "dup.safetensors")
        assert result is None
        assert any("multiple" in r.message.lower() for r in caplog.records)


# -------------------------------------------------- unreachable library dir


@pytest.fixture
def unreachable_context(context: LibraryContext, tmp_path: Path) -> LibraryContext:
    """*context* reconfigured so ``sets_dir()``'s on-demand mkdir raises
    OSError: the configured library path's parent is a plain FILE — the
    portable stand-in for an unmounted/unwritable NAS library folder (the
    audit 2026-08-08 failure the store's guards exist for)."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    context.save_config({"library_dir": str(blocker / "library")})
    return context


class TestUnreachableLibraryFolder:
    """sets_dir() creates the folder on demand, so an unreachable
    configured library raises OSError at directory RESOLUTION — before any
    per-file tolerance runs. Listing must degrade (warned, per its
    one-bad-thing-must-not-take-down-the-rest posture); save/delete/load
    must surface SetValidationError so the routes answer 4xx, never 500."""

    def test_list_sets_degrades_to_empty_with_a_warning(
        self, unreachable_context: LibraryContext, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="lora_library"):
            assert sets_store.list_sets(unreachable_context) == []
        assert any("unreachable" in r.message for r in caplog.records)

    def test_save_set_raises_set_validation_error_naming_the_folder(
        self, unreachable_context: LibraryContext
    ) -> None:
        with pytest.raises(sets_store.SetValidationError, match="library folder"):
            sets_store.save_set(unreachable_context, {"name": "Foo", "loras": []})

    def test_delete_set_raises_rather_than_reporting_nothing_to_delete(
        self, unreachable_context: LibraryContext
    ) -> None:
        # A plain False would read as 404 "no such set" at the route —
        # misdiagnosing an unreachable folder as a missing set.
        with pytest.raises(sets_store.SetValidationError, match="library folder"):
            sets_store.delete_set(unreachable_context, "foo")

    def test_load_set_raises_set_validation_error(
        self, unreachable_context: LibraryContext
    ) -> None:
        # Same class every caller already handles: the routes 400, and
        # nodes_sets warns + passes through instead of failing the prompt.
        with pytest.raises(sets_store.SetValidationError, match="library folder"):
            sets_store.load_set(unreachable_context, "foo")

    def test_save_set_still_validates_payload_first(
        self, unreachable_context: LibraryContext
    ) -> None:
        # A malformed payload is the caller's bug regardless of disk state
        # — its §4 message must keep winning over the folder diagnosis.
        with pytest.raises(sets_store.SetValidationError, match="loras"):
            sets_store.save_set(unreachable_context, {"name": "x", "loras": "nope"})


class TestSetsLayoutV0650:
    """§4.2 (v0.65.0): the controller pane's categories + order, in its own
    file so §4 set files never change shape (downgrade-safe), self-healing
    against the sets actually on disk."""

    def _mk(self, context: LibraryContext, name: str) -> str:
        slug, _ = sets_store.save_set(context, {"name": name, "loras": []})
        return slug

    def test_missing_file_heals_to_every_set_uncategorized_name_sorted(
        self, context: LibraryContext
    ) -> None:
        b = self._mk(context, "bravo")
        a = self._mk(context, "alpha")
        layout = sets_store.load_layout(context)
        assert layout == {"categories": [], "order": {"": [a, b]}}

    def test_save_drops_unknown_slugs_and_appends_missing_sets(
        self, context: LibraryContext
    ) -> None:
        a = self._mk(context, "alpha")
        b = self._mk(context, "bravo")
        c = self._mk(context, "charlie")
        saved = sets_store.save_layout(
            context,
            {"categories": ["Portraits"], "order": {"Portraits": [b, "ghost-slug", b], "": [a]}},
        )
        # ghost dropped, duplicate b kept once, missing c appended uncategorized
        assert saved == {"categories": ["Portraits"], "order": {"": [a, c], "Portraits": [b]}}
        # ...and what was written is what loads back
        assert sets_store.load_layout(context) == saved

    def test_empty_categories_persist(self, context: LibraryContext) -> None:
        saved = sets_store.save_layout(context, {"categories": ["Empty Group"], "order": {}})
        assert saved["categories"] == ["Empty Group"]
        assert sets_store.load_layout(context)["categories"] == ["Empty Group"]

    def test_malformed_file_degrades_to_rebuilt_layout(self, context: LibraryContext) -> None:
        a = self._mk(context, "alpha")
        sets_store.layout_path(context).write_text("{not json", encoding="utf-8")
        assert sets_store.load_layout(context) == {"categories": [], "order": {"": [a]}}

    def test_order_key_implies_category_membership(self, context: LibraryContext) -> None:
        """A category present only as an `order` key still counts -- a
        hand-edited or older file must not lose the group."""
        layout = sets_store.normalize_layout({"order": {"Styles": []}})
        assert layout["categories"] == ["Styles"]

    def test_layout_file_lives_outside_sets_dir(self, context: LibraryContext) -> None:
        """list_sets globs sets_dir/*.json and warns on non-slug stems --
        the layout must never be inside it."""
        assert sets_store.layout_path(context).parent != context.sets_dir()


# ------------------------------------ warm-cache splice (2026-08-26 while-running round)


class TestSaveDeleteWarmCacheSplice:
    """Finding 1: `save_set`/`delete_set` used to force the very next
    `list_sets()` call (routes_sets.py's save/delete handlers both tail one
    onto their response, per FORMAT.md §5) into a full disk rescan, by
    evicting the whole listing cache unconditionally on every write. They
    now splice the one changed slug into an already-cached listing instead,
    verified two ways below: (1) the spliced result is byte-for-byte what a
    FORCED fresh scan (`clear_caches()` then `list_sets()`) produces -- the
    finding's hard "response shape identical" requirement, order included
    -- and (2) no `_scan_sets` call happens at all when the listing was
    already warm.
    """

    def test_save_after_a_warm_listing_matches_a_forced_fresh_scan(
        self, context: LibraryContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sets_store.save_set(context, {"name": "Alpha", "loras": []})
        sets_store.save_set(context, {"name": "Zebra", "loras": []})
        sets_store.list_sets(context)  # warms the listing cache
        calls = _scan_counter(monkeypatch)

        sets_store.save_set(context, {"name": "Mango", "loras": [{"file": "a.safetensors"}]})
        spliced = sets_store.list_sets(context)

        assert calls == []  # the splice served it -- no rescan
        sets_store.clear_caches()
        forced = sets_store.list_sets(context)
        assert spliced == forced
        assert [e["name"] for e in spliced] == ["Alpha", "Mango", "Zebra"]

    def test_rename_via_explicit_slug_matches_a_forced_fresh_scan(
        self, context: LibraryContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slug, _ = sets_store.save_set(context, {"name": "Foo", "loras": []})
        sets_store.save_set(context, {"name": "Bravo", "loras": []})
        sets_store.list_sets(context)
        calls = _scan_counter(monkeypatch)

        sets_store.save_set(context, {"name": "Zzz Renamed", "loras": []}, slug=slug)
        spliced = sets_store.list_sets(context)

        assert calls == []
        sets_store.clear_caches()
        forced = sets_store.list_sets(context)
        assert spliced == forced
        assert [e["name"] for e in spliced] == ["Bravo", "Zzz Renamed"]

    def test_delete_after_a_warm_listing_matches_a_forced_fresh_scan(
        self, context: LibraryContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slug_a, _ = sets_store.save_set(context, {"name": "Alpha", "loras": []})
        sets_store.save_set(context, {"name": "Bravo", "loras": []})
        sets_store.save_set(context, {"name": "Charlie", "loras": []})
        sets_store.list_sets(context)
        calls = _scan_counter(monkeypatch)

        assert sets_store.delete_set(context, slug_a) is True
        spliced = sets_store.list_sets(context)

        assert calls == []
        sets_store.clear_caches()
        forced = sets_store.list_sets(context)
        assert spliced == forced
        assert [e["name"] for e in spliced] == ["Bravo", "Charlie"]

    def test_no_warm_listing_yet_falls_back_to_one_normal_scan(
        self, context: LibraryContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No regression for the cold-start case (nothing cached yet, e.g.
        right after the process starts): the splice is a documented no-op,
        so the very next `list_sets()` just scans once, exactly as it
        always did -- not zero (nothing to splice into), not more than one.
        """
        calls = _scan_counter(monkeypatch)
        sets_store.save_set(context, {"name": "Solo", "loras": []})
        listed = sets_store.list_sets(context)
        assert [e["name"] for e in listed] == ["Solo"]
        assert len(calls) == 1

    def test_delete_of_unknown_slug_leaves_a_warm_listing_untouched(
        self, context: LibraryContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """delete_set's FileNotFoundError branch (nothing to splice -- see
        its own comment) still forgets the listing outright rather than
        guessing; the following list_sets() call must still land on the
        correct, complete set -- just via one real rescan, not a splice."""
        sets_store.save_set(context, {"name": "Alpha", "loras": []})
        sets_store.list_sets(context)
        calls = _scan_counter(monkeypatch)

        assert sets_store.delete_set(context, "ghost-slug") is False
        listed = sets_store.list_sets(context)
        assert [e["name"] for e in listed] == ["Alpha"]
        assert len(calls) == 1  # forgotten, not spliced -- one real rescan


class TestLoadSetCache:
    """Finding 2: `load_set` (``GET /lora_library/set``) used to open+parse
    the file on EVERY call -- including the pin-drift check every pinned
    Apply-Set node fires. Backed now by a per-file (mtime_ns, size) cache,
    the same idiom `_entry_cache` already used for listings, applied to the
    FULL normalized dict this time."""

    def test_unchanged_file_parses_once_across_repeated_loads(
        self, context: LibraryContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slug, _ = sets_store.save_set(
            context, {"name": "Foo", "loras": [{"file": "a.safetensors"}]}
        )
        sets_store.load_set(context, slug)  # warms the load cache
        parses = _parse_counter(monkeypatch)

        first = sets_store.load_set(context, slug)
        second = sets_store.load_set(context, slug)
        third = sets_store.load_set(context, slug)

        assert parses == []  # every call was a warm (mtime_ns, size) hit
        assert first == second == third
        assert first["loras"][0]["file"] == "a.safetensors"

    def test_stat_still_runs_on_every_call_even_when_warm(
        self, context: LibraryContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The freshness check itself (the stat) is never skipped -- only
        the parse behind a cache hit is."""
        slug, _ = sets_store.save_set(context, {"name": "Foo", "loras": []})
        sets_store.load_set(context, slug)
        stats = _stat_counter(monkeypatch)

        sets_store.load_set(context, slug)
        sets_store.load_set(context, slug)

        assert len(stats) >= 2

    def test_save_set_warms_the_load_cache_so_the_next_load_is_free(
        self, context: LibraryContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """save_set warms `_load_cache` directly with what it just wrote
        (see its own 2026-08-26 comment) -- so a save followed by a load
        (the common "save, then re-fetch to confirm" shape) costs zero
        extra parses, not merely one instead of a full rescan."""
        slug, _ = sets_store.save_set(context, {"name": "Foo", "loras": []})
        sets_store.load_set(context, slug)
        parses = _parse_counter(monkeypatch)

        sets_store.save_set(
            context, {"name": "Foo", "loras": [{"file": "a.safetensors"}]}, slug=slug
        )
        # save_set's own `normalize_set(set_data)` call (unavoidable -- every
        # save validates its payload) is the only entry so far.
        assert len(parses) == 1
        reloaded = sets_store.load_set(context, slug)

        assert reloaded["loras"][0]["file"] == "a.safetensors"
        assert len(parses) == 1  # the follow-up load_set() added none

    def test_out_of_process_edit_is_detected_via_stat_and_reparsed(
        self, context: LibraryContext, library_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An edit that does NOT go through this process's own save_set
        (another machine sharing the NAS library, or a hand edit) can only
        be noticed via the file's own (mtime_ns, size) -- exercised here by
        writing straight to the file, bypassing save_set's cache-warming
        entirely. The new content differs in byte SIZE from what was
        cached, which alone forces a cache miss regardless of any
        filesystem's mtime resolution."""
        slug, _ = sets_store.save_set(context, {"name": "Foo", "loras": []})
        sets_store.load_set(context, slug)  # warms the load cache
        parses = _parse_counter(monkeypatch)

        path = library_dir / "sets" / f"{slug}.json"
        path.write_text(
            json.dumps({"format": 1, "name": "Foo", "loras": [{"file": "a.safetensors"}]}),
            encoding="utf-8",
        )

        reloaded = sets_store.load_set(context, slug)
        assert reloaded["loras"][0]["file"] == "a.safetensors"
        assert len(parses) == 1

    def test_returned_dict_is_independent_across_calls(self, context: LibraryContext) -> None:
        """Same "safe to mutate freely" contract list_sets() already
        documents for its summaries -- a cache hit must never hand back an
        aliased object."""
        slug, _ = sets_store.save_set(
            context, {"name": "Foo", "loras": [{"file": "a.safetensors"}]}
        )
        first = sets_store.load_set(context, slug)
        first["loras"][0]["strength"] = 999.0
        first["name"] = "MUTATED"

        second = sets_store.load_set(context, slug)
        assert second["name"] == "Foo"
        assert second["loras"][0]["strength"] == 1.0

    def test_missing_slug_is_still_none_with_the_cache_in_play(
        self, context: LibraryContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        parses = _parse_counter(monkeypatch)
        assert sets_store.load_set(context, "does-not-exist") is None
        assert parses == []
