"""Frontend tests for cross-OS model-path healing on load (FORMAT.md §7.6,
``web/lora_library/path_heal.js``; owner report 2026-08-22: a workflow saved
on the Windows PC opens on the Linux box with every model combo's value
spelled ``styles\\x.safetensors`` -- not in the local list, painted missing,
queue rejected, every model re-picked by hand).

``path_heal.js`` factors its logic into PURE exported functions --
``healComboValue`` (the matcher), ``comboValuesOf`` (array / function /
object option lists), ``healNode`` (in-place over a node's widgets),
``isHealEnabled`` (the setting gate) -- plus the two hook bodies
``loadedGraphNode`` / ``attachConfigureHeal``, all driven here under Node
following ``tests/test_picker_js.py``'s "served-layout" convention: the
module imports ``./api.js`` and ``../../../scripts/app.js`` resolved against
the served layout, so the fixture mirrors that depth in a tmp dir,
byte-copies the real siblings in, and stubs ``scripts/app.js`` with a
settings + toast recorder so the gate and the one-toast coalescer are
exercised for real (not just source-pinned).

The wiring -- the ``loadedGraphNode`` registration in ``web/lora_library.js``,
the ``nodeCreated`` chain, the ``settings.js`` entry -- is pinned via
SOURCE-TEXT assertions (the repo's convention for code that only runs
inside a live ComfyUI). Skips cleanly when Node isn't installed; the live
mechanics (a real workflow load, the toast on screen) are for the rig.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PATH_HEAL_JS = REPO_ROOT / "web" / "lora_library" / "path_heal.js"
API_JS = REPO_ROOT / "web" / "lora_library" / "api.js"
VERSION_JS = REPO_ROOT / "web" / "lora_library" / "version.js"
SETTINGS_JS = REPO_ROOT / "web" / "lora_library" / "settings.js"
ENTRY_JS = REPO_ROOT / "web" / "lora_library.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

SETTING_ID = "EPSNodes.HealModelPaths"

# --------------------------------------------------------------- case tables

#: (value as a JS source snippet, options as a Python list -> JSON, expected
#: {healed, value}). Raw JS for the value so `undefined` / non-strings have a
#: representation; the options go through json.dumps, so a Python
#: single-backslash string lands in JS as a single-backslash string.
HEAL_CASES = [
    # exact-present: untouched, even though another option normalizes to it
    ("'vae/x.st'", ["vae/x.st", "vae\\x.st"], {"healed": False, "value": "vae/x.st"}),
    # Windows-saved -> this machine's forward slash
    (
        "'styles\\\\film.safetensors'",
        ["a.safetensors", "styles/film.safetensors"],
        {"healed": True, "value": "styles/film.safetensors"},
    ),
    # the reverse trip: forward-slash saved -> Windows list
    (
        "'styles/film.safetensors'",
        ["styles\\film.safetensors", "other.safetensors"],
        {"healed": True, "value": "styles\\film.safetensors"},
    ),
    # nested folders, mixed separators in the saved value
    ("'a\\\\b/c.st'", ["a/b/c.st"], {"healed": True, "value": "a/b/c.st"}),
    # no match anywhere: unhealed, value untouched
    ("'gone.st'", ["a.st", "b/c.st"], {"healed": False, "value": "gone.st"}),
    # two DISTINCT candidates collapse onto the key: never guess
    ("'p\\\\q.st'", ["p/q.st", "p\\q.st "], {"healed": False, "value": "p\\q.st"}),
    # the same option listed twice is still ONE candidate
    ("'p\\\\q.st'", ["p/q.st", "p/q.st"], {"healed": True, "value": "p/q.st"}),
    # whitespace is trimmed on both sides
    ("'  a/b.st '", ["a/b.st"], {"healed": True, "value": "a/b.st"}),
    ("'a/b.st'", [" a/b.st"], {"healed": True, "value": " a/b.st"}),
    # case is NOT folded (significant on Linux)
    ("'A/b.st'", ["a/b.st"], {"healed": False, "value": "A/b.st"}),
    ("'Styles\\\\Film.st'", ["styles/film.st"], {"healed": False, "value": "Styles\\Film.st"}),
    # non-strings / undefined / junk options: untouched, never throws
    ("undefined", ["a/b.st"], {"healed": False}),
    ("null", ["a/b.st"], {"healed": False, "value": None}),
    ("42", ["a/b.st"], {"healed": False, "value": 42}),
    ("'a\\\\b.st'", None, {"healed": False, "value": "a\\b.st"}),
    ("'a\\\\b.st'", "not-an-array", {"healed": False, "value": "a\\b.st"}),
    ("'a\\\\b.st'", [1, None, "a/b.st"], {"healed": True, "value": "a/b.st"}),
    # whitespace-only value never matches anything
    ("'   '", ["   ", "x"], {"healed": False, "value": "   "}),
]

#: (values expression as JS, expected comboValuesOf() array).
COMBO_VALUES_CASES = [
    ("{ options: { values: ['a', 'b/c'] } }", ["a", "b/c"]),
    ("{ options: { values: () => ['f/g'] } }", ["f/g"]),
    ("{ options: { values: { 'k/1': 1, 'k2': 2 } } }", ["k/1", "k2"]),
    ("{ options: { values: () => { throw new Error('boom') } } }", []),
    ("{ options: { values: () => 'not an array' } }", []),
    ("{ options: {} }", []),
    ("{}", []),
    ("null", []),
]

PROBE_JS = """
import * as m from './extensions/comfyui-epsnodes/lora_library/path_heal.js'
import { app } from './scripts/app.js'

