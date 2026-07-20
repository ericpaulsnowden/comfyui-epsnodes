"""Tests for eps_image.nodes_switcher (FORMAT.md §6.4, `EPSSwitcher`).

No ComfyUI/torch anywhere -- unlike lora_library's tests, this node needs no
context fixture (no `set_context`) and no faked `comfy.*` modules; "images"
are plain sentinel objects, since the node never inspects their contents.
"""

from __future__ import annotations

import inspect
import json
import sys

import pytest

from eps_image import nodes_switcher
from eps_image.nodes_switcher import EPSSwitcher, _FlexibleOptionalImageInputs


def _toggles(**overrides: bool) -> str:
    """A `toggles` JSON string, e.g. `_toggles(image_2=False)`."""
    return json.dumps(overrides)


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
        assert optional["image_5"] == ("IMAGE",)

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
        assert input_types["optional"]["image_5"] == ("IMAGE",)

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
        assert result == (["img1"],)

    def test_default_toggles_enables_every_connected_slot(self) -> None:
        # No `toggles` value at all (a plain API caller who never loaded
        # switcher.js) -- module docstring's default-enabled rationale.
        node = EPSSwitcher()
        result = node.execute(image_1="img1", image_2="img2")
        assert result == (["img1", "img2"],)

    def test_collects_in_ascending_n_regardless_of_kwarg_order(self) -> None:
        node = EPSSwitcher()
        result = node.execute(
            toggles=_toggles(),
            image_3="img3",
            image_1="img1",
            image_2="img2",
        )
        assert result == (["img1", "img2", "img3"],)

    def test_disabled_slot_is_omitted(self) -> None:
        node = EPSSwitcher()
        result = node.execute(
            toggles=_toggles(image_2=False),
            image_1="img1",
            image_2="img2",
            image_3="img3",
        )
        assert result == (["img1", "img3"],)

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
        assert result == (["img1", "img2", "img3"],)

    def test_explicit_boolean_false_is_the_only_disabler(self) -> None:
        node = EPSSwitcher()
        result = node.execute(
            toggles=json.dumps({"image_2": False}), image_1="img1", image_2="img2"
        )
        assert result == (["img1"],)

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
        assert result == (["img1", "img3"],)

    def test_multiple_disabled_slots_all_omitted(self) -> None:
        node = EPSSwitcher()
        result = node.execute(
            toggles=_toggles(image_1=False, image_3=False),
            image_1="img1",
            image_2="img2",
            image_3="img3",
            image_4="img4",
        )
        assert result == (["img2", "img4"],)

    def test_malformed_toggles_json_falls_back_to_all_enabled(self) -> None:
        node = EPSSwitcher()
        result = node.execute(toggles="not json{{", image_1="img1", image_2="img2")
        assert result == (["img1", "img2"],)

    def test_toggles_that_is_not_a_json_object_falls_back_to_all_enabled(self) -> None:
        node = EPSSwitcher()
        result = node.execute(toggles="[1, 2, 3]", image_1="img1")
        assert result == (["img1"],)


class TestEnforceAtLeastOneEnabled:
    def test_nothing_connected_raises_naming_no_connections(self) -> None:
        node = EPSSwitcher()
        with pytest.raises(ValueError, match="no image inputs are connected"):
            node.execute(toggles=_toggles())

    def test_all_connected_but_toggled_off_raises_naming_the_count(self) -> None:
        node = EPSSwitcher()
        with pytest.raises(ValueError, match="3 image input"):
            node.execute(
                toggles=_toggles(image_1=False, image_2=False, image_3=False),
                image_1="img1",
                image_2="img2",
                image_3="img3",
            )

    def test_all_toggled_off_error_mentions_toggled_off(self) -> None:
        node = EPSSwitcher()
        with pytest.raises(ValueError, match="toggled off"):
            node.execute(toggles=_toggles(image_1=False), image_1="img1")

    def test_only_none_valued_slots_raises_the_nothing_connected_message(self) -> None:
        node = EPSSwitcher()
        with pytest.raises(ValueError, match="no image inputs are connected"):
            node.execute(toggles=_toggles(), image_1=None, image_2=None)


# --------------------------------------------------------- class shape / spec


class TestClassShapeMatchesFormatMdSection6_4:
    def test_category(self) -> None:
        assert EPSSwitcher.CATEGORY == "EPSNodes"

    def test_return_types_is_a_single_image_output(self) -> None:
        assert EPSSwitcher.RETURN_TYPES == ("IMAGE",)
        assert EPSSwitcher.RETURN_NAMES == ("images",)

    def test_output_is_list_flagged_true(self) -> None:
        assert EPSSwitcher.OUTPUT_IS_LIST == (True,)

    def test_function_name_matches_the_declared_entry_point(self) -> None:
        assert EPSSwitcher.FUNCTION == "execute"
        assert callable(getattr(EPSSwitcher(), EPSSwitcher.FUNCTION))

    def test_execute_return_shape_is_a_one_tuple_of_a_list(self) -> None:
        result = EPSSwitcher().execute(toggles=_toggles(), image_1="img1")
        assert isinstance(result, tuple)
        assert len(result) == 1
        assert isinstance(result[0], list)


# --------------------------------------------------------------- no ComfyUI import


def test_module_never_imports_comfy_or_torch() -> None:
    assert "comfy" not in nodes_switcher.__dict__
    assert "torch" not in nodes_switcher.__dict__
    source = inspect.getsource(sys.modules[nodes_switcher.__name__])
    assert "import comfy" not in source
    assert "import torch" not in source
