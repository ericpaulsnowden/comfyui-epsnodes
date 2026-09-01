"""Tests for `GET /eps/state_registry` (`eps_image/routes_state_registry.py`)
-- the §6.16 M0 declarative state registry: every state-bearing node's own
`EPS_STATE_WIDGETS` descriptor, collected in one place for the run-count
estimator, EPS Save Image pinning, and the Universal State Controller alike.

Mirrors `tests/test_routes_list_flags.py`'s conventions throughout: a plain
`aiohttp` app wrapping `build_routes()` with a fake `nodes` module installed
into `sys.modules`, the same memoization/`_reset_cache` test shape. Also
exercises every real node class this pack ships (`_STATE_BEARING_SPECS`
below, one row per `__init__.py` `_NODE_SPECS` entry) directly against its
own `EPS_STATE_WIDGETS` attribute -- no ComfyUI needed anywhere, since every
one of these classes stays importable bare (see each module's own
docstring).
"""

from __future__ import annotations

import json
import re
import sys
import threading
import types
from typing import Any, ClassVar

import pytest
from aiohttp import web

from eps_image import routes_state_registry
from eps_image.nodes_checkpoint_switcher import EPSCheckpointSwitcher
from eps_image.nodes_cross_sweep import EPSCrossSweep
from eps_image.nodes_distributor import EPSDistributor
from eps_image.nodes_frame_saver import EPSFrameSaver
from eps_image.nodes_image_grid import EPSImageGrid
from eps_image.nodes_resolution import EPSResolution
from eps_image.nodes_save_image import EPSSaveImage
from eps_image.nodes_switcher import (
    EPSClipSwitcher,
    EPSModelSwitcher,
    EPSSwitcher,
    EPSVaeSwitcher,
)
from eps_image.routes_state_registry import collect_state_registry
from lora_library.nodes_notebook import LoraLibraryNotebook
from lora_library.nodes_picker import EPSLoraPicker
from lora_library.nodes_prompt_builder import EPSPromptBuilder
from lora_library.nodes_sets import LoraLibraryApplySet
from lora_library.nodes_sweep import LoraLibrarySweep


@pytest.fixture(autouse=True)
def _reset_state_registry_cache():
    """The route's response is memoized at MODULE scope, process-lifetime
    by design (mirrors `routes_list_flags.py`'s own cache) -- reset it
    around every test in this file so one test's fake `nodes` module can
    never leak its cached answer into the next."""
    routes_state_registry._reset_cache()
    yield
    routes_state_registry._reset_cache()


#: One row per `__init__.py` `_NODE_SPECS` entry -- (class_id, class,
#: display). Every node this pack ships now carries an `EPS_STATE_WIDGETS`
#: descriptor (§6.16 M0, even if it's "everything excluded" for Image Grid/
#: Frame Saver), so this list doubles as "every class the registry must
#: serve".
_STATE_BEARING_SPECS: list[tuple[str, type, str]] = [
    ("LoraLibraryNotebook", LoraLibraryNotebook, "EPS Prompt Notebook"),
    ("EPSPromptBuilder", EPSPromptBuilder, "EPS Prompt Builder"),
    ("LoraLibraryApplySet", LoraLibraryApplySet, "EPS Apply LoRA Set"),
    ("LoraLibrarySweep", LoraLibrarySweep, "EPS LoRA Iterator"),
    ("EPSLoraPicker", EPSLoraPicker, "EPS LoRA Picker"),
    ("EPSSwitcher", EPSSwitcher, "EPS Image Switcher"),
    ("EPSModelSwitcher", EPSModelSwitcher, "EPS Model Switcher"),
    ("EPSClipSwitcher", EPSClipSwitcher, "EPS CLIP Switcher"),
    ("EPSVaeSwitcher", EPSVaeSwitcher, "EPS VAE Switcher"),
    ("EPSResolution", EPSResolution, "EPS Resolution"),
    ("EPSImageGrid", EPSImageGrid, "EPS Image Grid"),
    ("EPSFrameSaver", EPSFrameSaver, "EPS Frame Saver"),
    ("EPSCrossSweep", EPSCrossSweep, "EPS Run Multiplier"),
    ("EPSSaveImage", EPSSaveImage, "EPS Save Image"),
    ("EPSDistributor", EPSDistributor, "EPS Distributor"),
    ("EPSCheckpointSwitcher", EPSCheckpointSwitcher, "EPS Checkpoint Switcher"),
]

