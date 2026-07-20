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
 *    for a widget literally named `set` do. `_buildWidgets()` therefore sets
 *    `this._w.set.label = 'state'` right after creating the widget and
 *    leaves `name: 'set'` alone.
 *  - Per-widget custom colors (delete-button arm indicator, added for the
 *    2026-07-18c delete-bug fix, see `_armDeleteButtonColor()`):
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
 *    `status` + all 4 buttons (all `serialize:false`) are declared last.
 *    Do not reorder without re-checking this. Button count history: 4 → 3
 *    when the 2026-07-18 owner change removed the standalone Apply button
 *    (see `_onSetSelected`) → 4 again on 2026-07-19 with Push State added
 *    (see `_doPush`).
 *  - `ComboWidget` supports `options.values` as a function
 *    (ComboWidget.ts:59-64, `getValues()`) — but it's deprecated as of
 *    v0.14.5 and logs a console warning on every dropdown open
 *    (ComboWidget.ts:126-135: "Using a function for values is deprecated.").
 *    It is still fully functional (this is the same pattern the deprecation
 *    message itself cites from ComfyUI-KJNodes), so we use it for `target`
 *    (cheap, pure in-memory graph scan — safe to re-run on every call) and
 *    for `set` (backed by a manually-refreshed cache, never a network call
 *    from inside the values function itself). VERIFY(live): a future
 *    litegraph release could remove this path outright.
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
 * "armed" even on a canvas that's constantly redrawing (an active queue) —
 * see `_armDeleteButtonColor()` and the file header's per-widget-color
 * citation for why this needs `Object.defineProperty` rather than a plain
 * assignment.
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
 * Multi-target APPLY (FORMAT.md §6.3 amendment): "APPLY ... applies the set
 * to EVERY PLL." `applySetToTarget()` only READS `setData` (builds a fresh
 * `.value` object per row; never mutates the set or any row in place), so
 * reusing the same `setData` across every target here is safe — no cloning
 * needed.
 */
