"""Tests for ``POST /eps_image_grid/add`` (``eps_image/routes_image_grid.py``),
the M0 deliverable of ``docs/ROADMAP-image-grid-bulk-add.md``: "Write the
first HTTP test for ``POST /eps_image_grid/add`` (the route has none)."

Before this file, ``/add`` had zero HTTP-level coverage. ``tests/
test_image_grid_store.py``'s ``TestAppendUploadedImage`` class exercises
the underlying ``image_grid_store.append_uploaded_image`` directly (no
HTTP), which is a DIFFERENT thing from what's tested here: this file adds
the HTTP envelope on top -- status codes, the JSON response shape
(``{"ok", "uuid", "images"}``, note the key is ``images`` here, NOT
``refs`` like ``/list``/``/clone`` use), and -- most importantly -- the
route's OWN ``is_valid_grid_uuid`` gate (``routes_image_grid.py:75-77``),
which the store-level tests never touch since they call
``append_uploaded_image`` directly and skip the route entirely.

Fixtures mirror ``tests/test_image_grid_store.py``'s ``fake_folder_paths``/
``fake_input_dir`` (NOT ``tests/test_image_grid.py``'s partial, output-only
``fake_folder_paths`` -- ``/add`` needs ``get_directory_by_type``/
``get_input_directory`` too, to resolve an already-"uploaded" source file),
and the aiohttp test client wiring mirrors ``test_image_grid.py``'s own
``client`` fixture (``routes_image_grid.build_routes()`` in a throwaway
``aiohttp.web.Application`` -- no ComfyUI needed).

**Dependency note (differs from every sibling image-grid test file):** this
module guards on ``pytest.importorskip("PIL")``, NOT ``"torch"``. Unlike
``append_batch`` (M1, tensor batches), ``append_uploaded_image`` (M2, the
path ``/add`` calls) never imports torch at all -- only PIL, lazily, to
re-encode whatever was already uploaded to disk. Requiring torch here would
be strictly wrong (the M0 brief that spawned this file says as much: "Tests
must not require torch"). **Also discovered while writing this file: in the
repo's own ``./venv``, PIL is NOT installed either** (only pytest/aiohttp/
ruff are -- confirmed via direct ``import PIL`` and ``pip list``), despite
that venv's ``pytest.importorskip("torch")``-guarded siblings
(``test_image_grid.py``, ``test_image_grid_store.py``) being counted among
the "793 tests green" as SKIPPED, not run (``pytest -rs`` shows
``SKIPPED ... could not import 'torch'`` for both). So under ``./venv``
today, this file's PIL-dependent tests also skip -- consistent with, not a
regression from, that pre-existing gap. They run for real wherever PIL is
importable (any actual ComfyUI install, since Pillow is one of ComfyUI's
own hard dependencies).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PIL")

import io
import sys
import time
import types

from aiohttp import web
from PIL import Image

from eps_image import image_grid_store as store
from eps_image import routes_image_grid

VALID_UUID = "a1b2c3d4-e5f6-47a8-9b0c-d1e2f3a4b5c6"


@pytest.fixture
def fake_folder_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Mirrors ``test_image_grid_store.py``'s fixture of the same name
    exactly: wires ``get_output_directory``/``get_input_directory``/
    ``get_temp_directory``/``get_directory_by_type`` against sibling dirs
    under one throwaway ``tmp_path``, so ``append_uploaded_image``'s
    ``_resolve_uploaded_path`` (which needs ``get_directory_by_type``) can
    resolve a source file the same way it would against a real ComfyUI
    install. Returns the output dir, matching every other fixture of this
    name across the suite.
    """
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    dirs_by_type = {"output": str(output_dir), "input": str(input_dir), "temp": str(temp_dir)}

    fake_module = types.ModuleType("folder_paths")
    fake_module.get_output_directory = lambda: str(output_dir)
    fake_module.get_input_directory = lambda: str(input_dir)
    fake_module.get_temp_directory = lambda: str(temp_dir)
    fake_module.get_directory_by_type = lambda type_name: dirs_by_type.get(type_name)
    monkeypatch.setitem(sys.modules, "folder_paths", fake_module)
    return output_dir


