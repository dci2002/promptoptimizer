#!/usr/bin/env python3
"""tests.test_runner — unit tests for core.runner (T4.7 + T5.4).

Covers:
  - resolve_role_configs: correct mapping, max_attempts injection,
    missing-role / unknown-llm errors.
  - run_prompt_check: validation abort (no files written), full success
    pipeline (LLM2Executor mocked at the boundary), event emission,
    LLM error propagation.
  - run_optimization (Phase 5): validation abort (no files written), full
    success pipeline (ReActAgent + LLM2Executor mocked at the boundary),
    OptimizationResult shape, HL-bridge forwarding, LLM error propagation.

The real LLM is never called: LLM2Executor / ReActAgent are monkeypatched
in core.runner.
"""

import os
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import core.runner as runner
from core.runner import (
    OptimizationResult,
    ValidationAbort,
    resolve_role_configs,
    run_optimization,
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


# ============================================================
# Phase 5 (T5.4) — run_optimization
# ============================================================


class TestRunOptimizationValidation:
    def test_validation_abort_raises_and_writes_nothing(self, tmp_path):
        cfg = _valid_config()
        # Empty prompt -> validation error (req 3.6).
        with pytest.raises(ValidationAbort):
            run_optimization(cfg, "", "expected", {}, str(tmp_path), hl=False)
        assert not (tmp_path / "prompt.txt").exists()
        assert not (tmp_path / "final_prompt.txt").exists()

    def test_validation_abort_carries_errors(self, tmp_path):
        cfg = _valid_config()
        with pytest.raises(ValidationAbort) as exc_info:
            run_optimization(cfg, "Hello {{name}}", "exp", {}, str(tmp_path), hl=False)
        assert any("{{name}}" in e for e in exc_info.value.errors)


class TestRunOptimizationSuccess:
    def test_full_pipeline_and_result(self, tmp_path, monkeypatch):
        mock_exec = mock.Mock(name="LLM2Executor")
        monkeypatch.setattr(runner, "LLM2Executor", mock_exec)

        mock_agent_cls = mock.Mock(name="ReActAgent")
        mock_agent = mock.Mock(name="agent")
        mock_agent.run.return_value = {
            "success": True,
            "attempts": 1,
            "final_result": "the-final-result",
            "final_prompt_file": str(tmp_path / "final_prompt.txt"),
            "final_template": "final {{name}}",
            "history": [{"attempt": 1}],
        }
        mock_agent_cls.return_value = mock_agent
        # runner does a lazy `from core.agent import ReActAgent`; patch that module.
        import core.agent as agent_mod
        monkeypatch.setattr(agent_mod, "ReActAgent", mock_agent_cls)

        cfg = _valid_config()
        events: list[str] = []
        result = run_optimization(
            cfg,
            "Hello {{name}}",
            "expected result",
            {"name": "Ada"},
            str(tmp_path),
            hl=False,
            on_event=events.append,
        )

        # OptimizationResult dataclass with the four fields.
        assert isinstance(result, OptimizationResult)
        assert result.success is True
        assert result.final_template == "final {{name}}"
        assert result.final_result == "the-final-result"
        assert result.attempts == 1

        # Run files were written to the workspace.
        assert (tmp_path / "prompt.txt").exists()
        assert (tmp_path / "result.txt").exists()
        assert (tmp_path / "name.txt").exists()

        # The executor got the judge LLM config with base_dir injected.
        passed_cfg = mock_exec.call_args.args[0]
        assert passed_cfg["name"] == "judge-llm"
        assert passed_cfg["base_dir"] == str(tmp_path)

        # The agent was built with the prompt LLM config + max_attempts + hl.
        agent_kwargs = mock_agent_cls.call_args.kwargs
        assert agent_kwargs["llm1_config"]["name"] == "prompt-llm"
        assert agent_kwargs["llm1_config"]["max_attempts"] == 7
        assert agent_kwargs["hl"] is False
        assert callable(agent_kwargs["on_event"])

        # run() got the right args.
        run_kwargs = mock_agent.run.call_args.kwargs
        assert run_kwargs["prompt_template"] == "Hello {{name}}"
        assert run_kwargs["expected_result"] == "expected result"
        assert run_kwargs["base_dir"] == str(tmp_path)

        # Events were emitted through the callback.
        assert events
        assert any("Validation passed" in e for e in events)
        assert any("optimization loop" in e for e in events)

    def test_hl_flag_and_bridge_forwarded(self, tmp_path, monkeypatch):
        monkeypatch.setattr(runner, "LLM2Executor", mock.Mock(name="LLM2Executor"))
        mock_agent_cls = mock.Mock(name="ReActAgent")
        mock_agent_cls.return_value.run.return_value = {
            "success": False,
            "attempts": 1,
            "final_result": "",
            "final_prompt_file": str(tmp_path / "final_prompt.txt"),
            "final_template": "t",
            "history": [],
        }
        import core.agent as agent_mod
        monkeypatch.setattr(agent_mod, "ReActAgent", mock_agent_cls)

        bridge = mock.Mock(name="hl_bridge")
        run_optimization(
            _valid_config(), "plain", "exp", {}, str(tmp_path),
            hl=True, hl_bridge=bridge,
        )
        kwargs = mock_agent_cls.call_args.kwargs
        assert kwargs["hl"] is True
        assert kwargs["hl_bridge"] is bridge


class TestRunOptimizationLlmError:
    def test_runtime_error_propagates(self, tmp_path, monkeypatch):
        monkeypatch.setattr(runner, "LLM2Executor", mock.Mock(name="LLM2Executor"))
        mock_agent_cls = mock.Mock(name="ReActAgent")
        mock_agent_cls.return_value.run.side_effect = RuntimeError("LLM call failed: x")
        import core.agent as agent_mod
        monkeypatch.setattr(agent_mod, "ReActAgent", mock_agent_cls)
        with pytest.raises(RuntimeError, match="LLM call failed"):
            run_optimization(_valid_config(), "plain", "exp", {}, str(tmp_path), hl=False)

    def test_value_error_propagates(self, tmp_path, monkeypatch):
        monkeypatch.setattr(runner, "LLM2Executor", mock.Mock(name="LLM2Executor"))
        mock_agent_cls = mock.Mock(name="ReActAgent")
        mock_agent_cls.return_value.run.side_effect = ValueError("API key is not configured")
        import core.agent as agent_mod
        monkeypatch.setattr(agent_mod, "ReActAgent", mock_agent_cls)
        with pytest.raises(ValueError, match="API key"):
            run_optimization(_valid_config(), "plain", "exp", {}, str(tmp_path), hl=False)
