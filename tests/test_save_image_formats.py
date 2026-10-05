"""Tests for EPS Save Image's FORMAT widgets (owner request 2026-10-04: "There
is an advanced image save node that has options for format, bit depth, and
color space. Can those be added to the eps save image."; FORMAT.md §6.14
"Formats").

Core ComfyUI is not importable here, so ``comfy_extras.nodes_images`` -- the
module whose OWN encoders the node delegates to -- is replaced in
``sys.modules`` by a FAKE with recording stand-ins (``fake_core``). That
pins everything this pack is responsible for: WHICH helper is called, with
WHICH mapped arguments (our "HDR (HLG)" is core's "HDR"; our "8-bit" is
core's "8-bit YUV420"), that the BAKED prompt / workflow / pins / ``eps_run``
record reach the metadata helpers, that validation says no -- with the valid
choices in the message -- before any work is done, that a missing helper is a
plain-language refusal rather than a silent fallback, that ``preview_only``
wins, and that the defaults still write byte-identical PNGs. The one real
encode against the rig's real core module is run by hand (see the report).
"""

from __future__ import annotations

import copy
import inspect
import json
import re
import sys
import types
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from eps_image import nodes_save_image as m

# ---------------------------------------------------------------- fixtures


class _FakeTensor:
    """Just enough torch for save(): ``.shape``, ``.cpu().numpy()`` (the
    default PIL path) and ``.unsqueeze(0)`` (core's AVIF batch-of-one)."""

    def __init__(self, array: np.ndarray) -> None:
        self._array = array
        self.shape = array.shape

    def cpu(self) -> _FakeTensor:
        return self

    def numpy(self) -> np.ndarray:
        return self._array

    def unsqueeze(self, dim: int) -> _FakeTensor:
        assert dim == 0
        return _FakeTensor(self._array[np.newaxis])


def _image(w: int = 4, h: int = 3, seed: float = 0.0) -> _FakeTensor:
    base = np.linspace(0.0, 1.0, w * h * 3, dtype=np.float32).reshape(h, w, 3)
    return _FakeTensor(np.clip(base + seed, 0.0, 1.0))


class _Dirs:
    """The fake ``folder_paths`` plus a record of which getter was asked for."""

    def __init__(self, out: Path, temp: Path) -> None:
        self.out = out
        self.temp = temp
        self.calls: list[str] = []


@pytest.fixture
def dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Dirs:
    out = tmp_path / "output"
    temp = tmp_path / "temp"
    out.mkdir()
    temp.mkdir()
    state = _Dirs(out, temp)
    module = types.ModuleType("folder_paths")

    def get_output_directory() -> str:
        state.calls.append("output")
        return str(out)

    def get_temp_directory() -> str:
        state.calls.append("temp")
        return str(temp)

    def get_save_image_path(prefix: str, output_dir: str, w: int = 0, h: int = 0):
        import os

        subfolder = os.path.dirname(os.path.normpath(prefix))
        filename = os.path.basename(os.path.normpath(prefix))
        folder = os.path.join(output_dir, subfolder)
        os.makedirs(folder, exist_ok=True)
        return folder, filename, 1, subfolder, prefix

    module.get_output_directory = get_output_directory
    module.get_temp_directory = get_temp_directory
    module.get_save_image_path = get_save_image_path
    monkeypatch.setitem(sys.modules, "folder_paths", module)
    return state


class FakeCore(types.ModuleType):
    """A stand-in for ``comfy_extras.nodes_images`` that RECORDS every call.
    Arguments that core would serialize are deep-copied at call time -- the
    node bakes them in place and restores them in a ``finally``, so a later
    look at the live objects would see the un-baked originals."""

    def __init__(self) -> None:
        super().__init__("comfy_extras.nodes_images")
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_encode: Exception | None = None
        self.fail_avif: Exception | None = None

    def named(self, name: str) -> list[dict[str, Any]]:
        return [args for called, args in self.calls if called == name]

    # core's own signatures (rig comfy_extras/nodes_images.py) -- positional
    def _encode_image(self, img_tensor, file_format, bit_depth, colorspace):
        self.calls.append(
            (
                "_encode_image",
                {
                    "shape": img_tensor.shape,
                    "file_format": file_format,
                    "bit_depth": bit_depth,
                    "colorspace": colorspace,
                },
            )
        )
        if self.fail_encode is not None:
            raise self.fail_encode
        return f"ENC[{file_format}/{bit_depth}/{colorspace}]".encode()

    def inject_png_metadata(self, png_bytes, prompt, extra_pnginfo):
        self.calls.append(
            (
                "inject_png_metadata",
                {"prompt": copy.deepcopy(prompt), "extra": copy.deepcopy(extra_pnginfo)},
            )
        )
        return png_bytes + b"+PNGMETA"

    def inject_exr_metadata(self, exr_bytes, prompt, extra_pnginfo, colorspace=None):
        self.calls.append(
            (
                "inject_exr_metadata",
                {
                    "prompt": copy.deepcopy(prompt),
                    "extra": copy.deepcopy(extra_pnginfo),
                    "colorspace": colorspace,
                },
            )
        )
        return exr_bytes + b"+EXRMETA"

    def _save_avif(
        self,
        images,
        output_path,
        bit_depth,
        colorspace,
        crf,
        fps=1.0,
        loop_count=None,
        metadata=None,
    ):
        self.calls.append(
            (
                "_save_avif",
                {
                    "shape": images.shape,
                    "path": output_path,
                    "bit_depth": bit_depth,
                    "colorspace": colorspace,
                    "crf": crf,
                    "fps": fps,
                    "loop_count": loop_count,
                    "metadata": copy.deepcopy(metadata),
                },
            )
        )
        Path(output_path).write_bytes(f"AVIF[{bit_depth}/{colorspace}/{crf}]".encode())
        if self.fail_avif is not None:
            raise self.fail_avif


def _install_fake_av(
    monkeypatch: pytest.MonkeyPatch,
    *,
    codec_error: Exception | None = None,
    formats: set[str] | None = None,
) -> None:
    """A fake PyAV for ``_avif_codec_gap``: ``av.codec.Codec(name, mode)``
    either works or raises *codec_error*; ``av.formats_available`` is
    *formats*."""
    av = types.ModuleType("av")
    codec = types.ModuleType("av.codec")

    def Codec(name: str, mode: str = "r"):  # PyAV's own (capitalised) name
        if codec_error is not None:
            raise codec_error
        return types.SimpleNamespace(name=name)

    codec.Codec = Codec
    av.codec = codec
    av.formats_available = {"avif", "png"} if formats is None else formats
    monkeypatch.setitem(sys.modules, "av", av)
    monkeypatch.setitem(sys.modules, "av.codec", codec)


@pytest.fixture
def fake_core(monkeypatch: pytest.MonkeyPatch) -> FakeCore:
    """Core's image module with all four helpers, and a PyAV that can write
    AVIF -- the happy ComfyUI."""
    core = FakeCore()
    package = types.ModuleType("comfy_extras")
    package.__path__ = []  # a package, so the dotted import is realistic
    monkeypatch.setitem(sys.modules, "comfy_extras", package)
    monkeypatch.setitem(sys.modules, "comfy_extras.nodes_images", core)
    _install_fake_av(monkeypatch)
    return core


