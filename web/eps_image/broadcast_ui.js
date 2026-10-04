/**
 * @file EPS Run Multiplier "broadcast" -- the DOM pieces (FORMAT.md §6.10
 * "Broadcast (v1)"): the compact "📡 Broadcast" row that lives INSIDE the
 * existing run-count readout element, the ⋯ options popover, and the
 * "Wire now" preview dialog. No graph logic here -- every action is a
 * callback `broadcast.js` supplies -- so the pure helpers below (skip
 * grouping, the tick -> apply / leave-alone split, the row text) are
 * unit-testable under Node and the DOM builders stay thin.
 *
 * **Nodes 2.0 rules (research/nodes-2.0/frontend-contract.md §2.5, §3, §5),
 * all honoured here:**
 *  - No canvas drawing and no node mouse hooks: the row is plain DOM inside
 *    the readout's DOM widget (`hideInPanel` is set where that widget is
 *    created, cross_sweep.js), so it is the same element in both renderers.
 *    (The tucked-wire drawing is LINK-level and lives in broadcast_draw.js.)
 *  - The popover and the dialog are portaled to `document.body`, positioned
 *    with `getBoundingClientRect()` (popover) or centred (dialog), at a high
 *    z-index, and dismissed on Esc / an outside pointerdown. Never
 *    `position: fixed` inside the node subtree -- the Vue node pane is
 *    CSS-transformed and `contain: layout style`, which breaks fixed popups.
 *  - Bare keys fire GLOBAL commands from any non-text element, so while the
 *    dialog/popover is open a capture-phase `keydown` listener on `document`
 *    stops propagation of every key (Esc closes) -- the dialog is modal.
 *  - Scrollable regions carry `data-capture-wheel="true"` and are focusable,
 *    so a wheel scrolls the list instead of zooming the canvas.
 * No polling, no timers: everything here is event driven.
 */

import {
  BROADCAST_OUTPUTS,
  KINDS,
  SKIP_CODES,
  describeProposal
} from './broadcast_plan.js'

const STYLE_TAG_ID = 'eps-broadcast-styles'

/** The row's height inside the readout box (px). cross_sweep.js's sizing adds
 * it to every height it reports (`state.extraHeight`). */
export const BROADCAST_ROW_HEIGHT = 24

/** Above the node pane / canvas overlays, below nothing of ours. Core's own
 * popups use 3000 (frontend-contract §3.5). */
const Z_POPOVER = 3000
const Z_DIALOG = 3100

// ---------------------------------------------------------------------------
// Pure helpers (exported for tests/test_broadcast_ui_js.py)
// ---------------------------------------------------------------------------

/** Skip codes in the order the dialog lists them, with their headings. The
 * target-less per-output codes are reported as one-line notes instead. */
export const SKIP_SECTIONS = Object.freeze([
  [SKIP_CODES.LOOP, 'Would create a loop'],
  [SKIP_CODES.NEGATIVE_GUARD, 'Feeds a negative prompt (text is never wired into it)'],
  [SKIP_CODES.WAN_LOW, 'WAN low-noise sampler (gets model_low, not model)'],
  [SKIP_CODES.WAN_AMBIGUOUS, 'WAN: more than one empty MODEL input'],
  [SKIP_CODES.OTHER_MULTIPLIER, 'Another EPS Run Multiplier'],
  [SKIP_CODES.SUBGRAPH_SHARED, 'Subgraph used elsewhere — wire by hand'],
  [SKIP_CODES.SUBGRAPH_MIXED, 'Subgraph input also feeds something else — wire by hand'],
  [SKIP_CODES.SUBGRAPH_TOO_DEEP, 'Subgraphs nested too deeply'],
  [SKIP_CODES.SCOPE_PARTIAL, 'Subgraph used inside and outside the group — wire by hand'],
  [SKIP_CODES.UNKNOWN_REQUIRED, "Can't tell whether the input is required"],
  [SKIP_CODES.OPTIONAL, 'Optional input (never filled automatically)'],
  [SKIP_CODES.DOUBLE_CLAIM, 'Already fed by another output'],
  [SKIP_CODES.ALREADY_WIRED, 'Already wired']
])

