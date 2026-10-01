"""Command-line entry point: argument parsing, token resolution, and wiring.

This module owns the interface between the shell and the analysis pipeline:
resolving the GitHub token, parsing and validating the ``owner/name``
identifier, invoking the orchestrator, selecting a renderer, and translating
the exception hierarchy into process exit codes.

Only the pure helpers used before any network access live here so far:

- :func:`resolve_token` applies the ``--token`` flag / ``GITHUB_TOKEN``
  precedence rule (Req 2.1-2.4).
- :func:`parse_identifier` validates and splits ``owner/name`` into a
  :class:`~git_repo_health_checker.models.RepoRef` (Req 1.3).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Mapping, Sequence

from git_repo_health_checker.errors import (
    HealthCheckerError,
    IncompleteReportError,
    InvalidIdentifierError,
    MissingIdentifierError,
    RateLimitExhaustedError,
)
from git_repo_health_checker.github_client import GitHubClient
from git_repo_health_checker.models import RepoRef
from git_repo_health_checker.orchestrator import Orchestrator
from git_repo_health_checker.reporting.json_report import render_json
from git_repo_health_checker.reporting.terminal import render_terminal

#: Program name used in ``argparse`` usage/help text and error messages. Matches
#: the ``console_scripts`` entry point declared in ``pyproject.toml``.
PROG_NAME = "repo-health"

# A GitHub-legal path segment: one or more of alphanumerics, ``-``, ``_``, ``.``.
_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


def resolve_token(flag_token: str | None, env: Mapping[str, str]) -> str | None:
    """Resolve the GitHub access token to use.

    The ``--token`` flag takes precedence over the ``GITHUB_TOKEN`` environment
    variable (Req 2.4). If neither yields a truthy value, no token is used and
    the tool operates unauthenticated (Req 2.1-2.3).

    Args:
        flag_token: The value of the ``--token`` flag, or ``None`` when absent.
        env: A mapping of environment variables (e.g. ``os.environ``).

    Returns:
        The resolved token, or ``None`` when no token is available.
    """
    if flag_token:
        return flag_token
    return env.get("GITHUB_TOKEN") or None


def parse_identifier(raw: str) -> RepoRef:
    """Parse and validate an ``owner/name`` repository identifier (Req 1.3).

    A valid identifier is exactly two non-empty GitHub-legal segments separated
    by a single ``/``. GitHub-legal segment characters are alphanumerics, ``-``,
    ``_``, and ``.``. Anything else raises :class:`InvalidIdentifierError`.

    Args:
        raw: The raw identifier string supplied on the command line.

    Returns:
        A :class:`RepoRef` with the parsed ``owner`` and ``name``.

    Raises:
        InvalidIdentifierError: If ``raw`` is not exactly two non-empty
            GitHub-legal segments separated by a single ``/``.
    """
    segments = raw.split("/")
    if len(segments) != 2:
        raise InvalidIdentifierError(
            f"Invalid repository identifier {raw!r}: expected 'owner/name'."
        )

    owner, name = segments
    if not _SEGMENT.match(owner) or not _SEGMENT.match(name):
        raise InvalidIdentifierError(
            f"Invalid repository identifier {raw!r}: 'owner' and 'name' must be "
            "non-empty and contain only letters, digits, '-', '_', or '.'."
        )

    return RepoRef(owner=owner, name=name)


def _build_parser() -> argparse.ArgumentParser:
    """Construct the ``argparse`` parser for the command line.

    The repository identifier is declared as an *optional* positional
    (``nargs="?"``) rather than a required one. This is deliberate: a missing
    identifier must surface as :class:`MissingIdentifierError` (exit code 2 via
    the top-level exception mapping) with usage guidance (Req 1.2), rather than
    ``argparse``'s own ``SystemExit`` for a missing required argument. Making
    the positional optional lets :func:`main` detect its absence and raise the
    mapped error, keeping all error-to-exit-code translation in one place.
    """

    parser = argparse.ArgumentParser(
        prog=PROG_NAME,
        description=(
            "Analyze the health of a public GitHub repository and report a "
            "weighted score and letter grade."
        ),
    )
    parser.add_argument(
        "identifier",
        nargs="?",
        default=None,
        metavar="owner/name",
        help="Repository identifier in 'owner/name' form (e.g. 'octocat/Hello-World').",
    )
    parser.add_argument(
        "--token",
        dest="token",
        default=None,
        metavar="TOKEN",
        help=(
            "GitHub access token. Takes precedence over the GITHUB_TOKEN "
            "environment variable. When omitted, requests are unauthenticated."
        ),
    )
    parser.add_argument(
        "--json",
        dest="json",
        action="store_true",
        help="Emit a JSON report on stdout instead of the human-readable report.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point: parse arguments and run the analysis pipeline.

    Parses the repository identifier plus the ``--token`` and ``--json`` flags,
    resolves the access token (``--token`` over ``GITHUB_TOKEN`` — Req
    2.1-2.4), constructs a :class:`GitHubClient`, runs the
    :class:`Orchestrator`, and renders the resulting report. In ``--json`` mode
    the JSON document is the sole stdout content (Req 10.1, 10.3) and all
    diagnostics go to stderr (Req 10.5); otherwise the human-readable terminal
    report is written to stdout with color determined by ``rich``'s terminal
    detection (Req 9.1).

    A missing identifier raises :class:`MissingIdentifierError` after writing
    usage guidance to stderr (Req 1.2). That error — like every other
    :class:`~git_repo_health_checker.errors.HealthCheckerError` subclass — is
    caught by the single top-level handler, which writes the message to stderr
    (Req 10.5) and returns the error's mapped ``exit_code`` (Req 1.2-1.5, 2.5-2.7,
    8.1, 10.4). :class:`RateLimitExhaustedError` additionally reports its reset
    time (Req 2.5).

    In ``--json`` mode, when the produced report is incomplete — a required
    value could not be produced (Req 10.4) — the JSON renderer's ``error``
    document is written to stdout (so stdout stays parseable JSON) while a
    diagnostic is written to stderr, and :func:`main` returns exit code 8 via
    :class:`IncompleteReportError`.

    Args:
        argv: The argument vector to parse (excluding the program name). When
            ``None``, ``sys.argv[1:]`` is used.

    Returns:
        The process exit code (``0`` on success).
    """

    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        if not args.identifier:
            # Missing identifier: show usage guidance and raise the mapped
            # error so it flows through the top-level exit-code translation
            # (Req 1.2). Usage goes to stderr to keep stdout clean for JSON.
            parser.print_usage(sys.stderr)
            raise MissingIdentifierError(
                "A repository identifier ('owner/name') is required."
            )

        ref = parse_identifier(args.identifier)
        token = resolve_token(args.token, os.environ)

        client = GitHubClient(token)

        # Preflight existence/access check (design.md, "Data Flow"): verify the
        # repository exists and is accessible before running the analyzers. This
        # is the single place that surfaces whole-run abort errors —
        # RepoNotFoundError (Req 1.4), PrivateRepoAccessError (Req 2.6),
        # AuthenticationError (Req 2.7) — and, when the API cannot be reached at
        # all, ApiUnreachableError (Req 1.5). Performing it here (rather than
        # relying on a per-category call) guarantees a connection failure aborts
        # the run with exit code 7 instead of being absorbed by an analyzer's
        # own ApiUnreachableError handling.
        client.get_repo(ref)

        report = Orchestrator().analyze(ref, client)

        if args.json:
            # JSON is the only stdout content; diagnostics belong on stderr
            # (Req 10.1, 10.3, 10.5). The terminal renderer is never invoked.
            # render_json already emits an ``error`` field for an incomplete
            # report, so the document is written to stdout to keep it parseable
            # (Req 10.4). A raised IncompleteReportError then carries the
            # exit-code-8 mapping through the top-level handler below, with the
            # human-readable diagnostic routed to stderr (Req 10.5).
            print(render_json(report))
            if report.incomplete:
                raise IncompleteReportError(
                    "A required value was unavailable; the JSON report is "
                    "incomplete (see the 'error' field)."
                )
        else:
            # Color is decided by rich's terminal detection against the real
            # stdout (TTY plus NO_COLOR/FORCE_COLOR awareness) (Req 9.1).
            render_terminal(report, color=sys.stdout.isatty())
    except RateLimitExhaustedError as exc:
        # Rate limit exhausted: report when the limit resets (Req 2.5). Any
        # partial results already produced were retained by the caller. The
        # diagnostic goes to stderr so --json stdout stays clean (Req 10.5).
        message = str(exc) or "GitHub API rate limit exhausted."
        print(f"{message} Rate limit resets at {exc.reset_at}.", file=sys.stderr)
        return exc.exit_code
    except HealthCheckerError as exc:
        # Single top-level mapping for the whole exception hierarchy: emit the
        # message to stderr (diagnostics never contaminate --json stdout —
        # Req 10.5) and return the error's mapped exit code (Req 1.2-1.5,
        # 2.6-2.7, 8.1, 10.4).
        print(exc, file=sys.stderr)
        return exc.exit_code

    return 0