@pytest.fixture
def no_core(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ComfyUI without ``comfy_extras.nodes_images`` at all (and, as in the
    plain test environment, nothing that could import it)."""
    monkeypatch.setitem(sys.modules, "comfy_extras.nodes_images", None)
    monkeypatch.setitem(sys.modules, "comfy_extras", None)


def _without(core: FakeCore, *names: str) -> FakeCore:
    """*core* minus some helpers -- an older ComfyUI."""
    for name in names:
        # instance attribute shadows the class method; deleting from the
        # class would leak into the other tests, so shadow with None
        setattr(core, name, None)
    return core


def _run_chunks() -> tuple[dict, dict, str]:
    """``(workflow, prompt, run_info)`` for one Run Multiplier run."""
    workflow = {
        "nodes": [{"id": 5, "type": "EPSCrossSweep", "widgets_values": ["shoot", "multiply"]}]
    }
    prompt = {"5": {"class_type": "EPSCrossSweep", "inputs": {"solo_run": ""}}}
    info = json.dumps({"token": "m2_i1_t3", "node": "5", "run": 4, "total": 12})
    return workflow, prompt, info


def _saved_path(d: _Dirs, saved: dict) -> Path:
    base = d.temp if saved["type"] == "temp" else d.out
    return base / saved["subfolder"] / saved["filename"]


# The owner's table, written out independently of the implementation.
VALID = {
    "png": {"depth": {"auto", "8-bit", "16-bit"}, "space": {"sRGB"}},
    "exr": {"depth": {"auto", "32-bit float"}, "space": {"sRGB", "linear", "HDR (HLG)"}},
    "avif": {"depth": {"auto", "8-bit", "10-bit"}, "space": {"sRGB", "HDR (HLG)", "HDR PQ"}},
}
ALL_DEPTHS = ["auto", "8-bit", "10-bit", "16-bit", "32-bit float"]
ALL_SPACES = ["sRGB", "linear", "HDR (HLG)", "HDR PQ"]
ALL_COMBOS = [(f, d, s) for f in VALID for d in ALL_DEPTHS for s in ALL_SPACES]
VALID_COMBOS = [
    c for c in ALL_COMBOS if c[1] in VALID[c[0]]["depth"] and c[2] in VALID[c[0]]["space"]
]
INVALID_COMBOS = [c for c in ALL_COMBOS if c not in VALID_COMBOS]


# ------------------------------------------------------------ the matrix


class TestMatrix:
    def test_the_owners_table_is_exactly_what_the_module_declares(self) -> None:
        assert set(m.FORMATS) == {"png", "exr", "avif"}
        assert tuple(ALL_DEPTHS) == m.BIT_DEPTH_CHOICES
        assert tuple(ALL_SPACES) == m.COLOR_SPACE_CHOICES
        for name, valid in VALID.items():
            assert set(m.FORMAT_MATRIX[name]["bit_depth"]) == valid["depth"]
            assert set(m.FORMAT_MATRIX[name]["color_space"]) == valid["space"]

    def test_counts(self) -> None:
        # 3 formats x 5 depths x 4 spaces; valid = png 3x1 + exr 2x3 + avif 3x3
        assert len(ALL_COMBOS) == 60
        assert len(VALID_COMBOS) == 3 * 1 + 2 * 3 + 3 * 3 == 18
        assert len(INVALID_COMBOS) == 42

    @pytest.mark.parametrize("fmt,depth,space", VALID_COMBOS)
    def test_every_valid_combination_resolves(self, fmt, depth, space) -> None:
        plan = m.plan_for(fmt, depth, space)
        assert plan.file_format == fmt
        assert plan.color_space == space

    @pytest.mark.parametrize("fmt,depth,space", INVALID_COMBOS)
    def test_every_invalid_combination_is_refused_with_the_valid_choices(
        self, fmt, depth, space
    ) -> None:
        with pytest.raises(m.FormatError) as caught:
            m.plan_for(fmt, depth, space)
        text = str(caught.value)
        assert text.startswith("EPS Save Image:")
        if depth not in VALID[fmt]["depth"]:
            # names the bad value and lists THIS format's valid depths
            assert repr(depth) in text
            for good in VALID[fmt]["depth"]:
                assert good in text
        if space not in VALID[fmt]["space"]:
            assert repr(space) in text
            for good in VALID[fmt]["space"]:
                assert good in text

    @pytest.mark.parametrize(
        "fmt,depth,space,core_depth,core_space,default",
        [
            # png: auto == 8-bit; sRGB is the only space; the PIL default
            ("png", "auto", "sRGB", "8-bit", "sRGB", True),
            ("png", "8-bit", "sRGB", "8-bit", "sRGB", True),
            ("png", "16-bit", "sRGB", "16-bit", "sRGB", False),
            # exr: auto == 32-bit float; core's "HDR" is our "HDR (HLG)"
            ("exr", "auto", "sRGB", "32-bit float", "sRGB", False),
            ("exr", "32-bit float", "linear", "32-bit float", "linear", False),
            ("exr", "auto", "HDR (HLG)", "32-bit float", "HDR", False),
            # avif: "auto" is core's own; 8-bit/10-bit are core's YUV420 names
            ("avif", "auto", "sRGB", "auto", "sRGB", False),
            ("avif", "auto", "HDR PQ", "auto", "HDR PQ", False),
            ("avif", "8-bit", "HDR (HLG)", "8-bit YUV420", "HDR", False),
            ("avif", "10-bit", "HDR PQ", "10-bit YUV420", "HDR PQ", False),
            ("avif", "10-bit", "sRGB", "10-bit YUV420", "sRGB", False),
        ],
    )
    def test_mapping_to_core_names(
        self, fmt, depth, space, core_depth, core_space, default
    ) -> None:
        plan = m.plan_for(fmt, depth, space)
        assert (plan.core_bit_depth, plan.core_color_space) == (core_depth, core_space)
        assert plan.is_default_png is default

    def test_only_png_8bit_srgb_is_the_default_path(self) -> None:
        defaults = [c for c in VALID_COMBOS if m.plan_for(*c).is_default_png]
        assert defaults == [("png", "auto", "sRGB"), ("png", "8-bit", "sRGB")]

    def test_unknown_format_lists_the_formats(self) -> None:
        with pytest.raises(m.FormatError, match=r"'tiff'.*png, exr, avif"):
            m.plan_for("tiff", "auto", "sRGB")
        with pytest.raises(m.FormatError, match="png, exr, avif"):
            m.plan_for(["a", "b"], "auto", "sRGB")  # unhashable junk: still a plain error

    def test_absent_values_are_the_defaults_and_wrapped_values_unwrap(self) -> None:
        assert m.plan_for(None, None, None) == m.DEFAULT_PLAN
        assert m.plan_for(["exr"], ["auto"], ["linear"]) == m.plan_for("exr", "auto", "linear")

    def test_both_problems_are_reported_together(self) -> None:
        with pytest.raises(m.FormatError) as caught:
            m.plan_for("png", "32-bit float", "HDR PQ")
        assert "bit_depth" in str(caught.value) and "input_color_space" in str(caught.value)

    def test_encoder_names_and_labels(self) -> None:
        assert m.plan_for("png", "16-bit", "sRGB").encoder_name == "png 16-bit"
        assert m.plan_for("exr", "auto", "sRGB").encoder_name == "exr"
        assert m.plan_for("avif", "auto", "sRGB").label == "avif (auto, sRGB)"


class TestParseAvifCrf:
    @pytest.mark.parametrize(
        "raw,expected",
        [(1, 1), (63, 63), (18, 18), ("24", 24), (30.0, 30), ([40], 40), (None, 18)],
    )
    def test_valid(self, raw, expected) -> None:
        assert m.parse_avif_crf(raw) == expected

    @pytest.mark.parametrize("raw", [0, 64, -3, 18.5, "abc", "", True, False, {"x": 1}])
    def test_invalid(self, raw) -> None:
        with pytest.raises(m.FormatError, match="avif_crf"):
            m.parse_avif_crf(raw)


# ---------------------------------------------------------- the encoders


class TestRequireEncoder:
    def test_the_default_png_needs_no_core_at_all(self, no_core) -> None:
        assert m.require_encoder(m.DEFAULT_PLAN) is None

    @pytest.mark.parametrize(
        "fmt,depth", [("png", "16-bit"), ("exr", "auto"), ("avif", "auto")]
    )
    def test_a_complete_core_is_returned(self, fake_core, fmt, depth) -> None:
        assert m.require_encoder(m.plan_for(fmt, depth, "sRGB")) is fake_core

    @pytest.mark.parametrize(
        "fmt,depth,name,missing",
        [
            ("png", "16-bit", "png 16-bit", "_encode_image"),
            ("exr", "auto", "exr", "_encode_image"),
            ("avif", "auto", "avif", "_save_avif"),
        ],
    )
    def test_no_core_module_is_a_plain_language_refusal(
        self, no_core, fmt, depth, name, missing
    ) -> None:
        with pytest.raises(m.FormatError) as caught:
            m.require_encoder(m.plan_for(fmt, depth, "sRGB"))
        text = str(caught.value)
        assert f"can't save {name}" in text
        assert "your ComfyUI doesn't have this encoder yet" in text
        assert "update ComfyUI or pick png 8-bit" in text
        assert missing in text

    @pytest.mark.parametrize(
        "fmt,depth,drop",
        [
            ("png", "16-bit", "_encode_image"),
            ("png", "16-bit", "inject_png_metadata"),
            ("exr", "auto", "_encode_image"),
            ("exr", "auto", "inject_exr_metadata"),
            ("avif", "auto", "_save_avif"),
        ],
    )
    def test_each_missing_helper_is_caught_by_name(self, fake_core, fmt, depth, drop) -> None:
        _without(fake_core, drop)
        with pytest.raises(m.FormatError) as caught:
            m.require_encoder(m.plan_for(fmt, depth, "sRGB"))
        assert drop in str(caught.value)
        assert "doesn't have this encoder yet" in str(caught.value)

    def test_a_format_only_needs_ITS_helpers(self, fake_core) -> None:
        # An older ComfyUI: PNG16/EXR helpers present, AVIF's not -- exr and
        # png16 keep working, only avif is refused (and says 0.35).
        _without(fake_core, "_save_avif")
        assert m.require_encoder(m.plan_for("exr", "auto", "sRGB")) is fake_core
        assert m.require_encoder(m.plan_for("png", "16-bit", "sRGB")) is fake_core
        with pytest.raises(m.FormatError, match=r"0\.35"):
            m.require_encoder(m.plan_for("avif", "auto", "sRGB"))
        # ... and the reverse: only AVIF's helper present
        _without(fake_core, "_encode_image")
        fake_core._save_avif = FakeCore._save_avif.__get__(fake_core)
        assert m.require_encoder(m.plan_for("avif", "auto", "sRGB")) is fake_core
        with pytest.raises(m.FormatError, match="exr"):
            m.require_encoder(m.plan_for("exr", "auto", "sRGB"))

    def test_a_core_that_fails_to_import_counts_as_missing(self, monkeypatch) -> None:
        def explode(name: str):
            raise RuntimeError("core module is half broken")

        monkeypatch.setattr(m.importlib, "import_module", explode)
        with pytest.raises(m.FormatError, match="doesn't have this encoder yet"):
            m.require_encoder(m.plan_for("exr", "auto", "sRGB"))

    # -- PyAV (queue-time, AVIF only): only a DEFINITE absence rejects

    def test_pyav_without_the_av1_encoder_is_refused_for_avif_only(
        self, fake_core, monkeypatch
    ) -> None:
        _install_fake_av(monkeypatch, codec_error=ValueError("libsvtav1"))
        with pytest.raises(m.FormatError, match="no AV1"):
            m.require_encoder(m.plan_for("avif", "auto", "sRGB"))
        assert m.require_encoder(m.plan_for("exr", "auto", "sRGB")) is fake_core
        assert m.require_encoder(m.plan_for("png", "16-bit", "sRGB")) is fake_core

    def test_pyav_without_the_avif_writer_is_refused(self, fake_core, monkeypatch) -> None:
        _install_fake_av(monkeypatch, formats={"png", "mp4"})
        with pytest.raises(m.FormatError, match="no AVIF writer"):
            m.require_encoder(m.plan_for("avif", "auto", "sRGB"))

    def test_a_probe_it_cannot_run_never_rejects(self, fake_core, monkeypatch) -> None:
        # an API the probe doesn't recognise is "unknown", never "missing"
        _install_fake_av(monkeypatch, codec_error=TypeError("different signature"))
        assert m.require_encoder(m.plan_for("avif", "auto", "sRGB")) is fake_core
        monkeypatch.setitem(sys.modules, "av", None)  # av not even importable
        assert m.require_encoder(m.plan_for("avif", "auto", "sRGB")) is fake_core


# ------------------------------------------------------ queue-time validate


class TestValidateInputs:
    def test_names_exactly_the_inputs_it_checks(self) -> None:
        # Core skips ITS OWN min/max/combo check for any input NAMED here (and
        # for everything with **kwargs): avif_crf must stay unnamed so its
        # 1-63 range is still enforced by core.
        spec = inspect.getfullargspec(m.EPSSaveImage.VALIDATE_INPUTS)
        names = [a for a in spec.args if a != "cls"]
        assert names == ["format", "bit_depth", "input_color_space", "preview_only"]
        assert spec.varkw is None

    def test_defaults_pass_even_with_no_core(self, no_core) -> None:
        assert m.EPSSaveImage.VALIDATE_INPUTS() is True
        assert m.EPSSaveImage.VALIDATE_INPUTS("png", "auto", "sRGB", False) is True
        assert m.EPSSaveImage.VALIDATE_INPUTS("png", "8-bit", "sRGB") is True

    @pytest.mark.parametrize("fmt,depth,space", VALID_COMBOS)
    def test_every_valid_combination_passes_with_a_full_core(
        self, fake_core, fmt, depth, space
    ) -> None:
        assert m.EPSSaveImage.VALIDATE_INPUTS(fmt, depth, space, False) is True

    @pytest.mark.parametrize("fmt,depth,space", INVALID_COMBOS)
    def test_every_invalid_combination_is_a_message_listing_the_choices(
        self, fake_core, fmt, depth, space
    ) -> None:
        result = m.EPSSaveImage.VALIDATE_INPUTS(fmt, depth, space, False)
        assert isinstance(result, str) and result.startswith("EPS Save Image:")
        offending = depth if depth not in VALID[fmt]["depth"] else space
        assert repr(offending) in result
        valid = VALID[fmt]["depth"] if offending == depth else VALID[fmt]["space"]
        assert all(choice in result for choice in valid)

    def test_a_hand_built_unknown_value_is_refused_not_waved_through(self, fake_core) -> None:
        # naming the combos here switched core's own membership check off
        assert "tiff" in m.EPSSaveImage.VALIDATE_INPUTS("tiff", "auto", "sRGB", False)
        assert "12-bit" in m.EPSSaveImage.VALIDATE_INPUTS("png", "12-bit", "sRGB", False)
        assert "Rec.709" in m.EPSSaveImage.VALIDATE_INPUTS("exr", "auto", "Rec.709", False)

    def test_a_missing_encoder_is_refused_at_queue_time(self, no_core) -> None:
        for fmt in ("exr", "avif"):
            result = m.EPSSaveImage.VALIDATE_INPUTS(fmt, "auto", "sRGB", False)
            assert isinstance(result, str)
            assert fmt in result and "doesn't have this encoder yet" in result
        result = m.EPSSaveImage.VALIDATE_INPUTS("png", "16-bit", "sRGB", False)
        assert "png 16-bit" in result and "pick png 8-bit" in result

    def test_preview_only_skips_the_check(self, no_core) -> None:
        # A preview is always the plain PNG: a format it will ignore must not
        # stop the queue.
        assert m.EPSSaveImage.VALIDATE_INPUTS("avif", "auto", "sRGB", True) is True
        assert m.EPSSaveImage.VALIDATE_INPUTS("png", "32-bit float", "HDR PQ", True) is True
        assert m.EPSSaveImage.VALIDATE_INPUTS("avif", "auto", "sRGB", [True]) is True

    def test_a_wired_preview_only_is_checked_as_a_save(self, no_core) -> None:
        # linked -> None: unknown, and "save" is the safe reading
        assert isinstance(m.EPSSaveImage.VALIDATE_INPUTS("avif", "auto", "sRGB", None), str)

    def test_linked_inputs_arrive_as_none_and_are_not_guessed(self, no_core) -> None:
        # format wired: nothing to check yet (save() re-checks the real value)
        assert m.EPSSaveImage.VALIDATE_INPUTS(None, "16-bit", "HDR PQ", False) is True
        # depth/space wired: the always-valid default stands in
        assert m.EPSSaveImage.VALIDATE_INPUTS("png", None, None, False) is True

    def test_list_wrapped_values_are_unwrapped(self, fake_core) -> None:
        assert m.EPSSaveImage.VALIDATE_INPUTS(["exr"], ["auto"], ["linear"], [False]) is True
        assert isinstance(m.EPSSaveImage.VALIDATE_INPUTS(["avif"], ["16-bit"], ["sRGB"]), str)

    def test_an_unexpected_failure_is_a_courtesy_miss_not_a_blocked_queue(
        self, fake_core, monkeypatch, caplog
    ) -> None:
        def boom(plan):
            raise RuntimeError("surprise")

        monkeypatch.setattr(m, "require_encoder", boom)
        with caplog.at_level("ERROR", logger="eps_image"):
            assert m.EPSSaveImage.VALIDATE_INPUTS("exr", "auto", "sRGB", False) is True
        assert any("format check failed" in r.message for r in caplog.records)

    def test_it_runs_as_core_runs_it(self, fake_core) -> None:
        # execution.py calls getattr(cls, "VALIDATE_INPUTS")(**present_inputs),
        # passing only inputs that are in the prompt.
        fn = getattr(m.EPSSaveImage, "VALIDATE_INPUTS")  # noqa: B009
        assert fn(format="exr", bit_depth="auto", input_color_space="linear") is True
        assert isinstance(fn(format="exr", bit_depth="8-bit"), str)
        assert fn(format="avif") is True


# ----------------------------------------------------------- the widgets


class TestWidgetShapes:
    def test_the_four_tail_widgets(self) -> None:
        optional = m.EPSSaveImage.INPUT_TYPES()["optional"]
        fmt, depth, space, crf = (
            optional["format"],
            optional["bit_depth"],
            optional["input_color_space"],
            optional["avif_crf"],
        )
        assert fmt[0] == ["png", "exr", "avif"] and fmt[1]["default"] == "png"
        assert depth[0] == ["auto", "8-bit", "10-bit", "16-bit", "32-bit float"]
        assert depth[1]["default"] == "auto"
        assert space[0] == ["sRGB", "linear", "HDR (HLG)", "HDR PQ"]
        assert space[1]["default"] == "sRGB"
        assert crf[0] == "INT"
        assert (crf[1]["default"], crf[1]["min"], crf[1]["max"]) == (18, 1, 63)

    def test_advanced_flags_ride_through_and_format_stays_visible(self) -> None:
        optional = m.EPSSaveImage.INPUT_TYPES()["optional"]
        assert "advanced" not in optional["format"][1]
        for name in ("bit_depth", "input_color_space", "avif_crf"):
            assert optional[name][1]["advanced"] is True, name

    def test_tooltips_explain_the_choices(self) -> None:
        optional = m.EPSSaveImage.INPUT_TYPES()["optional"]
        for name in ("format", "bit_depth", "input_color_space", "avif_crf"):
            assert optional[name][1]["tooltip"].strip(), name
        assert "preview_only" in optional["format"][1]["tooltip"]
        assert "avif only" in optional["avif_crf"][1]["tooltip"]
        assert "lower" in optional["avif_crf"][1]["tooltip"].lower()
        assert "plain png" in optional["preview_only"][1]["tooltip"]

    def test_the_default_widget_values_are_always_valid(self) -> None:
        optional = m.EPSSaveImage.INPUT_TYPES()["optional"]
        defaults = [optional[n][1]["default"] for n in ("format", "bit_depth", "input_color_space")]
        assert m.plan_for(*defaults) == m.DEFAULT_PLAN
        assert m.DEFAULT_PLAN.is_default_png

    def test_description_mentions_the_formats_and_the_preview_rule(self) -> None:
        text = m.EPSSaveImage.DESCRIPTION
        for word in ("format", "exr", "avif", "bit_depth", "avif_crf", "preview_only"):
            assert word in text

    def test_save_signature_defaults_match_the_widget_defaults(self) -> None:
        params = inspect.signature(m.EPSSaveImage.save).parameters
        assert params["format"].default == "png"
        assert params["bit_depth"].default == "auto"
        assert params["input_color_space"].default == "sRGB"
        assert params["avif_crf"].default == 18


class TestPositionalCompatibility:
    """widgets_values restores POSITIONALLY (FORMAT.md §8): the tail is only
    ever appended, so every older array keeps meaning what it meant."""

    NEW: ClassVar[list[str]] = [
        "filename_prefix",
        "preview_only",
        "format",
        "bit_depth",
        "input_color_space",
        "avif_crf",
    ]
    V1_4: ClassVar[list[str]] = ["filename_prefix", "preview_only"]
    V0: ClassVar[list[str]] = ["filename_prefix"]

    @staticmethod
    def _restore(names: list[str], defaults: list, saved: list) -> dict:
        """What the frontend does: positional zip, defaults for what's
        missing, extra trailing values ignored."""
        values = dict(zip(names, defaults, strict=False))
        values.update(zip(names, saved, strict=False))
        return values

    def test_the_declared_order_is_the_frozen_order(self) -> None:
        names = [n for n, _k, _o in m._iter_widgets(m.EPSSaveImage)]
        assert names == self.NEW
        assert names[:2] == self.V1_4 and names[:1] == self.V0

    @pytest.mark.parametrize(
        "saved,expected_preview",
        [(["EPS"], False), (["EPS", False], False), (["EPS", True], True)],
    )
    def test_old_arrays_load_with_the_format_defaults(self, saved, expected_preview) -> None:
        restored = self._restore(self.NEW, m.widget_defaults(m.EPSSaveImage), saved)
        assert restored["filename_prefix"] == "EPS"
        assert restored["preview_only"] is expected_preview
        assert (restored["format"], restored["bit_depth"]) == ("png", "auto")
        assert (restored["input_color_space"], restored["avif_crf"]) == ("sRGB", 18)

    def test_a_partial_new_array_keeps_defaults_for_the_rest(self) -> None:
        restored = self._restore(self.NEW, m.widget_defaults(m.EPSSaveImage), ["EPS", False, "exr"])
        assert restored["format"] == "exr" and restored["bit_depth"] == "auto"

    @pytest.mark.parametrize("old_names", [V1_4, V0])
    def test_new_arrays_load_on_older_builds(self, old_names) -> None:
        saved = ["shoot/EPS", True, "avif", "10-bit", "HDR PQ", 30]
        restored = self._restore(old_names, ["EPS", False][: len(old_names)], saved)
        # the older build keeps what it knows and ignores the trailing values
        assert restored == dict(zip(old_names, saved, strict=False))

    def test_the_run_info_socket_still_holds_no_slot(self) -> None:
        assert "run_info" not in [n for n, _k, _o in m._iter_widgets(m.EPSSaveImage)]


# ------------------------------------------------------- default = bytes


def _reference_png(image: _FakeTensor, path: Path, prompt, extra, record, compress_level: int):
    """The v1.4.0 PIL write, restated: what ``save()`` has always done."""
    array = 255.0 * image.cpu().numpy()
    img = Image.fromarray(np.clip(array, 0, 255).astype(np.uint8))
    metadata = PngInfo()
    if prompt is not None:
        metadata.add_text("prompt", json.dumps(prompt))
    for key, value in extra.items():
        metadata.add_text(str(key), json.dumps(value))
    if record is not None:
        metadata.add_text("eps_run", json.dumps(record))
    img.save(path, pnginfo=metadata, compress_level=compress_level)


class TestDefaultsAreByteIdentical:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {},
            {"format": "png"},
            {"format": "png", "bit_depth": "auto", "input_color_space": "sRGB"},
            {"format": "png", "bit_depth": "8-bit", "input_color_space": "sRGB", "avif_crf": 63},
            {"format": ["png"], "bit_depth": ["auto"], "input_color_space": ["sRGB"]},
        ],
    )
    def test_with_a_run_the_png_matches_the_old_write_byte_for_byte(
        self, dirs, no_core, tmp_path, kwargs
    ) -> None:
        workflow, prompt, info = _run_chunks()
        extra = {"workflow": workflow}
        saved = m.EPSSaveImage().save(
            [_image()],
            filename_prefix="shoot/Portrait_m2_i1_t3",
            run_info=info,
            prompt=prompt,
            extra_pnginfo=extra,
            **kwargs,
        )["ui"]["images"][0]
        assert saved == {
            "filename": "Portrait_m2_i1_t3_00001_.png",
            "subfolder": "shoot",
            "type": "output",
        }
        # the same chunks, baked by hand, written the v1.4.0 way
        baked_workflow = copy.deepcopy(workflow)
        baked_workflow["nodes"][0]["widgets_values"] = ["shoot", "multiply", "multiply", "m2_i1_t3"]
        baked_prompt = copy.deepcopy(prompt)
        baked_prompt["5"]["inputs"]["solo_run"] = "m2_i1_t3"
        record = {
            **json.loads(info),
            "baked": True,
            "pinned": [],
            "format": 1,
        }
        reference = tmp_path / "reference.png"
        _reference_png(
            _image(), reference, baked_prompt, {"workflow": baked_workflow}, record, 4
        )
        assert _saved_path(dirs, saved).read_bytes() == reference.read_bytes()

    def test_without_run_info_it_is_plain_save_image_bytes(self, dirs, no_core, tmp_path) -> None:
        workflow, prompt, _info = _run_chunks()
        saved = m.EPSSaveImage().save(
            [_image()], filename_prefix="plain", prompt=prompt, extra_pnginfo={"workflow": workflow}
        )["ui"]["images"][0]
        reference = tmp_path / "reference.png"
        _reference_png(_image(), reference, prompt, {"workflow": workflow}, None, 4)
        assert _saved_path(dirs, saved).read_bytes() == reference.read_bytes()

    def test_the_default_path_never_touches_core(self, dirs, fake_core) -> None:
        m.EPSSaveImage().save([_image()], filename_prefix="plain")
        m.EPSSaveImage().save([_image()], filename_prefix="plain", format="png", bit_depth="8-bit")
        assert fake_core.calls == []

    def test_a_bad_avif_crf_is_ignored_for_png_and_exr(self, dirs, fake_core) -> None:
        # crf only means something for avif: a wired garbage value must not
        # break a png or exr save
        m.EPSSaveImage().save([_image()], filename_prefix="a", avif_crf="garbage")
        m.EPSSaveImage().save([_image()], filename_prefix="b", format="exr", avif_crf=999)


# ---------------------------------------------------------------- routing


class TestRouting:
    def test_png16_calls_encode_then_inject_png_then_writes(self, dirs, fake_core) -> None:
        result = m.EPSSaveImage().save(
            [_image()], filename_prefix="x", format="png", bit_depth="16-bit"
        )
        saved = result["ui"]["images"][0]
        assert saved == {"filename": "x_00001_.png", "subfolder": "", "type": "output"}
        assert [name for name, _ in fake_core.calls] == ["_encode_image", "inject_png_metadata"]
        assert fake_core.named("_encode_image") == [
            {"shape": (3, 4, 3), "file_format": "png", "bit_depth": "16-bit", "colorspace": "sRGB"}
        ]
        # encode -> inject -> write, in that order, on the SAME bytes
        assert _saved_path(dirs, saved).read_bytes() == b"ENC[png/16-bit/sRGB]+PNGMETA"

    @pytest.mark.parametrize(
        "space,core_space", [("sRGB", "sRGB"), ("linear", "linear"), ("HDR (HLG)", "HDR")]
    )
    def test_exr_maps_the_color_space_for_both_helpers(
        self, dirs, fake_core, space, core_space
    ) -> None:
        saved = m.EPSSaveImage().save(
            [_image()], filename_prefix="x", format="exr", input_color_space=space
        )["ui"]["images"][0]
        assert saved["filename"] == "x_00001_.exr"
        assert fake_core.named("_encode_image")[0] == {
            "shape": (3, 4, 3),
            "file_format": "exr",
            "bit_depth": "32-bit float",  # auto
            "colorspace": core_space,
        }
        assert fake_core.named("inject_exr_metadata")[0]["colorspace"] == core_space
        assert _saved_path(dirs, saved).read_bytes() == (
            f"ENC[exr/32-bit float/{core_space}]+EXRMETA".encode()
        )

    @pytest.mark.parametrize(
        "depth,space,core_depth,core_space",
        [
            ("auto", "sRGB", "auto", "sRGB"),
            ("auto", "HDR (HLG)", "auto", "HDR"),
            ("auto", "HDR PQ", "auto", "HDR PQ"),
            ("8-bit", "sRGB", "8-bit YUV420", "sRGB"),
            ("8-bit", "HDR (HLG)", "8-bit YUV420", "HDR"),
            ("10-bit", "sRGB", "10-bit YUV420", "sRGB"),
            ("10-bit", "HDR PQ", "10-bit YUV420", "HDR PQ"),
        ],
    )
    def test_avif_maps_depth_and_space_and_passes_the_crf(
        self, dirs, fake_core, depth, space, core_depth, core_space
    ) -> None:
        saved = m.EPSSaveImage().save(
            [_image()],
            filename_prefix="x",
            format="avif",
            bit_depth=depth,
            input_color_space=space,
            avif_crf=30,
        )["ui"]["images"][0]
        assert saved["filename"] == "x_00001_.avif"
        call = fake_core.named("_save_avif")[0]
        assert (call["bit_depth"], call["colorspace"], call["crf"]) == (
            core_depth,
            core_space,
            30,
        )
        # still image: a batch of exactly ONE frame, the default animation args
        assert call["shape"] == (1, 3, 4, 3)
        assert (call["fps"], call["loop_count"]) == (1.0, None)
        assert call["path"] == str(_saved_path(dirs, saved))
        # AVIF has no separate encode/inject calls: core writes its own file
        assert [name for name, _ in fake_core.calls] == ["_save_avif"]

    def test_the_default_crf_is_18(self, dirs, fake_core) -> None:
        m.EPSSaveImage().save([_image()], filename_prefix="x", format="avif")
        assert fake_core.named("_save_avif")[0]["crf"] == 18

    def test_a_batch_is_one_file_per_image_with_counted_names(self, dirs, fake_core) -> None:
        images = [_image(seed=0.0), _image(seed=0.1), _image(seed=0.2)]
        for fmt, ext in (("png", "png"), ("exr", "exr"), ("avif", "avif")):
            fake_core.calls.clear()
            kwargs = {"bit_depth": "16-bit"} if fmt == "png" else {}
            result = m.EPSSaveImage().save(
                images, filename_prefix=f"set/{fmt}_%batch_num%", format=fmt, **kwargs
            )
            names = [e["filename"] for e in result["ui"]["images"]]
            assert names == [f"{fmt}_{i}_{i + 1:05}_.{ext}" for i in range(3)], names
            entries = result["ui"]["images"]
            assert all(e["subfolder"] == "set" and e["type"] == "output" for e in entries)
            assert result["result"] == (images,)
            for entry in result["ui"]["images"]:
                assert _saved_path(dirs, entry).is_file()
        # avif: three separate one-frame calls, not one animated batch
        assert len(fake_core.named("_save_avif")) == 3
        assert {c["shape"] for c in fake_core.named("_save_avif")} == {(1, 3, 4, 3)}

    def test_each_image_of_a_png16_batch_is_encoded_separately(self, dirs, fake_core) -> None:
        m.EPSSaveImage().save(
            [_image(), _image(5, 2)], filename_prefix="x", format="png", bit_depth="16-bit"
        )
        assert [c["shape"] for c in fake_core.named("_encode_image")] == [(3, 4, 3), (2, 5, 3)]

    def test_hand_built_prompt_values_that_are_list_wrapped_still_route(
        self, dirs, fake_core
    ) -> None:
        saved = m.EPSSaveImage().save(
            [_image()],
            filename_prefix="x",
            format=["exr"],
            bit_depth=["auto"],
            input_color_space=["HDR (HLG)"],
        )["ui"]["images"][0]
        assert saved["filename"].endswith(".exr")
        assert fake_core.named("_encode_image")[0]["colorspace"] == "HDR"


class TestEnforcementAtSaveTime:
    """VALIDATE_INPUTS refuses a typed bad choice at queue time; save() is
    the enforcement for a wired value or a hand-built prompt -- and it fails
    BEFORE any directory is asked for, any bake happens or any file exists."""

    def test_an_invalid_combination_does_no_work(self, dirs, fake_core) -> None:
        workflow, prompt, info = _run_chunks()
        extra = {"workflow": workflow}
        before = copy.deepcopy((workflow, prompt, extra))
        with pytest.raises(m.FormatError, match=r"'16-bit'.*valid for avif: auto, 8-bit, 10-bit"):
            m.EPSSaveImage().save(
                [_image()],
                filename_prefix="x",
                run_info=info,
                prompt=prompt,
                extra_pnginfo=extra,
                format="avif",
                bit_depth="16-bit",
            )
        assert dirs.calls == []  # no folder_paths getter was even asked
        assert list(dirs.out.iterdir()) == [] and list(dirs.temp.iterdir()) == []
        assert fake_core.calls == []
        assert (workflow, prompt, extra) == before

    @pytest.mark.parametrize(
        "fmt,kwargs", [("exr", {}), ("avif", {}), ("png", {"bit_depth": "16-bit"})]
    )
    def test_a_missing_encoder_is_refused_with_nothing_written(
        self, dirs, no_core, fmt, kwargs
    ) -> None:
        with pytest.raises(m.FormatError, match="your ComfyUI doesn't have this encoder yet"):
            m.EPSSaveImage().save([_image()], filename_prefix="x", format=fmt, **kwargs)
        assert dirs.calls == [] and list(dirs.out.iterdir()) == []

    def test_there_is_never_a_silent_fallback_to_another_format(self, dirs, no_core) -> None:
        with pytest.raises(m.FormatError):
            m.EPSSaveImage().save([_image()], filename_prefix="x", format="exr")
        assert list(dirs.out.rglob("*")) == []  # not even a png

    @pytest.mark.parametrize("crf", [0, 64, 18.5, "abc", True])
    def test_a_bad_wired_crf_is_refused_for_avif(self, dirs, fake_core, crf) -> None:
        with pytest.raises(m.FormatError, match="avif_crf"):
            m.EPSSaveImage().save(
                [_image()], filename_prefix="x", format="avif", avif_crf=crf
            )
        assert fake_core.calls == [] and list(dirs.out.iterdir()) == []

    def test_a_core_failure_keeps_core_s_message_and_names_the_format(
        self, dirs, fake_core
    ) -> None:
        fake_core.fail_encode = ValueError("No exr/32-bit float encoder for 2-channel images")
        with pytest.raises(RuntimeError) as caught:
            m.EPSSaveImage().save([_image()], filename_prefix="x", format="exr")
        assert "couldn't save exr (32-bit float, sRGB)" in str(caught.value)
        assert "No exr/32-bit float encoder" in str(caught.value)
        assert isinstance(caught.value.__cause__, ValueError)
        assert list(dirs.out.iterdir()) == []  # nothing half-written

    def test_a_half_written_avif_is_removed(self, dirs, fake_core) -> None:
        fake_core.fail_avif = ValueError("AVIF saving supports 1-channel and 3-channel images")
        with pytest.raises(RuntimeError, match="1-channel and 3-channel"):
            m.EPSSaveImage().save([_image()], filename_prefix="x", format="avif")
        assert list(dirs.out.iterdir()) == []  # the fake wrote a stub before failing


# -------------------------------------------------------------- provenance


def _pin_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        m,
        "capture_pins",
        lambda prompt, info: {"7": m.PinnedWidget("LoraLibraryNotebook", "pinned", "PIN-JSON")},
    )


def _workflow_with_a_notebook() -> tuple[dict, dict, str]:
    workflow, prompt, info = _run_chunks()
    workflow["nodes"].append(
        {
            "id": 7,
            "type": "LoraLibraryNotebook",
            "widgets_values": ["loras.md", "a", "", "{}", "\n"],
        }
    )
    prompt["7"] = {"class_type": "LoraLibraryNotebook", "inputs": {"pinned": ""}}
    return workflow, prompt, info


FORMAT_CASES = [
    pytest.param({"format": "png", "bit_depth": "16-bit"}, id="png16"),
    pytest.param({"format": "exr"}, id="exr"),
    pytest.param({"format": "avif"}, id="avif"),
]


def _metadata_seen_by_core(core: FakeCore, kwargs: dict) -> tuple[Any, dict]:
    """``(prompt, extra)`` as core's metadata path received them, whichever
    format: the inject helper for png/exr, the ``metadata`` dict for avif."""
    if kwargs["format"] == "avif":
        metadata = core.named("_save_avif")[0]["metadata"]
        extra = {k: v for k, v in metadata.items() if k != "prompt"}
        return metadata.get("prompt"), extra
    name = "inject_png_metadata" if kwargs["format"] == "png" else "inject_exr_metadata"
    call = core.named(name)[0]
    return call["prompt"], call["extra"]


class TestProvenanceReachesEveryFormat:
    @pytest.mark.parametrize("kwargs", FORMAT_CASES)
    def test_the_baked_prompt_workflow_and_eps_run_reach_core(
        self, dirs, fake_core, kwargs
    ) -> None:
        workflow, prompt, info = _run_chunks()
        m.EPSSaveImage().save(
            [_image()],
            filename_prefix="shoot/Portrait_m2_i1_t3",
            run_info=info,
            prompt=prompt,
            extra_pnginfo={"workflow": workflow},
            **kwargs,
        )
        got_prompt, got_extra = _metadata_seen_by_core(fake_core, kwargs)
        # the BAKED objects: solo_run set in both chunks
        assert got_prompt["5"]["inputs"]["solo_run"] == "m2_i1_t3"
        assert got_extra["workflow"]["nodes"][0]["widgets_values"] == [
            "shoot",
            "multiply",
            "multiply",
            "m2_i1_t3",
        ]
        record = got_extra["eps_run"]
        assert record["token"] == "m2_i1_t3" and record["run"] == 4 and record["total"] == 12
        assert record["baked"] is True and record["pinned"] == [] and record["format"] == 1
        assert set(got_extra) == {"workflow", "eps_run"}

    @pytest.mark.parametrize("kwargs", FORMAT_CASES)
    def test_pins_are_baked_and_listed(self, dirs, fake_core, monkeypatch, kwargs) -> None:
        _pin_capture(monkeypatch)
        workflow, prompt, info = _workflow_with_a_notebook()
        m.EPSSaveImage().save(
            [_image()],
            filename_prefix="x",
            run_info=info,
            prompt=prompt,
            extra_pnginfo={"workflow": workflow},
            **kwargs,
        )
        got_prompt, got_extra = _metadata_seen_by_core(fake_core, kwargs)
        notebook = next(n for n in got_extra["workflow"]["nodes"] if n["id"] == 7)
        assert notebook["widgets_values"][m.widget_index(
            m._pinnable_class("LoraLibraryNotebook"), "pinned"
        )] == "PIN-JSON"
        assert got_prompt["7"]["inputs"]["pinned"] == "PIN-JSON"
        assert got_extra["eps_run"]["pinned"] == ["7"]

    @pytest.mark.parametrize("kwargs", FORMAT_CASES)
    def test_unwired_run_info_writes_the_standard_chunks_only(
        self, dirs, fake_core, kwargs
    ) -> None:
        workflow, prompt, _info = _run_chunks()
        m.EPSSaveImage().save(
            [_image()],
            filename_prefix="plain",
            prompt=prompt,
            extra_pnginfo={"workflow": workflow},
            **kwargs,
        )
        got_prompt, got_extra = _metadata_seen_by_core(fake_core, kwargs)
        assert got_prompt == prompt
        assert got_extra == {"workflow": workflow}  # no eps_run, nothing baked

    @pytest.mark.parametrize("kwargs", FORMAT_CASES)
    def test_extra_keys_all_travel_and_non_string_keys_are_stringified(
        self, dirs, fake_core, kwargs
    ) -> None:
        m.EPSSaveImage().save(
            [_image()],
            filename_prefix="x",
            prompt={"1": {}},
            extra_pnginfo={"workflow": {"nodes": []}, "parameters": "p", 7: {"k": 1}},
            **kwargs,
        )
        _prompt, got_extra = _metadata_seen_by_core(fake_core, kwargs)
        assert set(got_extra) == {"workflow", "parameters", "7"}

    def test_avif_metadata_puts_the_prompt_first_like_core_writes_it(
        self, dirs, fake_core
    ) -> None:
        workflow, prompt, info = _run_chunks()
        m.EPSSaveImage().save(
            [_image()],
            filename_prefix="x",
            run_info=info,
            prompt=prompt,
            extra_pnginfo={"workflow": workflow},
            format="avif",
        )
        metadata = fake_core.named("_save_avif")[0]["metadata"]
        assert list(metadata) == ["prompt", "workflow", "eps_run"]

    @pytest.mark.parametrize("kwargs", FORMAT_CASES)
    def test_the_texts_core_will_write_equal_the_texts_the_png_path_writes(
        self, dirs, fake_core, tmp_path, kwargs
    ) -> None:
        # Core JSON-encodes each value with json.dumps; so does the PIL path.
        # Feeding core the same OBJECTS therefore makes every format's text
        # identical to the default PNG's chunks.
        workflow, prompt, info = _run_chunks()
        m.EPSSaveImage().save(
            [_image()],
            filename_prefix="ref",
            run_info=info,
            prompt=copy.deepcopy(prompt),
            extra_pnginfo={"workflow": copy.deepcopy(workflow)},
        )
        png = Image.open(dirs.out / "ref_00001_.png")
        m.EPSSaveImage().save(
            [_image()],
            filename_prefix="new",
            run_info=info,
            prompt=copy.deepcopy(prompt),
            extra_pnginfo={"workflow": copy.deepcopy(workflow)},
            **kwargs,
        )
        got_prompt, got_extra = _metadata_seen_by_core(fake_core, kwargs)
        assert json.dumps(got_prompt) == png.text["prompt"]
        assert json.dumps(got_extra["workflow"]) == png.text["workflow"]
        assert json.dumps(got_extra["eps_run"]) == png.text["eps_run"]

    @pytest.mark.parametrize("kwargs", FORMAT_CASES)
    def test_the_shared_hidden_objects_come_out_untouched(
        self, dirs, fake_core, monkeypatch, kwargs
    ) -> None:
        # The v0.80.0 bake-undo contract: the hidden extra_pnginfo / prompt
        # are shared across a queue's mapped save() calls.
        _pin_capture(monkeypatch)
        workflow, prompt, info = _workflow_with_a_notebook()
        extra = {"workflow": workflow}
        before = copy.deepcopy((workflow, prompt, extra))
        m.EPSSaveImage().save(
            [_image(), _image()],
            filename_prefix="x",
            run_info=info,
            prompt=prompt,
            extra_pnginfo=extra,
            **kwargs,
        )
        assert (workflow, prompt, extra) == before
        assert "eps_run" not in extra  # the record never leaks into the caller's dict

    @pytest.mark.parametrize("kwargs", FORMAT_CASES)
    def test_they_are_restored_even_when_core_fails_mid_save(
        self, dirs, fake_core, kwargs
    ) -> None:
        fake_core.fail_encode = ValueError("boom")
        fake_core.fail_avif = ValueError("boom")
        workflow, prompt, info = _run_chunks()
        extra = {"workflow": workflow}
        before = copy.deepcopy((workflow, prompt, extra))
        with pytest.raises(RuntimeError, match="boom"):
            m.EPSSaveImage().save(
                [_image()],
                filename_prefix="x",
                run_info=info,
                prompt=prompt,
                extra_pnginfo=extra,
                **kwargs,
            )
        assert (workflow, prompt, extra) == before

    @pytest.mark.parametrize("kwargs", FORMAT_CASES)
    def test_disable_metadata_is_honoured_like_core(
        self, dirs, fake_core, monkeypatch, kwargs
    ) -> None:
        cli_args = types.ModuleType("comfy.cli_args")
        cli_args.args = types.SimpleNamespace(disable_metadata=True)
        monkeypatch.setitem(sys.modules, "comfy", types.ModuleType("comfy"))
        monkeypatch.setitem(sys.modules, "comfy.cli_args", cli_args)
        workflow, prompt, info = _run_chunks()
        m.EPSSaveImage().save(
            [_image()],
            filename_prefix="quiet",
            run_info=info,
            prompt=prompt,
            extra_pnginfo={"workflow": workflow},
            **kwargs,
        )
        # same flag core's own saver obeys: no inject helper is called at all
        # (so no chromaticities either), and AVIF gets no metadata dict
        assert fake_core.named("inject_png_metadata") == []
        assert fake_core.named("inject_exr_metadata") == []
        if kwargs["format"] == "avif":
            assert fake_core.named("_save_avif")[0]["metadata"] is None
        else:
            assert len(fake_core.named("_encode_image")) == 1  # still encoded


# ------------------------------------------------------------ preview wins

TEMP_NAME = re.compile(r"^(?P<base>.+)_temp_[a-z]{5}_00001_\.png$")


class TestPreviewWins:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"format": "exr"},
            {"format": "avif", "avif_crf": 5},
            {"format": "png", "bit_depth": "16-bit"},
            {"format": "exr", "input_color_space": "HDR (HLG)"},
            {"format": "tiff"},  # not even a format: a preview never reads it
            {"format": "avif", "bit_depth": "16-bit"},  # an invalid combination
            {"format": "avif", "avif_crf": "garbage"},
        ],
    )
    def test_a_preview_is_always_the_plain_temp_png(
        self, dirs, no_core, monkeypatch, kwargs
    ) -> None:
        # no_core: a preview must not even NEED an encoder
        seen: list[dict] = []
        original = Image.Image.save

        def spy(self, fp, *args, **kw):
            seen.append(dict(kw))
            return original(self, fp, *args, **kw)

        monkeypatch.setattr(Image.Image, "save", spy)
        saved = m.EPSSaveImage().save(
            [_image()], filename_prefix="shoot/look", preview_only=True, **kwargs
        )["ui"]["images"][0]
        assert saved["type"] == "temp" and saved["subfolder"] == "shoot"
        match = TEMP_NAME.match(saved["filename"])
        assert match and match["base"] == "look"
        assert _saved_path(dirs, saved).is_file()
        assert dirs.calls == ["temp"]  # the output folder was never asked for
        assert list(dirs.out.iterdir()) == []
        assert seen[0]["compress_level"] == 1  # core PreviewImage's level
        with Image.open(_saved_path(dirs, saved)) as png:
            assert png.format == "PNG" and png.mode == "RGB"  # 8-bit

    def test_core_is_never_called_in_preview_even_when_present(self, dirs, fake_core) -> None:
        m.EPSSaveImage().save(
            [_image()], filename_prefix="x", preview_only=True, format="exr", bit_depth="auto"
        )
        m.EPSSaveImage().save([_image()], filename_prefix="x", preview_only=[True], format="avif")
        assert fake_core.calls == []

    def test_provenance_still_bakes_into_the_preview_png(self, dirs, no_core) -> None:
        workflow, prompt, info = _run_chunks()
        saved = m.EPSSaveImage().save(
            [_image()],
            filename_prefix="shoot/Portrait_m2_i1_t3",
            run_info=info,
            prompt=prompt,
            extra_pnginfo={"workflow": workflow},
            preview_only=True,
            format="exr",
        )["ui"]["images"][0]
        with Image.open(_saved_path(dirs, saved)) as png:
            assert json.loads(png.text["prompt"])["5"]["inputs"]["solo_run"] == "m2_i1_t3"
            assert json.loads(png.text["eps_run"])["baked"] is True

    def test_flipping_back_to_save_uses_the_chosen_format_again(self, dirs, fake_core) -> None:
        node = m.EPSSaveImage()
        node.save([_image()], filename_prefix="flip", preview_only=True, format="exr")
        saved = node.save(
            [_image()], filename_prefix="flip", preview_only=False, format="exr"
        )["ui"]["images"][0]
        assert saved["filename"] == "flip_00001_.exr" and saved["type"] == "output"
        assert len(fake_core.named("_encode_image")) == 1


# ------------------------------------------------------------- file names


class TestFileNames:
    @pytest.mark.parametrize("fmt,ext", [("png", "png"), ("exr", "exr"), ("avif", "avif")])
    def test_eps_naming_with_the_trailing_underscore(self, dirs, fake_core, fmt, ext) -> None:
        kwargs = {"bit_depth": "16-bit"} if fmt == "png" else {}
        saved = m.EPSSaveImage().save(
            [_image()], filename_prefix="Portrait_m2_i1_t3", format=fmt, **kwargs
        )["ui"]["images"][0]
        assert saved["filename"] == f"Portrait_m2_i1_t3_00001_.{ext}"
        assert re.fullmatch(r".+_\d{5}_\.(png|exr|avif)", saved["filename"])
