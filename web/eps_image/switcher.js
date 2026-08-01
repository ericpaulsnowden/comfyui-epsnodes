/**
 * @file EPS Switcher frontend (FORMAT.md §6.4). Exports the `init()`/
 * `attach(node)` hooks `web/eps_image.js` calls; `attach` no-ops for any
 * node class not listed in the `SWITCHER_CLASSES` registry below.
 *
 * **Generalized to four switcher classes** (originally `EPSSwitcher` only):
 * `SWITCHER_CLASSES` maps each backend class id to its own growing-socket
 * prefix and default input type -- `EPSSwitcher` (`image_N` / IMAGE, the
 * original class, still the concrete example used throughout the rest of
 * this file header), `EPSModelSwitcher` (`model_N` / MODEL),
 * `EPSClipSwitcher` (`clip_N` / CLIP), and `EPSVaeSwitcher` (`vae_N` / VAE).
 * Every mechanism below -- growing sockets, per-row toggle, header
 * tri-state, renamable rows, the `toggles` JSON bridge -- is driven off
 * that node's own registry entry (`switcherSpecOf(node)`) instead of a
 * single hardcoded class id / regex / type, so it applies identically to
 * all four; nothing about the MECHANISM changed, only what previously read
 * a fixed `IMAGE_INPUT_RE`/`'IMAGE'` now reads whichever entry matches the
 * node in hand. The verification narrative below (citations, line numbers,
 * live-drag testing on the rig) was carried out against `EPSSwitcher`'s
 * `image_N` specifically -- it documents the mechanism these four classes
 * all now share, not an independent re-verification of the three new ones.
 *
 * Three pieces, each borrowed from a proven pattern already in the
 * comfy_ps monorepo (cited FORMAT.md §6.4 asks for exactly these two
 * borrows) but reimplemented here with no runtime dependency on either
 * source:
 *
 * 1. **Growing `image_N` sockets** -- a direct structural port of
 *    comfyui-premiere-bridge's `web/cprb/nodes.js`
 *    `convergeVideoInputs`/`wireVideoInputGrowth` (video_N -> image_N).
 *    Same invariant: every CONNECTED `image_N` keeps its name/link
 *    untouched (never renumbered, a disconnected MIDDLE slot is left as a
 *    gap), and exactly one trailing EMPTY slot exists at all times, one
 *    past the highest connected `image_N`. Same double-hook shape
 *    (`configure` + `onConnectionsChange`) for the same reason: litegraph's
 *    own `configure()` restore loop
 *    (`LGraphNode.ts` -- see cprb/nodes.js's citation of the exact lines)
 *    calls `onConnectionsChange` once per restored slot with `isConnected`
 *    HARDCODED `true`, even for a slot whose own `.link` is `null` --
 *    reacting to that directly would misgrow on every workflow load/undo/
 *    paste, so a `state.restoring` guard blanks `onConnectionsChange`
 *    for exactly the duration of THIS node's own `configure()` call, and a
 *    single converge pass runs in `finally` once `this.inputs` is stable.
 *
 *    **Round-10 dead-rewire fix**: the LIVE `onConnectionsChange` path (a
 *    real user drag, not `configure()`) now SCHEDULES `convergeImageInputs`
 *    via `setTimeout(fn, 0)` instead of calling it synchronously, guarded so
 *    a burst of events coalesces into one deferred run (`wireImageInputGrowth`
 *    below). Root cause investigated per owner report ("rewiring a socket
 *    that was disconnected while toggled off goes dead"): `LGraphNode.ts`'s
 *    `removeInput(slot)` (~1726) calls `this.disconnectInput(slot, true)`
 *    FIRST, and `disconnectInput` (~3240) itself dispatches
 *    `this.onConnectionsChange?.(...)` (~3321) BEFORE it returns -- so the
 *    trailing-collapse case (disconnecting the HIGHEST connected `image_N`,
 *    where 2+ trailing empties must collapse to 1) previously called
 *    `node.removeInput()` on that SAME slot SYNCHRONOUSLY from inside
 *    litegraph's own `disconnectInput` call for that slot, splicing
 *    `this.inputs` while litegraph (and whatever mouse-gesture code called
 *    `disconnectInput`) was still mid-call on it. Live-verified with real
 *    drags against the dev rig's frontend (ComfyUI 0.28.0 /
 *    comfyui-frontend-package 1.45.21): this specific reentrant splice did
 *    NOT corrupt `this.inputs` there (nothing after the `onConnectionsChange`
 *    dispatch inside `disconnectInput` re-touches the slot by index; verified
 *    by reading to the end of the function) -- both the middle-gap and
 *    trailing-collapse disconnect/rewire sequences below (see `attach`'s
 *    call sites) worked via real mouse drags every time, and a queued fan-out
 *    run afterward produced the correct output count. The bug therefore
 *    could NOT be reproduced on 1.45.21. It is exactly the kind of
 *    internal-timing assumption that can differ on another frontend build
 *    (the owner's ComfyUI 0.28.1, never re-verified here -- see the file-level
 *    0.28.1 caveat), so deferring the restructuring past the entire
 *    synchronous mouse-event chain is shipped anyway, defensively: by the
 *    time the `setTimeout` fires, no litegraph-internal call frame is still
 *    holding a slot index or input reference into `this.inputs`, so the
 *    restructuring can never race a caller that assumes indices stay put.
 *    `configure()`'s own post-restore converge above is UNCHANGED (still
 *    synchronous, in `finally`, once per `configure()` call) -- that path is
 *    never nested inside a live litegraph mutation, so deferring it would
 *    only add risk (e.g. a paste/undo immediately followed by another
 *    `configure()` before the deferred call fires) for no benefit.
 *
 * 2. **Per-row on/off toggle** -- rgthree Power Lora Loader's per-row dot
 *    (`web/comfyui/power_lora_loader.js` `PowerLoraLoaderWidget.onToggleDown`)
 *    is UX borrowed, not code: PLL's rows are pure WIDGETS (a lora's
 *    on/off/strength lives entirely in a widget value, no real graph
 *    socket involved), so its click dispatch rides litegraph's built-in
 *    custom-widget `mouse()` protocol. Our `image_N` rows are REAL input
 *    sockets (actual image data has to flow through them), and sockets
 *    aren't part of that widget dispatch at all -- so the toggle here is
 *    drawn and hit-tested by hand, hooking `onDrawForeground`/`onMouseDown`
 *    directly (verified against a local ComfyUI_frontend 1.45.21 TS
 *    checkout, `src/lib/litegraph/src/`):
 *      - `LGraphCanvas.ts` (~2930) calls `node.onMouseDown?.(e, pos, this)`
 *        with `pos = [x - node.pos[0], y - node.pos[1])` -- but ONLY after
 *        an earlier input/output hit-test in the same function has already
 *        failed (comment there: "Click was inside the node, but not on
 *        input/output, or resize area"). So a toggle box can never be drawn
 *        ON TOP of an input's own clickable region, or the click would
 *        never reach `onMouseDown` at all.
 *      - That input hit-region is wide: `canvas/measureSlots.ts`
 *        `getNodeInputOnPos` reserves `20 + nameLength*7` px starting 10px
 *        left of the socket dot (~70-90px for a name like `image_12`) --
 *        so the toggle box is drawn LEFT-anchored, just past that reserved
 *        width (`drawRowToggles()`'s `boxX`, computed per row from the
 *        input's own name length), rather than from the node's right edge.
 *        An early version
 *        anchored from the right instead and visually collided with the
 *        node's single `images` OUTPUT label (live-verify screenshot,
 *        2026-07-19 rig session) -- that label's TEXT extends further left
 *        than its ~40px `getNodeOutputOnPos` hit-region alone would
 *        suggest, so a right-anchored margin big enough to clear the click
 *        zone was still not big enough to clear the drawn text. Anchoring
 *        from the left of each row (always well clear of the lone output,
 *        which only ever occupies the row-0 slot on the opposite edge)
 *        sidesteps the whole text-vs-hit-region distinction.
 *        `ensureMinNodeWidth` keeps enough room for this on any node width.
 *      - Row Y comes from `node.getConnectionPos(true, slotIndex)` (graph
 *        coords) minus `node.pos` -- the OLD/deprecated-but-still-present
 *        API, preferred here over the newer `getInputPos` specifically
 *        because it predates this fork's TS rewrite and is far more likely
 *        to exist unchanged on the owner's ComfyUI 0.28.1 frontend (see the
 *        file-level 0.28.1 caveat below).
 *
 * 3. **Header tri-state "toggle all"** -- PLL's header widget
 *    (`PowerLoraLoaderHeaderWidget`) draws itself via the SAME legacy
 *    custom-widget contract litegraph has supported for years: a plain
 *    widget object with `.type`, `.draw(ctx, node, width, y, height)`, and
 *    `.mouse(event, pos, node)`, added via `node.addCustomWidget(...)`.
 *    Unlike the per-row toggles, this one rides that stock dispatch
 *    (`LGraphNode.ts` `drawWidgets`: `if (typeof widget.draw ===
 *    'function') widget.draw(...)`; `LGraphCanvas.ts`
 *    `processWidgetClick`: `else if (widget.mouse) widget.mouse(...)`,
 *    labeled "Legacy custom widget callback" in that source -- i.e. this is
 *    the OLD, most-portable litegraph widget shape, safer across frontend
 *    versions than the newer `addTitleButton`/`title_buttons` API this
 *    fork also has). One wrinkle confirmed against that same source:
 *    `processWidgetClick` calls `widget.mouse()` TWICE per click -- once on
 *    mousedown, once again in `pointer.finally` replaying the mouseup
 *    event -- so `HEADER_WIDGET.mouse()` below only acts when
 *    `event.type` ends in "down", or a single click would toggle-all twice
 *    (a no-op that looks like the header silently does nothing).
 *    Positionally the header widget lands in the WIDGET stack, which
 *    litegraph always lays out below every input/output socket -- there is
 *    no supported way to place a widget above the growing `image_N` row
 *    stack, so "header" here means "first (only) widget, directly under
 *    the last image_N socket" rather than literally above the title. This
 *    is a deliberate M1 trade-off, not an oversight.
 *
 * 4. **Renamable rows** (round-10, FORMAT.md §6.4 "Renamable rows") --
 *    double-clicking an `image_N` row opens `LGraphCanvas.prompt` (confirmed
 *    present on this fork; falls back to `window.prompt` otherwise) and
 *    sets `input.label`, display-only: `input.name` and every `toggles` key
 *    stay the frozen `image_N` (backend contract untouched -- confirmed live
 *    by inspecting the actual `/prompt` payload a queued run sends: it
 *    carries only names, never a label). Two hooks cover the whole row
 *    because litegraph dispatches double-clicks differently depending on
 *    WHERE they land: `onInputDblClick(index, e)` fires for a click within
 *    litegraph's OWN input hit-region (the socket dot and its name/label
 *    text -- `LGraphCanvas.ts`'s inputs loop registers this and `return`s
 *    before reaching anything else); `onDblClick(e, pos, canvas)` fires for
 *    a click anywhere ELSE in the node body -- our toggle checkbox (point 2
 *    above; deliberately outside that same input hit-region) or blank row
 *    space. Both were live-verified (real double-clicks, both a connected
 *    row and the trailing spare). The header widget's own clicks are
 *    dispatched through a third, earlier branch (`getWidgetOnPos` /
 *    `processWidgetClick`), so `onDblClick` never fires for it; the title
 *    bar is excluded explicitly (`pos[1] < 0`, litegraph's own signal for
 *    "this was the title"). `drawRowToggles` (point 2) measures `label ||
 *    name` (not just `name`) for its width math, matching
 *    `measureSlots.ts`'s own precedence, so a long label can't collide with
 *    the toggle box. **Persistence**: FORMAT.md §6.4 asks to verify
 *    `input.label` survives save/reload before trusting it, with a
 *    node-property fallback if the fork drops it. Confirmed unnecessary
 *    here on two levels: `node/slotUtils.ts`'s `inputAsSerialisable`
 *    (called from `LGraphNode.serialize()`) explicitly destructures `label`
 *    into the serialized POJO, and a live round trip (rename a connected
 *    row AND the spare, reload the page, re-read `node.inputs[i].label`)
 *    came back unchanged both times. Plain `input.label` is therefore the
 *    only mechanism used -- no node-property map.
 *
 * **`toggles` is the enabled-set bridge to the backend** (module docstring,
 * e.g. `eps_image/nodes_switcher.py` for `EPSSwitcher` -- each sibling class
 * carries the identical widget in its own backend module): a hidden
 * (`.hidden = true`, same trick FORMAT.md §7.2 uses for the Prompt
 * Notebook's `file` widget) STRING widget holding a JSON object of
 * `{"<prefix>_N": false}` overrides -- absent key means enabled. Every
 * toggle/connection-change call here keeps it in
 * lockstep so the backend's filtering (which is authoritative; this file
 * only drives what the backend already trusts) always matches what's on
 * screen, and pruned to slots that still exist so it never grows unbounded
 * across many connect/disconnect cycles.
 *
 * **0.28.1 caveat**: this file was built and live-verified against the dev
 * rig's ComfyUI 0.28.0 / `comfyui-frontend-package` 1.45.21. Every litegraph
 * API used above is checked against that fork's TS source directly (see
 * citations); `getConnectionPos`, `onDrawForeground`, `onMouseDown`,
 * `addCustomWidget`, and the legacy `draw`/`mouse` widget contract are all
 * old, pre-rewrite APIs kept for backward compatibility, so they are the
 * LOW-risk choices here -- but this was not re-verified against whatever
 * frontend version ships with the owner's ComfyUI 0.28.1, and every failure
 * path below is a `console.warn` + graceful no-op rather than a thrown
 * error, on purpose.
 */

