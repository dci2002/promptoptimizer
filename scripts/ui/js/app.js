/* Prompt Optimizer — app.js (Phase 1: tab switching + bridge probe) */

(function () {
    "use strict";

    var statusLine = document.getElementById("status-line");

    function setStatus(text, cls) {
        statusLine.textContent = text;
        statusLine.className = cls || "";
    }

    /* ───────────────────────── Tab switching ───────────────────────── */

    var tabButtons = document.querySelectorAll(".tab-btn");
    var tabPanels = document.querySelectorAll(".tab-panel");

    function switchTab(name) {
        tabButtons.forEach(function (btn) {
            btn.classList.toggle("active", btn.dataset.tab === name);
        });
        tabPanels.forEach(function (panel) {
            panel.classList.toggle("active", panel.id === "tab-" + name);
        });
    }

    tabButtons.forEach(function (btn) {
        btn.addEventListener("click", function () {
            switchTab(btn.dataset.tab);
        });
    });

    /* ───────────────────────── Bridge probe (Phase 1) ─────────────────────────
     * On load call window.pywebview.api.get_config() and log the result
     * to the status line to prove the JS ↔ Python bridge works.
     */

    function probeBridge() {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.get_config) {
            window.pywebview.api.get_config().then(function (cfg) {
                    if (!cfg || cfg.ok === false) {
                        setStatus(
                            "Bridge error: " + (cfg ? cfg.error : "no config returned"),
                            "error"
                        );
                        return;
                    }
                    var llms = cfg.llms || [];
                    var roles = cfg.roles || {};
                    var msg =
                        "Config loaded: " +
                        llms.length + " LLMs (" +
                        llms.map(function (l) { return l.name; }).join(", ") +
                        "), roles: judge=" + (roles.judge || "—") +
                        ", prompts=" + (roles.prompts || "—") +
                        ", hl=" + cfg.hl +
                        ", max_attempts=" + cfg.max_attempts;
                    setStatus(msg, "ok");
                })
                .catch(function (err) {
                    setStatus("Bridge error: " + err, "error");
                });
        } else {
            // pywebview injects window.pywebview asynchronously on first event.
            // Retry briefly before giving up.
            var attempts = 0;
            var timer = setInterval(function () {
                attempts += 1;
                if (window.pywebview && window.pywebview.api && window.pywebview.api.get_config) {
                    clearInterval(timer);
                    probeBridge();
                } else if (attempts > 80) {
                    clearInterval(timer);
                    setStatus("pywebview API not available (window bridge offline)", "error");
                }
            }, 250);
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", probeBridge);
    } else {
        probeBridge();
    }
})();
