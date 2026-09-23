# System Patterns

This file documents recurring patterns and standards used in the project.
It is optional, but recommended to be updated as the project evolves.
2026-09-22 14:20:00 - Log of updates made.

## Coding Patterns

- [2026-09-22 14:20:00] Python 3.10+ type annotations throughout (`list[str]`, `dict[str, str]`); no `typing.List/Dict`.
- [2026-09-22 14:20:00] `reasoning_effort=None` must be set on EVERY `ChatOpenAI` instantiation (disables extended-thinking on local vLLM-style servers).
- [2026-09-22 14:20:00] LLM configs are validated at construction time: empty/placeholder api_key → `ValueError` with a hint pointing at Settings/config.yaml.
- [2026-09-22 14:20:00] All file I/O uses `encoding="utf-8"` explicitly.
- [2026-09-22 14:20:00] `core/` modules are side-effect-free on import; no globals except constants (SYSTEM_PROMPT).
- [2026-09-22 14:20:00] Errors from core are returned as `{"ok": False, "error": "..."}` dicts at the bridge boundary; exceptions only for programmer errors.
- [2026-09-22 14:20:00] Variable placeholders use the strict regex `\{\{(\w+)\}\}` — same as the template.
- [2026-09-22 14:20:00] ConfigManager.save() is atomic: write to a temp file in the same directory, then `os.replace`.
- [2026-09-23 12:22:00] `build_final_prompt` returns the final prompt **string** (not a file path) — GUI callers need the text. The template returned a path; this was adapted for the GUI context. The fully-substituted text is also written to `final_prompt.txt` for the LLM2 executor.
- [2026-09-23 12:22:00] `write_run_files` accepts `variables` as a `dict[str, str]` (not a list of dicts) — the JS `[{name, value}]` array is normalized to a dict at the bridge boundary (`Api.run_validation`).
- [2026-09-23 12:22:00] UI validation is debounced (400 ms) via `scheduleValidation()` / `runValidation()` — the strip re-renders after any prompt/result/variable/role change without flooding the bridge on every keystroke.
- [2026-09-23 12:22:00] Variable names are validated client-side with `^\w+$` before being added to the table, matching the server-side extraction regex.
- [2026-09-23 13:39:00] Clipboard access in Qt WebEngine: `navigator.clipboard.readText()` crashes (setFeaturePermission TypeError); `document.execCommand("paste")` doesn't fire native paste events. The reliable approach is a Python bridge method using `QApplication.clipboard().text()` / `.setText()`. Copy/Cut via `document.execCommand("copy"/"cut")` work fine without a bridge.
 
## Architectural Patterns

- [2026-09-22 14:20:00] Two-LLM scheme (from the template): llm1 = ReAct agent (`create_react_agent`, 6 fixed tools), llm2 = plain `ChatOpenAI` executor exposed as the zero-argument `llm2()` tool.
- [2026-09-22 14:20:00] File-based tool communication inside workspace/: `build_prompt` → `final_prompt.txt` → `llm2` reads it; variable values NEVER enter the agent context — substitution is pure Python.
- [2026-09-22 14:20:00] Agent self-comparison: the llm1 agent semantically compares llm2 output vs expected result (no deterministic comparator in Python).
- [2026-09-22 14:20:00] Versioned audit trail: `save_prompt` writes `prompt_V<n>.txt` per attempt; `_change_history` is injected into subsequent `analyze_prompt` calls to avoid repeating fixes.
- [2026-09-22 14:20:00] GUI worker-thread + state polling: one worker at a time, `threading.Lock`-guarded shared state, JS polls `get_state()` every 500 ms.
- [2026-09-22 14:20:00] HL via injected `HLBridge` (`threading.Event`): tool writes `srcprompt.txt`, notifies UI (hl_waiting=true, source_prompt filled), blocks on event until `continue_hl` delivers the response text; response parsed as `ШАБЛОН:` / `ИЗМЕНЕНИЯ:` exactly like the template.
- [2026-09-22 14:20:00] `recursion_limit = max_attempts * 10` caps total agent steps.
- [2026-09-22 14:20:00] Bridge pattern: single `Api` object exposed to JS (`window.pywebview.api`); UI never imports langchain or touches files directly.

## Testing Patterns

- [2026-09-22 14:20:00] `core/` unit tests are headless (no pywebview, no real LLM calls) — LLM calls are mocked at `LLM2Executor` / `ReActAgent` boundaries.
- [2026-09-22 14:20:00] Validation matrix tests cover each failure mode of req 3.6 individually (roles unset, unknown LLM, empty prompt, missing variable value).
- [2026-09-22 14:20:00] Integration tests (real LLM endpoints) are marked skippable when the local vLLM servers are unreachable.
- [2026-09-22 14:20:00] E2E verification is manual (tasklist Phase 10, T10.1–T10.4) against the four scenarios: automatic run, HL run, validation abort, restart persistence.
