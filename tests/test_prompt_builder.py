"""Tests for lora_library.nodes_prompt_builder (FORMAT.md §6.15, ``EPSPromptBuilder``)."""

from __future__ import annotations

import inspect
import json
import logging
import sys
from pathlib import Path

import pytest

from lora_library import nodes_prompt_builder
from lora_library.context import LibraryContext
from lora_library.nodes_prompt_builder import EPSPromptBuilder


@pytest.fixture(autouse=True)
def _wire_context(context: LibraryContext):
    nodes_prompt_builder.set_context(context)
    yield
    nodes_prompt_builder.set_context(None)


def _write_notebook(library_dir: Path, filename: str, text: str) -> None:
    path = library_dir / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _blocks(*names: str) -> str:
    return json.dumps(list(names))


# --------------------------------------------------------------- class shape


class TestClassShape:
    def test_class_shape(self) -> None:
        assert EPSPromptBuilder.CATEGORY == "EPSNodes"
        assert EPSPromptBuilder.RETURN_TYPES == ("STRING", "STRING")
        assert EPSPromptBuilder.RETURN_NAMES == ("text", "name")
        assert EPSPromptBuilder.OUTPUT_IS_LIST == (True, True)
        assert EPSPromptBuilder.INPUT_IS_LIST is True
        assert EPSPromptBuilder.FUNCTION == "build"

    def test_input_types_order_and_defaults(self) -> None:
        spec = EPSPromptBuilder.INPUT_TYPES()
        assert list(spec["required"]) == ["file", "blocks", "separator"]
        assert list(spec["optional"]) == ["text", "name"]

        file_kind, file_opts = spec["required"]["file"]
        assert file_kind == "STRING"
        assert file_opts["default"] == "loras.md"
        assert file_opts["hidden"] is True

        blocks_kind, blocks_opts = spec["required"]["blocks"]
        assert blocks_kind == "STRING"
        assert blocks_opts["default"] == "[]"
        assert blocks_opts["hidden"] is True

        sep_kind, sep_opts = spec["required"]["separator"]
        assert sep_kind == "STRING"
        assert sep_opts["default"] == ", "
        assert sep_opts["multiline"] is False
        assert "\\n" in sep_opts["tooltip"]

        text_kind, text_opts = spec["optional"]["text"]
        assert text_kind == "STRING"
        assert text_opts["forceInput"] is True

        name_kind, name_opts = spec["optional"]["name"]
        assert name_kind == "STRING"
        assert name_opts["forceInput"] is True


# -------------------------------------------------------------------- combine


