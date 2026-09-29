"""EPSBypass (FORMAT.md section 6.18, display: "EPS Bypass") -- a pass-through
you can switch OFF so that, to whatever it feeds, *nothing is connected*.

The owner's ask (v0.99.0): "A node you can plug something into (like audio),
switch it off, and it looks to the downstream node like nothing is
connected." Muting the source node was tried first and did not do it.

**Why the node UNPLUGS instead of blocking (decided, not open).** The
obvious backend answer is an `ExecutionBlocker` on the off path, and it is
wrong for this job. Core's per-node dispatch (`execution.py`'s
`process_inputs`) walks EVERY resolved input of a node and skips the node
outright the moment ANY one of them is an `ExecutionBlocker` -- required or
optional, no distinction. So a blocked AUDIO branch feeding a video node's
optional `audio` input would not produce "a silent video"; it would skip the
whole video node (and everything downstream of it). The only thing that makes
a consumer run "as if nothing were connected" is the link genuinely not
existing in the submitted prompt, which is a FRONTEND act
(`web/eps_image/bypass.js`: on toggle-off it remembers this node's output
targets in the hidden `links` widget and disconnects them; on toggle-on it
reconnects exactly those). That is legal exactly when the consumer's input is
OPTIONAL -- an unconnected REQUIRED input fails validation with "Required
input is missing" -- so the frontend refuses the switch-off (and says why)
when any target input is required. Everything about that lives in the
frontend; see FORMAT.md section 6.18 for the full rule set.

**What the backend still has to do.** Two things, both deliberately tiny:

- Enabled: hand the input straight back (`(value,)`, the very same object,
  no copy). An UNWIRED input arrives as `None` (an absent optional input),
  and `None` is passed through as-is: that is what an optional consumer's own
  default already is, so a muted/absent upstream still looks like "nothing"
  to it -- returning a blocker here would skip the consumer, the exact
  failure this node exists to avoid.
- Disabled but somehow EXECUTED: return the silent `ExecutionBlocker(None)`,
  never the value. This is the REFUSED-UNPLUG FALLBACK path, not the normal
  one: with the wire gone the node has no consumer and core never runs it at
  all. It runs only when a wire is still attached while the toggle says off --
  an API caller that never loaded the frontend, or a frontend that could not
  unplug (a required-input target, an unverifiable one) -- and in that case
  "off" must still mean off. A blocker there skips the consumer instead of
  leaking the value through an off switch, and `None` for the message keeps
  it silent (a string would surface as an error in the UI).

**One wildcard in, one wildcard out** -- the same `"*"` technique
`nodes_distributor.py` documents (v0.75.0 section): core's
`validate_node_input` returns `True` the moment either side of a link is
`"*"`, so an AUDIO/IMAGE/MODEL link into the input and a `*` output into a
typed downstream input both validate at queue time. The FRONTEND is what
adopts the concrete wired type onto both sockets (so they read AUDIO, IMAGE,
... and litegraph then rejects a mismatched later connection by itself).
The input is in `optional`, so a Bypass with nothing plugged in still
validates.

**Widget order is a persistence contract (FORMAT.md section 8).**
`widgets_values` restores POSITIONALLY: `enabled` (visible BOOLEAN) first,
then `links` (hidden JSON STRING) -- hidden state last, so a later widget can
only ever append after it. `value` is a socket, not a widget, and never
occupies a `widgets_values` slot. Do not reorder.

`links` is the frontend's private memory, `{"owner": <this node's id>,
"links": [{"node": <id>, "input": "<name>"}, ...]}` (the Number Controller's
remembered-targets list, the same helpers, plus the `owner` that lets a
pasted copy's memory be recognised as stale), and the backend NEVER reads it
-- it only has to exist so it serialises with the workflow. It is
deliberately EXCLUDED from the section 6.16 state registry: a captured state
that carried it could overwrite the live memory with a stale (often empty)
list while the node is off, and the next switch-on would have nothing to
reconnect. Only `enabled` is captured; applying a state replays the same
unplug/replug as a click.

No `IS_CHANGED` (both inputs are ordinary tracked inputs already in core's
cache key), no list flags (a scalar tee -- like the Distributor, it maps
per element when an upstream fans out), no lazy inputs (an off node with no
wire is never executed, and a still-wired one is the rare fallback path).

No torch/ComfyUI import at module scope: `ExecutionBlocker` is imported
lazily, only on the disabled path, exactly like the Distributor's -- so this
module stays importable in a bare test environment.
"""

from __future__ import annotations

from typing import Any, ClassVar

#: Default of the hidden `links` widget: no remembered targets.
DEFAULT_LINKS = "{}"


