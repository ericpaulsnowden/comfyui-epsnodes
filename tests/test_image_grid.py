"""Tests for eps_image.nodes_image_grid (FORMAT.md §6.6, `EPSImageGrid`).

``folder_paths`` is faked via ``sys.modules`` (this pack's established
convention, see ``tests/test_image_grid_store.py``'s identical fixture) so
the node's calls into ``image_grid_store`` resolve under a throwaway
``tmp_path``. ``torch``/``PIL``/``numpy`` are real (installed in this dev
environment). ``comfy_execution.graph.ExecutionBlocker`` is faked the same
way ``tests/test_switcher.py`` fakes it for `EPSSwitcher`'s identical
empty-output mechanism.
"""

from __future__ import annotations

import inspect
import math
import sys
import types
from pathlib import Path

import pytest
import torch

from eps_image import nodes_image_grid
from eps_image.nodes_image_grid import EPSImageGrid


@pytest.fixture
def fake_folder_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    fake_module = types.ModuleType("folder_paths")
    fake_module.get_output_directory = lambda: str(output_dir)
    monkeypatch.setitem(sys.modules, "folder_paths", fake_module)
    return output_dir


@pytest.fixture
def fake_execution_blocker(monkeypatch: pytest.MonkeyPatch):
    """Mirrors tests/test_switcher.py's identically-named fixture -- see its
    docstring. `EPSImageGrid.run`'s empty-buffer path imports
    `ExecutionBlocker` lazily from exactly this module path."""

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


VALID_UUID = "a1b2c3d4-e5f6-47a8-9b0c-d1e2f3a4b5c6"


def _make_batch(count: int, height: int = 4, width: int = 6) -> torch.Tensor:
    frames = [torch.full((height, width, 3), (i + 1) / (count + 1)) for i in range(count)]
    return torch.stack(frames, dim=0)


def _node() -> EPSImageGrid:
    return EPSImageGrid()


# --------------------------------------------------------- class shape / spec


class TestClassShapeMatchesFormatMdSection6_6:
    def test_category(self) -> None:
        assert EPSImageGrid.CATEGORY == "EPSNodes"

    def test_return_types_and_names(self) -> None:
        assert EPSImageGrid.RETURN_TYPES == ("IMAGE", "INT", "INT")
        assert EPSImageGrid.RETURN_NAMES == ("image", "width", "height")

    def test_output_is_list_flagged_true_for_all_three(self) -> None:
        assert EPSImageGrid.OUTPUT_IS_LIST == (True, True, True)

    def test_output_node_is_true(self) -> None:
        assert EPSImageGrid.OUTPUT_NODE is True

    def test_function_name_matches_the_declared_entry_point(self) -> None:
        assert EPSImageGrid.FUNCTION == "run"
        assert callable(getattr(EPSImageGrid(), EPSImageGrid.FUNCTION))


class TestInputTypes:
    def test_mode_combo_is_required_with_collect_default(self) -> None:
        input_types = EPSImageGrid.INPUT_TYPES()
        mode_type, mode_spec = input_types["required"]["mode"]
        assert mode_type == ["Collect", "Emit"]
        assert mode_spec["default"] == "Collect"

    def test_image_is_optional(self) -> None:
        input_types = EPSImageGrid.INPUT_TYPES()
        assert input_types["optional"]["image"] == ("IMAGE",)

    def test_grid_uuid_is_optional_string_defaulting_empty(self) -> None:
        # optional (NOT required): a hand-built /prompt that omits it must
        # still run -- see nodes_image_grid.py's DEFAULT_GRID_UUID docstring.
        input_types = EPSImageGrid.INPUT_TYPES()
        assert "grid_uuid" not in input_types["required"]
        widget_type, spec = input_types["optional"]["grid_uuid"]
        assert widget_type == "STRING"
        assert spec["default"] == ""


