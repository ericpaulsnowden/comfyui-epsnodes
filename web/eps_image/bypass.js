/**
 * @file EPS Bypass frontend (FORMAT.md section 6.18). Exports the
 * `init()`/`attach(node)` hooks `web/eps_image.js` calls; `attach` no-ops for
 * every node type other than `EPSBypass`.
 *
 * Owner's ask (v0.99.0): "A node you can plug something into (like audio),
 * switch it off, and it looks to the downstream node like nothing is
 * connected." Muting the source node did not do it.
 *
 * **The whole trick is the WIRE, not the value.** An `ExecutionBlocker` on
 * ANY input -- required or optional -- makes core skip the CONSUMING node
 * outright (`execution.py`'s `process_inputs`), so a blocked audio branch
 * would take the video node down with it instead of giving a silent video.
 * The only thing that makes a consumer run "as if nothing were connected" is
 * the link genuinely not existing in the prompt, and that is legal exactly
 * when the consumer's input is OPTIONAL. So the backend node is a plain
 * pass-through (`nodes_bypass.py`) and THIS file does the unplugging, with
 * the Number Controller's rig-verified mechanism (number_controller.js's
 * checkbox paragraph): toggle OFF remembers every output target in the
 * hidden `links` widget and calls `target.disconnectInput(...)` on each;
 * toggle ON replays the memory through `node.connect(...)`.
 *
 * **Shared code, deliberately imported (ROADMAP-shared-panel-code.md).** The
 * unplug/replug machinery is number_controller.js's
 * `collectOutputTargets`/`disconnectAllTargets`/`reconnectRememberedTargets`/
 * `normalizeRememberedLinks`, plus its `hideValuesWidget` and
 * `installMinWidth`; the adopted-type rule is distributor.js's
 * `resolveAdoptedType` and the cross-file link-colour convention
 * (`LINK_COLOR_OWNER_KEY`/`LINK_COLOR_RESYNC_HOOK`). All are IMPORTED under
 * their existing bare names, not hand-copied: this pack has shipped a fix to
 * a duplicated helper that missed a sibling three times, and unplug/replug is
 * exactly the code that must never diverge between two nodes. Only the
 * trivial lookups (`nodeClassOf`, `findWidget`, `linkById`, `toast`) are
 * local copies, like every other module's. Those exports carry a comment in
 * their own files naming this one.
 *
 * **Required-input guard -- refuse wholesale, never partially.** Before
 * disconnecting anything, every target input is classified
 * (`inputVerdict`): a widget-backed input is SAFE (its own value takes over,
 * always serialised); otherwise the target class's definition
 * (`constructor.nodeData`, the frontend's copy of `/object_info`) says
 * optional or required; the slot's hollow-circle shape is the fallback for a
 * frontend-added dynamic socket (the Switcher's `image_N`); anything that
 * cannot be told is treated as REQUIRED. If ANY target is unsafe, nothing is
 * unplugged, the toggle snaps back ON and a toast names the node(s) and why.
 * A mixed optional/required fan-out refuses wholesale rather than unplugging
 * "the optional ones": the switch would then read OFF while a wire is still
 * attached, the backend fallback would block that consumer, and a consumer
 * silently skipped is precisely the failure this node exists to avoid. It is
 * also one sentence to explain. Likewise a wire that CANNOT be remembered
 * (its target node is gone, or its input has no name -- the legacy Reroute
 * NODE's input is nameless) refuses: `disconnectAllTargets` would sever it
 * for good.
 *
 * **Nested subgraphs (v1.2.0, owner ask 2026-10-03: "Make sure all of the
 * nodes that can control other nodes also looks into nested nodes"; FORMAT.md
 * section 7.10 nested reach).** Two wires cross a subgraph boundary:
 *   (a) a wire INTO a SubgraphNode input. The unplug always worked (a
 *       SubgraphNode is an ordinary node); the GUARD used to classify the
 *       SubgraphNode with its own (absent) `nodeData` and refuse even when
 *       the real consumer inside was optional. Now `subgraphInputVerdicts`
 *       follows the wire to every real inner consumer (further SubgraphNodes
 *       and subgraph-output pass-throughs included, via `api.js`'s
 *       `resolveLinkTargets`) and runs the same `inputVerdict` on each; a
 *       PROMOTED widget on that SubgraphNode input is safe (its own value
 *       takes over when the outer wire goes -- `ExecutableNodeDTO.resolveInput`),
 *       and a subgraph input nobody reads is safe (nothing consumes it);
 *   (b) THIS node inside a subgraph, output wired to the subgraph's OUTPUT
 *       node (`target_id` -20). It used to refuse as "unrestorable"; now the
 *       shared unplug helpers handle it (remembered as `{node: -20, input:
 *       <subgraph output name>}`, unplugged by `SubgraphOutput.disconnect()`,
 *       replugged by `SubgraphOutput.connect` -- number_controller.js's nested
 *       paragraph) and `subgraphOutputVerdicts` classifies the consumers on
 *       the far side, one set per SubgraphNode instance of the definition
 *       (`locationsOfNode`), under the same all-or-nothing rule.
 * Refusal labels for consumers in a subgraph say where they are --
 * "Subgraph name › Node title (input)" (`describePath`). A -20 wire in a
 * graph that is not a real Subgraph (no `outputs` slot to reach) still refuses
 * as unrestorable.
 *
 * **Every route to "off" runs the same code.** A click and a Universal State
 * Controller Apply both reach `onEnabledChanged`: an Apply does `widget.value
 * = v; widget.callback?.(v, canvas, node)` per node and then
 * `announceWidgetsChangedExternally`, so the widget-callback wrap handles the
 * write itself and the announce subscription (`__epsBypassReload`) is an
 * idempotent second pass (stored OFF + still wired -> guard + unplug; stored
 * ON + unwired + memory -> replug; otherwise a no-op). The registry declares
 * only `enabled`; `links` is excluded (see `nodes_bypass.py`).
 *
 * **The memory** is `{"owner": <this node's id>, "links": [{"node": id,
 * "input": name}, ...]}` in the hidden `links` widget (`node` is -20 and
 * `input` the subgraph output's name for a wire into the owning subgraph's
 * output -- the v1.2.0 nested paragraph above). `owner` exists for one
 * reason: copy/paste. A pasted OFF Bypass carries the original's memory, whose
 * targets are the ORIGINAL consumers -- with their inputs free -- so switching
 * the copy on would wire it into them. A pasted node has a new id, so memory
 * whose owner is not this node is stale and is ignored (`parseMemory`).
 * Replug is ONE attempt and then the memory is forgotten (number_controller.js's
 * reasoning: otherwise a later manual unplug would silently reconnect), each
 * item fails soft (a missing node/input, or a socket another wire has since
 * claimed, is skipped -- never stomped), and the toast says how many came back.
 * A NEW wire dragged onto an OFF Bypass is unambiguous intent and switches it
 * back on, forgetting the memory (number_controller.js's
 * `autoReenableNewlyWiredRows`), run ONLY from the live connection hook so a
 * state Apply that has not caught up yet is never mistaken for it.
 *
 * **The re-render law (FORMAT.md section 7.9).** Both widgets are the only
 * durable stores; nothing is cached JS-side. `settle` (types, labels, link
 * colours) is read-only with respect to every widget and idempotent, so a tab
 * switch / undo / redo / reload -- which rebuild the node and call
 * `onConfigure` -- re-derive everything from the saved widgets and the saved
 * links. Loading NEVER rewires: an OFF node that loads wired (only reachable
 * by hand-editing or an API caller) is left as it is, and the backend's
 * ExecutionBlocker fallback keeps "off" meaning off; only a user action, a
 * state Apply, or a wire dragged on changes the graph.
 *
 * **Type adoption** is distributor.js's (one type per node, from the input
 * link first, then the output links), minus its allowlist: any type may be
 * carried. Once both sockets hold a concrete type, litegraph's own
 * `isValidConnection` refuses a mismatched later connection by itself, so no
 * veto hook is installed. Labels show the type (`AUDIO`, `IMAGE`, `any`).
 *
 * **"Off" is visible on the node**, without drawing on the canvas: the
 * toggle's own text is `off — sends nothing` (backend `label_off`, honoured
 * by both renderers) and the output socket's label says the same, so this
 * node needs no `onDrawForeground` and no `web/eps_image.js`
 * `VUE_AFFECTED_CLASSES` entry (nothing here is hand-drawn).
 *
 * Known limits, stated rather than discovered: switching off garbage-collects
 * a wire's NATIVE link reroutes (core's `disconnectInput` default, and
 * `SubgraphOutput.disconnect()` likewise), so switching back on reconnects
 * straight; a legacy Reroute NODE downstream refuses (above).
 */

