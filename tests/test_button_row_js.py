"""``web/eps_image/button_row.js`` -- the shared side-by-side button row (owner
ask 2026-10-05: "Save and Delete should be next to each other not stacked",
"next to the copy from image button there should be a 'rotate' button").

One helper builds both of EPS Resolution's rows (``Save | Delete`` and
``copy from image | rotate``). A litegraph ``button`` widget is always one
full-width row, so a side-by-side pair has to be a DOM widget holding real HTML
buttons -- and the things a native button widget got for free (never saved,
never queued, a fixed height in both renderers, a one-undo-step click) have to
be rebuilt by hand. This file pins each of them.

``tests/button_row_probe.mjs`` drives the REAL module under Node over the shared
fake DOM (``tests/fake_dom.mjs``) and the fake litegraph node
(``tests/fake_resolution_node.mjs``, which models a DOM widget whose
``computeLayoutSize`` lives on the prototype, as ``DOMWidgetImpl``'s does). What
only the rig can show -- real pixels, Nodes 2.0's grid rows, the real
ChangeTracker -- is listed in the round report.

Skips cleanly when Node isn't installed.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
from nested_layout import NODE, build_layout, run_probe

TESTS = Path(__file__).resolve().parent
REPO_ROOT = TESTS.parent
PROBE = TESTS / "button_row_probe.mjs"
BUTTON_ROW_JS = REPO_ROOT / "web" / "eps_image" / "button_row.js"

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

APP_STUB = "export const app = { graph: null, configuringGraph: false }\n"

#: Core's widget-type alias table (frontend-contract §2.2): a DOM widget typed
#: with one of these is REPLACED by core's own Vue component in Nodes 2.0.
CORE_WIDGET_TYPES = {
    "button", "BUTTON", "string", "STRING", "text", "int", "INT", "float", "FLOAT", "number",
    "slider", "gradientslider", "boolean", "BOOLEAN", "toggle", "combo", "COMBO", "asset",
    "color", "COLOR", "textarea", "TEXTAREA", "multiline", "customtext", "chart",
    "imagecompare", "galleria", "markdown", "MARKDOWN", "progressText", "textPreview",
    "TEXT_PREVIEW", "legacy", "audiorecord", "audioUI", "load3D", "load3DAdvanced",
    "cameraInfo", "imagecrop", "boundingbox", "curve", "painter", "compositor", "range",
    "boundingboxes", "videoedit", "colors", "resolutionpreview",
}  # fmt: skip


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict:
    root = build_layout(
        tmp_path_factory.mktemp("button_row"),
        eps_image=("button_row.js",),
        app_stub=APP_STUB,
    )
    for name in ("fake_dom.mjs", "fake_resolution_node.mjs"):
        shutil.copyfile(TESTS / name, root / name)
    return run_probe(root, PROBE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def source() -> str:
    return BUTTON_ROW_JS.read_text(encoding="utf-8")


# ------------------------------------------------------------------- the height


class TestHeightArithmetic:
    def test_reported_height_is_element_plus_margins_minus_the_arrange_pad(
        self, run: dict
    ) -> None:
        """The classic overlay boxes a DOM widget at `computedHeight - 2*margin`
        and `_arrangeWidgets` sets `computedHeight = computeSize()[1] + 4`, so
        the VISIBLE box equals the element only when computeSize()[1] =
        element + 2*margin - 4."""
        heights = run["heights"]
        assert heights["default"] == 26  # 22 + 2*4 - 4
        assert heights["tenMargin"] == 38  # 22 + 2*10 - 4
        assert heights["zeroMargin"] == 16
        assert heights["floor"] == 1  # never 0 or negative
        assert heights["constants"] == [22, 4, "eps_button_row"]

    def test_the_visible_box_equals_the_elements_own_height(self, run: dict) -> None:
        reported = run["flags"]["computeSizeNoArg"][1]
        assert reported + 4 - 2 * run["flags"]["margin"] == 22

    def test_the_node_budgets_exactly_one_arrange_stride_for_the_row(self, run: dict) -> None:
        assert run["nodeBudget"]["arrangeStride"] == 30
        # (an empty node: slots row + the one widget stride + the 8 + 6 litegraph adds)
        assert run["nodeBudget"]["computeSize"][1] == 20 * 13 + 30 + 8 + 6

    def test_classic_knobs_all_report_the_same_number(self, run: dict) -> None:
        flags = run["flags"]
        assert flags["computeSizeNoArg"][1] == flags["computeSizeArg"][1] == 26
        assert flags["getMin"] == flags["getMax"] == 26
        assert flags["computedHeight"] == 30
        # computeSize() with no argument falls back to the node's own width
        assert flags["computeSizeNoArg"][0] == 210 and flags["computeSizeArg"][0] == 300


# ------------------------------------------------------------------ the element


class TestStructure:
    def test_the_row_is_a_flex_container_of_equal_buttons(self, run: dict) -> None:
        data = run["structure"]
        assert data["elementTag"] == "div" and data["elementClass"] == "eps-btn-row"
        assert data["childTags"] == ["button", "button", "button"]
        assert data["labels"] == ["Alpha", "Beta", "Gamma"]
        assert data["titles"] == ["first", "second", ""]
        assert data["buttonTypes"] == ["button", "button", "button"]
        assert data["keys"] == ["a", "b", "c"]
        assert data["buttonsAreTheSameElements"] is True
        assert data["disabled"] == [False, True, False]

    def test_the_element_is_pinned_to_its_height_and_does_not_stretch_under_nodes_2(
        self, run: dict
    ) -> None:
        """`WidgetDOM` is `flex flex-col *:flex-1`: without an explicit height AND
        `flex: 0 0 auto` the element fills the node's spare height."""
        style = run["structure"]["style"]
        assert style["height"] == "22px"
        assert style["minHeight"] == "22px"
        assert style["flex"] == "0 0 auto"

    def test_the_widget_is_added_at_the_tail_for_the_caller_to_place(self, run: dict) -> None:
        assert run["structure"]["widgetName"] == "demo_row"
        assert run["structure"]["onNodeAtTail"] is True

    def test_the_type_is_not_a_core_alias(self, run: dict) -> None:
        assert run["structure"]["widgetType"] == "eps_button_row"
        assert run["structure"]["widgetType"] not in CORE_WIDGET_TYPES

    def test_the_stylesheet_is_injected_once_and_uses_the_pack_variables(self, run: dict) -> None:
        assert run["styles"]["tags"] == 1  # two rows were built
        css = run["styles"]["css"]
        for variable in ("--comfy-input-bg", "--input-text", "--border-color"):
            assert variable in css
        assert ".eps-btn-row-btn:disabled" in css  # a disabled Delete must LOOK disabled
        # equal-width buttons, however long a label is
        assert "flex: 1 1 0" in css and "min-width: 0" in css
        assert "text-overflow: ellipsis" in css


