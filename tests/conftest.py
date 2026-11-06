"""Shared fixtures: a fully fake LibraryContext wired to tmp_path directories.

No ComfyUI anywhere: the context-injection pattern (``lora_library/
context.py``) means every test gets real behavior against throwaway
directories and a fake installed-lora list. Same approach as
comfyui-photoshop-bridge's test suite.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lora_library.context import LibraryContext

#: What the fake "models/loras" folder contains, in folder_paths format
#: (forward slashes, relative to the loras root).
FAKE_LORAS = [
    "detailer.safetensors",
    "styles/film_grain.safetensors",
    "styles/cinematic.safetensors",
]


@pytest.fixture
def context(tmp_path: Path) -> LibraryContext:
    """A LibraryContext over fresh tmp_path dirs with a fake lora list."""
    user_dir = tmp_path / "user" / "lora_library"
    return LibraryContext(
        user_dir=user_dir,
        default_library_dir=user_dir / "library",
        list_loras=lambda: list(FAKE_LORAS),
    )


@pytest.fixture
def library_dir(context: LibraryContext) -> Path:
    """The context's active library dir, created."""
    return context.library_dir()


# --- Cross-platform seams for the CI matrix (Linux + Windows) ----------------
#
# The suite was written on macOS. These helpers keep the platform-specific
# tests honest on the other two CI hosts instead of letting them fail (or
# pass by accident) for reasons unrelated to what they test.