import { app } from '../../../scripts/app.js'
import {
  describePath,
  isSubgraphNode,
  locationsOfNode,
  resolveLinkTargets,
  rootGraphOf,
  subscribeWidgetsChangedExternally
} from '../lora_library/api.js'
import {
  collectOutputTargets,
  disconnectAllTargets,
  hideValuesWidget,
  installMinWidth,
  isOutputConnected,
  isSubgraphOutputLink,
  normalizeRememberedLinks,
  reconnectRememberedTargets,
  subgraphOutputSlotOf
} from './number_controller.js'
import {
  LINK_COLOR_OWNER_KEY,
  LINK_COLOR_RESYNC_HOOK,
  resolveAdoptedType
} from './distributor.js'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** FORMAT.md section 6.18 -- frozen once shipped (section 8). */
export const CLASS_ID = 'EPSBypass'
const PREFIX = '[eps_image:bypass]'
const NODE_TITLE = 'EPS Bypass'

/** The visible BOOLEAN widget -- FIRST in `widgets_values` (section 8). */
export const ENABLED_WIDGET_NAME = 'enabled'
/** The hidden JSON memory widget -- SECOND, hidden state last (section 8). */
export const LINKS_WIDGET_NAME = 'links'
/** The one wildcard socket's NAME -- inputs restore by name (section 8). */
export const INPUT_NAME = 'value'
/** The one output is always index 0. */
export const OUTPUT_INDEX = 0
/** litegraph's `NodeSlotType.OUTPUT` -- the `type` argument of an output-side
 * `onConnectionsChange` (a plain number: the enum is not a stable public
 * import, like `HOLLOW_CIRCLE_SHAPE` below). */
const NODE_SLOT_OUTPUT = 2

/** Litegraph's own "matches anything" type. */
export const WILDCARD = '*'
/** Label shown on a socket whose type is not adopted yet. */
export const ANY_LABEL = 'any'
/** The output socket's label while OFF -- keep in step with the backend's
 * `label_off` (`nodes_bypass.py`), which is the toggle's own text. */
export const OFF_LABEL = 'off — sends nothing'

/** Width floor (section 7.2's house rule): wide enough that the input label,
 * the output's OFF label and the toggle text do not collide. */
export const MIN_NODE_WIDTH = 240

