# Example workflows

Drag any workflow `.json` here onto the ComfyUI canvas (or File → Open) to
load it. Every one of them is annotated on the canvas — a big note explaining
what it proves and what to try, plus smaller notes beside the nodes they
describe — so you shouldn't need this page open to follow along.

All were round-trip verified against ComfyUI v0.28 / frontend 1.45.

The list runs simple → advanced. The first five are single-node demos; the
next three combine several nodes; the pipeline at the bottom is the whole tour.

Two files here are **not** workflows — don't drag them onto the canvas:

- [`eps-cross-test-prompts.md`](eps-cross-test-prompts.md) — a prompt notebook
  for the two CROSS graphs. Copy it into your EPSNodes library folder (the one
  in Settings → EPSNodes).
- [`eps-resolution-test-presets.json`](eps-resolution-test-presets.json) — two
  ready-made size presets for the EPS Resolution demo. Optional; see that
  entry below before you copy it anywhere.

| File | What it shows | Needs | Runs as shipped? |
|---|---|---|---|
| [`eps-test-frame-saver.json`](eps-test-frame-saver.json) | Pull one frame out of a video by path | none | yes — pick a video file first |
| [`eps-test-image-grid.json`](eps-test-image-grid.json) | A buffer that grows across separate Runs and survives a restart | none | yes — press Run |
| [`eps-test-resolution-presets.json`](eps-test-resolution-presets.json) | Resize + report size in one node; named presets you can tick | none | yes — press Run |
| [`eps-test-distributor.json`](eps-test-distributor.json) | One image, three independently-toggled branches, one run | none | yes — press Run |
| [`eps-test-checkpoint-switcher.json`](eps-test-checkpoint-switcher.json) | Tick several checkpoints; one queue runs once per model, with matched model/CLIP/VAE | checkpoint (only to generate) | yes — but writes nothing until you tick a checkpoint |
| [`eps-test-model-clip-vae-switchers.json`](eps-test-model-clip-vae-switchers.json) | Model / CLIP / VAE switchers as three independent axes | checkpoint | no — the two loaders ship with nothing chosen |
| [`eps-test-cross-product.json`](eps-test-cross-product.json) | 2 images × 4 prompts = 8 runs | none | yes — copy `eps-cross-test-prompts.md` into your library first |
| [`eps-test-cross-sweep.json`](eps-test-cross-sweep.json) | 3 lora strengths × 8 image/prompt pairs = 24 runs, foldered by strength | checkpoint, LoRA | no — pick a checkpoint and a saved lora state |
| [`eps-full-pipeline.json`](eps-full-pipeline.json) | Eleven of the fifteen nodes stitched into one graph — the full tour | checkpoint, LoRA, rgthree | no — pick a checkpoint, images, a video path, prompts, and a lora state |

---

## eps-test-frame-saver.json — *pull one frame out of a video*

**Needs:** any video file already on the machine running ComfyUI. No
checkpoint, no lora, no GPU, no plugin.

Frame Saver → Preview Image. Click **Browse…**, pick a video, scrub with the
transport strip under the preview (start / −5 / −1 / play / +1 / +5, or type a
frame number), press Run, and that exact frame comes out as an image.
Browse works only in a browser on the ComfyUI machine; from another computer,
select the node and paste the full path instead.

The path ships **empty** on purpose — a path off someone else's machine is
useless, and this node never copies your video into ComfyUI's `input` folder
anyway. It reads the file where it lies.

Expect: one image, matching the frame you scrubbed to. The on-node preview is
a fast approximation; the output is decoded fresh from the file, so the output
is the one to trust.

## eps-test-image-grid.json — *press Run, no setup, no GPU*

**Needs:** nothing. Ships pointing at ComfyUI's own `example.png`.

Load Image → EPS Image Grid → Save Image. Proves a buffer that **grows across
separate Runs and survives a restart**.

Four passes, all on the canvas note: collect three pictures over three Runs
(one file each), flip **mode** to **Emit** and Run once to get **four** files
out of a single Run (three buffered + one wired), restart ComfyUI and Emit
again to prove the buffer is on disk, then **Clear**.

The grid ships with an **empty** id. It mints its own on first load — which is
why two copies of this workflow don't end up fighting over one buffer, and why
you should never type an id in by hand.

## eps-test-resolution-presets.json — *press Run, no setup, no GPU*

**Needs:** nothing to run as it ships. The preset half is optional setup.

Load Image → EPS Resolution → Save Image. One node does the resize *and*
reports the numbers, so there's no separate resize / reroute / get-size chain.

Run it as it ships for one 1024×1024 file, then work through the note: the
four resize methods, `multiple_of` for sampler-safe sizes, leaving one axis at
`0` to derive it from the picture's own shape, and the drag pad with its
`W × H · ratio · MP` readout.

Then the real point — **save named sizes and tick more than one**. One Run,
one picture per ticked preset. Presets live in your library folder beside your
notebooks and lora sets, so they travel between machines.

Ships with **nothing ticked**, so it runs on a fresh install untouched.
[`eps-resolution-test-presets.json`](eps-resolution-test-presets.json) holds
two ready-made presets (`Square 1024`, `Portrait 832`). If you have no
`resolution_presets.json` in your library folder yet, copy that file in under
that name. **If you already have one, don't overwrite it** — make the two by
hand with **Save** instead; it takes ten seconds and you keep what you had.

