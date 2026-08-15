"""Frontend tests for the EPS Run Multiplier run-count readout (FORMAT.md
§6.10 "Run-count visibility", v0.58.0 -- the pre-queue ESTIMATE layer; the
backend `eps-run-multiplier-count` truth event is covered by
tests/test_cross_sweep.py).

``web/eps_image/cross_sweep.js`` factors the whole estimator into PURE
exported functions -- ``estimateRuns`` (the multiplier math over a plain
graph snapshot), ``formatReadout`` (the one-line text + class),
``snapshotFromGraph`` (the thin live-graph adapter), and the per-source
counters (``switcherEnabledCount``/``checkpointSelectionCount``/
``notebookEntryCount``/``pickerEnabledRowCount``/``iteratorValueCount``) --
precisely so this file can drive them under Node with no litegraph node
stub, following ``tests/test_picker_js.py``'s "served-layout" convention:
the module imports ``../../../scripts/api.js`` and
``../../../scripts/app.js``, resolved against the served layout, so the
fixture mirrors that directory depth in a tmp dir and byte-copies the real
module in. Get the relative import depth wrong and Node cannot resolve the
module at all -- the ``cross_sweep_api`` fixture is therefore also a
regression test for the import paths.

The rest -- attach()'s class gate, the display-only DOM widget's two
serialize flags, the throttled redraw-driven recompute, the toast listener
-- is structural/closure-bound (it only runs against a real litegraph node
and a real DOM) and is pinned via SOURCE-TEXT assertions, matching
test_picker_js.py's module-scope ``source`` fixture convention.

Skips cleanly when Node isn't installed; the LIVE mechanics (the readout
repainting on a real canvas, the toast firing off a real queue) are for the
rig, not here.
"""

# ruff: noqa: RUF001 — the readout/toast pins quote FORMAT.md §6.10's exact
# glyphs (the readout separator is the real MULTIPLICATION SIGN, the floor
# marker the real greater-or-equal sign): byte-exact contract pins, not
# accidental ASCII look-alikes -- test_frame_saver_paste_js.py's identical
# file-scoped convention.

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CROSS_SWEEP_JS = REPO_ROOT / "web" / "eps_image" / "cross_sweep.js"
ENTRY_JS = REPO_ROOT / "web" / "eps_image.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

# --------------------------------------------------------------- case tables


def _link(origin: str, slot: int = 0) -> dict:
    """One snapshot input link -- the estimator's `{originId, originSlot}`."""
    return {"originId": origin, "originSlot": slot}


def _text_source() -> dict:
    """A core single-output STRING source (allow-list: counts 1)."""
    return {"classType": "PrimitiveStringMultiline", "widgets": {"value": "hello"}, "inputs": {}}


def _notebook(entry: str) -> dict:
    """§6.1 Notebook: fan-out = non-empty `entry` lines."""
    return {
        "classType": "LoraLibraryNotebook",
        "widgets": {"file": "loras.md", "entry": entry},
        "inputs": {},
    }


def _checkpoint_switcher(names: list[str]) -> dict:
    """§6.12: every output is `selection`-array length."""
    return {
        "classType": "EPSCheckpointSwitcher",
        "widgets": {"selection": json.dumps(names)},
        "inputs": {},
    }


def _owner_shape(sweep_mode: str) -> dict:
    """The owner's exact v0.57.0 shape: a 4-model Model Switcher + a 2-VAE
    VAE Switcher into one multiplier (FORMAT.md §6.10's sweep_mode story)."""
    nodes: dict = {
        "3": _text_source(),
        "1": {
            "classType": "EPSModelSwitcher",
            "widgets": {"toggles": "{}"},
            "inputs": {f"model_{i}": _link(str(10 + i)) for i in range(1, 5)},
        },
        "2": {
            "classType": "EPSVaeSwitcher",
            "widgets": {"toggles": "{}"},
            "inputs": {"vae_1": _link("15"), "vae_2": _link("16")},
        },
        "5": {
            "classType": "EPSCrossSweep",
            "widgets": {"base_folder": "", "pair_mode": "paired", "sweep_mode": sweep_mode},
            "inputs": {"model": _link("1"), "vae": _link("2"), "text": _link("3")},
        },
    }
    for i in range(1, 5):
        nodes[str(10 + i)] = {"classType": "UNETLoader", "widgets": {}, "inputs": {}}
    for nid in ("15", "16"):
        nodes[nid] = {"classType": "VAELoader", "widgets": {}, "inputs": {}}
    return {"nodes": nodes}


def _owner_shape_with_blocked_label() -> dict:
    """Round-2 repro 7 (ORDERING): the owner shape's aligned CONFLICT
    (model=4 vs vae=2) PLUS a label wired from an EMPTY-selection
    Checkpoint Switcher -- a KNOWN-blocked source. One blocker element in
    any consumed input list blocks the whole node, so run() never executes,
    its conflict ValueError is never raised, and the queue SUCCEEDS with 0
    runs: the readout must say nothing-to-run naming label, never the
    "queue will fail" conflict paint."""
    shape = _owner_shape("aligned")
    shape["nodes"]["20"] = _checkpoint_switcher([])
    shape["nodes"]["5"]["inputs"]["label"] = _link("20", 3)
    return shape


def _image_switcher_two_of_three() -> dict:
    """§6.4 Image Switcher: 3 wired, image_2 toggled off -> emits 2."""
    return {
        "classType": "EPSSwitcher",
        "widgets": {"toggles": '{"image_2": false}'},
        "inputs": {"image_1": _link("91"), "image_2": _link("92"), "image_3": _link("93")},
    }


_LOAD_IMAGES = {
    nid: {"classType": "LoadImage", "widgets": {}, "inputs": {}} for nid in ("91", "92", "93")
}

_PICKER_TWO_ENABLED = {
    "classType": "EPSLoraPicker",
    "widgets": {
        "selection": json.dumps(
            {
                "scope": "",
                "loras": [
                    {"file": "a.st", "on": True, "strength": 1},
                    {"file": "b.st", "strength": 1},  # `on` defaults true
                    {"file": "c.st", "on": False, "strength": 1},  # excluded
                ],
            }
        )
    },
    "inputs": {},
}

