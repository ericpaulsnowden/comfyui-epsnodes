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
from nested_layout import build_layout, run_probe

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


def _pinned(*names: str) -> str:
    """A provenance-M3 `pinned` widget value naming *names* (FORMAT.md §6.1:
    `{format, entries: [{name, text}], source: {file, token, captured}}`)."""
    return json.dumps(
        {
            "format": 1,
            "entries": [{"name": n, "text": f"{n} body"} for n in names],
            "source": {"file": "loras.md", "token": "m1_t1", "captured": "2026-08-21T10:00:00"},
        }
    )


def _builder(inputs: dict) -> dict:
    """§6.15 Prompt Builder: passes the incoming text axis through (one
    combined output per incoming text); unwired text input -> exactly 1."""
    return {
        "classType": "EPSPromptBuilder",
        "widgets": {"file": "loras.md", "blocks": '["Neon City"]', "separator": ", "},
        "inputs": inputs,
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


def _owner_shape_solo(token: str, sweep_mode: str = "multiply") -> dict:
    """v0.67.0 provenance M1: the owner shape with a `solo_run` token --
    text-only pairs, so valid multiply tokens are `m{1..4}_v{1..2}_t1`."""
    shape = _owner_shape(sweep_mode)
    shape["nodes"]["5"]["widgets"]["solo_run"] = token
    return shape


def _chained_solo_shape() -> dict:
    """A solo'd multiplier feeding a second multiplier's model input: run()
    truly emits ONE model, so the downstream count must be 1, not the full
    set of 8 -- the reason estimateInner folds solo into `total` instead of
    only dressing the readout."""
    shape = _owner_shape_solo("m3_v2_t1")
    shape["nodes"]["6"] = {
        "classType": "EPSCrossSweep",
        "widgets": {"base_folder": "", "pair_mode": "paired", "sweep_mode": "aligned"},
        "inputs": {"model": _link("5", 0), "text": _link("3")},
    }
    return shape


def _third_party(inputs: dict, **flags) -> dict:
    """A class the estimator has never heard of ("Krea2TEnhancer"), with
    the adapter-injected list flags (v0.68.0) -- or none, for the
    unknowable baseline."""
    return {"classType": "Krea2TEnhancer", "widgets": {}, "inputs": inputs, **flags}


def _models_through(enhancer: dict) -> dict:
    """3-tick Checkpoint Switcher -> enhancer -> multiplier.model, one text."""
    return {
        "nodes": {
            "20": _checkpoint_switcher(["a.st", "b.st", "c.st"]),
            "3": _text_source(),
            "en": enhancer,
            "5": {
                "classType": "EPSCrossSweep",
                "widgets": {"base_folder": "", "pair_mode": "multiply", "sweep_mode": "multiply"},
                "inputs": {"model": _link("en", 0), "text": _link("3")},
            },
        }
    }


def _blocked_models_through() -> dict:
    """The mapped-enhancer shape over an EMPTY-selection Checkpoint Switcher
    (known-blocked upstream)."""
    shape = _models_through(
        _third_party({"model": _link("20", 0)}, inputIsList=False, outputIsList=[False])
    )
    shape["nodes"]["20"] = _checkpoint_switcher([])
    return shape


def _image_grid_emit(count: int) -> dict:
    """§6.6 Image Grid in Emit mode with the adapter's injected live buffer
    count (a client-side echo of server state, so always a FLOOR)."""
    return {"classType": "EPSImageGrid", "widgets": {"mode": "Emit"}, "inputs": {},
            "imageGridCount": count}


def _resolution(inputs: dict, presets: str = "[]") -> dict:
    """§6.5 EPS Resolution (v0.67.1 in the estimator): mapped over its
    longest list input x max(1, selected presets)."""
    return {
        "classType": "EPSResolution",
        "widgets": {"width": 1024, "height": 1024, "presets": presets},
        "inputs": inputs,
    }


def _resolution_chain(resolution_inputs: dict, extra_nodes: dict, presets: str = "[]") -> dict:
    """Text source + a Resolution (slot 1 = resized_image) feeding a pair-
    multiply multiplier's image input -- the owner's exact report shape."""
    nodes = {
        "3": _text_source(),
        "res": _resolution(resolution_inputs, presets),
        "5": {
            "classType": "EPSCrossSweep",
            "widgets": {"base_folder": "", "pair_mode": "multiply", "sweep_mode": "multiply"},
            "inputs": {"image": _link("res", 1), "text": _link("3")},
        },
    }
    nodes.update(extra_nodes)
    return {"nodes": nodes}


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
        # §6.6 focus: a focused frame narrows Emit to exactly one image --
        # count 1, EXACT (no >= floor), regardless of the adapter's echo.
        "emit_grid_with_focus_counts_exactly_one",
        {
            "nodes": {
                "30": _notebook("First\nSecond\n\n"),
                "31": {
                    "classType": "EPSImageGrid",
                    "widgets": {"mode": "Emit", "focus": "frame-abc"},
                    "inputs": {},
                },
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
            "atLeast": False,
            "steps": 1,
            "pairs": 2,
            "unknowns": [],
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
        # v0.98.0: Collect only is a TRUE known-blocked branch (unlike plain
        # Collect, which is merely a §6.10 policy floor -- see the
        # estimator's own comment) -- same zero-collapse shape either way.
        "collect_only_grid_counts_zero_and_blocks_the_same_way_as_collect",
        {
            "nodes": {
                "30": _notebook("First\nSecond"),
                "31": {
                    "classType": "EPSImageGrid",
                    "widgets": {"mode": "Collect only"},
                    "inputs": {},
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
            "total": 0,
            "atLeast": False,
            "steps": 1,
            "pairs": 0,
            "error": None,
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
        # §6.15 Prompt Builder passes the incoming text axis through: a
        # 3-line notebook piped into it still means 3 runs downstream.
        "builder_passes_the_notebook_axis_through",
        {
            "nodes": {
                "30": _notebook("First\nSecond\nThird"),
                "40": _builder({"text": _link("30", 0), "name": _link("30", 1)}),
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "paired", "sweep_mode": "aligned"},
                    "inputs": {"text": _link("40", 0), "name": _link("40", 1)},
                },
            }
        },
        "5",
        {"total": 3, "atLeast": False, "steps": 1, "pairs": 3, "unknowns": [], "error": None},
    ),
    (
        # Unwired text input: the builder emits exactly ONE combined prompt
        # from its blocks alone -- never zero, never unknown.
        "builder_with_no_text_input_is_exactly_one",
        {
            "nodes": {
                "40": _builder({}),
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "paired", "sweep_mode": "aligned"},
                    "inputs": {"text": _link("40", 0)},
                },
            }
        },
        "5",
        {"total": 1, "atLeast": False, "steps": 1, "pairs": 1, "unknowns": [], "error": None},
    ),
    (
        # An empty notebook piped into the builder is still that queue's
        # error -- the builder consumes the raising upstream, so the error
        # paint must survive the pass-through.
        "builder_propagates_the_empty_notebook_error",
        {
            "nodes": {
                "90": {"classType": "LoraLibraryNotebook", "widgets": {"entry": ""}, "inputs": {}},
                "40": _builder({"text": _link("90", 0)}),
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "paired", "sweep_mode": "aligned"},
                    "inputs": {"text": _link("40", 0)},
                },
            }
        },
        "5",
        {"error": "EPS Prompt Notebook has no entry selected — the queue will fail"},
    ),
    (
        # Provenance M3: a baked image's workflow pins the Notebook -- the
        # backend outputs the PINNED entries (3 here) and ignores `entry`
        # (2 lines), so the estimate must say 3, not 2.
        "pinned_notebook_counts_the_pin_not_the_live_entry_lines",
        {
            "nodes": {
                "90": {
                    "classType": "LoraLibraryNotebook",
                    "widgets": {
                        "file": "loras.md",
                        "entry": "First\nSecond",
                        "pinned": _pinned("A", "B", "C"),
                    },
                    "inputs": {},
                },
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "paired", "sweep_mode": "aligned"},
                    "inputs": {"text": _link("90", 0)},
                },
            }
        },
        "5",
        {"total": 3, "atLeast": False, "steps": 1, "pairs": 3, "unknowns": [], "error": None},
    ),
    (
        # CHAINING (§6.1, owner ask 2026-09-09): a BUILDER feeding the
        # Notebook is ONE incoming prompt, so the count must not move -- the
        # owner's own words, "for the notebook to run the number of times it
        # would have already run".
        "notebook_chained_from_a_builder_keeps_its_own_count",
        {
            "nodes": {
                "80": {
                    "classType": "EPSPromptBuilder",
                    "widgets": {"file": "loras.md", "blocks": '["A","B"]'},
                    "inputs": {},
                },
                "90": {
                    "classType": "LoraLibraryNotebook",
                    "widgets": {"file": "loras.md", "entry": "First\nSecond\nThird"},
                    "inputs": {"text": _link("80", 0)},
                },
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "paired", "sweep_mode": "aligned"},
                    "inputs": {"text": _link("90", 0)},
                },
            }
        },
        "5",
        {"total": 3, "atLeast": False, "steps": 1, "pairs": 3, "unknowns": [], "error": None},
    ),
    (
        # CHAINING: notebook -> notebook MULTIPLIES. 2 selected upstream x 3
        # selected here = 6, not 3. Undercounting here is the failure the
        # owner is most exposed to, since he queues hundreds of images.
        "notebook_chained_from_a_notebook_multiplies",
        {
            "nodes": {
                "80": {
                    "classType": "LoraLibraryNotebook",
                    "widgets": {"file": "loras.md", "entry": "Up1\nUp2"},
                    "inputs": {},
                },
                "90": {
                    "classType": "LoraLibraryNotebook",
                    "widgets": {"file": "loras.md", "entry": "First\nSecond\nThird"},
                    "inputs": {"text": _link("80", 0)},
                },
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "paired", "sweep_mode": "aligned"},
                    "inputs": {"text": _link("90", 0)},
                },
            }
        },
        "5",
        {"total": 6, "atLeast": False, "steps": 1, "pairs": 6, "unknowns": [], "error": None},
    ),
    (
        # Provenance M3: an EMPTY live selection under a valid pin is NOT the
        # "no entry selected" queue-fail -- the pin is what executes.
        "pinned_notebook_with_empty_entry_is_not_a_queue_fail",
        {
            "nodes": {
                "90": {
                    "classType": "LoraLibraryNotebook",
                    "widgets": {"file": "loras.md", "entry": "", "pinned": _pinned("Only")},
                    "inputs": {},
                },
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "paired", "sweep_mode": "aligned"},
                    "inputs": {"text": _link("90", 1)},
                },
            }
        },
        "5",
        {"total": 1, "atLeast": False, "steps": 1, "pairs": 1, "unknowns": [], "error": None},
    ),
    (
        # Provenance M3: an unparseable `pinned` (e.g. "" after Unpin, or a
        # stray non-pin string) falls back to the live entry lines exactly.
        "unpinned_or_invalid_pin_falls_back_to_live_entry_lines",
        {
            "nodes": {
                "90": {
                    "classType": "LoraLibraryNotebook",
                    "widgets": {
                        "file": "loras.md",
                        "entry": "First\nSecond",
                        "pinned": "not a pin",
                    },
                    "inputs": {},
                },
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {"pair_mode": "paired", "sweep_mode": "aligned"},
                    "inputs": {"text": _link("90", 0)},
                },
            }
        },
        "5",
        {"total": 2, "atLeast": False, "steps": 1, "pairs": 2, "unknowns": [], "error": None},
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
    # ------------------------------------------------ v0.67.0 solo_run (M1)
    (
        # A valid token collapses the total to the ONE run the backend will
        # emit; the full set size moves to soloOf for the readout.
        "solo_valid_token_is_one_of_eight",
        _owner_shape_solo("m3_v2_t1"),
        "5",
        {
            "total": 1, "atLeast": False, "steps": 8, "pairs": 1,
            "solo": "m3_v2_t1", "soloOf": 8, "soloOfAtLeast": False,
            "error": None,
        },
    ),
    (
        # Out-of-bounds index: run() raises at queue time, the estimator
        # paints that failure early (all counts here are exact, so the
        # validation is definitive).
        "solo_out_of_bounds_is_the_queue_error_early",
        _owner_shape_solo("m9_v1_t1"),
        "5",
        {"error_contains": ["solo_run", "m9_v1_t1", "8 runs", "queue will fail"]},
    ),
    (
        # Wrong grammar for the modes: aligned mode has no independent vae
        # axis, so a _v fragment can never match (run()'s _run_token never
        # emits one there).
        "solo_v_fragment_in_aligned_mode_never_matches",
        {
            "nodes": {
                "20": _checkpoint_switcher(["a.st", "b.st", "c.st"]),
                "3": _text_source(),
                "5": {
                    "classType": "EPSCrossSweep",
                    "widgets": {
                        "base_folder": "", "pair_mode": "paired",
                        "sweep_mode": "aligned", "solo_run": "m2_v1_t1",
                    },
                    "inputs": {"model": _link("20"), "text": _link("3")},
                },
            }
        },
        "5",
        {"error_contains": ["solo_run", "m2_v1_t1", "queue will fail"]},
    ),
    (
        # Chained: the solo'd upstream emits ONE model, so downstream is 1
        # run, not 8 -- see _chained_solo_shape's docstring.
        "chained_downstream_of_a_solo_multiplier_counts_one",
        _chained_solo_shape(),
        "6",
        {"total": 1, "atLeast": False, "steps": 1, "pairs": 1, "error": None},
    ),
    # ------------------------------------ v0.67.1 EPS Resolution pass-through
    (
        # Owner report: "if an image grid is run through a resolution node
        # before going to a run multiplier, then the multiplier can't count
        # the images". Resolution is mapped over the grid's fan -> the
        # grid's count comes through (still a floor -- the grid echo is),
        # and `image` is no longer an UNKNOWN input.
        "grid_through_resolution_counts_the_grid",
        _resolution_chain({"image": _link("grid")}, {"grid": _image_grid_emit(3)}),
        "5",
        # unknowns lists `image` exactly as the DIRECT grid case above does
        # (a floor is reported as "source unknown" -- parity with wiring
        # the grid straight in is the whole point).
        {"total": 3, "atLeast": True, "pairs": 3, "pairsAtLeast": True,
         "unknowns": ["image"], "error": None},
    ),
    (
        # Bonus shape: Grid -> Switcher -> Resolution -> multiplier. The
        # switcher sums its slots (grid 3 + one LoadImage 1 = 4) and the
        # Resolution maps over that.
        "grid_through_switcher_through_resolution_counts_four",
        _resolution_chain(
            {"image": _link("sw")},
            {
                "grid": _image_grid_emit(3),
                "91": _LOAD_IMAGES["91"],
                "sw": {
                    "classType": "EPSSwitcher",
                    "widgets": {"toggles": "{}"},
                    "inputs": {"image_1": _link("grid"), "image_2": _link("91")},
                },
            },
        ),
        "5",
        {"total": 4, "atLeast": True, "pairs": 4, "unknowns": ["image"], "error": None},
    ),
    (
        # Two size presets ticked = resolve() runs once per preset: a single
        # LoadImage becomes 2 exact pairs.
        "resolution_two_presets_doubles_one_image",
        _resolution_chain({"image": _link("91")}, {"91": _LOAD_IMAGES["91"]}, '["A", "B"]'),
        "5",
        {"total": 2, "atLeast": False, "pairs": 2, "unknowns": [], "error": None},
    ),
    (
        # No image wired into the Resolution: its resized_image output is a
        # per-run ExecutionBlocker, so the multiplier is blocked -- the
        # known-zero family, naming the input.
        "resolution_with_no_image_is_known_zero",
        _resolution_chain({}, {}),
        "5",
        {"total": 0, "atLeast": False, "error": None,
         "breakdown": "nothing to run (image input is empty/blocked)"},
    ),
    # ------------------------------ v0.68.0 generic list flags (any node)
    (
        # Owner ask: 3 models through a third-party enhancer "only shows 1".
        # With the route's flags (plain node: INPUT_IS_LIST false, output
        # not a list) it is MAPPED over the switcher's 3 -> 3 exact steps.
        "third_party_mapped_node_counts_its_longest_list_input",
        _models_through(_third_party({"model": _link("20", 0)},
                                     inputIsList=False, outputIsList=[False])),
        "5",
        {"total": 3, "atLeast": False, "steps": 3, "pairs": 1, "unknowns": [], "error": None},
    ),
    (
        # No flags (route not answered): the pre-v0.68.0 posture, unknowable.
        "third_party_without_flags_stays_unknowable",
        _models_through(_third_party({"model": _link("20", 0)})),
        "5",
        {"total": 1, "atLeast": True, "steps": 1, "unknowns": ["model"], "error": None},
    ),
    (
        # A FLATTENER (INPUT_IS_LIST true, plain output) executes once and
        # emits exactly one -- 1 EXACT, not a floor.
        "third_party_flattener_is_exactly_one",
        _models_through(_third_party({"model": _link("20", 0)},
                                     inputIsList=True, outputIsList=[False])),
        "5",
        {"total": 1, "atLeast": False, "steps": 1, "unknowns": [], "error": None},
    ),
    (
        # OUTPUT_IS_LIST on the consumed slot: it emits a list of its own
        # choosing -- unknowable, honest floor.
        "third_party_list_output_is_a_floor",
        _models_through(_third_party({"model": _link("20", 0)},
                                     inputIsList=False, outputIsList=[True])),
        "5",
        {"total": 1, "atLeast": True, "unknowns": ["model"], "error": None},
    ),
    (
        # A mapped node whose upstream is known-blocked is blocked outright.
        "third_party_mapped_node_over_a_blocked_upstream_is_zero",
        _blocked_models_through(),
        "5",
        {"total": 0, "atLeast": False, "error": None},
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
    (
        # v0.67.0 solo -- warn-painted even when VALID: solo is a mode you
        # can forget you left on.
        {
            "total": 1, "atLeast": False, "steps": 8, "stepsAtLeast": False,
            "pairs": 1, "pairsAtLeast": False, "unknowns": [], "error": None,
            "breakdown": "8 sweep steps × 1 pair",
            "solo": "m3_v2_t1", "soloOf": 8, "soloOfAtLeast": False,
        },
        {"text": "Solo m3_v2_t1 — 1 of 8 runs", "cls": "eps-rc-warn"},
    ),
    (
        # An unknowable set size keeps the honest floor in the "of N".
        {
            "total": 1, "atLeast": False, "steps": 8, "stepsAtLeast": True,
            "pairs": 1, "pairsAtLeast": False, "unknowns": ["model"], "error": None,
            "breakdown": "8 sweep steps × 1 pair",
            "solo": "m3_v2_t1", "soloOf": 8, "soloOfAtLeast": True,
        },
        {"text": "Solo m3_v2_t1 — 1 of ≥ 8 runs", "cls": "eps-rc-warn"},
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

#: (case name, raw `presets` widget value, expected resolutionPresetCount)
#: -- mirrors nodes_resolution.py `_parse_preset_names` byte-for-byte:
#: strings kept in order WITHOUT dedupe, non-strings dropped, malformed /
#: non-array / empty -> 0 ("no presets selected").
RESOLUTION_PRESET_CASES = [
    ("empty_string_is_none", "", 0),
    ("empty_array_is_none", "[]", 0),
    ("one_name", '["A"]', 1),
    ("two_names", '["A", "B"]', 2),
    ("duplicates_are_NOT_deduped_like_the_backend", '["A", "A"]', 2),
    ("non_strings_dropped", '["A", 5, null, "B"]', 2),
    ("non_array_json_is_none", '{"A": 1}', 0),
    ("malformed_is_none", "not json", 0),
]

#: (case name, raw `pinned` widget value, expected notebookPinnedCount) --
#: mirrors notebook.js `parsePinned`'s acceptance rule: a JSON object whose
#: `entries` holds >= 1 `{name: string}` item counts those items; anything
#: else is null (= fall back to the live `entry` line count).
NOTEBOOK_PINNED_CASES = [
    ("empty_string_is_live", "", None),
    ("whitespace_is_live", "  ", None),
    ("not_json_is_live", "nope", None),
    ("legacy_tag_string_is_live", "(any)", None),
    ("array_is_live", "[1, 2]", None),
    ("no_entries_key_is_live", json.dumps({"format": 1}), None),
    ("empty_entries_is_live", json.dumps({"entries": []}), None),
    ("nameless_entries_are_not_counted", json.dumps({"entries": [{"text": "x"}, 5, None]}), None),
    ("one_entry", _pinned("A"), 1),
    ("three_entries", _pinned("A", "B", "C"), 3),
    (
        "named_items_counted_nameless_skipped",
        json.dumps({"entries": [{"name": "A"}, {"text": "no"}, {"name": "B", "text": "t"}]}),
        2,
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
    hasNotebookPinnedCount: typeof m.notebookPinnedCount === 'function',
    hasPickerEnabledRowCount: typeof m.pickerEnabledRowCount === 'function',
    hasIteratorValueCount: typeof m.iteratorValueCount === 'function',
    hasResolutionPresetCount: typeof m.resolutionPresetCount === 'function'
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
  pickerCounts: %(picker_inputs)s.map((raw) => m.pickerEnabledRowCount(raw)),
  resolutionPresetCounts: %(resolution_preset_inputs)s.map((raw) => m.resolutionPresetCount(raw)),
  notebookPinnedCounts: %(notebook_pinned_inputs)s.map((raw) => m.notebookPinnedCount(raw))
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
    # v1.2.0 nested reach: cross_sweep.js imports the shared boundary
    # helpers from `../lora_library/api.js` (which pulls `./version.js`), so
    # a fixture that byte-copies only the one module no longer resolves.
    library_dir = layout / "extensions" / "comfyui-epsnodes" / "lora_library"
    library_dir.mkdir(parents=True)
    for shared in ("api.js", "version.js"):
        shutil.copyfile(REPO_ROOT / "web" / "lora_library" / shared, library_dir / shared)

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
            "resolution_preset_inputs": json.dumps(
                [raw for _, raw, _ in RESOLUTION_PRESET_CASES]
            ),
            "notebook_pinned_inputs": json.dumps([raw for _, raw, _ in NOTEBOOK_PINNED_CASES]),
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
        "hasNotebookPinnedCount": True,
        "hasPickerEnabledRowCount": True,
        "hasIteratorValueCount": True,
        "hasResolutionPresetCount": True,
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


def test_resolution_preset_count_mirrors_parse_preset_names(cross_sweep_api: dict) -> None:
    """v0.67.1: the Resolution branch's K factor must match
    nodes_resolution.py `_parse_preset_names` exactly -- including NOT
    deduping (the UI never writes duplicates, but the backend would run
    them twice, so the estimate must too)."""
    for (name, _raw, expected), got in zip(
        RESOLUTION_PRESET_CASES, cross_sweep_api["resolutionPresetCounts"], strict=True
    ):
        assert got == expected, f"{name}: got {got!r}, wanted {expected!r}"


def test_notebook_pinned_count_mirrors_parse_pinned(cross_sweep_api: dict) -> None:
    """Provenance M3: `notebookPinnedCount(raw)` -> the pin's named-entry
    count, or null for anything that is not a valid pin (so the Notebook
    branch falls back to `notebookEntryCount(entry)`); the acceptance rule
    is notebook.js `parsePinned`'s, kept in sync by hand."""
    for (name, _raw, expected), got in zip(
        NOTEBOOK_PINNED_CASES, cross_sweep_api["notebookPinnedCounts"], strict=True
    ):
        assert got == expected, f"{name}: got {got!r}, wanted {expected!r}"


def test_notebook_branch_prefers_the_pin_over_the_live_lines(source: str) -> None:
    """Structural pin for the M3 Notebook branch: the pinned count wins when
    present, the live `entry` line count otherwise, and the zero-lines
    queue-fail paint stays downstream of that choice."""
    body = _function_body(source, "sourceCount(snapshot, link, path)")
    branch = body.split("if (type === 'LoraLibraryNotebook') {", 1)[1]
    branch = branch.split("\n  if (type === ", 1)[0]
    assert "const pinnedLines = notebookPinnedCount(node.widgets?.pinned)" in branch
    assert "const lines = pinnedLines ?? notebookEntryCount(node.widgets?.entry)" in branch
    assert branch.index("notebookPinnedCount(") < branch.index("if (lines === 0) {")


def test_resolution_branch_is_mapped_not_flattened(source: str) -> None:
    """v0.67.1 structural pins: the Resolution branch maps over its longest
    list input (max, not sum -- nodes_resolution.py has no INPUT_IS_LIST),
    multiplies by max(1, presets), and treats an image-typed output with
    its backing image input unwired as the known-zero family (the
    per-run `_resized(None)` blocker)."""
    body = _function_body(source, "sourceCount(snapshot, link, path)")
    assert "if (type === 'EPSResolution') {" in body
    branch = body.split("if (type === 'EPSResolution') {", 1)[1]
    branch = branch.split("if (CORE_SINGLE_CLASSES.has(type))", 1)[0]
    assert "mapLen = Math.max(mapLen, inner.count)" in branch
    assert "Math.max(1, resolutionPresetCount(node.widgets?.presets))" in branch
    assert "slot <= 1 ? 'image' : slot >= 6 ? `image_${slot - 4}` : null" in branch
    assert "return { count: 0, atLeast: false, srcId: id }" in branch


def test_list_flags_are_fetched_once_and_injected_by_the_adapter(source: str) -> None:
    """v0.68.0: init() kicks off ONE fetch of the pack's `/eps/list_flags`;
    the adapter injects each class's flags onto the snapshot entry; the
    generic branch keys off exactly those two fields, and a late answer
    recomputes every graph (root + subgraphs)."""
    assert "export const LIST_FLAGS_ROUTE = '/eps/list_flags'" in source
    init = _function_body(source, "init()")
    assert "loadListFlags()" in init
    loader = _function_body(source, "loadListFlags()")
    assert "api.fetchApi(LIST_FLAGS_ROUTE)" in loader
    assert "recomputeEveryGraph()" in loader
    adapter = _function_body(source, "snapshotFromGraph(graph)")
    assert "const flags = listFlags?.get(classType)" in adapter
    assert "entry.inputIsList = flags.inputIsList" in adapter
    assert "entry.outputIsList = flags.outputIsList" in adapter
    body = _function_body(source, "sourceCount(snapshot, link, path)")
    assert "const flagsKnown = typeof node.inputIsList === 'boolean' && typeof slotIsList === 'boolean'" in body
    assert "if (flagsKnown && node.inputIsList && !slotIsList) {" in body
    assert "if (CORE_SINGLE_CLASSES.has(type) || (flagsKnown && !node.inputIsList && !slotIsList)) {" in body


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
    # v0.67.0: solo_run joined the widget-callback triggers (a token
    # paste changes the count with no rewire, same as a mode flip).
    assert "for (const name of ['pair_mode', 'sweep_mode', 'solo_run'])" in body
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
    -- neither may appear even once. setTimeout appears exactly TWICE, and
    both are DEFERRALS, not polling timers: the one-shot post-add recompute
    (at nodeCreated the node has no id yet, and the Vue renderer never
    self-heals via a redraw -- 2026-08-14), and the per-tick coalescer for
    the graph-change watch (v0.63.2; since v1.2.0 one pass per tick on the
    ROOT graph for the whole workflow, FORMAT.md §7.10 nested reach)."""
    assert "window.addEventListener" not in source
    assert "setInterval" not in source
    assert source.count("setTimeout(") == 2
    attach = _function_body(source, "attach(node)")
    assert "setTimeout(() => {" in attach  # the documented post-add deferral
    coalescer = _function_body(source, "scheduleGraphRecompute(graph)")
    assert "root.__epsRcRefreshQueued" in coalescer
    assert "setTimeout(() => {" in coalescer


def test_recompute_repaints_only_on_change(source: str) -> None:
    """A busy canvas redraws constantly; identical text must not thrash the
    DOM."""
    body = _function_body(source, "recompute(state, pass)")
    assert "if (view.text === state.lastText && view.cls === state.lastCls) {" in body
    # v1.2.0: the view is estimated from the ROOT's snapshot (a lone trigger
    # builds its own, the whole-workflow pass shares one) -- see
    # test_recompute_estimates_from_the_root_graph below.
    assert "readoutViewFor(state, root, pass?.snapshot || snapshotFromGraph(root))" in body


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
    recompute_body = _function_body(source, "recompute(state, pass)")
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
    # v0.87.5 tab-switch audit: the delta write is now gated on
    # `wasBaselined` (test_readout_size_never_double_counts_across_a_rebuild
    # below covers WHY) -- the growth math itself is otherwise unchanged.
    assert (
        "const target = wasBaselined ? node.size[1] + (state.outerHeight - previousOuter)"
        " : node.size[1]" in body
    )
    assert "Math.max(target, floor)" in body
    assert "sizeToContent(state)" in _function_body(source, "recompute(state, pass)")


def test_readout_size_never_double_counts_across_a_rebuild(source: str) -> None:
    """Tab-switch audit (2026-08-31), reproduced live on the rig: a Run
    Multiplier showing a wrapped (grown) readout got a LITTLE TALLER on
    every round-trip through another workflow tab, never shrinking back.

    Root cause: `attach()` builds a brand-new `state` on every rebuild (tab
    switch, undo/redo, a plain reload), reseeded to READOUT_HEIGHT --
    `sizeToContent`'s delta math (`node.size[1] + (state.outerHeight -
    previousOuter)`) then diffed the CURRENT (correctly wrapped) message
    against that fresh default instead of against what `node.size` already
    accounted for post-restore, adding the readout's own height on TOP of a
    size that already included it. `state.heightBaselined` (seeded `false`
    in attach()'s state literal) closes this: the first size-changing pass
    for a given `state` only ever floors (`node.size[1]` unmodified, just
    maxed against `computeSize()`), never subtracts/adds a delta it has no
    way to know is accurate; only a LATER pass -- once `state.textHeight`
    reflects a change this function itself made -- resumes the ordinary
    grow/shrink-back delta."""
    attach_body = _function_body(source, "attach(node)")
    assert "heightBaselined: false" in attach_body, (
        "state must start un-baselined so the first post-rebuild pass floors "
        "instead of trusting a stale delta"
    )
    body = _function_body(source, "sizeToContent(state)")
    assert "const wasBaselined = state.heightBaselined" in body
    assert "state.heightBaselined = true" in body
    # The un-baselined branch must floor via node.size[1] alone -- no delta.
    assert re.search(r"wasBaselined\s*\?\s*node\.size\[1\]\s*\+.*?:\s*node\.size\[1\]", body), (
        "the un-baselined branch must fall back to node.size[1] with no delta"
    )
    # Establishing the baseline must happen strictly BEFORE state.textHeight
    # is overwritten -- reading `wasBaselined` after that line would always
    # see the value this same pass just set.
    read_at = body.index("const wasBaselined = state.heightBaselined")
    overwrite_at = body.index("state.textHeight = needed")
    assert read_at < overwrite_at, "wasBaselined must be captured before textHeight moves"


def test_run_count_refreshes_on_graph_changes_not_only_draws(source: str) -> None:
    """The count depends on the WHOLE upstream graph, but every other
    trigger fires only for this node's OWN edits -- and the Vue renderer
    never calls onDrawForeground (§7.5). EPSCrossSweep is deliberately NOT
    in eps_image.js's VUE_AFFECTED_CLASSES, so "works under Vue" is a
    promise this file has to keep: deleting an upstream loader must not
    leave a stale number behind."""
    assert "function installGraphNodeWatch(root)" in source
    watch = _function_body(source, "installGraphNodeWatch(root)")
    # v0.68.1 -> v1.2.0: NOT a one-shot boolean. Core's useGraphNodeManager
    # cleanup and installErrorClearingHooks disposer (1.48.7 source maps)
    # RESTORE graph.onNodeAdded/onNodeRemoved to the values captured at
    # THEIR install on every subgraph enter/exit -- dropping a later wrapper
    # -- so the installed wrapper is stored per hook and re-verified on every
    # call (re-wrapping the CURRENT value when it is no longer ours; a
    # surviving older wrapper of ours is adopted, bounded chain; the graph is
    # the closure's, not `this`). That logic now lives ONCE in the shared
    # lora_library/api.js `watchAllGraphs` (tests/test_nested_reach_js.py
    # pins it), and this file just delegates -- on EVERY graph, since a
    # subgraph's hooks fire only on that subgraph (FORMAT.md §7.10).
    assert "watchAllGraphs(root, WATCH_KEY, WATCH_HOOKS, onWatchedGraphEvent)" in watch
    assert "const WATCH_KEY = '__epsRcNodeWatch'" in source
    assert "const WATCH_HOOKS = ['onNodeAdded', 'onNodeRemoved', 'onAfterChange']" in source
    assert "graph.__epsRcNodeWatch = true" not in source, "the one-shot flag is gone"
    assert "wrapper.__epsRcNodeWatch" not in source, "no hand-copied wrapper installer"
    assert "original?.apply(this, args)" not in source, "the chain lives in the shared helper"
    handler = _function_body(source, "onWatchedGraphEvent(graph)")
    assert "scheduleGraphRecompute(graph)" in handler
    assert "scheduleGraphRecompute(this)" not in source
    # State hangs off the node, so a deleted node takes its state with it.
    assert "node.__epsRcState = state" in source
    assert "installGraphNodeWatch(rootGraphOf(node.graph) || app.graph)" in source
    # ...re-verified from every lone recompute pass (and once per
    # whole-workflow pass), which is also what arms a SUBGRAPH's own graph:
    # node.graph is null at nodeCreated (attach falls back to app.graph, the
    # root) and the deferred first recompute sees the real graphs.
    recompute = _function_body(source, "recompute(state, pass)")
    assert "if (!pass) installGraphNodeWatch(root)" in recompute
    assert recompute.index("installGraphNodeWatch(root)") < recompute.index(
        "readoutViewFor(state, root,"
    )
    coalescer = _function_body(source, "scheduleGraphRecompute(graph)")
    assert "installGraphNodeWatch(root)" in coalescer


def test_recompute_estimates_from_the_root_graph(source: str) -> None:
    """v1.2.0 nested reach (owner ask 2026-10-03, FORMAT.md §7.10): the
    readout estimates from the ROOT graph (`rootGraphOf(state.node.graph)`,
    falling back to app.graph), so a multiplier inside a subgraph sees
    sources outside and vice versa, and finds its own path id(s) with
    `locationsOfNode` -- one per instance of a shared definition."""
    body = _function_body(source, "recompute(state, pass)")
    assert "const root = pass?.root || rootGraphOf(state.node.graph) || app.graph" in body
    view = _function_body(source, "readoutViewFor(state, root, snapshot)")
    assert "locationsOfNode(root, state.node)" in view
    assert "estimateRuns(snapshot, pathId)" in view
    assert "formatInstanceReadouts(" in view
    # the old "estimate from the node's own graph" call is gone
    assert "estimateRuns(snapshotFromGraph(graph)" not in source
    assert "state.node.graph || app.graph" not in source


def test_whole_workflow_recompute_walks_every_graph(source: str) -> None:
    """A change in ANY graph can change ANY multiplier's count, so the
    coalesced pass is keyed on the ROOT graph, walks every node under it
    (subgraphs included, via the shared walkLiveNodes), shares ONE snapshot,
    and de-duplicates states (a node in a shared definition is walked once
    per instance). `recomputeEveryGraph` no longer keeps its own graph
    stack."""
    body = _function_body(source, "scheduleGraphRecompute(graph)")
    assert "const root = rootGraphOf(graph)" in body
    assert "for (const { node } of walkLiveNodes(root))" in body
    assert "const states = new Set()" in body
    assert "pass = { root, snapshot: snapshotFromGraph(root) }" in body
    assert "recompute(state, pass)" in body
    assert "graph._nodes" not in body
    every = _function_body(source, "recomputeEveryGraph()")
    assert "scheduleGraphRecompute(root)" in every
    assert "stack" not in every
    assert "._nodes" not in every


def test_model_low_is_a_welded_axis_member_in_the_estimator(source: str) -> None:
    """v0.66.0 WAN pairing: model_low participates in zero-collapse, the
    aligned agreement check, and the axis length -- and NEVER adds an axis
    of its own, so the readout's counts stay exactly what they were.
    Backend wired_sweep parity, which the referee rounds taught us to keep
    exact."""
    assert "for (const name of ['model', 'model_low', 'clip', 'label', 'vae'])" in source
    assert "for (const name of ['model', 'model_low', 'clip', 'label'])" in source
    assert "7: 'model_low'" in source  # chained dead-output guard covers the new tail


def test_estimator_defaults_missing_modes_to_multiply(source: str) -> None:
    """v0.66.1 backend parity: run()'s python defaults are multiply, so a
    snapshot with no stored mode value must read as multiply too -- the
    referee lesson: estimator and backend must never disagree."""
    assert "const pairMultiply = (widgets.pair_mode ?? 'multiply') === 'multiply'" in source
    assert "const sweepMultiply = (widgets.sweep_mode ?? 'multiply') === 'multiply'" in source


def test_mode_combos_hidden_behind_a_property(source: str) -> None:
    """v0.66.1 (owner): both mode combos hidden by default, revealed by the
    'Show mode options' node property. BOTH hide flags per §7.5 (canvas
    reads widget.hidden, Vue reads options.hidden); the widgets stay in
    node.widgets so §8's positional widgets_values contract is untouched."""
    assert "const PROP_SHOW_MODES = 'Show mode options'" in source
    body = _function_body(source, "applyModeVisibility(node)")
    assert "widget.hidden = hidden" in body
    assert "widget.options = { ...(widget.options || {}), hidden }" in body
    wire = _function_body(source, "wireModeVisibility(node)")
    assert "node.addProperty(PROP_SHOW_MODES, false, 'boolean')" in wire
    assert "applyModeVisibility(node)" in wire  # apply-once at attach
    assert "wireModeVisibility(node)" in _function_body(source, "attach(node)")


# =============================================================================
# v1.2.0 NESTED SUBGRAPHS (owner ask 2026-10-03: "make sure all of the nodes
# that can control other nodes also looks into nested nodes"; FORMAT.md §7.10
# nested reach). The REAL `snapshotFromGraph` + `estimateRuns` over the shared
# fake nested litegraph (tests/nested_graph.mjs -- SubgraphNode `.subgraph`,
# index-aligned inputs/outputs, boundary links with origin id -10 / target id
# -20 that are NOT nodes, one definition shared by several instances), plus
# the readout watch driven through the REAL `attach()` against a stub DOM.
#
# What this cannot cover (the rig must): the real LGraph/LLink classes, a
# real graphToPrompt, core's own hook restore on subgraph enter/exit, and the
# Vue renderer.
# =============================================================================

NESTED_PRELUDE_JS = r"""
import { FakeGraph, FakeNode, FakeSubgraphNode, wire, INPUT, OUTPUT } from './nested_graph.mjs'
import * as cs from './extensions/comfyui-epsnodes/eps_image/cross_sweep.js'

const wlist = (widgets) => Object.entries(widgets).map(([name, value]) => ({ name, value }))
const mk = (id, type, { inputs = [], outputs = ['out'], widgets = {} } = {}) =>
  new FakeNode({ id, type, inputs, outputs, widgets: wlist(widgets) })
const MULT_IN = ['model', 'model_low', 'clip', 'label', 'vae', 'text', 'image', 'name']
const MULT_OUT = ['model', 'clip', 'image', 'text', 'save_prefix', 'label', 'vae', 'model_low']
const mult = (id, widgets = {}) =>
  mk(id, 'EPSCrossSweep', { inputs: MULT_IN, outputs: MULT_OUT, widgets })
const slot = (name) => MULT_IN.indexOf(name)
const notebook = (id, lines) =>
  mk(id, 'LoraLibraryNotebook', {
    inputs: ['text'],
    outputs: ['text', 'name'],
    widgets: { file: 'a.md', entry: Array.from({ length: lines }, (_, i) => `e${i}`).join('\n') }
  })
const loader = (id) => mk(id, 'UNETLoader', { outputs: ['MODEL'] })
const textSource = (id) => mk(id, 'PrimitiveStringMultiline', { outputs: ['STRING'] })
const modelSwitcher = (id, n) =>
  mk(id, 'EPSModelSwitcher', {
    inputs: Array.from({ length: n }, (_, i) => `model_${i + 1}`),
    outputs: ['model', 'models_low'],
    widgets: { toggles: '{}' }
  })
const subnode = (root, id, name, ins, outs) =>
  new FakeSubgraphNode({ id, name, inputs: ins, outputs: outs, rootGraph: root })
/** A model switcher fed by loaders `base..base+n-1`, all in *graph*. */
const fedSwitcher = (graph, id, n, base) => {
  const sw = graph.add(modelSwitcher(id, n))
  for (let i = 0; i < n; i++) wire(graph, graph.add(loader(base + i)), 0, sw, i)
  return sw
}
const pick = (e) => ({
  total: e.total, atLeast: e.atLeast, steps: e.steps, pairs: e.pairs,
  unknowns: [...e.unknowns].sort(), error: e.error, breakdown: e.breakdown
})
const est = (root, pathId) => pick(cs.estimateRuns(cs.snapshotFromGraph(root), pathId))
const snap = (root) => cs.snapshotFromGraph(root)
const out = {}
"""

NESTED_ESTIMATE_PROBE_JS = NESTED_PRELUDE_JS + r"""
// ---- 1. root multiplier fed by sources INSIDE a subgraph --------------------
{
  // a Notebook (3 lines) behind a subgraph output
  const root = new FakeGraph()
  const S = root.add(subnode(root, 10, 'Prompts', [], ['text']))
  wire(S.subgraph, S.subgraph.add(notebook(2, 3)), 0, OUTPUT, 0)
  const M = root.add(mult(5))
  wire(root, S, 0, M, slot('text'))
  out.notebookInside = { est: est(root, '5'), text: snap(root).nodes['5'].inputs.text }
}
{
  // a 3-way Model Switcher inside (the model axis), plain text at the root
  const root = new FakeGraph()
  const S = root.add(subnode(root, 10, 'Models', [], ['model']))
  const sw = fedSwitcher(S.subgraph, 2, 3, 3)
  wire(S.subgraph, sw, 0, OUTPUT, 0)
  const M = root.add(mult(5))
  const T = root.add(textSource(6))
  wire(root, S, 0, M, slot('model'))
  wire(root, T, 0, M, slot('text'))
  out.switcherInside = est(root, '5')
}
{
  // an Emit-mode Image Grid inside: the live imgs count crosses the boundary
  const root = new FakeGraph()
  const S = root.add(subnode(root, 10, 'Frames', [], ['image']))
  const grid = S.subgraph.add(
    mk(2, 'EPSImageGrid', { outputs: ['image'], widgets: { mode: 'Emit', focus: '' } })
  )
  grid.imgs = [1, 2, 3, 4]
  wire(S.subgraph, grid, 0, OUTPUT, 0)
  const M = root.add(mult(5))
  wire(root, S, 0, M, slot('image'))
  wire(root, root.add(notebook(6, 2)), 0, M, slot('text'))
  out.gridInside = est(root, '5')
}

// ---- 2. multiplier INSIDE a subgraph, fed from OUTSIDE -----------------------
{
  const root = new FakeGraph()
  const nb = root.add(notebook(1, 4))
  const sw = fedSwitcher(root, 3, 2, 20)
  const S = root.add(subnode(root, 10, 'Runner', ['text', 'model'], []))
  wire(root, nb, 0, S, 0)
  wire(root, sw, 0, S, 1)
  const M = S.subgraph.add(mult(2))
  wire(S.subgraph, INPUT, 0, M, slot('text'))
  wire(S.subgraph, INPUT, 1, M, slot('model'))
  out.insideFedFromOutside = {
    est: est(root, '10:2'),
    inputs: snap(root).nodes['10:2'].inputs,
    // the SubgraphNode itself never enters the snapshot (nothing points at it)
    keys: Object.keys(snap(root).nodes).sort()
  }
}

// ---- 3. two levels deep ------------------------------------------------------
{
  // (a) root multiplier <- S1 <- S2 <- Notebook(5)
  const root = new FakeGraph()
  const S1 = root.add(subnode(root, 5, 'Outer', [], ['text']))
  const S2 = S1.subgraph.add(subnode(root, 7, 'Inner', [], ['text']))
  wire(S2.subgraph, S2.subgraph.add(notebook(2, 5)), 0, OUTPUT, 0)
  wire(S1.subgraph, S2, 0, OUTPUT, 0)
  const M = root.add(mult(9))
  wire(root, S1, 0, M, slot('text'))
  out.deepSource = { est: est(root, '9'), text: snap(root).nodes['9'].inputs.text }
}
{
  // (b) multiplier at depth 2 <- (through two subgraph inputs) <- root switcher
  const root = new FakeGraph()
  const sw = fedSwitcher(root, 1, 3, 20)
  const S1 = root.add(subnode(root, 5, 'Outer', ['model'], []))
  wire(root, sw, 0, S1, 0)
  const S2 = S1.subgraph.add(subnode(root, 7, 'Inner', ['model'], []))
  wire(S1.subgraph, INPUT, 0, S2, 0)
  const M = S2.subgraph.add(mult(4))
  wire(S2.subgraph, INPUT, 0, M, slot('model'))
  wire(S2.subgraph, S2.subgraph.add(notebook(6, 2)), 0, M, slot('text'))
  out.deepConsumer = { est: est(root, '5:7:4'), model: snap(root).nodes['5:7:4'].inputs.model }
}
{
  // (c) multiplier at depth 1: model from the ROOT (up one), text from depth 2 (down one)
  const root = new FakeGraph()
  const sw = fedSwitcher(root, 1, 2, 20)
  const S1 = root.add(subnode(root, 5, 'Outer', ['model'], []))
  wire(root, sw, 0, S1, 0)
  const M = S1.subgraph.add(mult(3))
  wire(S1.subgraph, INPUT, 0, M, slot('model'))
  const S2 = S1.subgraph.add(subnode(root, 7, 'Inner', [], ['text']))
  wire(S2.subgraph, S2.subgraph.add(notebook(2, 4)), 0, OUTPUT, 0)
  wire(S1.subgraph, S2, 0, M, slot('text'))
  out.mixedDepths = { est: est(root, '5:3'), text: snap(root).nodes['5:3'].inputs.text }
}

// ---- 4. pass-through (a subgraph input wired straight to its output) ---------
{
  const root = new FakeGraph()
  const nb = root.add(notebook(1, 3))
  const S = root.add(subnode(root, 10, 'Pipe', ['x'], ['y']))
  wire(root, nb, 0, S, 0)
  wire(S.subgraph, INPUT, 0, OUTPUT, 0)
  const M = root.add(mult(5))
  wire(root, S, 0, M, slot('text'))
  out.passThrough = { est: est(root, '5'), text: snap(root).nodes['5'].inputs.text }
}
{
  // two pass-throughs nested: S1.x -> S2.x -> S2.y -> S1.y
  const root = new FakeGraph()
  const nb = root.add(notebook(1, 3))
  const S1 = root.add(subnode(root, 10, 'Pipe1', ['x'], ['y']))
  wire(root, nb, 0, S1, 0)
  const S2 = S1.subgraph.add(subnode(root, 11, 'Pipe2', ['x'], ['y']))
  wire(S1.subgraph, INPUT, 0, S2, 0)
  wire(S2.subgraph, INPUT, 0, OUTPUT, 0)
  wire(S1.subgraph, S2, 0, OUTPUT, 0)
  const M = root.add(mult(5))
  wire(root, S1, 0, M, slot('text'))
  out.passThroughNested = { est: est(root, '5'), text: snap(root).nodes['5'].inputs.text }
}

// ---- 5. dangling boundaries read as UNWIRED (the prompt drops them too) -------
{
  // (a) inside multiplier, model from a subgraph input nobody feeds outside
  const root = new FakeGraph()
  const S = root.add(subnode(root, 10, 'Open', ['m'], []))
  const M = S.subgraph.add(mult(2))
  wire(S.subgraph, INPUT, 0, M, slot('model'))
  wire(S.subgraph, S.subgraph.add(notebook(3, 2)), 0, M, slot('text'))
  out.danglingInput = { est: est(root, '10:2'), model: snap(root).nodes['10:2'].inputs.model }
}
{
  // (b) root multiplier, model from a SubgraphNode output with nothing behind it
  const root = new FakeGraph()
  const S = root.add(subnode(root, 10, 'Empty', [], ['y']))
  const M = root.add(mult(5))
  wire(root, S, 0, M, slot('model'))
  wire(root, root.add(notebook(6, 2)), 0, M, slot('text'))
  out.danglingOutput = { est: est(root, '5'), model: snap(root).nodes['5'].inputs.model }
}
{
  // (c) the REQUIRED text is the dangling one: nothing to pair, no crash
  const root = new FakeGraph()
  const S = root.add(subnode(root, 10, 'Open', ['t'], []))
  const M = S.subgraph.add(mult(2))
  wire(S.subgraph, INPUT, 0, M, slot('text'))
  out.danglingText = est(root, '10:2')
}

// ---- 6. a chained multiplier INSIDE a subgraph: the dead-output guard --------
const chained = (wireBacking) => {
  const root = new FakeGraph()
  const S = root.add(subnode(root, 10, 'Inner sweep', ['m'], ['m']))
  const inner = S.subgraph.add(mult(2))
  wire(S.subgraph, INPUT, 0, inner, slot('model'))
  wire(S.subgraph, S.subgraph.add(notebook(3, 2)), 0, inner, slot('text'))
  wire(S.subgraph, inner, 0, OUTPUT, 0) // the inner multiplier's MODEL output leaves the subgraph
  if (wireBacking) wire(root, root.add(loader(20)), 0, S, 0)
  const outer = root.add(mult(9))
  wire(root, S, 0, outer, slot('model'))
  wire(root, root.add(notebook(21, 3)), 0, outer, slot('text'))
  return root
}
{
  const root = chained(true)
  const nodes = snap(root).nodes
  out.chainedBacked = { est: est(root, '9'), outerModel: nodes['9'].inputs.model }
  // the flattened-prompt view of the same wires, for the Python agreement test
  const asPromptInputs = (inputs) =>
    Object.fromEntries(
      Object.entries(inputs)
        .filter(([, link]) => link)
        .map(([name, link]) => [name, [link.originId, link.originSlot]])
    )
  out.chainedPrompt = Object.fromEntries(
    Object.entries(nodes).map(([id, n]) => [id, { inputs: asPromptInputs(n.inputs) }])
  )
}
{
  const root = chained(false)
  out.chainedDead = est(root, '9')
}

// ---- 7. ONE definition, TWO instances, different upstream --------------------
const shared = (leftLines, rightLines) => {
  const root = new FakeGraph()
  const nbA = root.add(notebook(1, leftLines))
  const nbB = root.add(notebook(2, rightLines))
  const S1 = root.add(subnode(root, 10, 'Twin', ['t'], []))
  const S2 = root.add(subnode(root, 11, 'Twin', ['t'], []))
  S2.subgraph = S1.subgraph // one shared definition, like a pasted SubgraphNode
  const M = S1.subgraph.add(mult(20))
  wire(S1.subgraph, INPUT, 0, M, slot('text'))
  wire(root, nbA, 0, S1, 0)
  wire(root, nbB, 0, S2, 0)
  return { root, M }
}
{
  const { root } = shared(3, 5)
  out.sharedDifferent = { first: est(root, '10:20'), second: est(root, '11:20') }
}
{
  // instance 10's output feeds instance 11's input: each path id resolves its OWN upstream
  const root = new FakeGraph()
  const nb = root.add(notebook(1, 3))
  const S1 = root.add(subnode(root, 10, 'Link', ['t'], ['t']))
  const S2 = root.add(subnode(root, 11, 'Link', ['t'], ['t']))
  S2.subgraph = S1.subgraph
  const M = S1.subgraph.add(mult(20))
  wire(S1.subgraph, INPUT, 0, M, slot('text'))
  wire(S1.subgraph, M, 3, OUTPUT, 0)
  wire(root, nb, 0, S1, 0)
  wire(root, S1, 0, S2, 0)
  out.sharedChained = {
    first: est(root, '10:20'),
    second: est(root, '11:20'),
    secondText: snap(root).nodes['11:20'].inputs.text
  }
}

// ---- 8. same id at the root and inside a subgraph ----------------------------
{
  const root = new FakeGraph()
  const rootNb = root.add(notebook(2, 7)) // root node id 2
  const S = root.add(subnode(root, 10, 'Twin id', [], ['text']))
  wire(S.subgraph, S.subgraph.add(notebook(2, 3)), 0, OUTPUT, 0) // inner node id 2
  const MInner = root.add(mult(5))
  wire(root, S, 0, MInner, slot('text'))
  const MRoot = root.add(mult(6))
  wire(root, rootNb, 0, MRoot, slot('text'))
  const nodes = snap(root).nodes
  out.sameId = {
    viaSubgraph: est(root, '5'),
    viaRoot: est(root, '6'),
    keys: Object.keys(nodes).sort(),
    innerEntry: nodes['10:2'].widgets.entry.split('\n').length,
    rootEntry: nodes['2'].widgets.entry.split('\n').length
  }
}

// ---- 9. cycles terminate -------------------------------------------------------
{
  // two multipliers feeding each other's TEXT across a boundary
  const root = new FakeGraph()
  const S = root.add(subnode(root, 10, 'Loop', ['x'], ['y']))
  const M2 = S.subgraph.add(mult(2))
  wire(S.subgraph, INPUT, 0, M2, slot('text'))
  wire(S.subgraph, M2, 3, OUTPUT, 0)
  const M1 = root.add(mult(5))
  wire(root, S, 0, M1, slot('text'))
  wire(root, M1, 3, S, 0)
  out.cycleMultipliers = { outer: est(root, '5'), inner: est(root, '10:2') }
}
{
  // a boundary-only loop: S.y -> S.x with a pass-through inside
  const root = new FakeGraph()
  const S = root.add(subnode(root, 10, 'Ouroboros', ['x'], ['y']))
  wire(S.subgraph, INPUT, 0, OUTPUT, 0)
  wire(root, S, 0, S, 0)
  const M = root.add(mult(5))
  wire(root, S, 0, M, slot('text'))
  out.cycleBoundary = { est: est(root, '5'), text: snap(root).nodes['5'].inputs.text }
}

// ---- 10. a flat graph snapshots exactly as before ------------------------------
{
  const root = new FakeGraph()
  const sw = fedSwitcher(root, 1, 4, 11)
  const T = root.add(textSource(3))
  const M = root.add(mult(5, { pair_mode: 'paired', sweep_mode: 'multiply' }))
  wire(root, sw, 0, M, slot('model'))
  wire(root, T, 0, M, slot('text'))
  // a STALE link: its origin id (99) names no node in this graph
  const M2 = root.add(mult(6))
  root.links.set(900, {
    id: 900, origin_id: 99, origin_slot: 2, target_id: 6, target_slot: slot('model')
  })
  M2.inputs[slot('model')].link = 900
  wire(root, T, 0, M2, slot('text'))
  const nodes = snap(root).nodes
  out.flat = {
    keys: Object.keys(nodes).sort(),
    five: nodes['5'],
    staleModel: nodes['6'].inputs.model,
    est: est(root, '5'),
    staleEst: est(root, '6')
  }
}
{
  // a stale link INSIDE a subgraph keeps the prefix: still an unknown id, not "unwired"
  const root = new FakeGraph()
  const S = root.add(subnode(root, 10, 'Stale', [], []))
  const M = S.subgraph.add(mult(2))
  S.subgraph.links.set(901, {
    id: 901, origin_id: 77, origin_slot: 0, target_id: 2, target_slot: slot('model')
  })
  M.inputs[slot('model')].link = 901
  wire(S.subgraph, S.subgraph.add(notebook(3, 2)), 0, M, slot('text'))
  out.staleInside = { model: snap(root).nodes['10:2'].inputs.model, est: est(root, '10:2') }
}

// ---- 11. link tables of every shape (Map / plain object / Map-and-index Proxy) --
{
  const build = () => {
    const root = new FakeGraph()
    const S = root.add(subnode(root, 10, 'Tables', [], ['text']))
    wire(S.subgraph, S.subgraph.add(notebook(2, 3)), 0, OUTPUT, 0)
    const M = root.add(mult(5))
    wire(root, S, 0, M, slot('text'))
    return { root, S }
  }
  const asObject = (graph) => { graph.links = Object.fromEntries(graph.links) }
  const asProxy = (graph) => {
    const map = graph.links
    graph.links = new Proxy(map, {
      get(target, prop) {
        if (typeof prop === 'string' && /^\d+$/.test(prop)) return target.get(Number(prop))
        const value = Reflect.get(target, prop)
        return typeof value === 'function' ? value.bind(target) : value
      }
    })
  }
  out.linkTables = {}
  for (const [name, shape] of [['map', null], ['object', asObject], ['proxy', asProxy]]) {
    const { root, S } = build()
    if (shape) { shape(root); shape(S.subgraph) }
    out.linkTables[name] = est(root, '5').total
  }
}

console.log(JSON.stringify(out))
"""

NESTED_FORMAT_PROBE_JS = NESTED_PRELUDE_JS + r"""
const E = (o) => ({
  total: 0, atLeast: false, steps: 1, pairs: 1, unknowns: [], error: null, breakdown: '',
  solo: null, soloOf: 0, soloOfAtLeast: false, ...o
})
const fmt = (...ests) =>
  cs.formatInstanceReadouts(ests.map((est, i) => ({ pathId: `${3 + i}:5`, est })))
const four = E({ total: 6, steps: 2, pairs: 3 })
const three = E({ total: 3, steps: 1, pairs: 3 })
const five = E({ total: 5, steps: 1, pairs: 5 })
out.cases = {
  empty: cs.formatInstanceReadouts([]),
  single: fmt(E({ total: 8, steps: 4, pairs: 2 })),
  singleMatchesFormatReadout: cs.formatReadout(E({ total: 8, steps: 4, pairs: 2 })),
  agree: fmt(E({ total: 8, steps: 4, pairs: 2 }), E({ total: 8, steps: 4, pairs: 2 })),
  differentTotals: fmt(three, five),
  threeUses: fmt(five, three, five),
  sameTotalOtherSplit: fmt(four, E({ total: 6, steps: 3, pairs: 2 })),
  oneIsFloor: fmt(
    E({ total: 4, atLeast: true, steps: 2, pairs: 2, unknowns: ['text'] }),
    E({ total: 4, steps: 2, pairs: 2 })
  ),
  failing: fmt(
    three,
    E({ error: 'sweep lengths disagree: model=4 vs vae=2 — the queue will fail' })
  ),
  solo: fmt(
    E({ total: 1, solo: 'm1_t1', soloOf: 4 }),
    E({ total: 1, solo: 'm1_t1', soloOf: 6 })
  ),
  zeroAndEight: fmt(
    E({ total: 0, breakdown: 'nothing to run' }),
    E({ total: 8, steps: 4, pairs: 2 })
  ),
  allZeroDifferentNames: fmt(
    E({ total: 0, breakdown: 'nothing to run (text input is empty/blocked)' }),
    E({ total: 0, breakdown: 'nothing to run' })
  )
}
console.log(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def nested_estimates(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """The estimator probe over every nested fixture (one Node run)."""
    layout = build_layout(
        tmp_path_factory.mktemp("cross_sweep_nested"), eps_image=("cross_sweep.js",)
    )
    return run_probe(layout, NESTED_ESTIMATE_PROBE_JS)


@pytest.fixture(scope="module")
def nested_formats(tmp_path_factory: pytest.TempPathFactory) -> dict:
    layout = build_layout(
        tmp_path_factory.mktemp("cross_sweep_nested_fmt"), eps_image=("cross_sweep.js",)
    )
    return run_probe(layout, NESTED_FORMAT_PROBE_JS)


def _est(total: int, steps: int, pairs: int, **extra: object) -> dict:
    """The estimator fields every nested case asserts on (see `pick` in the probe)."""
    return {
        "total": total,
        "atLeast": False,
        "steps": steps,
        "pairs": pairs,
        "unknowns": [],
        "error": None,
        "breakdown": f"{steps} sweep step{'s' if steps != 1 else ''} × "
        f"{pairs} pair{'s' if pairs != 1 else ''}",
        **extra,
    }


# ------------------------------------------------- sources inside a subgraph


def test_root_multiplier_counts_a_notebook_inside_a_subgraph(nested_estimates: dict) -> None:
    """The headline gap: the snapshot used to read ONE graph, so a wire out of
    a SubgraphNode output pointed at an id that was not in it and counted as
    UNKNOWN (`≥`). It now resolves to the inner node's PATH id."""
    got = nested_estimates["notebookInside"]
    assert got["text"] == {"originId": "10:2", "originSlot": 0}
    assert got["est"] == _est(3, 1, 3)


def test_root_multiplier_counts_a_switcher_inside_a_subgraph(nested_estimates: dict) -> None:
    assert nested_estimates["switcherInside"] == _est(3, 3, 1)


def test_root_multiplier_counts_an_image_grid_inside_a_subgraph(nested_estimates: dict) -> None:
    """The live imgs count is injected per node OBJECT, so it crosses the
    boundary too: 4 frames x 2 texts, still a floor (server state)."""
    got = nested_estimates["gridInside"]
    assert got["total"] == 8
    assert got["atLeast"] is True
    assert got["unknowns"] == ["image"]


# ------------------------------------------- multiplier inside, source outside


def test_multiplier_inside_a_subgraph_sees_sources_outside(nested_estimates: dict) -> None:
    """A link out of the subgraph's INPUT pseudo-node (origin id -10) climbs
    out through the owning SubgraphNode instance -- known from the path
    prefix -- to whatever feeds that input in the parent graph."""
    got = nested_estimates["insideFedFromOutside"]
    assert got["inputs"]["text"] == {"originId": "1", "originSlot": 0}
    assert got["inputs"]["model"] == {"originId": "3", "originSlot": 0}
    assert got["est"] == _est(8, 2, 4)
    # the SubgraphNode never enters the snapshot: nothing can see it as an
    # unknown-class source any more
    assert "10" not in got["keys"]
    assert "10:2" in got["keys"]


def test_two_levels_deep_in_both_directions(nested_estimates: dict) -> None:
    deep_source = nested_estimates["deepSource"]
    assert deep_source["text"] == {"originId": "5:7:2", "originSlot": 0}
    assert deep_source["est"] == _est(5, 1, 5)
    deep_consumer = nested_estimates["deepConsumer"]
    assert deep_consumer["model"] == {"originId": "1", "originSlot": 0}
    assert deep_consumer["est"] == _est(6, 3, 2)


def test_source_and_consumer_at_different_depths(nested_estimates: dict) -> None:
    got = nested_estimates["mixedDepths"]
    assert got["text"] == {"originId": "5:7:2", "originSlot": 0}
    assert got["est"] == _est(8, 2, 4)


def test_pass_through_subgraph_resolves(nested_estimates: dict) -> None:
    """A subgraph input wired straight to its output (no node inside)."""
    for case in ("passThrough", "passThroughNested"):
        got = nested_estimates[case]
        assert got["text"] == {"originId": "1", "originSlot": 0}, case
        assert got["est"] == _est(3, 1, 3), case


# ------------------------------------------------------- dangling boundaries


def test_dangling_boundaries_read_as_unwired_not_unknown(nested_estimates: dict) -> None:
    """An unconnected subgraph input/output is dropped by the prompt
    flattening, so the input is UNWIRED (null) -- not a `≥` unknown, and
    never a crash."""
    inside = nested_estimates["danglingInput"]
    assert inside["model"] is None
    assert inside["est"] == _est(2, 1, 2)
    outside = nested_estimates["danglingOutput"]
    assert outside["model"] is None
    assert outside["est"] == _est(2, 1, 2)


def test_dangling_required_text_means_nothing_to_run(nested_estimates: dict) -> None:
    got = nested_estimates["danglingText"]
    assert got["total"] == 0
    assert got["error"] is None
    assert got["breakdown"] == "nothing to run"


# ------------------------------------------- chained multiplier (dead output)


def test_chained_multiplier_inside_a_subgraph_counts_through(nested_estimates: dict) -> None:
    """The outer multiplier's model axis is the INNER multiplier's run count
    (2 notebook lines), reached across the boundary: 2 steps x 3 pairs."""
    got = nested_estimates["chainedBacked"]
    assert got["outerModel"] == {"originId": "10:2", "originSlot": 0}
    assert got["est"] == _est(6, 2, 3)


def test_dead_output_guard_follows_the_flattened_prompt(nested_estimates: dict) -> None:
    """Consuming the inner multiplier's model OUTPUT while its model INPUT is
    unwired (here a dangling subgraph input) is the v0.51.0 queue-time
    ValueError -- and the readout paints it, exactly as the flattened prompt
    (where the inner node is "10:2" and the input carries no link) will."""
    got = nested_estimates["chainedDead"]
    assert got["error"] is not None
    assert "model output is consumed" in got["error"]
    assert "the queue will fail" in got["error"]


def test_snapshot_links_agree_with_the_python_consumed_slot_scan(nested_estimates: dict) -> None:
    """Item 4 of the nested-reach brief: the backend's `_consumed_output_slots`
    scans the FLATTENED prompt, where a consumer reached across a subgraph
    boundary references the inner multiplier by its path id. The snapshot's
    boundary-resolved links, rendered as `[origin, slot]` prompt inputs, must
    make that scan report the same consumed slot the JS dead-output guard
    reasons about -- the two sides cannot disagree about who consumes it."""
    from eps_image.nodes_cross_sweep import _consumed_output_slots

    prompt = nested_estimates["chainedPrompt"]
    assert _consumed_output_slots(prompt, "10:2") == {0}
    # the outer multiplier's own output is consumed by nobody
    assert _consumed_output_slots(prompt, "9") == set()
    # ...and a root node whose id merely EQUALS the inner id is a different node
    assert _consumed_output_slots(prompt, "2") == set()


# ----------------------------------------------------- shared definitions


def test_shared_definition_is_estimated_per_instance(nested_estimates: dict) -> None:
    got = nested_estimates["sharedDifferent"]
    assert got["first"] == _est(3, 1, 3)
    assert got["second"] == _est(5, 1, 5)


def test_instance_to_instance_wire_resolves_each_path_id_its_own_upstream(
    nested_estimates: dict,
) -> None:
    """Instance 10's output feeds instance 11's input; the walk UP out of the
    shared definition must pick the instance named by the path prefix, not
    fan out to every instance."""
    got = nested_estimates["sharedChained"]
    assert got["secondText"] == {"originId": "10:20", "originSlot": 3}
    assert got["first"] == _est(3, 1, 3)
    assert got["second"] == _est(3, 1, 3)


def test_same_id_at_root_and_inside_a_subgraph_stay_apart(nested_estimates: dict) -> None:
    got = nested_estimates["sameId"]
    assert got["viaSubgraph"]["pairs"] == 3
    assert got["viaRoot"]["pairs"] == 7
    assert got["innerEntry"] == 3
    assert got["rootEntry"] == 7
    assert "2" in got["keys"]
    assert "10:2" in got["keys"]


# ------------------------------------------------------------------- cycles


def test_cycles_across_a_boundary_terminate(nested_estimates: dict) -> None:
    """The probe finishing is the guard proof. Two multipliers feeding each
    other across a boundary degrade to the honest `≥` floor; a boundary-only
    loop (no node in it) resolves to an unwired input."""
    for case in ("outer", "inner"):
        got = nested_estimates["cycleMultipliers"][case]
        assert got["atLeast"] is True, case
        assert got["error"] is None, case
    boundary = nested_estimates["cycleBoundary"]
    assert boundary["text"] is None
    assert boundary["est"]["total"] == 0


# --------------------------------------------------------- flat is unchanged


def test_flat_graph_snapshot_is_unchanged(nested_estimates: dict) -> None:
    """No subgraphs: keys are the plain ids, the entry shape is exactly the
    pre-1.0.0 one, and a STALE link (its origin names no node) still points
    at the missing id so the estimator counts it UNKNOWN -- not unwired."""
    got = nested_estimates["flat"]
    assert sorted(got["keys"]) == sorted(["1", "3", "5", "6", "11", "12", "13", "14"])
    five = got["five"]
    assert set(five) == {"classType", "widgets", "inputs"}
    assert five["classType"] == "EPSCrossSweep"
    assert five["inputs"]["model"] == {"originId": "1", "originSlot": 0}
    assert five["inputs"]["text"] == {"originId": "3", "originSlot": 0}
    assert five["inputs"]["vae"] is None
    assert got["staleModel"] == {"originId": "99", "originSlot": 2}
    assert got["staleEst"]["atLeast"] is True
    assert got["staleEst"]["unknowns"] == ["model"]
    # 4 models x 1 text, text is the paired side
    assert got["est"]["total"] == 4


def test_stale_link_inside_a_subgraph_keeps_the_path_prefix(nested_estimates: dict) -> None:
    got = nested_estimates["staleInside"]
    assert got["model"] == {"originId": "10:77", "originSlot": 0}
    assert got["est"]["atLeast"] is True
    assert got["est"]["unknowns"] == ["model"]


def test_every_link_table_shape_resolves_across_boundaries(nested_estimates: dict) -> None:
    """Map, plain object and the Map-and-index Proxy 1.5x uses."""
    assert nested_estimates["linkTables"] == {"map": 3, "object": 3, "proxy": 3}


# ------------------------------------------------ shared helpers, not copies


def test_nested_walk_is_delegated_to_the_shared_helpers(source: str) -> None:
    """This pack keeps shipping fixes that miss hand-copied siblings, so the
    boundary traversal, the path-id lookup and the hook watch are IMPORTED
    from lora_library/api.js (FORMAT.md §7.10) -- never re-implemented in
    this file."""
    imports = re.search(r"import \{([^}]*)\} from '\.\./lora_library/api\.js'", source)
    assert imports, "cross_sweep.js must import the shared nested helpers"
    names = {name.strip() for name in imports.group(1).split(",")}
    assert {
        "SUBGRAPH_INPUT_ID",
        "comparePathIds",
        "graphLink",
        "isSubgraphNode",
        "locationsOfNode",
        "resolveLinkSources",
        "rootGraphOf",
        "walkLiveNodes",
        "watchAllGraphs",
    } <= names
    # the old one-graph link reader is gone (api.graphLink is its superset)
    assert "function resolveGraphLink(" not in source
    link = _function_body(source, "resolveInputLink(root, graph, prefix, idsInGraph, linkId)")
    assert "graphLink(graph, linkId)" in link
    assert "resolveLinkSources(root, graph, prefix, link)" in link
    adapter = _function_body(source, "snapshotFromGraph(graph)")
    assert "walkLiveNodes(graph)" in adapter
    assert "if (isSubgraphNode(node)) continue" in adapter
    assert "snapshot.nodes[pathId] = entry" in adapter
    assert "graph?._nodes" not in adapter
    # the header no longer claims a single-graph snapshot
    assert "NESTED SUBGRAPHS (v1.2.0" in source
    assert "built from the\n * live `app.graph`" not in source


# ------------------------------------------------- the multi-instance readout


def test_instance_readouts_agree_or_never_overclaim(nested_formats: dict) -> None:
    """`formatInstanceReadouts` -- one multiplier, several path ids."""
    cases = nested_formats["cases"]
    assert cases["empty"] == {"text": "", "cls": ""}
    # one instance / agreeing instances: exactly the ordinary readout
    assert cases["single"] == cases["singleMatchesFormatReadout"]
    assert cases["single"] == {"text": "Runs: 8 — 4 sweep steps × 2 pairs", "cls": ""}
    assert cases["agree"] == cases["single"]
    # disagreement: the largest as a floor + a note naming the N uses
    assert cases["differentTotals"] == {
        "text": "Runs: ≥ 5 — this subgraph is used 2 times with different counts (3, 5)",
        "cls": "eps-rc-warn",
    }
    assert cases["threeUses"] == {
        "text": "Runs: ≥ 5 — this subgraph is used 3 times with different counts (3, 5)",
        "cls": "eps-rc-warn",
    }
    # equal totals, different splits: the total IS known -- no floor claimed
    assert cases["sameTotalOtherSplit"] == {
        "text": "Runs: 6 — this subgraph is used 2 times with different sweep/pair setups",
        "cls": "eps-rc-warn",
    }
    # an instance that is itself a floor keeps the floor and stays honest
    assert cases["oneIsFloor"]["text"] == (
        "Runs: ≥ 4 — this subgraph is used 2 times with different counts (4, ≥ 4)"
    )
    # a zero-run copy beside a real one
    assert cases["zeroAndEight"]["text"] == (
        "Runs: ≥ 8 — this subgraph is used 2 times with different counts (0, 8)"
    )
    assert cases["allZeroDifferentNames"]["text"] == (
        "Runs: 0 — this subgraph is used 2 times with different sweep/pair setups"
    )


def test_instance_readout_failure_and_solo_are_never_softened(nested_formats: dict) -> None:
    cases = nested_formats["cases"]
    # one failing copy fails the whole queue, whatever the others say
    assert cases["failing"]["cls"] == "eps-rc-error"
    assert cases["failing"]["text"] == (
        "sweep lengths disagree: model=4 vs vae=2 — the queue will fail "
        "(copy 4:5 of 2 uses of this subgraph)"
    )
    # solo: the SET sizes are what differ between copies
    assert cases["solo"] == {
        "text": "Solo m1_t1 — 1 of ≥ 6 runs — this subgraph is used 2 times "
        "with different counts (4, 6)",
        "cls": "eps-rc-warn",
    }


# ------------------------------------------------------- live readout + watch

NESTED_WATCH_PROBE_JS = NESTED_PRELUDE_JS + r"""
import { app } from './scripts/app.js'

// ---- a stub DOM: attach() builds a root div holding the readout line --------
const makeEl = (tag) => ({
  tag, className: '', textContent: '', title: '', style: {}, clientWidth: 0, scrollHeight: 0,
  children: [], appendChild(c) { this.children.push(c); return c }
})
globalThis.document = {
  createElement: makeEl, getElementById: () => null, head: { appendChild() {} }
}

class MultNode extends FakeNode {
  constructor(opts = {}) {
    super({ type: 'EPSCrossSweep', inputs: MULT_IN, outputs: MULT_OUT, ...opts })
    this.properties = {}
  }
  addDOMWidget(name, type, el, options) {
    const widget = { name, type, element: el, options }
    this.domEl = el
    this.widgets.push(widget)
    return widget
  }
  addProperty(name, value) { this.properties[name] = value }
  computeSize() { return [200, 80] }
  get readout() { return this.domEl?.children?.[0]?.textContent ?? null }
  get readoutClass() { return this.domEl?.children?.[0]?.className ?? null }
}
const tick = () => new Promise((resolve) => setTimeout(resolve, 5))
const marked = (graph, hook) => !!graph[hook]?.__epsRcNodeWatch

// ---- A. multiplier at the ROOT, the change happens INSIDE a subgraph ----------
{
  const root = new FakeGraph()
  app.graph = root
  const S = root.add(subnode(root, 10, 'Models', [], ['model']))
  const sw = S.subgraph.add(modelSwitcher(2, 6))
  wire(S.subgraph, S.subgraph.add(loader(3)), 0, sw, 0)
  wire(S.subgraph, S.subgraph.add(loader(4)), 0, sw, 1)
  wire(S.subgraph, sw, 0, OUTPUT, 0)
  const nb = root.add(notebook(7, 1))
  const M = root.add(new MultNode({ id: 5 }))
  wire(root, S, 0, M, slot('model'))
  wire(root, nb, 0, M, slot('text'))
  cs.attach(M)
  const A = { first: M.readout }
  A.armed = {
    root: ['onNodeAdded', 'onNodeRemoved', 'onAfterChange'].map((h) => marked(root, h)),
    sub: ['onNodeAdded', 'onNodeRemoved', 'onAfterChange'].map((h) => marked(S.subgraph, h))
  }
  const add = (id, at) => {
    const l = loader(id)
    wire(S.subgraph, l, 0, sw, at)
    S.subgraph.add(l)
  }
  // 1) an event fired on the SUBGRAPH's own hook reaches a ROOT multiplier
  add(5, 2)
  await tick()
  A.afterSubAdd = M.readout
  // 2) core restores the subgraph's hook to the value it captured (ours dropped)
  S.subgraph.onNodeAdded = undefined
  A.deafHookIsOurs = marked(S.subgraph, 'onNodeAdded')
  add(6, 3)
  await tick()
  A.deaf = M.readout // the new loader went unnoticed: the scenario is real
  // 3) the next lone recompute re-verifies every graph's hooks
  M.onConnectionsChange()
  A.healed = { hookIsOurs: marked(S.subgraph, 'onNodeAdded'), readout: M.readout }
  // 4) ...so a later subgraph add reaches the multiplier again
  add(7, 4)
  await tick()
  A.afterHeal = M.readout
  // 5) removal inside the subgraph is noticed too
  const doomed = S.subgraph.getNodeById(7)
  sw.disconnectInput(4)
  S.subgraph.remove(doomed)
  await tick()
  A.afterRemove = M.readout
  out.rootWatchesSubgraph = A
}

// ---- B. multiplier INSIDE a subgraph, the change happens at the ROOT ----------
{
  const root = new FakeGraph()
  app.graph = root
  const sw = root.add(modelSwitcher(3, 5))
  wire(root, root.add(loader(20)), 0, sw, 0)
  wire(root, root.add(loader(21)), 0, sw, 1)
  const S = root.add(subnode(root, 10, 'Runner', ['model'], []))
  wire(root, sw, 0, S, 0)
  const M = S.subgraph.add(new MultNode({ id: 2 }))
  wire(S.subgraph, INPUT, 0, M, slot('model'))
  wire(S.subgraph, S.subgraph.add(notebook(3, 2)), 0, M, slot('text'))
  cs.attach(M)
  const B = { first: M.readout }
  const l = loader(22)
  wire(root, l, 0, sw, 2)
  root.add(l)
  await tick()
  B.afterRootAdd = M.readout
  out.subgraphWatchesRoot = B
}

// ---- C. one pass per tick on the ROOT, one shared snapshot --------------------
{
  const root = new FakeGraph()
  app.graph = root
  let snapshotsBuilt = 0
  const probe = loader(40)
  Object.defineProperty(probe, 'widgets', { get() { snapshotsBuilt += 1; return [] }, set() {} })
  root.add(probe)
  const S = root.add(subnode(root, 10, 'Holder', [], []))
  const first = root.add(new MultNode({ id: 5 }))
  const second = S.subgraph.add(new MultNode({ id: 2 }))
  for (const m of [first, second]) {
    const nb = (m.graph || root).add(notebook(m.id + 100, 2))
    wire(m.graph, nb, 0, m, slot('text'))
    cs.attach(m)
  }
  await tick()
  snapshotsBuilt = 0
  for (let i = 0; i < 5; i++) root.add(loader(50 + i)) // five events, one tick
  S.subgraph.add(loader(60)) // ...and one from the subgraph
  await tick()
  out.coalesced = {
    snapshotsBuilt,
    queuedFlagCleared: root.__epsRcRefreshQueued === false,
    subFlag: S.subgraph.__epsRcRefreshQueued ?? null
  }
}

// ---- D. a shared definition: instances agree / disagree on the node's line ----
{
  const mkShared = (left, right) => {
    const root = new FakeGraph()
    app.graph = root
    const S1 = root.add(subnode(root, 10, 'Twin', ['t'], []))
    const S2 = root.add(subnode(root, 11, 'Twin', ['t'], []))
    S2.subgraph = S1.subgraph
    const M = S1.subgraph.add(new MultNode({ id: 20 }))
    wire(S1.subgraph, INPUT, 0, M, slot('text'))
    wire(root, root.add(notebook(1, left)), 0, S1, 0)
    wire(root, root.add(notebook(2, right)), 0, S2, 0)
    cs.attach(M)
    return { root, M }
  }
  const differ = mkShared(3, 5)
  const agree = mkShared(3, 3)
  out.sharedReadout = {
    differ: [differ.M.readout, differ.M.readoutClass],
    agree: [agree.M.readout, agree.M.readoutClass]
  }
}

// ---- E. attach before the node has a graph/id (nodeCreated), then add ---------
{
  const root = new FakeGraph()
  app.graph = root
  const nb = root.add(notebook(7, 2))
  const M = new MultNode({ id: -1 })
  cs.attach(M) // node.graph is null here, exactly like nodeCreated
  const E_ = { beforeAdd: M.readout, armedAtAttach: marked(root, 'onNodeAdded') }
  root.add(M)
  M.id = 5
  wire(root, nb, 0, M, slot('text'))
  await tick()
  E_.afterAdd = M.readout
  out.createdThenAdded = E_
}

// ---- F. an orphan definition (no SubgraphNode instantiates it) ----------------
{
  const root = new FakeGraph()
  app.graph = root
  const orphan = new FakeGraph({ name: 'Orphan', rootGraph: root })
  const M = orphan.add(new MultNode({ id: 2 }))
  wire(orphan, orphan.add(notebook(3, 2)), 0, M, slot('text'))
  cs.attach(M)
  out.orphan = M.readout
}

console.log(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def nested_watch(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """attach() + the graph watch against the fake nested litegraph and a
    stub DOM (one Node run)."""
    layout = build_layout(
        tmp_path_factory.mktemp("cross_sweep_nested_watch"), eps_image=("cross_sweep.js",)
    )
    return run_probe(layout, NESTED_WATCH_PROBE_JS)


def test_readout_watch_is_armed_on_every_graph(nested_watch: dict) -> None:
    """attach() arms the root AND the subgraph (a subgraph's add/remove hooks
    fire only on the subgraph itself); every hook carries our owner mark."""
    got = nested_watch["rootWatchesSubgraph"]
    assert got["first"] == "Runs: 2 — 2 sweep steps × 1 pair"
    assert got["armed"] == {"root": [True, True, True], "sub": [True, True, True]}


def test_a_change_in_another_graph_recomputes_the_readout(nested_watch: dict) -> None:
    """The multiplier is at the ROOT; the loader is added INSIDE a subgraph
    (an event on the subgraph's own hook) -- and vice versa."""
    got = nested_watch["rootWatchesSubgraph"]
    assert got["afterSubAdd"] == "Runs: 3 — 3 sweep steps × 1 pair"
    inverse = nested_watch["subgraphWatchesRoot"]
    assert inverse["first"] == "Runs: 4 — 2 sweep steps × 2 pairs"
    assert inverse["afterRootAdd"] == "Runs: 6 — 3 sweep steps × 2 pairs"


def test_core_restoring_a_subgraph_hook_is_healed_not_one_shot(nested_watch: dict) -> None:
    """Core restores graph hooks on subgraph enter/exit. The scenario is
    proven real first (the add goes unnoticed while the hook is gone), then
    the next recompute re-verifies EVERY graph and a later add lands again."""
    got = nested_watch["rootWatchesSubgraph"]
    assert got["deafHookIsOurs"] is False
    assert got["deaf"] == "Runs: 3 — 3 sweep steps × 1 pair"
    assert got["healed"] == {
        "hookIsOurs": True,
        "readout": "Runs: 4 — 4 sweep steps × 1 pair",
    }
    assert got["afterHeal"] == "Runs: 5 — 5 sweep steps × 1 pair"
    assert got["afterRemove"] == "Runs: 4 — 4 sweep steps × 1 pair"


def test_burst_of_events_is_one_pass_with_one_shared_snapshot(nested_watch: dict) -> None:
    """Six events (five at the root, one in a subgraph) in the same tick, two
    multipliers (one nested): the snapshot is built exactly ONCE."""
    got = nested_watch["coalesced"]
    assert got["snapshotsBuilt"] == 1
    assert got["queuedFlagCleared"] is True
    # the coalescing flag lives on the ROOT, never on a subgraph
    assert got["subFlag"] is None


def test_shared_definition_readout_never_overclaims(nested_watch: dict) -> None:
    got = nested_watch["sharedReadout"]
    assert got["differ"] == [
        "Runs: ≥ 5 — this subgraph is used 2 times with different counts (3, 5)",
        "eps-rc-line eps-rc-warn",
    ]
    assert got["agree"] == ["Runs: 3 — 1 sweep step × 3 pairs", "eps-rc-line"]


def test_attach_before_the_node_is_in_any_graph_still_settles(nested_watch: dict) -> None:
    """nodeCreated runs before the node has a graph or an id: the first paint
    is the old "not an EPSCrossSweep" text, the root's watch is armed from
    app.graph, and the graph add heals it through the whole-workflow pass."""
    got = nested_watch["createdThenAdded"]
    assert "is not an EPSCrossSweep" in got["beforeAdd"]
    assert got["armedAtAttach"] is True
    assert got["afterAdd"] == "Runs: 2 — 1 sweep step × 2 pairs"


def test_orphan_definition_falls_back_to_its_own_graph(nested_watch: dict) -> None:
    """A node in a definition NO SubgraphNode instantiates is unreachable from
    the root: it estimates over its own graph (the pre-1.0.0 behaviour)
    instead of painting a misleading error."""
    assert nested_watch["orphan"] == "Runs: 2 — 1 sweep step × 2 pairs"
