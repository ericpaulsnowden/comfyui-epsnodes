# EPSNodes

Eric Paul Snowden's ComfyUI node pack — practical workflow utilities that
live in **plain files you own**. Everything appears under **EPSNodes** in
the node browser and Settings. It started as a LoRA family and has grown
beyond it — image-flow utilities now live here too.

Current capabilities, no dependencies:

- **Prompt Notebook** — your prompt library as a node: a scrollable list of
  named prompts with an editor pane beside it. Backed by a plain
  **Markdown file you can put anywhere** — including a NAS folder shared by
  several machines. Selected prompts flow out as `STRING`s you can wire
  into any text input. The file is the truth: edit it in ComfyUI, in
  VS Code, on the other computer — everything stays in sync.
- **LoRA states** — save a whole lora configuration (which loras, order,
  on/off, strengths) as a named state, then switch from a list.
  `Apply LoRA Set` works standalone (MODEL/CLIP in → out, plus a
  `LORA_STACK` and trigger words). If you use
  [rgthree's Power Lora Loader](https://github.com/rgthree/rgthree-comfy),
  the `Lora Loader State Controller` drives it directly — capture
  its current rows as a state, apply a state back, reorder included.
- **Image utilities** — **EPS Switcher** toggles any number of image inputs
  on/off and fans the enabled ones out (N enabled → the workflow runs N
  times); **EPS Resolution** is an image-first, all-in-one resize + size
  node (target size, four resize modes, and the original image + both sizes
  passed through) so one node replaces a resize + a reroute + a get-size;
  **EPS Image Grid** collects images across separate Runs into a navigable
  grid and fans the whole set out (gather 10, then run them through a
  workflow at once); **EPS Frame Saver** loads a video by path, lets you
  scrub/play to a frame, and outputs that frame as an image.

> **Status: pre-release. Seven capabilities ship today:** the **Prompt
> Notebook**, **Apply LoRA Set**, the **Lora Loader State Controller**,
> **EPS Switcher**, **EPS Resolution**, **EPS Image Grid**, and **EPS Frame
> Saver** (each described below). Contracts live in
> [docs/FORMAT.md](docs/FORMAT.md).

## Prompt Notebook (shipped)

`EPSNodes → Prompt Notebook`: a two-pane editor inside the node — entry
list on the left (grouped by `# Category` headings, with `＋ New` /
`🗑 Delete`), a flexible text editor + `Save` on the right. Outputs:
`text` and `name` (the entry's heading — handy for filename prefixes and
captions).

- **Select several prompts, get one run per prompt:** ctrl/cmd+click
  toggles entries into the selection, shift+click selects a range. Queue
  once and the workflow executes once per selected prompt, in selection
  order, with `text`/`name` paired. A single selection behaves exactly
  like a plain string.
- **Drag to reorder:** drag entries within the list (an insertion line
  shows the landing spot); drop onto a category header to move an entry
  into that category — the file is rewritten to match, byte-safe.
- **Categories, made in the UI:** click `＋ New` and type a name starting
  with `#` (e.g. `# Styles`) to create a category instead of a prompt.
  Click any category header and the editor switches to that category's
  **description** — the prose that lives under its `# heading` in the
  markdown, so it reads naturally in any text editor too. A hint above the
  editor always says whether you're editing a prompt or a category.

- **Plain Markdown, yours:** one `## Entry Name` per entry in a file you
  point the node at — relative names live in the library folder, absolute
  paths (including NAS shares) work as-is. Edit it in ComfyUI, VS Code, or
  on the other machine; the node re-reads the file every run, so external
  edits are picked up automatically (`IS_CHANGED` hashes the file).
- **Safe for shared files:** saving checks the file hasn't changed under
  you since you loaded it. If it has (the other machine got there first),
  nothing is written — you get *File changed on disk* with **Reload** /
  **Overwrite** to resolve it yourself. Writes are atomic, and the file's
  existing CRLF/LF style is preserved so cross-OS diffs stay clean.
- The workflow stores only the file path + selected entry name — never the
  text. The file is the truth; the node is a view.

## Lora Loader State Controller (shipped; requires rgthree-comfy)

`EPSNodes → Lora Loader State Controller`: a small panel node that
drives a genuine, untouched
[Power Lora Loader (rgthree)](https://github.com/rgthree/rgthree-comfy)
elsewhere in your graph — rgthree stays the loader; this node just moves
whole configurations ("states") in and out of it:

- **Two-pane layout** (like the Prompt Notebook): a scrolling list of all
  your saved states on the left, the buttons stacked on the right.
- **One click selects, a second click applies.** A single click just
  *selects* a state (highlights it, loads its name) — it does **not** touch
  your loaders, so you can safely rename or delete a state without rewriting
  every wired loader. Clicking the already-selected state again applies it:
  the target's rows snap to it — count, order, toggles, strengths. (Reloading
  a saved workflow never re-applies; only that second click does.)
- **New State** captures the loader's current rows into a named state
  file; **Save State** overwrites the selected state with the current rows —
  **unless you've typed a new name in the field, in which case it saves a
  brand-new state under that name** (the quickest way to spin a variant off an
  existing state); **Delete State** removes it (two-click "Are you sure?"
  confirm — the armed button turns red).
- **Multi-loader targeting:** with two or more Power Lora Loaders in the
  graph (WAN high/low noise, for example) the target dropdown offers
  `All Power Lora Loaders (N)`. With `All` selected, a state stores **each
  loader's OWN config** (a "composite" state): New/Save State captures every
  loader distinctly, and picking that state restores each loader to its own
  rows — so one state file holds your whole WAN high+low setup. To feed those
  distinct configs into the standalone `Apply LoRA Set` loaders, give each
  Apply node a `loader_slot` (0 = first loader, 1 = second, …; revealed via
  right-click → Properties → `Show loader slot`). Single-loader states are
  unchanged and fully backward-compatible.
- A debug `status` line is hidden by default — right-click the node →
  Properties → `Show status` to reveal it.
- **If a saved state ever disagrees with what you set on the loader**, the
  capture now reads the loader's own serialized row values (the same source
  your saved workflows use) and every New/Save State leaves a compact
  per-row trace in the browser console. Right-click → Properties →
  `Debug capture` adds a full table of each row's raw values — paste that
  with any bug report and the cause is pinpointed.
