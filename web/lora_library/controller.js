/**
 * @file Lora Loader State Controller — frontend-only virtual node
 * (FORMAT.md §6.3) that drives a genuine, untouched `Power Lora Loader
 * (rgthree)` node elsewhere in the graph. Registered purely in JS (like
 * core's MarkdownNote/NoteNode) — never executes, never appears in the API
 * prompt.
 *
 * Renamed from "LoRA Set Controller" (owner, 2026-07-18c): every user-facing
 * word now says "state" instead of "set" — button labels, the `set` widget's
 * on-canvas text, placeholders, toasts. The class id
 * `LoraLibrarySetController` and the backend `sets` storage/routes stay
 * FROZEN (FORMAT.md §6.3/§8) and keep saying "set" underneath — internal
 * identifiers (variables, methods, the widget's `name`, the
 * `lora_library:sets-changed` DOM event sets.js listens for) are
 * DELIBERATELY left alone so this stays a pure vocabulary change with zero
 * behavioral or serialization risk. See the `label` vs `name` bullet below
 * for the one widget where that distinction is load-bearing.
 *
 * Renamed AGAIN 2026-07-19 (owner: drop "Power" — "Power Lora Loader State
 * Controller" -> "Lora Loader State Controller"). Only `NODE_TITLE` changed:
 * the TARGET node this file drives is still genuinely titled "Power Lora
 * Loader (rgthree)", so every other "Power Lora Loader" string below
 * describes THAT node, not this one. Same owner report, two more fixes in
 * this same pass:
 *  (1) "Save State doesn't work; re-picking reverts to original strengths" —
 *      root-caused and fixed as two parts, LIVE-VERIFIED against the rig
 *      (comfyui-test, port 8199): see `_hookSetValueForReselect()` (a
 *      same-value re-pick in the `set` combo provably never reaches its
 *      callback on this fork — confirmed by reading the exact installed
 *      `BaseWidget.setValue` in comfyui-frontend-package 1.45.21) and
 *      `_doUpdate()`'s post-save re-apply. Capture itself was NOT at fault —
 *      `captureRows()` already reads the live, in-place-mutated
 *      `widget.value.strength`/`.strengthTwo` rgthree's real strength-drag
 *      handler writes (`power_lora_loader.js` `doOnStrengthAnyMove`/
 *      `stepStrength`), confirmed with an actual pointer drag on the rig.
 *  (2) Added the `Push State` button — broadcasts the selected state to
 *      every `LoraLibraryApplySet` node in the graph; see `_doPush()`.
 *
 * 2026-07-19c hardening (owner report AGAIN, this time on ComfyUI 0.28.1 —
 * "strengths are still not saved or updated"). (1) above shadowed
 * `BaseWidget.setValue` on the `set` widget INSTANCE. That shadow is correct
 * on THIS rig's exact frontend build (comfyui-frontend-package 1.45.21,
 * re-verified below, same file/lines as before) but is not a portable fix:
 * a different frontend build on the owner's 0.28.1 install can lay out
 * `ComboWidget`/`BaseWidget` differently enough that the shadow point
 * silently stops applying, and the regression looks IDENTICAL to the
 * original bug. Three changes replace it:
 *  (3) VERSION-PROOF SELECTION — `_hookSetValueForReselect()` is REMOVED.
 *      `_hookSetWidgetMenu()` / `_openSetMenu()` / `_onSetPicked()` replace
 *      it: the controller now owns the `set` widget's CLICK, not its VALUE
 *      SETTER. Re-verified against the same rig bundle cited above
 *      (static/assets/api-BqIxvqZ8.js):
 *        - `LGraphCanvas.processWidgetClick(e, node, widget)` (fired from
 *          widget hit-testing on pointer-down) resolves
 *          `c = toConcreteWidget(widget, node, false)` and only DEFERS the
 *          actual click: `pointer.onClick = () => c.onClick({e, node,
 *          canvas: this})`, invoked later by `CanvasPointer._completeClick`
 *          on pointer-up-without-drag.
 *        - `toConcreteWidget(e, node, promote)` starts with
 *          `if (e instanceof BaseWidget) return e`. `LGraphNode.addWidget()`
 *          itself already routes the raw `{type,name,value,...}` data
 *          through `toConcreteWidget` inside `addCustomWidget()` and
 *          pushes/returns THAT result (a real `ComboWidget` instance), never
 *          the raw data — so the object `this.addWidget('combo', ...)` hands
 *          back to us IS ALREADY that instance, and `c === widget` above,
 *          every time, with no proxy or fresh wrapper in between. Shadowing
 *          `.onClick` as an own property on that exact instance is therefore
 *          a stable, version-proof override: unlike `setValue`, there is no
 *          second internal (a `callback` option, a same-value branch) for a
 *          future frontend build to reintroduce this same failure through.
 *        - Stock `ComboWidget.onClick` — the method being replaced — is
 *          itself nothing more than `new LiteGraph.ContextMenu(values, {
 *          scale, event, className:'dark', callback: v => this.setValue(v,
 *          {e,node,canvas}) })`. `_openSetMenu()` below is that exact
 *          mechanism with the `setValue` detour removed, not a foreign
 *          technique bolted on. Further precedent for a node pack calling
 *          `LiteGraph.ContextMenu` directly: rgthree's OWN lora-row
 *          right-click menu (power_lora_loader.js
 *          `RgthreePowerLoraLoader.getSlotMenuOptions`, `new
 *          LiteGraph.ContextMenu(menuItems, {title, event})`).
 *        - `ContextMenu`'s per-item click (its `inner_onclick`) invokes
 *          `options.callback.call(itemEl, pickedValue, options, domEvent,
 *          menuInstance)` — `pickedValue` is exactly the array element we
 *          passed in, the identical shape the stock widget's own callback
 *          above receives — so `_openSetMenu()`'s callback getting the
 *          picked LABEL string as its one argument is guaranteed by the same
 *          code path the stock widget relies on, not an assumption.
 *      Net effect: EVERY menu pick — including re-picking the state already
 *      showing — runs `_onSetPicked()` unconditionally. There is no
 *      same-value branch anywhere in this path to route around, because
 *      `BaseWidget.setValue` (the only place that branch lives) is never
 *      called for a user pick anymore. The `_isRestoring`/`_silentSetWrite`
 *      guard fields and the `configure()` override that bracketed them are
 *      REMOVED as a consequence, not merely left unused: they existed only
 *      to stop a programmatic `.value =` write from being mistaken for a
 *      real pick THROUGH THE OLD CALLBACK/setValue path. With that path
 *      gone, a plain assignment structurally cannot reach `_onSetPicked()`
 *      — see the "Combo callbacks do NOT fire during workflow restore"
 *      bullet below, updated to match.
 *  (4) READ-BACK TOAST — `_toastRowsSaved()`. `New State`/`Save State` now
 *      follow their write with a real `GET /lora_library/set?slug=`
 *      (FORMAT.md §5) and toast what the FILE holds, not what the button
 *      THINKS it sent — a wrong `captureRows()` read on a different rgthree
 *      build, or any backend-side transform, shows up in the toast text
 *      itself. `captureRows()`'s per-field fallback chain was also widened
 *      (still read-only, still never writes an alias back). `Show status`
 *      (still hidden by default) additionally names the capture-source
 *      loader id + row count on every capture/save — see `_setStatusText()`.
 *  (5) SELECTIVE PUSH — FORMAT.md §6.2's `mirrors loader` tag, written by
 *      the sibling `sets.js` on every `LoraLibraryApplySet` node. `_doPush()`
 *      now reads the controller's OWN `target` combo to decide WHICH Apply
 *      nodes to touch: a specific Power Lora Loader target restricts the
 *      push to Apply nodes tagged to that same node (plus any tagged
 *      "(any)"); `All…` pushes to every Apply node regardless of tag. See
 *      `selectPushTargets()`/`mirrorsTagMatches()`. Deliberately still
 *      independent of `probeTargets()` (rgthree health) — a push never
 *      touches rgthree, so it keeps working with rgthree uninstalled or the
 *      target unhealthy, exactly as before this amendment.
 *
 * 2026-07-20 (round 10, owner report on v0.14.0): "Isn't working. Strength
 * is always 1.0." for BOTH `sc-save-state-overwrites` and `sc-readback-toast`
 * — the read-back toast (4 above) is doing exactly its job: it shows what
 * CAPTURE actually read, and on the owner's machine that is a flat 1.0
 * regardless of the dragged on-screen value, even though APPLY (3 above),
 * Push State, and selective Push all still work for him. Investigation:
 *  - The rig's rgthree-comfy checkout was diffed BYTE-FOR-BYTE (2026-07-20)
 *    against `raw.githubusercontent.com/rgthree/rgthree-comfy/main/web/
 *    comfyui/power_lora_loader.js` — zero differences; this rig is running
 *    current upstream `main`. `PowerLoraLoaderWidget` stores its row as a
 *    plain `{on, lora, strength, strengthTwo}` object behind a plain
 *    get/set pair (`set value(v) { this._value = v }`), mutated IN PLACE by
 *    the real drag handler (`doOnStrengthAnyMove`: `this.value[prop] =
 *    (this.value[prop] ?? 1) + event.deltaX * 0.05`) — exactly what
 *    `captureRows()` already assumed and what R4-R9 verified with a real
 *    pointer drag on this rig. Could NOT reproduce the owner's exact
 *    symptom against this code — see the final "HONEST GAP" bullet.
 *  - ComfyUI_frontend's OWN `LGraphNode.serialize()` (src/lib/litegraph/src/
 *    LGraphNode.ts, the `widgets_values`-building loop, ~lines 984-990)
 *    reads `widget.value` directly too, for the workflow-SAVE path — but it
 *    round-trips any object value through `JSON.parse(JSON.stringify(val))`,
 *    with the code comment "Ensure object values are plain (not reactive
 *    proxies) for structuredClone compatibility." That is this frontend's
 *    OWN authors documenting that `.value` is not guaranteed to be the plain
 *    object rgthree constructs on every widget/render path — precisely the
 *    class of skew a newer or differently-configured frontend build (the
 *    owner is on ComfyUI 0.28.1; this rig is verified against
 *    comfyui-frontend-package 1.45.21) could introduce without rgthree
 *    itself changing one line.
 *  - A DIFFERENT frontend path, `executionUtil.ts` (the API-PROMPT-building
 *    path, not the workflow-save path above, ~lines 103-104), goes further:
 *    `widget.serializeValue ? await widget.serializeValue(node, i) :
 *    widget.value` — i.e. ComfyUI_frontend's own code PREFERS a widget's
 *    `serializeValue()` over raw `.value`, wherever a widget defines one,
 *    for this exact reason. rgthree's `PowerLoraLoaderWidget.serializeValue
 *    (node, index)` DOES define one: `{...this.value}` (spreading into a
 *    fresh object literal always yields a plain one, proxied source or not)
 *    with `strengthTwo` deleted in single-strength mode or defaulted to 1 in
 *    dual mode. That is rgthree's OWN stated choice of "the value an
 *    outside reader should trust" — not a foreign technique bolted on, the
 *    same seam ComfyUI_frontend itself already relies on for this exact
 *    widget.
 *  - THE FIX: `captureRows()` (see its own updated doc comment and
 *    `readRowSources()`) now reads BOTH sources per row and prefers
 *    `serializeValue()`'s fields when it returns a usable object, falling
 *    back to the existing `.value`-based alias chain field-by-field
 *    otherwise — a widening, not a replacement: on any rgthree without a
 *    working `serializeValue` (including THIS rig's) this produces
 *    byte-identical output to before; on any environment where the two
 *    sources diverge, this reads the one both rgthree and ComfyUI_frontend
 *    already trust more. Every strength is then coerced through `Number()`
 *    (`coerceStrength()`); a non-numeric result is reported by row name via
 *    `console.warn` and replaced with a safe fallback rather than silently
 *    writing garbage into a saved state file. `serializeValue()` is awaited
 *    (its type allows returning a `Promise`, per ComfyUI_frontend's
 *    `simplifiedWidget.ts`), so `captureRows()` is now `async` — both call
 *    sites (`_doCapture`/`_doUpdate`) already `await` other calls in the
 *    same function, so this is a non-breaking signature change internal to
 *    this file.
 *  - DIAGNOSTICS: new `Debug capture` node property (default false,
 *    alongside `Show status`). Every capture now `console.debug`s one
 *    compact line per row (`.value` strength vs `serializeValue()` strength
 *    vs the strength actually used, and which source won) UNCONDITIONALLY —
 *    no property to flip, so the very next mismatch report already has this
 *    trail in the console with zero setup. With `Debug capture` on, a full
 *    `console.table()` of every row's raw `.value` + `serializeValue()`
 *    objects is additionally logged, for the rarer case the compact line
 *    can't resolve alone.
 *  - HONEST GAP: none of the above could be LIVE-verified against the
 *    owner's exact ComfyUI 0.28.1 install and his exact installed rgthree
 *    commit — this rig has neither, and rgthree ships no version tags to
 *    pin against (its GitHub releases list is a single 2023 entry; it is
 *    developed and distributed straight off `main`). The fix is reasoned
 *    from cited, current source in both projects, not from a rig-side
 *    repro of the 1.0 symptom itself. It is still the right bet: it can only
 *    ever be MORE correct than the old read (identical on this rig, better
 *    anywhere the two sources diverge), it costs nothing when the two agree,
 *    and it now leaves a console trail specific enough that if the owner's
 *    next report is still "1.0," the `console.debug`/`console.table` output
 *    will show definitively whether `serializeValue()` itself is returning
 *    1.0 (an rgthree-side or 0.28.1-side issue upstream of this file) or
 *    whether something else is happening (e.g. the wrong node/row being
 *    read) — either way narrowing the next round instead of re-guessing.
 *
 * 2026-07-20 (§4.1 composite fix, owner: "All Power Lora Loaders currently
 * reads only the lowest-id loader and writes that ONE config to ALL
 * loaders; I want each loader to keep its OWN distinct set, captured/
 * restored together" — same root cause as "two Apply LoRA Set nodes on
 * different loaders show identical loras_text", fixed on the ApplySet side
 * by nodes_sets.py's new `loader_slot`). FORMAT.md §4.1 adds a `loaders[]`
 * array to the §4 set-file schema for exactly this case; this file's half:
 *  - CAPTURE (`_doCapture()`/`_doUpdate()`, now delegating to
 *    `_captureComposite()`/`_updateComposite()` for target "All Power Lora
 *    Loaders (N)", N>=2): every PLL's rows, in ascending-node-id order
 *    (`resolveTargetNodes()` already sorts "All…" that way), become one
 *    `loaders[i]` each — via the SAME `captureRows()` this file has used
 *    since v0.14.1, called once per loader, completely unmodified. The
 *    single-target path (every OTHER target value) is a pure ADDITION
 *    guarded by `targets.length > 1` — the owner-validated capture/strength
 *    read for one loader runs through zero new code.
 *  - APPLY (`_doApply()`, via the now composite-aware
 *    `applySetToTargets()`): a format-2 state's `loaders[i]` goes to the
 *    i-th PLL by ascending id for an "All…" target (fewer loaders than
 *    PLLs -> extras left untouched + a toast names the mismatch, never
 *    guessed); for a SINGLE specific-PLL target, the slice is that PLL's
 *    ascending-id RANK AMONG EVERY PLL IN THE GRAPH (`pllAscendingIndex()`)
 *    — not its (always-0) position within a 1-element `targets` array. A
 *    format-1 state (no `loaders`) takes the exact pre-existing path either
 *    way — `isComposite` short-circuits every new branch.
 *  - PUSH STATE (`_doPush()`): unchanged in scope — still broadcasts the
 *    selected slug to every selected Apply node exactly as before. As a
 *    strictly best-effort ADDITION (`_syncLoaderSlotsForPush()`, wrapped in
 *    the new `_guardedAsync()`), a composite push also sets each pushed
 *    Apply node's `loader_slot` from its `mirrors loader` tag's
 *    ascending-id index, so a WAN pair of tagged Apply nodes needs no
 *    manual "reveal loader_slot and set it to 1" step. This runs AFTER the
 *    push count is already computed and can never affect it, is skipped
 *    outright for a format-1 push, and any failure inside it is caught and
 *    logged rather than surfaced — chosen deliberately over baking this
 *    into `pushStateToNode()` itself, to keep the validated core broadcast
 *    (`pushStateToNodes()`) exactly as it was.
 *  - `lorasForLoaderIndex()` is this file's hand-kept-in-sync mirror of
 *    `sets_store.py`'s `loras_for_slot()` — same "no cross-language import"
 *    convention as every other duplicated constant here (e.g.
 *    `APPLY_SET_WIDGET_NAME`).
 *
 * 2026-07-21 (TWO-PANE layout, owner ask, FORMAT.md §6.3 "TWO-PANE layout"
 * bullet — "just the two-pane layout," explicitly no file/Browse panel this
 * round): the `set` state COMBO (built + owned end-to-end by the
 * 2026-07-19c hardening above) is replaced by a Notebook-style two-pane DOM
 * widget (`_buildStatePane()`) — LEFT: a scrolling `<div>` list of every
 * saved state, one row per `_setsCache` entry, the current selection
 * highlighted; RIGHT: the four action buttons stacked vertically. This
 * mirrors `web/lora_library/notebook.js`'s own two-pane editor (FORMAT.md
 * §7.2) function-for-function, not just in spirit: `_renderStateList()` is
 * this file's `renderList()`/`buildEntryRow()` (list rebuild, per-row
 * click+keydown, an `llsc-row-active` highlight class in place of
 * Notebook's `.llnb-entry-active`); `_buildStatePane()`'s `addDOMWidget(...)`
 * call is Notebook's `attachDomWidget()` (identical `hideOnZoom`/
 * `serialize:false`/`serializeValue: () => undefined`/`getMinHeight`-only
 * shape, so the panel fills the node and never collapses to a sliver,
 * exactly per Notebook's own verified citation for that mechanism — not
 * re-derived here, just reused); `el()` below is a hand-copied twin of
 * Notebook's own tiny DOM-builder helper (this file had zero DOM-
 * construction code before today — nothing to import, so it's copied, same
 * "duplicated by hand, no cross-module coupling" convention as every other
 * shared-shape constant/helper in this file, e.g. `APPLY_SET_WIDGET_NAME`
 * above); `injectControllerStyles()`/`STATE_PANE_CSS_TEXT` is Notebook's
 * `injectStyles()`/`CSS_TEXT` twin, under a DISTINCT `<style>` tag id and an
 * `llsc-` class prefix (vs. Notebook's `llnb-`) so the two style sheets
 * never collide in the shared `document.head`.
 *
 * Row-click semantics as FIRST built that day: clicking a list row WAS the
 * apply, unconditionally, even on the already-selected row (the exact
 * semantics the combo had per the 2026-07-19/2026-07-19c strength-
 * persistence fixes above) — SUPERSEDED the SAME DAY by the owner's
 * select-vs-apply split; see the 2026-07-21b section below for the
 * two-step semantics `_onSetPicked()` implements now. What this paragraph
 * still accurately records is the CLICK PLUMBING, which the split did not
 * change: the row's plain DOM `click` handler calls `_onSetPicked(entry.
 * label)` directly, and the APPLY branch inside it still reaches
 * `_onSetSelected()`/`_doApply()` with no same-value branch anywhere in
 * the path — an apply-click on the already-showing state re-applies
 * exactly like the old combo pick did. This is a STRENGTHENING of the
 * 2026-07-19c hardening's own goal, not just a UI change:
 * `_hookSetWidgetMenu()`/`_openSetMenu()` are REMOVED OUTRIGHT this round
 * (not merely superseded — there is no combo left to hook a click onto),
 * because a plain DOM element's `click`/`keydown` listener touches ZERO
 * litegraph widget internals — no `ComboWidget`, no `BaseWidget.onClick`, no
 * `LiteGraph.ContextMenu` — so the entire CLASS of "a different frontend
 * build lays out widget internals differently" risk that motivated
 * 2026-07-19c's `onClick`-shadowing design in the first place (see that
 * section's own citation trail below — still an accurate record of why THAT
 * design was necessary at the time, not rewritten) is now structurally
 * inapplicable to state selection: there is nothing version-sensitive left
 * to bind to. `_setComboValues()` is removed as dead code (no combo left to
 * feed values to); `_renderStateList()` reads `_setsCache` directly instead,
 * which is what every label string was always sourced from underneath the
 * combo anyway.
 *
 * Serialization (FORMAT.md §6.3: "keep the internal `set` STRING widget,
 * hidden, driven by the list selection — the Notebook's `entry`-widget
 * trick"): `_w.set` changes TYPE from `'combo'` to `'text'` and gains
 * `.hidden = true` (the same first-class litegraph layout primitive this
 * file already cites for `status` below, and Notebook's own header cites
 * for its `file` widget — a real hide, not a value-blank). The widget's
 * `name` stays the frozen literal `'set'` — unchanged, so every existing
 * "kept in sync by hand" consumer of that name elsewhere, and the
 * save/restore positional-index contract documented below, are unaffected
 * by the type change. `_w.set` stays in the SERIALIZED group, declared
 * before `status` (which stays `serialize:false`) — the save/restore
 * ordering invariant documented below is unaffected: the split is still
 * target+set+name (serialized) then status+the-DOM-widget (not), in that
 * order. Restore still goes through `configure()`'s plain
 * `widget.value = ...` assignment (LGraphNode.ts, cited below) for EVERY
 * widget type, not only `ComboWidget` — so the "combo callbacks do NOT fire
 * during workflow restore" guarantee documented below is, if anything, on
 * firmer ground for a plain text widget than it was for the combo (one
 * fewer subclass layer between `BaseWidget`'s value setter and this
 * widget). VERIFY(live) regardless: re-confirm empirically (reload a saved
 * workflow, watch it NOT re-apply) rather than resting on that reasoning
 * alone — this file's own hard-won lesson from 2026-07-19c is that
 * litegraph internals are exactly the kind of thing to verify live, never
 * assume.
 *
 * The four buttons move from canvas `addWidget('button', ...)` widgets into
 * real `<button>` elements in the DOM widget's right pane
 * (`_createActionButton()`, replacing the removed `_addButton()`); every
 * click is now wrapped in `_guarded()` (a strengthening — previously only
 * Delete's click was explicitly re-guarded at the call site).
 * `_onCaptureClick()`/`_onUpdateClick()`/`_onDeleteClick()`/`_onPushClick()`
 * and everything they call (`_doCapture`/`_doUpdate`/`_doApply`/`_doDelete`/
 * `_doPush`, composite capture/apply, selective Push, the read-back toasts)
 * are UNTOUCHED — this change only alters how a click REACHES those
 * methods, never what they do once reached. The two-click delete-confirm
 * (2026-07-18c) keeps its exact constants (`DELETE_CONFIRM_MS`,
 * `DELETE_ARMED_BG_COLOR`/`_TEXT_COLOR`) and its exact arm/disarm timing and
 * slug-anchoring (`_selectedSetEntry()` is still called at arm time,
 * unchanged) — only the PAINT mechanism changes: from
 * `Object.defineProperty`-shadowing litegraph's getter-only
 * `background_color`/`text_color` accessors (moot for a real DOM node,
 * which has plain, freely-settable `style` properties — see
 * `_armDeleteButtonColor()`) to `button.style.background`/`.color`, and
 * from `button.name = ...` (a canvas WIDGET's displayed text) to
 * `button.textContent = ...` (a DOM button's displayed text — `.name` on a
 * real `<button>` element is the unrelated HTML form-control name
 * attribute, invisible on screen; using it here would silently fail to show
 * anything, which is why this particular rename is not optional cosmetics).
 * `_actionButtons`/`_probeAndUpdateStatus()`'s disable loop is untouched and
 * works verbatim: a real `<button>`'s native `.disabled` is, if anything, a
 * more correct fit than the canvas widget's `.disabled` ever was (recall
 * the file header's own VERIFY(live) note below about `.disabled` blanking
 * a TEXT widget's value outright — moot for a button, which has no value to
 * blank either way).
 *
 * `target`/`name`/`status` (FORMAT.md §6.3: "[keep] the `name` text input
 * ... and the `target` control (place it sensibly — e.g. a small row above
 * the panes)") are untouched canvas widgets, declared before the DOM widget
 * in `_buildWidgets()` exactly as before — litegraph stacks widgets
 * top-to-bottom in declaration order, so they land above the two-pane panel
 * for free, with no new layout code needed. Composite capture/apply
 * (§4.1), selective Push (§6.2), `Show status`, and the v0.14.1
 * serialize-based capture are all in the "pure graph helpers" section far
 * below this class and in the `_do*`/`applySetTo*` methods — none of them
 * reference the `set` widget's TYPE or the removed menu functions at all,
 * so none of them needed to change for this redesign to be, per the task's
 * own framing, "a UI swap, not a behavior change."
 *
 * 2026-07-21b (owner feedback on the same-day two-pane build; FORMAT.md
 * §6.3 "Select vs. apply are SEPARATE clicks" + "Save State honors a
 * changed name" — both amended there the same day). Two behavior changes,
 * nothing else:
 *
 * (A) SELECT vs APPLY split (owner: "We should now have it be a second
 * click to activate. Single click to select in case a user wants to update
 * a name or delete without changing all of the nodes."). A SINGLE click on
 * a list row now only SELECTS it — `_selectEntry()`: `_selectedSlug`, the
 * hidden serialized `set` widget + highlight repaint (both via
 * `_setSetValueSilently()`), and the `name` field loaded with the state's
 * own name (`entryDisplayName()` — the NAME, never the `.label`, whose
 * dedup "(slug)" suffix must not leak into the field and read as a rename)
 * — and provably cannot touch a loader: the select branch of
 * `_onSetPicked()` never reaches `_onSetSelected()`/`_doApply()`, and that
 * pair now has NO caller other than the apply branch. Applying is the
 * SECOND click — a click on the row that is ALREADY the current selection,
 * which is also exactly what the second click of a double-click is by the
 * time it lands. That branch keeps the old guarantee verbatim:
 * unconditional `_onSetSelected()` -> `_doApply()`, no same-value branch,
 * so re-applying the already-showing state still force-re-pushes strengths
 * (the 2026-07-19/19c fixes, unchanged). MECHANISM — ONE plain `click`
 * listener branching on "is this row the current selection?" (slug-
 * anchored via `_selectedSetEntry()`, the same label-drift-proof
 * resolution the delete confirm relies on), deliberately NOT a `dblclick`
 * listener: (1) a double-click's two presses arrive as two ordinary
 * `click` events — the first lands on a not-yet-selected row and selects,
 * the second lands on the now-selected row and applies — so double-click
 * works with zero double-click-specific code; (2) a `dblclick` listener ON
 * TOP of that would double-fire the apply (dblclick follows the second
 * click, which already applied); (3) FORMAT.md §6.3 spells the trigger as
 * "a double-click, or a click on the row that is already highlighted" —
 * the latter has no dblclick timing window at all (select now, apply
 * minutes later still applies; likewise the highlight RESTORED from a
 * saved workflow applies on its first click, by design). The mid-double-
 * click list rebuild is safe: click #1's re-render runs synchronously
 * inside its own handler (long before the second press can physically
 * land), and rebuilds the same rows in the same geometry (only classes
 * change — the active row's bold/border shifts no layout), so click #2's
 * mousedown+mouseup both land on the fresh row element carrying the same
 * listener and compose a normal `click` on it. Keyboard parity: Enter/
 * Space run the same two-step handler, and `_renderStateList()` now
 * restores focus (by row `data-slug`, `preventScroll: true`) onto the
 * rebuilt row — without that, the select-branch rebuild would drop focus
 * on the floor and a keyboard user could never deliver the second Enter;
 * it also stops the background sets-poll (`_applySetsResponse()` ->
 * `_renderStateList()`) from eating list focus mid-Tab. Any selection
 * MOVEMENT still disarms a pending delete-confirm (`_selectEntry()` keeps
 * `_onSetPicked()`'s old disarm and extends it to every selection write):
 * `_doDelete()` deletes whatever `_selectedSetEntry()` resolves at confirm
 * time, so a selection allowed to move under an armed button would aim the
 * confirm at the WRONG state. Restore round-trip is UNCHANGED: workflow
 * restore still writes the hidden widget via `configure()`'s plain
 * `.value =` (never a DOM click), so a reopened workflow shows its saved
 * selection highlighted without applying anything.
 *
 * (B) SAVE STATE HONORS A CHANGED NAME (owner: "Save does not save a new
 * name if one of the elements is selected and the name has changed. This
 * is key for creating new items."). `_saveAsNewName()` decides once per
 * Save, up front in `_doUpdate()` (shared with `_updateComposite()`): the
 * trimmed `name` field, when non-empty AND different from the selected
 * entry's own name, makes this Save a SAVE-AS-NEW — the SAME
 * `POST /lora_library/set` route in its NO-SLUG form (the exact create
 * path `New State`/`_doCapture()` has always used; routes_sets.py
 * `post_set` derives the slug from the new name and `sets_store.
 * _unique_slug` de-duplicates it against existing files, so a new name
 * that happens to collide with some OTHER state's name mints a fresh file
 * rather than overwriting that state) — then selects the newly created
 * state and toasts with the create verb ("Saved"), read-back included.
 * Rows are the CURRENT capture either way (Save has always been a
 * re-capture); trigger_words/notes are inherited from the SELECTED state
 * (the same best-effort GET the overwrite path already did) — a spun-off
 * variant keeps its parent's metadata rather than silently blanking it;
 * only the name and slug are new. An UNCHANGED or EMPTY field is the
 * byte-identical pre-existing overwrite (empty never means "rename to
 * empty" — the field simply doesn't participate, exactly as every Save
 * before this rule; it also means the post-New-State field state stays
 * benign). `New State` itself is unchanged, including its field-clearing
 * epilogue: `_doCapture()`/`_captureComposite()`'s explicit
 * `name.value = ''` deliberately runs AFTER `_selectSetBySlug()` (which
 * now loads the new state's name into the field via `_selectEntry()`), so
 * New State still ends with an empty field — typing a name and pressing
 * New State twice still yields one named state plus one auto-named
 * "State N", never two same-named copies.
 *
 * This file binds to rgthree internals it does not own. Every binding is
 * cited below with the exact file + lines read (rgthree-comfy's COMPILED
 * `web/comfyui/power_lora_loader.js`, since that's what actually runs — not
 * the TS source), plus the ComfyUI_frontend litegraph fork that governs
 * widget/serialize mechanics. `VERIFY(live)` marks anything that could differ
 * on the version actually installed on Eric's rig — see the final report for
 * the consolidated risk list.
 *
 * Key rgthree bindings (rgthree-comfy/web/comfyui/power_lora_loader.js):
 *  - Node type string: `"Power Lora Loader (rgthree)"` — constants.js
 *    `addRgthree("Power Lora Loader")` (lines 2-4, 36).
 *  - Row widgets are named `lora_<n>` via `addNewLoraWidget()` (lines 85-94);
 *    `<n>` comes from a counter that only increments (never reuses numbers
 *    after a row is removed), so row names are NOT necessarily contiguous —
 *    we identify rows by name-pattern + value-shape, never by parsing/relying
 *    on the numeric suffix, and never by assuming a fixed count.
 *  - Row value shape: `{on, lora, strength, strengthTwo}` (DEFAULT_LORA_WIDGET_DATA,
 *    lines 352-357); the `value` setter (lines 386-395) does a plain
 *    `this._value = v` assignment — confirms whole-object assignment
 *    (`widget.value = {...}`) is safe and is in fact the exact pattern
 *    rgthree's own `configure()` uses when restoring rows from a workflow
 *    (lines 65-70: `const widget = this.addNewLoraWidget(); widget.value = {...widgetValue}`).
 *  - Single vs dual strength mode is a per-NODE property, not per-row:
 *    `node.properties["Show Strengths"]` (constants at lines 15-20; default
 *    set at line 28), consumed at draw time at line 405
 *    (`currentShowModelAndClip = node.properties[...] === "Separate Model & Clip"`).
 *  - Resize-after-mutate: rgthree's own "+ Add Lora" button handler (lines
 *    99-113) does `computeSize()` then `size[1] = Math.max(..., computed[1])`
 *    then `setDirtyCanvas(true, true)`. We follow the simpler `onNodeCreated`
 *    variant (lines 78-83, `Math.max(this.size[N], computed[N])`) rather than
 *    the `configure()`-only `_tempHeight` fallback (line 73), because
 *    `_tempHeight`/`_tempWidth` are only ever set inside `configure()` (line
 *    63-64) and may not exist on a node that was only ever built via manual
 *    "+ Add Lora" clicks.
 *  - Row removal: rgthree's own context-menu "🗑️ Remove" handler (lines
 *    201-206) calls `removeArrayItem(this.widgets, widget)`, a plain splice
 *    helper (rgthree-comfy/web/common/shared_utils.js:111-114 —
 *    `arr.splice(arr.indexOf(item), 1)`, no side effects). We do not import
 *    that internal helper (it is not a stable public surface for another
 *    node pack to depend on); instead we call the official LiteGraph
 *    `LGraphNode.prototype.removeWidget(widget)` API, which a genuine PLL
 *    instance always inherits. See VERIFY(live) note at applySetToTarget().
 *
 * Key ComfyUI_frontend litegraph bindings (src/lib/litegraph/src/):
 *  - `LGraphNode.removeWidget(widget)` (LGraphNode.ts:2037-2058) takes a
 *    WIDGET REFERENCE and throws `'Widget not found on this node'` if it
 *    can't find it via `indexOf` — it does NOT accept a numeric index,
 *    despite rgthree's OWN internal `configure()` calling `this.removeWidget(0)`
 *    in a loop (power_lora_loader.js:57-58). VERIFY(live): that suggests
 *    either version skew (an older/looser litegraph rgthree was built
 *    against) or that rgthree's own teardown loop is fragile on current
 *    frontends — not our problem to fix, but it means we must ALWAYS pass a
 *    real widget object, never an index, which is what we do.
 *  - Two distinct, similarly-named "serialize" flags (types/widgets.ts:62-66,
 *    434-441; confirmed by an explicit code comment in
 *    src/utils/executionUtil.ts:96-98): `widget.serialize` (top-level on the
 *    widget instance) controls inclusion in the saved workflow's
 *    `widgets_values` (LGraphNode.ts:986); `widget.options.serialize`
 *    controls inclusion in the API execution prompt only. rgthree's button
 *    pattern `addWidget("button", ..., {serialize:false})`
 *    (fast_actions_button.js:24) sets the LATTER, not the former — it does
 *    NOT keep a button out of `widgets_values` on this fork. We therefore set
 *    `widget.serialize = false` directly on the widget objects `addWidget`
 *    returns (see `_addButton`/status widget below). Moot for prompt
 *    inclusion either way since `isVirtualNode` nodes are stripped from the
 *    API prompt wholesale (executionUtil.ts:37-39, 86-91).
 *  - `label` vs `name` (2026-07-18c rename — fetched live from
 *    Comfy-Org/ComfyUI_frontend `main` @ src/lib/litegraph/src/widgets/
 *    BaseWidget.ts on 2026-07-18; VERIFY(live) against whatever's actually
 *    installed on Eric's rig): `label` is a plain get/set pair backed by
 *    internal state (lines 91-96); `name` is set once from `addWidget()`'s
 *    2nd argument (line 169, `this.name = widget.name`) and never touched
 *    again by the base class. The rendered row text is
 *    `get displayName() { return this.label || this.name }` (lines 246-248),
 *    read by the shared label+value draw routine every combo/text/number
 *    widget goes through (`drawTruncatingText()`, lines 338-397 — line 348
 *    `const { displayName, _displayValue } = this`). So `.label` changes
 *    ONLY what's painted; `.name` is what `scanLoraRows()` pattern-matches
 *    in this file and what `widgetId()`/`setNodeId()` (lines 135-158) key
 *    off of, and per the save/restore-ordering note above this fork restores
 *    `widgets_values` by plain positional index, not by name — so nothing
 *    here needs `name === 'set'` for reload correctness, but our own
 *    lookups and any external workflow-scripting that greps a saved graph
 *    for a widget literally named `set` do. (2026-07-21: since the two-pane
 *    redesign hides this widget outright — `.hidden = true`, nothing paints
 *    it — `_buildWidgets()` no longer sets `.label` on it at all; `name:
 *    'set'` alone still carries every reload/lookup guarantee this bullet
 *    documents, unchanged.)
 *  - Per-widget custom colors (delete-button arm indicator, added for the
 *    2026-07-18c delete-bug fix, see `_armDeleteButtonColor()`; 2026-07-21:
 *    that function no longer uses this technique — the delete button is now
 *    a real DOM `<button>` with freely-settable `style` properties, so no
 *    getter-shadowing is needed there anymore. This bullet remains an
 *    accurate general LITEGRAPH BINDING for any canvas widget that still
 *    needs it — `status` below does not need colors, so nothing else in
 *    this file uses this technique post-redesign either):
 *    `background_color`/`text_color`/`outline_color` on `BaseWidget` are
 *    GETTER-ONLY accessors reading global theme constants (BaseWidget.ts:
 *    222-244, e.g. `get background_color() { return litegraph().WIDGET_BGCOLOR }`).
 *    The constructor even destructures and DISCARDS those exact key names
 *    off whatever's passed to `addWidget()` (lines 175-200, comment "Prevent
 *    naming conflicts with custom nodes") — passing them as widget options
 *    is a silent no-op by design, and a plain `widget.background_color = x`
 *    assignment would THROW if it ever reached the instance (getter-only
 *    accessor, strict-mode ES module semantics). `Object.defineProperty` is
 *    a different operation (`[[DefineOwnProperty]]`, not `[[Set]]`) and CAN
 *    still shadow an inherited accessor with an own one; `ButtonWidget`'s
 *    `drawWidget`/`drawLabel` (widgets/ButtonWidget.ts:24-59) read
 *    `this.background_color`/`this.text_color` at draw time, so an own
 *    accessor defined directly on one button instance is picked up on the
 *    very next repaint with no need to override `drawWidget` itself, and
 *    `delete`-ing that own property (`configurable: true`) restores the
 *    original theme getter exactly. VERIFY(live).
 *  - Save/restore ordering hazard (LGraphNode.ts:912-936 restore vs 982-994
 *    save): save writes `widgets_values[i]` at each widget's OWN index and
 *    leaves a hole where `serialize===false`; restore instead walks a
 *    SEPARATE counter that only advances past non-skipped widgets. These two
 *    only agree if every `serialize:false` widget sits AFTER every normally
 *    serialized widget — an interleaved layout would misread values on
 *    reload. This is why `target`/`set`/`name` are declared first and
 *    `status` + the two-pane DOM widget (both `serialize:false`) are
 *    declared last. Do not reorder without re-checking this. History: 4
 *    canvas buttons → 3 (2026-07-18 owner change removed the standalone
 *    Apply button, see `_onSetSelected`) → 4 (2026-07-19, Push State added,
 *    see `_doPush`) → 0 (2026-07-21: all 4 moved into the two-pane DOM
 *    widget's right pane as real `<button>` elements, see the file header's
 *    2026-07-21 section — they no longer participate in this ordering
 *    invariant at all; only `status` and the DOM widget itself still do).
 *  - `ComboWidget` supports `options.values` as a function
 *    (ComboWidget.ts:59-64, `getValues()`) — but it's deprecated as of
 *    v0.14.5 and logs a console warning on every dropdown open
 *    (ComboWidget.ts:126-135: "Using a function for values is deprecated.").
 *    It is still fully functional (this is the same pattern the deprecation
 *    message itself cites from ComfyUI-KJNodes), so we use it for `target`
 *    (cheap, pure in-memory graph scan — safe to re-run on every call).
 *    VERIFY(live): a future litegraph release could remove this path
 *    outright. (2026-07-21: `set` no longer uses this at all — it is a
 *    hidden plain `'text'` widget now, not a combo; the two-pane DOM
 *    widget's list reads `_setsCache` directly, with no `ComboWidget`/
 *    `getValues()` involved for state selection anymore.)
 *  - `LiteGraph`/`LGraphNode` are ambient globals in real, currently-shipping
 *    custom-node JS (not importable via any stable path from a node pack's
 *    own web dir) — confirmed by rgthree's OWN shipped code using them
 *    unimported: `LiteGraph.ContextMenu`/`LiteGraph.WIDGET_TEXT_COLOR` in
 *    power_lora_loader.js, `LiteGraph.NODE_WIDGET_HEIGHT` in bookmark.js:34,
 *    `LiteGraph.registerNodeType` in node_collector.js/base_node.js. Prior
 *    art for virtual-node registration itself: ComfyUI_frontend's
 *    `src/extensions/core/noteNode.ts` (`class X extends LGraphNode`,
 *    `this.isVirtualNode = true`, `LiteGraph.registerNodeType(name, X)`).
 *  - Combo callbacks do NOT fire during workflow restore. `configure()`
 *    restores `widgets_values` via a plain `widget.value = info.widgets_values[i++]`
 *    assignment (LGraphNode.ts:928-935). `ComboWidget`/`BaseSteppedWidget`
 *    (widgets/ComboWidget.ts, widgets/BaseSteppedWidget.ts) do not override
 *    `value`, so that assignment resolves to `BaseWidget`'s plain property
 *    setter (widgets/BaseWidget.ts:131-133: `this._state.value = value`) —
 *    NOT `setValue()` (BaseWidget.ts:416-436), the ONLY place `callback` is
 *    invoked (line 432). A saved workflow reopening can therefore never
 *    silently re-apply a set, no matter which mechanism drives the live
 *    pick. 2026-07-19c: since selection now goes through
 *    `_hookSetWidgetMenu()`'s `onClick` override (see the top-of-file
 *    2026-07-19c section) instead of the combo's `callback` option at all,
 *    this guarantee is now STRUCTURAL rather than belt-and-suspenders —
 *    restore literally cannot reach `_onSetPicked()`, so the old
 *    `_isRestoring`/`_silentSetWrite`/`configure()`-override guards (which
 *    existed only to stop a programmatic write from being mistaken for a
 *    callback-driven pick) were removed as dead code, not merely unused.
 *    `_setSetValueSilently()` survives as a plain, unguarded `.value =`
 *    helper — a display-only write, same as this bullet's finding always
 *    said it safely could be.
 *  - `widget.hidden` is a first-class, purpose-built hiding mechanism in
 *    this fork — NOT the same thing as the `.disabled` trick noted above
 *    for `status`. `LGraphNode.isWidgetVisible()` (LGraphNode.ts:3935-3939)
 *    and `getLayoutWidgets()` (3941-3947, "Filters out hidden widgets only
 *    ... for layout calculations") both branch on `.hidden`; `computeSize()`
 *    (line 1820) and `_arrangeWidgets()` (line 4166's `visibleWidgets`,
 *    consumed at 4206-4210 for both Y-position and total height) build
 *    exclusively off that filtered list. So `widget.hidden = true` removes
 *    a widget from drawing AND layout AND size — later widgets shift up,
 *    the node's natural height shrinks — a real hide, not a value-blank.
 *    `drawNode()` calls `node.arrange()` unconditionally every frame
 *    (LGraphCanvas.ts:5730), so toggling `.hidden` + `setDirtyCanvas(true,
 *    true)` is sufficient; no manual resize bookkeeping like
 *    `applySetToTarget()` needs for the (foreign, not-auto-arranging-for-
 *    our-purposes) PLL target. VERIFY(live) — DONE on Eric's rig
 *    (comfyui-test, port 8199): toggling the Properties Panel's "Show
 *    status" row live-flips `.hidden`, the row appears/disappears with no
 *    dead space, and the node redraws immediately — matches this reading
 *    exactly.
 *  - `LGraphNode.addProperty(name, default, type)` (LGraphNode.ts:1624-1638)
 *    pushes an `INodePropertyInfo` onto `properties_info` and seeds
 *    `this.properties[name]`. The right-click "Properties Panel" reads it
 *    back via `getPropertyInfo()` (1905-1934, matches by `name` in
 *    `properties_info`) and keys its editor widget off `info.type`
 *    (LGraphCanvas.ts ~8408-8416: `panel.addWidget(info.widget || info.type,
 *    pName, value, info, fUpdate)`) — passing `'boolean'` is what makes
 *    "Show status" a checkbox row rather than free text. `onPropertyChanged`
 *    fires from two places: user edits via `setProperty()` (LGraphNode.ts:
 *    1061-1081) and workflow restore via `configure()`'s OWN properties loop
 *    (842-850, `this.onPropertyChanged?.(k, info.properties[k])`) — the
 *    latter is exactly what we want here (a saved "Show status: true"
 *    should reveal the widget immediately on load), which is the opposite
 *    conclusion from the `set`-combo finding above; the two are unrelated
 *    code paths (property restore vs. widget-value restore) and both were
 *    read, not assumed.
 */

