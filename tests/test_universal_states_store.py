"""Tests for lora_library.universal_states_store (FORMAT.md §4.3).

Mirrors tests/test_sets_store.py's structure and conventions section by
section (slugify, format validation, persistence, listing, unreachable
library dir, layout, warm-cache splice, load cache) plus this store's own
new surface: §6.16 registry-backed widget validation (a fake ``nodes``
module installed into ``sys.modules``, exactly like
tests/test_routes_list_flags.py's ``fake_nodes`` fixture) and the
foreign-class tolerate-on-save posture.

Uses the shared ``context``/``library_dir`` fixtures from conftest.py.
"""

from __future__ import annotations

import json
import logging
import sys
import types
from pathlib import Path
from typing import Any, ClassVar

import pytest

from lora_library import universal_states_store as store
from lora_library.context import LibraryContext
from lora_library.routes import SLUG_RE

# ------------------------------------------------------- fake `nodes` module

@pytest.fixture
def fake_nodes(monkeypatch: pytest.MonkeyPatch):
    """Installs an empty fake ComfyUI ``nodes`` module into ``sys.modules``
    -- the same technique tests/test_routes_list_flags.py's ``fake_nodes``
    fixture uses for ``routes_list_flags``. Individual tests populate
    ``NODE_CLASS_MAPPINGS`` with whatever fake classes they need."""
    module = types.ModuleType("nodes")
    module.NODE_CLASS_MAPPINGS = {}
    monkeypatch.setitem(sys.modules, "nodes", module)
    return module


class _Resolution:
    """A stand-in for a real §6.16-registered node class (shape lifted
    from lora_library/nodes_sets.py's actual ``EPS_STATE_WIDGETS``)."""

    EPS_STATE_WIDGETS: ClassVar[dict[str, Any]] = {
        "format": 1,
        "widgets": {
            "width": {"kind": "int", "min": 64, "max": 8192},
            "height": {"kind": "int", "min": 64, "max": 8192},
            "resize_method": {"kind": "choice"},
            "strength": {"kind": "float", "min": 0.0, "max": 2.0},
            "notes_text": {"kind": "lines"},
            "file": {"kind": "string", "max_len": 10},
            "tags": {"kind": "json_array", "items": "string"},
            "per_loader": {"kind": "json_object", "key_pattern": r"loader_\d+"},
            "anything": {"kind": "json_object"},
        },
        "excluded": {
            "pinned_state": "provenance from a baked image, not user intent",
        },
    }


def _register(fake_nodes, class_id: str, cls: type) -> None:
    fake_nodes.NODE_CLASS_MAPPINGS[class_id] = cls


# ------------------------------------------------------------------- slugify


class TestSlugify:
    def test_lowercases_and_turns_spaces_into_hyphens(self) -> None:
        assert store.slugify("Portrait Pass") == "portrait-pass"

    def test_collapses_whitespace_runs_to_one_hyphen(self) -> None:
        assert store.slugify("Multi   Word   Name") == "multi-word-name"

    def test_strips_characters_outside_allowed_set(self) -> None:
        assert store.slugify("Foo! Bar?") == "foo-bar"

    def test_emoji_only_name_falls_back_to_state(self) -> None:
        assert store.slugify("\U0001f3a8\U0001f3a8") == "state"

    def test_empty_or_blank_name_falls_back_to_state(self) -> None:
        assert store.slugify("") == "state"
        assert store.slugify("   ") == "state"

    def test_leading_underscore_is_trimmed_so_result_satisfies_slug_re(self) -> None:
        slug = store.slugify("_leading")
        assert slug == "leading"
        assert SLUG_RE.match(slug)

    def test_result_always_satisfies_slug_re_for_a_sample_of_tricky_names(self) -> None:
        for name in ["", "   ", "---", "_x", "\U0001f3a8", "Café", "!!!leading"]:
            slug = store.slugify(name)
            assert SLUG_RE.match(slug), f"{name!r} -> {slug!r} does not satisfy SLUG_RE"


