"""Frontend tests for the Image Grid skipped-run alert (FORMAT.md §6.6).

Core never calls an INPUT_IS_LIST node whose list holds an ExecutionBlocker,
so the frontend detects a skipped grid by the ABSENCE of `eps-image-grid-ran`
for a grid core announced (`progress_state` 'running'), and decides only at
`execution_success`. Driven under Node against the same stub-`scripts/`
served layout the other image_grid *_js tests use; skips without Node.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB = REPO_ROOT / "web"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

PROBE_JS = """
import { app } from './scripts/app.js'
import { api } from './scripts/api.js'
import * as grid from './extensions/comfyui-epsnodes/eps_image/image_grid.js'

const out = {}
const mk = () => {
  const skipped = []
  const t = grid.createSkippedRunTracker({
    isGrid: (id) => id.startsWith('g'),
    onSkipped: (id) => skipped.push(id)
  })
  return { t, skipped }
}
const running = (prompt_id, ...ids) => ({
  prompt_id,
  nodes: Object.fromEntries(ids.map((id) => [id, { state: 'running' }]))
})

out.pure = grid.skippedGridIds(new Set(['a', 'b', 'c']), new Set(['b']))

{ // announced + ran -> nothing
  const { t, skipped } = mk()
  t.progressState(running('p', 'g1')); t.ran({ node: 'g1', prompt_id: 'p' }); t.success({ prompt_id: 'p' })
  out.ran = { skipped, size: t.size() }
}
{ // announced, never ran, success -> skipped once; non-grid ignored
  const { t, skipped } = mk()
  t.progressState(running('p', 'g1', 'x2')); t.success({ prompt_id: 'p' })
  out.skipped = { skipped, size: t.size() }
}
{ // lazy pass: announced twice, then ran
  const { t, skipped } = mk()
  t.progressState(running('p', 'g1')); t.progressState(running('p', 'g1'))
  t.ran({ node: 'g1', prompt_id: 'p' }); t.success({ prompt_id: 'p' })
  out.lazy = { skipped }
}
{ // a node only ever listed as 'finished' (e.g. resent state) was never announced
  const { t, skipped } = mk()
  t.progressState({ prompt_id: 'p', nodes: { g1: { state: 'finished' } } })
  t.success({ prompt_id: 'p' })
  out.finishedNotAnnounce = { skipped }
}
{ // error / interrupt: discard, no alert, state cleared
  const { t, skipped } = mk()
  t.progressState(running('p', 'g1')); t.discard({ prompt_id: 'p' })
  t.progressState(running('q', 'g1')); t.discard({ prompt_id: 'q' })
  t.success({ prompt_id: 'p' }); t.success({ prompt_id: 'q' })
  out.errored = { skipped, size: t.size() }
}
{ // cached: never announced
  const { t, skipped } = mk()
  t.success({ prompt_id: 'p' })
  out.cached = { skipped, size: t.size() }
}
{ // two prompts interleaved by prompt_id
  const { t, skipped } = mk()
  t.progressState(running('p', 'g1')); t.progressState(running('q', 'g1', 'g2'))
  t.ran({ node: 'g1', prompt_id: 'p' }); t.ran({ node: 'g2', prompt_id: 'q' })  // q's g1 never ran
  t.success({ prompt_id: 'p' })
  const afterP = [...skipped]
  t.success({ prompt_id: 'q' })
  out.interleaved = { afterP, skipped, size: t.size() }
}
{ // ran without a prompt id counts for every open prompt (never a false alert)
  const { t, skipped } = mk()
  t.progressState(running('p', 'g1')); t.ran({ node: 'g1' }); t.success({ prompt_id: 'p' })
  out.noPromptId = { skipped }
}

