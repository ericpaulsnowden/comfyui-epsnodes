# Roadmap — EPS Image Grid: bulk add (upload button + folders)

**Status:** planned, not started. Researched 2026-07-28.
**Owner ask:** *"Is it possible to give the image grid an upload button? Ideally that upload
button would allow multiselect to add many images at once. Without multiselect I don't think
it's worth it, but right now it's really hard to add many images to comfy."*

**Owner answers that shaped this plan (2026-07-28):**

| Question | Answer |
|---|---|
| Build it? | Build the thin version |
| Where the pain actually is | **The Finder drag dance** + **whole folders at once** |
| Typical batch size | **20–100 images** |
| Ordering | **Filename, numeric-aware** (`img2` before `img10`) |

Note what is *not* on that list: getting Mac files onto the PC's ComfyUI was offered and not
picked. Design accordingly — the LAN case still works for free (uploads are not
secure-context-gated, unlike clipboard), but it is not the thing to optimize for.

---

## The finding that reframes the whole feature

**Multi-file ingest already exists and appears to work.** `addFilesToBuffer(node, files)`
(`web/eps_image/image_grid.js:1126-1145`) filters `Array.from(files)` for `image/*` and loops
over **every** file. Both callers hand it the complete list: `pasteFiles`
(`:1156-1161`) and the drop handler (`:1287-1301`, reading the whole
`e.dataTransfer.files`). The pack deliberately avoids litegraph's `onDropFile`, which is
documented at `:514-517` as firing once per file with the *same* file on a multi-drop.

So dragging 30 files onto the node in one go should already append all 30, in order.

**Caveat: nothing tests any of it.** No test touches `pasteFiles`, `onDragDrop`, or
`addFilesToBuffer`, and there is no HTTP test for `POST /eps_image_grid/add` either (only
`/list` and `/clone` are covered). This is a code-reading claim, not a verified one — which is
why M0 exists.

**Therefore the button is an ergonomics fix, not new plumbing.** ~15 lines. The real work is
the three things bulk exposes that single-add never did (M2), and folder import (M3).

---

## Market check — is this redundant?

**As an idea: yes, thoroughly.** As a thing *this pack* should have: also yes, and cheaply.

Verified prior art with a genuine multiselect upload button + on-node thumbnails:

