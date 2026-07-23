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

**The input side is list-aware too (``INPUT_IS_LIST = True``), fixing a
real bug.** Every input — ``toggles`` and every connected ``image_N`` alike
— now arrives at ``execute``/``check_lazy_status`` already wrapped in a
list (ComfyUI's own convention for any node that opts in this way, passed
through unsliced: ``execution.py``'s ``get_input_data`` builds it,
``_async_map_node_over_list`` passes it straight through when
``INPUT_IS_LIST`` is set instead of slicing it). ``execute`` flattens each
ENABLED slot's list into the output one level: an ordinary node's
single-value output (itself always a length-1 list once wrapped)
contributes ONE output element — the same batch semantics as before — while
a list-producing upstream like ``EPSImageGrid`` (also ``OUTPUT_IS_LIST``)
contributes every one of its elements. WITHOUT ``INPUT_IS_LIST``, a
connected list-producing upstream instead made ComfyUI re-run THIS node
once per UPSTREAM element, broadcasting (repeating) every OTHER input's
single value across all those reruns (core's own ``slice_dict``/
map-over-list mechanism — the behavior for any node that does not declare
``INPUT_IS_LIST``) — the root cause of a real, owner-reported bug: a grid
input toggled OFF alongside a single enabled Load Image still ran the
downstream branch once per grid element (ten identical reruns of the one
Load Image picture) instead of once.

**Disabled slots are also genuinely lazy now (``check_lazy_status``,
below).** Each ``image_N`` input carries ``lazy: True`` in its INPUT_TYPES
options; a toggled-off slot's upstream branch is never even requested, so
it never executes — a real branch-skip, not just an output-side filter.
See ``check_lazy_status``'s own docstring for the mechanics and
``EPSSwitcher``'s class docstring for the "why".

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
at all should pass all three through, not silently drop them. ``toggles``
itself is deliberately NOT lazy (unlike every ``image_N``) — it has to be
available immediately, since it's what ``check_lazy_status`` reads to
decide which ``image_N`` slots to even ask for.

