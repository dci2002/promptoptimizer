#!/usr/bin/env python3
"""tests.test_agent — unit tests for core.agent (Phase 5: T5.1–T5.3 + T5.7).

Covers:
  - ReActAgent construction / defaults (no LLM is created at init).
  - _emit: forwards to on_event; a raising callback never breaks the run.
  - _save_template: versioned prompt_V{n}.txt writing + counter increment.
  - HL mode: analyze_prompt requires an injected hl_bridge (RuntimeError).
  - _make_tools: build_prompt / llm2 / save_prompt / finish tool behavior
    (langchain @tool boundary faked with a plain wrapper class).
  - run(): full headless loop with a mocked langgraph agent — the agent
    calls `finish`, success=True, events non-empty, final prompt built.

The real LLM is never called: make_llm1 / langchain are monkeypatched, and
the langgraph `create_react_agent` boundary is faked via a stub module.
"""

import os
import sys
import types
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import core.agent as agent_mod
from core.agent import HLBridge, ReActAgent


def _llm1_config() -> dict:
    return {
        "name": "prompt-llm",
        "api_base": "http://localhost:1235/v1",
        "api_key": "sk-prompt",
        "model": "prompt-model",
        "temperature": 0.1,
        "max_attempts": 7,
    }


def _llm2_exec(tmp_path) -> "mock.Mock":
    """A stand-in LLM2Executor that reads final_prompt.txt and returns a
    fixed result (mirrors the real contract but never calls the network)."""
    exc = mock.Mock(name="LLM2Executor")
    exc.final_prompt_file = os.path.join(str(tmp_path), "final_prompt.txt")
    exc.execute.return_value = "EXEC-RESULT"
    return exc


class _FakeTool:
    """A stand-in for a langchain tool object: carries the original ``func``
    and the tool ``name``; calling it invokes the underlying function."""

    def __init__(self, func):
        self.func = func
        self.name = func.__name__

    def __call__(self, *args, **kwargs):
        return self.func(*args, **kwargs)


def _fake_tool_decorator():
    """Mimic ``langchain_core.tools.tool``: wrap the function in _FakeTool."""

    def decorator(func):
        return _FakeTool(func)

    return decorator


def _install_fake_langchain_tools(monkeypatch):
    """Install a fake langchain_core.tools module so _make_tools can import it
    without langchain installed."""
    fake_tools_mod = types.ModuleType("langchain_core.tools")
    fake_tools_mod.tool = _fake_tool_decorator()
    fake_core_mod = types.ModuleType("langchain_core")
    fake_core_mod.tools = fake_tools_mod
    monkeypatch.setitem(sys.modules, "langchain_core", fake_core_mod)
    monkeypatch.setitem(sys.modules, "langchain_core.tools", fake_tools_mod)
    return _FakeTool


class TestConstruction:
    def test_defaults(self):
        agent = ReActAgent(_llm2_exec("/tmp"), _llm1_config())
        assert agent.max_attempts == 30
        assert agent.hl is False
        assert agent.on_event is None
        assert agent.hl_bridge is None
        assert agent._llm is None  # lazy: no LLM created at init
        assert agent._current_attempt == 1
        assert agent._final_prompt is None
        assert agent.attempt_history == []

    def test_max_attempts_int_coerced(self):
        agent = ReActAgent(_llm2_exec("/tmp"), _llm1_config(), max_attempts="5")
        assert agent.max_attempts == 5


class TestEmit:
    def test_forwarded_to_callback(self):
        events = []
        agent = ReActAgent(_llm2_exec("/tmp"), _llm1_config(), on_event=events.append)
        agent._emit("hello")
        assert events == ["hello"]

    def test_raising_callback_does_not_break(self):
        def boom(msg):
            raise RuntimeError("ui exploded")

        agent = ReActAgent(_llm2_exec("/tmp"), _llm1_config(), on_event=boom)
        # Must not raise — logging never breaks the run.
        agent._emit("hello")

    def test_noop_without_callback(self):
        agent = ReActAgent(_llm2_exec("/tmp"), _llm1_config())
        agent._emit("hello")  # no error


class TestSaveTemplate:
    def test_writes_versioned_file_and_bumps_counter(self, tmp_path):
        agent = ReActAgent(_llm2_exec("/tmp"), _llm1_config())
        path = agent._save_template("template-v1", str(tmp_path))
        assert os.path.basename(path) == "prompt_V1.txt"
        assert open(path, encoding="utf-8").read() == "template-v1"
        assert agent._current_attempt == 2
        # Second save uses the next version number.
        path2 = agent._save_template("template-v2", str(tmp_path))
        assert os.path.basename(path2) == "prompt_V2.txt"
        assert agent._current_attempt == 3


