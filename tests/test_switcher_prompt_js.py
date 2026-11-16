"""The switcher prompt post-pass (FORMAT.md §6.4 "Disabled rows and the
prompt"; ``web/eps_image/switcher_prompt.js``).

The bug: core scans EVERY provided input of a node for an
``ExecutionBlocker`` before calling it, so a DISABLED row whose source also
runs elsewhere and emits a blocker (an all-off switcher) blocks the whole
switcher. No node-side code can fix that; the fix removes the disabled rows'
links from the API prompt the frontend sends.

* The pure function is driven under Node against tables.
* The rule is checked against the BACKEND's own: for every switcher class and
  every ``toggles`` value in a table, JS strips exactly the rows
  ``check_lazy_status`` would not request.
* The prompt-only helpers and ``execute`` are run on stripped prompts.
* The bug and the fix are proven against core's REAL dispatch
  (``execution._async_map_node_over_list``) when a ComfyUI checkout is
  available (the rig, or ``$COMFYUI_DIR``); that test skips otherwise.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from eps_image import nodes_switcher
from eps_image.nodes_switcher import (
    _SWITCHER_SLOT_PATTERNS,
    EPSClipSwitcher,
    EPSLatentSwitcher,
    EPSModelSwitcher,
    EPSSwitcher,
    EPSVaeSwitcher,
    inputs_fed_by_an_all_off_switcher,
    wired_input_names,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

CLASSES = {
    "EPSSwitcher": EPSSwitcher,
    "EPSModelSwitcher": EPSModelSwitcher,
    "EPSClipSwitcher": EPSClipSwitcher,
    "EPSVaeSwitcher": EPSVaeSwitcher,
    "EPSLatentSwitcher": EPSLatentSwitcher,
}
#: The two classes with a per-row partner socket, and the partner's name.
PARTNER_NAMES = {"EPSSwitcher": "mask_{n}", "EPSModelSwitcher": "model_{n}_low"}
PARTNER_GATES = {"EPSSwitcher": {"pass_masks": [True]}}

#: ``{p}`` is the class's own row prefix.
TOGGLES_TABLE = [
    "",
    "{}",
    "not json",
    "[]",
    '"image_1"',
    "null",
    "42",
    "true",
    '{"{p}_2": false}',
    '{"{p}_1": true}',
    '{"{p}_1": false, "{p}_3": false}',
    '{"{p}_1": false, "{p}_2": false, "{p}_3": false}',
    '{"{p}_2": 0}',
    '{"{p}_2": null}',
    '{"{p}_2": "false"}',
    '{"{p}_2": []}',
    '{"other_2": false}',
    '{"{p}_9": false}',
    '{"{p}_2": false, "{p}_2": true}',
    '{"{p}_2": true, "{p}_2": false}',
    '{"{p}_2": false, "extra": {"nested": false}}',
]

PROBE_JS = r"""
import { stripDisabledSwitcherRows, installPromptPostPass } from './extensions/comfyui-epsnodes/eps_image/switcher_prompt.js'
import { SWITCHER_CLASSES } from './extensions/comfyui-epsnodes/eps_image/switcher.js'

const input = JSON.parse(await new Promise((resolve) => {
  let data = ''
  process.stdin.on('data', (chunk) => { data += chunk })
  process.stdin.on('end', () => resolve(data))
}))
const out = {}
const clone = (value) => JSON.parse(JSON.stringify(value))

out.classes = Object.fromEntries(Object.entries(SWITCHER_CLASSES).map(([k, v]) => [k, v.prefix]))

// Parity table: rows 1..3 (+ partners) wired; what survives per toggles.
out.parity = input.parity.map(({ cls, toggles, names }) => {
  const inputs = { toggles }
  names.forEach((name, i) => { inputs[name] = [String(10 + i), 0] })
  const output = { 1: { class_type: cls, inputs } }
  stripDisabledSwitcherRows(output)
  return Object.keys(output[1].inputs).filter((k) => k !== 'toggles')
})