@pytest.fixture
def fake_input_dir(fake_folder_paths: Path, tmp_path: Path) -> Path:
    """The sibling ``input`` dir ``fake_folder_paths`` also sets up -- the
    default resolution target for a plain paste/upload (``source_type``
    defaults to ``"input"`` on both the route and the store)."""
    return tmp_path / "input"


@pytest.fixture
async def client(fake_folder_paths: Path, aiohttp_client):
    """A plain aiohttp test client wired to just this module's routes (no
    ComfyUI) -- mirrors ``test_image_grid.py``'s own ``client`` fixture.
    Depends on ``fake_folder_paths`` so every route handler's calls into
    ``image_grid_store`` resolve under the same throwaway ``tmp_path`` a
    test can also poke directly via the ``store`` import above.
    """
    app = web.Application()
    app.add_routes(routes_image_grid.build_routes())
    return await aiohttp_client(app)


def _write_fake_upload(
    directory: Path, filename: str, *, color: tuple[int, int, int] = (10, 20, 30), size=(5, 3)
) -> Path:
    """Writes a small real PNG at *directory*/*filename*, standing in for
    whatever ``POST /upload/image`` would have already placed there before
    ``POST /eps_image_grid/add`` is called (mirrors ``test_image_grid_store
    .py``'s identically-purposed helper). *color* defaults to an arbitrary
    flat fill; override it to make a specific upload identifiable later by
    its pixel value (e.g. to confirm append ORDER, not just count).
    """
    width, height = size
    image = Image.new("RGB", (width, height), color)
    path = directory / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        image.save(fh, format="PNG")
    return path


# ------------------------------------------------------------ happy path


class TestAddRouteHappyPath:
    async def test_add_grows_the_buffer_by_one_with_the_ref_shape(
        self, client, fake_input_dir: Path
    ) -> None:
        _write_fake_upload(fake_input_dir, "pasted.png")

        before = await client.get("/eps_image_grid/list", params={"uuid": VALID_UUID})
        assert (await before.json())["refs"] == []  # nothing buffered yet

        response = await client.post(
            "/eps_image_grid/add",
            json={"uuid": VALID_UUID, "filename": "pasted.png", "subfolder": "", "type": "input"},
        )
        assert response.status == 200
        body = await response.json()
        assert body["ok"] is True
        assert body["uuid"] == VALID_UUID
        assert len(body["images"]) == 1  # grew by one, from the 0 confirmed above

        [ref] = body["images"]
        # `mtime` joined the HTTP ref shape 2026-08-21 (perf round: the
        # per-frame cache key, `store.with_frame_mtimes`); the store's own
        # `ui.images` triple is unchanged -- see TestRefsCarryPerFrameMtime.
        assert set(ref.keys()) == {"filename", "subfolder", "type", "mtime"}
        assert ref["filename"] == "0001.png"
        assert ref["subfolder"] == f"{store.DIRNAME}/{VALID_UUID}"
        # NB, easy to conflate: the REQUEST's "type" (above) is the SOURCE
        # type -- where to go read the already-uploaded file FROM (here,
        # "input"). The RESPONSE ref's "type" (below) is always "output" --
        # where the buffer itself lives -- regardless of the source type,
        # because `_refs_for` (image_grid_store.py:209-215) hard-codes
        # `OUTPUT_TYPE`. Pinning this so it isn't "fixed" into matching the
        # request by accident later.
        assert ref["type"] == "output"

    async def test_add_response_matches_a_follow_up_list_call(
        self, client, fake_input_dir: Path
    ) -> None:
        _write_fake_upload(fake_input_dir, "pasted.png")
        response = await client.post(
            "/eps_image_grid/add", json={"uuid": VALID_UUID, "filename": "pasted.png"}
        )
        added_images = (await response.json())["images"]

        listing = await client.get("/eps_image_grid/list", params={"uuid": VALID_UUID})
        assert (await listing.json())["refs"] == added_images


# --------------------------------------------------------------- append order


