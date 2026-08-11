/**
 * @file EPS Frame Saver frontend (FORMAT.md §6.7). Exports the `init()`/
 * `attach(node)`/`loadedGraphNode(node)` hooks `web/eps_image.js` calls; each
 * no-ops for every node type other than `EPSFrameSaver`.
 *
 * Clean-room implementation — this pack is MIT, so nothing here is derived
 * from GPL video-picker prior art (VHS/LNL); the player is built fresh from
 * this pack's OWN proven patterns:
 *
 * - **The Browse dialog** (`openPicker`/`loadPickerDir`/`renderPickerDialog`/
 *   `buildPickerFooter` below) is a trimmed port of `comfyui-premiere-bridge`'s
 *   `web/cprb/nodes.js` `openPicker()` family (itself ported from this pack's
 *   own `web/lora_library/notebook.js` `openBrowsePicker()`), reduced to the
 *   FILE-mode-only shape (no directory-choose mode — this node never picks a
 *   folder): a singleton overlay on `document.body` (never nested in the
 *   node's own DOM, so it isn't clipped by a small node), Escape/backdrop-
 *   click to close, a "type or paste a path" row for UNC/other-drive targets
 *   independent of the server's own drive enumeration, and the `ROOTS`
 *   sentinel's drive-list navigation. Reuses the pack's EXISTING
 *   `GET /lora_library/fs/list` (FORMAT.md §5/STANDARD-fs-browse.md) with
 *   `ext` narrowed to :data:`VIDEO_EXT_PARAM` — no new fs/list route.
 * - **Host-only gating** mirrors both files' identical `GET /lora_library/
 *   config` `is_local` cache/gate (`fetchConfig`/`getConfig`/`refreshGating`
 *   below) — Browse hides and a calm note appears on a remote browser,
 *   exactly like the Notebook/premiere file panels.
 * - **DOM-widget sizing** follows the two proven shapes documented in
 *   `web/lora_library/notebook.js`'s `attachDomWidget()` and
 *   `comfyui-premiere-bridge/web/cprb/nodes.js`'s `attachBarWidget()`:
 *     - **Fill** (the video area, `attachFillWidget` below): `getMinHeight`
 *       only, no `getMaxHeight` — litegraph's widget-arrange pass then gives
 *       this widget whatever height is left after the fixed-height bars
 *       above/below it.
 *     - **Fixed** (the path bar + control strip, `attachFixedWidget` below):
 *       ALL FOUR knobs — `getMinHeight`+`getMaxHeight` (the classic canvas
 *       renderer) AND `computeSize`+`computedHeight` (what cprb's
 *       `attachBarWidget()` found the Vue-node renderer treats as
 *       authoritative instead — "verified live 2026-07-19: the bar
 *       collapsed to ~7px and its buttons clipped past the node's bottom
 *       edge" when only the first two were set) AND the element's own CSS
 *       `height`/`min-height`. Three separate `addDOMWidget` calls (path bar
 *       / video / strip), not one mega-widget with internal flex children —
 *       litegraph arranges DOM widgets top-to-bottom exactly like ordinary
 *       widgets, handing each fixed one its declared height and the one
 *       flexible one everything left over, so three simple single-purpose
 *       widgets compose correctly without needing to fight the layout pass.
 * - **Post-`configure()` resync** follows `eps_image/image_grid.js`'s
 *   `attach()`: `nodeCreated` (this file's `attach()`) fires BEFORE
 *   `LGraphNode.configure()` restores a loaded workflow's real widget
 *   values (`eps_image/resolution.js`'s header: confirmed live and in
 *   `LGraphNode.ts`), so reading `video_path`/`frame` synchronously here
 *   would only ever see their just-constructed defaults. `attach()` below
 *   defers its initial resync one tick (`setTimeout(fn, 0)`, the same
 *   trick `image_grid.js`'s `attach()` uses for its own per-node restore
 *   race) so the current synchronous `configure()` call — if this IS a
 *   workflow load — finishes first. `loadedGraphNode()` (fired once per
 *   node after the WHOLE graph's `configure()` pass completes — the
 *   cross-workflow-load path) and a wrapped `node.onConfigure` (undo/redo,
 *   which re-applies serialized state via `configure()` directly without
 *   re-firing `nodeCreated`) are both belt-and-suspenders re-syncs onto the
 *   same idempotent `fullResync()` — redundant by design, matching
 *   `image_grid.js`'s explicit "all three firing is redundant by design"
 *   stance for the identical class of restore-timing bug.
 *
 * **Two widgets, hidden.** `video_path` (STRING) and `frame` (INT) are the
 * node's real, server-declared, serialized widgets — FORMAT.md §6.7 requires
 * both to round-trip and reach `execute()` untouched by our JS. Both are
 * hidden (`.hidden = true`, the pack-wide "hidden serialized bridge" trick
 * already used for the Notebook's `file`, `EPSSwitcher`'s `toggles`, and
 * `EPSImageGrid`'s `grid_uuid`) and replaced visually by this file's own
 * controls: the path bar's Browse button and the paste-a-path handler both
 * write `video_path`; the control strip's step buttons/number input/playback
 * all write `frame`. Unlike `notebook.js`'s `file` widget, neither needs its
 * OWN wrapped `.callback` for external-change detection — the only writers
 * of `video_path` are this file's Browse picker and paste handler (both
 * funnel through `chooseVideoPath`, which calls `onPathChanged` directly, no
 * callback indirection needed) and a workflow load/undo (`configure()` never
 * invokes widget callbacks at all — it assigns `.value` directly — so a
 * wrapped callback would not even see that case; the three resync hooks
 * above are what actually cover it).
 *
 * **Preview vs. output ("close-enough preview, EXACT frame on output",
 * FORMAT.md §6.7's locked framing).** The `<video>` element gives smooth,
 * native play/pause/loop/seek for scrubbing, but it is ONLY ever a preview:
 * every frame/counter/seek computation here uses `currentTime = frame / fps`
 * against the PROBED `fps`/`frame_count` (`GET /eps_frame_saver/probe`),
 * never the browser's own (possibly VFR-approximate) notion of time. The
 * actual pixels ComfyUI receives always come from `eps_image/
 * frame_saver_video.py`'s `extract_frame` at execution time, independent of
 * whatever the preview happened to show. Two direct consequences below:
 *   - **Exotic codecs degrade gracefully, never hard-fail.** If the browser
 *     can't decode the container/codec, the `<video>` element's `error`
 *     event fires; `state.videoPlayable` flips to `false`, the overlay
 *     switches to a plain message, and playback/seeking are disabled — but
 *     the frame number input, step buttons, and counter stay FULLY
 *     functional (they only need the PROBED fps/frame_count, decoded
 *     server-side by PyAV, completely independent of what Chrome/whatever
 *     browser can render), and Run still extracts the exact frame
 *     regardless. Probing itself failing too (a file PyAV also can't read)
 *     is the one case output would also fail — surfaced as a status line on
 *     the path bar, never a client-side block on Run.
 *   - **Step buttons derive `currentTime` FROM the frame integer
 *     (`frame / fps`), never the reverse.** FORMAT.md §6.7 describes the
 *     step as "`currentTime ± 1/fps`" conceptually (each step moves the
 *     play head by one frame's duration) — implemented here as "increment
 *     the canonical integer frame by 1, then seek to its exact time" rather
 *     than repeatedly nudging `currentTime` by a float `1/fps`, which is
 *     mathematically the same step size but avoids compounding float error
 *     across many successive steps (`setFrame()`/`seekVideoToFrame()`
 *     below).
 *
 * **Playback vs. programmatic seeking don't fight each other.** While the
 * video is actually PLAYING, its own `timeupdate` events are the source of
 * truth for the frame counter (`syncFrameFromPlayback()`) — that path
 * deliberately never re-assigns `video.currentTime` (it was just READ from
 * there), only writes the `frame` widget + updates the number input/counter.
 * A user-driven change (step button, typed number, `configure()`/undo
 * restore) goes through `setFrame()`/`refreshFrameUi({seek:true})` instead,
 * which DOES seek the video to match. Mixing the two (seeking during
 * `timeupdate`) would fight native playback with a constant few-millisecond
 * correction every tick, from the target time never landing exactly back on
 * `currentTime` after a `round()`.
 *
 * **Paste a path onto the node (owner ask 2026-07-21).** Copy a video
 * file's path in Finder ("Copy as Pathname") or Explorer ("Copy as path"),
 * select this node, Ctrl/Cmd+V: the clipboard TEXT becomes `video_path`
 * through the exact same `chooseVideoPath()` a Browse pick uses — one load
 * path, so a pasted file probes, previews, and drives the "Frame X / N"
 * counter identically to a browsed one. Mechanics (implementation under
 * the "Paste a path" section below):
 *   - **DOM `paste` event + `event.clipboardData.getData('text')`, never
 *     `navigator.clipboard.readText()`**: the async Clipboard API is
 *     [SecureContext]-gated — on the owner's Mac viewing the PC over plain
 *     `http://<pc-ip>` it does not even EXIST — while the paste event's
 *     `clipboardData` works there. Same boundary `image_grid.js` documents
 *     for its Copy (FORMAT.md §6.6's Mac fixes) and already solves the
 *     same way for its own paste IN ("0.28.1 / clipboard-API sensitivity"
 *     in its header).
 *   - **Selection-gated, per-instance, cleaned up** (`image_grid.js`'s
 *     `installPasteFiles` idiom, adapted — that node gets selection gating
 *     free from core's `usePaste.ts` FILE routing; TEXT needs our own
 *     listener): each attached node registers one capture-phase `document`
 *     listener that no-ops unless THIS node is the SINGLE selected node
 *     (`app.canvas.selected_nodes`, failing CLOSED) and neither
 *     `event.target` nor `document.activeElement` is a text-entry surface
 *     — so pasting into the Browse dialog's own path input, the frame
 *     number input, or any other field/dialog is never hijacked.
 *     `wireNodeCleanup`'s `onRemoved` wrap removes the listener; no leaked
 *     document listeners.
 *   - **Consume only what is path-shaped.** The text is normalized first
 *     (`cleanPastedVideoPath`: first non-empty line — Finder multi-select
 *     copies one path per line — one pair of wrapping quotes stripped
 *     (Explorer's "Copy as path" double-quotes; shells single-quote), and
 *     `file://` URLs percent-decoded to plain paths incl. the `/C:/`
 *     drive and UNC-host forms). Text that does not clean up to
 *     `looksAbsolutePath` is left entirely alone — NOT consumed — so
 *     core's own paste pipeline (workflow JSON, node paste, ...) still
 *     sees it untouched. A path-shaped paste IS consumed (preventDefault +
 *     stopImmediatePropagation; the capture-phase registration is what
 *     lets that preempt core's earlier-registered bubble-phase `usePaste`
 *     listener): an allowlisted-extension or extensionless path routes to
 *     `chooseVideoPath()` — the probe route stays the REAL validator, its
 *     errors landing on the path bar exactly as for Browse — while a
 *     clearly non-video extension is refused EARLY (toast + path-bar
 *     status naming the allowlist) WITHOUT clobbering the currently-loaded
 *     path with a rejection the server (lockstep list) guarantees anyway.
 *   - **Remote viewers change nothing here**: the pasted path writes the
 *     widget exactly like a workflow-loaded path would, and
 *     `refreshVideoSource`'s existing `isLocal === false` short-circuit
 *     skips probe/stream and shows the host-only overlay — Run still
 *     works, since the SERVER is what reads the path at execute() time.
 *     The asymmetry with Browse (hidden when remote) is deliberate: Browse
 *     needs the server to LIST folders for you, paste needs nothing from
 *     the server until probe time — and the owner's usual viewer (Mac →
 *     PC over LAN) is exactly the remote case pasting must keep working
 *     in.
 * The cleanup/verdict helpers are pure and exported;
 * `tests/test_frame_saver_paste_js.py` drives them under Node (the
 * `test_resolution_grid_js.py` fixture pattern) and locks the JS ext
 * allowlist to `routes_frame_saver.py`'s tuple.
 *
 * **Five-button transport + press-and-hold auto-repeat (owner ask
 * 2026-07-26: "+5/-5 and a jump-to-start button; holding -5/-1/+1/+5 should
 * keep moving you through the timeline instead of having to click
 * repeatedly").** `buildControlStrip()`'s strip now reads, left to right:
 * jump-to-start (`⏮`, `setFrame(state, 0)`, plain click ONLY -- no hold
 * wiring, since it's idempotent and a repeat would mean nothing), `−5`/`−1`/
 * `+1`/`+5` (the exact same `stepFrame()` the old −1/+1 always used, just a
 * different delta), play/pause, the frame number box, and the counter.
 *   - **Pointer Events, not mouse events** (`pointerdown`/`pointerup`/
 *     `pointercancel`/`pointerleave` + `setPointerCapture`), so a hold that
 *     drifts off the button, or releases outside it entirely, still stops
 *     cleanly. This pack already has proven precedent for exactly this
 *     shape: `comfyui-photoshop-bridge/web/cpsb/gallery.js`'s
 *     `attachHoldToCompare` (press-and-hold a thumbnail to reveal its
 *     original) captures the pointer on `pointerdown` and releases/cleans up
 *     on the SAME `pointerup`/`pointercancel`/`pointerleave` trio, wrapped in
 *     try/catch for partial Pointer Events support -- `attachStepButton()`
 *     below follows that idiom directly (including skipping
 *     `preventDefault()`, which gallery.js's press-reveal also never needed
 *     -- unlike `lora_library/notebook.js`'s pane-splitter DRAG, which does
 *     call it to suppress text-selection/scroll during a drag, a step button
 *     has no such default action worth suppressing, and doing so risked
 *     swallowing the browser's own focus-shift-commits-the-frame-box
 *     behavior when a user clicks a step button right after typing a frame
 *     number).
 *   - **Click vs. hold, one function, no double-stepping.** `pointerdown`
 *     performs the first step immediately (this IS "a click" for a mouse/
 *     touch/pen press) and arms a `HOLD_INITIAL_DELAY_MS` timer; if still
 *     held when it fires, a `HOLD_REPEAT_INTERVAL_MS` interval takes over.
 *     Browsers still also dispatch an ordinary `click` once a real pointer
 *     press-release completes (unrelated to anything this file asks for --
 *     just how `click` always works) -- but that event's `event.detail` is
 *     always >= 1 for a pointer-originated click and exactly 0 ONLY for a
 *     click synthesized by KEYBOARD activation (Enter/Space on a focused
 *     button -- the standard cross-browser tell for this, needing no
 *     browser-sniffing). The `click` listener acts ONLY when `detail === 0`,
 *     so a real pointer click is handled exactly once (by `pointerdown`) and
 *     a keyboard activation is handled exactly once (by `click`) -- the two
 *     paths can never both fire for the same press. Holding Enter/Space
 *     auto-repeats NATIVELY (the browser re-fires `click`/`keydown` on its
 *     own key-repeat cadence) straight through this same `detail === 0`
 *     branch, so keyboard users get the identical "hold to keep moving"
 *     behavior for free, with no separate keydown handler to keep in sync
 *     (and so nothing here can "double-step or break" that native repeat --
 *     there simply is no keydown listener on these buttons to conflict).
 *   - **Same clamp every path, and no runaway interval.** `stepFrame()` is
 *     the ONE function every trigger calls -- a plain click, the
 *     pointerdown's own first step, and every repeat tick alike -- and it
 *     clamps through the exact same `clampFrame()`/`setFrame()` every other
 *     frame change (typed number, probe-time reclamp) already used: no
 *     parallel clamp logic to drift out of sync. It returns `false` WITHOUT
 *     calling `setFrame` at all (so no redundant seek/commit) the instant a
 *     step would not actually move the frame -- already at 0 for a negative
 *     delta, already at the last frame for a positive one. The repeat
 *     interval checks that return value and stops ITSELF the first time a
 *     tick is a no-op, rather than continuing to poll a boundary it can
 *     never cross every `HOLD_REPEAT_INTERVAL_MS` for as long as the button
 *     stays held -- "never leave a runaway interval."
 *   - **Every stop path actually stops it.** `pointerup`/`pointercancel`/
 *     `pointerleave` (gallery.js's own three), losing the window (`blur` --
 *     a hold gesture never fires pointerup at all if e.g. Cmd/Alt-Tab swaps
 *     the app away mid-hold), and the node's `onRemoved` (undo-deleting the
 *     node mid-hold) all reach the same per-button `stopRepeat()` -- matching
 *     this file's existing cleanup discipline (`wireNodeCleanup`'s
 *     `onRemoved` wrap already tears down the picker's keydown listener and
 *     the paste listener the identical way; the `blur` listener and every
 *     button's `stopRepeat()` join that same wrap here).
 * **`MIN_NODE_WIDTH` raised alongside it** (2026-07-26) -- three buttons
 * became six (five transport buttons + play/pause), which needs a taller
 * floor than the v0.30.3 Linux-overflow fix originally set; see the
 * constant's own docstring for the arithmetic.
 *
 * Vanilla ES modules, no build step, matching the rest of this pack.
 */

