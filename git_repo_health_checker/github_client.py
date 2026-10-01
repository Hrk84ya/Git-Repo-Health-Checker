"""GitHub REST API client: request/transport layer.

A thin wrapper around the GitHub REST API using the :mod:`requests` library
(design.md, "GitHub Client"). It centralizes:

- Base URL handling (``https://api.github.com``).
- Optional token authentication (Req 2.2, 2.3): an ``Authorization`` header is
  attached only when a token is supplied; otherwise requests are sent
  unauthenticated (Req 2.1).
- Pagination via the ``Link`` header ``rel="next"`` relation.
- Uniform translation of HTTP outcomes into the tool's exception hierarchy
  (Req 1.4, 1.5, 2.5, 2.6, 2.7).

Isolating all network access here keeps the analyzers testable with in-memory
fakes and gives a single place to reason about error mapping.

The public data-fetch methods (``get_repo``, ``get_readme``, ``list_tree``,
``list_open_issues``, ``list_pull_requests``) build on
:meth:`GitHubClient._get` / :meth:`GitHubClient._get_paginated` and call
:meth:`GitHubClient._translate_error` with the appropriate context flags. The
README endpoint tolerates a 404 as "absent README" (returning ``None``) via
:meth:`GitHubClient._raw_get`, which returns the response for status
inspection instead of raising.

Status-code translation (design.md, "Status-code translation"):

=========================================  ==============================  ===
Condition                                  Exception                       Req
=========================================  ==============================  ===
Connection failure / timeout / DNS         ``ApiUnreachableError``         1.5
404 on ``get_repo``                        ``RepoNotFoundError``           1.4
404/403 on private repo, no token          ``PrivateRepoAccessError``      2.6
401 / bad credentials                      ``AuthenticationError``         2.7
403 with rate-limit-exhausted headers      ``RateLimitExhaustedError``     2.5
=========================================  ==============================  ===

The not-found-vs-private and rate-limit-vs-auth distinctions depend on context
(which endpoint, whether a token was supplied). Rather than guessing, the
translation is exposed as :meth:`GitHubClient._translate_error`, which accepts
context flags so each data-fetch method can request the correct mapping. See
that method's docstring for the resolution rules.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone

import requests

from git_repo_health_checker.errors import (
    ApiUnreachableError,
    AuthenticationError,
    PrivateRepoAccessError,
    RateLimitExhaustedError,
    RepoNotFoundError,
)
from git_repo_health_checker.models import (
    IssueRecord,
    PullRequestRecord,
    RepoMetadata,
    RepoRef,
)

#: Base URL for the GitHub REST API.
API_BASE_URL = "https://api.github.com"

#: Media type requested for GitHub REST API v3 responses.
GITHUB_ACCEPT = "application/vnd.github+json"

#: Default per-request timeout (connect + read) in seconds.
DEFAULT_TIMEOUT = 10


class GitHubClient:
    """Thin wrapper over the GitHub REST API.

    Parameters
    ----------
    token:
        An optional GitHub access token. When provided, every request carries
        an ``Authorization: Bearer <token>`` header (Req 2.2, 2.3). When
        ``None``, requests are unauthenticated (Req 2.1).
    session:
        An optional pre-configured :class:`requests.Session`. When omitted a
        new session is created. Injecting a session makes the client testable
        against a mocked HTTP layer.
    """

    def __init__(
        self,
        token: str | None,
        session: requests.Session | None = None,
    ) -> None:
        self._token = token
        self._session = session if session is not None else requests.Session()
        self._base_url = API_BASE_URL
        self._timeout = DEFAULT_TIMEOUT

    # -- headers ---------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        """Build request headers.

        The ``Authorization`` header is included only when a token is present
        (Req 2.2, 2.3); its absence yields an unauthenticated request (Req
        2.1).
        """
        headers = {"Accept": GITHUB_ACCEPT}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    # -- transport -------------------------------------------------------

    def _get(
        self,
        path: str,
        params: dict[str, object] | None = None,
        *,
        is_repo_check: bool = False,
    ) -> requests.Response:
        """Perform a single authenticated/unauthenticated GET.

        ``path`` may be an API path (e.g. ``/repos/o/n``) that is joined onto
        the base URL, or an absolute URL (as found in a ``Link`` header), which
        is used verbatim.

        Connection-level failures (DNS, connect, timeout) are translated into
        :class:`ApiUnreachableError` (Req 1.5). A response with an error status
        is translated via :meth:`_translate_error`; ``is_repo_check`` is
        forwarded so a 404 on the repo-existence check maps to
        :class:`RepoNotFoundError` (Req 1.4).
        """
        url = path if path.startswith("http") else f"{self._base_url}{path}"
        try:
            response = self._session.get(
                url,
                headers=self._headers(),
                params=params,
                timeout=self._timeout,
            )
        except requests.exceptions.RequestException as exc:
            # Covers ConnectionError, Timeout, and DNS/transport failures.
            raise ApiUnreachableError(str(exc)) from exc

        if not response.ok:
            self._translate_error(response, is_repo_check=is_repo_check)
        return response

    def _get_paginated(
        self,
        path: str,
        params: dict[str, object] | None = None,
    ) -> list[object]:
        """Fetch a paginated list resource, following ``Link`` ``rel="next"``.

        Each page's JSON body is expected to be a list; the results are
        accumulated across all pages until no ``next`` relation remains. The
        same status-code translation as :meth:`_get` applies to every page.
        """
        results: list[object] = []
        next_url: str | None = path
        # Query params only apply to the first request; subsequent page URLs
        # from the Link header already carry their own query string.
        next_params = params
        while next_url is not None:
            response = self._get(next_url, params=next_params)
            page = response.json()
            if isinstance(page, list):
                results.extend(page)
            else:
                results.append(page)
            next_url = self._next_link(response)
            next_params = None
        return results

    @staticmethod
    def _next_link(response: requests.Response) -> str | None:
        """Return the ``rel="next"`` URL from the response ``Link`` header.

        Uses ``requests``' parsed ``links`` mapping when available, falling
        back to ``None`` when there is no next page.
        """
        next_link = response.links.get("next")
        if next_link is not None:
            return next_link.get("url")
        return None

    # -- error translation ----------------------------------------------

    def _translate_error(
        self,
        response: requests.Response,
        *,
        is_repo_check: bool = False,
    ) -> None:
        """Translate an error response into the exception hierarchy.

        This is the single, uniform mapping applied to every failed call. It
        never returns normally: it always raises one of the mapped exceptions.

        Context flags disambiguate cases that share a status code:

        - ``is_repo_check``: set by ``get_repo`` (the repo-existence check).
          When ``True`` and no token is present, a 404 is a genuine
          not-found and maps to :class:`RepoNotFoundError` (Req 1.4). When
          ``True`` and a token *is* present, a 404/403 more likely means the
          authenticated user cannot see the repo; it is still surfaced as
          not-found for the repo check.
        - ``self._token``: whether a token was supplied. Without a token, a
          403/404 on a repo indicates a private repository that requires
          authentication and maps to :class:`PrivateRepoAccessError` (Req
          2.6). With a token, a rejected request maps to
          :class:`AuthenticationError` (Req 2.7).

        Resolution rules, in order:

        1. 401 (or bad credentials) -> :class:`AuthenticationError` (Req 2.7).
        2. 403 with rate-limit-exhausted headers
           (``X-RateLimit-Remaining: 0``) -> :class:`RateLimitExhaustedError`
           carrying the ``X-RateLimit-Reset`` time (Req 2.5).
        3. 403 without rate-limit headers, no token -> the repo is private and
           needs auth -> :class:`PrivateRepoAccessError` (Req 2.6).
        4. 403 without rate-limit headers, with token -> credentials lack
           access -> :class:`AuthenticationError` (Req 2.7).
        5. 404 without a token -> could be private-and-hidden ->
           :class:`PrivateRepoAccessError` (Req 2.6).
        6. 404 with a token, on a repo check -> :class:`RepoNotFoundError`
           (Req 1.4).
        7. 404 otherwise -> :class:`RepoNotFoundError` (Req 1.4).

        Any other non-OK status falls through to
        :class:`ApiUnreachableError` (Req 1.5) as a conservative default.
        """
        status = response.status_code

        if status == 401:
            raise AuthenticationError("GitHub rejected the access token (401).")

        if status == 403:
            if self._is_rate_limited(response):
                raise RateLimitExhaustedError(
                    self._reset_at(response),
                    "GitHub API rate limit exhausted (403).",
                )
            if self._token:
                raise AuthenticationError(
                    "Access forbidden; the access token lacks permission (403)."
                )
            raise PrivateRepoAccessError(
                "Repository is private and requires authentication (403)."
            )

        if status == 404:
            if not self._token:
                # Without auth, GitHub returns 404 for private repos to avoid
                # disclosing their existence; treat as an access issue.
                raise PrivateRepoAccessError(
                    "Repository not found or private; authentication may be "
                    "required (404)."
                )
            raise RepoNotFoundError("Repository does not exist (404).")

        # Any other error status: treat the API as effectively unreachable for
        # this request (Req 1.5).
        raise ApiUnreachableError(
            f"Unexpected GitHub API response status {status}."
        )

    @staticmethod
    def _is_rate_limited(response: requests.Response) -> bool:
        """Return ``True`` when the response indicates an exhausted rate limit.

        A 403 accompanied by ``X-RateLimit-Remaining: 0`` marks rate-limit
        exhaustion (Req 2.5), distinct from a permission/auth 403.
        """
        remaining = response.headers.get("X-RateLimit-Remaining")
        return remaining is not None and remaining.strip() == "0"

    @staticmethod
    def _reset_at(response: requests.Response) -> datetime | None:
        """Convert the ``X-RateLimit-Reset`` epoch header into a UTC datetime.

        Returns ``None`` when the header is missing or unparseable so callers
        can still report an exhausted limit without a precise reset time.
        """
        raw = response.headers.get("X-RateLimit-Reset")
        if raw is None:
            return None
        try:
            epoch = int(raw)
        except (TypeError, ValueError):
            return None
        return datetime.fromtimestamp(epoch, tz=timezone.utc)

    # -- data-fetch methods ---------------------------------------------

    def get_repo(self, ref: RepoRef) -> RepoMetadata:
        """Fetch repository metadata, verifying existence and access.

        Performs ``GET /repos/{owner}/{name}`` (design.md, "GitHub Client";
        Req 1.1). ``is_repo_check=True`` is forwarded to :meth:`_get` so a 404
        maps per the repo-existence rules (``RepoNotFoundError`` with a token,
        ``PrivateRepoAccessError`` without one).

        Returns
        -------
        RepoMetadata
            Carrying the ``private`` flag and the ``default_branch`` used by
            :meth:`list_tree` and content retrieval.
        """
        response = self._get(
            f"/repos/{ref.owner}/{ref.name}",
            is_repo_check=True,
        )
        data = response.json()
        return RepoMetadata(
            ref=ref,
            private=bool(data.get("private", False)),
            default_branch=str(data.get("default_branch", "")),
        )

    def get_readme(self, ref: RepoRef) -> str | None:
        """Return the repository README as decoded text, or ``None`` if absent.

        Performs ``GET /repos/{owner}/{name}/readme``, which returns JSON with
        base64-encoded ``content`` and an ``encoding`` field. A 404 means the
        repository simply has no README and must yield ``None`` rather than
        propagating an error, so this uses :meth:`_raw_get` and inspects the
        status directly: a 404 short-circuits to ``None`` while any other error
        status is routed through :meth:`_translate_error` for the uniform
        mapping.
        """
        response = self._raw_get(f"/repos/{ref.owner}/{ref.name}/readme")
        if response.status_code == 404:
            return None
        if not response.ok:
            self._translate_error(response)
        data = response.json()
        content = data.get("content")
        if content is None:
            return None
        encoding = (data.get("encoding") or "").lower()
        if encoding == "base64":
            decoded = base64.b64decode(content)
            return decoded.decode("utf-8", errors="replace")
        # Unencoded (or unknown-encoding) content is returned as-is.
        return str(content)

    def list_tree(self, ref: RepoRef) -> list[str]:
        """Return file paths on the default branch for CI/test detection.

        Resolves the default branch via :meth:`get_repo`, then performs
        ``GET /repos/{owner}/{name}/git/trees/{default_branch}?recursive=1``
        and returns the ``path`` of every ``blob`` (file) entry in the ``tree``
        array. Directory (``tree``) entries are excluded so callers see only
        real files.
        """
        metadata = self.get_repo(ref)
        response = self._get(
            f"/repos/{ref.owner}/{ref.name}/git/trees/{metadata.default_branch}",
            params={"recursive": "1"},
        )
        data = response.json()
        entries = data.get("tree", []) if isinstance(data, dict) else []
        paths: list[str] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("type") == "blob":
                path = entry.get("path")
                if path is not None:
                    paths.append(str(path))
        return paths

    def list_open_issues(self, ref: RepoRef) -> list[IssueRecord]:
        """Return open issues, flagging any that are actually pull requests.

        Performs a paginated ``GET /repos/{owner}/{name}/issues?state=open``.
        The GitHub issues endpoint also returns pull requests; an item is a PR
        when it carries a ``pull_request`` key. Each item becomes an
        :class:`IssueRecord` with a timezone-aware ``created_at`` and the
        ``is_pull_request`` flag set accordingly, leaving PR filtering to the
        analyzer (Req 6.2).
        """
        items = self._get_paginated(
            f"/repos/{ref.owner}/{ref.name}/issues",
            params={"state": "open"},
        )
        records: list[IssueRecord] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            created_at = _parse_github_datetime(item.get("created_at"))
            if created_at is None:
                continue
            records.append(
                IssueRecord(
                    created_at=created_at,
                    is_pull_request="pull_request" in item,
                )
            )
        return records

    def list_pull_requests(
        self, ref: RepoRef, since: datetime
    ) -> list[PullRequestRecord]:
        """Return pull requests created on or after ``since``.

        Performs a paginated
        ``GET /repos/{owner}/{name}/pulls?state=all&sort=created&direction=desc``.
        Because results are ordered by creation time descending, iteration
        stops as soon as a PR older than ``since`` is seen: everything after it
        is also older. Each kept PR becomes a :class:`PullRequestRecord` with a
        timezone-aware ``created_at``.
        """
        items = self._get_paginated(
            f"/repos/{ref.owner}/{ref.name}/pulls",
            params={"state": "all", "sort": "created", "direction": "desc"},
        )
        records: list[PullRequestRecord] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            created_at = _parse_github_datetime(item.get("created_at"))
            if created_at is None:
                continue
            if created_at < since:
                # Sorted created-desc: the first older PR marks the boundary.
                break
            records.append(PullRequestRecord(created_at=created_at))
        return records

    # -- raw transport (for endpoints with tolerated statuses) ----------

    def _raw_get(
        self,
        path: str,
        params: dict[str, object] | None = None,
    ) -> requests.Response:
        """Perform a GET and return the response WITHOUT status translation.

        Identical to :meth:`_get` for connection-level failures (still mapped
        to :class:`ApiUnreachableError`, Req 1.5), but it does not raise on an
        error status. This lets callers such as :meth:`get_readme` inspect the
        status code (e.g. treat 404 as "absent") before deciding whether to
        delegate to :meth:`_translate_error`.
        """
        url = path if path.startswith("http") else f"{self._base_url}{path}"
        try:
            return self._session.get(
                url,
                headers=self._headers(),
                params=params,
                timeout=self._timeout,
            )
        except requests.exceptions.RequestException as exc:
            raise ApiUnreachableError(str(exc)) from exc


def _parse_github_datetime(raw: object) -> datetime | None:
    """Parse a GitHub ISO 8601 timestamp into a timezone-aware UTC datetime.

    GitHub emits timestamps like ``2024-01-01T00:00:00Z``. The trailing ``Z``
    (UTC designator) is normalized to ``+00:00`` so :meth:`datetime.fromisoformat`
    can parse it; naive results are pinned to UTC. Returns ``None`` for missing
    or unparseable values so callers can skip malformed records rather than
    fail the whole fetch.
    """
    if not isinstance(raw, str) or not raw:
        return None
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
