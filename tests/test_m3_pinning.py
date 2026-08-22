"""Provenance M3 -- full pinning (FORMAT.md §6.14, roadmap
``docs/ROADMAP-run-provenance.md``): EPS Save Image captures Prompt Notebook
/ Apply LoRA Set pins FROM THE STORES at save time (through the same
helpers the nodes' run paths use) and bakes them into those nodes'
TAIL-appended ``pinned`` / ``pinned_state`` widgets in the workflow chunk
(root AND subgraph path ids) and the prompt chunk, next to the M2 solo
bake; the ``eps_run`` chunk lists the pinned node ids. Store errors skip a
node, never the queue. The node-level pin semantics live in
``test_nodes_notebook.py`` / ``test_nodes_sets.py``; the M2 pins stay in
``test_save_image.py``.

Fakes as the sibling files do: a real ``LibraryContext`` over ``tmp_path``
(conftest), ``folder_paths`` via ``sys.modules``, tensors via a tiny
``.cpu().numpy()`` shim."""

from __future__ import annotations

import json
import logging
import re
import sys
import types
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from eps_image import nodes_save_image as m
from eps_image.nodes_cross_sweep import EPSCrossSweep
from lora_library import nodes_notebook, nodes_sets, sets_store
from lora_library.context import LibraryContext
from lora_library.nodes_notebook import LoraLibraryNotebook
from lora_library.nodes_sets import LoraLibraryApplySet

CAPTURED_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

#: A multiplier run_info as v0.71.0 emits it (M2 keys + the M3 name/text/label).
RUN = {
    "format": 1,
    "token": "m2_i1_t3",
    "node": "5",
    "run": 4,
    "total": 12,
    "steps": 2,
    "pairs": 6,
    "name": "Portrait",
    "text": "portrait text",
    "label": "lora_0.5",
}

NOTEBOOK_MD = "## Portrait\nportrait text\n## Neg\nbad hands\n## Landscape\nwide text\n"


@pytest.fixture(autouse=True)
def _wire(context: LibraryContext):
    context.resolve_lora_path = lambda name: name
    nodes_notebook.set_context(context)
    nodes_sets.set_context(context)
    yield
    nodes_notebook.set_context(None)
    nodes_sets.set_context(None)


@pytest.fixture
def notebook(library_dir: Path) -> Path:
    path = library_dir / "loras.md"
    path.write_text(NOTEBOOK_MD, encoding="utf-8")
    return path


def _save_set(context: LibraryContext, **overrides) -> str:
    payload = {
        "name": "Test Set",
        "loras": [
            {"file": "detailer.safetensors", "on": True, "strength": 0.8, "strength_clip": None},
            {"file": "styles/cinematic.safetensors", "on": False, "strength": 1.0},
            {
                "file": "styles/film_grain.safetensors",
                "on": True,
                "strength": 0.4,
                "strength_clip": 0.6,
            },
        ],
        "trigger_words": "cinematic, detailed",
    }
    payload.update(overrides)
    slug, _ = sets_store.save_set(context, payload)
    return slug


def _save_composite(context: LibraryContext) -> str:
    payload = {
        "format": 2,
        "name": "WAN composite",
        "loaders": [
            {"loras": [{"file": "detailer.safetensors", "on": True, "strength": 0.8}]},
            {"loras": [{"file": "styles/film_grain.safetensors", "on": True, "strength": 0.3}]},
        ],
    }
    slug, _ = sets_store.save_set(context, payload)
    return slug


def _notebook_prompt(entry: str = "Neg\nPortrait", pinned: str = "", **inputs) -> dict:
    return {
        "class_type": "LoraLibraryNotebook",
        "inputs": {"file": "loras.md", "entry": entry, "pinned": pinned, **inputs},
    }


def _apply_set_prompt(slug: str, pinned_state: str = "", **inputs) -> dict:
    return {
        "class_type": "LoraLibraryApplySet",
        "inputs": {
            "set": slug,
            "strength_scale": 1.0,
            "loader_slot": 0,
            "pinned_state": pinned_state,
            **inputs,
        },
    }