class TestSlugCollision:
    def test_repeated_name_gets_dash_two_dash_three(self, context: LibraryContext) -> None:
        slug1, _, _ = store.save_state(context, {"name": "Foo", "nodes": []})
        slug2, _, _ = store.save_state(context, {"name": "Foo", "nodes": []})
        slug3, _, _ = store.save_state(context, {"name": "Foo", "nodes": []})
        assert (slug1, slug2, slug3) == ("foo", "foo-2", "foo-3")

    def test_explicit_slug_bypasses_collision_numbering_and_overwrites(
        self, context: LibraryContext
    ) -> None:
        slug, _, _ = store.save_state(context, {"name": "Foo", "nodes": []}, slug="foo")
        assert slug == "foo"
        slug_again, data, _ = store.save_state(
            context, {"name": "Foo Updated", "nodes": []}, slug="foo"
        )
        assert slug_again == "foo"
        assert data["name"] == "Foo Updated"


# --------------------------------------------------------------- format field


class TestFormatValidation:
    def test_format_1_is_accepted(self) -> None:
        normalized, foreign = store.normalize_state({"format": 1, "name": "x", "nodes": []})
        assert normalized["format"] == 1
        assert foreign == []

    def test_missing_format_defaults_to_1(self) -> None:
        normalized, _ = store.normalize_state({"name": "x", "nodes": []})
        assert normalized["format"] == 1

    def test_format_greater_than_current_is_rejected_with_update_the_pack_message(self) -> None:
        too_new = store.CURRENT_FORMAT + 1
        with pytest.raises(store.StateValidationError, match="update the pack"):
            store.normalize_state({"format": too_new, "name": "x", "nodes": []})

    def test_non_int_format_is_rejected(self) -> None:
        with pytest.raises(store.StateValidationError, match="format"):
            store.normalize_state({"format": "1", "name": "x", "nodes": []})

    def test_bool_format_is_rejected(self) -> None:
        with pytest.raises(store.StateValidationError, match="format"):
            store.normalize_state({"format": True, "name": "x", "nodes": []})


class TestShapeValidation:
    def test_non_dict_payload_is_rejected(self) -> None:
        with pytest.raises(store.StateValidationError):
            store.normalize_state(["not", "a", "dict"])  # type: ignore[arg-type]

    def test_missing_name_is_rejected(self) -> None:
        with pytest.raises(store.StateValidationError, match="name"):
            store.normalize_state({"nodes": []})

    def test_blank_name_is_rejected(self) -> None:
        with pytest.raises(store.StateValidationError, match="name"):
            store.normalize_state({"name": "   ", "nodes": []})

    def test_non_string_name_is_rejected(self) -> None:
        with pytest.raises(store.StateValidationError, match="name"):
            store.normalize_state({"name": 5, "nodes": []})

    def test_non_string_notes_is_rejected(self) -> None:
        with pytest.raises(store.StateValidationError, match="notes"):
            store.normalize_state({"name": "x", "notes": 5, "nodes": []})

    def test_non_list_nodes_is_rejected(self) -> None:
        with pytest.raises(store.StateValidationError, match="nodes"):
            store.normalize_state({"name": "x", "nodes": "nope"})

    def test_missing_nodes_defaults_to_empty_list(self) -> None:
        normalized, _ = store.normalize_state({"name": "x"})
        assert normalized["nodes"] == []

    def test_captured_defaults_to_a_timestamp_when_omitted(self) -> None:
        normalized, _ = store.normalize_state({"name": "x", "nodes": []})
        assert normalized["captured"].endswith("Z")

    def test_captured_is_preserved_when_given(self) -> None:
        normalized, _ = store.normalize_state(
            {"name": "x", "nodes": [], "captured": "2026-08-26T12:00:00Z"}
        )
        assert normalized["captured"] == "2026-08-26T12:00:00Z"

    def test_non_string_captured_is_rejected(self) -> None:
        with pytest.raises(store.StateValidationError, match="captured"):
            store.normalize_state({"name": "x", "nodes": [], "captured": 5})