// Hand cases.
const run = (output) => JSON.stringify(stripDisabledSwitcherRows(clone(output)))
out.cases = input.cases.map((output) => JSON.parse(run(output)))

// Chaining.
const calls = []
const makeApp = (result, { reject } = {}) => {
  const app = {
    marker: 'the-app',
    async graphToPrompt(...args) {
      calls.push(['original', this?.marker, args])
      if (reject) throw reject
      return result()
    }
  }
  return app
}
const fresh = () => ({
  workflow: { nodes: [{ id: 1, widgets_values: ['{"image_2": false}'] }] },
  output: {
    1: { class_type: 'EPSSwitcher', inputs: { image_1: ['5', 0], image_2: ['6', 0], toggles: '{"image_2": false}' } }
  }
})

{
  let produced
  const app = makeApp(() => (produced = fresh()))
  const before = app.graphToPrompt
  // a wrapper installed BEFORE ours (rgthree-style) and one AFTER
  const seenByAfter = []
  const first = app.graphToPrompt
  app.graphToPrompt = async function (...args) { calls.push(['before']); return first.apply(this, args) }
  installPromptPostPass(app)
  installPromptPostPass(app) // second setup: no second wrap
  const ours = app.graphToPrompt
  app.graphToPrompt = async function (...args) {
    const result = await ours.apply(this, args)
    seenByAfter.push(clone(result.output[1].inputs))
    calls.push(['after'])
    return result
  }
  const result = await app.graphToPrompt('graph', { x: 1 })
  out.chain = {
    sameObject: result === produced,
    inputs: result.output[1].inputs,
    workflowUntouched: JSON.stringify(result.workflow) === JSON.stringify(fresh().workflow),
    seenByAfter,
    calls: calls.slice(),
    wrapsAfterDoubleInstall: ours !== before
  }
}
{
  const failure = new Error('boom')
  const app = makeApp(() => fresh(), { reject: failure })
  installPromptPostPass(app)
  try { await app.graphToPrompt(); out.rejects = 'resolved' } catch (error) { out.rejects = error === failure }
}
{
  // Our own pruning throws: result comes back unpruned, a warning is logged.
  const warnings = []
  const realWarn = console.warn
  console.warn = (...args) => warnings.push(args.map(String).join(' '))
  const hostile = fresh()
  hostile.output[2] = { class_type: 'EPSSwitcher', inputs: new Proxy({}, { get() { throw new Error('hostile prompt') }, ownKeys() { throw new Error('hostile prompt') } }) }
  const app = makeApp(() => hostile)
  installPromptPostPass(app)
  const result = await app.graphToPrompt()
  console.warn = realWarn
  out.failure = {
    sameObject: result === hostile,
    keptEveryInput: Object.keys(result.output[1].inputs).sort(),
    warned: warnings.some((line) => line.includes('[eps_image:switcher_prompt]'))
  }
}
{
  // No graphToPrompt at all: nothing to chain, no throw.
  const app = {}
  installPromptPostPass(app)
  out.noOriginal = app.graphToPrompt === undefined
}
process.stdout.write(JSON.stringify(out))
"""


def _names(cls_id: str, prefix: str) -> list[str]:
    names = [f"{prefix}_{n}" for n in (1, 2, 3)]
    if cls_id in PARTNER_NAMES:
        names += [PARTNER_NAMES[cls_id].format(n=n) for n in (1, 2, 3)]
    return names


HAND_CASES = {
    "plain": {
        "1": {
            "class_type": "EPSSwitcher",
            "inputs": {"image_1": ["5", 0], "image_2": ["6", 0], "toggles": '{"image_2": false}'},
        },
        "5": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
    },
    "toggles_wired": {
        "1": {
            "class_type": "EPSSwitcher",
            "inputs": {"image_1": ["5", 0], "image_2": ["6", 0], "toggles": ["7", 0]},
        }
    },
    "toggles_absent": {
        "1": {"class_type": "EPSSwitcher", "inputs": {"image_1": ["5", 0], "image_2": ["6", 0]}}
    },
    "widget_never_touched": {
        "1": {
            "class_type": "EPSSwitcher",
            "inputs": {
                "image_2": "not-a-link",
                "pass_masks": False,
                "toggles": '{"image_2": false, "pass_masks": false}',
            },
        }
    },
    "foreign_class_same_names": {
        "1": {"class_type": "SomethingElse", "inputs": {"image_2": ["6", 0], "toggles": '{"image_2": false}'}}
    },
    "other_prefix_not_stripped": {
        "1": {
            "class_type": "EPSClipSwitcher",
            "inputs": {"image_2": ["6", 0], "clip_2": ["7", 0], "toggles": '{"image_2": false, "clip_2": false}'},
        }
    },
    "partner_follows_row": {
        "1": {
            "class_type": "EPSModelSwitcher",
            "inputs": {
                "model_1": ["5", 0],
                "model_1_low": ["5", 1],
                "model_2": ["6", 0],
                "model_2_low": ["6", 1],
                "toggles": '{"model_2": false}',
            },
        }
    },
    "orphan_partner_of_disabled_row": {
        "1": {
            "class_type": "EPSSwitcher",
            "inputs": {"mask_3": ["6", 0], "image_1": ["5", 0], "toggles": '{"image_3": false}'},
        }
    },
    "subgraph_ids": {
        "7:3": {
            "class_type": "EPSVaeSwitcher",
            "inputs": {"vae_1": ["7:1", 0], "vae_2": ["7:2", 0], "toggles": '{"vae_1": false}'},
        }
    },
    "prototype_class_type": {"1": {"class_type": "constructor", "inputs": {"toggles": "{}"}}},
}


@pytest.fixture(scope="module")
def probe(tmp_path_factory: pytest.TempPathFactory, install_switcher_js) -> dict:
    layout = tmp_path_factory.mktemp("web_root_switcher_prompt")
    install_switcher_js(layout)
    (layout / "probe.mjs").write_text(PROBE_JS, encoding="utf-8")
    parity = []
    for cls_id, cls in CLASSES.items():
        prefix = _SWITCHER_SLOT_PATTERNS[cls_id].pattern.split("_")[0]
        for template in TOGGLES_TABLE:
            parity.append(
                {
                    "cls": cls_id,
                    "toggles": template.replace("{p}", prefix),
                    "names": _names(cls_id, prefix),
                }
            )
        del cls
    payload = {"parity": parity, "cases": list(HAND_CASES.values())}
    result = subprocess.run(
        [NODE, str(layout / "probe.mjs")],
        input=json.dumps(payload),
        capture_output=True,
        encoding="utf-8",
        timeout=60,
        cwd=layout,
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    data = json.loads(result.stdout)
    data["parity_in"] = parity
    data["hand"] = dict(zip(HAND_CASES, data["cases"], strict=True))
    return data


# --- which classes -----------------------------------------------------------


def test_js_knows_every_switcher_class_python_builds(probe: dict) -> None:
    assert set(probe["classes"]) == set(_SWITCHER_SLOT_PATTERNS)
    assert set(probe["classes"]) == set(CLASSES)
    for cls_id, prefix in probe["classes"].items():
        assert _SWITCHER_SLOT_PATTERNS[cls_id].fullmatch(f"{prefix}_7")


def test_js_partner_names_match_the_ones_python_accepts() -> None:
    for cls_id, cls in CLASSES.items():
        accepted = cls.INPUT_TYPES()["optional"]
        for other_id, template in PARTNER_NAMES.items():
            assert (template.format(n=3) in accepted) is (other_id == cls_id), (cls_id, template)


# --- ONE rule: JS strips exactly what Python would not request ---------------


def test_js_strips_exactly_the_rows_python_does_not_request(probe: dict) -> None:
    mismatches = []
    for case, surviving in zip(probe["parity_in"], probe["parity"], strict=True):
        cls_id = case["cls"]
        kwargs: dict = {name: (None,) for name in case["names"]}
        kwargs.update(PARTNER_GATES.get(cls_id, {}))
        wanted = CLASSES[cls_id]().check_lazy_status(toggles=[case["toggles"]], **kwargs)
        if sorted(surviving) != sorted(wanted):
            mismatches.append((cls_id, case["toggles"], sorted(surviving), sorted(wanted)))
    assert not mismatches


def test_the_extension_installs_the_post_pass_in_setup() -> None:
    extension = (REPO_ROOT / "web" / "eps_image.js").read_text(encoding="utf-8")
    assert "safely('switcherPrompt.install', () => installPromptPostPass(app))" in extension
    assert "  setup() {" in extension


# --- hand cases --------------------------------------------------------------


def test_plain_disabled_row_loses_its_link_only(probe: dict) -> None:
    node = probe["hand"]["plain"]["1"]["inputs"]
    assert node == {"image_1": ["5", 0], "toggles": '{"image_2": false}'}
    assert probe["hand"]["plain"]["5"] == HAND_CASES["plain"]["5"]


def test_nothing_is_stripped_unless_provable_from_the_prompt(probe: dict) -> None:
    for name in ("toggles_wired", "toggles_absent", "foreign_class_same_names", "prototype_class_type"):
        assert probe["hand"][name] == HAND_CASES[name], name


def test_widgets_are_never_touched(probe: dict) -> None:
    assert probe["hand"]["widget_never_touched"] == HAND_CASES["widget_never_touched"]


def test_only_the_nodes_own_prefix_names_a_row(probe: dict) -> None:
    inputs = probe["hand"]["other_prefix_not_stripped"]["1"]["inputs"]
    assert inputs == {"image_2": ["6", 0], "toggles": '{"image_2": false, "clip_2": false}'}


def test_partner_socket_follows_its_rows_toggle(probe: dict) -> None:
    inputs = probe["hand"]["partner_follows_row"]["1"]["inputs"]
    assert set(inputs) == {"model_1", "model_1_low", "toggles"}
    assert set(probe["hand"]["orphan_partner_of_disabled_row"]["1"]["inputs"]) == {"image_1", "toggles"}


def test_subgraph_node_ids_with_colons(probe: dict) -> None:
    assert set(probe["hand"]["subgraph_ids"]["7:3"]["inputs"]) == {"vae_2", "toggles"}


# --- chaining ----------------------------------------------------------------


def test_chain_runs_other_wrappers_and_returns_the_originals_result(probe: dict) -> None:
    chain = probe["chain"]
    assert chain["sameObject"] is True
    assert chain["inputs"] == {"image_1": ["5", 0], "toggles": '{"image_2": false}'}
    assert chain["seenByAfter"] == [chain["inputs"]]
    assert [call[0] for call in chain["calls"]] == ["before", "original", "after"]
    # `this` and every argument reach the original through all three layers.
    assert chain["calls"][1] == ["original", "the-app", ["graph", {"x": 1}]]
    assert chain["workflowUntouched"] is True


def test_installing_twice_wraps_once(probe: dict) -> None:
    assert [call[0] for call in probe["chain"]["calls"]].count("original") == 1
    assert probe["chain"]["wrapsAfterDoubleInstall"] is True


def test_a_rejecting_original_propagates(probe: dict) -> None:
    assert probe["rejects"] is True


def test_our_own_failure_returns_the_unpruned_result_and_warns(probe: dict) -> None:
    failure = probe["failure"]
    assert failure["sameObject"] is True
    assert failure["keptEveryInput"] == ["image_1", "image_2", "toggles"]
    assert failure["warned"] is True


def test_no_graph_to_prompt_is_not_an_error(probe: dict) -> None:
    assert probe["noOriginal"] is True


# --- the server side with the links removed ----------------------------------

STRIPPED = {
    "1": {"class_type": "EPSSwitcher", "inputs": {"image_1": ["5", 0], "toggles": '{"image_2": false}'}},
    "5": {"class_type": "LoadImage", "inputs": {}},
}
FULL = {
    "1": {
        "class_type": "EPSSwitcher",
        "inputs": {"image_1": ["5", 0], "image_2": ["6", 0], "toggles": '{"image_2": false}'},
    },
    "5": {"class_type": "LoadImage", "inputs": {}},
    "6": {"class_type": "EPSSwitcher", "inputs": {"toggles": "{}"}},
}


def test_row_inputs_are_optional_so_core_validation_accepts_a_missing_one() -> None:
    for cls in CLASSES.values():
        types = cls.INPUT_TYPES()
        assert types["required"] == {}
        assert "toggles" in types["optional"]


def test_lazy_and_execute_answer_the_same_with_the_link_removed() -> None:
    sentinel = object()
    full = {"clip_1": [sentinel], "clip_2": (None,)}
    stripped = {"clip_1": [sentinel]}
    for kwargs in (full, stripped):
        assert EPSClipSwitcher().check_lazy_status(toggles=['{"clip_2": false}'], **kwargs) == ["clip_1"]
    assert EPSClipSwitcher().execute(toggles=['{"clip_2": false}'], **stripped) == ([sentinel],)


def test_all_disabled_rows_stripped_still_reads_as_all_off() -> None:
    wired_all_off_full = {
        "class_type": "EPSSwitcher",
        "inputs": {"image_1": ["5", 0], "toggles": '{"image_1": false}'},
    }
    wired_all_off_stripped = {"class_type": "EPSSwitcher", "inputs": {"toggles": '{"image_1": false}'}}
    for upstream in (wired_all_off_full, wired_all_off_stripped):
        prompt = {"9": upstream, "2": {"class_type": "X", "inputs": {"image": ["9", 0]}}}
        assert nodes_switcher._switcher_is_statically_all_off(upstream)
        assert inputs_fed_by_an_all_off_switcher(prompt, "2", ["image"]) == {"image"}


def test_prompt_only_helpers_ignore_stripped_rows() -> None:
    prompt = {
        "1": {"class_type": "EPSSwitcher", "inputs": {"image_1": ["5", 0], "toggles": '{"image_2": false}'}},
        "3": {"class_type": "EPSSwitcher", "inputs": {"image_1": ["1", 0], "image_2": ["4", 0]}},
        "4": {"class_type": "EPSSwitcher", "inputs": {"toggles": "{}"}},
    }
    # the downstream switcher's wired inputs are unchanged by stripping the
    # UPSTREAM switcher's disabled row
    assert wired_input_names(prompt, "3", ["image_1", "image_2"]) == ["image_1", "image_2"]
    assert not nodes_switcher._switcher_is_statically_all_off(prompt["1"])


# --- the bug and the fix, against core's real dispatch ----------------------

def _comfyui_dir() -> Path | None:
    configured = os.environ.get("COMFYUI_DIR")
    candidates = [Path(configured)] if configured else []
    candidates.append(Path.home() / "Library/Application Support/comfy_ps/rig/ComfyUI")
    for candidate in candidates:
        if (candidate / "execution.py").is_file():
            return candidate
    return None


CORE_DRIVER = r"""
import asyncio, json, sys, types
comfy_dir, repo, payload = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
sys.argv = ['x', '--cpu']
sys.path[:0] = [comfy_dir, repo]
import execution
import nodes
from comfy_execution.graph import ExecutionBlocker
from eps_image.nodes_switcher import EPSSwitcher


