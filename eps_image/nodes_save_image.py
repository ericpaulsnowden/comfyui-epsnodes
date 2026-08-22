"""EPS Save Image -- a SaveImage-compatible saver that BAKES provenance
(FORMAT.md §6.14, provenance roadmap M2+M3; owner goal 2026-08-18: "drop a
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

**M3 -- full pinning (v0.71.0, owner 2026-08-18/21).** M2 recreated a run
with the library AS IT IS NOW: edit the Prompt Notebook entry or the Apply
LoRA Set's rows afterwards and the recreation drifts. At save time this
node now also walks the hidden PROMPT for every ``LoraLibraryNotebook`` /
``LoraLibraryApplySet`` node, resolves their file-backed references to
VALUES through this pack's own stores (the notebook's selected entries'
text; the set's normalized rows -- via the SAME helpers the nodes' run
paths use), and bakes them into those nodes' TAIL-appended ``pinned`` /
``pinned_state`` widgets in the workflow chunk (+ the prompt chunk), so the
dropped workflow is byte-faithful. A multi-select Notebook is narrowed to
THIS run's entry when exactly one of its entries matches run_info's
``name`` or ``text``; otherwise (a notebook wired elsewhere, e.g. a
negative prompt) the whole selection is pinned. A node whose pin widget is
already non-empty keeps it (re-saving a recreated run preserves the
original capture). Every store error skips that node with a warning --
pinning never fails the queue. The ``eps_run`` chunk lists the pinned node
ids. The lora_library modules are imported lazily inside ``save`` (never at
module scope, never torch).

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
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any, NamedTuple

from .nodes_cross_sweep import EPSCrossSweep

logger = logging.getLogger("eps_image")

CATEGORY_NAME = "EPSNodes"
EPS_RUN_CHUNK = "eps_run"

#: The class ids (FROZEN, FORMAT.md §8) of the two pinnable nodes and the
#: TAIL widgets that carry their pins (FORMAT.md §6.1 / §6.2).
NOTEBOOK_CLASS = "LoraLibraryNotebook"
NOTEBOOK_PIN_WIDGET = "pinned"
APPLY_SET_CLASS = "LoraLibraryApplySet"
APPLY_SET_PIN_WIDGET = "pinned_state"
MULTIPLIER_CLASS = "EPSCrossSweep"
SOLO_WIDGET = "solo_run"

_WIDGET_KINDS = ("STRING", "INT", "FLOAT", "BOOLEAN")


class PinnedWidget(NamedTuple):
    """One captured pin: bake *value* into *widget* of every node of
    *class_type* at the keyed node id (workflow + prompt chunks)."""

    class_type: str
    widget: str
    value: str


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


# ------------------------------------------------------------ widget index


def _iter_widgets(node_class: Any) -> Iterator[tuple[str, Any, dict[str, Any]]]:
    """``(name, kind, options)`` for every SERIALIZED widget of *node_class*
    in ``widgets_values`` order (FORMAT.md §8: widgets_values restores
    positionally): every non-forceInput, widget-typed input (COMBO list or
    STRING/INT/FLOAT/BOOLEAN) in INPUT_TYPES declaration order, required
    section first, then optional. Sockets (MODEL, CLIP, IMAGE, ...) and
    forceInput inputs are not widgets and are skipped."""
    spec = node_class.INPUT_TYPES()
    for section in ("required", "optional"):
        for name, definition in spec.get(section, {}).items():
            kind = definition[0]
            options = (
                definition[1] if len(definition) > 1 and isinstance(definition[1], dict) else {}
            )
            if options.get("forceInput"):
                continue
            if isinstance(kind, list) or kind in _WIDGET_KINDS:
                yield name, kind, options


def widget_index(node_class: Any, widget_name: str) -> int:
    """The positional index of *widget_name* among *node_class*'s
    SERIALIZED widgets (see :func:`_iter_widgets`). Derived from
    INPUT_TYPES, never hand-typed, so a future tail widget can't drift
    this. Raises ``RuntimeError`` when the class has no such widget."""
    for index, (name, _kind, _options) in enumerate(_iter_widgets(node_class)):
        if name == widget_name:
            return index
    raise RuntimeError(
        f"EPS Save Image: {getattr(node_class, '__name__', node_class)} has no "
        f"{widget_name!r} widget"
    )


def widget_defaults(node_class: Any) -> list[Any]:
    """Default values for *node_class*'s serialized widgets, in order --
    used to pad a SHORT saved widgets_values (a workflow saved before a
    tail widget existed) so a baked value lands at its real index."""
    out: list[Any] = []
    for _name, kind, options in _iter_widgets(node_class):
        if isinstance(kind, list):
            out.append(options.get("default", kind[0] if kind else ""))
        else:
            out.append(options.get("default", ""))
    return out


def solo_widget_index() -> int:
    """``widget_index(EPSCrossSweep, "solo_run")`` -- the M2 name, kept."""
    return widget_index(EPSCrossSweep, SOLO_WIDGET)


def _multiplier_widget_defaults() -> list[Any]:
    """``widget_defaults(EPSCrossSweep)`` -- the M2 name, kept."""
    return widget_defaults(EPSCrossSweep)


# ----------------------------------------------------- lora_library access


def _lora_library() -> tuple[Any, Any, Any, Any]:
    """``(markdown_store, nodes_notebook, nodes_sets, sets_store)`` -- lazy,
    never at module scope. Real ComfyUI loads the pack as ONE nested
    package (eps_image and lora_library are sibling sub-packages, so ``..``
    reaches their shared parent); the flat form is what pytest's rootdir
    setup resolves (``resolution_presets_store.py`` documents the same
    two-branch import). Raises ``ImportError`` if neither works."""
    try:
        from ..lora_library import markdown_store, nodes_notebook, nodes_sets, sets_store
    except ImportError:
        from lora_library import markdown_store, nodes_notebook, nodes_sets, sets_store
    return markdown_store, nodes_notebook, nodes_sets, sets_store


def _pinnable_class(class_type: str) -> Any:
    """The node class behind a pinnable *class_type* (for
    :func:`widget_index`), the multiplier, or ``None``."""
    if class_type == MULTIPLIER_CLASS:
        return EPSCrossSweep
    try:
        _md, nodes_notebook, nodes_sets, _ss = _lora_library()
    except ImportError:
        return None
    if class_type == NOTEBOOK_CLASS:
        return nodes_notebook.LoraLibraryNotebook
    if class_type == APPLY_SET_CLASS:
        return nodes_sets.LoraLibraryApplySet
    return None


def _utc_now() -> str:
    """ISO-8601 UTC, second precision, ``Z`` suffix -- the pins' ``captured``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------- pin capture