#: The COMPLETE closed set of widget kinds (module docstring / §6.16) -- the
#: frontend implements exactly one generic validator per kind, so a
#: descriptor naming anything outside this set is a bug, not a new kind.
_CLOSED_KINDS = {"string", "int", "float", "choice", "lines", "json_array", "json_object"}

#: Mirrors `nodes_save_image._iter_widgets`'s exact kind test: a COMBO
#: (Python list) or one of these four ComfyUI primitive types is a widget;
#: anything else (MODEL/CLIP/IMAGE/VAE/LORA_STACK/VIDEO/"*") is a socket.
_WIDGET_KINDS = ("STRING", "INT", "FLOAT", "BOOLEAN")


def _widget_bearing_inputs(node_class: type) -> set[str]:
    """Every widget-bearing (non-forceInput) input name in *node_class*'s
    INPUT_TYPES, `required` + `optional` only (the `hidden` section carries
    no real widgets, only PROMPT/UNIQUE_ID/EXTRA_PNGINFO plumbing) -- the
    completeness check's universe of "things the registry must account for
    one way or another"."""
    spec = node_class.INPUT_TYPES()
    names: set[str] = set()
    for section in ("required", "optional"):
        for name, definition in spec.get(section, {}).items():
            kind = definition[0]
            options = (
                definition[1] if len(definition) > 1 and isinstance(definition[1], dict) else {}
            )
            if options.get("forceInput"):
                continue
            if isinstance(kind, list) or kind in _WIDGET_KINDS:
                names.add(name)
    return names


def _all_input_names(node_class: type) -> set[str]:
    """Every input name (any kind, any section) in *node_class*'s
    INPUT_TYPES -- `required` + `optional` only."""
    spec = node_class.INPUT_TYPES()
    names: set[str] = set()
    for section in ("required", "optional"):
        names.update(spec.get(section, {}).keys())
    return names


# ---------------------------------------------------------------- schema


class TestSchema:
    """Every real node's descriptor is pure JSON and matches the §6.16
    schema: kinds from the closed set, key_patterns that compile as regex,
    json_array declaring string items, numeric min/max when present."""

    @pytest.mark.parametrize("class_id,node_class,_display", _STATE_BEARING_SPECS)
    def test_descriptor_is_json_serializable(self, class_id, node_class, _display) -> None:
        json.dumps(node_class.EPS_STATE_WIDGETS)  # raises on a function/set/etc.

    @pytest.mark.parametrize("class_id,node_class,_display", _STATE_BEARING_SPECS)
    def test_top_level_shape(self, class_id, node_class, _display) -> None:
        descriptor = node_class.EPS_STATE_WIDGETS
        assert descriptor["format"] == 1
        assert isinstance(descriptor["widgets"], dict)
        assert set(descriptor) <= {"format", "widgets", "excluded"}
        if "excluded" in descriptor:
            assert isinstance(descriptor["excluded"], dict)
            for reason in descriptor["excluded"].values():
                assert isinstance(reason, str) and reason.strip()

    @pytest.mark.parametrize("class_id,node_class,_display", _STATE_BEARING_SPECS)
    def test_widget_kinds_are_in_the_closed_set(self, class_id, node_class, _display) -> None:
        for name, widget_spec in node_class.EPS_STATE_WIDGETS["widgets"].items():
            assert widget_spec["kind"] in _CLOSED_KINDS, f"{class_id}.{name}: {widget_spec!r}"

    @pytest.mark.parametrize("class_id,node_class,_display", _STATE_BEARING_SPECS)
    def test_json_array_declares_string_items(self, class_id, node_class, _display) -> None:
        for name, widget_spec in node_class.EPS_STATE_WIDGETS["widgets"].items():
            if widget_spec["kind"] == "json_array":
                assert widget_spec.get("items") == "string", f"{class_id}.{name}"

    @pytest.mark.parametrize("class_id,node_class,_display", _STATE_BEARING_SPECS)
    def test_key_patterns_compile_as_regex(self, class_id, node_class, _display) -> None:
        for _name, widget_spec in node_class.EPS_STATE_WIDGETS["widgets"].items():
            pattern = widget_spec.get("key_pattern")
            if pattern is not None:
                re.compile(pattern)  # raises re.error on a malformed pattern

    @pytest.mark.parametrize("class_id,node_class,_display", _STATE_BEARING_SPECS)
    def test_min_max_are_numbers_when_present(self, class_id, node_class, _display) -> None:
        for name, widget_spec in node_class.EPS_STATE_WIDGETS["widgets"].items():
            for bound in ("min", "max"):
                if bound in widget_spec:
                    value = widget_spec[bound]
                    assert isinstance(value, (int, float)), f"{class_id}.{name}.{bound}"


