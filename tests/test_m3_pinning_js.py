"""Frontend pins for provenance M3 "full pinning" (FORMAT.md §6.1/§6.2/§7.2,
docs/ROADMAP-run-provenance.md M3; owner 2026-08-18/21: "will the user be
able to see what the old values are? That would be important", plus a
one-click clear).

The backend bakes VALUES into a per-image workflow as two tail STRING
widgets -- the Notebook's `pinned` (`{format, entries: [{name, text}],
source: {file, token, captured}}`) and Apply Set's `pinned_state`
(`{format, slug, name, set: {...}, source: {token, captured}}`), `""` =
live. The frontend half is VISIBILITY + UNPIN: notebook.js shows a badge
row, the pinned entries read-only with drift markers against the live
library, and an Unpin button; sets.js shows a badge row with the pinned
rows' summary, a drift verdict from `GET /lora_library/set?slug=`, and
Unpin; cross_sweep.js's estimator counts a pin's entries.

Two layers, the sibling JS test files' conventions:

1. **Node probe** of the PURE helpers both modules export -- `parsePinned`
   / `pinnedDrift` / `pinnedBadgeText` (notebook.js) and `parsePinnedState`
   / `pinnedRowsSummary` / `comparePinnedSet` / `pinnedStateBadgeText` /
   `migrateLegacyMirrorsValue` (sets.js) -- run against the REAL modules in
   a served-layout tmp dir (test_picker_js.py / test_cross_sweep_js.py's
   convention: the modules import `./api.js` -> `../../../scripts/api.js`
   and `../../../scripts/app.js`, so the fixture mirrors that depth, copies
   the real `api.js`/`version.js` in, and stubs the two core scripts).
   Importing the modules under Node is itself a regression test: it is
   what caught a backtick inside notebook.js's CSS template literal that
   `node --check` (which parses a bare .js as CommonJS) let through.
2. **Source-text pins** for the closure-bound wiring that only runs
   against a real litegraph node and a real DOM: both hide flags on both
   new widgets, the badge/unpin wiring, mutation gating while pinned,
   unpin's value + callback write, the drift fetch, no window listeners /
   no canvas drawing in the sets.js pin code (Vue-safe), the DOM row's
   serialize flags and its LAST position, and the pre-M3 positional
   migration. The LIVE mechanics are for the rig.

Skips cleanly when Node isn't installed.
"""

# The badge pins below quote the exact user-facing strings (em dashes, the
# pin emoji, the ≠ marker, the ellipsis): byte-exact contract pins, not
# accidental look-alikes.

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB = REPO_ROOT / "web"
NOTEBOOK_JS = WEB / "lora_library" / "notebook.js"
SETS_JS = WEB / "lora_library" / "sets.js"
API_JS = WEB / "lora_library" / "api.js"
VERSION_JS = WEB / "lora_library" / "version.js"
CROSS_SWEEP_JS = WEB / "eps_image" / "cross_sweep.js"

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")


# --------------------------------------------------------------- case tables

NOTEBOOK_PIN = {
    "format": 1,
    "entries": [{"name": "A", "text": "hello\nworld"}, {"name": "B", "text": "b body"}],
    "source": {"file": "loras.md", "token": "m2_i1_t3", "captured": "2026-08-21T10:00:00"},
}

#: (case, raw `pinned` value, expected parsePinned() result)
NOTEBOOK_PARSE_CASES = [
    ("empty_string_is_live", "", None),
    ("whitespace_is_live", "   ", None),
    ("not_json_is_live", "nope", None),
    ("legacy_shifted_tag_is_live", "(any)", None),
    ("array_is_live", "[1]", None),
    ("no_entries_key_is_live", json.dumps({"format": 1}), None),
    ("empty_entries_is_live", json.dumps({"entries": []}), None),
    ("entries_without_names_is_live", json.dumps({"entries": [{"text": "x"}]}), None),
    (
        "full_pin",
        json.dumps(NOTEBOOK_PIN),
        {
            "format": 1,
            "entries": [{"name": "A", "text": "hello\nworld"}, {"name": "B", "text": "b body"}],
            "source": {"file": "loras.md", "token": "m2_i1_t3", "captured": "2026-08-21T10:00:00"},
        },
    ),
    (
        "missing_source_and_text_coerce_to_empty_strings",
        json.dumps({"entries": [{"name": "Only"}]}),
        {
            "format": None,
            "entries": [{"name": "Only", "text": ""}],
            "source": {"file": "", "token": "", "captured": ""},
        },
    ),
    (
        "unknown_format_still_renders_entries_are_the_stable_core",
        json.dumps(
            {"format": 9, "entries": [{"name": "A", "text": "t"}], "source": {"token": "m1_t1"}}
        ),
        {
            "format": 9,
            "entries": [{"name": "A", "text": "t"}],
            "source": {"file": "", "token": "m1_t1", "captured": ""},
        },
    ),
    (
        "nameless_items_are_dropped_named_ones_kept",
        json.dumps({"entries": [{"name": "A", "text": "t"}, {"text": "no name"}, None, 5]}),
        {
            "format": None,
            "entries": [{"name": "A", "text": "t"}],
            "source": {"file": "", "token": "", "captured": ""},
        },
    ),
]

