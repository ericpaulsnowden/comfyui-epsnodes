/**
 * @file Entry point for the comfyui-epsnodes frontend extension.
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
import * as picker from './lora_library/picker.js'
import * as pathHeal from './lora_library/path_heal.js'
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

const REPO_URL = 'https://github.com/ericpaulsnowden/comfyui-epsnodes'

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
   * Give the frontend-only controller node its real display name, BEFORE the
   * defs reach the store (FORMAT.md §6.3).
   *
   * `app.ts`'s `registerNodes` synthesizes a def for every
   * frontend-registered litegraph type with `display_name` HARDCODED to the
   * registration name — it reads the class's `category` and `description`
   * statics but never its `title`. So this node announced itself as
   * "LoraLibrarySetController" and a gallery search for "EPS" missed it
   * (owner report 2026-07-22, and AGAIN on 2026-07-27 after the first fix).
   *
   * The first fix patched `nodeDefsByName` in the Pinia store on a retry
   * loop AFTER `updateNodeDefs` had already run. That made the SEARCH index
   * agree (which is what got verified), but anything the store had already
   * derived from the def kept the old name — which is why it looked
   * unfixed. This hook is the actual seam: `registerNodes` calls
   * `invokeExtensions('beforeRegisterVueAppNodeDefs', nodeDefArray)` on the
   * array and only THEN hands it to `nodeDefStore.updateNodeDefs`, so
   * editing it here means nothing downstream ever sees the class id.
   * Verified in `app.ts` from the installed frontend package's own source
   * map (1.45.21). Older frontends simply never call this — the legacy
   * store patch in controller.js stays as the fallback for them.
   */
  beforeRegisterVueAppNodeDefs(defs) {
    safely('controller.nameNodeDef', () => controller.nameNodeDef(defs))
  },

  /**
   * Fires once per node instance. Attaches the notebook's two-pane DOM
   * widget (FORMAT.md §7.2) to LoraLibraryNotebook nodes.
   */
  nodeCreated(node) {
    safely('notebook.attachNotebookWidget', () => notebook.attachNotebookWidget(node))
    safely('sets.attachApplySetBehavior', () => sets.attachApplySetBehavior(node))
    safely('picker.attachPickerPanel', () => picker.attachPickerPanel(node))
    // FORMAT.md §7.6: every node (any type) gets a chained onConfigure so a
    // PASTED node's model paths heal too; the load path is loadedGraphNode.
    safely('pathHeal.attachConfigureHeal', () => pathHeal.attachConfigureHeal(node))
  },

  /**
   * Fires once per node, subgraphs included, after a whole workflow has
   * finished loading -- `app.ts`'s `loadGraphData` invokes it from its
   * post-configure `forEachNode` loop, after every widget value is restored
   * (verified in the installed 1.48.7 bundle's source map). FORMAT.md §7.6:
   * heal model-combo values saved with the other OS's path separator.
   */
  loadedGraphNode(node) {
    safely('pathHeal.loadedGraphNode', () => pathHeal.loadedGraphNode(node))
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
