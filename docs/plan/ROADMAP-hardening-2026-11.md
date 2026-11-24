# ROADMAP — hardening round, November 2026 (bugs, adversarial review, shared code)

**Status:** shipped 2026-11-13 (main v0.110.3 → v0.110.13; `v1` 1.24.0 → 1.34.0).
Owner ask: "create new branches. optimize code, fix as many bugs as you can.
share code when possible. run my skill for adversarial tests." One branch and
one pull request per unit; every unit reviewed by an independent model (Codex
through the `cross-check` skill's PR gate) before merge; version bumped and
tagged at each merge; every fix ported 1:1 to the `v1` branch.

The two bridge packs shipped their own units in the same round
(comfyui-photoshop-bridge v0.5.84 → v0.5.89, comfyui-premiere-bridge
v0.16.3 → v0.16.4); their lists live in those repos' changelogs.

## How the bugs were found

- Four Codex audits of the server code (switchers + consumers, the
  lora_library server, and the two bridge packs): 22 findings, 21 confirmed
  and fixed, 1 owner decision (below).
- Three lower-cost agents reading the frontend with Node repros: 14
  findings, 13 fixed, 1 deferred (below).
- Round-trip fuzzing: the notebook store (model vs reparse), the switcher
  prompt helpers, the three JSON stores, the Premiere timeline writer/parser,
  the Photoshop raster decoders, and a Python-vs-JS differential fuzz of the
  mirrored Resolution math (8 000 cases, 130 mismatches before the half-up
  fix, 0 after).
- Every PR then went through the Codex PR gate: 19 gate runs, 26 further
  findings, all confirmed and fixed or documented. Nothing was rejected.

## Units (EPSNodes, in merge order)

| Unit | PR | Version | What |
| --- | --- | --- | --- |
| Clone ghosts | #6 | v0.110.3 | Ctrl+C / Alt-drag ghosts no longer clone grid buffers, poll, fetch or leak listeners |
| Library panels | #8 | v0.110.4 | Renames keep drafts (incl. `__proto__`), a failed star toasts and undoes only itself, Apply survives a throwing callback |
| NC owner + estimator | #7 | v0.110.5 | Number Controller memory carries its owner; estimator: Distributor off-slot, empty vs malformed Checkpoint selection, wired toggles |
| Grid list input | #5 | v0.110.6 | Image Grid consumes a switcher list in ONE run (`INPUT_IS_LIST`); trade-off documented in FORMAT §6.6 |
| Containment | #2 | v0.110.7 | Upload filename and `input_ref` fenced inside ComfyUI's folders, symlinks resolved, OS-independent rule |
| Notebook store | #3 | v0.110.8 | Edits can no longer vanish on reload (fences, newlines, reconcile + rollback, duplicates, token order); the panel adopts the stored text |
| Resolution | #9 | v0.110.9 | Contiguous outputs; one shared target for every image; half-up rounding on both sides with a Python-vs-JS parity test |
| Shared helpers M1 | #10 | v0.110.10 | `web/lora_library/panel_helpers.js`: one `el()`, one `installMinWidth()` (28 files) |
| Store I/O | #4 | v0.110.11 | Atomic save keeps the last complete copy; fstat mtimes; case-folded + inode locks; every store save route answers JSON on failure |
| Shared logger/toast M3 | #11 | v0.110.12 | `loggerFor(prefix)` and one `showToast()` for every panel (162 warn sites, 14 toast copies) |
| Saved-wire repair | #12 | v0.110.13 | Resolution puts each saved wire back on the output the user chose, read from the raw workflow (`beforeConfigureGraph`), keyed by link id, floating wires included |

Every unit is on `v1` as well (1.24.0 … 1.34.0, one minor bump each);
`v1`'s broadcast panel builds through an attribute-map adapter over the
shared `el()`.

## Decisions for the owner

1. **"A disabled switcher row blocks its switcher" — NOT A BUG. Closed
   2026-11-24.** The audit claimed core scans every provided input for
   `ExecutionBlocker`s, so a disabled row whose source ran anyway would block
   the rows that are on. Core does scan every PROVIDED input, but an
   unrequested lazy input is never provided: `ExecutionList.get_cache` only
   returns links registered through `add_strong_link`, and a lazy link becomes
   strong only when `check_lazy_status` asks for it
   (`comfy_execution/graph.py`; true on v0.36.0 and back to at least
   v0.28.0). Queued by hand on a live v0.36.0 server in three execution
   orders, the enabled row came out every time. A `graphToPrompt` post-pass
   shipped in v0.110.16 on the strength of a test harness whose cache fake
   handed over every link; it fixed nothing, ran on every queue and changed
   API-format exports, so the owner had it removed in v0.110.20. **Lesson:**
   a claim about core's execution is proved by queueing a prompt on a live
   server, not by a harness that fakes core's cache.
2. **Image Grid `INPUT_IS_LIST` trade-off** (gate on PR #5): a list holding a
   blocker ELEMENT now blocks the grid for that run, where per-element
   mapping collected the valid ones. Kept (one batch for the common switcher
   case). Decision: kept one-run, with a skipped-run alert (a warning toast
   when the grid was skipped; FORMAT §6.6).
3. **Suspected** (agent reports; all three since settled, see the follow-up
   build below):
   `controller.js` `_refreshTargetCombo` may overwrite the saved target on a
   transient zero-loader tick; `sets.js` `healMirrorsTags` undo ordering;
   Bypass/Switcher unplug memory keyed by node id could be trusted after a
   paste into ANOTHER workflow where the id coincides.

## Follow-up build, 2026-11-15 to 2026-11-24

The owner's answers to the decisions above, and what shipped for each.

| Unit | PR | Version | What |
| --- | --- | --- | --- |
| Controller target | #14 | v0.110.15 | Suspected item, REAL: the LoRA Controller's saved target survives a refresh that finds no loader |
| Switcher prompt pass | #15, #22 | v0.110.16, removed v0.110.20 | Decision 1: built, then found to fix nothing on a live server and removed |
| Nine images | #21 | v0.110.19 | EPS Resolution takes nine images (owner ask) |
| Group helpers | #18 | v0.110.21 | Shared-code M4 |
| Skipped-run alert | #16 | v0.110.22 | Decision 2: Image Grid keeps one-run and warns when core skipped it |
| Copies own no memory | #17 | v0.110.23 | Suspected item, REAL: a pasted or cloned node, and a pasted subgraph's interior, never re-plug the original's remembered wires |

- Suspected item "`sets.js` heal undo order": NOT REAL on frontend 1.52.7.
  The delete step is captured before the heal tick and the change tracker's
  50 ms squash folds the heal's write into it. Depends on that squash; the
  owner's checklist has one live check for newer frontends.
- Every unit is on `v1` (1.37.0 to 1.45.0), together with `main`'s
  v0.110.17 and v0.110.18 from the simplification round.
- The plan for bringing `v1` onto `main` is
  [ROADMAP-v1-merge.md](ROADMAP-v1-merge.md).

## Still open from this round

- Shared code M2 (`chainHook`, ~90 sites, in batches) — see
  [ROADMAP-shared-panel-code.md](ROADMAP-shared-panel-code.md). M4 shipped in
  v0.110.21. The owner asked for M2 as the next build.
