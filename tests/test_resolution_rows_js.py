"""EPS Resolution's 2026-10-05 round, run for real: the typed ``custom…`` ratio,
the ``rotate`` button and the two side-by-side button rows -- plus the proof
that none of it changed a byte of what a workflow saves.

Owner's words (2026-10-05): "For EPS resolution I should be able to type in a
ratio not just use presets. 3:4 and 4:3 should be added as presets. Next to the
copy from image button there should be a 'rotate' button that swaps the width
and height values. Save and Delete should be next to each other not stacked."

How this differs from the sibling ``test_resolution_*_js.py`` files: those
drive the pure exported helpers under Node and pin the stateful, closure-bound
code with SOURCE-TEXT assertions, because nothing in this repo ran
``attach()``. This file does: ``tests/resolution_rows_probe.mjs`` loads the
REAL, unmodified ``resolution.js`` + ``button_row.js`` and runs ``attach()``
against ``tests/fake_resolution_node.mjs`` -- a fake litegraph node that models,
exactly as the frontend 1.52.7 sources define them, ``serialize()``'s
by-raw-index ``widgets_values`` (holes and all), ``configure()``'s compacted
restore counter, ``computeSize()``'s widget-height rules, a DOM widget whose
``computeLayoutSize`` lives on the prototype, and both renderers' way of
writing a picked value (classic hands the callback the click event, Nodes 2.0
does not). The probe then clicks real ``<button>`` elements and picks real
values, and this file asserts on what it records.

What this cannot cover (the rig must -- the round report lists each): the real
Vue renderer's reactivity (the red "invalid" ring, ``options.values`` being
re-read when the value changes), real pixels (button looks, row heights,
alignment), and ComfyUI's real dialogs and ChangeTracker.

Skips cleanly when Node isn't installed.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from nested_layout import NODE, build_layout, run_probe

TESTS = Path(__file__).resolve().parent
PROBE = TESTS / "resolution_rows_probe.mjs"

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

APP_STUB = "export const app = { graph: null, configuringGraph: false, canvas: null }\n"
API_STUB = (
    "export const api = { fetchApi: async (route, init) => globalThis.__fetch(route, init), "
    "addEventListener: () => {}, apiURL: (p) => p }\n"
)

CUSTOM = "custom…"
PRESETS = ["none", "1:1", "5:4", "4:5", "4:3", "3:4", "16:9", "9:16"]
BACKEND_NAMES = [
    "height",
    "width",
    "resize_method",
    "interpolation",
    "multiple_of",
    "presets",
    "ratio",
]
FRONTEND_ONLY = [
    "eps_resolution_copy_row",
    "preset",
    "eps_resolution_preset_row",
    "eps_resolution_grid",
]


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict:
    root = build_layout(
        tmp_path_factory.mktemp("resolution_rows"),
        eps_image=("resolution.js", "button_row.js"),
        api_stub=API_STUB,
        app_stub=APP_STUB,
    )
    for name in ("fake_dom.mjs", "fake_resolution_node.mjs"):
        shutil.copyfile(TESTS / name, root / name)
    return run_probe(root, PROBE.read_text(encoding="utf-8"), timeout=120)


# ============================================================ A. layout + flags


class TestLayoutAndFlags:
    def test_widget_order_rows_above_and_below_with_the_backend_widgets_untouched(
        self, run: dict
    ) -> None:
        """[copy|rotate row] sits ABOVE the size fields (the non-tail position
        whose serialization hole is compacted below); the preset combo and the
        Save|Delete row sit right before the pad, which stays last."""
        assert run["layout"]["names"] == [
            "eps_resolution_copy_row",
            "height",
            "width",
            "resize_method",
            "interpolation",
            "multiple_of",
            "presets",
            "ratio",
            "preset",
            "eps_resolution_preset_row",
            "eps_resolution_grid",
        ]
        assert run["layout"]["padIsLast"] is True

    def test_four_stacked_button_widgets_became_two_row_widgets(self, run: dict) -> None:
        names = run["layout"]["names"]
        for gone in ("copy from image", "rotate", "Save", "Delete"):
            assert gone not in names
        assert set(run["layout"]["rows"]) == {
            "eps_resolution_copy_row",
            "eps_resolution_preset_row",
        }

    def test_every_frontend_only_widget_keeps_both_serialize_flags_false(
        self, run: dict
    ) -> None:
        """`widget.serialize` gates the workflow FILE, `options.serialize` gates
        the API PROMPT -- not interchangeable (executionUtil.ts says so)."""
        layout = run["layout"]
        assert layout["frontendOnlyNames"] == FRONTEND_ONLY
        for name in FRONTEND_ONLY:
            assert layout["serializeFlags"][name] is False, name
            assert layout["optionSerializeFlags"][name] is False, name
        for name in BACKEND_NAMES:
            assert name not in layout["serializeFlags"], f"{name} must serialize"

    def test_no_phantom_inputs_in_the_queued_prompt(self, run: dict) -> None:
        """A past regression (rig-caught 2026-08-14): every queued prompt
        carried a phantom `"Save"`/`"Delete"`/`"copy from image"` input. The
        names a prompt would carry are exactly the seven backend widgets."""
        assert run["layout"]["promptInputNames"] == BACKEND_NAMES
        assert run["phantom"]["promptInputNames"] == BACKEND_NAMES
        assert run["phantom"]["anyForbidden"] == []

    def test_rows_never_serialize_a_value(self, run: dict) -> None:
        for name, row in run["layout"]["rows"].items():
            assert row["serialize"] is False, name
            assert row["optionSerialize"] is False, name
            assert row["serializeValue"] == "undefined", name

    def test_rows_are_hidden_from_the_properties_panel(self, run: dict) -> None:
        for name, row in run["layout"]["rows"].items():
            assert row["hideInPanel"] is True, name

    def test_rows_use_their_own_widget_type_not_a_core_alias(self, run: dict) -> None:
        """A core-aliased type (`button`, `combo`, ...) makes Nodes 2.0 render
        core's own Vue component INSTEAD of our element."""
        for row in run["layout"]["rows"].values():
            assert row["type"] == "eps_button_row"

    def test_each_row_is_a_fixed_height_in_both_renderers(self, run: dict) -> None:
        """Classic: computeSize/computedHeight/getMin+MaxHeight agree. Nodes
        2.0: an explicit CSS height, `flex: 0 0 auto`, and `computeLayoutSize`
        shadowed to undefined so its grid row is `min-content`, not `auto`."""
        for name, row in run["layout"]["rows"].items():
            reported = row["computeSize"][1]
            assert reported == 26, name  # element 22 + 2*margin 4 - the +4 arrange adds
            assert row["computedHeight"] == reported + 4, name
            assert row["getMinHeight"] == row["getMaxHeight"] == reported, name
            assert row["margin"] == 4, name
            # the visible box the classic overlay draws == the element's own height
            assert reported + 4 - 2 * row["margin"] == 22, name
            style = row["elementStyle"]
            assert style["height"] == "22px" and style["minHeight"] == "22px", name
            assert style["flex"] == "0 0 auto", name

    def test_compute_layout_size_is_shadowed_not_deleted(self, run: dict) -> None:
        """An OWN property whose value is undefined: `typeof` reads
        'undefined' (so Nodes 2.0's `hasLayoutSize` is false), while the
        prototype still has the method (so the test would notice if it did not
        need shadowing)."""
        for name, row in run["layout"]["rows"].items():
            assert row["ownComputeLayoutSizeShadow"] is True, name
            assert row["typeofComputeLayoutSize"] == "undefined", name
            assert row["prototypeStillHasMethod"] is True, name

    def test_copy_row_buttons_are_copy_on_the_left_rotate_on_the_right(self, run: dict) -> None:
        buttons = run["layout"]["rows"]["eps_resolution_copy_row"]["buttons"]
        assert [b["label"] for b in buttons] == ["copy from image", "rotate"]
        assert all(b["disabled"] is False for b in buttons)

    def test_preset_row_buttons_are_save_then_delete_with_delete_disabled(
        self, run: dict
    ) -> None:
        buttons = run["layout"]["rows"]["eps_resolution_preset_row"]["buttons"]
        assert [b["label"] for b in buttons] == ["Save", "Delete"]
        assert [b["disabled"] for b in buttons] == [False, True]

    def test_every_button_keeps_a_tooltip_and_is_a_plain_button(self, run: dict) -> None:
        for row in run["layout"]["rows"].values():
            for button in row["buttons"]:
                assert button["title"], button["label"]
                assert button["type"] == "button"  # never a form-submitting default
                assert button["className"] == "eps-btn-row-btn"
            assert row["rowClass"] == "eps-btn-row"

    def test_tooltips_carry_the_original_wording(self, run: dict) -> None:
        save = run["layout"]["rows"]["eps_resolution_preset_row"]["buttons"]
        assert "named size preset" in save[0]["title"]
        assert "exactly one preset" in save[1]["title"]
        rotate = run["layout"]["rows"]["eps_resolution_copy_row"]["buttons"][1]["title"]
        assert "Swap width and height" in rotate and "16:9 becomes 9:16" in rotate


