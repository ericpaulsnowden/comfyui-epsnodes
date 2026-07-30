/**
 * @file EPS Image Grid frontend (FORMAT.md §6.6). Exports the `init()`/
 * `attach(node)`/`loadedGraphNode(node)` hooks `web/eps_image.js` calls;
 * each no-ops for every node type other than `EPSImageGrid`.
 *
 * M1: identity/dedup for the hidden `grid_uuid` widget, and a `Clear`
 * button. ORIGINALLY (M1) the thumbnail grid was free from ComfyUI core —
 * the backend returned `{"ui": {"images": [...whole buffer...]}}` on every
 * Run, which core turned into `node.imgs` unconditionally (no different for
 * an `OUTPUT_NODE` than for `SaveImage`/`PreviewImage`) — because that
 * response always carried the COMPLETE current buffer, core's own ordinary
 * replace-per-run handling was all it took to make the grid look like it
 * was growing across separate Runs.
 *
 * **This stopped being true 2026-07-22** (see the dated section below,
 * "Execution-complete refresh") — the backend now reports only what THIS
 * Run actually appended (nothing at all for an Emit Run), so the grid is no
 * longer free: this file now owns keeping it in sync, via its own explicit
 * refresh triggered off this node's execution-complete signal.
 *
 * M2: copy OUT (OS clipboard + ComfyUI clipspace) and paste IN (Ctrl+V adds
 * to the buffer).
 *
 * ---- Copy OUT is entirely free from core — verified against the actual
 * installed frontend source, not assumed ----
 *
 * `docs/FORMAT.md` §6.6 describes wiring "Copy image (clipboard)" / "Copy
 * (Clipspace)" as right-click menu items. Reading the exact ComfyUI
 * checkout this pack is built against (`ComfyUI_frontend/src/services/
 * litegraphService.ts` `addNodeContextMenuHandler`, which every node class
 * gets via the SAME generic registration path custom node defs go through
 * -- not a core-only allowlist) shows `LGraphNode.prototype.
 * getExtraMenuOptions` already adds, for ANY node:
 *   - "Copy Image" (OS clipboard: canvas -> `toBlob('image/png')` ->
 *     `navigator.clipboard.write([ClipboardItem])`, feature-detected on
 *     `window.ClipboardItem` and gated on `this.imgs` + a selected/hovered
 *     image) -- the EXACT mechanism FORMAT.md §6.6 names.
 *   - "Copy (Clipspace)" (`ComfyApp.copyToClipspace`, `scripts/app.ts`) --
 *     UNCONDITIONAL, not even gated on `this.imgs`.
 *   - "Paste (Clipspace)" (`ComfyApp.pasteFromClipspace`) -- shown whenever
 *     `ComfyApp.clipspace != null` (something has been copied by ANY node).
 *   - "Open Image" / "Save Image" (same `this.imgs` gate as Copy Image).
 * The Vue "More Options" popover surfaces equivalent items independently
 * (`composables/graph/useImageMenuOptions.ts` `getImageMenuOptions`, wired
 * from `useMoreOptionsMenu.ts` for any `hasImageNode` selection). Both
 * surfaces key off `node.imgs` (which core already populates for us every
 * Run, M1) -- there is nothing to build here; the file's own job is to
 * click-and-confirm these actually render for `EPSImageGrid`, which
 * `imageIndex` (set by clicking a cell in the free grid pager) already
 * targets at the specific cell the user selected.
 *
 * ---- Paste IN needs real code — `node.pasteFiles` ----
 *
 * There's no free equivalent for "add an image to this specific node's
 * durable buffer" (core's paste only ever replaces a node's in-memory
 * `imgs`/widget value, per `ComfyApp.pasteFromClipspace` above -- it never
 * calls any of our routes). FORMAT.md §6.6: "Ctrl+V on the selected node
 * uploads the pasted image (`POST /upload/image`) and appends it (`POST
 * /eps_image_grid/add`)". The real Ctrl+V dispatch (`composables/
 * usePaste.ts`'s global `document` "paste" listener) only routes pasted
 * image data to the SELECTED node when `isImageNode(node)` is true
 * (`utils/litegraphUtil.ts`: `previewMediaType === 'image' || (!video &&
 * imgs.length)`) -- otherwise it creates a brand-new `LoadImage` node
 * instead. Setting `node.previewMediaType = 'image'` in `attach()` (a
 * documented, typed node property other core code already sets this same
 * way for its own image-preview nodes, `composables/node/useNodeImage.ts`)
 * makes that true EVEN ON A STILL-EMPTY BUFFER (where `imgs.length` alone
 * would be falsy) -- the exact case that matters for "paste something
 * before you've ever Collected anything". Once routed, `usePaste.ts` calls
 * `node.pasteFiles?.(files)` (via `pasteItemsOnNode`); the SAME free
 * "Paste Image" menu item mentioned above (`useImageMenuOptions.ts`'s
 * `canPasteImage`, gated on `typeof node.pasteFiles === 'function'`) reads
 * the OS clipboard directly and calls the exact same function -- so
 * installing `pasteFiles` here (`installPasteFiles()` below) wires BOTH
 * the keyboard and the explicit menu path at once.
 *
 * `pasteFiles`/`useNodePaste`/`useNodeImageUpload` are themselves Vue
 * composables living in the app's own bundle (no stable import path for a
 * plain extension script), so the upload (`POST /upload/image`, core's
 * route) and the `/eps_image_grid/add` call are reimplemented directly
 * here rather than imported -- same shapes, verified against
 * `composables/node/useNodeImageUpload.ts`'s `uploadFile` for the request,
 * and `server.py`'s `image_upload` for the `{name,subfolder,type}`
 * response this hands straight to `/add`.
 *
 * ---- 0.28.1 / clipboard-API sensitivity ----
 *
 * Copy OUT (above) is core's own code, already feature-detected there
 * (`typeof window.ClipboardItem === 'undefined'` short-circuits `Copy
 * Image` to a no-op rather than throwing). Paste IN here uses the native
 * DOM `"paste"` event's `clipboardData` -- NOT `navigator.clipboard.read()`
 * -- so it needs no permission prompt and works the same across browsers
 * that support the clipboard event at all; the "Paste Image" MENU item
 * (free, core's) is the one path that does call `navigator.clipboard.read()`
 * and could differ across browsers/frontend versions. All of the above was
 * verified against this rig's installed `comfyui-frontend-package` 1.45.21
 * source, not the owner's actual ComfyUI 0.28.1 -- re-verify live if
 * anything here doesn't match.
 *
 * ---- Per-node identity + dedup (FORMAT.md §6.6) ----
 *
 * `grid_uuid` is a HIDDEN (`.hidden = true`, the same trick FORMAT.md §7.2
 * uses for the Prompt Notebook's `file` widget, and this pack's own
 * `nodes_switcher.py` `toggles` widget) but genuinely serialized STRING
 * widget — it has to reach the Python backend (which keys the on-disk
 * buffer by it), and `node.properties` alone never does that (properties
 * round-trip through save/reload but are never sent in a queued `/prompt`).
 * So this file keeps BOTH a `node.properties.uuid` copy (for our own
 * bookkeeping/dedup reads) and the widget's value (what execute() actually
 * sees) in lockstep.
 *
 * The dedup problem: litegraph only self-heals a node's own NUMERIC `.id`
 * on copy/paste and cross-workflow load — it happily restores an arbitrary
 * serialized property or widget value VERBATIM, uuid included. So a paste,
 * or opening a second saved workflow that already has a live node using the
 * same uuid, produces two nodes pointing at ONE buffer unless something
 * notices and re-mints one of them. Two hooks cover the two ways that
 * happens, confirmed against the exact ComfyUI checkout/`comfyui-frontend-
 * package` build this was built and verified against:
 *
 * 1. **`nodeCreated` (this file's `attach()`), deferred one tick** — the
 *    paste path. Litegraph creates the new node instance FIRST (firing
 *    `nodeCreated` synchronously, before any serialized data is applied),
 *    THEN calls `node.configure(clipboardData)` to restore the pasted
 *    node's widgets/properties — including the SAME `grid_uuid` the
 *    original has, which is the actual moment the collision is created.
 *    Checking synchronously inside `attach()` would only ever see the
 *    still-empty, just-constructed default; `setTimeout(fn, 0)` runs after
 *    that same-tick `configure()` call has already restored the real
 *    (possibly colliding) value, matching this pack's own already-verified
 *    finding for property restore timing (`eps_image/resolution.js`'s
 *    header: "`nodeCreated` ... always runs BEFORE `LGraphNode.configure()`
 *    for a saved workflow ... confirmed live and in `LGraphNode.ts`").
 *
 * 2. **`loadedGraphNode` (this file's `loadedGraphNode()`)** — the
 *    cross-workflow-load path (opening/dragging in a saved workflow).
 *    Verified directly against the installed `comfyui-frontend-package`'s
 *    bundled `dialogService-*.js`: after the WHOLE graph's own `configure()`
 *    call finishes (every node's widgets/properties already restored to
 *    their saved values), a SEPARATE traversal fires this hook once per
 *    node — `br(this.rootGraph, e => { ...; useExtensionService()
 *    .invokeExtensions("loadedGraphNode", e) })`. Since every sibling node
 *    is already fully configured by the time ANY node's `loadedGraphNode`
 *    fires, the collision check here runs synchronously against the whole
 *    graph, no deferral needed. This does NOT fire for a same-session paste
 *    (that never runs the whole-graph configure pass) — hence hook 1 above
 *    still being needed separately.
 *
 * Both funnel through the same idempotent `ensureUniqueUuid()`: a valid,
 * non-colliding uuid is left untouched; an empty/invalid one (first create)
 * or one that collides with a live sibling gets a fresh
 * `crypto.randomUUID()`, written to the widget's `.value` directly (that's
 * what actually persists/serializes -- confirmed live: a widget's
 * `.callback` is a NOTIFICATION hook, not a setter, so calling it alone
 * without also assigning `.value` leaves the old value in place) and then
 * ALSO through its `.callback`, if any, so anything else observing
 * widget-value changes sees it too. Since the 2026-07-20 fixes below,
 * `ensureUniqueUuid()` is `async`: the COLLISION branch (a genuine
 * live-sibling duplicate) additionally clones the OLD uuid's buffer into
 * the fresh one before returning; the first-create branch (empty/invalid
 * uuid, nothing to clone) still just mints and returns.
 *
 * ---- Bug fixes 2026-07-20: display-on-load + copy independence ----
 *
 * Two distinct owner reports, both rooted in the same fact: `node.imgs`
 * (what core's free thumbnail grid actually draws, M1) is populated ONLY by
 * a Run's `{"ui":{"images":[...]}}` response or the M2 paste-add path --
 * it is never serialized with the node, and NOTHING previously re-read the
 * on-disk buffer on load. So a reloaded, pasted, or undone node showed an
 * EMPTY grid until the next Run, even though the buffer was intact on disk
 * the whole time.
 *
 * 1. **"I copy-pasted the grid node from one workflow to a second workflow
 *    and the images didn't travel."** Two separate causes:
 *    - Display: opening a second workflow is exactly the `loadedGraphNode`
 *      path above -- it fixed the uuid but never told the canvas to show
 *      anything.
 *    - True in-graph duplicates (paste within the SAME graph, hook 1's
 *      collision branch): `ensureUniqueUuid()` minted a fresh uuid for the
 *      duplicate but never copied the source's buffer, so the fresh uuid
 *      pointed at a genuinely empty folder even after a display refresh.
 *      Fixed by cloning: `POST /eps_image_grid/clone {from: oldUuid, to:
 *      newUuid}` (`store.clone_buffer`) right after minting, so the
 *      duplicate gets its own independent copy of the images -- verified
 *      independent both ways (Clearing the original leaves the copy's
 *      images intact, and vice versa).
 * 2. **"I copied an image out of the grid into another node, then pressed
 *    undo a few times -- the grid cleared itself and the images never came
 *    back."** Undo/redo re-applies a node's PRIOR serialized state through
 *    `LGraphNode.configure()` directly -- it does NOT recreate the node
 *    instance, so `nodeCreated`/`attach()` never runs again. `node.imgs`
 *    was never part of that serialized state either way, so undo left
 *    whatever `node.imgs` happened to hold at that moment (frequently
 *    nothing) on screen -- looking exactly like the buffer itself had been
 *    wiped, when it was untouched on disk the entire time.
 *
 * The fix for both is the same primitive, `refreshFromBuffer(node)` (`GET
 * /eps_image_grid/list?uuid=` -> `setNodeImagesFromRefs`), called from
 * every place a node's uuid can become "the one to display" without a Run
 * ever happening: the deferred `nodeCreated` tail in `attach()` (after
 * `ensureUniqueUuid` settles, so it fetches the correct, possibly-just-
 * cloned uuid), `loadedGraphNode()` (after its own dedup call), and a
 * wrapped `onConfigure` (installed once per node, guarded by
 * `node.__epsGridConfigureWrapped` -- this is the undo fix, since
 * `onConfigure` is the one hook that fires on undo/redo without `attach()`
 * running first). Every one of these fails soft (a missing route on an
 * older backend, a network hiccup, or a not-yet-valid uuid all just leave
 * today's display untouched) -- this is pure ADDITIONAL visibility into a
 * buffer that was already safe, never a new way to lose data.
 *
 * ---- Identity is STABLE; refresh is CHEAP (fix 2026-07-21, owner-reported
 * regressions from the block above) ----
 *
 * The 2026-07-20 fix above was too aggressive. `loadGraphData` on this
 * frontend (`scripts/app.ts`) rebuilds the ENTIRE node list on every whole-
 * graph load -- initial open, undo/redo (`changeTracker.ts`'s `undo()`/
 * `redo()` both call `app.loadGraphData()`), and tab switch all go through
 * it (confirmed live against this rig's `comfyui-frontend-package` 1.45.21).
 * Each rebuild wipes `LGraph._nodes` and creates FRESH node object instances
 * for every serialized node, even ones whose data never changed. If a SECOND
 * rebuild starts before an EARLIER one's deferred `attach()` dedup check
 * (the `setTimeout(fn, 0)` below) has fired -- trivially reproduced live by
 * firing a few overlapping `app.loadGraphData()` calls without awaiting each
 * one, which is exactly what rapid repeated Ctrl+Z or fast tab-switching can
 * do -- a stale, about-to-be-discarded node instance and the fresh,
 * currently-displayed one briefly coexist in `app.graph._nodes` sharing the
 * SAME uuid. `ensureUniqueUuid()`'s collision check couldn't tell "a
 * genuine in-graph duplicate" apart from "the same logical node, mid-
 * rebuild, momentarily double-represented" -- so it fired the same
 * remint+clone either way, on nodes that were never actually duplicated.
 * Verified live: a single 13-image grid, reloaded a handful of times
 * without waiting for each to settle, logs multiple spurious "collided with
 * a live sibling" mints and clones -- harmless orphan buffers when the
 * CURRENT node happens to settle back onto the original uuid, but nothing
 * guaranteed that; the owner's report ("13 -> 4", thumbnails vanishing) is
 * this same race landing less kindly.
 *
 * The fix distinguishes the two cases with `app.configuringGraph`
 * (`scripts/app.ts`): a private counter incremented for the exact duration
 * of `LGraph.prototype.configure()`, which every whole-graph rebuild above
 * runs through. Confirmed LIVE against this rig's installed frontend by
 * instrumenting the pack's own registered extension hooks directly (not
 * just timing theory): our `nodeCreated` hook (`attach()` below) and a
 * wrapped `LGraphNode.prototype.configure` (what the `onConfigure` wrap
 * below observes) both read `app.configuringGraph === true` while a whole-
 * graph load/undo/redo/tab-switch is rebuilding nodes; a genuine paste/
 * clone (`LiteGraph.createNode()` + a LONE `node.configure()`, never
 * wrapped by the graph-level flag) reads `false` at both points.
 * `loadedGraphNode()` itself always observes `false` (it fires only AFTER
 * `rootGraph.configure()` has already returned) -- but it ONLY EVER fires
 * from within that same whole-graph rebuild in the first place (confirmed
 * live: never for a same-session paste), so its own invocation already IS
 * the "don't mint" signal, no flag needed there.
 *
 * `isGraphConfiguring()` below reads that flag; `attach()` captures it
 * SYNCHRONOUSLY at `nodeCreated` time (before deferring) rather than
 * re-reading it inside the deferred callback, because `LGraph.configure()`
 * is fully synchronous and reliably finishes -- flag back to `false` --
 * before any `setTimeout(fn, 0)` macrotask gets a turn. `ensureUniqueUuid()`
 * now takes an explicit `allowCollisionMint` flag: when false (this check
 * is running as part of a load/undo/configure pass), a valid uuid that
 * "collides" is left COMPLETELY untouched -- no remint, no clone, no
 * buffer-repointing side effect -- because the saved uuid is authoritative
 * there and the apparent collision is transient rebuild noise, never a real
 * duplicate. An actually invalid/empty uuid (a hand-edited or corrupted
 * workflow) still self-heals to a fresh one even during a load -- there is
 * no buffer to protect for a uuid that was never valid. `loadedGraphNode()`
 * always passes `allowCollisionMint: false` unconditionally, both because it
 * can't be true (see above) and as defense-in-depth if that ever changes.
 * Only a genuine interactive duplicate detected OUTSIDE any configure pass
 * (`attach()` when `isGraphConfiguring()` read false at creation time) still
 * mints + clones, preserving the 2026-07-20 "copy carries the images"
 * behavior for the case it actually describes.
 *
 * **0.28.1 risk:** `app.configuringGraph` is an internal flag (its own
 * comment in `scripts/app.ts` calls it exactly that) -- not documented
 * public extension API -- and its presence/behavior on the frontend build
 * ComfyUI 0.28.1 actually pins has NOT been independently verified (this rig
 * runs `comfyui-frontend-package` 1.45.21). `isGraphConfiguring()` reads it
 * via a plain property access and coerces with `Boolean(...)`, so a
 * frontend build where the property is simply absent degrades to `false`
 * (fails OPEN to the pre-2026-07-21 remint-on-collision behavior for that
 * one check) rather than throwing -- never worse than before 2026-07-21,
 * but re-verify live on 0.28.1 if this specific guard doesn't hold there.
 *
 * Separately, CHEAP: `refreshFromBuffer()` was being triggered up to THREE
 * times per node per load (the deferred `attach()` tail, `loadedGraphNode`,
 * and `onConfigure` each fire once for the very same settle) -- confirmed
 * live via the network log. `scheduleRefresh()` below is the single funnel
 * every call site now goes through: concurrent calls for the same node
 * coalesce onto one in-flight fetch, and a call arriving just after one
 * already settled is skipped outright (nothing changed in the meantime).
 * The `onConfigure` wrap additionally skips scheduling entirely while
 * `isGraphConfiguring()` is true, since `loadedGraphNode` is about to (or
 * just did) cover the exact same settle -- it still refreshes for a
 * standalone per-node `configure()` outside a load pass (a paste/clone
 * restore), the one case nothing else reaches. `setNodeImagesFromRefs()`
 * also no longer focuses the last image after a refresh or a paste-add --
 * it sets `imageIndex = null` (core's own "show the grid" sentinel) per
 * FORMAT.md §6.6, fixing the "paste shows one image, no way back to the
 * grid until you switch tabs" report; `setDirtyCanvas` still repaints
 * immediately, no tab round-trip required.
 *
 * ---- Mac-only fixes 2026-07-21: Copy Image (insecure context) +
 * late-image repaint ----
 *
 * Two more owner reports, both reproducible ONLY on Eric's Mac. Ground
 * truth for both: ComfyUI runs on Eric's Windows PC; the Mac reaches it
 * over the LAN at `http://<pc-ip>:8188` -- plain http, never `localhost` --
 * so `window.isSecureContext` is `false` there. Both machines load the
 * exact SAME frontend build (served by the PC), so a PC-vs-Mac difference
 * can only come from the browser/network, never from different code.
 * Neither fix below could be driven live from this session -- the shared
 * verification rig is localhost-only on a different frontend build, so it
 * cannot reproduce either bug by construction -- both need Eric's actual
 * Mac to confirm.
 *
 * 1. **"On the Mac I only see Copy (Clipspace), not Copy Image."**
 *    Mechanism confirmed against frontend SOURCE, not guessed (see
 *    `canUseOsClipboardImage`'s docstring below for exact file/line
 *    citations): core's "Copy Image" menu item feature-detects
 *    `window.ClipboardItem` and silently omits itself when that's
 *    `undefined` -- which the Clipboard API spec makes true for ANY
 *    insecure context, not just a broken/old browser. "Copy (Clipspace)"
 *    has no such gate (it never touches the Clipboard API, only internal
 *    `node.imgs` bookkeeping), so it alone survives -- exactly the split
 *    reported. This is a BROWSER security boundary, not a ComfyUI or pack
 *    bug, and it cannot be bypassed from here -- only gracefully degraded
 *    around. Fix: `installCopyImageMenuItem` below adds an
 *    EPSImageGrid-owned "Copy image" item (wrapping, never replacing, this
 *    node's `getExtraMenuOptions` -- the same per-instance idiom
 *    `installPasteFiles`/`installConfigureRefresh` already use) that does
 *    the real OS-clipboard copy when the APIs exist, and otherwise copies
 *    the image's URL as text (`document.execCommand('copy')`, which has no
 *    secure-context requirement) and opens the image in a new tab for a
 *    native browser-level "Copy Image" -- plus a toast that says plainly
 *    why a true OS image-copy isn't happening. "Copy (Clipspace)" remains
 *    the one path that's identical in every context.
 * 2. **"Undo still clears the grid preview on the Mac, works fine on the
 *    PC."** `setNodeImagesFromRefs` (backing every refresh/paste-add path
 *    above) sets each new `Image`'s `.src` and returns immediately --
 *    decoding is always async -- and every caller repaints exactly ONCE,
 *    right away. Confirmed in litegraph source: the render loop
 *    (`LGraphCanvas.ts`'s `startRendering`) calls `draw()` every animation
 *    frame unconditionally, but `draw()` itself only repaints a layer when
 *    its `dirty_canvas`/`dirty_bgcanvas` flag is set -- an image that
 *    finishes loading AFTER that one repaint call never appears until
 *    something ELSE marks the canvas dirty. On localhost (the PC) these
 *    `/view` fetches are done before that repaint even happens; over the
 *    Mac's LAN hop to the PC they routinely aren't -- reproducing exactly
 *    as "the grid cleared and never came back" (nothing else touches this
 *    node's canvas once an undo settles). The fix is litegraph's OWN
 *    established idiom for this exact problem -- `LGraphNode.prototype.
 *    loadImage()` already repaints from the image's own `load` event, not
 *    from the moment `.src` was set -- so `setNodeImagesFromRefs` now does
 *    the same for every image it creates. Whichever image is slowest to
 *    arrive is also the one that gets the canvas its final, correct paint.
 *    Correct and cheap for ANY client, not only a slow one, so it's
 *    included even though only Eric's Mac can fully confirm it firing
 *    fixes the report.
 *
 * ---- Execution-complete refresh (2026-07-22 owner fix: output-panel
 * pollution) -- the grid is no longer free ----
 *
 * Owner report: "If I run a grid with 10 images through an image editor, I
 * see 10 new images AND the 10 original images in the generated output
 * panel, every run." Root cause was `nodes_image_grid.py` reporting
 * `ui.images` = the WHOLE buffer on EVERY execution (the M1 "free grid"
 * design, file header above) -- core's own `/history`-output bookkeeping
 * (what the generated-images panel renders) faithfully recorded that whole
 * buffer again on every single Run, not just what was actually new.
 *
 * `nodes_image_grid.py`'s fix (see its own module docstring) narrows
 * `ui.images` to ONLY the refs a Run actually appended -- omitted entirely
 * when nothing was (Collect with nothing wired, or an invalid/not-yet-
 * minted `grid_uuid`), and NEVER present at all for an Emit Run. Confirmed
 * against this repo's `execution.py`: a return dict with no `"ui"` key
 * means `get_output_from_returns` never populates its `uis` list, so
 * `output_ui` stays `{}`, and `if len(output_ui) > 0` (the SAME condition
 * gating both the `ui_outputs[unique_id]` cache write and the
 * `server.send_sync("executed", ...)` call) is false -- NO `"executed"`
 * websocket event reaches the frontend for that Run at all, not even one
 * with an empty `images` list.
 *
 * That leaves nothing to keep `node.imgs` in sync for those Runs -- core's
 * own `"executed"` handling (`scripts/app.ts`'s `addApiUpdateHandlers`:
 * `nodeOutputStore.setNodeOutputsByExecutionId(executionId, detail.output,
 * {merge: detail.merge})`, feeding `composables/node/useNodeImage.ts`'s
 * reactive `showPreview()`, which does `node.imgs = elements` -- a full
 * REPLACE, never a merge, confirmed both by that assignment itself and by
 * `stores/nodeOutputStore.ts`'s `setOutputsByLocatorId` never actually
 * receiving a true `merge` flag from THIS backend: `zExecutedWsMessage
 * .merge` is a real, documented optional field on the message, but nothing
 * in this repo's Python backend ever sets it -- `execution.py`'s two
 * `send_sync("executed", ...)` call sites are the ONLY senders) simply
 * never fires for those Runs. Confirmed the other direction too: when it
 * DOES fire (Collect with something newly appended), `setOutputsByLocatorId`
 * REPLACES `app.nodeOutputs[locatorId]` with JUST those new refs (no
 * merge), so core's own handling would show ONLY the newest image(s), not
 * the whole buffer, if left alone -- confirming `node.imgs` needs this
 * file's OWN correction either way a Run's `ui` result can now look.
 *
 * Fix: `installExecutionRefreshListener()` below installs ONE module-scope
 * `api.addEventListener('progress_state', ...)`. Unlike `"executed"`,
 * `progress_state` is sent UNCONDITIONALLY on every successful node
 * completion -- `comfy_execution/progress.py`'s
 * `ProgressRegistry.finish_progress()`, called from BOTH of
 * `execution.py`'s `execute()` success paths (the cached-result early
 * return AND the normal fresh-execution tail) right before each returns
 * `ExecutionResult.SUCCESS`, with no dependency on `ui`/`output_ui`
 * whatsoever. Its payload shape (`apiSchema.ts`'s `zProgressStateWsMessage`:
 * `{prompt_id, nodes: {[nodeId]: {state: 'pending'|'running'|'finished'|
 * 'error', ...}}}`, matching `progress.py`'s `_send_progress_state` field
 * for field) reports every node that has started so far in the CURRENT
 * prompt, so the listener checks specifically for each live EPSImageGrid
 * node's OWN id transitioning into `'finished'`, then calls the existing
 * `scheduleRefresh()` (unchanged, M1/M2-era) -- the SAME "fetch `/list`,
 * repopulate `node.imgs` from the WHOLE buffer" primitive the 2026-07-20/21
 * load/undo fixes already use -- so the grid ends up correct (the full
 * buffer, always) regardless of what that Run's own `ui` result happened to
 * contain. `lastKnownProgressState` (a `WeakMap`) additionally skips a node
 * already known `'finished'` -- the registry resends every non-pending
 * node's state on EVERY change for the rest of that prompt (`progress.py`'s
 * `active_nodes` comprehension includes anything not `Pending`), so without
 * this a later, unrelated node finishing later in the same prompt would
 * re-trigger an extra (harmless, but pointless once outside
 * `scheduleRefresh`'s own settle window) `/list` fetch.
 *
 * Verified against this rig's installed `comfyui-frontend-package` 1.45.21:
 * `scripts/api.ts`'s socket message handler dispatches `progress_state` as
 * a plain `CustomEvent` with `detail` = the raw message payload verbatim
 * (`case 'progress_state': ... this.dispatchCustomEvent(msg.type,
 * msg.data)`) -- the exact event `stores/executionStore.ts`'s own
 * `handleProgressState` consumes for the app's built-in per-node progress
 * UI, so this listens to the same public signal core's own indicators use,
 * not an undocumented internal.
 *
 * ---- Clipspace paste appends, doesn't replace (2026-07-22 owner fix) ----
 *
 * Owner report: paste one image into an empty grid via clipspace, fine;
 * paste a SECOND one and it appears to overwrite the first ("there should
 * be some way to see both"). This is a DIFFERENT path from the M2 Ctrl+V
 * paste above -- confirmed against frontend source, not guessed.
 *
 * "Paste (Clipspace)" is a context-menu item added by core's OWN
 * `getExtraMenuOptions` (`services/litegraphService.ts`'s
 * `addNodeContextMenuHandler`, installed once on the node CLASS's
 * prototype at registration -- the exact method this file's OWN
 * `installCopyImageMenuItem` below already wraps per-instance), gated on
 * `ComfyApp.clipspace != null` (something has been copied by ANY node),
 * with callback `() => { ComfyApp.pasteFromClipspace(this) }`. Confirmed
 * this is the ONLY place in the installed frontend that adds a clipspace-
 * paste item at all -- the Vue "More Options" popover's own image menu
 * (`composables/graph/useImageMenuOptions.ts`'s `getImageMenuOptions`) has
 * no clipspace equivalent, only "Paste Image" (the OS-clipboard path,
 * `node.pasteFiles`, already covered by `installPasteFiles` above).
 *
 * `ComfyApp.pasteFromClipspace(node)` (`scripts/app.ts`), for the plain
 * "one image, default paste mode" case that reproduces the report, does
 * exactly this to `node.imgs`:
 * ```
 * const img = new Image()
 * img.src = ComfyApp.clipspace.imgs[ComfyApp.clipspace.selectedIndex].src
 * node.imgs = [img]          // <- REPLACE, never append
 * node.imageIndex = 0
 * ```
 * (`img_paste_mode: 'all'` -- reachable via the Clipspace dialog's own
 * paste-mode selector, `extensions/core/clipspace.ts` -- does the same
 * REPLACE with the whole clipspace set instead of one image; there is no
 * append branch either way.) This whole block is gated on `node.imgs`
 * being truthy -- an EMPTY array (`[]`, what `setNodeImagesFromRefs` leaves
 * an empty buffer showing) still satisfies that gate in plain JS, which is
 * exactly why the owner's FIRST paste onto a genuinely empty grid "works"
 * (`node.imgs` goes `[] -> [img1]`) and the SECOND one silently clobbers it
 * (`[img1] -> [img2]`) -- reproducing the report precisely. Crucially, none
 * of this touches ANY of our routes -- no upload, no `/eps_image_grid/add`
 * -- so even the first successful-LOOKING paste was never actually
 * durable; only the in-memory preview looked right.
 *
 * Fix: `installClipspacePasteOverride()` below WRAPS (never replaces)
 * `getExtraMenuOptions` -- the same idiom `installCopyImageMenuItem` uses
 * -- calling the original FIRST (so core's own "Paste (Clipspace)" item
 * still gets pushed into `options` exactly as before, and this file's own
 * "Copy image" item from `installCopyImageMenuItem` still gets added too,
 * regardless of install order), then finds that pushed item by its exact
 * label and wraps ITS `callback`: the original callback still runs FIRST
 * (so `node.imgs`/`node.images`/widgets update precisely as core intends --
 * this override is strictly additive, never a replacement of core's own
 * behavior), followed by `addClipspaceToBuffer()`, which re-derives the
 * actual image(s) from `ComfyApp.clipspace` (respecting `img_paste_mode`
 * the same way core's own callback does) and appends each to the durable
 * buffer via the SAME add-route pipeline (`addUploadToBuffer`) the Ctrl+V
 * path already uses -- preferring a cheap ref-reuse (`refFromImageSrc`:
 * parse `{filename, subfolder, type}` straight out of the pasted image's
 * own `/view?...` URL, since it already names a file the server has -- no
 * re-upload needed) and only re-uploading actual pixels when that URL
 * carries no `filename` param. Every successful add refreshes the node
 * from the FULL buffer (`setNodeImagesFromRefs`), so the grid always shows
 * everything ever pasted or collected, never just the latest clipspace
 * paste. A plain Ctrl+V is a completely separate code path (`usePaste.ts`
 * -> `node.pasteFiles`, the M2 section above) and is untouched by any of
 * this.
 *
 * ---- Drag onto the node wins over workflow-load (2026-07-22 owner fix)
 * ----
 *
 * Owner report: dragging an image from the app's left ASSETS PANEL onto the
 * grid node loads the workflow embedded in that image instead of adding it
 * to the grid.
 *
 * Confirmed against frontend source exactly how a canvas-level drop is
 * routed, and that a node-level hook fully pre-empts the workflow-load
 * fallback (`scripts/app.ts`'s `addDropHandler` -- NOT litegraph's own
 * built-in `onDropFile`; core's own comment there calls that one "buggy" --
 * fires once per file with the SAME file on a multi-file drop -- and
 * deliberately bypasses it):
 *   - a `dragover` listener on the canvas element hit-tests the node under
 *     the cursor (`this.canvas.graph?.getNodeOnPos(event.canvasX,
 *     event.canvasY)`) and calls `node.onDragOver(event)`; a truthy return
 *     remembers it as `this.dragOverNode` for the drop that follows.
 *   - the GLOBAL `document` `'drop'` listener calls
 *     `dragOverNode.onDragDrop(event)` FIRST and returns immediately if
 *     that resolves truthy: `if (await n?.onDragDrop?.(event)) return` --
 *     entirely skipping everything below it (`extractFilesFromDragEvent` +
 *     `this.handleFile(file, 'file_drop', ...)`, which is what loads a
 *     dropped image's embedded workflow). A drop this node claims therefore
 *     CANNOT also be interpreted as a workflow load -- winning is
 *     structural (an early `return`), not a race that could be lost.
 *
 * `installDragAndDrop()` below installs `node.onDragOver`/`node.onDragDrop`
 * mirroring `composables/node/useNodeDragAndDrop.ts` exactly (no stable
 * import path for a plain extension script -- the same constraint the M2
 * paste section above already documents; this is the SAME composable
 * `composables/node/useNodeImageUpload.ts` wires up for core's own upload-
 * capable nodes, e.g. LoadImage, so this node now accepts drops the same
 * way those do).
 *
 * What the assets panel's drag ACTUALLY carries -- verified, not assumed:
 * `platform/assets/components/MediaAssetCard.vue`'s `dragStart()` sets TWO
 * `dataTransfer` entries, never a real `File`:
 *   1. `dataTransfer.items.add(JSON.stringify({filename, subfolder, type}),
 *      MIME_ASSET_INFO)` (`MIME_ASSET_INFO = 'application/x-comfy-asset-
 *      info'`, `platform/assets/schemas/mediaAssetSchema.ts`) -- a
 *      `ResultItem`-shaped (`schemas/apiSchema.ts`'s `zResultItem`)
 *      reference to a file the server ALREADY has, when the asset's output
 *      metadata resolves one.
 *   2. `dataTransfer.items.add(url.toString(), 'text/uri-list')` --
 *      UNCONDITIONALLY (whenever the asset's `preview_url` parses), the
 *      asset's own preview URL.
 * Since a `DataTransferItem` added via `.items.add(string, type)` has
 * `kind: 'string'`, never `'file'`, `dataTransfer.files` is empty for this
 * drag -- but (2) means `isDraggingFiles` (installed as `onDragOver`,
 * mirroring `useNodeDragAndDrop.ts`'s own check for
 * `types.includes('text/uri-list')`) still returns `true`, so
 * `dragOverNode` DOES get set, and `onDragDrop` below finds (1) via
 * `getData(MIME_ASSET_INFO)` and adds it directly through the existing
 * add-route pipeline (`addResultItemToBuffer` -> `addUploadToBuffer`, no
 * upload step needed -- it already names a server-side file) with a full-
 * buffer refresh afterward. This is confirmed WORKING end to end from
 * source, not a suspected gap that needed a fallback -- see the final
 * report for the complete citation chain. Real OS files (Finder/Explorer)
 * carry genuine `File` objects and are checked FIRST, reusing
 * `addFilesToBuffer` (the SAME function the Ctrl+V/paste-file path already
 * uses) unchanged. A bare `text/uri-list` with no usable asset-info (some
 * other drag source) falls back to fetching that URL and adding it as a
 * file, same-origin only.
 *
 * ---- Clear button ----
 *
 * A plain `addWidget('button', ...)` (not a DOM widget — no layout beyond a
 * normal widget row is needed). `widget.serialize = false` is REQUIRED, not
 * cosmetic: `EPSImageGrid` is a real (non-virtual) backend node whose OTHER
 * widgets (`mode`, `grid_uuid`) restore from `widgets_values` positionally,
 * and this pack already found (`lora_library/controller.js`'s header
 * comment, "Two distinct ... serialize flags") that `widget.serialize`
 * (the top-level instance flag `LGraphNode.ts` checks when writing
 * `widgets_values`) — NOT `options.serialize` — is what actually keeps a
 * button out of a saved workflow's widget-value array; leaving it default
 * would insert an extra slot and risk desyncing that positional restore.
 *
 * ---- M1/M2 bulk add (docs/ROADMAP-image-grid-bulk-add.md, owner ask:
 * "give the image grid an upload button ... multiselect") ----
 *
 * M1 adds "Add images…" right after Clear — a detached, lazily-created
 * `<input type="file" multiple>` (`ensureFileInput`), sorted before ingest
 * with a numeric-aware `Intl.Collator` (`sortFilesForIngest`, exported and
 * pure) so `img2` lands before `img10` regardless of the unspecified
 * `FileList`/OS-dialog order — applied at BOTH this picker and
 * `installDragAndDrop`'s Finder-files branch, which was unsorted before
 * this. No `canvasOnly` (renders nowhere under Nodes 2.0/Vue); the button
 * callback captures `node` in its closure rather than trusting its own
 * `(value, options, node, pos, event)` arguments, which are absent/
 * clobbered there too.
 *
 * M2 rewrites `addFilesToBuffer`'s loop (shared, via `runAddBatch`, with
 * `addClipspaceToBuffer`'s 'all' mode) to make a 20–100-image batch
 * pleasant instead of ~5,050 full-resolution image loads:
 *  - the display refresh moves OUT of the per-item loop to every 10th item
 *    and once at the end, reusing the last `/add` response's whole-buffer
 *    `images` rather than a fresh `/list` round trip;
 *  - `imageUrlForRef` drops the old per-render `rand=Math.random()`
 *    cache-buster (defeated the browser cache on every repaint) and gains
 *    `preview`/`epoch` options instead. `preview: true` requests a
 *    compressed `webp;80` thumbnail (core's own `/view?preview=` resize
 *    hint) for DISPLAY only; every pixel-consuming path (Copy image,
 *    clipspace add) derives a full-resolution url from a buffer REF
 *    directly rather than trusting a possibly-compressed `<img>.src` (see
 *    `fullResImageUrl`/`currentMenuSelection` — `addClipspaceImageToBuffer`
 *    was audited too and found already safe: its ref-reuse fast path never
 *    touches pixels at all). `epoch` replaces `rand=` as the cache key —
 *    checked against `image_grid_store.py` before removing `rand=`
 *    outright: `clear()` is a plain `rmtree` and the next append restarts
 *    numbering from `0001.png`, so a FULLY stable url would show a stale
 *    cached frame after Clear → re-add reuses a name. `epoch` is the
 *    server's own `generation` (manifest mtime, `buffer_generation`),
 *    threaded through `noteBufferGeneration`/`getBufferGeneration`
 *    (a per-node `WeakMap`) from every response that carries one
 *    (`/list`, `/add` today) and reset on Clear — stable across calls
 *    describing the same buffer contents, fresh the moment a Clear could
 *    have replaced what a filename points to;
 *  - the "Add images…" button doubles as the progress/Cancel control while
 *    a batch runs (`.label` mutated to `Cancel (n/total)`, restored in a
 *    `finally`) — cooperative cancel is a `state.cancelled` flag checked
 *    every iteration PLUS a real `AbortController` (confirmed against the
 *    installed frontend's `ComfyApi.prototype.fetchApi` source that it
 *    spreads its whole options bag straight into `fetch()`, so a `signal`
 *    passed through `api.fetchApi` reaches the browser unmodified);
 *  - one aggregate added/skipped/failed toast per batch
 *    (`notifyBatchResult`) instead of a `console.warn` per file; a
 *    same-response-length compared against the previous one is how a
 *    silent HTTP-200 soft-skip (an unreadable file, `append_uploaded_image`)
 *    gets counted instead of silently miscounted as "added";
 *  - `grid_uuid` is captured ONCE per batch (`currentUuid(node)`, before
 *    the loop) and threaded through every item, so a debounced collision
 *    remint mid-batch (the roadmap's risk #1) can't send the tail of a
 *    long batch into a different buffer than the head.
 *
 * M3 ("Add folder…", right after "Add images…") reuses every piece of the
 * above rather than a parallel implementation: a second detached
 * `<input webkitdirectory multiple>` (`ensureFolderInput`) yields every
 * descendant file with a real `webkitRelativePath`, which
 * `sortFilesForIngest` now sorts on when present (falling back to `.name`
 * otherwise -- provably a no-op change for M1's own callers, since
 * `webkitRelativePath` is falsy for anything not picked via
 * `webkitdirectory`) so `shots/2/img2.png` orders before
 * `shots/10/img1.png`. A directory input's `accept` is ignored by every
 * browser in practice, so filtering is `sortFilesForIngest`'s `isImageFile`
 * gate alone. Over `FOLDER_WARN_THRESHOLD` (200, exported) filtered images,
 * a synchronous `window.confirm` asks before starting at all (roadmap risk
 * #7: no buffer cap + Emit fan-out means one downstream run per buffered
 * image) -- the only dialog primitive available here. The result feeds the
 * SAME `addFilesToBuffer` → `runAddBatch` path M1/M2 already built;
 * `runAddBatch` gained a *buttonLabel* option (default "Add images…") so
 * the invoking button, not always the same one, shows progress/Cancel, and
 * the busy-guard (`getNodeFileState`'s single `state.batch` slot) already
 * covers both buttons for free -- a click on EITHER while a batch is
 * running cancels the one in-flight batch, never starts a second.
 *
 * M5, un-deferred (owner ask 2026-07-29: "right click and delete individual
 * images or tiles ... a duplicate image ... makes the grid useless and you
 * have to start fresh" -- the revisit condition this pack's own roadmap
 * named, "bulk add lands and the buffer becomes hard to curate", just got
 * met by the owner's own words). Deliberately narrow: one tile, right-
 * click, no drag-reorder, no multi-select. `installDeleteImageMenuItem`
 * chains onto the SAME `getExtraMenuOptions` surface
 * `installCopyImageMenuItem` established (same `currentMenuSelection`
 * resolution, so Delete appears exactly where Copy does), pushing right
 * after it. No confirm dialog -- verified against `image_grid_store.py`
 * first: every buffer frame is either a fresh tensor-encode or a PIL
 * RE-ENCODED COPY of the uploaded/referenced source, written to a
 * DIFFERENT path under the buffer dir; the source itself is never touched.
 * `POST /eps_image_grid/remove {uuid, filename}` returns the whole
 * remaining buffer (`{images, generation}`, same shape as `/add`), so
 * deleting reuses the exact same `noteBufferGeneration` +
 * `setNodeImagesFromRefs` refresh every other mutation here already goes
 * through -- including, for free, `setNodeImagesFromRefs`'s existing
 * "any content change resets `imageIndex` to `null`" behavior, which
 * already returns the view to the grid if the deleted tile was the
 * focused one (no new index math needed). Refuses while a bulk batch is
 * running (the SAME `state.batch` slot `runAddBatch`'s own busy-guard
 * checks) -- a delete's manifest rewrite racing a batch's own unlocked
 * read-modify-write is the identical atomicity hazard the busy-guard
 * already exists to prevent between two adds.
 */

