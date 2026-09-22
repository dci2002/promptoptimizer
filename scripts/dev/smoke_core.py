#!/usr/bin/env python3
"""Headless smoke test for the core package (Phase 3, T3.4).

Verifies config + prompt_io without a window. Intentionally a stub at
Phase 0 — the real implementation lands in Phase 3.

Run from the project root:
    python scripts/dev/smoke_core.py
"""

from __future__ import annotations

import sys
from pathlib import Path


def _ensure_scripts_on_path() -> None:
    """Put ``scripts/`` on sys.path so ``import core`` works from anywhere."""
    project_root = Path(__file__).resolve().parents[2]
    scripts_dir = project_root / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


def main() -> int:
    _ensure_scripts_on_path()
    print("smoke_core.py is a stub in Phase 0.")
    print("The real headless smoke test (config + prompt_io) lands in Phase 3 (T3.4).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
