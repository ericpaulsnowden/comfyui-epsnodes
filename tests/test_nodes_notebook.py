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


# --------------------------------------------------- cross-OS foreign-absolute
#
# 2026-08-28 owner report: Linux box, library on a gvfs SMB mount, workflow
# saved on the Windows PC. The `file` widget held `Z:\docs\short_prompts.md`;
# on POSIX that used to join WHOLE under `library_dir`
# (`context.is_foreign_absolute`/`heal_foreign_absolute` fix that instead).


class TestForeignAbsoluteFileHealing:
    def test_reads_the_entry_after_healing_to_an_existing_tail(
        self, library_dir: Path
    ) -> None:
        _write_notebook(library_dir, "short_prompts.md", "## Portrait\nSome prompt text.\n")
        node = nodes_notebook.LoraLibraryNotebook()
        result = node.read_entry(file=r"Z:\docs\short_prompts.md", entry="Portrait")
        assert result == (["Some prompt text."], ["Portrait"])

    def test_missing_file_error_names_what_was_tried_not_a_bogus_join(
        self, library_dir: Path
    ) -> None:
        node = nodes_notebook.LoraLibraryNotebook()
        with pytest.raises(ValueError) as exc_info:
            node.read_entry(file=r"Z:\docs\short_prompts.md", entry="Anything")
        message = str(exc_info.value)
        assert "tried as a Windows path from another machine:" in message
        tried = message.split("tried as a Windows path from another machine: ", 1)[1]
        # The candidate NAMED in the hint is a clean local path -- no
        # leftover backslash/drive-colon from the original foreign value.
        assert "\\" not in tried.split(")", 1)[0]
        assert str(library_dir / "docs" / "short_prompts.md") in message

    def test_ordinary_missing_file_still_uses_the_plain_resolved_wording(
        self, library_dir: Path
    ) -> None:
        # Regression guard: a normal (non-foreign) missing file must keep
        # the pre-existing "(resolved: ...)" wording, not the new hint.
        node = nodes_notebook.LoraLibraryNotebook()
        with pytest.raises(ValueError) as exc_info:
            node.read_entry(file="loras.md", entry="Anything")
        message = str(exc_info.value)
        assert "resolved:" in message
        assert "tried as a" not in message


# --------------------------------------------------------------- multi-select


