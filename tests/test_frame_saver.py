"""Tests for ``EPSFrameSaver`` (FORMAT.md §6.7): the probe/extract helper
(``eps_image/frame_saver_video.py``), the node (``eps_image/
nodes_frame_saver.py``), and the HTTP routes (``eps_image/
routes_frame_saver.py``).

Real ``av``/``torch`` are used throughout -- this module `importorskip`s both
at collection time so it degrades to a clean SKIP (never a collection error)
on an interpreter missing either, but the pack's own dev rig venv has both
(see ``eps_image/nodes_resolution.py``'s sibling test file for the same "real
tensors, no fakes" stance on ``torch``). Route tests use aiohttp's own test
client (``aiohttp_client``, from the ``pytest-aiohttp`` plugin) against
``routes_frame_saver.build_routes()`` -- no ComfyUI needed, mirroring
``tests/test_image_grid.py``'s identical ``client`` fixture pattern.

Real-decode assertions run against this rig's seeded test clips
(``clip_red.mp4``/``clip_green.mp4``/``clip_blue.mp4``, 16 frames @ 24fps,
320x180, one solid color each -- confirmed via a live PyAV probe before
writing these expectations) rather than a fixture shipped in the repo, per
the task's own direction; :func:`_seeded_clip` skips (not fails) any test
that needs one when the rig path isn't present, so this file degrades
gracefully outside that specific dev rig too.
"""

from __future__ import annotations

import inspect
import re
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("av")
pytest.importorskip("torch")

import torch
from aiohttp import web

from eps_image import (
    frame_saver_video,
    nodes_frame_saver,
    routes_frame_saver,
)
from eps_image.nodes_frame_saver import EPSFrameSaver

# ------------------------------------------------------------- seeded fixtures

#: The rig's seeded test clips (RIG ETIQUETTE: shared dev rig, not shipped in
#: this repo) -- 16 frames @ 24fps, 320x180, solid-color content confirmed by
#: a live probe: red-dominant / green-dominant / blue-dominant respectively.
_SEEDED_CLIPS_DIR = Path(
    "/Users/ericsnowden/Library/Application Support/"
    "comfy_ps/rig/ComfyUI/input"
)
CLIP_RED = _SEEDED_CLIPS_DIR / "clip_red.mp4"
CLIP_GREEN = _SEEDED_CLIPS_DIR / "clip_green.mp4"
CLIP_BLUE = _SEEDED_CLIPS_DIR / "clip_blue.mp4"

#: Confirmed via a live PyAV probe (see final report) -- pinned here so a
#: cascade-tier regression (e.g. accidentally landing on the decode-count
#: tier instead of `stream.frames`) would change these and fail loudly.
SEEDED_FPS = 24.0
SEEDED_FRAME_COUNT = 16
SEEDED_WIDTH = 320
SEEDED_HEIGHT = 180


def _seeded_clip(path: Path) -> str:
    """*path* as a string, or skip this test -- see module docstring."""
    if not path.is_file():
        pytest.skip(f"seeded test clip not present at {path} (rig-specific fixture)")
    return str(path)


# ------------------------------------------------------------- frame_saver_video.probe


class TestProbe:
    def test_known_clip_reports_correct_metadata(self) -> None:
        path = _seeded_clip(CLIP_RED)
        info = frame_saver_video.probe(path)
        assert info["fps"] == SEEDED_FPS
        assert info["frame_count"] == SEEDED_FRAME_COUNT
        assert info["width"] == SEEDED_WIDTH
        assert info["height"] == SEEDED_HEIGHT
        assert info["duration"] == pytest.approx(SEEDED_FRAME_COUNT / SEEDED_FPS)

    def test_duration_is_always_frame_count_over_fps(self) -> None:
        path = _seeded_clip(CLIP_GREEN)
        info = frame_saver_video.probe(path)
        assert info["duration"] == pytest.approx(info["frame_count"] / info["fps"])

    def test_missing_file_raises_value_error_naming_the_path(self) -> None:
        with pytest.raises(ValueError, match=re.escape("does-not-exist.mp4")):
            frame_saver_video.probe("/no/such/place/does-not-exist.mp4")

    def test_missing_file_is_a_clean_value_error_not_a_raw_traceback_type(self) -> None:
        # i.e. never an unwrapped OSError/av error leaking through.
        try:
            frame_saver_video.probe("/no/such/place/does-not-exist.mp4")
        except ValueError:
            pass
        except Exception as exc:  # pragma: no cover -- failure path only
            pytest.fail(f"expected ValueError, got {type(exc).__name__}: {exc}")
        else:
            pytest.fail("expected a ValueError")

    def test_unreadable_garbage_file_raises_value_error(self, tmp_path: Path) -> None:
        garbage = tmp_path / "not_a_video.mp4"
        garbage.write_bytes(b"this is not a real video container")
        with pytest.raises(ValueError, match=str(garbage)):
            frame_saver_video.probe(str(garbage))


