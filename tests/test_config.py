"""Unit tests for CategoryWeights validation in config.py.

Covers Requirement 8.1: the five per-category weights are configurable values
that must sum to 1.0. Construction with non-summing weights raises ConfigError;
valid combinations (including DEFAULT_WEIGHTS and the boundary tolerance) succeed.
"""

import pytest

from git_repo_health_checker.config import DEFAULT_WEIGHTS, CategoryWeights
from git_repo_health_checker.errors import ConfigError


class TestInvalidWeights:
    """Weights that do not sum to 1.0 must raise ConfigError (Req 8.1)."""

    def test_weights_summing_below_one_raise_config_error(self):
        # 0.1 * 5 = 0.5
        with pytest.raises(ConfigError):
            CategoryWeights(0.1, 0.1, 0.1, 0.1, 0.1)

    def test_weights_summing_above_one_raise_config_error(self):
        # 0.26 * 5 = 1.3
        with pytest.raises(ConfigError):
            CategoryWeights(0.26, 0.26, 0.26, 0.26, 0.26)

    def test_all_zero_weights_raise_config_error(self):
        with pytest.raises(ConfigError):
            CategoryWeights(0.0, 0.0, 0.0, 0.0, 0.0)

    def test_config_error_message_reports_total(self):
        with pytest.raises(ConfigError, match="1.0"):
            CategoryWeights(0.1, 0.1, 0.1, 0.1, 0.1)


class TestValidWeights:
    """Weights that sum to 1.0 must construct successfully (Req 8.1)."""

    def test_default_weights_are_valid(self):
        # DEFAULT_WEIGHTS = 0.2 each -> sums to 1.0.
        assert DEFAULT_WEIGHTS.readme == 0.2
        assert DEFAULT_WEIGHTS.ci == 0.2
        assert DEFAULT_WEIGHTS.coverage == 0.2
        assert DEFAULT_WEIGHTS.issue_age == 0.2
        assert DEFAULT_WEIGHTS.pr_activity == 0.2

    def test_equal_weights_construct_successfully(self):
        weights = CategoryWeights(0.2, 0.2, 0.2, 0.2, 0.2)
        assert weights == DEFAULT_WEIGHTS

    def test_unequal_valid_weights_construct_successfully(self):
        # 0.5 + 0.2 + 0.1 + 0.1 + 0.1 = 1.0
        weights = CategoryWeights(0.5, 0.2, 0.1, 0.1, 0.1)
        assert weights.readme == 0.5
        assert weights.pr_activity == 0.1

    def test_all_weight_on_one_category_is_valid(self):
        # A single category carrying the full weight still sums to 1.0.
        weights = CategoryWeights(1.0, 0.0, 0.0, 0.0, 0.0)
        assert weights.readme == 1.0


class TestBoundaryTolerance:
    """Sums within abs_tol=1e-9 of 1.0 succeed; just outside it fails (Req 8.1)."""

    def test_weights_within_tolerance_succeed(self):
        # Total deviates from 1.0 by 1e-10, which is inside abs_tol=1e-9.
        weights = CategoryWeights(0.2, 0.2, 0.2, 0.2, 0.2 + 1e-10)
        assert weights is not None

    def test_floating_point_thirds_succeed(self):
        # Classic floating-point sum that is not exactly 1.0 but within tolerance.
        third = 1.0 / 3.0
        weights = CategoryWeights(third, third, third, 0.0, 0.0)
        assert weights is not None

    def test_weights_just_outside_tolerance_raise_config_error(self):
        # Total deviates from 1.0 by 1e-8, which is outside abs_tol=1e-9.
        with pytest.raises(ConfigError):
            CategoryWeights(0.2, 0.2, 0.2, 0.2, 0.2 + 1e-8)
