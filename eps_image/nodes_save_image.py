"""EPS Save Image -- a SaveImage-compatible saver that BAKES provenance
(FORMAT.md §6.14, provenance roadmap M2; owner goal 2026-08-18: "drop a
single image from a set onto comfyui and recreate just that image").

Core's ``SaveImage`` already embeds the queue's ``prompt`` + ``workflow``
as PNG text chunks -- which is why dropping any ComfyUI image loads the
WHOLE workflow. Under the Run Multiplier every image of a set carries the
SAME chunks (the hidden inputs are per-queue), so a dropped file cannot
say which of the N runs made it. M1 (v0.67.0) put a pure-index run token
in every filename and added ``solo_run``; this node closes the loop: wired
to the multiplier's new ``run_info`` output (one JSON per run, index-
aligned with ``save_prefix``), it writes the standard chunks with THAT
run's ``solo_run`` already set -- in the ``workflow`` chunk (the
multiplier node found by execution path id, subgraph paths like ``"5:3"``
resolved through ``definitions.subgraphs``) AND in the ``prompt`` chunk --
plus an ``eps_run`` chunk carrying the run_info verbatim. Drop the image:
ComfyUI's own loader does the rest and the graph comes up pre-soloed to
exactly that run. No custom drop handler is needed for baked files; the
frontend's filename fallback (``web/eps_image/save_image.js``) covers
pre-M2 files that only have the M1 token in their name.

Signature and on-disk behavior otherwise mirror core ``SaveImage``
(``images`` + ``filename_prefix``, counter naming via
``folder_paths.get_save_image_path``, ``OUTPUT_NODE``, ``ui.images``), so
it is a drop-in replacement -- with ``run_info`` unwired it IS SaveImage
(standard chunks, nothing baked). No torch/PIL/ComfyUI import at module
scope (tests drive it with fakes); everything heavy is inside ``save``.
"""

from __future__ import annotations

import copy
import json
import logging
import os
from typing import Any

from .nodes_cross_sweep import EPSCrossSweep

logger = logging.getLogger("eps_image")

CATEGORY_NAME = "EPSNodes"
EPS_RUN_CHUNK = "eps_run"


def _unwrap(value: Any) -> Any:
    """Hidden inputs arrive plain for a mapped node, but a 1-element list
    when core treats the node as list-taking -- tolerate both (the
    ``_unwrap_hidden`` idiom the multiplier already uses)."""
    if isinstance(value, list) and len(value) == 1:
        return value[0]
    return value


def parse_run_info(raw: Any) -> dict[str, Any] | None:
    """The multiplier's ``run_info`` JSON as a dict, or None for anything
    that isn't one (unwired, blank, malformed) -- degrade to plain save."""
    raw = _unwrap(raw)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        logger.warning(
            "EPS Save Image: run_info is not JSON (%r); saving without provenance", raw[:80]
        )
        return None
    if not isinstance(data, dict) or not isinstance(data.get("token"), str):
        return None
    return data


def solo_widget_index() -> int:
    """The positional index of ``solo_run`` among the multiplier's
    SERIALIZED widgets (widgets_values restores positionally -- FORMAT §8):
    every non-forceInput, widget-typed input in INPUT_TYPES declaration
    order. Derived, never hand-typed, so a future tail widget can't drift
    this."""
    spec = EPSCrossSweep.INPUT_TYPES()
    index = 0
    for section in ("required", "optional"):
        for name, definition in spec.get(section, {}).items():
            kind = definition[0]
            options = (
                definition[1] if len(definition) > 1 and isinstance(definition[1], dict) else {}
            )
            if options.get("forceInput"):
                continue
            is_widget = isinstance(kind, list) or kind in ("STRING", "INT", "FLOAT", "BOOLEAN")
            if not is_widget:
                continue
            if name == "solo_run":
                return index
            index += 1
    raise RuntimeError("EPS Save Image: EPSCrossSweep has no solo_run widget")