import { app } from '../../../scripts/app.js'

const PREFIX = '[eps_image:switcher]'

/**
 * Registry of every backend class this file's per-node experience attaches
 * to. `prefix` names that class's growing optional inputs (`<prefix>_N`);
 * `type` is the socket/default type for a freshly-added slot (see
 * `addImageInput`'s template fallback below). `inputRe` is precompiled once
 * here, `^<prefix>_(\d+)$` -- the exact shape `IMAGE_INPUT_RE` used to be,
 * now per-class -- rather than rebuilt on every call. `EPSSwitcher` is the
 * original, live-verified class (file header above); the other three reuse
 * every mechanism in this file unchanged, parameterized only by their own
 * entry here. Exported (read-only in practice) so tests/test_switcher_js.py
 * can pin its contents directly, the same convention distributor.js uses
 * for its own pure constants.
 */
export const SWITCHER_CLASSES = {
  EPSSwitcher: { prefix: 'image', type: 'IMAGE' },
  EPSModelSwitcher: { prefix: 'model', type: 'MODEL' },
  // `noun` overrides the header label's naive `<prefix>s` pluralization
  // where the prefix is an initialism -- "no CLIPs connected" reads right,
  // "no clips connected" reads like film clips.
  EPSClipSwitcher: { prefix: 'clip', type: 'CLIP', noun: 'CLIPs' },
  EPSVaeSwitcher: { prefix: 'vae', type: 'VAE', noun: 'VAEs' }
}
for (const spec of Object.values(SWITCHER_CLASSES)) {
  spec.inputRe = new RegExp(`^${spec.prefix}_(\\d+)$`)
}