import { api } from '../../../scripts/api.js'
import { app, ComfyApp } from '../../../scripts/app.js'

const CLASS_ID = 'EPSImageGrid'
const PREFIX = '[eps_image:image_grid]'
const NODE_TITLE = 'EPS Image Grid' // FORMAT.md §6.6 display name -- toast summaries only.
const GRID_UUID_WIDGET_NAME = 'grid_uuid'
const CLEAR_BUTTON_LABEL = 'Clear'
const UUID_PROPERTY_NAME = 'uuid'

//: Mirrors the backend's own validation (`image_grid_store.py`
//: `_GRID_UUID_RE`) rather than re-deriving a second, possibly-drifting
//: regex -- deliberately a little looser than canonical UUID4.
const GRID_UUID_RE = /^[0-9a-fA-F-]{8,64}$/

/** Nodes we've already wired, guarding against a double `nodeCreated`. */
const attachedNodes = new WeakSet()

// ---------------------------------------------------------------------------
// Node / widget lookups (same idiom as switcher.js / resolution.js)
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

function getGridUuidWidget(node) {
  return findWidget(node, GRID_UUID_WIDGET_NAME) || null
}

/**
 * Hides the `grid_uuid` widget's on-canvas row (kept as the serialized
 * value only) -- FORMAT.md §7.2's `.hidden = true` trick, called once at
 * attach.
 */
