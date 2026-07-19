# EPSNodes

Eric Paul Snowden's ComfyUI node pack — practical workflow utilities that
live in **plain files you own**. Everything appears under **EPSNodes** in
the node browser and Settings. The first feature family is about LoRAs;
the pack will grow beyond them.

Current capabilities, no dependencies:

- **Prompt Notebook** — your prompt library as a node: a scrollable list of
  named prompts with an editor pane beside it. Backed by a plain
  **Markdown file you can put anywhere** — including a NAS folder shared by
  several machines. Selected prompts flow out as `STRING`s you can wire
  into any text input. The file is the truth: edit it in ComfyUI, in
  VS Code, on the other computer — everything stays in sync.
- **LoRA states** — save a whole lora configuration (which loras, order,
  on/off, strengths) as a named state, then switch from a dropdown.
  `Apply LoRA Set` works standalone (MODEL/CLIP in → out, plus a
  `LORA_STACK` and trigger words). If you use
  [rgthree's Power Lora Loader](https://github.com/rgthree/rgthree-comfy),
  the `Lora Loader State Controller` drives it directly — capture
  its current rows as a state, apply a state back, reorder included.

> **Status: pre-release. All three capabilities ship today:** the **Prompt
> Notebook**, **Apply LoRA Set**, and the **Lora Loader State Controller**
> (each described below). Contracts live in
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

- **Picking a state applies it.** The `state` dropdown *is* the switch:
  choose one and the target loader's rows snap to it — count, order,
  toggles, strengths. (Reloading a saved workflow never re-applies; only a
  real selection does.)
- **New State** captures the loader's current rows into a named state
  file; **Save State** overwrites the selected state with the current
  rows; **Delete State** removes it (two-click "Are you sure?" confirm —
  the armed button turns red).
- **Multi-loader targeting:** with two or more Power Lora Loaders in the
  graph (WAN high/low noise, for example) the target dropdown offers
  `All Power Lora Loaders (N)` — one state pick updates every loader;
  capture reads from the lowest-numbered one.
- A debug `status` line is hidden by default — right-click the node →
  Properties → `Show status` to reveal it.
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

`strength_scale` multiplies every applied strength (quick global A/B).
States are JSON files in `<library folder>/sets/` — captured from a Power
Lora Loader by the State Controller, created via the API, or hand-edited. Loras
referenced by a set resolve **separator-insensitively** with a unique-
basename fallback, so a set written on Windows applies on macOS and vice
versa; anything that can't resolve is skipped with a logged warning rather
than failing the run. After creating states outside the graph, press `R`
(refresh node definitions) to update an open dropdown.

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