const TOGGLES_WIDGET_NAME = 'toggles'
const HEADER_WIDGET_NAME = '__eps_switcher_toggle_all'

const ROW_BOX = 12
//: Matches canvas/measureSlots.ts's getNodeInputOnPos reserved width
//: (`20 + nameLength*7`, starting ~10px left of the dot) plus a small pad --
//: see the file header's "Per-row on/off toggle" note for why this is
//: LEFT-anchored rather than measured from the node's right edge.
const ROW_LABEL_PAD = 12
const ROW_MIN_X = 92
//: Half-height of the Y band (around a row's getConnectionPos) that counts
//: as "this row" for double-click rename hit-testing (file header point 4).
//: Rows are ~20px apart (litegraph's default slot height); 9 leaves a small
//: deadzone between adjacent rows rather than an ambiguous overlap.
const ROW_HIT_HALF_HEIGHT = 9
const HEADER_BOX = 12
const HEADER_ROW_HEIGHT = 20
const MIN_NODE_WIDTH = 200

/** Nodes we've already wired, guarding against a double `nodeCreated`. */
const attachedNodes = new WeakSet()

// ---------------------------------------------------------------------------
// Node / widget lookups
// ---------------------------------------------------------------------------

/** @param {object} node @returns {string|null} */
function nodeClassOf(node) {
  if (!node) return null
  if (node.comfyClass) return node.comfyClass
  if (node.constructor && node.constructor.comfyClass) return node.constructor.comfyClass
  return null
}

