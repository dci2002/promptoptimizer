# Decision Log

This file records architectural and implementation decisions using a list format.
2026-09-22 14:20:00 - Log of updates made.

## Decision

- [2026-09-22 14:20:00] HL (Human-in-the-loop) is implemented via an in-memory `threading.Event` bridge (`HLBridge`) injected into `ReActAgent`, NOT the template's `srcprompt.txt`/`dstprompt.txt` file polling. `srcprompt.txt` is still written (for audit), but the wait is event-based and the user responds through the GUI "Result prompt" field + "Continue" button.
- [2026-09-22 14:20:00] config.yaml schema redesigned: template's `llm1`/`llm2`/`HL` keys replaced with a normalized `llms[]` list + `roles: {judge, prompts}` + top-level `hl` + `max_attempts`. Seeded from the template's two endpoints.
- [2026-09-22 14:20:00] All dialogs (LLM add/edit, variable add) are in-page modals in the single pywebview window — no separate pywebview windows (simpler state, no cross-window IPC).
- [2026-09-22 14:20:00] Long-running optimization executes on a dedicated worker thread; JS polls `api.get_state()` every 500 ms (pywebview GUI-thread constraint: Api methods must return fast).
- [2026-09-22 14:20:00] `scripts/template/` is treated as an immutable reference: code is adapted/copy-modified into `scripts/core/`, never imported.
- [2026-09-22 14:20:00] `core/` has zero pywebview dependencies (unit-testable headless); `ui/api.py` is the only bridge; UI never calls langchain directly.
- [2026-09-22 14:20:00] Per-run files are isolated in `workspace/` (fresh per Start), not the app root as in the template's CLI layout.
- [2026-09-22 14:20:00] Agent logging: template `print()` calls replaced with an `on_event(str)` callback forwarded to the "Progress" field (bounded deque, 500 lines).
- [2026-09-22 14:20:00] Role mapping: "LLM as Judge" = llm2 (target executor tool) in the optimization loop; "LLM for prompts" = llm1 (ReAct agent). "Run prompt" executes the prompt on LLM as Judge per req 3.5.
- [2026-09-22 15:01:00] tasklist.md restructured so every phase ends with a manual GUI test (per user request): skeleton window + get_config probe in Phase 1, Settings CRUD in Phase 2, variables + live 3.6 validation strip in Phase 3, real "Run prompt" LLM call in Phase 4, dry-run Start + polling pipeline in Phase 5, real automatic run in Phase 6, HL flow in Phase 7, persistence/polish/full regression in Phase 8. A headless smoke helper (scripts/dev/smoke_core.py) complements the GUI tests.
- [2026-09-22 19:13:00] Python environment: a project-local `.venv` on python3.12 (`.venv/bin/python`) is the canonical interpreter for the app and tests (per user request to install for python3.12; system pip is PEP 668 externally-managed). GUI backend: pywebview + PyQt6 (+ PyQt6-WebEngine, qtpy). GTK (`gi`) is NOT available in this environment; pywebview logs a harmless "GTK cannot be loaded" notice and falls back to Qt.
- [2026-09-22 19:13:00] Headless GUI verification: `scripts/dev/probe_ui.py` launches the real window with `QT_QPA_PLATFORM=offscreen QTWEBENGINE_DISABLE_GPU=1` and polls the DOM via `window.evaluate_js`. Known pywebview/Qt quirks worked around: `webview.destroy()` from a non-GUI thread hangs the loop (probe uses `window.hide()` + `os._exit()`); `evaluate_js` must receive a synchronous expression (async IIFEs serialize to `{}`); JS bridge probes must retry until `window.pywebview.api` is injected.
- [2026-09-22 19:27:00] requirements.txt lists the GUI backend explicitly (PyQt6, PyQt6-WebEngine, qtpy) instead of relying on pywebview to auto-detect an available backend: in this environment GTK (`gi`) is unavailable, so without these entries a fresh `pip install -r requirements.txt` yields an app that cannot open a window. pytest is listed as the test dependency.

## Rationale

- [2026-09-22 14:20:00] File polling blocks a thread for unbounded time and provides no UI feedback; an event bridge integrates cleanly with the GUI and keeps the tool contract (response format `ШАБЛОН:` / `ИЗМЕНЕНИЯ:`) identical to the template.
- [2026-09-22 14:20:00] Normalized LLM list supports the required Add/Edit/Remove table and role dropdowns; two hardcoded LLM slots cannot satisfy scenarios 3.1–3.4.
- [2026-09-22 14:20:00] In-page modals keep a single source of truth (one window, one Api) and avoid pywebview multi-window lifecycle complexity.
- [2026-09-22 14:20:00] Polling `get_state()` is the simplest robust pattern for pywebview (no websockets); 500 ms is responsive enough for log streaming.
- [2026-09-22 14:20:00] Copy-modifying (not importing) the template isolates the app from template layout assumptions (base_dir = script dir) and lets the workspace/ separation and logging hooks be introduced cleanly.
- [2026-09-22 14:20:00] UI/core separation enables headless unit tests of the entire optimization pipeline without a display.
- [2026-09-22 14:20:00] The template's CLI design writes prompt.txt etc. next to the script; for a GUI app a dedicated workspace/ prevents collisions between concurrent/sequential runs.

## Implementation Details

- [2026-09-22 14:20:00] See architecture.md §3.4 (HLBridge protocol), §3.6 (Api method table + get_state shape), §3.7 (threading sequence diagram), §6 (config schema), §8 (error handling table).
- [2026-09-22 14:20:00] See tasklist.md for the full task breakdown: Phase 0 bootstrap → 1 config → 2 prompt_io → 3 llm → 4 agent → 5 runner → 6 bridge → 7 app entry → 8 settings UI → 9 prompts UI → 10 e2e; milestones M1–M10.