function hideGridUuidWidget(node) {
  const widget = getGridUuidWidget(node)
  if (!widget) {
    console.warn(
      PREFIX,
      'EPSImageGrid node is missing its `grid_uuid` widget; identity will not persist'
    )
    return
  }
  // BOTH flags, deliberately (2026-07-29, owner's "uptick in issues using my
  // mac" report): litegraph's canvas renderer hides on `widget.hidden`, but
  // the Vue-nodes renderer ("New node design") decides visibility from
  // `widget.options.hidden` (useProcessedWidgets.ts: `options.hidden ?? false`,
  // verified in this rig's frontend source maps) and IGNORES `widget.hidden`
  // -- so with only the canvas flag, this internal widget leaked into the Vue
  // node as a raw editable text field. Canvas mode ignores `options.hidden`
  // right back, so setting both is safe everywhere.
  widget.hidden = true
  widget.options = { ...(widget.options || {}), hidden: true }
}

// ---------------------------------------------------------------------------
// Identity + dedup (FORMAT.md §6.6) -- see file header for the hook-ordering
// citations.
// ---------------------------------------------------------------------------

function generateUuid() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  // Fallback for a context without `crypto.randomUUID` (very old browser,
  // or a non-HTTPS/non-localhost origin) -- FORMAT.md §6.6 names
  // `crypto.randomUUID()` specifically, but a same-shape fallback keeps the
  // node usable rather than throwing outright if it's ever unavailable.
  console.warn(PREFIX, 'crypto.randomUUID unavailable; using a fallback generator')
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0
    const v = c === 'x' ? r : (r & 0x3) | 0x8
    return v.toString(16)
  })
}

/** The node's current uuid, property taking precedence over the widget
 * (they're kept in lockstep by `writeUuid`; property is checked first
 * purely because it's cheaper to read and just as authoritative). */
function currentUuid(node) {
  return node.properties?.[UUID_PROPERTY_NAME] || getGridUuidWidget(node)?.value || ''
}

/**
 * Writes *uuid* into BOTH `node.properties.uuid` and the `grid_uuid`
 * widget's real `.value` (FORMAT.md §6.6) -- confirmed live that a
 * widget's `.callback` alone does NOT set `.value` (it's a notification
 * hook litegraph's own input handling calls AFTER assigning `.value`
 * itself, never a substitute for the assignment) -- and then also fires
 * the widget's `.callback`, if any, so anything else that reacts to a
 * widget-value change (ComfyUI's own dirty-canvas bookkeeping, a future
 * feature) still sees it.
 */
function writeUuid(node, uuid) {
  node.properties = node.properties || {}
  node.properties[UUID_PROPERTY_NAME] = uuid
  const widget = getGridUuidWidget(node)
  if (!widget) return
  // `.value =` is what actually persists/serializes (confirmed live: a
  // widget's `.callback` is a NOTIFICATION hook, not a setter -- litegraph's
  // own text-input handling always assigns `.value` itself and calls
  // `.callback` afterward as a side effect, never the other way around).
  // Set the value directly, THEN best-effort notify any callback so other
  // code reacting to a value change still sees this one.
  widget.value = uuid
  if (typeof widget.callback === 'function') {
    widget.callback(uuid, app.canvas, node)
  }
}

/** Every OTHER live `EPSImageGrid` node's current uuid. `app.graph._nodes`/
 * `.nodes` is this pack's own established idiom for walking the live graph
 * (`lora_library/controller.js`, `lora_library/sets.js`). */
function siblingUuids(node) {
  const nodes = app.graph?._nodes || app.graph?.nodes || []
  const uuids = new Set()
  for (const other of nodes) {
    if (!other || other === node) continue
    if (nodeClassOf(other) !== CLASS_ID) continue
    const uuid = currentUuid(other)
    if (uuid) uuids.add(uuid)
  }
  return uuids
}

/**
 * Whether a whole-graph rebuild (initial load, workflow open, undo/redo, or
 * a workflow-tab switch -- all route through `app.loadGraphData()` ->
 * `LGraph.prototype.configure()` on this frontend) is CURRENTLY in progress
 * -- FORMAT.md §6.6 "Identity is STABLE" (2026-07-21). `app.configuringGraph`
 * (`scripts/app.ts`) is the exact flag core wraps that method with; confirmed
 * LIVE against this rig's installed frontend that it reads `true` at both
 * `nodeCreated` time and per-node `configure()` time during such a rebuild,
 * and `false` at the equivalent points for a genuine interactive paste/clone
 * (see the file header's dated section for the full writeup + how each call
 * site below uses this). Coerced with `Boolean(...)` so a frontend build
 * missing this internal, non-public property reads `false` rather than
 * throwing -- see the header's "0.28.1 risk" note.
 */
function isGraphConfiguring() {
  return Boolean(app.configuringGraph)
}

/**
 * Mint a fresh uuid on first create (empty/invalid); mint + clone on
 * collision with a live sibling ONLY when *allowCollisionMint* is true;
 * otherwise leave a valid uuid -- colliding or not -- completely untouched
 * (re-syncing property<->widget only if they've somehow drifted apart).
 * Idempotent and cheap to call repeatedly -- both call sites below
 * (deferred `nodeCreated`, and `loadedGraphNode`) may run for the same node.
 *
 * *allowCollisionMint* is the 2026-07-21 fix (FORMAT.md §6.6 "Identity is
 * STABLE"): a whole-graph rebuild (load/undo/redo/tab-switch) can make an
 * untouched node's OWN saved uuid look like it "collides" against a stale
 * sibling instance left over from an overlapping rebuild generation -- see
 * the file header's dated section for the live-reproduced mechanism. That
 * is never a genuine duplicate, so callers pass `false` for it whenever this
 * check is running as part of such a rebuild (`isGraphConfiguring()`, or --
 * for `loadedGraphNode`, which only ever fires from inside one -- always).
 * Only a real interactive duplicate, detected OUTSIDE any rebuild, passes
 * `true` and actually mints + clones.
 *
 * `async` since the 2026-07-20 "images didn't travel to a copy" fix: the
 * COLLISION+mint branch (a genuine live-sibling duplicate -- NOT first-
 * create, see FORMAT.md §6.6) captures the OLD uuid before overwriting,
 * mints the fresh one, then clones the old buffer into the new uuid
 * (`POST /eps_image_grid/clone`) before scheduling a refresh, so the
 * duplicate carries its own independent copy of the original's images
 * instead of starting empty. Callers that don't care about completion can
 * ignore the returned promise; both call sites below await it specifically
 * so their OWN follow-up refresh fetches the settled (possibly just-cloned)
 * uuid rather than racing it.
 */
async function ensureUniqueUuid(node, { allowCollisionMint }) {
  if (nodeClassOf(node) !== CLASS_ID) return

  const widget = getGridUuidWidget(node)
  const propertyUuid = node.properties?.[UUID_PROPERTY_NAME] || ''
  const widgetUuid = widget?.value || ''
  const current = propertyUuid || widgetUuid
  const valid = GRID_UUID_RE.test(current)
  const colliding = valid && siblingUuids(node).has(current)

  if (valid) {
    if (colliding && !allowCollisionMint) {
      // Never remint/clone during a load/undo/configure pass -- the saved
      // uuid is authoritative here, and an apparent collision against a
      // transient sibling from an overlapping rebuild is not a real
      // duplicate. Leave the node -- and its buffer pointer -- untouched.
      return
    }
    if (!colliding) {
      if (propertyUuid !== widgetUuid) {
        writeUuid(node, current) // re-sync, e.g. a hand-edited workflow set only one of the two
      }
      return
    }
    // valid && colliding && allowCollisionMint -- a genuine interactive
    // duplicate; fall through to mint + clone below.
  }

  const oldUuid = current
  const fresh = generateUuid()
  writeUuid(node, fresh)

  if (!valid) {
    // First create (empty/invalid uuid, e.g. a brand-new node, or a
    // hand-edited/corrupted workflow value) -- fresh uuid, no clone: there
    // is no source buffer to copy from, load pass or not.
    return
  }

  console.log(
    PREFIX,
    `node #${node.id}: grid_uuid collided with a live sibling -- minted a new one ` +
      '(each EPSImageGrid keeps its own buffer)'
  )
  try {
    await postClone(oldUuid, fresh)
  } catch (error) {
    console.warn(
      PREFIX,
      'clone-on-collision failed; the duplicate starts with an empty buffer',
      error
    )
  }
  await scheduleRefresh(node)
}

// ---------------------------------------------------------------------------
// Clear button
// ---------------------------------------------------------------------------

async function postClear(grid_uuid) {
  const response = await api.fetchApi('/eps_image_grid/clear', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ uuid: grid_uuid })
  })
  let data = null
  try {
    data = await response.json()
  } catch {
    // Non-JSON body (proxy error page etc.) -- fall through to status check.
  }
  if (!response.ok) {
    const message = data && data.error ? data.error : `HTTP ${response.status}`
    throw new Error(message)
  }
  return data
}

/** Drops the node's own displayed thumbnails immediately, ahead of the next
 * Run's `ui.images` (which would otherwise be the only thing that refreshes
 * the canvas) -- Clear should feel instant. `imageIndex = null` keeps a
 * stale index from pointing past the now-empty array (FORMAT.md §6.6). */
function clearNodePreview(node) {
  node.imgs = []
  node.images = undefined
  node.imageIndex = null
  // M2 cache-token reset (see `imageUrlForRef`'s docstring): Clear can
  // reuse `NNNN.png` names on the next append, so a stale cached epoch
  // must not survive it -- back to the "unknown" default (`0`) until the
  // next response that carries a real `generation`.
  bufferGenerationByNode.delete(node)
  try {
    node.setSizeForImage?.()
  } catch {
    // Best-effort resize; a missing/erroring hook must not block the clear.
  }
  app.graph?.setDirtyCanvas(true, true)
}

async function onClearClicked(node) {
  const uuid = currentUuid(node)
  if (!GRID_UUID_RE.test(uuid)) {
    console.warn(PREFIX, 'Clear clicked with no valid grid_uuid yet; nothing to clear')
    return
  }
  try {
    await postClear(uuid)
  } catch (error) {
    console.warn(PREFIX, 'clear request failed', error)
    return
  }
  clearNodePreview(node)
}

function addClearButton(node) {
  if (findWidget(node, CLEAR_BUTTON_LABEL)) return
  const widget = node.addWidget('button', CLEAR_BUTTON_LABEL, null, () => onClearClicked(node), {})
  // See file header "Clear button" -- the top-level flag, not options.serialize.
  widget.serialize = false
}

// ---------------------------------------------------------------------------
// M1/M3: "Add images..." + "Add folder..." buttons (roadmap-eps-image-
// grid.md M1/M3) -- two detached, lazily-created pickers (multiselect and
// `webkitdirectory`) sharing one busy-guard/cancel handler
// (`handleAddButtonClicked`). The heavy lifting (upload+add, batching,
// progress/cancel) lives in the M2 section below (`addFilesToBuffer`/
// `runAddBatch`); this section only owns the two buttons and the picker
// `<input>`s that feed them.
// ---------------------------------------------------------------------------

const ADD_IMAGES_BUTTON_LABEL = 'Add images…'
const ACCEPTED_IMAGE_TYPES = 'image/png,image/jpeg,image/webp'

//: node -> { input: HTMLInputElement|null, folderInput: HTMLInputElement|
//: null, batch: object|null } -- the lazily-created file-picker `<input>`
//: (M1) and its M3 `webkitdirectory` sibling, plus the in-flight batch's
//: cancel/progress bookkeeping (M2, `runAddBatch`) -- ONE batch slot for
//: BOTH inputs (roadmap M3: "one batch per node total, not per button"),
//: keyed by node so `onRemoved` can tear all three down in one place
//: without hunting the widget list.
const nodeFileState = new WeakMap()

function getNodeFileState(node) {
  let state = nodeFileState.get(node)
  if (!state) {
    state = { input: null, folderInput: null, batch: null }
    nodeFileState.set(node, state)
  }
  return state
}

/** Same image-shaped test `addFilesToBuffer` has always filtered with,
 * factored out so `sortFilesForIngest` below filters identically. */
function isImageFile(file) {
  return Boolean(file) && typeof file.type === 'string' && file.type.startsWith('image/')
}

/**
 * *file*'s sort key for `sortFilesForIngest` below: `webkitRelativePath`
 * when present -- a directory pick (M3, e.g. `shots/2/img2.png`), so files
 * sort by their FULL path, folder segments included -- else plain `.name`
 * (a regular multi-select or a Finder/Explorer drop). Safe to apply
 * unconditionally: `webkitRelativePath` is the empty string (falsy) on
 * every File not selected via a `webkitdirectory` input, on every browser
 * that defines the property at all, and simply `undefined` (also falsy)
 * on a plain test fixture or a browser that doesn't -- either way this
 * degrades to exactly `.name`, so M1's existing picker/drop callers are
 * unaffected in every case, not merely the common one.
 */
function ingestSortKey(file) {
  return file?.webkitRelativePath || file?.name || ''
}