/**
 * @param {object} node
 * @returns {{prefix: string, type: string, inputRe: RegExp}|null} *node*'s
 *   own SWITCHER_CLASSES entry, or null for any node class this file does
 *   not attach to. Every function below that used to read the single
 *   `IMAGE_INPUT_RE`/`'IMAGE'` constants now calls this instead -- always
 *   non-null in practice, since none of them ever run against a node
 *   `attach()` didn't already gate through the same lookup, but every call
 *   site still degrades gracefully (falls back to the original `image`/
 *   `IMAGE` literals) rather than trust that invariant blindly.
 */
function switcherSpecOf(node) {
  return SWITCHER_CLASSES[nodeClassOf(node)] || null
}

function findWidget(node, name) {
  return node.widgets?.find((w) => w && w.name === name)
}

function getTogglesWidget(node) {
  return findWidget(node, TOGGLES_WIDGET_NAME) || null
}

// ---------------------------------------------------------------------------
// `toggles` state: the serialized bridge to nodes_switcher.py's execute()
// ---------------------------------------------------------------------------

/**
 * Parses the `toggles` widget's current JSON value. Never throws -- a
 * malformed value (never expected from this file's own writes, but a
 * hand-edited workflow is always possible) degrades to "no overrides",
 * matching the backend's own `_parse_toggles` fallback.
 * @param {object} node
 * @returns {Record<string, boolean>}
 */
function readToggles(node) {
  const widget = getTogglesWidget(node)
  if (!widget) return {}
  try {
    const parsed = JSON.parse(widget.value || '{}')
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {}
  } catch (error) {
    console.warn(PREFIX, 'toggles value is not valid JSON; treating as {}', error)
    return {}
  }
}

function writeToggles(node, map) {
  const widget = getTogglesWidget(node)
  if (!widget) return
  widget.value = JSON.stringify(map)
}

/** Enabled unless explicitly recorded `false` -- mirrors the backend default. */
function isRowEnabled(node, name) {
  return readToggles(node)[name] !== false
}

function setRowEnabled(node, name, enabled) {
  const map = readToggles(node)
  if (enabled) delete map[name] // absent == enabled; keeps the JSON minimal
  else map[name] = false
  writeToggles(node, map)
}

function toggleRowEnabled(node, name) {
  setRowEnabled(node, name, !isRowEnabled(node, name))
}

/**
 * Drops toggle-map entries for slots that no longer exist OR are currently
 * disconnected. A disconnected slot's on/off override is meaningless (the
 * backend ignores an unconnected slot entirely), and -- the bug this guards
 * against -- keeping a stale `false` on a disconnected MIDDLE (gap) slot
 * would silently disable a DIFFERENT value later re-wired into that same
 * slot number. Clearing it on every converge means a freshly (re)wired socket
 * always starts enabled, matching the backend's absent-key-means-enabled
 * contract. A CONNECTED slot deliberately toggled off keeps its `false`
 * (toggling doesn't call converge, and converge preserves connected keys), so
 * an intentional off-state survives adding/removing OTHER slots and reload.
 */
function pruneToggles(node) {
  const widget = getTogglesWidget(node)
  if (!widget) return
  const map = readToggles(node)
  const connectedNames = new Set(
    imageInputEntries(node)
      .filter((entry) => entry.connected)
      .map((entry) => entry.name)
  )
  let changed = false
  for (const key of Object.keys(map)) {
    if (!connectedNames.has(key)) {
      delete map[key]
      changed = true
    }
  }
  if (changed) writeToggles(node, map)
}

/**
 * Hides the `toggles` widget's on-canvas row (kept as the serialized value
 * only) -- FORMAT.md §7.2's `.hidden = true` trick, called once at attach.
 */
function hideTogglesWidget(node) {
  const widget = getTogglesWidget(node)
  if (!widget) {
    console.warn(
      PREFIX,
      `${nodeClassOf(node)} node is missing its \`toggles\` widget; per-row state will not persist`
    )
    return
  }
  // BOTH flags, deliberately (2026-07-29, owner's "uptick in issues using my
  // mac" report): litegraph's canvas renderer hides on `widget.hidden`, but
  // the Vue-nodes renderer ("New node design") decides visibility from
  // `widget.options.hidden` (useProcessedWidgets.ts: `options.hidden ?? false`,
  // verified in this rig's frontend source maps) and IGNORES `widget.hidden`
  // -- so with only the canvas flag, this internal widget leaked into the Vue
  // node as a raw editable text field. Canvas mode ignores `options.hidden`
  // right back, so setting both is safe everywhere.
  widget.hidden = true
  widget.options = { ...(widget.options || {}), hidden: true }
}

// ---------------------------------------------------------------------------
// Growing `image_N` sockets -- ported from cprb/web/cprb/nodes.js
// convergeVideoInputs/wireVideoInputGrowth (video_N -> image_N; see the
// file header for the exact citations this structure follows).
// ---------------------------------------------------------------------------

