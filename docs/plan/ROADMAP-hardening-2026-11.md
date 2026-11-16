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

1. **A disabled switcher row still blocks its switcher when the row's source
   emits blockers and that source is executed anyway** (Codex, switchers
   audit). Core scans every PROVIDED input for `ExecutionBlocker`s before
   `run()`, lazy or not, so no node-side change can help. The fix is on the
   frontend: strip disabled rows' links from the PROMPT (a chained
   `app.graphToPrompt` post-pass, the rgthree precedent) so the server never
   sees them. **Built** (`web/eps_image/switcher_prompt.js`, FORMAT §6.4):
   one pure function over the API prompt, chained from the extension's
   `setup`, no setting; a Python-vs-JS parity test and a test against core's
   real dispatch back it.
2. **Image Grid `INPUT_IS_LIST` trade-off** (gate on PR #5): a list holding a
   blocker ELEMENT now blocks the grid for that run, where per-element
   mapping collected the valid ones. Kept (one batch for the common switcher
   case); say so if you would rather revert.
3. **Suspected, not pursued** (agent reports, unverified on the rig):
   `controller.js` `_refreshTargetCombo` may overwrite the saved target on a
   transient zero-loader tick; `sets.js` `healMirrorsTags` undo ordering;
   Bypass/Switcher unplug memory keyed by node id could be trusted after a
   paste into ANOTHER workflow where the id coincides.

## Still open from this round

- Shared code M2 (`chainHook`, ~90 sites, in batches) and M4 (group
  helpers) — see [ROADMAP-shared-panel-code.md](ROADMAP-shared-panel-code.md).