#: (case name, snapshot, node id, expected subset). ``error_contains`` is a
#: list of substrings the error must carry; every other key compares
#: directly (``unknowns`` sorted).
ESTIMATE_CASES = [
    (
        "owner_shape_aligned_disagreement_is_the_queue_error_early",
        _owner_shape("aligned"),
        "5",
        {"error_contains": ["sweep lengths disagree", "model=4", "vae=2", "queue will fail"]},
    ),
    (
        "owner_shape_sweep_multiply_is_4x2_8_runs",
        _owner_shape("multiply"),
        "5",
        {"total": 8, "atLeast": False, "steps": 8, "pairs": 1, "unknowns": [], "error": None},
    ),
    (
        "checkpoint_switcher_three_ticks_aligned_is_3",
        {
            "nodes": {
                "20": _checkpoint_switcher(["a.st", "b.st", "c.st"]),
                "3": _text_source(),
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "paired", "sweep_mode": "aligned"},
                    "inputs": {
                        "model": _link("20", 0),
                        "clip": _link("20", 1),
                        "vae": _link("20", 2),
                        "label": _link("20", 3),
                        "text": _link("3"),
                    },
                },
            }
        },
        "5",
        {"total": 3, "atLeast": False, "steps": 3, "pairs": 1, "unknowns": [], "error": None},
    ),
    (
        "notebook_pairs_times_unknown_emit_grid_is_at_least",
        {
            "nodes": {
                "30": _notebook("First\nSecond\n\n"),
                "31": {"classType": "EPSImageGrid", "widgets": {"mode": "Emit"}, "inputs": {}},
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "multiply", "sweep_mode": "aligned"},
                    "inputs": {
                        "image": _link("31"),
                        "text": _link("30", 0),
                        "name": _link("30", 1),
                    },
                },
            }
        },
        "5",
        {
            "total": 2,
            "atLeast": True,
            "steps": 1,
            "pairs": 2,
            "unknowns": ["image"],
            "error": None,
        },
    ),
    (
        # The injected imageGridCount is a client-side ECHO of SERVER state
        # (the live node's imgs preview can lag or lie), so the number is
        # used but stays a FLOOR: atLeast True, the readout keeps its >=.
        "emit_grid_with_adapter_injected_count_is_a_floor",
        {
            "nodes": {
                "30": _notebook("First\nSecond\n\n"),
                "31": {
                    "classType": "EPSImageGrid",
                    "widgets": {"mode": "Emit"},
                    "inputs": {},
                    "imageGridCount": 3,
                },
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "multiply", "sweep_mode": "aligned"},
                    "inputs": {"image": _link("31"), "text": _link("30", 0)},
                },
            }
        },
        "5",
        {
            "total": 6,
            "atLeast": True,
            "steps": 1,
            "pairs": 6,
            "unknowns": ["image"],
            "error": None,
        },
    ),
    (
        "chained_multiplier_recursion_is_exact",
        {
            "nodes": {
                "40": _checkpoint_switcher(["a.st", "b.st"]),
                "3": _text_source(),
                "41": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "paired", "sweep_mode": "aligned"},
                    "inputs": {"model": _link("40", 0), "text": _link("3")},
                },
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "paired", "sweep_mode": "aligned"},
                    "inputs": {"model": _link("41", 0), "text": _link("3")},
                },
            }
        },
        "5",
        {"total": 2, "atLeast": False, "steps": 2, "pairs": 1, "unknowns": [], "error": None},
    ),
    (
        "multiplier_cycle_guard_degrades_to_at_least_never_hangs",
        {
            "nodes": {
                "3": _text_source(),
                "50": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "paired", "sweep_mode": "aligned"},
                    "inputs": {"model": _link("51", 0), "text": _link("3")},
                },
                "51": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "paired", "sweep_mode": "aligned"},
                    "inputs": {"model": _link("50", 0), "text": _link("3")},
                },
            }
        },
        "50",
        {"total": 1, "atLeast": True, "unknowns": ["model"], "error": None},
    ),
    (
        "collect_grid_counts_zero_nothing_to_run",
        {
            "nodes": {
                "30": _notebook("First\nSecond"),
                "31": {"classType": "EPSImageGrid", "widgets": {"mode": "Collect"}, "inputs": {}},
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "multiply", "sweep_mode": "aligned"},
                    "inputs": {"image": _link("31"), "text": _link("30", 0)},
                },
            }
        },
        "5",
        {
            "total": 0,
            "atLeast": False,
            "steps": 1,
            "pairs": 0,
            "error": None,
            # A Collect-mode grid is a KNOWN-zero image source, so the
            # round-2 zero-collapse names the culprit input.
            "breakdown": "nothing to run (image input is empty/blocked)",
        },
    ),
    (
        "per_lora_iterator_with_picker_two_rows_times_three_values",
        {
            "nodes": {
                "10": {"classType": "CheckpointLoaderSimple", "widgets": {}, "inputs": {}},
                "70": _PICKER_TWO_ENABLED,
                "71": {
                    "classType": "LoraLibrarySweep",
                    "widgets": {
                        "min": 0,
                        "max": 1,
                        "increment": 0.5,
                        "mode": "Each lora independently",
                    },
                    "inputs": {
                        "model": _link("10", 0),
                        "clip": _link("10", 1),
                        "lora_stack": _link("70", 0),
                    },
                },
                "3": _text_source(),
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "paired", "sweep_mode": "aligned"},
                    "inputs": {
                        "model": _link("71", 0),
                        "clip": _link("71", 1),
                        "label": _link("71", 2),
                        "text": _link("3"),
                    },
                },
            }
        },
        "5",
        {"total": 6, "atLeast": False, "steps": 6, "pairs": 1, "unknowns": [], "error": None},
    ),
    (
        # Round-3: the iterator's clip OUTPUT with its clip INPUT unwired is
        # a None-passthrough -- _as_clean_list strips it to empty, steps=0,
        # a silent 0-run queue. The estimator used to overclaim 6 here.
        "iterator_clip_output_with_clip_input_unwired_is_none_passthrough",
        {
            "nodes": {
                "10": {"classType": "CheckpointLoaderSimple", "widgets": {}, "inputs": {}},
                "70": _PICKER_TWO_ENABLED,
                "71": {
                    "classType": "LoraLibrarySweep",
                    "widgets": {
                        "min": 0,
                        "max": 1,
                        "increment": 0.5,
                        "mode": "Each lora independently",
                    },
                    "inputs": {"model": _link("10", 0), "lora_stack": _link("70", 0)},
                },
                "3": _text_source(),
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "paired", "sweep_mode": "aligned"},
                    "inputs": {
                        "model": _link("71", 0),
                        "clip": _link("71", 1),
                        "label": _link("71", 2),
                        "text": _link("3"),
                    },
                },
            }
        },
        "5",
        {
            "total": 0,
            "atLeast": False,
            "error": None,
            "breakdown": "nothing to run (clip input is empty/blocked)",
        },
    ),
    (
        # Round-3: a picker's model OUTPUT with its model INPUT unwired is
        # the same None-passthrough shape.
        "picker_model_output_with_model_input_unwired_is_none_passthrough",
        {
            "nodes": {
                "70": _PICKER_TWO_ENABLED,
                "3": _text_source(),
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "paired", "sweep_mode": "aligned"},
                    "inputs": {"model": _link("70", 0), "text": _link("3")},
                },
            }
        },
        "5",
        {
            "total": 0,
            "atLeast": False,
            "error": None,
            "breakdown": "nothing to run (model input is empty/blocked)",
        },
    ),
    (
        # Round-3: an EMPTY notebook RAISES at execution ("no entry
        # selected") -- an error paint, never the queue-succeeds
        # empty/blocked family.
        "empty_notebook_is_a_queue_fail_error_not_a_zero",
        {
            "nodes": {
                "90": {"classType": "LoraLibraryNotebook", "widgets": {"entry": ""}, "inputs": {}},
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "paired", "sweep_mode": "aligned"},
                    "inputs": {"text": _link("90", 1)},
                },
            }
        },
        "5",
        {"error": "EPS Prompt Notebook has no entry selected — the queue will fail"},
    ),
    (
        # Round-3: a KNOWN-blocked inner multiplier never runs, so its
        # dead-output ValueError never raises -- zero-collapse outranks the
        # fail paint.
        "dead_output_on_a_known_blocked_inner_is_zero_not_queue_fail",
        {
            "nodes": {
                "40": {
                    "classType": "EPSCheckpointSwitcher",
                    "widgets": {"selection": "[]"},
                    "inputs": {},
                },
                "3": _text_source(),
                "6": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "paired", "sweep_mode": "aligned"},
                    "inputs": {"text": _link("3"), "name": _link("40", 3)},
                },
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "paired", "sweep_mode": "aligned"},
                    "inputs": {"model": _link("6", 0), "text": _link("3")},
                },
            }
        },
        "5",
        {"total": 0, "atLeast": False, "error": None},
    ),
    (
        "unknown_third_party_floors_at_one_and_names_the_input",
        {
            "nodes": {
                "80": {"classType": "SomeThirdPartyLoader", "widgets": {}, "inputs": {}},
                "20": _checkpoint_switcher(["a.st", "b.st", "c.st"]),
                "3": _text_source(),
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "paired", "sweep_mode": "aligned"},
                    "inputs": {
                        "model": _link("80"),
                        "label": _link("20", 3),
                        "text": _link("3"),
                    },
                },
            }
        },
        "5",
        {
            "total": 3,
            "atLeast": True,
            "steps": 3,
            "pairs": 1,
            "unknowns": ["model"],
            "error": None,
        },
    ),
    (
        "pair_multiply_math_switcher_times_notebook",
        {
            "nodes": {
                **_LOAD_IMAGES,
                "90": _image_switcher_two_of_three(),
                "30": _notebook("a\nb\nc"),
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "multiply", "sweep_mode": "aligned"},
                    "inputs": {"image": _link("90"), "text": _link("30", 0)},
                },
            }
        },
        "5",
        {"total": 6, "atLeast": False, "steps": 1, "pairs": 6, "unknowns": [], "error": None},
    ),
    (
        "pair_paired_min_clamps_like_run",
        {
            "nodes": {
                **_LOAD_IMAGES,
                "90": _image_switcher_two_of_three(),
                "30": _notebook("a\nb\nc"),
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "paired", "sweep_mode": "aligned"},
                    "inputs": {"image": _link("90"), "text": _link("30", 0)},
                },
            }
        },
        "5",
        {"total": 2, "atLeast": False, "steps": 1, "pairs": 2, "unknowns": [], "error": None},
    ),
    (
        "multiply_same_origin_vae_is_the_v057_error_early",
        {
            "nodes": {
                "20": _checkpoint_switcher(["a.st", "b.st", "c.st"]),
                "3": _text_source(),
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "paired", "sweep_mode": "multiply"},
                    "inputs": {
                        "model": _link("20", 0),
                        "vae": _link("20", 2),
                        "text": _link("3"),
                    },
                },
            }
        },
        "5",
        {"error_contains": ["sweep_mode multiply", "vae and model", "same node"]},
    ),
    (
        "aligned_length_one_broadcasts_fine",
        {
            "nodes": {
                "1": {
                    "classType": "EPSModelSwitcher",
                    "widgets": {"toggles": "{}"},
                    "inputs": {f"model_{i}": _link("11") for i in range(1, 5)},
                },
                "11": {"classType": "UNETLoader", "widgets": {}, "inputs": {}},
                "15": {"classType": "VAELoader", "widgets": {}, "inputs": {}},
                "3": _text_source(),
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "paired", "sweep_mode": "aligned"},
                    "inputs": {"model": _link("1"), "vae": _link("15"), "text": _link("3")},
                },
            }
        },
        "5",
        {"total": 4, "atLeast": False, "steps": 4, "pairs": 1, "unknowns": [], "error": None},
    ),
    (
        # The reviewer's exact fan-in topology: EPSSwitcher.execute FLATTENS
        # each enabled slot's upstream LIST (nodes_switcher.py INPUT_IS_LIST
        # + enabled_values.extend), so Switcher B fed by a 3-fan Switcher A
        # in slot 1 and a single Load Image in slot 2 emits FOUR images, not
        # two -- x 2 notebook lines in pair multiply = 8 runs EXACT.
        "switcher_fan_in_sums_upstream_list_lengths_8_exact",
        {
            "nodes": {
                **_LOAD_IMAGES,
                "94": {"classType": "LoadImage", "widgets": {}, "inputs": {}},
                "100": {  # Switcher A: 3 enabled single-image slots -> 3
                    "classType": "EPSSwitcher",
                    "widgets": {"toggles": "{}"},
                    "inputs": {
                        "image_1": _link("91"),
                        "image_2": _link("92"),
                        "image_3": _link("93"),
                    },
                },
                "101": {  # Switcher B: slot 1 <- A (3), slot 2 <- single (1) -> 4
                    "classType": "EPSSwitcher",
                    "widgets": {"toggles": "{}"},
                    "inputs": {"image_1": _link("100"), "image_2": _link("94")},
                },
                "30": _notebook("First\nSecond"),
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "multiply", "sweep_mode": "aligned"},
                    "inputs": {"image": _link("101"), "text": _link("30", 0)},
                },
            }
        },
        "5",
        {"total": 8, "atLeast": False, "steps": 1, "pairs": 8, "unknowns": [], "error": None},
    ),
    (
        # The reviewer's second topology: LoraLoader has no INPUT_IS_LIST,
        # so core MAPS it over the switcher's 3-model list (one run per
        # element, one output each) -- the 3 must propagate THROUGH it.
        "map_over_list_lora_loader_propagates_switcher_fan_3",
        {
            "nodes": {
                "1": {
                    "classType": "EPSModelSwitcher",
                    "widgets": {"toggles": "{}"},
                    "inputs": {f"model_{i}": _link(str(10 + i)) for i in range(1, 4)},
                },
                **{
                    str(10 + i): {"classType": "UNETLoader", "widgets": {}, "inputs": {}}
                    for i in range(1, 4)
                },
                "60": {
                    "classType": "LoraLoader",
                    "widgets": {"lora_name": "x.safetensors", "strength_model": 1},
                    "inputs": {"model": _link("1", 0)},
                },
                "3": _text_source(),
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "paired", "sweep_mode": "aligned"},
                    "inputs": {"model": _link("60", 0), "text": _link("3")},
                },
            }
        },
        "5",
        {"total": 3, "atLeast": False, "steps": 3, "pairs": 1, "unknowns": [], "error": None},
    ),
    (
        # nodes_sweep.py also has no INPUT_IS_LIST: core maps sweep() over
        # its model/clip list inputs and concatenates the per-call plans --
        # plan (2 picker rows x 3 values = 6) x 2 switcher-fed models = 12.
        "iterator_plan_multiplies_by_its_mapped_model_fan",
        {
            "nodes": {
                "1": {
                    "classType": "EPSModelSwitcher",
                    "widgets": {"toggles": "{}"},
                    "inputs": {"model_1": _link("11"), "model_2": _link("12")},
                },
                "11": {"classType": "UNETLoader", "widgets": {}, "inputs": {}},
                "12": {"classType": "UNETLoader", "widgets": {}, "inputs": {}},
                "70": _PICKER_TWO_ENABLED,
                "71": {
                    "classType": "LoraLibrarySweep",
                    "widgets": {
                        "min": 0,
                        "max": 1,
                        "increment": 0.5,
                        "mode": "Each lora independently",
                    },
                    "inputs": {"model": _link("1", 0), "lora_stack": _link("70", 0)},
                },
                "3": _text_source(),
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "paired", "sweep_mode": "aligned"},
                    "inputs": {"model": _link("71", 0), "text": _link("3")},
                },
            }
        },
        "5",
        {"total": 12, "atLeast": False, "steps": 12, "pairs": 1, "unknowns": [], "error": None},
    ),
    (
        "reroutes_are_followed_through",
        {
            "nodes": {
                "20": _checkpoint_switcher(["a.st", "b.st", "c.st"]),
                "60": {"classType": "Reroute", "widgets": {}, "inputs": {"": _link("20", 0)}},
                "3": _text_source(),
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "paired", "sweep_mode": "aligned"},
                    "inputs": {"model": _link("60", 0), "text": _link("3")},
                },
            }
        },
        "5",
        {"total": 3, "atLeast": False, "steps": 3, "pairs": 1, "unknowns": [], "error": None},
    ),
    # ---- Round-2 review: BLOCKED/EMPTY PROPAGATION (cross_sweep.js header;
    # the governing backend rule is nodes_switcher.py check_lazy_status +
    # ComfyUI's one-blocker-blocks-the-consumer semantics).
    (
        # Repro 1: an EMPTY-selection Checkpoint Switcher is known-blocked
        # ([ExecutionBlocker] on every output) but is NOT a sibling
        # switcher, so switcher 1's check_lazy_status still requests the
        # slot and the blocker collapses it; switcher 1 in turn IS a
        # switcher class but is NOT statically all-off (its slot is
        # wired+enabled -- it merely leads nowhere), so nested switcher 2
        # collapses too, GOOD model_2 slot and all: {count: 0, atLeast:
        # false}, never "the empty slot contributes nothing".
        "empty_checkpoint_through_nested_switcher_collapses_all_of_it",
        {
            "nodes": {
                "20": _checkpoint_switcher([]),
                "1": {
                    "classType": "EPSModelSwitcher",
                    "widgets": {"toggles": "{}"},
                    "inputs": {"model_1": _link("20", 0)},
                },
                "2": {
                    "classType": "EPSModelSwitcher",
                    "widgets": {"toggles": "{}"},
                    "inputs": {"model_1": _link("1"), "model_2": _link("11")},
                },
                "11": {"classType": "UNETLoader", "widgets": {}, "inputs": {}},
                "3": _text_source(),
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "paired", "sweep_mode": "aligned"},
                    "inputs": {"model": _link("2"), "text": _link("3")},
                },
            }
        },
        "5",
        {
            "total": 0,
            "atLeast": False,
            "steps": 0,
            "pairs": 1,
            "error": None,
            "breakdown": "nothing to run (model input is empty/blocked)",
        },
    ),
    (
        # Repro 2: a switcher emptied by a DEAD-END reroute chain (no link
        # survives into the serialized prompt, so nothing is wired) feeding
        # switcher 101's only slot leaves 101 with zero enabled values --
        # the whole-node blocker path -- and the multiplier's image member
        # is KNOWN-zero: 0 runs, named.
        "dead_end_reroute_empty_switcher_feeding_a_switcher_is_zero",
        {
            "nodes": {
                "60": {"classType": "Reroute", "widgets": {}, "inputs": {}},
                "100": {
                    "classType": "EPSSwitcher",
                    "widgets": {"toggles": "{}"},
                    "inputs": {"image_1": _link("60")},
                },
                "101": {
                    "classType": "EPSSwitcher",
                    "widgets": {"toggles": "{}"},
                    "inputs": {"image_1": _link("100")},
                },
                "30": _notebook("First\nSecond"),
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "multiply", "sweep_mode": "aligned"},
                    "inputs": {"image": _link("101"), "text": _link("30", 0)},
                },
            }
        },
        "5",
        {
            "total": 0,
            "atLeast": False,
            "steps": 1,
            "pairs": 0,
            "error": None,
            "breakdown": "nothing to run (image input is empty/blocked)",
        },
    ),
    (
        # Repro 3: the ONE exemption. Switcher 100 is STATICALLY all-off
        # (its only wired slot literally toggled false) -- exactly the
        # shape a consuming switcher's check_lazy_status skips
        # (_switcher_is_statically_all_off): 100 is never requested, never
        # runs, never emits its blocker, so 101's OTHER slot still counts
        # and the total stays nonzero AND exact.
        "statically_all_off_switcher_into_a_slot_still_lazily_skips",
        {
            "nodes": {
                **_LOAD_IMAGES,
                "100": {
                    "classType": "EPSSwitcher",
                    "widgets": {"toggles": '{"image_1": false}'},
                    "inputs": {"image_1": _link("91")},
                },
                "101": {
                    "classType": "EPSSwitcher",
                    "widgets": {"toggles": "{}"},
                    "inputs": {"image_1": _link("100"), "image_2": _link("92")},
                },
                "30": _notebook("First\nSecond"),
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "multiply", "sweep_mode": "aligned"},
                    "inputs": {"image": _link("101"), "text": _link("30", 0)},
                },
            }
        },
        "5",
        {"total": 2, "atLeast": False, "steps": 1, "pairs": 2, "unknowns": [], "error": None},
    ),
    (
        # Repro 4: run() only reads `name` for save_prefix -- it never
        # enters the pair math -- but it IS a consumed input list, and one
        # blocker element in it blocks the whole node: a known-blocked name
        # source is 0 runs, not a confident 1.
        "blocked_name_source_is_nothing_to_run",
        {
            "nodes": {
                "20": _checkpoint_switcher([]),
                "3": _text_source(),
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "paired", "sweep_mode": "aligned"},
                    "inputs": {"text": _link("3"), "name": _link("20", 3)},
                },
            }
        },
        "5",
        {
            "total": 0,
            "atLeast": False,
            "steps": 1,
            "pairs": 1,
            "error": None,
            "breakdown": "nothing to run (name input is empty/blocked)",
        },
    ),
    (
        # Repro 5: per-lora with an UNKNOWABLE stack (Apply-Set reads a
        # file on disk). The only count build_sweep_plan GUARANTEES for an
        # unknowable stack is its empty-stack sentinel's single passthrough
        # -- an actually-empty stack sweeps ONCE, not `values` times -- so
        # the plan floors at 1 (>= 1 x pairs), never at values=3.
        "per_lora_unknowable_stack_floors_at_one_not_values",
        {
            "nodes": {
                "10": {"classType": "CheckpointLoaderSimple", "widgets": {}, "inputs": {}},
                "72": {"classType": "LoraLibraryApplySet", "widgets": {}, "inputs": {}},
                "71": {
                    "classType": "LoraLibrarySweep",
                    "widgets": {
                        "min": 0,
                        "max": 1,
                        "increment": 0.5,
                        "mode": "Each lora independently",
                    },
                    "inputs": {"model": _link("10", 0), "lora_stack": _link("72", 0)},
                },
                "3": _text_source(),
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "paired", "sweep_mode": "aligned"},
                    "inputs": {"model": _link("71", 0), "text": _link("3")},
                },
            }
        },
        "5",
        {
            "total": 1,
            "atLeast": True,
            "steps": 1,
            "pairs": 1,
            "unknowns": ["model"],
            "error": None,
        },
    ),
    (
        # Repro 6: run()'s v0.51.0 dead-output guard, mirrored at the
        # CONSUMPTION site -- the inner multiplier 41 has no model INPUT
        # wired, and the outer consumes its model OUTPUT (slot 0): the
        # inner's run() raises a queue-time ValueError, painted on the
        # OUTER readout. Its text output (slot 3) is consumed too, proving
        # the always-live slots (text/save_prefix) are exempt.
        "inner_multiplier_dead_model_output_paints_the_v051_error",
        {
            "nodes": {
                "3": _text_source(),
                "41": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "paired", "sweep_mode": "aligned"},
                    "inputs": {"text": _link("3")},
                },
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "paired", "sweep_mode": "aligned"},
                    "inputs": {"model": _link("41", 0), "text": _link("41", 3)},
                },
            }
        },
        "5",
        {
            "error_contains": [
                "model output",
                "model input is not wired",
                "queue will fail",
            ]
        },
    ),
    (
        # Repro 7 (ORDERING): aligned conflict AND a known-blocked label
        # together -- see _owner_shape_with_blocked_label's docstring. The
        # zero-collapse must win: nothing-to-run naming label, NOT the
        # conflict error (the queue actually SUCCEEDS with 0 runs).
        "conflict_plus_known_zero_is_nothing_to_run_not_error",
        _owner_shape_with_blocked_label(),
        "5",
        {
            "total": 0,
            "atLeast": False,
            "error": None,
            "breakdown": "nothing to run (label input is empty/blocked)",
        },
    ),
]

