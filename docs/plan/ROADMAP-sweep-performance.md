# Sweep performance roadmap

Owner ask 2026-08-24: "Look for opportunities that may help optimize
long/complex runs … a few hundred items." Evidence gathered by two research
passes (core mechanics read against the rig's ComfyUI v0.31.1 with
file:line citations; micro-benchmarks on the rig venv) plus a live 200-run
rig sweep. **M1 SHIPPED as v0.80.0**; M2/M3 change node interfaces and wait
on the owner's word.

## Measured reality (2026-08-24)

- Pack-side per-run overhead: ~25–50 ms all-in (multiplier emission 5 µs/run;
  notebook/builder parse <1 ms/queue; provenance bake was 5.5–55 ms/save,
  now ~1.5–15 ms; PNG write 40–250 ms/image *equal to core's own SaveImage*,
  compress_level=4 both).
- Cross-prompt caching works (unchanged 100-run re-queue: ~1 s) — M1 made
  the pack stop breaking it (content-derived IS_CHANGED; Emit buffer token).
- **Core never dedups identical work inside one queue**: a mapped node runs
  once per list element regardless of identical inputs
  (`execution.py::_async_map_node_over_list` — no memo). So CLIPTextEncode
  downstream of the multiplier encodes M×I×T times when only M×T are
  distinct; VAEEncode of input images M×I×T when M×I are distinct.
- Core holds CPU RAM ≈ N × checkpoint size for the whole queue (the emitted
  list keeps every ModelPatcher alive; core's RAM-pressure eviction is
  structurally blind to live patchers). VRAM squatting by bulk-loaded
  models fixed in M1; CPU RAM is inherent to the list design.

## M1 — SHIPPED (v0.80.0)

Content-derived IS_CHANGED (Notebook selection / Builder blocks / pinned →
constant); Image Grid Emit buffer-token IS_CHANGED (unchanged buffer =
cached queue); EPS Save Image in-place bake + undo (deepcopy removed);
Checkpoint Switcher bulk-load VRAM parking; README "Long runs" guidance.

## M2 — pre-encoded axes through the multiplier (owner's word)

The ONLY lever core offers against the M×I×T duplicate-encode waste: encode
each distinct text/image ONCE, upstream, and carry the encoded values
through the multiplier. Sketch: optional `conditioning` input+output
(welded to the text axis the way clip welds to model) and/or `latent`
input+output (welded to the image axis) — wire CLIPTextEncode/VAEEncode
BEFORE the multiplier, per-axis, so encodes run once per distinct value and
the sweep fans out already-encoded pairs. Saves I× the CLIP encodes and T×
the image VAE encodes; at 4 images × 25 texts that's 100 encodes down to
29 per model. Open questions for the owner: which axes first; whether
conditioning also needs the model axis respected (an encode is
clip-specific, so conditioning must be a LIST aligned to model × text —
this is the design work). Tail-append outputs only (§8).

## M3 — batched VAE decode collector (owner's word)

Core's `VAE.decode` batches internally when handed a multi-image tensor,
but map-over-list always hands it batch-of-1. A small collector node
(gather N latents → one batched decode → re-split) could cut decode
overhead on large sweeps. Needs measurement on real GPU hardware first.

## Parked

- `compress_level` widget on EPS Save Image (1 vs 4 ≈ 25–33% faster writes,
  bigger files; core parity today). Owner's call whether the dial is wanted.
- `--cache-none` launch flag would defeat all of the above — never use it
  on the sweep machines.
