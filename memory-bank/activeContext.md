# Active Context

This file tracks the project's current status, including recent changes, current goals, and open questions.
2026-09-22 14:20:00 - Log of updates made.

## Current Focus

- [2026-09-22 15:27:00] Per user request: reference documents are linked in the Memory Bank. Architecture: `../architecture.md`; task list: `../tasklist.md` (also listed in `productContext.md` § Reference Documents).
- [2026-09-22 18:39:00] Phase 0 (Project bootstrap) complete — T0.1–T0.5 all done. Scaffolding created: folder structure (scripts/main, scripts/core, scripts/ui/js, scripts/dev, scripts/tests, workspace/), requirements.txt (5 deps), seed config.yaml (qwen-judge + gemma-target, roles, hl:false, max_attempts:30), package inits, and `import core` verified from project root. Next: Phase 1 (config core + skeleton window).

## Recent Changes

- [2026-09-22 18:39:00] Phase 0 bootstrap executed (T0.1–T0.5): created folder structure, requirements.txt, seed config.yaml, package inits; added `workspace/` to .gitignore (keeping `.gitkeep`); verified `import core` works from project root with `scripts/` on sys.path.
- [2026-09-22 14:20:00] Project initialized. Created architecture.md (folder structure, components, threading model, HL bridge design, config.yaml schema, UI layout, error handling) and tasklist.md (Phase 0–10, tasks T0.1–T10.5, milestones M1–M10). Initialized the Memory Bank.

## Open Questions/Issues

- [2026-09-22 14:20:00] HL "Continue" flow detail (req 3.7, item: "if the Result prompt field is pressed, it causes a user error") — interpreted as: pressing/clicking the "Result prompt" field while NOT in HL-wait state shows a user error; while waiting it is editable. Confirm with user if behavior should differ (e.g., "Continue pressed with empty field" only).
- [2026-09-22 14:20:00] max_attempts location in config.yaml — placed at top level (not per-LLM) per architecture §6; template had it under llm1. Acceptable?
- [2026-09-22 14:20:00] "Run prompt" uses LLM as Judge (per req 3.5: "runs the prompt on LLM as Judge") while the optimization loop uses LLM for prompts as the agent (llm1) and LLM as Judge as the target executor (llm2) — verified against template, but confirm the intended role mapping with the user.
