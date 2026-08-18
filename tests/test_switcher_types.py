"""Tests for eps_image.nodes_switcher's MODEL/CLIP/VAE switcher siblings --
`EPSModelSwitcher`/`EPSClipSwitcher`/`EPSVaeSwitcher`, the `_make_switcher_ns`
generalization of `EPSSwitcher` (FORMAT.md §6.4) to non-IMAGE types.

Parametrized over the three new classes so every behavior (flatten
semantics, toggles, all-off blocker, lazy status decisions, empty-sibling
skip, flexible-INPUT_TYPES contains/getitem, class shape, wording) is proven
identical -- for its own noun -- to `EPSSwitcher`'s own coverage in
tests/test_switcher.py (left completely unmodified by this change; see that
file for the exhaustive IMAGE-specific suite this one deliberately does not
re-duplicate). "models"/"clips"/"vaes" here are plain sentinel objects (or
plain lists/tuples of sentinels standing in for an upstream's list output),
mirroring tests/test_switcher.py's own convention -- the node treats every
element opaquely regardless of type.

A dedicated, non-parametrized section at the bottom asserts the identity
case: `EPSSwitcher` itself, built by the same factory, still reports
IMAGE/`image_` exactly as before.
"""

from __future__ import annotations

import inspect
import json
import logging
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _fake_execution_blocker(monkeypatch: pytest.MonkeyPatch):
    """Autouse since v0.66.0: EPSModelSwitcher's execute now builds
    per-slot ExecutionBlockers for its models_low output whenever a row has
    no low wired (the common non-WAN case), so the lazy
    `comfy_execution.graph` import fires on ordinary executes here too --
    not just the all-off path. Same fake as test_switcher.py's fixture."""

    class FakeExecutionBlocker:
        def __init__(self, message: object) -> None:
            self.message = message

    graph_module = types.ModuleType("comfy_execution.graph")
    graph_module.ExecutionBlocker = FakeExecutionBlocker
    package = types.ModuleType("comfy_execution")
    package.graph = graph_module
    monkeypatch.setitem(sys.modules, "comfy_execution", package)
    monkeypatch.setitem(sys.modules, "comfy_execution.graph", graph_module)
    return FakeExecutionBlocker

