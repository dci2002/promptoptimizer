#!/usr/bin/env python3
"""tests.test_llm — unit tests for core.llm (T4.7).

Covers:
  - LLM2Executor.execute: API-key validation, missing final_prompt.txt,
    successful call (ChatOpenAI mocked at the boundary), reasoning_effort
    always None, LLM failure wrapped in RuntimeError.
  - make_llm1: API-key validation + factory returns a ChatOpenAI.
  - SYSTEM_MESSAGE constant.

langchain is never actually used here: the ChatOpenAI symbol is monkeypatched
into the langchain_openai module before it is imported lazily.
"""

import os
import sys
import types
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.llm import LLM2Executor, SYSTEM_MESSAGE, make_llm1


def _install_mock_langchain(monkeypatch, invoke_result="LLM-ANSWER"):
    """Install fake langchain_openai / langchain_core modules and return the
    mock classes so tests can assert on construction and invoke calls."""
    fake_chat = mock.Mock(name="ChatOpenAI")
    fake_chat.return_value = mock.Mock(name="chat_instance")
    fake_chat.return_value.invoke.return_value = mock.Mock(name="response")
    fake_chat.return_value.invoke.return_value.content = invoke_result

    fake_openai_mod = types.ModuleType("langchain_openai")
    fake_openai_mod.ChatOpenAI = fake_chat

    class _Msg:
        def __init__(self, content):
            self.content = content

    fake_messages_mod = types.ModuleType("langchain_core.messages")
    fake_messages_mod.SystemMessage = type("SystemMessage", (_Msg,), {})
    fake_messages_mod.HumanMessage = type("HumanMessage", (_Msg,), {})
    fake_core_mod = types.ModuleType("langchain_core")
    fake_core_mod.messages = fake_messages_mod

    monkeypatch.setitem(sys.modules, "langchain_openai", fake_openai_mod)
    monkeypatch.setitem(sys.modules, "langchain_core", fake_core_mod)
    monkeypatch.setitem(sys.modules, "langchain_core.messages", fake_messages_mod)
    return fake_chat


def _valid_llm2_config(base_dir: str) -> dict:
    return {
        "api_base": "http://localhost:1234/v1",
        "api_key": "sk-test",
        "model": "gemma-target",
        "temperature": 0.0,
        "base_dir": base_dir,
    }


class TestSystemMessage:
    def test_constant_contains_directive(self):
        assert "without showing your reasoning" in SYSTEM_MESSAGE


class TestLLM2ExecutorApiKeyIdKey:
    def test_empty_key_raises(self, tmp_path):
        cfg = _valid_llm2_config(str(tmp_path))
        cfg["api_key"] = ""
        executor = LLM2Executor(cfg)
        with pytest.raises(ValueError, match="API key"):
            executor.execute()

    def test_placeholder_key_raises(self, tmp_path):
        cfg = _valid_llm2_config(str(tmp_path))
        cfg["api_key"] = "your-api-key-here"
        executor = LLM2Executor(cfg)
        with pytest.raises(ValueError, match="API key"):
            executor.execute()


class TestLLM2ExecutorMissingPrompt:
    def test_missing_final_prompt_raises(self, tmp_path, monkeypatch):
        _install_mock_langchain(monkeypatch)
        cfg = _valid_llm2_config(str(tmp_path))
        executor = LLM2Executor(cfg)
        with pytest.raises(FileNotFoundError, match="final_prompt.txt"):
            executor.execute()


class TestLLM2ExecutorExecute:
    def test_success_returns_content_and_uses_reasoning_none(
        self, tmp_path, monkeypatch
    ):
        fake_chat = _install_mock_langchain(monkeypatch, invoke_result="42")
        cfg = _valid_llm2_config(str(tmp_path))
        (tmp_path / "final_prompt.txt").write_text("final prompt text", encoding="utf-8")

        result = LLM2Executor(cfg).execute()

        assert result == "42"
        fake_chat.assert_called_once()
        kwargs = fake_chat.call_args.kwargs
        assert kwargs["reasoning_effort"] is None
        assert kwargs["openai_api_key"] == "sk-test"
        assert kwargs["openai_api_base"] == "http://localhost:1234/v1"
        assert kwargs["model"] == "gemma-target"
        # invoke was called with [System, Human]
        messages = fake_chat.return_value.invoke.call_args.args[0]
        assert len(messages) == 2
        assert messages[0].content == SYSTEM_MESSAGE
        assert messages[1].content == "final prompt text"

    def test_llm_failure_wrapped_in_runtime_error(self, tmp_path, monkeypatch):
        fake_chat = _install_mock_langchain(monkeypatch)
        fake_chat.return_value.invoke.side_effect = ConnectionError("boom")
        cfg = _valid_llm2_config(str(tmp_path))
        (tmp_path / "final_prompt.txt").write_text("x", encoding="utf-8")

        with pytest.raises(RuntimeError, match="LLM call failed"):
            LLM2Executor(cfg).execute()

    def test_missing_langchain_import(self, tmp_path, monkeypatch):
        # No mock installed -> the lazy import raises ImportError.
        monkeypatch.setitem(sys.modules, "langchain_openai", None)
        cfg = _valid_llm2_config(str(tmp_path))
        (tmp_path / "final_prompt.txt").write_text("x", encoding="utf-8")
        with pytest.raises(ImportError, match="langchain"):
            LLM2Executor(cfg).execute()


class TestMakeLlm1:
    def test_empty_key_raises(self, monkeypatch):
        _install_mock_langchain(monkeypatch)
        with pytest.raises(ValueError, match="API key"):
            make_llm1({"api_key": ""})

    def test_placeholder_key_raises(self, monkeypatch):
        _install_mock_langchain(monkeypatch)
        with pytest.raises(ValueError, match="API key"):
            make_llm1({"api_key": "your-api-key-here"})

    def test_returns_chatopenai_with_reasoning_none(self, monkeypatch):
        fake_chat = _install_mock_langchain(monkeypatch)
        llm = make_llm1(
            {
                "api_base": "http://x/v1",
                "api_key": "sk-agent",
                "model": "gemma-target",
                "temperature": 0.1,
            }
        )
        assert llm is fake_chat.return_value
        kwargs = fake_chat.call_args.kwargs
        assert kwargs["reasoning_effort"] is None
        assert kwargs["openai_api_key"] == "sk-agent"
        assert kwargs["model"] == "gemma-target"
