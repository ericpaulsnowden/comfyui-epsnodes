"""Regression pins for the Prompt Notebook's workflow-RESTORE path
(FORMAT.md §7.2 — owner report 2026-07-27: "Every time I load a workflow on
my Linux machine I have to re-select the location of my Notebook .md file on
the server. And it doesn't take until after recreate node > reset widget
values").

Two defects, one vicious loop, both reproduced live on the real
``app.loadGraphData`` path before fixing:

1. **The panel loaded the wrong file.** ``attachNotebookWidget`` fires
   ``reloadNow()`` immediately, but litegraph restores ``widgets_values``
   LAST — after the node is constructed and added — so at attach time the
   ``file`` widget still held its backend DEFAULT. The panel therefore
   loaded the default file's entries, and ``configure()`` then wrote the
   saved path into the widget *without* firing its callback (litegraph
   assigns ``widget.value`` directly). Result: ``state.file`` (what the
   panel shows) and ``fileWidget.value`` (what a Run actually reads)
   disagreed permanently — the node ran the right file while displaying the
   wrong one.

2. **Re-picking the same path did nothing**, which is what made it a loop
   rather than an annoyance: ``setFileWidgetValue`` early-returned whenever
   the chosen path equalled the widget's current value — and after a load it
   always did. The only escape was recreating the node so the values
   differed again, exactly the workaround the owner found.

``notebook.js`` is DOM/closure-bound (no pure seam to drive under Node the
way ``test_distributor_js.py`` drives ``toggleBoxRect``), so these are
SOURCE-TEXT pins in the convention ``test_frame_saver_paste_js.py``
established for exactly that case. The live behavior is verified on the rig.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
NOTEBOOK_JS = REPO_ROOT / "web" / "lora_library" / "notebook.js"


@pytest.fixture(scope="module")
def source() -> str:
    return NOTEBOOK_JS.read_text(encoding="utf-8")


def test_attach_installs_the_post_configure_reload(source: str) -> None:
    # Without this call the restore path has nothing that re-reads the file
    # after `configure()` lands — defect 1 returns silently.
    assert "wireConfigureReload(state)" in source
    assert "function wireConfigureReload(state)" in source


def test_post_configure_reload_wraps_on_configure(source: str) -> None:
    """`onConfigure` is the only hook that fires AFTER widgets_values are
    restored (for both a whole-workflow load and a pasted/cloned node)."""
    block = source.split("function wireConfigureReload(state)", 1)[1]
    block = block.split("\nfunction ", 1)[0]
    assert "node.onConfigure" in block
    # Must chain, never replace: other wiring (and core) may already own it.
    assert "originalOnConfigure" in block
    assert "reloadNow(state)" in block


def test_post_configure_reload_is_conditional_on_a_real_mismatch(source: str) -> None:
    # Reloading unconditionally on every configure would re-fetch on paste,
    # undo, and every workflow load even when nothing changed.
    block = source.split("function wireConfigureReload(state)", 1)[1]
    block = block.split("\nfunction ", 1)[0]
    assert "restored !== state.file" in block


def test_post_configure_reload_resyncs_the_remote_guard_baseline(source: str) -> None:
    """`wireFileWidget` captures `lastKnownFileValue` at ATTACH time (the
    default). For a remote viewer (`isLocal === false`) the read-only guard
    reverts edits back to that captured value — so leaving it stale would let
    a remote browser silently rewrite a loaded workflow's saved path back to
    the default."""
    block = source.split("function wireConfigureReload(state)", 1)[1]
    block = block.split("\nfunction ", 1)[0]
    assert "state.lastKnownFileValue = restored" in block


def test_same_value_reselect_reloads_instead_of_no_oping(source: str) -> None:
    """Defect 2's pin: an equal-value pick must still reload when the panel
    is displaying a DIFFERENT file. Without this, Browse…-picking the path
    already in the widget is a dead click — the loop the owner hit."""
    block = source.split("function setFileWidgetValue(state, value)", 1)[1]
    block = block.split("\n// ---", 1)[0]
    assert "if (widget.value === value)" in block
    assert "state.file !== value" in block
    assert "reloadNow(state)" in block


def test_same_value_reselect_still_avoids_pointless_refetches(source: str) -> None:
    # When the panel already shows that file, the equal-value path must stay
    # a no-op — otherwise every redundant pick hits the network.
    block = source.split("function setFileWidgetValue(state, value)", 1)[1]
    block = block.split("\n// ---", 1)[0]
    # the early return survives; it's now guarded, not removed
    assert "return" in block.split("state.file !== value", 1)[1][:400]
