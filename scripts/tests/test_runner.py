#!/usr/bin/env python3
"""tests.test_runner — unit tests for core.runner (T4.7).

Covers:
  - resolve_role_configs: correct mapping, max_attempts injection,
    missing-role / unknown-llm errors.
  - run_prompt_check: validation abort (no files written), full success
    pipeline (LLM2Executor mocked at the boundary), event emission,
    LLM error propagation.

The real LLM is never called: LLM2Executor is monkeypatched in core.runner.
"""

import os
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import core.runner as runner
from core.runner import (
    ValidationAbort,
    resolve_role_configs,
    run_prompt_check,
)


def _valid_config() -> dict:
    return {
        "llms": [
            {
                "name": "judge-llm",
                "api_base": "http://localhost:1234/v1",
                "api_key": "sk-judge",
                "model": "judge-model",
                "temperature": 0.0,
            },
            {
                "name": "prompt-llm",
                "api_base": "http://localhost:1235/v1",
                "api_key": "sk-prompt",
                "model": "prompt-model",
                "temperature": 0.1,
            },
        ],
        "roles": {"judge": "judge-llm", "prompts": "prompt-llm"},
        "hl": False,
        "max_attempts": 7,
    }


class TestResolveRoleConfigs:
    def test_maps_and_injects_max_attempts(self):
        llm1, llm2 = resolve_role_configs(_valid_config())
        assert llm1["name"] == "prompt-llm"
        assert llm1["max_attempts"] == 7
        assert llm2["name"] == "judge-llm"
        assert "max_attempts" not in llm2

    def test_returns_copies_not_references(self):
        cfg = _valid_config()
        llm1, llm2 = resolve_role_configs(cfg)
        llm1["model"] = "mutated"
        assert cfg["llms"][1]["model"] == "prompt-model"
        assert llm2["model"] == "judge-model"

    def test_missing_judge_role(self):
        cfg = _valid_config()
        cfg["roles"]["judge"] = ""
        with pytest.raises(ValueError, match="LLM as Judge"):
            resolve_role_configs(cfg)

    def test_missing_prompts_role(self):
        cfg = _valid_config()
        cfg["roles"]["prompts"] = ""
        with pytest.raises(ValueError, match="LLM for prompts"):
            resolve_role_configs(cfg)

    def test_unknown_llm_reference(self):
        cfg = _valid_config()
        cfg["roles"]["judge"] = "does-not-exist"
        with pytest.raises(ValueError, match="unknown LLM"):
            resolve_role_configs(cfg)


class TestRunPromptCheckValidation:
    def test_validation_abort_raises_and_writes_nothing(self, tmp_path):
        cfg = _valid_config()
        # Empty prompt -> validation error.
        with pytest.raises(ValidationAbort):
            run_prompt_check(cfg, "", "expected", {}, str(tmp_path))
        # Workspace must not have been created/used.
        assert not (tmp_path / "final_prompt.txt").exists()
        assert not (tmp_path / "prompt.txt").exists()

    def test_validation_abort_carries_errors(self, tmp_path):
        cfg = _valid_config()
        with pytest.raises(ValidationAbort) as exc_info:
            run_prompt_check(cfg, "Hello {{name}}", "exp", {}, str(tmp_path))
        assert any("{{name}}" in e for e in exc_info.value.errors)


class TestRunPromptCheckSuccess:
    def test_full_pipeline_and_events(self, tmp_path, monkeypatch):
        mock_exec = mock.Mock(name="LLM2Executor")
        mock_exec.return_value.execute.return_value = "the-llm-result"
        monkeypatch.setattr(runner, "LLM2Executor", mock_exec)

        cfg = _valid_config()
        events: list[str] = []
        result = run_prompt_check(
            cfg,
            "Hello {{name}}",
            "expected result",
            {"name": "Ada"},
            str(tmp_path),
            on_event=events.append,
        )

        assert result == "the-llm-result"
        # Run files + final prompt exist.
        assert (tmp_path / "prompt.txt").exists()
        assert (tmp_path / "result.txt").exists()
        assert (tmp_path / "name.txt").exists()
        final_prompt = (tmp_path / "final_prompt.txt").read_text(encoding="utf-8")
        assert final_prompt == "Hello Ada"
        # Executor got the judge LLM config with base_dir injected.
        passed_cfg = mock_exec.call_args.args[0]
        assert passed_cfg["name"] == "judge-llm"
        assert passed_cfg["base_dir"] == str(tmp_path)
        # Events were emitted.
        assert events
        assert any("final_prompt.txt" in e for e in events)

    def test_no_events_without_callback(self, tmp_path, monkeypatch):
        mock_exec = mock.Mock(name="LLM2Executor")
        mock_exec.return_value.execute.return_value = "ok"
        monkeypatch.setattr(runner, "LLM2Executor", mock_exec)
        assert run_prompt_check(
            _valid_config(), "plain", "exp", {}, str(tmp_path)
        ) == "ok"


class TestRunPromptCheckLlmError:
    def test_runtime_error_propagates(self, tmp_path, monkeypatch):
        mock_exec = mock.Mock(name="LLM2Executor")
        mock_exec.return_value.execute.side_effect = RuntimeError("LLM call failed: x")
        monkeypatch.setattr(runner, "LLM2Executor", mock_exec)
        with pytest.raises(RuntimeError, match="LLM call failed"):
            run_prompt_check(_valid_config(), "plain", "exp", {}, str(tmp_path))

    def test_value_error_propagates(self, tmp_path, monkeypatch):
        mock_exec = mock.Mock(name="LLM2Executor")
        mock_exec.return_value.execute.side_effect = ValueError("API key is not configured")
        monkeypatch.setattr(runner, "LLM2Executor", mock_exec)
        with pytest.raises(ValueError, match="API key"):
            run_prompt_check(_valid_config(), "plain", "exp", {}, str(tmp_path))
