"""Frontend tests for the EPS Prompt Builder panel
(``web/lora_library/prompt_builder.js`` -- companion to the EPS Prompt
Notebook, FORMAT.md §7.2-family conventions).

Two layers, this pack's established convention for a DOM/closure-bound
module (test_pll_bridge_js.py's / test_notebook_restore_js.py's fixture
shapes):

1. A Node probe (``PROBE_JS``) run against the REAL module in a served-
   layout tmp dir -- ``prompt_builder.js`` imports
   ``../../../scripts/app.js``, ``./api.js`` and ``./notebook.js``
   (-> ``../../../scripts/api.js``), so the layout mirrors that depth and
   stubs the two core scripts. Importing the module under Node is itself a
   regression test: nothing at its top level may touch the DOM/LiteGraph.
   Drives every PURE exported helper directly.
2. Source-text pins (``_body``/``_function_body``, this pack's shared
   helper shape) for the closure-bound attach/render/event-wiring code that
   has no pure seam to drive headlessly -- live DOM/LiteGraph behavior is
   for the rig, not here.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB = REPO_ROOT / "web"
PROMPT_BUILDER_JS = WEB / "lora_library" / "prompt_builder.js"
NOTEBOOK_JS = WEB / "lora_library" / "notebook.js"
API_JS = WEB / "lora_library" / "api.js"
VERSION_JS = WEB / "lora_library" / "version.js"
LORA_LIBRARY_JS = REPO_ROOT / "web" / "lora_library.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")


@pytest.fixture(scope="module")
def source() -> str:
    return PROMPT_BUILDER_JS.read_text(encoding="utf-8")


def _body(source_text: str, signature: str) -> str:
    """Body of a top-level ``function <signature> {`` (an `export`/`async`
    prefix is not part of *signature*) up to its column-0 closing brace --
    the sibling JS test files' identical helper."""
    head = f"function {signature} {{\n"
    assert head in source_text, f"{head!r} not found"
    start = source_text.index(head) + len(head)
    end = source_text.index("\n}\n", start)
    return source_text[start:end]


# ---------------------------------------------------------------------------
# node --check
# ---------------------------------------------------------------------------


def test_prompt_builder_js_parses() -> None:
    result = subprocess.run(
        [NODE, "--check", "--input-type=module"],
        input=PROMPT_BUILDER_JS.read_text(encoding="utf-8"),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_only_the_three_documented_imports(source: str) -> None:
    # 2026-09-01: the notebook.js import grew three more named pure helpers
    # (parseCollapsedSections/toggleCollapsedSection/isSectionCollapsed,
    # reused verbatim for the left column's collapsible category headers
    # instead of reinventing the same logic) -- still the same THREE source
    # files (app.js, api.js, notebook.js), so the assertion shape is
    # unchanged, only this one line's literal grew.
    imports = re.findall(r"^import .*$", source, flags=re.MULTILINE)
    assert imports == [
        "import { app } from '../../../scripts/app.js'",
        "import * as api from './api.js'",
        "import { walkLiveNodes } from './api.js'",
        "import { notebookCacheGet, notebookCacheSet, isUnchangedResponse, "
        "parseCollapsedSections, toggleCollapsedSection, isSectionCollapsed } "
        "from './notebook.js'",
    ]


# ---------------------------------------------------------------------------
# Pure helpers, driven for real under Node
# ---------------------------------------------------------------------------

PROBE_JS = """
import * as m from './extensions/comfyui-epsnodes/lora_library/prompt_builder.js'

const out = {
  exports: {
    CLASS_ID: m.CLASS_ID,
    hasAttach: typeof m.attachPromptBuilderPanel === 'function',
    hasParseBlocks: typeof m.parseBlocks === 'function',
    hasSerializeBlocks: typeof m.serializeBlocks === 'function',
    hasReorderBlocks: typeof m.reorderBlocks === 'function',
    hasAppendBlock: typeof m.appendBlock === 'function',
    hasRemoveBlockAt: typeof m.removeBlockAt === 'function',
    hasNotebookOptionsOf: typeof m.notebookOptionsOf === 'function',
    hasFilterEntries: typeof m.filterEntries === 'function',
    hasMissingBlockNames: typeof m.missingBlockNames === 'function',
    hasGroupEntriesByCategory: typeof m.groupEntriesByCategory === 'function',
    hasMirroredDraftsRaw: typeof m.mirroredDraftsRaw === 'function'
  }
}

// --------------------------------------------------------------- parseBlocks
out.parseBlocks = {
  normal: m.parseBlocks('["a","b"]'),
  empty: m.parseBlocks(''),
  nul: m.parseBlocks(null),
  undef: m.parseBlocks(undefined),
  notJson: m.parseBlocks('not json'),
  notArray: m.parseBlocks('{"a":1}'),
  mixedMembers: m.parseBlocks('["a",1,null,"b",{}]'),
  emptyArray: m.parseBlocks('[]'),
  dedupesRepeats: m.parseBlocks('["a","b","a","c","b"]')
}

// ----------------------------------------------------------- serializeBlocks
out.serializeBlocks = {
  normal: m.serializeBlocks(['a', 'b']),
  empty: m.serializeBlocks([]),
  nonArray: m.serializeBlocks(null),
  dropsNonStrings: m.serializeBlocks(['a', 1, null, 'b'])
}

// -------------------------------------------------------------- reorderBlocks
out.reorderBlocks = {
  moveForward: m.reorderBlocks(['a', 'b', 'c', 'd'], 0, 2),
  moveBackward: m.reorderBlocks(['a', 'b', 'c', 'd'], 3, 0),
  noop: m.reorderBlocks(['a', 'b', 'c'], 1, 1),
  emptyList: m.reorderBlocks([], 0, 0),
  fromClampedNegative: m.reorderBlocks(['a', 'b', 'c'], -5, 0),
  fromClampedOverflow: m.reorderBlocks(['a', 'b', 'c'], 99, 0),
  toClampedOverflow: m.reorderBlocks(['a', 'b', 'c'], 0, 99),
  toClampedNegative: m.reorderBlocks(['a', 'b', 'c'], 2, -5),
  nonArrayList: m.reorderBlocks(null, 0, 0),
  neverMutatesInput: (() => {
    const original = ['a', 'b', 'c']
    const result = m.reorderBlocks(original, 0, 2)
    return { original, result, sameRef: original === result }
  })()
}

// --------------------------------------------------------------- appendBlock
out.appendBlock = {
  toEmpty: m.appendBlock([], 'x'),
  toExisting: m.appendBlock(['a'], 'b'),
  duplicateRejected: m.appendBlock(['a'], 'a'),
  duplicateRejectedMidList: m.appendBlock(['a', 'b', 'c'], 'b'),
  nonStringIgnored: m.appendBlock(['a'], 42),
  nonArrayList: m.appendBlock(null, 'a'),
  neverMutatesInput: (() => {
    const original = ['a']
    const result = m.appendBlock(original, 'b')
    return { original, result }
  })(),
  neverMutatesInputOnDuplicate: (() => {
    const original = ['a', 'b']
    const result = m.appendBlock(original, 'a')
    return { original, result, sameRef: original === result }
  })()
}

// ------------------------------------------------------------- removeBlockAt
out.removeBlockAt = {
  middle: m.removeBlockAt(['a', 'b', 'c'], 1),
  first: m.removeBlockAt(['a', 'b', 'c'], 0),
  last: m.removeBlockAt(['a', 'b', 'c'], 2),
  outOfRangeHigh: m.removeBlockAt(['a', 'b'], 5),
  outOfRangeNegative: m.removeBlockAt(['a', 'b'], -1),
  nonInteger: m.removeBlockAt(['a', 'b'], 1.5),
  nonArrayList: m.removeBlockAt(null, 0),
  neverMutatesInput: (() => {
    const original = ['a', 'b', 'c']
    const result = m.removeBlockAt(original, 1)
    return { original, result }
  })()
}

// --------------------------------------------------------- notebookOptionsOf
out.notebookOptionsOf = {
  basic: m.notebookOptionsOf([
    { title: 'My Notebook', file: '/lib/notebook.md' },
    { title: 'Windows Path', file: 'C:\\\\lib\\\\notes.md' }
  ]),
  dedupeKeepsFirstTitle: m.notebookOptionsOf([
    { title: 'First', file: 'a.md' },
    { title: 'Second', file: 'a.md' }
  ]),
  missingTitleFallsBack: m.notebookOptionsOf([{ title: '', file: 'b.md' }]),
  skipsNonStringFile: m.notebookOptionsOf([{ title: 'X', file: null }, null, { title: 'Y' }]),
  empty: m.notebookOptionsOf([]),
  nonArray: m.notebookOptionsOf(null)
}

// -------------------------------------------------------------- filterEntries
const entries = [
  { name: 'Foo', text: 'bar baz' },
  { name: 'Hello', text: 'World FOO' },
  { name: 'Nothing', text: 'unrelated' }
]
out.filterEntries = {
  emptyQueryReturnsAll: m.filterEntries(entries, ''),
  matchesNameCaseInsensitive: m.filterEntries(entries, 'foo').map((e) => e.name),
  matchesTextSubstring: m.filterEntries(entries, 'world').map((e) => e.name),
  noMatch: m.filterEntries(entries, 'zzz'),
  nonArrayEntries: m.filterEntries(null, 'x'),
  whitespaceQueryTrimmed: m.filterEntries(entries, '  foo  ').map((e) => e.name)
}

// ---------------------------------------------------------- missingBlockNames
out.missingBlockNames = {
  basic: m.missingBlockNames(['A', 'B', 'C'], ['A']),
  dedupesAndOrders: m.missingBlockNames(['B', 'A', 'B', 'C', 'A'], ['A']),
  allKnown: m.missingBlockNames(['A', 'B'], ['A', 'B']),
  emptyBlocks: m.missingBlockNames([], ['A']),
  nonArrayEntryNames: m.missingBlockNames(['A'], null)
}

// ----------------------------------------------------- groupEntriesByCategory
out.groupEntriesByCategory = {
  basic: m.groupEntriesByCategory([
    { name: 'One', category: '' },
    { name: 'Two', category: '' },
    { name: 'Three', category: 'Characters' },
    { name: 'Four', category: 'Characters' },
    { name: 'Five', category: 'Styles' }
  ]),
  allUngrouped: m.groupEntriesByCategory([
    { name: 'A' },
    { name: 'B', category: '' }
  ]),
  missingCategoryField: m.groupEntriesByCategory([{ name: 'A' }, { name: 'B' }]),
  nonStringCategory: m.groupEntriesByCategory([{ name: 'A', category: 42 }]),
  skipsNullAndNonObjectEntries: m.groupEntriesByCategory([
    null,
    { name: 'A', category: 'X' },
    'not-an-object'
  ]),
  nonArrayInput: m.groupEntriesByCategory(null),
  empty: m.groupEntriesByCategory([])
}

// ------------------------------------------------------------ mirroredDraftsRaw
out.mirroredDraftsRaw = {
  matchingCandidate: m.mirroredDraftsRaw(
    [
      { file: 'a.md', draftsRaw: '{"A":"edited"}' },
      { file: 'b.md', draftsRaw: '{"B":"other"}' }
    ],
    'a.md'
  ),
  firstMatchWins: m.mirroredDraftsRaw(
    [
      { file: 'a.md', draftsRaw: '{"first":true}' },
      { file: 'a.md', draftsRaw: '{"second":true}' }
    ],
    'a.md'
  ),
  noMatch: m.mirroredDraftsRaw([{ file: 'a.md', draftsRaw: '{"A":"x"}' }], 'z.md'),
  emptyFile: m.mirroredDraftsRaw([{ file: 'a.md', draftsRaw: '{"A":"x"}' }], ''),
  emptyCandidates: m.mirroredDraftsRaw([], 'a.md'),
  nonArrayCandidates: m.mirroredDraftsRaw(null, 'a.md'),
  candidateWithNullDraftsRaw: m.mirroredDraftsRaw(
    [{ file: 'a.md', draftsRaw: null }],
    'a.md'
  ),
  candidateMissingDraftsRaw: m.mirroredDraftsRaw([{ file: 'a.md' }], 'a.md')
}

process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def probe_api(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Runs PROBE_JS against the REAL prompt_builder.js in a served-layout
    tmp dir (test_pll_bridge_js.py's / test_notebook_restore_js.py's
    fixture shape). prompt_builder.js imports ../../../scripts/app.js,
    ./api.js, and ./notebook.js (which itself imports ./api.js), so all
    three real modules ride along and the two core scripts are stubbed."""
    layout = tmp_path_factory.mktemp("web_root")
    module_dir = layout / "extensions" / "comfyui-epsnodes" / "lora_library"
    module_dir.mkdir(parents=True)
    for src in (PROMPT_BUILDER_JS, NOTEBOOK_JS, API_JS, VERSION_JS):
        shutil.copyfile(src, module_dir / src.name)
    scripts = layout / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "api.js").write_text(
        "export const api = { fetchApi: () => {}, apiURL: (p) => p, addEventListener: () => {} }\n",
        encoding="utf-8",
    )
    (scripts / "app.js").write_text("export const app = {}\n", encoding="utf-8")
    probe = layout / "probe.mjs"
    probe.write_text(PROBE_JS, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(probe)], capture_output=True, text=True, timeout=60, cwd=layout
    )
    assert result.returncode == 0, f"probe failed (module must IMPORT under Node):\n{result.stderr}"
    return json.loads(result.stdout)


