"""Test coverage analyzer (Req 5).

This module holds the pure scoring and badge-parsing logic for the test
coverage category. Both are deterministic functions of already-fetched data
(a listing of detected :class:`Test_Heuristic` signals and the README content
that may carry a Codecov/Coveralls coverage badge), kept free of network
effects so they can be exercised in isolation with property-based tests
(badge precedence and fallback, heuristic-guaranteed positive score).

The analyzer wiring that gathers the heuristics from the file tree and the
badge from the README through the client is added in a later subtask.

Scoring semantics (Req 5):

- A valid Coverage_Badge percentage in ``[0, 100]`` sets the score directly and
  takes precedence over any heuristic-derived score (Req 5.3).
- Otherwise, when one or more Test_Heuristics are present, the score is a
  heuristic-derived integer in ``[1, 100]`` (Req 5.2).
- A malformed or out-of-range badge value is ignored; the score falls back to
  the heuristic-derived score and an invalid-badge diagnostic is recorded
  (Req 5.5).
- No Test_Heuristic and no (valid) Coverage_Badge yields ``0`` (Req 5.4).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from git_repo_health_checker.errors import ApiUnreachableError
from git_repo_health_checker.models import Category, CategoryResult, RepoRef
from git_repo_health_checker.scoring import clamp

if TYPE_CHECKING:  # pragma: no cover - typing only
    from git_repo_health_checker.github_client import GitHubClient

# Heuristic-derived score awarded for the first detected Test_Heuristic. Any
# repository with at least one heuristic scores at least this value, keeping the
# heuristic-derived score in ``[1, 100]`` (Req 5.2).
HEURISTIC_BASE_SCORE = 40

# Additional score per detected Test_Heuristic beyond the first. More detected
# signals yield a higher (non-decreasing) heuristic-derived score, saturating at
# 100.
HEURISTIC_PER_SIGNAL_SCORE = 20

# Diagnostic recorded when a Coverage_Badge is present but its value is
# malformed or outside ``[0, 100]``; the badge is ignored and the heuristic
# score is used instead (Req 5.5).
INVALID_BADGE_DIAGNOSTIC = "coverage badge value invalid; used heuristics"


@dataclass(frozen=True)
class CoverageScore:
    """The outcome of the pure :func:`score_coverage` computation.

    ``score`` is an integer in ``[0, 100]``. ``diagnostics`` carries the
    invalid-badge indication when a detected badge was ignored because its value
    was malformed or out of range (Req 5.5); it is empty otherwise. The
    analyzer wiring (a later subtask) turns this into a
    :class:`~git_repo_health_checker.models.CategoryResult`.
    """

    score: int
    diagnostics: tuple[str, ...] = ()


# --- Coverage badge parsing -------------------------------------------------

# A Codecov/Coveralls coverage badge in a README typically renders as a
# shields.io-style badge whose message segment is a percentage, e.g.::
#
#     https://img.shields.io/codecov/c/github/owner/repo
#     https://codecov.io/gh/owner/repo/branch/main/graph/badge.svg
#     [![coverage](https://coveralls.io/repos/github/owner/repo/badge.svg)]
#     ![Coverage](https://img.shields.io/badge/coverage-87%25-brightgreen)
#
# and is commonly accompanied by a rendered percentage such as ``coverage 87%``
# or ``coverage-87%`` in the badge label/message. We look for a percentage that
# appears in the vicinity of a Codecov/Coveralls/"coverage" marker so that
# unrelated percentages elsewhere in the README are not misread as coverage.

# Marker keywords that identify a coverage badge context.
_COVERAGE_MARKERS: tuple[str, ...] = ("codecov", "coveralls", "coverage")

# A percentage token: an integer or decimal immediately followed by a percent
# sign (URL-encoded ``%25`` or a literal ``%``). Group 1 captures the numeric
# portion.
_PERCENT_TOKEN = r"(\d{1,3}(?:\.\d+)?)\s*(?:%25|%)"

# Matches a coverage marker followed, within a short span, by a percentage
# token. The span between the marker and the percentage is limited so that only
# a percentage genuinely associated with the badge is captured.
_BADGE_PERCENT_RE = re.compile(
    r"(?:" + "|".join(_COVERAGE_MARKERS) + r")[^\n]{0,40}?" + _PERCENT_TOKEN,
    re.IGNORECASE,
)

# A coverage marker that is present but carries no parseable percentage nearby.
# Used to distinguish "a badge exists but its value is malformed" (Req 5.5) from
# "no badge at all" (Req 5.4).
_COVERAGE_MARKER_RE = re.compile(
    r"(?:" + "|".join(_COVERAGE_MARKERS) + r")",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BadgeParse:
    """The outcome of parsing a README for a Coverage_Badge percentage.

    - ``present`` is ``True`` when a coverage badge marker was detected in the
      content at all (whether or not a usable percentage was found).
    - ``percent`` is the parsed percentage as a float when a numeric value was
      found next to a marker, or ``None`` when no numeric value was parseable.

    Validity/range checking is deferred to :func:`score_coverage`, which treats
    a ``percent`` outside ``[0, 100]`` as an invalid badge (Req 5.5).
    """

    present: bool
    percent: float | None = None


def parse_coverage_badge(content: str | None) -> BadgeParse:
    """Parse ``content`` for a Codecov/Coveralls coverage badge (Req 5.3, 5.5).

    Returns a :class:`BadgeParse` describing whether a coverage badge marker is
    present and, if so, the percentage value found near it. When no coverage
    marker is present at all, ``present`` is ``False`` and ``percent`` is
    ``None`` (there is no badge — Req 5.4). When a marker is present but no
    percentage can be parsed nearby, ``present`` is ``True`` and ``percent`` is
    ``None`` (a badge exists but its value is malformed — Req 5.5).

    Only the numeric extraction happens here; whether a parsed percentage is in
    range is decided by :func:`score_coverage`.
    """

    if not content:
        return BadgeParse(present=False, percent=None)

    match = _BADGE_PERCENT_RE.search(content)
    if match is not None:
        try:
            percent = float(match.group(1))
        except ValueError:  # pragma: no cover - regex guarantees a number
            percent = None
        return BadgeParse(present=True, percent=percent)

    # No percentage found near a marker; report whether a badge marker exists at
    # all so the caller can distinguish a malformed badge from no badge.
    present = _COVERAGE_MARKER_RE.search(content) is not None
    return BadgeParse(present=present, percent=None)


# --- Coverage scoring -------------------------------------------------------


def _heuristic_score(heuristic_count: int) -> int:
    """Derive a coverage score in ``[1, 100]`` from the number of heuristics.

    Assumes ``heuristic_count >= 1`` (the caller only invokes this when at least
    one Test_Heuristic is present). The score starts at
    :data:`HEURISTIC_BASE_SCORE` for a single heuristic and increases by
    :data:`HEURISTIC_PER_SIGNAL_SCORE` for each additional heuristic, saturating
    at 100. The result is therefore a non-decreasing integer in ``[1, 100]``
    (Req 5.2).
    """

    raw = HEURISTIC_BASE_SCORE + HEURISTIC_PER_SIGNAL_SCORE * (heuristic_count - 1)
    return int(clamp(raw, 1, 100))


def _is_valid_badge(badge: BadgeParse | None) -> bool:
    """Return whether ``badge`` carries a valid percentage in ``[0, 100]``."""

    return (
        badge is not None
        and badge.percent is not None
        and 0 <= badge.percent <= 100
    )


def score_coverage(
    heuristics: Iterable[object] | Sequence[object] | int,
    badge: BadgeParse | None,
) -> CoverageScore:
    """Score test coverage as an integer in ``[0, 100]`` (Req 5).

    Parameters
    ----------
    heuristics:
        The detected Test_Heuristics. May be passed as a collection of detected
        signals (its length is used) or as an integer count directly. A count of
        zero (or an empty collection) means no Test_Heuristic was detected.
    badge:
        The result of :func:`parse_coverage_badge` on the README content, or
        ``None`` when no README/badge information is available.

    Scoring precedence (Req 5.2-5.5):

    1. A **valid** Coverage_Badge — one whose parsed percentage is in
       ``[0, 100]`` — sets the score directly to that percentage (rounded),
       independent of the heuristics (Req 5.3).
    2. Otherwise, when the badge value is **malformed or out of range** (a badge
       marker is present but its percentage is missing or outside ``[0, 100]``),
       the badge is ignored, the score falls back to the heuristic-derived score,
       and an invalid-badge diagnostic is recorded (Req 5.5).
    3. When there is **no badge** but one or more Test_Heuristics are present,
       the score is the heuristic-derived integer in ``[1, 100]`` (Req 5.2).
    4. With **no Test_Heuristic and no badge**, the score is ``0`` (Req 5.4).
    """

    heuristic_count = heuristics if isinstance(heuristics, int) else len(list(heuristics))
    if heuristic_count < 0:
        heuristic_count = 0

    # 1. Valid badge takes precedence over heuristics (Req 5.3).
    if _is_valid_badge(badge):
        assert badge is not None and badge.percent is not None
        return CoverageScore(score=int(round(clamp(badge.percent, 0, 100))))

    # Determine the heuristic-derived fallback score (0 when no heuristics).
    fallback = _heuristic_score(heuristic_count) if heuristic_count >= 1 else 0

    # 2. A present-but-invalid badge is ignored with a diagnostic (Req 5.5).
    if badge is not None and badge.present:
        return CoverageScore(score=fallback, diagnostics=(INVALID_BADGE_DIAGNOSTIC,))

    # 3./4. No badge: heuristic score when present, else 0 (Req 5.2, 5.4).
    return CoverageScore(score=fallback)


# --- Test heuristic detection -----------------------------------------------

# Exact repository file paths that each count as a single Test_Heuristic. These
# are unambiguous test/coverage configuration markers (per the Test_Heuristic
# glossary definition: a pytest config file or a coverage configuration file).
_EXACT_HEURISTIC_PATHS: frozenset[str] = frozenset(
    {
        "pytest.ini",
        "tox.ini",
        ".coveragerc",
        "coverage.ini",
        "codecov.yml",
        ".codecov.yml",
    }
)

# Top-level directory names that indicate a dedicated test location. A path is
# treated as living in a test directory when any of its path segments equals one
# of these names (e.g. ``tests/test_foo.py`` or ``src/pkg/test/thing.py``).
_TEST_DIR_NAMES: frozenset[str] = frozenset({"test", "tests"})

# Regular expressions matching Python test module file names: ``test_*.py`` or
# ``*_test.py`` (matched against the final path segment only).
_TEST_FILE_RE = re.compile(r"(?:^test_.+\.py$)|(?:.+_test\.py$)", re.IGNORECASE)


def _detect_heuristics(paths: Iterable[str]) -> tuple[str, ...]:
    """Detect the distinct Test_Heuristic signals present in ``paths`` (Req 5.2).

    Recognizes the Test_Heuristic markers from the glossary:

    - a test directory (a path segment named ``test`` or ``tests``),
    - a file matching ``test_*.py`` or ``*_test.py``,
    - a ``pytest.ini`` file, and
    - a coverage configuration file (e.g. ``.coveragerc``, ``codecov.yml``).

    Returns a tuple of distinct heuristic identifiers. Each *kind* of signal is
    counted at most once (a repository with many ``test_*.py`` files still
    contributes a single "test file" heuristic) so the heuristic count reflects
    the variety of detected signals rather than the raw file count. The tuple is
    empty when no Test_Heuristic is present.
    """

    found: set[str] = set()

    for raw in paths:
        normalized = raw.strip().replace("\\", "/")
        if normalized.startswith("./"):
            normalized = normalized[2:]
        elif normalized.startswith("/"):
            normalized = normalized[1:]
        normalized = normalized.strip("/")
        if not normalized:
            continue

        segments = normalized.split("/")
        filename = segments[-1]

        if normalized in _EXACT_HEURISTIC_PATHS or filename in _EXACT_HEURISTIC_PATHS:
            found.add(f"config:{filename}")

        if any(segment.lower() in _TEST_DIR_NAMES for segment in segments):
            found.add("test-directory")

        if _TEST_FILE_RE.match(filename):
            found.add("test-file")

    return tuple(sorted(found))


# Diagnostic recorded when the repository file tree cannot be retrieved, so the
# coverage category is reported as unavailable (excluded from weighting) without
# aborting the run, mirroring the CI presence analyzer (Req 4.4-style handling).
UNAVAILABLE_TREE_DIAGNOSTIC = "coverage could not be evaluated: repository contents unavailable"


class CoverageAnalyzer:
    """Analyze test coverage from Test_Heuristics and a README Coverage_Badge.

    Implements the ``CategoryAnalyzer`` protocol (see
    :mod:`git_repo_health_checker.analyzers.base`). Gathers the Test_Heuristics
    from the repository file tree and the Coverage_Badge from the README
    content, then applies the pure :func:`score_coverage`.
    """

    category: Category = Category.COVERAGE

    def analyze(self, ref: RepoRef, client: "GitHubClient") -> CategoryResult:
        """Score test coverage for ``ref`` using the tree and README (Req 5.1, 5.3).

        The repository file paths are retrieved through the client and inspected
        for Test_Heuristics, and the README content is retrieved and parsed for a
        Coverage_Badge. A valid badge percentage takes precedence; otherwise the
        heuristic-derived score is used; an invalid badge is ignored with a
        diagnostic (Req 5.2, 5.3, 5.5) — all decided by :func:`score_coverage`.

        If the file tree cannot be retrieved — surfaced as an
        :class:`ApiUnreachableError` retrieval failure — the category is withheld
        as unavailable (``available=False``, ``score=None``) so it is excluded
        from weighting without aborting the run, mirroring the CI analyzer
        (Req 4.4). A README that cannot be read is treated as carrying no badge:
        coverage is still scored from the heuristics. Whole-run abort errors
        (authentication, rate-limit, private access, repo-not-found) are not
        caught here and propagate to abort the run.
        """

        try:
            paths = client.list_tree(ref)
        except ApiUnreachableError:
            return CategoryResult(
                category=Category.COVERAGE,
                score=None,
                available=False,
                diagnostics=(UNAVAILABLE_TREE_DIAGNOSTIC,),
            )

        heuristics = _detect_heuristics(paths)

        try:
            readme = client.get_readme(ref)
        except ApiUnreachableError:
            # The README is a supplementary signal for the Coverage_Badge; if it
            # cannot be read, fall back to heuristic-only scoring rather than
            # withholding the whole category.
            badge = None
        else:
            badge = parse_coverage_badge(readme)

        result = score_coverage(heuristics, badge)
        return CategoryResult(
            category=Category.COVERAGE,
            score=result.score,
            available=True,
            diagnostics=result.diagnostics,
        )
