"""Unit tests for CiAnalyzer error and edge paths.

Covers the CI presence analyzer in
:mod:`git_repo_health_checker.analyzers.ci`:

- **No recognized CI configuration (Req 4.3):** a file tree containing no known
  CI markers scores ``0``. Verified both via the pure :func:`score_ci` with
  non-CI paths and via :meth:`CiAnalyzer.analyze` with a stub client returning a
  non-CI tree (``available=True``, ``score=0``).
- **Tree retrieval failure (Req 4.4):** a retrieval failure surfaced as
  :class:`ApiUnreachableError` is converted into an unavailable result
  (``available=False``, ``score=None``) carrying the unavailable diagnostic,
  without aborting the run (no exception escapes ``analyze``).

These are example-based tests using a simple stub client exposing only the
``list_tree`` method the analyzer relies on; they do not depend on the real
``GitHubClient``.
"""

from __future__ import annotations

from git_repo_health_checker.analyzers.ci import (
    UNAVAILABLE_TREE_DIAGNOSTIC,
    CiAnalyzer,
    score_ci,
)
from git_repo_health_checker.errors import ApiUnreachableError
from git_repo_health_checker.models import Category, RepoRef


class _StubClient:
    """Minimal stand-in for ``GitHubClient`` exposing only ``list_tree``.

    ``list_tree`` returns ``paths`` unless ``error`` is set, in which case it
    raises that exception, letting tests drive the analyzer's success and
    failure branches without any network access.
    """

    def __init__(
        self,
        paths: list[str] | None = None,
        error: Exception | None = None,
    ):
        self._paths = paths if paths is not None else []
        self._error = error

    def list_tree(self, ref: RepoRef) -> list[str]:
        if self._error is not None:
            raise self._error
        return self._paths


REF = RepoRef(owner="octocat", name="hello-world")

# A tree with no recognized CI markers: only ordinary source/doc files.
_NON_CI_PATHS = [
    "README.md",
    "src/main.py",
    "src/utils.py",
    "docs/guide.md",
    "tests/test_main.py",
    "pyproject.toml",
    # Superficially similar but not recognized markers.
    ".github/ISSUE_TEMPLATE.md",
    "config.yml",
    "ci.yml",
]


class TestScoreCiNoRecognizedConfig:
    """The pure ``score_ci`` yields 0 when no recognized CI marker is present (Req 4.3)."""

    def test_non_ci_paths_score_zero(self):
        assert score_ci(_NON_CI_PATHS) == 0

    def test_empty_tree_scores_zero(self):
        assert score_ci([]) == 0

    def test_lookalike_paths_score_zero(self):
        # Files that resemble CI markers but do not match the recognized
        # patterns must not be counted as CI presence.
        lookalikes = [
            ".github/workflows/",  # bare directory entry, no workflow file
            ".github/dependabot.yml",  # not under workflows/
            "circleci/config.yml",  # missing leading dot
            "azure-pipelines.txt",  # wrong extension
            "travis.yml",  # missing leading dot
        ]
        assert score_ci(lookalikes) == 0


class TestAnalyzeNoRecognizedConfig:
    """``CiAnalyzer.analyze`` reports available=True, score=0 for a non-CI tree (Req 4.3)."""

    def test_non_ci_tree_scores_zero_and_available(self):
        result = CiAnalyzer().analyze(REF, _StubClient(paths=_NON_CI_PATHS))

        assert result.category is Category.CI
        assert result.score == 0
        assert result.available is True

    def test_non_ci_tree_records_no_diagnostic(self):
        # A recognized-CI-absent tree is a legitimate 0, not an error case, so
        # no unavailable diagnostic is recorded.
        result = CiAnalyzer().analyze(REF, _StubClient(paths=_NON_CI_PATHS))

        assert result.diagnostics == ()
        assert UNAVAILABLE_TREE_DIAGNOSTIC not in result.diagnostics

    def test_empty_tree_scores_zero_and_available(self):
        result = CiAnalyzer().analyze(REF, _StubClient(paths=[]))

        assert result.score == 0
        assert result.available is True
        assert result.diagnostics == ()


class TestAnalyzeTreeRetrievalFailure:
    """A tree-retrieval failure yields an unavailable result without aborting (Req 4.4)."""

    def test_api_unreachable_does_not_propagate(self):
        # analyze must swallow ApiUnreachableError so the overall run continues.
        client = _StubClient(error=ApiUnreachableError())

        # Should not raise.
        CiAnalyzer().analyze(REF, client)

    def test_api_unreachable_is_unavailable_with_no_score(self):
        client = _StubClient(error=ApiUnreachableError())

        result = CiAnalyzer().analyze(REF, client)

        assert result.category is Category.CI
        # Unavailable -> excluded from weighting: no score, available False.
        assert result.available is False
        assert result.score is None

    def test_api_unreachable_records_unavailable_diagnostic(self):
        client = _StubClient(error=ApiUnreachableError())

        result = CiAnalyzer().analyze(REF, client)

        assert UNAVAILABLE_TREE_DIAGNOSTIC in result.diagnostics
        assert result.diagnostics == (UNAVAILABLE_TREE_DIAGNOSTIC,)


class TestAnalyzeRecognizedConfig:
    """Sanity: a tree with a recognized CI marker yields a positive, available score."""

    def test_github_workflow_scores_positive_and_available(self):
        paths = ["README.md", "src/main.py", ".github/workflows/ci.yml"]

        result = CiAnalyzer().analyze(REF, _StubClient(paths=paths))

        assert result.category is Category.CI
        assert result.available is True
        assert result.score > 0
        assert 0 <= result.score <= 100
        assert result.diagnostics == ()