#: (estimate object, expected formatReadout result) -- pins the exact line
#: text and class for each of the readout's four states, singulars included.
_ALIGNED_ERROR = "sweep lengths disagree: model=4 vs vae=2 — the queue will fail"
READOUT_CASES = [
    (
        {
            "total": 8,
            "atLeast": False,
            "steps": 4,
            "stepsAtLeast": False,
            "pairs": 2,
            "pairsAtLeast": False,
            "unknowns": [],
            "error": None,
            "breakdown": "4 sweep steps × 2 pairs",
        },
        {"text": "Runs: 8 — 4 sweep steps × 2 pairs", "cls": ""},
    ),
    (
        {
            "total": 8,
            "atLeast": True,
            "steps": 4,
            "stepsAtLeast": True,
            "pairs": 2,
            "pairsAtLeast": False,
            "unknowns": ["model"],
            "error": None,
            "breakdown": "4 sweep steps × 2 pairs",
        },
        {
            "text": "Runs: ≥ 8 — 4 sweep steps × 2 pairs — model source unknown",
            "cls": "eps-rc-warn",
        },
    ),
    (
        {
            "total": 0,
            "atLeast": False,
            "steps": 4,
            "stepsAtLeast": False,
            "pairs": 2,
            "pairsAtLeast": False,
            "unknowns": [],
            "error": _ALIGNED_ERROR,
            "breakdown": "",
        },
        {"text": _ALIGNED_ERROR, "cls": "eps-rc-error"},
    ),
    (
        {
            "total": 0,
            "atLeast": False,
            "steps": 1,
            "stepsAtLeast": False,
            "pairs": 0,
            "pairsAtLeast": False,
            "unknowns": [],
            "error": None,
            "breakdown": "nothing to run",
        },
        {"text": "Runs: 0 — nothing to run", "cls": "eps-rc-warn"},
    ),
    (
        # Round-2 zero-collapse: a KNOWN-blocked member names the culprit
        # input in the breakdown, and the line carries it verbatim -- a
        # WARN, never an error (the queue succeeds with 0 runs).
        {
            "total": 0,
            "atLeast": False,
            "steps": 0,
            "stepsAtLeast": False,
            "pairs": 1,
            "pairsAtLeast": False,
            "unknowns": [],
            "error": None,
            "breakdown": "nothing to run (model input is empty/blocked)",
        },
        {
            "text": "Runs: 0 — nothing to run (model input is empty/blocked)",
            "cls": "eps-rc-warn",
        },
    ),
    (
        {
            "total": 1,
            "atLeast": False,
            "steps": 1,
            "stepsAtLeast": False,
            "pairs": 1,
            "pairsAtLeast": False,
            "unknowns": [],
            "error": None,
            "breakdown": "1 sweep step × 1 pair",
        },
        {"text": "Runs: 1 — 1 sweep step × 1 pair", "cls": ""},
    ),
]

