/**
 * @file EPS Image Grid frontend (FORMAT.md §6.6). Exports the `init()`/
 * `attach(node)`/`loadedGraphNode(node)` hooks `web/eps_image.js` calls;
 * each no-ops for every node type other than `EPSImageGrid`.
 *
 * M1: identity/dedup for the hidden `grid_uuid` widget, and a `Clear`
 * button. The thumbnail GRID ITSELF IS FREE from ComfyUI core — the backend
 * (`nodes_image_grid.py`) returns `{"ui": {"images": [...whole buffer...]}}`
 * on every Run, which core turns into `node.imgs` (its normal, unconditional
 * per-run image-preview handling — no different for an `OUTPUT_NODE` than
 * for `SaveImage`/`PreviewImage`). Because we always send the COMPLETE
 * current buffer rather than just what THIS run added, that ordinary
 * replace-per-run behavior is what makes the grid look like it's growing
 * across separate Runs — no custom widget, no code here at all for the grid
 * proper.
 *
 * M2 (this round): copy OUT (OS clipboard + ComfyUI clipspace) and paste IN
 * (Ctrl+V adds to the buffer).
 *
 * ---- Copy OUT is entirely free from core — verified against the actual
 * installed frontend source, not assumed ----
 *
 * `docs/FORMAT.md` §6.6 describes wiring "Copy image (clipboard)" / "Copy
 * (Clipspace)" as right-click menu items. Reading the exact ComfyUI
 * checkout this pack is built against (`ComfyUI_frontend/src/services/
 * litegraphService.ts` `addNodeContextMenuHandler`, which every node class
 * gets via the SAME generic registration path custom node defs go through
 * -- not a core-only allowlist) shows `LGraphNode.prototype.
 * getExtraMenuOptions` already adds, for ANY node:
 *   - "Copy Image" (OS clipboard: canvas -> `toBlob('image/png')` ->
 *     `navigator.clipboard.write([ClipboardItem])`, feature-detected on
 *     `window.ClipboardItem` and gated on `this.imgs` + a selected/hovered
 *     image) -- the EXACT mechanism FORMAT.md §6.6 names.
 *   - "Copy (Clipspace)" (`ComfyApp.copyToClipspace`, `scripts/app.ts`) --
 *     UNCONDITIONAL, not even gated on `this.imgs`.
 *   - "Paste (Clipspace)" (`ComfyApp.pasteFromClipspace`) -- shown whenever
 *     `ComfyApp.clipspace != null` (something has been copied by ANY node).
 *   - "Open Image" / "Save Image" (same `this.imgs` gate as Copy Image).
 * The Vue "More Options" popover surfaces equivalent items independently
 * (`composables/graph/useImageMenuOptions.ts` `getImageMenuOptions`, wired
 * from `useMoreOptionsMenu.ts` for any `hasImageNode` selection). Both
 * surfaces key off `node.imgs` (which core already populates for us every
 * Run, M1) -- there is nothing to build here; the file's own job is to
 * click-and-confirm these actually render for `EPSImageGrid`, which
 * `imageIndex` (set by clicking a cell in the free grid pager) already
 * targets at the specific cell the user selected.
 *
 * ---- Paste IN needs real code — `node.pasteFiles` ----
 *
 * There's no free equivalent for "add an image to this specific node's
 * durable buffer" (core's paste only ever replaces a node's in-memory
 * `imgs`/widget value, per `ComfyApp.pasteFromClipspace` above -- it never
 * calls any of our routes). FORMAT.md §6.6: "Ctrl+V on the selected node
 * uploads the pasted image (`POST /upload/image`) and appends it (`POST
 * /eps_image_grid/add`)". The real Ctrl+V dispatch (`composables/
 * usePaste.ts`'s global `document` "paste" listener) only routes pasted
 * image data to the SELECTED node when `isImageNode(node)` is true
 * (`utils/litegraphUtil.ts`: `previewMediaType === 'image' || (!video &&
 * imgs.length)`) -- otherwise it creates a brand-new `LoadImage` node
 * instead. Setting `node.previewMediaType = 'image'` in `attach()` (a
 * documented, typed node property other core code already sets this same
 * way for its own image-preview nodes, `composables/node/useNodeImage.ts`)
 * makes that true EVEN ON A STILL-EMPTY BUFFER (where `imgs.length` alone
 * would be falsy) -- the exact case that matters for "paste something
 * before you've ever Collected anything". Once routed, `usePaste.ts` calls
 * `node.pasteFiles?.(files)` (via `pasteItemsOnNode`); the SAME free
 * "Paste Image" menu item mentioned above (`useImageMenuOptions.ts`'s
 * `canPasteImage`, gated on `typeof node.pasteFiles === 'function'`) reads
 * the OS clipboard directly and calls the exact same function -- so
 * installing `pasteFiles` here (`installPasteFiles()` below) wires BOTH
 * the keyboard and the explicit menu path at once.
 *
 * `pasteFiles`/`useNodePaste`/`useNodeImageUpload` are themselves Vue
 * composables living in the app's own bundle (no stable import path for a
 * plain extension script), so the upload (`POST /upload/image`, core's
 * route) and the `/eps_image_grid/add` call are reimplemented directly
 * here rather than imported -- same shapes, verified against
 * `composables/node/useNodeImageUpload.ts`'s `uploadFile` for the request,
 * and `server.py`'s `image_upload` for the `{name,subfolder,type}`
 * response this hands straight to `/add`.
 *
 * ---- 0.28.1 / clipboard-API sensitivity ----
 *
 * Copy OUT (above) is core's own code, already feature-detected there
 * (`typeof window.ClipboardItem === 'undefined'` short-circuits `Copy
 * Image` to a no-op rather than throwing). Paste IN here uses the native
 * DOM `"paste"` event's `clipboardData` -- NOT `navigator.clipboard.read()`
 * -- so it needs no permission prompt and works the same across browsers
 * that support the clipboard event at all; the "Paste Image" MENU item
 * (free, core's) is the one path that does call `navigator.clipboard.read()`
 * and could differ across browsers/frontend versions. All of the above was
 * verified against this rig's installed `comfyui-frontend-package` 1.45.21
 * source, not the owner's actual ComfyUI 0.28.1 -- re-verify live if
 * anything here doesn't match.
 *
 * ---- Per-node identity + dedup (FORMAT.md §6.6) ----
 *
 * `grid_uuid` is a HIDDEN (`.hidden = true`, the same trick FORMAT.md §7.2
 * uses for the Prompt Notebook's `file` widget, and this pack's own
 * `nodes_switcher.py` `toggles` widget) but genuinely serialized STRING
 * widget — it has to reach the Python backend (which keys the on-disk
 * buffer by it), and `node.properties` alone never does that (properties
 * round-trip through save/reload but are never sent in a queued `/prompt`).
 * So this file keeps BOTH a `node.properties.uuid` copy (for our own
 * bookkeeping/dedup reads) and the widget's value (what execute() actually
 * sees) in lockstep.
 *
 * The dedup problem: litegraph only self-heals a node's own NUMERIC `.id`
 * on copy/paste and cross-workflow load — it happily restores an arbitrary
 * serialized property or widget value VERBATIM, uuid included. So a paste,
 * or opening a second saved workflow that already has a live node using the
 * same uuid, produces two nodes pointing at ONE buffer unless something
 * notices and re-mints one of them. Two hooks cover the two ways that
 * happens, confirmed against the exact ComfyUI checkout/`comfyui-frontend-
 * package` build this was built and verified against:
 *
 * 1. **`nodeCreated` (this file's `attach()`), deferred one tick** — the
 *    paste path. Litegraph creates the new node instance FIRST (firing
 *    `nodeCreated` synchronously, before any serialized data is applied),
 *    THEN calls `node.configure(clipboardData)` to restore the pasted
 *    node's widgets/properties — including the SAME `grid_uuid` the
 *    original has, which is the actual moment the collision is created.
 *    Checking synchronously inside `attach()` would only ever see the
 *    still-empty, just-constructed default; `setTimeout(fn, 0)` runs after
 *    that same-tick `configure()` call has already restored the real
 *    (possibly colliding) value, matching this pack's own already-verified
 *    finding for property restore timing (`eps_image/resolution.js`'s
 *    header: "`nodeCreated` ... always runs BEFORE `LGraphNode.configure()`
 *    for a saved workflow ... confirmed live and in `LGraphNode.ts`").
 *
 * 2. **`loadedGraphNode` (this file's `loadedGraphNode()`)** — the
 *    cross-workflow-load path (opening/dragging in a saved workflow).
 *    Verified directly against the installed `comfyui-frontend-package`'s
 *    bundled `dialogService-*.js`: after the WHOLE graph's own `configure()`
 *    call finishes (every node's widgets/properties already restored to
 *    their saved values), a SEPARATE traversal fires this hook once per
 *    node — `br(this.rootGraph, e => { ...; useExtensionService()
 *    .invokeExtensions("loadedGraphNode", e) })`. Since every sibling node
 *    is already fully configured by the time ANY node's `loadedGraphNode`
 *    fires, the collision check here runs synchronously against the whole
 *    graph, no deferral needed. This does NOT fire for a same-session paste
 *    (that never runs the whole-graph configure pass) — hence hook 1 above
 *    still being needed separately.
 *
 * Both funnel through the same idempotent `ensureUniqueUuid()`: a valid,
 * non-colliding uuid is left untouched; an empty/invalid one (first create)
 * or one that collides with a live sibling gets a fresh
 * `crypto.randomUUID()`, written to the widget's `.value` directly (that's
 * what actually persists/serializes -- confirmed live: a widget's
 * `.callback` is a NOTIFICATION hook, not a setter, so calling it alone
 * without also assigning `.value` leaves the old value in place) and then
 * ALSO through its `.callback`, if any, so anything else observing
 * widget-value changes sees it too. Since the 2026-07-20 fixes below,
 * `ensureUniqueUuid()` is `async`: the COLLISION branch (a genuine
 * live-sibling duplicate) additionally clones the OLD uuid's buffer into
 * the fresh one before returning; the first-create branch (empty/invalid
 * uuid, nothing to clone) still just mints and returns.
 *
 * ---- Bug fixes 2026-07-20: display-on-load + copy independence ----
 *
 * Two distinct owner reports, both rooted in the same fact: `node.imgs`
 * (what core's free thumbnail grid actually draws, M1) is populated ONLY by
 * a Run's `{"ui":{"images":[...]}}` response or the M2 paste-add path --
 * it is never serialized with the node, and NOTHING previously re-read the
 * on-disk buffer on load. So a reloaded, pasted, or undone node showed an
 * EMPTY grid until the next Run, even though the buffer was intact on disk
 * the whole time.
 *
 * 1. **"I copy-pasted the grid node from one workflow to a second workflow
 *    and the images didn't travel."** Two separate causes:
 *    - Display: opening a second workflow is exactly the `loadedGraphNode`
 *      path above -- it fixed the uuid but never told the canvas to show
 *      anything.
 *    - True in-graph duplicates (paste within the SAME graph, hook 1's
 *      collision branch): `ensureUniqueUuid()` minted a fresh uuid for the
 *      duplicate but never copied the source's buffer, so the fresh uuid
 *      pointed at a genuinely empty folder even after a display refresh.
 *      Fixed by cloning: `POST /eps_image_grid/clone {from: oldUuid, to:
 *      newUuid}` (`store.clone_buffer`) right after minting, so the
 *      duplicate gets its own independent copy of the images -- verified
 *      independent both ways (Clearing the original leaves the copy's
 *      images intact, and vice versa).
 * 2. **"I copied an image out of the grid into another node, then pressed
 *    undo a few times -- the grid cleared itself and the images never came
 *    back."** Undo/redo re-applies a node's PRIOR serialized state through
 *    `LGraphNode.configure()` directly -- it does NOT recreate the node
 *    instance, so `nodeCreated`/`attach()` never runs again. `node.imgs`
 *    was never part of that serialized state either way, so undo left
 *    whatever `node.imgs` happened to hold at that moment (frequently
 *    nothing) on screen -- looking exactly like the buffer itself had been
 *    wiped, when it was untouched on disk the entire time.
 *
 * The fix for both is the same primitive, `refreshFromBuffer(node)` (`GET
 * /eps_image_grid/list?uuid=` -> `setNodeImagesFromRefs`), called from
 * every place a node's uuid can become "the one to display" without a Run
 * ever happening: the deferred `nodeCreated` tail in `attach()` (after
 * `ensureUniqueUuid` settles, so it fetches the correct, possibly-just-
 * cloned uuid), `loadedGraphNode()` (after its own dedup call), and a
 * wrapped `onConfigure` (installed once per node, guarded by
 * `node.__epsGridConfigureWrapped` -- this is the undo fix, since
 * `onConfigure` is the one hook that fires on undo/redo without `attach()`
 * running first). Every one of these fails soft (a missing route on an
 * older backend, a network hiccup, or a not-yet-valid uuid all just leave
 * today's display untouched) -- this is pure ADDITIONAL visibility into a
 * buffer that was already safe, never a new way to lose data.
 *
 * ---- Identity is STABLE; refresh is CHEAP (fix 2026-07-21, owner-reported
 * regressions from the block above) ----
 *
 * The 2026-07-20 fix above was too aggressive. `loadGraphData` on this
 * frontend (`scripts/app.ts`) rebuilds the ENTIRE node list on every whole-
 * graph load -- initial open, undo/redo (`changeTracker.ts`'s `undo()`/
 * `redo()` both call `app.loadGraphData()`), and tab switch all go through
 * it (confirmed live against this rig's `comfyui-frontend-package` 1.45.21).
 * Each rebuild wipes `LGraph._nodes` and creates FRESH node object instances
 * for every serialized node, even ones whose data never changed. If a SECOND
 * rebuild starts before an EARLIER one's deferred `attach()` dedup check
 * (the `setTimeout(fn, 0)` below) has fired -- trivially reproduced live by
 * firing a few overlapping `app.loadGraphData()` calls without awaiting each
 * one, which is exactly what rapid repeated Ctrl+Z or fast tab-switching can
 * do -- a stale, about-to-be-discarded node instance and the fresh,
 * currently-displayed one briefly coexist in `app.graph._nodes` sharing the
 * SAME uuid. `ensureUniqueUuid()`'s collision check couldn't tell "a
 * genuine in-graph duplicate" apart from "the same logical node, mid-
 * rebuild, momentarily double-represented" -- so it fired the same
 * remint+clone either way, on nodes that were never actually duplicated.
 * Verified live: a single 13-image grid, reloaded a handful of times
 * without waiting for each to settle, logs multiple spurious "collided with
 * a live sibling" mints and clones -- harmless orphan buffers when the
 * CURRENT node happens to settle back onto the original uuid, but nothing
 * guaranteed that; the owner's report ("13 -> 4", thumbnails vanishing) is
 * this same race landing less kindly.
 *
 * The fix distinguishes the two cases with `app.configuringGraph`
 * (`scripts/app.ts`): a private counter incremented for the exact duration
 * of `LGraph.prototype.configure()`, which every whole-graph rebuild above
 * runs through. Confirmed LIVE against this rig's installed frontend by
 * instrumenting the pack's own registered extension hooks directly (not
 * just timing theory): our `nodeCreated` hook (`attach()` below) and a
 * wrapped `LGraphNode.prototype.configure` (what the `onConfigure` wrap
 * below observes) both read `app.configuringGraph === true` while a whole-
 * graph load/undo/redo/tab-switch is rebuilding nodes; a genuine paste/
 * clone (`LiteGraph.createNode()` + a LONE `node.configure()`, never
 * wrapped by the graph-level flag) reads `false` at both points.
 * `loadedGraphNode()` itself always observes `false` (it fires only AFTER
 * `rootGraph.configure()` has already returned) -- but it ONLY EVER fires
 * from within that same whole-graph rebuild in the first place (confirmed
 * live: never for a same-session paste), so its own invocation already IS
 * the "don't mint" signal, no flag needed there.
 *
 * `isGraphConfiguring()` below reads that flag; `attach()` captures it
 * SYNCHRONOUSLY at `nodeCreated` time (before deferring) rather than
 * re-reading it inside the deferred callback, because `LGraph.configure()`
 * is fully synchronous and reliably finishes -- flag back to `false` --
 * before any `setTimeout(fn, 0)` macrotask gets a turn. `ensureUniqueUuid()`
 * now takes an explicit `allowCollisionMint` flag: when false (this check
 * is running as part of a load/undo/configure pass), a valid uuid that
 * "collides" is left COMPLETELY untouched -- no remint, no clone, no
 * buffer-repointing side effect -- because the saved uuid is authoritative
 * there and the apparent collision is transient rebuild noise, never a real
 * duplicate. An actually invalid/empty uuid (a hand-edited or corrupted
 * workflow) still self-heals to a fresh one even during a load -- there is
 * no buffer to protect for a uuid that was never valid. `loadedGraphNode()`
 * always passes `allowCollisionMint: false` unconditionally, both because it
 * can't be true (see above) and as defense-in-depth if that ever changes.
 * Only a genuine interactive duplicate detected OUTSIDE any configure pass
 * (`attach()` when `isGraphConfiguring()` read false at creation time) still
 * mints + clones, preserving the 2026-07-20 "copy carries the images"
 * behavior for the case it actually describes.
 *
 * **0.28.1 risk:** `app.configuringGraph` is an internal flag (its own
 * comment in `scripts/app.ts` calls it exactly that) -- not documented
 * public extension API -- and its presence/behavior on the frontend build
 * ComfyUI 0.28.1 actually pins has NOT been independently verified (this rig
 * runs `comfyui-frontend-package` 1.45.21). `isGraphConfiguring()` reads it
 * via a plain property access and coerces with `Boolean(...)`, so a
 * frontend build where the property is simply absent degrades to `false`
 * (fails OPEN to the pre-2026-07-21 remint-on-collision behavior for that
 * one check) rather than throwing -- never worse than before 2026-07-21,
 * but re-verify live on 0.28.1 if this specific guard doesn't hold there.
 *
 * Separately, CHEAP: `refreshFromBuffer()` was being triggered up to THREE
 * times per node per load (the deferred `attach()` tail, `loadedGraphNode`,
 * and `onConfigure` each fire once for the very same settle) -- confirmed
 * live via the network log. `scheduleRefresh()` below is the single funnel
 * every call site now goes through: concurrent calls for the same node
 * coalesce onto one in-flight fetch, and a call arriving just after one
 * already settled is skipped outright (nothing changed in the meantime).
 * The `onConfigure` wrap additionally skips scheduling entirely while
 * `isGraphConfiguring()` is true, since `loadedGraphNode` is about to (or
 * just did) cover the exact same settle -- it still refreshes for a
 * standalone per-node `configure()` outside a load pass (a paste/clone
 * restore), the one case nothing else reaches. `setNodeImagesFromRefs()`
 * also no longer focuses the last image after a refresh or a paste-add --
 * it sets `imageIndex = null` (core's own "show the grid" sentinel) per
 * FORMAT.md §6.6, fixing the "paste shows one image, no way back to the
 * grid until you switch tabs" report; `setDirtyCanvas` still repaints
 * immediately, no tab round-trip required.
 *
 * ---- Clear button ----
 *
 * A plain `addWidget('button', ...)` (not a DOM widget — no layout beyond a
 * normal widget row is needed). `widget.serialize = false` is REQUIRED, not
 * cosmetic: `EPSImageGrid` is a real (non-virtual) backend node whose OTHER
 * widgets (`mode`, `grid_uuid`) restore from `widgets_values` positionally,
 * and this pack already found (`lora_library/controller.js`'s header
 * comment, "Two distinct ... serialize flags") that `widget.serialize`
 * (the top-level instance flag `LGraphNode.ts` checks when writing
 * `widgets_values`) — NOT `options.serialize` — is what actually keeps a
 * button out of a saved workflow's widget-value array; leaving it default
 * would insert an extra slot and risk desyncing that positional restore.
 */

