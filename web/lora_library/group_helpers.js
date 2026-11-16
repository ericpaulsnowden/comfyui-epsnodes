/**
 * @file The group-system helpers shared by every panel that has `#` groups
 * (docs/plan/ROADMAP-shared-panel-code.md M4): the Notebook, both Controllers
 * and the Prompt Builder. Before this module each helper was hand-copied into
 * 2-3 files under two names (`category` in notebook.js/controller.js, `group`
 * in universal_controller.js) and a fix to one never reached its siblings
 * (the `_flushPendingNameEdit` commit-gap fix reached the second Controller
 * a week late, as a second bug report).
 *
 * Pure: no DOM, no `app`, no imports, so it is tested directly in Node
 * (tests/test_group_helpers_js.py). The node-property KEYS stay in each
 * panel (`PROP_COLLAPSED_SECTIONS` / `PROP_COLLAPSED_GROUPS` differ on
 * purpose; the saved formats must not change) except `PROP_RENAME_DRAFT`,
 * which both Controllers always spelled identically.
 */

/** Node property holding an open group rename as `[group, text]` (`[]` = none).
 * Same key in both Controllers; saved workflows carry it. */
export const PROP_RENAME_DRAFT = 'Group rename draft'

/** `# Portraits` -> true: a name field starting with `#` (after trim) makes a
 * GROUP instead of an entry/state (FORMAT.md section 7.2). */
export function isGroupNameInput(rawName) {
  return (rawName || '').trim().startsWith('#')
}

/** The stored group name: leading `#`s + whitespace stripped. */
export function groupNameFromInput(rawName) {
  return (rawName || '').trim().replace(/^#+\s*/, '').trim()
}

/**
 * Tolerant parse of a "collapsed groups" node property into an array of
 * group-name strings. Accepts the canonical shape (an array, non-string
 * entries dropped), a JSON-encoded string of the same (what a hand-edit
 * through the node's Properties panel round-trips as), or anything else --
 * `undefined`/`null`/malformed JSON/a non-array all fold to `[]`. Never throws.
 * @param {unknown} raw
 * @returns {string[]}
 */
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

/**
 * Fail-soft parse for `PROP_RENAME_DRAFT` -- same lenient-degrade shape as
 * `parseCollapsedGroups()` (a raw array from `configure()`'s normal restore,
 * OR a JSON string from a hand-edit through the Properties panel), returning
 * `null` for "no open rename" instead of throwing on anything malformed.
 * (`category` is the saved array's slot name; the field is a group.)
 * @param {unknown} raw
 * @returns {{category: string, text: string} | null}
 */
export function parseRenameDraft(raw) {
  let value = raw
  if (typeof raw === 'string') {
    const trimmed = raw.trim()
    if (!trimmed) return null
    try {
      value = JSON.parse(trimmed)
    } catch {
      return null
    }
  }
  if (!Array.isArray(value) || value.length !== 2) return null
  const [category, text] = value
  if (typeof category !== 'string' || !category || typeof text !== 'string') return null
  return { category, text }
}

/**
 * Best-effort commit of an in-progress LEGACY CANVAS edit of a Controller's
 * `name` widget into `widget.value`. The canvas renderer's floating prompt
 * dialog (`canvas.prompt_box`, a free-floating `<input class="value">`) only
 * writes the widget on Enter/OK, so clicking New State straight after typing
 * `# Portraits` would silently make a state instead of a group. Call this
 * before any read of `widget.value` used for branching/naming. Not public
 * litegraph API: every probe is guarded, so a frontend that renames the shape
 * degrades to reading `widget.value` as before -- never a throw (FORMAT.md
 * section 7 fail-soft law).
 *
 * Per-panel differences are parameters: `title` prefixes the warnings, `warn`
 * is the panel's logger, `node` gets `setDirtyCanvas`.
 * @param {{node: {setDirtyCanvas: Function}, widget: object|undefined,
 *   canvas: object|undefined, title: string, warn: Function}} args
 */
export function flushPendingNameEdit({ node, widget, canvas, title, warn }) {
  if (!widget) return
  try {
    const dialog = canvas?.prompt_box
    if (!dialog?.isConnected) return // nothing open -- widget.value is authoritative
    const input = dialog.querySelector?.('.value') // prompt()'s own input/textarea class
    if (!input) return
    const live = input.value
    if (live === widget.value) return // already in sync, nothing pending
    // value + callback + dirty, so this reads as a real committed edit to
    // BOTH renderers and to everything that reads the widget afterward.
    widget.value = live
    try {
      widget.callback?.(live)
    } catch (error) {
      warn(`${title}: name widget callback threw`, error)
    }
    node.setDirtyCanvas(true, true)
    // Courtesy close: we applied the SAME value Enter/OK would have, through
    // the SAME widget.value+callback path; close() only tears down the
    // dialog's own DOM, it never re-invokes callback, so nothing double-fires.
    dialog.close?.()
  } catch (error) {
    warn(`${title}: could not flush pending name edit`, error)
  }
}