import { api } from '../../../scripts/api.js'
import { app } from '../../../scripts/app.js'

/** FORMAT.md §6.7 — frozen once shipped. */
const CLASS_ID = 'EPSFrameSaver'

/** The node's real, server-declared widget names (FORMAT.md §6.7). */
const PATH_WIDGET_NAME = 'video_path'
const FRAME_WIDGET_NAME = 'frame'

const PATH_BAR_WIDGET_NAME = 'epsfs_path_bar'
const PATH_BAR_WIDGET_TYPE = 'eps_frame_saver_path_bar'
const VIDEO_WIDGET_NAME = 'epsfs_video'
const VIDEO_WIDGET_TYPE = 'eps_frame_saver_video'
const STRIP_WIDGET_NAME = 'epsfs_strip'
const STRIP_WIDGET_TYPE = 'eps_frame_saver_strip'

/** Fixed heights for the two non-flexible bars (see file header's DOM-widget
 * sizing section) -- kept small and constant, like cprb's own `BAR_HEIGHT`. */
const PATH_BAR_HEIGHT = 32
const STRIP_HEIGHT = 36

/** Press-and-hold auto-repeat timing for the four frame-step buttons (owner
 * ask 2026-07-26: "holding down the -5/-1/+1/+5 should keep moving you
 * through the timeline instead of having to click repeatedly"). ~400ms
 * initial delay before the first repeat -- long enough that an ordinary
 * click never feels like it "double-moved" -- then a steady ~90ms cadence
 * (within the owner's requested 80-120ms band) fast enough to read as
 * scrubbing rather than a slideshow. One shared cadence for all four
 * buttons (not tuned per delta) -- see `attachStepButton()` / file header. */
const HOLD_INITIAL_DELAY_MS = 400
const HOLD_REPEAT_INTERVAL_MS = 90

/** Floor for the flexible video area -- enough to see a usable preview even
 * on a freshly-dropped, not-yet-resized node; grows to fill a taller node. */
const VIDEO_MIN_HEIGHT = 220

/** STANDARD-fs-browse.md's `fs/list` sentinel for "the top level" (mirrors
 * `lora_library/routes.py`'s `ROOTS`). */
const FS_ROOTS = 'ROOTS'

/** FORMAT.md §6.7's video ext allowlist, mirrored from `eps_image/
 * routes_frame_saver.py`'s `VIDEO_EXTENSIONS` -- JS has no way to import a
 * Python module, so this list is kept in lockstep by hand; a file the picker
 * lets you choose is always one the probe/stream routes will also accept.
 * Exported: paste-a-path's early extension check reuses it, and
 * `tests/test_frame_saver_paste_js.py` asserts it still equals the backend
 * tuple -- the hand-lockstep, now machine-checked. */
export const VIDEO_EXT_LIST = ['.mp4', '.mov', '.webm', '.mkv', '.avi', '.m4v', '.ogv']
const VIDEO_EXT_PARAM = VIDEO_EXT_LIST.join(',')

const STYLE_TAG_ID = 'eps-frame-saver-styles'
const PICKER_OVERLAY_ID = 'epsfs-picker-overlay'

const PREFIX = '[eps_image:frame_saver]'

/** FORMAT.md §6.7 display name -- toast summaries only (`resolution.js`'s
 * identical `NODE_TITLE` convention). */
const NODE_TITLE = 'EPS Frame Saver'

/** node -> per-instance state, for every EPSFrameSaver we've attached to.
 * A WeakMap (not just a WeakSet) because `loadedGraphNode()` needs to look
 * the state BACK up by node, not just check "have we seen this before". */
const nodeStates = new WeakMap()

function warn(message, error) {
  if (error !== undefined) console.warn(PREFIX, message, error)
  else console.warn(PREFIX, message)
}

/** Best-effort toast via the pack's established `app.extensionManager?.
 * toast?.add?.(...)` convention (`resolution.js`'s `toast()`,
 * `image_grid.js`'s `notifyClipboard()`) -- never throws; a missing toast
 * surface on some older/newer frontend build just degrades to silence,
 * this pack's usual fail-soft posture. */
function toast(node, severity, detail) {
  try {
    app.extensionManager?.toast?.add?.({
      severity,
      summary: node.title || NODE_TITLE,
      detail,
      life: severity === 'error' ? 6000 : 3000
    })
  } catch (error) {
    console.warn(PREFIX, 'toast failed', error)
  }
}

// ---------------------------------------------------------------------------
// Tiny DOM builder + fetch helpers -- this pack is vanilla JS with no
// templating engine and no shared per-request wrapper across `eps_image/`
// (unlike `lora_library/api.js`, which `web/lora_library/notebook.js` and
// `comfyui-premiere-bridge/web/cprb/nodes.js` both import); every existing
// `eps_image/*.js` file (image_grid.js, switcher.js, resolution.js) instead
// calls `api.fetchApi`/`api.apiURL` directly or via its own tiny local
// helper, so this file follows suit rather than reaching into a sibling
// family's module.
// ---------------------------------------------------------------------------

/**
 * @param {string} tag
 * @param {{className?: string, text?: string, attrs?: Record<string,string>}} [options]
 * @param {(Node|string)[]} [children]
 * @returns {HTMLElement}
 */
function el(tag, options = {}, children = []) {
  const node = document.createElement(tag)
  if (options.className) node.className = options.className
  if (options.text !== undefined) node.textContent = options.text
  if (options.attrs) {
    for (const [key, value] of Object.entries(options.attrs)) {
      node.setAttribute(key, value)
    }
  }
  for (const child of children) {
    if (child == null) continue
    node.append(child instanceof Node ? child : document.createTextNode(String(child)))
  }
  return node
}

/**
 * GET a JSON route. Resolves to parsed JSON; rejects with an Error whose
 * message is the server's `error` field when the response is non-2xx.
 * @param {string} path
 * @param {Record<string, string>} [params]
 */
