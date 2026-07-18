# FORMAT.md — the binding contract for EPSNodes

Naming (2026-07-18 rebrand): the PACK is **EPSNodes** (repo, node-browser
category, Settings section, About badge — everything a user sees). The
python module `lora_library/`, the `/lora_library/*` route prefix, and the
`LoraLibrary*` node class ids are the pack's first FEATURE FAMILY and stay
frozen (§8) — future non-lora features arrive as sibling modules under the
same EPSNodes banner, without repo churn.

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
- **Move** relocates one entry (drag-reorder's primitive): to just before a
  named sibling entry, or to the END of a named category (creating that
  category heading at end-of-file when new), or to the end of the file
  (`category: ""` targets the uncategorized head region only when one
  exists, else the file start). Category membership FOLLOWS position — the
  file is the truth, so dragging an entry under another category heading IS
  the category change. The moved entry's body travels byte-identically.

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

- `format`: integer, currently `1`. Readers reject greater values with a
  clear "update the pack" error and tolerate missing optional fields.
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
| `GET /lora_library/config` | `{"library_dir", "default_library_dir", "configured": bool}` |
| `POST /lora_library/config` `{"library_dir"}` | validates (absolute, creatable, writable), persists; `{"ok", "library_dir"}` |
| `GET /lora_library/loras` | `{"loras": [".."]}` — installed loras for pickers |
| `GET /lora_library/notebook?file=` | `{"file": <resolved abs>, "exists": bool, "mtime", "entries": [{"name","category"}], "problems": [".."]}` (missing file ⇒ `exists:false`, empty entries — NOT an error) |
| `GET /lora_library/notebook/entry?file=&name=` | `{"name","category","text","mtime"}`; 404 if absent |
| `POST /lora_library/notebook/entry` `{"file","name","text","category"?,"rename_to"?,"base_mtime"?}` | create-or-update per §3.4/§3.5; `{"ok","mtime","entries"}` (fresh list) |
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

### §6.1 `LoraLibraryNotebook` (display: "LoRA Notebook")

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
  applied strength).
- Outputs: `MODEL`, `CLIP`, `LORA_STACK`, `STRING` (`trigger_words`),
  `STRING` (`loras_text`).
- `loras_text` is the human-readable/paste-able summary of what was applied:
  the enabled, resolved rows in order as A1111-style tags —
  `<lora:stem:strength>` (or `<lora:stem:strength_model:strength_clip>`
  when the clip strength differs), `stem` = basename without extension,
  strengths post-`strength_scale`, space-joined; `""` when nothing applied.
- Behavior: loads the §4 file; applies enabled rows IN ORDER via the same
  core machinery ComfyUI's own LoraLoader uses, when `model`/`clip` are
  wired; always emits `LORA_STACK` = `[(file, strength_model,
  strength_clip), …]` for enabled rows (efficiency-nodes-compatible) and
  `trigger_words`. With no model/clip wired the node is a pure
  stack/trigger source. `"None"` (or a missing/unresolvable set) with
  model/clip wired ⇒ passthrough + empty stack; missing SET file logs a
  warning, missing individual loras follow §4 skip rules.
- `IS_CHANGED` → set file mtime/size + widget values; `VALIDATE_INPUTS`
  True (set list is dynamic).

### §6.3 `LoRA Set Controller` (frontend-only virtual node)

Registered purely in JS (like core's MarkdownNote) under the type name
`LoraLibrarySetController`; it never executes server-side and never blocks a
queue. It drives a **genuine, untouched `Power Lora Loader (rgthree)`**:

- Widgets: `target` (COMBO of PLL nodes in the graph, by title `#id`, PLUS
  an `All Power Lora Loaders (N)` option when N ≥ 2 — the WAN high/low
  dual-loader case; auto-selects when exactly one exists), `set` (COMBO
  from §5 sets routes — **choosing a set IS the apply**: the combo's
  callback applies immediately, there is NO Apply button; owner decision
  2026-07-18 after the button read as broken), `name` (text), buttons:
  `Capture target → new set`, `Update set`, `Delete set`.
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
  - Drag-reorder: rows drag within the list with a visible insertion
    marker; dropping emits one §5 `/notebook/move` (before = the row below
    the marker, or `category` append when dropped at a category's end/on
    its header). §3.5 conflicts surface exactly like Save conflicts.
- **§7.3 Settings**: an "EPSNodes" settings section shows backend +
  frontend versions (mismatch ⇒ "pulled but not restarted" hint, cpsb
  pattern) and the `library_dir` (editable → `POST /config`). Remote
  browsers (§2: a Mac viewing the PC's ComfyUI) DEFER to the host: the
  field displays the server's value, `POST /config` is only attempted when
  the user actually edits it to a DIFFERENT value, and a 403 downgrades to
  a calm "the library folder is controlled by the server machine" notice —
  never an error toast on page load (owner report 2026-07-18).
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