def select_this_runs_entries(
    entries: list[dict[str, str]], run_info: dict[str, Any]
) -> list[dict[str, str]]:
    """The M3 narrowing rule for a Notebook's selection: when EXACTLY ONE
    entry's ``name`` equals run_info's ``name`` or ``text`` equals
    run_info's ``text`` (a null run_info field never matches), that entry
    alone is this run's and is what gets pinned; otherwise the WHOLE
    selection is pinned (a single-entry selection trivially; a notebook
    wired elsewhere -- a negative prompt, a caption source -- whose entries
    never match this run; or an ambiguous match)."""
    name = run_info.get("name")
    text = run_info.get("text")
    matches = [
        entry
        for entry in entries
        if (isinstance(name, str) and entry.get("name") == name)
        or (isinstance(text, str) and entry.get("text") == text)
    ]
    return matches if len(matches) == 1 else list(entries)


def _capture_notebook(
    nodes_notebook: Any,
    node_id: str,
    inputs: dict[str, Any],
    run_info: dict[str, Any],
    captured: str,
) -> PinnedWidget | None:
    existing = inputs.get(NOTEBOOK_PIN_WIDGET)
    if isinstance(existing, str) and existing.strip():
        # Re-saving a recreated run: the original capture stays byte-exact.
        return None
    file = inputs.get("file", "")
    entry = inputs.get("entry", "")
    if not isinstance(file, str) or not isinstance(entry, str):
        logger.warning(
            "EPS Save Image: Prompt Notebook %s has a wired file/entry; not pinning it",
            node_id,
        )
        return None
    context = getattr(nodes_notebook, "_context", None)
    if context is None:
        logger.warning(
            "EPS Save Image: Prompt Notebook %s has no library context; not pinning it",
            node_id,
        )
        return None
    try:
        texts, names = nodes_notebook.resolve_selection(context, file, entry)
    except Exception as exc:  # ValueError / MarkdownStoreError / OSError -- never fail the queue
        logger.warning(
            "EPS Save Image: could not resolve Prompt Notebook %s for pinning (%s); "
            "saving it unpinned",
            node_id,
            exc,
        )
        return None
    entries = [{"name": n, "text": t} for t, n in zip(texts, names, strict=True)]
    if not entries:
        return None
    selected = select_this_runs_entries(entries, run_info)
    pin = nodes_notebook.make_pin(
        selected, file=file, token=run_info.get("token"), captured=captured
    )
    return PinnedWidget(NOTEBOOK_CLASS, NOTEBOOK_PIN_WIDGET, json.dumps(pin))