- It's a frontend-only virtual node: it never executes and can't block a
  queue. If rgthree isn't installed (or its internals ever drift), the node
  disables itself with a message and points you at `Apply LoRA Set`, which
  needs no dependencies.
- Every `Apply LoRA Set` dropdown refreshes automatically after any state
  change — no page reload.

## Apply LoRA Set (shipped)

`EPSNodes → Apply LoRA Set`: pick a saved state from the dropdown and every
enabled lora in it is applied **in order** to the `model`/`clip` you wire
through — it *is* a loader, no Power Lora Loader involved. (For WAN-style
dual-model workflows: two Apply nodes, one in the HIGH branch, one in the
LOW branch, each with its own state.) Outputs:

- `model`, `clip` — patched (or passed through untouched on `"None"`).
- `lora_stack` — a `LORA_STACK` list compatible with stack-consuming nodes
  from other packs.
- `trigger_words` — the state's stored trigger words, ready to concatenate
  into a prompt.
- `loras_text` — what was applied, as normalized filename-friendly tokens:
  `detailer_0.8 film_grain_1` (a dual clip strength appends too:
  `detailer_0.8_0.4`; values reflect `strength_scale`). Wire it into
  captions, filenames, or notes.

`strength_scale` multiplies every applied strength (quick global A/B) — an
edge-case override that's **hidden by default** so the node passes the set's
own strengths straight through; reveal it with right-click → Properties →
`Show strength scale`.
States are JSON files in `<library folder>/sets/` — captured from a Power
Lora Loader by the State Controller, created via the API, or hand-edited. Loras
referenced by a set resolve **separator-insensitively** with a unique-
basename fallback, so a set written on Windows applies on macOS and vice
versa; anything that can't resolve is skipped with a logged warning rather
than failing the run. After creating states outside the graph, press `R`
(refresh node definitions) to update an open dropdown.

