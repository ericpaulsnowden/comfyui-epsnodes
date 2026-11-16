/**
 * @file Switcher prompt post-pass (FORMAT.md §6.4 "Disabled rows and the
 * prompt"). Core scans EVERY provided input of a node for an
 * `ExecutionBlocker` before calling it (execution.py `process_inputs`), so a
 * DISABLED switcher row whose source also feeds another node -- and so runs,
 * and emits a blocker (an all-off switcher) -- blocks the whole switcher
 * and its ENABLED rows come out empty. No node-side code can prevent that,
 * but the server never sees an input that is not in the prompt: after
 * `app.graphToPrompt` builds `{ workflow, output }`, the links of
 * statically-disabled rows are deleted from `output`. `workflow` (the canvas,
 * the saved PNG) is never touched.
 *
 * "Disabled" is the ONE rule the backend uses (`toggles[row] === false`,
 * nodes_switcher.py `check_lazy_status`), read through switcher.js's own
 * `parseToggles` / `isEnabledIn`. Only provable from the prompt alone:
 * `toggles` must be a literal string, and only `[nodeId, slot]` links are
 * removed -- never a widget value.
 */
import { loggerFor } from '../lora_library/api.js'
import { SWITCHER_CLASSES, isEnabledIn, parseToggles } from './switcher.js'

const { warn } = loggerFor('[eps_image:switcher_prompt]')

const isLink = (value) => Array.isArray(value) && value.length === 2

/** The `<prefix>_N` row an input belongs to -- the row itself, or its
 * partner socket (`model_N_low` / `mask_N`, which follows its row's one
 * toggle) -- or null for any other input. Same `int()` the backend applies. */
function rowOf(spec, name) {
  const match = spec.inputRe.exec(name) || spec.pair?.inputRe.exec(name)
  return match ? `${spec.prefix}_${Number(match[1])}` : null
}

/**
 * Deletes, from an API prompt (`{ [id]: { class_type, inputs } }`), the
 * links wired into disabled rows of every switcher node. Mutates and
 * returns *output*. Pure: no app, DOM or graph.
 */
export function stripDisabledSwitcherRows(output) {
  const doomed = []
  for (const node of Object.values(output ?? {})) {
    const spec = Object.hasOwn(SWITCHER_CLASSES, node?.class_type) && SWITCHER_CLASSES[node.class_type]
    const inputs = node?.inputs
    if (!spec || !inputs || typeof inputs.toggles !== 'string') continue
    const toggles = parseToggles(inputs.toggles)
    for (const [name, value] of Object.entries(inputs)) {
      const row = isLink(value) ? rowOf(spec, name) : null
      if (row && !isEnabledIn(toggles, row)) doomed.push([inputs, name])
    }
  }
  // Collected first, deleted after: a failure while reading leaves the
  // prompt untouched.
  for (const [inputs, name] of doomed) delete inputs[name]
  return output
}

const installed = new WeakSet()

/** Chains `app.graphToPrompt` (never replaces it: other packs wrap it too)
 * so its result passes through :func:`stripDisabledSwitcherRows`. Once per
 * app. A failure of ours is logged and the unpruned result returned. */
export function installPromptPostPass(app) {
  const original = app?.graphToPrompt
  if (typeof original !== 'function' || installed.has(app)) return
  installed.add(app)
  app.graphToPrompt = async function (...args) {
    const result = await original.apply(this, args)
    try {
      stripDisabledSwitcherRows(result?.output)
    } catch (error) {
      warn('could not drop disabled switcher rows from the prompt; sending it unchanged', error)
    }
    return result
  }
}
