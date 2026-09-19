"""Tests for eps_image.nodes_image_grid (FORMAT.md §6.6, `EPSImageGrid`).

``folder_paths`` is faked via ``sys.modules`` (this pack's established
convention, see ``tests/test_image_grid_store.py``'s identical fixture) so
the node's calls into ``image_grid_store`` resolve under a throwaway
``tmp_path``. ``torch``/``PIL``/``numpy`` are real (installed in this dev
environment). ``comfy_execution.graph.ExecutionBlocker`` is faked the same
way ``tests/test_switcher.py`` fakes it for `EPSSwitcher`'s identical
empty-output mechanism.

Also covers ``eps_image.routes_image_grid``'s ``GET /eps_image_grid/list``
and ``POST /eps_image_grid/clone`` routes (the 2026-07-20 bug-fix pair),
through ``routes_image_grid.build_routes()`` and aiohttp's own test client
(``aiohttp_client``, from the ``pytest-aiohttp`` plugin) -- no ComfyUI
needed, mirroring ``tests/test_routes_sets.py``'s ``make_app`` pattern.
"""

from __future__ import annotations

import inspect
import math
import sys
import types
from pathlib import Path

import pytest

pytest.importorskip("torch")

import torch
from aiohttp import web

from eps_image import image_grid_store as store
from eps_image import nodes_image_grid, routes_image_grid
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


@pytest.fixture
async def client(fake_folder_paths: Path, aiohttp_client):
    """A plain aiohttp test client wired to just this module's routes (no
    ComfyUI) -- mirrors test_routes_sets.py's ``make_app``. Depends on
    ``fake_folder_paths`` so every route handler's calls into
    ``image_grid_store`` resolve under the same throwaway ``tmp_path`` a
    test can also poke directly via the ``store`` import above.
    """
    app = web.Application()
    app.add_routes(routes_image_grid.build_routes())
    return await aiohttp_client(app)


VALID_UUID = "a1b2c3d4-e5f6-47a8-9b0c-d1e2f3a4b5c6"
OTHER_VALID_UUID = "11111111-2222-3333-4444-555555555555"


def _make_batch(count: int, height: int = 4, width: int = 6) -> torch.Tensor:
    frames = [torch.full((height, width, 3), (i + 1) / (count + 1)) for i in range(count)]
    return torch.stack(frames, dim=0)


def _node() -> EPSImageGrid:
    return EPSImageGrid()


# --------------------------------------------------------- class shape / spec


class TestClassShapeMatchesFormatMdSection6_6:
    def test_category(self) -> None:
        assert EPSImageGrid.CATEGORY == "EPSNodes/Images"

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
        # v0.98.0: "Collect only" inserted BETWEEN "Collect" and "Emit" --
        # see nodes_image_grid.py's MODES comment for why the position (a
        # pure addition to the option list) is safe for already-saved
        # workflows.
        input_types = EPSImageGrid.INPUT_TYPES()
        mode_type, mode_spec = input_types["required"]["mode"]
        assert mode_type == ["Collect", "Collect only", "Emit"]
        assert mode_spec["default"] == "Collect"

    def test_image_is_optional(self) -> None:
        input_types = EPSImageGrid.INPUT_TYPES()
        # Type-only (not exact-tuple): `image` also carries a UI-facing
        # `tooltip` string (text pass, 2026-07).
        assert input_types["optional"]["image"][0] == "IMAGE"

    def test_grid_uuid_is_optional_string_defaulting_empty(self) -> None:
        # optional (NOT required): a hand-built /prompt that omits it must
        # still run -- see nodes_image_grid.py's DEFAULT_GRID_UUID docstring.
        input_types = EPSImageGrid.INPUT_TYPES()
        assert "grid_uuid" not in input_types["required"]
        widget_type, spec = input_types["optional"]["grid_uuid"]
        assert widget_type == "STRING"
        assert spec["default"] == ""

    def test_focus_is_optional_string_defaulting_empty_and_hidden(self) -> None:
        # Owner ask 2026-08-23. Optional (NOT required) for the same
        # hand-built-/prompt reason as grid_uuid; DEFAULT_FOCUS == "" means
        # "no frame focused" (unchanged Emit behavior).
        input_types = EPSImageGrid.INPUT_TYPES()
        assert "focus" not in input_types["required"]
        widget_type, spec = input_types["optional"]["focus"]
        assert widget_type == "STRING"
        assert spec["default"] == ""
        # "hidden": True is the same key nodes_image_grid.py already
        # documents for grid_uuid as covering the Vue-nodes hide path
        # (`options.hidden`, FORMAT.md §7.5) -- the classic-canvas half
        # (`widget.hidden`) is set by image_grid.js at attach time.
        assert spec["hidden"] is True

    def test_focus_is_the_last_key_in_optional_a_true_tail_append(self) -> None:
        # FORMAT.md §8's tail-only rule for backend widgets: litegraph
        # restores a saved workflow's `widgets_values` POSITIONALLY, so an
        # OLDER save's shorter array (ending at grid_uuid) must still line
        # up with mode/grid_uuid unchanged -- which only holds if `focus`
        # was appended, never inserted, into `optional`. `image` is an
        # IMAGE-typed input (a socket, not a widget) so it doesn't occupy a
        # widgets_values slot at all; grid_uuid and focus, both STRING, do.
        input_types = EPSImageGrid.INPUT_TYPES()
        assert list(input_types["optional"].keys()) == ["image", "grid_uuid", "focus"]