import { app } from '../../../scripts/app.js'
import * as api from './api.js'

// ---------------------------------------------------------------- constants

const NODE_TYPE = 'LoraLibrarySetController'
const NODE_TITLE = 'Lora Loader State Controller'
const NODE_CATEGORY = 'EPSNodes'

/** Exact rgthree type/title/comfyClass string — constants.js addRgthree("Power Lora Loader"). */
const POWER_LORA_LOADER_TYPE = 'Power Lora Loader (rgthree)'
/** Per-node property (not per-row) that picks single vs dual strength mode. */
const PROP_SHOW_STRENGTHS = 'Show Strengths'
const PROP_SHOW_STRENGTHS_DUAL = 'Separate Model & Clip'
/** rgthree row widgets are named lora_<counter>; counter never reuses numbers. */
const LORA_ROW_NAME_RE = /^lora_\d+$/

/**
 * FORMAT.md §6.3 Push State: the Apply LoRA Set node's class id + its `set`
 * widget's internal name (lora_library/nodes_sets.py `LoraLibraryApplySet`;
 * web/lora_library/sets.js `WIDGET_NAME`) — same literals, kept in sync by
 * hand since neither file imports the other.
 */
const APPLY_SET_NODE_CLASS = 'LoraLibraryApplySet'
const APPLY_SET_WIDGET_NAME = 'set'
/**
 * FORMAT.md §6.2/§4.1 (2026-07-20 composite fix): the Apply LoRA Set node's
 * `loader_slot` widget name — a real, Python-declared widget
 * (lora_library/nodes_sets.py INPUT_TYPES), hidden by default by the
 * sibling `sets.js` behind its own `Show loader slot` property. Same
 * "kept in sync by hand, no cross-module import" convention as
 * `APPLY_SET_WIDGET_NAME` above.
 */
