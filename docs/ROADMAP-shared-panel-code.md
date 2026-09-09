# ROADMAP — shared panel code

**Status:** planned, not started. Owner asked for the plan only (2026-09-08).
**Census:** performed 2026-09-08 against v0.91.1. Every number below was
counted from the files, not estimated.

---

## Why this is worth doing

The pack has a standing convention: sibling panels duplicate small helpers
**by hand** rather than cross-importing. There are **119 comments in the
source saying so** — "verbatim", "by hand", "twin", "no cross-module
import", "hand-port".

The convention was a reasonable call when the pack was small. It is now
demonstrably leaking bugs, and we have three concrete cases:

### 1. A fix that never reached its sibling — cost: a second bug report

`_flushPendingNameEdit()` was written in `controller.js` for v0.90.0: the
classic canvas renderer's floating prompt dialog only commits a typed value
on Enter or OK, so clicking **New State** straight after typing `# Portraits`
silently made a *state*. `universal_controller.js` had cloned that entry
point earlier and **never received the fix**. The owner had to find and
report the identical bug a second time, in a second node, and it was fixed
again in v0.91.0. `universal_controller.js`'s own comment now records this:
*"this pack's 'duplicated by hand, not imported' convention ... cuts both
ways."*

### 2. A fix that never reached FOUR siblings — live, and not yet reported

`installMinWidth()` exists in **9 files**. A v0.68.1 fix replaced a guard
that is *always false* (`Array.isArray(node.size)` — `node.size` is a Proxy
over a typed array) with a `setSize()`-aware lift. It reached five copies:

| Fixed | Still stale |
| --- | --- |
| `picker.js`, `checkpoint_switcher.js`, `frame_saver.js`, `number_controller.js`, `distributor.js` | `notebook.js`, `controller.js`, `universal_controller.js`, `prompt_builder.js` |

In those four, a node created narrower than its minimum **never has its
width corrected** — only later resizes are protected. Those four are the
pack's four biggest panels. This is shipping today.

### 3. "Groups behave inconsistently between nodes"

Reported repeatedly by the owner across many rounds. It is not a series of
unrelated slips; it is the mechanical consequence of the group system being
implemented three times. Same helper, two different names
(`isCategoryNameInput` / `isGroupNameInput`), three independent copies.

**The pattern:** every one of these was found by the owner, in production,
one node at a time. That is the cost being paid, and it recurs on every
future fix to any duplicated helper.

---

## The rule going forward

> **A behaviour that is meant to be identical across panels must have exactly
> one implementation.** If two panels should behave the same, they import the
> same function. If they should behave differently, that difference is a
> parameter with a comment saying why — never a second copy that might drift.

Corollary, and the thing that made this expensive: **when you fix a helper,
grep for its name across `web/` before you finish.** Until the milestones
below land, that grep is the only safety net.

### The no-cross-import folklore is already false

Worth stating plainly, because it is the main objection to this work:
`web/eps_image/*` **already imports from `web/lora_library/*` in
production** — `checkpoint_switcher.js`, `resolution.js`,
`number_controller.js` and `save_image.js` all do. `prompt_builder.js`
already imports `parseCollapsedSections` from `notebook.js`. There is no
bundler and no loader change needed: a shared module is one `import` line.
The Python side never adopted the convention at all, and
`resolution_presets_store.py` documents precisely when crossing the boundary
is *correct*.

So the barrier is habit, not architecture.

---

## Milestones

Ordered by value-per-risk. Each is independently shippable and useful on its
own; none depends on a later one.

### M0 — Fix the live drift first (no refactor)

Bring the four stale `installMinWidth()` copies in line with the five fixed
ones. **This is a bug fix, not a cleanup**, and it should not wait behind
any refactoring.

- Size: ~4 lines × 4 files.
- Risk: near zero — it is a copy of code already shipping in five files.
- Verify: create each of the four nodes below its minimum width on the rig
  and confirm it lifts.

