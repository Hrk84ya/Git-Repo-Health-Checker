"""CI presence analyzer (Req 4).

This module holds the pure scoring logic for the continuous-integration
presence category. The scoring function is a deterministic function of an
already-fetched listing of repository file paths, kept free of network effects
so it can be exercised in isolation with property-based tests (any recognized
CI marker yields a positive score; no marker yields ``0``). The analyzer wiring
that fetches the file tree through the client is added in a later subtask.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from git_repo_health_checker.errors import ApiUnreachableError
from git_repo_health_checker.models import Category, CategoryResult, RepoRef

if TYPE_CHECKING:  # pragma: no cover - typing only
    from git_repo_health_checker.github_client import GitHubClient

# Score assigned whenever one or more recognized CI configurations are detected
# (Req 4.2). A single fixed positive value in ``[1, 100]`` is sufficient:
# recognized CI presence is a binary signal, so any match yields this score and
# the absence of any match yields ``0`` (Req 4.3).
CI_PRESENT_SCORE = 100

# Exact file paths that unambiguously identify a CI configuration.
_EXACT_CI_PATHS: frozenset[str] = frozenset(
    {
        ".gitlab-ci.yml",
        ".circleci/config.yml",
        ".travis.yml",
        "azure-pipelines.yml",
    }
)

# Directory prefix under which GitHub Actions workflow definitions live. Any
# ``.yml``/``.yaml`` file inside this directory is a recognized workflow.
_GITHUB_WORKFLOWS_PREFIX = ".github/workflows/"

# Recognized workflow file extensions within the GitHub Actions directory.
_WORKFLOW_SUFFIXES: tuple[str, ...] = (".yml", ".yaml")


def _normalize(path: str) -> str:
    """Normalize a repository path for marker matching.

    Strips surrounding whitespace and a single leading ``./`` or ``/`` so that
    equivalent listings (``.travis.yml`` vs ``./.travis.yml``) match the same
    markers. Backslash separators are converted to forward slashes.
    """

    normalized = path.strip().replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    elif normalized.startswith("/"):
        normalized = normalized[1:]
    return normalized


def _is_ci_path(path: str) -> bool:
    """Return whether ``path`` matches a recognized CI marker.

    Matches either an exact known configuration file or a ``.yml``/``.yaml``
    workflow definition under ``.github/workflows/``.
    """

    normalized = _normalize(path)
    if normalized in _EXACT_CI_PATHS:
        return True
    if normalized.startswith(_GITHUB_WORKFLOWS_PREFIX):
        remainder = normalized[len(_GITHUB_WORKFLOWS_PREFIX) :]
        # Require an actual workflow file (non-empty name) with a recognized
        # extension; a bare ``.github/workflows/`` directory entry is not a
        # workflow definition on its own.
        if remainder and remainder.endswith(_WORKFLOW_SUFFIXES):
            return True
    return False


def score_ci(paths: list[str]) -> int:
    """Score CI presence as an integer in ``[0, 100]`` (Req 4).

    Matches each entry in ``paths`` against the known CI markers:

    - GitHub Actions workflows: ``.github/workflows/*.yml`` (and ``*.yaml``)
    - GitLab CI: ``.gitlab-ci.yml``
    - CircleCI: ``.circleci/config.yml``
    - Travis CI: ``.travis.yml``
    - Azure Pipelines: ``azure-pipelines.yml``

    Any match yields a positive score reflecting detected configuration
    (Req 4.1, 4.2); no recognized CI configuration yields ``0`` (Req 4.3).
    """

    for path in paths:
        if _is_ci_path(path):
            return CI_PRESENT_SCORE
    return 0


# Diagnostic recorded when the repository file tree cannot be retrieved, so the
# CI presence category is reported as unavailable (excluded from weighting)
# without aborting the run (Req 4.4).
UNAVAILABLE_TREE_DIAGNOSTIC = "CI presence could not be evaluated: repository contents unavailable"


class CiAnalyzer:
    """Analyze CI presence by fetching the file tree and applying ``score_ci``.

    Implements the ``CategoryAnalyzer`` protocol (see
    :mod:`git_repo_health_checker.analyzers.base`).
    """

    category: Category = Category.CI

    def analyze(self, ref: RepoRef, client: "GitHubClient") -> CategoryResult:
        """Fetch the file tree for ``ref`` and score CI presence (Req 4.1, 4.4).

        The repository file paths are retrieved through the client and scored
        with the pure :func:`score_ci` (any recognized CI marker → positive
        score; none → ``0``). If the tree cannot be retrieved — surfaced as an
        :class:`ApiUnreachableError` retrieval failure — the category is
        withheld: an unavailable result (``available=False``, ``score=None``)
        is returned with a diagnostic so the score is excluded from weighting
        without aborting the rest of the run (Req 4.4). Whole-run abort errors
        (authentication, rate-limit, private access, repo-not-found) are not
        caught here and propagate to abort the run.
        """

        try:
            paths = client.list_tree(ref)
        except ApiUnreachableError:
            return CategoryResult(
                category=Category.CI,
                score=None,
                available=False,
                diagnostics=(UNAVAILABLE_TREE_DIAGNOSTIC,),
            )

        return CategoryResult(
            category=Category.CI,
            score=score_ci(paths),
            available=True,
        )
