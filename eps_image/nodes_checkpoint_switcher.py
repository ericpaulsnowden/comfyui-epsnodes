"""``EPSCheckpointSwitcher`` (display: "EPS Checkpoint Switcher") -- tick
several checkpoint files, get one queue that runs the rest of the workflow
once per ticked checkpoint ("try the same prompt across N models").

Shape: the mirror-image of ``EPSSwitcher`` (``nodes_switcher.py``) turned
into a LOADER instead of a passthrough. Switcher fans in N already-loaded
``image_N`` inputs; this node has no inputs to fan in at all -- it fans OUT
by loading each of N *selected filenames* itself and emitting four
index-aligned ``OUTPUT_IS_LIST`` lists (``model``, ``clip``, ``vae``,
``label``), one element per enabled checkpoint, in selection order. Core's
own list-execution fan-out (the same mechanism Switcher/EPSImageGrid rely
on) then re-runs everything downstream once per element -- N ticked
checkpoints, N runs, each with its own model/clip/vae/label already
resolved to the SAME index, so a downstream ``KSampler`` + ``SaveImage``
branch needs no extra wiring to know which model produced which image.

**Selection is server-authoritative, exactly like Switcher's `toggles`
(FORMAT.md §6.4's bridge pattern).** Which checkpoints are ticked is
frontend state (the panel this node's own JS half draws), but the
FILTERING/LOADING must happen server-side so the emitted lists are
authoritative regardless of what UI drove it -- a raw ``/prompt`` POST from
a script, an API-only caller with no frontend at all. The bridge is a plain
``selection`` STRING widget: a JSON ARRAY of checkpoint filenames exactly as
``folder_paths.get_filename_list("checkpoints")`` lists them, in emission
order, that the panel keeps in lockstep with its own tick-boxes. Unlike
Switcher's ``toggles`` (an OVERRIDE map layered on top of whatever is
wired), there is nothing to default-enable here -- no sockets, no wiring --
so an empty/omitted ``selection`` means "nothing ticked yet", not "assume
everything on": the least-surprising reading for a brand-new node on the
canvas, and it degrades to the same all-off blocker path as Switcher's own
"nothing connected" case.

**Loading, not passthrough.** Every other list-fan-out node in this pack
(Switcher, EPSImageGrid) hands along values that already exist upstream;
this one calls ``comfy.sd.load_checkpoint_guess_config`` itself, once per
selected name, through the exact same call core's own
``CheckpointLoaderSimple.load_checkpoint`` makes (``nodes.py``, read
directly off the ComfyUI install rather than guessed --
:func:`_load_checkpoint` below cites the exact lines). No caching of loaded
checkpoints here -- and none is needed for repeat queues: ComfyUI caches
NODE OUTPUTS by input hash (execution caching, not model management), so an
unchanged ``selection`` means this node's execute() is not re-run at all and
the previously loaded model/clip/vae objects are handed downstream again,
exactly as happens for a plain ``CheckpointLoaderSimple``. Only a CHANGED
selection re-loads, and then only as this node's single execution.

**Empty selection, and a selection where every named file has since
vanished from disk, are both valid states** -- the same FORMAT.md §6.4
"all-off is a VALID state" convention Switcher and Distributor both follow.
:meth:`EPSCheckpointSwitcher.execute` returns a one-element list holding a
silent (``message=None``) ``comfy_execution.graph.ExecutionBlocker`` on
each of the four outputs instead of raising: the queue succeeds and
whatever is wired downstream is silently skipped. A selection that names
SOME files that still exist and SOME that don't loads the ones that do and
just skips the missing ones (logged, not fatal) -- one stale tick (a
checkpoint deleted off disk after the workflow was saved) shouldn't sink
every other model in the sweep.

**Queue-time validation is a courtesy, not the enforcement layer.**
``VALIDATE_INPUTS`` below re-checks every selected name against
``folder_paths.get_filename_list("checkpoints")`` and fails the QUEUE with
a clear message naming whichever names are unknown -- catching a typo'd or
hand-edited ``selection`` before anything runs, rather than N-1 checkpoints
loading successfully and only then discovering the last one is a 404. It is
deliberately not the ONLY line of defense: ``execute``'s own missing-file
skip (above) is what actually protects a live queue against a file that
existed at validate time and is gone by execute time (a `git pull` on a
shared model folder mid-session, a NAS hiccup) -- the two checks are
independent and agree only by construction, not by sharing state.

No comfy/torch/folder_paths import anywhere at MODULE scope (the pack-wide
promise -- see ``nodes_switcher.py``'s own module docstring for the same
rule and ``tests/test_switcher.py``'s ``test_module_never_imports_comfy_or_torch``
for how it's pinned): every one of those three is imported lazily, each
only inside the function that actually needs it
(:func:`_load_checkpoint`, :meth:`EPSCheckpointSwitcher.execute`'s all-off
branch, :meth:`EPSCheckpointSwitcher.VALIDATE_INPUTS`), so this module stays
importable in a bare test environment with no ComfyUI on the path.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("eps_image")

#: Default ``selection`` widget value: nothing ticked yet (module docstring
#: "Selection is server-authoritative" -- unlike Switcher's `toggles`, an
#: empty/omitted selection here means "nothing selected", not "everything
#: on", since there is nothing to default-enable without any sockets).
DEFAULT_SELECTION = "[]"


def _parse_selection(selection: Any) -> list[str]:
    """Best-effort JSON-array parse of the ``selection`` widget value.

    Never raises (mirrors ``nodes_switcher.py``'s ``_parse_toggles`` /
    ``nodes_distributor.py``'s function of the same name -- this pack's
    standard degrade-don't-crash contract for a serialized JSON widget):

    - Empty/``None``/falsy input -> ``[]``.
    - Unparseable JSON -> ``[]``, logged at WARNING.
    - Valid JSON that isn't a list (an object, a number, a bare string) ->
      ``[]``, logged at WARNING.
    - A valid JSON list whose entries are a MIX of strings and other JSON
      types (numbers, ``null``, nested objects/arrays -- "mixed junk", e.g.
      from a hand-edited workflow) keeps every STRING entry and drops only
      the non-string ones individually, each logged at WARNING -- one bad
      entry must not blank out an otherwise-good selection, the same
      per-element tolerance ``nodes_switcher.py``'s ``execute`` already
      applies to ``None`` elements inside a resolved image list.

    Returns filenames in the SAME order they appear in *selection* --
    callers (``execute``, ``VALIDATE_INPUTS``) rely on this for the
    "ordering follows selection order" contract.
    """
    if not selection:
        return []
    try:
        parsed = json.loads(selection)
    except (TypeError, ValueError) as exc:
        logger.warning(
            "EPS Checkpoint Switcher: malformed `selection` value (%s); treating "
            "as no checkpoints selected",
            exc,
        )
        return []
    if not isinstance(parsed, list):
        logger.warning(
            "EPS Checkpoint Switcher: `selection` was not a JSON array (got %r); "
            "treating as no checkpoints selected",
            type(parsed).__name__,
        )
        return []
    names: list[str] = []
    for entry in parsed:
        if isinstance(entry, str):
            names.append(entry)
        else:
            logger.warning(
                "EPS Checkpoint Switcher: `selection` entry %r is not a string; "
                "skipping it",
                entry,
            )
    return names


def _stem(filename: str) -> str:
    """The ``label`` output for *filename*: its final path segment, minus
    its last extension (``sub/dir/name.safetensors`` -> ``name``).

    Splits on EITHER ``/`` or ``\\``, not just the host OS's own separator:
    ``folder_paths.get_filename_list`` returns paths using the OS's NATIVE
    separator (precedent, verified against the owner's actual mixed
    Windows/Mac/Linux setup: ``lora_library/sets_store.py``'s own
    ``_normalize_separators``/``_basename``), so a subfoldered checkpoint
    reads ``styles/sdxl.safetensors`` on macOS/Linux and
    ``styles\\sdxl.safetensors`` on Windows -- and this pack's own test
    suite runs on macOS regardless of which OS eventually serves the queue.
    Only the LAST extension is stripped (``name.v2.safetensors`` ->
    ``name.v2``), matching ``pathlib.PurePath.stem``; a name with no
    extension, or one whose only ``.`` is a leading dotfile marker, is
    returned unchanged rather than collapsing to an empty string.
    """
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    stem = base.rsplit(".", 1)[0] if "." in base else base
    return stem or base


def _load_checkpoint(name: str) -> tuple[Any, Any, Any]:
    """Load one checkpoint file exactly the way core's own
    ``CheckpointLoaderSimple.load_checkpoint`` does it.

    Read directly off this machine's ComfyUI install
    (``~/Library/Application Support/comfy_ps/rig/ComfyUI/nodes.py``,
    ``CheckpointLoaderSimple`` around line 609) rather than guessed: resolve
    *name* through ``folder_paths.get_full_path_or_raise("checkpoints",
    name)``, then call ``comfy.sd.load_checkpoint_guess_config`` with the
    SAME ``output_vae=True, output_clip=True, embedding_directory=
    folder_paths.get_folder_paths("embeddings")`` arguments that node uses,
    and keep only the first three returned values (model, clip, vae) --
    core's own loader slices its own longer return tuple the same way
    (``out[:3]``; the guess-config call can also hand back a fourth,
    loader-internal value neither it nor this function exposes).

    This is the ONE seam :meth:`EPSCheckpointSwitcher.execute` calls
    through for the actual load -- tests monkeypatch THIS function directly
    (see ``tests/test_checkpoint_switcher.py``) rather than faking
    ``folder_paths``/``comfy.sd`` themselves, so a test can simulate a
    successful load or a missing file without needing real model weights or
    a real ComfyUI install anywhere on the path.

    Raises ``FileNotFoundError`` verbatim -- ``get_full_path_or_raise``'s
    own exception -- when *name* no longer resolves to a file under the
    configured checkpoints folder(s). This function does NOT catch it:
    ``execute`` is the layer that turns a missing file into a per-name skip
    (module docstring) rather than a queue failure, and every other load
    failure (a corrupt/unrecognized file, ``comfy.sd`` raising its own
    ``RuntimeError`` when it can't detect a model type) is deliberately left
    to propagate -- only "the file is gone" degrades gracefully; a genuinely
    broken checkpoint is a real error the queue should surface, the same as
    it would for a plain ``CheckpointLoaderSimple``.

    Both imports are lazy (module docstring's bare-importable promise):
    this function only ever runs from inside a live ComfyUI process, never
    at import time.
    """
    import comfy.sd  # ComfyUI's own module; only importable inside ComfyUI
    import folder_paths  # ComfyUI's own module; only importable inside ComfyUI

    ckpt_path = folder_paths.get_full_path_or_raise("checkpoints", name)
    out = comfy.sd.load_checkpoint_guess_config(
        ckpt_path,
        output_vae=True,
        output_clip=True,
        embedding_directory=folder_paths.get_folder_paths("embeddings"),
    )
    model, clip, vae = out[:3]
    return model, clip, vae


class EPSCheckpointSwitcher:
    """Tick N checkpoints, get four index-aligned ``OUTPUT_IS_LIST`` lists
    (``model``, ``clip``, ``vae``, ``label``) -- one element per ticked
    checkpoint, in selection order -- so the rest of the workflow runs once
    per checkpoint via core's own list-execution fan-out (module docstring).

    ``execute`` re-parses ``selection`` and re-loads every named checkpoint
    fresh on each call -- there is no other state that could go stale, so
    unlike the Prompt Notebook this node needs no ``IS_CHANGED`` override:
    ``selection`` is an ordinary tracked input already covered by ComfyUI's
    own input-hash caching, and ComfyUI's/model-management's own weight
    caching (not this node) is what makes re-loading the SAME checkpoint
    across queues cheap.

    Zero ticked checkpoints -- nothing selected, or every selected name has
    since vanished from disk -- is a VALID queue, not an error (module
    docstring, same FORMAT.md §6.4 convention as ``EPSSwitcher``'s all-off
    state): ``execute`` returns a one-element list holding a silent
    ``ExecutionBlocker`` on EACH of the four outputs, so the queue succeeds
    and everything wired downstream is silently skipped.
    """

    CATEGORY = "EPSNodes"
    RETURN_TYPES = ("MODEL", "CLIP", "VAE", "STRING")
    RETURN_NAMES = ("model", "clip", "vae", "label")
    OUTPUT_IS_LIST = (True, True, True, True)
    OUTPUT_TOOLTIPS = (
        "The loaded model for each ticked checkpoint, in selection order.",
        "The loaded CLIP for each ticked checkpoint, in selection order.",
        "The loaded VAE for each ticked checkpoint, in selection order.",
        "Each ticked checkpoint's filename stem (basename minus extension) "
        "-- handy for naming outputs per-model, e.g. in a filename prefix.",
    )
    FUNCTION = "execute"
    DESCRIPTION = (
        "Tick any number of checkpoint files; each one is loaded and the "
        "rest of the workflow runs once per ticked checkpoint (a list-"
        "producing fan-out, like EPS Image Switcher) -- the same prompt, tried "
        "across N models in one queue. model/clip/vae/label are index-"
        "aligned: element i of every output came from the same checkpoint. "
        "Ticking nothing, or ticking only checkpoints that have since been "
        "removed from disk, is a valid state: the queue still succeeds and "
        "the downstream branch simply doesn't run."
    )

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {},
            "optional": {
                # Server-authoritative selection bridge -- module docstring
                # "Selection is server-authoritative" -- a JSON array of
                # checkpoint filenames (folder_paths spelling), in emission
                # order. In `optional`, NOT `required`: ComfyUI's own prompt
                # validation rejects a hand-built /prompt that omits any
                # REQUIRED input before the node ever runs (no backend
                # default-fill at validation time), which would make a
                # plain API caller who has never loaded this node's panel
                # JS unable to queue it at all. `execute`'s own
                # `selection=DEFAULT_SELECTION` default covers the omitted
                # case.
                #
                # "hidden": True is the VUE-nodes ("New node design") hide
                # flag (FORMAT.md §7.5): that renderer decides widget
                # visibility from the input spec's OPTIONS
                # (`options.hidden`, useProcessedWidgets.ts) and IGNORES the
                # litegraph `widget.hidden` the frontend sets at attach time
                # -- without this, this internal plumbing widget would leak
                # into Vue nodes as a raw editable text field (exactly what
                # happened to Switcher's `toggles`/the Notebook's `file`
                # before this flag existed). The classic canvas renderer
                # ignores this key right back, so it changes nothing there.
                "selection": (
                    "STRING",
                    {
                        "default": DEFAULT_SELECTION,
                        "multiline": False,
                        "hidden": True,
                        "tooltip": (
                            "JSON array of ticked checkpoint filenames, in "
                            "emission order -- maintained by this node's "
                            "own panel; you don't need to type into it "
                            "directly."
                        ),
                    },
                ),
            },
        }

    @classmethod
    def VALIDATE_INPUTS(cls, selection: str = DEFAULT_SELECTION) -> bool | str:
        """Queue-time courtesy check (module docstring "Queue-time
        validation is a courtesy, not the enforcement layer").

        Returns ``True`` for anything that doesn't parse to a non-empty
        list of names -- malformed JSON, a non-array, an all-junk array --
        the same graceful degradation ``execute`` itself applies via
        :func:`_parse_selection` (reused here, not reimplemented, so the two
        can never disagree about what counts as a selected name). Only once
        *selection* parses to at least one name does this check the names
        against ``folder_paths.get_filename_list("checkpoints")`` and
        return a STRING naming whichever ones are unknown -- ComfyUI turns
        a non-``True`` return into a queue-time validation error (see
        core's ``execution.py`` ``validate_inputs``: "if r is not True ...
        details += f' - {str(r)}'"), which is a clearer failure than
        letting N-1 checkpoints load and only then discovering the last one
        404s inside ``execute``.

        The ``folder_paths`` import is lazy AND guarded (module docstring's
        bare-importable promise, and this pack's tests routinely import
        this module with no ComfyUI on the path at all): any failure to
        import it, OR any failure from ``get_filename_list`` itself (a
        broken/unreadable model directory), returns ``True`` rather than
        raising -- this queue-time check simply can't run in that
        environment, and it is a courtesy layer, not the only thing
        standing between a bad name and a crash (``execute``'s own
        missing-file skip is that layer, and it works with no ComfyUI
        install assumptions of its own beyond what ``execute`` already
        requires to run at all).
        """
        names = _parse_selection(selection)
        if not names:
            return True
        try:
            import folder_paths  # ComfyUI's own module; only importable inside ComfyUI

            known = set(folder_paths.get_filename_list("checkpoints"))
        except Exception:
            return True
        unknown = [name for name in names if name not in known]
        if unknown:
            return (
                "EPS Checkpoint Switcher: unknown checkpoint file(s), not in "
                "folder_paths' current checkpoints list: " + ", ".join(unknown)
            )
        return True

    def execute(self, selection: str = DEFAULT_SELECTION) -> tuple[list, list, list, list]:
        names = _parse_selection(selection)

        models: list[Any] = []
        clips: list[Any] = []
        vaes: list[Any] = []
        labels: list[str] = []
        missing: list[str] = []

        for name in names:
            try:
                model, clip, vae = _load_checkpoint(name)
            except FileNotFoundError:
                # module docstring: a stale tick (deleted off disk since the
                # workflow was saved/validated) is skipped, not fatal -- the
                # other selected checkpoints still run.
                missing.append(name)
                continue
            models.append(model)
            clips.append(clip)
            vaes.append(vae)
            labels.append(_stem(name))

        if missing:
            logger.warning(
                "EPS Checkpoint Switcher: %d selected checkpoint(s) no longer "
                "found on disk, skipping: %s",
                len(missing),
                ", ".join(missing),
            )

        if models:
            return (models, clips, vaes, labels)

        # FORMAT.md §6.4 "All-off / none-connected is a VALID state",
        # applied here to "nothing selected" and "everything selected is
        # missing" alike (module docstring). Returning a list holding a
        # single ExecutionBlocker(None) on EACH output makes ComfyUI's own
        # list-fanout machinery (execution.py `merge_result_data`) treat
        # THAT WHOLE LIST as "blocked": every downstream node whose input
        # traces back to it is silently skipped -- no `execution_error`
        # event (that only fires when `.message` is not None), no
        # exception, a normal SUCCESS queue. See nodes_switcher.py's own
        # `execute` for the live-verified reasoning against a bare `[]`
        # instead (crashes on the zero-co-input and mixed-length paths) --
        # identical logic applies here, unchanged.
        if names:
            logger.info(
                "EPS Checkpoint Switcher: %d checkpoint(s) selected but none "
                "could be loaded -- returning an execution blocker so the "
                "queue succeeds and downstream nodes are silently skipped",
                len(names),
            )
        else:
            logger.info(
                "EPS Checkpoint Switcher: no checkpoints selected -- "
                "returning an execution blocker so the queue succeeds and "
                "downstream nodes are silently skipped"
            )

        # Lazy import: keeps this module importable with no ComfyUI on the
        # path (module docstring's bare-importable promise; see
        # tests/test_checkpoint_switcher.py's
        # test_module_never_imports_comfy_or_torch_at_module_scope).
        from comfy_execution.graph import ExecutionBlocker

        return (
            [ExecutionBlocker(None)],
            [ExecutionBlocker(None)],
            [ExecutionBlocker(None)],
            [ExecutionBlocker(None)],
        )
