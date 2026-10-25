"""Tests for eps_image.nodes_number_controller (FORMAT.md section 6.17,
`EPSNumberController`).

No ComfyUI/torch anywhere -- this node never imports either, at module
scope or lazily (unlike EPSDistributor, it never needs `ExecutionBlocker`:
nothing here is gated, every slot always carries a plain Python number).
"""

from __future__ import annotations

import inspect
import json
import logging
import math
import sys
import types

import pytest

from eps_image import nodes_number_controller
from eps_image.nodes_number_controller import MAX_OUTPUTS, EPSNumberController
from lora_library import universal_states_store as store


def _values(**rows: dict) -> str:
    """A `values` JSON string, e.g. `_values(num_1={"name": ..., "value": 30, "type": "INT"})`."""
    return json.dumps(rows)


def _row(name: str = "", value: object = 0, type_: str = "*") -> dict:
    return {"name": name, "value": value, "type": type_}


# ------------------------------------------------------------- coercion


class TestCoercion:
    def test_int_typed_float_value_rounds_to_int(self) -> None:
        node = EPSNumberController()
        result = node.get_numbers(values=_values(num_1=_row("steps", 30.0, "INT")))
        assert result[0] == 30
        assert isinstance(result[0], int)

    def test_int_typed_half_rounds_away_from_zero_not_to_even(self) -> None:
        """2.5 must land on 3, not on 2.

        Python's own `round()` rounds halves to EVEN, so it would answer 2
        here and 4 for 3.5 -- defensible numerically, but it reads as a bug
        to the artist who typed the number. A row legitimately holds a
        fraction while it is still unwired, so wiring it to an INT socket
        afterwards genuinely arrives here with a .5 on it.
        """
        node = EPSNumberController()
        for typed, expected in ((2.5, 3), (3.5, 4), (0.5, 1), (-2.5, -3), (-3.5, -4)):
            result = node.get_numbers(values=_values(num_1=_row("steps", typed, "INT")))
            assert result[0] == expected, f"{typed} should round to {expected}"

    def test_int_typed_half_results_are_plain_ints(self) -> None:
        node = EPSNumberController()
        result = node.get_numbers(values=_values(num_1=_row("steps", 2.5, "INT")))
        assert isinstance(result[0], int) and not isinstance(result[0], bool)

    def test_int_typed_fractional_value_rounds(self) -> None:
        node = EPSNumberController()
        result = node.get_numbers(values=_values(num_1=_row("steps", 29.6, "INT")))
        assert result[0] == 30
        assert isinstance(result[0], int)

    def test_float_typed_int_value_becomes_float(self) -> None:
        node = EPSNumberController()
        result = node.get_numbers(values=_values(num_1=_row("cfg", 7, "FLOAT")))
        assert result[0] == 7.0
        assert isinstance(result[0], float)

    def test_float_typed_fractional_value_stays_float(self) -> None:
        node = EPSNumberController()
        result = node.get_numbers(values=_values(num_1=_row("cfg", 7.5, "FLOAT")))
        assert result[0] == 7.5
        assert isinstance(result[0], float)

    def test_untyped_value_that_round_trips_becomes_int(self) -> None:
        node = EPSNumberController()
        result = node.get_numbers(values=_values(num_1=_row("n", 30, "*")))
        assert result[0] == 30
        assert isinstance(result[0], int)

    def test_untyped_whole_float_becomes_int(self) -> None:
        # A JSON number like 30.0 still round-trips exactly as an int.
        node = EPSNumberController()
        result = node.get_numbers(values=_values(num_1=_row("n", 30.0, "*")))
        assert result[0] == 30
        assert isinstance(result[0], int)

    def test_untyped_fractional_value_stays_float(self) -> None:
        node = EPSNumberController()
        result = node.get_numbers(values=_values(num_1=_row("n", 7.5, "*")))
        assert result[0] == 7.5
        assert isinstance(result[0], float)

    def test_absent_type_behaves_like_untyped(self) -> None:
        node = EPSNumberController()
        result = node.get_numbers(values=json.dumps({"num_1": {"name": "n", "value": 5}}))
        assert result[0] == 5
        assert isinstance(result[0], int)

    def test_unrecognized_type_tag_behaves_like_untyped(self) -> None:
        # A foreign/future type tag is not evidence of corrupted data --
        # module docstring: treated exactly like "*", silently.
        node = EPSNumberController()
        result = node.get_numbers(values=_values(num_1=_row("n", 5.5, "BOGUS")))
        assert result[0] == 5.5

    def test_negative_and_zero_values_coerce_correctly(self) -> None:
        node = EPSNumberController()
        result = node.get_numbers(
            values=json.dumps(
                {
                    "num_1": _row("a", -12, "INT"),
                    "num_2": _row("b", 0, "FLOAT"),
                    "num_3": _row("c", -3.25, "*"),
                }
            )
        )
        assert result[0] == -12
        assert result[1] == 0.0 and isinstance(result[1], float)
        assert result[2] == -3.25


