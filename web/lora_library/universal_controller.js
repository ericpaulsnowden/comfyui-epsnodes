/**
 * @file EPS Universal State Controller — frontend-only virtual node
 * (FORMAT.md §6.3-style registration) that captures/applies a named
 * "state" spanning EVERY state-bearing node class in the workflow, not
 * just one loader family. Architectural blueprint: `web/lora_library/
 * controller.js` (the Lora Loader State Controller) — this file clones its
 * DOM-widget shape, CSS conventions (`llsc-*` -> `lusc-*`), the v0.81/0.82
 * optimistic-save/delete + provisional-slug-guard pattern, the §4.2-style
 * group system (create via `# name`, armed two-click ✕, collapse persisted
 * via a `Collapsed groups` property), and the shared-module-scope poller —
 * MINUS everything Power-Lora-Loader-specific (no target combo, no Push,
 * no per-row strength columns, no rgthree binding at all).
 *
 * Deliberate scope trims from the controller.js blueprint (read this before
 * extending the file):
 *
 *  1. NO drag-to-reorder / drag-into-group. controller.js's drag machinery
 *     (`_onStateRowPointerDown`/`_computeStateDropTarget`/
 *     `_computeCategoryDropTarget`/`_finishStateDrag`/...) is several
 *     hundred lines of capture-phase pointer-gesture code tied tightly to
 *     litegraph's own layout math. Porting it was judged too large for
 *     this round. Instead, a state is assigned to a group through a small
 *     "Group:" `<select>` next to the search box (`_renderMoveGroupControl`/
 *     `_onMoveToGroupChange`) — same `layout.order` mutation + `_saveLayout()`
 *     POST underneath, just a cheaper interaction than a drag gesture. Groups
 *     therefore stay fully functional (create/rename/delete/collapse/assign)
 *     without the drag port.
 *  2. (2026-08-29 bugfix round, REVERSED — see below) New State / Save
 *     State are now a controller.js-identical PAIR, not one capture-only
 *     button. Owner report: "anytime I change a value of a set and try to
 *     save over it, it creates a new set" — the original three-button
 *     shape (Save/Apply/Delete, "Save" = capture) had no update-in-place
 *     path at all, so saving over an existing name minted `<slug>-2` every
 *     time. Mirrors controller.js's `_onCaptureClick()`/`_doCapture()`
 *     (`New State`, unchanged create path) vs `_onUpdateClick()`/
 *     `_doUpdate()` (`Save State`, overwrites the SELECTED state's file in
 *     place — same slug, fresh widgets + `captured` timestamp) exactly,
 *     including the rename-in-place rule (`_saveAsNewName()`: an edited
 *     name field renames the selected state rather than forking a
 *     duplicate — controller.js's 2026-07-22 "Save renames in place"
 *     reversal, not the create-a-new-entry behavior an earlier
 *     controller.js history entry describes) and the disable rule (New
 *     State always enabled — it is also the `#`-group-creation entry
 *     point and must work with nothing selected; Save/Apply/Delete
 *     disabled with no state selected). `POST /lora_library/universal_state`
 *     already accepted an optional `slug` for this (verified against
 *     `lora_library/routes_universal_states.py` and
 *     `universal_states_store.save_state()`: a caller-supplied slug is
 *     used AS-IS, never re-derived/de-duplicated) — only the frontend UI
 *     was missing the second button.
 *  3. No NAS "states location" footer / Browse…/Open-folder UI. That whole
 *     subsystem in controller.js is about surfacing a SHARED library folder
 *     across machines and isn't part of this node's spec.
 *  4. No legacy pre-`beforeRegisterVueAppNodeDefs` Pinia-store display-name
 *     patch. controller.js keeps one as a fallback for very old frontends;
 *     since this is a brand-new node with no legacy workflows depending on
 *     it, only the modern `nameNodeDef()` hook is implemented.
 *  5. Selection is a single click (no controller.js-style "second click on
 *     the already-selected row re-applies" split) — Apply State is its own
 *     button here, so the select-vs-apply ambiguity controller.js solved
 *     for a combo-turned-list simply doesn't exist for this node.
 *
 * Backend contract this file codes to (owned by a concurrent backend round
 * — routes/shapes below are ASSUMED STABLE per the task brief, and every
 * network call is stubbed in tests/test_universal_controller_js.py rather
 * than hit for real):
 *  - `GET /eps/state_registry` -> `{format, classes: {<class>: {display,
 *    format, widgets: {<name>: {kind, ...}}, excluded: {...}}}}`. Fetched
 *    ONCE per page via a module-scope shared promise (`fetchStateRegistry`,
 *    `web/eps_image/cross_sweep.js`'s `loadListFlags` shape).
 *  - `GET /lora_library/universal_states` -> `{ok, states:[{slug, name,
 *    count, captured}], layout, states_dir, is_default_library, mtime}` —
 *    states AND the §4.2-style group layout arrive in ONE feed (unlike
 *    controller.js's two separate routes), so `_applyStatesResponse()`
 *    updates both `_statesCache` and `_layoutCache` from one payload.
 *  - `GET /lora_library/universal_state?slug=` -> the full state:
 *    `{format, name, notes, captured, nodes:[{class, id, title, widgets}]}`.
 *  - `POST /lora_library/universal_state {state}` -> the listing feed shape
 *    above PLUS `slug` and an optional `foreign` warnings array.
 *  - `POST /lora_library/universal_state/delete {slug}` -> the listing feed.
 *  - `POST /lora_library/universal_states/layout {layout}` -> `{layout}`
 *    (server-healed, same shape `normalizeLayoutClient` produces).
 *
 * Pure, exported, unit-tested helpers (no DOM/network touched):
 * `validateStateValue`, `buildStatePayload`, `applyPlan`,
 * `exclusionsAfterToggle`, `classToggleState`, `summarizeCapture`,
 * `summarizeUpdate`, `summarizeApply`, `compareStateEntries`,
 * `parseCollapsedGroups`, `isGroupNameInput`, `groupNameFromInput`,
 * `normalizeExclusions`, `normalizeRegistry`.
 *
 * 2026-08-29 bugfix round, second half (owner: "applying any of the sets
 * won't change anything") — `_doApply()`'s write loop (`_writeApplyPlan()`)
 * correctly updates every matched node's widgets, but a DOM-panel node
 * (the Notebook, the LoRA Picker, the Prompt Builder, the Checkpoint
 * Switcher, Resolution's own presets `<select>`) holds its own rendered
 * state that a bare `widget.value = x; widget.callback?.()` never touches
 * — that panel's DOM only repaints from its own gestures/reload cycles.
 * `_writeApplyPlan()` now calls `api.announceWidgetsChangedExternally()`
 * ONCE, after its existing single `setDirtyCanvas`, naming only the nodes
 * it actually wrote; each affected panel module subscribes
 * (`api.subscribeWidgetsChangedExternally()`) and re-syncs from that
 * node's OWN existing reload entry point — see api.js's own doc comment on
 * both functions for the full mechanism (coalescing, the
 * `app.configuringGraph` graph-load guard, the per-subscriber try/catch).
 * Canvas-drawn nodes (Switchers, Distributor) need no such subscription —
 * they redraw every frame straight from the widget, so the existing
 * `setDirtyCanvas` alone already covers them (verified by reading both
 * files: neither owns a DOM widget at all).
 */

import { app } from '../../../scripts/app.js'
import * as api from './api.js'

// ---------------------------------------------------------------- constants

const NODE_TYPE = 'EPSUniversalStateController'
const NODE_TITLE = 'EPS Universal State Controller'
const NODE_CATEGORY = 'EPSNodes'

const STATE_REGISTRY_ROUTE = '/eps/state_registry'
const STATES_ROUTE = '/lora_library/universal_states'
const STATE_ROUTE = '/lora_library/universal_state'
const STATE_DELETE_ROUTE = '/lora_library/universal_state/delete'
const STATES_LAYOUT_ROUTE = '/lora_library/universal_states/layout'

/** Node properties (FORMAT.md §6.3-style, right-click Properties panel). */
const PROP_COLLAPSED_GROUPS = 'Collapsed groups'
/** Stores ONLY exclusions -- default is "everything included" so a newly
 * added node never needs an entry and the property stays tiny. Shape:
 * `{nodes: {<pathId>: false}, classes: {<class>: false}}` -- `false` is the
 * only value ever written; there is no `true` entry, ever. */
const PROP_INCLUDED_NODES = 'Included nodes'

const LABEL_CAPTURE = 'New State'
const LABEL_UPDATE = 'Save State'
const LABEL_APPLY = 'Apply State'
const LABEL_DELETE = 'Delete State'
const LABEL_DELETE_CONFIRM = 'Are you sure?'
const DELETE_CONFIRM_MS = 4000
const CATEGORY_DELETE_CONFIRM_MS = 4000
const DELETE_ARMED_BG_COLOR = '#8b2020'
const DELETE_ARMED_TEXT_COLOR = '#ffffff'

/** Twin of controller.js's SETS_POLL_MS: one shared module-level poller for
 * every live instance of this node, instead of a per-node draw-driven one. */
const USC_POLL_MS = 15000
const USC_CHANGED_EVENT = 'lora_library:universal-states-changed'

const UNCATEGORIZED = ''

const MSG_LAYOUT_NOT_LOADED = 'Group layout not loaded yet — try again.'

// ============================================================================
// Pure helpers -- no DOM, no network, no litegraph globals. These are the
// primary test surface (tests/test_universal_controller_js.py drives them
// directly under Node); everything else in this file is either a thin
// impure wrapper around one of them or closure-bound class-internal state
// pinned via source-text assertions, matching pll_bridge.js's own split.
// ============================================================================

/**
 * ONE generic validator for every registry widget "kind" (the closed set:
 * string(max_len), int(min,max), float(min,max), choice(options?), lines,
 * json_array(items), json_object(key_pattern)). Used identically at CAPTURE
 * time (registry-only `desc`, no `options`) and at APPLY time for `choice`
 * fields (the caller folds the LIVE widget's resolved options into `desc`
 * first -- see `applyPlan()`). Never throws; always returns `{ok: true}` or
 * `{ok: false, error}`.
 * @param {{kind: string, max_len?: number, min?: number, max?: number,
 *   options?: unknown[], items?: string, key_pattern?: string}} desc
 * @param {unknown} value
 */
export function validateStateValue(desc, value) {
  switch (desc?.kind) {
    case 'string': {
      if (typeof value !== 'string') return { ok: false, error: 'expected a string' }
      if (typeof desc.max_len === 'number' && value.length > desc.max_len) {
        return { ok: false, error: `exceeds max_len ${desc.max_len}` }
      }
      return { ok: true }
    }
    case 'lines': {
      if (typeof value !== 'string') return { ok: false, error: 'expected a string' }
      if (typeof desc.max_len === 'number' && value.length > desc.max_len) {
        return { ok: false, error: `exceeds max_len ${desc.max_len}` }
      }
      return { ok: true }
    }
    case 'int': {
      if (typeof value !== 'number' || !Number.isInteger(value)) {
        return { ok: false, error: 'expected an integer' }
      }
      if (typeof desc.min === 'number' && value < desc.min) {
        return { ok: false, error: `below min ${desc.min}` }
      }
      if (typeof desc.max === 'number' && value > desc.max) {
        return { ok: false, error: `above max ${desc.max}` }
      }
      return { ok: true }
    }
    case 'float': {
      if (typeof value !== 'number' || !Number.isFinite(value)) {
        return { ok: false, error: 'expected a finite number' }
      }
      if (typeof desc.min === 'number' && value < desc.min) {
        return { ok: false, error: `below min ${desc.min}` }
      }
      if (typeof desc.max === 'number' && value > desc.max) {
        return { ok: false, error: `above max ${desc.max}` }
      }
      return { ok: true }
    }
    case 'choice': {
      if (typeof value !== 'string' && typeof value !== 'number') {
        return { ok: false, error: 'expected a string or number' }
      }
      if (Array.isArray(desc.options) && !desc.options.includes(value)) {
        return { ok: false, error: 'value not in options' }
      }
      return { ok: true }
    }
    case 'json_array': {
      if (!Array.isArray(value)) return { ok: false, error: 'expected an array' }
      const itemKind = desc.items || 'string'
      if (itemKind === 'string' && !value.every((item) => typeof item === 'string')) {
        return { ok: false, error: 'every item must be a string' }
      }
      return { ok: true }
    }
    case 'json_object': {
      if (!value || typeof value !== 'object' || Array.isArray(value)) {
        return { ok: false, error: 'expected an object' }
      }
      if (desc.key_pattern) {
        let re
        try {
          re = new RegExp(desc.key_pattern)
        } catch {
          return { ok: false, error: 'invalid key_pattern' }
        }
        for (const key of Object.keys(value)) {
          if (!re.test(key)) return { ok: false, error: `key "${key}" does not match key_pattern` }
        }
      }
      return { ok: true }
    }
    default:
      return { ok: false, error: `unknown kind "${desc?.kind}"` }
  }
}