class TestMultiSelect:
    def test_output_is_file_order_not_click_order(self, library_dir: Path) -> None:
        # v0.85.0 (owner report 2026-08-28: "sometimes the order of images
        # doesn't match the order of list"): `entry` records the order the
        # user CLICKED, which the panel's own gestures disagree about
        # (ctrl+click appends, shift+click slices in list order). The node
        # now emits FILE order regardless, so a §6.10 run token's `t{N}`
        # follows what the list shows. This inverts the pre-v0.85.0
        # contract deliberately.
        _write_notebook(library_dir, "loras.md", "## A\nbodyA\n## B\nbodyB\n## C\nbodyC\n")
        node = nodes_notebook.LoraLibraryNotebook()
        texts, names = node.read_entry(file="loras.md", entry="B\nA\nC")
        assert names == ["A", "B", "C"]
        assert texts == ["bodyA", "bodyB", "bodyC"]

    def test_file_order_survives_an_already_saved_click_ordered_widget(
        self, library_dir: Path
    ) -> None:
        # The whole reason the sort lives in the NODE and not only in the
        # panel: a workflow saved before v0.85.0 still holds click order.
        _write_notebook(library_dir, "loras.md", "## A\nbodyA\n## B\nbodyB\n## C\nbodyC\n")
        node = nodes_notebook.LoraLibraryNotebook()
        _texts, names = node.read_entry(file="loras.md", entry="C\nB")
        assert names == ["B", "C"]

    def test_file_order_follows_a_reordered_file(self, library_dir: Path) -> None:
        # Drag an entry in the notebook (which rewrites the file) and the
        # emitted order follows the NEW file order, same selection.
        _write_notebook(library_dir, "loras.md", "## A\nbodyA\n## B\nbodyB\n")
        node = nodes_notebook.LoraLibraryNotebook()
        assert node.read_entry(file="loras.md", entry="A\nB")[1] == ["A", "B"]
        _write_notebook(library_dir, "loras.md", "## B\nbodyB\n## A\nbodyA\n")
        assert node.read_entry(file="loras.md", entry="A\nB")[1] == ["B", "A"]

    def test_texts_and_names_stay_paired_across_categories(self, library_dir: Path) -> None:
        _write_notebook(
            library_dir, "loras.md", "# Cat A\n## A\nbodyA\n# Cat B\n## B\nbodyB\n"
        )
        node = nodes_notebook.LoraLibraryNotebook()
        texts, names = node.read_entry(file="loras.md", entry="B\nA")
        # v0.85.0: file order across categories too (Cat A's A precedes
        # Cat B's B), pairing intact.
        assert list(zip(names, texts, strict=True)) == [("A", "bodyA"), ("B", "bodyB")]

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
        assert cls.CATEGORY == "EPSNodes/Prompts"
        assert cls.RETURN_TYPES == ("STRING", "STRING")
        assert cls.RETURN_NAMES == ("text", "name")
        assert cls.OUTPUT_IS_LIST == (True, True)
        assert cls.FUNCTION == "read_entry"
        # Chaining inputs (owner ask 2026-09-09): declared so the new
        # text/name links arrive as whole lists rather than being mapped
        # over -- see the module docstring's "Chaining inputs" paragraph.
        assert cls.INPUT_IS_LIST is True


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
        # `drafts` (v0.86.0) is appended AFTER `pinned`, and chaining's
        # `text`/`name`/`separator` (owner ask 2026-09-09) after THAT --
        # FORMAT.md §8's tail-only law: the final declaration order is
        # required=[file, entry], optional=[pinned, drafts, text, name,
        # separator]. `text`/`name` are forceInput and carry no
        # widgets_values slot at all -- see TestChainingInputs's own
        # widget-order test for the REAL (serialized-widget-only) order.
        assert list(spec["optional"]) == ["pinned", "drafts", "text", "name", "separator"]
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

    def test_is_changed_pinned_is_a_constant_and_ignores_the_file(
        self, library_dir: Path
    ) -> None:
        # v0.80.0 contract: while pinned, IS_CHANGED is the constant
        # "pinned" -- the pin JSON is a WIDGET, already inside core's
        # input-hash key, so a pin change re-executes via the signature;
        # and the FILE is irrelevant to a pinned node's output, so an
        # on-disk edit must NOT re-run whole sweeps built on pins.
        _write_notebook(library_dir, "loras.md", "## A\nx\n")
        cls = nodes_notebook.LoraLibraryNotebook
        live = cls.IS_CHANGED(file="loras.md", entry="A")
        assert live == cls.IS_CHANGED(file="loras.md", entry="A", pinned="")
        pin_x = _pin([{"name": "A", "text": "x"}])
        pinned = cls.IS_CHANGED(file="loras.md", entry="A", pinned=pin_x)
        assert pinned == "pinned"
        assert pinned != live
        _write_notebook(library_dir, "loras.md", "## A\nEDITED\n## Z\nnew\n")
        assert cls.IS_CHANGED(file="loras.md", entry="A", pinned=pin_x) == "pinned"
        # unpinned sees the edit
        assert cls.IS_CHANGED(file="loras.md", entry="A", pinned="") != live

    def test_is_changed_tracks_selected_content_not_the_whole_file(
        self, library_dir: Path
    ) -> None:
        # v0.80.0 sweep-performance round: editing an entry the selection
        # does NOT include must not invalidate the token (previously the
        # whole-file mtime did, re-running entire downstream sweeps).
        _write_notebook(library_dir, "loras.md", "## A\nbodyA\n## B\nbodyB\n")
        cls = nodes_notebook.LoraLibraryNotebook
        before = cls.IS_CHANGED(file="loras.md", entry="A")
        _write_notebook(library_dir, "loras.md", "## A\nbodyA\n## B\nCHANGED\n")
        assert cls.IS_CHANGED(file="loras.md", entry="A") == before  # B is unselected
        _write_notebook(library_dir, "loras.md", "## A\nCHANGED-A\n## B\nCHANGED\n")
        assert cls.IS_CHANGED(file="loras.md", entry="A") != before  # A is selected

    def test_is_changed_flips_when_a_selected_entry_appears_or_vanishes(
        self, library_dir: Path
    ) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nbodyA\n")
        cls = nodes_notebook.LoraLibraryNotebook
        missing = cls.IS_CHANGED(file="loras.md", entry="A\nGhost")
        _write_notebook(library_dir, "loras.md", "## A\nbodyA\n## Ghost\nnow real\n")
        assert cls.IS_CHANGED(file="loras.md", entry="A\nGhost") != missing

    def test_is_changed_missing_file_token_recovers(self, library_dir: Path) -> None:
        cls = nodes_notebook.LoraLibraryNotebook
        gone = cls.IS_CHANGED(file="nope.md", entry="A")
        assert gone.startswith("missing:")
        _write_notebook(library_dir, "nope.md", "## A\nx\n")
        assert cls.IS_CHANGED(file="nope.md", entry="A") != gone

    def test_resolve_selection_is_the_live_path_shared_with_save_image(
        self, library_dir: Path, context: LibraryContext
    ) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nbodyA\n## B\nbodyB\n")
        # v0.85.0: file order (the pin capture shares this path, so a pin
        # records exactly what the run used).
        assert nodes_notebook.resolve_selection(context, "loras.md", "B\nA") == (
            ["bodyA", "bodyB"],
            ["A", "B"],
        )
        with pytest.raises(ValueError, match="Ghost"):
            nodes_notebook.resolve_selection(context, "loras.md", "Ghost")
        with pytest.raises(ValueError, match=r"loras\.md"):
            nodes_notebook.resolve_selection(context, "loras.md", "")
        with pytest.raises(ValueError, match=r"other\.md"):
            nodes_notebook.resolve_selection(context, "other.md", "A")