# ------------------------------------------------------------------------ flags


class TestSerializeAndPanelFlags:
    def test_both_serialize_flags_are_false(self, run: dict) -> None:
        """`options.serialize` gates the API PROMPT (executionUtil.ts),
        `widget.serialize` gates the workflow FILE (LGraphNode.ts) -- NOT
        interchangeable; a past regression shipped phantom inputs with only one."""
        assert run["flags"]["serialize"] is False
        assert run["flags"]["optionSerialize"] is False
        assert run["flags"]["serializeValue"] == "undefined"

    def test_hide_in_panel_so_the_right_hand_panel_does_not_list_it(self, run: dict) -> None:
        assert run["flags"]["hideInPanel"] is True

    def test_compute_layout_size_is_shadowed_to_undefined(self, run: dict) -> None:
        """Nodes 2.0 reads `typeof widget.computeLayoutSize === 'function'` to
        pick a grid row of `auto` (grows) vs `min-content` (keeps its height);
        DOMWidgetImpl defines the method on its prototype, so a DOM widget is an
        expanding row unless the instance shadows it."""
        flags = run["flags"]
        assert flags["ownLayoutShadow"] is True
        assert flags["typeofLayout"] == "undefined"
        assert flags["prototypeHasLayout"] is True

    def test_the_flags_are_set_in_place_even_if_the_frontend_drops_the_option_bag(
        self, run: dict
    ) -> None:
        """A build whose `addDOMWidget` returns a widget with an options object
        that lacks our keys still ends up with both flags set -- on that same
        object, never a replacement (Nodes 2.0 keeps a reference to the original)."""
        assert run["bareOptions"] == {
            "optionSerialize": False,
            "hideInPanel": True,
            "serialize": False,
        }

    def test_the_margin_option_reaches_the_widget(self, run: dict) -> None:
        assert run["flags"]["margin"] == 4


# ------------------------------------------------------------------------ clicks