class TestNodeEntryShapeValidation:
    def test_non_dict_node_entry_is_rejected(self) -> None:
        with pytest.raises(store.StateValidationError, match=r"nodes\[0\]"):
            store.normalize_state({"name": "x", "nodes": ["nope"]})

    def test_missing_class_is_rejected(self) -> None:
        with pytest.raises(store.StateValidationError, match="class"):
            store.normalize_state({"name": "x", "nodes": [{"id": "3", "widgets": {}}]})

    def test_empty_class_is_rejected(self) -> None:
        with pytest.raises(store.StateValidationError, match="class"):
            store.normalize_state({"name": "x", "nodes": [{"class": "", "id": "3", "widgets": {}}]})

    def test_missing_id_is_rejected(self) -> None:
        with pytest.raises(store.StateValidationError, match="id"):
            store.normalize_state({"name": "x", "nodes": [{"class": "Foo", "widgets": {}}]})

    def test_non_dict_widgets_is_rejected(self) -> None:
        with pytest.raises(store.StateValidationError, match="widgets"):
            store.normalize_state(
                {"name": "x", "nodes": [{"class": "Foo", "id": "3", "widgets": "nope"}]}
            )

    def test_missing_title_defaults_to_empty_string(self) -> None:
        normalized, _ = store.normalize_state(
            {"name": "x", "nodes": [{"class": "Foo", "id": "3", "widgets": {}}]}
        )
        assert normalized["nodes"][0]["title"] == ""

    def test_non_string_title_is_rejected(self) -> None:
        with pytest.raises(store.StateValidationError, match="title"):
            store.normalize_state(
                {"name": "x", "nodes": [{"class": "Foo", "id": "3", "title": 5, "widgets": {}}]}
            )

    def test_subgraph_path_id_is_kept_opaque(self) -> None:
        normalized, _ = store.normalize_state(
            {"name": "x", "nodes": [{"class": "Foo", "id": "3:2", "widgets": {}}]}
        )
        assert normalized["nodes"][0]["id"] == "3:2"


# ------------------------------------------------ §6.16 registry validation