/**
 * @param {object} node
 * @returns {{idx: number, n: number, name: string, input: object, connected: boolean}[]}
 */
function imageInputEntries(node) {
  const entries = []
  const spec = switcherSpecOf(node)
  if (!spec) return entries
  const inputs = node.inputs || []
  for (let idx = 0; idx < inputs.length; idx++) {
    const input = inputs[idx]
    const match = input && spec.inputRe.exec(input.name)
    if (!match) continue
    entries.push({
      idx,
      n: Number(match[1]),
      name: input.name,
      input,
      connected: input.link != null
    })
  }
  return entries
}

function addImageInput(node, n, template) {
  const spec = switcherSpecOf(node)
  const prefix = spec ? spec.prefix : 'image'
  const type = template?.type ?? (spec ? spec.type : 'IMAGE')
  const extraInfo = template && template.shape !== undefined ? { shape: template.shape } : undefined
  return node.addInput(`${prefix}_${n}`, type, extraInfo)
}

/**
 * Grows/shrinks *node*'s growing-socket inputs (`image_N`/`model_N`/
 * `clip_N`/`vae_N`, per SWITCHER_CLASSES/switcherSpecOf) to the one
 * invariant: every CONNECTED slot keeps its name/link untouched, and
 * exactly one trailing EMPTY slot exists, numbered one past the highest
 * connected slot (slot 1 itself, empty, when nothing is connected).
 * Idempotent -- every call site below calls it unconditionally. A no-op
 * (via imageInputEntries'/addImageInput's own registry lookup) for any node
 * whose class isn't in the registry, though in practice this never runs
 * against one (attach() already gates on the same lookup).
 * @param {object} node
 */
function convergeImageInputs(node) {
  if (!node.inputs) return
  const entries = imageInputEntries(node)
  if (entries.length === 0) {
    // Defensive only: nodes_switcher.py's INPUT_TYPES always declares
    // image_1, so ComfyUI's own node construction gives every node this
    // one slot before nodeCreated ever runs.
    addImageInput(node, 1, null)
    return
  }

  let highestConnectedN = 0
  for (const entry of entries) {
    if (entry.connected && entry.n > highestConnectedN) highestConnectedN = entry.n
  }
  const desiredSpareN = highestConnectedN + 1
  const trailingEmpties = entries.filter((entry) => !entry.connected && entry.n > highestConnectedN)

  const alreadyConverged = trailingEmpties.length === 1 && trailingEmpties[0].n === desiredSpareN
  if (!alreadyConverged) {
    // Highest array index first: removeInput() splices node.inputs by
    // POSITION, so removing high-to-low keeps the remaining queued
    // indices valid.
    const removeIdxs = trailingEmpties.map((entry) => entry.idx).sort((a, b) => b - a)
    for (const idx of removeIdxs) node.removeInput(idx)
    addImageInput(node, desiredSpareN, entries[0].input)
  }

  pruneToggles(node)
}

/**
 * Chains *node*'s `configure` and `onConnectionsChange` so its growing-
 * socket inputs converge per convergeImageInputs() above. See the file
 * header for why two hooks (not one) and the `state.restoring` guard.
 * @param {object} node
 */
function wireImageInputGrowth(node) {
  // Resolved once (node's class never changes across its lifetime) rather
  // than re-derived on every onConnectionsChange -- see switcherSpecOf.
  const spec = switcherSpecOf(node)
  const state = { restoring: false, convergeScheduled: false }

  /**
   * Runs the actual convergeImageInputs() pass for a DEFERRED (live,
   * post-setTimeout) call. Guarded against a `configure()` having started
   * in the meantime (that path runs its own synchronous converge in
   * `finally` once restore completes, so a deferred call left over from
   * before the configure() would be redundant at best) and against the
   * node having been removed from the graph while the timeout was pending.
   * @param {object} target
   */
  function runDeferredConverge(target) {
    state.convergeScheduled = false
    if (state.restoring || !target.graph) return
    try {
      convergeImageInputs(target)
    } catch (error) {
      console.warn(PREFIX, 'convergeImageInputs (deferred) failed', error)
    }
  }

  /**
   * Schedules ONE convergeImageInputs() pass on the next macrotask --
   * see the file header's "Round-10 dead-rewire fix" for why this is
   * deferred rather than synchronous. `convergeScheduled` coalesces a burst
   * of connect/disconnect events (e.g. a fast drag) into a single pass, so
   * this is safe to call from every qualifying onConnectionsChange.
   * @param {object} target
   */
  function scheduleConverge(target) {
    if (state.convergeScheduled) return
    state.convergeScheduled = true
    setTimeout(() => runDeferredConverge(target), 0)
  }

  const originalConfigure = node.configure
  node.configure = function (...args) {
    state.restoring = true
    try {
      return originalConfigure.apply(this, args)
    } finally {
      state.restoring = false
      try {
        convergeImageInputs(this)
      } catch (error) {
        console.warn(PREFIX, 'convergeImageInputs (post-configure) failed', error)
      }
    }
  }

  const originalOnConnectionsChange = node.onConnectionsChange
  node.onConnectionsChange = function (type, index, isConnected, linkInfo, inputOrOutput) {
    let result
    if (typeof originalOnConnectionsChange === 'function') {
      result = originalOnConnectionsChange.apply(this, arguments)
    }
    if (!state.restoring && spec && spec.inputRe.test(inputOrOutput?.name || '')) {
      scheduleConverge(this)
    }
    return result
  }

  // A brand-new node already satisfies the invariant via its class-def
  // default image_1 socket -- cheap insurance against a future INPUT_TYPES
  // default change.
  convergeImageInputs(node)
}