from eps_image import nodes_switcher
from eps_image.nodes_switcher import (
    EPSClipSwitcher,
    EPSModelSwitcher,
    EPSSwitcher,
    EPSVaeSwitcher,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

#: One row per new switcher class: (class, prefix, io_type, output_name).
#: Drives every parametrized test below; the `id=` names the exact class in
#: a failing test's id instead of an opaque tuple repr.
_SWITCHER_TYPES = [
    pytest.param(EPSModelSwitcher, "model", "MODEL", "models", id="model"),
    pytest.param(EPSClipSwitcher, "clip", "CLIP", "clips", id="clip"),
    pytest.param(EPSVaeSwitcher, "vae", "VAE", "vaes", id="vae"),
]

#: Round-robin sibling for cross-type checks: a DIFFERENT registered
#: switcher class + its own prefix, for proving the sibling-emptiness check
#: recognizes an upstream of any of the four classes (FORMAT.md §6.4),
#: not just the caller's own type.
_OTHER_SWITCHER = {
    EPSModelSwitcher: (EPSClipSwitcher, "clip"),
    EPSClipSwitcher: (EPSVaeSwitcher, "vae"),
    EPSVaeSwitcher: (EPSModelSwitcher, "model"),
}

#: A different new-class prefix, for "rejects a sibling class's own prefix"
#: coverage (distinct from "rejects image_N", which every test below also
#: checks directly).
_OTHER_PREFIX = {"model": "clip", "clip": "vae", "vae": "model"}


def _toggles(**overrides: bool) -> str:
    """A `toggles` JSON string, e.g. `_toggles(model_2=False)`."""
    return json.dumps(overrides)


@pytest.fixture
def fake_execution_blocker(monkeypatch: pytest.MonkeyPatch):
    """Installs a fake ``comfy_execution.graph`` module exposing
    ``ExecutionBlocker`` into ``sys.modules``. Duplicated from
    tests/test_switcher.py's fixture of the same name rather than imported
    from it -- pytest test modules are not meant to import each other, and
    this pack's own convention (e.g. tests/test_resolution.py,
    tests/test_nodes_sets.py) is to redefine small fixtures like this one
    per file.
    """

    class FakeExecutionBlocker:
        def __init__(self, message: object) -> None:
            self.message = message

    fake_graph = types.ModuleType("comfy_execution.graph")
    fake_graph.ExecutionBlocker = FakeExecutionBlocker
    fake_pkg = types.ModuleType("comfy_execution")
    fake_pkg.graph = fake_graph

    monkeypatch.setitem(sys.modules, "comfy_execution", fake_pkg)
    monkeypatch.setitem(sys.modules, "comfy_execution.graph", fake_graph)
    return FakeExecutionBlocker


# --------------------------------------------------------- flexible inputs


@pytest.mark.parametrize(("cls", "prefix", "io_type", "output_name"), _SWITCHER_TYPES)
class TestFlexibleOptionalInputsPerType:
    def test_contains_accepts_own_prefix_any_n(self, cls, prefix, io_type, output_name) -> None:
        optional = cls.INPUT_TYPES()["optional"]
        assert f"{prefix}_5" in optional
        assert f"{prefix}_37" in optional

    def test_getitem_synthesizes_own_io_type_and_lazy(
        self, cls, prefix, io_type, output_name
    ) -> None:
        optional = cls.INPUT_TYPES()["optional"]
        entry_type, options = optional[f"{prefix}_5"]
        assert entry_type == io_type
        assert options["lazy"] is True
        assert isinstance(options["tooltip"], str) and options["tooltip"]

    def test_slot_1_is_a_real_dict_entry_and_lazy(self, cls, prefix, io_type, output_name) -> None:
        optional = cls.INPUT_TYPES()["optional"]
        assert list(optional.keys()) == [f"{prefix}_1", "toggles"]
        entry_type, options = optional[f"{prefix}_1"]
        assert entry_type == io_type
        assert options["lazy"] is True

    def test_contains_rejects_image_prefix(self, cls, prefix, io_type, output_name) -> None:
        # FORMAT.md hard constraint's own example: image_2 must not be
        # accepted on a non-IMAGE switcher.
        optional = cls.INPUT_TYPES()["optional"]
        assert "image_1" not in optional
        assert "image_2" not in optional

    def test_contains_rejects_a_sibling_switcher_types_prefix(
        self, cls, prefix, io_type, output_name
    ) -> None:
        other_prefix = _OTHER_PREFIX[prefix]
        optional = cls.INPUT_TYPES()["optional"]
        assert f"{other_prefix}_1" not in optional

    def test_contains_rejects_near_miss_names(self, cls, prefix, io_type, output_name) -> None:
        optional = cls.INPUT_TYPES()["optional"]
        assert f"{prefix}_" not in optional
        assert f"{prefix}_1x" not in optional
        assert f"not_a_{prefix}_1" not in optional

    def test_getitem_raises_keyerror_for_non_matching_key(
        self, cls, prefix, io_type, output_name
    ) -> None:
        optional = cls.INPUT_TYPES()["optional"]
        with pytest.raises(KeyError):
            optional["not_a_real_input"]

    def test_toggles_widget_shape(self, cls, prefix, io_type, output_name) -> None:
        optional = cls.INPUT_TYPES()["optional"]
        widget_type, spec = optional["toggles"]
        assert widget_type == "STRING"
        assert spec["default"] == "{}"
        assert spec["hidden"] is True
        assert "lazy" not in spec

    def test_toggles_is_optional_not_required(self, cls, prefix, io_type, output_name) -> None:
        spec = cls.INPUT_TYPES()
        assert spec["required"] == {}
        assert "toggles" not in spec["required"]


# ------------------------------------------------------------------ execute


@pytest.mark.parametrize(("cls", "prefix", "io_type", "output_name"), _SWITCHER_TYPES)
class TestExecuteFlattenSemantics:
    def test_single_connected_and_enabled_value_passes_through(
        self, cls, prefix, io_type, output_name
    ) -> None:
        node = cls()
        result = node.execute(toggles=_toggles(), **{f"{prefix}_1": "a"})
        assert result[0] == ["a"]

    def test_default_toggles_enables_every_connected_slot(
        self, cls, prefix, io_type, output_name
    ) -> None:
        # No `toggles` value at all -- a plain API caller who never loaded
        # the frontend JS.
        node = cls()
        result = node.execute(**{f"{prefix}_1": "a", f"{prefix}_2": "b"})
        assert result[0] == ["a", "b"]

    def test_collects_in_ascending_n_regardless_of_kwarg_order(
        self, cls, prefix, io_type, output_name
    ) -> None:
        node = cls()
        result = node.execute(
            toggles=_toggles(),
            **{f"{prefix}_3": "c", f"{prefix}_1": "a", f"{prefix}_2": "b"},
        )
        assert result[0] == ["a", "b", "c"]

    def test_toggles_key_absent_for_a_slot_keeps_it_enabled(
        self, cls, prefix, io_type, output_name
    ) -> None:
        # toggles mentions only a DIFFERENT slot; the untouched one defaults on.
        node = cls()
        toggles = json.dumps({f"{prefix}_2": False})
        result = node.execute(toggles=toggles, **{f"{prefix}_1": "a", f"{prefix}_2": "b"})
        assert result[0] == ["a"]

    @pytest.mark.parametrize("falsy", [None, 0, "", [], {}])
    def test_non_bool_falsy_toggle_value_keeps_slot_enabled(
        self, cls, prefix, io_type, output_name, falsy: object
    ) -> None:
        # Only the LITERAL boolean False disables a slot (matches the
        # frontend's `!== false` and EPSSwitcher's own contract).
        node = cls()
        toggles = json.dumps({f"{prefix}_2": falsy})
        result = node.execute(
            toggles=toggles,
            **{f"{prefix}_1": "a", f"{prefix}_2": "b", f"{prefix}_3": "c"},
        )
        assert result[0] == ["a", "b", "c"]

    def test_explicit_boolean_false_is_the_only_disabler(
        self, cls, prefix, io_type, output_name
    ) -> None:
        node = cls()
        result = node.execute(
            toggles=json.dumps({f"{prefix}_2": False}),
            **{f"{prefix}_1": "a", f"{prefix}_2": "b"},
        )
        assert result[0] == ["a"]

    def test_disconnected_slot_none_is_skipped_even_if_marked_enabled(
        self, cls, prefix, io_type, output_name
    ) -> None:
        node = cls()
        result = node.execute(
            toggles=_toggles(**{f"{prefix}_2": True}),
            **{f"{prefix}_1": "a", f"{prefix}_2": None, f"{prefix}_3": "c"},
        )
        assert result[0] == ["a", "c"]

    def test_multiple_disabled_slots_all_omitted(self, cls, prefix, io_type, output_name) -> None:
        node = cls()
        result = node.execute(
            toggles=_toggles(**{f"{prefix}_1": False, f"{prefix}_3": False}),
            **{
                f"{prefix}_1": "a",
                f"{prefix}_2": "b",
                f"{prefix}_3": "c",
                f"{prefix}_4": "d",
            },
        )
        assert result[0] == ["b", "d"]

    def test_malformed_toggles_json_falls_back_to_all_enabled(
        self, cls, prefix, io_type, output_name
    ) -> None:
        node = cls()
        result = node.execute(
            toggles="not json{{", **{f"{prefix}_1": "a", f"{prefix}_2": "b"}
        )
        assert result[0] == ["a", "b"]

    def test_toggles_that_is_not_a_json_object_falls_back_to_all_enabled(
        self, cls, prefix, io_type, output_name
    ) -> None:
        node = cls()
        result = node.execute(toggles="[1, 2, 3]", **{f"{prefix}_1": "a"})
        assert result[0] == ["a"]

    def test_list_wrapped_toggles_is_unwrapped(self, cls, prefix, io_type, output_name) -> None:
        # INPUT_IS_LIST shape: real ComfyUI wraps every input, `toggles`
        # included, in a list.
        node = cls()
        result = node.execute(toggles=[_toggles()], **{f"{prefix}_1": ["a"]})
        assert result[0] == ["a"]

    def test_empty_list_toggles_falls_back_to_default(
        self, cls, prefix, io_type, output_name
    ) -> None:
        node = cls()
        result = node.execute(toggles=[], **{f"{prefix}_1": ["a"]})
        assert result[0] == ["a"]

    def test_list_producing_upstream_merges_element_wise(
        self, cls, prefix, io_type, output_name
    ) -> None:
        # A list-producing upstream (OUTPUT_IS_LIST, e.g. any future
        # multi-value producer of this type) contributes every element, not
        # one opaque item -- the same mechanic EPSSwitcher fixed for
        # EPSImageGrid, generalized.
        node = cls()
        result = node.execute(
            toggles=[_toggles()],
            **{f"{prefix}_1": ["x0", "x1", "x2"], f"{prefix}_2": ["y0"]},
        )
        assert result[0] == ["x0", "x1", "x2", "y0"]

    def test_disabled_list_shaped_slot_contributes_nothing(
        self, cls, prefix, io_type, output_name
    ) -> None:
        node = cls()
        result = node.execute(
            toggles=[_toggles(**{f"{prefix}_1": False})],
            **{f"{prefix}_1": ["x0", "x1"], f"{prefix}_2": ["y0"]},
        )
        assert result[0] == ["y0"]

    def test_flatten_preserves_slot_order_and_intra_slot_order(
        self, cls, prefix, io_type, output_name
    ) -> None:
        node = cls()
        result = node.execute(
            toggles=[_toggles()],
            **{
                f"{prefix}_3": ["c0", "c1"],
                f"{prefix}_1": ["a0", "a1", "a2"],
                f"{prefix}_2": ["b0"],
            },
        )
        assert result[0] == ["a0", "a1", "a2", "b0", "c0", "c1"]

    def test_single_element_list_batch_stays_one_output_element(
        self, cls, prefix, io_type, output_name
    ) -> None:
        node = cls()
        batch_stand_in = object()
        result = node.execute(toggles=[_toggles()], **{f"{prefix}_1": [batch_stand_in]})
        assert result[0] == [batch_stand_in]

    def test_none_elements_within_a_resolved_list_are_skipped(
        self, cls, prefix, io_type, output_name
    ) -> None:
        node = cls()
        result = node.execute(toggles=[_toggles()], **{f"{prefix}_1": ["x0", None, "x2"]})
        assert result[0] == ["x0", "x2"]

    def test_bare_non_list_value_is_tolerated_as_one_element(
        self, cls, prefix, io_type, output_name
    ) -> None:
        node = cls()
        result = node.execute(toggles=_toggles(), **{f"{prefix}_1": "plain_stand_in"})
        assert result[0] == ["plain_stand_in"]


# --------------------------------------------------------------- all-off


@pytest.mark.parametrize(("cls", "prefix", "io_type", "output_name"), _SWITCHER_TYPES)
class TestAllOffOrNoneConnectedReturnsAnExecutionBlocker:
    def test_nothing_connected_returns_a_one_element_blocker_list(
        self, cls, prefix, io_type, output_name, fake_execution_blocker: type
    ) -> None:
        node = cls()
        result = node.execute(toggles=_toggles())
        expected_arity = 2 if cls is nodes_switcher.EPSModelSwitcher else 1
        assert isinstance(result, tuple) and len(result) == expected_arity
        assert isinstance(result[0], list) and len(result[0]) == 1
        blocker = result[0][0]
        assert isinstance(blocker, fake_execution_blocker)
        assert blocker.message is None

    def test_all_connected_but_toggled_off_returns_a_one_element_blocker_list(
        self, cls, prefix, io_type, output_name, fake_execution_blocker: type
    ) -> None:
        node = cls()
        result = node.execute(
            toggles=_toggles(**{f"{prefix}_1": False, f"{prefix}_2": False}),
            **{f"{prefix}_1": "a", f"{prefix}_2": "b"},
        )
        assert len(result[0]) == 1
        assert isinstance(result[0][0], fake_execution_blocker)
        assert result[0][0].message is None

    def test_all_off_does_not_raise(
        self, cls, prefix, io_type, output_name, fake_execution_blocker: type
    ) -> None:
        node = cls()
        node.execute(toggles=_toggles(**{f"{prefix}_1": False}), **{f"{prefix}_1": "a"})

    def test_nothing_connected_logs_the_right_noun_at_info(
        self,
        cls,
        prefix,
        io_type,
        output_name,
        fake_execution_blocker: type,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        node = cls()
        with caplog.at_level(logging.INFO, logger="eps_image"):
            node.execute(toggles=_toggles())
        assert any(f"no {prefix} inputs are connected" in r.message for r in caplog.records)
        assert all(r.levelno <= logging.INFO for r in caplog.records)

    def test_all_toggled_off_log_names_the_count_and_noun(
        self,
        cls,
        prefix,
        io_type,
        output_name,
        fake_execution_blocker: type,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        node = cls()
        with caplog.at_level(logging.INFO, logger="eps_image"):
            node.execute(
                toggles=_toggles(**{f"{prefix}_1": False, f"{prefix}_2": False}),
                **{f"{prefix}_1": "a", f"{prefix}_2": "b"},
            )
        messages = [r.message for r in caplog.records]
        assert any(f"2 {prefix} input" in m for m in messages)
        assert any("toggled off" in m for m in messages)


# ------------------------------------------------------------ check_lazy_status


@pytest.mark.parametrize(("cls", "prefix", "io_type", "output_name"), _SWITCHER_TYPES)
class TestCheckLazyStatus:
    def test_enabled_connected_slots_are_requested_in_ascending_order(
        self, cls, prefix, io_type, output_name
    ) -> None:
        node = cls()
        result = node.check_lazy_status(
            toggles=[_toggles()], **{f"{prefix}_1": (None,), f"{prefix}_2": (None,)}
        )
        assert result == [f"{prefix}_1", f"{prefix}_2"]

    def test_disabled_connected_slot_is_not_requested(
        self, cls, prefix, io_type, output_name
    ) -> None:
        node = cls()
        result = node.check_lazy_status(
            toggles=[_toggles(**{f"{prefix}_1": False})],
            **{f"{prefix}_1": (None,), f"{prefix}_2": (None,)},
        )
        assert result == [f"{prefix}_2"]

    def test_all_disabled_requests_nothing(self, cls, prefix, io_type, output_name) -> None:
        node = cls()
        result = node.check_lazy_status(
            toggles=[_toggles(**{f"{prefix}_1": False, f"{prefix}_2": False})],
            **{f"{prefix}_1": (None,), f"{prefix}_2": (None,)},
        )
        assert result == []

    def test_unconnected_slot_is_never_requested(self, cls, prefix, io_type, output_name) -> None:
        node = cls()
        result = node.check_lazy_status(toggles=[_toggles()], **{f"{prefix}_1": (None,)})
        assert result == [f"{prefix}_1"]

    def test_nothing_connected_requests_nothing(self, cls, prefix, io_type, output_name) -> None:
        node = cls()
        result = node.check_lazy_status(toggles=[_toggles()])
        assert result == []

    def test_already_resolved_enabled_slot_is_still_named(
        self, cls, prefix, io_type, output_name
    ) -> None:
        node = cls()
        result = node.check_lazy_status(
            toggles=[_toggles()], **{f"{prefix}_1": ["already_resolved"]}
        )
        assert result == [f"{prefix}_1"]

    def test_bare_toggles_string_is_tolerated(self, cls, prefix, io_type, output_name) -> None:
        node = cls()
        result = node.check_lazy_status(
            toggles=_toggles(**{f"{prefix}_1": False}), **{f"{prefix}_1": (None,)}
        )
        assert result == []

    def test_malformed_toggles_falls_back_to_all_requested(
        self, cls, prefix, io_type, output_name
    ) -> None:
        node = cls()
        result = node.check_lazy_status(
            toggles=["not json{{"], **{f"{prefix}_1": (None,), f"{prefix}_2": (None,)}
        )
        assert result == [f"{prefix}_1", f"{prefix}_2"]


# --------------------------------------------------- empty-sibling skip


@pytest.mark.parametrize(("cls", "prefix", "io_type", "output_name"), _SWITCHER_TYPES)
class TestEmptySiblingSkipAcrossClassTypes:
    """2026-07-26 owner report (originally for EPSSwitcher, generalized
    here): a provably-all-off switcher must never veto a sibling's OTHER,
    perfectly good enabled inputs. FORMAT.md §6.4's generalized hard rule:
    each class checks prompts for ALL FOUR class ids, using each upstream's
    OWN prefix -- proven directly by ``test_..._DIFFERENT_switcher_class``
    below, which feeds a slot from a switcher of a different registered
    class than the one under test.
    """

    @staticmethod
    def _prompt(
        consumer_class_id: str,
        consumer_prefix: str,
        upstream_class_id: str,
        upstream_prefix: str,
        upstream_toggles: str,
        *,
        upstream_wired: bool = True,
    ) -> dict:
        upstream_inputs: dict = {"toggles": upstream_toggles}
        if upstream_wired:
            upstream_inputs[f"{upstream_prefix}_1"] = ["1", 0]
        return {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {}},
            "2": {"class_type": upstream_class_id, "inputs": upstream_inputs},
            "3": {"class_type": "CheckpointLoaderSimple", "inputs": {}},
            "4": {
                "class_type": consumer_class_id,
                "inputs": {
                    f"{consumer_prefix}_1": ["2", 0],
                    f"{consumer_prefix}_2": ["3", 0],
                    "toggles": "{}",
                },
            },
        }

    def _request(self, cls, prefix: str, prompt: dict):
        return cls().check_lazy_status(
            toggles=["{}"],
            prompt=[prompt],
            unique_id=["4"],
            **{f"{prefix}_1": (None,), f"{prefix}_2": (None,)},
        )

    def test_slot_fed_by_an_all_off_sibling_of_the_SAME_type_is_not_requested(
        self, cls, prefix, io_type, output_name
    ) -> None:
        prompt = self._prompt(
            cls.__name__, prefix, cls.__name__, prefix, json.dumps({f"{prefix}_1": False})
        )
        assert self._request(cls, prefix, prompt) == [f"{prefix}_2"]

    def test_slot_fed_by_a_same_type_sibling_with_nothing_wired_is_not_requested(
        self, cls, prefix, io_type, output_name
    ) -> None:
        prompt = self._prompt(
            cls.__name__, prefix, cls.__name__, prefix, "{}", upstream_wired=False
        )
        assert self._request(cls, prefix, prompt) == [f"{prefix}_2"]

    def test_slot_fed_by_a_LIVE_same_type_sibling_is_still_requested(
        self, cls, prefix, io_type, output_name
    ) -> None:
        prompt = self._prompt(cls.__name__, prefix, cls.__name__, prefix, "{}")
        assert self._request(cls, prefix, prompt) == [f"{prefix}_1", f"{prefix}_2"]

    def test_slot_fed_by_an_all_off_DIFFERENT_switcher_class_is_also_skipped(
        self, cls, prefix, io_type, output_name
    ) -> None:
        # The core of the generalization: the registry recognizes ALL FOUR
        # class ids, not just this class's own -- proven by feeding this
        # class's slot from a DIFFERENT registered switcher class that is
        # itself provably all-off, using ITS OWN prefix.
        other_cls, other_prefix = _OTHER_SWITCHER[cls]
        prompt = self._prompt(
            cls.__name__,
            prefix,
            other_cls.__name__,
            other_prefix,
            json.dumps({f"{other_prefix}_1": False}),
        )
        assert self._request(cls, prefix, prompt) == [f"{prefix}_2"]

    def test_slot_fed_by_a_LIVE_DIFFERENT_switcher_class_is_still_requested(
        self, cls, prefix, io_type, output_name
    ) -> None:
        other_cls, other_prefix = _OTHER_SWITCHER[cls]
        prompt = self._prompt(cls.__name__, prefix, other_cls.__name__, other_prefix, "{}")
        assert self._request(cls, prefix, prompt) == [f"{prefix}_1", f"{prefix}_2"]

    def test_non_switcher_upstream_is_never_skipped(
        self, cls, prefix, io_type, output_name
    ) -> None:
        prompt = self._prompt(cls.__name__, prefix, cls.__name__, prefix, "{}")
        prompt["2"] = {"class_type": "CheckpointLoaderSimple", "inputs": {}}
        assert self._request(cls, prefix, prompt) == [f"{prefix}_1", f"{prefix}_2"]

    def test_wired_toggles_upstream_is_treated_as_unknown(
        self, cls, prefix, io_type, output_name
    ) -> None:
        # toggles arriving as a LINK can't be evaluated statically -> assume
        # the upstream produces something.
        prompt = self._prompt(cls.__name__, prefix, cls.__name__, prefix, "{}")
        prompt["2"]["inputs"]["toggles"] = ["9", 0]
        assert self._request(cls, prefix, prompt) == [f"{prefix}_1", f"{prefix}_2"]

    def test_uninspectable_graph_requests_everything_enabled(
        self, cls, prefix, io_type, output_name
    ) -> None:
        for kwargs in ({}, {"prompt": [None], "unique_id": ["4"]}):
            requested = cls().check_lazy_status(
                toggles=["{}"],
                **{f"{prefix}_1": (None,), f"{prefix}_2": (None,)},
                **kwargs,
            )
            assert requested == [f"{prefix}_1", f"{prefix}_2"]

    def test_an_unrequested_slot_contributes_nothing_at_execute(
        self, cls, prefix, io_type, output_name
    ) -> None:
        result = cls().execute(
            toggles=["{}"], **{f"{prefix}_1": (None,), f"{prefix}_2": ["live"]}
        )
        assert result[0] == ["live"]


# --------------------------------------------------------- class shape / spec


@pytest.mark.parametrize(("cls", "prefix", "io_type", "output_name"), _SWITCHER_TYPES)
class TestClassShape:
    def test_category(self, cls, prefix, io_type, output_name) -> None:
        assert cls.CATEGORY == "EPSNodes/Switchers"

    def test_return_types_and_names(self, cls, prefix, io_type, output_name) -> None:
        # v0.66.0: the MODEL class carries the WAN high/low paired tail
        # output; CLIP/VAE stay single-output (owner spec: models only).
        if cls is nodes_switcher.EPSModelSwitcher:
            assert (io_type, io_type) == cls.RETURN_TYPES
        else:
            assert (io_type,) == cls.RETURN_TYPES
        if cls is nodes_switcher.EPSModelSwitcher:
            assert (output_name, "models_low") == cls.RETURN_NAMES
        else:
            assert (output_name,) == cls.RETURN_NAMES

    def test_output_is_list_flagged_true(self, cls, prefix, io_type, output_name) -> None:
        expected = (True, True) if cls is nodes_switcher.EPSModelSwitcher else (True,)
        assert expected == cls.OUTPUT_IS_LIST

    def test_input_is_list_flagged_true(self, cls, prefix, io_type, output_name) -> None:
        assert cls.INPUT_IS_LIST is True

    def test_check_lazy_status_is_defined_and_callable(
        self, cls, prefix, io_type, output_name
    ) -> None:
        assert callable(getattr(cls(), "check_lazy_status", None))

    def test_function_name_matches_the_declared_entry_point(
        self, cls, prefix, io_type, output_name
    ) -> None:
        assert cls.FUNCTION == "execute"
        assert callable(getattr(cls(), cls.FUNCTION))

    def test_execute_return_shape_is_a_one_tuple_of_a_list(
        self, cls, prefix, io_type, output_name
    ) -> None:
        result = cls().execute(toggles=_toggles(), **{f"{prefix}_1": "a"})
        assert isinstance(result, tuple)
        assert len(result) == (2 if cls is nodes_switcher.EPSModelSwitcher else 1)
        assert isinstance(result[0], list)

    def test_hidden_inputs_declared(self, cls, prefix, io_type, output_name) -> None:
        spec = cls.INPUT_TYPES()
        assert spec["hidden"] == {"prompt": "PROMPT", "unique_id": "UNIQUE_ID"}

    def test_output_tooltips_present(self, cls, prefix, io_type, output_name) -> None:
        expected_len = 2 if cls is nodes_switcher.EPSModelSwitcher else 1
        assert len(cls.OUTPUT_TOOLTIPS) == expected_len
        assert all(isinstance(tip, str) and tip for tip in cls.OUTPUT_TOOLTIPS)
        assert isinstance(cls.OUTPUT_TOOLTIPS[0], str) and cls.OUTPUT_TOOLTIPS[0]

    def test_classes_are_distinct_objects(self, cls, prefix, io_type, output_name) -> None:
        # Each _make_switcher_ns call must produce its OWN class, never
        # accidentally sharing identity with EPSSwitcher or another sibling.
        assert cls is not EPSSwitcher
        assert cls.__name__ == {
            "model": "EPSModelSwitcher",
            "clip": "EPSClipSwitcher",
            "vae": "EPSVaeSwitcher",
        }[prefix]


@pytest.mark.parametrize(("cls", "prefix", "io_type", "output_name"), _SWITCHER_TYPES)
class TestDescriptionAndTooltipWording:
    """FORMAT.md hard constraint: every class's DESCRIPTION/tooltips must
    read naturally for its own noun, and mention that a toggled-off
    branch's upstream (e.g. a checkpoint load) never executes."""

    def test_description_does_not_leak_image_wording(
        self, cls, prefix, io_type, output_name
    ) -> None:
        assert "image" not in cls.DESCRIPTION.lower()

    def test_description_mentions_a_checkpoint_load_never_running(
        self, cls, prefix, io_type, output_name
    ) -> None:
        assert "checkpoint" in cls.DESCRIPTION.lower()
        assert "never run" in cls.DESCRIPTION.lower() or "never execute" in cls.DESCRIPTION.lower()

    def test_input_tooltip_does_not_leak_image_wording(
        self, cls, prefix, io_type, output_name
    ) -> None:
        _entry_type, options = cls.INPUT_TYPES()["optional"][f"{prefix}_1"]
        assert "image" not in options["tooltip"].lower()

    def test_output_tooltip_does_not_leak_image_wording(
        self, cls, prefix, io_type, output_name
    ) -> None:
        assert "image" not in cls.OUTPUT_TOOLTIPS[0].lower()


# --------------------------------------------------------------- registration


class TestRegistration:
    """FORMAT.md §8: node classes register via the top-level ``__init__.py``'s
    ``_NODE_SPECS`` defensive-loader table (module_path, class_id,
    display_name). That file needs a real ComfyUI (``folder_paths``,
    ``server``) to actually IMPORT -- no test in this suite imports it live
    (see pytest.ini's ``--confcutdir=tests`` comment) -- so, mirroring
    tests/test_vue_nodes_compat.py's source-inspection convention, this
    checks the registration table TEXTUALLY instead.
    """

    @staticmethod
    def _init_source() -> str:
        return (REPO_ROOT / "__init__.py").read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        ("class_id", "display_name"),
        [
            ("EPSModelSwitcher", "EPS Model Switcher"),
            ("EPSClipSwitcher", "EPS CLIP Switcher"),
            ("EPSVaeSwitcher", "EPS VAE Switcher"),
        ],
    )
    def test_new_switcher_registered_with_display_name(
        self, class_id: str, display_name: str
    ) -> None:
        source = self._init_source()
        expected = f'("eps_image.nodes_switcher", "{class_id}", "{display_name}")'
        assert expected in source, (
            f"{class_id} is not registered in _NODE_SPECS with display name {display_name!r}"
        )

    def test_eps_switcher_registration_unchanged(self) -> None:
        source = self._init_source()
        assert '("eps_image.nodes_switcher", "EPSSwitcher", "EPS Image Switcher")' in source


# --------------------------------------------------------------- no ComfyUI import


def test_module_never_imports_comfy_or_torch() -> None:
    # Re-pins tests/test_switcher.py's own check against THIS module after
    # its generalization -- the lazy ExecutionBlocker import is now shared
    # code reached by all four classes' execute(), so re-confirming it stays
    # import-free at module scope (and stays a `from comfy_execution.graph
    # import ExecutionBlocker` shape, which does not match the substring
    # this test looks for) matters just as much post-refactor.
    assert "comfy" not in nodes_switcher.__dict__
    assert "torch" not in nodes_switcher.__dict__
    source = inspect.getsource(sys.modules[nodes_switcher.__name__])
    assert "import comfy" not in source
    assert "import torch" not in source


# ------------------------------------------------ EPSSwitcher identity case


class TestEPSSwitcherIdentityViaFactory:
    """FORMAT.md hard constraint: EPSSwitcher itself is now ALSO built by
    `_make_switcher_ns` (prefix="image", io_type="IMAGE") -- confirm the
    factory reproduces its original, pre-refactor shape exactly. The
    exhaustive behavioral suite for EPSSwitcher lives, unmodified, in
    tests/test_switcher.py; this class only pins the identity claim itself.
    """

    def test_return_types_and_names_are_still_image(self) -> None:
        assert EPSSwitcher.RETURN_TYPES == ("IMAGE",)
        assert EPSSwitcher.RETURN_NAMES == ("images",)

    def test_input_types_optional_still_uses_the_image_prefix(self) -> None:
        optional = EPSSwitcher.INPUT_TYPES()["optional"]
        assert "image_1" in optional
        assert "image_5" in optional
        entry_type, options = optional["image_5"]
        assert entry_type == "IMAGE"
        assert options["lazy"] is True

    def test_input_types_optional_rejects_the_new_classes_prefixes(self) -> None:
        optional = EPSSwitcher.INPUT_TYPES()["optional"]
        assert "model_1" not in optional
        assert "clip_1" not in optional
        assert "vae_1" not in optional

    def test_class_id_is_still_epsswitcher(self) -> None:
        assert EPSSwitcher.__name__ == "EPSSwitcher"

    def test_registered_in_the_slot_pattern_registry(self) -> None:
        assert "EPSSwitcher" in nodes_switcher._SWITCHER_SLOT_PATTERNS
        assert nodes_switcher._SWITCHER_SLOT_PATTERNS["EPSSwitcher"].pattern == r"image_(\d+)"