*Do this even if every other milestone is declined.*

### M1 — `web/lora_library/dom_helpers.js`: `el()`

`el(tag, options, children)` is declared **8 times, byte-identical** apart
from brace style: `controller.js`, `universal_controller.js`, `picker.js`,
`notebook.js`, `prompt_builder.js`, `checkpoint_switcher.js`,
`number_controller.js`, `frame_saver.js`.

Zero dependencies (just `document.createElement`), zero domain coupling, and
it has already been independently reinvented on *both* sides of the
package boundary — which is the argument that a shared home works for both.

- Size: delete ~8 × 12 lines, add one ~15-line module + 8 import lines.
- Risk: lowest in the whole plan.
- Test cost: minimal — few pins reference `el`'s declaration.

### M2 — `chainHook()`: the biggest number in the census

The pack hand-rolls "capture the original hook, wrap it, try/catch both
halves, call through, return the original's result" **63 times across 14
files** — for `onConfigure`, `onResize`, `onPropertyChanged`, `onRemoved`,
`onConnectionsChange`, `onDrawForeground`, `onDblClick`, `onSerialize`,
`onExecuted`. At ~8–12 lines of pure scaffolding per site that is roughly
**500–700 lines of mechanically identical boilerplate with no shared
implementation**. No such utility exists anywhere in the pack.

The wrapper carries no domain knowledge — the per-site body stays exactly
where it is. This is the highest-volume, lowest-judgement extraction
available.

- Size: one ~25-line helper; convert sites incrementally, file by file.
- Risk: low per site, but 63 sites means **do it in batches with the suite
  green between each**, never as one commit.
- Note: it also gives one place to enforce FORMAT §7.9's "chain, never
  replace" law, which is currently 63 independent chances to get wrong.

### M3 — `toast()`

Nine files, **four shapes**, ~90% overlapping: class-method
`_toast(severity, summary, detail, life)` (×2), free `toast(severity,
summary, detail)` (×2), free `toast(node, severity, detail)` (×4), and
`save_image.js`'s minimal `toast(severity, detail)` which silently swallows
errors.

Every difference is parameterisable: where the summary comes from, the
default life, and the fallback log. Fold into `api.js`.

- Related, same milestone: **114 `console.warn(PREFIX, …)` call sites** in
  `web/eps_image/*` hand-roll what `api.js`'s `warn()` already does, just
  with a hardcoded prefix. Parameterise the prefix and both sides share one
  function. Keep the distinct prefix *strings* — they are what let the
  browser console attribute a warning to a node.

### M4 — The group-system helpers (care required)

Byte-identical logic, extractable:

| Helper | Copies |
| --- | --- |
| `isCategoryNameInput` / `isGroupNameInput` | 3 |
| `categoryNameFromInput` / `groupNameFromInput` | 3 |
| `parseCollapsedSections` / `parseCollapsedGroups` | 3 |
| `parseRenameDraft` + `PROP_RENAME_DRAFT` | 2 |
| `_flushPendingNameEdit` | 2 |

**Rename to one name.** The `category`/`group` split is a pure naming
accident and is itself a source of confusion when grepping for a fix.

**The hidden cost lives here** — see "Test-pin migration" below. Do M1–M3
first; by then the extraction pattern will be established and the test
migration is the only novel work left.

### M5 — Two-click armed confirm

The same `4000ms` armed-confirm state machine (timer, armed flag, colour
swap) is independently written in 4 files. The colour-swap halves are
byte-identical.

**One real semantic difference must survive as a parameter, not be
flattened:** `controller.js` re-enables its delete button from a live
rgthree health probe (`!this._lastProbe.ok`); `universal_controller.js` uses
selection state (`!this._selectedStateEntry()`). That is a genuine domain
difference — controller.js targets an external node and the other does not.
Extract the machine, take the re-enable predicate as a callback.

### M6 — Group-creation mutation

