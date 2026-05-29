"""Entry point for the sequential OpenCode resume harness.

Implementation lives in :mod:`tests.manual.opencode_resume`. This shim
exists so the CLI command remains ``python tests/manual/test_opencode_resume.py``
matching the sibling ``test_all_models.py`` harness.
"""

from __future__ import annotations

import sys

if __name__ == "__main__":
    # Keep the ``sys.path`` mutation and package import inside the
    # ``__main__`` guard so pytest's automatic collection of this
    # ``test_*.py`` file imports a no-op module — the harness is a CLI
    # script, not a pytest test, and the guard prevents the path
    # manipulation from ever running during collection.
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from opencode_resume import main

    sys.exit(main())
