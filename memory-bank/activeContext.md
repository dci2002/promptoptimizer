# Active Context

This file tracks the project's current status, including recent changes, current goals, and open questions.
2026-09-22 14:20:00 - Log of updates made.

## Current Focus

- [2026-09-22 15:27:00] Per user request: reference documents are linked in the Memory Bank. Architecture: `../architecture.md`; task list: `../tasklist.md` (also listed in `productContext.md` § Reference Documents).
- [2026-09-22 18:39:00] Phase 0 (Project bootstrap) complete — T0.1–T0.5 all done. Scaffolding created: folder structure (scripts/main, scripts/core, scripts/ui/js, scripts/dev, scripts/tests, workspace/), requirements.txt (5 deps), seed config.yaml (qwen-judge + gemma-target, roles, hl:false, max_attempts:30), package inits, and `import core` verified from project root. Next: Phase 1 (config core + skeleton window).
- [2026-09-22 19:13:00] Phase 1 (Config core + skeleton UI) complete — T1.1–T1.8 all done and verified: `core/config.py` (ConfigManager load/save/validate + validate_llm_entry), `ui/js/index.html` two-tab skeleton, `ui/js/style.css`, `ui/js/app.js` (tabs + get_config bridge probe), `ui/api.py` (get_config implemented, rest stubbed), `scripts/main/app.py` entry, `scripts/tests/test_config.py` (25 tests pass). GUI test passed via offscreen DOM probe (`scripts/dev/probe_ui.py` → PROBE OK: window, tab switching, status line shows parsed config). Next: Phase 2 (Settings tab LLM table + CRUD).

## Recent Changes

- [2026-09-22 19:27:00] requirements.txt completeness checked per user request and completed: added the missing GUI backend deps (PyQt6, PyQt6-WebEngine, qtpy — mandatory because GTK is unavailable, pywebview must use the Qt backend) and pytest (tests). Verified by installing the updated requirements.txt into a clean venv (python3.12): all project imports succeed; 25/25 unit tests pass.
- [2026-09-22 19:13:00] Phase 1 executed (T1.1–T1.8): created `scripts/core/config.py` (ConfigManager: load with schema validation, atomic save via tmp+os.replace, accessors; validate_llm_entry), `scripts/ui/js/index.html` (two tabs with all section shells + icon buttons), `scripts/ui/js/style.css` (tabs/sections/multiline-10/icon-btn+CSS-tooltip/modal base/toast base), `scripts/ui/js/app.js` (tab switching + async get_config probe with retry), `scripts/ui/api.py` (Api.get_config + 15 stubs), `scripts/main/app.py` (main(): config load with clear failure, Api, webview window), `scripts/tests/test_config.py` (25 tests). Environment: created `.venv` (python3.12) with requirements + PyQt6 + PyQt6-WebEngine + qtpy + pytest; app runs offscreen via `QT_QPA_PLATFORM=offscreen QTWEBENGINE_DISABLE_GPU=1`. Added dev probe `scripts/dev/probe_ui.py`.
- [2026-09-22 18:39:00] Phase 0 bootstrap executed (T0.1–T0.5): created folder structure, requirements.txt, seed config.yaml, package inits; added `workspace/` to .gitignore (keeping `.gitkeep`); verified `import core` works from project root with `scripts/` on sys.path.
- [2026-09-22 14:20:00] Project initialized. Created architecture.md (folder structure, components, threading model, HL bridge design, config.yaml schema, UI layout, error handling) and tasklist.md (Phase 0–10, tasks T0.1–T10.5, milestones M1–M10). Initialized the Memory Bank.

## Open Questions/Issues

- [2026-09-22 14:20:00] HL "Continue" flow detail (req 3.7, item: "if the Result prompt field is pressed, it causes a user error") — interpreted as: pressing/clicking the "Result prompt" field while NOT in HL-wait state shows a user error; while waiting it is editable. Confirm with user if behavior should differ (e.g., "Continue pressed with empty field" only).
- [2026-09-22 14:20:00] max_attempts location in config.yaml — placed at top level (not per-LLM) per architecture §6; template had it under llm1. Acceptable?
- [2026-09-22 14:20:00] "Run prompt" uses LLM as Judge (per req 3.5: "runs the prompt on LLM as Judge") while the optimization loop uses LLM for prompts as the agent (llm1) and LLM as Judge as the target executor (llm2) — verified against template, but confirm the intended role mapping with the user.