const NOTE_CODES = new Set([
  SKIP_CODES.OUTPUT_OFF,
  SKIP_CODES.SETTING_OFF,
  SKIP_CODES.OUTPUT_DEAD,
  SKIP_CODES.WAN_UNRESOLVED,
  SKIP_CODES.NO_GROUP
])

/**
 * Splits a plan's skips for display: `notes` (output-level reasons, one line
 * each), `leftAlone` (rows the user can tick to re-include) and `groups`
 * (everything else, by reason, in `SKIP_SECTIONS` order, each with its
 * count). Pure.
 * @param {object[]} skips
 * @returns {{notes: object[], leftAlone: object[], groups: Array<{code: string, label: string, items: object[]}>}}
 */
export function groupSkips(skips) {
  const list = Array.isArray(skips) ? skips : []
  const notes = list.filter((skip) => NOTE_CODES.has(skip.code))
  const leftAlone = list.filter((skip) => skip.code === SKIP_CODES.LEFT_ALONE)
  const groups = []
  for (const [code, label] of SKIP_SECTIONS) {
    const items = list.filter((skip) => skip.code === code)
    if (items.length) groups.push({ code, label, items })
  }
  return { notes, leftAlone, groups }
}

/**
 * What the dialog's ticks mean (FORMAT.md §6.10): ticked proposals are
 * applied; UNTICKED ones go to the multiplier's "leave alone" list so Keep
 * wired never fights the choice; a previously left-alone row the user TICKED
 * is applied and removed from that list. Pure.
 * @param {{proposals: object[], skips: object[]}} plan
 * @param {Iterable<string>} tickedKeys
 * @returns {{toApply: object[], leaveAlone: string[], include: string[]}}
 */
export function selectionOutcome(plan, tickedKeys) {
  const ticked = new Set(tickedKeys)
  const proposals = plan?.proposals || []
  const toApply = proposals.filter((p) => ticked.has(p.key))
  const leaveAlone = proposals.filter((p) => !ticked.has(p.key)).map((p) => p.key)
  const include = []
  for (const skip of plan?.skips || []) {
    if (skip.code !== SKIP_CODES.LEFT_ALONE || !skip.proposal) continue
    if (ticked.has(skip.key)) {
      toApply.push(skip.proposal)
      include.push(skip.key)
    }
  }
  return { toApply, leaveAlone, include }
}

/** Proposals grouped by output name, in output order. Pure. */
export function groupByOutput(proposals) {
  const order = BROADCAST_OUTPUTS.map((spec) => spec.name)
  const map = new Map()
  for (const proposal of proposals || []) {
    map.set(proposal.output, [...(map.get(proposal.output) || []), proposal])
  }
  return order.filter((name) => map.has(name)).map((name) => ({ output: name, items: map.get(name) }))
}

/**
 * The row's text: which outputs are broadcasting (enabled AND live) and how
 * many wires are recorded. `live` = names of enabled+live outputs; `wired` =
 * recorded entries that are connected; `paused` = withdrawn entries; `scope` =
 * 'group' adds "group only" (Reach, FORMAT.md §6.10) -- the config, not a
 * membership check, so the text never goes stale when a node is dragged.
 * @param {{live: string[], wired: number, paused: number, keep: boolean, scope?: string}} info
 */
export function rowSummary(info) {
  const names = info.live.length ? info.live.join(' · ') : 'nothing live'
  const parts = [names]
  if (info.wired > 0) parts.push(`${info.wired} wired`)
  if (info.paused > 0) parts.push(`${info.paused} paused`)
  if (info.keep) parts.push('keep ✓')
  if (info.scope === 'group') parts.push('group only')
  return parts.join(' · ')
}

/** Choices of the ⋯ popover's "Wires" and "Reach" selects: `[value, label]`,
 * the stored default first (FORMAT.md §6.10 "Tucked wires" / "Reach"). */