class TestModesConstant:
    """v0.98.0: `MODES`' ORDER is user-facing (it's the combo's on-screen
    option order) -- "Collect only" sits BETWEEN "Collect" and "Emit", per
    the owner ask. A pure addition to the option list, never a reorder of
    the other two, is what keeps an older saved workflow's `mode` value
    (restored by VALUE, not by index) meaning exactly what it always did."""

    def test_order_and_default(self) -> None:
        assert nodes_image_grid.MODES == ["Collect", "Collect only", "Emit"]
        assert nodes_image_grid.MODE_COLLECT == "Collect"
        assert nodes_image_grid.MODE_COLLECT_ONLY == "Collect only"
        assert nodes_image_grid.MODE_EMIT == "Emit"

    def test_input_types_default_is_still_collect(self) -> None:
        input_types = EPSImageGrid.INPUT_TYPES()
        _mode_type, mode_spec = input_types["required"]["mode"]
        assert mode_spec["default"] == nodes_image_grid.MODE_COLLECT


class TestIsChanged:
    def test_returns_nan_with_no_args(self) -> None:
        # No args = the Collect default: appends are a side effect the
        # cache must never skip.
        assert math.isnan(EPSImageGrid.IS_CHANGED())

    def test_collect_two_calls_are_never_equal(self) -> None:
        # NaN != NaN, so ComfyUI's cache can never see two Collect
        # IS_CHANGED results as "the same".
        assert EPSImageGrid.IS_CHANGED(mode="Collect") != EPSImageGrid.IS_CHANGED(
            mode="Collect"
        )

    def test_collect_only_is_also_the_always_changed_sentinel(self) -> None:
        # v0.98.0: Collect only appends exactly like Collect -- the same
        # side-effect-must-never-be-cached-away rationale applies, and
        # `mode != MODE_EMIT` already covers it without a dedicated branch.
        assert math.isnan(EPSImageGrid.IS_CHANGED(mode="Collect only"))
        assert EPSImageGrid.IS_CHANGED(mode="Collect only") != EPSImageGrid.IS_CHANGED(
            mode="Collect only"
        )

    def test_emit_is_the_buffer_state_token_not_nan(self, fake_folder_paths: Path) -> None:
        # v0.80.0: Emit is side-effect-free, so an UNCHANGED buffer must
        # cross-prompt-cache -- the token is the manifest state, stable
        # across calls, and it flips when the buffer changes.
        token1 = EPSImageGrid.IS_CHANGED(
            mode="Emit", image=None, grid_uuid=VALID_UUID, focus="0001.png"
        )
        token2 = EPSImageGrid.IS_CHANGED(
            mode="Emit", image=None, grid_uuid=VALID_UUID, focus="0001.png"
        )
        assert isinstance(token1, str) and token1.startswith("emit:")
        assert token1 == token2

    def test_emit_token_flips_when_the_buffer_changes(self, fake_folder_paths: Path) -> None:
        import torch

        before = EPSImageGrid.IS_CHANGED(mode="Emit", grid_uuid=VALID_UUID)
        store.append_batch(VALID_UUID, torch.zeros((1, 4, 4, 3)))
        after = EPSImageGrid.IS_CHANGED(mode="Emit", grid_uuid=VALID_UUID)
        assert before != after
        # unchanged buffer -> stable again
        assert EPSImageGrid.IS_CHANGED(mode="Emit", grid_uuid=VALID_UUID) == after

    def test_emit_with_a_bogus_uuid_still_returns_a_string(self) -> None:
        token = EPSImageGrid.IS_CHANGED(mode="Emit", grid_uuid="../nope")
        assert isinstance(token, str)
        assert "no-buffer" in token


# -------------------------------------------------------------------- run()