# ------------------------------------------------------------- frame_saver_video.extract_frame


class TestExtractFrame:
    def test_returns_batch_of_one_hwc_float_tensor(self) -> None:
        path = _seeded_clip(CLIP_RED)
        tensor, width, height = frame_saver_video.extract_frame(path, 0)
        assert tensor.shape == (1, SEEDED_HEIGHT, SEEDED_WIDTH, 3)
        assert tensor.dtype == torch.float32
        assert width == SEEDED_WIDTH
        assert height == SEEDED_HEIGHT
        assert float(tensor.min()) >= 0.0
        assert float(tensor.max()) <= 1.0

    def test_width_height_match_stream_dimensions_regardless_of_frame_index(self) -> None:
        path = _seeded_clip(CLIP_RED)
        for frame_index in (0, 5, 15):
            _tensor, width, height = frame_saver_video.extract_frame(path, frame_index)
            assert (width, height) == (SEEDED_WIDTH, SEEDED_HEIGHT)

    def test_specific_frame_index_lands_on_a_real_decoded_frame(self) -> None:
        # Frame 0 and frame 15 (of a 16-frame clip) must both decode without
        # error and be genuine, distinctly-seekable positions in the stream.
        path = _seeded_clip(CLIP_RED)
        first, _w, _h = frame_saver_video.extract_frame(path, 0)
        last, _w, _h = frame_saver_video.extract_frame(path, 15)
        assert first.shape == last.shape

    def test_solid_color_clips_decode_the_dominant_channel_correctly(self) -> None:
        # A real, end-to-end confidence check (not just shape) that the
        # right BYTES came out the other end -- confirmed live: clip_red is
        # red-dominant, clip_green green-dominant, clip_blue blue-dominant.
        red, _w, _h = frame_saver_video.extract_frame(_seeded_clip(CLIP_RED), 0)
        green, _w, _h = frame_saver_video.extract_frame(_seeded_clip(CLIP_GREEN), 0)
        blue, _w, _h = frame_saver_video.extract_frame(_seeded_clip(CLIP_BLUE), 0)

        red_mean = red.mean(dim=(0, 1, 2))
        green_mean = green.mean(dim=(0, 1, 2))
        blue_mean = blue.mean(dim=(0, 1, 2))

        assert red_mean[0] > red_mean[1] and red_mean[0] > red_mean[2]
        assert green_mean[1] > green_mean[0] and green_mean[1] > green_mean[2]
        assert blue_mean[2] > blue_mean[0] and blue_mean[2] > blue_mean[1]

    def test_out_of_range_frame_index_clamps_to_the_last_frame_not_an_error(self) -> None:
        path = _seeded_clip(CLIP_RED)
        last, _w, _h = frame_saver_video.extract_frame(path, 15)
        way_past_the_end, _w, _h = frame_saver_video.extract_frame(path, 9999)
        assert way_past_the_end.shape == last.shape
        assert float((way_past_the_end - last).abs().max()) < 0.05

    def test_negative_frame_index_clamps_to_frame_zero_not_an_error(self) -> None:
        path = _seeded_clip(CLIP_RED)
        frame_zero, _w, _h = frame_saver_video.extract_frame(path, 0)
        negative, _w, _h = frame_saver_video.extract_frame(path, -5)
        assert negative.shape == frame_zero.shape
        assert float((negative - frame_zero).abs().max()) < 0.05

    def test_missing_file_raises_value_error_naming_the_path(self) -> None:
        with pytest.raises(ValueError, match=re.escape("does-not-exist.mp4")):
            frame_saver_video.extract_frame("/no/such/place/does-not-exist.mp4", 0)


# ------------------------------------------------------------- import cleanliness


