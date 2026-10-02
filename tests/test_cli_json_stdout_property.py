"""Property-based test for --json mode stdout/stderr separation.

Feature: git-repo-health-checker, Property 15: JSON mode emits only valid JSON
on stdout with diagnostics on stderr

Validates: Requirements 10.1, 10.5

For any run invoked with the ``--json`` flag, the entire standard-output
content SHALL parse as a single well-formed JSON document and any diagnostic or
error text SHALL appear only on standard error.

The test drives :func:`git_repo_health_checker.cli.main` in ``--json`` mode with
the ``Orchestrator`` and ``GitHubClient`` it constructs replaced by test doubles
(patched on the ``cli`` module via :func:`unittest.mock.patch` context managers)
so an arbitrary generated :class:`HealthReport` flows through the real JSON
routing without any network access. The patches and stream redirection live
inside the per-example runner (not pytest fixtures, which Hypothesis cannot
reset per generated input). ``stdout`` and ``stderr`` are captured with
:func:`contextlib.redirect_stdout` / :func:`contextlib.redirect_stderr` and
asserted independently.
"""

from __future__ import annotations

import contextlib
import io
import json
from unittest import mock

from hypothesis import given, settings
from hypothesis import strategies as st

from git_repo_health_checker import cli
from git_repo_health_checker.models import (
    Category,
    CategoryResult,
    Grade,
    HealthReport,
    RepoRef,
)

# All five categories; the strategy chooses an arbitrary subset (size 0..5) so
# fully-populated, partially-populated, and empty reports are all exercised.
_ALL_CATEGORIES = list(Category)

# Owner/name segments kept to a small GitHub-legal alphabet so the generated
# identifier always passes ``parse_identifier``. The first character is
# constrained to an alphanumeric so the assembled ``owner/name`` argument never
# begins with ``-`` (which ``argparse`` would mistake for an option flag) — that
# is an incidental CLI-parsing artifact, not the JSON-mode behavior under test.
_segments = st.builds(
    lambda head, tail: head + tail,
    head=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=1),
    tail=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_.", min_size=0, max_size=11),
)

repo_refs = st.builds(RepoRef, owner=_segments, name=_segments)

# Diagnostic strings that may look like human-readable prose or partial JSON.
# Excluding surrogate code points keeps them serializable without loss; the
# text is deliberately allowed to contain braces/brackets so a leak into stdout
# would still be caught by the single-document parse assertion.
_diagnostic_text = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    min_size=0,
    max_size=30,
)


def _category_result(category: Category) -> st.SearchStrategy[CategoryResult]:
    """Build a :class:`CategoryResult` strategy for a specific ``category``.

    Either available with an integer score in ``[0, 100]`` or unavailable with
    ``score=None``, optionally carrying diagnostics.
    """

    diagnostics = st.one_of(
        st.just(()),
        st.lists(_diagnostic_text, min_size=1, max_size=3).map(tuple),
    )

    available = st.builds(
        lambda score, diags: CategoryResult(
            category=category,
            score=score,
            available=True,
            diagnostics=diags,
        ),
        score=st.integers(min_value=0, max_value=100),
        diags=diagnostics,
    )
    unavailable = st.builds(
        lambda diags: CategoryResult(
            category=category,
            score=None,
            available=False,
            diagnostics=diags,
        ),
        diags=diagnostics,
    )
    return st.one_of(available, unavailable)


@st.composite
def health_reports(draw: st.DrawFn) -> HealthReport:
    """Generate an arbitrary :class:`HealthReport`.

    A random subset (size 0..5) of the five categories is evaluated; each gets a
    matching :class:`CategoryResult`. Unavailable categories are recorded in
    ``excluded``. ``weighted_score`` is an int in [0, 100], ``grade`` is sampled
    from :class:`Grade`, report-level diagnostics vary, and ``incomplete`` is a
    boolean so both the complete and JSON-error paths of ``main`` are covered.
    """

    chosen = draw(
        st.lists(
            st.sampled_from(_ALL_CATEGORIES),
            min_size=0,
            max_size=len(_ALL_CATEGORIES),
            unique=True,
        )
    )

    categories: dict[Category, CategoryResult] = {}
    for category in chosen:
        categories[category] = draw(_category_result(category))

    excluded = frozenset(
        category for category, result in categories.items() if not result.available
    )

    return HealthReport(
        repo=draw(repo_refs),
        categories=categories,
        weighted_score=draw(st.integers(min_value=0, max_value=100)),
        grade=draw(st.sampled_from(list(Grade))),
        excluded=excluded,
        diagnostics=draw(
            st.lists(_diagnostic_text, min_size=0, max_size=3).map(tuple)
        ),
        incomplete=draw(st.booleans()),
    )


