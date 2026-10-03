"""``EPSSplatPlacement`` (FORMAT.md §6.19, display: "EPS Splat Placement").

Moves, rotates and scales a gaussian splat exactly the way ComfyUI's 3D
viewer is showing it, so the core **Render Splat** node draws the same
picture as the **Save Splat** / **Preview Splat** / **Load 3D** preview.

Why it exists (owner report 2026-10-02): wiring Save Splat's
``camera_info`` into Render Splat rendered the splat as a dot in the middle
of the frame. The viewer never moves the camera to fit a splat -- its "Fit
to viewer" button (and the move/rotate/scale gizmo) transforms the MODEL:
``SceneModelManager.fitToViewer`` scales it to a 20-unit box
(``SplatModelAdapter.capabilities.fitTargetSize``) and re-centres it, then
frames the camera on that enlarged model. Save Splat reports both halves --
``camera_info`` AND ``model_3d_info`` (the model's position, quaternion and
scale) -- but core Render Splat only reads the camera, so it rendered the
raw-sized splat through a camera framed for one ~20x larger. Applying
``model_3d_info`` to the splat first makes the two agree again.

Coordinates. ``model_3d_info`` is three.js world space (right-handed, Y-up).
The viewer puts the splat inside a group whose transform IS that info, and
turns the splat itself 180 degrees about X first
(``SplatModelAdapter``: ``splatMesh.quaternion.set(1, 0, 0, 0)``), because
splat files are 3DGS-convention (Y-down, Z-forward). Render Splat maps the
camera with the same flip (``_camera_basis``: world -> splat is
``(x, y, z) -> (x, -y, -z)``). With F = diag(1, -1, -1), the viewer shows a
splat point ``p`` at ``world = T + R S (F p)``, and Render Splat reads world
point ``w`` as splat point ``F w``, so the splat Render Splat needs is::

    p' = F T + (F R F) S p

``F R F`` is R conjugated by the 180-degree X turn, which as a quaternion
is simply ``(w, x, -y, -z)``. Gaussian scales and orientations follow the
same linear map (uniform scale: multiply scales, compose quaternions;
non-uniform: transform each covariance and re-decompose, as core's own
Transform Splat does). Spherical-harmonic colour is not rotated -- core
Transform Splat makes the same choice; only the view-dependent (degree >= 1)
part of a rotated splat's colour is affected, and only slightly.

No placement (``model_3d_info`` unwired, empty, or the identity) passes the
splat through untouched, so the node is always safe to leave in the chain.
"""

from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger("eps_image")

#: The core type names this node speaks (comfy_api ``IO.Splat`` /
#: ``IO.Load3DModelInfo``). Strings, so importing this module never imports
#: torch or ComfyUI.
SPLAT_TYPE = "SPLAT"
MODEL_INFO_TYPE = "LOAD3D_MODEL_INFO"

#: Treat |a - b| below this as equal when deciding identity / uniform scale.
_EPS = 1e-9


def _vec(entry: Any, key: str, names: tuple[str, ...], default: float) -> tuple[float, ...]:
    raw = entry.get(key) if isinstance(entry, dict) else None
    if not isinstance(raw, dict):
        return tuple(default for _ in names)
    out = []
    for name in names:
        try:
            value = float(raw.get(name, default))
        except (TypeError, ValueError):
            value = default
        out.append(value if math.isfinite(value) else default)
    return tuple(out)


def placement_from_info(model_3d_info: Any) -> dict | None:
    """The first model's ``{position, quaternion(wxyz), scale}`` from a
    ``LOAD3D_MODEL_INFO`` value, or ``None`` when there is nothing to apply.

    Accepts the list core sends (one entry per model; a splat viewer holds
    one) or a bare dict. Missing/garbled fields fall back to the identity
    component; a zero-length quaternion is the identity rotation.
    """
    entry = model_3d_info
    if isinstance(entry, (list, tuple)):
        if not entry:
            return None
        if len(entry) > 1:
            logger.info(
                "EPSNodes: EPS Splat Placement: %d models in model_3d_info; using the first",
                len(entry),
            )
        entry = entry[0]
    if not isinstance(entry, dict):
        return None
    position = _vec(entry, "position", ("x", "y", "z"), 0.0)
    qx, qy, qz, qw = _vec(entry, "quaternion", ("x", "y", "z", "w"), 0.0)
    norm = math.sqrt(qw * qw + qx * qx + qy * qy + qz * qz)
    quat = (1.0, 0.0, 0.0, 0.0) if norm < _EPS else (qw / norm, qx / norm, qy / norm, qz / norm)
    scale = _vec(entry, "scale", ("x", "y", "z"), 1.0)
    return {"position": position, "quaternion": quat, "scale": scale}


def is_identity(placement: dict | None) -> bool:
    if placement is None:
        return True
    w, x, y, z = placement["quaternion"]
    return (
        all(abs(v) < _EPS for v in placement["position"])
        and all(abs(v - 1.0) < _EPS for v in placement["scale"])
        and abs(abs(w) - 1.0) < _EPS
        and abs(x) < _EPS and abs(y) < _EPS and abs(z) < _EPS
    )


def splat_frame_placement(placement: dict) -> tuple[tuple, tuple, tuple]:
    """Re-express a world-space (three.js, Y-up) placement in the splat's
    own frame (3DGS, Y-down): conjugate by F = diag(1, -1, -1). Returns
    ``(translation, quaternion_wxyz, scale)``. Pure, so the frame algebra
    is testable without torch."""
    tx, ty, tz = placement["position"]
    w, x, y, z = placement["quaternion"]
    return (tx, -ty, -tz), (w, x, -y, -z), tuple(placement["scale"])


