"""``EPSImageGrid`` (FORMAT.md §6.6, display: "EPS Image Grid") -- a
pass-through recorder: whatever is wired to `image` ALWAYS flows straight
through to the output; `Collect` mode ALSO records it into a disk-backed
buffer that grows across separate Runs and survives a ComfyUI restart;
`Emit` mode fans the whole buffer back out, with whatever's currently wired
appended at the end.

M1 (`research/roadmap-eps-image-grid.md`): Collect/Emit toggle, the buffer
itself, the free thumbnail grid, Clear, and per-node identity/dedup (the
frontend half, `web/eps_image/image_grid.js`). M2 (also that same file)
adds copy/paste. The 2026-07-22 owner-reported fixes below (output-panel
pollution + flow-through) reshape `run()`'s OUTPUT contract without
touching any of that -- the on-disk buffer format, `grid_uuid`
identity/dedup, and Clear are all unchanged.

No torch/ComfyUI import anywhere at module scope -- `run()` only reaches
into `image_grid_store` (which itself lazily imports
`folder_paths`/`torch`/`numpy`/`PIL`, see that module's docstring), so this
stays importable in a plain test environment with neither installed.
`_expand_to_frames` below needs no import either -- `image_batch[i:i+1]`/
`.shape[0]` are plain tensor operations, not torch-specific ones, so there
was no genuine need to move this into ``image_grid_store`` (which stays
untouched by this round of fixes).

**Execution model** (roadmap-eps-image-grid.md "Design guardrails" --
verified directly against this repo's ComfyUI checkout, `execution.py`):
`OUTPUT_NODE = True` makes the node execute even with nothing wired
downstream (the `SaveImage`/`PreviewImage` precedent -- `validate_prompt`
only requires an execution path when there's an `OUTPUT_NODE` to reach), and
`IS_CHANGED` returning `float("nan")` defeats ComfyUI's own input-hash
caching (`NaN != NaN`; confirmed as the SAME sentinel `execution.py`'s own
`IsChangedCache` falls back to on an `IS_CHANGED` exception, `node["is_changed"]
= float("NaN")`) so caching never skips a Run. Together: exactly one
execution -- at most one append of the current batch -- per queued prompt,
in EITHER mode; `Emit` simply skips the append.

**2026-07-22 owner fixes -- output-panel pollution + flow-through:**

1. **"Run a grid with 10 images through an image editor: I see 10 new
   images AND the 10 originals in the generated-output panel, every run."**
   Cause: `run()` used to report `ui.images` = the WHOLE buffer on EVERY
   execution, so core's own per-run `/history` bookkeeping (what the
   generated-images panel renders) accumulated the entire buffer again on
   every single Run. Fix: `ui.images` now reports ONLY the refs THIS run
   actually appended to disk (the `SaveImage` convention -- "this run
   produced these"): `Collect` with nothing newly appended (no image wired,
   or an invalid/not-yet-minted `grid_uuid`) omits the `"ui"` key ENTIRELY,
   and `Emit` NEVER includes one (it never appends). Verified against
   `execution.py`: a return dict with no `"ui"` key means
   `get_output_from_returns` never populates `uis`, so `output_ui` stays
   `{}` and `len(output_ui) > 0` (the SAME condition that guards both the
   `ui_outputs[unique_id]` cache write AND the `"executed"` websocket send)
   is false -- no `"executed"` event reaches the frontend at all for that
   Run. The on-node thumbnail grid can therefore no longer be driven by the
   execution result alone -- `image_grid.js` now refreshes it independently
   from `GET /eps_image_grid/list` on this node's own execution-complete
   signal (see that file for exactly which event carries that signal, and
   why the `"executed"` event alone isn't sufficient).
2. **"Load Image -> Grid (10 buffered): running should end with 11 -- the
   wired node should always flow through; the mode decides whether it's
   collected or whether the grid emits the other collected images."**
   Cause: `run()` used to always emit the WHOLE buffer downstream
   regardless of mode, and outright ignored whatever was wired in `Emit`
   mode. Fix -- see the class docstring and `run()` below for the exact new
   tee (`Collect`) / fan-out-plus-tail (`Emit`) split.

**Empty-buffer safety (a deliberate, verified addition beyond FORMAT.md
§6.6's literal "emit empty lists" text -- see final report for the flag to
amend that section).** A bare `([], [], [])` result is only safe when
NOTHING is wired downstream. Traced directly against this repo's
`execution.py`: for any downstream node with ANY other, non-list input (a
plain widget value -- e.g. `SaveImage.filename_prefix` -- which is virtually
every node), `get_input_data` wraps that widget value as a length-1 list,
so `max_len_input = max(0, 1, ...) = 1`, and the per-index loop's
`slice_dict` (`{k: v[i if len(v) > i else -1] for k, v in d.items()}`)
evaluates `v[-1]` on OUR empty `images` list -- `IndexError`, crashing the
whole queue. This is the EXACT failure mode `EPSSwitcher` already discovered
and fixed for the identical reason (`nodes_switcher.py`'s module docstring
"All-off / none-connected"): its fix -- return a list holding a single,
silent `ExecutionBlocker(None)` instead of a bare `[]` -- is reused here,
applied to all three output slots, whenever THIS run has nothing at all to
emit (`Collect`: nothing wired; `Emit`: an empty buffer AND nothing wired)
-- so that case is silently skipped (a normal, successful queue) rather
than crashing. A genuinely bare graph (nothing wired downstream at all) was
never at risk either way -- there is no other node's inputs for an empty
list to break.
"""

