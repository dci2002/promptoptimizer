"""core.prompt_io — Prompt I/O: variable extraction, file writing, substitution.

Ported from the template (``scripts/template/interview_copilot.py``) and
decoupled from any class (architecture §3.2).

Functions:

- ``extract_variables_from_prompt(text) -> list[str]`` — regex
  ``\\{\\{(\\w+)\\}\\}``, deduplicated, insertion order preserved;
- ``write_run_files(base_dir, template, expected_result, variables)`` —
  creates a fresh ``workspace/`` directory with ``prompt.txt``,
  ``result.txt`` and one ``{var}.txt`` per variable;
- ``build_final_prompt(template, base_dir) -> str`` — substitutes variables
  from files, writes ``final_prompt.txt``, returns the final prompt string;
- ``load_expected_result(base_dir) -> str``;
- ``validate_run_inputs(config_data, template, variables) -> list[str]`` —
  the req 3.6 validation matrix (architecture §3.2, T3.2).

This module has **no** pywebview imports — it runs headless and is
unit-tested independently of the UI.
"""

from __future__ import annotations

import os
import re
import shutil

__all__ = [
    "extract_variables_from_prompt",
    "write_run_files",
    "build_final_prompt",
    "load_expected_result",
    "validate_run_inputs",
]

#: Strict placeholder regex — same as the template.
_VAR_RE = re.compile(r"\{\{(\w+)\}\}")


# ─────────────────────────── Variable extraction ───────────────────────────


def extract_variables_from_prompt(text: str) -> list[str]:
    """Extract variable names from a prompt template.

    Variables are in the ``{{variable_name}}`` format. The returned list is
    deduplicated and preserves first-occurrence order.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for match in _VAR_RE.finditer(text):
        name = match.group(1)
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


# ─────────────────────────── File I/O ───────────────────────────


def write_run_files(
    base_dir: str,
    template: str,
    expected_result: str,
    variables: dict[str, str],
) -> list[str]:
    """Create a fresh run directory and write all source files.

    Parameters
    ----------
    base_dir:
        Target directory (e.g. ``workspace/``). Deleted and recreated if it
        already exists (per-run isolation, architecture §3.1 decision).
    template:
        The prompt template with ``{{var}}`` placeholders.
    expected_result:
        The expected result text (written to ``result.txt``).
    variables:
        Mapping of variable name → value. Each value is written to
        ``{name}.txt``.

    Returns
    -------
    list[str]
        Paths of all files written (for logging / testing).
    """
    # Fresh directory per run (per-run isolation).
    if os.path.isdir(base_dir):
        shutil.rmtree(base_dir)
    os.makedirs(base_dir, exist_ok=True)

    written: list[str] = []

    # prompt.txt — the template as-is (with placeholders).
    prompt_path = os.path.join(base_dir, "prompt.txt")
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(template)
    written.append(prompt_path)

    # result.txt — the expected result.
    result_path = os.path.join(base_dir, "result.txt")
    with open(result_path, "w", encoding="utf-8") as f:
        f.write(expected_result)
    written.append(result_path)

    # {var}.txt — one file per variable.
    for name, value in variables.items():
        var_path = os.path.join(base_dir, f"{name}.txt")
        with open(var_path, "w", encoding="utf-8") as f:
            f.write(value)
        written.append(var_path)

    return written


def build_final_prompt(template: str, base_dir: str) -> str:
    """Substitute variables from ``{var}.txt`` files into the template.

    Reads each variable's value from ``base_dir/{var}.txt``. If the file is
    missing, an empty string is used (with a warning printed to stderr).
    The fully-substituted prompt is written to ``base_dir/final_prompt.txt``.

    Returns the final prompt string (not the file path — callers in the GUI
    need the text, not the path).
    """
    import sys

    variables = extract_variables_from_prompt(template)
    final_prompt = template
    for var in variables:
        file_path = os.path.join(base_dir, f"{var}.txt")
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                value = f.read()
        else:
            value = ""
            print(f"[WARNING] Variable file not found: {file_path}", file=sys.stderr)
        final_prompt = final_prompt.replace("{{" + var + "}}", value)

    # Save the fully-substituted prompt for the LLM2 executor to read.
    final_prompt_file = os.path.join(base_dir, "final_prompt.txt")
    with open(final_prompt_file, "w", encoding="utf-8") as f:
        f.write(final_prompt)

    return final_prompt


def load_expected_result(base_dir: str) -> str:
    """Read the expected result from ``base_dir/result.txt`` (stripped)."""
    result_path = os.path.join(base_dir, "result.txt")
    with open(result_path, "r", encoding="utf-8") as f:
        return f.read().strip()


# ─────────────────────────── Validation (req 3.6) ───────────────────────────


def validate_run_inputs(
    config_data: dict,
    template: str,
    variables: dict[str, str],
) -> list[str]:
    """Validate that the settings are filled in for running the prompt.

    Implements the req 3.6 check matrix:

    1. LLM as Judge and LLM for prompts are selected and the corresponding
       LLM entries exist in the config;
    2. The prompt text is non-empty;
    3. Every ``{{var}}`` placeholder in the prompt has a corresponding
       entry in ``variables`` with a non-empty value.

    Parameters
    ----------
    config_data:
        The full config dict (as loaded by :class:`~core.config.ConfigManager`).
    template:
        The prompt template text.
    variables:
        Mapping of variable name → value (from the UI variables table).

    Returns
    -------
    list[str]
        Human-readable error messages. Empty list == valid.
    """
    errors: list[str] = []

    # --- 1. Roles selected + LLMs exist ---
    llms = config_data.get("llms") or []
    llm_names = {
        entry.get("name", "") for entry in llms if isinstance(entry, dict)
    }
    roles = config_data.get("roles") or {}

    for role_key, label in (("judge", "LLM as Judge"), ("prompts", "LLM for prompts")):
        role_value = roles.get(role_key, "") or ""
        if not role_value:
            errors.append(f"{label} is not selected")
        elif role_value not in llm_names:
            errors.append(f"{label} references unknown LLM '{role_value}'")

    # --- 2. Prompt non-empty ---
    if not template or not template.strip():
        errors.append("Prompt is empty")
    else:
        # --- 3. Every {{var}} has a non-empty value ---
        required_vars = extract_variables_from_prompt(template)
        for var in required_vars:
            if var not in variables:
                errors.append(f"Variable '{{{{{var}}}}}' has no value set")
            elif not str(variables[var]).strip():
                errors.append(f"Variable '{{{{{var}}}}}' value is empty")

    return errors
