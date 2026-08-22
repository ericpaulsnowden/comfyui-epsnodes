"""``EPSSwitcher`` (FORMAT.md §6.4, display: "EPS Image Switcher") — image toggle +
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

**Generalized to MODEL/CLIP/VAE (this module also defines
EPSModelSwitcher/EPSClipSwitcher/EPSVaeSwitcher).** Everything above is
still exactly what ``EPSSwitcher`` does -- its class id, ``image_N`` input
names, ``toggles`` semantics, and every behavior documented above are
unchanged. What changed is HOW it's built: ``_make_switcher_ns`` (near the
bottom of this module) factors the INPUT_TYPES/check_lazy_status/execute
shape into one parameterized builder -- unbounded ``<prefix>_N`` inputs via
a parameterized ``_make_flexible_optional_inputs_class``, the same hidden
``toggles`` bridge, the same INPUT_IS_LIST/OUTPUT_IS_LIST list fan-out, the
same lazy per-slot skip, the same all-off ``ExecutionBlocker`` -- so the
MODEL/CLIP/VAE siblings are the identical mechanism with a different
prefix/IO type/wording. ``EPSSwitcher`` is now ALSO built by a call into
that same factory (``prefix="image"``, ``io_type="IMAGE"``); only its
construction moved from a hand-written class body to a factory call. The
sibling-emptiness check (``_slots_fed_by_an_empty_switcher`` /
``_switcher_is_statically_all_off``) now recognizes an upstream of ANY of
the four classes via a small ``class_id -> that class's own slot pattern``
registry (``_SWITCHER_SLOT_PATTERNS``), populated as each class is built --
not just a single hardcoded class id.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("eps_image")

#: Slot-name patterns for each switcher's unbounded ``<prefix>_N`` inputs.
#: Shared between that type's flexible-optional-inputs class (INPUT_TYPES
#: validation) and the connected-slot/lazy-skip helpers below, so all of
#: them agree on what counts as that class's own slot. Modeled on the
#: sibling pack's ``cprb/nodes_save.py`` ``_VIDEO_INPUT_PATTERN``.
_IMAGE_INPUT_PATTERN = re.compile(r"image_(\d+)")
_MODEL_INPUT_PATTERN = re.compile(r"model_(\d+)")
_CLIP_INPUT_PATTERN = re.compile(r"clip_(\d+)")
_VAE_INPUT_PATTERN = re.compile(r"vae_(\d+)")

#: Per-type hover tooltip for slot 1 and every dynamically-grown slot alike,
#: so e.g. model_2+ reads identically to model_1 on hover -- see
#: _make_flexible_optional_inputs_class's docstring for the mechanism.
_IMAGE_INPUT_TOOLTIP = (
    "An image to include when enabled. Toggle it from the row on the node; "
    "when off, nothing upstream of this socket runs at all."
)
#: §6.4 WAN high/low pairing (v0.66.0, owner ask 2026-08-14: "models like
#: WAN that have more than one model (high and low version)"; his spec:
#: two stages exactly, named high/low, enabled per-node from Properties).
#: A `model_N_low` slot pairs with `model_N` (the HIGH model); the row's
#: single toggle governs both.
_MODEL_LOW_INPUT_PATTERN = re.compile(r"model_(\d+)_low")
_MODEL_LOW_INPUT_TOOLTIP = (
    "The LOW-noise partner of this row's model (WAN-style two-stage "
    "pairs). Appears when the node's 'High/low pairs' property is on; "
    "the row's one toggle governs both models together."
)

_MODEL_INPUT_TOOLTIP = (
    "A model to include when enabled. Toggle it from the row on the node; "
    "when off, nothing upstream of this socket runs at all."
)
_CLIP_INPUT_TOOLTIP = (
    "A CLIP to include when enabled. Toggle it from the row on the node; "
    "when off, nothing upstream of this socket runs at all."
)
_VAE_INPUT_TOOLTIP = (
    "A VAE to include when enabled. Toggle it from the row on the node; "
    "when off, nothing upstream of this socket runs at all."
)

#: Default ``toggles`` widget value: no overrides recorded yet, so every
#: connected slot is enabled (see the module docstring's default-enabled
#: rationale). Shared by all four classes -- the JSON shape doesn't depend
#: on the noun, only its keys (``<prefix>_N``) do.
DEFAULT_TOGGLES = "{}"

#: class_id -> that class's own slot pattern, for every switcher class this
#: module defines. Populated by _make_switcher_ns as each class is built
#: (bottom of this module); read by _switcher_is_statically_all_off so a
#: sibling-emptiness check recognizes an upstream of ANY of the four
#: classes -- using THAT upstream's own prefix to scan its wired slots,
#: never the calling class's prefix.
_SWITCHER_SLOT_PATTERNS: dict[str, re.Pattern[str]] = {}


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


def _parse_toggles(
    toggles: str, *, log_prefix: str = "EPS Image Switcher", noun: str = "image"
) -> dict[str, Any]:
    """Best-effort JSON object parse of the ``toggles`` widget value.

    Never raises: a malformed/foreign value (a hand-edited workflow, an API
    caller sending garbage) degrades to "no overrides recorded" -- i.e.
    every slot enabled -- rather than crashing the node, logging a warning
    so the cause is visible without being fatal. *log_prefix*/*noun* name
    the calling switcher class and its slot noun in that warning and default
    to EPSSwitcher's own original "EPS Image Switcher"/"image" wording, so every
    pre-existing call site (which never passed them) logs byte-identical
    text to before this function was shared across all four switcher
    classes.
    """
    if not toggles:
        return {}
    try:
        parsed = json.loads(toggles)
    except (TypeError, ValueError) as exc:
        logger.warning(
            "%s: malformed `toggles` value (%s); treating every connected %s as enabled",
            log_prefix,
            exc,
            noun,
        )
        return {}
    if not isinstance(parsed, dict):
        logger.warning(
            "%s: `toggles` was not a JSON object (got %r); treating every "
            "connected %s as enabled",
            log_prefix,
            type(parsed).__name__,
            noun,
        )
        return {}
    return parsed


def _connected_slot_indices(kwargs: dict[str, Any], pattern: re.Pattern[str]) -> list[int]:
    """Ascending slot numbers for every ``<prefix>_N`` key (matching
    *pattern*) present in *kwargs* with a non-``None`` value.

    Presence-of-KEY (not value-truthiness) is what "connected" means here,
    on purpose: with every slot now ``lazy``, a connected slot that hasn't
    resolved YET is still a key in *kwargs* -- ComfyUI fills it with a
    placeholder (a one-element tuple holding ``None``) rather than omitting
    it -- so it must still count as connected for ``check_lazy_status`` to
    be able to request it. A genuinely unconnected optional slot is never a
    key at all (ComfyUI's own input collection only populates what the
    prompt actually wires). A bare ``None`` VALUE (as opposed to the
    one-tuple placeholder, or an absent key) is kept as the "not connected"
    tolerance direct callers/older call sites rely on (a disconnected
    middle slot, or a hand-edited prompt) -- real ComfyUI never actually
    produces a bare ``None`` here itself (only the one-tuple placeholder or
    a real list), so this never excludes a genuinely-pending slot.
    """
    return sorted(
        int(match.group(1))
        for key, value in kwargs.items()
        if value is not None and (match := pattern.fullmatch(key))
    )


def _switcher_is_statically_all_off(node: dict) -> bool:
    """True when *node* is a prompt entry for one of this module's switcher
    classes (EPSSwitcher/EPSModelSwitcher/EPSClipSwitcher/EPSVaeSwitcher)
    that provably emits nothing: every ``<prefix>_N`` slot it has wired is
    turned off in its own ``toggles`` (or it has none wired at all).

    Generalized beyond a single hardcoded class id (FORMAT.md §6.4, grown to
    MODEL/CLIP/VAE): *node*'s ``class_type`` is looked up in
    ``_SWITCHER_SLOT_PATTERNS`` to find THAT class's own slot-name pattern --
    e.g. an EPSModelSwitcher upstream is scanned for ``model_N`` keys, not
    whatever prefix the CALLER happens to use -- so any of the four classes
    is recognized as a sibling, not just the caller's own type.

    "Provably" from the PROMPT ALONE -- ``toggles`` must be a literal string
    (it is: a hidden widget value). If it arrives as a link, or as anything
    this can't parse, or *node* isn't one of this module's switcher classes
    at all, the answer is False: unknown means "assume it produces
    something", so the caller behaves exactly as it did before this check
    existed.
    """
    class_type = str(node.get("class_type") or "")
    pattern = _SWITCHER_SLOT_PATTERNS.get(class_type)
    if pattern is None:
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
        if pattern.fullmatch(key) and isinstance(value, (list, tuple)) and len(value) == 2
    ]
    return all(toggle_map.get(key, True) is False for key in wired)


def _slots_fed_by_an_empty_switcher(
    prompt: Any, unique_id: Any, pattern: re.Pattern[str]
) -> set[str]:
    """The ``<prefix>_N`` input names of THIS node (matching *pattern*, this
    class's own slot prefix) whose upstream is a switcher of ANY of the four
    classes that provably emits nothing (:func:`_switcher_is_statically_all_off`).

    Why this exists (owner report 2026-07-26, originally observed on
    EPSSwitcher alone): "if one of them has all of the inputs unchecked the
    entire workflow won't run. Even if it's earlier in the workflow than the
    second one." Reproduced exactly. An all-off switcher emits
    ``[ExecutionBlocker(None)]``, and core's pre-execution scan
    (``execution.py``'s ``process_inputs``) blocks a node when ANY element of
    ANY of its inputs is a blocker -- core has no notion of blocking just one
    input. So an all-off switcher feeding a SECOND switcher killed that
    second switcher outright, even though it had a perfectly good enabled
    input of its own. That is wrong: a switcher's job is "pass on the enabled
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
    cache key. Tried it (for EPSSwitcher): the cached ``[]`` from a graph
    where it WAS safe got replayed into one where it wasn't, and SaveImage
    died with ``IndexError`` in ``slice_dict``. Never make an output
    graph-dependent.
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
        if not pattern.fullmatch(name):
            continue
        if not (isinstance(value, (list, tuple)) and len(value) == 2):
            continue
        upstream = prompt.get(str(value[0]))
        if isinstance(upstream, dict) and _switcher_is_statically_all_off(upstream):
            skip.add(name)
    return skip


def _make_flexible_optional_inputs_class(
    pattern: re.Pattern[str],
    io_type: str,
    tooltip: str,
    extra_specs: list[tuple[re.Pattern[str], str, str]] | None = None,
) -> type[dict]:
    """Build a dict-subclass accepting ANY ``<prefix>_N`` key -- the
    ``optional`` half of a switcher's INPUT_TYPES (FORMAT.md §6.4).

    ComfyUI's own input validation checks ``input_name in
    class_inputs['optional']`` (the ``in`` operator, i.e. ``__contains__``)
    before letting a workflow wire a given input on a node -- unbounded
    ``<prefix>_N`` growth needs that check to say yes to ``model_5``,
    ``clip_37``, etc. even though only slot 1 is ever actually stored in the
    dict. Originally written once for IMAGE (``_FlexibleOptionalImageInputs``,
    itself modeled on the sibling comfyui-premiere-bridge pack's
    ``cprb/nodes_save.py`` ``_FlexibleOptionalVideoInputs``, in turn modeled
    on rgthree-comfy's ``FlexibleOptionalInputType`` trick, reimplemented
    locally -- this pack does not depend on rgthree); this factory
    parameterizes that same mechanism by *pattern* (which ``<prefix>_N``
    shape to accept), *io_type* (what type each synthesized slot reports),
    and *tooltip* (its hover text) so MODEL/CLIP/VAE don't each duplicate the
    class body.

    Both ``__contains__`` and ``__getitem__`` are overridden (the latter for
    safety, in case something subscripts rather than uses ``in``/``.get``):
    any key matching *pattern* is treated as present with type
    ``(io_type, {"lazy": True, "tooltip": tooltip})`` -- the ``lazy`` flag
    matters here just as much as it does on the hardcoded slot-1 entry
    (module docstring "Disabled slots are also genuinely lazy now"): ComfyUI
    reads it straight off whatever ``INPUT_TYPES()["optional"][input_name]``
    returns for THAT input name when deciding whether to eagerly walk its
    upstream (``comfy_execution/graph.py`` ``TopologicalSort.add_node``/
    ``get_input_info``), so a dynamically-grown slot this ``__getitem__``
    synthesizes must carry the identical options dict as slot 1 or its
    upstream would eagerly execute regardless of toggle state.

    Plain dict iteration/``.items()``/``.keys()`` is left untouched, so it
    still only yields whatever was actually inserted (slot 1) -- which is
    what ``/object_info`` (and thus the frontend's default socket rendering)
    sees, giving the node exactly one visible socket out of the box; the
    frontend JS grows the rest.
    """

    # v0.66.0: a class may accept MORE than one slot shape (the model
    # switcher's paired `model_N_low` slots ride alongside `model_N`).
    # `extra_specs` entries are (pattern, io_type, tooltip) checked AFTER
    # the primary pattern; everything else about the mechanism is
    # unchanged. fullmatch keeps the shapes disjoint: `model_1_low` can
    # only ever match its own spec, never slot-1's `model_(\d+)`.
    extra_specs = extra_specs or []

    class _FlexibleOptionalInputs(dict):
        def __contains__(self, key: object) -> bool:
            if isinstance(key, str) and pattern.fullmatch(key):
                return True
            if isinstance(key, str):
                for extra_pattern, _io, _tip in extra_specs:
                    if extra_pattern.fullmatch(key):
                        return True
            return super().__contains__(key)

        def __getitem__(self, key: str) -> Any:
            if super().__contains__(key):
                return super().__getitem__(key)
            if isinstance(key, str) and pattern.fullmatch(key):
                return (io_type, {"lazy": True, "tooltip": tooltip})
            if isinstance(key, str):
                for extra_pattern, extra_io, extra_tip in extra_specs:
                    if extra_pattern.fullmatch(key):
                        return (extra_io, {"lazy": True, "tooltip": extra_tip})
            raise KeyError(key)

    return _FlexibleOptionalInputs


# The IMAGE instantiation keeps its original module-level name -- unchanged
# from before this factory existed -- because tests/test_switcher.py imports
# it directly (`from eps_image.nodes_switcher import ... _FlexibleOptionalImageInputs`)
# and constructs instances of it itself. Built via the shared factory just
# like the three siblings below, so EPSSwitcher's own INPUT_TYPES() uses this
# exact class (see its `flexible_cls=` argument at the bottom of this module).
_FlexibleOptionalImageInputs = _make_flexible_optional_inputs_class(
    _IMAGE_INPUT_PATTERN, "IMAGE", _IMAGE_INPUT_TOOLTIP
)


def _make_switcher_ns(
    *,
    class_id: str,
    prefix: str,
    pattern: re.Pattern[str],
    io_type: str,
    output_name: str,
    log_prefix: str,
    noun: str,
    input_tooltip: str,
    output_tooltip: str,
    description: str,
    class_doc: str,
    flexible_cls: type[dict] | None = None,
    low_pattern: re.Pattern[str] | None = None,
    low_tooltip: str = "",
    low_output_name: str = "",
    low_output_tooltip: str = "",
) -> type:
    """Build one switcher class (FORMAT.md §6.4 and its MODEL/CLIP/VAE
    generalization). ``EPSSwitcher`` (IMAGE) and its three siblings
    (``EPSModelSwitcher``/``EPSClipSwitcher``/``EPSVaeSwitcher``) are all
    just different parameterizations of this one shape: unbounded
    ``<prefix>_N`` optional inputs (via
    :func:`_make_flexible_optional_inputs_class`), a hidden ``toggles``
    STRING bridge, ``INPUT_IS_LIST``/``OUTPUT_IS_LIST`` list fan-out, lazy
    per-slot skip (:func:`_slots_fed_by_an_empty_switcher`), and an
    ``ExecutionBlocker`` when nothing ends up enabled. See ``EPSSwitcher``'s
    own class docstring (the IMAGE instantiation, at the bottom of this
    module) for the full historical mechanism write-up -- only the noun and
    IO type genuinely vary per class; the mechanics are identical.

    Registers *class_id* -> *pattern* into ``_SWITCHER_SLOT_PATTERNS`` as a
    side effect, so :func:`_switcher_is_statically_all_off` recognizes this
    class as a sibling switcher from here on (this module calls this factory
    for all four classes before any of them can actually execute, so the
    registry is always fully populated by the time a real prompt runs).

    *flexible_cls*, when given, is used as-is instead of building a fresh
    one -- solely so ``EPSSwitcher`` can keep using the pre-existing,
    externally-imported ``_FlexibleOptionalImageInputs`` object identity;
    every other class lets this factory build its own private one.
    """
    if flexible_cls is None:
        extra = [(low_pattern, io_type, low_tooltip)] if low_pattern is not None else None
        flexible_cls = _make_flexible_optional_inputs_class(
            pattern, io_type, input_tooltip, extra_specs=extra
        )
    _SWITCHER_SLOT_PATTERNS[class_id] = pattern

    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {},
            "optional": flexible_cls(
                {
                    # `lazy: True` -- module docstring "Disabled slots are
                    # also genuinely lazy now"; __getitem__ above synthesizes
                    # the SAME options dict (including the same tooltip) for
                    # every dynamically grown slot (slot 2 and up) so they're
                    # all equally lazy and read identically on hover.
                    f"{prefix}_1": (io_type, {"lazy": True, "tooltip": input_tooltip}),
                    # Serialized bridge to the frontend's per-row/header
                    # toggle UI (module docstring "Enabled-set mechanism");
                    # the frontend hides this widget's on-canvas row but
                    # keeps writing its value, so it still serializes with
                    # the workflow and still reaches execute() untouched for
                    # a plain API caller who never loads our JS.
                    #
                    # In `optional`, NOT `required`: ComfyUI's
                    # validate_inputs rejects a /prompt whose inputs omit any
                    # REQUIRED key BEFORE the node runs (there is no backend
                    # default-fill), which would make the documented "API
                    # caller who never heard of this widget" path (module
                    # docstring) unreachable. execute()'s own
                    # `toggles=DEFAULT_TOGGLES` default covers the omitted
                    # case; the frontend still serializes it.
                    #
                    # Deliberately NOT lazy (module docstring "Enabled-set
                    # mechanism"): `check_lazy_status` needs it immediately
                    # to decide which slots are even worth asking for.
                    #
                    # "hidden": True is the VUE-nodes ("New node design")
                    # hide flag (2026-07-29): that renderer decides widget
                    # visibility from the input spec's options
                    # (`options.hidden`, useProcessedWidgets.ts) and IGNORES
                    # the litegraph `widget.hidden` the frontend sets --
                    # without this, this internal widget leaks into Vue nodes
                    # as a raw editable field. The classic canvas renderer
                    # ignores this key right back, so it changes nothing
                    # there.
                    "toggles": (
                        "STRING",
                        {"default": DEFAULT_TOGGLES, "multiline": False, "hidden": True},
                    ),
                }
            ),
            # Server-supplied, never user-facing: lets check_lazy_status
            # inspect the prompt for a provably-empty sibling switcher before
            # requesting a slot fed by one. Hidden inputs can't be omitted by
            # an API caller in a way that breaks us -- both methods' own
            # defaults cover their absence, and the fallback is "assume the
            # upstream produces something".
            "hidden": {"prompt": "PROMPT", "unique_id": "UNIQUE_ID"},
        }

    def check_lazy_status(
        self,
        toggles: Any = DEFAULT_TOGGLES,
        prompt: Any = None,
        unique_id: Any = None,
        **kwargs: Any,
    ) -> list[str]:
        """Which ``<prefix>_N`` inputs ComfyUI should actually resolve.

        Called by core through the SAME ``INPUT_IS_LIST``-gated dispatch as
        ``execute`` (``execution.py``'s ``_async_map_node_over_list``), so it
        receives kwargs shaped exactly like ``execute``'s. Returns every
        ENABLED, connected slot's name, regardless of whether it has already
        resolved -- core's own post-filter makes that safe and
        self-terminating (the documented contract of
        ``comfy.comfy_types.node_typing.CheckLazyMixin``: "Will be executed
        repeatedly until it returns an empty list, or all requested items
        were already evaluated"). DISABLED connected slots, slots that
        aren't connected at all, and slots fed by a provably-empty sibling
        switcher (:func:`_slots_fed_by_an_empty_switcher`) are never named
        here -- so core never asks for their upstream, which is the actual
        branch-skip.
        """
        toggle_map = _parse_toggles(_unwrap_toggles(toggles), log_prefix=log_prefix, noun=noun)
        # 2026-07-26: never request a slot fed by a provably-empty sibling
        # switcher of ANY of the four classes -- see
        # _slots_fed_by_an_empty_switcher for the full why. Not requesting it
        # means that switcher never runs, so its blocker is never created and
        # it cannot veto this node's OTHER enabled inputs.
        skip = _slots_fed_by_an_empty_switcher(
            _unwrap_hidden(prompt), _unwrap_hidden(unique_id), pattern
        )
        if skip:
            logger.info(
                "%s: not requesting %s -- fed by a switcher with everything "
                "toggled off, so it would only contribute an execution "
                "block; this node's other enabled inputs are unaffected",
                log_prefix,
                ", ".join(sorted(skip)),
            )
        wanted = [
            name
            for index in _connected_slot_indices(kwargs, pattern)
            if (name := f"{prefix}_{index}") not in skip and toggle_map.get(name, True) is not False
        ]
        if low_pattern is not None:
            # §6.4 pairing (v0.66.0): a row's LOW slot follows its row's one
            # toggle (keyed by the HIGH name) -- a disabled row skips both
            # upstreams, so a toggled-off WAN pair loads neither model.
            # ...and only when the HIGH itself is wanted (audit 2026-08-21):
            # a high fed by a statically-all-off sibling switcher is in
            # `skip` and never arrives, so requesting its low alone ran the
            # low's upstream for nothing and then failed the 0-high/1-low
            # pairing check in execute().
            wanted_highs = set(wanted)
            wanted.extend(
                f"{prefix}_{index}_low"
                for index in _connected_slot_indices(kwargs, low_pattern)
                if f"{prefix}_{index}" in wanted_highs
            )
        return wanted

    def execute(
        self,
        toggles: Any = DEFAULT_TOGGLES,
        prompt: Any = None,
        unique_id: Any = None,
        **kwargs: Any,
    ) -> tuple[list[Any]]:
        toggle_map = _parse_toggles(_unwrap_toggles(toggles), log_prefix=log_prefix, noun=noun)
        connected = _connected_slot_indices(kwargs, pattern)

        # A slot is enabled unless its key is present and EXPLICITLY the
        # boolean `false` (matches the frontend's `!== false` and this
        # module's own docstring). Plain truthiness would wrongly drop a slot
        # whose value is a non-bool falsy like null/0/"" from a hand-edited
        # workflow or a non-frontend API caller -- the frontend renders those
        # as ON, so the backend must too, or the fan-out count silently
        # disagrees with the UI.
        #
        # Every ENABLED slot's value is itself a list here (INPUT_IS_LIST --
        # module docstring): an ordinary upstream's single value arrives
        # wrapped as a length-1 list, a list-producing upstream arrives as
        # its full multi-element list. Extending the output with each slot's
        # elements (one level of flattening) reproduces the exact same
        # per-slot batch semantics as before INPUT_IS_LIST for the length-1
        # case, while correctly merging a list-producing upstream
        # element-wise instead of counting it as one opaque item. A bare
        # (non-list) value -- a direct caller/test that skips the wrapping,
        # since real ComfyUI never sends one for a connected input -- is
        # tolerated as a single opaque element the same way a length-1 list
        # would flatten to one element. `None` elements inside a resolved
        # list are skipped defensively (a partial/misbehaving upstream, or
        # the lazy "not resolved" placeholder for a slot that -- because it's
        # disabled -- was never actually requested and so never resolved).
        enabled_values: list[Any] = []
        low_values: list[Any] = []

        def _flatten(raw: Any) -> list[Any]:
            if isinstance(raw, (list, tuple)):
                return [element for element in raw if element is not None]
            return [raw]

        for index in connected:
            key = f"{prefix}_{index}"
            if toggle_map.get(key, True) is False:
                continue
            highs = _flatten(kwargs[key])
            if not highs:
                # A skipped/blocked high (lazy skip set, None upstream) has
                # nothing to pair or emit -- never a 0-vs-N pairing error
                # (audit 2026-08-21).
                continue
            enabled_values.extend(highs)
            if low_pattern is None:
                continue
            # §6.4 WAN pairing (v0.66.0): the low output stays index-aligned
            # with the high output BY CONSTRUCTION. Per enabled slot: a
            # wired low pairs 1:1 with that slot's highs, a SINGLE low
            # broadcasts across them (a list-producing high with one low
            # partner), any other length disagreement is a MISWIRE and
            # fails loudly -- a silent clamp here is exactly the wrong
            # high+low pairing WAN must never get. An unwired low
            # contributes per-element ExecutionBlockers, so only consumers
            # of the LOW output are skipped for that row (Resolution's §6.5
            # per-slot blocker precedent).
            low_key = f"{key}_low"
            if low_key in kwargs and kwargs[low_key] is not None:
                lows = _flatten(kwargs[low_key])
                if len(lows) == len(highs):
                    low_values.extend(lows)
                elif len(lows) == 1 and highs:
                    low_values.extend(lows * len(highs))
                else:
                    raise ValueError(
                        f"{log_prefix}: row {index} pairs {len(highs)} high "
                        f"model(s) with {len(lows)} low model(s) -- the two "
                        "sides of a high/low pair must match (or wire a "
                        "single low to broadcast). Fix the wiring on "
                        f"{low_key}."
                    )
            else:
                from comfy_execution.graph import ExecutionBlocker

                low_values.extend(ExecutionBlocker(None) for _ in highs)

        if not enabled_values:
            # FORMAT.md §6.4 "All-off / none-connected is a VALID state"
            # (owner decision 2026-07-20). Returning a list holding a single
            # ExecutionBlocker(None) makes ComfyUI's own list-fanout
            # machinery (execution.py `merge_result_data`) treat THIS WHOLE
            # LIST as "blocked": every downstream node whose input traces
            # back to it is silently skipped -- no `execution_error` event
            # (that only fires when `.message` is not None, per
            # execution.py's `execution_block_cb`), no exception, a normal
            # SUCCESS queue. A bare `[]` was tried (for EPSSwitcher) and
            # rejected: with no co-input it still hits the
            # `max_len_input == 0` path and calls the downstream function
            # with ZERO kwargs (a crash for any node whose signature requires
            # the list), and mixed with a second, non-empty list input on the
            # same downstream node it IndexErrors inside `slice_dict`'s
            # `v[-1]` on an empty list. See tests/test_switcher.py and the
            # round-10 report for the original EPSSwitcher verification.
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
                    "%s: no %s inputs are connected -- returning an execution "
                    "blocker so the queue succeeds and downstream nodes are "
                    "silently skipped",
                    log_prefix,
                    noun,
                )
            else:
                logger.info(
                    "%s: %d %s input(s) connected but all are toggled off -- "
                    "returning an execution blocker so the queue succeeds and "
                    "downstream nodes are silently skipped",
                    log_prefix,
                    len(connected),
                    noun,
                )
            # Lazy import: keeps this module importable with no ComfyUI on
            # the path (module docstring's no-ComfyUI-at-module-scope
            # promise; see tests/test_switcher.py's
            # test_module_never_imports_comfy_or_torch).
            from comfy_execution.graph import ExecutionBlocker

            blocked = [ExecutionBlocker(None)]
            return (blocked, [ExecutionBlocker(None)]) if low_pattern is not None else (blocked,)

        if low_pattern is not None:
            return (enabled_values, low_values)
        return (enabled_values,)

    paired = low_pattern is not None
    namespace: dict[str, Any] = {
        "__module__": __name__,
        "__doc__": class_doc,
        "CATEGORY": "EPSNodes/Switchers",
        # §8: the low output is a TAIL append -- existing wires never move.
        "RETURN_TYPES": (io_type, io_type) if paired else (io_type,),
        "RETURN_NAMES": (output_name, low_output_name) if paired else (output_name,),
        "INPUT_IS_LIST": True,
        "OUTPUT_IS_LIST": (True, True) if paired else (True,),
        "OUTPUT_TOOLTIPS": (output_tooltip, low_output_tooltip) if paired else (output_tooltip,),
        "FUNCTION": "execute",
        "DESCRIPTION": description,
        "INPUT_TYPES": classmethod(INPUT_TYPES),
        "check_lazy_status": check_lazy_status,
        "execute": execute,
    }
    return type(class_id, (), namespace)


# --------------------------------------------------------------- EPSSwitcher

_EPS_SWITCHER_CLASS_DOC = """Any number of ``image_N`` inputs, each independently on/off, fanned
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

EPSSwitcher = _make_switcher_ns(
    class_id="EPSSwitcher",
    prefix="image",
    pattern=_IMAGE_INPUT_PATTERN,
    io_type="IMAGE",
    output_name="images",
    log_prefix="EPS Image Switcher",
    noun="image",
    input_tooltip=_IMAGE_INPUT_TOOLTIP,
    output_tooltip=(
        "Every enabled image, in slot order, as a list -- the rest of the "
        "workflow runs once per element."
    ),
    description=(
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
    ),
    class_doc=_EPS_SWITCHER_CLASS_DOC,
    flexible_cls=_FlexibleOptionalImageInputs,
)


# ---------------------------------------------------------- EPSModelSwitcher

_EPS_MODEL_SWITCHER_CLASS_DOC = """Any number of ``model_N`` inputs, each
independently on/off, fanned into one ``MODEL`` list output (FORMAT.md
§6.4's mechanism, generalized from ``EPSSwitcher``'s IMAGE case to MODEL
via ``_make_switcher_ns`` -- see that class's own docstring, above, for
the full historical write-up; this is the identical shape with "model"
in place of "image").

``INPUT_IS_LIST = True``: a list-producing upstream is merged element-wise
into the output instead of ComfyUI silently re-running this node once per
upstream element with every OTHER input broadcast-repeated. Each
``model_N`` slot is ALSO ``lazy`` (``check_lazy_status`` below): a
toggled-off slot's upstream -- for example, a checkpoint load feeding a
disabled model slot -- is never requested and so never executes at all, a
genuine branch-skip rather than an output-side filter.

Zero enabled models -- everything toggled off, or nothing connected at
all -- is a VALID queue, not an error: ``execute`` returns an
``ExecutionBlocker`` instead of raising, so the queue succeeds and the
downstream model branch simply doesn't run for it.

Re-derives the enabled set from ``toggles`` + the connected ``model_N``
kwargs on every execution -- there is no other state to go stale, so this
node needs no ``IS_CHANGED`` override.
"""

EPSModelSwitcher = _make_switcher_ns(
    class_id="EPSModelSwitcher",
    prefix="model",
    pattern=_MODEL_INPUT_PATTERN,
    io_type="MODEL",
    output_name="models",
    log_prefix="EPS Model Switcher",
    noun="model",
    low_pattern=_MODEL_LOW_INPUT_PATTERN,
    low_tooltip=_MODEL_LOW_INPUT_TOOLTIP,
    low_output_name="models_low",
    low_output_tooltip=(
        "Every enabled row's LOW-noise partner model, index-aligned with "
        "the models output (WAN-style high/low pairs -- see the 'High/low "
        "pairs' property). Rows without a low wired contribute a skip for "
        "this output only."
    ),
    input_tooltip=_MODEL_INPUT_TOOLTIP,
    output_tooltip=(
        "Every enabled model, in slot order, as a list -- the rest of the "
        "workflow runs once per element."
    ),
    description=(
        "Toggle any number of model inputs on or off; the enabled ones fan "
        "out in slot order, so N enabled inputs make the rest of the "
        "workflow run N times. A toggled-off input's upstream branch never "
        "executes at all -- for example, a checkpoint load feeding a "
        "disabled model slot never runs, not just filtered out afterward. "
        "Turning every input off, or connecting none at all, is a valid "
        "state: the queue still succeeds and the downstream branch simply "
        "doesn't run. A value wired further downstream that isn't itself a "
        "list, like a fixed seed, repeats identically across every run "
        "unless you give it an explicit per-model list. For WAN-style "
        "two-stage models, turn on the 'High/low pairs' property: every "
        "row gains a low-model socket, the models_low output stays "
        "index-aligned with models, and the row's one toggle governs the "
        "pair."
    ),
    class_doc=_EPS_MODEL_SWITCHER_CLASS_DOC,
)


# ----------------------------------------------------------- EPSClipSwitcher

_EPS_CLIP_SWITCHER_CLASS_DOC = """Any number of ``clip_N`` inputs, each independently on/off, fanned
into one ``CLIP`` list output (FORMAT.md §6.4's mechanism, generalized
from ``EPSSwitcher``'s IMAGE case to CLIP via ``_make_switcher_ns`` --
see that class's own docstring, above, for the full historical write-up;
this is the identical shape with "clip" in place of "image").

``INPUT_IS_LIST = True``: a list-producing upstream is merged element-wise
into the output instead of ComfyUI silently re-running this node once per
upstream element with every OTHER input broadcast-repeated. Each
``clip_N`` slot is ALSO ``lazy`` (``check_lazy_status`` below): a
toggled-off slot's upstream -- for example, a checkpoint load feeding a
disabled CLIP slot -- is never requested and so never executes at all, a
genuine branch-skip rather than an output-side filter.

Zero enabled CLIPs -- everything toggled off, or nothing connected at
all -- is a VALID queue, not an error: ``execute`` returns an
``ExecutionBlocker`` instead of raising, so the queue succeeds and the
downstream CLIP branch simply doesn't run for it.

Re-derives the enabled set from ``toggles`` + the connected ``clip_N``
kwargs on every execution -- there is no other state to go stale, so this
node needs no ``IS_CHANGED`` override.
"""

EPSClipSwitcher = _make_switcher_ns(
    class_id="EPSClipSwitcher",
    prefix="clip",
    pattern=_CLIP_INPUT_PATTERN,
    io_type="CLIP",
    output_name="clips",
    log_prefix="EPS CLIP Switcher",
    noun="clip",
    input_tooltip=_CLIP_INPUT_TOOLTIP,
    output_tooltip=(
        "Every enabled CLIP, in slot order, as a list -- the rest of the "
        "workflow runs once per element."
    ),
    description=(
        "Toggle any number of CLIP inputs on or off; the enabled ones fan "
        "out in slot order, so N enabled inputs make the rest of the "
        "workflow run N times. A toggled-off input's upstream branch never "
        "executes at all -- for example, a checkpoint load feeding a "
        "disabled CLIP slot never runs, not just filtered out afterward. "
        "Turning every input off, or connecting none at all, is a valid "
        "state: the queue still succeeds and the downstream branch simply "
        "doesn't run. A value wired further downstream that isn't itself a "
        "list, like a fixed seed, repeats identically across every run "
        "unless you give it an explicit per-CLIP list."
    ),
    class_doc=_EPS_CLIP_SWITCHER_CLASS_DOC,
)


# ------------------------------------------------------------ EPSVaeSwitcher

_EPS_VAE_SWITCHER_CLASS_DOC = """Any number of ``vae_N`` inputs, each independently on/off, fanned
into one ``VAE`` list output (FORMAT.md §6.4's mechanism, generalized
from ``EPSSwitcher``'s IMAGE case to VAE via ``_make_switcher_ns`` --
see that class's own docstring, above, for the full historical write-up;
this is the identical shape with "vae" in place of "image").

``INPUT_IS_LIST = True``: a list-producing upstream is merged element-wise
into the output instead of ComfyUI silently re-running this node once per
upstream element with every OTHER input broadcast-repeated. Each
``vae_N`` slot is ALSO ``lazy`` (``check_lazy_status`` below): a
toggled-off slot's upstream -- for example, a checkpoint load feeding a
disabled VAE slot -- is never requested and so never executes at all, a
genuine branch-skip rather than an output-side filter.

Zero enabled VAEs -- everything toggled off, or nothing connected at
all -- is a VALID queue, not an error: ``execute`` returns an
``ExecutionBlocker`` instead of raising, so the queue succeeds and the
downstream VAE branch simply doesn't run for it.

Re-derives the enabled set from ``toggles`` + the connected ``vae_N``
kwargs on every execution -- there is no other state to go stale, so this
node needs no ``IS_CHANGED`` override.
"""

EPSVaeSwitcher = _make_switcher_ns(
    class_id="EPSVaeSwitcher",
    prefix="vae",
    pattern=_VAE_INPUT_PATTERN,
    io_type="VAE",
    output_name="vaes",
    log_prefix="EPS VAE Switcher",
    noun="vae",
    input_tooltip=_VAE_INPUT_TOOLTIP,
    output_tooltip=(
        "Every enabled VAE, in slot order, as a list -- the rest of the "
        "workflow runs once per element."
    ),
    description=(
        "Toggle any number of VAE inputs on or off; the enabled ones fan "
        "out in slot order, so N enabled inputs make the rest of the "
        "workflow run N times. A toggled-off input's upstream branch never "
        "executes at all -- for example, a checkpoint load feeding a "
        "disabled VAE slot never runs, not just filtered out afterward. "
        "Turning every input off, or connecting none at all, is a valid "
        "state: the queue still succeeds and the downstream branch simply "
        "doesn't run. A value wired further downstream that isn't itself a "
        "list, like a fixed seed, repeats identically across every run "
        "unless you give it an explicit per-VAE list."
    ),
    class_doc=_EPS_VAE_SWITCHER_CLASS_DOC,
)