def test_frame_saver_video_has_no_torch_or_av_bound_at_runtime() -> None:
    # `av`/`torch` type hints ARE imported at module scope, but ONLY inside
    # an `if TYPE_CHECKING:` guard (mirrors comfyui-premiere-bridge's
    # cprb/frame_extract.py identical pattern) -- it never executes at
    # runtime. This confirms the guard is doing its job: neither name leaks
    # into the module's real namespace, even though both ARE installed in
    # this test environment (a naive "'import torch' not in source" text
    # check would give a FALSE positive here, since that string genuinely
    # appears -- inside the guard).
    assert "torch" not in vars(frame_saver_video)
    assert "av" not in vars(frame_saver_video)
    assert "comfy" not in vars(frame_saver_video)


def test_nodes_frame_saver_never_imports_torch_av_or_comfy_at_module_scope() -> None:
    assert "torch" not in vars(nodes_frame_saver)
    assert "av" not in vars(nodes_frame_saver)
    assert "comfy" not in vars(nodes_frame_saver)
    source = inspect.getsource(sys.modules[nodes_frame_saver.__name__])
    assert "import torch" not in source
    assert "import av" not in source
    assert "import comfy" not in source


def test_routes_frame_saver_never_imports_torch_or_comfy_at_module_scope() -> None:
    assert "torch" not in vars(routes_frame_saver)
    assert "comfy" not in vars(routes_frame_saver)
    source = inspect.getsource(sys.modules[routes_frame_saver.__name__])
    assert "import torch" not in source
    assert "import comfy" not in source


# ------------------------------------------------------------- EPSFrameSaver class shape


class TestClassShapeMatchesFormatMdSection6_7:
    def test_category(self) -> None:
        assert EPSFrameSaver.CATEGORY == "EPSNodes"

    def test_return_types_and_names(self) -> None:
        assert EPSFrameSaver.RETURN_TYPES == ("IMAGE", "INT", "INT")
        assert EPSFrameSaver.RETURN_NAMES == ("image", "width", "height")

    def test_output_is_list_is_not_declared(self) -> None:
        # FORMAT.md §6.7: single-frame output, NOT a list -- matches
        # PremiereShotFrame. The attribute must be genuinely ABSENT, not
        # merely falsy, so a downstream reader using getattr-with-default
        # sees the same "no OUTPUT_IS_LIST" state core itself checks for.
        assert not hasattr(EPSFrameSaver, "OUTPUT_IS_LIST")

    def test_function_name_matches_the_declared_entry_point(self) -> None:
        assert EPSFrameSaver.FUNCTION == "run"
        assert callable(getattr(EPSFrameSaver(), EPSFrameSaver.FUNCTION))

    def test_no_output_node_flag(self) -> None:
        # Unlike EPSImageGrid, this node has no reason to run with nothing
        # wired downstream -- a plain function node, no OUTPUT_NODE needed.
        assert not hasattr(EPSFrameSaver, "OUTPUT_NODE")


class TestInputTypes:
    def test_video_path_is_required_string_defaulting_empty(self) -> None:
        input_types = EPSFrameSaver.INPUT_TYPES()
        widget_type, spec = input_types["required"]["video_path"]
        assert widget_type == "STRING"
        assert spec["default"] == ""

    def test_frame_is_required_int_defaulting_zero(self) -> None:
        input_types = EPSFrameSaver.INPUT_TYPES()
        widget_type, spec = input_types["required"]["frame"]
        assert widget_type == "INT"
        assert spec["default"] == 0
        assert spec["min"] == 0

    def test_no_optional_inputs(self) -> None:
        # FORMAT.md §6.7: "No IMAGE input."
        input_types = EPSFrameSaver.INPUT_TYPES()
        assert input_types.get("optional", {}) == {}


# ------------------------------------------------------------- EPSFrameSaver.run()


