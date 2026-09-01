"""EPSNumberController (FORMAT.md section 6.17, display: "EPS Number
Controller") -- one node, up to MAX_OUTPUTS (currently 16) independently
named number rows, each a plain number box wired to wherever a workflow
needs a hand-typed number.

The owner's own framing: "It could have any number of outputs. Each output
would have a field attached to it where a user could type in a number. This
would allow all numerical values for a workflow to be saved in one place,
and for those values to all be controlled at the same time with our
universal state controller." So this node is deliberately NOT a fancy
numeric widget -- no min/max, no seed-style randomize, no step/scrub (owner
answer, verbatim: "just a plain number box"). Its entire job is to be one
place many otherwise-scattered numbers can live, so the Universal State
Controller (FORMAT.md §6.16) can save and recall all of them together.

**Untyped until wired, then adopts INT or FLOAT (owner answer).** A row
starts as a plain number with no ComfyUI type. The FRONTEND is what
notices a row got wired to a socket and records that socket's concrete
type (INT/FLOAT) into this node's own `values` widget -- the backend never
inspects the graph to decide this (this module has no `prompt`/`unique_id`
hidden inputs at all, unlike EPSDistributor's wiring-aware `check_lazy_
status`: nothing here is gated, so there is nothing that needs graph
access). The backend's whole job is the last mile: given a row's recorded
`type` and its typed-in `value`, hand back a Python `int` or `float` that
actually behaves like one downstream.

**Coercion is the reason this node exists on the backend at all.**
ComfyUI's own INT-typed sockets/widgets genuinely misbehave when fed a
Python `float` (e.g. a `steps` input landing a `30.0` from an upstream
computation) -- some nodes cast, some silently truncate, some error deep
inside a sampler far from this node. Coercing HERE, once, per the row's
adopted type, means every downstream INT socket gets a real `int` and every
FLOAT socket gets a real `float`, regardless of what the widget happened to
serialize.

**RETURN_NAMES is class-level and therefore generic (`num_1`..`num_16`),
not the user's row names.** ComfyUI reads `RETURN_NAMES` off the CLASS,
once, at node-type registration -- it has no per-instance hook for output
labels. The user's typed name (`values[num_N].name`) is display text the
FRONTEND draws as this instance's socket label (litegraph `output.label`,
the same non-`.name` mechanism EPSDistributor's own renamable outputs use
-- see nodes_distributor.py's "Outputs are renamable" section); the
backend's positional contract underneath never moves. The name also never
feeds `get_numbers`: it exists purely for the on-node display and for
matching a row across machines when the Universal State Controller applies
a saved state to a differently-ordered graph (module docstring's
`name`-is-for-display-and-matching-only rule -- see FORMAT.md §6.16's M3
cross-machine matching writeup for the general mechanism this feeds).

**No gating, so none of EPSDistributor's execution-graph machinery
applies.** Every row always carries a real number -- there is no "off"
state, nothing to block, and no upstream to skip -- so this node declares
no `INPUT_IS_LIST`/`OUTPUT_IS_LIST` (nothing here is a list in the first
place: sixteen independent SCALAR outputs, not one value fanned into many
runs or many runs merged into one), no `ExecutionBlocker` import anywhere,
and no lazy inputs. `get_numbers` always returns a fixed
`MAX_OUTPUTS`-length tuple, one Python number per slot, unconditionally.

**No `IS_CHANGED`.** `values` is an ordinary tracked STRING widget, and
ComfyUI's own prompt-hash caching already includes every widget value in a
node's cache key -- editing any row's number changes `values`, which
changes the hash, which invalidates the cache on its own. There is no
other state here that could go stale (contrast §6.7's EPSFrameSaver, whose
`IS_CHANGED` exists because an on-disk video file can change without the
node's own widgets changing at all -- nothing analogous is true here).

**Bridge widget: `values`, a required hidden STRING, JSON object keyed
`num_1`..`num_MAX_OUTPUTS` (FORMAT.md §6.16's JSON-bridge pattern, same
generic shape EPSDistributor's `toggles` and the Prompt Notebook's
`pinned`/`drafts` widgets use).** Each present key maps to `{"name": str,
"value": <JSON number>, "type": "INT" | "FLOAT" | "*"}`. `required`, not
`optional` -- unlike EPSDistributor's `toggles` (where an ABSENT widget
still has a sensible default: "every output enabled"), `values` IS this
node's entire reason for being; there is no meaningful "everything reads a
sane number" default for a node whose only job is holding hand-typed
numbers, so an API caller who actually wants specific numbers out of this
node must supply them, exactly like the Prompt Notebook's own required
`file`/`entry` widgets (nodes_notebook.py) for the same reason: it is the
node's own essential state, not an optional override of an otherwise
complete default behavior. `default="{}"` still means the node NEVER
fails to run without it -- every row simply reads `0` (see below) --
`required` only affects hand-built `/prompt`s that omit the key entirely at
validation time, not what `get_numbers` does once it runs.

**Degrade table (never raises, one bad row never poisons its neighbours):**
- The whole `values` string fails to parse as JSON, or parses to something
  other than a JSON object -> logged ONCE, every row reads `0` (mirrors
  EPSDistributor's own `_parse_toggles` degrade contract exactly, including
  the blank-input-is-silent case: an empty/`None` `values` is the node's own
  fresh-node default, not a malformed value, so it degrades to `{}` with NO
  warning).
- A row's key (`num_N`) is simply ABSENT from an otherwise-valid `values`
  object -> that row reads `0`, SILENTLY. This is the ordinary steady state
  for every row past however many the user has actually created (the
  frontend only ever writes an entry for a row that exists, the same way
  EPSDistributor's `toggles` only ever names the slots it has overridden) --
  warning on every one of the ~12 unused slots on every single queue would
  be pure log noise for the single most common case this node has, so this
  follows EPSDistributor's own "absent key -> silent default" precedent
  (`_enabled_slots`'s `.get(key, True)`) rather than the letter of "missing
  key -> logger.warning" read as applying even here.
- A row's entry IS present but is malformed in some way that actually
  indicates broken data (not a JSON object at all; its `value` is missing,
  `null`, a bool, a string, or any other non-numeric JSON type; its `value`
  is `NaN`/`Infinity`/`-Infinity`; its `value` is a JSON number too large
  for a Python `float` to represent) -> that row logs a WARNING and reads
  `0`. `type` is never validated this strictly -- an unrecognized `type`
  (anything other than the literal strings `"INT"`/`"FLOAT"`) is treated
  exactly like `"*"` (module docstring's coercion table), silently, since a
  foreign/future type tag is not evidence of corrupted data the way a
  non-numeric `value` is.
- `TypeError`/`ValueError` is the parse catch (mirrors every other JSON
  bridge in this pack): `UnicodeDecodeError` is itself a `ValueError`
  subclass, so a `values` payload handed in as raw undecodable bytes by
  some exotic non-frontend caller degrades exactly like garbled text
  instead of raising.

**Coercion table, per row, once `value` is confirmed a finite non-bool
`int`/`float`:**
- `type == "INT"` -> ``int(round(value))``.
- `type == "FLOAT"` -> ``float(value)``.
- `type == "*"`, absent, or any unrecognized tag (not yet wired) ->
  whichever Python type ROUND-TRIPS EXACTLY: `30` (or `30.0`) stays an
  `int`; `7.5` stays a `float`. This is the one case where the row's OWN
  number decides its Python type, because there is no wired socket to
  defer to yet.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any, ClassVar

logger = logging.getLogger("eps_image")

#: How many num_N sockets this node declares. RETURN_TYPES/RETURN_NAMES are
#: both DERIVED from it, never hand-typed (nodes_distributor.py's own
#: MAX_OUTPUTS comment has the full "why 16, why a hard ceiling, why raising
#: it later is safe" argument -- it applies here verbatim, for the identical
#: reason: outputs resolve POSITIONALLY in a saved link
#: (`[origin_id, origin_slot]` indexed straight into RETURN_TYPES), unlike
#: EPSSwitcher's by-NAME inputs, which is why this can't just grow
#: unbounded the way Switcher's image_N inputs do).
MAX_OUTPUTS = 16

#: Default `values` widget value: no rows recorded yet, so every slot reads
#: as the degrade value (`0`) until the frontend creates a row.
DEFAULT_VALUES = "{}"

#: `num_1`..`num_MAX_OUTPUTS`, the fixed key vocabulary `values` may use.
#: The one place the naming scheme (`num_{n}`) is spelled out; RETURN_NAMES
#: below is built from this SAME tuple, so the widget's keys and the
#: node's socket names can never drift apart.
_SLOT_KEYS = tuple(f"num_{n}" for n in range(1, MAX_OUTPUTS + 1))


def _parse_values(values: Any) -> dict[str, Any]:
    """Best-effort JSON-object parse of the `values` widget value.

    Adapted from (not imported from) nodes_distributor.py's
    `_parse_toggles` -- same degrade contract, own log text, so a malformed
    value reported here always says "EPS Number Controller", never
    misattributes to a sibling node (own-your-helpers precedent, also
    followed by nodes_cross_sweep.py). Never raises: a malformed/foreign
    value degrades to "no rows recorded" -- i.e. every slot reads `0` --
    rather than crashing the node, logging a warning so the cause is
    visible without being fatal. An empty/`None` value is the ordinary
    fresh-node default, not a malformed one, so it is silent.
    """
    if not values:
        return {}
    try:
        parsed = json.loads(values)
    except (TypeError, ValueError) as exc:
        # UnicodeDecodeError is a ValueError subclass, so raw undecodable
        # bytes from an exotic caller degrade the same way as garbled text.
        logger.warning(
            "EPS Number Controller: malformed `values` (%s); every row reads as 0",
            exc,
        )
        return {}
    if not isinstance(parsed, dict):
        logger.warning(
            "EPS Number Controller: `values` was not a JSON object (got %r); "
            "every row reads as 0",
            type(parsed).__name__,
        )
        return {}
    return parsed


def _coerce_slot(slot_key: str, entry: Any) -> int | float:
    """Turn one `values[slot_key]` entry into the Python number its adopted
    `type` calls for -- module docstring's coercion table. Never raises:
    every way an entry can be broken (not an object, a missing/non-numeric/
    NaN/inf/oversized `value`) degrades to `0` plus a logged WARNING, and
    nothing here shares state with any other slot, so one bad row can never
    drag a neighbour down with it.
    """
    if not isinstance(entry, dict):
        logger.warning(
            "EPS Number Controller: %s entry is not an object (got %r); reads as 0",
            slot_key,
            type(entry).__name__,
        )
        return 0

    raw_value = entry.get("value")
    # `bool` is an `int` subclass in Python -- rejected explicitly so a
    # stray JSON `true`/`false` (never something a user typed into a number
    # box) degrades like any other non-numeric value instead of quietly
    # becoming 1/0.
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
        logger.warning(
            "EPS Number Controller: %s `value` is not a number (got %r); reads as 0",
            slot_key,
            raw_value,
        )
        return 0
    try:
        # `math.isnan`/`math.isinf` convert their argument to `float`
        # internally, which is exactly where a huge JSON integer (arbitrary
        # precision in Python, unlike `float`) raises `OverflowError` --
        # caught below alongside the same failure mode in the coercions
        # further down, since both are "this number is too large to
        # represent as a float", not "this number is not finite".
        if math.isnan(raw_value) or math.isinf(raw_value):
            logger.warning(
                "EPS Number Controller: %s `value` is not finite (%r); reads as 0",
                slot_key,
                raw_value,
            )
            return 0

        slot_type = entry.get("type")
        if slot_type == "INT":
            # Deliberately NOT `round()`: Python rounds halves to EVEN
            # (2.5 -> 2 but 3.5 -> 4), which reads as a plain bug to anyone
            # who is not a programmer. This path IS reachable in ordinary
            # use -- a row holds whatever was typed while it was still
            # unwired, so typing 2.5 and only then plugging the row into an
            # INT socket like `steps` arrives here with a fraction -- and
            # school arithmetic says 2.5 goes to 3. Halves therefore round
            # AWAY from zero, symmetrically for negatives (-2.5 -> -3).
            # The panel re-snaps the DISPLAYED number on adoption too, so
            # the box never shows 2.5 while the socket quietly sends 3.
            if isinstance(raw_value, int):
                return raw_value
            if raw_value >= 0:
                return math.floor(raw_value + 0.5)
            return math.ceil(raw_value - 0.5)
        if slot_type == "FLOAT":
            return float(raw_value)
        # "*", absent, or any unrecognized tag: not (yet) wired, or a
        # foreign value from a future frontend -- fall back to whichever
        # Python type round-trips exactly, so an untyped 30 stays an int
        # and an untyped 7.5 stays a float.
        as_float = float(raw_value)
        as_int = int(as_float)
        return as_int if as_int == as_float else as_float
    except OverflowError:
        # A JSON number too large for a Python float (e.g. a huge integer
        # literal) -- pathological, but "never raise" means even this
        # degrades rather than crashing the node.
        logger.warning(
            "EPS Number Controller: %s `value` is too large to represent (%r); reads as 0",
            slot_key,
            raw_value,
        )
        return 0


class EPSNumberController:
    """Any number of named, plain-number rows in one node, each adopting
    INT or FLOAT once wired (class docstring's framing; see the module
    docstring for the full rationale behind every choice below).

    RETURN_TYPES/RETURN_NAMES are both built from MAX_OUTPUTS -- never
    hand-typed -- so the two can never drift apart in length.
    `get_numbers` always returns a fixed-length MAX_OUTPUTS-tuple of plain
    Python `int`/`float` values, one per slot, unconditionally: there is no
    "off" state and nothing to gate, unlike EPSDistributor's per-slot
    `ExecutionBlocker`.
    """

    CATEGORY = "EPSNodes"
    # "*" (matches EPSDistributor's own contract): the socket is
    # type-agnostic server side -- ComfyUI's own `validate_node_input`
    # accepts `*` on either side of a link, so an INT- or FLOAT-typed
    # downstream input both validate cleanly. The FRONTEND is what narrows
    # each wired row's adopted type and feeds it back into `values`; the
    # backend's job is only to coerce the NUMBER, never to police the wire.
    RETURN_TYPES = ("*",) * MAX_OUTPUTS
    # Class-level and therefore generic -- see module docstring's
    # "RETURN_NAMES is class-level" section for why this can never carry
    # the user's own row names: ComfyUI reads RETURN_NAMES off the CLASS
    # once, at registration, with no per-instance hook. The frontend sets
    # the visible socket LABEL per instance instead (litegraph
    # `output.label`, not `.name`), the same mechanism EPSDistributor's
    # renamable outputs use.
    RETURN_NAMES = _SLOT_KEYS
    OUTPUT_TOOLTIPS = (
        "This row's number -- a whole number or one with a decimal, "
        "whichever kind the socket you wire it into expects.",
    ) * MAX_OUTPUTS
    FUNCTION = "get_numbers"
    DESCRIPTION = (
        "One place to keep every number your workflow uses -- steps, cfg, "
        "a batch count, anything you'd otherwise type into a dozen "
        "separate nodes. Each row is just a name and a plain number box. "
        "Wire a row into a socket and it quietly becomes the right kind of "
        "number for that socket -- a whole number or one with a decimal -- "
        "you never have to choose. A new row appears as you use the last "
        f"one, up to {MAX_OUTPUTS}. Because every number lives on this one "
        "node, the EPS Universal State Controller can save and recall the "
        "whole set together, even across different computers."
    )

    #: §6.16 state registry: the widget a Universal State Controller may
    #: capture/apply, declared next to the parser that owns its shape.
    EPS_STATE_WIDGETS: ClassVar[dict[str, Any]] = {
        "format": 1,
        "widgets": {
            "values": {"kind": "json_object", "key_pattern": r"^num_\d+$"},
        },
    }

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                # `required`, not `optional` -- module docstring's "Bridge
                # widget" section has the full rationale (mirrors
                # nodes_notebook.py's own required `file`/`entry`: this IS
                # the node's entire state, not an optional override of an
                # otherwise-complete default). `default="{}"` still means
                # the node never fails to run without it -- every row just
                # reads 0.
                #
                # "hidden": True is the VUE-nodes ("New node design") hide
                # flag (2026-07-29, same as every other internal bridge
                # widget in this pack -- see nodes_distributor.py's
                # `toggles` for the full citation): that renderer decides
                # widget visibility from the input spec's options
                # (`options.hidden`, useProcessedWidgets.ts) and IGNORES the
                # litegraph `widget.hidden` the frontend sets -- without
                # this, this internal widget leaks into Vue nodes as a raw
                # editable field. The classic canvas renderer ignores this
                # key right back, so it changes nothing there.
                "values": (
                    "STRING",
                    {
                        "default": DEFAULT_VALUES,
                        "multiline": False,
                        "hidden": True,
                        "tooltip": (
                            "Every row's name, number, and adopted type, "
                            "maintained automatically by the panel -- you "
                            "don't need to edit this directly."
                        ),
                    },
                ),
            },
        }

    def get_numbers(self, values: str = DEFAULT_VALUES) -> tuple[Any, ...]:
        """Return one coerced Python number per `num_N` slot, in order,
        always MAX_OUTPUTS long.

        Every failure mode degrades to `0` for just that slot (module
        docstring's degrade table) -- this never raises, and one bad row
        never affects any other.
        """
        parsed = _parse_values(values)
        results: list[int | float] = []
        for slot_key in _SLOT_KEYS:
            entry = parsed.get(slot_key)
            if entry is None:
                # Absent (or explicit JSON `null`) -- the ordinary steady
                # state for every row past however many the user has
                # actually created (module docstring's degrade table: this
                # mirrors EPSDistributor's own "absent key -> silent
                # default" rule instead of warning on every unused slot on
                # every single queue).
                results.append(0)
                continue
            results.append(_coerce_slot(slot_key, entry))
        return tuple(results)