# --------------------------------------------------------------- BOOLEAN
# Owner ask, verbatim, 2026-10-25: "For the number controller node it would be
# useful if one of the options was a boolean. A user would need to input a 0 or
# 1 but it could hook into boolean inputs that accept true or false."


def _bool_row(value: object, name: str = "gate") -> str:
    return _values(num_1=_row(name, value, "BOOLEAN"))


class TestBooleanCoercion:
    @pytest.mark.parametrize(
        ("typed", "expected"),
        [
            (0, False),
            (0.0, False),
            (-0.0, False),
            (1, True),
            (1.0, True),
            (2.5, True),  # any non-zero number is on -- the panel shows 1
            (-1, True),
            (0.4, True),  # NOT rounded first: 0.4 is non-zero
            (1e-9, True),
            (1e308, True),  # huge but representable
            (10**15, True),
        ],
    )
    def test_value_not_equal_zero_is_a_real_bool(self, typed: object, expected: bool) -> None:
        result = EPSNumberController().get_numbers(values=_bool_row(typed))
        assert result[0] is expected, f"{typed!r} should read {expected}"
        assert type(result[0]) is bool

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_reads_false_and_warns(
        self, bad: float, caplog: pytest.LogCaptureFixture
    ) -> None:
        # "garbage reads as off": a damaged toggle must never switch a feature on.
        with caplog.at_level(logging.WARNING, logger="eps_image"):
            result = EPSNumberController().get_numbers(values=_bool_row(bad))
        assert result[0] is False
        assert any(
            r.levelno == logging.WARNING and "not finite" in r.message for r in caplog.records
        )

    def test_integer_too_large_for_a_float_reads_false_and_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Same unrepresentable-number degrade INT/FLOAT already have (-> 0), which for a
        # BOOLEAN row is False. 1e308 above is representable and reads True.
        with caplog.at_level(logging.WARNING, logger="eps_image"):
            result = EPSNumberController().get_numbers(values=_bool_row(10**400))
        assert result[0] is False
        assert any("too large" in r.message for r in caplog.records)

    @pytest.mark.parametrize("junk", ["1", "true", None, True, False, [1], {"a": 1}])
    def test_non_numeric_value_reads_false_and_warns(
        self, junk: object, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="eps_image"):
            result = EPSNumberController().get_numbers(values=_bool_row(junk))
        assert result[0] is False and type(result[0]) is bool
        assert any(r.levelno == logging.WARNING for r in caplog.records)

    def test_missing_value_reads_false_and_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        values = json.dumps({"num_1": {"name": "gate", "type": "BOOLEAN"}})
        with caplog.at_level(logging.WARNING, logger="eps_image"):
            result = EPSNumberController().get_numbers(values=values)
        assert result[0] is False
        assert caplog.records

    def test_an_entry_that_is_not_an_object_has_no_readable_type_and_reads_zero(self) -> None:
        # Nothing to say it was a BOOLEAN row: the long-standing degrade value stays 0.
        result = EPSNumberController().get_numbers(values=json.dumps({"num_1": "garbage"}))
        assert result[0] == 0 and type(result[0]) is int

    def test_round_trip_from_the_shape_the_panel_writes(self) -> None:
        """The exact JSON the frontend stores for a mixed node -- INT, FLOAT and
        BOOLEAN rows side by side, each coerced to its own Python type with
        neighbours untouched, and the BOOLEAN row's extra keys (enabled/links)
        ignored like any other row's."""
        stored = json.dumps(
            {
                "num_1": {"name": "steps", "value": 30, "type": "INT"},
                "num_2": {"name": "cfg", "value": 7.5, "type": "FLOAT"},
                "num_3": {"name": "use_upscale", "value": 1, "type": "BOOLEAN"},
                "num_4": {
                    "name": "use_mask",
                    "value": 0,
                    "type": "BOOLEAN",
                    "enabled": False,
                    "links": [{"node": 12, "input": "enabled"}],
                },
                "num_5": {"name": "adopted_from_a_fraction", "value": 2.5, "type": "BOOLEAN"},
            }
        )
        result = EPSNumberController().get_numbers(values=stored)
        assert (result[0], type(result[0])) == (30, int)
        assert (result[1], type(result[1])) == (7.5, float)
        assert result[2] is True
        assert result[3] is False
        assert result[4] is True
        assert result[5:] == (0,) * (MAX_OUTPUTS - 5)
        assert len(result) == MAX_OUTPUTS

    def test_one_broken_boolean_row_never_poisons_its_neighbours(self) -> None:
        values = json.dumps(
            {
                "num_1": _row("a", float("nan"), "BOOLEAN"),
                "num_2": _row("b", 1, "BOOLEAN"),
                "num_3": _row("c", "x", "BOOLEAN"),
                "num_4": _row("d", 3, "INT"),
            }
        )
        result = EPSNumberController().get_numbers(values=values)
        assert result[:4] == (False, True, False, 3)

    def test_untyped_one_is_still_an_int_never_a_bool(self) -> None:
        # BOOLEAN must be opt-in via the tag: an unwired/INT row holding 1 is unchanged.
        for tag in ("*", "INT", None):
            entry = {"name": "n", "value": 1}
            if tag is not None:
                entry["type"] = tag
            result = EPSNumberController().get_numbers(values=json.dumps({"num_1": entry}))
            assert result[0] == 1 and type(result[0]) is int, tag

    def test_the_tag_is_case_sensitive_like_int_and_float(self) -> None:
        # Exactly the literal "BOOLEAN" -- lowercase is an unrecognised tag, round-trips.
        result = EPSNumberController().get_numbers(values=_values(num_1=_row("g", 1, "boolean")))
        assert result[0] == 1 and type(result[0]) is int

    def test_return_types_and_count_are_unchanged(self) -> None:
        # No new output type and no new widget: BOOLEAN lives in the values JSON.
        assert EPSNumberController.RETURN_TYPES == ("*",) * MAX_OUTPUTS
        assert set(EPSNumberController.INPUT_TYPES()["required"]) == {"values"}
        assert len(EPSNumberController().get_numbers(values=_bool_row(1))) == MAX_OUTPUTS