class TestRegistryValidationKnownClass:
    def test_every_kind_validates_a_correct_payload(self, fake_nodes) -> None:
        _register(fake_nodes, "EPSResolution", _Resolution)
        payload = {
            "name": "Hero size",
            "nodes": [
                {
                    "class": "EPSResolution",
                    "id": "12",
                    "title": "Hero size",
                    "widgets": {
                        "width": 1024,
                        "height": 1024,
                        "resize_method": "stretch",
                        "strength": 0.8,
                        "notes_text": "line one\nline two",
                        "file": "short.txt",
                        "tags": ["a", "b"],
                        "per_loader": {"loader_0": {"on": True}},
                        "anything": {"whatever": 1, "nested": [1, 2]},
                    },
                }
            ],
        }
        normalized, foreign = store.normalize_state(payload)
        assert foreign == []
        widgets = normalized["nodes"][0]["widgets"]
        assert widgets["width"] == 1024
        assert widgets["strength"] == 0.8
        assert isinstance(widgets["strength"], float)

    def test_int_field_accepts_bare_int(self, fake_nodes) -> None:
        _register(fake_nodes, "EPSResolution", _Resolution)
        normalized, _ = store.normalize_state(
            _payload_with_widgets({"width": 512, "height": 512})
        )
        assert normalized["nodes"][0]["widgets"] == {"width": 512, "height": 512}

    def test_bool_is_rejected_for_int_field(self, fake_nodes) -> None:
        _register(fake_nodes, "EPSResolution", _Resolution)
        with pytest.raises(store.StateValidationError, match="width"):
            store.normalize_state(_payload_with_widgets({"width": True}))

    def test_int_field_out_of_range_is_rejected(self, fake_nodes) -> None:
        _register(fake_nodes, "EPSResolution", _Resolution)
        with pytest.raises(store.StateValidationError, match="width"):
            store.normalize_state(_payload_with_widgets({"width": 1}))
        with pytest.raises(store.StateValidationError, match="width"):
            store.normalize_state(_payload_with_widgets({"width": 999999}))

    def test_float_field_accepts_int_and_coerces(self, fake_nodes) -> None:
        _register(fake_nodes, "EPSResolution", _Resolution)
        normalized, _ = store.normalize_state(_payload_with_widgets({"strength": 1}))
        value = normalized["nodes"][0]["widgets"]["strength"]
        assert value == 1.0
        assert isinstance(value, float)

    def test_float_field_out_of_range_is_rejected(self, fake_nodes) -> None:
        _register(fake_nodes, "EPSResolution", _Resolution)
        with pytest.raises(store.StateValidationError, match="strength"):
            store.normalize_state(_payload_with_widgets({"strength": 9.0}))

    def test_bool_is_rejected_for_float_field(self, fake_nodes) -> None:
        _register(fake_nodes, "EPSResolution", _Resolution)
        with pytest.raises(store.StateValidationError, match="strength"):
            store.normalize_state(_payload_with_widgets({"strength": False}))

    def test_choice_accepts_any_string(self, fake_nodes) -> None:
        _register(fake_nodes, "EPSResolution", _Resolution)
        normalized, _ = store.normalize_state(
            _payload_with_widgets({"resize_method": "literally-anything"})
        )
        assert normalized["nodes"][0]["widgets"]["resize_method"] == "literally-anything"

    def test_choice_rejects_non_string(self, fake_nodes) -> None:
        _register(fake_nodes, "EPSResolution", _Resolution)
        with pytest.raises(store.StateValidationError, match="resize_method"):
            store.normalize_state(_payload_with_widgets({"resize_method": 5}))

    def test_lines_accepts_multiline_string(self, fake_nodes) -> None:
        _register(fake_nodes, "EPSResolution", _Resolution)
        normalized, _ = store.normalize_state(
            _payload_with_widgets({"notes_text": "a\nb\nc"})
        )
        assert normalized["nodes"][0]["widgets"]["notes_text"] == "a\nb\nc"

    def test_string_max_len_enforced(self, fake_nodes) -> None:
        _register(fake_nodes, "EPSResolution", _Resolution)
        with pytest.raises(store.StateValidationError, match="file"):
            store.normalize_state(_payload_with_widgets({"file": "way-too-long-for-ten"}))

    def test_string_under_max_len_is_fine(self, fake_nodes) -> None:
        _register(fake_nodes, "EPSResolution", _Resolution)
        normalized, _ = store.normalize_state(_payload_with_widgets({"file": "ok.txt"}))
        assert normalized["nodes"][0]["widgets"]["file"] == "ok.txt"

    def test_json_array_rejects_non_list(self, fake_nodes) -> None:
        _register(fake_nodes, "EPSResolution", _Resolution)
        with pytest.raises(store.StateValidationError, match="tags"):
            store.normalize_state(_payload_with_widgets({"tags": "nope"}))

    def test_json_array_item_type_is_checked(self, fake_nodes) -> None:
        _register(fake_nodes, "EPSResolution", _Resolution)
        with pytest.raises(store.StateValidationError, match=r"tags\[1\]"):
            store.normalize_state(_payload_with_widgets({"tags": ["ok", 5]}))

    def test_json_object_rejects_non_dict(self, fake_nodes) -> None:
        _register(fake_nodes, "EPSResolution", _Resolution)
        with pytest.raises(store.StateValidationError, match="anything"):
            store.normalize_state(_payload_with_widgets({"anything": "nope"}))

    def test_json_object_without_key_pattern_accepts_any_keys(self, fake_nodes) -> None:
        _register(fake_nodes, "EPSResolution", _Resolution)
        normalized, _ = store.normalize_state(
            _payload_with_widgets({"anything": {"literally": "anything"}})
        )
        assert normalized["nodes"][0]["widgets"]["anything"] == {"literally": "anything"}

    def test_json_object_key_pattern_fullmatch_enforced(self, fake_nodes) -> None:
        _register(fake_nodes, "EPSResolution", _Resolution)
        normalized, _ = store.normalize_state(
            _payload_with_widgets({"per_loader": {"loader_0": {}, "loader_12": {}}})
        )
        assert set(normalized["nodes"][0]["widgets"]["per_loader"]) == {"loader_0", "loader_12"}

    def test_json_object_key_pattern_rejects_a_bad_key(self, fake_nodes) -> None:
        _register(fake_nodes, "EPSResolution", _Resolution)
        with pytest.raises(store.StateValidationError, match="per_loader"):
            store.normalize_state(_payload_with_widgets({"per_loader": {"bogus": {}}}))

    def test_key_pattern_is_a_fullmatch_not_a_search(self, fake_nodes) -> None:
        """A key pattern is anchored end-to-end -- "loader_0-extra" must not
        sneak past a `search`-style partial match."""
        _register(fake_nodes, "EPSResolution", _Resolution)
        with pytest.raises(store.StateValidationError, match="per_loader"):
            store.normalize_state(_payload_with_widgets({"per_loader": {"loader_0-extra": {}}}))

    def test_excluded_widget_is_rejected_naming_class_and_widget(self, fake_nodes) -> None:
        _register(fake_nodes, "EPSResolution", _Resolution)
        with pytest.raises(
            store.StateValidationError, match=r"EPSResolution\.pinned_state"
        ):
            store.normalize_state(_payload_with_widgets({"pinned_state": "{}"}))

    def test_unknown_widget_is_rejected_naming_class_and_widget(self, fake_nodes) -> None:
        _register(fake_nodes, "EPSResolution", _Resolution)
        with pytest.raises(
            store.StateValidationError, match=r"EPSResolution\.totally_bogus_widget"
        ):
            store.normalize_state(_payload_with_widgets({"totally_bogus_widget": 1}))


