"""core.runner — UI-independent orchestration (Phase 4: T4.3+T4.4; Phase 5: T5.4).

Phase 4 scope:

- ``resolve_role_configs(config_data) -> (llm1_cfg, llm2_cfg)`` — maps role
  names from ``config.yaml`` to concrete LLM dicts; the llm1 (agent /
  "LLM for prompts") dict carries ``max_attempts``;
- ``run_prompt_check(config_data, template, expected, variables,
  base_dir, on_event) -> str`` — the one-shot "Run prompt" pipeline
  (req 3.5): validate (req 3.6) → write run files → build final prompt →
  ``LLM2Executor.execute()`` → return the LLM result text.

Phase 5 scope (T5.4):

- ``OptimizationResult`` — dataclass returned by the optimization loop
  (``success``, ``final_template``, ``final_result``, ``attempts``);
- ``run_optimization(config_data, template, expected, variables, base_dir,
  hl, on_event, hl_bridge) -> OptimizationResult`` — the full "Start"
  pipeline: validate (req 3.6) → write run files → ``ReActAgent.run()``.

Error convention: this module raises typed exceptions
(``ValidationAbort``, ``RuntimeError``, ``ValueError``,
``FileNotFoundError``); the bridge boundary (``ui/api.py``) converts them
into ``{"ok": False, "error": "..."}`` dicts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from core.llm import LLM2Executor
from core.prompt_io import (
    build_final_prompt,
    validate_run_inputs,
    write_run_files,
)

__all__ = [
    "ValidationAbort",
    "OptimizationResult",
    "resolve_role_configs",
    "run_prompt_check",
    "run_optimization",
]


class ValidationAbort(Exception):
    """Raised when the req 3.6 validation fails before a run starts.

    ``errors`` carries the human-readable error list produced by
    :func:`core.prompt_io.validate_run_inputs`.
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__("Validation failed:\n  - " + "\n  - ".join(self.errors))


def resolve_role_configs(config_data: dict) -> tuple[dict, dict]:
    """Map role names to concrete LLM dicts.

    Parameters
    ----------
    config_data:
        The full config dict (``llms[]``, ``roles``, ``max_attempts``).

    Returns
    -------
    tuple[dict, dict]
        ``(llm1_cfg, llm2_cfg)`` where

        - ``llm1_cfg`` — the "LLM for prompts" entry (the future ReAct
          agent), with ``max_attempts`` injected;
        - ``llm2_cfg`` — the "LLM as Judge" entry (the target executor).

    Raises
    ------
    ValueError
        If a role is not set or references an unknown LLM name.
    """
    llms = config_data.get("llms") or []
    by_name: dict[str, dict] = {}
    for entry in llms:
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            by_name[entry["name"]] = entry

    roles = config_data.get("roles") or {}
    max_attempts = config_data.get("max_attempts", 30)

    def _resolve(role_key: str, label: str) -> dict:
        name = str(roles.get(role_key) or "").strip()
        if not name:
            raise ValueError(f"{label} is not selected (set the role in Settings)")
        cfg = by_name.get(name)
        if cfg is None:
            raise ValueError(f"{label} references unknown LLM '{name}'")
        return dict(cfg)

    llm1_cfg = _resolve("prompts", "LLM for prompts")
    llm1_cfg["max_attempts"] = int(max_attempts)
    llm2_cfg = _resolve("judge", "LLM as Judge")
    return llm1_cfg, llm2_cfg


def run_prompt_check(
    config_data: dict,
    template: str,
    expected: str,
    variables: dict[str, str],
    base_dir: str,
    on_event: Optional[Callable[[str], None]] = None,
) -> str:
    """One-shot "Run prompt" pipeline (req 3.5, T4.4).

    Steps:

    1. ``validate_run_inputs`` (req 3.6) — abort with ``ValidationAbort``
       (no files are written when validation fails);
    2. ``write_run_files`` — fresh ``workspace/`` with prompt.txt,
       result.txt, ``{var}.txt`` per variable;
    3. ``build_final_prompt`` — substitute variables, write
       ``final_prompt.txt``;
    4. ``LLM2Executor.execute()`` — the judge LLM runs the final prompt.

    Parameters
    ----------
    config_data:
        The full config dict (as held by ``ConfigManager``).
    template:
        The prompt template (``{{var}}`` placeholders).
    expected:
        The expected result text (written to ``result.txt``; not used by
        the judge call itself).
    variables:
        Mapping of variable name → value.
    base_dir:
        The workspace directory (per-run file sandbox).
    on_event:
        Optional log callback; receives step strings.

    Returns
    -------
    str
        The raw LLM result text.

    Raises
    ------
    ValidationAbort
        Validation 3.6 failed (``.errors`` holds the messages).
    ValueError
        API key missing/placeholder, or roles not resolvable.
    FileNotFoundError
        ``final_prompt.txt`` missing (cannot happen: build precedes execute).
    RuntimeError
        The LLM call failed.
    """
    def _emit(msg: str) -> None:
        if on_event:
            try:
                on_event(msg)
            except Exception:
                pass  # logging must never break the run

    # 1. Validation (req 3.6) — no files written on failure.
    errors = validate_run_inputs(config_data, template, variables)
    if errors:
        raise ValidationAbort(errors)
    _emit("Validation passed")

    # 2. Write the run files (fresh workspace).
    written = write_run_files(base_dir, template, expected, variables)
    _emit(f"Workspace prepared: {len(written)} files written to {base_dir}")

    # 3. Build the final prompt (substitution + final_prompt.txt).
    build_final_prompt(template, base_dir)
    _emit("Final prompt built (final_prompt.txt)")

    # 4. Execute on the judge LLM (llm2).
    _, llm2_cfg = resolve_role_configs(config_data)
    llm2_cfg["base_dir"] = base_dir
    _emit("Running prompt on LLM as Judge …")
    result = LLM2Executor(llm2_cfg).execute()
    _emit(f"LLM result received ({len(result)} chars)")
    return result