# =================== B. widgets_values: byte-identical to v1.5.0, both directions


#: What each golden workflow array becomes once loaded and saved again. A
#: workflow saved before `ratio` / `presets` existed is padded with the
#: defaults when saved again -- the same thing v1.5.0 did.
GOLDEN = {
    "freshDefaults": (
        [1024, 1024, "stretch", "bilinear", 0, "[]", "none"],
        '[1024,1024,"stretch","bilinear",0,"[]","none"]',
    ),
    "plainEdited": (
        [333, 777, "crop to fill", "lanczos", 0, '["Portrait"]', "none"],
        '[333,777,"crop to fill","lanczos",0,"[\\"Portrait\\"]","none"]',
    ),
    "lockedAndSnapped": (
        [576, 1024, "pad", "area", 64, "[]", "16:9"],
        '[576,1024,"pad","area",64,"[]","16:9"]',
    ),
    "portraitPreset": (
        [1280, 960, "stretch", "bilinear", 0, "[]", "3:4"],
        '[1280,960,"stretch","bilinear",0,"[]","3:4"]',
    ),
    "typedRatio": (
        [418, 1000, "stretch", "bilinear", 0, "[]", "2.39:1"],
        '[418,1000,"stretch","bilinear",0,"[]","2.39:1"]',
    ),
    "beforeRatio": (
        [512, 768, "stretch", "bilinear", 0, "[]", "none"],
        '[512,768,"stretch","bilinear",0,"[]","none"]',
    ),
    "beforePresets": (
        [512, 768, "stretch", "bilinear", 0, "[]", "none"],
        '[512,768,"stretch","bilinear",0,"[]","none"]',
    ),
}


