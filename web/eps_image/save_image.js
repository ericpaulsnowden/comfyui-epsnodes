/**
 * EPS Save Image -- frontend half (FORMAT.md §6.14, provenance M2).
 *
 * The node itself needs no panel: it is Save Image with provenance baked
 * into the PNG, and a BAKED file loads pre-soloed through ComfyUI's own
 * drop handler. What this file adds, first, is the FILENAME FALLBACK for
 * files that predate M2 but carry the M1 run token in their name (every
 * `save_prefix`-named file since v0.67.0): after the frontend's
 * `app.handleFile` has loaded a dropped/opened image's workflow, if the
 * graph holds exactly one EPS Run Multiplier whose `solo_run` is empty and
 * the file name ends in `<token>_NNNNN_`, the token is written into
 * `solo_run` (value + callback, so the readout recomputes) and a toast
 * says so. A baked file (its multiplier already soloed to that token) is
 * left alone; an ambiguous graph (several unsoloed multipliers) gets a
 * toast naming the token to paste by hand instead of a guess. Nothing
 * here touches the drop path itself -- it only reads the graph after the
 * frontend has finished loading it (wrapped, never replaced; §7.5).
 *
 * Second (owner ask 2026-10-29), the `image_output` toggle's two canvas
 * duties, installed per node by `attach()`. (1) The outgoing `images`
 * wires are DIMMED while the toggle is off: the backend returns a silent
 * `ExecutionBlocker` for that output, so everything downstream is skipped
 * and the wire should look that way. The dim is always DERIVED from the
 * live widget value, never stored (`link.color` is not serialized -- see
 * image_grid.js's finding), and a toggle whose own input socket is wired
 * (a Number Controller BOOLEAN row) is not dimmed, since the value isn't
 * known at edit time. (2) A cross-branch restore guard in `onConfigure`:
 * main's Save Image carries only `[filename_prefix, image_output]`, and
 * `widgets_values` restores positionally, so a main save opened on this
 * branch would otherwise drop `image_output`'s value (usually `true`) into
 * `preview_only` and silently STOP SAVING (`mainLayoutRestore`).
 */

import { app } from '../../../scripts/app.js'
import { walkLiveNodes } from '../lora_library/api.js'
import { ensureOwnedLinkColourHook } from './owned_link_colour.js'

const PREFIX = '[EPSNodes save_image]'
const MULTIPLIER_CLASS = 'EPSCrossSweep'
const SOLO_WIDGET = 'solo_run'

/** FORMAT.md §6.14 -- frozen once shipped (§8). */
const CLASS_ID = 'EPSSaveImage'
/** The BOOLEAN tail widget (nodes_save_image.py `IMAGE_OUTPUT_WIDGET`). */
const IMAGE_OUTPUT_WIDGET = 'image_output'
/** v1.1.0's preview switch (nodes_save_image.py `PREVIEW_WIDGET`). */
const PREVIEW_WIDGET = 'preview_only'
/** The one output, by RETURN_NAMES. */
const IMAGES_OUTPUT_NAME = 'images'

/** The M1 token grammar (nodes_cross_sweep.py `_run_token`): optional
 * `m{N}` (+`_v{M}`), then `p{N}` | `i{N}_t{N}` | `t{N}` -- taken only from
 * the END of the stem, right before Save Image's `_NNNNN_` counter.
 *
 * v1.1.0: a file made with EPS Save Image's `preview_only` switch on carries
 * core Preview Image's `_temp_xxxxx` (five lowercase letters) between the
 * token and the counter (`Portrait_m2_i1_t3_temp_abcde_00001_.png`), so that
 * one optional segment is skipped -- otherwise a dragged-out preview would
 * silently lose its run token.
 *
 * 2026-10-04 formats: the extension is stripped generically (`/\.[^.]+$/`), so
 * the same names read the same with a `.avif` or `.exr` ending -- EPS Save
 * Image's `format` widget writes `Portrait_m2_i1_t3_00001_.avif` / `.exr`
 * with the identical `_NNNNN_` counter tail. */
const TOKEN_AT_END_RE =
  /(?:^|_)((?:m\d+(?:_v\d+)?_)?(?:p\d+|i\d+_t\d+|t\d+))(?:_temp_[a-z]{5})?_\d{5}_?$/

