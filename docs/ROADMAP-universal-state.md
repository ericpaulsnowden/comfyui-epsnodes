# Universal State Controller roadmap

Owner spec 2026-08-26: "a new node called 'Universal State Controller' …
UI identical to the Lora State Controller … save states of any of the EPS
nodes that have a state … a sub page … check on and off the nodes they
want to include." Four design choices confirmed the same day: the 14 core
nodes; cross-workflow apply WITH a diff; side-by-side with the existing
Lora State Controller (untouched); scope checkboxes stored per controller
node, per workflow. The architecture question ("hidden JSON bridge
widgets — better way?") was answered with M0: the storage mechanism stays
(ComfyUI forces widget-borne state), the per-consumer ad-hoc parsers are
the debt, and a declarative registry is the fix.

## M0 — state registry (SHIPPED v0.83.0)

`EPS_STATE_WIDGETS` class attribute on every state-bearing node (pure
declarative dict: widget → kind + constraints + excluded-with-reasons),
collected by `GET /eps/state_registry` (routes_list_flags' memo shape).
Closed kind set: string / int / float / choice / lines / json_array /
json_object(+key_pattern). Consumers validate through the registry instead
of hand-parsing blobs. Existing consumers (estimator's six parsers, EPS
Save Image's `_pinnable_class`) migrate OPPORTUNISTICALLY later — no
big-bang refactor.

## M1 — capture/apply within a workflow (SHIPPED v0.83.0)

`EPSUniversalStateController` (frontend-only, §6.2's architecture):
States page = the lora controller's twin (groups, search, optimistic
save/apply/delete, per-workflow collapsed groups). Storage
`library/states/*.json` + `universal_states_layout.json` sidecar
(sets_store's atomic/gvfs/splice machinery, FORMAT §4.3). Apply matches
by exact pathId+class, validates every value through the registry (choice
against the LIVE widget's options), writes via widget callbacks, and ends
with an honest diff toast — never a silent partial apply. Foreign
(unknown-class) entries: save warns, load tolerates (forward compat).

## M2 — Included-nodes sub page (SHIPPED v0.83.0)

Second page on the node: every state-bearing node on canvas (subgraphs
included), grouped by class, per-node checkboxes + tri-state per-class
masters. Default INCLUDED; the `'Included nodes'` property stores only
exclusions, so new nodes join automatically and the property stays small.
Per workflow by construction.

## M3 — cross-workflow apply (owner's word)

Layered matching when pathIds don't line up: exact id → node title →
class+position; a PRE-APPLY dry-run diff dialog ("9 matched · 2 by title ·
1 not found · 1 untouched") with per-row accept; machine-specific values
(file paths) routed through `EPSNodes.HealModelPaths`-style healing.
Design risk to solve: two same-class nodes with swapped titles.

## M4 — opt-in risky fields (owner's word)

Explicitly-flagged captures beyond the core scope: Frame Saver
video_path+frame (path-healed), Image Grid mode/focus (never grid_uuid),
socket-shaping properties (Distributor `Outputs`, Model Switcher
`High/low pairs`) with loud orphaned-wire warnings in the diff, and
third-party loader rows (rgthree/DaSiWa) unifying the two controllers'
coverage in one state.

## Parked / rejected

- Rejected: replacing hidden JSON bridge widgets with server-side state —
  grid_uuid demonstrates the portability cost; widget-borne state is what
  makes copy/paste, workflow JSON sharing, and PNG provenance work.
- Parked: migrating cross_sweep.js's six state parsers + nodes_save_image's
  `_pinnable_class` onto the registry (opportunistic, zero user value on
  its own).
