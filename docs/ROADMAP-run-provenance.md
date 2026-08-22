# Roadmap: Run Provenance — identify and recreate one image from a multiplied set

Owner ask (2026-08-14): *"reliably drop a single image or video from a set
onto comfyui and be able to recreate just that image vs all of the images —
especially for large sets."* Two stated problems: (1) filenames don't say
which combination produced an image; (2) file-backed references (Notebook
prompts, Apply Set states) drift, so even a perfect name can't recreate the
image after a library edit.

Decisions pinned with the owner (AskUserQuestion, 2026-08-14):
- **Naming: pure indices** — `model2_vae1_img3_txt1` style. Always
  unambiguous; no new name-output plumbing required.
- **Recreate-one: baked per-image workflow.** Every saved image embeds its
  own copy of the workflow with the Run Multiplier pre-set to solo that
  run. Drop on canvas → the ENTIRE workflow loads through ComfyUI's
  bone-stock path; queue → just that image. Visible solo badge on the
  multiplier, one-click back to the full set.
- **Drift: FULL PINNING** — recreation is byte-faithful even after library
  edits, with the captured old values visibly pinned (badged) on the
  Notebook/Apply Set and one-click unpin back to the current library.
- **Videos: out of scope for v1** (images first; videos a later milestone,
  sidecar-JSON design sketched below).

## Research ground truth (2026-08-14, agents + local inventory)