def _capture_apply_set(
    nodes_sets: Any,
    sets_store: Any,
    node_id: str,
    inputs: dict[str, Any],
    run_info: dict[str, Any],
    captured: str,
) -> PinnedWidget | None:
    existing = inputs.get(APPLY_SET_PIN_WIDGET)
    if isinstance(existing, str) and existing.strip():
        return None
    slug = inputs.get("set")
    if not isinstance(slug, str) or slug in ("None", ""):
        return None  # nothing to pin: passthrough state, or a wired combo
    context = getattr(nodes_sets, "_context", None)
    if context is None:
        logger.warning(
            "EPS Save Image: Apply LoRA Set %s has no library context; not pinning it", node_id
        )
        return None
    try:
        set_data = sets_store.load_set(context, slug)
    except Exception as exc:  # SetValidationError / OSError -- never fail the queue
        logger.warning(
            "EPS Save Image: could not load set %r for Apply LoRA Set %s (%s); saving it unpinned",
            slug,
            node_id,
            exc,
        )
        return None
    if set_data is None:
        logger.warning(
            "EPS Save Image: set %r (Apply LoRA Set %s) has no file on disk; saving it unpinned",
            slug,
            node_id,
        )
        return None
    pin = nodes_sets.make_pin(slug, set_data, token=run_info.get("token"), captured=captured)
    return PinnedWidget(APPLY_SET_CLASS, APPLY_SET_PIN_WIDGET, json.dumps(pin))


def capture_pins(prompt: Any, run_info: dict[str, Any]) -> dict[str, PinnedWidget]:
    """Walk the hidden PROMPT (execution id -> ``{"class_type", "inputs"}``;
    ids are ``"5"`` at the root, ``"5:3"`` inside a subgraph -- the same
    shape :func:`find_node_in_workflow` takes) and build a pin FROM THE
    STORES for every Prompt Notebook / Apply LoRA Set node: the notebook's
    selected entries' text (narrowed to THIS run's entry per
    :func:`select_this_runs_entries`); the set's normalized dict. Skips,
    with a warning, any node that is already pinned, has a wired
    file/entry/set, has no context, or whose store raises -- pinning never
    fails the queue. Returns ``{node_id: PinnedWidget}``."""
    pins: dict[str, PinnedWidget] = {}
    if not isinstance(prompt, dict):
        return pins
    try:
        _markdown_store, nodes_notebook, nodes_sets, sets_store = _lora_library()
    except ImportError as exc:
        logger.warning("EPS Save Image: lora_library unavailable (%s); nothing pinned", exc)
        return pins
    captured = _utc_now()
    for node_id, node in prompt.items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        class_type = node.get("class_type")
        if class_type == NOTEBOOK_CLASS:
            pin = _capture_notebook(nodes_notebook, str(node_id), inputs, run_info, captured)
        elif class_type == APPLY_SET_CLASS:
            pin = _capture_apply_set(
                nodes_sets, sets_store, str(node_id), inputs, run_info, captured
            )
        else:
            continue
        if pin is not None:
            pins[str(node_id)] = pin
    return pins


# ------------------------------------------------------------------ baking


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


def _bake_widget(
    workflow: Any,
    prompt: Any,
    node_id: Any,
    node_class: Any,
    class_type: str,
    widget: str,
    value: Any,
) -> bool:
    """Set *widget* = *value* on node *node_id* in BOTH chunks (in place --
    callers pass their deep copies): the workflow node's
    ``widgets_values[widget_index(node_class, widget)]`` (a short array is
    padded with the class's widget defaults first) and the prompt entry's
    ``inputs[widget]``. Either chunk landing counts; a node missing from
    both, or of another class, is left alone. Returns whether it landed."""
    landed = False
    node = find_node_in_workflow(workflow, node_id)
    if node is not None and node.get("type") == class_type:
        values = node.get("widgets_values")
        values = list(values) if isinstance(values, list) else []
        index = widget_index(node_class, widget)
        defaults = widget_defaults(node_class)
        while len(values) <= index:
            values.append(defaults[len(values)] if len(values) < len(defaults) else "")
        values[index] = value
        node["widgets_values"] = values
        landed = True
    if isinstance(prompt, dict):
        entry = prompt.get(str(node_id))
        if isinstance(entry, dict) and entry.get("class_type") == class_type:
            inputs = entry.setdefault("inputs", {})
            if isinstance(inputs, dict):
                inputs[widget] = value
                landed = True
    return landed