def test_module_exports_the_documented_surface(probe_api: dict) -> None:
    exports = probe_api["exports"]
    assert exports["CLASS_ID"] == "EPSPromptBuilder"
    for key, value in exports.items():
        if key == "CLASS_ID":
            continue
        assert value is True, key


def test_parse_blocks_is_tolerant_and_filters_non_strings(probe_api: dict) -> None:
    got = probe_api["parseBlocks"]
    assert got["normal"] == ["a", "b"]
    assert got["empty"] == []
    assert got["nul"] == []
    assert got["undef"] == []
    assert got["notJson"] == []
    assert got["notArray"] == []
    assert got["mixedMembers"] == ["a", "b"]
    assert got["emptyArray"] == []


def test_parse_blocks_dedupes_a_repeat_on_restore(probe_api: dict) -> None:
    """Owner ask 2026-09-01: a `blocks` value saved with a repeat (a hand
    edit, or a workflow from before the duplicate guard existed) must load
    back with the repeat silently dropped, never thrown on and never shown
    as two rows -- first occurrence wins, same as missingBlockNames()'s own
    dedupe."""
    got = probe_api["parseBlocks"]
    assert got["dedupesRepeats"] == ["a", "b", "c"]


def test_serialize_blocks_round_trips_and_drops_non_strings(probe_api: dict) -> None:
    got = probe_api["serializeBlocks"]
    assert got["normal"] == '["a","b"]'
    assert got["empty"] == "[]"
    assert got["nonArray"] == "[]"
    assert got["dropsNonStrings"] == '["a","b"]'


