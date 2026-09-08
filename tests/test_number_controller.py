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

import pytest

from eps_image import nodes_number_controller
from eps_image.nodes_number_controller import MAX_OUTPUTS, EPSNumberController


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
