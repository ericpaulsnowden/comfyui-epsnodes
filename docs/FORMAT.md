# FORMAT.md — the binding contract for EPSNodes

Naming (2026-07-18 rebrand, refined same day): the PACK is **EPSNodes**
everywhere a user sees it (node-browser category, Settings section, About
badge); the REPO/install folder is **`comfyui-epsnodes`**, matching the
sibling plugins' `comfyui-*` convention (owner decision). The python
module `lora_library/`, the `/lora_library/*` route prefix, and the
`LoraLibrary*` node class ids are the pack's first FEATURE FAMILY and stay
frozen (§8) — future non-lora features arrive as sibling modules under the
same EPSNodes banner, without repo churn. `LoraLibraryNotebook`'s display
name is **"Prompt Notebook"** (it stores prompts; the original name was a
misnomer).

This document is BINDING, in the comfyui-photoshop-bridge PROTOCOL.md sense:
the backend (`lora_library/`), the frontend (`web/`), and the on-disk file
formats must all match what is written here. Any interface change amends this
file FIRST, in the same commit as the code. Section numbers are stable —
cite them in code comments as `FORMAT.md §N`.

Contents: §1 library directory · §2 security posture · §3 notebook markdown
grammar · §4 set files · §5 HTTP routes · §6 nodes · §7 frontend surfaces ·
§8 versioning & stability.

---

## §1 The library directory

One directory holds everything a user shares between machines:

```
<library_dir>/
  loras.md          # default notebook file (§3) — users may add more .md files
  sets/             # one JSON file per saved LoRA set (§4)
    <slug>.json
```

- `library_dir` is persisted server-side in `<comfyui user dir>/lora_library/
  config.json` as `{"library_dir": "<absolute path>"}`. When unset, the
  default is `<comfyui user dir>/lora_library/library/`.
- The whole point of the setting is that it may point ANYWHERE the server
  process can read/write — a NAS share (`\\nas\share\comfy-library`, a mounted
  volume, a Dropbox folder). Multi-machine sharing = both machines' ComfyUIs
  pointing at the same directory. This is the design center (owner
  requirement), not an edge case.
- The notebook node's `file` value resolves per
  `LibraryContext.resolve_notebook_file`: relative → under `library_dir`;
  absolute (incl. UNC) → used as-is. Set files are ALWAYS under
  `<library_dir>/sets/` — sets are library-wide, not per-workflow.
- Writes are atomic (same-directory temp file + `os.replace`) and preserve
  the target file's dominant line-ending style (§3.6). Never call
  `os.path.relpath` against ComfyUI dirs (cross-drive crash on Windows).

## §2 Security posture

ComfyUI custom routes have no auth layer, so exposure follows the server's
own bind address:

- **Loopback requests** (`request.remote` is 127.0.0.1/::1): full capability —
  read/write any path the process user can touch, per §1.
- **Non-loopback requests** (the server is `--listen`-ing and the caller is
  another machine): mutating routes (§5 POST) and arbitrary-path reads REFUSE
  paths outside `library_dir` with `403 {"error": ...}` naming this section.
  Everything inside `library_dir` still works — a remote browser tab driving
  a shared library is legitimate.
- Notebook writes additionally require the resolved path to end in `.md`
  (any request origin). Set slugs must match `^[a-z0-9][a-z0-9-_]*$` and
  resolve strictly inside `sets/` (no traversal).
- `POST /lora_library/config` is loopback-only: `library_dir` IS the
  boundary the two rules above enforce for remote callers, so only the
  local machine may move it.

## §3 Notebook markdown grammar

A notebook file is plain Markdown, hand-editable in any editor. The unit of
retrieval is the **entry**.

### §3.1 Structure

```
(optional preamble — anything before the first heading, preserved verbatim)

# Category Name          ← optional H1 = category (groups entries)

## Entry Name            ← H2 = one entry
entry body …             ← everything until the next H1/H2 heading

## Another Entry
…
```

- `## ` (H2) starts an entry; the heading text (trimmed) is the entry name.
- `# ` (H1) starts a category; entries that follow belong to it until the
  next H1. Entries before any H1 have category `""`.
- **Category description** (owner ask 2026-07-19): the prose between a
  `# Category` heading and its first `## entry` (or the next heading) is
  that category's DESCRIPTION — plain text, preserved verbatim on
  round-trip, empty when absent. It is presentation/reference prose only:
  it is never an entry, never appears in the node's outputs.
- `###` and deeper headings belong to the entry body (they do NOT split).
- Fenced code blocks (``` … ```) are respected: heading-looking lines inside
  a fence are body text, not boundaries.

### §3.2 Names

- Entry names are unique per file, compared case-sensitively after trimming.
  A file with duplicates still parses: the FIRST occurrence is addressable;
  every duplicate is reported in the route's `problems` array (§5) so the UI
  can warn. Names cannot contain newlines; empty H2 headings are reported as
  problems and skipped.

### §3.3 Entry text (what the node outputs)

- The entry's text is its body verbatim, minus leading/trailing blank lines,
  joined with `\n`. Internal blank lines are preserved. This exact string is
  the node's STRING output — no substitution, no templating (v1).

### §3.4 Write operations (roundtrip safety)

Writers re-emit the file from the parse, with these guarantees:

- Preamble, category headings, category order, entry order, and every
  untouched entry's body are preserved byte-for-byte (modulo §3.6 line
  endings and a single guaranteed trailing newline).
- **Update** replaces one entry's body in place (and/or renames its heading).
- **Create** appends the entry: to the end of the named category when
  `category` is provided (creating the category heading at end-of-file if
  new), else to the end of the file.
- **Delete** removes the entry's heading + body. Deleting a category's last
  entry leaves the (now empty) category heading in place — categories are
  the user's prose, not derived state.
- A body line that itself starts with `# ` or `## ` (outside a fence) cannot
  be represented; saves containing one are refused with 400 and a message
  telling the user to use `###`, indentation, or a code fence.
- **Create category** appends a new `# Name` heading at end-of-file (name
  must be unique among categories, non-empty after trimming, no newlines);
  **Set category description** replaces the §3.1 description block under
  an existing heading (same un-representable-line rule as entry bodies:
  a description line starting with `# `/`## ` outside a fence is refused).