import { api } from '../../../scripts/api.js'
import { app } from '../../../scripts/app.js'

const CLASS_ID = 'EPSImageGrid'
const PREFIX = '[eps_image:image_grid]'
const GRID_UUID_WIDGET_NAME = 'grid_uuid'
const CLEAR_BUTTON_LABEL = 'Clear'
const UUID_PROPERTY_NAME = 'uuid'

//: Mirrors the backend's own validation (`image_grid_store.py`
//: `_GRID_UUID_RE`) rather than re-deriving a second, possibly-drifting
//: regex -- deliberately a little looser than canonical UUID4.
const GRID_UUID_RE = /^[0-9a-fA-F-]{8,64}$/

/** Nodes we've already wired, guarding against a double `nodeCreated`. */
const attachedNodes = new WeakSet()

// ---------------------------------------------------------------------------
// Node / widget lookups (same idiom as switcher.js / resolution.js)
// ---------------------------------------------------------------------------

function nodeClassOf(node) {
  if (!node) return null
  if (node.comfyClass) return node.comfyClass
  if (node.constructor && node.constructor.comfyClass) return node.constructor.comfyClass
  return null
}

function findWidget(node, name) {
  return node.widgets?.find((w) => w && w.name === name)
}

function getGridUuidWidget(node) {
  return findWidget(node, GRID_UUID_WIDGET_NAME) || null
}

