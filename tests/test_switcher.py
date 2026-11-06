"""Tests for eps_image.nodes_switcher (FORMAT.md §6.4, `EPSSwitcher`).

No ComfyUI/torch anywhere -- unlike lora_library's tests, this node needs no
context fixture (no `set_context`) and no faked `comfy.*` modules; "images"
are plain sentinel objects (or, for the INPUT_IS_LIST-shaped tests below,
plain lists/tuples of sentinels standing in for an upstream's IMAGE output
list -- the node treats every element opaquely either way), since the node
never inspects their contents.
"""

from __future__ import annotations

import inspect
import json
import logging
import sys
import types

import pytest

from eps_image import nodes_switcher
from eps_image.nodes_switcher import EPSSwitcher, _FlexibleOptionalImageInputs


def _toggles(**overrides: bool) -> str:
    """A `toggles` JSON string, e.g. `_toggles(image_2=False)`."""
    return json.dumps(overrides)


@pytest.fixture(autouse=True)
def _fake_execution_blocker_everywhere(monkeypatch: pytest.MonkeyPatch):
    """Autouse since the pass_masks round: with `pass_masks` off (every
    legacy call in this file), execute() emits ONE ExecutionBlocker on the
    `masks` output, so the lazy `comfy_execution.graph` import now fires on
    the happy path too, not just the all-off one -- the same move
    tests/test_switcher_types.py made for v0.66.0's models_low. The named
    `fake_execution_blocker` fixture below is kept for the tests that
    isinstance() the blocker; it installs the same shape."""

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


