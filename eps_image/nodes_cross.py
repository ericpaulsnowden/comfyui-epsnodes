"""``EPSCrossProduct`` (FORMAT.md §6.9, display: "EPS Cross Product") —
pair EVERY image with EVERY text.

Why this node exists (owner report, 2026-07-23): wiring TWO fanned lists
into the same downstream path does NOT multiply them. ComfyUI's core list
execution ZIPS lists index-by-index and repeats the SHORTER list's last
element (``execution.py``'s ``slice_dict``: ``v[i if len(v) > i else -1]``)
— so a 2-image EPS Image Grid paired with 4 selected EPS Prompt Notebook
entries runs downstream 4 times as (img1, p1), (img2, p2), (img2, p3),
(img2, p4): the owner's observed "1 layer from the first image and 3 from
the second", where 2 x 4 = 8 was wanted. Core has no cross-product
mechanism; this node is it: N images x M texts -> N*M pairs, image-major
(img1 with every text in order, then img2 with every text, ...), each pair
riding out through two ``OUTPUT_IS_LIST`` outputs that stay index-aligned
by construction.

``INPUT_IS_LIST = True`` for the same reason as ``EPSSwitcher`` (see
``nodes_switcher.py``'s module docstring): without it, core would map THIS
node once per element of the longer input — zipping the very lists we're
here to multiply — instead of handing both lists over whole.

No torch/ComfyUI import anywhere at module scope: elements are treated as
opaque values (an image element that is a ``[B,H,W,C]`` batch stays ONE
element, exactly like the switcher's flatten semantics), so this module
stays importable in a bare test environment.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("eps_image")


def _as_clean_list(value: Any) -> list[Any]:
    """*value* as a list with ``None`` elements dropped.

    With ``INPUT_IS_LIST`` every connected input arrives as a list; a bare
    non-list (a direct caller/test skipping the wrapping) is tolerated as a
    single-element list, mirroring ``EPSSwitcher.execute``'s tolerance.
    ``None`` elements are skipped defensively (same rationale as the
    switcher: a partial/misbehaving upstream must degrade, not crash).
    """
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        return [value]
    return [element for element in value if element is not None]


class EPSCrossProduct:
    """N images x M texts -> N*M (image, text) pairs, image-major.

    2026-07-23b (owner's folder-organization ask, FORMAT.md §6.10): an
    optional ``names`` input (the Prompt Notebook's ``name`` output — its
    entry headings, index-aligned with its ``text``) rides through the
    SAME cross as a third output, so every pair downstream carries a short
    human-readable identity — EPS Run Multiplier turns it into the pair half
    of ``save_prefix``. Additive only: the new output is APPENDED (existing
    workflows' wires keep their indices), and unwired ``names`` yields
    empty strings, never a shape change.
    """

    CATEGORY = "EPSNodes"
    RETURN_TYPES = ("IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("image", "text", "name")
    OUTPUT_IS_LIST = (True, True, True)
    INPUT_IS_LIST = True
    OUTPUT_TOOLTIPS = (
        "One image per pair, image-major order (all of image 1's pairs, "
        "then all of image 2's, and so on).",
        "One text per pair, index-aligned with image.",
        "One name per pair, index-aligned with image and text -- empty "
        "string where names wasn't wired.",
    )
    FUNCTION = "run"
    DESCRIPTION = (
        "Pairs every image with every text: 2 images and 4 texts produce 8 "
        "pairs, so the rest of the workflow runs 8 times -- image 1 with "
        "each text in order, then image 2 with each text, and so on. Use "
        "this when two upstream lists (e.g. EPS Image Grid and EPS Prompt "
        "Notebook's multi-select) should multiply instead of zip: "
        "ComfyUI's default pairs lists index-by-index and repeats the "
        "shorter list's last element, which is why 2 images and 4 texts "
        "otherwise produces only 4 runs, most of them reusing the last "
        "image. Wire this node's outputs onward in place of the two "
        "originals -- they stay paired index-for-index."
    )

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "images": (
                    "IMAGE",
                    {"tooltip": "The images to pair. Every image is paired with every text below."},
                ),
                # forceInput: this is a wire-only socket (there is nothing
                # sensible to type into a widget here -- the whole point is
                # pairing an upstream LIST, e.g. the Prompt Notebook's
                # multi-select `text` output).
                "texts": (
                    "STRING",
                    {
                        "forceInput": True,
                        "tooltip": (
                            "The texts to pair. Every text is paired with "
                            "every image above. Wire-only -- usually from a "
                            "multi-select Prompt Notebook or similar list."
                        ),
                    },
                ),
            },
            "optional": {
                # The Prompt Notebook's `name` output, index-aligned with
                # its `text` -- crossed identically so each pair keeps its
                # short identity (class docstring). Optional + additive.
                "names": (
                    "STRING",
                    {
                        "forceInput": True,
                        "tooltip": (
                            "Optional short label per text, index-aligned "
                            "with texts (e.g. the Prompt Notebook's name "
                            "output) -- carried through the same pairing "
                            "so each pair keeps an identity. Empty string "
                            "where not wired."
                        ),
                    },
                ),
            },
        }

    def run(
        self, images: Any = None, texts: Any = None, names: Any = None
    ) -> tuple[list[Any], list[Any], list[Any]]:
        image_list = _as_clean_list(images)
        text_list = _as_clean_list(texts)
        # Names align with TEXTS by index (the Notebook emits text/name as
        # parallel lists); a missing/short list pads with "" rather than
        # guessing -- downstream fallbacks (EPS Run Multiplier's pair_NN) own
        # the empty case.
        name_list = _as_clean_list(names)

        if not image_list or not text_list:
            # Same empty-safety pattern as EPSSwitcher/EPSImageGrid (see
            # nodes_switcher.py's execute for the full bare-[]-vs-blocker
            # trace): an empty side means there is nothing to pair, and the
            # downstream branch should silently skip, not crash the queue.
            logger.info(
                "EPS Cross Product: %d image(s) x %d text(s) -- nothing to "
                "pair; returning an execution blocker so the queue succeeds "
                "and downstream nodes are silently skipped",
                len(image_list),
                len(text_list),
            )
            from comfy_execution.graph import ExecutionBlocker

            blocked = [ExecutionBlocker(None)]
            return (blocked, blocked, blocked)

        out_images: list[Any] = []
        out_texts: list[Any] = []
        out_names: list[Any] = []
        for image in image_list:
            for index, text in enumerate(text_list):
                out_images.append(image)
                out_texts.append(text)
                out_names.append(name_list[index] if index < len(name_list) else "")
        return (out_images, out_texts, out_names)