async function getJson(path, params) {
  const query = params ? `?${new URLSearchParams(params)}` : ''
  const response = await api.fetchApi(`${path}${query}`)
  let data = null
  try {
    data = await response.json()
  } catch {
    // Non-JSON body (proxy error page etc.) -- fall through to status check.
  }
  if (!response.ok) {
    const message = data && data.error ? data.error : `HTTP ${response.status}`
    const error = new Error(message)
    error.status = response.status
    throw error
  }
  return data
}

/**
 * *text* if it already fits in *maxChars*, else `'…' + the last
 * (maxChars - 1) characters` -- truncated from the FRONT so the tail
 * (usually the filename, the part that actually identifies the file) stays
 * visible. Ported from `notebook.js`/`cprb/nodes.js`'s identical
 * `frontTruncate()`, with a fixed character budget rather than their
 * `ResizeObserver`-driven pixel measurement -- a reasonable simplification
 * for this file's small, fixed-width bar (unlike the Notebook's full-width,
 * highly variable file panel).
 * @param {string} text
 * @param {number} [maxChars]
 */
function frontTruncate(text, maxChars = 60) {
  const value = String(text ?? '')
  if (value.length <= maxChars) return value
  return `…${value.slice(-(maxChars - 1))}`
}

// ---------------------------------------------------------------------------
// Node / widget lookups
// ---------------------------------------------------------------------------

/**
 * @param {object} node
 * @returns {string|null} the node's ComfyUI class id, or null if it can't be
 * determined (ported from `notebook.js`'s `isNotebookNode`/`nodeClassOf`).
 */
function nodeClassOf(node) {
  if (!node) return null
  if (node.comfyClass) return node.comfyClass
  if (node.constructor && node.constructor.comfyClass) return node.constructor.comfyClass
  return null
}

function findWidget(node, name) {
  return node.widgets?.find((w) => w && w.name === name)
}

/** Writes *value* through *widget*'s real setter + callback, so a
 * programmatic change (Browse's onChoose) behaves exactly like the user
 * having typed/picked it themselves. Ported from `notebook.js`/`cprb/
 * nodes.js`'s identical `setFileWidgetValue`/`setWidgetValue`. */
function writeWidgetValue(widget, node, value) {
  widget.value = value
  try {
    widget.callback?.(value)
  } catch (error) {
    warn(`${widget.name} widget callback threw`, error)
  }
  node.graph?.setDirtyCanvas(true, true)
}

function hideWidget(node, widget) {
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
  node.graph?.setDirtyCanvas(true, true)
}

// ---------------------------------------------------------------------------
// Styles -- one injected <style> tag, guarded so re-registration (hot
// reload, multiple nodes) never duplicates it. ComfyUI theme variables with
// literal fallbacks, matching every other DOM widget in this pack.
// ---------------------------------------------------------------------------

let stylesInjected = false

const CSS_TEXT = `
.epsfs-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  height: 100%;
  box-sizing: border-box;
  padding: 4px 6px;
  background: var(--comfy-menu-bg, #262626);
  border: 1px solid var(--border-color, #444);
  border-radius: 4px;
  font-family: inherit;
  font-size: 11px;
  color: var(--input-text, #ccc);
  overflow: hidden;
}
.epsfs-btn {
  flex: 0 0 auto;
  background: var(--comfy-input-bg, #1e1e1e);
  border: 1px solid var(--border-color, #444);
  color: var(--input-text, #ccc);
  border-radius: 4px;
  padding: 3px 8px;
  font-size: 11px;
  cursor: pointer;
  white-space: nowrap;
}
.epsfs-btn:hover:not(:disabled) { background: var(--content-hover-bg, #2a2a2a); }
.epsfs-btn:disabled { opacity: 0.5; cursor: default; }
.epsfs-btn-small { padding: 2px 8px; }
.epsfs-btn-icon { min-width: 30px; text-align: center; font-weight: 600; }
.epsfs-path-text {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 10.5px;
  color: var(--descrip-text, #999);
}
.epsfs-host-note {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-style: italic;
  color: var(--descrip-text, #999);
}
.epsfs-host-note:empty { display: none; }
.epsfs-path-status {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  text-align: right;
  color: var(--descrip-text, #999);
}
.epsfs-path-status:empty { display: none; }
.epsfs-path-status-error { color: var(--error-text, #ff4444); }
.epsfs-videowrap {
  position: relative;
  width: 100%;
  height: 100%;
  box-sizing: border-box;
  overflow: hidden;
  background: #000;
  border: 1px solid var(--border-color, #444);
  border-radius: 4px;
}
.epsfs-video {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: contain;
  background: #000;
  cursor: pointer;
}
.epsfs-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 12px;
  background: rgba(0, 0, 0, 0.55);
  color: var(--input-text, #ccc);
  font-size: 12px;
  font-style: italic;
  pointer-events: none;
}
.epsfs-strip {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  height: 100%;
  box-sizing: border-box;
  padding: 4px 6px;
  background: var(--comfy-menu-bg, #262626);
  border: 1px solid var(--border-color, #444);
  border-radius: 4px;
  font-family: inherit;
  font-size: 11px;
  color: var(--input-text, #ccc);
  overflow: hidden;
}
.epsfs-frame-input {
  /* 2026-07-26 (owner, Linux): a fixed 56px width sized against
     macOS/Windows metrics clipped its own digits under Linux's wider
     default fonts. Floor + shrink-to-fit instead of a hard width. */
  flex: 0 1 auto;
  width: auto;
  min-width: 56px;
  max-width: 100%;
  box-sizing: border-box;
  background: var(--comfy-input-bg, #1e1e1e);
  border: 1px solid var(--border-color, #444);
  color: var(--input-text, #ccc);
  border-radius: 4px;
  padding: 3px 5px;
  font-size: 11px;
}
.epsfs-frame-input:disabled { opacity: 0.5; }
.epsfs-counter {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  text-align: right;
  color: var(--descrip-text, #999);
}
.epsfs-picker-backdrop {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.5);
  z-index: 10000;
}
.epsfs-picker {
  display: flex;
  flex-direction: column;
  width: min(480px, 90vw);
  max-height: min(520px, 80vh);
  background: var(--comfy-menu-bg, #262626);
  border: 1px solid var(--border-color, #444);
  border-radius: 6px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
  overflow: hidden;
  font-family: inherit;
  font-size: 11px;
  color: var(--input-text, #ccc);
}
.epsfs-picker-pathbar {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--border-color, #444);
}
.epsfs-picker-path-input {
  flex: 1 1 auto;
  min-width: 0;
  background: var(--comfy-input-bg, #1e1e1e);
  border: 1px solid var(--border-color, #444);
  color: var(--input-text, #ccc);
  border-radius: 4px;
  padding: 4px 6px;
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 10.5px;
}
.epsfs-picker-path-input:focus { outline: 1px solid var(--input-focus-border, #5c9dff); }
.epsfs-picker-content {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.epsfs-picker-header {
  flex: 0 0 auto;
  padding: 8px 10px;
  border-bottom: 1px solid var(--border-color, #444);
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 10.5px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--descrip-text, #999);
}
.epsfs-picker-list {
  flex: 1 1 auto;
  min-height: 120px;
  overflow-y: auto;
  padding: 4px;
}
.epsfs-picker-row {
  padding: 5px 8px;
  border-radius: 3px;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.epsfs-picker-row:hover { background: var(--content-hover-bg, #2a2a2a); }
.epsfs-picker-status,
.epsfs-picker-empty {
  padding: 10px;
  color: var(--descrip-text, #999);
  font-style: italic;
}
.epsfs-picker-error { color: var(--error-text, #ff4444); font-style: normal; }
.epsfs-picker-footer {
  flex: 0 0 auto;
  display: flex;
  justify-content: flex-end;
  gap: 6px;
  padding: 6px 8px;
  border-top: 1px solid var(--border-color, #444);
}
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
// Remote gating (FORMAT.md §6.7 "Host-only Browse") -- `GET /lora_library/
// config` cached at MODULE scope (every attached EPSFrameSaver node shares
// one fetch) with a short TTL; concurrent callers de-dupe onto one in-flight
// promise. Ported from `notebook.js`/`cprb/nodes.js`'s identical
// `fetchConfig`/`getConfig`/`refreshGating`. Reusing lora_library's OWN
// `/config` route (rather than inventing a second one) matches this file
// reusing `/lora_library/fs/list` for Browse -- both are the pack's existing
// "am I local" + "browse the server filesystem" primitives, and FORMAT.md
// §6.7 explicitly says to reuse the fs/list standard rather than building a
// parallel one.
// ---------------------------------------------------------------------------

const CONFIG_CACHE_TTL_MS = 60000

let cachedConfig = null
let cachedConfigAt = 0
let cachedConfigPromise = null

function fetchConfig() {
  if (cachedConfigPromise) return cachedConfigPromise
  cachedConfigPromise = getJson('/lora_library/config')
    .then((data) => {
      cachedConfig = data
      cachedConfigAt = Date.now()
      return data
    })
    .finally(() => {
      cachedConfigPromise = null
    })
  return cachedConfigPromise
}

function getConfig() {
  if (cachedConfig && Date.now() - cachedConfigAt < CONFIG_CACHE_TTL_MS) {
    return Promise.resolve(cachedConfig)
  }
  return fetchConfig()
}

/**
 * Refreshes `state.isLocal` from (cached) `/lora_library/config` and applies
 * it (hides Browse + shows the host-machine note on a remote viewer). Never
 * throws: a failed fetch leaves `state.isLocal` whatever it already was
 * (`null`/unknown reads as local everywhere this is checked with
 * `=== false`) -- fails OPEN rather than disabling the node over a network
 * hiccup, this pack's usual posture.
 */
async function refreshGating(state) {
  let config
  try {
    config = await getConfig()
  } catch (error) {
    warn('could not load /lora_library/config; treating this node as local', error)
    return
  }
  state.isLocal = config?.is_local !== false
  applyGating(state)
}

