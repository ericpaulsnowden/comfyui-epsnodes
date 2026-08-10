# ROADMAP — EPS LoRA Picker (folder-scoped browsing, favorites, recents)

Status: **researched + roadmapped 2026-08-09, NOT built.** Eric's idea via
`/anthropic-skills:eric-rearch-ideas`. This file is the durable plan; update
the milestone headers with `SHIPPED vX.Y.Z` as they land (the convention the
sweep/grid roadmaps already use).

Home: builds **inside comfyui-epsnodes** under `lora_library/` — the same
module that already holds Apply LoRA Set, LoRA Iterator, and the State
Controller. NOT a new pack (the 2026-07 "separate comfyui-lora-library"
suggestion was superseded when those utilities shipped inside epsnodes).

---

## The problem (Eric, 2026-08-08)

1. ComfyUI shows every LoRA as one huge flat list — no way to click a folder
   and drill into just its contents.
2. His LoRAs are organized in folders; for most workflows he wants to see only
   the LoRAs in a **single folder + its subfolders**.
3. There is no way to **favorite** LoRAs or see **recently-used** ones.

Hard constraint from Eric: **do not rebuild LoRA loaders from scratch.** This
must feed or augment loaders — ideally the ones he already uses — or build on
top of the pack's existing LoRA nodes.

---

## Market research (2026-08-09) — what exists, and why this isn't redundant

Three parallel research agents surveyed the ecosystem against the source, not
just READMEs. Full briefs in the session transcript; verdicts:

| Need | Off-the-shelf today | Gap for Eric |
|---|---|---|
| **Folder drill-down** | SOLVED in general: pysssss "Tree (subfolders)" mode; ComfyUI-Lora-Manager folder-tree sidebar; External-Lora-Loader tree browser | **None of them touch rgthree Power Lora Loader** (Eric's daily loader) — it stays a flat list. pysssss only enhances its OWN loaders; Lora-Manager feeds its OWN nodes (existing loaders get manual copy-paste). |
| **Per-workflow folder scope** | Effectively UNSERVED. Closest = rgthree's *hidden* "Match" regex node-property (`^SDXL/artistic/`), saved in the workflow — a regex field, not a folder UI. | A real "scope this to one folder + subfolders" picker, saved per-workflow, is whitespace. |
| **Favorites** | PARTIAL: Lora-Manager stars, "Finding LoRA" bookmarks — **all per-machine / per-server.** | Cross-machine favorites (PC + Linux + Mac sharing one list) exist NOWHERE. |
| **Recently-used** | **ABSENT market-wide** — not one surveyed tool, including the 1.3k-star Lora-Manager, tracks recency. | Pure whitespace. |

**Honest redundancy call:** "just a LoRA folder browser" WOULD be redundant —
pysssss and Lora-Manager do that well and are free. The non-redundant thing is
the *combination* this pack is uniquely positioned to ship:

- **per-workflow folder scope** (nobody has a real UI for it),
- **recently-used** (nobody has it at all),
- **cross-machine favorites + recents** (everyone else is per-machine), and
- **feeds the loaders Eric already uses** — both a `LORA_STACK` (graph) AND a
  real rgthree Power Lora Loader (client-side), rather than a walled-garden
  loader.

If the goal ever collapses to *only* drill-down, the honest answer is "install
pysssss" — say so rather than rebuild it. The value is the combination.

---

## The two integration seams (both already proven in this pack)

1. **`LORA_STACK` producer (graph seam).** `EPS Apply LoRA Set`
   (`lora_library/nodes_sets.py`) already emits `LORA_STACK` from a saved-set
   JSON store in the shared library folder; `EPS LoRA Iterator` consumes a
   `LORA_STACK`. A picker that outputs `LORA_STACK` drops straight into this.

2. **Client-side rgthree widget-driving (loader-augment seam).** rgthree Power
   Lora Loader is a **closed unit** — its LoRA choices are internal string
   widgets; it has **no `LORA_STACK` in/out and cannot be fed a LoRA through
   the graph.** The ONLY way to hand it a LoRA is to rewrite its widgets in JS
   — which `EPS Lora Loader State Controller` (`web/lora_library/
   controller.js`) already does for a real Power Lora Loader. The picker reuses
   that exact technique.

Eric's decision (2026-08-08): support **both** seams.

3. **The store.** Favorites + recents live in the **shared, NAS-able library
   folder** next to notebooks / LoRA sets / resolution presets — Eric's choice,
   and the thing no competitor offers. Reuse `LibraryContext` +
   `_atomic_write_text` + the §3.5 mtime-conflict convention verbatim
   (`resolution_presets_store.py` is the closest existing precedent). One JSON
   file, format:1, `{favorites: [...], recents: [{name, ts}...]}`, capped
   recents (e.g. last 30), server-authoritative so all three machines share it.

---

## Milestones (each independently useful)

### M1 — `EPS LoRA Picker`: scoped browse + favorites + recents → `LORA_STACK` — SHIPPED v0.54.0 (2026-08-09)
*(the whole ask, graph-side, in one release — Eric chose "all three together";
built per FORMAT.md §6.13, rig-verified end-to-end incl. loadGraphData restore)*

A new node `EPSLoraPicker` (display "EPS LoRA Picker") in `lora_library/`.
User value on its own: pick LoRAs from a folder-scoped, favorited,
recently-used view and feed them anywhere a `LORA_STACK` goes (Apply LoRA Set
downstream, LoRA Iterator, KJ/efficiency samplers).

Scope of M1:
- **Folder tree + drill-down.** A DOM panel on the node (the pack's proven
  `addDOMWidget` pattern — renders under both litegraph and Vue) showing the
  LoRA tree from ComfyUI's `folder_paths.get_filename_list("loras")` (already
  read in `__init__.py`). Click a folder to drill in; breadcrumb to climb out.
  A new route serves the tree (or the frontend builds it from the flat
  path list — decide at build time; flat-list-client-side avoids a route).
