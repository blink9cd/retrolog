"""Tests for review_page: inlining the template, embedding the data, and the CSP.

The template, stylesheet and script are the stand-ins in tests/fixtures, never the
real page/ files, so these tests do not change when the front end does.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
import unittest
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_TESTS.parent / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import review_page  # noqa: E402 - after the sys.path inserts above
from review_fixtures import SCRIPT, STYLE, TEMPLATE  # noqa: E402

_TEMPLATE_TEXT = TEMPLATE.read_text(encoding="utf-8")
_CSS = STYLE.read_text(encoding="utf-8")
_JS = SCRIPT.read_text(encoding="utf-8")
_DATA: dict[str, object] = {
    "schema": 1,
    "entries": [{"title": "Placeholder </script><!-- Film"}],
}
_STYLE_LINK = '<link rel="stylesheet" href="review.css">'
_SCRIPT_LINE = '<script src="review.js"></script>'
_DATA_BLOCK = re.compile(
    r'<script type="application/json" id="review-data">(.*?)</script>'
)
_POLICY_LINE = re.compile(
    r'^ *<meta http-equiv="Content-Security-Policy" content="([^"]*)">$', re.M
)


def _source(text: str) -> str:
    """The CSP source allowing exactly ``text`` inline, computed here independently."""
    digest = base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest())
    return f"'sha256-{digest.decode('ascii')}'"


def _page(template: str = _TEMPLATE_TEXT, css: str = _CSS, js: str = _JS) -> str:
    """The page built from the stand-ins, with any of the three replaced."""
    return review_page.build_page(template, css, js, _DATA)


class BuildPageTest(unittest.TestCase):
    def _refusal(
        self, template: str = _TEMPLATE_TEXT, css: str = _CSS, js: str = _JS
    ) -> str:
        """The PageError message for a page built from these three."""
        with self.assertRaises(review_page.PageError) as caught:
            _page(template, css, js)
        return str(caught.exception)

    def test_stylesheet_is_inlined_in_place_of_its_link(self) -> None:
        page = _page()
        self.assertNotIn(_STYLE_LINK, page)
        self.assertIn(f"    <style>{_CSS}</style>\n", page)

    def test_script_is_inlined_in_place_of_its_tag(self) -> None:
        page = _page()
        self.assertNotIn(_SCRIPT_LINE, page)
        self.assertIn(f"    <script>{_JS}</script>\n", page)

    def test_policy_allows_exactly_the_inlined_style_and_script(self) -> None:
        (policy,) = _POLICY_LINE.findall(_page())
        self.assertEqual(
            policy,
            "default-src 'none'; "
            f"script-src {_source(_JS)}; "
            f"style-src {_source(_CSS)}; "
            "connect-src 'none'; img-src 'none'; font-src 'none'; "
            "form-action 'none'; base-uri 'none'",
        )

    def test_policy_hashes_what_the_browser_hashes(self) -> None:
        """A carriage return reaches the browser as a newline, so it is hashed as one."""
        page = _page(css=_CSS.replace("\n", "\r\n"), js=_JS.replace("\n", "\r"))
        self.assertNotIn("\r", page)
        self.assertIn(_source(_CSS), page)
        self.assertIn(_source(_JS), page)

    def test_data_is_embedded_with_every_angle_bracket_escaped(self) -> None:
        (block,) = _DATA_BLOCK.findall(_page())
        self.assertNotIn("<", block)
        self.assertIn("\\u003c", block)
        self.assertEqual(json.loads(block), _DATA)

    def test_every_other_line_is_kept(self) -> None:
        page_lines = _page().splitlines()
        for line in _TEMPLATE_TEXT.splitlines():
            if line.strip() not in (_STYLE_LINK, _SCRIPT_LINE) and (
                "Content-Security-Policy" not in line and "review-data" not in line
            ):
                self.assertIn(line, page_lines)
        # Only the inlined files' own lines are added.
        added = _CSS.count("\n") + _JS.count("\n")
        self.assertEqual(len(page_lines), len(_TEMPLATE_TEXT.splitlines()) + added)

    def test_marker_text_in_the_inlined_files_is_not_replaced(self) -> None:
        page = _page(css=f"/* {_SCRIPT_LINE} */\n", js=f"// {_STYLE_LINK}\n")
        self.assertIn(f"<style>/* {_SCRIPT_LINE} */\n</style>", page)
        self.assertIn(f"<script>// {_STYLE_LINK}\n</script>", page)

    def test_missing_or_repeated_marker_is_refused(self) -> None:
        markers = {
            _STYLE_LINK: "the stylesheet link line",
            _SCRIPT_LINE: "the script tag line",
            '<meta http-equiv="Content-Security-Policy" content="default-src '
            "'none'\">": "the Content-Security-Policy line",
            '<script type="application/json" id="review-data">{}</script>': (
                "the review-data block"
            ),
        }
        for marker, name in markers.items():
            with self.subTest(marker=name):
                expected = f"the template must hold {name} exactly once"
                self.assertEqual(
                    self._refusal(_TEMPLATE_TEXT.replace(marker, "")), expected
                )
                twice = _TEMPLATE_TEXT.replace(marker, f"{marker}\n    {marker}")
                self.assertEqual(self._refusal(twice), expected)

    def test_policy_line_after_the_style_or_script_is_refused(self) -> None:
        """A meta policy governs only what the parser meets after it."""
        expected = (
            "the template must hold the Content-Security-Policy line before the "
            "stylesheet link line and the script tag line"
        )
        lines = _TEMPLATE_TEXT.splitlines(keepends=True)
        (meta,) = [i for i, line in enumerate(lines) if "Content-Security" in line]
        policy = lines.pop(meta)
        for marker in (_STYLE_LINK, _SCRIPT_LINE):
            with self.subTest(after=marker):
                (at,) = [i for i, line in enumerate(lines) if marker in line]
                moved = [*lines[: at + 1], policy, *lines[at + 1 :]]
                self.assertEqual(self._refusal("".join(moved)), expected)

    def test_marker_sharing_a_line_is_not_a_marker_line(self) -> None:
        shared = _TEMPLATE_TEXT.replace(_SCRIPT_LINE, f"<p></p>{_SCRIPT_LINE}")
        self.assertEqual(
            self._refusal(shared),
            "the template must hold the script tag line exactly once",
        )

    def test_a_closing_tag_in_an_inlined_file_is_refused(self) -> None:
        self.assertEqual(
            self._refusal(css="a { } </STYLE >"),
            "review.css: must not hold </style",
        )
        for text in ("</Script>", "<!-- x"):
            with self.subTest(text=text):
                self.assertEqual(
                    self._refusal(js=f"let a = '{text}';"),
                    "the page scripts: must not hold </script or <!--",
                )

    @given(st.text(max_size=60))
    def test_embedded_data_round_trips(self, title: str) -> None:
        data: dict[str, object] = {"entries": [{"title": title}]}
        page = review_page.build_page(_TEMPLATE_TEXT, _CSS, _JS, data)
        (block,) = _DATA_BLOCK.findall(page)
        self.assertNotIn("<", block)
        self.assertEqual(json.loads(block), data)


if __name__ == "__main__":
    unittest.main()