// path_heal.js logs every heal via api.log (console.log) -- keep stdout
// pure JSON for the Python side by routing those lines to stderr.
console.log = (...args) => console.error(...args)
globalThis.__logged = []
const origWarn = console.warn
console.warn = (...args) => {
  globalThis.__logged.push(args.map(String).join(' '))
  origWarn(...args)
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 10))

function fakeNode(id, widgets, extra = {}) {
  const node = { id, title: `Node ${id}`, widgets, dirtyCalls: 0, ...extra }
  node.setDirtyCanvas = function () { this.dirtyCalls += 1 }
  return node
}

/** A node holding one of every interesting combo shape; healable = 2. */
function sampleNode(id) {
  const callbackCalls = []
  const node = fakeNode(id, [
    // healable (array values): Windows spelling -> local forward slash
    { name: 'ckpt_name', type: 'combo', value: 'subdir\\\\x.safetensors',
      options: { values: ['a.safetensors', 'subdir/x.safetensors'] },
      callback: (v) => callbackCalls.push(v) },
    // healable (FUNCTION values): forward-slash saved -> this list's backslash
    { name: 'lora_name', type: 'combo', value: 'styles/film.safetensors',
      options: { values: () => ['styles\\\\film.safetensors', 'other.safetensors'] } },
    // combo without any separator in its options: never considered
    { name: 'sampler_name', type: 'combo', value: 'euler_x',
      options: { values: ['euler', 'dpmpp_2m'] } },
    // not a combo at all (no options.values, type text)
    { name: 'text', type: 'text', value: 'a\\\\b', options: {} },
    // exact-present: untouched even though a sibling option normalizes to it
    { name: 'vae_name', type: 'combo', value: 'vae/x.st',
      options: { values: ['vae/x.st', 'vae\\\\x.st'] } },
    // collision: two distinct local files differ only by separator -> untouched
    { name: 'amb', type: 'combo', value: 'p\\\\q.st',
      options: { values: ['p/q.st', 'p\\\\q.st '] } },
    // genuinely missing here: untouched
    { name: 'gone', type: 'combo', value: 'nope\\\\x.st', options: { values: ['y/z.st'] } },
    // non-string value: untouched
    { name: 'num', type: 'combo', value: 5, options: { values: ['a/b'] } },
    // custom combo type (not type 'combo' but has options.values): present -> untouched
    { name: 'custom', type: 'my_combo', value: 'c/d.st', options: { values: ['c/d.st'] } }
  ])
  node.callbackCalls = callbackCalls
  return node
}

const valuesOf = (node) => Object.fromEntries(node.widgets.map((w) => [w.name, w.value]))

const out = {
  exports: {
    hasHealComboValue: typeof m.healComboValue === 'function',
    hasComboValuesOf: typeof m.comboValuesOf === 'function',
    hasHealNode: typeof m.healNode === 'function',
    hasIsHealEnabled: typeof m.isHealEnabled === 'function',
    hasLoadedGraphNode: typeof m.loadedGraphNode === 'function',
    hasAttachConfigureHeal: typeof m.attachConfigureHeal === 'function',
    hasNormalizeSeparators: typeof m.normalizeSeparators === 'function'
  },
  settingId: m.HEAL_SETTING_ID,
  healComboValue: [%(heal_cases)s].map(([v, opts]) => m.healComboValue(v, opts)),
  comboValuesOf: [%(combo_values_cases)s].map((w) => m.comboValuesOf(w, {})),
  normalizeSeparators: [m.normalizeSeparators('a\\\\b\\\\c'), m.normalizeSeparators(7)]
}