- **Per-workflow folder scope.** A "scope to this folder" control; the chosen
  scope path is stored in a **hidden serialized widget** (the restore-proof
  single-STRING-widget pattern — checkpoint switcher / resolution presets),
  so it travels in the workflow `.json` and each workflow keeps its own scope.
  Sub-scope drill-down is a view on top of the saved scope.
- **Favorites.** Star a LoRA; stars come from the shared store (M1 builds the
  store). A "★ Favorites" pseudo-folder at the top of the tree.
- **Recently-used.** Selecting a LoRA for a run records it (name + ts) to the
  shared store; a "🕘 Recent" pseudo-folder shows the last N, newest first.
  (Recording point: on selection commit, not on execute, so it's instant and
  needs no backend hook — decide vs. an execute-time record at build time.)
- **Output:** `LORA_STACK` (+ trigger words STRING, matching Apply LoRA Set's
  shape) so it composes with the existing nodes. Multi-select → a stack of
  rows, each with a strength widget.
- **Store + routes:** `lora_library/lora_picker_store.py` (favorites/recents,
  atomic, §3.5 conflicts, degrade-on-unreachable-folder like the v0.52.2
  presets guard) + `lora_library/routes_lora_picker.py`
  (GET list/tree, POST favorite toggle, POST record-recent) — NO loopback gate
  (inside library_dir, §2 grants remote read+write, same as presets).
- **Tests:** store (fav toggle, recents cap + ordering, malformed-file
  tolerance, unreachable-folder degrade), routes (round trips incl.
  X-Forwarded-For remote write), node (LORA_STACK shape, scope filtering,
  restore-proof widget). JS source-text pins for the panel.
- **Docs:** FORMAT §6.x new section; README node section + "Works with" row;
  checklist items; rig verification (tree renders, scope saves/reloads, star
  persists across a simulated second machine, recents order).

Effort: **M–L** (one DOM panel + store + routes + node). Highest-value single
release; delivers all three of Eric's needs at the graph level.

### M2 — Drive the rgthree Power Lora Loader (the loader-augment seam) — SHIPPED v0.55.0 (2026-08-09)
*(Eric's "both" — augment the loader he already uses; pll_bridge.js, controller.js
untouched; rig-verified incl. the never-guess deleted-target rule and shrink)*

