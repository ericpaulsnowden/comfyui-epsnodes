/**
 * @file EPS Image Switcher frontend (FORMAT.md §6.4). Exports the `init()`/
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
 *    `configure()`'s own post-restore converge stayed synchronous (in
 *    `finally`, once per call) until v0.100.4, when it joined the same
 *    deferred path: litegraph's `clone()` -- what `_serializeItems` puts
 *    on the clipboard for Ctrl+C, and what Alt-drag makes -- configures a
 *    snapshot with every input link nulled and serializes it in the SAME
 *    tick, so a synchronous collapse there stripped every row past the
 *    first from the clipboard and Ctrl+Shift+V came back with one wire
 *    (rig 1.52.7, real keys). A paste/undo followed by another
 *    `configure()` before the deferred call fires is harmless: the runner
 *    checks `state.restoring`, and the pass is idempotent over whatever
 *    wiring it finds.
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
 * screen. A row's `false` is STICKY to its row number (v0.100.3,
 * `pruneToggles`): nothing but a click on that row or the header clears
 * it -- not a rewire, not a reload -- and only keys that cannot name a row
 * at all are ever pruned, so the map stays bounded by the row count.
 *
 * **Row partners -- WAN lows (v0.66.0) and masks.** A pairable class
 * carries a second socket directly under every `<prefix>_N` row and a TAIL
 * output index-aligned with the main one: `EPSModelSwitcher`'s
 * `model_N_low` + `models_low`, gated by the `High/low pairs` node
 * PROPERTY, and `EPSSwitcher`'s `mask_N` + `masks`, gated by the backend's
 * visible `pass_masks` WIDGET (owner ask 2026-10-31: "an option that is
 * off by default to also pass along the mask with every image. the mask
 * and image should always be passed together"; a widget rather than a
 * property because Properties are unreachable in Nodes 2.0). One `pair`
 * spec per class (`SWITCHER_CLASSES[...].pair`) drives both: the
 * converge/re-seat, the tail output's add/remove, the wired-refusal toast,
 * and the gate hook -- `onPropertyChanged` for the property, the widget's
 * own chained `callback` for the widget (a click, and a Universal State
 * Apply's `widget.callback?.(v, canvas, node)`, both land there). The one
 * asymmetry that matters: litegraph's `configure()` fires
 * `onPropertyChanged` for a restored property but NO callback for a
 * restored `widgets_values` entry, so the widget-gated partner is re-seated
 * synchronously in the configure wrap's `finally` -- paste-with-links
 * (`_deserializeItems`) connects wires by slot INDEX right after
 * `configure()` returns, and the def-first slot merge would otherwise
 * leave `mask_1` behind `toggles`/`pass_masks` where the wire lands on
 * the wrong socket. Partners never match `inputRe`: no checkbox, no
 * rename, no column entry of their own -- the row's one toggle governs
 * the pair, backend and frontend alike.
 *
 * **All-off looks unplugged (owner report 2026-11-05: "if all of the
 * toggles are off it should look like nothing is connected").** While every
 * WIRED row is toggled off -- or nothing is wired at all -- the switcher
 * emits only an `ExecutionBlocker` (FORMAT.md §6.4), and the pack's lazy
 * consumers (Run Multiplier, Resolution, Image Grid) now treat it exactly
 * like an unwired input. Every link on every OUTPUT (`images`/`models`/
 * `clips`/`vaes`, plus the `masks`/`models_low` tails) is then drawn in
 * §6.6 "Collect only"'s dim grey via the shared `owned_link_colour.js`
 * hook -- WITHOUT unplugging anything, and restored to its exact prior
 * colour the instant one row comes back on. `reconcileLinkDimming` is a
 * BY-HAND copy of image_grid.js's/save_image.js's (owner tag
 * `LINK_COLOR_OWNER_SWITCHER`, the shared `__epsLinkColorOwner` key, a
 * WeakMap stash of the exact prior colour, a per-node tracked Set, the
 * target's `__epsResyncLinkColors` called once on release, a link another
 * owner has claimed left alone). State is always DERIVED from `toggles` +
 * the wiring (`link.color` is never serialized), re-derived on a row
 * click, the header toggle-all, the `toggles` widget's own callback (a
 * Universal State Apply), every `onConnectionsChange` (input or output),
 * `onConfigure` (load/undo/redo/paste/tab switch), and `onRemoved` (force
 * undim). Not dimmed while `toggles` arrives over a wire (not statically
 * knowable -- the backend's own rule). No toast.
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
import { ensureOwnedLinkColourHook } from './owned_link_colour.js'

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
  // §6.4 masks: the IMAGE switcher's row partner is a MASK socket behind
  // the visible `pass_masks` widget -- its `pair` spec is set below.
  EPSSwitcher: { prefix: 'image', type: 'IMAGE', pairable: true },
  // §6.4 WAN pairing (v0.66.0): the MODEL switcher's row partner is the
  // high/low pair, enabled by the node's 'High/low pairs' property.
  EPSModelSwitcher: { prefix: 'model', type: 'MODEL', pairable: true },
  // `noun` overrides the header label's naive `<prefix>s` pluralization
  // where the prefix is an initialism -- "no CLIPs connected" reads right,
  // "no clips connected" reads like film clips.
  EPSClipSwitcher: { prefix: 'clip', type: 'CLIP', noun: 'CLIPs' },
  EPSVaeSwitcher: { prefix: 'vae', type: 'VAE', noun: 'VAEs' },
  // v0.109.0 (owner ask 2026-11-06): the fifth class, for LATENT dicts.
  EPSLatentSwitcher: { prefix: 'latent', type: 'LATENT', noun: 'latents' }
}
for (const spec of Object.values(SWITCHER_CLASSES)) {
  spec.inputRe = new RegExp(`^${spec.prefix}_(\\d+)$`)
}

//: v0.66.0 pairing property + names (owner spec: "call them high and low
//: ... an option that gets turned on in properties and is for all models
//: in that node").
const PROP_HIGH_LOW = 'High/low pairs'
const LOW_OUTPUT_NAME = 'models_low'
//: §6.4 masks: the gate is the backend's visible BOOLEAN widget
//: (nodes_switcher.py PASS_MASKS_WIDGET_NAME) and the tail output is
//: `masks` -- names shared with the backend, frozen once shipped (§8).
const PASS_MASKS_WIDGET_NAME = 'pass_masks'
const MASKS_OUTPUT_NAME = 'masks'

/**
 * Row-partner specs (file header "Row partners"), one per pairable class.
 * `inputRe` is the partner socket's own name shape -- deliberately NOT
 * `inputRe` above, so a partner never gets a checkbox of its own (the
 * row's one toggle governs the pair), never becomes a rename target and
 * never enters the column math; `nameOf(n)` builds row n's partner name;
 * `type`/`outputName` are the socket type and the tail output; exactly
 * one of `gateProperty`/`gateWidget` names what turns the partner on;
 * `title` is the toast summary when the node has no title of its own;
 * `refuseInputs`/`refuseOutput` are the wired-refusal toasts. Assigned
 * onto the registry entries (not inlined) so the registry's own
 * prefix/type shape, which tests/test_switcher_js.py pins, stays flat.
 */
