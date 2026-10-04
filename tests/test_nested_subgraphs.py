"""v1.2.0 nested reach, PYTHON side (owner ask 2026-10-03: "make sure all of
the nodes that can control other nodes also looks into nested nodes";
FORMAT.md §7.10).

Two shapes of data carry a nested node on the backend, and both were audited:

* the flattened API ``prompt`` -- ComfyUI's frontend resolves every link
  through subgraph boundaries (``ExecutableNodeDTO``) and keys nodes by
  EXECUTION path id, so node 2 inside SubgraphNode 3 is ``"3:2"`` and a
  consumer's link to it reads ``["3:2", 0]``. Every prompt scan in the pack
  (``_consumed_output_slots``, ``_input_origin``, the Distributor's wired-slot
  scan, the switchers' all-off sibling scan) compares ids as STRINGS, so they
  are nested-aware by construction -- pinned here so it stays that way;
* the saved ``workflow`` JSON (``extra_pnginfo["workflow"]``) -- subgraph
  contents live under ``definitions.subgraphs[].nodes`` and a SubgraphNode's
  ``type`` is the definition's ``id``. EPS Save Image's bake/pin walks that
  with ``find_node_in_workflow``, which resolves a path id segment by segment
  at any depth.

These tests drive the REAL functions with fake nested prompts/workflows (and
one end-to-end PNG save). What they cannot cover (the rig must): a real
``graphToPrompt`` flattening, and a real drop of the baked PNG back onto the
canvas.
"""

from __future__ import annotations

import copy
import json
import sys
import types
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from eps_image import nodes_save_image as m
from eps_image.nodes_cross_sweep import _consumed_output_slots, _input_origin
from eps_image.nodes_distributor import MAX_OUTPUTS, EPSDistributor
from eps_image.nodes_switcher import _SWITCHER_SLOT_PATTERNS, _slots_fed_by_an_empty_switcher
from lora_library import nodes_notebook, nodes_sets, sets_store
from lora_library.context import LibraryContext

NOTEBOOK_MD = "## Portrait\nportrait text\n## Neg\nbad hands\n## Landscape\nwide text\n"

#: Run info for a multiplier that lives TWO subgraphs deep (path "3:5:2").
RUN_DEEP = {
    "format": 1,
    "token": "m2_i1_t3",
    "node": "3:5:2",
    "run": 4,
    "total": 12,
    "steps": 2,
    "pairs": 6,
    "name": "Portrait",
    "text": "portrait text",
    "label": "lora_0.5",
}


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


def _save_set(context: LibraryContext) -> str:
    slug, _ = sets_store.save_set(
        context,
        {
            "name": "Nested Set",
            "loras": [{"file": "detailer.safetensors", "on": True, "strength": 0.8}],
            "trigger_words": "nested",
        },
    )
    return slug


# ---------------------------------------------------------------- fixtures
#
# root:    5  EPSCrossSweep           (root-level control: the id "5" below
#                                      collides with the inner "5" of subgraph A)
#          3  SubgraphNode  type=uuid-A
# uuid-A:  2  LoraLibraryApplySet     -> path "3:2"
#          5  SubgraphNode  type=uuid-B   (inner id 5 collides with ROOT id 5)
# uuid-B:  2  EPSCrossSweep           -> path "3:5:2"   (inner id 2 collides with A's 2)
#          4  LoraLibraryNotebook     -> path "3:5:4"


def _workflow() -> dict:
    return {
        "nodes": [
            {"id": 5, "type": "EPSCrossSweep", "widgets_values": ["root", "multiply"]},
            {"id": 3, "type": "uuid-A"},
        ],
        "definitions": {
            "subgraphs": [
                {
                    "id": "uuid-A",
                    "nodes": [
                        {
                            "id": 2,
                            "type": "LoraLibraryApplySet",
                            "widgets_values": ["my-set", 1.0],
                        },
                        {"id": 5, "type": "uuid-B"},
                    ],
                },
                {
                    "id": "uuid-B",
                    "nodes": [
                        {
                            "id": 2,
                            "type": "EPSCrossSweep",
                            "widgets_values": ["deep", "multiply"],
                        },
                        {
                            "id": 4,
                            "type": "LoraLibraryNotebook",
                            "widgets_values": ["loras.md", "Neg\nPortrait"],
                        },
                    ],
                },
            ]
        },
    }