const APPLY_SET_LOADER_SLOT_WIDGET_NAME = 'loader_slot'

/**
 * FORMAT.md §6.2 `mirrors loader` tag — web/lora_library/sets.js's
 * frontend-only widget name + its "no PLL selected" default value. Same
 * "kept in sync by hand" convention as the two constants above (neither
 * file imports the other — see sets.js's file header).
 */
const MIRRORS_WIDGET_NAME = 'mirrors loader'
const MIRRORS_ANY_VALUE = '(any)'

/** FORMAT.md §6.3: our OWN node property — default false, revealed via right-click Properties. */
const PROP_SHOW_STATUS = 'Show status'
/**
 * 2026-07-20 (round 10) diagnostics property — default false, revealed via
 * right-click Properties. When on, every capture additionally
 * `console.table()`s each row's full raw `.value` + `serializeValue()`
 * objects (see `captureRows()`). The compact one-line-per-row
 * `console.debug` trace runs unconditionally regardless of this property —
 * this only adds the deeper dump for a report the compact trace can't
 * resolve on its own.
 */
const PROP_DEBUG_CAPTURE = 'Debug capture'

/**
 * FORMAT.md §6.3 multi-target: label prefix + matching regex for the "All
 * Power Lora Loaders (N)" target-combo entry. Keep these two in sync by
 * hand (formatAllTargetsLabel() below is the only writer) — there's no
 * runtime derivation of one from the other.
 */
const ALL_TARGETS_LABEL_PREFIX = 'All Power Lora Loaders'
const ALL_TARGETS_RE = /^All Power Lora Loaders \(\d+\)$/

/**
 * Button labels (owner, 2026-07-18c rename). Identifier names below stay
 * put — same freeze pattern as NODE_TYPE and the `set` widget's `name`
 * (FORMAT.md §6.3) — only the displayed strings follow the state vocabulary.
 */
const LABEL_CAPTURE = 'New State'
const LABEL_UPDATE = 'Save State'
const LABEL_DELETE = 'Delete State'
const LABEL_PUSH = 'Push State'
const LABEL_DELETE_CONFIRM = 'Are you sure?'
const DELETE_CONFIRM_MS = 4000
/**
 * Delete-armed visual (2026-07-18c delete-bug fix): a distinct color on the
 * button itself, on top of the label swap above, so the two-step reads as
 * "armed" even during a constantly-redrawing active queue — see
 * `_armDeleteButtonColor()`. Since the 2026-07-21 two-pane redesign the
 * delete button is a real DOM `<button>` element, so applying this is a
 * plain `button.style.*` assignment; before that it needed
 * `Object.defineProperty` to shadow litegraph's getter-only canvas-widget
 * color accessors (file header's per-widget-color citation has that
 * history).
 */
const DELETE_ARMED_BG_COLOR = '#8b2020'
const DELETE_ARMED_TEXT_COLOR = '#ffffff'

/** onDrawForeground fires on every canvas redraw; throttle our own work. */
const HEARTBEAT_MIN_MS = 1000
const SETS_POLL_MS = 4000
/** Belt-and-suspenders cap so a future rgthree shape change can never spin us forever. */
const MAX_ROW_ADJUST_STEPS = 500

const PLACEHOLDER_NO_TARGET = '(none found)'
const PLACEHOLDER_NO_SETS = '(no states saved)'

const MSG_NO_RGTHREE = 'Install rgthree-comfy, or use Apply LoRA Set instead'
const MSG_SHAPE_DRIFT = 'Power Lora Loader internals changed — controller disabled (v-check)'
const MSG_NO_TARGET_IN_GRAPH =
  'No Power Lora Loader (rgthree) node in this graph yet — add one, then pick it above.'
const MSG_NO_TARGET_SELECTED = 'Pick a target Power Lora Loader node above.'

// ------------------------------------------------------- pure graph helpers
// (No `this` — these only ever read/write a passed-in node, so probe/capture/
// apply can be reasoned about and, if needed, exercised independently of the
// widget/UI plumbing below.)

/** Every live `Power Lora Loader (rgthree)` instance in the current graph. */
function findTargetCandidates() {
  const nodes = app.graph?._nodes || app.graph?.nodes || []
  const out = []
  for (const node of nodes) {
    if (node && node.type === POWER_LORA_LOADER_TYPE) {
      out.push({ id: node.id, node, label: `${node.title || node.type} #${node.id}` })
    }
  }
  return out
}

/**
 * FORMAT.md §6.2/§6.3: the node id embedded in a "<title> #<id>" combo
 * label — the shape both this file's `target` combo and sets.js's `mirrors
 * loader` tag use for the same underlying concept (which PLL). `null` for
 * anything without that suffix ("(any)", "(none found)", "All Power Lora
 * Loaders (N)").
 */
function pllIdFromLabel(label) {
  const match = /#(-?\d+)\s*$/.exec(String(label || ''))
  return match ? match[1] : null
}

/** Resolve a combo label ("<title> #<id>") back to a live node, or null. */
function resolveTargetNode(label) {
  const id = pllIdFromLabel(label)
  if (id == null) return null
  const nodes = app.graph?._nodes || app.graph?.nodes || []
  return nodes.find((n) => n && String(n.id) === id && n.type === POWER_LORA_LOADER_TYPE) || null
}

/** FORMAT.md §6.3: the target combo's multi-target entry text, e.g. "All Power Lora Loaders (2)". */
function formatAllTargetsLabel(count) {
  return `${ALL_TARGETS_LABEL_PREFIX} (${count})`
}

/**
 * Resolve the target combo's current value to ZERO OR MORE live nodes.
 * Everywhere that used to call `resolveTargetNode` (singular) now calls
 * this instead, so probe/capture/apply are written once against an array
 * and don't need to know whether "All" is in play.
 *
 * The "All" case returns every candidate sorted by ASCENDING node id —
 * FORMAT.md §6.3: "CAPTURE reads from the lowest-node-id PLL", so callers
 * that need that one specific node just take `nodes[0]`.
 */