# ----------------------------------------------------------- torch helpers


def _quat_to_mat(q):
    """(..., 4) wxyz unit quaternions -> (..., 3, 3) rotation matrices."""
    import torch

    w, x, y, z = q.unbind(-1)
    return torch.stack(
        [
            1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y),
            2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x),
            2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y),
        ],
        dim=-1,
    ).reshape((*q.shape[:-1], 3, 3))


def _quat_mul(a, b):
    """Hamilton product of (..., 4) wxyz quaternions."""
    import torch

    aw, ax, ay, az = a.unbind(-1)
    bw, bx, by, bz = b.unbind(-1)
    return torch.stack(
        [
            aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
        ],
        dim=-1,
    )


def _mat_to_quat(m):
    """(..., 3, 3) proper rotations -> (..., 4) wxyz unit quaternions
    (Shepperd's method: pick the largest diagonal term for stability)."""
    import torch

    m00, m11, m22 = m[..., 0, 0], m[..., 1, 1], m[..., 2, 2]
    m01, m02, m10 = m[..., 0, 1], m[..., 0, 2], m[..., 1, 0]
    m12, m20, m21 = m[..., 1, 2], m[..., 2, 0], m[..., 2, 1]
    d10, d02, d21 = m10 - m01, m02 - m20, m21 - m12
    s10, s02, s21 = m10 + m01, m02 + m20, m21 + m12
    trace = m00 + m11 + m22
    cands = torch.stack(
        [
            torch.stack([1 + trace, d21, d02, d10], -1),
            torch.stack([d21, 1 + m00 - m11 - m22, s10, s02], -1),
            torch.stack([d02, s10, 1 - m00 + m11 - m22, s21], -1),
            torch.stack([d10, s02, s21, 1 - m00 - m11 + m22], -1),
        ],
        dim=-2,
    )  # (..., 4 candidates, 4)
    pick = torch.stack([trace, m00, m11, m22], -1).argmax(-1)
    q = torch.gather(cands, -2, pick[..., None, None].expand(*pick.shape, 1, 4)).squeeze(-2)
    q = q / q.norm(dim=-1, keepdim=True).clamp_min(1e-12)
    return q * torch.where(q[..., :1] < 0, -1.0, 1.0)  # canonical w >= 0


def apply_placement(splat: Any, placement: dict) -> Any:
    """A new splat of the same class with *placement* (world-space, as the
    viewer reports it) baked in. See the module docstring for the algebra."""
    import torch

    pos = splat.positions
    dev, dt = pos.device, pos.dtype
    (tx, ty, tz), q_wxyz, (sx, sy, sz) = splat_frame_placement(placement)
    q = torch.tensor(q_wxyz, dtype=dt, device=dev)
    rot = _quat_to_mat(q)
    scale = torch.tensor([sx, sy, sz], dtype=dt, device=dev)
    linear = rot * scale[None, :]  # R' @ diag(S): scale in the model's own axes, then rotate
    t = torch.tensor([tx, ty, tz], dtype=dt, device=dev)
    positions = pos @ linear.T + t

    if abs(sx - sy) < _EPS and abs(sy - sz) < _EPS:
        scales = splat.scales * sx
        rotations = _quat_mul(q.expand_as(splat.rotations), splat.rotations)
        rotations = rotations / rotations.norm(dim=-1, keepdim=True).clamp_min(1e-12)
    else:  # non-uniform: Sigma' = A Sigma A^T, then re-decompose into axes + lengths
        flat_rot = _quat_to_mat(splat.rotations.reshape(-1, 4))
        s2 = splat.scales.reshape(-1, 3).square()
        cov = (flat_rot * s2[:, None, :]) @ flat_rot.transpose(-1, -2)
        cov = linear @ cov @ linear.T
        lam, axes = torch.linalg.eigh(cov)
        axes = axes * torch.where(torch.linalg.det(axes) < 0, -1.0, 1.0)[..., None, None]
        scales = lam.clamp_min(0).sqrt().reshape(splat.scales.shape)
        rotations = _mat_to_quat(axes).reshape(splat.rotations.shape)

    return type(splat)(
        positions, scales, rotations, splat.opacities, splat.sh,
        counts=getattr(splat, "counts", None),
    )


class EPSSplatPlacement:
    """Bake the 3D viewer's model placement into a splat (FORMAT.md §6.19)."""

    CATEGORY = "EPSNodes/Utilities"
    FUNCTION = "place"
    RETURN_TYPES = (SPLAT_TYPE,)
    RETURN_NAMES = ("splat",)
    DESCRIPTION = (
        "Moves, rotates and scales a gaussian splat exactly the way the 3D viewer shows it, so "
        "Render Splat draws the same picture as the preview. Wire Save Splat's (or Load 3D's) "
        "model_3d_info here, put this between Get Splat and Render Splat, and give Render Splat "
        "the same node's camera_info. Without it, a splat you fitted to the viewer renders as a "
        "dot: the viewer enlarges the model, not the camera."
    )

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "splat": (SPLAT_TYPE, {"tooltip": "The splat, from Get Splat."}),
            },
            "optional": {
                "model_3d_info": (
                    MODEL_INFO_TYPE,
                    {
                        "tooltip": (
                            "From the same Save Splat / Preview Splat / Load 3D node whose "
                            "camera_info feeds Render Splat. Unwired = pass the splat through."
                        )
                    },
                ),
            },
        }

    def place(self, splat: Any, model_3d_info: Any = None) -> tuple[Any]:
        placement = placement_from_info(model_3d_info)
        if is_identity(placement):
            return (splat,)
        return (apply_placement(splat, placement),)