class TestOlderBuildsReadingABooleanRow:
    """The compatibility note (FORMAT.md section 6.17): a workflow saved with a
    BOOLEAN row and opened on an OLDER build, whose backend only knows INT/FLOAT.

    Such a build falls into the documented unrecognised-tag path, so the row is
    treated as untyped and round-trips its number. This build exercises that
    same path with a tag it does not know, because BOOLEAN is now known."""

    @pytest.mark.parametrize(
        ("typed", "arrives_as", "arrives_type"),
        [
            (1, 1, int),
            (0, 0, int),
            (1.0, 1, int),  # a whole float round-trips as an int
            (0.0, 0, int),
            (2.5, 2.5, float),  # a fraction the panel only DISPLAYS as 1
            (-1, -1, int),
        ],
    )
    def test_an_unknown_tag_round_trips_the_number(
        self, typed: float, arrives_as: float, arrives_type: type
    ) -> None:
        result = EPSNumberController().get_numbers(
            values=_values(num_1=_row("gate", typed, "A_TAG_FROM_THE_FUTURE"))
        )
        assert result[0] == arrives_as
        assert type(result[0]) is arrives_type

    def test_so_an_older_build_sends_1_and_0_not_true_and_false(self) -> None:
        """Documented, not a bug: truthiness-testing consumers behave identically;
        a consumer that tests `is True` would not. This build is the one that
        sends real bools."""
        old_like = EPSNumberController().get_numbers(values=_values(num_1=_row("g", 1, "TOGGLE")))
        new = EPSNumberController().get_numbers(values=_values(num_1=_row("g", 1, "BOOLEAN")))
        assert old_like[0] is not True and bool(old_like[0]) is True
        assert new[0] is True