// ---------------------------------------------------------------------------
// Header tri-state ("toggle all") logic
// ---------------------------------------------------------------------------

function connectedImageEntries(node) {
  return imageInputEntries(node).filter((entry) => entry.connected)
}

/** @returns {true|false|null} true=all on, false=all off (or none connected), null=mixed */
function allRowsState(node) {
  const entries = connectedImageEntries(node)
  if (entries.length === 0) return false
  let allOn = true
  let allOff = true
  for (const entry of entries) {
    const on = isRowEnabled(node, entry.name)
    allOn = allOn && on
    allOff = allOff && !on
  }
  if (allOn) return true
  if (allOff) return false
  return null
}

/** rgthree `toggleAllLoras` semantics: anything but "all on" -> turn all on; all on -> turn all off. */
function toggleAllRows(node) {
  const entries = connectedImageEntries(node)
  if (entries.length === 0) return
  const target = allRowsState(node) !== true
  for (const entry of entries) setRowEnabled(node, entry.name, target)
}

// ---------------------------------------------------------------------------
// Drawing
// ---------------------------------------------------------------------------

function drawToggleBox(ctx, x, y, size, enabled, mixed) {
  ctx.save()
  ctx.beginPath()
  if (typeof ctx.roundRect === 'function') ctx.roundRect(x, y, size, size, 3)
  else ctx.rect(x, y, size, size)
  ctx.fillStyle = mixed ? '#8a7a3a' : enabled ? '#4f9a44' : '#3a3a3a'
  ctx.strokeStyle = mixed ? '#d7c37a' : enabled ? '#a8dd93' : '#777777'
  ctx.lineWidth = 1
  ctx.fill()
  ctx.stroke()

  if (mixed) {
    ctx.beginPath()
    ctx.strokeStyle = '#2a2410'
    ctx.lineWidth = 2
    ctx.moveTo(x + size * 0.22, y + size * 0.5)
    ctx.lineTo(x + size * 0.78, y + size * 0.5)
    ctx.stroke()
  } else if (enabled) {
    ctx.beginPath()
    ctx.strokeStyle = '#153a10'
    ctx.lineWidth = 2
    ctx.lineJoin = 'round'
    ctx.lineCap = 'round'
    ctx.moveTo(x + size * 0.2, y + size * 0.55)
    ctx.lineTo(x + size * 0.42, y + size * 0.8)
    ctx.lineTo(x + size * 0.82, y + size * 0.22)
    ctx.stroke()
  }
  ctx.restore()
}

/**
 * Recomputes and draws every connected image_N row's toggle box, caching
 * their hit rects on the node for wireRowToggleClicks() to consume.
 * @param {object} node
 * @param {CanvasRenderingContext2D} ctx
 */
function drawRowToggles(node, ctx) {
  if (node.flags?.collapsed) return
  if (typeof node.getConnectionPos !== 'function') return

  const rects = []
  for (const entry of imageInputEntries(node)) {
    if (!entry.connected) continue
    let pos
    try {
      pos = node.getConnectionPos(true, entry.idx)
    } catch (error) {
      console.warn(PREFIX, 'getConnectionPos failed for', entry.name, error)
      continue
    }
    const localY = pos[1] - node.pos[1]
    // getNodeInputOnPos's reserved width for THIS row's own DISPLAYED text
    // (FORMAT.md §6.4 "Renamable rows"): litegraph draws `label || name` and
    // measureSlots.ts's getNodeInputOnPos sizes ITS OWN hit-region off the
    // same `label ?? localized_name ?? name` precedence, so a renamed row
    // with a long label needs the same width bump here, or the toggle box
    // would start drawing on top of litegraph's now-wider input hit-zone.
    const inputHitWidth = 20 + displayText(entry.input).length * 7
    const boxX = Math.max(inputHitWidth + ROW_LABEL_PAD, ROW_MIN_X)
    const boxY = localY - ROW_BOX / 2
    drawToggleBox(ctx, boxX, boxY, ROW_BOX, isRowEnabled(node, entry.name), false)
    rects.push({ name: entry.name, x: boxX, y: boxY, w: ROW_BOX, h: ROW_BOX })
  }
  node.__epsSwitcherRowRects = rects
}

function wireRowToggleDrawing(node) {
  const original = node.onDrawForeground
  node.onDrawForeground = function (ctx, canvas, canvasEl) {
    let result
    if (typeof original === 'function') result = original.apply(this, arguments)
    try {
      drawRowToggles(this, ctx)
    } catch (error) {
      console.warn(PREFIX, 'drawRowToggles failed', error)
    }
    return result
  }
}

/** @returns {boolean} true if the click hit a row toggle (and was handled). */
function handleRowToggleClick(node, localPos) {
  const rects = node.__epsSwitcherRowRects || []
  const [x, y] = localPos
  for (const rect of rects) {
    if (x >= rect.x && x <= rect.x + rect.w && y >= rect.y && y <= rect.y + rect.h) {
      toggleRowEnabled(node, rect.name)
      node.graph?.setDirtyCanvas(true, true)
      return true
    }
  }
  return false
}