function applyGating(state) {
  const remote = state.isLocal === false
  if (state.browseBtn) state.browseBtn.style.display = remote ? 'none' : ''
  if (state.hostNoteEl) {
    state.hostNoteEl.textContent = remote ? 'Host machine only' : ''
    state.hostNoteEl.title = remote
      ? 'Video preview and probing only work on the machine running ComfyUI; Run still works.'
      : ''
  }
  refreshVideoSource(state) // re-decide the preview now that isLocal is known.
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

function createState(node, pathWidget, frameWidget) {
  return {
    node,
    pathWidget,
    frameWidget,
    path: '',
    // §6.7 v0.60.0 wired `video` input, re-derived by every fullResync():
    // null (unwired) | {kind:'input_ref', ref, title} (upstream LoadVideo,
    // statically knowable -> full scrubbing via the routes' input_ref mode)
    // | {kind:'opaque', title} (any other wired source -- arrives at run
    // time; the frame field stays live, the preview honestly says so).
    wired: null,
    // GET /eps_frame_saver/probe's last successful result, or null.
    probe: null,
    // Bumped on every path change; a probe response is discarded if this no
    // longer matches the token it was issued under (stale, superseded by a
    // later path change) -- same guard shape as notebook.js's loadToken.
    probeToken: 0,
    // §2/§7.1-style locality verdict; null = not yet known (treated as
    // local -- see refreshGating's docstring).
    isLocal: null,
    // null = loading/unknown, true = the <video> can decode this file,
    // false = it fired an `error` event (exotic codec) -- see file header's
    // "Exotic codecs degrade gracefully" section.
    videoPlayable: null,
    // DOM refs, filled in by buildUi().
    videoEl: null,
    overlayEl: null,
    browseBtn: null,
    pathTextEl: null,
    hostNoteEl: null,
    pathStatusEl: null,
    jumpStartBtn: null,
    stepBack5Btn: null,
    stepBackBtn: null,
    stepFwdBtn: null,
    stepFwd5Btn: null,
    playPauseBtn: null,
    frameInputEl: null,
    counterEl: null,
    // stopRepeat() for each of the four hold-repeat buttons (−5/−1/+1/+5),
    // and the window `blur` listener that force-stops all of them -- both
    // filled in by buildControlStrip(), both torn down by wireNodeCleanup's
    // onRemoved wrap (file header's "press-and-hold" section).
    stepRepeaters: [],
    windowBlurHandler: null,
    // The Browse picker's window-level Escape-key listener while open (the
    // picker lives on document.body, not inside this widget's own DOM).
    pickerKeydownHandler: null,
    pickerPathInputEl: null,
    // Bumped on every picker navigation; a fs/list response issued under an
    // older value is dropped -- same guard shape as probeToken above.
    // Without it a slow listing landing after a fast typed-path navigation
    // both re-renders the stale directory AND overwrites the path the user
    // just typed (renderPickerDialog rewrites pickerPathInputEl from
    // data.dir). 2026-08-08 audit.
    pickerNavToken: 0,
    // The per-instance document-level `paste` listener for this node's
    // whole lifetime (paste-a-path, file header) -- registered by
    // installPastePathHandler, removed by wireNodeCleanup's onRemoved wrap.
    pasteHandler: null
  }
}

// ---------------------------------------------------------------------------
// DOM-widget attachment (see file header's "DOM-widget sizing" section for
// which of these two shapes each of the three widgets below uses, and why).
// ---------------------------------------------------------------------------

/** The narrowest this node may be. First set 2026-07-26 (owner report from
 * Linux: "it is possible for the container to be smaller than the content")
 * at 260 for a three-button strip; raised the SAME day when the owner's next
 * ask turned three buttons into six (jump-to-start, −5, −1, +1, +5, and
 * play/pause) -- 260 was sized for half as many and would clip the new ones.
 *
 * The three bars here clamp HEIGHT exhaustively (see the header's sizing
 * section) but nothing clamps WIDTH, so this constant is the one thing
 * standing between a narrow node and a strip that can't render its own
 * buttons. Derivation, CSS-exact terms first:
 *   - Every `.epsfs-btn.epsfs-btn-icon` has a guaranteed floor, not an
 *     estimate: that class sets no `box-sizing`, so its `min-width: 30px`
 *     applies to the CONTENT box alone, with `.epsfs-btn`'s `padding: 3px
 *     8px` (16px horizontal) and 1px border each side (2px) added on top --
 *     30 + 16 + 2 = 48px per button, regardless of how few characters it
 *     holds. True for all SIX buttons here: `⏮ − 5 − 1 + 1 + 5 ▶` are each
 *     1-2 characters, comfortably under the 30px content floor even on
 *     Linux's wider default fonts, so 48px is what actually renders, not a
 *     smaller number this padded up front. 6 x 48 = 288px.
 *   - `.epsfs-frame-input`'s `min-width: 56px` is already its TOTAL
 *     (border-box) floor -- 56px, unchanged from the three-button strip.
 *   - `.epsfs-strip`'s `gap: 6px` applies between all 8 flex children now
 *     (6 buttons + the frame field + the counter) = 7 gaps = 42px.
 *   - `.epsfs-strip` is border-box too: its `padding: 4px 6px` (12px
 *     horizontal) plus a 1px border each side (2px) = 14px is carved OUT of
 *     whatever width the node gives the widget, so it has to be ADDED to
 *     reach the required NODE width.
 *   - 288 + 56 + 42 + 14 = 400px of CSS-exact floors. `.epsfs-counter` has
 *     NO min-width (`min-width: 0`, `flex: 1 1 auto`) and is deliberately
 *     the one element allowed to shrink toward nothing at the floor (file
 *     header's layout note) -- so 400px is the width where the counter is
 *     squeezed to ~0, not a width that overflows.
 *   - The shipped 260 for the OLD three-button strip decomposes the
 *     identical way to 238px of CSS-exact floors (3x48 + 56 + 4x6 + 14),
 *     meaning 260 carried a ~22px allowance beyond bare survival -- for a
 *     visible sliver of counter, and for whatever litegraph node-chrome
 *     inset isn't visible in this file's own CSS (never independently
 *     measured; see this change's verification notes). Carrying a
 *     comparable allowance forward, and rounding up since this is a
 *     proportionally bigger jump (twice the buttons) than that ~22px was
 *     ever stress-tested against, lands on 440.
 * Enforced by `installMinWidth` below, which only ever GROWS a node. */
const MIN_NODE_WIDTH = 440

/** Twin of notebook.js/controller.js's installMinWidth (same rationale and
 * shape; each web module stays self-contained). */
function installMinWidth(node, minWidth) {
  if (!node || node.__epsMinWidthInstalled) return
  node.__epsMinWidthInstalled = true
  const originalOnResize = node.onResize
  node.onResize = function (size) {
    if (size && size[0] < minWidth) size[0] = minWidth
    return originalOnResize?.call(this, size)
  }
  if (Array.isArray(node.size) && node.size[0] < minWidth) {
    node.size[0] = minWidth
  }
}

function attachFillWidget(node, name, type, element, minHeight) {
  installMinWidth(node, MIN_NODE_WIDTH) // 2026-07-26 Linux fix
  const domWidget = node.addDOMWidget(name, type, element, {
    hideOnZoom: true,
    serialize: false, // excludes from the API prompt (utils/executionUtil.ts)
    getMinHeight: () => minHeight
    // Deliberately no getMaxHeight: litegraph's widget-arrange pass then
    // gives this widget whatever height remains after the fixed-height bars
    // above/below it (notebook.js attachDomWidget()'s identical pattern).
  })
  domWidget.serialize = false // excludes from the workflow JSON (a DIFFERENT flag -- see file header)
  domWidget.serializeValue = () => undefined
  return domWidget
}

function attachFixedWidget(node, name, type, element, height) {
  installMinWidth(node, MIN_NODE_WIDTH) // 2026-07-26 Linux fix
  const domWidget = node.addDOMWidget(name, type, element, {
    hideOnZoom: true,
    serialize: false,
    getMinHeight: () => height,
    getMaxHeight: () => height
  })
  // getMinHeight/getMaxHeight ALONE are ignored for a small standalone DOM
  // widget on the Vue-node renderer (cprb/nodes.js's attachBarWidget(): "the
  // bar collapsed to ~7px"). All four knobs together is the robust fix --
  // see file header.
  domWidget.computeSize = (width) => [width, height]
  domWidget.computedHeight = height
  element.style.height = `${height}px`
  element.style.minHeight = `${height}px`
  domWidget.serialize = false
  domWidget.serializeValue = () => undefined
  return domWidget
}

// ---------------------------------------------------------------------------
// UI construction
// ---------------------------------------------------------------------------

function buildPathBar(state) {
  state.browseBtn = el('button', {
    className: 'epsfs-btn',
    text: 'Browse…',
    attrs: { title: 'Pick a video file on the server (never copied into ComfyUI)' }
  })
  state.browseBtn.addEventListener('click', () => {
    if (state.browseBtn.disabled) return
    openPicker(state)
  })
  state.pathTextEl = el('div', { className: 'epsfs-path-text', text: '(no video selected)' })
  state.hostNoteEl = el('div', { className: 'epsfs-host-note' })
  state.pathStatusEl = el('div', { className: 'epsfs-path-status' })
  return el('div', { className: 'epsfs-bar' }, [
    state.browseBtn,
    state.pathTextEl,
    state.hostNoteEl,
    state.pathStatusEl
  ])
}

function buildVideoArea(state) {
  state.videoEl = el('video', { className: 'epsfs-video' })
  state.videoEl.loop = true
  state.videoEl.muted = true
  state.videoEl.playsInline = true
  state.videoEl.preload = 'auto'
  state.videoEl.controls = false // this file owns the whole control surface -- see file header
  state.overlayEl = el('div', { className: 'epsfs-overlay', text: 'No video selected' })
  wireVideoEvents(state)
  return el('div', { className: 'epsfs-videowrap' }, [state.videoEl, state.overlayEl])
}

function buildControlStrip(state) {
  state.jumpStartBtn = el('button', {
    className: 'epsfs-btn epsfs-btn-icon',
    text: '⏮',
    attrs: { title: 'Jump to the first frame' }
  })
  state.stepBack5Btn = el('button', {
    className: 'epsfs-btn epsfs-btn-icon',
    text: '−5',
    attrs: { title: 'Step back 5 frames (hold to keep stepping)' }
  })
  state.stepBackBtn = el('button', {
    className: 'epsfs-btn epsfs-btn-icon',
    text: '−1',
    attrs: { title: 'Step back one frame (hold to keep stepping)' }
  })
  state.stepFwdBtn = el('button', {
    className: 'epsfs-btn epsfs-btn-icon',
    text: '+1',
    attrs: { title: 'Step forward one frame (hold to keep stepping)' }
  })
  state.stepFwd5Btn = el('button', {
    className: 'epsfs-btn epsfs-btn-icon',
    text: '+5',
    attrs: { title: 'Step forward 5 frames (hold to keep stepping)' }
  })
  state.playPauseBtn = el('button', {
    className: 'epsfs-btn epsfs-btn-icon',
    text: '▶',
    attrs: { title: 'Play preview' }
  })
  state.frameInputEl = el('input', {
    className: 'epsfs-frame-input',
    attrs: { type: 'number', min: '0', step: '1', title: 'Frame number' }
  })
  state.counterEl = el('div', { className: 'epsfs-counter', text: 'No video selected' })

  state.jumpStartBtn.addEventListener('click', () => {
    if (state.jumpStartBtn.disabled) return
    setFrame(state, 0) // idempotent -- no hold-repeat wired (file header)
  })
  // −5/−1/+1/+5 all share attachStepButton()'s click + press-and-hold-repeat
  // wiring (file header's "press-and-hold" section); collecting each
  // button's stopRepeat lets a window blur or node removal force any
  // in-flight hold to stop (below / wireNodeCleanup).
  state.stepRepeaters = [
    attachStepButton(state, state.stepBack5Btn, -5),
    attachStepButton(state, state.stepBackBtn, -1),
    attachStepButton(state, state.stepFwdBtn, 1),
    attachStepButton(state, state.stepFwd5Btn, 5)
  ]
  state.playPauseBtn.addEventListener('click', () => togglePlayPause(state))
  state.frameInputEl.addEventListener('change', () => commitFrameInputValue(state))
  state.frameInputEl.addEventListener('keydown', (event) => {
    event.stopPropagation() // don't let litegraph's global shortcuts eat this keystroke
    if (event.key === 'Enter') {
      event.preventDefault()
      commitFrameInputValue(state)
      state.frameInputEl.blur()
    }
  })

  // Losing the window mid-hold (Cmd/Alt-Tab away, a devtools focus steal,
  // ...) never fires pointerup/pointercancel/pointerleave on the button --
  // without this, the repeat interval would keep stepping a node the user
  // can no longer see or interact with. One listener covers all four
  // buttons; removed by wireNodeCleanup's onRemoved wrap.
  state.windowBlurHandler = () => {
    for (const stop of state.stepRepeaters) stop()
  }
  window.addEventListener('blur', state.windowBlurHandler)

  // Order (owner ask 2026-07-26 + the conventional transport layout every
  // video tool uses): jump-to-start, then the two REVERSE steps, play/pause
  // in the MIDDLE, then the two FORWARD steps, then the frame field and the
  // counter. Play/pause deliberately keeps the position it has always had
  // (between -1 and +1) -- the owner asked to ADD buttons, not to move the
  // control he already reaches for, and centering it makes the strip read
  // symmetrically: big-step / small-step / play / small-step / big-step.
  return el('div', { className: 'epsfs-strip' }, [
    state.jumpStartBtn,
    state.stepBack5Btn,
    state.stepBackBtn,
    state.playPauseBtn,
    state.stepFwdBtn,
    state.stepFwd5Btn,
    state.frameInputEl,
    state.counterEl
  ])
}

function buildUi(state) {
  injectStyles()
  const pathBarEl = buildPathBar(state)
  const videoAreaEl = buildVideoArea(state)
  const stripEl = buildControlStrip(state)

  attachFixedWidget(state.node, PATH_BAR_WIDGET_NAME, PATH_BAR_WIDGET_TYPE, pathBarEl, PATH_BAR_HEIGHT)
  attachFillWidget(state.node, VIDEO_WIDGET_NAME, VIDEO_WIDGET_TYPE, videoAreaEl, VIDEO_MIN_HEIGHT)
  attachFixedWidget(state.node, STRIP_WIDGET_NAME, STRIP_WIDGET_TYPE, stripEl, STRIP_HEIGHT)
}

// ---------------------------------------------------------------------------
// Frame state -- the single source of truth is the (hidden) `frame` widget;
// every function below either WRITES it (setFrame/syncFrameFromPlayback, on
// a real change in intent) or just REFRESHES the visible UI from its CURRENT
// value (refreshFrameUi) -- see file header's "Playback vs. programmatic
// seeking" section for why the two write paths are kept separate.
// ---------------------------------------------------------------------------

function clampFrame(state, frame) {
  const n = Number.isFinite(frame) ? Math.trunc(frame) : 0
  const max = state.probe ? Math.max(0, state.probe.frame_count - 1) : Number.MAX_SAFE_INTEGER
  return Math.min(Math.max(n, 0), max)
}

function updateCounterLabel(state, frame) {
  if (!state.counterEl) return
  if (!state.path) {
    state.counterEl.textContent = 'No video selected'
    state.counterEl.title = ''
    return
  }
  const total = state.probe ? String(state.probe.frame_count) : '?'
  state.counterEl.textContent = `Frame ${frame} / ${total}`
  state.counterEl.title = state.probe
    ? `${state.probe.fps.toFixed(3)} fps · ${state.probe.duration.toFixed(2)}s · ${state.probe.width}×${state.probe.height}`
    : ''
}

/** Seeks the PREVIEW to *frame*'s exact time (`frame / fps`, against the
 * PROBED fps -- file header). No-ops when the video can't currently seek
 * (not yet probed, or the codec fallback is active) -- the frame widget/
 * counter/number-input stay correct regardless; only the visual seek is
 * skipped. */
function seekVideoToFrame(state, frame) {
  if (state.videoPlayable !== true || !state.probe) return
  const target = frame / state.probe.fps
  if (Number.isFinite(target)) {
    try {
      state.videoEl.currentTime = target
    } catch (error) {
      warn('video currentTime seek failed', error)
    }
  }
}

/**
 * Writes *frame* to the (hidden) `frame` widget -- unconditionally refreshes
 * the visible number input + counter, but only touches the widget's own
 * `.value`/`.callback`/canvas-dirty when the value actually changed.
 * `dirty: false` (used by the high-frequency playback path,
 * `syncFrameFromPlayback`) skips `setDirtyCanvas` -- nothing CANVAS-visible
 * depends on this hidden widget's value in real time, and redrawing the
 * whole LiteGraph canvas on every `timeupdate` tick while a video plays
 * would be needless overhead.
 */
function commitFrame(state, frame, { dirty = true } = {}) {
  if (state.frameWidget.value !== frame) {
    state.frameWidget.value = frame
    try {
      state.frameWidget.callback?.(frame)
    } catch (error) {
      warn('frame widget callback threw', error)
    }
    if (dirty) state.node.graph?.setDirtyCanvas(true, true)
  }
  if (state.frameInputEl) state.frameInputEl.value = String(frame)
  updateCounterLabel(state, frame)
}

/** User-driven frame change (step buttons, typed number, or a probe-time
 * clamp) -- clamps, commits, AND seeks the preview to match. */
function setFrame(state, frame) {
  const clamped = clampFrame(state, frame)
  commitFrame(state, clamped)
  seekVideoToFrame(state, clamped)
}

/** One frame-step in *delta*'s direction -- the −5/−1/+1/+5 buttons' shared
 * implementation (file header's "press-and-hold" section). Goes through the
 * exact same `clampFrame()`/`setFrame()` every other frame change already
 * uses (no parallel clamp logic), so a plain click, the first press of a
 * hold gesture, and every subsequent auto-repeat tick can never disagree
 * with each other -- or with the typed-number/probe-clamp paths -- about
 * where 0 or the last frame is. Returns `false` WITHOUT calling `setFrame`
 * at all (so no redundant commit/seek) when *delta*'s direction is already
 * exhausted -- frame 0 for a negative delta, the last frame for a positive
 * one -- so `attachStepButton()`'s repeat loop can stop ITSELF the instant
 * it reaches a clamp boundary instead of continuing to poll a no-op every
 * tick for as long as the button stays held. */
function stepFrame(state, delta) {
  const current = Number(state.frameWidget.value)
  const next = clampFrame(state, (Number.isFinite(current) ? current : 0) + delta)
  if (next === current) return false
  setFrame(state, next)
  return true
}

/** Playback-driven sync (`timeupdate` while playing) -- derives the frame
 * from the video's OWN current position and commits it, WITHOUT seeking
 * (the video is already there -- see file header). */
function syncFrameFromPlayback(state) {
  if (!state.probe || state.videoPlayable !== true) return
  const raw = Math.round(state.videoEl.currentTime * state.probe.fps)
  commitFrame(state, clampFrame(state, raw), { dirty: false })
}

/** Read-only refresh of the visible UI from the frame widget's CURRENT
 * value -- never writes the widget. Used for the initial/resync paths
 * (attach, post-configure, post-probe) where the value itself hasn't
 * changed, just what we know about its bounds/preview has. */
function refreshFrameUi(state, { seek = false } = {}) {
  const raw = Number(state.frameWidget.value)
  const frame = clampFrame(state, Number.isFinite(raw) ? Math.round(raw) : 0)
  if (state.frameInputEl) state.frameInputEl.value = String(frame)
  updateCounterLabel(state, frame)
  if (seek) seekVideoToFrame(state, frame)
}

/** Called once right after a probe resolves: if the currently-stored frame
 * is now out of range for THIS video (e.g. a workflow saved against a
 * longer clip, now pointed at a shorter one), clamp it for real -- keeping
 * an unreachable value serialized would be misleading, and `extract_frame`
 * would silently clamp it anyway at Run time (FORMAT.md §6.7). Otherwise
 * just refresh the display and seek the (now-known-playable-or-not)
 * preview to the still-valid current frame. */
function clampFrameToProbeBounds(state) {
  if (!state.probe) return
  const current = Number(state.frameWidget.value)
  const clamped = clampFrame(state, Number.isFinite(current) ? Math.round(current) : 0)
  if (clamped !== current) {
    setFrame(state, clamped)
  } else {
    refreshFrameUi(state, { seek: true })
  }
}

function commitFrameInputValue(state) {
  const parsed = Number.parseInt(state.frameInputEl.value, 10)
  setFrame(state, Number.isFinite(parsed) ? parsed : 0)
}

// ---------------------------------------------------------------------------
// Press-and-hold auto-repeat for the −5/−1/+1/+5 buttons (owner ask
// 2026-07-26) -- see file header for the full citation/rationale. NOT wired
// onto jump-to-start (buildControlStrip) -- it's idempotent, so "repeat"
// wouldn't mean anything.
// ---------------------------------------------------------------------------

/**
 * Wires *button* so a plain press (a click, or a tap/press released before
 * the initial delay) performs exactly ONE `stepFrame(state, delta)`, and a
 * longer press auto-repeats it every `HOLD_REPEAT_INTERVAL_MS` once
 * `HOLD_INITIAL_DELAY_MS` has elapsed -- see file header for the full
 * click-vs-hold / Pointer Events rationale. Returns `stopRepeat` so the
 * caller can force a stop from outside the gesture itself (a window `blur`,
 * or the node's own `onRemoved`).
 * @param {object} state
 * @param {HTMLButtonElement} button
 * @param {number} delta
 * @returns {() => void} stopRepeat -- idempotent, safe to call any number of
 * times (including when nothing is in flight).
 */
function attachStepButton(state, button, delta) {
  let holdTimer = null
  let repeatInterval = null
  let activePointerId = null

  const stopRepeat = () => {
    if (holdTimer !== null) {
      clearTimeout(holdTimer)
      holdTimer = null
    }
    if (repeatInterval !== null) {
      clearInterval(repeatInterval)
      repeatInterval = null
    }
    activePointerId = null
  }

  const repeatTick = () => {
    if (!stepFrame(state, delta)) stopRepeat() // clamp boundary reached -- see stepFrame's doc
  }

  button.addEventListener('pointerdown', (event) => {
    if (button.disabled) return
    if (event.pointerType === 'mouse' && event.button !== 0) return
    stopRepeat() // at most one gesture in flight per button
    activePointerId = event.pointerId
    try {
      button.setPointerCapture(event.pointerId)
    } catch {
      // Older/partial Pointer Events: degrade to press-and-release-in-place
      // (gallery.js's attachHoldToCompare's identical fallback).
    }
    stepFrame(state, delta) // the press itself -- the same single step a plain click gives
    holdTimer = setTimeout(() => {
      holdTimer = null
      repeatTick() // first repeat tick right at the delay boundary
      // repeatTick() may have already called stopRepeat() (the very first
      // tick landed on a clamp boundary -- e.g. holding −1 from frame 0) --
      // activePointerId is null in exactly that case (nothing else clears
      // it between here and the check), and NOT creating the interval at
      // all is what keeps this a true zero-extra-ticks stop rather than
      // one wasted tick that then cancels itself on ITS OWN next firing.
      if (activePointerId !== null) {
        repeatInterval = setInterval(repeatTick, HOLD_REPEAT_INTERVAL_MS)
      }
    }, HOLD_INITIAL_DELAY_MS)
  })

  const release = (event) => {
    if (activePointerId === null || event.pointerId !== activePointerId) return
    stopRepeat()
  }
  button.addEventListener('pointerup', release)
  button.addEventListener('pointercancel', release)
  button.addEventListener('pointerleave', release)

  // Keyboard activation only: Enter fires 'click' on every repeated keydown
  // while held, Space fires it once on release -- both arrive here as an
  // ordinary 'click' with `detail === 0` (the standard cross-browser tell
  // for a keyboard-synthesized click; a real pointer click's detail is >= 1
  // and was ALREADY stepped by pointerdown above -- acting on it again here
  // would double-step, which is exactly what this guard prevents).
  button.addEventListener('click', (event) => {
    if (button.disabled) return
    if (event.detail !== 0) return
    stepFrame(state, delta)
  })

  return stopRepeat
}

// ---------------------------------------------------------------------------
// Playback controls
// ---------------------------------------------------------------------------

function togglePlayPause(state) {
  if (!state.videoEl || state.videoPlayable !== true) return
  if (state.videoEl.paused) {
    state.videoEl.play().catch((error) => warn('video play() rejected', error))
  } else {
    state.videoEl.pause()
  }
}

function updatePlayPauseIcon(state) {
  if (!state.playPauseBtn) return
  const playing = Boolean(state.videoEl) && !state.videoEl.paused && !state.videoEl.ended
  state.playPauseBtn.textContent = playing ? '⏸' : '▶'
  state.playPauseBtn.title = playing ? 'Pause preview' : 'Play preview'
}

function wireVideoEvents(state) {
  const video = state.videoEl
  video.addEventListener('loadedmetadata', () => {
    state.videoPlayable = true
    updateOverlayUi(state)
    refreshFrameUi(state, { seek: true }) // land the preview on the stored frame, not frame 0
  })
  video.addEventListener('error', () => {
    // FORMAT.md §6.7's exotic-codec fallback -- see file header. Probe data
    // (if any) is left untouched: it came from PyAV server-side and is
    // independent of what THIS browser can decode.
    state.videoPlayable = false
    updateOverlayUi(state)
  })
  video.addEventListener('play', () => updatePlayPauseIcon(state))
  video.addEventListener('pause', () => updatePlayPauseIcon(state))
  video.addEventListener('timeupdate', () => {
    if (!video.paused) syncFrameFromPlayback(state)
  })
  video.addEventListener('click', () => togglePlayPause(state))
}

// ---------------------------------------------------------------------------
// Overlay / control-enablement (FORMAT.md §6.7's "fail soft throughout")
// ---------------------------------------------------------------------------

function currentOverlayMessage(state) {
  if (state.wired?.kind === 'opaque') {
    // §6.7 v0.60.0's honest degrade: the video only exists once the graph
    // runs, so the preview cannot show it -- but frame selection still
    // works by number, and extraction clamps past-the-end as always.
    return (
      `Video arrives from "${state.wired.title}" when the workflow runs — ` +
      'type a frame number below to pick the frame.'
    )
  }
  if (!state.path && !state.wired) {
    return 'No video selected — Browse for a video file, or wire a video into the video input.'
  }
  if (!state.wired && state.isLocal === false) {
    // input_ref previews work remotely (§6.7 v0.60.0) -- only PATH mode
    // needs the host machine.
    return 'Preview + probing require the machine running ComfyUI — Run still works.'
  }
  if (state.videoPlayable === false) {
    return "Preview unavailable for this codec — Run still extracts the exact frame."
  }
  if (state.videoPlayable === null) return 'Loading preview…'
  return '' // playable -- overlay hidden, video shown.
}

function updateOverlayUi(state) {
  const message = currentOverlayMessage(state)
  if (state.overlayEl) {
    state.overlayEl.textContent = message
    state.overlayEl.style.display = message ? '' : 'none'
  }
  if (state.videoEl) {
    state.videoEl.style.visibility = message ? 'hidden' : 'visible'
  }
  updateControlsEnabled(state)
}

function updateControlsEnabled(state) {
  // A wired video counts as a source (§6.7 v0.60.0): with an input_ref
  // upstream everything works as for a path; with an OPAQUE upstream the
  // number-driven controls still work (no probe means no upper clamp --
  // extraction clamps past-the-end server-side regardless).
  const hasSource = Boolean(state.path) || Boolean(state.wired)
  // Jump/step/number-input stay enabled whenever a source is set,
  // REGARDLESS of preview playability -- they only need the PROBED
  // fps/frame_count (decoded server-side, independent of what this browser
  // can render), so they keep working even when the <video> preview itself
  // has degraded.
  if (state.frameInputEl) state.frameInputEl.disabled = !hasSource
  if (state.jumpStartBtn) state.jumpStartBtn.disabled = !hasSource
  if (state.stepBack5Btn) state.stepBack5Btn.disabled = !hasSource
  if (state.stepBackBtn) state.stepBackBtn.disabled = !hasSource
  if (state.stepFwdBtn) state.stepFwdBtn.disabled = !hasSource
  if (state.stepFwd5Btn) state.stepFwd5Btn.disabled = !hasSource
  // Play/pause is the one control that genuinely needs a playable preview.
  if (state.playPauseBtn) state.playPauseBtn.disabled = !hasSource || state.videoPlayable !== true
}

// ---------------------------------------------------------------------------
// Path changes -- Browse pick, workflow load/undo resync, or gating flip.
// ---------------------------------------------------------------------------

function updatePathBarText(state) {
  if (!state.pathTextEl) return
  if (state.wired) {
    // §6.7 v0.60.0: the wire wins, so the bar names the wire -- showing
    // the (ignored) browsed path here would misstate what will run.
    const label =
      state.wired.kind === 'input_ref'
        ? `⇐ wired: ${state.wired.ref} (${state.wired.title})`
        : `⇐ wired: ${state.wired.title}`
    state.pathTextEl.textContent = frontTruncate(label)
    state.pathTextEl.title = label
    return
  }
  const text = state.path || '(no video selected)'
  state.pathTextEl.textContent = frontTruncate(text)
  state.pathTextEl.title = state.path || ''
}

function setPathBarStatus(state, message, isError = false) {
  if (!state.pathStatusEl) return
  state.pathStatusEl.textContent = message || ''
  state.pathStatusEl.title = message || ''
  state.pathStatusEl.classList.toggle('epsfs-path-status-error', Boolean(isError))
}

/**
 * `GET /eps_frame_saver/probe` for *path*, guarded by `state.probeToken` so
 * a slow response from a since-superseded path can never clobber current
 * state (mirrors `notebook.js`'s identical `loadToken` guard).
 */
async function startProbe(state, params) {
  const token = state.probeToken
  setPathBarStatus(state, '')
  try {
    const data = await getJson('/eps_frame_saver/probe', params)
    if (token !== state.probeToken) return // superseded by a later path change
    state.probe = data
    clampFrameToProbeBounds(state)
  } catch (error) {
    if (token !== state.probeToken) return
    state.probe = null
    // Self-healing remote detection (2026-07-30, remote-regression audit):
    // `refreshGating` fails OPEN -- one failed /config fetch at attach time
    // leaves `isLocal` null (treated local) forever, and a remote viewer
    // then reaches this catch with the probe route's raw loopback-only 403,
    // which reads like a broken node rather than a documented boundary. The
    // 403 itself IS the ground truth that this viewer is remote, so adopt
    // it: flip the gating (which hides Browse, shows the host note, and
    // clears the video via refreshVideoSource's remote branch) and show the
    // same calm message the properly-gated path uses. PATH mode only:
    // input_ref probes are deliberately ungated (§6.7 v0.60.0), so a 403
    // there would be something else entirely -- fall through to the
    // generic error text instead of mis-flipping the gating.
    if (error?.status === 403 && params.path) {
      state.isLocal = false
      applyGating(state) // also re-runs refreshVideoSource's remote branch
      setPathBarStatus(
        state,
        'Video preview and probing run on the machine hosting ComfyUI -- ' +
          'open ComfyUI there to scrub this video. The saved frames still ' +
          'flow through the graph normally from here.'
      )
      return
    }
    setPathBarStatus(state, error.message || 'Could not probe this video.', true)
    refreshFrameUi(state)
  }
}

/**
 * Re-derives the preview (video `src` + probe fetch) from `state.path` +
 * `state.isLocal`. Called after every path change AND after gating
 * resolves/changes (`applyGating`) -- idempotent, safe to call redundantly.
 */
function refreshVideoSource(state) {
  state.videoPlayable = null
  state.probe = null
  state.probeToken += 1

  if (state.wired?.kind === 'input_ref') {
    // §6.7 v0.60.0: an upstream LoadVideo's file, streamed via the routes'
    // input_ref mode -- deliberately NOT gated on isLocal (an input-dir
    // file is exposed to every viewer by core's own /view already), so the
    // scrubber works from a remote browser too.
    const url = api.apiURL(`/eps_frame_saver/stream?input_ref=${encodeURIComponent(state.wired.ref)}`)
    state.videoEl.src = url
    state.videoEl.load()
    startProbe(state, { input_ref: state.wired.ref })
  } else if (state.wired) {
    // Opaque wired source: the video only exists at run time. No preview
    // to try; the overlay says so and the frame FIELD stays live.
    state.videoEl.removeAttribute('src')
    state.videoEl.load()
  } else if (!state.path || state.isLocal === false) {
    // Empty path, or a remote viewer (the stream/probe routes are
    // loopback-only regardless -- FORMAT.md §6.7 -- so don't even try;
    // avoids a confusing generic video `error` event standing in for "this
    // requires the host machine").
    state.videoEl.removeAttribute('src')
    state.videoEl.load()
  } else {
    const url = api.apiURL(`/eps_frame_saver/stream?path=${encodeURIComponent(state.path)}`)
    state.videoEl.src = url
    state.videoEl.load()
    startProbe(state, { path: state.path })
  }
  updateOverlayUi(state)
  refreshFrameUi(state)
}

function onPathChanged(state, rawPath) {
  state.path = String(rawPath || '').trim()
  updatePathBarText(state)
  setPathBarStatus(state, '')
  refreshVideoSource(state)
}

/**
 * §6.7 v0.60.0: what is wired into the node's `video` input, followed
 * through reroutes. An upstream core `LoadVideo` is statically knowable
 * from its `file` widget (the exact annotated value the routes' input_ref
 * mode resolves); anything else is a run-time video -- opaque here.
 * @param {object} node
 * @returns {null | {kind: string, ref?: string, title: string}}
 */
function resolveWiredVideo(node) {
  const slot = (node.inputs || []).findIndex((input) => input && input.name === 'video')
  if (slot === -1) return null
  let current = node
  let currentSlot = slot
  for (let hops = 0; hops < 32; hops++) {
    const link_id = current.inputs?.[currentSlot]?.link
    if (link_id == null) return null
    const link = current.graph?.links?.[link_id] ?? current.graph?.links?.get?.(link_id)
    if (!link) return null
    const upstream = current.graph?.getNodeById?.(link.origin_id)
    if (!upstream) return null
    if (upstream.type === 'Reroute' || upstream.type === 'Reroute (rgthree)') {
      current = upstream
      currentSlot = 0
      continue
    }
    const upstreamClass = upstream.comfyClass ?? upstream.constructor?.comfyClass ?? upstream.type
    const title = upstream.title || upstreamClass
    if (upstreamClass === 'LoadVideo') {
      const fileWidget = (upstream.widgets || []).find((w) => w && w.name === 'file')
      const ref = typeof fileWidget?.value === 'string' ? fileWidget.value.trim() : ''
      if (ref) return { kind: 'input_ref', ref, title }
    }
    return { kind: 'opaque', title }
  }
  return { kind: 'opaque', title: '(reroute loop)' }
}

/** Re-derives EVERYTHING from the widgets' CURRENT values AND the wire
 * state -- the single entry point all restore-timing hooks (and the
 * v0.60.0 onConnectionsChange wrap) call (see file header). */
function fullResync(state) {
  state.wired = resolveWiredVideo(state.node)
  onPathChanged(state, state.pathWidget.value)
}

// ---------------------------------------------------------------------------
// Browse picker (FORMAT.md §6.7's "Browse a file server-side") -- trimmed,
// file-mode-only port of `cprb/nodes.js`'s `openPicker` family; see file
// header for the full citation.
// ---------------------------------------------------------------------------

function closePicker(state) {
  document.getElementById(PICKER_OVERLAY_ID)?.remove()
  if (state.pickerKeydownHandler) {
    // Same capture flag as the registration in openPicker(), or this
    // silently fails to detach (same rule as the paste listener below).
    window.removeEventListener('keydown', state.pickerKeydownHandler, { capture: true })
    state.pickerKeydownHandler = null
  }
  state.pickerPathInputEl = null
}

/** Whether *value* is an absolute path in SOME supported OS shape -- the
 * picker's start-dir choice AND paste-a-path's "is this even a path" gate
 * (exported for the Node tests). Deliberately shallow: real validation is
 * the server's (`routes_frame_saver.py`'s `_validate_video_path`). */
export function looksAbsolutePath(value) {
  const trimmed = typeof value === 'string' ? value.trim() : ''
  if (!trimmed) return false
  if (trimmed.startsWith('/')) return true // POSIX
  if (/^[a-zA-Z]:[\\/]/.test(trimmed)) return true // Windows drive, e.g. C:\ or C:/
  if (trimmed.startsWith('\\\\')) return true // UNC, e.g. \\server\share
  return false
}

function dirnameOfServerPath(path) {
  const trimmed = typeof path === 'string' ? path.trim() : ''
  if (!trimmed) return null
  const sep = trimmed.includes('\\') && !trimmed.includes('/') ? '\\' : '/'
  const idx = trimmed.lastIndexOf(sep)
  if (idx <= 0) return null
  return trimmed.slice(0, idx)
}

/**
 * @param {string} dir
 * @param {string} name
 * @param {string} [sep] - the server-reported `sep` (`fs/list`'s response
 * field) -- preferred when given, since it's authoritative for the machine
 * actually being browsed.
 */
function joinServerPath(dir, name, sep) {
  const separator = sep || (dir.includes('\\') && !dir.includes('/') ? '\\' : '/')
  return dir.endsWith(separator) ? `${dir}${name}` : `${dir}${separator}${name}`
}

function openPicker(state) {
  closePicker(state) // only one picker at a time, ever
  injectStyles()

  const backdrop = el('div', { className: 'epsfs-picker-backdrop', attrs: { id: PICKER_OVERLAY_ID } })
  const dialog = el('div', { className: 'epsfs-picker' })
  backdrop.append(dialog)
  backdrop.addEventListener('mousedown', (event) => {
    if (event.target === backdrop) closePicker(state)
  })
  dialog.addEventListener('mousedown', (event) => event.stopPropagation())
  document.body.append(backdrop)

  state.pickerKeydownHandler = (event) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      closePicker(state)
    }
  }
  // CAPTURE-phase, deliberately (FORMAT.md §7.5's window-level-listener
  // rule; same reasoning as installPastePathHandler below): the picker
  // path bar's own keydown handler stopPropagation()s every key, so a
  // bubble-phase listener never sees Escape while the input has focus --
  // the state the user is in right after typing a path. closePicker() must
  // remove with the same flag.
  window.addEventListener('keydown', state.pickerKeydownHandler, { capture: true })

  // The navigable area is its own child so the path bar built next (which
  // sits above it, for the picker's whole lifetime) is never wiped out by
  // loadPickerDir()'s replaceChildren() calls.
  const content = el('div', { className: 'epsfs-picker-content' })
  dialog.append(buildPickerPathBar(state, content), content)

  const startDir = looksAbsolutePath(state.path) ? dirnameOfServerPath(state.path) : null
  loadPickerDir(state, content, startDir)
}