/**
 * Numeric-aware, case-insensitive ingest order -- roadmap-eps-image-grid.md
 * M1 owner decision ("Ordering: Filename, numeric-aware, img2 before
 * img10"), extended for M3's folder picker to sort by full relative PATH
 * when one is available (`ingestSortKey` above), so `shots/2/img2.png`
 * sorts before `shots/10/img1.png` (the path segments themselves compare
 * numerically, same as a bare filename would) and files within one folder
 * still sort numerically among themselves. `FileList` order is NOT the
 * order files appear in the OS picker/Finder/folder dialog -- unspecified
 * by spec, and on Windows the last-CLICKED file (not the first) can land
 * first in the list -- so every bulk-ingest entry point (the file picker
 * below, `installDragAndDrop`'s Finder-files branch, and the M3 folder
 * picker) sorts through this before handing files to `addFilesToBuffer`,
 * which itself preserves whatever order it's given.
 *
 * Pure: *files* (an `Array`, or anything `Array.from`-able like a real
 * `FileList`) in, a NEW array out, never mutated. Filters to image-shaped
 * entries the SAME way `addFilesToBuffer` does (`isImageFile` above) --
 * non-images are dropped here rather than merely sorted-then-re-filtered
 * downstream, so `addFilesToBuffer`'s own defensive filter is always a
 * no-op for a caller that already sorted. This is ALSO the folder picker's
 * only image filter -- a directory input's `accept` attribute is ignored
 * by every browser in practice, so `ensureFolderInput` sets none and
 * relies entirely on this. Missing `.name`/`.webkitRelativePath` are
 * tolerated, never thrown: a nameless file sorts as if its key were `''`
 * (first); a typeless one is simply filtered out by `isImageFile`.
 * `Intl.Collator`'s `numeric: true` makes "img10" sort after "img2" (not
 * lexicographically, where "10" < "2"); `sensitivity: 'base'` makes the
 * order case-insensitive. `Array.prototype.sort` is spec-stable (ES2019+,
 * and this pack only ever runs on modern Chromium/Node), so two files
 * whose collated keys tie keep their original relative order.
 */
export function sortFilesForIngest(files) {
  const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })
  return Array.from(files || [])
    .filter(isImageFile)
    .sort((a, b) => collator.compare(ingestSortKey(a), ingestSortKey(b)))
}

/**
 * Lazily creates *node*'s detached file-picker `<input>` (M1). Deliberately
 * never appended to the DOM -- a detached `<input type="file">`'s
 * `.click()` still opens the native OS picker in every evergreen browser,
 * so there is no layout to manage and nothing to `.remove()` on cleanup
 * beyond dropping the reference (`installFileInputCleanup` below).
 * `multiple` + an image-only `accept` narrows the OS dialog (still just a
 * hint -- the real gate is `isImageFile`, applied by `sortFilesForIngest`
 * and, defensively, by `addFilesToBuffer` itself). `value = ''` in
 * `onchange` is what lets picking the EXACT SAME file(s) again re-fire
 * `change` -- a browser only fires it when the input's value actually
 * differs from its last-committed one otherwise (roadmap M1).
 */
function ensureFileInput(node, state) {
  if (state.input) return state.input
  const input = document.createElement('input')
  input.type = 'file'
  input.multiple = true
  input.accept = ACCEPTED_IMAGE_TYPES
  input.style.display = 'none'
  input.onchange = () => {
    const files = Array.from(input.files || [])
    input.value = ''
    if (files.length) void addFilesToBuffer(node, sortFilesForIngest(files))
  }
  state.input = input
  return input
}

/**
 * Shared click handler for BOTH the "Add images..." and "Add folder..."
 * buttons -- Nodes 2.0 (verified in the roadmap's own research): NO
 * `canvasOnly` on either widget (a `canvasOnly` widget renders NOWHERE
 * under Vue nodes), and a button callback's own `(value, options, node,
 * pos, event)` arguments are absent/clobbered there too -- *node* is
 * captured in `addAddImagesButton`/`addAddFolderButton`'s closures below
 * instead of read from an argument, and nothing here reads any of them
 * either.
 *
 * A click WHILE a batch is already running for this node CANCELS it
 * instead of starting another -- roadmap M3: "one batch per node total,
 * not per button", so EITHER button cancels the one in-flight batch
 * regardless of which button started it (there is exactly one
 * `state.batch` slot per node, `getNodeFileState`). Otherwise runs
 * *openPicker* to start this button's own kind of pick. The button IS the
 * Cancel control while its label reads `Cancel (n/total)` (M2/M3, see
 * `runAddBatch`'s docstring for the full progress/cancel design) -- only
 * whichever button actually started the running batch shows that label
 * (`runAddBatch`'s *buttonLabel* option), but a click on the OTHER button
 * still cancels it rather than silently queuing or no-op'ing, since there
 * is only ever one batch to act on.
 */
function handleAddButtonClicked(node, openPicker) {
  const state = getNodeFileState(node)
  if (state.batch && !state.batch.done) {
    state.batch.cancelled = true
    try {
      state.batch.controller?.abort()
    } catch {
      // Best-effort -- the cooperative `cancelled` flag alone still stops
      // the loop at its next iteration even if abort() itself throws.
    }
    return
  }
  // Diagnosis instrumentation (owner report 2026-07-29: the Add buttons "don't
  // do anything" from his Mac against the Linux server, while the whole
  // pipeline -- callback, picker, upload, /eps_image_grid/add -- verified
  // clean on the rig in BOTH render modes, and the backend verified clean for
  // a genuine non-loopback caller). The one client step that can silently
  // no-op is `input.click()`: a browser refuses to open a file picker without
  // transient USER ACTIVATION, and it refuses in total silence (Chrome logs
  // one console line; Safari says nothing). If some dispatch path delivers
  // this callback outside the activation window, that is indistinguishable
  // from a dead button -- so measure it at the exact moment it matters and
  // SAY it, instead of leaving the next report as silent as this one.
  // `navigator.userActivation` is Chrome 105+/Safari 16.4+/Firefox 120+; on
  // anything older `active` stays null and nothing is claimed either way.
  let activation = null
  try {
    if (navigator.userActivation) activation = navigator.userActivation.isActive === true
  } catch {
    // Leave null -- diagnostics must never break the pick itself.
  }
  if (activation === false) {
    const message =
      'The browser refused the file picker: this click reached EPS Image Grid ' +
      'outside its "user activation" window, so input.click() is ignored. ' +
      'Please report this toast -- it pinpoints the bug.'
    console.warn(PREFIX, message, {
      isSecureContext: typeof window !== 'undefined' ? window.isSecureContext : null,
      userAgent: typeof navigator !== 'undefined' ? navigator.userAgent : null
    })
    try {
      app.extensionManager?.toast?.add?.({
        severity: 'warn',
        summary: 'Could not open the file picker',
        detail: message,
        life: 12000
      })
    } catch {
      // console.warn above already carries the message.
    }
    // Still try -- a null/failed probe must never block a working browser,
    // and even a refused click() costs nothing.
  }
  openPicker(state)
}

function onAddImagesClicked(node) {
  handleAddButtonClicked(node, (state) => ensureFileInput(node, state).click())
}

function onAddFolderClicked(node) {
  handleAddButtonClicked(node, (state) => ensureFolderInput(node, state).click())
}

function addAddImagesButton(node) {
  if (findWidget(node, ADD_IMAGES_BUTTON_LABEL)) return
  // Copies `addClearButton` above exactly, including `widget.serialize`
  // set on the INSTANCE (see its own citation just above -- the options-bag
  // form sets a different flag on this fork).
  const widget = node.addWidget(
    'button',
    ADD_IMAGES_BUTTON_LABEL,
    null,
    () => onAddImagesClicked(node),
    {}
  )
  widget.serialize = false
}

//: M3 ("Add folder..." -- roadmap risk #7, "Emit fan-out means 100
//: buffered images = 100 downstream runs. M3's folder picker makes
//: reaching that trivial -- the warn matters"). Exported so a caller
//: (or a test) can see the exact number this asks about before starting.
export const FOLDER_WARN_THRESHOLD = 200

const ADD_FOLDER_BUTTON_LABEL = 'Add folder…'

/**
 * Lazily creates *node*'s detached DIRECTORY-picker `<input>` (M3) --
 * `webkitdirectory` (the same non-standard-but-universally-supported
 * attribute VHS's own folder loader uses, roadmap market check) makes the
 * native dialog a folder chooser; picking one yields EVERY descendant file
 * (subfolders included) with a real `webkitRelativePath` on each
 * (`shots/2/img2.png`-shaped) -- `sortFilesForIngest` above already knows
 * to sort on that when present. No `accept` set here -- unlike the plain
 * file picker (`ensureFileInput`), a directory input's `accept` is IGNORED
 * by every browser in practice, so filtering is left entirely to
 * `sortFilesForIngest`'s own `isImageFile` gate rather than a hint that
 * would do nothing. `value = ''` in `onchange`, same reason as
 * `ensureFileInput`: lets re-picking the SAME folder re-fire `change`.
 */
function ensureFolderInput(node, state) {
  if (state.folderInput) return state.folderInput
  const input = document.createElement('input')
  input.type = 'file'
  input.webkitdirectory = true
  input.multiple = true
  input.style.display = 'none'
  input.onchange = () => {
    const files = Array.from(input.files || [])
    input.value = ''
    void startFolderBatch(node, files)
  }
  state.folderInput = input
  return input
}

/**
 * Filters+sorts *files* (every descendant a directory pick yields --
 * `sortFilesForIngest`, which is also the only image filter here, see
 * `ensureFolderInput`'s docstring) then guards the roadmap's documented
 * foot-gun before actually starting: FORMAT.md §6.6 says the buffer has
 * NO cap by design, and in Emit mode each buffered image becomes its own
 * downstream run -- so a folder with hundreds of images can silently
 * queue hundreds of runs. A filtered count over `FOLDER_WARN_THRESHOLD`
 * asks first, via a SYNCHRONOUS `window.confirm` -- the only dialog
 * primitive available here (this file has no dialog helper, and a toast
 * can't ask a question); confirm is plain DOM API, so it is unaffected by
 * Nodes 2.0/Vue. Proceeds UNGATED under the threshold, and unconditionally
 * once confirmed. A folder with nothing image-shaped after filtering is a
 * silent no-op, matching `addFilesToBuffer`'s own contract.
 */
async function startFolderBatch(node, files) {
  const sorted = sortFilesForIngest(files)
  if (!sorted.length) return
  if (sorted.length > FOLDER_WARN_THRESHOLD) {
    const proceed = window.confirm(
      `This folder has ${sorted.length} images. EPS Image Grid keeps every buffered ` +
        `image (no cap), and in Emit mode each one becomes its own downstream run -- ` +
        `adding all ${sorted.length} now means ${sorted.length} runs later.\n\n` +
        'Add them all anyway?'
    )
    if (!proceed) return
  }
  void addFilesToBuffer(node, sorted, { buttonLabel: ADD_FOLDER_BUTTON_LABEL })
}

function addAddFolderButton(node) {
  if (findWidget(node, ADD_FOLDER_BUTTON_LABEL)) return
  // Same widget conventions as `addAddImagesButton`/`addClearButton`.
  const widget = node.addWidget(
    'button',
    ADD_FOLDER_BUTTON_LABEL,
    null,
    () => onAddFolderClicked(node),
    {}
  )
  widget.serialize = false
}

/**
 * Tears down this node's lazily-created file/folder `<input>`s (drops the
 * references AND their `onchange` closures over *node*, so nothing keeps
 * the node reachable after removal) and cancels any in-flight batch.
 * Installed once per node instance (`__epsGridFileCleanupInstalled`),
 * mirroring `installConfigureRefresh`'s guard idiom; wraps (never
 * replaces) `onRemoved`, calling the original first.
 */
function installFileInputCleanup(node) {
  if (node.__epsGridFileCleanupInstalled) return
  node.__epsGridFileCleanupInstalled = true

  const originalOnRemoved = node.onRemoved
  node.onRemoved = function (...args) {
    try {
      const state = nodeFileState.get(node)
      if (state) {
        if (state.input) {
          state.input.onchange = null
          state.input = null
        }
        if (state.folderInput) {
          state.folderInput.onchange = null
          state.folderInput = null
        }
        if (state.batch) {
          state.batch.cancelled = true
          state.batch.controller?.abort()
        }
      }
    } catch (error) {
      console.warn(PREFIX, 'file-input cleanup failed', error)
    }
    return originalOnRemoved?.apply(this, args)
  }
}

// ---------------------------------------------------------------------------
// M2: paste-to-add (Ctrl+V and the free "Paste Image" menu item both land
// on `node.pasteFiles`, installed below -- see file header for the hook
// citations).
// ---------------------------------------------------------------------------

const UPLOAD_ROUTE = '/upload/image'
const ADD_ROUTE = '/eps_image_grid/add'

/**
 * node -> the last server-reported `generation` (a cache token for the
 * buffer's CONTENTS -- `image_grid_store.py`'s `buffer_generation`,
 * manifest mtime in ms) seen for that node. M2 (roadmap "100 files ~=
 * 5,050 loads"): defaults to `0` (unknown yet, e.g. before the first
 * `/list`/`/add` response) via `getBufferGeneration` below rather than
 * `undefined`, so `imageUrlForRef`'s `v=` param is always present and the
 * URL shape never depends on whether a generation has been observed.
 */
const bufferGenerationByNode = new WeakMap()

function getBufferGeneration(node) {
  return bufferGenerationByNode.get(node) || 0
}

/**
 * Records *data*'s `generation` field for *node*, if present and numeric.
 * Called after every response that carries one (`/list`, `/add` today --
 * `/clear`/`/clone` may gain it later; this tolerates either). Absent/
 * non-numeric `generation` (an older backend, or a response shape that
 * never carries one) leaves the LAST-KNOWN value untouched -- never resets
 * to `0` -- so display URLs stay keyed off the most recent truth this node
 * has actually seen, not a fresh guess on every call.
 */
function noteBufferGeneration(node, data) {
  if (data && typeof data.generation === 'number') {
    bufferGenerationByNode.set(node, data.generation)
  }
}

/**
 * A core-style `/view` URL for one buffer ref (`{filename, subfolder,
 * type}`) -- same identity query-param shape already observed for every
 * OTHER thumbnail this node shows (core's own `ui.images` handling), plus
 * two independent, both-optional decorations appended AFTER those identity
 * params (in this fixed order) so `refFromImageSrc` can keep parsing just
 * `filename`/`subfolder`/`type` back out of either a plain or a decorated
 * URL:
 *
 *  - `preview: true` appends core's own `Comfy.PreviewFormat` resize hint
 *    (`preview=webp;80`, confirmed against this rig's `server.py` `/view`
 *    handler: `preview_info = query['preview'].split(';')` ->
 *    format/quality, format constrained to webp/jpeg) -- a COMPRESSED
 *    thumbnail, DISPLAY-only (`setNodeImagesFromRefs`). Never used for a
 *    pixel-consuming path (Copy image, clipspace add) -- those derive a
 *    full-res URL from a REF directly (`preview` omitted) instead of
 *    trusting an on-screen `<img>`'s `.src` -- see `fullResImageUrl`.
 *  - `epoch` becomes a `v=<epoch>` cache-token param, REPLACING the old
 *    per-render `rand=Math.random()` (M2, roadmap "100 files ~= 5,050
 *    full-resolution loads" -- `rand=` defeated the browser cache on
 *    EVERY repaint, not just every append). Buffer frames are append-only
 *    while a buffer lives (`image_grid_store.py`'s `_next_frame_filename`:
 *    "never reuses a name" -- true only WITHIN one generation), but
 *    `clear()` is a plain `rmtree` and the next append restarts numbering
 *    from `0001.png` -- verified in `image_grid_store.py` before removing
 *    `rand=` outright, since a fully stable URL would then show a STALE
 *    cached frame after Clear -> re-add reuses a name. `epoch` is the
 *    server's own `generation` (`image_grid_store.py`'s
 *    `buffer_generation`, manifest mtime): identical -- therefore
 *    cacheable -- for any two calls describing the SAME buffer contents;
 *    different the moment a Clear could have replaced what a given
 *    filename points to. Defaults to `0`; see `getBufferGeneration`.
 *
 * Exported for tests.
 */
export function imageUrlForRef(ref, { preview = false, epoch = 0 } = {}) {
  const params = new URLSearchParams({
    filename: ref.filename,
    subfolder: ref.subfolder || '',
    type: ref.type || 'output'
  })
  if (preview) params.set('preview', 'webp;80')
  params.set('v', String(epoch))
  return api.apiURL(`/view?${params.toString()}`)
}

/**
 * Replaces the node's displayed thumbnails with *refs* (the whole buffer,
 * same shape the backend's `ui.images` always sends) and shows the GRID
 * (`imageIndex = null`, core's own "no single image focused" sentinel --
 * FORMAT.md §6.6 "Identity is STABLE; refresh is CHEAP", 2026-07-21).
 * Previously this focused the LAST image (`imgs.length - 1`), which was the
 * owner-reported "paste shows one image, no way back to the grid until you
 * switch tabs" regression -- a paste-add should surface the new thumbnail
 * IN the grid, not replace the grid view. Shared with the Clear path's
 * empty case, and with `refreshFromBuffer` below (the 2026-07-20 display-
 * on-load/undo fix) -- every caller hands it the SAME ref shape, so a
 * load-triggered refresh renders identically to a just-added image.
 *
 * Repaint-on-load (Mac-only fix 2026-07-21, see file header for the full
 * writeup): setting `.src` starts an async fetch+decode and returns
 * immediately -- every caller here repaints ONCE, right after this
 * function returns, long before any of these images can possibly have
 * finished loading over a slow/remote connection. Litegraph's own render
 * loop redraws every animation frame regardless (`LGraphCanvas.ts`'s
 * `startRendering`/`draw()`), but only actually REPAINTS a layer when its
 * `dirty_canvas`/`dirty_bgcanvas` flag is set -- confirmed in source, not
 * assumed -- so an image that finishes loading AFTER that one repaint
 * never appears until something ELSE marks the canvas dirty (switching
 * tabs, moving the node, undoing again...). On localhost this race is
 * invisible (images load fast enough to already be ready at that one
 * repaint); over the Mac's LAN link to the PC they routinely aren't, which
 * is the "undo clears the grid" report. Each image's own `onload` now
 * repaints individually -- the same idiom litegraph's own
 * `LGraphNode.prototype.loadImage()` helper already uses for exactly this
 * problem (`this.setDirtyCanvas(true)` from the image's own `load`
 * listener) -- so whichever image is slowest to arrive is also the one
 * that gets the canvas its final, correct paint, independent of how the
 * rest finished. Correct and cheap for ANY client, including a fast one
 * (an extra repaint of an already-correct canvas is a no-op visually);
 * `onerror` only logs -- a broken thumbnail must not repeatedly retrigger
 * anything.
 */
/**
 * Deduplicating merge of buffer refs: *existing* first (buffer order),
 * then any *incoming* not already present. Key is the full identity core
 * itself uses to build a /view URL (`type|subfolder|filename`).
 *
 * Returns `{refs, added}` -- and, deliberately, `refs` IS `existing` (same
 * array identity) when nothing was added, so callers can skip pointless
 * re-renders and, more importantly, keep the `node.images ===
 * store.images` identity equality intact (see `syncCoreOutputStore`).
 * Pure; exported for tests/test_image_grid_js.py.
 */
/**
 * The bare filename to transmit for *file* — never a path.
 *
 * A `webkitdirectory` pick names every File after its position in the tree
 * (`shots/2/IMG_1865.PNG` via `webkitRelativePath`, and `file.name` itself
 * carries the folder on the browsers Eric hit), which core's upload route
 * cannot write (see `uploadImageFile`). Strips BOTH separators — a Windows
 * client talking to a POSIX server (and the reverse) is this pack's normal
 * case, so trusting one separator would fix only half the users. Falls back
 * to a generic name rather than an empty one, since core 400s a blank
 * filename outright.
 *
 * Pure; exported for tests.
 */
