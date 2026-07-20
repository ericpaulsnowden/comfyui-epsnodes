"""ComfyUI entry point for comfyui-lora-library.

This is the only file in the pack that touches ComfyUI's own modules
(``server``, ``folder_paths``). It builds the real
:class:`~lora_library.context.LibraryContext`, registers the HTTP routes and
the nodes, and exposes the standard ``NODE_CLASS_MAPPINGS`` /
``WEB_DIRECTORY`` attributes ComfyUI's loader looks for. Everything under
``lora_library/`` stays importable (and tested) without ComfyUI — see
``lora_library/context.py``.

Feature modules are imported DEFENSIVELY: a broken or not-yet-present
feature logs loudly and is skipped, and the rest of the pack still loads —
one bad module must never take the whole pack (or a user's queue) down.
"""

import importlib
import logging
from pathlib import Path

try:
    from .lora_library import routes as _routes
    from .lora_library.context import LibraryContext
    from .lora_library.version import __version__

    # The pack's own package name, so feature modules in ANY sibling
    # sub-package (lora_library/, eps_image/, …) import as
    # f"{_TOP_PREFIX}.{module_path}". EPSNodes is deliberately a multi-family
    # pack (FORMAT.md naming note): lora nodes live in lora_library/, non-lora
    # image nodes in eps_image/, future families in their own siblings.
    _TOP_PREFIX = __name__
except ImportError:
    # Imported without package context (e.g. pytest rootdir setups, or tooling
    # that loads node-pack entry files flat). ComfyUI itself always loads this
    # file as a package, taking the relative-import branch above.
    from lora_library import routes as _routes
    from lora_library.context import LibraryContext
    from lora_library.version import __version__

    _TOP_PREFIX = ""

logger = logging.getLogger("lora_library")


def _build_context() -> LibraryContext:
    import folder_paths  # ComfyUI's own module; only importable inside ComfyUI

    try:
        user_root = Path(folder_paths.get_user_directory())
    except AttributeError:  # pre-user-directory ComfyUI builds
        user_root = Path(folder_paths.base_path) / "user"
    pack_user_dir = user_root / "lora_library"

    def _list_loras() -> list[str]:
        try:
            return list(folder_paths.get_filename_list("loras"))
        except Exception:  # a broken model dir must not kill the pack
            logger.exception("lora_library: could not list loras")
            return []

    def _resolve_lora_path(name: str) -> str | None:
        try:
            return folder_paths.get_full_path("loras", name)
        except Exception:  # resolution failure means "not found"
            return None

    return LibraryContext(
        user_dir=pack_user_dir,
        default_library_dir=pack_user_dir / "library",
        list_loras=_list_loras,
        resolve_lora_path=_resolve_lora_path,
    )


_context = _build_context()
_routes.register(_context)

# Class ids are FROZEN once shipped (FORMAT.md §8): saved workflows reference
# nodes by id, and renaming one silently breaks every workflow containing it.
# Each spec is (module_path_under_the_pack, class_id, display_name); the
# module path names its sub-package so families can live in siblings
# (lora_library/ = the lora family; eps_image/ = non-lora image nodes).
# set_context is called only when a module defines it (the image nodes don't
# need the library context for their current milestones).
_NODE_SPECS = [
    ("lora_library.nodes_notebook", "LoraLibraryNotebook", "Prompt Notebook"),
    ("lora_library.nodes_sets", "LoraLibraryApplySet", "Apply LoRA Set"),
    ("eps_image.nodes_switcher", "EPSSwitcher", "EPS Switcher"),
    ("eps_image.nodes_resolution", "EPSResolution", "EPS Resolution"),
    ("eps_image.nodes_image_grid", "EPSImageGrid", "EPS Image Grid"),
]

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

for _module_path, _class_id, _display in _NODE_SPECS:
    try:
        _full = f"{_TOP_PREFIX}.{_module_path}" if _TOP_PREFIX else _module_path
        _module = importlib.import_module(_full)
        if hasattr(_module, "set_context"):
            _module.set_context(_context)
        NODE_CLASS_MAPPINGS[_class_id] = getattr(_module, _class_id)
        NODE_DISPLAY_NAME_MAPPINGS[_class_id] = _display
    except Exception:  # skip the feature, keep the pack alive
        logger.exception("lora_library: feature module %s failed to load", _module_path)

# EPSImageGrid's own tiny route module (FORMAT.md §6.6: just `POST
# /eps_image_grid/clear`) — needs no LibraryContext (its store resolves
# ComfyUI's output dir straight from `folder_paths`, lazily), so it isn't
# folded into `_routes.register(_context)` above. Defensive, like the node
# loop above: a routing failure here must not take the rest of the pack
# down with it.
try:
    _image_grid_routes_path = "eps_image.routes_image_grid"
    if _TOP_PREFIX:
        _image_grid_routes_path = f"{_TOP_PREFIX}.{_image_grid_routes_path}"
    _image_grid_routes = importlib.import_module(_image_grid_routes_path)
    _image_grid_routes.register()
except Exception:
    logger.exception("lora_library: eps_image.routes_image_grid failed to register")

WEB_DIRECTORY = "./web"


def _warn_on_duplicate_installs() -> None:
    """Shout when this pack is installed under more than one folder.

    A rename (comfyui-lora-library → EPSNodes → comfyui-epsnodes) makes it
    easy to end up with an old clone AND a new one in custom_nodes. Both
    load; ComfyUI keeps the FIRST frontend extension registered under our
    name, so a stale clone can silently win the UI while the newer backend
    wins the nodes — the worst of both. (Exactly this bit the owner on
    2026-07-18.) Detection: any sibling directory that also carries our
    ``lora_library/version.py``.
    """
    here = Path(__file__).resolve().parent
    try:
        siblings = [
            other
            for other in here.parent.iterdir()
            if other.is_dir()
            and other.resolve() != here
            and (other / "lora_library" / "version.py").is_file()
        ]
    except OSError:
        return
    for other in siblings:
        logger.error(
            "EPSNodes is installed TWICE: %s AND %s. Delete the older folder — "
            "with both present, whichever loads first controls the UI and may "
            "be a stale version.",
            here.name,
            other.name,
        )


_warn_on_duplicate_installs()

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY", "__version__"]

logger.info(
    "lora_library v%s loaded (%d nodes; library: %s)",
    __version__,
    len(NODE_CLASS_MAPPINGS),
    _context.library_dir(),
)
