"""Pure scoring, weight redistribution, and letter-grade logic (Req 8).

This module holds deterministic functions operating on already-fetched data,
kept free of network effects so the logic that most benefits from
property-based testing (score bounds, weight redistribution, grade thresholds)
can be exercised in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass

from git_repo_health_checker.config import CategoryWeights
from git_repo_health_checker.models import Category, CategoryResult, Grade


@dataclass(frozen=True)
class Aggregation:
    """The result of combining per-category results into a single score.

    ``weighted_score`` is an integer in ``[0, 100]`` (Req 8.2), ``grade`` is
    its derived A-F letter grade (Req 8.3), and ``excluded`` is the set of
    categories that were unavailable and therefore left out of the weighted
    sum (Req 8.4).
    """

    weighted_score: int
    grade: Grade
    excluded: frozenset[Category]


def clamp(value: float, low: float, high: float) -> float:
    """Constrain ``value`` to the closed interval ``[low, high]``.

    Values below ``low`` are raised to ``low`` and values above ``high`` are
    lowered to ``high``; values already within range are returned unchanged.
    """

    if value < low:
        return low
    if value > high:
        return high
    return value


def letter_grade(weighted_score: int) -> Grade:
    """Map a weighted score in ``[0, 100]`` to its A-F letter grade (Req 8.3).

    Thresholds: A for 90-100, B for 80-89, C for 70-79, D for 60-69, and F for
    0-59.
    """

    if weighted_score >= 90:
        return Grade.A
    if weighted_score >= 80:
        return Grade.B
    if weighted_score >= 70:
        return Grade.C
    if weighted_score >= 60:
        return Grade.D
    return Grade.F


def redistribute_weights(
    weights: CategoryWeights, available: set[Category]
) -> dict[Category, float]:
    """Drop unavailable categories; rescale the rest to sum to 1.0 (Req 8.4).

    Every :class:`~git_repo_health_checker.models.Category` is present in the
    returned mapping. Available categories receive their original weight
    divided by the sum of available categories' weights, so the applied weights
    sum to 1.0 while preserving their relative proportions. Unavailable
    categories receive ``0.0``.

    When ``available`` is empty (no categories could be evaluated), every
    category maps to ``0.0`` and no division is performed (Req 8.4/9.4/10.4).
    """

    # Map each Category to its configured weight field on CategoryWeights.
    original: dict[Category, float] = {
        Category.README: weights.readme,
        Category.CI: weights.ci,
        Category.COVERAGE: weights.coverage,
        Category.ISSUE_AGE: weights.issue_age,
        Category.PR_ACTIVITY: weights.pr_activity,
    }

    total_available = sum(original[c] for c in available)

    if total_available <= 0:
        # Empty available set (or degenerate zero-sum) -> all-zero weights.
        return {category: 0.0 for category in original}

    return {
        category: (weight / total_available if category in available else 0.0)
        for category, weight in original.items()
    }


def aggregate(
    results: dict[Category, CategoryResult], weights: CategoryWeights
) -> Aggregation:
    """Combine per-category results into a weighted score and grade (Req 8).

    Only categories whose :class:`~git_repo_health_checker.models.CategoryResult`
    is ``available`` contribute to the score. Their weights are redistributed to
    sum to 1.0 (Req 8.4), the weighted sum is computed, clamped to ``[0, 100]``,
    and rounded to the nearest integer (Req 8.1, 8.2). The letter grade is
    derived from that integer (Req 8.3) and the unavailable categories are
    reported in ``excluded``.

    When no categories are available the redistributed weights are all zero, so
    the raw sum is ``0``, yielding a ``weighted_score`` of ``0`` and grade
    ``F`` with every category present in ``results`` reported as excluded.
    """

    available = {c for c, r in results.items() if r.available}
    applied = redistribute_weights(weights, available)
    raw = sum(results[c].score * applied[c] for c in available)
    weighted = round(clamp(raw, 0, 100))
    return Aggregation(
        weighted_score=weighted,
        grade=letter_grade(weighted),
        excluded=frozenset(set(results) - available),
    )