function applySetToTargets(nodes, setData) {
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
        // target/set/name persist in the workflow; buttons+status opt out
        // individually via widget.serialize = false (see _addButton below).
        this.serialize_widgets = true

        this._w = {}
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

      // ------------------------------------------------------------ widgets

      _buildWidgets() {
        // Order matters beyond layout: every serialize:false widget below
        // (status + 4 buttons) MUST stay after every normally-serialized one
        // (target/set/name) — see the litegraph save/restore note in the
        // file header for why an interleaved order would corrupt reload.
        this._w.target = this.addWidget(
          'combo',
          'target',
          '',
          () => this._guarded('target changed', () => this._probeAndUpdateStatus()),
          { values: () => this._targetComboValues() }
        )

        // Choosing a set IS the apply (FORMAT.md §6.3, owner decision
        // 2026-07-18: the separate Apply button "was very confusing and I
        // thought it was broken") — there is no Apply button anymore.
        // 2026-07-19c: the combo's own `callback` option below is
        // deliberately a no-op — `_hookSetWidgetMenu()` replaces the click
        // path entirely (file header 2026-07-19c section), so this callback
        // is unreachable in normal operation. It's still passed so
        // `addWidget('combo', ...)` doesn't console.warn about a
        // callback-less combo.
        this._w.set = this.addWidget('combo', 'set', '', () => {}, { values: () => this._setComboValues() })
        // 2026-07-18c rename: display-only. `name` stays 'set' (serialize +
        // our own scanLoraRows-style lookups key off it); see file header's
        // `label` vs `name` citation for the BaseWidget mechanics this relies
        // on. VERIFY(live).
        this._w.set.label = 'state'
        // 2026-07-19c hardening: the controller owns this widget's click
        // entirely (see file header + _hookSetWidgetMenu/_openSetMenu below)
        // instead of shadowing setValue — see _onSetPicked() for why this
        // makes a same-value re-pick a non-issue by construction.
        this._hookSetWidgetMenu(this._w.set)

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

        this._w.captureBtn = this._addButton(LABEL_CAPTURE, () => this._onCaptureClick())
        this._w.updateBtn = this._addButton(LABEL_UPDATE, () => this._onUpdateClick())
        // Guarded like the target/set combo callbacks above (unlike capture/
        // update, this button's own synchronous body now does arm/disarm
        // bookkeeping — Object.defineProperty et al — so it gets the same
        // never-throw belt-and-suspenders. 2026-07-18c delete-bug fix.
        this._w.deleteBtn = this._addButton(LABEL_DELETE, () => this._guarded('delete click', () => this._onDeleteClick()))
        // FORMAT.md §6.3 Push State (2026-07-19): broadcasts to Apply LoRA
        // Set nodes, entirely independent of the rgthree target/probe this
        // pack drives above — deliberately NOT in `_actionButtons` below, so
        // it stays clickable even with no Power Lora Loader in the graph (or
        // rgthree not installed at all); see `_doPush()`.
        this._w.pushBtn = this._addButton(LABEL_PUSH, () => this._onPushClick())
        this._actionButtons = [this._w.captureBtn, this._w.updateBtn, this._w.deleteBtn]

        this._refreshTargetCombo()
        this._probeAndUpdateStatus()
      }

      _addButton(label, callback) {
        const widget = this.addWidget('button', label, null, callback, {})
        // widget.serialize (top-level) — NOT options.serialize — is what
        // LGraphNode.ts:986 checks when writing widgets_values. See file
        // header for the two-flags finding this depends on. VERIFY(live).
        widget.serialize = false
        return widget
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

      _setComboValues() {
        return this._setsCache.length ? this._setsCache.map((s) => s.label) : [PLACEHOLDER_NO_SETS]
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
        if (entry) {
          this._selectedSlug = entry.slug
          this._setSetValueSilently(entry.label)
        }
      }

      /**
       * The ONLY sanctioned way to write `this._w.set.value` from our own
       * code (Capture/Update select the newly-touched set; Delete falls
       * back to whatever is now first). 2026-07-19c: a plain, UNGUARDED
       * `.value =` assignment — it no longer needs to "silently" suppress
       * anything, since selection no longer goes through the combo's
       * callback/setValue at all (see the file header's 2026-07-19c section
       * and `_hookSetWidgetMenu()` below). Kept as a named helper purely for
       * readability at call sites, not for a guard it used to need.
       */
      _setSetValueSilently(label) {
        if (!this._w.set) return
        this._w.set.value = label
      }

      /**
       * FORMAT.md §6.3 2026-07-19c hardening — see the file header's
       * dated section for the full citation trail (verified against THIS
       * rig's exact bundle: comfyui-frontend-package 1.45.21,
       * static/assets/api-BqIxvqZ8.js). Shadows `.onClick` as an own
       * property on THIS widget INSTANCE — identity-stable per the header's
       * `toConcreteWidget`/`addCustomWidget` citation, so this is not the
       * same kind of internal-dependent trick the REMOVED
       * `_hookSetValueForReselect()` was; it replaces the stock
       * ComboWidget click -> dropdown -> `setValue()` path with our own
       * dropdown -> direct-apply path (`_openSetMenu()`/`_onSetPicked()`),
       * never touching `setValue` at all. `widget.callback` (passed to
       * `addWidget` in `_buildWidgets`) is deliberately a no-op now — it is
       * unreachable through any path we exercise, kept only so `addWidget`
       * doesn't console.warn about a callback-less combo.
       */
      _hookSetWidgetMenu(widget) {
        widget.onClick = ({ e, canvas }) =>
          this._guarded('set widget click', () => this._openSetMenu(e, canvas))
      }

      /**
       * Builds the controller's OWN dropdown for the `set`/state widget
       * (file header 2026-07-19c) — the exact `LiteGraph.ContextMenu(items,
       * {scale, event, className, callback})` shape stock `ComboWidget.onClick`
       * itself uses (file header citation), minus the `setValue()` detour.
       */
      _openSetMenu(event, canvas) {
        const labels = this._setComboValues()
        if (!labels.length || labels[0] === PLACEHOLDER_NO_SETS) {
          this._toast('warn', NODE_TITLE, 'No saved states yet — use New State first.')
          return
        }
        new LiteGraph.ContextMenu(labels, {
          event,
          scale: Math.max(1, canvas?.ds?.scale || 1),
          className: 'dark',
          callback: (label) => this._onSetPicked(label)
        })
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
       * FORMAT.md §6.3 2026-07-19c hardening: fired by `_openSetMenu()`'s
       * `LiteGraph.ContextMenu` callback — i.e. a REAL user pick, always
       * (see the file header's 2026-07-19c section for why nothing else can
       * reach this method: workflow restore only ever does a plain
       * `.value =` assignment, never `.onClick`). `label` is one of
       * `_setComboValues()`'s own strings, so a `_setsCache` lookup by exact
       * match always succeeds here. Runs apply UNCONDITIONALLY — there is
       * no same-value branch anywhere in this path, so re-picking the state
       * already showing re-applies exactly like picking a different one.
       */
      _onSetPicked(label) {
        this._guarded('set picked', () => {
          const entry = this._setsCache.find((s) => s.label === label)
          if (!entry) return
          this._selectedSlug = entry.slug
          this._setSetValueSilently(label)
          this.setDirtyCanvas(true, false)
          // A genuine user pick means any pending delete-confirm is now
          // about a DIFFERENT entry than what's on screen — disarm rather
          // than let a stale confirm click land on the old pick.
          this._disarmDeleteButton()
          this._onSetSelected()
        })
      }

      /**
       * Shared apply trigger, called only from `_onSetPicked()` above. Kept
       * as its own method purely for the `_runAction` wrapping/naming in
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
          button.name = LABEL_DELETE_CONFIRM
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
        button.name = LABEL_DELETE
        this._disarmDeleteButtonColor(button)
        this.setDirtyCanvas(true, false)
      }

      /**
       * File header's per-widget-color citation: shadow the inherited
       * getter-only `background_color`/`text_color` accessors with own
       * properties via `Object.defineProperty` (a plain assignment would
       * throw). Wrapped defensively — this is cosmetic only, and must never
       * be the reason the arm/disarm STATE machine itself breaks on some
       * future or different litegraph build.
       */
      _armDeleteButtonColor(button) {
        try {
          Object.defineProperty(button, 'background_color', {
            get: () => DELETE_ARMED_BG_COLOR,
            configurable: true
          })
          Object.defineProperty(button, 'text_color', {
            get: () => DELETE_ARMED_TEXT_COLOR,
            configurable: true
          })
        } catch (error) {
          api.warn(`${NODE_TITLE}: could not color the armed delete button (cosmetic only)`, error)
        }
      }

      _disarmDeleteButtonColor(button) {
        try {
          delete button.background_color
          delete button.text_color
        } catch (error) {
          api.warn(`${NODE_TITLE}: could not reset delete button color (cosmetic only)`, error)
        }
      }

      /**
       * FORMAT.md §6.3 amendment: with "All…" selected, APPLY writes the set
       * to every PLL — `targets` may hold 1 or N nodes, `probeTargets`/
       * `applySetToTargets` already handle both uniformly.
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
        applySetToTargets(targets, full)
        this._probeAndUpdateStatus()
        const rows = (full.loras || []).length
        const targetDesc =
          targets.length > 1
            ? `${targets.length} Power Lora Loaders`
            : `${targets[0].title || targets[0].type} #${targets[0].id}`
        this._toast(
          'success',
          NODE_TITLE,
          `Applied "${full.name}" -> ${targetDesc} (${rows} row${rows === 1 ? '' : 's'} each).`
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
       * FORMAT.md §6.3 amendment: with "All…" selected, CAPTURE reads from
       * the lowest-node-id PLL — `targets[0]` after `resolveTargetNodes()`'s
       * ascending sort.
       */
      async _doCapture() {
        const targets = resolveTargetNodes(this._w.target?.value)
        const probe = probeTargets(targets)
        if (!probe.ok) {
          this._toast('warn', NODE_TITLE, probe.message)
          return
        }
        const source = targets[0]
        const loras = await captureRows(source, { debugCapture: !!this.properties[PROP_DEBUG_CAPTURE] })
        const name = (this._w.name?.value || '').trim() || `State ${this._setsCache.length + 1}`
        const response = await api.postJson('/lora_library/set', {
          set: { format: 1, name, loras, trigger_words: '', notes: '' }
        })
        this._applySetsResponse(response)
        announceSetsChanged()
        this._selectSetBySlug(response.slug)
        if (this._w.name) this._w.name.value = ''
        const sourceNote =
          targets.length > 1 ? ` from ${source.title || source.type} #${source.id} (lowest id of ${targets.length})` : ''
        // FORMAT.md §6.3: "Show status" names the capture-source loader id +
        // row count on every capture/save.
        this._setStatusText(
          `Captured ${loras.length} row${loras.length === 1 ? '' : 's'} from ${source.title || source.type} #${source.id}.`
        )
        await this._toastRowsSaved('Saved', response.slug, sourceNote)
      }

      /** Same lowest-node-id source rule as _doCapture() — Update is a re-capture. */
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
        const source = targets[0]
        const loras = await captureRows(source, { debugCapture: !!this.properties[PROP_DEBUG_CAPTURE] })
        // Preserve the existing name/trigger_words/notes; only the rows
        // change on "Update" — best-effort GET, falls back to rows-only.
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
        const response = await api.postJson('/lora_library/set', {
          slug: entry.slug,
          set: { format: 1, name, loras, trigger_words, notes }
        })
        this._applySetsResponse(response)
        announceSetsChanged()
        this._selectSetBySlug(entry.slug)
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
        const sourceNote =
          targets.length > 1 ? ` from ${source.title || source.type} #${source.id} (lowest id of ${targets.length})` : ''
        // FORMAT.md §6.3: "Show status" names the capture-source loader id +
        // row count on every capture/save — set AFTER _probeAndUpdateStatus()
        // above so this is the status line's final word for this action,
        // not immediately overwritten by the generic probe message.
        this._setStatusText(
          `Captured ${loras.length} row${loras.length === 1 ? '' : 's'} from ${source.title || source.type} #${source.id}.`
        )
        await this._toastRowsSaved('Updated', entry.slug, sourceNote)
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
        const nextEntry = this._setsCache[0] || null
        this._selectedSlug = nextEntry?.slug || null
        this._setSetValueSilently(nextEntry?.label || '')
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
        const scopeNote = pushingAll ? '' : ` (target: "${targetValue}")`
        this._toast(
          'success',
          NODE_TITLE,
          `Pushed "${entry.name}" to ${count} Apply LoRA Set node${count === 1 ? '' : 's'}${scopeNote}.`
        )
      }
    }

    LiteGraph.registerNodeType(NODE_TYPE, LoraLibrarySetController)
    LoraLibrarySetController.category = NODE_CATEGORY
  } catch (error) {
    api.warn('registerControllerNode failed', error)
  }
}