function buildPickerPathBar(state, content) {
  const input = el('input', {
    className: 'epsfs-picker-path-input',
    attrs: {
      type: 'text',
      placeholder: String.raw`\\server\share or D:\clips`,
      spellcheck: 'false',
      autocomplete: 'off'
    }
  })
  const goBtn = el('button', {
    className: 'epsfs-btn epsfs-btn-small',
    text: 'Go',
    attrs: { title: 'Go to this path' }
  })

  const goToTypedPath = () => {
    const typed = input.value.trim()
    if (!typed) return
    loadPickerDir(state, content, typed)
  }
  goBtn.addEventListener('click', goToTypedPath)
  input.addEventListener('keydown', (event) => {
    event.stopPropagation()
    if (event.key !== 'Enter') return
    event.preventDefault()
    goToTypedPath()
  })

  state.pickerPathInputEl = input
  return el('div', { className: 'epsfs-picker-pathbar' }, [input, goBtn])
}

async function loadPickerDir(state, content, dir) {
  // A lapped response -- success OR failure -- must not render: the
  // navigation that superseded this one owns the dialog (and the path
  // input renderPickerDialog rewrites). See pickerNavToken's state
  // comment; same guard shape as startProbe's probeToken.
  const navToken = ++state.pickerNavToken
  content.replaceChildren(el('div', { className: 'epsfs-picker-status', text: 'Loading…' }))
  const params = { ext: VIDEO_EXT_PARAM }
  if (dir) params.dir = dir
  let data
  try {
    data = await getJson('/lora_library/fs/list', params)
  } catch (error) {
    if (navToken !== state.pickerNavToken) return // superseded by a later navigation
    content.replaceChildren(
      el('div', {
        className: 'epsfs-picker-header',
        text: frontTruncate(dir || 'Browse'),
        attrs: { title: dir || '' }
      }),
      el('div', {
        className: 'epsfs-picker-status epsfs-picker-error',
        text: `Could not list folder: ${error.message}`
      }),
      buildPickerFooter(state)
    )
    return
  }
  if (navToken !== state.pickerNavToken) return // superseded by a later navigation
  renderPickerDialog(state, content, data)
}