function resolveTargetNodes(label) {
  if (!label) return []
  if (ALL_TARGETS_RE.test(String(label))) {
    return findTargetCandidates()
      .sort((a, b) => a.id - b.id)
      .map((c) => c.node)
  }
  const single = resolveTargetNode(label)
  return single ? [single] : []
}

/**
 * FORMAT.md §4.1: the lora rows a state stores for loader index `index`.
 * Frontend mirror of `sets_store.py`'s `loras_for_slot()` — same
 * "kept in sync by hand, no cross-language import" convention as every
 * other duplicated-on-purpose constant/helper in this file (see file
 * header). Format-2 `setData` (`loaders` present, non-empty) returns
 * `loaders[clamp(index)].loras`; anything else (format-1, or a
 * malformed/missing `loaders`) returns the top-level `loras` array. Never
 * throws; clamps into range; empty-safe.
 */
function lorasForLoaderIndex(setData, index) {
  const loaders = setData?.loaders
  if (Array.isArray(loaders) && loaders.length) {
    const i = Math.max(0, Math.min(Number(index) || 0, loaders.length - 1))
    const loras = loaders[i]?.loras
    return Array.isArray(loras) ? loras : []
  }
  return Array.isArray(setData?.loras) ? setData.loras : []
}

/**
 * FORMAT.md §6.3 (2026-07-20 §4.1 composite extension): `node`'s rank
 * (0-based) among EVERY live PLL in the graph, sorted ascending by id —
 * "the slice for THAT loader's ascending-id index among the graph's PLLs"
 * that a single-PLL-target APPLY of a composite state uses (see
 * `_doApply()`). Deliberately scans the WHOLE graph
 * (`findTargetCandidates()`), not just whatever `resolveTargetNodes()`
 * returned for the current target — a single target's rank among ALL PLLs
 * is a different number than its rank within a 1-element array (always 0).
 * Falls back to 0 (never -1) if `node` isn't found among the candidates for
 * any reason (e.g. removed between resolve and apply) — degrades to
 * loader 0 rather than throwing.
 */
function pllAscendingIndex(node) {
  const candidates = findTargetCandidates().sort((a, b) => a.id - b.id)
  const index = candidates.findIndex((c) => c.node === node)
  return index === -1 ? 0 : index
}

/**
 * Like `pllAscendingIndex()` above but starting from a PLL's node id
 * (string or number) rather than a live node reference — used where only
 * the id is at hand, e.g. a `mirrors loader` tag's embedded id (FORMAT.md
 * §6.2), see `pushLoaderSlotForTag()`. `null` when `id` is
 * `null`/`undefined` or doesn't match any live PLL (never guesses 0 here,
 * unlike `pllAscendingIndex()` — the caller needs to tell "no specific PLL"
 * apart from "the first PLL").
 */
function pllAscendingIndexById(id) {
  if (id == null) return null
  const candidates = findTargetCandidates().sort((a, b) => a.id - b.id)
  const index = candidates.findIndex((c) => String(c.id) === String(id))
  return index === -1 ? null : index
}

/**
 * Scan a target's widgets for lora rows. `named` = everything that LOOKS like
 * a row by name (FORMAT.md §6.3: `/^lora_\d+$/`); `rows` = the subset whose
 * `.value` is actually an object with a `lora` key. `named.length !== rows.length`
 * is the shape-drift signal (a row-shaped name with a value that doesn't look
 * like rgthree's row shape) — zero rows found at all is a normal, healthy
 * "empty PLL", not drift.
 */
function scanLoraRows(node) {
  const named = []
  const rows = []
  const widgets = (node && node.widgets) || []
  for (const widget of widgets) {
    if (!widget || typeof widget.name !== 'string' || !LORA_ROW_NAME_RE.test(widget.name)) continue
    named.push(widget)
    const v = widget.value
    if (v && typeof v === 'object' && 'lora' in v) rows.push(widget)
  }
  return { named, rows }
}

/** rgthree registers the PLL type with LiteGraph iff it's installed and loaded. */
function isRgthreeInstalled() {
  return (
    typeof LiteGraph !== 'undefined' &&
    !!(LiteGraph.registered_node_types && LiteGraph.registered_node_types[POWER_LORA_LOADER_TYPE])
  )
}

/**
 * Single feature-detection gate every rgthree interaction goes through
 * (FORMAT.md §6.3: "probe first, mutate after"). Never partially mutates
 * anything — it only reads. Single-node; `probeTargets()` below is the
 * multi-target-aware wrapper the UI layer actually calls.
 */
function probeTarget(node) {
  if (!isRgthreeInstalled()) {
    return { ok: false, code: 'no-rgthree', message: MSG_NO_RGTHREE }
  }
  if (!node) {
    const hasAny = findTargetCandidates().length > 0
    return {
      ok: false,
      code: hasAny ? 'no-target-selected' : 'no-target-in-graph',
      message: hasAny ? MSG_NO_TARGET_SELECTED : MSG_NO_TARGET_IN_GRAPH
    }
  }
  if (node.type !== POWER_LORA_LOADER_TYPE) {
    return { ok: false, code: 'wrong-type', message: MSG_NO_TARGET_SELECTED }
  }
  // Capability check, not a version check (FORMAT.md §6.3: "feature detection,
  // not version pinning"). addNewLoraWidget is rgthree's own row-add method
  // (power_lora_loader.js:85-94); removeWidget/computeSize are standard
  // LGraphNode API every genuine node inherits.
  if (
    typeof node.addNewLoraWidget !== 'function' ||
    typeof node.removeWidget !== 'function' ||
    typeof node.computeSize !== 'function' ||
    !Array.isArray(node.widgets)
  ) {
    return { ok: false, code: 'shape-drift', message: MSG_SHAPE_DRIFT }
  }
  const { named, rows } = scanLoraRows(node)
  if (named.length !== rows.length) {
    return { ok: false, code: 'shape-drift', message: MSG_SHAPE_DRIFT }
  }
  return {
    ok: true,
    code: 'ok',
    message: `Ready — target has ${rows.length} row${rows.length === 1 ? '' : 's'}.`,
    rowCount: rows.length
  }
}

/**
 * Multi-target probe (FORMAT.md §6.3 amendment): `nodes` is whatever
 * `resolveTargetNodes()` returned — 0, 1, or (with "All…" selected) every
 * PLL in the graph. "probe requires ALL targets healthy" — the first
 * unhealthy node wins and its identity is folded into the message ("any
 * shape-drift disables with a message naming the offending node"); this
 * still degrades to plain `probeTarget()` behavior for the single-target
 * (N<=1) case, including the exact no-rgthree/no-target-selected/
 * no-target-in-graph messages, since the empty/rgthree checks run first
 * and unchanged.
 */
function probeTargets(nodes) {
  if (!isRgthreeInstalled()) {
    return { ok: false, code: 'no-rgthree', message: MSG_NO_RGTHREE }
  }
  if (!nodes || nodes.length === 0) {
    const hasAny = findTargetCandidates().length > 0
    return {
      ok: false,
      code: hasAny ? 'no-target-selected' : 'no-target-in-graph',
      message: hasAny ? MSG_NO_TARGET_SELECTED : MSG_NO_TARGET_IN_GRAPH
    }
  }
  for (const node of nodes) {
    const single = probeTarget(node)
    if (!single.ok) {
      const named = node.title || node.type
      const message = single.code === 'shape-drift' ? `${single.message} — ${named} #${node.id}` : single.message
      return { ...single, message }
    }
  }
  const rowCount = nodes.reduce((sum, node) => sum + scanLoraRows(node).rows.length, 0)
  return {
    ok: true,
    code: 'ok',
    message:
      nodes.length > 1
        ? `Ready — ${nodes.length} targets healthy (${rowCount} row${rowCount === 1 ? '' : 's'} total).`
        : `Ready — target has ${rowCount} row${rowCount === 1 ? '' : 's'}.`,
    rowCount
  }
}

/**
 * 2026-07-20 (round 10) — read one row's value from BOTH sources: the raw
 * `widget.value` this file has always read, and rgthree's own
 * `serializeValue(node, index)` where the widget defines one (see the file
 * header's dated section for the full evidence trail on why the latter is
 * the more version-proof source: it's the exact seam ComfyUI_frontend's own
 * `executionUtil.ts` prefers over raw `.value`, and rgthree's
 * `PowerLoraLoaderWidget` defines one that returns an always-plain snapshot,
 * `{...this.value}`). `serializeValue` is awaited defensively — its declared
 * type (ComfyUI_frontend's `simplifiedWidget.ts`) allows returning a
 * `Promise`, even though rgthree's own implementation today is synchronous.
 * Never throws: a `serializeValue` that throws or returns something unusable
 * just falls back to `liveValue` alone, so this can only ever make a read
 * MORE forgiving, never less, exactly like the fallback-chain widening below
 * it. `nodeIndex` (the widget's real position in `node.widgets`, not its
 * position among lora rows) is what `serializeValue(node, index)` actually
 * expects per its signature, even though rgthree's current implementation
 * ignores both arguments.
 */
async function readRowSources(node, widget, nodeIndex) {
  const liveValue = (widget && widget.value) || {}
  let serialized = null
  if (typeof widget?.serializeValue === 'function') {
    try {
      serialized = await widget.serializeValue(node, nodeIndex)
    } catch (error) {
      api.warn(`${NODE_TITLE}: ${widget.name}.serializeValue() threw; falling back to .value`, error)
    }
  }
  const usedSerialize = serialized != null && typeof serialized === 'object'
  // serializeValue's fields win when present; anything it omits (e.g. a
  // deleted `strengthTwo` in single-strength mode) still falls through to
  // liveValue, so the alias-chain widening below sees a merged object, not
  // a partial one.
  const merged = usedSerialize ? { ...liveValue, ...serialized } : liveValue
  return { liveValue, serialized, merged, usedSerialize }
}

/**
 * `Number()`, NaN -> `fallback` (FORMAT.md §6.3 2026-07-20 hardening): a
 * strength that survives every alias in the fallback chain but still isn't a
 * finite number (an entirely unanticipated shape — not something either the
 * owner's or this rig's rgthree has ever been observed to produce) must
 * never be written into a saved state file silently. Named by ROW so a
 * console.warn here is actionable without needing `Debug capture` on.
 */
function coerceStrength(raw, fallback, rowName, fieldName) {
  if (raw == null) return fallback
  const n = Number(raw)
  if (Number.isNaN(n)) {
    api.warn(
      `${NODE_TITLE}: ${rowName}.${fieldName} read a non-numeric strength (${JSON.stringify(raw)}); using ${fallback}`
    )
    return fallback
  }
  return n
}

/**
 * CAPTURE (FORMAT.md §6.3 + §4). Only called after probeTargets().ok (which
 * runs probeTarget() over every node in play), so every row here is already
 * known to have the expected `{on, lora, strength, strengthTwo}`-ish shape.
 * nd-super-nodes' `{enabled, strengthClip}` aliases are read (never written).
 * 2026-07-19c: widened the fallback chain per field (snake_case spellings +
 * a couple of other plausible fork property names) — the owner's installed
 * rgthree may not match this rig's exact shape (confirmed correct on THIS
 * rig's rgthree via a real pointer drag, see file header), and every
 * fallback here is read-only/additive, so it can only make capture MORE
 * forgiving, never change behavior on a normal rgthree row.
 * `_toastRowsSaved()`'s read-back is the other half of "robust and
 * observable" (FORMAT.md §6.3): if even this widened chain still reads the
 * wrong thing on the owner's fork, the save toast shows it plainly.
 * 2026-07-20: now reads `serializeValue()` alongside `.value` per row (see
 * `readRowSources()`) and coerces every strength through `Number()` (see
 * `coerceStrength()`) — `async` as a result (`serializeValue` may return a
 * Promise); both call sites already `await` other calls in the same
 * function, so this is a non-breaking signature change internal to this
 * file. `debugCapture` (FORMAT.md §6.3 2026-07-20 diagnostics, the "Debug
 * capture" node property) additionally `console.table()`s every row's full
 * raw sources; the compact one-line-per-row `console.debug` trace below runs
 * unconditionally either way.
 */
async function captureRows(node, { debugCapture = false } = {}) {
  const { rows } = scanLoraRows(node)
  const out = []
  const debugRows = debugCapture ? [] : null
  for (const widget of rows) {
    const nodeIndex = node.widgets.indexOf(widget)
    const { liveValue, serialized, merged, usedSerialize } = await readRowSources(node, widget, nodeIndex)
    const v = merged
    if (v.lora == null || v.lora === 'None') continue
    const strength = coerceStrength(v.strength ?? v.strengthOne ?? v.strength_model, 1, widget.name, 'strength')
    const rawClip = v.strengthTwo ?? v.strength_two ?? v.strengthClip ?? v.strength_clip
    const strength_clip = rawClip == null ? null : coerceStrength(rawClip, strength, widget.name, 'strength_clip')
    out.push({ file: v.lora, on: v.on ?? v.enabled ?? v.active ?? true, strength, strength_clip })

    // Always-on, compact per-row trace: which source won, and what each one
    // said the strength was — so the NEXT mismatch report already has this
    // in the console with no property to flip first.
    console.debug(
      `${NODE_TITLE}: captured ${widget.name} — value.strength=${liveValue?.strength} ` +
        `serializeValue.strength=${usedSerialize ? serialized?.strength : '(n/a)'} -> ${strength}` +
        `${usedSerialize ? ' [serializeValue]' : ' [value]'}`
    )
    if (debugRows) {
      debugRows.push({
        row: widget.name,
        lora: v.lora,
        strength,
        strength_clip,
        usedSerialize,
        value: liveValue,
        serializeValue: serialized
      })
    }
  }
  if (debugRows) {
    try {
      console.table(debugRows)
    } catch {
      console.debug(`${NODE_TITLE}: Debug capture rows`, debugRows)
    }
  }
  return out
}

/** Basename minus extension, either separator — same rule FORMAT.md §4 uses for lora resolution. */
function stemOf(file) {
  const base = String(file || '')
    .split(/[\\/]/)
    .pop()
  const dot = base.lastIndexOf('.')
  return dot > 0 ? base.slice(0, dot) : base
}

/**
 * FORMAT.md §6.3 read-back toast (2026-07-19c): human-readable row summary
 * for a saved-state toast, e.g. `detailer 0.8, film_grain 1.2`. Deliberately
 * NOT the Apply LoRA Set node's `loras_text` format (FORMAT.md §6.2 —
 * underscore-joined, filename-safe tokens): this is comma-separated prose
 * for a toast, always shows BOTH strengths when they differ (a
 * dual-strength state applied against a single-strength target, or vice
 * versa, is exactly the kind of mismatch this toast exists to surface), and
 * flags an `off` row explicitly, since a silently-skipped disabled row is
 * otherwise invisible.
 */
function summarizeRowsForToast(rows) {
  if (!Array.isArray(rows) || !rows.length) return '(no rows)'
  return rows
    .map((row) => {
      const stem = stemOf(row.file)
      const strength = row.strength ?? 1
      const clip = row.strength_clip
      const strengthText = clip != null && clip !== strength ? `${strength}/${clip}` : `${strength}`
      return row.on === false ? `${stem} ${strengthText} (off)` : `${stem} ${strengthText}`
    })
    .join(', ')
}

/**
 * APPLY (FORMAT.md §6.3). Rewrites `node`'s rows to match `setData.loras`
 * exactly: count, order, on/off, strengths. Missing-on-this-machine loras are
 * still written (rgthree shows its own missing-lora state — that's the
 * user's ground truth, not ours to hide).
 *
 * Single vs dual strength decision (documented per the task's explicit ask):
 * `strength_clip: null` only becomes a real `strengthTwo` value when the
 * TARGET NODE is currently in dual mode (`properties["Show Strengths"] ===
 * "Separate Model & Clip"`, power_lora_loader.js:15-20/28/405). In single
 * mode we leave `strengthTwo: null` untouched rather than inventing a value —
 * we deliberately do NOT flip the target's mode to fit the set; the property
 * is the target's own presentation choice, not something a set should
 * override. This means a set captured in dual mode can lose visible
 * per-clip-strength fidelity if applied to a target left in single mode;
 * that's accepted, spec'd behavior, not a bug.
 */
/**
 * Announce a set CRUD to the rest of the pack (FORMAT.md §7.4) — sets.js
 * listens and refreshes every Apply LoRA Set combo. A DOM event rather than
 * an import keeps the two modules decoupled: either can ship/fail alone.
 */
function announceSetsChanged() {
  try {
    window.dispatchEvent(new CustomEvent('lora_library:sets-changed'))
  } catch {
    // Announcement is a nicety; CRUD success must not depend on it.
  }
}

