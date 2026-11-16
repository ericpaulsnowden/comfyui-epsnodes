"""Behaviour tests for the shared group-system helpers
(``web/lora_library/group_helpers.js``, docs/plan/ROADMAP-shared-panel-code.md
M4): the ``#`` group-name rules, the tolerant parsers for the two persisted
node properties, and the canvas-legacy prompt-dialog flush.

Before the module existed each helper was hand-copied into 2-3 panels under two
names. The module imports nothing, so these tests drive it directly under Node
and also fail if a panel declares its own copy again.
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
GROUP_HELPERS_JS = WEB / "lora_library" / "group_helpers.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

PROBE_JS = """
import * as g from '%(module)s'

const out = {}
out.propKey = g.PROP_RENAME_DRAFT
out.isGroup = ['# Portraits', '  #x', '#', 'Portraits', 'a # b', '', null, undefined].map(g.isGroupNameInput)
out.groupName = ['#   Portraits  ', '###Portraits', '# ## Mixed', 'Portraits', '#', '  ', null, undefined]
  .map(g.groupNameFromInput)
out.collapsed = [
  ['A', 'B'], ['A', 1, null, 'B', {}], '["A","B"]', '  ["A"]  ', 'not json', '[1,2]', '', '   ',
  null, undefined, 42, { A: true }, '{"a":1}'
].map(g.parseCollapsedGroups)
out.draft = [
  ['Group A', 'Group A2'], '["Group A","Group A2"]', [], ['Group A'], ['Group A', 'x', 'y'], ['', 'x'],
  [1, 'x'], ['Group A', 1], 'not json', '', '   ', null, undefined, 42, { category: 'Group A', text: 'x' },
  ['Group A', '']
].map(g.parseRenameDraft)

// --- flushPendingNameEdit -------------------------------------------------
const run = ({ dialog, value = 'old', callback = true, widget = undefined, warnTwice = false }) => {
  const warnings = []
  const w = widget === undefined ? { value, calls: [] } : widget
  if (w && callback === true) w.callback = (v) => { w.calls.push(v) }
  if (w && callback === 'throws') w.callback = () => { throw new Error('cb') }
  const node = { dirty: [] }
  node.setDirtyCanvas = (...a) => node.dirty.push(a)
  const canvas = { prompt_box: dialog }
  g.flushPendingNameEdit({ node, widget: w, canvas, title: 'T', warn: (...a) => warnings.push(a.map(String)) })
  return { value: w?.value, calls: w?.calls, dirty: node.dirty, warnings, closed: dialog?.closed }
}
const dialogWith = (live, opts = {}) => ({
  isConnected: opts.isConnected ?? true, closed: false,
  querySelector: () => (opts.noInput ? null : { value: live }),
  close() { this.closed = true }
})
out.flushCommits = run({ dialog: dialogWith('# Portraits') })
out.flushNoDialog = run({ dialog: null })
out.flushDisconnected = run({ dialog: dialogWith('x', { isConnected: false }) })
out.flushNoInput = run({ dialog: dialogWith('x', { noInput: true }) })
out.flushInSync = run({ dialog: dialogWith('old') })
out.flushNoWidget = run({ dialog: dialogWith('x'), widget: null })
out.flushCallbackThrows = run({ dialog: dialogWith('new'), callback: 'throws' })
out.flushProbeThrows = run({ dialog: { isConnected: true, querySelector() { throw new Error('boom') }, close() {} } })
out.flushNoCanvas = (() => {
  const w = { value: 'old' }
  g.flushPendingNameEdit({ node: {}, widget: w, canvas: undefined, title: 'T', warn: () => { throw new Error('no') } })
  return w.value
})()
process.stdout.write('RESULT:' + JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def probe(tmp_path_factory: pytest.TempPathFactory) -> dict:
    layout = tmp_path_factory.mktemp("group_helpers")
    script = layout / "probe.mjs"
    script.write_text(PROBE_JS % {"module": GROUP_HELPERS_JS.as_uri()}, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(script)], capture_output=True, encoding="utf-8", timeout=60, cwd=layout
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout.split("RESULT:", 1)[1])


def test_rename_draft_property_key_is_the_saved_one(probe: dict) -> None:
    # Saved workflows carry this key; changing it would orphan every open rename.
    assert probe["propKey"] == "Group rename draft"


