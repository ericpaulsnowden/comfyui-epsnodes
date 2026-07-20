"""The ``EPSResolution`` ComfyUI node (FORMAT.md §6.5, display: "EPS Resolution").

M1 = the functional core: typed width/height (with ``0``-axis derivation from
the input image's aspect), a thin built-in resize (stretch / keep-aspect-fit /
crop-to-fill / pad), untouched image + original-size passthrough, and
``multiple_of`` rounding. The grid (M2), NAS-shared presets (M3), and
multi-image list fan-out (M4) are deliberately NOT built here — see
``research/roadmap-eps-resolution.md``.

``torch``/``comfy.utils`` are imported only inside the functions that touch
real tensors, never at module scope, so this module stays importable in a
plain test environment without either installed — same convention as
``lora_library/nodes_sets.py`` (see its module docstring) and
comfyui-photoshop-bridge's ``cpsb/nodes.py``. No ``set_context`` is needed:
unlike the lora nodes, EPS Resolution has no file-backed state to wire in.
"""

from __future__ import annotations

from typing import Any

CATEGORY_NAME = "EPSNodes"

#: FORMAT.md §6.5 combo options — user-facing, stable identifiers (widget
#: values persist in saved workflows, so don't rename these once shipped).
RESIZE_METHODS = ["stretch", "keep aspect (fit)", "crop to fill", "pad"]
INTERPOLATIONS = ["nearest", "bilinear", "bicubic", "area", "lanczos"]

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


class EPSResolution:
    """Elegant, IMAGE-first (not latent) all-in-one resolution node.

    Re-derives everything from its inputs on every execution — there is no
    persisted state to go stale. With no ``image`` wired, the node is a pure
    target-size source: ``resized_image`` is ``None`` and
    ``original_width``/``original_height`` are ``0``, but ``width``/``height``
    still report the (0-axis-derivation-aside, since that needs an image to
    derive an aspect from) requested target, ``multiple_of``-rounded — so it
    is usable standalone to drive downstream size-consuming nodes.
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
    FUNCTION = "resolve"
    DESCRIPTION = (
        "EPS Resolution — set a target width/height (0 on an axis derives it "
        "from the other axis + the input image's aspect), get the resized "
        "image, the original image untouched, and both images' sizes — one "
        "node instead of a resize + a reroute + a get-size node. Pipe the "
        "width/height outputs into KJNodes' resize for anything fancier than "
        "stretch / keep-aspect-fit / crop-to-fill / pad."
    )

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "width": ("INT", {"default": 1024, "min": 0, "max": 16384, "step": 1}),
                "height": ("INT", {"default": 1024, "min": 0, "max": 16384, "step": 1}),
                "resize_method": (RESIZE_METHODS, {"default": "stretch"}),
                "interpolation": (INTERPOLATIONS, {"default": "bilinear"}),
                "multiple_of": ("INT", {"default": 0, "min": 0, "max": 1024, "step": 1}),
            },
            "optional": {"image": ("IMAGE",)},
        }

    def resolve(
        self,
        width: int,
        height: int,
        resize_method: str = "stretch",
        interpolation: str = "bilinear",
        multiple_of: int = 0,
        image: Any = None,
    ) -> tuple[Any, Any, int, int, int, int]:
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
                fit_w, fit_h = _fit_dimensions(
                    original_width, original_height, target_w, target_h
                )
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