class TestSavedWorkflowsAreByteIdentical:
    """"Prove with tests that the saved widgets_values is byte-identical to
    v1.5.0's for a fresh node and for a node loaded from an older workflow, and
    that older-build workflows load here and vice versa."

    v1.5.0 is modelled by its widget list -- ONE leading `copy from image`
    button, the seven backend widgets, the preset combo, Save, Delete and the
    pad, every frontend widget `serialize: false`, and the same hole-compacting
    `onSerialize` -- run through the same serialize/configure algorithm. Swapping
    its four button widgets for two row widgets changes how many frontend-only
    widgets exist, and so how many holes the RAW array has, but not one byte
    that survives compaction. These tests compare the bytes."""

    @pytest.mark.parametrize("case", sorted(GOLDEN))
    def test_the_saved_array_is_the_same_bytes_as_v150(self, run: dict, case: str) -> None:
        data = run["compat"][case]
        expected_values, expected_json = GOLDEN[case]
        assert data["savedNew"] == expected_values
        assert data["saved150"] == expected_values
        assert data["savedNewJson"] == expected_json
        assert data["saved150Json"] == expected_json
        assert data["savedNewJson"] == data["saved150Json"]

    @pytest.mark.parametrize("case", sorted(GOLDEN))
    def test_an_older_build_workflow_loads_here_exactly_as_it_loads_there(
        self, run: dict, case: str
    ) -> None:
        data = run["compat"][case]
        assert data["loadedNew"] == data["loaded150"]
        # and the widgets land by NAME on the right values (positional restore intact)
        loaded = dict(data["loadedNew"])
        for name, value in zip(BACKEND_NAMES, data["input"], strict=False):
            assert loaded[name] == value, (case, name)

    @pytest.mark.parametrize("case", sorted(GOLDEN))
    def test_a_workflow_saved_here_loads_on_the_older_build(self, run: dict, case: str) -> None:
        data = run["compat"][case]
        assert data["newSavedLoadedBy150"] == data["loaded150"]

    def test_a_full_length_array_round_trips_unchanged(self, run: dict) -> None:
        for case, (values, _json) in GOLDEN.items():
            data = run["compat"][case]
            if len(data["input"]) == len(BACKEND_NAMES):
                assert data["savedNew"] == data["input"] == values, case

    def test_the_raw_array_never_carries_a_hole_or_a_null_for_a_button(self, run: dict) -> None:
        """The failure the compaction exists for (rig-proven 2026-08-14): a
        leading skipped widget turned [333, 777, ...] into [null, 333, ...]."""
        for case, data in run["compat"].items():
            if case == "preHeightFirst":
                continue
            assert None not in data["savedNew"], case
            assert len(data["savedNew"]) == len(BACKEND_NAMES), case

    def test_the_by_name_copy_lists_only_the_backend_widgets(self, run: dict) -> None:
        assert list(run["layout"]["namedValues"]) == BACKEND_NAMES

    def test_a_pre_height_first_workflow_still_gets_its_two_values_swapped_back(
        self, run: dict
    ) -> None:
        """The v0.61.0 migration shim (no layout stamp -> width-first values)
        is unchanged by any of this."""
        assert run["compat"]["preHeightFirst"] == {"height": 480, "width": 640}

    def test_no_downgrade_hazard_the_saved_values_hold_no_frontend_widget(
        self, run: dict
    ) -> None:
        saved = run["layout"]["savedValues"]
        assert saved == [1024, 1024, "stretch", "bilinear", 0, "[]", "none"]
        assert len(saved) == len(BACKEND_NAMES)