# ---------------------------------------------------- M4 unsaved-edit `drafts`
#
# v0.86.0 (owner ask 2026-08-28: "if you change a prompt that is selected and
# run it without saving, it should run the changed prompt"). A TAIL-appended
# (after `pinned`), hidden `drafts` STRING widget, default "{}" -- a JSON
# object mapping selected entry name -> unsaved text. `resolve_selection`
# applies a draft ON TOP OF the file's text for a selected name it names;
# text only, never position. Ignored while pinned. Malformed -> warning +
# no drafts, never a raise.


class TestDraftsM4:
    def test_widget_is_tail_appended_after_pinned_optional_hidden_string(self) -> None:
        spec = nodes_notebook.LoraLibraryNotebook.INPUT_TYPES()
        assert list(spec["required"]) == ["file", "entry"]
        # Chaining's `text`/`name`/`separator` (owner ask 2026-09-09) land
        # AFTER `drafts` -- see TestPinnedM3's identical assertion above.
        assert list(spec["optional"]) == ["pinned", "drafts", "text", "name", "separator"]
        kind, options = spec["optional"]["drafts"]
        assert kind == "STRING"
        assert options["default"] == "{}"
        assert options["multiline"] is False
        assert options["hidden"] is True  # Vue-nodes hide flag (§7.5)
        assert nodes_notebook.DRAFTS_WIDGET == "drafts"

    def test_drafts_is_excluded_from_the_state_registry(self) -> None:
        # Like `pinned`, a draft is scratch text the panel maintains, not
        # something a Universal State Controller save/apply should carry.
        excluded = nodes_notebook.LoraLibraryNotebook.EPS_STATE_WIDGETS["excluded"]
        assert nodes_notebook.DRAFTS_WIDGET in excluded

    # ------------------------------------------------------------- parse_drafts

    def test_parse_drafts_shapes(self) -> None:
        assert nodes_notebook.parse_drafts("") == {}
        assert nodes_notebook.parse_drafts("   ") == {}
        assert nodes_notebook.parse_drafts(None) == {}
        assert nodes_notebook.parse_drafts("{}") == {}
        assert nodes_notebook.parse_drafts(json.dumps({"A": "unsaved text"})) == {
            "A": "unsaved text"
        }
        assert nodes_notebook.parse_drafts(json.dumps({"A": "a", "B": "b"})) == {
            "A": "a",
            "B": "b",
        }

    @pytest.mark.parametrize(
        "bad",
        ["not json", "[]", "42", '"just a string"'],
    )
    def test_parse_drafts_malformed_shape_warns_and_degrades_to_none(
        self, caplog: pytest.LogCaptureFixture, bad: str
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="lora_library"):
            assert nodes_notebook.parse_drafts(bad) == {}
        assert any("ignoring unsaved edits" in r.message for r in caplog.records)

    def test_parse_drafts_drops_one_malformed_entry_and_keeps_the_rest(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # A scratch buffer, not a contract: one bad key/value must not blank
        # out every other unsaved edit in a multi-select run.
        raw = json.dumps({"A": "good", "B": 7, "C": "also good"})
        with caplog.at_level(logging.WARNING, logger="lora_library"):
            assert nodes_notebook.parse_drafts(raw) == {"A": "good", "C": "also good"}
        assert any("malformed" in r.message for r in caplog.records)

    # ------------------------------------------------------- resolve_selection

    def test_resolve_selection_applies_a_draft_to_a_selected_entry(
        self, library_dir: Path, context: LibraryContext
    ) -> None:
        _write_notebook(library_dir, "loras.md", "## A\non disk\n")
        drafts = json.dumps({"A": "auditioned, unsaved text"})
        assert nodes_notebook.resolve_selection(context, "loras.md", "A", drafts) == (
            ["auditioned, unsaved text"],
            ["A"],
        )

    def test_resolve_selection_ignores_a_draft_for_an_unselected_entry(
        self, library_dir: Path, context: LibraryContext
    ) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nbodyA\n## B\nbodyB\n")
        # Only "A" is selected -- a draft naming "B" (selected nowhere) must
        # not surface in the output at all, and must not error.
        drafts = json.dumps({"B": "should never appear"})
        assert nodes_notebook.resolve_selection(context, "loras.md", "A", drafts) == (
            ["bodyA"],
            ["A"],
        )

    def test_resolve_selection_ignores_a_draft_for_a_nonexistent_entry(
        self, library_dir: Path, context: LibraryContext
    ) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nbodyA\n")
        # "Ghost" is neither in the file nor selected -- a scratch buffer,
        # not a contract: this must never raise.
        drafts = json.dumps({"Ghost": "text for an entry that doesn't exist"})
        assert nodes_notebook.resolve_selection(context, "loras.md", "A", drafts) == (
            ["bodyA"],
            ["A"],
        )

    def test_resolve_selection_preserves_file_order_with_drafts_applied(
        self, library_dir: Path, context: LibraryContext
    ) -> None:
        # v0.85.0 file-order sort is untouched by drafts -- a draft changes
        # TEXT only. Selection is click-ordered ("B\nA"); output must still
        # come back in FILE order (A, B), each with its own draft applied.
        _write_notebook(library_dir, "loras.md", "## A\nbodyA\n## B\nbodyB\n")
        drafts = json.dumps({"A": "draft A", "B": "draft B"})
        assert nodes_notebook.resolve_selection(context, "loras.md", "B\nA", drafts) == (
            ["draft A", "draft B"],
            ["A", "B"],
        )

    def test_resolve_selection_malformed_drafts_degrades_to_the_file_text(
        self, library_dir: Path, context: LibraryContext, caplog: pytest.LogCaptureFixture
    ) -> None:
        _write_notebook(library_dir, "loras.md", "## A\non disk\n")
        with caplog.at_level(logging.WARNING, logger="lora_library"):
            result = nodes_notebook.resolve_selection(context, "loras.md", "A", "not json")
        assert result == (["on disk"], ["A"])
        assert any("ignoring unsaved edits" in r.message for r in caplog.records)

    def test_resolve_selection_default_drafts_is_backward_compatible(
        self, library_dir: Path, context: LibraryContext
    ) -> None:
        # eps_image/nodes_save_image.py's pin-capture path calls this with
        # exactly 3 positional args (context, file, entry) -- the drafts
        # parameter must default to "no drafts" so that call keeps reading
        # the plain file text, unchanged from before v0.86.0.
        _write_notebook(library_dir, "loras.md", "## A\non disk\n")
        assert nodes_notebook.resolve_selection(context, "loras.md", "A") == (
            ["on disk"],
            ["A"],
        )

    # -------------------------------------------------------------- read_entry

    def test_read_entry_runs_the_draft_text_not_the_file_text(
        self, library_dir: Path
    ) -> None:
        # The owner's ask, verbatim: "run it without saving ... run the
        # changed prompt."
        _write_notebook(library_dir, "loras.md", "## A\nsaved text\n")
        node = nodes_notebook.LoraLibraryNotebook()
        drafts = json.dumps({"A": "changed, unsaved text"})
        assert node.read_entry(file="loras.md", entry="A", drafts=drafts) == (
            ["changed, unsaved text"],
            ["A"],
        )

    def test_read_entry_pinned_wins_and_ignores_drafts_entirely(
        self, library_dir: Path
    ) -> None:
        _write_notebook(library_dir, "loras.md", "## A\non disk\n")
        node = nodes_notebook.LoraLibraryNotebook()
        pin = _pin([{"name": "A", "text": "pinned text"}])
        drafts = json.dumps({"A": "a draft that must be ignored"})
        assert node.read_entry(file="loras.md", entry="A", pinned=pin, drafts=drafts) == (
            ["pinned text"],
            ["A"],
        )

    # -------------------------------------------------------------- IS_CHANGED

    def test_is_changed_changes_when_a_draft_changes_for_the_same_selection(
        self, library_dir: Path
    ) -> None:
        # The cache-identity contract: same file, same selection, only the
        # draft differs -- the token (and so the node's cache identity)
        # must differ too.
        _write_notebook(library_dir, "loras.md", "## A\non disk\n")
        cls = nodes_notebook.LoraLibraryNotebook
        no_draft = cls.IS_CHANGED(file="loras.md", entry="A")
        draft_one = cls.IS_CHANGED(file="loras.md", entry="A", drafts=json.dumps({"A": "one"}))
        draft_two = cls.IS_CHANGED(file="loras.md", entry="A", drafts=json.dumps({"A": "two"}))
        assert len({no_draft, draft_one, draft_two}) == 3

    def test_is_changed_ignores_a_draft_for_an_unselected_entry(
        self, library_dir: Path
    ) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nbodyA\n## B\nbodyB\n")
        cls = nodes_notebook.LoraLibraryNotebook
        before = cls.IS_CHANGED(file="loras.md", entry="A")
        after = cls.IS_CHANGED(file="loras.md", entry="A", drafts=json.dumps({"B": "unrelated"}))
        assert before == after

    def test_is_changed_pinned_is_unaffected_by_drafts(self, library_dir: Path) -> None:
        # Pinned mode wins outright: the constant "pinned" token must not
        # move just because a (moot) draft changed.
        _write_notebook(library_dir, "loras.md", "## A\nx\n")
        cls = nodes_notebook.LoraLibraryNotebook
        pin_x = _pin([{"name": "A", "text": "x"}])
        token_one = cls.IS_CHANGED(
            file="loras.md", entry="A", pinned=pin_x, drafts=json.dumps({"A": "one"})
        )
        token_two = cls.IS_CHANGED(
            file="loras.md", entry="A", pinned=pin_x, drafts=json.dumps({"A": "two"})
        )
        assert token_one == token_two == "pinned"

    def test_is_changed_malformed_drafts_does_not_raise(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nx\n")
        token = nodes_notebook.LoraLibraryNotebook.IS_CHANGED(
            file="loras.md", entry="A", drafts="not json"
        )
        assert isinstance(token, str)

    # ------------------------------------------------- §6.14 pin-capture path
    #
    # `eps_image/nodes_save_image.py`'s `_capture_notebook` (read-only for
    # this change) builds a pin by calling THIS `resolve_selection` with
    # whatever `file`/`entry` the queued PROMPT carried -- see FORMAT.md
    # §6.14 and the module docstring's "Unsaved-edit drafts" paragraph. This
    # pins the mechanism that path depends on: once a caller also forwards
    # the queued `drafts` value through (as this call does), the entries it
    # gets back -- and so the pin it bakes -- already carry the draft text a
    # run actually used, never the stale on-disk text. (`_capture_notebook`
    # itself does not yet extract/forward `inputs["drafts"]`; see this
    # session's report for that one-line follow-up, out of scope here since
    # eps_image/nodes_save_image.py is read-only for this change.)

    def test_resolve_selection_is_the_mechanism_pin_capture_needs_for_drafts(
        self, library_dir: Path, context: LibraryContext
    ) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nstale on-disk text\n")
        drafts = json.dumps({"A": "the text this run actually used"})
        texts, names = nodes_notebook.resolve_selection(context, "loras.md", "A", drafts)
        # This is exactly the shape `_capture_notebook` zips into
        # `{"name": n, "text": t}` pairs before `make_pin()` -- a pin built
        # from this result records the DRAFT text, not the file's.
        entries = [{"name": n, "text": t} for t, n in zip(texts, names, strict=True)]
        assert entries == [{"name": "A", "text": "the text this run actually used"}]


# --------------------------------------------------- Chaining inputs (2026-09-09)
#
# Owner ask 2026-09-09: "The prompt notebook node should also be able to
# accept text and name as inputs. That way they can be chained together
# with other nodes or a builder node could come before the notebook node."
# Chosen semantics (owner): combine/cross-product, incoming text FIRST --
# one output per (incoming text, selected/pinned entry) pair, in
# INCOMING-MAJOR order (every entry for incoming #1, then every entry for
# incoming #2, ...). `text`/`name` are optional forceInput-only STRING
# inputs (no widget); a new TAIL `separator` STRING widget (after
# `pinned`/`drafts`) decides how TEXT joins -- names always join with `+`,
# unconfigurable, matching EPSPromptBuilder. INPUT_IS_LIST = True means
# every widget -- including the previously-scalar `file`/`entry`/`pinned`/
# `drafts`/`separator` -- now arrives list-wrapped too.


class TestChainingWidgetShape:
    def test_input_types_declares_text_and_name_forceinput_only(self) -> None:
        spec = nodes_notebook.LoraLibraryNotebook.INPUT_TYPES()
        text_type, text_opts = spec["optional"]["text"]
        name_type, name_opts = spec["optional"]["name"]
        assert text_type == "STRING"
        assert text_opts["forceInput"] is True
        assert "default" not in text_opts  # link-only -- no widget/widgets_values slot
        assert name_type == "STRING"
        assert name_opts["forceInput"] is True
        assert "default" not in name_opts

    def test_separator_widget_is_visible_not_hidden(self) -> None:
        spec = nodes_notebook.LoraLibraryNotebook.INPUT_TYPES()
        kind, options = spec["optional"]["separator"]
        assert kind == "STRING"
        # Owner ask 2026-09-09: default is the `\n` ESCAPE, not a literal
        # newline -- a plain STRING widget cannot hold one, so
        # `_decode_separator` converts it at run time.
        assert options["default"] == "\\n"
        assert options["multiline"] is False
        # Unlike file/entry/pinned/drafts: a plain visible widget the panel
        # never touches, same as EPSPromptBuilder's own `separator`.
        assert "hidden" not in options

    def test_serialized_widget_order_is_pinned_drafts_then_separator(self) -> None:
        # FORMAT.md §8: widgets_values restores POSITIONALLY. `text`/`name`
        # are forceInput and carry no widgets_values slot at all, so the
        # REAL (serialized) widget order is file, entry, pinned, drafts,
        # separator regardless of where text/name sit in `optional`'s own
        # declaration order -- this is the hazard the task/owner flagged:
        # a new tail widget landing before an existing one shifts every
        # already-saved workflow's values into the wrong slot.
        spec = nodes_notebook.LoraLibraryNotebook.INPUT_TYPES()
        serialized = []
        for section in ("required", "optional"):
            for name, definition in spec[section].items():
                options = definition[1] if len(definition) > 1 else {}
                if options.get("forceInput"):
                    continue
                serialized.append(name)
        assert serialized == ["file", "entry", "pinned", "drafts", "separator"]

    def test_separator_is_declared_in_the_state_registry_text_name_are_absent(self) -> None:
        descriptor = nodes_notebook.LoraLibraryNotebook.EPS_STATE_WIDGETS
        assert descriptor["widgets"]["separator"] == {"kind": "string", "max_len": 10000}
        excluded = descriptor.get("excluded", {})
        assert "text" not in descriptor["widgets"] and "text" not in excluded
        assert "name" not in descriptor["widgets"] and "name" not in excluded


class TestChainingCombine:
    def test_unwired_text_is_byte_identical_to_pre_chaining(self, library_dir: Path) -> None:
        # The backward-compatibility test that matters most: a call shaped
        # exactly like every pre-chaining call site (no text/name kwargs
        # at all) must produce EXACTLY today's output.
        _write_notebook(library_dir, "loras.md", "## A\nfirst\n## B\nsecond\n")
        node = nodes_notebook.LoraLibraryNotebook()
        result = node.read_entry(file="loras.md", entry="A\nB")
        assert result == (["first", "second"], ["A", "B"])

    def test_unwired_single_selection_still_matches_pre_chaining(
        self, library_dir: Path
    ) -> None:
        _write_notebook(library_dir, "loras.md", "## Portrait\nSome prompt text.\n")
        node = nodes_notebook.LoraLibraryNotebook()
        assert node.read_entry(file="loras.md", entry="Portrait") == (
            ["Some prompt text."],
            ["Portrait"],
        )

    def test_incoming_text_comes_first_joined_by_separator(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nentry text\n")
        node = nodes_notebook.LoraLibraryNotebook()
        result = node.read_entry(
            file="loras.md", entry="A", text=["incoming"], separator=", "
        )
        assert result == (["incoming, entry text"], ["A"])

    def test_two_incoming_by_three_entries_is_six_outputs_incoming_major(
        self, library_dir: Path
    ) -> None:
        _write_notebook(
            library_dir, "loras.md", "## A\nfirst\n## B\nsecond\n## C\nthird\n"
        )
        node = nodes_notebook.LoraLibraryNotebook()
        texts, names = node.read_entry(
            file="loras.md", entry="A\nB\nC", text=["one", "two"], separator=", "
        )
        assert texts == [
            "one, first", "one, second", "one, third",
            "two, first", "two, second", "two, third",
        ]
        assert names == ["A", "B", "C", "A", "B", "C"]
        assert len(texts) == len(names) == 6  # NOT 3 -- this is the estimator's own check

    def test_wired_but_empty_text_yields_zero_outputs(self, library_dir: Path) -> None:
        # A real upstream that emitted nothing -- distinct from UNWIRED.
        _write_notebook(library_dir, "loras.md", "## A\nfirst\n")
        node = nodes_notebook.LoraLibraryNotebook()
        assert node.read_entry(file="loras.md", entry="A", text=[]) == ([], [])

    def test_blank_incoming_text_contributes_no_leading_separator(
        self, library_dir: Path
    ) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nentry text\n")
        node = nodes_notebook.LoraLibraryNotebook()
        texts, _names = node.read_entry(
            file="loras.md", entry="A", text=["   "], separator=", "
        )
        assert texts == ["entry text"]

    def test_no_entry_selected_still_raises_even_with_text_wired(
        self, library_dir: Path
    ) -> None:
        # Chaining does not relax the pre-existing "an entry must be
        # selected" contract -- entries are resolved ONCE, up front,
        # regardless of the incoming axis.
        _write_notebook(library_dir, "loras.md", "## A\nx\n")
        node = nodes_notebook.LoraLibraryNotebook()
        with pytest.raises(ValueError, match="no entry selected"):
            node.read_entry(file="loras.md", entry="", text=["incoming"])


class TestChainingNamePairing:
    def test_names_always_join_with_plus_never_separator(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nx\n")
        node = nodes_notebook.LoraLibraryNotebook()
        _texts, names = node.read_entry(
            file="loras.md", entry="A", text=["in"], name=["InName"], separator=" | "
        )
        assert names == ["InName+A"]

    def test_single_incoming_name_broadcasts_across_every_incoming_text(
        self, library_dir: Path
    ) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nx\n")
        node = nodes_notebook.LoraLibraryNotebook()
        _texts, names = node.read_entry(
            file="loras.md", entry="A", text=["a", "b", "c"], name=["Shared"]
        )
        assert names == ["Shared+A", "Shared+A", "Shared+A"]

    def test_n_names_pair_positionally_with_n_incoming_texts(
        self, library_dir: Path
    ) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nx\n## B\ny\n")
        node = nodes_notebook.LoraLibraryNotebook()
        _texts, names = node.read_entry(
            file="loras.md", entry="A\nB", text=["a", "b"], name=["N1", "N2"]
        )
        assert names == ["N1+A", "N1+B", "N2+A", "N2+B"]

    def test_absent_incoming_name_falls_back_to_the_entrys_own_name(
        self, library_dir: Path
    ) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nx\n")
        node = nodes_notebook.LoraLibraryNotebook()
        _texts, names = node.read_entry(file="loras.md", entry="A", text=["a", "b"])
        assert names == ["A", "A"]

    def test_blank_incoming_name_contributes_nothing(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nx\n")
        node = nodes_notebook.LoraLibraryNotebook()
        _texts, names = node.read_entry(
            file="loras.md", entry="A", text=["a"], name=[""]
        )
        assert names == ["A"]

    def test_mismatched_name_length_pairs_what_overlaps_and_warns(
        self, library_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nx\n")
        node = nodes_notebook.LoraLibraryNotebook()
        with caplog.at_level(logging.WARNING, logger="lora_library"):
            _texts, names = node.read_entry(
                file="loras.md", entry="A", text=["a", "b", "c"], name=["OnlyOne", "Two"]
            )
        assert names == ["OnlyOne+A", "Two+A", "A"]
        assert any("EPS Prompt Notebook" in r.message for r in caplog.records)


class TestChainingSeparator:
    def test_default_separator(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nx\n")
        node = nodes_notebook.LoraLibraryNotebook()
        texts, _names = node.read_entry(file="loras.md", entry="A", text=["in"])
        # Owner ask 2026-09-09: the default separator is now a NEWLINE --
        # prompts read better one per line than comma-joined.
        assert texts == ["in\nx"]

    def test_custom_separator(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nx\n")
        node = nodes_notebook.LoraLibraryNotebook()
        texts, _names = node.read_entry(
            file="loras.md", entry="A", text=["in"], separator=" | "
        )
        assert texts == ["in | x"]

    def test_newline_escape_decoded(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nx\n")
        node = nodes_notebook.LoraLibraryNotebook()
        texts, _names = node.read_entry(
            file="loras.md", entry="A", text=["in"], separator="\\n"
        )
        assert texts == ["in\nx"]

    def test_tab_escape_decoded(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nx\n")
        node = nodes_notebook.LoraLibraryNotebook()
        texts, _names = node.read_entry(
            file="loras.md", entry="A", text=["in"], separator="\\t"
        )
        assert texts == ["in\tx"]

    def test_backslash_escape_decoded(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nx\n")
        node = nodes_notebook.LoraLibraryNotebook()
        texts, _names = node.read_entry(
            file="loras.md", entry="A", text=["in"], separator="\\\\"
        )
        assert texts == ["in\\x"]

    def test_unrecognized_escape_passes_through_unchanged(
        self, library_dir: Path
    ) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nx\n")
        node = nodes_notebook.LoraLibraryNotebook()
        texts, _names = node.read_entry(
            file="loras.md", entry="A", text=["in"], separator="\\d"
        )
        assert texts == ["in\\dx"]

    def test_empty_separator_is_plain_concatenation(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nx\n")
        node = nodes_notebook.LoraLibraryNotebook()
        texts, _names = node.read_entry(
            file="loras.md", entry="A", text=["in"], separator=""
        )
        assert texts == ["inx"]


class TestChainingWithDraftsAndPinning:
    def test_draft_still_applies_to_entry_text_when_chaining(
        self, library_dir: Path
    ) -> None:
        _write_notebook(library_dir, "loras.md", "## A\non disk\n")
        node = nodes_notebook.LoraLibraryNotebook()
        drafts = json.dumps({"A": "unsaved edit"})
        texts, _names = node.read_entry(
            file="loras.md", entry="A", drafts=drafts, text=["incoming"], separator=", "
        )
        assert texts == ["incoming, unsaved edit"]

    def test_pinned_entries_still_cross_with_incoming_text(
        self, library_dir: Path
    ) -> None:
        node = nodes_notebook.LoraLibraryNotebook()
        pin = _pin([{"name": "A", "text": "old A"}, {"name": "B", "text": "old B"}])
        texts, names = node.read_entry(
            file="loras.md", entry="ignored", pinned=pin, text=["in1", "in2"], separator=" - "
        )
        assert texts == ["in1 - old A", "in1 - old B", "in2 - old A", "in2 - old B"]
        assert names == ["A", "B", "A", "B"]


class TestChainingInputIsListWrapping:
    """Every widget -- not only the new inputs -- must still read correctly
    now that INPUT_IS_LIST = True wraps all of them (module docstring's
    "Chaining inputs" paragraph)."""

    def test_read_entry_accepts_every_widget_list_wrapped(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nx\n")
        node = nodes_notebook.LoraLibraryNotebook()
        result = node.read_entry(
            file=["loras.md"],
            entry=["A"],
            pinned=[""],
            drafts=["{}"],
            text=["incoming"],
            name=["N"],
            separator=[", "],
        )
        assert result == (["incoming, x"], ["N+A"])

    def test_is_changed_accepts_every_widget_list_wrapped(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nx\n")
        token = nodes_notebook.LoraLibraryNotebook.IS_CHANGED(
            file=["loras.md"],
            entry=["A"],
            pinned=[""],
            drafts=["{}"],
            text=["incoming"],
            name=["N"],
            separator=[", "],
        )
        assert isinstance(token, str)


class TestChainingIsChanged:
    def test_moves_when_incoming_text_changes(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nx\n")
        cls = nodes_notebook.LoraLibraryNotebook
        token_a = cls.IS_CHANGED(file="loras.md", entry="A", text=["one"])
        token_b = cls.IS_CHANGED(file="loras.md", entry="A", text=["two"])
        assert token_a != token_b

    def test_moves_when_incoming_name_changes(self, library_dir: Path) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nx\n")
        cls = nodes_notebook.LoraLibraryNotebook
        token_a = cls.IS_CHANGED(file="loras.md", entry="A", text=["one"], name=["N1"])
        token_b = cls.IS_CHANGED(file="loras.md", entry="A", text=["one"], name=["N2"])
        assert token_a != token_b

    def test_moves_when_separator_changes_while_text_is_wired(
        self, library_dir: Path
    ) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nx\n")
        cls = nodes_notebook.LoraLibraryNotebook
        token_a = cls.IS_CHANGED(file="loras.md", entry="A", text=["one"], separator=", ")
        token_b = cls.IS_CHANGED(file="loras.md", entry="A", text=["one"], separator=" | ")
        assert token_a != token_b

    def test_unwired_token_is_byte_identical_to_pre_chaining(
        self, library_dir: Path, context: LibraryContext
    ) -> None:
        # Backward-compatibility contract: a call shaped exactly like every
        # pre-chaining call site (no text/name kwargs at all) must return
        # the bare pre-chaining token, not a decorated one, so an
        # already-cached run for a never-chained node is never spuriously
        # invalidated.
        _write_notebook(library_dir, "loras.md", "## A\nx\n")
        cls = nodes_notebook.LoraLibraryNotebook
        token = cls.IS_CHANGED(file="loras.md", entry="A")
        expected = nodes_notebook._selection_token(context, "loras.md", "A", "", "{}")
        assert token == expected

    def test_pinned_stays_the_bare_constant_when_chaining_is_unwired(
        self, library_dir: Path
    ) -> None:
        _write_notebook(library_dir, "loras.md", "## A\nx\n")
        cls = nodes_notebook.LoraLibraryNotebook
        pin = _pin([{"name": "A", "text": "x"}])
        assert cls.IS_CHANGED(file="loras.md", entry="A", pinned=pin) == "pinned"

    def test_pinned_still_moves_with_a_wired_incoming_text(self, library_dir: Path) -> None:
        # A pin freezes the resolved ENTRIES, not the cross against an
        # incoming value (read_entry still crosses them) -- so IS_CHANGED
        # must move too, or a chained change under a pin would serve a
        # stale cached run.
        _write_notebook(library_dir, "loras.md", "## A\nx\n")
        cls = nodes_notebook.LoraLibraryNotebook
        pin = _pin([{"name": "A", "text": "x"}])
        token_a = cls.IS_CHANGED(file="loras.md", entry="A", pinned=pin, text=["one"])
        token_b = cls.IS_CHANGED(file="loras.md", entry="A", pinned=pin, text=["two"])
        assert token_a != token_b
        assert token_a != "pinned"