def test_reorder_blocks_moves_by_final_index_and_clamps(probe_api: dict) -> None:
    got = probe_api["reorderBlocks"]
    # a,b,c,d: move 'a' (0) to final index 2 -> b,c,a,d
    assert got["moveForward"] == ["b", "c", "a", "d"]
    # a,b,c,d: move 'd' (3) to final index 0 -> d,a,b,c
    assert got["moveBackward"] == ["d", "a", "b", "c"]
    assert got["noop"] == ["a", "b", "c"]
    assert got["emptyList"] == []
    assert got["nonArrayList"] == []
    # from clamped into [0, len-1]
    assert got["fromClampedNegative"] == ["a", "b", "c"]  # from -5 -> 0, to 0 -> noop
    assert got["fromClampedOverflow"] == ["c", "a", "b"]  # from 99 -> 2 ('c'), to 0 -> c,a,b
    assert got["toClampedOverflow"] == ["b", "c", "a"]  # to 99 clamped to len-1=2 (post-removal)
    assert got["toClampedNegative"] == ["c", "a", "b"]  # from 2 ('c'), to -5 -> 0


def test_reorder_blocks_never_mutates_its_input(probe_api: dict) -> None:
    got = probe_api["reorderBlocks"]["neverMutatesInput"]
    assert got["original"] == ["a", "b", "c"]
    assert got["sameRef"] is False


def test_append_block_pushes_to_the_end_and_rejects_duplicates(probe_api: dict) -> None:
    """Owner ask 2026-09-01: "you should not be able to add a prompt more
    than once" -- this REPLACES the old `duplicateAllowed` behavior (a
    second add used to push a second copy). appendBlock() is the single
    choke point every add goes through, so the guard lives here rather than
    only in the UI's dblclick gate (renderLeftPane's own doc)."""
    got = probe_api["appendBlock"]
    assert got["toEmpty"] == ["x"]
    assert got["toExisting"] == ["a", "b"]
    assert got["duplicateRejected"] == ["a"]
    assert got["duplicateRejectedMidList"] == ["a", "b", "c"]
    assert got["nonStringIgnored"] == ["a"]
    assert got["nonArrayList"] == ["a"]
    assert got["neverMutatesInput"]["original"] == ["a"]
    assert got["neverMutatesInput"]["result"] == ["a", "b"]
    assert got["neverMutatesInputOnDuplicate"]["original"] == ["a", "b"]
    assert got["neverMutatesInputOnDuplicate"]["result"] == ["a", "b"]
    assert got["neverMutatesInputOnDuplicate"]["sameRef"] is False


def test_remove_block_at_splices_only_the_given_index(probe_api: dict) -> None:
    got = probe_api["removeBlockAt"]
    assert got["middle"] == ["a", "c"]
    assert got["first"] == ["b", "c"]
    assert got["last"] == ["a", "b"]
    assert got["outOfRangeHigh"] == ["a", "b"]
    assert got["outOfRangeNegative"] == ["a", "b"]
    assert got["nonInteger"] == ["a", "b"]
    assert got["nonArrayList"] == []
    assert got["neverMutatesInput"]["original"] == ["a", "b", "c"]
    assert got["neverMutatesInput"]["result"] == ["a", "c"]