#: (case name, raw `selection` widget value, expected ENABLED row count) --
#: pickerEnabledRowCount must match lora_library/nodes_picker.py
#: `_parse_selection` byte-for-byte on hostile JSON: `on` is PYTHON
#: truthiness with an absent-key default of True (0/null/''/[]/{} are all
#: DISABLED, not JS-Boolean); a non-coercible strength/strength_clip skips
#: the ROW (bool included -- `_coerce_strength` rejects it explicitly)
#: without entering the dedupe set; dedupe is on the RAW file string, no
#: separator normalization.
PICKER_CASES = [
    ("on_zero_is_disabled", json.dumps({"loras": [{"file": "a.st", "on": 0}]}), 0),
    ("on_null_is_disabled", json.dumps({"loras": [{"file": "a.st", "on": None}]}), 0),
    ("on_empty_string_is_disabled", json.dumps({"loras": [{"file": "a.st", "on": ""}]}), 0),
    # bool([]) is False in Python; Boolean([]) is true in JS -- the exact
    # divergence the byte-for-byte rule exists for.
    ("on_empty_list_is_disabled", json.dumps({"loras": [{"file": "a.st", "on": []}]}), 0),
    ("on_nonempty_string_is_enabled", json.dumps({"loras": [{"file": "a.st", "on": "no"}]}), 1),
    (
        "non_coercible_strength_skips_row",
        json.dumps({"loras": [{"file": "a.st", "strength": "abc"}]}),
        0,
    ),
    (
        "bool_strength_is_rejected_like_coerce_strength",
        json.dumps({"loras": [{"file": "a.st", "strength": True}]}),
        0,
    ),
    (
        "numeric_string_strength_coerces_like_python_float",
        json.dumps({"loras": [{"file": "a.st", "strength": " 0.5 "}]}),
        1,
    ),
    (
        "null_strength_clip_is_tolerated",
        json.dumps({"loras": [{"file": "a.st", "strength_clip": None}]}),
        1,
    ),
    (
        "non_coercible_strength_clip_skips_row",
        json.dumps({"loras": [{"file": "a.st", "strength_clip": []}]}),
        0,
    ),
    (
        "dedupe_is_on_the_raw_file_string_no_normalization",
        json.dumps({"loras": [{"file": "sub\\a.st"}, {"file": "sub/a.st"}]}),
        2,
    ),
    (
        "exact_raw_duplicate_first_parsed_wins",
        json.dumps({"loras": [{"file": "a.st"}, {"file": "a.st", "on": False}]}),
        1,
    ),
    (
        "skipped_duplicate_does_not_block_a_later_valid_row",
        json.dumps({"loras": [{"file": "a.st", "strength": "abc"}, {"file": "a.st"}]}),
        1,
    ),
]

