/**
 * @file Shared DOM/node helpers for every EPS panel (docs/plan/ROADMAP-shared-panel-code.md
 * M1 + the `installMinWidth` unification). Before this module `el` was
 * declared 8 times and `installMinWidth` 9 times, one copy per panel, and a
 * fix to one never reached its siblings (the v0.68.1 width-lift fix reached
 * five of nine copies; M0 / v0.91.2 patched the other four).
 *
 * Every panel imports these under their EXISTING bare names (`el`,
 * `installMinWidth`) so every CALL-SITE string a test pins on stays
 * byte-identical; only the declarations moved (the search.js precedent).
 * eps_image panels import it as `../lora_library/panel_helpers.js`.
 *
 * Browser-only (`document`, `Node`); no other imports.
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
  const originalOnResize = node.onResize
  node.onResize = function (size) {
    if (size && size[0] < minWidth) size[0] = minWidth
    return originalOnResize?.call(this, size)
  }
  if (node.size && node.size[0] < minWidth) {
    if (typeof node.setSize === 'function') node.setSize([minWidth, node.size[1]])
    else node.size[0] = minWidth
  }
}
