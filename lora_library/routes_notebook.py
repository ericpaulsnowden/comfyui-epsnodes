"""HTTP routes for the LoRA notebook — the six ``/lora_library/notebook*``
rows of FORMAT.md §5.

Mirrors ``routes_sets.py``'s shape: thin aiohttp handlers doing only
HTTP-shaped work (status codes, body/query parsing, the §2 path guard),
delegating everything else to ``markdown_store``. Unlike a set slug, a
notebook ``file`` value is an arbitrary path the caller chooses (FORMAT.md
§1/§2) — so, unlike ``routes_sets.py``, every handler here resolves that
path and runs it through ``notebook_path_error`` before touching disk.

**Every filesystem touch runs off the event loop** (NAS round 2026-08-22,
owner: the Notebook "sometimes looks broken but just takes over a minute
to load, even when just tabbing between open workflows", library on a
NAS, usually from a remote browser). ``load_notebook``/``save_notebook``,
the resolve+§2-guard step (``library_dir()``'s on-demand mkdir, the
remote guard's ``Path.resolve`` walks) and ``open_folder``'s ``is_dir``
all go through ``asyncio.to_thread``, so one slow mount delays only the
request that touched it instead of stalling every request on the server
(queue submissions included). The handlers' error mapping is unchanged:
the thread call raises exactly what the direct call raised, where it
raised it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path

from aiohttp import web

from . import markdown_store
from .context import LibraryContext
from .routes import error_response, notebook_path_error, request_is_loopback

logger = logging.getLogger("lora_library")


def _resolve_path(
    context: LibraryContext, file_value: object, *, writing: bool = False
) -> tuple[Path | None, web.Response | None]:
    """``(path, None)`` on success, or ``(None, error_response)`` for a
    non-string *file_value* or one Path can't make sense of (e.g. an
    embedded NUL) — kept out of every handler below.

    ``writing=True`` (every mutating route since v0.52.1, owner report
    2026-08-03) additionally REFUSES an empty/absent *file_value* with a
    400: ``resolve_notebook_file`` resolves ``""`` to the DEFAULT notebook,
    which is fine for reads (a fresh node listing the default library
    file) but was the silent finalizer of the frontend's "reset to
    defaults" bug — a panel whose load had failed posted ``file: null``
    and the write landed in the default notebook instead of erroring.
    A well-behaved client always names the file it means to write.
    """
    if file_value is not None and not isinstance(file_value, str):
        return None, error_response(400, "'file' must be a string")
    if isinstance(file_value, str) and "\x00" in file_value:
        # Audit 2026-08-21: an embedded NUL used to reach open() (loopback)
        # or Path.resolve() inside the §2 guard (remote) as a 500; this is
        # the 400 the docstring above always promised.
        return None, error_response(400, "invalid 'file' path: embedded NUL byte")
    if writing and not (isinstance(file_value, str) and file_value.strip()):
        return None, error_response(
            400,
            "'file' is required for writes -- refusing to fall back to the "
            "default notebook",
        )
    try:
        return context.resolve_notebook_file(file_value or ""), None
    except (OSError, ValueError) as exc:
        return None, error_response(400, f"invalid 'file' path: {exc}")


def _resolve_and_guard_sync(
    context: LibraryContext, file_value: object, *, loopback: bool, writing: bool
) -> tuple[Path | None, web.Response | None]:
    """:func:`_resolve_path` followed by the FORMAT.md §2 guard -- the two
    steps every handler runs before touching the file, as one synchronous
    unit so :func:`_resolve_and_guard` can push both off the event loop in
    a single hop. ``(path, None)`` on success, else ``(None, response)``
    with the same 400/403 bodies the handlers always produced."""
    path, err = _resolve_path(context, file_value, writing=writing)
    if err is not None:
        return None, err
    guard = notebook_path_error(context, path, loopback=loopback, writing=writing)
    if guard:
        return None, error_response(403, guard)
    return path, None


async def _resolve_and_guard(
    context: LibraryContext, request: web.Request, file_value: object, *, writing: bool
) -> tuple[Path | None, web.Response | None]:
    """:func:`_resolve_and_guard_sync` in a worker thread. Resolving a
    relative ``file`` ensures the library folder exists (a ``mkdir`` --
    memoized by ``LibraryContext`` but a NAS round trip when it isn't) and
    the remote guard ``resolve()``s both the path and every allowed root
    (an ``lstat`` per path component on the NAS), so neither belongs on
    the loop. ``request_is_loopback`` stays on the loop: it reads the
    request, not the disk."""
    return await asyncio.to_thread(
        _resolve_and_guard_sync,
        context,
        file_value,
        loopback=request_is_loopback(request),
        writing=writing,
    )


def _parse_known_mtime(raw: object) -> float | None:
    """``GET /notebook``'s optional ``known_mtime`` query value as a float,
    or ``None`` when absent or not a number (then the full payload is
    returned -- a malformed hint degrades to a slower answer, never to an
    error; FORMAT.md §5)."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _file_mtime(path: Path) -> float | None:
    """*path*'s mtime, or ``None`` when it can't be stat'd (missing file,
    unreachable mount) -- the one ``stat`` the unchanged short-circuit costs."""
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _reveal_folder(path: Path) -> None:
    """Open *path* in the OS file manager, non-blocking.

    A module-level seam (FORMAT.md §5 ``open_folder``) so tests monkeypatch
    this one function instead of spawning a real Finder/Explorer/xdg-open
    process.
    """
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    elif sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])


