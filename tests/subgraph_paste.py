"""Shared by the three unplug-memory probe files (Number Controller, EPS
Bypass, EPS switcher): the one place that models what ComfyUI frontend 1.52.7
does with node ids when a SUBGRAPH is pasted.

SIMULATED, not executed: ``remapPastedInteriorIds`` is a port of the id
assignment in ``remapClipboardSubgraphNodeIds``
(``src/lib/litegraph/src/LGraphCanvas.ts`` lines 9095-9140 of the 1.52.7
sources, read from the frontend package's source maps), and
``LGraphCanvas._deserializeItems`` (lines 4227-4246 of the same file) is what
the probes imitate around it: every pasted subgraph DEFINITION gets a new
UUID, ``graph.createSubgraph(info)`` + ``subgraph.configure(info)`` then
configure each interior node with its (remapped) real id, and nothing
touches widget values or properties, so a remembered target id inside them
keeps pointing at the id it had in the source workflow.

Not simulated: the rest of litegraph. A probe's fake graph stands in for a
subgraph by carrying an ``id`` (the definition's UUID) and
``isRootGraph = false`` -- all that ``Subgraph`` / ``LGraph`` expose which the
code under test reads (``node.graph``, set by ``LGraph.add`` before the node
is configured: LGraph.ts line 1007).
"""

SUBGRAPH_PASTE_JS = r"""
/** Port of remapClipboardSubgraphNodeIds' id logic: *interiorIds* in clipboard
 * order, *rootIds* the ids already used in the destination workflow,
 * *lastNodeId* its counter. Returns [{from, to}] in the same order. */
function remapPastedInteriorIds(interiorIds, rootIds, lastNodeId) {
  const used = new Set(rootIds)
  let last = lastNodeId
  for (const id of rootIds) if (last < id) last = id
  const next = () => { while (used.has(++last)); used.add(last); return last }
  return interiorIds.map((from) => {
    if (used.has(from)) return { from, to: next() }
    used.add(from)
    if (last < from) last = from
    return { from, to: from }
  })
}
"""