@pytest.fixture
def fake_execution_blocker(monkeypatch: pytest.MonkeyPatch):
    """Installs a fake ``comfy_execution.graph`` module exposing
    ``ExecutionBlocker`` into ``sys.modules`` -- mirrors the ``fake_comfy``
    convention in ``tests/test_resolution.py``/``test_nodes_sets.py`` (this
    pack's tests never require a real ComfyUI install on the path).
    ``EPSSwitcher.execute``'s all-off path imports ``ExecutionBlocker``
    lazily from exactly this module path (FORMAT.md §6.4), so installing the
    fake here is the whole story -- nothing in ``nodes_switcher`` itself
    needs patching. Returns the fake class so tests can ``isinstance()``
    the returned blocker.
    """

    class FakeExecutionBlocker:
        """Mirrors the real ``comfy_execution.graph_utils.ExecutionBlocker``
        (``__init__(self, message)`` storing ``self.message``) exactly
        enough for these tests -- an identity/attribute check, since
        nodes_switcher.py never does anything else with the instance.
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


# --------------------------------------------------------- flexible inputs


class TestFlexibleOptionalImageInputs:
    def test_declared_image_1_is_a_real_dict_entry(self) -> None:
        optional = _FlexibleOptionalImageInputs({"image_1": ("IMAGE",)})
        assert optional["image_1"] == ("IMAGE",)
        assert list(optional.keys()) == ["image_1"]

    def test_contains_accepts_any_image_n(self) -> None:
        optional = _FlexibleOptionalImageInputs({"image_1": ("IMAGE",)})
        assert "image_5" in optional
        assert "image_37" in optional
        # Only image_1 was actually inserted -- __contains__ says yes to
        # image_5 without it ever appearing in .keys()/.items() (see the
        # dedicated .keys() assertion above).

    def test_getitem_synthesizes_the_image_type_for_ungrown_slots(self) -> None:
        optional = _FlexibleOptionalImageInputs({"image_1": ("IMAGE",)})
        # Every dynamically-grown slot is lazy too (nodes_switcher.py module
        # docstring "Disabled slots are also genuinely lazy now") --
        # independent of whatever the LOCALLY inserted image_1 above
        # carries, since __getitem__'s synthesis path is what's under test.
        # Per-key (not exact-dict): the synthesized options also carry a
        # UI-facing `tooltip` string (text pass, 2026-07).
        image_type, options = optional["image_5"]
        assert image_type == "IMAGE"
        assert options["lazy"] is True

    def test_contains_rejects_non_matching_keys(self) -> None:
        optional = _FlexibleOptionalImageInputs({"image_1": ("IMAGE",)})
        assert "video_1" not in optional
        assert "image_" not in optional
        assert "image_1x" not in optional

    def test_getitem_raises_keyerror_for_non_matching_keys(self) -> None:
        optional = _FlexibleOptionalImageInputs({"image_1": ("IMAGE",)})
        with pytest.raises(KeyError):
            optional["not_an_image_input"]

    def test_input_types_optional_accepts_image_5(self) -> None:
        input_types = EPSSwitcher.INPUT_TYPES()
        assert "image_5" in input_types["optional"]
        # Per-key (not exact-dict): the synthesized options also carry a
        # UI-facing `tooltip` string (text pass, 2026-07).
        image_type, options = input_types["optional"]["image_5"]
        assert image_type == "IMAGE"
        assert options["lazy"] is True

    def test_input_types_image_1_is_lazy(self) -> None:
        # The concrete, hardcoded entry -- independent of the synthesized
        # ones above; both must carry `lazy: True` (nodes_switcher.py module
        # docstring "Disabled slots are also genuinely lazy now") or slots
        # would be inconsistently lazy depending on whether switcher.js had
        # grown them yet. Per-key (not exact-dict): also carries a
        # UI-facing `tooltip` string (text pass, 2026-07).
        input_types = EPSSwitcher.INPUT_TYPES()
        image_type, options = input_types["optional"]["image_1"]
        assert image_type == "IMAGE"
        assert options["lazy"] is True

    def test_input_types_toggles_is_not_lazy(self) -> None:
        # `toggles` must stay eager -- `check_lazy_status` needs it
        # immediately, before anything is resolved, to decide which
        # image_N slots are even worth requesting.
        input_types = EPSSwitcher.INPUT_TYPES()
        _widget_type, spec = input_types["optional"]["toggles"]
        assert "lazy" not in spec

    def test_input_types_optional_toggles_widget_default(self) -> None:
        # `toggles` rides in `optional` (NOT `required`): a hand-built /prompt
        # that omits it must still run -- a missing REQUIRED input is rejected
        # by ComfyUI before execute() ever sees it, which would break the
        # documented no-frontend API path. required is empty.
        input_types = EPSSwitcher.INPUT_TYPES()
        assert input_types["required"] == {}
        assert "toggles" not in input_types["required"]
        widget_type, spec = input_types["optional"]["toggles"]
        assert widget_type == "STRING"
        assert spec["default"] == "{}"


# ------------------------------------------------------------------ execute


class TestExecuteCollectsEnabledInAscendingOrder:
    def test_single_connected_and_enabled_image_passes_through(self) -> None:
        node = EPSSwitcher()
        result = node.execute(toggles=_toggles(), image_1="img1")
        assert result[0] == ["img1"]

    def test_default_toggles_enables_every_connected_slot(self) -> None:
        # No `toggles` value at all (a plain API caller who never loaded
        # switcher.js) -- module docstring's default-enabled rationale.
        node = EPSSwitcher()
        result = node.execute(image_1="img1", image_2="img2")
        assert result[0] == ["img1", "img2"]

    def test_collects_in_ascending_n_regardless_of_kwarg_order(self) -> None:
        node = EPSSwitcher()
        result = node.execute(
            toggles=_toggles(),
            image_3="img3",
            image_1="img1",
            image_2="img2",
        )
        assert result[0] == ["img1", "img2", "img3"]

    def test_disabled_slot_is_omitted(self) -> None:
        node = EPSSwitcher()
        result = node.execute(
            toggles=_toggles(image_2=False),
            image_1="img1",
            image_2="img2",
            image_3="img3",
        )
        assert result[0] == ["img1", "img3"]

    @pytest.mark.parametrize("falsy", [None, 0, "", [], {}])
    def test_non_bool_falsy_toggle_value_keeps_slot_enabled(self, falsy: object) -> None:
        # Regression (R9 review): only the LITERAL boolean False disables a
        # slot. A non-bool falsy value (null/0/""/[]/{}) from a hand-edited
        # workflow or a non-frontend API caller renders as ON in switcher.js
        # (`!== false`), so the backend must keep it too -- plain truthiness
        # would silently drop it and make the fan-out count disagree with the
        # UI. image_2's `null` here must NOT drop it.
        node = EPSSwitcher()
        toggles = json.dumps({"image_2": falsy})
        result = node.execute(toggles=toggles, image_1="img1", image_2="img2", image_3="img3")
        assert result[0] == ["img1", "img2", "img3"]

    def test_explicit_boolean_false_is_the_only_disabler(self) -> None:
        node = EPSSwitcher()
        result = node.execute(
            toggles=json.dumps({"image_2": False}), image_1="img1", image_2="img2"
        )
        assert result[0] == ["img1"]

    def test_disconnected_slot_none_is_skipped_even_if_marked_enabled(self) -> None:
        # A gap slot (per FORMAT.md §6.4 growth invariant, a disconnected
        # middle slot can still exist as a key) is None from ComfyUI's own
        # call path -- toggle state is moot for it.
        node = EPSSwitcher()
        result = node.execute(
            toggles=_toggles(image_2=True),
            image_1="img1",
            image_2=None,
            image_3="img3",
        )
        assert result[0] == ["img1", "img3"]

    def test_multiple_disabled_slots_all_omitted(self) -> None:
        node = EPSSwitcher()
        result = node.execute(
            toggles=_toggles(image_1=False, image_3=False),
            image_1="img1",
            image_2="img2",
            image_3="img3",
            image_4="img4",
        )
        assert result[0] == ["img2", "img4"]

    def test_malformed_toggles_json_falls_back_to_all_enabled(self) -> None:
        node = EPSSwitcher()
        result = node.execute(toggles="not json{{", image_1="img1", image_2="img2")
        assert result[0] == ["img1", "img2"]

    def test_toggles_that_is_not_a_json_object_falls_back_to_all_enabled(self) -> None:
        node = EPSSwitcher()
        result = node.execute(toggles="[1, 2, 3]", image_1="img1")
        assert result[0] == ["img1"]


class TestExecuteInputIsListContract:
    """``INPUT_IS_LIST = True`` (nodes_switcher.py module docstring): real
    ComfyUI now wraps every input -- ``toggles`` and each connected
    ``image_N`` -- in a list before ``execute`` ever sees it, and a
    list-producing upstream (``EPSImageGrid``, itself ``OUTPUT_IS_LIST``)
    arrives as its full multi-element list rather than one opaque item.
    These tests exercise that shape directly, distinct from the
    bare-scalar calling convention the older tests above use (still
    tolerated -- see ``execute``'s own comment -- but no longer what real
    ComfyUI actually sends).
    """

    def test_list_wrapped_toggles_is_unwrapped(self) -> None:
        node = EPSSwitcher()
        result = node.execute(toggles=[_toggles()], image_1=["img1"])
        assert result[0] == ["img1"]

    def test_empty_list_toggles_falls_back_to_default(self) -> None:
        # `_unwrap_toggles`'s edge case: a list-wrapped input that's
        # somehow empty rather than holding the one expected string.
        node = EPSSwitcher()
        result = node.execute(toggles=[], image_1=["img1"])
        assert result[0] == ["img1"]

    def test_list_wrapped_malformed_toggles_falls_back_to_all_enabled(self) -> None:
        node = EPSSwitcher()
        result = node.execute(toggles=["not json{{"], image_1=["a"], image_2=["b"])
        assert result[0] == ["a", "b"]

    def test_grid_shaped_upstream_list_merges_element_wise(self) -> None:
        # The bug scenario itself, scaled down: a list-producing upstream
        # (EPS Image Grid) contributes every one of its elements, not one
        # opaque item -- 3 grid tiles + 1 ordinary image = 4 output
        # elements, matching the live-verification "grid(3 collected) +
        # load, both enabled -> 4 downstream runs" case.
        node = EPSSwitcher()
        result = node.execute(
            toggles=[_toggles()],
            image_1=["tile0", "tile1", "tile2"],
            image_2=["loaded"],
        )
        assert result[0] == ["tile0", "tile1", "tile2", "loaded"]

    def test_disabled_grid_shaped_slot_contributes_nothing(self) -> None:
        # The owner-reported bug's fix, scaled down: grid disabled + a
        # single ordinary image enabled must yield exactly that one image,
        # not the grid's elements too and not a repeat per grid element.
        node = EPSSwitcher()
        result = node.execute(
            toggles=[_toggles(image_1=False)],
            image_1=["tile0", "tile1", "tile2"],
            image_2=["loaded"],
        )
        assert result[0] == ["loaded"]

    def test_flatten_preserves_slot_order_and_intra_slot_order(self) -> None:
        # Slot order ascending (image_1, image_2, image_3) regardless of
        # kwarg order, AND each slot's own elements stay in their original
        # order -- "one level of flattening", not a re-sort.
        node = EPSSwitcher()
        result = node.execute(
            toggles=[_toggles()],
            image_3=["c0", "c1"],
            image_1=["a0", "a1", "a2"],
            image_2=["b0"],
        )
        assert result[0] == ["a0", "a1", "a2", "b0", "c0", "c1"]

    def test_single_element_list_batch_stays_one_output_element(self) -> None:
        # An ordinary (non-list-producing) upstream's batch tensor arrives
        # as a length-1 list wrapping ONE value (INPUT_IS_LIST wraps
        # everything); that value -- even though it's itself a stand-in for
        # a [B>1,H,W,C] batch -- must stay ONE output element, not be
        # iterated apart. A plain object (not itself a list/tuple) makes
        # this unambiguous.
        node = EPSSwitcher()
        batch_stand_in = object()
        result = node.execute(toggles=[_toggles()], image_1=[batch_stand_in])
        assert result[0] == [batch_stand_in]

    def test_none_elements_within_a_resolved_list_are_skipped(self) -> None:
        # Defensive tolerance for a partial/misbehaving upstream list (or a
        # lazy "not resolved" placeholder that leaked through) -- None
        # elements are dropped, real ones kept, order preserved.
        node = EPSSwitcher()
        result = node.execute(toggles=[_toggles()], image_1=["tile0", None, "tile2"])
        assert result[0] == ["tile0", "tile2"]

    def test_bare_non_list_image_value_is_tolerated_as_one_element(self) -> None:
        # Real ComfyUI always sends a list here (module docstring); this
        # documents the deliberate fallback for direct callers/tests that
        # skip the wrapping -- every older test above in this file relies
        # on exactly this tolerance.
        node = EPSSwitcher()
        result = node.execute(toggles=_toggles(), image_1="plain_tensor_stand_in")
        assert result[0] == ["plain_tensor_stand_in"]

    def test_all_off_returns_blocker_with_list_shaped_inputs(
        self, fake_execution_blocker: type
    ) -> None:
        # The all-off/blocker path (TestAllOffOrNoneConnectedReturnsAn-
        # ExecutionBlocker below) still applies unchanged when inputs are
        # genuinely list-shaped, including a grid-shaped multi-element slot.
        node = EPSSwitcher()
        result = node.execute(
            toggles=[_toggles(image_1=False, image_2=False)],
            image_1=["tile0", "tile1"],
            image_2=["loaded"],
        )
        assert len(result[0]) == 1
        assert isinstance(result[0][0], fake_execution_blocker)
        assert result[0][0].message is None


class TestAllOffOrNoneConnectedReturnsAnExecutionBlocker:
    """FORMAT.md §6.4 "All-off / none-connected is a VALID state" (owner
    decision 2026-07-20, superseding the v0.14.0 behavior these tests used
    to cover -- a queue-time ``ValueError``): queueing with nothing
    connected, or everything toggled off, must SUCCEED. ``execute`` returns
    a one-element list holding a silent (``message=None``)
    ``ExecutionBlocker`` instead of raising.
    """

    def test_nothing_connected_returns_a_one_element_blocker_list(
        self, fake_execution_blocker: type
    ) -> None:
        node = EPSSwitcher()
        result = node.execute(toggles=_toggles())
        assert isinstance(result, tuple)
        assert len(result) == 2  # images + the pass_masks round's masks tail
        assert isinstance(result[0], list)
        assert len(result[0]) == 1
        blocker = result[0][0]
        assert isinstance(blocker, fake_execution_blocker)
        # `message=None` matters: execution.py's own `execution_block_cb`
        # only broadcasts an `execution_error` websocket event when
        # `.message is not None` -- a message here would turn our graceful
        # skip back into a reported error.
        assert blocker.message is None
        # ...and the masks output is blocked the same way, whatever pass_masks.
        assert len(result[1]) == 1 and isinstance(result[1][0], fake_execution_blocker)

    def test_nothing_connected_logs_at_info_not_warning_or_error(
        self, fake_execution_blocker: type, caplog: pytest.LogCaptureFixture
    ) -> None:
        node = EPSSwitcher()
        with caplog.at_level(logging.INFO, logger="eps_image"):
            node.execute(toggles=_toggles())
        assert any(
            "no image inputs are connected" in record.message for record in caplog.records
        )
        assert all(record.levelno <= logging.INFO for record in caplog.records)

    def test_all_connected_but_toggled_off_returns_a_one_element_blocker_list(
        self, fake_execution_blocker: type
    ) -> None:
        node = EPSSwitcher()
        result = node.execute(
            toggles=_toggles(image_1=False, image_2=False, image_3=False),
            image_1="img1",
            image_2="img2",
            image_3="img3",
        )
        assert len(result[0]) == 1
        assert isinstance(result[0][0], fake_execution_blocker)
        assert result[0][0].message is None

    def test_all_toggled_off_log_names_the_count_and_toggled_off(
        self, fake_execution_blocker: type, caplog: pytest.LogCaptureFixture
    ) -> None:
        node = EPSSwitcher()
        with caplog.at_level(logging.INFO, logger="eps_image"):
            node.execute(
                toggles=_toggles(image_1=False, image_2=False, image_3=False),
                image_1="img1",
                image_2="img2",
                image_3="img3",
            )
        messages = [record.message for record in caplog.records]
        assert any("3 image input" in message for message in messages)
        assert any("toggled off" in message for message in messages)

    def test_only_none_valued_slots_returns_the_nothing_connected_blocker(
        self, fake_execution_blocker: type
    ) -> None:
        node = EPSSwitcher()
        result = node.execute(toggles=_toggles(), image_1=None, image_2=None)
        assert len(result[0]) == 1
        assert isinstance(result[0][0], fake_execution_blocker)

    def test_all_off_does_not_raise(self, fake_execution_blocker: type) -> None:
        # The headline behavior change, asserted directly rather than only
        # via return-value shape: this must not raise ANYTHING.
        node = EPSSwitcher()
        node.execute(toggles=_toggles(image_1=False), image_1="img1")

    def test_missing_fake_execution_blocker_module_surfaces_as_import_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Sanity check on the test fixture's own premise: without a real or
        # faked `comfy_execution.graph` on the path, the lazy import inside
        # the all-off branch fails loudly rather than silently -- confirms
        # the other tests above are genuinely exercising that import, not
        # accidentally passing because ExecutionBlocker was already
        # importable some other way. The autouse fake is REMOVED here (it
        # is what the rest of this file now relies on); a real ComfyUI on
        # the path would still satisfy the import, so that case skips.
        import importlib.util
        import sys as _sys

        monkeypatch.delitem(_sys.modules, "comfy_execution.graph", raising=False)
        monkeypatch.delitem(_sys.modules, "comfy_execution", raising=False)
        if importlib.util.find_spec("comfy_execution") is not None:
            pytest.skip("comfy_execution is genuinely importable in this environment")
        node = EPSSwitcher()
        with pytest.raises(ModuleNotFoundError):
            node.execute(toggles=_toggles())


# ------------------------------------------------------------ check_lazy_status


class TestCheckLazyStatus:
    """``EPSSwitcher.check_lazy_status`` (nodes_switcher.py): the actual
    branch-skip -- a name this method never returns is never requested by
    ComfyUI, so that slot's upstream is never added to the execution graph
    and never runs. Called through the SAME ``INPUT_IS_LIST``-gated
    dispatch as ``execute``, so kwargs are shaped the same way: ``toggles``
    list-wrapped (or bare, both tolerated -- same as ``execute``), each
    connected ``image_N`` either its resolved value or the ``(None,)``
    "not resolved yet" placeholder ComfyUI uses on an ``INPUT_IS_LIST``
    node, and an unconnected slot simply absent.
    """

    def test_enabled_connected_slots_are_requested_in_ascending_order(self) -> None:
        node = EPSSwitcher()
        result = node.check_lazy_status(toggles=[_toggles()], image_1=(None,), image_2=(None,))
        assert result == ["image_1", "image_2"]

    def test_disabled_connected_slot_is_not_requested(self) -> None:
        node = EPSSwitcher()
        result = node.check_lazy_status(
            toggles=[_toggles(image_1=False)], image_1=(None,), image_2=(None,)
        )
        assert result == ["image_2"]

    def test_all_disabled_requests_nothing(self) -> None:
        node = EPSSwitcher()
        result = node.check_lazy_status(
            toggles=[_toggles(image_1=False, image_2=False)],
            image_1=(None,),
            image_2=(None,),
        )
        assert result == []

    def test_unconnected_slot_is_never_requested(self) -> None:
        # image_2 simply isn't passed at all -- exactly how ComfyUI omits a
        # genuinely unconnected optional socket (same tolerance `execute`
        # relies on).
        node = EPSSwitcher()
        result = node.check_lazy_status(toggles=[_toggles()], image_1=(None,))
        assert result == ["image_1"]

    def test_nothing_connected_requests_nothing(self) -> None:
        node = EPSSwitcher()
        result = node.check_lazy_status(toggles=[_toggles()])
        assert result == []

    def test_already_resolved_enabled_slot_is_still_named(self) -> None:
        # Harmless and expected per ComfyUI's own contract
        # (comfy.comfy_types.node_typing.CheckLazyMixin's docstring:
        # "executed repeatedly until it returns an empty list, or all
        # requested items were already evaluated") -- core's own filter
        # drops an already-resolved name from what actually gets
        # re-requested; this method doesn't need to track resolution state
        # itself.
        node = EPSSwitcher()
        result = node.check_lazy_status(toggles=[_toggles()], image_1=["already_resolved"])
        assert result == ["image_1"]

    def test_bare_toggles_string_is_tolerated(self) -> None:
        node = EPSSwitcher()
        result = node.check_lazy_status(toggles=_toggles(image_1=False), image_1=(None,))
        assert result == []

    def test_malformed_toggles_falls_back_to_all_requested(self) -> None:
        node = EPSSwitcher()
        result = node.check_lazy_status(
            toggles=["not json{{"], image_1=(None,), image_2=(None,)
        )
        assert result == ["image_1", "image_2"]

    def test_disconnected_none_valued_slot_is_not_requested(self) -> None:
        # Same "bare None means not connected" tolerance as execute's
        # _connected_image_indices -- a gap slot some call path passes as
        # a literal None rather than omitting.
        node = EPSSwitcher()
        result = node.check_lazy_status(toggles=[_toggles()], image_1=(None,), image_2=None)
        assert result == ["image_1"]


# --------------------------------------------------------- class shape / spec


class TestClassShapeMatchesFormatMdSection6_4:
    def test_category(self) -> None:
        assert EPSSwitcher.CATEGORY == "EPSNodes/Switchers"

    def test_return_types_is_images_plus_the_masks_tail(self) -> None:
        # §8 tail append (pass_masks round): `images` is still slot 0, so
        # every saved wire stays put; `masks` rides behind it.
        assert EPSSwitcher.RETURN_TYPES == ("IMAGE", "MASK")
        assert EPSSwitcher.RETURN_NAMES == ("images", "masks")
        assert len(EPSSwitcher.OUTPUT_TOOLTIPS) == 2
        assert all(isinstance(tip, str) and tip for tip in EPSSwitcher.OUTPUT_TOOLTIPS)

    def test_output_is_list_flagged_true(self) -> None:
        assert EPSSwitcher.OUTPUT_IS_LIST == (True, True)

    def test_input_is_list_flagged_true(self) -> None:
        # The Part A fix itself: without this, a list-producing upstream
        # (EPS Image Grid) makes ComfyUI re-run this node once per upstream
        # element instead of merging it element-wise (module docstring).
        assert EPSSwitcher.INPUT_IS_LIST is True

    def test_check_lazy_status_is_defined_and_callable(self) -> None:
        # Presence alone is what activates ComfyUI's lazy-evaluation path
        # for a non-V3 node (execution.py: `getattr(obj, "check_lazy_status",
        # None) is not None`) -- no base class or decorator needed.
        assert callable(getattr(EPSSwitcher(), "check_lazy_status", None))

    def test_function_name_matches_the_declared_entry_point(self) -> None:
        assert EPSSwitcher.FUNCTION == "execute"
        assert callable(getattr(EPSSwitcher(), EPSSwitcher.FUNCTION))

    def test_execute_return_shape_is_a_two_tuple_of_lists(self) -> None:
        result = EPSSwitcher().execute(toggles=_toggles(), image_1="img1")
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], list)
        assert isinstance(result[1], list)


# --------------------------------------------------------------- no ComfyUI import


def test_module_never_imports_comfy_or_torch() -> None:
    assert "comfy" not in nodes_switcher.__dict__
    assert "torch" not in nodes_switcher.__dict__
    source = inspect.getsource(sys.modules[nodes_switcher.__name__])
    assert "import comfy" not in source
    assert "import torch" not in source


class TestUpstreamEmptySwitcherIsNotRequested:
    """2026-07-26 owner report: "if one of them has all of the inputs
    unchecked the entire workflow won't run. Even if it's earlier in the
    workflow than the second one."

    Root cause (reproduced live): core blocks a node when ANY element of ANY
    of its inputs is an ExecutionBlocker, so an all-off switcher vetoed a
    DOWNSTREAM switcher that had its own perfectly good enabled image.

    The fix is on the CONSUMER, through the lazy mechanism: never request a
    slot whose upstream is a provably-empty sibling switcher, so that
    switcher never runs and no blocker is ever created. (Emitting a bare []
    from the empty switcher instead was tried and reverted -- it made the
    output graph-dependent while the cache key is input-only, and a cached []
    crashed a later graph. See the module's own notes.)
    """

    @staticmethod
    def _prompt(upstream_toggles: str, *, upstream_wired: bool = True) -> dict:
        upstream_inputs: dict = {"toggles": upstream_toggles}
        if upstream_wired:
            upstream_inputs["image_1"] = ["1", 0]
        return {
            "1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
            "2": {"class_type": "EPSSwitcher", "inputs": upstream_inputs},
            "3": {"class_type": "LoadImage", "inputs": {"image": "b.png"}},
            # node 4 = the node under test: slot 1 from the switcher, slot 2 direct
            "4": {"class_type": "EPSSwitcher",
                  "inputs": {"image_1": ["2", 0], "image_2": ["3", 0], "toggles": "{}"}},
        }

    def _request(self, prompt: dict) -> list[str]:
        return EPSSwitcher().check_lazy_status(
            toggles=["{}"], prompt=[prompt], unique_id=["4"],
            image_1=(None,), image_2=(None,),
        )

    def test_slot_fed_by_an_all_off_switcher_is_not_requested(self) -> None:
        requested = self._request(self._prompt('{"image_1": false}'))
        assert requested == ["image_2"]

    def test_slot_fed_by_a_switcher_with_nothing_wired_is_not_requested(self) -> None:
        requested = self._request(self._prompt("{}", upstream_wired=False))
        assert requested == ["image_2"]

    def test_slot_fed_by_a_LIVE_switcher_is_still_requested(self) -> None:
        requested = self._request(self._prompt("{}"))
        assert requested == ["image_1", "image_2"]

    def test_partially_off_upstream_is_still_requested(self) -> None:
        prompt = self._prompt('{"image_1": false}')
        prompt["2"]["inputs"]["image_2"] = ["3", 0]  # a second, ENABLED slot upstream
        assert self._request(prompt) == ["image_1", "image_2"]

    def test_non_switcher_upstream_is_never_skipped(self) -> None:
        prompt = self._prompt("{}")
        prompt["2"] = {"class_type": "EPSImageGrid", "inputs": {"mode": "Emit"}}
        assert self._request(prompt) == ["image_1", "image_2"]

    def test_wired_toggles_upstream_is_treated_as_unknown(self) -> None:
        # toggles arriving as a LINK can't be evaluated statically -> assume
        # the upstream produces something (pre-fix behavior).
        prompt = self._prompt("{}")
        prompt["2"]["inputs"]["toggles"] = ["9", 0]
        assert self._request(prompt) == ["image_1", "image_2"]

    @pytest.mark.parametrize(
        "kwargs",
        [
            {},
            {"prompt": [None], "unique_id": ["4"]},
            {"prompt": [{"4": {}}], "unique_id": [None]},
        ],
    )
    def test_uninspectable_graph_requests_everything_enabled(self, kwargs: dict) -> None:
        requested = EPSSwitcher().check_lazy_status(
            toggles=["{}"], image_1=(None,), image_2=(None,), **kwargs
        )
        assert requested == ["image_1", "image_2"]

    def test_an_unrequested_slot_contributes_nothing_at_execute(self) -> None:
        # Core hands an unresolved lazy input the (None,) placeholder; it must
        # neither crash nor contribute, and the other slot must still flow.
        assert EPSSwitcher().execute(
            toggles=["{}"], image_1=(None,), image_2=["live"]
        )[0] == ["live"]

    def test_all_off_still_returns_a_blocker_unconditionally(
        self, fake_execution_blocker: type
    ) -> None:
        # The reverted approach would have returned [] here for a tolerant
        # consumer; it must not, at any graph.
        prompt = {
            "4": {"class_type": "EPSSwitcher", "inputs": {"toggles": '{"image_1": false}'}},
            "5": {"class_type": "EPSSwitcher", "inputs": {"image_1": ["4", 0]}},
        }
        images, _masks = EPSSwitcher().execute(
            toggles=['{"image_1": false}'], prompt=[prompt], unique_id=["4"], image_1=["img"]
        )
        assert len(images) == 1 and isinstance(images[0], fake_execution_blocker)


class TestHiddenInputsDeclared:
    def test_prompt_and_unique_id_are_declared_hidden(self) -> None:
        spec = EPSSwitcher.INPUT_TYPES()
        assert spec["hidden"] == {"prompt": "PROMPT", "unique_id": "UNIQUE_ID"}


EPSModelSwitcher = nodes_switcher.EPSModelSwitcher
EPSClipSwitcher = nodes_switcher.EPSClipSwitcher
EPSVaeSwitcher = nodes_switcher.EPSVaeSwitcher
EPSAudioSwitcher = nodes_switcher.EPSAudioSwitcher


class TestModelHighLowPairsV0660:
    """§6.4 WAN pairing (v0.66.0, owner: "models like WAN that have more
    than one model (high and low version)"; two stages exactly, named
    high/low, one row toggle governing both)."""

    def _node(self):
        return EPSModelSwitcher()

    def test_model_switcher_declares_the_low_tail_output(self) -> None:
        assert EPSModelSwitcher.RETURN_TYPES == ("MODEL", "MODEL")
        assert EPSModelSwitcher.RETURN_NAMES == ("models", "models_low")
        assert EPSModelSwitcher.OUTPUT_IS_LIST == (True, True)
        # ...and the sibling classes are untouched single-output nodes.
        assert EPSClipSwitcher.RETURN_TYPES == ("CLIP",)
        assert EPSVaeSwitcher.RETURN_TYPES == ("VAE",)
        assert EPSAudioSwitcher.RETURN_TYPES == ("AUDIO",)

    def test_input_types_accepts_low_slots(self) -> None:
        optional = EPSModelSwitcher.INPUT_TYPES()["optional"]
        assert "model_3_low" in optional
        io_type, options = optional["model_3_low"]
        assert io_type == "MODEL"
        assert options["lazy"] is True
        assert "LOW-noise" in options["tooltip"]
        # the high pattern never swallows a low name
        assert "model_3" in optional

    def test_paired_rows_stay_index_aligned(self, fake_execution_blocker) -> None:
        node = self._node()
        highs, lows = node.execute(
            toggles=["{}"],
            model_1=["high-A"],
            model_1_low=["low-A"],
            model_2=["high-B"],
            model_2_low=["low-B"],
        )
        assert highs == ["high-A", "high-B"]
        assert lows == ["low-A", "low-B"]

    def test_row_toggle_governs_both_and_lazy_skips_both(self, fake_execution_blocker) -> None:
        node = self._node()
        toggles = ['{"model_1": false}']
        highs, lows = node.execute(
            toggles=toggles,
            model_1=["high-A"],
            model_1_low=["low-A"],
            model_2=["high-B"],
            model_2_low=["low-B"],
        )
        assert highs == ["high-B"]
        assert lows == ["low-B"]
        wanted = node.check_lazy_status(
            toggles=toggles,
            model_1=[None],
            model_1_low=[None],
            model_2=[None],
            model_2_low=[None],
        )
        assert "model_1" not in wanted and "model_1_low" not in wanted
        assert "model_2" in wanted and "model_2_low" in wanted

    def test_low_is_requested_only_when_its_high_is_wanted(
        self, fake_execution_blocker
    ) -> None:
        """Audit 2026-08-21: a low whose high is not connected (or is in the
        lazy skip set -- fed by a statically-all-off sibling) must not be
        requested on its own: its upstream ran for nothing and execute()
        then hit the 0-high/1-low pairing error."""
        node = self._node()
        wanted = node.check_lazy_status(toggles=["{}"], model_1_low=[None])
        assert "model_1_low" not in wanted
        wanted = node.check_lazy_status(toggles=["{}"], model_1=[None], model_1_low=[None])
        assert "model_1" in wanted and "model_1_low" in wanted

    def test_skipped_high_with_a_wired_low_is_skipped_not_an_error(
        self, fake_execution_blocker
    ) -> None:
        """Audit 2026-08-21: a row whose high arrived as None (lazy-skipped
        upstream) pairs nothing -- it is skipped, never the misleading
        "0 high model(s) with 1 low model(s)" ValueError."""
        node = self._node()
        highs, lows = node.execute(
            toggles=["{}"],
            model_1=[None],
            model_1_low=["low-A"],
            model_2=["high-B"],
            model_2_low=["low-B"],
        )
        assert highs == ["high-B"]
        assert lows == ["low-B"]

    def test_missing_low_blocks_only_the_low_output(self, fake_execution_blocker) -> None:
        from comfy_execution.graph import ExecutionBlocker

        node = self._node()
        highs, lows = node.execute(
            toggles=["{}"],
            model_1=["high-A"],  # no low wired on this row
            model_2=["high-B"],
            model_2_low=["low-B"],
        )
        assert highs == ["high-A", "high-B"]
        assert isinstance(lows[0], ExecutionBlocker)
        assert lows[1] == "low-B"

    def test_single_low_broadcasts_across_a_list_producing_high(
        self, fake_execution_blocker
    ) -> None:
        node = self._node()
        highs, lows = node.execute(
            toggles=["{}"],
            model_1=["h1", "h2", "h3"],
            model_1_low=["lo"],
        )
        assert highs == ["h1", "h2", "h3"]
        assert lows == ["lo", "lo", "lo"]

    def test_length_disagreement_fails_loudly_naming_the_slot(
        self, fake_execution_blocker
    ) -> None:
        node = self._node()
        with pytest.raises(ValueError, match=r"model_1_low"):
            node.execute(
                toggles=["{}"],
                model_1=["h1", "h2", "h3"],
                model_1_low=["l1", "l2"],
            )

    def test_all_off_blocks_both_outputs(self, fake_execution_blocker) -> None:
        from comfy_execution.graph import ExecutionBlocker

        node = self._node()
        highs, lows = node.execute(toggles=['{"model_1": false}'], model_1=["high-A"])
        assert isinstance(highs[0], ExecutionBlocker)
        assert isinstance(lows[0], ExecutionBlocker)


# ------------------------------------------------------------ pass_masks


class _FakeImage:
    """A stand-in for an IMAGE tensor ([B, H, W, C]) carrying just what
    `_zeros_like_mask` touches: `.shape` and `.new_zeros(shape)`. Counts
    the `new_zeros` calls so a test can prove the OFF path allocates
    nothing (a long video batch must never pay for masks nobody asked for)."""

    allocations = 0

    def __init__(self, *shape: int) -> None:
        self.shape = shape

    def new_zeros(self, shape: tuple[int, ...]) -> _FakeMask:
        _FakeImage.allocations += 1
        return _FakeMask(shape)


class _FakeMask:
    def __init__(self, shape: tuple[int, ...]) -> None:
        self.shape = tuple(shape)


class TestPassMasks:
    """§6.4 masks (owner ask 2026-10-31: "an option that is off by default to
    also pass along the mask with every image. the mask and image should
    always be passed together"). The EPSSwitcher row partner: `mask_N`
    under every `image_N`, gated by the visible `pass_masks` BOOLEAN
    widget, with the `masks` tail output index-aligned with `images` BY
    CONSTRUCTION."""

    @pytest.fixture(autouse=True)
    def _reset_allocation_counter(self):
        _FakeImage.allocations = 0

    # ------------------------------------------------------ INPUT_TYPES

    def test_pass_masks_is_a_visible_boolean_widget_off_by_default(self) -> None:
        optional = EPSSwitcher.INPUT_TYPES()["optional"]
        widget_type, options = optional["pass_masks"]
        assert widget_type == "BOOLEAN"
        assert options["default"] is False
        assert isinstance(options["tooltip"], str) and options["tooltip"]
        # A visible widget, NOT a Properties entry, and no on/off labels
        # (they truncate in Nodes 2.0 on frontend 1.52.7).
        assert "hidden" not in options
        assert "label_on" not in options and "label_off" not in options
        # Eager, like toggles: check_lazy_status reads it before anything resolves.
        assert "lazy" not in options
        # ...and in `optional`, so a hand-built /prompt may omit it.
        assert "pass_masks" not in EPSSwitcher.INPUT_TYPES()["required"]

    def test_pass_masks_is_appended_after_toggles(self) -> None:
        # widgets_values restores positionally: an old save's `["{}"]` must
        # land on `toggles`, leaving pass_masks at its False default.
        assert list(EPSSwitcher.INPUT_TYPES()["optional"].keys()) == [
            "image_1", "toggles", "pass_masks",
        ]

    def test_mask_slots_are_accepted_lazy_mask_typed(self) -> None:
        optional = EPSSwitcher.INPUT_TYPES()["optional"]
        assert "mask_1" in optional and "mask_37" in optional
        io_type, options = optional["mask_5"]
        assert io_type == "MASK"
        assert options["lazy"] is True
        assert "mask" in options["tooltip"].lower()
        # never stored as a real key -- the frontend grows every mask socket
        assert "mask_1" not in list(optional.keys())
        # ...and the image pattern never swallows a mask name (or vice versa)
        assert _FlexibleOptionalImageInputs({})["mask_2"][0] == "MASK"
        assert _FlexibleOptionalImageInputs({})["image_2"][0] == "IMAGE"
        assert "mask_" not in optional and "mask_1x" not in optional

    def test_state_registry_declares_pass_masks_as_boolean(self) -> None:
        widgets = EPSSwitcher.EPS_STATE_WIDGETS["widgets"]
        assert widgets["pass_masks"] == {"kind": "boolean"}
        assert widgets["toggles"] == {"kind": "json_object", "key_pattern": r"^image_\d+$"}

    # ------------------------------------------------------------- off

    def test_off_by_default_images_unchanged_masks_blocked_no_zeros(
        self, fake_execution_blocker: type
    ) -> None:
        img = _FakeImage(1, 64, 64, 3)
        images, masks = EPSSwitcher().execute(toggles=["{}"], image_1=[img], image_2=["b"])
        assert images == [img, "b"]
        assert len(masks) == 1 and isinstance(masks[0], fake_execution_blocker)
        assert masks[0].message is None
        assert _FakeImage.allocations == 0

    @pytest.mark.parametrize(
        "off_value", [[False], False, [], [None], [0], [1], ["yes"], ["truthy"], "false", None]
    )
    def test_only_a_real_true_turns_masks_on(
        self, off_value: object, fake_execution_blocker: type
    ) -> None:
        # The old-API prompt shape (`pass_masks` absent) and every non-true
        # value stay OFF; a wired mask is simply ignored in that state.
        node = EPSSwitcher()
        kwargs = {} if off_value is None else {"pass_masks": off_value}
        images, masks = node.execute(toggles=["{}"], image_1=["a"], mask_1=["m"], **kwargs)
        assert images == ["a"]
        assert len(masks) == 1 and isinstance(masks[0], fake_execution_blocker)

    @pytest.mark.parametrize("on_value", [[True], True, ["true"], [" TRUE "]])
    def test_true_and_the_string_true_turn_masks_on(self, on_value: object) -> None:
        images, masks = EPSSwitcher().execute(
            toggles=["{}"], pass_masks=on_value, image_1=["a"], mask_1=["m"]
        )
        assert images == ["a"]
        assert masks == ["m"]

    def test_off_never_requests_a_mask_upstream(self) -> None:
        wanted = EPSSwitcher().check_lazy_status(
            toggles=["{}"], pass_masks=[False], image_1=(None,), mask_1=(None,)
        )
        assert wanted == ["image_1"]

    # -------------------------------------------------------------- on

    def test_equal_lengths_pair_by_index_in_slot_order(self) -> None:
        images, masks = EPSSwitcher().execute(
            toggles=["{}"],
            pass_masks=[True],
            image_2=["b"],
            mask_2=["mb"],
            image_1=["a"],
            mask_1=["ma"],
        )
        assert images == ["a", "b"]
        assert masks == ["ma", "mb"]

    def test_a_wired_mask_is_passed_through_untouched(self) -> None:
        mask = _FakeMask((1, 8, 8))
        _images, masks = EPSSwitcher().execute(
            toggles=["{}"], pass_masks=[True], image_1=[_FakeImage(1, 64, 64, 3)], mask_1=[mask]
        )
        assert masks[0] is mask  # never resized, never copied
        assert _FakeImage.allocations == 0

    def test_list_producing_image_pairs_element_wise_with_a_list_mask(self) -> None:
        # EPS Image Grid (OUTPUT_IS_LIST) into image_1, three masks beside it.
        images, masks = EPSSwitcher().execute(
            toggles=["{}"],
            pass_masks=[True],
            image_1=["t0", "t1", "t2"],
            mask_1=["m0", "m1", "m2"],
            image_2=["loaded"],
            mask_2=["mloaded"],
        )
        assert images == ["t0", "t1", "t2", "loaded"]
        assert masks == ["m0", "m1", "m2", "mloaded"]

    def test_single_mask_broadcasts_across_a_list_producing_image(self) -> None:
        images, masks = EPSSwitcher().execute(
            toggles=["{}"], pass_masks=[True], image_1=["t0", "t1", "t2"], mask_1=["m"]
        )
        assert images == ["t0", "t1", "t2"]
        assert masks == ["m", "m", "m"]

    def test_length_disagreement_fails_loudly_naming_the_row_and_socket(self) -> None:
        with pytest.raises(ValueError, match=r"row 2 .*3 image\(s\) with 2 mask\(s\).*mask_2"):
            EPSSwitcher().execute(
                toggles=["{}"],
                pass_masks=[True],
                image_1=["a"],
                mask_1=["ma"],
                image_2=["t0", "t1", "t2"],
                mask_2=["m0", "m1"],
            )

    def test_unwired_mask_on_an_enabled_row_is_an_all_zero_mask_shaped_like_the_image(
        self,
    ) -> None:
        img = _FakeImage(2, 64, 48, 3)  # [B, H, W, C]
        images, masks = EPSSwitcher().execute(
            toggles=["{}"], pass_masks=[True], image_1=[img], image_2=["b"], mask_2=["mb"]
        )
        assert images == [img, "b"]
        assert isinstance(masks[0], _FakeMask)
        assert masks[0].shape == (2, 64, 48)  # [B, H, W] -- the channel dim dropped
        assert masks[1] == "mb"
        assert _FakeImage.allocations == 1  # one zero mask, for the one unwired row

    def test_wired_but_empty_mask_is_treated_like_an_unwired_one(self) -> None:
        img = _FakeImage(1, 4, 4, 3)
        _images, masks = EPSSwitcher().execute(
            toggles=["{}"], pass_masks=[True], image_1=[img], mask_1=[None]
        )
        assert isinstance(masks[0], _FakeMask) and masks[0].shape == (1, 4, 4)

    def test_masks_stay_index_aligned_with_images_by_construction(self) -> None:
        # Mixed rows -- wired masks, an unwired one, a list-producing image
        # with a broadcast mask, a disabled row in the middle -- and the two
        # outputs still line up one for one.
        img_a = _FakeImage(1, 2, 2, 3)
        images, masks = EPSSwitcher().execute(
            toggles=['{"image_2": false}'],
            pass_masks=[True],
            image_1=[img_a],
            image_2=["skipped"],
            mask_2=["mskipped"],
            image_3=["c0", "c1"],
            mask_3=["mc"],
            image_4=["d"],
            mask_4=["md"],
        )
        assert images == [img_a, "c0", "c1", "d"]
        assert len(masks) == len(images)
        assert masks[0].shape == (1, 2, 2)
        assert masks[1:] == ["mc", "mc", "md"]

    def test_toggled_off_row_runs_neither_image_nor_mask_upstream(self) -> None:
        wanted = EPSSwitcher().check_lazy_status(
            toggles=['{"image_1": false}'],
            pass_masks=[True],
            image_1=(None,),
            mask_1=(None,),
            image_2=(None,),
            mask_2=(None,),
        )
        assert wanted == ["image_2", "mask_2"]

    def test_mask_is_requested_only_when_its_image_is_wanted(self) -> None:
        node = EPSSwitcher()
        # a mask alone (no image on its row) is never requested...
        assert node.check_lazy_status(toggles=["{}"], pass_masks=[True], mask_1=(None,)) == []
        # ...nor is a mask whose image is fed by a statically-all-off sibling
        prompt = {
            "2": {"class_type": "EPSSwitcher", "inputs": {"toggles": '{"image_1": false}',
                                                          "image_1": ["1", 0]}},
            "4": {"class_type": "EPSSwitcher", "inputs": {"image_1": ["2", 0],
                                                          "mask_1": ["2", 1],
                                                          "image_2": ["3", 0]}},
        }
        wanted = node.check_lazy_status(
            toggles=["{}"], pass_masks=[True], prompt=[prompt], unique_id=["4"],
            image_1=(None,), mask_1=(None,), image_2=(None,),
        )
        assert wanted == ["image_2"]

    def test_mask_with_no_image_on_its_row_is_dropped_with_a_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="eps_image"):
            images, masks = EPSSwitcher().execute(
                toggles=["{}"], pass_masks=[True], image_1=["a"], mask_1=["ma"], mask_3=["orphan"]
            )
        assert images == ["a"]
        assert masks == ["ma"]  # the orphan never travels alone
        warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1  # once per execution
        assert "mask_3" in warnings[0] and "never travels alone" in warnings[0]

    def test_mask_on_a_toggled_off_row_is_neither_emitted_nor_an_orphan(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="eps_image"):
            images, masks = EPSSwitcher().execute(
                toggles=['{"image_2": false}'],
                pass_masks=[True],
                image_1=["a"], mask_1=["ma"], image_2=["b"], mask_2=["mb"],
            )
        assert images == ["a"] and masks == ["ma"]
        assert not [r for r in caplog.records if r.levelno == logging.WARNING]

    def test_skipped_image_with_a_wired_mask_is_skipped_not_an_error(self) -> None:
        # The lazy "not resolved" placeholder on the image (fed by an
        # all-off sibling) pairs nothing -- never a "0 image(s) with 1
        # mask(s)" error (the lows' audit-2026-08-21 rule, kept for masks).
        images, masks = EPSSwitcher().execute(
            toggles=["{}"], pass_masks=[True],
            image_1=(None,), mask_1=["m"], image_2=["b"], mask_2=["mb"],
        )
        assert images == ["b"]
        assert masks == ["mb"]

    def test_all_off_blocks_both_outputs(self, fake_execution_blocker: type) -> None:
        images, masks = EPSSwitcher().execute(
            toggles=['{"image_1": false}'], pass_masks=[True], image_1=["a"], mask_1=["m"]
        )
        assert len(images) == 1 and isinstance(images[0], fake_execution_blocker)
        assert len(masks) == 1 and isinstance(masks[0], fake_execution_blocker)

    def test_none_connected_blocks_both_outputs(self, fake_execution_blocker: type) -> None:
        images, masks = EPSSwitcher().execute(toggles=["{}"], pass_masks=[True])
        assert isinstance(images[0], fake_execution_blocker)
        assert isinstance(masks[0], fake_execution_blocker)

    def test_statically_all_off_detection_ignores_mask_keys(self) -> None:
        # A sibling-emptiness scan reads `image_N` wires only: a wired mask
        # on an otherwise all-off switcher does not make it "live".
        node = {
            "class_type": "EPSSwitcher",
            "inputs": {"toggles": '{"image_1": false}', "image_1": ["1", 0],
                       "mask_1": ["1", 1], "pass_masks": True},
        }
        assert nodes_switcher._switcher_is_statically_all_off(node) is True


# ---------------------------------------------------------- all-off, by name (2026-11-05)
class TestAllOffRecognitionByName:
    """The consumer-side half of the §6.4 all-off rule, generalized by input
    NAME so the Run Multiplier / Resolution / Image Grid can leave a slot fed
    by a provably-empty switcher unrequested (owner, 2026-11-05: "if all of
    the toggles are off it should look like nothing is connected")."""

    def test_checkpoint_switcher_with_an_empty_selection_is_statically_all_off(self) -> None:
        from eps_image.nodes_switcher import _switcher_is_statically_all_off as off

        ck = lambda sel: {"class_type": "EPSCheckpointSwitcher", "inputs": {"selection": sel}}  # noqa: E731
        assert off(ck("[]")) is True
        assert off(ck("")) is True
        assert off(ck('[null, ""]')) is True  # no string entry -> loads nothing
        assert off(ck('["a.safetensors"]')) is False
        assert off(ck(["9", 0])) is False  # linked: not statically knowable
        assert off(ck("not json")) is False  # unparseable: not PROVABLY empty
        assert off(ck('{"a": 1}')) is False
        assert off({"class_type": "EPSCheckpointSwitcher"}) is False

    def test_inputs_fed_by_an_all_off_switcher_names_only_the_empty_feeds(self) -> None:
        from eps_image.nodes_switcher import inputs_fed_by_an_all_off_switcher as fed

        prompt = {
            "3": {"class_type": "EPSSwitcher", "inputs": {"image_1": ["1", 0], "toggles": '{"image_1": false}'}},
            "9": {"class_type": "EPSModelSwitcher", "inputs": {"model_1": ["8", 0], "toggles": "{}"}},
            "4": {"class_type": "EPSCheckpointSwitcher", "inputs": {"selection": "[]"}},
            "7": {
                "class_type": "EPSCrossSweep",
                "inputs": {"image": ["3", 0], "model": ["9", 0], "clip": ["4", 1], "text": "x"},
            },
        }
        names = ("model", "model_low", "clip", "vae", "image", "mask")
        assert fed(prompt, "7", names) == {"image", "clip"}
        assert fed(prompt, 7, names) == {"image", "clip"}  # int ids compare as strings
        assert fed(prompt, "7", ("text",)) == set()  # a literal, not a link
        assert fed(prompt, "nope", names) == set()
        assert fed(None, "7", names) == set()
        assert fed(prompt, None, names) == set()

    def test_wired_input_names_keeps_order_and_is_conservative_without_a_prompt(self) -> None:
        from eps_image.nodes_switcher import wired_input_names as wired

        prompt = {"7": {"inputs": {"image": ["3", 0], "model": ["9", 0], "text": "x", "vae": None}}}
        names = ("model", "model_low", "clip", "vae", "image", "mask")
        assert wired(prompt, "7", names) == ["model", "image"]
        assert wired(None, "7", names) == list(names)  # unreadable prompt -> request everything
        assert wired(prompt, "nope", names) == list(names)
