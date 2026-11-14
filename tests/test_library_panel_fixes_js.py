"""Behaviour tests for four lora_library frontend defects (branch
fix/library-panel-drafts-and-errors).

notebook.js / picker.js keep their logic in non-exported closures, so each
test builds a served layout (the pattern of test_picker_js.py) in which the
REAL module text is copied with two mechanical edits: the functions under
test are exported, and the DOM-only repaint helpers get an early ``return``.
Every function that decides the outcome (applyRenameResult, setSelection,
pruneDraftsToSelection, syncDraftsWidget, toggleFavorite, ...) runs
unmodified. For universal_controller.js, whose method lives on a class built
inside registerControllerNode, the real ``_writeApplyPlan`` method body is
extracted and run in a fake node (test_universal_controller_js.py's seam).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = REPO_ROOT / "web" / "lora_library"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

NOTEBOOK_EXPORTS = (
    "applyRenameResult, setSelection, commitDraftForActiveEntry, flushDraftSync"
)
NOTEBOOK_NEUTERED = (
    "updateShareToggle",
    "updateSelectionHint",
    "updateModeHint",
    "renderList",
    "updateSaveButtonEnabled",
    "updateDeleteButtonEnabled",
    "setStatus",
    "clearConflict",
)
PICKER_NEUTERED = ("patchFavoriteChange", "renderBrowser", "invalidateInFlightLoad")


def _neuter(source: str, names: tuple[str, ...]) -> str:
    for name in names:
        source, n = re.subn(rf"(\nfunction {name}\([^)]*\) \{{\n)", r"\1  return\n", source)
        assert n == 1, f"function {name} not found exactly once"
    return source


def _layout(tmp_path_factory, label: str, module: str, transform) -> Path:
    layout = tmp_path_factory.mktemp(label)
    module_dir = layout / "extensions" / "comfyui-epsnodes" / "lora_library"
    module_dir.mkdir(parents=True)
    for sibling in LIB.glob("*.js"):  # the module's whole import closure
        shutil.copyfile(sibling, module_dir / sibling.name)
    (module_dir / "under_test.js").write_text(
        transform((LIB / module).read_text(encoding="utf-8")), encoding="utf-8"
    )
    scripts = layout / "scripts"
    scripts.mkdir()
    (scripts / "api.js").write_text(
        "export const api = { fetchApi: (...a) => globalThis.__fetch(...a),"
        " apiURL: (p) => p, addEventListener() {} }\n",
        encoding="utf-8",
    )
    (scripts / "app.js").write_text(
        "export const app = { graph: null, registerExtension() {},"
        " extensionManager: { toast: { add(m) { globalThis.__toasts.push(m) } } },"
        " ui: { settings: { addSetting() {}, getSettingValue() { return true } } } }\n",
        encoding="utf-8",
    )
    return layout


def _run(layout: Path, body: str) -> dict:
    probe = layout / "probe.mjs"
    probe.write_text(
        "globalThis.window = globalThis\n"
        "globalThis.document = {hidden:false, activeElement:null,"
        " addEventListener(){}, removeEventListener(){}}\n"
        "globalThis.__toasts = []\n"
        "const m = await import('./extensions/comfyui-epsnodes/lora_library/under_test.js')\n"
        + body,
        encoding="utf-8",
    )
    result = subprocess.run(
        [NODE, str(probe)], capture_output=True, encoding="utf-8", timeout=60, cwd=layout
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def notebook_layout(tmp_path_factory) -> Path:
    def transform(src: str) -> str:
        return _neuter(src, NOTEBOOK_NEUTERED) + f"\nexport {{ {NOTEBOOK_EXPORTS} }}\n"

    return _layout(tmp_path_factory, "nb_fixes", "notebook.js", transform)


@pytest.fixture(scope="module")
def picker_layout(tmp_path_factory) -> Path:
    def transform(src: str) -> str:
        return _neuter(src, PICKER_NEUTERED) + "\nexport { toggleFavorite }\n"

    return _layout(tmp_path_factory, "pk_fixes", "picker.js", transform)


_NOTEBOOK_STATE = """
const widget = (v) => ({ value: v, callback() {} })
const state = {
  node: { graph: { setDirtyCanvas() {} }, properties: {} },
  entryWidget: widget(''), draftsWidget: widget('{}'),
  entries: [{ name: 'A' }, { name: 'Z' }], selection: ['A'], activeName: 'A',
  activeCategory: null, drafts: {}, draftSyncTimer: null,
  textarea: { value: 'disk text' }, nameFieldEl: { value: 'A' },
  lastSavedText: 'disk text', lastSavedName: 'A',
  entryTextByName: { A: 'disk text', Z: 'z' }, searchCorpus: new Map(),
  categoryDescriptionByName: {}, categories: [], collapsedCategories: new Set(),
  paintedMtime: 1, pinnedWidget: null, file: 'f.md', loadError: null
}
"""


def test_renaming_the_active_dirty_entry_keeps_its_persisted_draft(notebook_layout: Path) -> None:
    out = _run(
        notebook_layout,
        _NOTEBOOK_STATE
        + """