export const LOOK_CHOICES = Object.freeze([
  ['tucked', 'Tucked (default)'],
  ['dim', 'Dim'],
  ['normal', 'Normal']
])
export const SCOPE_CHOICES = Object.freeze([
  ['graph', 'Whole workflow (default)'],
  ['group', 'Only my group']
])

/** The one line the preview shows under its lead when Reach is "only my
 * group" and the multiplier IS in a group; '' otherwise. Pure. */
export function reachNote(scope) {
  if (!scope || scope.mode !== 'group' || !scope.inGroup) return ''
  const names = (scope.groups || []).map((g) => `“${g.title || 'untitled group'}”`).join(', ')
  return `Reach: only my group (${names}) — inputs outside it are not considered.`
}

/** "KSampler #31 (model, vae); Save Image #22 (save_prefix)" for a toast,
 * capped at three targets. Pure. */
export function wiredToastText(applied, proposalsByKey) {
  const byTarget = new Map()
  for (const entry of applied) {
    const proposal = proposalsByKey.get(entry.key)
    if (!proposal) continue
    const label = `${proposal.targetTitle || 'node'} #${proposal.targetPathId}`
    byTarget.set(label, [...(byTarget.get(label) || []), proposal.output])
  }
  const parts = [...byTarget.entries()].map(([label, outs]) => `${label} (${outs.join(', ')})`)
  const shown = parts.slice(0, 3).join('; ')
  return parts.length > 3 ? `${shown}; +${parts.length - 3} more` : shown
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

let stylesInjected = false

const CSS_TEXT = `
.eps-rc-root.eps-rc-has-bc { flex-direction: column; align-items: stretch; justify-content: flex-start; }
.eps-rc-root.eps-rc-has-bc .eps-rc-line { flex: 0 0 auto; }
.eps-bc-row { flex: 0 0 ${BROADCAST_ROW_HEIGHT}px; height: ${BROADCAST_ROW_HEIGHT}px; box-sizing: border-box; display: flex; align-items: center; gap: 4px; padding: 0 4px; font-size: 11px; color: var(--descrip-text, #999); min-width: 0; }
.eps-bc-label { flex: 0 0 auto; white-space: nowrap; }
.eps-bc-summary { flex: 1 1 auto; min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.eps-bc-btn { flex: 0 0 auto; font: inherit; font-size: 11px; line-height: 1; padding: 3px 7px; border-radius: 4px; border: 1px solid var(--border-color, #4e4e4e); background: var(--comfy-input-bg, #2a2a2a); color: var(--input-text, #ddd); cursor: pointer; }
.eps-bc-btn:hover { background: var(--comfy-menu-bg, #353535); }
.eps-bc-btn:focus-visible { outline: 2px solid var(--p-primary-color, #6aa0ff); outline-offset: 1px; }
.eps-bc-btn[disabled] { opacity: 0.5; cursor: default; }
.eps-bc-btn.primary { background: var(--p-primary-color, #3b82f6); border-color: transparent; color: #fff; }
.eps-bc-pop { position: fixed; z-index: ${Z_POPOVER}; min-width: 280px; max-width: 360px; max-height: calc(100vh - 16px); overflow-y: auto; box-sizing: border-box; padding: 10px; border-radius: 8px; border: 1px solid var(--border-color, #4e4e4e); background: var(--comfy-menu-bg, #202020); color: var(--input-text, #ddd); font-size: 12px; box-shadow: 0 8px 24px rgba(0,0,0,0.45); }
.eps-bc-pop h3 { margin: 0 0 6px; font-size: 12px; font-weight: 600; }
.eps-bc-pop .eps-bc-check { display: flex; align-items: center; gap: 6px; padding: 2px 0; cursor: pointer; }
.eps-bc-pop .eps-bc-check input { margin: 0; }
.eps-bc-badge { margin-left: auto; font-size: 10px; padding: 0 5px; border-radius: 8px; border: 1px solid var(--border-color, #4e4e4e); opacity: 0.85; }
.eps-bc-badge.dead { color: var(--warning-text, #e6a23c); border-color: var(--warning-text, #e6a23c); }
.eps-bc-field { display: flex; align-items: center; gap: 8px; padding: 2px 0; }
.eps-bc-field > span { flex: 0 0 52px; font-weight: 600; }
.eps-bc-select { flex: 1 1 auto; min-width: 0; font: inherit; font-size: 12px; padding: 2px 4px; border-radius: 4px; border: 1px solid var(--border-color, #4e4e4e); background: var(--comfy-input-bg, #2a2a2a); color: var(--input-text, #ddd); }
.eps-bc-hint { margin: 2px 0 6px; opacity: 0.7; font-size: 11px; line-height: 1.35; }
.eps-bc-sep { height: 1px; background: var(--border-color, #4e4e4e); margin: 8px 0; opacity: 0.6; }
.eps-bc-actions { display: flex; flex-wrap: wrap; gap: 6px; }
.eps-bc-overlay { position: fixed; inset: 0; z-index: ${Z_DIALOG}; display: flex; align-items: center; justify-content: center; background: rgba(0,0,0,0.5); }
.eps-bc-dialog { display: flex; flex-direction: column; width: min(760px, 94vw); max-height: 86vh; box-sizing: border-box; padding: 16px; border-radius: 10px; border: 1px solid var(--border-color, #4e4e4e); background: var(--comfy-menu-bg, #202020); color: var(--input-text, #ddd); font-size: 13px; box-shadow: 0 16px 48px rgba(0,0,0,0.55); }
.eps-bc-dialog:focus { outline: none; }
.eps-bc-dialog h2 { margin: 0 0 4px; font-size: 15px; font-weight: 600; }
.eps-bc-lead { margin: 0 0 10px; opacity: 0.8; line-height: 1.4; }
.eps-bc-body { flex: 1 1 auto; min-height: 60px; overflow: auto; padding-right: 4px; }
.eps-bc-body:focus { outline: none; }
.eps-bc-group { margin: 0 0 12px; }
.eps-bc-group-head { display: flex; align-items: center; gap: 8px; margin: 0 0 4px; font-weight: 600; }
.eps-bc-item { display: flex; align-items: flex-start; gap: 8px; padding: 3px 0; line-height: 1.35; }
.eps-bc-item input { margin: 3px 0 0; flex: 0 0 auto; }
.eps-bc-item .nested { opacity: 0.85; font-size: 11px; display: block; }
.eps-bc-item .warn { color: var(--warning-text, #e6a23c); font-size: 11px; display: block; }
.eps-bc-skipped summary { cursor: pointer; opacity: 0.85; padding: 2px 0; }
.eps-bc-skipped ul { margin: 2px 0 6px 18px; padding: 0; font-size: 12px; opacity: 0.85; }
.eps-bc-note { margin: 2px 0; font-size: 12px; opacity: 0.8; }
.eps-bc-footer { display: flex; align-items: center; gap: 8px; margin-top: 12px; }
.eps-bc-footer .spacer { flex: 1 1 auto; }
`

function injectStyles() {
  if (stylesInjected) return
  stylesInjected = true
  if (document.getElementById(STYLE_TAG_ID)) return
  const style = document.createElement('style')
  style.id = STYLE_TAG_ID
  style.textContent = CSS_TEXT
  document.head.appendChild(style)
}

// ---------------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------------

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag)
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue
    if (key === 'class') node.className = value
    else if (key === 'text') node.textContent = value
    else if (key === 'title') node.title = value
    else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value)
    else node.setAttribute(key, value === true ? '' : String(value))
  }
  for (const child of Array.isArray(children) ? children : [children]) {
    if (child === null || child === undefined || child === false) continue
    node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child)
  }
  return node
}

