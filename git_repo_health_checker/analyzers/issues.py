"""Open issue age analyzer (Req 6).

This module holds the pure scoring logic for the open-issue-age category. Both
the median-age computation and the score mapping are deterministic functions of
already-fetched data (a listing of :class:`IssueRecord` values and a reference
"now" datetime), kept free of network effects so they can be exercised in
isolation with property-based tests (the issue-age score is a clamped
non-increasing function of the median age).

The analyzer wiring that fetches the open issues through the client is added in
a later subtask.

Scoring semantics (Req 6):

- The score is derived from the *median* age in days of all open issues, where
  each issue's age is measured from its creation date to a reference "now"
  (Req 6.2).
- A median age at most :data:`FULL_SCORE_MAX_DAYS` (30) days maps to ``100``
  (Req 6.3).
- A median age at least :data:`ZERO_SCORE_MIN_DAYS` (365) days maps to ``0``
  (Req 6.4).
- Between those bounds the score decreases linearly (non-increasing) with age.
- A repository with no open issues maps to ``100`` (Req 6.5).

The result is always an integer in ``[0, 100]`` (Req 6.1).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from git_repo_health_checker.errors import ApiUnreachableError
from git_repo_health_checker.models import Category, CategoryResult, IssueRecord, RepoRef
from git_repo_health_checker.scoring import clamp

if TYPE_CHECKING:  # pragma: no cover - typing only
    from git_repo_health_checker.github_client import GitHubClient

# Median age (in days) at or below which the open-issue-age score is a full
# ``100`` (Req 6.3).
FULL_SCORE_MAX_DAYS = 30.0

# Median age (in days) at or above which the open-issue-age score is ``0``
# (Req 6.4).
ZERO_SCORE_MIN_DAYS = 365.0

# Score assigned when a repository has no open issues (Req 6.5) or when the
# median age is at most :data:`FULL_SCORE_MAX_DAYS` (Req 6.3).
FULL_SCORE = 100

# Number of seconds in a day, used to convert a timedelta to fractional days.
_SECONDS_PER_DAY = 86_400.0


def _issue_age_days(created_at: datetime, now: datetime) -> float:
    """Return the age in days from ``created_at`` to ``now``.

    Ages are measured as fractional days (a timedelta divided by one day). An
    issue created in the future relative to ``now`` yields a negative age; such
    values are clamped to a non-negative age by the caller via the score
    mapping, which treats any age at or below :data:`FULL_SCORE_MAX_DAYS` as a
    full score.
    """

    return (now - created_at).total_seconds() / _SECONDS_PER_DAY


def median_open_issue_age_days(
    issues: Iterable[IssueRecord], now: datetime
) -> float | None:
    """Compute the median open-issue age in days relative to ``now`` (Req 6.2).

    Pull requests are filtered out — the GitHub issues endpoint also returns
    PRs, which are not true issues (see :class:`IssueRecord.is_pull_request`) —
    so only genuine open issues contribute to the median.

    The median of an even-sized set is the arithmetic mean of the two central
    ages. Returns ``None`` when there are no open issues (after filtering out
    PRs), signalling to :func:`score_issue_age` that a full score applies
    (Req 6.5).
    """

    ages = sorted(
        _issue_age_days(issue.created_at, now)
        for issue in issues
        if not issue.is_pull_request
    )

    count = len(ages)
    if count == 0:
        return None

    mid = count // 2
    if count % 2 == 1:
        return ages[mid]
    return (ages[mid - 1] + ages[mid]) / 2.0


def score_issue_age(median_age_days: float | None) -> int:
    """Map a median open-issue age (in days) to a score in ``[0, 100]`` (Req 6).

    - ``None`` (no open issues) maps to ``100`` (Req 6.5).
    - A median age at most :data:`FULL_SCORE_MAX_DAYS` maps to ``100``
      (Req 6.3).
    - A median age at least :data:`ZERO_SCORE_MIN_DAYS` maps to ``0``
      (Req 6.4).
    - Between those bounds the score decreases linearly with age, so the mapping
      is non-increasing in the median age.

    The result is always an integer in ``[0, 100]`` (Req 6.1).
    """

    if median_age_days is None:
        return FULL_SCORE

    # Clamp the age to the mapping's defined range so the linear interpolation
    # below covers exactly the (30, 365) interval; ages outside the range
    # saturate at the endpoints (Req 6.3, 6.4).
    age = clamp(median_age_days, FULL_SCORE_MAX_DAYS, ZERO_SCORE_MIN_DAYS)

    # Linear, non-increasing interpolation: fraction is 0.0 at the full-score
    # bound and 1.0 at the zero-score bound.
    span = ZERO_SCORE_MIN_DAYS - FULL_SCORE_MAX_DAYS
    fraction = (age - FULL_SCORE_MAX_DAYS) / span
    score = FULL_SCORE * (1.0 - fraction)

    return int(round(clamp(score, 0, 100)))


# Diagnostic recorded when the open issues cannot be retrieved, so the
# open-issue-age category is reported as unavailable (excluded from weighting)
# without aborting the run (Req 6.6).
UNAVAILABLE_ISSUES_DIAGNOSTIC = (
    "Open issue age could not be evaluated: issues unavailable"
)


class IssuesAnalyzer:
    """Analyze open-issue age by fetching open issues and applying scoring.

    Implements the ``CategoryAnalyzer`` protocol (see
    :mod:`git_repo_health_checker.analyzers.base`).
    """

    category: Category = Category.ISSUE_AGE

    def analyze(self, ref: RepoRef, client: "GitHubClient") -> CategoryResult:
        """Fetch open issues for ``ref`` and score open-issue age (Req 6.1, 6.6).

        The open issues are retrieved through the client (which excludes pull
        requests per the client contract; :func:`median_open_issue_age_days`
        also filters out any :class:`IssueRecord` flagged as a pull request).
        The median open-issue age is computed relative to the current UTC
        datetime and mapped to a score with the pure :func:`score_issue_age`
        (no open issues → ``100``; older medians → lower scores). If the issues
        cannot be retrieved — surfaced as an :class:`ApiUnreachableError`
        retrieval failure — the category is withheld: an unavailable result
        (``available=False``, ``score=None``) is returned with a diagnostic so
        the score is excluded from weighting without aborting the rest of the
        run (Req 6.6). Whole-run abort errors (authentication, rate-limit,
        private access, repo-not-found) are not caught here and propagate to
        abort the run.
        """

        try:
            issues = client.list_open_issues(ref)
        except ApiUnreachableError:
            return CategoryResult(
                category=Category.ISSUE_AGE,
                score=None,
                available=False,
                diagnostics=(UNAVAILABLE_ISSUES_DIAGNOSTIC,),
            )

        now = datetime.now(timezone.utc)
        median_age = median_open_issue_age_days(issues, now)

        return CategoryResult(
            category=Category.ISSUE_AGE,
            score=score_issue_age(median_age),
            available=True,
        )