// ---- healNode over a fake node
{
  const node = sampleNode(1)
  out.healNode = {
    count: m.healNode(node),
    values: valuesOf(node),
    dirtyCalls: node.dirtyCalls,
    callbackCalls: node.callbackCalls,
    secondPassCount: m.healNode(node),
    dirtyCallsAfterSecondPass: node.dirtyCalls,
    noWidgets: m.healNode({ id: 9 }),
    nullNode: m.healNode(null),
    widgetsNotArray: m.healNode({ widgets: 'nope' })
  }
}

// ---- the setting gate
globalThis.__settings = {}
out.gate = { unset: m.isHealEnabled() }
globalThis.__settings[m.HEAL_SETTING_ID] = false
out.gate.off = m.isHealEnabled()
globalThis.__settings[m.HEAL_SETTING_ID] = true
out.gate.on = m.isHealEnabled()

// ---- loadedGraphNode: OFF -> untouched, no toast
globalThis.__toasts = []
globalThis.__settings[m.HEAL_SETTING_ID] = false
{
  const node = sampleNode(2)
  m.loadedGraphNode(node)
  await tick()
  out.hookOff = { values: valuesOf(node), toasts: globalThis.__toasts.length }
}

// ---- loadedGraphNode: ON -> two nodes, one coalesced toast
globalThis.__toasts = []
globalThis.__settings[m.HEAL_SETTING_ID] = true
{
  const a = sampleNode(3)
  const b = sampleNode(4)
  m.loadedGraphNode(a)
  m.loadedGraphNode(b)
  m.loadedGraphNode(null) // must not throw
  m.loadedGraphNode({ widgets: [{ // a throwing values() must not throw out of the hook
    name: 'x', type: 'combo', value: 'a/b', options: { values: () => { throw new Error('boom') } }
  }] })
  const toastsBeforeTick = globalThis.__toasts.length
  await tick()
  out.hookOn = {
    valuesA: valuesOf(a),
    valuesB: valuesOf(b),
    toastsBeforeTick,
    toasts: globalThis.__toasts.map((t) => ({
      severity: t.severity,
      summary: t.summary,
      hasDetail: typeof t.detail === 'string' && t.detail.length > 0
    }))
  }
}

// ---- unset setting reads as ON
globalThis.__toasts = []
delete globalThis.__settings[m.HEAL_SETTING_ID]
{
  const node = sampleNode(5)
  m.loadedGraphNode(node)
  await tick()
  out.hookUnset = { values: valuesOf(node), toasts: globalThis.__toasts.map((t) => t.summary) }
}

// ---- a single heal -> singular wording
globalThis.__toasts = []
{
  const node = fakeNode(6, [
    { name: 'ckpt_name', type: 'combo', value: 'sub\\\\one.st',
      options: { values: ['sub/one.st'] } }
  ])
  m.loadedGraphNode(node)
  await tick()
  out.hookSingle = {
    value: node.widgets[0].value,
    toasts: globalThis.__toasts.map((t) => t.summary)
  }
}

// ---- attachConfigureHeal: chained onConfigure heals a pasted node
globalThis.__toasts = []
globalThis.__settings[m.HEAL_SETTING_ID] = true
{
  const calls = []
  const node = sampleNode(7)
  node.onConfigure = function (info) { calls.push({ self: this === node, info }); return 'orig' }
  m.attachConfigureHeal(node)
  const first = node.onConfigure
  m.attachConfigureHeal(node) // double nodeCreated -> no second wrap
  const ret = node.onConfigure({ k: 1 })
  await tick()
  out.configure = {
    ret,
    calls,
    sameWrapperAfterDoubleAttach: node.onConfigure === first,
    values: valuesOf(node),
    toasts: globalThis.__toasts.map((t) => t.summary)
  }
  // OFF -> original still runs, nothing healed, no toast
  globalThis.__toasts = []
  globalThis.__settings[m.HEAL_SETTING_ID] = false
  const node2 = sampleNode(8)
  let origRan = false
  node2.onConfigure = function () { origRan = true }
  m.attachConfigureHeal(node2)
  node2.onConfigure({})
  await tick()
  out.configureOff = { origRan, values: valuesOf(node2), toasts: globalThis.__toasts.length }
  // no original onConfigure at all -> still fine
  globalThis.__settings[m.HEAL_SETTING_ID] = true
  const node3 = sampleNode(9)
  m.attachConfigureHeal(node3)
  const ret3 = node3.onConfigure({})
  await tick()
  out.configureNoOriginal = { ret3: ret3 === undefined, healedCkpt: node3.widgets[0].value }
  m.attachConfigureHeal(null) // must not throw
}