def register(context: LibraryContext, routes: web.RouteTableDef) -> None:
    """Attach the §5 notebook rows to *routes*."""

    @routes.get("/lora_library/notebook")
    async def get_notebook(request: web.Request) -> web.Response:
        path, err = await _resolve_and_guard(
            context, request, request.query.get("file", ""), writing=False
        )
        if err is not None:
            return err

        # FORMAT.md §5 `known_mtime` (NAS round 2026-08-22): the panel sends
        # the mtime it last painted; when the file still carries it, ONE
        # stat answers with `unchanged: true` and nothing else -- no read,
        # no parse, no entries -- so a tab switch / poll over a slow mount
        # costs a round trip instead of the whole file. Missing file,
        # mismatch, absent or malformed hint: the full payload, exactly as
        # before.
        known_mtime = _parse_known_mtime(request.query.get("known_mtime"))
        if known_mtime is not None:
            current_mtime = await asyncio.to_thread(_file_mtime, path)
            if current_mtime is not None and abs(current_mtime - known_mtime) < 1e-6:
                return web.json_response(
                    {
                        "ok": True,
                        "unchanged": True,
                        "mtime": current_mtime,
                        "exists": True,
                        "file": str(path),
                    }
                )

        try:
            parsed, mtime, _line_ending = await asyncio.to_thread(
                markdown_store.load_notebook, path
            )
        except markdown_store.MarkdownStoreError as exc:
            return error_response(400, str(exc))
        entries = markdown_store.list_entries(parsed)
        # §5 amendment (v0.53.0, owner ask 2026-08-08: a search field over
        # titles AND bodies): `include_text=1` adds each entry's body text
        # to its row, so the panel can filter client-side -- instant on the
        # owner's LAN, no per-keystroke round trips, and the corpus rides
        # the panel's own reload cycle so it can never go stale relative to
        # what the list shows. Opt-in so every existing caller's payload is
        # byte-identical.
        if request.query.get("include_text") == "1":
            entries = [
                {
                    **entry,
                    "text": (markdown_store.get_entry(parsed, entry["name"]) or {}).get(
                        "text", ""
                    ),
                }
                for entry in entries
            ]
        return web.json_response(
            {
                "file": str(path),
                "exists": mtime is not None,
                "mtime": mtime,
                "entries": entries,
                # FORMAT.md §5: names in file order, INCLUDING empty
                # categories — the one thing `entries` alone can't reveal. A
                # missing file parses to zero blocks-with-headings, so this
                # is already `[]` without any special-casing here.
                "categories": markdown_store.list_categories(parsed),
                "problems": parsed.problems,
            }
        )

    @routes.get("/lora_library/notebook/category")
    async def get_notebook_category(request: web.Request) -> web.Response:
        path, err = await _resolve_and_guard(
            context, request, request.query.get("file", ""), writing=False
        )
        if err is not None:
            return err

        name = request.query.get("name", "")
        try:
            parsed, mtime, _line_ending = await asyncio.to_thread(
                markdown_store.load_notebook, path
            )
        except markdown_store.MarkdownStoreError as exc:
            return error_response(400, str(exc))
        description = markdown_store.get_category_description(parsed, name)
        if description is None:
            return error_response(404, f"no such category {name!r} in {path}")
        return web.json_response({"name": name.strip(), "description": description, "mtime": mtime})

    @routes.post("/lora_library/notebook/category")
    async def post_notebook_category(request: web.Request) -> web.Response:
        """FORMAT.md §5's create-or-describe row: an unknown ``name`` CREATEs
        the category (§3.4's Create category, with the given description
        applied atomically; ``after`` positions the new heading right after
        that entry/category instead of appending at end-of-file); a known
        one replaces its description (Set category description) and, when
        ``rename_to`` is given, renames its heading too. Same
        guard/conflict/error shape as ``post_notebook_entry`` above — see
        that handler for the parallel structure."""
        try:
            body = await request.json()
        except Exception:  # broad: malformed body is a client error
            return error_response(400, "body must be JSON")
        if not isinstance(body, dict):
            return error_response(400, "body must be a JSON object")

        path, err = await _resolve_and_guard(context, request, body.get("file"), writing=True)
        if err is not None:
            return err

        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            return error_response(400, "'name' is required")
        description = body.get("description")
        if description is not None and not isinstance(description, str):
            return error_response(400, "'description' must be a string")
        # after is CREATE-only (positions the new heading, FORMAT.md §3.4);
        # rename_to is known-name-only (nothing to rename on a create) — the
        # branches below use only the one that applies to each.
        after = body.get("after")
        if after is not None and not isinstance(after, str):
            return error_response(400, "'after' must be a string")
        rename_to = body.get("rename_to")
        if rename_to is not None and not isinstance(rename_to, str):
            return error_response(400, "'rename_to' must be a string")
        base_mtime = body.get("base_mtime")
        if base_mtime is not None and not isinstance(base_mtime, (int, float)):
            return error_response(400, "'base_mtime' must be a number")

        try:
            parsed, current_mtime, line_ending = await asyncio.to_thread(
                markdown_store.load_notebook, path
            )
        except markdown_store.MarkdownStoreError as exc:
            return error_response(400, str(exc))
        try:
            markdown_store.check_conflict(base_mtime, current_mtime)
        except markdown_store.ConflictError as exc:
            return web.json_response({"error": str(exc), "mtime": exc.current_mtime}, status=409)

        try:
            if markdown_store.get_category_description(parsed, name) is None:
                result = markdown_store.create_category(
                    parsed, name, description or "", after=after
                )
            else:
                result = markdown_store.set_category_description(parsed, name, description or "")
                if rename_to and rename_to.strip():
                    markdown_store.set_category_name(parsed, name, rename_to)
        except markdown_store.MarkdownStoreError as exc:
            return error_response(400, str(exc))

        new_mtime = await asyncio.to_thread(
            markdown_store.save_notebook, path, parsed, line_ending
        )
        response = {
            "ok": True,
            "mtime": new_mtime,
            "entries": markdown_store.list_entries(parsed),
            "categories": markdown_store.list_categories(parsed),
            # §3.4 demote-don't-refuse (v0.48.1) -- see post_notebook_entry.
            "adjusted_headings": result["adjusted_headings"],
        }
        if result["adjusted_headings"]:
            response["description"] = result["description"]
        return web.json_response(response)

    @routes.get("/lora_library/notebook/entry")
    async def get_notebook_entry(request: web.Request) -> web.Response:
        path, err = await _resolve_and_guard(
            context, request, request.query.get("file", ""), writing=False
        )
        if err is not None:
            return err

        name = request.query.get("name", "")
        try:
            parsed, mtime, _line_ending = await asyncio.to_thread(
                markdown_store.load_notebook, path
            )
        except markdown_store.MarkdownStoreError as exc:
            return error_response(400, str(exc))
        entry = markdown_store.get_entry(parsed, name)
        if entry is None:
            return error_response(404, f"no such entry {name!r} in {path}")
        return web.json_response({**entry, "mtime": mtime})

    @routes.post("/lora_library/notebook/entry")
    async def post_notebook_entry(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:  # broad: malformed body is a client error
            return error_response(400, "body must be JSON")
        if not isinstance(body, dict):
            return error_response(400, "body must be a JSON object")

        path, err = await _resolve_and_guard(context, request, body.get("file"), writing=True)
        if err is not None:
            return err

        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            return error_response(400, "'name' is required")
        text = body.get("text")
        if not isinstance(text, str):
            return error_response(400, "'text' must be a string")
        category = body.get("category")
        if category is not None and not isinstance(category, str):
            return error_response(400, "'category' must be a string")
        rename_to = body.get("rename_to")
        if rename_to is not None and not isinstance(rename_to, str):
            return error_response(400, "'rename_to' must be a string")
        # after is CREATE-only (FORMAT.md §3.4 Create after): positions a
        # brand-new entry right below the named one; ignored once `name`
        # already exists — see markdown_store.upsert_entry.
        after = body.get("after")
        if after is not None and not isinstance(after, str):
            return error_response(400, "'after' must be a string")
        base_mtime = body.get("base_mtime")
        if base_mtime is not None and not isinstance(base_mtime, (int, float)):
            return error_response(400, "'base_mtime' must be a number")

        try:
            parsed, current_mtime, line_ending = await asyncio.to_thread(
                markdown_store.load_notebook, path
            )
        except markdown_store.MarkdownStoreError as exc:
            return error_response(400, str(exc))
        try:
            markdown_store.check_conflict(base_mtime, current_mtime)
        except markdown_store.ConflictError as exc:
            return web.json_response({"error": str(exc), "mtime": exc.current_mtime}, status=409)

        try:
            result = markdown_store.upsert_entry(
                parsed, name, text, category=category, rename_to=rename_to, after=after
            )
        except markdown_store.MarkdownStoreError as exc:
            return error_response(400, str(exc))

        new_mtime = await asyncio.to_thread(
            markdown_store.save_notebook, path, parsed, line_ending
        )
        response = {
            "ok": True,
            "mtime": new_mtime,
            "entries": markdown_store.list_entries(parsed),
            # §3.4 demote-don't-refuse (v0.48.1): how many heading-looking
            # lines were demoted to keep the file format safe -- 0 for the
            # overwhelming majority of saves.
            "adjusted_headings": result["adjusted_headings"],
        }
        if result["adjusted_headings"]:
            # Only when something changed: the STORED text, so the editor
            # can fold disk truth in rather than silently diverging from
            # the file until the next reload.
            response["text"] = result["text"]
        return web.json_response(response)

    @routes.post("/lora_library/notebook/delete")
    async def post_notebook_delete(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:  # broad: malformed body is a client error
            return error_response(400, "body must be JSON")
        if not isinstance(body, dict):
            return error_response(400, "body must be a JSON object")

        path, err = await _resolve_and_guard(context, request, body.get("file"), writing=True)
        if err is not None:
            return err

        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            return error_response(400, "'name' is required")
        base_mtime = body.get("base_mtime")
        if base_mtime is not None and not isinstance(base_mtime, (int, float)):
            return error_response(400, "'base_mtime' must be a number")

        try:
            parsed, current_mtime, line_ending = await asyncio.to_thread(
                markdown_store.load_notebook, path
            )
        except markdown_store.MarkdownStoreError as exc:
            return error_response(400, str(exc))
        try:
            markdown_store.check_conflict(base_mtime, current_mtime)
        except markdown_store.ConflictError as exc:
            return web.json_response({"error": str(exc), "mtime": exc.current_mtime}, status=409)

        if not markdown_store.remove_entry(parsed, name):
            return error_response(404, f"no such entry {name!r} in {path}")

        new_mtime = await asyncio.to_thread(
            markdown_store.save_notebook, path, parsed, line_ending
        )
        return web.json_response(
            {"ok": True, "mtime": new_mtime, "entries": markdown_store.list_entries(parsed)}
        )

    @routes.post("/lora_library/notebook/move")
    async def post_notebook_move(request: web.Request) -> web.Response:
        """FORMAT.md §5's move row / §3.4 Move — exactly one of ``before``/
        ``category`` (else 400); unknown ``name``/``before`` is 404; §3.5
        conflicts are 409. Same shape as ``post_notebook_entry`` above."""
        try:
            body = await request.json()
        except Exception:  # broad: malformed body is a client error
            return error_response(400, "body must be JSON")
        if not isinstance(body, dict):
            return error_response(400, "body must be a JSON object")

        path, err = await _resolve_and_guard(context, request, body.get("file"), writing=True)
        if err is not None:
            return err

        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            return error_response(400, "'name' is required")
        before = body.get("before")
        if before is not None and not isinstance(before, str):
            return error_response(400, "'before' must be a string")
        category = body.get("category")
        if category is not None and not isinstance(category, str):
            return error_response(400, "'category' must be a string")
        if (before is None) == (category is None):
            return error_response(400, "exactly one of 'before' or 'category' is required")
        base_mtime = body.get("base_mtime")
        if base_mtime is not None and not isinstance(base_mtime, (int, float)):
            return error_response(400, "'base_mtime' must be a number")

        try:
            parsed, current_mtime, line_ending = await asyncio.to_thread(
                markdown_store.load_notebook, path
            )
        except markdown_store.MarkdownStoreError as exc:
            return error_response(400, str(exc))
        try:
            markdown_store.check_conflict(base_mtime, current_mtime)
        except markdown_store.ConflictError as exc:
            return web.json_response({"error": str(exc), "mtime": exc.current_mtime}, status=409)

        try:
            markdown_store.move_entry(parsed, name, before=before, category=category)
        except markdown_store.EntryNotFoundError as exc:
            return error_response(404, str(exc))

        new_mtime = await asyncio.to_thread(
            markdown_store.save_notebook, path, parsed, line_ending
        )
        return web.json_response(
            {"ok": True, "mtime": new_mtime, "entries": markdown_store.list_entries(parsed)}
        )

    @routes.post("/lora_library/notebook/move_category")
    async def post_notebook_move_category(request: web.Request) -> web.Response:
        """FORMAT.md §5's move_category row / §3.4 Move category: relocate a
        whole category block (heading + §3.1 description + all its entries)
        before another named category, or to end-of-file when ``before`` is
        omitted. Same guard/conflict shape as ``post_notebook_move`` above;
        unknown ``name``/``before`` — including the un-movable uncategorized
        head region — is 404 via ``markdown_store.CategoryNotFoundError``."""
        try:
            body = await request.json()
        except Exception:  # broad: malformed body is a client error
            return error_response(400, "body must be JSON")
        if not isinstance(body, dict):
            return error_response(400, "body must be a JSON object")

        path, err = await _resolve_and_guard(context, request, body.get("file"), writing=True)
        if err is not None:
            return err

        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            return error_response(400, "'name' is required")
        before = body.get("before")
        if before is not None and not isinstance(before, str):
            return error_response(400, "'before' must be a string")
        base_mtime = body.get("base_mtime")
        if base_mtime is not None and not isinstance(base_mtime, (int, float)):
            return error_response(400, "'base_mtime' must be a number")

        try:
            parsed, current_mtime, line_ending = await asyncio.to_thread(
                markdown_store.load_notebook, path
            )
        except markdown_store.MarkdownStoreError as exc:
            return error_response(400, str(exc))
        try:
            markdown_store.check_conflict(base_mtime, current_mtime)
        except markdown_store.ConflictError as exc:
            return web.json_response({"error": str(exc), "mtime": exc.current_mtime}, status=409)

        try:
            markdown_store.move_category(parsed, name, before=before)
        except markdown_store.CategoryNotFoundError as exc:
            return error_response(404, str(exc))

        new_mtime = await asyncio.to_thread(
            markdown_store.save_notebook, path, parsed, line_ending
        )
        return web.json_response(
            {
                "ok": True,
                "mtime": new_mtime,
                "entries": markdown_store.list_entries(parsed),
                "categories": markdown_store.list_categories(parsed),
            }
        )

    @routes.post("/lora_library/notebook/open_folder")
    async def post_notebook_open_folder(request: web.Request) -> web.Response:
        """FORMAT.md §5's ``open_folder`` row: reveal the resolved file's
        parent folder in the OS file manager on THIS machine. Unconditionally
        loopback-only like ``fs/list`` — §2's library_dir exception for
        remote callers doesn't apply, since this drives desktop UI that only
        makes sense on the server's own machine."""
        if not request_is_loopback(request):
            return error_response(
                403,
                "opening a folder only works in a browser on the machine "
                "ComfyUI runs on",
            )
        try:
            body = await request.json()
        except Exception:  # broad: malformed body is a client error
            return error_response(400, "body must be JSON")
        if not isinstance(body, dict):
            return error_response(400, "body must be a JSON object")

        path, err = _resolve_path(context, body.get("file"), writing=True)
        if err is not None:
            return err

        folder = path.parent
        if not await asyncio.to_thread(folder.is_dir):
            return error_response(404, f"no such folder {folder}")

        try:
            _reveal_folder(folder)
        except Exception as exc:  # broad: any spawn failure surfaces to the caller
            return error_response(500, str(exc))

        return web.json_response({"ok": True})
