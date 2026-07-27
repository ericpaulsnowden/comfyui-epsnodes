"""Tests for eps_image.nodes_distributor (FORMAT.md section 6.11, `EPSDistributor`).

No ComfyUI/torch anywhere -- "images" are plain `object()` sentinels; the
node treats them opaquely and never inspects their contents, so identity
(`is`) is what every enabled-slot assertion here checks. Mirrors
tests/test_switcher.py's conventions for its structural mirror-image
sibling node: the `fake_execution_blocker` fixture, the `_toggles` JSON
helper, and the no-comfy-import proof are all adapted from there.
"""

from __future__ import annotations

import inspect
import json
import logging
import sys
import types

import pytest

from eps_image import nodes_distributor
from eps_image.nodes_distributor import MAX_OUTPUTS, EPSDistributor


def _toggles(**overrides: bool) -> str:
    """A `toggles` JSON string, e.g. `_toggles(out_2=False)`."""
    return json.dumps(overrides)


@pytest.fixture
def fake_execution_blocker(monkeypatch: pytest.MonkeyPatch):
    """Installs a fake `comfy_execution.graph` module exposing
    `ExecutionBlocker` into `sys.modules` -- the same convention as
    `tests/test_switcher.py`'s fixture of the same name (this pack's tests
    never require a real ComfyUI install on the path).
    `EPSDistributor.distribute`'s any-slot-disabled path imports
    `ExecutionBlocker` lazily from exactly this module path, so installing
    the fake here is the whole story -- nothing in nodes_distributor itself
    needs patching. Returns the fake class so tests can `isinstance()` the
    returned blockers.
    """

    class FakeExecutionBlocker:
        """Mirrors the real `comfy_execution.graph_utils.ExecutionBlocker`
        (`__init__(self, message)` storing `self.message`) exactly enough
        for these tests.
        """

        def __init__(self, message: object) -> None:
            self.message = message

    fake_graph = types.ModuleType("comfy_execution.graph")
    fake_graph.ExecutionBlocker = FakeExecutionBlocker
    fake_pkg = types.ModuleType("comfy_execution")
    fake_pkg.graph = fake_graph

    monkeypatch.setitem(sys.modules, "comfy_execution", fake_pkg)
    monkeypatch.setitem(sys.modules, "comfy_execution.graph", fake_graph)
    return FakeExecutionBlocker


# ------------------------------------------------------------- all enabled


class TestAllEnabled:
    def test_default_toggles_all_slots_are_the_image(self) -> None:
        node = EPSDistributor()
        image = object()
        result = node.distribute(image=image, toggles=_toggles())
        assert result == (image,) * MAX_OUTPUTS
        assert all(slot is image for slot in result)

    def test_toggles_omitted_entirely_is_all_enabled(self) -> None:
        # A plain API caller who never loaded any frontend JS -- module
        # docstring's default-enabled rationale.
        node = EPSDistributor()
        image = object()
        result = node.distribute(image=image)
        assert len(result) == MAX_OUTPUTS
        assert all(slot is image for slot in result)


# --------------------------------------------------------- toggle semantics


