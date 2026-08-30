"""Which installed pack does this file (or this call) belong to?

Every lens in this family reports per PACK — routes, egress destinations and
scan findings are only actionable when you know which folder in
``custom_nodes/`` they came from. That mapping is this module's whole job,
and it is deliberately pure: it takes the roots as arguments and touches
neither ``folder_paths`` nor ComfyUI, so the tests drive it with plain
strings (this pack's ``context.py`` seam, same reasoning).

"Pack" means the immediate child of a ``custom_nodes`` root — the folder a
user installs, updates and deletes as one unit — never the deepest package.
A finding in ``custom_nodes/foo/js/deep/mod.py`` belongs to ``foo``, because
``foo`` is what they would uninstall.
"""

from __future__ import annotations

import os
import sys

#: Frames to walk before giving up attributing a call. Deep enough to climb
#: out of a client library's own internals (aiohttp's request path is ~6),
#: shallow enough to stay cheap on a call that may run mid-render.
_MAX_FRAMES = 25


def normalize_root(root: str) -> str:
    """A root path in the one form :func:`pack_of_path` compares against."""
    return os.path.normcase(os.path.realpath(root))


def pack_of_path(path: str, roots) -> str | None:
    """Name of the pack owning *path*, or ``None`` when it is outside every
    root in *roots* (ComfyUI core, site-packages, the stdlib).

    *roots* are ``custom_nodes`` directories, already normalized by
    :func:`normalize_root`. Comparison is done on ``realpath`` +
    ``normcase`` so a symlinked pack (this repo is symlinked into the test
    rig exactly that way) and Windows' case-insensitive paths both attribute
    correctly rather than silently reporting ``None``.
    """
    if not path:
        return None
    for candidate in _path_forms(path):
        for root in roots:
            try:
                relative = os.path.relpath(candidate, root)
            except ValueError:  # different drive on Windows
                continue
            if relative.startswith(os.pardir) or os.path.isabs(relative):
                continue
            first = relative.split(os.sep, 1)[0]
            if first and first != os.curdir:
                return first
    return None


def _path_forms(path: str) -> list[str]:
    """*path* as both its literal absolute form and its symlink-resolved
    one, deduped — BOTH are needed, in this order.

    A pack symlinked INTO ``custom_nodes`` (this repo is installed exactly
    that way on the owner's rig) reports module files under the symlinked
    location, which ``realpath`` rewrites to somewhere outside every root —
    attributing the whole pack to ``None``. Resolving is still required for
    the mirror case, a real directory reached through a symlinked ROOT. So
    the literal form is tried first (it is the one ComfyUI actually
    imported through) and the resolved form second.
    """
    forms: list[str] = []
    for convert in (os.path.abspath, os.path.realpath):
        try:
            value = os.path.normcase(convert(path))
        except (OSError, ValueError):
            continue
        if value not in forms:
            forms.append(value)
    return forms


def pack_of_frame(roots, skip=0) -> str | None:
    """Name of the pack whose code is calling us right now, or ``None``.

    Walks up the live stack and returns the FIRST frame that belongs to a
    pack — the innermost custom-node file involved, which for an outbound
    HTTP call is the pack that asked for it rather than the client library
    that performed it. Uses ``sys._getframe`` and a bounded climb instead of
    ``inspect.stack()``, which builds a full FrameInfo (source lines and
    context reads) for every level and is far too expensive to sit in front
    of another pack's network call.
    """
    try:
        frame = sys._getframe(skip + 1)
    except ValueError:
        return None
    for _ in range(_MAX_FRAMES):
        if frame is None:
            return None
        pack = pack_of_path(frame.f_code.co_filename, roots)
        if pack is not None:
            return pack
        frame = frame.f_back
    return None