export function basenameForUpload(file) {
  const raw = String(file?.name ?? '')
  const cut = Math.max(raw.lastIndexOf('/'), raw.lastIndexOf('\\'))
  const base = (cut >= 0 ? raw.slice(cut + 1) : raw).trim()
  return base || 'image.png'
}

/** One ref's identity, the same triple core builds a /view URL from. */
function refKey(ref) {
  return `${ref?.type || 'output'}|${ref?.subfolder || ''}|${ref?.filename || ''}`
}

export function mergeBufferRefs(existing, incoming) {
  const base = Array.isArray(existing) ? existing : []
  const extra = Array.isArray(incoming) ? incoming : []
  const keyOf = refKey
  const seen = new Set(base.map(keyOf))
  const additions = []
  for (const ref of extra) {
    if (!ref || !ref.filename) continue
    const key = keyOf(ref)
    if (seen.has(key)) continue
    seen.add(key)
    additions.push(ref)
  }
  if (!additions.length) return { refs: base, added: 0 }
  return { refs: base.concat(additions), added: additions.length }
}

/**
 * Makes core's own output store agree with the buffer -- THE root fix for
 * the owner's longest-lingering grid bug ("a new image becomes the focus
 * and there is no way back to the full grid", reported across several
 * rounds; root-caused and reproduced live 2026-07-27).
 *
 * Mechanism, verified in this rig's frontend 1.45.21 source, then
 * reproduced live in one call: `litegraphService.ts` wires EVERY node's
 * `onDrawBackground` to `updatePreviews`, which re-renders whenever
 * `node.images !== store.images` -- an ARRAY-IDENTITY comparison against
 * `app.nodeOutputs[locator].images` -- and re-renders FROM THE STORE
 * (`useNodeImage.ts` `showPreview()`: `node.imageIndex = null; node.imgs =
 * elements`). Core's `executed` handler REPLACES that store entry with
 * just the refs a Run reported (`ui.images` = only the newly-appended
 * ones, by design, for output-panel cleanliness). So after every append,
 * the store says "this node's images = [the new one]", our refresh says
 * otherwise on `node` itself -- and the very next repaint takes the
 * store's side. Every earlier fix here rewrote `node.imgs`/`node.images`
 * and lost by construction: repair-then-clobber, once per repaint,
 * forever. (The pane-based checks that "verified" those fixes never ran
 * real draws -- the browser pane's degenerate viewport -- which is exactly
 * how it kept shipping.)
 *
 * The fix writes the store THROUGH the same assignment shape the store's
 * own `setOutputsByLocatorId` uses (`app.nodeOutputs[locator] = outputs`),
 * with the SAME array instance that goes on `node.images` -- after which
 * `updatePreviews`' identity check is EQUAL, core stops re-rendering, and
 * any independent `showPreview()` that does run renders the full buffer
 * anyway. Both sides of the identity comparison are ours now.
 *
 * Locator: for a root-graph node core keys the store by `String(node.id)`
 * (`nodeToNodeLocatorId`); inside a SUBGRAPH the locator is
 * subgraph-scoped (`<uuid>:<id>`), and writing the plain id would hit some
 * unrelated root node's entry -- so a non-root grid keeps the old
 * imgs-only behavior (safe, just un-hardened) rather than guessing keys.
 * Exported for tests.
 */
export function syncCoreOutputStore(node, refs) {
  try {
    if (!node || !app?.nodeOutputs) return
    if (!node.graph || !app.graph || node.graph !== app.graph) return
    const locator = String(node.id)
    if (refs && refs.length) {
      app.nodeOutputs[locator] = { images: refs }
    } else if (app.nodeOutputs[locator]) {
      // Empty buffer: remove the entry outright (mirrors the store's own
      // removal path) so a repaint can't resurrect stale refs.
      delete app.nodeOutputs[locator]
    }
  } catch (error) {
    console.warn(PREFIX, 'syncCoreOutputStore failed', error)
  }
}

export function setNodeImagesFromRefs(node, refs) {
  if (!refs || !refs.length) {
    node.imgs = []
    node.images = undefined
    node.imageIndex = null
    syncCoreOutputStore(node, [])
    return
  }
  // Unchanged-content short-circuit (2026-07-27, same round as the
  // focus-clobber root fix): the forced post-run reconcile calls this on
  // EVERY finished execution, including runs that appended nothing -- and
  // unconditionally rebuilding meant resetting `imageIndex` (yanking a
  // user out of an image they'd enlarged, once per queued run) and
  // re-creating every <img> for no reason. When the list is
  // content-identical to what's already shown, only re-assert the store
  // identity (`node.images`' own array -- NOT the fresh `refs` -- so
  // `updatePreviews`' identity check stays equal) and leave the view,
  // enlarged or grid, exactly where the user put it. Only taken when
  // `node.imgs` is a matching real array -- a fresh/restored node (imgs
  // not yet built) must still fall through and build them.
  if (
    Array.isArray(node.images) &&
    Array.isArray(node.imgs) &&
    node.images.length === refs.length &&
    node.imgs.length === refs.length &&
    refs.every((ref, i) => refKey(ref) === refKey(node.images[i]))
  ) {
    syncCoreOutputStore(node, node.images)
    return
  }
  // M2: a compressed thumbnail (`preview=webp;80`), never the full-res
  // fetch `imageUrlForRef`'s default gives -- pixel-consuming paths (Copy
  // image, clipspace add) derive their own full-res URL from a REF
  // instead of trusting this `.src` (see `fullResImageUrl`).
  const epoch = getBufferGeneration(node)
  const imgs = refs.map((ref) => {
    const img = new Image()
    // Listeners attached BEFORE `.src` (belt-and-suspenders -- image loads
    // are always async in every real browser, so ordering can't actually
    // matter, but this matches the safer convention).
    img.onload = () => node.setDirtyCanvas(true, true)
    img.onerror = () => console.warn(PREFIX, `image failed to load: ${ref.filename}`)
    img.src = imageUrlForRef(ref, { preview: true, epoch })
    return img
  })
  node.imgs = imgs
  // SAME array instance on both sides of updatePreviews' identity check --
  // load-bearing, see syncCoreOutputStore's docstring. Never clone here.
  node.images = refs
  node.imageIndex = null
  syncCoreOutputStore(node, refs)
}

/**
 * `POST /upload/image` (core's own route) -- returns `{name, subfolder,
 * type}` on success, throws otherwise. Reimplemented directly (see file
 * header) rather than importing `useNodeImageUpload`'s `uploadFile`.
 *
 * *signal* (M2, optional): an `AbortController.signal` a batch (
 * `runAddBatch`) passes through so a Cancel click aborts an in-flight
 * upload rather than waiting for it to finish first. Confirmed against the
 * installed frontend's `ComfyApi.prototype.fetchApi` source that it spreads
 * its whole options bag straight into the real `fetch()` call (`fetch(this
 * .apiURL(route), {cache: 'no-cache', ...options, headers})`), so a
 * `{signal}` here reaches the browser unmodified -- not a guess.
 * `undefined` (every non-batch caller) is simply "no signal", identical to
 * omitting the option entirely.
 */
async function uploadImageFile(file, signal) {
  const formData = new FormData()
  // ALWAYS send an explicit BASENAME, never `file.name` implicitly (owner
  // bug 2026-07-29, "Adding a folder fails", reproduced on the rig): a
  // `webkitdirectory` pick gives every File a name carrying its folder
  // (`Eric/IMG_1865.PNG`), and `FormData.append(name, file)` sends that
  // whole string as the multipart filename. Core's `/upload/image` then
  // does `filepath = join(upload_dir, normpath(subfolder), filename)` while
  // only ever `makedirs`-ing the SUBFOLDER part (`server.py` ~400-420), so
  // the directory implied by the FILENAME never exists and the write dies
  // with `FileNotFoundError: ...\Input\Eric\IMG_1865.PNG` -- a 500, his
  // exact traceback. `append`'s third argument overrides the transmitted
  // filename, so one basename fixes every folder pick. Same-basename
  // collisions across different subfolders are fine: core auto-suffixes
  // `name (1).ext` (and short-circuits byte-identical re-uploads).
  formData.append('image', file, basenameForUpload(file))
  const response = await api.fetchApi(UPLOAD_ROUTE, { method: 'POST', body: formData, signal })
  if (!response.ok) {
    throw new Error(`upload failed (HTTP ${response.status})`)
  }
  return response.json()
}

/**
 * `POST /eps_image_grid/add` -- appends the just-uploaded file (its
 * `{name,subfolder,type}` from `uploadImageFile`) to *node*'s buffer.
 * Returns `{ok, uuid, images, generation}` on success, throws otherwise.
 *
 * *uuidOverride* (M2, optional -- roadmap risk #1 "uuid remint race"): a
 * batch (`runAddBatch` via `addFilesToBuffer`/`addClipspaceToBuffer`)
 * captures `currentUuid(node)` ONCE, before its loop starts, and passes it
 * here for every item so a debounced collision-sweep remint mid-batch
 * can't send the tail of a long batch into a DIFFERENT buffer than the
 * head. Falsy (every non-batch caller, e.g. a single Ctrl+V or asset-drop
 * add) falls back to reading `currentUuid(node)` fresh, exactly the old
 * behavior. *signal*: see `uploadImageFile`'s docstring.
 */
async function addUploadToBuffer(node, uploaded, uuidOverride, signal) {
  const uuid = uuidOverride || currentUuid(node)
  if (!GRID_UUID_RE.test(uuid)) {
    throw new Error('node has no valid grid_uuid yet')
  }
  const response = await api.fetchApi(ADD_ROUTE, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      uuid,
      filename: uploaded.name,
      subfolder: uploaded.subfolder || '',
      type: uploaded.type || 'input'
    }),
    signal
  })
  let data = null
  try {
    data = await response.json()
  } catch {
    // Non-JSON body -- fall through to the status check below.
  }
  if (!response.ok) {
    const message = data && data.error ? data.error : `HTTP ${response.status}`
    throw new Error(message)
  }
  return data
}

//: M2 (roadmap "make 20-100 images pleasant"): how often a batch refreshes
//: the displayed grid mid-flight -- every Kth item, plus always once at the
//: end. K=10 per the brief; tune here if 20-100-image batches ever want a
//: different cadence.
const BATCH_REFRESH_INTERVAL = 10

/**
 * Best-effort aggregate toast for ONE finished batch -- replaces the old
 * per-file `console.warn` (still there, but no longer the only signal) with
 * a single added/skipped/failed summary. Reuses `notifyClipboard`'s toast
 * idiom (same `app.extensionManager?.toast?.add?.()` call, same shape) but
 * NOT its per-node cooldown -- a batch summary is inherently one
 * deliberate event per batch, never spam.
 */
function notifyBatchResult(node, { noun, added, skipped, failed, cancelled }) {
  const total = added + skipped + failed
  if (!total) return
  const parts = [`added ${added}`]
  if (skipped) parts.push(`skipped ${skipped}`)
  if (failed) parts.push(`failed ${failed}`)
  const detail = `${cancelled ? 'Cancelled -- ' : ''}${parts.join(', ')} of ${total} ${noun}.`
  try {
    if (app.extensionManager?.toast?.add) {
      app.extensionManager.toast.add({
        severity: failed ? 'warn' : 'info',
        summary: node.title || NODE_TITLE,
        detail,
        life: 6000
      })
      return
    }
  } catch (error) {
    console.warn(PREFIX, 'batch toast failed', error)
  }
  console.info(PREFIX, detail)
}

/**
 * Runs *addOne(item, signal)* sequentially over *items* (never concurrent
 * -- our own atomic manifest writes are not safe against truly concurrent
 * appends from the same process, the same reasoning this file has always
 * used for a single-file batch). Shared by `addFilesToBuffer`'s file loop
 * AND `addClipspaceToBuffer`'s 'all' mode (M2 #8, "apply the same
 * batching/refresh discipline to both") so both get the identical M2
 * package instead of two drifting copies:
 *
 *  - the display refresh is HOISTED out of the per-item loop -- every
 *    `BATCH_REFRESH_INTERVAL`th item and unconditionally once more at the
 *    end, reusing the LAST successful response's whole-buffer `images`
 *    (never a fresh `/list` round trip mid-batch: the bug this whole
 *    rewrite exists for -- 100 files used to mean ~5,050 image loads,
 *    roadmap M2). A redundant final call when the loop already refreshed
 *    on the exact last item is cheap on purpose: `setNodeImagesFromRefs`'s
 *    own unchanged-content short-circuit makes a repeat of identical refs
 *    a no-op rebuild.
 *  - the INVOKING button (M3: *buttonLabel*, which of `ADD_IMAGES_BUTTON_
 *    LABEL`/`ADD_FOLDER_BUTTON_LABEL` started this particular batch --
 *    defaults to the "Add images..." button so `addFilesToBuffer`'s other
 *    callers, paste and drop, are unchanged; found by NAME, which never
 *    changes even while `.label` -- the PAINTED text -- does) mutates
 *    `.label` to `Cancel (n/total)` for the duration, restored to whatever
 *    it showed before in a `finally` regardless of how the batch ends.
 *    This reconciles the brief's two framings of the same mechanism --
 *    "show progress" and "the button becomes Cancel" -- as ONE label that
 *    does both: it names the count AND is the click target that aborts.
 *    The OTHER button stays at its idle label throughout (only the one
 *    that actually started the batch shows progress), but a click on
 *    EITHER still cancels -- see `handleAddButtonClicked`'s docstring.
 *  - cooperative cancel: *batchState.cancelled* is checked every
 *    iteration, AND an `AbortController` (its `.signal` handed to
 *    *addOne*) aborts whatever request is actually in flight -- so a
 *    cancel during a slow upload doesn't have to wait for that upload to
 *    finish first. An aborted request's rejection is NOT counted as a
 *    failure.
 *  - ONE aggregate toast at the end (`notifyBatchResult`), never one per
 *    item.
 *  - silent-skip detection: the backend fails SOFT (HTTP 200, buffer
 *    unchanged) on an unreadable file (`append_uploaded_image`'s
 *    docstring) -- *previousLength* starts from *node*'s OWN currently-
 *    displayed buffer length (cheap, synchronous, no extra round trip
 *    needed just to seed a baseline) and is compared against each
 *    response's `images.length`: unchanged == skipped, grew == added.
 *
 * Refuses to start a SECOND concurrent batch for the same node (returns
 * immediately, logging a warning) -- the button-click cancel path only
 * guards the button's own second click; a drop or paste arriving while a
 * picker-driven batch is still running must not run two loops appending to
 * the same manifest at once.
 *
 * *addOne* must resolve to `{images, generation?}` (the `/eps_image_grid/
 * add` response shape `addUploadToBuffer` already returns) or throw/reject
 * -- a per-item failure is caught here, counted, and does not stop the
 * rest of the batch (the long-standing "fails soft per item" contract).
 *
 * Returns `{added, skipped, failed, cancelled}`. Never throws.
 */
async function runAddBatch(
  node,
  items,
  addOne,
  { noun = 'images', buttonLabel = ADD_IMAGES_BUTTON_LABEL } = {}
) {
  const total = items.length
  if (!total) return { added: 0, skipped: 0, failed: 0, cancelled: false }

  const state = getNodeFileState(node)
  if (state.batch && !state.batch.done) {
    console.warn(
      PREFIX,
      'a batch is already in progress for this node; ignoring the new add request until it finishes'
    )
    return { added: 0, skipped: 0, failed: 0, cancelled: false }
  }

  const controller = typeof AbortController === 'function' ? new AbortController() : null
  const batchState = { cancelled: false, controller, done: false }
  state.batch = batchState

  // M3: targets whichever button actually started this batch (defaults to
  // "Add images..." -- see the docstring above) -- NOT always
  // ADD_IMAGES_BUTTON_LABEL, so the folder picker's own button shows its
  // own progress instead of silently updating the other one.
  const buttonWidget = findWidget(node, buttonLabel)
  const idleLabel = (buttonWidget && buttonWidget.label) || buttonLabel

  let added = 0
  let skipped = 0
  let failed = 0
  let lastImages = null
  // Seed 0 when nothing is displayed yet: an EMPTY buffer displays
  // `images: undefined` (setNodeImagesFromRefs' empty branch), and 0 is its
  // true length -- so a silent skip on the very FIRST file of a fresh
  // buffer still counts as skipped, not added. (A restored-but-unfetched
  // non-empty buffer also lands here; its first add then can't be
  // distinguished from a skip either way, and counting it as added is the
  // better default -- identical behavior to the null-seed this replaces.)
  let previousLength = Array.isArray(node.images) ? node.images.length : 0

  try {
    for (let i = 0; i < total; i++) {
      if (batchState.cancelled) break

      if (buttonWidget) {
        buttonWidget.label = `Cancel (${i + 1}/${total})`
        node.setDirtyCanvas(true, true)
      }

      try {
        const result = await addOne(items[i], controller?.signal)
        noteBufferGeneration(node, result)
        const images = Array.isArray(result?.images) ? result.images : null
        if (images) {
          lastImages = images
          if (previousLength !== null && images.length === previousLength) {
            skipped++
          } else {
            added++
          }
          previousLength = images.length
        }
      } catch (error) {
        if (batchState.cancelled) break // an abort() surfaces as a rejection here -- not a real failure.
        failed++
        console.warn(PREFIX, 'batch add failed for one item', error)
      }

      if (lastImages && (i + 1) % BATCH_REFRESH_INTERVAL === 0) {
        setNodeImagesFromRefs(node, lastImages)
        node.setDirtyCanvas(true, true)
      }
    }
  } finally {
    if (lastImages) {
      setNodeImagesFromRefs(node, lastImages)
      node.setDirtyCanvas(true, true)
    }
    if (buttonWidget) {
      buttonWidget.label = idleLabel
      node.setDirtyCanvas(true, true)
    }
    batchState.done = true
    if (state.batch === batchState) state.batch = null
    notifyBatchResult(node, { noun, added, skipped, failed, cancelled: batchState.cancelled })
  }

  return { added, skipped, failed, cancelled: batchState.cancelled }
}

/**
 * Uploads and appends every image `File` in *files* to *node*'s buffer, in
 * the given order (sort happens at the CALL SITES that need it --
 * `ensureFileInput`'s picker and `installDragAndDrop`'s Finder-files
 * branch, via `sortFilesForIngest` -- not here, so a Ctrl+V/paste-file
 * batch, which is usually one or two images anyway, is unaffected). M2
 * rewrite (roadmap-eps-image-grid.md M2): the display refresh used to run
 * INSIDE this loop, once per file, rebuilding an `Image()` for every ref in
 * the WHOLE buffer on every single iteration (100 files ~= 5,050 loads) --
 * now hoisted into `runAddBatch`'s shared K-and-final cadence, which also
 * gives this path progress/cancel/aggregate-toast for free.
 *
 * The batch's `grid_uuid` is captured ONCE, before the loop starts
 * (roadmap risk #1, "uuid remint race") -- see `addUploadToBuffer`'s
 * docstring for why.
 *
 * A *files* list with nothing image-shaped is a silent no-op (`false`,
 * unchanged contract). Never throws -- per-file failures are caught and
 * counted by `runAddBatch`.
 *
 * *buttonLabel* (M3, optional): which button's `.label` `runAddBatch`
 * shows progress/Cancel on -- defaults to "Add images..." (unchanged for
 * this function's other callers, paste/drop/the image picker); the M3
 * folder picker (`startFolderBatch`) passes `ADD_FOLDER_BUTTON_LABEL` so
 * ITS OWN button reflects progress instead.
 */