@pytest.fixture
def number_controller_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real EPSNumberController in a fake ComfyUI ``nodes`` registry --
    tests/test_universal_states_store.py's technique."""
    module = types.ModuleType("nodes")
    module.NODE_CLASS_MAPPINGS = {"EPSNumberController": EPSNumberController}
    monkeypatch.setitem(sys.modules, "nodes", module)


class TestUniversalStateAcceptsBooleanRows:
    """The Universal State `values` kind is `json_object` with a key pattern. Nothing
    in either validator (Python `_check_kind`, JS `validateStateValue`) looks INSIDE a
    row, so a BOOLEAN row needs no registry change -- pinned here so that stays true."""

    @staticmethod
    def _state(values: dict) -> dict:
        return {
            "name": "toggles",
            "nodes": [
                {
                    "class": "EPSNumberController",
                    "id": "7",
                    "title": "Numbers",
                    "widgets": {"values": values},
                }
            ],
        }

    def test_a_boolean_row_is_accepted_and_round_trips_untouched(
        self, number_controller_registered: None
    ) -> None:
        values = {
            "num_1": {"name": "steps", "value": 30, "type": "INT"},
            "num_2": {"name": "use_upscale", "value": 1, "type": "BOOLEAN"},
            "num_3": {
                "name": "use_mask",
                "value": 0,
                "type": "BOOLEAN",
                "enabled": False,
                "links": [{"node": 12, "input": "enabled"}],
            },
        }
        normalized, foreign = store.normalize_state(self._state(values))
        assert foreign == []
        assert normalized["nodes"][0]["widgets"]["values"] == values

    def test_the_inner_type_tag_is_not_validated(self, number_controller_registered: None) -> None:
        # A tag from a future build must survive a round trip through this one.
        values = {"num_1": {"name": "x", "value": 1, "type": "SOMETHING_NEWER"}}
        normalized, _ = store.normalize_state(self._state(values))
        assert normalized["nodes"][0]["widgets"]["values"] == values

    def test_row_keys_are_still_pattern_checked(self, number_controller_registered: None) -> None:
        bad = {"bool_1": {"name": "x", "value": 1, "type": "BOOLEAN"}}
        with pytest.raises(store.StateValidationError, match="key_pattern"):
            store.normalize_state(self._state(bad))


# --------------------------------------------------------------- degrade


