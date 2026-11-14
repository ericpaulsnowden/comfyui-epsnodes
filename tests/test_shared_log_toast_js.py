"""Behaviour tests for the shared logger and toast in
``web/lora_library/api.js`` (``loggerFor`` and ``showToast``,
docs/ROADMAP-shared-panel-code.md M3).

Before M3 each eps_image module hand-wrote ``console.warn(PREFIX, ...)`` and 14
files carried their own copy of the toast helper (4 signature shapes, 3
default lifetimes, 3 fallbacks). These tests run the ONE implementation under
Node in a served layout and guard that every module still logs under its own
prefix, so the browser console keeps attributing a message to the module that
raised it.
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
LORA_LIBRARY = WEB / "lora_library"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

PROBE_JS = """
import { loggerFor, showToast, warn, log } from './extensions/comfyui-epsnodes/lora_library/api.js'
import { app } from './scripts/app.js'

const out = {}
const captured = []
const original = { warn: console.warn, log: console.log }
console.warn = (...args) => captured.push(['warn', ...args])
console.log = (...args) => captured.push(['log', ...args])

function run(label, fn) {
  captured.length = 0
  app.extensionManager = undefined
  app.toasts = []
  fn()
  out[label] = { console: captured.slice(), toasts: app.toasts.slice() }
}
const surface = () => ({ toast: { add(t) { app.toasts.push(t) } } })

const lg = loggerFor('[eps_image:probe]')
run('logger_warn', () => lg.warn('boom'))
run('logger_warn_error', () => lg.warn('boom', 'E'))
run('logger_warn_context', () => lg.warn('slot', 3, 'E'))
run('logger_warn_trailing_undefined', () => lg.warn('slot', 3, undefined))
run('logger_log', () => lg.log('hello'))
run('logger_warn_falsy_error', () => lg.warn('boom', 0))
run('lora_warn', () => warn('x', 'E'))
run('lora_log', () => log('y'))

run('default_info', () => { app.extensionManager = surface(); showToast('info', 'S', 'D') })
run('default_error', () => { app.extensionManager = surface(); showToast('error', 'S', 'D') })
run('lives_info', () => { app.extensionManager = surface(); showToast('info', 'S', 'D', { lives: { error: 8000, other: 5000 } }) })
run('lives_error', () => { app.extensionManager = surface(); showToast('error', 'S', 'D', { lives: { error: 8000, other: 5000 } }) })
run('explicit_life_wins', () => { app.extensionManager = surface(); showToast('error', 'S', 'D', { life: 1234, lives: { error: 8000, other: 5000 } }) })
run('no_detail', () => { app.extensionManager = surface(); showToast('info', 'S', undefined, { life: 8000 }) })

let fallbackCalls = 0
run('missing_surface_silent', () => showToast('warn', 'S', 'D', { warn: lg.warn }))
run('missing_surface_fallback', () => showToast('warn', 'S', 'D', { fallback: () => { fallbackCalls += 1; lg.log('S: D') } }))
run('missing_add_fn', () => { app.extensionManager = { toast: {} }; showToast('warn', 'S', 'D', { fallback: () => lg.log('fb') }) })
run('throwing_add_silent', () => { app.extensionManager = { toast: { add() { throw new Error('nope') } } }; showToast('warn', 'S', 'D') })
run('throwing_add_warns', () => { app.extensionManager = { toast: { add() { throw new Error('nope') } } }; showToast('warn', 'S', 'D', { warn: lg.warn }) })
run('throwing_add_then_fallback', () => {
  app.extensionManager = { toast: { add() { throw new Error('nope') } } }
  showToast('warn', 'S', 'D', { warn: lg.warn, fallback: () => lg.log('fb') })
})
run('add_is_called_on_the_service', () => {
  const svc = { add(t) { app.toasts.push({ ...t, sameThis: this === svc }) } }
  app.extensionManager = { toast: svc }
  showToast('info', 'S', 'D')
})

console.warn = original.warn
console.log = original.log
process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def probe(tmp_path_factory: pytest.TempPathFactory) -> dict:
    layout = tmp_path_factory.mktemp("web_root")
    lora_library = layout / "extensions" / "comfyui-epsnodes" / "lora_library"
    lora_library.mkdir(parents=True)
    for name in ("api.js", "version.js"):
        shutil.copyfile(LORA_LIBRARY / name, lora_library / name)
    scripts = layout / "scripts"
    scripts.mkdir()
    (scripts / "app.js").write_text("export const app = {}\n", encoding="utf-8")
    (scripts / "api.js").write_text("export const api = {}\n", encoding="utf-8")
    (layout / "package.json").write_text('{"type":"module"}', encoding="utf-8")
    (layout / "probe.mjs").write_text(PROBE_JS, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(layout / "probe.mjs")], capture_output=True, encoding="utf-8", timeout=60, cwd=layout
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


# --------------------------------------------------------------- loggerFor


def test_logger_warn_prefixes_with_the_callers_own_prefix(probe: dict) -> None:
    assert probe["logger_warn"]["console"] == [["warn", "[eps_image:probe]", "boom"]]


def test_logger_warn_appends_the_error_only_when_given(probe: dict) -> None:
    assert probe["logger_warn_error"]["console"] == [["warn", "[eps_image:probe]", "boom", "E"]]
    # `error !== undefined`, not truthiness: a falsy error value is still shown
    assert probe["logger_warn_falsy_error"]["console"] == [["warn", "[eps_image:probe]", "boom", 0]]