process.stdout.write(JSON.stringify(out))
"""

APP_STUB_JS = """
globalThis.__settings = {}
globalThis.__toasts = []
export const app = {
  extensionManager: {
    setting: { get: (id) => globalThis.__settings[id] },
    toast: { add: (toast) => { globalThis.__toasts.push(toast) } }
  }
}
"""


@pytest.fixture(scope="module")
def heal_api(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Runs the probe against the REAL path_heal.js in a served-layout tmp
    dir (see module docstring) and returns its JSON output."""
    layout = tmp_path_factory.mktemp("web_root")

    module_dir = layout / "extensions" / "comfyui-epsnodes" / "lora_library"
    module_dir.mkdir(parents=True)
    shutil.copyfile(PATH_HEAL_JS, module_dir / "path_heal.js")
    # path_heal.js imports `./api.js` (-> `./version.js`, `../../../scripts/
    # api.js`) and `../../../scripts/app.js` -- copy the real siblings, stub
    # the served ComfyUI scripts (app.js records settings reads + toasts).
    shutil.copyfile(API_JS, module_dir / "api.js")
    shutil.copyfile(VERSION_JS, module_dir / "version.js")

    scripts = layout / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "api.js").write_text("export const api = { fetchApi: () => {} }\n", encoding="utf-8")
    (scripts / "app.js").write_text(APP_STUB_JS, encoding="utf-8")

    heal_cases = ", ".join(f"[{value}, {json.dumps(options)}]" for value, options, _ in HEAL_CASES)
    combo_values_cases = ", ".join(expr for expr, _ in COMBO_VALUES_CASES)
    probe = layout / "probe.mjs"
    probe.write_text(
        PROBE_JS % {"heal_cases": heal_cases, "combo_values_cases": combo_values_cases},
        encoding="utf-8",
    )

    result = subprocess.run(
        [NODE, str(probe)], capture_output=True, text=True, timeout=60, cwd=layout
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def source() -> str:
    """Raw text of path_heal.js -- for SOURCE-STRUCTURE pins."""
    return PATH_HEAL_JS.read_text(encoding="utf-8")


def _function_body(source_text: str, signature: str) -> str:
    """The body of a top-level ``function <signature> {`` declaration (an
    `export` prefix, if any, is not part of *signature*), up to its closing
    brace at column 0 -- test_checkpoint_switcher_js.py's identical helper."""
    start_match = re.search(re.escape(f"function {signature} {{") + r"\n", source_text)
    assert start_match, f"function {signature} {{ not found"
    start = start_match.end()
    end_match = re.search(r"\n\}\n", source_text[start:])
    assert end_match, f"function {signature}'s closing brace not found"
    return source_text[start : start + end_match.start()]


# ------------------------------------------------------------- parses / exports


def test_path_heal_js_parses() -> None:
    result = subprocess.run(
        [NODE, "--check", str(PATH_HEAL_JS)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


def test_module_exports_the_hooks_and_pure_helpers(heal_api: dict) -> None:
    assert heal_api["exports"] == {
        "hasHealComboValue": True,
        "hasComboValuesOf": True,
        "hasHealNode": True,
        "hasIsHealEnabled": True,
        "hasLoadedGraphNode": True,
        "hasAttachConfigureHeal": True,
        "hasNormalizeSeparators": True,
    }


def test_setting_id_constant(heal_api: dict) -> None:
    assert heal_api["settingId"] == SETTING_ID


def test_normalize_separators(heal_api: dict) -> None:
    assert heal_api["normalizeSeparators"] == ["a/b/c", 7]


# --------------------------------------------------------------- healComboValue


def test_heal_combo_value_cases(heal_api: dict) -> None:
    pairs = zip(HEAL_CASES, heal_api["healComboValue"], strict=True)
    for (value_js, options, expected), got in pairs:
        msg = f"healComboValue({value_js}, {options!r}) -> {got!r}, wanted {expected!r}"
        assert got["healed"] is expected["healed"], msg
        if "value" in expected:  # `undefined` has no JSON form -> key absent
            assert got.get("value") == expected["value"], msg
        else:
            assert "value" not in got, msg


def test_heal_combo_value_never_lowercases(source: str) -> None:
    """Case can be significant on Linux; the matcher must never fold it."""
    assert "toLowerCase" not in source
    assert "toUpperCase" not in source
    assert "localeCompare" not in source


def test_combo_values_of_cases(heal_api: dict) -> None:
    pairs = zip(COMBO_VALUES_CASES, heal_api["comboValuesOf"], strict=True)
    for (expr, expected), got in pairs:
        assert got == expected, f"comboValuesOf({expr}) -> {got!r}, wanted {expected!r}"


# --------------------------------------------------------------------- healNode


def test_heal_node_heals_only_the_healable_widgets(heal_api: dict) -> None:
    result = heal_api["healNode"]
    assert result["count"] == 2
    assert result["values"] == {
        "ckpt_name": "subdir/x.safetensors",  # array values, \\ -> /
        "lora_name": "styles\\film.safetensors",  # FUNCTION values, / -> \\
        "sampler_name": "euler_x",  # no separator in options: never considered
        "text": "a\\b",  # not a combo
        "vae_name": "vae/x.st",  # exact-present
        "amb": "p\\q.st",  # collision: never guess
        "gone": "nope\\x.st",  # genuinely missing here
        "num": 5,  # non-string
        "custom": "c/d.st",  # custom combo type, present
    }


def test_heal_node_marks_the_canvas_dirty_once_and_never_fires_callbacks(heal_api: dict) -> None:
    """A restore-time correction, not a user edit: `widget.value` is written
    directly, `widget.callback` is never invoked (it would run every
    on-change side effect for a spelling fix), and setDirtyCanvas fires
    once per healed node -- not per widget, and not at all on a no-op pass."""
    result = heal_api["healNode"]
    assert result["dirtyCalls"] == 1
    assert result["callbackCalls"] == []
    assert result["secondPassCount"] == 0
    assert result["dirtyCallsAfterSecondPass"] == 1


def test_heal_node_tolerates_degenerate_nodes(heal_api: dict) -> None:
    result = heal_api["healNode"]
    assert result["noWidgets"] == 0
    assert result["nullNode"] == 0
    assert result["widgetsNotArray"] == 0


def test_heal_node_never_calls_widget_callback(source: str) -> None:
    body = _function_body(source, "healNode(node)")
    assert ".callback(" not in body
    assert ".callback?.(" not in body
    assert "widget.value = result.value" in body
    assert "node.setDirtyCanvas(true, true)" in body


# ------------------------------------------------------------------- the gate


def test_setting_gate_reads_fresh_and_defaults_on(heal_api: dict) -> None:
    assert heal_api["gate"] == {"unset": True, "off": False, "on": True}


def test_gate_uses_the_packs_settings_accessor(source: str) -> None:
    body = _function_body(source, "isHealEnabled()")
    assert "app.extensionManager?.setting?.get?.(HEAL_SETTING_ID)" in body
    assert "return value !== false" in body


# ------------------------------------------------------- loadedGraphNode hook


def test_hook_off_leaves_values_and_shows_no_toast(heal_api: dict) -> None:
    result = heal_api["hookOff"]
    assert result["values"]["ckpt_name"] == "subdir\\x.safetensors"
    assert result["values"]["lora_name"] == "styles/film.safetensors"
    assert result["toasts"] == 0


def test_hook_on_heals_and_shows_exactly_one_coalesced_toast(heal_api: dict) -> None:
    """Two nodes, two heals each, one junk node, one null -> ONE toast after
    the load settles (setTimeout 0), none synchronously."""
    result = heal_api["hookOn"]
    assert result["valuesA"]["ckpt_name"] == "subdir/x.safetensors"
    assert result["valuesB"]["lora_name"] == "styles\\film.safetensors"
    assert result["toastsBeforeTick"] == 0
    assert len(result["toasts"]) == 1
    toast = result["toasts"][0]
    assert toast["severity"] == "info"
    assert toast["summary"] == "EPSNodes: healed 4 model paths for this machine"
    assert toast["hasDetail"] is True


def test_hook_unset_setting_reads_as_on(heal_api: dict) -> None:
    result = heal_api["hookUnset"]
    assert result["values"]["ckpt_name"] == "subdir/x.safetensors"
    assert result["toasts"] == ["EPSNodes: healed 2 model paths for this machine"]


def test_hook_singular_wording(heal_api: dict) -> None:
    result = heal_api["hookSingle"]
    assert result["value"] == "sub/one.st"
    assert result["toasts"] == ["EPSNodes: healed 1 model path for this machine"]


def test_hook_is_gated_try_caught_and_coalesced(source: str) -> None:
    body = _function_body(source, "loadedGraphNode(node)")
    assert body.strip().startswith("try {")
    assert "if (!isHealEnabled()) return" in body
    assert "recordHealed(healNode(node))" in body
    assert "} catch (error) {" in body
    record = _function_body(source, "recordHealed(count)")
    assert "if (toastTimer !== null) return" in record
    assert "setTimeout(flushHealToast, 0)" in record
    flush = _function_body(source, "flushHealToast()")
    assert "app.extensionManager?.toast?.add?.(" in flush
    assert "pendingHealed = 0" in flush
    assert "toastTimer = null" in flush


# ------------------------------------------------------ attachConfigureHeal


def test_configure_wrap_chains_the_original_and_heals(heal_api: dict) -> None:
    result = heal_api["configure"]
    assert result["ret"] == "orig"
    assert result["calls"] == [{"self": True, "info": {"k": 1}}]
    assert result["sameWrapperAfterDoubleAttach"] is True
    assert result["values"]["ckpt_name"] == "subdir/x.safetensors"
    assert result["values"]["lora_name"] == "styles\\film.safetensors"
    assert result["toasts"] == ["EPSNodes: healed 2 model paths for this machine"]


def test_configure_wrap_respects_the_gate_and_a_missing_original(heal_api: dict) -> None:
    off = heal_api["configureOff"]
    assert off["origRan"] is True
    assert off["values"]["ckpt_name"] == "subdir\\x.safetensors"
    assert off["toasts"] == 0
    none = heal_api["configureNoOriginal"]
    assert none["ret3"] is True
    assert none["healedCkpt"] == "subdir/x.safetensors"


def test_configure_wrap_source_shape(source: str) -> None:
    body = _function_body(source, "attachConfigureHeal(node)")
    assert "wrappedNodes.has(node)" in body
    assert "const originalOnConfigure = node.onConfigure" in body
    assert "node.onConfigure = function (info) {" in body
    assert "originalOnConfigure.apply(this, arguments)" in body
    assert "recordHealed(healNode(this))" in body


# ---------------------------------------------------- window-listener safety


def test_no_window_listeners_at_all(source: str) -> None:
    """FORMAT.md §7.5: nothing here listens on window (no gestures to
    commit) -- a future bubble-phase listener would silently die under Vue
    nodes, so pin the absence."""
    assert "window.addEventListener" not in source
    assert "document.addEventListener" not in source


# ------------------------------------------------------------ wiring pins


def test_entry_file_registers_loaded_graph_node_and_the_configure_chain() -> None:
    entry = ENTRY_JS.read_text(encoding="utf-8")
    assert "import * as pathHeal from './lora_library/path_heal.js'" in entry
    assert "loadedGraphNode(node) {" in entry
    assert "safely('pathHeal.loadedGraphNode', () => pathHeal.loadedGraphNode(node))" in entry
    node_created = entry.split("nodeCreated(node) {", 1)[1].split("\n  },", 1)[0]
    assert (
        "safely('pathHeal.attachConfigureHeal', () => pathHeal.attachConfigureHeal(node))"
        in node_created
    )
    # every other nodeCreated attach is still there -- wrapped, never replaced
    assert "safely('picker.attachPickerPanel'" in node_created
    assert "safely('notebook.attachNotebookWidget'" in node_created
    assert "safely('sets.attachApplySetBehavior'" in node_created


def test_settings_js_registers_the_boolean_default_on() -> None:
    settings = SETTINGS_JS.read_text(encoding="utf-8")
    assert "import { HEAL_SETTING_ID } from './path_heal.js'" in settings
    entry = settings.split("id: HEAL_SETTING_ID,", 1)[1].split("\n  },", 1)[0]
    assert "name: 'Heal model paths across operating systems on load'" in entry
    assert "type: 'boolean'" in entry
    assert "defaultValue: true" in entry
    assert "category: [CATEGORY, 'Workflows', 'Model paths']" in entry
    assert "tooltip:" in entry
    assert "missing" in entry and "exactly ONE local file" in entry
