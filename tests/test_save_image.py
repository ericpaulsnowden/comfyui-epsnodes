"""Tests for ``eps_image.nodes_save_image`` (FORMAT.md §6.14, provenance
M2): run_info parsing, the derived solo_run widget index, path-id lookup
through subgraph definitions, the baking itself, and a real save round
trip against a fake ``folder_paths`` -- the PNG is read back and its
``workflow``/``prompt``/``eps_run`` chunks checked."""

from __future__ import annotations

import json
import re
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
        # v1.1.0: preview_only is a TAIL widget, in `optional` like every tail
        # widget this pack has added (a hand-built API prompt that predates it
        # must still validate). run_info is a forceInput socket: no widget slot.
        assert list(spec["optional"]) == ["run_info", "preview_only"]
        assert spec["optional"]["run_info"][1]["forceInput"] is True
        assert spec["hidden"] == {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"}
        assert m.EPSSaveImage.OUTPUT_NODE is True
        assert m.EPSSaveImage.RETURN_TYPES == ("IMAGE",)


# ------------------------------------------------------------ preview only
#
# v1.1.0 (owner request 2026-10-03, FORMAT.md §6.14): the `preview_only`
# toggle turns EPS Save Image into core's PreviewImage. What "behaves like
# PreviewImage" means is read from the rig's ComfyUI `nodes.py`:
#   output_dir = folder_paths.get_temp_directory()
#   type = "temp"
#   prefix_append = "_temp_" + five random lowercase letters
#   compress_level = 1     (SaveImage: 4)
# and PreviewImage embeds the prompt/workflow metadata too (unless
# --disable-metadata), which this node keeps -- plus the provenance bake.

TEMP_NAME_RE = re.compile(r"^(?P<base>.+)_temp_[a-z]{5}_00001_\.png$")


@pytest.fixture
def preview_dirs(fake_folder_paths: Path, tmp_path: Path):
    """``(output_dir, temp_dir, calls)``: the fake ``folder_paths`` extended
    with a temp directory. ``calls`` records which directory getter a save
    asked for, so a test can pin that save mode never touches the temp
    directory and preview mode never touches the output one."""
    temp = tmp_path / "temp"
    temp.mkdir()
    module = sys.modules["folder_paths"]
    calls: list[str] = []
    real_output = module.get_output_directory

    def get_output_directory() -> str:
        calls.append("output")
        return real_output()

    def get_temp_directory() -> str:
        calls.append("temp")
        return str(temp)

    module.get_output_directory = get_output_directory
    module.get_temp_directory = get_temp_directory
    return fake_folder_paths, temp, calls


@pytest.fixture
def save_spy(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Records every ``Image.save`` call's kwargs (and still saves)."""
    seen: list[dict] = []
    original = Image.Image.save

    def spy(self, fp, *args, **kwargs):
        seen.append(dict(kwargs))
        return original(self, fp, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "save", spy)
    return seen


def _run_chunks() -> tuple[dict, dict, str]:
    """``(workflow, prompt, run_info)`` for one Run Multiplier run."""
    workflow = {
        "nodes": [{"id": 5, "type": "EPSCrossSweep", "widgets_values": ["shoot", "multiply"]}]
    }
    prompt = {"5": {"class_type": "EPSCrossSweep", "inputs": {"solo_run": ""}}}
    info = json.dumps({"token": "m2_i1_t3", "node": "5", "run": 4, "total": 12})
    return workflow, prompt, info


class TestParsePreviewOnly:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (True, True),
            (False, False),
            ([True], True),  # a list-taking node sees a 1-element list (_unwrap)
            ([False], False),
            (None, False),  # absent in an older / hand-built API prompt
            (1, True),
            (0, False),
            ("true", True),
            (" TRUE ", True),
            ("false", False),
            ("", False),
            ("preview only", False),  # a label is not a value
            ([], False),
            ([True, True], False),  # not a shape this node ever receives: save, never guess
            ({"x": 1}, False),
        ],
    )
    def test_cases(self, raw, expected) -> None:
        assert m.parse_preview_only(raw) is expected

    def test_garbage_logs_and_saves(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        with caplog.at_level(logging.WARNING, logger="eps_image"):
            assert m.parse_preview_only({"x": 1}) is False
        assert any("preview_only" in r.message for r in caplog.records)


class TestPreviewOnly:
    def test_default_is_the_old_behaviour_and_never_touches_the_temp_dir(
        self, preview_dirs, save_spy: list[dict]
    ) -> None:
        out, temp, calls = preview_dirs
        result = m.EPSSaveImage().save([_image()], filename_prefix="plain")
        saved = result["ui"]["images"][0]
        assert saved == {"filename": "plain_00001_.png", "subfolder": "", "type": "output"}
        assert (out / "plain_00001_.png").is_file()
        assert list(temp.iterdir()) == []
        assert calls == ["output"]
        assert save_spy[0]["compress_level"] == 4

    def test_explicit_false_is_the_same_as_the_default(self, preview_dirs) -> None:
        out, _temp, calls = preview_dirs
        result = m.EPSSaveImage().save([_image()], filename_prefix="plain", preview_only=False)
        assert result["ui"]["images"][0]["type"] == "output"
        assert (out / "plain_00001_.png").is_file()
        assert calls == ["output"]

    def test_preview_writes_to_the_temp_dir_with_type_temp(
        self, preview_dirs, save_spy: list[dict]
    ) -> None:
        out, temp, calls = preview_dirs
        result = m.EPSSaveImage().save([_image()], filename_prefix="look", preview_only=True)
        saved = result["ui"]["images"][0]
        assert saved["type"] == "temp"
        assert saved["subfolder"] == ""
        assert TEMP_NAME_RE.match(saved["filename"]), saved["filename"]
        assert (temp / saved["filename"]).is_file()
        assert list(out.iterdir()) == []  # NOTHING reaches the output folder
        assert calls == ["temp"]  # and the output getter is never even asked
        assert save_spy[0]["compress_level"] == 1

    def test_prefix_stays_the_base_subfolders_and_run_token_survive(self, preview_dirs) -> None:
        out, temp, _calls = preview_dirs
        result = m.EPSSaveImage().save(
            [_image()],
            filename_prefix="shoot/lora_0.5/Portrait_m2_i1_t3",
            preview_only=True,
        )
        saved = result["ui"]["images"][0]
        assert saved["subfolder"] == "shoot/lora_0.5"
        match = TEMP_NAME_RE.match(saved["filename"])
        assert match and match["base"] == "Portrait_m2_i1_t3"
        assert (temp / "shoot" / "lora_0.5" / saved["filename"]).is_file()
        assert not (out / "shoot").exists()

    def test_five_random_lowercase_letters_per_node_instance(self, preview_dirs) -> None:
        # Core's PreviewImage draws them ONCE in __init__ and ComfyUI keeps one
        # instance per node id: every run of a node shares its suffix (so the
        # counter keeps counting up), a different node gets its own.
        def suffix_of(node: m.EPSSaveImage) -> str:
            saved = node.save([_image()], filename_prefix="x", preview_only=True)
            name = saved["ui"]["images"][0]["filename"]
            found = re.search(r"_temp_([a-z]{5})_\d{5}_\.png$", name)
            assert found, saved
            return found[1]

        node = m.EPSSaveImage()
        assert len({suffix_of(node) for _ in range(3)}) == 1
        # 26**5 ~ 11.8M: six identical draws from six nodes would be a bug.
        assert len({suffix_of(m.EPSSaveImage()) for _ in range(6)}) > 1

    def test_a_save_after_a_preview_on_the_same_node_is_a_normal_save(self, preview_dirs) -> None:
        out, _temp, _calls = preview_dirs
        node = m.EPSSaveImage()
        node.save([_image()], filename_prefix="flip", preview_only=True)
        saved = node.save([_image()], filename_prefix="flip", preview_only=False)["ui"]["images"][0]
        assert saved == {"filename": "flip_00001_.png", "subfolder": "", "type": "output"}
        assert (out / "flip_00001_.png").is_file()

    def test_list_wrapped_boolean_previews(self, preview_dirs) -> None:
        out, temp, _calls = preview_dirs
        node = m.EPSSaveImage()
        on = node.save([_image()], filename_prefix="w", preview_only=[True])["ui"]["images"][0]
        assert on["type"] == "temp" and (temp / on["filename"]).is_file()
        off = node.save([_image()], filename_prefix="w", preview_only=[False])["ui"]["images"][0]
        assert off["type"] == "output" and (out / off["filename"]).is_file()

    def test_unrecognised_value_saves_rather_than_silently_previewing(
        self, preview_dirs
    ) -> None:
        _out, temp, _calls = preview_dirs
        result = m.EPSSaveImage().save([_image()], filename_prefix="odd", preview_only="maybe")
        assert result["ui"]["images"][0]["type"] == "output"
        assert list(temp.iterdir()) == []

    def test_images_pass_through_in_both_modes(self, preview_dirs) -> None:
        images = [_image(), _image(5, 2)]
        node = m.EPSSaveImage()
        for flag in (False, True):
            result = node.save(images, filename_prefix="batch", preview_only=flag)
            assert result["result"] == (images,)
            assert len(result["ui"]["images"]) == 2

    def test_provenance_bake_and_metadata_survive_in_preview(self, preview_dirs) -> None:
        # Core PreviewImage embeds prompt + workflow; this node also bakes the
        # run's solo_run and writes the eps_run chunk -- a dropped preview must
        # recreate its run exactly like a saved file does.
        _out, temp, _calls = preview_dirs
        workflow, prompt, info = _run_chunks()
        result = m.EPSSaveImage().save(
            [_image()],
            filename_prefix="shoot/Portrait_m2_i1_t3",
            run_info=info,
            prompt=prompt,
            extra_pnginfo={"workflow": workflow},
            preview_only=True,
        )
        saved = result["ui"]["images"][0]
        assert saved["type"] == "temp"
        png = Image.open(temp / saved["subfolder"] / saved["filename"])
        chunks = png.text
        assert json.loads(chunks["prompt"])["5"]["inputs"]["solo_run"] == "m2_i1_t3"
        values = json.loads(chunks["workflow"])["nodes"][0]["widgets_values"]
        assert values == ["shoot", "multiply", "multiply", "m2_i1_t3"]
        run = json.loads(chunks[m.EPS_RUN_CHUNK])
        assert run["token"] == "m2_i1_t3" and run["baked"] is True and run["run"] == 4

    def test_unwired_run_info_preview_is_plain_preview_image_metadata(self, preview_dirs) -> None:
        _out, temp, _calls = preview_dirs
        workflow, prompt, _info = _run_chunks()
        result = m.EPSSaveImage().save(
            [_image()],
            filename_prefix="plain",
            prompt=prompt,
            extra_pnginfo={"workflow": workflow},
            preview_only=True,
        )
        png = Image.open(temp / result["ui"]["images"][0]["filename"])
        assert m.EPS_RUN_CHUNK not in png.text
        assert json.loads(png.text["workflow"]) == workflow
        assert json.loads(png.text["prompt"]) == prompt

    def test_shared_hidden_objects_come_out_untouched_in_preview(self, preview_dirs) -> None:
        # The v0.80.0 in-place bake + undo contract holds in preview mode too.
        import copy

        workflow, prompt, info = _run_chunks()
        extra = {"workflow": workflow}
        before = copy.deepcopy((workflow, prompt, extra))
        m.EPSSaveImage().save(
            [_image()],
            filename_prefix="restore",
            run_info=info,
            prompt=prompt,
            extra_pnginfo=extra,
            preview_only=True,
        )
        assert (workflow, prompt, extra) == before

    def test_disable_metadata_is_honoured_in_preview(
        self, preview_dirs, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Same flag core PreviewImage obeys (`args.disable_metadata`).
        cli_args = types.ModuleType("comfy.cli_args")
        cli_args.args = types.SimpleNamespace(disable_metadata=True)
        monkeypatch.setitem(sys.modules, "comfy", types.ModuleType("comfy"))
        monkeypatch.setitem(sys.modules, "comfy.cli_args", cli_args)
        _out, temp, _calls = preview_dirs
        workflow, prompt, info = _run_chunks()
        result = m.EPSSaveImage().save(
            [_image()],
            filename_prefix="quiet",
            run_info=info,
            prompt=prompt,
            extra_pnginfo={"workflow": workflow},
            preview_only=True,
        )
        png = Image.open(temp / result["ui"]["images"][0]["filename"])
        assert png.text == {}


class TestPreviewOnlyWidgetShape:
    def test_boolean_default_off_with_no_custom_labels(self) -> None:
        kind, options = m.EPSSaveImage.INPUT_TYPES()["optional"]["preview_only"]
        assert kind == "BOOLEAN"
        assert options["default"] is False
        # Rig 2026-10-03: with label_on/label_off Nodes 2.0 draws a two-
        # segment control whose 69 px halves truncate "preview only" (88 px)
        # at the default node width -- a plain switch named preview_only is
        # what stays readable in both renderers.
        assert "label_on" not in options
        assert "label_off" not in options
        assert "preview" in options["tooltip"].lower()

    def test_is_the_tail_widget_after_filename_prefix(self) -> None:
        # widgets_values restores POSITIONALLY (FORMAT.md §8): filename_prefix
        # keeps index 0, preview_only is appended at 1; the `run_info`
        # forceInput socket and the `images` socket hold no slot.
        names = [name for name, _kind, _options in m._iter_widgets(m.EPSSaveImage)]
        assert names == ["filename_prefix", "preview_only"]
        assert m.widget_index(m.EPSSaveImage, "filename_prefix") == 0
        assert m.widget_index(m.EPSSaveImage, "preview_only") == 1
        assert m.widget_defaults(m.EPSSaveImage) == ["EPS", False]

    @staticmethod
    def _restore(widget_names: list[str], defaults: list, saved: list) -> dict:
        """What the frontend does: positional zip, defaults for what's missing,
        extra trailing values ignored."""
        values = dict(zip(widget_names, defaults, strict=True))
        values.update(zip(widget_names, saved, strict=False))
        return values

    def test_an_old_workflow_loads_with_preview_off(self) -> None:
        restored = self._restore(
            ["filename_prefix", "preview_only"], m.widget_defaults(m.EPSSaveImage), ["EPS"]
        )
        assert restored == {"filename_prefix": "EPS", "preview_only": False}

    def test_a_new_workflow_loads_on_an_older_build(self) -> None:
        # An older EPS build has only filename_prefix; the saved ["EPS", True]
        # keeps its prefix and the trailing extra value is ignored.
        restored = self._restore(["filename_prefix"], ["EPS"], ["EPS", True])
        assert restored == {"filename_prefix": "EPS"}

    def test_a_short_saved_array_pads_to_the_real_index(self) -> None:
        # The baking helper pads a short widgets_values with the class's
        # defaults before writing, so it lands at its real index for the
        # two-widget layout (index 1) too.
        workflow = {"nodes": [{"id": 3, "type": "EPSSaveImage", "widgets_values": []}]}
        landed = m._bake_widget(
            workflow, None, 3, m.EPSSaveImage, "EPSSaveImage", m.PREVIEW_WIDGET, True
        )
        assert landed is True
        assert workflow["nodes"][0]["widgets_values"] == ["EPS", True]

    def test_description_and_output_tooltip_mention_the_toggle(self) -> None:
        assert "preview_only" in m.EPSSaveImage.DESCRIPTION
        assert "preview" in m.EPSSaveImage.OUTPUT_TOOLTIPS[0].lower()

    def test_state_registry_declares_it_as_a_boolean(self) -> None:
        widgets = m.EPSSaveImage.EPS_STATE_WIDGETS["widgets"]
        assert widgets["preview_only"] == {"kind": "boolean"}
        assert widgets["filename_prefix"]["kind"] == "string"
