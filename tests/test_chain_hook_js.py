"""Behaviour tests for ``chainHook`` (``web/lora_library/panel_helpers.js``,
docs/plan/ROADMAP-shared-panel-code.md M2) and the guard that keeps the
hand-written "capture the original hook, wrap it, call through" scaffolding
from coming back.

Before the helper existed the pack wrote that wrapper ~75 times by hand
(FORMAT.md section 7.9's "chain, never replace"), each copy with its own idea
of what happens when a half throws. The helper is driven here under Node with
no layout (``panel_helpers.js`` imports nothing). Expected values come from
the helper's documented contract, not from running it.
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
import { chainHook } from '%(module)s'

const out = {}
const events = []
const warnings = []
const warn = (message, error) => warnings.push({ message, error })
const rec = (name, self, args) => events.push({ name, self, args: [...args] })

// A target whose original records `this` + arguments and answers 'ORIG'.
const withOriginal = (hook = 'onX', answer = 'ORIG') => {
  const target = {}
  target[hook] = function (...args) { rec('orig', this, args); return answer }
  return target
}
const reset = () => { events.length = 0; warnings.length = 0 }
const describe = () => events.map((e) => e.name)

// -- A. default order: original first, same this + args, original's return --
{
  reset()
  const target = withOriginal()
  chainHook(target, 'onX', function (...args) { rec('ours', this, args); return 'OURS' }, { warn })
  out.a = {
    result: target.onX(1, 'two'),
    order: describe(),
    thisIsTarget: events.every((e) => e.self === target),
    args: events.map((e) => e.args),
    warnings: warnings.length
  }
}

// -- B. no original at all: ours still runs, result undefined --
{
  reset()
  const target = {}
  chainHook(target, 'onX', function (...args) { rec('ours', this, args); return 'OURS' }, { warn })
  out.b = { result: target.onX(7), order: describe(), args: events.map((e) => e.args) }
}

// -- C. order 'before': ours first, original's return still wins --
{
  reset()
  const target = withOriginal()
  chainHook(target, 'onX', (...args) => rec('ours', null, args), { warn, order: 'before' })
  out.c = { result: target.onX('p'), order: describe(), args: events.map((e) => e.args) }
}

// -- D. ours throws: caught, logged once through warn, original unaffected --
{
  reset()
  const boom = new Error('ours exploded')
  const target = withOriginal()
  chainHook(target, 'onX', () => { throw boom }, { warn, label: 'link dim sync (onX)' })
  const result = target.onX(1)
  out.d = {
    result,
    order: describe(),
    warnings: warnings.map((w) => w.message),
    sameError: warnings[0]?.error === boom
  }
}
{
  reset()
  const target = withOriginal()
  chainHook(target, 'onX', () => { throw new Error('x') }, { warn })
  target.onX()
  out.dDefaultLabel = warnings.map((w) => w.message)
}
{
  reset()
  const target = withOriginal()
  chainHook(target, 'onX', () => { throw new Error('x') }, { warn, order: 'before', label: 'cleanup' })
  out.dBefore = { result: target.onX(), order: describe(), warnings: warnings.map((w) => w.message) }
}

// -- E. the ORIGINAL throws --
{
  // default: propagates untouched, ours is skipped, nothing is logged
  reset()
  const boom = new Error('original exploded')
  const target = { onX() { throw boom } }
  chainHook(target, 'onX', (...args) => rec('ours', null, args), { warn })
  let thrown = null
  try { target.onX() } catch (error) { thrown = error }
  out.ePropagate = { sameError: thrown === boom, oursRan: events.length > 0, warnings: warnings.length }
}
{
  // 'still-run': ours runs, then the original's own error is rethrown
  reset()
  const boom = new Error('original exploded')
  const target = { onX() { throw boom } }
  chainHook(target, 'onX', (...args) => rec('ours', null, args), { warn, onOriginalThrow: 'still-run' })
  let thrown = null
  try { target.onX(4) } catch (error) { thrown = error }
  out.eStillRun = {
    sameError: thrown === boom, order: describe(), args: events.map((e) => e.args), warnings: warnings.length
  }
}
{
  // 'still-run' where ours ALSO throws: ours is logged, the original's error still wins
  reset()
  const boom = new Error('original exploded')
  const target = { onX() { throw boom } }
  chainHook(target, 'onX', () => { throw new Error('ours too') }, { warn, label: 'mine', onOriginalThrow: 'still-run' })
  let thrown = null
  try { target.onX() } catch (error) { thrown = error }
  out.eStillRunBoth = { sameError: thrown === boom, warnings: warnings.map((w) => w.message) }
}
{
  // 'log': swallowed and reported as `original <hook> threw`, ours runs, result undefined
  reset()
  const boom = new Error('original exploded')
  const target = { onX() { throw boom } }
  chainHook(target, 'onX', (...args) => rec('ours', null, args), { warn, onOriginalThrow: 'log' })
  out.eLog = {
    result: target.onX(9),
    order: describe(),
    warnings: warnings.map((w) => w.message),
    sameError: warnings[0]?.error === boom
  }
}

// -- F. veto --
const vetoTarget = (original) => {
  const target = original ? { onX: original } : {}
  return target
}
{
  // ours answers: that answer is returned and the original is NOT called
  reset()
  const target = vetoTarget(function (...args) { rec('orig', this, args); return 'ORIG' })
  chainHook(target, 'onX', () => false, { warn, order: 'before', veto: true })
  out.fVeto = { result: target.onX(1), originalCalled: events.length > 0 }
}
{
  // ours declines (undefined): the original runs and ITS answer is returned, even `false`
  reset()
  const target = vetoTarget(function (...args) { rec('orig', this, args); return false })
  chainHook(target, 'onX', () => undefined, { warn, order: 'before', veto: true })
  out.fDecline = { result: target.onX(1, 2), order: describe(), args: events.map((e) => e.args) }
}
{
  // declines and there is no original: undefined
  reset()
  const target = vetoTarget(null)
  chainHook(target, 'onX', () => undefined, { warn, order: 'before', veto: true })
  out.fNoOriginal = { result: target.onX(1) === undefined }
}
{
  // ours throws while vetoing: fail open (the original decides), failure logged
  reset()
  const target = vetoTarget(function (...args) { rec('orig', this, args); return 'ORIG' })
  chainHook(target, 'onX', () => { throw new Error('x') }, { warn, order: 'before', veto: true, label: 'veto' })
  out.fThrows = { result: target.onX(), order: describe(), warnings: warnings.map((w) => w.message) }
}
{
  // WITHOUT veto, ours' return value is ignored
  reset()
  const target = vetoTarget(function () { return 'ORIG' })
  chainHook(target, 'onX', () => false, { warn, order: 'before' })
  out.fNoVetoOption = target.onX()
}

// -- G. a second, independent chain on the same hook (another module) --
{
  reset()
  const target = withOriginal()
  chainHook(target, 'onX', function (...args) { rec('first', this, args) }, { warn })
  chainHook(target, 'onX', function (...args) { rec('second', this, args) }, { warn })
  out.g = { result: target.onX(3), order: describe(), args: events.map((e) => e.args) }
}
{
  // a hand-written wrapper from elsewhere sits underneath; both still run
  reset()
  const target = { onX(...args) { rec('foreign', this, args); return 'FOREIGN' } }
  const foreign = target.onX
  target.onX = function (...args) { const r = foreign.apply(this, args); rec('wrap', this, args); return r }
  chainHook(target, 'onX', function (...args) { rec('ours', this, args) }, { warn })
  out.gForeign = { result: target.onX(), order: describe() }
}

// -- H. no warn given: console.warn --
{
  const seen = []
  const real = console.warn
  console.warn = (...args) => seen.push(args)
  try {
    const target = {}
    chainHook(target, 'onX', () => { throw new Error('x') }, { label: 'no logger' })
    target.onX()
  } finally {
    console.warn = real
  }
  out.h = seen.map((a) => a[0])
}

// -- I. any hook name, a widget callback's real arguments pass through --
{
  reset()
  const widget = { callback(...args) { rec('orig', this, args); return 'cb' } }
  chainHook(widget, 'callback', (...args) => rec('ours', null, args), { warn })
  out.i = { result: widget.callback(5, 'canvas', 'node'), args: events.map((e) => e.args) }
}

// -- J. the receiver is whoever calls it (litegraph calls hooks with the node; a core wrapper may use another) --
{
  reset()
  const target = withOriginal()
  const other = { name: 'other' }
  chainHook(target, 'onX', function (...args) { rec('ours', this, args) }, { warn })
  target.onX.call(other, 1)
  out.j = events.every((e) => e.self === other)
}

// -- K. the original is captured when chainHook runs, not looked up per call --
{
  reset()
  const target = withOriginal('onX', 'FIRST')
  chainHook(target, 'onX', () => {}, { warn })
  out.k = target.onX()
}
process.stdout.write('RESULT:' + JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def probe(tmp_path_factory: pytest.TempPathFactory) -> dict:
    layout = tmp_path_factory.mktemp("chain_hook")
    script = layout / "probe.mjs"
    script.write_text(PROBE_JS % {"module": PANEL_HELPERS_JS.as_uri()}, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(script)], capture_output=True, encoding="utf-8", timeout=60, cwd=layout
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout.split("RESULT:", 1)[1])


def test_original_runs_first_with_the_same_this_and_arguments_and_its_result_is_returned(
    probe: dict,
) -> None:
    a = probe["a"]
    assert a["order"] == ["orig", "ours"]
    assert a["thisIsTarget"] is True
    assert a["args"] == [[1, "two"], [1, "two"]]
    assert a["result"] == "ORIG", "ours' own return value is ignored"
    assert a["warnings"] == 0


def test_with_no_original_ours_still_runs_and_the_result_is_undefined(probe: dict) -> None:
    b = probe["b"]
    assert b["order"] == ["ours"]
    assert b["args"] == [[7]]
    assert "result" not in b  # undefined does not survive JSON


def test_order_before_runs_ours_first_and_still_returns_the_originals_result(probe: dict) -> None:
    c = probe["c"]
    assert c["order"] == ["ours", "orig"]
    assert c["args"] == [["p"], ["p"]]
    assert c["result"] == "ORIG"


def test_ours_throwing_is_caught_logged_once_and_the_original_is_unaffected(probe: dict) -> None:
    d = probe["d"]
    assert d["result"] == "ORIG"
    assert d["order"] == ["orig"]
    assert d["warnings"] == ["link dim sync (onX) failed"]
    assert d["sameError"] is True, "the caught error is handed to warn"


def test_the_failure_message_names_the_hook_when_no_label_is_given(probe: dict) -> None:
    assert probe["dDefaultLabel"] == ["onX failed"]


def test_ours_throwing_before_the_original_does_not_stop_the_original(probe: dict) -> None:
    d = probe["dBefore"]
    assert d["order"] == ["orig"]
    assert d["result"] == "ORIG"
    assert d["warnings"] == ["cleanup failed"]


def test_the_original_throwing_propagates_by_default_and_ours_is_skipped(probe: dict) -> None:
    e = probe["ePropagate"]
    assert e == {"sameError": True, "oursRan": False, "warnings": 0}


def test_still_run_runs_ours_then_rethrows_the_originals_own_error(probe: dict) -> None:
    e = probe["eStillRun"]
    assert e["sameError"] is True
    assert e["order"] == ["ours"]
    assert e["args"] == [[4]]
    assert e["warnings"] == 0, "the original's error is not swallowed, so nothing is logged"


def test_still_run_logs_ours_failing_but_the_originals_error_still_wins(probe: dict) -> None:
    e = probe["eStillRunBoth"]
    assert e["sameError"] is True
    assert e["warnings"] == ["mine failed"]


def test_log_swallows_the_original_reports_it_and_still_runs_ours(probe: dict) -> None:
    e = probe["eLog"]
    assert e["order"] == ["ours"]
    assert e["warnings"] == ["original onX threw"]
    assert e["sameError"] is True
    assert "result" not in e, "a swallowed original leaves no result"


def test_veto_answers_for_the_hook_and_the_original_is_not_called(probe: dict) -> None:
    assert probe["fVeto"] == {"result": False, "originalCalled": False}


def test_a_declined_veto_falls_through_to_the_original_and_returns_its_answer(probe: dict) -> None:
    f = probe["fDecline"]
    assert f["order"] == ["orig"]
    assert f["args"] == [[1, 2]]
    assert f["result"] is False, "the original's `false` is its own answer, returned as is"


def test_a_declined_veto_with_no_original_is_undefined(probe: dict) -> None:
    assert probe["fNoOriginal"] == {"result": True}


def test_a_throwing_veto_fails_open_to_the_original_and_is_logged(probe: dict) -> None:
    f = probe["fThrows"]
    assert f["order"] == ["orig"]
    assert f["result"] == "ORIG"
    assert f["warnings"] == ["veto failed"]


def test_without_the_veto_option_ours_return_value_is_ignored(probe: dict) -> None:
    assert probe["fNoVetoOption"] == "ORIG"


def test_a_second_independent_chain_on_the_same_hook_runs_both(probe: dict) -> None:
    g = probe["g"]
    assert g["order"] == ["orig", "first", "second"]
    assert g["args"] == [[3], [3], [3]]
    assert g["result"] == "ORIG"


def test_it_chains_onto_a_hand_written_wrapper_already_installed(probe: dict) -> None:
    g = probe["gForeign"]
    assert g["order"] == ["foreign", "wrap", "ours"]
    assert g["result"] == "FOREIGN"


def test_without_a_warn_option_failures_go_to_console_warn(probe: dict) -> None:
    assert probe["h"] == ["no logger failed"]


def test_any_property_name_works_and_widget_callback_arguments_pass_through(probe: dict) -> None:
    i = probe["i"]
    assert i["result"] == "cb"
    assert i["args"] == [[5, "canvas", "node"], [5, "canvas", "node"]]


def test_the_receiver_is_whoever_calls_the_hook(probe: dict) -> None:
    assert probe["j"] is True


def test_the_original_is_captured_at_install_time(probe: dict) -> None:
    assert probe["k"] == "FIRST"


# ---------------------------------------------------------------------------
# Enforcement: no file under web/ chains a hook by hand any more
# ---------------------------------------------------------------------------

#: Sites that deliberately do NOT go through chainHook, keyed by
#: (file under web/, hook) -> (how many source lines match, why). The scan
#: sees two things: an assignment to a hook (`x.onFoo = ...`) and the capture
#: of an existing one (`const original = x.onFoo` / `.callback` /
#: `.getExtraMenuOptions`, or a computed `x[hook] = ...`). Adding a new hand
#: chain means adding a line here WITH a reason a reviewer can disagree with;
#: an entry whose site was converted must be deleted (the test fails on a
#: stale entry too).
HAND_CHAIN_ALLOW = {
    ("eps_image/switcher.js", "onMouseDown"): (
        2,
        "the row-toggle click answers first and, with no original, returns `false`; "
        "chainHook's veto form returns `undefined` there",
    ),
    ("eps_image/distributor.js", "onMouseDown"): (
        2,
        "same row-toggle click as switcher.js: `false` (not `undefined`) with no original",
    ),
    ("eps_image/resolution.js", "onClick"): (
        1,
        "replaces the preset combo's own onClick and calls the stock one (bound) only for "
        "a plain click -- a replacement, not a chain",
    ),
    ("eps_image/resolution.js", "callback"): (
        1,
        "the ratio widget's callback reads `args[0]` BEFORE the original runs and, for the "
        "`custom…` command, answers instead of calling it (v1-only; chainHook always calls "
        "the original first)",
    ),
    ("eps_image/resolution.js", "onConfigure"): (
        2,
        "reads `info.properties` BEFORE the original onConfigure runs (the old-layout "
        "stamp is unreadable afterwards); chainHook calls the original first",
    ),
    ("eps_image/image_grid.js", "callback"): (
        1,
        "wraps a context-menu ITEM's callback, rebuilt on every menu open, calling the "
        "original unbound like litegraph's menu does -- not a node/widget hook",
    ),
    ("eps_image/image_grid.js", "onDragOver"): (
        1,
        "defines a hook the node class never had: no original to chain",
    ),
    ("eps_image/image_grid.js", "onDragDrop"): (
        1,
        "defines a hook the node class never had: no original to chain",
    ),
    ("eps_image/frame_saver.js", "onDragOver"): (
        1,
        "defines a hook the node class never had: no original to chain",
    ),
    ("eps_image/frame_saver.js", "onDragDrop"): (
        1,
        "defines a hook the node class never had: no original to chain",
    ),
    ("lora_library/notebook.js", "callback"): (
        1,
        "the remote-viewer guard answers for the file widget's callback and skips the "
        "original entirely when it reverts a hand edit, with state written between the "
        "guard and the original call",
    ),
}

_ASSIGN_HOOK = re.compile(r"\.(on[A-Z]\w*|getExtraMenuOptions)\s*=(?![=>])")
_CAPTURE_HOOK = re.compile(r"=\s*[A-Za-z_][\w.]*\.(on[A-Z]\w*|callback|getExtraMenuOptions)\s*$", re.M)
_ASSIGN_COMPUTED = re.compile(r"\b(?:graph|node|widget|target)\[hook\]\s*=(?!=)")


def _strip_comments(source: str) -> str:
    """Drop block comments and whole-line `//` comments, keeping line count."""
    source = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", source, flags=re.M)


def _hand_chain_hits() -> dict[tuple[str, str], int]:
    hits: dict[tuple[str, str], int] = {}
    for path in sorted(WEB.rglob("*.js")):
        if path == PANEL_HELPERS_JS:
            continue  # chainHook itself
        code = _strip_comments(path.read_text(encoding="utf-8"))
        rel = path.relative_to(WEB).as_posix()
        for line in code.splitlines():
            names = [m.group(1) for m in _ASSIGN_HOOK.finditer(line)]
            names += [m.group(1) for m in _CAPTURE_HOOK.finditer(line)]
            names += ["<computed hook>" for _ in _ASSIGN_COMPUTED.finditer(line)]
            for name in names:
                hits[(rel, name)] = hits.get((rel, name), 0) + 1
    return hits


def test_no_file_chains_a_hook_by_hand_outside_the_allow_list() -> None:
    """The point of ``chainHook``: ONE implementation of "capture the original,
    wrap, call through, never break the node". A file that writes the wrapper
    out again is the drift this pack kept paying for (docs/plan/ROADMAP-shared-
    panel-code.md); use ``chainHook`` or add a reasoned HAND_CHAIN_ALLOW entry."""
    hits = _hand_chain_hits()
    unexpected = {key: n for key, n in hits.items() if key not in HAND_CHAIN_ALLOW}
    assert not unexpected, f"hand-chained hooks (use chainHook from panel_helpers.js): {unexpected}"
    over = {
        key: (n, HAND_CHAIN_ALLOW[key][0]) for key, n in hits.items() if n > HAND_CHAIN_ALLOW[key][0]
    }
    assert not over, f"more hand-chained lines than the allow-list says (found, allowed): {over}"


def test_the_allow_list_has_no_stale_entries() -> None:
    hits = _hand_chain_hits()
    stale = {
        key: (hits.get(key, 0), count)
        for key, (count, _reason) in HAND_CHAIN_ALLOW.items()
        if hits.get(key, 0) != count
    }
    assert not stale, f"allow-list entries that no longer match the source (found, listed): {stale}"


def test_every_allow_list_entry_states_a_reason() -> None:
    for key, (count, reason) in HAND_CHAIN_ALLOW.items():
        assert count >= 1 and len(reason) > 20, key


def test_the_scan_really_sees_a_hand_chain() -> None:
    """The scan above must be able to fail: feed it the old shape."""
    old_shape = (
        "  const original = node.onConfigure\n"
        "  node.onConfigure = function (info) {\n"
        "    return original?.call(this, info)\n"
        "  }\n"
    )
    code = _strip_comments(old_shape)
    assert _CAPTURE_HOOK.search(code) and _ASSIGN_HOOK.search(code)
    assert _ASSIGN_COMPUTED.search("    graph[hook] = function (...args) {")
    assert _CAPTURE_HOOK.search("  const original = widget.callback\n")
    assert not _ASSIGN_HOOK.search(_strip_comments("  // node.onConfigure = function"))
    assert not _ASSIGN_HOOK.search("input.onchange = null")


def test_chain_hook_is_declared_once_and_imported_everywhere_it_is_used() -> None:
    declaration = re.compile(r"^(?:export )?(?:function|const) chainHook\b", re.M)
    offenders = [
        path.relative_to(WEB).as_posix()
        for path in sorted(WEB.rglob("*.js"))
        if path != PANEL_HELPERS_JS and declaration.search(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, offenders
    import_line = re.compile(r"^import \{[^}]*\bchainHook\b[^}]*\} from '[./]*(?:lora_library/)?panel_helpers\.js'", re.M)
    missing = [
        path.relative_to(WEB).as_posix()
        for path in sorted(WEB.rglob("*.js"))
        if path != PANEL_HELPERS_JS
        and re.search(r"\bchainHook\(", _strip_comments(path.read_text(encoding="utf-8")))
        and not import_line.search(path.read_text(encoding="utf-8"))
    ]
    assert not missing, f"uses chainHook without importing it: {missing}"
