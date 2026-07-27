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

#: Shared verbatim between the hardcoded ``image_1`` entry and every
#: dynamically-grown slot ``_FlexibleOptionalImageInputs.__getitem__``
#: synthesizes, so image_2 and up read identically to image_1 on hover.
_IMAGE_INPUT_TOOLTIP = (
    "An image to include when enabled. Toggle it from the row on the node; "
    "when off, nothing upstream of this socket runs at all."
)

#: Default ``toggles`` widget value: no overrides recorded yet, so every
#: connected slot is enabled (see the module docstring's default-enabled
#: rationale).
DEFAULT_TOGGLES = "{}"

#: This node's own class id, as it appears in a prompt's ``class_type`` --
#: used to spot an UPSTREAM sibling switcher (see
#: :func:`_slots_fed_by_an_empty_switcher`).
_CLASS_ID = "EPSSwitcher"


def _unwrap_hidden(value: Any) -> Any:
    """Undo ``INPUT_IS_LIST``'s wrapping of a hidden input.

    Core wraps hidden inputs in a one-element list exactly like widget values
    (``execution.py`` ``get_input_data``: ``input_data_all[x] =
    [dynprompt.get_original_prompt()]`` / ``[unique_id]``), and
    ``INPUT_IS_LIST`` hands them over unsliced -- same shape, same unwrap, as
    :func:`_unwrap_toggles`. A bare value (a direct caller/test) passes
    through untouched.
    """
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def _switcher_is_statically_all_off(node: dict) -> bool:
    """True when *node* is a prompt entry for an EPSSwitcher that provably
    emits nothing: every ``image_N`` it has wired is turned off in its own
    ``toggles`` (or it has none wired at all).

    "Provably" from the PROMPT ALONE -- ``toggles`` must be a literal string
    (it is: a hidden widget value). If it arrives as a link, or as anything
    this can't parse, the answer is False: unknown means "assume it produces
    something", so the caller behaves exactly as it did before this check
    existed.
    """
    if str(node.get("class_type") or "") != _CLASS_ID:
        return False
    inputs = node.get("inputs")
    if not isinstance(inputs, dict):
        return False
    toggles = inputs.get("toggles", DEFAULT_TOGGLES)
    if not isinstance(toggles, str):
        return False  # wired from another node -- not statically knowable
    toggle_map = _parse_toggles(toggles)
    wired = [
        key
        for key, value in inputs.items()
        if _IMAGE_INPUT_PATTERN.fullmatch(key)
        and isinstance(value, (list, tuple))
        and len(value) == 2
    ]
    return all(toggle_map.get(key, True) is False for key in wired)