class TestDegradePaths:
    def test_bad_json_degrades_every_slot_to_zero(self) -> None:
        node = EPSNumberController()
        result = node.get_numbers(values="not json{{")
        assert result == (0,) * MAX_OUTPUTS

    def test_bad_json_logs_a_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        node = EPSNumberController()
        with caplog.at_level(logging.WARNING, logger="eps_image"):
            node.get_numbers(values="not json{{")
        assert any("malformed" in record.message.lower() for record in caplog.records)

    def test_non_object_json_degrades_every_slot_to_zero(self) -> None:
        node = EPSNumberController()
        result = node.get_numbers(values="[1, 2, 3]")
        assert result == (0,) * MAX_OUTPUTS

    @pytest.mark.parametrize("blank", ["", None])
    def test_blank_values_is_all_zero_without_warning(
        self, blank: object, caplog: pytest.LogCaptureFixture
    ) -> None:
        # A fresh node's own default -- not a malformed value.
        node = EPSNumberController()
        with caplog.at_level(logging.WARNING, logger="eps_image"):
            result = node.get_numbers(values=blank)  # type: ignore[arg-type]
        assert result == (0,) * MAX_OUTPUTS
        assert not caplog.records

    def test_missing_key_reads_as_zero(self) -> None:
        # num_2..num_16 are never mentioned -- the ordinary steady state for
        # every row past however many the user has created.
        node = EPSNumberController()
        result = node.get_numbers(values=_values(num_1=_row("only", 5, "INT")))
        assert result[0] == 5
        assert result[1:] == (0,) * (MAX_OUTPUTS - 1)

    def test_missing_keys_do_not_warn(self, caplog: pytest.LogCaptureFixture) -> None:
        # Regression pin: warning on every one of the ~15 unused rows on
        # every single queue would be pure log noise for the single most
        # common case this node has.
        node = EPSNumberController()
        with caplog.at_level(logging.WARNING, logger="eps_image"):
            node.get_numbers(values=_values(num_1=_row("only", 5, "INT")))
        assert not caplog.records

    def test_wrong_typed_entry_reads_as_zero_and_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # The entry itself is a bare number, not a {name, value, type} object.
        node = EPSNumberController()
        with caplog.at_level(logging.WARNING, logger="eps_image"):
            result = node.get_numbers(values=json.dumps({"num_3": 42}))
        assert result[2] == 0
        assert any(record.levelno == logging.WARNING for record in caplog.records)

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_nan_and_inf_read_as_zero_and_warn(
        self, bad: float, caplog: pytest.LogCaptureFixture
    ) -> None:
        node = EPSNumberController()
        values = json.dumps({"num_1": {"name": "n", "value": bad, "type": "FLOAT"}})
        with caplog.at_level(logging.WARNING, logger="eps_image"):
            result = node.get_numbers(values=values)
        assert result[0] == 0
        assert any(record.levelno == logging.WARNING for record in caplog.records)

    def test_json_nan_literal_is_caught_too(self, caplog: pytest.LogCaptureFixture) -> None:
        # json.loads accepts the bare (non-standard) NaN/Infinity literals
        # by default -- exactly how a NaN could arrive over the wire.
        node = EPSNumberController()
        with caplog.at_level(logging.WARNING, logger="eps_image"):
            result = node.get_numbers(values='{"num_1": {"name": "n", "value": NaN}}')
        assert result[0] == 0
        assert any(record.levelno == logging.WARNING for record in caplog.records)

    def test_string_number_reads_as_zero_and_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        node = EPSNumberController()
        values = json.dumps({"num_1": {"name": "n", "value": "30", "type": "INT"}})
        with caplog.at_level(logging.WARNING, logger="eps_image"):
            result = node.get_numbers(values=values)
        assert result[0] == 0
        assert any(record.levelno == logging.WARNING for record in caplog.records)

    def test_null_value_reads_as_zero_and_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        node = EPSNumberController()
        values = json.dumps({"num_1": {"name": "n", "value": None, "type": "INT"}})
        with caplog.at_level(logging.WARNING, logger="eps_image"):
            result = node.get_numbers(values=values)
        assert result[0] == 0
        assert any(record.levelno == logging.WARNING for record in caplog.records)

    def test_boolean_value_reads_as_zero_and_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # bool is an int subclass in Python -- must not silently become 1/0.
        node = EPSNumberController()
        values = json.dumps({"num_1": {"name": "n", "value": True, "type": "*"}})
        with caplog.at_level(logging.WARNING, logger="eps_image"):
            result = node.get_numbers(values=values)
        assert result[0] == 0
        assert any(record.levelno == logging.WARNING for record in caplog.records)

    def test_oversized_value_reads_as_zero_and_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # A JSON integer too large for a Python float to represent.
        node = EPSNumberController()
        values = json.dumps({"num_1": {"name": "n", "value": 10**400, "type": "FLOAT"}})
        with caplog.at_level(logging.WARNING, logger="eps_image"):
            result = node.get_numbers(values=values)
        assert result[0] == 0
        assert any(record.levelno == logging.WARNING for record in caplog.records)


