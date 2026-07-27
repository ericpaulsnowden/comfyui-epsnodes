/**
 * @file "EPSNodes" settings section (FORMAT.md §7.3): backend+frontend
 * version display (mismatch = "pulled but not restarted" hint, the
 * comfyui-photoshop-bridge pattern) and the library_dir setting.
 *
 * Remote-browser posture (FORMAT.md §7.3, owner reports 2026-07-18 and
 * 2026-07-19): a browser viewing a ComfyUI on ANOTHER machine must DEFER to
 * the host's library folder, and the field must be GENUINELY read-only
 * there — the original fix (revert-on-edit in `onChange`) still fired a
 * POST + an error toast on every keystroke, because `onChange` runs once
 * per keystroke regardless. The fix here is structural instead of
 * reactive: `libraryDirAttrs.disabled` is forwarded straight through
 * ComfyUI's generic settings-form plumbing (`FormItem.vue`'s
 * `v-bind="getFormAttrs(item)"` onto the underlying PrimeVue `InputText`,
 * which accepts a standard `disabled` prop) so a remote browser's field is
 * a genuinely disabled `<input>` — no keystroke can reach it, so `onChange`
 * simply never fires from typing. `onChange` keeps a silent-restore
 * fallback for defense in depth (settings-store replay, or some future
 * frontend build that doesn't honor `attrs.disabled`), per FORMAT.md §7.3:
 * never POST, never toast when remote — just restore the server's value
 * with zero user-facing noise.
 *
 * `libraryDirAttrs` is mutated in place once `/config` resolves (same
 * "plain module state, read fresh whenever the settings dialog next
 * mounts" pattern this file already uses for `backendVersion`/`versionRow`
 * below) — ComfyUI registers `SETTINGS` synchronously at extension load,
 * before `initSettings()`'s async fetch can know `is_local`, so there is no
 * way to know the right value up front.
 */

import { app } from '../../../scripts/app.js'
import * as api from './api.js'

const CATEGORY = 'EPSNodes'

let backendVersion = null

/** The server's current library_dir ('' = unconfigured default). onChange
 * compares against this so settings-store replays never POST; null = not
 * yet known (also never POSTs). */
let serverValue = null

/** FORMAT.md §2/§7.3 verdict for this browser (`GET /config`'s `is_local`).
 * null = not yet known — treated as local (fails open, same posture as the
 * rest of this pack) until the first `/config` response lands. */
let isLocal = null

/** FORMAT.md §5 `library_dir_exists`/`library_dir_note` — whether the
 * SERVER machine can currently see the configured folder, and a one-line
 * diagnosis when it can't (owner report 2026-07-19: a NAS path was
 * invisible until a node errored at run time). */
let libraryDirExists = true
let libraryDirNote = ''

/** Forwarded onto the `loraLibrary.libraryDir` text input via `attrs`
 * (see file header). Mutated in place — never replaced — so the object
 * identity the setting was registered with stays valid. */
const libraryDirAttrs = { disabled: false }

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
    attrs: libraryDirAttrs,
    defaultValue: '',
    onChange: onLibraryDirChanged
  },
  {
    id: 'loraLibrary.libraryDirStatus',
    category: [CATEGORY, 'Library', 'Folder'],
    name: 'Folder status',
    tooltip:
      'Whether the machine ComfyUI runs on can currently see the library ' +
      'folder above. A missing/unreachable folder is otherwise invisible ' +
      'until a node errors at run time (FORMAT.md §7.3).',
    type: () => folderStatusRow(),
    defaultValue: ''
  },
  {
    id: 'loraLibrary.versions',
    category: [CATEGORY, 'About', 'Versions'],
    name: 'Backend / frontend versions',
    tooltip:
      'Confirms the installed backend and frontend are the same version. A ' +
      "mismatch usually means an update was pulled but the server hasn't " +
      "been restarted, or the browser hasn't been hard-refreshed.",
    type: () => versionRow(),
    defaultValue: ''
  }
]