/** `RenderShape.HollowCircle` (litegraph `globalEnums.ts`, value 7): what
 * the frontend stamps on every OPTIONAL input it builds from a definition
 * (`litegraphService.ts` `addInputSocket`: `shape: inputSpec.isOptional ?
 * RenderShape.HollowCircle : undefined`). A plain number here because the
 * enum is not a stable public import. */
export const HOLLOW_CIRCLE_SHAPE = 7

/** How long a refusal toast stays up -- long, because it carries a
 * sentence the user has to read before they can act on it. */
const REFUSAL_TOAST_MS = 9000

/** Nodes we've already wired, guarding against a double `nodeCreated`. */
const attachedNodes = new WeakSet()

// ---------------------------------------------------------------------------
// Pure helpers -- exported so tests/test_bypass_js.py can drive them under
// Node with no litegraph node. No node/ctx/DOM in these signatures unless
// stated.
// ---------------------------------------------------------------------------

/**
 * Whether the toggle reads as ON: anything but an explicit falsy value.
 * `null`/`undefined` (a widget that has no value yet) read ON, the backend's
 * own rule (`nodes_bypass.py`: "on is the least-surprising default").
 */
export function isBypassEnabled(value) {
  return value == null ? true : Boolean(value)
}

/**
 * Parses the hidden `links` widget for *ownerId*. Never throws. Returns the
 * `normalizeRememberedLinks`-sanitised target list, or `[]` for: malformed
 * JSON, a non-object, a missing/foreign `owner` (a pasted copy's memory
 * points at the ORIGINAL's consumers -- module docstring), or no `links`.
 * `owner` must be present and equal (compared as strings, since a hand-edited
 * or foreign workflow may stringify ids) -- memory this file did not write is
 * not trusted.
 */
export function parseMemory(raw, ownerId) {
  try {
    const parsed = JSON.parse(raw || '{}')
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return []
    if (parsed.owner == null || ownerId == null) return []
    if (String(parsed.owner) !== String(ownerId)) return []
    return normalizeRememberedLinks(parsed.links)
  } catch (error) {
    return []
  }
}

/**
 * Inverse of `parseMemory`: the JSON string for *links* owned by *ownerId*.
 * An empty (or wholly invalid) list serialises to `'{}'` -- the backend's
 * default -- so a switched-on node carries no residue in its saved workflow.
 */
export function serializeMemory(ownerId, links) {
  const clean = normalizeRememberedLinks(links)
  return clean.length > 0 ? JSON.stringify({ owner: ownerId, links: clean }) : '{}'
}

/** Whether two remembered targets are the same wire (ids compared as strings,
 * so a hand-stringified id -- or the subgraph output pseudo-node's `-20` --
 * still matches). */
export function sameTarget(a, b) {
  return String(a?.node) === String(b?.node) && a?.input === b?.input
}

/** Whether *type* carries no preference (litegraph's generic forms). */
function isGenericType(type) {
  return type === '' || type === WILDCARD || type === 0 || type === null || type === undefined
}

/** Socket label for an adopted *type*: the type itself (`AUDIO`), `any` for
 * the wildcard. */
export function labelForType(type) {
  return isGenericType(type) ? ANY_LABEL : String(type)
}

/** The OUTPUT socket's label: the type while ON, `OFF_LABEL` while OFF. */
export function outputLabelFor(enabled, type) {
  return enabled ? labelForType(type) : OFF_LABEL
}

/**
 * Whether unplugging *slot* on *targetNode* is safe -- the required-input
 * guard's per-input decision (module docstring). Returns `{safe, why}` with
 * `why` one of `'widget'`, `'optional'`, `'shape'` (safe) or `'required'`,
 * `'unknown'` (not safe).
 *
 *  1. A widget-backed input (`slot.widget`): unplugging hands the value back
 *     to its widget, which is ALWAYS serialised into the prompt -- the same
 *     fact the Number Controller's checkbox rests on -- so it is safe even
 *     though the definition lists such inputs as `required`.
 *  2. The definition (`targetNode.constructor.nodeData`): V1 `input.optional`
 *     / `input.required` by name, then V2 `inputs[name].isOptional`.
 *  3. The slot's hollow-circle shape -- the frontend's own optional marker --
 *     for a dynamic socket the definition does not list.
 *  4. Otherwise `'unknown'`, which refuses: an unplugged REQUIRED input fails
 *     the run, an unnecessary refusal only costs a message.
 */
export function inputVerdict(targetNode, slot) {
  if (!slot || typeof slot !== 'object') return { safe: false, why: 'unknown' }
  if (slot.widget) return { safe: true, why: 'widget' }
  const name = slot.name
  const def = targetNode?.constructor?.nodeData
  const has = (obj, key) => Boolean(obj) && Object.prototype.hasOwnProperty.call(obj, key)
  if (def && typeof name === 'string' && name !== '') {
    if (has(def.input?.optional, name)) return { safe: true, why: 'optional' }
    if (has(def.input?.required, name)) return { safe: false, why: 'required' }
    const v2 = has(def.inputs, name) ? def.inputs[name] : null
    if (v2) return v2.isOptional ? { safe: true, why: 'optional' } : { safe: false, why: 'required' }
  }
  if (slot.shape === HOLLOW_CIRCLE_SHAPE) return { safe: true, why: 'shape' }
  return { safe: false, why: 'unknown' }
}

/**
 * The toast sentence for a refused switch-off. *blockers* is the list of
 * unsafe `{label, why}` entries (`why`: `'required'`, `'unrestorable'` or
 * `'unknown'`). The sentence is about ONE reason -- the most decisive one
 * present, in that order -- and names at most three of the inputs that
 * carry it, counting the rest, so the grammar always agrees with the list.
 * Pure, so the wording is testable.
 */