state.textarea.value = 'UNSAVED EDIT'
m.commitDraftForActiveEntry(state)
state.entries = [{ name: 'B' }, { name: 'Z' }]
m.applyRenameResult(state, 'entry', 'A', 'B', { entries: state.entries, mtime: 2 })
// what a tab switch would restore from: the persisted widget, not the textarea
process.stdout.write(JSON.stringify({
  persisted: JSON.parse(state.draftsWidget.value), active: state.activeName
}))
""",
    )
    assert out == {"persisted": {"B": "UNSAVED EDIT"}, "active": "B"}


def test_rename_flushes_a_still_debouncing_edit_under_the_new_name(notebook_layout: Path) -> None:
    """Typed, rename lands before the 400 ms debounce fired: the NEWEST text
    must end up under the new name, not a stale or missing one."""
    out = _run(
        notebook_layout,
        _NOTEBOOK_STATE
        + """
state.textarea.value = 'first'
m.commitDraftForActiveEntry(state)
state.textarea.value = 'second, still debouncing'
state.draftSyncTimer = setTimeout(() => {}, 100000)
state.entries = [{ name: 'B' }, { name: 'Z' }]
m.applyRenameResult(state, 'entry', 'A', 'B', { entries: state.entries, mtime: 2 })
clearTimeout(state.draftSyncTimer)
process.stdout.write(JSON.stringify({ persisted: JSON.parse(state.draftsWidget.value) }))
""",
    )
    assert out == {"persisted": {"B": "second, still debouncing"}}


def test_renaming_a_category_moves_its_unsaved_description_draft(notebook_layout: Path) -> None:
    out = _run(
        notebook_layout,
        _NOTEBOOK_STATE
        + """
Object.assign(state, {
  entries: [], selection: [], activeName: null, activeCategory: 'Cats',
  nameFieldEl: { value: 'Cats' }, lastSavedName: 'Cats',
  categories: ['Cats'], categoryDescriptionByName: { Cats: 'disk text' }
})
state.textarea.value = 'UNSAVED DESCRIPTION'
m.commitDraftForActiveEntry(state)
m.applyRenameResult(state, 'category', 'Cats', 'Dogs', { categories: ['Dogs'], mtime: 2 })
process.stdout.write(JSON.stringify({
  persisted: JSON.parse(state.draftsWidget.value),
  newKey: m.categoryDraftKey('Dogs'), oldKey: m.categoryDraftKey('Cats'),
  forNewName: m.draftTextFor(state, 'Dogs'), forOldName: m.draftTextFor(state, 'Cats')
}))
""",
    )
    assert out["persisted"] == {out["newKey"]: "UNSAVED DESCRIPTION"}
    assert out["oldKey"] not in out["persisted"]
    assert out["forNewName"] == "UNSAVED DESCRIPTION"
    assert out["forOldName"] is None


def test_renaming_a_clean_entry_leaves_drafts_untouched(notebook_layout: Path) -> None:
    out = _run(
        notebook_layout,
        _NOTEBOOK_STATE
        + """
state.entries = [{ name: 'B' }, { name: 'Z' }]
m.applyRenameResult(state, 'entry', 'A', 'B', { entries: state.entries, mtime: 2 })
process.stdout.write(JSON.stringify({ persisted: state.draftsWidget.value, active: state.activeName }))
""",
    )
    assert out == {"persisted": "{}", "active": "B"}


_FAV_PROBE = """
const resp = (ok, body) => ({ ok, status: ok ? 200 : 500, json: async () => body })
const pending = []
globalThis.__fetch = (path, opts) =>
  new Promise((res) => pending.push({ path, file: JSON.parse(opts.body).file, res }))
const state = { favorites: [], favoriteToken: 0 }
"""


def test_a_failed_star_is_toasted_and_reverted_even_when_a_newer_click_is_in_flight(
    picker_layout: Path,
) -> None:
    out = _run(
        picker_layout,
        _FAV_PROBE
        + """
