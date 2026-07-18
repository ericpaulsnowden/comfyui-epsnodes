"""Tests for lora_library.nodes_notebook (FORMAT.md §6.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lora_library import nodes_notebook
from lora_library.context import LibraryContext


@pytest.fixture(autouse=True)
def _wire_context(context: LibraryContext):
    nodes_notebook.set_context(context)
    yield
    nodes_notebook.set_context(None)


def _write_notebook(library_dir: Path, filename: str, text: str) -> None:
    path = library_dir / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ----------------------------------------------------------------- read_entry


class TestReadEntry:
    def test_reads_the_entry_text(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## Portrait\nSome prompt text.\n")
        node = nodes_notebook.LoraLibraryNotebook()
        result = node.read_entry(file="loras.md", entry="Portrait")
        assert result == (["Some prompt text."], ["Portrait"])

    def test_returns_a_two_tuple_of_one_element_lists_for_a_single_selection(
        self, library_dir: Path
    ) -> None:
        # FORMAT.md §6.1: a single-line `entry` is the degenerate one-line
        # case — same shape as multi-select, just length-1 lists.
        _write_notebook(library_dir, "loras.md", "## E\nx\n")
        node = nodes_notebook.LoraLibraryNotebook()
        result = node.read_entry(file="loras.md", entry="E")
        assert isinstance(result, tuple)
        assert len(result) == 2
        texts, names = result
        assert isinstance(texts, list) and isinstance(names, list)
        assert len(texts) == len(names) == 1

    def test_missing_file_raises_naming_the_file(self) -> None:
        node = nodes_notebook.LoraLibraryNotebook()
        with pytest.raises(ValueError, match=r"loras\.md"):
            node.read_entry(file="loras.md", entry="Anything")

    def test_missing_entry_raises_naming_the_entry(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## Real\nbody\n")
        node = nodes_notebook.LoraLibraryNotebook()
        with pytest.raises(ValueError, match="Ghost"):
            node.read_entry(file="loras.md", entry="Ghost")

    def test_no_context_configured_raises_runtime_error(self) -> None:
        nodes_notebook.set_context(None)
        node = nodes_notebook.LoraLibraryNotebook()
        with pytest.raises(RuntimeError):
            node.read_entry(file="loras.md", entry="Anything")

    def test_reads_entry_inside_a_category(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "# Style\n\n## Cinematic\nfilm grain\n")
        node = nodes_notebook.LoraLibraryNotebook()
        assert node.read_entry(file="loras.md", entry="Cinematic") == (
            ["film grain"],
            ["Cinematic"],
        )

    def test_reads_from_a_non_default_file(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "other.md", "## E\nother file body\n")
        node = nodes_notebook.LoraLibraryNotebook()
        assert node.read_entry(file="other.md", entry="E") == (["other file body"], ["E"])

    def test_unicode_entry_name_and_text(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## Café ☕\n日本語のテキスト\n")
        node = nodes_notebook.LoraLibraryNotebook()
        assert node.read_entry(file="loras.md", entry="Café ☕") == (
            ["日本語のテキスト"],
            ["Café ☕"],
        )

    def test_interior_blank_lines_are_kept_in_the_output(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## E\nLine1\n\nLine2\n")
        node = nodes_notebook.LoraLibraryNotebook()
        assert node.read_entry(file="loras.md", entry="E") == (["Line1\n\nLine2"], ["E"])

    def test_first_occurrence_wins_when_the_file_has_duplicate_names(
        self, library_dir: Path
    ) -> None:
        _write_notebook(library_dir, "loras.md", "## Foo\nfirst\n## Foo\nsecond\n")
        node = nodes_notebook.LoraLibraryNotebook()
        assert node.read_entry(file="loras.md", entry="Foo") == (["first"], ["Foo"])

    def test_re_reads_the_file_on_every_call_rather_than_caching(
        self, library_dir: Path
    ) -> None:
        _write_notebook(library_dir, "loras.md", "## E\noriginal\n")
        node = nodes_notebook.LoraLibraryNotebook()
        assert node.read_entry(file="loras.md", entry="E") == (["original"], ["E"])
        _write_notebook(library_dir, "loras.md", "## E\nedited on the other machine\n")
        assert node.read_entry(file="loras.md", entry="E") == (
            ["edited on the other machine"],
            ["E"],
        )


# --------------------------------------------------------------- multi-select


class TestMultiSelect:
    def test_selection_order_is_preserved_not_file_order(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nbodyA\n## B\nbodyB\n## C\nbodyC\n")
        node = nodes_notebook.LoraLibraryNotebook()
        texts, names = node.read_entry(file="loras.md", entry="B\nA\nC")
        assert names == ["B", "A", "C"]
        assert texts == ["bodyB", "bodyA", "bodyC"]

    def test_texts_and_names_stay_paired_across_categories(self, library_dir: Path) -> None:
        _write_notebook(
            library_dir, "loras.md", "# Cat A\n## A\nbodyA\n# Cat B\n## B\nbodyB\n"
        )
        node = nodes_notebook.LoraLibraryNotebook()
        texts, names = node.read_entry(file="loras.md", entry="B\nA")
        assert list(zip(names, texts, strict=True)) == [("B", "bodyB"), ("A", "bodyA")]

    def test_blank_and_whitespace_only_lines_are_skipped(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nbodyA\n## B\nbodyB\n")
        node = nodes_notebook.LoraLibraryNotebook()
        texts, names = node.read_entry(file="loras.md", entry="\nA\n\n   \nB\n")
        assert names == ["A", "B"]
        assert texts == ["bodyA", "bodyB"]

    def test_empty_selection_raises_naming_the_file(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nbodyA\n")
        node = nodes_notebook.LoraLibraryNotebook()
        with pytest.raises(ValueError, match=r"loras\.md"):
            node.read_entry(file="loras.md", entry="")

    def test_whitespace_only_selection_raises(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nbodyA\n")
        node = nodes_notebook.LoraLibraryNotebook()
        with pytest.raises(ValueError):
            node.read_entry(file="loras.md", entry="\n   \n\n")

    def test_one_missing_entry_among_several_raises_naming_it(
        self, library_dir: Path
    ) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nbodyA\n## B\nbodyB\n")
        node = nodes_notebook.LoraLibraryNotebook()
        with pytest.raises(ValueError, match="Ghost"):
            node.read_entry(file="loras.md", entry="A\nGhost\nB")

    def test_every_missing_entry_is_named_in_the_error(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nbodyA\n")
        node = nodes_notebook.LoraLibraryNotebook()
        with pytest.raises(ValueError) as exc_info:
            node.read_entry(file="loras.md", entry="Ghost1\nGhost2")
        message = str(exc_info.value)
        assert "Ghost1" in message
        assert "Ghost2" in message


# --------------------------------------------------------------------- IS_CHANGED


class TestIsChanged:
    def test_changes_when_the_file_is_rewritten(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## E\nv1\n")
        token1 = nodes_notebook.LoraLibraryNotebook.IS_CHANGED(file="loras.md", entry="E")
        _write_notebook(library_dir, "loras.md", "## E\nv2 with different length\n")
        token2 = nodes_notebook.LoraLibraryNotebook.IS_CHANGED(file="loras.md", entry="E")
        assert token1 != token2

    def test_changes_when_the_entry_widget_value_changes(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nx\n## B\ny\n")
        token_a = nodes_notebook.LoraLibraryNotebook.IS_CHANGED(file="loras.md", entry="A")
        token_b = nodes_notebook.LoraLibraryNotebook.IS_CHANGED(file="loras.md", entry="B")
        assert token_a != token_b

    def test_stable_across_calls_when_nothing_changed(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## E\nsame\n")
        token1 = nodes_notebook.LoraLibraryNotebook.IS_CHANGED(file="loras.md", entry="E")
        token2 = nodes_notebook.LoraLibraryNotebook.IS_CHANGED(file="loras.md", entry="E")
        assert token1 == token2

    def test_uses_a_missing_token_for_a_nonexistent_file(self) -> None:
        token = nodes_notebook.LoraLibraryNotebook.IS_CHANGED(file="loras.md", entry="E")
        assert isinstance(token, str)
        assert "missing" in token

    def test_missing_then_created_file_changes_the_token(self, library_dir: Path) -> None:
        token_before = nodes_notebook.LoraLibraryNotebook.IS_CHANGED(file="loras.md", entry="E")
        _write_notebook(library_dir, "loras.md", "## E\nnow it exists\n")
        token_after = nodes_notebook.LoraLibraryNotebook.IS_CHANGED(file="loras.md", entry="E")
        assert token_before != token_after

    def test_handles_no_context_without_raising(self) -> None:
        nodes_notebook.set_context(None)
        token = nodes_notebook.LoraLibraryNotebook.IS_CHANGED(file="loras.md", entry="E")
        assert isinstance(token, str)


# ------------------------------------------------------- VALIDATE_INPUTS / INPUT_TYPES


class TestValidateAndInputTypes:
    def test_validate_inputs_always_true(self) -> None:
        assert nodes_notebook.LoraLibraryNotebook.VALIDATE_INPUTS(entry="not-yet-created") is True

    def test_input_types_file_widget_default(self) -> None:
        input_types = nodes_notebook.LoraLibraryNotebook.INPUT_TYPES()
        widget_type, spec = input_types["required"]["file"]
        assert widget_type == "STRING"
        assert spec["default"] == "loras.md"

    def test_input_types_entry_widget_default(self) -> None:
        input_types = nodes_notebook.LoraLibraryNotebook.INPUT_TYPES()
        widget_type, spec = input_types["required"]["entry"]
        assert widget_type == "STRING"
        assert spec["default"] == ""

    def test_class_shape_matches_format_md_section_6_1(self) -> None:
        cls = nodes_notebook.LoraLibraryNotebook
        assert cls.CATEGORY == "EPSNodes"
        assert cls.RETURN_TYPES == ("STRING", "STRING")
        assert cls.RETURN_NAMES == ("text", "name")
        assert cls.OUTPUT_IS_LIST == (True, True)
        assert cls.FUNCTION == "read_entry"


# --------------------------------------------------------------- no ComfyUI import


def test_module_never_imports_comfy_or_folder_paths() -> None:
    import sys

    assert "comfy" not in nodes_notebook.__dict__
    assert "folder_paths" not in nodes_notebook.__dict__
    # And the module itself never names them at all, not even lazily inside
    # a method (unlike nodes_sets.py, this node has no reason to).
    import inspect

    source = inspect.getsource(sys.modules[nodes_notebook.__name__])
    assert "import comfy" not in source
    assert "import folder_paths" not in source
