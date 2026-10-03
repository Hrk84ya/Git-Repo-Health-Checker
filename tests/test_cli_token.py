"""Unit tests for token resolution in cli.py.

Covers the four flag/env combinations of ``resolve_token`` (Req 2.1-2.4):

- 2.1: no ``--token`` flag and no ``GITHUB_TOKEN`` env var -> None (unauthenticated).
- 2.2: ``GITHUB_TOKEN`` env var present, no flag -> the env token.
- 2.3: ``--token`` flag present, no env var -> the flag token.
- 2.4: both ``--token`` flag and ``GITHUB_TOKEN`` present -> the flag token
  (the flag takes precedence and the env value is ignored).
"""

from git_repo_health_checker.cli import resolve_token


class TestResolveTokenUnauthenticated:
    """No flag and no env var yields no token (Req 2.1)."""

    def test_no_flag_and_no_env_returns_none(self):
        assert resolve_token(None, {}) is None

    def test_no_flag_and_unrelated_env_returns_none(self):
        assert resolve_token(None, {"PATH": "/usr/bin"}) is None

    def test_empty_flag_and_empty_env_value_returns_none(self):
        # An empty string is not a usable token from either source.
        assert resolve_token("", {"GITHUB_TOKEN": ""}) is None


class TestResolveTokenFromEnv:
    """GITHUB_TOKEN env var, no flag, yields the env token (Req 2.2)."""

    def test_env_token_returned_when_no_flag(self):
        assert resolve_token(None, {"GITHUB_TOKEN": "env-token"}) == "env-token"

    def test_env_token_returned_when_flag_is_empty(self):
        # An empty flag is falsy, so the env var is used.
        assert resolve_token("", {"GITHUB_TOKEN": "env-token"}) == "env-token"


class TestResolveTokenFromFlag:
    """--token flag, no env var, yields the flag token (Req 2.3)."""

    def test_flag_token_returned_when_no_env(self):
        assert resolve_token("flag-token", {}) == "flag-token"

    def test_flag_token_returned_when_env_absent(self):
        assert resolve_token("flag-token", {"PATH": "/usr/bin"}) == "flag-token"


class TestResolveTokenFlagPrecedence:
    """--token flag takes precedence over GITHUB_TOKEN (Req 2.4)."""

    def test_flag_wins_over_env(self):
        resolved = resolve_token("flag-token", {"GITHUB_TOKEN": "env-token"})
        assert resolved == "flag-token"

    def test_env_value_is_ignored_when_flag_present(self):
        resolved = resolve_token("flag-token", {"GITHUB_TOKEN": "env-token"})
        assert resolved != "env-token"
