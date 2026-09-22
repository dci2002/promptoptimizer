"""core — reusable, UI-independent logic.

Modules:
    config     — ConfigManager: load/save/validate config.yaml
    prompt_io  — write prompt.txt / result.txt / {var}.txt, extract variables, build final prompt
    llm        — LLM2Executor + factory for llm1 ChatOpenAI
    agent      — ReActAgent (6 tools) + HL bridge hooks
    runner     — run_prompt_check, run_optimization, event/log callbacks, HL state machine

This package must remain free of any pywebview imports so it can run headless
and be unit-tested independently of the UI.
"""

__all__ = [
    "config",
    "prompt_io",
    "llm",
    "agent",
    "runner",
]