class TestToggleSemantics:
    def test_absent_key_is_enabled(self, fake_execution_blocker) -> None:
        # Only out_1 is mentioned (and disabled, hence the fixture -- the
        # implementation still needs a real/fake ExecutionBlocker for that
        # one slot); out_2..out_8 are never named and must still be enabled.
        node = EPSDistributor()
        image = object()
        result = node.distribute(image=image, toggles=_toggles(out_1=False))
        assert all(slot is image for slot in result[1:])

    def test_explicit_true_is_enabled(self) -> None:
        node = EPSDistributor()
        image = object()
        result = node.distribute(image=image, toggles=json.dumps({"out_4": True}))
        assert all(slot is image for slot in result)

    @pytest.mark.parametrize("stray_key", ["out_0", "out_9", "out_99", "nonsense", "1", ""])
    def test_keys_outside_out_1_to_out_8_are_ignored(self, stray_key: str) -> None:
        # A stale save from a future MAX, a hand-edited workflow, or an
        # unrelated key sharing the widget must never disable a real slot --
        # `distribute` only ever LOOKS UP out_1..out_MAX_OUTPUTS, so an
        # unrecognized key (even `false`) can't reach a socket.
        node = EPSDistributor()
        image = object()
        result = node.distribute(image=image, toggles=json.dumps({stray_key: False}))
        assert all(slot is image for slot in result)

    @pytest.mark.parametrize("blank", ["", None])
    def test_blank_toggles_is_all_enabled_without_warning(
        self, blank: object, caplog: pytest.LogCaptureFixture
    ) -> None:
        # `_parse_toggles`'s `if not toggles` short-circuit: an empty widget
        # value (or a caller passing None outright) is the ABSENCE of
        # overrides, not a malformed value -- all enabled, and silent, since
        # there is nothing to report.
        node = EPSDistributor()
        image = object()
        with caplog.at_level(logging.WARNING, logger="eps_image"):
            result = node.distribute(image=image, toggles=blank)  # type: ignore[arg-type]
        assert all(slot is image for slot in result)
        assert not caplog.records

    @pytest.mark.parametrize("falsy", [None, 0, "", [], {}])
    def test_non_bool_falsy_toggle_value_keeps_slot_enabled(self, falsy: object) -> None:
        # Regression pin (mirrors test_switcher.py's test of the same name):
        # only the LITERAL boolean False disables a slot. A non-bool falsy
        # value (null/0/""/[]/{}) from a hand-edited workflow or a
        # non-frontend API caller must NOT disable the slot -- plain
        # truthiness would wrongly drop it.
        node = EPSDistributor()
        image = object()
        toggles = json.dumps({"out_2": falsy})
        result = node.distribute(image=image, toggles=toggles)
        assert all(slot is image for slot in result)


# --------------------------------------------------------- slots disabled


class TestSlotsDisabled:
    def test_one_slot_off_only_that_slot_is_a_blocker(self, fake_execution_blocker) -> None:
        node = EPSDistributor()
        image = object()
        result = node.distribute(image=image, toggles=_toggles(out_3=False))
        assert len(result) == MAX_OUTPUTS
        for index, value in enumerate(result, start=1):
            if index == 3:
                assert isinstance(value, fake_execution_blocker)
                assert value.message is None
            else:
                assert value is image

    def test_explicit_false_is_the_only_disabler(self, fake_execution_blocker) -> None:
        node = EPSDistributor()
        image = object()
        result = node.distribute(image=image, toggles=json.dumps({"out_2": False}))
        assert isinstance(result[1], fake_execution_blocker)
        assert all(result[i] is image for i in range(MAX_OUTPUTS) if i != 1)

    def test_several_slots_off(self, fake_execution_blocker) -> None:
        node = EPSDistributor()
        image = object()
        result = node.distribute(
            image=image, toggles=_toggles(out_2=False, out_5=False, out_8=False)
        )
        off_indices = {2, 5, 8}
        for index, value in enumerate(result, start=1):
            if index in off_indices:
                assert isinstance(value, fake_execution_blocker)
            else:
                assert value is image

    def test_all_off_returns_8_blockers_and_does_not_raise(self, fake_execution_blocker) -> None:
        # All-off is a VALID state (mirrors EPSSwitcher's own all-off
        # decision) -- must not raise anything.
        node = EPSDistributor()
        toggles = _toggles(**{f"out_{n}": False for n in range(1, MAX_OUTPUTS + 1)})
        result = node.distribute(image=object(), toggles=toggles)
        assert len(result) == MAX_OUTPUTS
        assert all(isinstance(value, fake_execution_blocker) for value in result)
        assert all(value.message is None for value in result)


# ----------------------------------------------------------- malformed json