/**
 * Hides the `grid_uuid` widget's on-canvas row (kept as the serialized
 * value only) -- FORMAT.md §7.2's `.hidden = true` trick, called once at
 * attach.
 */
function hideGridUuidWidget(node) {
  const widget = getGridUuidWidget(node)
  if (!widget) {
    console.warn(
      PREFIX,
      'EPSImageGrid node is missing its `grid_uuid` widget; identity will not persist'
    )
    return
  }
  widget.hidden = true
}

// ---------------------------------------------------------------------------
// Identity + dedup (FORMAT.md §6.6) -- see file header for the hook-ordering
// citations.
// ---------------------------------------------------------------------------

function generateUuid() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  // Fallback for a context without `crypto.randomUUID` (very old browser,
  // or a non-HTTPS/non-localhost origin) -- FORMAT.md §6.6 names
  // `crypto.randomUUID()` specifically, but a same-shape fallback keeps the
  // node usable rather than throwing outright if it's ever unavailable.
  console.warn(PREFIX, 'crypto.randomUUID unavailable; using a fallback generator')
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0
    const v = c === 'x' ? r : (r & 0x3) | 0x8
    return v.toString(16)
  })
}

/** The node's current uuid, property taking precedence over the widget
 * (they're kept in lockstep by `writeUuid`; property is checked first
 * purely because it's cheaper to read and just as authoritative). */
