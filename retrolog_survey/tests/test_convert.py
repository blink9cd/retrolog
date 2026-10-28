"""Tests for survey_convert: converting a .doc copy and comparing what each converter kept."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_TESTS.parent / "src"))
sys.path.insert(0, str(_TESTS / "fixtures"))
import build_fixtures  # noqa: E402
import survey_convert  # noqa: E402
import survey_docx  # noqa: E402

# Generous for a tiny fixture; only a pathological file comes near it.
_MAX_UNCOMPRESSED = 10_000_000
# Seconds a fake or live converter may take.
_TIMEOUT = 60.0


class FakeRunner:
    """Stands in for a converter: records argv and copies a prepared .docx to its output."""

    def __init__(self, produce: Path | None, code: int = 0) -> None:
        self.produce, self.code = produce, code
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str], timeout: float) -> int:
        self.calls.append(list(argv))
        if self.produce is not None:
            if argv[0] == "textutil":
                target = Path(argv[argv.index("-output") + 1])
            else:
                target = Path(argv[argv.index("--outdir") + 1]) / (
                    Path(argv[-1]).stem + ".docx"
                )
            shutil.copyfile(self.produce, target)
        return self.code


class RaisingRunner:
    """A converter call that raises instead of returning an exit code."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def __call__(self, argv: Sequence[str], timeout: float) -> int:
        raise self.error


class PartialWriter:
    """A converter that writes a partial output, then fails: by exit code or by raising."""

    def __init__(self, code: int = 1, error: Exception | None = None) -> None:
        self.code, self.error = code, error

    def __call__(self, argv: Sequence[str], timeout: float) -> int:
        Path(argv[argv.index("-output") + 1]).write_bytes(b"half a docx")
        if self.error is not None:
            raise self.error
        return self.code


class _TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()


class LocateSofficeTest(_TempDirCase):
    def test_which_wins(self) -> None:
        found = survey_convert.locate_soffice(
            which=lambda name: "/opt/lo/" + name, candidates=()
        )
        self.assertEqual(found, Path("/opt/lo/soffice"))

    def test_first_existing_candidate(self) -> None:
        present = self.root / "present" / "soffice"
        present.parent.mkdir()
        present.write_bytes(b"")
        found = survey_convert.locate_soffice(
            which=lambda name: None,
            candidates=(self.root / "missing" / "soffice", present),
        )
        self.assertEqual(found, present)

    def test_directory_candidate_is_not_a_program(self) -> None:
        found = survey_convert.locate_soffice(
            which=lambda name: None, candidates=(self.root,)
        )
        self.assertIsNone(found)

    def test_nothing_found(self) -> None:
        found = survey_convert.locate_soffice(
            which=lambda name: None, candidates=(self.root / "missing",)
        )
        self.assertIsNone(found)

    def test_default_candidates_are_app_bundles(self) -> None:
        defaults = survey_convert.default_soffice_candidates()
        self.assertEqual(len(defaults), 2)
        for path in defaults:
            self.assertEqual(
                path.parts[-4:], ("LibreOffice.app", "Contents", "MacOS", "soffice")
            )
        self.assertEqual(defaults[1].parents[3], Path.home() / "Applications")

    def test_default_candidates_used_when_none(self) -> None:
        present = self.root / "soffice"
        present.write_bytes(b"")
        with mock.patch.object(
            survey_convert,
            "default_soffice_candidates",
            return_value=(self.root / "missing", present),
        ):
            found = survey_convert.locate_soffice(which=lambda name: None)
        self.assertEqual(found, present)


class FeaturesOfTest(_TempDirCase):
    def test_fixture_features(self) -> None:
        path = self.root / "rated.docx"
        build_fixtures.make_rated_docx(path)
        features = survey_convert.features_of(
            survey_docx.read_docx(path, max_uncompressed=_MAX_UNCOMPRESSED)
        )
        for expected in (
            "sym:Wingdings/F043",
            "color:00B050",
            "color:0070C0",
            "hl:yellow",
            "shd:FF0000",
            "image:image/png",
            "font:Wingdings",
        ):
            self.assertIn(expected, features)

    def test_run_style(self) -> None:
        run = survey_docx.RunInfo("x", None, "Rated", None, None, None, None, 0)
        info = survey_docx.DocxInfo(
            (survey_docx.ParagraphInfo(1, None, (run,)),), (), ()
        )
        self.assertEqual(survey_convert.features_of(info), frozenset({"style:Rated"}))

    def test_empty(self) -> None:
        self.assertEqual(
            survey_convert.features_of(survey_docx.DocxInfo((), (), ())), frozenset()
        )