// ---- the installed listeners, end to end through init() ----
{
  const handlers = {}
  api.addEventListener = (name, fn) => { handlers[name] = fn }
  const toasts = []
  const warns = []
  console.warn = (...a) => warns.push(a.join(' '))
  app.extensionManager = { toast: { add: (m) => toasts.push(m) } }
  const gridNode = { id: 5, comfyClass: 'EPSImageGrid', title: 'My Grid' }
  const inner = { id: 2, comfyClass: 'EPSImageGrid' } // inside subgraph node 3, no title
  const other = { id: 9, comfyClass: 'SaveImage' }
  const byId = { 5: gridNode, 9: other }
  app.graph = {
    getNodeById: (n) => (n === 3 ? { id: 3, subgraph: { getNodeById: (m) => (m === 2 ? inner : null) } } : byId[n]),
    _nodes: []
  }
  globalThis.document = { addEventListener() {}, hidden: false }
  grid.init()
  const fire = (name, detail) => handlers[name]({ detail })
  fire('progress_state', running('p', '5', '9', '3:2'))
  fire('eps-image-grid-ran', { node: '5', prompt_id: 'p' })
  fire('execution_success', { prompt_id: 'p' })
  out.wired = { toasts: toasts.map((m) => ({ severity: m.severity, summary: m.summary, detail: m.detail })),
                warns: warns.length }
  fire('progress_state', running('r', '5'))
  fire('execution_error', { prompt_id: 'r' })
  fire('execution_success', { prompt_id: 'r' })
  out.wiredAfterError = { toasts: toasts.length }
}
process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def probe(tmp_path_factory: pytest.TempPathFactory) -> dict:
    layout = tmp_path_factory.mktemp("web_root")
    ext = layout / "extensions" / "comfyui-epsnodes"
    (ext / "eps_image").mkdir(parents=True)
    (ext / "lora_library").mkdir(parents=True)
    for name in ("image_grid.js", "owned_link_colour.js"):
        shutil.copyfile(WEB / "eps_image" / name, ext / "eps_image" / name)
    for name in ("api.js", "version.js"):
        shutil.copyfile(WEB / "lora_library" / name, ext / "lora_library" / name)
    scripts = layout / "scripts"
    scripts.mkdir()
    (scripts / "app.js").write_text(
        "export const app = {}\nexport class ComfyApp {}\n", encoding="utf-8"
    )
    (scripts / "api.js").write_text(
        "export const api = {\n  apiURL: (p) => p,\n"
        "  fetchApi: async () => ({ ok: true, json: async () => ({}) }),\n"
        "  addEventListener: () => {}\n}\n",
        encoding="utf-8",
    )
    script = layout / "probe.mjs"
    script.write_text(PROBE_JS, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(script)], capture_output=True, encoding="utf-8", cwd=layout, timeout=60
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


def test_skipped_ids_are_announced_minus_ran(probe: dict) -> None:
    assert probe["pure"] == ["a", "c"]


def test_a_grid_that_ran_does_not_alert(probe: dict) -> None:
    assert probe["ran"] == {"skipped": [], "size": 0}


def test_announced_but_never_ran_alerts_once_at_success(probe: dict) -> None:
    assert probe["skipped"] == {"skipped": ["g1"], "size": 0}


def test_lazy_double_announcement_then_ran_does_not_alert(probe: dict) -> None:
    assert probe["lazy"]["skipped"] == []


def test_a_finished_state_alone_is_not_an_announcement(probe: dict) -> None:
    assert probe["finishedNotAnnounce"]["skipped"] == []


def test_error_or_interrupt_discards_without_alerting(probe: dict) -> None:
    assert probe["errored"] == {"skipped": [], "size": 0}


def test_cached_grid_never_announced_does_not_alert(probe: dict) -> None:
    assert probe["cached"] == {"skipped": [], "size": 0}


def test_two_prompts_do_not_mix_p_ran_g1_q_did_not(probe: dict) -> None:
    assert probe["interleaved"] == {"afterP": [], "skipped": ["g1"], "size": 0}


def test_ran_without_prompt_id_never_causes_a_false_alert(probe: dict) -> None:
    assert probe["noPromptId"]["skipped"] == []


def test_installed_listeners_toast_once_with_title_and_default_title(probe: dict) -> None:
    toasts = probe["wired"]["toasts"]
    # grid 5 ran, SaveImage 9 is not a grid; only the subgraph grid 3:2 was skipped
    assert [t["summary"] for t in toasts] == ["EPS Image Grid"]
    assert toasts[0]["severity"] == "warn"
    assert "blocked upstream" in toasts[0]["detail"]
    assert probe["wired"]["warns"] == 1


def test_installed_listeners_stay_quiet_after_an_error(probe: dict) -> None:
    assert probe["wiredAfterError"]["toasts"] == 1
