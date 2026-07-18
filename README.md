# EPSNodes

Eric Paul Snowden's ComfyUI node pack — practical workflow utilities that
live in **plain files you own**. Everything appears under **EPSNodes** in
the node browser and Settings. The first feature family is about LoRAs;
the pack will grow beyond them.

Current capabilities, no dependencies:

- **LoRA Notebook** — a node holding a scrollable list of entries (one per
  lora, or anything else) with an editor pane beside it. Backed by a plain
  **Markdown file you can put anywhere** — including a NAS folder shared by
  several machines. The selected entry's text is a `STRING` output you can
  wire into any prompt input. The file is the truth: edit it in ComfyUI, in
  VS Code, on the other computer — everything stays in sync.
- **LoRA Sets** — save a whole lora configuration (which loras, order,
  on/off, strengths) as a named set, then switch between sets from a
  dropdown. `Apply LoRA Set` works standalone (MODEL/CLIP in → out, plus a
  `LORA_STACK` and trigger words). If you use
  [rgthree's Power Lora Loader](https://github.com/rgthree/rgthree-comfy),
  the `LoRA Set Controller` drives it directly — capture its current rows as
  a set, apply a set back, reorder included.

> **Status: pre-release. All three capabilities ship today:** the **LoRA
> Notebook**, **Apply LoRA Set**, and the **LoRA Set Controller** (each
> described below). Contracts live in [docs/FORMAT.md](docs/FORMAT.md).

## LoRA Notebook (shipped)

`LoRA Library → LoRA Notebook`: a two-pane editor inside the node — entry
list on the left (grouped by `# Category` headings, with `＋ New` /
`🗑 Delete`), a flexible text editor + `Save` on the right. The selected
entry's text is the node's `STRING` output.

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

## LoRA Set Controller (shipped; requires rgthree-comfy)

`LoRA Library → LoRA Set Controller`: a small panel node that drives a
genuine, untouched
[Power Lora Loader (rgthree)](https://github.com/rgthree/rgthree-comfy)
elsewhere in your graph — rgthree stays the loader; this node just moves
whole configurations in and out of it:

- **Capture target → new set** reads the loader's current rows (which
  loras, order, on/off, strengths) into a named set file.
- **Apply set → target** rewrites the loader to match a saved set exactly —
  row count, order, toggles, strengths — one dropdown pick + one click to
  swap your whole lora setup. Update/Delete manage existing sets (delete is
  two-click confirmed).
- It's a frontend-only virtual node: it never executes and can't block a
  queue. If rgthree isn't installed (or its internals ever drift), the node
  disables itself with a message and points you at `Apply LoRA Set`, which
  needs no dependencies.
- Every `Apply LoRA Set` dropdown refreshes automatically after any
  capture/update/delete — no page reload.

## Apply LoRA Set (shipped)

`LoRA Library → Apply LoRA Set`: pick a set from the dropdown and every
enabled lora in it is applied **in the set's order** to the `model`/`clip`
you wire through. Outputs:

- `model`, `clip` — patched (or passed through untouched on `"None"`).
- `lora_stack` — a `LORA_STACK` list compatible with stack-consuming nodes
  from other packs.
- `trigger_words` — the set's stored trigger words, ready to concatenate
  into a prompt.

`strength_scale` multiplies every applied strength (quick global A/B).
Sets are JSON files in `<library folder>/sets/` — created via the API or
(soon) captured from a Power Lora Loader; hand-editing is fine too. Loras
referenced by a set resolve **separator-insensitively** with a unique-
basename fallback, so a set written on Windows applies on macOS and vice
versa; anything that can't resolve is skipped with a logged warning rather
than failing the run. After creating sets outside the graph, press `R`
(refresh node definitions) to update an open dropdown.

## Install

See [docs/INSTALL.md](docs/INSTALL.md). Short version: clone into
`ComfyUI/custom_nodes/` and restart ComfyUI. No pip requirements.

## The library folder

Everything lives in one folder — `loras.md` plus `sets/*.json` — configured
in **Settings → LoRA Library → Library folder** (server-side, so every
browser sees the same value). Point it at a shared/NAS path to use the same
library from multiple machines. Details: [docs/FORMAT.md §1](docs/FORMAT.md).

## Versioning

Backend and frontend each carry the pack version and it is shown in
**Settings → LoRA Library**; a mismatch means you pulled an update but
haven't restarted the server (or need a hard refresh). Every push bumps the
version and is tagged.

## License

MIT — see [LICENSE](LICENSE).