class TestAddRouteAppendOrder:
    async def test_three_sequential_adds_list_in_append_order(
        self, client, fake_input_dir: Path, fake_folder_paths: Path
    ) -> None:
        # Three distinct colors so order can be confirmed by CONTENT, not
        # just by the naming scheme (0001/0002/0003 is always sequential
        # regardless of upload order -- `_next_frame_filename` just counts).
        _write_fake_upload(fake_input_dir, "first.png", color=(255, 0, 0))
        _write_fake_upload(fake_input_dir, "second.png", color=(0, 255, 0))
        _write_fake_upload(fake_input_dir, "third.png", color=(0, 0, 255))

        for filename in ("first.png", "second.png", "third.png"):
            response = await client.post(
                "/eps_image_grid/add", json={"uuid": VALID_UUID, "filename": filename}
            )
            assert response.status == 200

        listing = await client.get("/eps_image_grid/list", params={"uuid": VALID_UUID})
        refs = (await listing.json())["refs"]
        assert [r["filename"] for r in refs] == ["0001.png", "0002.png", "0003.png"]

        # And the CONTENT landed in upload order too, not just the name.
        buffer_dir = fake_folder_paths / store.DIRNAME / VALID_UUID
        colors = []
        for name in ("0001.png", "0002.png", "0003.png"):
            with Image.open(buffer_dir / name) as decoded:
                colors.append(decoded.convert("RGB").getpixel((0, 0)))
        assert colors == [(255, 0, 0), (0, 255, 0), (0, 0, 255)]


# ------------------------------------------------------- soft-fail contract


class TestAddRouteSoftFailOnMissingSource:
    """``image_grid_store.py:306-359`` (``append_uploaded_image``)
    docstring: "Never raises" -- an unreadable/missing source file is a
    silent no-op that returns the buffer UNCHANGED, over HTTP 200 (not a
    4xx/5xx). This is load-bearing for M2 (roadmap "Detect silent skips"):
    per the roadmap, the frontend can only notice a per-file failure by
    comparing ``result.images.length`` before vs. after each POST -- there
    is no error field and no non-200 status to key off instead.
    """

    async def test_nonexistent_source_file_is_200_with_buffer_unchanged(
        self, client, fake_input_dir: Path
    ) -> None:
        _write_fake_upload(fake_input_dir, "real.png")
        first = await client.post(
            "/eps_image_grid/add", json={"uuid": VALID_UUID, "filename": "real.png"}
        )
        before = (await first.json())["images"]
        assert len(before) == 1

        response = await client.post(
            "/eps_image_grid/add",
            json={"uuid": VALID_UUID, "filename": "never-uploaded.png"},
        )
        assert response.status == 200  # soft-fail, NOT an error status
        after = (await response.json())["images"]
        assert after == before  # buffer truly unchanged, not merely same length


# ------------------------------------------------------------- invalid uuid


class TestAddRouteInvalidUuid:
    """**Contradicts the M0 task brief's assumption** ("invalid uuid -> 200
    with images: []"). What actually happens: the route's own
    ``is_valid_grid_uuid`` gate (``routes_image_grid.py:75-77``, identical
    to ``/list``'s at ``:94-96`` and ``/clone``'s at ``:111-115``) rejects
    an invalid uuid with **400** BEFORE ``append_uploaded_image`` is ever
    called -- the store function's own "``[]`` for an invalid uuid"
    contract (``image_grid_store.py:328-331``) is real, but it is never
    reached over HTTP, because the route never gets that far. Pinning what
    IS, matching ``test_image_grid.py``'s identical
    ``TestListRoute.test_invalid_uuid_is_400`` /
    ``TestCloneRoute.test_invalid_from_uuid_is_400`` precedent for the
    sibling routes that share this exact guard clause.
    """

    async def test_invalid_uuid_is_400_not_200(self, client) -> None:
        # No fake upload needed: the uuid gate rejects before the route
        # ever tries to resolve/read a source file at all.
        response = await client.post(
            "/eps_image_grid/add",
            json={"uuid": "not valid!", "filename": "pasted.png"},
        )
        assert response.status == 400
        assert "error" in await response.json()

    async def test_missing_uuid_key_is_also_400(self, client) -> None:
        response = await client.post("/eps_image_grid/add", json={"filename": "pasted.png"})
        assert response.status == 400


# ----------------------------------------------------- path traversal (subfolder)


