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

import json
import sys
import types
from pathlib import Path

import pytest
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


VALID_UUID = "a1b2c3d4-e5f6-47a8-9b0c-d1e2f3a4b5c6"
OTHER_VALID_UUID = "11111111-2222-3333-4444-555555555555"


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