function currentUuid(node) {
  return node.properties?.[UUID_PROPERTY_NAME] || getGridUuidWidget(node)?.value || ''
}

/**
 * Writes *uuid* into BOTH `node.properties.uuid` and the `grid_uuid`
 * widget's real `.value` (FORMAT.md §6.6) -- confirmed live that a
 * widget's `.callback` alone does NOT set `.value` (it's a notification
 * hook litegraph's own input handling calls AFTER assigning `.value`
 * itself, never a substitute for the assignment) -- and then also fires
 * the widget's `.callback`, if any, so anything else that reacts to a
 * widget-value change (ComfyUI's own dirty-canvas bookkeeping, a future
 * feature) still sees it.
 */
function writeUuid(node, uuid) {
  node.properties = node.properties || {}
  node.properties[UUID_PROPERTY_NAME] = uuid
  const widget = getGridUuidWidget(node)
  if (!widget) return
  // `.value =` is what actually persists/serializes (confirmed live: a
  // widget's `.callback` is a NOTIFICATION hook, not a setter -- litegraph's
  // own text-input handling always assigns `.value` itself and calls
  // `.callback` afterward as a side effect, never the other way around).
  // Set the value directly, THEN best-effort notify any callback so other
  // code reacting to a value change still sees this one.
  widget.value = uuid
  if (typeof widget.callback === 'function') {
    widget.callback(uuid, app.canvas, node)
  }
}

