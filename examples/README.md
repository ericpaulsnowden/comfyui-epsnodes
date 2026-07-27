# Example workflows

Drag any `.json` here onto the ComfyUI canvas (or File → Open) to load it.
All four were built and round-trip verified against ComfyUI v0.28 /
frontend 1.45. The cross/pipeline graphs need this pack at v0.30.0+;
`eps-test-distributor.json` needs v0.34.0+.

**One-time setup for the two CROSS test workflows:** copy
[`eps-cross-test-prompts.md`](eps-cross-test-prompts.md) into your EPSNodes
library folder (the one in Settings → EPSNodes). Both cross graphs point
their Prompt Notebook at that file with all four prompts pre-selected.
`eps-test-distributor.json` needs no setup at all.

---

## eps-test-cross-product.json — *press Run, no GPU needed*

Proves **2 images × 4 prompts = 8**: two Load Image nodes → EPS Switcher →
EPS Cross Product → Save Image, with the crossed `name` output driving
`filename_prefix` so the filenames themselves show the pairing.

Expected output — 8 files, each prompt appearing exactly twice:

```
Neon City_00001_.png   Golden Hour_00001_.png   Ink Sketch_00001_.png   Pastel Studio_00001_.png
Neon City_00002_.png   Golden Hour_00002_.png   Ink Sketch_00002_.png   Pastel Studio_00002_.png
```

`_00001_` = the first image, `_00002_` = the second. Without EPS Cross
Product ComfyUI would zip the two lists and give you 4 files, three of them
reusing the last picture. No checkpoint, lora, or sampling involved — it
just moves images around, so it runs anywhere in seconds.

*Verified end to end on the test rig: this exact graph produced exactly
those 8 files.*

## eps-test-distributor.json — *press Run, no setup, no GPU*

Proves **one image, three branches, one run** — and that a switched-off
branch skips its whole chain, not just the node it touches. Load Image → EPS
Distributor → `out_1` saves as-is, `out_2` goes through **Invert Image
Colors** and then saves, `out_3` previews on canvas.

Three passes, all on the canvas note:

1. **All on** → `branch_A_00001_`, `branch_B_inverted_00001_`, and a preview.
2. **Click `out_2`'s checkbox off** → branch A still writes a file, **no new
   `branch_B_inverted_`**, preview still updates. The Invert node in the
   middle never executes either.
3. **All off** → the queue still reports success, writes nothing, no error.

Also try right-click → Properties → `Outputs` to show up to 8 sockets, and
try lowering it while `out_2` is wired — it refuses rather than silently
dropping your wire.

*Verified end to end on the test rig: every one of those claims was
confirmed against a real queue, including that Invert itself produced nothing
when `out_2` was off.*

## eps-test-cross-sweep.json — *a real 24-image generation run*

Proves **3 strengths × 8 pairs = 24**, foldered by strength. Same pair side
(plus EPS Resolution normalizing to a 1024 fit / multiple of 8 so img2img is
safe), crossed against an EPS LoRA Sweep, with `save_prefix` wired into Save
Image's `filename_prefix`.

Pick two things before running (both marked on the canvas): a **checkpoint**
and a **lora state** on EPS Apply LoRA Set. Then Run once.

Expected output:

```
output/eps_sweeptest/
  <loraname>_0.0/   Neon City_00001_.png  Golden Hour_00001_.png  … (8 files)
  <loraname>_0.5/   … (8 files)
  <loraname>_1.0/   … (8 files)
```

Fixed seed 42 and denoise 0.6 across all 24, so within a folder only the
prompt changes and between folders only the lora strength does. The sweep
ships in **All together** mode so the count stays predictable regardless of
how many loras your state holds; switch it to *Each lora independently* and
the run multiplies by the lora count. Dropping the sweep `increment` from
0.5 to 0.1 gives 11 strengths = 88 images.

*The rig has no checkpoint, so the generation half is your first real run.
Everything up to it is verified: the graph passes ComfyUI's own validation
with the checkpoint as the only missing input, and it round-trips with all
30 links and both notebook selections intact.*

## eps-full-pipeline.json — *the tour*

Every EPSNodes capability stitched into one complex workflow. Also needs
[rgthree-comfy](https://github.com/rgthree/rgthree-comfy), for the
state-controller corner only.

The graph, stage by stage (numbered groups + notes on the canvas):

1. **Sources & Switcher** — two Load Image nodes + an EPS Frame Saver (video
   frame) feed an EPS Switcher: toggle rows to pick which sources flow;
   toggled-off branches never execute.
2. **Normalize & Collect** — EPS Resolution fits everything to 1024, then an
   EPS Image Grid records the stream (Collect) across runs; flip it to Emit
   to fan the whole collection out.
3. **Prompts & Pairs** — an EPS Prompt Notebook (multi-select) and an EPS
   Cross Product pair EVERY grid image with EVERY selected prompt; entry
   names ride along.
4. **Loras & Sweep** — Checkpoint → EPS Apply LoRA Set (reads a saved state)
   → EPS LoRA Sweep (0.0–1.0 @ 0.5 to start = 3 steps). The controller
   corner (EPS Lora Loader State Controller + Power Lora Loader) is where
   states get captured/renamed.
5. **Cross Sweep & Generate** — EPS Cross Sweep multiplies sweep steps ×
   pairs (strength-major), then img2img sampling (fixed seed 42, denoise
   0.6) saves via `save_prefix` into
   `output/eps_demo/<lora>_<strength>/<PromptName>_*.png` — one folder per
   strength.
6. **Distributor → branches** — the decoded result fans out through an EPS
   Distributor: `out_1` to the Save Image (still using Cross Sweep's folder
   path), `out_2` to a preview, `out_3` spare. Switch a socket off to drop
   that branch without touching the rest of the run.

Before running: pick a checkpoint, images, and a video path; select 2+
prompts in the Notebook; pick (or capture) a lora state. Run count =
loras × strengths × grid images × prompts — start small.