class FakeSource:
    RETURN_TYPES = ('IMAGE',)
    FUNCTION = 'run'
    CATEGORY = 'test'

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {}}

    def run(self):
        return ('x',)


nodes.NODE_CLASS_MAPPINGS['EPSSwitcher'] = EPSSwitcher
nodes.NODE_CLASS_MAPPINGS['FakeSource'] = FakeSource


class Cache:
    def __init__(self, outputs):
        self.outputs = outputs


class ExecList:
    def __init__(self, outputs):
        self.outputs = outputs

    def get_cache(self, from_id, to_id):
        out = self.outputs.get(str(from_id))
        return None if out is None else Cache(out)


class Dyn:
    def __init__(self, prompt):
        self.prompt = prompt

    def get_original_prompt(self):
        return self.prompt


def describe(result):
    if isinstance(result, ExecutionBlocker):
        return 'BLOCKED'
    if isinstance(result, tuple):
        return [describe(r) if isinstance(r, ExecutionBlocker) else r for r in result[0]]
    return [describe(r) if isinstance(r, ExecutionBlocker) else r for r in result]


async def drive(prompt):
    # Source 5 is enabled; source 6 feeds a disabled row but ALSO ran for
    # another node and emitted a blocker, so its cached output is one.
    cache = ExecList({'5': [['imgA']], '6': [[ExecutionBlocker(None)]]})
    node = prompt['1']
    data, _, _ = execution.get_input_data(node['inputs'], EPSSwitcher, '1', cache, Dyn(prompt))
    lazy = await execution._async_map_node_over_list('p', '1', EPSSwitcher(), data, 'check_lazy_status')
    run = await execution._async_map_node_over_list('p', '1', EPSSwitcher(), data, 'execute')
    valid = await execution.validate_inputs('p', prompt, '1', {})
    return {'lazy': describe(lazy[0]), 'execute': describe(run[0]), 'valid': bool(valid[0])}