from __future__ import annotations

from typing import Any

from . import image_grid_store as store

CATEGORY_NAME = "EPSNodes"

#: FORMAT.md §6.6 — user-facing, stable identifiers (widget values persist
#: in saved workflows; don't rename these once shipped).
MODE_COLLECT = "Collect"
MODE_EMIT = "Emit"
MODES = [MODE_COLLECT, MODE_EMIT]

#: `optional` (not `required`) default for the hidden identity bridge —
#: mirrors `nodes_switcher.py`'s `toggles` rationale: a hand-built `/prompt`
#: that omits it must still run. An empty/unset uuid simply can't reach any
#: buffer (`image_grid_store.buffer_dir` returns `None` for it), so the node
#: degrades to "no buffer yet" rather than erroring.
DEFAULT_GRID_UUID = ""


def _expand_to_frames(image_batch: Any) -> list:
    """A `[B,H,W,C]` batch -> a list of B `[1,H,W,C]` slices, in order.

    The same per-frame indexing convention `image_grid_store.append_batch`
    already uses internally (`batch_len = int(image_batch.shape[0])`, one
    entry per index) — reimplemented here rather than imported from the
    store, since this is pure tensor slicing with no filesystem/PNG-encoding
    concerns of its own (see module docstring's no-import note; a slice
    `image_batch[i:i+1]` keeps the leading batch dim as size-1, so no
    `torch`/`numpy` import is needed just to reshape it). `None` (nothing
    wired) -> `[]`.
    """
    if image_batch is None:
        return []
    batch_len = int(image_batch.shape[0])
    return [image_batch[i : i + 1] for i in range(batch_len)]