# ---------------------------------------------------- one bad slot isolation


class TestOneBadSlotNeverPoisonsNeighbours:
    def test_a_broken_row_leaves_the_others_correct(self) -> None:
        node = EPSNumberController()
        values = json.dumps(
            {
                "num_1": _row("steps", 30, "INT"),
                "num_2": "not an object",
                "num_3": {"name": "cfg", "value": float("nan"), "type": "FLOAT"},
                "num_4": _row("count", 7.5, "*"),
            }
        )
        result = node.get_numbers(values=values)
        assert result[0] == 30 and isinstance(result[0], int)
        assert result[1] == 0
        assert result[2] == 0
        assert result[3] == 7.5
        assert result[4:] == (0,) * (MAX_OUTPUTS - 4)

    def test_every_row_broken_still_returns_full_length_of_zeros(self) -> None:
        node = EPSNumberController()
        values = json.dumps({f"num_{n}": "garbage" for n in range(1, MAX_OUTPUTS + 1)})
        result = node.get_numbers(values=values)
        assert result == (0,) * MAX_OUTPUTS


# ------------------------------------------------------- returned tuple length


class TestReturnedTupleIsAlwaysMaxOutputsLong:
    @pytest.mark.parametrize(
        "values",
        [
            "{}",
            "",
            None,
            "not json{{",
            "[1, 2, 3]",
            json.dumps({"num_1": _row("a", 1, "INT")}),
            json.dumps({f"num_{n}": _row(str(n), n, "INT") for n in range(1, MAX_OUTPUTS + 1)}),
        ],
    )
    def test_length_is_always_max_outputs(self, values: object) -> None:
        result = EPSNumberController().get_numbers(values=values)  # type: ignore[arg-type]
        assert len(result) == MAX_OUTPUTS == 16

    def test_values_omitted_entirely_uses_the_default(self) -> None:
        node = EPSNumberController()
        result = node.get_numbers()
        assert result == (0,) * MAX_OUTPUTS


# ------------------------------------------------------------- class shape


class TestClassShape:
    def test_category(self) -> None:
        assert EPSNumberController.CATEGORY == "EPSNodes/Controllers"

    def test_return_types_length_and_values(self) -> None:
        assert len(EPSNumberController.RETURN_TYPES) == MAX_OUTPUTS == 16
        assert EPSNumberController.RETURN_TYPES == ("*",) * MAX_OUTPUTS

    def test_return_names_length_and_values(self) -> None:
        assert len(EPSNumberController.RETURN_NAMES) == MAX_OUTPUTS == 16
        expected = tuple(f"num_{n}" for n in range(1, MAX_OUTPUTS + 1))
        assert expected == EPSNumberController.RETURN_NAMES
        assert EPSNumberController.RETURN_NAMES[0] == "num_1"
        assert EPSNumberController.RETURN_NAMES[-1] == "num_16"

    def test_function_name_matches_the_declared_entry_point(self) -> None:
        assert EPSNumberController.FUNCTION == "get_numbers"
        assert callable(getattr(EPSNumberController(), EPSNumberController.FUNCTION))

    def test_no_output_is_list_attribute(self) -> None:
        assert not hasattr(EPSNumberController, "OUTPUT_IS_LIST")

    def test_no_input_is_list_attribute(self) -> None:
        assert not hasattr(EPSNumberController, "INPUT_IS_LIST")

    def test_no_is_changed_attribute(self) -> None:
        assert not hasattr(EPSNumberController, "IS_CHANGED")

    def test_input_types_shape(self) -> None:
        spec = EPSNumberController.INPUT_TYPES()
        assert set(spec["required"]) == {"values"}
        assert spec.get("optional", {}) == {}
        widget_type, options = spec["required"]["values"]
        assert widget_type == "STRING"
        assert options["default"] == "{}"
        assert options["multiline"] is False
        assert options["hidden"] is True

    def test_values_widget_is_required_not_optional(self) -> None:
        # Frozen contract: unlike EPSDistributor's `toggles`, `values` is
        # this node's entire reason for being, so it lives in `required`
        # (mirrors nodes_notebook.py's own required `file`/`entry`).
        spec = EPSNumberController.INPUT_TYPES()
        assert "values" in spec["required"]
        assert "values" not in spec.get("optional", {})