class TestIsChanged:
    def test_returns_nan_with_no_args(self) -> None:
        assert math.isnan(EPSImageGrid.IS_CHANGED())

    def test_returns_nan_regardless_of_kwargs(self) -> None:
        assert math.isnan(
            EPSImageGrid.IS_CHANGED(mode="Emit", image=None, grid_uuid=VALID_UUID)
        )

    def test_two_calls_are_never_equal(self) -> None:
        # The whole point: NaN != NaN, so ComfyUI's cache can never see two
        # IS_CHANGED results as "the same".
        assert EPSImageGrid.IS_CHANGED() != EPSImageGrid.IS_CHANGED()


# -------------------------------------------------------------------- run()


class TestCollectMode:
    def test_appends_and_emits_the_whole_buffer(self, fake_folder_paths: Path) -> None:
        node = _node()
        result = node.run(mode="Collect", image=_make_batch(2), grid_uuid=VALID_UUID)
        images, widths, heights = result["result"]
        assert len(images) == 2
        assert len(widths) == 2
        assert len(heights) == 2
        assert len(result["ui"]["images"]) == 2

    def test_second_collect_run_emits_first_plus_second(self, fake_folder_paths: Path) -> None:
        node = _node()
        node.run(mode="Collect", image=_make_batch(2), grid_uuid=VALID_UUID)
        result = node.run(mode="Collect", image=_make_batch(1), grid_uuid=VALID_UUID)
        images, _widths, _heights = result["result"]
        assert len(images) == 3  # whole buffer, not just this run's frame
        assert len(result["ui"]["images"]) == 3

    def test_batch_b_greater_than_1_adds_b_frames_in_one_run(
        self, fake_folder_paths: Path
    ) -> None:
        node = _node()
        result = node.run(mode="Collect", image=_make_batch(5), grid_uuid=VALID_UUID)
        images, _widths, _heights = result["result"]
        assert len(images) == 5

    def test_collect_with_no_image_wired_does_not_append(self, fake_folder_paths: Path) -> None:
        node = _node()
        node.run(mode="Collect", image=_make_batch(2), grid_uuid=VALID_UUID)
        result = node.run(mode="Collect", image=None, grid_uuid=VALID_UUID)
        images, _widths, _heights = result["result"]
        assert len(images) == 2  # unchanged -- nothing to append

    def test_emitted_tensors_are_batch_of_one_each(self, fake_folder_paths: Path) -> None:
        node = _node()
        batch = _make_batch(2, height=8, width=10)
        result = node.run(mode="Collect", image=batch, grid_uuid=VALID_UUID)
        images, widths, heights = result["result"]
        for image in images:
            assert image.shape == (1, 8, 10, 3)
        assert widths == [10, 10]
        assert heights == [8, 8]


class TestEmitMode:
    def test_emits_without_appending(self, fake_folder_paths: Path) -> None:
        node = _node()
        node.run(mode="Collect", image=_make_batch(3), grid_uuid=VALID_UUID)

        result = node.run(mode="Emit", image=_make_batch(4), grid_uuid=VALID_UUID)
        images, _widths, _heights = result["result"]
        assert len(images) == 3  # the 4-frame batch was never appended

        # Confirm it really wasn't appended -- a follow-up Collect run (no
        # new image) should still see exactly 3, not 3+4.
        again = node.run(mode="Collect", image=None, grid_uuid=VALID_UUID)
        assert len(again["result"][0]) == 3

    def test_emit_with_empty_buffer_and_image_wired_still_does_not_append(
        self, fake_folder_paths: Path, fake_execution_blocker: type
    ) -> None:
        node = _node()
        node.run(mode="Emit", image=_make_batch(2), grid_uuid=VALID_UUID)
        # Nothing was ever collected -- a subsequent Collect run should add
        # exactly 2 (from THIS call), not see any leftover from the Emit call.
        result = node.run(mode="Collect", image=_make_batch(2), grid_uuid=VALID_UUID)
        assert len(result["result"][0]) == 2


