"""EPSDistributor (FORMAT.md section 6.11, display: "EPS Distributor") -- one
image in, MAX_OUTPUTS (currently 8) independently-gated images out.

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
caching -- there is no other state that could go stale), and no
lazy/check_lazy_status (Switcher's per-input lazy skip exists to stop an
upstream from running at all when its slot is disabled; here there is only
ONE input, and it is always needed to produce ANY enabled output, so there
is nothing to conditionally avoid resolving -- the branch-skip this node
provides is entirely on the OUTPUT side, via ExecutionBlocker, never on the
input side).

Toggle bridge: semantics identical to Switcher's toggles widget (own local
_parse_toggles/DEFAULT_TOGGLES below, adapted rather than imported -- see
their docstrings for why: a malformed value logs as "EPS Distributor" here,
never misattributed to the sibling node; this also keeps the two node
modules independent of each other, the same way nodes_cross.py owns its own
small helpers instead of reaching into nodes_switcher.py for conceptually
similar ones). A JSON object {"out_N": false, ...}, keyed out_1..out_8. A
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

#: Fixed output count for this M1 design (roadmap "Decisions to confirm" #1:
#: fixed MAX=8 with trailing reveal, chosen over a true-growable output list
#: so this node reuses the toggle-bridge machinery outright; a growable
#: version is M2-only, if 8 ever chafes). RETURN_TYPES/RETURN_NAMES below are
#: both DERIVED from this constant, never hand-typed, so they can never
#: drift out of sync in length.
MAX_OUTPUTS = 8

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
    """One image in, MAX_OUTPUTS (currently 8) independently-gated IMAGE
    outputs out, keyed out_1..out_8 (FORMAT.md section 6.11).

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
    FUNCTION = "distribute"
    DESCRIPTION = (
        "One image in, up to 8 independently-toggleable images out: a tee with a "
        "per-branch gate. Every out_N carries the same image unless its own slot "
        "is toggled off, in which case that socket silently blocks execution and "
        "only the branch wired to it is skipped -- the other branches see the "
        "real image and run normally, all in one pass (this node never re-runs "
        "the rest of the graph the way EPS Switcher's fan-out does). Turning "
        "every output off is a valid state: the queue still succeeds, nothing "
        "downstream of this node's outputs runs. An out_N with nothing wired to "
        "it is unaffected either way."
    )

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "image": ("IMAGE",),
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
        }

    def distribute(self, image: Any, toggles: str = DEFAULT_TOGGLES) -> tuple[Any, ...]:
        """Fan `image` out to MAX_OUTPUTS sockets, gated per-slot by `toggles`.

        A slot is enabled unless `toggles` names it (out_1..out_8) and the
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
        toggle_map = _parse_toggles(toggles)
        enabled = [
            toggle_map.get(f"out_{n}", True) is not False for n in range(1, MAX_OUTPUTS + 1)
        ]

        if all(enabled):
            # Nothing disabled -- skip the import entirely; every slot below
            # always just carries the same `image` reference.
            return (image,) * MAX_OUTPUTS

        # Lazy import: keeps this module importable with no ComfyUI on the
        # path (module docstring's "no torch/ComfyUI import" promise; see
        # tests/test_distributor.py's test_module_never_imports_comfy_or_torch).
        from comfy_execution.graph import ExecutionBlocker

        return tuple(image if slot_enabled else ExecutionBlocker(None) for slot_enabled in enabled)