`controller.js._doNewCategory()` inlines the layout mutation;
`universal_controller.js` already factored it into `_addGroupToLayout(name)`
and calls it from both its `#` route and its new dropdown. Adopt that
factoring in `controller.js` first, *then* consider sharing the mutation.

Low priority — small, and M4 is the prerequisite that makes it worthwhile.

---

## Explicitly NOT doing

An honest do-not-touch list, because a bad extraction here would be worse
than the duplication.

- **DOM-widget sizing formulas.** 11 files declare `getMinHeight` closures,
  each with a genuinely different formula (row counts, pin offsets, group
  counts). There is no shared body to extract. The pack already documents
  the real rule — min-only when content grows below, min+max when the widget
  stands alone — and applies it correctly everywhere; the doc convention
  already does a shared helper's job.
- **`EPS_STATE_WIDGETS` (13 classes) and `IS_CHANGED` (7).** Not
  duplication. These are per-class data contracts read generically by two
  consumers. This is the *model* the rest of the pack should aspire to, not
  a problem.
- **`controller.js`'s drag-into-group vs `universal_controller.js`'s
  dropdown.** Same data mutation, deliberately different gestures — the
  ~230-line drag port was explicitly judged disproportionate and skipped.
  Unifying the interaction would be a regression, not a cleanup.
- **The Notebook's name `<input>` vs the Controllers' litegraph widget.**
  A documented, load-bearing asymmetry: the Controllers need a real widget
  slot for the hidden `set` widget's serialisation. `_flushPendingNameEdit`
  exists *because* of it. Never "unify" by changing the widget kind.
- **`prompt_builder.js`'s drafts mirroring.** Already correct — it forwards
  the raw JSON string and never re-implements the Notebook's parser. This is
  the model for what a good extraction looks like: share the contract, not
  the parser.
- **Python JSON bridge-widget parsers.** The skeleton repeats 3 times but
  the validation and normalisation — the actual logic — differ completely.
  Extracting the 6–8 lines of outer guard would cost readability.
- **`image_grid_store.py`'s atomic writes.** Deliberate, documented
  self-containment for the eps_image family.

---

## Test-pin migration — the hidden cost, quantified

The JS test suites assert on **literal source text**, not just behaviour:

```python
assert "function isCategoryNameInput(rawName)" in controller_source
```

Move that function to a shared module and this fails — not because anything
broke, but because the string left the file. Counted: ~241 such patterns in
`test_picker_js.py`, ~86 in `test_notebook_restore_js.py`, ~12 in
`test_prompt_builder_js.py`, plus specific pins on `PROP_RENAME_DRAFT`,
`_beginCategoryRename`/`_commitCategoryRename` bodies, and
`isGroupNameInput` call sites in `test_universal_controller_js.py`.

**The mitigation that keeps this cheap:** import each helper under its
**existing bare name**, so every *call site* string is unchanged. Only the
*declaration* pins (`"function isCategoryNameInput(rawName)"`) need
rewriting, and they should be rewritten to assert against the shared
module's source instead. Budget this explicitly in M4 — it is most of M4's
real cost.

---

## One product decision needed first

Not a refactor — a behaviour question only the owner can answer.

**Search boxes disagree across the state panels:**

| Panel | Behaviour |
| --- | --- |
| `notebook.js`, `picker.js` | multi-word AND (`red car` matches both words) |
| `universal_controller.js` | single substring only |
| `controller.js` | no search box at all |

Making these consistent is a **UX change**, not a bug fix, and should be
decided separately from the refactor rather than silently "fixed" as part
of it.

---

## Suggested order

**M0 now** (it is a live bug). Then **M1 → M2 → M3** — high volume, low
judgement, and they establish the shared-module pattern and prove the import
path. Then **M4** with the test-pin budget understood, and **M5/M6** only if
they still feel worth it by then.

M0 alone removes a shipping bug. M1–M3 alone remove roughly 700–900 lines
and the two failure modes that have actually cost the owner bug reports.
