"""Property-based test for identifier parsing.

Feature: git-repo-health-checker, Property 2: Identifier parsing accepts exactly well-formed owner/name
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from git_repo_health_checker.cli import parse_identifier
from git_repo_health_checker.errors import InvalidIdentifierError
from git_repo_health_checker.models import RepoRef

# GitHub-legal segment characters: alphanumerics, ``-``, ``_``, ``.`` (design
# Property 2). A well-formed identifier is exactly two non-empty legal segments
# joined by a single ``/``.
_LEGAL_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"

# Non-empty strings drawn only from the legal segment alphabet.
_legal_segments = st.text(alphabet=_LEGAL_CHARS, min_size=1, max_size=20)

# Arbitrary text spanning printable + punctuation so malformed inputs (extra
# slashes, spaces, illegal punctuation, empty segments) surface naturally.
_arbitrary_text = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126),
    min_size=0,
    max_size=30,
)


def _is_well_formed(raw: str) -> bool:
    """Independently decide whether ``raw`` is a well-formed ``owner/name``.

    Oracle mirroring the acceptance criterion (design Property 2), computed
    without reusing ``parse_identifier``'s implementation details: exactly two
    ``/``-separated segments, both non-empty and made only of legal characters.
    """

    segments = raw.split("/")
    if len(segments) != 2:
        return False
    return all(seg != "" and all(ch in _LEGAL_CHARS for ch in seg) for seg in segments)


@settings(max_examples=200)
@given(owner=_legal_segments, name=_legal_segments)
def test_well_formed_identifiers_parse(owner: str, name: str) -> None:
    """Feature: git-repo-health-checker, Property 2: Identifier parsing accepts exactly well-formed owner/name.

    Validates: Requirements 1.3

    Any two non-empty legal segments joined by a single ``/`` parse to a
    ``RepoRef`` carrying exactly that owner and name.
    """

    raw = f"{owner}/{name}"
    assert parse_identifier(raw) == RepoRef(owner=owner, name=name)


@settings(max_examples=200)
@given(raw=_arbitrary_text)
def test_parse_identifier_accepts_iff_well_formed(raw: str) -> None:
    """Feature: git-repo-health-checker, Property 2: Identifier parsing accepts exactly well-formed owner/name.

    Validates: Requirements 1.3

    For any input string, ``parse_identifier`` succeeds and yields the exact
    ``(owner, name)`` if and only if the string is two non-empty legal segments
    separated by a single ``/``; every other string raises
    ``InvalidIdentifierError``.
    """

    if _is_well_formed(raw):
        owner, name = raw.split("/")
        assert parse_identifier(raw) == RepoRef(owner=owner, name=name)
    else:
        with pytest.raises(InvalidIdentifierError):
            parse_identifier(raw)


@settings(max_examples=200)
@given(
    parts=st.lists(_legal_segments, min_size=0, max_size=5).filter(
        lambda ps: len(ps) != 2
    )
)
def test_wrong_segment_count_is_rejected(parts: list[str]) -> None:
    """Feature: git-repo-health-checker, Property 2: Identifier parsing accepts exactly well-formed owner/name.

    Validates: Requirements 1.3

    Any number of legal segments other than exactly two (zero, one, three or
    more), joined by ``/``, is rejected as malformed.
    """

    raw = "/".join(parts)
    # A single legal segment with no slash, or any count != 2, must be rejected.
    with pytest.raises(InvalidIdentifierError):
        parse_identifier(raw)
