"""Tests for ``eps_image.nodes_save_image`` (FORMAT.md §6.14, provenance
M2): run_info parsing, the derived solo_run widget index, path-id lookup
through subgraph definitions, the baking itself, and a real save round
trip against a fake ``folder_paths`` -- the PNG is read back and its
``workflow``/``prompt``/``eps_run`` chunks checked. Plus the ``image_output``
toggle: off still saves and still reports ``ui.images``, but the result is
the silent blocker (faked in ``sys.modules`` the way tests/test_bypass.py
does), imported lazily and never at module scope."""

from __future__ import annotations

import inspect
import json
import sys
import types
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from eps_image import nodes_save_image as m


class _FakeTensor:
    """Just enough of a torch tensor for save(): .shape, .cpu().numpy()."""

    def __init__(self, array: np.ndarray) -> None:
        self._array = array
        self.shape = array.shape

    def cpu(self) -> _FakeTensor:
        return self

    def numpy(self) -> np.ndarray:
        return self._array


def _image(w: int = 4, h: int = 3) -> _FakeTensor:
    return _FakeTensor(np.linspace(0.0, 1.0, w * h * 3, dtype=np.float32).reshape(h, w, 3))


@pytest.fixture
def fake_folder_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    out = tmp_path / "output"
    out.mkdir()
    module = types.ModuleType("folder_paths")
    module.get_output_directory = lambda: str(out)

    def get_save_image_path(prefix: str, output_dir: str, w: int = 0, h: int = 0):
        import os

        subfolder = os.path.dirname(os.path.normpath(prefix))
        filename = os.path.basename(os.path.normpath(prefix))
        folder = os.path.join(output_dir, subfolder)
        os.makedirs(folder, exist_ok=True)
        return folder, filename, 1, subfolder, prefix

    module.get_save_image_path = get_save_image_path
    monkeypatch.setitem(sys.modules, "folder_paths", module)
    return out


@pytest.fixture
def fake_execution_blocker(monkeypatch: pytest.MonkeyPatch):
    """A fake `comfy_execution.graph.ExecutionBlocker` in `sys.modules`
    (tests/test_bypass.py's fixture of the same name) -- the off path
    imports it lazily from exactly this module path. Returns the fake class
    so tests can `isinstance()` the returned blocker."""

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


class TestParseRunInfo:
    def test_none_blank_and_garbage_degrade_to_none(self) -> None:
        assert m.parse_run_info(None) is None
        assert m.parse_run_info("") is None
        assert m.parse_run_info("not json") is None
        assert m.parse_run_info(json.dumps({"node": "5"})) is None  # no token
        assert m.parse_run_info(json.dumps([1, 2])) is None

    def test_valid_and_list_wrapped(self) -> None:
        raw = json.dumps({"token": "m1_p1", "node": "5"})
        assert m.parse_run_info(raw) == {"token": "m1_p1", "node": "5"}
        assert m.parse_run_info([raw]) == {"token": "m1_p1", "node": "5"}


class TestSoloWidgetIndex:
    def test_is_the_multipliers_tail_widget(self) -> None:
        # [base_folder, pair_mode, sweep_mode, solo_run] -- derived, not typed
        assert m.solo_widget_index() == 3
        assert m._multiplier_widget_defaults() == ["", "multiply", "multiply", ""]


class TestFindNodeInWorkflow:
    def test_root_and_missing(self) -> None:
        wf = {"nodes": [{"id": 5, "type": "EPSCrossSweep"}]}
        assert m.find_node_in_workflow(wf, "5")["id"] == 5
        assert m.find_node_in_workflow(wf, 5)["id"] == 5
        assert m.find_node_in_workflow(wf, "6") is None
        assert m.find_node_in_workflow(None, "5") is None
        assert m.find_node_in_workflow(wf, None) is None

    def test_subgraph_path_through_definitions(self) -> None:
        wf = {
            "nodes": [{"id": 9, "type": "uuid-a"}],
            "definitions": {
                "subgraphs": [
                    {"id": "uuid-a", "nodes": [{"id": 2, "type": "uuid-b"}]},
                    {"id": "uuid-b", "nodes": [{"id": 7, "type": "EPSCrossSweep"}]},
                ]
            },
        }
        assert m.find_node_in_workflow(wf, "9:2:7")["id"] == 7
        assert m.find_node_in_workflow(wf, "9:3") is None
        assert m.find_node_in_workflow(wf, "9:2:8") is None


