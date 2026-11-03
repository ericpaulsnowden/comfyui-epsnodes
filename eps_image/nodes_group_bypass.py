"""EPSGroupBypass (FORMAT.md section 6.20, display: "EPS Group Bypass") --
an on/off switch for a whole GROUP of nodes, which a boolean wire can drive.

The owner's ask (2026-11-02): "Is there a group bypass switch that takes an
input like a boolean? One that I could control from the EPS Number node?"
Nothing installed did: rgthree's Fast Groups Bypasser toggles groups by
click only, and its Mode Repeater copies another node's MODE, not a value.
The agreed design: an on/off switch, a group dropdown, a `mute` switch
(off = muted instead of bypassed), and the switch's own input socket, so an
EPS Number Controller BOOLEAN row (or any on-canvas toggle) can drive it.

**Why this node never runs (decided, not open).** ComfyUI fixes every node's
mode BEFORE a run starts: the frontend leaves muted nodes out of the prompt
and wires bypassed ones through. A value computed DURING a run therefore
cannot bypass anything in that same run. So the whole job is FRONTEND
(`web/eps_image/group_bypass.js`): it reads the switch -- or, when the
switch's socket is wired, the value sitting on the canvas at the other end
of that wire (a Number Controller row, a primitive) -- and sets the mode of
every node inside the chosen group whenever the wanted state changes. The
group flips the moment you flip the switch, before you queue.

This backend class exists so the node has a real type with real sockets: the
BOOLEAN widgets give a Number Controller row a typed socket to adopt, the
state registry can capture the switches, and an API caller sees the node in
`/object_info`. It has no outputs and is not an output node, so core's
`validate_prompt` (which walks only from output nodes) never validates or
executes it -- which is also why the `group` combo may hold names the
backend has never heard of.

**Widget order is a persistence contract (FORMAT.md section 8).**
`widgets_values` restores POSITIONALLY: `enabled`, `mute`, `group`, then the
hidden `group_id` -- hidden state last, so a later widget can only append.
`group` is the visible label; `group_id` is the group's litegraph id, which
is what the frontend actually targets (a rename or a duplicate title must
not lose or confuse the target). Do not reorder.
"""

from __future__ import annotations

from typing import Any, ClassVar

#: The `group` combo's value when no group is chosen.
NO_GROUP = "(none)"
#: Default of the hidden `group_id` widget: no group chosen.
DEFAULT_GROUP_ID = ""


class EPSGroupBypass:
    """Two switches and a group picker; the frontend does the rest (module
    docstring)."""

    CATEGORY = "EPSNodes/Utilities"
    RETURN_TYPES: ClassVar[tuple] = ()
    FUNCTION = "noop"
    DESCRIPTION = (
        "Switch a whole group of nodes on or off. Pick the group, then flip "
        "the switch: off bypasses every node in the group (or mutes them, "
        "with 'mute' on). Wire a boolean into the switch -- an EPS Number "
        "Controller row set to 0 or 1, or any toggle -- to drive it from "
        "there. The group changes as soon as the switch does, before you "
        "run, so what you see on the canvas is what will run."
    )

    #: §6.16 state registry: the two switches are captured; the group choice
    #: is wiring, not a setting.
    EPS_STATE_WIDGETS: ClassVar[dict[str, Any]] = {
        "format": 1,
        "widgets": {
            "enabled": {"kind": "boolean"},
            "mute": {"kind": "boolean"},
        },
        "excluded": {
            "group": (
                "Which group this controls is part of the workflow's layout; "
                "a saved state must not retarget the switch."
            ),
            "group_id": (
                "The frontend's id for the chosen group; it only means "
                "something in the workflow it was picked in."
            ),
        },
    }

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "enabled": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "label_on": "on",
                        "label_off": "off",
                        "tooltip": (
                            "On: the group's nodes run. Off: they are "
                            "bypassed (or muted, with 'mute' on). Wire a "
                            "boolean in -- an EPS Number Controller row "
                            "(0 or 1) or any toggle -- to drive it."
                        ),
                    },
                ),
                "mute": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": (
                            "Off: switching the group off BYPASSES its nodes "
                            "(their inputs pass straight through). On: it "
                            "MUTES them (they and anything needing them "
                            "don't run)."
                        ),
                    },
                ),
                # The options are filled by the frontend from the live
                # graph's groups (module docstring: never validated here).
                "group": (
                    [NO_GROUP],
                    {
                        "default": NO_GROUP,
                        "tooltip": (
                            "The group this switch controls. This node is "
                            "never switched off itself, even inside the "
                            "group, and neither is whatever drives it."
                        ),
                    },
                ),
            },
            "optional": {
                # Hidden, LAST (FORMAT.md section 8). `"hidden": True` is the
                # Vue-nodes hide flag (see nodes_bypass.py's `links`).
                "group_id": (
                    "STRING",
                    {
                        "default": DEFAULT_GROUP_ID,
                        "multiline": False,
                        "hidden": True,
                        "tooltip": (
                            "The chosen group's id, so a rename keeps the "
                            "target. Maintained automatically -- don't edit."
                        ),
                    },
                ),
            },
        }

    def noop(self, **_kwargs: Any) -> tuple:
        """Never called by core (no outputs, not an output node); defined so
        the class is a complete node."""
        return ()