export function refusalMessage(blockers) {
  const list = Array.isArray(blockers) ? blockers : []
  const why = ['required', 'unrestorable', 'unknown'].find((w) => list.some((b) => b.why === w))
  const group = why ? list.filter((b) => b.why === why) : list
  const one = group.length === 1
  const subject =
    group.slice(0, 3).map((b) => b.label).join(', ') +
    (group.length > 3 ? ` (+${group.length - 3} more)` : '')
  let reason
  if (why === 'required') {
    reason =
      `${subject} ${one ? 'is a required input' : 'are required inputs'}, so unplugging ` +
      `${one ? 'it' : 'them'} would fail the run with "Required input is missing".`
  } else if (why === 'unrestorable') {
    reason =
      `${subject} can't be reconnected afterwards (a legacy Reroute node, a subgraph output ` +
      "it can't reach, or a wire to a node that no longer exists), so unplugging would lose " +
      'the wire for good.'
  } else {
    reason =
      `couldn't confirm that ${subject} ${one ? 'is' : 'are'} optional, and unplugging a ` +
      'required input would fail the run.'
  }
  return `Can't switch off: ${reason} It stays on. EPS Bypass only works into optional inputs.`
}

// ---------------------------------------------------------------------------
// Node / graph lookups (local copies of the pack's trivial helpers -- module
// docstring: only these four are hand-copied, deliberately).
// ---------------------------------------------------------------------------

function nodeClassOf(node) {
  if (!node) return null
  if (node.comfyClass) return node.comfyClass
  if (node.constructor && node.constructor.comfyClass) return node.constructor.comfyClass
  return null
}

function findWidget(node, name) {
  return node.widgets?.find((w) => w && w.name === name)
}

/** An LLink by id, tolerant of `graph.links` being a plain object/array or
 * a Map (distributor.js's/number_controller.js's identical lookup). */
function linkById(graph, linkId) {
  if (linkId == null || !graph) return null
  return graph.links?.[linkId] ?? graph.links?.get?.(linkId) ?? null
}

function toast(node, severity, detail, life) {
  try {
    app.extensionManager?.toast?.add?.({
      severity,
      summary: node.title || NODE_TITLE,
      detail,
      life: life ?? (severity === 'error' ? 6000 : 3000)
    })
  } catch (error) {
    console.warn(PREFIX, 'toast failed', error)
  }
}

/** `app.configuringGraph` -- true for exactly the duration of a whole-graph
 * load/undo/redo/tab-switch (image_grid.js's `isGraphConfiguring` cites the
 * live verification). `Boolean()` so a frontend without it reads `false`. */
function isGraphConfiguring() {
  return Boolean(app.configuringGraph)
}

function outputOf(node) {
  return node.outputs?.[OUTPUT_INDEX]
}

function inputOf(node) {
  return (node.inputs || []).find((input) => input?.name === INPUT_NAME)
}

function isEnabled(state) {
  return isBypassEnabled(state.enabledWidget.value)
}

function readMemory(state) {
  return parseMemory(state.linksWidget.value, state.node.id)
}

/** The SOLE writer of the `links` widget. Writes only on a real change. */
function writeMemory(state, links) {
  const json = serializeMemory(state.node.id, links)
  if (state.linksWidget.value === json) return
  state.linksWidget.value = json
  state.linksWidget.callback?.(json)
  state.node.graph?.setDirtyCanvas(true, true)
}

/** `"Create Video #12 (audio)"` -- how a toast names a target input. */
function describeTarget(target, slot) {
  const title = target?.title || nodeClassOf(target) || 'a node'
  const id = target?.id != null ? ` #${target.id}` : ''
  const input = slot?.label || slot?.localized_name || slot?.name
  return input ? `${title}${id} (${input})` : `${title}${id}`
}

// ---------------------------------------------------------------------------
// The required-input guard
// ---------------------------------------------------------------------------

/**
 * Whether *slot* -- an input of a SubgraphNode -- is a PROMOTED WIDGET: the
 * subgraph exposes an inner node's widget as its own, so when the OUTER wire
 * is unplugged the SubgraphNode's own value takes over. Read from
 * `ExecutableNodeDTO.resolveInput` (ComfyUI 1.52.7): with the outer link gone
 * it returns the promoted widget's value when `subgraphNodeInput.widgetId` is
 * set and nothing otherwise, and `SubgraphNode._setWidget` stamps
 * `input.widgetId`, `input._widget` and `input.widget` together. Any of the
 * three counts, so an older/newer build that keeps only one of them is still
 * recognised; a field that is simply absent reads as "not promoted", which
 * is the refusing side. Exported for tests.
 */
export function hasPromotedWidget(slot) {
  return Boolean(slot && (slot.widgetId || slot._widget || slot.widget))
}

/**
 * How a toast names a consumer found across a subgraph boundary (owner ask
 * 2026-10-03): `"Subgraph name › Node title (input)"` through
 * `api.describePath`, so two same-titled nodes in different subgraphs read
 * apart. A consumer at the ROOT (reached through a subgraph output) keeps the
 * ordinary `describeTarget` wording, `Title #id (input)`.
 */
function describeConsumer(root, consumer) {
  const slot = consumer.node?.inputs?.[consumer.slot]
  const where = describePath(root, consumer.pathId)
  if (where.trail.length === 0) return describeTarget(consumer.node, slot)
  const input = slot?.label || slot?.localized_name || slot?.name
  return input ? `${where.text} (${input})` : where.text
}

