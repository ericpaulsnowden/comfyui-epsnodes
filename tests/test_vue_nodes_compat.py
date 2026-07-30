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

from eps_image.nodes_distributor import EPSDistributor
from eps_image.nodes_frame_saver import EPSFrameSaver
from eps_image.nodes_image_grid import EPSImageGrid
from eps_image.nodes_switcher import EPSSwitcher
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
        (EPSImageGrid, "grid_uuid"),
        (EPSFrameSaver, "video_path"),
        (EPSFrameSaver, "frame"),
        (LoraLibraryNotebook, "file"),
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
    body = source.split("function warnIfVueNodesMode(node)", 1)[1]
    body = body.split("\napp.registerExtension", 1)[0]
    assert "if (vueModeWarned) return" in body, "must fire once per session"
    assert "'Comfy.VueNodes.Enabled'" in body, "must check the real setting key"
    assert "=== true" in body, "an absent settings store must read as OFF"
    for node_type in ("EPSSwitcher", "EPSDistributor", "EPSResolution"):
        assert node_type in body, f"{node_type} draws its controls; must be covered"


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