def test_notebook_options_of_labels_dedupes_and_skips_bad_rows(probe_api: dict) -> None:
    got = probe_api["notebookOptionsOf"]
    assert got["basic"] == [
        {"label": "My Notebook — notebook.md", "value": "/lib/notebook.md"},
        {"label": "Windows Path — notes.md", "value": "C:\\lib\\notes.md"},
    ]
    # de-duplicated by file value; the FIRST title wins
    assert got["dedupeKeepsFirstTitle"] == [{"label": "First — a.md", "value": "a.md"}]
    assert got["missingTitleFallsBack"] == [{"label": "Notebook — b.md", "value": "b.md"}]
    assert got["skipsNonStringFile"] == []
    assert got["empty"] == []
    assert got["nonArray"] == []


def test_filter_entries_matches_name_or_text_case_insensitively(probe_api: dict) -> None:
    got = probe_api["filterEntries"]
    assert len(got["emptyQueryReturnsAll"]) == 3
    # "Foo" matches by name; "Hello" matches because its text is "World FOO"
    # (case-insensitive substring on EITHER field) -- the point of include_text=1.
    assert got["matchesNameCaseInsensitive"] == ["Foo", "Hello"]
    assert got["matchesTextSubstring"] == ["Hello"]
    assert got["noMatch"] == []
    assert got["nonArrayEntries"] == []
    assert got["whitespaceQueryTrimmed"] == ["Foo", "Hello"]


def test_missing_block_names_dedupes_preserves_order_and_excludes_known(probe_api: dict) -> None:
    got = probe_api["missingBlockNames"]
    assert got["basic"] == ["B", "C"]
    assert got["dedupesAndOrders"] == ["B", "C"]
    assert got["allKnown"] == []
    assert got["emptyBlocks"] == []
    assert got["nonArrayEntryNames"] == ["A"]


def test_group_entries_by_category_builds_groups_in_file_order(probe_api: dict) -> None:
    """Owner ask 2026-09-01: "the left column should have the same groups
    as the notebook it is mirroring" -- entries arrive already in FILE
    order (markdown_store.py's list_entries()); this groups them into
    contiguous runs by `.category` without re-sorting anything."""
    got = probe_api["groupEntriesByCategory"]
    assert got["basic"] == [
        {
            "category": "",
            "entries": [{"name": "One", "category": ""}, {"name": "Two", "category": ""}],
        },
        {
            "category": "Characters",
            "entries": [
                {"name": "Three", "category": "Characters"},
                {"name": "Four", "category": "Characters"},
            ],
        },
        {"category": "Styles", "entries": [{"name": "Five", "category": "Styles"}]},
    ]
    assert got["empty"] == []
    assert got["nonArrayInput"] == []


def test_group_entries_by_category_treats_bad_or_missing_category_as_ungrouped(
    probe_api: dict,
) -> None:
    """A missing `.category` field, or a non-string one, degrades to `''`
    -- markdown_store.py's own convention for the notebook's leading,
    un-headed region -- rather than throwing or growing a header of its
    own. A non-object entry (or `null`) is skipped, never crashes."""
    got = probe_api["groupEntriesByCategory"]
    assert got["allUngrouped"] == [
        {"category": "", "entries": [{"name": "A"}, {"name": "B", "category": ""}]}
    ]
    assert got["missingCategoryField"] == [
        {"category": "", "entries": [{"name": "A"}, {"name": "B"}]}
    ]
    assert got["nonStringCategory"] == [
        {"category": "", "entries": [{"name": "A", "category": 42}]}
    ]
    assert got["skipsNullAndNonObjectEntries"] == [
        {"category": "X", "entries": [{"name": "A", "category": "X"}]}
    ]


# ------------------------------------------------------------ mirroredDraftsRaw
#
# Owner report 2026-09-02: routed through this panel, an edited-but-unsaved
# Notebook prompt used to always run the SAVED text. mirroredDraftsRaw()
# is the pure half of the fix -- picking which discovered Notebook
# candidate's raw `drafts` value this panel should copy onto its own.


def test_mirrored_drafts_raw_matches_by_file_first_match_wins(probe_api: dict) -> None:
    got = probe_api["mirroredDraftsRaw"]
    assert got["matchingCandidate"] == '{"A":"edited"}'
    # Two candidates sharing a `file` (two Notebook nodes on the same
    # library) -- first discovered wins, same tie-break notebookOptionsOf()
    # already uses for the selector's own dedupe.
    assert got["firstMatchWins"] == '{"first":true}'


def test_mirrored_drafts_raw_falls_back_to_the_default_value(probe_api: dict) -> None:
    got = probe_api["mirroredDraftsRaw"]
    assert got["noMatch"] == "{}"
    assert got["emptyFile"] == "{}"
    assert got["emptyCandidates"] == "{}"
    assert got["nonArrayCandidates"] == "{}"
    assert got["candidateWithNullDraftsRaw"] == "{}"
    assert got["candidateMissingDraftsRaw"] == "{}"


# ---------------------------------------------------------------------------
# Source-text pins — the closure-bound attach/render/event-wiring code.
# ---------------------------------------------------------------------------


def test_attach_guards_on_the_exported_class_id(source: str) -> None:
    # Confirm the guard function reads CLASS_ID, not a re-hardcoded string,
    # and that the exported entry point calls it first.
    entry = source.split("export function attachPromptBuilderPanel(node) {", 1)[1]
    entry = entry.split("\n}\n", 1)[0]
    assert "if (!isPromptBuilderNode(node)) return" in entry
    assert "if (attachedNodes.has(node)) return" in entry
    guard = _body(source, "isPromptBuilderNode(node)")
    assert "node.comfyClass === CLASS_ID" in guard
    assert "node.constructor.comfyClass === CLASS_ID" in guard
    assert "export const CLASS_ID = 'EPSPromptBuilder'" in source


def test_attach_is_wrapped_in_try_catch_and_fails_soft(source: str) -> None:
    entry = source.split("export function attachPromptBuilderPanel(node) {", 1)[1]
    entry = entry.split("\n}\n", 1)[0]
    assert entry.strip().startswith("try {")
    assert "} catch (error) {" in entry
    assert "api.warn('attachPromptBuilderPanel failed', error)" in entry


