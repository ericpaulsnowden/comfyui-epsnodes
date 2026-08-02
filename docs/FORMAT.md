# FORMAT.md — the binding contract for EPSNodes

Naming (2026-07-18 rebrand, refined same day): the PACK is **EPSNodes**
everywhere a user sees it (node-browser category, Settings section, About
badge); the REPO/install folder is **`comfyui-epsnodes`**, matching the
sibling plugins' `comfyui-*` convention (owner decision). The python
module `lora_library/`, the `/lora_library/*` route prefix, and the
`LoraLibrary*` node class ids are the pack's first FEATURE FAMILY and stay
frozen (§8) — future non-lora features arrive as sibling modules under the
same EPSNodes banner, without repo churn. `LoraLibraryNotebook`'s display
name is **"EPS Prompt Notebook"** (it stores prompts; the original name was a
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
  paths outside `library_dir` **and outside the host's `remote_dirs`
  allow-list** with `403 {"error": ...}` naming this section. Everything
  inside those still works — a remote browser tab driving a shared library is
  legitimate.
- **`remote_dirs` — the allow-list, and why it has to exist** (owner report
  2026-07-29). A notebook on a NAS mount
  (`/run/user/1000/gvfs/smb-share:…/docs/loras.md`) worked from the Linux box
  running ComfyUI and 403'd from his Mac. Two rules that were each correct
  collided: §1 explicitly blesses absolute NAS paths as "the design center,
  not an edge case", and the rule above confines remote callers to
  `library_dir` — so the node was unusable from any second machine.
  - The reconciliation is a host-owned list of extra folders, in `config.json`
    alongside `library_dir`, written ONLY through the loopback-only
    `POST /lora_library/remote_dirs`. §7.2's "Share this folder with remote
    browsers" toggle is its one UI, and it appears only for a LOCAL viewer.
  - **Why not simply let remote reads through.** The `file` value arrives in
    the request, and the workflow that "chose" it lives in the browser — so
    the server cannot distinguish a path the host's workflow configured from
    one a caller invented. Permitting an arbitrary remote `file` is therefore
    an arbitrary-file read on the host, which is the one thing this section
    exists to prevent. Only a host-side list can tell the two apart.
  - **Containment rules** (each pinned in `tests/test_routes_notebook.py`):
    both sides fully resolved, so `..` traversal escapes and — importantly —
    a **symlink planted inside a shared folder resolves to its real target and
    is refused**. That is the safe direction, and it is also why "symlink the
    NAS file into the library folder" is NOT a workaround for this guard.
    Matching is by path SEGMENT, so `/nas/docs` never grants `/nas/docs-private`,
    and never grants the parent. A listed folder is never created (`mkdir`
    would shadow a real mount point while its NAS is offline), and an
    unresolvable entry simply doesn't match instead of failing the check.
  - **What changed to surface this**: nothing loosened — this confinement has
    been in place since the first commit. Before v0.35.8 a remote browser
    never actually REQUESTED the workflow's saved absolute path (litegraph
    restores `widgets_values` after attach and nothing re-read the file), so
    it silently loaded the backend DEFAULT notebook inside `library_dir` and
    looked like it worked. v0.35.8's `wireConfigureReload` made the node
    honour its saved path, which exposed a wall that had always been there.
    General lesson: **fixing "the node ignores its saved state" can surface
    permission errors that the bug was masking** — when a state-restore fix
    ships, re-check the remote/gated paths it now genuinely exercises.
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
  be represented — it would read back as a boundary. **Since v0.48.1 saves
  containing one succeed anyway: the writer DEMOTES each such line by two
  levels** (`# X` → `### X`, `## X` → `#### X`, bare `#`/`##` likewise,
  leading indentation kept), landing it in the H3+ range §3.1 defines as
  body text while keeping every heading a heading and h1-vs-h2 hierarchy
  intact. (Until v0.48.1 these saves were refused with a 400 — the owner
  hit that constantly pasting LLM output, 2026-08-02, and a refused save
  reads as "it just fails".) The save response reports
  `adjusted_headings` (0 on the ordinary save) and, only when non-zero,
  echoes the STORED `text` so the editor can fold disk truth in; fenced
  heading-looking lines are body text already and are never touched.
