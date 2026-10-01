"""Analysis orchestration (design.md, "Overview"/"Data Flow").

The :class:`Orchestrator` coordinates the analysis pipeline: it runs each of
the five category analyzers independently, aggregates their
:class:`~git_repo_health_checker.models.CategoryResult`s into a single weighted
score and grade via :func:`git_repo_health_checker.scoring.aggregate`, and
builds the final :class:`~git_repo_health_checker.models.HealthReport`.

Per-category vs whole-run failures (design.md, "Error Handling"):

- A retrieval failure scoped to a single category (README/CI/coverage/issues/
  PRs) must NOT abort the pipeline; the remaining categories still produce a
  report (Req 3.6, 4.4, 6.6, 7.5, 8.4). Each analyzer already catches its own
  :class:`~git_repo_health_checker.errors.ApiUnreachableError` and converts it
  to an unavailable / zero-with-note result, so the orchestrator's per-analyzer
  ``try``/``except`` is a *safety net* that catches only
  :class:`ApiUnreachableError` that unexpectedly escapes an analyzer and
  converts it to an unavailable result for that analyzer's category.
- Whole-run abort errors — :class:`RateLimitExhaustedError`,
  :class:`AuthenticationError`, :class:`PrivateRepoAccessError`, and
  :class:`RepoNotFoundError` — are NOT caught here. They propagate to the CLI,
  which maps them to exit codes (design.md, "Whole-run vs per-category
  failures"). The narrow ``except ApiUnreachableError`` guarantees these abort
  errors are never swallowed.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from git_repo_health_checker.analyzers.base import CategoryAnalyzer
from git_repo_health_checker.analyzers.ci import CiAnalyzer
from git_repo_health_checker.analyzers.coverage import CoverageAnalyzer
from git_repo_health_checker.analyzers.issues import IssuesAnalyzer
from git_repo_health_checker.analyzers.pull_requests import PullRequestAnalyzer
from git_repo_health_checker.analyzers.readme import ReadmeAnalyzer
from git_repo_health_checker.config import DEFAULT_WEIGHTS, CategoryWeights
from git_repo_health_checker.errors import ApiUnreachableError
from git_repo_health_checker.models import (
    Category,
    CategoryResult,
    HealthReport,
    RepoRef,
)
from git_repo_health_checker.scoring import aggregate

if TYPE_CHECKING:  # pragma: no cover - typing only
    from git_repo_health_checker.github_client import GitHubClient


# Diagnostic recorded when an analyzer unexpectedly propagates an
# ``ApiUnreachableError`` (its own internal handling should have converted the
# failure already). The safety-net conversion mirrors the per-category
# "unavailable" handling of the CI/issues analyzers (Req 4.4, 6.6): the category
# is excluded from weighting without aborting the run.
SAFETY_NET_UNAVAILABLE_DIAGNOSTIC = (
    "{category} could not be evaluated: category data unavailable"
)


def _default_analyzers() -> tuple[CategoryAnalyzer, ...]:
    """Return the five analyzers in the pipeline's canonical order.

    The order mirrors the data-flow diagram in design.md (README, CI, coverage,
    issues, PR activity) and the field order of
    :class:`~git_repo_health_checker.config.CategoryWeights`. Analyzers run
    independently, so the order does not affect scores; it only determines the
    order in which categories are evaluated and reported.
    """

    return (
        ReadmeAnalyzer(),
        CiAnalyzer(),
        CoverageAnalyzer(),
        IssuesAnalyzer(),
        PullRequestAnalyzer(),
    )


class Orchestrator:
    """Coordinate the analyzers, aggregation, and report construction.

    Runs each analyzer independently and assembles the results into a single
    :class:`~git_repo_health_checker.models.HealthReport` (design.md,
    "Overview"). The set of analyzers can be injected for testing; by default
    the five production analyzers are used.
    """

    def __init__(
        self,
        weights: CategoryWeights = DEFAULT_WEIGHTS,
        analyzers: Sequence[CategoryAnalyzer] | None = None,
    ) -> None:
        """Create an orchestrator.

        Parameters
        ----------
        weights:
            The per-category weights used by :func:`aggregate` to combine the
            available category scores (Req 8.1). Defaults to
            :data:`~git_repo_health_checker.config.DEFAULT_WEIGHTS`.
        analyzers:
            The analyzers to run. Defaults to the five production analyzers in
            canonical order (README, CI, coverage, issues, PR activity). Inject
            a custom sequence to run a subset or test doubles.
        """

        self._weights = weights
        self._analyzers: tuple[CategoryAnalyzer, ...] = (
            tuple(analyzers) if analyzers is not None else _default_analyzers()
        )

    def analyze(self, ref: RepoRef, client: GitHubClient) -> HealthReport:
        """Run the analyzers for ``ref`` and build its :class:`HealthReport`.

        Each analyzer's ``analyze(ref, client)`` is invoked independently and
        its :class:`~git_repo_health_checker.models.CategoryResult` collected
        into a mapping keyed by ``result.category``. A per-analyzer
        ``ApiUnreachableError`` safety net converts an unexpectedly escaping
        category retrieval failure into an unavailable result for that
        analyzer's category, so a single category failure never aborts the run
        or discards the other categories (Req 3.6, 4.4, 6.6, 7.5, 8.4).
        Whole-run abort errors (rate-limit, authentication, private-access,
        repo-not-found) are not caught and propagate to the CLI.

        The collected results are aggregated into a weighted score, grade, and
        excluded set via :func:`aggregate` (which redistributes weights over the
        available categories — Req 8.4). Report-level diagnostics are gathered
        from every category (prefixed with the category name) together with a
        note for each excluded category. The report is marked ``incomplete``
        when no category could be evaluated (see below).
        """

        results: dict[Category, CategoryResult] = {}
        for analyzer in self._analyzers:
            try:
                result = analyzer.analyze(ref, client)
            except ApiUnreachableError:
                # Safety net: an analyzer's own handling should have caught this
                # already. Convert to an unavailable result for its category so
                # the category is excluded from weighting without aborting the
                # run (Req 4.4-style handling). Abort errors are intentionally
                # not caught here and propagate.
                result = CategoryResult(
                    category=analyzer.category,
                    score=None,
                    available=False,
                    diagnostics=(
                        SAFETY_NET_UNAVAILABLE_DIAGNOSTIC.format(
                            category=analyzer.category.value
                        ),
                    ),
                )
            results[result.category] = result

        aggregation = aggregate(results, self._weights)

        diagnostics = self._collect_diagnostics(results, aggregation.excluded)

        # ``incomplete`` marks a report where a required value could not be
        # formed (Req 10.4). ``aggregate`` always yields a weighted score and
        # grade, so the concrete "required value unavailable" case here is when
        # NO category could be evaluated: either there were no analyzers, or
        # every result is unavailable (equivalently, every evaluated category is
        # excluded). In that case there is no meaningful weighted score to
        # report and the CLI drives JSON error mode + exit 8.
        incomplete = len(results) == 0 or all(
            not r.available for r in results.values()
        )

        return HealthReport(
            repo=ref,
            categories=results,
            weighted_score=aggregation.weighted_score,
            grade=aggregation.grade,
            excluded=aggregation.excluded,
            diagnostics=diagnostics,
            incomplete=incomplete,
        )

    @staticmethod
    def _collect_diagnostics(
        results: dict[Category, CategoryResult],
        excluded: frozenset[Category],
    ) -> tuple[str, ...]:
        """Aggregate per-category diagnostics and excluded-category notes.

        Each category's own diagnostics are surfaced at the report level,
        prefixed with the category name (e.g. ``"readme: ..."``) so the source
        of each note is clear. In addition, every excluded category gets a
        top-level note recording that its weight was redistributed (design.md
        shows notes such as ``"pr_activity excluded; weight redistributed"``),
        which explains the redistribution performed by :func:`aggregate`
        (Req 8.4). Diagnostics are emitted in analyzer/category evaluation
        order.
        """

        diagnostics: list[str] = []
        for category, result in results.items():
            for note in result.diagnostics:
                diagnostics.append(f"{category.value}: {note}")
        for category in results:
            if category in excluded:
                diagnostics.append(
                    f"{category.value} excluded; weight redistributed"
                )
        return tuple(diagnostics)
