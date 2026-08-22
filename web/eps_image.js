/**
 * @file Entry point for the EPSNodes image-utility frontend (EPS Image Switcher §6.4,
 * EPS Resolution §6.5, EPS Image Grid §6.6, EPS Frame Saver §6.7, EPS
 * Run Multiplier run-count §6.10, EPS Distributor §6.11). ComfyUI auto-imports every
 * top-level `.js` under `WEB_DIRECTORY` (`./web`); this is a SECOND
 * extension alongside `lora_library.js`, so the image nodes' frontend is
 * cleanly separated from the lora family. Each sub-feature is wrapped so one
 * failing module never blocks the others (the pack-wide `safely` pattern).
 */

import { app } from '../../scripts/app.js'
import * as switcher from './eps_image/switcher.js'
import * as resolution from './eps_image/resolution.js'
import * as imageGrid from './eps_image/image_grid.js'
import * as frameSaver from './eps_image/frame_saver.js'
import * as distributor from './eps_image/distributor.js'
import * as checkpointSwitcher from './eps_image/checkpoint_switcher.js'
import * as crossSweep from './eps_image/cross_sweep.js'

const PREFIX = '[eps_image]'
const REPO_URL = 'https://github.com/ericpaulsnowden/comfyui-epsnodes'

/**
 * FORMAT.md §7.5: Vue-nodes ("New node design") compatibility warning.
 *
 * Root-caused on the rig 2026-07-29 while chasing the owner's "uptick in
 * issues using my mac to connect to the linux machine": with
 * `Comfy.VueNodes.Enabled` on, the frontend renders nodes as Vue DOM and
 * **never calls `onDrawForeground`/`onMouseDown`** — so every hand-drawn EPS
 * control (Switcher/Distributor per-row toggles, double-click renames)
 * silently disappears, and canvas-only hooks go with it (Resolution's
 * cosmetic `drawSlots` passthrough-output hide and its modifier-click
 * multi-preset ContextMenu -- its pad/readout is a DOM widget and is fine;
 * v0.68.1 wording). Nothing errors; the features
 * are just gone, which reads as "these nodes are broken". Two browsers
 * pointed at the SAME server can disagree (the frontend nags each browser to
 * try the new design, and a long-lived tab keeps the old mode), which is
 * exactly the works-on-one-machine/broken-on-the-other shape the owner hit.
 *
 * Until the pack ships Vue-native widgets (roadmap), the honest move is to
 * SAY it, once, with the escape hatch: this toast names the setting to turn
 * off. Never thrown, never blocking — a missing settings store just means no
 * warning (canvas mode stays the silent default).
 */
/**
 * Every class whose controls are hand-drawn on the canvas, and so vanish in
 * Vue-nodes mode. The four switcher classes are read from
 * `switcher.SWITCHER_CLASSES` rather than listed by hand: EPS Model / CLIP /
 * VAE Switcher go through the SAME `switcher.js` `onDrawForeground` toggle
 * drawing as EPS Image Switcher, so a hardcoded list silently missed them (they
 * were added after this warning was written) and their users got the silent
 * missing-toggles failure this toast exists to prevent. Adding a fifth
 * switcher class to that registry now covers it here for free.
 */
const VUE_AFFECTED_CLASSES = new Set([
  ...Object.keys(switcher.SWITCHER_CLASSES ?? {}),
  'EPSDistributor',
  'EPSResolution'
])

let vueModeWarned = false
function warnIfVueNodesMode(node) {
  if (vueModeWarned) return
  const AFFECTED = VUE_AFFECTED_CLASSES
  const type = node?.comfyClass || node?.type
  if (!AFFECTED.has(type)) return
  let enabled = false
  try {
    enabled = app.extensionManager?.setting?.get?.('Comfy.VueNodes.Enabled') === true
  } catch {
    return
  }
  if (!enabled) return
  vueModeWarned = true
  const detail =
    'The EPS Switchers (Image, Model, CLIP, VAE) and EPS Distributor draw ' +
    'their controls (per-row toggles, double-click rename) directly on the ' +
    'node, and EPS Resolution hides its passthrough output and opens its ' +
    'multi-preset menu through canvas-only hooks -- ' +
    "ComfyUI's New node design (beta) does not run that drawing yet. " +
    'To use them, turn OFF Settings > Lite Graph > "New node design (beta)" ' +
    '(Comfy.VueNodes.Enabled) and reload. Full support is planned.'
  console.warn(PREFIX, detail)
  try {
    app.extensionManager?.toast?.add?.({
      severity: 'warn',
      summary: 'EPSNodes: some controls need the classic node design',
      detail,
      life: 15000
    })
  } catch {
    // console.warn above already carries the message.
  }
}

function safely(label, fn) {
  try {
    const result = fn()
    if (result && typeof result.catch === 'function') {
      result.catch((error) => console.warn(PREFIX, `${label} failed`, error))
    }
  } catch (error) {
    console.warn(PREFIX, `${label} failed`, error)
  }
}

app.registerExtension({
  name: 'eps_image.EPSImageNodes',
  aboutPageBadges: [{ label: 'EPSNodes (image)', url: REPO_URL, icon: 'pi pi-github' }],

  /** Frontend-only registrations that must run before nodes are created. */
  init() {
    safely('switcher.init', () => switcher.init?.())
    safely('resolution.init', () => resolution.init?.())
    safely('imageGrid.init', () => imageGrid.init?.())
    safely('frameSaver.init', () => frameSaver.init?.())
    safely('distributor.init', () => distributor.init?.())
    safely('checkpointSwitcher.init', () => checkpointSwitcher.init?.())
    safely('crossSweep.init', () => crossSweep.init?.())
  },

  /** Fires once per node instance; each attach is a no-op for other types. */
  nodeCreated(node) {
    safely('vueModeWarning', () => warnIfVueNodesMode(node))
    safely('switcher.attach', () => switcher.attach?.(node))
    safely('resolution.attach', () => resolution.attach?.(node))
    safely('imageGrid.attach', () => imageGrid.attach?.(node))
    safely('frameSaver.attach', () => frameSaver.attach?.(node))
    safely('distributor.attach', () => distributor.attach?.(node))
    safely('checkpointSwitcher.attach', () => checkpointSwitcher.attach?.(node))
    safely('crossSweep.attach', () => crossSweep.attach?.(node))
  },

  /**
   * Fires once per node, AFTER a whole saved workflow has finished loading
   * (every node's widgets/properties already restored) — EPS Image Grid's
   * §6.6 cross-workflow-reuse half of its uuid dedup, and EPS Frame Saver's
   * §6.7 identical restore-timing resync; see each module's own header
   * comment for the exact hook this is (verified against the installed
   * frontend package's bundle).
   */
  loadedGraphNode(node) {
    safely('imageGrid.loadedGraphNode', () => imageGrid.loadedGraphNode?.(node))
    safely('frameSaver.loadedGraphNode', () => frameSaver.loadedGraphNode?.(node))
  }
})