def _prompt(set_slug: str = "my-set") -> dict:
    return {
        "5": {"class_type": "EPSCrossSweep", "inputs": {"solo_run": ""}},
        "3:2": {
            "class_type": "LoraLibraryApplySet",
            "inputs": {
                "set": set_slug,
                "strength_scale": 1.0,
                "loader_slot": 0,
                "pinned_state": "",
            },
        },
        "3:5:2": {"class_type": "EPSCrossSweep", "inputs": {"solo_run": ""}},
        "3:5:4": {
            "class_type": "LoraLibraryNotebook",
            "inputs": {"file": "loras.md", "entry": "Neg\nPortrait", "pinned": ""},
        },
    }


# ------------------------------------------------- find_node_in_workflow


class TestFindNodeInWorkflowAtAnyDepth:
    def test_resolves_root_one_level_and_two_levels(self) -> None:
        wf = _workflow()
        assert m.find_node_in_workflow(wf, "5")["widgets_values"][0] == "root"
        assert m.find_node_in_workflow(wf, "3:2")["type"] == "LoraLibraryApplySet"
        assert m.find_node_in_workflow(wf, "3:5:2")["widgets_values"][0] == "deep"
        assert m.find_node_in_workflow(wf, "3:5:4")["type"] == "LoraLibraryNotebook"

    def test_colliding_inner_ids_are_kept_apart_by_the_path(self) -> None:
        """Root 5, A's inner 5 (a SubgraphNode) and B's inner 2 / A's inner 2
        all share bare ids -- only the full path names the right node."""
        wf = _workflow()
        assert m.find_node_in_workflow(wf, "5")["type"] == "EPSCrossSweep"
        assert m.find_node_in_workflow(wf, "3:5")["type"] == "uuid-B"
        assert m.find_node_in_workflow(wf, "3:2")["type"] == "LoraLibraryApplySet"
        assert m.find_node_in_workflow(wf, "3:5:2")["type"] == "EPSCrossSweep"

    @pytest.mark.parametrize("path", ["", "9", "3:9", "3:5:9", "3:2:1", "5:2", "x:y"])
    def test_stale_or_wrong_paths_are_none_never_a_wrong_node(self, path: str) -> None:
        # "3:2:1": node 2 of uuid-A is an ApplySet, not a SubgraphNode, so
        # there is no definition to descend into; "5:2": root 5 is a plain
        # node. Both must degrade to None.
        assert m.find_node_in_workflow(_workflow(), path) is None

    def test_string_ids_in_the_saved_json_resolve_too(self) -> None:
        wf = _workflow()
        for node in wf["nodes"] + [n for sg in wf["definitions"]["subgraphs"] for n in sg["nodes"]]:
            node["id"] = str(node["id"])  # newer frontends serialise string ids
        assert m.find_node_in_workflow(wf, "3:5:2")["widgets_values"][0] == "deep"

    def test_a_missing_definition_is_none(self) -> None:
        wf = _workflow()
        wf["definitions"]["subgraphs"] = wf["definitions"]["subgraphs"][:1]  # uuid-B is gone
        assert m.find_node_in_workflow(wf, "3:5:2") is None
        assert m.find_node_in_workflow(wf, "3:2")["type"] == "LoraLibraryApplySet"


# ---------------------------------------------------------- the bake itself