/** Every OTHER live `EPSImageGrid` node's current uuid. `app.graph._nodes`/
 * `.nodes` is this pack's own established idiom for walking the live graph
 * (`lora_library/controller.js`, `lora_library/sets.js`). */
function siblingUuids(node) {
  const nodes = app.graph?._nodes || app.graph?.nodes || []
  const uuids = new Set()
  for (const other of nodes) {
    if (!other || other === node) continue
    if (nodeClassOf(other) !== CLASS_ID) continue
    const uuid = currentUuid(other)
    if (uuid) uuids.add(uuid)
  }
  return uuids
}

/**
 * Whether a whole-graph rebuild (initial load, workflow open, undo/redo, or
 * a workflow-tab switch -- all route through `app.loadGraphData()` ->
 * `LGraph.prototype.configure()` on this frontend) is CURRENTLY in progress
 * -- FORMAT.md §6.6 "Identity is STABLE" (2026-07-21). `app.configuringGraph`
 * (`scripts/app.ts`) is the exact flag core wraps that method with; confirmed
 * LIVE against this rig's installed frontend that it reads `true` at both
 * `nodeCreated` time and per-node `configure()` time during such a rebuild,
 * and `false` at the equivalent points for a genuine interactive paste/clone
 * (see the file header's dated section for the full writeup + how each call
 * site below uses this). Coerced with `Boolean(...)` so a frontend build
 * missing this internal, non-public property reads `false` rather than
 * throwing -- see the header's "0.28.1 risk" note.
 */
function isGraphConfiguring() {
  return Boolean(app.configuringGraph)
}

/**
 * Mint a fresh uuid on first create (empty/invalid); mint + clone on
 * collision with a live sibling ONLY when *allowCollisionMint* is true;
 * otherwise leave a valid uuid -- colliding or not -- completely untouched
 * (re-syncing property<->widget only if they've somehow drifted apart).
 * Idempotent and cheap to call repeatedly -- both call sites below
 * (deferred `nodeCreated`, and `loadedGraphNode`) may run for the same node.
 *
 * *allowCollisionMint* is the 2026-07-21 fix (FORMAT.md §6.6 "Identity is
 * STABLE"): a whole-graph rebuild (load/undo/redo/tab-switch) can make an
 * untouched node's OWN saved uuid look like it "collides" against a stale
 * sibling instance left over from an overlapping rebuild generation -- see
 * the file header's dated section for the live-reproduced mechanism. That
 * is never a genuine duplicate, so callers pass `false` for it whenever this
 * check is running as part of such a rebuild (`isGraphConfiguring()`, or --
 * for `loadedGraphNode`, which only ever fires from inside one -- always).
 * Only a real interactive duplicate, detected OUTSIDE any rebuild, passes
 * `true` and actually mints + clones.
 *
 * `async` since the 2026-07-20 "images didn't travel to a copy" fix: the
 * COLLISION+mint branch (a genuine live-sibling duplicate -- NOT first-
 * create, see FORMAT.md §6.6) captures the OLD uuid before overwriting,
 * mints the fresh one, then clones the old buffer into the new uuid
 * (`POST /eps_image_grid/clone`) before scheduling a refresh, so the
 * duplicate carries its own independent copy of the original's images
 * instead of starting empty. Callers that don't care about completion can
 * ignore the returned promise; both call sites below await it specifically
 * so their OWN follow-up refresh fetches the settled (possibly just-cloned)
 * uuid rather than racing it.
 */
async function ensureUniqueUuid(node, { allowCollisionMint }) {
  if (nodeClassOf(node) !== CLASS_ID) return

  const widget = getGridUuidWidget(node)
  const propertyUuid = node.properties?.[UUID_PROPERTY_NAME] || ''
  const widgetUuid = widget?.value || ''
  const current = propertyUuid || widgetUuid
  const valid = GRID_UUID_RE.test(current)
  const colliding = valid && siblingUuids(node).has(current)

  if (valid) {
    if (colliding && !allowCollisionMint) {
      // Never remint/clone during a load/undo/configure pass -- the saved
      // uuid is authoritative here, and an apparent collision against a
      // transient sibling from an overlapping rebuild is not a real
      // duplicate. Leave the node -- and its buffer pointer -- untouched.
      return
    }
    if (!colliding) {
      if (propertyUuid !== widgetUuid) {
        writeUuid(node, current) // re-sync, e.g. a hand-edited workflow set only one of the two
      }
      return
    }
    // valid && colliding && allowCollisionMint -- a genuine interactive
    // duplicate; fall through to mint + clone below.
  }

  const oldUuid = current
  const fresh = generateUuid()
  writeUuid(node, fresh)

  if (!valid) {
    // First create (empty/invalid uuid, e.g. a brand-new node, or a
    // hand-edited/corrupted workflow value) -- fresh uuid, no clone: there
    // is no source buffer to copy from, load pass or not.
    return
  }

  console.log(
    PREFIX,
    `node #${node.id}: grid_uuid collided with a live sibling -- minted a new one ` +
      '(each EPSImageGrid keeps its own buffer)'
  )
  try {
    await postClone(oldUuid, fresh)
  } catch (error) {
    console.warn(
      PREFIX,
      'clone-on-collision failed; the duplicate starts with an empty buffer',
      error
    )
  }
  await scheduleRefresh(node)
}

