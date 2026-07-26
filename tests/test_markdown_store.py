"""Tests for lora_library.markdown_store (FORMAT.md §3).

Parsing/serialization/mutation are pure functions tested with plain strings;
``load_notebook``/``save_notebook`` are the only pieces that touch a
filesystem, exercised here with pytest's own ``tmp_path`` (markdown_store
takes a raw ``Path``, not a ``LibraryContext`` — that bridge is
``context.resolve_notebook_file``, tested at the route layer).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lora_library import markdown_store as ms
from lora_library.context import LibraryContext

# ------------------------------------------------------------------- parsing


class TestParseStructure:
    def test_simple_entry_with_no_category(self) -> None:
        parsed = ms.parse("## Entry\nBody text.\n")
        assert ms.list_entries(parsed) == [{"name": "Entry", "category": ""}]
        assert ms.get_entry(parsed, "Entry")["text"] == "Body text."

    def test_entry_under_a_category(self) -> None:
        parsed = ms.parse("# Category A\n\n## Entry\nBody.\n")
        assert ms.list_entries(parsed) == [{"name": "Entry", "category": "Category A"}]

    def test_entries_before_first_h1_have_empty_category(self) -> None:
        parsed = ms.parse("## First\nA\n# Cat\n## Second\nB\n")
        entries = ms.list_entries(parsed)
        assert entries == [
            {"name": "First", "category": ""},
            {"name": "Second", "category": "Cat"},
        ]

    def test_h3_and_deeper_headings_belong_to_the_body(self) -> None:
        parsed = ms.parse("## Entry\n### Subheading\nBody under it.\n")
        assert ms.get_entry(parsed, "Entry")["text"] == "### Subheading\nBody under it."

    def test_fenced_block_containing_a_fake_h2_is_body_not_a_boundary(self) -> None:
        text = (
            "## Real Entry\n"
            "Here is code:\n"
            "```\n"
            "## fake\n"
            "```\n"
            "More text after fence.\n"
        )
        parsed = ms.parse(text)
        assert ms.list_entries(parsed) == [{"name": "Real Entry", "category": ""}]
        assert not parsed.problems
        assert ms.get_entry(parsed, "Real Entry")["text"] == (
            "Here is code:\n```\n## fake\n```\nMore text after fence."
        )

    def test_fenced_block_containing_a_fake_h1_is_also_body(self) -> None:
        text = "## Entry\n```\n# fake category\n```\nafter\n"
        parsed = ms.parse(text)
        assert ms.list_entries(parsed) == [{"name": "Entry", "category": ""}]

    def test_interior_blank_lines_are_preserved(self) -> None:
        parsed = ms.parse("## Entry\nLine1\n\nLine2\n")
        assert ms.get_entry(parsed, "Entry")["text"] == "Line1\n\nLine2"

    def test_empty_file_has_no_entries_and_no_problems(self) -> None:
        parsed = ms.parse("")
        assert ms.list_entries(parsed) == []
        assert parsed.problems == []

    def test_preamble_only_file_has_no_entries(self) -> None:
        parsed = ms.parse("Just some notes, no headings at all.\n")
        assert ms.list_entries(parsed) == []


class TestNames:
    def test_duplicate_name_first_wins_second_is_a_problem(self) -> None:
        parsed = ms.parse("## Foo\nBody1\n\n## Foo\nBody2\n")
        assert ms.list_entries(parsed) == [{"name": "Foo", "category": ""}]
        assert ms.get_entry(parsed, "Foo")["text"] == "Body1"
        assert len(parsed.problems) == 1
        assert "Foo" in parsed.problems[0]

    def test_three_way_duplicate_reports_two_problems(self) -> None:
        parsed = ms.parse("## Foo\nA\n## Foo\nB\n## Foo\nC\n")
        assert ms.list_entries(parsed) == [{"name": "Foo", "category": ""}]
        assert len(parsed.problems) == 2

    def test_empty_heading_is_a_problem_and_not_listed(self) -> None:
        parsed = ms.parse("##  \nOrphan body.\n## Real\nBody.\n")
        assert ms.list_entries(parsed) == [{"name": "Real", "category": ""}]
        assert len(parsed.problems) == 1
        assert "empty" in parsed.problems[0].lower()

    def test_names_are_compared_case_sensitively(self) -> None:
        parsed = ms.parse("## Foo\nA\n## foo\nB\n")
        assert ms.list_entries(parsed) == [
            {"name": "Foo", "category": ""},
            {"name": "foo", "category": ""},
        ]
        assert parsed.problems == []

    def test_unicode_name_and_body_round_trip(self) -> None:
        parsed = ms.parse("## Café ☕\nLine with ünïcode and 日本語.\n")
        entries = ms.list_entries(parsed)
        assert entries == [{"name": "Café ☕", "category": ""}]
        assert ms.get_entry(parsed, "Café ☕")["text"] == (
            "Line with ünïcode and 日本語."
        )


class TestEntryTextTrimming:
    def test_leading_and_trailing_blank_lines_are_stripped(self) -> None:
        parsed = ms.parse("## Entry\n\n\nBody.\n\n\n## Next\nX\n")
        assert ms.get_entry(parsed, "Entry")["text"] == "Body."

    def test_all_blank_body_becomes_empty_string(self) -> None:
        parsed = ms.parse("## Entry\n\n\n## Next\nX\n")
        assert ms.get_entry(parsed, "Entry")["text"] == ""

    def test_no_body_at_all_becomes_empty_string(self) -> None:
        parsed = ms.parse("## Entry\n## Next\nX\n")
        assert ms.get_entry(parsed, "Entry")["text"] == ""


# --------------------------------------------------------- write-time guard


class TestUnrepresentableHeadingLine:
    def test_h1_line_in_text_is_flagged(self) -> None:
        assert ms.find_unrepresentable_heading_line("ok\n# looks like h1\nmore") == 2

    def test_h2_line_in_text_is_flagged(self) -> None:
        assert ms.find_unrepresentable_heading_line("## looks like h2\n") == 1

    def test_bare_hash_with_no_space_is_flagged(self) -> None:
        assert ms.find_unrepresentable_heading_line("#") == 1

    def test_hash_with_no_following_space_is_allowed(self) -> None:
        # Not a heading by this grammar's rule (needs "# " or a bare "#").
        assert ms.find_unrepresentable_heading_line("#no-space-here") is None

    def test_h3_line_is_allowed(self) -> None:
        assert ms.find_unrepresentable_heading_line("### still just body") is None

    def test_heading_line_inside_a_fence_is_allowed(self) -> None:
        text = "```\n## fake\n# also fake\n```\n"
        assert ms.find_unrepresentable_heading_line(text) is None

    def test_safe_text_returns_none(self) -> None:
        assert ms.find_unrepresentable_heading_line("just prose\nmore prose\n") is None


# ------------------------------------------------------------------ mutation


class TestUpsertCreate:
    def test_create_into_empty_file_with_no_category(self) -> None:
        parsed = ms.parse("")
        result = ms.upsert_entry(parsed, "New Entry", "hello")
        assert result == {"name": "New Entry", "category": ""}
        assert ms.list_entries(parsed) == [{"name": "New Entry", "category": ""}]
        assert ms.get_entry(parsed, "New Entry")["text"] == "hello"

    def test_create_with_no_category_appends_to_end_of_file_whatever_that_is(self) -> None:
        # FORMAT.md §3.4: omitting `category` means "end of the file", not
        # "the top-level (uncategorized) section" — if the file's last block
        # happens to be a named category, that's where a bare create lands.
        parsed = ms.parse("# Cat A\n## E1\nB1\n")
        ms.upsert_entry(parsed, "E2", "B2")
        assert ms.list_entries(parsed) == [
            {"name": "E1", "category": "Cat A"},
            {"name": "E2", "category": "Cat A"},
        ]

    def test_create_with_new_category_appends_heading_at_end_of_file(self) -> None:
        parsed = ms.parse("## Existing\nBody\n")
        ms.upsert_entry(parsed, "New Entry", "text2", category="Cat A")
        lines = ms.serialize(parsed)
        assert lines == ["## Existing", "Body", "# Cat A", "## New Entry", "text2"]
        assert ms.list_entries(parsed) == [
            {"name": "Existing", "category": ""},
            {"name": "New Entry", "category": "Cat A"},
        ]

    def test_create_into_existing_category_does_not_duplicate_heading(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n")
        ms.upsert_entry(parsed, "E2", "B2", category="Cat A")
        lines = ms.serialize(parsed)
        assert lines == ["# Cat A", "## E1", "B1", "## E2", "B2"]
        assert lines.count("# Cat A") == 1

    def test_create_into_a_repeated_category_name_lands_in_the_last_one(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n# Cat B\n## E2\nB2\n# Cat A\n## E3\nB3\n")
        ms.upsert_entry(parsed, "E4", "B4", category="Cat A")
        lines = ms.serialize(parsed)
        assert lines[-4:] == ["## E3", "B3", "## E4", "B4"]
        assert lines.count("# Cat A") == 2

    def test_create_with_blank_name_raises(self) -> None:
        parsed = ms.parse("")
        with pytest.raises(ms.InvalidEntryNameError):
            ms.upsert_entry(parsed, "   ", "text")

    def test_create_rejects_text_with_h1_line(self) -> None:
        parsed = ms.parse("")
        with pytest.raises(ms.InvalidEntryTextError):
            ms.upsert_entry(parsed, "Name", "line one\n# oops\n")

    def test_create_rejects_text_with_h2_line(self) -> None:
        parsed = ms.parse("")
        with pytest.raises(ms.InvalidEntryTextError):
            ms.upsert_entry(parsed, "Name", "## oops")

    def test_create_allows_heading_line_inside_a_fence_in_the_text(self) -> None:
        parsed = ms.parse("")
        ms.upsert_entry(parsed, "Name", "```\n## fake\n```")
        assert ms.get_entry(parsed, "Name")["text"] == "```\n## fake\n```"

    def test_rejected_create_leaves_the_notebook_unmodified(self) -> None:
        parsed = ms.parse("## Existing\nBody\n")
        with pytest.raises(ms.InvalidEntryTextError):
            ms.upsert_entry(parsed, "New", "# oops")
        assert ms.list_entries(parsed) == [{"name": "Existing", "category": ""}]


class TestUpsertCreateAfter:
    def test_create_after_inserts_immediately_below_the_named_entry(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n## E2\nB2\n## E3\nB3\n")
        ms.upsert_entry(parsed, "New", "new body", after="E1")
        assert ms.list_entries(parsed) == [
            {"name": "E1", "category": "Cat A"},
            {"name": "New", "category": "Cat A"},
            {"name": "E2", "category": "Cat A"},
            {"name": "E3", "category": "Cat A"},
        ]
        assert ms.get_entry(parsed, "New")["text"] == "new body"

    def test_create_after_the_last_entry_lands_at_the_category_boundary(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n# Cat B\n## E2\nB2\n")
        ms.upsert_entry(parsed, "New", "n", after="E1")
        assert ms.serialize(parsed) == [
            "# Cat A",
            "## E1",
            "B1",
            "## New",
            "n",
            "# Cat B",
            "## E2",
            "B2",
        ]
        assert ms.list_entries(parsed) == [
            {"name": "E1", "category": "Cat A"},
            {"name": "New", "category": "Cat A"},
            {"name": "E2", "category": "Cat B"},
        ]

    def test_create_after_unknown_name_falls_back_to_append(self) -> None:
        parsed = ms.parse("## E1\nB1\n")
        ms.upsert_entry(parsed, "New", "n", after="does-not-exist")
        assert ms.list_entries(parsed) == [
            {"name": "E1", "category": ""},
            {"name": "New", "category": ""},
        ]

    def test_create_after_omitted_falls_back_to_append(self) -> None:
        parsed = ms.parse("## E1\nB1\n")
        ms.upsert_entry(parsed, "New", "n")
        assert ms.list_entries(parsed) == [
            {"name": "E1", "category": ""},
            {"name": "New", "category": ""},
        ]

    def test_create_after_takes_priority_over_category(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n# Cat B\n## E2\nB2\n")
        ms.upsert_entry(parsed, "New", "n", category="Cat B", after="E1")
        # `after` resolved, so `category` is never consulted: New lands
        # next to E1 in Cat A, not appended to Cat B.
        assert ms.list_entries(parsed) == [
            {"name": "E1", "category": "Cat A"},
            {"name": "New", "category": "Cat A"},
            {"name": "E2", "category": "Cat B"},
        ]

    def test_create_after_is_ignored_on_update(self) -> None:
        parsed = ms.parse("## E1\nold\n## E2\nB2\n")
        ms.upsert_entry(parsed, "E1", "new body", after="E2")
        # E1 already existed: this is an update, so `after` never applies —
        # E1 keeps its position and E2 is untouched.
        assert ms.list_entries(parsed) == [
            {"name": "E1", "category": ""},
            {"name": "E2", "category": ""},
        ]
        assert ms.get_entry(parsed, "E1")["text"] == "new body"


class TestUpsertUpdate:
    def test_update_replaces_body_in_place(self) -> None:
        parsed = ms.parse("## E1\nold\n## E2\nother\n")
        ms.upsert_entry(parsed, "E1", "new body")
        assert ms.get_entry(parsed, "E1")["text"] == "new body"
        assert ms.list_entries(parsed) == [
            {"name": "E1", "category": ""},
            {"name": "E2", "category": ""},
        ]

    def test_update_ignores_category_the_entry_keeps_its_position(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nold\n")
        ms.upsert_entry(parsed, "E1", "new", category="Cat B")
        assert ms.list_entries(parsed) == [{"name": "E1", "category": "Cat A"}]
        assert ms.serialize(parsed) == ["# Cat A", "## E1", "new"]

    def test_update_rejects_text_with_heading_line_and_leaves_body_untouched(self) -> None:
        parsed = ms.parse("## E1\noriginal\n")
        with pytest.raises(ms.InvalidEntryTextError):
            ms.upsert_entry(parsed, "E1", "# oops")
        assert ms.get_entry(parsed, "E1")["text"] == "original"


class TestUpsertRename:
    def test_rename_changes_heading_and_body_keeps_position(self) -> None:
        parsed = ms.parse("# Cat A\n## Old Name\nBody\n")
        result = ms.upsert_entry(parsed, "Old Name", "new body", rename_to="New Name")
        assert result == {"name": "New Name", "category": "Cat A"}
        assert ms.get_entry(parsed, "Old Name") is None
        assert ms.get_entry(parsed, "New Name")["text"] == "new body"
        assert ms.serialize(parsed) == ["# Cat A", "## New Name", "new body"]

    def test_rename_to_its_own_current_name_is_a_plain_update(self) -> None:
        parsed = ms.parse("## Name\nold\n")
        ms.upsert_entry(parsed, "Name", "new", rename_to="Name")
        assert ms.get_entry(parsed, "Name")["text"] == "new"

    def test_rename_to_an_existing_different_name_raises_collision(self) -> None:
        parsed = ms.parse("## A\nbodyA\n## B\nbodyB\n")
        with pytest.raises(ms.NameCollisionError):
            ms.upsert_entry(parsed, "A", "new", rename_to="B")
        # Unchanged after the failed rename.
        assert ms.get_entry(parsed, "A")["text"] == "bodyA"
        assert ms.get_entry(parsed, "B")["text"] == "bodyB"


class TestRemoveEntry:
    def test_remove_existing_entry_returns_true(self) -> None:
        parsed = ms.parse("## A\nx\n## B\ny\n")
        assert ms.remove_entry(parsed, "A") is True
        assert ms.list_entries(parsed) == [{"name": "B", "category": ""}]

    def test_remove_missing_entry_returns_false(self) -> None:
        parsed = ms.parse("## A\nx\n")
        assert ms.remove_entry(parsed, "does-not-exist") is False
        assert ms.list_entries(parsed) == [{"name": "A", "category": ""}]

    def test_delete_last_entry_in_a_category_keeps_the_heading(self) -> None:
        parsed = ms.parse("# Cat A\n## Only\nBody\n")
        assert ms.remove_entry(parsed, "Only") is True
        assert ms.serialize(parsed) == ["# Cat A"]
        assert ms.list_entries(parsed) == []

    def test_delete_one_of_two_in_a_category_keeps_the_other(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n## E2\nB2\n")
        ms.remove_entry(parsed, "E1")
        assert ms.serialize(parsed) == ["# Cat A", "## E2", "B2"]


# ---------------------------------------------------------------- categories


class TestListCategories:
    def test_empty_file_has_no_categories(self) -> None:
        assert ms.list_categories(ms.parse("")) == []

    def test_entries_with_no_h1_have_no_categories(self) -> None:
        assert ms.list_categories(ms.parse("## E1\nB1\n")) == []

    def test_lists_named_categories_in_file_order(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n# Cat B\n## E2\nB2\n")
        assert ms.list_categories(parsed) == ["Cat A", "Cat B"]

    def test_includes_a_category_with_zero_entries(self) -> None:
        parsed = ms.parse("# Empty Cat\n# Cat A\n## E1\nB1\n")
        assert ms.list_categories(parsed) == ["Empty Cat", "Cat A"]
        assert ms.list_entries(parsed) == [{"name": "E1", "category": "Cat A"}]

    def test_a_trailing_empty_category_is_included(self) -> None:
        parsed = ms.parse("## E1\nB1\n# Trailing Empty\n")
        assert ms.list_categories(parsed) == ["Trailing Empty"]

    def test_duplicate_category_name_is_reported_once_per_occurrence(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n# Cat B\n## E2\nB2\n# Cat A\n## E3\nB3\n")
        assert ms.list_categories(parsed) == ["Cat A", "Cat B", "Cat A"]


class TestGetCategoryDescription:
    def test_missing_category_is_none(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n")
        assert ms.get_category_description(parsed, "Nope") is None

    def test_blank_name_is_none(self) -> None:
        parsed = ms.parse("## E1\nB1\n")
        assert ms.get_category_description(parsed, "") is None
        assert ms.get_category_description(parsed, "   ") is None

    def test_category_with_no_description_is_empty_string(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n")
        assert ms.get_category_description(parsed, "Cat A") == ""

    def test_category_with_a_description_returns_it_trimmed(self) -> None:
        parsed = ms.parse("# Cat A\n\nSome prose about this category.\n\n## E1\nB1\n")
        assert ms.get_category_description(parsed, "Cat A") == "Some prose about this category."

    def test_multiline_description_preserves_interior_blank_lines(self) -> None:
        parsed = ms.parse("# Cat A\nLine1\n\nLine2\n## E1\nB1\n")
        assert ms.get_category_description(parsed, "Cat A") == "Line1\n\nLine2"

    def test_empty_category_with_description_and_no_entries(self) -> None:
        parsed = ms.parse("# Cat A\nJust description, no entries at all.\n")
        assert ms.get_category_description(parsed, "Cat A") == (
            "Just description, no entries at all."
        )
        assert ms.list_entries(parsed) == []

    def test_repeated_name_targets_the_last_block(self) -> None:
        parsed = ms.parse("# Cat A\nFirst.\n## E1\nB1\n# Cat A\nSecond.\n## E2\nB2\n")
        assert ms.get_category_description(parsed, "Cat A") == "Second."


class TestCreateCategory:
    def test_create_into_empty_file_with_no_description(self) -> None:
        parsed = ms.parse("")
        result = ms.create_category(parsed, "Styles")
        assert result == {"name": "Styles", "description": ""}
        assert ms.list_categories(parsed) == ["Styles"]
        assert ms.get_category_description(parsed, "Styles") == ""
        assert ms.serialize(parsed) == ["# Styles"]

    def test_create_with_a_description(self) -> None:
        parsed = ms.parse("")
        result = ms.create_category(parsed, "Styles", "Prose about styles.")
        assert result == {"name": "Styles", "description": "Prose about styles."}
        assert ms.get_category_description(parsed, "Styles") == "Prose about styles."
        assert ms.serialize(parsed) == ["# Styles", "Prose about styles."]

    def test_create_appends_at_eof_after_existing_content_byte_safely(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n")
        ms.create_category(parsed, "Cat B", "New category.")
        assert ms.serialize(parsed) == ["# Cat A", "## E1", "B1", "# Cat B", "New category."]
        # Existing entry is untouched.
        assert ms.get_entry(parsed, "E1")["text"] == "B1"

    def test_create_strips_the_name(self) -> None:
        parsed = ms.parse("")
        ms.create_category(parsed, "  Styles  ")
        assert ms.list_categories(parsed) == ["Styles"]

    def test_create_duplicate_name_raises_and_leaves_notebook_unmodified(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n")
        with pytest.raises(ms.NameCollisionError):
            ms.create_category(parsed, "Cat A")
        assert ms.list_categories(parsed) == ["Cat A"]
        assert ms.serialize(parsed) == ["# Cat A", "## E1", "B1"]

    def test_create_blank_name_raises(self) -> None:
        parsed = ms.parse("")
        with pytest.raises(ms.InvalidEntryNameError):
            ms.create_category(parsed, "   ")

    def test_create_name_with_newline_raises(self) -> None:
        parsed = ms.parse("")
        with pytest.raises(ms.InvalidEntryNameError):
            ms.create_category(parsed, "Cat\nA")

    def test_create_rejects_description_with_heading_line(self) -> None:
        parsed = ms.parse("")
        with pytest.raises(ms.InvalidEntryTextError):
            ms.create_category(parsed, "Styles", "line one\n## oops")
        assert ms.list_categories(parsed) == []


class TestCreateCategoryAfter:
    def test_after_a_category_name_inserts_the_new_block_right_after_it(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n# Cat B\n## E2\nB2\n")
        ms.create_category(parsed, "New Cat", "desc", after="Cat A")
        assert ms.serialize(parsed) == [
            "# Cat A",
            "## E1",
            "B1",
            "# New Cat",
            "desc",
            "# Cat B",
            "## E2",
            "B2",
        ]
        assert ms.list_categories(parsed) == ["Cat A", "New Cat", "Cat B"]

    def test_after_an_entry_name_splits_the_block_and_reparents_the_remainder(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n## E2\nB2\n## E3\nB3\n")
        ms.create_category(parsed, "New Cat", "desc", after="E1")
        assert ms.serialize(parsed) == [
            "# Cat A",
            "## E1",
            "B1",
            "# New Cat",
            "desc",
            "## E2",
            "B2",
            "## E3",
            "B3",
        ]
        assert ms.list_entries(parsed) == [
            {"name": "E1", "category": "Cat A"},
            {"name": "E2", "category": "New Cat"},
            {"name": "E3", "category": "New Cat"},
        ]
        # The reparented entries' bodies travelled byte-identically.
        assert ms.get_entry(parsed, "E2")["text"] == "B2"
        assert ms.get_entry(parsed, "E3")["text"] == "B3"

    def test_after_the_last_entry_in_a_block_has_no_remainder_to_split(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n")
        ms.create_category(parsed, "New Cat", after="E1")
        assert ms.serialize(parsed) == ["# Cat A", "## E1", "B1", "# New Cat"]
        assert ms.list_categories(parsed) == ["Cat A", "New Cat"]

    def test_after_unknown_falls_back_to_end_of_file(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n")
        ms.create_category(parsed, "New Cat", after="does-not-exist")
        assert ms.serialize(parsed) == ["# Cat A", "## E1", "B1", "# New Cat"]

    def test_after_prefers_a_category_match_over_a_same_named_entry(self) -> None:
        # "Dup" names both a category and an (unrelated) entry — different
        # namespaces — so this also documents the tie-break: the category
        # match wins, checked before any entry lookup is attempted.
        parsed = ms.parse("# Dup\n## E1\nB1\n# Cat B\n## Dup\nB2\n")
        ms.create_category(parsed, "New Cat", after="Dup")
        assert ms.serialize(parsed) == [
            "# Dup",
            "## E1",
            "B1",
            "# New Cat",
            "# Cat B",
            "## Dup",
            "B2",
        ]


class TestSetCategoryDescription:
    def test_replace_description_on_a_bare_heading(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n")
        result = ms.set_category_description(parsed, "Cat A", "New prose.")
        assert result == {"name": "Cat A", "description": "New prose."}
        assert ms.get_category_description(parsed, "Cat A") == "New prose."
        assert ms.list_entries(parsed) == [{"name": "E1", "category": "Cat A"}]

    def test_replace_an_existing_description(self) -> None:
        parsed = ms.parse("# Cat A\nOld prose.\n## E1\nB1\n")
        ms.set_category_description(parsed, "Cat A", "Updated prose.")
        assert ms.get_category_description(parsed, "Cat A") == "Updated prose."

    def test_clear_a_description_back_to_empty(self) -> None:
        parsed = ms.parse("# Cat A\nOld prose.\n## E1\nB1\n")
        ms.set_category_description(parsed, "Cat A", "")
        assert ms.get_category_description(parsed, "Cat A") == ""

    def test_replace_preserves_surrounding_blocks_byte_identically(self) -> None:
        text = "Preamble.\n\n## Head1\nH1body\n# Cat A\nOld.\n## E1\nB1\n# Cat B\n## E2\nB2\n"
        parsed = ms.parse(text)
        ms.set_category_description(parsed, "Cat A", "New.")
        assert ms.serialize(parsed) == [
            "Preamble.",
            "",
            "## Head1",
            "H1body",
            "# Cat A",
            "New.",
            "## E1",
            "B1",
            "# Cat B",
            "## E2",
            "B2",
        ]

    def test_repeated_name_replaces_the_last_block_only(self) -> None:
        parsed = ms.parse("# Cat A\nFirst.\n## E1\nB1\n# Cat A\nSecond.\n## E2\nB2\n")
        ms.set_category_description(parsed, "Cat A", "Replaced.")
        assert ms.serialize(parsed) == [
            "# Cat A",
            "First.",
            "## E1",
            "B1",
            "# Cat A",
            "Replaced.",
            "## E2",
            "B2",
        ]

    def test_missing_category_raises_category_not_found(self) -> None:
        parsed = ms.parse("## E1\nB1\n")
        with pytest.raises(ms.CategoryNotFoundError):
            ms.set_category_description(parsed, "Nope", "text")

    def test_blank_name_raises_category_not_found(self) -> None:
        parsed = ms.parse("## E1\nB1\n")
        with pytest.raises(ms.CategoryNotFoundError):
            ms.set_category_description(parsed, "", "text")

    def test_rejects_description_with_heading_line_and_leaves_it_untouched(self) -> None:
        parsed = ms.parse("# Cat A\nOriginal.\n## E1\nB1\n")
        with pytest.raises(ms.InvalidEntryTextError):
            ms.set_category_description(parsed, "Cat A", "# oops")
        assert ms.get_category_description(parsed, "Cat A") == "Original."

    def test_set_description_on_crlf_file_round_trips_and_stays_crlf(self, tmp_path: Path) -> None:
        path = tmp_path / "loras.md"
        raw = "# Cat A\r\nOld.\r\n## E1\r\nB1\r\n# Cat B\r\n## E2\r\nB2\r\n"
        path.write_bytes(raw.encode("utf-8"))

        parsed, _mtime, line_ending = ms.load_notebook(path)
        assert line_ending == "\r\n"
        ms.set_category_description(parsed, "Cat A", "New.")
        ms.save_notebook(path, parsed, line_ending)

        with open(path, encoding="utf-8", newline="") as fh:
            rewritten = fh.read()
        assert rewritten.count("\n") == rewritten.count("\r\n")  # every \n is part of \r\n
        assert "# Cat A\r\nNew.\r\n## E1\r\nB1\r\n" in rewritten
        # The untouched sibling category + entry are still present byte-for-byte.
        assert "# Cat B\r\n## E2\r\nB2\r\n" in rewritten


class TestSetCategoryName:
    def test_rename_changes_the_heading_and_keeps_entries_intact(self) -> None:
        parsed = ms.parse("# Cat A\nSome prose.\n## E1\nB1\n## E2\nB2\n")
        result = ms.set_category_name(parsed, "Cat A", "Cat A2")
        assert result == {"name": "Cat A2", "description": "Some prose."}
        assert ms.list_categories(parsed) == ["Cat A2"]
        assert ms.list_entries(parsed) == [
            {"name": "E1", "category": "Cat A2"},
            {"name": "E2", "category": "Cat A2"},
        ]
        assert ms.get_entry(parsed, "E1")["text"] == "B1"
        assert ms.get_entry(parsed, "E2")["text"] == "B2"
        assert ms.serialize(parsed) == [
            "# Cat A2",
            "Some prose.",
            "## E1",
            "B1",
            "## E2",
            "B2",
        ]

    def test_rename_to_a_duplicate_name_raises_and_leaves_both_unchanged(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n# Cat B\n## E2\nB2\n")
        with pytest.raises(ms.NameCollisionError):
            ms.set_category_name(parsed, "Cat A", "Cat B")
        assert ms.list_categories(parsed) == ["Cat A", "Cat B"]
        assert ms.list_entries(parsed) == [
            {"name": "E1", "category": "Cat A"},
            {"name": "E2", "category": "Cat B"},
        ]

    def test_rename_to_its_own_current_name_is_a_noop(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n")
        ms.set_category_name(parsed, "Cat A", "Cat A")
        assert ms.list_categories(parsed) == ["Cat A"]

    def test_rename_unknown_category_raises_not_found(self) -> None:
        parsed = ms.parse("## E1\nB1\n")
        with pytest.raises(ms.CategoryNotFoundError):
            ms.set_category_name(parsed, "Nope", "New Name")

    def test_rename_blank_new_name_raises(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n")
        with pytest.raises(ms.InvalidEntryNameError):
            ms.set_category_name(parsed, "Cat A", "   ")

    def test_rename_new_name_with_newline_raises(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n")
        with pytest.raises(ms.InvalidEntryNameError):
            ms.set_category_name(parsed, "Cat A", "Cat\nB")

    def test_rename_repeated_name_targets_the_last_block(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n# Cat A\n## E2\nB2\n")
        ms.set_category_name(parsed, "Cat A", "Renamed")
        assert ms.serialize(parsed) == [
            "# Cat A",
            "## E1",
            "B1",
            "# Renamed",
            "## E2",
            "B2",
        ]


class TestMoveEntry:
    def test_move_before_an_earlier_sibling_within_a_category(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n## E2\nB2\n## E3\nB3\n")
        ms.move_entry(parsed, "E3", before="E1")
        assert ms.list_entries(parsed) == [
            {"name": "E3", "category": "Cat A"},
            {"name": "E1", "category": "Cat A"},
            {"name": "E2", "category": "Cat A"},
        ]

    def test_move_before_a_later_sibling_within_a_category(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n## E2\nB2\n## E3\nB3\n")
        ms.move_entry(parsed, "E1", before="E3")
        assert ms.list_entries(parsed) == [
            {"name": "E2", "category": "Cat A"},
            {"name": "E1", "category": "Cat A"},
            {"name": "E3", "category": "Cat A"},
        ]

    def test_move_across_categories_membership_follows_position(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n# Cat B\n## E2\nB2\n")
        result = ms.move_entry(parsed, "E1", before="E2")
        assert result == {"name": "E1", "category": "Cat B"}
        assert ms.list_entries(parsed) == [
            {"name": "E1", "category": "Cat B"},
            {"name": "E2", "category": "Cat B"},
        ]

    def test_move_across_categories_body_travels_byte_identically(self) -> None:
        text = "# Cat A\n## E1\nLine one.\n\nLine two, blank above.\n# Cat B\n## E2\nB2\n"
        parsed = ms.parse(text)
        original_text = ms.get_entry(parsed, "E1")["text"]
        ms.move_entry(parsed, "E1", before="E2")
        assert ms.get_entry(parsed, "E1")["text"] == original_text
        assert ms.list_entries(parsed) == [
            {"name": "E1", "category": "Cat B"},
            {"name": "E2", "category": "Cat B"},
        ]
        assert ms.serialize(parsed) == [
            "# Cat A",
            "# Cat B",
            "## E1",
            "Line one.",
            "",
            "Line two, blank above.",
            "## E2",
            "B2",
        ]

    def test_move_to_a_new_category_creates_heading_at_end_of_file(self) -> None:
        parsed = ms.parse("## E1\nB1\n")
        result = ms.move_entry(parsed, "E1", category="Brand New")
        assert result == {"name": "E1", "category": "Brand New"}
        assert ms.serialize(parsed) == ["# Brand New", "## E1", "B1"]

    def test_move_to_an_existing_category_lands_at_its_end(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n## E2\nB2\n# Cat B\n## E3\nB3\n")
        ms.move_entry(parsed, "E3", category="Cat A")
        assert ms.serialize(parsed) == [
            "# Cat A",
            "## E1",
            "B1",
            "## E2",
            "B2",
            "## E3",
            "B3",
            "# Cat B",
        ]

    def test_move_by_category_into_a_repeated_name_lands_in_the_last_one(self) -> None:
        parsed = ms.parse(
            "# Cat A\n## E1\nB1\n# Cat B\n## E2\nB2\n# Cat A\n## E3\nB3\n## E4\nB4\n"
        )
        ms.move_entry(parsed, "E1", category="Cat A")
        lines = ms.serialize(parsed)
        assert lines[-2:] == ["## E1", "B1"]
        assert lines.count("# Cat A") == 2

    def test_move_category_empty_string_lands_just_before_first_h1(self) -> None:
        parsed = ms.parse("## Head1\nH1body\n# Cat A\n## E1\nB1\n")
        ms.move_entry(parsed, "E1", category="")
        assert ms.list_entries(parsed) == [
            {"name": "Head1", "category": ""},
            {"name": "E1", "category": ""},
        ]
        assert ms.serialize(parsed) == [
            "## Head1",
            "H1body",
            "## E1",
            "B1",
            "# Cat A",
        ]

    def test_move_category_empty_string_with_no_head_region_lands_at_file_start(self) -> None:
        # No entries currently have category "" — the file's first heading
        # is already an H1 — so the moved entry becomes the new head.
        parsed = ms.parse("# Cat A\n## E1\nB1\n## E2\nB2\n")
        ms.move_entry(parsed, "E2", category="")
        assert ms.list_entries(parsed) == [
            {"name": "E2", "category": ""},
            {"name": "E1", "category": "Cat A"},
        ]
        assert ms.serialize(parsed) == ["## E2", "B2", "# Cat A", "## E1", "B1"]

    def test_move_category_empty_string_with_no_h1_anywhere_is_end_of_file(self) -> None:
        # The whole file is already the "" category, so "end of the head
        # region" and "end of file" are the same place.
        parsed = ms.parse("## E1\nB1\n## E2\nB2\n")
        ms.move_entry(parsed, "E1", category="")
        assert ms.serialize(parsed) == ["## E2", "B2", "## E1", "B1"]

    def test_move_before_self_is_a_documented_noop(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n## E2\nB2\n")
        result = ms.move_entry(parsed, "E1", before="E1")
        assert result == {"name": "E1", "category": "Cat A"}
        assert ms.serialize(parsed) == ["# Cat A", "## E1", "B1", "## E2", "B2"]

    def test_move_unknown_name_raises_entry_not_found(self) -> None:
        parsed = ms.parse("## E1\nB1\n")
        with pytest.raises(ms.EntryNotFoundError):
            ms.move_entry(parsed, "does-not-exist", category="")

    def test_move_unknown_before_raises_entry_not_found(self) -> None:
        parsed = ms.parse("## E1\nB1\n")
        with pytest.raises(ms.EntryNotFoundError):
            ms.move_entry(parsed, "E1", before="does-not-exist")

    def test_move_requires_exactly_one_of_before_or_category_neither(self) -> None:
        parsed = ms.parse("## E1\nB1\n")
        with pytest.raises(ValueError):
            ms.move_entry(parsed, "E1")

    def test_move_requires_exactly_one_of_before_or_category_both(self) -> None:
        parsed = ms.parse("## E1\nB1\n## E2\nB2\n")
        with pytest.raises(ValueError):
            ms.move_entry(parsed, "E1", before="E2", category="Cat A")

    def test_move_leaves_untouched_entries_and_preamble_byte_identical(self) -> None:
        text = (
            "Preamble line.\n\n"
            "# Cat A\n## Keep Me\nUntouched.\nTwo lines.\n"
            "## E1\nB1\n# Cat B\n## E2\nB2\n"
        )
        parsed = ms.parse(text)
        ms.move_entry(parsed, "E1", before="E2")
        assert ms.get_entry(parsed, "Keep Me")["text"] == "Untouched.\nTwo lines."
        assert ms.serialize(parsed)[:4] == [
            "Preamble line.",
            "",
            "# Cat A",
            "## Keep Me",
        ]


class TestMoveCategory:
    def test_move_before_another_category(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n# Cat B\n## E2\nB2\n# Cat C\n## E3\nB3\n")
        result = ms.move_category(parsed, "Cat C", before="Cat A")
        assert result == {"name": "Cat C"}
        assert ms.list_categories(parsed) == ["Cat C", "Cat A", "Cat B"]
        assert ms.list_entries(parsed) == [
            {"name": "E3", "category": "Cat C"},
            {"name": "E1", "category": "Cat A"},
            {"name": "E2", "category": "Cat B"},
        ]

    def test_move_to_end_of_file_when_before_omitted(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n# Cat B\n## E2\nB2\n")
        ms.move_category(parsed, "Cat A")
        assert ms.list_categories(parsed) == ["Cat B", "Cat A"]
        assert ms.serialize(parsed) == [
            "# Cat B",
            "## E2",
            "B2",
            "# Cat A",
            "## E1",
            "B1",
        ]

    def test_move_carries_description_and_entries_byte_identically(self) -> None:
        text = (
            "# Cat B\n## E3\nB3\n"
            "# Cat A\nDescription prose.\n## E1\nLine one.\n\nLine two.\n## E2\nB2\n"
        )
        parsed = ms.parse(text)
        ms.move_category(parsed, "Cat A", before="Cat B")
        assert ms.list_categories(parsed) == ["Cat A", "Cat B"]
        assert ms.get_category_description(parsed, "Cat A") == "Description prose."
        assert ms.get_entry(parsed, "E1")["text"] == "Line one.\n\nLine two."
        assert ms.get_entry(parsed, "E2")["text"] == "B2"
        assert ms.list_entries(parsed) == [
            {"name": "E1", "category": "Cat A"},
            {"name": "E2", "category": "Cat A"},
            {"name": "E3", "category": "Cat B"},
        ]

    def test_move_preserves_relative_order_of_a_multi_entry_block(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n## E2\nB2\n## E3\nB3\n# Cat B\n## E4\nB4\n")
        ms.move_category(parsed, "Cat B", before="Cat A")
        assert ms.serialize(parsed) == [
            "# Cat B",
            "## E4",
            "B4",
            "# Cat A",
            "## E1",
            "B1",
            "## E2",
            "B2",
            "## E3",
            "B3",
        ]

    def test_move_the_uncategorized_head_block_is_rejected(self) -> None:
        parsed = ms.parse("## Head1\nH1\n# Cat A\n## E1\nB1\n")
        with pytest.raises(ms.CategoryNotFoundError):
            ms.move_category(parsed, "", before="Cat A")

    def test_move_blank_name_is_rejected_even_with_no_head_entries(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n")
        with pytest.raises(ms.CategoryNotFoundError):
            ms.move_category(parsed, "   ")

    def test_move_unknown_name_raises(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n")
        with pytest.raises(ms.CategoryNotFoundError):
            ms.move_category(parsed, "does-not-exist")

    def test_move_unknown_before_raises(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n")
        with pytest.raises(ms.CategoryNotFoundError):
            ms.move_category(parsed, "Cat A", before="does-not-exist")

    def test_move_before_self_is_a_documented_noop(self) -> None:
        parsed = ms.parse("# Cat A\n## E1\nB1\n# Cat B\n## E2\nB2\n")
        result = ms.move_category(parsed, "Cat A", before="Cat A")
        assert result == {"name": "Cat A"}
        assert ms.list_categories(parsed) == ["Cat A", "Cat B"]

    def test_move_explicit_blank_before_is_not_treated_as_omitted(self) -> None:
        # Unlike a bare omission (before=None, which means "end of file"),
        # an explicit "" is looked up like any other name and fails — same
        # "blank is never a valid name" convention move_entry gets for free.
        parsed = ms.parse("# Cat A\n## E1\nB1\n")
        with pytest.raises(ms.CategoryNotFoundError):
            ms.move_category(parsed, "Cat A", before="")


# -------------------------------------------------------------- round trips


class TestByteFidelity:
    def test_preamble_is_preserved_verbatim(self) -> None:
        text = "Some intro text.\nMore intro.\n\n## Entry\nBody\n"
        parsed = ms.parse(text)
        assert ms.serialize(parsed) == [
            "Some intro text.",
            "More intro.",
            "",
            "## Entry",
            "Body",
        ]

    def test_untouched_file_serializes_identically_modulo_trailing_newline(self) -> None:
        text = "# Cat A\n\n## E1\nLine1\n\nLine2\n\n## E2\nOther\n"
        parsed = ms.parse(text)
        assert "\n".join(ms.serialize(parsed)) == text.rstrip("\n")

    def test_untouched_entry_survives_an_edit_to_a_sibling(self) -> None:
        text = "## Keep Me\nUntouched body.\nWith two lines.\n\n## Change Me\nold\n"
        parsed = ms.parse(text)
        ms.upsert_entry(parsed, "Change Me", "new")
        assert ms.get_entry(parsed, "Keep Me")["text"] == "Untouched body.\nWith two lines."


# ---------------------------------------------------------- line endings


class TestDetectLineEnding:
    def test_crlf_file_is_detected(self) -> None:
        assert ms.detect_line_ending("## A\r\nBody\r\n") == "\r\n"

    def test_lf_file_is_detected(self) -> None:
        assert ms.detect_line_ending("## A\nBody\n") == "\n"

    def test_empty_text_defaults_to_lf(self) -> None:
        assert ms.detect_line_ending("") == "\n"

    def test_majority_wins_on_mixed_endings(self) -> None:
        text = "a\r\nb\r\nc\r\nd\n"  # 3 CRLF vs 1 lone LF
        assert ms.detect_line_ending(text) == "\r\n"

    def test_tie_defaults_to_lf(self) -> None:
        text = "a\r\nb\n"  # 1 CRLF vs 1 lone LF
        assert ms.detect_line_ending(text) == "\n"


# --------------------------------------------------------------------- I/O


class TestLoadSaveNotebook:
    def test_missing_file_yields_empty_parsed_and_none_mtime(self, tmp_path: Path) -> None:
        parsed, mtime, line_ending = ms.load_notebook(tmp_path / "nope.md")
        assert ms.list_entries(parsed) == []
        assert mtime is None
        assert line_ending == "\n"

    def test_existing_empty_file_has_a_real_mtime(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.md"
        path.write_text("", encoding="utf-8")
        parsed, mtime, _line_ending = ms.load_notebook(path)
        assert ms.list_entries(parsed) == []
        assert mtime is not None

    def test_save_then_load_round_trips_entries(self, tmp_path: Path) -> None:
        path = tmp_path / "loras.md"
        parsed = ms.parse("")
        ms.upsert_entry(parsed, "Entry One", "hello world", category="Cat A")
        ms.save_notebook(path, parsed, "\n")

        loaded, mtime, line_ending = ms.load_notebook(path)
        assert mtime is not None
        assert line_ending == "\n"
        assert ms.list_entries(loaded) == [{"name": "Entry One", "category": "Cat A"}]
        assert ms.get_entry(loaded, "Entry One")["text"] == "hello world"

    def test_save_creates_missing_parent_directories(self, tmp_path: Path) -> None:
        path = tmp_path / "sub" / "dir" / "loras.md"
        parsed = ms.parse("")
        ms.upsert_entry(parsed, "E", "x")
        ms.save_notebook(path, parsed, "\n")
        assert path.exists()

    def test_save_collapses_trailing_blank_lines_to_one_newline(self, tmp_path: Path) -> None:
        path = tmp_path / "loras.md"
        parsed = ms.parse("## E\nbody\n\n\n")
        ms.save_notebook(path, parsed, "\n")
        raw = path.read_text(encoding="utf-8")
        assert raw.endswith("body\n")
        assert not raw.endswith("body\n\n")

    def test_unicode_round_trips_through_disk(self, tmp_path: Path) -> None:
        path = tmp_path / "loras.md"
        parsed = ms.parse("")
        ms.upsert_entry(parsed, "Café ☕", "日本語 text")
        ms.save_notebook(path, parsed, "\n")
        loaded, _mtime, _le = ms.load_notebook(path)
        assert ms.get_entry(loaded, "Café ☕")["text"] == "日本語 text"

    def test_crlf_file_round_trips_and_stays_crlf_after_an_edit(self, tmp_path: Path) -> None:
        path = tmp_path / "loras.md"
        raw = "## Entry A\r\nLine A1\r\n\r\n## Entry B\r\nLine B1\r\n"
        path.write_bytes(raw.encode("utf-8"))

        parsed, _mtime, line_ending = ms.load_notebook(path)
        assert line_ending == "\r\n"
        ms.upsert_entry(parsed, "Entry A", "Line A1\nNew line2")
        ms.save_notebook(path, parsed, line_ending)

        with open(path, encoding="utf-8", newline="") as fh:
            rewritten = fh.read()
        assert "\r\n" in rewritten
        # No lone LF anywhere: every "\n" is part of a "\r\n" pair.
        assert rewritten.count("\n") == rewritten.count("\r\n")
        # The untouched sibling entry's exact bytes are still present.
        assert "## Entry B\r\nLine B1\r\n" in rewritten

    def test_save_returns_the_new_mtime_matching_the_file_on_disk(self, tmp_path: Path) -> None:
        path = tmp_path / "loras.md"
        parsed = ms.parse("")
        ms.upsert_entry(parsed, "E", "x")
        returned_mtime = ms.save_notebook(path, parsed, "\n")
        assert returned_mtime == path.stat().st_mtime


# ------------------------------------------------------------------ conflict


class TestCheckConflict:
    def test_matching_mtime_does_not_raise(self) -> None:
        ms.check_conflict(100.0, 100.0)

    def test_missing_base_mtime_skips_the_check(self) -> None:
        ms.check_conflict(None, 999.0)

    def test_missing_file_skips_the_check_even_with_base_mtime_given(self) -> None:
        ms.check_conflict(100.0, None)

    def test_tiny_float_drift_within_tolerance_does_not_raise(self) -> None:
        ms.check_conflict(100.0, 100.0 + 1e-7)

    def test_mismatched_mtime_raises_with_current_mtime_attached(self) -> None:
        with pytest.raises(ms.ConflictError) as exc_info:
            ms.check_conflict(100.0, 200.0)
        assert exc_info.value.current_mtime == 200.0


class TestResolveNotebookFileRejectsUriValues:
    """2026-07-26 owner report (Linux): a typed ``smb://`` address used to be
    silently swallowed. ``Path("smb://host/x")`` collapses the double slash to
    ``smb:/host/x``, which is NOT absolute on POSIX, so the value was treated
    as RELATIVE and joined under the library folder -- the node then reported a
    missing file (reading as an empty notebook) and the first save would have
    created a bogus ``smb:/host/...`` tree inside the real library folder.
    """

    @pytest.mark.parametrize(
        "value",
        [
            "smb://dxp4800pro-ring.local/personal_folder/docs/loras.md",
            "nfs://server/export/loras.md",
            "http://example.com/loras.md",
            "  smb://host/share/loras.md  ",
        ],
    )
    def test_uri_style_values_raise_valueerror(
        self, context: LibraryContext, value: str
    ) -> None:
        with pytest.raises(ValueError) as exc_info:
            context.resolve_notebook_file(value)
        message = str(exc_info.value)
        assert "network address, not a file path" in message
        assert "Mount the share first" in message

    def test_error_names_the_callers_own_scheme(self, context: LibraryContext) -> None:
        with pytest.raises(ValueError) as exc_info:
            context.resolve_notebook_file("afp://host/share/loras.md")
        assert str(exc_info.value).startswith("afp://")

    def test_uri_value_never_lands_inside_the_library_folder(
        self, context: LibraryContext
    ) -> None:
        # The actual damage this prevents: a relative-looking join under the
        # user's real library dir (and a mkdir of it on the next save).
        with pytest.raises(ValueError):
            context.resolve_notebook_file("smb://host/share/loras.md")
        assert not list(context.library_dir().glob("smb:*"))

    @pytest.mark.parametrize(
        "value",
        ["loras.md", "sub/loras.md", "/mnt/nas/loras.md", "//server/share/loras.md"],
    )
    def test_ordinary_and_unc_paths_still_resolve(
        self, context: LibraryContext, value: str
    ) -> None:
        # No false positives: relative names, POSIX absolutes, and a UNC-style
        # double-slash path (no scheme, so no "://") all pass through as before.
        assert isinstance(context.resolve_notebook_file(value), Path)