const a = m.toggleFavorite(state, 'a.safetensors', true)
const b = m.toggleFavorite(state, 'b.safetensors', true)
const optimistic = state.favorites.slice()
pending[0].res(resp(false, { error: 'disk full' }))
pending[1].res(resp(true, { favorites: ['b.safetensors'] }))
await a; await b
process.stdout.write(JSON.stringify({
  optimistic, final: state.favorites, toasts: globalThis.__toasts.map((t) => t.severity)
}))
""",
    )
    assert out["optimistic"] == ["a.safetensors", "b.safetensors"]
    assert out["final"] == ["b.safetensors"]
    assert out["toasts"] == ["error"]


def test_an_older_failure_arriving_after_the_newer_success_still_toasts(
    picker_layout: Path,
) -> None:
    out = _run(
        picker_layout,
        _FAV_PROBE
        + """
const a = m.toggleFavorite(state, 'a.safetensors', true)
const b = m.toggleFavorite(state, 'b.safetensors', true)
pending[1].res(resp(true, { favorites: ['a.safetensors', 'b.safetensors'] }))
await b
pending[0].res(resp(false, { error: 'disk full' }))
await a
process.stdout.write(JSON.stringify({ final: state.favorites, toasts: globalThis.__toasts.length }))
""",
    )
    assert out["toasts"] == 1
    assert out["final"] == ["b.safetensors"]  # A's failure undid only A


def test_a_failed_unstar_with_a_newer_click_restores_that_star_and_keeps_the_new_one(
    picker_layout: Path,
) -> None:
    out = _run(
        picker_layout,
        _FAV_PROBE
        + """
state.favorites = ['a.safetensors']
const a = m.toggleFavorite(state, 'a.safetensors', false)
const b = m.toggleFavorite(state, 'b.safetensors', true)
pending[0].res(resp(false, { error: 'nope' }))
await a
process.stdout.write(JSON.stringify({ final: state.favorites.slice().sort(), toasts: globalThis.__toasts.length }))
""",
    )
    assert out == {"final": ["a.safetensors", "b.safetensors"], "toasts": 1}


def test_a_lone_failed_star_reverts_and_toasts(picker_layout: Path) -> None:
    out = _run(
        picker_layout,
        _FAV_PROBE
        + """
state.favorites = ['x.safetensors']
const a = m.toggleFavorite(state, 'a.safetensors', true)
pending[0].res(resp(false, { error: 'boom' }))
await a
process.stdout.write(JSON.stringify({ final: state.favorites, toasts: globalThis.__toasts.length }))
""",
    )
    assert out == {"final": ["x.safetensors"], "toasts": 1}


# ------------------------------------------------- universal _writeApplyPlan


def _method_body(source: str, signature: str) -> str:
    head = f"      {signature} {{\n"
    start = source.index(head) + len(head)
    end = re.search(r"\n      \}\n", source[start:])
    assert end, f"{signature}: closing brace not found"
    return source[start : start + end.start()]


def test_apply_survives_a_throwing_widget_callback_and_still_announces(tmp_path: Path) -> None:
    """Seam: the real `_writeApplyPlan` method text inside a fake node (the
    class lives inside registerControllerNode, so there is no import seam)."""
    body = _method_body(
        (LIB / "universal_controller.js").read_text(encoding="utf-8"),
        "_writeApplyPlan(plan, discovered)",
    )
    script = (
        "const NODE_TITLE = 'T'\n"
        "const WARNS = [], ANNOUNCED = []\n"
        "const api = { warn: (...a) => WARNS.push(a.length),"
        " announceWidgetsChangedExternally: (e) => ANNOUNCED.push(e) }\n"
        "const app = { canvas: {} }\n"
        "class N {\n  constructor() { this.dirty = 0 }\n"
        "  setDirtyCanvas() { this.dirty++ }\n"
        f"  _writeApplyPlan(plan, discovered) {{\n{body}\n  }}\n}}\n"
        """
const mk = (throws) => ({ widgets: [{ name: 'w', value: 0,
  callback() { if (throws) throw new Error('third-party widget exploded') } }] })
const n1 = mk(true), n2 = mk(false)
const plan = { matched: [
  { id: 'p1', class: 'C', writes: [{ name: 'w', value: 7 }] },
  { id: 'p2', class: 'C', writes: [{ name: 'w', value: 9 }] } ] }
const node = new N()
node._writeApplyPlan(plan, [{ pathId: 'p1', node: n1 }, { pathId: 'p2', node: n2 }])
process.stdout.write(JSON.stringify({
  v1: n1.widgets[0].value, v2: n2.widgets[0].value, dirty: node.dirty,
  announced: ANNOUNCED.map((l) => l.map((e) => e.pathId)), warned: WARNS.length
}))
"""
    )
    probe = tmp_path / "probe.mjs"
    probe.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(probe)], capture_output=True, encoding="utf-8", timeout=60, cwd=tmp_path
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    out = json.loads(result.stdout)
    assert out == {"v1": 7, "v2": 9, "dirty": 1, "announced": [["p1", "p2"]], "warned": 1}