def test_file_and_blocks_widgets_are_hidden_both_ways(source: str) -> None:
    entry = source.split("export function attachPromptBuilderPanel(node) {", 1)[1]
    entry = entry.split("\n}\n", 1)[0]
    assert "hideWidgetBothWays(fileWidget, node)" in entry
    assert "hideWidgetBothWays(blocksWidget, node)" in entry
    hide = _body(source, "hideWidgetBothWays(widget, node)")
    assert "widget.hidden = true" in hide
    assert "widget.options = { ...(widget.options || {}), hidden: true }" in hide


def test_drafts_widget_is_looked_up_hidden_and_null_safe(source: str) -> None:
    """Unsaved-edit drafts (owner report 2026-09-02) -- `drafts` is looked
    up by NAME every attach, exactly like `file`/`blocks`, and degrades to
    `null` (never a refusal to attach) on a backend that predates it, same
    convention notebook.js already uses for ITS OWN pinned/drafts
    widgets."""
    entry = source.split("export function attachPromptBuilderPanel(node) {", 1)[1]
    entry = entry.split("\n}\n", 1)[0]
    assert "const draftsWidget = findWidget(node, DRAFTS_WIDGET_NAME) || null" in entry
    assert "hideWidgetBothWays(draftsWidget, node)" in entry
    assert "createState(node, fileWidget, blocksWidget, draftsWidget)" in entry
    assert "const DRAFTS_WIDGET_NAME = 'drafts'" in source
    assert "const DEFAULT_DRAFTS_VALUE = '{}'" in source


def test_drafts_mirror_survives_a_rebuild(source: str) -> None:
    """§7.9: a tab switch/rebuild throws away the old node object and DOM
    widget and reruns attachPromptBuilderPanel() from scratch on the fresh
    one. There is no module-scope cache tying `draftsWidget` to a
    particular node instance -- it is looked up by NAME (findWidget) every
    single attach and threaded straight into a brand-new state object, so a
    rebuilt node re-discovers (and, via the attach-time rescanNotebooks()
    call, re-syncs) its mirrored drafts exactly like a fresh one would,
    never carrying forward a stale reference from before the rebuild."""
    assert (
        "function createState(node, fileWidget, blocksWidget, draftsWidget = null) {" in source
    )
    create_state = _body(source, "createState(node, fileWidget, blocksWidget, draftsWidget = null)")
    assert "draftsWidget," in create_state
    entry = source.split("export function attachPromptBuilderPanel(node) {", 1)[1]
    entry = entry.split("\n}\n", 1)[0]
    assert "const draftsWidget = findWidget(node, DRAFTS_WIDGET_NAME) || null" in entry
    assert "rescanNotebooks(state, { force: true })" in entry


def test_double_click_handler_appends_via_append_block(source: str) -> None:
    handler = _body(source, "onEntryDoubleClick(state, name)")
    assert "writeBlocksWidget(state, appendBlock(state.blocks, name))" in handler
    left = _body(source, "renderLeftPane(state)")
    assert "onEntryDoubleClick(state, name)" in left
    assert "'dblclick'" in left


def test_remove_button_uses_remove_block_at(source: str) -> None:
    handler = _body(source, "onRemoveBlockClick(state, idx)")
    assert "writeBlocksWidget(state, removeBlockAt(state.blocks, idx))" in handler
    right = _body(source, "renderRightPane(state)")
    assert "onRemoveBlockClick(state, idx)" in right


def test_dnd_reorder_path_uses_reorder_blocks(source: str) -> None:
    commit = _body(source, "commitReorder(state, from, rawInsertion)")
    assert "reorderBlocks(state.blocks, from, to)" in commit
    assert "writeBlocksWidget(state," in commit


def test_dnd_is_native_html5_not_pointer_based(source: str) -> None:
    """Deliberately different from notebook.js's pointer-driven technique --
    see the file header's rationale."""
    right = _body(source, "renderRightPane(state)")
    assert "draggable" in right
    dnd = _body(source, "wireRightListDnD(state)")
    assert "'dragover'" in dnd
    assert "'drop'" in dnd
    assert "event.preventDefault()" in dnd
    row_drag = _body(source, "wireRowDrag(state, row, idx)")
    assert "'dragstart'" in row_drag
    assert "'dragend'" in row_drag


def test_dnd_events_stop_propagation_so_they_never_leak_to_the_canvas(source: str) -> None:
    row_drag = _body(source, "wireRowDrag(state, row, idx)")
    assert row_drag.count("event.stopPropagation()") >= 2
    dnd = _body(source, "wireRightListDnD(state)")
    assert dnd.count("event.stopPropagation()") >= 2


def test_right_pane_render_defers_while_a_drag_is_active(source: str) -> None:
    """Finding 6 (2026-08-26 while-running round): a poll-triggered reload
    landing mid-drag used to replaceChildren() the very row the user has a
    native HTML5 drag on. renderRightPane() now defers itself while
    state.dragFromIndex is set, and wireRowDrag's dragend flushes exactly
    one render if anything was skipped meanwhile. The left pane is
    deliberately NOT gated -- it may keep updating during a drag."""
    right = _body(source, "renderRightPane(state)")
    assert right.strip().startswith("if (state.dragFromIndex != null) {")
    assert "state.rightPaneRenderPending = true" in right
    assert "state.rightPaneRenderPending = false" in right
    row_drag = _body(source, "wireRowDrag(state, row, idx)")
    assert "if (state.rightPaneRenderPending) renderRightPane(state)" in row_drag
    left = _body(source, "renderLeftPane(state)")
    assert "dragFromIndex" not in left


def test_drop_path_clears_drag_before_rendering_so_it_is_never_deferred(source: str) -> None:
    """The DROP handler must null out dragFromIndex BEFORE triggering the
    render that follows a reorder (commitReorder -> writeBlocksWidget ->
    renderRightPane) -- otherwise the user's own drop would be the thing
    that gets deferred."""
    dnd = _body(source, "wireRightListDnD(state)")
    drop = dnd.split("addEventListener('drop'", 1)[1]
    assert drop.index("state.dragFromIndex = null") < drop.index("commitReorder(state, from, raw)")


