"""Pull request activity analyzer (Req 7).

This module holds the pure scoring logic for the pull-request-activity
category. The scoring function is a deterministic function of already-fetched
data (an iterable of PR creation datetimes and a reference "now" datetime),
kept free of network effects so it can be exercised in isolation with
property-based tests (out-of-window PRs are ignored, and the score is
non-decreasing in both volume and recency).

The analyzer wiring that fetches the pull requests through the client is added
in a later subtask.

Scoring semantics (Req 7):

- The evaluated period is the :data:`WINDOW_DAYS` (365) days preceding ``now``.
  Only PRs whose creation date falls within this window contribute to the
  score; PRs created before the window are ignored entirely (Req 7.2).
- A repository with no in-window PRs scores ``0`` (Req 7.4).
- Otherwise the score combines two components, each normalized to ``[0, 1]``
  (Req 7.3):

  - **Recency** — derived from the number of days since the most recent
    in-window PR was created. A PR created at ``now`` yields a recency of
    ``1.0``; one created at the far edge of the window (``WINDOW_DAYS`` days
    ago) yields ``0.0``. The component decays linearly with age, so a more
    recent most-recent-PR never lowers the recency.
  - **Volume** — derived from the count of in-window PRs, saturating at
    :data:`VOLUME_SATURATION_COUNT`. The component is ``count /
    VOLUME_SATURATION_COUNT`` clamped to ``1.0``, so it is non-decreasing in
    the count.

  The two components are combined as a weighted sum
  (:data:`RECENCY_WEIGHT` + :data:`VOLUME_WEIGHT` == ``1.0``) scaled to
  ``[0, 100]``.

The result is always an integer in ``[0, 100]`` (Req 7.1). Because adding an
in-window PR can only increase the count and can only move the most-recent date
later (or leave it unchanged), the score is non-decreasing in both volume and
recency (Req 7.2, 7.3).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from git_repo_health_checker.errors import ApiUnreachableError
from git_repo_health_checker.models import Category, CategoryResult, RepoRef
from git_repo_health_checker.scoring import clamp

if TYPE_CHECKING:  # pragma: no cover - typing only
    from git_repo_health_checker.github_client import GitHubClient

# Length in days of the evaluated period preceding ``now`` (Req 7.2). PRs
# created before this window are excluded from scoring entirely. Mirrors the
# ``ZERO_SCORE_MIN_DAYS`` style of the issues analyzer.
WINDOW_DAYS = 365.0

# Number of in-window PRs at (or above) which the volume component saturates at
# its maximum of ``1.0``. Repositories with at least this many PRs in the year
# receive full credit for volume; the count is otherwise scaled linearly.
VOLUME_SATURATION_COUNT = 20

# Relative weights of the recency and volume components. They sum to ``1.0`` so
# that a maximal recency and volume together yield the full score of ``100``.
RECENCY_WEIGHT = 0.5
VOLUME_WEIGHT = 0.5

# Score assigned when there are no PRs created within the evaluated period
# (Req 7.4).
ZERO_SCORE = 0

# Number of seconds in a day, used to convert a timedelta to fractional days.
_SECONDS_PER_DAY = 86_400.0


def _pr_age_days(created_at: datetime, now: datetime) -> float:
    """Return the age in days from ``created_at`` to ``now``.

    Ages are measured as fractional days (a timedelta divided by one day). A PR
    created in the future relative to ``now`` yields a negative age; the score
    mapping clamps such values so they are treated as maximally recent.
    """

    return (now - created_at).total_seconds() / _SECONDS_PER_DAY


def score_pr_activity(
    pr_dates: Iterable[datetime], now: datetime
) -> int:
    """Score PR activity as an integer in ``[0, 100]`` (Req 7).

    Only PRs whose creation date falls within the :data:`WINDOW_DAYS`-day
    window preceding ``now`` are considered; PRs created before the window
    (age greater than :data:`WINDOW_DAYS` days) are ignored entirely, so they
    never affect the score (Req 7.2).

    When there are no in-window PRs the score is :data:`ZERO_SCORE` (``0``)
    (Req 7.4). Otherwise the score combines (Req 7.3):

    - a **recency** component from the age of the most recent in-window PR,
      ``1.0`` at ``now`` decaying linearly to ``0.0`` at the window edge, and
    - a **volume** component from the count of in-window PRs, ``count /
      VOLUME_SATURATION_COUNT`` clamped to ``1.0``,

    combined as ``RECENCY_WEIGHT * recency + VOLUME_WEIGHT * volume`` and scaled
    to ``[0, 100]``.

    The result is always an integer in ``[0, 100]`` (Req 7.1). Adding an
    in-window PR can only raise the count (volume non-decreasing) and can only
    make the most-recent PR more recent or leave it unchanged (recency
    non-decreasing), so the score never decreases when volume increases or the
    most recent PR becomes more recent (Req 7.2, 7.3).
    """

    # Ages (in days) of only the in-window PRs. An age at or below WINDOW_DAYS
    # is in the window; PRs older than the window are dropped here so they have
    # no effect on either component (Req 7.2).
    in_window_ages = [
        age
        for age in (_pr_age_days(created_at, now) for created_at in pr_dates)
        if age <= WINDOW_DAYS
    ]

    if not in_window_ages:
        return ZERO_SCORE

    # Recency: the most recent in-window PR has the smallest age. Clamp the age
    # to the window so future-dated PRs (negative age) saturate at full recency
    # and the fraction stays in [0, 1]. recency is 1.0 at age 0 and 0.0 at the
    # window edge, decaying linearly (non-decreasing as the PR becomes more
    # recent).
    most_recent_age = clamp(min(in_window_ages), 0.0, WINDOW_DAYS)
    recency = 1.0 - (most_recent_age / WINDOW_DAYS)

    # Volume: count of in-window PRs, scaled and saturating at 1.0. Adding PRs
    # can only increase the count, so volume is non-decreasing in the count.
    volume = clamp(len(in_window_ages) / VOLUME_SATURATION_COUNT, 0.0, 1.0)

    raw = (RECENCY_WEIGHT * recency + VOLUME_WEIGHT * volume) * 100.0
    return int(round(clamp(raw, 0, 100)))


# Diagnostic recorded when the pull requests cannot be retrieved. Unlike the
# CI and issues categories, a PR-retrieval failure does NOT exclude the
# pull-request-activity category from weighting; instead the category stays
# included with a zero score and this status note explaining that PR data was
# unavailable (Req 7.5).
UNAVAILABLE_PRS_DIAGNOSTIC = (
    "Pull request activity could not be evaluated: pull requests unavailable; "
    "applied a score of 0"
)


class PullRequestAnalyzer:
    """Analyze pull-request activity by fetching PRs and applying scoring.

    Implements the ``CategoryAnalyzer`` protocol (see
    :mod:`git_repo_health_checker.analyzers.base`).
    """

    category: Category = Category.PR_ACTIVITY

    def analyze(self, ref: RepoRef, client: "GitHubClient") -> CategoryResult:
        """Fetch pull requests for ``ref`` and score PR activity (Req 7.1, 7.5).

        The pull requests created within the :data:`WINDOW_DAYS`-day window are
        retrieved through the client (``list_pull_requests(ref, since)``) and
        each :class:`~git_repo_health_checker.models.PullRequestRecord`'s
        ``created_at`` is scored with the pure :func:`score_pr_activity`
        (no in-window PRs → ``0``; more recent and more numerous PRs → higher
        scores).

        Unlike the CI and issues analyzers, a PR-retrieval failure — surfaced
        as an :class:`ApiUnreachableError` — does *not* withhold the category.
        The category stays available (included in weighting) with a score of
        ``0`` and a status-note diagnostic, so a missing PR signal is treated
        as no activity rather than an excluded category (Req 7.5). Whole-run
        abort errors (authentication, rate-limit, private access,
        repo-not-found) are not caught here and propagate to abort the run.
        """

        now = datetime.now(timezone.utc)
        since = now - timedelta(days=WINDOW_DAYS)

        try:
            pull_requests = client.list_pull_requests(ref, since)
        except ApiUnreachableError:
            return CategoryResult(
                category=Category.PR_ACTIVITY,
                score=0,
                available=True,
                diagnostics=(UNAVAILABLE_PRS_DIAGNOSTIC,),
            )

        pr_dates = [pr.created_at for pr in pull_requests]

        return CategoryResult(
            category=Category.PR_ACTIVITY,
            score=score_pr_activity(pr_dates, now),
            available=True,
        )