Gotcha worth knowing before you hit it: a ticked preset that no longer exists
**fails the run**, on purpose, naming it. Silently substituting some other
size would mean shipping the wrong pictures and never finding out.

## eps-test-distributor.json — *press Run, no setup, no GPU*

**Needs:** nothing.

Proves **one image, three branches, one run** — and that a switched-off branch
skips its whole chain, not just the node it touches. Load Image → EPS
Distributor → `out_1` saves as-is, `out_2` goes through **Invert Image Colors**
and then saves, `out_3` previews on canvas.

Three passes, all on the canvas note:

1. **All on** → `branch_A_00001_`, `branch_B_inverted_00001_`, and a preview.
2. **Click `out_2`'s checkbox off** → branch A still writes a file, **no new
   `branch_B_inverted_`**, preview still updates. The Invert node in the
   middle never executes either.
3. **All off** → the queue still reports success, writes nothing, no error.

Also try right-click → Properties → `Outputs` to show up to **sixteen**
sockets, and try lowering it while `out_2` is wired — it refuses rather than
silently dropping your wire.

## eps-test-checkpoint-switcher.json — *press Run now; add models when you're ready*

**Needs:** your own checkpoints — but only once you want pictures out of it.

EPS Checkpoint Switcher → prompt → KSampler → Save Image, with the switcher's
`label` output wired into `filename_prefix` so every result is named after the
model that made it.

Press Run as it ships and the queue **succeeds having done nothing** — that's
the first lesson: nothing ticked is a legal state, and everything downstream is
simply skipped. Then tick two or three checkpoints and Run again: one full
generation per model, same prompt, same seed, so the model is the only
variable.

The reason to use this rather than three Checkpoint Loaders: `model`, `clip`
and `vae` come out **matched by construction** — run 2's CLIP and VAE came out
of run 2's file — so you can't accidentally pair one model's weights with
another model's VAE.

Start with three at 20 steps and 512×512 (how it ships) before you tick ten.

## eps-test-model-clip-vae-switchers.json — *needs your own models*

**Needs:** two checkpoints of your own (the two red-titled loaders ship with
nothing chosen).

Two Checkpoint Loaders → EPS **Model** / **CLIP** / **VAE** Switchers →
prompt → KSampler → Save Image. Tick both rows for two pictures; untick row 2
and checkpoint B **never loads at all** — the branch above a switched-off input
doesn't execute, so leaving options wired in and turned off costs nothing.

The note also spells out the trap these three set: **one switcher is one
axis**. Tick 3 models and 2 VAEs and you get **three** runs, not six —
ComfyUI zips the lists and repeats the shorter one's last entry. For every
combination, cross them deliberately with **EPS Cross Product**.

And the honest recommendation, on the canvas: if your model, CLIP and VAE all
come from the same checkpoint file — the normal case — use **EPS Checkpoint
Switcher** instead. These three are for when the pieces come from different
places.

Tip: switch both rows off first and Run — the queue succeeds without loading
either checkpoint, confirming a switched-off branch's checkpoint load truly
never runs, so it's safe to explore the toggles before you've picked any
files.

## eps-test-cross-product.json — *press Run, no GPU needed*

**Needs:** [`eps-cross-test-prompts.md`](eps-cross-test-prompts.md) copied into
your library folder. No checkpoint.

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

## eps-test-cross-sweep.json — *a real 24-image generation run*

**Needs:** [`eps-cross-test-prompts.md`](eps-cross-test-prompts.md) in your
library folder, a checkpoint, and a saved lora state.

Proves **3 strengths × 8 pairs = 24**, foldered by strength. Same pair side
(plus EPS Resolution normalizing to a 1024 fit / multiple of 8 so img2img is
safe), crossed against an EPS LoRA Iterator, with `save_prefix` wired into Save
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

Every wire in this graph is already correct — the checkpoint and a saved
lora state are the only things standing between this file and a real run.

## eps-full-pipeline.json — *the tour*

**Needs:** a checkpoint, images, a video path, the prompt notebook, a saved
lora state, and [rgthree-comfy](https://github.com/rgthree/rgthree-comfy) for
the state-controller corner.

Eleven of the fifteen nodes stitched into one complex workflow. This is the
tour, not the starting point — if you want to understand one node, the small
`eps-test-*.json` files above are far quicker. The four switcher nodes
(Checkpoint / Model / CLIP / VAE) don't appear here at all; they have their
own demos.

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
   → EPS LoRA Iterator (0.0–1.0 @ 0.5 to start = 3 steps). The controller
   corner (EPS Lora Loader State Controller + Power Lora Loader) is where
   states get captured/renamed.
5. **Multiply & Generate** — EPS Run Multiplier multiplies sweep steps ×
   pairs (strength-major), then img2img sampling (fixed seed 42, denoise
   0.6) saves via `save_prefix` into
   `output/eps_demo/<lora>_<strength>/<PromptName>_00001_.png` — one folder
   per strength.
6. **Distributor → branches** — the decoded result fans out through an EPS
   Distributor: `out_1` to the Save Image (still using the Run Multiplier's folder
   path), `out_2` to a preview, `out_3` spare. Switch a socket off to drop
   that branch without touching the rest of the run.

Before running: pick a checkpoint, images, and a video path; select 2+
prompts in the Notebook; pick (or capture) a lora state. Run count =
loras × strengths × grid images × prompts — start small.