def test_poll_is_change_gated_with_no_unconditional_dirty_canvas(source: str) -> None:
    """The poll tick itself must never call setDirtyCanvas -- only the write
    helpers it may indirectly trigger do, and only when a value actually
    changed (writeFileWidget/writeBlocksWidget's own value-compare guards)."""
    tick = _body(source, "onPollTick(state)")
    assert "setDirtyCanvas" not in tick
    assert "rescanNotebooks(state)" in tick
    assert "reloadEntries(state)" in tick
    rescan = _body(source, "rescanNotebooks(state, { force = false } = {})")
    assert "if (!force && signature === state.notebookOptionsSignature) return false" in rescan
    write_file = _body(source, "writeFileWidget(state, value)")
    assert "if (widget.value === value) return" in write_file
    write_blocks = _body(source, "writeBlocksWidget(state, list)")
    assert "if (widget.value !== raw) {" in write_blocks
    install = _body(source, "installPoll(state)")
    assert "setInterval(" in install
    assert "const POLL_MS = 5000" in source


def test_selector_writes_the_file_widget_through_its_callback(source: str) -> None:
    write_file = _body(source, "writeFileWidget(state, value)")
    assert "widget.value = value" in write_file
    assert "widget.callback?.(value)" in write_file
    assert "state.node.graph?.setDirtyCanvas(true, true)" in write_file
    header = _body(source, "wireHeaderEvents(state)")
    assert "writeFileWidget(state, value)" in header
    assert "state.selectorEl.addEventListener('change'" in header


def test_left_pane_has_no_edit_or_delete_controls(source: str) -> None:
    """Deliberately read-only (owner spec): every edit stays in the
    Notebook. No delete/rename affordance may appear anywhere in the left
    pane's render path."""
    left = _body(source, "renderLeftPane(state)")
    for forbidden in ("removeBlockAt", "contentEditable", "eps-pb-remove", "input.value ="):
        assert forbidden not in left, forbidden
    assert "eps-pb-row-left" in left


def test_left_pane_marks_already_added_rows_and_gates_the_dblclick(source: str) -> None:
    """Owner ask 2026-09-01 ("you should not be able to add a prompt more
    than once"): a row already in `blocks` reads as added (dimmed row +
    badge) and its dblclick listener is never attached at all -- the
    gesture that normally adds a name simply has nothing to call for a row
    that's already added. appendBlock() is the data-layer backstop for
    every other path in (see its own doc)."""
    left = _body(source, "renderLeftPane(state)")
    assert "const blockSet = new Set(state.blocks)" in left
    assert "const added = blockSet.has(name)" in left
    assert "eps-pb-row-left-added" in left
    assert "eps-pb-added-badge" in left
    assert "if (added) {" in left
    assert left.count("addEventListener('dblclick'") == 1


def test_write_blocks_widget_repaints_left_pane_too(source: str) -> None:
    """So a block removed on the right re-enables its left row (added
    badge gone, dblclick re-attached) the instant it's removed, not on the
    next unrelated repaint."""
    write_blocks = _body(source, "writeBlocksWidget(state, list)")
    assert "renderRightPane(state)" in write_blocks
    assert "renderLeftPane(state)" in write_blocks


def test_left_pane_groups_by_category_and_search_never_hides_a_match(source: str) -> None:
    """Owner ask 2026-09-01: the left column groups under the notebook's
    own category headers, in file order, via groupEntriesByCategory().
    Grouping runs on the FILTERED list, so a search that empties a
    category's matches removes that group (and its header) from the loop
    entirely -- no leftover empty heading. notebook.js's own renderList()
    rule is matched too: a persisted collapse never hides a match while a
    search is active."""
    left = _body(source, "renderLeftPane(state)")
    assert "groupEntriesByCategory(filtered)" in left
    assert "if (group.category) {" in left
    assert "buildGroupHeaderRow(state, group.category, group.entries.length)" in left
    assert (
        "if (!filtering && isSectionCollapsed(state.collapsedSections, group.category)) continue"
        in left
    )


def test_group_header_row_toggles_collapse_and_shows_the_count(source: str) -> None:
    header = _body(source, "buildGroupHeaderRow(state, category, count)")
    assert "isSectionCollapsed(state.collapsedSections, category)" in header
    assert "toggleGroupCollapse(state, category)" in header
    assert "${category} (${count})" in header
    assert "eps-pb-group-header" in header


def test_collapsed_sections_property_mirrors_notebook_exactly(source: str) -> None:
    """Owner ask 2026-09-01: collapse persists in a node PROPERTY (§7.9 --
    pure view state, no §8 positional `widgets_values` hazard), same NAME
    and the same pure parse/toggle/query idiom as notebook.js's own
    `Collapsed sections` -- imported rather than reinvented, so this is
    byte-identical to the Notebook's own convention rather than a second,
    parallel one."""
    assert "const PROP_COLLAPSED_SECTIONS = 'Collapsed sections'" in source
    assert (
        "parseCollapsedSections, toggleCollapsedSection, isSectionCollapsed } "
        "from './notebook.js'" in source
    )
    register = _body(source, "registerCollapsedSectionsProperty(state)")
    assert "node.addProperty(PROP_COLLAPSED_SECTIONS, [], 'array')" in register
    assert "applyCollapsedSectionsFromProperty(state)" in register
    assert "renderLeftPane(state)" in register
    apply_fn = _body(source, "applyCollapsedSectionsFromProperty(state)")
    assert (
        "state.collapsedSections = parseCollapsedSections("
        "state.node.properties?.[PROP_COLLAPSED_SECTIONS])" in apply_fn
    )
    sync_fn = _body(source, "syncCollapsedSectionsProperty(state)")
    assert "node.properties[PROP_COLLAPSED_SECTIONS] = state.collapsedSections" in sync_fn
    assert "node.graph?.setDirtyCanvas(true, true)" in sync_fn
    toggle_fn = _body(source, "toggleGroupCollapse(state, category)")
    assert "toggleCollapsedSection(state.collapsedSections, category)" in toggle_fn
    assert "syncCollapsedSectionsProperty(state)" in toggle_fn
    assert "renderLeftPane(state)" in toggle_fn
    entry = source.split("export function attachPromptBuilderPanel(node) {", 1)[1]
    entry = entry.split("\n}\n", 1)[0]
    assert "registerCollapsedSectionsProperty(state)" in entry