| Repo | Stars | State |
|---|---|---|
| [vslinx/ComfyUI-vslinx-nodes](https://github.com/vslinx/ComfyUI-vslinx-nodes) | 20★ | **Maintained, in the Comfy Registry** (64k downloads). The safest comparable. |
| [Latentnaut/ComfyUI-Multi-Image-Loader](https://github.com/Latentnaut/ComfyUI-Multi-Image-Loader) | 7★ | Best thumbnail grid found (4-col, drag-reorder, per-tile remove). Not in registry; build debris in repo root. |
| [xmarre/ComfyUI-Image-Conveyor](https://github.com/xmarre/ComfyUI-Image-Conveyor) | 25★ | **Closest to EPS Image Grid overall** — multiselect picker + on-node thumbnails + *one run per image*. |
| ermin44/LoadImageBatchUpload, MG-CP/CP_MultiImageLoader, balarooty/mult_image, yy-king/MultiImageUploadCanvas | 0–2★ | Real implementations; all abandoned within days, or capped (3 images). |

Adjacent, widely used: **VHS `Load Images (Upload)`** uses `webkitdirectory: true` — a *folder*
picker, not a file picker. It cannot take an arbitrary subset, and re-uploading to an existing
folder name is blocked outright (`alert("A folder of the same name already exists")`).

**Why build it anyway:** none of those feed *this* buffer. EPS Image Grid's value is the
persistent cross-run buffer plus Emit fan-out plus Cross Product / Run Multiplier (fka Cross Sweep). Swapping in a
foreign loader means losing that pipeline. The button completes a node whose hard part is
already built — it is catching up on table stakes, not competing on novelty. Plan and describe
it that way; do not market it as new.

**Upstream context:** ComfyUI core still has no multi-image upload.
[ComfyUI#2609](https://github.com/comfyanonymous/ComfyUI/issues/2609) has been open with **zero
maintainer comments since Jan 2024**, and
[frontend#3461](https://github.com/Comfy-Org/ComfyUI_frontend/issues/3461) (generic file upload
for custom nodes) is open and `help wanted`. The gap is structural, not temporary.

**The one-line shortcut, and why to skip it:** declaring
`{"image_upload": True, "allow_batch": True}` makes core's own frontend set
`fileInput.multiple` with no custom JS
([`useNodeFileInput.ts`](https://github.com/Comfy-Org/ComfyUI_frontend/blob/main/src/composables/node/useNodeFileInput.ts)).
Tempting, but: it is undocumented, core deleted its only consumer (`LoadImageSetNode`), it
stuffs an array into a combo widget behind a `@ts-expect-error`, and it gives no thumbnail grid
— which is probably why every pack rolls its own. **Worth a 10-minute spike, not a dependency.**

---

## Milestones

Each is independently shippable and independently useful.

### M0 — Prove and document what exists *(~1h)*

The cheapest possible win, and it de-risks everything after it.

- Write the **first test for the multi-file path**: a `test_image_grid_js.py`-style Node probe
  that calls `addFilesToBuffer` with 3 fake `File`s and asserts 3 uploads in order.
- Write the **first HTTP test for `POST /eps_image_grid/add`** (the route has none).
- Confirm on-device: select 20 files in Finder, drag as one drop, count what lands.
- README currently says **"Three ways to ADD an image"** (`README.md:356`) — correct it and say
  plainly that a multi-file drag works.

**User-visible value:** if drag already works, that alone may solve the stated pain today.

### M1 — "Add images…" button *(~2h)*

- `node.addWidget('button', 'Add images…', null, cb, {})` — copy `addClearButton`
  (`image_grid.js:864-869`) exactly, **including `widget.serialize = false` on the instance**
  (the options-bag form sets the wrong flag on this fork; documented at
  `web/lora_library/controller.js:568-580`).
- Detached `<input type="file" multiple accept="image/png,image/jpeg,image/webp">`, `.click()`,
  reset `value=''` in `onchange` so re-picking the same file fires again.
- **Sort with a numeric collator before ingest** — `new Intl.Collator(undefined, {numeric: true,
  sensitivity: 'base'})`. FileList order is unspecified by spec; on Windows the last-clicked file
  jumps to the front. Apply the same sort to the existing drop path, which is unsorted today.
- Hand the sorted array to the existing `addFilesToBuffer`. No new ingest code.

**⚠️ Do NOT set `canvasOnly: true`** (core's own upload widget does). Under Nodes 2.0 a
`canvasOnly` widget renders *nowhere* — verified in frontend v1.49.0. Eric's rig is a source
install (Vue nodes off by default), but Desktop and Cloud installs default it **on**.

**User-visible value:** the Finder drag dance is gone — the #1 stated pain.

### M2 — Make 20–100 images actually pleasant *(~3h)* — **required at the stated batch size**

The single worst bug in the current path, and it is already there for drag-and-drop:

> `addFilesToBuffer` calls `setNodeImagesFromRefs(node, result.images)` **inside** the per-file
> loop (`:1136-1138`), and that rebuilds an `Image()` for **every ref in the whole buffer**. Its
> unchanged-content short-circuit can never fire mid-batch. Worse, `imageUrlForRef` appends
> `rand=Math.random()` (`:892`), so every one is an uncached **full-resolution** fetch.
> **100 files ≈ 5,000 full-size image loads.**

- Hoist the refresh **out of the loop** — once at the end, or every K files.
- Request compressed thumbnails (`&preview=webp;80`) — core's own `Comfy.PreviewFormat`
  mechanism; the Vue image preview already does this via `getGridThumbnailUrl()`.
- **Progress**: mutate `widget.label` in place (`"Adding 12/40…"`), which only changes what is
  painted; `name` stays the lookup key. Restore on completion.
- **Errors**: today every per-file failure is a silent `console.warn` (`:1139-1141`). Aggregate
  and emit **one** toast with added/skipped/failed counts via
  `app.extensionManager?.toast?.add?.()` — the pattern `notifyClipboard` already uses (`:1400`).
- **Detect silent skips**: the backend fails *soft* with HTTP 200 — `append_uploaded_image`
  returns the unchanged buffer on an unreadable file rather than raising
  (`image_grid_store.py:333-351`). Compare `result.images.length` before/after; nothing does
  today.
- **Cancel**: an `AbortController` per batch, wired to a second click on the button.

**User-visible value:** 40 images completes in a sane time, visibly, and tells you what happened.

### M3 — "Add folder…" *(~2h)* — the second stated pain

- Second button with `webkitdirectory: true` (the VHS pattern) — the OS folder dialog, then
  filter to images, sort numerically, feed the same path.
- Recurse subfolders (a directory pick yields all descendants via `webkitRelativePath`).
- Guard the obvious foot-gun: a 2,000-file folder should warn before starting, not just go.

**Beats the closest comparable:** VHS is folder-*only* with no thumbnails and refuses a
same-named re-upload; this appends to a live buffer you can see.

### M4 — Batch `/add` route *(~2h, only if M2 isn't enough)*

Today each image is **two** sequential HTTP round trips (`/upload/image` then
`/eps_image_grid/add`). Uploads could run 3–4 wide (pure I/O), but `/add` **must stay
sequential** — `append_uploaded_image` does an unlocked read-modify-write of `manifest.json`,
so concurrent adds would lose entries.

If needed: add `POST /eps_image_grid/add_many` taking a list. **Do not repurpose `/add`** —
`docs/FORMAT.md:1642-1644` freezes shipped route contracts; new capabilities add routes/fields.

### M5 — Grid management *(deferred — deliberately)*

Per-thumbnail delete, drag-to-reorder. This is the most crowded part of the market
(Latentnaut, MG-CP, ermin44 all ship it) and the least connected to the stated pain. Revisit
only if bulk add lands and the buffer becomes hard to curate.

---

## Risks and invariants a bulk path must respect

1. **uuid remint race.** The uuid is re-read per file (`:1090`), and a collision sweep is
   debounced 1500ms (`:1956`) — a long batch can straddle a remint, sending the tail into a
   different buffer than the head. Worse, `clone_buffer` **replaces** the destination manifest
   (`image_grid_store.py:489`), so concurrent appends survive on disk but vanish from the
   manifest. Capture the uuid once per batch, and serialize bulk-add against clone.
2. **Never write the uuid.** Read via `currentUuid` only (`:660-662`); `FORMAT.md:946-957`
   requires identity to stay stable across load/undo/configure.
3. **Never reconstruct filenames.** ComfyUI auto-suffixes collisions as `IMG_1234 (1).jpg`, and
   short-circuits identical bytes to a no-op returning the *existing* name. Always thread the
   response `{name, subfolder, type}` through.
4. **No dedupe on disk** — adding the same file twice creates two frames. `mergeBufferRefs`
   (`:950-965`) dedupes the display path only. Decide explicitly whether bulk add should dedupe.
5. **Nodes 2.0**: no `canvasOnly`; `onDrawForeground` is never called there either.
6. **Per-request upload cap is 100 MB** (`--max-upload-size`), one file per request — a big
   batch can't hit it, a single huge file can.
7. **Buffer has no cap by design** (`FORMAT.md:910`). Emit fan-out means 100 buffered images =
   100 downstream runs. M3's folder picker makes reaching that trivial — the warn matters.

## Pack conventions this work must follow

- **`docs/FORMAT.md` §6.6 is the binding contract** — amend it with a dated, owner-anchored
  subsection (closest precedent: "Drop-to-add (2026-07-22, owner ask)" at `:1004-1012`).
- **Version bump on every push**: `scripts/bump_version.py` keeps `lora_library/version.py`,
  `pyproject.toml`, `web/lora_library/version.js` in lockstep (all at **0.35.8** today). Commit
  style: `Area: plain-language outcome (vX.Y.Z)`; docs-only changes don't bump.
- **README**: update the node's `## EPS Image Grid (shipped)` section and its row in the
  node table under `## The fifteen nodes`; owner-facing plain language, bolded lead-ins.
- **Tests**: pytest `asyncio_mode = "auto"`; JS tested headlessly under Node by copying the
  module into a served layout with stubbed `scripts/app.js`/`api.js` at the exact relative depth
  (`tests/test_image_grid_js.py:181-212`).
- **Frontend**: one `app.registerExtension` per family, every call wrapped in `safely()`,
  per-node install guards, and **wrap-never-replace** for any core method.

## Suggested order

**M0 → M1 → M2** is the coherent first release (roughly one focused day) and covers the stated
pain completely. **M3** is a natural second release. M4 only if measured. M5 probably never.
