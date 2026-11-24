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
const G = (...ids) => new Map(ids.map((id) => [id, 'T' + id]))
const mk = () => {
  const skipped = []
  const t = grid.createSkippedRunTracker({ onSkipped: (id, title) => skipped.push([id, title]) })
  return { t, skipped }
}
const running = (prompt_id, ...ids) => ({
  prompt_id,
  nodes: Object.fromEntries(ids.map((id) => [id, { state: 'running' }]))
})

out.pure = grid.skippedGridIds(G('a', 'b', 'c', 'd'), new Set(['a', 'b', 'x']), new Set(['b']))
{
  const output = {
    '5': { class_type: 'EPSImageGrid' }, '6': { class_type: 'SaveImage' },
    '3:2': { class_type: 'EPSImageGrid' }, '7': { class_type: 'EPSImageGrid' }
  }
  const workflow = {
    nodes: [{ id: 5, title: 'My Grid' }, { id: 3, type: 'sg-1' }, { id: 7 }],
    definitions: { subgraphs: [{ id: 'sg-1', nodes: [{ id: 2, title: 'Inner Grid' }] }] }
  }
  out.gridsOf = [...grid.gridsOfQueuedPrompt(output, workflow)]
  out.gridsOfEmpty = [...grid.gridsOfQueuedPrompt(undefined, undefined)]
}

{ // announced + ran -> nothing
  const { t, skipped } = mk()
  t.queued('p', G('g1')); t.progressState(running('p', 'g1')); t.ran({ node: 'g1', prompt_id: 'p' })
  t.success({ prompt_id: 'p' })
  out.ran = { skipped, size: t.size() }
}
{ // announced, never ran, success -> one alert with the queued title
  const { t, skipped } = mk()
  t.queued('p', G('g1')); t.progressState(running('p', 'g1', 'x2')); t.success({ prompt_id: 'p' })
  out.skipped = { skipped, size: t.size() }
}
{ // queue response AFTER the first progress_state
  const { t, skipped } = mk()
  t.progressState(running('p', 'g1')); t.queued('p', G('g1')); t.success({ prompt_id: 'p' })
  out.lateQueued = { skipped }
}
{ // never recorded by queuePrompt -> nothing
  const { t, skipped } = mk()
  t.progressState(running('p', 'g1')); t.success({ prompt_id: 'p' })
  out.unrecorded = { skipped, size: t.size() }
}
{ // lazy pass: announced twice, then ran
  const { t, skipped } = mk()
  t.queued('p', G('g1')); t.progressState(running('p', 'g1')); t.progressState(running('p', 'g1'))
  t.ran({ node: 'g1', prompt_id: 'p' }); t.success({ prompt_id: 'p' })
  out.lazy = { skipped }
}
{ // a grid queued but never announced (cached / not reached) -> nothing
  const { t, skipped } = mk()
  t.queued('p', G('g1')); t.success({ prompt_id: 'p' })
  out.cached = { skipped, size: t.size() }
}
{ // error / interrupt: discard
  const { t, skipped } = mk()
  t.queued('p', G('g1')); t.progressState(running('p', 'g1')); t.discard({ prompt_id: 'p' })
  t.success({ prompt_id: 'p' })
  out.errored = { skipped, size: t.size() }
}
{ // two queued prompts: the waiting one survives the first one's start and still reports
  const { t, skipped } = mk()
  t.queued('p', G('g1')); t.queued('q', G('g1'))
  t.started({ prompt_id: 'p' }); t.progressState(running('p', 'g1')); t.ran({ node: 'g1', prompt_id: 'p' })
  t.success({ prompt_id: 'p' })
  const afterP = [...skipped]
  t.started({ prompt_id: 'q' }); t.progressState(running('q', 'g1')); t.success({ prompt_id: 'q' })
  out.twoQueued = { afterP, skipped, size: t.size() }
}
{ // a newer execution_start drops an earlier prompt whose end was missed
  const { t, skipped } = mk()
  t.queued('p', G('g1')); t.progressState(running('p', 'g1'))
  t.queued('q', G('g1')); t.started({ prompt_id: 'q' })
  t.success({ prompt_id: 'p' })
  out.missedEnd = { skipped, size: t.size() }
}
{ // ran without a prompt id counts for every open prompt
  const { t, skipped } = mk()
  t.queued('p', G('g1')); t.progressState(running('p', 'g1')); t.ran({ node: 'g1' }); t.success({ prompt_id: 'p' })
  out.noPromptId = { skipped }
}

