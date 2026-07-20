"""``EPSSwitcher`` (FORMAT.md §6.4, display: "EPS Switcher") — image toggle +
fan-out node.

Growing ``image_N`` optional sockets (unbounded, like the sibling pack's
video-input growth) feed a single ``IMAGE`` output declared
``OUTPUT_IS_LIST``: the ENABLED images, in ascending slot order, so N
enabled inputs make every ordinary downstream node run N times (ComfyUI's
own list-fan-out mechanics — see ``lora_library/nodes_notebook.py``
``read_entry`` / the sibling pack's ``PremiereIterateShots`` for the same
trick). No torch/ComfyUI import anywhere in this module (needed by neither
the flexible-input trick nor plain list-building), so it stays importable
in a bare test environment.

**Enabled-set mechanism (the piece FORMAT.md §6.4 leaves to the
implementer):** which ``image_N`` slots are "on" is frontend state (a
per-row toggle + header tri-state toggle-all, drawn by ``switcher.js``), but
the FILTERING must happen server-side so the emitted list is authoritative
regardless of what UI drove it (a raw ``/prompt`` POST from a script, an
API-only caller with no frontend at all, a future non-JS client). The
bridge is a plain ``toggles`` STRING widget: a JSON object
``{"image_2": false, ...}`` that the frontend keeps in lockstep with every
per-row toggle click (``switcher.js`` writes it on every toggle and prunes
keys for slots that no longer exist) and that ComfyUI serializes/transmits
exactly like any other STRING widget — no custom hidden-input machinery,
just the same "hide the widget, keep it a real serialized value" trick
FORMAT.md §7.2 already uses for the Prompt Notebook's ``file`` widget.
Design choice: a slot is enabled unless its key is present and explicitly
``false`` — so an entry the JSON never mentions (a slot connected by a
plain API caller who has never heard of this widget) defaults to enabled,
which is the least-surprising behavior for the "ComfyUI-only must work"
floor (bridge design ethos): wiring three images with no ``toggles`` value
at all should pass all three through, not silently drop them.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("eps_image")

#: Shared between ``_FlexibleOptionalImageInputs`` (INPUT_TYPES validation)
#: and ``_image_kwargs`` (execute-time collection), so both agree on what
#: counts as an image slot. Modeled on the sibling pack's
#: ``cprb/nodes_save.py`` ``_VIDEO_INPUT_PATTERN``.
_IMAGE_INPUT_PATTERN = re.compile(r"image_(\d+)")

#: Default ``toggles`` widget value: no overrides recorded yet, so every
#: connected slot is enabled (see the module docstring's default-enabled
#: rationale).
DEFAULT_TOGGLES = "{}"


class _FlexibleOptionalImageInputs(dict):
    """The ``optional`` half of INPUT_TYPES: accepts ANY ``image_N`` key.

    FORMAT.md §6.4's unbounded ``image_N`` needs ComfyUI's own input
    validation -- which checks ``input_name in class_inputs['optional']``
    (the ``in`` operator, i.e. ``__contains__``) before letting a workflow
    wire a given input on this node -- to say yes to ``image_5``,
    ``image_37``, etc. even though only ``image_1`` is ever actually stored
    in this dict. Directly modeled on the sibling comfyui-premiere-bridge
    pack's ``cprb/nodes_save.py`` ``_FlexibleOptionalVideoInputs`` (itself
    modeled on rgthree-comfy's ``FlexibleOptionalInputType`` trick,
    reimplemented locally -- this pack does not depend on rgthree):
    override ``__contains__`` (and, for safety, ``__getitem__`` in case
    something subscripts rather than uses ``in``/``.get``) to treat any key
    matching ``_IMAGE_INPUT_PATTERN`` as present with type ``("IMAGE",)``.
    Plain dict iteration/``.items()``/``.keys()`` is left untouched, so it
    still only yields whatever was actually inserted (``image_1``) -- which
    is what ``/object_info`` (and thus the frontend's default socket
    rendering) sees, giving the node exactly one visible socket out of the
    box; ``switcher.js`` grows the rest.
    """

    def __contains__(self, key: object) -> bool:
        if isinstance(key, str) and _IMAGE_INPUT_PATTERN.fullmatch(key):
            return True
        return super().__contains__(key)

    def __getitem__(self, key: str) -> Any:
        if super().__contains__(key):
            return super().__getitem__(key)
        if isinstance(key, str) and _IMAGE_INPUT_PATTERN.fullmatch(key):
            return ("IMAGE",)
        raise KeyError(key)


def _image_kwargs(kwargs: dict[str, Any]) -> list[tuple[int, Any]]:
    """``(index, image)`` for every present, non-``None`` ``image_N`` kwarg.

    Sorted by ``N`` ascending (FORMAT.md §6.4 "in slot order"), independent
    of *kwargs*' own iteration order. An unconnected slot is simply a key
    that's absent or ``None`` from *kwargs* (ComfyUI omits unconnected
    optional sockets on some call paths and passes ``None`` on others; both
    mean "not connected" -- same tolerance as the sibling pack's
    ``_video_kwargs``).
    """
    indexed: list[tuple[int, Any]] = []
    for key, value in kwargs.items():
        if value is None:
            continue
        match = _IMAGE_INPUT_PATTERN.fullmatch(key)
        if match:
            indexed.append((int(match.group(1)), value))
    indexed.sort(key=lambda pair: pair[0])
    return indexed


def _parse_toggles(toggles: str) -> dict[str, Any]:
    """Best-effort JSON object parse of the ``toggles`` widget value.

    Never raises: a malformed/foreign value (a hand-edited workflow, an API
    caller sending garbage) degrades to "no overrides recorded" -- i.e.
    every slot enabled -- rather than crashing the node, logging a warning
    so the cause is visible without being fatal.
    """
    if not toggles:
        return {}
    try:
        parsed = json.loads(toggles)
    except (TypeError, ValueError) as exc:
        logger.warning(
            "EPS Switcher: malformed `toggles` value (%s); treating every "
            "connected image as enabled",
            exc,
        )
        return {}
    if not isinstance(parsed, dict):
        logger.warning(
            "EPS Switcher: `toggles` was not a JSON object (got %r); treating "
            "every connected image as enabled",
            type(parsed).__name__,
        )
        return {}
    return parsed


class EPSSwitcher:
    """Any number of ``image_N`` inputs, each independently on/off, fanned
    into one ``IMAGE`` list output (FORMAT.md §6.4).

    v1 = simple filter (roadmap M1): disabled inputs are omitted from the
    output list, but their upstream nodes still execute -- true branch-skip
    (lazy evaluation) is tracked as M3 (``research/roadmap-eps-switcher.md``),
    not built here.

    Re-derives the enabled set from ``toggles`` + the connected ``image_N``
    kwargs on every execution -- there is no other state to go stale, so
    unlike the Prompt Notebook this node needs no ``IS_CHANGED`` override:
    ``toggles`` and every ``image_N`` are ordinary tracked inputs already
    covered by ComfyUI's own input-hash caching.
    """

    CATEGORY = "EPSNodes"
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("images",)
    OUTPUT_IS_LIST = (True,)
    FUNCTION = "execute"
    DESCRIPTION = (
        "Toggle any number of image inputs on/off; the enabled ones fan out in "
        "slot order (N enabled -> the rest of the workflow runs N times). "
        "Caveat: a scalar value (e.g. a seed) wired downstream repeats "
        "identically across all N runs -- use an explicit per-image list for "
        "per-image variation."
    )

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {},
            "optional": _FlexibleOptionalImageInputs(
                {
                    "image_1": ("IMAGE",),
                    # Serialized bridge to the frontend's per-row/header toggle
                    # UI (module docstring "Enabled-set mechanism"); switcher.js
                    # hides this widget's on-canvas row (`.hidden = true`, same
                    # trick FORMAT.md §7.2 uses for the Prompt Notebook's `file`
                    # widget) but keeps writing its value, so it still
                    # serializes with the workflow and still reaches execute()
                    # untouched for a plain API caller who never loads our JS.
                    #
                    # In `optional`, NOT `required`: ComfyUI's validate_inputs
                    # rejects a /prompt whose inputs omit any REQUIRED key
                    # BEFORE the node runs (there is no backend default-fill),
                    # which would make the documented "API caller who never
                    # heard of this widget" path (module docstring) unreachable.
                    # execute()'s own `toggles=DEFAULT_TOGGLES` default covers
                    # the omitted case; the frontend still serializes it.
                    "toggles": ("STRING", {"default": DEFAULT_TOGGLES, "multiline": False}),
                }
            ),
        }

    def execute(self, toggles: str = DEFAULT_TOGGLES, **kwargs: Any) -> tuple[list[Any]]:
        toggle_map = _parse_toggles(toggles)
        present = _image_kwargs(kwargs)

        # A slot is enabled unless its key is present and EXPLICITLY the boolean
        # `false` (matches switcher.js's `!== false` and this module's own
        # docstring). Plain truthiness would wrongly drop a slot whose value is
        # a non-bool falsy like null/0/"" from a hand-edited workflow or a
        # non-frontend API caller -- the frontend renders those as ON, so the
        # backend must too, or the fan-out count silently disagrees with the UI.
        enabled_images = [
            image
            for index, image in present
            if toggle_map.get(f"image_{index}", True) is not False
        ]

        if not enabled_images:
            # FORMAT.md §6.4 "Enforce ≥1 enabled": an empty list can crash a
            # downstream node whose only input is this list
            # (`max_len_input == 0` → called with zero kwargs) -- a loud,
            # clear queue-time error beats that silent crash.
            if not present:
                raise ValueError(
                    "EPS Switcher: no image inputs are connected -- wire at "
                    "least one image_N input"
                )
            raise ValueError(
                f"EPS Switcher: {len(present)} image input(s) connected but all "
                "are toggled off -- enable at least one (or remove this node)"
            )

        return (enabled_images,)