// ---------------------------------------------------------------------------
// Clear button
// ---------------------------------------------------------------------------

async function postClear(grid_uuid) {
  const response = await api.fetchApi('/eps_image_grid/clear', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ uuid: grid_uuid })
  })
  let data = null
  try {
    data = await response.json()
  } catch {
    // Non-JSON body (proxy error page etc.) -- fall through to status check.
  }
  if (!response.ok) {
    const message = data && data.error ? data.error : `HTTP ${response.status}`
    throw new Error(message)
  }
  return data
}

/** Drops the node's own displayed thumbnails immediately, ahead of the next
 * Run's `ui.images` (which would otherwise be the only thing that refreshes
 * the canvas) -- Clear should feel instant. `imageIndex = null` keeps a
 * stale index from pointing past the now-empty array (FORMAT.md §6.6). */
function clearNodePreview(node) {
  node.imgs = []
  node.images = undefined
  node.imageIndex = null
  try {
    node.setSizeForImage?.()
  } catch {
    // Best-effort resize; a missing/erroring hook must not block the clear.
  }
  app.graph?.setDirtyCanvas(true, true)
}

async function onClearClicked(node) {
  const uuid = currentUuid(node)
  if (!GRID_UUID_RE.test(uuid)) {
    console.warn(PREFIX, 'Clear clicked with no valid grid_uuid yet; nothing to clear')
    return
  }
  try {
    await postClear(uuid)
  } catch (error) {
    console.warn(PREFIX, 'clear request failed', error)
    return
  }
  clearNodePreview(node)
}

function addClearButton(node) {
  if (findWidget(node, CLEAR_BUTTON_LABEL)) return
  const widget = node.addWidget('button', CLEAR_BUTTON_LABEL, null, () => onClearClicked(node), {})
  // See file header "Clear button" -- the top-level flag, not options.serialize.
  widget.serialize = false
}

// ---------------------------------------------------------------------------
// M2: paste-to-add (Ctrl+V and the free "Paste Image" menu item both land
// on `node.pasteFiles`, installed below -- see file header for the hook
// citations).
// ---------------------------------------------------------------------------

const UPLOAD_ROUTE = '/upload/image'
const ADD_ROUTE = '/eps_image_grid/add'

/**
 * A core-style `/view` URL for one buffer ref (`{filename, subfolder,
 * type}`) -- same query-param shape already observed for every OTHER
 * thumbnail this node shows (core's own `ui.images` handling), so a
 * just-added image renders identically to one that arrived via a normal
 * Run. `rand=` matches core's own cache-busting convention.
 */
function imageUrlForRef(ref) {
  const params = new URLSearchParams({
    filename: ref.filename,
    subfolder: ref.subfolder || '',
    type: ref.type || 'output',
    rand: String(Math.random())
  })
  return api.apiURL(`/view?${params.toString()}`)
}

/**
 * Replaces the node's displayed thumbnails with *refs* (the whole buffer,
 * same shape the backend's `ui.images` always sends) and shows the GRID
 * (`imageIndex = null`, core's own "no single image focused" sentinel --
 * FORMAT.md §6.6 "Identity is STABLE; refresh is CHEAP", 2026-07-21).
 * Previously this focused the LAST image (`imgs.length - 1`), which was the
 * owner-reported "paste shows one image, no way back to the grid until you
 * switch tabs" regression -- a paste-add should surface the new thumbnail
 * IN the grid, not replace the grid view. Shared with the Clear path's
 * empty case, and with `refreshFromBuffer` below (the 2026-07-20 display-
 * on-load/undo fix) -- every caller hands it the SAME ref shape, so a
 * load-triggered refresh renders identically to a just-added image.
 */
function setNodeImagesFromRefs(node, refs) {
  if (!refs || !refs.length) {
    node.imgs = []
    node.images = undefined
    node.imageIndex = null
    return
  }
  const imgs = refs.map((ref) => {
    const img = new Image()
    img.src = imageUrlForRef(ref)
    return img
  })
  node.imgs = imgs
  node.images = refs
  node.imageIndex = null
}

/**
 * `POST /upload/image` (core's own route) -- returns `{name, subfolder,
 * type}` on success, throws otherwise. Reimplemented directly (see file
 * header) rather than importing `useNodeImageUpload`'s `uploadFile`.
 */
async function uploadImageFile(file) {
  const formData = new FormData()
  formData.append('image', file)
  const response = await api.fetchApi(UPLOAD_ROUTE, { method: 'POST', body: formData })
  if (!response.ok) {
    throw new Error(`upload failed (HTTP ${response.status})`)
  }
  return response.json()
}

/**
 * `POST /eps_image_grid/add` -- appends the just-uploaded file (its
 * `{name,subfolder,type}` from `uploadImageFile`) to *node*'s buffer.
 * Returns `{ok, uuid, images}` on success, throws otherwise.
 */
async function addUploadToBuffer(node, uploaded) {
  const uuid = currentUuid(node)
  if (!GRID_UUID_RE.test(uuid)) {
    throw new Error('node has no valid grid_uuid yet')
  }
  const response = await api.fetchApi(ADD_ROUTE, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      uuid,
      filename: uploaded.name,
      subfolder: uploaded.subfolder || '',
      type: uploaded.type || 'input'
    })
  })
  let data = null
  try {
    data = await response.json()
  } catch {
    // Non-JSON body -- fall through to the status check below.
  }
  if (!response.ok) {
    const message = data && data.error ? data.error : `HTTP ${response.status}`
    throw new Error(message)
  }
  return data
}

/**
 * Uploads and appends every image `File` in *files* to *node*'s buffer,
 * one at a time (so a batch paste/drop with several images adds all of
 * them, in order -- sequential on purpose: our own atomic manifest writes
 * are not safe against truly concurrent appends from the same process).
 * Refreshes the displayed grid after each successful add. Fails soft per
 * file -- one bad file logs a warning and does not stop the rest; a
 * *files* list with nothing image-shaped is a silent no-op (`false`).
 */