def _payload_with_widgets(widgets: dict) -> dict:
    return {
        "name": "x",
        "nodes": [{"class": "EPSResolution", "id": "12", "widgets": widgets}],
    }


class TestForeignClass:
    """FORMAT.md §4.3: a class with no registry entry (a future EPSNodes
    build, a third-party pack, or simply not loaded in THIS process) is
    tolerated on save -- kept as-is, never rejected -- and collected into
    the returned ``foreign`` list so the save route can warn about it."""

    def test_unregistered_class_is_kept_as_is_and_flagged_foreign(self, fake_nodes) -> None:
        payload = {
            "name": "x",
            "nodes": [
                {
                    "class": "SomeThirdPartyNode",
                    "id": "9",
                    "title": "Weird node",
                    "widgets": {"anything_goes": [1, "two", {"three": 3}]},
                }
            ],
        }
        normalized, foreign = store.normalize_state(payload)
        assert foreign == ["SomeThirdPartyNode"]
        assert normalized["nodes"][0]["widgets"] == {"anything_goes": [1, "two", {"three": 3}]}

    def test_no_comfyui_nodes_module_at_all_treats_every_class_as_foreign(self) -> None:
        """No `fake_nodes` fixture here -- outside ComfyUI (the normal test
        environment), `import nodes` itself fails, so every class reads as
        unknown/foreign. This is the module's own importability-without-
        ComfyUI seam, exercised end to end."""
        payload = {"name": "x", "nodes": [{"class": "AnyNode", "id": "1", "widgets": {}}]}
        normalized, foreign = store.normalize_state(payload)
        assert foreign == ["AnyNode"]
        assert normalized["nodes"][0]["class"] == "AnyNode"

    def test_multiple_entries_of_the_same_foreign_class_are_deduplicated(self, fake_nodes) -> None:
        payload = {
            "name": "x",
            "nodes": [
                {"class": "SomeThirdPartyNode", "id": "1", "widgets": {}},
                {"class": "SomeThirdPartyNode", "id": "2", "widgets": {}},
                {"class": "AnotherOne", "id": "3", "widgets": {}},
            ],
        }
        _normalized, foreign = store.normalize_state(payload)
        assert foreign == ["SomeThirdPartyNode", "AnotherOne"]

    def test_a_class_whose_attribute_access_raises_is_treated_as_foreign(self, fake_nodes) -> None:
        class _Broken:
            @property
            def EPS_STATE_WIDGETS(self):
                raise RuntimeError("boom")

        # Access on the metaclass path: emulate a class-level property that
        # raises when read, the same shape test_routes_list_flags.py's
        # `_Raises`/`_RaisesOnType` classes exercise.
        class _RaisesOnType(type):
            def __getattribute__(cls, name):
                if name == "EPS_STATE_WIDGETS":
                    raise RuntimeError("boom")
                return super().__getattribute__(name)

        class _BrokenClass(metaclass=_RaisesOnType):
            pass

        _register(fake_nodes, "Broken", _BrokenClass)
        payload = {"name": "x", "nodes": [{"class": "Broken", "id": "1", "widgets": {}}]}
        normalized, foreign = store.normalize_state(payload)
        assert foreign == ["Broken"]
        assert normalized["nodes"][0]["class"] == "Broken"

    def test_save_persists_foreign_entries_and_echoes_the_foreign_list(
        self, context: LibraryContext
    ) -> None:
        payload = {
            "name": "Mixed",
            "nodes": [{"class": "ThirdParty", "id": "1", "widgets": {"anything": 1}}],
        }
        slug, _normalized, foreign = store.save_state(context, payload)
        assert foreign == ["ThirdParty"]
        reloaded = store.load_state(context, slug)
        assert reloaded["nodes"][0]["class"] == "ThirdParty"
        assert reloaded["nodes"][0]["widgets"] == {"anything": 1}


# -------------------------------------------------------------- persistence


