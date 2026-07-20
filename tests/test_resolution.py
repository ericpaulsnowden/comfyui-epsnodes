"""Tests for eps_image.nodes_resolution (FORMAT.md §6.5, EPS Resolution M1).

``comfy.utils.common_upscale`` is faked via ``sys.modules`` (same convention
as ``lora_library``'s ``fake_comfy`` fixture — see tests/test_nodes_sets.py)
so this module stays testable without a real ComfyUI install on the path.
Unlike that fixture's recorder-style fake, this one is a faithful port of
core's actual crop-then-interpolate algorithm (``comfy/utils.py``,
verified on the rig) restricted to torch-native interpolate modes, so shape
and crop-region assertions below exercise real resize behavior, not a stub.
Real ``torch`` tensors are used throughout (available in the rig venv).
"""

from __future__ import annotations

import sys
import types

import pytest
import torch

from eps_image import nodes_resolution


@pytest.fixture(autouse=True)
def _fake_comfy_utils(monkeypatch: pytest.MonkeyPatch):
    """Fakes ``comfy.utils.common_upscale`` with a real crop + resize.

    Faithful port of core ``comfy/utils.py``'s ``common_upscale`` (center
    crop toward the target aspect, then interpolate to the exact target
    size) restricted to modes ``torch.nn.functional.interpolate`` natively
    supports — "lanczos"/"bislerp" fall back to "bilinear" here since we're
    testing OUR node's shape/dispatch logic, not core's custom kernels.
    """

    def common_upscale(samples, width, height, upscale_method, crop):
        if crop == "center":
            old_width = samples.shape[-1]
            old_height = samples.shape[-2]
            old_aspect = old_width / old_height
            new_aspect = width / height
            x = y = 0
            if old_aspect > new_aspect:
                x = round((old_width - old_width * (new_aspect / old_aspect)) / 2)
            elif old_aspect < new_aspect:
                y = round((old_height - old_height * (old_aspect / new_aspect)) / 2)
            s = samples.narrow(-2, y, old_height - y * 2).narrow(-1, x, old_width - x * 2)
        else:
            s = samples
        mode = (
            upscale_method
            if upscale_method in ("nearest-exact", "nearest", "bilinear", "bicubic", "area")
            else "bilinear"
        )
        return torch.nn.functional.interpolate(s, size=(height, width), mode=mode)

    fake_utils = types.ModuleType("comfy.utils")
    fake_utils.common_upscale = common_upscale
    fake_comfy_pkg = types.ModuleType("comfy")
    fake_comfy_pkg.utils = fake_utils
    monkeypatch.setitem(sys.modules, "comfy", fake_comfy_pkg)
    monkeypatch.setitem(sys.modules, "comfy.utils", fake_utils)


def _make_image(height: int, width: int, batch: int = 1, value: float = 1.0) -> torch.Tensor:
    """A synthetic ``[B,H,W,C]`` IMAGE tensor filled with a constant value."""
    return torch.full((batch, height, width, 3), value, dtype=torch.float32)


def _node() -> nodes_resolution.EPSResolution:
    return nodes_resolution.EPSResolution()


# ------------------------------------------------------------------- stretch


def test_stretch_produces_exact_target_shape_and_reports_it() -> None:
    image = _make_image(height=64, width=128)  # aspect 2:1
    node = _node()
    out_image, resized, width, height, orig_w, orig_h = node.resolve(
        width=50, height=200, resize_method="stretch", interpolation="bilinear", image=image
    )
    assert resized.shape == (1, 200, 50, 3)
    assert (width, height) == (50, 200)
    assert (orig_w, orig_h) == (128, 64)
    assert out_image is image


# ------------------------------------------------------------ keep aspect fit


def test_keep_aspect_fit_produces_contained_size_not_the_full_box() -> None:
    image = _make_image(height=100, width=200)  # aspect 2:1
    node = _node()
    _, resized, width, height, orig_w, orig_h = node.resolve(
        width=100,
        height=100,
        resize_method="keep aspect (fit)",
        interpolation="bilinear",
        image=image,
    )
    # Contained within the 100x100 box, aspect preserved -> 100x50, not 100x100.
    assert resized.shape == (1, 50, 100, 3)
    assert (width, height) == (100, 50)
    assert (orig_w, orig_h) == (200, 100)


# -------------------------------------------------------------- crop to fill