class TestClicks:
    def test_the_handler_gets_the_real_click_event(self, run: dict) -> None:
        """`canvas.prompt` positions its box off `event.clientX/Y`; a native
        widget callback under Nodes 2.0 hands the handler no event at all."""
        assert run["clicks"]["calls"] == [["a", 321, 123]]

    def test_a_disabled_button_never_fires_and_never_captures(self, run: dict) -> None:
        assert run["clicks"]["calls"] == [["a", 321, 123]]  # b was clicked, ignored
        assert run["clicks"]["capturesAfterOneClick"] == 1

    def test_a_throwing_handler_is_contained_and_the_click_still_captures(
        self, run: dict
    ) -> None:
        """a (1 capture) + b disabled (0) + c throws (1): 2 -- and nothing escaped
        the click into the canvas."""
        assert run["clicks"]["capturesAfterThree"] == 2

    def test_a_reenabled_button_fires_again(self, run: dict) -> None:
        assert run["clicks"]["afterEnable"][-1] == ["b"]

    def test_a_clicked_button_gives_up_focus(self, run: dict) -> None:
        """A focused button would send bare keystrokes (a stray `r` is the
        frontend's Refresh shortcut) to itself instead of the canvas."""
        assert run["clicks"]["blurred"]["a"] == 1
        assert run["clicks"]["blurred"]["b"] == 0  # disabled: returned before anything ran
        assert run["clicks"]["blurred"]["c"] == 1

    def test_a_click_is_one_undo_step(self, run: dict) -> None:
        assert run["clicks"]["capturesAfterOneClick"] == 1

    def test_the_undo_capture_never_throws(self, run: dict) -> None:
        """An older frontend (`checkState`), a missing tracker, a missing
        extensionManager, a tracker that throws: none may break a click."""
        assert run["undoFallbacks"] == {"checkState": True, "neverThrew": True}


# -------------------------------------------------------------------- hide/show


class TestHideAndShow:
    def test_hiding_writes_every_flag_in_place(self, run: dict) -> None:
        """`widget.hidden` (classic), `options.hidden` (Nodes 2.0, IN PLACE) and
        the element's own display -- Nodes 2.0 1.52.7 does not observe either
        flag after the first render, so without the display it would hide in
        classic only."""
        hidden = run["hide"]["hidden"]
        assert hidden["widget"] is True and hidden["option"] is True
        assert hidden["display"] == "none"
        assert hidden["sameOptions"] is True

    def test_showing_undoes_all_of_it(self, run: dict) -> None:
        shown = run["hide"]["shown"]
        assert shown == {"widget": False, "option": False, "display": ""}
        assert run["hide"]["sameOptionsAfter"] is True


class TestFailSoft:
    def test_a_frontend_without_add_dom_widget_returns_null_and_warns(self, run: dict) -> None:
        assert run["noDom"] == {"result": None, "widgets": 0, "warned": 1}


# ---------------------------------------------------------------- source pins


class TestSourcePins:
    """What only reading the source can pin: the event type, and the things the
    probe's fake cannot show."""

    def test_clicks_use_click_not_pointerdown(self, source: str) -> None:
        """Nodes 2.0's `WidgetDOM` stops pointerdown/move/up propagation (so
        they cannot drag the node) but not `click`; a button needs `click` for
        keyboard activation anyway."""
        assert "button.addEventListener('click'" in source
        assert "addEventListener('pointerdown'" not in source
        assert "addEventListener('mousedown'" not in source

    def test_no_canvas_drawing_or_node_mouse_hooks(self, source: str) -> None:
        for forbidden in ("onDrawForeground", "onMouseDown", "onDrawBackground", "ctx."):
            assert forbidden not in source, forbidden

    def test_it_imports_only_the_app_never_a_second_copy_of_a_shared_helper(
        self, source: str
    ) -> None:
        imports = re.findall(r"^import .* from '(.*)'", source, re.M)
        assert imports == ["../../../scripts/app.js"]

    def test_the_two_serialize_flags_and_in_place_options_are_spelled_out(
        self, source: str
    ) -> None:
        assert "serialize: false," in source  # the options bag at creation
        assert "widget.options.serialize = false" in source
        assert "widget.serialize = false" in source
        assert "widget.serializeValue = () => undefined" in source
        assert "hideInPanel: true" in source
        assert "widget.computeLayoutSize = undefined" in source
        assert "element.style.flex = '0 0 auto'" in source
        assert "widget.options.hidden = flag" in source
        assert "widget.options = {" not in source  # never replaced
