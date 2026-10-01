"""Exception hierarchy for the Git Repo Health Checker.

Every error raised by the tool derives from :class:`HealthCheckerError` and
carries a class-level ``exit_code``. The CLI catches these at the top level,
routes messages appropriately (stderr in ``--json`` mode), and returns the
mapped code as the process exit status.

Exit-code mapping (see design.md, "Error Handling"):

===============================  =========  ============================================
Exception                        Exit code  Trigger
===============================  =========  ============================================
``MissingIdentifierError``       2          No repository argument (usage shown)
``InvalidIdentifierError``       2          Malformed ``owner/name``
``RepoNotFoundError``            3          Well-formed but non-existent repo
``PrivateRepoAccessError``       4          Private repo, no token
``AuthenticationError``          5          Token rejected / unauthorized
``RateLimitExhaustedError``      6          Rate limit exhausted (reports reset time)
``ApiUnreachableError``          7          GitHub API unreachable
``IncompleteReportError``        8          ``--json`` and required value unavailable
``ConfigError``                  9          Weights do not sum to 1.0
===============================  =========  ============================================
"""


class HealthCheckerError(Exception):
    """Base class for all Health_Checker errors.

    Every subclass carries a class-level ``exit_code`` used by the CLI to
    translate a raised error into a process exit status. The attribute is
    defined on the class so it is accessible without needing an instance.
    """

    exit_code: int = 1


class MissingIdentifierError(HealthCheckerError):
    """Raised when no repository identifier argument is supplied (Req 1.2)."""

    exit_code: int = 2


class InvalidIdentifierError(HealthCheckerError):
    """Raised when the ``owner/name`` identifier is malformed (Req 1.3)."""

    exit_code: int = 2


class RepoNotFoundError(HealthCheckerError):
    """Raised when a well-formed identifier names a non-existent repo (Req 1.4)."""

    exit_code: int = 3


class PrivateRepoAccessError(HealthCheckerError):
    """Raised when a private repository is accessed without a token (Req 2.6)."""

    exit_code: int = 4


class AuthenticationError(HealthCheckerError):
    """Raised when the supplied token is rejected or unauthorized (Req 2.7)."""

    exit_code: int = 5


class RateLimitExhaustedError(HealthCheckerError):
    """Raised when the GitHub API rate limit is exhausted (Req 2.5).

    Carries ``reset_at``, the ``X-RateLimit-Reset`` timestamp, so the CLI can
    report when the limit resets. Any partial results already produced are
    retained and reported by the caller.
    """

    exit_code: int = 6

    def __init__(self, reset_at, *args: object) -> None:
        super().__init__(*args)
        self.reset_at = reset_at


class ApiUnreachableError(HealthCheckerError):
    """Raised when the GitHub API is unreachable (Req 1.5)."""

    exit_code: int = 7


class IncompleteReportError(HealthCheckerError):
    """Raised when ``--json`` is set but a required value is unavailable (Req 10.4)."""

    exit_code: int = 8


class ConfigError(HealthCheckerError):
    """Raised when configuration is invalid, e.g. weights not summing to 1.0 (Req 8.1)."""

    exit_code: int = 9
