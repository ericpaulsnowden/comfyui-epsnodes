# Developing EPSNodes

A map for people changing this pack: it points at code and at the binding contract and repeats
neither. User install and update: [INSTALL.md](INSTALL.md). What each node does: `README.md`. The
contract: [FORMAT.md](FORMAT.md), which is binding; an interface change amends it first, in the same
commit as the code. Cite sections as `FORMAT.md §N` (bare `§N` below means FORMAT.md).

## 1. Architecture map

**Load sequence.** `__init__.py` is the entry point ComfyUI loads. Every registration step is guarded: a
failure is logged and the rest of the pack still loads (FORMAT.md §7.8).

1. Builds one `LibraryContext` (`lora_library/context.py`) from `folder_paths`: `user_dir` =
   `<ComfyUI user dir>/lora_library`, default library = `<user_dir>/library`, plus LoRA list/resolve
   callables.
2. `lora_library.routes.register(context)` attaches the library routes to `PromptServer.instance.routes`
   (`routes._register_all`: core + `routes_notebook` + `routes_sets` + `routes_lora_picker`).
3. Loops `_NODE_SPECS` = `(module_path, class_id, display_name)`: imports the module, calls
   `module.set_context(context)` if defined, fills `NODE_CLASS_MAPPINGS` / `NODE_DISPLAY_NAME_MAPPINGS`.
4. Registers the remaining routes, each in its own `try`:
   `eps_image.routes_{image_grid,frame_saver,checkpoint_switcher,list_flags,state_registry}.register()`,
   `eps_audit.routes_audit.register()` (+ `install_hooks_at_startup()`), and the context-taking
   `routes_resolution_presets.register_live(ctx)` and `routes_universal_states.register_live(ctx)`.
5. Sets `WEB_DIRECTORY = "./web"`, warns if installed twice, logs `EPSNodes vX.Y.Z loaded`.

**Python packages.** No module imports `torch`, `comfy`, `folder_paths`, `server`, numpy, PIL or `av` at
module scope (type-checking-only imports aside); each is imported inside the function that needs it.
- `lora_library/`: the library-folder family. `LibraryContext` is the injection seam: stores, routes and nodes
  receive it and import no ComfyUI module.
- `eps_image/`: sibling family for image and workflow nodes, with their stores and routes. It imports
  `lora_library` only via the two-branch idiom (`from ..lora_library.x`, flat fallback on `ImportError`) in
  `resolution_presets_store.py`, `nodes_frame_saver.py`, `nodes_save_image.py`; other helpers are duplicated
  on purpose.
- `eps_audit/`: the outward-looking audit family (egress hooks, scanner, report, route, node).

**The 22 nodes** = 20 Python classes + 2 frontend-only virtual nodes. Folder = `EPSNodes/<Folder>`; the two
virtual nodes set theirs in a JS `NODE_CATEGORY` constant, which a search of `.py` files misses.