# ------------------------------------------------------------ completeness


class TestCompleteness:
    """THE point of the registry: every declared widget name is real, and
    every real widget-bearing input is accounted for -- declared or
    excluded, never silently missing."""

    @pytest.mark.parametrize("class_id,node_class,_display", _STATE_BEARING_SPECS)
    def test_every_declared_widget_exists_in_input_types(
        self, class_id, node_class, _display
    ) -> None:
        declared = set(node_class.EPS_STATE_WIDGETS["widgets"])
        missing = declared - _all_input_names(node_class)
        assert not missing, f"{class_id} declares {missing}, not present in INPUT_TYPES"

    @pytest.mark.parametrize("class_id,node_class,_display", _STATE_BEARING_SPECS)
    def test_every_widget_bearing_input_is_declared_or_excluded(
        self, class_id, node_class, _display
    ) -> None:
        descriptor = node_class.EPS_STATE_WIDGETS
        declared = set(descriptor["widgets"])
        excluded = set(descriptor.get("excluded", {}))
        overlap = declared & excluded
        assert not overlap, f"{class_id}: {overlap} is both declared and excluded"

        widget_bearing = _widget_bearing_inputs(node_class)
        unaccounted = widget_bearing - declared - excluded
        assert not unaccounted, f"{class_id}: {unaccounted} is neither declared nor excluded"


# --------------------------------------------------------- switcher family


class TestSwitcherPatterns:
    """Each of the four switcher classes carries its OWN toggles
    key_pattern -- one shared factory (`_make_switcher_ns`) stamps a
    different pattern per class, never one pattern reused by mistake."""

    @pytest.mark.parametrize(
        "node_class,expected_pattern",
        [
            (EPSSwitcher, r"^image_\d+$"),
            (EPSModelSwitcher, r"^model_\d+$"),
            (EPSClipSwitcher, r"^clip_\d+$"),
            (EPSVaeSwitcher, r"^vae_\d+$"),
        ],
    )
    def test_each_switcher_carries_its_own_toggles_pattern(
        self, node_class, expected_pattern
    ) -> None:
        toggles = node_class.EPS_STATE_WIDGETS["widgets"]["toggles"]
        assert toggles["kind"] == "json_object"
        assert toggles["key_pattern"] == expected_pattern

    def test_the_four_patterns_are_all_distinct(self) -> None:
        patterns = {
            cls.EPS_STATE_WIDGETS["widgets"]["toggles"]["key_pattern"]
            for cls in (EPSSwitcher, EPSModelSwitcher, EPSClipSwitcher, EPSVaeSwitcher)
        }
        assert len(patterns) == 4


# -------------------------------------------------- out-of-scope nodes


class TestOutOfScopeNodes:
    """EPSImageGrid/EPSFrameSaver are out of the universal-state scope by
    owner decision (2026-08-26): every real widget is excluded with a
    reason, none is declared."""

    @pytest.mark.parametrize("node_class", [EPSImageGrid, EPSFrameSaver])
    def test_widgets_is_empty_and_excluded_is_not(self, node_class) -> None:
        descriptor = node_class.EPS_STATE_WIDGETS
        assert descriptor["widgets"] == {}
        assert descriptor["excluded"]
        for reason in descriptor["excluded"].values():
            assert reason.strip()

    def test_image_grid_excludes_exactly_its_three_real_widgets(self) -> None:
        assert set(EPSImageGrid.EPS_STATE_WIDGETS["excluded"]) == {"mode", "focus", "grid_uuid"}

    def test_frame_saver_excludes_exactly_its_two_real_widgets(self) -> None:
        assert set(EPSFrameSaver.EPS_STATE_WIDGETS["excluded"]) == {"video_path", "frame"}


# ---------------------------------------------------- collect_state_registry


class _NoState:
    """No EPS_STATE_WIDGETS attribute at all -- the ordinary case for the
    other ~250 non-state-bearing third-party/core classes a real
    NODE_CLASS_MAPPINGS holds."""