def _multiplier_widget_defaults() -> list[Any]:
    """Default values for the multiplier's serialized widgets, in order --
    used to pad a SHORT saved widgets_values (a workflow saved before a
    tail widget existed) so solo_run lands at its real index."""
    spec = EPSCrossSweep.INPUT_TYPES()
    out: list[Any] = []
    for section in ("required", "optional"):
        for _name, definition in spec.get(section, {}).items():
            kind = definition[0]
            options = (
                definition[1] if len(definition) > 1 and isinstance(definition[1], dict) else {}
            )
            if options.get("forceInput"):
                continue
            if isinstance(kind, list):
                out.append(options.get("default", kind[0] if kind else ""))
            elif kind in ("STRING", "INT", "FLOAT", "BOOLEAN"):
                out.append(options.get("default", ""))
    return out


def find_node_in_workflow(workflow: Any, path_id: Any) -> dict[str, Any] | None:
    """The serialized node *path_id* names in *workflow* -- ``"5"`` at the
    root, ``"5:3"`` = node 3 inside the subgraph DEFINITION that root node
    5 instantiates (its ``type`` is the subgraph uuid, the definition lives
    in ``definitions.subgraphs``), deeper paths recurse. Editing a
    definition node soloes every instance of that subgraph -- the right
    answer for "recreate this one image". None when anything is missing."""
    if not isinstance(workflow, dict) or path_id is None:
        return None
    parts = str(path_id).split(":")
    definitions = workflow.get("definitions") or {}
    subgraphs = {
        str(sg.get("id")): sg
        for sg in (definitions.get("subgraphs") or [])
        if isinstance(sg, dict)
    }
    nodes = workflow.get("nodes") or []
    node: dict[str, Any] | None = None
    for depth, part in enumerate(parts):
        node = next(
            (n for n in nodes if isinstance(n, dict) and str(n.get("id")) == part), None
        )
        if node is None:
            return None
        if depth < len(parts) - 1:
            subgraph = subgraphs.get(str(node.get("type")))
            if subgraph is None:
                return None
            nodes = subgraph.get("nodes") or []
    return node


def bake_solo(workflow: Any, prompt: Any, run_info: dict[str, Any]) -> tuple[Any, Any, bool]:
    """Deep-copy *workflow* and *prompt* with the multiplier named by
    ``run_info["node"]`` pre-soloed to ``run_info["token"]``. Returns
    ``(workflow, prompt, baked)``; ``baked`` is False when the multiplier
    could not be found in EITHER chunk (the copies are then untouched).
    Never raises on odd shapes -- the standard chunks still get written."""
    token = run_info.get("token")
    node_id = run_info.get("node")
    if not isinstance(token, str) or node_id is None:
        return workflow, prompt, False
    baked = False
    workflow_out = copy.deepcopy(workflow) if isinstance(workflow, dict) else workflow
    prompt_out = copy.deepcopy(prompt) if isinstance(prompt, dict) else prompt

    node = find_node_in_workflow(workflow_out, node_id)
    if node is not None and node.get("type") == "EPSCrossSweep":
        values = node.get("widgets_values")
        values = list(values) if isinstance(values, list) else []
        index = solo_widget_index()
        defaults = _multiplier_widget_defaults()
        while len(values) <= index:
            values.append(defaults[len(values)] if len(values) < len(defaults) else "")
        values[index] = token
        node["widgets_values"] = values
        baked = True

    if isinstance(prompt_out, dict):
        entry = prompt_out.get(str(node_id))
        if isinstance(entry, dict) and entry.get("class_type") == "EPSCrossSweep":
            inputs = entry.setdefault("inputs", {})
            if isinstance(inputs, dict):
                inputs["solo_run"] = token
                baked = True
    return workflow_out, prompt_out, baked