| Class id | Display name | Source | Folder | Panel in `web/` | FORMAT |
|---|---|---|---|---|---|
| `LoraLibraryNotebook` | EPS Prompt Notebook | `lora_library/nodes_notebook.py` | Prompts | `lora_library/notebook.js` | §6.1 |
| `EPSPromptBuilder` | EPS Prompt Builder | `lora_library/nodes_prompt_builder.py` | Prompts | `lora_library/prompt_builder.js` | §6.15 |
| `LoraLibraryApplySet` | EPS Apply LoRA Set | `lora_library/nodes_sets.py` | LoRA | `lora_library/sets.js` | §6.2 |
| `LoraLibrarySweep` | EPS LoRA Iterator | `lora_library/nodes_sweep.py` | LoRA | none | §6.8 |
| `EPSLoraPicker` | EPS LoRA Picker | `lora_library/nodes_picker.py` | LoRA | `lora_library/picker.js` (+ `pll_bridge.js`, `dasiwa_bridge.js`) | §6.13 |
| `EPSSwitcher` | EPS Image Switcher | `eps_image/nodes_switcher.py` | Switchers | `eps_image/switcher.js` | §6.4 |
| `EPSModelSwitcher` | EPS Model Switcher | same (factory `_make_switcher_ns`) | Switchers | same | §6.4b |
| `EPSClipSwitcher` | EPS CLIP Switcher | same | Switchers | same | §6.4b |
| `EPSVaeSwitcher` | EPS VAE Switcher | same | Switchers | same | §6.4b |
| `EPSCheckpointSwitcher` | EPS Checkpoint Switcher | `eps_image/nodes_checkpoint_switcher.py` | Switchers | `eps_image/checkpoint_switcher.js` | §6.12 |
| `EPSResolution` | EPS Resolution | `eps_image/nodes_resolution.py` | Images | `eps_image/resolution.js` | §6.5 |
| `EPSImageGrid` | EPS Image Grid | `eps_image/nodes_image_grid.py` | Images | `eps_image/image_grid.js` | §6.6 |
| `EPSFrameSaver` | EPS Frame Saver | `eps_image/nodes_frame_saver.py` | Images | `eps_image/frame_saver.js` | §6.7 |
| `EPSSaveImage` | EPS Save Image | `eps_image/nodes_save_image.py` | Images | `eps_image/save_image.js` | §6.14 |
| `EPSCrossSweep` | EPS Run Multiplier | `eps_image/nodes_cross_sweep.py` | Utilities | `eps_image/cross_sweep.js` | §6.10 |
| `EPSDistributor` | EPS Distributor | `eps_image/nodes_distributor.py` | Utilities | `eps_image/distributor.js` | §6.11 |
| `EPSBypass` | EPS Bypass | `eps_image/nodes_bypass.py` | Utilities | `eps_image/bypass.js` | §6.18 |
| `EPSSplatPlacement` | EPS Splat Placement | `eps_image/nodes_splat_placement.py` | Utilities | none | §6.19 |
| `EPSNodeAudit` | EPS Node Audit | `eps_audit/nodes_audit.py` | Utilities | none | §9 |
| `EPSNumberController` | EPS Number Controller | `eps_image/nodes_number_controller.py` | Controllers | `eps_image/number_controller.js` | §6.17 |
| `LoraLibrarySetController` | EPS Lora Loader State Controller | none (frontend only) | LoRA | `lora_library/controller.js` | §6.3 |
| `EPSUniversalStateController` | EPS Universal State Controller | none (frontend only) | Controllers | `lora_library/universal_controller.js` | §6.16 |

**Frontend.** ComfyUI auto-loads every top-level `.js` under `web/`: two files, one `app.registerExtension`
each; the sub-directories are imported by them. Each panel module exports an attach function that returns
early unless `node.comfyClass` is its class (`attach(node)` in `eps_image/*.js`; `attachNotebookWidget`,
`attachPromptBuilderPanel`, `attachApplySetBehavior`, `attachPickerPanel` in `lora_library/`). Every call goes
through `safely(label, fn)`, so one broken panel cannot block the rest. Neither extension uses
`beforeRegisterNodeDef`.

| Entry file | Extension name | Hooks used |
|---|---|---|
| `web/lora_library.js` | `lora_library.LoraLibrary` | `settings`, `aboutPageBadges`; `init` (registers the two virtual nodes via `LiteGraph.registerNodeType`); `beforeRegisterVueAppNodeDefs` (their display names); `nodeCreated` (notebook, prompt_builder, sets, picker, `path_heal.attachConfigureHeal`); `loadedGraphNode` (`path_heal`); `setup` (`initSettings`, `sets.initSetsFreshness`) |
| `web/eps_image.js` | `eps_image.EPSImageNodes` | `aboutPageBadges`; `init` (per-module one-time setup: stylesheets, event listeners, a chained `app.handleFile`); `nodeCreated` (every `eps_image/*.js` attach, plus the Vue-mode warning `warnIfVueNodesMode`); `loadedGraphNode` (`image_grid`, `frame_saver`) |