class _WithState:
    EPS_STATE_WIDGETS: ClassVar[dict[str, Any]] = {
        "format": 1,
        "widgets": {"foo": {"kind": "string"}},
    }


class _NotADict:
    EPS_STATE_WIDGETS: ClassVar[str] = "not-a-dict"


class _NonJSON:
    EPS_STATE_WIDGETS: ClassVar[dict[str, Any]] = {
        "format": 1,
        "widgets": {"foo": {"kind": "string", "note": lambda: None}},
    }


class _RaisesOnType(type):
    def __getattribute__(cls, name):
        if name == "EPS_STATE_WIDGETS":
            raise RuntimeError("boom")
        return super().__getattribute__(name)


class _Broken(metaclass=_RaisesOnType):
    EPS_STATE_WIDGETS: ClassVar[dict[str, Any]] = {"format": 1, "widgets": {}}


class TestCollect:
    def test_class_without_the_attribute_is_skipped(self) -> None:
        assert collect_state_registry({"NoState": _NoState}) == {}

    def test_class_with_the_attribute_is_included(self) -> None:
        registry = collect_state_registry({"WithState": _WithState})
        assert registry["WithState"]["format"] == 1
        assert registry["WithState"]["widgets"] == {"foo": {"kind": "string"}}

    def test_display_name_falls_back_to_class_id(self) -> None:
        registry = collect_state_registry({"WithState": _WithState})
        assert registry["WithState"]["display"] == "WithState"

    def test_display_name_uses_the_mapping_when_present(self) -> None:
        registry = collect_state_registry(
            {"WithState": _WithState}, {"WithState": "Pretty Name"}
        )
        assert registry["WithState"]["display"] == "Pretty Name"

    def test_a_broken_attribute_access_is_skipped_not_fatal(self) -> None:
        registry = collect_state_registry({"Broken": _Broken, "WithState": _WithState})
        assert "Broken" not in registry
        assert "WithState" in registry

    def test_a_non_dict_descriptor_is_skipped(self) -> None:
        assert collect_state_registry({"NotADict": _NotADict}) == {}

    def test_a_non_json_serializable_descriptor_is_skipped(self) -> None:
        assert collect_state_registry({"NonJSON": _NonJSON}) == {}

    def test_returned_descriptor_is_a_copy_not_an_alias(self) -> None:
        registry = collect_state_registry({"WithState": _WithState})
        registry["WithState"]["widgets"]["foo"]["kind"] = "mutated"
        assert _WithState.EPS_STATE_WIDGETS["widgets"]["foo"]["kind"] == "string"

    def test_non_mapping_degrades_to_empty(self) -> None:
        assert collect_state_registry(None) == {}
        assert collect_state_registry(42) == {}


# --------------------------------------------------------------- the route


@pytest.fixture
def fake_nodes(monkeypatch: pytest.MonkeyPatch):
    module = types.ModuleType("nodes")
    module.NODE_CLASS_MAPPINGS = {
        class_id: cls for class_id, cls, _display in _STATE_BEARING_SPECS
    }
    module.NODE_DISPLAY_NAME_MAPPINGS = {
        class_id: display for class_id, _cls, display in _STATE_BEARING_SPECS
    }
    monkeypatch.setitem(sys.modules, "nodes", module)
    return module


@pytest.fixture
async def client(fake_nodes, aiohttp_client):
    app = web.Application()
    app.add_routes(routes_state_registry.build_routes())
    return await aiohttp_client(app)


class TestRoute:
    async def test_serves_every_state_bearing_class(self, client) -> None:
        response = await client.get(routes_state_registry.ROUTE)
        assert response.status == 200
        body = await response.json()
        assert body["format"] == 1
        assert set(body["classes"]) == {class_id for class_id, _c, _d in _STATE_BEARING_SPECS}

    async def test_display_names_come_from_the_mapping(self, client) -> None:
        body = await (await client.get(routes_state_registry.ROUTE)).json()
        entry = body["classes"]["EPSCheckpointSwitcher"]
        assert entry["display"] == "EPS Checkpoint Switcher"

    async def test_a_class_entry_carries_its_own_widgets_and_excluded(self, client) -> None:
        body = await (await client.get(routes_state_registry.ROUTE)).json()
        notebook = body["classes"]["LoraLibraryNotebook"]
        assert notebook["widgets"]["entry"] == {"kind": "lines"}
        assert notebook["excluded"] == {
            "pinned": "provenance from a baked image, not user intent",
            # v0.86.0: an audition buffer is mid-edit scratch text -- a
            # Universal State must never capture or replay it.
            "drafts": "unsaved mid-edit scratch text, not user-chosen state",
        }

    async def test_out_of_scope_classes_serve_empty_widgets(self, client) -> None:
        body = await (await client.get(routes_state_registry.ROUTE)).json()
        assert body["classes"]["EPSImageGrid"]["widgets"] == {}
        assert body["classes"]["EPSFrameSaver"]["widgets"] == {}

    async def test_route_constant_matches(self) -> None:
        assert routes_state_registry.ROUTE == "/eps/state_registry"


