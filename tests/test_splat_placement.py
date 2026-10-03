"""Tests for eps_image.nodes_splat_placement (FORMAT.md §6.19, `EPSSplatPlacement`).

The frame algebra is checked against an independent statement of what the
3D viewer does (world = T + R S (F p), Render Splat reads world w as F w),
not against the implementation's own formula. The end-to-end proof --
Render Splat after this node matching the browser viewer's own capture of
a fitted splat -- was done on the rig (FORMAT §6.19, 2026-10-02).
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from eps_image import nodes_splat_placement as sp
from eps_image.nodes_splat_placement import (
    EPSSplatPlacement,
    is_identity,
    placement_from_info,
    splat_frame_placement,
)

torch = pytest.importorskip("torch")


class FakeSplat:
    """The shape core's comfy_api SPLAT has: positions/scales/rotations/
    opacities/sh tensors + optional counts, constructed positionally."""

    def __init__(self, positions, scales, rotations, opacities, sh, counts=None):
        self.positions = positions
        self.scales = scales
        self.rotations = rotations
        self.opacities = opacities
        self.sh = sh
        self.counts = counts


def _random_splat(n=40, batch=1, seed=0):
    g = torch.Generator().manual_seed(seed)
    q = torch.randn(batch, n, 4, generator=g)
    return FakeSplat(
        torch.randn(batch, n, 3, generator=g),
        torch.rand(batch, n, 3, generator=g) * 0.1 + 0.01,
        q / q.norm(dim=-1, keepdim=True),
        torch.rand(batch, n, 1, generator=g),
        torch.rand(batch, n, 1, 3, generator=g),
        counts=torch.tensor([n] * batch),
    )


def _info(pos=(0.0, 0.0, 0.0), axis=(0.0, 1.0, 0.0), degrees=0.0, scale=(1.0, 1.0, 1.0)):
    a = torch.tensor(axis, dtype=torch.float64)
    a = a / a.norm()
    half = math.radians(degrees) / 2
    v = a * math.sin(half)
    return [{
        "position": dict(zip("xyz", pos, strict=True)),
        "quaternion": {"x": float(v[0]), "y": float(v[1]), "z": float(v[2]), "w": math.cos(half)},
        "scale": dict(zip("xyz", scale, strict=True)),
    }]


F = torch.diag(torch.tensor([1.0, -1.0, -1.0]))


def _viewer_model(info_entry):
    """What the viewer does, stated independently: world = T + R S (F p)."""
    q = info_entry["quaternion"]
    R = sp._quat_to_mat(torch.tensor([q["w"], q["x"], q["y"], q["z"]], dtype=torch.float32))
    S = torch.diag(torch.tensor([info_entry["scale"][k] for k in "xyz"], dtype=torch.float32))
    T = torch.tensor([info_entry["position"][k] for k in "xyz"], dtype=torch.float32)
    return R, S, T


def _cov(rotations, scales):
    R = sp._quat_to_mat(rotations)
    return (R * scales.square()[..., None, :]) @ R.transpose(-1, -2)


# ------------------------------------------------------------ pure helpers


class TestPlacementFromInfo:
    def test_list_takes_the_first_model(self):
        p = placement_from_info(_info(pos=(1, 2, 3)) + _info(pos=(9, 9, 9)))
        assert p["position"] == (1.0, 2.0, 3.0)

    def test_bare_dict_is_accepted(self):
        assert placement_from_info(_info(pos=(1, 0, 0))[0])["position"] == (1.0, 0.0, 0.0)

    @pytest.mark.parametrize("value", [None, [], "", 5, [None], {"nope": 1}])
    def test_nothing_usable_is_none_or_identity(self, value):
        assert is_identity(placement_from_info(value))

    def test_garbled_fields_fall_back_to_identity_parts(self):
        p = placement_from_info([{"position": {"x": "bad", "y": float("nan")}, "scale": None,
                                  "quaternion": {"x": 0, "y": 0, "z": 0, "w": 0}}])
        assert p["position"] == (0.0, 0.0, 0.0)
        assert p["scale"] == (1.0, 1.0, 1.0)
        assert p["quaternion"] == (1.0, 0.0, 0.0, 0.0)

    def test_quaternion_is_normalised_and_wxyz(self):
        p = placement_from_info([{"quaternion": {"x": 0, "y": 0, "z": 2, "w": 2}}])
        w, x, y, z = p["quaternion"]
        assert (x, y) == (0.0, 0.0)
        assert w == pytest.approx(math.sqrt(0.5)) and z == pytest.approx(math.sqrt(0.5))


class TestIdentityAndFrame:
    def test_identity_detected(self):
        assert is_identity(placement_from_info(_info()))

    def test_negated_identity_quaternion_is_identity(self):
        assert is_identity(placement_from_info([{"quaternion": {"x": 0, "y": 0, "z": 0, "w": -1}}]))

    @pytest.mark.parametrize("kw", [{"pos": (0.1, 0, 0)}, {"degrees": 5}, {"scale": (2, 2, 2)}])
    def test_any_component_breaks_identity(self, kw):
        assert not is_identity(placement_from_info(_info(**kw)))

    def test_splat_frame_flips_y_and_z(self):
        t, q, s = splat_frame_placement({
            "position": (1.0, 2.0, 3.0),
            "quaternion": (0.5, 0.1, 0.2, 0.3),
            "scale": (4.0, 5.0, 6.0),
        })
        assert t == (1.0, -2.0, -3.0)
        assert q == (0.5, 0.1, -0.2, -0.3)
        assert s == (4.0, 5.0, 6.0)


# ------------------------------------------------------------ the transform


@pytest.mark.parametrize(
    "info",
    [
        _info(pos=(1.5, -2.0, 0.25), axis=(0.3, 0.8, -0.5), degrees=40, scale=(3, 3, 3)),
        # the rig's real Fit-to-viewer placement
        _info(pos=(-37.05, 11.02, 1.95), scale=(15.29, 15.29, 15.29)),
        # non-uniform scale
        _info(pos=(0.5, 0.5, -1.0), axis=(1, 0, 0), degrees=90, scale=(1.5, 0.5, 2.0)),
    ],
)
def test_positions_and_covariances_match_the_viewer(info):
    splat = _random_splat()
    out = EPSSplatPlacement().place(splat, info)[0]
    R, S, T = _viewer_model(info[0])
    # Positions: the viewer shows splat point p at world T + R S F p; Render
    # Splat reads world w as splat point F w.
    expect = ((splat.positions[0] @ F.T) @ S @ R.T + T) @ F.T
    assert torch.allclose(out.positions[0], expect, atol=1e-4)
    # Covariances follow the same linear part, in the splat frame.
    M = F @ R @ S @ F
    want = M @ _cov(splat.rotations[0], splat.scales[0]) @ M.T
    got = _cov(out.rotations[0], out.scales[0])
    assert torch.allclose(got, want, atol=1e-5)


def test_untouched_fields_carry_over():
    splat = _random_splat()
    out = EPSSplatPlacement().place(splat, _info(scale=(2, 2, 2)))[0]
    assert isinstance(out, FakeSplat)
    assert out.opacities is splat.opacities
    assert out.sh is splat.sh
    assert out.counts is splat.counts


def test_rotations_stay_unit_quaternions():
    info = _info(axis=(1, 1, 0), degrees=70, scale=(1, 2, 3))
    out = EPSSplatPlacement().place(_random_splat(), info)[0]
    norms = out.rotations.norm(dim=-1)
    assert torch.allclose(norms, torch.ones_like(norms), atol=1e-5)


def test_batch_dimension_is_handled():
    splat = _random_splat(batch=2)
    out = EPSSplatPlacement().place(splat, _info(pos=(1, 0, 0), scale=(2, 2, 2)))[0]
    assert out.positions.shape == splat.positions.shape
    expect = splat.positions * 2 + torch.tensor([1.0, 0.0, 0.0])
    assert torch.allclose(out.positions, expect, atol=1e-5)


@pytest.mark.parametrize("info", [None, [], _info()])
def test_no_placement_passes_the_same_object_through(info):
    splat = _random_splat()
    assert EPSSplatPlacement().place(splat, info)[0] is splat


def test_omitted_input_passes_through():
    splat = _random_splat()
    assert EPSSplatPlacement().place(splat)[0] is splat


# ------------------------------------------------------------ node contract


def test_schema():
    spec = EPSSplatPlacement.INPUT_TYPES()
    assert spec["required"]["splat"][0] == "SPLAT"
    assert spec["optional"]["model_3d_info"][0] == "LOAD3D_MODEL_INFO"
    assert EPSSplatPlacement.RETURN_TYPES == ("SPLAT",)
    assert EPSSplatPlacement.CATEGORY == "EPSNodes/Utilities"
    assert EPSSplatPlacement.FUNCTION == "place"


def test_registered_in_the_pack():
    init = (Path(__file__).resolve().parent.parent / "__init__.py").read_text(encoding="utf-8")
    assert '("eps_image.nodes_splat_placement", "EPSSplatPlacement", "EPS Splat Placement")' in init


def test_module_import_does_not_import_torch_at_top_level():
    src = Path(sp.__file__).read_text(encoding="utf-8")
    top = src.split("torch helpers")[0]
    assert "import torch" not in top
