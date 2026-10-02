"""Unit tests for the ``cli.main`` entry point behaviors.

Covers two example-based CLI behaviors that are not exercised by the
property-based CLI tests:

- Missing identifier shows usage guidance on stderr and exits non-zero
  (Req 1.2): ``main([])`` prints usage to stderr and returns
  ``MissingIdentifierError.exit_code`` (2), without touching stdout.
- ``--json`` suppresses the terminal report (Req 10.3): in ``--json`` mode the
  colorized terminal renderer is never invoked and stdout carries only the JSON
  document.

The ``--json`` test stubs the network-facing collaborators (``GitHubClient``
and ``Orchestrator``) in the ``cli`` module namespace so no real HTTP occurs,
returning a fixed, complete :class:`HealthReport`, and replaces
``render_terminal`` with a spy to assert it is not called.
"""

from __future__ import annotations

import json

import pytest

from git_repo_health_checker import cli
from git_repo_health_checker.errors import MissingIdentifierError
from git_repo_health_checker.models import (
    Category,
    CategoryResult,
    Grade,
    HealthReport,
    RepoRef,
)


def _complete_report(ref: RepoRef) -> HealthReport:
    """Build a fixed, complete :class:`HealthReport` for CLI wiring tests.

    Every category is available with a numeric score so ``incomplete`` is
    ``False`` and the JSON path renders a plain document (no ``error`` field,
    no ``IncompleteReportError``).
    """

    categories = {
        Category.README: CategoryResult(
            category=Category.README, score=90, available=True
        ),
        Category.CI: CategoryResult(category=Category.CI, score=80, available=True),
        Category.COVERAGE: CategoryResult(
            category=Category.COVERAGE, score=70, available=True
        ),
        Category.ISSUE_AGE: CategoryResult(
            category=Category.ISSUE_AGE, score=60, available=True
        ),
        Category.PR_ACTIVITY: CategoryResult(
            category=Category.PR_ACTIVITY, score=50, available=True
        ),
    }
    return HealthReport(
        repo=ref,
        categories=categories,
        weighted_score=75,
        grade=Grade.C,
        excluded=frozenset(),
        diagnostics=(),
        incomplete=False,
    )


class _StubClient:
    """Stand-in for ``GitHubClient`` that performs no network access."""

    def __init__(self, token):  # noqa: D401 - trivial stub
        self.token = token

    def get_repo(self, ref):  # noqa: D401 - trivial stub
        # The preflight existence/access check in ``cli.main`` calls this; the
        # stub succeeds without touching the network.
        return None


class _StubOrchestrator:
    """Stand-in for ``Orchestrator`` returning a fixed complete report."""

    def analyze(self, ref: RepoRef, client) -> HealthReport:
        return _complete_report(ref)


class TestMissingIdentifier:
    """A missing identifier shows usage and exits non-zero (Req 1.2)."""

    def test_returns_missing_identifier_exit_code(self, capsys):
        exit_code = cli.main([])

        assert exit_code == MissingIdentifierError.exit_code
        assert exit_code == 2

    def test_exit_code_is_non_zero(self, capsys):
        assert cli.main([]) != 0

    def test_usage_written_to_stderr(self, capsys):
        cli.main([])

        captured = capsys.readouterr()
        # Usage guidance goes to stderr and references the program name and the
        # 'owner/name' argument metavar.
        assert "usage" in captured.err.lower()
        assert cli.PROG_NAME in captured.err
        assert "owner/name" in captured.err

    def test_stdout_stays_empty(self, capsys):
        cli.main([])

        captured = capsys.readouterr()
        # Nothing is written to stdout for the missing-identifier error so that
        # stdout remains clean.
        assert captured.out == ""


class TestJsonSuppressesTerminalReport:
    """``--json`` suppresses the terminal report; only JSON on stdout (Req 10.3)."""

    @pytest.fixture()
    def stubbed_pipeline(self, monkeypatch):
        """Patch the network-facing collaborators in the ``cli`` namespace."""

        monkeypatch.setattr(cli, "GitHubClient", _StubClient)
        monkeypatch.setattr(cli, "Orchestrator", _StubOrchestrator)

    def test_render_terminal_not_invoked_in_json_mode(
        self, monkeypatch, stubbed_pipeline
    ):
        calls: list[tuple] = []

        def _spy(*args, **kwargs):
            calls.append((args, kwargs))

        monkeypatch.setattr(cli, "render_terminal", _spy)

        exit_code = cli.main(["--json", "octocat/Hello-World"])

        assert exit_code == 0
        # The terminal renderer must never run in --json mode.
        assert calls == []

    def test_stdout_contains_only_json(
        self, monkeypatch, capsys, stubbed_pipeline
    ):
        # Guard against any accidental terminal output by making render_terminal
        # raise if it is ever called.
        def _fail(*args, **kwargs):  # pragma: no cover - only runs on regression
            raise AssertionError("render_terminal must not be called in --json mode")

        monkeypatch.setattr(cli, "render_terminal", _fail)

        exit_code = cli.main(["--json", "octocat/Hello-World"])
        captured = capsys.readouterr()

        assert exit_code == 0
        # stdout parses as a single JSON document (no colorized report mixed in).
        document = json.loads(captured.out)
        assert document["repository"] == "octocat/Hello-World"
        assert document["weighted_score"] == 75
        assert document["grade"] == "C"
        # No terminal-report artifacts leak onto stdout.
        assert "Category breakdown" not in captured.out
        assert "Weighted score:" not in captured.out
        assert "Grade:" not in captured.out

    def test_terminal_report_rendered_without_json_flag(
        self, monkeypatch, capsys, stubbed_pipeline
    ):
        # Complement: without --json the terminal renderer IS invoked, confirming
        # the suppression above is specific to --json mode and not a stub artifact.
        calls: list[tuple] = []

        def _spy(*args, **kwargs):
            calls.append((args, kwargs))

        monkeypatch.setattr(cli, "render_terminal", _spy)

        exit_code = cli.main(["octocat/Hello-World"])

        assert exit_code == 0
        assert len(calls) == 1