- **Move** relocates one entry (drag-reorder's primitive): to just before a
  named sibling entry, or to the END of a named category (creating that
  category heading at end-of-file when new), or to the end of the file
  (`category: ""` targets the uncategorized head region only when one
  exists, else the file start). Category membership FOLLOWS position — the
  file is the truth, so dragging an entry under another category heading IS
  the category change. The moved entry's body travels byte-identically.
- **Create after** (owner ask 2026-07-19 "New makes an entry right below
  the selected one"): create supports an optional `after` = an existing
  entry name; the new entry is inserted immediately BELOW it (same
  category), rather than at end-of-file/category. `after` naming an unknown
  entry, or omitted, falls back to the existing append behavior. The new
  entry's name must still be unique (§3.2).
- **Move category** (owner ask 2026-07-19 "drag category and everything in
  it"): relocate a whole category block — its `# heading`, its §3.1
  description, and ALL its entries as one unit — to just before another
  named category, or to end-of-file. Every moved entry's body and the
  description travel byte-identically; the relative order inside the block
  is preserved. The uncategorized head region (`""`) is not a movable
  category.

### §3.5 Concurrency (the two-machine case)

Save/delete requests carry `base_mtime` (the file mtime the client last
loaded, as a float). If the file's current mtime differs by more than 1e-6,
the server refuses with `409 {"error", "mtime"}` and writes nothing; the UI
offers reload-then-reapply. Omitting `base_mtime` skips the check (first
save to a brand-new file).

### §3.6 Line endings

On write, the file's dominant existing line ending (CRLF vs LF; LF for new
files) is preserved across the whole file — a library shared between the
Windows PC and the Mac must not flip-flop diffs.

## §4 Set files

One JSON file per set: `<library_dir>/sets/<slug>.json`, UTF-8, format:

```json
{
  "format": 1,
  "name": "Cinematic portrait",
  "loras": [
    {"file": "subdir/detailer.safetensors", "on": true,  "strength": 0.8, "strength_clip": null},
    {"file": "film_grain.safetensors",      "on": false, "strength": 1.0, "strength_clip": 0.5}
  ],
  "trigger_words": "cinematic, film grain",
  "notes": ""
}
```

- `format`: integer, `1` or `2` (see §4.1). Readers reject GREATER values
  with a clear "update the pack" error and tolerate missing optional fields.

### §4.1 Format 2 — composite multi-loader state (owner ask 2026-07-20)

For the WAN high/low workflow (two+ Power Lora Loaders, each with its OWN
distinct set), a state may store per-loader configs. Format 2 ADDS a
`loaders` array; it is fully backward/forward-compatible:

```json
{
  "format": 2,
  "name": "WAN hi+lo",
  "loaders": [
    {"loras": [ {"file": "...", "on": true, "strength": 0.8, "strength_clip": null} ]},
    {"loras": [ {"file": "...", "on": true, "strength": 0.3, "strength_clip": null} ]}
  ],
  "loras": [ {"file": "...", "on": true, "strength": 0.8, "strength_clip": null} ],
  "trigger_words": "",
  "notes": ""
}
```

- `loaders[i].loras` = the i-th loader's rows, ordered by ascending node id at
  capture time (loader 0 = the lowest-id Power Lora Loader). Each `loras` list
  obeys every §4 rule (order = apply order, separator-insensitive resolve,
  `on:false` kept-not-applied, `strength_clip:null`).
- `loras` (top level) is MIRRORED = `loaders[0].loras`, so a format-1-only
  reader (old pack, or any consumer that only knows `loras`) still gets a
  sane single-loader config instead of nothing. Writers MUST keep it in sync
  with `loaders[0]`.
- A **format-1** state (no `loaders`) is a single-loader config: applying it
  to N loaders applies the same `loras` to all (the pre-2026-07-20 behavior,
  unchanged). Readers treat "no `loaders` key" as format 1 regardless of the
  `format` integer, so a hand-edited file degrades gracefully.
- Consumers pick their slice by INDEX (see §6.2 Apply `loader_slot`, §6.3
  controller capture/apply). Index out of range clamps to the last available
  loader (never errors).
- `loras[]` order **is** application order (reordering is a first-class
  feature). `file` holds the lora exactly as ComfyUI lists it
  (`folder_paths.get_filename_list("loras")`) — NOTE that ComfyUI uses the
  OS's native separator there, so a set written on Windows carries `\` and
  one written on macOS carries `/`. Resolution at apply time is therefore
  SEPARATOR-INSENSITIVE and returns the INSTALLED spelling for this
  machine: exact match after normalizing both sides' separators to `/`
  first, then unique basename match (rgthree-style leniency for
  cross-machine subfolder differences; basename = last segment across
  either separator); a lora that still doesn't resolve — including an
  AMBIGUOUS basename — is SKIPPED WITH A LOGGED WARNING — a missing file
  must not fail the whole run.
- `on: false` rows are kept (they round-trip through the UI) but not applied.
- `strength_clip: null` means "use `strength` for both model and clip"
  (parity with rgthree's single-strength mode).
- `slug` = filename stem; derived from `name` when saving (lowercase; spaces
  → `-`; strip everything outside `[a-z0-9-_]`; collision → `-2`, `-3`, …).
  `name` is the display name and may be any string.

## §5 HTTP routes

All under `/lora_library/`, JSON in/out; errors are `{"error": "<human
message>"}` with a 4xx status. `mtime` values are float POSIX seconds.

| Route | → |
|---|---|
| `GET /lora_library/version` | `{"version": "X.Y.Z"}` |
| `GET /lora_library/config` | `{"library_dir", "default_library_dir", "configured": bool, "is_local": bool, "library_dir_exists": bool, "library_dir_note": str}` — `is_local` = §2 loopback verdict for THIS request (drives §7.2's remote read-only gating). `library_dir_exists` = whether the SERVER can see the configured folder right now — the owner's 2026-07-19 NAS confusion was a library path the server machine couldn't resolve, invisible until a node errored. `library_dir_note` = "" when fine, else a one-line human diagnosis chosen server-side: unreachable path, or a path whose SHAPE doesn't match the server's OS (e.g. `/Volumes/…` configured while the server is Windows, or `C:\`/UNC while it's POSIX — a strong sign it was set from the wrong machine's perspective) |
| `GET /lora_library/fs/list?dir=` | **loopback-only** (403 remote): server-filesystem browser for the §7.2 picker. Empty/missing `dir` ⇒ `library_dir`. `dir="ROOTS"` (sentinel) ⇒ the top level: on Windows every existing drive (`C:\`, `D:\`, `U:\`, …) as `dirs` + `parent: null`; on POSIX the filesystem root `/`. → `{"dir": <abs or "ROOTS">, "parent": <abs, "ROOTS", or null>, "dirs": [names], "files": [names]}` — `files` limited to `.md`; entries sorted case-insensitively; a directory at a drive root reports `parent: "ROOTS"` so the picker can climb to the drive list (the 2026-07-19 "stuck at top of C:\, can't reach another drive/NAS" fix); a UNC path (`\\server\share\…`) passed as `dir` lists normally; unreadable/nonexistent dir ⇒ 400 |
| `POST /lora_library/notebook/open_folder` `{"file"}` | **loopback-only** (403 remote): reveals the resolved notebook file's folder in the OS file manager ON THE SERVER MACHINE (Explorer/Finder). Missing folder ⇒ 404; `{"ok": true}` |
| `POST /lora_library/config` `{"library_dir"}` | validates (absolute, creatable, writable), persists; `{"ok", "library_dir"}` |
| `GET /lora_library/loras` | `{"loras": [".."]}` — installed loras for pickers |
| `GET /lora_library/notebook?file=` | `{"file": <resolved abs>, "exists": bool, "mtime", "entries": [{"name","category"}], "categories": [names in file order — includes EMPTY categories, which `entries` alone can't reveal], "problems": [".."]}` (missing file ⇒ `exists:false`, empty lists — NOT an error) |
| `GET /lora_library/notebook/category?file=&name=` | `{"name","description","mtime"}`; 404 if no such category |
| `POST /lora_library/notebook/category` `{"file","name","description"?,"after"?,"rename_to"?,"base_mtime"?}` | §3.4 create-or-describe: unknown `name` ⇒ CREATE the category (default end-of-file; `after` = insert the new `# heading` right after that entry/category — used by New-below when the active item is a category) with the given description; known `name` ⇒ replace its description and, when `rename_to` is present, rename the heading (unique among categories). §3.5 ⇒ 409; un-representable description lines ⇒ 400. → `{"ok","mtime","entries","categories"}` |
| `GET /lora_library/notebook/entry?file=&name=` | `{"name","category","text","mtime"}`; 404 if absent |
| `POST /lora_library/notebook/entry` `{"file","name","text","category"?,"after"?,"rename_to"?,"base_mtime"?}` | create-or-update per §3.4/§3.5; `after` = insert a NEW entry directly below that entry (§3.4 Create after); `{"ok","mtime","entries"}` (fresh list) |
| `POST /lora_library/notebook/move_category` `{"file","name","before"?,"base_mtime"?}` | §3.4 Move category: relocate the whole block before the named category, or to end-of-file when `before` omitted; unknown `name`/`before` ⇒ 404; §3.5 ⇒ 409; `{"ok","mtime","entries","categories"}` |
| `POST /lora_library/notebook/delete` `{"file","name","base_mtime"?}` | `{"ok","mtime","entries"}` |
| `POST /lora_library/notebook/move` `{"file","name","before"?,"category"?,"base_mtime"?}` | §3.4 Move: exactly one of `before` (entry name to insert before) or `category` (append to that category's end; `""` = uncategorized/file-end rule) — both/neither ⇒ 400; unknown `name`/`before` ⇒ 404; §3.5 conflicts ⇒ 409; `{"ok","mtime","entries"}` |
| `GET /lora_library/sets` | `{"sets": [{"slug","name","count"}]}` sorted by name |
| `GET /lora_library/set?slug=` | the full §4 JSON + `"slug"` |
| `POST /lora_library/set` `{"slug"?, "set": {…}}` | save (slug derived from `set.name` when absent); `{"ok","slug","sets"}` |
| `POST /lora_library/set/delete` `{"slug"}` | `{"ok","sets"}` |

Route paths are FROZEN once shipped (§8).

## §6 Nodes

Class ids are FROZEN once shipped. Both nodes re-read their files at every
execution — **the file is the truth; the UI is a view.**

### §6.1 `LoraLibraryNotebook` (display: "Prompt Notebook")

- Widgets: `file` (STRING, default `"loras.md"`), `entry` (STRING — the
  SELECTION; the DOM widget UI sets it, but it stays a plain serialized
  STRING so workflows and the API can drive it without our JS). Multi-
  select: `entry` holds one entry name per LINE (newline-separated, order =
  selection order); a single name is the degenerate one-line case, so every
  pre-multiselect workflow keeps working unchanged.
- The two-pane editor (§7.2) is a DOM widget that does NOT serialize into
  the workflow — only `file` + `entry` persist (owner requirement: the
  workflow stores the pointer, never the text).
- Outputs: `text` (STRING) + `name` (STRING), both declared
  `OUTPUT_IS_LIST` — one element per selected entry, in selection order.
  ComfyUI's list execution then runs every downstream consumer once per
  element: selecting three prompts queues one run that generates with each
  prompt separately (the owner's fan-out ask), and a single selection
  behaves exactly like a plain STRING for typical wiring. `name` is the
  entry's heading text (§3.2) — usable for filename prefixes, captions, or
  routing.
- Execution: resolve → parse → return the entries' texts+names. Missing
  file, an empty selection, or ANY missing selected entry ⇒ node error
  naming the file/entry (a failed lookup must be loud at queue time, §3.5
  notwithstanding).
- `IS_CHANGED` → `(resolved_path, mtime, size, entry)` tuple-ish string so an
  on-disk edit from the *other* machine re-executes; `VALIDATE_INPUTS`
  returns True (entry names are dynamic).

### §6.2 `LoraLibraryApplySet` (display: "Apply LoRA Set")

- Optional inputs: `model` (MODEL), `clip` (CLIP).
- Widgets: `set` (COMBO of set names by slug + `"None"`), `strength_scale`
  (FLOAT 0.0–2.0, default 1.0, step 0.05 — master multiplier on every
  applied strength). **`strength_scale` is HIDDEN by default** (owner ask
  2026-07-20: "this should be turned off by default … by default the
  strength should pass through what is set in the loader … it's an edge
  case"): a node property `Show strength scale` (default false, right-click
  Properties) reveals the widget. Hidden, its value stays `1.0`, so the
  default is a clean pass-through of each set's stored strengths — the
  multiplier never silently overrides them. Move `strength_scale` to
  `optional` (default `1.0`) as part of this change (same rationale as the
  Switcher's `toggles`, §6.4): a hidden widget still serializes from the
  frontend, and `optional` makes a hand-built `/prompt` that omits it get
  `1.0` rather than a "required input missing" rejection. The apply math
  (`strength * strength_scale`) is otherwise unchanged.
- Outputs: `MODEL`, `CLIP`, `LORA_STACK`, `STRING` (`trigger_words`),
  `STRING` (`loras_text`).
- `loras_text` is the normalized summary of what was applied (owner format,
  2026-07-18c — filename/caption-friendly, no `<>`/`:` punctuation): the
  enabled, resolved rows in order as `stem_strength` tokens —
  `MYLORA_HIGH_1`, `detailer_0.8` (dual strengths append both:
  `detailer_0.8_0.4`) — `stem` = basename without extension, strengths
  post-`strength_scale` formatted `%g`, tokens space-joined; `""` when
  nothing applied.
- **`loader_slot` (INT, optional, default 0, owner ask 2026-07-20, §4.1):**
  which loader's slice of a COMPOSITE (format-2) state to apply. `0` = the
  first loader (and the whole config for a plain format-1 state, where the
  slot is ignored). For the WAN workflow: an Apply node representing the high
  loader uses slot 0, the low loader uses slot 1 — so two Apply nodes on the
  SAME composite state produce DISTINCT `loras_text` (this is the fix for the
  owner's "both Apply nodes show the same loras_text" report — same root
  cause as the controller's shared-config bug). Out-of-range clamps to the
  last loader. HIDDEN by default behind a `Show loader slot` node property
  (same declutter pattern as `strength_scale`); most single-loader users
  never see it. In `optional` (API-omit-safe), default 0.
- Behavior: loads the §4 file; selects the slice per `loader_slot` when the
  file is format 2 (else the single `loras`); applies enabled rows IN ORDER
  via the same core machinery ComfyUI's own LoraLoader uses, when
  `model`/`clip` are wired; always emits `LORA_STACK` = `[(file, strength_model,
  strength_clip), …]` for enabled rows (efficiency-nodes-compatible) and
  `trigger_words`. With no model/clip wired the node is a pure
  stack/trigger source. `"None"` (or a missing/unresolvable set) with
  model/clip wired ⇒ passthrough + empty stack; missing SET file logs a
  warning, missing individual loras follow §4 skip rules.
- `IS_CHANGED` → set file mtime/size + widget values; `VALIDATE_INPUTS`
  True (set list is dynamic).
- **Sync target of the controller's Push State (§6.3).** Apply LoRA Set
  needs no structural change for this: the controller's Push State button
  sets each Apply LoRA Set node's `set` widget to a chosen state and
  triggers a re-read. So one controller can keep any number of Apply LoRA
  Set nodes on the same state at once — the owner's "multiple Apply LoRA
  Set nodes all controlled by one controller, kept in sync" use case.
- **`mirrors loader` tag (owner ask 2026-07-19c: "set different Apply LoRA
  Set nodes to different Power Lora Loaders as targets").** A FRONTEND-ONLY
  combo widget (added by `sets.js` on nodeCreated; the server never sees
  it) listing the graph's PLL nodes plus `"(any)"` (default). It's a
  GROUPING TAG for §6.3's selective Push — it does not change what the
  node executes (states still come from the file). Serialized with the
  node (appended after the server widgets — appended-last consistently so
  positional restore stays aligned); stores the tagged PLL's node id,
  displayed by title; tolerates the id disappearing (falls back to
  "(any)").

### §6.3 `Lora Loader State Controller` (frontend-only virtual node)

Naming (owner 2026-07-18c, refined 2026-07-19): the node's DISPLAY name is
**"Lora Loader State Controller"** (was "Power Lora Loader State
Controller" — "Power" dropped) and every user-facing word in its UI says
**state**, not set — widget label `state`, buttons `New State` (capture
current rows as a new state), `Save State` (overwrite the selected state
with current rows), `Delete State` (two-click confirm), and `Push State`
(broadcast — below). The class id `LoraLibrarySetController` stays frozen
(§8), and states ARE §4 set files — same storage, same routes, same files
the Apply LoRA Set node reads; only the controller's vocabulary changes.

**Push State** (owner ask 2026-07-19; SELECTIVE since 2026-07-19c): sets
`LoraLibraryApplySet` nodes to the controller's currently-selected state
(writing each one's `set` widget + firing its callback). WHICH Apply nodes
it touches follows the controller's `target` and each Apply node's §6.2
`mirrors loader` tag: controller target = a specific PLL ⇒ only Apply
nodes tagged to that PLL (plus, when none are tagged to it, a toast says
so instead of silently doing nothing); controller target = `All…` ⇒ every
Apply node regardless of tag; an Apply tagged `"(any)"` is included in
every push. The toast reports the count. This is the sync mechanism for
"different Apply nodes represent different loaders, one controller keeps
each group in step." It also double-serves as an explicit re-apply.

**Composite multi-loader capture/apply (owner ask 2026-07-20, §4.1
format 2): with target = `All Power Lora Loaders (N)`, each loader keeps its
OWN config.** The prior behavior — capture read only the lowest-id PLL and
apply wrote that one config to ALL — is replaced FOR THE `All` TARGET ONLY:
- **New State / Save State with target `All`:** capture EVERY PLL's rows, in
  ascending-node-id order, into a §4.1 format-2 state (`loaders[i]` = the
  i-th PLL). Also mirror `loras` = `loaders[0]` (§4.1). The read-back toast
  summarizes per loader ("Saved 'WAN': L0 detailer 0.8 / L1 detailer 0.3").
- **Selecting/applying a format-2 state with target `All`:** apply
  `loaders[i]` to the i-th PLL by ascending id; if the state has FEWER
  loaders than the graph, extra PLLs are left untouched (never guess) and a
  toast notes the mismatch; extra state loaders beyond the PLLs are ignored.
- **Single-PLL target** (target = one specific loader): capture writes a
  plain format-1 state (that one loader's rows). Applying a format-2 state to
  a single target uses the slice for THAT loader's ascending-id index among
  the graph's PLLs (clamped), so single-targeting the low loader restores the
  low slice; a format-1 state applies its single `loras` as before.
- **Backward compatible:** a format-1 state through target `All` still
  applies its one config to every PLL (unchanged). Nothing about the
  single-loader common case changes.

**State selection must not depend on widget-internals (2026-07-19c
hardening).** v0.12.0's apply-on-select shadowed the combo's `setValue` —
correct on the dev rig's frontend but STILL reported broken on the owner's
ComfyUI 0.28.1 ("strengths are still not saved or updated"), i.e. the
shadow point is not stable across frontend builds. The durable design: the
controller OWNS its state dropdown end to end — clicking the state widget
opens a menu the controller builds itself (`LiteGraph.ContextMenu` or
equivalent), and every pick runs capture-independent apply logic directly.
No reliance on `BaseWidget.setValue`/`callback` semantics, so no
same-value no-op and no version skew. Additionally: `Save State` performs
a READ-BACK after saving (GET the state and toast the saved rows —
"Saved 'X': detailer 0.8, grain 1.2") so file truth is visible; and while
`Show status` is on, the status line names the capture-source loader id +
row count on every capture/save. Diagnose the 0.28.1 failure by upgrading
the dev rig's `comfyui-frontend-package` to the version ComfyUI 0.28.1
pins before concluding anything.

**Strength persistence — 2026-07-19 bug fixes (owner: "Save State doesn't
work; the loader isn't remembering strengths; re-picking reverts").** Two
distinct causes, both fixed here:
1. Re-selecting the SAME state in the `state` combo is a no-op on this
   litegraph fork (the callback only fires when the value CHANGES), so
   after Save State a re-pick never re-applied and looked like a revert.
   Fix: apply must be invokable independently of a combo-value change —
   `Save State` re-applies immediately after saving, and any state
   selection (even to the current value) forces an apply. Verify the
   fork's actual same-value callback behavior live and route around it.
2. Capture must read the LIVE dragged strength. rgthree stores it at
   `widget.value.strength` and mutates in place on step/drag
   (power_lora_loader.js `stepStrength`: `this.value[prop] = …`), so a
   live read is correct — VERIFY on the rig with a REAL strength drag
   (not a programmatic value swap) against a real lora, since real loras
   carry `loraInfo` (strengthMin/Max) that rgthree clamps against; confirm
   capture and apply both preserve a hand-dragged value end to end. If the
   real drag path stores strength anywhere other than `value.strength`,
   read that and document it.

Registered purely in JS (like core's MarkdownNote) under the type name
`LoraLibrarySetController`; it never executes server-side and never blocks a
queue. It drives a **genuine, untouched `Power Lora Loader (rgthree)`**:

- **TWO-PANE layout (owner ask 2026-07-21 — supersedes the state DROPDOWN;
  "just the two-pane layout"):** replace the `state` COMBO with a Notebook-
  style two-pane DOM widget — LEFT: a scrolling list of ALL states (one row
  per state, the current one highlighted); RIGHT: the buttons stacked
  vertically. **Select vs. apply are SEPARATE clicks (owner ask 2026-07-21 —
  supersedes "clicking a row IS the apply"):** a SINGLE click only *selects* a
  row (highlights it, loads its name into the `name` field) and does NOT touch
  any loader — so the user can rename or delete a state without rewriting every
  wired loader. A SECOND click on the already-selected row (i.e. a double-click,
  or a click on the row that is already highlighted) is what *applies* the state
  to the target loader(s) — forcing the apply even when re-picking the same row,
  per the strength-persistence fix. (The old auto-apply-on-single-click was too
  eager: selecting to rename/delete detonated an apply across all loaders.) A
  `name` text field for New/Save stays. **Save State honors a changed name
  (owner bug 2026-07-21 — "Save doesn't save a new name if an element is
  selected"):** when a row is selected and the `name` field has been edited to
  something different, `Save State` writes a NEW state under that new name
  (this is the primary way to spin a new item off an existing one) and selects
  it; when the name is unchanged it overwrites the selected state as before.
  `New State` (empty name → capture current rows) is unchanged. The selected
  state must still round-trip as a serialized value
  (keep the internal `set` STRING widget, hidden, driven by the list
  selection — the Notebook's `entry`-widget trick, §7.2), so a saved
  workflow reopens on the same state and re-selecting never auto-re-applies
  on load. Storage is UNCHANGED (per-state JSON files in the shared library
  folder, configured in Settings as today — NO file/Browse panel this round,
  owner scoped it to "just the two-pane layout"). Mirror the Notebook's
  list-render/scroll/click/highlight (`web/lora_library/notebook.js`) and its
  DOM-widget sizing so the panel fills the node and never collapses.
- Widgets/controls retained: `target` (COMBO of PLL nodes by title `#id`,
  PLUS `All Power Lora Loaders (N)` when N ≥ 2 — the WAN high/low case;
  auto-selects when exactly one exists). Buttons (now stacked in the RIGHT
  pane): `New State`, `Save State`, `Delete State` (two-click "Are you sure?"
  confirm; the armed button is visually distinct, survives background cache
  refreshes for its full window, and selection is slug-anchored so a
  mid-window sets-poll cannot invalidate it — the 2026-07-18 "delete does
  nothing during a running workflow" bug), and `Push State` (broadcast to all
  Apply LoRA Set nodes). All existing behavior — apply-on-select, composite
  capture/apply with target `All`, selective Push, `Show status`,
  serialize-based capture (v0.14.1), own-menu version-proof apply (v0.13.0) —
  is PRESERVED; only the state-selection UI changes from a dropdown to the
  left list.
- Multi-target semantics: with `All…` selected, APPLY writes the set to
  every PLL in the graph; CAPTURE reads from the lowest-node-id PLL (a
  deterministic, documented choice — capture needs one source of truth).
- A read-only `status` line exists for debugging but is HIDDEN by default;
  the node property `Show status` (boolean, default false, in the node's
  right-click Properties) reveals it. Fail-soft states (§ below) must
  surface through toasts/disabled widgets even while status is hidden.
- **Capture** reads the target's lora rows — value shape `{on, lora,
  strength, strengthTwo}` (rgthree) — into a §4 set (`strengthTwo` ⇒
  `strength_clip`; absent ⇒ `null`).
- **Apply** rewrites the target's rows to match the set exactly: row count,
  ORDER, on/off, strengths, then dirties the canvas. Loras missing on this
  machine stay in the row (rgthree shows its own missing-lora state) — the
  user sees the truth rather than a silently shrunken set.
- Feature detection, not version pinning: if the target's widgets don't
  look like PLL rows (no `.value.lora`), the controller disables itself
  with a visible message instead of corrupting widgets. nd-super-nodes'
  `{enabled, strengthClip}` aliases are read (not written) for capture.
- If rgthree isn't installed the node still loads and says so — pointing
  at `LoraLibraryApplySet` as the no-dependency alternative (ethos: the
  ComfyUI-only floor is §6.2; the controller is the upgrade for rgthree
  users).

## §6.4 `EPSSwitcher` (display: "EPS Switcher") — image toggle + fan-out

Roadmap: `research/roadmap-eps-switcher.md` (M1 = this section). NON-lora node;
lives in the sibling `eps_image/` module, category "EPSNodes". Class id
`EPSSwitcher` frozen once shipped (§8). Genuinely novel per research
(`research-eps-nodes.md`): every existing switch picks ONE input; nothing fuses
per-input toggle + toggle-all header + N-enabled→N-runs fan-out.

- **Inputs:** growing optional `image_1`, `image_2`, … (IMAGE) — a fresh empty
  socket appears when the last is connected; connected slots never renumber;
  trailing empties collapse to exactly one spare (the monorepo's proven
  pattern: backend `_FlexibleOptionalImageInputs` dict-subclass à la
  `cprb/nodes_save.py`'s `_FlexibleOptionalVideoInputs`; frontend
  `converge`/`onConnectionsChange` à la `cprb/web/cprb/nodes.js`, guarding
  `configure()`'s hardcoded `isConnected=true` restore). Do NOT use core
  `io.Autogrow` (unverified on the rig's 1.45.21).
- **Per-input toggle + header:** each `image_N` row carries an on/off toggle;
  a header "all on / all off" tri-state toggle like rgthree's Power Lora
  Loader (borrow the pattern, MIT — write fresh, no rgthree runtime dep).
  Toggle state is a serialized node property/widget (survives reload).
- **Renamable rows** (owner ask 2026-07-20): double-clicking an `image_N`
  row renames its DISPLAYED label only — set `input.label` (litegraph draws
  `label || name`); `input.name` stays the frozen `image_N` (it is the
  backend kwargs/serialization contract, and `toggles` keys stay names).
  Labels persist with the workflow (the serialized inputs array carries
  `label`; verify `configure()` restores it). The per-row toggle box measures
  the DISPLAYED label so a long label never collides with its hit-region.
  Renaming to an empty string resets the label back to the socket name.
- **Output:** single `IMAGE` declared `OUTPUT_IS_LIST` — emits the ENABLED
  images in slot order; downstream runs once per enabled image (N enabled →
  N runs) via core list execution. Disabled inputs are simply omitted from
  the list (v1 = simple filter; their upstreams still execute — lazy-skip is
  the tracked M3 future, `research-eps-nodes.md` § lazy backlog).
- **All-off / none-connected is a VALID state** (owner decision 2026-07-20,
  supersedes the v0.14.0 queue-time error — "there will be times when a user
  might want to turn them all off"): queueing with every input toggled off,
  or nothing wired at all, must SUCCEED, with the downstream image branch
  simply not running that queue. No error, no silent crash. Mechanism: when
  zero images are enabled, return `[ExecutionBlocker(None)]` (lazy
  `from comfy_execution.graph import ExecutionBlocker`) as the list — a bare
  empty list only propagates safely while every downstream list input comes
  from this node (a node mixing our list with a non-empty co-input hits
  repeat-last on an empty list → IndexError), while an ExecutionBlocker makes
  core skip dependent nodes silently (rgthree / Impact precedent). VERIFIED
  LIVE 2026-07-20 (shipped v0.16.0): all-off and none-connected queues
  succeed (`status_str: success`, no `execution_error`) with zero downstream
  executions — including when the blocked list feeds a node that mixes it
  with a non-empty co-input (ImageBlend test). The bare-empty-list
  alternative was traced in core execution.py and REJECTED: sole-input
  empty list calls the downstream function with zero kwargs
  (`max_len_input == 0`), and mixed with a non-empty co-input it
  IndexErrors in `slice_dict`'s `v[-1]`. `ExecutionBlocker(None)`'s null
  message is what keeps the skip silent (`execution_block_cb` only errors
  on a non-None message).
- **Backend:** a real (non-virtual) node; `INPUT_TYPES` uses the flexible
  optional dict, which also carries the `toggles` STRING bridge (in `optional`,
  NOT `required` — a required input absent from a hand-built `/prompt` is
  rejected before the node runs, breaking the no-frontend API path;
  `execute`'s default covers omission). `execute(**kwargs)` collects
  present-and-enabled `image_N` in ascending N into a list — a slot is enabled
  unless `toggles` records it as the literal boolean `false` (matching the
  frontend's `!== false`); `RETURN_TYPES=("IMAGE",)`, `OUTPUT_IS_LIST=(True,)`.
  No ComfyUI imports at module scope (torch only if needed, lazy).
  `set_context` optional (not needed for M1).
- **Docs caveat:** a scalar seed downstream repeats identically across the N
  fanned runs — surface this in the node description (per-image variation
  needs an explicit seed list).

## §6.5 `EPSResolution` (display: "EPS Resolution") — M1 core

Roadmap: `research/roadmap-eps-resolution.md` (M1 = this section; grid=M2,
NAS presets=M3, list multi-image=M4 come later). NON-lora node in `eps_image/`,
category "EPSNodes". Class id `EPSResolution` frozen once shipped (§8). Owner
framing: an elegant, IMAGE-first (not latent) all-in-one resolution node — M1
is the functional core WITHOUT the grid.

- **Inputs:** `image` (IMAGE, optional — a single image for M1; list/multi is
  M4), widgets `width` (INT) and `height` (INT) (easy-to-edit target size;
  `0` on an axis = derive it from the other axis + the input image's aspect),
  plus the thin-resize controls: `resize_method` (COMBO: `stretch`,
  `keep aspect (fit)`, `crop to fill`, `pad`), `interpolation` (COMBO:
  `nearest`, `bilinear`, `bicubic`, `area`, `lanczos`), `multiple_of` (INT,
  default 0 = off).
- **Outputs (in this order):** `image` (passthrough, untouched),
  `resized_image` (the input resized to target per the controls; if no image
  is wired this is `None` — the node is then a pure size source), `width`
  (INT), `height` (INT), `original_width` (INT), `original_height` (INT). The
  passthrough + original-size outputs are the novel bit (Resolution Master
  re-emits neither). `width`/`height` report the ACTUAL dimensions of
  `resized_image` (so for `keep aspect (fit)` they are the fitted size, not the
  requested box — the fit is smaller); with no image wired they report the
  requested target (`multiple_of`-rounded), so the node still drives downstream
  size consumers standalone.
- **Hideable outputs:** implemented as two per-node **right-click Properties**
  (`Show passthrough image`, `Show original size` — both default **OFF** per
  owner ask 2026-07-20 after validating the mechanism: a fresh node shows only
  `resized_image`/`width`/`height`, and the Properties reveal the passthrough
  and original-size outputs when wanted; `attach()` applies the hidden state
  to fresh nodes, while a reloaded workflow's saved property values win via
  `configure()`), NOT a global settings group — output visibility is inherently per-node, and the JS
  file that would own a settings registration is the shared entry, not this
  node's module. NOTE: litegraph has no output-slot `hidden` flag (only widget
  INPUT slots have one), so the hide uses two mechanisms: `Show original size`
  really `removeOutput`/`addOutput`s the trailing `original_width`/
  `original_height` pair (safe because they are the TAIL of `RETURN_TYPES` —
  removing a non-tail output would repoint later wires, since ComfyUI resolves
  a link's source by positional index against the fixed backend tuple);
  `Show passthrough image` is a cosmetic draw-suppression of the leading
  `image` output's dot/label (removing it for real would corrupt the links of
  `resized_image`/`width`/`height` after it). Both refuse to hide an output
  that is currently wired (revert + toast) rather than leave a dangling wire.
- **Resize impl:** mirror core `ImageScale` semantics via
  `comfy.utils.common_upscale` (lazy `import comfy.utils`/`torch` inside the
  function, never at module scope) — thin, common-case; documented "pipe the
  width/height outputs into KJNodes' resize for anything fancier" (ethos).
  `stretch` = plain resize to WxH; `crop to fill` = `common_upscale` crop
  `"center"` (scale-to-cover + center-crop); `keep aspect (fit)` = the largest
  aspect-correct size that fits within WxH; `pad` = fit then center on a black
  (`0.0`) canvas at WxH. When `multiple_of` > 0, `stretch`/`crop`/`pad` round
  the box to the NEAREST multiple, but `keep aspect (fit)` FLOORS the fitted
  axes to the multiple so the result can never exceed the box (containment).
- **Backend-first:** M1 needs almost no custom frontend (standard widgets +
  the per-node Property toggles for hideable outputs). The canvas GRID is M2 —
  a separate, higher-risk build (dual LiteGraph/Vue rendering backends). Ship
  M1 first.
- **M2 — the size grid** (owner go 2026-07-20): an interactive 2D size pad
  INSIDE the node — the "simple, image-first" grid (anti-Resolution-Master),
  per `research/roadmap-eps-resolution.md` M2.
  - **Mechanism: a DOM widget** (a `<canvas>` element via `addDOMWidget`),
    NOT a litegraph `draw()`/`mouse()` custom widget: DOM widgets render
    under BOTH frontend backends (LiteGraph canvas and Vue nodes) with one
    implementation — the pack's proven Notebook / premiere-buttons pattern —
    which sidesteps exactly the dual-backend risk the roadmap flags for a
    canvas widget. Size it with the premiere lesson (widget
    `computeSize` + `computedHeight` + explicit element height) so it can
    never collapse to a sliver. **The pad is a FULL-WIDTH square whose size is
    driven by node WIDTH (fix 2026-07-21, supersedes the "drag taller to grow"
    model — owner reported it "grows, but awkwardly"):**
    - **No horizontal letterbox.** The square spans the node's full content
      width, locked to the left and right edges — there is NEVER empty space
      beside it. (The old centered-square-with-side-margins is gone.)
    - **Height follows width.** The pad's height == its width (a true square),
      so the DOM widget's `computeSize`/`computedHeight` report a height equal
      to the current content width; the NODE's min height is therefore
      *determined by its width*. The user resizes by dragging the node WIDER
      (bigger square, node auto-grows taller to fit) — not by dragging it
      taller.
    - **Freely shrinkable (owner bug 2026-07-21 — "once dragged taller you
      can't reduce the height").** Because height is width-derived, there is no
      independent tall state to get stuck in: narrowing the node shrinks the
      square and the node's height with it. Do NOT reintroduce any
      grow-never-shrink / `getMaxHeight → Infinity` floor-only logic — that was
      the cause of the stuck-tall bug. Round-trips through save/reload at the
      saved width.
    - **Readout text (owner bug 2026-07-21).** The two lines below the pad must
      be the SAME (small) font size — the pixel-dimension line was too large.
      The megapixels reading is RIGHT-ALIGNED on the SAME line as the
      dimensions (not wrapped onto its own second line): line 1 = `W x H`
      (left) · `N.N MP` (right-aligned); line 2 = the reduced aspect (e.g.
      `3:2`), same small size, muted.
  - **Interaction:** drag anywhere on the pad to set the target — x maps to
    `width`, y to `height`, over a 64..`Grid max` range (node property,
    default **2048** — owner ask 2026-07-20). Dragging SNAPS to `multiple_of`
    when > 0, else to 64. **Modifiers (owner ask 2026-07-20, supersedes the
    v0.15.0 "Shift = free drag"): hold Shift to constrain to a 1:1 square
    (width == height as you drag); hold Ctrl/Cmd to constrain to the aspect
    ratio the box had when THIS drag started (e.g. a 16:9 box grows/shrinks
    staying 16:9).** Snapping still applies under both modifiers. Two-way
    sync: the grid writes the `width`/`height` INT widgets (value + callback)
    and editing the numbers moves the dot. The grid never writes `0` — the
    0=derive mode stays a typed-field feature; a `0` axis renders as "auto"
    on the pad.
  - **Square cells (owner bug 2026-07-20):** the pad must map both axes at
    the SAME pixels-per-unit so a square target (e.g. 1000×1000) plots as a
    square on the true 45° diagonal and the gridline cells are square — not
    rectangles. v0.15.0 mapped x over the full (wide) width and y over the
    (short) height independently, distorting squares. Uniform scale is now
    automatic since the plot region is itself the full-width square (plotW ==
    plotH == content width): one pixels-per-unit for both axes, so the drag
    space is visually true to the numbers.
  - **Display:** current-target dot + crosshair, live `W x H` label, reduced
    aspect (e.g. 3:2) + megapixels, subtle gridlines (every 512) and a faint
    1:1 diagonal. Dark, minimal, readable on both Comfy themes.
  - **`Show grid` node property** (default on) hides it for users who only
    want the typed fields. No backend change in M2.
- **Deferred (M3–M4):** NAS presets (reuse `lora_library`
  context/sets_store/settings), multi-image list fan-out. Do NOT build them
  yet.

## §6.6 `EPSImageGrid` (display: "EPS Image Grid") — accumulate + fan out

Roadmap/research: `research/roadmap-eps-image-grid.md`, `research-eps-image-grid.md`.
NON-lora node in `eps_image/`, category "EPSNodes". Class id `EPSImageGrid`
frozen once shipped (§8). Owner decisions locked 2026-07-20 (see roadmap):
Collect/Emit toggle; copy/paste = OS clipboard + ComfyUI clipspace + Ctrl+V
add; single batch-aware IMAGE input; disk-backed, survive-restart, NO cap.

- **Inputs:** `image` (IMAGE, optional — a Run with it wired-and-present in
  Collect mode appends; batch-aware: a `[B,H,W,C]` input adds all B frames).
- **Widgets:** `mode` (COMBO `Collect`/`Emit`, default `Collect`);
  `grid_uuid` (STRING, HIDDEN — the per-node identity mirrored from
  `node.properties.uuid`, the `EPSSwitcher.toggles` serialized-hidden-widget
  trick, so the backend can key the buffer; frontend generates + dedupes it).
- **Outputs (fan-out):** `image`, `width` (INT), `height` (INT);
  `RETURN_NAMES=("image","width","height")`, `OUTPUT_IS_LIST=(True,True,True)`
  — N buffered images make the workflow run N times, each with its paired
  width/height. Each emitted image is a `[1,H,W,C]` batch-of-one in a plain
  Python list (NEVER stacked — buffered images may differ in size).
  **Empty buffer emits `[ExecutionBlocker(None)]` for each output, NOT bare
  `[]`:** a bare empty list `IndexError`s in execution.py's `slice_dict`
  (`v[-1]`) the moment it feeds a downstream node that also has any ordinary
  widget input (nearly every node), crashing the run; the blocker makes core
  silently skip the downstream branch (the same fix `EPSSwitcher` uses for
  all-off). Verified live 2026-07-20.
- **Execution model:** `OUTPUT_NODE = True` (so it runs even with nothing
  wired downstream — collect phase) + `IS_CHANGED` returns an
  always-different token (`float("nan")`) so caching never skips it → exactly
  one execution (= at most one append of the batch) per queued prompt. In
  `Collect` mode `execute()` appends the present input (if any) to the buffer
  then emits the whole buffer; in `Emit` mode it appends nothing, only emits.
  Returns the `{"ui":{"images":[…whole buffer…]}}` shape so ComfyUI core
  renders the navigable thumbnail grid + pager FOR FREE (no custom widget).
- **Buffer store (disk, survives restart):** under ComfyUI's OUTPUT dir so
  core's `/view` serves the thumbnails AND it survives restart —
  `<comfy output>/eps_image_grid/<grid_uuid>/NNNN.png` + a `manifest.json`
  (order/metadata), atomic writes (`_atomic_write_text` pattern). NOT temp
  (`cleanup_temp()` wipes it). No cap (owner decision); Clear wipes the dir.
  PNG-on-disk; decode to tensors lazily at emit.
- **Identity/dedup:** `grid_uuid` generated (`crypto.randomUUID()`) into
  `node.properties.uuid` + the hidden widget on first create; on copy/paste
  and cross-workflow load a collision check (`loadedGraphNode` + deferred
  `nodeCreated`) against live siblings mints a fresh uuid (litegraph only
  self-heals its numeric `id`). Regex-validate the uuid before any fs use.
- **Display reflects the buffer on LOAD, not only after a Run** (fix
  2026-07-20, owner-reported): `node.imgs` is not serialized, so a reloaded /
  pasted / undone node would otherwise show an EMPTY grid until the next Run
  even though the buffer is intact on disk. The frontend must FETCH the
  buffer and populate `node.imgs` on `attach`, `loadedGraphNode`, AND
  `onConfigure` (the last covers undo, which re-applies node state without
  re-running attach) — via a new `GET /eps_image_grid/list?uuid=` route
  returning `{refs: list_refs(uuid)}`. So the persistent grid is visible
  immediately, and undo/reload never appear to "lose" images (they were
  always safe on disk). (On frontend 1.45.21, undo/redo go through
  `loadGraphData` = a full node rebuild that re-fires `nodeCreated`/`attach`
  AND `loadedGraphNode`, so those two already cover undo; `onConfigure` is
  the belt-and-suspenders third site. All three firing is redundant by
  design. KNOWN churn: because that rebuild re-runs the collision dedup on
  transient historical states, an undo/redo sequence can mint fresh uuids +
  clone buffers for those transients, leaving orphan `eps_image_grid/<uuid>/`
  dirs — harmless, correctness always settles right, and folded into the M3
  orphaned-buffer-cleanup backlog.)
- **Copy carries the images, independently** (fix 2026-07-20, owner-reported
  "images didn't travel to a copy"): when the dedup mints a fresh uuid
  because of a live-sibling COLLISION (a genuine in-graph duplicate), the
  source buffer is CLONED into the new uuid's dir (`POST
  /eps_image_grid/clone {from, to}` → `store.clone_buffer`), so the duplicate
  carries its own copy of the images and the two nodes stay independent.
  (First-create — an empty/invalid uuid — mints fresh with NO clone. A
  cross-tab paste with no live sibling keeps the uuid and thus shares the
  same on-disk buffer as its source — the images still "travel"/show via the
  load-fetch above; true cross-tab buffer independence is a later
  refinement, noted not blocking.)
- **Identity is STABLE; refresh is CHEAP (fix 2026-07-21, owner-reported
  regressions from v0.19.1).** v0.19.1's remint+clone was too aggressive:
  undo/reload go through a full `loadGraphData`/`configure` rebuild that
  re-runs the dedup on TRANSIENT historical states, so a node could be
  reminted onto a freshly-cloned PARTIAL buffer — the owner saw a 13-image
  grid drop to 4, and thumbnails vanish. Rules now:
  - **Never remint/clone during a graph load/undo/configure pass** — saved
    uuids are authoritative there; a node that already holds a valid uuid
    keeps it untouched. Reminting+cloning happens ONLY for a genuine
    interactive duplicate (a real clipboard/Ctrl-C-V paste of a node whose
    original is a live sibling), detected out of any configure/load pass.
    A node's populated buffer is never silently repointed or overwritten.
  - **Refresh must be debounced/cheap**: fetch `/list` and repopulate at
    most once per load-settle, not 3× per node and not on every
    `onConfigure` fire during undo churn (that is the slow-redraw the owner
    hit). After any refresh or a paste-add, set `imageIndex = null` so the
    node shows the GRID, not a single image (the owner's "paste shows one
    image, no way back to the grid" bug), and repaint without a full tab
    round-trip.
  - The backend buffer is verified lossless at 13+ images across restart +
    repeated emit; any apparent loss is a frontend identity/refresh bug, not
    the store — fix it there, never by touching the manifest.
- **Clear:** a frontend Clear button → `POST /eps_image_grid/clear {uuid}`
  (on `PromptServer.instance.routes`, never raw `app.add_routes`).
- **Copy/paste (M2):** copy a selected grid cell to the OS clipboard
  (`canvas→toBlob('image/png')→ClipboardItem`, the shipped `copyImage`
  pattern) AND to ComfyUI clipspace (populate `node.images`/`imgs`); Ctrl+V
  on the selected node uploads the pasted image (`POST /upload/image`) and
  appends it (`POST /eps_image_grid/add`).
- **Mac / insecure-context fixes (owner reports 2026-07-21, both fail ONLY on
  his Mac; ComfyUI runs on his Windows PC and the Mac views it over
  `http://<pc-ip>` — plain http, so `window.isSecureContext === false`
  there):**
  - **"Copy Image" missing on the Mac (only "Copy (Clipspace)" shows).** Core's
    OS-clipboard copy uses `navigator.clipboard.write([ClipboardItem])`, which
    is `[SecureContext]`-gated — `ClipboardItem` is `undefined` on insecure
    origins, so core omits its own menu item; "Copy (Clipspace)" survives
    because it never touches the Clipboard API. This is a BROWSER security
    boundary, not fixable — you cannot put a binary image on the OS clipboard
    from insecure http. The node adds its OWN "Copy image" item that does the
    real OS copy when a secure context exists, and otherwise DEGRADES: copies
    the image's `/view` URL as text (`execCommand('copy')`, no secure-context
    requirement) + opens it in a new tab (for the browser's native Copy Image)
    + a toast explaining. "Copy (Clipspace)" stays the identical-everywhere
    path. The wrap of `getExtraMenuOptions` is per-instance (guard flag), the
    same idiom as `installPasteFiles`.
  - **Undo "clears" the grid on the Mac (fine on the PC).** `setNodeImagesFromRefs`
    sets each `Image.src` and every caller repaints ONCE, immediately —
    before the `/view` images finish loading over the LAN. Litegraph only
    repaints a layer whose dirty flag is set, so an image that arrives after
    that one repaint never shows until something else dirties the canvas. Fix
    = litegraph's own `loadImage()` idiom: each image's `onload` calls
    `setDirtyCanvas(true, true)`, so the slowest image still gets a final
    correct paint. Correct for any client; only a slow/remote one shows the bug.
- **Milestones:** M1 = collect/emit + buffer + fan-out + free grid + Clear +
  identity/dedup; M2 = copy/paste (both targets) + Ctrl+V add; M3 = buffer
  management (per-image remove, count, reorder, batch-count guard). No cap in
  v1. No module-scope torch/ComfyUI import (lazy inside functions).

## §6.7 `EPSFrameSaver` (display: "EPS Frame Saver") — video frame picker

Owner ask 2026-07-21 ("a load-video node; pick a frame by play/pause/step/
type; output that frame + w/h; see total frames"). Research:
`research/` feasibility scan 2026-07-21. NON-lora node in `eps_image/`,
category "EPSNodes". Class id `EPSFrameSaver` frozen once shipped (§8).
Owner decisions locked: **PATH source (Browse a file, NEVER copy to input)**;
single-frame output (NOT a list); "close-enough preview, EXACT on output".

- **Inputs/widgets:** `video_path` STRING (the video file, chosen via a
  Browse dialog reusing the pack's `fs/list` fs-browse standard with a VIDEO
  ext allowlist — `.mp4,.mov,.webm,.mkv,.avi,...`); `frame` INT (the selected
  frame index, default 0, driven by the player + serialized so it round-trips
  and reaches `execute()`). Host-only Browse (hidden on a remote browser, like
  the notebook/premiere pickers). No IMAGE input.
- **Outputs:** `image` (IMAGE, a single `[1,H,W,C]` float32 0..1),
  `width` (INT), `height` (INT). **NO `OUTPUT_IS_LIST`** — plain single
  outputs (matches `PremiereShotFrame`). Multi-frame = a future sibling node.
- **Backend extract:** lazy `import av`/`torch` inside the function (av is a
  hard ComfyUI dep, but keep the module import-clean per pack convention);
  MIRROR the sibling `comfyui-premiere-bridge/cprb/frame_extract.py` recipe
  (Eric's own MIT: `frame_index/fps → target_seconds`, `container.seek`
  keyframe + decode-forward to the first frame at/after target, keep the last
  frame if the stream ends; `frame.to_ndarray('rgb24')`/255 → `[1,H,W,C]`).
  A missing/unreadable path → clean `ValueError` naming the path, never a raw
  ffmpeg traceback. width/height from `stream.width/height`.
- **Probe route** `GET /eps_frame_saver/probe?path=` → `{fps, frame_count,
  width, height, duration}` — loopback-only + path-validated (the pack's
  `is_local` gate + ext allowlist, NOT VHS's permissive default). Frame count
  via the trustworthy cascade (mirror ComfyUI core `video_types.py`:
  `stream.frames>0` → `duration*rate` estimate → decode-count last resort);
  fps via `stream.average_rate`. The frontend calls this on path-set to drive
  the counter + frame-input bounds.
- **Stream route** `GET /eps_frame_saver/stream?path=` → `web.FileResponse(
  path)` (aiohttp gives Range/206 seek support for free → smooth `<video>`
  scrubbing on browser-native codecs), loopback-only + path-validated. This
  is the `<video>` element's `src`. Exotic codecs the browser can't decode →
  the player degrades to backend-extracted still-frame scrubbing (a frame
  route or reuse the probe/extract path); the OUTPUT extraction is exact
  regardless. Both routes on `PromptServer.instance.routes`.
- **Frontend player** (DOM widget, notebook/premiere fill-height + fixed-bar
  sizing patterns): a `<video>` for smooth play/pause/loop preview + a control
  strip — step −/＋ (currentTime ± 1/fps), play/pause, a bounded `frame`
  number input, and a live "Frame X / N" counter — all driven by
  `currentTime = frame/fps` against the PROBED fps/frame_count (the LNL /
  core / cprb split: preview approximate, run-time extraction authoritative).
  Selecting/stepping writes the `frame` INT widget. Clean-room — do NOT copy
  GPL VHS/LNL code (this pack is MIT); reimplement the pattern.
- **Deferred:** multi-frame (sibling node), transcode-for-exotic-codecs,
  in/out range. VFR sources: the frame↔time arithmetic is approximate for the
  counter (shared limitation of all prior art); output still lands on a real
  frame. No module-scope torch/av/ComfyUI import.

## §7 Frontend surfaces

- **§7.1 Extension entry** `web/lora_library.js`: exactly one
  `app.registerExtension` call named `lora_library.LoraLibrary`; every
  sub-feature module is wrapped so one failure never blocks the others
  (cpsb pattern). About-panel badge links the GitHub repo and shows the
  frontend version.
- **§7.2 Notebook widget**: two panes inside the node via `addDOMWidget` —
  left: scrolling entry list (click = select → loads text right; shows
  category grouping when present) + `＋ New` / `🗑 Delete`; right: plain
  `<textarea>` + `Save` (disabled until dirty) + a muted status line
  (conflicts per §3.5 surface here with Reload / Overwrite). The node is
  resizable; the widget fills available height. Selection writes the
  `entry` STRING widget so serialization needs no custom code.
  - Multi-select: ctrl/cmd+click toggles an entry in/out of the selection;
    shift+click selects the visible range; plain click collapses to a
    single selection. All selected rows highlight; the EDITOR always shows
    the most recently clicked entry (the "active" one) — editing/saving
    touches only it. The `entry` widget holds the §6.1 newline-joined list.
  - New-below (owner ask 2026-07-19): `＋ New` inserts the new entry
    directly below the ACTIVE entry (via the §5 entry route's `after`),
    in that entry's category — not at end-of-file. With nothing selected it
    appends as before.
  - Rename via the editor header (owner ask 2026-07-19): the editor pane
    has a NAME field at its top showing the active item's name; editing it
    and Saving renames (entries via the entry route's `rename_to`;
    categories via a category-rename — see §6.3 note / add a `rename_to`
    to the category route). This is the PRIMARY rename path because the
    old double-click-inline rename was reported not working; double-click
    now simply focuses this field. Duplicate names refused client-side
    first, server authoritative.
  - Delete removes EVERY selected entry (owner amendment 2026-07-18c): the
    confirm label shows the count when >1 ("Are you sure? (3)"); deletion
    is sequential client-side over the §5 delete route, refreshing
    `base_mtime` from each response; a mid-sequence conflict stops the run
    and surfaces the standard §3.5 conflict UI.
  - Rename: double-click an entry row to edit its name inline (Enter/✓
    commits via the §5 entry route's `rename_to`, Esc cancels; duplicate
    names are refused client-side first, server remains the authority).
  - Categories in the UI (owner ask 2026-07-19): `＋ New` with a name
    STARTING WITH `#` creates a category instead of an entry (the `#` and
    surrounding whitespace are stripped from the stored name). Category
    headers are CLICKABLE and do TWO things at once: (1) toggle
    collapse/expand of that category's entries in the left list (owner ask
    "single tap category name to collapse category"; collapse state is
    UI-only, per-browser, not written to the file), and (2) make the
    category active so the editor pane shows its §3.1 description. Save
    writes the description (and rename via the header name field) through
    the §5 category route — the editor is contextual (entry active ⇒ body;
    category active ⇒ description; the mode hint says which). Category
    selection is UI-only: it never touches the `entry` widget, the entry
    selection set, or the node's outputs. Empty categories render from the
    §5 `categories` list.
  - Multi-select drag into a category (owner ask 2026-07-19): when 2+
    entries are selected, dragging any one of them moves the WHOLE
    selection to the drop target, in selection order (one §5 `/move` per
    entry, or a batch — implementer's choice, but base_mtime is refreshed
    between each so the run doesn't self-conflict).
  - Drag a category header to move the whole category and its entries
    (§3.4 Move category, §5 `/move_category`), with the same insertion
    marker as entry drag.
  - File panel (owner amendment 2026-07-18c, reworked 2026-07-19): the
    panel IS the file control — the raw `file` STRING widget is HIDDEN
    (kept only as the serialized value the node reads; §6.1) and the panel
    replaces it. It shows the RESOLVED absolute path FULL-WIDTH (owner ask:
    "make this full width so it doesn't need to be trimmed"); front-trim
    (keeping the filename) only when the path genuinely overflows the bar,
    full path in tooltip. `Browse…` (picker over §5 `fs/list`, now with
    drive/UNC navigation and a type-a-path input — §5) and `Open folder`
    (§5 `open_folder`). When §5 config reports `is_local: false`, both
    buttons hide, the path becomes read-only, AND the host-machine notice
    ("the host controls which file this node reads") shows on ITS OWN line
    only then (owner ask: separate line, only when needed) — never inline,
    never on load when local.
  - Browse picker: a "type or paste a path" input (accepts any absolute
    path incl. UNC `\\server\share`, applied on Enter/Go), a `..` row that
    climbs to the drive list at a drive root (via §5 `dir="ROOTS"`), and
    the drive list itself — so a NAS/other-drive target is always
    reachable (the 2026-07-19 "couldn't leave C:\" fix).
  - Drag-reorder: rows drag within the list with a visible insertion
    marker; dropping emits one §5 `/notebook/move` (before = the row below
    the marker, or `category` append when dropped at a category's end/on
    its header). §3.5 conflicts surface exactly like Save conflicts.
- **§7.3 Settings**: an "EPSNodes" settings section shows backend +
  frontend versions (mismatch ⇒ "pulled but not restarted" hint, cpsb
  pattern) and the `library_dir`. Local browser: editable → `POST /config`.
  **Remote browser (`is_local:false`): the field is genuinely READ-ONLY**
  (owner report 2026-07-19: the prior revert-on-edit fired an error toast
  on EVERY keystroke). Implement read-only robustly for a ComfyUI text
  setting — if the settings API exposes no disabled state, do NOT let
  `onChange` POST or toast at all when remote; silently restore the
  server value with zero user-facing noise. A single calm caption ("The
  library folder is set on the machine ComfyUI runs on.") shown once, not
  per keystroke.
  - **Server-can't-see-the-folder surfacing (owner report 2026-07-19, the
    "NAS .md not found" case).** The settings section reads §5 config's
    `library_dir_exists`/`library_dir_note`: when the SERVER can't resolve
    the configured folder, show a persistent WARNING line with the note
    (e.g. "The server machine can't reach this folder — it looks like a
    macOS path but ComfyUI is running on Windows" or "…can't reach this
    folder right now; is the NAS mounted on the server?"). This turns the
    silent "file not found" at node-run time into an at-a-glance Settings
    warning. The notebook node's own file-not-found error ALSO names the
    RESOLVED ABSOLUTE path it tried (§6.1) so the mismatch is obvious.
- **§7.4 Combo freshness**: after any set CRUD the frontend refreshes every
  `LoraLibraryApplySet` node's `set` combo options in place (no page
  reload). Server-side `VALIDATE_INPUTS` already accepts values the combo
  hasn't seen yet.

## §8 Versioning & stability

- Backend version: `lora_library/version.py` (source of truth); frontend:
  `web/lora_library/version.js`; package: `pyproject.toml`. Kept in
  lockstep by `scripts/bump_version.py`; **every push bumps at least the
  patch version and is tagged `vX.Y.Z`** (docs-only changes do not bump —
  version = code-sync signal).
- FROZEN once shipped: node class ids, route paths, the §3 grammar's
  meaning of existing files, and §4 `format: 1` field semantics. New
  capabilities add fields/routes; they do not repurpose old ones.