class TestHlBridge:
    def test_analyze_prompt_requires_bridge_in_hl_mode(self, tmp_path, monkeypatch):
        # Fake the tools import + a stub LLM so analyze_prompt can run up to
        # the HL branch, which must raise because no bridge is injected.
        _install_fake_langchain_tools(monkeypatch)
        monkeypatch.setattr(agent_mod, "make_llm1", mock.Mock(name="make_llm1"))

        agent = ReActAgent(
            _llm2_exec(tmp_path), _llm1_config(), hl=True, hl_bridge=None
        )
        tools = agent._make_tools(str(tmp_path), str(tmp_path))
        analyze = next(t for t in tools if t.name == "analyze_prompt")
        with pytest.raises(RuntimeError, match="hl_bridge"):
            analyze.func(
                current_template="t",
                expected_result="exp",
                actual_result="act",
            )


class TestTools:
    @pytest.fixture
    def tools_env(self, tmp_path, monkeypatch):
        _FakeTool = _install_fake_langchain_tools(monkeypatch)
        exec_mock = _llm2_exec(tmp_path)
        agent = ReActAgent(exec_mock, _llm1_config(), on_event=print)
        tools = agent._make_tools(str(tmp_path), str(tmp_path))
        by_name = {t.name: t for t in tools}
        return tmp_path, agent, by_name, _FakeTool

    def test_six_tools_present(self, tools_env):
        _, _, by_name, _ = tools_env
        assert set(by_name) == {
            "build_prompt",
            "llm2",
            "explain_result",
            "analyze_prompt",
            "save_prompt",
            "finish",
        }

    def test_build_prompt_writes_final_prompt(self, tools_env):
        tmp_path, agent, by_name, _ = tools_env
        # Provide the variable file so substitution is non-empty.
        (tmp_path / "name.txt").write_text("Ada", encoding="utf-8")
        out = by_name["build_prompt"].func("Hello {{name}}")
        # build_final_prompt returns the substituted prompt string (not a path).
        assert out == "Hello Ada"
        final = (tmp_path / "final_prompt.txt").read_text(encoding="utf-8")
        assert final == "Hello Ada"

    def test_llm2_delegates_to_executor(self, tools_env):
        tmp_path, agent, by_name, _ = tools_env
        exec_mock = agent.llm2
        out = by_name["llm2"].func()
        assert out == "EXEC-RESULT"
        exec_mock.execute.assert_called_once_with()

    def test_save_prompt_versions_and_reports(self, tools_env):
        tmp_path, agent, by_name, _ = tools_env
        msg = by_name["save_prompt"].func("tpl v1")
        assert (tmp_path / "prompt_V1.txt").read_text(encoding="utf-8") == "tpl v1"
        assert "v1" in msg or "1" in msg

    def test_finish_stores_template_and_returns_done(self, tools_env):
        tmp_path, agent, by_name, _ = tools_env
        out = by_name["finish"].func("final template")
        assert out == "DONE"
        assert agent._final_prompt == "final template"