Core (read from the rig's ComfyUI v0.31.x source):
- `%Node.widget%` filename tokens resolve ONCE per queue, client-side
  (`saveImageExtraOutput.ts` → `applyTextReplacements`), so they can NOT
  carry per-run identity. But a save node's `filename_prefix` INPUT is
  sliced per list element — the multiplier's per-run `save_prefix` list is
  already the right vehicle. Backend re-resolves only `%width%/%height%`
  + date parts per call (`folder_paths.get_save_image_path`).
- Fan-out saves embed BYTE-IDENTICAL `prompt`/`workflow` chunks in every
  image (`hidden` inputs are 1-element lists; `slice_dict` index -1
  fallback). Per-image identity therefore requires our own save node —
  a per-run STRING list input slices correctly per item.
- Drop-to-load: canvas drops parse PNG tEXt chunks and load `workflow`
  (priority) via `loadGraphData`. A node-targeted `onDragDrop` prototype
  hook exists but canvas drops can't be intercepted — which is fine: the
  baked-workflow design uses the standard path on purpose.
- Hidden `PROMPT` input gives a node the full API prompt dict (same object
  for every fan-out element).
- SaveVideo (MP4-only in this build) + SaveWEBM embed container metadata
  tags; reliability varies by container (ecosystem reports mp4 failures).

Ecosystem (survey): ComfyUI-Image-Saver, SaveImageWithMetaData(+Universal),
WAS tokens, Efficiency XY Plot, VHS. **All operate one-execution-one-save.
Nothing does per-item identity inside a multi-run queue, frozen-value
capture that survives library drift, or drop-one-to-recreate-one.** All
four target capabilities are net-new. Reusable prior art: graph-traversal
value capture (SaveImageWithMetaData) and filename-token vocabularies.

Local inventory:
- Already flowing through the multiplier per run: resolved prompt TEXT
  (drift-proof), entry `name`, sweep `label`, vae index, model_low.
- Already drift-proof inside the workflow file itself: switcher toggles,
  iterator params, picker `selection` (serialized widget), seeds/samplers,
  checkpoint filenames. The ONLY file-backed drift points: Notebook entry
  text (md) and Apply Set state rows (json). Image Grid buffers are also
  server-side mutable (noted; grid pinning is a stretch item).
- Apply Set already emits `loras_text`; `sets_store`/`markdown_store` are
  importable server-side — a save-time node in this pack can resolve
  slugs/entries to VALUES authoritatively at the save moment.

## Design overview

New primitive: the **run token** — pure indices, stable within a queue:
`m{model_idx}_v{vae_idx}_i{image_idx}_t{text_idx}` (axes present only when
wired; sweep label index doubles for iterator steps). Plus a per-queue
`set_id` (short uuid) so tokens from different sets never collide.

Data flow: the multiplier emits a per-run `run_info` JSON (token, indices,
captured text/name/label, its own node id, set_id). A new **EPS Save
Image** node (SaveImage sibling) takes `images` + `filename_prefix` +
optional `run_info`, and per image:
1. names the file with the token (indices in the filename, owner's pick);
2. embeds an `eps_run` tEXt chunk (the full record, incl. captured values);
3. embeds a BAKED `workflow` chunk: deep-copy of the live workflow with
   (a) the multiplier's `solo_run` widget set to this token, and
   (b) pinned-value widgets written into the Notebook/Apply Set nodes
   (full pinning, below).
Core's own `prompt` chunk stays for compatibility.

## Milestones

### M1 — Run tokens + solo (independently useful: identifiable files, manual re-run) — SHIPPED v0.67.0 (2026-08-18)
- Multiplier: token computation; `save_prefix` gains the token as the pair
  component's suffix (or dedicated `run_token` STRING output — decide at
  build time by testing what reads best in folder trees); `set_id` per
  queue.
- Multiplier: `solo_run` widget (serialized; text; empty = all runs).
  When set: emit only the matching run's columns; readout shows
  `Solo: m2_v1 — 1 of 8 runs` with a visible badge; clearing restores the
  set. Estimator mirrors solo exactly (referee discipline).
- Manual recreate already works here: read the token off a filename, type
  it into solo.

### M2 — EPS Save Image + baked workflows (drop-to-recreate, current values) — SHIPPED v0.70.0 (2026-08-21; plus the filename-token drop fallback for pre-M2 files)
- `run_info` output on the multiplier (STRING list, one JSON per run).
- EPS Save Image node: SaveImage-compatible signature + optional
  `run_info`; writes token-named files, `eps_run` chunk, and the baked
  per-image `workflow` chunk with `solo_run` pre-set (find the multiplier
  node in the workflow JSON by the node id carried in run_info; subgraph
  path ids supported — v0.64.0 walkers).
- Drop on canvas → whole workflow, soloed. Queue → one image. No custom
  drop handler; ComfyUI's standard path does everything.
- README/FORMAT: the "recreate one image" story.

### M3 — Full pinning (byte-faithful recreation after library edits) — SHIPPED v0.72.0 (2026-08-22)
- Capture: EPS Save Image (server-side, at save time) walks its hidden
  PROMPT for `LoraLibraryNotebook` / `LoraLibraryApplySet` /
  `EPSLoraPicker` nodes and resolves their file-backed references to
  VALUES via this pack's own stores (md entry text; state rows). Notebook
  multi-select: match THIS run's entry via run_info's text/name. Picker
  needs no capture (selection already in the workflow).
- Pin mechanism: Notebook gains a serialized `pinned` widget
  (`{entry, text}`) — when set, outputs the pinned text and shows a
  visible "pinned — captured from image" badge with the old text readable
  in the editor pane (read-only until unpinned). Apply Set gains
  `pinned_state` (`{slug, rows}`) — applies those rows instead of the
  slug lookup, badge shows the pinned rows. One-click unpin each.
- The baked workflow writes these pinned widgets per image, so pinning
  rides the SAME standard drop path — nothing new to intercept.
- Drift visibility: when a pinned value differs from the current library,
  the badge says so explicitly (pin badge = the diff indicator).
- §8 care: new widgets on Python nodes are TAIL-appended (positional
  widgets_values law); pinned widgets are serialized on purpose (they ARE
  the mechanism).

### M4 (later) — Videos + stretch
- Sidecar `*.eps_run.json` next to saved videos (+ MP4/WebM container tags
  where reliable); dropping the sidecar or tagged video onto the canvas
  needs a load path — investigate frontend handling of json drops vs a
  small loader affordance.
- Image Grid element pinning (buffer refs are mutable server-side) — only
  if grid-fed sets prove drift-painful in practice.
- Optional A1111 `parameters` chunk for civitai compatibility (ecosystem
  standard; orthogonal, cheap to add to EPS Save Image).

## Risks / notes
- Baked workflow size: one workflow JSON per image (~10-100KB) — same
  order as what core already embeds; fine.
- `solo_run`/pinned widgets must not break older builds: tail-appended,
  absent = current behavior (empty solo = all runs; empty pin = live
  library).
- The multiplier's indices must be STABLE across the queue they were
  captured in — they are (deterministic emission order); across graph
  EDITS they are not, which is exactly why the baked workflow (not the
  token alone) is the recreation vehicle.