# ======================================================= D. the ratio's option list


class TestRatioOptionList:
    def test_the_list_is_a_function_edited_in_place(self, run: dict) -> None:
        """A function so a typed value is never absent from it (Nodes 2.0's
        select flags a value that is not in its list), and assigned onto the
        EXISTING options object so Nodes 2.0, which keeps a reference to the
        original, sees it."""
        data = run["ratioOptions"]
        assert data["valuesIsFunction"] is True
        assert data["sameOptionsObject"] is True
        assert data["argumentsIgnored"] is True  # classic calls it with (widget, node)

    def test_presets_first_custom_last(self, run: dict) -> None:
        assert run["ratioOptions"]["listBefore"] == [*PRESETS, CUSTOM]
        assert run["ratioOptions"]["listWithPreset"] == [*PRESETS, CUSTOM]

    def test_a_typed_ratio_is_folded_in_before_custom(self, run: dict) -> None:
        assert run["ratioOptions"]["listWithCustom"] == [*PRESETS, "21:9", CUSTOM]

    def test_garbage_and_the_command_are_never_folded_in(self, run: dict) -> None:
        assert run["ratioOptions"]["listWithGarbage"] == [*PRESETS, CUSTOM]
        assert run["ratioOptions"]["listWhileSentinel"] == [*PRESETS, CUSTOM]


# ============================================================== E. the custom… flow