## 2. One request, end to end: saving a Prompt Notebook entry

1. **Panel.** Save runs `performSave` (`web/lora_library/notebook.js`), which posts
   `{file, name, text, base_mtime}` with
   `api.postJson('/lora_library/notebook/entry', body, {timeoutMs: WRITE_TIMEOUT_MS})`
   (`web/lora_library/api.js`, over `api.fetchApi`; real URL `/api/lora_library/notebook/entry`).
2. **Route.** `post_notebook_entry` in `lora_library/routes_notebook.py` (attached by
   `register(context, routes)` via `routes._register_all`). `_resolve_and_guard` runs in `asyncio.to_thread`:
   `LibraryContext.resolve_notebook_file(file)`, then `notebook_path_error` (§2: remote callers are confined
   to `library_dir` + `remote_dirs`; writes must end in `.md`). Failures return `error_response(4xx, msg)`.
3. **Store and disk.** `markdown_store.load_notebook(path)` gives `(ParsedNotebook, mtime, line_ending)`;
   `check_conflict(base_mtime, current_mtime)` raises `ConflictError`, answered 409 `{error, mtime}` (§3.5);
   `upsert_entry` edits the parse; `save_notebook` writes via `context._atomic_write_text` (same-directory
   temp file + `os.replace`, §1) to `<library_dir>/loras.md` or the absolute path `file` names. Load and save
   run in `asyncio.to_thread`; the response is folded back by `noteFileMtime`.
4. **Widgets and queue (no HTTP).** Clicking rows runs `setSelection`, then `syncEntryWidget`, which writes
   the hidden `entry` STRING widget (one name per line, file order); unsaved text is mirrored into the hidden
   `drafts` widget (`DRAFT_SYNC_DEBOUNCE_MS`). Both live in the workflow's `widgets_values`. Queueing sends
   the serializing widgets:
   `{"<id>": {"class_type": "LoraLibraryNotebook", "inputs": {file, entry, pinned, drafts, separator}}}`; the
   two-pane panel is a DOM widget with `serialize: false`, so it is absent.
5. **Execution.** `IS_CHANGED` hashes the selected entries' effective text (`_selection_token`); `read_entry`
   runs once. `INPUT_IS_LIST = True` list-wraps every input (`_unwrap_scalar` undoes it). A valid `pinned`
   skips the file; otherwise `resolve_selection(context, file, entry, drafts)` reads it via
   `_load_notebook_cached` (keyed on mtime_ns + size), applies drafts, and returns `(texts, names)`
   (`OUTPUT_IS_LIST`), crossed with any wired `text`/`name`. The file is re-read every run: the file is the
   truth, the UI is a view (§6).

## 3. Module ownership

| Module | Owns | Binding |
|---|---|---|
| `__init__.py` | context build, route + node registration, load-time containment | §7.8, §8 |
| `lora_library/context.py` | `LibraryContext`, `config.json`, `library_dir`, `remote_dirs`, foreign-absolute healing, atomic writes | §1, §2 |
| `lora_library/routes.py` | core routes, shared guards (`request_is_loopback`, `notebook_path_error`, `error_response`), `fs/list` | §2, §5 |
| `lora_library/markdown_store.py`, `routes_notebook.py`, `nodes_notebook.py`, `nodes_prompt_builder.py` | notebook grammar, writes, conflicts; Notebook, Builder | §3, §5, §6.1, §6.15 |
| `lora_library/sets_store.py`, `routes_sets.py`, `nodes_sets.py`, `nodes_sweep.py` | LoRA sets + layout sidecar; Apply Set, Iterator | §4, §6.2, §6.8 |
| `lora_library/lora_picker_store.py`, `routes_lora_picker.py`, `nodes_picker.py` | favorites/recents, thumbnails, Picker | §6.13 |
| `lora_library/universal_states_store.py`, `routes_universal_states.py` | state files; registry validation on save | §4.3, §6.16 |
| `eps_image/nodes_*.py` | the image/workflow nodes in the table above | §6.4 to §6.19 |
| `eps_image/*_store.py`, `routes_*.py`, `frame_saver_video.py` | grid buffer, size presets, video decode; routes and metadata feeds | §5, §6.5 to §6.7, §6.10, §6.16 |
| `eps_audit/` | node-pack audit | §9 |
| `web/lora_library.js`, `web/eps_image.js` | extension entry points | §7, §7.8 |
| `web/lora_library/*.js`, `web/eps_image/*.js` | panels; shared `api.js`, `search.js`, `path_heal.js`, `settings.js`, `owned_link_colour.js` | §7.2, §7.3, §7.5, §7.6, §7.9 |

