"""Render a survey report dictionary as Markdown for a person to read.

The input is the dictionary `survey_report.build_report` returns. Every count table in
a group (a mapping of names to numbers) is shown as a two-column table, in the order
the report holds them, so a table added to the report appears here without a change.
The remaining sections (images, conversions, samples and errors) are lists.

The output quotes real text from the surveyed files: it is as private as they are.
That text, and every name taken from a file (paths, fonts, styles, error reasons), is
untrusted, so none of it is written raw: `_escape` backslash-escapes the characters
that could break the layout (`MARKUP`), `_code` wraps a path in
a code span whose fence is longer than any backtick run inside it, and both replace a
line break with a visible mark so one value always stays on one line.

`counted` is public because probe's report counts the same way and imports it, and
`LINE_BREAK`, `BREAK_MARK`, and `MARKUP` because parse's Markdown cells escape by the
same rules, so each rule lives here once.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

# Group keys rendered as their own sections, not as count tables.
_SECTIONS = ("images", "conversions", "samples", "errors")
_NONE = "None."
_CONVERTERS = ("textutil", "libreoffice")
# Every line break Python's str.splitlines knows, CRLF first so it counts once.
LINE_BREAK = re.compile("\r\n|[\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029]")
BREAK_MARK = "\N{DOWNWARDS ARROW WITH CORNER LEFTWARDS}"
# What could change the document's structure: a backslash (the escape itself), a
# backtick (a code span), angle brackets (HTML), a pipe (the end of a table cell) and
# the "(" of an inline link's "](". Emphasis characters and lone square brackets are
# left alone: they can restyle a word but not break a line, a list or a table, and the
# run annotations ("[#00B050]") stay readable.
MARKUP = re.compile(r"([\\`<>|]|(?<=\])\()")
_BACKTICKS = re.compile("`+")


def counted(count: int, noun: str) -> str:
    """``count`` and ``noun``, the noun plural unless the count is exactly one."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _one_line(text: str) -> str:
    """``text`` with each line break replaced by a visible mark."""
    return LINE_BREAK.sub(BREAK_MARK, text)


def _escape(value: object) -> str:
    """``value`` as plain Markdown text: on one line, with markup characters escaped."""
    return MARKUP.sub(r"\\\1", _one_line(str(value)))


def _code(value: object) -> str:
    """``value`` as an inline code span that no backtick inside it can close early."""
    text = _one_line(str(value))
    longest = max((len(run) for run in _BACKTICKS.findall(text)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _label(key: str) -> str:
    """``Line endings`` for the report key ``line_endings``."""
    return key.replace("_", " ").capitalize()


def _heading(key: str) -> str:
    """``### Line endings`` for the report key ``line_endings``."""
    return f"### {_label(key)}"


def _count_table(key: str, counts: Mapping[str, object]) -> list[str]:
    """A section holding a two-column table, or "None." when it is empty."""
    lines = [_heading(key), ""]
    if not counts:
        return [*lines, _NONE, ""]
    lines += ["| Name | Count |", "| --- | --- |"]
    lines += [f"| {_escape(name)} | {count} |" for name, count in counts.items()]
    return [*lines, ""]


def _list_section(key: str, items: Sequence[str]) -> list[str]:
    """A section holding a bullet list, or "None." when it is empty."""
    body = [f"- {item}" for item in items] or [_NONE]
    return [_heading(key), "", *body, ""]


def _image_item(image: Mapping[str, object]) -> str:
    return (
        f"`{image['sha256_12']}` {_escape(image['content_type'])}, "
        f"{image['byte_size']} bytes, "
        f"{image['count']} reference(s)"
    )


def _converter_text(name: str, result: Mapping[str, object]) -> str:
    """``textutil ok (exit 0), only font:Times`` for one converter's result."""
    text = f"{name} {result['status']}"
    if result["detail"]:
        text += f" ({result['detail']})"
    only = result["only"]
    assert isinstance(only, list)
    if only:
        text += f", only {_escape(', '.join(map(str, only)))}"
    return text


def _conversion_item(conversion: Mapping[str, object]) -> str:
    parts = []
    for name in _CONVERTERS:
        result = conversion[name]
        assert isinstance(result, Mapping)
        parts.append(_converter_text(name, result))
    both = conversion["both"]
    assert isinstance(both, list)
    parts.append(f"both: {_escape(', '.join(map(str, both))) or 'none'}")
    return f"{_code(conversion['file'])}: {'; '.join(parts)}"


def _sample_items(samples: Mapping[str, Sequence[Mapping[str, object]]]) -> list[str]:
    return [
        f"{kind}: {_code(sample['file'])} line {sample['line']}: {_escape(sample['text'])}"
        for kind, taken in samples.items()
        for sample in taken
    ]


def _group_lines(group: Mapping[str, object]) -> list[str]:
    """One group's Markdown: its count tables, then its list sections."""
    lines = [f"## Group {_code(group['group'])}", ""]
    for key, value in group.items():
        if isinstance(value, Mapping) and key not in _SECTIONS:
            lines += _count_table(key, value)
        elif isinstance(value, int):
            lines += [f"{_label(key)}: {value}", ""]
    images, conversions, samples, errors = (group[key] for key in _SECTIONS)
    assert isinstance(images, list) and isinstance(conversions, list)
    assert isinstance(samples, Mapping) and isinstance(errors, list)
    lines += _list_section("images", [_image_item(image) for image in images])
    lines += _list_section("conversions", [_conversion_item(c) for c in conversions])
    lines += _list_section("samples", _sample_items(samples))
    lines += _list_section(
        "errors",
        [f"{_code(error['file'])}: {_escape(error['error'])}" for error in errors],
    )
    return lines


def render_markdown(report: Mapping[str, object]) -> str:
    """The whole report as Markdown, ending in a newline."""
    options = report["options"]
    converters = report["converters"]
    groups = report["groups"]
    assert isinstance(options, Mapping) and isinstance(converters, Mapping)
    assert isinstance(groups, list)
    available = ", ".join(f"{name} {state}" for name, state in converters.items())
    lines = [
        "# retrolog_survey report",
        "",
        f"Schema version {report['schema_version']}. Group depth "
        f"{options['group_depth']}, {options['samples']} sample(s) per line kind, "
        f"conversion {'on' if options['converters'] else 'off'}.",
        "",
        f"Converters: {available or 'none'}.",
        "",
    ]
    for group in groups:
        lines += _group_lines(group)
    return "\n".join(lines).rstrip("\n") + "\n"