class CompareConvertersTest(_TempDirCase):
    def setUp(self) -> None:
        super().setUp()
        self.prepared = self.root / "prepared.docx"
        build_fixtures.make_rated_docx(self.prepared)
        self.expected = survey_convert.features_of(
            survey_docx.read_docx(self.prepared, max_uncompressed=_MAX_UNCOMPRESSED)
        )
        self.src = self.root / "source" / "old list.doc"
        self.src.parent.mkdir()
        self.src.write_bytes(b"pretend Word 97 bytes")
        self.workdir = self.root / "work"
        self.workdir.mkdir()

    def _compare(
        self, run: survey_convert.Runner, soffice: Path | None = None
    ) -> tuple[survey_convert.ConversionResult, ...]:
        return survey_convert.compare_converters(
            self.src,
            self.workdir,
            run=run,
            soffice=soffice,
            max_uncompressed=_MAX_UNCOMPRESSED,
            timeout=_TIMEOUT,
        )

    def test_soffice_absent(self) -> None:
        runner = FakeRunner(self.prepared)
        textutil, libreoffice = self._compare(runner)
        self.assertEqual(
            textutil,
            survey_convert.ConversionResult("textutil", "ok", "exit 0", self.expected),
        )
        self.assertEqual(
            libreoffice,
            survey_convert.ConversionResult("libreoffice", "absent", "", frozenset()),
        )
        self.assertEqual(len(runner.calls), 1)

    def test_textutil_argv(self) -> None:
        runner = FakeRunner(self.prepared)
        self._compare(runner)
        self.assertEqual(
            runner.calls[0],
            [
                "textutil",
                "-convert",
                "docx",
                "-output",
                str(self.workdir / "textutil" / "old list.docx"),
                str(self.workdir / "in" / self.src.name),
            ],
        )

    def test_both_ok_with_soffice(self) -> None:
        runner = FakeRunner(self.prepared)
        soffice = Path("/opt/lo/soffice")
        results = self._compare(runner, soffice=soffice)
        self.assertEqual([r.status for r in results], ["ok", "ok"])
        self.assertEqual(results[1].converter, "libreoffice")
        self.assertEqual(results[1].features, self.expected)
        argv = runner.calls[1]
        self.assertEqual(argv[0], str(soffice))
        self.assertIn("--headless", argv)
        profile = (self.workdir / "lo_profile").resolve().as_uri()
        self.assertEqual(
            argv,
            [
                str(soffice),
                "--headless",
                f"-env:UserInstallation={profile}",
                "--convert-to",
                "docx",
                "--outdir",
                str(self.workdir / "libreoffice"),
                str(self.workdir / "in" / self.src.name),
            ],
        )

    def test_nonzero_exit_fails(self) -> None:
        results = self._compare(FakeRunner(self.prepared, code=1), Path("/opt/soffice"))
        for result in results:
            self.assertEqual((result.status, result.detail), ("failed", "exit 1"))
            self.assertEqual(result.features, frozenset())

    def test_missing_output_fails(self) -> None:
        textutil, _ = self._compare(FakeRunner(None))
        self.assertEqual((textutil.status, textutil.detail), ("failed", "no output"))

    def test_stale_output_is_not_read_as_success(self) -> None:
        # A reused workdir holds an earlier run's outputs; a converter that writes
        # nothing (soffice exits 0 even when it cannot load its input) must not pass.
        self._compare(FakeRunner(self.prepared), Path("/opt/soffice"))
        results = self._compare(FakeRunner(None), Path("/opt/soffice"))
        for result in results:
            self.assertEqual((result.status, result.detail), ("failed", "no output"))

    def test_partial_output_removed_after_failed_exit(self) -> None:
        textutil, _ = self._compare(PartialWriter(code=1))
        self.assertEqual(textutil.detail, "exit 1")
        self.assertEqual(list((self.workdir / "textutil").iterdir()), [])

    def test_partial_output_removed_after_timeout(self) -> None:
        error = subprocess.TimeoutExpired(["textutil"], _TIMEOUT)
        textutil, _ = self._compare(PartialWriter(error=error))
        self.assertEqual(textutil.detail, "TimeoutExpired")
        self.assertEqual(list((self.workdir / "textutil").iterdir()), [])

    def test_unreadable_output_removed(self) -> None:
        garbage = self.root / "garbage.docx"
        garbage.write_bytes(b"not a zip")
        self._compare(FakeRunner(garbage))
        self.assertEqual(list((self.workdir / "textutil").iterdir()), [])

    def test_timeout_fails_with_class_name(self) -> None:
        runner = RaisingRunner(subprocess.TimeoutExpired(["textutil"], _TIMEOUT))
        results = self._compare(runner, Path("/opt/soffice"))
        for result in results:
            self.assertEqual(
                (result.status, result.detail), ("failed", "TimeoutExpired")
            )

    def test_os_error_fails_with_class_name(self) -> None:
        textutil, _ = self._compare(RaisingRunner(FileNotFoundError("textutil")))
        self.assertEqual(
            (textutil.status, textutil.detail), ("failed", "FileNotFoundError")
        )

    def test_unreadable_output_fails_as_bad_docx(self) -> None:
        garbage = self.root / "garbage.docx"
        garbage.write_bytes(b"not a zip")
        textutil, _ = self._compare(FakeRunner(garbage))
        self.assertEqual((textutil.status, textutil.detail), ("failed", "BadDocx"))

    def test_source_untouched(self) -> None:
        before = self.src.read_bytes()
        runner = FakeRunner(self.prepared)
        self._compare(runner, Path("/opt/soffice"))
        self.assertEqual(len(runner.calls), 2)
        for argv in runner.calls:
            last = Path(argv[-1])
            self.assertNotEqual(last, self.src)
            self.assertTrue(last.is_relative_to(self.workdir))
        self.assertEqual(self.src.read_bytes(), before)
        self.assertEqual(
            sorted(p.name for p in self.src.parent.iterdir()), [self.src.name]
        )

    def test_detail_never_holds_a_path(self) -> None:
        results = self._compare(FakeRunner(None, code=3), Path("/opt/soffice"))
        for result in results:
            self.assertNotIn("/", result.detail)