class TestBakeSolo:
    def test_sets_both_chunks_and_never_mutates_inputs(self) -> None:
        wf = {"nodes": [{"id": 5, "type": "EPSCrossSweep", "widgets_values": ["b", "multiply"]}]}
        pr = {"5": {"class_type": "EPSCrossSweep", "inputs": {"text": ["1", 0], "solo_run": ""}}}
        w2, p2, baked = m.bake_solo(wf, pr, {"token": "m2_i1_t3", "node": "5"})
        assert baked is True
        assert w2["nodes"][0]["widgets_values"] == ["b", "multiply", "multiply", "m2_i1_t3"]
        assert p2["5"]["inputs"]["solo_run"] == "m2_i1_t3"
        assert wf["nodes"][0]["widgets_values"] == ["b", "multiply"]  # untouched
        assert pr["5"]["inputs"]["solo_run"] == ""

    def test_wrong_class_or_missing_node_is_not_baked(self) -> None:
        wf = {"nodes": [{"id": 5, "type": "SaveImage", "widgets_values": ["x"]}]}
        _w, _p, baked = m.bake_solo(wf, {}, {"token": "p1", "node": "5"})
        assert baked is False
        _w, _p, baked = m.bake_solo(wf, {}, {"token": "p1", "node": "77"})
        assert baked is False

    def test_prompt_only_still_counts(self) -> None:
        pr = {"5:3": {"class_type": "EPSCrossSweep", "inputs": {}}}
        _w, p2, baked = m.bake_solo(None, pr, {"token": "t1", "node": "5:3"})
        assert baked is True and p2["5:3"]["inputs"]["solo_run"] == "t1"