class TestAddRoutePathTraversalInSubfolder:
    """Store docstring's "unresolvable source (bad type/traversal
    attempt)" branch (``image_grid_store.py:333-342``):
    ``_resolve_uploaded_path`` refuses any ``subfolder`` containing ``..``
    before ever touching the filesystem (``image_grid_store.py:290-291``),
    and ``append_uploaded_image`` then returns ``list_refs(grid_uuid)`` --
    the CURRENT buffer, unchanged -- same soft-fail shape as a missing
    file, just a different reason. No crash either way.
    """

    async def test_traversal_attempt_in_subfolder_is_200_with_buffer_unchanged(
        self, client, fake_input_dir: Path
    ) -> None:
        _write_fake_upload(fake_input_dir, "real.png")
        first = await client.post(
            "/eps_image_grid/add", json={"uuid": VALID_UUID, "filename": "real.png"}
        )
        before = (await first.json())["images"]
        assert len(before) == 1

        response = await client.post(
            "/eps_image_grid/add",
            json={"uuid": VALID_UUID, "filename": "real.png", "subfolder": "../../etc"},
        )
        assert response.status == 200  # no crash
        after = (await response.json())["images"]
        assert after == before  # unchanged -- traversal refused, not honored


# --------------------------------------------------------------------- no dedupe


class TestAddRouteNoDedupeOnReAdd:
    """``docs/ROADMAP-image-grid-bulk-add.md`` "Risks and invariants" #4:
    "No dedupe on disk -- adding the same file twice creates two frames."
    Documented, DELIBERATE behavior (the roadmap explicitly leaves
    deciding whether bulk-add SHOULD dedupe as an open question for a
    later milestone) -- ``append_uploaded_image`` always mints the next
    ``NNNN.png`` via ``_next_frame_filename`` regardless of whether
    byte-identical content is already buffered; there is no content hash
    or filename check anywhere in the path.
    """

    async def test_readding_the_same_file_creates_a_second_frame(
        self, client, fake_input_dir: Path
    ) -> None:
        _write_fake_upload(fake_input_dir, "pasted.png")
        payload = {"uuid": VALID_UUID, "filename": "pasted.png"}

        first = await client.post("/eps_image_grid/add", json=payload)
        assert len((await first.json())["images"]) == 1

        second = await client.post("/eps_image_grid/add", json=payload)
        body = await second.json()
        assert len(body["images"]) == 2  # deliberately NOT deduped
        assert [r["filename"] for r in body["images"]] == ["0001.png", "0002.png"]


# ------------------------------------------------------- /clear round-trip
# (optional per the M0 brief -- included because the fixtures above already
# make it a two-line addition; not a forced/separate setup.)


class TestClearRouteAfterAdds:
    async def test_clear_after_adds_empties_the_list(self, client, fake_input_dir: Path) -> None:
        _write_fake_upload(fake_input_dir, "a.png")
        _write_fake_upload(fake_input_dir, "b.png")
        await client.post("/eps_image_grid/add", json={"uuid": VALID_UUID, "filename": "a.png"})
        await client.post("/eps_image_grid/add", json={"uuid": VALID_UUID, "filename": "b.png"})

        listing = await client.get("/eps_image_grid/list", params={"uuid": VALID_UUID})
        assert len((await listing.json())["refs"]) == 2

        cleared = await client.post("/eps_image_grid/clear", json={"uuid": VALID_UUID})
        assert cleared.status == 200
        assert (await cleared.json())["cleared"] is True

        after = await client.get("/eps_image_grid/list", params={"uuid": VALID_UUID})
        assert (await after.json())["refs"] == []


# ------------------------------------------------- basic body validation (bonus)
# Not explicitly requested by the M0 brief's checklist, but cheap, mirrors
# /clone's identical malformed-body coverage 1:1 (TestCloneRoute in
# test_image_grid.py), and is the kind of thing "first-ever coverage of the
# HTTP route" should include. None of these reach PIL (the route 400s
# before ever calling into the store).