def test_right_pane_shows_missing_badge_using_missing_block_names(source: str) -> None:
    right = _body(source, "renderRightPane(state)")
    assert "missingBlockNames(blocks, entryNames)" in right
    assert "eps-pb-missing-badge" in right
    assert "eps-pb-row-missing" in right


def test_restore_race_is_guarded_like_notebooks_own_fix(source: str) -> None:
    """nodeCreated fires before configure() restores widgets_values -- the
    same defect notebook.js's file header documents for `file`, here for
    `blocks`. wireConfigureReload re-syncs state.blocks from the widget, and
    the attach-time load defers one tick and stands down once configure
    already ran it."""
    configure = _body(source, "wireConfigureReload(state)")
    assert "state.configureReloaded = true" in configure
    assert "state.blocks = parseBlocks(state.blocksWidget.value)" in configure
    assert "reloadEntries(state)" in configure
    entry = source.split("export function attachPromptBuilderPanel(node) {", 1)[1]
    entry = entry.split("\n}\n", 1)[0]
    assert "wireConfigureReload(state)" in entry
    assert "state.attachLoadTimer = setTimeout(() => {" in entry
    deferred = entry.split("state.attachLoadTimer = setTimeout(() => {", 1)[1]
    assert "if (state.configureReloaded) return" in deferred


def test_external_write_resync_never_forces_a_network_call_for_blocks_only(source: str) -> None:
    """2026-08-29 bugfix round (owner: "applying any of the sets won't
    change anything") -- deliberately NOT `wireConfigureReload()`'s full
    body: that one ALWAYS calls `reloadEntries()`, which issues a real GET
    even when the cache already has the answer. An Apply writing only
    `blocks` (never touching `file`) must repaint the right pane alone,
    purely from `state.entries` already cached -- no network. Only a
    changed `file` falls through to the real reload."""
    resync = _body(source, "resyncAfterExternalWrite(state)")
    assert "state.blocks = parseBlocks(state.blocksWidget.value)" in resync
    assert "const fileChanged = (state.fileWidget.value ?? '') !== state.file" in resync
    assert "if (fileChanged) {" in resync
    assert "reloadEntries(state)" in resync
    assert "renderRightPane(state)" in resync


def test_attach_publishes_the_reload_seam_and_installs_the_subscription(source: str) -> None:
    """Same `__eps<Panel>Reload` seam convention picker.js already
    publishes for controller.js's Push State -- api.js's shared
    `announceWidgetsChangedExternally()`/`subscribeWidgetsChangedExternally()`
    routes by NODE IDENTITY, so this file only needs to stamp its own
    reload function onto the node and install the ONE shared subscription
    once (idempotent module flag)."""
    entry = source.split("export function attachPromptBuilderPanel(node) {", 1)[1]
    entry = entry.split("\n}\n", 1)[0]
    assert "node.__epsPromptBuilderReload = () => resyncAfterExternalWrite(state)" in entry
    assert "installExternalWriteSubscription()" in entry
    install = _body(source, "installExternalWriteSubscription()")
    assert "if (externalWriteSubscribed) return" in install
    assert "externalWriteSubscribed = true" in install
    assert "api.subscribeWidgetsChangedExternally((entries) => {" in install
    assert "entry?.node?.__epsPromptBuilderReload?.()" in install


def test_teardown_clears_poll_and_attach_timers(source: str) -> None:
    teardown = _body(source, "teardown(state)")
    assert "if (state.pollTimer) clearInterval(state.pollTimer)" in teardown
    assert "if (state.attachLoadTimer) clearTimeout(state.attachLoadTimer)" in teardown
    cleanup = _body(source, "wireNodeCleanup(state)")
    assert "teardown(state)" in cleanup


def test_reload_entries_reuses_notebooks_session_cache_and_known_mtime(source: str) -> None:
    """2026-08-26 while-running round (findings 4 + 5): the actual network
    call moved from a literal `api.getJson('/lora_library/notebook', params)`
    into the module-scope shared `sharedReloadFetch(file, knownMtime)` (see
    the tests below for its own single-flight + TTL contract) -- reloadEntries
    itself still owns the session-cache paint + known_mtime DECISION, just not
    the request that goes out."""
    reload_fn = _body(source, "reloadEntries(state)")
    assert "notebookCacheGet(file)" in reload_fn
    assert "notebookCacheSet(file, data" in reload_fn
    assert "isUnchangedResponse(data)" in reload_fn
    assert (
        "(showing || paintedFromCache) && typeof state.paintedMtime === 'number' "
        "? state.paintedMtime : null"
        in reload_fn
    )
    assert "data = await sharedReloadFetch(file, knownMtime)" in reload_fn
    shared = _body(source, "sharedReloadFetch(file, knownMtime)")
    assert "include_text: '1'" in shared
    assert "params.known_mtime = String(knownMtime)" in shared


def test_shared_reload_fetch_is_single_flight_and_short_ttl_per_file(source: str) -> None:
    """Finding 5: every Builder node mirroring the same notebook `file` used
    to fire its own concurrent GET on every poll tick. sharedReloadFetch
    joins an already-outstanding request for that file, and a just-finished
    one stays servable for a short TTL so a same-tick fanout across nodes
    costs one request, not N."""
    shared = _body(source, "sharedReloadFetch(file, knownMtime)")
    assert "entriesFetchCache.get(file)" in shared
    assert "Date.now() - cached.fetchedAt < ENTRIES_FETCH_TTL_MS" in shared
    assert "entriesFetchInFlight.get(file)" in shared
    assert "if (inFlight) return inFlight" in shared
    assert "entriesFetchInFlight.set(file, promise)" in shared
    assert "entriesFetchInFlight.delete(file)" in shared
    assert "const entriesFetchCache = new Map()" in source
    assert "const entriesFetchInFlight = new Map()" in source
    assert "const ENTRIES_FETCH_TTL_MS = 1500" in source


def test_poll_tick_skips_while_a_reload_is_already_in_flight(source: str) -> None:
    """Finding 4: a single node's own tick must not stack a second
    concurrent reloadEntries() while a previous tick's fetch is still
    outstanding -- mid-run the server can easily take longer than POLL_MS to
    answer."""
    tick = _body(source, "onPollTick(state)")
    assert "if (state.pollReloadInFlight) return" in tick
    assert "state.pollReloadInFlight = reloadEntries(state)" in tick
    assert "state.pollReloadInFlight = null" in tick
    assert "pollReloadInFlight: null" in source  # state field exists


