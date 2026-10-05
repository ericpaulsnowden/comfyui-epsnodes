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

**Preview only (v1.1.0, owner request 2026-10-03: "modify the save node so
it has a preview toggle and can operate like a preview-only node without
needing to swap nodes").** A BOOLEAN ``preview_only`` widget (default off =
today's behaviour, byte for byte) turns this node into core's
``PreviewImage``: it writes to ``folder_paths.get_temp_directory()``,
reports ``"type": "temp"`` in ``ui.images``, appends ``_temp_`` + five
random lowercase letters to the prefix and compresses at level 1 -- exactly
what ``PreviewImage.__init__`` sets (read from the rig's ``nodes.py``). It
is a TAIL widget (after ``filename_prefix``, FORMAT.md §8), so ``["EPS"]``
from an older workflow loads with preview off and ``["EPS", true]`` from a
newer one loads on an older EPS build (a trailing extra value is ignored).
Everything else is deliberately UNCHANGED in preview mode: the prefix stays
the base (subfolders and the run token in the file name survive -- the
drop-fallback in ``web/eps_image/save_image.js`` reads tokens from file
names), and the provenance bake + PNG metadata still run, so a dropped
preview recreates its run just like a saved file (core ``PreviewImage``
embeds metadata too, unless ``--disable-metadata``).

**Formats (owner request 2026-10-04: "There is an advanced image save node
that has options for format, bit depth, and color space. Can those be added
to the eps save image.").** The node he means is core's ``SaveImageAdvanced``.
Four TAIL widgets (after ``preview_only``, FORMAT.md §8) bring its choices
here: ``format`` (png / exr / avif), ``bit_depth``, ``input_color_space``
and ``avif_crf`` -- the last three flagged ``advanced`` so core's frontend
tucks them behind "Show advanced inputs". The DEFAULTS (png, auto, sRGB)
take the exact PIL path this node has always used, so an old workflow and
every existing test produce the same bytes. Every OTHER combination calls
core's OWN encoders (``comfy_extras.nodes_images``: ``_encode_image`` +
``inject_png_metadata`` / ``inject_exr_metadata``, and ``_save_avif``),
imported lazily at call time and feature-detected by name. ComfyUI is GPL-3
and this pack is MIT: calling it at runtime is fine, COPYING its code is
not -- so there is no encoder here, only routing, naming and validation.
If the helper a format needs is missing (an older ComfyUI: AVIF arrived in
core 0.35, PNG 16-bit / EXR by 0.28) the node says so in plain language and
points at png 8-bit -- it never falls back to another format silently. The
same check runs at QUEUE time in ``VALIDATE_INPUTS`` (so an overnight batch
is refused immediately, not on its first save), together with the
format / bit-depth / color-space matrix. ``preview_only`` WINS: a preview is
always the plain temp PNG 8-bit, whatever ``format`` says. The baked prompt,
workflow, pins and ``eps_run`` record reach every format -- PNG tEXt chunks,
EXR header attributes, the AVIF Exif item -- through core's own metadata
helpers, and the bake-undo ``finally`` still restores the shared objects.

Signature and on-disk behavior otherwise mirror core ``SaveImage``
(``images`` + ``filename_prefix``, counter naming via
``folder_paths.get_save_image_path``, ``OUTPUT_NODE``, ``ui.images``), so
it is a drop-in replacement -- with ``run_info`` unwired it IS SaveImage
(standard chunks, nothing baked). No torch/PIL/ComfyUI import at module
scope (tests drive it with fakes); everything heavy is inside ``save``.
"""

from __future__ import annotations

import contextlib
import copy
import importlib
import json
import logging
import os
import random
import string
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any, ClassVar, NamedTuple

from .nodes_cross_sweep import EPSCrossSweep

logger = logging.getLogger("eps_image")

CATEGORY_NAME = "EPSNodes/Images"
EPS_RUN_CHUNK = "eps_run"

#: The class ids (FROZEN, FORMAT.md §8) of the two pinnable nodes and the
#: TAIL widgets that carry their pins (FORMAT.md §6.1 / §6.2), plus the
#: notebook's `drafts` audition buffer -- read (never written) here so a
#: pin captures the text the run actually used.
NOTEBOOK_CLASS = "LoraLibraryNotebook"
NOTEBOOK_PIN_WIDGET = "pinned"
NOTEBOOK_DRAFTS_WIDGET = "drafts"
APPLY_SET_CLASS = "LoraLibraryApplySet"
APPLY_SET_PIN_WIDGET = "pinned_state"
MULTIPLIER_CLASS = "EPSCrossSweep"
SOLO_WIDGET = "solo_run"

_WIDGET_KINDS = ("STRING", "INT", "FLOAT", "BOOLEAN")

#: The preview toggle (v1.1.0, FORMAT.md §6.14) and the three things core's
#: ``PreviewImage`` changes relative to ``SaveImage`` (rig ``nodes.py``:
#: ``output_dir = get_temp_directory()``, ``type = "temp"``,
#: ``compress_level = 1`` vs Save's 4). Named so a test can pin each.
PREVIEW_WIDGET = "preview_only"
OUTPUT_TYPE = "output"
PREVIEW_TYPE = "temp"
SAVE_COMPRESS_LEVEL = 4
PREVIEW_COMPRESS_LEVEL = 1
#: Strings a hand-built /prompt could carry for a true BOOLEAN. The frontend
#: always sends a real bool; anything outside this set (and outside bool/int/
#: float) reads as FALSE = save, because the failure that loses nothing is
#: the right one (a stray preview would silently not keep the user's file).
_TRUE_STRINGS = frozenset({"true", "1", "yes", "on"})


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


def parse_preview_only(raw: Any) -> bool:
    """The ``preview_only`` widget as a plain bool. Tolerates the 1-element
    list a list-taking node would see (:func:`_unwrap`, the same idiom the
    hidden inputs use -- the boolean may arrive wrapped), a real bool, a
    number, and the strings in :data:`_TRUE_STRINGS` (a hand-built
    /prompt). ``None`` (the input is absent in an older or hand-built API
    prompt -- it is a tail widget in ``optional``) and anything
    unrecognised mean ``False``: save, never silently preview."""
    raw = _unwrap(raw)
    if raw is None:
        return False
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return raw != 0
    if isinstance(raw, str):
        return raw.strip().lower() in _TRUE_STRINGS
    logger.warning(
        "EPS Save Image: %s is %r, not a boolean; saving normally", PREVIEW_WIDGET, raw
    )
    return False


def _temp_suffix() -> str:
    """``"_temp_"`` + five random lowercase letters -- core ``PreviewImage``'s
    ``prefix_append``. Core draws from a hand-typed alphabet that happens
    to omit ``w``; a plain a-z is what the owner asked for and nothing
    reads the letters back."""
    return "_temp_" + "".join(random.choice(string.ascii_lowercase) for _ in range(5))


# ---------------------------------------------------------------- formats

#: The four TAIL widgets (owner request 2026-10-04, FORMAT.md §6.14 "Formats").
#: Appended after ``preview_only`` -- ``widgets_values`` restores
#: POSITIONALLY (§8), so they are only ever appended, never inserted.
FORMAT_WIDGET = "format"
BIT_DEPTH_WIDGET = "bit_depth"
COLOR_SPACE_WIDGET = "input_color_space"
AVIF_CRF_WIDGET = "avif_crf"

FORMATS = ("png", "exr", "avif")
BIT_DEPTH_CHOICES = ("auto", "8-bit", "10-bit", "16-bit", "32-bit float")
#: Core's ``SaveImageAdvanced`` calls "HDR (HLG)" just "HDR"; the longer name
#: says what it is (BT.2100 HLG, as opposed to "HDR PQ"). It is mapped back
#: to core's name in :data:`_CORE_COLOR_SPACE` below, at the one call site.
COLOR_SPACE_CHOICES = ("sRGB", "linear", "HDR (HLG)", "HDR PQ")
DEFAULT_FORMAT = "png"
DEFAULT_BIT_DEPTH = "auto"
DEFAULT_COLOR_SPACE = "sRGB"
AVIF_CRF_MIN = 1
AVIF_CRF_MAX = 63
DEFAULT_AVIF_CRF = 18

#: Which bit depths / color spaces each format accepts -- the matrix the
#: owner asked for (it is core ``SaveImageAdvanced``'s own, with "auto"
#: allowed everywhere). ``auto_depth`` is what "auto" means for the format;
#: AVIF has none because core's own ``auto`` decides (8-bit YUV420 for sRGB,
#: 10-bit for either HDR space), so "auto" is handed through untouched.
FORMAT_MATRIX: dict[str, dict[str, Any]] = {
    "png": {
        "bit_depth": ("auto", "8-bit", "16-bit"),
        "color_space": ("sRGB",),
        "auto_depth": "8-bit",
    },
    "exr": {
        "bit_depth": ("auto", "32-bit float"),
        "color_space": ("sRGB", "linear", "HDR (HLG)"),
        "auto_depth": "32-bit float",
    },
    "avif": {
        "bit_depth": ("auto", "8-bit", "10-bit"),
        "color_space": ("sRGB", "HDR (HLG)", "HDR PQ"),
        "auto_depth": None,
    },
}

#: Our names -> the strings core's helpers compare against (read from the
#: rig's ``comfy_extras/nodes_images.py``: ``_encode_image`` tests
#: ``"sRGB"`` / ``"HDR"`` and lets anything else -- ``"linear"`` -- through
#: unconverted; ``_AVIF_COLOR_PROPERTIES`` keys are ``"sRGB"`` / ``"HDR"`` /
#: ``"HDR PQ"``).
_CORE_COLOR_SPACE = {
    "sRGB": "sRGB",
    "linear": "linear",
    "HDR (HLG)": "HDR",
    "HDR PQ": "HDR PQ",
}
#: Core's AVIF bit-depth names. Its ``bit_depth`` options are
#: ``auto`` / ``8-bit YUV420`` / ``10-bit YUV420``; the owner's list says the
#: shorter "8-bit" and "10-bit" mean exactly those two.
_CORE_AVIF_DEPTH = {"auto": "auto", "8-bit": "8-bit YUV420", "10-bit": "10-bit YUV420"}

#: The helpers each format needs from ``comfy_extras.nodes_images``, looked
#: up BY NAME at call time (``callable(getattr(...))``) so an older ComfyUI
#: that predates one of them is reported, not crashed into.
CORE_IMAGES_MODULE = "comfy_extras.nodes_images"
CORE_HELPERS = {
    "png": ("_encode_image", "inject_png_metadata"),
    "exr": ("_encode_image", "inject_exr_metadata"),
    "avif": ("_save_avif",),
}


class FormatError(ValueError):
    """A format / bit depth / color space choice this node can't deliver --
    an invalid combination, or an encoder this ComfyUI doesn't have. The text
    is plain language and is shown to the user verbatim, both as the
    queue-time validation message and as the execution error."""


class FormatPlan(NamedTuple):
    """One resolved format choice: what the user picked (``file_format``,
    ``bit_depth`` with "auto" resolved where this node can resolve it,
    ``color_space``) and the names core's helpers are called with."""

    file_format: str
    bit_depth: str
    color_space: str
    core_bit_depth: str
    core_color_space: str

    @property
    def is_default_png(self) -> bool:
        """The combination ``save()`` has always written -- the PIL path,
        byte for byte. Everything else goes to core's encoders."""
        return (
            self.file_format == "png"
            and self.bit_depth == "8-bit"
            and self.color_space == "sRGB"
        )

    @property
    def encoder_name(self) -> str:
        """How an error names what it can't write: ``"png 16-bit"`` (plain
        ``png`` would read as the default one), else just the format."""
        return f"png {self.bit_depth}" if self.file_format == "png" else self.file_format

    @property
    def label(self) -> str:
        return f"{self.file_format} ({self.bit_depth}, {self.color_space})"


#: The plan preview mode always uses (and an old workflow's defaults resolve
#: to): core ``PreviewImage`` writes a plain 8-bit PNG.
DEFAULT_PLAN = FormatPlan("png", "8-bit", DEFAULT_COLOR_SPACE, "8-bit", DEFAULT_COLOR_SPACE)


def _choices(values: tuple[str, ...]) -> str:
    return ", ".join(values)


def _or_default(raw: Any, default: str) -> Any:
    """*raw* unwrapped from a 1-element list; ``default`` when it is absent."""
    raw = _unwrap(raw)
    return default if raw is None else raw


def plan_for(file_format: Any, bit_depth: Any, color_space: Any) -> FormatPlan:
    """Resolve the three combo values into a :class:`FormatPlan`, or raise
    :class:`FormatError` naming what is wrong AND the valid choices for that
    format. List-wrapped values (``_unwrap``) and an absent value (``None``
    -> the default, which is valid for every format) are tolerated; anything
    else that isn't a listed choice is refused -- including values a
    hand-built API prompt invented, because naming the combos in
    ``VALIDATE_INPUTS`` switches core's own membership check off."""
    file_format = _or_default(file_format, DEFAULT_FORMAT)
    bit_depth = _or_default(bit_depth, DEFAULT_BIT_DEPTH)
    color_space = _or_default(color_space, DEFAULT_COLOR_SPACE)
    spec = FORMAT_MATRIX.get(file_format) if isinstance(file_format, str) else None
    if spec is None:
        raise FormatError(
            f"EPS Save Image: format {file_format!r} isn't one of {_choices(FORMATS)}."
        )
    problems = []
    if bit_depth not in spec["bit_depth"]:
        problems.append(
            f"bit_depth {bit_depth!r} isn't available for {file_format} -- "
            f"valid for {file_format}: {_choices(spec['bit_depth'])}."
        )
    if color_space not in spec["color_space"]:
        problems.append(
            f"input_color_space {color_space!r} isn't available for {file_format} -- "
            f"valid for {file_format}: {_choices(spec['color_space'])}."
        )
    if problems:
        raise FormatError(
            "EPS Save Image: " + " ".join(problems) + " Pick one of the listed choices, "
            "or change format."
        )
    resolved_depth = spec["auto_depth"] if bit_depth == "auto" and spec["auto_depth"] else bit_depth
    core_depth = _CORE_AVIF_DEPTH[bit_depth] if file_format == "avif" else resolved_depth
    return FormatPlan(
        file_format, resolved_depth, color_space, core_depth, _CORE_COLOR_SPACE[color_space]
    )


def parse_avif_crf(raw: Any) -> int:
    """``avif_crf`` as an int in 1-63, or :class:`FormatError`. Core already
    range-checks a typed widget at queue time; this covers a hand-built
    prompt and a wired (linked) value, which core cannot check until the
    value exists. ``None`` is the default; a bool is not a number."""
    raw = _unwrap(raw)
    if raw is None:
        return DEFAULT_AVIF_CRF
    try:
        if isinstance(raw, bool):
            raise ValueError("a bool is not a quality number")
        value = int(raw)
        if value != float(raw):  # 18.5 must not quietly become 18
            raise ValueError("not a whole number")
    except (TypeError, ValueError) as exc:
        raise FormatError(
            f"EPS Save Image: avif_crf {raw!r} isn't a whole number from "
            f"{AVIF_CRF_MIN} to {AVIF_CRF_MAX}."
        ) from exc
    if not AVIF_CRF_MIN <= value <= AVIF_CRF_MAX:
        raise FormatError(
            f"EPS Save Image: avif_crf {value} is outside {AVIF_CRF_MIN}-{AVIF_CRF_MAX} "
            "(lower = higher quality and a bigger file)."
        )
    return value


def _core_images() -> Any | None:
    """Core's ``comfy_extras.nodes_images`` module, or ``None`` when it can't
    be imported (no ComfyUI around -- the tests -- or a ComfyUI old enough
    not to have the module). Lazy and per call: nothing heavy at import time,
    and an already-imported module is just a ``sys.modules`` hit. Any
    exception counts as "can't use it": a half-broken core module is as
    unusable as a missing one, and the caller turns ``None`` into the
    plain-language error."""
    try:
        return importlib.import_module(CORE_IMAGES_MODULE)
    except Exception:
        logger.debug("EPS Save Image: %s is not importable", CORE_IMAGES_MODULE, exc_info=True)
        return None


def _avif_codec_gap() -> str | None:
    """A plain-language reason this machine's PyAV can't write AVIF, or
    ``None`` (it can, or this check can't tell). Core's ``_save_avif`` needs
    PyAV's ``avif`` muxer and the ``libsvtav1`` encoder; a PyAV build without
    them would otherwise fail on the first save of an overnight batch. Only a
    DEFINITE absence rejects: if ``av`` itself can't be imported the core
    helper check has already said so, and an API this probe doesn't
    recognise is treated as "unknown", never as "missing"."""
    try:
        import av
    except Exception:
        return None
    try:
        from av.codec import Codec

        Codec("libsvtav1", "w")
    except ValueError:
        # PyAV's UnknownCodecError (a ValueError): the encoder is not built in.
        return (
            "EPS Save Image: can't save avif -- this ComfyUI's PyAV has no AV1 "
            "(SVT-AV1) encoder; update ComfyUI's PyAV or pick png 8-bit."
        )
    except Exception:
        return None  # an API this probe doesn't know: unknown, never "missing"
    formats = getattr(av, "formats_available", None)
    if isinstance(formats, (set, frozenset)) and "avif" not in formats:
        return (
            "EPS Save Image: can't save avif -- this ComfyUI's PyAV has no AVIF "
            "writer; update ComfyUI's PyAV or pick png 8-bit."
        )
    return None


def require_encoder(plan: FormatPlan) -> Any | None:
    """Make sure this ComfyUI can deliver *plan*; return core's module for a
    delegated plan (``None`` for the default PNG, which needs no core
    module). Raises :class:`FormatError` -- naming the format and saying
    this ComfyUI "doesn't have this encoder yet" -- when a helper the format
    needs is missing, so nothing ever falls back to another format on its
    own."""
    if plan.is_default_png:
        return None
    core = _core_images()
    needed = CORE_HELPERS[plan.file_format]
    missing = (
        list(needed)
        if core is None
        else [name for name in needed if not callable(getattr(core, name, None))]
    )
    if missing:
        hint = " (AVIF needs ComfyUI 0.35 or newer)" if plan.file_format == "avif" else ""
        raise FormatError(
            f"EPS Save Image: can't save {plan.encoder_name} -- your ComfyUI doesn't "
            f"have this encoder yet; update ComfyUI or pick png 8-bit{hint}. "
            f"(missing: {', '.join(missing)})"
        )
    if plan.file_format == "avif":
        gap = _avif_codec_gap()
        if gap is not None:
            raise FormatError(gap)
    return core


def eps_run_record(info: dict[str, Any], baked: bool, pinned_ids: list[str]) -> dict[str, Any]:
    """The ``eps_run`` provenance record: the run_info verbatim plus what the
    bake did. ``"format": 1`` is THIS RECORD's schema version (not the image
    format). One builder for every file format, so a PNG's text chunk, an
    EXR's header attribute and an AVIF's Exif tag always say the same."""
    return {**info, "baked": baked, "pinned": pinned_ids, "format": 1}


def _delegated_metadata(
    prompt_data: Any, extra: dict[str, Any], record: dict[str, Any] | None
) -> tuple[Any, dict[str, Any]]:
    """``(prompt, extra_pnginfo)`` for core's inject helpers: the SAME baked
    objects the PIL path writes -- the prompt, every ``extra`` key (the
    workflow among them) and, with ``run_info`` wired, the ``eps_run``
    record. A fresh dict: ``extra`` is the caller's copy and the record must
    not leak back into the shared hidden objects. Core JSON-encodes each
    value itself, exactly as the PIL path does with ``json.dumps``."""
    merged = {str(key): value for key, value in extra.items()}
    if record is not None:
        merged[EPS_RUN_CHUNK] = record
    return prompt_data, merged


def _write_delegated(
    core: Any,
    plan: FormatPlan,
    image: Any,
    path: str,
    crf: int,
    metadata: tuple[Any, dict[str, Any]] | None,
) -> None:
    """Write one image through core's own helpers. Core's exceptions come
    back as a :class:`RuntimeError` that names the format and keeps core's
    own (plain) message -- e.g. its AVIF "no alpha" refusal -- and an AVIF
    that died half way is removed rather than left as a corrupt file."""
    try:
        if plan.file_format == "avif":
            # `images.unsqueeze(1)[i]` in core: a batch of ONE frame, still image.
            avif_metadata = None
            if metadata is not None:
                avif_metadata = dict(metadata[1])
                if metadata[0] is not None:
                    avif_metadata = {"prompt": metadata[0], **avif_metadata}
            core._save_avif(
                image.unsqueeze(0),
                path,
                plan.core_bit_depth,
                plan.core_color_space,
                crf,
                metadata=avif_metadata,
            )
            return
        encoded = core._encode_image(
            image, plan.file_format, plan.core_bit_depth, plan.core_color_space
        )
        if metadata is not None:
            if plan.file_format == "png":
                encoded = core.inject_png_metadata(encoded, metadata[0], metadata[1])
            else:
                encoded = core.inject_exr_metadata(
                    encoded, metadata[0], metadata[1], plan.core_color_space
                )
        with open(path, "wb") as handle:
            handle.write(encoded)
    except Exception as exc:
        if plan.file_format == "avif":
            with contextlib.suppress(OSError):
                os.unlink(path)
        raise RuntimeError(f"EPS Save Image: couldn't save {plan.label}: {exc}") from exc


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
    # v0.86.0: forward the audition `drafts` buffer (§6.1). A pin's whole
    # promise is "exactly what this run used" -- without this it would bake
    # the STALE on-disk text whenever the user queued an unsaved edit,
    # which is precisely the case the drafts feature exists to support.
    # Tolerant: a wired/absent/non-string value degrades to "no drafts",
    # matching resolve_selection's own posture.
    drafts = inputs.get(NOTEBOOK_DRAFTS_WIDGET, "{}")
    if not isinstance(drafts, str):
        drafts = "{}"
    try:
        texts, names = nodes_notebook.resolve_selection(context, file, entry, drafts)
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


#: Sentinel for "this key/index did not exist before the bake" in an undo
#: log (v0.80.0 mutate-and-restore path below).
_UNSET = object()


def _bake_widget(
    workflow: Any,
    prompt: Any,
    node_id: Any,
    node_class: Any,
    class_type: str,
    widget: str,
    value: Any,
    undo: list | None = None,
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
        if undo is not None:
            undo.append((node, "widgets_values", node.get("widgets_values", _UNSET)))
        node["widgets_values"] = values
        landed = True
    if isinstance(prompt, dict):
        entry = prompt.get(str(node_id))
        if isinstance(entry, dict) and entry.get("class_type") == class_type:
            inputs = entry.setdefault("inputs", {})
            if isinstance(inputs, dict):
                if undo is not None:
                    undo.append((inputs, widget, inputs.get(widget, _UNSET)))
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


def bake_provenance_inplace(
    workflow: Any,
    prompt: Any,
    run_info: dict[str, Any],
    pins: dict[str, PinnedWidget] | None,
    undo: list,
) -> tuple[Any, Any, bool, list[str]]:
    """:func:`bake_provenance` WITHOUT the two deep copies (v0.80.0
    sweep-performance round): mutates *workflow*/*prompt* IN PLACE,
    recording every write into *undo* so :func:`undo_bakes` can put the
    originals back after the caller has serialized. Measured: the copies
    were 72-95%% of the per-save bake (~40 ms of 55 ms at a 2 MB workflow;
    ~12 s across a 300-save sweep) while the mutation itself is two widget
    writes. ``save()`` wraps the serialize loop in ``try/finally
    undo_bakes(undo)`` so the shared hidden ``extra_pnginfo`` objects are
    byte-identical afterwards even on an exception mid-save -- the public
    copying :func:`bake_provenance` keeps its never-mutates contract for
    every other caller."""
    token = run_info.get("token")
    node_id = run_info.get("node")
    baked = False
    if isinstance(token, str) and node_id is not None:
        baked = _bake_widget(
            workflow, prompt, node_id, EPSCrossSweep, MULTIPLIER_CLASS,
            SOLO_WIDGET, token, undo,
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
            workflow, prompt, pin_node_id, node_class, pin.class_type,
            pin.widget, pin.value, undo,
        ):
            pinned_ids.append(str(pin_node_id))
    return workflow, prompt, baked, pinned_ids


def undo_bakes(undo: list) -> None:
    """Reverse every write :func:`bake_provenance_inplace` recorded, newest
    first (a container touched twice restores to its ORIGINAL value)."""
    for container, key, old in reversed(undo):
        try:
            if old is _UNSET:
                container.pop(key, None)
            else:
                container[key] = old
        except Exception:
            logger.exception("EPS Save Image: bake undo failed for %r", key)
    undo.clear()


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
    OUTPUT_TOOLTIPS = (
        "The images, passed through unchanged (like Save Image) -- whether "
        "they were saved or only previewed.",
    )
    DESCRIPTION = (
        "Save Image with provenance baked in. Wire images and filename_prefix "
        "exactly like the core Save Image node -- and wire EPS Run Multiplier's "
        "run_info output too. Every file then carries its own workflow with "
        "solo_run already set to the run that made it, and with the Prompt "
        "Notebook text and Apply LoRA Set rows pinned to the values used: "
        "drop the image onto the canvas and the whole workflow loads ready to "
        "recreate just that one image, exactly, even after the library was "
        "edited. With run_info unwired it behaves exactly like Save Image. "
        "Switch preview_only on and it becomes a Preview Image instead: "
        "nothing goes to your output folder, the images just show on the "
        "node, so you never have to swap nodes to stop saving. "
        "format picks png (the default, exactly as before), exr (32-bit "
        "float, for compositing) or avif (small, high quality, 8 or 10-bit); "
        "bit_depth, input_color_space and avif_crf (under Show advanced "
        "inputs) fine-tune it. Every format keeps the workflow inside the "
        "file. exr and avif need a ComfyUI that has those encoders; if yours "
        "doesn't, the node says so before it runs. preview_only always "
        "shows a plain png, whatever format says."
    )

    #: §6.16 state registry (v0.83.0): the widgets a Universal State
    #: Controller may capture/apply, declared next to the parser that owns
    #: their shape. ``images`` is a socket and ``run_info`` is wire-only
    #: (forceInput); neither appears here. ``preview_only`` (v1.1.0) is the
    #: registry's ``boolean`` kind (v0.99.0, first used by EPS Bypass), so a
    #: saved state can flip every EPS Save Image between saving and
    #: previewing at once -- e.g. a "draft" state that previews and a
    #: "final" state that saves. The four format widgets (owner request
    #: 2026-10-04) are plain choices and one bounded int -- the same kinds
    #: EPS Resolution declares for its combos and its width/height -- so a
    #: "draft" state can preview while a "final" state saves a 16-bit png or
    #: an exr. (``format`` here is a WIDGET name under ``widgets``; the
    #: descriptor's own top-level ``"format": 1`` is its schema version.)
    EPS_STATE_WIDGETS: ClassVar[dict[str, Any]] = {
        "format": 1,
        "widgets": {
            "filename_prefix": {"kind": "string", "max_len": 10000},
            PREVIEW_WIDGET: {"kind": "boolean"},
            FORMAT_WIDGET: {"kind": "choice"},
            BIT_DEPTH_WIDGET: {"kind": "choice"},
            COLOR_SPACE_WIDGET: {"kind": "choice"},
            AVIF_CRF_WIDGET: {"kind": "int", "min": AVIF_CRF_MIN, "max": AVIF_CRF_MAX},
        },
    }

    #: Core ``PreviewImage`` picks its five random letters ONCE per node
    #: instance (``__init__``), and ComfyUI keeps one instance per node id,
    #: so every run of a given node lands in the same ``_temp_xxxxx`` files
    #: and ``get_save_image_path``'s counter keeps counting up. Created
    #: lazily (and only for a node that ever previews) so tests and callers
    #: that skip ``__init__`` still work.
    _preview_suffix: str | None = None

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
                # v1.1.0 TAIL widget (FORMAT.md §8: widgets_values restores
                # POSITIONALLY, so a widget is only ever appended -- here
                # after filename_prefix, the node's only other widget;
                # run_info is a forceInput socket and holds no slot).
                # `optional`, like every tail widget this pack has added
                # (solo_run, pair_mode, ...): a hand-built API /prompt that
                # predates the toggle still validates (a missing REQUIRED
                # input is a hard error) and save() defaults it to False.
                # NO `label_on`/`label_off` (rig 2026-10-03, Nodes 2.0 on
                # frontend 1.52.7): with labels, Nodes 2.0 draws a two-
                # segment control whose halves are 69 px at the default
                # node width (px-5 padding) -- "preview only" needs 88 and
                # even "preview" needs 75, so it rendered truncated. Without
                # labels it is a plain on/off switch next to the name
                # `preview_only`, which reads correctly in both renderers.
                PREVIEW_WIDGET: (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": (
                            "Off (save): write the images to your output "
                            "folder, exactly like Save Image. On (preview "
                            "only): don't save -- just show them on the node, "
                            "like Preview Image. Previews go to ComfyUI's "
                            "temporary folder, which it empties when it "
                            "restarts. The workflow is still embedded in the "
                            "image, so saving one from the preview and "
                            "dropping it on the canvas still recreates the run. "
                            "A preview is always a plain png, whatever format "
                            "says."
                        ),
                    },
                ),
                # 2026-10-04 TAIL widgets (owner request, FORMAT.md §6.14
                # "Formats"), appended AFTER preview_only for the same
                # positional-restore reason, and in `optional` for the same
                # old-API-prompt reason: save() defaults all four to today's
                # behaviour. `advanced: True` rides through V1 INPUT_TYPES
                # to the frontend, which tucks the widget behind "Show
                # advanced inputs" (as far as the 1.52.7 bundle shows: the
                # Nodes 2.0 renderer reads it from the widget options; the
                # classic canvas renderer never maps it onto
                # `widget.advanced`, so there the three just stay visible --
                # harmless, and the same as core's own Save Image
                # (Advanced); UNCONFIRMED on the rig). `format` stays
                # visible: it is the one choice a user comes here for.
                FORMAT_WIDGET: (
                    list(FORMATS),
                    {
                        "default": DEFAULT_FORMAT,
                        "tooltip": (
                            "File format. png (the default) is exactly what "
                            "this node has always saved. exr is 32-bit float "
                            "for compositing; avif is a small, high-quality 8 "
                            "or 10-bit file and needs a recent ComfyUI. Every "
                            "format carries the workflow: drop a png or avif "
                            "on the canvas and it recreates the run; an exr "
                            "keeps the workflow in its header, but ComfyUI "
                            "can't load a workflow from an exr. preview_only "
                            "always shows a plain png, whatever this says."
                        ),
                    },
                ),
                BIT_DEPTH_WIDGET: (
                    list(BIT_DEPTH_CHOICES),
                    {
                        "default": DEFAULT_BIT_DEPTH,
                        "advanced": True,
                        "tooltip": (
                            "Bits per channel. auto = png 8-bit, exr 32-bit "
                            "float, avif 8-bit (10-bit for HDR input). png "
                            "takes 8-bit or 16-bit, exr only 32-bit float, "
                            "avif 8-bit or 10-bit. A depth the chosen format "
                            "doesn't have is refused when you queue, with the "
                            "valid choices in the message."
                        ),
                    },
                ),
                COLOR_SPACE_WIDGET: (
                    list(COLOR_SPACE_CHOICES),
                    {
                        "default": DEFAULT_COLOR_SPACE,
                        "advanced": True,
                        "tooltip": (
                            "How to read the pixels coming IN -- this "
                            "describes your images, it doesn't convert them "
                            "(use Convert Image Color Space for that). sRGB: "
                            "ordinary sRGB pictures. linear: already linear "
                            "light, e.g. renderer or compositor output (exr "
                            "only). HDR (HLG): BT.2100 HLG (exr and avif). HDR "
                            "PQ: BT.2100 PQ (avif only). png takes sRGB only. "
                            "An exr is always written as linear light: sRGB "
                            "and HDR (HLG) input is converted on the way out, "
                            "linear is written as it is."
                        ),
                    },
                ),
                AVIF_CRF_WIDGET: (
                    "INT",
                    {
                        "default": DEFAULT_AVIF_CRF,
                        "min": AVIF_CRF_MIN,
                        "max": AVIF_CRF_MAX,
                        "advanced": True,
                        "tooltip": (
                            "avif only: quality. Lower means better quality "
                            "and a bigger file (1 = best, 63 = smallest); 18 "
                            "is a good everyday value. png and exr ignore it."
                        ),
                    },
                ),
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
        }

    @classmethod
    def VALIDATE_INPUTS(
        cls,
        format: Any = DEFAULT_FORMAT,
        bit_depth: Any = DEFAULT_BIT_DEPTH,
        input_color_space: Any = DEFAULT_COLOR_SPACE,
        preview_only: Any = False,
    ) -> bool | str:
        """Queue-time check (owner request 2026-10-04: an overnight batch
        must be refused immediately, not fail on its first save): reject an
        invalid format / bit depth / color-space combination, and a format
        whose encoder this ComfyUI doesn't have, with a message that lists
        the valid choices. The returned string becomes core's "Custom
        validation failed" text (``execution.py`` ``validate_inputs``).

        How core calls this (read from the rig's ``execution.py``): an input
        NAMED in this signature loses core's own combo-membership check, so
        :func:`plan_for` does that too; only inputs present in the prompt
        are passed (the defaults cover an old API prompt); and a LINKED
        input arrives as ``None`` -- its value doesn't exist yet -- which
        for ``format`` means "can't tell, let ``save`` decide" and for the
        other two means their always-valid default. This node doesn't use
        ``INPUT_IS_LIST``, so values arrive as plain scalars (``_unwrap``
        still tolerates a 1-element list). ``preview_only`` is named so a
        preview skips the check -- a preview is always the plain PNG, so
        its ``format`` must not stop the queue. ``avif_crf`` is left to
        core's own min/max check (it isn't named here); ``save`` re-checks
        a wired value.

        Core reports one error per NAMED input that is present, all with
        this same text -- repetitive but accurate, and the only per-node
        shape V1 validation offers. A courtesy layer, not the enforcement
        layer: an unexpected failure in here returns ``True`` and ``save``
        raises the real error.
        """
        try:
            if parse_preview_only(preview_only):
                return True
            file_format = _unwrap(format)
            if file_format is None:  # wired: nothing to check yet
                return True
            require_encoder(plan_for(file_format, bit_depth, input_color_space))
        except FormatError as exc:
            return str(exc)
        except Exception:
            logger.exception("EPS Save Image: format check failed; leaving it to save()")
        return True

    def save(
        self,
        images: Any,
        filename_prefix: str = "EPS",
        run_info: Any = None,
        prompt: Any = None,
        extra_pnginfo: Any = None,
        preview_only: Any = False,
        format: Any = DEFAULT_FORMAT,
        bit_depth: Any = DEFAULT_BIT_DEPTH,
        input_color_space: Any = DEFAULT_COLOR_SPACE,
        avif_crf: Any = DEFAULT_AVIF_CRF,
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
        # v1.1.0: preview_only turns the three things core's PreviewImage
        # changes relative to SaveImage (rig nodes.py) -- where the file goes
        # (temp dir), what the frontend is told (type "temp") and how hard it
        # compresses (level 1) -- plus its `_temp_xxxxx` prefix_append.
        # NOTHING else forks: the bake, the pins and the PNG chunks below run
        # identically either way, so a preview stays a faithful provenance
        # carrier (and the filename keeps `filename_prefix` as its base, so
        # subfolders and the run token survive).
        preview = parse_preview_only(preview_only)
        # 2026-10-04 formats: decide the encoder FIRST -- before anything is
        # baked, pinned or written -- so a bad choice or a missing encoder
        # fails here, plainly, having done no work (VALIDATE_INPUTS already
        # refused it at queue time for a typed widget; this is the
        # enforcement for a wired value or a hand-built prompt). PREVIEW WINS:
        # core's PreviewImage is always a plain 8-bit PNG in the temp
        # folder, so a preview ignores `format` and the rest entirely and
        # never needs a core encoder.
        if preview:
            plan = DEFAULT_PLAN
            core = None
            crf = DEFAULT_AVIF_CRF
        else:
            plan = plan_for(format, bit_depth, input_color_space)
            core = require_encoder(plan)
            crf = parse_avif_crf(avif_crf) if plan.file_format == "avif" else DEFAULT_AVIF_CRF
        if preview:
            if self._preview_suffix is None:
                self._preview_suffix = _temp_suffix()
            prefix += self._preview_suffix
            result_type = PREVIEW_TYPE
            compress_level = PREVIEW_COMPRESS_LEVEL
        else:
            result_type = OUTPUT_TYPE
            compress_level = SAVE_COMPRESS_LEVEL
        # v0.80.0: every provenance write below is recorded here and
        # reversed in the finally -- the hidden extra_pnginfo objects are
        # shared across all of this queue's mapped save() calls and must
        # come out byte-identical (bake_provenance_inplace's contract).
        bake_undo: list = []
        try:
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
                # v0.80.0: in place + undo, not deepcopy -- see
                # bake_provenance_inplace. `extra["workflow"]` needs no
                # replacement: the baked object IS the one already in `extra`.
                workflow_data, prompt_data, baked, pinned_ids = bake_provenance_inplace(
                    workflow_data, prompt_data, info, pins, bake_undo
                )
                if not baked:
                    logger.warning(
                        "EPS Save Image: multiplier %r not found in the workflow/prompt; "
                        "saving with the standard (un-soloed) chunks",
                        info.get("node"),
                    )

            # Resolved here, not above: save mode must never touch
            # get_temp_directory() (and preview never get_output_directory()).
            if preview:
                output_dir = folder_paths.get_temp_directory()
            else:
                output_dir = folder_paths.get_output_directory()
            first = images[0]
            full_output_folder, filename, counter, subfolder, _prefix = (
                folder_paths.get_save_image_path(prefix, output_dir, first.shape[1], first.shape[0])
            )
            results: list[dict[str, Any]] = []
            record = eps_run_record(info, baked, pinned_ids) if info is not None else None
            # Everything below runs INSIDE the try, so the baked objects are
            # still baked when core's helpers (or PIL) serialize them.
            delegated_metadata = (
                None
                if core is None or disable_metadata
                else _delegated_metadata(prompt_data, extra, record)
            )
            for batch_number, image in enumerate(images):
                # Names keep EPS's `{name}_{counter:05}_.{ext}`; the counter
                # is shared across extensions (get_save_image_path reads the
                # digits before the first "." and the trailing "_").
                stem = f"{filename.replace('%batch_num%', str(batch_number))}_{counter:05}_"
                if core is not None:
                    file = f"{stem}.{plan.file_format}"
                    _write_delegated(
                        core,
                        plan,
                        image,
                        os.path.join(full_output_folder, file),
                        crf,
                        delegated_metadata,
                    )
                    results.append({"filename": file, "subfolder": subfolder, "type": result_type})
                    counter += 1
                    continue
                array = 255.0 * image.cpu().numpy()
                img = Image.fromarray(np.clip(array, 0, 255).astype(np.uint8))
                metadata = None
                if not disable_metadata:
                    metadata = PngInfo()
                    if prompt_data is not None:
                        metadata.add_text("prompt", json.dumps(prompt_data))
                    for key, value in extra.items():
                        metadata.add_text(str(key), json.dumps(value))
                    if record is not None:
                        metadata.add_text(EPS_RUN_CHUNK, json.dumps(record))
                file = f"{stem}.png"
                img.save(
                    os.path.join(full_output_folder, file),
                    pnginfo=metadata,
                    compress_level=compress_level,
                )
                results.append({"filename": file, "subfolder": subfolder, "type": result_type})
                counter += 1
            return {"ui": {"images": results}, "result": (images,)}
        finally:
            undo_bakes(bake_undo)