class TestBakeReachesTwoLevelsDeep:
    def test_multiplier_inside_nested_subgraphs_is_soloed_in_both_chunks(self) -> None:
        wf, pr = _workflow(), _prompt()
        w2, p2, baked = m.bake_solo(wf, pr, {"token": "m2_i1_t3", "node": "3:5:2"})
        assert baked is True
        deep = m.find_node_in_workflow(w2, "3:5:2")
        # solo_run is the tail after the multiplier's earlier widgets: derived
        # from INPUT_TYPES, short arrays padded with the class defaults first.
        assert deep["widgets_values"][-1] == "m2_i1_t3"
        assert p2["3:5:2"]["inputs"]["solo_run"] == "m2_i1_t3"
        # the ROOT multiplier (same class, same bare inner id) is untouched
        assert m.find_node_in_workflow(w2, "5")["widgets_values"] == ["root", "multiply"]
        assert p2["5"]["inputs"]["solo_run"] == ""
        # the caller's dicts are never mutated by the copying variant
        assert wf == _workflow() and pr == _prompt()

    def test_multiplier_found_in_the_workflow_alone_or_the_prompt_alone(self) -> None:
        info = {"token": "t", "node": "3:5:2"}
        _w, p2, baked = m.bake_solo(None, _prompt(), info)
        assert baked is True and p2["3:5:2"]["inputs"]["solo_run"] == "t"
        w2, _p, baked = m.bake_solo(_workflow(), None, info)
        assert baked is True
        assert m.find_node_in_workflow(w2, "3:5:2")["widgets_values"][-1] == "t"

    def test_pins_for_nodes_inside_subgraphs_land_in_definition_and_prompt(self) -> None:
        pins = {
            "3:2": m.PinnedWidget("LoraLibraryApplySet", "pinned_state", "AS-PIN"),
            "3:5:4": m.PinnedWidget("LoraLibraryNotebook", "pinned", "NB-PIN"),
        }
        w2, p2, baked, pinned = m.bake_provenance(_workflow(), _prompt(), RUN_DEEP, pins)
        assert baked is True and pinned == ["3:2", "3:5:4"]
        apply_set = m.find_node_in_workflow(w2, "3:2")
        assert apply_set["widgets_values"] == ["my-set", 1.0, 0, "AS-PIN"]
        notebook = m.find_node_in_workflow(w2, "3:5:4")
        assert notebook["widgets_values"][:3] == ["loras.md", "Neg\nPortrait", "NB-PIN"]
        assert p2["3:2"]["inputs"]["pinned_state"] == "AS-PIN"
        assert p2["3:5:4"]["inputs"]["pinned"] == "NB-PIN"

    def test_inplace_bake_and_undo_restore_nested_definitions_exactly(self) -> None:
        """v0.80.0 mutate-and-restore: the shared hidden extra_pnginfo objects
        must come out byte-identical, including edits made INSIDE a subgraph
        definition two levels down."""
        wf, pr = _workflow(), _prompt()
        before_wf, before_pr = copy.deepcopy(wf), copy.deepcopy(pr)
        pins = {"3:5:4": m.PinnedWidget("LoraLibraryNotebook", "pinned", "NB-PIN")}
        undo: list = []
        w2, p2, baked, pinned = m.bake_provenance_inplace(wf, pr, RUN_DEEP, pins, undo)
        assert baked is True and pinned == ["3:5:4"]
        assert w2 is wf and p2 is pr  # in place
        assert m.find_node_in_workflow(wf, "3:5:2")["widgets_values"][-1] == "m2_i1_t3"
        assert pr["3:5:4"]["inputs"]["pinned"] == "NB-PIN"
        m.undo_bakes(undo)
        assert wf == before_wf and pr == before_pr

    def test_two_instances_of_one_definition_bake_identically(self) -> None:
        """A definition instantiated twice (SubgraphNodes 3 and 7) appears in
        the flattened prompt as "3:2" and "7:2" but is ONE node in the
        workflow JSON; baking both ids is idempotent and lands once."""
        wf = _workflow()
        wf["nodes"].append({"id": 7, "type": "uuid-A"})
        pr = _prompt()
        pr["7:2"] = copy.deepcopy(pr["3:2"])
        pins = {
            "3:2": m.PinnedWidget("LoraLibraryApplySet", "pinned_state", "AS-PIN"),
            "7:2": m.PinnedWidget("LoraLibraryApplySet", "pinned_state", "AS-PIN"),
        }
        w2, p2, _baked, pinned = m.bake_provenance(wf, pr, {"token": "t", "node": "3:5:2"}, pins)
        assert pinned == ["3:2", "7:2"]
        assert m.find_node_in_workflow(w2, "7:2")["widgets_values"] == ["my-set", 1.0, 0, "AS-PIN"]
        pin_3, pin_7 = p2["3:2"]["inputs"]["pinned_state"], p2["7:2"]["inputs"]["pinned_state"]
        assert pin_3 == pin_7 == "AS-PIN"