## 4. State and storage

**Per server, on disk (survives restart).**
- `<ComfyUI user dir>/lora_library/config.json`: `library_dir` and the `remote_dirs` allow-list, written only
  by the loopback-only `POST /lora_library/config` and `/remote_dirs`; the Settings entry "EPSNodes > Library
  folder" mirrors it. §1, §2.
- The library folder (default `<user dir>/lora_library/library/`; may be a NAS): `*.md` notebooks (`loras.md`
  default), `sets/<slug>.json`, `lora_sets_layout.json`, `lora_picker.json`, `resolution_presets.json`,
  `states/<slug>.json`, `universal_states_layout.json`. §1, §4 to §4.3, §6.5, §6.13.
- Image Grid buffer: `<ComfyUI output dir>/eps_image_grid/<grid_uuid>/` (`NNNN.png`, `manifest.json`,
  `.thumbs/`); under `output/`, not `temp/`, so a restart keeps it. §6.6.

**Per server, in memory (lost at restart).** The `/eps/list_flags` and `/eps/state_registry` memos,
`LibraryContext`'s config cache and 30 s "directory exists" memo, the sets-listing caches,
`nodes_notebook._PARSE_CACHE`, the audit egress tallies.

**Per workflow (travels with the file).** Widget values in `widgets_values`, restored by position: whatever
Python reads at run time (Notebook `file`/`entry`/`pinned`/`drafts`/`separator`, a Switcher's `toggles`, Image
Grid `grid_uuid`). Node properties (`node.properties`): view state that survives a panel rebuild but is not
run input (Notebook `Collapsed sections`, `Open category`; Picker `Browse folder`). The API prompt is derived
at queue time, not stored; EPS Save Image bakes provenance into its files (§6.14).

