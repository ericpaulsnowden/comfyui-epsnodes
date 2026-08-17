"""FORMAT.md §7.5: Vue-nodes ("New node design") compatibility pins.

Root-caused on the rig 2026-07-29, chasing the owner's "uptick in issues
using my mac to connect to the linux machine": with `Comfy.VueNodes.Enabled`
on, the frontend renders nodes as Vue DOM instead of running litegraph's
canvas drawing -- so every hand-drawn EPS control (Switcher/Distributor
per-row toggles, Resolution's readout) silently disappears, AND the pack's
hidden plumbing widgets (`toggles`, `grid_uuid`, ...) leaked into the node as
raw editable text fields, because the Vue renderer decides visibility from
the input spec's OPTIONS (`options.hidden`, useProcessedWidgets.ts --
verified in the rig frontend's source maps) and ignores the litegraph
`widget.hidden` flag the frontend sets at attach time. Worse, Vue snapshots
widget options at creation, so a frontend that mutates `options.hidden`
after the fact changes nothing -- the flag has to arrive FROM THE BACKEND
INPUT SPEC.

The first block below therefore asserts on the REAL `INPUT_TYPES()` dicts:
every internal plumbing input carries `"hidden": True`. The classic canvas
renderer ignores that key (its visibility flag is `widget.hidden`, which the
frontend still sets), so this is purely additive.

Two browsers pointed at the SAME server can disagree about render mode (the
frontend nags each browser separately to try the new design, and a long-lived
tab keeps whatever mode it loaded with) -- which is exactly the
works-on-one-machine / broken-on-the-other shape the owner reported.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from eps_image.nodes_checkpoint_switcher import EPSCheckpointSwitcher
from eps_image.nodes_distributor import EPSDistributor
from eps_image.nodes_frame_saver import EPSFrameSaver
from eps_image.nodes_image_grid import EPSImageGrid
from eps_image.nodes_switcher import (
    EPSClipSwitcher,
    EPSModelSwitcher,
    EPSSwitcher,
    EPSVaeSwitcher,
)
from lora_library.nodes_notebook import LoraLibraryNotebook

REPO_ROOT = Path(__file__).resolve().parent.parent


def _input_options(node_class, name: str) -> dict:
    spec = node_class.INPUT_TYPES()
    for section in ("required", "optional"):
        entry = spec.get(section, {}).get(name)
        if entry is not None:
            assert len(entry) >= 2, f"{name} has no options dict to carry the flag"
            return entry[1]
    raise AssertionError(f"{node_class.__name__} has no input named {name!r}")


@pytest.mark.parametrize(
    ("node_class", "input_name"),
    [
        (EPSDistributor, "toggles"),
        (EPSSwitcher, "toggles"),
        (EPSModelSwitcher, "toggles"),
        (EPSClipSwitcher, "toggles"),
        (EPSVaeSwitcher, "toggles"),
        (EPSCheckpointSwitcher, "selection"),
        (EPSImageGrid, "grid_uuid"),
        (EPSFrameSaver, "video_path"),
        (EPSFrameSaver, "frame"),
        (LoraLibraryNotebook, "file"),
        (LoraLibraryNotebook, "entry"),
    ],
)
def test_plumbing_inputs_carry_the_vue_hidden_flag(node_class, input_name: str) -> None:
    """Each internal widget must be hidden in BOTH renderers: the frontend
    sets `widget.hidden` for canvas, and this backend flag covers Vue."""
    options = _input_options(node_class, input_name)
    assert options.get("hidden") is True, (
        f"{node_class.__name__}.{input_name} needs 'hidden': True in its input "
        "options -- without it the widget leaks into Vue nodes as a raw field"
    )


# ---------------------------------------------------------------- JS pins


def _source(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def test_every_frontend_hide_site_sets_both_flags() -> None:
    """Belt to the backend flag's suspenders: wherever the frontend hides a
    widget via `widget.hidden`, it must also set `options.hidden`, so a
    frontend-created widget (the Controller's) and any future renderer that
    tracks live options both stay covered."""
    for rel in (
        "web/eps_image/distributor.js",
        "web/eps_image/switcher.js",
        "web/eps_image/image_grid.js",
        "web/eps_image/frame_saver.js",
        "web/lora_library/notebook.js",
        "web/lora_library/controller.js",
    ):
        source = _source(rel)
        hides = len(re.findall(r"\.hidden = true$", source, re.M))
        options_hides = source.count("hidden: true }") + source.count("hidden: true\n")
        assert hides > 0, f"{rel}: expected at least one hide site"
        assert options_hides >= hides, (
            f"{rel}: {hides} `.hidden = true` site(s) but only {options_hides} "
            "options.hidden mirror(s) -- a hide site is missing its Vue flag"
        )


def test_controller_show_status_toggle_tracks_both_flags() -> None:
    """The one widget whose visibility flips at RUNTIME (Show status): both
    assignments must move both flags, or Vue and canvas disagree after a
    toggle."""
    source = _source("web/lora_library/controller.js")
    for anchor in ("this._w.status.hidden = !value", "this._w.status.hidden = !this.properties"):
        at = source.index(anchor)
        window = source[at : at + 400]
        assert "options" in window and "hidden:" in window, (
            "a Show-status assignment does not mirror options.hidden"
        )


def test_vue_mode_warning_is_wired_and_once_only() -> None:
    """The honest-degradation toast (FORMAT.md §7.5): fires from nodeCreated
    for the hand-drawn-control nodes, once per session, only when the Vue
    setting is actually on, and never throws into the attach chain."""
    source = _source("web/eps_image.js")
    assert "function warnIfVueNodesMode(node)" in source
    assert "safely('vueModeWarning', () => warnIfVueNodesMode(node))" in source
    # Slice from the covered-class set (declared just above the function) so
    # this covers both halves of the guard.
    body = source.split("const VUE_AFFECTED_CLASSES", 1)[1]
    body = body.split("\napp.registerExtension", 1)[0]
    assert "if (vueModeWarned) return" in body, "must fire once per session"
    assert "'Comfy.VueNodes.Enabled'" in body, "must check the real setting key"
    assert "=== true" in body, "an absent settings store must read as OFF"
    for node_type in ("EPSDistributor", "EPSResolution"):
        assert node_type in body, f"{node_type} draws its controls; must be covered"
    # The switcher family (EPSSwitcher + the Model/CLIP/VAE siblings, which
    # share switcher.js's hand-drawn toggles) must be DERIVED from that
    # module's registry, never re-listed here: the hand-written list missed
    # the three siblings when they shipped. `SWITCHER_CLASSES`'s contents are
    # pinned in tests/test_switcher_js.py.
    assert "switcher.SWITCHER_CLASSES" in body, (
        "the switcher family must come from switcher.js's registry, not a "
        "hand-maintained list that a new switcher class can fall out of"
    )


def test_add_buttons_carry_the_user_activation_diagnostic() -> None:
    """The grid Add buttons' one silent client failure mode is input.click()
    being refused without transient user activation. The handler must probe
    `navigator.userActivation` and SAY it (console + toast) when the click
    arrived outside the window -- and must still attempt the pick, so a
    null/absent probe never blocks a working browser."""
    source = _source("web/eps_image/image_grid.js")
    body = source.split("function handleAddButtonClicked(node, openPicker)", 1)[1]
    body = body.split("\nfunction ", 1)[0]
    assert "navigator.userActivation" in body
    assert "activation === false" in body, "only a definite false may warn"
    assert "openPicker(state)" in body.split("activation === false", 1)[1], (
        "the pick must still be attempted after the diagnostic"
    )


# ---------------------------------------------------------------------------
# v0.43.1: window-level pointer listeners must be CAPTURE-phase (owner's Mac
# report 2026-07-30, reproduced on the rig with Vue nodes enabled: the Vue
# node wrapper stops pointer events from BUBBLING to window, so every gesture
# that commits in a plain window listener silently died -- entry clicks never
# selected, drags never dropped, dblclick pairs never completed -- while
# element-level listeners (typing, buttons) kept working. Capture descends
# from the window before any bubble-path stopPropagation can intervene, so
# capture-phase listeners fire in BOTH renderers.)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel",
    ["web/lora_library/notebook.js", "web/eps_image/resolution.js"],
)
def test_window_pointer_listeners_are_capture_phase(rel: str) -> None:
    """Every window.add/removeEventListener for pointer events must pass
    `{ capture: true }` -- and add/remove must MATCH, because a remove without
    the same flag silently fails to detach (a leak that would re-fire stale
    drag handlers forever)."""
    source = _source(rel)
    plain_adds = re.findall(
        r"window\.addEventListener\('pointer\w+', \w+\)", source
    )
    assert not plain_adds, (
        f"{rel}: bubble-phase window pointer listener(s) {plain_adds} -- these "
        "never fire under Vue nodes; use {{ capture: true }}"
    )
    plain_removes = re.findall(
        r"window\.removeEventListener\('pointer\w+', \w+\)", source
    )
    assert not plain_removes, (
        f"{rel}: removeEventListener without the capture flag never detaches "
        f"a capture-phase listener: {plain_removes}"
    )
    cap = r"Listener\('pointer\w+', \w+, \{ capture: true \}\)"
    adds = len(re.findall("window\\.addEvent" + cap, source))
    removes = len(re.findall("window\\.removeEvent" + cap, source))
    assert adds > 0 and removes > 0, f"{rel}: expected capture-phase pairs, found {adds}/{removes}"


def test_controller_target_discovery_does_not_depend_on_drawing() -> None:
    """Owner report 2026-08-14: "the Lora Loader State Controller won't see
    a Power Lora Loader node when dropped into a workflow".

    Discovery rode only on `_heartbeat()`, which runs from
    `onDrawForeground()` -- and that loses the event three separate ways:
    the heartbeat self-throttles to 1/sec (so the draws a drop triggers are
    swallowed, after which an idle canvas never re-triggers it), litegraph
    only draws VISIBLE nodes (an off-screen controller never beats), and
    the Vue renderer never calls onDrawForeground at all. Rig-proven: after
    a drop the combo still read "(none found)" until a draw was forced.

    The fix must be a GRAPH-level add/remove watch -- no draw involved --
    so it works in both renderers."""
    source = _source("web/lora_library/controller.js")
    assert "function installGraphNodeWatch(graph)" in source
    watch = source.split("function installGraphNodeWatch(graph)", 1)[1].split("\n}\n", 1)[0]
    assert "graph.__epsCtrlNodeWatch" in watch, "must install once per graph"
    assert "'onNodeAdded', 'onNodeRemoved'" in watch
    assert "original?.apply(this, args)" in watch, "chained, never replaced"
    assert "scheduleControllerRefresh()" in watch
    # ...installed from onAdded across EVERY graph in the workflow (v0.64.0:
    # subgraph events fire only on the subgraph's own hooks).
    assert "for (const graph of api.walkGraphs(app.graph)) installGraphNodeWatch(graph)" in source


def test_controller_graph_refresh_is_coalesced_per_tick() -> None:
    """`onNodeAdded` fires once per node, so a workflow load would re-scan
    the graph hundreds of times without this. A one-tick deferral, not a
    polling timer (§6.10)."""
    source = _source("web/lora_library/controller.js")
    body = source.split("function scheduleControllerRefresh()", 1)[1].split("\n}\n", 1)[0]
    assert "root.__epsCtrlRefreshQueued" in body
    assert "setTimeout(" in body
    # v0.64.0: the refresh walks the WHOLE workflow (nested controllers
    # refresh too) and arms any newly created subgraph's own hooks.
    assert "for (const graph of api.walkGraphs(root)) installGraphNodeWatch(graph)" in body
    assert "api.walkLiveNodes(root)" in body
    assert "node._refreshTargetCombo()" in body
    assert "node._probeAndUpdateStatus()" in body
    # Never throws into litegraph's own add/remove dispatch.
    assert "api.warn" in body
