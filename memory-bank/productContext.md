# Product Context

This file provides a high-level overview of the project and the expected product that will be created. Initially it is based upon requirements/req_en.md and all other available project-related information in the working directory. This file is intended to be updated as the project evolves, and should be used to inform all modes of the project's goals and context.
2026-09-22 14:20:00 - Log of updates made will be appended as footnotes to the end of this file.

## Project Goal

Build "Prompt Optimizer" — a desktop application (pywebview + vanilla JS) that iteratively optimizes LLM prompts using a two-LLM ReAct scheme (agent + judge/target LLM as tool), with a GUI for LLM configuration and Human-in-the-loop control of the analysis step.

## Key Features

- Two tabs: "Prompts" (prompt + result + variables, Run prompt, Start, Progress, Optimization result, Human-in-the-loop area) and "Settings" (LLM list table with Add/Edit/Remove, role dropdowns "LLM as Judge" / "LLM for prompts").
- Multiple named LLM configurations persisted in config.yaml (name, api_base, api_key, model, temperature).
- One-shot "Run prompt" check with validation of roles and {{var}} values (req 3.6).
- Full optimization loop (req 3.7): ReAct agent iterates build_prompt → llm2 → compare → explain_result → analyze_prompt → save_prompt until match or max_attempts.
- Human-in-the-loop mode: analyze_prompt delegated to a human via UI fields "Source prompt" (Copy to clipboard) and "Result prompt" + "Continue" button.
- Live step/tool-call logging into the "Progress" field.
- Icons-only buttons with hover tooltips (req "Styles").

## Overall Architecture

- `scripts/core/` — UI-independent logic: config.py (ConfigManager), prompt_io.py (files/variables/final prompt), llm.py (LLM2Executor + llm1 factory), agent.py (ReActAgent with 6 tools + HLBridge), runner.py (validation + run_prompt_check + run_optimization, event callbacks).
- `scripts/ui/` — pywebview layer: api.py (single Api bridge object), js/ (index.html two tabs, app.js polling every 500 ms, dialog.html/js LLM modal, variables.html/js variable modal, style.css).
- `scripts/main/app.py` — entry point: loads config, creates window + Api, starts pywebview.
- `workspace/` — per-run file sandbox (prompt.txt, result.txt, {var}.txt, final_prompt.txt, srcprompt.txt, dstprompt.txt, prompt_V<n>.txt).
- Worker thread model: Api.start_run launches a threading.Thread running run_optimization; shared state lock-protected; HL wait via threading.Event (replaces the template's file-polling HL).
- Execution logic adapted (not imported) from scripts/template/interview_copilot.py; reasoning_effort=None on every LLM call.

## Reference Documents

- requirements/req_en.md — functional requirements (source of truth).
- architecture.md — full architecture, folder structure, component contracts, config schema, UI layout, error handling.
- tasklist.md — phased task plan (Phase 0 bootstrap → Phase 10 e2e) with milestones M1–M10.
- scripts/template/ — immutable reference implementation (interview_copilot.py, design.md, config.yaml).

---

*2026-09-22 14:20:00 - Initial creation from requirements/req_en.md and scripts/template/ reference.*
