/**
 * @file EPS Switcher frontend (FORMAT.md §6.4). Exports the `init()`/
 * `attach(node)` hooks `web/eps_image.js` calls; `attach` no-ops for every
 * node type other than `EPSSwitcher`.
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
 * **`toggles` is the enabled-set bridge to the backend** (module docstring,
 * `eps_image/nodes_switcher.py`): a hidden (`.hidden = true`, same trick
 * FORMAT.md §7.2 uses for the Prompt Notebook's `file` widget) STRING
 * widget holding a JSON object of `{"image_N": false}` overrides -- absent
 * key means enabled. Every toggle/connection-change call here keeps it in
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

const CLASS_ID = 'EPSSwitcher'
const PREFIX = '[eps_image:switcher]'
const IMAGE_INPUT_RE = /^image_(\d+)$/
const TOGGLES_WIDGET_NAME = 'toggles'
const HEADER_WIDGET_NAME = '__eps_switcher_toggle_all'

const ROW_BOX = 12
//: Matches canvas/measureSlots.ts's getNodeInputOnPos reserved width
//: (`20 + nameLength*7`, starting ~10px left of the dot) plus a small pad --
//: see the file header's "Per-row on/off toggle" note for why this is
//: LEFT-anchored rather than measured from the node's right edge.
const ROW_LABEL_PAD = 12
const ROW_MIN_X = 92
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
 * backend ignores an unconnected image_N entirely), and -- the bug this
 * guards against -- keeping a stale `false` on a disconnected MIDDLE (gap)
 * slot would silently disable a DIFFERENT image later re-wired into that same
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
      'EPSSwitcher node is missing its `toggles` widget; per-row state will not persist'
    )
    return
  }
  widget.hidden = true
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
  const inputs = node.inputs || []
  for (let idx = 0; idx < inputs.length; idx++) {
    const input = inputs[idx]
    const match = input && IMAGE_INPUT_RE.exec(input.name)
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
  const type = template?.type ?? 'IMAGE'
  const extraInfo = template && template.shape !== undefined ? { shape: template.shape } : undefined
  return node.addInput(`image_${n}`, type, extraInfo)
}

/**
 * Grows/shrinks *node*'s `image_N` sockets to the one invariant: every
 * CONNECTED `image_N` keeps its name/link untouched, and exactly one
 * trailing EMPTY slot exists, numbered one past the highest connected
 * `image_N` (`image_1` itself, empty, when nothing is connected). Idempotent
 * -- every call site below calls it unconditionally.
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
 * Chains *node*'s `configure` and `onConnectionsChange` so its `image_N`
 * sockets converge per convergeImageInputs() above. See the file header for
 * why two hooks (not one) and the `state.restoring` guard.
 * @param {object} node
 */
function wireImageInputGrowth(node) {
  const state = { restoring: false }

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
    if (!state.restoring && IMAGE_INPUT_RE.test(inputOrOutput?.name || '')) {
      try {
        convergeImageInputs(this)
      } catch (error) {
        console.warn(PREFIX, 'convergeImageInputs (onConnectionsChange) failed', error)
      }
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
    // getNodeInputOnPos's reserved width for THIS row's own name, so a
    // longer name (image_12, image_137, ...) still clears its own hit-zone.
    const inputHitWidth = 20 + entry.name.length * 7
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
          ? 'Toggle All (no images connected)'
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

/** Per-node-instance attach; no-op unless *node* is an EPSSwitcher. */
export function attach(node) {
  try {
    if (!node) return
    if (nodeClassOf(node) !== CLASS_ID) return
    if (attachedNodes.has(node)) return
    attachedNodes.add(node)

    hideTogglesWidget(node)
    ensureMinNodeWidth(node)
    wireImageInputGrowth(node)
    wireRowToggleDrawing(node)
    wireRowToggleClicks(node)
    addHeaderWidget(node)
  } catch (error) {
    console.warn(PREFIX, 'attach failed', error)
  }
}