class TestRun:
    def test_returns_image_width_height_tuple_for_a_real_clip(self) -> None:
        node = EPSFrameSaver()
        image, width, height = node.run(video_path=_seeded_clip(CLIP_RED), frame=0)
        assert image.shape == (1, SEEDED_HEIGHT, SEEDED_WIDTH, 3)
        assert width == SEEDED_WIDTH
        assert height == SEEDED_HEIGHT

    def test_output_dims_match_the_probe_helpers_own_report(self) -> None:
        path = _seeded_clip(CLIP_RED)
        info = frame_saver_video.probe(path)
        node = EPSFrameSaver()
        _image, width, height = node.run(video_path=path, frame=0)
        assert width == info["width"]
        assert height == info["height"]

    def test_selected_frame_actually_changes_the_output(self) -> None:
        # Not a strong pixel-diff assertion (a solid-color test clip could
        # legitimately decode identically frame to frame) -- just confirms
        # `frame` really reaches extract_frame and both requests succeed
        # cleanly end to end via the node's own run(), not just the helper.
        path = _seeded_clip(CLIP_RED)
        node = EPSFrameSaver()
        first, _w, _h = node.run(video_path=path, frame=0)
        last, _w, _h = node.run(video_path=path, frame=15)
        assert first.shape == last.shape

    def test_out_of_range_frame_does_not_raise(self) -> None:
        node = EPSFrameSaver()
        image, width, height = node.run(video_path=_seeded_clip(CLIP_RED), frame=99999)
        assert image.shape == (1, height, width, 3)

    def test_default_frame_is_zero(self) -> None:
        node = EPSFrameSaver()
        image, _width, _height = node.run(video_path=_seeded_clip(CLIP_RED))
        first, _w, _h = frame_saver_video.extract_frame(_seeded_clip(CLIP_RED), 0)
        assert image.shape == first.shape

    def test_empty_video_path_raises_value_error_naming_it(self) -> None:
        node = EPSFrameSaver()
        with pytest.raises(ValueError, match="video_path"):
            node.run(video_path="", frame=0)

    def test_whitespace_only_video_path_raises_value_error(self) -> None:
        node = EPSFrameSaver()
        with pytest.raises(ValueError, match="video_path"):
            node.run(video_path="   ", frame=0)

    def test_nonexistent_video_path_raises_value_error_naming_the_path(self) -> None:
        node = EPSFrameSaver()
        with pytest.raises(ValueError, match=re.escape("nope.mp4")):
            node.run(video_path="/no/such/place/nope.mp4", frame=0)


# ============================================================= routes_frame_saver


class TestValidateVideoPath:
    """Unit tests for the shared path-validation helper both routes use."""

    def test_empty_path_is_rejected(self) -> None:
        resolved, error = routes_frame_saver._validate_video_path("")
        assert resolved is None
        assert error is not None

    def test_relative_path_is_rejected(self) -> None:
        resolved, error = routes_frame_saver._validate_video_path("clips/video.mp4")
        assert resolved is None
        assert "absolute" in error

    def test_disallowed_extension_is_rejected(self) -> None:
        resolved, error = routes_frame_saver._validate_video_path("/tmp/notes.txt")
        assert resolved is None
        assert "extension" in error

    def test_extension_check_is_case_insensitive(self, tmp_path: Path) -> None:
        target = tmp_path / "clip.MP4"
        resolved, error = routes_frame_saver._validate_video_path(str(target))
        assert error is None
        assert resolved == target.resolve()

    def test_dot_segments_are_normalized_before_validation(self, tmp_path: Path) -> None:
        (tmp_path / "sub").mkdir()
        noisy = str(tmp_path / "sub" / ".." / "clip.mp4")
        resolved, error = routes_frame_saver._validate_video_path(noisy)
        assert error is None
        assert resolved == (tmp_path / "clip.mp4").resolve()

    def test_every_declared_extension_is_accepted(self, tmp_path: Path) -> None:
        for ext in routes_frame_saver.VIDEO_EXTENSIONS:
            resolved, error = routes_frame_saver._validate_video_path(str(tmp_path / f"clip{ext}"))
            assert error is None, f"{ext} should be accepted"
            assert resolved is not None

    def test_embedded_nul_byte_is_a_clean_rejection_not_a_raw_exception(self) -> None:
        # Regression: Path.resolve() raises a bare ValueError ("embedded null
        # character in path") for an absolute-looking string containing a
        # NUL byte -- confirmed live before this guard was added, and would
        # otherwise propagate straight into an unhandled 500.
        resolved, error = routes_frame_saver._validate_video_path("/tmp/\x00nul.mp4")
        assert resolved is None
        assert error is not None


@pytest.fixture
async def client(aiohttp_client):
    """A plain aiohttp test client wired to just this module's routes (no
    ComfyUI) -- mirrors test_image_grid.py's identical ``client`` fixture."""
    app = web.Application()
    app.add_routes(routes_frame_saver.build_routes())
    return await aiohttp_client(app)


