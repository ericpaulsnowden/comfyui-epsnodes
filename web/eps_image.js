/**
 * @file Entry point for the EPSNodes image-utility frontend (EPS Switcher §6.4,
 * EPS Resolution §6.5). ComfyUI auto-imports every top-level `.js` under
 * `WEB_DIRECTORY` (`./web`); this is a SECOND extension alongside
 * `lora_library.js`, so the image nodes' frontend is cleanly separated from
 * the lora family. Each sub-feature is wrapped so one failing module never
 * blocks the other (the pack-wide `safely` pattern).
 */

import { app } from '../../scripts/app.js'
import * as switcher from './eps_image/switcher.js'
import * as resolution from './eps_image/resolution.js'

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
  },

  /** Fires once per node instance; each attach is a no-op for other types. */
  nodeCreated(node) {
    safely('switcher.attach', () => switcher.attach?.(node))
    safely('resolution.attach', () => resolution.attach?.(node))
  }
})
