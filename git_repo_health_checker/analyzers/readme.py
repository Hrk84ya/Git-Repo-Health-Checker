"""README quality analyzer (Req 3).

This module holds the pure scoring logic for the README quality category. The
scoring function is a deterministic function of already-fetched README content,
kept free of network effects so it can be exercised in isolation with
property-based tests (README monotonicity, saturation, and the missing/empty
zero case). The analyzer wiring that fetches the README through the client is
added in a later subtask.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from git_repo_health_checker.errors import ApiUnreachableError
from git_repo_health_checker.models import Category, CategoryResult, RepoRef
from git_repo_health_checker.scoring import clamp

if TYPE_CHECKING:  # pragma: no cover - typing only
    from git_repo_health_checker.github_client import GitHubClient

# Content length (in characters) at which the length component saturates. A
# README of at least this length contributes the full length component.
README_LENGTH_TARGET = 1500

# Section-heading count at which the heading component saturates. A README with
# at least this many headings contributes the full heading component.
README_HEADING_TARGET = 5

# Relative contribution of the length and heading components. They sum to 1.0
# so the raw score stays in ``[0.0, 1.0]`` before scaling to ``[0, 100]``.
LENGTH_WEIGHT = 0.5
HEADING_WEIGHT = 0.5


def count_headings(content: str) -> int:
    """Count the Markdown section headings in ``content``.

    Recognizes both heading styles:

    - **ATX headings** — a line whose first non-space characters are one to six
      ``#`` characters followed by whitespace (e.g. ``## Usage``).
    - **Setext headings** — a non-empty text line immediately followed by a line
      consisting solely of ``=`` or ``-`` characters (e.g. a title underlined
      with ``====``).

    Returns the total heading count, which is zero for content with no
    headings.
    """

    lines = content.splitlines()
    count = 0

    for index, line in enumerate(lines):
        stripped = line.strip()

        # ATX heading: 1-6 leading '#' followed by a space or end of line.
        hashes = 0
        while hashes < len(stripped) and stripped[hashes] == "#":
            hashes += 1
        if 1 <= hashes <= 6:
            rest = stripped[hashes:]
            if rest == "" or rest[0] in (" ", "\t"):
                count += 1
                continue

        # Setext heading: a non-empty text line underlined by '=' or '-'.
        if stripped and index + 1 < len(lines):
            underline = lines[index + 1].strip()
            if underline and (
                all(ch == "=" for ch in underline)
                or all(ch == "-" for ch in underline)
            ):
                # Only count when the current line is plain text, not itself an
                # ATX heading or an underline for a previous line.
                if not stripped.startswith("#"):
                    count += 1

    return count


def score_readme(content: str | None) -> int:
    """Score README quality as an integer in ``[0, 100]`` (Req 3).

    Missing, empty, or whitespace-only content scores ``0`` (Req 3.2, 3.4).
    Otherwise the score is a non-decreasing function of content length and
    heading count, each saturating at its target (Req 3.3, 3.5):

    - ``length_component`` = ``min(1.0, len(content) / README_LENGTH_TARGET)``
    - ``heading_component`` = ``min(1.0, count_headings(content) / README_HEADING_TARGET)``
    - ``raw`` = ``LENGTH_WEIGHT * length_component + HEADING_WEIGHT * heading_component``

    The raw value is scaled to ``[0, 100]``, clamped, and rounded to the
    nearest integer.
    """

    if not content or not content.strip():
        return 0

    length_component = min(1.0, len(content) / README_LENGTH_TARGET)
    heading_component = min(1.0, count_headings(content) / README_HEADING_TARGET)
    raw = LENGTH_WEIGHT * length_component + HEADING_WEIGHT * heading_component
    return round(clamp(raw * 100, 0, 100))


# Diagnostic recorded when the README exists but its content cannot be
# retrieved/decoded, so the category is scored 0 without aborting the run.
UNREADABLE_README_DIAGNOSTIC = "README could not be evaluated"


class ReadmeAnalyzer:
    """Analyze README quality by fetching content and applying ``score_readme``.

    Implements the ``CategoryAnalyzer`` protocol (see
    :mod:`git_repo_health_checker.analyzers.base`).
    """

    category: Category = Category.README

    def analyze(self, ref: RepoRef, client: "GitHubClient") -> CategoryResult:
        """Fetch the README for ``ref`` and score its quality (Req 3.1, 3.6).

        The README is retrieved through the client and scored with the pure
        :func:`score_readme` (missing/empty content → ``0``). If the README
        cannot be read — surfaced as an :class:`ApiUnreachableError` retrieval
        failure — the category is scored ``0`` with a diagnostic and left
        ``available`` so it does not exclude or abort the rest of the run
        (Req 3.6). Whole-run abort errors (authentication, rate-limit, private
        access, repo-not-found) are not caught here and propagate to abort the
        run.
        """

        try:
            content = client.get_readme(ref)
        except ApiUnreachableError:
            return CategoryResult(
                category=Category.README,
                score=0,
                available=True,
                diagnostics=(UNREADABLE_README_DIAGNOSTIC,),
            )

        return CategoryResult(
            category=Category.README,
            score=score_readme(content),
            available=True,
        )