SWITCHER_CLASSES.EPSModelSwitcher.pair = {
  inputRe: /^model_(\d+)_low$/,
  nameOf: (n) => `model_${n}_low`,
  type: 'MODEL',
  outputName: LOW_OUTPUT_NAME,
  gateProperty: PROP_HIGH_LOW,
  title: 'EPS Model Switcher',
  refuseInputs: 'Unwire the low-model sockets before turning High/low pairs off.',
  refuseOutput: 'Unwire the models_low output before turning High/low pairs off.'
}
SWITCHER_CLASSES.EPSSwitcher.pair = {
  inputRe: /^mask_(\d+)$/,
  nameOf: (n) => `mask_${n}`,
  type: 'MASK',
  outputName: MASKS_OUTPUT_NAME,
  gateWidget: PASS_MASKS_WIDGET_NAME,
  title: 'EPS Image Switcher',
  refuseInputs: 'Unwire the mask sockets before turning pass_masks off.',
  refuseOutput: 'Unwire the masks output before turning pass_masks off.'
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
//: Right-edge reserve for the OUTPUT side when clamping a shared column
//: (below): the socket dot (~10px) plus its right-aligned label, measured
//: with litegraph's own `length * 7` approximation (measureSlots.ts), plus
//: a small gap. Only ever binds on a narrow node or an unusually long
//: renamed label -- clearance still wins over it (see sharedColumnX).
const OUTPUT_LABEL_CHAR_W = 7
const OUTPUT_SOCKET_RESERVE = 16
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

/** Enabled unless *toggles* explicitly records `false` for *name* -- the
 * backend's own rule over an ALREADY-PARSED map (v0.68.1: the draw paths
 * parse `toggles` once per pass and read rows through this, instead of one
 * JSON.parse per row per frame -- distributor.js's `isSlotEnabled` twin). */
function isEnabledIn(toggles, name) {
  return toggles[name] !== false
}

/** Enabled unless explicitly recorded `false` -- mirrors the backend default. */
function isRowEnabled(node, name) {
  return isEnabledIn(readToggles(node), name)
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
 * Drops toggle-map entries that cannot name a row of this node at all -- a
 * key that is not `<prefix>_N` shaped (a hand-edited workflow, a sibling
 * class's prefix applied by mistake). Every real row key is otherwise
 * STICKY: a row's `false` belongs to the ROW NUMBER, not to the wire in it,
 * and only a click on that row (or the header) ever clears it. Bounded by
 * construction -- at most one key per row number ever switched off.
 *
 * v0.100.3 (owner report 2026-10-27: "5 models connected but only model 1
 * turned on, I still get 2 videos", and every video's embedded workflow
 * showed rows 1 AND 5 on): this used to ALSO drop every key whose slot was
 * UNWIRED at the moment a converge ran, so that a freshly (re)wired socket
 * always started enabled. But converge runs after every link change and
 * every configure(), and a row is unwired for the whole of any gesture that
 * lifts its wire -- picking it up and dropping it back (litegraph
 * disconnects on pick-up, the deferred converge fires mid-drag), parking
 * it on the canvas, deleting and re-adding the loader. The row the user had
 * switched off came back ON with nothing but its checkbox to say so, the
 * queued `toggles` lacked its `false`, and the backend ran it. Keeping the
 * key is the safe direction: a re-wired row that stays off is DRAWN off,
 * its box right there to click, whereas a row that flips on runs a model
 * nobody asked for. The old rationale (a stale `false` on a gap slot would
 * "silently" disable a different value re-wired there) is exactly that
 * visible case -- the backend ignores an unwired slot's key, allRowsState /
 * the header count read wired rows only, so a sticky key on an unwired
 * spare changes nothing until something is wired into it, and then it
 * shows. Rig-verified 1.52.7 (tests/test_switcher_js.py's attach probe
 * drives the same gestures under Node).
 */
function pruneToggles(node) {
  const spec = switcherSpecOf(node)
  const widget = getTogglesWidget(node)
  if (!widget || !spec) return
  const map = readToggles(node)
  let changed = false
  for (const key of Object.keys(map)) {
    if (!spec.inputRe.test(key)) {
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

/**
 * The node-def store's `nodeDefsByName`, or `null` when the frontend
 * internals have moved. Same access path (and same "never throw, just give
 * up") contract as `web/lora_library/controller.js`'s
 * `_fixLibraryDisplayName`, which has been live-verified on the rig.
 */
let cachedNodeDefsByName = null
function nodeDefsByName() {
  if (cachedNodeDefsByName) return cachedNodeDefsByName
  try {
    const el = document.querySelector('#vue-app') || document.querySelector('#app')
    const vue = el && el.__vue_app__
    const pinia = vue?.config?.globalProperties?.$pinia
    cachedNodeDefsByName = pinia?.state?.value?.nodeDef?.nodeDefsByName || null
    return cachedNodeDefsByName
  } catch {
    return null // no DOM (tests under Node) or the internals moved
  }
}

/**
 * Gives a GROWN socket (`image_2`, `model_3`, ...) the same hover tooltip
 * slot 1 already has.
 *
 * The frontend's `NodeTooltip.vue` resolves an input tooltip by NAME off the
 * node DEF (`nodeDef?.inputs[inputName]?.tooltip`), never off the litegraph
 * slot -- so setting `input.tooltip` here would do nothing. The backend only
 * declares `<prefix>_1` (every later slot is synthesized on demand by
 * `nodes_switcher.py`'s `_FlexibleOptionalImageInputs`), which left every
 * socket except the first with no tooltip at all.
 *
 * `ComfyNodeDefImpl.inputs` is the V2 view built once in the constructor
 * (`nodeDefStore.ts`); node CONSTRUCTION reads the separate V1 `input`
 * object, so adding a key here cannot add a socket to a freshly dropped
 * node. Verified against comfyui_frontend_package's own bundled TS source.
 *
 * Fully guarded: if any of that moves, the socket simply keeps today's
 * no-tooltip behavior.
 */
function mirrorSlotTooltip(node, name) {
  const spec = switcherSpecOf(node)
  if (!spec) return
  try {
    const defs = nodeDefsByName()
    const def = defs && defs[node.comfyClass || node.type]
    const inputs = def && def.inputs
    if (!inputs) return
    if (inputs[name]) return
    const first = inputs[`${spec.prefix}_1`]
    if (!first || !first.tooltip) return
    inputs[name] = { ...first, name }
  } catch (error) {
    console.warn(PREFIX, 'could not mirror the slot tooltip onto', name, error)
  }
}

function addImageInput(node, n, template) {
  const spec = switcherSpecOf(node)
  const prefix = spec ? spec.prefix : 'image'
  const type = template?.type ?? (spec ? spec.type : 'IMAGE')
  const extraInfo = template && template.shape !== undefined ? { shape: template.shape } : undefined
  const name = `${prefix}_${n}`
  if (n > 1) mirrorSlotTooltip(node, name)
  return node.addInput(name, type, extraInfo)
}

//: v0.66.0 WAN pairing helpers (generalized to every row partner) ------

/** *node*'s row-partner spec (`SWITCHER_CLASSES[...].pair`), or null for
 * a class without one. */
function pairSpecOf(node) {
  return switcherSpecOf(node)?.pair || null
}

/**
 * The partner gate's RAW value -- the Model switcher's property or the
 * Image switcher's `pass_masks` widget value -- or `undefined` when the
 * class has no partner or the gate does not exist yet (a property before
 * addProperty, a widget the backend did not declare). Callers that refuse
 * a flip compare against a literal `false`: only an explicit OFF is ever
 * reverted, never an absent gate.
 * @param {object} node
 */
function pairGateValue(node) {
  const pair = pairSpecOf(node)
  if (!pair) return undefined
  if (pair.gateWidget) return findWidget(node, pair.gateWidget)?.value
  return node.properties?.[pair.gateProperty]
}

/** Whether this node's row partners are ON (pairable classes only). */
function pairingOn(node) {
  return pairGateValue(node) === true
}

/** Write the gate DIRECTLY -- the property or the widget value, never
 * through its hook/callback, so a refusal cannot re-enter the converge. */
function setPairGate(node, on) {
  const pair = pairSpecOf(node)
  if (!pair) return
  if (pair.gateWidget) {
    const widget = findWidget(node, pair.gateWidget)
    if (widget) widget.value = on
    return
  }
  node.properties[pair.gateProperty] = on
}

/** Any partner socket or the partner output still carrying a wire. */
function pairWiresPresent(node) {
  const pair = pairSpecOf(node)
  if (!pair) return false
  const inputWired = (node.inputs || []).some(
    (input) => input && pair.inputRe.test(input.name) && input.link != null
  )
  const output = (node.outputs || []).find((output) => output?.name === pair.outputName)
  return inputWired || outputIsWired(output)
}

function outputIsWired(output) {
  if (!output) return false
  return (
    (Array.isArray(output.links) && output.links.length > 0) ||
    (typeof output._floatingLinks?.size === 'number' && output._floatingLinks.size > 0)
  )
}

/**
 * Refuse a gate-off: snap the gate back ON and say what to unwire
 * (resolution.js's hide-refusal posture -- never silently orphan a user's
 * wire). For the WIDGET gate the refusal is asserted a second time on the
 * next tick (bypass.js's `refuseOff`): the Vue renderer writes its own
 * model around the widget callback, and this makes the refusal win in
 * either order. A later real flip is never overridden -- the re-assert
 * only fires while a partner wire is still present.
 */
function refusePairOff(node, message) {
  setPairGate(node, true)
  toastSwitcher(node, message)
  if (!pairSpecOf(node)?.gateWidget) return
  setTimeout(() => {
    if (!node.graph || pairingOn(node) || !pairWiresPresent(node)) return
    setPairGate(node, true)
    node.graph.setDirtyCanvas(true, true)
  }, 0)
}

/** Re-seat an existing input, fixing every affected link-slot index. */
function moveInput(node, from, to) {
  if (from === to) return
  const [input] = node.inputs.splice(from, 1)
  node.inputs.splice(to, 0, input)
  const links = node.graph?.links
  if (!links) return
  const start = Math.min(from, to)
  for (let i = start; i < node.inputs.length; i++) {
    const linkId = node.inputs[i]?.link
    if (linkId == null) continue
    const link = typeof links.get === 'function' ? links.get(linkId) : links[linkId]
    if (link) link.target_slot = i
  }
}

/** Insert a fresh input at *at*, fixing the link-slot indices litegraph
 * stores by POSITION on every input after it (removeInput does its own
 * fixup; insertion has no built-in, so this is its mirror). */
function insertInputAt(node, name, type, at) {
  node.addInput(name, type)
  const input = node.inputs.pop()
  node.inputs.splice(at, 0, input)
  const links = node.graph?.links
  if (!links) return input
  for (let i = at; i < node.inputs.length; i++) {
    const linkId = node.inputs[i]?.link
    if (linkId == null) continue
    const link = typeof links.get === 'function' ? links.get(linkId) : links[linkId]
    if (link) link.target_slot = i
  }
  return input
}

/**
 * Converge the partner sockets (`model_N_low` / `mask_N`) to the gate
 * (owner spec 2026-08-14 for lows: high/low, per-node, all rows; the
 * same shape for masks). ON: every `<prefix>_N` row -- the trailing spare
 * included -- gets its partner directly BELOW it, so pairs read as pairs.
 * OFF: unwired partners are removed; a WIRED partner refuses the flip
 * (gate reverts + toast -- resolution.js's hide-refusal posture: never
 * silently orphan a user's wire).
 */
function convergePairInputs(node) {
  const pair = pairSpecOf(node)
  if (!pair || !node.inputs) return
  const on = pairingOn(node)

  if (!on) {
    // A wired partner SOCKET or a wired partner OUTPUT refuses the flip
    // here, BEFORE anything is removed. Checking the output here too (and
    // not only in convergePairOutput, which runs after this) keeps the
    // unwired partner sockets in place through the refusal -- otherwise
    // they were dropped, the gate reverted, and the rows sat partner-less
    // until the next converge (the mask probe caught it with only the
    // `masks` output wired).
    if (pairGateValue(node) === false && pairWiresPresent(node)) {
      const socketWired = node.inputs.some(
        (input) => input && pair.inputRe.test(input.name) && input.link != null
      )
      refusePairOff(node, socketWired ? pair.refuseInputs : pair.refuseOutput)
      return
    }
    for (let i = node.inputs.length - 1; i >= 0; i--) {
      const input = node.inputs[i]
      if (input && pair.inputRe.test(input.name) && input.link == null) {
        node.removeInput(i)
      }
    }
    return
  }

  // ON: primaries first (their converge already ran), then place
  // partners -- creating missing ones AND re-seating existing ones
  // directly under their primary (a workflow reload restores inputs in
  // file order, which can interleave `toggles` between a pair; wires are
  // name-bound so a seat move only needs the link-slot fixup
  // insertInputAt already does).
  const primaries = imageInputEntries(node)
  for (const entry of primaries) {
    const partnerName = pair.nameOf(entry.n)
    const primaryIdx = node.inputs.findIndex((input) => input?.name === entry.name)
    const at = node.inputs.findIndex((input) => input?.name === partnerName)
    if (at === -1) {
      insertInputAt(node, partnerName, pair.type, primaryIdx + 1)
    } else if (at !== primaryIdx + 1) {
      moveInput(node, at, at < primaryIdx ? primaryIdx : primaryIdx + 1)
    }
  }
  // Drop partners whose primary is gone (a collapsed trailing spare).
  const liveNs = new Set(primaries.map((entry) => entry.n))
  for (let i = node.inputs.length - 1; i >= 0; i--) {
    const input = node.inputs[i]
    const match = input && pair.inputRe.exec(input.name)
    if (match && !liveNs.has(Number(match[1])) && input.link == null) {
      node.removeInput(i)
    }
  }
}

/**
 * Show/hide the partner output (`models_low` / `masks`) with the gate. It
 * is the TAIL output (§8: tail add/remove is the one safe true removal),
 * so OFF removes it outright when unwired -- and refuses (revert + toast)
 * when wired -- and ON re-appends it at its canonical last slot.
 */
function convergePairOutput(node) {
  const pair = pairSpecOf(node)
  if (!pair) return
  const idx = (node.outputs || []).findIndex((output) => output?.name === pair.outputName)
  if (pairingOn(node)) {
    if (idx === -1) node.addOutput(pair.outputName, pair.type)
    return
  }
  if (idx === -1) return
  if (outputIsWired(node.outputs[idx])) {
    if (pairGateValue(node) === false) refusePairOff(node, pair.refuseOutput)
    return
  }
  node.removeOutput(idx)
}

function toastSwitcher(node, detail) {
  try {
    app.extensionManager?.toast?.add?.({
      severity: 'warn',
      summary: node.title || pairSpecOf(node)?.title || 'EPS Switcher',
      detail,
      life: 4000
    })
  } catch (error) {
    console.warn(PREFIX, 'toast failed', error)
  }
}

/**
 * The gate + its live hook -- pairable classes only. The Model switcher's
 * gate is a node PROPERTY (v0.66.0, owner spec: "an option that gets
 * turned on in properties"); the Image switcher's is the backend's visible
 * `pass_masks` WIDGET (Properties are unreachable in Nodes 2.0, and a
 * widget shows on the node in both renderers). Either hook is CHAINED,
 * never replaced -- `onPropertyChanged` for the property, the widget's own
 * `callback` for the widget (bypass.js's `wireToggle`: a click and a
 * Universal State Apply's `widget.callback?.(v, canvas, node)` both land
 * there) -- and both run the same reconverge: sockets, tail output, a
 * height that fits the new rows, a repaint.
 */
function wirePairingGate(node) {
  const pair = pairSpecOf(node)
  if (!pair) return
  const reconverge = (target) => {
    try {
      convergePairInputs(target)
      convergePairOutput(target)
      target.setSize?.([target.size[0], Math.max(target.size[1], target.computeSize()[1])])
      target.graph?.setDirtyCanvas(true, true)
    } catch (error) {
      console.warn(PREFIX, 'pair visibility failed', error)
    }
  }
  if (pair.gateWidget) {
    const widget = findWidget(node, pair.gateWidget)
    if (!widget) {
      console.warn(
        PREFIX,
        `${nodeClassOf(node)} node is missing its \`${pair.gateWidget}\` widget; ` +
          'partner sockets will not follow it'
      )
    } else {
      const original = widget.callback
      widget.callback = function (...args) {
        const result = typeof original === 'function' ? original.apply(this, args) : undefined
        reconverge(node)
        return result
      }
    }
  } else {
    node.addProperty(pair.gateProperty, false, 'boolean')
    const original = node.onPropertyChanged
    node.onPropertyChanged = function (name, value, prevValue) {
      const result = original?.call(this, name, value, prevValue)
      if (name === pair.gateProperty) reconverge(this)
      return result
    }
  }
  // Fresh node: the gate is off -> the backend-declared tail output comes
  // off until the gate turns it on (resolution.js's apply-once rationale:
  // addProperty alone never fires onPropertyChanged, and a widget's
  // construction default fires no callback).
  convergePairOutput(node)
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

  // Slots restored from a saved workflow never went through addImageInput,
  // so mirror the tooltip onto them here too (idempotent + memoized).
  for (const entry of entries) {
    if (entry.n > 1) mirrorSlotTooltip(node, entry.name)
  }

  pruneToggles(node)
  // v0.66.0: the low sockets + paired output track every high converge --
  // one choke point, so a grown/collapsed spare's partner can never lag.
  try {
    convergePairInputs(node)
    convergePairOutput(node)
  } catch (error) {
    console.warn(PREFIX, 'pair converge failed', error)
  }
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
   * Runs the actual convergeImageInputs() pass for a DEFERRED call -- a
   * live link change, or (v0.100.4) a `configure()` that just completed.
   * Guarded against a `configure()` having started in the meantime (it
   * schedules its own pass once restore completes, so one left over from
   * before it would be redundant at best) and against the node not being
   * in a graph: removed while the timeout was pending, or a `clone()`
   * litegraph made only to serialize for the clipboard and never added.
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
      // v0.100.4: DEFERRED -- the live path's own macrotask, never run
      // inside configure() itself. litegraph's `clone()` (the copy half of
      // Ctrl+C / Ctrl+Shift+V via `_serializeItems`, and Alt-drag)
      // configures a snapshot with EVERY input link nulled and serializes
      // it in the same tick; a synchronous collapse here emptied that
      // clipboard of every row past the first, and the paste then
      // reconnected the original links by slot INDEX into slots that no
      // longer existed, so only model_1 came back. By the time this fires
      // the paste's own connect() calls have landed and the pass sees the
      // real wiring; the runner skips a clone that never joins a graph.
      //
      // The WIDGET-gated partner (masks) is re-seated synchronously here,
      // and only here, BEFORE that deferral: litegraph's configure() fires
      // `onPropertyChanged` for a restored property -- which is how the
      // Model switcher's lows are already back under their highs by the
      // time configure() returns -- but sets a restored widgets_values
      // entry with NO callback. Without this, the def-first slot merge
      // leaves `mask_1` behind `toggles`/`pass_masks`, and the paste's
      // index-based connect() lands the mask wire on the wrong socket.
      // Safe for the clone() snapshot too: ON only inserts/moves (the
      // snapshot copied every slot), and OFF has nothing to remove --
      // a wired partner keeps the gate on, so no mask ever exists while
      // the gate is off. Idempotent with the deferred pass that follows.
      if (spec?.pair?.gateWidget) {
        try {
          convergePairInputs(this)
          convergePairOutput(this)
        } catch (error) {
          console.warn(PREFIX, 'pair re-seat after configure failed', error)
        }
      }
      scheduleConverge(this)
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

/** @returns {true|false|null} true=all on, false=all off (or none connected), null=mixed
 * *toggles* (v0.68.1): an already-parsed map, so a draw pass parses once. */
function allRowsState(node, toggles = readToggles(node)) {
  const entries = connectedImageEntries(node)
  if (entries.length === 0) return false
  let allOn = true
  let allOff = true
  for (const entry of entries) {
    const on = isEnabledIn(toggles, entry.name)
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
 * The right-edge ceiling for *node*'s toggle column: far enough left that a
 * box can't land under an output socket or its right-aligned label. Output
 * label widths use litegraph's OWN `length * 7` approximation
 * (canvas/measureSlots.ts) rather than ctx.measureText, for the same reason
 * the input side does -- mirroring litegraph's math is what keeps the box
 * clear of ITS regions, and a measured width would drift from it.
 * @param {object} node @returns {number}
 */
function outputSafeColumnX(node) {
  let widestLabel = 0
  for (const output of node.outputs || []) {
    const text = output?.label || output?.localized_name || output?.name || ''
    if (text.length > widestLabel) widestLabel = text.length
  }
  const reserve = OUTPUT_SOCKET_RESERVE + widestLabel * OUTPUT_LABEL_CHAR_W
  // v0.68.1: `node.size` is a Proxy over a typed array here, never an Array.
  const width = node.size ? node.size[0] : 0
  return Math.max(ROW_MIN_X, width - ROW_BOX - reserve)
}

/**
 * One switcher's own column requirement -- the x its rows need to clear
 * litegraph's input hit regions. PURE: derived from the row labels and
 * their wiring alone, with no canvas and no draw pass, so it reads the same
 * for an off-screen node as for a visible one. That is what lets every
 * switcher reach the same shared answer within a SINGLE frame (litegraph
 * only calls onDrawForeground for visible nodes, so a publish-as-you-draw
 * scheme would silently omit every switcher scrolled out of view, and the
 * column would jump as nodes entered the viewport).
 * `0` = no wired rows, i.e. no boxes and nothing to contribute.
 * @param {object} node @returns {number}
 */
function columnNeedOf(node) {
  let need = 0
  for (const entry of imageInputEntries(node)) {
    if (!entry.connected) continue
    const inputHitWidth = 20 + displayText(entry.input).length * 7
    const required = Math.max(inputHitWidth + ROW_LABEL_PAD, ROW_MIN_X)
    if (required > need) need = required
  }
  return need
}

/**
 * ONE COLUMN ACROSS EVERY SWITCHER IN THE GRAPH (owner ask 2026-08-14:
 * "for all of the EPS switchers, the checkboxes should right align in a
 * straight horizontal column based on where the furthest checkbox is").
 *
 * v0.59.1 aligned each node's own rows, which is why a single switcher
 * looked right -- but every node still picked its column from ITS OWN
 * longest label, so a stack of switchers with different renamed rows sat at
 * different x (rig-measured 2026-08-14: four 270px-wide switchers at x =
 * 95 / 144 / 186 / 92). This takes the MAX over every switcher in the SAME
 * graph (subgraphs and other tabs stay independent) via the pure
 * `columnNeedOf` above -- every node computes the identical value in the
 * same frame, so there is no publish step, no staleness and no redraw nudge.
 *
 * Two clamps, in priority order:
 *  - the shared column never goes past the SMALLEST `outputSafeColumnX`
 *    among the switchers that actually draw boxes, so no node's box lands
 *    under its own output labels. The ceiling is shared for the same reason
 *    the column is: a per-node ceiling puts them back at different x the
 *    moment clamping binds (rig-measured 2026-08-14: 200 / 207 / 214, ragged
 *    again, because each node's outputs are named differently);
 *  - but it never falls below the node's OWN `ownRequiredX` either --
 *    clearing litegraph's input hit region is functional (a box inside it
 *    starts a link-drag instead of toggling), so clearance outranks both
 *    the shared column and the ceiling. A node whose own labels are too long
 *    for its width therefore still puts its box past its right edge, exactly
 *    as before this change: that node needs widening, and forcing its box
 *    inward would only make it unclickable.
 *
 * v0.68.1 (2026-08-21): the graph walk itself lives in `graphColumnBounds`
 * and runs ONCE per canvas frame per graph -- it used to run inside every
 * visible switcher's onDrawForeground, i.e. (switchers x all nodes) per
 * frame. The result is node-independent (including the calling node in
 * the max/min is idempotent), so this function is now just the two clamps.
 * @param {object} node @param {number} ownRequiredX @returns {number}
 */
function sharedColumnX(node, ownRequiredX) {
  const bounds = graphColumnBounds(node.graph)
  const need = Math.max(ownRequiredX, bounds.need)
  const ceiling = Math.min(outputSafeColumnX(node), bounds.ceiling)
  return Math.max(ownRequiredX, Math.min(need, ceiling))
}

/** graph -> {frame, need, ceiling}: the walk below, memoized for the frame
 * it ran in (v0.68.1). A WeakMap, not a property on litegraph's graph. */
const columnBoundsCache = new WeakMap()

/**
 * The graph-wide column inputs: the MAX `columnNeedOf` and the MIN
 * `outputSafeColumnX` over every switcher in *graph* that draws boxes
 * (need 0 = no wired rows = neither raises nor constrains). Pure over
 * labels + wiring, so off-screen switchers count (the shared-column
 * invariant above).
 *
 * Memoized per CANVAS FRAME: litegraph's `LGraphCanvas.frame` counter
 * steps once per draw() (1.48.7, LGraphCanvas.ts), and every
 * onDrawForeground in one pass sees the same value, so a hit here is by
 * construction the SAME frame's answer -- never cross-frame state (the
 * offscreen-switchers test's stale-state rule). Rename/rewire/add/remove
 * all land between frames and the next pass recomputes. No frame counter
 * (another frontend) = no memo, the pre-v0.68.1 per-call walk.
 * @param {object|null|undefined} graph @returns {{need: number, ceiling: number}}
 */
function graphColumnBounds(graph) {
  const frame = app?.canvas?.frame
  const canMemo = graph != null && typeof frame === 'number'
  if (canMemo) {
    const cached = columnBoundsCache.get(graph)
    if (cached && cached.frame === frame) return cached
  }
  let need = 0
  let ceiling = Infinity
  const siblings = graph?._nodes
  if (Array.isArray(siblings)) {
    for (const other of siblings) {
      if (!switcherSpecOf(other)) continue
      const otherNeed = columnNeedOf(other)
      if (otherNeed === 0) continue // no boxes: neither raises nor constrains
      if (otherNeed > need) need = otherNeed
      const otherCeiling = outputSafeColumnX(other)
      if (otherCeiling < ceiling) ceiling = otherCeiling
    }
  }
  const bounds = { frame, need, ceiling }
  if (canMemo) columnBoundsCache.set(graph, bounds)
  return bounds
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

  // ONE ALIGNED COLUMN (owner ask 2026-08-09: "keep the checkboxes aligned
  // with each other ... aligned with the checkbox furthest to the right,
  // not randomly spaced based on the string length") -- the same fix
  // distributor.js's drawRowToggles already carries for its output side
  // (owner's 2026-07-27 ask there; see its comment). Pass 1 takes the
  // LONGEST drawn row's required x, pass 2 draws every box at it, so mixed
  // -length socket names give a straight edge instead of a ragged stagger.
  // Since v0.62.0 that column is shared with every OTHER switcher in the
  // graph too (sharedColumnX -- the 2026-08-14 follow-up ask).
  //
  // Per-row x is what has to clear litegraph's own input hit-region:
  // getNodeInputOnPos sizes that region off the row's DISPLAYED text
  // (`label ?? localized_name ?? name` -- FORMAT.md §6.4 "Renamable
  // rows"), so a renamed row with a long label reserves more width. Taking
  // the MAX satisfies every row's clearance at once (it is only ever
  // larger than each row's own requirement), which is why aligning is safe
  // here rather than a compromise.
  const drawn = []
  for (const entry of imageInputEntries(node)) {
    if (!entry.connected) continue
    let pos
    try {
      pos = node.getConnectionPos(true, entry.idx)
    } catch (error) {
      console.warn(PREFIX, 'getConnectionPos failed for', entry.name, error)
      continue
    }
    const inputHitWidth = 20 + displayText(entry.input).length * 7
    drawn.push({
      entry,
      localY: pos[1] - node.pos[1],
      requiredX: Math.max(inputHitWidth + ROW_LABEL_PAD, ROW_MIN_X)
    })
  }

  if (drawn.length === 0) {
    node.__epsSwitcherRowRects = []
    return
  }

  let ownRequiredX = ROW_MIN_X
  for (const row of drawn) {
    if (row.requiredX > ownRequiredX) ownRequiredX = row.requiredX
  }
  const boxX = sharedColumnX(node, ownRequiredX)

  const toggles = readToggles(node) // v0.68.1: one parse per draw pass, not per row
  const rects = []
  for (const row of drawn) {
    const boxY = row.localY - ROW_BOX / 2
    drawToggleBox(ctx, boxX, boxY, ROW_BOX, isEnabledIn(toggles, row.entry.name), false)
    rects.push({ name: row.entry.name, x: boxX, y: boxY, w: ROW_BOX, h: ROW_BOX })
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
      syncLinkDimming(node, 'row click')
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
    // Hover text. `NodeTooltip.vue` prefers a canvas widget's own
    // `.tooltip` over the node def's, which is the only route open to a
    // frontend-added widget like this one.
    tooltip:
      `Turn every ${noun.toLowerCase()} row on or off at once. ` +
      'The count beside it is how many rows are on right now — that is how ' +
      'many times the rest of the workflow will run.',
    // Presentation-only control -- never persisted, never sent to the
    // backend (the real enabled-set lives in the `toggles` widget above).
    serialize: false,
    serializeValue: () => undefined,
    // v0.100.4: `LGraphNode.setProperty` (right-click > Properties, the
    // properties panel's Assign) scans EVERY widget's `options.property`
    // with no guard on `options` itself -- without this object the
    // property applied and then the dialog threw. `serialize: false` here
    // is the PROMPT-side twin of the flag above (graphToPrompt reads
    // `options.serialize`; the workflow's widgets_values reads
    // `widget.serialize`), so neither payload changes. distributor.js's
    // header carries the identical object.
    options: { serialize: false },
    computeSize(width) {
      return [width ?? 0, HEADER_ROW_HEIGHT]
    },
    draw(ctx, drawNode, widgetWidth, y, height) {
      const entries = connectedImageEntries(drawNode)
      const toggles = readToggles(drawNode) // v0.68.1: one parse per draw pass
      const state = allRowsState(drawNode, toggles)
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
      const enabledCount = entries.filter((entry) => isEnabledIn(toggles, entry.name)).length
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
      syncLinkDimming(mouseNode, 'toggle all')
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
// All-off -> dim every outgoing wire (file header "All-off looks
// unplugged"). image_grid.js's "Collect only" link dimming, copied BY HAND
// (docs/ROADMAP-shared-panel-code.md: helpers are duplicated per file; grep
// every sibling before calling one fixed). The cross-file convention is
// distributor.js's: a link this file dims carries `LINK_COLOR_OWNER_KEY`,
// so image_grid.js, distributor.js, bypass.js and save_image.js leave its
// `.color` alone while it is claimed, and the TARGET node's optional
// `LINK_COLOR_RESYNC_HOOK` is called once on release. `link.color` is never
// serialized (image_grid.js's finding against the frontend source maps), so
// every load/undo/paste hands out undimmed links and this state is ALWAYS
// DERIVED, never stored.
// ---------------------------------------------------------------------------

/** image_grid.js's dim grey -- the same "not really there" look. */
const DIM_LINK_COLOR = 'rgba(128,128,128,0.25)'
const LINK_COLOR_OWNER_KEY = '__epsLinkColorOwner'
export const LINK_COLOR_OWNER_SWITCHER = 'EPSSwitcher:all-off'
const LINK_COLOR_RESYNC_HOOK = '__epsResyncLinkColors'

/** Per-link stash of the pre-dim colour, keyed by the LLink object itself
 * (its identity is its lifetime); `{color}` so a stashed `undefined` -- the
 * usual case -- is distinguishable from "never stashed". */
const dimStash = new WeakMap()

/** Same lookup as image_grid.js / save_image.js (own copy, by convention). */
function linkById(graph, linkId) {
  if (linkId == null || !graph) return null
  return graph.links?.[linkId] ?? graph.links?.get?.(linkId) ?? null
}

/** Whether the `toggles` widget's OWN input socket is wired (frontend 1.5x
 * gives every widget one) -- then the enabled set is only known at run
 * time, exactly the backend's `_switcher_is_statically_all_off` "a toggles
 * arriving as a LINK is not statically knowable", so nothing is dimmed. */
function isTogglesInputWired(node) {
  return (node.inputs || []).some(
    (inp) =>
      inp &&
      (inp.widget?.name === TOGGLES_WIDGET_NAME || inp.name === TOGGLES_WIDGET_NAME) &&
      inp.link != null
  )
}

/**
 * The pure "does this switcher emit nothing?" decision the dim follows --
 * the live-graph twin of nodes_switcher.py `_switcher_is_statically_all_off`
 * (and cross_sweep.js's `switcherIsStaticallyAllOff`): every WIRED
 * `<prefix>_N` row literally toggled off, or no row wired at all
 * (`allRowsState` false covers both); never while `toggles` itself is
 * wired. Row partners (`mask_N`, `model_N_low`) are not rows and never
 * count. Exported for tests.
 * @param {object} node @returns {boolean}
 */
export function isSwitcherAllOff(node) {
  if (!node || !switcherSpecOf(node)) return false
  if (isTogglesInputWired(node)) return false
  return allRowsState(node) === false
}

/** Every link id on EVERY output of *node* (the main output and any tail --
 * `masks`/`models_low` are blocked too when nothing is on). A missing/
 * malformed `.links` contributes nothing. Exported for tests. */
export function outputLinkIds(node) {
  const ids = []
  for (const output of node.outputs || []) {
    const links = output?.links
    if (!Array.isArray(links)) continue
    for (const id of links) if (id !== null && id !== undefined) ids.push(id)
  }
  return ids
}

/**
 * Dims or restores every output link to match the CURRENT all-off state
 * (`isSwitcherAllOff`) -- always derived (FORMAT.md §7.9's rebuild law), so
 * it is called from every place that can change the toggles or the wiring
 * (file header). Reconciles against `node.__epsSwitcherDimmedLinkIds` (a
 * per-instance Set; a rebuild hands the node a fresh one, which is right):
 * anything tracked that should no longer be dimmed gets its stashed colour
 * back EXACTLY, its owner tag cleared and its target's resync hook called
 * once; anything newly desired that nobody owns is stashed, dimmed and
 * tagged. A link another owner has claimed is left alone (and retried next
 * time). Change-gated repaint; never unplugs anything. Exported for tests.
 */
export function reconcileLinkDimming(node, { forceUndim = false } = {}) {
  if (!node) return
  const shouldDim = !forceUndim && isSwitcherAllOff(node)
  const graph = node.graph
  const tracked = node.__epsSwitcherDimmedLinkIds || (node.__epsSwitcherDimmedLinkIds = new Set())
  const desired = shouldDim ? new Set(outputLinkIds(node)) : new Set()
  let changed = false

  // Restore anything tracked that should no longer be dimmed.
  for (const id of tracked) {
    if (desired.has(id)) continue
    const link = linkById(graph, id)
    if (link && link[LINK_COLOR_OWNER_KEY] === LINK_COLOR_OWNER_SWITCHER) {
      const stash = dimStash.get(link)
      link.color = stash ? stash.color : undefined
      delete link[LINK_COLOR_OWNER_KEY]
      dimStash.delete(link)
      changed = true
      try {
        const target = graph?.getNodeById?.(link.target_id)
        const resync = target?.[LINK_COLOR_RESYNC_HOOK]
        if (typeof resync === 'function') resync()
      } catch (error) {
        console.warn(PREFIX, 'link colour resync hook failed', error)
      }
    }
    tracked.delete(id)
  }

  // Frontend 1.52.7 ignores `link.color` on a plain link; this hook makes
  // it draw a link we've claimed in our colour (owned_link_colour.js).
  if (desired.size) ensureOwnedLinkColourHook()

  // Dim anything newly desired that nobody else owns.
  for (const id of desired) {
    const link = linkById(graph, id)
    if (!link) continue
    const owner = link[LINK_COLOR_OWNER_KEY]
    if (owner && owner !== LINK_COLOR_OWNER_SWITCHER) continue
    if (!owner) {
      dimStash.set(link, { color: link.color })
      link.color = DIM_LINK_COLOR
      link[LINK_COLOR_OWNER_KEY] = LINK_COLOR_OWNER_SWITCHER
      changed = true
    }
    tracked.add(id)
  }

  if (changed) graph?.setDirtyCanvas?.(true, true)
}

/** `reconcileLinkDimming` behind this file's never-throw posture, for the
 * click/header call sites. */
function syncLinkDimming(node, where) {
  try {
    reconcileLinkDimming(node)
  } catch (error) {
    console.warn(PREFIX, `link dim sync (${where}) failed`, error)
  }
}

/**
 * Chains the hidden `toggles` widget's callback (never replaces it) -- a
 * Universal State Apply writes the value and calls
 * `widget.callback?.(v, canvas, node)`, which a row click never does.
 * Guarded on the WIDGET, like image_grid.js's `installModeDimSync`.
 */
function installTogglesDimSync(node) {
  const widget = getTogglesWidget(node)
  if (!widget || widget.__epsSwitcherDimSyncInstalled) return
  widget.__epsSwitcherDimSyncInstalled = true
  const originalCallback = widget.callback
  widget.callback = function (...args) {
    const result =
      typeof originalCallback === 'function' ? originalCallback.apply(this, args) : undefined
    syncLinkDimming(node, 'toggles callback')
    return result
  }
}

/**
 * Chains `onConnectionsChange` (never replaces): a wire landing on or
 * leaving ANY input or output. Cheap and idempotent, so it runs on every
 * call unfiltered -- including configure()'s restore loop, where it is
 * merely premature (the `onConfigure` wrap settles it once the toggles are
 * restored).
 */
function installLinkDimConnectionsSync(node) {
  if (node.__epsSwitcherLinkDimConnectionsWrapped) return
  node.__epsSwitcherLinkDimConnectionsWrapped = true
  const original = node.onConnectionsChange
  node.onConnectionsChange = function (...args) {
    const result = typeof original === 'function' ? original.apply(this, args) : undefined
    syncLinkDimming(this, 'onConnectionsChange')
    return result
  }
}

/**
 * Chains `onConfigure` (never replaces): it fires at the very end of
 * `LGraphNode.configure()` -- load, undo/redo, paste, tab switch -- with the
 * `toggles` widget already restored, so the dim is re-derived from the
 * final state (a fresh `LLink` is never dimmed on its own).
 */
function installLinkDimConfigureSync(node) {
  if (node.__epsSwitcherLinkDimConfigureWrapped) return
  node.__epsSwitcherLinkDimConfigureWrapped = true
  const original = node.onConfigure
  node.onConfigure = function (...args) {
    const result = typeof original === 'function' ? original.apply(this, args) : undefined
    syncLinkDimming(this, 'onConfigure')
    return result
  }
}

/**
 * Chains `onRemoved` (never replaces): restore anything still dimmed before
 * the node goes. Belt-and-suspenders -- removal disconnects the links
 * anyway -- but a tagged link must never outlive the node that dimmed it.
 */
function installLinkDimCleanup(node) {
  if (node.__epsSwitcherLinkDimCleanupWrapped) return
  node.__epsSwitcherLinkDimCleanupWrapped = true
  const original = node.onRemoved
  node.onRemoved = function (...args) {
    try {
      reconcileLinkDimming(this, { forceUndim: true })
    } catch (error) {
      console.warn(PREFIX, 'link dim cleanup (onRemoved) failed', error)
    }
    return typeof original === 'function' ? original.apply(this, args) : undefined
  }
}

// ---------------------------------------------------------------------------
// Node width floor (keeps the per-row toggle box clear of both the input
// label hit-region and the single `images` output's hit-region -- see file
// header point 2)
// ---------------------------------------------------------------------------

function ensureMinNodeWidth(node) {
  // v0.68.1 (2026-08-21): `node.size` is a Proxy over a typed-array view
  // (never an Array) on this frontend, so the old `Array.isArray` guard
  // silently skipped this lift; setSize() runs `_sizeUpdated` + onResize.
  if (node.size && node.size[0] < MIN_NODE_WIDTH) {
    if (typeof node.setSize === 'function') node.setSize([MIN_NODE_WIDTH, node.size[1]])
    else node.size[0] = MIN_NODE_WIDTH
  }
  const originalComputeSize = node.computeSize
  node.computeSize = function (...args) {
    const size =
      typeof originalComputeSize === 'function'
        ? originalComputeSize.apply(this, args)
        : [MIN_NODE_WIDTH, 60]
    // computeSize() hands back `out ?? [0, 0]` -- a caller-supplied typed
    // array is as valid as the plain default, so no Array test (v0.68.1).
    if (size) size[0] = Math.max(size[0], MIN_NODE_WIDTH)
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
    wirePairingGate(node)
    wireImageInputGrowth(node)
    wireRowToggleDrawing(node)
    wireRowToggleClicks(node)
    addHeaderWidget(node)
    wireRowRename(node)
    installTogglesDimSync(node) // all-off: dim/restore the outgoing wires
    installLinkDimConnectionsSync(node)
    installLinkDimConfigureSync(node)
    installLinkDimCleanup(node)
    reconcileLinkDimming(node)
  } catch (error) {
    console.warn(PREFIX, 'attach failed', error)
  }
}
