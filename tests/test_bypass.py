"""Tests for eps_image.nodes_bypass (FORMAT.md section 6.18, `EPSBypass`).

No ComfyUI/torch anywhere: the passed-through "audio" is a plain `object()`
sentinel, since the node carries any value opaquely -- so identity (`is`)
is what every enabled-path assertion checks. The `fake_execution_blocker`
fixture is `tests/test_distributor.py`'s, the same convention every
ExecutionBlocker-returning node in this pack is tested with.

The node's real switching happens in the FRONTEND (it unplugs the wire --
see `tests/test_bypass_js.py`); what is pinned here is the contract that
frontend depends on: the widget ORDER (a persistence contract, FORMAT.md
section 8), the wildcard typing, the silent-blocker fallback, and the
registration/state-registry declarations.
"""

from __future__ import annotations

import inspect
import sys
import types
from pathlib import Path

import pytest

from eps_image import nodes_bypass
from eps_image.nodes_bypass import DEFAULT_LINKS, EPSBypass

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The input kinds that become a WIDGET (and so occupy a `widgets_values`
#: slot); anything else in INPUT_TYPES is a socket. Mirrors the frontend's
#: own rule and `tests/test_state_registry.py`'s `_WIDGET_KINDS`.
_WIDGET_TYPES = ("STRING", "INT", "FLOAT", "BOOLEAN")


@pytest.fixture
def fake_execution_blocker(monkeypatch: pytest.MonkeyPatch):
    """A fake `comfy_execution.graph.ExecutionBlocker` in `sys.modules`
    (tests/test_distributor.py's fixture of the same name) -- the disabled
    path imports it lazily from exactly this module path. Returns the fake
    class so tests can `isinstance()` the returned blocker."""

    class FakeExecutionBlocker:
        def __init__(self, message: object) -> None:
            self.message = message

    fake_graph = types.ModuleType("comfy_execution.graph")
    fake_graph.ExecutionBlocker = FakeExecutionBlocker
    fake_pkg = types.ModuleType("comfy_execution")
    fake_pkg.graph = fake_graph
    monkeypatch.setitem(sys.modules, "comfy_execution", fake_pkg)
    monkeypatch.setitem(sys.modules, "comfy_execution.graph", fake_graph)
    return FakeExecutionBlocker


def _widget_order() -> list[str]:
    """The names of every widget input, in the order `widgets_values` will
    restore them: required first, then optional, declaration order within
    each -- exactly how the frontend lays them out. Sockets are skipped."""
    spec = EPSBypass.INPUT_TYPES()
    order: list[str] = []
    for section in ("required", "optional"):
        for name, definition in spec.get(section, {}).items():
            kind = definition[0]
            options = definition[1] if len(definition) > 1 else {}
            if options.get("forceInput"):
                continue
            if isinstance(kind, list) or kind in _WIDGET_TYPES:
                order.append(name)
    return order


# ----------------------------------------------------------- passthrough


class TestEnabledPassesThrough:
    def test_returns_the_very_same_object(self) -> None:
        audio = object()
        result = EPSBypass().bypass(enabled=True, value=audio)
        assert result == (audio,)
        assert result[0] is audio  # identity, no copy

    def test_enabled_is_the_default(self) -> None:
        audio = object()
        assert EPSBypass().bypass(value=audio)[0] is audio

    def test_an_unwired_input_passes_none_not_a_blocker(self) -> None:
        # An absent optional input arrives as None. None is what an optional
        # consumer's own default already is, so a muted/absent upstream still
        # reads as "nothing" to it -- a blocker here would SKIP the consumer,
        # the exact failure this node exists to avoid. No comfy import needed
        # on this path (no fake_execution_blocker fixture on purpose).
        assert EPSBypass().bypass(enabled=True, value=None) == (None,)

    def test_the_result_is_always_a_one_tuple(self) -> None:
        assert len(EPSBypass().bypass(enabled=True, value=object())) == 1

    @pytest.mark.parametrize("value", [0, "", [], {}, False])
    def test_falsy_values_are_carried_faithfully(self, value: object) -> None:
        # The node never inspects what it carries: an empty list or a zero is
        # a legitimate value, not "nothing".
        assert EPSBypass().bypass(enabled=True, value=value)[0] is value

    def test_links_is_ignored_entirely(self) -> None:
        audio = object()
        for links in (DEFAULT_LINKS, "{}", "not json at all", '{"owner": 1, "links": []}', None):
            assert EPSBypass().bypass(enabled=True, value=audio, links=links)[0] is audio


# ----------------------------------------------- disabled: the fallback path


class TestDisabledIsASilentBlocker:
    def test_disabled_returns_a_silent_blocker_not_the_value(
        self, fake_execution_blocker
    ) -> None:
        audio = object()
        result = EPSBypass().bypass(enabled=False, value=audio)
        assert len(result) == 1
        assert isinstance(result[0], fake_execution_blocker)
        assert result[0] is not audio
        # `None` message = SILENT (core reports a string message as an error
        # in the UI); this is the refused-unplug fallback, not a user error.
        assert result[0].message is None

    def test_off_means_off_even_with_nothing_wired(self, fake_execution_blocker) -> None:
        result = EPSBypass().bypass(enabled=False, value=None)
        assert isinstance(result[0], fake_execution_blocker)

    def test_a_fresh_blocker_each_call(self, fake_execution_blocker) -> None:
        first = EPSBypass().bypass(enabled=False, value=object())[0]
        second = EPSBypass().bypass(enabled=False, value=object())[0]
        assert first is not second

    @pytest.mark.parametrize("falsy", [0, "", [], 0.0])
    def test_any_falsy_enabled_is_off(self, fake_execution_blocker, falsy: object) -> None:
        # A BOOLEAN wired from another node can arrive as a falsy non-bool.
        # "off always means off" -- never leak the value through.
        result = EPSBypass().bypass(enabled=falsy, value=object())  # type: ignore[arg-type]
        assert isinstance(result[0], fake_execution_blocker)

    def test_none_enabled_means_on(self) -> None:
        # A real BOOLEAN widget always has a value, so None can only be an
        # API caller that left it out: ON is the least-surprising default
        # (the Distributor's rule). No blocker, no comfy import.
        audio = object()
        assert EPSBypass().bypass(enabled=None, value=audio)[0] is audio  # type: ignore[arg-type]


# --------------------------------------------------------- wildcard typing


class TestWildcardTyping:
    def test_output_is_a_single_wildcard(self) -> None:
        assert EPSBypass.RETURN_TYPES == ("*",)

    def test_return_names_and_tooltips_match_the_one_output(self) -> None:
        assert len(EPSBypass.RETURN_NAMES) == 1
        assert len(EPSBypass.OUTPUT_TOOLTIPS) == 1

    def test_input_is_one_optional_wildcard_socket(self) -> None:
        spec = EPSBypass.INPUT_TYPES()
        assert spec["optional"]["value"][0] == "*"
        assert "value" not in spec["required"]

    def test_the_wildcard_input_is_not_a_widget(self) -> None:
        # A socket must never occupy a `widgets_values` slot.
        assert "value" not in _widget_order()

    def test_the_wildcard_input_is_not_lazy(self) -> None:
        # Deliberate: see the module docstring ("no lazy inputs"). A lazy
        # optional input would need a wiring-aware check_lazy_status (asking
        # core for an input that isn't in the prompt raises NodeInputError).
        options = EPSBypass.INPUT_TYPES()["optional"]["value"][1]
        assert not options.get("lazy")
        assert not hasattr(EPSBypass, "check_lazy_status")


# ------------------------------------------------------------ widget order


class TestWidgetOrderIsAPersistenceContract:
    """FORMAT.md section 8: `widgets_values` restores POSITIONALLY, so this
    order is frozen the moment it ships."""

    def test_enabled_first_then_links(self) -> None:
        assert _widget_order() == ["enabled", "links"]

    def test_enabled_is_a_visible_boolean_defaulting_on(self) -> None:
        kind, options = EPSBypass.INPUT_TYPES()["required"]["enabled"]
        assert kind == "BOOLEAN"
        assert options["default"] is True
        assert not options.get("hidden")

    def test_enabled_spells_out_off_on_the_toggle_itself(self) -> None:
        # The toggle's own text is the "off is obvious" annotation in both
        # renderers (canvas draws options.on/off; Vue uses them as segments).
        options = EPSBypass.INPUT_TYPES()["required"]["enabled"][1]
        assert options["label_on"] == "on"
        assert "off" in options["label_off"]

    def test_links_is_a_hidden_json_string_last(self) -> None:
        kind, options = EPSBypass.INPUT_TYPES()["optional"]["links"]
        assert kind == "STRING"
        assert options["hidden"] is True  # the Vue-nodes hide flag
        assert options["default"] == DEFAULT_LINKS
        assert _widget_order()[-1] == "links"

    def test_the_default_memory_is_an_empty_json_object(self) -> None:
        assert DEFAULT_LINKS == "{}"

    def test_every_input_carries_a_tooltip(self) -> None:
        spec = EPSBypass.INPUT_TYPES()
        for section in ("required", "optional"):
            for name, definition in spec[section].items():
                assert definition[1].get("tooltip"), name


# ------------------------------------------------------------- class shape


class TestClassShape:
    def test_category_is_the_utilities_folder(self) -> None:
        assert EPSBypass.CATEGORY == "EPSNodes/Utilities"

    def test_function_name_matches_the_entry_point(self) -> None:
        assert EPSBypass.FUNCTION == "bypass"
        assert callable(getattr(EPSBypass(), EPSBypass.FUNCTION))

    def test_no_list_flags_and_no_is_changed(self) -> None:
        for attr in ("INPUT_IS_LIST", "OUTPUT_IS_LIST", "IS_CHANGED"):
            assert not hasattr(EPSBypass, attr), attr

    def test_description_says_what_it_refuses(self) -> None:
        # The refusal is a user-facing behaviour; the node help must own it.
        assert "optional" in EPSBypass.DESCRIPTION
        assert "refused" in EPSBypass.DESCRIPTION

    def test_module_never_imports_comfy_or_torch(self) -> None:
        assert "comfy" not in nodes_bypass.__dict__
        assert "torch" not in nodes_bypass.__dict__
        source = inspect.getsource(sys.modules[nodes_bypass.__name__])
        assert "import comfy\n" not in source
        assert "import torch" not in source
        # The one comfy import is lazy: it must sit INSIDE the method, never
        # at column 0.
        assert "\nfrom comfy_execution" not in source
        assert "\n            from comfy_execution.graph import ExecutionBlocker" in source


# ------------------------------------------------------------ registration


class TestRegisteredInThePack:
    """The repo-root __init__.py needs a real ComfyUI to import, so read its
    text (tests/test_checkpoint_switcher.py's convention)."""

    def test_node_spec_entry_present_with_display_name(self) -> None:
        source = (REPO_ROOT / "__init__.py").read_text(encoding="utf-8")
        assert '("eps_image.nodes_bypass", "EPSBypass", "EPS Bypass")' in source

    def test_the_frontend_extension_wires_attach_and_init(self) -> None:
        source = (REPO_ROOT / "web" / "eps_image.js").read_text(encoding="utf-8")
        assert "import * as bypass from './eps_image/bypass.js'" in source
        assert "safely('bypass.init', () => bypass.init?.())" in source
        assert "safely('bypass.attach', () => bypass.attach?.(node))" in source

    def test_it_is_not_in_the_vue_affected_class_set(self) -> None:
        # That set is for HAND-DRAWN canvas controls that vanish under the
        # Vue renderer. EPS Bypass draws nothing (a native BOOLEAN widget
        # plus slot labels), so listing it would raise a false warning.
        source = (REPO_ROOT / "web" / "eps_image.js").read_text(encoding="utf-8")
        set_body = source.split("const VUE_AFFECTED_CLASSES = new Set([", 1)[1].split("])", 1)[0]
        assert "EPSBypass" not in set_body


# ---------------------------------------------------------- state registry


class TestStateRegistryDeclaration:
    """§6.16: only the visible toggle is a captured state. The `links`
    memory must NEVER be: a captured (often empty) memory applied while the
    node is off would leave nothing to reconnect on switch-on."""

    def test_enabled_is_declared_as_a_boolean(self) -> None:
        widgets = EPSBypass.EPS_STATE_WIDGETS["widgets"]
        assert widgets == {"enabled": {"kind": "boolean"}}

    def test_links_is_excluded_with_a_reason(self) -> None:
        excluded = EPSBypass.EPS_STATE_WIDGETS["excluded"]
        assert set(excluded) == {"links"}
        assert "memory" in excluded["links"]

    def test_links_is_not_declared(self) -> None:
        assert "links" not in EPSBypass.EPS_STATE_WIDGETS["widgets"]
