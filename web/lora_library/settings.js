/**
 * @file "EPSNodes" settings section (FORMAT.md §7.3): backend+frontend
 * version display (mismatch = "pulled but not restarted" hint, the
 * comfyui-photoshop-bridge pattern) and the library_dir setting.
 *
 * Remote-browser posture (FORMAT.md §7.3, owner report 2026-07-18): a
 * browser viewing a ComfyUI on ANOTHER machine must DEFER to the host's
 * library folder. ComfyUI's settings store replays values through
 * `onChange` on page load, which used to fire a `POST /config` from the
 * remote browser and surface the server's §2 403 as an error toast. The
 * fix is structural: track the server's own value and only POST when the
 * user actually edits the field to something DIFFERENT — and when the
 * server still refuses (403), revert the field and say, calmly, that the
 * host machine controls the folder.
 */

import { app } from '../../../scripts/app.js'
import * as api from './api.js'

const CATEGORY = 'EPSNodes'

let backendVersion = null

/** The server's current library_dir ('' = unconfigured default). onChange
 * compares against this so settings-store replays never POST; null = not
 * yet known (also never POSTs). */
let serverValue = null

export const SETTINGS = [
  {
    id: 'loraLibrary.libraryDir',
    category: [CATEGORY, 'Library', 'Folder'],
    name: 'Library folder',
    tooltip:
      'Absolute path of the shared library folder (holds loras.md and sets/). ' +
      'May be a NAS/network path readable by every machine that shares it. ' +
      'Leave empty for the per-user default. Lives server-side (FORMAT.md §1) ' +
      'and can only be CHANGED from the machine ComfyUI runs on — a browser ' +
      'on another computer sees the value but defers to the host.',
    type: 'text',
    defaultValue: '',
    onChange: onLibraryDirChanged
  },
  {
    id: 'loraLibrary.versions',
    category: [CATEGORY, 'About', 'Versions'],
    name: 'Backend / frontend versions',
    type: () => versionRow(),
    defaultValue: ''
  }
]

async function onLibraryDirChanged(value) {
  const trimmed = (value ?? '').trim()
  // Settings-store replay (page load, workspace switch) or a no-op edit:
  // the server already has this value — never POST it back (FORMAT.md §7.3).
  if (serverValue === null || trimmed === serverValue) return
  try {
    const response = await api.postJson('/lora_library/config', { library_dir: trimmed })
    serverValue = trimmed === '' ? '' : (response.library_dir ?? trimmed)
  } catch (error) {
    if (error.status === 403) {
      // §2: only the host machine may move the boundary. Defer: put the
      // host's value back and explain once, calmly.
      try {
        await reflectServerValue()
      } catch (refreshError) {
        api.warn('could not re-read host library folder', refreshError)
      }
      app.extensionManager?.toast?.add?.({
        severity: 'info',
        summary: 'EPSNodes',
        detail:
          'The library folder is controlled by the machine ComfyUI runs on — ' +
          'change it there. Showing the host’s current folder.',
        life: 6000
      })
      return
    }
    api.warn('saving library_dir failed', error)
    app.extensionManager?.toast?.add?.({
      severity: 'error',
      summary: 'EPSNodes',
      detail: `Could not set library folder: ${error.message}`,
      life: 6000
    })
  }
}

/** Pull the server's config and mirror it into the settings field without
 * triggering a POST (serverValue is set BEFORE the field, and onChange
 * treats an equal value as a no-op). */
async function reflectServerValue() {
  const config = await api.getJson('/lora_library/config')
  serverValue = config.configured ? config.library_dir : ''
  await app.extensionManager?.setting?.set?.('loraLibrary.libraryDir', serverValue)
}

/** One-time setup: mirror server config, fetch version, toast on mismatch. */
export async function initSettings() {
  try {
    const version = await api.getJson('/lora_library/version')
    backendVersion = version.version
    await reflectServerValue()
    if (backendVersion && backendVersion !== api.FRONTEND_VERSION) {
      app.extensionManager?.toast?.add?.({
        severity: 'warn',
        summary: 'EPSNodes version mismatch',
        detail:
          `backend v${backendVersion}, frontend v${api.FRONTEND_VERSION} — if you ` +
          'just updated, restart the ComfyUI server (backend) or hard-refresh ' +
          'the browser (frontend).',
        life: 8000
      })
    }
  } catch (error) {
    api.warn('initSettings failed (backend not reachable?)', error)
  }
}

function versionRow() {
  const el = document.createElement('div')
  el.style.opacity = '0.85'
  el.textContent = backendVersion
    ? `backend v${backendVersion} · frontend v${api.FRONTEND_VERSION}` +
      (backendVersion === api.FRONTEND_VERSION ? '' : '  ⚠ mismatch — restart server or hard-refresh')
    : `frontend v${api.FRONTEND_VERSION} · backend unreachable`
  return el
}