def test_crop_to_fill_produces_exact_target_shape() -> None:
    image = _make_image(height=200, width=100)  # portrait, aspect 0.5
    node = _node()
    _, resized, width, height, _, _ = node.resolve(
        width=100, height=100, resize_method="crop to fill", interpolation="bilinear", image=image
    )
    assert resized.shape == (1, 100, 100, 3)
    assert (width, height) == (100, 100)


# ------------------------------------------------------------------------ pad


def test_pad_produces_exact_target_shape_with_black_borders() -> None:
    image = _make_image(height=100, width=200, value=1.0)  # aspect 2:1, all-ones content
    node = _node()
    _, resized, width, height, _, _ = node.resolve(
        width=100, height=100, resize_method="pad", interpolation="nearest", image=image
    )
    assert resized.shape == (1, 100, 100, 3)
    assert (width, height) == (100, 100)

    # Fitted content is 100x50 centered on a 100x100 canvas -> rows 0..24 and
    # 75..99 are pad (black); rows 25..74 are the all-ones source content.
    assert torch.all(resized[:, 0, :, :] == 0.0)
    assert torch.all(resized[:, 99, :, :] == 0.0)
    assert torch.all(resized[:, 50, :, :] == 1.0)


# ------------------------------------------------------------- 0-axis derive


def test_zero_width_derives_from_height_and_image_aspect() -> None:
    image = _make_image(height=100, width=200)  # aspect 2:1
    node = _node()
    _, resized, width, height, _, _ = node.resolve(
        width=0, height=50, resize_method="stretch", interpolation="bilinear", image=image
    )
    assert (width, height) == (100, 50)
    assert resized.shape == (1, 50, 100, 3)


def test_zero_height_derives_from_width_and_image_aspect() -> None:
    image = _make_image(height=100, width=200)  # aspect 2:1
    node = _node()
    _, resized, width, height, _, _ = node.resolve(
        width=80, height=0, resize_method="stretch", interpolation="bilinear", image=image
    )
    assert (width, height) == (80, 40)
    assert resized.shape == (1, 40, 80, 3)


def test_zero_both_axes_derives_the_original_size() -> None:
    image = _make_image(height=60, width=90)
    node = _node()
    _, resized, width, height, orig_w, orig_h = node.resolve(
        width=0, height=0, resize_method="stretch", interpolation="bilinear", image=image
    )
    assert (width, height) == (90, 60)
    assert resized.shape == (1, 60, 90, 3)
    assert (orig_w, orig_h) == (90, 60)


# ------------------------------------------------------------------ multiple_of


def test_multiple_of_rounds_the_final_target_with_an_image() -> None:
    image = _make_image(height=10, width=10)
    node = _node()
    _, resized, width, height, _, _ = node.resolve(
        width=1000, height=500, resize_method="stretch", interpolation="bilinear",
        multiple_of=64, image=image,
    )
    # 1000/64 = 15.625 -> round 16 -> 1024; 500/64 = 7.8125 -> round 8 -> 512.
    assert (width, height) == (1024, 512)
    assert resized.shape == (1, 512, 1024, 3)


def test_multiple_of_rounds_the_pure_size_source_with_no_image() -> None:
    node = _node()
    _, resized, width, height, orig_w, orig_h = node.resolve(
        width=100, height=100, multiple_of=64, image=None
    )
    # 100/64 = 1.5625 -> round 2 -> 128.
    assert (width, height) == (128, 128)
    assert resized is None
    assert (orig_w, orig_h) == (0, 0)


def test_multiple_of_off_by_default_leaves_target_untouched() -> None:
    node = _node()
    _, _, width, height, _, _ = node.resolve(width=101, height=203, image=None)
    assert (width, height) == (101, 203)


@pytest.mark.parametrize(
    ("box", "orig_wh"),
    [
        (1080, (200, 100)),  # 2:1 landscape into a square box
        (1000, (200, 100)),
        (1000, (100, 200)),  # 1:2 portrait into a square box
        (500, (300, 100)),  # 3:1
    ],
)
def test_keep_aspect_fit_never_exceeds_box_with_multiple_of(box: int, orig_wh) -> None:
    # Regression (R9 review, MAJOR): "keep aspect (fit)" must FLOOR the fitted
    # axes to multiple_of, never round to nearest -- nearest could push a
    # fitted axis back above the box (e.g. 2:1 into 1080 sq @ 64 -> fit
    # 1080x540 -> nearest 1088x512, and 1088 > 1080), breaking "fit within".
    orig_w, orig_h = orig_wh
    image = _make_image(height=orig_h, width=orig_w)
    node = _node()
    _, resized, width, height, _, _ = node.resolve(
        width=box,
        height=box,
        resize_method="keep aspect (fit)",
        interpolation="bilinear",
        multiple_of=64,
        image=image,
    )
    assert width <= box and height <= box  # containment: never exceeds the box
    assert width % 64 == 0 and height % 64 == 0  # both axes honor multiple_of
    assert resized.shape == (1, height, width, 3)  # outputs match the resized image