def test_hidden_tab_skips_ticks_and_flushes_once_on_visibilitychange(source: str) -> None:
    """Finding 4's second half: a background tab must not poll a GIL-busy
    server, but coming back to the tab should not wait up to POLL_MS for the
    next tick -- one flush fires immediately via visibilitychange, guarded by
    the same in-flight check onPollTick already has."""
    install = _body(source, "installPoll(state)")
    assert "if (document.hidden) return" in install
    assert "state.visibilityHandler = () => {" in install
    assert "document.addEventListener('visibilitychange', state.visibilityHandler)" in install
    assert "onPollTick(state)" in install
    teardown = _body(source, "teardown(state)")
    assert (
        "document.removeEventListener('visibilitychange', state.visibilityHandler)" in teardown
    )


def test_discovery_walks_the_whole_workflow_rooted_at_app_graph(source: str) -> None:
    """Never state.node.graph -- a builder living inside a subgraph must
    still find notebooks anywhere else in the workflow (picker.js's
    findSendCandidates() precedent)."""
    discover = _body(source, "discoverNotebookCandidates()")
    assert "walkLiveNodes(app.graph)" in discover
    assert "state.node.graph" not in discover


def test_discovery_captures_each_candidates_raw_drafts_value_too(source: str) -> None:
    """Unsaved-edit drafts (owner report 2026-09-02): the SAME discovery
    walk that already builds the selector's file list also grabs each
    candidate's raw `drafts` widget value, so mirroredDraftsRaw() has
    something to pick from without a second graph walk."""
    discover = _body(source, "discoverNotebookCandidates()")
    assert "findWidget(node, DRAFTS_WIDGET_NAME)" in discover
    assert "draftsRaw: typeof dw?.value === 'string' ? dw.value : null" in discover


def test_sync_mirrored_drafts_is_write_if_different(source: str) -> None:
    """Same idiom as writeFileWidget()/writeBlocksWidget(): a no-op widget
    (backend predates it) or an unchanged value never dirties the canvas."""
    sync = _body(source, "syncMirroredDrafts(state, candidates)")
    assert sync.strip().startswith("const widget = state.draftsWidget")
    assert "if (!widget) return" in sync
    assert "mirroredDraftsRaw(candidates, state.fileWidget.value ?? '')" in sync
    assert "if (widget.value === raw) return" in sync
    assert "widget.value = raw" in sync
    assert "widget.callback?.(raw)" in sync
    assert "state.node.graph?.setDirtyCanvas(true, true)" in sync


def test_rescan_syncs_mirrored_drafts_before_the_options_change_gate(source: str) -> None:
    """A draft can change on the mirrored Notebook while the discovered
    notebook SET (and thus rescanNotebooks()'s own options-signature
    early-return) stays identical -- syncMirroredDrafts() must run
    unconditionally, on every rescan, not only the ones that go on to
    rebuild the selector. rescanNotebooks() already runs from attach,
    configure, selector focus, and every ~5s poll tick (see POLL_MS in
    test_poll_is_change_gated_with_no_unconditional_dirty_canvas) -- no new
    timer is introduced for this."""
    rescan = _body(source, "rescanNotebooks(state, { force = false } = {})")
    assert "syncMirroredDrafts(state, candidates)" in rescan
    sync_index = rescan.index("syncMirroredDrafts(state, candidates)")
    gate_index = rescan.index(
        "if (!force && signature === state.notebookOptionsSignature) return false"
    )
    assert sync_index < gate_index


def test_write_file_widget_adopts_the_newly_mirrored_notebooks_drafts(source: str) -> None:
    """Switching which Notebook this panel mirrors (the selector) adopts
    that Notebook's drafts immediately rather than waiting for the next
    poll tick."""
    write_file = _body(source, "writeFileWidget(state, value)")
    assert "syncMirroredDrafts(state, discoverNotebookCandidates())" in write_file


def test_no_notebook_option_disables_the_selector(source: str) -> None:
    selector = _body(source, "renderSelector(state)")
    assert "NO_NOTEBOOK_OPTION_TEXT" in selector
    assert "select.disabled = true" in selector
    assert "const NO_NOTEBOOK_OPTION_TEXT = 'no Prompt Notebook on canvas'" in source


def test_empty_state_hint_shown_when_no_notebooks_discovered(source: str) -> None:
    left = _body(source, "renderLeftPane(state)")
    assert "EMPTY_NO_NOTEBOOK_HINT" in left
    assert (
        "const EMPTY_NO_NOTEBOOK_HINT =\n"
        "  'Add an EPS Prompt Notebook to choose the library file — this panel mirrors it.'"
        in source
    )


def test_dom_widget_never_serializes(source: str) -> None:
    attach_dom = _body(source, "attachDomWidget(node, rootEl)")
    assert "serialize: false" in attach_dom
    assert "domWidget.serialize = false" in attach_dom
    assert "domWidget.serializeValue = () => undefined" in attach_dom


def test_auto_select_keeps_a_still_present_file_and_adopts_a_single_candidate(source: str) -> None:
    auto = _body(source, "autoSelectNotebookFile(state)")
    assert "if (options.some((opt) => opt.value === current)) return" in auto
    assert "if (options.length === 1) writeFileWidget(state, options[0].value)" in auto


# ---------------------------------------------------------------------------
# lora_library.js wiring
# ---------------------------------------------------------------------------


def test_lora_library_js_imports_and_attaches_the_panel() -> None:
    entry = LORA_LIBRARY_JS.read_text(encoding="utf-8")
    assert "import * as promptBuilder from './lora_library/prompt_builder.js'" in entry
    assert (
        "safely('promptBuilder.attachPromptBuilderPanel', "
        "() => promptBuilder.attachPromptBuilderPanel(node))" in entry
    )
    # mirrors the notebook's own nodeCreated line, same wrapper convention
    notebook_line = (
        "safely('notebook.attachNotebookWidget', () => notebook.attachNotebookWidget(node))"
    )
    assert notebook_line in entry
