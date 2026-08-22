# EPSNodes

Eric Paul Snowden's ComfyUI node pack — practical workflow utilities that
live in **plain files you own**. Everything appears under **EPSNodes** in
the node browser and Settings. It started as a LoRA family and has grown
beyond it — image-flow utilities now live here too.

## The sixteen nodes

No third-party packs required — every node here runs on ComfyUI alone;
the Lora Loader State Controller is built to extend rgthree-comfy, but only
its Power Lora Loader targets need it — its EPS LoRA Picker targets work
without rgthree installed. Some nodes are drag-and-drop
on their own; others are designed to pair with a saved set or another EPS
node — the **Works with** column says which. Each node also has its own
section further down; this is the map.

| Node | What it does | Works with |
| --- | --- | --- |
| [**EPS Prompt Notebook**](#eps-prompt-notebook-shipped) | Your prompt library as a node — a scrolling list of named prompts with an editor beside it, backed by a plain Markdown file you own (local or on a NAS). Select several and the workflow runs once per prompt. | Nothing — its own Markdown file (auto-created). |
| [**EPS LoRA Picker**](#eps-lora-picker-shipped) | Browse your loras by folder instead of one flat list — drill down, star favorites, see recents, pin a per-workflow folder scope — and build a selection with per-lora strengths. Outputs a `LORA_STACK`, patched model/clip, trigger words, and a filename token. | Your LoRA files; nothing else — favorites/recents live in the shared library folder. |
| [**EPS Apply LoRA Set**](#eps-apply-lora-set-shipped) | Pick a saved lora configuration ("state") from a dropdown and apply it — which loras, order, on/off, strengths. Standalone: MODEL/CLIP in → out, plus a `LORA_STACK` and trigger words. | Your LoRA files + a saved set (via the Controller, the API, or by hand). |
| [**EPS Lora Loader State Controller**](#eps-lora-loader-state-controller-shipped-requires-rgthree-comfy) | Captures and applies those states directly on an [rgthree Power Lora Loader](https://github.com/rgthree/rgthree-comfy) or an EPS LoRA Picker — the loader stays the loader, this moves whole configurations in and out of it. | **rgthree-comfy**'s Power Lora Loader (third-party; only those targets need it) and/or the EPS LoRA Picker (no dependency). |
| [**EPS LoRA Iterator**](#eps-lora-iterator-shipped) | Auditions any `LORA_STACK` by strength: set min/max/increment and one queue runs your workflow once per step (per lora, or all together). | A `LORA_STACK` source (usually Apply LoRA Set) + a model. |
| [**EPS Image Switcher**](#eps-image-switcher-shipped) | Any number of image inputs, each independently on/off; the enabled ones fan out (N enabled → N runs). Disabled branches never execute. | Nothing — drag-and-drop with core nodes. |
| [**EPS Model / CLIP / VAE Switcher**](#eps-model--clip--vae-switcher-shipped) | The image Switcher's exact mechanism for models, CLIPs, and VAEs: any number of inputs, each on/off, enabled ones fan out (N enabled → N runs), disabled branches — including their checkpoint loads — never execute. | Nothing — drag-and-drop with core nodes. |
| [**EPS Distributor**](#eps-distributor-shipped) | The mirror of the Switcher: one image in, up to sixteen branches out, each independently on/off. New outputs appear as you wire them up. Toggle a branch off and only that branch is skipped — everything happens in one run. | Nothing — drag-and-drop with core nodes. |
| [**EPS Checkpoint Switcher**](#eps-checkpoint-switcher-shipped) | Tick several checkpoint files in a list; one queue runs the workflow once per ticked checkpoint, with each run's model, CLIP, and VAE kept together and a label for save paths. | Your checkpoint files — drag-and-drop with core nodes. |
| [**EPS Resolution**](#eps-resolution-shipped) | Image-first resize + size in one node: target size (with a drag pad), four resize modes, and the original image + both sets of dimensions passed through. Named size presets are shared across your machines — tick several and one Run resizes once per preset. Wire in extra images and they all come out at the same target size in one Run. | Nothing — drag-and-drop with core nodes. |
| [**EPS Image Grid**](#eps-image-grid-shipped) | Collects images across separate Runs into a buffer that survives restarts, shows them as a thumbnail grid, and fans the whole set out on demand. Add whole batches at once — a multiselect picker, a folder importer, or one big drag. | Nothing — drag-and-drop with core nodes. |
| [**EPS Run Multiplier**](#eps-run-multiplier-shipped) | Multiplies whatever you wire in: a sweep group (LoRA Iterator, or Checkpoint Switcher with its VAEs) × images × texts, in one node — or just images × texts with no sweep at all — with per-run save paths so big runs land in tidy folders. | Any of: EPS LoRA Iterator / EPS Checkpoint Switcher / EPS Model + VAE Switchers (`models`→`model`, `models_low`→`model_low`, `vaes`→`vae`) on the sweep side; an Image Grid or Image Switcher plus a multi-select Prompt Notebook on the pair side. |
| [**EPS Save Image**](#eps-save-image-shipped) | Save Image with provenance baked in: wire the Run Multiplier's `run_info` and every saved file carries a workflow already soloed to the run that made it — drop the image onto the canvas to recreate just that one. Without `run_info` it is exactly Save Image. | Nothing — a drop-in for the core Save Image node; `run_info` from EPS Run Multiplier unlocks the recreate-one-image drop. |
| [**EPS Frame Saver**](#eps-frame-saver-shipped) | Loads a video by path — or takes one from a wire — lets you scrub or play to a frame, and outputs that frame as an image. | A video file on the ComfyUI machine, or any `VIDEO` output in the workflow. |

> **Status: pre-release.** Contracts live in
> [docs/FORMAT.md](docs/FORMAT.md). Want to see the nodes working? Every
> workflow in [examples/](examples/) is annotated on the canvas and ready
> to load — nine example files between them cover fifteen of the sixteen
> nodes (the new LoRA Picker's example is still to come), some
> sharing a graph. Most just want a Run; the LoRA trio (Apply LoRA Set,
> LoRA Iterator, and the rgthree-dependent Lora Loader State Controller) only
> shows up in `eps-test-cross-sweep.json` and `eps-full-pipeline.json`,
> which need a real checkpoint and a saved LoRA state to actually generate.
> See [examples/README.md](examples/README.md) for which need setup and
> which just want a Run.

## A note on ComfyUI's "New node design (beta)"

ComfyUI is rolling out a new way of drawing nodes (Settings → search "new
node design", the `Comfy.VueNodes` option — the app may prompt you to try
it). Several EPS controls are **drawn directly on the node** — the per-row
toggles on all four Switchers and the Distributor, the Resolution size readouts, the
double-click renames — and the new design **does not run that drawing yet**,
so those controls disappear while it's on. Nothing errors; they're just gone.
The pack now shows a one-time warning when it notices, and the fix is to turn
the option off and reload. Full support for the new design is planned.

Worth knowing: the prompt appears **per browser**, so two machines pointed at
the same ComfyUI can end up in different modes — if the nodes look right on
one machine and broken on another, check this setting first on the machine
that looks broken.

## EPS Prompt Notebook (shipped)

`EPSNodes → EPS Prompt Notebook`: a two-pane editor inside the node — entry
list on the left (grouped by `# Category` headings, with `＋ New` /
`🗑 Delete`), a flexible text editor + `Save` on the right. Outputs:
`text` and `name` (the entry's heading — handy for filename prefixes and
captions).

- **Instant on tab switch (v0.74.0):** switching between open workflows used to leave the Notebook on an empty list until the file was re-read — over a minute on a slow NAS. It now repaints instantly from what it loaded earlier in this browser session (a faint "cached — refreshing…" shows while it re-checks), and the re-check tells the server what it already has, so an unchanged file costs a stat, not a full re-read.
- **Pinned from an image (v0.72.0):** drop an EPS Save Image file and the Notebook shows a `📌 Pinned — captured from image …` badge: the prompt text exactly as it was when that image was made, read-only, with a `≠` mark wherever your notebook has changed since — the run recreates the image byte-for-byte. One click on **Unpin** goes back to the live file.
- **Search everything:** the box at the top of the list filters as you
  type, matching every word you enter against prompt **titles and bodies**
  (so "portrait rim" finds a prompt titled "Studio A" whose text mentions
  rim light). Esc clears it. Filtering never changes your selection — a
  selected prompt that's filtered out of view stays selected — and
  drag-reorder pauses while a filter is active.
- **Select several prompts, get one run per prompt:** ctrl/cmd+click
  toggles entries into the selection, shift+click selects a range. Queue
  once and the workflow executes once per selected prompt, in selection
  order, with `text`/`name` paired. A single selection behaves exactly
  like a plain string.
- **Drag to reorder:** drag entries within the list (an insertion line
  shows the landing spot); drop onto a category header to move an entry
  into that category — the file is rewritten to match, byte-safe.
- **Paste anything — `#` headings included.** The file uses `#`/`##`
  headings as its own structure, so pasted content (LLM output, notes)
  that starts lines with `#` or `##` used to be refused. Now it just
  saves: those lines are automatically demoted two levels (`# Title` →
  `### Title`) so they stay inside your entry, the status line tells you
  how many were adjusted, and the editor shows exactly what was stored.
  `###`-and-deeper headings, and anything inside a code fence, were
  always fine and are untouched.
- **Renaming, two ways** (whichever you reach for first):
  - **Double-click the name** — in the list, on a prompt or a category
    header — and type over it. Enter saves, Esc cancels, clicking away
    saves. The rename happens right there in the row.
  - **Or use the name field** at the top of the editor pane. Change it and
    `Save` lights up; saving commits the new name together with whatever you
    changed in the text, in one write.

  Either way it renames the `##` heading in the file and nothing else — the
  prompt text is untouched, and a name that's already taken is refused with a
  message rather than quietly overwriting the other one.
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
- **Putting it on a NAS — just click it in Browse…:** the picker lists the
  shares and drives your machine already has mounted, **wherever they
  live**, named for humans (`personal_folder on my-nas.local`,
  `nas (network)`, `USB-STICK (exfat)`) with network shares first. On Linux
  it reads the system mount table, so a share mounted by your file manager,
  by `/etc/fstab`, or by an admin at some path nobody would guess all show
  up on their own — you never have to know or type a path. macOS lists
  `/Volumes`, Windows lists drive letters.
- **Opening the same workflow from a second machine** (e.g. ComfyUI runs on a
  Linux box, you drive it from a laptop): a notebook that lives *outside* the
  library folder — on a NAS, say — is blocked for the second machine until the
  host allows it. That's deliberate: ComfyUI's custom routes have no login, so
  a browser on another machine is only trusted with the library folder plus
  whatever the host explicitly shares. **The fix is one click, on the machine
  running ComfyUI:** open the notebook there and tick **"Share this folder with
  remote browsers"** under the file path. The laptop can then read and edit it
  normally. Only that machine can grant it, and it grants exactly that one
  folder — not its parent, not a similarly-named neighbour.
- **What won't work is a `smb://…` URL** typed into the file box. That's a
  file-manager *address*, not a filesystem path — no program (ComfyUI
  included) can open one, and the node says so plainly instead of failing
  silently. If a share isn't in the Browse… list yet, it isn't mounted on
  the machine ComfyUI runs on; mount it once (clicking it in your file
  manager is enough on most desktops) and it appears. This is the same
  constraint ComfyUI itself has for `models/`, `input/`, and `output/`.
- **Safe for shared files:** saving checks the file hasn't changed under
  you since you loaded it. If it has (the other machine got there first),
  nothing is written — you get *File changed on disk* with **Reload** /
  **Overwrite** to resolve it yourself. Writes are atomic, and the file's
  existing CRLF/LF style is preserved so cross-OS diffs stay clean.
- The workflow stores only the file path + selected entry name — never the
  text. The file is the truth; the node is a view.

## EPS Lora Loader State Controller (shipped; requires rgthree-comfy)

`EPSNodes → LoRA → EPS Lora Loader State Controller`: a small panel node that
drives a genuine, untouched
[Power Lora Loader (rgthree)](https://github.com/rgthree/rgthree-comfy) —
or an [EPS LoRA Picker](#eps-lora-picker-shipped) — elsewhere in your graph;
the loader stays the loader, this node just moves whole configurations
("states") in and out of it (only Power Lora Loader targets need rgthree):

- **Where your states live, and how to share them (v0.74.0):** the bottom of the state list says where the state files are. Unlike the Notebook's per-node `.md` file, states live in the pack's Library folder (`<library>/sets`), so they are shared between computers only when every machine's Library folder points at the same NAS folder. Never set it? The line reads "States: this machine only (default library folder)" with the fix right under it (Settings → EPSNodes → Library folder); once it is a shared folder it shows the path (hover for the full one) with an Open folder button (from the machine running ComfyUI).
- **Targets both loader families, anywhere in the workflow** (since
  v0.64.0): the `target` dropdown lists every Power Lora Loader (rgthree)
  AND every [EPS LoRA Picker](#eps-lora-picker-shipped) — including ones
  nested inside subgraphs, shown with their nested id (`#3:2`). Capture
  reads a picker's current selection, Apply writes a state into it (its
  folder scope is left alone), and `All loaders (N)` spans both kinds.
  Picker targets don't even need rgthree installed.
- **Two-pane layout** (like the EPS Prompt Notebook): a scrolling list of all
  your saved states on the left, the buttons stacked on the right.
- **Group your states like Notebook prompts** (since v0.65.0): type a
  `#`-prefixed name (say `# Portraits`) and press **New State** to create a
  group. Drag states between groups or drop one on a group's header; drag a
  header to reorder whole groups; tap a header to collapse it (▸ shows the
  count). Hover a header for a ✕ that removes just the group — its states
  move back to the ungrouped list, nothing is deleted. Groups and order are
  saved in your shared library folder, so every machine sees the same
  arrangement. **Double-click a group header to rename it** (since v0.68.1) — type the new name, Enter saves, Escape cancels; the group keeps its states and its open/collapsed state. Dropping a state back where it already was changes (and saves) nothing. Creating a group toasts a confirmation, clicking a state keeps the list where you scrolled it (v0.73.0), and group changes show up in your other open State Controllers right away, and saving, renaming or deleting a state no longer triggers a full node-definition reload — the `/object_info` round trip and its two toasts are gone, so Save responds immediately.
- **The Apply Set dropdown shows state names now** — a state named
  "State 1" reads as `State 1`, not its `state-1` file slug (what executes
  is unchanged).
- **One click selects, a second click applies.** A single click just
  *selects* a state (highlights it, loads its name) — it does **not** touch
  your loaders, so you can safely rename or delete a state without rewriting
  every wired loader. Clicking the already-selected state again applies it:
  the target's rows snap to it — count, order, toggles, strengths. (Reloading
  a saved workflow never re-applies; only that second click does.)
- **New State** captures the loader's current rows into a named state
  file — it's the only button that creates a new entry; **Save State**
  overwrites the selected state with the current rows — **and if you've
  typed a different name in the field, it renames that same state in
  place** (no duplicate entry; saved workflows keep working because the
  internal id never changes); **Delete State** removes it (two-click "Are
  you sure?" confirm — the armed button turns red; it's deliberately the
  last button in the stack).
- **Multi-loader targeting:** with two or more loaders in the graph —
  Power Lora Loaders and/or EPS LoRA Pickers (WAN high/low noise, for
  example) — the target dropdown offers `All loaders (N)`. With `All`
  selected, a state stores **each loader's OWN config** (a "composite"
  state): New/Save State captures every
  loader distinctly, and picking that state restores each loader to its own
  rows — so one state file holds your whole WAN high+low setup. To feed those
  distinct configs into the standalone `EPS Apply LoRA Set` loaders, give each
  Apply node a `loader_slot` (0 = first loader, 1 = second, …; revealed via
  right-click → Properties → `Show loader slot`). Single-loader states are
  unchanged and fully backward-compatible.
- **Push State** points your `EPS Apply LoRA Set` nodes at the selected
  state in one click — it sets each one's dropdown and re-reads it, so one
  controller keeps any number of Apply nodes in step (and it doubles as an
  explicit re-apply). Which Apply nodes it reaches follows the controller's
  `target` and each Apply node's `mirrors loader` tag: a specific loader as
  target pushes only to Apply nodes tagged to that loader (a toast tells you
  if none are); `All loaders` pushes to every Apply node; an Apply tagged
  `(any)` is included in every push. The toast reports the count.
- A debug `status` line is hidden by default — right-click the node →
  Properties → `Show status` to reveal it.
- **If a saved state ever disagrees with what you set on the loader**, the
  capture now reads the loader's own serialized row values (the same source
  your saved workflows use) and every New/Save State leaves a compact
  per-row trace in the browser console. Right-click → Properties →
  `Debug capture` adds a full table of each row's raw values — paste that
  with any bug report and the cause is pinpointed.
- It's a frontend-only virtual node: it never executes and can't block a
  queue. If rgthree isn't installed (or its internals ever drift), Power Lora
  Loader targets disable themselves with a message that points you at
  `EPS Apply LoRA Set`, which needs no dependencies — EPS LoRA Picker targets
  keep working without rgthree at all.
- Every `EPS Apply LoRA Set` dropdown refreshes automatically after any state
  change — no page reload.

## EPS LoRA Picker (shipped)

`EPSNodes → LoRA → EPS LoRA Picker`: browse your lora collection **by
folder**, right on the node, and build a selection to apply. It fixes
three everyday pains at once: the flat everything-in-one-dropdown lora
list, no way to scope a workflow to one folder and its subfolders, and no
favorites or recently-used anywhere in the ecosystem.

- **The panel, top to bottom:** the **Selected** rows — on/off toggle,
  name, strength, ✕ remove per row (the section grows with your picks so
  the full list is always visible — the node makes the room, nothing
  crops or scrolls); the **Send to** row; then the **browser** — a
  breadcrumb you drill down through (with a ✕ at its right edge to clear
  a pinned scope — and the folder you drilled into stays put until you
  navigate back or change the scope; since v0.67.2 a background refresh
  never bounces you to the root), the search box, `★ Favorites` and `🕘 Recent`
  pseudo-folders at the top, every folder with a lora count and a
  **Scope** pin, every lora with a ★ star and `＋ Add`. A selected or
  starred lora that isn't installed on this machine shows a dimmed ⚠ row
  — visible, never silently dropped. Your library's total lora count
  lives in right-click → Properties (`library_loras`) and the breadcrumb
  root's tooltip.
- **Your height sticks (v0.73.0).** A fresh picker grows per selected row
  as before; the moment you drag the node's corner yourself it stops
  growing — the Selected list scrolls within about half the panel and the
  browser keeps the rest, so a State Controller applying a big state never
  stretches the node. Right-click → Properties → `Auto-grow with selection`
  turns growth back on.
- **Click a lora once to select it, again to load it** — the second click
  does exactly what ＋ Add does, and the just-added lora stays
  highlighted. Added loras always arrive enabled.
- **Works with the State Controller and saved states** (since v0.64.0):
  the [EPS Lora Loader State Controller](#eps-lora-loader-state-controller-shipped-requires-rgthree-comfy)
  lists pickers in its target dropdown right next to Power Lora Loaders —
  including pickers nested inside subgraphs — so you can capture a
  picker's selection as a named state, apply states back into it, and
  Push to Apply Set nodes tagged to it. Apply Set's `mirrors loader` tag
  can name a picker too.
- **Scope is per-workflow.** Pin a folder and the browser shows only it
  and its subfolders — the pin saves **into the workflow file**, so the
  character workflow opens scoped to `characters/`, the style workflow to
  `styles/`. It's pure view state: re-scoping never re-runs the graph.
- **Favorites and recents follow you across machines:** one file,
  `lora_picker.json`, in the same shared library folder as your notebooks
  and sets — point the library at a NAS and every machine sees the same
  stars. `＋ Add` is what stamps a lora recent.
- **Outputs — Apply LoRA Set's exact five, so it drops in anywhere that
  node does:** `lora_stack` wires straight into the [EPS LoRA
  Iterator](#eps-lora-iterator-shipped) or any stack-consuming node;
  `model`/`clip` pass through patched — the same loader path [EPS Apply
  LoRA Set](#eps-apply-lora-set-shipped) uses, and chaining through an
  Apply node combines your picked loras with a saved set;
  `trigger_words` is read from the sidecar `.txt` next to each lora file
  (the ecosystem's activation-text convention), ready to concatenate into
  a prompt; `loras_text` names what was applied, for a Save Image
  `filename_prefix`. An empty or all-off selection just passes through —
  never an error. A selection saved on Windows resolves on macOS/Linux
  (same separator-insensitive matching as saved sets).
- **Send to loader — entirely optional:** the picker **applies its loras
  itself** — wire `model`/`clip` through it (or use `lora_stack`) and
  you're done; "no loader in graph" on the Send row is not a problem to
  fix. The row exists only to *copy* your picks into another pack's
  loader node: it targets any
  [rgthree Power Lora Loader](https://github.com/rgthree/rgthree-comfy)
  **or [DaSiWa Advanced LoRA
  Loader](https://github.com/darksidewalker/ComfyUI-DaSiWa-Nodes)** in
  the graph and writes your picked loras straight in — on/off and
  strengths included. Neither pack is required; with neither installed
  the row just says so. It never guesses a target: delete the loader
  you'd picked and Send refuses until you pick again. DaSiWa specifics,
  always said out loud, never silent: it stores ONE strength per lora
  (a row whose clip strength differs sends the model strength, and the
  toast names it), strengths clamp to its ±5 range (also named), and
  re-sending preserves the video/audio (`vs`/`as`) multipliers you've
  tuned in DaSiWa's own UI for loras already in the loader.
- **Already-works-by-wire:** stackers that take a `LORA_STACK` input —
  Efficiency's LoRA Stacker, Easy-Use's loraStack, Comfyroll's CR LoRA
  Stack — need no Send support at all: wire this node's `lora_stack`
  output straight in.
- **Search, thumbnails, trigger words, ordering:** the search field under
  the breadcrumb filters as you type (debounced, and a very long result
  list caps at 200 rows with a "keep typing" hint — v0.68.1) — it searches
  **the folder you're in** (and its subfolders; drill into `characters/`
  and you search just
  that, back out to the library root and you search everything; every
  word must match somewhere in the path, Escape clears); loras with a
  sidecar image (`mylora.png` / `mylora.preview.png` next to the file)
  show a small thumbnail; the 📋 button copies a lora's sidecar trigger
  words to the clipboard; the Recent view has a two-click **Clear
  recents**; and the Favorites view supports drag-reordering by the ≡
  handle — the order is shared across machines like the stars themselves.

## EPS Apply LoRA Set (shipped)

`EPSNodes → LoRA → EPS Apply LoRA Set`: pick a saved state from the dropdown and every
enabled lora in it is applied **in order** to the `model`/`clip` you wire
through — it *is* a loader, no Power Lora Loader involved. (For WAN-style
dual-model workflows: two Apply nodes, one in the HIGH branch, one in the
LOW branch, each with its own state.) Outputs:

- **Pinned from an image (v0.72.0):** drop an EPS Save Image file and the node shows a `📌 Pinned state: … — captured from image …` badge with the LoRA rows as they were, says whether they match or differ from the current state (or that the state no longer exists), and ignores the dropdown until you click **Unpin**.
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
A frontend-only **`mirrors loader`** dropdown on the node (default `(any)`)
tags it to one Power Lora Loader or EPS LoRA Picker in the graph — a grouping
tag for the State Controller's **Push State**: a controller targeting that
loader pushes only to Apply nodes tagged to it, while `(any)` follows every
push. The tag never changes what executes — states still come from the file.
States are JSON files in `<library folder>/sets/` — captured from a Power
Lora Loader by the State Controller, created via the API, or hand-edited. Loras
referenced by a set resolve **separator-insensitively** with a unique-
basename fallback, so a set written on Windows applies on macOS and vice
versa; anything that can't resolve is skipped with a logged warning rather
than failing the run. States created outside the graph (another machine, a script) show up the next time you open the dropdown — it refreshes itself in the background — or press `R` (refresh node definitions).

The weight math is covered by a permanent numeric regression test
(`tests/test_nodes_sets_weight_math.py`): it drives this node against real
ComfyUI patching machinery and asserts the patched model AND clip weights
equal the first-principles `base + strength × (alpha/rank) × (up·down)`
expectation — including stacked rows, a dual clip strength, a disabled row,
and `strength_scale`. It needs `torch` plus an importable ComfyUI (set
`EPS_COMFYUI_ROOT=/path/to/ComfyUI` if `comfy` isn't already on the path)
and skips cleanly where those are absent.

## EPS LoRA Iterator (shipped)

Since v0.68.1 each lora file is loaded once per run of the node (not once per strength step), so long sweeps over big loras start faster.

*Renamed from "EPS LoRA Sweep" in v0.48.4 (display name only — saved
workflows keep working unchanged, and nodes already placed in an old
workflow keep showing the name they were saved with).*

`EPSNodes → LoRA → EPS LoRA Iterator`: audition a lora (or several) by strength —
wire in a `LORA_STACK`, set `min` / `max` / `increment`, queue once, and
the rest of your workflow runs at every step.

- **Wire `EPS Apply LoRA Set`'s `lora_stack` output straight in**, alongside
  the `model` (and `clip`) you'd normally pass through the loader — EPS LoRA
  Iterator does its own applying internally, so no separate "apply the stack"
  node sits between them. Any other `LORA_STACK` producer works too.
- **`clip` is optional.** Models without a text encoder (or any workflow
  where you only want to patch the model) can leave `clip` unwired — the
  sweep then patches the **model only** (the lora's clip-side weights, if
  any, are skipped, exactly like ComfyUI's own model-only lora loader) and
  the `clip` output passes through empty. `model` is still required — a
  strength tester needs a model to patch.
- **Two modes:** `Each lora independently` (the default) sweeps one lora at
  a time across the range while every other active lora holds its own
  saved strength; `All together` moves every active lora to the same value
  at once. **Watch the run count** — independent mode is `n_loras ×
  n_steps`, so 3 active loras swept 0.0→1.0 at 0.1 is **33 runs**; all-
  together mode is just `n_steps` (11, same range) no matter how many
  loras are active. **Both endpoints are inclusive** — 0.0 to 1.0 at a 0.1
  increment is 11 steps, not 10.
- **Outputs:** `model`, `clip`, and `label`, one triple per run, all fanned
  out together — plug them straight into a sampler chain and it runs once
  per step automatically, no extra wiring needed. `label` names exactly the
  lora and strength that run swept — `my_great_lora_0.5` (in "all together"
  mode, `all_0.5`) — with a consistent decimal so the files line up and
  sort. Wire it into a `SaveImage` `filename_prefix` and every image names
  itself by the lora value under test.
- **Same seed every run, on purpose:** whatever seed you wire downstream
  repeats identically across the whole sweep — that's what turns it into a
  clean side-by-side strength comparison instead of 11 unrelated random
  images. Want per-step variation too? Wire an explicit per-run seed list
  instead.
- Changing **any** setting (even just the mode) re-renders the **whole**
  sweep on the next queue — there's no partial re-render of only the new
  steps.
- `min`/`max` go from −10 to 10 and are **not clamped** to the usual 0–1
  range, for deliberately testing over- or under-strength.

## EPS Image Switcher (shipped)

*Renamed from "EPS Switcher" in v0.49.2 — every other switcher says what it
switches (Model, CLIP, VAE, Checkpoint), so the original now does too.
Display name only: saved workflows keep working, and nodes already placed
keep showing the name they were saved with.*

`EPSNodes → Switchers → EPS Image Switcher`: wire in **any number of images**, flip each one
on or off, and the enabled ones flow out as a list — so the rest of the
workflow **runs once per enabled image**. Four images in with one turned
off means three runs.

- **Grows as you wire:** connect the last image socket and a fresh empty one
  appears; a connected socket never renumbers, so your wires stay put.
- **A toggle on every row**, plus a **Toggle All** header (tri-state: all
  on / all off / a dash for mixed, with a live `enabled/total` count) — the
  same one-click-everything control as rgthree's Power Lora Loader.
- **Fan-out, not pick-one:** unlike a normal switch that forwards a single
  chosen input, EPS Image Switcher forwards *all* the enabled ones and lets
  ComfyUI iterate. (A scalar wired downstream — e.g. a seed — repeats
  identically across the runs; use a per-image list for per-image
  variation.)
- **A list-producing input (like EPS Image Grid) counts every image it
  holds:** wiring a grid into a slot merges its whole buffer into the run
  count element-by-element, same as if each image were wired in
  separately — three grid images + one ordinary image enabled means four
  runs, not two.
- **Disabled branches don't run at all:** toggle a slot off and whatever
  feeds it — even a slow loader or another grid — never executes for that
  queue, not just gets dropped from the output afterward. (One quirk to
  know: an *enabled* slot fed by an *empty* grid with nothing wired into
  the grid skips the whole switcher branch for that queue — toggle the
  empty grid off, or put something in it. Since v0.52.0 a Collect-mode grid
  in that state raises a warning toast naming those fixes, so the skip is
  no longer silent.)
- **All off is allowed:** toggle everything off (or wire nothing) and the
  queue still succeeds — the image branch simply doesn't run that time. No
  error, no downstream crash.
- **Double-click a row to rename it:** the label is display-only (wires,
  toggles, and the backend still see `image_N`), persists with the
  workflow, and an empty name resets it.
- **The checkboxes line up in one straight column — across every switcher
  in the workflow**, not just within a node (since v0.62.0). Rename rows to
  whatever you like and the boxes still form a single vertical line at the
  furthest-right one, so a stack of Image/Model/CLIP/VAE Switchers reads as
  a tidy column instead of a ragged stagger. If one node's names are too
  long for its width, widen that node — its box has to stay clear of the
  socket to remain clickable.
- Toggle states save with the workflow and survive reload.

## EPS Model / CLIP / VAE Switcher (shipped)

`EPSNodes → Switchers → EPS Model Switcher / EPS CLIP Switcher / EPS VAE Switcher`: the
[EPS Image Switcher](#eps-image-switcher-shipped), one per data type. Wire in any number
of models (or CLIPs, or VAEs), tick them on and off, and the enabled ones
fan out in slot order — three enabled models means the rest of the workflow
runs three times, once per model. Built for "try the same prompt across
several models/VAEs in one queue".

- **WAN-style high/low pairs** (Model Switcher only, since v0.66.0): flip
  `High/low pairs` on in right-click → Properties and every row gains a
  `model_N_low` socket under its high model, plus a `models_low` output
  that stays perfectly index-aligned with `models`. One checkbox still
  governs the whole pair — toggle a row off and neither model loads. Rows
  where the two sides' counts disagree fail the queue loudly instead of
  silently mispairing; a row with no low wired simply skips whatever
  consumes `models_low`.
- **Everything the image Switcher does, identically:** growing sockets (wire
  the last one and a new one appears), per-row toggles, Toggle All,
  double-click rename, all-off is valid (queue succeeds, downstream skips).
- **A toggled-off branch never runs at all.** Wire three Load Checkpoint
  nodes into a Model Switcher, tick one off, and that checkpoint is never
  even loaded — not loaded-then-discarded. Verified against a real loader.
- **One switcher = one axis.** ComfyUI pairs two fanned lists index-by-index
  (it does NOT multiply them): a 3-model switcher plus a 2-VAE switcher into
  the same sampler gives **3 runs** — (m1,v1), (m2,v2), (m3,v2) — not 6. To
  actually multiply axes, run them through [EPS Run
  Multiplier](#eps-run-multiplier-shipped), or use the [EPS Checkpoint
  Switcher](#eps-checkpoint-switcher-shipped) which keeps model+CLIP+VAE
  aligned as one axis by construction.
- Loading several checkpoints in one queue is heavy on disk and RAM/VRAM;
  ComfyUI offloads between runs, but start with two or three, not ten.

## EPS Distributor (shipped)

`EPSNodes → EPS Distributor`: the **mirror of EPS Image Switcher**. Where the
Switcher gathers many toggleable inputs into one flow, the Distributor takes
**one image and fans it out to up to sixteen outputs, each independently on or
off**. Wire the same picture into an upscale branch, a restyle branch and a
straight-to-save branch, then turn any of them off from this one node — no
rewiring, no dragging bypass boxes around groups.

- **A toggle on every output,** plus a tri-state **Toggle All** header (all
  on / all off / a dash for mixed, with a live `enabled/total` count — the
  same control the Switchers have). Click a toggle off and the branch wired
  to that socket is skipped for that queue; the branches on the sockets still
  turned on see the real image and run normally.
- **One run, not many.** This is the important difference from the Switcher.
  The Switcher's fan-out makes the rest of your workflow run *N times*; the
  Distributor's branches are **parallel paths inside a single run**. Use the
  Switcher to iterate over images, the Distributor to route one image several
  ways at once.
- **Skipped, not "sent an empty image."** A disabled branch doesn't execute
  at all — the sampler, upscaler, or save node on it never runs, so you don't
  pay for it and it can't write a file.
- **All off is allowed, and it skips the work too:** turn every output off
  and the queue still succeeds with no error — and nothing *upstream* of the
  node runs either. Put a Distributor at the end of a graph, switch
  everything off, and the sampler feeding it never fires. (With even one
  output still on, the upstream runs normally.)
- **Name your outputs:** double-click an output — the socket or anywhere in
  its row — and give it a real name like `upscale branch`. It's display-only,
  so wires and toggles are unaffected, and clearing the field resets it to
  `out_N`.
- **The outputs grow as you use them,** the same way EPS Image Switcher's inputs do:
  the node starts with three, and wiring the last one reveals another, so
  there is always one spare socket waiting. Sixteen is the ceiling (a
  ComfyUI limit, not a preference — see below).
- **Want fewer?** Right-click the node → Properties → `Outputs` and set the
  number by hand. Growth never takes a socket away on its own, so a name you
  typed or a wire you ran is never removed behind your back; and a socket
  that still has a wire on it refuses to hide, with the number snapping back
  and a message telling you which one. Only *trailing* sockets are ever added
  or removed, so the wires you already have never shift to a different
  output.
- **Why sixteen and not unlimited?** The Switcher's inputs can grow forever
  because ComfyUI matches inputs by *name*. Outputs are matched by *position*
  in a list the node declares once when ComfyUI starts, so they have to exist
  up front. Sixteen is simply where that list is set; if you need more, say
  so and the number can be raised safely — raising it never disturbs a saved
  workflow.
- An output with nothing wired to it doesn't care either way — leave it on
  or off, it changes nothing.
- Toggle states and the visible-output count save with the workflow and
  survive reload.

## EPS Checkpoint Switcher (shipped)

`EPSNodes → Switchers → EPS Checkpoint Switcher`: a checkbox list of every checkpoint
ComfyUI can see. Tick the ones you want, wire `model`/`clip`/`vae` where a
Load Checkpoint's outputs would go, and one queue runs the rest of the
workflow **once per ticked checkpoint** — each run using that checkpoint's
own model, CLIP, and VAE together, plus a `label` (the filename stem, no
extension) you can
wire into save paths so results land in folders named by model.

- **Ticks travel between Windows and Linux/macOS (v0.71.0).** A selection saved on the other OS still shows the right rows ticked and loads the right files; with the heal setting on, the saved names are rewritten to this machine's spelling on load so a re-save is clean.
- **Why this beats three separate switchers for model testing:** a
  checkpoint's model/CLIP/VAE can never drift out of alignment (they travel
  as a group), there's nothing to wire up per checkpoint, and the label
  gives every run a name.
- **The list is searchable and grouped by folder;** a ticked file that has
  since been deleted shows with a ⚠ so you can untick it — it's skipped at
  run time (with a log line) rather than failing the whole queue.
- **Ticking nothing is valid:** the queue succeeds and downstream simply
  doesn't run. A typo'd file name (hand-edited workflow/API) fails the
  queue up front with a message naming it.
- **Go easy on the count.** Every ticked checkpoint really loads: three or
  four is a sweep; ten is a disk-churning afternoon. ComfyUI unloads between
  runs, but start small.
- Selections save with the workflow and survive reload.

## EPS Resolution (shipped)

`EPSNodes → EPS Resolution`: one image-first node for the everyday
"resize this and tell me the sizes" job — set a target width/height, pick a
mode, and get back the resized image **and** the original, plus both sets of
dimensions. It replaces a resize node + a reroute + a get-image-size node.

- **See what you're resizing FROM:** wire an image in and a second line
  appears under the readout — `in 1920 x 1080 16:9 2.07 MP` — the incoming
  image's own size, ratio and megapixels, in the same format as your target
  above it. It shows up as soon as the image loads (no Run needed), and the
  panel returns to one line if you unplug the input.
- **The size grid:** a full-width square drag pad right on the node — drag
  anywhere and `width`/`height` follow, snapping to `multiple_of` (or 64 when
  it's off). The pad is locked to the node's left and right edges (no wasted
  space beside it), and it's a true square, so a 1:1 target sits on the
  diagonal. **Make it bigger by dragging the node wider** — the square (and the
  node's height) grow to match; narrow the node and it shrinks back. Hold
  **Shift** for a 1:1 square, **Ctrl/Cmd** to keep the aspect ratio the box had
  when you started dragging. The crosshair is drawn only up to the dot — the
  lines don't run past it, so the marked-out rectangle reads as the image
  you're sizing. The typed fields and the grid stay in sync (edit either); a
  one-line readout under the pad shows `W x H` with the ratio (3:2) right next
  to it and the megapixels right-aligned, and right-click Properties offers
  `Grid max` (range) and `Show grid` (hide the pad
  entirely if you only want the numbers).
- **Four resize modes:** `stretch`, `keep aspect (fit)`, `crop to fill`,
  and `pad` (black), with a choice of interpolation. `multiple_of` snaps the
  result to a multiple (e.g. 64) for latent-friendly sizes.
- **Set one axis to `0`** to derive it from the other and the image's aspect.
- **`copy from image`** (since v0.63.0): a button above the size fields that
  fills `width`/`height` with the wired image's own pixel size in one click
  — no reading the numbers off the panel and retyping them. It copies the
  EXACT size (if you've set `multiple_of`, the Run rounds it just as it
  would a size you typed), and it tells you why nothing happened if there's
  no image wired, or if the one you wired hasn't loaded a preview yet.
- **`height` sits above `width`** (since v0.61.0) so the fields read in the
  same H-before-W order most model cards use. Old workflows load with their
  saved values intact — the values follow the field *names*, not the rows.
- **Resize several images to one size:** wire your first image and an
  `image_2` socket appears; wire that and `image_3` appears, up to
  `image_8`. Every wired image is resized to the same target in one Run,
  each coming out on its own `resized_2` … `resized_8` output (revealed as
  you wire the inputs — sockets and outputs both tidy themselves back up
  when you unplug). In this mode `resized_image` still carries the first
  image and `width`/`height` report the shared target; the passthrough and
  original-size outputs are single-image concepts, so anything wired to
  them is skipped cleanly for that Run. With `width` or
  `height` at `0`, the missing axis derives from the **first** wired
  image's aspect and that one target applies to every image. Multi-image
  composes with multi-preset ticks: 3 images x 2 presets = every image at
  every size. With `keep aspect (fit)` every image fits inside the one shared box (a 0 width/height derives that box from the first image), so a wide first image never shrinks the others (v0.68.1).
- **Size presets, shared across machines:** the `preset` dropdown (with
  **Save** and **Delete** right under it, above the pad) saves all five
  fields — width, height, mode, interpolation, `multiple_of` — as a named
  preset. Pick one to apply it; **Shift-click the dropdown to tick
  several**, and one Run then resizes once per preset (2 presets → the same
  image at 2 sizes, like the Prompt Notebook's multi-select). **Edit any
  of the five fields by hand and the preset un-selects itself** (v0.67.1)
  — the dropdown drops back to `(none)` and the numbers you typed are what
  runs; a preset is only ever "applied" while it's showing in the
  dropdown. Presets live
  in one small JSON file in the same shared library folder as the Prompt
  Notebook (local or NAS), so your other machines — and a browser on
  another machine — all see the same list, and edits made anywhere are
  picked up on the next Run. Save with exactly one preset chosen pre-fills
  its name (that's "update"); Delete only lights up with exactly one
  chosen. A preset renamed or deleted elsewhere shows as `name (missing)`
  and **fails the Run with a clear error** naming it — never a silent
  substitute. Don't want presets on a node? Right-click → Properties →
  `Presets` off hides the whole cluster.
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

Since v0.69.0 the on-node thumbnails are real downscaled previews served by the pack (disk-cached per frame, keyed by each frame's own timestamp), so a big buffer no longer re-downloads every picture after each run and the canvas draws small images, not full-resolution ones.

`EPSNodes → EPS Image Grid`: a node that **collects images across separate
Runs** into a buffer and then fans them out — wire a loader in, run it a few
times to gather images, then send the whole set through a workflow at once.

- **Flow-through, always:** whatever's wired into the node always continues
  downstream. **Collect** mode ALSO records it into the buffer (only that
  Run's own image(s) continue downstream — Collect doesn't replay the whole
  buffer). Switch to **Emit** and Run once to send the WHOLE buffer
  downstream instead, with whatever's currently wired appended as the final
  image(s) (10 buffered + 1 wired → 11 runs).
- **Navigable grid:** the collected images show as a clickable thumbnail grid
  right on the node (ComfyUI's own image viewer — click to enlarge, arrow
  through them). **Adding an image keeps you on the full grid** — a new
  arrival (a Collect run, a paste, a drop) shows up as one more thumbnail
  *in* the grid instead of taking over the view, and if you've enlarged one
  image to inspect it, re-running the queue won't yank you back out.
- **Fan-out outputs (Emit mode):** `image`, `width`, `height` — wire them
  downstream in **Emit** mode and the workflow runs once per buffered image,
  plus once more for anything currently wired (10 images → 10 runs, e.g. to
  put a logo on 10 models' shirts). Nothing to send (Collect with nothing
  wired, or Emit with an empty buffer and nothing wired) is skipped cleanly,
  never a crash — and since v0.52.0, when something downstream actually
  consumes a Collect grid in that state you also get a warning toast naming
  the two real fixes (wire an image in, or switch to Emit to send the
  buffer). Runs no longer re-list the whole buffer in the generated
  output panel — only newly collected images show up there.
- **Survives restarts:** the buffer lives on disk (under ComfyUI's output
  folder, keyed to that node), so it's still there after you close and reopen
  ComfyUI. A **Clear** button wipes it; deleting the node abandons it. No cap.
- Each node keeps its own independent buffer, even after copy/paste.
- **Copy/paste:** right-click a collected image → Copy image (to the OS
  clipboard, for Photoshop/etc.) or Copy (Clipspace) (into the mask editor or
  another node). Five ways to ADD images: the **Add images… button** (a
  real file picker — select as many as you want at once); the **Add folder…
  button** (pick a folder and every image in it, subfolders included, is
  added); with the node
  selected, **Ctrl+V**; **right-click → Paste (Clipspace)**; or **drag
  files (or an assets-panel image) straight onto the node** — and yes,
  dragging thirty files as one drop adds all thirty. Every path appends to
  the buffer without losing what's already there.
- **Delete one image without starting over:** right-click any thumbnail →
  **Delete this image**. A stray duplicate no longer means wiping the whole
  buffer — remove just that tile and keep going. (Only the grid's own copy
  is deleted; your original file is untouched.)
- **Built for real batches (20–100 images):** files ingest in
  filename-numeric order (`img2` before `img10`, whatever order your OS
  hands them over); the button shows progress and becomes **Cancel** while
  a batch runs; and when it finishes you get one summary (added / skipped /
  failed) instead of silence. Thumbnails load as compressed previews so a
  big grid stays snappy — Copy image still copies the full-resolution
  original.
  - *Viewing ComfyUI on another machine over plain `http://` (e.g. a Mac
    pointed at a PC's LAN address)?* Browsers block writing an image to the OS
    clipboard outside a "secure context", so **Copy image** can't reach the OS
    clipboard there — it falls back to copying the image's link and opening it
    in a new tab (right-click → Copy Image there for a true copy), and tells
    you so. **Copy (Clipspace)** works everywhere. For real OS image-copy, use
    the ComfyUI desktop app or open ComfyUI via `localhost`/`https`.

## EPS Run Multiplier (shipped)

*Renamed from "EPS Cross Sweep" in v0.48.4 (display name only — saved
workflows keep working unchanged, and nodes already placed in an old
workflow keep showing the name they were saved with).*

**One node, up to four axes.** The sweep side
(`model`/`clip`/`label`/`vae`) is **optional**: wire none of it and the
node is a pure image × text multiplier (it fully replaced the old EPS
Cross Product node, removed in v0.50.0). A `pair_mode` switch controls
the pair side: **`multiply`** (the default — the dropdown itself, like
`sweep_mode`, stays hidden until you flip `Show mode options` in
right-click → Properties) crosses every image with every text right on
this node — wire an Image Grid and a multi-select Prompt Notebook straight
in, names riding along per text; **`paired`** runs image/text as
index-aligned pairs you wired elsewhere. With a sweep wired too that's a
genuine three-axis batch — checkpoints × images × prompts from one Run —
and with `sweep_mode: multiply` (also the default) `vae` becomes a fourth
axis crossed against the model axis; `label` is optional as well (no label
wired → folders fall back to `step_01, step_02, …`). **Sweep inputs must
agree on length**:
a length-1 input (one constant VAE for a whole sweep) repeats for every
step, but two fanned sweep lists of different lengths fail the Run with
an error naming them — that's always a miswire, usually a leftover wire
into `label` from something unrelated.

**Know the run count BEFORE an overnight run (v0.59.0).** The node shows
a live **`Runs: N — S sweep steps × P pairs`** line computed from your
graph before you ever queue: switcher ticks, Checkpoint Switcher
selections, Notebook multi-select, Iterator step math, an Image Grid run
through an EPS Resolution (or through a switcher and then a Resolution —
v0.67.1), even chained multipliers — all counted the way ComfyUI will
actually execute them (fanned lists through loaders included). It is honest about limits:
ordinary third-party nodes in the chain (a model enhancer, a CLIP
tweak — anything ComfyUI maps over a list) count through too, since
v0.68.0; only a node that emits a list of its own choosing or a saved
set's file turns the line into **`≥ N`** and names which input is
unknowable; a
wiring that will *fail* the queue (mismatched sweep lengths, an empty
Notebook) paints red with the reason, and a wiring that will silently run
*zero* times says "nothing to run" and names the empty input. Then, the
moment a queue starts, the node announces the **definitive** count as a
toast ("EPS Run Multiplier: 8 runs") — the multiplier executes in the
first seconds, long before samplers, so a wrong number is cancellable
while it's still free.

**Every file names its exact run — and can be re-run alone (v0.67.0).**
Each save path now ends with a short **run token** built from the run's
indices: `m2` = 2nd sweep step, `_v1` = 1st VAE (only when VAEs are
their own axis in `sweep_mode: multiply`), then the pair — `p1`
(paired), `i2_t3` (image 2 × text 3), or `t1` (text-only). A filename
like `lora_0.5/Portrait_m2_i1_t3_00001_.png` identifies its run even
after the file leaves its folders. To **re-run just that one image**,
paste the token (`m2_i1_t3`) into the node's new **`solo_run`** box and
queue: the node emits exactly that single run out of the whole set —
the readout switches to `Solo m2_i1_t3 — 1 of 24 runs` so you can't
forget it's on, and a mistyped token fails the queue immediately with a
real example token to copy (never a silent zero-run "success"). Clear
the box to run the full set again. This is milestone M1 of the
provenance roadmap (`docs/ROADMAP-run-provenance.md`); later milestones
bake per-image workflows so a dropped file loads pre-soloed.

**Both modes default to `multiply` (v0.66.1)** — images × texts crossed,
and the vae axis crossed against the model axis — because that's almost
always what you want; the two mode dropdowns are hidden until you flip
`Show mode options` in right-click → Properties. Set them to
`paired`/`aligned` for the index-aligned cases below. Workflows that
saved an explicit mode keep it; very old saves from before the modes
existed now load as multiply.

**`sweep_mode` — every model × every VAE (v0.57.0).** In `aligned` the
sweep side is one *aligned* set: step 3 runs model 3 with VAE 3 (what an
EPS LoRA Iterator or Checkpoint Switcher emits, where they belong
together). **`sweep_mode: multiply`** — the default since v0.66.1 (reveal
the dropdown via `Show mode options` to pick `aligned`) — treats `vae` as
its own axis instead: a 4-model Model Switcher × a 2-VAE VAE Switcher =
**8 runs**,
model-major (each model loads once and runs both VAEs before the next
model), with folders like `modelname_vae01/`, `modelname_vae02/`.
`model`/`clip`/`label` still travel together as one axis. Guard rail:
wiring more than one VAE from the *same* node as `model` (one Checkpoint
Switcher feeding both) while in multiply mode fails the Run with an
explanation —
those lists are already matched per checkpoint, and crossing them would
pair one checkpoint's model with another's VAE.

`EPSNodes → EPS Run Multiplier`: run a **whole lora sweep across a whole set
of image/prompt pairs** — 11 strengths × 8 pairs = 88 runs, grouped by
strength, each landing in its own folder.

**Model iteration:** wire the [EPS Checkpoint
Switcher](#eps-checkpoint-switcher-shipped)'s `model`/`clip`/`vae`/`label`
into this node's sweep side, and **a multi-select Prompt Notebook's
`text`** (leave `image` unwired for txt2img, or add images — the default
`pair_mode: multiply` crosses them)
on the other — every ticked checkpoint runs against every prompt, each run
with its checkpoint's own VAE, saving into a folder named by checkpoint.
The `vae` output only means something when the sweep side supplies VAEs:
leaving the `vae` input unwired is fine as long as nothing downstream
consumes the `vae` output, but wiring that output somewhere without a VAE
source feeding the input **fails the Run with an error naming the miswire**
(since v0.51.0 — a dead wire that would silently skip part of the graph is
always a mistake; the same rule covers `model`/`clip`/`image`/`label`).


- **WAN high/low sweeps** (since v0.66.0): wire the Model Switcher's
  `models` → `model` and `models_low` → `model_low`, and every run carries
  its high+low pair welded together — `model_low` is never a new axis, so
  run counts (and the readout) don't change. In `sweep_mode: multiply`
  each VAE runs the same high+low pair. Mismatched lengths fail the queue
  naming `model_low`, never a silent wrong pairing.
- **Why you need it:** ComfyUI's own list pairing ZIPS index-by-index —
  two fanned lists into one sampler give you max(11, 8) = 11 runs, not
  11 × 8. This node is the pack's one true multiplier: sweep group ×
  images × texts, each group internally matched.
- **How to wire it:** EPS LoRA Iterator `model`/`clip`/`label` → the same
  inputs here; an image source → `image`, a Notebook's `text`/`name` →
  likewise (the default `pair_mode: multiply` crosses them). Use this node's
  outputs downstream. **Strength-grouped:** all pairs at the first
  strength, then all pairs at the next.
- **Folders for free:** wire `save_prefix` into SaveImage's
  `filename_prefix` and every run lands at
  `output/<base_folder>/<sweep label>/<pair name>_<run token>_00001_.png`
  (e.g. `Portrait_m2_i1_t3_00001_.png`) — one folder per strength, files
  named by prompt entry plus the run token. `base_folder` is a text field
  on the node (nesting with `/` works); the pair name comes from the
  notebook's `name` output, with a clean `pair_01` fallback.
- **Mind the multiplication:** steps × pairs × (loras, in the sweep's
  independent mode). 2 loras × 11 steps × 8 pairs = 176 generations in one
  queue — deliberate-use / overnight territory, exactly as intended. A
  fixed seed repeats across every run, so strength and pair are the only
  variables moving.


## EPS Save Image (shipped)

`EPSNodes → EPS Save Image`: a drop-in for the core **Save Image** node —
same `images` + `filename_prefix`, same files, same counter — that bakes
**provenance** into every PNG. Wire **EPS Run Multiplier's `run_info`
output** into its `run_info` input (and `save_prefix` into
`filename_prefix`, as before) and each saved file carries its own copy of
the workflow with `solo_run` **already set to the run that made it**. Drop
the image onto the canvas: the whole workflow loads, the Run Multiplier's
readout reads `Solo m2_i1_t3 — 1 of 24 runs`, and queueing recreates
exactly that one image (the multiplier is found even inside a subgraph).
Leave `run_info` unwired and it behaves exactly like Save Image.


**Recreate one image — exactly (v0.72.0).** Beyond the solo, EPS Save Image
now pins the **Prompt Notebook text** and the **Apply LoRA Set rows** at
save time into each file's workflow. Drop the image, queue — you get that
one image back byte-for-byte, even if you have since rewritten the prompt
or re-tuned the set. Pinned nodes show a **📌 Pinned — captured from
image** badge with the OLD values readable right on the node, say whether
they still match the current library or differ, and one click **Unpin**
puts you back on the live library.
- **Existing images** (saved through the core Save Image since v0.67.0, so
  their names carry the run token): dropping one still helps — the pack
  reads the token off the file name and, when the loaded workflow has one
  Run Multiplier with an empty `solo_run`, fills it in with a toast (with
  several multipliers it tells you the token to paste instead of guessing).
  Images from before the token update have no per-run identity and load
  the full workflow as always.
- **Caveat:** the file must keep its PNG metadata (re-encoding, stripping
  or converting to JPEG loses it), and the filename fallback needs the name
  intact. The image also carries the Prompt Notebook text and Apply LoRA Set rows as they were (M3 pinning, v0.72.0) — the nodes show them pinned with a drift badge and an Unpin button.

## EPS Frame Saver (shipped)

`EPSNodes → EPS Frame Saver`: load a video and pull a single frame out of it
as an image.

- **Point at a video by path** — click **Browse…** to pick a file (nothing is
  copied; it reads your file in place, NAS included). Host-machine only, like
  the other pickers. **Or paste a path:** copy a video file's path (Finder's
  "Copy as Pathname" / Explorer's "Copy as path"), select the node, and press
  **Ctrl/Cmd+V** — it loads that video. (Quotes and `file://` wrappers are
  handled; pasting into a text field still pastes normally.)
- **Or wire a video in (v0.60.0):** the optional `video` input takes any
  `VIDEO` output already in your workflow — and the wire wins over the
  browsed path. Wire from a **Load Video** node and the scrubber works
  exactly as for a browsed file (including from another machine's browser —
  those files live in ComfyUI's input folder, which is already shared).
  Wire from anything else (Video Slice, a generated video) and the preview
  honestly says the video arrives when the workflow runs — type the frame
  number instead; a number past the end clamps to the last frame, and a
  Video Slice's trim is respected (frame 0 is the slice's first frame).
- **Scrub to the frame you want:** the node shows the video with a transport
  strip — **jump to start**, **−5**, **−1**, **play/pause**, **+1**, **+5** —
  plus a frame-number box and a live **Frame X / N** counter (total frames
  included). **Hold** any of the step buttons and it keeps moving through the
  timeline instead of making you click repeatedly; it stops the moment you
  release, slide off the button, or switch away from the window.
- **Outputs** `image`, `width`, `height` for the selected frame. The on-screen
  preview is best-effort (browser video seeking isn't always frame-perfect);
  the frame extracted on Run is exact (decoded server-side with PyAV).
- Common codecs (H.264 mp4, webm) play and scrub smoothly; an exotic codec the
  browser can't decode still extracts correctly on Run, it just won't preview.

## Install

See [docs/INSTALL.md](docs/INSTALL.md). Short version: clone into
`ComfyUI/custom_nodes/` and restart ComfyUI. No pip requirements.

## The library folder

Everything lives in one folder — `*.md` prompt notebooks (`loras.md` is
just the default one), `sets/*.json` (saved lora states),
`resolution_presets.json` (saved size presets), `lora_picker.json` (the
LoRA Picker's favorites and recents), and `lora_sets_layout.json` (the
State Controller's groups and order) — configured
in **Settings → EPSNodes → Library folder** (server-side, so every
browser sees the same value). Point it at a shared/NAS path to use the same
library from multiple machines. This pack's HTTP routes (library browse/
read/write, the checkpoint-list, size-preset, LoRA-picker and list-flags
feeds, plus the image-grid and frame-saver helpers) carry no auth layer
of their own, so exposing ComfyUI beyond localhost (`--listen`) exposes them
too — the same trust model as ComfyUI's own routes. Details:
[docs/FORMAT.md §1](docs/FORMAT.md) (layout) and [§2](docs/FORMAT.md)
(security posture).


## Library on a NAS

Pointing the library at a network share works (it's the design center),
and since v0.74.0 it stays responsive when the share is slow: the server
never waits on the share while serving other requests, a notebook that
hasn't changed re-loads with a single stat instead of a full read (and the
panel paints instantly from what it last showed while it checks), and the
State Controller's 15-second sets poll costs one stat instead of re-reading
every state file. What to expect: a state saved from another machine
through EPSNodes shows up on the next poll; a state file you hand-edit in
place on the share shows within 30 seconds; an unmounted share makes the
affected panel report the folder as unreachable instead of freezing
ComfyUI. The State Controller shows where your states live and has an
"Open folder" button on the machine running ComfyUI. **To share states,
groups, favorites and presets between computers, point every machine's
Settings → EPSNodes → Library folder at the same share** — notebooks can
also point at absolute share paths per node, which is why they may already
look "shared" while states are not.

## Sharing workflows between machines

ComfyUI lists subfoldered models with the saving machine's path separator
— `styles\film_grain.safetensors` on Windows, `styles/film_grain.safetensors`
on Linux/macOS — so a workflow saved on one and opened on the other shows
every model as missing and the queue rejects it. Since v0.71.0 EPSNodes
heals this on load: for every model/file combo (core Load Checkpoint /
UNET / CLIP / VAE / LoRA / ControlNet / upscale loaders, Load Image
folders, third-party loaders) a saved value that is missing here but
matches **exactly one** local file once `\`/`/` are normalized is
re-pointed at that file, and one toast reports how many were healed. It
never guesses (zero or several candidates are left alone), never changes
case, and fires for workflow loads, image/JSON drops, templates, undo/redo
and paste. Toggle it in **Settings → EPSNodes → Workflows → Heal model
paths across operating systems on load** (on by default; the setting is
per server, so set it on each machine). The pack's own LoRA nodes already
store forward-slash names and resolve either way; the Checkpoint
Switcher's ticks travel the same way. Keep the folder layout under each
machine's `models/` mirrored and the chore is gone — a model that truly
lives somewhere else on the other machine stays red for you to re-pick.

## Versioning

Backend and frontend each carry the pack version and it is shown in
**Settings → EPSNodes**; a mismatch means you pulled an update but
haven't restarted the server (or need a hard refresh). Every push bumps the
version and is tagged.

## License

MIT — see [LICENSE](LICENSE).
