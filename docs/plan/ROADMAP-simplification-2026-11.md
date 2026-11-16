# Simplification audit, November 2026

An audit of this pack for code that can be removed or simplified without
changing behaviour, and for copied logic that has drifted. It ran at v0.110.14.
Each item lists its evidence so it can be re-checked. The shared-panel-code
work it extends is tracked in `ROADMAP-shared-panel-code.md`.

Status words: **Shipped** (in `main`), **Open** (agreed, not built),
**Owner decision** (a choice, not a defect), **Leave** (looked at, kept on
purpose).

## Shipped

| Item | Evidence | Version |
| --- | --- | --- |
| Dead bridge finders removed: `findPllNodes`, `findDasiwaNodes`, and the null-node leg of `probePll` | Only declarations and tests referenced them; the leg was commented unreachable since v0.68.1 | v0.110.17 |
| `_notebook_token` and `InvalidEntryTextError` removed | Zero references anywhere, tests included | v0.110.17 |
| Prompt Builder imports its helpers from `nodes_notebook.py` | Six functions and a parse cache were re-declared; all identical by `ast.dump`. 4,608 fingerprint and build calls compared before and after: 0 differences | v0.110.17 |
| One `nodeClassOf`, `findWidget`, `linkById` in `web/lora_library/panel_helpers.js` | 10, 11 and 7 hand copies, each set with one distinct body | v0.110.17 |
| One `LINK_COLOR_OWNER_KEY` in `owned_link_colour.js` | Declared in six files | v0.110.17 |
| A test fails if a panel re-declares any of these | `tests/test_panel_helpers_js.py` | v0.110.17 |
| Image Grid no longer recolours a link another node has claimed | `reconcileLinkDimming` in `image_grid.js` overwrote the colour and owner tag of any link it did not own; the three sibling implementations skip links another owner holds. No graph that triggers it is known today: each owner only tags its own output links | v0.110.18 |

## Open, low risk

- **`controller.js` copies of bridge functions.** `scanLoraRows` and
  `isRgthreeInstalled` are byte-identical to the ones in `pll_bridge.js`,
  which `controller.js` does not import. About 18 lines.
- **Distributor / Number Controller / Switcher pairs.** `outputEntries`,
  `highestWiredSlot`, `outputIndexByName`, `linkColorFor`, `drawToggleBox`,
  `wireRowToggleDrawing`, `wireRowToggleClicks`, `setRowEnabled` are identical
  in two or three files. About 120 lines. The drawing ones can only be
  confirmed on the canvas by eye.
- **Module-level controller helpers (M4, first step).** `normalizeLayoutClient`,
  `parseRenameDraft`, `parseCollapsedGroups`, `categoryOfSlug`, `nameNodeDef`
  are identical in `controller.js` and `universal_controller.js`. About 65
  lines.
- **Store helpers.** `normalize_layout`, `save_layout`, `layout_path`,
  `clear_caches`, `_listing_sort_key`, `_copy_summaries` are identical in
  `sets_store.py` and `universal_states_store.py`. About 60 lines.
- **Loopback guard.** `request_is_loopback` and `_machine_owns_address` exist
  in `lora_library/routes.py` and `eps_image/routes_frame_saver.py` with
  identical code; the second copy's own docstring records that it once missed
  a fix. `eps_image` already imports from `lora_library.context` elsewhere.
  About 85 lines. It is a security gate, and two tests patch the copy by name.
- **Zero-caller exports.** `switcherEnabledCount` (`cross_sweep.js`),
  `isSwitcherAllOffWithWiredRows` (`switcher.js`), `readIncomingImageSize`
  (`resolution.js`).

## Owner decision

- **Link dimming, four copies.** `reconcileLinkDimming` and its siblings
  (`outputLinkIds`, `installLinkDimConnectionsSync`, `installLinkDimCleanup`)
  differ only in owner constant, tracking property and predicate. One
  parameterised function in `owned_link_colour.js` would save about 200 lines.
  The result on the canvas needs the owner's eyes.
- **The two state controllers.** 22 methods are byte-identical between the
  controller classes and 9 more are over 80% alike. Sharing them needs a base
  class, which adds structure rather than removing it.
- **`injectStyles`.** Seven copies, but each closes over its own module's CSS
  and style-tag id, so they are not one function. Sharing needs a parameter
  and changed call sites.
- **`_fixLibraryDisplayName`.** A 10-second poll of Vue internals that only
  runs on frontends without `beforeRegisterVueAppNodeDefs`. The pack declares
  no minimum frontend version, so removing it is a support decision.
- **`GET /lora_library/loras`.** No caller in `web/`, tests or examples;
  documented in `FORMAT.md`.
- **Old-name wrappers.** `solo_widget_index`, `_multiplier_widget_defaults`,
  `bake_solo` in `nodes_save_image.py` are used only by tests.
- **`relativize_library_path`.** Called only by a test; `FORMAT.md` §2 names it
  as the forward fix.
- **Listing timeouts.** The two bridge packs bound folder listings at 5 s;
  this pack does not.

## Leave

- `_replace_over_existing` exists in both `context.py` and
  `image_grid_store.py`; the shared-panel roadmap documents why.
- `migrateLegacyMirrorsValue` and the other saved-workflow migrations: saved
  workflows outlive the code.
- About 200 JS exports with no outside caller are exported for tests.

## Tests that pin source text

Roughly 36 tests assert that a function declaration exists in a source file
and 83 assert a `.count()` of some string. They pass or fail on how the code is
written, not on what it does, and each de-duplication above has to rewrite a
few. When one is touched, prefer replacing it with a check of behaviour.