class TestOutputIsListShape:
    def test_three_lists_are_always_equal_length(self, fake_folder_paths: Path) -> None:
        node = _node()
        node.run(mode="Collect", image=_make_batch(4), grid_uuid=VALID_UUID)
        result = node.run(mode="Emit", grid_uuid=VALID_UUID)
        images, widths, heights = result["result"]
        assert len(images) == len(widths) == len(heights) == 4

    def test_result_is_a_three_tuple(self, fake_folder_paths: Path) -> None:
        node = _node()
        result = node.run(mode="Collect", image=_make_batch(1), grid_uuid=VALID_UUID)
        assert isinstance(result["result"], tuple)
        assert len(result["result"]) == 3


class TestReturnShape:
    def test_return_value_has_ui_and_result_keys_only(self, fake_folder_paths: Path) -> None:
        node = _node()
        result = node.run(mode="Collect", image=_make_batch(1), grid_uuid=VALID_UUID)
        assert set(result.keys()) == {"ui", "result"}

    def test_ui_images_matches_the_on_disk_buffer(self, fake_folder_paths: Path) -> None:
        from eps_image import image_grid_store as store

        node = _node()
        result = node.run(mode="Collect", image=_make_batch(2), grid_uuid=VALID_UUID)
        assert result["ui"]["images"] == store.list_refs(VALID_UUID)


class TestEmptyBufferSafety:
    """Module docstring "Empty-buffer safety" -- a still-empty buffer must
    not crash a Run. `run()` returns an `ExecutionBlocker(None)` for each of
    the three output slots rather than a bare `[]` (see the module docstring
    for the exact IndexError this avoids, traced through this repo's
    `execution.py`)."""

    def test_empty_buffer_returns_one_blocker_per_output_slot(
        self, fake_folder_paths: Path, fake_execution_blocker: type
    ) -> None:
        node = _node()
        result = node.run(mode="Emit", image=None, grid_uuid=VALID_UUID)
        images, widths, heights = result["result"]
        for lst in (images, widths, heights):
            assert len(lst) == 1
            assert isinstance(lst[0], fake_execution_blocker)
            assert lst[0].message is None  # silent block, not a reported error

    def test_empty_buffer_ui_images_is_a_bare_empty_list(
        self, fake_folder_paths: Path, fake_execution_blocker: type
    ) -> None:
        node = _node()
        result = node.run(mode="Emit", image=None, grid_uuid=VALID_UUID)
        assert result["ui"]["images"] == []

    def test_default_missing_grid_uuid_is_also_treated_as_empty(
        self, fake_folder_paths: Path, fake_execution_blocker: type
    ) -> None:
        # A bare API caller who omits grid_uuid entirely gets the node's
        # declared default (""), which the store treats as invalid -- must
        # degrade to the same safe empty-buffer behavior, never raise.
        node = _node()
        result = node.run(mode="Collect", image=_make_batch(2))
        images, _widths, _heights = result["result"]
        assert len(images) == 1
        assert isinstance(images[0], fake_execution_blocker)

    def test_does_not_raise_without_the_execution_blocker_fixture(
        self, fake_folder_paths: Path
    ) -> None:
        # Sanity check on the OTHER tests' premise: without a real or faked
        # comfy_execution.graph on the path, the lazy import must fail
        # loudly (ModuleNotFoundError), confirming those tests are genuinely
        # exercising this path rather than passing by accident.
        if "comfy_execution" in sys.modules or "comfy_execution.graph" in sys.modules:
            pytest.skip("comfy_execution is already importable in this environment")
        node = _node()
        with pytest.raises(ModuleNotFoundError):
            node.run(mode="Emit", image=None, grid_uuid=VALID_UUID)


# --------------------------------------------------------------- no ComfyUI import


def test_module_never_imports_torch_or_comfy_at_module_scope() -> None:
    assert "torch" not in vars(nodes_image_grid)
    assert "comfy" not in vars(nodes_image_grid)
    source = inspect.getsource(sys.modules[nodes_image_grid.__name__])
    assert "import torch" not in source
    assert "import comfy" not in source