/**
 * One verdict per REAL consumer `api.resolveLinkTargets` found, the SAME
 * per-input decision a direct target gets (`inputVerdict`). A `boundary`
 * result is a SubgraphNode input the walk was told not to descend into -- a
 * promoted widget -- which is safe by construction (`'widget'`). *seen*
 * de-duplicates by `pathId#slot` (the same consumer reached by two instances'
 * walks of one shared definition is one consumer).
 */
function classifyConsumers(root, consumers, seen) {
  const verdicts = []
  for (const consumer of consumers) {
    const key = `${consumer.pathId}#${consumer.slot}`
    if (seen.has(key)) continue
    seen.add(key)
    const label = describeConsumer(root, consumer)
    if (consumer.boundary) {
      verdicts.push({ label, safe: true, why: 'widget' })
      continue
    }
    const { safe, why } = inputVerdict(consumer.node, consumer.node?.inputs?.[consumer.slot])
    verdicts.push({ label, safe, why })
  }
  return verdicts
}

/**
 * A wire INTO a SubgraphNode input (owner ask 2026-10-03, FORMAT.md section
 * 7.10). Unplugging the OUTER wire already worked mechanically -- the
 * SubgraphNode is an ordinary node -- but its own `constructor.nodeData` does
 * not exist, so the plain classification used to fall to "unknown" and refuse
 * even when the real consumer inside is optional. What the flattened prompt
 * does once the outer wire is gone (`ExecutableNodeDTO.resolveInput`,
 * `utils/executionUtil.ts` skips an input that resolves to nothing):
 *
 *  - the SubgraphNode input is a PROMOTED WIDGET -> its own value takes over,
 *    for EVERY inner consumer: safe (`hasPromotedWidget`; the walk is told to
 *    stop there, and only there -- a promoted widget on a DEEPER SubgraphNode
 *    does not matter, because the inner link feeding it still exists);
 *  - otherwise every inner consumer loses the input, so each REAL consumer --
 *    through further nested SubgraphNodes, and on through a pass-through to a
 *    subgraph output -- is classified with `inputVerdict` and the refusal
 *    names it with its path;
 *  - nobody reads that subgraph input inside -> no consumers -> safe, nothing
 *    consumes it.
 *
 * Walked once per instance of THIS node's own graph when it sits inside a
 * shared definition (`locationsOfNode`); an unreachable graph (an unused
 * definition) still walks DOWN from here, just without a path prefix.
 */
function subgraphInputVerdicts(node, root, link, target) {
  const locations = locationsOfNode(root, node)
  const starts = locations.length > 0 ? locations : [{ graph: node.graph, prefix: '' }]
  const options = {
    stopAtSubgraphInput: (subgraphNode, slotIndex) =>
      subgraphNode === target && hasPromotedWidget(subgraphNode.inputs?.[slotIndex])
  }
  const seen = new Set()
  const verdicts = []
  for (const { graph, prefix } of starts) {
    const consumers = resolveLinkTargets(root, graph, prefix, link, options)
    verdicts.push(...classifyConsumers(root, consumers, seen))
  }
  return verdicts
}

/**
 * THIS node sits inside a subgraph and its output is wired to that subgraph's
 * OUTPUT node (owner ask 2026-10-03). The shared unplug machinery handles the
 * wire itself (number_controller.js's nested paragraph); what the guard has to
 * decide is whether the consumers on the far side survive losing it. Those
 * live OUTSIDE, one set per SubgraphNode instance of this node's definition,
 * so each instance is walked (`locationsOfNode`; `resolveLinkTargets` follows
 * the instance's own output wires, down through further SubgraphNode inputs)
 * and every real consumer is classified with the same per-input rules and the
 * same all-or-nothing refusal. An unused definition (no instance) has no
 * consumers -> safe. If the ROOT graph cannot be told apart from this subgraph
 * (a frontend without `subgraph.rootGraph` and no `app.graph` to fall back
 * on), nothing can be verified, so the wire is `unknown` and refuses.
 */
function subgraphOutputVerdicts(node, root, link) {
  let top = root
  if (top === node.graph && app.graph && app.graph !== node.graph) top = app.graph
  if (top === node.graph) return [{ label: "the subgraph's output", safe: false, why: 'unknown' }]
  const seen = new Set()
  const verdicts = []
  for (const { graph, prefix } of locationsOfNode(top, node)) {
    verdicts.push(...classifyConsumers(top, resolveLinkTargets(top, graph, prefix, link), seen))
  }
  return verdicts
}

/**
 * One verdict per LIVE link on the output: `{label, safe, why}` (a SubgraphNode
 * input or a subgraph output expands to one per REAL consumer behind it --
 * `subgraphInputVerdicts` / `subgraphOutputVerdicts`). A link that
 * `collectOutputTargets` could not record (dangling link id, missing target
 * node, an input with no name -- the legacy Reroute node's -- or a link into
 * a subgraph output this file cannot reach, e.g. in a graph that is not a
 * Subgraph) is `'unrestorable'`, because `disconnectAllTargets` severs every
 * link regardless and an unrecorded one could never come back.
 */
function collectTargetVerdicts(node) {
  const verdicts = []
  const graph = node.graph
  const links = outputOf(node)?.links
  if (!Array.isArray(links)) return verdicts
  const root = rootGraphOf(graph)
  for (const linkId of links) {
    const link = linkById(graph, linkId)
    const target = link ? graph?.getNodeById?.(link.target_id) : null
    const slot = target?.inputs?.[link?.target_slot]
    if (link && !target && subgraphOutputSlotOf(graph, link)) {
      verdicts.push(...subgraphOutputVerdicts(node, root, link))
      continue
    }
    if (link && target && slot?.name && isSubgraphNode(target)) {
      verdicts.push(...subgraphInputVerdicts(node, root, link, target))
      continue
    }
    if (!link || !target || !slot || !slot.name) {
      const gone = isSubgraphOutputLink(link) ? "the subgraph's output" : 'a wire to a missing node'
      verdicts.push({
        label: target ? describeTarget(target, slot) : gone,
        safe: false,
        why: 'unrestorable'
      })
      continue
    }
    const { safe, why } = inputVerdict(target, slot)
    verdicts.push({ label: describeTarget(target, slot), safe, why })
  }
  return verdicts
}