/**
 * CAPTURE, the pure half. `nodesInfo` is already-read plain data (the
 * impure `discoverStateNodes()`/`readWidgetValues()` pair does the live
 * `.widgets`/`.value` reading elsewhere) -- one entry per live node:
 * `{pathId, class, title, widgetValues: {<name>: value}}`. Drops nodes
 * whose class isn't in the registry (or has no declared widgets: not
 * state-bearing) and nodes excluded by `exclusions` (property-only,
 * default-included -- see `normalizeExclusions`). For every DECLARED
 * widget name in the registry: a missing key in `widgetValues` or a
 * `validateStateValue` failure skips just THAT widget (never the whole
 * node) and appends a warning string; the caller is expected to
 * `console.warn` each one (kept out of this function so it stays pure and
 * testable without stubbing console).
 * @returns {{nodes: Array<{class, id, title, widgets}>, warnings: string[]}}
 */
/**
 * The STRING<->STRUCTURE seam for JSON-kind widgets (v0.83.0 rig catch):
 * a `json_object`/`json_array` widget carries its state as a JSON STRING
 * on the live widget (the hidden-bridge law), but a §4.3 state stores the
 * PARSED structure -- and the backend's normalize_state validates the
 * structural form. Feeding the raw string to the dict-shaped validator
 * silently dropped every toggles/selection/blocks/presets widget from
 * captures. Parse here, tolerantly: `""` degrades to the kind's empty
 * value, a malformed blob is a skip-with-warning, never a saved
 * corruption. Non-JSON kinds pass through untouched.
 */
export function captureWidgetValue(desc, rawValue) {
  const kind = desc?.kind
  if (kind !== 'json_object' && kind !== 'json_array') return { ok: true, value: rawValue }
  if (typeof rawValue !== 'string') return { ok: true, value: rawValue } // already structural
  const text = rawValue.trim() === '' ? (kind === 'json_array' ? '[]' : '{}') : rawValue
  try {
    return { ok: true, value: JSON.parse(text) }
  } catch (error) {
    return { ok: false, error: `unparseable JSON (${error.message})` }
  }
}

/** The mirror of `captureWidgetValue` at APPLY time: JSON kinds serialize
 * back to the string the live widget carries; everything else writes as
 * stored. */
export function widgetWriteValue(desc, storedValue) {
  const kind = desc?.kind
  if (kind !== 'json_object' && kind !== 'json_array') return storedValue
  return typeof storedValue === 'string' ? storedValue : JSON.stringify(storedValue)
}

export function buildStatePayload(nodesInfo, registry, exclusions) {
  const nodes = []
  const warnings = []
  for (const info of nodesInfo || []) {
    const regClass = registry?.classes?.[info.class]
    if (!regClass || !regClass.widgets || !Object.keys(regClass.widgets).length) continue
    if (exclusions?.classes?.[info.class] === false) continue
    if (exclusions?.nodes?.[info.pathId] === false) continue
    const widgets = {}
    for (const [name, desc] of Object.entries(regClass.widgets)) {
      const values = info.widgetValues || {}
      if (!(name in values)) {
        warnings.push(`missing widget "${name}" on ${info.class} #${info.pathId}`)
        continue
      }
      const parsed = captureWidgetValue(desc, values[name])
      if (!parsed.ok) {
        warnings.push(`skipping "${name}" on ${info.class} #${info.pathId} (${parsed.error})`)
        continue
      }
      const result = validateStateValue(desc, parsed.value)
      if (!result.ok) {
        warnings.push(`skipping "${name}" on ${info.class} #${info.pathId} (${result.error})`)
        continue
      }
      widgets[name] = parsed.value
    }
    nodes.push({ class: info.class, id: info.pathId, title: info.title, widgets })
  }
  return { nodes, warnings }
}

/**
 * The pure planning half of Apply -- computes what WOULD happen, writes
 * nothing. `stateNodes` is a loaded state's `.nodes` array; `liveIndex` is
 * `{<pathId>: {class, widgets: {<name>: {value, options}}}}` (built by the
 * impure `buildLiveIndex()` from a live discovery pass). Matching is EXACT
 * pathId + class (a pathId whose live class changed counts as missing, not
 * a silent mismatch). For `choice` widgets, the live widget's OWN resolved
 * options (already folded into `liveWidget.options` by the caller) gate the
 * value -- a value outside them is a per-widget failure, not a node-level
 * one: the node still counts as matched, with that one write dropped and
 * recorded in `invalid`. NEVER includes an invalid value in `writes`.
 * @returns {{matched: Array<{id,class,title,writes,invalid}>,
 *   missing: Array<{id,class,title,reason}>,
 *   skipped: Array<{id,class,title,reason}>}}
 */
/**
 * Pair each state entry with a LIVE node on this canvas (v0.86.0 — the
 * roadmap's M3 matching, brought forward by the owner's 2026-08-28 report:
 * "applying on two computers ... even when using the same nodes with the
 * same names ... Applied 1 of 4 · 3 not found"). M1 matched on the stored
 * pathId alone, which only ever agrees when both machines opened the SAME
 * workflow file — rebuild the graph anywhere and every id shifts.
 *
 * Three passes, each claiming a live node at most once so two state
 * entries can never collide on one node:
 *  1. `id`    — the stored pathId, class confirmed. Exact, always right.
 *  2. `title` — same class AND same title, when exactly ONE unclaimed live
 *               node qualifies. Ambiguous titles fall through rather than
 *               guess (that is what pass 3 is for, and it says so).
 *  3. `class` — same class, paired in order (state order vs discovery
 *               order). The last resort: correct for the ordinary "one
 *               Model Switcher per graph" case, a POSITIONAL GUESS when
 *               there are several, which is why `summarizeApply` reports
 *               the count separately instead of burying it.
 *
 * Returns an array parallel to *stateNodes*: `{pathId, how}` or `null`.
 */
export function resolveMatches(stateNodes, liveIndex) {
  const entries = Array.isArray(stateNodes) ? stateNodes : []
  const results = new Array(entries.length).fill(null)
  const index = liveIndex || {}
  const claimed = new Set()
  const liveList = Object.keys(index)
    .map((id) => ({ id, ...index[id] }))
    .sort((a, b) => (a.order || 0) - (b.order || 0))

  entries.forEach((entry, i) => {
    const live = index[entry?.id]
    if (live && live.class === entry.class && !claimed.has(entry.id)) {
      claimed.add(entry.id)
      results[i] = { pathId: entry.id, how: 'id' }
    }
  })

  entries.forEach((entry, i) => {
    if (results[i] || !entry) return
    const title = (entry.title || '').trim()
    if (!title) return
    const candidates = liveList.filter(
      (live) =>
        !claimed.has(live.id) && live.class === entry.class && (live.title || '').trim() === title
    )
    if (candidates.length !== 1) return
    claimed.add(candidates[0].id)
    results[i] = { pathId: candidates[0].id, how: 'title' }
  })

  const poolByClass = new Map()
  for (const live of liveList) {
    if (claimed.has(live.id)) continue
    if (!poolByClass.has(live.class)) poolByClass.set(live.class, [])
    poolByClass.get(live.class).push(live)
  }
  entries.forEach((entry, i) => {
    if (results[i] || !entry) return
    const pool = poolByClass.get(entry.class)
    if (!pool || !pool.length) return
    const pick = pool.shift()
    claimed.add(pick.id)
    results[i] = { pathId: pick.id, how: 'class' }
  })

  return results
}

export function applyPlan(stateNodes, liveIndex, registry, exclusions) {
  const matched = []
  const missing = []
  const skipped = []
  const entries = Array.isArray(stateNodes) ? stateNodes : []
  const matches = resolveMatches(entries, liveIndex)
  entries.forEach((entry, entryIndex) => {
    const match = matches[entryIndex]
    const live = match ? liveIndex?.[match.pathId] : null
    if (!live) {
      missing.push({
        id: entry.id,
        class: entry.class,
        title: entry.title,
        reason: 'not-found'
      })
      return
    }
    // Exclusions are about THIS canvas's nodes, so they key off the LIVE
    // pathId the match resolved to -- never the id the state was saved
    // with on another machine (v0.86.0).
    if (
      exclusions?.classes?.[entry.class] === false ||
      exclusions?.nodes?.[match.pathId] === false
    ) {
      skipped.push({ id: match.pathId, class: entry.class, title: entry.title, reason: 'excluded' })
      return
    }
    const regClass = registry?.classes?.[entry.class]
    if (!regClass || !regClass.widgets) {
      missing.push({ id: entry.id, class: entry.class, title: entry.title, reason: 'unregistered' })
      return
    }
    const writes = []
    const invalid = []
    for (const [name, value] of Object.entries(entry.widgets || {})) {
      const desc = regClass.widgets[name]
      const liveWidget = live.widgets?.[name]
      if (!desc) {
        invalid.push({ name, reason: 'unknown-widget' })
        continue
      }
      if (!liveWidget) {
        invalid.push({ name, reason: 'missing-live-widget' })
        continue
      }
      const checkDesc =
        desc.kind === 'choice' ? { ...desc, options: resolveWidgetOptions(liveWidget.options) } : desc
      const result = validateStateValue(checkDesc, value)
      if (!result.ok) {
        invalid.push({ name, reason: result.error })
        continue
      }
      writes.push({ name, value: widgetWriteValue(desc, value) })
    }
    matched.push({
      id: match.pathId,
      savedId: entry.id,
      how: match.how,
      class: entry.class,
      title: entry.title,
      writes,
      invalid
    })
  })
  return { matched, missing, skipped }
}

/** `widget.options.values` resolution for a `choice` widget -- an array as
 * given, a function called (litegraph's deprecated-but-live convention,
 * controller.js's own `target` combo citation), anything else -> []. */
function resolveWidgetOptions(options) {
  const values = options?.values
  if (Array.isArray(values)) return values
  if (typeof values === 'function') {
    try {
      return values() || []
    } catch {
      return []
    }
  }
  return []
}

/**
 * Tri-state a class's master checkbox reads (the switchers' Toggle All
 * shape): `true` = every instance included, `false` = the whole class is
 * excluded (or every instance is individually excluded with no live
 * instances at all), `null` = mixed.
 * @param {{nodes?: Record<string,false>, classes?: Record<string,false>}} exclusions
 * @param {string} klass
 * @param {string[]} pathIds every live pathId currently in this class
 */
export function classToggleState(exclusions, klass, pathIds) {
  if (exclusions?.classes?.[klass] === false) return false
  if (!pathIds || !pathIds.length) return true
  let allOn = true
  let allOff = true
  for (const id of pathIds) {
    const on = exclusions?.nodes?.[id] !== false
    allOn = allOn && on
    allOff = allOff && !on
  }
  if (allOn) return true
  if (allOff) return false
  return null
}

/**
 * Pure reducer: the NEXT exclusions object after one toggle. Never mutates
 * `exclusions`. Two action shapes:
 *  - `{type: 'node', pathId, included}` -- flips one node's own entry.
 *  - `{type: 'class', klass, pathIds}` -- the tri-state master checkbox,
 *    rgthree's `toggleAllLoras` convention ("anything but all on -> turn
 *    all on; all on -> turn all off") with ONE deliberate exception: when
 *    the class is currently excluded via its OWN class-level flag (set by
 *    a PRIOR master click), clicking again does NOT force every node on --
 *    it just removes the class-level flag and lets whatever per-node
 *    entries already existed show through again ("rechecking restores
 *    per-node states"). That is the only path that can leave the result
 *    "mixed"; every other transition lands on a clean true/false.
 */
export function exclusionsAfterToggle(exclusions, action) {
  const next = {
    nodes: { ...(exclusions?.nodes || {}) },
    classes: { ...(exclusions?.classes || {}) }
  }
  if (action?.type === 'node') {
    if (action.included) delete next.nodes[action.pathId]
    else next.nodes[action.pathId] = false
    return next
  }
  if (action?.type === 'class') {
    const pathIds = action.pathIds || []
    const wasClassOff = exclusions?.classes?.[action.klass] === false
    const state = classToggleState(exclusions, action.klass, pathIds)
    if (state === false && wasClassOff) {
      delete next.classes[action.klass]
      return next
    }
    if (state === true) {
      next.classes[action.klass] = false
      return next
    }
    delete next.classes[action.klass]
    for (const id of pathIds) delete next.nodes[id]
    return next
  }
  return next
}