class EPSSaveImage:
    """FORMAT.md §6.14 -- see the module docstring."""

    CATEGORY = CATEGORY_NAME
    FUNCTION = "save"
    OUTPUT_NODE = True
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("images",)
    OUTPUT_TOOLTIPS = ("The saved images, passed through unchanged (like Save Image).",)
    DESCRIPTION = (
        "Save Image with provenance baked in. Wire images and filename_prefix "
        "exactly like the core Save Image node -- and wire EPS Run Multiplier's "
        "run_info output too. Every file then carries its own workflow with "
        "solo_run already set to the run that made it: drop the image onto "
        "the canvas and the whole workflow loads ready to recreate just that "
        "one image. With run_info unwired it behaves exactly like Save Image."
    )

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "images": ("IMAGE", {"tooltip": "The images to save."}),
                "filename_prefix": (
                    "STRING",
                    {
                        "default": "EPS",
                        "tooltip": (
                            "The prefix for the file to save -- wire EPS Run "
                            "Multiplier's save_prefix output here for per-run "
                            "folders and token-named files. Same rules as Save "
                            "Image (subfolders via /, %date% style tokens)."
                        ),
                    },
                ),
            },
            "optional": {
                "run_info": (
                    "STRING",
                    {
                        "forceInput": True,
                        "tooltip": (
                            "EPS Run Multiplier's run_info output: one JSON per "
                            "run, index-aligned with save_prefix. When wired, each "
                            "saved file's embedded workflow is pre-soloed to its "
                            "own run. Leave unwired for a plain Save Image."
                        ),
                    },
                ),
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
        }

    def save(
        self,
        images: Any,
        filename_prefix: str = "EPS",
        run_info: Any = None,
        prompt: Any = None,
        extra_pnginfo: Any = None,
    ) -> dict[str, Any]:
        import folder_paths  # ComfyUI's own module; only importable inside ComfyUI
        import numpy as np
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo

        try:
            from comfy.cli_args import args as comfy_args

            disable_metadata = bool(getattr(comfy_args, "disable_metadata", False))
        except Exception:  # direct callers/tests: metadata on
            disable_metadata = False

        prefix = str(_unwrap(filename_prefix) or "EPS")
        info = parse_run_info(run_info)
        prompt_data = _unwrap(prompt)
        extra = _unwrap(extra_pnginfo)
        extra = dict(extra) if isinstance(extra, dict) else {}
        baked = False
        if info is not None:
            workflow_data = extra.get("workflow")
            workflow_data, prompt_data, baked = bake_solo(workflow_data, prompt_data, info)
            if workflow_data is not None:
                extra["workflow"] = workflow_data
            if not baked:
                logger.warning(
                    "EPS Save Image: multiplier %r not found in the workflow/prompt; "
                    "saving with the standard (un-soloed) chunks",
                    info.get("node"),
                )

        output_dir = folder_paths.get_output_directory()
        first = images[0]
        full_output_folder, filename, counter, subfolder, _prefix = (
            folder_paths.get_save_image_path(prefix, output_dir, first.shape[1], first.shape[0])
        )
        results: list[dict[str, Any]] = []
        for batch_number, image in enumerate(images):
            array = 255.0 * image.cpu().numpy()
            img = Image.fromarray(np.clip(array, 0, 255).astype(np.uint8))
            metadata = None
            if not disable_metadata:
                metadata = PngInfo()
                if prompt_data is not None:
                    metadata.add_text("prompt", json.dumps(prompt_data))
                for key, value in extra.items():
                    metadata.add_text(str(key), json.dumps(value))
                if info is not None:
                    metadata.add_text(
                        EPS_RUN_CHUNK, json.dumps({**info, "baked": baked, "format": 1})
                    )
            file = f"{filename.replace('%batch_num%', str(batch_number))}_{counter:05}_.png"
            img.save(os.path.join(full_output_folder, file), pnginfo=metadata, compress_level=4)
            results.append({"filename": file, "subfolder": subfolder, "type": "output"})
            counter += 1
        return {"ui": {"images": results}, "result": (images,)}