async def main():
    return {name: await drive(prompt) for name, prompt in payload.items()}


print('RESULT' + json.dumps(asyncio.run(main())))
"""


PROMPT = {
    "1": {
        "class_type": "EPSSwitcher",
        "inputs": {"image_1": ["5", 0], "image_2": ["6", 0], "toggles": '{"image_2": false}'},
    },
    "5": {"class_type": "FakeSource", "inputs": {}},
    "6": {"class_type": "FakeSource", "inputs": {}},
}


@pytest.fixture(scope="module")
def core_dispatch(tmp_path_factory: pytest.TempPathFactory, install_switcher_js) -> dict:
    comfy_dir = _comfyui_dir()
    if comfy_dir is None:
        pytest.skip("no ComfyUI checkout (set COMFYUI_DIR) to drive core's real dispatch")
    layout = tmp_path_factory.mktemp("core_dispatch")
    install_switcher_js(layout)
    (layout / "strip.mjs").write_text(
        "import { stripDisabledSwitcherRows } from "
        "'./extensions/comfyui-epsnodes/eps_image/switcher_prompt.js'\n"
        "let data = ''\nfor await (const chunk of process.stdin) data += chunk\n"
        "process.stdout.write(JSON.stringify(stripDisabledSwitcherRows(JSON.parse(data))))\n",
        encoding="utf-8",
    )
    stripped = subprocess.run(
        [NODE, str(layout / "strip.mjs")],
        input=json.dumps(PROMPT),
        capture_output=True,
        encoding="utf-8",
        timeout=60,
        cwd=layout,
    )
    assert stripped.returncode == 0, stripped.stderr
    payload = {"as_sent_before": PROMPT, "as_sent_now": json.loads(stripped.stdout)}
    result = subprocess.run(
        [sys.executable, "-c", CORE_DRIVER, str(comfy_dir), str(REPO_ROOT), json.dumps(payload)],
        capture_output=True,
        encoding="utf-8",
        timeout=180,
    )
    assert result.returncode == 0, result.stderr[-3000:]
    line = next(x for x in result.stdout.splitlines() if x.startswith("RESULT"))
    return json.loads(line[len("RESULT") :])


def test_core_blocks_the_whole_switcher_when_a_disabled_rows_source_emits_a_blocker(
    core_dispatch: dict,
) -> None:
    before = core_dispatch["as_sent_before"]
    assert before["lazy"] == "BLOCKED"
    assert before["execute"] == "BLOCKED"


def test_with_the_disabled_link_absent_the_enabled_rows_come_out(core_dispatch: dict) -> None:
    now = core_dispatch["as_sent_now"]
    assert now["lazy"] == ["image_1"]
    assert now["execute"] == ["imgA"]


def test_core_validation_accepts_the_stripped_prompt(core_dispatch: dict) -> None:
    assert core_dispatch["as_sent_now"]["valid"] is True
