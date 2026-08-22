"""Tests for lora_library.nodes_notebook (FORMAT.md §6.1)."""

from __future__ import annotations

import json
import logging
import os
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


# ------------------------------------------- missing-file error: FORMAT.md §6.1
#
# 2026-07-19 owner report: pointing the library folder at a NAS the server
# machine can't reach made the .md "not found" at node-run time, invisible
# until then — the node error must name the RESOLVED ABSOLUTE path it tried
# (so a NAS mismatch is obvious) and, when the library folder itself isn't
# reachable, add a hint pointing at EPSNodes settings instead of reading
# like a plain typo.


class TestMissingFileErrorNamesResolvedPath:
    def test_error_names_the_resolved_absolute_path(self, library_dir: Path) -> None:
        node = nodes_notebook.LoraLibraryNotebook()
        with pytest.raises(ValueError) as exc_info:
            node.read_entry(file="loras.md", entry="Anything")
        resolved = library_dir / "loras.md"
        assert str(resolved) in str(exc_info.value)
        assert resolved.is_absolute()

    def test_no_unreachable_hint_when_the_library_dir_itself_is_fine(
        self, library_dir: Path
    ) -> None:
        # The library folder exists (the `library_dir` fixture created it);
        # only the file itself is missing — that's an ordinary typo/rename,
        # not a NAS problem, so no hint.
        node = nodes_notebook.LoraLibraryNotebook()
        with pytest.raises(ValueError) as exc_info:
            node.read_entry(file="loras.md", entry="Anything")
        assert "isn't reachable from the server machine" not in str(exc_info.value)

    def test_unreachable_hint_when_the_configured_library_dir_cannot_be_created(
        self, context: LibraryContext, tmp_path: Path
    ) -> None:
        # A real (not monkeypatched) unreachable-folder scenario: a
        # read-only parent means `library_dir()`'s `mkdir(parents=True)`
        # genuinely cannot create the configured folder — unlike a plain
        # nonexistent path under a writable tmp_path, which `mkdir`
        # would just create. Root bypasses permission bits entirely, so
        # this needs a non-root test runner (the usual case).
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            pytest.skip("cannot simulate a permission-denied directory as root")
        readonly_parent = tmp_path / "readonly"
        readonly_parent.mkdir()
        readonly_parent.chmod(0o555)
        configured = readonly_parent / "library"
        context.save_config({"library_dir": str(configured)})
        node = nodes_notebook.LoraLibraryNotebook()
        try:
            with pytest.raises(ValueError) as exc_info:
                node.read_entry(file="loras.md", entry="Anything")
        finally:
            readonly_parent.chmod(0o755)
        message = str(exc_info.value)
        assert str(configured / "loras.md") in message
        assert "isn't reachable from the server machine" in message
        assert "EPSNodes settings" in message

    def test_recovers_when_resolve_notebook_file_raises_oserror(
        self, context: LibraryContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The real `resolve_notebook_file` resolves a relative `file` via
        # `library_dir()`, which unconditionally `mkdir`s — an unreachable
        # configured folder (unmounted NAS, permission-denied mount point)
        # makes that raise an OSError before a path is ever produced.
        # read_entry must still surface a clean ValueError naming a path,
        # never a raw OSError.
        configured = tmp_path / "nas" / "not-mounted"
        context.save_config({"library_dir": str(configured)})
        monkeypatch.setattr(
            context,
            "resolve_notebook_file",
            lambda _file_value: (_ for _ in ()).throw(OSError("no such device")),
        )
        node = nodes_notebook.LoraLibraryNotebook()
        with pytest.raises(ValueError) as exc_info:
            node.read_entry(file="loras.md", entry="Anything")
        message = str(exc_info.value)
        assert str(configured / "loras.md") in message
        assert "isn't reachable from the server machine" in message

    def test_recovers_with_an_absolute_file_when_resolve_raises(
        self, context: LibraryContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Absolute `file` values pass through resolve_notebook_file
        # untouched (no library_dir() call at all in the real method) — the
        # peek fallback must mirror that rather than rebasing under
        # library_dir.
        absolute_file = str(tmp_path / "elsewhere" / "loras.md")
        monkeypatch.setattr(
            context,
            "resolve_notebook_file",
            lambda _file_value: (_ for _ in ()).throw(OSError("boom")),
        )
        node = nodes_notebook.LoraLibraryNotebook()
        with pytest.raises(ValueError) as exc_info:
            node.read_entry(file=absolute_file, entry="Anything")
        assert absolute_file in str(exc_info.value)


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


# ------------------------------------------------ M3 `pinned` (FORMAT.md §6.1)
#
# Provenance M3 (v0.71.0): a TAIL-appended hidden `pinned` STRING widget.
# Empty = live; non-empty = the pin JSON EPS Save Image captured when an
# image was made, and read_entry outputs THOSE entries without opening the
# file. Malformed pin -> warning + live. IS_CHANGED folds the pin in.


def _pin(entries: list[dict], file: str = "loras.md", token: str | None = "m1_p1") -> str:
    return json.dumps(
        nodes_notebook.make_pin(entries, file=file, token=token, captured="2026-08-22T00:00:00Z")
    )


class TestPinnedM3:
    def test_widget_is_tail_appended_optional_hidden_string(self) -> None:
        spec = nodes_notebook.LoraLibraryNotebook.INPUT_TYPES()
        assert list(spec["required"]) == ["file", "entry"]
        assert list(spec["optional"]) == ["pinned"]  # the tail -- FORMAT.md §8
        kind, options = spec["optional"]["pinned"]
        assert kind == "STRING"
        assert options["default"] == ""
        assert options["multiline"] is False
        assert options["hidden"] is True  # Vue-nodes hide flag (§7.5)
        assert nodes_notebook.PIN_WIDGET == "pinned"
        assert nodes_notebook.PIN_FORMAT == 1

    def test_pinned_outputs_the_pinned_lists_in_pin_order_without_the_file(self) -> None:
        # No notebook on disk at all: the live path would raise "does not
        # exist" -- the pinned path never opens it.
        node = nodes_notebook.LoraLibraryNotebook()
        pin = _pin([{"name": "B", "text": "old B"}, {"name": "A", "text": "old A"}])
        assert node.read_entry(file="loras.md", entry="A\nB", pinned=pin) == (
            ["old B", "old A"],
            ["B", "A"],
        )

    def test_pinned_wins_over_an_edited_live_file_and_empty_reads_live(
        self, library_dir: Path
    ) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nedited since\n")
        node = nodes_notebook.LoraLibraryNotebook()
        pin = _pin([{"name": "A", "text": "as saved"}])
        assert node.read_entry(file="loras.md", entry="A", pinned=pin) == (["as saved"], ["A"])
        # unpin (the frontend's one click = "") and the live file is back
        assert node.read_entry(file="loras.md", entry="A", pinned="") == (
            ["edited since"],
            ["A"],
        )
        assert node.read_entry(file="loras.md", entry="A") == (["edited since"], ["A"])

    def test_pinned_needs_no_context(self) -> None:
        nodes_notebook.set_context(None)
        node = nodes_notebook.LoraLibraryNotebook()
        pin = _pin([{"name": "A", "text": "t"}])
        assert node.read_entry(file="loras.md", entry="A", pinned=pin) == (["t"], ["A"])

    @pytest.mark.parametrize(
        "bad",
        [
            "not json",
            "[]",
            "42",
            "{}",
            json.dumps({"entries": []}),
            json.dumps({"entries": "A"}),
            json.dumps({"entries": [{"name": "A"}]}),  # no text
            json.dumps({"entries": [{"name": "A", "text": 7}]}),  # non-string text
            json.dumps({"entries": [{"name": "A", "text": "ok"}, "junk"]}),
        ],
    )
    def test_malformed_pin_warns_and_reads_live(
        self, library_dir: Path, caplog: pytest.LogCaptureFixture, bad: str
    ) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nlive\n")
        node = nodes_notebook.LoraLibraryNotebook()
        with caplog.at_level(logging.WARNING, logger="lora_library"):
            assert node.read_entry(file="loras.md", entry="A", pinned=bad) == (["live"], ["A"])
        assert any("reading the live file" in r.message for r in caplog.records)

    def test_malformed_pin_keeps_the_live_paths_loud_errors(self) -> None:
        node = nodes_notebook.LoraLibraryNotebook()
        with pytest.raises(ValueError, match=r"loras\.md"):
            node.read_entry(file="loras.md", entry="A", pinned="not json")

    def test_parse_pinned_shapes(self) -> None:
        assert nodes_notebook.parse_pinned("") is None
        assert nodes_notebook.parse_pinned("   ") is None
        assert nodes_notebook.parse_pinned(None) is None
        pin = _pin([{"name": "X", "text": "tx", "extra": "dropped"}])
        assert nodes_notebook.parse_pinned(pin) == [{"name": "X", "text": "tx"}]
        # a missing/non-string name degrades to "" -- text is what matters
        assert nodes_notebook.parse_pinned(json.dumps({"entries": [{"text": "only"}]})) == [
            {"name": "", "text": "only"}
        ]

    def test_make_pin_shape_is_the_contract(self) -> None:
        pin = nodes_notebook.make_pin(
            [{"name": "A", "text": "ta", "category": "ignored"}],
            file="loras.md",
            token="m1_p1",
            captured="2026-08-22T00:00:00Z",
        )
        assert pin == {
            "format": 1,
            "entries": [{"name": "A", "text": "ta"}],
            "source": {"file": "loras.md", "token": "m1_p1", "captured": "2026-08-22T00:00:00Z"},
        }
        assert list(pin) == ["format", "entries", "source"]
        # a None token (run_info without one) is carried as null, not dropped
        assert nodes_notebook.make_pin([], file="f", token=None, captured="c")["source"] == {
            "file": "f",
            "token": None,
            "captured": "c",
        }

    def test_is_changed_folds_the_pin_in(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nx\n")
        cls = nodes_notebook.LoraLibraryNotebook
        live = cls.IS_CHANGED(file="loras.md", entry="A")
        assert live == cls.IS_CHANGED(file="loras.md", entry="A", pinned="")
        pin_x = _pin([{"name": "A", "text": "x"}])
        pin_y = _pin([{"name": "A", "text": "y"}])
        pinned = cls.IS_CHANGED(file="loras.md", entry="A", pinned=pin_x)
        assert pinned != live
        other = cls.IS_CHANGED(file="loras.md", entry="A", pinned=pin_y)
        assert other != pinned
        assert cls.IS_CHANGED(file="loras.md", entry="A", pinned="") == live  # unpin = back to live

    def test_resolve_selection_is_the_live_path_shared_with_save_image(
        self, library_dir: Path, context: LibraryContext
    ) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nbodyA\n## B\nbodyB\n")
        assert nodes_notebook.resolve_selection(context, "loras.md", "B\nA") == (
            ["bodyB", "bodyA"],
            ["B", "A"],
        )
        with pytest.raises(ValueError, match="Ghost"):
            nodes_notebook.resolve_selection(context, "loras.md", "Ghost")
        with pytest.raises(ValueError, match=r"loras\.md"):
            nodes_notebook.resolve_selection(context, "loras.md", "")
        with pytest.raises(ValueError, match=r"other\.md"):
            nodes_notebook.resolve_selection(context, "other.md", "A")
