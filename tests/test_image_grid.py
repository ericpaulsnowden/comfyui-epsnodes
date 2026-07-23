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
        assert await response.json() == {"ok": True, "uuid": VALID_UUID, "refs": []}

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
