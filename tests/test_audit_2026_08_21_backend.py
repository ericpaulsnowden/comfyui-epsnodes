"""Pins for the 2026-08-21 backend audit round (v0.68.1): degrade-don't-crash
guards that missed ValueError siblings, a presets-file data-loss path, a
blocking video probe + gate ordering, Frame Saver's missing IS_CHANGED, a
config re-parse per call, an unbounded folder listing, a remote-dir
unshare mismatch, and the picker feed's preview sweep."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
from aiohttp import web

from eps_image import nodes_frame_saver, routes_frame_saver, routes_resolution_presets
from eps_image import resolution_presets_store as presets_store
from lora_library import markdown_store, routes, routes_lora_picker, sets_store
from lora_library.context import LibraryContext
from lora_library.routes import _is_inside

REMOTE = {"X-Forwarded-For": "192.168.1.50"}
_ROUTES_SRC = Path(routes.__file__).read_text(encoding="utf-8")
FS_LIST_ROUTE = re.search(r'routes\.get\("(/lora_library/fs[^"]*)"', _ROUTES_SRC).group(1)
REMOTE_DIRS_ROUTE = re.search(r'routes\.post\("(/lora_library/remote_dirs?)"', _ROUTES_SRC).group(1)


@pytest.fixture
async def core_client(context: LibraryContext, aiohttp_client):
    app = web.Application()
    app.add_routes(routes.build_routes(context))
    return await aiohttp_client(app)


# ------------------------------------------------------------- sets (A1)


class TestSetStoreUnreadableFile:
    def test_utf16_set_file_is_a_validation_error_not_a_crash(
        self, context: LibraryContext
    ) -> None:
        sets_dir = context.sets_dir()
        payload = json.dumps(
            {"format": 1, "name": "Bad", "loras": [], "trigger_words": "", "notes": ""}
        )
        (sets_dir / "bad.json").write_bytes(payload.encode("utf-16"))
        with pytest.raises(sets_store.SetValidationError):
            sets_store.load_set(context, "bad")

    def test_list_sets_survives_a_sibling_that_cannot_be_decoded(
        self, context: LibraryContext
    ) -> None:
        sets_store.save_set(
            context, {"format": 1, "name": "Good", "loras": [], "trigger_words": "", "notes": ""}
        )
        (context.sets_dir() / "bad.json").write_bytes(b"\xff\xfe{\x00}\x00")
        slugs = {entry["slug"] for entry in sets_store.list_sets(context)}
        assert "good" in slugs  # the dropdown no longer collapses to ["None"]


# --------------------------------------------------------- notebook (A2, A3)


class TestNotebookUnreadableFile:
    def test_directory_named_as_the_file_is_a_store_error(self, library_dir: Path) -> None:
        (library_dir / "adir").mkdir()
        with pytest.raises(markdown_store.MarkdownStoreError):
            markdown_store.load_notebook(library_dir / "adir")

    def test_utf16_notebook_is_a_store_error(self, library_dir: Path) -> None:
        path = library_dir / "bad.md"
        path.write_bytes("# A\n\nbody\n".encode("utf-16"))
        with pytest.raises(markdown_store.MarkdownStoreError):
            markdown_store.load_notebook(path)

    async def test_get_notebook_unreadable_file_is_400_not_500(
        self, core_client, library_dir: Path
    ) -> None:
        (library_dir / "adir").mkdir()
        response = await core_client.get("/lora_library/notebook", params={"file": "adir"})
        assert response.status == 400
        assert "could not read" in (await response.json())["error"]

    def test_is_inside_never_raises_on_an_embedded_nul(self, tmp_path: Path) -> None:
        assert _is_inside(Path("a\x00b"), tmp_path) is False

    async def test_get_notebook_nul_in_file_is_400(self, core_client) -> None:
        response = await core_client.get("/lora_library/notebook", params={"file": "a\x00b.md"})
        assert response.status == 400


# ------------------------------------------------------------ config (A7)


class TestConfigCache:
    def test_repeat_loads_are_equal_and_mutation_does_not_poison(
        self, context: LibraryContext
    ) -> None:
        context.save_config({"library_dir": str(context.default_library_dir)})
        first = context.load_config()
        first["poison"] = True
        second = context.load_config()
        assert "poison" not in second
        assert second["library_dir"] == str(context.default_library_dir)

    def test_a_save_invalidates_the_cache(self, context: LibraryContext) -> None:
        context.save_config({"a": 1})
        assert context.load_config() == {"a": 1}
        context.save_config({"a": 2})
        assert context.load_config() == {"a": 2}


# ----------------------------------------------------------- fs list (A5)


class TestFsListBudgets:
    async def test_subfolders_never_starve_the_file_list(self, core_client, tmp_path: Path) -> None:
        root = tmp_path / "many"
        root.mkdir()
        for index in range(routes._FS_LIST_MAX_ENTRIES + 5):
            (root / f"a{index:04d}").mkdir()  # all sort BEFORE the file
        (root / "zz.md").write_text("# z\n", encoding="utf-8")
        response = await core_client.get(FS_LIST_ROUTE, params={"dir": str(root)})
        assert response.status == 200
        body = await response.json()
        assert [f["name"] for f in body["files"]] == ["zz.md"]
        assert len(body["dirs"]) == routes._FS_LIST_MAX_ENTRIES
        assert body["truncated"] is True


# -------------------------------------------------------- remote dirs (A6)


class TestRemoteDirUnshare:
    async def test_unshare_matches_a_differently_spelled_path(
        self, core_client, tmp_path: Path
    ) -> None:
        nas = tmp_path / "nas" / "docs"
        nas.mkdir(parents=True)
        spelled = str(tmp_path / "nas" / ".." / "nas" / "docs")
        add = await core_client.post(REMOTE_DIRS_ROUTE, json={"dir": spelled, "allow": True})
        assert add.status == 200, await add.text()
        remove = await core_client.post(REMOTE_DIRS_ROUTE, json={"dir": str(nas), "allow": False})
        assert remove.status == 200, await remove.text()
        assert (await remove.json())["remote_dirs"] == []


# ------------------------------------------------------------ presets (B1, B8)


class TestPresetsUnreadableFileKeepsItsMtime:
    def test_stale_base_mtime_conflicts_instead_of_wiping(
        self, context: LibraryContext, library_dir: Path
    ) -> None:
        path = library_dir / presets_store.PRESETS_FILENAME
        path.write_bytes(json.dumps({"format": 1, "presets": {}}).encode("utf-16"))
        _presets, mtime = presets_store.load_presets(context)
        assert mtime == path.stat().st_mtime
        with pytest.raises(presets_store.ConflictError):
            presets_store.check_conflict(1.0, mtime)

    async def test_save_rejects_a_boolean_base_mtime(
        self, context: LibraryContext, aiohttp_client
    ) -> None:
        app = web.Application()
        app.add_routes(routes_resolution_presets.build_routes(context))
        client = await aiohttp_client(app)
        response = await client.post(
            "/eps_resolution/presets/save",
            json={
                "name": "X",
                "values": {"width": 64, "height": 64, "resize_method": "stretch",
                           "interpolation": "bilinear", "multiple_of": 0},
                "base_mtime": True,
            },
        )
        assert response.status == 400


# ------------------------------------------------------ frame saver (B2, B3, B4)


class TestFrameSaverRoutes:
    async def test_remote_path_mode_is_refused_before_any_resolve(
        self, aiohttp_client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []

        def spy(request):  # must never run for a remote path-mode request
            calls.append("resolved")
            return None, "boom", True

        monkeypatch.setattr(routes_frame_saver, "_resolve_request_source", spy)
        app = web.Application()
        app.add_routes(routes_frame_saver.build_routes())
        client = await aiohttp_client(app)
        for route in ("/eps_frame_saver/probe", "/eps_frame_saver/stream"):
            response = await client.get(
                route, params={"path": "/definitely/not/here.mp4"}, headers=REMOTE
            )
            assert response.status == 403
        assert calls == []

    async def test_probe_runs_off_the_event_loop(
        self, aiohttp_client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import threading

        seen: dict[str, object] = {}
        clip = tmp_path / "c.mp4"
        clip.write_bytes(b"0")
        monkeypatch.setattr(
            routes_frame_saver, "_resolve_request_source", lambda r: (clip, None, True)
        )

        def fake_probe(path: str) -> dict:
            seen["thread"] = threading.current_thread().name
            return {"ok": True}

        monkeypatch.setattr(routes_frame_saver.video, "probe", fake_probe)
        app = web.Application()
        app.add_routes(routes_frame_saver.build_routes())
        client = await aiohttp_client(app)
        response = await client.get("/eps_frame_saver/probe", params={"path": str(clip)})
        assert response.status == 200
        assert seen["thread"] != threading.main_thread().name


class TestFrameSaverIsChanged:
    def test_token_tracks_mtime_size_and_frame(self, tmp_path: Path) -> None:
        clip = tmp_path / "c.mp4"
        clip.write_bytes(b"0123")
        first = nodes_frame_saver.EPSFrameSaver.IS_CHANGED(str(clip), 0)
        assert first == nodes_frame_saver.EPSFrameSaver.IS_CHANGED(str(clip), 0)
        assert nodes_frame_saver.EPSFrameSaver.IS_CHANGED(str(clip), 1) != first
        clip.write_bytes(b"01234567")
        assert nodes_frame_saver.EPSFrameSaver.IS_CHANGED(str(clip), 0) != first

    def test_missing_and_empty_paths(self) -> None:
        assert nodes_frame_saver.EPSFrameSaver.IS_CHANGED("", 0) == "missing"
        assert nodes_frame_saver.EPSFrameSaver.IS_CHANGED("/nope/never.mp4", 0) == "missing"

    def test_wired_video_is_never_cached_by_path(self) -> None:
        token = nodes_frame_saver.EPSFrameSaver.IS_CHANGED("/x.mp4", 0, video=object())
        assert token != token  # NaN: core's never-equal sentinel


# ------------------------------------------------------ picker previews (A4)


class TestPickerPreviewsCache:
    async def test_second_request_within_ttl_reuses_the_sweep(
        self, context: LibraryContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        routes_lora_picker._previews_cache_clear()
        calls: list[int] = []

        def fake_sweep(ctx, loras):
            calls.append(1)
            return ["a.safetensors"]

        monkeypatch.setattr(routes_lora_picker, "_loras_with_previews", fake_sweep)
        both = ["a.safetensors", "b.safetensors"]
        first = await routes_lora_picker._previews_cached(context, both)
        second = await routes_lora_picker._previews_cached(context, both)
        assert first == second == ["a.safetensors"]
        assert len(calls) == 1
        # a changed lora list is a new key -> fresh sweep
        await routes_lora_picker._previews_cached(context, ["a.safetensors"])
        assert len(calls) == 2
        routes_lora_picker._previews_cache_clear()


# ------------------------------------------ backend synthesis (same round)


class TestMultiplierModelLowBlocker:
    """Audit 2026-08-21: with the four classic sweep inputs wired and ONLY
    model_low unwired, the multiplier emitted bare None per run on the
    model_low output (the run_blocker was never built), and the v0.51.0
    consumed-but-unwired guard never covered slot 7."""

    def test_model_low_is_a_blocker_not_none_when_everything_else_is_wired(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import types

        class FakeBlocker:
            def __init__(self, message):
                self.message = message

        graph_mod = types.ModuleType("comfy_execution.graph")
        graph_mod.ExecutionBlocker = FakeBlocker
        pkg = types.ModuleType("comfy_execution")
        pkg.graph = graph_mod
        monkeypatch.setitem(sys.modules, "comfy_execution", pkg)
        monkeypatch.setitem(sys.modules, "comfy_execution.graph", graph_mod)
        from eps_image.nodes_cross_sweep import EPSCrossSweep

        out = EPSCrossSweep().run(
            model=["h1", "h2"], clip=["c1", "c2"], label=["A", "B"], vae=["v1", "v2"],
            image=["img"], text=["t"], pair_mode="paired", sweep_mode="aligned",
        )
        assert len(out[7]) == 2
        assert all(isinstance(element, FakeBlocker) for element in out[7])

    def test_consuming_model_low_with_it_unwired_fails_the_queue(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from eps_image.nodes_cross_sweep import EPSCrossSweep

        prompt = {
            "9": {"class_type": "KSampler", "inputs": {"model": ["1", 7]}},
            "1": {"class_type": "EPSCrossSweep", "inputs": {}},
        }
        with pytest.raises(ValueError) as exc:
            EPSCrossSweep().run(
                model=["h1"], clip=["c1"], label=["A"], vae=["v1"], text=["t"],
                pair_mode="paired", sweep_mode="aligned", prompt=prompt, unique_id="1",
            )
        assert "model_low" in str(exc.value)


class TestHealedLayoutScansOnce:
    def test_list_sets_called_once(
        self, context: LibraryContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[int] = []
        real = sets_store.list_sets

        def counting(ctx):
            calls.append(1)
            return real(ctx)

        monkeypatch.setattr(sets_store, "list_sets", counting)
        sets_store.healed_layout(context, {"categories": ["G"], "order": {"G": []}})
        assert calls == [1]


class TestLoraCacheAcrossASweep:
    def test_apply_stack_loads_each_file_once_per_cache(
        self, context: LibraryContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import types

        from lora_library import nodes_sets

        loads: list[str] = []
        fake_utils = types.ModuleType("comfy.utils")
        fake_utils.load_torch_file = lambda path, safe_load=True: loads.append(path) or {"p": path}
        fake_sd = types.ModuleType("comfy.sd")
        fake_sd.load_lora_for_models = lambda model, clip, sd, sm, sc: (model, clip)
        comfy_pkg = types.ModuleType("comfy")
        comfy_pkg.utils = fake_utils
        comfy_pkg.sd = fake_sd
        monkeypatch.setitem(sys.modules, "comfy", comfy_pkg)
        monkeypatch.setitem(sys.modules, "comfy.utils", fake_utils)
        monkeypatch.setitem(sys.modules, "comfy.sd", fake_sd)
        monkeypatch.setattr(context, "resolve_lora_path", lambda name: f"/loras/{name}")

        stack = [("a.safetensors", 0.5, 0.5), ("b.safetensors", 0.7, 0.7)]
        cache: dict = {}
        for _step in range(3):  # three strength steps of the same two files
            nodes_sets.LoraLibraryApplySet._apply_stack(context, "m", "c", stack, lora_cache=cache)
        assert loads == ["/loras/a.safetensors", "/loras/b.safetensors"]
        # no cache passed: the one-shot behavior is unchanged
        nodes_sets.LoraLibraryApplySet._apply_stack(context, "m", "c", stack)
        assert len(loads) == 4