function wireRowToggleClicks(node) {
  const original = node.onMouseDown
  node.onMouseDown = function (e, pos, canvas) {
    try {
      if (handleRowToggleClick(this, pos)) return true
    } catch (error) {
      console.warn(PREFIX, 'handleRowToggleClick failed', error)
    }
    if (typeof original === 'function') return original.apply(this, arguments)
    return false
  }
}

// ---------------------------------------------------------------------------
// Header "toggle all" widget (legacy custom-widget contract; see file header)
// ---------------------------------------------------------------------------

function addHeaderWidget(node) {
  if (findWidget(node, HEADER_WIDGET_NAME)) return
  if (typeof node.addCustomWidget !== 'function') {
    console.warn(PREFIX, 'node.addCustomWidget is unavailable; header toggle-all not added')
    return
  }

  // Drives the "no <noun> connected" header label below -- naive pluralized
  // prefix (image -> images, model -> models, clip -> clips, vae -> vaes).
  // For EPSSwitcher this reproduces the original hardcoded 'images' exactly.
  const spec = switcherSpecOf(node)
  const noun = spec ? spec.noun || `${spec.prefix}s` : 'images'

  const widget = {
    name: HEADER_WIDGET_NAME,
    type: 'custom',
    value: null,
    // Presentation-only control -- never persisted, never sent to the
    // backend (the real enabled-set lives in the `toggles` widget above).
    serialize: false,
    serializeValue: () => undefined,
    computeSize(width) {
      return [width ?? 0, HEADER_ROW_HEIGHT]
    },
    draw(ctx, drawNode, widgetWidth, y, height) {
      const entries = connectedImageEntries(drawNode)
      const state = allRowsState(drawNode)
      const boxX = 8
      const boxY = y + (height - HEADER_BOX) / 2
      drawToggleBox(ctx, boxX, boxY, HEADER_BOX, state === true, state === null)

      ctx.save()
      const textColor =
        (typeof LiteGraph !== 'undefined' && LiteGraph.WIDGET_SECONDARY_TEXT_COLOR) || '#999999'
      ctx.fillStyle = textColor
      ctx.font = '11px sans-serif'
      ctx.textAlign = 'left'
      ctx.textBaseline = 'middle'
      const enabledCount = entries.filter((entry) => isRowEnabled(drawNode, entry.name)).length
      const label =
        entries.length === 0
          ? `Toggle All (no ${noun} connected)`
          : `Toggle All  (${enabledCount}/${entries.length} enabled)`
      ctx.fillText(label, boxX + HEADER_BOX + 8, y + height / 2)
      ctx.restore()
    },
    mouse(event, pos, mouseNode) {
      // processWidgetClick() (LGraphCanvas.ts) replays this on mouseup too
      // (see file header) -- only react to the actual mousedown/pointerdown
      // to avoid a toggle-then-untoggle no-op per click.
      const type = event && event.type
      if (typeof type !== 'string' || !type.endsWith('down')) return false
      toggleAllRows(mouseNode)
      mouseNode.graph?.setDirtyCanvas(true, true)
      return true
    }
  }

  try {
    node.addCustomWidget(widget)
  } catch (error) {
    console.warn(PREFIX, 'addCustomWidget (header) failed', error)
  }
}

// ---------------------------------------------------------------------------
// Double-click rename (FORMAT.md §6.4 "Renamable rows"; see file header
// point 4)
// ---------------------------------------------------------------------------

/**
 * The text litegraph actually draws for *input* -- `label || name`, per
 * FORMAT.md §6.4 and measureSlots.ts's own precedence
 * (`input.label?.length ?? input.localized_name?.length ?? input.name?.length`).
 * @param {object} input
 * @returns {string}
 */
function displayText(input) {
  return (input && (input.label || input.name)) || ''
}

/**
 * Sets or clears *input*'s display label. `input.name` (the backend
 * kwargs/serialization contract) and the `toggles` map's keys (also names)
 * are untouched -- purely a display-layer change (FORMAT.md §6.4). An
 * empty/whitespace *label* resets to the socket name: the property is
 * DELETED rather than set to `""`, so `displayText`/litegraph's own
 * `label || name` fallback shows `name` again immediately.
 * @param {object} node
 * @param {object} input
 * @param {string} label
 */
function setInputLabel(node, input, label) {
  const trimmed = (label ?? '').trim()
  if (trimmed) input.label = trimmed
  else delete input.label
  node.graph?.setDirtyCanvas(true, true)
}

/**
 * Opens the rename editor for *input*: `LGraphCanvas.prompt` when this fork
 * has it (confirmed present on the dev rig's 1.45.21 -- `LGraphCanvas.ts`
 * `prompt(title, value, callback, event, multiline)`, the small styled
 * dialog rgthree's own widgets use), else a plain `window.prompt` fallback
 * so renaming still works on a fork that dropped the custom dialog.
 * `window.prompt` returns `null` on Cancel but `""` on an intentional
 * OK-with-empty-field -- `commit` only skips the `null` case, so clearing
 * the field still resets the label per FORMAT.md §6.4.
 * @param {object} node
 * @param {object} input
 * @param {object|null} canvas
 * @param {Event} event
 */
function promptForLabel(node, input, canvas, event) {
  const commit = (value) => {
    if (value == null) return
    setInputLabel(node, input, value)
  }
  if (canvas && typeof canvas.prompt === 'function') {
    canvas.prompt('Label', displayText(input), commit, event)
  } else {
    commit(window.prompt('Label', displayText(input)))
  }
}

