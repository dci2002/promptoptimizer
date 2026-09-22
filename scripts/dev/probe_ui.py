"""dev.probe_ui — headless UI probe (dev helper, not part of the app).

Launches the real window offscreen, waits for the page + bridge to load,
then dumps the DOM state via ``window.evaluate_js`` and exits 0 on success.

Used to verify Phase 1's GUI test (window opens, tabs exist, bridge works)
without a human in front of the screen:

    DISPLAY=:0.0 QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/dev/probe_ui.py
"""

from __future__ import annotations

import os
import sys
import threading
import time

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import webview  # noqa: E402

from core.config import ConfigManager  # noqa: E402
from ui.api import Api  # noqa: E402

PROJECT_ROOT = os.path.dirname(_SCRIPTS_DIR)
INDEX_HTML = os.path.join(_SCRIPTS_DIR, "ui", "js", "index.html")

PROBE_JS = """
(() => {
    const status = (document.getElementById('status-line') || {}).textContent || '';
    const tabs = [...document.querySelectorAll('.tab-btn')].map(b =>
        b.textContent + (b.classList.contains('active') ? '*' : ''));
    const panels = [...document.querySelectorAll('.tab-panel')].map(p =>
        p.id + (p.classList.contains('active') ? '*' : ''));
    // exercise tab switching
    const settingsBtn = document.querySelector('.tab-btn[data-tab="settings"]');
    if (settingsBtn) settingsBtn.click();
    const afterSwitch = [...document.querySelectorAll('.tab-panel')]
        .filter(p => p.classList.contains('active')).map(p => p.id);
    return {status: status, tabs: tabs, panels: panels, afterSwitch: afterSwitch};
})()
"""


def main() -> int:
    config = ConfigManager(path=os.path.join(PROJECT_ROOT, "config.yaml"))
    api = Api(config)
    window = webview.create_window(
        "Prompt Optimizer (probe)", INDEX_HTML, js_api=api, width=1000, height=720
    )

    result = {}
    error = {}

    def probe():
        # NOTE: webview.destroy() must run on the GUI thread; from a worker
        # thread it hangs the Qt event loop. We use window.hide() (thread-safe
        # enough in practice for offscreen) and then os._exit after printing.
        # Poll PROBE_JS until the status line is no longer 'Connecting…'
        # (the bridge probe finishes a few seconds after page load).
        for _ in range(60):
            time.sleep(1)
            try:
                out = window.evaluate_js(PROBE_JS)
            except Exception:  # noqa: BLE001
                out = None
            if isinstance(out, dict) and "Connecting…" not in str(out.get("status", "")):
                result["value"] = out
                finish()
                return
        error["value"] = "status line never left 'Connecting…'"
        finish()

    def finish():
        try:
            window.hide()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1)
        # print + hard exit so the Qt loop cannot hold us
        if "value" in result:
            r = result["value"]
            print("DOM probe result:", r, flush=True)
            ok = True
            if "Connecting…" in str(r.get("status", "")):
                print("FAIL: status line still 'Connecting…' — bridge probe did not complete", flush=True)
                ok = False
            if not any(t.startswith("Prompts") for t in r.get("tabs", [])):
                print("FAIL: Prompts tab missing", flush=True)
                ok = False
            if not any(t.startswith("Settings") for t in r.get("tabs", [])):
                print("FAIL: Settings tab missing", flush=True)
                ok = False
            if r.get("afterSwitch") != ["tab-settings"]:
                print("FAIL: tab switching did not activate the Settings panel", flush=True)
                ok = False
            if "Config loaded" not in str(r.get("status", "")):
                print("FAIL: status line does not report a loaded config", flush=True)
                ok = False
            print("PROBE OK" if ok else "PROBE FAILED", flush=True)
            os._exit(0 if ok else 1)
        else:
            print("PROBE FAILED:", error.get("value", "no result"), flush=True)
            os._exit(1)

    threading.Thread(target=probe, daemon=True).start()
    webview.start()
    # Unreachable in practice: finish() hard-exits the process.
    return 1


if __name__ == "__main__":
    sys.exit(main())