class _FakeTensor:
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


# ------------------------------------------------------------ widget index


class TestWidgetIndex:
    def test_derived_for_every_pinnable_class(self) -> None:
        # [base_folder, pair_mode, sweep_mode, solo_run]
        assert m.widget_index(EPSCrossSweep, "solo_run") == 3 == m.solo_widget_index()
        # [file, entry, pinned] -- the M3 tail
        assert m.widget_index(LoraLibraryNotebook, "pinned") == 2
        # [set, strength_scale, loader_slot, pinned_state] -- sockets are not widgets
        assert m.widget_index(LoraLibraryApplySet, "pinned_state") == 3
        assert m.widget_defaults(LoraLibraryNotebook) == ["loras.md", "", ""]
        assert m.widget_defaults(LoraLibraryApplySet) == ["None", 1.0, 0, ""]
        assert m._multiplier_widget_defaults() == m.widget_defaults(EPSCrossSweep)

    def test_sockets_and_force_inputs_are_skipped_and_missing_raises(self) -> None:
        # images = socket, run_info = forceInput -> only filename_prefix serializes
        assert m.widget_index(m.EPSSaveImage, "filename_prefix") == 0
        assert m.widget_defaults(m.EPSSaveImage) == ["EPS"]
        with pytest.raises(RuntimeError, match="nope"):
            m.widget_index(LoraLibraryNotebook, "nope")

    def test_pinnable_class_lookup(self) -> None:
        assert m._pinnable_class("EPSCrossSweep") is EPSCrossSweep
        assert m._pinnable_class("LoraLibraryNotebook") is LoraLibraryNotebook
        assert m._pinnable_class("LoraLibraryApplySet") is LoraLibraryApplySet
        assert m._pinnable_class("KSampler") is None


# --------------------------------------------------------- entry narrowing


ENTRIES = [
    {"name": "Neg", "text": "bad hands"},
    {"name": "Portrait", "text": "portrait text"},
    {"name": "Landscape", "text": "wide text"},
]


class TestSelectThisRunsEntries:
    ENTRIES = ENTRIES

    def test_single_match_by_name(self) -> None:
        assert m.select_this_runs_entries(self.ENTRIES, {"name": "Portrait", "text": None}) == [
            self.ENTRIES[1]
        ]

    def test_single_match_by_text(self) -> None:
        assert m.select_this_runs_entries(self.ENTRIES, {"name": None, "text": "wide text"}) == [
            self.ENTRIES[2]
        ]

    def test_no_match_pins_the_whole_selection(self) -> None:
        # a notebook wired elsewhere (negative prompt, caption source)
        assert (
            m.select_this_runs_entries(self.ENTRIES, {"name": "Other", "text": "other text"})
            == self.ENTRIES
        )
        assert m.select_this_runs_entries(self.ENTRIES, {}) == self.ENTRIES  # M2-era run_info

    def test_ambiguous_match_pins_the_whole_selection(self) -> None:
        twins = [{"name": "A", "text": "same"}, {"name": "B", "text": "same"}]
        assert m.select_this_runs_entries(twins, {"name": None, "text": "same"}) == twins
        # name hits one entry, text hits another -> two candidates -> whole
        assert (
            m.select_this_runs_entries(self.ENTRIES, {"name": "Neg", "text": "wide text"})
            == self.ENTRIES
        )

    def test_null_fields_never_match_empty_strings(self) -> None:
        entries = [{"name": "", "text": ""}, {"name": "B", "text": "b"}]
        assert m.select_this_runs_entries(entries, {"name": None, "text": None}) == entries


# ------------------------------------------------------- capture: notebook