# ============================================================
# Phase 5 (T5.4) — Optimization loop
# ============================================================


@dataclass
class OptimizationResult:
    """Result of the ReAct optimization loop (architecture §3.5).

    Attributes
    ----------
    success:
        True if the agent called ``finish`` (self-comparison passed).
    final_template:
        The best / final prompt template (``{{var}}`` placeholders).
    final_result:
        The raw LLM result obtained from the final prompt execution.
    attempts:
        Number of optimization iterations performed.
    """

    success: bool
    final_template: str
    final_result: str
    attempts: int


def run_optimization(
    config_data: dict,
    template: str,
    expected: str,
    variables: dict[str, str],
    base_dir: str,
    hl: bool,
    on_event: Optional[Callable[[str], None]] = None,
    hl_bridge: Optional["HLBridge"] = None,
) -> OptimizationResult:
    """Full "Start" optimization pipeline (Phase 5, T5.4).

    Steps:

    1. ``validate_run_inputs`` (req 3.6) — abort with ``ValidationAbort``
       (no files are written when validation fails);
    2. ``write_run_files`` — fresh ``workspace/`` with prompt.txt,
       result.txt, ``{var}.txt`` per variable;
    3. ``ReActAgent.run()`` — the langchain/langgraph ReAct optimization
       loop (build_prompt → llm2 → explain → analyze → save → repeat).

    Parameters
    ----------
    config_data:
        The full config dict (as held by ``ConfigManager``).
    template:
        The prompt template (``{{var}}`` placeholders).
    expected:
        The expected result text (written to ``result.txt``).
    variables:
        Mapping of variable name → value.
    base_dir:
        The workspace directory (per-run file sandbox).
    hl:
        Human-in-the-loop mode (bool).
    on_event:
        Optional log callback; receives step strings.
    hl_bridge:
        Optional HLBridge instance; required when ``hl`` is True.

    Returns
    -------
    OptimizationResult
        ``success``, ``final_template``, ``final_result``, ``attempts``.

    Raises
    ------
    ValidationAbort
        Validation 3.6 failed (``.errors`` holds the messages).
    ValueError
        API key missing/placeholder, or roles not resolvable.
    RuntimeError
        The LLM call failed, or HL mode was enabled without a bridge.
    """
    from core.agent import HLBridge, ReActAgent  # lazy: langchain boundary

    def _emit(msg: str) -> None:
        if on_event:
            try:
                on_event(msg)
            except Exception:
                pass  # logging must never break the run

    # 1. Validation (req 3.6) — no files written on failure.
    errors = validate_run_inputs(config_data, template, variables)
    if errors:
        raise ValidationAbort(errors)
    _emit("Validation passed")

    # 2. Write the run files (fresh workspace).
    written = write_run_files(base_dir, template, expected, variables)
    _emit(f"Workspace prepared: {len(written)} files written to {base_dir}")

    # 3. Resolve role configs and build the executor + agent.
    llm1_cfg, llm2_cfg = resolve_role_configs(config_data)
    llm2_cfg["base_dir"] = base_dir
    llm2_executor = LLM2Executor(llm2_cfg)
    _emit(f"Agent configured (max_attempts={llm1_cfg.get('max_attempts', 30)})")

    agent = ReActAgent(
        llm2_executor=llm2_executor,
        llm1_config=llm1_cfg,
        max_attempts=llm1_cfg.get("max_attempts", 30),
        hl=hl,
        on_event=on_event,
        hl_bridge=hl_bridge,
    )
    output_dir = base_dir  # versioned templates saved in the workspace

    # 4. Run the optimization loop.
    _emit("Starting optimization loop …")
    result = agent.run(
        prompt_template=template,
        expected_result=expected,
        base_dir=base_dir,
        output_dir=output_dir,
    )

    return OptimizationResult(
        success=result["success"],
        final_template=result["final_template"],
        final_result=result["final_result"],
        attempts=result["attempts"],
    )