@pytest.fixture
def posix_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin ``lora_library.context._IS_WINDOWS`` to False for a test written
    from a POSIX host's point of view ("a Windows-shaped path arrives at a
    macOS/Linux ComfyUI"). A no-op on macOS/Linux; on a Windows CI runner it
    simulates the POSIX host through the same seam the ``*_on_simulated_
    windows`` tests already use for the reverse direction, so both directions
    run on every OS."""
    from lora_library import context as context_module

    monkeypatch.setattr(context_module, "_IS_WINDOWS", False)


#: chmod-based "unreadable/unwritable directory" tests need POSIX permission
#: bits to be enforced. Windows ignores them for directories (chmod only sets
#: the read-only attribute), and root bypasses them everywhere.
POSIX_PERMISSIONS_ENFORCED = sys.platform != "win32" and not (
    hasattr(os, "geteuid") and os.geteuid() == 0
)

needs_posix_permissions = pytest.mark.skipif(
    not POSIX_PERMISSIONS_ENFORCED,
    reason="needs enforced POSIX permission bits (Windows ignores chmod on "
    "directories; root bypasses them)",
)


def symlink_or_skip(link: Path, target: Path, *, target_is_directory: bool = False) -> None:
    """Create *link* -> *target*, or skip the calling test where this user may
    not create symlinks (Windows without Developer Mode or admin rights)."""
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create symlinks here: {exc}")


# --- Session-start temp-dir hygiene -----------------------------------------
#
# test_routes_core.py's TestListableDirGuard/TestRootsOmitsUnlistableHome
# chmod two throwaway tmp_path dirs to 0o000 to exercise unreadable-directory
# handling, restoring them in a `finally`. If a run is hard-killed inside
# that window (Ctrl-C mid-test, OOM kill, CI timeout), the `finally` never
# runs and an unreadable dir survives -- not just in this repo's own
# tmp_path, but under this machine's SHARED ``pytest-of-<user>`` root, the
# parent of every pytest session's numbered dir, reused across every repo
# and venv this user runs pytest from.
#
# pytest's own end-of-session cleanup (``_pytest.pathlib.cleanup_numbered_dir``,
# deferred to session finish) globs that root for ``garbage-*`` entries --
# both ones it renames itself from old numbered dirs, and ones already
# sitting there from a past session -- and tries to delete them. It can't
# delete what it can't read, so a single hard-kill at the wrong moment
# leaves a `garbage-*` directory that makes every subsequent pytest run, in
# any repo, spam `PytestWarning: (rm_rf) error removing ...` forever.
#
# The fixture below is a courtesy, best-effort pre-heal: before pytest's own
# cleanup runs, chmod any unreadable directory under an existing
# ``garbage-*`` entry back to u+rwx so pytest's normal deletion can finally
# succeed. It only touches entries directly under the pytest-of-* root whose
# names start with ``garbage-``; it never follows symlinks (``os.walk``'s
# ``followlinks`` defaults to False here -- relied on, not overridden); and
# it never deletes anything itself -- pytest still does the actual deleting.


def _heal_unreadable_dir(path: Path) -> bool:
    """chmod *path* to add owner rwx if it's currently missing any of that.

    Best-effort: returns False (never raises) for any OSError, including a
    path that vanished under us or one this process doesn't own.
    """
    try:
        if path.is_symlink() or not path.is_dir():
            return False
        if os.access(path, os.R_OK | os.W_OK | os.X_OK):
            return False
        mode = path.stat().st_mode
        os.chmod(path, mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        return True
    except OSError:
        return False


def _heal_unreadable_garbage_dirs(pytest_of_user_root: Path) -> int:
    """Best-effort chmod repair for unreadable dirs under *pytest_of_user_root*'s
    own ``garbage-*`` entries (see the section comment above).

    Returns how many directories were actually healed; 0 on a no-op (nothing
    to heal, or the root doesn't exist yet -- normal on a first-ever run).
    Pure and scoped entirely to the *pytest_of_user_root* it's given, so
    it's safe to unit-test directly against a constructed directory --
    unlike the fixture below, which must run against pytest's own real,
    shared temp root and so isn't exercised by a test.
    """
    try:
        candidates = list(pytest_of_user_root.glob("garbage-*"))
    except OSError:
        return 0

    healed = 0
    for entry in candidates:
        try:
            if entry.is_symlink() or not entry.is_dir():
                continue
        except OSError:
            continue
        if _heal_unreadable_dir(entry):
            healed += 1
        # followlinks=False is the default: a garbage-* dir is pytest's own
        # former numbered tmp dir, never expected to symlink outside itself,
        # and healing must never chase a link to somewhere it doesn't own.
        for dirpath, dirnames, _filenames in os.walk(entry, followlinks=False):
            for dirname in dirnames:
                if _heal_unreadable_dir(Path(dirpath) / dirname):
                    healed += 1
    return healed


@pytest.fixture(scope="session", autouse=True)
def _heal_stale_pytest_garbage_dirs(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Pre-heal leftover ``garbage-*`` permission damage before pytest cleans up.

    See the section comment above: this exists solely so that a past
    hard-kill inside one of test_routes_core.py's chmod(0o000) tests doesn't
    leave every future pytest run on this machine warning about a directory
    it cannot delete. Quiet on success; prints one line only when it
    actually healed something.
    """
    try:
        pytest_of_user_root = tmp_path_factory.getbasetemp().parent
        healed = _heal_unreadable_garbage_dirs(pytest_of_user_root)
    except Exception:
        # Best-effort courtesy cleanup: must never fail a test session.
        return
    if healed:
        print(
            f"[conftest] healed {healed} unreadable leftover pytest garbage "
            f"dir(s) under {pytest_of_user_root}"
        )


# --- switcher.js served layout -------------------------------------------------
#
# web/eps_image/switcher.js imports owned_link_colour.js (the all-off dim),
# and -- since the all-off unplug -- number_controller.js and bypass.js (the
# shared unplug/replug machinery; bypass.js in turn imports distributor.js and
# both import ``../lora_library/api.js``). Every Node harness that loads the
# REAL switcher.js lays these out the same way, so it lives here once.

_EPS_WEB = Path(__file__).resolve().parent.parent / "web" / "eps_image"
_SWITCHER_JS_CLOSURE = (
    "switcher.js",
    "owned_link_colour.js",
    "number_controller.js",
    "bypass.js",
    "distributor.js",
)


@pytest.fixture(scope="session")
def install_switcher_js():
    """A function ``install(layout, app_js=...)`` that copies switcher.js and
    its import closure into ``layout/extensions/comfyui-epsnodes/eps_image``,
    writes the ``scripts/app.js`` stub (*app_js*), and stubs
    ``lora_library/api.js`` (only ``subscribeWidgetsChangedExternally`` is
    imported, and only called from attach paths of other node classes)."""
    import shutil

    def install(layout: Path, app_js: str = "export const app = {}\n") -> Path:
        module_dir = layout / "extensions" / "comfyui-epsnodes" / "eps_image"
        module_dir.mkdir(parents=True, exist_ok=True)
        for name in _SWITCHER_JS_CLOSURE:
            shutil.copyfile(_EPS_WEB / name, module_dir / name)
        lora_library = layout / "extensions" / "comfyui-epsnodes" / "lora_library"
        lora_library.mkdir(parents=True, exist_ok=True)
        (lora_library / "api.js").write_text(
            "export function subscribeWidgetsChangedExternally() {}\n", encoding="utf-8"
        )
        scripts = layout / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        (scripts / "app.js").write_text(app_js, encoding="utf-8")
        return module_dir

    return install