/** While a modal piece is open: swallow every keydown at `document` capture
 * (Esc closes it) so bare keys never reach ComfyUI's global shortcuts.
 * Returns the remover. */
function installModalKeys(onEscape) {
  const handler = (event) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      onEscape()
    }
    event.stopPropagation()
  }
  document.addEventListener('keydown', handler, true)
  return () => document.removeEventListener('keydown', handler, true)
}

// ---------------------------------------------------------------------------
// The row inside the readout element
// ---------------------------------------------------------------------------

/**
 * Builds the compact row. `onWire` / `onMore(anchorEl)` are supplied by
 * broadcast.js. The row is NOT a widget: it is appended inside the readout's
 * existing DOM element (the positional `widgets_values` contract stays
 * untouched -- nothing here is serialised).
 * @returns {{rowEl: HTMLElement, summaryEl: HTMLElement, wireBtn: HTMLElement, moreBtn: HTMLElement}}
 */
export function buildBroadcastRow({ onWire, onMore }) {
  injectStyles()
  const summaryEl = el('span', { class: 'eps-bc-summary' })
  const wireBtn = el('button', {
    class: 'eps-bc-btn',
    type: 'button',
    text: 'Wire now',
    title: 'Preview and connect this multiplier’s live outputs to matching empty inputs',
    onclick: (event) => {
      event.stopPropagation()
      onWire()
    }
  })
  const moreBtn = el('button', {
    class: 'eps-bc-btn',
    type: 'button',
    text: '⋯',
    title: 'Broadcast options',
    'aria-haspopup': 'true',
    onclick: (event) => {
      event.stopPropagation()
      onMore(moreBtn)
    }
  })
  const rowEl = el('div', { class: 'eps-bc-row' }, [
    el('span', { class: 'eps-bc-label', text: '📡 Broadcast' }),
    summaryEl,
    wireBtn,
    moreBtn
  ])
  // A click inside a DOM widget must not start a node drag / canvas gesture.
  for (const type of ['pointerdown', 'mousedown', 'dblclick', 'contextmenu']) {
    rowEl.addEventListener(type, (event) => event.stopPropagation())
  }
  return { rowEl, summaryEl, wireBtn, moreBtn }
}