function applySetToTarget(node, setData) {
  const desired = Array.isArray(setData?.loras) ? setData.loras : []
  const dualMode = !!(node.properties && node.properties[PROP_SHOW_STRENGTHS] === PROP_SHOW_STRENGTHS_DUAL)

  let { rows: current } = scanLoraRows(node)
  let steps = 0
  // Grow: rgthree's own addNewLoraWidget() (power_lora_loader.js:85-94) both
  // creates the widget AND repositions it just before the "+ Add Lora"
  // button spacer — no extra bookkeeping needed on our side.
  while (current.length < desired.length && steps++ < MAX_ROW_ADJUST_STEPS) {
    node.addNewLoraWidget()
    current = scanLoraRows(node).rows
  }
  // Shrink: remove extras from the tail via the official LGraphNode API
  // (ComfyUI_frontend src/lib/litegraph/src/LGraphNode.ts:2037-2058), which
  // takes a WIDGET REFERENCE (not an index — VERIFY(live), see file header).
  // Every remaining row's `.value` gets fully overwritten below regardless,
  // so which specific rows we drop doesn't matter — tail-removal is simplest.
  steps = 0
  while (current.length > desired.length && steps++ < MAX_ROW_ADJUST_STEPS) {
    const widget = current.pop()
    try {
      node.removeWidget(widget)
    } catch (error) {
      const idx = node.widgets.indexOf(widget)
      if (idx !== -1) node.widgets.splice(idx, 1)
    }
  }

  // Whole-object assignment through the widget's `value` setter — the same
  // pattern rgthree's own configure() uses (power_lora_loader.js:65-70,
  // 386-395), so getLoraInfo() etc. fire exactly as they would on workflow load.
  current = scanLoraRows(node).rows
  for (let i = 0; i < desired.length; i++) {
    const row = desired[i] || {}
    current[i].value = {
      on: row.on ?? true,
      lora: row.file,
      strength: row.strength ?? 1,
      strengthTwo: dualMode ? (row.strength_clip ?? row.strength ?? 1) : null
    }
  }

  // Redraw/resize exactly as rgthree's own onNodeCreated (power_lora_loader.js:78-83) —
  // not the configure()-only `_tempHeight` fallback (line 73), which may not
  // exist on a node that was only ever built via "+ Add Lora" clicks.
  const computed = node.computeSize()
  node.size[0] = Math.max(node.size[0], computed[0])
  node.size[1] = Math.max(node.size[1], computed[1])
  node.setDirtyCanvas(true, true)
}

/**
 * Multi-target APPLY (FORMAT.md §6.3 amendment, extended 2026-07-20 for
 * §4.1 composites): `applySetToTarget()` only READS `setData` per node
 * (builds a fresh `.value` object per row; never mutates the set or any row
 * in place), so reusing/reslicing the same `setData` across multiple
 * targets here is always safe — no cloning needed.
 *
 * - `setData` format-1 (no usable `loaders`): the exact pre-existing
 *   behavior — the same `loras` applied to every node in `nodes`.
 * - `setData` format-2 (`loaders` present, non-empty) AND `nodes.length > 1`
 *   ("All…" target, §6.3): `loaders[i]` -> `nodes[i]`, by ascending id
 *   (guaranteed by `resolveTargetNodes()`'s sort before `nodes` gets here).
 *   Fewer loaders than nodes -> the extra trailing nodes are left untouched
 *   (FORMAT.md §6.3: "never guess"); the caller toasts the mismatch (see
 *   `_doApply()`).
 * - `setData` format-2 AND `nodes.length === 1` with `loaderIndexForSingle`
 *   given (a single-PLL target, §6.3): applies ONLY
 *   `loaders[clamp(loaderIndexForSingle)]` to that one node — `_doApply()`
 *   computes the index as the target's ascending-id RANK AMONG EVERY PLL IN
 *   THE GRAPH (via `pllAscendingIndex()`), per FORMAT.md §6.3's "uses the
 *   slice for THAT loader's ascending-id index among the graph's PLLs."
 */
function applySetToTargets(nodes, setData, { loaderIndexForSingle } = {}) {
  const loaders = setData?.loaders
  const isComposite = Array.isArray(loaders) && loaders.length > 0
  if (isComposite && nodes.length === 1 && loaderIndexForSingle != null) {
    applySetToTarget(nodes[0], { loras: lorasForLoaderIndex(setData, loaderIndexForSingle) })
    return
  }
  if (isComposite && nodes.length > 1) {
    for (let i = 0; i < nodes.length; i++) {
      if (i >= loaders.length) continue // fewer loaders than PLLs -> leave untouched (§6.3)
      applySetToTarget(nodes[i], { loras: lorasForLoaderIndex(setData, i) })
    }
    return
  }
  for (const node of nodes) applySetToTarget(node, setData)
}

/**
 * FORMAT.md §6.3 Push State: every `LoraLibraryApplySet` node in the current
 * graph, found by `comfyClass` — the exact lookup the spec calls out, with
 * the same instance-or-constructor fallback `sets.js`'
 * `attachApplySetBehavior()` already uses for this same node type.
 */
function findApplySetNodes() {
  const nodes = app.graph?._nodes || app.graph?.nodes || []
  return nodes.filter((node) => (node?.comfyClass ?? node?.constructor?.comfyClass) === APPLY_SET_NODE_CLASS)
}

/**
 * Write `slug` to one Apply LoRA Set node's `set` widget through its REAL
 * setter — `BaseWidget.setValue()`, not a plain `.value =` assignment — so
 * its `callback`/`node.onWidgetChanged` fire exactly like a genuine user
 * pick — DELIBERATELY still `setValue()`, not the `onClick`-menu replacement
 * this file's OWN `set` combo now uses (file header 2026-07-19c): pushing a
 * slug the Apply node doesn't already show is the common case, so the old
 * same-value no-op essentially never bites here, and going through
 * `setValue()` is what makes the Apply node's REAL server-widget callback
 * (which re-reads its file) fire like a genuine pick. ApplySet's `set`
 * widget is a stock litegraph ComboWidget (built from the node's Python
 * INPUT_TYPES, never touched by this file otherwise); the verified
 * `setValue` mechanics this relies on are documented in the file header's
 * "Key ComfyUI_frontend litegraph bindings" section. Falls back to a
 * manual value+callback+onWidgetChanged sequence if `setValue` isn't there
 * (shape drift on some future core), so Push State degrades gracefully
 * instead of throwing. Always dirties the node's canvas so the combo's
 * on-screen text updates immediately.
 */
function pushStateToNode(node, slug) {
  const widget = (node.widgets || []).find((w) => w && w.name === APPLY_SET_WIDGET_NAME)
  if (!widget) return false
  if (typeof widget.setValue === 'function') {
    widget.setValue(slug, { node, canvas: app.canvas })
  } else {
    const old = widget.value
    widget.value = slug
    widget.callback?.(slug, app.canvas, node, undefined, undefined)
    node.onWidgetChanged?.(widget.name, slug, old, widget)
  }
  node.setDirtyCanvas(true, true)
  return true
}

/**
 * FORMAT.md §6.2/§6.3 selective Push (2026-07-19c): does `applyNode`'s own
 * `mirrors loader` tag (sets.js) match the controller's currently-selected
 * push scope? `pllId` is `null` when the controller's target isn't a single
 * specific PLL (no PLL in the graph at all — `selectPushTargets()` already
 * short-circuits the "All…" case before this function is ever consulted, so
 * `null` here specifically means "no single PLL is selected," never "push
 * everything"). An Apply node with NO tag widget at all (sets.js didn't
 * load, or a workflow saved before this feature existed) degrades to
 * "(any)" — always included — rather than silently dropping out of every
 * push just because the tagging feature happens to be unavailable on it.
 */
function mirrorsTagMatches(applyNode, pllId) {
  const widget = (applyNode.widgets || []).find((w) => w && w.name === MIRRORS_WIDGET_NAME)
  const tagValue = widget ? String(widget.value || '') : MIRRORS_ANY_VALUE
  if (tagValue === MIRRORS_ANY_VALUE) return true
  if (pllId == null) return false
  return pllIdFromLabel(tagValue) === pllId
}

/**
 * FORMAT.md §6.2/§6.3 selective Push: which of `findApplySetNodes()`'s
 * results the controller's raw `target` combo value should touch.
 * `targetValue` is read straight off `this._w.target.value` by the caller —
 * deliberately NOT `probeTargets()`/`resolveTargetNodes()` (rgthree
 * health): a push never touches rgthree and must keep working with rgthree
 * uninstalled or the target unhealthy, exactly as before this amendment.
 */
function selectPushTargets(targetValue) {
  const label = String(targetValue || '')
  if (ALL_TARGETS_RE.test(label)) return findApplySetNodes()
  const pllId = pllIdFromLabel(label)
  return findApplySetNodes().filter((node) => mirrorsTagMatches(node, pllId))
}

/** Push `slug` to every node in `nodes`; returns how many were touched. */
function pushStateToNodes(nodes, slug) {
  let count = 0
  for (const node of nodes) {
    if (pushStateToNode(node, slug)) count++
  }
  return count
}

/**
 * FORMAT.md §6.2/§6.3 (2026-07-20, §4.1 nice-to-have): when `node`'s
 * `mirrors loader` tag names a SPECIFIC PLL (not "(any)"), also set that
 * Apply node's `loader_slot` widget to the tagged PLL's ascending-id rank
 * among the graph's PLLs — so pushing a composite state to a WAN-style pair
 * of tagged Apply nodes lands each one on its own slice with zero manual
 * "reveal loader_slot and type 1" step. Best-effort and silent: a tag of
 * "(any)" (no natural single slot), a missing/never-tagged widget, a
 * `loader_slot` widget that isn't there (older workflow, or `sets.js`
 * failed to load), or a tag whose PLL id no longer resolves are all left
 * COMPLETELY ALONE — this never touches the `set` widget Push State's core
 * broadcast depends on, so it can never affect that broadcast even when
 * every early-return below fires. A plain `.value =` assignment (not
 * `setValue()`) is deliberate: `loader_slot` has no live-preview reason to
 * fire a callback (its only consumer is server-side `apply()` at the next
 * queue), and a plain assignment is the same restore-safe pattern this
 * file's own header cites for workflow-load (`configure()`'s
 * `widget.value = ...`) — see `pushStateToNode()`'s own fallback branch for
 * the same idiom applied to a widget WITH a meaningful callback.
 */
function pushLoaderSlotForTag(node) {
  const tagWidget = (node.widgets || []).find((w) => w && w.name === MIRRORS_WIDGET_NAME)
  const tagValue = tagWidget ? String(tagWidget.value || '') : MIRRORS_ANY_VALUE
  if (tagValue === MIRRORS_ANY_VALUE) return
  const index = pllAscendingIndexById(pllIdFromLabel(tagValue))
  if (index == null) return
  const slotWidget = (node.widgets || []).find(
    (w) => w && w.name === APPLY_SET_LOADER_SLOT_WIDGET_NAME
  )
  if (!slotWidget || slotWidget.value === index) return
  slotWidget.value = index
  node.setDirtyCanvas(true, true)
}

// ---------------------------------------------------- two-pane state picker
// FORMAT.md §6.3 TWO-PANE layout (owner ask 2026-07-21) / §7.2 pattern this
// mirrors. See the file header's 2026-07-21 section for the full design
// rationale; everything below is new DOM-construction code this file had
// none of before today.
// ---------------------------------------------------------------------------

/** DOM widget name/type for `addDOMWidget` — Notebook's own `WIDGET_NAME`/`WIDGET_TYPE` twin (web/lora_library/notebook.js). */
const STATE_PANE_WIDGET_NAME = 'states'
const STATE_PANE_WIDGET_TYPE = 'lora_library_state_picker'

/** FORMAT.md §7.2 "the widget fills available height" — Notebook's `MIN_WIDGET_HEIGHT` twin, sized for 4 stacked buttons plus a couple of visible list rows. */
const MIN_STATE_PANE_HEIGHT = 140

const STATE_PANE_STYLE_TAG_ID = 'lora-library-controller-styles'
let controllerStylesInjected = false

/**
 * Notebook's `CSS_TEXT` twin (web/lora_library/notebook.js) — same
 * ComfyUI theme variables with literal fallbacks, under an `llsc-` prefix
 * (Notebook uses `llnb-`) so the two injected `<style>` tags never collide
 * in the shared `document.head`.
 */
const STATE_PANE_CSS_TEXT = `
.llsc-root {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  box-sizing: border-box;
  overflow: hidden;
  background: var(--comfy-input-bg, #1e1e1e);
  border: 1px solid var(--border-color, #444);
  border-radius: 4px;
  font-family: inherit;
  font-size: 11px;
  color: var(--input-text, #ccc);
}
.llsc-panes {
  display: flex;
  flex-direction: row;
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.llsc-pane-left {
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  border-right: 1px solid var(--border-color, #444);
}
.llsc-pane-right {
  flex: 0 0 104px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 4px;
  overflow-y: auto;
}
.llsc-list {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 3px;
}
.llsc-row {
  padding: 4px 7px;
  margin: 1px 0;
  border-radius: 3px;
  border-left: 3px solid transparent;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  outline: none;
  user-select: none;
}
.llsc-row:hover { background: var(--content-hover-bg, #2a2a2a); }
.llsc-row:focus-visible { box-shadow: inset 0 0 0 1px var(--border-color, #444); }
.llsc-row-active,
.llsc-row-active:hover {
  background: rgba(66, 133, 244, 0.28);
  border-left-color: rgba(66, 133, 244, 1);
  font-weight: 600;
}
.llsc-empty {
  padding: 6px 7px;
  color: var(--descrip-text, #999);
  font-style: italic;
}
.llsc-btn {
  flex: 0 0 auto;
  box-sizing: border-box;
  width: 100%;
  background: var(--comfy-menu-bg, #262626);
  border: 1px solid var(--border-color, #444);
  color: var(--input-text, #ccc);
  border-radius: 4px;
  padding: 6px 4px;
  font-size: 11px;
  font-family: inherit;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.llsc-btn:hover:not(:disabled) { background: var(--content-hover-bg, #2a2a2a); }
.llsc-btn:disabled { opacity: 0.45; cursor: default; }
.llsc-btn-danger { border-color: var(--error-text, #ff4444); color: var(--error-text, #ff4444); }
`

function injectControllerStyles() {
  if (controllerStylesInjected) return
  controllerStylesInjected = true
  if (document.getElementById(STATE_PANE_STYLE_TAG_ID)) return
  const style = document.createElement('style')
  style.id = STATE_PANE_STYLE_TAG_ID
  style.textContent = STATE_PANE_CSS_TEXT
  document.head.appendChild(style)
}

/**
 * Tiny DOM builder, hand-copied from `web/lora_library/notebook.js`'s own
 * `el()` — this file had zero DOM-construction code before the 2026-07-21
 * two-pane redesign, so there was nothing to import; same "duplicated by
 * hand, no cross-module coupling" convention as every other shared-shape
 * constant/helper in this file (e.g. `APPLY_SET_WIDGET_NAME` above).
 * @param {string} tag
 * @param {{className?: string, text?: string, attrs?: Record<string,string>}} [options]
 * @param {(Node|string)[]} [children]
 * @returns {HTMLElement}
 */
function el(tag, options = {}, children = []) {
  const node = document.createElement(tag)
  if (options.className) node.className = options.className
  if (options.text !== undefined) node.textContent = options.text
  if (options.attrs) {
    for (const [key, value] of Object.entries(options.attrs)) {
      node.setAttribute(key, value)
    }
  }
  for (const child of children) {
    if (child == null) continue
    node.append(child instanceof Node ? child : document.createTextNode(String(child)))
  }
  return node
}

/**
 * The human name a `_setsCache` entry answers to — what `_selectEntry()`
 * loads into the `name` field on select and what `_saveAsNewName()`
 * compares that field against (FORMAT.md §6.3, 2026-07-21b). `name` with a
 * `slug` fallback, mirroring `_applySetsResponse()`'s own `s.name ||
 * s.slug` label seed — deliberately NOT `.label`, which can carry a dedup
 * "(slug)" suffix: a suffixed label loaded into the field would compare
 * unequal to the entry's real name on the very next Save and mint a bogus
 * copy named after the suffix.
 */
function entryDisplayName(entry) {
  return entry?.name || entry?.slug || ''
}

// ------------------------------------------------------------ node registration

/**
 * Register the `LoraLibrarySetController` virtual node type with LiteGraph.
 * Called once from the extension's `init()` hook (lora_library.js), which
 * already wraps this call in its own try/catch — we still guard everything
 * here too, per FORMAT.md §6.3: this file must never throw during graph load.
 */