The weight math is covered by a permanent numeric regression test
(`tests/test_nodes_sets_weight_math.py`): it drives this node against real
ComfyUI patching machinery and asserts the patched model AND clip weights
equal the first-principles `base + strength × (alpha/rank) × (up·down)`
expectation — including stacked rows, a dual clip strength, a disabled row,
and `strength_scale`. It needs `torch` plus an importable ComfyUI (set
`EPS_COMFYUI_ROOT=/path/to/ComfyUI` if `comfy` isn't already on the path)
and skips cleanly where those are absent.

## EPS Switcher (shipped)

`EPSNodes → EPS Switcher`: wire in **any number of images**, flip each one
on or off, and the enabled ones flow out as a list — so the rest of the
workflow **runs once per enabled image**. Four images in with one turned
off means three runs.

- **Grows as you wire:** connect the last image socket and a fresh empty one
  appears; a connected socket never renumbers, so your wires stay put.
- **A toggle on every row**, plus a **Toggle All** header (tri-state: all
  on / all off / a dash for mixed, with a live `enabled/total` count) — the
  same one-click-everything control as rgthree's Power Lora Loader.
- **Fan-out, not pick-one:** unlike a normal switch that forwards a single
  chosen input, EPS Switcher forwards *all* the enabled ones and lets
  ComfyUI iterate. (A scalar wired downstream — e.g. a seed — repeats
  identically across the runs; use a per-image list for per-image
  variation.)
- **All off is allowed:** toggle everything off (or wire nothing) and the
  queue still succeeds — the image branch simply doesn't run that time. No
  error, no downstream crash.
- **Double-click a row to rename it:** the label is display-only (wires,
  toggles, and the backend still see `image_N`), persists with the
  workflow, and an empty name resets it.
- Toggle states save with the workflow and survive reload.

## EPS Resolution (shipped)

`EPSNodes → EPS Resolution`: one image-first node for the everyday
"resize this and tell me the sizes" job — set a target width/height, pick a
mode, and get back the resized image **and** the original, plus both sets of
dimensions. It replaces a resize node + a reroute + a get-image-size node.

- **The size grid:** a full-width square drag pad right on the node — drag
  anywhere and `width`/`height` follow, snapping to `multiple_of` (or 64 when
  it's off). The pad is locked to the node's left and right edges (no wasted
  space beside it), and it's a true square, so a 1:1 target sits on the
  diagonal. **Make it bigger by dragging the node wider** — the square (and the
  node's height) grow to match; narrow the node and it shrinks back. Hold
  **Shift** for a 1:1 square, **Ctrl/Cmd** to keep the aspect ratio the box had
  when you started dragging. The typed fields and the grid stay in sync (edit
  either); a compact readout under the pad shows `W x H` with the megapixels
  right-aligned on the same line and the reduced aspect (3:2) below it, and
  right-click Properties offers `Grid max` (range) and `Show grid` (hide the
  pad
  entirely if you only want the numbers).
- **Four resize modes:** `stretch`, `keep aspect (fit)`, `crop to fill`,
  and `pad` (black), with a choice of interpolation. `multiple_of` snaps the
  result to a multiple (e.g. 64) for latent-friendly sizes.
- **Set one axis to `0`** to derive it from the other and the image's aspect.
- **Outputs:** `resized_image`, `width`, `height` out of the box —
  `width`/`height` report the actual resized dimensions, and with no image
  wired the node still emits your target size, so it doubles as a pure size
  source. The untouched `image` passthrough and `original_width`/
  `original_height` outputs are **hidden by default**: right-click →
  Properties → `Show passthrough image` / `Show original size` to reveal
  them (it won't hide one that's still wired).
- Deliberately thin — pipe `width`/`height` into a heavier resize node for
  anything fancier.

## EPS Image Grid (shipped)

`EPSNodes → EPS Image Grid`: a node that **collects images across separate
Runs** into a buffer and then fans them out — wire a loader in, run it a few
times to gather images, then send the whole set through a workflow at once.

- **Collect / Emit:** in **Collect** mode each Run adds the current input
  image to the buffer (it *keeps* them — unlike a Preview node that replaces);
  switch to **Emit** and a Run just sends the buffer out without adding.
- **Navigable grid:** the collected images show as a clickable thumbnail grid
  right on the node (ComfyUI's own image viewer — click to enlarge, arrow
  through them).
- **Fan-out outputs:** `image`, `width`, `height` — wire them downstream and
  the workflow runs once per collected image (10 images → 10 runs, e.g. to
  put a logo on 10 models' shirts). All-empty runs are skipped cleanly, never
  a crash.
- **Survives restarts:** the buffer lives on disk (under ComfyUI's output
  folder, keyed to that node), so it's still there after you close and reopen
  ComfyUI. A **Clear** button wipes it; deleting the node abandons it. No cap.
- Each node keeps its own independent buffer, even after copy/paste.
- **Copy/paste:** right-click a collected image → Copy image (to the OS
  clipboard, for Photoshop/etc.) or Copy (Clipspace) (into the mask editor or
  another node); and with the node selected, **Ctrl+V** an image to add it to
  the buffer.
  - *Viewing ComfyUI on another machine over plain `http://` (e.g. a Mac
    pointed at a PC's LAN address)?* Browsers block writing an image to the OS
    clipboard outside a "secure context", so **Copy image** can't reach the OS
    clipboard there — it falls back to copying the image's link and opening it
    in a new tab (right-click → Copy Image there for a true copy), and tells
    you so. **Copy (Clipspace)** works everywhere. For real OS image-copy, use
    the ComfyUI desktop app or open ComfyUI via `localhost`/`https`.

## EPS Frame Saver (shipped)

`EPSNodes → EPS Frame Saver`: load a video and pull a single frame out of it
as an image.

- **Point at a video by path** — click **Browse…** to pick a file (nothing is
  copied; it reads your file in place, NAS included). Host-machine only, like
  the other pickers. **Or paste a path:** copy a video file's path (Finder's
  "Copy as Pathname" / Explorer's "Copy as path"), select the node, and press
  **Ctrl/Cmd+V** — it loads that video. (Quotes and `file://` wrappers are
  handled; pasting into a text field still pastes normally.)
- **Scrub to the frame you want:** the node shows the video with play/pause,
  step −1/+1 one frame at a time, a frame-number box, and a live
  **Frame X / N** counter (total frames included).
- **Outputs** `image`, `width`, `height` for the selected frame. The on-screen
  preview is best-effort (browser video seeking isn't always frame-perfect);
  the frame extracted on Run is exact (decoded server-side with PyAV).
- Common codecs (H.264 mp4, webm) play and scrub smoothly; an exotic codec the
  browser can't decode still extracts correctly on Run, it just won't preview.

## Install

See [docs/INSTALL.md](docs/INSTALL.md). Short version: clone into
`ComfyUI/custom_nodes/` and restart ComfyUI. No pip requirements.

## The library folder

Everything lives in one folder — `loras.md` plus `sets/*.json` (your prompt notebook + saved lora states) — configured
in **Settings → EPSNodes → Library folder** (server-side, so every
browser sees the same value). Point it at a shared/NAS path to use the same
library from multiple machines. Details: [docs/FORMAT.md §1](docs/FORMAT.md).

## Versioning

Backend and frontend each carry the pack version and it is shown in
**Settings → EPSNodes**; a mismatch means you pulled an update but
haven't restarted the server (or need a hard refresh). Every push bumps the
version and is tagged.

## License

MIT — see [LICENSE](LICENSE).