class TestCustomRatioFlow:
    def test_picking_custom_puts_the_widget_straight_back_then_applies_the_answer(
        self, run: dict
    ) -> None:
        e1 = run["custom"]["e1"]
        # the sentinel never stays: before the dialog even answers, the widget
        # already holds what it held
        assert e1["valueRightAfterThePick"] == "none"
        assert e1["valueAfter"] == "2.39:1"  # ' 2.39 : 1 ' typed, normalised
        assert e1["widthHeight"] == [1024, 428]  # width kept EXACT, height derived once
        assert e1["listAfter"] == [*PRESETS, "2.39:1", CUSTOM]
        assert e1["toasts"] == []

    def test_the_box_is_the_extension_dialog_prefilled_with_the_current_value(
        self, run: dict
    ) -> None:
        e1 = run["custom"]["e1"]
        assert e1["canvasPromptCalls"] == 0  # the extension dialog was used
        (call,) = e1["dialogCalls"]
        assert call["title"] == "Custom ratio"
        assert "21:9" in call["message"] and "2.39:1" in call["message"]
        assert call["defaultValue"] == "none"
        # ...and with a custom ratio current, THAT is the prefill
        assert run["custom"]["e2"]["prefill"] == "2.39:1"

    def test_cancel_keeps_the_previous_ratio_and_says_nothing(self, run: dict) -> None:
        e2 = run["custom"]["e2"]
        assert e2["valueAfterVuePick"] == "2.39:1"  # reverted at once, no event needed
        assert e2["valueAfterCancel"] == "2.39:1"
        assert e2["toasts"] == []
        assert e2["widthHeight"] == [1024, 428]

    @pytest.mark.parametrize(
        "key,fragment",
        [
            ("invalid_letters", '"banana" isn\'t a ratio'),
            ("invalid_zero", '"0:5" isn\'t a ratio'),
            ("invalid_negative", '"-4:3" isn\'t a ratio'),
            ("invalid_three", '"1:2:3" isn\'t a ratio'),
            ("invalid_empty", "Nothing typed"),
        ],
    )
    def test_an_invalid_entry_toasts_and_keeps_the_previous_ratio(
        self, run: dict, key: str, fragment: str
    ) -> None:
        data = run["custom"][key]
        assert data["value"] == "2.39:1"
        assert data["widthHeight"] == [1024, 428]
        assert len(data["toasts"]) == 1
        toast = data["toasts"][0]
        assert toast.startswith("warn: ") and fragment in toast
        assert "ratio stays 2.39:1" in toast
        if key != "invalid_empty":
            assert "21:9" in toast  # says what to type instead

    @pytest.mark.parametrize(
        "typed,stored",
        [
            ("21x9", "21:9"),
            ("21 / 9", "21:9"),
            ("21X9", "21:9"),
            ("21\u00d79", "21:9"),
            ("2.390 : 1.0", "2.39:1"),
            ("1.85:1", "1.85:1"),
            ("16:9", "16:9"),
            ("none", "none"),
            ("NONE", "none"),
        ],
    )
    def test_spellings_are_stored_in_one_canonical_form(
        self, run: dict, typed: str, stored: str
    ) -> None:
        assert run["custom"]["spellings"][typed] == stored

    def test_retyping_the_current_ratio_is_a_quiet_no_op(self, run: dict) -> None:
        retype = run["custom"]["retype"]
        assert retype["callbackCalls"] == [CUSTOM]  # only the pick itself, no second write
        assert retype["value"] == "16:9"
        assert retype["sizeUnchanged"] is True

    def test_the_box_falls_back_tier_by_tier_and_is_never_a_silent_no_op(
        self, run: dict
    ) -> None:
        tiers = run["custom"]["tiers"]
        # classic: canvas.prompt gets the REAL click event (it positions off it)
        assert tiers["classicPromptTitle"] == "Ratio as width:height, like 21:9 or 2.39:1"
        assert tiers["classicPromptValue"] == "none"
        assert tiers["classicEventForwarded"] is True
        assert tiers["afterClassic"] == "3:2"
        # Nodes 2.0 hands no event: canvas.prompt is called with null (centred)
        assert tiers["vuePromptEvent"] is None
        # no canvas.prompt: window.prompt, its answer normalised
        assert tiers["windowPromptValue"] == "5:3"
        assert tiers["windowAnswerApplied"] == "7:5"
        # window.prompt throws: the built-in DOM dialog opens, and the widget
        # still holds a real ratio while it is open
        assert tiers["domDialogOpened"] is True
        assert tiers["valueWhileDomDialogOpen"] == "7:5"

    def test_a_dialog_that_throws_falls_through_to_the_canvas_prompt(self, run: dict) -> None:
        assert run["custom"]["dialogRejects"] == {"value": "9:5", "canvasPromptCalls": 1}


# ================================== F. programmatic writes (Universal State Apply)


class TestProgrammaticWrites:
    def test_apply_of_a_typed_ratio_conforms_like_any_pick(self, run: dict) -> None:
        """Apply writes `widget.value = v; widget.callback(v, canvas, node)` --
        a typed ratio is just another `W:H` string to the lock."""
        assert run["apply"]["applied"] == {"value": "2.39:1", "size": [1200, 502]}

    def test_another_spelling_arrives_canonical(self, run: dict) -> None:
        assert run["apply"]["respelled"] == "16:9"

    def test_garbage_is_refused_and_the_committed_ratio_comes_back(self, run: dict) -> None:
        assert run["apply"]["afterGarbage"] == "16:9"
        assert len(run["apply"]["toasts"]) == 1
        assert '"banana" isn\'t a ratio' in run["apply"]["toasts"][0]


class TestRestore:
    """configure() restores the ratio with a bare assignment and no callback,
    so the custom… flow re-syncs what it remembers."""

    def test_a_restored_alternate_spelling_becomes_canonical(self, run: dict) -> None:
        assert run["restore"]["canonicalised"] == "21:9"

    def test_the_remembered_ratio_after_a_restore_is_the_restored_one(self, run: dict) -> None:
        # custom… then Cancel puts back THE RESTORED ratio, not a stale 'none'
        assert run["restore"]["afterCancel"] == "21:9"

    def test_an_unreadable_saved_ratio_is_kept_but_said_out_loud_once(self, run: dict) -> None:
        assert run["restore"]["garbageKept"] == "zzz"  # never destroy what a file says
        (toast,) = run["restore"]["garbageToasts"]
        assert '"zzz" isn\'t a ratio' in toast and "lock is off" in toast

    def test_a_saved_custom_command_becomes_none(self, run: dict) -> None:
        assert run["restore"]["sentinelRestored"] == "none"


# ========================================================================= H. rotate