class TestRun:
    """run() with a mocked langgraph agent (create_react_agent boundary)."""

    @pytest.fixture
    def run_env(self, tmp_path, monkeypatch):
        # Fake the tools import (agent._make_tools imports langchain_core.tools).
        _install_fake_langchain_tools(monkeypatch)
        # Fake make_llm1 (the agent LLM) — returns a sentinel object.
        llm_sentinel = mock.Mock(name="llm1")
        monkeypatch.setattr(agent_mod, "make_llm1", lambda cfg: llm_sentinel)
        # Fake langgraph.prebuilt.create_react_agent.
        fake_react = mock.Mock(name="create_react_agent")
        fake_agent = mock.Mock(name="react_agent")
        fake_react.return_value = fake_agent
        fake_graph_mod = types.ModuleType("langgraph")
        fake_prebuilt_mod = types.ModuleType("langgraph.prebuilt")
        fake_prebuilt_mod.create_react_agent = fake_react
        fake_graph_mod.prebuilt = fake_prebuilt_mod
        monkeypatch.setitem(sys.modules, "langgraph", fake_graph_mod)
        monkeypatch.setitem(sys.modules, "langgraph.prebuilt", fake_prebuilt_mod)
        return tmp_path, monkeypatch, fake_react, fake_agent

    def test_run_success_calls_finish(self, run_env):
        tmp_path, monkeypatch, fake_react, fake_agent = run_env

        # The mocked react agent, when invoked, calls the `finish` tool on the
        # real agent to simulate a successful self-comparison.
        def _fake_invoke(state, config=None):
            return {"messages": []}

        fake_agent.invoke.side_effect = _fake_invoke

        events: list[str] = []
        agent = ReActAgent(
            _llm2_exec(tmp_path), _llm1_config(),
            max_attempts=7, on_event=events.append,
        )

        # Pre-seed the finish result via a wrapper: emulate the agent calling
        # finish by patching _final_prompt after invoke. We instead verify the
        # fallback path AND the finish path in separate tests; here we set
        # _final_prompt via a tool call simulated through the real tools.
        # Simplest: let the fake agent invoke the real `finish` tool by
        # capturing it. We rebuild tools to get the finish function.
        tools = agent._make_tools(str(tmp_path), str(tmp_path))
        finish_tool = next(t for t in tools if t.name == "finish")

        def _invoke_calls_finish(state, config=None):
            finish_tool.func("final {{name}}")
            return {"messages": []}

        fake_agent.invoke.side_effect = _invoke_calls_finish

        result = agent.run(
            prompt_template="Hello {{name}}",
            expected_result="expected",
            base_dir=str(tmp_path),
            output_dir=str(tmp_path),
        )

        assert result["success"] is True
        assert result["attempts"] == 1
        assert result["final_result"] == "EXEC-RESULT"
        assert result["final_template"] == "final {{name}}"
        # build_final_prompt returns the substituted prompt (the template has a
        # {{name}} placeholder with no name.txt -> empty substitution).
        assert result["final_prompt_file"] == "final "
        assert (tmp_path / "final_prompt.txt").exists()
        assert len(result["history"]) == 1
        # Events non-empty (T5.2 / T5.7 requirement).
        assert events
        assert any("SUCCESS" in e for e in events)

    def test_run_without_finish_falls_back(self, run_env):
        tmp_path, monkeypatch, fake_react, fake_agent = run_env
        # Agent never calls finish -> success False, template = initial.
        fake_agent.invoke.return_value = {"messages": []}

        events: list[str] = []
        agent = ReActAgent(
            _llm2_exec(tmp_path), _llm1_config(),
            max_attempts=3, on_event=events.append,
        )
        result = agent.run(
            prompt_template="initial template",
            expected_result="expected",
            base_dir=str(tmp_path),
            output_dir=str(tmp_path),
        )
        assert result["success"] is False
        assert result["final_template"] == "initial template"
        assert any("WARNING" in e for e in events)

    def test_recursion_limit_is_max_attempts_times_10(self, run_env):
        tmp_path, monkeypatch, fake_react, fake_agent = run_env
        fake_agent.invoke.return_value = {"messages": []}
        agent = ReActAgent(
            _llm2_exec(tmp_path), _llm1_config(), max_attempts=5
        )
        agent.run(
            prompt_template="t", expected_result="e",
            base_dir=str(tmp_path), output_dir=str(tmp_path),
        )
        # create_react_agent was called with the llm + tools + prompt.
        react_kwargs = fake_react.call_args.kwargs
        assert "model" in react_kwargs
        assert "tools" in react_kwargs
        assert react_kwargs["prompt"]
        # invoke got recursion_limit = max_attempts * 10 (passed as the `config` kwarg).
        assert fake_agent.invoke.call_args.kwargs == {"config": {"recursion_limit": 50}}


# ---- HLBridge protocol sanity -------------------------------------------

class _StubBridge:
    def __init__(self):
        self.waited = False
        self._resp = "ШАБЛОН: improved\nИЗМЕНЕНИЯ: made it better"

    def wait_for_response(self) -> str:
        self.waited = True
        return self._resp


class TestHlBridgeProtocol:
    def test_stub_satisfies_protocol(self, tmp_path, monkeypatch):
        # A stub bridge implementing wait_for_response() must satisfy HLBridge
        # and drive the HL branch of analyze_prompt end-to-end.
        _install_fake_langchain_tools(monkeypatch)
        monkeypatch.setattr(agent_mod, "make_llm1", mock.Mock(name="make_llm1"))

        bridge = _StubBridge()
        agent = ReActAgent(
            _llm2_exec(tmp_path), _llm1_config(), hl=True, hl_bridge=bridge
        )
        tools = agent._make_tools(str(tmp_path), str(tmp_path))
        analyze = next(t for t in tools if t.name == "analyze_prompt")
        out = analyze.func(
            current_template="old", expected_result="exp", actual_result="act"
        )
        # srcprompt.txt was written before waiting.
        assert (tmp_path / "srcprompt.txt").exists()
        # The bridge was consulted.
        assert bridge.waited is True
        # The human response was parsed (ШАБЛОН: / ИЗМЕНЕНИЯ:).
        assert out == "improved"
        assert agent._change_history == ["made it better"]