class TestMalformedTogglesDegradesToAllEnabled:
    def test_malformed_json_is_all_enabled(self) -> None:
        node = EPSDistributor()
        image = object()
        result = node.distribute(image=image, toggles="not json{{")
        assert all(slot is image for slot in result)

    def test_non_object_json_is_all_enabled(self) -> None:
        node = EPSDistributor()
        image = object()
        result = node.distribute(image=image, toggles="[1, 2, 3]")
        assert all(slot is image for slot in result)

    def test_malformed_json_logs_a_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        node = EPSDistributor()
        with caplog.at_level(logging.WARNING, logger="eps_image"):
            node.distribute(image=object(), toggles="not json{{")
        assert any("malformed" in record.message.lower() for record in caplog.records)

    def test_non_object_json_logs_a_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        node = EPSDistributor()
        with caplog.at_level(logging.WARNING, logger="eps_image"):
            node.distribute(image=object(), toggles="[1, 2, 3]")
        assert any(record.levelno == logging.WARNING for record in caplog.records)


# ------------------------------------------------------- returned tuple length


class TestReturnedTupleIsAlwaysMaxOutputsLong:
    @pytest.mark.parametrize(
        "toggles",
        [
            "{}",
            json.dumps({"out_1": False}),
            json.dumps({f"out_{n}": False for n in range(1, MAX_OUTPUTS + 1)}),
            "not json{{",
            "[1, 2, 3]",
        ],
    )
    def test_length_is_always_max_outputs(self, toggles: str, fake_execution_blocker) -> None:
        result = EPSDistributor().distribute(image=object(), toggles=toggles)
        assert len(result) == MAX_OUTPUTS == 8


# ------------------------------------------------------------- class shape


class TestClassShape:
    def test_category(self) -> None:
        assert EPSDistributor.CATEGORY == "EPSNodes"

    def test_return_types_length_and_values(self) -> None:
        assert len(EPSDistributor.RETURN_TYPES) == MAX_OUTPUTS == 8
        assert EPSDistributor.RETURN_TYPES == ("IMAGE",) * MAX_OUTPUTS

    def test_return_names_length_and_values(self) -> None:
        assert len(EPSDistributor.RETURN_NAMES) == MAX_OUTPUTS == 8
        assert tuple(f"out_{n}" for n in range(1, MAX_OUTPUTS + 1)) == EPSDistributor.RETURN_NAMES
        assert EPSDistributor.RETURN_NAMES[0] == "out_1"
        assert EPSDistributor.RETURN_NAMES[-1] == "out_8"

    def test_function_name_matches_the_declared_entry_point(self) -> None:
        assert EPSDistributor.FUNCTION == "distribute"
        assert callable(getattr(EPSDistributor(), EPSDistributor.FUNCTION))

    def test_no_output_is_list_attribute(self) -> None:
        # This node routes ONE image to N PARALLEL branches in ONE run --
        # unlike EPSSwitcher, it never fans into multiple downstream runs
        # (module docstring), so it must not declare OUTPUT_IS_LIST.
        assert not hasattr(EPSDistributor, "OUTPUT_IS_LIST")

    def test_no_input_is_list_attribute(self) -> None:
        assert not hasattr(EPSDistributor, "INPUT_IS_LIST")

    def test_no_is_changed_attribute(self) -> None:
        assert not hasattr(EPSDistributor, "IS_CHANGED")

    def test_image_is_lazy(self) -> None:
        # The flag `check_lazy_status` below depends on -- without it core
        # resolves `image` eagerly and the all-off upstream skip silently
        # stops working (owner question 2026-07-27; measured on the rig).
        _type, options = EPSDistributor.INPUT_TYPES()["required"]["image"]
        assert options["lazy"] is True

    def test_input_types_shape(self) -> None:
        spec = EPSDistributor.INPUT_TYPES()
        assert set(spec["required"]) == {"image"}
        assert spec["required"]["image"][0] == "IMAGE"
        assert set(spec["optional"]) == {"toggles"}
        widget_type, options = spec["optional"]["toggles"]
        assert widget_type == "STRING"
        assert options["default"] == "{}"
        assert options["multiline"] is False

    def test_toggles_is_optional_not_required(self) -> None:
        # A hand-built /prompt that omits `toggles` must still validate --
        # ComfyUI rejects a /prompt missing a REQUIRED input before the node
        # ever runs (module docstring).
        spec = EPSDistributor.INPUT_TYPES()
        assert "toggles" not in spec["required"]