# What survived a docx -> textutil .doc -> textutil .docx round trip of the fixture on
# macOS, measured when this test was written. See the test docstring for what was lost.
_TEXTUTIL_ROUND_TRIP = frozenset(
    {"color:0070C0", "color:00B050", "font:Times", "font:Wingdings"}
)


@unittest.skipUnless(
    sys.platform == "darwin" and shutil.which("textutil"), "textutil is macOS-only"
)
class TextutilLiveTest(_TempDirCase):
    def test_round_trip_fidelity(self) -> None:
        """Measured fidelity of textutil's .doc -> .docx conversion of the fixture.

        The synthetic .doc is itself written by textutil, so this measures the whole
        round trip (.docx -> .doc -> .docx), not textutil's reading of a .doc saved by
        Word. Recorded when written, on macOS:

        - Kept: ``color:0070C0`` and ``color:00B050`` (direct run colors), and
          ``font:Wingdings`` from the run whose font is Wingdings.
        - Added: ``font:Times``, which textutil writes on every run it emits.
        - Lost: ``sym:Wingdings/F043`` (the ``w:sym`` thumb becomes an empty run that
          keeps only its color), ``hl:yellow``, ``shd:FF0000`` and ``image:image/png``.

        An exact comparison, so a macOS update that changes this fidelity fails here.
        """
        docx_path = self.root / "rated.docx"
        build_fixtures.make_rated_docx(docx_path)
        doc_path = self.root / "source" / "rated.doc"
        doc_path.parent.mkdir()
        code = survey_convert.real_run(
            ["textutil", "-convert", "doc", "-output", str(doc_path), str(docx_path)],
            _TIMEOUT,
        )
        self.assertEqual(code, 0)
        source_listing = sorted(doc_path.parent.iterdir())
        workdir = self.root / "work"
        workdir.mkdir()
        textutil, libreoffice = survey_convert.compare_converters(
            doc_path,
            workdir,
            run=survey_convert.real_run,
            soffice=None,
            max_uncompressed=_MAX_UNCOMPRESSED,
            timeout=_TIMEOUT,
        )
        self.assertEqual((textutil.status, textutil.detail), ("ok", "exit 0"))
        self.assertEqual(libreoffice.status, "absent")
        self.assertEqual(textutil.features, _TEXTUTIL_ROUND_TRIP)
        # The converter ran on a copy: nothing was written beside the source.
        self.assertEqual(source_listing, [doc_path])
        self.assertEqual(sorted(doc_path.parent.iterdir()), [doc_path])


if __name__ == "__main__":
    unittest.main()
