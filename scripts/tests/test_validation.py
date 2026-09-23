#!/usr/bin/env python3
"""tests.test_validation — unit tests for validate_run_inputs (T3.9).

Full req 3.6 matrix: each failure mode individually + the all-valid case.

The function signature is:
    validate_run_inputs(config_data: dict, template: str, variables: dict[str, str]) -> list[str]

Failure modes covered:
  1. judge role not selected
  2. prompts role not selected
  3. both roles not selected
  4. judge references unknown LLM
  5. prompts references unknown LLM
  6. prompt is empty
  7. prompt is whitespace-only
  8. a {{var}} has no value set
  9. a {{var}} value is empty (whitespace-only)
 10. multiple errors accumulate
 11. all-valid case → empty list
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.prompt_io import validate_run_inputs


def make_config(
    llms=None,
    judge="",
    prompts="",
):
    """Build a minimal config dict for validation tests."""
    if llms is None:
        llms = [
            {"name": "alpha", "api_base": "http://a", "api_key": "k", "model": "m", "temperature": 0.1},
            {"name": "beta", "api_base": "http://b", "api_key": "k", "model": "m", "temperature": 0.2},
        ]
    return {
        "llms": llms,
        "roles": {"judge": judge, "prompts": prompts},
        "hl": False,
        "max_attempts": 30,
    }


# ───────────────────────── Roles validation ─────────────────────────


class TestRolesValidation:
    def test_all_valid_roles(self):
        cfg = make_config(judge="alpha", prompts="beta")
        errors = validate_run_inputs(cfg, "hello {{x}}", {"x": "val"})
        assert errors == []

    def test_judge_not_selected(self):
        cfg = make_config(judge="", prompts="beta")
        errors = validate_run_inputs(cfg, "hello", {})
        assert any("LLM as Judge" in e and "not selected" in e for e in errors)

    def test_prompts_not_selected(self):
        cfg = make_config(judge="alpha", prompts="")
        errors = validate_run_inputs(cfg, "hello", {})
        assert any("LLM for prompts" in e and "not selected" in e for e in errors)

    def test_both_roles_not_selected(self):
        cfg = make_config(judge="", prompts="")
        errors = validate_run_inputs(cfg, "hello", {})
        assert any("LLM as Judge" in e for e in errors)
        assert any("LLM for prompts" in e for e in errors)
        assert len([e for e in errors if "not selected" in e]) == 2

    def test_judge_references_unknown_llm(self):
        cfg = make_config(judge="nonexistent", prompts="beta")
        errors = validate_run_inputs(cfg, "hello", {})
        assert any("unknown LLM" in e and "nonexistent" in e for e in errors)

    def test_prompts_references_unknown_llm(self):
        cfg = make_config(judge="alpha", prompts="ghost")
        errors = validate_run_inputs(cfg, "hello", {})
        assert any("unknown LLM" in e and "ghost" in e for e in errors)

    def test_empty_llms_list_with_roles_set(self):
        """Roles point to LLM names but the LLM list is empty → both unknown."""
        cfg = make_config(llms=[], judge="alpha", prompts="beta")
        errors = validate_run_inputs(cfg, "hello", {})
        assert any("unknown LLM 'alpha'" in e for e in errors)
        assert any("unknown LLM 'beta'" in e for e in errors)


# ───────────────────────── Prompt validation ─────────────────────────


class TestPromptValidation:
    def _valid_config(self):
        return make_config(judge="alpha", prompts="beta")

    def test_prompt_empty_string(self):
        cfg = self._valid_config()
        errors = validate_run_inputs(cfg, "", {})
        assert any("Prompt is empty" in e for e in errors)

    def test_prompt_whitespace_only(self):
        cfg = self._valid_config()
        errors = validate_run_inputs(cfg, "   \n\t  ", {})
        assert any("Prompt is empty" in e for e in errors)

    def test_prompt_non_empty_no_vars(self):
        cfg = self._valid_config()
        errors = validate_run_inputs(cfg, "just a plain prompt", {})
        assert errors == []


# ───────────────────────── Variable validation ─────────────────────────


class TestVariableValidation:
    def _valid_config(self):
        return make_config(judge="alpha", prompts="beta")

    def test_all_vars_present(self):
        cfg = self._valid_config()
        errors = validate_run_inputs(cfg, "Hi {{a}} {{b}}", {"a": "1", "b": "2"})
        assert errors == []

    def test_missing_variable(self):
        cfg = self._valid_config()
        errors = validate_run_inputs(cfg, "Hi {{a}} {{b}}", {"a": "1"})
        assert any("{{b}}" in e and "no value set" in e for e in errors)

    def test_empty_variable_value(self):
        cfg = self._valid_config()
        errors = validate_run_inputs(cfg, "Hi {{a}}", {"a": ""})
        assert any("{{a}}" in e and "value is empty" in e for e in errors)

    def test_whitespace_variable_value(self):
        cfg = self._valid_config()
        errors = validate_run_inputs(cfg, "Hi {{a}}", {"a": "   "})
        assert any("{{a}}" in e and "value is empty" in e for e in errors)

    def test_multiple_missing_vars(self):
        cfg = self._valid_config()
        errors = validate_run_inputs(cfg, "{{a}} {{b}} {{c}}", {})
        missing = [e for e in errors if "no value set" in e]
        assert len(missing) == 3

    def test_repeated_var_only_checked_once(self):
        """A variable that appears twice in the prompt is reported at most once
        per error type (deduplication in extract_variables_from_prompt)."""
        cfg = self._valid_config()
        errors = validate_run_inputs(cfg, "{{a}} and {{a}} again", {"a": "x"})
        assert errors == []


# ───────────────────────── Combined / multiple errors ─────────────────────────


class TestCombinedErrors:
    def test_all_failure_modes_together(self):
        cfg = make_config(judge="", prompts="")
        errors = validate_run_inputs(cfg, "", {"a": ""})
        # Roles (2) + empty prompt (1) = 3 errors (var check skipped when prompt empty)
        role_errors = [e for e in errors if "not selected" in e]
        prompt_errors = [e for e in errors if "Prompt is empty" in e]
        assert len(role_errors) == 2
        assert len(prompt_errors) == 1
        assert len(errors) == 3

    def test_roles_and_vars_both_fail(self):
        cfg = make_config(judge="", prompts="beta")
        template = "Hi {{x}}"
        errors = validate_run_inputs(cfg, template, {})
        assert any("LLM as Judge" in e for e in errors)
        assert any("{{x}}" in e for e in errors)

    def test_full_matrix_all_valid(self):
        cfg = make_config(judge="alpha", prompts="beta")
        template = "Tell me about {{city}} on {{date}}"
        variables = {"city": "Moscow", "date": "2025-01-01"}
        errors = validate_run_inputs(cfg, template, variables)
        assert errors == []

    def test_unicode_variables_valid(self):
        cfg = make_config(judge="alpha", prompts="beta")
        template = "Привет {{город}}"
        errors = validate_run_inputs(cfg, template, {"город": "Москва"})
        assert errors == []