#: (case, entryTextByName, libraryLoaded, expected pinnedDrift() result for NOTEBOOK_PIN)
NOTEBOOK_DRIFT_CASES = [
    (
        "identical_texts_match",
        {"A": "hello\nworld", "B": "b body"},
        True,
        {
            "status": "match",
            "rows": [
                {"name": "A", "kind": "same", "current": "hello\nworld"},
                {"name": "B", "kind": "same", "current": "b body"},
            ],
        },
    ),
    (
        "crlf_and_trailing_whitespace_are_not_drift",
        {"A": "hello  \r\nworld\r\n", "B": "b body\n\n"},
        True,
        {
            "status": "match",
            "rows": [
                {"name": "A", "kind": "same", "current": "hello  \r\nworld\r\n"},
                {"name": "B", "kind": "same", "current": "b body\n\n"},
            ],
        },
    ),
    (
        "changed_text_differs_and_carries_the_current_text",
        {"A": "hello\nworld", "B": "rewritten"},
        True,
        {
            "status": "differs",
            "rows": [
                {"name": "A", "kind": "same", "current": "hello\nworld"},
                {"name": "B", "kind": "differs", "current": "rewritten"},
            ],
        },
    ),
    (
        "missing_name_is_not_in_the_library_anymore",
        {"A": "hello\nworld"},
        True,
        {
            "status": "differs",
            "rows": [
                {"name": "A", "kind": "same", "current": "hello\nworld"},
                {"name": "B", "kind": "missing", "current": None},
            ],
        },
    ),
    (
        "library_not_loaded_is_unknown_not_differs",
        {},
        False,
        {
            "status": "unknown",
            "rows": [
                {"name": "A", "kind": "unknown", "current": None},
                {"name": "B", "kind": "unknown", "current": None},
            ],
        },
    ),
    (
        "empty_library_loaded_means_every_pinned_entry_is_missing",
        {},
        True,
        {
            "status": "differs",
            "rows": [
                {"name": "A", "kind": "missing", "current": None},
                {"name": "B", "kind": "missing", "current": None},
            ],
        },
    ),
]

#: (case, pin-ish object, status, expected pinnedBadgeText())
NOTEBOOK_BADGE_CASES = [
    (
        "match_with_token",
        NOTEBOOK_PIN,
        "match",
        "📌 Pinned — captured from image m2_i1_t3 — matches library",
    ),
    (
        "differs_with_token",
        NOTEBOOK_PIN,
        "differs",
        "📌 Pinned — captured from image m2_i1_t3 — differs from current library",
    ),
    (
        "unknown_with_token",
        NOTEBOOK_PIN,
        "unknown",
        "📌 Pinned — captured from image m2_i1_t3 — library not loaded",
    ),
    (
        "no_token_says_saved_image",
        {"source": {}},
        "match",
        "📌 Pinned — captured from a saved image — matches library",
    ),
]

SET_PIN = {
    "format": 1,
    "slug": "cine",
    "name": "Cinematic",
    "set": {
        "format": 1,
        "name": "Cinematic",
        "loras": [
            {"file": "sub\\a.safetensors", "on": True, "strength": 0.8, "strength_clip": None},
            {"file": "b.safetensors", "on": False, "strength": 1.0, "strength_clip": None},
            {"file": "c.safetensors", "strength": 0.5, "strength_clip": 0.25},
        ],
        "trigger_words": "cinematic",
        "notes": "",
    },
    "source": {"token": "m1_t1", "captured": "2026-08-21T10:00:00"},
}

SET_PIN_FORMAT2 = {
    "format": 1,
    "slug": "wan",
    "name": "WAN hi+lo",
    "set": {
        "format": 2,
        "name": "WAN hi+lo",
        "loaders": [
            {
                "loras": [
                    {"file": "hi.safetensors", "on": True, "strength": 0.8, "strength_clip": None}
                ]
            },
            {
                "loras": [
                    {"file": "lo.safetensors", "on": True, "strength": 0.3, "strength_clip": None}
                ]
            },
        ],
        "loras": [{"file": "hi.safetensors", "on": True, "strength": 0.8, "strength_clip": None}],
        "trigger_words": "",
    },
    "source": {"token": "m1_v2_t1", "captured": "t"},
}

#: (case, raw `pinned_state`, expected parsePinnedState() subset -- None, or
#: {slug, name, source} (the `set` dict passes through untouched))
SET_PARSE_CASES = [
    ("empty_string_is_live", "", None),
    ("legacy_any_tag_is_live", "(any)", None),
    ("legacy_path_id_is_live", "12:3", None),
    ("not_json_is_live", "nope", None),
    ("no_set_key_is_live", json.dumps({"format": 1, "slug": "x"}), None),
    ("set_without_rows_is_live", json.dumps({"set": {"name": "x"}}), None),
    (
        "full_pin",
        json.dumps(SET_PIN),
        {
            "format": 1,
            "slug": "cine",
            "name": "Cinematic",
            "source": {"token": "m1_t1", "captured": "2026-08-21T10:00:00"},
        },
    ),
    (
        "name_falls_back_to_the_set_name_then_the_slug",
        json.dumps({"slug": "s1", "set": {"name": "From Set", "loras": []}}),
        {"format": None, "slug": "s1", "name": "From Set", "source": {"token": "", "captured": ""}},
    ),
    (
        "name_falls_back_to_the_slug",
        json.dumps({"slug": "s1", "set": {"loras": []}}),
        {"format": None, "slug": "s1", "name": "s1", "source": {"token": "", "captured": ""}},
    ),
    (
        "format2_loaders_only_is_a_pin",
        json.dumps(SET_PIN_FORMAT2),
        {
            "format": 1,
            "slug": "wan",
            "name": "WAN hi+lo",
            "source": {"token": "m1_v2_t1", "captured": "t"},
        },
    ),
]