# ------------------------------------------------------- lazy upstream skip


class TestCheckLazyStatus:
    """The all-off upstream skip (owner question 2026-07-27).

    Measured on the rig BEFORE this existed: with every output off, the whole
    upstream chain still executed and its results were thrown away. Declining
    `image` is what turns that into a real branch skip.
    """

    def test_requests_image_when_all_slots_enabled(self) -> None:
        assert EPSDistributor().check_lazy_status(toggles=_toggles()) == ["image"]

    def test_requests_image_when_toggles_omitted(self) -> None:
        # The no-frontend API path: no toggles at all means every slot is on.
        assert EPSDistributor().check_lazy_status() == ["image"]

    @pytest.mark.parametrize("still_on", [1, 4, MAX_OUTPUTS])
    def test_requests_image_when_even_one_slot_is_enabled(self, still_on: int) -> None:
        # A single enabled slot still needs the real image, so the upstream
        # must run -- only ALL-off may decline it.
        off = {f"out_{n}": False for n in range(1, MAX_OUTPUTS + 1) if n != still_on}
        assert EPSDistributor().check_lazy_status(toggles=json.dumps(off)) == ["image"]

    def test_declines_image_when_every_slot_is_off(self) -> None:
        toggles = _toggles(**{f"out_{n}": False for n in range(1, MAX_OUTPUTS + 1)})
        assert EPSDistributor().check_lazy_status(toggles=toggles) == []

    def test_malformed_toggles_still_requests_image(self) -> None:
        # Degrades to "everything enabled" exactly like `distribute` does --
        # a garbled widget value must never silently skip the upstream.
        assert EPSDistributor().check_lazy_status(toggles="not json{{") == ["image"]

    def test_all_off_distribute_never_touches_the_unresolved_image(
        self, fake_execution_blocker
    ) -> None:
        # The safety argument for declining `image` at all: on the all-off
        # path the value is genuinely unused, so core handing us `None`
        # instead of a tensor cannot change what we return.
        toggles = _toggles(**{f"out_{n}": False for n in range(1, MAX_OUTPUTS + 1)})
        result = EPSDistributor().distribute(image=None, toggles=toggles)
        assert len(result) == MAX_OUTPUTS
        assert all(isinstance(value, fake_execution_blocker) for value in result)

    def test_lazy_decision_matches_distribute_exactly(self, fake_execution_blocker) -> None:
        # check_lazy_status and distribute must never disagree about which
        # slots are on -- disagreeing would mean declining `image` while some
        # slot still expects to emit it. Both read `_enabled_slots`.
        node = EPSDistributor()
        for off_count in range(MAX_OUTPUTS + 1):
            toggles = json.dumps({f"out_{n}": False for n in range(1, off_count + 1)})
            wants_image = node.check_lazy_status(toggles=toggles) == ["image"]
            emits_image = any(
                value is not None and not isinstance(value, fake_execution_blocker)
                for value in node.distribute(image=object(), toggles=toggles)
            )
            assert wants_image == emits_image, f"{off_count} slots off"


# --------------------------------------------------------- no ComfyUI import


def test_module_never_imports_comfy_or_torch() -> None:
    assert "comfy" not in nodes_distributor.__dict__
    assert "torch" not in nodes_distributor.__dict__
    source = inspect.getsource(sys.modules[nodes_distributor.__name__])
    assert "import comfy" not in source
    assert "import torch" not in source