class TestRotate:
    def test_swaps_width_and_height(self, run: dict) -> None:
        r = run["rotate"]["noRatio"]
        assert r["before"]["dims"] == [1280, 720]
        assert r["after"]["dims"] == [720, 1280]
        assert r["after"]["ratio"] == "none"  # no lock, nothing to flip
        assert r["toasts"] == []

    @pytest.mark.parametrize(
        "case,before,after",
        [
            ("lock169", ("16:9", [1024, 576]), ("9:16", [576, 1024])),
            ("lock34", ("3:4", [768, 1024]), ("4:3", [1024, 768])),
            ("customDecimal", ("2.39:1", [1000, 418]), ("1:2.39", [418, 999])),
            ("square11", ("1:1", [640, 640]), ("1:1", [640, 640])),
        ],
    )
    def test_a_locked_ratio_flips_with_the_numbers(
        self, run: dict, case: str, before: tuple, after: tuple
    ) -> None:
        r = run["rotate"][case]
        assert (r["before"]["ratio"], r["before"]["dims"]) == before
        assert (r["after"]["ratio"], r["after"]["dims"]) == after

    def test_the_swap_under_an_inexact_lock_is_conformed_like_every_write(
        self, run: dict
    ) -> None:
        """418 wide at 1:2.39 IS 999 tall (the backend derives it from the
        width), not an exact 1000: the panel must show the number that runs.
        Rotating back lands on the lock's answer for that width."""
        twice = run["rotate"]["customTwice"]
        assert twice["start"] == {"dims": [1000, 418], "ratio": "2.39:1"}
        assert twice["once"] == {"dims": [418, 999], "ratio": "1:2.39"}
        assert twice["twice"] == {"dims": [999, 418], "ratio": "2.39:1"}

    def test_one_and_one_and_none_stay_as_they_are(self, run: dict) -> None:
        assert run["rotate"]["square11"]["after"]["ratio"] == "1:1"
        assert run["rotate"]["noRatio"]["after"]["ratio"] == "none"

    @pytest.mark.parametrize("case", ["square11", "squareNone"])
    def test_a_square_with_nothing_to_flip_says_so_instead_of_doing_nothing_silently(
        self, run: dict, case: str
    ) -> None:
        r = run["rotate"][case]
        assert r["toasts"] == ["info: Width and height are already the same -- nothing to rotate."]
        assert r["after"] == r["before"]

    def test_a_zero_axis_swaps_too(self, run: dict) -> None:
        r = run["rotate"]["zeroAxis"]
        assert (r["before"]["dims"], r["after"]["dims"]) == ([0, 800], [800, 0])

    def test_it_is_a_manual_edit_so_it_clears_the_active_preset(self, run: dict) -> None:
        r = run["rotate"]["withPreset"]
        assert r["before"]["presets"] == '["Portrait"]'
        assert r["before"]["dims"] == [832, 1216]
        assert r["after"]["presets"] == "[]"  # the numbers shown are what runs
        assert r["comboValue"] == "__eps_resolution_preset_none__"  # the combo says (none)
        assert r["after"]["dims"] == [1216, 832]

    def test_it_clears_the_preset_under_a_lock_too_and_flips_the_lock(self, run: dict) -> None:
        r = run["rotate"]["withPresetAndLock"]
        assert r["before"]["presets"] == '["Portrait"]' and r["before"]["ratio"] == "4:5"
        assert r["after"]["presets"] == "[]"
        assert (r["after"]["ratio"], r["after"]["dims"]) == ("5:4", [1040, 832])

    def test_it_does_not_resnap_to_multiple_of(self, run: dict) -> None:
        """1000 x 500 is deliberately NOT a multiple of 64 (written bare,
        bypassing the snap); swapping must give 500 x 1000 exactly, not
        512 x 1024 -- a re-snap could only change a number the user did not ask
        to change."""
        r = run["rotate"]["multipleOfNotResnapped"]
        assert r["before"]["dims"] == [1000, 500]
        assert r["after"]["dims"] == [500, 1000]

    def test_multiple_of_and_a_lock_still_agree(self, run: dict) -> None:
        r = run["rotate"]["multipleOfWithLock"]
        assert (r["after"]["ratio"], r["after"]["dims"]) == ("9:16", [576, 1024])

    def test_the_snap_suppression_is_released_after_a_rotate(self, run: dict) -> None:
        r = run["rotate"]["snapWrapperRestored"]
        assert r["rotated"] == [768, 1280]
        assert r["suppressFlag"] is False
        assert r["width"] == 1024  # a typed 1000 after the rotate snaps to 64 again

    def test_the_pad_repaints_after_a_rotate(self, run: dict) -> None:
        assert run["rotate"]["lock169"]["repaintRequested"] is True
        assert run["rotate"]["noRatio"]["repaintRequested"] is True

    def test_one_click_is_one_undo_capture(self, run: dict) -> None:
        for case in ("noRatio", "lock169", "customDecimal", "withPreset"):
            assert run["rotate"][case]["undoCapturesForClick"] == 1, case

    def test_rotating_twice_on_a_clean_lock_gets_back_to_the_start(self, run: dict) -> None:
        """1024 x 576 at 16:9 rotates to 576 x 1024 at 9:16 -- both exact -- so
        two rotations are the identity (landscape -> portrait -> landscape)."""
        r = run["rotate"]["lock169"]
        assert r["before"]["dims"] == [1024, 576] and r["after"]["dims"] == [576, 1024]