class TestCombine:
    def test_zero_blocks_unwired_text_is_valid_no_error(self) -> None:
        node = EPSPromptBuilder()
        result = node.build(file="loras.md", blocks="[]", separator=", ")
        assert result == ([""], [""])

    def test_one_block_no_incoming_text(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## Portrait\nA portrait.\n")
        node = EPSPromptBuilder()
        result = node.build(file="loras.md", blocks=_blocks("Portrait"), separator=", ")
        assert result == (["A portrait."], ["Portrait"])

    def test_many_blocks_combine_in_order(self, library_dir: Path) -> None:
        _write_notebook(
            library_dir, "loras.md", "## A\nfirst\n## B\nsecond\n## C\nthird\n"
        )
        node = EPSPromptBuilder()
        result = node.build(
            file="loras.md", blocks=_blocks("A", "B", "C"), separator=", "
        )
        assert result == (["first, second, third"], ["A+B+C"])

    def test_blocks_combine_in_the_json_order_not_file_order(
        self, library_dir: Path
    ) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nfirst\n## B\nsecond\n")
        node = EPSPromptBuilder()
        result = node.build(
            file="loras.md", blocks=_blocks("B", "A"), separator=", "
        )
        assert result == (["second, first"], ["B+A"])

    def test_piped_text_is_always_first(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nblock text\n")
        node = EPSPromptBuilder()
        result = node.build(
            file="loras.md",
            blocks=_blocks("A"),
            separator=", ",
            text=["piped in"],
        )
        assert result == (["piped in, block text"], ["A"])

    def test_blank_incoming_text_is_skipped_no_leading_separator(
        self, library_dir: Path
    ) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nblock text\n")
        node = EPSPromptBuilder()
        result = node.build(
            file="loras.md", blocks=_blocks("A"), separator=", ", text=["   "]
        )
        assert result == (["block text"], ["A"])

    def test_no_blocks_just_piped_text(self) -> None:
        node = EPSPromptBuilder()
        result = node.build(
            file="loras.md", blocks="[]", separator=", ", text=["only this"]
        )
        assert result == (["only this"], [""])


# ------------------------------------------------------------- multi-incoming


class TestMultiIncomingFanOut:
    def test_three_in_three_out_blocks_appended_to_each(
        self, library_dir: Path
    ) -> None:
        _write_notebook(library_dir, "loras.md", "## Style\nfilm grain\n")
        node = EPSPromptBuilder()
        texts, names = node.build(
            file="loras.md",
            blocks=_blocks("Style"),
            separator=", ",
            text=["portrait", "landscape", "still life"],
        )
        assert texts == [
            "portrait, film grain",
            "landscape, film grain",
            "still life, film grain",
        ]
        assert len(names) == 3

    def test_output_length_matches_incoming_text_length(self) -> None:
        node = EPSPromptBuilder()
        texts, names = node.build(
            file="loras.md", blocks="[]", separator=", ", text=["a", "b", "c", "d"]
        )
        assert len(texts) == len(names) == 4


# --------------------------------------------------------------- name pairing


class TestNamePairing:
    def test_pairwise_names_equal_length(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## Style\nfg\n")
        node = EPSPromptBuilder()
        _texts, names = node.build(
            file="loras.md",
            blocks=_blocks("Style"),
            separator=", ",
            text=["a", "b"],
            name=["Portrait A", "Landscape B"],
        )
        assert names == ["Portrait A+Style", "Landscape B+Style"]

    def test_broadcast_single_name(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## Style\nfg\n")
        node = EPSPromptBuilder()
        _texts, names = node.build(
            file="loras.md",
            blocks=_blocks("Style"),
            separator=", ",
            text=["a", "b", "c"],
            name=["Shared"],
        )
        assert names == ["Shared+Style", "Shared+Style", "Shared+Style"]

    def test_absent_name_falls_back_to_empty(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## Style\nfg\n")
        node = EPSPromptBuilder()
        _texts, names = node.build(
            file="loras.md", blocks=_blocks("Style"), separator=", ", text=["a", "b"]
        )
        assert names == ["Style", "Style"]

    def test_no_incoming_name_and_no_blocks_is_empty_string(self) -> None:
        node = EPSPromptBuilder()
        _texts, names = node.build(
            file="loras.md", blocks="[]", separator=", ", text=["a"]
        )
        assert names == [""]

    def test_blank_incoming_name_contributes_nothing(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## Style\nfg\n")
        node = EPSPromptBuilder()
        _texts, names = node.build(
            file="loras.md",
            blocks=_blocks("Style"),
            separator=", ",
            text=["a"],
            name=[""],
        )
        assert names == ["Style"]

    def test_mismatched_name_length_pairs_what_overlaps(
        self, library_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _write_notebook(library_dir, "loras.md", "## Style\nfg\n")
        node = EPSPromptBuilder()
        with caplog.at_level(logging.WARNING, logger="lora_library"):
            _texts, names = node.build(
                file="loras.md",
                blocks=_blocks("Style"),
                separator=", ",
                text=["a", "b", "c"],
                name=["OnlyOne", "Two"],
            )
        assert names == ["OnlyOne+Style", "Two+Style", "Style"]
        assert any("EPS Prompt Builder" in r.message for r in caplog.records)


# ----------------------------------------------------------------- separator


class TestSeparator:
    def test_default_separator(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nx\n## B\ny\n")
        node = EPSPromptBuilder()
        result = node.build(file="loras.md", blocks=_blocks("A", "B"), separator=", ")
        assert result == (["x, y"], ["A+B"])

    def test_custom_separator(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nx\n## B\ny\n")
        node = EPSPromptBuilder()
        result = node.build(file="loras.md", blocks=_blocks("A", "B"), separator=" | ")
        assert result == (["x | y"], ["A+B"])

    def test_newline_escape_decoded(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nx\n## B\ny\n")
        node = EPSPromptBuilder()
        result = node.build(file="loras.md", blocks=_blocks("A", "B"), separator="\\n")
        assert result == (["x\ny"], ["A+B"])

    def test_tab_escape_decoded(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nx\n## B\ny\n")
        node = EPSPromptBuilder()
        result = node.build(file="loras.md", blocks=_blocks("A", "B"), separator="\\t")
        assert result == (["x\ty"], ["A+B"])

    def test_backslash_escape_decoded(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nx\n## B\ny\n")
        node = EPSPromptBuilder()
        result = node.build(file="loras.md", blocks=_blocks("A", "B"), separator="\\\\")
        assert result == (["x\\y"], ["A+B"])

    def test_unrecognized_escape_passes_through_unchanged(
        self, library_dir: Path
    ) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nx\n## B\ny\n")
        node = EPSPromptBuilder()
        result = node.build(file="loras.md", blocks=_blocks("A", "B"), separator="\\d")
        assert result == (["x\\dy"], ["A+B"])

    def test_empty_separator_is_plain_concatenation(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nx\n## B\ny\n")
        node = EPSPromptBuilder()
        result = node.build(file="loras.md", blocks=_blocks("A", "B"), separator="")
        assert result == (["xy"], ["A+B"])


# --------------------------------------------------------------- malformed JSON


class TestMalformedBlocksJson:
    @pytest.mark.parametrize(
        "bad", ["not json", "{}", "42", '"just a string"', "null"]
    )
    def test_malformed_or_non_array_degrades_to_no_blocks(
        self, bad: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        node = EPSPromptBuilder()
        with caplog.at_level(logging.WARNING, logger="lora_library"):
            result = node.build(file="loras.md", blocks=bad, separator=", ")
        assert result == ([""], [""])
        assert any("EPS Prompt Builder" in r.message for r in caplog.records)

    def test_blank_blocks_value_is_silently_no_blocks(self) -> None:
        node = EPSPromptBuilder()
        result = node.build(file="loras.md", blocks="", separator=", ")
        assert result == ([""], [""])


# ------------------------------------------------------------- missing blocks


class TestMissingBlocks:
    def test_one_missing_block_name_raises_naming_it_and_the_file(
        self, library_dir: Path
    ) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nx\n")
        node = EPSPromptBuilder()
        with pytest.raises(ValueError, match="Ghost") as exc_info:
            node.build(file="loras.md", blocks=_blocks("A", "Ghost"), separator=", ")
        assert "loras.md" in str(exc_info.value)

    def test_every_missing_name_is_listed(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nx\n")
        node = EPSPromptBuilder()
        with pytest.raises(ValueError) as exc_info:
            node.build(
                file="loras.md", blocks=_blocks("Ghost1", "A", "Ghost2"), separator=", "
            )
        message = str(exc_info.value)
        assert "Ghost1" in message
        assert "Ghost2" in message

    def test_missing_notebook_file_fails_via_the_missing_name_error(self) -> None:
        node = EPSPromptBuilder()
        with pytest.raises(ValueError, match=r"loras\.md"):
            node.build(file="loras.md", blocks=_blocks("Anything"), separator=", ")

    def test_no_context_with_blocks_raises_runtime_error(self) -> None:
        nodes_prompt_builder.set_context(None)
        node = EPSPromptBuilder()
        with pytest.raises(RuntimeError):
            node.build(file="loras.md", blocks=_blocks("Anything"), separator=", ")

    def test_no_context_with_zero_blocks_is_still_valid(self) -> None:
        nodes_prompt_builder.set_context(None)
        node = EPSPromptBuilder()
        result = node.build(file="loras.md", blocks="[]", separator=", ")
        assert result == ([""], [""])


# --------------------------------------------------- cross-OS foreign-absolute
#
# 2026-08-28 owner report (mirrors nodes_notebook.py's identical fix): a
# `file` value that's absolute for the OTHER platform (e.g.
# `Z:\docs\short_prompts.md` read on POSIX) used to join WHOLE under
# `library_dir` instead of resolving relative to it.


class TestForeignAbsoluteFileHealing:
    def test_resolves_blocks_after_healing_to_an_existing_tail(
        self, library_dir: Path
    ) -> None:
        _write_notebook(library_dir, "short_prompts.md", "## A\nbodyA\n")
        node = EPSPromptBuilder()
        result = node.build(
            file=r"Z:\docs\short_prompts.md", blocks=_blocks("A"), separator=", "
        )
        assert result == (["bodyA"], ["A"])

    def test_missing_block_error_names_what_was_tried_not_a_bogus_join(
        self, library_dir: Path
    ) -> None:
        node = EPSPromptBuilder()
        with pytest.raises(ValueError) as exc_info:
            node.build(file=r"Z:\docs\short_prompts.md", blocks=_blocks("Ghost"), separator=", ")
        message = str(exc_info.value)
        assert "tried as a Windows path from another machine:" in message
        tried = message.split("tried as a Windows path from another machine: ", 1)[1]
        assert "\\" not in tried.split(")", 1)[0]
        assert str(library_dir / "docs" / "short_prompts.md") in message

    def test_ordinary_missing_block_still_uses_the_plain_resolved_wording(
        self, library_dir: Path
    ) -> None:
        node = EPSPromptBuilder()
        with pytest.raises(ValueError) as exc_info:
            node.build(file="loras.md", blocks=_blocks("Ghost"), separator=", ")
        message = str(exc_info.value)
        assert "resolved:" in message
        assert "tried as a" not in message


# ------------------------------------------------------------------ IS_CHANGED


class TestIsChanged:
    def test_changes_when_a_blocked_entrys_text_changes(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nv1\n## B\nother\n")
        token1 = EPSPromptBuilder.IS_CHANGED(file="loras.md", blocks='["A"]', separator=", ")
        _write_notebook(library_dir, "loras.md", "## A\nv2 different\n## B\nother\n")
        token2 = EPSPromptBuilder.IS_CHANGED(file="loras.md", blocks='["A"]', separator=", ")
        assert token1 != token2

    def test_stable_when_an_unblocked_entry_changes(self, library_dir: Path) -> None:
        # v0.80.0 sweep-performance round: editing an entry no block
        # references must not invalidate downstream sweep caches.
        _write_notebook(library_dir, "loras.md", "## A\nv1\n## B\nother\n")
        token1 = EPSPromptBuilder.IS_CHANGED(file="loras.md", blocks='["A"]', separator=", ")
        _write_notebook(library_dir, "loras.md", "## A\nv1\n## B\nCHANGED a lot\n")
        token2 = EPSPromptBuilder.IS_CHANGED(file="loras.md", blocks='["A"]', separator=", ")
        assert token1 == token2

    def test_zero_blocks_is_a_constant_with_no_file_dependency(
        self, library_dir: Path
    ) -> None:
        # Zero blocks never touches the file (build()'s own shortcut), so
        # the token is a constant -- file edits cannot re-run an empty node.
        token1 = EPSPromptBuilder.IS_CHANGED(file="loras.md", blocks="[]", separator=", ")
        assert token1 == "no-blocks"
        _write_notebook(library_dir, "loras.md", "## A\nnew\n")
        assert (
            EPSPromptBuilder.IS_CHANGED(file="loras.md", blocks="[]", separator=", ")
            == "no-blocks"
        )

    def test_stable_across_calls_when_nothing_changed(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nsame\n")
        token1 = EPSPromptBuilder.IS_CHANGED(file="loras.md", blocks="[]", separator=", ")
        token2 = EPSPromptBuilder.IS_CHANGED(file="loras.md", blocks="[]", separator=", ")
        assert token1 == token2

    def test_uses_a_missing_token_for_a_nonexistent_file(self) -> None:
        token = EPSPromptBuilder.IS_CHANGED(file="loras.md", blocks='["A"]', separator=", ")
        assert isinstance(token, str)
        assert "missing" in token

    def test_missing_then_created_file_changes_the_token(self, library_dir: Path) -> None:
        before = EPSPromptBuilder.IS_CHANGED(file="loras.md", blocks='["A"]', separator=", ")
        _write_notebook(library_dir, "loras.md", "## A\nnow exists\n")
        after = EPSPromptBuilder.IS_CHANGED(file="loras.md", blocks='["A"]', separator=", ")
        assert before != after

    def test_handles_no_context_without_raising(self) -> None:
        nodes_prompt_builder.set_context(None)
        token = EPSPromptBuilder.IS_CHANGED(file="loras.md", blocks="[]", separator=", ")
        assert isinstance(token, str)

    def test_accepts_the_input_is_list_wrapped_shape(self, library_dir: Path) -> None:
        # Real ComfyUI calls IS_CHANGED the same way it calls build() for an
        # INPUT_IS_LIST node -- every declared input arrives already
        # wrapped in a list, including scalar widgets (module docstring).
        _write_notebook(library_dir, "loras.md", "## A\nx\n")
        token = EPSPromptBuilder.IS_CHANGED(
            file=["loras.md"], blocks=["[]"], separator=[", "]
        )
        assert isinstance(token, str)
        assert "missing" not in token


# ------------------------------------------------------- INPUT_IS_LIST wrapping


class TestInputIsListWrapping:
    def test_build_accepts_the_wrapped_widget_shape(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nx\n")
        node = EPSPromptBuilder()
        result = node.build(
            file=["loras.md"], blocks=[_blocks("A")], separator=[", "]
        )
        assert result == (["x"], ["A"])


# --------------------------------------------------------------- no ComfyUI import


def test_module_never_imports_comfy_or_torch() -> None:
    assert "comfy" not in nodes_prompt_builder.__dict__
    assert "torch" not in nodes_prompt_builder.__dict__
    assert "folder_paths" not in nodes_prompt_builder.__dict__
    source = inspect.getsource(sys.modules[nodes_prompt_builder.__name__])
    assert "import comfy" not in source
    assert "import torch" not in source
    assert "import folder_paths" not in source