def _slots_fed_by_an_empty_switcher(prompt: Any, unique_id: Any) -> set[str]:
    """The ``image_N`` input names of THIS node whose upstream is an
    EPSSwitcher that provably emits nothing (:func:`_switcher_is_statically_all_off`).

    Why this exists (owner report 2026-07-26): "if one of them has all of the
    inputs unchecked the entire workflow won't run. Even if it's earlier in
    the workflow than the second one." Reproduced exactly. An all-off switcher
    emits ``[ExecutionBlocker(None)]``, and core's pre-execution scan
    (``execution.py``'s ``process_inputs``) blocks a node when ANY element of
    ANY of its inputs is a blocker -- core has no notion of blocking just one
    input. So an all-off switcher feeding a SECOND switcher killed that
    second switcher outright, even though it had a perfectly good enabled
    image of its own. That is wrong: a switcher's job is "pass on the enabled
    inputs", and an empty upstream should contribute nothing, not veto us.

    The fix has to live HERE, on the consumer, and it has to work through the
    LAZY mechanism: by not REQUESTING such a slot, the upstream switcher never
    runs at all, so no blocker is ever created (rather than being created and
    then swallowed -- core gives us no way to swallow one).

    Rejected alternative, with live proof: having the all-off switcher emit a
    bare ``[]`` when it can see that all its consumers tolerate one. That is
    unsound because it makes a node's OUTPUT depend on the GRAPH while
    ComfyUI's cache key depends only on its INPUTS -- ``IsChangedCache`` even
    calls ``get_input_data`` with ``dynprompt=None`` ("We only want constants
    in IS_CHANGED"), so a graph-derived decision can never participate in the
    cache key. Tried it: the cached ``[]`` from a graph where it WAS safe got
    replayed into one where it wasn't, and SaveImage died with
    ``IndexError`` in ``slice_dict``. Never make an output graph-dependent.
    """
    if not isinstance(prompt, dict) or unique_id is None:
        return set()
    me = prompt.get(str(unique_id))
    if not isinstance(me, dict):
        return set()
    my_inputs = me.get("inputs")
    if not isinstance(my_inputs, dict):
        return set()
    skip: set[str] = set()
    for name, value in my_inputs.items():
        if not _IMAGE_INPUT_PATTERN.fullmatch(name):
            continue
        if not (isinstance(value, (list, tuple)) and len(value) == 2):
            continue
        upstream = prompt.get(str(value[0]))
        if isinstance(upstream, dict) and _switcher_is_statically_all_off(upstream):
            skip.add(name)
    return skip


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
            return ("IMAGE", {"lazy": True, "tooltip": _IMAGE_INPUT_TOOLTIP})
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
    OUTPUT_TOOLTIPS = (
        "Every enabled image, in slot order, as a list -- the rest of the "
        "workflow runs once per element.",
    )
    FUNCTION = "execute"
    DESCRIPTION = (
        "Toggle any number of image inputs on or off; the enabled ones fan "
        "out in slot order, so N enabled inputs make the rest of the "
        "workflow run N times (a list-producing input, like EPS Image Grid, "
        "counts for as many images as it holds). A toggled-off input's "
        "upstream branch never executes at all -- not just filtered out "
        "afterward. Turning every input off, or connecting none at all, is "
        "a valid state: the queue still succeeds and the downstream branch "
        "simply doesn't run. A value wired further downstream, like a seed, "
        "repeats identically across every run unless you give it an "
        "explicit per-image list."
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
                    # synthesizes the SAME options dict (including the same
                    # tooltip, `_IMAGE_INPUT_TOOLTIP`) for every dynamically
                    # grown slot (image_2 and up) so they're all equally lazy
                    # and read identically on hover.
                    "image_1": ("IMAGE", {"lazy": True, "tooltip": _IMAGE_INPUT_TOOLTIP}),
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
            # Server-supplied, never user-facing: lets the all-off branch see
            # WHO consumes this output before choosing between an empty list
            # and a blocker (see _EMPTY_TOLERANT_CONSUMERS). Hidden inputs
            # can't be omitted by an API caller in a way that breaks us --
            # `execute`'s own defaults cover their absence, and the fallback
            # is the pre-2026-07-26 behavior.
            "hidden": {"prompt": "PROMPT", "unique_id": "UNIQUE_ID"},
        }

    def check_lazy_status(
        self,
        toggles: Any = DEFAULT_TOGGLES,
        prompt: Any = None,
        unique_id: Any = None,
        **kwargs: Any,
    ) -> list[str]:
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
        # 2026-07-26: never request a slot fed by a provably-empty sibling
        # switcher -- see _slots_fed_by_an_empty_switcher for the full why.
        # Not requesting it means that switcher never runs, so its blocker is
        # never created and it cannot veto this node's OTHER enabled inputs.
        skip = _slots_fed_by_an_empty_switcher(
            _unwrap_hidden(prompt), _unwrap_hidden(unique_id)
        )
        if skip:
            logger.info(
                "EPS Switcher: not requesting %s -- fed by an EPS Switcher "
                "with everything toggled off, so it would only contribute an "
                "execution block; this node's other enabled inputs are "
                "unaffected",
                ", ".join(sorted(skip)),
            )
        return [
            name
            for index in _connected_image_indices(kwargs)
            if (name := f"image_{index}") not in skip
            and toggle_map.get(name, True) is not False
        ]

    def execute(
        self,
        toggles: Any = DEFAULT_TOGGLES,
        prompt: Any = None,
        unique_id: Any = None,
        **kwargs: Any,
    ) -> tuple[list[Any]]:
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
            # NOTE (2026-07-26): the blocker below is UNCONDITIONAL on
            # purpose. Emitting a bare [] for graphs whose consumers tolerate
            # one was tried and REVERTED -- it makes the output depend on the
            # graph while the cache key depends only on the inputs, so a
            # cached [] gets replayed into a graph where it crashes
            # (`slice_dict` IndexError). The consumer-side lazy skip in
            # `check_lazy_status` is the sound fix; see
            # `_slots_fed_by_an_empty_switcher`.
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
