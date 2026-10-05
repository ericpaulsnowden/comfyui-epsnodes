"""ONE table of ratio cases for both readers of a ratio.

EPS Resolution's ``ratio`` is read twice, by two separate implementations that
must never disagree about what a ratio string means (own-your-helpers, this
pack's convention for frontend/backend parity): ``eps_image/nodes_resolution.py``
(``parse_ratio`` / ``conform_to_ratio`` / ``ratio_problem`` / the state
pattern) and ``web/eps_image/resolution.js`` (``parseRatio`` /
``conformToRatio``). ``tests/test_resolution.py`` runs this table through the
first, ``tests/test_resolution_ratio_js.py`` through the second -- so a case
added here is checked on both sides, and a divergence fails one of them.

2026-10-05 (owner: "I should be able to type in a ratio not just use presets"):
decimals, the ``x`` / ``X`` / multiplication-sign / ``/`` separators and spaces joined the
grammar, and the exact ``.5`` ties joined the conform table (the panel now
rounds half to EVEN, like Python).
"""

from __future__ import annotations

#: (text, expected ``(w, h)`` or ``None``).
PARSE_CASES: list[tuple[str, tuple[float, float] | None]] = [
    # every preset the dropdown offers
    ("1:1", (1, 1)),
    ("5:4", (5, 4)),
    ("4:5", (4, 5)),
    ("4:3", (4, 3)),
    ("3:4", (3, 4)),
    ("16:9", (16, 9)),
    ("9:16", (9, 16)),
    # typed whole numbers and decimals
    ("21:9", (21, 9)),
    ("2.39:1", (2.39, 1)),
    ("1.85:1", (1.85, 1)),
    ("0.5:1", (0.5, 1)),
    ("1:2.39", (1, 2.39)),
    ("1.0:1", (1, 1)),
    ("007:4", (7, 4)),
    ("999999:999999", (999999, 999999)),
    ("999999.999999:0.000001", (999999.999999, 0.000001)),
    # spaces and the other separators people write
    ("  4:5  ", (4, 5)),
    ("16x9", (16, 9)),
    ("16X9", (16, 9)),
    ("16\u00d79", (16, 9)),  # the multiplication sign
    ("16/9", (16, 9)),
    ("16 / 9", (16, 9)),
    ("16 x 9", (16, 9)),
    ("2.39 : 1", (2.39, 1)),
    # not a ratio: off, empty, malformed, zero, negative, extra parts
    ("none", None),
    ("", None),
    ("   ", None),
    ("bogus", None),
    ("1:0", None),
    ("0:1", None),
    ("0:0", None),
    ("0.0:1", None),
    ("1:0.000", None),
    ("-1:1", None),
    ("1:-1", None),
    ("1:1:1", None),
    ("1", None),
    ("1:", None),
    (":1", None),
    (":", None),
    ("16:9x", None),
    ("a16:9", None),
    ("1:1 1", None),
    ("1,5:1", None),
    ("1.:1", None),
    (".5:1", None),
    ("1e3:1", None),
    ("0x10:1", None),
    # past the six-digit caps (keeps a typo from becoming a billion pixels)
    ("1234567:1", None),
    ("1:1234567", None),
    ("1.1234567:1", None),
    # non-ASCII digits are not digits to the JavaScript RegExp, so not here either
    ("٣:٤", None),
    ("custom…", None),
]

#: (width, height, ratio, multiple_of, anchor, expected ``(width, height)``).
CONFORM_CASES: list[tuple[int, int, str, int, str, tuple[int, int]]] = [
    # the two new presets, both anchors
    (1024, 1, "4:3", 0, "width", (1024, 768)),
    (1024, 1, "3:4", 0, "width", (1024, 1365)),
    (1, 1024, "4:3", 0, "height", (1365, 1024)),
    (1, 1024, "3:4", 0, "height", (768, 1024)),
    (576, 1, "9:16", 0, "width", (576, 1024)),
    # typed decimals and whole-number ratios
    (1000, 1, "2.39:1", 0, "width", (1000, 418)),
    (1, 418, "2.39:1", 0, "height", (999, 418)),
    (1024, 1, "21:9", 0, "width", (1024, 439)),
    (1920, 1, "1.85:1", 0, "width", (1920, 1038)),
    (1000, 1, "0.5:1", 0, "width", (1000, 2000)),
    (1000, 1, "1:2.39", 0, "width", (1000, 2390)),
    (418, 1, "1:2.39", 0, "width", (418, 999)),
    # another spelling of the same ratio is the same lock
    (1024, 1, "4x3", 0, "width", (1024, 768)),
    (1024, 1, "4 / 3", 0, "width", (1024, 768)),
    # EXACT .5 ties: Python's round() takes the EVEN neighbour, so the panel
    # must too (562.5 -> 562, not Math.round's 563) or the pad shows a number
    # the backend will not compute from the same width
    (1000, 1, "16:9", 0, "width", (1000, 562)),
    (8, 1, "16:9", 0, "width", (8, 4)),
    (40, 1, "16:9", 0, "width", (40, 22)),
    # ...and the same tie when snapping the derived side to multiple_of
    # (800 / 64 == 12.5 -> 12 multiples = 768, not 13 = 832)
    (1000, 1, "5:4", 64, "width", (1000, 768)),
    (160, 1, "1:1", 64, "width", (160, 128)),
    # a non-tie snap, for contrast
    (1000, 1, "4:5", 64, "width", (1000, 1280)),
]

#: What a Universal State may STORE as ``ratio`` -- ``none`` or a canonical
#: ``W:H`` (strict about the form: only ``:``, no spaces). (text, matches).
STATE_PATTERN_CASES: list[tuple[str, bool]] = [
    ("none", True),
    ("1:1", True),
    ("16:9", True),
    ("9:16", True),
    ("4:3", True),
    ("2.39:1", True),
    ("1:2.39", True),
    ("0.5:1", True),
    ("21:9", True),
    ("999999.999999:999999.999999", True),
    # a machine writes the canonical form; the loose spellings are for typing
    ("16x9", False),
    ("16 : 9", False),
    (" 16:9", False),
    ("16:9 ", False),
    ("16:9\n", False),
    # not positive
    ("0:5", False),
    ("5:0", False),
    ("0.0:1", False),
    ("0:0", False),
    # not a ratio
    ("", False),
    ("None", False),
    ("banana", False),
    ("custom…", False),
    ("1:2:3", False),
    ("1e3:1", False),
    ("-1:1", False),
    ("1234567:1", False),
    ("1.1234567:1", False),
]
