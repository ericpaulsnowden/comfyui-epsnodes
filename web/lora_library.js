/**
 * @file Entry point for the comfyui-lora-library frontend extension.
 * ComfyUI auto-imports every top-level `.js` file under `WEB_DIRECTORY`
 * (`./web`, set by the Python backend) — this is the only such file; every
 * other module lives under `lora_library/` and is wired together here into
 * exactly one `app.registerExtension(...)` call (FORMAT.md §7.1).
 *
 * Each hook defensively catches errors from its module so one broken
 * sub-feature never prevents the others from loading (pattern inherited
 * from comfyui-photoshop-bridge's entry file).
 */

import { app } from '../../scripts/app.js'
import * as api from './lora_library/api.js'
import * as notebook from './lora_library/notebook.js'
import * as sets from './lora_library/sets.js'
import * as controller from './lora_library/controller.js'
import { SETTINGS, initSettings } from './lora_library/settings.js'

/**
 * Runs `fn`, logging and swallowing any thrown/rejected error with the
 * project's `[lora_library]` prefix instead of letting it propagate.
 * @param {string} label
 * @param {() => unknown} fn
 */
function safely(label, fn) {
  try {
    const result = fn()
    if (result && typeof result.catch === 'function') {
      result.catch((error) => api.warn(`${label} failed`, error))
    }
  } catch (error) {
    api.warn(`${label} failed`, error)
  }
}

const REPO_URL = 'https://github.com/ericpaulsnowden/EPSNodes'

app.registerExtension({
  name: 'lora_library.LoraLibrary',
  settings: SETTINGS,
  aboutPageBadges: [
    {
      label: `EPSNodes v${api.FRONTEND_VERSION}`,
      url: REPO_URL,
      icon: 'pi pi-github'
    }
  ],

  /**
   * Fires once, before node registration — where frontend-only virtual node
   * types must be registered (FORMAT.md §6.3).
   */
  init() {
    safely('controller.registerControllerNode', () => controller.registerControllerNode())
  },

  /**
   * Fires once per node instance. Attaches the notebook's two-pane DOM
   * widget (FORMAT.md §7.2) to LoraLibraryNotebook nodes.
   */
  nodeCreated(node) {
    safely('notebook.attachNotebookWidget', () => notebook.attachNotebookWidget(node))
    safely('sets.attachApplySetBehavior', () => sets.attachApplySetBehavior(node))
  },

  /**
   * Fires once after startup: version-mismatch check + set-combo freshness
   * wiring (FORMAT.md §7.3/§7.4).
   */
  async setup() {
    safely('initSettings', () => initSettings())
    safely('sets.initSetsFreshness', () => sets.initSetsFreshness())
  }
})