async function addFilesToBuffer(node, files) {
  const imageFiles = Array.from(files || []).filter(
    (file) => file && typeof file.type === 'string' && file.type.startsWith('image/')
  )
  if (!imageFiles.length) return false

  for (const file of imageFiles) {
    try {
      const uploaded = await uploadImageFile(file)
      const result = await addUploadToBuffer(node, uploaded)
      if (result && Array.isArray(result.images)) {
        setNodeImagesFromRefs(node, result.images)
      }
    } catch (error) {
      console.warn(PREFIX, 'paste-to-add failed for one file', error)
    }
  }
  app.graph?.setDirtyCanvas(true, true)
  return true
}

/**
 * Installs `node.pasteFiles` (FORMAT.md §6.6). This is the exact hook
 * core's own paste pipeline calls -- both a real Ctrl+V while this node is
 * selected (via `usePaste.ts`, gated on `isImageNode(node)`; see
 * `previewMediaType` in `attach()` for why that's true even on an empty
 * buffer) and the free "Paste Image" menu item (`canPasteImage`, gated on
 * `typeof node.pasteFiles === 'function'`) land here. Never throws --
 * `addFilesToBuffer` already fails soft per file.
 */
function installPasteFiles(node) {
  node.pasteFiles = (files) => {
    void addFilesToBuffer(node, files)
    return true
  }
}

// ---------------------------------------------------------------------------
// Bug fixes 2026-07-20: buffer refresh (display-on-load/undo) + clone-on-
// collision (copy independence) -- see file header for the full writeup.
// ---------------------------------------------------------------------------

const LIST_ROUTE = '/eps_image_grid/list'
const CLONE_ROUTE = '/eps_image_grid/clone'

/**
 * Fetches *node*'s whole on-disk buffer (`GET /eps_image_grid/list`) and
 * repopulates its displayed thumbnails to match -- FORMAT.md §6.6 "Display
 * reflects the buffer on LOAD, not only after a Run". Reuses
 * `setNodeImagesFromRefs` (the exact same "replace the node's thumbnails"
 * primitive the M2 paste-add path already uses), so a load-triggered
 * refresh renders identically to a just-added image.
 *
 * Called (via `scheduleRefresh` below, never directly) from every place a
 * node's uuid can become "the one to show" without a Run: the deferred
 * `nodeCreated` tail (`attach()`), `loadedGraphNode()`, and the
 * `onConfigure` wrap below (undo/redo, and a standalone paste/clone
 * restore). Fails soft in every direction on purpose -- a node whose uuid
 * isn't valid yet, a missing route (an older backend that hasn't picked up
 * this fix), a non-OK response, or a network error all just leave the
 * node's CURRENT display untouched rather than throwing; never rejects, so
 * callers may `await` it or fire-and-forget it interchangeably.
 */
async function refreshFromBuffer(node) {
  const uuid = currentUuid(node)
  if (!GRID_UUID_RE.test(uuid)) return
  try {
    const response = await api.fetchApi(`${LIST_ROUTE}?uuid=${encodeURIComponent(uuid)}`)
    if (!response.ok) {
      console.warn(PREFIX, `refreshFromBuffer: HTTP ${response.status} for uuid ${uuid}`)
      return
    }
    const data = await response.json()
    if (!data || data.ok !== true || !Array.isArray(data.refs)) return
    setNodeImagesFromRefs(node, data.refs)
    node.setDirtyCanvas(true, true)
  } catch (error) {
    console.warn(PREFIX, 'refreshFromBuffer failed', error)
  }
}

//: node -> {promise: Promise|null, settledAt: number} -- backs
//: `scheduleRefresh` immediately below (FORMAT.md §6.6 "refresh is CHEAP",
//: 2026-07-21).
const refreshState = new WeakMap()
//: How long a just-settled refresh is trusted as "still fresh" before a new
//: call is allowed to hit the network again -- long enough to swallow the
//: 2-3 refresh triggers one load/undo/paste settle fires in quick
//: succession, short enough that it's imperceptible as a delay.
const REFRESH_SETTLE_MS = 250

/**
 * The single funnel every call site now uses instead of calling
 * `refreshFromBuffer` directly -- coalesces the several refresh triggers
 * that can legitimately fire for the SAME node within one load/undo/paste
 * "settle" (the deferred `attach()` tail, `loadedGraphNode`, `onConfigure`)
 * down to AT MOST ONE real `/list` fetch, confirmed live to otherwise fire
 * up to 3x per node per load. A call while a fetch is already in flight for
 * *node* rides that SAME promise rather than starting a second one; a call
 * arriving shortly after one already settled is skipped outright (nothing
 * changed in the meantime, so there is nothing new to fetch). Never
 * rejects, same contract as `refreshFromBuffer` itself.
 */
function scheduleRefresh(node) {
  const state = refreshState.get(node)
  if (state) {
    if (state.promise) return state.promise
    if (Date.now() - state.settledAt < REFRESH_SETTLE_MS) return Promise.resolve()
  }
  const promise = refreshFromBuffer(node).finally(() => {
    refreshState.set(node, { promise: null, settledAt: Date.now() })
  })
  refreshState.set(node, { promise, settledAt: 0 })
  return promise
}

/**
 * `POST /eps_image_grid/clone {from, to}` -- copies *fromUuid*'s buffer
 * into *toUuid*'s (FORMAT.md §6.6 "Copy carries the images,
 * independently"). Called only from `ensureUniqueUuid`'s collision branch.
 * Mirrors `postClear`'s request/response shape exactly. Throws on a non-OK
 * response; the caller decides how to degrade (see `ensureUniqueUuid`).
 */
async function postClone(fromUuid, toUuid) {
  const response = await api.fetchApi(CLONE_ROUTE, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ from: fromUuid, to: toUuid })
  })
  let data = null
  try {
    data = await response.json()
  } catch {
    // Non-JSON body (proxy error page etc.) -- fall through to status check.
  }
  if (!response.ok) {
    const message = data && data.error ? data.error : `HTTP ${response.status}`
    throw new Error(message)
  }
  return data
}