def bake_provenance(
    workflow: Any,
    prompt: Any,
    run_info: dict[str, Any],
    pins: dict[str, PinnedWidget] | None = None,
) -> tuple[Any, Any, bool, list[str]]:
    """Deep-copy *workflow* and *prompt* with (a) the multiplier named by
    ``run_info["node"]`` pre-soloed to ``run_info["token"]`` and (b) every
    *pins* entry baked into its node's pin widget (M3). Returns
    ``(workflow, prompt, baked, pinned_ids)``: ``baked`` is False when the
    multiplier could not be found in EITHER chunk; ``pinned_ids`` lists the
    node ids whose pin landed in at least one chunk (the ``eps_run``
    chunk's ``"pinned"``). Never raises on odd shapes -- the standard
    chunks still get written; the inputs are never mutated."""
    workflow_out = copy.deepcopy(workflow) if isinstance(workflow, dict) else workflow
    prompt_out = copy.deepcopy(prompt) if isinstance(prompt, dict) else prompt

    token = run_info.get("token")
    node_id = run_info.get("node")
    baked = False
    if isinstance(token, str) and node_id is not None:
        baked = _bake_widget(
            workflow_out, prompt_out, node_id, EPSCrossSweep, MULTIPLIER_CLASS, SOLO_WIDGET, token
        )

    pinned_ids: list[str] = []
    for pin_node_id, pin in (pins or {}).items():
        node_class = _pinnable_class(pin.class_type)
        if node_class is None:
            logger.warning(
                "EPS Save Image: cannot bake a pin for unknown class %r (node %s)",
                pin.class_type,
                pin_node_id,
            )
            continue
        if _bake_widget(
            workflow_out, prompt_out, pin_node_id, node_class, pin.class_type, pin.widget, pin.value
        ):
            pinned_ids.append(str(pin_node_id))
    return workflow_out, prompt_out, baked, pinned_ids


def bake_solo(workflow: Any, prompt: Any, run_info: dict[str, Any]) -> tuple[Any, Any, bool]:
    """The M2 name: :func:`bake_provenance` with no pins, returning
    ``(workflow, prompt, baked)``."""
    workflow_out, prompt_out, baked, _pinned = bake_provenance(workflow, prompt, run_info, {})
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
        "solo_run already set to the run that made it, and with the Prompt "
        "Notebook text and Apply LoRA Set rows pinned to the values used: "
        "drop the image onto the canvas and the whole workflow loads ready to "
        "recreate just that one image, exactly, even after the library was "
        "edited. With run_info unwired it behaves exactly like Save Image."
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
                            "own run, with Prompt Notebook text and Apply LoRA Set "
                            "rows pinned to the values used. Leave unwired for a "
                            "plain Save Image."
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
        pinned_ids: list[str] = []
        if info is not None:
            # M3: capture BEFORE baking, from the stores, never failing the
            # queue -- a capture error just means that node saves unpinned.
            try:
                pins = capture_pins(prompt_data, info)
            except Exception:
                logger.exception("EPS Save Image: pin capture failed; saving unpinned")
                pins = {}
            workflow_data = extra.get("workflow")
            workflow_data, prompt_data, baked, pinned_ids = bake_provenance(
                workflow_data, prompt_data, info, pins
            )
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
                        EPS_RUN_CHUNK,
                        json.dumps(
                            {**info, "baked": baked, "pinned": pinned_ids, "format": 1}
                        ),
                    )
            file = f"{filename.replace('%batch_num%', str(batch_number))}_{counter:05}_.png"
            img.save(os.path.join(full_output_folder, file), pnginfo=metadata, compress_level=4)
            results.append({"filename": file, "subfolder": subfolder, "type": "output"})
            counter += 1
        return {"ui": {"images": results}, "result": (images,)}
