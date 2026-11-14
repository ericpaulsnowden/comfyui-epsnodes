"""Behaviour tests for two notebook.js panel/cache defects that followed the
store changes on fix/notebook-store-reload-safe-edits (duplicate-name
survivors become addressable; the server echoes STORED text with closed
fences).

notebook.js keeps its logic in non-exported closures, so the test builds a
served layout (test_picker_js.py's pattern) holding the REAL module text with
two mechanical edits: the functions under test are exported and the DOM-only
repaint helpers get an early ``return``. Everything that decides the outcome
(performDeleteRun, syncNotebookCache, applyRenameResult, foldStoredBody,
renameEntryRequest, ...) runs unmodified.
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

EXPORTS = (
    "performDeleteRun, applyRenameResult, renameEntryRequest, renameCategoryRequest, "
    "hasCachedEntryText"
)
NEUTERED = (
    "updateShareToggle",
    "updateSelectionHint",
    "updateModeHint",
    "renderList",
    "updateSaveButtonEnabled",
    "updateDeleteButtonEnabled",
    "setStatus",
    "clearConflict",
    "resetEditorDom",
)


def _neuter(source: str, names: tuple[str, ...]) -> str:
    for name in names:
        source, n = re.subn(rf"(\nfunction {name}\([^)]*\) \{{\n)", r"\1  return\n", source)
        assert n == 1, f"function {name} not found exactly once"
    return source


@pytest.fixture(scope="module")
def layout(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("nb_cache")
    module_dir = root / "extensions" / "comfyui-epsnodes" / "lora_library"
    module_dir.mkdir(parents=True)
    for sibling in LIB.glob("*.js"):
        shutil.copyfile(sibling, module_dir / sibling.name)
    src = _neuter((LIB / "notebook.js").read_text(encoding="utf-8"), NEUTERED)
    (module_dir / "under_test.js").write_text(
        src + f"\nexport {{ {EXPORTS} }}\n", encoding="utf-8"
    )
    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "api.js").write_text(
        "export const api = { fetchApi: (...a) => globalThis.__fetch(...a),"
        " apiURL: (p) => p, addEventListener() {} }\n",
        encoding="utf-8",
    )
    (scripts / "app.js").write_text(
        "export const app = { graph: null, registerExtension() {}, extensionManager: {},"
        " ui: { settings: { addSetting() {}, getSettingValue() { return true } } } }\n",
        encoding="utf-8",
    )
    return root


_STATE = """
const resp = (body) => ({ ok: true, status: 200, json: async () => body })
globalThis.__requests = []
const widget = (v) => ({ value: v, callback() {} })
const state = {
  node: { graph: { setDirtyCanvas() {} }, properties: {} },
  entryWidget: widget(''), draftsWidget: widget('{}'),
  entries: [], selection: [], activeName: null, activeCategory: null,
  drafts: {}, draftSyncTimer: null, busy: false,
  textarea: { value: '' }, nameFieldEl: { value: '' },
  lastSavedText: '', lastSavedName: '', baseMtime: 1, paintedMtime: 1,
  entryTextByName: {}, searchCorpus: new Map(), categoryDescriptionByName: {},
  categories: [], collapsedCategories: new Set(), pinnedWidget: null,
  file: 'cache_test.md', exists: true, loadError: null
}
"""


def _run(layout: Path, body: str) -> dict:
    probe = layout / "probe.mjs"
    probe.write_text(
        "globalThis.window = globalThis\n"
        "globalThis.document = {hidden:false, activeElement:null,"
        " addEventListener(){}, removeEventListener(){}}\n"
        "const m = await import('./extensions/comfyui-epsnodes/lora_library/under_test.js')\n"
        + _STATE
        + body,
        encoding="utf-8",
    )
    result = subprocess.run(
        [NODE, str(probe)], capture_output=True, encoding="utf-8", timeout=60, cwd=layout
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


# -------------------------------------------- 1. unknown text is never ''


def test_delete_of_one_duplicate_leaves_the_survivor_text_unknown_not_empty(layout: Path) -> None:
    """"## dup\\nfirst\\n## dup\\nsecond\\n": deleting the visible dup makes the
    survivor addressable; the panel never received its body, so the cache must
    not record it as ''."""
    out = _run(
        layout,
        """
state.entries = [{ name: 'dup' }, { name: 'dup' }]
state.selection = ['dup']; state.activeName = 'dup'
state.entryTextByName = { dup: 'first' }
globalThis.__fetch = async (path, opts) => {
  globalThis.__requests.push(path)
  return resp({ ok: true, entries: [{ name: 'dup' }], categories: [], mtime: 5 })
}
await m.performDeleteRun(state, ['dup'])
const cached = m.notebookCacheGet('cache_test.md')
process.stdout.write(JSON.stringify({
  cachedEntries: cached.payload.entries,
  known: m.hasCachedEntryText(state, 'dup')
}))
""",
    )
    assert out["cachedEntries"] == [{"name": "dup"}]  # no `text` key at all
    assert out["known"] is False


def test_known_bodies_still_ride_the_cache(layout: Path) -> None:
    out = _run(
        layout,
        """
