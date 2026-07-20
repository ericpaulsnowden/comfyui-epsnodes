/**
 * @file EPS Image Grid frontend (FORMAT.md §6.6). Exports the `init()`/
 * `attach(node)`/`loadedGraphNode(node)` hooks `web/eps_image.js` calls;
 * each no-ops for every node type other than `EPSImageGrid`.
 *
 * M1 only: identity/dedup for the hidden `grid_uuid` widget, and a `Clear`
 * button. The thumbnail GRID ITSELF IS FREE from ComfyUI core — the backend
 * (`nodes_image_grid.py`) returns `{"ui": {"images": [...whole buffer...]}}`
 * on every Run, which core turns into `node.imgs` (its normal, unconditional
 * per-run image-preview handling — no different for an `OUTPUT_NODE` than
 * for `SaveImage`/`PreviewImage`). Because we always send the COMPLETE
 * current buffer rather than just what THIS run added, that ordinary
 * replace-per-run behavior is what makes the grid look like it's growing
 * across separate Runs — no custom widget, no code here at all for the grid
 * proper. Copy/paste-to-clipspace (M2, roadmap-eps-image-grid.md) is
 * deliberately NOT built here; the module shape below (separate,
 * independently-callable helpers for identity vs. the Clear affordance) is
 * meant to leave room for it without a rewrite.
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
 * widget-value changes sees it too.
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
 * Mint a fresh uuid on first create (empty/invalid) or on collision with a
 * live sibling; otherwise leave a valid, non-colliding uuid untouched
 * (re-syncing property<->widget only if they've somehow drifted apart).
 * Idempotent and cheap to call repeatedly -- both call sites below
 * (deferred `nodeCreated`, and `loadedGraphNode`) may run for the same node.
 */
function ensureUniqueUuid(node) {
  if (nodeClassOf(node) !== CLASS_ID) return

  const widget = getGridUuidWidget(node)
  const propertyUuid = node.properties?.[UUID_PROPERTY_NAME] || ''
  const widgetUuid = widget?.value || ''
  const current = propertyUuid || widgetUuid
  const valid = GRID_UUID_RE.test(current)
  const colliding = valid && siblingUuids(node).has(current)

  if (!valid || colliding) {
    const fresh = generateUuid()
    writeUuid(node, fresh)
    if (colliding) {
      console.log(
        PREFIX,
        `node #${node.id}: grid_uuid collided with a live sibling -- minted a new one ` +
          '(each EPSImageGrid keeps its own buffer)'
      )
    }
    return
  }
  if (propertyUuid !== widgetUuid) {
    writeUuid(node, current) // re-sync, e.g. a hand-edited workflow set only one of the two
  }
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
 * the canvas) -- Clear should feel instant. */
function clearNodePreview(node) {
  node.imgs = []
  node.images = undefined
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

    hideGridUuidWidget(node)
    addClearButton(node)

    // Deferred one tick -- the paste-collision path. See file header
    // point 1 for exactly why this can't run synchronously here.
    setTimeout(() => {
      try {
        ensureUniqueUuid(node)
      } catch (error) {
        console.warn(PREFIX, 'deferred uuid dedup failed', error)
      }
    }, 0)
  } catch (error) {
    console.warn(PREFIX, 'attach failed', error)
  }
}

/** Fires once per node, after a whole saved workflow has finished loading
 * (see file header point 2). No-op unless *node* is an EPSImageGrid. */
export function loadedGraphNode(node) {
  try {
    if (!node) return
    if (nodeClassOf(node) !== CLASS_ID) return
    ensureUniqueUuid(node)
  } catch (error) {
    console.warn(PREFIX, 'loadedGraphNode dedup failed', error)
  }
}
