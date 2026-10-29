/**
 * Draw a link in the colour an EPS node has CLAIMED for it (FORMAT.md §6.6
 * "Collect only", §6.14 `image_output`).
 *
 * image_grid.js and save_image.js grey out the wires of an output that will
 * not run by writing `link.color` and stamping `link.__epsLinkColorOwner`.
 * That relied on the canvas drawing a link in `link.color` -- which the
 * classic litegraph did, but frontend 1.52.7 does NOT (rig 2026-10-29, read
 * in its LGraphCanvas.ts / litegraphLinkAdapter.ts source maps): a plain
 * link is drawn by `_renderAllLinkSegments` -> `renderLink(..., color =
 * null, ...)`, and the adapter then colours a null by `link.type` alone.
 * Only a REROUTE still reads `link.color`. So the dim was invisible
 * everywhere except across a reroute.
 *
 * The fix is one wrap of `LGraphCanvas.prototype.renderLink`: when core
 * passes no colour (`color == null`) for a link an EPS file has claimed
 * (owner tag set, `link.color` set), pass that colour instead. An explicit
 * colour (the white "event flash", a drag preview) and every unclaimed link
 * go through untouched, so links nobody here owns keep core's look exactly.
 * `renderLink` is the same hook the `v1` branch's broadcast_draw.js wraps
 * (its header: links are drawn by the classic canvas in BOTH renderers, so
 * this works with Nodes 2.0 too); the two wraps compose in either order.
 *
 * One module, imported by both files, because the wrap must be installed
 * once (an exception to the duplicate-by-hand convention: two copies would
 * double-wrap). No imports of its own, so the tests' served layouts only
 * need to copy this file. Installed lazily, the first time a file dims a
 * link (the canvas class exists by then). Never throws.
 */

const PREFIX = '[EPSNodes owned_link_colour]'

/** The tag image_grid.js / save_image.js / distributor.js / bypass.js share. */
export const LINK_COLOR_OWNER_KEY = '__epsLinkColorOwner'

const HOOK_FLAG = '__epsOwnedLinkColour'

/** Prototypes already wrapped (a later wrapper by another extension hides
 * our flag, so the flag alone can't stop a second wrap). */
const hooked = new WeakSet()

/** The colour `renderLink` should draw *link* in, given the *color* core
 * passed: core's, unless core passed none and an EPS file claims the link.
 * Pure, for the tests. */
export function ownedLinkColour(link, color) {
  if (color !== null && color !== undefined) return color
  if (link && link[LINK_COLOR_OWNER_KEY] && link.color) return link.color
  return color
}

/** Wraps `proto.renderLink` ONCE. Returns true when the hook is (already) in
 * place, false when there is nothing to wrap (the wire just shows its type
 * colour -- cosmetic only, the backend still skips). Exported for tests. */
export function installOwnedLinkColourHook(proto) {
  try {
    if (!proto || typeof proto.renderLink !== 'function') return false
    if (proto.renderLink[HOOK_FLAG] === true || hooked.has(proto)) return true
    const original = proto.renderLink
    const ownedColourRenderLink = function (ctx, a, b, link, skipBorder, flow, color, ...rest) {
      let colour = color
      try {
        colour = ownedLinkColour(link, color)
      } catch {
        colour = color
      }
      if (colour === color) return original.apply(this, arguments)
      return original.call(this, ctx, a, b, link, skipBorder, flow, colour, ...rest)
    }
    ownedColourRenderLink[HOOK_FLAG] = true
    proto.renderLink = ownedColourRenderLink
    hooked.add(proto)
    return true
  } catch (error) {
    console.warn(PREFIX, 'renderLink hook not installed -- dimmed wires keep their type colour', error)
    return false
  }
}

/** Installs the hook on the live canvas class, if there is one yet. Safe to
 * call as often as you like. */
export function ensureOwnedLinkColourHook() {
  try {
    const canvasClass = globalThis.LiteGraph?.LGraphCanvas
    return installOwnedLinkColourHook(canvasClass?.prototype)
  } catch (error) {
    console.warn(PREFIX, 'renderLink hook lookup failed', error)
    return false
  }
}
