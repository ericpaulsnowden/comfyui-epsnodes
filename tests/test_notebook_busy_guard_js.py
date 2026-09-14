"""Regression pins for the Prompt Notebook panel's own mutation-request
serialization (RELEASE-REVIEW-2026-09-13.md finding 3's client-side half).

The review's finding was a SERVER-side race (routes_notebook.py, fixed by
``lora_library.path_locks``): two mutation requests loaded from the same
``base_mtime`` could both land as 200s. Locking the server means a second
*overlapping* request from the SAME panel would now get an honest 409
instead of silently "succeeding" -- which would be a regression for a
panel that legitimately fires two overlapping requests against itself
(e.g. a rename immediately followed by a move, before the first response
returns).

Investigation (see this session's report for the full trace) found the
panel already serializes every mutating action through one shared
``state.busy`` flag, set synchronously before any ``await`` at every real
UI entry point (``performMove``, ``performMoveCategory``,
``commitInlineRename``, ``performSave``/``performSaveCategory``,
``onDeleteClick``), so no code path today can fire two overlapping
mutation requests from one panel. Two siblings, `performMoveRun`
(multi-select drag-move) and `performDeleteRun` (multi-select delete),
were the ONE inconsistency: unlike every other performX function they did
NOT check `state.busy` themselves, relying entirely on their (today's
only) callers already having checked it -- latent, not live, but fragile
against a future call site that doesn't happen to gate the same way.
These pins lock that consistency in.

``notebook.js`` is DOM/closure-bound (no pure seam to drive under Node the
way ``test_distributor_js.py`` drives ``toggleBoxRect``), so -- same
convention ``test_notebook_restore_js.py``/``test_frame_saver_paste_js.py``
established -- these are SOURCE-TEXT pins, not executed behavior. Live
behavior is verified on the rig.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
NOTEBOOK_JS = REPO_ROOT / "web" / "lora_library" / "notebook.js"


@pytest.fixture(scope="module")
def source() -> str:
    return NOTEBOOK_JS.read_text(encoding="utf-8")


def _function_body(source: str, signature: str) -> str:
    """The text of one top-level `async function <signature>` / `function
    <signature>` definition, up to (not including) the next top-level
    `function` -- same slicing convention
    ``test_notebook_restore_js.py``/``test_notebook_delete_category_js.py``
    already use for pinning one closure's body."""
    marker = f"function {signature}"
    assert marker in source, f"{marker!r} not found in notebook.js"
    block = source.split(marker, 1)[1]
    return block.split("\nfunction ", 1)[0]


def test_perform_move_checks_busy_before_starting(source: str) -> None:
    """The single-entry mover already had this guard; pinned so it can
    never quietly regress alongside its siblings below."""
    body = _function_body(source, "performMove(state, name, target, { force = false } = {}) {")
    assert "if (state.busy) return" in body


def test_perform_move_category_checks_busy_before_starting(source: str) -> None:
    body = _function_body(
        source, "performMoveCategory(state, category, target, { force = false } = {}) {"
    )
    assert "if (state.busy) return" in body


def test_perform_move_run_checks_busy_before_starting(source: str) -> None:
    """finding 3 hardening: performMoveRun (the multiselect drag-move,
    finishDrag's other branch) used to skip this check entirely, unlike
    performMove above -- fragile even though today's only call site
    (finishDrag, gated by onEntryPointerDown's onMove) never actually
    reaches it while busy."""
    body = _function_body(source, "performMoveRun(state, names, target, { force = false } = {}) {")
    assert "if (state.busy) return" in body


def test_perform_delete_run_checks_busy_before_starting(source: str) -> None:
    """finding 3 hardening: performDeleteRun used to rely ENTIRELY on its
    only caller (onDeleteClick) having already checked `state.busy`."""
    body = _function_body(source, "performDeleteRun(state, names, { force = false } = {}) {")
    assert "if (state.busy) return" in body


def test_busy_is_set_before_any_await_in_every_mutating_entry_point(source: str) -> None:
    """The invariant the whole "no phantom conflict" analysis rests on:
    every mutating entry point sets `state.busy = true` SYNCHRONOUSLY --
    before its own first `await` -- so no other code can observe
    `state.busy === false` in the gap between "decided to mutate" and "the
    request is actually in flight". Checked for every function this file's
    review covered; a function that awaited something before setting busy
    would reopen exactly the race this whole round closed.
    """
    for signature in (
        "performMove(state, name, target, { force = false } = {}) {",
        "performMoveCategory(state, category, target, { force = false } = {}) {",
        "performMoveRun(state, names, target, { force = false } = {}) {",
        "performDeleteRun(state, names, { force = false } = {}) {",
    ):
        body = _function_body(source, signature)
        busy_index = body.index("state.busy = true")
        first_await_index = body.index("await ")
        assert busy_index < first_await_index, (
            f"{signature!r}: `state.busy = true` must precede the first `await`"
        )
