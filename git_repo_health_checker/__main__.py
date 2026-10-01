"""Enable ``python -m git_repo_health_checker``.

Delegates to :func:`git_repo_health_checker.cli.main`. The import is performed
lazily inside ``_main`` so this module can be imported even before ``cli.py``
exists (it is implemented in a later task).
"""

import sys


def _main() -> int:
    from git_repo_health_checker.cli import main

    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(_main())
