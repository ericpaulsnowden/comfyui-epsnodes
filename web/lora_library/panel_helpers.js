/**
 * @file Shared DOM/node helpers for every EPS panel (docs/plan/ROADMAP-shared-panel-code.md
 * M1 + the `installMinWidth` unification; the three lookups joined 2026-11-15
 * and `chainHook` is M2). Before this module `el` was
 * declared 8 times and `installMinWidth` 9 times, one copy per panel, and a
 * fix to one never reached its siblings (the v0.68.1 width-lift fix reached
 * five of nine copies; M0 / v0.91.2 patched the other four).
 *
 * Every panel imports these under their EXISTING bare names (`el`,
 * `installMinWidth`, `nodeClassOf`, `findWidget`, `linkById`, `chainHook`) so
 * every CALL-SITE string a test pins on stays byte-identical; only the declarations moved (the search.js precedent).
 * eps_image panels import it as `../lora_library/panel_helpers.js`.
 *
 * `el` is browser-only (`document`, `Node`); the rest are plain functions.
 * No imports.
 */

/**
 * Tiny DOM builder: `el('div', { className, text, attrs }, [children])`.
 * `children` may hold Nodes, strings/numbers (appended as text) or
 * null/undefined (skipped).
 */
export function el(tag, options = {}, children = []) {
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

/**
 * Stops *node* from being sized below *minWidth*: wraps `onResize` so later
 * resizes are floored, and lifts the CURRENT width once, so a saved workflow
 * that was already too narrow opens correctly (FORMAT.md section 7.2, the
 * 2026-07-26 Linux fix: wider UI fonts squeeze the panels past what their
 * contents need). Purely additive -- it only ever GROWS a node.
 *
 * Why the lift has a fallback: `node.size` is a Proxy over a typed-array
 * view (a `Float64Array`), never an Array, so the original
 * `Array.isArray(node.size)` guard was always false and a node created
 * narrower than its floor was never lifted (v0.68.1; FORMAT.md section 7.2
 * describes the Proxy, M0 / v0.91.2 backported the fix to the four stale
 * copies). `setSize()` is the preferred path because it runs litegraph's
 * `_sizeUpdated` (the Vue layout mirror) and the onResize wrap just
 * installed; a node without `setSize` still gets the width written straight
 * into the typed array rather than being silently skipped.
 *
 * One guard flag per node: every node class in this pack carries exactly one
 * panel (frame_saver.js calls it twice on the same node with the same floor,
 * which the flag turns into one install), so a second call is always a
 * repeat, never a different floor.
 */
export function installMinWidth(node, minWidth) {
  if (!node || node.__epsMinWidthInstalled) return
  node.__epsMinWidthInstalled = true
  chainHook(
    node,
    'onResize',
    (size) => {
      if (size && size[0] < minWidth) size[0] = minWidth
    },
    { order: 'before', label: 'min-width floor (onResize)' }
  )
  if (node.size && node.size[0] < minWidth) {
    if (typeof node.setSize === 'function') node.setSize([minWidth, node.size[1]])
    else node.size[0] = minWidth
  }
}

/**
 * THE one implementation of FORMAT.md section 7.5's "chain, never replace":
 * capture whatever is on `target[hook]` NOW (a litegraph hook, another
 * extension's wrapper, a widget callback -- or nothing), install a wrapper
 * that runs it and *ours* with the SAME `this` and arguments, and make the
 * wrapper return THE ORIGINAL'S result. `ours`' own return value is ignored
 * (except under `veto`, below). Before this existed the pack hand-wrote the
 * same wrapper ~75 times (docs/plan/ROADMAP-shared-panel-code.md M2).
 *
 * *ours* throwing never breaks the node and never stops the original: it is
 * caught and reported as `warn(`${label} failed`, error)`. A site that needs
 * an error to propagate does not belong here.
 *
 * Install-once guards are NOT owned here: independent chains on one hook
 * are normal (a second module chaining the same hook still runs both), so
 * each caller keeps its own per-node flag/WeakSet where it needs one.
 *
 * Options:
 * - `warn(message, error)`   where failures are reported (each module's
 *   `loggerFor(PREFIX).warn`, so the console names the module); default
 *   `console.warn`.
 * - `label`                  names *ours* in that message (default: the hook).
 * - `order`                  'after' (default): original first, then ours.
 *   'before': ours first, then the original (cleanups that must run while the
 *   node is still intact; the original's errors then simply propagate).
 * - `onOriginalThrow`        only for order 'after'. 'propagate' (default):
 *   the original's error escapes and ours is skipped. 'still-run': ours runs
 *   anyway, then the original's error is rethrown (a try/finally). 'log': the
 *   error is reported through `warn` as `original ${hook} threw`, ours runs,
 *   and the wrapper returns undefined. Never swallow another extension's
 *   errors unless the site did so before.
 * - `veto`                   only for order 'before': ours may answer for the
 *   hook. A return value other than `undefined` is returned and the original
 *   is NOT called (onConnectInput / onConnectOutput: `false` refuses the
 *   link); `undefined` falls through to the original, whose result is returned.
 */
export function chainHook(target, hook, ours, options = {}) {
  const { warn = console.warn, label = hook, order = 'after', onOriginalThrow = 'propagate', veto = false } = options
  const original = target[hook]
  const callOriginal = (self, args) => (typeof original === 'function' ? original.apply(self, args) : undefined)
  const callOurs = (self, args) => {
    try {
      return ours.apply(self, args)
    } catch (error) {
      warn(`${label} failed`, error)
      return undefined
    }
  }
  target[hook] = function (...args) {
    if (order === 'before') {
      const answer = callOurs(this, args)
      if (veto && answer !== undefined) return answer
      return callOriginal(this, args)
    }
    let result
    try {
      result = callOriginal(this, args)
    } catch (error) {
      if (onOriginalThrow === 'propagate') throw error
      if (onOriginalThrow === 'still-run') {
        callOurs(this, args)
        throw error
      }
      warn(`original ${hook} threw`, error)
    }
    callOurs(this, args)
    return result
  }
}

/**
 * A node's ComfyUI class id (`comfyClass`, on the instance or its
 * constructor), or null when it can't be determined. Was declared in 10
 * files, byte-identical.
 * @param {object} node @returns {string|null}
 */
export function nodeClassOf(node) {
  if (!node) return null
  if (node.comfyClass) return node.comfyClass
  if (node.constructor && node.constructor.comfyClass) return node.constructor.comfyClass
  return null
}

/**
 * *node*'s widget called *name*, or undefined. Was declared in 11 files,
 * byte-identical.
 * @param {object} node @param {string} name
 */
export function findWidget(node, name) {
  return node.widgets?.find((w) => w && w.name === name)
}

/**
 * An LLink by id, tolerant of either shape this frontend's forks have used
 * for `graph.links` (a plain object/array indexed by id, or a `Map`). Null
 * when unreachable. Was declared in 7 files, byte-identical.
 */
export function linkById(graph, linkId) {
  if (linkId == null || !graph) return null
  return graph.links?.[linkId] ?? graph.links?.get?.(linkId) ?? null
}
