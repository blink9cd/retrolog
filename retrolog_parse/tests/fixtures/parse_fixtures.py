"""Build retrolog_parse test inputs at test time, so no source file is ever committed.

Every value here is invented and deliberately unlike any real rating scale: three keys,
three letters, three dark colors and made-up titles. Nothing here holds real list
content.
"""

from __future__ import annotations

import io
from collections.abc import Mapping, Sequence

import docx
from docx.shared import RGBColor

# One Word run: its text, its font name (None leaves the default) and its RGB color
# ("RRGGBB"; None leaves it automatic).
RunSpec = tuple[str, str | None, str | None]

# The rules every suite shares. A test that needs a variant asks ``rules_bytes`` for a
# text substitution rather than keeping a second copy that would drift.
RULES_TOML = """
schema = 1

[media]
book = ["*read*"]
film = ["*seen*"]

[ratings]
letters = { x = "1", o = "2", k = "3" }
split_separator = "/"
colors = { "112233" = "1", "445566" = "2", "778899" = "3" }
color_tolerance = 40
legend_order = ["3", "2", "1"]

[shapes]
"Wingdings/F041" = ["3"]
"Wingdings/F042" = ["1", "2"]

[lines]
headings = ['^list of ']
sections = ["work", "fun"]
progress = '^(?P<title>.*?)\\s*-\\s*\\d+/\\d+$'

[dates]
two_year_folder = '^(\\d{2})-(\\d{2})$'
one_year_folder = '^(\\d{2})$'
century = 2000
earlier_words = ["first half"]
later_words = ["second half"]
explicit_year = ['#(\\d{2})(?!\\d)']
seasons = { harvest = 10 }

[[terms]]
file = "31-32/seen.txt"
lines = [1, 2]
date = 2031-12-31
expect = "first film - k"

[[overrides]]
file = "33/read.txt"
line = 4
expect = "odd book - q"
action = "rating"
value = "3"
"""


def rules_bytes(replace: Mapping[str, str] | None = None) -> bytes:
    """The fixture rules, with `old -> new` text substitutions for one test's variant."""
    text = RULES_TOML
    for old, new in (replace or {}).items():
        assert old in text, old  # a substitution that matches nothing is a broken test
        text = text.replace(old, new)
    return text.encode("utf-8")


# --- Word documents ---


def docx_bytes(paragraphs: Sequence[Sequence[RunSpec]]) -> bytes:
    """A .docx with one paragraph per item, each built from its runs in order.

    A glyph is written the way Word 97 files store one: a private-use character
    (U+F041, say) in a run whose font is a symbol font, not a ``w:sym`` element.
    """
    document = docx.Document()
    for runs in paragraphs:
        paragraph = document.add_paragraph()
        for text, font, color in runs:
            run = paragraph.add_run(text)
            if font is not None:
                run.font.name = font
            if color is not None:
                run.font.color.rgb = RGBColor.from_string(color)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