**Per browser.** The pack writes nothing to `localStorage`, `sessionStorage` or IndexedDB (none in `web/`).
Per-tab JS state (each panel's `state` object, module caches such as `notebookCacheGet`) is rebuilt on load,
tab switch, undo and reload, so nothing the user authored may live only there (§7.9).
`EPSNodes.HealModelPaths` lives in ComfyUI's settings store; Vue-node mode is per browser (§7.5).

## 5. Supported versions

- Python `>=3.10` (`requires-python`). No runtime dependencies (`dependencies = []`; `requirements.txt` is
  intentionally empty); rgthree-comfy is optional (INSTALL.md).
- ComfyUI: the repo declares and enforces no minimum. It claims: a 2025+ build, developed against current
  ComfyUI (INSTALL.md); examples "round-trip verified against ComfyUI v0.28 / frontend 1.45"
  (`examples/README.md`); rig checks against ComfyUI v0.36.0 + frontend 1.52.7 (§6.14, §6.19). Version-gated
  behaviour is noted where it bites: `LGraphNode.size` is a typed-array Proxy on frontend 1.48+ (§7.2);
  `loadedGraphNode` was checked on 1.48.7 and `beforeRegisterVueAppNodeDefs` on 1.45.21 (comments in
  `web/lora_library.js`).
- Classic nodes vs the new node design (Vue nodes): the user-facing caveat is the README section
  `A note on ComfyUI's "New node design (beta)"`; the mechanism is §7.5. Hand-drawn controls
  (Switcher/Distributor toggles, Resolution's canvas-only hooks) do not run in Vue mode, and plumbing widgets
  stay hidden there only because `INPUT_TYPES` options carry `"hidden": True` (pinned by
  `tests/test_vue_nodes_compat.py`).

## 6. Setup and test

User install and update (requirements, clone, library-folder setting, hard refresh) are in
[INSTALL.md](INSTALL.md); this is the developer delta.

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/ericpaulsnowden/comfyui-epsnodes
cd comfyui-epsnodes
python -m pip install pytest pytest-asyncio pytest-aiohttp ruff   # the repo has no dev-requirements file
python -m pytest -p no:cacheprovider -q tests
ruff check .
```

- Run from the repo root. `pyproject.toml` sets `testpaths`, `asyncio_mode = "auto"` and `--confcutdir=tests`,
  which stops pytest importing the root `__init__.py` (it needs `folder_paths`). `-p no:cacheprovider` turns
  off pytest's cache plugin (no `.pytest_cache/`, no last-failed replay): the maintainers' standing flag, so a
  run never depends on an earlier one.
- Optional dependencies sit behind `pytest.importorskip` (skipped when absent): `torch`, `av`, `PIL`,
  `safetensors`. Two tests need a real ComfyUI checkout (`tests/test_nodes_sets_weight_math.py`,
  `tests/test_nodes_sweep.py`): set `EPS_COMFYUI_ROOT=/path/to/ComfyUI` or they skip.
- `ruff check .` uses `[tool.ruff]` (line length 100, `web/` excluded); lint your diff, since the tree can
  carry older findings.
- **Stubs.** No test imports ComfyUI. `tests/conftest.py` gives `context` and `library_dir`: a real
  `LibraryContext` over `tmp_path` with a fake LoRA list. Per test, `monkeypatch.setitem(sys.modules, ...)`
  fakes `folder_paths`, `comfy_execution(.graph)`, `server`, `comfy(.utils)` (`tests/test_image_grid.py`,
  `tests/test_save_image.py`). Routes run via `build_routes(...)` and `aiohttp_client`. The root `__init__.py`
  cannot be imported, so registration is pinned by reading its source
  (`tests/test_splat_placement.py::test_registered_in_the_pack`).
- **Torch-free by design.** `tests/test_cross_sweep.py::test_module_never_imports_comfy_or_torch` imports
  `eps_image.nodes_cross_sweep` in a fresh subprocess and asserts no `torch`, `comfy` or `comfy_execution`
  module loaded; `test_prompt_builder.py`, `test_bypass.py`, `test_distributor.py`, `test_switcher_types.py`
  pin it on module source.
- **JavaScript tests** are `tests/test_*_js.py`. Those that execute JS find `node` with `shutil.which("node")`
  and skip without it; typically each copies the real `web/**.js` module into a temp tree mirroring the served
  layout, stubs `scripts/app.js`, and runs a generated `.mjs` probe (or `node --check`). Closure-bound code is
  pinned by source-text assertions. There is no `package.json`, bundler or browser harness: canvas drawing,
  drags and live races are checked by hand. The suite ran clean on Node 22.

## 7. How to add

### A node
1. Write `eps_image/nodes_<name>.py` (or a new sibling package): `CATEGORY = "EPSNodes/<Folder>"` (menu folder
   only; saved workflows ignore it), `INPUT_TYPES`, `RETURN_TYPES`, `FUNCTION`, and `OUTPUT_NODE` /
   `IS_CHANGED` / `check_lazy_status` as needed. Import torch/comfy inside functions; define
   `set_context(context)` if it needs the library folder (the loader calls it). Small models:
   `nodes_bypass.py`, `nodes_splat_placement.py`.
2. Register: add `("eps_image.nodes_<name>", "ClassId", "EPS Display Name")` to `_NODE_SPECS` in
   `__init__.py`. Class ids are frozen once shipped (§8). A module that fails to import is logged and skipped:
   check the server log, not only the menu.
3. Positional rules: new widget inputs go at the END of `optional` with a default (`widgets_values` restores
   by index); new outputs are appended to `RETURN_TYPES`. Plumbing widgets carry `"hidden": True` in their
   input options for the Vue renderer (§7.5).
4. State registry: declare
   `EPS_STATE_WIDGETS: ClassVar[dict[str, Any]] = {"format": 1, "widgets": {name: {"kind": ...}}, "excluded": {name: "reason"}}`.
   Kinds are a closed set (`string`, `int`, `float`, `choice`, `lines`, `boolean`, `json_array`,
   `json_object`; §6.16); a new kind moves `universal_states_store._check_kind`, `validateStateValue` in
   `universal_controller.js` and `_CLOSED_KINDS` in the test together. Add a row to `_STATE_BEARING_SPECS` in
   `tests/test_state_registry.py`: `TestCompleteness` fails unless every declared name is in `INPUT_TYPES` and
   every widget-bearing (non-`forceInput`) input is declared or excluded with a reason. `EPSSplatPlacement`
   and `EPSNodeAudit` carry no entry.
5. Panel, if any: `web/<family>/<name>.js` exporting `init()` and `attach(node)` (`eps_image/` convention;
   `lora_library/` modules name theirs, e.g. `attachNotebookWidget`), plus `loadedGraphNode(node)` when load
   timing matters. Import it in the family entry file and call it through `safely(...)` from `init` /
   `nodeCreated`.
6. Docs and tests: amend FORMAT.md first (a new `§6.N`; routes into the §5 table); add a README section and
   table row; update the node counts (README "The twenty-two nodes", `pyproject.toml` description, FORMAT.md
   §6 opening); add an annotated workflow in `examples/` plus a row in `examples/README.md`; add
   `tests/test_<name>.py` (+ `_js.py`); bump (section 8).

### A route
1. Library-folder routes: `lora_library/routes_<feature>.py` with `register(context, routes)`, its name added
   to the tuple in `routes._register_all` (as `routes_sets`); or expose `register_live(context)` and call it
   from `__init__.py` in its own `try` (as `routes_universal_states`). Context-free routes:
   `register_routes(routes)`, `build_routes()` (the tests' entry) and `register()`, wired the same way
   (`eps_image/routes_checkpoint_switcher.py`).
2. Register on `PromptServer.instance.routes`, imported lazily inside `register()`. Never `app.add_routes`:
   ComfyUI mirrors that table under `/api`, and a route outside it 405s from the frontend.
3. Prefixes: `/lora_library/`, `/eps_image_grid/`, `/eps_frame_saver/`, `/eps_ckpt/`, `/eps_resolution/`, and
   `/eps/` for cross-cutting feeds (`list_flags`, `state_registry`, `audit`). Paths are frozen once shipped
   (§8); list each in the §5 table.
4. Guards (§2): a route that takes an arbitrary path or drives the server's desktop calls
   `request_is_loopback(request)` (`lora_library/routes.py`) and returns `error_response(403, ...)`; a path
   inside the library goes through `notebook_path_error`; a route that only echoes what `/object_info` already
   exposes has no gate and says why in its module docstring. `eps_image/routes_frame_saver.py` keeps a local
   copy of the loopback guard: change both together.
5. Do filesystem, parse and hash work in `await asyncio.to_thread(fn, ...)`: a NAS read can block for the SMB
   timeout (§1 "Library on a NAS"). `request_is_loopback` stays on the loop. Errors are `{"error": msg}` with
   a 4xx; writes take `base_mtime` and answer 409 `{error, mtime}` on conflict. Test in
   `tests/test_routes_<feature>.py` (`build_routes(...)` into an aiohttp app).

### A panel control
1. DOM widget: `node.addDOMWidget(NAME, TYPE, element, {serialize: false, getMinHeight})`, then
   `domWidget.serialize = false` and `domWidget.serializeValue = () => undefined`. The flags differ:
   `options.serialize` gates the API prompt, `widget.serialize` gates the workflow file; buttons need both
   (§6.5). Reference: `attachDomWidget` in `notebook.js`.
2. Where state goes (§7.9): what Python reads at run time is a real hidden STRING widget declared in
   `INPUT_TYPES`; view-only state is a node property; a plain `state` field holds only what a rebuild may
   reset. Hide a plumbing widget with `widget.hidden = true` and
   `widget.options = {...widget.options, hidden: true}`, since canvas and Vue read different flags
   (`hideTogglesWidget` in `switcher.js`).
3. Tail-only serialized widgets: `widgets_values` restores by index, so a serialized widget the frontend adds
   goes after every existing one (tail rule: §6.1, §6.10; §8 freezes ids and paths). A `serialize: false`
   widget placed earlier leaves a hole that shifts later values on reload; the one sanctioned exception
   carries an `onSerialize` compaction (§6.5, `copy from image`).
4. Re-render rule: `attach()` reruns on tab switch, undo and reload and rebuilds `state`. A repaint reads from
   the persisted widget/property and never clears it; only a user action clears (§6.1 draft rule, §7.9).
   Handle both orders of the restore race; never let a late response overwrite a field being typed in (§7.5).
   In Vue mode `onDrawForeground` / `onMouseDown` never run, and window-level listeners must be capture-phase
   (§7.5).
5. Shared-panel caveat: helpers such as `el`, `installMinWidth`, `panelScale` and the group/category helpers
   are copied by hand between panels (the roadmap's census counted 119 source comments saying so), and fixes
   have missed siblings (`docs/ROADMAP-shared-panel-code.md`). After fixing a helper, grep `web/` for its name
   and fix every copy. Already shared: `lora_library/api.js`, `search.js`, `path_heal.js`,
   `eps_image/owned_link_colour.js`.

## 8. Conventions that bite

- **Versions.** `python scripts/bump_version.py [--minor|--major] [--dry-run]` is the only way to bump: it
  rewrites `pyproject.toml`, `lora_library/version.py` and `web/lora_library/version.js` together and refuses
  if they have drifted. It does not commit, tag or push. Ship = bump + commit + tag `vX.Y.Z` + push; docs-only
  changes do not bump (§8). A backend/frontend mismatch in Settings means a stale server or tab.
- **Concurrent sessions push straight to `main`.** `git fetch` and re-check before you bump or tag, and stage
  only your own files (a bump touches three files other sessions also touch).
- **Windows and Linux paths.** A path absolute in the other OS's syntax is detected by
  `context.is_foreign_absolute` and healed by `heal_foreign_absolute` at read time (§1); library-internal
  paths are stored relative (`relativize_library_path`). Model combo values are healed on load by
  `path_heal.js` (§7.6, setting `EPSNodes.HealModelPaths`). Never call `os.path.relpath` against ComfyUI dirs;
  atomic writers already handle gvfs/FUSE (§1).
- **LAN latency is a test condition.** Races invisible on loopback open up across machines: before declaring a
  frontend race unreproducible, inject latency on both `fetch` (e.g. 600 ms on `/lora_library/*`) and image
  loads (§7.5, §7.9, §6.6). Two browsers on one server can also differ in node-design mode; check that first
  for works-here-not-there reports.
- **One install.** With two clones in `custom_nodes`, the first to load wins the UI
  (`_warn_on_duplicate_installs`).