export async function addFilesToBuffer(node, files, { buttonLabel = ADD_IMAGES_BUTTON_LABEL } = {}) {
  const imageFiles = Array.from(files || []).filter(isImageFile)
  if (!imageFiles.length) return false

  const uuid = currentUuid(node) // captured ONCE for the whole batch -- see docstring above.
  await runAddBatch(
    node,
    imageFiles,
    async (file, signal) => {
      const uploaded = await uploadImageFile(file, signal)
      return addUploadToBuffer(node, uploaded, uuid, signal)
    },
    { buttonLabel }
  )
  return true
}

/**
 * Installs `node.pasteFiles` (FORMAT.md §6.6). This is the exact hook
 * core's own paste pipeline calls -- both a real Ctrl+V while this node is
 * selected (via `usePaste.ts`, gated on `isImageNode(node)`; see
 * `previewMediaType` in `attach()` for why that's true even on an empty
 * buffer) and the free "Paste Image" menu item (`canPasteImage`, gated on
 * `typeof node.pasteFiles === 'function'`) land here. Never throws --
 * `addFilesToBuffer` already fails soft per file.
 */
function installPasteFiles(node) {
  node.pasteFiles = (files) => {
    void addFilesToBuffer(node, files)
    return true
  }
}

// ---------------------------------------------------------------------------
// Drag-and-drop onto the node (2026-07-22 owner fix: dropping an image from
// the assets panel loaded its embedded workflow instead of adding it to the
// grid) -- see file header "Drag onto the node wins over workflow-load" for
// the full writeup + citations.
// ---------------------------------------------------------------------------

//: Custom MIME type the assets panel's own drag-start sets
//: (`platform/assets/schemas/mediaAssetSchema.ts`'s `MIME_ASSET_INFO`,
//: confirmed set by `platform/assets/components/MediaAssetCard.vue`'s
//: `dragStart()` -- file header) -- a JSON-encoded `{filename, subfolder,
//: type}` (core's `ResultItem` shape) pointing at a file the server
//: ALREADY has (an output or input asset), not raw bytes.
const MIME_ASSET_INFO = 'application/x-comfy-asset-info'

/**
 * Whether *e* (a `dragover`/`dragenter` `DragEvent`) looks like it's
 * carrying something this node can accept -- mirrors
 * `composables/node/useNodeDragAndDrop.ts`'s own `isDraggingFiles` exactly
 * (no stable import path for a plain extension script, file header): a
 * real file item, OR a `text/uri-list` entry (which the assets panel
 * ALWAYS also sets alongside its own `MIME_ASSET_INFO` -- confirmed
 * against `MediaAssetCard.vue`'s `dragStart()`, file header). Installed
 * directly as `node.onDragOver`; `scripts/app.ts`'s own canvas-level
 * `dragover` listener calls this to decide whether to remember this node
 * as the active drop target for the `drop` event that follows.
 */
function isDraggingFiles(e) {
  if (!e?.dataTransfer?.items) return false
  const hasFileItem = Array.from(e.dataTransfer.items).some((item) => item.kind === 'file')
  return hasFileItem || Boolean(e.dataTransfer.types?.includes('text/uri-list'))
}

/**
 * Best-effort `{filename, subfolder, type}` parse of *raw* (the JSON
 * string `dataTransfer.getData(MIME_ASSET_INFO)` returns) -- `null` for
 * anything that isn't valid JSON or lacks a usable `filename`, so a drag
 * from anywhere else that happens to reuse this MIME type is never
 * mistaken for an asset reference.
 */
function parseAssetInfo(raw) {
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw)
    if (parsed && typeof parsed.filename === 'string' && parsed.filename) return parsed
  } catch {
    // not JSON -- fall through to null
  }
  return null
}

/**
 * Adds a `{filename, subfolder, type}` asset reference (the assets-panel
 * drop payload -- already a file the server has) directly to *node*'s
 * buffer, no upload needed -- the same ref-reuse idiom
 * `addClipspaceImageToBuffer` below uses.
 */
async function addResultItemToBuffer(node, item) {
  const result = await addUploadToBuffer(node, {
    name: item.filename,
    subfolder: item.subfolder || '',
    type: item.type || 'input'
  })
  noteBufferGeneration(node, result)
  if (result && Array.isArray(result.images)) {
    setNodeImagesFromRefs(node, result.images)
  }
  app.graph?.setDirtyCanvas(true, true)
}

/**
 * Fallback for a `text/uri-list` drop with no usable `MIME_ASSET_INFO`
 * (some other same-origin drag source entirely): fetches the first URI in
 * the list and, if it's actually an image, uploads+adds it exactly like
 * any other dropped file. `false` for anything that doesn't resolve to a
 * same-origin, fetchable image -- callers treat that as "not ours to
 * handle" (falls through to core's own workflow-load handling).
 */
async function addUriListToBuffer(node, dataTransfer) {
  const raw = dataTransfer?.getData('text/uri-list') ?? ''
  const firstLine = raw
    .split('\n')
    .map((line) => line.trim())
    .find((line) => line && !line.startsWith('#'))
  if (!firstLine) return false

  let url
  try {
    url = new URL(firstLine, location.href)
  } catch {
    return false
  }
  if (url.origin !== location.origin) return false

  try {
    const response = await fetch(url)
    if (!response.ok) return false
    const blob = await response.blob()
    if (!blob.type.startsWith('image/')) return false
    const fileName =
      url.searchParams.get('filename') || firstLine.split('/').pop() || 'dropped-image'
    const file = new File([blob], fileName, { type: blob.type })
    return addFilesToBuffer(node, [file])
  } catch (error) {
    console.warn(PREFIX, 'uri-list drop failed', error)
    return false
  }
}

/**
 * Installs `node.onDragOver`/`node.onDragDrop` -- see file header for the
 * full writeup of why these two hooks (and not litegraph's own
 * `onDropFile`, which core's own comment calls "buggy") are what actually
 * gate a canvas-level drop, and exactly why a drop this node claims cannot
 * also fall through to core's workflow-load handling.
 *
 * Checks, in order: real OS files (`dataTransfer.files`, e.g. a drag from
 * Finder/Explorer) via the SAME `addFilesToBuffer` the Ctrl+V/paste-file
 * path already uses; then an assets-panel reference
 * (`MIME_ASSET_INFO` -> `addResultItemToBuffer`, no upload needed); then a
 * bare `text/uri-list` fallback (`addUriListToBuffer`). Returns `false`
 * (never handled) only when NONE of those resolve, letting core's own
 * fallback (workflow-load from a dropped file) run exactly as before for
 * anything genuinely unrelated to this node.
 */
function installDragAndDrop(node) {
  node.onDragOver = isDraggingFiles
  node.onDragDrop = async (e) => {
    const files = Array.from(e?.dataTransfer?.files || [])
    if (files.length) {
      // M1: numeric-aware order before ingest -- FileList order is
      // unspecified by spec, and Windows Explorer can hoist the
      // last-clicked file first; matches the "Add images..." picker path
      // (`ensureFileInput`), which sorts the same way.
      return addFilesToBuffer(node, sortFilesForIngest(files))
    }
    const asset = parseAssetInfo(e?.dataTransfer?.getData(MIME_ASSET_INFO))
    if (asset) {
      await addResultItemToBuffer(node, asset)
      return true
    }
    return addUriListToBuffer(node, e?.dataTransfer)
  }
}

// ---------------------------------------------------------------------------
// Copy image (Mac-only fix 2026-07-21): OS-clipboard image copy needs a
// secure context core doesn't have on Eric's Mac-over-LAN-http setup -- see
// file header for the full writeup.
// ---------------------------------------------------------------------------

/**
 * Whether the OS-clipboard image-copy path -- `navigator.clipboard.write([
 * new ClipboardItem(...)])`, the exact mechanism core's own "Copy Image"
 * menu item uses (`ComfyUI_frontend/src/services/litegraphService.ts`'s
 * `getCopyImageOption`, inside `addNodeContextMenuHandler`) -- is actually
 * usable in THIS browsing context. `Clipboard` and `ClipboardItem` are both
 * `[SecureContext]` in the platform spec: outside a secure context
 * (`https:`, or `localhost`/`127.0.0.1`/`file:`), `navigator.clipboard` and
 * `window.ClipboardItem` don't merely reject -- they don't EXIST
 * (`undefined`). Eric's Mac browses ComfyUI at `http://<pc-ip>:8188` (the
 * Windows PC's LAN address) -- plain http, not localhost -- so
 * `window.isSecureContext` is `false` there, `window.ClipboardItem` is
 * `undefined`, and core's own item disappears with NO replacement
 * (confirmed at the exact line: `if (typeof window.ClipboardItem ===
 * 'undefined') return []`, `litegraphService.ts` line 634). "Copy
 * (Clipspace)" keeps showing because it never touches the Clipboard API at
 * all (`ComfyApp.copyToClipspace`, `scripts/app.ts`, line ~723 of
 * `litegraphService.ts` for where it's added to the menu -- plain
 * `node.imgs`/`node.images` bookkeeping) -- exactly the split the owner
 * reported ("I only see Copy (Clipspace), not Copy Image").
 *
 * This is a BROWSER security boundary, not a ComfyUI or pack bug -- there
 * is no way to put a binary image on the OS clipboard from an insecure
 * origin, full stop. `installCopyImageMenuItem` below exists to degrade
 * GRACEFULLY when this reads false, never to bypass it.
 *
 * Verified against a `ComfyUI_frontend` git checkout at 1.48.3 (this
 * session's scratchpad) -- newer than both the rig's actually-installed
 * `comfyui-frontend-package` 1.45.21 and Eric's real ComfyUI 0.28.1-paired
 * build, so the exact line numbers above are NOT independently confirmed
 * on either of those; the underlying Clipboard-API secure-context gate is
 * a stable web-platform rule with no ComfyUI-version dependency, so it
 * should hold regardless -- re-verify live on 0.28.1 if this citation ever
 * looks wrong.
 */
function canUseOsClipboardImage() {
  return (
    typeof window !== 'undefined' &&
    window.isSecureContext === true &&
    typeof window.ClipboardItem !== 'undefined' &&
    typeof navigator !== 'undefined' &&
    typeof navigator.clipboard?.write === 'function'
  )
}

/**
 * The exact image (and its buffer ref, if one lines up) core's OWN
 * "Open/Copy/Save Image" items would target -- mirrors
 * `litegraphService.ts`'s `getExtraMenuOptions` resolution order precisely
 * (a focused single image via `imageIndex`, else a merely-hovered one via
 * `overIndex` while the grid view is showing) so this node's OWN "Copy
 * image" item appears/targets in exactly the same circumstances core's
 * would. `ref` is `node.images[index]` -- the SAME index into the ref
 * array `setNodeImagesFromRefs` builds `node.imgs` from (both are
 * `refs.map(...)`-built together, same length, same order by
 * construction), so it always names the identical buffered frame `img`
 * shows, full quality regardless of what `img.src` itself happens to be
 * decorated with (M2, "compressed thumbnails without degrading Copy" --
 * see `fullResImageUrl`). `{img: null, ref: null}` when nothing resolves
 * (e.g. the grid view with nothing hovered) -- callers must not offer a
 * copy action with nothing to act on.
 */
function currentMenuSelection(node) {
  if (!node.imgs || !node.imgs.length) return { img: null, ref: null }
  let index = null
  if (node.imageIndex != null) index = node.imageIndex
  else if (node.overIndex != null) index = node.overIndex
  if (index == null) return { img: null, ref: null }
  const img = node.imgs[index] || null
  const ref = Array.isArray(node.images) ? node.images[index] || null : null
  return { img, ref }
}

/**
 * The full-RESOLUTION `/view` url for the (*img*, *ref*) pair
 * `currentMenuSelection` resolves -- what core's own Open/Copy/Save Image
 * items would target. M2 ("compressed thumbnails without degrading Copy"):
 * `node.imgs[i].src` may now be a COMPRESSED preview url
 * (`imageUrlForRef(ref, {preview: true, ...})`, the display path) --
 * reading pixels from it directly would silently degrade Copy image and
 * clipspace paste. Resolution order:
 *  1. *ref* (`node.images[index]`, index-aligned with `node.imgs` by
 *     construction) -- always full-res, since `preview` is omitted here.
 *  2. `refFromImageSrc(img.src)` -- a fallback for the (shouldn't-happen-
 *     by-the-invariant-above, but cheap to guard) case where `node.images`
 *     doesn't line up; re-derives a REF from *img*'s own url's identity
 *     params rather than trusting whatever decoration is on it.
 *  3. *img*'s own `.src`, verbatim -- absolute last resort, for a
 *     non-ComfyUI-native image (e.g. a stray `data:`/`blob:` url) that
 *     carries no `filename` param at all; degraded only in the sense that
 *     there is nothing else this function could possibly return.
 * Always includes the current buffer generation (`getBufferGeneration`) as
 * `v=` -- correctness matters even more here than for the display path,
 * and the epoch gives both (see `imageUrlForRef`'s docstring).
 */
function fullResImageUrl(node, img, ref) {
  if (ref && ref.filename) {
    return imageUrlForRef(ref, { epoch: getBufferGeneration(node) })
  }
  const parsed = refFromImageSrc(img.src)
  if (parsed) {
    return imageUrlForRef(
      { filename: parsed.name, subfolder: parsed.subfolder, type: parsed.type },
      { epoch: getBufferGeneration(node) }
    )
  }
  return img.src
}

//: Per-node cooldown so a burst of clicks on the degraded path doesn't
//: stack several identical toasts.
const lastClipboardToastAt = new WeakMap()
const CLIPBOARD_TOAST_COOLDOWN_MS = 4000

/** Best-effort toast via this pack's established `app.extensionManager?.
 * toast?.add?.(...)` convention (`resolution.js`'s `toast()`,
 * `lora_library/controller.js`'s `_toast()`) -- never throws, falls back
 * to `console.info` if the toast surface is missing on some older/newer
 * frontend build. Debounced per node (*lastClipboardToastAt* above) so
 * this reads as one calm explanation, not per-click spam. */
function notifyClipboard(node, detail) {
  const now = Date.now()
  const last = lastClipboardToastAt.get(node) || 0
  if (now - last < CLIPBOARD_TOAST_COOLDOWN_MS) return
  lastClipboardToastAt.set(node, now)
  try {
    if (app.extensionManager?.toast?.add) {
      app.extensionManager.toast.add({
        severity: 'info',
        summary: node.title || NODE_TITLE,
        detail,
        life: 6000
      })
      return
    }
  } catch (error) {
    console.warn(PREFIX, 'toast failed', error)
  }
  console.info(PREFIX, detail)
}

/**
 * Legacy copy-as-TEXT for an insecure context, where
 * `navigator.clipboard.writeText` is EQUALLY unavailable (see
 * `canUseOsClipboardImage`'s docstring -- the whole `navigator.clipboard`
 * object is `[SecureContext]`, not just `.write`). A hidden, off-screen
 * `<textarea>` + `document.execCommand('copy')` has no secure-context
 * requirement -- it's the pre-Clipboard-API mechanism the modern API
 * replaced, still supported everywhere. Mirrors ComfyUI's OWN fallback for
 * this exact situation almost line for line (`ComfyUI_frontend/src/
 * composables/useCopyToClipboard.ts`'s `legacyCopy()`) -- not a workaround
 * invented for this pack, the same thing core reaches for when its own
 * primary clipboard path fails. Unlike that version, this one also catches
 * a THROWING `execCommand` (rather than letting it propagate) so a caller
 * can still fall through to the new-tab fallback below even if this one
 * fails outright, not just when it returns `false`.
 */
function copyTextViaExecCommand(text) {
  const textarea = document.createElement('textarea')
  textarea.setAttribute('readonly', '')
  textarea.value = text
  textarea.style.position = 'fixed'
  textarea.style.left = '-9999px'
  textarea.style.top = '-9999px'
  document.body.appendChild(textarea)
  textarea.select()
  try {
    return document.execCommand('copy')
  } catch (error) {
    return false
  } finally {
    textarea.remove()
  }
}

/**
 * OS-clipboard image copy -- the "normal path", only ever called after
 * `canUseOsClipboardImage()` has confirmed the APIs exist. Fetches *url*'s
 * own bytes (plain `fetch`, matching core's OWN `getCopyImageOption`
 * exactly -- `/view` is a CORE route, not one of this pack's own, so
 * there's no reason to route it through `api.fetchApi`) and hands them to
 * `navigator.clipboard.write` as a `ClipboardItem`. *url* is always the
 * FULL-RESOLUTION url (`fullResImageUrl`, M2) -- never a thumbnail's own
 * possibly-compressed `.src`. Still wrapped in try/catch by the caller --
 * a secure context guarantees the API's PRESENCE, not that the write will
 * succeed (the user can deny a clipboard permission prompt, etc).
 */
async function copyImageToOsClipboard(url) {
  const response = await fetch(url)
  if (!response.ok) throw new Error(`fetch failed (HTTP ${response.status})`)
  const blob = await response.blob()
  await navigator.clipboard.write([
    new ClipboardItem({ [blob.type || 'image/png']: blob })
  ])
}

/**
 * Insecure-context degradation (the Mac case). Cannot put a binary image
 * on the OS clipboard -- that's the browser's security boundary, not
 * fixable from here (see `canUseOsClipboardImage`'s docstring) -- so
 * instead this does the best available TWO things, independently,
 * best-effort:
 *   1. Copies *url* as TEXT (`copyTextViaExecCommand` above) -- at least
 *      something lands on the OS clipboard.
 *   2. Opens *url* in a new browser tab, where the BROWSER's OWN native
 *      "Copy Image" (no ComfyUI/secure-context dependency at all) is
 *      available on the image itself.
 * *url* is always the FULL-RESOLUTION url (`fullResImageUrl`, M2), same as
 * `copyImageToOsClipboard` above. Deliberately fully SYNCHRONOUS (no
 * `await` before `window.open`) so the call stays inside the same
 * user-gesture the menu click provides -- anything async first risks the
 * browser treating the tab-open as an unrequested popup and blocking it. A
 * toast always explains why this isn't a real OS image-copy -- never
 * pretend to fix what the browser forbids; "Copy (Clipspace)" remains the
 * one path that works identically in every context, because it never
 * touches the OS clipboard at all.
 */
function degradedCopyImage(node, url) {
  const textCopied = copyTextViaExecCommand(url)
  window.open(url, '_blank', 'noopener')
  notifyClipboard(
    node,
    (textCopied
      ? 'Copied the image link (opened it in a new tab too). '
      : 'Opened the image in a new tab. ') +
      'A real OS image-copy needs a secure context (https, or localhost) -- ' +
      'browsers block it over plain http on a LAN address. In the new tab, ' +
      'right-click the image and choose "Copy Image" for a true OS-' +
      'clipboard copy, or use "Copy (Clipspace)" to stay inside ComfyUI.'
  )
}