function renderPickerDialog(state, content, data) {
  if (state.pickerPathInputEl) {
    state.pickerPathInputEl.value = data.dir === FS_ROOTS ? '' : data.dir
  }

  const isRootsList = data.dir === FS_ROOTS
  const headerText = isRootsList ? 'Top Level' : data.dir
  const header = el('div', {
    className: 'epsfs-picker-header',
    text: frontTruncate(headerText),
    attrs: { title: headerText }
  })
  const list = el('div', { className: 'epsfs-picker-list' })

  if (data.parent !== null) {
    const upRow = el('div', { className: 'epsfs-picker-row', text: '.. (parent folder)' })
    upRow.addEventListener('click', () => loadPickerDir(state, content, data.parent))
    list.append(upRow)
  }
  for (const dirEntry of data.dirs || []) {
    const row = el('div', {
      className: 'epsfs-picker-row',
      text: isRootsList ? dirEntry.name : `${dirEntry.name}/`
    })
    const target = isRootsList ? dirEntry.path : joinServerPath(data.dir, dirEntry.name, data.sep)
    row.addEventListener('click', () => loadPickerDir(state, content, target))
    list.append(row)
  }
  for (const file of data.files || []) {
    const row = el('div', { className: 'epsfs-picker-row', text: file.name })
    row.addEventListener('click', () => {
      const path = joinServerPath(data.dir, file.name, data.sep)
      closePicker(state)
      chooseVideoPath(state, path)
    })
    list.append(row)
  }
  const hasFiles = (data.files || []).length > 0
  if (data.parent === null && !(data.dirs || []).length && !hasFiles) {
    list.append(el('div', { className: 'epsfs-picker-empty', text: 'No subfolders or video files here.' }))
  }

  content.replaceChildren(header, list, buildPickerFooter(state))
}

