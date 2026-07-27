"""Unit tests for conftest.py's best-effort ``garbage-*`` permission healing.

These exercise the pure helper function directly against a directory tree
*we* construct under this test's own ``tmp_path`` -- never pytest's real,
shared ``pytest-of-<user>`` root. Testing the ``_heal_stale_pytest_garbage_dirs``
fixture itself against that real root isn't attempted here: it would mean
racing pytest's own numbered-dir bookkeeping for the actual session under
test, which is exactly the kind of flakiness this module's docstring in
conftest.py warns about.
"""

from __future__ import annotations

import os
from pathlib import Path

import conftest


class TestHealUnreadableGarbageDirs:
    def test_heals_an_unreadable_top_level_garbage_dir(self, tmp_path: Path) -> None:
        garbage = tmp_path / "garbage-abc123"
        garbage.mkdir()
        garbage.chmod(0o000)
        try:
            healed = conftest._heal_unreadable_garbage_dirs(tmp_path)
        finally:
            # Restore first, so pytest's own tmp_path cleanup can't trip over
            # the very thing this test is about, even if an assertion below fails.
            garbage.chmod(0o755)
        assert healed == 1
        assert os.access(garbage, os.R_OK | os.W_OK | os.X_OK)

    def test_heals_a_nested_unreadable_dir(self, tmp_path: Path) -> None:
        garbage = tmp_path / "garbage-nested"
        nested = garbage / "blocked"
        nested.mkdir(parents=True)
        nested.chmod(0o000)
        try:
            healed = conftest._heal_unreadable_garbage_dirs(tmp_path)
        finally:
            nested.chmod(0o755)
        assert healed == 1
        assert os.access(nested, os.R_OK | os.W_OK | os.X_OK)

    def test_ignores_entries_not_named_garbage(self, tmp_path: Path) -> None:
        other = tmp_path / "pytest-123"
        other.mkdir()
        other.chmod(0o000)
        try:
            healed = conftest._heal_unreadable_garbage_dirs(tmp_path)
        finally:
            other.chmod(0o755)
        assert healed == 0

    def test_no_garbage_dirs_present_is_a_noop(self, tmp_path: Path) -> None:
        assert conftest._heal_unreadable_garbage_dirs(tmp_path) == 0

    def test_missing_root_is_a_noop_not_an_error(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist"
        assert conftest._heal_unreadable_garbage_dirs(missing) == 0

    def test_already_readable_garbage_dir_is_left_alone(self, tmp_path: Path) -> None:
        garbage = tmp_path / "garbage-fine"
        garbage.mkdir()
        assert conftest._heal_unreadable_garbage_dirs(tmp_path) == 0

    def test_never_raises_on_a_symlinked_garbage_entry(self, tmp_path: Path) -> None:
        real_target = tmp_path / "elsewhere"
        real_target.mkdir()
        link = tmp_path / "garbage-link"
        link.symlink_to(real_target, target_is_directory=True)
        # Must not follow the symlink (never touch *real_target*, never raise).
        assert conftest._heal_unreadable_garbage_dirs(tmp_path) == 0
        assert os.access(real_target, os.R_OK | os.W_OK | os.X_OK)