def _run_json_main(report: HealthReport) -> tuple[int, str, str]:
    """Invoke ``cli.main([..., "--json"])`` with ``report`` injected.

    Replaces the ``GitHubClient`` and ``Orchestrator`` that ``cli.main``
    constructs (via ``mock.patch`` context managers on the ``cli`` module, so
    the patches are applied and torn down for every generated example) so no
    network access occurs and ``Orchestrator().analyze(...)`` returns the
    generated ``report``. Captures stdout and stderr separately.

    Returns the process exit code plus the captured stdout and stderr strings.
    """

    # A GitHubClient stand-in: constructing it and the preflight get_repo
    # existence/access check must not touch the network.
    class _StubClient:
        def __init__(self, token):  # noqa: D401 - trivial stub
            self.token = token

        def get_repo(self, ref):  # noqa: D401 - trivial stub
            return None

    # An Orchestrator stand-in whose analyze() returns the generated report.
    class _StubOrchestrator:
        def __init__(self, *args, **kwargs):
            pass

        def analyze(self, ref, client):  # noqa: D401 - trivial stub
            return report

    out = io.StringIO()
    err = io.StringIO()
    with (
        mock.patch.object(cli, "GitHubClient", _StubClient),
        mock.patch.object(cli, "Orchestrator", _StubOrchestrator),
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(err),
    ):
        exit_code = cli.main([str(report.repo), "--json"])

    return exit_code, out.getvalue(), err.getvalue()


@settings(max_examples=200)
@given(report=health_reports())
def test_stdout_is_a_single_well_formed_json_document(report) -> None:
    """Feature: git-repo-health-checker, Property 15: JSON mode emits only valid
    JSON on stdout with diagnostics on stderr.

    Validates: Requirements 10.1, 10.5

    The entire stdout content SHALL parse as a single well-formed JSON document
    (``json.loads`` over the whole stream succeeds and yields a JSON object).
    """

    _exit_code, stdout, _stderr = _run_json_main(report)

    # The whole stream must parse as ONE document. json.loads rejects trailing
    # content after a single value, so this also proves nothing extra (e.g. a
    # stray diagnostic line) was appended to stdout.
    parsed = json.loads(stdout)
    assert isinstance(parsed, dict)


@settings(max_examples=200)
@given(report=health_reports())
def test_stdout_matches_the_rendered_json_report(report) -> None:
    """Feature: git-repo-health-checker, Property 15: JSON mode emits only valid
    JSON on stdout with diagnostics on stderr.

    Validates: Requirements 10.1, 10.5

    The parsed stdout SHALL equal the JSON renderer's document for the same
    report, confirming stdout carries exactly the JSON report and nothing else.
    """

    _exit_code, stdout, _stderr = _run_json_main(report)

    parsed = json.loads(stdout)
    assert parsed == json.loads(cli.render_json(report))


@settings(max_examples=200)
@given(report=health_reports())
def test_incomplete_report_diagnostic_only_on_stderr(report) -> None:
    """Feature: git-repo-health-checker, Property 15: JSON mode emits only valid
    JSON on stdout with diagnostics on stderr.

    Validates: Requirements 10.1, 10.5

    When the report is incomplete, ``main`` SHALL return exit code 8 and write a
    human-readable diagnostic to stderr while stdout stays a single well-formed
    JSON document carrying the ``error`` field. When complete, exit code is 0
    and no diagnostic is written to stderr.
    """

    exit_code, stdout, stderr = _run_json_main(report)

    # stdout is always pure JSON regardless of completeness.
    parsed = json.loads(stdout)

    if report.incomplete:
        # Exit-code-8 mapping (Req 10.4) and the error surfaces in the JSON.
        assert exit_code == 8
        assert "error" in parsed
        # A diagnostic is routed to stderr (Req 10.5), never to stdout.
        assert stderr.strip() != ""
    else:
        assert exit_code == 0
        assert "error" not in parsed
        assert stderr == ""