- **Create category** appends a new `# Name` heading at end-of-file (name
  must be unique among categories, non-empty after trimming, no newlines);
  **Set category description** replaces the §3.1 description block under
  an existing heading (same demote-two-levels rule as entry bodies for a
  description line starting with `# `/`## ` outside a fence, reported as
  `adjusted_headings` + a stored-`description` echo on the category
  route's response).
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
| `POST /lora_library/remote_dirs` `{"dir","allow"?}` | §2 remote allow-list: add (`allow` omitted/true) or remove (`false`) one absolute folder non-loopback callers may also touch. **LOOPBACK-ONLY** (403 otherwise) — this list IS part of the boundary §2 enforces, so a remote caller able to extend it could grant itself the arbitrary-file read that boundary denies. Non-absolute / `scheme://` / non-bool `allow` ⇒ 400. Adding a folder already covered is a no-op. → `{"ok","remote_dirs"}` |
| `GET /lora_library/config` | `{"library_dir", "default_library_dir", "configured": bool, "is_local": bool, "library_dir_exists": bool, "library_dir_note": str}` — `is_local` = §2 loopback verdict for THIS request (drives §7.2's remote read-only gating). `library_dir_exists` = whether the SERVER can see the configured folder right now — the owner's 2026-07-19 NAS confusion was a library path the server machine couldn't resolve, invisible until a node errored. `library_dir_note` = "" when fine, else a one-line human diagnosis chosen server-side: unreachable path, or a path whose SHAPE doesn't match the server's OS (e.g. `/Volumes/…` configured while the server is Windows, or `C:\`/UNC while it's POSIX — a strong sign it was set from the wrong machine's perspective). `remote_dirs` = the §2 allow-list, driving §7.2's host-only share toggle |
| `GET /lora_library/fs/list?dir=` | **loopback-only** (403 remote): server-filesystem browser for the §7.2 picker. Empty/missing `dir` ⇒ `library_dir`. `dir="ROOTS"` (sentinel) ⇒ the top level: the library folder (labeled) + "Home" always, then a platform tail — every existing drive on Windows (`C:\`, `D:\`, `U:\`, …); every `/Volumes` mount on macOS; or on Linux **every filesystem the OS reports mounted** (read from `/proc/self/mounts`, network shares first and labeled — a GVFS share becomes `<share> on <server>`, everything else `<folder> (<fstype>)`), then the conventional mount parents that exist (`/`, `/mnt`, `/media`, `/media/<user>`, `/run/media/<user>`, `/srv`, `/run/user/<uid>/gvfs`) as a fallback for browsing somewhere not yet mounted. Pseudo filesystems, snap/container/EFI subtrees, `/` itself (offered as "Filesystem root") and anything this process cannot list are filtered out. **2026-07-26**: the POSIX tail used to read `/Volumes` ONLY, which does not exist on Linux, so a Linux user's ROOTS collapsed to library+Home; then reading the mount table replaced guessing at parents — owner: "How do I get to my shared drives? Mounting can't be the answer." Reading the table means an already-mounted share is offered **wherever it lives**, with no path knowledge required. Entries are de-duplicated by path, and **every** root entry (including Home) is dropped when the server can't actually list it — his `HOME=/root` on a non-root process made "Home" a guaranteed `could not list /root: Permission denied` 400. → `{"dir": <abs or "ROOTS">, "parent": <abs, "ROOTS", or null>, "dirs": [names], "files": [names]}` — `files` limited to `.md`; entries sorted case-insensitively; a directory at a drive root reports `parent: "ROOTS"` so the picker can climb to the drive list (the 2026-07-19 "stuck at top of C:\, can't reach another drive/NAS" fix); a UNC path (`\\server\share\…`) passed as `dir` lists normally; unreadable/nonexistent dir ⇒ 400; a `dir` containing `://` (a typed `smb://`/`nfs://`/… address) ⇒ 400 whose message says in plain language that it is a network address, not a file path, and names a mount point to use instead (2026-07-26) |
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

### §6.1 `LoraLibraryNotebook` (display: "EPS Prompt Notebook")

- **A `scheme://` `file` value is REJECTED, loudly (2026-07-26 fix).**
  `Path("smb://host/share/x.md")` collapses the double slash to
  `smb:/host/share/x.md`, which is NOT absolute on POSIX — so a typed
  network address used to fall through to the relative branch and get
  joined UNDER the library folder. It failed in the worst way: the node
  reported a missing file (indistinguishable from an empty notebook) and
  the first SAVE would `mkdir -p` a bogus `smb:/host/…` tree inside the
  user's real library folder (`:` is a legal POSIX filename character).
  `LibraryContext.resolve_notebook_file` now raises `ValueError` naming
  the caller's own scheme, which `routes_notebook._resolve_path` turns
  into a 400 and the node surfaces at queue time. A NAS is reached by its
  MOUNT POINT (`/mnt/nas/loras.md`, `/Volumes/share/loras.md`,
  `/run/user/<uid>/gvfs/smb-share:server=…`), never by its URL.

- **List scroll position is preserved across re-renders (2026-07-24 owner
  fix):** `renderList()` rebuilds the left column on every selection,
  save, poll refresh, drag, and collapse — the rebuild now restores the
  prior `scrollTop` instead of resetting to the top.
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

### §6.2 `LoraLibraryApplySet` (display: "EPS Apply LoRA Set")

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
- **Sync target of the controller's Push State (§6.3).** EPS Apply LoRA Set
  needs no structural change for this: the controller's Push State button
  sets each EPS Apply LoRA Set node's `set` widget to a chosen state and
  triggers a re-read. So one controller can keep any number of EPS Apply LoRA Set nodes on the same state at once — the owner's "multiple EPS Apply LoRA Set nodes all controlled by one controller, kept in sync" use case.
- **`mirrors loader` tag (owner ask 2026-07-19c: "set different EPS Apply LoRA Set nodes to different Power Lora Loaders as targets").** A FRONTEND-ONLY
  combo widget (added by `sets.js` on nodeCreated; the server never sees
  it) listing the graph's PLL nodes plus `"(any)"` (default). It's a
  GROUPING TAG for §6.3's selective Push — it does not change what the
  node executes (states still come from the file). Serialized with the
  node (appended after the server widgets — appended-last consistently so
  positional restore stays aligned); stores the tagged PLL's node id,
  displayed by title; tolerates the id disappearing (falls back to
  "(any)").

### §6.3 `EPS Lora Loader State Controller` (frontend-only virtual node)

**Three separate things have to say "EPS Lora Loader State Controller", and
missing any one of them makes the node look unrenamed.** Reported twice
(2026-07-22, again 2026-07-27) before all three were covered:

1. **The canvas title** — `static title` on the class. Always worked.
2. **The node library / search entry** — `app.ts`'s `registerNodes`
   synthesizes a def for every frontend-registered litegraph type with
   `display_name` HARDCODED to the registration name; it reads the class's
   `category` and `description` statics but never its `title`. The fix is the
   `beforeRegisterVueAppNodeDefs` extension hook, which `registerNodes`
   invokes on the def ARRAY *between* synthesis and
   `nodeDefStore.updateNodeDefs`. Patching the store afterwards (the first
   attempt) only reaches what hasn't already been derived from the def —
   which is why the search index agreed while the visible name did not.
3. **A title baked into an ALREADY-SAVED workflow** — the one that actually
   kept the bug alive. `configure()` restores `info.title` verbatim, so a
   graph saved back when the title *was* the class id carries
   `"title": "LoraLibrarySetController"` forever, and no amount of fixing the
   registration can reach it. Fresh nodes were always fine (they serialize no
   `title` at all), which is exactly why this looked fixed from the dev rig
   and stayed broken on the owner's machine. The class's `configure()`
   override rewrites that ONE exact stale string, leaving any deliberate
   rename alone.

Naming (owner 2026-07-18c, refined 2026-07-19): the node's DISPLAY name is
**"EPS Lora Loader State Controller"** (was "Power Lora Loader State
Controller" — "Power" dropped) and every user-facing word in its UI says
**state**, not set — widget label `state`, buttons in this order (Delete
last — owner ask 2026-07-22): `New State` (capture current rows as a new
state — the ONLY create path), `Save State` (overwrite the selected state
with current rows; a changed `name` field renames it IN PLACE, slug
unchanged — see below), `Push State` (broadcast — below), and
`Delete State` (two-click confirm). The class id `LoraLibrarySetController` stays frozen
(§8), and states ARE §4 set files — same storage, same routes, same files
the EPS Apply LoRA Set node reads; only the controller's vocabulary changes.

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
  `name` text field for New/Save stays. **Save State renames IN PLACE
  (REVERSED 2026-07-22, owner bug report: "selecting a state, changing the
  name of a state, and clicking save will create a new entry" — supersedes
  the 2026-07-21 save-as-new interpretation):** Save State ALWAYS writes to
  the selected state's own slug (`POST /lora_library/set` slug-form,
  `{slug, set}`); a non-empty `name` field that differs from the selected
  state's name rides along as `set.name` — a rename-in-place. The slug
  NEVER changes (sets_store.save_set's caller-supplied-slug contract), so
  EPS Apply LoRA Set nodes referencing the state by slug keep working.
  Renaming to another state's display name is allowed (slugs stay unique;
  the list's dedup "(slug)" suffix disambiguates). `New State` (empty name
  → capture current rows) is the ONLY create path. The selected
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
  auto-selects when exactly one exists). Buttons (stacked in the RIGHT
  pane, Delete LAST — owner ask 2026-07-22): `New State`, `Save State`,
  `Push State` (broadcast to all EPS Apply LoRA Set nodes), `Delete State`
  (two-click "Are you sure?" confirm; the armed button is visually
  distinct, survives background cache refreshes for its full window, and
  selection is slug-anchored so a mid-window sets-poll cannot invalidate
  it — the 2026-07-18 "delete does nothing during a running workflow"
  bug). All existing behavior — apply-on-select, composite
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
  N runs) via core list execution. A list-producing upstream (e.g. EPS
  Image Grid, itself `OUTPUT_IS_LIST`) merges element-wise into that count
  instead of counting as one image — the node also declares
  `INPUT_IS_LIST = True` so core merges correctly instead of silently
  re-running EPSSwitcher once per upstream element with every OTHER input
  broadcast-repeated (the shipped bug this fixed 2026-07-22: a disabled
  Image Grid input still forced the downstream branch to run once per grid
  element, duplicating the one enabled image). Disabled inputs are omitted
  from the list AND their upstreams never execute: every `image_N` is
  `lazy`, and `check_lazy_status` only requests enabled, connected slots,
  so a toggled-off branch is skipped before it runs, not filtered out
  after (supersedes the M1 "their upstreams still execute" limitation and
  closes the M3 lazy-skip backlog item). KNOWN CORE SEMANTIC (execution.py
  `_async_map_node_over_list`, list-input blocker scan): if any ENABLED
  input's resolved list contains an `ExecutionBlocker` element — e.g. an
  enabled EPS Image Grid that is EMPTY with nothing wired into it — core
  blocks the ENTIRE switcher before it runs, silently skipping the whole
  branch (queue still succeeds). Toggle an empty grid off (its branch then
  never even executes) or collect into it first.
- **A slot fed by a provably-empty SIBLING switcher is never requested**
  (2026-07-26 owner report: "if one of them has all of the inputs unchecked
  the entire workflow won't run. Even if it's earlier in the workflow than
  the second one" — reproduced exactly). Because of the core semantic
  above, an all-off switcher's blocker vetoed a DOWNSTREAM switcher that had
  its own perfectly good enabled image. The fix lives on the CONSUMER and
  works through laziness: `check_lazy_status` reads the graph (hidden
  `PROMPT`/`UNIQUE_ID` inputs) and, for any `image_N` whose upstream is an
  `EPSSwitcher` that provably emits nothing — every wired `image_N` of it
  `false` in its own literal `toggles`, or nothing wired at all — does NOT
  request that slot. The upstream then never runs, so no blocker is ever
  created and this node's other enabled inputs are untouched. Unknowable
  cases (a `toggles` arriving as a LINK, a non-switcher upstream, an
  uninspectable graph) request as before. **Limitation:** only a DIRECT
  upstream switcher is detected — a blocker arriving through an intermediate
  node still blocks, as does an ordinary consumer like `ImageBatch` that
  genuinely needs every input.
- **NEVER make an output depend on the graph** (learned the hard way,
  2026-07-26): the first attempt at the above had the all-off switcher emit
  a bare `[]` when it could see that all its consumers tolerate one.
  Unsound — a node's cache key is built from its INPUTS only, and
  `IsChangedCache` even calls `get_input_data` with `dynprompt=None` ("We
  only want constants in IS_CHANGED"), so a graph-derived decision can never
  participate in it. Proven live: the cached `[]` from a graph where it was
  safe got replayed into one where it wasn't, and `SaveImage` died with an
  `IndexError` in `slice_dict`. Graph inspection may only influence which
  inputs a node REQUESTS (laziness), never what it RETURNS.
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
  `execute`'s default covers omission). `INPUT_IS_LIST = True`: every input
  — `toggles` included — arrives wrapped in a list; `execute` unwraps
  `toggles` and, for each present-and-enabled `image_N` in ascending N,
  extends the output with that slot's list elements (one level of
  flattening, so a list-producing upstream merges element-wise) — a slot is
  enabled unless `toggles` records it as the literal boolean `false`
  (matching the frontend's `!== false`). Every `image_N`, including
  dynamically-grown ones, also carries `lazy: True`; `check_lazy_status`
  returns only enabled, connected slot names, so a toggled-off branch's
  upstream is never requested and never runs. `toggles` itself stays
  non-lazy — `check_lazy_status` needs it immediately to decide.
  `RETURN_TYPES=("IMAGE",)`, `OUTPUT_IS_LIST=(True,)`. No ComfyUI imports at
  module scope (torch only if needed, lazy). `set_context` optional (not
  needed for M1).
- **Docs caveat:** a scalar seed downstream repeats identically across the N
  fanned runs — surface this in the node description (per-image variation
  needs an explicit seed list).

## §6.4b `EPSModelSwitcher` / `EPSClipSwitcher` / `EPSVaeSwitcher` (v0.44.0)

Three sibling classes of §6.4's `EPSSwitcher`, built by the SAME factory
(`nodes_switcher.py`'s `_make_switcher_ns`) and attached by the SAME frontend
(`switcher.js`'s `SWITCHER_CLASSES` registry): every §6.4 contract — growing
`<prefix>_N` optional inputs resolved BY NAME through the flexible-dict
`INPUT_TYPES` proxy, the hidden `toggles` JSON bridge (`{"<prefix>_N":
false}`, absent = enabled, only literal `false` disables), per-slot `lazy`
inputs whose toggled-off upstream branch never executes,
`INPUT_IS_LIST`/`OUTPUT_IS_LIST` one-level flatten, and the all-off/none-
connected `[ExecutionBlocker(None)]` success path — applies verbatim, with
only the prefix and IO type substituted:

| class | display | inputs | type | output |
| --- | --- | --- | --- | --- |
| `EPSModelSwitcher` | EPS Model Switcher | `model_N` | MODEL | `models` |
| `EPSClipSwitcher` | EPS CLIP Switcher | `clip_N` | CLIP | `clips` |
| `EPSVaeSwitcher` | EPS VAE Switcher | `vae_N` | VAE | `vaes` |

- **`EPSSwitcher` itself is untouched** — same class id, `image_N` names,
  behavior byte-identical (its pre-refactor class attributes and
  `INPUT_TYPES()` output were diffed against the factory's, not just
  test-equivalent; `tests/test_switcher.py` passes unmodified). §8's freeze
  holds: the factory is an internal reorganization, not a contract change.
- **The empty-sibling consumer skip (§6.4) is cross-type.** The
  statically-all-off recognition now keys off a class-id → slot-pattern
  registry (`_SWITCHER_SLOT_PATTERNS`) covering all four classes, each
  scanned with the UPSTREAM's own prefix — so e.g. a `vae_N` slot fed by an
  all-off `EPSModelSwitcher` is still skipped, pinned by a cross-class test.
- **Pass-through safety**: these nodes only select and return; they never
  mutate. That matters because ComfyUI caches node outputs BY REFERENCE
  (verified against execution/caching source, 2026-08-01) — a MODEL/CLIP/VAE
  handed downstream is the upstream loader's own cached object. Anything
  that needs to modify one must follow that type's own clone convention
  (MODEL/CLIP `.clone()`; VAE has none) — never this node's concern.
- **The zip trap still applies ACROSS switchers** (§6.9's founding bug): two
  switchers wired into one sampler pair index-by-index (3 models × 2 VAEs =
  3 runs, not 6). One switcher = one axis; crossing axes needs §6.9/§6.10.
  README documents this loudly for users.
- Live-verified on the rig (2026-08-01): typed socket growth per class,
  litegraph refusing MODEL→IMAGE while accepting MODEL→KSampler,
  save/reload keeping wired sockets + one spare + prefixed toggles, and —
  with a REAL `CheckpointLoaderSimple` wired upstream of a toggled-off
  `model_1` pointing at a deliberately-invalid placeholder file — a
  successful queue, proving the lazy skip (the loader would have raised).

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
  "Wired" means what the frontend itself means by it — `LGraphCanvas`'s
  `hasRelevantOutputLinks` unions `output.links` (settled) with
  `output._floatingLinks` (a link still mid-drag), and the check here does the
  same (fixed v0.34.0; it was `.links`-only before, which let a mid-drag link
  read as unconnected — so `Show original size` could `removeOutput` the
  socket out from under it). §6.11 carries the identical function; the two are
  kept in lockstep, with one headless case list each.
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
    - **Readout is ONE line (owner asks 2026-07-21).** All at the same small
      font size (the pixel-dimension line was too large): `W x H` (left,
      strong) then the reduced aspect (e.g. `3:2`) immediately BESIDE it (muted
      — owner: "the ratio should be next to the pixel dimensions, not below
      them"), and `N.N MP` right-aligned on that same line. No second line;
      `TEXT_STRIP_H` shrank to match (which also shortens the node slightly).
    - **Crosshair stops AT the dot (owner ask 2026-07-21).** The target
      crosshair is drawn only from the origin edges (top + left; origin =
      smallest size, since `mapX`/`mapY` grow right/down) TO the dot — never
      past it. The segments that used to continue to the right of the dot and
      below it are hidden: those are sizes LARGER than the chosen W/H, outside
      the image rectangle the user is defining. What remains traces the right +
      bottom edges of that rectangle, meeting at the dot (its far corner).
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
  - **Incoming-image line (2026-07-29, owner ask — "show the width/height/
    ratio of the incoming image (if the input is hooked up) at the bottom of
    the panel in addition to the info for the grid. Display in a similar
    format"):** a SECOND readout line under the target one, muted and
    prefixed `in`, in the identical shape — dims, reduced aspect, megapixels
    right-aligned. `getSourceReadoutLine` reuses the target line's own
    `formatAspect`/`formatMegapixels`, so the two can never drift into
    different formats (pinned by a test comparing both for equal dims).
    - **Where the number comes from:** the upstream node's already-displayed
      image element, one hop via `getInputNode` →
      `imgs[imageIndex ?? 0].naturalWidth/Height`. That is the SOURCE
      resolution (not the on-canvas thumbnail), and it is live BEFORE any
      Run — which is the point, since choosing a target size is what you do
      first. Deliberately shallow: one hop is the real wiring, and a wrong
      number would be worse than none.
    - **Nothing to show ⇒ nothing drawn, and the strip stays ONE line.**
      `hasSourceLine` gates the draw AND both height functions
      (`computeGridWidgetHeight`/`computeGridElementHeight` gained a
      defaulted `withSourceLine`, so every pre-existing geometry test still
      passes untouched and an unconnected node is pixel-identical to before).
      Height and draw asking the same question is what keeps line 2 from
      rendering into clipped space — the exact failure mode §6.5's own
      "second line is cut off" fix already burned once.
    - **Two timing hazards, both closed:** `onConnectionsChange` (wrapped,
      never replaced) flips the line as the input is wired/unwired; and
      because a freshly-wired upstream is usually still DECODING then
      (`naturalWidth` reads 0), a self-cancelling probe re-checks for up to
      3s and repaints the moment a size resolves. The probe is the only
      timer this widget owns and is cleared on node removal.
      NOTE `node.imgs` is populated by core's `updatePreviews` during a
      DRAW, so this is invisible to state-only probes in a headless pane —
      verified live instead: connect grew the node 510 → 525 and painted
      `in 768 x 768 1:1 0.59 MP` on its own baseline; disconnect returned it
      to 510.
- **M3 — server-side size presets, backend half (v0.47.0).** Named bundles
  of the five fields in ONE JSON file, `resolution_presets.json`, directly
  inside `context.library_dir()` (no subdirectory — one file, unlike
  `sets/`): `{"format": 1, "presets": {"<name>": {width, height,
  resize_method, interpolation, multiple_of}}}`. Same shared folder as the
  Notebook's file and LoRA sets, so a preset travels across the owner's
  machines identically (`eps_image/resolution_presets_store.py` — which
  deliberately imports `lora_library.context`'s
  `LibraryContext`/`_atomic_write_text`; its docstring records why that is
  the sanctioned exception to eps_image's usual self-containment). Atomic
  same-dir writes; a malformed file or entry degrades per-entry with a
  WARNING, never crashes; §3.5's `base_mtime` conflict convention verbatim
  (`check_conflict`/`ConflictError`, current mtime echoed in the 409 body).
  - **Routes** (`eps_image/routes_resolution_presets.py`, registered
    defensively from `__init__.py` with the shared `_context`):
    `GET /eps_resolution/presets` → `{presets, mtime}`;
    `POST /eps_resolution/presets/save` `{name, values{5}, base_mtime?}`;
    `POST /eps_resolution/presets/delete` `{name, base_mtime?}`. The ROUTE
    layer owns range/enum validation against `nodes_resolution`'s own
    widget constants (`WIDTH_MIN/MAX` etc. — extracted so the two can never
    drift); the STORE owns shape/type only. There is deliberately NO
    loopback gate anywhere in the module: no client-supplied path exists,
    and the file lives inside `library_dir`, which §2 already grants
    non-loopback callers read AND write — that grant is exactly what makes
    presets editable from the Mac against the PC/Linux box. Do not add one.
  - **Node semantics:** a hidden `presets` STRING widget (JSON array of
    preset NAMES in selection order, default `"[]"`; `options.hidden` in
    INPUT_TYPES is the §7.5 Vue-mode hide flag). All six outputs became
    `OUTPUT_IS_LIST` — an empty/absent/malformed selection wraps the
    UNCHANGED M1 computation in length-1 lists (downstream-indistinguishable
    from scalars; the Notebook's long-standing precedent), and K selected
    names resolve against the store AT EXECUTE TIME (server-authoritative,
    like Notebook entries) into six K-length index-aligned lists — one full
    resize of the SAME wired image per preset, the node's own five widget
    fields ignored entirely. A selected name absent from the store raises,
    naming the preset(s) and the file — a rename/delete on another machine
    must fail the queue loudly, never silently substitute. `IS_CHANGED`
    folds the presets file's mtime+size into the cache key ONLY when a
    selection exists (a constant otherwise, so no-preset nodes cache exactly
    as before) — the Notebook's own cross-machine staleness fix. Context via
    `set_context` (mirrors `nodes_notebook`; `__init__.py`'s generic
    `hasattr(_module, "set_context")` loop wires it).
- **M3 — the preset UI, frontend half (v0.48.0, `web/eps_image/
  resolution.js`).** A real `combo` widget `preset` plus `Save`/`Delete`
  BUTTON widgets — standard widget types, so Vue nodes render all three
  natively (unlike the M2 pad's canvas-drawn readout). Inserted AFTER all
  six backend widgets, immediately above the M2 pad, deliberately NOT
  above `width` even though the owner's brief asked for that literally:
  `widgets_values` serialize/restore is POSITIONAL (and hole-asymmetric
  for `serialize:false` widgets — serialize skips at the RAW index leaving
  a null hole, restore reads DENSELY), so any leading widget corrupts
  every pre-existing workflow's width/height on reload, and a SERIALIZED
  leading widget additionally breaks files on version DOWNGRADE (an older
  pack build restoring a new file reads the combo's string into `width`)
  — fatal for the owner's update-machines-at-different-times reality. The
  tail is the one provably safe region; the file's "M3: size presets"
  header carries the full LGraphNode.ts citation trail, and image_grid.js
  ("Clear button") + controller.js document the same constraint
  independently. All three widgets are `serialize:false` both ways
  (top-level flag for widgets_values, `options.serialize` for the API
  prompt), so a saved file keeps exactly the six dense backend values:
  old files restore via the early-exit, new files stay downgrade-safe.
  - **Combo contract:** `.value` is always a REAL token — a `(none)`
    sentinel, the sole selected NAME (kept even when no longer in the
    fetched store), or a multi sentinel — never an ad-hoc display string,
    because Vue cross-checks value ∈ options.values and draws an invalid
    ring otherwise; `options.values` is a live FUNCTION folding the
    current value in. Labels via `getOptionLabel`: `(none)`, the name
    (suffixed ` (missing)` when absent from the store), or `N presets`.
    A plain pick applies that preset's five values onto the visible
    widgets (a courtesy preview — the BACKEND ignores those fields and
    resolves from the store) and selects it; `(none)` clears.
    SHIFT/Ctrl/Cmd+click opens a checkbox-style stay-open ContextMenu
    (each row's callback returns `true` — the verified `close_parent`
    mechanism) for multi-select; canvas-only, since Vue's combobox never
    calls `widget.onClick` (a documented §7.5-class renderer gap, not an
    oversight). Selection is normalized to FETCHED order on every write
    (checkpoint_switcher's convention); still-selected names missing from
    the store are KEPT (appended, prior relative order) so the backend can
    fail the queue loudly.
  - **Save/Delete:** Save prompts via `LGraphCanvas.prompt`, prefilled
    with the active preset's name when EXACTLY one is selected (that is
    the definition of "active") — i.e. update-in-place — blank otherwise;
    the saved preset becomes the sole selection. Delete is enabled only at
    exactly one selected; no confirm dialog (re-saving recreates). A 409
    on either → warn toast + refetch, deliberately NOT the Notebook's
    Reload/Overwrite dialog — a five-field record has no partial-merge
    story. Every selection write funnels through ONE path
    (`commitSelection`: normalize → widget.value + callback → combo/
    Delete re-render).
  - **`Presets` node property** (boolean, default true): false hides all
    three widgets (BOTH hide flags each, §7.5) and force-clears the
    selection to `"[]"` so the backend provably runs classic mode; the
    reconcile also enforces that invariant on restore of a saved
    `Presets: false` file.
  - **Restore-safety:** an `onConfigure`-chained reconcile re-derives
    selection from the hidden widget's CURRENT value (checkpoint_switcher
    `reloadFromWidget` pattern), so whichever of {initial fetch,
    configure} lands LAST renders correctly — verified on the rig with a
    600 ms latency wrap (§7.5's test condition): mid-flight the sole name
    already labels ` (missing)`, and the store's answer relabels or
    confirms it.
- **Deferred (M4):** multi-image list fan-out. Do NOT build it yet.

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
- **Outputs (flow-through tee / fan-out, 2026-07-22):** `image`, `width`
  (INT), `height` (INT); `RETURN_NAMES=("image","width","height")`,
  `OUTPUT_IS_LIST=(True,True,True)`. Whatever's wired to `image` ALWAYS
  flows straight through. **Collect** mode's downstream result is ONLY this
  Run's just-recorded frame(s) — a tee, not a fan-out of the whole buffer.
  **Emit** mode's downstream result is the WHOLE buffer (chronological)
  with whatever's currently wired appended at the end (10 buffered + 1
  wired → 11 runs). Each image is a `[1,H,W,C]` batch-of-one (NEVER
  stacked — buffered images may differ in size); width/height pair 1:1
  with the emitted list in both modes.
  **Empty buffer emits `[ExecutionBlocker(None)]` for each output, NOT bare
  `[]`:** a bare empty list `IndexError`s in execution.py's `slice_dict`
  (`v[-1]`) the moment it feeds a downstream node that also has any ordinary
  widget input (nearly every node), crashing the run; the blocker makes core
  silently skip the downstream branch (the same fix `EPSSwitcher` uses for
  all-off). Verified live 2026-07-20.
- **Execution model:** `OUTPUT_NODE = True` (so it runs even with nothing
  wired downstream — collect phase; NOTE this also means the node executes
  every queue even when a downstream lazy consumer like EPS Switcher has
  its branch toggled off) + `IS_CHANGED` returning `float("nan")` → exactly
  one execution (= at most one append of the batch) per queued prompt;
  `Emit` simply skips the append. **2026-07-22:** `ui.images` now reports
  ONLY the refs a Run actually appended — omitted entirely when nothing was
  (Collect with nothing wired, or an invalid `grid_uuid`), and `Emit` never
  reports `"ui"` at all (previously every Run reported the whole buffer,
  which polluted ComfyUI's generated-output panel with the same images on
  every single Run — the owner-reported "10 new + the 10 original images,
  every run"). **The thumbnail grid is therefore no longer free from
  core** — `image_grid.js` keeps it in sync itself, refreshing from
  `GET /eps_image_grid/list` on this node's own execution-complete signal
  (a `progress_state` "finished" transition, chosen because core sends NO
  `executed` event at all for a run whose result carries no `ui` —
  execution.py gates both the event and the cache on non-empty ui).
- **The display sync must own BOTH sides of core's identity check
  (root-caused + fixed 2026-07-27; the owner's longest-lingering report:
  "a new image becomes the focus and there is no way back to the full
  grid").** Frontend 1.45.21 wires every node's `onDrawBackground` to
  `updatePreviews` (litegraphService.ts), which re-renders the node's
  images FROM `app.nodeOutputs[locator]` whenever `node.images !==
  store.images` — an ARRAY-IDENTITY comparison — and core's `executed`
  handler REPLACES that store entry with just the run's reported refs
  (which, per the 2026-07-22 decision above, is deliberately only the
  newly-appended ones). So any fix that wrote only `node.imgs`/`node.images`
  lost by construction: repaired, then re-clobbered on the very next
  repaint, forever — which is why three earlier rounds of imperative
  refreshes and a timer "bet" (an 800ms second refresh, now deleted) never
  stuck. Reproduced live in one call before fixing (one simulated repaint
  flipped a 3-image grid to the store's single new ref). The fix:
  `setNodeImagesFromRefs` writes `app.nodeOutputs[String(node.id)] =
  {images: refs}` with the SAME array instance it puts on `node.images`
  (`syncCoreOutputStore` — root-graph nodes only; a subgraph locator is
  `<uuid>:<id>` and guessing it could hit an unrelated root node), and an
  `onExecuted` wrapper (`installExecutedMerge`) merges the run's refs into
  the full list SYNCHRONOUSLY in the same tick core clobbers the store
  (core calls `onExecuted` right AFTER its store write — app.ts order,
  verified — and no repaint can interleave a synchronous tick). New refs →
  the whole grid shows with the new image in it (`imageIndex = null`); a
  cached re-send (same refs) → store healed with the EXISTING array
  identity, so a user's deliberately focused cell survives batch re-queues.
  A FORCED `/list` reconcile follows (`scheduleRefresh(node,
  {force:true})`, which never rides an in-flight pre-event fetch and never
  settle-skips — both were real stale-view holes); display correctness
  never depends on it. Pinned headless in `tests/test_image_grid_js.py`
  (including core's identity check replayed verbatim, and that
  `node.images = refs` is never a defensive copy — a clone would resurrect
  the bug with every other test green) and verified live: three real
  queued Collect runs showed 1 → 2 → 3 images with repaints in between.
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
- **Identity hardening (2026-07-24, owner bug "a copied grid ran with the
  original's buffer"):** (1) the `grid_uuid` widget carries a
  `serializeValue()` returning `currentUuid(node)` (properties-first) — the
  /prompt build and workflow saves both prefer `serializeValue`, so a stale
  widget value can never make a RUN diverge from what the node displays;
  (2) a debounced **settled-collision sweep** runs after the graph goes
  quiet — two live grids still sharing a uuid at settle time is a genuine
  PERSISTED duplicate (e.g. an undo snapshot captured between a paste and
  its deferred remint), and the higher-id node gets the normal mint+clone
  (lowest-id keeps the identity; clones copy the buffer, never a loss).
  Transient rebuild collisions still remain untouched (the v0.19.2 rule).
- **Empty-grid visibility (2026-07-24):** a run of an ENABLED grid with an
  EMPTY buffer and nothing wired in raises a warning toast naming the node
  — that state emits an ExecutionBlocker and silently skips every
  downstream branch (§6.4 list semantics), which owners read as "my
  workflow won't run". Post-run refresh also fires a second, DELAYED
  refresh (~800ms) so core's async `executed` imgs-replace can't win the
  race and strand the node showing only the newest image.
- **Clipspace paste appends too (2026-07-22, owner bug "second paste
  overwrites the first"):** core's own "Paste (Clipspace)" right-click item
  is wrapped (`installClipspacePasteOverride`, per-instance guard) so it
  ALSO appends the pasted image(s) to the durable buffer via the same
  add-route pipeline as Ctrl+V, then refreshes to show the FULL buffer —
  core's `ComfyApp.pasteFromClipspace` only does a bare `node.imgs = [img]`
  replace (never touching our routes), which is why the second paste
  appeared to discard the first. Ref-reuse: an image whose src is already a
  ComfyUI `/view?...` URL is appended by `{filename,subfolder,type}` ref
  with NO re-upload; anything else falls back to fetch + `/upload/image`.
  Fails soft if a future frontend renames the menu label.
- **Bulk add (2026-07-29, owner ask — `docs/ROADMAP-image-grid-bulk-add.md`;
  "it's really hard to add many images to comfy," typical batch 20–100,
  ordering filename-numeric-aware):**
  - **"Add images…" button** (after Clear): a detached
    `<input type="file" multiple accept="image/png,image/jpeg,image/webp">`;
    `widget.serialize = false` on the INSTANCE (§6.3's flag nuance); no
    `canvasOnly`, and the callback uses only closure state (Nodes 2.0 strips
    `pos`/`event`/`value`). `input.value` resets in `onchange` so re-picking
    the same files fires again.
  - **Numeric-aware sort before ingest** (`sortFilesForIngest`, pure,
    exported): `Intl.Collator(undefined, {numeric: true, sensitivity:
    'base'})` on `file.name` — `img2` before `img10` — applied by BOTH the
    picker and the Finder-files drop branch (FileList order is
    spec-unspecified; Windows hoists the last-clicked file first). Ctrl+V
    paste batches are not sorted (typically 1–2 files, clipboard order is
    meaningful).
  - **One shared batch runner** (`runAddBatch`) behind the picker, the drop
    path, and clipspace-'all': display refresh HOISTED out of the per-file
    loop (every `BATCH_REFRESH_INTERVAL`=10 files + once at the end — the
    old per-file refresh rebuilt an `Image()` per ref in the whole buffer
    per file, ≈5,050 full-res fetches for 100 files); progress + cancel on
    the button itself (`label` mutated to `Cancel (n/total)` — paint-only,
    `name` stays the lookup key — restored in a `finally`); a real
    `AbortController` (verified: `api.fetchApi` spreads its options bag
    into `fetch`, so `signal` passes through) plus a cooperative flag; ONE
    aggregate toast (added/skipped/failed); a busy-guard refusing a second
    concurrent batch per node (manifest read-modify-write is unlocked —
    §6.6's own atomicity note); and the batch's `grid_uuid` captured ONCE
    up front (the uuid-remint race, roadmap risk #1).
  - **Uploads always send a BARE BASENAME** (owner bug 2026-07-29, "Adding
    a folder fails", reproduced live): a `webkitdirectory` pick names every
    File after its place in the tree, and `FormData.append(name, file)`
    transmits that whole string as the multipart filename. Core's
    `/upload/image` then builds `filepath = join(upload_dir,
    normpath(subfolder), filename)` while only ever `makedirs`-ing the
    SUBFOLDER part (`server.py` ~400-420) — so the directory implied by the
    FILENAME never exists and the write dies with `FileNotFoundError:
    ...\Input\Eric\IMG_1865.PNG`, a 500. `basenameForUpload` strips BOTH
    separators (a Windows client against a POSIX server, and the reverse,
    are both normal here) and `append`'s third argument overrides the
    transmitted name. Same-basename collisions across different subfolders
    are fine — core auto-suffixes `name (1).ext`. Verified on the rig: a
    path-carrying name 500s without the override and 200s with it.
  - **Button order: the two ADD buttons, then Clear** (owner ask
    2026-07-29). Destructive action last, the same reasoning §6.3's
    controller stack already follows. Paint order only — all three carry
    `serialize = false`, so `widgets_values` and every saved workflow are
    untouched by the move.
  - **Silent-skip detection:** `/add` fails SOFT (HTTP 200, buffer
    unchanged) on an unreadable source — pinned in
    `tests/test_routes_image_grid.py` — so the runner counts a response
    whose `images.length` didn't grow as `skipped`, seeding the baseline
    from the displayed buffer length (0 for a fresh/empty display).
  - **The `v=<generation>` cache token, replacing `rand=`:** buffer frames
    are append-only while a buffer lives, BUT `clear()` is an `rmtree` and
    `_next_frame_filename` restarts from the highest EXISTING frame — so
    after Clear, `0001.png` is REUSED with different pixels (pinned in
    `tests/test_image_grid_store.py`). Fully-stable URLs would show stale
    cached frames after Clear; per-render `rand=` defeated the cache on
    every repaint. `store.buffer_generation(uuid)` (manifest mtime, ms) now
    rides every `/add` and `/list` response as `generation`; the frontend
    threads the last-seen value per node into every URL as `v=` — stable
    within a generation, fresh across anything that can reuse a name.
  - **Compressed thumbnails without degrading Copy:**
    `imageUrlForRef(ref, {preview, epoch})` appends `preview=webp;80`
    (server-side resize, `server.py`'s `/view` handler) for DISPLAY only.
    Pixel-consuming paths (Copy image, clipspace) derive a FULL-RES URL
    from the REF (`node.images[index]`, index-aligned with `imgs`), never
    from an on-screen `.src` — a preview `.src` would otherwise silently
    put degraded webp pixels on the clipboard. Param order is fixed
    (identity → preview → v) so `refFromImageSrc` parses either shape.
- **Delete one tile (2026-07-29, owner ask — "you get a duplicate image and
  then the grid is useless and you have to start fresh"; this un-deferred
  the bulk-add roadmap's M5 on its own stated revisit condition):**
  right-click a hovered/focused tile → **Delete this image**, on the same
  `getExtraMenuOptions` surface as Copy image (its own wrap function, the
  one-wrap-per-feature pattern). The ref is derived from
  `node.images[idx]` (never `.src`) and captured at menu-BUILD time, so
  the frame named is provably the one deleted even if the buffer refreshes
  while the menu is open. Backend: `POST /eps_image_grid/remove`
  `{uuid, filename}` → the whole remaining buffer + `generation`, same
  soft-fail conventions as `/add` (`store.remove_frame`: unknown filename
  = no-op; the frame FILE is deleted best-effort but the MANIFEST is the
  truth; numbering stays one-past-highest-EXISTING so a deleted middle
  name is never reused). No confirm dialog, deliberately: frames are this
  store's OWN re-encoded PNGs — deleting one never touches the user's
  original upload under `input/` (verified end-to-end in the store before
  shipping). Refused with a toast while a bulk batch is running (the
  manifest read-modify-write is unlocked). A focused-tile delete returns
  to grid view via `setNodeImagesFromRefs`' existing changed-content path.
- **PIL `SyntaxError` is part of the soft-fail catch (2026-07-29):**
  `PngImagePlugin` raises a PLAIN `SyntaxError` — not an
  OSError/ValueError subclass — on a truncated/corrupt PNG, which let a
  corrupt upload 500 the `/add` route despite `append_uploaded_image`'s
  "never raises" contract (found live, pinned by a corrupt-PNG test). Both
  that catch and `read_all_as_tensors`' per-frame skip now include it.
- **Drop-to-add (2026-07-22, owner ask):** dropping onto the node adds to
  the buffer and pre-empts core's "load workflow from dropped image"
  (core's own drop handler early-returns when a node's `onDragDrop`
  resolves truthy). Handles, in order: real OS files (Finder/Explorer →
  upload + add), the assets-panel drag payload
  (`application/x-comfy-asset-info` JSON — a server-side ref, added with NO
  upload; the panel never carries real File objects), and a same-origin
  `text/uri-list` fallback. Non-image drops return false so core's normal
  behavior (e.g. workflow load) still applies.
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
  strip — in order: jump-to-start, step −5, step −1, play/pause (kept in the
  MIDDLE so the strip reads symmetrically and the long-shipped control never
  moves), step +1, step +5, a bounded `frame` number input, and a live
  "Frame X / N" counter. The four step buttons **press-and-hold to
  auto-repeat** (~400ms before the first repeat, then ~90ms per step) so a
  held button scrubs the timeline; jump-to-start is idempotent and
  deliberately has no repeat. Every trigger — click, press, repeat tick —
  goes through the SAME clamp as a single click, and a repeat that reaches
  frame 0 or the last frame stops rather than spinning. The repeat is torn
  down on pointerup/pointercancel/pointerleave, window blur (Cmd/Alt-Tab
  away mid-hold), and `onRemoved`. Six buttons plus the field and counter set
  this node's width floor (`MIN_NODE_WIDTH`, §7.2's no-hard-coded-px rule);
  the counter is the element that gives way when the node is at that floor.
  All of it driven by
  `currentTime = frame/fps` against the PROBED fps/frame_count (the LNL /
  core / cprb split: preview approximate, run-time extraction authoritative).
  Selecting/stepping writes the `frame` INT widget. Clean-room — do NOT copy
  GPL VHS/LNL code (this pack is MIT); reimplement the pattern.
- **Paste a video PATH onto the node (owner ask 2026-07-21; he chose
  file-path paste over file-upload, keeping the no-copy design):** a
  per-instance, capture-phase `document` `paste` listener that fires only when
  THIS Frame Saver is the SOLE selected node and no text field is focused
  (so the Browse dialog's own path input and the frame box keep native paste).
  It reads `event.clipboardData.getData('text')` — the paste EVENT, never
  `navigator.clipboard.readText()`, so it works in the same insecure context
  (Mac-over-`http://<pc-ip>`) that gates the Image Grid's Copy (§6.6). The text
  is cleaned to an absolute path (first non-empty line; one pair of wrapping
  `"`/`'` stripped — Explorer "Copy as path"/shells; `file://` decoded to a
  plain/UNC path; length-capped) and, if path-shaped, routed through the SAME
  `chooseVideoPath()` the Browse picker uses (writes `video_path` →
  probe → `<video>` → counter). Non-path text is left un-consumed (workflow-JSON
  paste still reaches core); a clearly non-video extension is rejected with a
  toast WITHOUT clobbering the loaded path (the probe stays the real
  validator); a remote viewer sets the path like a workflow-loaded one (Browse
  is hidden, the host-only overlay shows, Run still extracts). Capture-phase
  registration is what lets an accepted path pre-empt core's own bubble-phase
  paste handler. Verified live: pasting a clip path loaded it (counter "Frame 0
  / 16", `<video>` src set); a `.txt` path was rejected, clip unchanged.
- **Deferred:** multi-frame (sibling node), transcode-for-exotic-codecs,
  in/out range. VFR sources: the frame↔time arithmetic is approximate for the
  counter (shared limitation of all prior art); output still lands on a real
  frame. No module-scope torch/av/ComfyUI import.

### §6.8 `LoraLibrarySweep` (display: "EPS LoRA Sweep") — strength iterator

Roadmap: `research/roadmap-eps-lora-sweep.md` (M1 = this section). Lives in
`lora_library/` (a lora-family feature, unlike the `eps_image/` non-lora
nodes just above), category "EPSNodes". Class id `LoraLibrarySweep` frozen
once shipped (§8). Genuinely uncovered per research (`r1-market.md`): no
MIT-clean node anywhere combines a real min/max/increment range sweep with
per-activated-lora fan-out and filename-safe labeling in one place.

**ONE node, not a producer+applier pair** (roadmap "Architecture decision:
ONE node", 2026-07-22): it applies internally, reusing
`LoraLibraryApplySet._apply_stack` (§6.2) rather than emitting swept stacks
for a separate standalone `LORA_STACK` applier — the owner's use case never
needs a stack outside sweeping, so a second node would only be one more
thing to wire with no benefit; see the roadmap for the full tradeoff.

- **Inputs:** `model` (MODEL, required) + `lora_stack` (LORA_STACK, required
  — wire in any producer's output, most commonly EPS Apply LoRA Set's own
  `lora_stack`; "activated" = however many rows the wired stack already
  contains, since a producer like EPS Apply LoRA Set already excludes its own
  disabled rows), plus `clip` (CLIP, **OPTIONAL** — owner ask 2026-07-22).
  `model` is required because a lora *tester* needs a model to patch (no
  pure-stack-source mode). `clip` is NOT required: many models neither
  require nor ship a text-encoder CLIP, so an unwired `clip` means each step
  patches the MODEL only — `_apply_stack` → `comfy.sd.load_lora_for_models`
  already tolerates `clip=None` (patches only the wired side, exactly core's
  `LoraLoaderModelOnly`; the lora's clip-side weights, if any, are skipped),
  and the `clip` OUTPUT then passes through `None`. (Original M1 made both
  required, lumping `clip` in with `model`; there was never a clip-specific
  reason — corrected to optional.)
- **Widgets:** `min` / `max` (FLOAT, default 0.0/1.0, range −10.0..10.0,
  step 0.05), `increment` (FLOAT, default 0.1, range 0.01..10.0, step
  0.01), `mode` (COMBO: `Each lora independently` default / `All
  together`).
- **Outputs:** `RETURN_TYPES=("MODEL","CLIP","STRING")`,
  `RETURN_NAMES=("model","clip","label")`, all three declared
  `OUTPUT_IS_LIST=(True,True,True)` — the node produces the WHOLE list up
  front (not a per-widget list-conversion trick); downstream (KSampler →
  VAEDecode → SaveImage, none of which declare `INPUT_IS_LIST`) then fans
  out via ComfyUI's own list execution, running once per planned step —
  the same Prompt-Notebook/`EPSSwitcher` fan-out mechanic (§6.1/§6.4), not
  the "wire a list into a widget" pattern.
- **Step math (fencepost/precision — `build_sweep_plan`'s `_step_values`
  helper):** `n = max(1, round((max-min)/increment) + 1)` when
  `increment > 0` and `max >= min`, else a degenerate single step at `min`
  (covers a non-positive increment, `max < min`, and `min == max` alike —
  the last reaches the same single-value result through the ordinary
  formula, since `max >= min` already holds when they're equal). Both ends
  are INCLUSIVE: `0.0 → 1.0 @ 0.1` is **11** steps, not 10. `max` is a
  documented CEILING target, not a hard clamp, when the range doesn't
  divide evenly — the step count rounds to the NEAREST fit. Every value is
  computed FRESH from its index (`min + i*increment`, then rounded to
  `increment`'s own decimal precision), never accumulated — accumulating
  `+= increment` across many steps compounds binary-float error
  (`0.30000000000000004`-style dirt); computing fresh plus a trailing
  `round()` keeps every fencepost value exact for any sanely-dialed-in
  increment.
- **Mode "Each lora independently" (default):** for every row in
  `lora_stack`, sweep THAT row's strength across every step while every
  OTHER row holds its own configured `strength_model`/`strength_clip`
  unchanged. `n_loras × n_steps` runs total. A row whose stored
  `strength_model`/`strength_clip` differ is swept on BOTH sides to the
  same value (locked default — one knob, one mental model, matching what
  the label then shows; revisit only on feedback).
- **Mode "All together":** every row in `lora_stack` moves to the SAME
  swept value at once, one run per step. `n_steps` runs total, independent
  of how many loras are in the stack (including zero — see the empty-stack
  note below).
- **Label** (`_sweep_label`): `<lora>_<value>` naming ONLY the lora being
  swept and its value — e.g. `my_great_lora_0.5`. In "Each lora
  independently" that's the swept row's stem; in "All together" it's the lone
  lora's stem (1-lora stack) or `all` (≥2 loras, e.g. `all_0.5`). The value
  is formatted to a CONSTANT number of decimals (the increment's own
  precision, `_decimal_places`) so a sweep's filenames line up and sort
  (`my_great_lora_0.0` … `my_great_lora_1.0` for a 0.1 sweep), not the ragged
  `_0`/`_0.5`/`_1` a `%g` format gives at round-number endpoints.
  Deliberately NOT `nodes_sets._loras_text(swept_stack)` (owner ask
  2026-07-22): that dumps the WHOLE stack — every HELD lora too, space-joined
  (`detailer_0.3 grain_0.8`) — which buries the one value under test among
  constants; right for EPS Apply LoRA Set's static `loras_text` (§6.2), wrong for
  a per-run sweep filename. Filename-safe; wire straight into a `SaveImage`
  `filename_prefix`.
- **Empty-stack passthrough:** an empty `lora_stack` in "Each lora
  independently" mode has zero rows to iterate, so the plan would otherwise
  contain literally ZERO entries — not merely uninteresting: an empty
  `OUTPUT_IS_LIST` list actively crashes ComfyUI's own list fan-out
  (`execution.py`'s `slice_dict` indexes the LAST element of each list
  input; an empty list has none) the moment this node's output feeds any
  downstream node that also has an ordinary, non-list input — the exact
  lesson already learned for `EPSSwitcher`'s all-off case and
  `EPSImageGrid`'s empty buffer (§6.4/§6.6). THERE the fix is an
  `ExecutionBlocker` (deliberately skip downstream); HERE a base-model
  passthrough is more useful than a block — nothing about the inputs is
  wrong, there's just nothing to sweep — so the node instead emits a
  SINGLE sentinel run: unpatched `model`/`clip` and the label `(no loras to
  sweep)`. (An empty stack in "All together" mode does NOT hit this guard
  — it still emits `n_steps` passthrough-shaped runs, since there is
  always at least one step value regardless of the stack; each just carries
  an empty label.)
- **Weight patching:** `m, c = LoraLibraryApplySet._apply_stack(context,
  model, clip, swept_stack)` per planned step — the exact staticmethod §6.2
  uses, proven to match ComfyUI's own `LoraLoader` to zero floating-point
  diff by `tests/test_nodes_sets_weight_math.py`; this node never
  reimplements weight patching. `_apply_stack` clones a fresh patcher per
  call, so the N produced models/clips are fully independent of each other
  — and cheaply so: a `ModelPatcher` is a patch-list over a shared base,
  not a weight copy, so actual weight materialization stays lazy until
  sample time. Producing N patched models up front is not N times the
  VRAM.
- **No context configured** (node probed before `__init__.py`'s
  `set_context` loop has run): a single passthrough run — unpatched
  `model`/`clip`, label `(no context configured)` — mirroring
  `LoraLibraryApplySet.apply`'s own no-context posture (§6.2), logged as a
  warning.
- **Caching:** no `IS_CHANGED` override. Unlike EPS Apply LoRA Set, this node
  reads no file off disk — `lora_stack` is an ordinary hashed INPUT (the
  upstream producer re-executes on its own file change, not this node), so
  ComfyUI's default input-hash caching over the three widgets plus the
  three wired inputs is already correct. No `VALIDATE_INPUTS` either:
  `mode`'s COMBO is static (two hardcoded options), not a dynamic
  set-of-names list like EPS Apply LoRA Set's `set`.
- **Caveats (surfaced in the node's `DESCRIPTION`):** a scalar seed wired
  downstream repeats IDENTICALLY across every fanned run — the core
  list-zip mechanic (§6.4's same caveat) — which is exactly right for a
  clean strength A/B, not a bug; wire an explicit per-run seed list instead
  for per-run variation. Changing ANY widget here (even just `mode`)
  re-renders the WHOLE sweep on the next queue — caching is all-or-nothing
  per node, never per swept step. `min`/`max` apply UNCLAMPED (−10..10) —
  deliberate over/under-strength testing is allowed.

### §6.9 `EPSCrossProduct` (display: "EPS Cross Product") — every-with-every pairing

NON-lora node in `eps_image/`, category "EPSNodes". Class id `EPSCrossProduct`
frozen once shipped (§8). Born from an owner report 2026-07-23: a 2-image EPS
Image Grid feeding the same path as 4 selected EPS Prompt Notebook entries
produced 4 downstream runs — (img1,p1), (img2,p2), (img2,p3), (img2,p4) —
because core list execution ZIPS index-by-index and repeats the shorter
list's last element (`execution.py` `slice_dict`, `v[i if len(v) > i else
-1]`). Core has no cross-product mechanism; this node is it.

- **Inputs:** `images` (IMAGE) + `texts` (STRING, `forceInput` — wire-only)
  required; `names` (STRING, `forceInput`) OPTIONAL (2026-07-23b, §6.10's
  organization ask): the Prompt Notebook's `name` output, index-aligned
  with its `text`, crossed identically so each pair keeps a short
  human-readable identity. `INPUT_IS_LIST = True` for the same reason as
  §6.4's switcher: without it core would map THIS node over the longer
  list, zipping the very lists it exists to multiply.
- **Outputs:** `image` + `text` + `name` (appended 2026-07-23b — additive,
  existing wires keep their indices; unwired `names` → aligned empty
  strings, and a short `names` list pads with "" rather than guessing),
  all `OUTPUT_IS_LIST`, length N×M, IMAGE-MAJOR (image 1 with every text
  in order, then image 2 with every text, …), index-aligned by
  construction — wire them onward in place of the originals and downstream
  runs N×M times with the pairs intact. A `[B,H,W,C]` batch element stays
  ONE element (switcher-consistent; the node never unpacks upstream
  batches).
- **Empty side** (either list empty after dropping `None`s) → the §6.4
  `[ExecutionBlocker(None)]` pattern on BOTH outputs: the branch silently
  skips, the queue succeeds. No `IS_CHANGED` (pure function of inputs).
  No torch/ComfyUI import at module scope (elements are opaque).

### §6.10 `EPSCrossSweep` (display: "EPS Cross Sweep") — sweep × pairs, organized

NON-lora node in `eps_image/`, category "EPSNodes". Class id `EPSCrossSweep`
frozen once shipped (§8). Owner ask 2026-07-23b: run an EPS LoRA Sweep
across ALL of §6.9's image/text pairs — sweep(11 steps) × pairs(8) must be
88 runs, but wiring both into one sampler ZIPS them (§6.9's core
semantics), yielding 11. This node crosses the two GROUPS while keeping
each internally aligned — a model is only meaningful with ITS clip and
label, so two chained Cross Products cannot express it.

- **Inputs (required):** `model` + `clip` + `label` (the sweep's three
  aligned lists — wire all three from the SAME EPS LoRA Sweep) and
  `image` + `text` (from the SAME EPS Cross Product). Optional: `name`
  (Cross Product's `name` output) and a `base_folder` STRING widget (may
  be empty; `/` allowed for nesting). `INPUT_IS_LIST = True` (§6.9's
  rationale). Mismatched lengths within a group log a warning and use the
  min; either group empty → the §6.4 `[ExecutionBlocker(None)]` pattern on
  all six outputs.
- **Outputs (all `OUTPUT_IS_LIST`, length steps×pairs):** `model`, `clip`,
  `image`, `text`, `save_prefix`, `label` — **STRENGTH-MAJOR** (owner
  decision 2026-07-23b: outer loop = sweep step, so each strength's
  results land together; 11 steps × 8 pairs = 88, and the run count
  multiplies again per lora in the sweep's independent mode — surface the
  math in the DESCRIPTION, the owner manages scale on his side).
- **`save_prefix`** = the organization ask: per-run
  `<base_folder>/<sweep label>/<pair name>` ready for
  `SaveImage.filename_prefix` (core treats `/` as output subfolders) →
  one folder per strength, files named by pair. Components are sanitized
  (path separators/Windows-reserved/control chars → `_`, `..` segments
  dropped, whitespace collapsed); empty pair name falls back to a stable
  `pair_NN`, empty label to `step_NN`.
- A fixed seed repeats across all runs (desired: strength and pair are the
  only moving variables). No `IS_CHANGED` (pure function of inputs). No
  torch/ComfyUI import at module scope (elements are opaque).

- **v0.46.0 — the sweep group gains `vae`, and pairs can be text-only.**
  Both additive and §8-safe:
  - `vae` (optional input, index-aligned with model/clip/label — wire from
    §6.12's Checkpoint Switcher) rides each STEP; the new `vae` output is
    TAIL-APPENDED to `RETURN_TYPES` (outputs are positional; inserting
    mid-tuple would repoint saved workflows' wires). Unwired, the vae
    OUTPUT emits one silent `ExecutionBlocker` per run — only its own
    consumers skip; there is no sensible fallback VAE. Wired-but-empty
    clamps `steps` to 0 → the whole-node blocker path, like an empty model
    list. Length disagreements warn and clamp, naming vae in the message.
  - `image` moved REQUIRED → OPTIONAL (validation loosens only). Unwired =
    text-only mode: `pairs = len(texts)` and the `image` output emits a
    per-run blocker — this is what makes Checkpoint Switcher × a
    multi-select Notebook compose for txt2img with no input images
    anywhere. `save_prefix` still shapes as `<base>/<label>/<name|pair_NN>`,
    so a checkpoint sweep lands one folder per checkpoint.
  - Per-run blockers (not one blocker list, not None, not `[]`) keep every
    output the same length — index alignment is this node's whole contract,
    and §6.9 documents why `[]` and `None` both crash consumers.

### §6.11 `EPSDistributor` (display: "EPS Distributor") — one in, N gated out

NON-lora node in `eps_image/`, category "EPSNodes". Class id `EPSDistributor`
frozen once shipped (§8). **§6.4's Switcher pointed backwards**: Switcher is
many toggleable INPUTS gathered into one output (N enabled inputs → N
downstream RUNS); this is ONE input tee'd onto many toggleable OUTPUTS, each
socket independently carrying either the image or a silent block, all in a
SINGLE run. Wire one picture to an upscale branch, a restyle branch and a
save branch, then flip any branch off from this one node — no rewiring, no
hand-bypassing groups. Roadmap: `research/roadmap-eps-distributor.md`.

- **Input (required):** `image` (IMAGE — IMAGE-only, matching Switcher;
  any-type is an M2 scope option). **Optional:** a `toggles` STRING widget
  (JSON `{"out_N": false}`), visually hidden by the frontend. `optional`,
  NOT `required`, and NOT ComfyUI's `hidden` section (which is reserved for
  server-supplied `PROMPT`/`UNIQUE_ID`): core's prompt validation rejects a
  hand-built `/prompt` that omits any REQUIRED input BEFORE the node runs,
  which would break the no-frontend API path — so `optional` plus
  `distribute`'s own default is what keeps that path working.
- **Outputs:** fixed `RETURN_TYPES = ("IMAGE",) * MAX_OUTPUTS` (16),
  `RETURN_NAMES = out_1 … out_16`, both DERIVED from `MAX_OUTPUTS` so they
  cannot drift in length. The frontend hides the trailing unused sockets down
  to the user's chosen count (§6.5 EPS Resolution's `removeOutput`/
  `addOutputs` tail pattern — **TRAILING only, never a middle socket**, so
  existing wire indices never shift).
- **The outputs GROW as you wire, and the ceiling is real** (owner ask
  2026-07-29: "EPS Distributor should have more than three outputs. Just like
  EPS Switcher the number of nodes needs to be able to grow"). Wiring the last
  visible output reveals the next one, so there is always exactly one spare
  socket below the highest wired one — §6.4's `convergeImageInputs` feel, and a
  structural port of its `wireImageInputGrowth` hook pair. Two differences,
  both forced by outputs not being the mirror image of inputs:
  - **Bounded, not unbounded.** §6.4's `image_N` inputs can grow forever
    because ComfyUI resolves inputs **BY NAME** through `INPUT_TYPES`' dict-like
    proxy, so a socket the class never declared still binds. Outputs resolve
    **POSITIONALLY**: a link serializes as `[origin_id, origin_slot]` and core
    indexes that straight into `RETURN_TYPES`, read ONCE at registration. So
    every socket must be declared up front and `MAX_OUTPUTS` is a hard ceiling,
    raised 8 → 16 with this feature. Raising it is **append-only and therefore
    safe for saved workflows** (every existing `origin_slot` still points at
    the same output); **LOWERING it would silently repoint live links, so it
    must never happen** — the same freeze §8 applies to class ids.
  - **Grows only, never shrinks.** §6.4 CONVERGES (it also removes surplus
    trailing empties); here a socket the user has already seen stays put,
    because unlike an input an output can carry a user-typed rename that
    removal would discard, and because `Outputs` is a hand-editable property
    whose value would otherwise be fought over. Shrinking stays available, just
    explicitly: lower `Outputs` by hand, subject to the refuse-if-wired rule
    below. `MAX_OUTPUTS` is declared in BOTH `nodes_distributor.py` (the real
    sockets) and `distributor.js` (how far growth may reveal); a test imports
    the backend constant and asserts the frontend matches, because too low caps
    growth below sockets that exist and too high serializes an `origin_slot`
    core cannot resolve.
  - **Two hooks, for the two litegraph facts §6.4's header already documents.**
    `configure()`'s restore loop dispatches `onConnectionsChange(OUTPUT, …,
    true, …)` once per restored link from INSIDE its own
    `this.outputs.entries()` iteration, so growing there would splice under a
    live iterator — a `restoring` flag blanks the hook for exactly that call,
    and the existing `onConfigure` wrap re-applies the count once
    `node.outputs` is stable. And the LIVE path defers to the next macrotask
    (`disconnectOutput` dispatches the callback BEFORE returning, so a
    synchronous mutation would splice the array while litegraph still holds a
    slot index into it), coalescing a drag's event burst into one pass. That
    pass also short-circuits when growth changes nothing, since it would
    otherwise re-derive the node height on every single wire.
- **Toggle semantics — identical to §6.4's**, own local
  `_parse_toggles`/`DEFAULT_TOGGLES` (adapted, deliberately not imported:
  a malformed value must log as "EPS Distributor", never misattribute to the
  sibling, and `_unwrap_toggles` has no counterpart here since there is no
  `INPUT_IS_LIST`; `nodes_cross.py` set the same own-your-helpers precedent).
  A slot is ENABLED unless its key is present and **explicitly boolean
  `false`** — absent, `null`, `0`, `""` all mean enabled (the "ComfyUI-only
  must work" floor: a hand-edited workflow or an API caller that never heard
  of this widget still gets every output). Keys outside `out_1..out_16` are
  ignored. Malformed or non-object JSON → every output enabled + a logged
  warning, never an exception.
- **`distribute`** returns a fixed-length `MAX_OUTPUTS`-tuple every time: the
  SAME image object (no copy) per enabled slot, else a fresh
  `ExecutionBlocker(None)`. **All-off is VALID** — all blockers, no error,
  queue succeeds — mirroring
  §6.4's all-off decision, just distributed per-slot. An unwired disabled
  slot is inspected by nothing, hence harmless.
- **Why a per-slot blocker is possible at all** (the load-bearing mechanism,
  verified against the rig's own ComfyUI v0.28.0 before any code was
  written): `get_output_from_returns` rewrites a return into "every output
  blocked" ONLY when the WHOLE return value IS an `ExecutionBlocker`
  (`execution.py:394-395`). Ours is always a tuple, never a bare blocker, so
  that expansion never fires and the mixed tuple passes through untouched
  (`:396`). Each blocker then does its job at the CONSUMER: core's
  `process_inputs` blocks a downstream node the moment ANY resolved input is
  a blocker. So a disabled `out_N` skips only the branch wired to it, while
  a sibling branch on an enabled slot sees the real image and runs in the
  very same pass.
- **Deliberately NOT declared** (each would be actively wrong here):
  `OUTPUT_IS_LIST` — the values are one-per-SOCKET for parallel branches, not
  lists for a further fan-out; `INPUT_IS_LIST` — nothing fans in; and
  `IS_CHANGED` — both inputs are ordinary tracked inputs already covered by
  default input-hash caching, with no other state to go stale. This node
  also stays inside §6.4's hard rule: it never inspects the graph, so it
  cannot violate "graph inspection may change what a node REQUESTS, never
  what it RETURNS."
- **`image` IS lazy, and the request decision is WIRING-AWARE (2026-07-27b,
  after the owner's real workflow caught the toggles-only rule):** the first
  fix skipped the upstream only when every one of the fixed slots read
  disabled in `toggles` — but a workflow restored from disk replays a saved
  `toggles` that names only the VISIBLE slots (litegraph's `configure()`
  restores `widgets_values` last, clobbering any attach-time prune), so
  out_4..out_16 read enabled and his KSampler still ran, feeding sockets
  that don't exist on the node. Reproduced on the real restore path.
  `check_lazy_status` now carries hidden `prompt`/`unique_id` (§6.4's
  Switcher precedent) and requests `image` only when some ENABLED slot has
  a CONSUMER (`_wired_slots` scans the prompt for links `[unique_id, n]`);
  an unreadable graph degrades to the old any-enabled rule, never the
  reverse. Still §6.4-clean: the graph feeds only the REQUEST;
  `distribute`'s RETURN stays a pure function of `toggles` + `image`, with
  one addition — `image is None` (only reachable after a decline) returns
  blockers for EVERY slot, so a `None` can never ride the tuple, and
  `prompt` in the input signature means any rewiring re-executes rather
  than replaying that cached state into a newly-wired consumer. The
  frontend half (re-prune from `onConfigure`, which fires after
  `widgets_values` restore for both workflow-load and paste) is now just
  state hygiene, not correctness.
- **`image` IS lazy, for the all-off case only** — superseded by the entry
  above, kept for the original question's record (owner question 2026-07-27:
  "if this is at the end of a workflow, and all of the checkboxes are off,
  should the workflow run up to this point?"). Measured on the rig before
  answering: it DID still run — a non-lazy input is resolved before the node
  executes, so an entire upstream chain did its work only to have every
  output blocked and nothing consume any of it. `check_lazy_status` now
  returns `[]` when every slot is off and `["image"]` otherwise, which is a
  real branch skip (an upstream that is never REQUESTED is never added to the
  execution graph — `comfy_execution/graph.py`'s `TopologicalSort.add_node`
  `is_lazy` branch), and makes this node consistent with §6.4's Switcher,
  whose all-off case already skipped its upstream. Still inside the §6.4
  rule: the decision reads only `toggles`, an ordinary tracked input in the
  cache key, never the graph — and what the node RETURNS on that path is
  all blockers whether or not `image` was resolved, so declining it cannot
  change the result, only the work done to reach it.
  - **The frontend has to record hidden slots as `false` for this to fire.**
    The backend has a fixed `MAX_OUTPUTS` slots and treats an absent key as
    enabled (the no-frontend floor), but the node only shows `Outputs` of them
    — so with the default 3 visible and all three switched off, out_4 upward
    still read as enabled and the upstream still ran. `pruneToggles` therefore
    writes `false` for every hidden slot (they cannot be wired to anything —
    they are genuinely removed from `node.outputs`), and clears the entry for
    any slot a count INCREASE just revealed, so a revealed socket always
    starts enabled. Verified end to end on the rig with the exact string the
    UI produces.
- No torch/ComfyUI import at module scope (`ExecutionBlocker` is imported
  lazily, only on the at-least-one-disabled path).
- **The toggle geometry, and why it is simpler than §6.4's** (verified against
  frontend 1.45.21's own extracted TS, not assumed). Clicks on the output side
  DO reach `node.onMouseDown` — but `_processNodeClick` runs its outputs loop
  first and `return`s on
  `isInRectangle(x, y, socketX-15, socketY-10, 30, 20)`, starting a wire drag
  instead. So the hard rule is: **a toggle box's right edge must be strictly
  left of `socketX − 15`.** Three consequences:
  - The output hit box is a FIXED 30×20 regardless of label text, unlike the
    input side's conditionally label-aware region — so §6.4's
    `20 + name.length*7` heuristic must NOT be ported here. The box uses a
    fixed `ROW_GAP` (92, reused from §6.4's validated `ROW_MIN_X`) measured
    leftward from the socket, which keeps the geometry a PURE function and
    clears the boundary by ~77px.
  - The drawn label reaches further left than that hit box: `NodeSlot.draw()`
    renders an output's name at `pos[0] - 10` with `textAlign = 'right'`, so
    the text grows leftward from there. `ROW_GAP` clears that too for the
    default 5-6 character `out_N` names (a RENAME makes them unbounded, which
    the next entry covers).
  - **No row-0 collision with the `image` input.** `out_1` shares row 0 with
    it, but a normal slot's mousedown `boundingRect` is only
    `NODE_SLOT_HEIGHT` (20px) wide — `LGraphNode._measureSlot` sets
    `boundingRect[2] = slot.isWidgetInputSlot ? BaseWidget.margin :
    LiteGraph.NODE_SLOT_HEIGHT` — NOT the wider `20 + name.length*7` that
    `getNodeInputOnPos` uses for hover/link-drop only. At the 200px width
    floor the toggle sits at x≈87–99 against an input region of x≈0–20.
  `tests/test_distributor_js.py` pins the `right edge < socketX − 15`
  INEQUALITY (not the margin) across probe widths, so `ROW_GAP` can change
  without the guarantee silently lapsing.
- **Outputs are renamable** (owner ask 2026-07-27), the output-side twin of
  §6.4's row rename: double-click an output — either its socket
  (`onOutputDblClick`) or anywhere else in the row (`onDblClick`, which is
  where our toggle box lives, the two being mutually exclusive per click) —
  and set a display label. `output.name` is never touched, so a rename can
  never repoint a wire or move a `toggles` key; empty resets to `out_N`.
  Because a label is drawn LEFTWARD from the socket, `toggleBoxRect` takes
  the label's reach and pushes the box further left when needed — the fixed
  `ROW_GAP` is a floor, not an addition, and the hard `socketX − 15`
  inequality is pinned at label lengths up to 120 characters.
- **Lowering the visible count never destroys a wire.** §6.5's rule
  ("never leave a dangling wire") generalized from a boolean to a range: the
  request is clamped back UP to the highest WIRED slot rather than reverted to
  its previous value, so everything provably-unwired above it still hides, and
  the user gets a toast naming the socket. "Wired" checks BOTH `output.links`
  and `output._floatingLinks`, mirroring `LGraphCanvas`'s
  `hasRelevantOutputLinks`, because a missed link would be silently REMOVED —
  the same check (and the same function, verbatim) as §6.5's, which was
  `.links`-only until v0.34.0; the two are kept in lockstep.
- **Live-verified on the rig 2026-07-27, 5/5** (LoadImage → Distributor →
  three SaveImage branches): all-on saves 3; `out_2` off saves exactly the
  other 2; two off saves exactly 1; ALL off reports `success` with nothing
  saved; `{"out_2": null}` stays enabled.

## §6.12 `EPSCheckpointSwitcher` (display: "EPS Checkpoint Switcher") — tick N checkpoints, run N times

The grouped answer to "try the same prompt across several models" (owner ask
2026-08-01). Tick checkpoint FILES in a panel; ONE queue emits four
index-aligned `OUTPUT_IS_LIST` lists — `model`/`clip`/`vae`/`label` (label =
filename stem) — one element per ticked checkpoint, in a stable order, so
core list fan-out runs everything downstream once per checkpoint with that
checkpoint's OWN model+CLIP+VAE travelling together. Three per-type
switchers (§6.4b) cannot express this: separately-wired axes zip (§6.9), and
model/CLIP/VAE from one checkpoint must never drift out of alignment.

- **State is one `selection` STRING widget** (optional, default `"[]"`,
  Vue-hidden per §7.5): a JSON ARRAY of checkpoint filenames exactly as
  `folder_paths.get_filename_list("checkpoints")` spells them, order =
  emission order. ONE widget, deliberately — a per-row grown-widget design
  was rejected because `widgets_values` restores POSITIONALLY, so a variable
  widget count mis-restores saved workflows; a single JSON value is
  restore-proof. Empty/omitted = nothing ticked (no sockets to default-
  enable, unlike §6.4's toggles) → the §6.4 all-off convention: four
  `[ExecutionBlocker(None)]` outputs, queue succeeds, downstream skips.
- **Loading = core's own.** Per name: `folder_paths.get_full_path_or_raise`
  + `comfy.sd.load_checkpoint_guess_config(..., output_vae=True,
  output_clip=True, embedding_directory=...)`, `out[:3]` — read off
  `CheckpointLoaderSimple` (nodes.py ~:609), not guessed; the actual call
  lives in one seam (`_load_checkpoint`) that tests monkeypatch. A selected
  file that has vanished from disk is SKIPPED with a warning (one stale tick
  must not sink the sweep); every-file-missing → the blocker path; any other
  load failure (corrupt file) propagates like the core loader's would.
  Repeat queues don't re-load: ComfyUI caches node OUTPUTS by input hash, so
  an unchanged `selection` never re-runs execute at all.
- **`VALIDATE_INPUTS`** re-checks names against the live checkpoints list
  and fails the QUEUE naming the unknown files (live-verified: the 400
  carries "unknown checkpoint file(s) ... nope/not-real.safetensors").
  Degrades to True whenever `folder_paths` is unavailable (bare import).
- **Route** `GET /eps_ckpt/checkpoints` → `{"checkpoints": [...]}` feeds the
  panel; no loopback gate (same list `/object_info` already exposes to every
  viewer). Registered defensively like the grid/frame-saver routes.
- **Panel** (`web/eps_image/checkpoint_switcher.js`): an `addDOMWidget`
  checkbox list — filter (substring, keydown-stopPropagation'd),
  folder-grouped rows, count line, ⚠ rows for selected-but-missing files
  (still untickable-off, never silently dropped from the JSON), empty/error
  +Retry states, ~320px width floor. Writes `selection` re-sorted to the
  SERVER list's order (click order is not emission order), missing names
  appended after. Restore-safe via the §7.2 `wireConfigureReload` pattern:
  `onConfigure` chained, and both it and the fetch reconcile through one
  shared reload so whichever finishes last wins. No window-level listeners
  at all (§7.5 trivially satisfied).
- **Memory note** (docs duty, not code): N ticked checkpoints load N models
  in one queue; ComfyUI's model management offloads between runs, but users
  should start small — README says so.
- Live-verified on the rig (2026-08-01, placeholder checkpoint files):
  object_info shape; route list; panel render/group/count; tick/untick
  writing server-ordered JSON; save→reload restoring ticks; ⚠ missing row;
  filter; empty-selection queue succeeding through a real `VAEDecode`
  consumer (blocker path); queue-time rejection naming an unknown file.
  Real model loading needs real checkpoints — checklist item for the owner's
  machines.

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
  - **The panel MUST re-read the file after `configure()`** (2026-07-27,
    owner: "Every time I load a workflow on my Linux machine I have to
    re-select the location of my Notebook .md file… and it doesn't take
    until after recreate node > reset widget values"). Reproduced on the
    real `app.loadGraphData` path. `attach()` fires `reloadNow()` at once,
    but litegraph restores `widgets_values` LAST — after construction and
    `add` — so the panel loaded the BACKEND DEFAULT file's entries, and
    `configure()` then wrote the saved path into the widget *without*
    firing its callback (it assigns `widget.value` directly). `state.file`
    (displayed) and `fileWidget.value` (what a Run reads) then disagreed
    permanently: the node ran the right file while showing the wrong one.
    `wireConfigureReload` wraps `onConfigure` — the one hook that fires
    after the restore, for whole-workflow load AND paste — and reloads when
    they differ. It also re-syncs `lastKnownFileValue`, the baseline the
    remote read-only guard reverts to, which was captured at attach time
    and would otherwise let a remote browser rewrite a loaded workflow's
    saved path back to the default.
  - **An equal-value re-pick must still reload.** `setFileWidgetValue`
    early-returned whenever the chosen path equalled the widget's current
    value — and after a load it always did, so re-picking the same file in
    Browse… was a dead click and the only escape was recreating the node.
    The early return now fires only when the panel is ALSO showing that
    file (`state.file === value`); otherwise it reloads. This is the same
    restore-path lesson as §6.11's Distributor bug, one week apart: **a
    fresh in-session node is not the same code path as one restored from
    disk, and only the latter is what users actually have.**
  - **Every DOM-widget node also has a WIDTH floor** (2026-07-26, owner
    report from Linux: "the container [can be] smaller than the content …
    text boxes or columns break out"). Height was always clamped
    (`getMinHeight`); width never was, so a node could be dragged — or
    restored from a saved workflow — narrower than its layout can render.
    macOS/Windows default UI fonts are narrow enough to hide it; Linux's
    are not. Each module installs an additive `onResize` clamp
    (`installMinWidth`, guard-flagged, wraps-and-calls-through) that also
    lifts the CURRENT width so an already-too-narrow saved workflow opens
    correct: Notebook 320, Lora Loader State Controller 300, Frame Saver
    260. It only ever GROWS a node to the floor.
  - **No hard-coded pixel column widths.** Any column whose width was
    measured against one platform's font (the controller's button column
    was `flex: 0 0 104px`; Frame Saver's frame field `width: 56px`) is
    content-sized with that number as a `min-width` FLOOR instead, so a
    wider font expands the column rather than ellipsising its labels.
  - Multi-select: ctrl/cmd+click toggles an entry in/out of the selection;
    shift+click selects the visible range; plain click collapses to a
    single selection. All selected rows highlight; the EDITOR always shows
    the most recently clicked entry (the "active" one) — editing/saving
    touches only it. The `entry` widget holds the §6.1 newline-joined list.
  - New-below (owner ask 2026-07-19): `＋ New` inserts the new entry
    directly below the ACTIVE entry (via the §5 entry route's `after`),
    in that entry's category — not at end-of-file. With nothing selected it
    appends as before.
  - **Renaming — TWO paths, both required** (owner ask 2026-07-29: "my
    expectation is i can either double click a name to rename in place, or if
    I change the name at the top of the notebook the item becomes savable and
    I can save with the new name"). Entries AND category headers support both:
    - **In place:** double-click the row. An input replaces the row's label,
      pre-selected; Enter commits, Esc cancels, clicking away commits.
    - **Editor header:** a NAME field at the top of the right pane always
      shows the active item's name. Editing it dirties the SAME Save button
      the body does, and Save sends the new name as `rename_to` in the very
      same request as the body/description write.
    Both go through the §5 entry/category routes' `rename_to`, refuse
    duplicates client-side first (server authoritative), and surface a §3.5
    conflict through the standard Reload/Overwrite UI.
    **The in-place path is rename-ONLY:** those routes always rewrite the
    body, so it re-sends the target's CURRENT ON-DISK text (fetched at commit
    time), never the textarea's. That matters twice over — the row being
    renamed need not be the one loaded in the editor, and even when it is,
    unsaved body edits must not be committed by what the user asked to be a
    rename.
    **History (don't repeat it):** v0.10.0 shipped an inline rename, it was
    reported not working, and v0.12.0 REMOVED it in favour of the name field
    rather than root-causing it — so the request arrived a second time nine
    days later, and this doc kept a stale bullet promising the removed
    feature the whole time. The original's fatal flaw was that its editor
    lived only in the DOM: any `renderList()` under it (poll refresh, late
    fetch, collapse toggle, drag) restored the plain label and silently
    discarded the typed text. The live text now lives in
    `state.inlineRename.value` and `renderList()` re-establishes the editor
    after every rebuild, so a re-render is invisible to the user.
  - **Share this folder with remote browsers** (owner report 2026-07-29): a
    checkbox on its own row under the file-panel path, shown ONLY when (a) the
    viewer is LOCAL and (b) the current file sits outside everything remote
    callers can already reach — i.e. exactly when a second machine would get
    the §2 403. Ticking it POSTs the file's PARENT folder to the loopback-only
    `POST /lora_library/remote_dirs`; the toggle then self-hides, because the
    condition that summoned it no longer holds. Local-only is not cosmetic:
    see §2 for why a remote caller must not be able to extend that list. A
    remote viewer instead gets the §2 error text, which now NAMES the folder
    and says to go turn this on from the host machine — the old message
    stopped at "not allowed", which is a dead end for the one case that
    legitimately hits it.
  - Delete removes EVERY selected entry (owner amendment 2026-07-18c): the
    confirm label shows the count when >1 ("Are you sure? (3)"); deletion
    is sequential client-side over the §5 delete route, refreshing
    `base_mtime` from each response; a mid-sequence conflict stops the run
    and surfaces the standard §3.5 conflict UI.
  - Categories in the UI (owner ask 2026-07-19): `＋ New` with a name
    STARTING WITH `#` creates a category instead of an entry (the `#` and
    surrounding whitespace are stripped from the stored name). Category
    headers are CLICKABLE and do TWO things at once: (1) toggle
    collapse/expand of that category's entries in the left list (owner ask
    "single tap category name to collapse category"; collapse state is
    UI-only, per-browser, not written to the file), and (2) make the
    category active so the editor pane shows its §3.1 description.
    **One exception to (1), added 2026-07-29:** the tap that first SELECTS a
    header only ever EXPANDS, never collapses. Selecting is the only way to
    get a category's name into the editor, so folding collapse into that same
    tap meant "click the category you want to rename" also hid every entry
    inside it — the rename worked, but it read as the entries having been
    deleted (found on the rig while chasing the rename report). Taps on an
    ALREADY-ACTIVE header toggle collapse exactly as before, so the
    single-tap-collapse ask is intact. The toggle must therefore keep running
    BEFORE `selectCategory()` at both call sites, since it reads
    `activeCategory`'s PREVIOUS value. Save
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

- **§7.5 Vue nodes ("New node design") compatibility** (owner report
  2026-07-29: "in general i feel like there is an uptick in issues using my
  mac to connect to the linux machine with these nodes" — root-caused on the
  rig by flipping `Comfy.VueNodes.Enabled` and watching the pack degrade).
  With that setting on, the frontend renders nodes as Vue DOM instead of
  running litegraph's canvas pipeline, and THREE things follow:
  - **Hand-drawn controls disappear.** Everything this pack paints in
    `onDrawForeground`/`onMouseDown` — Switcher/Distributor per-row toggles,
    Resolution's readout lines, the Distributor's double-click rename — is
    simply not drawn and not clickable. No error anywhere; the features are
    just gone, which reads as "these nodes are broken."
  - **`widget.hidden` is ignored, so plumbing widgets LEAK.** The Vue
    renderer decides visibility from the input spec's OPTIONS
    (`options.hidden` — `useProcessedWidgets.ts`, verified in the rig
    frontend's source maps) and snapshots widget options at creation, so the
    attach-time `widget.hidden = true` this pack uses for canvas does
    nothing there, and a late `options.hidden` mutation does nothing either.
    `toggles`, `grid_uuid`, `video_path`/`frame`, and the Notebook's `file`
    all rendered as raw editable text fields. **Fix: the flag ships FROM THE
    BACKEND** — every internal plumbing input carries `"hidden": True` in
    its `INPUT_TYPES` options (pinned against the real dicts in
    `tests/test_vue_nodes_compat.py`); the frontend hide-sites also mirror
    `options.hidden` for their frontend-created widgets. Canvas ignores the
    key right back, so it is purely additive.
  - **Two browsers on ONE server can disagree.** The frontend nags each
    browser separately to try the new design, and a long-lived tab keeps
    whatever mode it loaded with — so "works on the Linux machine, broken
    from the Mac" can be nothing but the two browsers' render modes. This is
    the first thing to check for any works-here-not-there report.
  - **Window-level gesture listeners must be CAPTURE-phase.** The Vue node
    wrapper stops pointer events from BUBBLING out of the node's DOM, so a
    plain `window.addEventListener('pointerup', …)` never fires there — and
    every gesture that COMMITS in such a listener silently dies: the
    Notebook's row clicks stopped selecting, drags stopped dropping,
    double-click pairs never completed, while element-level listeners
    (typing, buttons) kept working. Capture-phase listeners descend from the
    window before any bubble-path `stopPropagation` can intervene, so they
    fire identically in both renderers; the matching `removeEventListener`
    must pass the same flag or it silently fails to detach. Pinned across
    `notebook.js`/`resolution.js` in `tests/test_vue_nodes_compat.py`.
  - **LAN latency is a first-class test condition** (2026-07-30, the owner's
    "rename does not save from my mac" report — reproduced on the rig only
    after injecting 600ms into every `/lora_library/*` fetch). Two races in
    the Notebook's editor were invisible on loopback (~1ms windows) and wide
    open across machines: a LATE `loadEntryText` resolution overwrote a
    mid-typing name field and hard-reset dirty (fix: `populateEditor` never
    overwrites a focused, user-edited field; baselines still update and
    `refreshDirty` re-derives), and the save path re-baselined
    `lastSavedName` from the LIVE field at RESPONSE time, silently absorbing
    anything typed during the round trip (fix: the baseline is what was
    SENT). Disk-truth parts of a save response (`entries`, `mtime`) fold in
    even when the selection moved on mid-flight, and a rename remaps the
    selection either way. When a frontend race won't reproduce, add latency
    before concluding it doesn't exist.
  Until the pack ships Vue-native widgets (roadmap), `web/eps_image.js`
  detects the mode once per session (`warnIfVueNodesMode`) and toasts the
  honest state of things, naming the setting to turn off. The EPS Image Grid
  Add buttons additionally probe `navigator.userActivation` at click time
  and toast when a click arrives outside its user-activation window — the
  one client-side step of the add pipeline that can refuse in total silence
  (the rest of that pipeline, and every `/eps_image_grid/*` +
  `/upload/image` route, verified working for a genuine non-loopback caller
  on the rig).

## §8 Versioning & stability

- Backend version: `lora_library/version.py` (source of truth); frontend:
  `web/lora_library/version.js`; package: `pyproject.toml`. Kept in
  lockstep by `scripts/bump_version.py`; **every push bumps at least the
  patch version and is tagged `vX.Y.Z`** (docs-only changes do not bump —
  version = code-sync signal).
- FROZEN once shipped: node class ids, route paths, the §3 grammar's
  meaning of existing files, and §4 `format: 1` field semantics. New
  capabilities add fields/routes; they do not repurpose old ones.