/**
 * Wraps `onConfigure` so *node*'s display re-syncs to the on-disk buffer
 * every time litegraph re-applies its serialized state WITHOUT going
 * through `attach()` first -- concretely, undo/redo (FORMAT.md §6.6): it
 * re-applies a node's PRIOR widgets/properties via `LGraphNode.configure()`
 * directly, on the SAME existing node instance, so `nodeCreated`/`attach()`
 * never runs again and nothing else here would ever notice. `node.imgs`
 * isn't part of that serialized state either way, so without this, undo
 * left the canvas showing whatever `node.imgs` happened to be at that
 * instant (often nothing) even though the on-disk buffer was untouched.
 *
 * 2026-07-21: skips scheduling a refresh here entirely while
 * `isGraphConfiguring()` reads true -- confirmed live that this fires WHILE
 * a whole-graph load/undo/redo/tab-switch's `rootGraph.configure()` is still
 * running, i.e. BEFORE `loadedGraphNode()` runs for this same node and
 * schedules the very same refresh moments later anyway. That redundancy was
 * a third of the "3 fetches per node per load" cost (FORMAT.md §6.6
 * "refresh is CHEAP"). Still schedules when `onConfigure` fires OUTSIDE a
 * configure pass -- a standalone paste/clone's per-node restore -- since
 * nothing else covers that case.
 *
 * Guarded by `node.__epsGridConfigureWrapped` (a plain instance flag, NOT
 * the module-level `attachedNodes` WeakSet `attach()` itself uses) so this
 * specific wrap can only ever be installed once per node no matter how many
 * times something re-triggers node setup for the same live instance. Calls
 * the original `onConfigure` FIRST, unconditionally, then (maybe) refreshes
 * -- `scheduleRefresh`/`refreshFromBuffer` never reject (see their own
 * docstrings), so firing without an `await` here is safe; `onConfigure`
 * itself is a synchronous litegraph hook with no promise contract to honor.
 */
function installConfigureRefresh(node) {
  if (node.__epsGridConfigureWrapped) return
  node.__epsGridConfigureWrapped = true

  const originalOnConfigure = node.onConfigure
  node.onConfigure = function (info) {
    let result
    try {
      result = originalOnConfigure?.call(this, info)
    } finally {
      if (!isGraphConfiguring()) {
        void scheduleRefresh(this)
      }
    }
    return result
  }
}

// ---------------------------------------------------------------------------
// Public entry points (called from web/eps_image.js)
// ---------------------------------------------------------------------------

/** EPSImageGrid is a real backend node (no frontend-only type registration
 * needed) -- everything here is per-instance, done in attach(). Kept as an
 * export because eps_image.js calls it unconditionally. */
export function init() {}

/** Per-node-instance attach; no-op unless *node* is an EPSImageGrid. */
export function attach(node) {
  try {
    if (!node) return
    if (nodeClassOf(node) !== CLASS_ID) return
    if (attachedNodes.has(node)) return
    attachedNodes.add(node)

    // Captured HERE, synchronously, at the exact moment litegraph creates
    // this node instance -- NOT re-read inside the deferred callback below.
    // `isGraphConfiguring()` correctly reads `true` during a whole-graph
    // load/undo/redo/tab-switch and `false` for a genuine interactive
    // paste/clone (confirmed live, see file header's 2026-07-21 section),
    // but `LGraph.configure()` -- the thing it wraps -- is fully
    // synchronous and reliably finishes (flag back to `false`) long before
    // any `setTimeout(fn, 0)` macrotask gets a turn, so re-reading it below
    // would always see `false` and defeat the whole guard.
    const allowCollisionMint = !isGraphConfiguring()

    hideGridUuidWidget(node)
    addClearButton(node)
    installPasteFiles(node)
    installConfigureRefresh(node) // undo/redo display fix -- see its own docstring
    // Makes `isImageNode(node)` (litegraphUtil.ts) true even before
    // anything has ever been collected, so a real Ctrl+V onto a
    // brand-new, still-empty node routes to `pasteFiles` above instead of
    // core creating a fresh LoadImage node (see file header's "Paste IN"
    // section for the exact check this satisfies).
    node.previewMediaType = 'image'

    // Deferred one tick -- the paste-collision path. See file header
    // point 1 for exactly why this can't run synchronously here. Awaiting
    // ensureUniqueUuid before scheduling a refresh (rather than firing both
    // in parallel) matters specifically for the collision branch: it
    // fetches whichever uuid actually settles -- the fresh, possibly-just-
    // cloned one -- not whatever was on the node before dedup ran.
    setTimeout(async () => {
      try {
        await ensureUniqueUuid(node, { allowCollisionMint })
      } catch (error) {
        console.warn(PREFIX, 'deferred uuid dedup failed', error)
      } finally {
        await scheduleRefresh(node)
      }
    }, 0)
  } catch (error) {
    console.warn(PREFIX, 'attach failed', error)
  }
}

/** Fires once per node, after a whole saved workflow has finished loading
 * (see file header point 2). No-op unless *node* is an EPSImageGrid.
 * Always passes `allowCollisionMint: false` -- this hook only ever fires
 * from within a whole-graph load pass (confirmed live: never for a
 * same-session paste), so collision-minting is never appropriate here; see
 * `ensureUniqueUuid`'s docstring. */
export function loadedGraphNode(node) {
  try {
    if (!node) return
    if (nodeClassOf(node) !== CLASS_ID) return
    ensureUniqueUuid(node, { allowCollisionMint: false })
      .catch((error) => console.warn(PREFIX, 'loadedGraphNode dedup failed', error))
      .finally(() => scheduleRefresh(node))
  } catch (error) {
    console.warn(PREFIX, 'loadedGraphNode dedup failed', error)
  }
}