/**
 * Installs an EPSImageGrid-owned "Copy image" context-menu item, WRAPPING
 * (never replacing) whatever `getExtraMenuOptions` this node instance
 * already has -- core's own generic per-CLASS handler
 * (`litegraphService.ts`'s `addNodeContextMenuHandler`, installed on the
 * node CLASS's prototype during registration, before any instance exists),
 * so calling it here still runs core's own Open/Copy/Save/Bypass/Clipspace
 * items first, unchanged. Assigning `node.getExtraMenuOptions` creates an
 * OWN, instance-level property that shadows the inherited prototype method
 * for THIS node only (confirmed at the call site, `LGraphCanvas.ts`:
 * `node.getExtraMenuOptions?.(this, options)` -- a plain method call,
 * resolved per-instance) -- the identical "wrap, don't replace" idiom
 * `installPasteFiles`/`installConfigureRefresh` already use elsewhere in
 * this file, so no other node type or instance is ever affected.
 *
 * Always adds the item when there's an image to target (`currentMenuSelection`
 * above); the callback branches on `canUseOsClipboardImage()` at CLICK
 * time (not once at menu-build time) since a browser's secure-context
 * status cannot change between one right-click and the next, but checking
 * fresh costs nothing and avoids any risk of a stale cached read. See
 * `copyImageToOsClipboard`/`degradedCopyImage` above for the two branches.
 *
 * Guarded by `node.__epsGridCopyMenuInstalled` (a plain instance flag,
 * mirroring `installConfigureRefresh`'s `__epsGridConfigureWrapped`) so
 * this wrap can only ever be installed once per node instance -- avoids a
 * chain of stacked wrappers (each adding its own duplicate menu item) if
 * anything ever re-triggers node setup for the same live instance; `attach`
 * already guards against that via `attachedNodes`, this is belt-and-
 * suspenders for the one thing that would visibly regress if it happened.
 */
function installCopyImageMenuItem(node) {
  if (node.__epsGridCopyMenuInstalled) return
  node.__epsGridCopyMenuInstalled = true

  const original = node.getExtraMenuOptions
  node.getExtraMenuOptions = function (canvas, options) {
    const result = original ? original.call(this, canvas, options) : undefined
    const { img, ref } = currentMenuSelection(node)
    if (img) {
      options.push({
        content: 'Copy image',
        callback: () => {
          const url = fullResImageUrl(node, img, ref)
          if (canUseOsClipboardImage()) {
            copyImageToOsClipboard(url).catch((error) => {
              console.warn(PREFIX, 'OS clipboard image copy failed', error)
              notifyClipboard(node, `Copy image failed: ${error?.message || error}`)
            })
          } else {
            degradedCopyImage(node, url)
          }
        }
      })
    }
    return result
  }
}

// ---------------------------------------------------------------------------
// Delete image (owner ask, un-defers roadmap M5 -- 2026-07-29: "right click
// and delete individual images or tiles ... a duplicate image ... makes the
// grid useless and you have to start fresh"). Scope is deliberately narrow:
// one tile, right-click, no drag-reorder, no multi-select.
// ---------------------------------------------------------------------------

const REMOVE_ROUTE = '/eps_image_grid/remove'
const DELETE_MENU_LABEL = 'Delete this image'

/**
 * `POST /eps_image_grid/remove {uuid, filename}` -- removes ONE frame from
 * *node*'s buffer by its own on-disk name (`ref.filename`, an `NNNN.png`,
 * unambiguous within a buffer). Returns `{ok, uuid, images, generation}` --
 * the WHOLE remaining buffer, same contract as `/add`/`/list`. Mirrors
 * `postClear`'s request/response handling exactly. Throws on a non-OK
 * response -- confirmed against the actual route (`routes_image_grid.py`):
 * an invalid uuid or a missing/non-string `filename` 400s, matching every
 * sibling route here. An unknown-but-well-formed `filename` is NOT an
 * error -- `remove_frame` soft-fails 200 with the buffer unchanged
 * (`image_grid_store.py`, the same soft-fail convention `append_uploaded_
 * image` already uses for a bad add).
 */
async function postRemove(grid_uuid, filename) {
  const response = await api.fetchApi(REMOVE_ROUTE, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ uuid: grid_uuid, filename })
  })
  let data = null
  try {
    data = await response.json()
  } catch {
    // Non-JSON body (proxy error page etc.) -- fall through to status check.
  }
  if (!response.ok) {
    const message = data && data.error ? data.error : `HTTP ${response.status}`
    throw new Error(message)
  }
  return data
}

/**
 * Deletes ONE buffered frame (*ref*, captured at menu-BUILD time --
 * `installDeleteImageMenuItem` below explains why) and refreshes the
 * display from the response's whole remaining buffer.
 *
 * **Verified against `image_grid_store.py` before shipping this with NO
 * confirm dialog**: every frame under `eps_image_grid/<uuid>/NNNN.png` is
 * either a fresh PNG encoded straight from an in-memory tensor batch
 * (`append_batch`, Collect mode -- there is no "original file" in that
 * path at all) or a PIL-RE-ENCODED COPY of whatever was uploaded/
 * referenced (`append_uploaded_image`: opens the source under `input`/
 * `output`/`temp`, re-encodes it, and writes the RESULT to a DIFFERENT
 * path under the buffer dir -- the source is read-only there and never
 * touched again). `remove_frame`'s own docstring states the same
 * conclusion independently ("Deleting a frame never touches the user's
 * original upload"). So `/remove` can only ever delete the buffer's own
 * disposable copy -- this is why Delete, like Clear, needs no confirm:
 * nothing irreplaceable is ever at risk.
 *
 * No `AbortController`/cancel here -- unlike `runAddBatch`'s multi-item
 * loops, this is a single, fast, non-batch request with nothing long-
 * running to cancel.
 *
 * A genuine failure (network/HTTP error -- NOT the soft-fail "unknown
 * filename" case, which resolves normally with an unchanged buffer)
 * surfaces as ONE toast naming the file; success is silent -- the tile
 * visibly vanishing from the grid IS the feedback, the same convention
 * every other add/refresh path in this file already follows.
 */
async function deleteBufferFrame(node, ref) {
  const uuid = currentUuid(node)
  if (!GRID_UUID_RE.test(uuid)) {
    console.warn(PREFIX, 'delete clicked with no valid grid_uuid yet; nothing to delete')
    return
  }
  try {
    const result = await postRemove(uuid, ref.filename)
    noteBufferGeneration(node, result)
    if (result && Array.isArray(result.images)) {
      setNodeImagesFromRefs(node, result.images)
      node.setDirtyCanvas(true, true)
    }
  } catch (error) {
    console.warn(PREFIX, 'delete failed', error)
    notifyClipboard(node, `Couldn't delete "${ref.filename}": ${error?.message || error}`)
  }
}

/**
 * Installs a "Delete this image" context-menu item -- WRAPS (never
 * replaces) `getExtraMenuOptions` AGAIN, the identical "wrap, don't
 * replace" idiom `installCopyImageMenuItem` just above already uses (and
 * `installClipspacePasteOverride` below chains onto in the same way),
 * called from `attach()` right after `installCopyImageMenuItem` so this
 * item is pushed immediately after "Copy image" -- adjacent, as asked.
 * Reuses the SAME `currentMenuSelection(node)` resolution (a focused
 * `imageIndex`, else a merely-hovered `overIndex` while the grid view
 * shows) that already solves "which tile is this menu about" for Copy, so
 * Delete appears/targets under the same circumstances Copy does -- gated
 * on `ref` specifically (not `img`, Copy's own gate) because Delete, per
 * the owner ask, has NO fallback path the way Copy's `fullResImageUrl`
 * does: it must have a real buffer ref to send to `/remove`, never a
 * `.src` parse, so there is nothing useful to offer without one.
 *
 * *ref* is captured HERE, at menu-BUILD time (when `getExtraMenuOptions`
 * runs, i.e. the moment of the right-click) -- not re-derived inside the
 * click callback -- so the frame the menu item is provably about is the
 * one actually deleted even in the unlikely case something else refreshes
 * the buffer while the (synchronous, litegraph-native) menu is briefly
 * open. Derived via `node.images[index]` -- NEVER by parsing an `<img>`'s
 * `.src` -- the same index-aligned-with-imgs derivation `fullResImageUrl`
 * uses for Copy (see `currentMenuSelection`'s own docstring for why that
 * alignment holds).
 *
 * Refuses while a bulk batch is running for this node (`state.batch &&
 * !state.batch.done` -- the SAME single per-node slot `runAddBatch`'s own
 * busy-guard already checks, `getNodeFileState`) rather than sending the
 * request: a delete's manifest read-modify-write racing a batch's own
 * unlocked read-modify-write (`append_uploaded_image`) is the identical
 * atomicity hazard `runAddBatch`'s busy-guard already exists to prevent
 * between two concurrent adds -- a toast explains rather than silently
 * doing nothing or corrupting the manifest.
 *
 * Guarded by `node.__epsGridDeleteMenuInstalled`, mirroring `installCopy
 * ImageMenuItem`'s own `__epsGridCopyMenuInstalled` guard.
 */
function installDeleteImageMenuItem(node) {
  if (node.__epsGridDeleteMenuInstalled) return
  node.__epsGridDeleteMenuInstalled = true

  const original = node.getExtraMenuOptions
  node.getExtraMenuOptions = function (canvas, options) {
    const result = original ? original.call(this, canvas, options) : undefined
    const { ref } = currentMenuSelection(node)
    if (ref && ref.filename) {
      options.push({
        content: DELETE_MENU_LABEL,
        callback: () => {
          const state = getNodeFileState(node)
          if (state.batch && !state.batch.done) {
            notifyClipboard(
              node,
              'A batch is already adding images -- finish or cancel it first, then delete.'
            )
            return
          }
          void deleteBufferFrame(node, ref)
        }
      })
    }
    return result
  }
}

// ---------------------------------------------------------------------------
// Clipspace paste appends, doesn't replace (2026-07-22 owner fix: a second
// clipspace paste appeared to overwrite the first) -- see file header for
// the full writeup + citations.
// ---------------------------------------------------------------------------

//: Exact label core's own `getExtraMenuOptions` pushes for this item
//: (`services/litegraphService.ts`) -- matched literally rather than by
//: position, since this file's own `installCopyImageMenuItem` above (and
//: potentially other extensions) may have already added items before this
//: one runs.
const CLIPSPACE_PASTE_MENU_LABEL = 'Paste (Clipspace)'

/**
 * Extracts `{name, subfolder, type}` (the shape `addUploadToBuffer` expects
 * -- `uploaded.name`/`.subfolder`/`.type`) straight out of a
 * `/view?filename=...&subfolder=...&type=...` URL -- the SAME query-param
 * shape this file's own `imageUrlForRef` produces, and the one core's own
 * thumbnail/preview URLs already carry. `null` when *src* carries no
 * `filename` param (e.g. a `data:`/`blob:` URL) -- not a ComfyUI-native
 * file reference, so the caller must fall back to re-uploading the actual
 * pixels instead. Exported for tests, and reused by `fullResImageUrl` (M2)
 * as the fallback for deriving a full-res Copy-image url when no `ref`
 * lines up.
 */
export function refFromImageSrc(src) {
  try {
    const url = new URL(src)
    const filename = url.searchParams.get('filename')
    if (!filename) return null
    return {
      name: filename,
      subfolder: url.searchParams.get('subfolder') || '',
      type: url.searchParams.get('type') || 'input'
    }
  } catch {
    return null
  }
}

/**
 * Adds ONE clipspace image element to *node*'s buffer. Tries the cheap
 * ref-reuse path first (`refFromImageSrc` above -- no re-upload needed,
 * straight to `POST /eps_image_grid/add`, since a ComfyUI-native `/view`
 * URL already names a file the server has), and only re-uploads actual
 * pixels (fetch -> Blob -> File -> `uploadImageFile`, mirroring the Ctrl+V
 * path) when *img*'s `.src` carries no `filename` param.
 *
 * M2 audit ("compressed thumbnails without degrading Copy"): this function
 * was flagged alongside the Copy-image path as a pixel-consuming reader of
 * `.src` -- audited, and it's safe as designed, not changed. The ref-reuse
 * branch never touches pixels at all (it appends a REFERENCE to a file the
 * server already has); the fetch-fallback branch only runs when *img.src*
 * carries no `filename` param at all, which never happens for one of this
 * node's OWN preview urls (`imageUrlForRef` always keeps `filename` -- the
 * `preview`/`v` decorations are strictly ADDITIONAL params). That fallback
 * exists for genuinely foreign clipspace content (e.g. `data:`/`blob:`
 * pixels from outside ComfyUI's own `/view`), which was never degraded by
 * this node's preview mechanism in the first place.
 *
 * *uuidOverride* and *signal*: threaded through from `runAddBatch` when
 * this runs as part of a batch (`addClipspaceToBuffer` below) -- see
 * `addUploadToBuffer`'s docstring.
 */
async function addClipspaceImageToBuffer(node, img, uuidOverride, signal) {
  const ref = refFromImageSrc(img.src)
  if (ref) return addUploadToBuffer(node, ref, uuidOverride, signal)

  const response = await fetch(img.src, { signal })
  if (!response.ok) throw new Error(`fetch failed (HTTP ${response.status})`)
  const blob = await response.blob()
  const ext = (blob.type.split('/')[1] || 'png').split('+')[0]
  const file = new File([blob], `clipspace-paste.${ext}`, { type: blob.type || 'image/png' })
  const uploaded = await uploadImageFile(file, signal)
  return addUploadToBuffer(node, uploaded, uuidOverride, signal)
}

/**
 * Appends whatever `ComfyApp.clipspace` currently holds to *node*'s buffer
 * -- respecting `img_paste_mode` (`'selected'`, the default: just the
 * currently-selected clipspace image; `'all'`: every image in it). M2 #8
 * ("apply the same batching/refresh discipline to addClipspaceToBuffer's
 * 'all' mode, it refreshes per item today"): now runs through the SAME
 * `runAddBatch` `addFilesToBuffer` uses, rather than a second, drifting
 * copy of the hoisted-refresh/progress/cancel/toast logic -- a
 * `'selected'`-mode single image is just a batch of one (refreshes once,
 * identical to the old per-item behavior for that case). uuid captured
 * ONCE for the same "remint race" reason `addFilesToBuffer` captures it.
 * Never throws -- fails soft per image via `runAddBatch`; a missing/empty
 * clipspace, or nothing left after filtering out src-less images, is a
 * silent no-op.
 */
async function addClipspaceToBuffer(node) {
  const clipspace = ComfyApp.clipspace
  const imgs = clipspace && clipspace.imgs
  if (!imgs || !imgs.length) return

  const selected = imgs[clipspace.selectedIndex] ? [imgs[clipspace.selectedIndex]] : []
  const targets = (clipspace.img_paste_mode === 'all' ? imgs : selected).filter(
    (img) => img && img.src
  )
  if (!targets.length) return

  const uuid = currentUuid(node) // captured ONCE -- see addUploadToBuffer's docstring.
  await runAddBatch(
    node,
    targets,
    (img, signal) => addClipspaceImageToBuffer(node, img, uuid, signal),
    { noun: 'clipspace images' }
  )
}

/**
 * Overrides core's own "Paste (Clipspace)" context-menu item so it ALSO
 * appends to this node's durable buffer (file header has the full root-
 * cause writeup: `ComfyApp.pasteFromClipspace` does a bare `node.imgs =
 * [img]` REPLACE, never an append, and never calls any of our routes).
 *
 * WRAPS (never replaces) `getExtraMenuOptions` -- the identical "wrap,
 * don't replace" idiom `installCopyImageMenuItem`/`installPasteFiles`
 * already use -- calling the original FIRST so core's own item still gets
 * pushed into `options` unchanged, THEN finds that pushed item by its
 * exact label and wraps ITS callback: the original callback still runs
 * first (so `node.imgs`/`node.images`/widgets update exactly as core
 * intends -- this override is strictly additive), followed by
 * `addClipspaceToBuffer` above, which re-fetches the FULL buffer
 * afterward so both (or all) images stay visible -- never losing whatever
 * was already buffered. A no-op (nothing to wrap) if `ComfyApp.clipspace`
 * is empty -- core's own `getExtraMenuOptions` never adds the item in that
 * case either, so `options.find` below simply finds nothing.
 */
function installClipspacePasteOverride(node) {
  if (node.__epsGridClipspaceOverrideInstalled) return
  node.__epsGridClipspaceOverrideInstalled = true

  const original = node.getExtraMenuOptions
  node.getExtraMenuOptions = function (canvas, options) {
    const result = original ? original.call(this, canvas, options) : undefined
    const pasteItem = options.find((opt) => opt && opt.content === CLIPSPACE_PASTE_MENU_LABEL)
    if (pasteItem && typeof pasteItem.callback === 'function') {
      const originalCallback = pasteItem.callback
      pasteItem.callback = (...args) => {
        const returned = originalCallback(...args)
        void addClipspaceToBuffer(node)
        return returned
      }
    }
    return result
  }
}

// ---------------------------------------------------------------------------
// Bug fixes 2026-07-20: buffer refresh (display-on-load/undo) + clone-on-
// collision (copy independence) -- see file header for the full writeup.
// ---------------------------------------------------------------------------

const LIST_ROUTE = '/eps_image_grid/list'
const CLONE_ROUTE = '/eps_image_grid/clone'

/**
 * Fetches *node*'s whole on-disk buffer (`GET /eps_image_grid/list`) and
 * repopulates its displayed thumbnails to match -- FORMAT.md §6.6 "Display
 * reflects the buffer on LOAD, not only after a Run". Reuses
 * `setNodeImagesFromRefs` (the exact same "replace the node's thumbnails"
 * primitive the M2 paste-add path already uses), so a load-triggered
 * refresh renders identically to a just-added image.
 *
 * Called (via `scheduleRefresh` below, never directly) from every place a
 * node's uuid can become "the one to show" without a Run: the deferred
 * `nodeCreated` tail (`attach()`), `loadedGraphNode()`, and the
 * `onConfigure` wrap below (undo/redo, and a standalone paste/clone
 * restore). Fails soft in every direction on purpose -- a node whose uuid
 * isn't valid yet, a missing route (an older backend that hasn't picked up
 * this fix), a non-OK response, or a network error all just leave the
 * node's CURRENT display untouched rather than throwing; never rejects, so
 * callers may `await` it or fire-and-forget it interchangeably.
 */
async function refreshFromBuffer(node) {
  const uuid = currentUuid(node)
  if (!GRID_UUID_RE.test(uuid)) return
  try {
    const response = await api.fetchApi(`${LIST_ROUTE}?uuid=${encodeURIComponent(uuid)}`)
    if (!response.ok) {
      console.warn(PREFIX, `refreshFromBuffer: HTTP ${response.status} for uuid ${uuid}`)
      return
    }
    const data = await response.json()
    if (!data || data.ok !== true || !Array.isArray(data.refs)) return
    noteBufferGeneration(node, data) // M2 cache-token -- see imageUrlForRef's docstring.
    setNodeImagesFromRefs(node, data.refs)
    node.setDirtyCanvas(true, true)
  } catch (error) {
    console.warn(PREFIX, 'refreshFromBuffer failed', error)
  }
}

//: node -> {promise: Promise|null, settledAt: number} -- backs
//: `scheduleRefresh` immediately below (FORMAT.md §6.6 "refresh is CHEAP",
//: 2026-07-21).
const refreshState = new WeakMap()
//: How long a just-settled refresh is trusted as "still fresh" before a new
//: call is allowed to hit the network again -- long enough to swallow the
//: 2-3 refresh triggers one load/undo/paste settle fires in quick
//: succession, short enough that it's imperceptible as a delay.
const REFRESH_SETTLE_MS = 250

/**
 * The single funnel every call site now uses instead of calling
 * `refreshFromBuffer` directly -- coalesces the several refresh triggers
 * that can legitimately fire for the SAME node within one load/undo/paste
 * "settle" (the deferred `attach()` tail, `loadedGraphNode`, `onConfigure`)
 * down to AT MOST ONE real `/list` fetch, confirmed live to otherwise fire
 * up to 3x per node per load. A call while a fetch is already in flight for
 * *node* rides that SAME promise rather than starting a second one; a call
 * arriving shortly after one already settled is skipped outright (nothing
 * changed in the meantime, so there is nothing new to fetch). Never
 * rejects, same contract as `refreshFromBuffer` itself.
 */