// ---------------------------------------------------------------------------
// Type adoption + labels (read-only with respect to every widget)
// ---------------------------------------------------------------------------

/** The colour to paint a link carrying *type* (Reroute's own rule --
 * distributor.js's `linkColorFor`). `undefined` for the wildcard. */
function linkColorFor(type) {
  if (typeof LGraphCanvas === 'undefined') return undefined
  return LGraphCanvas.link_type_colors?.[type]
}

/**
 * Every type relevant to adoption, input side FIRST so it wins
 * (distributor.js's `collectLinkTypes`, one input and one output): the input
 * link's origin output type and the link's own recorded type, then every
 * output link's target input type and recorded type. The recorded `link.type`
 * is the fallback for a peer that has not been configured yet
 * (mid-`configure()` restore ordering). Never throws.
 */
function collectLinkTypes(node) {
  const types = []
  try {
    const graph = node.graph
    const inputLink = linkById(graph, inputOf(node)?.link)
    if (inputLink) {
      const origin = graph?.getNodeById?.(inputLink.origin_id)
      types.push(origin?.outputs?.[inputLink.origin_slot]?.type, inputLink.type)
    }
    const links = outputOf(node)?.links
    if (Array.isArray(links)) {
      for (const linkId of links) {
        const link = linkById(graph, linkId)
        if (!link) continue
        const target = graph?.getNodeById?.(link.target_id)
        types.push(target?.inputs?.[link.target_slot]?.type, link.type)
      }
    }
  } catch (error) {
    console.warn(PREFIX, 'collectLinkTypes failed', error)
    return []
  }
  return types
}

/**
 * Re-derives the adopted type, both sockets' `.type`/`.label`, and the
 * colour of the links it owns, from the CURRENT wiring and toggle. Read-only
 * with respect to every widget (the re-render law), idempotent, change-gated
 * (`setDirtyCanvas` only when something moved -- the pack's 1Hz-repaint
 * lesson). Never touches `.name`. A link another module is force-colouring
 * (`LINK_COLOR_OWNER_KEY`, image_grid.js's Collect-only dim) keeps its
 * colour. Also the `LINK_COLOR_RESYNC_HOOK` seam. Never throws.
 */
function syncTypes(node) {
  try {
    const enabledWidget = findWidget(node, ENABLED_WIDGET_NAME)
    const enabled = isBypassEnabled(enabledWidget?.value)
    const candidates = collectLinkTypes(node)
    const { type, mixed } = resolveAdoptedType(candidates)
    let changed = false

    const input = inputOf(node)
    if (input) {
      if (input.type !== type) {
        input.type = type
        changed = true
      }
      const label = labelForType(type)
      if (input.label !== label) {
        input.label = label
        changed = true
      }
      const link = linkById(node.graph, input.link)
      if (link && !link[LINK_COLOR_OWNER_KEY]) {
        const color = linkColorFor(type)
        if (link.color !== color) {
          link.color = color
          changed = true
        }
      }
    }

    const output = outputOf(node)
    if (output) {
      if (output.type !== type) {
        output.type = type
        changed = true
      }
      const label = outputLabelFor(enabled, type)
      if (output.label !== label) {
        output.label = label
        changed = true
      }
      for (const linkId of Array.isArray(output.links) ? output.links : []) {
        const link = linkById(node.graph, linkId)
        if (!link || link[LINK_COLOR_OWNER_KEY]) continue
        const color = linkColorFor(type)
        if (link.color !== color) {
          link.color = color
          changed = true
        }
      }
    }

    if (mixed) {
      const distinct = [...new Set(candidates.filter((c) => !isGenericType(c)).map(String))]
      const signature = distinct.join(',')
      if (node.__epsBypassMixedWarning !== signature) {
        node.__epsBypassMixedWarning = signature
        console.warn(PREFIX, `EPS Bypass has mismatched wired types (${distinct.join(', ')}); keeping ${type}.`)
      }
    } else {
      node.__epsBypassMixedWarning = null
    }

    if (changed) node.setDirtyCanvas?.(true, true)
  } catch (error) {
    console.warn(PREFIX, 'syncTypes failed', error)
  }
}

/** Repaint-only settle: types, labels, link colours. Never writes a widget,
 * never touches the wiring -- safe from ANY hook, any number of times. */
function settle(state) {
  syncTypes(state.node)
}

// ---------------------------------------------------------------------------
// Switching off / on
// ---------------------------------------------------------------------------

/**
 * Refuses a switch-off: snaps the toggle back ON, tells the user why, and
 * leaves every wire exactly as it was. `widget.value` is set directly (never
 * through the callback -- no re-entry). It is asserted a SECOND time on the
 * next tick when the toggle still reads OFF over a wired output: the Vue
 * renderer writes its own model around the callback, and this makes the
 * refusal win in either order. Guarded by a token so a later real toggle
 * is never overridden.
 */