# ------------------------------------------------------ capture from stores


class TestCapturePinsInsideSubgraphs:
    def test_nested_notebook_and_apply_set_are_pinned_from_the_stores(
        self, context: LibraryContext, notebook: Path
    ) -> None:
        slug = _save_set(context)
        pins = m.capture_pins(_prompt(slug), RUN_DEEP)
        assert list(pins) == ["3:2", "3:5:4"]
        assert pins["3:2"].class_type == "LoraLibraryApplySet"
        assert json.loads(pins["3:2"].value)["slug"] == slug
        nb = json.loads(pins["3:5:4"].value)
        # the narrowing rule works on a nested notebook exactly like a root one:
        # run_info names "Portrait", one of the two selected entries.
        assert nb["entries"] == [{"name": "Portrait", "text": "portrait text"}]
        assert nb["source"]["token"] == "m2_i1_t3"

    def test_an_already_pinned_nested_node_keeps_its_pin(self, context: LibraryContext) -> None:
        slug = _save_set(context)
        prompt = _prompt(slug)
        prompt["3:2"]["inputs"]["pinned_state"] = json.dumps({"format": 1, "slug": slug})
        assert "3:2" not in m.capture_pins(prompt, RUN_DEEP)


# ------------------------------------------------- end to end: a real save


class _FakeTensor:
    def __init__(self, array: np.ndarray) -> None:
        self._array = array
        self.shape = array.shape

    def cpu(self) -> _FakeTensor:
        return self

    def numpy(self) -> np.ndarray:
        return self._array


@pytest.fixture
def fake_folder_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
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


def test_save_image_pins_and_solos_everything_inside_nested_subgraphs(
    fake_folder_paths: Path, context: LibraryContext, notebook: Path
) -> None:
    """The owner's literal case, end to end: the multiplier, a Prompt
    Notebook and an Apply LoRA Set all live inside (nested) subgraphs; the
    saved PNG's workflow and prompt chunks carry the solo AND both pins."""
    slug = _save_set(context)
    workflow, prompt = _workflow(), _prompt(slug)
    tensor = _FakeTensor(np.linspace(0.0, 1.0, 4 * 3 * 3, dtype=np.float32).reshape(3, 4, 3))
    result = m.EPSSaveImage().save(
        [tensor],
        filename_prefix="nested/Portrait_m2_i1_t3",
        run_info=json.dumps(RUN_DEEP),
        prompt=prompt,
        extra_pnginfo={"workflow": workflow},
    )
    saved = result["ui"]["images"][0]
    chunks = Image.open(fake_folder_paths / saved["subfolder"] / saved["filename"]).text

    baked_wf = json.loads(chunks["workflow"])
    assert m.find_node_in_workflow(baked_wf, "3:5:2")["widgets_values"][-1] == "m2_i1_t3"
    set_values = m.find_node_in_workflow(baked_wf, "3:2")["widgets_values"]
    assert json.loads(set_values[3])["slug"] == slug
    nb_values = m.find_node_in_workflow(baked_wf, "3:5:4")["widgets_values"]
    assert json.loads(nb_values[2])["entries"] == [{"name": "Portrait", "text": "portrait text"}]

    baked_pr = json.loads(chunks["prompt"])
    assert baked_pr["3:5:2"]["inputs"]["solo_run"] == "m2_i1_t3"
    assert baked_pr["5"]["inputs"]["solo_run"] == ""  # the root multiplier is NOT soloed
    assert json.loads(baked_pr["3:2"]["inputs"]["pinned_state"])["slug"] == slug
    assert json.loads(baked_pr["3:5:4"]["inputs"]["pinned"])["source"]["token"] == "m2_i1_t3"

    run = json.loads(chunks[m.EPS_RUN_CHUNK])
    assert run["baked"] is True and run["node"] == "3:5:2"
    assert run["pinned"] == ["3:2", "3:5:4"]
    # the shared hidden objects were restored byte-for-byte after the save
    assert workflow == _workflow() and prompt == _prompt(slug)