// ---------------------------------------------------------------------------
// The ⋯ popover
// ---------------------------------------------------------------------------

let openPopoverClose = null
let openPopoverAnchor = null

/** Closes the open popover, if any. */
export function closePopover() {
  if (openPopoverClose) openPopoverClose()
}

/** Whether the popover is open for *anchorEl* (the ⋯ button toggles it). */
export function isPopoverOpenFor(anchorEl) {
  return openPopoverClose !== null && openPopoverAnchor === anchorEl
}

/**
 * Opens the options popover anchored under *anchorEl*.
 * @param {{
 *   anchorEl: HTMLElement,
 *   outputs: Array<{name: string, enabled: boolean, live: boolean, gated: boolean, settingOn: boolean}>,
 *   keep: boolean,
 *   look?: string, scope?: string,
 *   onToggleOutput: (name: string, on: boolean) => void,
 *   onToggleKeep: (on: boolean) => void,
 *   onSetLook?: (look: string) => void,
 *   onSetScope?: (scope: string) => void,
 *   onWire: () => void,
 *   onRemove: () => void
 * }} options
 */
export function openPopover(options) {
  injectStyles()
  closePopover()
  const { anchorEl } = options
  const removeKeys = installModalKeys(() => close())

  const checks = options.outputs.map((out) => {
    const box = el('input', { type: 'checkbox' })
    box.checked = out.enabled && (!out.gated || out.settingOn)
    box.disabled = out.gated && !out.settingOn
    box.addEventListener('change', () => options.onToggleOutput(out.name, box.checked))
    const badge = out.gated && !out.settingOn
      ? el('span', { class: 'eps-bc-badge', text: 'needs setting', title: 'Settings › EPSNodes › Run Multiplier broadcast' })
      : el('span', { class: out.live ? 'eps-bc-badge' : 'eps-bc-badge dead', text: out.live ? 'live' : 'not live', title: out.live ? '' : 'Wire the matching multiplier input first' })
    return el('label', { class: 'eps-bc-check' }, [box, out.name, badge])
  })

  const keepBox = el('input', { type: 'checkbox' })
  keepBox.checked = options.keep
  keepBox.addEventListener('change', () => options.onToggleKeep(keepBox.checked))

  /** A labelled `<select>` (look / reach). The value is assigned AFTER the
   * options are in, so the right one shows as chosen. */
  const choiceField = (label, choices, current, onChange, ariaLabel) => {
    const select = el(
      'select',
      { class: 'eps-bc-select', 'aria-label': ariaLabel },
      choices.map(([value, text]) => el('option', { value, text }))
    )
    select.value = current
    select.addEventListener('change', () => onChange(select.value))
    return el('label', { class: 'eps-bc-field' }, [el('span', { text: label }), select])
  }

  // `data-capture-wheel`: the popover now has enough rows to scroll on a short
  // window, and a wheel over it must scroll it, not zoom the canvas behind.
  const pop = el('div', { class: 'eps-bc-pop', role: 'dialog', 'aria-label': 'Broadcast options', tabindex: '-1', 'data-capture-wheel': 'true' }, [
    el('h3', { text: 'Broadcast these outputs' }),
    ...checks,
    el('div', { class: 'eps-bc-hint', text: 'text, image and label only connect to inputs with exactly the same name, and only when the ComfyUI setting is on (Settings › EPSNodes).' }),
    el('div', { class: 'eps-bc-sep' }),
    el('label', { class: 'eps-bc-check' }, [keepBox, 'Keep wired']),
    el('div', { class: 'eps-bc-hint', text: 'New and pasted nodes get wired as you add them. A wire you unplug stays unplugged.' }),
    el('div', { class: 'eps-bc-sep' }),
    choiceField('Wires', LOOK_CHOICES, options.look || 'tucked', (value) => options.onSetLook?.(value), 'How broadcast wires are drawn'),
    el('div', { class: 'eps-bc-hint', text: 'Tucked hides broadcast wires (a 📡 stub marks each fed input) until you select this multiplier or a node it feeds. Right-click the canvas → “Broadcast: show all wires” shows every one. Dim draws them faintly.' }),
    choiceField('Reach', SCOPE_CHOICES, options.scope || 'graph', (value) => options.onSetScope?.(value), 'Which nodes broadcast may feed'),
    el('div', { class: 'eps-bc-hint', text: '“Only my group” feeds just the nodes inside a group that contains this multiplier. Wires already made stay; Wire now previews what would be added.' }),
    el('div', { class: 'eps-bc-sep' }),
    el('div', { class: 'eps-bc-actions' }, [
      el('button', { class: 'eps-bc-btn', type: 'button', text: 'Wire now…', onclick: () => { close(); options.onWire() } }),
      el('button', { class: 'eps-bc-btn', type: 'button', text: 'Remove broadcast wires', onclick: () => { close(); options.onRemove() } })
    ])
  ])
  document.body.appendChild(pop)

  // Portaled to <body>, positioned from the anchor's rect, clamped on-screen.
  const rect = anchorEl.getBoundingClientRect()
  const width = pop.offsetWidth || 300
  const height = pop.offsetHeight || 260
  const left = Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8))
  const top = rect.bottom + 6 + height > window.innerHeight - 8 ? Math.max(8, rect.top - height - 6) : rect.bottom + 6
  pop.style.left = `${left}px`
  pop.style.top = `${top}px`

  const onOutside = (event) => {
    if (!pop.contains(event.target) && !anchorEl.contains(event.target)) close()
  }
  document.addEventListener('pointerdown', onOutside, true)
  function close() {
    if (openPopoverClose !== close) return
    openPopoverClose = null
    openPopoverAnchor = null
    document.removeEventListener('pointerdown', onOutside, true)
    removeKeys()
    pop.remove()
    anchorEl.focus?.()
  }
  openPopoverClose = close
  openPopoverAnchor = anchorEl
  pop.focus()
  return close
}