/** Defensive re-shaping of the `Included nodes` property: only ever `false`
 * entries survive (there is no `true` entry, ever -- default is included),
 * anything else (a hand-edited `true`, a stray non-boolean, a malformed
 * property from an older/foreign save) is dropped rather than trusted. */
export function normalizeExclusions(raw) {
  const nodes = {}
  const classes = {}
  if (raw && typeof raw === 'object') {
    if (raw.nodes && typeof raw.nodes === 'object') {
      for (const [key, value] of Object.entries(raw.nodes)) if (value === false) nodes[key] = false
    }
    if (raw.classes && typeof raw.classes === 'object') {
      for (const [key, value] of Object.entries(raw.classes)) if (value === false) classes[key] = false
    }
  }
  return { nodes, classes }
}

/** Tolerant re-shaping of `GET /eps/state_registry`'s payload -- a
 * malformed/foreign class entry is dropped rather than trusted, so a
 * misbehaving registry route degrades to "no state-bearing classes" instead
 * of throwing. */
export function normalizeRegistry(raw) {
  const classes = {}
  if (raw && typeof raw === 'object' && raw.classes && typeof raw.classes === 'object') {
    for (const [klass, desc] of Object.entries(raw.classes)) {
      if (!desc || typeof desc !== 'object') continue
      const widgets = {}
      if (desc.widgets && typeof desc.widgets === 'object') {
        for (const [name, wdesc] of Object.entries(desc.widgets)) {
          if (wdesc && typeof wdesc === 'object') widgets[name] = wdesc
        }
      }
      classes[klass] = { display: desc.display || klass, widgets, excluded: desc.excluded || {} }
    }
  }
  return { classes }
}

/** Lowercased class display name, naively pluralized ("switcher" ->
 * "switchers") -- good enough for a toast, not a general i18n plural. */
function pluralizeLabel(label, count) {
  if (count === 1) return label
  return /s$/i.test(label) ? label : `${label}s`
}

/**
 * The per-class node-count tail BOTH `summarizeCapture` (New State) and
 * `summarizeUpdate` (Save State) build their toast around -- "9 nodes: 2
 * switchers, 1 notebook, …". Classes are counted from the CAPTURED nodes
 * (post-exclusion, post-validation -- i.e. `buildStatePayload(...).nodes`),
 * sorted by count descending then display-name ascending, capped at
 * `maxClasses` with a trailing "…" when more were captured than shown.
 * Factored out so the two toasts can never drift on how they count/format
 * -- only the leading verb + quoted name differ between them.
 */
function describeCapturedCounts(nodesEntries, registry, maxClasses) {
  const counts = new Map()
  for (const entry of nodesEntries || []) {
    counts.set(entry.class, (counts.get(entry.class) || 0) + 1)
  }
  const parts = [...counts.entries()]
    .map(([klass, count]) => {
      const label = (registry?.classes?.[klass]?.display || klass).toLowerCase()
      return { count, label }
    })
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label))
  const total = (nodesEntries || []).length
  const shown = parts.slice(0, maxClasses)
  const pieces = shown.map((p) => `${p.count} ${pluralizeLabel(p.label, p.count)}`)
  const suffix = parts.length > shown.length ? ', …' : ''
  return `${total} node${total === 1 ? '' : 's'}: ${pieces.join(', ')}${suffix}`
}

/**
 * The New State success toast: `Saved "<name>" — 9 nodes: 2 switchers, 1
 * notebook, …`. See `describeCapturedCounts` for the shared tail.
 */
export function summarizeCapture(name, nodesEntries, registry, { maxClasses = 4 } = {}) {
  return `Saved "${name}" — ${describeCapturedCounts(nodesEntries, registry, maxClasses)}`
}

/**
 * The Save State (update-in-place) success toast -- controller.js's
 * `_doUpdate()` verb rule, ported verbatim: `renamed` (the name field
 * carried a rename, `_saveAsNewName()` returned non-null) reads "Saved +
 * renamed to "<name>" — …"; a plain overwrite reads "Updated "<name>" —
 * …". Same shared node-count tail as `summarizeCapture` -- the two toasts
 * must never disagree on how a capture is counted, only on what verb
 * introduces it.
 */
export function summarizeUpdate(name, nodesEntries, registry, { renamed = false, maxClasses = 4 } = {}) {
  const verb = renamed ? 'Saved + renamed to' : 'Updated'
  return `${verb} "${name}" — ${describeCapturedCounts(nodesEntries, registry, maxClasses)}`
}

/** Up to `max` "<display> '<title>' #<id>" descriptions, "+N more" beyond that. */
function describeNodeEntries(entries, registry, max = 2) {
  const displayOf = (klass) => registry?.classes?.[klass]?.display || klass
  const shown = entries.slice(0, max).map((e) => `${displayOf(e.class)} '${e.title || e.class}' #${e.id}`)
  const extra = entries.length - shown.length
  return extra > 0 ? `${shown.join(', ')}, +${extra} more` : shown.join(', ')
}

function describeSkipReasons(entries) {
  const reasons = new Set(entries.map((e) => e.reason))
  if (reasons.size === 1 && reasons.has('excluded')) return 'excluded here'
  return [...reasons].join(', ')
}

/**
 * The Apply State diff toast's data + severity. `Applied 7 of 9 · 1 not
 * found (EPS Resolution 'Hero size' #12) · 1 skipped (excluded here)`.
 * `severity` is `'warn'` when NOTHING applied (a state applying 0 nodes
 * must never read as a quiet success) and `'success'` otherwise.
 */
export function summarizeApply(plan, registry) {
  const matched = plan?.matched || []
  const missing = plan?.missing || []
  const skipped = plan?.skipped || []
  const total = matched.length + missing.length + skipped.length
  const pieces = [`Applied ${matched.length} of ${total}`]
  if (missing.length) pieces.push(`${missing.length} not found (${describeNodeEntries(missing, registry)})`)
  if (skipped.length) pieces.push(`${skipped.length} skipped (${describeSkipReasons(skipped)})`)
  // v0.86.0: say HOW they matched. A `title`/`class` match means the state
  // came from another machine (or a rebuilt graph) and this run guessed --
  // correctly in the ordinary one-node-per-class case, but the user should
  // be able to see it rather than discover it in the output.
  const byTitle = matched.filter((m) => m.how === 'title').length
  const byClass = matched.filter((m) => m.how === 'class').length
  const how = []
  if (byTitle) how.push(`${byTitle} by name`)
  if (byClass) how.push(`${byClass} by position`)
  if (how.length) pieces[0] += ` (${how.join(', ')})`
  const partial = matched.filter((m) => (m.invalid || []).length > 0).length
  if (partial) pieces.push(`${partial} with skipped field(s)`)
  return { text: pieces.join(' · '), severity: matched.length === 0 ? 'warn' : 'success' }
}

/** Same ordering rule as controller.js's `compareSetEntries` -- name
 * case-insensitive, slug as the stable tiebreaker (never a real tie: slugs
 * are unique). Kept identical so a provisional/optimistic row lands in the
 * same slot the server's own `list_sets()`-style sort would place it. */
export function compareStateEntries(a, b) {
  const an = (a.name || '').toLowerCase()
  const bn = (b.name || '').toLowerCase()
  if (an !== bn) return an < bn ? -1 : 1
  return a.slug < b.slug ? -1 : a.slug > b.slug ? 1 : 0
}

/** Tolerant parse of the `Collapsed groups` property -- controller.js's
 * `parseCollapsedGroups()` duplicated by hand (this pack's no-cross-import
 * convention for small, exactly-shared helpers). */
export function parseCollapsedGroups(raw) {
  if (Array.isArray(raw)) return raw.filter((name) => typeof name === 'string')
  if (typeof raw === 'string') {
    const trimmed = raw.trim()
    if (!trimmed) return []
    let parsed
    try {
      parsed = JSON.parse(trimmed)
    } catch {
      return []
    }
    return Array.isArray(parsed) ? parsed.filter((name) => typeof name === 'string') : []
  }
  return []
}

/** `# Portraits` -> is-a-group-name / "Portraits" -- controller.js's
 * `isCategoryNameInput`/`categoryNameFromInput` duplicated by hand. */
