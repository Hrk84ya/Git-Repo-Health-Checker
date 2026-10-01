"""Configuration for the Git Repo Health Checker.

Defines the configurable per-category weights used to combine the five
Category_Scores into the Weighted_Score, along with the default weighting.
"""

import math
from dataclasses import dataclass

from git_repo_health_checker.errors import ConfigError


@dataclass(frozen=True)
class CategoryWeights:
    """Weights applied to each Category_Score when computing the Weighted_Score.

    The five weights must sum to 1.0 (within a small tolerance); otherwise a
    :class:`~git_repo_health_checker.errors.ConfigError` is raised on
    construction.
    """

    readme: float
    ci: float
    coverage: float
    issue_age: float
    pr_activity: float

    def __post_init__(self) -> None:
        total = (
            self.readme
            + self.ci
            + self.coverage
            + self.issue_age
            + self.pr_activity
        )
        if not math.isclose(total, 1.0, abs_tol=1e-9):
            raise ConfigError(f"weights must sum to 1.0, got {total}")


DEFAULT_WEIGHTS = CategoryWeights(0.2, 0.2, 0.2, 0.2, 0.2)
