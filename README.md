# Git Repo Health Checker

A Python command-line tool that analyzes a GitHub repository and reports on its
overall health. It evaluates a repository across five categories — README
quality, CI presence, test coverage, open issue age, and pull request
activity — and combines the results into a weighted score from 0 to 100 with a
corresponding A–F letter grade.

## Installation

```bash
pip install .
# or
pipx install .
```

## Usage

```bash
repo-health owner/name
# or
python -m git_repo_health_checker owner/name
```

Add `--json` for machine-readable output and `--token` (or the `GITHUB_TOKEN`
environment variable) to raise rate limits and access private repositories.