class TestSaveRoundTrip:
    def test_inplace_bake_restores_the_hidden_objects(
        self, fake_folder_paths: Path
    ) -> None:
        # v0.80.0 (sweep-performance round): save() now bakes IN PLACE and
        # restores in a finally instead of deep-copying per save. The hidden
        # extra_pnginfo/prompt objects are SHARED across every mapped save()
        # of a queue, so they must come out byte-identical -- while the PNG
        # written mid-block still carries the baked chunks.
        import copy

        node = m.EPSSaveImage()
        workflow = {
            "nodes": [{"id": 5, "type": "EPSCrossSweep", "widgets_values": ["shoot", "multiply"]}]
        }
        prompt = {"5": {"class_type": "EPSCrossSweep", "inputs": {"solo_run": ""}}}
        extra = {"workflow": workflow}
        workflow_before = copy.deepcopy(workflow)
        prompt_before = copy.deepcopy(prompt)
        result = node.save(
            [_image()],
            filename_prefix="restore/check_m1_i1_t1",
            run_info=json.dumps({"token": "m1_i1_t1", "node": "5", "run": 1, "total": 2}),
            prompt=prompt,
            extra_pnginfo=extra,
        )
        assert workflow == workflow_before
        assert prompt == prompt_before
        assert extra == {"workflow": workflow_before}
        saved = result["ui"]["images"][0]
        png = Image.open(fake_folder_paths / saved["subfolder"] / saved["filename"])
        assert json.loads(png.text["prompt"])["5"]["inputs"]["solo_run"] == "m1_i1_t1"
        baked_wf = json.loads(png.text["workflow"])
        assert baked_wf["nodes"][0]["widgets_values"][-1] == "m1_i1_t1"

    def test_baked_chunks_land_in_the_png(self, fake_folder_paths: Path) -> None:
        node = m.EPSSaveImage()
        workflow = {
            "nodes": [{"id": 5, "type": "EPSCrossSweep", "widgets_values": ["shoot", "multiply"]}]
        }
        prompt = {"5": {"class_type": "EPSCrossSweep", "inputs": {"solo_run": ""}}}
        result = node.save(
            [_image()],
            filename_prefix="shoot/lora_0.5/Portrait_m2_i1_t3",
            run_info=json.dumps({"token": "m2_i1_t3", "node": "5", "run": 4, "total": 12}),
            prompt=prompt,
            extra_pnginfo={"workflow": workflow},
        )
        saved = result["ui"]["images"][0]
        assert saved["filename"] == "Portrait_m2_i1_t3_00001_.png"
        assert saved["subfolder"] == "shoot/lora_0.5"
        png = Image.open(fake_folder_paths / saved["subfolder"] / saved["filename"])
        chunks = png.text
        assert json.loads(chunks["prompt"])["5"]["inputs"]["solo_run"] == "m2_i1_t3"
        baked_wf = json.loads(chunks["workflow"])
        values = baked_wf["nodes"][0]["widgets_values"]
        assert values == ["shoot", "multiply", "multiply", "m2_i1_t3"]
        run = json.loads(chunks[m.EPS_RUN_CHUNK])
        assert run["token"] == "m2_i1_t3" and run["baked"] is True and run["run"] == 4
        # the caller's dicts were not mutated
        assert workflow["nodes"][0]["widgets_values"] == ["shoot", "multiply"]
        assert prompt["5"]["inputs"]["solo_run"] == ""

    def test_unwired_run_info_is_plain_save_image(self, fake_folder_paths: Path) -> None:
        node = m.EPSSaveImage()
        workflow = {
            "nodes": [{"id": 5, "type": "EPSCrossSweep", "widgets_values": ["a", "multiply"]}]
        }
        result = node.save(
            [_image()],
            filename_prefix="plain",
            prompt={"5": {}},
            extra_pnginfo={"workflow": workflow},
        )
        png = Image.open(fake_folder_paths / result["ui"]["images"][0]["filename"])
        assert m.EPS_RUN_CHUNK not in png.text
        assert json.loads(png.text["workflow"]) == workflow

    def test_multiplier_missing_from_chunks_saves_with_baked_false(
        self, fake_folder_paths: Path
    ) -> None:
        node = m.EPSSaveImage()
        result = node.save(
            [_image()],
            filename_prefix="orphan",
            run_info=json.dumps({"token": "p1", "node": "99"}),
            prompt={"1": {"class_type": "LoadImage", "inputs": {}}},
            extra_pnginfo={"workflow": {"nodes": []}},
        )
        png = Image.open(fake_folder_paths / result["ui"]["images"][0]["filename"])
        assert json.loads(png.text[m.EPS_RUN_CHUNK])["baked"] is False

    def test_class_shape(self) -> None:
        spec = m.EPSSaveImage.INPUT_TYPES()
        assert list(spec["required"]) == ["images", "filename_prefix"]
        assert list(spec["optional"]) == ["run_info", "image_output"]
        assert spec["optional"]["run_info"][1]["forceInput"] is True
        assert spec["hidden"] == {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"}
        assert m.EPSSaveImage.OUTPUT_NODE is True
        assert m.EPSSaveImage.RETURN_TYPES == ("IMAGE",)


class TestParseImageOutput:
    def test_only_an_explicit_false_is_off(self) -> None:
        assert m.parse_image_output(False) is False
        assert m.parse_image_output("false") is False
        assert m.parse_image_output(" FALSE ") is False
        # 1-element-list-wrapped, the way a list-taking consumer hands it over
        assert m.parse_image_output([False]) is False
        assert m.parse_image_output(["false"]) is False

    def test_anything_else_is_on(self) -> None:
        for raw in (True, None, "", "true", "no", 0, [True], [None]):
            assert m.parse_image_output(raw) is True, raw


class TestImageOutput:
    """The off path (module docstring "image_output"): the files and
    `ui.images` are unchanged, only the result becomes the silent blocker
    that makes core skip the consumers."""

    def test_default_is_on_and_passes_the_images_through(self, fake_folder_paths: Path) -> None:
        images = [_image()]
        result = m.EPSSaveImage().save(images, filename_prefix="on")
        assert result["result"] == (images,)
        assert result["result"][0] is images

    def test_off_still_saves_and_returns_a_silent_blocker(
        self, fake_folder_paths: Path, fake_execution_blocker
    ) -> None:
        on = m.EPSSaveImage().save([_image()], filename_prefix="toggle")
        off = m.EPSSaveImage().save([_image()], filename_prefix="toggle", image_output=False)
        # same ui.images -- the on-node preview does not change
        assert off["ui"] == on["ui"] == {
            "images": [{"filename": "toggle_00001_.png", "subfolder": "", "type": "output"}]
        }
        assert Image.open(fake_folder_paths / "toggle_00001_.png").size == (4, 3)
        assert isinstance(off["result"], tuple) and len(off["result"]) == 1
        blocker = off["result"][0]
        assert isinstance(blocker, fake_execution_blocker)
        assert blocker.message is None  # silent: a string would surface as an error

    @pytest.mark.parametrize("raw", [False, "false", "FALSE", [False], ["false"]])
    def test_false_string_and_list_wrapped_false_mean_off(
        self, raw, fake_folder_paths: Path, fake_execution_blocker
    ) -> None:
        result = m.EPSSaveImage().save([_image()], filename_prefix="t", image_output=raw)
        assert isinstance(result["result"][0], fake_execution_blocker)

    def test_none_means_on(self, fake_folder_paths: Path) -> None:
        images = [_image()]
        result = m.EPSSaveImage().save(images, filename_prefix="t", image_output=None)
        assert result["result"][0] is images

    def test_is_the_last_serialized_widget(self) -> None:
        # [filename_prefix, image_output] -- run_info is forceInput, no slot;
        # a tail widget is the only kind that can ever be appended (§8).
        names = [name for name, _kind, _options in m._iter_widgets(m.EPSSaveImage)]
        assert names == ["filename_prefix", m.IMAGE_OUTPUT_WIDGET]
        assert m.widget_index(m.EPSSaveImage, m.IMAGE_OUTPUT_WIDGET) == 1
        assert m.widget_defaults(m.EPSSaveImage) == ["EPS", True]

    def test_widget_is_an_unlabelled_optional_boolean_default_on(self) -> None:
        kind, options = m.EPSSaveImage.INPUT_TYPES()["optional"][m.IMAGE_OUTPUT_WIDGET]
        assert kind == "BOOLEAN" and options["default"] is True
        # No label_on/label_off: a labelled BOOLEAN truncates under Nodes 2.0.
        assert "label_on" not in options and "label_off" not in options
        assert "skipped" in options["tooltip"]

    def test_state_registry_declares_it_as_boolean(self) -> None:
        widgets = m.EPSSaveImage.EPS_STATE_WIDGETS["widgets"]
        assert widgets[m.IMAGE_OUTPUT_WIDGET] == {"kind": "boolean"}

    def test_description_and_output_tooltip_say_so(self) -> None:
        assert "image_output" in m.EPSSaveImage.DESCRIPTION
        assert "skipped" in m.EPSSaveImage.OUTPUT_TOOLTIPS[0]

    def test_no_module_scope_comfy_execution_import(self) -> None:
        assert "comfy_execution" not in m.__dict__
        assert "ExecutionBlocker" not in m.__dict__
        source = inspect.getsource(m)
        # The one comfy import is lazy: inside save()'s off branch, never at
        # column 0 (tests/test_bypass.py's pin, same shape).
        assert "\nfrom comfy_execution" not in source
        assert "\n                from comfy_execution.graph import ExecutionBlocker" in source
