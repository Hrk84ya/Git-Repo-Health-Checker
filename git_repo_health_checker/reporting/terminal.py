"""Terminal report renderer (Req 9).

Renders a :class:`~git_repo_health_checker.models.HealthReport` to standard
output as a human-readable report: one per-category breakdown entry per
evaluated category (Req 9.3), the weighted score (Req 9.5), and the letter
grade (Req 9.6). When color is supported the report is colorized (Req 9.1);
when it is not, the same content is emitted without ANSI escape codes while
preserving all textual content (Req 9.2). If no category was evaluated, an
explicit "no categories evaluated" indication replaces the per-category
breakdown (Req 9.4).

The rendering pipeline is split so the pure, side-effect-free content
construction can be exercised directly in tests:

- :func:`evaluated_categories` — the ordered list of evaluated categories.
- :func:`build_report_lines` — the ordered plain-text lines that make up the
  report body (independent of color).

:func:`render_terminal` composes these and writes to stdout via ``rich``,
toggling color solely through the ``color`` parameter so the textual content
is identical between color and no-color modes (only ANSI styling differs).
"""

from __future__ import annotations

from rich.console import Console

from git_repo_health_checker.models import Category, CategoryResult, HealthReport

# Human-readable labels for each category, keyed by the enum member. Rendering
# uses these instead of the raw enum values so the breakdown reads naturally.
_CATEGORY_LABELS: dict[Category, str] = {
    Category.README: "README",
    Category.CI: "CI",
    Category.COVERAGE: "Coverage",
    Category.ISSUE_AGE: "Issue Age",
    Category.PR_ACTIVITY: "PR Activity",
}

# Indication rendered in place of the per-category breakdown when the report
# contains no evaluated categories (Req 9.4).
NO_CATEGORIES_MESSAGE = "No categories were evaluated."

# Text used for a category whose score is unavailable (available is False or
# score is None), so the entry is still listed (one per evaluated category)
# while signalling that no numeric score is present.
UNAVAILABLE_SCORE_TEXT = "unavailable"


def category_label(category: Category) -> str:
    """Return the human-readable label for ``category``.

    Falls back to the enum value for any category without an explicit label so
    the renderer never raises on an unmapped member.
    """

    return _CATEGORY_LABELS.get(category, category.value)


def evaluated_categories(report: HealthReport) -> list[Category]:
    """Return the report's evaluated categories in a stable display order.

    "Evaluated" means present as a key in ``report.categories`` (Req 9.3). The
    ordering follows the :class:`Category` enum's definition order, filtered to
    those present, giving a deterministic, readable sequence regardless of the
    dictionary's insertion order.
    """

    present = report.categories
    return [category for category in Category if category in present]


def _format_score(result: CategoryResult) -> str:
    """Format a single category result's score for display.

    Returns the integer score when the category is available with a numeric
    score, otherwise the :data:`UNAVAILABLE_SCORE_TEXT` indication.
    """

    if result.available and result.score is not None:
        return f"{result.score}/100"
    return UNAVAILABLE_SCORE_TEXT


def build_report_lines(report: HealthReport) -> list[str]:
    """Build the ordered plain-text lines composing the terminal report body.

    The returned lines are the full textual content of the report and are
    identical whether or not color is applied at render time (color only adds
    ANSI styling around this same text, satisfying Req 9.2). The content
    comprises:

    - A header identifying the repository.
    - Either one breakdown entry per evaluated category (Req 9.3) — each line
      showing the category label, its score (or an unavailable indication when
      ``available`` is ``False`` / ``score`` is ``None``), and any diagnostics
      — or the "no categories evaluated" indication when none exist (Req 9.4).
    - The weighted score (Req 9.5).
    - The letter grade (Req 9.6).
    """

    lines: list[str] = [f"Repository: {report.repo}"]

    categories = evaluated_categories(report)
    if categories:
        lines.append("Category breakdown:")
        for category in categories:
            result = report.categories[category]
            entry = f"  {category_label(category)}: {_format_score(result)}"
            if result.diagnostics:
                entry += f" ({'; '.join(result.diagnostics)})"
            lines.append(entry)
    else:
        lines.append(NO_CATEGORIES_MESSAGE)

    lines.append(f"Weighted score: {report.weighted_score}/100")
    lines.append(f"Grade: {report.grade.value}")
    return lines


def render_terminal(report: HealthReport, *, color: bool) -> None:
    """Write a per-category breakdown, weighted score, and grade to stdout.

    Falls back to plain text (no ANSI) when ``color`` is ``False``, preserving
    content (Req 9.1, 9.2). Displays exactly one breakdown entry per evaluated
    category (Req 9.3) or a "no categories evaluated" indication when none were
    evaluated (Req 9.4), followed by the weighted score (Req 9.5) and the
    letter grade (Req 9.6).

    Color is controlled solely by the ``color`` parameter: the console is
    constructed with ``no_color=not color`` (and ``force_terminal`` mirroring
    ``color``) so the emitted text is identical between modes and only ANSI
    escape sequences differ.
    """

    console = Console(no_color=not color, force_terminal=color, highlight=False)

    body = build_report_lines(report)

    # Optional styling applied per logical line when color is enabled. Styles
    # are intentionally omitted (style=None) in no-color mode; content is the
    # same list of lines either way (Req 9.2).
    header_style = "bold cyan" if color else None
    label_style = "bold" if color else None

    console.print(body[0], style=header_style)
    for line in body[1:-2]:
        console.print(line, style=label_style if line.endswith(":") else None)
    console.print(body[-2], style="bold" if color else None)
    console.print(body[-1], style="bold magenta" if color else None)
