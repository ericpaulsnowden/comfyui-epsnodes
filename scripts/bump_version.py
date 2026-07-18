#!/usr/bin/env python3
"""Bump EPSNodes' version across every file that carries it.

Usage::

    python scripts/bump_version.py               # bump the patch version
    python scripts/bump_version.py --minor        # bump minor, reset patch
    python scripts/bump_version.py --major        # bump major, reset minor+patch
    python scripts/bump_version.py --dry-run       # print what would change, write nothing

Stdlib-only. Adapted from comfyui-photoshop-bridge's script of the same name
(FORMAT.md §8: every push bumps + tags; version = code-sync signal).

Targets:

* ``lora_library/version.py``       -- ``__version__ = "X.Y.Z"``      (backend)
* ``pyproject.toml``                -- ``[project]`` ``version = "X.Y.Z"``
* ``web/lora_library/version.js``   -- ``FRONTEND_VERSION = 'X.Y.Z'`` (frontend)

Every target is located with an anchored regex required to match EXACTLY
once; if any file is missing or ambiguous the whole run refuses and writes
nothing ("dirty parse") — a partially-applied bump silently desynchronizes
the single source of truth this script exists to maintain. The current
versions must also already agree before bumping: if they've drifted, this
refuses rather than guessing which one is "right".
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Bare X.Y.Z, reused inside every file-specific pattern below.
_VERSION = r"\d+\.\d+\.\d+"


class _Target(NamedTuple):
    """One version-carrying file: path, message label, and the regex that
    locates the version. Every pattern captures ``(prefix)(version)(suffix)``
    so the writer substitutes group 2 alone."""

    path: Path
    pattern: re.Pattern[str]
    label: str


def _targets(repo_root: Path) -> list[_Target]:
    return [
        _Target(
            repo_root / "lora_library" / "version.py",
            re.compile(rf'(__version__\s*=\s*")({_VERSION})(")'),
            "lora_library/version.py",
        ),
        _Target(
            repo_root / "pyproject.toml",
            # No trailing anchor: subn() replaces the whole match, and `\s`
            # would swallow the following newline (learned in cpsb).
            re.compile(rf'(?m)^(version\s*=\s*")({_VERSION})(")'),
            "pyproject.toml",
        ),
        _Target(
            repo_root / "web" / "lora_library" / "version.js",
            re.compile(rf"(FRONTEND_VERSION\s*=\s*')({_VERSION})(')"),
            "web/lora_library/version.js",
        ),
    ]


def _bump(version: str, part: str) -> str:
    major, minor, patch = (int(piece) for piece in version.split("."))
    if part == "major":
        major, minor, patch = major + 1, 0, 0
    elif part == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    return f"{major}.{minor}.{patch}"


def _read_current_version(target: _Target) -> str:
    if not target.path.is_file():
        raise SystemExit(f"refusing to bump: {target.label} does not exist")
    text = target.path.read_text(encoding="utf-8")
    matches = list(target.pattern.finditer(text))
    if len(matches) != 1:
        raise SystemExit(
            f"refusing to bump: expected exactly one version string in "
            f"{target.label}, found {len(matches)}"
        )
    return matches[0].group(2)


def _write_new_version(target: _Target, new_version: str) -> None:
    text = target.path.read_text(encoding="utf-8")
    new_text, count = target.pattern.subn(
        lambda m: m.group(1) + new_version + m.group(3), text
    )
    if count != 1:
        raise SystemExit(
            f"refusing to bump: expected exactly one version string in "
            f"{target.label}, found {count} while writing"
        )
    target.path.write_text(new_text, encoding="utf-8")


def bump_all(repo_root: Path, part: str, dry_run: bool = False) -> tuple[str, str]:
    """Bump every version-carrying file under *repo_root* in lockstep.

    Returns ``(old_version, new_version)``; raises SystemExit on any dirty
    parse or drifted versions (see module docstring).
    """
    targets = _targets(repo_root)
    current_versions = {target.label: _read_current_version(target) for target in targets}
    distinct = set(current_versions.values())
    if len(distinct) != 1:
        details = ", ".join(f"{label}={version}" for label, version in current_versions.items())
        raise SystemExit(f"refusing to bump: versions have drifted out of sync ({details})")

    old_version = distinct.pop()
    new_version = _bump(old_version, part)

    if not dry_run:
        for target in targets:
            _write_new_version(target, new_version)

    return old_version, new_version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bump EPSNodes' version across every file that carries it."
    )
    bump_part = parser.add_mutually_exclusive_group()
    bump_part.add_argument(
        "--minor", action="store_const", dest="part", const="minor",
        help="Bump the minor version (resets patch to 0). Default: bump the patch version.",
    )
    bump_part.add_argument(
        "--major", action="store_const", dest="part", const="major",
        help="Bump the major version (resets minor and patch to 0).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would change without writing any file.",
    )
    parser.set_defaults(part="patch")
    args = parser.parse_args(argv)

    old_version, new_version = bump_all(REPO_ROOT, args.part, dry_run=args.dry_run)

    verb = "Would bump" if args.dry_run else "Bumped"
    print(f"{verb} {old_version} -> {new_version} ({args.part})")
    for target in _targets(REPO_ROOT):
        print(f"  {target.label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