export function registerControllerNode() {
  try {
    if (typeof LiteGraph === 'undefined' || typeof LGraphNode === 'undefined') {
      api.warn(`LiteGraph/LGraphNode globals not found; ${NODE_TITLE} not registered`)
      return
    }
    if (LiteGraph.registered_node_types && LiteGraph.registered_node_types[NODE_TYPE]) {
      return // already registered (double-init guard)
    }

    class LoraLibrarySetController extends LGraphNode {
      static title = NODE_TITLE

      constructor(title = NODE_TITLE) {
        super(title)
        // isVirtualNode: never executes, never enters the API prompt
        // (ComfyUI_frontend src/utils/executionUtil.ts:37-39, 86-91 strip
        // isVirtualNode nodes wholesale before building the prompt) — this
        // node can never break queueing by construction, not just by care.
        this.isVirtualNode = true
        // target/set/name persist in the workflow; status opts out via
        // widget.serialize = false (see _buildWidgets below) and the
        // two-pane DOM widget opts out the same way (see _buildStatePane) —
        // the 4 action buttons used to be a 3rd opt-out group here too, but
        // 2026-07-21 moved them into the DOM widget's right pane, where
        // they're plain DOM elements with no `serialize` flag of their own.
        this.serialize_widgets = true

        this._w = {}
        // FORMAT.md §6.3 TWO-PANE layout (2026-07-21) — the two-pane DOM
        // widget's own element refs (`root`/`listEl`), filled in by
        // `_buildStatePane()`. Kept separate from `_w` (which holds real
        // litegraph widgets) since these are plain DOM nodes.
        this._pane = null
        this._setsCache = []
        this._lastProbe = null
        this._lastStatusMessage = ''
        this._lastHeartbeat = 0
        this._lastSetsPoll = 0
        // 2026-07-18c delete-bug fix: the last SLUG (stable) a label lookup
        // in _selectedSetEntry() resolved to — a durable fallback for when
        // _setsCache gets rebuilt (heartbeat-driven _refreshSetsCache) with a
        // different dedup-suffixed label for the same underlying entry. See
        // _selectedSetEntry() for the full root-cause writeup.
        this._selectedSlug = null

        // FORMAT.md §6.3: "Show status" — boolean, default false, revealed
        // via the node's right-click Properties Panel. Must exist before
        // _buildWidgets() runs below so the status widget's initial
        // `.hidden` can read it. See onPropertyChanged() for the live toggle.
        this.addProperty(PROP_SHOW_STATUS, false, 'boolean')
        // 2026-07-20 (round 10): "Debug capture" — same Properties-Panel
        // pattern as "Show status" above, but this one has no widget to
        // show/hide; onPropertyChanged() has nothing to do for it, it's read
        // fresh from `this.properties` at the top of every capture instead.
        this.addProperty(PROP_DEBUG_CAPTURE, false, 'boolean')

        this._guarded('build widgets', () => this._buildWidgets())
      }

      // 2026-07-19c: the `configure()` override that used to live here is
      // removed — it existed only to bracket `_isRestoring` around
      // `super.configure(info)` so a workflow-restore `.value` write on the
      // `set` combo couldn't be mistaken for a user pick. Selection no
      // longer goes through that combo's callback at all (see the file
      // header's 2026-07-19c section and `_hookSetWidgetMenu()`), so restore
      // structurally cannot reach `_onSetPicked()` regardless — nothing left
      // to bracket. `onPropertyChanged()` below (a genuinely different,
      // still-needed code path) is unaffected.

      /**
       * FORMAT.md §6.3: "Show status" node property → status widget
       * visibility. `.hidden` is a real litegraph layout primitive on this
       * fork (file header citations) — true hiding, not the `.disabled`
       * value-blanking trick used elsewhere in this file. Fires on both a
       * user edit (Properties Panel → setProperty) and a workflow load
       * that restores a non-default value (configure()'s properties loop)
       * — both are desired here, unlike the `set`-combo case.
       */
      onPropertyChanged(name, value) {
        if (name !== PROP_SHOW_STATUS) return
        this._guarded('Show status property changed', () => {
          if (this._w.status) this._w.status.hidden = !value
          this.setDirtyCanvas(true, true)
        })
      }

      // ---------------------------------------------------------- lifecycle

      onAdded() {
        this._guarded('onAdded', () => {
          this._refreshTargetCombo()
          this._probeAndUpdateStatus()
          this._refreshSetsCache()
        })
      }

      onRemoved() {
        this._guarded('onRemoved', () => {
          clearTimeout(this._w.deleteBtn?._armTimer)
        })
      }

      onDrawForeground() {
        this._guarded('heartbeat', () => this._heartbeat())
      }

      /** Every handler funnels through here — FORMAT.md §6.3: never throw. */
      _guarded(label, fn) {
        try {
          fn()
        } catch (error) {
          api.warn(`${NODE_TITLE}: ${label} failed`, error)
        }
      }

      /**
       * Async sibling of `_guarded()` above — a plain try/catch cannot catch
       * a rejected Promise, so an `await`-ing caller (e.g. `_doPush()`'s
       * best-effort loader_slot sync, FORMAT.md §6.2/§4.1) needs this
       * instead. Same contract: never throws back to the caller.
       */
      async _guardedAsync(label, fn) {
        try {
          await fn()
        } catch (error) {
          api.warn(`${NODE_TITLE}: ${label} failed`, error)
        }
      }

      // ------------------------------------------------------------ widgets

      _buildWidgets() {
        // Order matters beyond layout: every serialize:false widget below
        // (status + the two-pane DOM widget) MUST stay after every
        // normally-serialized one (target/set/name) — see the litegraph
        // save/restore note in the file header for why an interleaved order
        // would corrupt reload. 2026-07-21: the 4 action buttons moved OFF
        // canvas into the DOM widget's right pane (`_buildStatePane()`), so
        // this invariant now covers one fewer *kind* of widget, not a
        // different rule.
        this._w.target = this.addWidget(
          'combo',
          'target',
          '',
          () => this._guarded('target changed', () => this._probeAndUpdateStatus()),
          { values: () => this._targetComboValues() }
        )

        // FORMAT.md §6.3 TWO-PANE layout (owner ask 2026-07-21): the `set`
        // COMBO and its on-canvas dropdown are gone — state selection is now
        // the two-pane DOM widget below (`_buildStatePane()`/
        // `_renderStateList()`). `_w.set` survives as a HIDDEN plain text
        // widget purely so the selection still serializes (Notebook's
        // `entry`-widget trick, FORMAT.md §7.2) — `name: 'set'` stays the
        // frozen literal (file header: every "kept in sync by hand" consumer
        // of that name, and the save/restore positional-index contract, are
        // unaffected by the type change from 'combo' to 'text'). The
        // callback is a deliberate no-op: the widget is hidden, so no canvas
        // click can ever reach it, and every programmatic write goes through
        // `_setSetValueSilently()`, which sets `.value` directly.
        this._w.set = this.addWidget('text', 'set', '', () => {}, {})
        this._w.set.hidden = true

        this._w.name = this.addWidget('text', 'name', '', () => {}, {})

        // Read-only status line. VERIFY(live) FINDING, confirmed against the
        // live litegraph build: setting `.disabled = true` on a plain 'text'
        // widget suppresses its VALUE text on canvas entirely (only the
        // widget's `name` label still renders) — it does not just gray it
        // out. That would make this widget useless for its one job (showing
        // the fail-soft message), so we deliberately do NOT set `.disabled`
        // here. Read-only-ness is enforced the other way instead: the
        // callback immediately reverts any value the user manages to type
        // back to the last computed status message.
        //
        // Hidden by default (FORMAT.md §6.3: "Show status" property, default
        // false) via `.hidden` — a real litegraph layout primitive, NOT the
        // `.disabled` trick above; see onPropertyChanged() for the live
        // toggle and the file header for the citations backing that choice.
        this._w.status = this.addWidget('text', 'status', '', () => {
          this._w.status.value = this._lastStatusMessage
        }, {})
        this._w.status.serialize = false
        this._w.status.hidden = !this.properties[PROP_SHOW_STATUS]

        // FORMAT.md §6.3 TWO-PANE layout (2026-07-21): left list + right
        // stacked buttons, replacing the removed `set` combo's dropdown and
        // the 4 canvas button widgets. See `_buildStatePane()` and the file
        // header's 2026-07-21 section.
        this._buildStatePane()

        this._refreshTargetCombo()
        this._probeAndUpdateStatus()
      }

      /**
       * FORMAT.md §6.3 (2026-07-21): builds one right-pane action button.
       * Every click funnels through `_guarded()` — a strengthening over the
       * pre-2026-07-21 canvas buttons, where only Delete's click was
       * explicitly re-guarded at the call site — consistent with this
       * file's "every handler funnels through here — never throw" rule
       * (`_guarded()`'s own doc comment). Replaces the removed
       * `_addButton()`, which built a canvas `'button'`-type widget instead.
       */
      _createActionButton(className, label, onClick) {
        const button = el('button', { className, text: label })
        button.addEventListener('click', () => this._guarded(`${label} click`, onClick))
        return button
      }

      /**
       * FORMAT.md §6.3 TWO-PANE layout (owner ask 2026-07-21) — see the file
       * header's dated section for the full design rationale. Builds the
       * left-list/right-buttons DOM widget that replaces the old `set`
       * COMBO's on-canvas dropdown. Mirrors `web/lora_library/notebook.js`'s
       * `attachDomWidget()` (identical addDOMWidget option shape) and its
       * separation of "build the static chrome once" from "re-render the
       * dynamic list on demand" (`renderList()`/here, `_renderStateList()`)
       * — the buttons are built exactly ONCE, here, so an armed Delete
       * button's DOM identity (and its `_armed`/`_armTimer` state) survives
       * every later `_renderStateList()` call untouched, including the
       * periodic background sets-cache poll (FORMAT.md §6.3: the armed
       * button "survives background cache refreshes for its full window").
       */
      _buildStatePane() {
        injectControllerStyles()

        this._pane = {}
        this._pane.listEl = el('div', { className: 'llsc-list' })
        const leftPane = el('div', { className: 'llsc-pane-left' }, [this._pane.listEl])

        this._w.captureBtn = this._createActionButton('llsc-btn', LABEL_CAPTURE, () => this._onCaptureClick())
        this._w.updateBtn = this._createActionButton('llsc-btn', LABEL_UPDATE, () => this._onUpdateClick())
        this._w.deleteBtn = this._createActionButton('llsc-btn llsc-btn-danger', LABEL_DELETE, () =>
          this._onDeleteClick()
        )
        // FORMAT.md §6.3 Push State (2026-07-19): broadcasts to Apply LoRA
        // Set nodes, entirely independent of the rgthree target/probe this
        // pack drives above — deliberately NOT in `_actionButtons` below, so
        // it stays clickable even with no Power Lora Loader in the graph (or
        // rgthree not installed at all); see `_doPush()`.
        this._w.pushBtn = this._createActionButton('llsc-btn', LABEL_PUSH, () => this._onPushClick())
        this._actionButtons = [this._w.captureBtn, this._w.updateBtn, this._w.deleteBtn]

        const rightPane = el('div', { className: 'llsc-pane-right' }, [
          this._w.captureBtn,
          this._w.updateBtn,
          this._w.deleteBtn,
          this._w.pushBtn
        ])

        const panes = el('div', { className: 'llsc-panes' }, [leftPane, rightPane])
        this._pane.root = el('div', { className: 'llsc-root' }, [panes])

        if (typeof this.addDOMWidget !== 'function') {
          // Fail soft (FORMAT.md §6.3) — mirrors notebook.js's own defensive
          // check for the identical API; every currently-shipping ComfyUI
          // frontend already runs the Notebook's addDOMWidget-based editor,
          // so this branch is not expected to fire, but the controller must
          // never throw during graph load regardless.
          api.warn(`${NODE_TITLE}: this ComfyUI frontend has no addDOMWidget; two-pane state picker not attached`)
          return
        }
        const domWidget = this.addDOMWidget(STATE_PANE_WIDGET_NAME, STATE_PANE_WIDGET_TYPE, this._pane.root, {
          hideOnZoom: true,
          serialize: false,
          getMinHeight: () => MIN_STATE_PANE_HEIGHT
        })
        domWidget.serialize = false
        domWidget.serializeValue = () => undefined

        this._renderStateList()
      }

      /**
       * FORMAT.md §6.3/§7.2 (2026-07-21): rebuild the left list from
       * `_setsCache` — Notebook's `renderList()`/`buildEntryRow()` twin,
       * minus categories/multi-select/drag (states have none of those; a
       * controller selects exactly one state at a time, same as the combo
       * it replaces). Called whenever `_setsCache` is rebuilt
       * (`_applySetsResponse()`) or the current selection changes
       * (`_setSetValueSilently()` — the single write point for
       * `_w.set.value`, so routing the repaint through it covers every
       * selection change for free: a user row click, a post-Capture/Save/
       * Delete auto-select, and the initial paint once the first
       * `_refreshSetsCache()` resolves after a workflow load).
       */
      _renderStateList() {
        const listEl = this._pane?.listEl
        if (!listEl) return
        // 2026-07-21b keyboard parity (file header, section A): this rebuild
        // replaces every row ELEMENT, which would silently drop focus to
        // <body> if it sat on a row — fatal for the two-step Enter flow (the
        // select-branch rebuild would eat the focus the second Enter needs)
        // and mildly hostile from the background sets-poll. Remember which
        // row held focus (by its stable `data-slug`, not its label — labels
        // can drift a dedup suffix between rebuilds) and put focus back on
        // the rebuilt row; `preventScroll` so restoration never yanks the
        // list's scroll position mid-interaction.
        const focused = document.activeElement
        const focusedSlug = focused && listEl.contains(focused) ? focused.getAttribute('data-slug') : null
        listEl.replaceChildren()

        if (!this._setsCache.length) {
          listEl.append(el('div', { className: 'llsc-empty', text: PLACEHOLDER_NO_SETS }))
          return
        }

        const selected = this._selectedSetEntry()
        for (const entry of this._setsCache) {
          const active = !!selected && entry.slug === selected.slug
          const row = el('div', {
            className: active ? 'llsc-row llsc-row-active' : 'llsc-row',
            text: entry.label,
            attrs: { tabindex: '0', title: entry.label, 'data-slug': entry.slug }
          })
          // FORMAT.md §6.3 select-vs-apply split (2026-07-21b): a click only
          // SELECTS this row — unless it is already the current selection,
          // in which case it APPLIES. Both steps live in `_onSetPicked()`,
          // which branches on the current selection; see its doc comment
          // for why ONE plain `click` listener (no `dblclick`) covers
          // single-click select, double-click apply, AND click-the-already-
          // highlighted-row apply. Still zero litegraph widget internals —
          // the 2026-07-21 version-proofing argument holds unchanged.
          row.addEventListener('click', () => this._onSetPicked(entry.label))
          row.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter' && event.key !== ' ') return
            event.preventDefault()
            this._onSetPicked(entry.label)
          })
          listEl.append(row)
          if (focusedSlug && entry.slug === focusedSlug) row.focus({ preventScroll: true })
        }
      }

      /**
       * FORMAT.md §6.3 amendment: when the graph holds >=2 PLLs, append the
       * "All Power Lora Loaders (N)" entry (the WAN high/low dual-loader
       * case) alongside every individual node.
       */
      _targetComboValues() {
        const candidates = findTargetCandidates()
        if (!candidates.length) return [PLACEHOLDER_NO_TARGET]
        const labels = candidates.map((c) => c.label)
        if (candidates.length >= 2) labels.push(formatAllTargetsLabel(candidates.length))
        return labels
      }

      /**
       * Auto-select when exactly one PLL exists; never guess among 2+ — with
       * one exception: "All Power Lora Loaders (N)" is STICKY. Once selected
       * it stays selected as N changes (we just rewrite the embedded count),
       * for as long as N stays >= 2. If N drops to 1, "All" stops being
       * offered at all and the single-PLL auto-select below takes over —
       * same rule that has always applied to the N=1 case.
       */
      _refreshTargetCombo() {
        const widget = this._w.target
        if (!widget) return
        const candidates = findTargetCandidates()
        if (candidates.length === 0) {
          widget.value = PLACEHOLDER_NO_TARGET
          return
        }
        if (candidates.length >= 2 && ALL_TARGETS_RE.test(String(widget.value || ''))) {
          widget.value = formatAllTargetsLabel(candidates.length)
          return
        }
        const stillValid = candidates.some((c) => c.label === widget.value)
        if (candidates.length === 1 && !stillValid) {
          widget.value = candidates[0].label
        }
        // A stale value against 2+ candidates (and not in "All" mode) is
        // left alone (tolerate a target id that no longer exists —
        // FORMAT.md §6.3 persistence note) — probeTargets() will report
        // "not found" rather than guess.
      }

      /** Single write point for the status line — every caller (probe, capture, update) stays consistent. FORMAT.md §6.3. */
      _setStatusText(message) {
        this._lastStatusMessage = message
        if (this._w.status) this._w.status.value = message
      }

      _probeAndUpdateStatus() {
        const targets = resolveTargetNodes(this._w.target?.value)
        const probe = probeTargets(targets)
        this._lastProbe = probe
        this._setStatusText(probe.message)
        for (const button of this._actionButtons || []) {
          // 2026-07-18c delete-bug fix: never let a heartbeat-driven probe
          // flip disable an ARMED delete button. A disabled widget swallows
          // its click with zero feedback (litegraph skips the callback
          // entirely) — during an active queue this heartbeat can easily
          // fire mid-arm (see _heartbeat()), so without this guard a
          // transient probe hiccup would eat click 2 silently and read as
          // "the button does nothing" for the rest of the confirm window.
          // The button re-syncs to the live probe the instant it disarms.
          if (button === this._w.deleteBtn && button._armed) continue
          button.disabled = !probe.ok
        }
        this.setDirtyCanvas(true, false)
      }

      _heartbeat() {
        const now = Date.now()
        if (now - this._lastHeartbeat < HEARTBEAT_MIN_MS) return
        this._lastHeartbeat = now
        this._refreshTargetCombo()
        this._probeAndUpdateStatus()
        if (now - this._lastSetsPoll >= SETS_POLL_MS) {
          this._lastSetsPoll = now
          this._guarded('sets poll', () => this._refreshSetsCache())
        }
      }

      // --------------------------------------------------------- sets cache

      async _refreshSetsCache() {
        try {
          const data = await api.getJson('/lora_library/sets')
          this._applySetsResponse(data)
        } catch (error) {
          api.warn(
            `${NODE_TITLE}: GET /lora_library/sets failed (backend sets routes may not be deployed yet)`,
            error
          )
        }
      }

      _applySetsResponse(data) {
        const list = Array.isArray(data?.sets) ? data.sets : []
        const seenLabels = new Set()
        this._setsCache = list.map((s) => {
          let label = s.name || s.slug
          if (seenLabels.has(label)) label = `${label} (${s.slug})`
          seenLabels.add(label)
          return { slug: s.slug, name: s.name, count: s.count, label }
        })
        // FORMAT.md §6.3 (2026-07-21): keep the two-pane list in sync with
        // the cache on every rebuild — a capture/update/delete response, or
        // the periodic sets-poll (`_heartbeat()`/`_refreshSetsCache()`).
        this._renderStateList()
        this.setDirtyCanvas(true, false)
      }

      /**
       * 2026-07-18c delete-bug fix (owner report — delete failing while a
       * workflow was RUNNING). ROOT CAUSE: the `set` combo's `.value` is a
       * derived, dedup-suffixed LABEL string (`_applySetsResponse()`),
       * rebuilt from scratch on every `_refreshSetsCache()` poll, and this
       * method used to match ONLY by that label against the current
       * `_setsCache`. The backend's `list_sets()` sort is fully
       * deterministic for unchanged data (lora_library/sets_store.py:242,
       * `sort(key=lambda e: (e["name"].casefold(), e["slug"]))` — slug is
       * unique so there's never a real tie), so a bare, no-op poll never by
       * itself reshuffles labels. But ANY actual library change during the
       * ~4s arm-to-confirm window — renaming/capturing/deleting ANY state,
       * even one unrelated to the one armed, since that can add or remove a
       * DIFFERENT entry's "(slug)" dedup suffix — can leave the widget still
       * holding a label string that no longer appears in the freshly
       * rebuilt cache, even though the armed entry itself still exists.
       * `_heartbeat()` only runs from `onDrawForeground()`, and an ACTIVE
       * QUEUE keeps the canvas dirty continuously (progress bar, executing-
       * node highlight), so its 1s throttle and the nested 4s
       * `SETS_POLL_MS` both fire like clockwork — and `SETS_POLL_MS` is the
       * SAME 4000ms as `DELETE_CONFIRM_MS`, so a sets-cache refresh is
       * near-guaranteed to land inside any given arm window while a queue is
       * busy, and often never fires at all across the same window while
       * idle. That is the concrete mechanism behind "worked when I tried it
       * standing still, failed mid-queue." When the label match failed, the
       * second click fell through to `_doDelete()`'s `if (!entry)` branch,
       * which only shows a WARN toast ("Pick a saved state first.") — easy
       * to miss on a busy screen, and the `status` widget that would have
       * shown the same text persistently is hidden by default
       * (`PROP_SHOW_STATUS`) — so the button read as simply dead.
       *
       * FIX: match by label first (cheap, correct the overwhelming majority
       * of the time, and what keeps a genuine NEW user pick working
       * immediately) but fall back to the last confidently-resolved SLUG —
       * stable for the entry's whole lifetime, FORMAT.md §4 — instead of
       * reporting "nothing selected." `_onDeleteClick()` also calls this at
       * ARM time specifically so `_selectedSlug` is fresh for the whole
       * window even if the user never triggers another label-matching read
       * before clicking confirm.
       */
      _selectedSetEntry() {
        const value = this._w.set?.value
        let entry = this._setsCache.find((s) => s.label === value)
        if (entry) {
          this._selectedSlug = entry.slug
          return entry
        }
        if (this._selectedSlug) {
          entry = this._setsCache.find((s) => s.slug === this._selectedSlug)
          if (entry) return entry
        }
        return null
      }

      _selectSetBySlug(slug) {
        const entry = this._setsCache.find((s) => s.slug === slug)
        if (entry) this._selectEntry(entry)
      }

      /**
       * FORMAT.md §6.3 select-vs-apply split (2026-07-21b, file header
       * section A): the ONE place a state becomes the current SELECTION,
       * and everything selection now means — `_selectedSlug` (the
       * drift-proof anchor `_selectedSetEntry()` falls back to), the hidden
       * serialized `set` widget + list-highlight repaint (via
       * `_setSetValueSilently()`), the `name` field loaded with the state's
       * own name (`entryDisplayName()` — the rename/spin-off flows both
       * start from that, see `_saveAsNewName()`; `loadName: false` is the
       * apply-reclick's way of NOT clobbering a name the user may already
       * have typed for a save-as-new), and disarming any pending
       * delete-confirm (moved here from `_onSetPicked()` so EVERY selection
       * movement — a user click, a post-save/capture auto-select, delete's
       * fallback — disarms, not just user picks: `_doDelete()` deletes
       * whatever `_selectedSetEntry()` resolves at confirm time, so a
       * selection allowed to move under an armed button would aim the
       * confirm at the WRONG state; disarm is idempotent, so the call
       * sites that already disarmed at click entry lose nothing). NEVER
       * applies anything — apply lives exclusively in `_onSetPicked()`'s
       * reclick branch, which is the split's whole point.
       */
      _selectEntry(entry, { loadName = true } = {}) {
        this._selectedSlug = entry.slug
        this._setSetValueSilently(entry.label)
        if (loadName && this._w.name) this._w.name.value = entryDisplayName(entry)
        this._disarmDeleteButton()
        this.setDirtyCanvas(true, false)
      }

      /**
       * The ONLY sanctioned way to write `this._w.set.value` from our own
       * code — every selection movement funnels through `_selectEntry()`
       * above (its only caller besides `_doDelete()`'s emptied-library
       * clear) — AND, since the 2026-07-21 two-pane redesign, the one place
       * that repaints the two-pane list's highlight (`_renderStateList()`),
       * so every selection-changing call site gets that repaint for free
       * instead of needing its own call. A plain, UNGUARDED `.value =`
       * assignment (2026-07-19c: it no longer needs to "silently" suppress
       * anything, since selection never goes through a combo's
       * callback/setValue at all — now doubly true, since there is no combo
       * left, only a hidden text widget and a DOM list with its own click
       * handlers). Kept as a named helper purely for readability at call
       * sites, not for a guard it used to need.
       */
      _setSetValueSilently(label) {
        if (!this._w.set) return
        this._w.set.value = label
        this._renderStateList()
      }

      _toast(severity, summary, detail) {
        try {
          app.extensionManager?.toast?.add?.({
            severity,
            summary,
            detail,
            life: severity === 'error' ? 6000 : 3000
          })
        } catch {
          // Toast is a nicety; never let it be the reason an action "fails".
        }
      }

      async _runAction(label, fn) {
        try {
          await fn()
        } catch (error) {
          api.warn(`${NODE_TITLE}: ${label} failed`, error)
          this._toast('error', NODE_TITLE, `${label} failed: ${error?.message || error}`)
        }
      }

      // -------------------------------------------------------- button actions

      /**
       * FORMAT.md §6.3 select-vs-apply split (owner ask 2026-07-21b —
       * supersedes this method's original click-IS-apply body; see the file
       * header's section A). Fired by a two-pane list row's plain
       * `click`/`keydown` handler (`_renderStateList()`) and by nothing
       * else — workflow restore still only plain-assigns the hidden `set`
       * widget's `.value` and can never get here. TWO-STEP:
       *
       *  - A click on a row that is NOT the current selection only SELECTS
       *    it (`_selectEntry()` — highlight, hidden `set` widget, `name`
       *    field, delete-confirm disarm) and provably cannot touch a
       *    loader: this branch never reaches `_onSetSelected()`, whose only
       *    caller is the branch below. Selecting to rename or delete no
       *    longer rewrites every wired PLL.
       *  - A click on the row that IS already the current selection — the
       *    second click of a double-click, or a plain later click on the
       *    highlighted row (including a highlight restored from a saved
       *    workflow) — is the APPLY, and keeps the pre-split guarantee
       *    intact: unconditional `_onSetSelected()` -> `_doApply()`, no
       *    same-value branch anywhere, so re-applying the already-showing
       *    state force-re-pushes strengths (2026-07-19/19c fixes).
       *
       * "Is the current selection" is slug-anchored through
       * `_selectedSetEntry()` (label-drift-proof — the same resolution the
       * delete confirm relies on), and the apply branch still runs
       * `_selectEntry()` first (with `loadName: false`, so it can't clobber
       * a typed save-as-new name) — a dedup-suffix label drift self-heals
       * into the serialized widget instead of surviving until the next
       * save. `label` is always one of `_setsCache`'s own `.label` strings
       * (the row's text IS the label), so the exact-match lookup below
       * always succeeds for a live row.
       */
      _onSetPicked(label) {
        this._guarded('set picked', () => {
          const entry = this._setsCache.find((s) => s.label === label)
          if (!entry) return
          const previous = this._selectedSetEntry()
          const isApplyClick = !!previous && previous.slug === entry.slug
          this._selectEntry(entry, { loadName: !isApplyClick })
          if (isApplyClick) this._onSetSelected()
        })
      }

      /**
       * Shared apply trigger, called ONLY from `_onSetPicked()`'s
       * apply/reclick branch since the 2026-07-21b select-vs-apply split —
       * the select branch structurally cannot reach it, which is the
       * split's "a single click never rewrites loaders" guarantee. Kept as
       * its own method purely for the `_runAction` wrapping/naming in
       * toasts, not because multiple call sites need it (2026-07-19c: there
       * used to be more, back when the combo's own callback and the
       * setValue-reselect shim both funneled through here).
       */
      _onSetSelected() {
        this._runAction('Apply State', () => this._doApply())
      }

      _onCaptureClick() {
        // A pending delete-confirm is about whatever was selected when it was
        // armed; capturing a new state is a big enough context switch that
        // it should never be silently confirmed by the next click instead.
        this._disarmDeleteButton()
        this._runAction(LABEL_CAPTURE, () => this._doCapture())
      }

      _onUpdateClick() {
        this._disarmDeleteButton()
        this._runAction(LABEL_UPDATE, () => this._doUpdate())
      }

      /** FORMAT.md §6.3 Push State — broadcasts to Apply LoRA Set nodes, see `_doPush()`. */
      _onPushClick() {
        this._disarmDeleteButton()
        this._runAction(LABEL_PUSH, () => this._doPush())
      }

      /**
       * Two-step confirm: first click arms the button (distinct color +
       * "Are you sure?" label) for DELETE_CONFIRM_MS; a second click within
       * that window actually deletes. 2026-07-18c delete-bug fix: arming
       * also refreshes `_selectedSlug` (via `_selectedSetEntry()`) while the
       * combo's label match is fresh, so `_doDelete()`'s own lookup at
       * confirm time — however many sets-cache refreshes have landed in
       * between, see `_selectedSetEntry()` — still resolves correctly.
       */
      _onDeleteClick() {
        const button = this._w.deleteBtn
        if (!button) return
        if (!button._armed) {
          this._selectedSetEntry()
          button._armed = true
          // 2026-07-21: `button` is a real DOM `<button>` — `.textContent`
          // is its displayed text. (`.name` on a real `<button>` element is
          // the unrelated HTML form-control name attribute, invisible on
          // screen; the pre-redesign canvas WIDGET used `.name` for its
          // painted label, which is why this literally changed from `.name`
          // here, not just cosmetically.)
          button.textContent = LABEL_DELETE_CONFIRM
          this._armDeleteButtonColor(button)
          clearTimeout(button._armTimer)
          button._armTimer = setTimeout(() => this._disarmDeleteButton(), DELETE_CONFIRM_MS)
          this.setDirtyCanvas(true, false)
          return
        }
        this._disarmDeleteButton()
        this._runAction(LABEL_DELETE, () => this._doDelete())
      }

      /** Cancel a pending delete-confirmation and restore the button's normal look. Idempotent. */
      _disarmDeleteButton() {
        const button = this._w.deleteBtn
        if (!button || !button._armed) return
        clearTimeout(button._armTimer)
        button._armed = false
        button.textContent = LABEL_DELETE
        this._disarmDeleteButtonColor(button)
        this.setDirtyCanvas(true, false)
      }

      /**
       * 2026-07-21: `button` is now a real DOM `<button>` element (the
       * two-pane redesign's right pane), not a canvas widget — plain
       * `style` properties are freely settable, so the
       * `Object.defineProperty`-shadowing trick this used before (needed
       * only because litegraph's `background_color`/`text_color` are
       * getter-only accessors on a canvas widget — file header citation) is
       * no longer needed at all. Still wrapped defensively: this is
       * cosmetic only, and must never be the reason the arm/disarm STATE
       * machine itself breaks.
       */
      _armDeleteButtonColor(button) {
        try {
          button.style.background = DELETE_ARMED_BG_COLOR
          button.style.borderColor = DELETE_ARMED_BG_COLOR
          button.style.color = DELETE_ARMED_TEXT_COLOR
        } catch (error) {
          api.warn(`${NODE_TITLE}: could not color the armed delete button (cosmetic only)`, error)
        }
      }

      _disarmDeleteButtonColor(button) {
        try {
          button.style.background = ''
          button.style.borderColor = ''
          button.style.color = ''
        } catch (error) {
          api.warn(`${NODE_TITLE}: could not reset delete button color (cosmetic only)`, error)
        }
      }

      /**
       * FORMAT.md §6.3 amendment: with "All…" selected, APPLY writes the set
       * to every PLL — `targets` may hold 1 or N nodes, `probeTargets`/
       * `applySetToTargets` already handle both uniformly.
       *
       * 2026-07-20 §4.1 composite extension: a format-2 `full` (has
       * `loaders`) is sliced per FORMAT.md §6.3's two composite-apply rules
       * — see `applySetToTargets()`'s doc comment for the exact mechanics;
       * this method only computes the one extra piece it can't derive by
       * itself (`loaderIndexForSingle`, meaningful only for a single,
       * non-"All" target — `pllAscendingIndex()`) and builds the toast,
       * including the §6.3 "fewer loaders than targets" mismatch note. A
       * format-1 `full` (no `loaders`) takes EXACTLY the same path as
       * before this addition — `isComposite` is false, so every branch
       * below that's gated on it is skipped, and the final toast is
       * byte-for-byte the old message.
       */
      async _doApply() {
        const targets = resolveTargetNodes(this._w.target?.value)
        const probe = probeTargets(targets)
        if (!probe.ok) {
          this._toast('warn', NODE_TITLE, probe.message)
          return
        }
        const entry = this._selectedSetEntry()
        if (!entry) {
          this._toast('warn', NODE_TITLE, 'Pick a saved state first.')
          return
        }
        const full = await api.getJson('/lora_library/set', { slug: entry.slug })
        const isComposite = Array.isArray(full?.loaders) && full.loaders.length > 0
        const targetDesc =
          targets.length > 1
            ? `${targets.length} Power Lora Loaders`
            : `${targets[0].title || targets[0].type} #${targets[0].id}`

        if (isComposite && targets.length > 1) {
          applySetToTargets(targets, full)
          this._probeAndUpdateStatus()
          // Targets beyond `full.loaders.length` were left UNTOUCHED by
          // applySetToTargets() above (§6.3: "never guess") — say so
          // explicitly rather than describing them via `lorasForLoaderIndex()`'s
          // clamp, which would misleadingly report the LAST loader's row
          // count for a target that didn't actually receive it.
          const rowsDesc = targets
            .map((_node, i) => (i < full.loaders.length ? `L${i} ${lorasForLoaderIndex(full, i).length}` : `L${i} untouched`))
            .join(', ')
          const shortBy = targets.length - full.loaders.length
          const mismatch =
            shortBy > 0
              ? ` (state has ${full.loaders.length} loader${full.loaders.length === 1 ? '' : 's'} — ` +
                `${shortBy} target${shortBy === 1 ? '' : 's'} left untouched)`
              : ''
          this._toast('success', NODE_TITLE, `Applied "${full.name}" -> ${targetDesc} (${rowsDesc})${mismatch}.`)
          return
        }

        const loaderIndexForSingle = isComposite ? pllAscendingIndex(targets[0]) : null
        applySetToTargets(targets, full, { loaderIndexForSingle })
        this._probeAndUpdateStatus()
        const rows = isComposite ? lorasForLoaderIndex(full, loaderIndexForSingle).length : (full.loras || []).length
        this._toast(
          'success',
          NODE_TITLE,
          `Applied "${full.name}" -> ${targetDesc} (${rows} row${rows === 1 ? '' : 's'}${targets.length > 1 ? ' each' : ''}).`
        )
      }

      /**
       * FORMAT.md §6.3 2026-07-19c read-back toast: GET the just-written
       * state back (§5 `GET /lora_library/set?slug=`) and toast what the
       * FILE holds, not what the caller thinks it sent — see the file
       * header's item (4) for why. `verb` matches the existing toast
       * vocabulary ("Saved"/"Updated"); `extraNote` is the existing
       * multi-target capture-source suffix, unchanged in shape.
       */
      async _toastRowsSaved(verb, slug, extraNote) {
        try {
          const saved = await api.getJson('/lora_library/set', { slug })
          const summary = summarizeRowsForToast(saved.loras)
          this._toast('success', NODE_TITLE, `${verb} "${saved.name}": ${summary}${extraNote || ''}`)
        } catch (error) {
          api.warn(`${NODE_TITLE}: read-back after ${verb} failed`, error)
          this._toast('warn', NODE_TITLE, `${verb}, but reading it back to confirm failed — see console.`)
        }
      }

      /**
       * FORMAT.md §4.1/§6.3 composite read-back toast (2026-07-20): same
       * read-the-file-back philosophy as `_toastRowsSaved()` above, but
       * formats a PER-LOADER summary — the spec's own example, "Saved
       * 'WAN': L0 detailer 0.8 / L1 detailer 0.3" — by running
       * `summarizeRowsForToast()` (unchanged, reused as-is) over each
       * loader's rows and slash-joining the per-loader segments.
       */
      async _toastCompositeRowsSaved(verb, slug) {
        try {
          const saved = await api.getJson('/lora_library/set', { slug })
          const loaders = Array.isArray(saved.loaders) ? saved.loaders : []
          const summary = loaders.map((loader, i) => `L${i} ${summarizeRowsForToast(loader.loras)}`).join(' / ')
          this._toast('success', NODE_TITLE, `${verb} "${saved.name}": ${summary}`)
        } catch (error) {
          api.warn(`${NODE_TITLE}: read-back after ${verb} failed`, error)
          this._toast('warn', NODE_TITLE, `${verb}, but reading it back to confirm failed — see console.`)
        }
      }

      /**
       * FORMAT.md §6.3 amendment: with "All…" selected, CAPTURE reads from
       * the lowest-node-id PLL — `targets[0]` after `resolveTargetNodes()`'s
       * ascending sort.
       *
       * 2026-07-20 §4.1 composite extension: `targets.length > 1` can ONLY
       * happen here when "All Power Lora Loaders (N)" is selected with
       * N>=2 — every other `target` value resolves to 0 or 1 node via
       * `resolveTargetNode()` singular (see `resolveTargetNodes()`). That
       * case now captures EVERY target into its own composite slice
       * (`_captureComposite()`) instead of the lowest-id-only capture below
       * — FORMAT.md §4.1/§6.3: "each loader keeps its OWN config," replacing
       * the prior All-target capture behavior for real. The single-target
       * path below the guard is UNCHANGED from before this addition (the
       * owner-validated path) — this is a pure ADDITION gated on
       * `targets.length > 1`, so that path never runs through new code.
       */
      async _doCapture() {
        const targets = resolveTargetNodes(this._w.target?.value)
        const probe = probeTargets(targets)
        if (!probe.ok) {
          this._toast('warn', NODE_TITLE, probe.message)
          return
        }
        const name = (this._w.name?.value || '').trim() || `State ${this._setsCache.length + 1}`

        if (targets.length > 1) {
          await this._captureComposite(targets, name)
          return
        }

        const source = targets[0]
        const loras = await captureRows(source, { debugCapture: !!this.properties[PROP_DEBUG_CAPTURE] })
        const response = await api.postJson('/lora_library/set', {
          set: { format: 1, name, loras, trigger_words: '', notes: '' }
        })
        this._applySetsResponse(response)
        announceSetsChanged()
        this._selectSetBySlug(response.slug)
        // Deliberately AFTER _selectSetBySlug(): selecting now loads the
        // selected state's name into the `name` field (2026-07-21b,
        // `_selectEntry()`), and New State's contract is that the field
        // ends EMPTY — owner: New State "keeps working as before" — so this
        // explicit clear must have the last word. (Pressing New State again
        // right away therefore still auto-names "State N" instead of
        // minting a same-named copy.)
        if (this._w.name) this._w.name.value = ''
        // FORMAT.md §6.3: "Show status" names the capture-source loader id +
        // row count on every capture/save.
        this._setStatusText(
          `Captured ${loras.length} row${loras.length === 1 ? '' : 's'} from ${source.title || source.type} #${source.id}.`
        )
        await this._toastRowsSaved('Saved', response.slug, '')
      }

      /**
       * FORMAT.md §4.1/§6.3 composite New State: target = "All Power Lora
       * Loaders (N)", N>=2. Captures EVERY target's rows via the EXISTING
       * `captureRows()` (the v0.14.1 serialize-based capture — reused per
       * loader completely unchanged, not rewritten), in ascending-node-id
       * order (already guaranteed before `targets` reaches here, by
       * `resolveTargetNodes()`'s own sort), into one format-2 state:
       * `loaders[i]` = the i-th PLL's rows, top-level `loras` mirrors
       * `loaders[0]` (both per FORMAT.md §4.1 — the backend's own
       * `normalize_set()` also enforces the mirror on save/load, so this is
       * belt-and-suspenders, not the only place it happens).
       */
      async _captureComposite(targets, name) {
        const debugCapture = !!this.properties[PROP_DEBUG_CAPTURE]
        const loadersRows = []
        for (const node of targets) {
          loadersRows.push(await captureRows(node, { debugCapture }))
        }
        const set = {
          format: 2,
          name,
          loaders: loadersRows.map((rows) => ({ loras: rows })),
          loras: loadersRows[0] || [],
          trigger_words: '',
          notes: ''
        }
        const response = await api.postJson('/lora_library/set', { set })
        this._applySetsResponse(response)
        announceSetsChanged()
        this._selectSetBySlug(response.slug)
        // Deliberately AFTER _selectSetBySlug() — same New State epilogue
        // rule as _doCapture()'s, see the comment there (2026-07-21b).
        if (this._w.name) this._w.name.value = ''
        this._setStatusText(
          `Captured ${targets.length} loaders: ` +
            targets.map((_node, i) => `L${i} ${loadersRows[i].length} row${loadersRows[i].length === 1 ? '' : 's'}`).join(', ') +
            '.'
        )
        await this._toastCompositeRowsSaved('Saved', response.slug)
      }

      /**
       * FORMAT.md §6.3 "Save State honors a changed name" (owner bug
       * 2026-07-21b — file header, section B): the NEW name this Save
       * should CREATE a state under, or `null` for the plain
       * overwrite-the-selected-state save. Non-null iff the trimmed `name`
       * field holds a non-empty value that differs from the selected
       * entry's own name (`entryDisplayName()` — the same string
       * `_selectEntry()` loads into the field on select, so "unchanged"
       * means exactly "the user didn't edit what selecting put there"). An
       * EMPTY field never means "rename to empty": it keeps the overwrite
       * byte-identical to every Save before this rule — the field simply
       * doesn't participate — which also keeps the post-New-State state
       * (selection set, field deliberately cleared) saving benignly.
       */
      _saveAsNewName(entry) {
        const typed = (this._w.name?.value || '').trim()
        if (!typed || typed === entryDisplayName(entry)) return null
        return typed
      }

      /**
       * Same lowest-node-id source rule as _doCapture() — Update is a
       * re-capture. 2026-07-20 §4.1 composite extension: same
       * `targets.length > 1` guard/split as `_doCapture()` — see that
       * method's doc comment for why that can only mean "All Power Lora
       * Loaders (N)", N>=2, and why the single-target path below is
       * otherwise untouched by this addition.
       *
       * 2026-07-21b "Save State honors a changed name" (file header,
       * section B): `_saveAsNewName()` decides ONCE, up front — shared by
       * the single-target path below and `_updateComposite()` — whether
       * this Save OVERWRITES the selected state (field empty/unchanged: the
       * exact pre-existing slug'd POST) or CREATES a new state under the
       * field's value (the no-slug form of the SAME POST /lora_library/set
       * route `New State` uses — same backend save path, same per-state
       * JSON files, slug de-duplicated server-side) and then selects the
       * newly created state. Either way the rows are the CURRENT capture,
       * and trigger_words/notes come from the selected state's file (a
       * spin-off inherits its parent's metadata; the overwrite preserves
       * its own — one GET serves both).
       */
      async _doUpdate() {
        const targets = resolveTargetNodes(this._w.target?.value)
        const probe = probeTargets(targets)
        if (!probe.ok) {
          this._toast('warn', NODE_TITLE, probe.message)
          return
        }
        const entry = this._selectedSetEntry()
        if (!entry) {
          this._toast('warn', NODE_TITLE, 'Pick a saved state first.')
          return
        }
        const newName = this._saveAsNewName(entry)

        if (targets.length > 1) {
          await this._updateComposite(targets, entry, newName)
          return
        }

        const source = targets[0]
        const loras = await captureRows(source, { debugCapture: !!this.properties[PROP_DEBUG_CAPTURE] })
        // Preserve the existing name/trigger_words/notes; only the rows
        // change on a plain overwrite — best-effort GET, falls back to
        // rows-only. The save-as-new path (2026-07-21b) reuses the same GET
        // for trigger_words/notes: a spun-off state inherits the selected
        // state's metadata, just never its name or slug.
        let name = entry.name
        let trigger_words = ''
        let notes = ''
        try {
          const existing = await api.getJson('/lora_library/set', { slug: entry.slug })
          name = existing.name ?? name
          trigger_words = existing.trigger_words ?? ''
          notes = existing.notes ?? ''
        } catch (error) {
          api.warn(`${NODE_TITLE}: could not read existing set before update; overwriting rows only`, error)
        }
        const set = { format: 1, name: newName ?? name, loras, trigger_words, notes }
        // 2026-07-21b: a changed name posts the NO-SLUG create form (see
        // `_doUpdate()`'s doc comment); unchanged/empty posts the exact
        // pre-existing overwrite, `entry.slug` and all.
        const response = await api.postJson('/lora_library/set', newName ? { set } : { slug: entry.slug, set })
        const savedSlug = newName ? response.slug : entry.slug
        this._applySetsResponse(response)
        announceSetsChanged()
        this._selectSetBySlug(savedSlug)
        // FORMAT.md §6.3 strength-persistence fix, cause A (unchanged by
        // 2026-07-19c): re-apply the just-saved rows to every target
        // immediately, unconditionally. Redundant for the single-target
        // case (we just captured these exact rows FROM the target), but
        // it's what keeps every OTHER target in sync in "All Power Lora
        // Loaders" mode, and it's what makes Save State visibly "take"
        // without the owner having to reselect anything — selection no
        // longer depends on the combo's value changing AT ALL now (2026-
        // 07-19c removed that mechanism outright), but re-applying here on
        // its own terms is still the right behavior.
        applySetToTargets(targets, { loras })
        this._probeAndUpdateStatus()
        // FORMAT.md §6.3: "Show status" names the capture-source loader id +
        // row count on every capture/save — set AFTER _probeAndUpdateStatus()
        // above so this is the status line's final word for this action,
        // not immediately overwritten by the generic probe message.
        this._setStatusText(
          `Captured ${loras.length} row${loras.length === 1 ? '' : 's'} from ${source.title || source.type} #${source.id}.`
        )
        // "Saved" is the create verb (`_doCapture()`'s), "Updated" the
        // overwrite verb — the read-back toast names which one happened.
        await this._toastRowsSaved(newName ? 'Saved' : 'Updated', savedSlug, '')
      }

      /**
       * FORMAT.md §4.1/§6.3 composite Save State: target = "All Power Lora
       * Loaders (N)", N>=2 — re-captures EVERY target into the SAME
       * selected slug (a format-2 overwrite), same "Update is a re-capture"
       * rule the single-target half of `_doUpdate()` already follows;
       * preserves the existing name/trigger_words/notes exactly like that
       * single-target path (best-effort GET, falls back to rows-only).
       * 2026-07-21b: `newName` (decided by `_doUpdate()`'s single up-front
       * `_saveAsNewName()` call, passed through so both halves agree) flips
       * this to the no-slug CREATE form of the same POST — a format-2
       * save-as-new — mirroring the single-target path exactly: new
       * name/slug, current rows, inherited trigger_words/notes, then select
       * the new state.
       */
      async _updateComposite(targets, entry, newName) {
        const debugCapture = !!this.properties[PROP_DEBUG_CAPTURE]
        const loadersRows = []
        for (const node of targets) {
          loadersRows.push(await captureRows(node, { debugCapture }))
        }
        let name = entry.name
        let trigger_words = ''
        let notes = ''
        try {
          const existing = await api.getJson('/lora_library/set', { slug: entry.slug })
          name = existing.name ?? name
          trigger_words = existing.trigger_words ?? ''
          notes = existing.notes ?? ''
        } catch (error) {
          api.warn(`${NODE_TITLE}: could not read existing set before update; overwriting rows only`, error)
        }
        const set = {
          format: 2,
          name: newName ?? name,
          loaders: loadersRows.map((rows) => ({ loras: rows })),
          loras: loadersRows[0] || [],
          trigger_words,
          notes
        }
        const response = await api.postJson('/lora_library/set', newName ? { set } : { slug: entry.slug, set })
        const savedSlug = newName ? response.slug : entry.slug
        this._applySetsResponse(response)
        announceSetsChanged()
        this._selectSetBySlug(savedSlug)
        // Re-apply immediately — same rationale the single-target half
        // documents above (keeps every OTHER target in sync, makes Save
        // State visibly "take"); composite-aware apply (loaders[i] ->
        // targets[i] by ascending id, same rule `_doApply()` uses) via the
        // just-built `set` payload, echoing back exactly what was just
        // captured.
        applySetToTargets(targets, set)
        this._probeAndUpdateStatus()
        this._setStatusText(
          `Captured ${targets.length} loaders: ` +
            targets.map((_node, i) => `L${i} ${loadersRows[i].length} row${loadersRows[i].length === 1 ? '' : 's'}`).join(', ') +
            '.'
        )
        await this._toastCompositeRowsSaved(newName ? 'Saved' : 'Updated', savedSlug)
      }

      async _doDelete() {
        const entry = this._selectedSetEntry()
        if (!entry) {
          this._toast('warn', NODE_TITLE, 'Pick a saved state first.')
          return
        }
        const response = await api.postJson('/lora_library/set/delete', { slug: entry.slug })
        this._applySetsResponse(response)
        announceSetsChanged()
        // Selection falls back to the first remaining state — through
        // `_selectEntry()` (2026-07-21b), so the `name` field follows the
        // selection like every other selection movement; an emptied library
        // clears all three (slug anchor, hidden widget, name field).
        const nextEntry = this._setsCache[0] || null
        if (nextEntry) {
          this._selectEntry(nextEntry)
        } else {
          this._selectedSlug = null
          this._setSetValueSilently('')
          if (this._w.name) this._w.name.value = ''
        }
        this._toast('success', NODE_TITLE, `Deleted "${entry.name}".`)
      }

      /**
       * FORMAT.md §6.2/§6.3 SELECTIVE Push State (2026-07-19c amendment,
       * owner: "set different Apply LoRA Set nodes to different Power Lora
       * Loaders as targets"): broadcast the currently-selected state, but
       * only to the Apply nodes this controller's `target` combo selects
       * for — see `selectPushTargets()`/`mirrorsTagMatches()` (this file)
       * and sets.js's `mirrors loader` tag they read. `target` = a specific
       * PLL ⇒ only Apply nodes tagged to that PLL (plus any tagged
       * "(any)"); `target` = "All…" ⇒ every Apply node regardless of tag.
       * Still entirely independent of `probeTargets()`/rgthree health — a
       * push never touches rgthree, so this runs fine with zero Power Lora
       * Loaders in the graph, or rgthree not installed at all; only the
       * RAW `target` combo label is read, not its rgthree-resolved node.
       * Writes the state's SLUG, not its (possibly dedup-suffixed) combo
       * label — ApplySet's own `set` combo is built server-side from
       * `["None"] + sorted slugs` (lora_library/nodes_sets.py
       * `_slug_options()`), and its frontend cache (sets.js
       * `refreshSetsCache`) mirrors that: slugs only, never names/labels.
       */
      async _doPush() {
        const entry = this._selectedSetEntry()
        if (!entry) {
          this._toast('warn', NODE_TITLE, 'Pick a saved state first.')
          return
        }
        const targetValue = String(this._w.target?.value || '')
        const pushingAll = ALL_TARGETS_RE.test(targetValue)
        const applyNodes = selectPushTargets(targetValue)
        if (applyNodes.length === 0) {
          const scope = pushingAll ? 'in this graph' : `tagged to "${targetValue}" (or "(any)")`
          this._toast('warn', NODE_TITLE, `No Apply LoRA Set nodes ${scope}.`)
          return
        }
        const count = pushStateToNodes(applyNodes, entry.slug)
        // FORMAT.md §6.2/§6.3 (2026-07-20, §4.1 nice-to-have): best-effort
        // loader_slot sync for composite states — see
        // `_syncLoaderSlotsForPush()`/`pushLoaderSlotForTag()`. Wrapped in
        // `_guardedAsync` so a failure here (a slow/failed GET, an
        // unexpected response shape) can NEVER affect the push above, which
        // has already completed by this line — this is strictly additive,
        // never a precondition for the toast below or the broadcast itself.
        await this._guardedAsync('push loader_slot sync', () => this._syncLoaderSlotsForPush(applyNodes, entry.slug))
        const scopeNote = pushingAll ? '' : ` (target: "${targetValue}")`
        this._toast(
          'success',
          NODE_TITLE,
          `Pushed "${entry.name}" to ${count} Apply LoRA Set node${count === 1 ? '' : 's'}${scopeNote}.`
        )
      }

      /**
       * Best-effort loader_slot sync (FORMAT.md §6.2/§6.3, 2026-07-20
       * nice-to-have for the WAN flow): when the just-pushed state is a
       * §4.1 composite (format 2), also set each pushed Apply node's
       * `loader_slot` to its `mirrors loader` tag's ascending-id index
       * among the graph's PLLs (`pushLoaderSlotForTag()`) — so a WAN pair
       * of tagged Apply nodes lands on its own slice with no manual "reveal
       * loader_slot and type 1" step. Deliberately skipped ENTIRELY for a
       * format-1 state: nothing to slice, and `loader_slot` is spec'd to be
       * ignored there anyway, so there's no reason to touch a hidden widget
       * the user never revealed for the common single-loader case — the
       * validated Push State path for a format-1 state runs through zero
       * new code beyond this one extra GET (see caller: any failure here is
       * caught by `_guardedAsync` and never reaches the push itself).
       */
      async _syncLoaderSlotsForPush(applyNodes, slug) {
        const full = await api.getJson('/lora_library/set', { slug })
        if (!Array.isArray(full?.loaders) || !full.loaders.length) return // format-1: nothing to sync
        for (const node of applyNodes) pushLoaderSlotForTag(node)
      }
    }

    LiteGraph.registerNodeType(NODE_TYPE, LoraLibrarySetController)
    LoraLibrarySetController.category = NODE_CATEGORY
  } catch (error) {
    api.warn('registerControllerNode failed', error)
  }
}