Add a mode/companion so the picker's selection writes into a **real rgthree
Power Lora Loader**, exactly as `EPS Lora Loader State Controller` already
rewrites PLL widgets client-side (reuse that code path; rgthree stays the
maintained loader — Eric's standing wish). User value: browse-scoped +
favorites + recents now drive his daily loader, not just a stack output.

- Reuse controller.js's proven `{on, lora, strength, strengthTwo}` widget
  rewrite; bind by the same node-name discipline the controller documents.
- Decide the UX: a button on the picker ("send to Power Lora Loader"), or a
  picker mode that targets a linked PLL. Client-side only; no graph handoff
  (PLL can't accept one — research-confirmed).
- Honest limit to document: multi-select/scope drives PLL rows, but PLL owns
  execution; the picker is the chooser, PLL is the loader.

Effort: **S–M** (mostly JS, on top of M1 + the controller precedent).

### M3 — Depth + polish — SHIPPED v0.56.0 (2026-08-09; all bullets below except the Iterator hand-off doc, which lives in README/FORMAT composition notes)
- **Preview thumbnails** in the tree (read a sidecar `<lora>.png`/`.preview`
  if present — the convention Lora-Manager/pysssss use; no CivitAI needed).
- **Trigger words** surfaced/click-to-copy (Apply LoRA Set already models
  trigger words — share the shape).
- **Search within scope** (reuse the Notebook's v0.53.0 include-text filter
  ergonomics: instant client-side, AND-of-words).
- **Recents refinements:** per-folder recents, pin/unpin, clear-recents.
- **Favorites ordering** (drag; the Notebook's drag-reorder precedent).
- **Iterator/Run-Multiplier hand-off:** picked stack → LoRA Iterator sweep in
  one wire; document the composition.

Effort: each **S**, spike-gated, shipped when Eric wants them.

---

### M4 — Send-to-loader target registry: DaSiWa Advanced LoRA Loader — SHIPPED v0.58.0 (2026-08-09)
*(researched 2026-08-09 via /eric-rearch-ideas — "is it possible to make it
also work with DaSiWa Advanced LoRA Loader without bloating the code?";
Eric's answer: BUILD NOW. Decisions locked 2026-08-09: a row whose clip
strength differs sends the MODEL strength + a toast naming what was
flattened (DaSiWa stores one strength per lora, clamped ±5); re-sending
PRESERVES an existing row's vs/as video/audio multipliers on a same-lora
match, 1.0 only for new rows.)*

Research verdicts (two agents, source-level):
- `DaSiWa_LTX2LoraLoader` (darksidewalker/ComfyUI-DaSiWa-Nodes, GPL-3,
  66k registry downloads) keeps its ENTIRE stack in one JSON STRING
  widget `stack_data` — rows `{on, lora, str, vs, as}` — with
  `node.properties.stack_data` as the live source of truth (its canvas UI
  syncs properties → widget on every draw). No LORA_STACK ports, no
  dynamic widgets. Row schema unchanged since the node's first commit.
  ⇒ the cheapest adapter shape possible: serialize + set property +
  mirror widget + redraw (~60-90 lines). GPL is irrelevant to driving it
  with DATA; no code is copied.
- Ecosystem survey: the ONLY multi-target dispatch precedent is a dict
  keyed by node class → small per-class adapter (Lora-Manager's
  NODE_EXTRACTORS, read side; 20-80 lines each). Nobody else write-drives
  another pack's loader at all — pll_bridge.js is ahead of the field.
  Efficiency / Easy-Use / Comfyroll stackers need NO adapter: they take a
  LORA_STACK input, so the picker's `lora_stack` output already feeds
  them by plain wire (README documents this).
Scope: `web/lora_library/dasiwa_bridge.js` (probe + convert + write in
pll_bridge's exact interface), a comfyClass-keyed adapter registry in the
Send row (both loader families listed, never-guess rules unchanged),
family-agnostic no-target messages, pins + Node-driven convert tests,
FORMAT §6.13 M4 bullet, README. Rig verify needs the DaSiWa pack cloned
into the rig at a pinned commit.

## Open decisions for build time (not blockers)

- Tree source: a dedicated tree route vs. build client-side from the flat
  `get_filename_list("loras")` path list (leaning client-side — no new route,
  matches how rgthree already gets nested paths).
- Recents recording point: on selection-commit (instant, no backend hook) vs.
  on execute (true "used", needs a hidden prompt/unique_id hook like the
  Run Multiplier guard). M1 can start with selection-commit and add execute
  later.
- Whether M1's node subsumes Apply LoRA Set's saved-set role or stays distinct
  (recommend distinct: Picker = browse/scope/fav/recent → stack; Apply LoRA
  Set = named saved configurations → stack; they compose).

## Licensing note (from the 2026-07 research, still current)
Forkable references (MIT): rgthree, pysssss, nd-super-nodes, Finding LoRA.
Lora-Manager / Easy-Use / KJNodes / Inspire are GPL — study for ideas, don't
copy code. This pack stays MIT-clean and self-contained (works without any of
them; augments rgthree when present, exactly like the State Controller).