function refuseOff(state, blockers) {
  const { node, enabledWidget } = state
  enabledWidget.value = true
  const message = refusalMessage(blockers)
  console.warn(PREFIX, message)
  toast(node, 'warn', message, REFUSAL_TOAST_MS)
  const token = ++state.refusalToken
  setTimeout(() => {
    if (token !== state.refusalToken || !node.graph) return
    if (!isEnabled(state) && isOutputConnected(outputOf(node))) {
      enabledWidget.value = true
      node.graph.setDirtyCanvas(true, true)
    }
  }, 0)
}

/**
 * Switch OFF: refuse if any target is unsafe; otherwise remember every
 * target, then unplug them all. All-or-nothing: if a wire survives the
 * unplug (a link `disconnectAllTargets` could not resolve), everything is
 * put back and the toggle returns to ON -- a half-unplugged node reading OFF
 * would leave the memory and the graph disagreeing. An already-unwired
 * output has nothing to do and its memory is left alone.
 */
function switchOff(state) {
  const { node } = state
  const output = outputOf(node)
  if (!isOutputConnected(output)) return
  const blockers = collectTargetVerdicts(node).filter((v) => !v.safe)
  if (blockers.length > 0) {
    refuseOff(state, blockers)
    return
  }
  const targets = collectOutputTargets(node, OUTPUT_INDEX)
  writeMemory(state, targets)
  disconnectAllTargets(node, OUTPUT_INDEX)
  if (isOutputConnected(output)) {
    reconnectRememberedTargets(node, OUTPUT_INDEX, targets)
    writeMemory(state, [])
    state.enabledWidget.value = true
    toast(node, 'warn', "Couldn't unplug every wire, so EPS Bypass stays on.", REFUSAL_TOAST_MS)
  }
}

/**
 * Switch ON: replay the memory through the validated `node.connect` path
 * (ONE attempt, per-item fail-soft, never stomps a claimed socket), then
 * forget it. Says so when only some wires came back.
 */
function switchOn(state) {
  const { node } = state
  const remembered = readMemory(state)
  if (remembered.length === 0) {
    writeMemory(state, []) // drops a stale/foreign memory; no-op for '{}'
    return
  }
  reconnectRememberedTargets(node, OUTPUT_INDEX, remembered)
  writeMemory(state, [])
  const now = collectOutputTargets(node, OUTPUT_INDEX)
  const restored = remembered.filter((r) => now.some((t) => sameTarget(t, r))).length
  if (restored < remembered.length) {
    toast(
      node,
      'warn',
      `Reconnected ${restored} of ${remembered.length} wires -- the rest are gone, renamed, ` +
        'or their socket now holds another wire.',
      REFUSAL_TOAST_MS
    )
  }
}

/**
 * The one place the toggle's value becomes wiring: a click, a Universal State
 * Controller Apply (both through the widget callback) and the external-write
 * reconcile all land here. Idempotent -- a graph already in agreement with the
 * toggle is a no-op.
 */
function onEnabledChanged(state) {
  if (isEnabled(state)) switchOn(state)
  else switchOff(state)
  settle(state)
  state.node.graph?.setDirtyCanvas(true, true)
}

/**
 * The external-write route (`announceWidgetsChangedExternally`): stored OFF
 * but still wired -> guard + unplug; stored ON but unwired with a memory ->
 * replug; anything else only repaints. The widget callback has normally
 * done the work already, which makes this a no-op second pass -- it is here
 * for a writer that sets `widget.value` without firing the callback.
 */
function reconcile(state) {
  const wired = isOutputConnected(outputOf(state.node))
  if (!isEnabled(state) && wired) onEnabledChanged(state)
  else if (isEnabled(state) && !wired && readMemory(state).length > 0) onEnabledChanged(state)
  else settle(state)
}

/**
 * A NEW wire landed on an OFF node -> switch it back on and forget the
 * memory (number_controller.js's `autoReenableNewlyWiredRows`: unambiguous
 * intent, and the new wire supersedes whatever was remembered). Called ONLY
 * from the live connection hook, never from `reconcile`: "stored off, still
 * wired" is otherwise identical to a state Apply that has not caught up.
 */
function autoReenableIfWiredWhileOff(state) {
  const { node } = state
  if (isEnabled(state) || !isOutputConnected(outputOf(node))) return
  state.enabledWidget.value = true
  writeMemory(state, [])
  toast(node, 'info', 'EPS Bypass switched back on because you connected its output.')
}

// ---------------------------------------------------------------------------
// Litegraph hooks
// ---------------------------------------------------------------------------

// One shared subscription to api.js's announce event serves every attached
// Bypass (number_controller.js's / checkpoint_switcher.js's shape): routes by
// NODE IDENTITY through the `__epsBypassReload` seam stamped at attach time.
let externalWriteSubscribed = false

function installExternalWriteSubscription() {
  if (externalWriteSubscribed) return
  externalWriteSubscribed = true
  subscribeWidgetsChangedExternally((entries) => {
    for (const entry of entries || []) entry?.node?.__epsBypassReload?.()
  })
}

/**
 * Chains the toggle's callback so a click (and a state Apply's
 * `widget.callback?.(...)`) unplugs/replugs. Chained, never replaced.
 * Wrapped in try/catch: a failure here must never break the click itself.
 */
function wireToggle(state) {
  const { enabledWidget } = state
  const original = enabledWidget.callback
  enabledWidget.callback = function (...args) {
    const result = typeof original === 'function' ? original.apply(this, args) : undefined
    try {
      onEnabledChanged(state)
    } catch (error) {
      console.warn(PREFIX, 'toggle handler failed', error)
    }
    return result
  }
}