/**
 * The run token carried by a saved file's name, or null.
 * `Portrait_m2_i1_t3_00001_.png` -> `m2_i1_t3`; `plain_00001_.png` -> null.
 * @param {string} fileName @returns {string|null}
 */
export function tokenFromFileName(fileName) {
  const stem = String(fileName || '').replace(/\.[^.]+$/, '')
  const match = TOKEN_AT_END_RE.exec(stem)
  return match ? match[1] : null
}

function soloWidgetOf(node) {
  return (node?.widgets || []).find((w) => w && w.name === SOLO_WIDGET) || null
}

function toast(severity, detail) {
  try {
    app.extensionManager?.toast?.add?.({ severity, summary: 'EPS Save Image', detail, life: 6000 })
  } catch {
    // toast is a nicety
  }
}

/**
 * Decide what the fallback should do for *token* against *multipliers*
 * (each `{soloValue}`): 'baked' (one already carries the token -- a baked
 * file, leave it), 'apply' (exactly one unsoloed multiplier), 'ambiguous'
 * (several unsoloed), or 'none'. Pure, for the tests.
 * @param {string} token @param {Array<{soloValue: string}>} multipliers
 */
export function decideFilenameSolo(token, multipliers) {
  if (!token || !Array.isArray(multipliers) || multipliers.length === 0) return 'none'
  if (multipliers.some((m) => (m.soloValue || '').trim() === token)) return 'baked'
  const unsoloed = multipliers.filter((m) => !(m.soloValue || '').trim())
  if (unsoloed.length === 1) return 'apply'
  if (unsoloed.length > 1) return 'ambiguous'
  return 'none'
}