class TestAddRouteBasicBodyValidation:
    async def test_missing_filename_is_400(self, client) -> None:
        response = await client.post("/eps_image_grid/add", json={"uuid": VALID_UUID})
        assert response.status == 400
        assert "error" in await response.json()

    async def test_malformed_json_body_is_400(self, client) -> None:
        response = await client.post(
            "/eps_image_grid/add",
            data="not json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status == 400

    async def test_non_object_body_is_400(self, client) -> None:
        response = await client.post("/eps_image_grid/add", json=["not", "an", "object"])
        assert response.status == 400


class TestRemoveRoute:
    """POST /eps_image_grid/remove (2026-07-29, per-tile delete)."""

    async def _seed(self, client, fake_input_dir, count: int = 3) -> list[dict]:
        """Three frames via the real /add path; returns the buffer refs."""
        refs: list[dict] = []
        for i in range(count):
            name = f"seed{i}.png"
            _write_fake_upload(fake_input_dir, name, color=(50 * i, 90, 130))
            response = await client.post(
                "/eps_image_grid/add", json={"uuid": VALID_UUID, "filename": name}
            )
            refs = (await response.json())["images"]
        return refs

    async def test_removes_and_returns_remaining_buffer(
        self, client, fake_input_dir
    ) -> None:
        refs = await self._seed(client, fake_input_dir)
        target = refs[1]["filename"]
        response = await client.post(
            "/eps_image_grid/remove", json={"uuid": VALID_UUID, "filename": target}
        )
        assert response.status == 200
        data = await response.json()
        assert data["ok"] is True
        assert [r["filename"] for r in data["images"]] == [
            refs[0]["filename"],
            refs[2]["filename"],
        ]
        assert isinstance(data["generation"], int) and data["generation"] > 0

    async def test_unknown_filename_soft_noop(self, client, fake_input_dir) -> None:
        refs = await self._seed(client, fake_input_dir)
        response = await client.post(
            "/eps_image_grid/remove", json={"uuid": VALID_UUID, "filename": "9999.png"}
        )
        assert response.status == 200
        assert len((await response.json())["images"]) == len(refs)

    async def test_invalid_uuid_is_400(self, client) -> None:
        response = await client.post(
            "/eps_image_grid/remove", json={"uuid": "nope", "filename": "0001.png"}
        )
        assert response.status == 400

    async def test_missing_filename_is_400(self, client) -> None:
        response = await client.post("/eps_image_grid/remove", json={"uuid": VALID_UUID})
        assert response.status == 400


# ---------------------------------------------- per-frame mtime (2026-08-21)

OTHER_VALID_UUID = "11111111-2222-3333-4444-555555555555"


class TestRefsCarryPerFrameMtime:
    """2026-08-21 perf round: every ref-returning route decorates its refs
    with that frame FILE's own mtime (int ms -- ``store.with_frame_mtimes``),
    the frontend's stable per-image cache key. Before this, thumbnails were
    keyed on ``generation`` (the MANIFEST mtime), which moves on every
    append -- so every Collect run re-downloaded and re-encoded every
    UNCHANGED frame. The bare ``ui.images`` triple stays the STORE's
    return value (core's shape); the extra key is added at the HTTP edge."""

    async def _add(self, client, fake_input_dir: Path, name: str) -> dict:
        _write_fake_upload(fake_input_dir, name)
        response = await client.post(
            "/eps_image_grid/add", json={"uuid": VALID_UUID, "filename": name}
        )
        assert response.status == 200
        return await response.json()

    async def test_add_images_carry_a_positive_int_mtime(self, client, fake_input_dir) -> None:
        [ref] = (await self._add(client, fake_input_dir, "a.png"))["images"]
        assert isinstance(ref["mtime"], int) and ref["mtime"] > 0

    async def test_list_refs_carry_mtime_stable_across_reads(self, client, fake_input_dir) -> None:
        await self._add(client, fake_input_dir, "a.png")
        first = await (await client.get("/eps_image_grid/list", params={"uuid": VALID_UUID})).json()
        again = await (await client.get("/eps_image_grid/list", params={"uuid": VALID_UUID})).json()
        assert first["refs"][0]["mtime"] > 0
        assert first["refs"][0]["mtime"] == again["refs"][0]["mtime"]

    async def test_an_unchanged_frame_keeps_its_key_across_an_append(
        self, client, fake_input_dir
    ) -> None:
        # THE fix: frame 1's key survives frame 2's append, while the old
        # buffer-wide `generation` key (manifest mtime) moves -- which is
        # exactly why keying on it re-fetched every unchanged frame per run.
        list_params = {"uuid": VALID_UUID}
        await self._add(client, fake_input_dir, "a.png")
        before = await (await client.get("/eps_image_grid/list", params=list_params)).json()
        time.sleep(0.002)  # ms-precision keys
        await self._add(client, fake_input_dir, "b.png")
        after = await (await client.get("/eps_image_grid/list", params=list_params)).json()
        assert after["refs"][0]["mtime"] == before["refs"][0]["mtime"]
        assert after["generation"] != before["generation"]

    async def test_remove_images_carry_mtime(self, client, fake_input_dir) -> None:
        await self._add(client, fake_input_dir, "a.png")
        refs = (await self._add(client, fake_input_dir, "b.png"))["images"]
        response = await client.post(
            "/eps_image_grid/remove", json={"uuid": VALID_UUID, "filename": refs[0]["filename"]}
        )
        [remaining] = (await response.json())["images"]
        assert remaining["mtime"] == refs[1]["mtime"] > 0

    async def test_clone_refs_carry_the_destination_frames_mtime(
        self, client, fake_input_dir
    ) -> None:
        await self._add(client, fake_input_dir, "a.png")
        response = await client.post(
            "/eps_image_grid/clone", json={"from": VALID_UUID, "to": OTHER_VALID_UUID}
        )
        [ref] = (await response.json())["refs"]
        assert ref["subfolder"].endswith(OTHER_VALID_UUID)
        assert isinstance(ref["mtime"], int) and ref["mtime"] > 0


# ------------------------------------------------ GET /eps_image_grid/frame


class TestFrameRoute:
    """``GET /eps_image_grid/frame`` (2026-08-21 perf round): with
    ``preview`` a genuinely DOWNSCALED, disk-cached webp thumbnail
    (``store.thumbnail_path`` -- core's ``/view?preview=`` re-encodes the
    FULL-resolution frame, no resize); without it the full PNG frame (what
    core's Open/Save/Copy Image menu items reach after deleting ``preview``
    from ``img.src``). Validation is ``store.frame_path``'s: manifest-listed
    bare names only."""

    async def _seed(self, client, fake_input_dir: Path, *, size=(640, 400)) -> dict:
        _write_fake_upload(fake_input_dir, "big.png", size=size)
        response = await client.post(
            "/eps_image_grid/add", json={"uuid": VALID_UUID, "filename": "big.png"}
        )
        [ref] = (await response.json())["images"]
        return ref

    async def _get(self, client, ref: dict, *, preview: bool = True, keyed: bool = True, **extra):
        params = {"uuid": VALID_UUID, "filename": ref["filename"]}
        if preview:
            params["preview"] = "webp;80"
        if keyed:
            params["v"] = str(ref["mtime"])
        return await client.get("/eps_image_grid/frame", params=params, **extra)

    async def test_preview_serves_a_downscaled_webp_keeping_aspect(
        self, client, fake_input_dir
    ) -> None:
        ref = await self._seed(client, fake_input_dir)
        response = await self._get(client, ref)
        assert response.status == 200
        assert response.headers["Content-Type"].startswith("image/webp")
        with Image.open(io.BytesIO(await response.read())) as img:
            assert img.format == "WEBP"
            assert max(img.size) == store.THUMB_MAX_EDGE == 256
            assert img.size == (256, 160)  # 640x400, long edge bound, aspect kept

    async def test_without_preview_serves_the_full_png_frame_bytes(
        self, client, fake_input_dir
    ) -> None:
        ref = await self._seed(client, fake_input_dir)
        response = await self._get(client, ref, preview=False)
        assert response.status == 200
        assert response.headers["Content-Type"].startswith("image/png")
        body = await response.read()
        assert body == (store.buffer_dir(VALID_UUID) / ref["filename"]).read_bytes()
        with Image.open(io.BytesIO(body)) as img:
            assert img.size == (640, 400)

    async def test_keyed_url_is_immutable_unkeyed_revalidates(self, client, fake_input_dir) -> None:
        ref = await self._seed(client, fake_input_dir)
        keyed = await self._get(client, ref)
        assert keyed.headers["Cache-Control"] == "public, max-age=31536000, immutable"
        assert keyed.headers.get("ETag")
        unkeyed = await self._get(client, ref, keyed=False)
        assert unkeyed.headers["Cache-Control"] == "no-cache"
        assert unkeyed.headers.get("ETag")
        # Core's Open/Save Image delete only `preview`, so `v` survives
        # there too -- but a hand-typed keyless full-frame URL must
        # revalidate as well.
        full_unkeyed = await self._get(client, ref, preview=False, keyed=False)
        assert full_unkeyed.headers["Cache-Control"] == "no-cache"

    async def test_etag_revalidation_answers_304(self, client, fake_input_dir) -> None:
        ref = await self._seed(client, fake_input_dir)
        first = await self._get(client, ref)
        etag = first.headers["ETag"]
        again = await self._get(client, ref, headers={"If-None-Match": etag})
        assert again.status == 304

    async def test_thumbnail_is_cached_on_disk_next_to_the_buffer(
        self, client, fake_input_dir
    ) -> None:
        ref = await self._seed(client, fake_input_dir)
        thumb = store.buffer_dir(VALID_UUID) / store.THUMBS_DIRNAME / f"{ref['filename']}.webp"
        assert not thumb.exists()
        await self._get(client, ref)
        assert thumb.is_file()
        stamp = thumb.stat().st_mtime_ns
        await self._get(client, ref)  # a second request reuses it (no rewrite)
        assert thumb.stat().st_mtime_ns == stamp

    async def test_unlisted_name_is_404_even_if_a_file_exists(self, client, fake_input_dir) -> None:
        ref = await self._seed(client, fake_input_dir)
        stray = store.buffer_dir(VALID_UUID) / "stray.png"
        stray.write_bytes((store.buffer_dir(VALID_UUID) / ref["filename"]).read_bytes())
        for name in ("9999.png", "stray.png", store.MANIFEST_FILENAME):
            response = await client.get(
                "/eps_image_grid/frame",
                params={"uuid": VALID_UUID, "filename": name, "preview": "webp;80"},
            )
            assert response.status == 404, name
            response = await client.get(
                "/eps_image_grid/frame", params={"uuid": VALID_UUID, "filename": name}
            )
            assert response.status == 404, name

    async def test_traversal_and_dotfiles_are_404(self, client, fake_input_dir) -> None:
        await self._seed(client, fake_input_dir)
        for name in ("../manifest.json", "..", ".", "sub/0001.png", "sub\\0001.png", ".thumbs"):
            response = await client.get(
                "/eps_image_grid/frame", params={"uuid": VALID_UUID, "filename": name}
            )
            assert response.status == 404, name

    async def test_invalid_uuid_is_400_and_missing_filename_is_400(self, client) -> None:
        response = await client.get(
            "/eps_image_grid/frame", params={"uuid": "nope!", "filename": "0001.png"}
        )
        assert response.status == 400
        response = await client.get("/eps_image_grid/frame", params={"uuid": VALID_UUID})
        assert response.status == 400

    async def test_frame_file_gone_from_disk_is_404_never_500(self, client, fake_input_dir) -> None:
        ref = await self._seed(client, fake_input_dir)
        (store.buffer_dir(VALID_UUID) / ref["filename"]).unlink()  # manifest still lists it
        assert (await self._get(client, ref)).status == 404
        assert (await self._get(client, ref, preview=False)).status == 404

    async def test_remove_deletes_the_cached_thumbnail(self, client, fake_input_dir) -> None:
        ref = await self._seed(client, fake_input_dir)
        await self._get(client, ref)
        thumb = store.buffer_dir(VALID_UUID) / store.THUMBS_DIRNAME / f"{ref['filename']}.webp"
        assert thumb.is_file()
        await client.post(
            "/eps_image_grid/remove", json={"uuid": VALID_UUID, "filename": ref["filename"]}
        )
        assert not thumb.exists()

    async def test_clear_wipes_thumbnails_with_the_buffer(self, client, fake_input_dir) -> None:
        ref = await self._seed(client, fake_input_dir)
        await self._get(client, ref)
        assert (store.buffer_dir(VALID_UUID) / store.THUMBS_DIRNAME).is_dir()
        await client.post("/eps_image_grid/clear", json={"uuid": VALID_UUID})
        assert not store.buffer_dir(VALID_UUID).exists()