class TestCollectMode:
    """2026-07-22 contract: Collect is a tee -- it appends to the buffer AND
    passes the SAME just-appended frames straight through downstream (never
    the whole buffer -- that would double-count on every second+ Run, the
    owner-reported "10 images -> ends up running the rest of the workflow
    on way more than 10")."""

    def test_appends_and_passes_through_just_the_wired_frames(
        self, fake_folder_paths: Path
    ) -> None:
        node = _node()
        result = node.run(mode="Collect", image=_make_batch(2), grid_uuid=VALID_UUID)
        images, widths, heights = result["result"]
        assert len(images) == 2
        assert len(widths) == 2
        assert len(heights) == 2
        assert len(store.list_refs(VALID_UUID)) == 2  # buffer itself grew too

    def test_second_collect_run_passes_through_only_its_own_frame(
        self, fake_folder_paths: Path
    ) -> None:
        node = _node()
        node.run(mode="Collect", image=_make_batch(2), grid_uuid=VALID_UUID)
        result = node.run(mode="Collect", image=_make_batch(1), grid_uuid=VALID_UUID)
        images, _widths, _heights = result["result"]
        assert len(images) == 1  # THIS run's frame only -- the tee, not a fan-out
        assert len(store.list_refs(VALID_UUID)) == 3  # buffer still grew to 3

    def test_batch_b_greater_than_1_appends_and_passes_through_b_frames(
        self, fake_folder_paths: Path
    ) -> None:
        node = _node()
        result = node.run(mode="Collect", image=_make_batch(5), grid_uuid=VALID_UUID)
        images, _widths, _heights = result["result"]
        assert len(images) == 5
        assert len(store.list_refs(VALID_UUID)) == 5

    def test_collect_with_no_image_wired_and_nothing_buffered_is_blocked(
        self, fake_folder_paths: Path, fake_execution_blocker: type
    ) -> None:
        # Nothing wired -> nothing to pass through -- Collect never falls
        # back to fanning out the (here, empty) buffer.
        node = _node()
        result = node.run(mode="Collect", image=None, grid_uuid=VALID_UUID)
        images, widths, heights = result["result"]
        for lst in (images, widths, heights):
            assert len(lst) == 1
            assert isinstance(lst[0], fake_execution_blocker)

    def test_collect_with_no_image_wired_does_not_append_and_does_not_replay_the_buffer(
        self, fake_folder_paths: Path, fake_execution_blocker: type
    ) -> None:
        node = _node()
        node.run(mode="Collect", image=_make_batch(2), grid_uuid=VALID_UUID)
        result = node.run(mode="Collect", image=None, grid_uuid=VALID_UUID)
        # Nothing wired this run -> nothing to pass through, EVEN THOUGH the
        # buffer already holds 2 -- Collect never fans the buffer out.
        images, widths, heights = result["result"]
        for lst in (images, widths, heights):
            assert len(lst) == 1
            assert isinstance(lst[0], fake_execution_blocker)
        assert len(store.list_refs(VALID_UUID)) == 2  # buffer itself untouched

    @staticmethod
    def _prompt_consuming_me(uid="3"):
        return {
            "9": {"class_type": "PreviewImage", "inputs": {"images": [uid, 0]}},
            uid: {"class_type": "EPSImageGrid", "inputs": {}},
        }

    @staticmethod
    def _fake_prompt_server(monkeypatch):
        """A fake `server.PromptServer` capturing send_sync calls -- the
        v0.52.0 warning path imports it lazily inside run()."""
        import types

        sent = []

        class FakeInstance:
            def send_sync(self, event, payload):
                sent.append((event, payload))

        server_mod = types.ModuleType("server")

        class PromptServer:
            instance = FakeInstance()

        server_mod.PromptServer = PromptServer
        monkeypatch.setitem(sys.modules, "server", server_mod)
        return sent

    def test_collect_unwired_with_consumer_warns_and_skips_nonblocking(
        self, fake_folder_paths: Path, fake_execution_blocker: type,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """v0.52.0 (owner direction 2026-08-03, demoting v0.51.1's hard
        error): a Collect grid may be parked behind a toggled-off switcher
        row, so the queue must NOT fail -- blockers still go out (downstream
        of the grid skips) and a warning event tells the browser why."""
        sent = self._fake_prompt_server(monkeypatch)
        node = _node()
        node.run(mode="Collect", image=_make_batch(2), grid_uuid=VALID_UUID)
        result = node.run(
            mode="Collect", image=None, grid_uuid=VALID_UUID,
            prompt=self._prompt_consuming_me(), unique_id="3",
        )
        for lst in result["result"]:
            assert isinstance(lst[0], fake_execution_blocker)
        assert len(sent) == 1
        event, payload = sent[0]
        assert event == "eps-image-grid-collect-skip"
        assert payload["node"] == "3"
        assert "Emit" in payload["detail"] and "2 image(s)" in payload["detail"]

    def test_collect_unwired_without_consumer_sends_no_event(
        self, fake_folder_paths: Path, fake_execution_blocker: type,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sent = self._fake_prompt_server(monkeypatch)
        node = _node()
        result = node.run(
            mode="Collect", image=None, grid_uuid=VALID_UUID,
            prompt={"5": {"class_type": "X", "inputs": {}}}, unique_id="3",
        )
        for lst in result["result"]:
            assert isinstance(lst[0], fake_execution_blocker)
        assert sent == []

    def test_collect_unwired_without_consumer_keeps_silent_blocker(
        self, fake_folder_paths: Path, fake_execution_blocker: type
    ) -> None:
        node = _node()
        result = node.run(
            mode="Collect", image=None, grid_uuid=VALID_UUID,
            prompt={"5": {"class_type": "X", "inputs": {}}}, unique_id="3",
        )
        for lst in result["result"]:
            assert isinstance(lst[0], fake_execution_blocker)

    def test_emit_empty_with_consumer_stays_a_silent_skip(
        self, fake_folder_paths: Path, fake_execution_blocker: type
    ) -> None:
        # An empty buffer in Emit is a normal transient state, not a miswire.
        node = _node()
        result = node.run(
            mode="Emit", image=None, grid_uuid=VALID_UUID,
            prompt=self._prompt_consuming_me(), unique_id="3",
        )
        for lst in result["result"]:
            assert isinstance(lst[0], fake_execution_blocker)

    def test_emitted_tensors_are_batch_of_one_each(self, fake_folder_paths: Path) -> None:
        node = _node()
        batch = _make_batch(2, height=8, width=10)
        result = node.run(mode="Collect", image=batch, grid_uuid=VALID_UUID)
        images, widths, heights = result["result"]
        for image in images:
            assert image.shape == (1, 8, 10, 3)
        assert widths == [10, 10]
        assert heights == [8, 8]

    def test_passed_through_frames_are_full_precision_slices_of_the_wired_batch(
        self, fake_folder_paths: Path
    ) -> None:
        # The tee must not be reconstructed from the just-written (lossy
        # for anything but this PNG path) disk copy -- it's the SAME
        # per-frame expansion the store's append path uses, handed back
        # directly.
        node = _node()
        batch = _make_batch(3)
        result = node.run(mode="Collect", image=batch, grid_uuid=VALID_UUID)
        images, _widths, _heights = result["result"]
        for i, image in enumerate(images):
            assert torch.equal(image, batch[i : i + 1])


class TestCollectOnlyMode:
    """v0.98.0 owner ask ("let me collect without running the rest of the
    workflow every time"): appends EXACTLY like Collect (same store append,
    same `ui.images` delta so the on-node thumbnail grid updates the same
    way) but NEVER passes anything downstream -- all three outputs are
    always the silent `ExecutionBlocker` triple, wired or not, buffer empty
    or not, and -- unlike plain Collect -- there is no "nothing wired"
    warning: a blocked Run in this mode is the mode working as designed."""

    def test_appends_like_collect_but_all_three_outputs_are_blocked(
        self, fake_folder_paths: Path, fake_execution_blocker: type
    ) -> None:
        node = _node()
        result = node.run(mode="Collect only", image=_make_batch(2), grid_uuid=VALID_UUID)
        images, widths, heights = result["result"]
        for lst in (images, widths, heights):
            assert len(lst) == 1
            assert isinstance(lst[0], fake_execution_blocker)
        assert len(store.list_refs(VALID_UUID)) == 2  # buffer still grew

    def test_batch_b_greater_than_1_appends_all_b_but_still_blocks(
        self, fake_folder_paths: Path, fake_execution_blocker: type
    ) -> None:
        node = _node()
        result = node.run(mode="Collect only", image=_make_batch(5), grid_uuid=VALID_UUID)
        for lst in result["result"]:
            assert len(lst) == 1
            assert isinstance(lst[0], fake_execution_blocker)
        assert len(store.list_refs(VALID_UUID)) == 5

    def test_second_run_ui_images_is_only_the_delta_not_the_whole_buffer(
        self, fake_folder_paths: Path, fake_execution_blocker: type
    ) -> None:
        node = _node()
        node.run(mode="Collect only", image=_make_batch(2), grid_uuid=VALID_UUID)
        result = node.run(mode="Collect only", image=_make_batch(1), grid_uuid=VALID_UUID)
        whole_buffer = store.list_refs(VALID_UUID)
        assert len(whole_buffer) == 3
        assert result["ui"]["images"] == whole_buffer[-1:]  # the delta, same as Collect

    def test_unwired_blocks_and_appends_nothing_and_omits_ui(
        self, fake_folder_paths: Path, fake_execution_blocker: type
    ) -> None:
        node = _node()
        result = node.run(mode="Collect only", image=None, grid_uuid=VALID_UUID)
        for lst in result["result"]:
            assert len(lst) == 1
            assert isinstance(lst[0], fake_execution_blocker)
        assert "ui" not in result
        assert len(store.list_refs(VALID_UUID)) == 0

    def test_unwired_with_something_already_buffered_still_blocks_and_appends_nothing(
        self, fake_folder_paths: Path, fake_execution_blocker: type
    ) -> None:
        node = _node()
        node.run(mode="Collect only", image=_make_batch(2), grid_uuid=VALID_UUID)
        result = node.run(mode="Collect only", image=None, grid_uuid=VALID_UUID)
        for lst in result["result"]:
            assert isinstance(lst[0], fake_execution_blocker)
        assert "ui" not in result
        assert len(store.list_refs(VALID_UUID)) == 2  # untouched

    def test_unwired_with_a_downstream_consumer_sends_no_warning_event(
        self, fake_folder_paths: Path, fake_execution_blocker: type,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Contrast plain Collect's test_collect_unwired_with_consumer_warns_
        # and_skips_nonblocking -- skipping downstream IS the point of this
        # mode, so there is nothing to warn about, consumer or not.
        sent = TestCollectMode._fake_prompt_server(monkeypatch)
        node = _node()
        result = node.run(
            mode="Collect only", image=None, grid_uuid=VALID_UUID,
            prompt=TestCollectMode._prompt_consuming_me(), unique_id="3",
        )
        for lst in result["result"]:
            assert isinstance(lst[0], fake_execution_blocker)
        assert sent == []

    def test_invalid_grid_uuid_appends_nothing_but_still_blocks(
        self, fake_folder_paths: Path, fake_execution_blocker: type
    ) -> None:
        node = _node()
        result = node.run(mode="Collect only", image=_make_batch(2), grid_uuid="not valid!")
        for lst in result["result"]:
            assert isinstance(lst[0], fake_execution_blocker)
        assert "ui" not in result

    def test_ignores_focus_entirely_same_as_collect(
        self, fake_folder_paths: Path, fake_execution_blocker: type
    ) -> None:
        node = _node()
        result = node.run(
            mode="Collect only", image=_make_batch(2), grid_uuid=VALID_UUID, focus="0001.png"
        )
        for lst in result["result"]:
            assert len(lst) == 1
        assert len(store.list_refs(VALID_UUID)) == 2

    def test_emitted_tensors_and_frames_are_full_batch_recorded(
        self, fake_folder_paths: Path, fake_execution_blocker: type
    ) -> None:
        # Confirms the append is the real per-frame expansion (same as
        # Collect), even though nothing of it is ever returned downstream.
        node = _node()
        node.run(
            mode="Collect only",
            image=_make_batch(3, height=8, width=10),
            grid_uuid=VALID_UUID,
        )
        refs = store.list_refs(VALID_UUID)
        assert len(refs) == 3

    def test_collect_and_emit_are_unaffected_by_collect_only_existing(
        self, fake_folder_paths: Path
    ) -> None:
        # Regression guard: Collect only is a separate early-return branch
        # in run() -- this pins that plain Collect/Emit still behave
        # exactly as before once that branch exists.
        node = _node()
        collect_result = node.run(mode="Collect", image=_make_batch(2), grid_uuid=VALID_UUID)
        assert len(collect_result["result"][0]) == 2
        emit_result = node.run(mode="Emit", grid_uuid=VALID_UUID)
        assert len(emit_result["result"][0]) == 2


class TestEmitMode:
    """2026-07-22 contract: Emit fans the WHOLE buffer out (buffer frames
    first, chronological), with whatever's CURRENTLY wired appended at the
    END -- the owner's "10 buffered + 1 wired -> 11"."""

    def test_emits_the_whole_buffer_without_appending(self, fake_folder_paths: Path) -> None:
        node = _node()
        node.run(mode="Collect", image=_make_batch(3), grid_uuid=VALID_UUID)

        result = node.run(mode="Emit", grid_uuid=VALID_UUID)
        images, widths, heights = result["result"]
        assert len(images) == 3
        assert len(widths) == len(heights) == 3

        # Confirm a plain Emit (nothing wired) is idempotent -- a second one
        # sees the same 3, not 3+3.
        again = node.run(mode="Emit", grid_uuid=VALID_UUID)
        assert len(again["result"][0]) == 3

    def test_wired_image_flows_through_appended_at_the_end_owners_10_plus_1(
        self, fake_folder_paths: Path
    ) -> None:
        node = _node()
        node.run(mode="Collect", image=_make_batch(10), grid_uuid=VALID_UUID)

        result = node.run(mode="Emit", image=_make_batch(1), grid_uuid=VALID_UUID)
        images, widths, heights = result["result"]
        assert len(images) == 11
        assert len(widths) == len(heights) == 11

        # And it truly was NOT appended to the buffer -- a follow-up Emit
        # (nothing wired) still sees exactly the original 10.
        again = node.run(mode="Emit", grid_uuid=VALID_UUID)
        assert len(again["result"][0]) == 10

    def test_buffer_frames_come_before_the_wired_frame_in_order(
        self, fake_folder_paths: Path
    ) -> None:
        node = _node()
        node.run(mode="Collect", image=_make_batch(2), grid_uuid=VALID_UUID)  # default 4x6
        wired = _make_batch(1, height=5, width=7)  # distinct shape to identify it
        result = node.run(mode="Emit", image=wired, grid_uuid=VALID_UUID)
        _images, widths, heights = result["result"]
        # First two are the buffered pair (4x6 each), last is the newly-
        # wired frame (5x7) -- chronological first, live newest last.
        assert widths == [6, 6, 7]
        assert heights == [4, 4, 5]

    def test_emit_with_wired_image_never_appends_it(self, fake_folder_paths: Path) -> None:
        node = _node()
        node.run(mode="Emit", image=_make_batch(2), grid_uuid=VALID_UUID)
        # Nothing was ever collected -- a subsequent Collect run should add
        # exactly 2 (from THIS call), not see any leftover from the Emit call.
        node.run(mode="Collect", image=_make_batch(2), grid_uuid=VALID_UUID)
        assert len(store.list_refs(VALID_UUID)) == 2

    def test_emit_with_empty_buffer_and_something_wired_emits_just_the_wired_frames(
        self, fake_folder_paths: Path
    ) -> None:
        node = _node()
        result = node.run(mode="Emit", image=_make_batch(2), grid_uuid=VALID_UUID)
        images, _widths, _heights = result["result"]
        assert len(images) == 2  # buffer(0) + live(2)


class TestFocusedEmit:
    """Owner ask 2026-08-23: "if you have a single image in focus (double
    click to make it large) the widget should only output that one image."
    `focus` names a buffered frame's own on-disk filename (the stable
    per-frame identity `image_grid_store.py`'s `_next_frame_filename` never
    reuses even across a delete -- see nodes_image_grid.py's INPUT_TYPES
    comment for why that beats a bare index)."""

    def test_focus_narrows_emit_to_exactly_that_one_frame(
        self, fake_folder_paths: Path
    ) -> None:
        node = _node()
        node.run(mode="Collect", image=_make_batch(5), grid_uuid=VALID_UUID)
        [target] = [
            r["filename"] for r in store.list_refs(VALID_UUID) if r["filename"] == "0003.png"
        ]
        result = node.run(mode="Emit", grid_uuid=VALID_UUID, focus=target)
        images, widths, heights = result["result"]
        assert len(images) == 1
        assert len(widths) == 1
        assert len(heights) == 1

    def test_focused_frame_is_the_right_one_not_just_any_single_frame(
        self, fake_folder_paths: Path
    ) -> None:
        # Distinct sizes per frame so the emitted one can be identified.
        node = _node()
        node.run(mode="Collect", image=_make_batch(1, height=4, width=4), grid_uuid=VALID_UUID)
        node.run(mode="Collect", image=_make_batch(1, height=9, width=5), grid_uuid=VALID_UUID)
        node.run(mode="Collect", image=_make_batch(1, height=2, width=7), grid_uuid=VALID_UUID)
        result = node.run(mode="Emit", grid_uuid=VALID_UUID, focus="0002.png")
        images, widths, heights = result["result"]
        assert widths == [5]
        assert heights == [9]
        assert images[0].shape == (1, 9, 5, 3)

    def test_focus_drops_the_wired_live_image_too_not_just_the_rest_of_the_buffer(
        self, fake_folder_paths: Path
    ) -> None:
        # "should only output that ONE image" -- not that image plus
        # whatever's currently wired (contrast the unfocused "10 + 1 -> 11"
        # rule in TestEmitMode above).
        node = _node()
        node.run(mode="Collect", image=_make_batch(3), grid_uuid=VALID_UUID)
        result = node.run(
            mode="Emit", image=_make_batch(1), grid_uuid=VALID_UUID, focus="0002.png"
        )
        images, widths, heights = result["result"]
        assert len(images) == len(widths) == len(heights) == 1

    def test_stale_focus_warns_and_emits_the_whole_buffer_instead(
        self, fake_folder_paths: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        node = _node()
        node.run(mode="Collect", image=_make_batch(3), grid_uuid=VALID_UUID)
        with caplog.at_level("WARNING", logger="eps_image"):
            result = node.run(mode="Emit", grid_uuid=VALID_UUID, focus="9999.png")
        images, widths, heights = result["result"]
        assert len(images) == 3  # degraded to the whole buffer, not a crash
        assert len(widths) == len(heights) == 3
        assert any("9999.png" in record.message for record in caplog.records)
        assert any("no longer exists" in record.message for record in caplog.records)

    def test_stale_focus_with_something_wired_still_appends_it_degraded_path(
        self, fake_folder_paths: Path
    ) -> None:
        # The degrade falls all the way back to ordinary unfocused Emit --
        # including its own "buffer + wired" tail, not just the buffer.
        node = _node()
        node.run(mode="Collect", image=_make_batch(2), grid_uuid=VALID_UUID)
        result = node.run(
            mode="Emit", image=_make_batch(1), grid_uuid=VALID_UUID, focus="not-a-real-frame.png"
        )
        images, _widths, _heights = result["result"]
        assert len(images) == 3  # 2 buffered + 1 wired

    def test_stale_focus_on_an_empty_buffer_with_nothing_wired_stays_the_safe_blocker(
        self, fake_folder_paths: Path, fake_execution_blocker: type
    ) -> None:
        # The degrade path re-enters the SAME empty-buffer safety net Emit
        # already has -- never a new crash surface.
        node = _node()
        result = node.run(mode="Emit", grid_uuid=VALID_UUID, focus="0001.png")
        images, widths, heights = result["result"]
        for lst in (images, widths, heights):
            assert len(lst) == 1
            assert isinstance(lst[0], fake_execution_blocker)

    def test_empty_focus_string_is_the_default_unfocused_behavior(
        self, fake_folder_paths: Path
    ) -> None:
        node = _node()
        node.run(mode="Collect", image=_make_batch(3), grid_uuid=VALID_UUID)
        explicit_default = node.run(mode="Emit", grid_uuid=VALID_UUID, focus="")
        omitted = node.run(mode="Emit", grid_uuid=VALID_UUID)
        assert len(explicit_default["result"][0]) == 3
        assert len(omitted["result"][0]) == 3

    def test_collect_mode_ignores_focus_entirely(self, fake_folder_paths: Path) -> None:
        node = _node()
        # A stray/leftover focus value must not affect Collect's tee at all
        # -- not even to trigger the stale-focus warning path.
        result = node.run(
            mode="Collect", image=_make_batch(2), grid_uuid=VALID_UUID, focus="0001.png"
        )
        images, _widths, _heights = result["result"]
        assert len(images) == 2  # unchanged Collect behavior
        assert len(store.list_refs(VALID_UUID)) == 2

    def test_collect_mode_with_stale_focus_does_not_warn(
        self, fake_folder_paths: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        node = _node()
        with caplog.at_level("WARNING", logger="eps_image"):
            node.run(
                mode="Collect", image=_make_batch(1), grid_uuid=VALID_UUID, focus="9999.png"
            )
        assert not any("9999.png" in record.message for record in caplog.records)

    def test_focus_is_a_tracked_input_a_bare_api_caller_omitting_it_still_runs(
        self, fake_folder_paths: Path
    ) -> None:
        # DEFAULT_FOCUS ("") covers a hand-built /prompt that omits `focus`
        # entirely -- same rationale as DEFAULT_GRID_UUID.
        node = _node()
        node.run(mode="Collect", image=_make_batch(2), grid_uuid=VALID_UUID)
        result = node.run(mode="Emit", grid_uuid=VALID_UUID)
        assert len(result["result"][0]) == 2


class TestUiReporting:
    """2026-07-22 owner fix ("10 new + 10 original images in the output
    panel, every run"): `ui.images` reports ONLY refs THIS run appended --
    never the whole buffer -- and is omitted entirely for a run that
    appended nothing (Collect with nothing wired, or an invalid/not-yet-
    minted grid_uuid) or that never appends at all (Emit, unconditionally).
    See module docstring point 1 and FORMAT.md §6.6."""

    def test_collect_first_run_ui_images_is_exactly_the_new_refs(
        self, fake_folder_paths: Path
    ) -> None:
        node = _node()
        result = node.run(mode="Collect", image=_make_batch(2), grid_uuid=VALID_UUID)
        assert result["ui"]["images"] == store.list_refs(VALID_UUID)  # buffer WAS empty

    def test_collect_second_run_ui_images_is_only_the_delta_not_the_whole_buffer(
        self, fake_folder_paths: Path
    ) -> None:
        node = _node()
        node.run(mode="Collect", image=_make_batch(2), grid_uuid=VALID_UUID)
        result = node.run(mode="Collect", image=_make_batch(1), grid_uuid=VALID_UUID)
        whole_buffer = store.list_refs(VALID_UUID)
        assert len(whole_buffer) == 3
        assert len(result["ui"]["images"]) == 1  # NOT 3 -- this is the fix
        assert result["ui"]["images"] == whole_buffer[-1:]

    def test_collect_batch_of_b_reports_all_b_new_refs_not_more(
        self, fake_folder_paths: Path
    ) -> None:
        node = _node()
        node.run(mode="Collect", image=_make_batch(2), grid_uuid=VALID_UUID)
        result = node.run(mode="Collect", image=_make_batch(3), grid_uuid=VALID_UUID)
        assert len(result["ui"]["images"]) == 3
        assert result["ui"]["images"] == store.list_refs(VALID_UUID)[-3:]

    def test_collect_with_nothing_wired_omits_ui_entirely(
        self, fake_folder_paths: Path, fake_execution_blocker: type
    ) -> None:
        node = _node()
        node.run(mode="Collect", image=_make_batch(2), grid_uuid=VALID_UUID)
        result = node.run(mode="Collect", image=None, grid_uuid=VALID_UUID)
        assert "ui" not in result

    def test_collect_with_invalid_grid_uuid_omits_ui_even_though_it_still_passes_through(
        self, fake_folder_paths: Path
    ) -> None:
        # Nothing was actually appended (invalid uuid -> append_batch is a
        # no-op) -- ui must therefore be omitted -- even though the wired
        # image still flows through downstream (module docstring point 2).
        node = _node()
        result = node.run(mode="Collect", image=_make_batch(2), grid_uuid="not valid!")
        assert "ui" not in result
        assert len(result["result"][0]) == 2  # still flows through

    def test_emit_never_reports_ui_even_with_a_full_buffer(self, fake_folder_paths: Path) -> None:
        node = _node()
        node.run(mode="Collect", image=_make_batch(3), grid_uuid=VALID_UUID)
        result = node.run(mode="Emit", grid_uuid=VALID_UUID)
        assert "ui" not in result

    def test_emit_never_reports_ui_with_something_wired_either(
        self, fake_folder_paths: Path
    ) -> None:
        node = _node()
        node.run(mode="Collect", image=_make_batch(3), grid_uuid=VALID_UUID)
        result = node.run(mode="Emit", image=_make_batch(1), grid_uuid=VALID_UUID)
        assert "ui" not in result

    def test_empty_buffer_safety_path_never_reports_ui(
        self, fake_folder_paths: Path, fake_execution_blocker: type
    ) -> None:
        node = _node()
        result = node.run(mode="Emit", image=None, grid_uuid=VALID_UUID)
        assert "ui" not in result


class TestOutputIsListShape:
    def test_three_lists_are_always_equal_length_collect(self, fake_folder_paths: Path) -> None:
        node = _node()
        result = node.run(mode="Collect", image=_make_batch(4), grid_uuid=VALID_UUID)
        images, widths, heights = result["result"]
        assert len(images) == len(widths) == len(heights) == 4

    def test_three_lists_are_always_equal_length_emit(self, fake_folder_paths: Path) -> None:
        node = _node()
        node.run(mode="Collect", image=_make_batch(4), grid_uuid=VALID_UUID)
        result = node.run(mode="Emit", image=_make_batch(2), grid_uuid=VALID_UUID)
        images, widths, heights = result["result"]
        assert len(images) == len(widths) == len(heights) == 6

    def test_result_is_a_three_tuple(self, fake_folder_paths: Path) -> None:
        node = _node()
        result = node.run(mode="Collect", image=_make_batch(1), grid_uuid=VALID_UUID)
        assert isinstance(result["result"], tuple)
        assert len(result["result"]) == 3


class TestReturnShape:
    def test_collect_with_new_images_has_ui_and_result_keys_only(
        self, fake_folder_paths: Path
    ) -> None:
        node = _node()
        result = node.run(mode="Collect", image=_make_batch(1), grid_uuid=VALID_UUID)
        assert set(result.keys()) == {"ui", "result"}

    def test_emit_has_result_key_only_never_ui(self, fake_folder_paths: Path) -> None:
        node = _node()
        node.run(mode="Collect", image=_make_batch(1), grid_uuid=VALID_UUID)
        result = node.run(mode="Emit", grid_uuid=VALID_UUID)
        assert set(result.keys()) == {"result"}

    def test_collect_with_nothing_new_has_result_key_only(
        self, fake_folder_paths: Path, fake_execution_blocker: type
    ) -> None:
        node = _node()
        result = node.run(mode="Collect", image=None, grid_uuid=VALID_UUID)
        assert set(result.keys()) == {"result"}


class TestEmptyBufferSafety:
    """Module docstring "Empty-buffer safety" -- nothing to emit this run
    (Collect: nothing wired; Emit: empty buffer AND nothing wired) must not
    crash a Run. `run()` returns an `ExecutionBlocker(None)` for each of the
    three output slots rather than a bare `[]` (see the module docstring
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

    def test_empty_buffer_omits_ui_entirely_not_a_bare_empty_list(
        self, fake_folder_paths: Path, fake_execution_blocker: type
    ) -> None:
        # 2026-07-22: previously `ui.images` was always present (even as a
        # bare `[]`) -- now the key itself is omitted whenever nothing was
        # appended, Emit included (module docstring point 1).
        node = _node()
        result = node.run(mode="Emit", image=None, grid_uuid=VALID_UUID)
        assert "ui" not in result

    def test_default_missing_grid_uuid_with_nothing_wired_is_blocked(
        self, fake_folder_paths: Path, fake_execution_blocker: type
    ) -> None:
        # A bare API caller who omits BOTH grid_uuid and image gets the
        # node's declared defaults ("" and None) -- nothing wired, nothing
        # buffered (can't be -- "" is invalid) -> the safe blocker triple.
        node = _node()
        result = node.run(mode="Collect")
        images, _widths, _heights = result["result"]
        assert len(images) == 1
        assert isinstance(images[0], fake_execution_blocker)

    def test_default_missing_grid_uuid_with_something_wired_still_flows_through(
        self, fake_folder_paths: Path
    ) -> None:
        # 2026-07-22: flow-through is unconditional -- an invalid/not-yet-
        # minted grid_uuid only means "couldn't actually be recorded", NOT
        # "block the passthrough" (contrast the OLD contract, which blocked
        # this case entirely).
        node = _node()
        result = node.run(mode="Collect", image=_make_batch(2))
        images, widths, heights = result["result"]
        assert len(images) == 2
        assert len(widths) == 2
        assert len(heights) == 2
        assert "ui" not in result  # nothing was actually appended

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


# ------------------------------------------------------- GET /eps_image_grid/list
# (2026-07-20 bug fix: FORMAT.md §6.6 "Display reflects the buffer on LOAD" --
# the frontend's `refreshFromBuffer` calls this on attach/reload/undo.)


class TestListRoute:
    async def test_unknown_but_valid_uuid_returns_an_empty_list(self, client) -> None:
        response = await client.get("/eps_image_grid/list", params={"uuid": VALID_UUID})
        assert response.status == 200
        # `generation` added 2026-07-29 (bulk-add cache token -- see
        # store.buffer_generation): 0 when no manifest exists yet.
        assert await response.json() == {
            "ok": True,
            "uuid": VALID_UUID,
            "refs": [],
            "generation": 0,
        }

    async def test_returns_the_whole_buffer_in_append_order(self, client) -> None:
        store.append_batch(VALID_UUID, _make_batch(3))
        response = await client.get("/eps_image_grid/list", params={"uuid": VALID_UUID})
        body = await response.json()
        assert body["ok"] is True
        assert [r["filename"] for r in body["refs"]] == ["0001.png", "0002.png", "0003.png"]

    async def test_reflects_a_second_append_without_a_second_call_needed_elsewhere(
        self, client
    ) -> None:
        store.append_batch(VALID_UUID, _make_batch(1))
        store.append_batch(VALID_UUID, _make_batch(2))
        response = await client.get("/eps_image_grid/list", params={"uuid": VALID_UUID})
        body = await response.json()
        assert len(body["refs"]) == 3

    async def test_two_uuids_never_share_a_list(self, client) -> None:
        store.append_batch(VALID_UUID, _make_batch(2))
        store.append_batch(OTHER_VALID_UUID, _make_batch(1))
        response = await client.get("/eps_image_grid/list", params={"uuid": OTHER_VALID_UUID})
        body = await response.json()
        assert len(body["refs"]) == 1

    async def test_invalid_uuid_is_400(self, client) -> None:
        response = await client.get("/eps_image_grid/list", params={"uuid": "not valid!"})
        assert response.status == 400
        assert "error" in await response.json()

    async def test_missing_uuid_query_param_is_400(self, client) -> None:
        response = await client.get("/eps_image_grid/list")
        assert response.status == 400

    async def test_never_500s_on_a_malformed_manifest(
        self, client, fake_folder_paths: Path
    ) -> None:
        directory = fake_folder_paths / store.DIRNAME / VALID_UUID
        directory.mkdir(parents=True)
        (directory / store.MANIFEST_FILENAME).write_text("{not valid json")
        response = await client.get("/eps_image_grid/list", params={"uuid": VALID_UUID})
        assert response.status == 200
        assert (await response.json())["refs"] == []


# ------------------------------------------------------ POST /eps_image_grid/clone
# (2026-07-20 bug fix: FORMAT.md §6.6 "Copy carries the images, independently"
# -- the frontend's `ensureUniqueUuid` collision branch calls this right after
# minting a fresh uuid for an in-graph duplicate.)


class TestCloneRoute:
    async def test_clones_the_source_buffer_into_the_destination(self, client) -> None:
        store.append_batch(VALID_UUID, _make_batch(2))
        response = await client.post(
            "/eps_image_grid/clone", json={"from": VALID_UUID, "to": OTHER_VALID_UUID}
        )
        assert response.status == 200
        body = await response.json()
        assert body["ok"] is True
        assert len(body["refs"]) == 2
        assert len(store.list_refs(OTHER_VALID_UUID)) == 2

    async def test_empty_source_returns_ok_with_empty_refs(self, client) -> None:
        response = await client.post(
            "/eps_image_grid/clone", json={"from": VALID_UUID, "to": OTHER_VALID_UUID}
        )
        assert response.status == 200
        assert await response.json() == {"ok": True, "refs": []}

    async def test_clone_is_independent_of_a_later_source_append(self, client) -> None:
        store.append_batch(VALID_UUID, _make_batch(2))
        await client.post(
            "/eps_image_grid/clone", json={"from": VALID_UUID, "to": OTHER_VALID_UUID}
        )
        store.append_batch(VALID_UUID, _make_batch(1))  # source grows to 3 post-clone
        assert len(store.list_refs(VALID_UUID)) == 3
        assert len(store.list_refs(OTHER_VALID_UUID)) == 2  # destination untouched

    async def test_clone_is_independent_of_a_later_source_clear(self, client) -> None:
        store.append_batch(VALID_UUID, _make_batch(2))
        await client.post(
            "/eps_image_grid/clone", json={"from": VALID_UUID, "to": OTHER_VALID_UUID}
        )
        assert store.clear(VALID_UUID) is True
        assert len(store.list_refs(OTHER_VALID_UUID)) == 2  # destination survives

    async def test_invalid_from_uuid_is_400(self, client) -> None:
        response = await client.post(
            "/eps_image_grid/clone", json={"from": "nope!", "to": OTHER_VALID_UUID}
        )
        assert response.status == 400
        assert "error" in await response.json()

    async def test_invalid_to_uuid_is_400(self, client) -> None:
        response = await client.post(
            "/eps_image_grid/clone", json={"from": VALID_UUID, "to": "nope!"}
        )
        assert response.status == 400

    async def test_missing_from_key_is_400(self, client) -> None:
        response = await client.post("/eps_image_grid/clone", json={"to": OTHER_VALID_UUID})
        assert response.status == 400

    async def test_missing_to_key_is_400(self, client) -> None:
        response = await client.post("/eps_image_grid/clone", json={"from": VALID_UUID})
        assert response.status == 400

    async def test_malformed_json_body_is_400(self, client) -> None:
        response = await client.post(
            "/eps_image_grid/clone",
            data="not json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status == 400

    async def test_non_object_body_is_400(self, client) -> None:
        response = await client.post("/eps_image_grid/clone", json=["not", "an", "object"])
        assert response.status == 400
