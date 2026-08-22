"""Tests for eps_image.nodes_resolution (FORMAT.md §6.5, EPS Resolution
M1 + M3 server-side size presets).

``comfy.utils.common_upscale`` is faked via ``sys.modules`` (same convention
as ``lora_library``'s ``fake_comfy`` fixture — see tests/test_nodes_sets.py)
so this module stays testable without a real ComfyUI install on the path.
Unlike that fixture's recorder-style fake, this one is a faithful port of
core's actual crop-then-interpolate algorithm (``comfy/utils.py``,
verified on the rig) restricted to torch-native interpolate modes, so shape
and crop-region assertions below exercise real resize behavior, not a stub.
Real ``torch`` tensors are used throughout (available in the rig venv).

Every pre-existing M1 test below calls :func:`_resolve_scalar` rather than
``node.resolve`` directly: with all six outputs now ``OUTPUT_IS_LIST``
(M3), ``resolve`` always returns six LISTS, even on the (still default,
still by far the most common) empty-presets path -- length-1 lists holding
exactly the same values the old scalar return used to carry. That
equivalence is itself pinned by
``TestPresetsEmptyOrAbsent.test_resolve_wraps_scalar_result_in_length_one_lists``
below; every other pre-existing test just needs the unwrapped scalars back
to keep asserting what it always asserted, which is what
:func:`_resolve_scalar` is for.
"""

from __future__ import annotations

import json
import sys
import types

import pytest

pytest.importorskip("torch")

import torch

from eps_image import nodes_resolution
from eps_image import resolution_presets_store as presets_store
from lora_library.context import LibraryContext

VALUES = {
    "width": 300,
    "height": 150,
    "resize_method": "stretch",
    "interpolation": "bilinear",
    "multiple_of": 0,
}


def _other_values(**overrides: object) -> dict:
    values = dict(VALUES)
    values.update(overrides)
    return values


@pytest.fixture(autouse=True)
def _wire_context(context: LibraryContext):
    """Wires a fresh tmp_path-backed LibraryContext before every test in
    this file (mirrors ``lora_library``'s own
    ``test_nodes_notebook._wire_context`` convention exactly). Harmless for
    the M1 tests below, which never select a preset and so never touch
    ``_context`` at all; required for the M3 preset tests further down.
    """
    nodes_resolution.set_context(context)
    yield
    nodes_resolution.set_context(None)


@pytest.fixture(autouse=True)
def fake_execution_blocker(monkeypatch: pytest.MonkeyPatch):
    """v0.61.0: every run now emits blockers on the unrevealed resized_N
    tail outputs (and, in multi mode, on the single-image-only outputs),
    so the lazy `comfy_execution` import runs on EVERY path -- the same
    autouse convention tests/test_cross_sweep.py documents."""

    class FakeExecutionBlocker:
        def __init__(self, message):
            self.message = message

    graph_mod = types.ModuleType("comfy_execution.graph")
    graph_mod.ExecutionBlocker = FakeExecutionBlocker
    pkg_mod = types.ModuleType("comfy_execution")
    pkg_mod.graph = graph_mod
    monkeypatch.setitem(sys.modules, "comfy_execution", pkg_mod)
    monkeypatch.setitem(sys.modules, "comfy_execution.graph", graph_mod)
    return FakeExecutionBlocker


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


def _resolve_scalar(node: nodes_resolution.EPSResolution, **kwargs: object):
    """Call ``node.resolve`` and unwrap its six length-1 ``OUTPUT_IS_LIST``
    lists back to plain scalars.

    Every test using this helper exercises the empty/absent-presets path
    (no ``presets`` kwarg passed), which the M3 feature contract requires
    stay pixel-for-pixel identical to the pre-list-fan-out scalar
    computation -- just each value now arrives wrapped in a length-1 list.
    Asserting the length-1-ness here, on every call, is itself part of
    that contract's coverage, not just a convenience unwrap.
    """
    result = node.resolve(**kwargs)
    # v0.61.0: 13 outputs -- the first six are the historical contract,
    # the 7 resized_N tail lists carry one blocker per run when no
    # image_N is wired (asserted in TestMultiImage, tolerated here).
    assert len(result) == 13
    out_image, resized, width, height, orig_w, orig_h = result[:6]
    lists = (out_image, resized, width, height, orig_w, orig_h)
    assert all(isinstance(lst, list) and len(lst) == 1 for lst in lists)
    return out_image[0], resized[0], width[0], height[0], orig_w[0], orig_h[0]