// ---------------------------------------------------------------------------
// The "Wire now" preview dialog
// ---------------------------------------------------------------------------

let openDialogClose = null

export function closeDialog() {
  if (openDialogClose) openDialogClose()
}

function proposalRow(proposal, box) {
  const nested = []
  if (proposal.kind === KINDS.EXISTING) {
    nested.push(
      el('span', {
        class: 'nested',
        text: `through the subgraph's existing "${proposal.inputName}" input → ${summariseReaches(proposal)}`
      })
    )
  } else if (proposal.kind === KINDS.NEW) {
    const names = (proposal.definitions || []).map((d) => `“${d.name || d.id}”`).join(', ')
    nested.push(
      el('span', {
        class: 'warn',
        text: proposal.reuse
          ? `uses the "${proposal.inputName}" input broadcast added to subgraph ${names} → ${summariseReaches(proposal)}`
          : `adds a "${proposal.inputName}" input to subgraph ${names} (every instance is wired) → ${summariseReaches(proposal)}`
      })
    )
  }
  const head =
    proposal.kind === KINDS.DIRECT
      ? `${proposal.targetTitle || 'node'} #${proposal.targetPathId} — ${proposal.inputName}`
      : `${proposal.targetTitle || 'subgraph'} #${proposal.targetPathId}`
  return el('label', { class: 'eps-bc-item', title: describeProposal(proposal) }, [box, el('span', {}, [head, ...nested])])
}

