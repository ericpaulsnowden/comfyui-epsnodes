"""Tests for eps_image.nodes_checkpoint_switcher (`EPSCheckpointSwitcher`).

No ComfyUI/torch anywhere -- "checkpoints" are plain sentinel tuples handed
back by a monkeypatched `_load_checkpoint` (the node's own load seam); the
node never inspects a loaded model/clip/vae's contents, only the FILENAME
used to select it. Mirrors tests/test_switcher.py's conventions for its
`fake_execution_blocker` fixture (this pack's tests never require a real
ComfyUI install on the path).
"""

from __future__ import annotations

import inspect
import json
import logging
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from eps_image import nodes_checkpoint_switcher
from eps_image.nodes_checkpoint_switcher import (
    EPSCheckpointSwitcher,
    _parse_selection,
    _stem,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def fake_execution_blocker(monkeypatch: pytest.MonkeyPatch):
    """Installs a fake `comfy_execution.graph` module exposing
    `ExecutionBlocker` into `sys.modules` -- the same convention as
    `tests/test_switcher.py`'s fixture of the same name.
    `EPSCheckpointSwitcher.execute`'s all-blocked path imports
    `ExecutionBlocker` lazily from exactly this module path, so installing
    the fake here is the whole story -- nothing in
    nodes_checkpoint_switcher itself needs patching. Returns the fake class
    so tests can `isinstance()` the returned blockers.
    """

    class FakeExecutionBlocker:
        """Mirrors the real `comfy_execution.graph_utils.ExecutionBlocker`
        (`__init__(self, message)` storing `self.message`) exactly enough
        for these tests.
        """

        def __init__(self, message: object) -> None:
            self.message = message

    fake_graph = types.ModuleType("comfy_execution.graph")
    fake_graph.ExecutionBlocker = FakeExecutionBlocker
    fake_pkg = types.ModuleType("comfy_execution")
    fake_pkg.graph = fake_graph

    monkeypatch.setitem(sys.modules, "comfy_execution", fake_pkg)
    monkeypatch.setitem(sys.modules, "comfy_execution.graph", fake_graph)
    return FakeExecutionBlocker


@pytest.fixture
def fake_folder_paths(monkeypatch: pytest.MonkeyPatch):
    """Installs a fake `folder_paths` module exposing a fixed
    `get_filename_list("checkpoints")` result -- mirrors how
    `tests/test_routes_image_grid.py` fakes `folder_paths` for its own
    route tests. Returns the fixed name list so a test can build a
    `selection` against known-good entries.
    """
    names = ["a.safetensors", "styles/b.safetensors"]
    fake_module = types.ModuleType("folder_paths")
    fake_module.get_filename_list = lambda folder: list(names) if folder == "checkpoints" else []
    monkeypatch.setitem(sys.modules, "folder_paths", fake_module)
    return names


def _stub_loader(monkeypatch: pytest.MonkeyPatch, table: dict[str, tuple[Any, Any, Any]]) -> None:
    """Monkeypatches `nodes_checkpoint_switcher._load_checkpoint` (the
    node's own load seam -- module docstring "This is the ONE seam
    execute() calls through") to return *table*'s entry for a known name,
    or raise `FileNotFoundError` -- exactly `_load_checkpoint`'s own
    documented contract -- for anything else. `execute` calls the
    module-level name directly (not through `self`), so patching it on the
    module is enough to intercept every call inside `execute`.
    """

    def _fake(name: str) -> tuple[Any, Any, Any]:
        if name in table:
            return table[name]
        raise FileNotFoundError(f"no such checkpoint on disk: {name}")

    monkeypatch.setattr(nodes_checkpoint_switcher, "_load_checkpoint", _fake)


# ------------------------------------------------------------- _parse_selection


class TestParseSelection:
    def test_valid_array_of_strings_preserves_order(self) -> None:
        assert _parse_selection(json.dumps(["b.safetensors", "a.safetensors"])) == [
            "b.safetensors",
            "a.safetensors",
        ]

    def test_empty_array_is_empty(self) -> None:
        assert _parse_selection("[]") == []

    def test_empty_string_is_empty(self) -> None:
        assert _parse_selection("") == []

    def test_none_is_empty(self) -> None:
        assert _parse_selection(None) == []

    def test_malformed_json_falls_back_to_empty(self) -> None:
        assert _parse_selection("not json{{") == []

    def test_malformed_json_logs_a_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="eps_image"):
            _parse_selection("not json{{")
        assert any("malformed" in record.message for record in caplog.records)

    @pytest.mark.parametrize("non_array", ['{"a": 1}', "42", '"just a string"', "true"])
    def test_non_array_json_falls_back_to_empty(self, non_array: str) -> None:
        assert _parse_selection(non_array) == []

    def test_non_array_json_logs_a_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="eps_image"):
            _parse_selection('{"not": "an array"}')
        assert any("not a JSON array" in record.message for record in caplog.records)

    def test_mixed_junk_keeps_only_string_entries_in_order(self) -> None:
        selection = json.dumps(["a.safetensors", 123, None, {"nested": True}, "b.safetensors", []])
        assert _parse_selection(selection) == ["a.safetensors", "b.safetensors"]

    def test_mixed_junk_logs_a_warning_per_bad_entry(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        selection = json.dumps(["a.safetensors", 123, None])
        with caplog.at_level(logging.WARNING, logger="eps_image"):
            names = _parse_selection(selection)
        assert names == ["a.safetensors"]
        not_string_warnings = [r for r in caplog.records if "is not a string" in r.message]
        assert len(not_string_warnings) == 2  # 123 and None, each individually skipped

    def test_all_junk_array_is_empty_not_an_error(self) -> None:
        assert _parse_selection(json.dumps([1, 2, None, {}])) == []


# ------------------------------------------------------------------------ _stem


class TestStem:
    def test_subfolder_and_extension_are_stripped(self) -> None:
        assert _stem("subfolder/name.safetensors") == "name"

    def test_plain_name_and_extension(self) -> None:
        assert _stem("name.safetensors") == "name"

    def test_no_extension_returns_whole_basename(self) -> None:
        assert _stem("name") == "name"

    def test_windows_backslash_separator_is_also_stripped(self) -> None:
        # folder_paths.get_filename_list uses the OS's NATIVE separator
        # (module docstring, precedent lora_library/sets_store.py) -- a
        # Windows-listed subfoldered checkpoint must stem the same as a
        # forward-slash one from macOS/Linux.
        assert _stem("styles\\sdxl.safetensors") == "sdxl"

    def test_only_the_last_extension_is_stripped(self) -> None:
        assert _stem("name.v2.safetensors") == "name.v2"

    def test_leading_dot_dotfile_is_not_collapsed_to_empty(self) -> None:
        assert _stem(".hidden") == ".hidden"

    def test_nested_subfolders(self) -> None:
        assert _stem("a/b/c/model.ckpt") == "model"


# --------------------------------------------------------------------- execute


class TestExecuteLoadsSelectedCheckpoints:
    def test_single_checkpoint_loads_with_stemmed_label(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sentinel = ("MODEL_A", "CLIP_A", "VAE_A")
        _stub_loader(monkeypatch, {"sub/name.safetensors": sentinel})
        node = EPSCheckpointSwitcher()
        result = node.execute(selection=json.dumps(["sub/name.safetensors"]))
        assert result == (["MODEL_A"], ["CLIP_A"], ["VAE_A"], ["name"])

    def test_multiple_checkpoints_are_index_aligned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        table = {
            "a.safetensors": ("MODEL_A", "CLIP_A", "VAE_A"),
            "b.safetensors": ("MODEL_B", "CLIP_B", "VAE_B"),
        }
        _stub_loader(monkeypatch, table)
        node = EPSCheckpointSwitcher()
        result = node.execute(selection=json.dumps(["a.safetensors", "b.safetensors"]))
        assert result == (
            ["MODEL_A", "MODEL_B"],
            ["CLIP_A", "CLIP_B"],
            ["VAE_A", "VAE_B"],
            ["a", "b"],
        )

    def test_ordering_follows_selection_order_not_alphabetical(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        table = {
            "a.safetensors": ("MODEL_A", "CLIP_A", "VAE_A"),
            "b.safetensors": ("MODEL_B", "CLIP_B", "VAE_B"),
            "c.safetensors": ("MODEL_C", "CLIP_C", "VAE_C"),
        }
        _stub_loader(monkeypatch, table)
        node = EPSCheckpointSwitcher()
        # Deliberately out of alphabetical order.
        selection = json.dumps(["c.safetensors", "a.safetensors", "b.safetensors"])
        result = node.execute(selection=selection)
        assert result[3] == ["c", "a", "b"]
        assert result[0] == ["MODEL_C", "MODEL_A", "MODEL_B"]


class TestExecuteMissingFileSkip:
    def test_missing_file_is_skipped_others_still_load(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        table = {
            "a.safetensors": ("MODEL_A", "CLIP_A", "VAE_A"),
            "b.safetensors": ("MODEL_B", "CLIP_B", "VAE_B"),
        }
        _stub_loader(monkeypatch, table)
        node = EPSCheckpointSwitcher()
        result = node.execute(
            selection=json.dumps(["a.safetensors", "missing.safetensors", "b.safetensors"])
        )
        assert result == (
            ["MODEL_A", "MODEL_B"],
            ["CLIP_A", "CLIP_B"],
            ["VAE_A", "VAE_B"],
            ["a", "b"],
        )

    def test_missing_file_logs_a_warning_naming_it(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        _stub_loader(monkeypatch, {"a.safetensors": ("M", "C", "V")})
        node = EPSCheckpointSwitcher()
        with caplog.at_level(logging.WARNING, logger="eps_image"):
            node.execute(selection=json.dumps(["a.safetensors", "missing.safetensors"]))
        messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("missing.safetensors" in m for m in messages)
        assert any("1 selected checkpoint" in m for m in messages)

    def test_all_missing_returns_blocker_on_every_output(
        self, monkeypatch: pytest.MonkeyPatch, fake_execution_blocker: type
    ) -> None:
        _stub_loader(monkeypatch, {})  # nothing resolves
        node = EPSCheckpointSwitcher()
        result = node.execute(selection=json.dumps(["gone1.safetensors", "gone2.safetensors"]))
        assert len(result) == 4
        for output in result:
            assert isinstance(output, list)
            assert len(output) == 1
            assert isinstance(output[0], fake_execution_blocker)
            assert output[0].message is None

    def test_all_missing_logs_warning_and_info(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_execution_blocker: type,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        _stub_loader(monkeypatch, {})
        node = EPSCheckpointSwitcher()
        with caplog.at_level(logging.INFO, logger="eps_image"):
            node.execute(selection=json.dumps(["gone1.safetensors", "gone2.safetensors"]))
        messages = [r.message for r in caplog.records]
        assert any("gone1.safetensors" in m and "gone2.safetensors" in m for m in messages)
        assert any("none could be loaded" in m for m in messages)


class TestExecuteAllOffOrNoneSelectedReturnsAnExecutionBlocker:
    """Same FORMAT.md §6.4 "all-off is a VALID state" convention as
    `EPSSwitcher`'s own all-off case, applied to "nothing ticked"."""

    def test_default_empty_selection_returns_a_one_element_blocker_per_output(
        self, fake_execution_blocker: type
    ) -> None:
        node = EPSCheckpointSwitcher()
        result = node.execute()
        assert len(result) == 4
        for output in result:
            assert isinstance(output, list)
            assert len(output) == 1
            blocker = output[0]
            assert isinstance(blocker, fake_execution_blocker)
            # message=None matters -- execution.py's execution_block_cb only
            # broadcasts an execution_error event when .message is not None.
            assert blocker.message is None

    def test_explicit_empty_array_selection_also_blocks(self, fake_execution_blocker: type) -> None:
        node = EPSCheckpointSwitcher()
        result = node.execute(selection="[]")
        assert all(isinstance(output[0], fake_execution_blocker) for output in result)

    def test_malformed_selection_degrades_to_the_same_blocker(
        self, fake_execution_blocker: type
    ) -> None:
        node = EPSCheckpointSwitcher()
        result = node.execute(selection="not json{{")
        assert all(isinstance(output[0], fake_execution_blocker) for output in result)

    def test_nothing_selected_logs_at_info_not_warning_or_error(
        self, fake_execution_blocker: type, caplog: pytest.LogCaptureFixture
    ) -> None:
        node = EPSCheckpointSwitcher()
        with caplog.at_level(logging.INFO, logger="eps_image"):
            node.execute(selection="[]")
        assert any("no checkpoints selected" in r.message for r in caplog.records)
        assert all(r.levelno <= logging.INFO for r in caplog.records)

    def test_does_not_raise(self, fake_execution_blocker: type) -> None:
        node = EPSCheckpointSwitcher()
        node.execute(selection="[]")  # must not raise

    def test_missing_fake_execution_blocker_module_surfaces_as_import_error(self) -> None:
        # Sanity check on the fixture's own premise: without a real or
        # faked comfy_execution.graph on the path, the lazy import inside
        # the blocker branch fails loudly, confirming the tests above
        # genuinely exercise it rather than passing by accident.
        if "comfy_execution" in sys.modules or "comfy_execution.graph" in sys.modules:
            pytest.skip("comfy_execution is already importable in this environment")
        node = EPSCheckpointSwitcher()
        with pytest.raises(ModuleNotFoundError):
            node.execute(selection="[]")


# -------------------------------------------------------------- VALIDATE_INPUTS


class TestValidateInputs:
    def test_empty_selection_is_true(self) -> None:
        assert EPSCheckpointSwitcher.VALIDATE_INPUTS(selection="[]") is True

    def test_omitted_selection_default_is_true(self) -> None:
        assert EPSCheckpointSwitcher.VALIDATE_INPUTS() is True

    def test_malformed_selection_is_true(self) -> None:
        # execute() degrades gracefully for this too -- a queue-time check
        # that can't even find a name to validate has nothing to reject.
        assert EPSCheckpointSwitcher.VALIDATE_INPUTS(selection="not json{{") is True

    def test_all_known_names_pass(self, fake_folder_paths: list[str]) -> None:
        selection = json.dumps(fake_folder_paths)
        assert EPSCheckpointSwitcher.VALIDATE_INPUTS(selection=selection) is True

    def test_unknown_name_returns_a_string_naming_it(self, fake_folder_paths: list[str]) -> None:
        result = EPSCheckpointSwitcher.VALIDATE_INPUTS(
            selection=json.dumps(["not-a-real-checkpoint.safetensors"])
        )
        assert isinstance(result, str)
        assert "not-a-real-checkpoint.safetensors" in result

    def test_mixed_known_and_unknown_names_only_the_unknown(
        self, fake_folder_paths: list[str]
    ) -> None:
        selection = json.dumps([fake_folder_paths[0], "bogus.safetensors"])
        result = EPSCheckpointSwitcher.VALIDATE_INPUTS(selection=selection)
        assert isinstance(result, str)
        assert "bogus.safetensors" in result
        assert fake_folder_paths[0] not in result

    def test_missing_folder_paths_module_degrades_to_true(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A None entry in sys.modules deterministically forces ImportError
        # for `import folder_paths`, regardless of whether this environment
        # happens to have a real one on the path elsewhere.
        monkeypatch.setitem(sys.modules, "folder_paths", None)
        result = EPSCheckpointSwitcher.VALIDATE_INPUTS(
            selection=json.dumps(["whatever.safetensors"])
        )
        assert result is True

    def test_broken_get_filename_list_degrades_to_true(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_module = types.ModuleType("folder_paths")

        def _boom(_folder: str) -> list[str]:
            raise RuntimeError("broken model directory")

        fake_module.get_filename_list = _boom
        monkeypatch.setitem(sys.modules, "folder_paths", fake_module)
        result = EPSCheckpointSwitcher.VALIDATE_INPUTS(
            selection=json.dumps(["whatever.safetensors"])
        )
        assert result is True


# --------------------------------------------------------- class shape / spec


class TestClassShape:
    def test_category(self) -> None:
        assert EPSCheckpointSwitcher.CATEGORY == "EPSNodes/Switchers"

    def test_return_types_and_names(self) -> None:
        assert EPSCheckpointSwitcher.RETURN_TYPES == ("MODEL", "CLIP", "VAE", "STRING")
        assert EPSCheckpointSwitcher.RETURN_NAMES == ("model", "clip", "vae", "label")

    def test_output_is_list_flagged_true_on_every_output(self) -> None:
        assert EPSCheckpointSwitcher.OUTPUT_IS_LIST == (True, True, True, True)

    def test_function_name_matches_the_declared_entry_point(self) -> None:
        assert EPSCheckpointSwitcher.FUNCTION == "execute"
        assert callable(getattr(EPSCheckpointSwitcher(), EPSCheckpointSwitcher.FUNCTION))

    def test_input_types_required_is_empty(self) -> None:
        spec = EPSCheckpointSwitcher.INPUT_TYPES()
        assert spec["required"] == {}
        assert "selection" not in spec["required"]

    def test_input_types_selection_is_in_optional_and_hidden(self) -> None:
        spec = EPSCheckpointSwitcher.INPUT_TYPES()
        widget_type, options = spec["optional"]["selection"]
        assert widget_type == "STRING"
        assert options["default"] == "[]"
        assert options["multiline"] is False
        assert options["hidden"] is True

    def test_execute_return_shape_is_a_four_tuple_of_lists(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_loader(monkeypatch, {"a.safetensors": ("M", "C", "V")})
        result = EPSCheckpointSwitcher().execute(selection=json.dumps(["a.safetensors"]))
        assert isinstance(result, tuple)
        assert len(result) == 4
        assert all(isinstance(output, list) for output in result)


class TestVueNodesHiddenFlag:
    """FORMAT.md §7.5: the `selection` widget must carry `"hidden": True`
    in its INPUT_TYPES options, or it leaks into Vue nodes as a raw
    editable text field -- the same pin `tests/test_vue_nodes_compat.py`
    applies to every other plumbing widget in this pack (`toggles`,
    `grid_uuid`, ...). Duplicated here (rather than added to that shared
    file) to keep this new node's own test file self-contained; the
    assertion is identical to that file's `test_plumbing_inputs_carry_the_
    vue_hidden_flag`.
    """

    def test_selection_carries_the_vue_hidden_flag(self) -> None:
        _widget_type, options = EPSCheckpointSwitcher.INPUT_TYPES()["optional"]["selection"]
        assert options.get("hidden") is True


# --------------------------------------------------------------- no ComfyUI import


class TestBareImport:
    def test_module_dict_has_no_comfy_torch_or_folder_paths_binding(self) -> None:
        # The real, load-bearing check: merely IMPORTING this module (as
        # every test above already has, at collection time) must not have
        # executed a module-scope `import comfy`/`import torch`/
        # `import folder_paths` -- if it had, the name would be bound in
        # the module's own namespace.
        assert "comfy" not in nodes_checkpoint_switcher.__dict__
        assert "torch" not in nodes_checkpoint_switcher.__dict__
        assert "folder_paths" not in nodes_checkpoint_switcher.__dict__

    def test_no_unindented_comfy_torch_or_folder_paths_import_line(self) -> None:
        # Belt-and-suspenders on the source text itself: any import of
        # comfy/torch/folder_paths must be INDENTED (i.e. inside a
        # function), never a column-0 module-scope statement. Deliberately
        # NOT a blanket substring search for "import comfy" anywhere in the
        # file (unlike some sibling test files) -- this module's lazy
        # `import comfy.sd` inside _load_checkpoint would false-positive on
        # that blunter check despite being correctly lazy.
        source = inspect.getsource(sys.modules[nodes_checkpoint_switcher.__name__])
        offenders = [
            line
            for line in source.splitlines()
            if line.startswith(("import comfy", "import torch", "import folder_paths"))
            or line.startswith(("from comfy", "from torch", "from folder_paths"))
        ]
        assert offenders == []


# --------------------------------------------------------------- registration


class TestRegisteredInThePack:
    """Cheap registration pin that needs no real ComfyUI import (the
    repo-root __init__.py isn't importable outside one -- see
    tests/conftest.py's `--confcutdir` comment): read its source text
    directly and confirm the exact _NODE_SPECS entry is present, the same
    way tests/test_vue_nodes_compat.py reads `web/eps_image.js` as text
    rather than executing it.
    """

    def test_node_spec_entry_present_with_correct_display_name(self) -> None:
        source = (REPO_ROOT / "__init__.py").read_text(encoding="utf-8")
        assert (
            '("eps_image.nodes_checkpoint_switcher", "EPSCheckpointSwitcher", '
            '"EPS Checkpoint Switcher")'
        ) in source

    def test_route_module_registered_defensively(self) -> None:
        source = (REPO_ROOT / "__init__.py").read_text(encoding="utf-8")
        assert "eps_image.routes_checkpoint_switcher" in source
        assert "_checkpoint_switcher_routes.register()" in source