# --------------------------------------------------------- state registry


class TestStateRegistryDeclaration:
    def test_declares_exactly_the_values_widget(self) -> None:
        descriptor = EPSNumberController.EPS_STATE_WIDGETS
        assert descriptor["format"] == 1
        assert set(descriptor["widgets"]) == {"values"}
        assert "excluded" not in descriptor or descriptor["excluded"] == {}

    def test_values_widget_kind_and_pattern(self) -> None:
        widget_spec = EPSNumberController.EPS_STATE_WIDGETS["widgets"]["values"]
        assert widget_spec["kind"] == "json_object"
        assert widget_spec["key_pattern"] == r"^num_\d+$"

    def test_declared_widget_matches_the_widget_that_actually_exists(self) -> None:
        # The registry's own completeness rule (tests/test_state_registry.py
        # exercises this generically for every node this pack ships): every
        # name EPS_STATE_WIDGETS declares must be a real INPUT_TYPES entry.
        declared = set(EPSNumberController.EPS_STATE_WIDGETS["widgets"])
        spec = EPSNumberController.INPUT_TYPES()
        all_names = set(spec.get("required", {})) | set(spec.get("optional", {}))
        assert declared <= all_names

    def test_key_pattern_matches_every_real_slot_name(self) -> None:
        import re

        widget_spec = EPSNumberController.EPS_STATE_WIDGETS["widgets"]["values"]
        pattern = re.compile(widget_spec["key_pattern"])
        for n in range(1, MAX_OUTPUTS + 1):
            assert pattern.match(f"num_{n}")


# ------------------------------------------------------------- name is inert


class TestNameFieldIsDisplayOnly:
    def test_name_never_affects_the_returned_number(self) -> None:
        node = EPSNumberController()
        for name in ("", "steps", "a very long custom row title", None, 42, ["x"]):
            values = json.dumps({"num_1": {"name": name, "value": 9, "type": "INT"}})
            result = node.get_numbers(values=values)
            assert result[0] == 9

    def test_missing_name_key_entirely_is_fine(self) -> None:
        node = EPSNumberController()
        result = node.get_numbers(values=json.dumps({"num_1": {"value": 9, "type": "INT"}}))
        assert result[0] == 9


# --------------------------------------------------------- no ComfyUI import


def test_module_never_imports_comfy_or_torch() -> None:
    assert "comfy" not in nodes_number_controller.__dict__
    assert "torch" not in nodes_number_controller.__dict__
    source = inspect.getsource(sys.modules[nodes_number_controller.__name__])
    assert "import comfy" not in source
    assert "import torch" not in source


def test_module_never_imports_math_lazily_only() -> None:
    # Unlike EPSDistributor's ExecutionBlocker, `math` is a stdlib module --
    # a plain top-level import is fine and expected (NaN/inf detection is
    # needed on every call, not gated behind a rare branch).
    assert "math" in nodes_number_controller.__dict__
    assert math.isnan(nodes_number_controller.math.nan)