function summariseReaches(proposal) {
  const reaches = proposal.reaches || []
  const shown = reaches.slice(0, 3).map((r) => `${r.title || 'node'} #${r.pathId}`).join(', ')
  return reaches.length > 3 ? `${shown} (+${reaches.length - 3} more)` : shown
}

/**
 * Opens the preview. Nothing is connected until [Connect]; Esc / Cancel /
 * the backdrop close it without changes.
 * @param {{
 *   title: string,
 *   plan: {proposals: object[], skips: object[], conflicts: object[], outputs: object[], error: string|null},
 *   onConnect: (outcome: {toApply: object[], leaveAlone: string[], include: string[]}) => void
 * }} options
 */
export function openPreviewDialog(options) {
  injectStyles()
  closeDialog()
  closePopover()
  const { plan } = options
  const previousFocus = document.activeElement
  const ticked = new Set(plan.proposals.map((p) => p.key))
  const boxes = new Map() // key -> checkbox
  const grouped = groupSkips(plan.skips)

  const connectBtn = el('button', { class: 'eps-bc-btn primary', type: 'button' })
  const refresh = () => {
    const outcome = selectionOutcome(plan, ticked)
    connectBtn.textContent = outcome.toApply.length ? `Connect ${outcome.toApply.length}` : 'Nothing to connect'
    connectBtn.disabled = outcome.toApply.length === 0 && outcome.leaveAlone.length === 0
  }
  const makeBox = (key, on) => {
    const box = el('input', { type: 'checkbox' })
    box.checked = on
    box.addEventListener('change', () => {
      if (box.checked) ticked.add(key)
      else ticked.delete(key)
      refresh()
    })
    boxes.set(key, box)
    return box
  }

  const body = el('div', { class: 'eps-bc-body', tabindex: '0', 'data-capture-wheel': 'true' })
  if (plan.error) body.appendChild(el('p', { class: 'eps-bc-note', text: plan.error }))
  const reach = reachNote(plan.scope)
  if (reach) body.appendChild(el('p', { class: 'eps-bc-note', text: reach }))

  for (const group of groupByOutput(plan.proposals)) {
    const groupBox = el('input', { type: 'checkbox' })
    groupBox.checked = true
    groupBox.addEventListener('change', () => {
      for (const proposal of group.items) {
        const box = boxes.get(proposal.key)
        box.checked = groupBox.checked
        if (groupBox.checked) ticked.add(proposal.key)
        else ticked.delete(proposal.key)
      }
      refresh()
    })
    body.appendChild(
      el('div', { class: 'eps-bc-group' }, [
        el('div', { class: 'eps-bc-group-head' }, [
          groupBox,
          `${group.output} → ${group.items.length} ${group.items.length === 1 ? 'input' : 'inputs'}`
        ]),
        ...group.items.map((proposal) => proposalRow(proposal, makeBox(proposal.key, true)))
      ])
    )
  }
  if (plan.proposals.length === 0 && !plan.error) {
    body.appendChild(el('p', { class: 'eps-bc-note', text: 'Nothing to connect right now. See "Not connected" below for why.' }))
  }

  if (plan.conflicts.length) {
    body.appendChild(
      el('div', { class: 'eps-bc-group' }, [
        el('div', { class: 'eps-bc-group-head', text: `Conflicts (${plan.conflicts.length}) — not connected` }),
        ...plan.conflicts.map((c) =>
          el('div', { class: 'eps-bc-note', text: `${c.output} → ${c.targetTitle || 'node'} #${c.targetPathId} (${c.inputName}): ${c.reason}` })
        )
      ])
    )
  }

  if (grouped.leftAlone.length) {
    body.appendChild(
      el('div', { class: 'eps-bc-group' }, [
        el('div', { class: 'eps-bc-group-head', text: `Left alone (${grouped.leftAlone.length}) — tick to include again` }),
        ...grouped.leftAlone.map((skip) => proposalRow(skip.proposal, makeBox(skip.key, false)))
      ])
    )
  }

  const skippedBits = []
  for (const note of grouped.notes) {
    skippedBits.push(el('div', { class: 'eps-bc-note', text: note.output ? `${note.output}: ${note.reason}` : note.reason }))
  }
  for (const group of grouped.groups) {
    skippedBits.push(
      el('details', { class: 'eps-bc-skipped' }, [
        el('summary', { text: `${group.label} (${group.items.length})` }),
        el(
          'ul',
          {},
          group.items.map((skip) =>
            el('li', {
              text: skip.targetPathId
                ? `${skip.output || ''} → ${skip.targetTitle || 'node'} #${skip.targetPathId}${skip.inputName ? ` (${skip.inputName})` : ''} — ${skip.reason}`
                : `${skip.output || ''}: ${skip.reason}`
            })
          )
        )
      ])
    )
  }
  if (skippedBits.length) {
    body.appendChild(el('div', { class: 'eps-bc-group' }, [el('div', { class: 'eps-bc-group-head', text: 'Not connected' }), ...skippedBits]))
  }

  const setAll = (on) => {
    for (const proposal of plan.proposals) {
      const box = boxes.get(proposal.key)
      box.checked = on
      if (on) ticked.add(proposal.key)
      else ticked.delete(proposal.key)
    }
    refresh()
  }

  const dialog = el('div', { class: 'eps-bc-dialog', role: 'dialog', 'aria-modal': 'true', 'aria-label': options.title, tabindex: '-1' }, [
    el('h2', { text: options.title }),
    el('p', {
      class: 'eps-bc-lead',
      text:
        'These are REAL wires. Untick anything you don’t want — it is remembered as “leave alone”. ' +
        'One Ctrl+Z undoes the whole batch.'
    }),
    body,
    el('div', { class: 'eps-bc-footer' }, [
      el('button', { class: 'eps-bc-btn', type: 'button', text: 'Select all', onclick: () => setAll(true) }),
      el('button', { class: 'eps-bc-btn', type: 'button', text: 'None', onclick: () => setAll(false) }),
      el('span', { class: 'spacer' }),
      el('button', { class: 'eps-bc-btn', type: 'button', text: 'Cancel', onclick: () => close() }),
      connectBtn
    ])
  ])
  const overlay = el('div', { class: 'eps-bc-overlay' }, [dialog])
  overlay.addEventListener('pointerdown', (event) => {
    if (event.target === overlay) close()
  })
  const removeKeys = installModalKeys(() => close())

  connectBtn.addEventListener('click', () => {
    const outcome = selectionOutcome(plan, ticked)
    close()
    options.onConnect(outcome)
  })

  function close() {
    if (openDialogClose !== close) return
    openDialogClose = null
    removeKeys()
    overlay.remove()
    try {
      previousFocus?.focus?.()
    } catch {
      // focus restore is a nicety
    }
  }
  openDialogClose = close
  document.body.appendChild(overlay)
  refresh()
  dialog.focus()
  return close
}