// ---- the installed listeners + queuePrompt chain, end to end through init() ----
{
  const handlers = {}
  api.addEventListener = (name, fn) => { handlers[name] = fn }
  const calls = []
  let nextId = 0
  api.queuePrompt = async function (number, data, options) {
    calls.push({ self: this === api, number, options })
    if (data.reject) throw new Error('queue rejected')
    return { prompt_id: data.pid ?? 'auto' + nextId++, number: 1, marker: 'orig' }
  }
  // another extension wrapped BEFORE us (installed first):
  const before = api.queuePrompt
  const order = []
  api.queuePrompt = async function (...a) { order.push('before'); return before.apply(this, a) }
  const toasts = []
  const warns = []
  console.warn = (...a) => warns.push(a.join(' '))
  app.extensionManager = { toast: { add: (m) => toasts.push(m) } }
  // The open graph must play NO part: it holds a SaveImage-less empty graph.
  app.graph = { getNodeById: () => { throw new Error('open graph consulted') }, _nodes: [] }
  globalThis.document = { addEventListener() {}, hidden: false }
  grid.init()
  grid.init() // twice: wraps once
  const afterWrap = api.queuePrompt
  // another extension wrapping AFTER us:
  api.queuePrompt = async function (...a) { order.push('after'); return afterWrap.apply(this, a) }

  const fire = (name, detail) => handlers[name]({ detail })
  const q = (pid, ids) => api.queuePrompt(0, {
    pid,
    output: Object.fromEntries(ids.map((id) => [id, { class_type: 'EPSImageGrid' }])),
    workflow: { nodes: [{ id: 5, title: 'Mine' }] }
  }, { tag: 1 })
  const res = await q('p', ['5', '3:2'])
  out.chain = { order: [...order], calls: calls.map((c) => ({ ...c })), result: res }
  // FINDING 2: another workflow is open now; event ids resolve against the QUEUED prompt only
  fire('execution_start', { prompt_id: 'p' })
  fire('progress_state', running('p', '5', '9', '3:2'))
  fire('eps-image-grid-ran', { node: '5', prompt_id: 'p' })
  fire('execution_success', { prompt_id: 'p' })
  out.wired = { toasts: toasts.map((m) => ({ severity: m.severity, summary: m.summary, detail: m.detail })),
                warns: warns.length }
  // FINDING 1: socket drop between running and success -> no alert
  await q('r', ['5'])
  fire('progress_state', running('r', '5'))
  fire('reconnecting', undefined); fire('reconnected', undefined)
  fire('progress_state', { prompt_id: 'r', nodes: { 5: { state: 'finished' } } })
  fire('execution_success', { prompt_id: 'r' })
  out.afterReconnect = { toasts: toasts.length }
  // a prompt queued by "another tab" (never through our queuePrompt)
  fire('progress_state', running('z', '5')); fire('execution_success', { prompt_id: 'z' })
  out.otherTab = { toasts: toasts.length }
  // rejecting original propagates and records nothing
  let rejected = null
  try { await api.queuePrompt(0, { reject: true, output: {} }) } catch (e) { rejected = e.message }
  out.rejected = rejected
  // our bookkeeping failing: logged, result untouched
  const before2 = warns.length
  const bad = await api.queuePrompt(0, { pid: 'w', output: { get 5() { throw new Error('boom') } } })
  out.bookkeepingFailure = { marker: bad.marker, warned: warns.length - before2 }
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
    for name in ("api.js", "version.js", "panel_helpers.js"):
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


def test_skipped_ids_need_a_queued_grid_that_was_announced_and_never_ran(probe: dict) -> None:
    assert probe["pure"] == ["a"]


def test_grids_come_from_the_queued_output_with_titles_from_the_workflow(probe: dict) -> None:
    assert probe["gridsOf"] == [["5", "My Grid"], ["7", "EPS Image Grid"], ["3:2", "Inner Grid"]]
    assert probe["gridsOfEmpty"] == []


def test_a_grid_that_ran_does_not_alert(probe: dict) -> None:
    assert probe["ran"] == {"skipped": [], "size": 0}


def test_announced_but_never_ran_alerts_once_with_the_queued_title(probe: dict) -> None:
    assert probe["skipped"] == {"skipped": [["g1", "Tg1"]], "size": 0}


def test_queue_response_after_the_first_progress_state_still_alerts(probe: dict) -> None:
    assert probe["lateQueued"]["skipped"] == [["g1", "Tg1"]]


def test_prompt_never_recorded_by_queue_prompt_alerts_nothing(probe: dict) -> None:
    assert probe["unrecorded"] == {"skipped": [], "size": 0}


def test_lazy_double_announcement_then_ran_does_not_alert(probe: dict) -> None:
    assert probe["lazy"]["skipped"] == []


def test_queued_but_never_announced_grid_does_not_alert(probe: dict) -> None:
    assert probe["cached"] == {"skipped": [], "size": 0}


def test_error_or_interrupt_discards_without_alerting(probe: dict) -> None:
    assert probe["errored"] == {"skipped": [], "size": 0}


def test_a_waiting_prompt_survives_the_running_ones_start_and_reports_its_skip(probe: dict) -> None:
    assert probe["twoQueued"] == {"afterP": [], "skipped": [["g1", "Tg1"]], "size": 0}


def test_a_newer_start_discards_a_prompt_whose_end_was_missed(probe: dict) -> None:
    assert probe["missedEnd"] == {"skipped": [], "size": 1}  # q is still waiting


def test_ran_without_prompt_id_never_causes_a_false_alert(probe: dict) -> None:
    assert probe["noPromptId"]["skipped"] == []


def test_queue_prompt_chain_preserves_other_wrappers_args_and_result(probe: dict) -> None:
    chain = probe["chain"]
    assert chain["order"] == ["after", "before"]  # wrapped once, both neighbours kept
    assert chain["calls"] == [{"self": True, "number": 0, "options": {"tag": 1}}]
    assert chain["result"] == {"prompt_id": "p", "number": 1, "marker": "orig"}


def test_alert_names_the_queued_grid_and_ignores_the_open_graph(probe: dict) -> None:
    # finding 2: the open graph throws if consulted; only subgraph grid 3:2 was skipped
    toasts = probe["wired"]["toasts"]
    assert [t["summary"] for t in toasts] == ["EPS Image Grid"]
    assert toasts[0]["severity"] == "warn"
    assert "blocked upstream" in toasts[0]["detail"]
    assert probe["wired"]["warns"] == 1


def test_a_reconnect_between_running_and_success_alerts_nothing(probe: dict) -> None:
    assert probe["afterReconnect"]["toasts"] == 1  # still just the first one


def test_a_prompt_queued_elsewhere_alerts_nothing(probe: dict) -> None:
    assert probe["otherTab"]["toasts"] == 1


def test_a_rejecting_queue_propagates(probe: dict) -> None:
    assert probe["rejected"] == "queue rejected"


def test_our_bookkeeping_failure_is_logged_and_the_result_returned(probe: dict) -> None:
    assert probe["bookkeepingFailure"] == {"marker": "orig", "warned": 1}