# --------------------------------------------------- process-lifetime cache
# (mirrors routes_list_flags.py's own TestCaching/TestOffLoop exactly.)


class TestCaching:
    async def test_second_call_reuses_the_same_cached_object_no_second_walk(
        self, fake_nodes, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[object] = []
        original = routes_state_registry.collect_state_registry

        def counting(mappings, display_names=None):
            calls.append(mappings)
            return original(mappings, display_names)

        monkeypatch.setattr(routes_state_registry, "collect_state_registry", counting)

        first_body = await (await client.get(routes_state_registry.ROUTE)).json()
        cached_after_first = routes_state_registry._cached_response
        assert cached_after_first is not None

        second_body = await (await client.get(routes_state_registry.ROUTE)).json()
        cached_after_second = routes_state_registry._cached_response

        assert first_body == second_body
        assert cached_after_first is cached_after_second  # SAME object, not just equal
        assert len(calls) == 1  # collect_state_registry walked exactly once

    async def test_a_class_added_after_the_first_call_is_not_picked_up(
        self, fake_nodes, client
    ) -> None:
        first = await (await client.get(routes_state_registry.ROUTE)).json()
        assert "NewOne" not in first["classes"]

        fake_nodes.NODE_CLASS_MAPPINGS["NewOne"] = _WithState

        second = await (await client.get(routes_state_registry.ROUTE)).json()
        assert "NewOne" not in second["classes"]  # still the stale cached answer

    async def test_reset_cache_forces_a_fresh_walk(self, fake_nodes, client) -> None:
        await client.get(routes_state_registry.ROUTE)
        fake_nodes.NODE_CLASS_MAPPINGS["NewOne"] = _WithState
        routes_state_registry._reset_cache()

        after_reset = await (await client.get(routes_state_registry.ROUTE)).json()
        assert "NewOne" in after_reset["classes"]

    async def test_a_failed_walk_is_never_cached(
        self, fake_nodes, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original = routes_state_registry.collect_state_registry

        def boom(mappings, display_names=None):
            raise RuntimeError("transient failure")

        monkeypatch.setattr(routes_state_registry, "collect_state_registry", boom)
        failed = await client.get(routes_state_registry.ROUTE)
        assert failed.status == 500
        assert routes_state_registry._cached_response is None

        # Restore just this one attribute -- NOT monkeypatch.undo(), which
        # would also revert the `fake_nodes` fixture's own sys.modules patch.
        monkeypatch.setattr(routes_state_registry, "collect_state_registry", original)
        recovered = await (await client.get(routes_state_registry.ROUTE)).json()
        assert "LoraLibraryNotebook" in recovered["classes"]


class TestOffLoop:
    async def test_the_uncached_walk_runs_off_the_event_loop_thread(
        self, fake_nodes, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loop_thread = threading.get_ident()
        recorded: list[int] = []
        original = routes_state_registry.collect_state_registry

        def recording(mappings, display_names=None):
            recorded.append(threading.get_ident())
            return original(mappings, display_names)

        monkeypatch.setattr(routes_state_registry, "collect_state_registry", recording)

        response = await client.get(routes_state_registry.ROUTE)

        assert response.status == 200
        assert recorded and all(t != loop_thread for t in recorded)

    async def test_a_cache_hit_skips_the_walk_entirely(
        self, fake_nodes, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await client.get(routes_state_registry.ROUTE)  # primes the cache

        calls: list[object] = []
        monkeypatch.setattr(
            routes_state_registry,
            "collect_state_registry",
            lambda mappings, display_names=None: calls.append(mappings),
        )

        response = await client.get(routes_state_registry.ROUTE)
        assert response.status == 200
        assert calls == []  # never called -- the cached response answered it