/**
 * Chains `configure`/`onConnectionsChange` so wiring/unwiring either socket
 * re-derives types and labels -- distributor.js's `wireOutputGrowth` shape
 * (both litegraph findings its docstring cites apply: `disconnectOutput`
 * dispatches `onConnectionsChange` BEFORE it returns, so the live path
 * defers a macrotask, and litegraph's restore loop fires it with a hardcoded
 * `isConnected = true` for slots that have no link, so the argument is never
 * trusted). `restoring` blanks scheduling for this node's own `configure()`;
 * `attach`'s `onConfigure` wrap runs its own settle afterwards.
 *
 * The deferred pass runs `autoReenableIfWiredWhileOff` ONLY when it was
 * scheduled by a real USER connection event (`fromConnection`): not by the
 * post-load settle, and not by a connection event that fires while the whole
 * graph is being (re)configured -- `isGraphConfiguring()` is read AT EVENT
 * TIME, because by the time the deferred pass runs a synchronous load has
 * always finished and the flag is long back to `false`. A load / undo / redo
 * / tab switch restores links through exactly such events, and it must never
 * be mistaken for the user dragging a wire onto an OFF node (loading never
 * rewires -- module docstring).
 * @returns {(fromConnection: boolean) => void} the scheduler, for the
 *   post-configure settle.
 */
function wireConnectionSync(state) {
  const { node } = state
  const hook = { restoring: false, scheduled: false, reenable: false }

  function runDeferred() {
    hook.scheduled = false
    const reenable = hook.reenable
    hook.reenable = false
    // A configure() that started while this was pending runs its own pass in
    // onConfigure; a node removed from the graph meanwhile needs none.
    if (hook.restoring || !node.graph) return
    try {
      if (reenable) autoReenableIfWiredWhileOff(state)
      settle(state)
    } catch (error) {
      console.warn(PREFIX, 'deferred sync failed', error)
    }
  }

  function schedule(fromConnection) {
    if (fromConnection) hook.reenable = true
    if (hook.scheduled) return
    hook.scheduled = true
    setTimeout(runDeferred, 0)
  }

  const originalConfigure = node.configure
  node.configure = function (...args) {
    hook.restoring = true
    try {
      return originalConfigure?.apply(this, args)
    } finally {
      hook.restoring = false
    }
  }

  const originalOnConnectionsChange = node.onConnectionsChange
  node.onConnectionsChange = function (type, index, isConnected, linkInfo, slot) {
    let result
    if (typeof originalOnConnectionsChange === 'function') {
      result = originalOnConnectionsChange.apply(this, arguments)
    }
    // Only OUR two sockets: the `enabled`/`links` widget inputs also fire
    // this when something is wired into them, and that is none of our
    // business here. An output-kind event for OUR output index counts even
    // when `slot` is not our output object: a subgraph output's own
    // `disconnect()` (the unplug of a wire into this node's subgraph output,
    // v1.2.0 nested reach) passes the SubgraphOutput as `slot` and this
    // node's real output index as `index`.
    const ours =
      slot === outputOf(this) ||
      slot?.name === INPUT_NAME ||
      (type === NODE_SLOT_OUTPUT && index === OUTPUT_INDEX)
    if (!hook.restoring && ours) schedule(!isGraphConfiguring())
    return result
  }

  return schedule
}

// ---------------------------------------------------------------------------
// Public entry points (called from web/eps_image.js)
// ---------------------------------------------------------------------------

/** Frontend-only one-time setup. EPSBypass is a real backend node (no
 * frontend-only type registration) -- everything is per-instance, in
 * attach(). Kept as an export because eps_image.js calls it unconditionally. */
export function init() {}

/**
 * Per-node-instance attach; no-op unless *node* is an EPSBypass. Never throws
 * (section 7/8): a failure is logged and leaves a plain, working pass-through
 * whose OFF is the backend's blocker fallback.
 */
export function attach(node) {
  try {
    if (!node) return
    if (nodeClassOf(node) !== CLASS_ID) return
    if (attachedNodes.has(node)) return
    const enabledWidget = findWidget(node, ENABLED_WIDGET_NAME)
    const linksWidget = findWidget(node, LINKS_WIDGET_NAME)
    if (!enabledWidget || !linksWidget) {
      console.warn(PREFIX, 'EPSBypass node is missing its `enabled`/`links` widgets; not attached')
      return
    }
    attachedNodes.add(node)

    hideValuesWidget(node, linksWidget)
    installMinWidth(node, MIN_NODE_WIDTH)

    const state = { node, enabledWidget, linksWidget, refusalToken: 0 }

    wireToggle(state)
    const schedule = wireConnectionSync(state)

    // The colour-convention seam (distributor.js's LINK_COLOR_RESYNC_HOOK
    // docstring) and the Universal State Controller seam.
    node[LINK_COLOR_RESYNC_HOOK] = () => syncTypes(node)
    node.__epsBypassReload = () => reconcile(state)
    installExternalWriteSubscription()

    // `onConfigure` fires at the very end of `configure()` for a whole-graph
    // load AND a paste. Settle once now (the widgets are restored; labels
    // must not flash wrong) and once more after the whole graph has been
    // configured (a peer may not have restored its inputs yet). Neither pass
    // rewires anything -- loading never does.
    const originalOnConfigure = node.onConfigure
    node.onConfigure = function (info) {
      const result = originalOnConfigure?.apply(this, arguments)
      try {
        settle(state)
        schedule(false)
      } catch (error) {
        console.warn(PREFIX, 'post-configure settle failed', error)
      }
      return result
    }

    // A fresh node: seed the `any` labels.
    settle(state)
  } catch (error) {
    console.warn(PREFIX, 'attach failed', error)
  }
}