export function isGroupNameInput(rawName) {
  return (rawName || '').trim().startsWith('#')
}
export function groupNameFromInput(rawName) {
  return (rawName || '').trim().replace(/^#+\s*/, '').trim()
}

/** A structurally-sound client copy of the group layout -- controller.js's
 * `normalizeLayoutClient()` duplicated by hand. Server healing (unknown
 * slugs dropped, missing ones appended) stays authoritative; this only
 * keeps local mutations well-formed between round trips. */
function normalizeLayoutClient(raw) {
  const categories = []
  const order = {}
  if (raw && typeof raw === 'object') {
    for (const entry of Array.isArray(raw.categories) ? raw.categories : []) {
      if (typeof entry !== 'string') continue
      const name = entry.trim()
      if (name && !categories.includes(name)) categories.push(name)
    }
    if (raw.order && typeof raw.order === 'object') {
      for (const [key, slugs] of Object.entries(raw.order)) {
        if (typeof key !== 'string' || !Array.isArray(slugs)) continue
        const name = key.trim()
        if (name && !categories.includes(name)) categories.push(name)
        order[name] = slugs.filter((s) => typeof s === 'string')
      }
    }
  }
  for (const name of categories) if (!order[name]) order[name] = []
  if (!order[UNCATEGORIZED]) order[UNCATEGORIZED] = []
  return { categories, order }
}

/** Remove *slug* from every order list -- the first half of any move. */
function pullSlugFromLayout(layout, slug) {
  for (const key of Object.keys(layout.order)) {
    layout.order[key] = layout.order[key].filter((s) => s !== slug)
  }
}

/** Which group's order list currently holds *slug* (UNCATEGORIZED when none). */
function categoryOfSlug(layout, slug) {
  for (const [key, slugs] of Object.entries(layout.order)) {
    if (slugs.includes(slug)) return key
  }
  return UNCATEGORIZED
}

// ---------------------------------------------------- state registry (shared)

/** Fetched ONCE per page -- every controller instance and every capture/
 * apply call joins the same promise (cross_sweep.js's `loadListFlags`
 * shape). A failed fetch resolves to an EMPTY registry (no state-bearing
 * classes) rather than rejecting, so a transient backend hiccup degrades to
 * "nothing to capture" instead of throwing out of a click handler. */
let stateRegistryPromise = null

function fetchStateRegistry() {
  if (stateRegistryPromise) return stateRegistryPromise
  stateRegistryPromise = (async () => {
    try {
      const data = await api.getJson(STATE_REGISTRY_ROUTE)
      return normalizeRegistry(data)
    } catch (error) {
      api.warn(`${NODE_TITLE}: GET ${STATE_REGISTRY_ROUTE} failed`, error)
      return { classes: {} }
    }
  })()
  return stateRegistryPromise
}

// -------------------------------------------------- live discovery (impure)

function isStateBearingClass(registry, klass) {
  const entry = registry?.classes?.[klass]
  return !!(entry && entry.widgets && Object.keys(entry.widgets).length)
}

/** Every state-bearing node on the canvas, subgraphs included --
 * `api.walkLiveNodes` filtered by the state registry. Keeps the live
 * `node` reference (for capture/apply/UI) alongside the plain fields the
 * pure helpers above consume. */
function discoverStateNodes(registry) {
  return api
    .walkLiveNodes(app.graph)
    .filter(({ node }) => node && isStateBearingClass(registry, node.type))
    .map(({ node, pathId }) => ({ pathId, class: node.type, title: node.title || node.type, node }))
}

/** Read every named widget's CURRENT `.value` off a live node -- a missing
 * widget is skipped with a console.warn (never thrown), per the capture
 * contract. */
function readWidgetValues(node, widgetNames) {
  const byName = new Map((node.widgets || []).map((w) => [w.name, w]))
  const values = {}
  for (const name of widgetNames) {
    const widget = byName.get(name)
    if (!widget) {
      api.warn(`${NODE_TITLE}: missing widget "${name}" on ${node.type} #${node.id}`)
      continue
    }
    values[name] = widget.value
  }
  return values
}

/** `discoverStateNodes()`'s output -> `applyPlan()`'s `liveIndex` shape. */
function buildLiveIndex(discovered) {
  const index = {}
  let order = 0
  for (const d of discovered) {
    const widgets = {}
    for (const widget of d.node.widgets || []) {
      if (!widget || typeof widget.name !== 'string') continue
      widgets[widget.name] = { value: widget.value, options: widget.options }
    }
    index[d.pathId] = { class: d.class, title: d.title || '', order, widgets }
    order += 1
  }
  return index
}

// ------------------------------------------------------ shared poller (M1/M2)
// One module-level interval feeds every live controller instance from a
// single fetch pair, instead of N per-node draw-driven polls -- exactly
// controller.js's v0.68.1 `registerController`/`sharedSetsRefresh` shape,
// duplicated here (not imported: this module owns a DIFFERENT feed).

const liveUniversalControllers = new Set()
let uscPollTimer = null
let uscSharedFetch = null
let uscRefetchQueued = false
let uscKickScheduled = false

/**
 * A newly-added node must not sit on the EMPTY default list for up to
 * `USC_POLL_MS` waiting on the next interval tick -- controller.js's
 * `scheduleSharedSetsRefresh()` one-tick coalescer, duplicated here: N
 * controllers registering in the same frame (a whole workflow loading)
 * still produce exactly ONE forced fetch, on the next microtask-free tick.
 */
function scheduleUniversalKick() {
  if (uscKickScheduled) return
  uscKickScheduled = true
  setTimeout(() => {
    uscKickScheduled = false
    sharedUniversalRefresh({ force: true }).catch(() => {})
  }, 0)
}

function registerUniversalController(node) {
  liveUniversalControllers.add(node)
  if (!uscPollTimer) {
    uscPollTimer = setInterval(() => scheduleUniversalRefresh(), USC_POLL_MS)
    document.addEventListener('visibilitychange', onUscVisibilityChange, { capture: true })
  }
  scheduleUniversalKick()
}

function unregisterUniversalController(node) {
  liveUniversalControllers.delete(node)
  if (liveUniversalControllers.size === 0 && uscPollTimer) {
    clearInterval(uscPollTimer)
    uscPollTimer = null
    document.removeEventListener('visibilitychange', onUscVisibilityChange, { capture: true })
  }
}

function onUscVisibilityChange() {
  if (document.visibilityState === 'visible') scheduleUniversalRefresh()
}

function scheduleUniversalRefresh() {
  if (document.hidden) return
  sharedUniversalRefresh().catch((error) => api.warn(`${NODE_TITLE}: shared poll failed`, error))
}

/** ONE fetch of the states+layout feed serves every live controller;
 * concurrent callers join the same in-flight promise, a `force` call during
 * one queues exactly one more. After the fetch settles, every controller
 * currently showing the Included-nodes page gets a change-gated local
 * re-walk (no network for that half -- FORMAT.md-style "page-open + poller
 * tick, change-gated" contract). */
async function sharedUniversalRefresh({ force = false } = {}) {
  if (uscSharedFetch) {
    if (force) uscRefetchQueued = true
    return uscSharedFetch
  }
  uscSharedFetch = (async () => {
    do {
      uscRefetchQueued = false
      try {
        const data = await api.getJson(STATES_ROUTE)
        for (const node of liveUniversalControllers) {
          node._guarded('states poll apply', () => node._applyStatesResponse(data))
        }
      } catch (error) {
        api.warn(`${NODE_TITLE}: GET ${STATES_ROUTE} failed`, error)
      }
    } while (uscRefetchQueued)
  })()
  try {
    await uscSharedFetch
  } finally {
    uscSharedFetch = null
  }
  for (const node of liveUniversalControllers) {
    if (node._removed || node._activePage !== 'nodes') continue
    node._guarded('nodes page poll refresh', () => node._refreshNodesPage())
  }
}

function announceStatesChanged() {
  try {
    window.dispatchEvent(new CustomEvent(USC_CHANGED_EVENT))
  } catch {
    // Announcement is a nicety; CRUD success must not depend on it.
  }
}

// ---------------------------------------------------------------- DOM helpers

/** Tiny DOM builder -- controller.js's `el()` duplicated by hand (this
 * pack's convention for small, exactly-shared, zero-import helpers). */
function el(tag, options = {}, children = []) {
  const node = document.createElement(tag)
  if (options.className) node.className = options.className
  if (options.text !== undefined) node.textContent = options.text
  if (options.attrs) {
    for (const [key, value] of Object.entries(options.attrs)) node.setAttribute(key, value)
  }
  for (const child of children) {
    if (child == null) continue
    node.append(child instanceof Node ? child : document.createTextNode(String(child)))
  }
  return node
}

const MIN_PANE_WIDTH = 320
const MIN_PANE_HEIGHT = 200
const PANE_WIDGET_NAME = 'universal_states'
const PANE_WIDGET_TYPE = 'eps_universal_state_panel'

/** controller.js's `installMinWidth()` duplicated by hand -- an additive,
 * guard-flagged `onResize` clamp so this node can never be dragged (or
 * restored from a saved workflow) narrower than its two-pane layout can
 * render (FORMAT.md §7.2 width-floor convention). */
function installMinWidth(node, minWidth) {
  if (!node || node.__epsUscMinWidthInstalled) return
  node.__epsUscMinWidthInstalled = true
  const originalOnResize = node.onResize
  node.onResize = function (size) {
    if (size && size[0] < minWidth) size[0] = minWidth
    return originalOnResize?.call(this, size)
  }
  if (Array.isArray(node.size) && node.size[0] < minWidth) node.size[0] = minWidth
}

const PANE_STYLE_TAG_ID = 'eps-universal-controller-styles'
let controllerStylesInjected = false

/** `llsc-*` (controller.js) -> `lusc-*` here, per the owner's spec ("UI
 * should start identical to the Lora State Controller"). Values copied
 * from `STATE_PANE_CSS_TEXT`; drag-specific rules dropped (no drag this
 * round -- see the file header), header-tab + nodes-page + move-to-group
 * rules added. */
const PANE_CSS_TEXT = `
.lusc-root {
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
.lusc-header {
  flex: 0 0 auto;
  display: flex;
  flex-direction: row;
  border-bottom: 1px solid var(--border-color, #444);
}
.lusc-tab {
  flex: 1 1 auto;
  box-sizing: border-box;
  background: var(--comfy-menu-bg, #262626);
  border: none;
  color: var(--descrip-text, #999);
  padding: 5px 4px;
  font-size: 11px;
  font-family: inherit;
  cursor: pointer;
}
.lusc-tab:hover { background: var(--content-hover-bg, #2a2a2a); }
.lusc-tab-active {
  color: var(--input-text, #ccc);
  font-weight: 600;
  box-shadow: inset 0 -2px 0 rgba(66, 133, 244, 1);
}
.lusc-body { flex: 1 1 auto; min-height: 0; overflow: hidden; }
.lusc-states-page, .lusc-nodes-page { width: 100%; height: 100%; box-sizing: border-box; }
.lusc-panes {
  display: flex;
  flex-direction: row;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.lusc-pane-left {
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  border-right: 1px solid var(--border-color, #444);
}
.lusc-pane-right {
  flex: 0 0 auto;
  min-width: 104px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 4px;
  overflow-y: auto;
}
.lusc-search {
  flex: 0 0 auto;
  box-sizing: border-box;
  width: 100%;
  background: var(--comfy-input-bg, #1e1e1e);
  border: none;
  border-bottom: 1px solid var(--border-color, #444);
  color: var(--input-text, #ccc);
  padding: 4px 6px;
  font-size: 11px;
  font-family: inherit;
  outline: none;
}
.lusc-move-row {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px 6px;
  border-bottom: 1px solid var(--border-color, #444);
  background: var(--comfy-menu-bg, #262626);
}
.lusc-move-label { flex: 0 0 auto; color: var(--descrip-text, #999); font-size: 10px; }
.lusc-move-select {
  flex: 1 1 auto;
  min-width: 0;
  background: var(--comfy-input-bg, #1e1e1e);
  color: var(--input-text, #ccc);
  border: 1px solid var(--border-color, #444);
  border-radius: 3px;
  font-size: 10px;
  font-family: inherit;
}
.lusc-list { flex: 1 1 auto; min-height: 0; overflow-y: auto; overflow-x: hidden; padding: 3px; }
.lusc-row {
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
.lusc-row:hover { background: var(--content-hover-bg, #2a2a2a); }
.lusc-row:focus-visible { box-shadow: inset 0 0 0 1px var(--border-color, #444); }
.lusc-row-active, .lusc-row-active:hover {
  background: rgba(66, 133, 244, 0.28);
  border-left-color: rgba(66, 133, 244, 1);
  font-weight: 600;
}
.lusc-category {
  padding: 4px 6px 2px;
  margin-top: 4px;
  font-size: 9.5px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--descrip-text, #999);
  user-select: none;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
  border-radius: 3px;
  outline: none;
  display: flex;
  align-items: center;
  gap: 4px;
}
.lusc-category:hover { background: var(--content-hover-bg, #2a2a2a); }
.lusc-category:focus-visible { box-shadow: inset 0 0 0 1px var(--border-color, #444); }
.lusc-category-label { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.lusc-category-delete {
  flex: 0 0 auto; background: none; border: none; cursor: pointer; padding: 0 2px;
  color: var(--descrip-text, #999); font-size: 10px; line-height: 1; font-family: inherit;
  visibility: hidden;
}
.lusc-category:hover .lusc-category-delete { visibility: visible; }
.lusc-category-delete-armed { color: #ff6b6b; visibility: visible; }
.lusc-inline-rename-host { padding: 2px 4px; cursor: text; }
.lusc-inline-rename {
  width: 100%; box-sizing: border-box; min-width: 0;
  font: inherit; font-size: 11px; font-weight: 400;
  text-transform: none; letter-spacing: normal;
  background: var(--comfy-input-bg, #1e1e1e); color: var(--input-text, #ccc);
  border: 1px solid rgba(66, 133, 244, 0.9); border-radius: 3px;
  padding: 1px 4px; outline: none;
}
.lusc-empty { padding: 6px 7px; color: var(--descrip-text, #999); font-style: italic; }
.lusc-btn {
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
.lusc-btn:hover:not(:disabled) { background: var(--content-hover-bg, #2a2a2a); }
.lusc-btn:disabled { opacity: 0.45; cursor: default; }
.lusc-btn-danger { border-color: var(--error-text, #ff4444); color: var(--error-text, #ff4444); }
.lusc-nodes-list { width: 100%; height: 100%; box-sizing: border-box; overflow-y: auto; padding: 3px; }
.lusc-class-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px 2px;
  margin-top: 4px;
  font-size: 9.5px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--descrip-text, #999);
}
.lusc-class-label { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.lusc-node-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 7px 3px 18px;
  white-space: nowrap;
  overflow: hidden;
}
.lusc-node-row-excluded { opacity: 0.45; }
.lusc-node-title { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.lusc-node-path { flex: 0 0 auto; color: var(--descrip-text, #999); font-size: 10px; }
`

function injectUniversalControllerStyles() {
  if (controllerStylesInjected) return
  controllerStylesInjected = true
  if (document.getElementById(PANE_STYLE_TAG_ID)) return
  const style = document.createElement('style')
  style.id = PANE_STYLE_TAG_ID
  style.textContent = PANE_CSS_TEXT
  document.head.appendChild(style)
}

// ------------------------------------------------------------ node registration

/** Give the frontend-only node its real display name before defs reach the
 * store -- controller.js's `nameNodeDef()` twin, one call site per module. */
export function nameNodeDef(defs) {
  if (!Array.isArray(defs)) return
  for (const def of defs) {
    if (def && def.name === NODE_TYPE) def.display_name = NODE_TITLE
  }
}

/**
 * Register the `EPSUniversalStateController` virtual node type with
 * LiteGraph. Called once from the extension's `init()` hook
 * (`web/lora_library.js`), which already wraps this call in its own
 * try/catch -- everything here is still guarded too, so this file can never
 * throw during graph load.
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

    class EPSUniversalStateControllerNode extends LGraphNode {
      static title = NODE_TITLE
      static description =
        'Captures a NAMED state across every state-bearing node in the ' +
        'workflow (switchers, notebooks, and anything else this pack ' +
        'registers) and applies it back later. This node never runs at ' +
        'queue time.'

      constructor(title = NODE_TITLE) {
        super(title)
        this.isVirtualNode = true
        this.serialize_widgets = true

        this._w = {}
        this._pane = null
        this._activePage = 'states' // property-free UI state; default States
        this._registry = null

        this._statesCache = []
        this._layoutCache = normalizeLayoutClient(null)
        this._collapsedCategories = new Set()
        this._layoutLoaded = false
        this._layoutToken = 0
        this._layoutSaveInFlight = false
        this._layoutSaveQueued = false
        this._categoryRename = null

        this._statesSignature = ''
        this._nodesPageSignature = ''
        this._includedNodesCache = []
        this._searchQuery = ''

        this._selectedSlug = null
        this._deleteInFlightSlugs = new Set()
        this._saveInFlightSlugs = new Set()
        this._saveProvisionalSeq = 0

        this._removed = false
        this._onStatesChanged = null

        // Task-spec property: a bare array of collapsed group NAMES,
        // identical shape to controller.js's `Collapsed groups`.
        this.addProperty(PROP_COLLAPSED_GROUPS, [], 'array')
        this._applyCollapsedGroupsFromProperty()
        // Task-spec property: EXCLUSIONS ONLY -- default included, so a
        // freshly-added node never needs an entry (see PROP_INCLUDED_NODES).
        this.addProperty(PROP_INCLUDED_NODES, { nodes: {}, classes: {} }, 'object')

        this._guarded('build widgets', () => this._buildWidgets())
      }

      /** Repair a title baked into a workflow saved before a rename --
       * controller.js's identical `configure()` override. */
      configure(info) {
        super.configure(info)
        try {
          if (this.title === NODE_TYPE) this.title = NODE_TITLE
        } catch (error) {
          api.warn('title repair failed', error)
        }
      }

      onAdded() {
        this._guarded('onAdded', () => {
          this._removed = false
          this._subscribeStatesChanged()
          registerUniversalController(this)
          fetchStateRegistry().then((registry) => {
            if (this._removed) return
            this._registry = registry
            if (this._activePage === 'nodes') this._refreshNodesPage({ force: true })
          })
        })
      }

      onRemoved() {
        this._guarded('onRemoved', () => {
          this._removed = true
          clearTimeout(this._w.deleteBtn?._armTimer)
          this._categoryRename = null
          this._unsubscribeStatesChanged()
          unregisterUniversalController(this)
        })
      }

      _subscribeStatesChanged() {
        if (this._onStatesChanged) return
        this._onStatesChanged = () => sharedUniversalRefresh({ force: true }).catch(() => {})
        window.addEventListener(USC_CHANGED_EVENT, this._onStatesChanged, { capture: true })
      }

      _unsubscribeStatesChanged() {
        if (!this._onStatesChanged) return
        window.removeEventListener(USC_CHANGED_EVENT, this._onStatesChanged, { capture: true })
        this._onStatesChanged = null
      }

      /** Every handler funnels through here -- never throw. */
      _guarded(label, fn) {
        try {
          fn()
        } catch (error) {
          api.warn(`${NODE_TITLE}: ${label} failed`, error)
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

      onPropertyChanged(name, value) {
        if (name === PROP_COLLAPSED_GROUPS) {
          this._guarded('Collapsed groups property changed', () => {
            this._applyCollapsedGroupsFromProperty()
            this._renderStateList()
          })
          return
        }
        if (name === PROP_INCLUDED_NODES) {
          this._guarded('Included nodes property changed', () => {
            this._nodesPageSignature = ''
            if (this._activePage === 'nodes') this._refreshNodesPage({ force: true })
          })
        }
      }

      _applyCollapsedGroupsFromProperty() {
        const names = parseCollapsedGroups(this.properties?.[PROP_COLLAPSED_GROUPS])
        this._collapsedCategories = new Set(names)
      }

      _syncCollapsedGroupsProperty() {
        this.properties = this.properties || {}
        this.properties[PROP_COLLAPSED_GROUPS] = Array.from(this._collapsedCategories)
        this.setDirtyCanvas(true, true)
      }

      /** Property-only exclusions, defensively re-shaped on every read (a
       * hand-edited or foreign `true` entry, or any other malformed value,
       * is dropped rather than trusted -- see `normalizeExclusions`). */
      _exclusions() {
        return normalizeExclusions(this.properties?.[PROP_INCLUDED_NODES])
      }

      _writeExclusions(next) {
        this.properties = this.properties || {}
        this.properties[PROP_INCLUDED_NODES] = next
        this.setDirtyCanvas(true, true)
        this._nodesPageSignature = ''
        this._refreshNodesPage({ force: true })
      }

      // ------------------------------------------------------------ widgets

      _buildWidgets() {
        // Hidden serialized `set` widget carries the selection across
        // reload (Notebook's `entry`-widget trick, controller.js's `set`
        // twin); visible `name` doubles as the New-State name field AND the
        // `# group name` group-creation input.
        this._w.set = this.addWidget('text', 'set', '', () => {}, {})
        this._w.set.hidden = true
        this._w.set.options = { ...(this._w.set.options || {}), hidden: true }

        this._w.name = this.addWidget('text', 'name', '', () => {}, {})
        this._w.name.tooltip =
          'The name for the next Save State. Prefix with "#" to create a ' +
          'group instead (e.g. "# Portraits").'

        this._buildPanel()
      }

      // -------------------------------------------------------------- panel

      _buildPanel() {
        injectUniversalControllerStyles()
        this._pane = {}

        this._pane.statesTab = this._createTabButton('States', 'states')
        this._pane.nodesTab = this._createTabButton('Included nodes', 'nodes')
        const header = el('div', { className: 'lusc-header' }, [this._pane.statesTab, this._pane.nodesTab])

        this._pane.searchEl = el('input', {
          className: 'lusc-search',
          attrs: { type: 'text', placeholder: 'Search states…', spellcheck: 'false' }
        })
        this._pane.searchEl.addEventListener('input', () => {
          this._searchQuery = this._pane.searchEl.value
          this._renderStateList()
        })
        this._pane.searchEl.addEventListener('keydown', (event) => {
          event.stopPropagation() // canvas hotkeys must not eat search typing
          if (event.key === 'Escape') {
            event.preventDefault()
            this._pane.searchEl.value = ''
            this._searchQuery = ''
            this._renderStateList()
          }
        })

        this._pane.moveGroupSelect = el('select', { className: 'lusc-move-select' })
        this._pane.moveGroupSelect.disabled = true
        this._pane.moveGroupSelect.addEventListener('change', () => {
          this._guarded('move to group', () => this._onMoveToGroupChange())
        })
        const moveRow = el('div', { className: 'lusc-move-row' }, [
          el('span', { className: 'lusc-move-label', text: 'Group:' }),
          this._pane.moveGroupSelect
        ])

        this._pane.listEl = el('div', { className: 'lusc-list' })
        const leftPane = el('div', { className: 'lusc-pane-left' }, [
          this._pane.searchEl,
          moveRow,
          this._pane.listEl
        ])

        this._w.captureBtn = this._createActionButton(
          'lusc-btn',
          LABEL_CAPTURE,
          () => this._onCaptureClick(),
          'Capture every included node’s current widget values as a NEW ' +
            'named state. A name starting with "#" creates a group instead. ' +
            'Never overwrites an existing state.'
        )
        this._w.updateBtn = this._createActionButton(
          'lusc-btn',
          LABEL_UPDATE,
          () => this._onUpdateClick(),
          'Overwrite the selected state with every included node’s ' +
            'current widget values. Edit the name field first to rename ' +
            'it too.'
        )
        this._w.applyBtn = this._createActionButton(
          'lusc-btn',
          LABEL_APPLY,
          () => this._onApplyClick(),
          'Write the selected state’s widget values back onto the ' +
            'matching live nodes.'
        )
        this._w.deleteBtn = this._createActionButton(
          'lusc-btn lusc-btn-danger',
          LABEL_DELETE,
          () => this._onDeleteClick(),
          'Delete the selected state’s file from your library folder. ' +
            'Click twice to confirm.'
        )
        const rightPane = el('div', { className: 'lusc-pane-right' }, [
          this._w.captureBtn,
          this._w.updateBtn,
          this._w.applyBtn,
          this._w.deleteBtn
        ])

        this._pane.statesPage = el('div', { className: 'lusc-states-page' }, [
          el('div', { className: 'lusc-panes' }, [leftPane, rightPane])
        ])

        this._pane.nodesListEl = el('div', { className: 'lusc-nodes-list' })
        this._pane.nodesPage = el('div', { className: 'lusc-nodes-page' }, [this._pane.nodesListEl])
        this._pane.nodesPage.style.display = 'none'

        const body = el('div', { className: 'lusc-body' }, [this._pane.statesPage, this._pane.nodesPage])
        this._pane.root = el('div', { className: 'lusc-root' }, [header, body])

        if (typeof this.addDOMWidget !== 'function') {
          api.warn(`${NODE_TITLE}: this ComfyUI frontend has no addDOMWidget; panel not attached`)
          return
        }
        const domWidget = this.addDOMWidget(PANE_WIDGET_NAME, PANE_WIDGET_TYPE, this._pane.root, {
          hideOnZoom: true,
          serialize: false,
          getMinHeight: () => MIN_PANE_HEIGHT
        })
        domWidget.serialize = false
        domWidget.serializeValue = () => undefined
        installMinWidth(this, MIN_PANE_WIDTH)

        this._refreshActionButtonsEnabled()
        this._renderStateList()
      }

      _createTabButton(label, page) {
        const btn = el('button', {
          className: 'lusc-tab' + (this._activePage === page ? ' lusc-tab-active' : ''),
          text: label
        })
        btn.addEventListener('click', () => this._guarded('tab click', () => this._setActivePage(page)))
        return btn
      }

      /** The two-page toggle -- a small, PROPERTY-FREE instance field
       * (`_activePage`, default 'states'); switching pages never dirties
       * the canvas or touches serialization, only DOM visibility. */
      _setActivePage(page) {
        if (this._activePage === page) return
        this._activePage = page
        this._pane.statesTab.classList.toggle('lusc-tab-active', page === 'states')
        this._pane.nodesTab.classList.toggle('lusc-tab-active', page === 'nodes')
        this._pane.statesPage.style.display = page === 'states' ? '' : 'none'
        this._pane.nodesPage.style.display = page === 'nodes' ? '' : 'none'
        if (page === 'nodes') this._refreshNodesPage({ force: true })
      }

      _createActionButton(className, label, onClick, title) {
        const button = el('button', { className, text: label, attrs: { title } })
        button.addEventListener('click', () => this._guarded(`${label} click`, onClick))
        return button
      }

      _toast(severity, summary, detail, life) {
        try {
          app.extensionManager?.toast?.add?.({
            severity,
            summary,
            detail,
            life: life ?? (severity === 'error' ? 6000 : 3000)
          })
        } catch {
          // Toast is a nicety; never let it be the reason an action "fails".
        }
      }

      // -------------------------------------------------------- States page

      _matchesSearch(entry) {
        const query = (this._searchQuery || '').trim().toLowerCase()
        if (!query) return true
        return (entry.name || entry.slug || '').toLowerCase().includes(query)
      }

      /** §4.2-style render plan: uncategorized first, then each group's
       * header + entries in layout order -- controller.js's `_groupedRows()`
       * twin, with a SEARCH pass folded in (FORMAT.md §7.2's search
       * contract: a query ignores collapse and hides empty groups, never
       * touches selection). */
      _groupedRows() {
        const searching = !!(this._searchQuery || '').trim()
        const matching = this._statesCache.filter((e) => this._matchesSearch(e))
        const bySlug = new Map(matching.map((e) => [e.slug, e]))
        const placed = new Set()
        const rows = []
        const pushEntries = (slugs) => {
          for (const slug of slugs) {
            const entry = bySlug.get(slug)
            if (!entry || placed.has(slug)) continue
            placed.add(slug)
            rows.push({ kind: 'entry', entry })
          }
        }
        pushEntries(this._layoutCache.order[UNCATEGORIZED] || [])
        const leftovers = matching.filter((entry) => {
          if (placed.has(entry.slug)) return false
          return !this._layoutCache.categories.some((c) =>
            (this._layoutCache.order[c] || []).includes(entry.slug)
          )
        })
        for (const entry of leftovers) {
          placed.add(entry.slug)
          rows.push({ kind: 'entry', entry })
        }
        for (const category of this._layoutCache.categories) {
          const slugs = this._layoutCache.order[category] || []
          const matchCount = slugs.filter((slug) => bySlug.has(slug)).length
          if (searching && matchCount === 0) continue
          rows.push({ kind: 'header', category, matchCount })
          const collapsed = !searching && this._collapsedCategories.has(category)
          if (!collapsed) pushEntries(slugs)
          else for (const slug of slugs) placed.add(slug)
        }
        return rows
      }

      _renderStateList() {
        const listEl = this._pane?.listEl
        if (!listEl || this._removed) return
        if (this._categoryRename) return // an open rename editor must survive a repaint
        const focused = document.activeElement
        const focusedSlug = focused && listEl.contains(focused) ? focused.getAttribute('data-slug') : null
        const scrollTop = listEl.scrollTop
        listEl.replaceChildren()

        if (!this._statesCache.length) {
          listEl.append(el('div', { className: 'lusc-empty', text: 'No saved states.' }))
        } else {
          const rows = this._groupedRows()
          if (!rows.length) {
            listEl.append(el('div', { className: 'lusc-empty', text: 'No states match your search.' }))
          } else {
            const selected = this._selectedStateEntry()
            for (const planRow of rows) {
              if (planRow.kind === 'header') {
                listEl.append(this._buildCategoryHeader(planRow.category, planRow.matchCount))
                continue
              }
              const entry = planRow.entry
              const active = selected && selected.slug === entry.slug
              const row = el('div', {
                className: 'lusc-row' + (active ? ' lusc-row-active' : ''),
                text: entry.label,
                attrs: { tabindex: '0', 'data-slug': entry.slug, title: entry.label }
              })
              row.addEventListener('click', () => {
                this._guarded('state row click', () => this._onStatePicked(entry.label))
              })
              // Owner ask 2026-08-28: "double clicking on elements ...
              // should apply them." The click above has already selected
              // the row, so this only has to fire the apply -- the same
              // path the Apply State button takes.
              row.addEventListener('dblclick', (event) => {
                event.preventDefault()
                event.stopPropagation()
                this._guarded('state row dblclick', () => {
                  this._onStatePicked(entry.label)
                  this._onApplyClick()
                })
              })
              row.addEventListener('keydown', (event) => {
                if (event.key !== 'Enter' && event.key !== ' ') return
                event.preventDefault()
                this._guarded('state row key', () => this._onStatePicked(entry.label))
              })
              listEl.append(row)
            }
          }
        }

        listEl.scrollTop = scrollTop
        if (focusedSlug) {
          const toFocus = listEl.querySelector(`[data-slug="${cssEscape(focusedSlug)}"]`)
          toFocus?.focus?.({ preventScroll: true })
        }
        this._refreshActionButtonsEnabled()
        this._renderMoveGroupControl()
      }

      // `captureBtn` (New State) is deliberately NEVER touched here --
      // controller.js's own captureBtn precedent (file header trim #2): a
      // `#`-named group creation must work with no state selected at all,
      // and New State's own click handler already probes/toasts for
      // anything else it needs. Save/Apply/Delete all require a SELECTED
      // state and are disabled without one.
      _refreshActionButtonsEnabled() {
        const hasSelection = !!this._selectedStateEntry()
        if (this._w.updateBtn) this._w.updateBtn.disabled = !hasSelection
        if (this._w.applyBtn) this._w.applyBtn.disabled = !hasSelection
        if (this._w.deleteBtn && !this._w.deleteBtn._armed) this._w.deleteBtn.disabled = !hasSelection
      }

      _buildCategoryHeader(category, matchCount) {
        const searching = !!(this._searchQuery || '').trim()
        const collapsed = !searching && this._collapsedCategories.has(category)
        const count = matchCount != null ? matchCount : (this._layoutCache.order[category] || []).length
        const label = el('span', {
          className: 'lusc-category-label',
          text: `${collapsed ? '▸' : '▾'} ${category}${collapsed ? ` (${count})` : ''}`
        })
        const deleteBtn = el('button', {
          className: 'lusc-category-delete',
          text: '✕',
          attrs: { title: 'Remove this group — its states move to the ungrouped list. Click twice.' }
        })
        deleteBtn.addEventListener('click', (event) => {
          event.stopPropagation()
          if (!deleteBtn._armed) {
            deleteBtn._armed = true
            deleteBtn.classList.add('lusc-category-delete-armed')
            deleteBtn.textContent = 'sure?'
            clearTimeout(deleteBtn._armTimer)
            deleteBtn._armTimer = setTimeout(() => {
              deleteBtn._armed = false
              deleteBtn.classList.remove('lusc-category-delete-armed')
              deleteBtn.textContent = '✕'
            }, CATEGORY_DELETE_CONFIRM_MS)
            return
          }
          clearTimeout(deleteBtn._armTimer)
          this._guarded('delete group', () => this._deleteCategory(category))
        })
        const headerEl = el(
          'div',
          {
            className: 'lusc-category',
            attrs: { tabindex: '0', title: category, 'data-category': category }
          },
          [label, deleteBtn]
        )
        headerEl.addEventListener('click', (event) => {
          if (event.target === deleteBtn || searching) return
          this._guarded('group collapse', () => this._toggleCategoryCollapsed(category))
        })
        headerEl.addEventListener('dblclick', (event) => {
          event.preventDefault()
          event.stopPropagation()
          this._guarded('group rename', () => this._beginCategoryRename(category))
        })
        headerEl.addEventListener('keydown', (event) => {
          if (event.key !== 'Enter' && event.key !== ' ') return
          event.preventDefault()
          if (!searching) this._guarded('group collapse', () => this._toggleCategoryCollapsed(category))
        })
        return headerEl
      }

      _beginCategoryRename(category) {
        if (this._removed) return
        if (this._categoryRename && this._categoryRename.category !== category) this._commitCategoryRename()
        const header = this._pane?.listEl?.querySelector(`[data-category="${cssEscape(category)}"]`)
        if (!header) return
        const input = el('input', {
          className: 'lusc-inline-rename',
          attrs: { type: 'text', spellcheck: 'false', 'aria-label': 'Rename group' }
        })
        input.value = category
        const rename = { category, inputEl: input }
        this._categoryRename = rename
        header.replaceChildren(input)
        header.classList.add('lusc-inline-rename-host')
        input.addEventListener('keydown', (event) => {
          event.stopPropagation()
          if (event.key === 'Enter') {
            event.preventDefault()
            this._guarded('group rename commit', () => this._commitCategoryRename())
          } else if (event.key === 'Escape') {
            event.preventDefault()
            this._guarded('group rename cancel', () => this._cancelCategoryRename())
          }
        })
        for (const type of ['pointerdown', 'mousedown', 'dblclick', 'click']) {
          input.addEventListener(type, (event) => event.stopPropagation())
        }
        input.addEventListener('blur', () => {
          if (this._categoryRename !== rename) return
          this._guarded('group rename commit', () => this._commitCategoryRename())
        })
        input.focus()
        input.select()
      }

      _cancelCategoryRename() {
        if (!this._categoryRename) return
        this._categoryRename = null
        this._renderStateList()
      }

      _commitCategoryRename() {
        const rename = this._categoryRename
        if (!rename) return
        this._categoryRename = null
        const from = rename.category
        const to = (rename.inputEl.value || '').trim()
        if (!to || to === from) {
          this._renderStateList()
          return
        }
        this._withLoadedLayout('group rename', () => {
          const layout = this._layoutCache
          if (!layout.categories.includes(from)) {
            this._toast('warn', NODE_TITLE, `Group "${from}" no longer exists.`)
            this._renderStateList()
            return
          }
          if (layout.categories.includes(to)) {
            this._toast('warn', NODE_TITLE, `A group named "${to}" already exists.`)
            this._renderStateList()
            return
          }
          layout.categories = layout.categories.map((c) => (c === from ? to : c))
          layout.order[to] = layout.order[from] || []
          delete layout.order[from]
          if (this._collapsedCategories.delete(from)) {
            this._collapsedCategories.add(to)
            this._syncCollapsedGroupsProperty()
          }
          this._renderStateList()
          this._saveLayout().catch((error) => api.warn(`${NODE_TITLE}: group rename save failed`, error))
        })
      }

      _toggleCategoryCollapsed(category) {
        if (this._collapsedCategories.has(category)) this._collapsedCategories.delete(category)
        else this._collapsedCategories.add(category)
        this._syncCollapsedGroupsProperty()
        this._renderStateList()
      }

      _deleteCategory(category) {
        this._withLoadedLayout('delete group', () => {
          const layout = this._layoutCache
          const orphans = layout.order[category] || []
          layout.order[UNCATEGORIZED] = [...(layout.order[UNCATEGORIZED] || []), ...orphans]
          delete layout.order[category]
          layout.categories = layout.categories.filter((c) => c !== category)
          if (this._collapsedCategories.delete(category)) this._syncCollapsedGroupsProperty()
          this._toast('info', NODE_TITLE, `Group "${category}" removed — its states are ungrouped.`)
          this._renderStateList()
          this._saveLayout().catch((error) => api.warn(`${NODE_TITLE}: group delete failed`, error))
        })
      }

      /** The drag-reorder replacement (file header, scope trim #1): a
       * compact `<select>` next to search that moves the SELECTED state
       * into a group, or back to ungrouped. */
      _renderMoveGroupControl() {
        const select = this._pane?.moveGroupSelect
        if (!select) return
        const entry = this._selectedStateEntry()
        select.replaceChildren(el('option', { attrs: { value: UNCATEGORIZED }, text: '(ungrouped)' }))
        for (const category of this._layoutCache.categories) {
          select.append(el('option', { attrs: { value: category }, text: category }))
        }
        select.disabled = !entry
        if (entry) select.value = categoryOfSlug(this._layoutCache, entry.slug)
      }

      async _onMoveToGroupChange() {
        const entry = this._selectedStateEntry()
        const select = this._pane?.moveGroupSelect
        if (!entry || !select) return
        const target = select.value || UNCATEGORIZED
        if (!(await this._ensureLayoutLoaded())) {
          this._renderMoveGroupControl()
          return
        }
        const layout = this._layoutCache
        pullSlugFromLayout(layout, entry.slug)
        if (!layout.order[target]) layout.order[target] = []
        layout.order[target].push(entry.slug)
        if (target !== UNCATEGORIZED && !layout.categories.includes(target)) layout.categories.push(target)
        this._renderStateList()
        await this._saveLayout()
      }

      /** v0.68.1-style data-loss guard (controller.js): before the FIRST
       * successful states+layout GET, `_layoutCache` is the EMPTY default,
       * and a layout POST made on top of it would replace every shared
       * group with just this edit. Every layout-mutating action goes
       * through `_withLoadedLayout()`, never straight to `_saveLayout()`. */
      async _ensureLayoutLoaded() {
        if (this._layoutLoaded) return true
        try {
          const data = await api.getJson(STATES_ROUTE)
          if (this._removed) return false
          this._applyStatesResponse(data)
        } catch (error) {
          api.warn(`${NODE_TITLE}: GET ${STATES_ROUTE} failed (group edit refused)`, error)
        }
        if (this._layoutLoaded) return true
        this._toast('warn', NODE_TITLE, MSG_LAYOUT_NOT_LOADED)
        this._renderStateList()
        return false
      }

      _withLoadedLayout(label, edit) {
        if (this._layoutLoaded) {
          edit()
          return
        }
        this._ensureLayoutLoaded()
          .then((ok) => {
            if (ok && !this._removed) this._guarded(label, edit)
          })
          .catch((error) => api.warn(`${NODE_TITLE}: ${label} failed`, error))
      }

      /** Full-replace POST of the layout; the response is server-healed
       * truth. Saves COALESCE (a second edit during an in-flight POST
       * queues one more instead of racing) via `_layoutToken` -- identical
       * shape to controller.js's `_saveLayout()`. */
      async _saveLayout() {
        this._layoutToken++
        if (this._layoutSaveInFlight) {
          this._layoutSaveQueued = true
          return
        }
        this._layoutSaveInFlight = true
        try {
          do {
            this._layoutSaveQueued = false
            const token = this._layoutToken
            try {
              const data = await api.postJson(STATES_LAYOUT_ROUTE, { layout: this._layoutCache })
              this._layoutLoaded = true
              if (token === this._layoutToken) {
                this._layoutCache = normalizeLayoutClient(data?.layout)
                this._renderStateList()
              }
              for (const other of liveUniversalControllers) {
                if (other === this || other._removed) continue
                other._guarded('layout fan-out', () => {
                  other._layoutCache = normalizeLayoutClient(data?.layout)
                  other._layoutLoaded = true
                  other._renderStateList()
                })
              }
            } catch (error) {
              api.warn(`${NODE_TITLE}: saving the group layout failed`, error)
              const failMessage = `Could not save the group layout: ${error?.message || error}`
              this._toast('error', NODE_TITLE, failMessage)
              this._layoutSaveQueued = false
              this._layoutSaveInFlight = false
              sharedUniversalRefresh({ force: true }).catch(() => {})
              return
            }
          } while (this._layoutSaveQueued)
        } finally {
          this._layoutSaveInFlight = false
        }
      }

      // ------------------------------------------------------ button actions

      _onCaptureClick() {
        this._disarmDeleteButton()
        if (isGroupNameInput(this._w.name?.value)) {
          this._runAction('New Group', () => this._doNewCategory())
          return
        }
        this._runAction(LABEL_CAPTURE, () => this._doCapture())
      }

      async _doNewCategory() {
        const name = groupNameFromInput(this._w.name?.value)
        if (!name) {
          this._toast('warn', NODE_TITLE, 'Enter a group name after the # (e.g. "# Portraits").')
          return
        }
        if (!(await this._ensureLayoutLoaded())) return
        if (this._layoutCache.categories.includes(name)) {
          this._toast('warn', NODE_TITLE, `A group named "${name}" already exists.`)
          return
        }
        this._layoutCache.categories.push(name)
        this._layoutCache.order[name] = []
        if (this._collapsedCategories.delete(name)) this._syncCollapsedGroupsProperty()
        this._renderStateList()
        this._clearNameField()
        this._toast(
          'info',
          NODE_TITLE,
          `Group "${name}" created — use the Group dropdown to assign states.`
        )
        await this._saveLayout()
      }

      _clearNameField() {
        const widget = this._w.name
        if (!widget) return
        widget.value = ''
        try {
          widget.callback?.('')
        } catch (error) {
          api.warn(`${NODE_TITLE}: name widget callback threw`, error)
        }
        this.setDirtyCanvas(true, true)
      }

      _onUpdateClick() {
        this._disarmDeleteButton()
        this._runAction(LABEL_UPDATE, () => this._doUpdate())
      }

      /**
       * Save State's rename decision -- controller.js's `_saveAsNewName()`
       * ported verbatim (2026-07-22 "Save renames in place", cited in the
       * file header trim #2 note): a non-empty `name` field that DIFFERS
       * from the selected entry's own current name renames the state IN
       * PLACE (same slug, new `name` inside the file); an empty or
       * unedited field means a plain overwrite that leaves the name
       * untouched. There is no "spin off a copy" path here at all -- New
       * State (`_doCapture()`) is the only way a new file is ever created,
       * so an edited name field can only ever mean "rename this one."
       */
      _saveAsNewName(entry) {
        const typed = (this._w.name?.value || '').trim()
        const current = entry?.name || entry?.slug || ''
        if (!typed || typed === current) return null
        return typed
      }

      _onApplyClick() {
        this._runAction(LABEL_APPLY, () => this._doApply())
      }

      /**
       * Fetch the full state, plan the diff against live nodes (`applyPlan`,
       * exclusion- and registry-aware, choice-vs-live-options checked), then
       * WRITE every valid `{name, value}` pair: `widget.value = v` THEN
       * `widget.callback?.(v, app.canvas, node)`, per-node -- and exactly
       * ONE `setDirtyCanvas(true, true)` after every node is done, never
       * per-write. Finishes with the diff toast (`summarizeApply`); applying
       * 0 nodes is a WARN toast, never a silent success.
       */
      async _doApply() {
        const entry = this._selectedStateEntry()
        if (!entry) {
          this._toast('warn', NODE_TITLE, 'Pick a saved state first.')
          return
        }
        const registry = await fetchStateRegistry()
        if (this._removed) return
        this._registry = registry
        let full
        try {
          full = await api.getJson(STATE_ROUTE, { slug: entry.slug })
        } catch (error) {
          this._toast('error', NODE_TITLE, `Could not load "${entry.name}": ${error?.message || error}`)
          return
        }
        const exclusions = this._exclusions()
        const discovered = discoverStateNodes(registry)
        const liveIndex = buildLiveIndex(discovered)
        const plan = applyPlan(full?.nodes || [], liveIndex, registry, exclusions)
        this._writeApplyPlan(plan, discovered)
        const { text, severity } = summarizeApply(plan, registry)
        this._toast(severity, NODE_TITLE, `Applied "${full?.name ?? entry.name}" -> ${text}.`)
      }

      /**
       * 2026-08-29 bugfix round (owner: "applying any of the sets won't
       * change anything") -- after the SAME write loop + single
       * `setDirtyCanvas` this method always had, announce which nodes were
       * actually written so their own DOM panel (if any) can re-sync --
       * see the file header's own section on this and api.js's
       * `announceWidgetsChangedExternally()` doc comment for the full
       * mechanism. Built from `writtenNames`, NOT `matchedNode.writes`
       * itself: a matched-but-all-invalid node (every write dropped by
       * `applyPlan`'s own validation) wrote nothing and must not appear --
       * "listing only the nodes it actually wrote."
       */
      _writeApplyPlan(plan, discovered) {
        const byPathId = new Map(discovered.map((d) => [d.pathId, d.node]))
        const changedEntries = []
        for (const matchedNode of plan.matched) {
          const liveNode = byPathId.get(matchedNode.id)
          if (!liveNode) continue
          const widgetsByName = new Map((liveNode.widgets || []).map((w) => [w.name, w]))
          const writtenNames = []
          for (const write of matchedNode.writes) {
            const widget = widgetsByName.get(write.name)
            if (!widget) continue
            widget.value = write.value
            widget.callback?.(write.value, app.canvas, liveNode)
            writtenNames.push(write.name)
          }
          if (writtenNames.length) {
            changedEntries.push({
              node: liveNode,
              pathId: matchedNode.id,
              class: matchedNode.class,
              widgets: writtenNames
            })
          }
        }
        this.setDirtyCanvas(true, true)
        api.announceWidgetsChangedExternally(changedEntries)
      }

      _onDeleteClick() {
        const button = this._w.deleteBtn
        if (!button) return
        if (!button._armed) {
          this._selectedStateEntry()
          button._armed = true
          button.textContent = LABEL_DELETE_CONFIRM
          this._armDeleteButtonColor(button)
          clearTimeout(button._armTimer)
          button._armTimer = setTimeout(() => this._disarmDeleteButton(), DELETE_CONFIRM_MS)
          this.setDirtyCanvas(true, false)
          return
        }
        this._disarmDeleteButton()
        const entry = this._selectedStateEntry()
        if (!entry) {
          this._toast('warn', NODE_TITLE, 'Pick a saved state first.')
          return
        }
        this._runAction(LABEL_DELETE, () => this._doDelete(entry))
      }

      _disarmDeleteButton() {
        const button = this._w.deleteBtn
        if (!button || !button._armed) return
        clearTimeout(button._armTimer)
        button._armed = false
        button.textContent = LABEL_DELETE
        this._disarmDeleteButtonColor(button)
        button.disabled = !this._selectedStateEntry()
        this.setDirtyCanvas(true, false)
      }

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

      _nextProvisionalSlug() {
        return `pending-${Date.now().toString(36)}-${this._saveProvisionalSeq++}`
      }

      /**
       * Save State's optimistic half (controller.js's v0.82
       * `_beginOptimisticCreate()` shape): invents a provisional slug,
       * marks it in-flight (`_saveInFlightSlugs` -- guards against
       * `_applyStatesResponse()` dropping the row before the real slug is
       * known, see that method), inserts + sorts + paints + selects +
       * clears the name field, ALL BEFORE the create POST is even sent.
       */
      _beginOptimisticCreate(name, count) {
        const provisionalSlug = this._nextProvisionalSlug()
        const previousSelectedSlug = this._selectedSlug
        this._saveInFlightSlugs.add(provisionalSlug)
        const entry = { slug: provisionalSlug, name, count, label: `${name} (saving…)` }
        this._statesCache = [...this._statesCache, entry].sort(compareStateEntries)
        this._renderStateList()
        this._selectEntry(entry, { loadName: false })
        this._clearNameField()
        this._toast('info', NODE_TITLE, `Saving "${name}"…`)
        return { provisionalSlug, previousSelectedSlug }
      }

      _rollbackOptimisticCreate(provisionalSlug, previousSelectedSlug, name, error) {
        this._statesCache = this._statesCache.filter((s) => s.slug !== provisionalSlug)
        const previous = previousSelectedSlug
          ? this._statesCache.find((s) => s.slug === previousSelectedSlug)
          : null
        if (previous) {
          this._selectEntry(previous, { loadName: false })
        } else {
          this._selectedSlug = null
          this._setSetValueSilently('')
        }
        this._renderStateList()
        if (this._w.name) {
          this._w.name.value = name
          try {
            this._w.name.callback?.(name)
          } catch (callbackError) {
            api.warn(`${NODE_TITLE}: name widget callback threw`, callbackError)
          }
          this.setDirtyCanvas(true, true)
        }
        this._toast('error', NODE_TITLE, `Could not save "${name}": ${error?.message || error}`)
      }

      /**
       * Save State's optimistic half (controller.js's `_beginOptimisticUpdate()`
       * shape): unlike a create, an update's slug never changes, so there is
       * no provisional slug to invent -- `entry.slug` ITSELF is marked
       * in-flight, in the SAME `_saveInFlightSlugs` set New State's
       * provisional row uses. That is what stops a stale poll snapshot
       * (captured before this update's write lands) from regressing the
       * name/count painted here back to what the file still says on disk --
       * `_applyStatesResponse()`'s `localBySlug` guard already covers any
       * slug in this set, create or update alike. `_statesCache` entries
       * are always REPLACED, never mutated in place, matching every other
       * write in this file. Re-sorts (a rename can move the row), repaints,
       * keeps the SAME row selected with `loadName: false` -- the name
       * field already holds whatever the user is mid-typing (a rename, or
       * nothing), and overwriting it here would fight that -- and toasts
       * "Saving…". Returns the OLD entry for
       * `_rollbackOptimisticUpdate()`'s failure path.
       */
      _beginOptimisticUpdate(entry, newName, count) {
        this._saveInFlightSlugs.add(entry.slug)
        const name = newName ?? entry.name
        const updated = {
          slug: entry.slug,
          name,
          count,
          captured: entry.captured,
          label: name || entry.slug
        }
        this._statesCache = this._statesCache
          .map((s) => (s.slug === entry.slug ? updated : s))
          .sort(compareStateEntries)
        this._renderStateList()
        this._selectEntry(updated, { loadName: false })
        this._toast('info', NODE_TITLE, `Saving "${name}"…`)
        return entry
      }

      /** `_beginOptimisticUpdate()`'s failure half: puts the row back
       * EXACTLY as it read before -- the same "restore exactly" rule
       * `_rollbackOptimisticCreate()`/`_doDelete()`'s own rollback use --
       * and toasts loudly. */
      _rollbackOptimisticUpdate(previous, error) {
        this._statesCache = this._statesCache
          .map((s) => (s.slug === previous.slug ? previous : s))
          .sort(compareStateEntries)
        this._selectEntry(previous, { loadName: false })
        this._renderStateList()
        this._toast('error', NODE_TITLE, `Could not save "${previous.name}": ${error?.message || error}`)
      }

      /**
       * Capture: registry -> live discovery -> read every declared
       * widget's `.value` -> `buildStatePayload()` (pure: filters
       * excluded/non-state-bearing, validates per kind) -> optimistic
       * paint -> POST -> reconcile from the response's own listing, or
       * roll back loudly on failure.
       */
      async _doCapture() {
        const registry = await fetchStateRegistry()
        if (this._removed) return
        this._registry = registry
        const name = (this._w.name?.value || '').trim() || `State ${this._statesCache.length + 1}`
        const exclusions = this._exclusions()
        const discovered = discoverStateNodes(registry)
        const nodesInfo = discovered.map((d) => ({
          pathId: d.pathId,
          class: d.class,
          title: d.title,
          widgetValues: readWidgetValues(d.node, Object.keys(registry.classes[d.class]?.widgets || {}))
        }))
        const { nodes, warnings } = buildStatePayload(nodesInfo, registry, exclusions)
        for (const warning of warnings) api.warn(`${NODE_TITLE}: capture: ${warning}`)

        const { provisionalSlug, previousSelectedSlug } = this._beginOptimisticCreate(name, nodes.length)
        try {
          const state = { format: 1, name, notes: '', captured: new Date().toISOString(), nodes }
          const response = await api.postJson(STATE_ROUTE, { state })
          this._applyStatesResponse(response)
          announceStatesChanged()
          this._selectStateBySlug(response.slug)
          this._clearNameField()
          const saved = this._statesCache.find((s) => s.slug === response.slug)
          this._toast('success', NODE_TITLE, summarizeCapture(saved?.name ?? name, nodes, registry))
          if (Array.isArray(response.foreign) && response.foreign.length) {
            this._toast('warn', NODE_TITLE, `Captured with warnings: ${response.foreign.join('; ')}`)
          }
        } catch (error) {
          this._rollbackOptimisticCreate(provisionalSlug, previousSelectedSlug, name, error)
        } finally {
          this._saveInFlightSlugs.delete(provisionalSlug)
        }
      }

      /**
       * Save State: overwrite the SELECTED state in place -- same slug, a
       * FRESH capture of every included node's current widget values, a
       * new `captured` timestamp, and (only when `_saveAsNewName()` says
       * the name field was edited) a renamed `name`. Same discovery/
       * exclusion/validation pipeline `_doCapture()` uses just above --
       * Save IS a re-capture, aimed at an existing file instead of a new
       * one -- and controller.js's `_doUpdate()` optimistic-then-POST
       * posture: `_beginOptimisticUpdate()` paints before the network
       * call, `_rollbackOptimisticUpdate()` restores the row exactly on
       * failure, `_saveInFlightSlugs` guards the slug the same way New
       * State's provisional slug guards a create.
       *
       * The backend already supports this: `POST
       * /lora_library/universal_state` accepts an optional `slug`, and
       * `universal_states_store.save_state()` uses a caller-supplied slug
       * AS-IS (no `_unique_slug` de-duplication -- that call only runs
       * when `slug is None`) -- passing the SELECTED state's own slug back
       * is what turns this POST from "mint `<slug>-2`" into "overwrite
       * this file," with no backend change needed (verified by reading
       * `lora_library/routes_universal_states.py` and
       * `universal_states_store.save_state()` directly).
       */
      async _doUpdate() {
        const entry = this._selectedStateEntry()
        if (!entry) {
          this._toast('warn', NODE_TITLE, 'Pick a saved state first.')
          return
        }
        const newName = this._saveAsNewName(entry)
        const registry = await fetchStateRegistry()
        if (this._removed) return
        this._registry = registry
        const exclusions = this._exclusions()
        const discovered = discoverStateNodes(registry)
        const nodesInfo = discovered.map((d) => ({
          pathId: d.pathId,
          class: d.class,
          title: d.title,
          widgetValues: readWidgetValues(d.node, Object.keys(registry.classes[d.class]?.widgets || {}))
        }))
        const { nodes, warnings } = buildStatePayload(nodesInfo, registry, exclusions)
        for (const warning of warnings) api.warn(`${NODE_TITLE}: save: ${warning}`)

        const savedSlug = entry.slug
        const previous = this._beginOptimisticUpdate(entry, newName, nodes.length)
        try {
          // Preserve the existing notes; only the widgets (and, optionally,
          // the name) change on Save -- best-effort GET, falls back to a
          // bare rewrite (notes reset) if the existing file can't be read
          // for some reason -- controller.js's `_doUpdate()` identical
          // trigger_words/notes fallback.
          let name = entry.name
          let notes = ''
          try {
            const existing = await api.getJson(STATE_ROUTE, { slug: savedSlug })
            name = existing.name ?? name
            notes = existing.notes ?? ''
          } catch (error) {
            api.warn(
              `${NODE_TITLE}: could not read the existing state before saving; overwriting anyway`,
              error
            )
          }
          const state = {
            format: 1,
            name: newName ?? name,
            notes,
            captured: new Date().toISOString(),
            nodes
          }
          const response = await api.postJson(STATE_ROUTE, { slug: savedSlug, state })
          this._applyStatesResponse(response)
          announceStatesChanged()
          this._selectStateBySlug(savedSlug)
          const saved = this._statesCache.find((s) => s.slug === savedSlug)
          this._toast(
            'success',
            NODE_TITLE,
            summarizeUpdate(saved?.name ?? (newName ?? name), nodes, registry, { renamed: !!newName })
          )
          if (Array.isArray(response.foreign) && response.foreign.length) {
            this._toast('warn', NODE_TITLE, `Saved with warnings: ${response.foreign.join('; ')}`)
          }
        } catch (error) {
          this._rollbackOptimisticUpdate(previous, error)
        } finally {
          this._saveInFlightSlugs.delete(savedSlug)
        }
      }

      /**
       * Delete: remove the row from the pane THE INSTANT the confirm click
       * lands (before the POST even starts), toast "Deleting…", then
       * reconcile on success or restore the row EXACTLY where it was and
       * toast loudly on failure -- controller.js's v0.82 `_doDelete()`
       * shape, `_deleteInFlightSlugs` guarding a stale poll response from
       * resurrecting the row mid-flight.
       */
      async _doDelete(entry) {
        const previousCache = this._statesCache
        this._deleteInFlightSlugs.add(entry.slug)
        this._statesCache = this._statesCache.filter((s) => s.slug !== entry.slug)
        this._renderStateList()
        this._toast('info', NODE_TITLE, `Deleting "${entry.name}"…`)
        try {
          const response = await api.postJson(STATE_DELETE_ROUTE, { slug: entry.slug })
          this._applyStatesResponse(response)
          announceStatesChanged()
          const nextEntry = this._statesCache[0] || null
          if (nextEntry) {
            this._selectEntry(nextEntry)
          } else {
            this._selectedSlug = null
            this._setSetValueSilently('')
            this._clearNameField()
          }
        } catch (error) {
          this._statesCache = previousCache
          this._renderStateList()
          this._toast('error', NODE_TITLE, `Could not delete "${entry.name}": ${error?.message || error}`)
        } finally {
          this._deleteInFlightSlugs.delete(entry.slug)
        }
      }

      _onStatePicked(label) {
        const entry = this._statesCache.find((s) => s.label === label)
        if (!entry) return
        this._selectEntry(entry)
      }

      _selectEntry(entry, { loadName = true } = {}) {
        this._selectedSlug = entry.slug
        this._setSetValueSilently(entry.label)
        if (loadName && this._w.name) this._w.name.value = entry.name || entry.slug || ''
        this._disarmDeleteButton()
        this.setDirtyCanvas(true, false)
      }

      _setSetValueSilently(label) {
        if (!this._w.set) return
        this._w.set.value = label
        this._renderStateList() // small lists: a full repaint is cheap enough
      }

      /** Label-first, slug-fallback resolution -- controller.js's
       * `_selectedSetEntry()` twin (a poll rebuilding `_statesCache` with a
       * different dedup-suffixed label must not read as "nothing selected"). */
      _selectedStateEntry() {
        const value = this._w.set?.value
        let entry = this._statesCache.find((s) => s.label === value)
        if (entry) {
          this._selectedSlug = entry.slug
          return entry
        }
        if (this._selectedSlug) {
          entry = this._statesCache.find((s) => s.slug === this._selectedSlug)
          if (entry) return entry
        }
        return null
      }

      _selectStateBySlug(slug) {
        const entry = this._statesCache.find((s) => s.slug === slug)
        if (entry) this._selectEntry(entry)
      }

      /**
       * Reconcile `_statesCache` (always present) and `_layoutCache` (ONLY
       * on the `GET /lora_library/universal_states` listing response --
       * verified against the concurrent backend's actual route handlers:
       * the create/delete POST responses carry `{ok, states, ...}` alone,
       * no `layout` key at all). Blindly normalizing a MISSING `layout`
       * into the empty default and writing it here would silently wipe
       * every real group after the very next Save/Delete -- exactly the
       * v0.68.1 data-loss class of bug controller.js's own history warns
       * about, so this method only ever touches `_layoutCache` when the
       * response actually carries a `layout` object.
       *
       * In-flight guards mirror controller.js's v0.82 pattern exactly: a
       * slug being deleted is filtered OUT of whatever this response says;
       * a slug being created/updated is served from the LOCAL optimistic
       * copy instead of a stale remote snapshot, then re-sorted since a
       * carried-forward row was never placed by the server's own order.
       */
      _applyStatesResponse(data) {
        if (this._removed) return
        const list = Array.isArray(data?.states) ? data.states : []
        const seenLabels = new Set()
        const previousCache = this._statesCache
        const localBySlug = new Map(
          previousCache.filter((s) => this._saveInFlightSlugs.has(s.slug)).map((s) => [s.slug, s])
        )
        const remoteSlugs = new Set(list.map((s) => s.slug))
        const built = list
          .filter((s) => !this._deleteInFlightSlugs.has(s.slug))
          .map((s) => {
            const local = localBySlug.get(s.slug)
            if (local) return local
            let label = s.name || s.slug
            if (seenLabels.has(label)) label = `${label} (${s.slug})`
            seenLabels.add(label)
            return { slug: s.slug, name: s.name, count: s.count, captured: s.captured, label }
          })
        let carriedProvisional = false
        for (const entry of previousCache) {
          if (this._saveInFlightSlugs.has(entry.slug) && !remoteSlugs.has(entry.slug)) {
            built.push(entry)
            carriedProvisional = true
          }
        }
        this._statesCache = carriedProvisional ? built.sort(compareStateEntries) : built

        if (data && typeof data.layout === 'object' && data.layout !== null) {
          this._layoutLoaded = true
          const nextLayout = normalizeLayoutClient(data.layout)
          if (JSON.stringify(nextLayout) !== JSON.stringify(this._layoutCache)) {
            this._layoutCache = nextLayout
          }
        }

        const signature = JSON.stringify({ states: this._statesCache, layout: this._layoutCache })
        if (signature === this._statesSignature) return
        this._statesSignature = signature
        this._renderStateList()
        this.setDirtyCanvas(true, false)
      }

      // ---------------------------------------------------- Included nodes page

      /** Page-open ALWAYS repaints (`force: true` from `_setActivePage`);
       * a background poller tick is change-gated on a signature of the
       * discovered nodes + the current exclusions, so it never tears the
       * list down for nothing. */
      async _refreshNodesPage({ force = false } = {}) {
        if (this._removed) return
        const registry = this._registry || (await fetchStateRegistry())
        if (this._removed) return
        this._registry = registry
        const discovered = discoverStateNodes(registry)
        const signature = JSON.stringify({
          exclusions: this._exclusions(),
          keys: discovered.map((d) => `${d.class}|${d.pathId}|${d.title}`)
        })
        if (!force && signature === this._nodesPageSignature) return
        this._nodesPageSignature = signature
        this._includedNodesCache = discovered
        this._renderNodesPage(discovered, registry)
      }

      _renderNodesPage(discovered, registry) {
        const listEl = this._pane?.nodesListEl
        if (!listEl || this._removed) return
        listEl.replaceChildren()
        if (!discovered.length) {
          listEl.append(
            el('div', { className: 'lusc-empty', text: 'No state-bearing nodes in this workflow.' })
          )
          return
        }
        const byClass = new Map()
        for (const d of discovered) {
          if (!byClass.has(d.class)) byClass.set(d.class, [])
          byClass.get(d.class).push(d)
        }
        const exclusions = this._exclusions()
        for (const [klass, nodes] of byClass) {
          const display = registry.classes[klass]?.display || klass
          const pathIds = nodes.map((n) => n.pathId)
          const state = classToggleState(exclusions, klass, pathIds)
          const master = el('input', { attrs: { type: 'checkbox' } })
          master.checked = state === true
          master.indeterminate = state === null
          master.addEventListener('change', () => {
            const action = { type: 'class', klass, pathIds }
            this._writeExclusions(exclusionsAfterToggle(this._exclusions(), action))
          })
          listEl.append(
            el('div', { className: 'lusc-class-header' }, [
              master,
              el('span', { className: 'lusc-class-label', text: `${display} (${nodes.length})` })
            ])
          )
          const classExcluded = exclusions.classes[klass] === false
          for (const d of nodes) {
            const included = exclusions.nodes[d.pathId] !== false
            const checkbox = el('input', { attrs: { type: 'checkbox' } })
            checkbox.checked = included
            checkbox.disabled = classExcluded
            checkbox.addEventListener('change', () => {
              this._writeExclusions(
                exclusionsAfterToggle(this._exclusions(), {
                  type: 'node',
                  pathId: d.pathId,
                  included: checkbox.checked
                })
              )
            })
            const dimmed = classExcluded || !included
            listEl.append(
              el(
                'div',
                { className: 'lusc-node-row' + (dimmed ? ' lusc-node-row-excluded' : '') },
                [
                  checkbox,
                  el('span', { className: 'lusc-node-title', text: d.title }),
                  el('span', { className: 'lusc-node-path', text: `#${d.pathId}` })
                ]
              )
            )
          }
        }
      }
    }

    LiteGraph.registerNodeType(NODE_TYPE, EPSUniversalStateControllerNode)
    EPSUniversalStateControllerNode.category = NODE_CATEGORY
  } catch (error) {
    api.warn('registerControllerNode failed', error)
  }
}

/** `CSS.escape` isn't defined in every JS host this file might run under
 * (Node test probes never call this path, but this local wrapper keeps the
 * degrade explicit rather than throwing if a future host lacks it). */
function cssEscape(value) {
  return typeof CSS !== 'undefined' && typeof CSS.escape === 'function'
    ? CSS.escape(value)
    : String(value).replace(/["\\]/g, '\\$&')
}