state.entries = [{ name: 'a' }, { name: 'b' }]
state.selection = ['a']; state.activeName = 'a'
state.entryTextByName = { a: 'A body', b: '' }
globalThis.__fetch = async () => resp({ ok: true, entries: [{ name: 'b' }], categories: [], mtime: 5 })
await m.performDeleteRun(state, ['a'])
process.stdout.write(JSON.stringify({ e: m.notebookCacheGet('cache_test.md').payload.entries }))
""",
    )
    assert out["e"] == [{"name": "b", "text": ""}]  # a KNOWN empty body is kept


def test_renaming_an_entry_with_unknown_body_reads_it_instead_of_sending_empty(
    layout: Path,
) -> None:
    out = _run(
        layout,
        """
state.entries = [{ name: 'dup' }]
globalThis.__fetch = async (path, opts) => {
  globalThis.__requests.push({ path, body: opts?.body ? JSON.parse(opts.body) : null })
  if (!opts) return resp({ ok: true, name: 'dup', text: 'second', mtime: 1 })
  return resp({ ok: true, entries: [{ name: 'x' }], mtime: 2 })
}
await m.renameEntryRequest(state, 'dup', 'x', false)
process.stdout.write(JSON.stringify({ sent: globalThis.__requests.at(-1).body.text }))
""",
    )
    assert out["sent"] == "second"


# ---------------------------------- 2. rename adopts the server's stored text


def test_entry_rename_adopts_the_stored_text_with_the_closing_fence(layout: Path) -> None:
    out = _run(
        layout,
        """
const open = '```python\\nx\\n'
const closed = open + '```\\n'
Object.assign(state, {
  entries: [{ name: 'A' }], selection: ['A'], activeName: 'A',
  entryTextByName: { A: open }, textarea: { value: open }, lastSavedText: open,
  nameFieldEl: { value: 'A' }, lastSavedName: 'A'
})
m.applyRenameResult(state, 'entry', 'A', 'B',
  { entries: [{ name: 'B' }], mtime: 2, text: closed, closed_fences: 1 })
process.stdout.write(JSON.stringify({
  cacheB: state.entryTextByName.B, hasA: 'A' in state.entryTextByName,
  textarea: state.textarea.value, baseline: state.lastSavedText,
  cached: m.notebookCacheGet('cache_test.md').payload.entries,
}))
""",
    )
    closed = "```python\nx\n```\n"
    assert out["cacheB"] == closed and out["hasA"] is False
    assert out["textarea"] == closed and out["baseline"] == closed
    assert out["cached"] == [{"name": "B", "text": closed}]


def test_rename_never_overwrites_typing_in_the_open_entry(layout: Path) -> None:
    """The textarea is replaced only while it still shows what was sent; typed
    edits survive and the baseline becomes disk truth."""
    out = _run(
        layout,
        """
Object.assign(state, {
  entries: [{ name: 'A' }], selection: ['A'], activeName: 'A',
  entryTextByName: { A: 'old' }, textarea: { value: 'old, plus typing' }, lastSavedText: 'old',
  nameFieldEl: { value: 'A' }, lastSavedName: 'A'
})
m.applyRenameResult(state, 'entry', 'A', 'B', { entries: [{ name: 'B' }], mtime: 2, text: 'old\\n' })
process.stdout.write(JSON.stringify({ textarea: state.textarea.value, baseline: state.lastSavedText, cacheB: state.entryTextByName.B }))
""",
    )
    assert out == {"textarea": "old, plus typing", "baseline": "old\n", "cacheB": "old\n"}


def test_rename_without_a_stored_text_keeps_the_moved_body(layout: Path) -> None:
    out = _run(
        layout,
        """
Object.assign(state, {
  entries: [{ name: 'A' }], selection: ['A'], activeName: 'A',
  entryTextByName: { A: 'body' }, textarea: { value: 'body' }, lastSavedText: 'body',
  nameFieldEl: { value: 'A' }, lastSavedName: 'A'
})
m.applyRenameResult(state, 'entry', 'A', 'B', { entries: [{ name: 'B' }], mtime: 2 })
process.stdout.write(JSON.stringify({ cacheB: state.entryTextByName.B, baseline: state.lastSavedText }))
""",
    )
    assert out == {"cacheB": "body", "baseline": "body"}


def test_category_rename_adopts_the_stored_description(layout: Path) -> None:
    out = _run(
        layout,
        """
const open = '```\\nnotes\\n'
const closed = open + '```\\n'
Object.assign(state, {
  entries: [], categories: ['Cats'], activeCategory: 'Cats',
  categoryDescriptionByName: { Cats: open }, textarea: { value: open }, lastSavedText: open,
  nameFieldEl: { value: 'Cats' }, lastSavedName: 'Cats'
})
m.applyRenameResult(state, 'category', 'Cats', 'Dogs',
  { categories: ['Dogs'], mtime: 2, description: closed, closed_fences: 1 })
process.stdout.write(JSON.stringify({
  desc: state.categoryDescriptionByName, textarea: state.textarea.value, baseline: state.lastSavedText
}))
""",
    )
    closed = "```\nnotes\n```\n"
    assert out == {"desc": {"Dogs": closed}, "textarea": closed, "baseline": closed}
