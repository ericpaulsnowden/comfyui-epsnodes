"""Pure-function tests for the shared search matcher
(``web/lora_library/search.js``, FORMAT.md §7.2) -- the pack's first
deliberate shared-panel module (docs/ROADMAP-shared-panel-code.md's
"one implementation, not a fourth copy" milestone). Case-insensitive,
whitespace-split, every query word AND'd against the haystack -- the
Notebook's original semantics (v0.53.0, owner ask 2026-08-08), now shared
by notebook.js/picker.js/universal_controller.js/controller.js instead of
being reimplemented (or diverging) per panel, per the owner's 2026-09-08
decision: "use Notebook as the model and make them all follow that
paradigm".

Zero dependencies (no DOM, no ``./api.js``, no ``app.js``) -- unlike every
other ``*_js.py`` file in this repo, no served-layout tmp dir is needed:
the probe imports ``search.js`` directly. Skips cleanly when Node isn't
installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SEARCH_JS = REPO_ROOT / "web" / "lora_library" / "search.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")


def test_search_js_parses() -> None:
    result = subprocess.run(
        [NODE, "--check", "--input-type=module"],
        input=SEARCH_JS.read_text(encoding="utf-8"),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_module_is_pure_no_imports_no_dom_no_domain_coupling() -> None:
    """The task brief for this file (2026-09-08): "pure functions, no DOM,
    no node/graph references, no imports of its own beyond nothing" -- the
    proof-of-concept for docs/ROADMAP-shared-panel-code.md's shared-module
    rule has to actually BE the model, not just be documented as one."""
    src = SEARCH_JS.read_text(encoding="utf-8")
    assert "\nimport " not in src and not src.startswith("import ")
    assert "document." not in src
    assert "window." not in src
    assert "node." not in src and "graph." not in src


#: (haystack name, haystack text, query, expected) -- entryMatchesSearch's
#: contract as every panel actually calls it: searchHaystack(name, text)
#: feeds entryMatchesSearch, whose words come from searchWords(query).
MATCH_CASES = [
    ("Red Car", "a fast vehicle", "red car", True),  # multi-word AND, both present
    ("Red Car", "a fast vehicle", "car red", True),  # order-independent
    ("Red Car", "a fast vehicle", "red missing", False),  # AND: one word absent
    ("Red Car", "a fast vehicle", "RED CAR", True),  # case-insensitive
    ("Red Car", "a fast vehicle", "  red   car  ", True),  # extra whitespace collapses
    ("Red Car", "", "", True),  # empty query matches everything
    ("Red Car", "", "   ", True),  # whitespace-only query matches everything
    ("Red Car", "", "blue", False),  # no match
    ("Title", "fast vehicle in the body", "vehicle", True),  # a word may live in the body only
    ("café menu", "naïve ño", "CAFÉ", True),  # unicode, case-insensitive
    ("café menu", "naïve ño", "café ño", True),  # unicode, multi-word AND
    ("café menu", "naïve ño", "coffee", False),  # unicode, no match
]

PROBE_JS = r"""
import { entryMatchesSearch, searchHaystack, searchWords } from './search.js'

const cases = %(cases)s
const out = {
  exports: {
    entryMatchesSearch: typeof entryMatchesSearch,
    searchHaystack: typeof searchHaystack,
    searchWords: typeof searchWords
  },
  matches: cases.map(([name, text, query]) =>
    entryMatchesSearch(searchHaystack(name, text), searchWords(query))
  ),
  haystack: [
    searchHaystack('Name', 'Body'),
    searchHaystack('Name', ''),
    searchHaystack('UPPER', 'MiXeD')
  ],
  words: [
    searchWords('red car'),
    searchWords('  red   car  '),
    searchWords(''),
    searchWords('   '),
    searchWords('ONE')
  ]
}
process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def search_api(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Runs PROBE_JS against the REAL search.js -- no served-layout tree
    needed (see module docstring): the module has no imports of its own."""
    layout = tmp_path_factory.mktemp("search_web_root")
    shutil.copyfile(SEARCH_JS, layout / "search.js")
    probe = layout / "probe.mjs"
    probe.write_text(
        PROBE_JS % {"cases": json.dumps([[n, t, q] for n, t, q, _ in MATCH_CASES])},
        encoding="utf-8",
    )
    result = subprocess.run(
        [NODE, str(probe)], capture_output=True, text=True, timeout=30, cwd=layout
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


def test_module_exports_the_three_pure_helpers(search_api: dict) -> None:
    assert search_api["exports"] == {
        "entryMatchesSearch": "function",
        "searchHaystack": "function",
        "searchWords": "function",
    }


def test_multi_word_and_case_insensitive_and_unicode_matching(search_api: dict) -> None:
    expected = [case[3] for case in MATCH_CASES]
    pairs = zip(MATCH_CASES, search_api["matches"], strict=True)
    for (name, text, query, want), got in pairs:
        msg = f"entryMatchesSearch({name!r}, {text!r}, {query!r}) -> {got!r}, want {want!r}"
        assert got is want, msg
    assert search_api["matches"] == expected


def test_search_haystack_is_name_newline_text_lowercased(search_api: dict) -> None:
    assert search_api["haystack"] == ["name\nbody", "name\n", "upper\nmixed"]


def test_search_words_splits_on_whitespace_lowercases_and_drops_empties(search_api: dict) -> None:
    assert search_api["words"] == [
        ["red", "car"],
        ["red", "car"],
        [],
        [],
        ["one"],
    ]
