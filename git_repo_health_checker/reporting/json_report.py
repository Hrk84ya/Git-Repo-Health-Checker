"""JSON report renderer (Req 10).

Serializes a :class:`~git_repo_health_checker.models.HealthReport` to a single,
well-formed JSON document (Req 10.1). The document carries each per-category
score together with its availability and any diagnostics, the overall weighted
score, the letter grade, the excluded categories, and the report-level
diagnostics (Req 10.2). When the report is incomplete — a required value could
not be produced (Req 10.4) — the document additionally carries an ``error``
field identifying the missing value(s); the rest of the structure is still
emitted on a best-effort basis so callers get whatever partial information is
available.

The rendering pipeline is split so the pure, side-effect-free dictionary
construction can be exercised directly in tests, independently of JSON
serialization:

- :func:`build_report_dict` — builds the plain ``dict`` mirroring the JSON
  document shape (property/round-trip and incomplete-path tests assert on this
  structure).
- :func:`render_json` — serializes that dict via :func:`json.dumps`, returning
  the string that is the sole stdout content in ``--json`` mode.

Key ordering is deterministic: the top-level keys follow the design document's
order, the ``categories`` mapping follows the :class:`Category` enum's
definition order (filtered to those present in the report), and ``excluded`` is
sorted by that same enum order for stable output.
"""

from __future__ import annotations

import json

from git_repo_health_checker.models import Category, CategoryResult, HealthReport


def _category_key_order() -> list[Category]:
    """Return the categories in :class:`Category` enum definition order.

    Used to give the emitted ``categories`` object and the ``excluded`` array a
    stable, human-readable ordering independent of dictionary insertion order.
    """

    return list(Category)


def _build_category_entry(result: CategoryResult) -> dict[str, object]:
    """Build the JSON object for a single category result.

    Emits ``score`` (the integer score when available, otherwise ``null``) and
    ``available`` (the boolean). A ``diagnostics`` array is included only when
    the category has non-empty diagnostics, mirroring the design example where
    ``diagnostics`` appears solely when present.
    """

    entry: dict[str, object] = {
        "score": result.score if result.available else None,
        "available": result.available,
    }
    if result.diagnostics:
        entry["diagnostics"] = list(result.diagnostics)
    return entry


def _missing_required_values(report: HealthReport) -> list[str]:
    """Return the names of required values missing from an incomplete report.

    A value is considered missing when a category present in the report is
    unavailable (``available`` is ``False`` or ``score`` is ``None``). The
    returned names are the category enum values (e.g. ``"pr_activity"``),
    ordered by :class:`Category` enum definition order for deterministic output.

    This drives the ``error`` field emitted for incomplete reports (Req 10.4).
    If no specific unavailable category can be identified, the caller falls back
    to a generic indication.
    """

    missing: list[str] = []
    for category in _category_key_order():
        result = report.categories.get(category)
        if result is None:
            continue
        if not result.available or result.score is None:
            missing.append(category.value)
    return missing


def build_report_dict(report: HealthReport) -> dict[str, object]:
    """Build the plain ``dict`` mirroring the JSON document for ``report``.

    The returned mapping is JSON-serializable and follows the design shape:

    - ``repository``: the ``owner/name`` reference as a string.
    - ``weighted_score``: the integer weighted score (0-100, Req 10.2).
    - ``grade``: the :class:`Grade` enum value string (Req 10.2).
    - ``categories``: an object keyed by :class:`Category` value; each entry has
      ``score`` (int or ``null``), ``available`` (bool), and — only when
      present — a ``diagnostics`` array. Ordered by enum definition order.
    - ``excluded``: a JSON array of excluded categories' enum values, sorted by
      enum definition order.
    - ``diagnostics``: the report-level diagnostics as a JSON array (always
      present; empty array when there are none).

    When ``report.incomplete`` is ``True`` (Req 10.4), an ``error`` field is
    added identifying the missing value(s). The error message has the format::

        required value unavailable: <name>[, <name>...]

    where each ``<name>`` is an unavailable category's enum value. When no
    specific category can be identified as missing, the message is::

        required value unavailable: report could not be fully generated

    The remaining structure is still populated best-effort so callers retain
    whatever partial information exists.
    """

    order = _category_key_order()

    categories: dict[str, object] = {}
    for category in order:
        result = report.categories.get(category)
        if result is None:
            continue
        categories[category.value] = _build_category_entry(result)

    excluded = [
        category.value for category in order if category in report.excluded
    ]

    document: dict[str, object] = {
        "repository": str(report.repo),
        "weighted_score": report.weighted_score,
        "grade": report.grade.value,
        "categories": categories,
        "excluded": excluded,
        "diagnostics": list(report.diagnostics),
    }

    if report.incomplete:
        missing = _missing_required_values(report)
        if missing:
            detail = ", ".join(missing)
        else:
            detail = "report could not be fully generated"
        document["error"] = f"required value unavailable: {detail}"

    return document


def render_json(report: HealthReport) -> str:
    """Return the JSON_Report string (the only stdout content in --json mode).

    Serializes :func:`build_report_dict` with :func:`json.dumps`, producing a
    single well-formed JSON document containing no human-readable or colorized
    formatting (Req 10.1). Key order is preserved from the built dict (not
    sorted) so the output is deterministic and matches the design shape. The
    result is parseable via :func:`json.loads` (Req 10.2), and includes an
    ``error`` field when the source report is incomplete (Req 10.4).
    """

    return json.dumps(build_report_dict(report), indent=2)