# ======================================================= N. the lock stays exact


class TestTheLockDoesNotWalkAwayFromWhatWasTyped:
    """Found while making typed ratios usable. The lock's wraps re-derived the
    other axis whenever either field's callback fired, and a derived write is
    itself a write that fires a callback -- so one typed edit bounced between
    the two fields and settled wherever both directions agreed. v1.5.0, 16:9,
    type width 1000: 1001 x 563. Typed ratio 2.39:1 at 1024 wide: 1023 x 428.
    The lock's own contract says the field you edit is kept exactly."""

    def test_each_edit_lands_exactly_where_it_was_typed(self, run: dict) -> None:
        steps = run["lockStability"]
        assert steps["pick169At1024"] == [1024, 576]
        assert steps["typed1000"] == [1000, 562]  # NOT 1001
        assert steps["typed1001"] == [1001, 563]
        assert steps["typedHeight563"] == [1001, 563]  # height kept, width derived
        assert steps["typed1000At239"] == [1000, 418]  # NOT 999
        assert steps["typedHeight418At239"] == [999, 418]  # the field typed is kept

    def test_picking_a_typed_ratio_keeps_the_width(self, run: dict) -> None:
        assert run["custom"]["e1"]["widthHeight"] == [1024, 428]  # NOT 1023
        assert run["lockStability"]["pick239"] == [1001, 419]  # width untouched

    def test_the_pad_and_copy_writes_are_exact_too(self, run: dict) -> None:
        assert run["lockStability"]["copyAt239"] == [1000, 418]

    @pytest.mark.parametrize(
        "width,ratio,step",
        [
            (1000, "16:9", "typed1000"),
            (1001, "16:9", "typed1001"),
            (1000, "2.39:1", "typed1000At239"),
        ],
    )
    def test_the_panels_derived_height_is_the_one_the_backend_computes(
        self, run: dict, width: int, ratio: str, step: str
    ) -> None:
        """1000 x 562 at 16:9: JavaScript used to round 562.5 UP (563) while
        Python rounds it to the even 562, so the pad showed a number the run
        would not compute. The panel now rounds half to even too -- and with the
        lock no longer bouncing the width, that tie is reachable by typing."""
        from eps_image import nodes_resolution

        _, height = nodes_resolution.conform_to_ratio(width, 1, ratio, 0, "width")
        assert run["lockStability"][step] == [width, height]


# ============================================================ I. copy from image


class TestCopyFromImage:
    def test_unwired_says_so(self, run: dict) -> None:
        assert run["copy"]["unwired"] == ["warn: Wire an image into this node first."]

    def test_copies_the_wired_size_as_one_undo_step(self, run: dict) -> None:
        assert run["copy"]["copied"] == [800, 600]
        assert run["copy"]["copyUndo"] == 1

    def test_a_lock_conforms_it_and_the_panel_says_so_but_the_anchor_stays_exact(
        self, run: dict
    ) -> None:
        # 1000 x 300 at 4:3 with multiple_of 64: width kept EXACT (copy means
        # copy -- no snap), height derived 750 and snapped to 768
        assert run["copy"]["withLock"] == [1000, 768]
        assert run["copy"]["toastsWithLock"] == [
            "warn: The wired image is 1000 x 300; conformed to the locked ratio -- "
            "applied 1000 x 768."
        ]


# ===================================================== J. the Save | Delete row