#: (case, set dict, expected pinnedRowsSummary())
SET_SUMMARY_CASES = [
    (
        "enabled_rows_stem_strength_clip_suffix_only_when_distinct",
        SET_PIN["set"],
        "a 0.8 · c 0.5/0.25",
    ),
    ("format2_lists_per_loader", SET_PIN_FORMAT2["set"], "L0 hi 0.8 / L1 lo 0.3"),
    ("no_enabled_rows", {"loras": [{"file": "x.st", "on": False}]}, "no enabled loras"),
    ("empty_set", {"loras": []}, "no enabled loras"),
    ("strength_defaults_to_1", {"loras": [{"file": "dir/x.safetensors"}]}, "x 1"),
    (
        "equal_clip_strength_is_not_repeated",
        {"loras": [{"file": "x.st", "strength": 0.5, "strength_clip": 0.5}]},
        "x 0.5",
    ),
]

#: (case, pinned set, current set payload, expected comparePinnedSet())
SET_COMPARE_CASES = [
    ("identical_is_match", SET_PIN["set"], SET_PIN["set"], "match"),
    (
        "separator_spelling_and_trigger_whitespace_are_not_drift",
        SET_PIN["set"],
        {
            "loras": [
                {"file": "sub/a.safetensors", "on": True, "strength": 0.8},
                {"file": "b.safetensors", "on": False, "strength": 1},
                {"file": "c.safetensors", "strength": 0.5, "strength_clip": 0.25},
            ],
            "trigger_words": " cinematic ",
            "notes": "different notes are display-only",
            "name": "Renamed",
        },
        "match",
    ),
    (
        "strength_change_differs",
        SET_PIN["set"],
        {
            "loras": [
                {"file": "sub/a.safetensors", "strength": 0.9},
                {"file": "b.safetensors", "on": False},
                {"file": "c.safetensors", "strength": 0.5, "strength_clip": 0.25},
            ],
            "trigger_words": "cinematic",
        },
        "differs",
    ),
    (
        "toggle_flip_differs",
        SET_PIN["set"],
        {
            "loras": [
                {"file": "sub/a.safetensors", "strength": 0.8},
                {"file": "b.safetensors", "on": True},
                {"file": "c.safetensors", "strength": 0.5, "strength_clip": 0.25},
            ],
            "trigger_words": "cinematic",
        },
        "differs",
    ),
    (
        "row_order_is_apply_order_so_reorder_differs",
        {"loras": [{"file": "a.st"}, {"file": "b.st"}]},
        {"loras": [{"file": "b.st"}, {"file": "a.st"}]},
        "differs",
    ),
    (
        "trigger_words_change_differs",
        {"loras": [{"file": "a.st"}], "trigger_words": "x"},
        {"loras": [{"file": "a.st"}], "trigger_words": "y"},
        "differs",
    ),
    ("format2_vs_identical_format2_match", SET_PIN_FORMAT2["set"], SET_PIN_FORMAT2["set"], "match"),
    (
        "format2_second_loader_change_differs",
        SET_PIN_FORMAT2["set"],
        {
            "format": 2,
            "loaders": [
                {"loras": [{"file": "hi.safetensors", "strength": 0.8}]},
                {"loras": [{"file": "lo.safetensors", "strength": 0.4}]},
            ],
            "loras": [{"file": "hi.safetensors", "strength": 0.8}],
        },
        "differs",
    ),
    (
        "single_loader_format2_equals_format1_of_the_same_rows",
        {
            "format": 2,
            "loaders": [{"loras": [{"file": "a.st", "strength": 0.8}]}],
            "loras": [{"file": "a.st", "strength": 0.8}],
        },
        {"loras": [{"file": "a.st", "strength": 0.8}]},
        "match",
    ),
    ("null_current_differs", SET_PIN["set"], None, "differs"),
]

#: (case, pin, status, expected pinnedStateBadgeText())
SET_BADGE_CASES = [
    (
        "match",
        SET_PIN,
        "match",
        "📌 Pinned state: Cinematic — captured from image m1_t1 — matches current state",
    ),
    (
        "differs",
        SET_PIN,
        "differs",
        "📌 Pinned state: Cinematic — captured from image m1_t1 — differs from current state",
    ),
    (
        "missing_404",
        SET_PIN,
        "missing",
        "📌 Pinned state: Cinematic — captured from image m1_t1 — state no longer exists",
    ),
    (
        "error",
        SET_PIN,
        "error",
        "📌 Pinned state: Cinematic — captured from image m1_t1 — current state unavailable",
    ),
    (
        "pending",
        SET_PIN,
        "pending",
        "📌 Pinned state: Cinematic — captured from image m1_t1 — checking current state…",
    ),
    (
        "no_token_no_name_falls_back",
        {"slug": "s1", "source": {}},
        "match",
        "📌 Pinned state: s1 — captured from a saved image — matches current state",
    ),
]