/**
 * Best-effort active LGraphCanvas for callbacks that don't receive one
 * directly (`onInputDblClick` below) -- same `app.canvas` access pattern
 * already used by lora_library/controller.js.
 * @returns {object|null}
 */
function activeCanvas() {
  return app?.canvas ?? null
}

/**
 * @param {object} node
 * @param {number} localY node-local Y (graph Y minus node.pos[1])
 * @returns {{idx: number, n: number, name: string, input: object}|null} the
 *   growing-socket row at *localY* -- connected or the trailing spare -- or
 *   null if no row is close enough.
 */
function rowAtLocalY(node, localY) {
  if (typeof node.getConnectionPos !== 'function') return null
  for (const entry of imageInputEntries(node)) {
    let pos
    try {
      pos = node.getConnectionPos(true, entry.idx)
    } catch (error) {
      continue
    }
    if (Math.abs(localY - (pos[1] - node.pos[1])) <= ROW_HIT_HALF_HEIGHT) return entry
  }
  return null
}

/**
 * Wraps `onInputDblClick` and `onDblClick` so double-clicking a growing-
 * socket row -- connected or the trailing spare -- opens the rename prompt.
 * `onInputDblClick(index, e)` fires when the double-click lands within
 * litegraph's OWN input hit-region (the socket dot and its name/label text,
 * per `measureSlots.ts` `getNodeInputOnPos` -- `LGraphCanvas.ts`'s inputs
 * loop registers this and `return`s BEFORE reaching the widget-or-background
 * branch, so it and `onDblClick` are mutually exclusive per click).
 * `onDblClick(e, pos, canvas)` fires for a double-click anywhere ELSE inside
 * the node body -- e.g. our toggle checkbox area (deliberately drawn outside
 * litegraph's input hit-region, see file header point 2) or blank space in
 * the row. The header widget's clicks are dispatched via
 * `getWidgetOnPos`/`processWidgetClick` -- a THIRD, earlier branch in that
 * same function -- so a double-click on the header never reaches
 * `onDblClick` at all, and the title bar is excluded explicitly below via
 * `pos[1] < 0` (litegraph's own "this was the title" signal, per
 * `LGraphCanvas.ts`'s dblclick dispatch). Together the two hooks cover the
 * whole visual row (FORMAT.md §6.4's "double-clicking an image_N row")
 * without reproducing litegraph's own hit-region math twice.
 * @param {object} node
 */
function wireRowRename(node) {
  // Resolved once, same reasoning as wireImageInputGrowth's own `spec`.
  const spec = switcherSpecOf(node)
  const originalOnInputDblClick = node.onInputDblClick
  node.onInputDblClick = function (index, e) {
    let result
    if (typeof originalOnInputDblClick === 'function') {
      result = originalOnInputDblClick.apply(this, arguments)
    }
    try {
      const input = this.inputs?.[index]
      if (input && spec && spec.inputRe.test(input.name || '')) {
        promptForLabel(this, input, activeCanvas(), e)
      }
    } catch (error) {
      console.warn(PREFIX, 'onInputDblClick rename failed', error)
    }
    return result
  }

  const originalOnDblClick = node.onDblClick
  node.onDblClick = function (e, pos, canvas) {
    let result
    if (typeof originalOnDblClick === 'function') {
      result = originalOnDblClick.apply(this, arguments)
    }
    try {
      if (Array.isArray(pos) && pos[1] >= 0) {
        const entry = rowAtLocalY(this, pos[1])
        if (entry) promptForLabel(this, entry.input, canvas || activeCanvas(), e)
      }
    } catch (error) {
      console.warn(PREFIX, 'onDblClick rename failed', error)
    }
    return result
  }
}

// ---------------------------------------------------------------------------
// Node width floor (keeps the per-row toggle box clear of both the input
// label hit-region and the single `images` output's hit-region -- see file
// header point 2)
// ---------------------------------------------------------------------------

function ensureMinNodeWidth(node) {
  if (Array.isArray(node.size) && node.size[0] < MIN_NODE_WIDTH) {
    node.size[0] = MIN_NODE_WIDTH
  }
  const originalComputeSize = node.computeSize
  node.computeSize = function (...args) {
    const size =
      typeof originalComputeSize === 'function'
        ? originalComputeSize.apply(this, args)
        : [MIN_NODE_WIDTH, 60]
    if (Array.isArray(size)) size[0] = Math.max(size[0], MIN_NODE_WIDTH)
    return size
  }
}

// ---------------------------------------------------------------------------
// Public entry points (called from web/eps_image.js)
// ---------------------------------------------------------------------------

/** Frontend-only one-time setup. EPSSwitcher is a real backend node (no
 * frontend-only type registration needed, unlike lora_library's virtual
 * controller node) -- everything here is per-instance, done in attach().
 * Kept as an export because eps_image.js calls it unconditionally. */
export function init() {}

/** Per-node-instance attach; no-op unless *node*'s class is a key in
 * SWITCHER_CLASSES (EPSSwitcher, EPSModelSwitcher, EPSClipSwitcher,
 * EPSVaeSwitcher). */
export function attach(node) {
  try {
    if (!node) return
    if (!switcherSpecOf(node)) return
    if (attachedNodes.has(node)) return
    attachedNodes.add(node)

    hideTogglesWidget(node)
    ensureMinNodeWidth(node)
    wireImageInputGrowth(node)
    wireRowToggleDrawing(node)
    wireRowToggleClicks(node)
    addHeaderWidget(node)
    wireRowRename(node)
  } catch (error) {
    console.warn(PREFIX, 'attach failed', error)
  }
}