class TestRoundTrip:
    def test_save_then_load_preserves_nodes_and_fields(self, context: LibraryContext) -> None:
        payload = {
            "name": "Portrait pass",
            "notes": "some notes",
            "captured": "2026-08-26T12:00:00Z",
            "nodes": [
                {"class": "Foo", "id": "3:2", "title": "Hero", "widgets": {"x": 1}},
            ],
        }
        slug, saved, _foreign = store.save_state(context, payload)
        assert slug == "portrait-pass"
        loaded = store.load_state(context, slug)
        assert loaded == saved
        assert loaded["nodes"][0]["id"] == "3:2"
        assert loaded["captured"] == "2026-08-26T12:00:00Z"

    def test_saved_file_is_utf8_json_under_states_dir(
        self, context: LibraryContext, library_dir: Path
    ) -> None:
        slug, _, _ = store.save_state(context, {"name": "Foo", "nodes": []})
        path = library_dir / "states" / f"{slug}.json"
        assert path.exists()
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk["format"] == 1

    def test_states_dir_is_a_sibling_of_sets_dir(
        self, context: LibraryContext, library_dir: Path
    ) -> None:
        store.save_state(context, {"name": "Foo", "nodes": []})
        assert (library_dir / "states").is_dir()
        assert store.states_dir(context).parent == library_dir

    def test_load_missing_slug_returns_none(self, context: LibraryContext) -> None:
        assert store.load_state(context, "does-not-exist") is None

    def test_delete_state_true_then_false(self, context: LibraryContext) -> None:
        slug, _, _ = store.save_state(context, {"name": "Temp", "nodes": []})
        assert store.delete_state(context, slug) is True
        assert store.load_state(context, slug) is None
        assert store.delete_state(context, slug) is False