function applyFilenameSolo(file) {
  if (!file || !/^image\//i.test(file.type || '')) return
  // 2026-10-04 formats: an EXR (EPS Save Image's `format` = exr) keeps its
  // workflow in the file header, but the frontend (1.52.7) has no EXR
  // metadata reader -- `handleFile` can never have LOADED a workflow from it.
  // Most browsers give a .exr an empty type (the check above already
  // returns), but some systems label it `image/x-exr`; soloing whatever
  // multiplier happens to be on the CURRENT canvas from such a file name
  // would be a guess about a workflow nobody loaded. PNG and AVIF (the
  // frontend reads AVIF's Exif item) are unaffected.
  if (/\.exr$/i.test(file.name || '')) return
  const token = tokenFromFileName(file.name)
  if (!token) return
  const found = []
  // v1.2.0 nested reach (owner ask 2026-10-03, FORMAT.md §7.10): the walk
  // already descends into subgraphs, but a multiplier inside a subgraph
  // DEFINITION that several SubgraphNodes instantiate is the SAME node object
  // reported once per instance path -- counting it per path made one
  // multiplier look like several un-soloed ones and turned a clean 'apply'
  // into 'ambiguous'. Distinct node objects only.
  const seenNodes = new Set()
  for (const { node } of walkLiveNodes(app.graph)) {
    const cls = node?.comfyClass || node?.constructor?.comfyClass || node?.type
    if (cls !== MULTIPLIER_CLASS) continue
    if (seenNodes.has(node)) continue
    seenNodes.add(node)
    const widget = soloWidgetOf(node)
    if (!widget) continue
    found.push({ node, widget, soloValue: String(widget.value ?? '') })
  }
  const verdict = decideFilenameSolo(token, found)
  if (verdict === 'apply') {
    const { widget, node } = found.find((m) => !(m.soloValue || '').trim())
    widget.value = token
    try {
      widget.callback?.(token)
    } catch (error) {
      console.warn(PREFIX, 'solo_run callback threw', error)
    }
    node.graph?.setDirtyCanvas(true, true)
    toast('info', `Soloed the Run Multiplier to ${token} (read from the file name). Clear solo_run to run the whole set.`)
  } else if (verdict === 'ambiguous') {
    toast('warn', `This file names run ${token}, but the workflow has several Run Multipliers -- paste the token into the right one's solo_run.`)
  }
}

let installed = false

/** Wrap `app.handleFile` ONCE (§7.5: chained, never replaced). */
export function init() {
  if (installed) return
  installed = true
  const original = app?.handleFile
  if (typeof original !== 'function') return
  app.handleFile = async function (file, ...rest) {
    const result = await original.call(this, file, ...rest)
    try {
      applyFilenameSolo(file)
    } catch (error) {
      console.warn(PREFIX, 'filename solo fallback failed', error)
    }
    return result
  }
}

// ---------------------------------------------------------------------------
// `image_output` off -> dim the outgoing `images` wires. The contract is
// image_grid.js's "Collect only" link dimming, copied BY HAND (this pack
// duplicates helpers per file on purpose -- the shared-panel-code rule is to
// grep every sibling before calling a helper fixed, not to share the code).
// The cross-file colour convention is distributor.js's: a link this file dims
// carries `LINK_COLOR_OWNER_KEY`, so image_grid.js, distributor.js and
// bypass.js (all three honour the key) leave its `.color` alone while it is
// claimed, and the TARGET node's optional `LINK_COLOR_RESYNC_HOOK` is called
// once on release so it can reassert its own colour. `link.color` is never
// serialized (image_grid.js's finding against the frontend source maps:
// `LLink.asSerialisable` copies no colour and every load builds fresh
// `LLink`s), so a load/undo/paste always hands out undimmed links and the
// state here is ALWAYS DERIVED from the live widget value, never stored.
// ---------------------------------------------------------------------------

/** image_grid.js's dim grey -- the same "not really there" look. */
const DIM_LINK_COLOR = 'rgba(128,128,128,0.25)'
const LINK_COLOR_OWNER_KEY = '__epsLinkColorOwner'
const LINK_COLOR_OWNER_SAVE = 'EPSSaveImage:image-output-off'
const LINK_COLOR_RESYNC_HOOK = '__epsResyncLinkColors'

/** Nodes we've already wired, guarding against a double `nodeCreated`. */
const attachedNodes = new WeakSet()

/** Per-link stash of the pre-dim colour, keyed by the LLink object itself
 * (its identity is its lifetime); `{color}` so a stashed `undefined` -- the
 * usual case -- is distinguishable from "never stashed". */
const dimStash = new WeakMap()

/** Same lookups as bypass.js / image_grid.js (own copies, by convention). */
function nodeClassOf(node) {
  if (!node) return null
  if (node.comfyClass) return node.comfyClass
  if (node.constructor && node.constructor.comfyClass) return node.constructor.comfyClass
  return null
}

function findWidget(node, name) {
  return node.widgets?.find((w) => w && w.name === name)
}

function linkById(graph, linkId) {
  if (linkId == null || !graph) return null
  return graph.links?.[linkId] ?? graph.links?.get?.(linkId) ?? null
}

/** The backend's `parse_image_output` rule: only an explicit `false` (or
 * the string "false", any case) is off. */
function isImageOutputOff(value) {
  if (value === false) return true
  return typeof value === 'string' && value.trim().toLowerCase() === 'false'
}

/** Whether the toggle's OWN input socket is wired (frontend 1.5x gives every
 * widget one, e.g. for a Number Controller BOOLEAN row). Then the value is
 * only known at run time, so nothing is dimmed. */
function isImageOutputInputWired(node) {
  return (node.inputs || []).some(
    (inp) => inp?.widget?.name === IMAGE_OUTPUT_WIDGET && inp.link != null
  )
}

/** Every link id wired to the `images` output; a missing/malformed `.links`
 * contributes nothing. Exported for tests. */
export function outputLinkIds(node) {
  const output = node.outputs?.find((o) => o && o.name === IMAGES_OUTPUT_NAME)
  const links = output?.links
  return Array.isArray(links) ? links.filter((id) => id !== null && id !== undefined) : []
}

/**
 * Dims or restores the `images` output links to match the CURRENT widget
 * value -- always derived (FORMAT.md §7.9's rebuild law), so it is called
 * from every place that can change the value or the wiring: the widget's
 * callback, the `onConfigure` wrap (load, undo/redo, paste, tab switch),
 * `onConnectionsChange`, and `onRemoved` (`forceUndim`).
 *
 * Reconciles against `node.__epsSaveDimmedLinkIds` (a per-instance Set; a
 * rebuild hands the node a fresh one, which is right): anything tracked
 * that should no longer be dimmed gets its stashed colour back EXACTLY,
 * its owner tag cleared and its target's resync hook called once; anything
 * newly desired that nobody owns is stashed, dimmed and tagged. A link
 * another owner has claimed is left alone (and retried next time). Change-
 * gated repaint; never throws past its callers' try/catch. Exported for
 * tests.
 */
export function reconcileLinkDimming(node, { forceUndim = false } = {}) {
  if (!node) return
  const widget = findWidget(node, IMAGE_OUTPUT_WIDGET)
  const shouldDim =
    !forceUndim && !!widget && isImageOutputOff(widget.value) && !isImageOutputInputWired(node)
  const graph = node.graph
  const tracked = node.__epsSaveDimmedLinkIds || (node.__epsSaveDimmedLinkIds = new Set())
  const desired = shouldDim ? new Set(outputLinkIds(node)) : new Set()
  let changed = false

  // Restore anything tracked that should no longer be dimmed.
  for (const id of tracked) {
    if (desired.has(id)) continue
    const link = linkById(graph, id)
    if (link && link[LINK_COLOR_OWNER_KEY] === LINK_COLOR_OWNER_SAVE) {
      const stash = dimStash.get(link)
      link.color = stash ? stash.color : undefined
      delete link[LINK_COLOR_OWNER_KEY]
      dimStash.delete(link)
      changed = true
      try {
        const target = graph?.getNodeById?.(link.target_id)
        const resync = target?.[LINK_COLOR_RESYNC_HOOK]
        if (typeof resync === 'function') resync()
      } catch (error) {
        console.warn(PREFIX, 'link colour resync hook failed', error)
      }
    }
    tracked.delete(id)
  }

  // Frontend 1.52.7 ignores `link.color` on a plain link; this hook makes
  // it draw a link we've claimed in our colour (owned_link_colour.js).
  if (desired.size) ensureOwnedLinkColourHook()

  // Dim anything newly desired that nobody else owns.
  for (const id of desired) {
    const link = linkById(graph, id)
    if (!link) continue
    const owner = link[LINK_COLOR_OWNER_KEY]
    if (owner && owner !== LINK_COLOR_OWNER_SAVE) continue
    if (!owner) {
      dimStash.set(link, { color: link.color })
      link.color = DIM_LINK_COLOR
      link[LINK_COLOR_OWNER_KEY] = LINK_COLOR_OWNER_SAVE
      changed = true
    }
    tracked.add(id)
  }

  if (changed) graph?.setDirtyCanvas?.(true, true)
}

/**
 * Chains the `image_output` widget's callback (never replaces it) so a
 * click re-derives the dim at once. Guarded on the WIDGET, like
 * image_grid.js's `installModeDimSync`.
 */
function installImageOutputDimSync(node) {
  const widget = findWidget(node, IMAGE_OUTPUT_WIDGET)
  if (!widget || widget.__epsSaveDimSyncInstalled) return
  widget.__epsSaveDimSyncInstalled = true
  const originalCallback = widget.callback
  widget.callback = function (...args) {
    const result =
      typeof originalCallback === 'function' ? originalCallback.apply(this, args) : undefined
    try {
      reconcileLinkDimming(node)
    } catch (error) {
      console.warn(PREFIX, 'link dim sync (image_output callback) failed', error)
    }
    return result
  }
}

/**
 * Chains `onConnectionsChange` (never replaces): a wire landing on or
 * leaving the output while off, or the toggle's own input socket being
 * wired/unwired. Cheap and idempotent, so it runs on every call unfiltered.
 */
function installLinkDimConnectionsSync(node) {
  if (node.__epsSaveLinkDimConnectionsWrapped) return
  node.__epsSaveLinkDimConnectionsWrapped = true
  const original = node.onConnectionsChange
  node.onConnectionsChange = function (...args) {
    const result = typeof original === 'function' ? original.apply(this, args) : undefined
    try {
      reconcileLinkDimming(this)
    } catch (error) {
      console.warn(PREFIX, 'link dim sync (onConnectionsChange) failed', error)
    }
    return result
  }
}

/**
 * Chains `onRemoved` (never replaces): restore anything still dimmed before
 * the node goes. Belt-and-suspenders -- removal disconnects the links
 * anyway -- but a tagged link must never outlive the node that dimmed it.
 */
function installLinkDimCleanup(node) {
  if (node.__epsSaveLinkDimCleanupWrapped) return
  node.__epsSaveLinkDimCleanupWrapped = true
  const original = node.onRemoved
  node.onRemoved = function (...args) {
    try {
      reconcileLinkDimming(this, { forceUndim: true })
    } catch (error) {
      console.warn(PREFIX, 'link dim cleanup (onRemoved) failed', error)
    }
    return typeof original === 'function' ? original.apply(this, args) : undefined
  }
}

/**
 * The cross-branch restore guard's verdict for a saved `widgets_values`
 * array: `null` = this branch's own layout, leave it; otherwise the values
 * `preview_only` and `image_output` must be set to AFTER
 * `LGraphNode.configure` has restored them positionally.
 *
 * Why: this branch serializes `[filename_prefix, preview_only, format,
 * bit_depth, input_color_space, avif_crf, image_output]`; main serializes
 * just `[filename_prefix, image_output]`. `widgets_values` restores
 * POSITIONALLY (FORMAT.md §8; the 1.52.7 bundle's `configure` assigns
 * `widgets[i].value = widgets_values[i]`, then calls `onConfigure(info)`),
 * so a main save opened here would put `image_output`'s value -- `true`
 * unless switched off -- into `preview_only`: the node would preview
 * instead of saving, with nothing on screen to say why. So exactly two
 * values is main's layout: preview off (main has no preview), and
 * `image_output` taken from index 1 (`!== false`, the backend's
 * only-an-explicit-false rule). The one casualty: a two-value save from
 * this branch's own v1.1.0 (preview_only only, before the format widgets,
 * one day in 2026-10) loads with preview off -- it saves, never loses
 * images. One value (a pre-v1.1 save) and six or seven (this branch) are
 * already right. Pure, for the tests. (main's mirror of this guard,
 * `imageOutputFromSavedValues`, handles the opposite direction.)
 * @param {unknown} values
 * @returns {{previewOnly: boolean, imageOutput: boolean}|null}
 */
export function mainLayoutRestore(values) {
  if (!Array.isArray(values) || values.length !== 2) return null
  return { previewOnly: false, imageOutput: values[1] !== false }
}

/**
 * Wraps `onConfigure` (chained, never replaced; §7.5) -- it fires at the
 * very end of `LGraphNode.configure()` for a load, undo/redo, paste and a
 * tab switch, with the widgets just restored. Two duties, in order: the
 * cross-branch guard (`mainLayoutRestore`, written via value +
 * callback like `applyFilenameSolo` does, so it takes the same path as a
 * click), then the dim re-derived from the now-final widget value.
 */
function installConfigureSync(node) {
  if (node.__epsSaveConfigureWrapped) return
  node.__epsSaveConfigureWrapped = true
  const original = node.onConfigure
  node.onConfigure = function (info) {
    const result = typeof original === 'function' ? original.call(this, info) : undefined
    try {
      const verdict = mainLayoutRestore(info?.widgets_values)
      if (verdict !== null) {
        const preview = findWidget(this, PREVIEW_WIDGET)
        if (preview && preview.value !== verdict.previewOnly) {
          preview.value = verdict.previewOnly
          preview.callback?.(verdict.previewOnly)
        }
        const widget = findWidget(this, IMAGE_OUTPUT_WIDGET)
        if (widget && widget.value !== verdict.imageOutput) {
          widget.value = verdict.imageOutput
          widget.callback?.(verdict.imageOutput)
        }
      }
      reconcileLinkDimming(this)
    } catch (error) {
      console.warn(PREFIX, 'post-configure sync failed', error)
    }
    return result
  }
}

/**
 * Per-node attach; a no-op unless *node* is an EPS Save Image (comfyClass),
 * guarded against a double `nodeCreated`. Installs the four re-derive hooks
 * and settles once now (a fresh node's toggle is on, so normally a no-op; a
 * loaded node is settled again by `onConfigure`). Never throws.
 */
export function attach(node) {
  try {
    if (!node || nodeClassOf(node) !== CLASS_ID) return
    if (attachedNodes.has(node)) return
    attachedNodes.add(node)
    installImageOutputDimSync(node)
    installLinkDimConnectionsSync(node)
    installLinkDimCleanup(node)
    installConfigureSync(node)
    reconcileLinkDimming(node)
  } catch (error) {
    console.warn(PREFIX, 'attach failed', error)
  }
}
