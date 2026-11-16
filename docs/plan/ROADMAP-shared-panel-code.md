# ROADMAP — shared panel code

**Status:** in progress. M1 (`el`, `installMinWidth`), M1b (`nodeClassOf`,
`findWidget`, `linkById`, `LINK_COLOR_OWNER_KEY`), M3 (`loggerFor`,
`showToast`) and M4 (the group helpers, `group_helpers.js`) are shipped and
enforced by tests; M2, M5 and M6 are open. The plan was first written on
2026-09-08.
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

*Update (v0.99.0):* `web/eps_image/bypass.js` adds the first
`eps_image` -> `eps_image` imports: its unplug/replug helpers
(`collectOutputTargets`, `disconnectAllTargets`, `reconnectRememberedTargets`,
`hideValuesWidget`) come from `number_controller.js` (`installMinWidth` from
the shared `panel_helpers.js` since 2026-11-13), and
its adopted-type rule and link-colour convention (`resolveAdoptedType`,
`LINK_COLOR_OWNER_KEY`, `LINK_COLOR_RESYNC_HOOK`) from `distributor.js`
(FORMAT.md section 6.18), all under their existing bare names. Each of those
exports carries a comment naming `bypass.js` as its second caller. They are
the obvious first residents of a neutral shared module when a milestone here
next runs; until then the exports are the single implementation.

---

## Milestones

Ordered by value-per-risk. Each is independently shippable and useful on its
own; none depends on a later one.

### M0 — Fix the live drift first (no refactor) — **DONE, v0.91.2**

Bring the four stale `installMinWidth()` copies in line with the five fixed
ones. **This is a bug fix, not a cleanup**, and it should not wait behind
any refactoring.

- Size: ~4 lines × 4 files.
- Risk: near zero — it is a copy of code already shipping in five files.
- Verify: create each of the four nodes below its minimum width on the rig
  and confirm it lifts.

*Shipped 2026-09-08.* Rig-confirmed the guard was genuinely dead
(`node.size.constructor.name === "bound Float64Array"`, so
`Array.isArray` is false), and that all four panels now lift a
too-narrow restored width. All nine copies agree again.

### M1 — `web/lora_library/panel_helpers.js`: `el()` and `installMinWidth()` — **SHIPPED 2026-11-13**

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

*Shipped 2026-11-13 (branch `refactor/shared-panel-helpers`, on v0.110.8),
together with the `installMinWidth` unification (the nine copies, two
variants, nine guard-flag names). The module is `panel_helpers.js`, not
`dom_helpers.js`, because it holds one node helper as well as the DOM one.
Final counts: `el` 8 declarations -> 1; `installMinWidth` 9 declarations -> 1
(the eps_image superset form -- the direct `node.size[0]` write when
`setSize` is missing -- and ONE guard flag, `__epsMinWidthInstalled`; no node
class carries two panels, and `frame_saver.js`'s two calls on one node use
the same floor). 10 files import the helpers under their existing bare names
(`el`: 8, `installMinWidth`: 9 declaring files + `bypass.js`, which now
imports it from the shared module instead of `number_controller.js`);
`web/` loses 391 lines and gains 24 plus the 69-line module. All 278 `el(`
call sites and every call-site test pin were left byte-identical; only 6
declaration pins were removed (their behaviour is now tested once in
`tests/test_panel_helpers_js.py`, which also fails if a panel declares its
own copy again). 14 test files that copy modules into a served layout (and
`conftest.py`'s `install_switcher_js`) copy `panel_helpers.js` too.*

### M1b — `nodeClassOf`, `findWidget`, `linkById`, one `LINK_COLOR_OWNER_KEY` — **DONE 2026-11-15**

Same recipe as M1 (branch `simplify/dead-code-and-duplicates`, on v0.110.14).
Grouped by normalised body first; every copy of the three lookups was
identical, so all of them moved into `panel_helpers.js` and are imported
under their existing bare names (call sites untouched):

| Helper | Declarations before | Files |
| --- | --- | --- |
| `nodeClassOf` | 10 -> 1 | 9 eps_image panels + `picker.js` |
| `findWidget` | 11 -> 1 | 8 eps_image panels + `notebook.js`, `picker.js`, `prompt_builder.js` |
| `linkById` | 7 -> 1 | `bypass`, `checkpoint_switcher`, `distributor`, `image_grid`, `number_controller`, `save_image`, `switcher` |

`LINK_COLOR_OWNER_KEY = '__epsLinkColorOwner'` was spelled in 6 files; the
one definition is `eps_image/owned_link_colour.js`'s. `save_image.js`,
`switcher.js`, `image_grid.js` and `checkpoint_switcher.js` import it from
there; `distributor.js` imports it and re-exports it (`export {
LINK_COLOR_OWNER_KEY }`) so `bypass.js`'s import from `distributor.js` is
unchanged.

**Left alone: `injectStyles` (7 declarations).** Six bodies are textually
identical (`number_controller.js` adds a `typeof document` guard), but each
closes over its own module's `stylesInjected` flag, `STYLE_TAG_ID` and
`CSS_TEXT`, so they are seven different functions. Sharing one means a
parameterised `injectStyles(id, css)` and a changed call site in every
panel, which is a different job from this recipe.

Enforcement: `tests/test_panel_helpers_js.py` now fails on a re-declared
`nodeClassOf` / `findWidget` / `linkById` anywhere under `web/` and on a
second spelling of the owner key, and tests the three lookups' behaviour
once. Four more files import `panel_helpers.js` (`save_image.js`,
`switcher.js`, `cross_sweep.js`, `image_grid.js`) and `distributor.js` now
imports `owned_link_colour.js`, so the served layouts of five test files copy
those modules too.

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

### M3 — one toast and one prefix-parameterised logger — **SHIPPED 2026-11-13**

The census found **14 toast helpers in 13 files** (four signature shapes) and
**162 hand-written `console.warn(PREFIX, …)` sites** in `web/eps_image/*.js`
and `web/eps_image.js`, plus `frame_saver.js`'s private `warn()` copy (22
callers). Every difference was a parameter, so `web/lora_library/api.js` now
owns both:

- `loggerFor(prefix)` returns `{ warn(message, ...details), log(message) }`
  with the exact `console.warn(prefix, message, …)` shape the old helpers
  used (a trailing `undefined` is dropped). `api.warn` / `api.log` are the
  `[lora_library]` instance. Each eps_image module keeps its own `PREFIX`
  string and does `const { warn } = loggerFor(PREFIX)`, so the console still
  attributes every message to the module that raised it
  (`tests/test_shared_log_toast_js.py` pins the 11 prefixes).
- `showToast(severity, summary, detail, { life, lives, warn, fallback })`:
  `lives` = per-severity defaults (6000 error / 3000 other; Notebook and
  Picker pass 8000 / 5000), `life` = explicit override, `warn` = where a
  THROWING `toast.add` is reported, `fallback` = what to do when the toast
  surface is missing. Each file's helper (`toast`, `_toast`, `toastSwitcher`,
  `toastInfo`, `notifyClipboard` with its debounce) is now a thin wrapper that
  keeps its summary source (node title / fixed string / argument), so every
  CALL SITE and its test pin stayed byte-identical.
- Kept silent where it was silent: a missing toast surface in
  `save_image.js`, `controller.js` and `universal_controller.js` (and the five
  node-first helpers, whose call sites already log). A THROWING `toast.add`
  in those three used to be swallowed by an empty `catch`; it now warns.
- Left alone on purpose: the 10 inline one-off `toast.add` sites (different
  lives, inline severities) and `owned_link_colour.js`, which a test pins as
  import-free, so it keeps its 2 `console.warn(PREFIX, …)`.

Net: `web/` 17 files, 50 lines fewer, and the toast/log behaviour now has one test home. Test side: `conftest.py`'s
`install_switcher_js` now copies the REAL `api.js` (+ `version.js`) instead of
generating a stub, and 9 test files that copy eps_image modules into a served
layout copy `api.js` + `version.js` too (bypass and number_controller keep
their subscription stubs and re-export the real `loggerFor` / `showToast`).
Declaration pins on the toast bodies were rewritten to the shared module;
`console.warn(PREFIX, …)` text pins became `warn(…)`.

### M4 — The group-system helpers — **SHIPPED 2026-11-15**

The group system was hand-copied into 2-3 panels under two names. Re-counted
on v0.110.14 (the v0.91.1 census numbers still held, no near-copies under
other names): `isCategoryNameInput`/`isGroupNameInput` 3 declarations,
`categoryNameFromInput`/`groupNameFromInput` 3, `parseCollapsedSections`/
`parseCollapsedGroups` 3 (notebook, controller, universal_controller; plus
prompt_builder importing notebook's), `parseRenameDraft` + `PROP_RENAME_DRAFT`
2, `_flushPendingNameEdit` 2. Diffing the copies found them behaviourally
identical apart from names and the two panels' `NODE_TITLE`, so no drift fix
was needed and no behaviour changed.

Now `web/lora_library/group_helpers.js` owns all of it, under the `group`
vocabulary: `isGroupNameInput`, `groupNameFromInput`, `parseCollapsedGroups`,
`parseRenameDraft`, `PROP_RENAME_DRAFT` and `flushPendingNameEdit({ node,
widget, canvas, title, warn })`. It is a new module rather than more of
`panel_helpers.js` because that file is DOM and node helpers while this is
pure domain logic with no `app`/`document`, so Node tests import it with no
layout. 4 panels import it (`notebook.js`, `controller.js`,
`universal_controller.js`, `prompt_builder.js`); `web/` loses about 220
lines. Saved data is untouched: each panel keeps its own property key
(`Collapsed sections` in the Notebook and Prompt Builder, `Collapsed groups`
in the Controllers) and passes the value in; only `PROP_RENAME_DRAFT`, always
spelled identically, moved.

Left separate on purpose: each Controller keeps a one-line
`_flushPendingNameEdit()` method that calls the shared function with its own
widget, `NODE_TITLE` and logger (so the click handlers and their tests are
unchanged); `toggleCollapsedSection`/`isSectionCollapsed` stay in
`notebook.js` (one definition, imported by `prompt_builder.js`); the
`_beginCategoryRename`/`_commitCategoryRename` bodies and the layout
mutation (M6).

Tests: `tests/test_group_helpers_js.py` drives the helpers directly and fails
if any other file under `web/` declares one under its old or new name, or
touches `prompt_box` / the `#` strip regex. The declaration pins and parser
probes in the Controller, Notebook and Prompt Builder test files were
removed or pointed at the shared module (their behaviour is now tested once);
call-site pins changed only by the rename. Six test files that copy panels
into a served layout copy `group_helpers.js` too.

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
