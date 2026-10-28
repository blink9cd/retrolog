"""Build the one-file review page from its template, stylesheet, script and data.

``build_page`` replaces three whole marker lines of the template: the stylesheet link
with a ``<style>`` holding the CSS, the script tag with a classic inline ``<script>``
holding the JS, and the Content-Security-Policy line with a policy allowing exactly
those two by their SHA-256 hashes and nothing else. It then fills the template's
``review-data`` block with the data as JSON, every ``<`` escaped, so no title can end
the block or open a comment. The result opens from ``file://`` and sends nothing.

A refusal (``PageError``) names the marker or file at fault; it never holds a title.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence

_STYLE_LINK = '<link rel="stylesheet" href="review.css">'
_SCRIPT_TAG = '<script src="review.js"></script>'
_POLICY_LINE = re.compile(
    r'<meta http-equiv="Content-Security-Policy" content="[^"]*">'
)
# The data block's opening tag. Every page this module builds holds it exactly once,
# so the CLI recognizes a page it wrote earlier by it before replacing that file.
DATA_OPENER = '<script type="application/json" id="review-data">'
_DATA_BLOCK = f"{DATA_OPENER}{{}}</script>"
_DATA_EMPTY = "{}"
# What would end an inline element early, or (for a script) open the HTML parser's
# escaped states, if it appeared in the inlined text.
_STYLE_END = re.compile(r"</style", re.IGNORECASE)
_SCRIPT_END = re.compile(r"</script|<!--", re.IGNORECASE)
# Every directive the policy sets to 'none'. default-src covers the fetch directives
# already; they are spelled out so the policy names each one it denies, and
# form-action and base-uri are not covered by default-src at all.
_NONE_DIRECTIVES = ("connect-src", "img-src", "font-src", "form-action", "base-uri")


class PageError(Exception):
    """The template or an inlined file cannot make a safe page; names no title."""


def _normalize_newlines(text: str) -> str:
    r"""``text`` with each ``\r\n`` and lone ``\r`` as ``\n``.

    The HTML parser does this to its input before a browser hashes an inline element,
    so the hash is taken over the text as the browser will see it.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def csp_source(text: str) -> str:
    """The CSP source allowing exactly ``text`` inline: ``'sha256-<base64>'``."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return f"'sha256-{base64.b64encode(digest).decode('ascii')}'"


def policy(css: str, js: str) -> str:
    """The page's Content-Security-Policy: the inlined style and script, nothing else."""
    directives = [
        "default-src 'none'",
        f"script-src {csp_source(js)}",
        f"style-src {csp_source(css)}",
        *(f"{name} 'none'" for name in _NONE_DIRECTIVES),
    ]
    return "; ".join(directives)


def embed_json(data: Mapping[str, object]) -> str:
    r"""``data`` as compact ASCII JSON with every ``<`` written ``<``.

    ASCII, so a title holding a lone surrogate cannot fail the page's encoding.
    """
    text = json.dumps(data, ensure_ascii=True, separators=(",", ":"))
    return text.replace("<", "\\u003c")


def _only_line(
    lines: Sequence[str], is_marker: Callable[[str], bool], name: str
) -> int:
    """The index of the one line ``is_marker`` accepts; ``PageError`` naming ``name``."""
    found = [index for index, line in enumerate(lines) if is_marker(line.strip())]
    if len(found) != 1:
        raise PageError(f"the template must hold {name} exactly once")
    return found[0]


def _indent(line: str) -> str:
    """The leading whitespace of ``line``, kept on its replacement."""
    return line[: len(line) - len(line.lstrip())]


def _check_inlined(css: str, js: str) -> None:
    """Refuse an inlined file holding text that would break out of its element."""
    if _STYLE_END.search(css):
        raise PageError("review.css: must not hold </style")
    if _SCRIPT_END.search(js):
        raise PageError("the page scripts: must not hold </script or <!--")


def build_page(template: str, css: str, js: str, data: Mapping[str, object]) -> str:
    """The page: ``template`` with the CSS, JS, their policy and ``data`` inlined.

    Raises ``PageError`` when a marker line is missing or repeated, when the policy
    line comes after the stylesheet or script line, or when the CSS or JS holds text
    that would end its element early.
    """
    css, js = _normalize_newlines(css), _normalize_newlines(js)
    _check_inlined(css, js)
    lines = template.splitlines(keepends=True)
    style = _only_line(lines, _STYLE_LINK.__eq__, "the stylesheet link line")
    script = _only_line(lines, _SCRIPT_TAG.__eq__, "the script tag line")
    meta = _only_line(
        lines,
        lambda text: _POLICY_LINE.fullmatch(text) is not None,
        "the Content-Security-Policy line",
    )
    if meta > min(style, script):
        # A meta policy governs only what the parser meets after it, so a style or
        # script above it would run unchecked.
        raise PageError(
            "the template must hold the Content-Security-Policy line before the "
            "stylesheet link line and the script tag line"
        )
    if template.count(_DATA_BLOCK) != 1:
        raise PageError("the template must hold the review-data block exactly once")
    replaced = {
        style: f"<style>{css}</style>",
        script: f"<script>{js}</script>",
        meta: f'<meta http-equiv="Content-Security-Policy" content="{policy(css, js)}">',
    }
    return _assemble(lines, replaced, embed_json(data))


def _assemble(lines: Sequence[str], replaced: Mapping[int, str], data: str) -> str:
    """``lines`` joined, each index in ``replaced`` swapped and the data block filled.

    A swapped line keeps its indentation and its own line ending. Only the template's
    lines are scanned, so marker text inside the inlined CSS or JS is left as it is.
    """
    filled = _DATA_BLOCK.replace(_DATA_EMPTY, data)
    out = []
    for index, line in enumerate(lines):
        if index in replaced:
            ending = line[len(line.rstrip("\r\n")) :]
            line = _indent(line) + replaced[index] + ending
        else:
            line = line.replace(_DATA_BLOCK, filled)
        out.append(line)
    return "".join(out)