class EPSBypass:
    """One optional wildcard input, one wildcard output, a visible on/off
    toggle, and a hidden memory of the wires the frontend unplugged (module
    docstring for why the switch works by unplugging, not blocking)."""

    CATEGORY = "EPSNodes/Utilities"
    RETURN_TYPES = ("*",)
    RETURN_NAMES = ("output",)
    OUTPUT_TOOLTIPS = (
        "The input, unchanged -- or, when switched off, nothing at all: the "
        "wire to whatever this fed is unplugged, so it behaves as if "
        "nothing were connected.",
    )
    FUNCTION = "bypass"
    DESCRIPTION = (
        "Plug anything in -- audio, an image, a model, a mask -- and switch "
        "it off: whatever it feeds then behaves exactly as if nothing were "
        "connected to that input, and the wire comes back when you switch it "
        "on. Works for inputs that are optional (a video node's audio, an "
        "optional mask, a reference image). If something it feeds needs this "
        "input to run, switching off is refused and the message says which "
        "one. Unlike a node that blocks its output (which makes ComfyUI "
        "skip everything downstream), this one takes the wire out, so the "
        "node it feeds simply runs without that input."
    )

    #: §6.16 state registry: only the visible toggle is captured. `links` is
    #: the frontend's own wire memory -- see the module docstring for why a
    #: state must never carry it. The `boolean` kind is new with this node
    #: (v0.99.0); the registry's closed kind set gained it for exactly this
    #: widget.
    EPS_STATE_WIDGETS: ClassVar[dict[str, Any]] = {
        "format": 1,
        "widgets": {
            "enabled": {"kind": "boolean"},
        },
        "excluded": {
            "links": (
                "The frontend's private memory of the wires it unplugged; "
                "capturing it would let a state overwrite the live memory "
                "(often with an empty list) while the node is off, leaving "
                "nothing to reconnect on the next switch-on."
            ),
        },
    }

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                # FIRST widget, visible. `label_on`/`label_off` are the
                # toggle's own on/off text in BOTH renderers (the canvas
                # draws `options.on/off`, the Vue node draws the same pair
                # as its two segments), so "off" is spelled out on the node
                # itself with no drawing code of ours -- the wire is already
                # gone, so the toggle text is the loudest thing left.
                "enabled": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "label_on": "on",
                        "label_off": "off — sends nothing",
                        "tooltip": (
                            "On: the input passes straight through. Off: the "
                            "wire to whatever this feeds is unplugged, so "
                            "it behaves as if nothing were connected. "
                            "Refused (and the toggle snaps back on) if what "
                            "it feeds needs this input to run."
                        ),
                    },
                ),
            },
            "optional": {
                # A socket, not a widget: no `widgets_values` slot. Named
                # `value`, not after any one type -- it adopts whatever gets
                # wired (module docstring). The NAME is frozen: inputs
                # restore BY NAME (FORMAT.md section 8).
                "value": (
                    "*",
                    {
                        "tooltip": (
                            "Anything: audio, an image, a model, a mask... "
                            "The first thing you wire sets the type."
                        ),
                    },
                ),
                # SECOND widget, hidden, LAST (FORMAT.md section 8: hidden
                # state trails the visible widgets). `"hidden": True` is the
                # Vue-nodes hide flag (2026-07-29 -- see
                # nodes_distributor.py's `toggles` for the citation): that
                # renderer reads visibility from the input spec's options
                # and ignores the litegraph `widget.hidden` the frontend
                # sets. In `optional` so a hand-built /prompt that omits it
                # still validates; the backend never reads it.
                "links": (
                    "STRING",
                    {
                        "default": DEFAULT_LINKS,
                        "multiline": False,
                        "hidden": True,
                        "tooltip": (
                            "Which wires were unplugged when this was "
                            "switched off, so switching on can put them "
                            "back. Maintained automatically -- don't edit."
                        ),
                    },
                ),
            },
        }

    def bypass(
        self,
        enabled: bool = True,
        value: Any = None,
        links: str = DEFAULT_LINKS,
    ) -> tuple[Any, ...]:
        """Pass `value` straight through when enabled; a silent
        `ExecutionBlocker` when disabled (the refused-unplug fallback --
        module docstring).

        `links` is accepted only so it can arrive as a kwarg; it is never
        read. `enabled` is disabled for any FALSY value except an absent
        (`None`) one: a real BOOLEAN widget always has a value, so `None`
        can only mean an API caller that left it out, and "on" is the
        least-surprising default for that (the Distributor's rule).
        """
        if enabled is not None and not enabled:
            # Lazy import -- module docstring's no-comfy-at-import promise
            # (tests/test_bypass.py pins it).
            from comfy_execution.graph import ExecutionBlocker

            return (ExecutionBlocker(None),)
        return (value,)
