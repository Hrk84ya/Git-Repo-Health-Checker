"""The shared analyzer protocol (design.md, "Category Analyzers").

Every category analyzer implements :class:`CategoryAnalyzer`: it exposes the
:class:`~git_repo_health_checker.models.Category` it evaluates and an
``analyze`` method that fetches the data it needs through the GitHub client and
returns a :class:`~git_repo_health_checker.models.CategoryResult`.

The ``client`` parameter is typed against the ``GitHubClient`` interface
described in design.md. It is referenced only under :data:`typing.TYPE_CHECKING`
so this module does not import
:mod:`git_repo_health_checker.github_client` at runtime, avoiding an import
cycle (the client module is implemented in a later task).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from git_repo_health_checker.models import Category, CategoryResult, RepoRef

if TYPE_CHECKING:  # pragma: no cover - typing only
    from git_repo_health_checker.github_client import GitHubClient


@runtime_checkable
class CategoryAnalyzer(Protocol):
    """Common interface implemented by every category analyzer."""

    category: Category

    def analyze(self, ref: RepoRef, client: "GitHubClient") -> CategoryResult:
        """Evaluate this analyzer's category for ``ref`` using ``client``."""
        ...
