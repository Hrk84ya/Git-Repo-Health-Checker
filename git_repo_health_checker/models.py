"""Core data models, enums, and record types.

This module defines the value types shared across the application:

- :class:`Category` and :class:`Grade` string enums.
- :class:`RepoRef`, the parsed ``owner/name`` reference.
- :class:`CategoryResult` and :class:`HealthReport`, the analysis outputs
  consumed by the renderers.
- :class:`RepoMetadata`, :class:`IssueRecord`, and :class:`PullRequestRecord`,
  the record types returned by :mod:`git_repo_health_checker.github_client`.

All types are immutable (frozen dataclasses / enums) so they can be shared
freely across the analysis pipeline and safely used as dictionary keys where
appropriate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Category(str, Enum):
    """The five evaluated health dimensions."""

    README = "readme"
    CI = "ci"
    COVERAGE = "coverage"
    ISSUE_AGE = "issue_age"
    PR_ACTIVITY = "pr_activity"


class Grade(str, Enum):
    """The A-F letter grade derived from the weighted score."""

    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"


@dataclass(frozen=True)
class RepoRef:
    """A parsed reference to a GitHub repository in ``owner/name`` form."""

    owner: str
    name: str

    def __str__(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass(frozen=True)
class CategoryResult:
    """The outcome of evaluating a single :class:`Category`.

    ``score`` is an integer in ``[0, 100]`` when the category was evaluated
    (``available`` is ``True``) and ``None`` when the category could not be
    evaluated (``available`` is ``False``), in which case it is excluded from
    the weighted score (Req 8.4).
    """

    category: Category
    score: int | None  # 0-100 when available; None when unavailable
    available: bool  # False -> excluded from weighting (Req 8.4)
    diagnostics: tuple[str, ...] = ()  # e.g. invalid-badge, unreadable-README


@dataclass(frozen=True)
class HealthReport:
    """The complete health analysis of a repository.

    Consumed by both the terminal and JSON renderers.
    """

    repo: RepoRef
    categories: dict[Category, CategoryResult]
    weighted_score: int  # 0-100 (Req 8.2)
    grade: Grade  # (Req 8.3)
    excluded: frozenset[Category]
    diagnostics: tuple[str, ...] = ()
    incomplete: bool = False  # True -> JSON error mode / non-zero exit (Req 10.4)


@dataclass(frozen=True)
class RepoMetadata:
    """Repository metadata returned by ``GitHubClient.get_repo``.

    Verifies existence/access and supplies the default branch used for tree
    and content retrieval.
    """

    ref: RepoRef
    private: bool
    default_branch: str


@dataclass(frozen=True)
class IssueRecord:
    """An open issue returned by ``GitHubClient.list_open_issues``.

    ``is_pull_request`` distinguishes true issues from pull requests, which the
    GitHub issues endpoint also returns; PRs are filtered out before scoring
    open issue age (Req 6.2).
    """

    created_at: datetime
    is_pull_request: bool = False


@dataclass(frozen=True)
class PullRequestRecord:
    """A pull request returned by ``GitHubClient.list_pull_requests``."""

    created_at: datetime
