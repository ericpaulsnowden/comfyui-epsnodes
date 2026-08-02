"""The ``EPSResolution`` ComfyUI node (FORMAT.md §6.5, display: "EPS Resolution").

M1 = the functional core: typed width/height (with ``0``-axis derivation from
the input image's aspect), a thin built-in resize (stretch / keep-aspect-fit /
crop-to-fill / pad), untouched image + original-size passthrough, and
``multiple_of`` rounding. M3 = server-side size PRESETS: named width/height/
resize_method/interpolation/multiple_of bundles saved to a shared JSON file
inside ``lora_library``'s own library folder (``resolution_presets_store.py``)
so they travel across machines exactly like the Prompt Notebook's file and
LoRA sets already do -- selecting one or more presets makes :meth:`resolve`
fan out into one execution per preset (see :class:`EPSResolution`'s own
docstring). The grid (M2) and multi-image list fan-out (M4) are deliberately
NOT built here -- see ``research/roadmap-eps-resolution.md``.

``torch``/``comfy.utils`` are imported only inside the functions that touch
real tensors, never at module scope, so this module stays importable in a
plain test environment without either installed — same convention as
``lora_library/nodes_sets.py`` (see its module docstring) and
comfyui-photoshop-bridge's ``cpsb/nodes.py``. ``resolution_presets_store``
(this module's only sibling import) is equally bare-importable — it needs no
ComfyUI at all, since the shared ``LibraryContext`` is injected via
:func:`set_context`, mirroring ``lora_library/nodes_notebook.py``'s own
context-injection seam exactly (read its module docstring): a module-level
``_context`` global, wired once from the pack's ``__init__.py`` for real
runs, reset to ``None`` between test cases.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from . import resolution_presets_store as presets_store

logger = logging.getLogger("eps_image")

CATEGORY_NAME = "EPSNodes"

#: FORMAT.md §6.5 combo options — user-facing, stable identifiers (widget
#: values persist in saved workflows, so don't rename these once shipped).
RESIZE_METHODS = ["stretch", "keep aspect (fit)", "crop to fill", "pad"]
INTERPOLATIONS = ["nearest", "bilinear", "bicubic", "area", "lanczos"]

#: `width`/`height`/`multiple_of` widget bounds — named constants (rather
#: than literals inline in INPUT_TYPES) so the size-presets HTTP route
#: (`routes_resolution_presets.py`) can validate a saved preset's values
#: against the SAME bounds the widgets themselves enforce, without the two
#: ever drifting apart.
WIDTH_MIN = 0
WIDTH_MAX = 16384
HEIGHT_MIN = 0
HEIGHT_MAX = 16384
MULTIPLE_OF_MIN = 0
MULTIPLE_OF_MAX = 1024

#: Default `presets` widget value: no size preset selected -- the node runs
#: exactly as it did before this feature existed, from its own typed
#: width/height/etc. fields (module docstring; mirrors
#: nodes_checkpoint_switcher.DEFAULT_SELECTION's identical convention).
DEFAULT_PRESETS = "[]"

#: Maps our public interpolation names to the identifiers core's
#: ``comfy.utils.common_upscale`` (and, beneath it, ``torch.nn.functional.
#: interpolate``) actually expects — mirrors core ``ImageScale.upscale_methods``
#: (``nodes.py``) except "nearest" -> "nearest-exact" (torch's plain "nearest"
#: is a different, blurrier filter than what ComfyUI's own nodes expose as
#: "nearest"). The rest pass through unchanged.
_UPSCALE_METHOD_MAP = {
    "nearest": "nearest-exact",
    "bilinear": "bilinear",
    "bicubic": "bicubic",
    "area": "area",
    "lanczos": "lanczos",
}

_context: presets_store.LibraryContext | None = None


def set_context(context: presets_store.LibraryContext | None) -> None:
    """Wire the shared :class:`~lora_library.context.LibraryContext` into
    this module -- mirrors ``lora_library.nodes_notebook.set_context``'s
    exact pattern (read its docstring): called once from the pack's
    ``__init__.py`` (real runs); tests call it directly against a fake
    context. Accepts ``None`` so tests can reset the module-level global
    between cases without leaking state.
    """
    global _context
    _context = context


def _parse_preset_names(raw: Any) -> list[str]:
    """Best-effort JSON-array parse of the `presets` widget value -- mirrors
    ``nodes_checkpoint_switcher._parse_selection``'s exact degrade-don't-
    crash contract (see its docstring): empty/``None``/falsy -> ``[]``;
    unparseable JSON, or valid JSON that isn't a list -> ``[]`` (logged
    WARNING); a list whose entries mix strings with other JSON types keeps
    every string entry and drops only the non-string ones individually
    (each logged WARNING) so one bad entry never blanks an otherwise-good
    selection. Order is preserved -- selection order = emission order, per
    this pack's existing multi-select convention (`LoraLibraryNotebook`,
    `EPSCheckpointSwitcher`).
    """
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError) as exc:
        logger.warning(
            "EPS Resolution: malformed `presets` value (%s); treating as no presets selected",
            exc,
        )
        return []
    if not isinstance(parsed, list):
        logger.warning(
            "EPS Resolution: `presets` was not a JSON array (got %r); "
            "treating as no presets selected",
            type(parsed).__name__,
        )
        return []
    names: list[str] = []
    for entry in parsed:
        if isinstance(entry, str):
            names.append(entry)
        else:
            logger.warning("EPS Resolution: `presets` entry %r is not a string; skipping it", entry)
    return names


def _presets_file_token(context: presets_store.LibraryContext | None) -> str:
    """*context*'s ``resolution_presets.json`` mtime+size as a cache-busting
    token, or a stable missing-marker -- mirrors
    ``lora_library.nodes_notebook._file_token``'s exact reasoning: an
    on-disk edit to a preset's VALUES (from this machine's panel, or from
    another machine sharing the same library folder) must force
    :meth:`EPSResolution.resolve` to re-execute even though the `presets`
    widget's selected NAMES haven't changed -- otherwise a stale cached
    width/height could silently survive a preset edit, the identical
    staleness bug `IS_CHANGED` already closes for the Prompt Notebook.
    """
    if context is None:
        return "no-context"
    path = presets_store.presets_path(context)
    try:
        stat = path.stat()
    except OSError:
        return "missing"
    return f"{stat.st_mtime}:{stat.st_size}"


def _round_to_multiple(value: int, multiple_of: int) -> int:
    """Round *value* to the nearest multiple of *multiple_of* (FORMAT.md §6.5).

    ``multiple_of <= 0`` is "off" (default) and returns *value* unchanged.
    A non-positive *value* (the 0-axis-with-no-image edge case — nothing to
    derive from, so it stays a literal 0) is left alone too: there's nothing
    to round. Otherwise the result is floored at one multiple so rounding
    can never collapse a positive value to 0.
    """
    if multiple_of <= 0 or value <= 0:
        return value
    rounded = int(round(value / multiple_of) * multiple_of)
    return max(multiple_of, rounded)


def _floor_to_multiple(value: int, multiple_of: int) -> int:
    """Round *value* DOWN to a multiple of *multiple_of* (containment-safe).

    Used only by "keep aspect (fit)": rounding a fitted axis to the NEAREST
    multiple can push it back ABOVE the box (e.g. a 2:1 image fit into a
    1080x1080 box -> 1080x540 -> nearest-64 1088x512, and 1088 > 1080 breaks
    "fit within"). Flooring can never exceed the fitted size, so containment
    (result <= box) always holds. ``multiple_of <= 0`` is "off". If flooring
    would drop below one whole multiple (a box smaller than multiple_of on an
    axis), the raw *value* is kept -- better a non-multiple that still fits
    than a 0 (invalid) or a forced multiple that overflows the box.
    """
    if multiple_of <= 0 or value <= 0:
        return value
    floored = (value // multiple_of) * multiple_of
    return floored if floored >= multiple_of else value


def _fit_dimensions(orig_w: int, orig_h: int, box_w: int, box_h: int) -> tuple[int, int]:
    """The largest size that fits within *box_w* x *box_h*, preserving aspect.

    "keep aspect (fit)" per FORMAT.md §6.5 — containment, not cover: the
    result is <= the box on both axes (no letterboxing to the box itself,
    that's a different, undemanded mode).
    """
    scale = min(box_w / orig_w, box_h / orig_h)
    return max(1, round(orig_w * scale)), max(1, round(orig_h * scale))


def _resize_tensor(image: Any, width: int, height: int, interpolation: str, crop: str) -> Any:
    """Mirrors core ``ImageScale.upscale`` exactly: movedim -> common_upscale
    -> movedim back (``nodes.py`` class ``ImageScale``, verified on the rig).

    *crop* is core's own vocabulary (``"disabled"`` or ``"center"``,
    ``comfy/utils.py`` ``common_upscale``) — ``"center"`` gives us
    "crop to fill" for free: it crops the SOURCE toward the target aspect
    before scaling, which is mathematically the same result as scale-to-cover
    then center-crop.
    """
    import comfy.utils  # lazy: keeps this module importable without ComfyUI/torch

    method = _UPSCALE_METHOD_MAP.get(interpolation, interpolation)
    samples = image.movedim(-1, 1)
    scaled = comfy.utils.common_upscale(samples, width, height, method, crop)
    return scaled.movedim(1, -1)


def _pad_to(image: Any, target_w: int, target_h: int, pad_value: float = 0.0) -> Any:
    """Center *image* ``[B,H,W,C]`` onto a *pad_value*-filled canvas.

    Used by "pad": the image is fit-resized first (see ``resolve()``), then
    this centers it on a ``target_w`` x ``target_h`` canvas — black by
    default (FORMAT.md §6.5: "pad color black default").
    """
    import torch  # lazy: see module docstring

    batch, height, width, channels = image.shape
    canvas = torch.full(
        (batch, target_h, target_w, channels), pad_value, dtype=image.dtype, device=image.device
    )
    top = max(0, (target_h - height) // 2)
    left = max(0, (target_w - width) // 2)
    canvas[:, top : top + height, left : left + width, :] = image[
        :, : min(height, target_h), : min(width, target_w), :
    ]
    return canvas


def _resolve_one(
    width: int,
    height: int,
    resize_method: str,
    interpolation: str,
    multiple_of: int,
    image: Any,
) -> tuple[Any, Any, int, int, int, int]:
    """The exact pre-M3 computation: resize *image* (or act as a pure size
    calculator when *image* is ``None``) per FORMAT.md §6.5. Factored out of
    :meth:`EPSResolution.resolve` so BOTH the empty-presets path (the node's
    own widget fields, run once) and the K-selected-presets path (each
    preset's five values, run once per preset) share ONE implementation --
    see :meth:`EPSResolution.resolve`'s own docstring for how the two are
    composed into its ``OUTPUT_IS_LIST`` lists. Body unchanged from the
    pre-M3 ``resolve()`` method it was extracted from.
    """
    original_width = original_height = 0
    target_w, target_h = width, height

    if image is not None:
        # IMAGE tensors are [B, H, W, C] (ComfyUI convention).
        original_height, original_width = int(image.shape[1]), int(image.shape[2])

        # 0 on an axis = derive it from the other axis + the image's
        # aspect (mirrors core ImageScale's own derivation, nodes.py).
        if target_w == 0 and target_h == 0:
            target_w, target_h = original_width, original_height
        elif target_w == 0:
            target_w = max(1, round(original_width * target_h / original_height))
        elif target_h == 0:
            target_h = max(1, round(original_height * target_w / original_width))
    # else: no image to derive an aspect from — an explicit 0 stays 0.

    resized_image = None
    final_w, final_h = target_w, target_h

    if image is not None and target_w > 0 and target_h > 0:
        if resize_method == "keep aspect (fit)":
            # Fit into the box FIRST, then FLOOR to the multiple so the
            # constraint can never push a fitted axis back above the box
            # (see _floor_to_multiple for the concrete failure it avoids).
            # multiple_of inevitably perturbs the exact aspect slightly;
            # that is the documented cost of opting into a size constraint,
            # and flooring keeps the perturbation on the safe (smaller,
            # still-contained) side.
            fit_w, fit_h = _fit_dimensions(original_width, original_height, target_w, target_h)
            final_w = _floor_to_multiple(fit_w, multiple_of)
            final_h = _floor_to_multiple(fit_h, multiple_of)
        else:
            # stretch / crop to fill / pad target the box itself, so the
            # output equals the (nearest-rounded) box exactly -- crop
            # absorbs any aspect change via center-crop, pad via letterbox,
            # stretch has no aspect to keep, so nearest-round is right here.
            final_w = _round_to_multiple(target_w, multiple_of)
            final_h = _round_to_multiple(target_h, multiple_of)

        if resize_method == "crop to fill":
            resized_image = _resize_tensor(image, final_w, final_h, interpolation, "center")
        elif resize_method == "pad":
            fit_w, fit_h = _fit_dimensions(original_width, original_height, final_w, final_h)
            fitted = _resize_tensor(image, fit_w, fit_h, interpolation, "disabled")
            resized_image = _pad_to(fitted, final_w, final_h)
        else:
            # "stretch" and "keep aspect (fit)" both land here: fit has
            # already replaced final_w/final_h with the floored fitted size
            # above, so a plain no-crop resize to that size is exactly right.
            resized_image = _resize_tensor(image, final_w, final_h, interpolation, "disabled")
    else:
        # Pure size-source path (no image, or nothing left to derive):
        # multiple_of still applies so this node is useful standalone.
        final_w = _round_to_multiple(final_w, multiple_of)
        final_h = _round_to_multiple(final_h, multiple_of)

    return (image, resized_image, final_w, final_h, original_width, original_height)


class EPSResolution:
    """Elegant, IMAGE-first (not latent) all-in-one resolution node.

    Re-derives everything from its inputs on every execution — there is no
    persisted state to go stale. With no ``image`` wired, the node is a pure
    target-size source: ``resized_image`` is ``None`` and
    ``original_width``/``original_height`` are ``0``, but ``width``/``height``
    still report the (0-axis-derivation-aside, since that needs an image to
    derive an aspect from) requested target, ``multiple_of``-rounded — so it
    is usable standalone to drive downstream size-consuming nodes.

    **Size presets (M3) and multi-select fan-out.** All six outputs are
    ``OUTPUT_IS_LIST`` (mirrors ``lora_library.nodes_notebook.
    LoraLibraryNotebook``'s own multi-select pattern -- see its docstring
    for the execution.py mechanics this relies on: core's list-execution
    fan-out re-runs everything downstream once per element, all from ONE
    call to :meth:`resolve`). The hidden ``presets`` widget carries a JSON
    array of saved preset NAMES, in selection order, resolved against
    ``resolution_presets_store``'s shared file at EXECUTE time (server-
    authoritative, like the Notebook resolves entry names -- the store is
    the shared truth across the owner's machines, not whatever the widget
    fields currently show):

    - Empty/absent/malformed ``presets`` (every workflow saved before this
      feature existed, and every fresh node by default) -- unchanged M1
      behavior: :meth:`resolve` computes once from its own typed
      width/height/resize_method/interpolation/multiple_of fields, and each
      of the six results is wrapped in a length-1 list. Indistinguishable
      from a plain scalar output to anything downstream (core's own
      map-over-list produces exactly one run) -- the Notebook's
      long-standing precedent for this exact equivalence.
    - One or more selected preset names -- the node's OWN width/height/etc.
      fields are ignored entirely; element *i* of every output is preset
      ``names[i]``'s complete resize computation (the SAME image, when
      wired, resized once per preset). A selected name absent from the
      store raises loudly, naming the preset and the file -- a rename/
      delete on another machine must fail the queue, not silently
      substitute something else.
    """

    CATEGORY = CATEGORY_NAME
    RETURN_TYPES = ("IMAGE", "IMAGE", "INT", "INT", "INT", "INT")
    RETURN_NAMES = (
        "image",
        "resized_image",
        "width",
        "height",
        "original_width",
        "original_height",
    )
    OUTPUT_IS_LIST = (True, True, True, True, True, True)
    OUTPUT_TOOLTIPS = (
        "The input image, unchanged. None if nothing is wired. With more "
        "than one size preset selected, this is a list, one element per "
        "preset, in selection order.",
        "The resized image. None if nothing is wired, since there's "
        "nothing to resize. With more than one size preset selected, this "
        "is a list, one element per preset, in selection order.",
        "The target width actually used, after deriving from aspect ratio "
        "(if 0) and rounding to multiple_of. With more than one size "
        "preset selected, this is a list, one element per preset.",
        "The target height actually used, after deriving from aspect ratio "
        "(if 0) and rounding to multiple_of. With more than one size "
        "preset selected, this is a list, one element per preset.",
        "The input image's width before resizing. 0 if nothing is wired. "
        "With more than one size preset selected, this is a list, one "
        "element per preset (the same value repeated -- it's the same "
        "input image every time).",
        "The input image's height before resizing. 0 if nothing is wired. "
        "With more than one size preset selected, this is a list, one "
        "element per preset (the same value repeated -- it's the same "
        "input image every time).",
    )
    FUNCTION = "resolve"
    DESCRIPTION = (
        "Resizes an image and reports both its original and new size in "
        "one node, so there's no separate resize, reroute, and get-size "
        "node to wire up. Set a target width and height; leaving one at 0 "
        "derives it from the other using the input image's aspect ratio (0 "
        "and 0 keeps the original size). Choose how the resize happens: "
        "stretch to fit exactly, keep aspect ratio and fit inside the box, "
        "crop to fill the box, or pad with black to fill it. With no image "
        "wired, the node still reports the requested size, so it can drive "
        "downstream nodes on its own. Save named size presets and select "
        "several to run the rest of the workflow once per preset, in one "
        "queue."
    )

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "width": (
                    "INT",
                    {
                        "default": 1024,
                        "min": WIDTH_MIN,
                        "max": WIDTH_MAX,
                        "step": 1,
                        "tooltip": (
                            "Target width in pixels. 0 derives it from "
                            "height and the input image's aspect ratio "
                            "(needs an image wired); with no image, 0 "
                            "stays 0."
                        ),
                    },
                ),
                "height": (
                    "INT",
                    {
                        "default": 1024,
                        "min": HEIGHT_MIN,
                        "max": HEIGHT_MAX,
                        "step": 1,
                        "tooltip": (
                            "Target height in pixels. 0 derives it from "
                            "width and the input image's aspect ratio "
                            "(needs an image wired); with no image, 0 "
                            "stays 0."
                        ),
                    },
                ),
                "resize_method": (
                    RESIZE_METHODS,
                    {
                        "default": "stretch",
                        "tooltip": (
                            "How to reach the target size. Stretch fills it "
                            "exactly without preserving aspect ratio; keep "
                            "aspect (fit) scales to fit inside it; crop to "
                            "fill scales to cover it and crops the "
                            "overflow; pad scales to fit inside it and "
                            "fills the rest with black."
                        ),
                    },
                ),
                "interpolation": (
                    INTERPOLATIONS,
                    {
                        "default": "bilinear",
                        "tooltip": (
                            "The resampling filter used when scaling. "
                            "Bilinear is a good default; lanczos and "
                            "bicubic are sharper for upscaling, area is "
                            "often better for downscaling, nearest keeps "
                            "hard pixel edges."
                        ),
                    },
                ),
                "multiple_of": (
                    "INT",
                    {
                        "default": 0,
                        "min": MULTIPLE_OF_MIN,
                        "max": MULTIPLE_OF_MAX,
                        "step": 1,
                        "tooltip": (
                            "Rounds the final width and height to the "
                            "nearest multiple of this number -- useful for "
                            "models that need dimensions divisible by 8, "
                            "16, or 64. 0 turns rounding off."
                        ),
                    },
                ),
            },
            "optional": {
                "image": (
                    "IMAGE",
                    {
                        "tooltip": (
                            "The image to resize. Leave unwired to use "
                            "this node purely as a size calculator."
                        ),
                    },
                ),
                # "hidden": True is the VUE-nodes ("New node design") hide
                # flag (FORMAT.md §7.5): that renderer decides widget
                # visibility from the input spec's OPTIONS
                # (`options.hidden`, useProcessedWidgets.ts) and IGNORES the
                # litegraph `widget.hidden` the frontend sets at attach time
                # -- without this, this internal plumbing widget would leak
                # into Vue nodes as a raw editable text field. The classic
                # canvas renderer ignores this key right back, so it
                # changes nothing there.
                "presets": (
                    "STRING",
                    {
                        "default": DEFAULT_PRESETS,
                        "multiline": False,
                        "hidden": True,
                        "tooltip": (
                            "Selected size preset name(s), one JSON array "
                            "entry per name, in selection order -- "
                            "maintained by this node's own panel; you "
                            "don't need to type into it directly. Leave "
                            "empty (the default) to use the width/height/"
                            "resize_method/interpolation/multiple_of "
                            "fields above; with one or more presets "
                            "selected, those fields are ignored and every "
                            "value comes from the saved preset(s) instead "
                            "-- selecting more than one runs the rest of "
                            "the workflow once per preset, in selection "
                            "order."
                        ),
                    },
                ),
            },
        }

    @classmethod
    def IS_CHANGED(cls, presets: str = DEFAULT_PRESETS, **_kwargs: Any) -> str:
        """Cache-busting token for the M3 preset-selection path only.

        With no preset selected (the overwhelmingly common case, and every
        workflow saved before this feature existed), this returns a
        constant -- contributing nothing beyond what ComfyUI's own default
        input-value caching already does, so a plain widget-driven node's
        caching behavior is completely unchanged. With one or more presets
        selected, an edit to ``resolution_presets.json`` -- from THIS
        machine's panel, or from another machine sharing the same library
        folder -- must force a re-execution even though the `presets`
        widget's selected NAMES haven't changed, or a stale cached
        width/height could silently survive a preset edit. Same staleness
        problem, same fix, as ``lora_library.nodes_notebook``'s own
        ``IS_CHANGED`` (see its docstring) -- only paid for by node
        instances that actually opted into presets.
        """
        names = _parse_preset_names(presets)
        if not names:
            return "no-presets-selected"
        return f"{_presets_file_token(_context)}:{presets}"

    def resolve(
        self,
        width: int,
        height: int,
        resize_method: str = "stretch",
        interpolation: str = "bilinear",
        multiple_of: int = 0,
        image: Any = None,
        presets: str = DEFAULT_PRESETS,
    ) -> tuple[list[Any], list[Any], list[int], list[int], list[int], list[int]]:
        names = _parse_preset_names(presets)

        if not names:
            # Unchanged M1 behavior, just each of the six values wrapped in
            # a length-1 list -- OUTPUT_IS_LIST's degenerate one-run case
            # (class docstring), byte-identical to the pre-M3 scalar
            # computation.
            single = _resolve_one(width, height, resize_method, interpolation, multiple_of, image)
            return tuple([value] for value in single)  # type: ignore[return-value]

        context = _context
        if context is None:
            raise RuntimeError("EPSNodes: EPS Resolution has no context configured")

        stored_presets, _mtime = presets_store.load_presets(context)
        resolved_path = presets_store.presets_path(context)

        missing = [name for name in names if name not in stored_presets]
        if missing:
            raise ValueError(
                f"EPS Resolution: no such preset(s) {missing!r} in {resolved_path} -- it may "
                "have been renamed or deleted on another machine"
            )

        images: list[Any] = []
        resized_images: list[Any] = []
        widths: list[int] = []
        heights: list[int] = []
        orig_widths: list[int] = []
        orig_heights: list[int] = []
        for name in names:
            preset = stored_presets[name]
            out_image, resized_image, out_w, out_h, orig_w, orig_h = _resolve_one(
                preset["width"],
                preset["height"],
                preset["resize_method"],
                preset["interpolation"],
                preset["multiple_of"],
                image,
            )
            images.append(out_image)
            resized_images.append(resized_image)
            widths.append(out_w)
            heights.append(out_h)
            orig_widths.append(orig_w)
            orig_heights.append(orig_h)

        return (images, resized_images, widths, heights, orig_widths, orig_heights)
