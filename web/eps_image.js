/**
 * @file Entry point for the EPSNodes image-utility frontend (EPS Switcher §6.4,
 * EPS Resolution §6.5, EPS Image Grid §6.6, EPS Frame Saver §6.7, EPS
 * Distributor §6.11). ComfyUI auto-imports every
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

const PREFIX = '[eps_image]'
const REPO_URL = 'https://github.com/ericpaulsnowden/comfyui-epsnodes'

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
  },

  /** Fires once per node instance; each attach is a no-op for other types. */
  nodeCreated(node) {
    safely('switcher.attach', () => switcher.attach?.(node))
    safely('resolution.attach', () => resolution.attach?.(node))
    safely('imageGrid.attach', () => imageGrid.attach?.(node))
    safely('frameSaver.attach', () => frameSaver.attach?.(node))
    safely('distributor.attach', () => distributor.attach?.(node))
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