#: (case, pinned_state raw, mirrors value, expected migrateLegacyMirrorsValue())
#: -- the coordinator's required cases (2026-08-22) plus the edges.
MIGRATE_CASES = [
    ("any_tag_moved", "(any)", "(any)", {"pinned": "", "mirrors": "(any)", "migrated": True}),
    ("path_id_moved", "12:3", "(any)", {"pinned": "", "mirrors": "12:3", "migrated": True}),
    (
        "bare_id_moved_over_an_unset_mirrors",
        "12",
        None,
        {"pinned": "", "mirrors": "12", "migrated": True},
    ),
    (
        "label_moved",
        "Power Lora Loader (rgthree) #7",
        "(any)",
        {"pinned": "", "mirrors": "Power Lora Loader (rgthree) #7", "migrated": True},
    ),
    ("empty_untouched", "", "(any)", {"pinned": "", "mirrors": "(any)", "migrated": False}),
    (
        "valid_pin_json_untouched",
        json.dumps(SET_PIN),
        "(any)",
        {"pinned": json.dumps(SET_PIN), "mirrors": "(any)", "migrated": False},
    ),
    (
        "malformed_pin_json_with_format_key_is_left_to_the_backend",
        '{"format": 1}',
        "(any)",
        {"pinned": '{"format": 1}', "mirrors": "(any)", "migrated": False},
    ),
    (
        "malformed_pin_json_with_set_key_is_left_to_the_backend",
        '{"set": {}}',
        "(any)",
        {"pinned": '{"set": {}}', "mirrors": "(any)", "migrated": False},
    ),
    (
        "mirrors_already_non_default_leaves_pinned_alone",
        "(any)",
        "PLL #4",
        {"pinned": "(any)", "mirrors": "PLL #4", "migrated": False},
    ),
    (
        "non_string_pinned_untouched",
        None,
        "(any)",
        {"pinned": "", "mirrors": "(any)", "migrated": False},
    ),
]

PROBE_JS = """
import * as nb from './extensions/comfyui-epsnodes/lora_library/notebook.js'
import * as sets from './extensions/comfyui-epsnodes/lora_library/sets.js'

const nbPin = nb.parsePinned(%(notebook_pin_raw)s)
const out = {
  exports: {
    parsePinned: typeof nb.parsePinned === 'function',
    pinnedDrift: typeof nb.pinnedDrift === 'function',
    pinnedBadgeText: typeof nb.pinnedBadgeText === 'function',
    attachNotebookWidget: typeof nb.attachNotebookWidget === 'function',
    parsePinnedState: typeof sets.parsePinnedState === 'function',
    pinnedRowsSummary: typeof sets.pinnedRowsSummary === 'function',
    comparePinnedSet: typeof sets.comparePinnedSet === 'function',
    pinnedStateBadgeText: typeof sets.pinnedStateBadgeText === 'function',
    migrateLegacyMirrorsValue: typeof sets.migrateLegacyMirrorsValue === 'function',
    normalizedRows: typeof sets.normalizedRows === 'function',
    loraStem: typeof sets.loraStem === 'function',
    attachApplySetBehavior: typeof sets.attachApplySetBehavior === 'function'
  },
  notebookParse: %(notebook_parse_inputs)s.map((raw) => nb.parsePinned(raw)),
  notebookDrift: %(notebook_drift_inputs)s.map(
    ([map, loaded]) => nb.pinnedDrift(nbPin, map, loaded)
  ),
  notebookBadge: %(notebook_badge_inputs)s.map(([pin, status]) => nb.pinnedBadgeText(pin, status)),
  setParse: %(set_parse_inputs)s.map((raw) => {
    const parsed = sets.parsePinnedState(raw)
    if (!parsed) return null
    const { set, ...rest } = parsed
    return { ...rest, setIsObject: !!set && typeof set === 'object' }
  }),
  setSummary: %(set_summary_inputs)s.map((set) => sets.pinnedRowsSummary(set)),
  setCompare: %(set_compare_inputs)s.map(([a, b]) => sets.comparePinnedSet(a, b)),
  setBadge: %(set_badge_inputs)s.map(([pin, status]) => sets.pinnedStateBadgeText(pin, status)),
  migrate: %(migrate_inputs)s.map(
    ([pinned, mirrors]) => sets.migrateLegacyMirrorsValue(pinned, mirrors)
  ),
  stems: ['sub\\\\a.safetensors', 'dir/b.ckpt', 'plain', ''].map((f) => sets.loraStem(f))
}
process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def m3_api(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Runs the probe against the REAL notebook.js + sets.js in a served-
    layout tmp dir and returns its JSON output."""
    layout = tmp_path_factory.mktemp("web_root")
    module_dir = layout / "extensions" / "comfyui-epsnodes" / "lora_library"
    module_dir.mkdir(parents=True)
    for src in (NOTEBOOK_JS, SETS_JS, API_JS, VERSION_JS):
        shutil.copyfile(src, module_dir / src.name)
    scripts = layout / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "api.js").write_text(
        "export const api = { fetchApi: () => {}, apiURL: (p) => p, addEventListener: () => {} }\n",
        encoding="utf-8",
    )
    (scripts / "app.js").write_text("export const app = {}\n", encoding="utf-8")

    probe = layout / "probe.mjs"
    probe.write_text(
        PROBE_JS
        % {
            "notebook_pin_raw": json.dumps(json.dumps(NOTEBOOK_PIN)),
            "notebook_parse_inputs": json.dumps([raw for _, raw, _ in NOTEBOOK_PARSE_CASES]),
            "notebook_drift_inputs": json.dumps(
                [[m, loaded] for _, m, loaded, _ in NOTEBOOK_DRIFT_CASES]
            ),
            "notebook_badge_inputs": json.dumps(
                [[pin, status] for _, pin, status, _ in NOTEBOOK_BADGE_CASES]
            ),
            "set_parse_inputs": json.dumps([raw for _, raw, _ in SET_PARSE_CASES]),
            "set_summary_inputs": json.dumps([s for _, s, _ in SET_SUMMARY_CASES]),
            "set_compare_inputs": json.dumps([[a, b] for _, a, b, _ in SET_COMPARE_CASES]),
            "set_badge_inputs": json.dumps(
                [[pin, status] for _, pin, status, _ in SET_BADGE_CASES]
            ),
            "migrate_inputs": json.dumps([[p, m] for _, p, m, _ in MIGRATE_CASES]),
        },
        encoding="utf-8",
    )
    result = subprocess.run(
        [NODE, str(probe)], capture_output=True, text=True, timeout=60, cwd=layout
    )
    assert result.returncode == 0, (
        f"probe failed (the modules must IMPORT under Node):\n{result.stderr}"
    )
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def notebook_source() -> str:
    return NOTEBOOK_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def sets_source() -> str:
    return SETS_JS.read_text(encoding="utf-8")