class TestCaptureNotebook:
    def test_single_entry_match_by_name_pins_only_this_runs_entry(self, notebook: Path) -> None:
        pins = m.capture_pins({"7": _notebook_prompt()}, RUN)
        assert list(pins) == ["7"]
        pin = pins["7"]
        assert pin.class_type == "LoraLibraryNotebook" and pin.widget == "pinned"
        data = json.loads(pin.value)
        assert data["format"] == 1
        assert data["entries"] == [{"name": "Portrait", "text": "portrait text"}]
        assert data["source"]["file"] == "loras.md"
        assert data["source"]["token"] == "m2_i1_t3"
        assert CAPTURED_RE.match(data["source"]["captured"])
        # and the node itself reads the pin back exactly
        assert LoraLibraryNotebook().read_entry(
            file="gone.md", entry="whatever", pinned=pin.value
        ) == (["portrait text"], ["Portrait"])

    def test_match_by_text_when_name_is_unwired(self, notebook: Path) -> None:
        run = {**RUN, "name": None, "text": "wide text"}
        pins = m.capture_pins({"7": _notebook_prompt(entry="Neg\nLandscape\nPortrait")}, run)
        assert json.loads(pins["7"].value)["entries"] == [
            {"name": "Landscape", "text": "wide text"}
        ]

    def test_whole_selection_when_nothing_matches(self, notebook: Path) -> None:
        # a negative-prompt notebook: none of its entries is THIS run's text
        run = {**RUN, "name": "SomethingElse", "text": "some other text"}
        pins = m.capture_pins({"7": _notebook_prompt(entry="Neg\nLandscape")}, run)
        assert json.loads(pins["7"].value)["entries"] == [
            {"name": "Neg", "text": "bad hands"},
            {"name": "Landscape", "text": "wide text"},
        ]

    def test_whole_selection_when_ambiguous(self, library_dir: Path) -> None:
        (library_dir / "loras.md").write_text("## A\nsame\n## B\nsame\n", encoding="utf-8")
        run = {**RUN, "name": None, "text": "same"}
        pins = m.capture_pins({"7": _notebook_prompt(entry="A\nB")}, run)
        assert [e["name"] for e in json.loads(pins["7"].value)["entries"]] == ["A", "B"]

    def test_already_pinned_node_keeps_its_pin(self, notebook: Path) -> None:
        original = json.dumps({"format": 1, "entries": [{"name": "Portrait", "text": "OLD"}]})
        pins = m.capture_pins({"7": _notebook_prompt(pinned=original)}, RUN)
        assert pins == {}  # nothing to bake -- the workflow already carries it

    def test_store_errors_skip_the_node_with_a_warning(
        self, library_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="eps_image"):
            # no notebook file at all
            assert m.capture_pins({"7": _notebook_prompt()}, RUN) == {}
            (library_dir / "loras.md").write_text(NOTEBOOK_MD, encoding="utf-8")
            # a selected entry that isn't in the file
            assert m.capture_pins({"7": _notebook_prompt(entry="Ghost")}, RUN) == {}
            # an empty selection
            assert m.capture_pins({"7": _notebook_prompt(entry="")}, RUN) == {}
            # a scheme:// file (resolve_notebook_file raises ValueError)
            bad = _notebook_prompt()
            bad["inputs"]["file"] = "smb://nas/loras.md"
            assert m.capture_pins({"7": bad}, RUN) == {}
        assert sum("Prompt Notebook 7" in r.message for r in caplog.records) == 4

    def test_wired_file_or_entry_is_skipped(self, notebook: Path) -> None:
        wired = _notebook_prompt()
        wired["inputs"]["entry"] = ["3", 0]  # a link, not a value
        assert m.capture_pins({"7": wired}, RUN) == {}

    def test_no_context_is_skipped(self, notebook: Path) -> None:
        nodes_notebook.set_context(None)
        assert m.capture_pins({"7": _notebook_prompt()}, RUN) == {}

    def test_odd_prompt_shapes_and_other_classes_are_ignored(self, notebook: Path) -> None:
        assert m.capture_pins(None, RUN) == {}
        assert m.capture_pins([], RUN) == {}
        prompt = {
            "1": {"class_type": "KSampler", "inputs": {}},
            "2": "junk",
            "3": {"class_type": "LoraLibraryNotebook"},  # no inputs dict
            "7": _notebook_prompt(),
        }
        assert list(m.capture_pins(prompt, RUN)) == ["7"]


