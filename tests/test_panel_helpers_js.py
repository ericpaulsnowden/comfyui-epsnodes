"""Behaviour tests for the shared panel helpers
(``web/lora_library/panel_helpers.js``: ``el`` and ``installMinWidth``,
docs/plan/ROADMAP-shared-panel-code.md M1 + the installMinWidth unification).

Before that module existed both helpers were hand-copied into 8-9 panels and
a fix reached only some copies. These tests drive the one implementation under
Node with a tiny DOM stub (the module imports nothing, so no served layout is
needed) and guard against a panel declaring its own copy again.
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
PANEL_HELPERS_JS = WEB / "lora_library" / "panel_helpers.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

PROBE_JS = """
import { el, installMinWidth } from '%(module)s'

class FakeNode {
  constructor(tag) { this.tag = tag; this.attrs = {}; this.kids = [] }
  setAttribute(k, v) { this.attrs[k] = v }
  append(child) { this.kids.push(child) }
}
globalThis.Node = FakeNode
globalThis.document = {
  createElement: (tag) => new FakeNode(tag),
  createTextNode: (text) => ({ text })
}
const describe = (n) => ({
  tag: n.tag, className: n.className, textContent: n.textContent, attrs: n.attrs,
  kids: n.kids.map((k) => (k instanceof FakeNode ? describe(k) : k))
})
const out = {}

const inner = el('span', { text: 'x' })
out.el = describe(el('div', { className: 'a b', text: 'hi', attrs: { title: 't', 'data-n': 3 } },
  ['plain', 7, null, undefined, inner]))
out.elBare = describe(el('p'))

// A node created narrower than the floor is lifted ONCE, through setSize.
const makeNode = (width, withSetSize = true) => {
  const node = { size: [width, 50], resizeCalls: [], setSizeCalls: [] }
  node.onResize = function (size) { node.resizeCalls.push([...size]); return 'orig' }
  if (withSetSize) node.setSize = (s) => { node.setSizeCalls.push([...s]); node.size[0] = s[0] }
  return node
}
const narrow = makeNode(100)
installMinWidth(narrow, 300)
installMinWidth(narrow, 999) // second call on the same node is a repeat, not a new floor
out.narrow = { size: narrow.size[0], setSizeCalls: narrow.setSizeCalls }
const dragged = [120, 40]
out.narrow.resizeResult = narrow.onResize(dragged)
out.narrow.dragged = dragged
out.narrow.chained = narrow.resizeCalls
const wideDrag = [500, 40]
narrow.onResize(wideDrag)
out.narrow.wideDrag = wideDrag

const wide = makeNode(400)
installMinWidth(wide, 300)
out.wide = { size: wide.size[0], setSizeCalls: wide.setSizeCalls }

// No setSize (older frontend / stub): the width is still written, not skipped.
const bare = makeNode(100, false)
installMinWidth(bare, 300)
out.bare = bare.size[0]

installMinWidth(null, 300)
installMinWidth(undefined, 300)
out.nullSafe = true
process.stdout.write('RESULT:' + JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def probe(tmp_path_factory: pytest.TempPathFactory) -> dict:
    layout = tmp_path_factory.mktemp("panel_helpers")
    script = layout / "probe.mjs"
    script.write_text(PROBE_JS % {"module": PANEL_HELPERS_JS.as_uri()}, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(script)], capture_output=True, encoding="utf-8", timeout=60, cwd=layout
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout.split("RESULT:", 1)[1])


def test_el_builds_the_element_it_is_given(probe: dict) -> None:
    built = probe["el"]
    assert built["tag"] == "div"
    assert built["className"] == "a b"
    assert built["textContent"] == "hi"
    assert built["attrs"] == {"title": "t", "data-n": 3}
    # strings/numbers become text nodes, null/undefined are skipped, a Node is appended as is
    kids = built["kids"]
    assert kids[:2] == [{"text": "plain"}, {"text": "7"}]
    assert len(kids) == 3
    assert kids[2]["tag"] == "span"
    assert kids[2]["textContent"] == "x"


def test_el_with_no_options_is_a_bare_element(probe: dict) -> None:
    bare = probe["elBare"]
    assert bare["tag"] == "p"
    assert "className" not in bare  # never set (undefined)
    assert "textContent" not in bare
    assert bare["attrs"] == {}
    assert bare["kids"] == []


def test_install_min_width_lifts_a_too_narrow_node_once(probe: dict) -> None:
    narrow = probe["narrow"]
    assert narrow["size"] == 300
    assert narrow["setSizeCalls"] == [[300, 50]], "one lift, through setSize, no second install"


def test_install_min_width_floors_later_resizes_and_chains_the_original(probe: dict) -> None:
    narrow = probe["narrow"]
    assert narrow["dragged"] == [300, 40], "a drag below the floor is raised in place"
    assert narrow["resizeResult"] == "orig", "the original onResize result is returned"
    assert narrow["chained"][0] == [300, 40], "the original sees the floored size"
    assert narrow["wideDrag"] == [500, 40], "a drag above the floor is untouched"
    assert len(narrow["chained"]) == 2, "wrapped once, not once per install call"


def test_install_min_width_never_shrinks_a_wide_node(probe: dict) -> None:
    assert probe["wide"] == {"size": 400, "setSizeCalls": []}


def test_install_min_width_writes_the_width_when_set_size_is_missing(probe: dict) -> None:
    assert probe["bare"] == 300


def test_install_min_width_tolerates_a_missing_node(probe: dict) -> None:
    assert probe["nullSafe"] is True


def test_no_panel_declares_its_own_copy_of_a_shared_helper() -> None:
    """The point of the module: one implementation. A second declaration is
    the drift this pack kept paying for (docs/plan/ROADMAP-shared-panel-code.md)."""
    declaration = re.compile(r"^(?:export )?(?:function|const) (el|installMinWidth)\b", re.M)
    offenders = [
        f"{path.relative_to(WEB)}: {m.group(1)}"
        for path in sorted(WEB.rglob("*.js"))
        if path != PANEL_HELPERS_JS
        for m in declaration.finditer(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, offenders
