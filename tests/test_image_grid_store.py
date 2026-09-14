"""Tests for eps_image.image_grid_store (FORMAT.md §6.6).

``folder_paths`` (ComfyUI's own module) is faked via ``sys.modules`` --
this pack's established convention for anything ComfyUI-only (see
``__init__.py``'s own ``_build_context``, and this file's
``fake_folder_paths`` fixture) -- so the store's ``_base_dir()`` resolves
under a throwaway ``tmp_path`` instead of a real ComfyUI install.
``torch``/``numpy``/``PIL`` are all really installed in this dev
environment, so -- like ``tests/test_resolution.py`` -- this file exercises
real tensor/PNG round trips rather than faking them.
"""

from __future__ import annotations

import itertools
import json
import sys
import threading
import time
import types
from pathlib import Path

import pytest

pytest.importorskip("torch")

import torch
from PIL import Image

from eps_image import image_grid_store as store


@pytest.fixture
def fake_folder_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Installs a fake ``folder_paths`` module whose ``get_output_directory``
    resolves to a fresh ``tmp_path`` subdirectory. Returns that directory.

    Also wires ``get_directory_by_type``/``get_input_directory``/
    ``get_temp_directory`` (M2's ``append_uploaded_image`` needs the first)
    against sibling ``input``/``temp`` dirs under the same ``tmp_path`` --
    added without changing this fixture's return value, so every M1 test
    already using it as a bare output-dir ``Path`` is unaffected.
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
    """The sibling ``input`` dir ``fake_folder_paths`` also sets up --
    depends on that fixture purely to force setup ordering."""
    return tmp_path / "input"


def _write_fake_upload(input_dir: Path, filename: str, *, size=(5, 3), fmt="PNG") -> None:
    """Writes a small real image at *input_dir* / *filename*, standing in
    for whatever ``POST /upload/image`` would have already placed there
    before ``append_uploaded_image`` is called."""
    width, height = size
    image = Image.new("RGB", (width, height), (10, 20, 30))
    path = input_dir / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        image.save(fh, format=fmt)


def _make_batch(count: int, height: int = 4, width: int = 6) -> torch.Tensor:
    """A synthetic `[B,H,W,C]` IMAGE tensor batch; each frame a distinct flat
    gray value so round-trip decode can be checked precisely."""
    frames = []
    for i in range(count):
        value = (i + 1) / (count + 1)
        frames.append(torch.full((height, width, 3), value, dtype=torch.float32))
    return torch.stack(frames, dim=0)


def _make_flat_batch(value: float, height: int = 4, width: int = 6) -> torch.Tensor:
    """A `[1,H,W,C]` batch, every pixel the SAME *value* -- the concurrency
    tests below use this so each racing thread's own contributed frame
    carries an identifiable, distinct pixel value (unlike `_make_batch`,
    which always builds several frames' worth in one call)."""
    return torch.full((1, height, width, 3), value, dtype=torch.float32)


def _write_colored_png(path: Path, gray_value: int, size: tuple[int, int] = (4, 4)) -> None:
    """Writes a flat-gray PNG at *path* -- a separate, parameterized sibling
    of `_write_fake_upload` above (which always writes the same fixed
    color) so the concurrency tests can give each simulated paste its own
    identifiable value, the upload-side equivalent of `_make_flat_batch`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, (gray_value, gray_value, gray_value))
    with open(path, "wb") as fh:
        image.save(fh, format="PNG")


def _widen_manifest_read_race(monkeypatch: pytest.MonkeyPatch, delay: float = 0.05) -> None:
    """Monkeypatches `_load_manifest` to sleep for *delay* seconds right
    AFTER reading -- deterministically widens the exact
    read-manifest -> allocate-name -> write window the pre-fix code raced
    in, so concurrently-started threads reliably land inside each other's
    window without depending on OS scheduling luck.

    Deliberately a sleep, not a `threading.Barrier` placed after the read:
    with the fix's lock in place, only ONE thread at a time can ever be
    inside `_load_manifest` for a given grid while holding that grid's
    lock, so a second thread can never reach a barrier positioned there --
    it would simply block on the LOCK first, and the test would deadlock
    waiting for a barrier party that can never arrive. A sleep has no such
    problem: it just makes whichever single thread currently holds the
    lock (post-fix) or is mid-race (pre-fix) slower, which is exactly the
    widening these tests want either way.
    """
    original = store._load_manifest

    def slow_load_manifest(directory):
        result = original(directory)
        time.sleep(delay)
        return result

    monkeypatch.setattr(store, "_load_manifest", slow_load_manifest)


VALID_UUID = "a1b2c3d4-e5f6-47a8-9b0c-d1e2f3a4b5c6"
OTHER_VALID_UUID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(autouse=True)
def _reset_mtime_cache():
    """``with_frame_mtimes``'s decorated-ref cache is process-lifetime and
    keyed on ``grid_uuid`` (2026-08-26 while-running round) -- reset it
    around every test in this file so two tests reusing ``VALID_UUID``/
    ``OTHER_VALID_UUID`` against DIFFERENT throwaway ``fake_folder_paths``
    buffers can never share a stale entry (see ``_mtime_cache_clear``'s
    docstring)."""
    store._mtime_cache_clear()
    yield
    store._mtime_cache_clear()


# --------------------------------------------------------------- uuid regex


class TestIsValidGridUuid:
    def test_accepts_a_canonical_uuid4(self) -> None:
        assert store.is_valid_grid_uuid(VALID_UUID) is True

    def test_accepts_a_minimal_8_char_hex_string(self) -> None:
        assert store.is_valid_grid_uuid("deadbeef") is True

    def test_rejects_empty_string(self) -> None:
        assert store.is_valid_grid_uuid("") is False

    def test_rejects_none(self) -> None:
        assert store.is_valid_grid_uuid(None) is False

    def test_rejects_non_string(self) -> None:
        assert store.is_valid_grid_uuid(12345) is False

    def test_rejects_path_traversal(self) -> None:
        assert store.is_valid_grid_uuid("../../etc/passwd") is False

    def test_rejects_embedded_path_separator(self) -> None:
        assert store.is_valid_grid_uuid("abc/def") is False
        assert store.is_valid_grid_uuid("abc\\def") is False

    def test_rejects_illegal_characters(self) -> None:
        assert store.is_valid_grid_uuid("uuid with spaces") is False
        assert store.is_valid_grid_uuid("uuid!@#") is False

    def test_rejects_too_short(self) -> None:
        assert store.is_valid_grid_uuid("abc123") is False

    def test_rejects_too_long(self) -> None:
        assert store.is_valid_grid_uuid("a" * 65) is False


class TestBufferDir:
    def test_invalid_uuid_returns_none(self, fake_folder_paths: Path) -> None:
        assert store.buffer_dir("../escape") is None

    def test_valid_uuid_resolves_under_the_output_dir(self, fake_folder_paths: Path) -> None:
        directory = store.buffer_dir(VALID_UUID)
        assert directory is not None
        assert directory == fake_folder_paths / store.DIRNAME / VALID_UUID


# -------------------------------------------------------------------- append


class TestAppendBatch:
    def test_invalid_uuid_is_a_safe_no_op(self, fake_folder_paths: Path) -> None:
        result = store.append_batch("bad uuid!", _make_batch(2))
        assert result == []
        assert not (fake_folder_paths / store.DIRNAME).exists() or list(
            (fake_folder_paths / store.DIRNAME).iterdir()
        ) == []

    def test_appends_b_frames_from_a_batch(self, fake_folder_paths: Path) -> None:
        refs = store.append_batch(VALID_UUID, _make_batch(3))
        assert len(refs) == 3
        directory = fake_folder_paths / store.DIRNAME / VALID_UUID
        pngs = sorted(p.name for p in directory.glob("*.png"))
        assert pngs == ["0001.png", "0002.png", "0003.png"]

    def test_manifest_records_frames_in_order(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(2))
        directory = fake_folder_paths / store.DIRNAME / VALID_UUID
        manifest = json.loads((directory / store.MANIFEST_FILENAME).read_text())
        assert manifest["frames"] == ["0001.png", "0002.png"]

    def test_refs_have_the_ui_images_shape(self, fake_folder_paths: Path) -> None:
        refs = store.append_batch(VALID_UUID, _make_batch(1))
        assert refs == [
            {
                "filename": "0001.png",
                "subfolder": f"{store.DIRNAME}/{VALID_UUID}",
                "type": "output",
            }
        ]

    def test_second_call_continues_numbering_and_returns_the_whole_buffer(
        self, fake_folder_paths: Path
    ) -> None:
        store.append_batch(VALID_UUID, _make_batch(2))
        refs = store.append_batch(VALID_UUID, _make_batch(1))
        assert [r["filename"] for r in refs] == ["0001.png", "0002.png", "0003.png"]

    def test_two_different_uuids_never_share_a_buffer(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(2))
        refs = store.append_batch(OTHER_VALID_UUID, _make_batch(1))
        assert [r["filename"] for r in refs] == ["0001.png"]
        first_refs = store.list_refs(VALID_UUID)
        assert len(first_refs) == 2


# ---------------------------------------------------------------- list_refs


class TestListRefs:
    def test_invalid_uuid_returns_empty_list(self, fake_folder_paths: Path) -> None:
        assert store.list_refs("not valid") == []

    def test_never_created_uuid_returns_empty_list(self, fake_folder_paths: Path) -> None:
        assert store.list_refs(VALID_UUID) == []

    def test_reflects_appended_frames(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(2))
        assert len(store.list_refs(VALID_UUID)) == 2


# --------------------------------------------------------- read_all_as_tensors


class TestReadAllAsTensors:
    def test_invalid_uuid_returns_empty_list(self, fake_folder_paths: Path) -> None:
        assert store.read_all_as_tensors("not valid") == []

    def test_never_created_uuid_returns_empty_list(self, fake_folder_paths: Path) -> None:
        assert store.read_all_as_tensors(VALID_UUID) == []

    def test_round_trips_the_right_count_and_shape(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(3, height=8, width=10))
        tensors = store.read_all_as_tensors(VALID_UUID)
        assert len(tensors) == 3
        for tensor in tensors:
            assert tensor.shape == (1, 8, 10, 3)

    def test_round_trips_approximate_pixel_values(self, fake_folder_paths: Path) -> None:
        # PNG is 8-bit/channel, so round-tripping a float batch through
        # append -> decode loses precision -- assert "close", not "equal".
        batch = _make_batch(1, height=4, width=4)
        store.append_batch(VALID_UUID, batch)
        [decoded] = store.read_all_as_tensors(VALID_UUID)
        assert torch.allclose(decoded, batch, atol=1.0 / 255.0 + 1e-6)

    def test_each_tensor_is_its_own_batch_of_one_never_stacked(
        self, fake_folder_paths: Path
    ) -> None:
        # Buffered frames may differ in size (FORMAT.md §6.6) -- append two
        # DIFFERENT sizes across two calls and confirm both survive as
        # independent [1,H,W,C] tensors rather than being forced together.
        store.append_batch(VALID_UUID, _make_batch(1, height=4, width=4))
        store.append_batch(VALID_UUID, _make_batch(1, height=9, width=5))
        tensors = store.read_all_as_tensors(VALID_UUID)
        assert isinstance(tensors, list)
        assert [tuple(t.shape) for t in tensors] == [(1, 4, 4, 3), (1, 9, 5, 3)]

    def test_skips_an_unreadable_frame_instead_of_raising(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(2))
        directory = fake_folder_paths / store.DIRNAME / VALID_UUID
        (directory / "0001.png").write_bytes(b"not a real png")
        tensors = store.read_all_as_tensors(VALID_UUID)
        assert len(tensors) == 1  # the corrupt frame is skipped, not fatal


# ------------------------------------------------------- read_frame_as_tensor
# (2026-08-23, owner ask: EPSImageGrid's Emit-mode `focus` narrowing --
# nodes_image_grid.py -- needs to decode exactly ONE buffered frame without
# paying to decode the whole buffer.)


class TestReadFrameAsTensor:
    def test_invalid_uuid_returns_none(self, fake_folder_paths: Path) -> None:
        assert store.read_frame_as_tensor("not valid", "0001.png") is None

    def test_unlisted_filename_returns_none(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(1))
        assert store.read_frame_as_tensor(VALID_UUID, "0099.png") is None

    def test_never_created_uuid_returns_none(self, fake_folder_paths: Path) -> None:
        assert store.read_frame_as_tensor(VALID_UUID, "0001.png") is None

    def test_path_traversal_filename_returns_none(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(1))
        assert store.read_frame_as_tensor(VALID_UUID, "../0001.png") is None

    def test_decodes_the_named_frame_with_the_right_shape(
        self, fake_folder_paths: Path
    ) -> None:
        store.append_batch(VALID_UUID, _make_batch(3, height=8, width=10))
        tensor = store.read_frame_as_tensor(VALID_UUID, "0002.png")
        assert tensor is not None
        assert tuple(tensor.shape) == (1, 8, 10, 3)

    def test_decodes_the_correct_frame_among_several_by_pixel_value(
        self, fake_folder_paths: Path
    ) -> None:
        # Each frame in `_make_batch` carries a distinct flat gray value --
        # confirm this reads FRAME 2, not frame 1 or 3, by value.
        batch = _make_batch(3, height=4, width=4)
        store.append_batch(VALID_UUID, batch)
        tensor = store.read_frame_as_tensor(VALID_UUID, "0002.png")
        assert torch.allclose(tensor, batch[1:2], atol=1.0 / 255.0 + 1e-6)

    def test_matches_read_all_as_tensors_for_the_same_frame(
        self, fake_folder_paths: Path
    ) -> None:
        store.append_batch(VALID_UUID, _make_batch(2, height=5, width=6))
        whole = store.read_all_as_tensors(VALID_UUID)
        single = store.read_frame_as_tensor(VALID_UUID, "0001.png")
        assert torch.equal(single, whole[0])

    def test_unreadable_frame_returns_none_instead_of_raising(
        self, fake_folder_paths: Path
    ) -> None:
        store.append_batch(VALID_UUID, _make_batch(1))
        directory = fake_folder_paths / store.DIRNAME / VALID_UUID
        (directory / "0001.png").write_bytes(b"not a real png")
        assert store.read_frame_as_tensor(VALID_UUID, "0001.png") is None

    def test_two_uuids_never_cross_read_each_others_frames(
        self, fake_folder_paths: Path
    ) -> None:
        store.append_batch(VALID_UUID, _make_batch(1))
        # OTHER_VALID_UUID has no buffer at all yet -- "0001.png" must not
        # resolve against it just because that name exists under a sibling.
        assert store.read_frame_as_tensor(OTHER_VALID_UUID, "0001.png") is None


# ----------------------------------------------------------------------- clear


class TestClear:
    def test_invalid_uuid_returns_false(self, fake_folder_paths: Path) -> None:
        assert store.clear("not valid") is False

    def test_never_created_uuid_returns_false(self, fake_folder_paths: Path) -> None:
        assert store.clear(VALID_UUID) is False

    def test_wipes_an_existing_buffer(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(2))
        directory = fake_folder_paths / store.DIRNAME / VALID_UUID
        assert directory.exists()

        assert store.clear(VALID_UUID) is True
        assert not directory.exists()
        assert store.list_refs(VALID_UUID) == []

    def test_clearing_one_uuid_never_touches_another(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(1))
        store.append_batch(OTHER_VALID_UUID, _make_batch(1))

        store.clear(VALID_UUID)

        assert store.list_refs(VALID_UUID) == []
        assert len(store.list_refs(OTHER_VALID_UUID)) == 1


# --------------------------------------------------------- manifest safety


class TestManifestSafety:
    def test_missing_dir_is_safe_for_every_reader(self, fake_folder_paths: Path) -> None:
        # Nothing was ever written for this uuid -- every read-side function
        # must degrade gracefully, never raise.
        assert store.list_refs(VALID_UUID) == []
        assert store.read_all_as_tensors(VALID_UUID) == []
        assert store.clear(VALID_UUID) is False

    def test_malformed_manifest_json_is_treated_as_empty(self, fake_folder_paths: Path) -> None:
        directory = fake_folder_paths / store.DIRNAME / VALID_UUID
        directory.mkdir(parents=True)
        (directory / store.MANIFEST_FILENAME).write_text("{not valid json")

        assert store.list_refs(VALID_UUID) == []
        assert store.read_all_as_tensors(VALID_UUID) == []

    def test_manifest_with_wrong_shape_is_treated_as_empty(self, fake_folder_paths: Path) -> None:
        directory = fake_folder_paths / store.DIRNAME / VALID_UUID
        directory.mkdir(parents=True)
        (directory / store.MANIFEST_FILENAME).write_text(json.dumps({"frames": "not-a-list"}))

        assert store.list_refs(VALID_UUID) == []

    def test_append_after_malformed_manifest_recovers_cleanly(
        self, fake_folder_paths: Path
    ) -> None:
        directory = fake_folder_paths / store.DIRNAME / VALID_UUID
        directory.mkdir(parents=True)
        (directory / store.MANIFEST_FILENAME).write_text("garbage")

        refs = store.append_batch(VALID_UUID, _make_batch(1))
        assert [r["filename"] for r in refs] == ["0001.png"]


# ------------------------------------------------------------ atomic writes


class TestAtomicWrites:
    def test_manifest_write_leaves_no_temp_files_behind(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(2))
        directory = fake_folder_paths / store.DIRNAME / VALID_UUID
        leftovers = list(directory.glob("*.tmp"))
        assert leftovers == []


# ------------------------------------------------------ append_uploaded_image
# (M2: the Ctrl+V/paste-to-add backend half, FORMAT.md §6.6 "Copy/paste (M2)")


class TestAppendUploadedImage:
    def test_invalid_uuid_is_a_safe_no_op(
        self, fake_folder_paths: Path, fake_input_dir: Path
    ) -> None:
        _write_fake_upload(fake_input_dir, "pasted.png")
        assert store.append_uploaded_image("not valid!", "pasted.png") == []

    def test_appends_one_frame_from_the_input_dir(
        self, fake_folder_paths: Path, fake_input_dir: Path
    ) -> None:
        _write_fake_upload(fake_input_dir, "pasted.png")
        refs = store.append_uploaded_image(VALID_UUID, "pasted.png")
        assert len(refs) == 1
        directory = fake_folder_paths / store.DIRNAME / VALID_UUID
        assert (directory / "0001.png").exists()

    def test_continues_numbering_after_a_collect_batch(
        self, fake_folder_paths: Path, fake_input_dir: Path
    ) -> None:
        store.append_batch(VALID_UUID, _make_batch(2))
        _write_fake_upload(fake_input_dir, "pasted.png")
        refs = store.append_uploaded_image(VALID_UUID, "pasted.png")
        assert [r["filename"] for r in refs] == ["0001.png", "0002.png", "0003.png"]

    def test_default_source_type_is_input(
        self, fake_folder_paths: Path, fake_input_dir: Path
    ) -> None:
        _write_fake_upload(fake_input_dir, "pasted.png")
        # No explicit source_type -- must resolve against the INPUT dir
        # (matching a plain Ctrl+V paste's /upload/image default), not output.
        refs = store.append_uploaded_image(VALID_UUID, "pasted.png")
        assert len(refs) == 1

    def test_resolves_against_a_subfolder(
        self, fake_folder_paths: Path, fake_input_dir: Path
    ) -> None:
        _write_fake_upload(fake_input_dir / "pasted", "clip.png")
        refs = store.append_uploaded_image(VALID_UUID, "clip.png", subfolder="pasted")
        assert len(refs) == 1

    def test_resolves_against_the_output_dir_when_asked(self, fake_folder_paths: Path) -> None:
        _write_fake_upload(fake_folder_paths, "from_output.png")
        refs = store.append_uploaded_image(
            VALID_UUID, "from_output.png", source_type="output"
        )
        assert len(refs) == 1

    def test_reencodes_a_non_png_source_as_canonical_png(
        self, fake_folder_paths: Path, fake_input_dir: Path
    ) -> None:
        _write_fake_upload(fake_input_dir, "pasted.jpg", fmt="JPEG")
        refs = store.append_uploaded_image(VALID_UUID, "pasted.jpg")
        assert len(refs) == 1
        directory = fake_folder_paths / store.DIRNAME / VALID_UUID
        with Image.open(directory / "0001.png") as decoded:
            assert decoded.format == "PNG"

    def test_added_frame_round_trips_through_read_all_as_tensors(
        self, fake_folder_paths: Path, fake_input_dir: Path
    ) -> None:
        _write_fake_upload(fake_input_dir, "pasted.png", size=(6, 4))
        store.append_uploaded_image(VALID_UUID, "pasted.png")
        tensors = store.read_all_as_tensors(VALID_UUID)
        assert len(tensors) == 1
        assert tensors[0].shape == (1, 4, 6, 3)

    def test_missing_source_file_returns_current_buffer_unchanged(
        self, fake_folder_paths: Path, fake_input_dir: Path
    ) -> None:
        store.append_batch(VALID_UUID, _make_batch(1))
        refs = store.append_uploaded_image(VALID_UUID, "never-uploaded.png")
        assert len(refs) == 1  # unchanged -- the missing file was never added

    def test_unknown_source_type_returns_current_buffer_unchanged(
        self, fake_folder_paths: Path, fake_input_dir: Path
    ) -> None:
        _write_fake_upload(fake_input_dir, "pasted.png")
        refs = store.append_uploaded_image(VALID_UUID, "pasted.png", source_type="bogus")
        assert refs == []  # buffer was empty and stays empty; no exception

    def test_path_traversal_in_filename_is_refused(
        self, fake_folder_paths: Path, fake_input_dir: Path
    ) -> None:
        _write_fake_upload(fake_folder_paths.parent, "escaped.png")  # outside every known dir
        refs = store.append_uploaded_image(VALID_UUID, "../escaped.png")
        assert refs == []

    def test_path_traversal_in_subfolder_is_refused(
        self, fake_folder_paths: Path, fake_input_dir: Path
    ) -> None:
        _write_fake_upload(fake_input_dir, "pasted.png")
        refs = store.append_uploaded_image(
            VALID_UUID, "pasted.png", subfolder="../../etc"
        )
        assert refs == []

    def test_two_different_uuids_never_share_an_added_frame(
        self, fake_folder_paths: Path, fake_input_dir: Path
    ) -> None:
        _write_fake_upload(fake_input_dir, "pasted.png")
        store.append_uploaded_image(VALID_UUID, "pasted.png")
        assert store.list_refs(OTHER_VALID_UUID) == []

    def test_leaves_no_temp_files_behind(
        self, fake_folder_paths: Path, fake_input_dir: Path
    ) -> None:
        _write_fake_upload(fake_input_dir, "pasted.png")
        store.append_uploaded_image(VALID_UUID, "pasted.png")
        directory = fake_folder_paths / store.DIRNAME / VALID_UUID
        assert list(directory.glob("*.tmp")) == []


class TestResolveUploadedPath:
    def test_rejects_empty_filename(self, fake_folder_paths: Path) -> None:
        assert store._resolve_uploaded_path("", "", "input") is None

    def test_rejects_filename_starting_with_slash(self, fake_folder_paths: Path) -> None:
        assert store._resolve_uploaded_path("/etc/passwd", "", "input") is None

    def test_rejects_filename_with_dot_dot(self, fake_folder_paths: Path) -> None:
        assert store._resolve_uploaded_path("../escaped.png", "", "input") is None

    def test_rejects_subfolder_with_dot_dot(self, fake_folder_paths: Path) -> None:
        assert store._resolve_uploaded_path("x.png", "../../etc", "input") is None

    def test_rejects_unknown_type(self, fake_folder_paths: Path) -> None:
        assert store._resolve_uploaded_path("x.png", "", "not-a-real-type") is None

    def test_resolves_a_plain_filename_under_input(
        self, fake_folder_paths: Path, fake_input_dir: Path
    ) -> None:
        resolved = store._resolve_uploaded_path("x.png", "", "input")
        assert resolved == fake_input_dir / "x.png"

    def test_resolves_a_filename_under_a_subfolder(
        self, fake_folder_paths: Path, fake_input_dir: Path
    ) -> None:
        resolved = store._resolve_uploaded_path("x.png", "pasted", "input")
        assert resolved == fake_input_dir / "pasted" / "x.png"


# ----------------------------------------------------------------- clone_buffer
# (2026-07-20 bug fix: FORMAT.md §6.6 "Copy carries the images, independently"
# -- the frontend calls this right after minting a fresh uuid for an in-graph
# duplicate, so the copy starts with the original's images.)


class TestCloneBuffer:
    def test_invalid_src_uuid_is_a_safe_no_op(self, fake_folder_paths: Path) -> None:
        store.append_batch(OTHER_VALID_UUID, _make_batch(1))
        assert store.clone_buffer("bad uuid!", OTHER_VALID_UUID) == []
        # The valid (dst-shaped) uuid's own pre-existing buffer is untouched.
        assert len(store.list_refs(OTHER_VALID_UUID)) == 1

    def test_invalid_dst_uuid_is_a_safe_no_op(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(2))
        assert store.clone_buffer(VALID_UUID, "bad uuid!") == []
        # Source is untouched by a rejected clone.
        assert len(store.list_refs(VALID_UUID)) == 2

    def test_both_uuids_invalid_is_a_safe_no_op(self, fake_folder_paths: Path) -> None:
        assert store.clone_buffer("nope!", "also nope!") == []

    def test_src_with_no_buffer_directory_is_a_safe_no_op(self, fake_folder_paths: Path) -> None:
        # VALID_UUID was never used -- no directory exists for it at all.
        assert store.clone_buffer(VALID_UUID, OTHER_VALID_UUID) == []
        assert store.list_refs(OTHER_VALID_UUID) == []
        dst_dir = fake_folder_paths / store.DIRNAME / OTHER_VALID_UUID
        assert not dst_dir.exists()  # nothing to clone -- dst is never created

    def test_src_with_a_dir_but_zero_frames_is_a_safe_no_op(
        self, fake_folder_paths: Path
    ) -> None:
        # A src directory that exists (e.g. left over after a Clear) but has
        # no manifest/frames -- still nothing to clone.
        src_dir = fake_folder_paths / store.DIRNAME / VALID_UUID
        src_dir.mkdir(parents=True)
        assert store.clone_buffer(VALID_UUID, OTHER_VALID_UUID) == []
        assert store.list_refs(OTHER_VALID_UUID) == []

    def test_copies_every_frame_and_the_manifest(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(3))
        refs = store.clone_buffer(VALID_UUID, OTHER_VALID_UUID)
        assert [r["filename"] for r in refs] == ["0001.png", "0002.png", "0003.png"]

        dst_dir = fake_folder_paths / store.DIRNAME / OTHER_VALID_UUID
        pngs = sorted(p.name for p in dst_dir.glob("*.png"))
        assert pngs == ["0001.png", "0002.png", "0003.png"]
        manifest = json.loads((dst_dir / store.MANIFEST_FILENAME).read_text())
        assert manifest["frames"] == ["0001.png", "0002.png", "0003.png"]

    def test_returned_refs_use_the_dst_uuid_subfolder(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(1))
        refs = store.clone_buffer(VALID_UUID, OTHER_VALID_UUID)
        assert refs == [
            {
                "filename": "0001.png",
                "subfolder": f"{store.DIRNAME}/{OTHER_VALID_UUID}",
                "type": "output",
            }
        ]

    def test_return_value_matches_list_refs_of_dst(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(2))
        refs = store.clone_buffer(VALID_UUID, OTHER_VALID_UUID)
        assert refs == store.list_refs(OTHER_VALID_UUID)

    def test_cloned_frames_round_trip_pixel_values(self, fake_folder_paths: Path) -> None:
        batch = _make_batch(1, height=5, width=5)
        store.append_batch(VALID_UUID, batch)
        store.clone_buffer(VALID_UUID, OTHER_VALID_UUID)
        [decoded] = store.read_all_as_tensors(OTHER_VALID_UUID)
        assert torch.allclose(decoded, batch, atol=1.0 / 255.0 + 1e-6)

    def test_dst_is_independent_of_a_later_append_to_src(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(2))
        store.clone_buffer(VALID_UUID, OTHER_VALID_UUID)

        store.append_batch(VALID_UUID, _make_batch(1))  # src grows to 3
        assert len(store.list_refs(VALID_UUID)) == 3
        assert len(store.list_refs(OTHER_VALID_UUID)) == 2  # dst untouched

    def test_dst_is_independent_of_a_later_clear_of_src(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(2))
        store.clone_buffer(VALID_UUID, OTHER_VALID_UUID)

        assert store.clear(VALID_UUID) is True
        assert store.list_refs(VALID_UUID) == []
        assert len(store.list_refs(OTHER_VALID_UUID)) == 2  # dst untouched

    def test_src_is_independent_of_a_later_append_to_dst(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(2))
        store.clone_buffer(VALID_UUID, OTHER_VALID_UUID)

        store.append_batch(OTHER_VALID_UUID, _make_batch(5))  # dst grows to 7
        assert len(store.list_refs(OTHER_VALID_UUID)) == 7
        assert len(store.list_refs(VALID_UUID)) == 2  # src untouched

    def test_skips_a_frame_missing_from_disk_but_copies_the_rest(
        self, fake_folder_paths: Path
    ) -> None:
        store.append_batch(VALID_UUID, _make_batch(2))
        src_dir = fake_folder_paths / store.DIRNAME / VALID_UUID
        (src_dir / "0001.png").unlink()  # simulate a corrupted/hand-deleted frame

        refs = store.clone_buffer(VALID_UUID, OTHER_VALID_UUID)
        assert [r["filename"] for r in refs] == ["0002.png"]

    def test_leaves_no_temp_files_behind(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(2))
        store.clone_buffer(VALID_UUID, OTHER_VALID_UUID)
        dst_dir = fake_folder_paths / store.DIRNAME / OTHER_VALID_UUID
        assert list(dst_dir.glob("*.tmp")) == []

    def test_does_not_mutate_the_source_buffer(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(2))
        before = store.list_refs(VALID_UUID)
        store.clone_buffer(VALID_UUID, OTHER_VALID_UUID)
        assert store.list_refs(VALID_UUID) == before


class TestBufferGeneration:
    """The bulk-add cache token (2026-07-29) -- see buffer_generation's
    docstring for why it exists: Clear is an rmtree and frame numbering
    restarts, so ``0001.png`` is REUSED with different pixels after a
    Clear + re-add. Display URLs carry this value as ``v=`` so they're
    stable (cacheable) within a generation and fresh across one."""

    def test_zero_for_invalid_uuid(self, fake_folder_paths: Path) -> None:
        assert store.buffer_generation("not-a-uuid") == 0

    def test_zero_before_any_append(self, fake_folder_paths: Path) -> None:
        assert store.buffer_generation(VALID_UUID) == 0

    def test_nonzero_after_append_and_stable_across_reads(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(1))
        first = store.buffer_generation(VALID_UUID)
        assert first > 0
        assert store.buffer_generation(VALID_UUID) == first  # pure read: stable

    def test_changes_when_a_cleared_frame_name_is_reused(self, fake_folder_paths: Path) -> None:
        # THE scenario the token exists for: 0001.png reused with new pixels.
        store.append_batch(VALID_UUID, _make_batch(1))
        gen_before = store.buffer_generation(VALID_UUID)
        first_name = store.list_refs(VALID_UUID)[0]["filename"]

        import time

        time.sleep(0.002)  # mtime resolution guard (ms-precision token)
        store.clear(VALID_UUID)
        store.append_batch(VALID_UUID, _make_batch(1))

        assert store.list_refs(VALID_UUID)[0]["filename"] == first_name  # name reuse is REAL
        assert store.buffer_generation(VALID_UUID) != gen_before  # token catches it


class TestRemoveFrame:
    """Per-tile delete (2026-07-29, owner: "you get a duplicate image and
    then the grid is useless and you have to start fresh")."""

    def test_removes_one_frame_and_keeps_order(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(3))
        refs = store.list_refs(VALID_UUID)
        middle = refs[1]["filename"]
        remaining = store.remove_frame(VALID_UUID, middle)
        assert [r["filename"] for r in remaining] == [refs[0]["filename"], refs[2]["filename"]]
        assert store.list_refs(VALID_UUID) == remaining

    def test_frame_file_is_gone_but_manifest_is_the_truth(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(1))
        name = store.list_refs(VALID_UUID)[0]["filename"]
        directory = store.buffer_dir(VALID_UUID)
        assert (directory / name).is_file()
        store.remove_frame(VALID_UUID, name)
        assert not (directory / name).is_file()

    def test_unknown_filename_is_a_soft_noop(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(2))
        before = store.list_refs(VALID_UUID)
        assert store.remove_frame(VALID_UUID, "9999.png") == before

    def test_invalid_uuid_returns_empty(self, fake_folder_paths: Path) -> None:
        assert store.remove_frame("nope", "0001.png") == []

    def test_numbering_never_reuses_a_deleted_middle_name(self, fake_folder_paths: Path) -> None:
        # _next_frame_filename is one-past-the-highest-EXISTING -- deleting
        # 0002 then appending must yield 0004, never a second 0002.
        store.append_batch(VALID_UUID, _make_batch(3))
        store.remove_frame(VALID_UUID, "0002.png")
        store.append_batch(VALID_UUID, _make_batch(1))
        names = [r["filename"] for r in store.list_refs(VALID_UUID)]
        assert names == ["0001.png", "0003.png", "0004.png"]

    def test_generation_advances_on_remove(self, fake_folder_paths: Path) -> None:
        import time

        store.append_batch(VALID_UUID, _make_batch(2))
        gen_before = store.buffer_generation(VALID_UUID)
        time.sleep(0.002)
        store.remove_frame(VALID_UUID, store.list_refs(VALID_UUID)[0]["filename"])
        assert store.buffer_generation(VALID_UUID) != gen_before


class TestCorruptPngSoftFail:
    def test_corrupt_png_source_is_a_soft_skip_not_a_crash(
        self, fake_folder_paths: Path, tmp_path: Path
    ) -> None:
        """PIL's PngImagePlugin raises a PLAIN SyntaxError on a truncated
        PNG -- not OSError/ValueError -- which used to escape the catch and
        500 the /add route despite the documented "never raises" contract
        (found live 2026-07-29). Pin the widened catch."""
        import folder_paths  # the fixture's fake

        input_dir = Path(folder_paths.get_input_directory())
        bad = input_dir / "corrupt.png"
        bad.write_bytes(b"\x89PNG\r\n\x1a\n" + b"this is not a real png body")
        store.append_batch(VALID_UUID, _make_batch(1))
        before = store.list_refs(VALID_UUID)
        result = store.append_uploaded_image(VALID_UUID, "corrupt.png", "", "input")
        assert result == before  # unchanged buffer, no exception


# --------------------------------------- frame files + thumbnails (2026-08-21)


class TestFramePath:
    """``frame_path`` (2026-08-21 perf round): the manifest-listed-only gate
    in front of ``GET /eps_image_grid/frame`` -- the same rule ``remove_frame``
    applies, plus a bare-single-segment check, so no query value can ever
    name anything the manifest doesn't own."""

    def test_resolves_a_listed_frame(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(2))
        path = store.frame_path(VALID_UUID, "0002.png")
        assert path == store.buffer_dir(VALID_UUID) / "0002.png"
        assert path.is_file()

    def test_unlisted_name_is_none_even_if_the_file_exists(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(1))
        (store.buffer_dir(VALID_UUID) / "stray.png").write_bytes(b"not ours")
        assert store.frame_path(VALID_UUID, "stray.png") is None
        assert store.frame_path(VALID_UUID, store.MANIFEST_FILENAME) is None

    def test_refuses_separators_dot_segments_and_dotfiles(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(1))
        for bad in ("../0001.png", "..", ".", "sub/0001.png", "sub\\0001.png", ".thumbs", "", None):
            assert store.frame_path(VALID_UUID, bad) is None, bad

    def test_invalid_uuid_is_none(self, fake_folder_paths: Path) -> None:
        assert store.frame_path("nope", "0001.png") is None

    def test_listed_but_missing_file_is_none(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(1))
        (store.buffer_dir(VALID_UUID) / "0001.png").unlink()
        assert store.frame_path(VALID_UUID, "0001.png") is None


class TestWithFrameMtimes:
    """``with_frame_mtimes`` (2026-08-21): the per-frame cache key. A frame
    FILE's mtime moves only when THAT file is written; ``buffer_generation``
    (the manifest mtime) moves on every append -- keying thumbnails on it
    re-fetched every UNCHANGED frame after every run."""

    def test_adds_int_mtime_without_mutating_the_input(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(2))
        refs = store.list_refs(VALID_UUID)
        decorated = store.with_frame_mtimes(VALID_UUID, refs)
        assert [set(r) for r in decorated] == [{"filename", "subfolder", "type", "mtime"}] * 2
        assert all(isinstance(r["mtime"], int) and r["mtime"] > 0 for r in decorated)
        assert [set(r) for r in refs] == [{"filename", "subfolder", "type"}] * 2  # untouched
        assert [r["filename"] for r in decorated] == [r["filename"] for r in refs]

    def test_unchanged_frame_keeps_its_mtime_across_an_append(
        self, fake_folder_paths: Path
    ) -> None:
        import time

        store.append_batch(VALID_UUID, _make_batch(1))
        first = store.with_frame_mtimes(VALID_UUID, store.list_refs(VALID_UUID))[0]["mtime"]
        generation_before = store.buffer_generation(VALID_UUID)
        time.sleep(0.002)  # ms-precision keys
        store.append_batch(VALID_UUID, _make_batch(1))
        again = store.with_frame_mtimes(VALID_UUID, store.list_refs(VALID_UUID))
        assert again[0]["mtime"] == first  # THE fix: frame 1's key survives frame 2
        assert store.buffer_generation(VALID_UUID) != generation_before  # the old key moved

    def test_a_reused_name_after_clear_gets_a_new_mtime(self, fake_folder_paths: Path) -> None:
        import time

        store.append_batch(VALID_UUID, _make_batch(1))
        first = store.with_frame_mtimes(VALID_UUID, store.list_refs(VALID_UUID))[0]["mtime"]
        time.sleep(0.002)
        store.clear(VALID_UUID)
        store.append_batch(VALID_UUID, _make_batch(1))
        [ref] = store.with_frame_mtimes(VALID_UUID, store.list_refs(VALID_UUID))
        assert ref["filename"] == "0001.png"  # name reuse is REAL (the generation token's case)
        assert ref["mtime"] != first  # ...and the per-frame key still catches it

    def test_missing_file_and_invalid_uuid_give_zero(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(1))
        refs = store.list_refs(VALID_UUID)
        (store.buffer_dir(VALID_UUID) / "0001.png").unlink()
        assert store.with_frame_mtimes(VALID_UUID, refs)[0]["mtime"] == 0
        foreign = store.with_frame_mtimes("nope", refs)
        assert foreign[0]["mtime"] == 0
        assert foreign[0]["filename"] == "0001.png"  # the rest of the ref is preserved


def _count_frame_mtime_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Patches ``_frame_mtime_ms`` (the per-frame ``stat()`` helper
    ``with_frame_mtimes``'s loop calls once per ref) to record every path
    it's asked to stat, still delegating to the real implementation -- the
    exact O(N) work the cache below is meant to skip entirely on a hit."""
    calls: list[str] = []
    original = store._frame_mtime_ms

    def counting(path):
        calls.append(str(path))
        return original(path)

    monkeypatch.setattr(store, "_frame_mtime_ms", counting)
    return calls


class TestWithFrameMtimesCache:
    """2026-08-26 while-running round: ``with_frame_mtimes`` itself stat'd
    EVERY frame on EVERY call -- an O(N) filesystem pass that fires once per
    FINISHED RUN during a sweep (every ref-returning route calls it). Now
    cached per ``grid_uuid``, keyed on :func:`store.buffer_generation`: an
    unchanged buffer skips the stat loop entirely, and every write op drops
    that uuid's cached entry so a stale decoration can never be served."""

    # --------------------------------------------------------- hit skips stats

    def test_a_second_call_on_an_unchanged_buffer_makes_no_new_stat_calls(
        self, fake_folder_paths: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store.append_batch(VALID_UUID, _make_batch(3))
        refs = store.list_refs(VALID_UUID)
        first = store.with_frame_mtimes(VALID_UUID, refs)  # primes the cache

        calls = _count_frame_mtime_calls(monkeypatch)
        second = store.with_frame_mtimes(VALID_UUID, refs)

        assert second == first
        assert calls == []  # cache hit -- the stat loop never ran

    def test_the_cache_hit_returns_the_same_list_object(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(1))
        refs = store.list_refs(VALID_UUID)
        first = store.with_frame_mtimes(VALID_UUID, refs)
        second = store.with_frame_mtimes(VALID_UUID, refs)
        assert first is second  # the SAME cached list, not merely an equal one

    def test_a_fresh_uuid_with_no_prior_call_is_a_real_miss(
        self, fake_folder_paths: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store.append_batch(VALID_UUID, _make_batch(2))
        refs = store.list_refs(VALID_UUID)
        calls = _count_frame_mtime_calls(monkeypatch)
        store.with_frame_mtimes(VALID_UUID, refs)
        assert len(calls) == 2  # one stat per frame, nothing cached yet

    # ------------------------------------------------------- write ops invalidate

    def test_append_invalidates_so_the_next_call_re_stats(
        self, fake_folder_paths: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store.append_batch(VALID_UUID, _make_batch(1))
        store.with_frame_mtimes(VALID_UUID, store.list_refs(VALID_UUID))  # primes the cache
        assert VALID_UUID in store._mtime_cache

        store.append_batch(VALID_UUID, _make_batch(1))  # a write op
        assert VALID_UUID not in store._mtime_cache  # dropped immediately, not just on next read

        calls = _count_frame_mtime_calls(monkeypatch)
        decorated = store.with_frame_mtimes(VALID_UUID, store.list_refs(VALID_UUID))
        assert len(decorated) == 2
        assert len(calls) == 2  # a genuine miss -- it re-stat'd both frames

    def test_append_uploaded_image_invalidates(
        self, fake_folder_paths: Path, fake_input_dir: Path
    ) -> None:
        _write_fake_upload(fake_input_dir, "pasted.png")
        store.append_uploaded_image(VALID_UUID, "pasted.png")
        store.with_frame_mtimes(VALID_UUID, store.list_refs(VALID_UUID))
        assert VALID_UUID in store._mtime_cache

        _write_fake_upload(fake_input_dir, "pasted2.png")
        store.append_uploaded_image(VALID_UUID, "pasted2.png")
        assert VALID_UUID not in store._mtime_cache

    def test_remove_frame_invalidates(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(2))
        refs = store.list_refs(VALID_UUID)
        store.with_frame_mtimes(VALID_UUID, refs)
        assert VALID_UUID in store._mtime_cache

        store.remove_frame(VALID_UUID, refs[0]["filename"])
        assert VALID_UUID not in store._mtime_cache

    def test_clear_invalidates(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(1))
        store.with_frame_mtimes(VALID_UUID, store.list_refs(VALID_UUID))
        assert VALID_UUID in store._mtime_cache

        store.clear(VALID_UUID)
        assert VALID_UUID not in store._mtime_cache

    def test_clone_invalidates_only_the_destination_uuid(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(1))  # source buffer
        store.with_frame_mtimes(VALID_UUID, store.list_refs(VALID_UUID))
        assert VALID_UUID in store._mtime_cache

        # A stale entry for the destination uuid too, from before the clone
        # (e.g. it was queried as an empty buffer earlier).
        store.with_frame_mtimes(OTHER_VALID_UUID, [])
        assert OTHER_VALID_UUID in store._mtime_cache

        store.clone_buffer(VALID_UUID, OTHER_VALID_UUID)

        assert OTHER_VALID_UUID not in store._mtime_cache  # invalidated
        assert VALID_UUID in store._mtime_cache  # untouched -- clone never writes the source

    def test_a_noop_remove_does_not_spuriously_invalidate(self, fake_folder_paths: Path) -> None:
        """`remove_frame` on a filename not in the manifest is a documented
        soft-fail no-op (no manifest rewrite) -- it must not touch the
        cache at all, unlike a real removal."""
        store.append_batch(VALID_UUID, _make_batch(1))
        store.with_frame_mtimes(VALID_UUID, store.list_refs(VALID_UUID))
        assert VALID_UUID in store._mtime_cache

        store.remove_frame(VALID_UUID, "9999.png")  # not in the manifest -- soft no-op
        assert VALID_UUID in store._mtime_cache  # still cached, nothing changed

    def test_clearing_one_uuid_leaves_a_different_uuids_cache_entry_alone(
        self, fake_folder_paths: Path
    ) -> None:
        store.append_batch(VALID_UUID, _make_batch(1))
        store.with_frame_mtimes(VALID_UUID, store.list_refs(VALID_UUID))
        store.append_batch(OTHER_VALID_UUID, _make_batch(1))
        store.with_frame_mtimes(OTHER_VALID_UUID, store.list_refs(OTHER_VALID_UUID))
        assert VALID_UUID in store._mtime_cache
        assert OTHER_VALID_UUID in store._mtime_cache

        store.clear(OTHER_VALID_UUID)

        assert OTHER_VALID_UUID not in store._mtime_cache  # invalidated
        assert VALID_UUID in store._mtime_cache  # a different uuid, untouched

    # -------------------------------------------------------- small + evicted

    def test_cache_never_grows_past_the_max_and_evicts_least_recently_used(
        self, fake_folder_paths: Path
    ) -> None:
        assert store._MTIME_CACHE_MAX_UUIDS > 0
        count = store._MTIME_CACHE_MAX_UUIDS + 1
        uuids = [f"{i:08x}-0000-4000-8000-000000000000" for i in range(count)]
        for grid_uuid in uuids:
            store.append_batch(grid_uuid, _make_batch(1))
            store.with_frame_mtimes(grid_uuid, store.list_refs(grid_uuid))

        assert len(store._mtime_cache) == store._MTIME_CACHE_MAX_UUIDS
        assert uuids[0] not in store._mtime_cache  # the least-recently-used entry was evicted
        assert uuids[-1] in store._mtime_cache  # the most recent one survives


class TestThumbnailPath:
    """``thumbnail_path`` (2026-08-21): a genuinely DOWNSCALED, disk-cached
    webp per frame under ``<buffer>/.thumbs/`` -- core's ``/view?preview=``
    re-encodes the FULL-resolution frame (no resize), which is what made a
    100-frame grid 100 full-res draws per repaint."""

    def _thumb(self, name: str = "0001.png") -> Path:
        return store.buffer_dir(VALID_UUID) / store.THUMBS_DIRNAME / f"{name}.webp"

    def test_builds_a_downscaled_webp_in_dot_thumbs(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(1, height=100, width=400))
        thumb = store.thumbnail_path(VALID_UUID, "0001.png", max_edge=128)
        assert thumb == self._thumb()
        with Image.open(thumb) as img:
            assert img.format == "WEBP"
            assert img.size == (128, 32)  # long edge bound, aspect kept

    def test_default_bound_is_thumb_max_edge(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(1, height=300, width=600))
        with Image.open(store.thumbnail_path(VALID_UUID, "0001.png")) as img:
            assert store.THUMB_MAX_EDGE == 256
            assert img.size == (256, 128)

    def test_never_upscales_a_small_frame(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(1, height=4, width=6))
        with Image.open(store.thumbnail_path(VALID_UUID, "0001.png")) as img:
            assert img.size == (6, 4)

    def test_cached_while_the_source_is_unchanged(self, fake_folder_paths: Path) -> None:
        import time

        store.append_batch(VALID_UUID, _make_batch(1, height=50, width=50))
        source = store.buffer_dir(VALID_UUID) / "0001.png"
        first = store.thumbnail_path(VALID_UUID, "0001.png")
        stamp = first.stat().st_mtime_ns
        assert stamp == source.stat().st_mtime_ns  # stamped with the SOURCE's mtime
        data = first.read_bytes()
        time.sleep(0.002)
        again = store.thumbnail_path(VALID_UUID, "0001.png")
        assert again == first
        assert again.stat().st_mtime_ns == stamp  # not regenerated
        assert again.read_bytes() == data

    def test_regenerated_when_the_name_is_reused_after_clear(self, fake_folder_paths: Path) -> None:
        import time

        store.append_batch(VALID_UUID, _make_batch(1, height=50, width=50))
        old_stamp = store.thumbnail_path(VALID_UUID, "0001.png").stat().st_mtime_ns
        time.sleep(0.002)
        store.clear(VALID_UUID)
        assert not self._thumb().exists()  # clear took the thumbs with it
        store.append_batch(VALID_UUID, _make_batch(1, height=50, width=50))
        new_thumb = store.thumbnail_path(VALID_UUID, "0001.png")
        assert new_thumb.stat().st_mtime_ns != old_stamp
        new_source = store.buffer_dir(VALID_UUID) / "0001.png"
        assert new_thumb.stat().st_mtime_ns == new_source.stat().st_mtime_ns

    def test_unlisted_or_invalid_is_none(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(1))
        assert store.thumbnail_path(VALID_UUID, "9999.png") is None
        assert store.thumbnail_path(VALID_UUID, "../0001.png") is None
        assert store.thumbnail_path("nope", "0001.png") is None
        assert not (store.buffer_dir(VALID_UUID) / store.THUMBS_DIRNAME).exists()

    def test_unreadable_frame_degrades_to_none_not_an_exception(
        self, fake_folder_paths: Path
    ) -> None:
        store.append_batch(VALID_UUID, _make_batch(1))
        (store.buffer_dir(VALID_UUID) / "0001.png").write_bytes(b"\x89PNG\r\n\x1a\ntruncated")
        assert store.thumbnail_path(VALID_UUID, "0001.png") is None

    def test_remove_frame_deletes_the_thumbnail(self, fake_folder_paths: Path) -> None:
        store.append_batch(VALID_UUID, _make_batch(2))
        store.thumbnail_path(VALID_UUID, "0001.png")
        store.thumbnail_path(VALID_UUID, "0002.png")
        store.remove_frame(VALID_UUID, "0001.png")
        assert not self._thumb("0001.png").exists()
        assert self._thumb("0002.png").is_file()

    def test_thumbs_dir_is_invisible_to_every_manifest_walker(
        self, fake_folder_paths: Path
    ) -> None:
        store.append_batch(VALID_UUID, _make_batch(2))
        store.thumbnail_path(VALID_UUID, "0001.png")
        store.thumbnail_path(VALID_UUID, "0002.png")
        assert [r["filename"] for r in store.list_refs(VALID_UUID)] == ["0001.png", "0002.png"]
        store.append_batch(VALID_UUID, _make_batch(1))  # numbering unaffected
        assert store.list_refs(VALID_UUID)[-1]["filename"] == "0003.png"
        assert len(store.read_all_as_tensors(VALID_UUID)) == 3
        store.clone_buffer(VALID_UUID, OTHER_VALID_UUID)
        assert not (store.buffer_dir(OTHER_VALID_UUID) / store.THUMBS_DIRNAME).exists()
        assert len(store.list_refs(OTHER_VALID_UUID)) == 3


# --------------------------------------------------- concurrency (2026-09-13)
# Data-loss fix, RELEASE-REVIEW-2026-09-13.md finding #2: every mutator here
# used to read manifest.json -> allocate a name -> write the image(s) ->
# `os.replace` the manifest with NO lock at all. Two concurrent callers of
# the SAME grid (a workflow Run's `append_batch` on ComfyUI's executor
# thread racing a browser paste/remove/clear/clone via `asyncio.to_thread`
# in `routes_image_grid.py`, or two overlapping Runs of the same node) could
# both read the same manifest, both allocate the SAME next filename, and one
# image + its manifest entry would silently vanish while BOTH callers
# reported success. `image_grid_store.py`'s `_grid_locks`/`_locked` fix this
# with a per-grid `threading.RLock` held across that whole sequence -- these
# tests reproduce the race (widened deterministically via
# `_widen_manifest_read_race`, never a `threading.Barrier` placed after the
# read -- see that helper's own docstring for why a barrier there would
# deadlock once the fix is in place) and check it's actually closed.


class TestAppendBatchConcurrency:
    def test_n_threads_append_batch_concurrently_all_frames_survive_with_correct_pixels(
        self, fake_folder_paths: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        thread_count = 12
        _widen_manifest_read_race(monkeypatch)
        expected_values = [(i + 1) / (thread_count + 2) for i in range(thread_count)]
        errors: list[BaseException] = []

        def worker(value: float) -> None:
            try:
                store.append_batch(VALID_UUID, _make_flat_batch(value))
            except BaseException as exc:  # pragma: no cover - surfaced via `errors`
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(v,)) for v in expected_values]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"append_batch raised in a worker thread: {errors}"

        refs = store.list_refs(VALID_UUID)
        names = [r["filename"] for r in refs]
        assert len(names) == thread_count, (
            f"expected all {thread_count} concurrent appends to survive, got "
            f"{len(names)} surviving frame(s): {names}"
        )
        assert len(set(names)) == thread_count  # every filename unique -- no clobbered writer

        directory = store.buffer_dir(VALID_UUID)
        for name in names:
            assert (directory / name).is_file(), f"manifest lists {name!r} but its file is missing"

        # Content check, not just presence: confirms no two threads' writes
        # landed on the same filename and clobbered each other's pixels --
        # the exact set of contributed values must all still be there.
        tensors = store.read_all_as_tensors(VALID_UUID)
        assert len(tensors) == thread_count
        decoded_values = sorted(float(t.mean()) for t in tensors)
        for decoded, expected in zip(decoded_values, sorted(expected_values), strict=True):
            assert abs(decoded - expected) < 1.0 / 255.0 + 1e-6

    def test_mixed_append_batch_and_append_uploaded_image_concurrently_all_survive(
        self, fake_folder_paths: Path, fake_input_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The same race, but half the threads go through `append_batch`
        (the Collect-mode node execution path) and half through
        `append_uploaded_image` (the paste-to-add route path) -- exactly
        the cross-path collision the review called out (a workflow Run
        collecting while the user pastes into the same grid)."""
        _widen_manifest_read_race(monkeypatch)
        batch_values = [0.15, 0.35, 0.55, 0.75]
        upload_grays = [40, 90, 140, 190]
        for i, gray in enumerate(upload_grays):
            _write_colored_png(fake_input_dir / f"paste_{i}.png", gray)

        errors: list[BaseException] = []

        def append_batch_worker(value: float) -> None:
            try:
                store.append_batch(VALID_UUID, _make_flat_batch(value))
            except BaseException as exc:  # pragma: no cover - surfaced via `errors`
                errors.append(exc)

        def append_upload_worker(index: int) -> None:
            try:
                store.append_uploaded_image(VALID_UUID, f"paste_{index}.png")
            except BaseException as exc:  # pragma: no cover - surfaced via `errors`
                errors.append(exc)

        threads = [threading.Thread(target=append_batch_worker, args=(v,)) for v in batch_values]
        threads += [
            threading.Thread(target=append_upload_worker, args=(i,))
            for i in range(len(upload_grays))
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"a worker thread raised: {errors}"

        total = len(batch_values) + len(upload_grays)
        refs = store.list_refs(VALID_UUID)
        names = [r["filename"] for r in refs]
        assert len(names) == total, f"expected {total} surviving frames, got {len(names)}: {names}"
        assert len(set(names)) == total

        directory = store.buffer_dir(VALID_UUID)
        for name in names:
            assert (directory / name).is_file(), f"manifest lists {name!r} but its file is missing"
            assert store.read_frame_as_tensor(VALID_UUID, name) is not None


class TestGridMutatorsRaceSafely:
    """`append_batch`/`append_uploaded_image` racing `remove_frame`/`clear`/
    `clone_buffer` on the SAME grid is exactly as unsafe, unlocked, as two
    concurrent appends -- all five mutators share the identical unlocked
    read-manifest -> write -> commit-manifest shape. This races all four
    together on one destination grid and checks the settled result is
    internally consistent."""

    def test_append_races_remove_clear_and_clone_into_destination(
        self, fake_folder_paths: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Seed the destination with two frames -- one will be targeted by
        # the concurrent `remove_frame`.
        store.append_batch(VALID_UUID, _make_flat_batch(0.11))
        store.append_batch(VALID_UUID, _make_flat_batch(0.22))
        remove_target = store.list_refs(VALID_UUID)[0]["filename"]

        # A separate SOURCE grid to clone FROM, never mutated during the
        # race -- its content is fixed and known, so a destination snapshot
        # that exactly matches it unambiguously means "the clone landed".
        store.append_batch(OTHER_VALID_UUID, _make_flat_batch(0.77))
        source_names = [r["filename"] for r in store.list_refs(OTHER_VALID_UUID)]

        _widen_manifest_read_race(monkeypatch)

        # ---- commit-order instrumentation ----------------------------
        # `_mtime_cache_invalidate` is called exactly once per successful
        # write, from INSIDE `_locked`, immediately after that write's
        # manifest commit, in every mutator (see image_grid_store.py) --
        # wrapping it gives the TRUE serialization order the fix's lock
        # enforces, each entry tagged with which logical operation just
        # committed via a thread-local the worker sets right before calling
        # the real store function.
        commit_log: list[dict] = []
        log_lock = threading.Lock()
        counter = itertools.count(1)
        op_label = threading.local()
        original_invalidate = store._mtime_cache_invalidate

        def logging_invalidate(grid_uuid: str) -> None:
            directory = store.buffer_dir(grid_uuid)
            frames = list(store._load_manifest(directory)["frames"]) if directory else []
            entry = {
                "seq": next(counter),
                "uuid": grid_uuid,
                "op": getattr(op_label, "value", "?"),
                "frames": frames,
            }
            with log_lock:
                commit_log.append(entry)
            original_invalidate(grid_uuid)

        monkeypatch.setattr(store, "_mtime_cache_invalidate", logging_invalidate)

        errors: list[BaseException] = []

        def run(label, fn, *args) -> None:
            op_label.value = label
            try:
                fn(*args)
            except BaseException as exc:  # pragma: no cover - surfaced via `errors`
                errors.append(exc)

        append_value = 0.55
        threads = [
            threading.Thread(
                target=run,
                args=("append", store.append_batch, VALID_UUID, _make_flat_batch(append_value)),
            ),
            threading.Thread(
                target=run, args=("remove", store.remove_frame, VALID_UUID, remove_target)
            ),
            threading.Thread(target=run, args=("clear", store.clear, VALID_UUID)),
            threading.Thread(
                target=run, args=("clone", store.clone_buffer, OTHER_VALID_UUID, VALID_UUID)
            ),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"a worker thread raised: {errors}"

        # ------------------------------------------------- self-consistency
        # THE primary invariant: whatever the settled manifest says, every
        # entry must point at a file that actually exists and decodes.
        # `os.replace` makes each individual manifest swap atomic on its
        # own, so this can only fail if two mutators interleaved their
        # reads/writes against each other (the bug this fix closes).
        final_refs = store.list_refs(VALID_UUID)
        final_names = [r["filename"] for r in final_refs]
        assert len(final_names) == len(set(final_names)), "duplicate filename in final manifest"
        directory = store.buffer_dir(VALID_UUID)
        for name in final_names:
            assert (directory / name).is_file(), f"manifest lists {name!r} but its file is missing"
            assert store.read_frame_as_tensor(VALID_UUID, name) is not None, (
                f"{name!r} is listed in the manifest but does not decode"
            )

        # --------------------------------------------- reconstruct true order
        dest_log = sorted(
            (e for e in commit_log if e["uuid"] == VALID_UUID), key=lambda e: e["seq"]
        )
        assert dest_log, "no mutator of the destination grid ever committed"
        assert dest_log[-1]["frames"] == final_names, (
            "the last logged commit doesn't match the settled manifest -- something wrote "
            "after the last commit this log saw, or the log missed a write entirely"
        )

        # clear and a clone landing on the destination are BOTH a full
        # manifest replace -- structurally identical erasure risk -- so
        # either can legitimately explain an earlier append's disappearance.
        # remove_frame can only legitimately erase the ONE frame it targeted.
        erasing_ops = {"clear", "clone"}
        for commit in (e for e in dest_log if e["op"] == "append"):
            appended_name = commit["frames"][-1]
            survives = appended_name in final_names
            later_erase = any(
                e["seq"] > commit["seq"] and e["op"] in erasing_ops for e in dest_log
            )
            assert survives or later_erase, (
                f"append at seq={commit['seq']} (frame {appended_name!r}) vanished with no "
                "later clear/clone to explain it -- the exact data-loss bug this fix closes"
            )

        # remove_frame (if it actually removed something -- a soft no-op
        # never calls `_mtime_cache_invalidate`, so it simply never appears
        # in the log) must not have silently dropped anything beyond its
        # own target.
        for commit in (e for e in dest_log if e["op"] == "remove"):
            assert remove_target not in commit["frames"]

        # A clone landing on the destination must be an EXACT copy of the
        # source's fixed content -- never a partial/mismatched copy (which
        # would mean the clone read a torn state of source or destination).
        for commit in (e for e in dest_log if e["op"] == "clone"):
            assert commit["frames"] == source_names


class TestIndependentGridsAreNotSerialized:
    def test_two_different_grids_append_concurrently_without_serializing(
        self, fake_folder_paths: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Proves the fix's lock is PER-GRID, not one global lock -- holding
        one grid's lock while its PNGs are written (`_locked`'s own
        docstring) must never block a completely different grid's mutator.
        Widens each call's critical section via an injected delay in
        `_save_manifest` (not `_load_manifest`'s read-race helper above --
        that widening is pointless here since these two calls are never
        contending for the same lock in the first place) so the two calls'
        windows are long enough to reliably overlap-detect even on a loaded
        CI box, then checks their wall-clock intervals actually overlap and
        that running both took roughly one window, not two."""
        delay = 0.2
        original_save = store._save_manifest

        def slow_save_manifest(directory, manifest):
            time.sleep(delay)
            original_save(directory, manifest)

        monkeypatch.setattr(store, "_save_manifest", slow_save_manifest)

        intervals: dict[str, tuple[float, float]] = {}
        interval_lock = threading.Lock()

        def worker(grid_uuid: str) -> None:
            start = time.monotonic()
            store.append_batch(grid_uuid, _make_flat_batch(0.5))
            end = time.monotonic()
            with interval_lock:
                intervals[grid_uuid] = (start, end)

        threads = [
            threading.Thread(target=worker, args=(VALID_UUID,)),
            threading.Thread(target=worker, args=(OTHER_VALID_UUID,)),
        ]
        start_all = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        total_wall = time.monotonic() - start_all

        assert set(intervals) == {VALID_UUID, OTHER_VALID_UUID}
        start_a, end_a = intervals[VALID_UUID]
        start_b, end_b = intervals[OTHER_VALID_UUID]
        overlap = min(end_a, end_b) - max(start_a, start_b)
        assert overlap > 0, (
            f"the two grids' append windows never overlapped ({intervals}) -- looks like "
            "they were serialized against each other"
        )
        # Generous slack: back-to-back (serialized) would take ~2*delay;
        # genuinely parallel takes ~delay. 1.5*delay is a clear, non-flaky
        # line between the two.
        assert total_wall < delay * 1.5, (
            f"took {total_wall:.3f}s for two {delay:.3f}s per-grid critical sections -- "
            "looks serialized, not parallel"
        )

        assert len(store.list_refs(VALID_UUID)) == 1
        assert len(store.list_refs(OTHER_VALID_UUID)) == 1
