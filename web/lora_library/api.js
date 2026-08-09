/**
 * @file Fetch helpers + logging for the lora_library frontend (FORMAT.md §5).
 * Every module goes through these so error shape and the `[lora_library]`
 * log prefix stay uniform.
 */

import { api } from '../../../scripts/api.js'

export { FRONTEND_VERSION } from './version.js'

const PREFIX = '[lora_library]'

export function warn(message, error) {
  if (error !== undefined) console.warn(PREFIX, message, error)
  else console.warn(PREFIX, message)
}

export function log(message) {
  console.log(PREFIX, message)
}

/**
 * Absolute URL for a lora_library route -- for element attributes (an
 * `<img src>`) that bypass fetchApi. `api.apiURL` carries ComfyUI's api
 * base/path prefix; a hardcoded root-absolute path breaks behind a
 * reverse-proxy prefix while every fetchApi call keeps working (review
 * 2026-08-09; image_grid.js's own img-src precedent).
 * @param {string} path - e.g. `/lora_library/picker/preview?file=x`
 */
export function apiUrl(path) {
  return typeof api.apiURL === 'function' ? api.apiURL(path) : path
}

/**
 * GET a lora_library route (FORMAT.md §5). Resolves to parsed JSON.
 * Rejects with an Error whose message is the server's `error` field when
 * the response is non-2xx.
 * @param {string} path - e.g. `/lora_library/sets`
 * @param {Record<string, string>} [params]
 */
export async function getJson(path, params) {
  const query = params ? `?${new URLSearchParams(params)}` : ''
  const response = await api.fetchApi(`${path}${query}`)
  return unwrap(response)
}

/**
 * POST JSON to a lora_library route (FORMAT.md §5).
 * @param {string} path
 * @param {object} body
 */
export async function postJson(path, body) {
  const response = await api.fetchApi(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {})
  })
  return unwrap(response)
}

async function unwrap(response) {
  let data = null
  let parsed = true
  try {
    data = await response.json()
  } catch {
    // Non-JSON body (proxy error page etc.) — fall through to status check.
    parsed = false
  }
  if (!response.ok) {
    const message = data && data.error ? data.error : `HTTP ${response.status}`
    const error = new Error(message)
    error.status = response.status
    error.data = data
    throw error
  }
  // A 200 whose body isn't JSON is a FAILURE, not an empty payload (review
  // 2026-08-09): returning null here made the picker read a truncated/proxy-
  // mangled response as a successfully-empty lora library — the "failed" and
  // "empty install" states must never collapse into each other.
  if (!parsed) {
    const error = new Error('server returned a response that is not JSON')
    error.status = response.status
    throw error
  }
  return data
}