def test_keep_aspect_fit_multiple_of_keeps_a_tiny_box_contained() -> None:
    # Degenerate: box smaller than multiple_of on the fitted axis -> flooring
    # to 64 would be 0 (invalid). _floor_to_multiple keeps the raw fitted value
    # instead, so the result still fits rather than collapsing or overflowing.
    image = _make_image(height=100, width=200)  # 2:1
    node = _node()
    _, resized, width, height, _, _ = node.resolve(
        width=100,
        height=100,
        resize_method="keep aspect (fit)",
        interpolation="bilinear",
        multiple_of=64,
        image=image,
    )
    # fit into 100x100 -> 100x50; 50 < 64 so height keeps 50; 100 floors to 64.
    assert width <= 100 and height <= 100
    assert (width, height) == (64, 50)
    assert resized.shape == (1, 50, 64, 3)


# --------------------------------------------------------------- no image


def test_no_image_returns_target_wh_and_safe_empty_resized() -> None:
    node = _node()
    out_image, resized, width, height, orig_w, orig_h = node.resolve(
        width=640, height=480, resize_method="crop to fill", interpolation="lanczos"
    )
    assert out_image is None
    assert resized is None
    assert (width, height) == (640, 480)
    assert (orig_w, orig_h) == (0, 0)


def test_no_image_with_zero_axis_cannot_derive_and_stays_zero() -> None:
    node = _node()
    _, resized, width, height, orig_w, orig_h = node.resolve(width=0, height=512, image=None)
    assert resized is None
    assert (width, height) == (0, 512)
    assert (orig_w, orig_h) == (0, 0)


def test_no_image_both_axes_zero_stays_zero() -> None:
    node = _node()
    _, resized, width, height, _, _ = node.resolve(width=0, height=0, image=None)
    assert (width, height) == (0, 0)
    assert resized is None


# --------------------------------------------------------------- passthrough


def test_image_output_is_the_exact_same_object_untouched() -> None:
    image = _make_image(height=32, width=32)
    node = _node()
    out_image, *_ = node.resolve(width=16, height=16, image=image)
    assert out_image is image


def test_original_size_outputs_report_the_input_images_actual_shape() -> None:
    image = _make_image(height=77, width=55)
    node = _node()
    *_, orig_w, orig_h = node.resolve(width=10, height=10, image=image)
    assert (orig_w, orig_h) == (55, 77)


# ------------------------------------------------------------------- shape


def test_class_shape_matches_format_md_section_6_5() -> None:
    cls = nodes_resolution.EPSResolution
    assert cls.CATEGORY == "EPSNodes"
    assert cls.RETURN_TYPES == ("IMAGE", "IMAGE", "INT", "INT", "INT", "INT")
    assert cls.RETURN_NAMES == (
        "image",
        "resized_image",
        "width",
        "height",
        "original_width",
        "original_height",
    )
    assert cls.FUNCTION == "resolve"


def test_input_types_declares_widgets_and_optional_image() -> None:
    input_types = nodes_resolution.EPSResolution.INPUT_TYPES()
    required = input_types["required"]
    assert required["width"][0] == "INT"
    assert required["height"][0] == "INT"
    assert required["resize_method"][0] == [
        "stretch",
        "keep aspect (fit)",
        "crop to fill",
        "pad",
    ]
    assert required["interpolation"][0] == ["nearest", "bilinear", "bicubic", "area", "lanczos"]
    assert required["multiple_of"][0] == "INT"
    assert required["multiple_of"][1]["default"] == 0
    assert input_types["optional"]["image"] == ("IMAGE",)


def test_no_comfy_or_torch_bound_at_module_scope() -> None:
    """The module docstring's promise: ``comfy``/``torch`` are imported only
    inside the functions that need real tensors, never at module scope, so
    the module stays importable in a plain test environment without either
    installed."""
    assert "torch" not in vars(nodes_resolution)
    assert "comfy" not in vars(nodes_resolution)