def test_a_leading_hash_after_trim_makes_a_group(probe: dict) -> None:
    assert probe["isGroup"] == [True, True, True, False, False, False, False, False]


def test_group_name_strips_leading_hashes_and_whitespace(probe: dict) -> None:
    assert probe["groupName"] == ["Portraits", "Portraits", "## Mixed", "Portraits", "", "", "", ""]


def test_collapsed_groups_parse_is_tolerant_and_never_throws(probe: dict) -> None:
    assert probe["collapsed"] == [
        ["A", "B"],  # canonical array
        ["A", "B"],  # non-string entries dropped
        ["A", "B"],  # hand-edited JSON string round-trips
        ["A"],  # surrounding whitespace tolerated
        [],  # malformed JSON
        [],  # JSON array of non-strings
        [],  # blank
        [],  # whitespace only
        [],  # null
        [],  # undefined
        [],  # number
        [],  # plain object
        [],  # JSON object, not an array
    ]


def test_rename_draft_parse_accepts_only_a_group_and_text_pair(probe: dict) -> None:
    pair = {"category": "Group A", "text": "Group A2"}
    assert probe["draft"] == [
        pair,
        pair,  # JSON string from a hand-edit
        None,  # [] = no open rename
        None,  # wrong length
        None,
        None,  # empty group name never counts as a draft
        None,  # non-string group
        None,  # non-string text
        None,  # malformed JSON
        None,  # blank
        None,  # whitespace only
        None,
        None,
        None,
        None,  # a plain object is not the saved array shape
        {"category": "Group A", "text": ""},  # an empty edit buffer is still an open rename
    ]


def test_flush_commits_the_typed_text_through_value_callback_dirty_and_closes(probe: dict) -> None:
    r = probe["flushCommits"]
    assert r["value"] == "# Portraits"
    assert r["calls"] == ["# Portraits"]
    assert r["dirty"] == [[True, True]]
    assert r["closed"] is True
    assert r["warnings"] == []


@pytest.mark.parametrize(
    "key", ["flushNoDialog", "flushDisconnected", "flushNoInput", "flushInSync", "flushNoWidget"]
)
def test_flush_does_nothing_when_nothing_is_pending(probe: dict, key: str) -> None:
    r = probe[key]
    assert r.get("calls", []) == []
    assert r["dirty"] == []
    assert r["warnings"] == []
    assert not r.get("closed")
    if key != "flushNoWidget":
        assert r["value"] == "old"


def test_flush_still_commits_and_warns_when_the_widget_callback_throws(probe: dict) -> None:
    r = probe["flushCallbackThrows"]
    assert r["value"] == "new"
    assert r["closed"] is True
    assert [w[0] for w in r["warnings"]] == ["T: name widget callback threw"]


def test_flush_never_throws_out_of_a_broken_dialog(probe: dict) -> None:
    r = probe["flushProbeThrows"]
    assert r["value"] == "old"
    assert [w[0] for w in r["warnings"]] == ["T: could not flush pending name edit"]
    assert probe["flushNoCanvas"] == "old"


SHARED_NAMES = (
    "isCategoryNameInput|isGroupNameInput|categoryNameFromInput|groupNameFromInput|"
    "parseCollapsedSections|parseCollapsedGroups|parseRenameDraft|PROP_RENAME_DRAFT|"
    "flushPendingNameEdit"
)


def test_no_panel_declares_its_own_copy_of_a_group_helper() -> None:
    """One implementation: a second declaration under the old OR the new name
    is the drift the roadmap exists to stop. `_flushPendingNameEdit` may stay
    on each Controller only as a wrapper, so the dialog mechanism
    (`prompt_box`) and the `#` strip regex must appear nowhere else."""
    declaration = re.compile(rf"^\s*(?:export )?(?:async )?(?:function|const) ({SHARED_NAMES})\b", re.M)
    offenders = []
    for path in sorted(WEB.rglob("*.js")):
        if path == GROUP_HELPERS_JS:
            continue
        source = path.read_text(encoding="utf-8")
        offenders += [f"{path.relative_to(WEB)}: declares {m.group(1)}" for m in declaration.finditer(source)]
        code = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith(("*", "//", "/*"))
        )
        offenders += [
            f"{path.relative_to(WEB)}: uses {needle}"
            for needle in ("prompt_box", "replace(/^#+")
            if needle in code
        ]
    assert not offenders, offenders