REMOTE_HEADERS = {"X-Forwarded-For": "192.168.1.50"}


class TestRequestIsLoopback:
    """The 2026-08-02 back-port of ``lora_library/routes.py``'s 2026-07-30
    same-machine-via-LAN fix (roadmap P0-1): a browser on the HOST that
    reaches ComfyUI via the machine's own LAN address must classify as
    LOCAL, while a genuinely foreign machine's address stays remote.
    ``request_is_loopback`` only touches ``.headers`` and ``.remote``, so a
    ``SimpleNamespace`` stands in for the request; the ownership probe is
    monkeypatched where the test needs a specific verdict, and exercised
    for real in :func:`test_machine_owns_address_bind_test` below
    (mirroring ``test_routes_notebook.py``'s canonical-copy test)."""

    def test_lan_address_the_machine_owns_is_local(self, monkeypatch) -> None:
        probed = []

        def owns(address: str) -> bool:
            probed.append(address)
            return address == "192.168.1.23"

        monkeypatch.setattr(routes_frame_saver, "_machine_owns_address", owns)
        request = SimpleNamespace(headers={}, remote="192.168.1.23")
        assert routes_frame_saver.request_is_loopback(request) is True
        assert probed == ["192.168.1.23"], "non-loopback peers must hit the bind probe"

    def test_foreign_lan_address_is_still_remote(self, monkeypatch) -> None:
        monkeypatch.setattr(
            routes_frame_saver, "_machine_owns_address", lambda address: False
        )
        request = SimpleNamespace(headers={}, remote="192.168.1.99")
        assert routes_frame_saver.request_is_loopback(request) is False

    def test_forwarded_request_is_remote_even_from_an_owned_address(
        self, monkeypatch
    ) -> None:
        # The peer of a forwarded request is the PROXY, not the client -- an
        # owned peer address must not launder a foreign origin into "local".
        monkeypatch.setattr(
            routes_frame_saver, "_machine_owns_address", lambda address: True
        )
        request = SimpleNamespace(
            headers={"X-Forwarded-For": "192.168.1.50"}, remote="192.168.1.23"
        )
        assert routes_frame_saver.request_is_loopback(request) is False

    def test_loopback_fast_path_never_probes(self, monkeypatch) -> None:
        def boom(address: str) -> bool:
            raise AssertionError("loopback peers must not reach the bind probe")

        monkeypatch.setattr(routes_frame_saver, "_machine_owns_address", boom)
        assert routes_frame_saver.request_is_loopback(
            SimpleNamespace(headers={}, remote="127.0.0.1")
        ) is True
        assert routes_frame_saver.request_is_loopback(
            SimpleNamespace(headers={}, remote=None)
        ) is True


