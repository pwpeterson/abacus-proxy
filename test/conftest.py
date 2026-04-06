from __future__ import annotations

import sys
from pathlib import Path


def pytest_configure() -> None:
    """Ensure project imports work without requiring an editable install.

    Tests import modules under the `src/` package (e.g. `src.utils.*`).
    When running pytest from the repo root, we add:
      - repo root (so `import src` works)
      - repo_root/src (so direct module imports also work if needed)
    """
    repo_root = Path(__file__).resolve().parents[1]
    src_dir = repo_root / "src"

    for p in (repo_root, src_dir):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)