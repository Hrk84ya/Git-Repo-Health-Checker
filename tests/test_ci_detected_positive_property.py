"""Property-based test for detected CI configuration yielding a positive score.

Feature: git-repo-health-checker, Property 5: Detected CI configuration yields
a positive score
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from git_repo_health_checker.analyzers.ci import score_ci

# Arbitrary repository file paths. These may or may not themselves be CI
# markers; the property injects a guaranteed marker separately so the assertion
# holds regardless of what noise is generated here. Control characters are
# excluded to keep generated paths reasonable, and surrogate categories are
# blacklisted to avoid unencodable text.
_PATH_ALPHABET = st.characters(
    blacklist_characters="\n\r",
    blacklist_categories=("Cs", "Cc"),
)

arbitrary_paths = st.lists(
    st.text(alphabet=_PATH_ALPHABET, min_size=0, max_size=40),
    min_size=0,
    max_size=10,
)

# A generated GitHub Actions workflow file under .github/workflows/ with a
# recognized extension. The stem excludes path separators and the '.' so the
# constructed path is a genuine single workflow file with a clean suffix.
_WORKFLOW_STEM_ALPHABET = st.characters(
    whitelist_categories=("Ll", "Lu", "Nd"),
    whitelist_characters="_-",
)

workflow_paths = st.builds(
    lambda stem, ext: f".github/workflows/{stem}{ext}",
    stem=st.text(alphabet=_WORKFLOW_STEM_ALPHABET, min_size=1, max_size=30),
    ext=st.sampled_from((".yml", ".yaml")),
)

# The full set of recognized CI markers: the fixed known configuration files
# plus a dynamically generated workflow definition.
recognized_ci_markers = st.one_of(
    st.sampled_from(
        [
            ".gitlab-ci.yml",
            ".circleci/config.yml",
            ".travis.yml",
            "azure-pipelines.yml",
        ]
    ),
    workflow_paths,
)


@st.composite
def paths_with_ci_marker(draw: st.DrawFn) -> list[str]:
    """Draw an arbitrary path listing containing at least one CI marker.

    A recognized CI marker is inserted at a random position within an otherwise
    arbitrary list of file paths, mirroring how a real repository listing mixes
    the marker among unrelated files.
    """

    paths = draw(arbitrary_paths)
    marker = draw(recognized_ci_markers)
    index = draw(st.integers(min_value=0, max_value=len(paths)))
    paths.insert(index, marker)
    return paths


@settings(max_examples=200)
@given(paths=paths_with_ci_marker())
def test_detected_ci_configuration_yields_positive_score(paths: list[str]) -> None:
    """Feature: git-repo-health-checker, Property 5: Detected CI configuration
    yields a positive score.

    Validates: Requirements 4.2

    For any repository file listing containing at least one recognized CI
    configuration file or workflow definition, the CI presence Category_Score
    (from ``score_ci``) SHALL be greater than 0.
    """

    assert score_ci(paths) > 0
