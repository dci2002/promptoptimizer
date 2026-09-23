"""main.app — application entry point.

Launches the pywebview window hosting the Prompt Optimizer UI.

Usage (from the project root)::

    python scripts/main/app.py

Phase 1 behaviour:
- load and validate ``config.yaml`` (fail clearly on invalid schema);
- create the :class:`ui.api.Api` bridge;
- open the window on ``scripts/ui/js/index.html`` and run the pywebview loop.
"""

from __future__ import annotations

import os
import sys

# ------------------------------------------------------------------ sys.path
# Ensure ``scripts/`` is importable (``core``, ``ui`` packages) regardless of
# the current working directory.  The app is launched as
# ``python scripts/main/app.py`` from the project root.
_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import webview  # noqa: E402

from core.config import ConfigError, ConfigManager  # noqa: E402
from ui.api import Api  # noqa: E402

#: Project root = parent of ``scripts/``.
PROJECT_ROOT = os.path.dirname(_SCRIPTS_DIR)

#: UI entry page.
INDEX_HTML = os.path.join(_SCRIPTS_DIR, "ui", "js", "index.html")

WINDOW_TITLE = "Prompt Optimizer"
WINDOW_WIDTH = 1000
WINDOW_HEIGHT = 720


def main() -> int:
    # 1. Load config — refuse to start with a clear message on failure.
    try:
        config = ConfigManager(path=os.path.join(PROJECT_ROOT, "config.yaml"))
    except ConfigError as e:
        print(f"ERROR: cannot start — {e}", file=sys.stderr)
        return 1

    # 2. Create the JS ↔ Python bridge.
    api = Api(config, base_dir=os.path.join(PROJECT_ROOT, "workspace"))

    # 3. Create the window and run the loop.
    window = webview.create_window(
        WINDOW_TITLE,
        INDEX_HTML,
        js_api=api,
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        min_size=(800, 600),
        resizable=True,
    )
    webview.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