# ------------------------------------------------------ capture: apply set


class TestCaptureApplySet:
    def test_pins_the_normalized_set_dict(self, context: LibraryContext) -> None:
        slug = _save_set(context)
        pins = m.capture_pins({"9": _apply_set_prompt(slug)}, RUN)
        pin = pins["9"]
        assert pin.class_type == "LoraLibraryApplySet" and pin.widget == "pinned_state"
        data = json.loads(pin.value)
        assert list(data) == ["format", "slug", "name", "set", "source"]
        assert data["format"] == 1
        assert data["slug"] == slug
        assert data["name"] == "Test Set"
        assert data["set"] == sets_store.load_set(context, slug)  # exactly the load_set shape
        assert data["set"]["format"] == 1
        assert data["source"]["token"] == "m2_i1_t3"
        assert CAPTURED_RE.match(data["source"]["captured"])
        # and the node applies the pin even after the file is deleted
        sets_store.delete_set(context, slug)
        *_, loras_text = LoraLibraryApplySet().apply(set=slug, pinned_state=pin.value)
        assert loras_text == "detailer_0.8 film_grain_0.4_0.6"

    def test_composite_set_pins_the_whole_format_2_dict(self, context: LibraryContext) -> None:
        slug = _save_composite(context)
        data = json.loads(m.capture_pins({"9": _apply_set_prompt(slug)}, RUN)["9"].value)
        assert data["set"]["format"] == 2
        assert len(data["set"]["loaders"]) == 2
        assert data["set"] == sets_store.load_set(context, slug)

    def test_none_missing_unloadable_and_wired_are_skipped(
        self, context: LibraryContext, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="eps_image"):
            assert m.capture_pins({"9": _apply_set_prompt("None")}, RUN) == {}
            assert m.capture_pins({"9": _apply_set_prompt("")}, RUN) == {}
            assert not caplog.records  # "None" is the idle state, not a warning
            assert m.capture_pins({"9": _apply_set_prompt("no-such-set")}, RUN) == {}
            sets_store.set_path(context, "broken").write_text(
                json.dumps({"format": 99, "loras": []}), encoding="utf-8"
            )
            assert m.capture_pins({"9": _apply_set_prompt("broken")}, RUN) == {}
            wired = _apply_set_prompt("x")
            wired["inputs"]["set"] = ["3", 0]
            assert m.capture_pins({"9": wired}, RUN) == {}
        assert sum("Apply LoRA Set 9" in r.message for r in caplog.records) == 2

    def test_already_pinned_node_keeps_its_pin(self, context: LibraryContext) -> None:
        slug = _save_set(context)
        original = json.dumps({"format": 1, "slug": slug, "set": {"loras": []}})
        assert m.capture_pins({"9": _apply_set_prompt(slug, pinned_state=original)}, RUN) == {}

    def test_no_context_is_skipped(self, context: LibraryContext) -> None:
        slug = _save_set(context)
        nodes_sets.set_context(None)
        assert m.capture_pins({"9": _apply_set_prompt(slug)}, RUN) == {}

    def test_both_classes_in_one_prompt(self, context: LibraryContext, notebook: Path) -> None:
        slug = _save_set(context)
        prompt = {
            "5": {"class_type": "EPSCrossSweep", "inputs": {"solo_run": ""}},
            "7": _notebook_prompt(),
            "9:2": _apply_set_prompt(slug),
        }
        pins = m.capture_pins(prompt, RUN)
        assert list(pins) == ["7", "9:2"]


# ------------------------------------------------------------------ baking


def _workflow() -> dict:
    return {
        "nodes": [
            {"id": 5, "type": "EPSCrossSweep", "widgets_values": ["b", "multiply"]},
            {"id": 7, "type": "LoraLibraryNotebook", "widgets_values": ["loras.md", "Portrait"]},
            {"id": 9, "type": "uuid-a"},
        ],
        "definitions": {
            "subgraphs": [
                {
                    "id": "uuid-a",
                    "nodes": [
                        {"id": 2, "type": "LoraLibraryApplySet", "widgets_values": ["my-set", 1.0]}
                    ],
                }
            ]
        },
    }