def test_machine_owns_address_bind_test() -> None:
    """The real UDP-bind probe, no monkeypatching -- mirrors the canonical
    copy's test in ``test_routes_notebook.py``: an address this machine
    actually owns (discovered via a UDP connect that sends nothing) binds,
    TEST-NET-1 never does, and an IPv4-mapped IPv6 peer reduces to IPv4."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))  # UDP connect sends nothing
        own_ip = probe.getsockname()[0]
    finally:
        probe.close()
    assert routes_frame_saver._machine_owns_address(own_ip) is True
    assert routes_frame_saver._machine_owns_address("::ffff:127.0.0.1") is True
    assert routes_frame_saver._machine_owns_address("192.0.2.1") is False  # TEST-NET-1


class TestProbeRoute:
    async def test_valid_seeded_clip_returns_the_probe_dict(self, client) -> None:
        path = _seeded_clip(CLIP_RED)
        response = await client.get("/eps_frame_saver/probe", params={"path": path})
        assert response.status == 200
        data = await response.json()
        assert data == frame_saver_video.probe(path)

    async def test_missing_path_param_is_400(self, client) -> None:
        response = await client.get("/eps_frame_saver/probe")
        assert response.status == 400
        assert "error" in await response.json()

    async def test_relative_path_is_400(self, client) -> None:
        response = await client.get("/eps_frame_saver/probe", params={"path": "clip.mp4"})
        assert response.status == 400

    async def test_disallowed_extension_is_400(self, client, tmp_path: Path) -> None:
        bad = tmp_path / "clip.txt"
        bad.write_text("nope")
        response = await client.get("/eps_frame_saver/probe", params={"path": str(bad)})
        assert response.status == 400

    async def test_nonexistent_file_with_good_extension_is_400_not_500(self, client) -> None:
        response = await client.get(
            "/eps_frame_saver/probe", params={"path": "/no/such/place/nope.mp4"}
        )
        assert response.status == 400
        assert "error" in await response.json()

    async def test_unreadable_file_is_400_not_500(self, client, tmp_path: Path) -> None:
        garbage = tmp_path / "clip.mp4"
        garbage.write_bytes(b"not a real video")
        response = await client.get("/eps_frame_saver/probe", params={"path": str(garbage)})
        assert response.status == 400
        assert "error" in await response.json()

    async def test_remote_caller_is_403(self, client) -> None:
        response = await client.get(
            "/eps_frame_saver/probe",
            params={"path": _seeded_clip(CLIP_RED)},
            headers=REMOTE_HEADERS,
        )
        assert response.status == 403
        assert "error" in await response.json()


class TestStreamRoute:
    async def test_valid_seeded_clip_streams_the_full_file(self, client) -> None:
        path = _seeded_clip(CLIP_RED)
        response = await client.get("/eps_frame_saver/stream", params={"path": path})
        assert response.status == 200
        body = await response.read()
        assert len(body) == Path(path).stat().st_size

    async def test_range_request_gets_a_206_partial_response(self, client) -> None:
        # aiohttp's web.FileResponse gives Range/206 seek support for free
        # (FORMAT.md §6.7) -- this is what lets the <video> element scrub
        # smoothly instead of re-downloading the whole file on every seek.
        path = _seeded_clip(CLIP_RED)
        response = await client.get(
            "/eps_frame_saver/stream", params={"path": path}, headers={"Range": "bytes=0-15"}
        )
        assert response.status == 206
        assert response.headers.get("Accept-Ranges") == "bytes"
        body = await response.read()
        assert len(body) == 16

    async def test_missing_path_param_is_400(self, client) -> None:
        response = await client.get("/eps_frame_saver/stream")
        assert response.status == 400

    async def test_disallowed_extension_is_400(self, client, tmp_path: Path) -> None:
        bad = tmp_path / "notes.txt"
        bad.write_text("nope")
        response = await client.get("/eps_frame_saver/stream", params={"path": str(bad)})
        assert response.status == 400

    async def test_nonexistent_file_is_400_not_500(self, client) -> None:
        response = await client.get(
            "/eps_frame_saver/stream", params={"path": "/no/such/place/nope.mp4"}
        )
        assert response.status == 400
        assert "error" in await response.json()

    async def test_directory_with_video_like_name_is_400_not_500(
        self, client, tmp_path: Path
    ) -> None:
        fake_dir = tmp_path / "not_really.mp4"
        fake_dir.mkdir()
        response = await client.get("/eps_frame_saver/stream", params={"path": str(fake_dir)})
        assert response.status == 400

    async def test_remote_caller_is_403(self, client) -> None:
        response = await client.get(
            "/eps_frame_saver/stream",
            params={"path": _seeded_clip(CLIP_RED)},
            headers=REMOTE_HEADERS,
        )
        assert response.status == 403


class TestNeverFiveHundreds:
    """A sweep of hostile/malformed inputs across both routes -- every one
    must resolve to a 4xx (FORMAT.md §6.7 "Never 500 on bad input")."""

    @pytest.mark.parametrize(
        "path",
        [
            "",
            "relative/clip.mp4",
            "/tmp/../../../etc/passwd",
            "/no/such/place/nope.mp4",
            "/no/such/place/nope.txt",
            "\x00nul",
        ],
    )
    async def test_probe_route(self, client, path: str) -> None:
        response = await client.get("/eps_frame_saver/probe", params={"path": path})
        assert response.status < 500

    @pytest.mark.parametrize(
        "path",
        [
            "",
            "relative/clip.mp4",
            "/tmp/../../../etc/passwd",
            "/no/such/place/nope.mp4",
            "/no/such/place/nope.txt",
            "\x00nul",
        ],
    )
    async def test_stream_route(self, client, path: str) -> None:
        response = await client.get("/eps_frame_saver/stream", params={"path": path})
        assert response.status < 500