function scheduleRefresh(node, opts) {
  // `force` (2026-07-27, part of the focus-clobber root fix): a caller that
  // KNOWS server state just changed (an append landed, a run finished) must
  // never be satisfied by a fetch that STARTED before its event -- riding an
  // in-flight promise, or being swallowed by the settle window, both leave
  // the pre-event buffer on screen with nothing scheduled to correct it.
  // Two stale-view holes, both hit in practice:
  //   1. in-flight ride: /list issued at T0, append lands at T1, trigger at
  //      T2 rides the T0 fetch -> renders the pre-append buffer;
  //   2. settle skip: append lands < REFRESH_SETTLE_MS after a refresh
  //      settled -> trigger returns without fetching at all.
  // Forced calls instead set `pendingForce`, and the run loop below keeps
  // fetching until no forced trigger arrived during the fetch -- every
  // forced caller is guaranteed a /list that STARTED after its call.
  // Passive callers (attach tail, loadedGraphNode, onConfigure) keep the
  // old coalescing: their state didn't change, dedupe is the whole point.
  const force = !!(opts && opts.force)
  let state = refreshState.get(node)
  if (state && state.promise) {
    if (force) state.pendingForce = true
    return state.promise
  }
  if (!force && state && Date.now() - state.settledAt < REFRESH_SETTLE_MS) {
    return Promise.resolve()
  }
  state = { promise: null, pendingForce: false, settledAt: 0 }
  refreshState.set(node, state)
  state.promise = (async () => {
    try {
      do {
        state.pendingForce = false
        await refreshFromBuffer(node)
      } while (state.pendingForce)
    } finally {
      state.promise = null
      state.settledAt = Date.now()
    }
  })()
  return state.promise
}

/**
 * `POST /eps_image_grid/clone {from, to}` -- copies *fromUuid*'s buffer
 * into *toUuid*'s (FORMAT.md §6.6 "Copy carries the images,
 * independently"). Called only from `ensureUniqueUuid`'s collision branch.
 * Mirrors `postClear`'s request/response shape exactly. Throws on a non-OK
 * response; the caller decides how to degrade (see `ensureUniqueUuid`).
 */
async function postClone(fromUuid, toUuid) {
  const response = await api.fetchApi(CLONE_ROUTE, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ from: fromUuid, to: toUuid })
  })
  let data = null
  try {
    data = await response.json()
  } catch {
    // Non-JSON body (proxy error page etc.) -- fall through to status check.
  }
  if (!response.ok) {
    const message = data && data.error ? data.error : `HTTP ${response.status}`
    throw new Error(message)
  }
  return data
}

/**
 * Wraps `onConfigure` so *node*'s display re-syncs to the on-disk buffer
 * every time litegraph re-applies its serialized state WITHOUT going
 * through `attach()` first -- concretely, undo/redo (FORMAT.md §6.6): it
 * re-applies a node's PRIOR widgets/properties via `LGraphNode.configure()`
 * directly, on the SAME existing node instance, so `nodeCreated`/`attach()`
 * never runs again and nothing else here would ever notice. `node.imgs`
 * isn't part of that serialized state either way, so without this, undo
 * left the canvas showing whatever `node.imgs` happened to be at that
 * instant (often nothing) even though the on-disk buffer was untouched.
 *
 * 2026-07-21: skips scheduling a refresh here entirely while
 * `isGraphConfiguring()` reads true -- confirmed live that this fires WHILE
 * a whole-graph load/undo/redo/tab-switch's `rootGraph.configure()` is still
 * running, i.e. BEFORE `loadedGraphNode()` runs for this same node and
 * schedules the very same refresh moments later anyway. That redundancy was
 * a third of the "3 fetches per node per load" cost (FORMAT.md §6.6
 * "refresh is CHEAP"). Still schedules when `onConfigure` fires OUTSIDE a
 * configure pass -- a standalone paste/clone's per-node restore -- since
 * nothing else covers that case.
 *
 * Guarded by `node.__epsGridConfigureWrapped` (a plain instance flag, NOT
 * the module-level `attachedNodes` WeakSet `attach()` itself uses) so this
 * specific wrap can only ever be installed once per node no matter how many
 * times something re-triggers node setup for the same live instance. Calls
 * the original `onConfigure` FIRST, unconditionally, then (maybe) refreshes
 * -- `scheduleRefresh`/`refreshFromBuffer` never reject (see their own
 * docstrings), so firing without an `await` here is safe; `onConfigure`
 * itself is a synchronous litegraph hook with no promise contract to honor.
 */
function installConfigureRefresh(node) {
  if (node.__epsGridConfigureWrapped) return
  node.__epsGridConfigureWrapped = true

  const originalOnConfigure = node.onConfigure
  node.onConfigure = function (info) {
    let result
    try {
      result = originalOnConfigure?.call(this, info)
    } finally {
      if (!isGraphConfiguring()) {
        void scheduleRefresh(this)
      }
      // 2026-07-24: every configure pass (load, undo, redo, paste-restore)
      // re-arms the settled-collision sweep below -- the sweep only acts
      // AFTER the graph has been quiet, so transient rebuild states never
      // trigger it (the v0.19.2 lesson), but a PERSISTED duplicate does.
      scheduleSettledCollisionSweep()
    }
    return result
  }
}

// ---------------------------------------------------------------------------
// Identity hardening (2026-07-24, owner report: "I copied a grid, cleared
// it, pasted two images -- running it output all of the images from the
// node I copied FROM"). Two independent guards:
//
// 1. `installUuidSerializeGuard` -- at serialization time (both the /prompt
//    build and workflow saves prefer `widget.serializeValue()` over the raw
//    `.value`; the same frontend fact the rgthree capture fix, v0.14.1,
//    established) the hidden `grid_uuid` widget now ALWAYS emits
//    `currentUuid(node)` -- `properties.uuid` first, the exact same
//    authoritative source every display/route path already uses. So even
//    if some paste/undo interleaving leaves the widget's `.value` carrying
//    the SOURCE node's uuid while `properties.uuid` holds this node's own,
//    the RUN and the SAVE can no longer diverge from what the node shows.
//
// 2. `scheduleSettledCollisionSweep` -- the load-pass rules deliberately
//    never remint during configure (saved uuids are authoritative there;
//    v0.19.2), which is correct for TRANSIENT rebuild states but leaves one
//    real gap: a PERSISTED duplicate -- e.g. an undo snapshot captured
//    between an interactive paste and its deferred remint, then restored --
//    shares its buffer with the original forever. The sweep runs only
//    after the graph has been QUIET for a while, so it can tell the two
//    apart: if two live grids STILL share a uuid at settle time, that is a
//    genuine persisted duplicate -- the lowest-id node keeps the identity,
//    every other gets the normal mint+clone (never a data loss: clones
//    copy the buffer).
// ---------------------------------------------------------------------------

function installUuidSerializeGuard(node) {
  const widget = getGridUuidWidget(node)
  if (!widget || widget.__epsGridSerializeGuard) return
  widget.__epsGridSerializeGuard = true
  widget.serializeValue = () => currentUuid(node) || widget.value || ''
}

let settledSweepTimer = 0

function scheduleSettledCollisionSweep() {
  clearTimeout(settledSweepTimer)
  settledSweepTimer = setTimeout(async () => {
    try {
      const nodes = (app.graph?._nodes || app.graph?.nodes || []).filter(
        (n) => nodeClassOf(n) === CLASS_ID
      )
      const byUuid = new Map()
      for (const n of nodes) {
        const uuid = currentUuid(n)
        if (!GRID_UUID_RE.test(uuid)) continue
        if (!byUuid.has(uuid)) byUuid.set(uuid, [])
        byUuid.get(uuid).push(n)
      }
      for (const [uuid, group] of byUuid) {
        if (group.length < 2) continue
        group.sort((a, b) => (a.id || 0) - (b.id || 0))
        for (const duplicate of group.slice(1)) {
          console.log(
            PREFIX,
            `settled duplicate: nodes share uuid ${uuid} after the graph went ` +
              `quiet -- reminting node #${duplicate.id} (buffer cloned, ` +
              'lowest-id node keeps the identity)'
          )
          // The collision re-check + mint + clone all live in
          // ensureUniqueUuid's own collision branch.
          await ensureUniqueUuid(duplicate, { allowCollisionMint: true })
        }
      }
    } catch (error) {
      console.warn(PREFIX, 'settled collision sweep failed', error)
    }
  }, 1500)
}

/**
 * 2026-07-24 (owner: "something seems broken where I can't get a full
 * workflow to run even when I have all of the items checked in the
 * switcher"): an ENABLED but EMPTY grid with nothing wired in emits an
 * ExecutionBlocker, and core then silently skips every downstream branch
 * it touches (FORMAT.md §6.4's documented list-input semantics) -- the
 * queue "succeeds" with nothing to show and no explanation. This makes
 * the silence visible: right after such a run, a warning toast names the
 * node. Advisory only -- every failure path here is swallowed.
 */
async function warnIfEmptyAfterRun(node) {
  try {
    const uuid = currentUuid(node)
    if (!GRID_UUID_RE.test(uuid)) return
    const imageInput = (node.inputs || []).find((input) => input.name === 'image')
    if (imageInput && imageInput.link != null) return // live input -> flow-through ran
    const response = await api.fetchApi(`${LIST_ROUTE}?uuid=${encodeURIComponent(uuid)}`)
    if (!response.ok) return
    const body = await response.json()
    if ((body.refs || []).length) return
    app.extensionManager?.toast?.add?.({
      severity: 'warn',
      summary: 'EPS Image Grid',
      detail:
        `"${node.title || 'EPS Image Grid'}" ran with an empty buffer and ` +
        'nothing wired in -- downstream nodes fed from it were skipped this ' +
        'run. Collect or paste something into it, or toggle its branch off.',
      life: 6000
    })
  } catch {
    // Advisory only -- never let the warning path break anything.
  }
}

// ---------------------------------------------------------------------------
// Execution-complete refresh (2026-07-22 owner fix: output-panel pollution)
// -- see file header for the full writeup + citations.
// ---------------------------------------------------------------------------

//: node -> the last `progress_state` state seen for it ('running'/
//: 'finished'/etc, or absent before the first sighting) -- so a refresh
//: fires only on the TRANSITION into 'finished', not on every LATER
//: `progress_state` message that still happens to list this node (the
//: registry resends every non-pending node's state on every change for
//: the rest of that prompt, per `progress.py`'s `_send_progress_state`).
const lastKnownProgressState = new WeakMap()

//: Guards `installExecutionRefreshListener` so the module-scope
//: `api.addEventListener` below is only ever attached once, no matter how
//: many times `init()` runs (mirrors this file's other one-time-install
//: guards, e.g. `attachedNodes`).
let executionRefreshListenerInstalled = false

/**
 * Installs ONE module-scope `api.addEventListener('progress_state', ...)`
 * listener (not one per node -- every live EPSImageGrid node is checked
 * against the same event) that refreshes a node from its on-disk buffer
 * the moment ITS OWN execution finishes -- regardless of whether that
 * Run's result carried a `"ui"` key at all. See file header for why
 * `progress_state`, not `"executed"`, is the signal used here.
 */
function installExecutionRefreshListener() {
  if (executionRefreshListenerInstalled) return
  executionRefreshListenerInstalled = true

  api.addEventListener('progress_state', (event) => {
    const nodes = event?.detail?.nodes
    if (!nodes) return
    const graphNodes = app.graph?._nodes || app.graph?.nodes || []
    for (const node of graphNodes) {
      if (nodeClassOf(node) !== CLASS_ID) continue
      const entry = nodes[String(node.id)]
      const state = entry ? entry.state : undefined
      const previous = lastKnownProgressState.get(node)
      if (state !== undefined) lastKnownProgressState.set(node, state)
      if (state === 'finished' && previous !== 'finished') {
        // Forced: this event IS the news that server state changed, so it
        // must never ride a pre-event fetch or be settle-skipped (see
        // scheduleRefresh). The 800ms "second, delayed refresh" that used
        // to sit here -- a timer bet against core's async executed-render
        // -- is gone: it treated the symptom, could never win (the clobber
        // re-fired on EVERY repaint via updatePreviews' identity check),
        // and is superseded by the real fix, `syncCoreOutputStore` +
        // `installExecutedMerge` (their docstrings have the mechanism).
        void scheduleRefresh(node, { force: true })
        void warnIfEmptyAfterRun(node) // see its docstring (2026-07-24)
      }
    }
  })
}

/**
 * Wraps `node.onExecuted` so a Run's reported refs are folded into the
 * full-buffer view SYNCHRONOUSLY, in the same tick core clobbers the store
 * -- the deterministic half of the focus-clobber root fix (the other half
 * is `syncCoreOutputStore`; its docstring has the whole mechanism).
 *
 * Order, confirmed in `app.ts`'s `'executed'` listener: core FIRST replaces
 * `app.nodeOutputs[locator]` with just this Run's refs, THEN calls
 * `node.onExecuted(output)` -- same tick, no repaint possible in between
 * (draws are rAF; JS is single-threaded). So by the time any repaint's
 * `updatePreviews` looks, the store already holds the merged full list
 * again, with `node.images`' own identity. No timers, no races, no bets.
 *
 * Two cases:
 *  - genuinely new refs (a Collect appended): show the whole grid with the
 *    new image IN it (`setNodeImagesFromRefs` -> `imageIndex = null`) --
 *    the owner-reported behavior ("a new image becomes the focus and there
 *    is no way back to the full grid") inverted to what he asked for.
 *  - nothing new (a CACHED node re-sends its old `ui` on every re-queue,
 *    both `execution.py` success paths do): only heal the store -- the
 *    user's current view, INCLUDING a deliberately focused cell
 *    (`imageIndex`), is left alone. A batch of queued runs must not yank
 *    the user back to the grid once per run while they're inspecting one
 *    image. `mergeBufferRefs` returning the SAME array identity for this
 *    case is what makes the heal invisible to `updatePreviews`.
 *
 * The forced `/list` afterwards reconciles authoritatively (true buffer
 * order, appends from another machine, evictions) -- display correctness
 * never depends on it arriving, or arriving in order.
 */
function installExecutedMerge(node) {
  const originalOnExecuted = node.onExecuted
  node.onExecuted = function (output) {
    let result
    if (typeof originalOnExecuted === 'function') {
      result = originalOnExecuted.apply(this, arguments)
    }
    try {
      const incoming = output && Array.isArray(output.images) ? output.images : []
      const existing = Array.isArray(this.images) ? this.images : []
      const { refs, added } = mergeBufferRefs(existing, incoming)
      if (added > 0) setNodeImagesFromRefs(this, refs)
      else syncCoreOutputStore(this, refs)
      void scheduleRefresh(this, { force: true })
    } catch (error) {
      console.warn(PREFIX, 'onExecuted merge failed', error)
    }
    return result
  }
}

// ---------------------------------------------------------------------------
// Public entry points (called from web/eps_image.js)
// ---------------------------------------------------------------------------

/** EPSImageGrid is a real backend node (no frontend-only type registration
 * needed) -- everything PER-NODE here is done in attach(). Kept as an
 * export because eps_image.js calls it unconditionally -- also the one
 * place a module-scope (not per-node) listener belongs, since this runs
 * exactly once per extension load. */
export function init() {
  installExecutionRefreshListener()
}

/** Per-node-instance attach; no-op unless *node* is an EPSImageGrid. */
export function attach(node) {
  try {
    if (!node) return
    if (nodeClassOf(node) !== CLASS_ID) return
    if (attachedNodes.has(node)) return
    attachedNodes.add(node)

    // Captured HERE, synchronously, at the exact moment litegraph creates
    // this node instance -- NOT re-read inside the deferred callback below.
    // `isGraphConfiguring()` correctly reads `true` during a whole-graph
    // load/undo/redo/tab-switch and `false` for a genuine interactive
    // paste/clone (confirmed live, see file header's 2026-07-21 section),
    // but `LGraph.configure()` -- the thing it wraps -- is fully
    // synchronous and reliably finishes (flag back to `false`) long before
    // any `setTimeout(fn, 0)` macrotask gets a turn, so re-reading it below
    // would always see `false` and defeat the whole guard.
    const allowCollisionMint = !isGraphConfiguring()

    hideGridUuidWidget(node)
    // Order is deliberate (owner ask 2026-07-29: "the clear button should be
    // below the two add buttons"): the two ADD actions first, then the
    // destructive one last -- the same put-Delete-last reasoning §6.3's
    // controller button stack already follows. Only affects paint order;
    // all three carry `serialize = false`, so `widgets_values` (and every
    // saved workflow) is untouched by the move.
    addAddImagesButton(node) // M1
    addAddFolderButton(node) // M3
    addClearButton(node)
    installFileInputCleanup(node) // tears down both pickers' <input>s + any in-flight batch on removal
    installPasteFiles(node)
    installDragAndDrop(node) // assets-panel/Finder drop-to-add -- see its own docstring
    installCopyImageMenuItem(node) // Mac-over-LAN-http Copy Image fix -- see its own docstring
    installDeleteImageMenuItem(node) // un-brick-my-grid delete -- see its own docstring
    installClipspacePasteOverride(node) // clipspace paste-to-add -- see its own docstring
    installConfigureRefresh(node) // undo/redo display fix -- see its own docstring
    // Makes `isImageNode(node)` (litegraphUtil.ts) true even before
    // anything has ever been collected, so a real Ctrl+V onto a
    // brand-new, still-empty node routes to `pasteFiles` above instead of
    // core creating a fresh LoadImage node (see file header's "Paste IN"
    // section for the exact check this satisfies).
    node.previewMediaType = 'image'
    installUuidSerializeGuard(node) // 2026-07-24 identity hardening -- see its block comment
    installExecutedMerge(node) // 2026-07-27 focus-clobber root fix -- see its docstring

    // Deferred one tick -- the paste-collision path. See file header
    // point 1 for exactly why this can't run synchronously here. Awaiting
    // ensureUniqueUuid before scheduling a refresh (rather than firing both
    // in parallel) matters specifically for the collision branch: it
    // fetches whichever uuid actually settles -- the fresh, possibly-just-
    // cloned one -- not whatever was on the node before dedup ran.
    setTimeout(async () => {
      try {
        await ensureUniqueUuid(node, { allowCollisionMint })
      } catch (error) {
        console.warn(PREFIX, 'deferred uuid dedup failed', error)
      } finally {
        installUuidSerializeGuard(node) // in case the widget appeared late
        scheduleSettledCollisionSweep() // 2026-07-24 -- persisted-duplicate net
        await scheduleRefresh(node)
      }
    }, 0)
  } catch (error) {
    console.warn(PREFIX, 'attach failed', error)
  }
}

/** Fires once per node, after a whole saved workflow has finished loading
 * (see file header point 2). No-op unless *node* is an EPSImageGrid.
 * Always passes `allowCollisionMint: false` -- this hook only ever fires
 * from within a whole-graph load pass (confirmed live: never for a
 * same-session paste), so collision-minting is never appropriate here; see
 * `ensureUniqueUuid`'s docstring. */
export function loadedGraphNode(node) {
  try {
    if (!node) return
    if (nodeClassOf(node) !== CLASS_ID) return
    installUuidSerializeGuard(node) // 2026-07-24 identity hardening
    ensureUniqueUuid(node, { allowCollisionMint: false })
      .catch((error) => console.warn(PREFIX, 'loadedGraphNode dedup failed', error))
      .finally(() => {
        scheduleSettledCollisionSweep() // 2026-07-24 -- persisted-duplicate net
        return scheduleRefresh(node)
      })
  } catch (error) {
    console.warn(PREFIX, 'loadedGraphNode dedup failed', error)
  }
}
