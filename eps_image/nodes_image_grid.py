"""``EPSImageGrid`` (FORMAT.md §6.6, display: "EPS Image Grid") — accumulate
across separate Runs, then fan out.

M1 only (`research/roadmap-eps-image-grid.md`): Collect/Emit toggle, a
disk-backed buffer that survives a ComfyUI restart, the free thumbnail grid,
Clear, and per-node identity/dedup (the frontend half, `web/eps_image/
image_grid.js`). Copy/paste (M2) and buffer-management polish (M3) are
deliberately NOT built here.

No torch/ComfyUI import anywhere at module scope — `run()` only reaches into
`image_grid_store` (which itself lazily imports `folder_paths`/`torch`/
`numpy`/`PIL`, see that module's docstring), so this stays importable in a
plain test environment with neither installed.

**Execution model** (roadmap-eps-image-grid.md "Design guardrails" —
verified directly against this repo's ComfyUI checkout, `execution.py`):
`OUTPUT_NODE = True` makes the node execute even with nothing wired
downstream (the `SaveImage`/`PreviewImage` precedent — `validate_prompt`
only requires an execution path when there's an `OUTPUT_NODE` to reach), and
`IS_CHANGED` returning `float("nan")` defeats ComfyUI's own input-hash
caching (`NaN != NaN`; confirmed as the SAME sentinel `execution.py`'s own
`IsChangedCache` falls back to on an `IS_CHANGED` exception, `node["is_changed"]
= float("NaN")`) so caching never skips a Run. Together: exactly one
execution — at most one append of the current batch — per queued prompt,
in EITHER mode; `Emit` simply skips the append.

**Empty-buffer safety (a deliberate, verified addition beyond FORMAT.md
§6.6's literal "emit empty lists" text — see final report for the flag to
amend that section).** A bare `([], [], [])` result is only safe when
NOTHING is wired downstream. Traced directly against this repo's
`execution.py`: for any downstream node with ANY other, non-list input (a
plain widget value — e.g. `SaveImage.filename_prefix` — which is virtually
every node), `get_input_data` wraps that widget value as a length-1 list,
so `max_len_input = max(0, 1, ...) = 1`, and the per-index loop's
`slice_dict` (`{k: v[i if len(v) > i else -1] for k, v in d.items()}`)
evaluates `v[-1]` on OUR empty `images` list — `IndexError`, crashing the
whole queue. This is the EXACT failure mode `EPSSwitcher` already discovered
and fixed for the identical reason (`nodes_switcher.py`'s module docstring
"All-off / none-connected"): its fix — return a list holding a single,
silent `ExecutionBlocker(None)` instead of a bare `[]` — is reused here,
applied to all three output slots, so a still-empty buffer with something
wired downstream is silently skipped (a normal, successful queue) rather
than crashing. A genuinely bare graph (nothing wired downstream at all) was
never at risk either way — there is no other node's inputs for an empty
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


class EPSImageGrid:
    """Wire an image loader in; each Run in `Collect` mode appends its
    current image(s) to a buffer that grows across separate Runs and
    survives restarts; the whole buffer fans out downstream (N buffered
    images -> the rest of the workflow runs N times); a `Clear` button
    (frontend, `image_grid.js`) wipes it.

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
        "EPS Image Grid -- wire a loader in; each Run in Collect mode adds its "
        "current image(s) to a buffer that grows across separate Runs and "
        "survives a restart (no cap -- you manage disk use, Clear wipes it). "
        "The buffer shows as a thumbnail grid on the node. Switch to Emit and "
        "Run once to fan every buffered image out downstream, paired with its "
        "own width/height (N images -> the rest of the workflow runs N "
        "times). A scalar value (e.g. a seed) wired further downstream "
        "repeats identically across all N runs."
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
        if mode == MODE_COLLECT and image is not None:
            store.append_batch(grid_uuid, image)

        refs = store.list_refs(grid_uuid)
        tensors = store.read_all_as_tensors(grid_uuid)

        if not tensors:
            # See module docstring "Empty-buffer safety".
            from comfy_execution.graph import ExecutionBlocker

            blocked = [ExecutionBlocker(None)]
            return {"ui": {"images": refs}, "result": (blocked, blocked, blocked)}

        widths = [int(tensor.shape[2]) for tensor in tensors]
        heights = [int(tensor.shape[1]) for tensor in tensors]
        return {"ui": {"images": refs}, "result": (tensors, widths, heights)}