# ----------------------------------------------------- prompt scans, nested


class TestPromptScansAreNestedAware:
    """Every backend prompt scan keys on the flattened execution id as a
    STRING, so a node at "3:2" is found exactly like a root node. Pinned so a
    well-meant `int(unique_id)` can never creep in."""

    def test_consumed_output_slots_sees_consumers_across_a_boundary(self) -> None:
        # Multiplier inside subgraph 3 (id "3:2"); its model (slot 0) feeds a
        # root sampler and its image (slot 2) feeds a node in ANOTHER subgraph.
        prompt = {
            "3:2": {"class_type": "EPSCrossSweep", "inputs": {}},
            "9": {"class_type": "KSampler", "inputs": {"model": ["3:2", 0], "seed": 1}},
            "4:7": {"class_type": "SaveImage", "inputs": {"images": ["3:2", 2]}},
            "5": {"class_type": "SaveImage", "inputs": {"images": ["2", 2]}},  # bare "2" != "3:2"
        }
        assert _consumed_output_slots(prompt, "3:2") == {0, 2}
        assert _consumed_output_slots(prompt, 2) == {2}  # the ROOT node 2 is a different node

    def test_input_origin_returns_the_nested_origin_id_verbatim(self) -> None:
        prompt = {
            "3:2": {"class_type": "EPSCrossSweep", "inputs": {"vae": ["4:7", 0], "x": 1}},
            "4:7": {"class_type": "VAELoader", "inputs": {}},
        }
        assert _input_origin(prompt, "3:2", "vae") == "4:7"
        assert _input_origin(prompt, "3:2", "x") is None

    def test_distributor_wired_slots_and_lazy_skip_for_a_nested_distributor(self) -> None:
        prompt = {
            "3:2": {"class_type": "EPSDistributor", "inputs": {}},
            "6": {"class_type": "SaveImage", "inputs": {"images": ["3:2", 0]}},
            "8:1": {"class_type": "PreviewImage", "inputs": {"images": ["3:2", 2]}},
            # root node 2 is a DIFFERENT node than "3:2" (bare ids never alias)
            "2": {"class_type": "PreviewImage", "inputs": {"images": ["2", 5]}},
        }
        assert EPSDistributor._wired_slots(prompt, "3:2") == {1, 3}
        assert EPSDistributor._wired_slots(prompt, "2") == {6}
        off = json.dumps({"out_1": False, "out_3": False})
        assert EPSDistributor().check_lazy_status(toggles=off, prompt=prompt, unique_id="3:2") == []
        on = json.dumps({"out_1": False})
        assert EPSDistributor().check_lazy_status(toggles=on, prompt=prompt, unique_id="3:2") == [
            "image"
        ]
        # a nested id absent from the prompt degrades to "assume wired"
        assert EPSDistributor._wired_slots(prompt, "9:9") is None
        assert MAX_OUTPUTS >= 3

    def test_switcher_lazy_skip_follows_an_all_off_sibling_across_a_boundary(self) -> None:
        """EPSSwitcher "3:2" is fed by an all-off sibling EPSSwitcher "3:5:1"
        two levels down: its slot is skipped exactly as at the root."""
        pattern = _SWITCHER_SLOT_PATTERNS["EPSSwitcher"]
        prompt = {
            "3:2": {
                "class_type": "EPSSwitcher",
                "inputs": {"image_1": ["3:5:1", 0], "image_2": ["8", 0], "toggles": "{}"},
            },
            "3:5:1": {
                "class_type": "EPSSwitcher",
                "inputs": {"image_1": ["9", 0], "toggles": json.dumps({"image_1": False})},
            },
            "8": {"class_type": "LoadImage", "inputs": {}},
        }
        assert _slots_fed_by_an_empty_switcher(prompt, "3:2", pattern) == {"image_1"}
