"""EPSDistributor (FORMAT.md section 6.11, display: "EPS Distributor") -- one
image in, MAX_OUTPUTS (currently 16) independently-gated images out.

The mirror of EPSSwitcher (nodes_switcher.py), pointed backwards. Switcher is
many toggleable INPUTS fanned into one gathered output (N enabled inputs ->
N downstream RUNS of the rest of the graph). This node is the opposite
shape: ONE input tee'd onto many toggleable OUTPUTS, each socket
independently carrying either the same image or a silent execution block,
all delivered in a SINGLE run. Plug one LoadImage in the front, wire the
same picture to an upscale branch, a restyle branch, and a save branch off
three of the out_N sockets, and flip any of them off from this one node --
no rewiring, no bypassing groups by hand.

Do NOT copy Switcher's OUTPUT_IS_LIST here -- it is the wrong shape for this
node. Switcher declares it because N enabled INPUTS become N downstream RUNS
of the rest of the graph (list-execution fan-out); this node never fans
anything into multiple runs -- it always executes exactly once and hands
back one fixed-length tuple, one value per output SOCKET, each wired to its
own PARALLEL branch. Declaring OUTPUT_IS_LIST here would tell core each of
those values is itself a list meant for a further list-execution fan-out,
which is not what a plain per-branch tee means. Likewise no INPUT_IS_LIST
(nothing fans IN either -- image and toggles are each consumed exactly once,
never merged with anything upstream), no IS_CHANGED (both inputs are
ordinary tracked inputs already covered by ComfyUI's default input-hash
caching -- there is no other state that could go stale).

`image` IS lazy: `check_lazy_status` requests it only when some ENABLED slot
actually has a CONSUMER wired (owner question 2026-07-27 -- "if this is at
the end of a workflow, and all of the checkboxes are off, should the
workflow run up to this point?" -- and, same day, his failure report that
the first fix missed: a workflow restored from disk replays a `toggles`
that names only the VISIBLE slots, so the hidden ones read as enabled and a
toggles-only rule ran his whole KSampler chain to feed sockets nothing
consumes). Declining is a real branch skip: an upstream that is never
REQUESTED is never added to the execution graph at all
(`comfy_execution/graph.py`'s `TopologicalSort.add_node` `is_lazy` branch),
same mechanism as EPSSwitcher's disabled slots. `_wired_slots` has the
wiring-aware rationale and the fallback contract (unreadable graph -> the
conservative any-slot-enabled rule).

This stays inside §6.4's hard rule -- "graph inspection may change what a
node REQUESTS, never what it RETURNS" -- because the decision reads only
`toggles`, an ordinary tracked input that is part of this node's cache key,
never the graph. And what `distribute` RETURNS in the all-off case is a full
tuple of blockers whether or not `image` was ever resolved: the value is genuinely
unused on that path, so declining it cannot change the result, only the
work done to reach it. Flipping any toggle changes `toggles`, which
invalidates the cache entry, so the next run re-decides from scratch.

Toggle bridge: semantics identical to Switcher's toggles widget (own local
_parse_toggles/DEFAULT_TOGGLES below, adapted rather than imported -- see
their docstrings for why: a malformed value logs as "EPS Distributor" here,
never misattributed to the sibling node; this also keeps the two node
modules independent of each other, the same way nodes_cross.py owns its own
small helpers instead of reaching into nodes_switcher.py for conceptually
similar ones). A JSON object {"out_N": false, ...}, keyed out_1..out_MAX. A
slot is ENABLED unless its key is present and EXPLICITLY the boolean false
-- a non-bool falsy value (null/0/"") or an absent key both mean enabled,
matching Switcher's "is not False" rule exactly (same rationale: the
least-surprising default for the "ComfyUI-only must work" floor -- a
hand-edited workflow or a non-frontend API caller that has never heard of
this widget should still get every output). Malformed JSON, or valid JSON
that parses to something other than an object, both degrade to "every
output enabled" plus a logged warning, never an exception -- Switcher's
exact degradation contract.

toggles lives in "optional", NOT "required", for the same reason documented
on Switcher's own toggles entry: ComfyUI's own prompt validation rejects a
hand-built /prompt that omits any REQUIRED input before the node ever runs
(there is no backend default-fill at validation time), which would make a
plain API caller who has never loaded any frontend JS unable to queue this
node at all. Putting it in "optional" lets distribute's own
toggles=DEFAULT_TOGGLES default cover the omitted case, so the no-frontend
API path keeps working.

Unlike Switcher, toggles is never list-wrapped here: this node declares no
INPUT_IS_LIST, so core resolves an ordinary widget input to a plain scalar
before distribute ever sees it (execution.py's _async_map_node_over_list:
the per-call slice_dict picks a single element out of each input's internal
list for any node that does not opt into INPUT_IS_LIST). No _unwrap_toggles
counterpart is needed or provided here.

Per-slot ExecutionBlocker -- the load-bearing mechanism, verified against
this repo's own rig ComfyUI checkout (~/Library/Application
Support/comfy_ps/rig/ComfyUI, v0.28.0): distribute always returns a
fixed-length MAX_OUTPUTS-tuple, one element per out_N, and NEVER a bare
ExecutionBlocker by itself. That distinction is exactly what makes a
PER-SLOT block possible instead of an all-or-nothing one: execution.py's
get_output_from_returns only rewrites a node's return value into "every
output blocked" when the WHOLE return value handed to it IS an
ExecutionBlocker instance -- "if isinstance(r, ExecutionBlocker): r =
tuple([r] * len(obj.RETURN_TYPES))" (execution.py:394-395). Our return
value is already a tuple, never an ExecutionBlocker itself, so that
expansion never fires, and the tuple -- blockers in some slots, the image in
others -- is passed straight through untouched one line later,
"results.append(r)" (execution.py:396). Each disabled slot's blocker then
does its actual job at the CONSUMER: core's own per-node dispatch
(_async_map_node_over_list's process_inputs) walks every resolved input of
a downstream node and blocks that node outright the moment ANY one of them
is an ExecutionBlocker instance. So a disabled out_N blocks only the branch
actually wired to it -- a sibling branch wired to a different, ENABLED slot
sees the real image and runs normally in the very same pass, and a disabled
slot with nothing wired to it is never inspected by anything, hence
harmless. All-off (every slot disabled) is therefore a valid, unexceptional
state -- MAX_OUTPUTS blockers, no error -- mirroring Switcher's own all-off
decision, just distributed per-slot instead of collapsed into one shared
list element.

No torch/ComfyUI import anywhere at module scope: ExecutionBlocker is
imported lazily inside EPSDistributor.distribute, only on the path that
actually needs it (at least one disabled slot), exactly like Switcher's own
all-off branch -- so this module stays importable in a bare test
environment with no ComfyUI on the path (see tests/test_distributor.py's
test_module_never_imports_comfy_or_torch).
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("eps_image")

#: How many out_N sockets this node declares. RETURN_TYPES/RETURN_NAMES are
#: both DERIVED from it, never hand-typed, so they can never drift in length.
#:
#: Raised 8 -> 16 on 2026-07-29 (owner: "EPS Distributor should have more
#: than three outputs. Just like EPS Switcher the number of nodes needs to be
#: able to grow"). Growth is now automatic in the frontend -- wiring the last
#: visible socket reveals the next one, the same feel as EPSSwitcher's
#: growing inputs -- but the CEILING is real and lives here, for a reason
#: that is a ComfyUI constraint rather than a choice:
#:
#: EPSSwitcher's inputs can be genuinely unbounded because inputs are
#: resolved BY NAME -- `_FlexibleOptionalImageInputs.__getitem__` synthesizes
#: `image_N` for any N on demand. OUTPUTS are resolved POSITIONALLY: a link
#: records `[origin_id, origin_slot]` and core indexes that straight into
#: this class's RETURN_TYPES tuple, which is read once at registration. There
#: is no per-instance output count in the node API, so "declare as many as
#: anyone will plausibly wire" is the only shape available. 16 is that
#: number; raising it later is safe (see below) if it ever chafes.
#:
#: Raising this is APPEND-ONLY and therefore safe for saved workflows: new
#: sockets land after the existing ones, so no already-recorded origin_slot
#: shifts (the same trailing-only rule section 6.11's hide/reveal follows).
#: Old saves whose `toggles` only mention the first eight leave the new ones
#: reading "enabled" by absence -- harmless, because `check_lazy_status` is
#: wiring-aware (an enabled socket with no consumer never forces the
#: upstream) and the frontend re-records hidden slots as off on load.
MAX_OUTPUTS = 16

#: Default `toggles` widget value: no overrides recorded yet, so every
#: output slot is enabled (module docstring's toggle-bridge section).
DEFAULT_TOGGLES = "{}"


def _parse_toggles(toggles: str) -> dict[str, Any]:
    """Best-effort JSON object parse of the `toggles` widget value.

    Adapted from (not imported from) nodes_switcher.py's function of the
    same name: same degradation contract, own log text, so a malformed
    value reported here always says "EPS Distributor", never misattributes
    to the sibling node. Never raises: a malformed/foreign value (a
    hand-edited workflow, an API caller sending garbage) degrades to "no
    overrides recorded" -- i.e. every output enabled -- rather than
    crashing the node, logging a warning so the cause is visible without
    being fatal.
    """
    if not toggles:
        return {}
    try:
        parsed = json.loads(toggles)
    except (TypeError, ValueError) as exc:
        logger.warning(
            "EPS Distributor: malformed `toggles` value (%s); treating every "
            "output as enabled",
            exc,
        )
        return {}
    if not isinstance(parsed, dict):
        logger.warning(
            "EPS Distributor: `toggles` was not a JSON object (got %r); "
            "treating every output as enabled",
            type(parsed).__name__,
        )
        return {}
    return parsed


class EPSDistributor:
    """One image in, MAX_OUTPUTS (currently 16) independently-gated IMAGE
    outputs out, keyed out_1..out_MAX_OUTPUTS (FORMAT.md section 6.11).

    RETURN_TYPES/RETURN_NAMES are both built from MAX_OUTPUTS -- never
    hand-typed -- so the two can never drift apart in length. distribute
    always returns a fixed-length MAX_OUTPUTS-tuple regardless of how many
    slots are enabled: for each slot, the SAME image object (identity-
    preserving passthrough, no copy) when enabled, else a fresh
    comfy_execution.graph.ExecutionBlocker(None) when disabled (module
    docstring's "Per-slot ExecutionBlocker" section has the full mechanism
    plus citations). Every out_N disabled is a valid, non-exceptional state
    -- MAX_OUTPUTS blockers, nothing raised -- mirroring EPSSwitcher's own
    all-off decision.

    No IS_CHANGED: image and toggles are both ordinary tracked inputs
    already covered by ComfyUI's default input-hash caching, and the
    enabled set is re-derived from toggles fresh on every call -- there is
    no other state that could go stale.
    """

    CATEGORY = "EPSNodes"
    RETURN_TYPES = ("IMAGE",) * MAX_OUTPUTS
    RETURN_NAMES = tuple(f"out_{n}" for n in range(1, MAX_OUTPUTS + 1))
    OUTPUT_TOOLTIPS = (
        "The input image, or a silent block if this output's toggle is off.",
    ) * MAX_OUTPUTS
    FUNCTION = "distribute"
    DESCRIPTION = (
        "One image in, up to 8 independently-toggleable images out -- a tee "
        "with a per-branch gate. Every out_N carries the same image unless "
        "its own toggle is off, in which case only the branch wired to "
        "that socket is skipped; the rest still see the image and run "
        "normally, all in a single pass. Turning every output off is a "
        "valid state: the queue still succeeds, and nothing downstream of "
        "this node runs. Unlike EPS Switcher, this node never re-runs the "
        "workflow multiple times -- every enabled branch runs once, "
        "together."
    )

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                # `lazy` so `check_lazy_status` below can decline it when every
                # slot is off (module docstring). Core reads this flag straight
                # off the input's options dict, exactly as it does for
                # EPSSwitcher's own `image_N` inputs; `lazy` is orthogonal to
                # required/optional.
                "image": (
                    "IMAGE",
                    {
                        "lazy": True,
                        "tooltip": (
                            "The image to distribute to every enabled "
                            "output. If every output is toggled off, this "
                            "input is never even requested upstream."
                        ),
                    },
                ),
            },
            "optional": {
                # In `optional`, NOT `required` (module docstring): a hand-built
                # /prompt that omits `toggles` must still validate -- ComfyUI's
                # own prompt validation rejects a /prompt missing any REQUIRED
                # input before the node ever runs, which would break the
                # no-frontend API path. `distribute`'s own
                # `toggles=DEFAULT_TOGGLES` default covers the omitted case.
                "toggles": ("STRING", {"default": DEFAULT_TOGGLES, "multiline": False}),
            },
            # For `check_lazy_status`'s wiring-aware REQUEST decision only --
            # never for what `distribute` RETURNS (the section 6.4 rule; see
            # `_wired_slots`). EPSSwitcher carries the same pair for the same
            # reason. Unlike Switcher, this node has no INPUT_IS_LIST, so
            # these arrive as PLAIN values, not one-element lists.
            "hidden": {"prompt": "PROMPT", "unique_id": "UNIQUE_ID"},
        }

    def _enabled_slots(self, toggles: str) -> list[bool]:
        """Per-slot enabled flags, `out_1`..`out_MAX_OUTPUTS`.

        Shared by `distribute` and `check_lazy_status` so the two can never
        disagree about which slots are on -- a disagreement would mean
        declining `image` while some slot still expects to emit it.
        """
        toggle_map = _parse_toggles(toggles)
        return [toggle_map.get(f"out_{n}", True) is not False for n in range(1, MAX_OUTPUTS + 1)]

    @staticmethod
    def _wired_slots(prompt: Any, unique_id: Any) -> set[int] | None:
        """1-based `out_N` numbers with at least one consumer wired, read
        from the hidden `prompt` graph -- or ``None`` when the graph can't
        be read (missing/malformed prompt, an exotic caller), which every
        caller must treat as "assume everything is wired" (the conservative
        fallback: at worst the upstream runs unnecessarily, never the
        reverse).

        Why this exists (owner failure report 2026-07-27, reproduced): the
        original all-off skip trusted `toggles` to describe every slot, but
        the backend declares MAX_OUTPUTS slots and an absent key means
        ENABLED -- so a workflow whose saved `toggles` only covered the
        visible three (any graph saved before the frontend recorded hidden
        slots, and any restore path that replays such a value) left every
        hidden socket "enabled", and `check_lazy_status` dutifully ran a
        whole KSampler chain to feed sockets that don't exist on the node
        and can't be wired to anything. The frontend recording hidden slots as off is now only a
        belt; THIS is the floor: an enabled slot with no consumer never
        justifies resolving `image`, no matter what `toggles` says or which
        client wrote it.

        Section 6.4's hard rule holds: graph inspection may change what a
        node REQUESTS, never what it RETURNS. This feeds only the REQUEST
        decision in `check_lazy_status`; `distribute`'s return value stays a
        pure function of `toggles` + `image`. (EPSSwitcher's
        `_slots_fed_by_an_empty_switcher` is the precedent, docstring and
        all -- including the live-proven disaster writeup for why the
        OUTPUT must never learn the graph.)

        Tolerant of one-element list wrapping on either hidden value (core
        only wraps under INPUT_IS_LIST, which this node doesn't declare --
        guarded anyway so a core behavior change degrades to the safe
        fallback rather than a crash).
        """
        if isinstance(prompt, list) and len(prompt) == 1:
            prompt = prompt[0]
        if isinstance(unique_id, list) and len(unique_id) == 1:
            unique_id = unique_id[0]
        if not isinstance(prompt, dict) or unique_id is None:
            return None
        me = str(unique_id)
        if me not in prompt:
            return None
        wired: set[int] = set()
        for node in prompt.values():
            inputs = node.get("inputs") if isinstance(node, dict) else None
            if not isinstance(inputs, dict):
                continue
            for value in inputs.values():
                # A link is `[origin_id, origin_slot]`; anything else is a
                # widget value.
                if (
                    isinstance(value, list)
                    and len(value) == 2
                    and str(value[0]) == me
                    and isinstance(value[1], int)
                    and 0 <= value[1] < MAX_OUTPUTS
                ):
                    wired.add(value[1] + 1)
        return wired

    def check_lazy_status(
        self,
        image: Any = None,
        toggles: str = DEFAULT_TOGGLES,
        prompt: Any = None,
        unique_id: Any = None,
    ) -> list[str]:
        """Request `image` only if some ENABLED slot is actually WIRED.

        Returning `[]` tells core nothing further is needed, so a lazy
        input's producer is never promoted into the execution graph and the
        whole upstream chain is skipped -- the same real branch-skip
        EPSSwitcher gets for its disabled slots. Returning `["image"]` is
        safe to repeat: core only actually requests a name that is still
        unresolved and keeps calling until nothing new is needed
        (`comfy.comfy_types.node_typing.CheckLazyMixin`).

        `_wired_slots` has the wiring-aware rationale (and the owner failure
        it fixes); an unreadable graph degrades to the old any-slot-enabled
        rule, never the other way.
        """
        enabled = self._enabled_slots(toggles)
        wired = self._wired_slots(prompt, unique_id)
        if wired is None:
            return ["image"] if any(enabled) else []
        return ["image"] if any(enabled[n - 1] for n in wired) else []

    def distribute(
        self,
        image: Any,
        toggles: str = DEFAULT_TOGGLES,
        prompt: Any = None,
        unique_id: Any = None,
    ) -> tuple[Any, ...]:
        """Fan `image` out to MAX_OUTPUTS sockets, gated per-slot by `toggles`.

        A slot is enabled unless `toggles` names it (out_1..out_MAX) and the
        value is EXPLICITLY the boolean `false` -- an absent key, or a
        present-but-non-bool-falsy value (null/0/""), both mean enabled
        (matches EPSSwitcher's own `is not False` rule; see the module
        docstring). Malformed/non-object `toggles` degrades to "every slot
        enabled" via `_parse_toggles`.

        Returns a fixed MAX_OUTPUTS-length tuple every time, never shorter:
        each element is `image` itself (same object, no copy) when that slot
        is enabled, else a fresh `ExecutionBlocker(None)` -- see the module
        docstring's "Per-slot ExecutionBlocker" section for why a blocker in
        ONE tuple slot blocks only the branch wired to it instead of this
        node's whole output.
        """
        enabled = self._enabled_slots(toggles)

        if image is None:
            # Only reachable when `check_lazy_status` declined `image` --
            # i.e. no ENABLED slot has a consumer. Block EVERY slot rather
            # than letting `None` into the tuple: an enabled-but-unwired
            # slot has nothing reading it today, and `prompt` sits in this
            # node's input signature, so any rewiring that gives such a slot
            # a consumer re-executes the node fresh (image then genuinely
            # requested) instead of replaying a cached `None` into a live
            # wire. NOTE this branch reads only its INPUT being None -- the
            # return value never consults the graph (section 6.4).
            from comfy_execution.graph import ExecutionBlocker

            return tuple(ExecutionBlocker(None) for _ in range(MAX_OUTPUTS))

        if all(enabled):
            # Nothing disabled -- skip the import entirely; every slot below
            # always just carries the same `image` reference.
            return (image,) * MAX_OUTPUTS

        # Lazy import: keeps this module importable with no ComfyUI on the
        # path (module docstring's "no torch/ComfyUI import" promise; see
        # tests/test_distributor.py's test_module_never_imports_comfy_or_torch).
        from comfy_execution.graph import ExecutionBlocker

        return tuple(image if slot_enabled else ExecutionBlocker(None) for slot_enabled in enabled)