**All-off / none-connected is a valid state** (FORMAT.md §6.4, owner
decision 2026-07-20 -- "there will be times when a user might want to turn
them all off"). When zero images end up enabled, ``execute`` returns a
one-element list holding a ``comfy_execution.graph.ExecutionBlocker``
instead of raising -- a deliberate downgrade from the v0.14.0 behavior (a
queue-time ``ValueError`` naming the reason). See ``execute``'s own comment
for why an ``ExecutionBlocker`` beats a bare empty list here.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("eps_image")

#: Shared between ``_FlexibleOptionalImageInputs`` (INPUT_TYPES validation)
#: and ``_connected_image_indices`` (execute-/check_lazy_status-time
#: collection), so both agree on what counts as an image slot. Modeled on
#: the sibling pack's ``cprb/nodes_save.py`` ``_VIDEO_INPUT_PATTERN``.
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
    matching ``_IMAGE_INPUT_PATTERN`` as present with type
    ``("IMAGE", {"lazy": True})`` -- the ``lazy`` flag matters here just as
    much as it does on the hardcoded ``image_1`` entry (module docstring
    "Disabled slots are also genuinely lazy now"): ComfyUI reads it straight
    off whatever ``INPUT_TYPES()["optional"][input_name]`` returns for THAT
    input name when deciding whether to eagerly walk its upstream
    (``comfy_execution/graph.py`` ``TopologicalSort.add_node``/
    ``get_input_info``), so a dynamically-grown slot this ``__getitem__``
    synthesizes (``image_5`` and up, never actually inserted into this
    dict) must carry the identical options dict as ``image_1`` or its
    upstream would eagerly execute regardless of toggle state -- silently
    reopening the bug this module's ``INPUT_IS_LIST``/lazy pair fixes, but
    only for slot 2 and beyond.
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
            return ("IMAGE", {"lazy": True})
        raise KeyError(key)


def _unwrap_toggles(toggles: Any) -> Any:
    """Undo ComfyUI's ``INPUT_IS_LIST`` wrapping on the ``toggles`` widget
    value.

    With ``INPUT_IS_LIST = True``, every input arrives already wrapped in a
    list -- a widget value like ``toggles`` is wrapped as a length-1 list
    holding the string (ComfyUI's ``get_input_data`` non-link branch,
    passed through unsliced because ``INPUT_IS_LIST`` skips the normal
    per-call slicing entirely). A bare, non-list value is returned as-is --
    both a direct caller/test that passes the plain string, and a
    hand-built ``/prompt`` that omits the ``toggles`` key outright (falling
    through to ``execute``'s/``check_lazy_status``'s own
    ``toggles=DEFAULT_TOGGLES`` default, a plain ``str``, never a list) --
    keep working. An empty list falls back to ``DEFAULT_TOGGLES`` the same
    as an empty/missing string would.
    """
    if isinstance(toggles, (list, tuple)):
        return toggles[0] if toggles else DEFAULT_TOGGLES
    return toggles


def _connected_image_indices(kwargs: dict[str, Any]) -> list[int]:
    """Ascending slot numbers for every ``image_N`` key present in *kwargs*
    with a non-``None`` value.

    Presence-of-KEY (not value-truthiness) is what "connected" means here,
    on purpose: with every ``image_N`` slot now ``lazy``, a connected slot
    that hasn't resolved YET is still a key in *kwargs* -- ComfyUI fills it
    with a placeholder (a one-element tuple holding ``None``) rather than
    omitting it -- so it must still count as connected for
    ``check_lazy_status`` to be able to request it. A genuinely unconnected
    optional slot is never a key at all (ComfyUI's own input collection
    only populates what the prompt actually wires). A bare ``None`` VALUE
    (as opposed to the one-tuple placeholder, or an absent key) is kept as
    the "not connected" tolerance direct callers/older call sites rely on
    (a disconnected middle slot, or a hand-edited prompt) -- real ComfyUI
    never actually produces a bare ``None`` here itself (only the one-tuple
    placeholder or a real list), so this never excludes a genuinely-pending
    slot.
    """
    return sorted(
        int(match.group(1))
        for key, value in kwargs.items()
        if value is not None and (match := _IMAGE_INPUT_PATTERN.fullmatch(key))
    )


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

    ``INPUT_IS_LIST = True`` (own class attribute; ``execute`` and
    ``check_lazy_status`` both receive every input already wrapped in a
    list -- see the module docstring): required so a list-producing
    upstream -- ``EPSImageGrid``, itself ``OUTPUT_IS_LIST`` -- is merged
    element-wise into the output instead of ComfyUI silently re-running
    THIS node once per upstream element with every OTHER input
    broadcast-repeated (core's own ``execution.py``
    ``slice_dict``/map-over-list machinery -- the default for any node that
    does NOT declare ``INPUT_IS_LIST``). That default was the root cause of
    a real, owner-reported bug: a grid input toggled off alongside a single
    enabled Load Image still ran the downstream branch once per grid
    element instead of once, producing repeated identical edits of the one
    Load Image picture.

    Each ``image_N`` slot is ALSO ``lazy`` (INPUT_TYPES options,
    ``check_lazy_status`` below): a toggled-off slot's upstream is never
    requested and so never executes at all -- a genuine branch-skip, not
    just an output-side filter (superseding the earlier "their upstream
    nodes still execute regardless of toggle state" behavior this docstring
    used to describe -- owner ask: "where in the workflow we disable the
    run... Seems like something we should fix").

    Zero enabled images -- everything toggled off, or nothing connected at
    all -- is a VALID queue, not an error (FORMAT.md §6.4 "All-off /
    none-connected is a VALID state"): ``execute`` returns an
    ``ExecutionBlocker`` instead of raising, so the queue succeeds and the
    downstream image branch simply doesn't run for it.

    Re-derives the enabled set from ``toggles`` + the connected ``image_N``
    kwargs on every execution -- there is no other state to go stale, so
    unlike the Prompt Notebook this node needs no ``IS_CHANGED`` override:
    ``toggles`` and every ``image_N`` are ordinary tracked inputs already
    covered by ComfyUI's own input-hash caching.
    """

    CATEGORY = "EPSNodes"
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("images",)
    INPUT_IS_LIST = True
    OUTPUT_IS_LIST = (True,)
    FUNCTION = "execute"
    DESCRIPTION = (
        "Toggle any number of image inputs on/off; the enabled ones fan out in "
        "slot order (N enabled -> the rest of the workflow runs N times). A "
        "list-producing upstream (e.g. EPS Image Grid) merges element-wise into "
        "that count instead of counting as a single image. Disabled inputs are "
        "skipped before they ever run -- their upstream branch doesn't execute "
        "at all. Turning every input off (or wiring none at all) is a valid "
        "state -- the queue still succeeds, the downstream image branch just "
        "doesn't run for it. Caveat: a scalar value (e.g. a seed) wired "
        "downstream repeats identically across all N runs -- use an explicit "
        "per-image list for per-image variation."
    )

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {},
            "optional": _FlexibleOptionalImageInputs(
                {
                    # `lazy: True` -- see the module docstring's "Disabled
                    # slots are also genuinely lazy now" and this class's
                    # own docstring; `_FlexibleOptionalImageInputs.__getitem__`
                    # synthesizes the SAME options dict for every dynamically
                    # grown slot (image_2 and up) so they're all equally lazy.
                    "image_1": ("IMAGE", {"lazy": True}),
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
                    #
                    # Deliberately NOT lazy (module docstring "Enabled-set
                    # mechanism"): `check_lazy_status` needs it immediately to
                    # decide which `image_N` slots are even worth asking for.
                    "toggles": ("STRING", {"default": DEFAULT_TOGGLES, "multiline": False}),
                }
            ),
        }

    def check_lazy_status(self, toggles: Any = DEFAULT_TOGGLES, **kwargs: Any) -> list[str]:
        """Which ``image_N`` inputs ComfyUI should actually resolve.

        Called by core through the SAME ``INPUT_IS_LIST``-gated dispatch as
        ``execute`` (``execution.py``'s ``_async_map_node_over_list``, keyed
        off this class's own ``INPUT_IS_LIST`` -- not a separate mechanism),
        so it receives kwargs shaped exactly like ``execute``'s: ``toggles``
        list-wrapped (unwrapped the same way, via ``_unwrap_toggles``), and
        each connected ``image_N`` either its fully-resolved list (if some
        earlier round already resolved it) or the one-tuple ``(None,)``
        placeholder core uses for "connected but not resolved yet" on an
        ``INPUT_IS_LIST`` node. An unconnected slot is simply absent from
        *kwargs*, exactly like in ``execute``.

        Returns every ENABLED, connected slot's name, regardless of whether
        it has already resolved -- core's own post-filter (it only actually
        requests a name that is still genuinely unresolved, and keeps
        calling this method until nothing new is needed) makes that safe
        and self-terminating; this is also the documented contract of
        ComfyUI's own ``check_lazy_status`` (see
        ``comfy.comfy_types.node_typing.CheckLazyMixin``: "Will be executed
        repeatedly until it returns an empty list, or all requested items
        were already evaluated"). DISABLED connected slots, and slots that
        aren't connected at all, are never named here -- so core never asks
        for their upstream, which is the actual branch-skip: an upstream
        that's never requested is never added to the execution graph and
        never runs (``comfy_execution/graph.py``
        ``TopologicalSort.add_node``'s ``is_lazy`` branch skips a lazy
        input's producer entirely unless/until something promotes it via
        ``make_input_strong_link`` -- which only happens for a name THIS
        method returns).
        """
        toggle_map = _parse_toggles(_unwrap_toggles(toggles))
        return [
            f"image_{index}"
            for index in _connected_image_indices(kwargs)
            if toggle_map.get(f"image_{index}", True) is not False
        ]

    def execute(self, toggles: Any = DEFAULT_TOGGLES, **kwargs: Any) -> tuple[list[Any]]:
        toggle_map = _parse_toggles(_unwrap_toggles(toggles))
        connected = _connected_image_indices(kwargs)

        # A slot is enabled unless its key is present and EXPLICITLY the boolean
        # `false` (matches switcher.js's `!== false` and this module's own
        # docstring). Plain truthiness would wrongly drop a slot whose value is
        # a non-bool falsy like null/0/"" from a hand-edited workflow or a
        # non-frontend API caller -- the frontend renders those as ON, so the
        # backend must too, or the fan-out count silently disagrees with the UI.
        #
        # Every ENABLED slot's value is itself a list here (INPUT_IS_LIST --
        # module docstring): an ordinary upstream's single value arrives
        # wrapped as a length-1 list, a list-producing upstream (EPSImageGrid)
        # arrives as its full multi-element list. Extending the output with
        # each slot's elements (one level of flattening) reproduces the exact
        # same per-slot batch semantics as before INPUT_IS_LIST for the
        # length-1 case, while correctly merging a list-producing upstream
        # element-wise instead of counting it as one opaque item. A bare
        # (non-list) value -- a direct caller/test that skips the wrapping,
        # since real ComfyUI never sends one for a connected input -- is
        # tolerated as a single opaque element the same way a length-1 list
        # would flatten to one element. `None` elements inside a resolved
        # list are skipped defensively (a partial/misbehaving upstream, or
        # the lazy "not resolved" placeholder for a slot that -- because it's
        # disabled -- was never actually requested and so never resolved).
        enabled_images: list[Any] = []
        for index in connected:
            if toggle_map.get(f"image_{index}", True) is False:
                continue
            elements = kwargs[f"image_{index}"]
            if isinstance(elements, (list, tuple)):
                enabled_images.extend(element for element in elements if element is not None)
            else:
                enabled_images.append(elements)

        if not enabled_images:
            # FORMAT.md §6.4 "All-off / none-connected is a VALID state"
            # (owner decision 2026-07-20, supersedes the v0.14.0 behavior this
            # used to raise here -- "there will be times when a user might
            # want to turn them all off"). Returning a list holding a single
            # ExecutionBlocker(None) makes ComfyUI's own list-fanout machinery
            # (execution.py `merge_result_data`) treat THIS WHOLE LIST as
            # "blocked": every downstream node whose input traces back to it
            # is silently skipped -- no `execution_error` event (that only
            # fires when `.message` is not None, per execution.py's
            # `execution_block_cb`), no exception, a normal SUCCESS queue. A
            # bare `[]` was tried and rejected: with no co-input it still hits
            # the `max_len_input == 0` path and calls the downstream function
            # with ZERO kwargs (a crash for any node whose signature requires
            # the list), and mixed with a second, non-empty list input on the
            # same downstream node it IndexErrors inside `slice_dict`'s
            # `v[-1]` on an empty list. Verified live with a real /prompt +
            # /history round trip -- see tests/test_switcher.py and the
            # round-10 report.
            if not connected:
                logger.info(
                    "EPS Switcher: no image inputs are connected -- "
                    "returning an execution blocker so the queue succeeds "
                    "and downstream nodes are silently skipped"
                )
            else:
                logger.info(
                    "EPS Switcher: %d image input(s) connected but all are "
                    "toggled off -- returning an execution blocker so the "
                    "queue succeeds and downstream nodes are silently "
                    "skipped",
                    len(connected),
                )
            # Lazy import: keeps this module importable with no ComfyUI on
            # the path (module docstring's "no torch/ComfyUI import" promise;
            # see tests/test_switcher.py's test_module_never_imports_comfy_or_torch).
            from comfy_execution.graph import ExecutionBlocker

            return ([ExecutionBlocker(None)],)

        return (enabled_images,)
