# ROADMAP — bringing `v1` onto `main`

**Status:** plan written 2026-11-15 at the owner's request. M0 is done (the
numbers below were measured, not estimated). Nothing has been merged. Owner
decision 2026-11-24: keep `v1` separate and caught up until he has tested it
(`main` v0.110.23 = `v1` 1.45.0).

## What this is

`v1` is the pack's next major version. It has lived on its own branch since
2026-10-03 so it could be tested without touching what the owner works with
every day. Since then every fix that shipped on `main` has also been ported to
`v1`, one unit at a time. This plan is how the two become one again.

## What a `main` user gets (the reason to do it)

| New on `v1` | Where to read about it |
| --- | --- |
| **Broadcast** on EPS Run Multiplier: real, tucked wires into empty required inputs, so the same outputs are not wired by hand again and again | README "Broadcast (v1)" |
| **EPS Audio Switcher** (new node) | README |
| **EPS Group Bypass** (new node): bypass or mute a whole group from one switch | README |
| **EPS Save Image**: `preview_only`, and `png` 16-bit / `exr` / `avif` output | README |
| **EPS Resolution**: typed ratio, rotate, Save and Delete side by side | README |
| **EPS Number Controller**: BOOLEAN rows | README |
| Nested reach (subgraphs), Option-click to collapse all sections, negative `scale_multiplier` | README |

Twenty-three nodes become twenty-five. Nothing is removed.

## M0 — Proof that `v1` already contains `main` — DONE 2026-11-15

Measured at `main` v0.110.14 and `v1` v1.36.0:

- Files on `main` that are missing on `v1`: **0**.
- Distinct test names on `main`: 1 237. Missing on `v1`: **3**, each one a
  test that `v1` renamed because the behaviour it pins grew
  (`test_image_output_from_saved_values_cases`,
  `test_registry_has_exactly_the_four_classes`,
  `test_save_button_forwards_litegraph_s_event`).
- `v1`'s own suite is green on every port (each port is gated on it).
- Commits: `v1` is 44 ahead of the shared base and `main` is 61 ahead, because
  ports are cherry-picks, not merges. So git sees two histories although the
  content is a superset. That shapes M3.

Re-run before M3 (from the repo root):

```bash
comm -23 <(git ls-tree -r --name-only origin/main | sort) <(git ls-tree -r --name-only origin/v1 | sort)
```

```bash
comm -23 <(git grep -hoE "def test_[A-Za-z0-9_]+" origin/main -- tests | sort -u) <(git grep -hoE "def test_[A-Za-z0-9_]+" origin/v1 -- tests | sort -u)
```

Running `main`'s test files unchanged against `v1`'s code is NOT a usable
check: `v1`'s modules import siblings that `main`'s test layouts do not copy,
so it reports hundreds of setup errors that are not regressions (tried
2026-11-15: 56 failed, 221 errors).

## M1 — Old workflows open unchanged on `v1` (automated, on the rig)

Useful on its own: it is the one thing a daily user cannot afford to lose.

- Point the rig at the `v1` worktree. Load every `examples/*.json` and the
  owner's own saved `main` workflows. For each: no missing node, no widget
  value shifted to a neighbour (frontend-added widgets are tail-only, outputs
  are positional), every wire still on the output it was saved on, and the
  API prompt equals the one `main` builds for the same file, apart from
  inputs that only exist on `v1`.
- Queue the examples that need no model.
- Script it so it can be re-run after every later port.

Exit: a report with zero differences, or a list of differences each explained.

## M2 — Owner test pass on `v1`

The `v1` checklist page (74 items, none validated yet):
https://claude.ai/artifact/78LzPqCTrc195TyMwKkqkZ

Not all 74 have to pass before the merge. The must-pass set is:

1. The version check and "old workflow opens unchanged" items.
2. One item per new node (Audio Switcher, Group Bypass).
3. Broadcast: preview, connect, one-step undo, and that it is OFF until used.
4. Save Image with the default settings writes the same file as before.

Everything else can be validated after the merge, because it is new surface
that an existing workflow does not touch.

## M3 — The merge

- One pull request, `v1` into `main`, merged with a **merge commit**. Not a
  rebase and not a squash: the tags `v1.0.0` … `v1.x` point at `v1`'s commits,
  and only a merge commit keeps them reachable from `main`.
- Conflicts: `v1` is a superset, so every conflict resolves to `v1`'s side.
  The proof is mechanical: after resolving, `git diff origin/v1 HEAD` must
  print nothing.
- `main` then carries `v1`'s version. Bump once more (minor) on the merge so
  the number shown in the app changes for everyone, and tag it.
- Before merging, tag the last `0.x` commit as the rollback point (it already
  has its release tag) and leave a branch `v0-maintenance` on it.
- CI must be green on all six legs on the pull request.

## M4 — After the merge

- Delete the `v1` branch and the port step from the ship process. One branch,
  one version line.
- Fold the `v1` checklist page into the main EPSNodes page.
- README and `docs/FORMAT.md` are already `v1`'s. Check the node count in
  `pyproject.toml`'s description and the install notes.
- Update the roadmap files that say "also on `v1`".

## Rollback

- A user: check out the last `v0.110.x` tag. Workflows saved on `v1` that use
  only old nodes still open there; ones that use Audio Switcher, Group Bypass,
  broadcast or the new Save Image formats do not.
- The repo: `git revert -m 1 <merge commit>` on `main`.

## Risks

| Risk | Mitigation |
| --- | --- |
| A daily workflow changes behaviour on update | M1 compares prompts for real saved workflows before anything merges |
| Wire drawing (tucked wires) in "New node design (beta)" cannot be checked on the rig, whose browser pane is hidden | Owner item on the checklist, in both modes |
| The rig runs frontend 1.52.7; shipping frontends are newer | The checklist's version items run on the owner's PC and Linux box |
| Work keeps landing on `main` during the test pass | Keep porting each unit as today until M3 |

## Decisions for the owner

1. When to do M3: after the must-pass set (recommended), or after all 74.
2. Whether to publish to the ComfyUI Registry at the same time (still deferred;
   it needs a publisher id).