PROBE_JS = """
import * as m from './extensions/comfyui-epsnodes/eps_image/cross_sweep.js'

const cases = %(estimate_inputs)s

const out = {
  exports: {
    hasInit: typeof m.init === 'function',
    hasAttach: typeof m.attach === 'function',
    hasEstimateRuns: typeof m.estimateRuns === 'function',
    hasFormatReadout: typeof m.formatReadout === 'function',
    hasSnapshotFromGraph: typeof m.snapshotFromGraph === 'function',
    hasSwitcherEnabledCount: typeof m.switcherEnabledCount === 'function',
    hasCheckpointSelectionCount: typeof m.checkpointSelectionCount === 'function',
    hasNotebookEntryCount: typeof m.notebookEntryCount === 'function',
    hasPickerEnabledRowCount: typeof m.pickerEnabledRowCount === 'function',
    hasIteratorValueCount: typeof m.iteratorValueCount === 'function'
  },
  constants: {
    classId: m.CLASS_ID,
    eventName: m.EVENT_NAME
  },
  estimates: cases.map(([snapshot, nodeId]) => m.estimateRuns(snapshot, nodeId)),
  readouts: %(readout_inputs)s.map((est) => m.formatReadout(est)),
  iteratorValues: [
    m.iteratorValueCount(0, 1, 0.1),
    m.iteratorValueCount(0, 1, 0.5),
    m.iteratorValueCount(0.5, 0.5, 0.1),
    m.iteratorValueCount(0, 1, 0),
    m.iteratorValueCount(1, 0, 0.1),
    m.iteratorValueCount(0, 0.5, 0.2),
    m.iteratorValueCount(0, 2.5, 1),
    m.iteratorValueCount(-1.35, -0.1, 0.1),
    m.iteratorValueCount(-1.04, 0.01, 0.1)
  ],
  pickerCounts: %(picker_inputs)s.map((raw) => m.pickerEnabledRowCount(raw))
}

process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def cross_sweep_api(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Runs the probe against the REAL cross_sweep.js in a served-layout tmp
    dir (see module docstring) and returns its JSON output."""
    layout = tmp_path_factory.mktemp("web_root")

    module_dir = layout / "extensions" / "comfyui-epsnodes" / "eps_image"
    module_dir.mkdir(parents=True)
    shutil.copyfile(CROSS_SWEEP_JS, module_dir / "cross_sweep.js")

    # cross_sweep.js's two imports -- `../../../scripts/api.js` and
    # `../../../scripts/app.js` -- stubbed exactly as test_picker_js.py
    # stubs them. Only the pure helpers run here, so no-op stubs suffice;
    # the relative DEPTH is the load-bearing part.
    scripts = layout / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "api.js").write_text(
        "export const api = { fetchApi: () => {}, addEventListener: () => {} }\n",
        encoding="utf-8",
    )
    (scripts / "app.js").write_text("export const app = {}\n", encoding="utf-8")

    probe = layout / "probe.mjs"
    probe.write_text(
        PROBE_JS
        % {
            "estimate_inputs": json.dumps(
                [[snapshot, node_id] for _, snapshot, node_id, _ in ESTIMATE_CASES]
            ),
            "readout_inputs": json.dumps([est for est, _ in READOUT_CASES]),
            "picker_inputs": json.dumps([raw for _, raw, _ in PICKER_CASES]),
        },
        encoding="utf-8",
    )

    result = subprocess.run(
        [NODE, str(probe)], capture_output=True, text=True, timeout=60, cwd=layout
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def source() -> str:
    """Raw text of cross_sweep.js -- for SOURCE-STRUCTURE assertions about
    code that only runs against a real litegraph node and a real DOM (the
    class gate, the DOM widget's serialize flags, the redraw-driven
    throttle, the toast listener) -- test_picker_js.py's identical
    convention."""
    return CROSS_SWEEP_JS.read_text(encoding="utf-8")


def _function_body(source_text: str, signature: str) -> str:
    """The body of a top-level ``function <signature> {`` declaration (an
    `export` prefix, if any, is not part of *signature*), up to its closing
    brace at column 0 -- test_picker_js.py's identical helper."""
    start_match = re.search(re.escape(f"function {signature} {{") + r"\n", source_text)
    assert start_match, f"function {signature} {{ not found"
    start = start_match.end()
    end_match = re.search(r"\n\}\n", source_text[start:])
    assert end_match, f"function {signature}'s closing brace not found"
    return source_text[start : start + end_match.start()]


# ------------------------------------------------------------- parses / exports


def test_cross_sweep_js_parses() -> None:
    """`node --check` -- the file must at minimum be valid ES module syntax."""
    result = subprocess.run(
        [NODE, "--check", str(CROSS_SWEEP_JS)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


def test_module_exports_the_hooks_and_pure_helpers(cross_sweep_api: dict) -> None:
    """web/eps_image.js consumes init()/attach(); the pure estimator and its
    per-source counters are the seams this file's own probe drives."""
    assert cross_sweep_api["exports"] == {
        "hasInit": True,
        "hasAttach": True,
        "hasEstimateRuns": True,
        "hasFormatReadout": True,
        "hasSnapshotFromGraph": True,
        "hasSwitcherEnabledCount": True,
        "hasCheckpointSelectionCount": True,
        "hasNotebookEntryCount": True,
        "hasPickerEnabledRowCount": True,
        "hasIteratorValueCount": True,
    }


def test_class_id_and_event_name_constants(cross_sweep_api: dict) -> None:
    """Pins the exact §6.10 contract shared with the backend: the frozen
    class id and the run()-side send_sync event name -- a typo on either
    side fails loudly instead of silently never toasting."""
    constants = cross_sweep_api["constants"]
    assert constants["classId"] == "EPSCrossSweep"
    assert constants["eventName"] == "eps-run-multiplier-count"


def test_event_name_appears_verbatim_in_both_layers(source: str) -> None:
    """The string must match nodes_cross_sweep.py's send_sync exactly."""
    assert "'eps-run-multiplier-count'" in source
    backend = (REPO_ROOT / "eps_image" / "nodes_cross_sweep.py").read_text(encoding="utf-8")
    assert '"eps-run-multiplier-count"' in backend


# ------------------------------------------------------------------ estimator


def test_estimate_runs_case_table(cross_sweep_api: dict) -> None:
    """The §6.10 estimator over snapshot fixtures -- the owner's exact
    4-model x 2-VAE shape in both sweep modes, every per-source count rule,
    the chained-multiplier recursion, the cycle guard, both early error
    paints, and the round-2 BLOCKED/EMPTY PROPAGATION repros (known-zero
    collapse vs the statically-all-off lazy-skip exemption, the blocked
    `name` member, the per-lora unknowable-stack floor, the dead-output
    paint, and zero-collapse suppressing the conflict error)."""
    pairs = zip(ESTIMATE_CASES, cross_sweep_api["estimates"], strict=True)
    for (name, _snapshot, _node_id, expected), got in pairs:
        for key, want in expected.items():
            if key == "error_contains":
                assert got["error"], f"{name}: expected an error, got {got!r}"
                for fragment in want:
                    assert fragment in got["error"], (
                        f"{name}: error {got['error']!r} lacks {fragment!r}"
                    )
            elif key == "unknowns":
                assert sorted(got["unknowns"]) == sorted(want), f"{name}: {got!r}"
            else:
                assert got[key] == want, f"{name}: {key} -> {got!r}, wanted {expected!r}"


def test_estimator_never_hangs_on_a_cycle(cross_sweep_api: dict) -> None:
    """The probe FINISHING is itself the cycle-guard proof (two multipliers
    feeding each other would otherwise recurse forever); the degraded
    result must be the honest `≥` floor, never a confident number."""
    by_name = {
        name: got
        for (name, _s, _n, _e), got in zip(
            ESTIMATE_CASES, cross_sweep_api["estimates"], strict=True
        )
    }
    cyclic = by_name["multiplier_cycle_guard_degrades_to_at_least_never_hangs"]
    assert cyclic["atLeast"] is True
    assert cyclic["error"] is None


def test_iterator_value_count_mirrors_step_values(cross_sweep_api: dict) -> None:
    """nodes_sweep.py `_step_values` parity: inclusive endpoints (0..1 by
    0.1 = 11), the degenerate single-value collapses, and -- the last four
    cases -- Python round()'s round-half-to-EVEN on an exactly-halfway
    quotient, where Math.round's half-up would say one MORE step:
    (0,0.5,0.2) and (0,2.5,1) quotient 2.5 -> 2 (3 values, not 4);
    (-1.35,-0.1,0.1) quotient 12.5 -> 12 (13, not 14); (-1.04,0.01,0.1)
    quotient 10.5 -> 10 (11, not 12). Bit-identical because JS and Python
    share IEEE-754 doubles."""
    assert cross_sweep_api["iteratorValues"] == [11, 3, 1, 1, 1, 3, 3, 13, 11]


def test_picker_enabled_row_count_matches_parse_selection(cross_sweep_api: dict) -> None:
    """nodes_picker.py `_parse_selection` parity on hostile JSON -- see
    PICKER_CASES' own comment for the exact rules (Python-truthiness `on`,
    row-skipping coercion failures, raw-string dedupe)."""
    pairs = zip(PICKER_CASES, cross_sweep_api["pickerCounts"], strict=True)
    for (name, raw, expected), got in pairs:
        assert got == expected, (
            f"{name}: pickerEnabledRowCount({raw!r}) -> {got!r}, wanted {expected!r}"
        )


def test_format_readout_cases(cross_sweep_api: dict) -> None:
    """Byte-exact pins for the readout line's states (plain / `≥` with the
    unknowable input named / error / nothing-to-run, both unnamed and with
    the round-2 empty/blocked input named) plus singulars."""
    pairs = zip(READOUT_CASES, cross_sweep_api["readouts"], strict=True)
    for (est, expected), got in pairs:
        assert got == expected, f"formatReadout({est!r}) -> {got!r}, wanted {expected!r}"


# ------------------------------------------------------------- toast listener


def test_toast_listener_is_wired_once_through_the_api_event(source: str) -> None:
    """§6.10 layer 2: ONE module-scope api.addEventListener on the backend
    event, guarded against double init (image_grid.js's install-once
    idiom), toasting through the pack's established surface."""
    body = _function_body(source, "init()")
    assert "if (countListenerInstalled) return" in body
    assert "countListenerInstalled = true" in body
    assert "api.addEventListener(EVENT_NAME" in body
    assert "app.extensionManager?.toast?.add" in source


def test_toast_message_is_the_contract_string(source: str) -> None:
    """"EPS Run Multiplier: <total> run(s) (<steps> sweep step(s) x <pairs>
    pair(s))" -- the §6.10 toast wording, total/steps/pairs from the event
    payload, gated on all three being real numbers."""
    body = _function_body(source, "init()")
    expected = (
        "`${NODE_TITLE}: ${total} run(s) "
        "(${steps} sweep step(s) × ${pairs} pair(s))`"
    )
    assert expected in body
    assert "Number.isFinite" in body
    assert "const NODE_TITLE = 'EPS Run Multiplier'" in source


# --------------------------------------------------------- readout DOM widget


def test_attach_gates_on_the_exact_class_id(source: str) -> None:
    body = _function_body(source, "attach(node)")
    assert "if (nodeClassOf(node) !== CLASS_ID) return" in body
    assert "export const CLASS_ID = 'EPSCrossSweep'" in source


def test_attach_guards_against_a_double_nodecreated(source: str) -> None:
    body = _function_body(source, "attach(node)")
    assert "if (attachedNodes.has(node)) return" in body
    assert "attachedNodes.add(node)" in body


def test_readout_widget_is_display_only_with_both_serialize_flags(source: str) -> None:
    """picker.js attachDomWidget's exact two-flag block: options.serialize
    excludes the API prompt, widget.serialize + serializeValue exclude the
    workflow JSON -- nothing may enter the file (§8: a serialized value
    would positionally shift widgets_values on downgrade)."""
    body = _function_body(source, "attach(node)")
    assert "serialize: false" in body
    assert "domWidget.serialize = false" in body
    assert "domWidget.serializeValue = () => undefined" in body


def test_readout_classes_are_the_contract_classes(source: str) -> None:
    assert "eps-rc-line" in source
    assert ".eps-rc-warn" in source
    assert ".eps-rc-error" in source


# ------------------------------------------------ refresh idiom (§6.3 / §7.5)


def test_all_three_refresh_triggers_share_the_wrapped_throttle(source: str) -> None:
    """§6.3's controller idiom, Vue-aware: onDrawForeground NEVER fires
    under the Vue renderer (project rule: no canvas drawing there), so the
    recompute rides THREE chained triggers -- the onDrawForeground wrap as
    the litegraph catch-all, plus onConnectionsChange and the two mode
    widgets' callbacks, which exist in BOTH renderers -- all through the
    SAME wrapWithRecompute -> maybeRecompute throttle. No polling loop."""
    body = _function_body(source, "attach(node)")
    assert "node.onDrawForeground = wrapWithRecompute(node.onDrawForeground, state)" in body
    assert (
        "node.onConnectionsChange = wrapWithRecompute(node.onConnectionsChange, state)" in body
    )
    assert "for (const name of ['pair_mode', 'sweep_mode'])" in body
    assert "widget.callback = wrapWithRecompute(widget.callback, state)" in body


def test_trigger_wrapper_chains_originals_and_never_throws(source: str) -> None:
    """wrapWithRecompute must WRAP, never replace: the original hook or
    widget callback (possibly undefined) runs first with its own
    this/arguments, its return value passes through, and the recompute is
    try/caught so it can never throw out of a draw or a callback."""
    helper = _function_body(source, "wrapWithRecompute(original, state)")
    assert "original.apply(this, arguments)" in helper
    assert "maybeRecompute(state)" in helper
    assert "try {" in helper
    assert "} catch (error) {" in helper
    assert "return result" in helper


def test_throttle_constant_and_gate(source: str) -> None:
    """controller.js's HEARTBEAT_MIN_MS shape at the contract's ~500ms."""
    assert "const READOUT_MIN_INTERVAL_MS = 500" in source
    body = _function_body(source, "maybeRecompute(state)")
    assert "if (now - state.lastStamp < READOUT_MIN_INTERVAL_MS) return" in body


def test_no_window_listeners_and_no_interval_timers(source: str) -> None:
    """§7.5 (window listeners) and §6.10's "no polling timers of its own"
    -- neither may appear even once. setTimeout appears EXACTLY once: the
    one-shot post-add deferral (at nodeCreated the node has no id yet;
    the Vue renderer never self-heals via a redraw -- 2026-08-14), which
    is a deferral, not a polling timer."""
    assert "window.addEventListener" not in source
    assert "setInterval" not in source
    assert source.count("setTimeout(") == 1
    attach = _function_body(source, "attach(node)")
    assert "setTimeout(() => {" in attach  # the documented deferral, nowhere else


def test_recompute_repaints_only_on_change(source: str) -> None:
    """A busy canvas redraws constantly; identical text must not thrash the
    DOM."""
    body = _function_body(source, "recompute(state)")
    assert "if (view.text === state.lastText && view.cls === state.lastCls) {" in body
    assert "estimateRuns(snapshotFromGraph(graph), String(state.node.id))" in body


def test_adapter_injects_the_live_image_grid_count(source: str) -> None:
    """The one live-only fact: the estimator stays snapshot-pure while the
    adapter reads the litegraph node's own imgs length (§6.10: Emit's
    buffer count is server state, not graph state)."""
    body = _function_body(source, "snapshotFromGraph(graph)")
    assert "Number.isFinite(node.imgs?.length)" in body
    assert "entry.imageGridCount = node.imgs.length" in body


# ----------------------------------------------------------- extension entry


def test_entry_file_wires_init_and_attach(source: str) -> None:
    """The eps_image.js hookup this readout ships with -- the import, the
    init() call, and the safely() attach inside nodeCreated, in the entry
    file's own style."""
    entry = ENTRY_JS.read_text(encoding="utf-8")
    assert "import * as crossSweep from './eps_image/cross_sweep.js'" in entry
    assert "safely('crossSweep.init', () => crossSweep.init?.())" in entry
    assert "safely('crossSweep.attach', () => crossSweep.attach?.(node))" in entry


def test_readout_sizes_itself_the_compact_standalone_way(source: str) -> None:
    """Owner report 2026-08-09 ("cropped ... about 1/3 the space it needs
    so it's not legible"): a COMPACT STANDALONE addDOMWidget is not sized
    by getMinHeight/getMaxHeight alone -- the row collapses to ~7px and
    clips the text. The cprb v0.5.0 root-cause and its exact fix: size
    through computeSize + computedHeight + an explicit element height.
    (The picker/notebook panels never hit this because they are FILL
    widgets riding the node's spare height.)"""
    body = _function_body(source, "attach(node)")
    assert "root.style.height = `${READOUT_HEIGHT}px`" in body
    # v0.61.3 (owner report 2026-08-14, "still cropped"): every height
    # REPORTED to litegraph budgets the overlay's 2*margin, because the
    # visible box is `computedHeight - margin*2` (DomWidgets.vue) -- the
    # bare text height left a 6px window. The element keeps the text half.
    assert "state.outerHeight = READOUT_HEIGHT + 2 * margin" in body
    assert "domWidget.computeSize = (width) => [width, state.outerHeight]" in body
    assert "domWidget.computedHeight = state.outerHeight" in body
    # The belt-and-braces hints stay for renderers that DO honor them.
    assert "getMinHeight: () => state.outerHeight" in body


def test_readout_wraps_and_grows_to_fit_long_messages(source: str) -> None:
    """Owner report 2026-08-14: long floor/error messages must be readable,
    not ellipsized -- the line wraps and sizeToContent grows the box (and
    the node) to fit, capped at READOUT_MAX_HEIGHT, never acting on a 0
    measurement (the §7.5 frozen-RAF artifact)."""
    assert "white-space: normal; overflow-wrap: anywhere;" in source
    assert "text-overflow" not in source
    body = _function_body(source, "sizeToContent(state)")
    # 0-height = hidden/between-frames; 0-WIDTH = pre-layout, where every
    # character wraps and scrollHeight reads hundreds of px -- neither may
    # be trusted, and the skipped pass retries via needsMeasure.
    assert "if (!scrollH || !lineEl.clientWidth) {" in body
    assert "state.needsMeasure = true" in body
    recompute_body = _function_body(source, "recompute(state)")
    # ...and a stale-WIDTH measurement re-sizes too: wrapping depends on
    # width, so a pass taken mid-layout (or before a node resize) must be
    # redone once the width settles.
    assert (
        "if (state.needsMeasure || state.lineEl.clientWidth !== state.lastMeasuredWidth)"
        in recompute_body
    )
    assert "state.lastMeasuredWidth = lineEl.clientWidth" in body
    assert "Math.min(scrollH + 6, READOUT_MAX_HEIGHT)" in body
    assert "if (needed === state.textHeight) return" in body
    assert "Math.max(node.size[1] + (state.outerHeight - previousOuter), floor)" in body
    assert "sizeToContent(state)" in _function_body(source, "recompute(state)")
