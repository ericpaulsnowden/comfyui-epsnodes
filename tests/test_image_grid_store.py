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

from eps_image import image_grid_store as store


@pytest.fixture
def fake_folder_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Installs a fake ``folder_paths`` module whose ``get_output_directory``
    resolves to a fresh ``tmp_path`` subdirectory. Returns that directory.
    """
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    fake_module = types.ModuleType("folder_paths")
    fake_module.get_output_directory = lambda: str(output_dir)
    monkeypatch.setitem(sys.modules, "folder_paths", fake_module)
    return output_dir


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