def _function_body(source_text: str, signature: str) -> str:
    """Body of a top-level ``function <signature> {`` (an `export`/`async`
    prefix is not part of *signature*) up to its column-0 closing brace --
    the sibling JS test files' identical helper."""
    start_match = re.search(re.escape(f"function {signature} {{") + r"\n", source_text)
    assert start_match, f"function {signature} {{ not found"
    start = start_match.end()
    end_match = re.search(r"\n\}\n", source_text[start:])
    assert end_match, f"end of {signature} not found"
    return source_text[start : start + end_match.start()]


# ------------------------------------------------------------ module loading


@pytest.mark.parametrize("path", [NOTEBOOK_JS, SETS_JS, CROSS_SWEEP_JS])
def test_modules_parse_as_es_modules(path: Path) -> None:
    """`node --check <file.js>` parses a bare .js as CommonJS and let a
    backtick inside notebook.js's CSS template literal through; checking
    the source as an ES MODULE is what the browser actually does."""
    result = subprocess.run(
        [NODE, "--check", "--input-type=module"],
        input=path.read_text(encoding="utf-8"),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_pure_helpers_are_exported(m3_api: dict) -> None:
    assert m3_api["exports"] == {key: True for key in m3_api["exports"]}


# ------------------------------------------------------------ notebook pure


def test_parse_pinned_cases(m3_api: dict) -> None:
    for (name, _raw, expected), got in zip(
        NOTEBOOK_PARSE_CASES, m3_api["notebookParse"], strict=True
    ):
        assert got == expected, name


def test_pinned_drift_cases(m3_api: dict) -> None:
    for (name, _map, _loaded, expected), got in zip(
        NOTEBOOK_DRIFT_CASES, m3_api["notebookDrift"], strict=True
    ):
        assert got == expected, name


def test_pinned_badge_text_variants(m3_api: dict) -> None:
    """The three user-facing badge variants (README/FORMAT §6.1 contract)."""
    for (name, _pin, _status, expected), got in zip(
        NOTEBOOK_BADGE_CASES, m3_api["notebookBadge"], strict=True
    ):
        assert got == expected, name


# ------------------------------------------------------------ sets pure


def test_parse_pinned_state_cases(m3_api: dict) -> None:
    for (name, _raw, expected), got in zip(SET_PARSE_CASES, m3_api["setParse"], strict=True):
        if expected is None:
            assert got is None, name
        else:
            assert got == {**expected, "setIsObject": True}, name


def test_pinned_rows_summary_cases(m3_api: dict) -> None:
    for (name, _set, expected), got in zip(SET_SUMMARY_CASES, m3_api["setSummary"], strict=True):
        assert got == expected, name


def test_compare_pinned_set_cases(m3_api: dict) -> None:
    for (name, _a, _b, expected), got in zip(SET_COMPARE_CASES, m3_api["setCompare"], strict=True):
        assert got == expected, name


def test_pinned_state_badge_text_variants(m3_api: dict) -> None:
    """The five verdict variants (README/FORMAT §6.2 contract)."""
    for (name, _pin, _status, expected), got in zip(
        SET_BADGE_CASES, m3_api["setBadge"], strict=True
    ):
        assert got == expected, name


def test_lora_stem_is_basename_without_extension_across_separators(m3_api: dict) -> None:
    assert m3_api["stems"] == ["a", "b", "plain", ""]


def test_migrate_legacy_mirrors_value_cases(m3_api: dict) -> None:
    """Coordinator-required (2026-08-22): the pre-M3 positional shift of the
    `mirrors loader` value into the new `pinned_state` widget is decided by
    a pure helper -- "(any)" / "12:3" moved, "" untouched, valid pin JSON
    untouched, a non-default mirrors value leaves pinned alone."""
    for (name, _p, _m, expected), got in zip(MIGRATE_CASES, m3_api["migrate"], strict=True):
        assert got == expected, name


# ------------------------------------------------------- notebook.js wiring


class TestNotebookPinWiring:
    def test_pinned_widget_is_hidden_with_both_flags_like_file(self, notebook_source: str) -> None:
        """§7.5: canvas reads `widget.hidden`, Vue reads `options.hidden`;
        hideFileWidget()'s exact pair, on the new widget."""
        body = _function_body(notebook_source, "hidePinnedWidget(state)")
        assert "widget.hidden = true" in body
        assert "widget.options = { ...(widget.options || {}), hidden: true }" in body
        assert "if (!widget) return" in body  # an older backend: no-op
        attach = notebook_source.split("export function attachNotebookWidget(node)", 1)[1].split(
            "\n}\n", 1
        )[0]
        assert "const pinnedWidget = findWidget(node, PINNED_WIDGET_NAME) || null" in attach
        assert "hidePinnedWidget(state)" in attach and "wirePinnedWidget(state)" in attach
        assert "const PINNED_WIDGET_NAME = 'pinned'" in notebook_source

    def test_pin_arrives_through_configure_reconcile_without_a_click(
        self, notebook_source: str
    ) -> None:
        """configure() assigns widget.value directly (no callback), so the
        existing wireConfigureReload hook is the reconcile point; the
        loadToken/restore-race guards of that function stay intact."""
        block = _function_body(notebook_source, "wireConfigureReload(state)")
        assert "syncPinnedFromWidget(state)" in block
        # the existing restore pins still hold inside the same function
        assert "if (restored !== state.file || state.loadError) {" in block
        assert "state.configureReloaded = true" in block
        assert "state.lastKnownFileValue = restored" in block
        sync = _function_body(notebook_source, "syncPinnedFromWidget(state)")
        assert "if (raw === state.pinnedRaw) return false" in sync  # idempotent
        assert "state.pinned = parsePinned(raw)" in sync
        assert "applyPinnedView(state)" in sync
        # the widget callback is CHAINED, never replaced
        wire = _function_body(notebook_source, "wirePinnedWidget(state)")
        assert "const original = widget.callback" in wire
        assert "original.apply(this, [value, ...rest])" in wire
        assert "syncPinnedFromWidget(state)" in wire

    def test_badge_row_and_unpin_button(self, notebook_source: str) -> None:
        bar = _function_body(notebook_source, "renderPinBar(state)")
        assert "pinnedBadgeText(pin, drift.status)" in bar
        assert (
            "pinnedDrift(pin, state.entryTextByName, state.file != null && !state.loadError)" in bar
        )
        assert "text: 'Unpin'" in bar
        assert "unpinBtn.addEventListener('click', () => unpin(state))" in bar
        assert "bar.replaceChildren()" in bar  # empty (hidden) when not pinned
        assert "state.pinBarEl = el('div', { className: 'llnb-pinbar' })" in notebook_source
        assert "[filePanel, state.pinBarEl, panesRow]" in notebook_source
        assert ".llnb-pinbar:empty { display: none; }" in notebook_source

    def test_unpin_writes_empty_through_value_plus_callback_and_toasts(
        self, notebook_source: str
    ) -> None:
        body = _function_body(notebook_source, "unpin(state)")
        assert "widget.value = ''" in body
        assert "widget.callback?.('')" in body
        assert body.index("widget.value = ''") < body.index("widget.callback?.('')")
        assert (
            "toast('info', 'Unpinned — back to the live notebook', "
            "'The node reads the current notebook file again.')"
            in body
        )
        # leaving the pinned view reloads the LIVE editor (selection + mtime from the file)
        view = _function_body(notebook_source, "applyPinnedView(state)")
        assert (
            "reloadNow(state).catch((error) => api.warn('reload after unpin failed', error))"
            in view
        )

    def test_pinned_list_is_read_only_with_drift_markers(self, notebook_source: str) -> None:
        render = notebook_source.split("function renderList(state)", 1)[1].split("\n/**", 1)[0]
        assert "if (isPinned(state)) {" in render and "renderPinnedList(state)" in render
        # the pinned branch returns BEFORE any live row / dragRows push
        assert render.index("renderPinnedList(state)") < render.index(
            "const words = searchWords(searchQuery)"
        )
        rows = _function_body(notebook_source, "buildPinnedEntryRow(state, entry, driftRow)")
        assert "text: '≠'" in rows
        assert "Not in the library anymore" in rows
        assert "Current text starts: ${firstLineOf(driftRow.current) || '(empty)'}" in rows
        assert "onEntryPointerDown" not in rows and "dblclick" not in rows  # no drag, no rename
        assert "selectPinnedEntry(state, entry.name)" in rows
        pinned_list = _function_body(notebook_source, "renderPinnedList(state)")
        assert "dragRows" not in pinned_list

    def test_editor_shows_the_old_text_read_only(self, notebook_source: str) -> None:
        paint = _function_body(notebook_source, "paintPinnedEditor(state)")
        assert "state.textarea.value = entry.text" in paint
        assert "state.textarea.readOnly = true" in paint
        assert "state.textarea.disabled = false" in paint  # legible + copyable, not greyed
        assert "state.nameFieldEl.readOnly = true" in paint
        assert "setDirty(state, false)" in paint
        clear = _function_body(notebook_source, "clearPinnedEditorLook(state)")
        assert "state.textarea.readOnly = false" in clear
        reset = _function_body(notebook_source, "resetEditorDom(state)")
        assert "clearPinnedEditorLook(state)" in reset
        populate = _function_body(notebook_source, "populateEditor(state, text, mtime, name)")
        assert "clearPinnedEditorLook(state)" in populate
        # The load's editor half paints the PINNED editor instead of fetching
        # the live entry. NAS round (2026-08-22): reloadNow's post-fetch body
        # moved into applyNotebookPayload (shared with the session-cache
        # instant paint) -> loadActiveEditor, which reloadNow's `unchanged`
        # branch also re-runs; the pin follows the code it guards.
        editor = _function_body(notebook_source, "loadActiveEditor(state)")
        assert (
            "if (isPinned(state)) {\n    renderPinBar(state)\n"
            "    paintPinnedEditor(state)\n    return\n  }"
            in editor
        )
        assert editor.index("paintPinnedEditor(state)") < editor.index(
            "loadEntryText(state, state.activeName)"
        )
        apply = _function_body(notebook_source, "applyNotebookPayload(state, file, data)")
        assert "await loadActiveEditor(state)" in apply
        reload = _function_body(notebook_source, "reloadNow(state)")
        assert "await applyNotebookPayload(state, file, data)" in reload
        assert "applyNotebookPayload(state, file, cached.payload)" in reload

    @pytest.mark.parametrize(
        "signature",
        [
            "openNewEntryRow(state)",
            "onDeleteClick(state)",
            "beginInlineRename(state, kind, name)",
            "onEntryDoubleClick(state, event, name)",
        ],
    )
    def test_mutation_entry_points_refuse_while_pinned(
        self, notebook_source: str, signature: str
    ) -> None:
        assert "if (pinnedRefuse(state)) return" in _function_body(notebook_source, signature)

    @pytest.mark.parametrize(
        "signature",
        [
            "performSave(state, { force = false } = {})",
            "performMove(state, name, target, { force = false } = {})",
            "performMoveRun(state, names, target, startIndex, { force = false } = {})",
            "performMoveCategory(state, category, target, { force = false } = {})",
            "confirmNewEntry(state, rawName)",
            "commitInlineRename(state)",
        ],
    )
    def test_async_mutation_paths_refuse_while_pinned(
        self, notebook_source: str, signature: str
    ) -> None:
        body = _function_body(notebook_source, signature)
        assert "pinnedRefuse(state)" in body
        assert body.index("pinnedRefuse(state)") < body.index("writesBlocked(state)")

    def test_pointer_and_keyboard_flows_are_gated(self, notebook_source: str) -> None:
        for signature in (
            "onEntryPointerDown(state, event, name)",
            "onCategoryPointerDown(state, event, category)",
            "handleEntryClick(state, name, modifiers)",
        ):
            assert "if (isPinned(state)) return" in _function_body(notebook_source, signature), (
                signature
            )
        footer = _function_body(notebook_source, "renderFooter(state)")
        assert "newBtn.disabled = true" in footer
        assert "Unavailable while pinned — Unpin (above) to edit the live notebook" in footer
        assert footer.count("if (pinnedRefuse(state)) return") == 2  # New + Delete click handlers
        assert "|| isPinned(state)" in _function_body(
            notebook_source, "updateSaveButtonEnabled(state)"
        )
        assert "|| isPinned(state)" in _function_body(
            notebook_source, "updateDeleteButtonEnabled(state)"
        )
        refuse = _function_body(notebook_source, "pinnedRefuse(state)")
        assert "Read-only while pinned — click Unpin (above) to edit the live notebook." in refuse

    def test_panel_makes_room_for_the_bar(self, notebook_source: str) -> None:
        """§7.2 sizing laws: getMinHeight grows by the bar while pinned and the
        node is lifted once (never below the computed floor; Float32Array-safe)."""
        assert (
            "getMinHeight: () => MIN_WIDGET_HEIGHT + "
            "(state && isPinned(state) ? PIN_BAR_HEIGHT : 0)"
            in notebook_source
        )
        grow = _function_body(notebook_source, "syncPinnedNodeHeight(state)")
        assert "if (pinned === state.pinGrown) return" in grow
        assert "node.setSize([node.size[0], Math.max(node.size[1] + delta, floor)])" in grow
        assert "Array.isArray" not in grow

    def test_no_new_window_listeners_and_capture_phase_rule_holds(
        self, notebook_source: str
    ) -> None:
        """§7.5: the pin code adds element-level listeners only; every window
        pointer listener in the file stays capture-phase (the Vue wrapper
        stops bubbling)."""
        marker = "// Pinned values (FORMAT.md §6.1/§7.2, provenance M3)"
        assert marker in notebook_source
        section = notebook_source.split(marker, 1)[1]
        section = section.split("// Status line + conflict UI", 1)[0]
        assert "window.addEventListener" not in section
        assert not re.findall(r"window\.addEventListener\('pointer\w+', \w+\)", notebook_source)


# ------------------------------------------------------------ sets.js wiring


class TestSetsPinWiring:
    def test_pinned_state_widget_is_hidden_with_both_flags(self, sets_source: str) -> None:
        body = _function_body(sets_source, "hidePinnedStateWidget(node)")
        assert "widget.hidden = true" in body
        assert "widget.options = { ...(widget.options || {}), hidden: true }" in body
        assert "const PINNED_STATE_WIDGET_NAME = 'pinned_state'" in sets_source

    def test_attach_is_a_no_op_without_the_backend_widget_and_runs_after_mirrors(
        self, sets_source: str
    ) -> None:
        """The DOM row must land AFTER `mirrors loader`: litegraph's save
        leaves a hole at a serialize:false widget's index and restore walks
        a counter that skips it (controller.js's ordering citation)."""
        attach = _function_body(sets_source, "attachApplySetBehavior(node)")
        assert attach.index("attachMirrorsWidget(node)") < attach.index("attachPinBehavior(node)")
        behavior = _function_body(sets_source, "attachPinBehavior(node)")
        assert "if (!findWidgetByName(node, PINNED_STATE_WIDGET_NAME)) return" in behavior
        for call in (
            "attachPinBadge(node)",
            "hidePinnedStateWidget(node)",
            "wirePinnedStateWidget(node, state)",
            "wirePinConfigureSync(node, state)",
            "syncPinFromWidget(state)",
        ):
            assert call in behavior, call

    def test_badge_is_a_display_only_dom_widget_sized_by_the_dom_widget_laws(
        self, sets_source: str
    ) -> None:
        """§7.2 DOM-widget laws (cross_sweep.js's readout idiom): computeSize +
        computedHeight + an explicit element height, every reported height
        = text + 2*margin; both serialize flags off; hidden (both flags +
        zero height) until pinned; Vue-safe (no canvas drawing)."""
        body = _function_body(sets_source, "attachPinBadge(node)")
        assert "node.addDOMWidget(PIN_WIDGET_NAME, PIN_WIDGET_TYPE, root, {" in body
        assert "serialize: false" in body and "domWidget.serialize = false" in body
        assert "domWidget.serializeValue = () => undefined" in body
        assert "root.style.height = `${PIN_ROW_HEIGHT}px`" in body
        assert "state.outerHeight = PIN_ROW_HEIGHT + 2 * margin" in body
        assert (
            "domWidget.computeSize = (width) => [width, state.visible ? state.outerHeight : 0]"
            in body
        )
        assert "if (node.__epsSetsPin) return node.__epsSetsPin" in body  # idempotent
        assert "unpinBtn.addEventListener('click', () => unpinState(state))" in body
        visible = _function_body(sets_source, "setPinRowVisible(state, visible)")
        assert "widget.hidden = !visible" in visible
        assert "widget.options = { ...(widget.options || {}), hidden: !visible }" in visible
        assert "widget.computedHeight = visible ? state.outerHeight : 0" in visible
        assert "state.root.style.display = visible ? '' : 'none'" in visible
        marker = "// Pinned state (FORMAT.md §6.2, provenance M3)"
        assert marker in sets_source
        # the pin section runs from its banner to attachApplySetBehavior()'s doc
        section = sets_source.split(marker, 1)[1].split("/**\n * Per-instance hook", 1)[0]
        assert "attachPinBehavior(node)" in section  # the slice really is the section
        assert "onDrawForeground" not in section and "onMouseDown" not in section
        assert "window.addEventListener" not in section

    def test_drift_fetches_the_current_set_once_and_maps_404(self, sets_source: str) -> None:
        body = _function_body(sets_source, "fetchPinDrift(state)")
        assert "api.getJson('/lora_library/set', { slug })" in body
        assert "state.driftStatus = comparePinnedSet(state.pin.set, data)" in body
        assert "error?.status === 404 ? 'missing' : 'error'" in body
        assert "if (token !== state.driftToken) return" in body  # a newer pin/unpin wins
        sync = _function_body(sets_source, "syncPinFromWidget(state)")
        assert "if (raw === state.pinnedRaw) return false" in sync
        assert "state.driftToken += 1" in sync
        assert "fetchPinDrift(state)" in sync

    def test_badge_text_summary_and_inactive_combo(self, sets_source: str) -> None:
        view = _function_body(sets_source, "applyPinView(state)")
        assert "pinnedStateBadgeText(state.pin, status)" in view
        assert "pinnedRowsSummary(state.pin.set)" in view
        assert "markSetComboInactive(state, pinned)" in view
        combo = _function_body(sets_source, "markSetComboInactive(state, pinned)")
        assert "widget.label = SET_COMBO_PINNED_LABEL" in combo
        assert "widget.disabled = true" in combo
        assert "widget.tooltip = SET_COMBO_PINNED_TOOLTIP" in combo
        assert "state.setWidgetOriginal = null" in combo  # restored on unpin
        assert "const SET_COMBO_PINNED_LABEL = 'set (ignored while pinned)'" in sets_source
        assert "const SET_COMBO_PINNED_TOOLTIP = 'ignored while pinned'" in sets_source

    def test_unpin_writes_empty_through_value_plus_callback_and_toasts(
        self, sets_source: str
    ) -> None:
        body = _function_body(sets_source, "unpinState(state)")
        assert "widget.value = ''" in body
        assert "widget.callback?.('')" in body
        assert body.index("widget.value = ''") < body.index("widget.callback?.('')")
        assert (
            "toastInfo('Unpinned — back to the live state', "
            "'The node applies the state selected in the dropdown again.')"
            in body
        )

    def test_configure_reconcile_is_chained_and_heals_the_legacy_shift_first(
        self, sets_source: str
    ) -> None:
        wire = _function_body(sets_source, "wirePinConfigureSync(node, state)")
        assert "const original = node.onConfigure" in wire
        assert "original.apply(this, args)" in wire
        assert wire.index("healLegacyWidgetShift(node)") < wire.index("syncPinFromWidget(state)")
        cb = _function_body(sets_source, "wirePinnedStateWidget(node, state)")
        assert "const original = widget.callback" in cb and "syncPinFromWidget(state)" in cb

    def test_legacy_migration_writes_through_the_widget_idiom_and_logs_once(
        self, sets_source: str
    ) -> None:
        """Coordinator-required (2026-08-22): move the shifted mirrors value
        back via value + callback, blank pinned_state, console.info once per
        node, no toast."""
        heal = _function_body(sets_source, "healLegacyWidgetShift(node)")
        assert "migrateLegacyMirrorsValue(pinnedWidget.value, mirrorsWidget.value)" in heal
        assert "mirrorsWidget.value = verdict.mirrors" in heal
        assert "mirrorsWidget.callback?.(verdict.mirrors)" in heal
        assert "pinnedWidget.value = verdict.pinned" in heal
        assert "node.__epsSetsLegacyMigrated" in heal and "console.info(" in heal
        assert "toast" not in heal.lower()
        pure = _function_body(sets_source, "migrateLegacyMirrorsValue(pinnedRaw, mirrorsValue)")
        assert (
            "if (mirrorsValue != null && mirrorsValue !== MIRRORS_ANY_VALUE) return untouched"
            in pure
        )
        assert "if (parsePinnedState(pinnedRaw)) return untouched" in pure
        assert "return { pinned: '', mirrors: pinnedRaw, migrated: true }" in pure
        # the mirrors combo keeps being appended LAST (file header rule)
        assert "attachMirrorsWidget(node)" in _function_body(
            sets_source, "attachApplySetBehavior(node)"
        )