def test_logger_warn_passes_extra_context_values_and_drops_trailing_undefined(probe: dict) -> None:
    assert probe["logger_warn_context"]["console"] == [["warn", "[eps_image:probe]", "slot", 3, "E"]]
    assert probe["logger_warn_trailing_undefined"]["console"] == [["warn", "[eps_image:probe]", "slot", 3]]


def test_logger_log_prefixes_and_uses_console_log(probe: dict) -> None:
    assert probe["logger_log"]["console"] == [["log", "[eps_image:probe]", "hello"]]


def test_the_lora_library_warn_and_log_are_the_lora_library_logger(probe: dict) -> None:
    assert probe["lora_warn"]["console"] == [["warn", "[lora_library]", "x", "E"]]
    assert probe["lora_log"]["console"] == [["log", "[lora_library]", "y"]]


# --------------------------------------------------------------- showToast


def test_default_lives_are_6000_for_errors_and_3000_otherwise(probe: dict) -> None:
    assert probe["default_error"]["toasts"] == [{"severity": "error", "summary": "S", "detail": "D", "life": 6000}]
    assert probe["default_info"]["toasts"][0]["life"] == 3000


def test_lives_parameter_replaces_the_defaults_per_severity(probe: dict) -> None:
    assert probe["lives_error"]["toasts"][0]["life"] == 8000
    assert probe["lives_info"]["toasts"][0]["life"] == 5000


def test_an_explicit_life_beats_every_default(probe: dict) -> None:
    assert probe["explicit_life_wins"]["toasts"][0]["life"] == 1234


def test_a_summary_only_toast_carries_no_detail(probe: dict) -> None:
    toast = probe["no_detail"]["toasts"][0]
    assert toast["summary"] == "S" and toast.get("detail") is None and toast["life"] == 8000


def test_add_is_called_on_the_toast_service(probe: dict) -> None:
    assert probe["add_is_called_on_the_service"]["toasts"][0]["sameThis"] is True


def test_a_missing_toast_surface_is_silent_unless_a_fallback_is_given(probe: dict) -> None:
    assert probe["missing_surface_silent"] == {"console": [], "toasts": []}
    assert probe["missing_surface_fallback"]["console"] == [["log", "[eps_image:probe]", "S: D"]]
    # `toast` present but no `add` function: same path
    assert probe["missing_add_fn"]["console"] == [["log", "[eps_image:probe]", "fb"]]


def test_a_throwing_add_never_escapes_and_warns_only_when_asked(probe: dict) -> None:
    assert probe["throwing_add_silent"]["console"] == []
    (entry,) = probe["throwing_add_warns"]["console"]
    assert entry[:3] == ["warn", "[eps_image:probe]", "toast failed"]


def test_a_throwing_add_warns_and_then_still_reaches_the_fallback(probe: dict) -> None:
    kinds = [entry[0] for entry in probe["throwing_add_then_fallback"]["console"]]
    assert kinds == ["warn", "log"]


# --------------------------------------- every module logs under its own prefix

#: file -> the PREFIX string its console lines have always carried. A change
#: here is a visible change in the browser console, so it must be deliberate.
PREFIXES = {
    "eps_image.js": "[eps_image]",
    "eps_image/bypass.js": "[eps_image:bypass]",
    "eps_image/checkpoint_switcher.js": "[eps_ckpt]",
    "eps_image/cross_sweep.js": "[eps_image]",
    "eps_image/distributor.js": "[eps_image:distributor]",
    "eps_image/frame_saver.js": "[eps_image:frame_saver]",
    "eps_image/image_grid.js": "[eps_image:image_grid]",
    "eps_image/number_controller.js": "[eps_image:number_controller]",
    "eps_image/resolution.js": "[eps_image/resolution]",
    "eps_image/save_image.js": "[EPSNodes save_image]",
    "eps_image/switcher.js": "[eps_image:switcher]",
}


@pytest.mark.parametrize("relative", sorted(PREFIXES))
def test_each_eps_image_module_binds_warn_to_its_own_prefix(relative: str) -> None:
    source = (WEB / relative).read_text(encoding="utf-8")
    assert f"const PREFIX = '{PREFIXES[relative]}'" in source
    assert "const { warn } = loggerFor(PREFIX)" in source
    # no hand-written copy of the logger or of `console.warn(PREFIX, ...)` is left
    assert not re.search(r"console\.warn\(\s*PREFIX", source)
    assert not re.search(r"^function warn\(", source, re.M)


def test_owned_link_colour_stays_import_free_and_keeps_its_own_console_warn() -> None:
    """The one exception: test_owned_link_colour_js.py pins this module as
    import-free (it is loaded in place by file URI), so it cannot import the
    shared logger and keeps `console.warn(PREFIX, ...)`."""
    source = (WEB / "eps_image" / "owned_link_colour.js").read_text(encoding="utf-8")
    assert "\nimport " not in source
    assert "console.warn(PREFIX" in source


def test_no_module_declares_its_own_toast_add_helper_any_more() -> None:
    """Only the 10 inline one-off sites (no helper function around them) and
    api.js's `showToast` may still call `toast.add`; a helper that builds the
    `{severity, summary, detail, life}` object by hand is the old duplication."""
    offenders = []
    for path in sorted(WEB.rglob("*.js")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"function\s+_?toast\w*\s*\([^)]*\)\s*\{(.*?)\n\s*\}\n", text, re.S):
            if "toast?.add" in match.group(1) or "toast.add(" in match.group(1):
                offenders.append(f"{path.relative_to(WEB)}: {match.group(0).splitlines()[0]}")
    assert offenders == []
