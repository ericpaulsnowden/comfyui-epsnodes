"""Tests for ``eps_image.nodes_save_image`` (FORMAT.md §6.14, provenance
M2): run_info parsing, the derived solo_run widget index, path-id lookup
through subgraph definitions, the baking itself, and a real save round
trip against a fake ``folder_paths`` -- the PNG is read back and its
``workflow``/``prompt``/``eps_run`` chunks checked."""

from __future__ import annotations

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
        assert baked_wf["nodes"][0]["widgets_values"] == ["shoot", "multiply", "multiply", "m2_i1_t3"]
        run = json.loads(chunks[m.EPS_RUN_CHUNK])
        assert run["token"] == "m2_i1_t3" and run["baked"] is True and run["run"] == 4
        # the caller's dicts were not mutated
        assert workflow["nodes"][0]["widgets_values"] == ["shoot", "multiply"]
        assert prompt["5"]["inputs"]["solo_run"] == ""

    def test_unwired_run_info_is_plain_save_image(self, fake_folder_paths: Path) -> None:
        node = m.EPSSaveImage()
        workflow = {"nodes": [{"id": 5, "type": "EPSCrossSweep", "widgets_values": ["a", "multiply"]}]}
        result = node.save(
            [_image()], filename_prefix="plain", prompt={"5": {}}, extra_pnginfo={"workflow": workflow}
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
        assert list(spec["optional"]) == ["run_info"]
        assert spec["optional"]["run_info"][1]["forceInput"] is True
        assert spec["hidden"] == {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"}
        assert m.EPSSaveImage.OUTPUT_NODE is True
        assert m.EPSSaveImage.RETURN_TYPES == ("IMAGE",)