class TestSaveDeleteRow:
    def test_delete_is_disabled_until_exactly_one_preset_is_picked(self, run: dict) -> None:
        row = run["presetRow"]
        assert row["fresh0"] == {"saveDisabled": False, "deleteDisabled": True}
        assert row["afterPick"] == {"deleteDisabled": False}

    def test_a_manual_edit_unselects_the_preset_and_disables_delete_again(
        self, run: dict
    ) -> None:
        assert run["presetRow"]["deleteWhenNothingSelected"] == {"disabled": True}

    def test_a_click_on_a_disabled_delete_does_nothing(self, run: dict) -> None:
        assert run["presetRow"]["postsAfterDisabledClick"] == 0

    def test_save_hands_canvas_prompt_the_real_click_event_and_prefills_the_active_name(
        self, run: dict
    ) -> None:
        row = run["presetRow"]
        assert row["promptTitle"] == "Preset name"
        assert row["promptValue"] == "Portrait"  # exactly one selected -> "update"
        assert (row["promptEventClientX"], row["promptEventClientY"]) == (321, 123)

    def test_save_relabels_and_disables_both_buttons_while_in_flight_then_restores(
        self, run: dict
    ) -> None:
        row = run["presetRow"]
        (mid,) = row["midLabels"]
        assert mid == {"save": "Saving…", "saveDisabled": True, "deleteDisabled": True}
        after = row["afterSave"]
        assert after["saveLabel"] == "Save" and after["saveDisabled"] is False
        # Delete is RE-DERIVED from the selection (the new preset is selected -> enabled)
        assert after["deleteDisabled"] is False
        assert after["presetsWidget"] == '["Square"]'
        assert after["toasts"] == ['success: Saved preset "Square".']

    def test_save_posts_the_five_fields_and_the_base_mtime(self, run: dict) -> None:
        (post,) = run["presetRow"]["afterSave"]["posts"]
        assert post["route"] == "/eps_resolution/presets/save"
        assert post["body"] == {
            "name": "Square",
            "values": {
                "width": 640,
                "height": 640,
                "resize_method": "stretch",
                "interpolation": "bilinear",
                "multiple_of": 0,
            },
            "base_mtime": 1,
        }

    def test_delete_posts_the_picked_name_and_restores_its_label(self, run: dict) -> None:
        deleted = run["presetRow"]["delete"]
        assert deleted["posts"] == [
            {"route": "/eps_resolution/presets/delete", "body": {"name": "Wide", "base_mtime": 1}}
        ]
        assert deleted["label"] == "Delete"


class TestPresetsPropertyHidesTheRow:
    def test_off_hides_the_row_and_the_combo_in_every_way_and_clears_the_selection(
        self, run: dict
    ) -> None:
        hidden = run["presetsProperty"]["hidden"]
        assert hidden["row"] == {"widget": True, "option": True, "display": "none"}
        assert hidden["combo"] == {"widget": True, "option": True}
        assert hidden["selectionCleared"] == "[]"
        assert hidden["heightShrank"] is True  # the node reclaims the row's space

    def test_options_are_mutated_in_place_never_replaced(self, run: dict) -> None:
        """Nodes 2.0 keeps a reference to the ORIGINAL options object, so a
        replacement is invisible to it (audit V-02)."""
        hidden = run["presetsProperty"]["hidden"]
        assert hidden["rowOptionsSameObject"] is True
        assert hidden["comboOptionsSameObject"] is True

    def test_back_on_shows_everything_again(self, run: dict) -> None:
        shown = run["presetsProperty"]["shown"]
        assert shown["row"] == {"widget": False, "option": False, "display": ""}
        assert shown["combo"] == {"widget": False, "option": False}
        assert run["presetsProperty"]["heightRestored"] is True


class TestNoDomWidgetFrontend:
    def test_a_frontend_without_add_dom_widget_fails_soft(self, run: dict) -> None:
        """The pack's fail-soft posture: no rows, no throw, the backend widgets
        and the combo still there, the saved values unchanged."""
        data = run["noDomWidgets"]
        assert data["threw"] is None
        assert "eps_resolution_copy_row" not in data["names"]
        assert "eps_resolution_preset_row" not in data["names"]
        assert data["names"][: len(BACKEND_NAMES)] == BACKEND_NAMES
        assert data["savedValues"] == [1024, 1024, "stretch", "bilinear", 0, "[]", "none"]


# ================================================================ output is JSON


def test_the_probe_output_is_one_json_object(run: dict) -> None:
    json.dumps(run)  # nothing non-serialisable slipped through
    assert set(run) >= {
        "layout",
        "compat",
        "phantom",
        "ratioOptions",
        "custom",
        "apply",
        "restore",
        "rotate",
        "copy",
        "presetRow",
        "presetsProperty",
        "noDomWidgets",
        "lockStability",
    }