def _prompt() -> dict:
    return {
        "5": {"class_type": "EPSCrossSweep", "inputs": {"solo_run": ""}},
        "7": {
            "class_type": "LoraLibraryNotebook",
            "inputs": {"file": "loras.md", "entry": "Portrait"},
        },
        "9:2": {"class_type": "LoraLibraryApplySet", "inputs": {"set": "my-set"}},
    }


PINS = {
    "7": m.PinnedWidget("LoraLibraryNotebook", "pinned", "NB-PIN"),
    "9:2": m.PinnedWidget("LoraLibraryApplySet", "pinned_state", "AS-PIN"),
}


class TestBakeProvenance:
    def test_pins_land_at_the_derived_index_in_root_subgraph_and_prompt(self) -> None:
        wf, pr = _workflow(), _prompt()
        w2, p2, baked, pinned = m.bake_provenance(wf, pr, {"token": "m2_i1_t3", "node": "5"}, PINS)
        assert baked is True
        assert pinned == ["7", "9:2"]
        # solo still baked (M2), at its own derived index
        assert w2["nodes"][0]["widgets_values"] == ["b", "multiply", "multiply", "m2_i1_t3"]
        assert p2["5"]["inputs"]["solo_run"] == "m2_i1_t3"
        # notebook at the root: [file, entry, pinned]
        assert w2["nodes"][1]["widgets_values"] == ["loras.md", "Portrait", "NB-PIN"]
        assert p2["7"]["inputs"]["pinned"] == "NB-PIN"
        # apply set inside the subgraph DEFINITION, short array padded with
        # the loader_slot default before the tail: [set, scale, slot, pin]
        sub = w2["definitions"]["subgraphs"][0]["nodes"][0]
        assert sub["widgets_values"] == ["my-set", 1.0, 0, "AS-PIN"]
        assert p2["9:2"]["inputs"]["pinned_state"] == "AS-PIN"
        # the caller's dicts were never mutated
        assert wf == _workflow()
        assert pr == _prompt()

    def test_pin_for_a_node_missing_from_both_chunks_is_not_listed(self) -> None:
        pins = {"42": m.PinnedWidget("LoraLibraryNotebook", "pinned", "X")}
        _w, _p, baked, pinned = m.bake_provenance(
            _workflow(), _prompt(), {"token": "t", "node": "5"}, pins
        )
        assert baked is True and pinned == []

    def test_wrong_class_at_that_id_is_left_alone(self) -> None:
        wf = {"nodes": [{"id": 7, "type": "KSampler", "widgets_values": [1, 2]}]}
        pr = {"7": {"class_type": "KSampler", "inputs": {"seed": 1}}}
        w2, p2, _baked, pinned = m.bake_provenance(wf, pr, {"token": "t", "node": "5"}, PINS)
        assert pinned == []
        assert w2 == wf and p2 == pr

    def test_one_chunk_is_enough(self) -> None:
        # prompt only (no workflow chunk at all)
        info = {"token": "t", "node": "5"}
        _w, p2, _b, pinned = m.bake_provenance(None, _prompt(), info, PINS)
        assert pinned == ["7", "9:2"] and p2["7"]["inputs"]["pinned"] == "NB-PIN"
        # workflow only
        w2, _p, _b, pinned = m.bake_provenance(_workflow(), None, info, PINS)
        assert pinned == ["7", "9:2"] and w2["nodes"][1]["widgets_values"][2] == "NB-PIN"

    def test_pins_bake_even_when_the_multiplier_is_missing(self) -> None:
        _w, _p, baked, pinned = m.bake_provenance(_workflow(), _prompt(), {"token": "t"}, PINS)
        assert baked is False and pinned == ["7", "9:2"]

    def test_unknown_class_pin_is_skipped_with_a_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        pins = {"7": m.PinnedWidget("KSampler", "seed", "1")}
        with caplog.at_level(logging.WARNING, logger="eps_image"):
            _w, _p, _b, pinned = m.bake_provenance(_workflow(), _prompt(), {"token": "t"}, pins)
        assert pinned == []
        assert any("unknown class" in r.message for r in caplog.records)

    def test_bake_solo_is_bake_provenance_without_pins(self) -> None:
        info = {"token": "m2_i1_t3", "node": "5"}
        assert m.bake_solo(_workflow(), _prompt(), info) == m.bake_provenance(
            _workflow(), _prompt(), info, {}
        )[:3]
        assert m.bake_solo(_workflow(), _prompt(), info)[2] is True