# ------------------------------------------------------------------- stretch


def test_stretch_produces_exact_target_shape_and_reports_it() -> None:
    image = _make_image(height=64, width=128)  # aspect 2:1
    node = _node()
    out_image, resized, width, height, orig_w, orig_h = _resolve_scalar(node,
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
    _, resized, width, height, orig_w, orig_h = _resolve_scalar(node,
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
    _, resized, width, height, _, _ = _resolve_scalar(node,
        width=100, height=100, resize_method="crop to fill", interpolation="bilinear", image=image
    )
    assert resized.shape == (1, 100, 100, 3)
    assert (width, height) == (100, 100)


# ------------------------------------------------------------------------ pad


def test_pad_produces_exact_target_shape_with_black_borders() -> None:
    image = _make_image(height=100, width=200, value=1.0)  # aspect 2:1, all-ones content
    node = _node()
    _, resized, width, height, _, _ = _resolve_scalar(node,
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
    _, resized, width, height, _, _ = _resolve_scalar(node,
        width=0, height=50, resize_method="stretch", interpolation="bilinear", image=image
    )
    assert (width, height) == (100, 50)
    assert resized.shape == (1, 50, 100, 3)


def test_zero_height_derives_from_width_and_image_aspect() -> None:
    image = _make_image(height=100, width=200)  # aspect 2:1
    node = _node()
    _, resized, width, height, _, _ = _resolve_scalar(node,
        width=80, height=0, resize_method="stretch", interpolation="bilinear", image=image
    )
    assert (width, height) == (80, 40)
    assert resized.shape == (1, 40, 80, 3)


def test_zero_both_axes_derives_the_original_size() -> None:
    image = _make_image(height=60, width=90)
    node = _node()
    _, resized, width, height, orig_w, orig_h = _resolve_scalar(node,
        width=0, height=0, resize_method="stretch", interpolation="bilinear", image=image
    )
    assert (width, height) == (90, 60)
    assert resized.shape == (1, 60, 90, 3)
    assert (orig_w, orig_h) == (90, 60)


# ------------------------------------------------------------------ multiple_of


def test_multiple_of_rounds_the_final_target_with_an_image() -> None:
    image = _make_image(height=10, width=10)
    node = _node()
    _, resized, width, height, _, _ = _resolve_scalar(node,
        width=1000, height=500, resize_method="stretch", interpolation="bilinear",
        multiple_of=64, image=image,
    )
    # 1000/64 = 15.625 -> round 16 -> 1024; 500/64 = 7.8125 -> round 8 -> 512.
    assert (width, height) == (1024, 512)
    assert resized.shape == (1, 512, 1024, 3)


def test_multiple_of_rounds_the_pure_size_source_with_no_image() -> None:
    node = _node()
    _, resized, width, height, orig_w, orig_h = _resolve_scalar(node,
        width=100, height=100, multiple_of=64, image=None
    )
    # 100/64 = 1.5625 -> round 2 -> 128.
    assert (width, height) == (128, 128)
    assert resized is None
    assert (orig_w, orig_h) == (0, 0)


def test_multiple_of_off_by_default_leaves_target_untouched() -> None:
    node = _node()
    _, _, width, height, _, _ = _resolve_scalar(node, width=101, height=203, image=None)
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
    _, resized, width, height, _, _ = _resolve_scalar(node,
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
    _, resized, width, height, _, _ = _resolve_scalar(node,
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
    out_image, resized, width, height, orig_w, orig_h = _resolve_scalar(node,
        width=640, height=480, resize_method="crop to fill", interpolation="lanczos"
    )
    assert out_image is None
    assert resized is None
    assert (width, height) == (640, 480)
    assert (orig_w, orig_h) == (0, 0)


def test_no_image_with_zero_axis_cannot_derive_and_stays_zero() -> None:
    node = _node()
    _, resized, width, height, orig_w, orig_h = _resolve_scalar(
        node, width=0, height=512, image=None
    )
    assert resized is None
    assert (width, height) == (0, 512)
    assert (orig_w, orig_h) == (0, 0)


def test_no_image_both_axes_zero_stays_zero() -> None:
    node = _node()
    _, resized, width, height, _, _ = _resolve_scalar(node, width=0, height=0, image=None)
    assert (width, height) == (0, 0)
    assert resized is None


# --------------------------------------------------------------- passthrough


def test_image_output_is_the_exact_same_object_untouched() -> None:
    image = _make_image(height=32, width=32)
    node = _node()
    out_image, *_ = _resolve_scalar(node, width=16, height=16, image=image)
    assert out_image is image


def test_original_size_outputs_report_the_input_images_actual_shape() -> None:
    image = _make_image(height=77, width=55)
    node = _node()
    *_, orig_w, orig_h = _resolve_scalar(node, width=10, height=10, image=image)
    assert (orig_w, orig_h) == (55, 77)


# ------------------------------------------------------------------- shape


def test_class_shape_matches_format_md_section_6_5() -> None:
    cls = nodes_resolution.EPSResolution
    assert cls.CATEGORY == "EPSNodes"
    assert cls.RETURN_TYPES == ("IMAGE", "IMAGE", "INT", "INT", "INT", "INT") + ("IMAGE",) * 7
    assert cls.RETURN_NAMES == (
        "image",
        "resized_image",
        "width",
        "height",
        "original_width",
        "original_height",
        "resized_2",
        "resized_3",
        "resized_4",
        "resized_5",
        "resized_6",
        "resized_7",
        "resized_8",
    )
    assert cls.OUTPUT_IS_LIST == (True,) * 13
    assert cls.FUNCTION == "resolve"


def test_widgets_are_height_first() -> None:
    """v0.61.0 (owner ask 2026-08-10): height ABOVE width -- widgets only;
    output slots stay width-first (§8-frozen). The old-save value
    transposition is handled by resolution.js's migration shim (pinned in
    tests/test_resolution_grid_js.py)."""
    required = nodes_resolution.EPSResolution.INPUT_TYPES()["required"]
    keys = list(required)
    assert keys.index("height") < keys.index("width")
    assert keys == ["height", "width", "resize_method", "interpolation", "multiple_of"]


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
    # Type-only (not exact-tuple): `image` also carries a UI-facing
    # `tooltip` string (text pass, 2026-07).
    assert input_types["optional"]["image"][0] == "IMAGE"


def test_no_comfy_or_torch_bound_at_module_scope() -> None:
    """The module docstring's promise: ``comfy``/``torch`` are imported only
    inside the functions that need real tensors, never at module scope, so
    the module stays importable in a plain test environment without either
    installed."""
    assert "torch" not in vars(nodes_resolution)
    assert "comfy" not in vars(nodes_resolution)


# ------------------------------------------------------------ size presets (M3)


class TestPresetsEmptyOrAbsent:
    """Empty/absent/malformed ``presets`` must be byte-for-byte identical to
    the pre-M3 scalar behavior (feature contract). These tests call
    ``node.resolve`` directly (not through ``_resolve_scalar``) to pin the
    exact length-1-list shape itself; everything ELSE in this file relies
    on that same equivalence via the helper.
    """

    def test_resolve_wraps_scalar_result_in_length_one_lists(self) -> None:
        image = _make_image(height=64, width=128)
        node = _node()
        out_image, resized, width, height, orig_w, orig_h = node.resolve(
            width=50, height=200, resize_method="stretch", interpolation="bilinear", image=image
        )[:6]
        for lst in (out_image, resized, width, height, orig_w, orig_h):
            assert isinstance(lst, list)
            assert len(lst) == 1
        assert out_image[0] is image
        assert resized[0].shape == (1, 200, 50, 3)
        assert (width[0], height[0]) == (50, 200)
        assert (orig_w[0], orig_h[0]) == (128, 64)

    def test_absent_presets_kwarg_uses_the_default(self) -> None:
        # An old workflow (or a raw API caller) that never sends `presets`
        # at all must still work -- the function's own default parameter
        # covers it, same as every other optional widget.
        node = _node()
        _, _, width, height, _, _ = node.resolve(width=10, height=10)[:6]
        assert (width, height) == ([10], [10])

    def test_empty_presets_works_even_with_no_context_configured(self) -> None:
        # The empty/absent path must never touch _context at all -- backward
        # compatible with every M1-era caller, none of which ever called
        # set_context.
        nodes_resolution.set_context(None)
        result = _resolve_scalar(_node(), width=10, height=10)
        assert result[2:4] == (10, 10)

    def test_malformed_presets_json_falls_back_to_widget_fields_and_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        node = _node()
        with caplog.at_level("WARNING"):
            result = _resolve_scalar(node, width=42, height=24, presets="not json{{{")
        assert (result[2], result[3]) == (42, 24)
        assert any("malformed" in r.message.lower() for r in caplog.records)

    def test_presets_not_a_json_array_falls_back_and_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        node = _node()
        with caplog.at_level("WARNING"):
            result = _resolve_scalar(node, width=42, height=24, presets='{"a": 1}')
        assert (result[2], result[3]) == (42, 24)
        assert any("JSON array" in r.message for r in caplog.records)

    def test_presets_entries_must_be_strings_others_dropped_with_warning(
        self, context: LibraryContext, caplog: pytest.LogCaptureFixture
    ) -> None:
        presets_store.save_preset(context, "Good", VALUES)
        presets_store.save_preset(context, "AlsoGood", _other_values(width=10))
        node = _node()
        with caplog.at_level("WARNING"):
            _, _, width, _height, _, _ = node.resolve(
                width=1, height=1, presets=json.dumps(["Good", 5, None, "AlsoGood"])
            )[:6]
        assert width == [VALUES["width"], 10]
        assert any("not a string" in r.message for r in caplog.records)


class TestPresetsInputDeclaration:
    def test_presets_declared_as_hidden_optional_string_default_empty_array(self) -> None:
        optional = nodes_resolution.EPSResolution.INPUT_TYPES()["optional"]
        assert optional["presets"][0] == "STRING"
        assert optional["presets"][1]["default"] == "[]"
        assert optional["presets"][1]["hidden"] is True

    def test_output_is_list_declared_for_all_outputs(self) -> None:
        assert nodes_resolution.EPSResolution.OUTPUT_IS_LIST == (True,) * 13

    def test_width_height_multiple_of_bounds_unchanged(self) -> None:
        # The M3 refactor moved these into named constants -- pin the
        # VALUES stayed byte-identical to the pre-refactor inline literals.
        required = nodes_resolution.EPSResolution.INPUT_TYPES()["required"]
        assert (required["width"][1]["min"], required["width"][1]["max"]) == (0, 16384)
        assert (required["height"][1]["min"], required["height"][1]["max"]) == (0, 16384)
        assert (required["multiple_of"][1]["min"], required["multiple_of"][1]["max"]) == (0, 1024)


class TestPresetsSelected:
    """K selected preset names -> K-length index-aligned lists, computed
    entirely from the STORE's values; the node's own widget fields are
    ignored (feature contract)."""

    def test_single_preset_uses_store_values_ignoring_widget_fields(
        self, context: LibraryContext
    ) -> None:
        image = _make_image(height=64, width=128)
        presets_store.save_preset(
            context,
            "Insta Square",
            {
                "width": 64,
                "height": 64,
                "resize_method": "stretch",
                "interpolation": "nearest",
                "multiple_of": 0,
            },
        )
        node = _node()
        out_image, resized, width, height, orig_w, orig_h = node.resolve(
            # Deliberately mismatched widget fields -- must be fully ignored
            # whenever a preset is selected (feature contract).
            width=9999,
            height=1,
            resize_method="pad",
            interpolation="lanczos",
            multiple_of=999,
            image=image,
            presets=json.dumps(["Insta Square"]),
        )[:6]
        assert (width, height) == ([64], [64])
        assert resized[0].shape == (1, 64, 64, 3)
        assert (orig_w, orig_h) == ([128], [64])
        assert out_image == [image]

    def test_k_presets_return_k_length_lists_in_selection_order(
        self, context: LibraryContext
    ) -> None:
        image = _make_image(height=64, width=128)  # 2:1, same golden case as
        # test_stretch_produces_exact_target_shape_and_reports_it /
        # test_keep_aspect_fit_produces_contained_size_not_the_full_box above.
        presets_store.save_preset(
            context,
            "P1",
            {
                "width": 50,
                "height": 200,
                "resize_method": "stretch",
                "interpolation": "bilinear",
                "multiple_of": 0,
            },
        )
        presets_store.save_preset(
            context,
            "P2",
            {
                "width": 100,
                "height": 100,
                "resize_method": "keep aspect (fit)",
                "interpolation": "bilinear",
                "multiple_of": 0,
            },
        )
        node = _node()
        # Selected in the OPPOSITE order from how they were saved, to pin
        # "selection order, not store order".
        _, resized, width, height, orig_w, orig_h = node.resolve(
            width=1, height=1, image=image, presets=json.dumps(["P2", "P1"])
        )[:6]
        assert width == [100, 50]
        assert height == [50, 200]
        assert resized[0].shape == (1, 50, 100, 3)  # P2: keep aspect (fit)
        assert resized[1].shape == (1, 200, 50, 3)  # P1: stretch
        assert orig_w == [128, 128]  # same source image both times
        assert orig_h == [64, 64]

    def test_k_presets_with_no_image_acts_as_calculator_per_element(
        self, context: LibraryContext
    ) -> None:
        presets_store.save_preset(
            context,
            "Rounded",
            {
                "width": 100,
                "height": 100,
                "resize_method": "stretch",
                "interpolation": "bilinear",
                "multiple_of": 64,
            },
        )
        presets_store.save_preset(
            context,
            "Exact",
            {
                "width": 1000,
                "height": 500,
                "resize_method": "stretch",
                "interpolation": "bilinear",
                "multiple_of": 0,
            },
        )
        node = _node()
        out_image, resized, width, height, orig_w, orig_h = node.resolve(
            width=1, height=1, image=None, presets=json.dumps(["Rounded", "Exact"])
        )[:6]
        assert out_image == [None, None]
        assert resized == [None, None]
        # 100/64 = 1.5625 -> round 2 -> 128, matching
        # test_multiple_of_rounds_the_pure_size_source_with_no_image's own
        # golden computation above; "Exact" has multiple_of=0 (off).
        assert width == [128, 1000]
        assert height == [128, 500]
        assert orig_w == [0, 0]
        assert orig_h == [0, 0]

    def test_missing_preset_name_raises_naming_it_and_the_file(
        self, context: LibraryContext
    ) -> None:
        node = _node()
        with pytest.raises(ValueError) as excinfo:
            node.resolve(width=1, height=1, presets=json.dumps(["DoesNotExist"]))
        message = str(excinfo.value)
        assert "DoesNotExist" in message
        assert presets_store.PRESETS_FILENAME in message

    def test_one_missing_among_several_still_raises_naming_the_missing_one(
        self, context: LibraryContext
    ) -> None:
        presets_store.save_preset(context, "Real", VALUES)
        node = _node()
        with pytest.raises(ValueError, match="Ghost"):
            node.resolve(width=1, height=1, presets=json.dumps(["Real", "Ghost"]))

    def test_no_context_configured_raises_runtime_error_when_presets_selected(self) -> None:
        nodes_resolution.set_context(None)
        node = _node()
        with pytest.raises(RuntimeError):
            node.resolve(width=1, height=1, presets=json.dumps(["Anything"]))


class TestIsChanged:
    """The M3 cache-busting token (module docstring/`IS_CHANGED` docstring):
    a no-op for the common no-presets case, but sensitive to on-disk preset
    edits once one or more presets are actually selected.
    """

    def test_no_presets_returns_a_constant_regardless_of_other_kwargs(self) -> None:
        # Must contribute NOTHING beyond ComfyUI's own default input-value
        # caching -- which already covers width/height/etc changes -- so a
        # plain widget-driven node's caching is completely unaffected.
        first = nodes_resolution.EPSResolution.IS_CHANGED(presets="[]", width=10)
        second = nodes_resolution.EPSResolution.IS_CHANGED(presets="[]", width=999)
        assert first == second

    def test_absent_presets_kwarg_also_returns_the_constant(self) -> None:
        assert nodes_resolution.EPSResolution.IS_CHANGED() == (
            nodes_resolution.EPSResolution.IS_CHANGED(presets="[]")
        )

    def test_selected_preset_token_changes_when_its_values_are_edited(
        self, context: LibraryContext
    ) -> None:
        presets_store.save_preset(context, "A", VALUES)
        selection = json.dumps(["A"])
        token_before = nodes_resolution.EPSResolution.IS_CHANGED(presets=selection)
        presets_store.save_preset(context, "A", _other_values(width=1))
        token_after = nodes_resolution.EPSResolution.IS_CHANGED(presets=selection)
        assert token_before != token_after

    def test_selected_preset_with_no_context_does_not_crash(self) -> None:
        nodes_resolution.set_context(None)
        token = nodes_resolution.EPSResolution.IS_CHANGED(presets=json.dumps(["A"]))
        assert isinstance(token, str)


class TestMultiImage:
    """v0.61.0 (FORMAT.md §6.5): the multi-image mode -- growing image_N
    inputs, tail resized_N outputs, blockers on the single-image-only
    outputs."""

    def test_flexible_optional_accepts_only_2_through_8(self) -> None:
        optional = nodes_resolution.EPSResolution.INPUT_TYPES()["optional"]
        for n in range(2, 9):
            assert f"image_{n}" in optional
            assert optional[f"image_{n}"][0] == "IMAGE"
        assert "image_1" not in optional
        assert "image_9" not in optional
        assert "image_" not in optional
        # static entries untouched, and iteration still shows only them
        assert set(optional) == {"image", "presets"}

    def test_two_images_same_target_both_resized(
        self, fake_execution_blocker: type
    ) -> None:
        a = _make_image(height=64, width=128)
        b = _make_image(height=32, width=32)
        node = _node()
        result = node.resolve(width=100, height=50, image=a, image_2=b)
        resized_1 = result[1][0]
        resized_2 = result[6][0]  # resized_2 is output index 6
        assert tuple(resized_1.shape) == (1, 50, 100, 3)
        assert tuple(resized_2.shape) == (1, 50, 100, 3)
        # single-image-only outputs are blocked in multi mode
        assert isinstance(result[0][0], fake_execution_blocker)   # image passthrough
        assert isinstance(result[4][0], fake_execution_blocker)   # original_width
        assert isinstance(result[5][0], fake_execution_blocker)   # original_height
        # width/height still report the shared target
        assert result[2][0] == 100 and result[3][0] == 50

    def test_gap_slots_block_only_their_own_output(
        self, fake_execution_blocker: type
    ) -> None:
        a = _make_image(height=64, width=64)
        c = _make_image(height=48, width=48)
        node = _node()
        result = node.resolve(width=32, height=32, image=a, image_4=c)
        assert tuple(result[1][0].shape) == (1, 32, 32, 3)       # image -> resized_image
        assert isinstance(result[6][0], fake_execution_blocker)  # resized_2 (unwired)
        assert isinstance(result[7][0], fake_execution_blocker)  # resized_3 (unwired)
        assert tuple(result[8][0].shape) == (1, 32, 32, 3)       # image_4 -> resized_4
        for idx in range(9, 13):
            assert isinstance(result[idx][0], fake_execution_blocker)

    def test_zero_dim_derives_from_first_wired_image_for_all(self) -> None:
        # width=0 -> derived from the FIRST wired image's aspect, then the
        # CONCRETE target applies to everyone (same size for all).
        a = _make_image(height=100, width=200)  # 2:1 -> width derives to 128
        b = _make_image(height=77, width=31)    # wild aspect -- must NOT affect target
        node = _node()
        result = node.resolve(width=0, height=64, image=a, image_2=b)
        assert result[2][0] == 128 and result[3][0] == 64
        assert tuple(result[1][0].shape) == (1, 64, 128, 3)
        assert tuple(result[6][0].shape) == (1, 64, 128, 3)

    def test_first_slot_unwired_derivation_falls_to_lowest_wired(self) -> None:
        b = _make_image(height=50, width=100)  # 2:1
        node = _node()
        result = node.resolve(width=0, height=32, image_2=b)
        assert result[2][0] == 64 and result[3][0] == 32
        assert tuple(result[6][0].shape) == (1, 32, 64, 3)

    def test_first_slot_unwired_blocks_resized_image(
        self, fake_execution_blocker: type
    ) -> None:
        b = _make_image(height=50, width=100)
        node = _node()
        result = node.resolve(width=32, height=32, image_2=b)
        assert isinstance(result[1][0], fake_execution_blocker)

    def test_single_image_mode_is_unchanged_plus_tail_blockers(
        self, fake_execution_blocker: type
    ) -> None:
        a = _make_image(height=64, width=64)
        node = _node()
        result = node.resolve(width=32, height=32, image=a)
        assert tuple(result[1][0].shape) == (1, 32, 32, 3)
        assert result[4][0] == 64 and result[5][0] == 64  # original size LIVE in single mode
        for idx in range(6, 13):
            assert isinstance(result[idx][0], fake_execution_blocker)

    def test_multi_composes_with_preset_fanout(self, context: LibraryContext) -> None:
        presets_store.save_preset(context, "P1", _other_values(width=40, height=20))
        presets_store.save_preset(context, "P2", _other_values(width=10, height=30))
        a = _make_image(height=64, width=64)
        b = _make_image(height=48, width=48)
        node = _node()
        result = node.resolve(
            width=1, height=1, image=a, image_2=b, presets=json.dumps(["P1", "P2"])
        )
        # each output: one element per preset, in selection order
        assert [tuple(x.shape) for x in result[1]] == [(1, 20, 40, 3), (1, 30, 10, 3)]
        assert [tuple(x.shape) for x in result[6]] == [(1, 20, 40, 3), (1, 30, 10, 3)]
        assert result[2] == [40, 10] and result[3] == [20, 30]


class TestMultiImageFitBoxV0681:
    """Audit 2026-08-21: in multi-image mode the shared target is the BOX
    (0 axis derived from the first image, multiple_of applied) -- "keep
    aspect (fit)" used to take the FIRST image's fitted size as everyone's
    box, so a 2:1 first image shrank a 1:1 second one to half the box and
    the result flipped with wiring order."""

    def test_fit_target_is_the_box_regardless_of_first_image_aspect(
        self, fake_execution_blocker: type
    ) -> None:
        wide = _make_image(height=100, width=200)   # 2:1
        square = _make_image(height=100, width=100)  # 1:1
        node = _node()
        result = node.resolve(
            width=1024, height=1024, resize_method="keep aspect (fit)",
            image=wide, image_2=square,
        )
        # width/height report the BOX
        assert result[2][0] == 1024 and result[3][0] == 1024
        # the square fits the whole box; the wide one fits its width
        assert tuple(result[6][0].shape) == (1, 1024, 1024, 3)
        assert tuple(result[1][0].shape) == (1, 512, 1024, 3)
        # and swapping the wiring order changes nothing about the box
        swapped = node.resolve(
            width=1024, height=1024, resize_method="keep aspect (fit)",
            image=square, image_2=wide,
        )
        assert swapped[2][0] == 1024 and swapped[3][0] == 1024
        assert tuple(swapped[1][0].shape) == (1, 1024, 1024, 3)
        assert tuple(swapped[6][0].shape) == (1, 512, 1024, 3)