class TestListStates:
    def test_sorted_by_name_with_row_counts_and_captured(self, context: LibraryContext) -> None:
        store.save_state(
            context, {"name": "Zebra", "nodes": [{"class": "A", "id": "1", "widgets": {}}]}
        )
        store.save_state(context, {"name": "Apple", "nodes": []})
        listed = store.list_states(context)
        assert [entry["name"] for entry in listed] == ["Apple", "Zebra"]
        zebra = next(entry for entry in listed if entry["name"] == "Zebra")
        assert zebra["count"] == 1
        assert "captured" in zebra

    def test_empty_library_has_no_states(self, context: LibraryContext) -> None:
        assert store.list_states(context) == []

    def test_skips_unreadable_file_and_logs_a_warning(
        self, context: LibraryContext, library_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        bad = library_dir / "states" / "broken.json"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("{not json", encoding="utf-8")
        store.save_state(context, {"name": "Good", "nodes": []})

        with caplog.at_level(logging.WARNING, logger="lora_library"):
            listed = store.list_states(context)

        assert [entry["name"] for entry in listed] == ["Good"]
        assert any("broken" in record.message for record in caplog.records)

    def test_skips_file_whose_stem_is_not_a_valid_slug(
        self, context: LibraryContext, library_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        bad = library_dir / "states" / "My State.json"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text(json.dumps({"format": 1, "name": "My State", "nodes": []}), encoding="utf-8")
        store.save_state(context, {"name": "Good", "nodes": []})

        with caplog.at_level(logging.WARNING, logger="lora_library"):
            listed = store.list_states(context)

        assert [entry["slug"] for entry in listed] == ["good"]
        assert any("rename" in record.message for record in caplog.records)


# -------------------------------------------------- unreachable library dir


@pytest.fixture
def unreachable_context(context: LibraryContext, tmp_path: Path) -> LibraryContext:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    context.save_config({"library_dir": str(blocker / "library")})
    return context


class TestUnreachableLibraryFolder:
    def test_list_states_degrades_to_empty_with_a_warning(
        self, unreachable_context: LibraryContext, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="lora_library"):
            assert store.list_states(unreachable_context) == []
        assert any("unreachable" in r.message for r in caplog.records)

    def test_save_state_raises_state_validation_error_naming_the_folder(
        self, unreachable_context: LibraryContext
    ) -> None:
        with pytest.raises(store.StateValidationError, match="library folder"):
            store.save_state(unreachable_context, {"name": "Foo", "nodes": []})

    def test_delete_state_raises_rather_than_reporting_nothing_to_delete(
        self, unreachable_context: LibraryContext
    ) -> None:
        with pytest.raises(store.StateValidationError, match="library folder"):
            store.delete_state(unreachable_context, "foo")

    def test_load_state_raises_state_validation_error(
        self, unreachable_context: LibraryContext
    ) -> None:
        with pytest.raises(store.StateValidationError, match="library folder"):
            store.load_state(unreachable_context, "foo")

    def test_save_state_still_validates_payload_first(
        self, unreachable_context: LibraryContext
    ) -> None:
        with pytest.raises(store.StateValidationError, match="name"):
            store.save_state(unreachable_context, {"nodes": "nope"})


# --------------------------------------------------------------- layout §4.2


class TestLayout:
    def _mk(self, context: LibraryContext, name: str) -> str:
        slug, _, _ = store.save_state(context, {"name": name, "nodes": []})
        return slug

    def test_missing_file_heals_to_every_state_uncategorized_name_sorted(
        self, context: LibraryContext
    ) -> None:
        b = self._mk(context, "bravo")
        a = self._mk(context, "alpha")
        layout = store.load_layout(context)
        assert layout == {"categories": [], "order": {"": [a, b]}}

    def test_save_drops_unknown_slugs_and_appends_missing_states(
        self, context: LibraryContext
    ) -> None:
        a = self._mk(context, "alpha")
        b = self._mk(context, "bravo")
        c = self._mk(context, "charlie")
        saved = store.save_layout(
            context,
            {"categories": ["Portraits"], "order": {"Portraits": [b, "ghost-slug", b], "": [a]}},
        )
        assert saved == {"categories": ["Portraits"], "order": {"": [a, c], "Portraits": [b]}}
        assert store.load_layout(context) == saved

    def test_malformed_file_degrades_to_rebuilt_layout(self, context: LibraryContext) -> None:
        a = self._mk(context, "alpha")
        store.layout_path(context).write_text("{not json", encoding="utf-8")
        assert store.load_layout(context) == {"categories": [], "order": {"": [a]}}

    def test_layout_file_lives_outside_states_dir(self, context: LibraryContext) -> None:
        assert store.layout_path(context).parent != store.states_dir(context)

    def test_layout_file_name_is_the_documented_sidecar(self, context: LibraryContext) -> None:
        assert store.layout_path(context).name == "universal_states_layout.json"


# ------------------------------------ warm-cache splice (mirrors sets_store)


def _scan_counter(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    original = store._scan_states

    def counting(context: LibraryContext, states_dir_path: Path) -> list[dict]:
        calls.append(1)
        return original(context, states_dir_path)

    monkeypatch.setattr(store, "_scan_states", counting)
    return calls


def _parse_counter(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    original = store.normalize_state

    def counting(raw: object):
        calls.append(1)
        return original(raw)

    monkeypatch.setattr(store, "normalize_state", counting)
    return calls


class TestSaveDeleteWarmCacheSplice:
    def test_save_after_a_warm_listing_matches_a_forced_fresh_scan(
        self, context: LibraryContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store.save_state(context, {"name": "Alpha", "nodes": []})
        store.save_state(context, {"name": "Zebra", "nodes": []})
        store.list_states(context)
        calls = _scan_counter(monkeypatch)

        store.save_state(
            context, {"name": "Mango", "nodes": [{"class": "A", "id": "1", "widgets": {}}]}
        )
        spliced = store.list_states(context)

        assert calls == []
        store.clear_caches()
        forced = store.list_states(context)
        assert spliced == forced
        assert [e["name"] for e in spliced] == ["Alpha", "Mango", "Zebra"]

    def test_delete_after_a_warm_listing_matches_a_forced_fresh_scan(
        self, context: LibraryContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slug_a, _, _ = store.save_state(context, {"name": "Alpha", "nodes": []})
        store.save_state(context, {"name": "Bravo", "nodes": []})
        store.list_states(context)
        calls = _scan_counter(monkeypatch)

        assert store.delete_state(context, slug_a) is True
        spliced = store.list_states(context)

        assert calls == []
        store.clear_caches()
        forced = store.list_states(context)
        assert spliced == forced
        assert [e["name"] for e in spliced] == ["Bravo"]

    def test_no_warm_listing_yet_falls_back_to_one_normal_scan(
        self, context: LibraryContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _scan_counter(monkeypatch)
        store.save_state(context, {"name": "Solo", "nodes": []})
        listed = store.list_states(context)
        assert [e["name"] for e in listed] == ["Solo"]
        assert len(calls) == 1


class TestLoadStateCache:
    def test_unchanged_file_parses_once_across_repeated_loads(
        self, context: LibraryContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slug, _, _ = store.save_state(
            context, {"name": "Foo", "nodes": [{"class": "A", "id": "1", "widgets": {}}]}
        )
        store.load_state(context, slug)
        parses = _parse_counter(monkeypatch)

        first = store.load_state(context, slug)
        second = store.load_state(context, slug)
        assert parses == []
        assert first == second

    def test_stat_still_runs_on_every_call_even_when_warm(
        self, context: LibraryContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slug, _, _ = store.save_state(context, {"name": "Foo", "nodes": []})
        store.load_state(context, slug)
        calls: list[int] = []
        original = Path.stat

        def counting(self: Path, *args, **kwargs):
            calls.append(1)
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", counting)
        store.load_state(context, slug)
        store.load_state(context, slug)
        assert len(calls) >= 2

    def test_save_state_warms_the_load_cache_so_the_next_load_is_free(
        self, context: LibraryContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slug, _, _ = store.save_state(context, {"name": "Foo", "nodes": []})
        store.load_state(context, slug)
        parses = _parse_counter(monkeypatch)

        store.save_state(
            context,
            {"name": "Foo", "nodes": [{"class": "A", "id": "1", "widgets": {}}]},
            slug=slug,
        )
        assert len(parses) == 1  # save_state's own normalize_state call
        reloaded = store.load_state(context, slug)
        assert reloaded["nodes"][0]["class"] == "A"
        assert len(parses) == 1  # the follow-up load added none

    def test_returned_dict_is_independent_across_calls(self, context: LibraryContext) -> None:
        slug, _, _ = store.save_state(
            context, {"name": "Foo", "nodes": [{"class": "A", "id": "1", "widgets": {"x": 1}}]}
        )
        first = store.load_state(context, slug)
        first["nodes"][0]["widgets"]["x"] = 999
        first["name"] = "MUTATED"

        second = store.load_state(context, slug)
        assert second["name"] == "Foo"
        assert second["nodes"][0]["widgets"]["x"] == 1


# ---------------------------------------------------- gvfs/FUSE EEXIST fallback


class TestGvfsReplaceFallback:
    """v0.80.1's gvfs/FUSE rename-over-existing fallback (test_nas_io_round.py's
    ``TestGvfsReplaceFallback`` shape), exercised through THIS store's own
    ``save_state`` rather than the shared ``context._atomic_write_text``
    directly -- proving the fallback keeps working when reached through the
    new module, not just re-testing ``context.py`` itself."""

    def test_save_state_survives_eexist_on_replace(
        self, context: LibraryContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from lora_library import context as ctx

        slug, _, _ = store.save_state(context, {"name": "Foo", "nodes": []})
        path = store.state_path(context, slug)
        assert path.exists()

        original = ctx.os.replace
        calls = {"n": 0}

        def flaky_replace(src, dst):
            calls["n"] += 1
            if calls["n"] == 1 and Path(dst).exists():
                raise FileExistsError(17, "File exists", str(src), None, str(dst))
            return original(src, dst)

        monkeypatch.setattr(ctx.os, "replace", flaky_replace)

        store.save_state(
            context,
            {"name": "Foo", "nodes": [{"class": "A", "id": "1", "widgets": {}}]},
            slug=slug,
        )
        assert calls["n"] == 2  # failed once, fell back to unlink+replace
        reloaded = store.load_state(context, slug)
        assert reloaded["nodes"][0]["class"] == "A"
        assert not list(path.parent.glob("*.tmp"))  # no temp litter


# --------------------------------------------------------- import hygiene


def test_universal_states_store_never_imports_comfy_at_module_scope() -> None:
    """No torch/comfy/ComfyUI-``nodes`` import at MODULE scope -- the
    module stays importable in a plain test environment, exactly the
    seam ``sets_store.py``/``context.py`` already keep. The registry
    lookup's ``import nodes`` lives INSIDE a function (see
    :func:`store._state_registry`), so it never binds a module-level
    name even though the source text does contain that import."""
    assert "torch" not in vars(store)
    assert "comfy" not in vars(store)
    assert "nodes" not in vars(store)