# --------------------------------------------------------------- round trip


class TestSaveRoundTrip:
    def test_png_carries_pins_solo_and_the_pinned_id_list(
        self, fake_folder_paths: Path, context: LibraryContext, notebook: Path
    ) -> None:
        slug = _save_set(context)
        workflow = {
            "nodes": [
                {"id": 5, "type": "EPSCrossSweep", "widgets_values": ["shoot", "multiply"]},
                {
                    "id": 7,
                    "type": "LoraLibraryNotebook",
                    "widgets_values": ["loras.md", "Neg\nPortrait"],
                },
                {
                    "id": 9,
                    "type": "LoraLibraryApplySet",
                    "widgets_values": [slug, 1.0, 0, "", "(any)"],
                },
            ]
        }
        prompt = {
            "5": {"class_type": "EPSCrossSweep", "inputs": {"solo_run": ""}},
            "7": _notebook_prompt(),
            "9": _apply_set_prompt(slug),
        }
        result = m.EPSSaveImage().save(
            [_image()],
            filename_prefix="shoot/lora_0.5/Portrait_m2_i1_t3",
            run_info=json.dumps(RUN),
            prompt=prompt,
            extra_pnginfo={"workflow": workflow},
        )
        saved = result["ui"]["images"][0]
        png = Image.open(fake_folder_paths / saved["subfolder"] / saved["filename"])
        chunks = png.text

        baked_wf = json.loads(chunks["workflow"])
        solo_values = baked_wf["nodes"][0]["widgets_values"]
        assert solo_values == ["shoot", "multiply", "multiply", "m2_i1_t3"]
        nb_values = baked_wf["nodes"][1]["widgets_values"]
        assert nb_values[:2] == ["loras.md", "Neg\nPortrait"]
        nb_pin = json.loads(nb_values[2])
        assert nb_pin["entries"] == [{"name": "Portrait", "text": "portrait text"}]
        as_values = baked_wf["nodes"][2]["widgets_values"]
        assert as_values[:3] == [slug, 1.0, 0]
        assert json.loads(as_values[3])["set"] == sets_store.load_set(context, slug)
        assert as_values[4] == "(any)"  # a frontend-appended tail widget after ours survives

        baked_pr = json.loads(chunks["prompt"])
        assert baked_pr["5"]["inputs"]["solo_run"] == "m2_i1_t3"
        assert json.loads(baked_pr["7"]["inputs"]["pinned"])["source"]["token"] == "m2_i1_t3"
        assert json.loads(baked_pr["9"]["inputs"]["pinned_state"])["slug"] == slug

        run = json.loads(chunks[m.EPS_RUN_CHUNK])
        assert run["token"] == "m2_i1_t3" and run["baked"] is True
        assert run["pinned"] == ["7", "9"]
        assert run["name"] == "Portrait" and run["text"] == "portrait text"

        # the caller's dicts were not mutated
        assert workflow["nodes"][1]["widgets_values"] == ["loras.md", "Neg\nPortrait"]
        assert prompt["7"]["inputs"]["pinned"] == ""
        assert prompt["9"]["inputs"]["pinned_state"] == ""

        # and the baked values drive the nodes byte-for-byte after edits
        notebook.write_text("## Portrait\nEDITED\n", encoding="utf-8")
        sets_store.delete_set(context, slug)
        assert LoraLibraryNotebook().read_entry(
            file="loras.md", entry="Portrait", pinned=nb_values[2]
        ) == (["portrait text"], ["Portrait"])
        *_, loras_text = LoraLibraryApplySet().apply(set=slug, pinned_state=as_values[3])
        assert loras_text == "detailer_0.8 film_grain_0.4_0.6"

    def test_resaving_a_recreated_run_keeps_the_original_pin(
        self, fake_folder_paths: Path, notebook: Path
    ) -> None:
        original = json.dumps(
            {
                "format": 1,
                "entries": [{"name": "Portrait", "text": "ORIGINAL"}],
                "source": {
                    "file": "loras.md",
                    "token": "m2_i1_t3",
                    "captured": "2026-08-20T00:00:00Z",
                },
            }
        )
        workflow = {
            "nodes": [
                {
                    "id": 5,
                    "type": "EPSCrossSweep",
                    "widgets_values": ["b", "multiply", "multiply", ""],
                },
                {
                    "id": 7,
                    "type": "LoraLibraryNotebook",
                    "widgets_values": ["loras.md", "Portrait", original],
                },
            ]
        }
        prompt = {
            "5": {"class_type": "EPSCrossSweep", "inputs": {"solo_run": ""}},
            "7": _notebook_prompt(entry="Portrait", pinned=original),
        }
        result = m.EPSSaveImage().save(
            [_image()], filename_prefix="again", run_info=json.dumps(RUN),
            prompt=prompt, extra_pnginfo={"workflow": workflow},
        )
        png = Image.open(fake_folder_paths / result["ui"]["images"][0]["filename"])
        assert json.loads(png.text["workflow"])["nodes"][1]["widgets_values"][2] == original
        assert json.loads(png.text["prompt"])["7"]["inputs"]["pinned"] == original
        assert json.loads(png.text[m.EPS_RUN_CHUNK])["pinned"] == []  # nothing NEW pinned

    def test_store_or_capture_failure_never_fails_the_save(
        self,
        fake_folder_paths: Path,
        notebook: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        workflow = {
            "nodes": [
                {"id": 7, "type": "LoraLibraryNotebook", "widgets_values": ["loras.md", "Portrait"]}
            ]
        }
        prompt = {"7": _notebook_prompt(entry="Portrait")}

        def boom(*_a, **_k):
            raise RuntimeError("store exploded")

        monkeypatch.setattr(nodes_notebook, "resolve_selection", boom)
        with caplog.at_level(logging.WARNING, logger="eps_image"):
            result = m.EPSSaveImage().save(
                [_image()], filename_prefix="one", run_info=json.dumps(RUN),
                prompt=prompt, extra_pnginfo={"workflow": workflow},
            )
        png = Image.open(fake_folder_paths / result["ui"]["images"][0]["filename"])
        assert json.loads(png.text[m.EPS_RUN_CHUNK])["pinned"] == []
        unpinned = json.loads(png.text["workflow"])["nodes"][0]["widgets_values"]
        assert unpinned == ["loras.md", "Portrait"]
        assert any("store exploded" in r.message for r in caplog.records)

        monkeypatch.setattr(m, "capture_pins", boom)
        result = m.EPSSaveImage().save(
            [_image()], filename_prefix="two", run_info=json.dumps(RUN),
            prompt=prompt, extra_pnginfo={"workflow": workflow},
        )
        assert result["ui"]["images"][0]["filename"] == "two_00001_.png"

    def test_unwired_run_info_pins_nothing(
        self, fake_folder_paths: Path, notebook: Path, context: LibraryContext
    ) -> None:
        slug = _save_set(context)
        prompt = {"7": _notebook_prompt(), "9": _apply_set_prompt(slug)}
        result = m.EPSSaveImage().save([_image()], filename_prefix="plain", prompt=prompt)
        png = Image.open(fake_folder_paths / result["ui"]["images"][0]["filename"])
        assert m.EPS_RUN_CHUNK not in png.text
        assert json.loads(png.text["prompt"]) == prompt