async function onLibraryDirChanged(value) {
  const trimmed = (value ?? '').trim()

  if (isLocal === false) {
    // Remote browser (FORMAT.md §7.3): `libraryDirAttrs.disabled` should
    // make this unreachable from a real keystroke, but this stays a
    // silent, no-POST, no-toast guard in case a settings-store replay (or
    // a frontend build that ignores `attrs.disabled`) calls this anyway —
    // exactly the noise the owner reported (an error toast on EVERY
    // keystroke) must never happen again.
    if (serverValue !== null && trimmed !== serverValue) {
      try {
        await app.extensionManager?.setting?.set?.('loraLibrary.libraryDir', serverValue)
      } catch (error) {
        api.warn('could not restore library folder for a remote browser', error)
      }
    }
    return
  }

  // Settings-store replay (page load, workspace switch) or a no-op edit:
  // the server already has this value — never POST it back (FORMAT.md §7.3).
  if (serverValue === null || trimmed === serverValue) return
  try {
    const response = await api.postJson('/lora_library/config', { library_dir: trimmed })
    serverValue = trimmed === '' ? '' : (response.library_dir ?? trimmed)
    await refreshLibraryDirStatus()
  } catch (error) {
    if (error.status === 403) {
      // §2: only the host machine may move the boundary — e.g. `is_local`
      // flipped between page load and this edit. Defer: put the host's
      // value back and explain once, calmly (not per keystroke).
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
 * treats an equal value as a no-op). Also refreshes `isLocal` (drives
 * `libraryDirAttrs.disabled`) and the FORMAT.md §5 folder-reachability
 * fields the status row reads. */
async function reflectServerValue() {
  const config = await api.getJson('/lora_library/config')
  serverValue = config.configured ? config.library_dir : ''
  isLocal = config.is_local !== false
  libraryDirAttrs.disabled = !isLocal
  libraryDirExists = config.library_dir_exists !== false
  libraryDirNote = config.library_dir_note || ''
  await app.extensionManager?.setting?.set?.('loraLibrary.libraryDir', serverValue)
}

/** Re-reads just the FORMAT.md §5 reachability fields (after a successful
 * local edit) without re-touching `isLocal`/`serverValue` bookkeeping that
 * `reflectServerValue` already owns for the initial load / remote path. */
async function refreshLibraryDirStatus() {
  try {
    const config = await api.getJson('/lora_library/config')
    libraryDirExists = config.library_dir_exists !== false
    libraryDirNote = config.library_dir_note || ''
  } catch (error) {
    api.warn('could not refresh library folder status', error)
  }
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

/** FORMAT.md §7.3: a persistent, calm status line under the folder field —
 * never a toast, so it can't spam. Up to two lines, built fresh each time
 * the settings panel mounts (same convention as `versionRow` above):
 *   - remote browser: "The library folder is set on the machine ComfyUI
 *     runs on." (shown once per panel-open, not per keystroke)
 *   - `library_dir_exists === false`: the server-chosen `library_dir_note`
 *     diagnosis, as a warning (the owner's 2026-07-19 "invisible until a
 *     node errors" NAS case).
 * Neither applies ⇒ a quiet confirmation, so the row is never blank. */
function folderStatusRow() {
  const el = document.createElement('div')
  el.style.display = 'flex'
  el.style.flexDirection = 'column'
  el.style.alignItems = 'flex-end'
  el.style.gap = '2px'
  el.style.fontSize = '0.85em'
  el.style.textAlign = 'right'
  el.style.maxWidth = '22rem'

  if (isLocal === false) {
    const caption = document.createElement('div')
    caption.style.opacity = '0.75'
    caption.textContent = 'The library folder is set on the machine ComfyUI runs on.'
    el.appendChild(caption)
  }

  if (libraryDirExists === false) {
    const warning = document.createElement('div')
    warning.style.color = 'var(--p-orange-400, #f0883e)'
    warning.style.fontWeight = '600'
    warning.textContent = `⚠ ${libraryDirNote || 'The library folder is not reachable from the server machine.'}`
    el.appendChild(warning)
  } else if (isLocal !== false) {
    const ok = document.createElement('div')
    ok.style.opacity = '0.6'
    ok.textContent = 'OK'
    el.appendChild(ok)
  }

  return el
}