class EPSImageGrid:
    """Wire an image loader in; whatever's wired ALWAYS flows straight
    through to the output, in both modes — `mode` only decides whether it's
    also recorded, or whether the grid's whole collection is fanned out
    alongside it (owner: "the node plugged into the front should always
    flow through; the mode decides whether it's collected or whether the
    grid emits the other collected images").

    - `Collect`: records the wired input into a buffer that grows across
      separate Runs and survives a restart (no cap — you manage disk use,
      Clear wipes it; the buffer shows as a thumbnail grid on the node,
      kept current by `image_grid.js`'s own execution-complete refresh, NOT
      by this Run's downstream result — see module docstring point 1).
      Downstream only gets THIS Run's just-recorded frame(s) — a tee, not a
      fan-out of the whole buffer. Nothing wired -> nothing to pass, an
      `ExecutionBlocker` triple (module docstring "Empty-buffer safety").
    - `Emit`: records nothing. Downstream gets the WHOLE buffer, in the
      order it was recorded, with whatever's CURRENTLY wired appended as
      the final image(s) (buffer of 10 + 1 wired -> 11, the rest of the
      workflow runs 11 times). An empty buffer with nothing wired either ->
      the same `ExecutionBlocker` triple.

    Re-reads the on-disk buffer on every execution — there is no in-memory
    state to go stale between Runs (a second EPSImageGrid instance pointed
    at the same `grid_uuid`, or the buffer being Cleared by another means,
    is always reflected immediately).
    """

    CATEGORY = CATEGORY_NAME
    RETURN_TYPES = ("IMAGE", "INT", "INT")
    RETURN_NAMES = ("image", "width", "height")
    OUTPUT_IS_LIST = (True, True, True)
    OUTPUT_NODE = True
    FUNCTION = "run"
    DESCRIPTION = (
        "EPS Image Grid -- wire a loader in; whatever's wired ALWAYS flows "
        "straight through to the output. In Collect mode, each Run ALSO "
        "records that input into a buffer that grows across separate Runs "
        "and survives a restart (no cap -- you manage disk use, Clear wipes "
        "it); the buffer shows as a thumbnail grid on the node, but only "
        "the image(s) you just fed in continue downstream this Run -- "
        "Collect does not replay everything collected so far. Switch to "
        "Emit and Run once to fan the WHOLE buffer out downstream, paired "
        "with its own width/height, with whatever's currently wired "
        "appended as the final image(s) -- 10 buffered + 1 wired -> the "
        "rest of the workflow runs 11 times. A scalar value (e.g. a seed) "
        "wired further downstream repeats identically across all runs."
    )

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "mode": (MODES, {"default": MODE_COLLECT}),
            },
            "optional": {
                "image": ("IMAGE",),
                # Hidden serialized bridge to this node's per-instance
                # identity (module docstring; `image_grid.js` generates +
                # dedups this into BOTH `node.properties.uuid` and this
                # widget, the same `EPSSwitcher.toggles` hidden-widget trick
                # FORMAT.md §7.2 already uses for the Prompt Notebook's
                # `file`). In `optional`, not `required` — see
                # DEFAULT_GRID_UUID's docstring.
                "grid_uuid": ("STRING", {"default": DEFAULT_GRID_UUID, "multiline": False}),
            },
        }

    @classmethod
    def IS_CHANGED(cls, **_kwargs: Any) -> float:
        # Always "changed" -- see module docstring's execution-model note.
        return float("nan")

    def run(
        self,
        mode: str = MODE_COLLECT,
        image: Any = None,
        grid_uuid: str = DEFAULT_GRID_UUID,
    ) -> dict[str, Any]:
        live = _expand_to_frames(image)
        #: Refs THIS run actually appended to disk — the ONLY thing `ui`
        #: ever reports (module docstring point 1). Stays `[]` (and `"ui"`
        #: is therefore omitted below) unless Collect mode both had
        #: something wired AND a valid `grid_uuid` to append it to; Emit
        #: mode never touches this at all.
        new_refs: list[dict] = []

        if mode == MODE_COLLECT:
            if live:
                all_refs = store.append_batch(grid_uuid, image)
                # `append_batch` always returns the WHOLE buffer, in append
                # order, with a fresh append landing at the tail (tested:
                # test_image_grid_store.py's
                # test_second_call_continues_numbering_and_returns_the_whole_buffer)
                # -- so the frames THIS call just appended are exactly its
                # last `len(live)` entries. `all_refs` is `[]` for an
                # invalid/not-yet-minted `grid_uuid` (a safe no-op, that
                # function's own docstring) -- slicing an empty list is
                # itself a safe `[]`, correctly reporting "nothing
                # appended" even though `live` still flows through below.
                new_refs = all_refs[-len(live) :]
            # The tee: input passes through while being recorded. The
            # frames just appended above ARE `live` -- do not also fan out
            # the rest of the buffer here (that's what Emit is for).
            result_frames = live
        else:  # MODE_EMIT -- never appends; never reports `ui` (point 1).
            # Buffer chronological first, then whatever's live right now,
            # newest last (owner's "10 buffered + 1 wired -> 11").
            result_frames = store.read_all_as_tensors(grid_uuid) + live

        if not result_frames:
            # See module docstring "Empty-buffer safety". Unreachable with
            # `new_refs` non-empty (that requires `live` non-empty, which
            # makes `result_frames` non-empty too in Collect mode; Emit mode
            # never populates `new_refs` at all) -- so this path never needs
            # a `"ui"` key, matching point 1 for both modes uniformly.
            from comfy_execution.graph import ExecutionBlocker

            blocked = [ExecutionBlocker(None)]
            return {"result": (blocked, blocked, blocked)}

        widths = [int(tensor.shape[2]) for tensor in result_frames]
        heights = [int(tensor.shape[1]) for tensor in result_frames]
        out: dict[str, Any] = {"result": (result_frames, widths, heights)}
        if new_refs:
            out["ui"] = {"images": new_refs}
        return out
