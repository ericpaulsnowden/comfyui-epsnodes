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

**Row partners: WAN lows (v0.66.0) and masks.** A class may carry a second
socket under every ``<prefix>_N`` whose value travels WITH that row's
primary -- ``EPSModelSwitcher``'s ``model_N_low`` and ``EPSSwitcher``'s
``mask_N`` -- plus a TAIL output (``models_low`` / ``masks``) that stays
index-aligned with the primary output BY CONSTRUCTION, so a consumer taking
both runs once per row element and can never pair the wrong two. One
``_RowPartner`` spec (below) describes either; the factory's
``check_lazy_status``/``execute`` read it instead of hardcoding the low.
The two differ in exactly three knobs: the mask partner is gated by the
visible ``pass_masks`` BOOLEAN widget (off by default -- owner ask
2026-10-31: "an option that is off by default to also pass along the mask
with every image. the mask and image should always be passed together"),
an unwired mask on an enabled row contributes an all-zero mask shaped like
its image (ComfyUI's own "no mask", what LoadImage emits without an alpha)
where an unwired low contributes per-element ``ExecutionBlocker``s, and a
mask wired with no image on its row is logged and dropped (a mask never
travels alone). With ``pass_masks`` off the ``masks`` output is one
``ExecutionBlocker`` and no zero mask is ever allocated -- a long video
batch would otherwise waste gigabytes on masks nobody consumes.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, NamedTuple

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
#: v1.8.0 (owner ask 2026-10-25: "add an EPS Audio Switcher to the switcher
#: list") -- the fifth parameterization of the same shape.
_AUDIO_INPUT_PATTERN = re.compile(r"audio_(\d+)")

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
#: §6.4 masks (owner ask 2026-10-31: "an option that is off by default to
#: also pass along the mask with every image. the mask and image should
#: always be passed together"). A `mask_N` slot pairs with `image_N`; the
#: row's one toggle governs both, and the visible `pass_masks` widget gates
#: the whole mechanism -- a WIDGET, not a Properties entry, because
#: Properties are unreachable in Nodes 2.0.
_MASK_INPUT_PATTERN = re.compile(r"mask_(\d+)")
_MASK_INPUT_TOOLTIP = (
    "The mask that belongs with this row's image (shown while pass_masks "
    "is on). Leave it unwired to send an empty, all-black mask the size of "
    "the image; the row's one toggle switches image and mask off together."
)
PASS_MASKS_WIDGET_NAME = "pass_masks"
_PASS_MASKS_TOOLTIP = (
    "Off: images only, exactly as before. On: every image row gets a mask "
    "socket under it, and the masks output carries one mask per enabled "
    "image in the same order, so a node that takes both runs once per "
    "image. A row with no mask wired sends an empty, all-black mask the "
    "size of its image. A row's one toggle switches image and mask off "
    "together."
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
_AUDIO_INPUT_TOOLTIP = (
    "An audio clip to include when enabled. Toggle it from the row on the "
    "node; when off, nothing upstream of this socket runs at all (a Load "
    "Audio feeding a switched-off row never even reads its file)."
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


def _flag_is_on(value: Any) -> bool:
    """A BOOLEAN widget value (``pass_masks``), unwrapped like any other
    ``INPUT_IS_LIST`` input (:func:`_unwrap_hidden`'s exact shape), read
    STRICTLY: only a real ``True`` -- or the string ``"true"`` a hand-built
    ``/prompt`` might send -- is on. A missing key, an empty wrapper, ``1``,
    ``"yes"`` and every other truthy value stay OFF, so the default (images
    only, no mask allocation) can never be switched on by accident.
    """
    value = _unwrap_hidden(value)
    if value is True:
        return True
    return isinstance(value, str) and value.strip().lower() == "true"


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
# The MASK extra spec is what lets a workflow wire `mask_N` at all (§6.4
# masks): ComfyUI's validation asks this dict `input_name in optional`, and
# `mask_1` is never a real key -- the frontend grows every mask socket, the
# same way `model_N_low` rides on the model switcher's own flexible dict.
_FlexibleOptionalImageInputs = _make_flexible_optional_inputs_class(
    _IMAGE_INPUT_PATTERN,
    "IMAGE",
    _IMAGE_INPUT_TOOLTIP,
    extra_specs=[(_MASK_INPUT_PATTERN, "MASK", _MASK_INPUT_TOOLTIP)],
)


class _RowPartner(NamedTuple):
    """One switcher class's per-row PARTNER socket (FORMAT.md §6.4, module
    docstring "Row partners"): a second slot under every ``<prefix>_N``
    whose value travels WITH that row's primary, plus the tail output that
    carries the partners index-aligned with the primary output. Two
    instantiations -- the model switcher's WAN ``model_N_low`` (v0.66.0:
    always on, blockers for unwired rows, silent about orphans, byte-for-
    byte the behaviour it shipped with) and the image switcher's ``mask_N``
    (gated by ``pass_masks``, zero masks for unwired rows, orphans logged).
    """

    #: The partner slot's own name shape (``model_(\d+)_low`` / ``mask_(\d+)``);
    #: group 1 is the row number, same as the primary pattern's.
    pattern: re.Pattern[str]
    #: ``str.format`` template for row *n*'s partner key, with ``prefix`` and
    #: ``n`` available: ``"{prefix}_{n}_low"`` / ``"mask_{n}"``.
    name_format: str
    io_type: str
    input_tooltip: str
    output_name: str
    output_tooltip: str
    #: ``str.format`` template for the length-disagreement ValueError, with
    #: ``index``/``primaries``/``partners``/``partner_key`` available. A
    #: silent clamp is exactly the wrong pairing neither WAN nor a
    #: mask-taking node must ever get, so the message is per-class prose.
    pair_error: str
    #: The one-word partner noun for the orphan warning ("mask").
    short_noun: str = ""
    #: Name of the BOOLEAN widget that turns the partner on; ``None`` means
    #: always on (the model switcher's property lives frontend-side only --
    #: its backend always declares both outputs and pairs whatever is wired).
    gate_widget: str | None = None
    gate_tooltip: str = ""
    #: What an UNWIRED partner on an enabled row contributes per primary
    #: element: ``True`` = zeros shaped like the primary (masks -- ComfyUI's
    #: own "no mask"), ``False`` = an ``ExecutionBlocker`` each (lows --
    #: only ``models_low``'s consumers skip that row, §6.5's precedent).
    fill_zeros: bool = False
    #: Log (once per execution) every partner slot wired with no primary on
    #: its row, then drop it. Off for lows (their v0.66.0 silence is kept).
    warn_orphans: bool = False


def _zeros_like_mask(image: Any) -> Any:
    """An all-zero MASK for *image*: IMAGE is ``[B, H, W, C]`` and MASK is
    ``[B, H, W]``, so the first three dims are the mask's shape. Built off
    the tensor itself (``new_zeros`` keeps dtype/device) so this module never
    imports torch -- the module-scope no-comfy/no-torch promise holds. This
    is ComfyUI's own meaning of "no mask": core's LoadImage emits exactly
    such a zero tensor for an image with no alpha channel.
    """
    return image.new_zeros(tuple(image.shape[:3]))


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
    partner: _RowPartner | None = None,
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

    *partner*, when given, is this class's per-row partner socket + tail
    output (:class:`_RowPartner`, module docstring "Row partners"): the
    model switcher's WAN lows, the image switcher's masks.
    """
    if flexible_cls is None:
        extra = (
            [(partner.pattern, partner.io_type, partner.input_tooltip)]
            if partner is not None
            else None
        )
        flexible_cls = _make_flexible_optional_inputs_class(
            pattern, io_type, input_tooltip, extra_specs=extra
        )
    _SWITCHER_SLOT_PATTERNS[class_id] = pattern

    def partner_key_of(index: int) -> str:
        """Row *index*'s partner slot name (``model_3_low`` / ``mask_3``)."""
        assert partner is not None
        return partner.name_format.format(prefix=prefix, n=index)

    def partner_on(kwargs: dict[str, Any]) -> bool:
        """Whether this execution pairs partners at all: no partner spec =
        never; an ungated partner (lows) = always; a gated one (masks) =
        only when its widget arrived as a real true."""
        if partner is None:
            return False
        if partner.gate_widget is None:
            return True
        return _flag_is_on(kwargs.get(partner.gate_widget))

    def INPUT_TYPES(cls) -> dict[str, Any]:
        declared: dict[str, Any] = {
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
        if partner is not None and partner.gate_widget is not None:
            # §6.4 masks: the VISIBLE on/off for the partner mechanism --
            # a plain BOOLEAN widget in `optional` (a hand-built /prompt
            # may omit it; `partner_on` reads an absent key as off), NOT
            # lazy (check_lazy_status needs it before anything resolves,
            # the same reason `toggles` is eager), and APPENDED after
            # `toggles` on purpose: widgets_values restores positionally,
            # so an old save's `["{}"]` lands on `toggles` and this one
            # keeps its False default. No label_on/label_off -- they
            # truncate in Nodes 2.0 on frontend 1.52.7.
            declared[partner.gate_widget] = (
                "BOOLEAN",
                {"default": False, "tooltip": partner.gate_tooltip},
            )
        return {
            "required": {},
            "optional": flexible_cls(declared),
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
        if partner is not None and partner_on(kwargs):
            # §6.4 pairing (v0.66.0 lows; masks follow the same rule): a
            # row's PARTNER slot follows its row's one toggle (keyed by the
            # primary name) -- a disabled row skips both upstreams, so a
            # toggled-off WAN pair loads neither model and a toggled-off
            # image row decodes neither its image nor its mask.
            # ...and only when the PRIMARY itself is wanted (audit
            # 2026-08-21): a high fed by a statically-all-off sibling
            # switcher is in `skip` and never arrives, so requesting its
            # low alone ran the low's upstream for nothing and then failed
            # the 0-high/1-low pairing check in execute(). With the mask
            # gate OFF nothing below runs, so no mask upstream is ever
            # asked for -- a wired mask_N is simply ignored.
            wanted_primaries = set(wanted)
            wanted.extend(
                partner_key_of(index)
                for index in _connected_slot_indices(kwargs, partner.pattern)
                if f"{prefix}_{index}" in wanted_primaries
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
        partner_values: list[Any] = []
        pairing = partner_on(kwargs)

        def _flatten(raw: Any) -> list[Any]:
            if isinstance(raw, (list, tuple)):
                return [element for element in raw if element is not None]
            return [raw]

        if pairing and partner.warn_orphans:
            # §6.4 masks: a mask wired on a row with no image never travels
            # alone -- it has nothing to pair with, so it contributes
            # nothing. Said ONCE per execution, naming every such socket,
            # because a silently dropped wire is the kind of thing that
            # costs an afternoon.
            primaries = set(connected)
            orphans = [
                partner_key_of(index)
                for index in _connected_slot_indices(kwargs, partner.pattern)
                if index not in primaries
            ]
            if orphans:
                logger.warning(
                    "%s: %s wired with no %s on the same row -- a %s never "
                    "travels alone; ignoring it",
                    log_prefix,
                    ", ".join(orphans),
                    noun,
                    partner.short_noun,
                )

        for index in connected:
            key = f"{prefix}_{index}"
            if toggle_map.get(key, True) is False:
                continue
            primaries = _flatten(kwargs[key])
            if not primaries:
                # A skipped/blocked primary (lazy skip set, None upstream)
                # has nothing to pair or emit -- never a 0-vs-N pairing
                # error (audit 2026-08-21).
                continue
            enabled_values.extend(primaries)
            if not pairing:
                continue
            # §6.4 pairing (v0.66.0 WAN lows; masks since the pass_masks
            # round): the partner output stays index-aligned with the
            # primary output BY CONSTRUCTION. Per enabled slot: a wired
            # partner pairs 1:1 with that slot's primaries, a SINGLE
            # partner broadcasts across them (a list-producing image or
            # high with one partner), any other length disagreement is a
            # MISWIRE and fails loudly -- a silent clamp here is exactly
            # the wrong high+low (or image+mask) pairing nobody must ever
            # get. An unwired partner contributes per element either an
            # all-zero mask shaped like its image (masks: ComfyUI's own
            # "no mask", never resized, never altered when wired) or an
            # ExecutionBlocker (lows: only consumers of the LOW output are
            # skipped for that row, Resolution's §6.5 per-slot precedent).
            # A WIRED-but-empty low still goes through the length check
            # (0 vs N fails, as it always has); a wired-but-empty mask is
            # treated like an unwired one -- the brief's "unwired (or
            # empty) mask" -- since there is nothing to pair and zeros are
            # the honest fill.
            partner_key = partner_key_of(index)
            wired = partner_key in kwargs and kwargs[partner_key] is not None
            partners = _flatten(kwargs[partner_key]) if wired else []
            if wired and (partners or not partner.fill_zeros):
                if len(partners) == len(primaries):
                    partner_values.extend(partners)
                elif len(partners) == 1 and primaries:
                    partner_values.extend(partners * len(primaries))
                else:
                    raise ValueError(
                        f"{log_prefix}: "
                        + partner.pair_error.format(
                            index=index,
                            primaries=len(primaries),
                            partners=len(partners),
                            partner_key=partner_key,
                        )
                    )
            elif partner.fill_zeros:
                partner_values.extend(_zeros_like_mask(element) for element in primaries)
            else:
                from comfy_execution.graph import ExecutionBlocker

                partner_values.extend(ExecutionBlocker(None) for _ in primaries)

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
            return (blocked, [ExecutionBlocker(None)]) if partner is not None else (blocked,)

        if partner is None:
            return (enabled_values,)
        if not pairing:
            # Gate off (masks): images exactly as before, and the partner
            # output is ONE blocker -- the same whole-output skip the
            # all-off path uses, so a consumer of `masks` is silently
            # skipped and no zero mask is ever allocated for a row nobody
            # asked a mask of (module docstring: a long video batch would
            # waste gigabytes). Never reached for an ungated partner.
            from comfy_execution.graph import ExecutionBlocker

            return (enabled_values, [ExecutionBlocker(None)])
        return (enabled_values, partner_values)

    paired = partner is not None
    #: §6.16 state registry (v0.83.0): the widgets a Universal State
    #: Controller may capture/apply, declared next to the parser that owns
    #: their shape -- stamped per generated class since ``toggles``'s own
    #: key_pattern is this class's slot prefix (``image_N``/``model_N``/
    #: ``clip_N``/``vae_N``). The unbounded ``<prefix>_N`` sockets
    #: themselves are IMAGE/MODEL/CLIP/VAE-typed, not widgets, and never
    #: appear here.
    state_widgets: dict[str, Any] = {
        "format": 1,
        "widgets": {
            "toggles": {"kind": "json_object", "key_pattern": f"^{prefix}_\\d+$"},
        },
    }
    if partner is not None and partner.gate_widget is not None:
        # §6.4 masks: `pass_masks` is a real, visible widget, so a captured
        # state carries it and an Apply flips it (the frontend converges the
        # sockets off the widget callback the Apply fires).
        state_widgets["widgets"][partner.gate_widget] = {"kind": "boolean"}
    namespace: dict[str, Any] = {
        "__module__": __name__,
        "__doc__": class_doc,
        "CATEGORY": "EPSNodes/Switchers",
        "EPS_STATE_WIDGETS": state_widgets,
        # §8: the partner output is a TAIL append -- existing wires never move.
        "RETURN_TYPES": (io_type, partner.io_type) if paired else (io_type,),
        "RETURN_NAMES": (output_name, partner.output_name) if paired else (output_name,),
        "INPUT_IS_LIST": True,
        "OUTPUT_IS_LIST": (True, True) if paired else (True,),
        "OUTPUT_TOOLTIPS": (
            (output_tooltip, partner.output_tooltip) if paired else (output_tooltip,)
        ),
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

``pass_masks`` (BOOLEAN widget, off by default -- owner ask 2026-10-31)
turns on the row-partner mechanism (module docstring "Row partners"):
every ``image_N`` gains a lazy ``mask_N`` partner, the row's one toggle
governs both, and the tail ``masks`` output carries one mask per enabled
image in the same order -- a wired mask as-is (equal lengths pair, a
single mask broadcasts across a list-producing image, anything else fails
the queue naming the socket), an unwired one as an all-zero mask shaped
like its image. Off, ``images`` is exactly what it always was, ``masks``
is a single ``ExecutionBlocker`` and no zero mask is allocated.
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
        "explicit per-image list. Turn on pass_masks to send each image's "
        "mask along with it: every row gets a mask socket, the masks output "
        "lines up one-to-one with images, a row with no mask wired sends an "
        "empty (all-black) mask the size of its image, and the row's one "
        "toggle switches both off together."
    ),
    class_doc=_EPS_SWITCHER_CLASS_DOC,
    flexible_cls=_FlexibleOptionalImageInputs,
    partner=_RowPartner(
        pattern=_MASK_INPUT_PATTERN,
        name_format="mask_{n}",
        io_type="MASK",
        input_tooltip=_MASK_INPUT_TOOLTIP,
        output_name="masks",
        output_tooltip=(
            "One mask per enabled image, in the same order as images (only "
            "while pass_masks is on; off, this output skips whatever "
            "consumes it). A row with no mask wired sends an empty, "
            "all-black mask the size of its image."
        ),
        pair_error=(
            "row {index} pairs {primaries} image(s) with {partners} mask(s) "
            "-- image and mask must match one for one (or wire a single "
            "mask to broadcast across the row). Fix the wiring on "
            "{partner_key}."
        ),
        short_noun="mask",
        gate_widget=PASS_MASKS_WIDGET_NAME,
        gate_tooltip=_PASS_MASKS_TOOLTIP,
        fill_zeros=True,
        warn_orphans=True,
    ),
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
    partner=_RowPartner(
        pattern=_MODEL_LOW_INPUT_PATTERN,
        name_format="{prefix}_{n}_low",
        io_type="MODEL",
        input_tooltip=_MODEL_LOW_INPUT_TOOLTIP,
        output_name="models_low",
        output_tooltip=(
            "Every enabled row's LOW-noise partner model, index-aligned with "
            "the models output (WAN-style high/low pairs -- see the 'High/low "
            "pairs' property). Rows without a low wired contribute a skip for "
            "this output only."
        ),
        # v0.66.0's exact wording -- pinned by tests/test_switcher.py.
        pair_error=(
            "row {index} pairs {primaries} high model(s) with {partners} low "
            "model(s) -- the two sides of a high/low pair must match (or wire "
            "a single low to broadcast). Fix the wiring on {partner_key}."
        ),
        short_noun="low",
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


# ---------------------------------------------------------- EPSAudioSwitcher

_EPS_AUDIO_SWITCHER_CLASS_DOC = """Any number of ``audio_N`` inputs, each independently on/off,
fanned into one ``AUDIO`` list output (FORMAT.md §6.4b; v1.8.0, owner ask
2026-10-25: "add an EPS Audio Switcher to the switcher list"). The fifth
parameterization of ``_make_switcher_ns`` -- the identical shape as
``EPSSwitcher`` with "audio" in place of "image"; see that class's own
docstring for the full mechanism write-up.

``INPUT_IS_LIST = True``: a list-producing upstream is merged element-wise
into the output. Each ``audio_N`` slot is ALSO ``lazy``: a toggled-off
slot's upstream -- a Load Audio, a text-to-speech or music node feeding a
disabled row -- is never requested and so never executes at all.

Zero enabled clips -- everything toggled off, or nothing connected -- is a
VALID queue: ``execute`` returns an ``ExecutionBlocker``, so the queue
succeeds and the downstream audio branch simply doesn't run. (To feed a
consumer whose audio input is OPTIONAL "nothing" instead -- a video
combine that should just render silent -- use EPS Bypass, FORMAT.md
§6.17: a blocker on any input skips the consumer entirely.)
"""

EPSAudioSwitcher = _make_switcher_ns(
    class_id="EPSAudioSwitcher",
    prefix="audio",
    pattern=_AUDIO_INPUT_PATTERN,
    io_type="AUDIO",
    # The family's `<prefix>s` list naming (images/models/clips/vaes) --
    # this output IS a list, one clip per enabled row.
    output_name="audios",
    log_prefix="EPS Audio Switcher",
    # The log noun reads "no audio inputs are connected" -- the same
    # "no <noun> inputs" phrasing as the siblings.
    noun="audio",
    input_tooltip=_AUDIO_INPUT_TOOLTIP,
    output_tooltip=(
        "Every enabled audio clip, in slot order, as a list -- the rest of "
        "the workflow runs once per clip."
    ),
    description=(
        "Toggle any number of audio inputs on or off; the enabled ones fan "
        "out in slot order, so N enabled clips make the rest of the "
        "workflow run N times. A toggled-off input's upstream branch never "
        "executes at all -- a Load Audio or a speech/music generator "
        "feeding a disabled row never runs, not just filtered out "
        "afterward. Turning every input off, or connecting none at all, is "
        "a valid state: the queue still succeeds and the downstream branch "
        "simply doesn't run (to send 'no audio' into an optional audio "
        "input instead, use EPS Bypass). A value wired further downstream "
        "that isn't itself a list, like a fixed seed, repeats identically "
        "across every run."
    ),
    class_doc=_EPS_AUDIO_SWITCHER_CLASS_DOC,
)