function buildPickerFooter(state) {
  const cancelBtn = el('button', { className: 'epsfs-btn epsfs-btn-small', text: 'Cancel' })
  cancelBtn.addEventListener('click', () => closePicker(state))
  return el('div', { className: 'epsfs-picker-footer' }, [cancelBtn])
}

/** Writes the chosen path through the real `video_path` widget (so it
 * serializes/reaches `execute()` exactly like typing it would), then runs
 * the same path-change flow every other trigger uses. */
function chooseVideoPath(state, path) {
  writeWidgetValue(state.pathWidget, state.node, path)
  onPathChanged(state, path)
}

// ---------------------------------------------------------------------------
// Paste a path (owner ask 2026-07-21) -- Ctrl/Cmd+V with this node solely
// selected sets `video_path` from the clipboard's TEXT, through the SAME
// `chooseVideoPath()` the Browse rows call. See the file header's "Paste a
// path onto the node" section for the secure-context/gating rationale; the
// text-cleanup + verdict helpers here are PURE and exported so
// `tests/test_frame_saver_paste_js.py` can drive them under Node.
// ---------------------------------------------------------------------------

/** Longest clipboard text still treated as a path candidate -- POSIX
 * PATH_MAX is 4096 and even Windows extended paths stay well under 32k;
 * anything longer is a pasted document, not a path. */
const MAX_PASTED_PATH_LENGTH = 4096

/** The first line of *text* with non-whitespace content, trimmed ('' when
 * there is none). Finder's "Copy as Pathname" with SEVERAL files selected
 * puts one path per line -- this node holds exactly one video, so the
 * first is taken and the rest are ignored. */
function firstNonEmptyLine(text) {
  for (const line of String(text ?? '').split(/\r\n|\r|\n/)) {
    const trimmed = line.trim()
    if (trimmed) return trimmed
  }
  return ''
}

/**
 * *text* minus ONE pair of matching wrapping quotes (double or single),
 * trimmed. Windows Explorer's "Copy as path" wraps the path in double
 * quotes; shell-copied paths are often single-quoted. Exactly one pair,
 * never a loop -- a quote character INSIDE a filename must survive, and no
 * OS stacks wrappers.
 * @param {string} text
 */
export function stripWrappingQuotes(text) {
  const value = typeof text === 'string' ? text.trim() : ''
  if (value.length >= 2) {
    const first = value[0]
    const last = value[value.length - 1]
    if ((first === '"' && last === '"') || (first === "'" && last === "'")) {
      return value.slice(1, -1).trim()
    }
  }
  return value
}

/**
 * A `file://` URL decoded to the plain filesystem path the SERVER can
 * open; any other *text* is returned unchanged. Copying from a browser's
 * address bar (or some file managers) yields `file:///...` with
 * percent-escapes -- the server's `Path()` needs the real characters.
 * Shapes handled (WHATWG `URL` does the parsing, so the Node tests
 * exercise the browser's own algorithm):
 *   - `file:///Users/e/My%20Videos/a.mp4` -> `/Users/e/My Videos/a.mp4`
 *   - `file:///C:/clips/a.mp4` -> `C:/clips/a.mp4` (drive form; forward
 *     slashes kept -- Python's `Path` accepts them on Windows)
 *   - `file://localhost/Users/e/a.mp4` -> `/Users/e/a.mp4`
 *   - `file://nas/share/a.mp4` -> `\\nas\share\a.mp4` (a real host maps
 *     to a UNC path, backslashed for Windows)
 * A malformed percent-escape keeps the raw pathname (decode fails soft);
 * an unparseable URL returns *text* unchanged -- it then simply fails
 * `looksAbsolutePath` and the paste is ignored.
 * @param {string} text
 */
export function fileUrlToPath(text) {
  const value = typeof text === 'string' ? text.trim() : ''
  if (!/^file:\/\//i.test(value)) return value
  let url
  try {
    url = new URL(value)
  } catch {
    return value
  }
  let pathname = url.pathname
  try {
    pathname = decodeURIComponent(pathname)
  } catch {
    // Malformed %-sequence in a real filename -- keep the raw pathname.
  }
  const host = url.hostname
  if (host && host.toLowerCase() !== 'localhost') {
    return `\\\\${host}${pathname.replace(/\//g, '\\')}`
  }
  if (/^\/[A-Za-z]:/.test(pathname)) return pathname.slice(1)
  return pathname
}

/**
 * Clipboard text -> the path candidate it carries ('' when it holds none):
 * first non-empty line, one pair of wrapping quotes stripped, `file://`
 * decoded, trimmed. Judging the result (absolute? video?) is
 * `evaluatePastedText`'s job -- this only normalizes what the OS put on
 * the clipboard.
 * @param {string} text
 */
export function cleanPastedVideoPath(text) {
  return fileUrlToPath(stripWrappingQuotes(firstNonEmptyLine(text))).trim()
}

/**
 * *path*'s lowercased dot-extension ('' when it has none) -- mirrors
 * Python `Path.suffix`'s semantics (last dot of the LAST component; a
 * leading dot (`.hidden`) or trailing dot (`name.`) is NO extension) so
 * the early client check below and `routes_frame_saver.py`'s
 * `_validate_video_path` reach the same verdict on the same path.
 * @param {string} path
 */
export function pathExtension(path) {
  const value = typeof path === 'string' ? path : ''
  const segments = value.split(/[\\/]/)
  const base = segments[segments.length - 1] || ''
  const idx = base.lastIndexOf('.')
  if (idx <= 0 || idx >= base.length - 1) return ''
  return base.slice(idx).toLowerCase()
}

/**
 * The paste handler's whole decision, as data (pure -- the Node tests
 * drive this exact function):
 *   - `{action: 'ignore', path: ''}` -- not path-shaped (empty, relative,
 *     prose, workflow JSON, over-long): do NOT consume the event, so
 *     core's own paste pipeline still sees it untouched.
 *   - `{action: 'reject', path, reason}` -- an absolute path whose
 *     extension the video allowlist (and therefore the server, kept in
 *     lockstep -- :data:`VIDEO_EXT_LIST`) is guaranteed to refuse:
 *     consume + surface *reason*, but leave the currently-loaded path
 *     UNTOUCHED rather than clobbering a working video with a
 *     guaranteed-to-fail one.
 *   - `{action: 'accept', path}` -- an absolute path with an allowlisted
 *     extension, or with NO extension at all (deliberately not judged
 *     client-side: the probe route stays the real validator, its error
 *     landing on the path bar exactly as for a Browse pick).
 * @param {string} text
 */
export function evaluatePastedText(text) {
  const path = cleanPastedVideoPath(text)
  if (!path || path.length > MAX_PASTED_PATH_LENGTH || !looksAbsolutePath(path)) {
    return { action: 'ignore', path: '' }
  }
  const ext = pathExtension(path)
  if (ext && !VIDEO_EXT_LIST.includes(ext)) {
    return {
      action: 'reject',
      path,
      reason: `Not a video file (${ext}) — allowed: ${VIDEO_EXT_LIST.join(', ')}`
    }
  }
  return { action: 'accept', path }
}

/**
 * True when *element* is a text-entry surface whose paste must be left
 * alone -- any `<input>`/`<textarea>`/`<select>` (conservatively including
 * non-text input types) or anything contenteditable. Duck-typed on
 * `tagName`/`isContentEditable` rather than instanceof so it stays pure
 * (Node-testable) and works across realms.
 * @param {object|null} element
 */
export function isTextEntryElement(element) {
  if (!element) return false
  const tag = typeof element.tagName === 'string' ? element.tagName.toUpperCase() : ''
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
  return element.isContentEditable === true
}

/**
 * True when *node* is the ONLY node currently selected on the canvas --
 * `app.canvas.selected_nodes` is litegraph's live id->node selection map
 * (the same `app.canvas` access `resolution.js`/`switcher.js` already
 * use). With 2+ nodes selected the paste target would be ambiguous, so
 * nothing acts. Fails CLOSED (no canvas / unexpected shape reads as
 * not-selected): a paste that silently no-ops beats one that hijacks text
 * meant for something else.
 */
function isSoleSelectedNode(node) {
  const selected = app.canvas?.selected_nodes
  if (!selected || typeof selected !== 'object') return false
  const nodes = Object.values(selected)
  return nodes.length === 1 && nodes[0] === node
}

/**
 * The per-instance document `paste` listener (file header, "Paste a path
 * onto the node"). Reads `event.clipboardData` -- the DOM paste event's
 * payload, available on INSECURE contexts (the owner's Mac viewing the PC
 * over plain http) where `navigator.clipboard.readText()` simply does not
 * exist -- and only ever CONSUMES the event (preventDefault +
 * stopImmediatePropagation, which the capture-phase registration lets run
 * ahead of core's bubble-phase `usePaste` listener) once the text is
 * judged path-shaped for THIS solely-selected node with no text field
 * focused. Never throws -- a paste handler must not break the document's
 * dispatch (pack fail-soft posture).
 */
function onPastePathEvent(state, event) {
  try {
    if (!isSoleSelectedNode(state.node)) return
    if (isTextEntryElement(event.target) || isTextEntryElement(document.activeElement)) return
    const text = event.clipboardData ? event.clipboardData.getData('text') : ''
    const verdict = evaluatePastedText(text)
    if (verdict.action === 'ignore') return
    event.preventDefault()
    event.stopImmediatePropagation()
    if (verdict.action === 'reject') {
      setPathBarStatus(state, verdict.reason, true)
      toast(state.node, 'warn', verdict.reason)
      return
    }
    // Same code path as a Browse pick -- writes the widget, probes,
    // (re)loads the <video>, updates the counter. A remote viewer gets
    // refreshVideoSource's existing isLocal short-circuit (host-only
    // overlay, no probe attempt -- Run still works, the SERVER reads it).
    chooseVideoPath(state, verdict.path)
  } catch (error) {
    warn('paste-a-path handler failed', error)
  }
}

/** Registers *state*'s document-level paste listener. Capture-phase on
 * purpose: core's `usePaste` listener is bubble-phase on this same
 * document and registered earlier (app boot precedes node attach), so
 * capture is the one registration that still lets a CONSUMED path-shaped
 * paste preempt it; an ignored paste propagates exactly as if this
 * listener didn't exist. */
function installPastePathHandler(state) {
  state.pasteHandler = (event) => onPastePathEvent(state, event)
  document.addEventListener('paste', state.pasteHandler, true)
}

/** Removes the paste listener (same capture flag -- removal must match
 * registration). Called from wireNodeCleanup's onRemoved wrap, so a
 * deleted node never leaks a document-level listener. */
function removePastePathHandler(state) {
  if (!state.pasteHandler) return
  document.removeEventListener('paste', state.pasteHandler, true)
  state.pasteHandler = null
}

// ---------------------------------------------------------------------------
// Node lifecycle
// ---------------------------------------------------------------------------

function wireNodeCleanup(state) {
  const node = state.node
  const originalOnRemoved = node.onRemoved
  node.onRemoved = function (...args) {
    let result
    if (typeof originalOnRemoved === 'function') {
      try {
        result = originalOnRemoved.apply(this, args)
      } catch (error) {
        warn('original node onRemoved threw', error)
      }
    }
    try {
      closePicker(state)
      removePastePathHandler(state)
      // Force-stop any in-flight hold-repeat before tearing down the window
      // listener that would otherwise do it (file header's "press-and-hold"
      // section) -- a node deleted mid-hold must never leave a repeat
      // interval running against widgets that are about to be gone.
      for (const stop of state.stepRepeaters) stop()
      if (state.windowBlurHandler) {
        window.removeEventListener('blur', state.windowBlurHandler)
        state.windowBlurHandler = null
      }
      nodeStates.delete(node)
    } catch (error) {
      warn('frame_saver teardown failed', error)
    }
    return result
  }
}

/** Undo/redo re-applies a node's prior serialized state via
 * `LGraphNode.configure()` directly -- it does NOT recreate the node
 * instance, so `nodeCreated`/`attach()` never runs again for it (see file
 * header's citation of `eps_image/image_grid.js`'s identical finding for its
 * own uuid dedup). Wrapping `onConfigure` here is the belt-and-suspenders
 * third resync path alongside the deferred `attach()` call and
 * `loadedGraphNode()` below -- all three landing on the same idempotent
 * `fullResync()`. */
function wireConfigureResync(state) {
  const node = state.node
  const originalOnConfigure = node.onConfigure
  node.onConfigure = function (...args) {
    const result =
      typeof originalOnConfigure === 'function' ? originalOnConfigure.apply(this, args) : undefined
    try {
      fullResync(state)
    } catch (error) {
      warn('post-configure resync failed', error)
    }
    return result
  }
}

// ---------------------------------------------------------------------------
// Public entry points (called from web/eps_image.js)
// ---------------------------------------------------------------------------

/** EPSFrameSaver is a real backend node (no frontend-only type registration
 * needed) -- everything here is per-instance, done in attach(). Kept as an
 * export because eps_image.js calls it unconditionally. */
export function init() {}

/** Per-node-instance attach; no-op unless *node* is an EPSFrameSaver. */
export function attach(node) {
  try {
    if (!node) return
    if (nodeClassOf(node) !== CLASS_ID) return
    if (nodeStates.has(node)) return
    if (typeof node.addDOMWidget !== 'function') {
      warn('this ComfyUI frontend has no addDOMWidget; frame_saver player not attached')
      return
    }

    const pathWidget = findWidget(node, PATH_WIDGET_NAME)
    const frameWidget = findWidget(node, FRAME_WIDGET_NAME)
    if (!pathWidget || !frameWidget) {
      warn('EPSFrameSaver node is missing its video_path/frame widgets; player not attached')
      return
    }

    const state = createState(node, pathWidget, frameWidget)
    nodeStates.set(node, state)

    buildUi(state)
    hideWidget(node, pathWidget)
    hideWidget(node, frameWidget)
    wireNodeCleanup(state)
    wireConfigureResync(state)
    // §6.7 v0.60.0: (un)wiring the `video` input changes the whole source
    // story -- re-derive through the same fullResync every restore hook
    // uses. Chained + guarded, the pack's standard wrap; fires in BOTH
    // renderers (a node hook, not a canvas one).
    const originalOnConnectionsChange = node.onConnectionsChange
    node.onConnectionsChange = function (...args) {
      let result
      if (typeof originalOnConnectionsChange === 'function') {
        try {
          result = originalOnConnectionsChange.apply(this, args)
        } catch (error) {
          warn('original onConnectionsChange threw', error)
        }
      }
      try {
        fullResync(state)
      } catch (error) {
        warn('wire-change resync failed', error)
      }
      return result
    }
    // After wireNodeCleanup on purpose: the removal wrap exists before the
    // document listener does, so there is no window where a torn-down node
    // could leak it.
    installPastePathHandler(state)

    refreshGating(state).catch((error) => warn('initial config load failed', error))

    // Deferred one tick -- see file header's "Post-configure() resync"
    // section for exactly why this can't run synchronously here.
    setTimeout(() => {
      try {
        fullResync(state)
      } catch (error) {
        warn('deferred initial resync failed', error)
      }
    }, 0)
  } catch (error) {
    warn('attach failed', error)
  }
}

/** Fires once per node, AFTER a whole saved workflow has finished loading
 * (every node's widgets already restored) -- the cross-workflow-load resync
 * path; see file header. No-op unless *node* was actually attached. */
export function loadedGraphNode(node) {
  try {
    if (!node) return
    const state = nodeStates.get(node)
    if (!state) return
    fullResync(state)
  } catch (error) {
    warn('loadedGraphNode resync failed', error)
  }
}
