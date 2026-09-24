#!/usr/bin/env python3
"""tests.test_start_run — unit tests for Api.start_run real implementation
(Phase 6: T6.1–T6.3).

Covers:
- double-start guard (T6.3): second start_run while running → error;
- validation abort (T6.7): bad input → errors returned, no worker spawned;
- real run completion (T6.1+T6.2): worker runs run_optimization,
  progress deque receives events, optimization_result = final_template;
- worker exception → running cleared, error event appended.

All tests mock ``core.runner.run_optimization`` — no real LLM calls.

Run::

    python -m pytest scripts/tests/test_start_run.py -v
"""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from unittest import mock

import yaml

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from core.config import ConfigManager  # noqa: E402
from core.runner import OptimizationResult  # noqa: E402
from ui.api import Api  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _write_config(path: str) -> str:
    """Write a minimal valid config.yaml and return the path."""
    cfg = {
        "llms": [
            {
                "name": "judge",
                "api_base": "http://127.0.0.1:11434/v1",
                "api_key": "sk-test",
                "model": "judge/model",
                "temperature": 0.1,
            },
            {
                "name": "agent",
                "api_base": "http://127.0.0.1:11435/v1",
                "api_key": "sk-test",
                "model": "agent/model",
                "temperature": 0.2,
            },
        ],
        "roles": {"judge": "judge", "prompts": "agent"},
        "hl": False,
        "max_attempts": 30,
    }
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
    return path


def _wait_worker(api: Api, timeout: float = 10.0) -> None:
    """Block until the worker thread exits (running → False)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not api._running:
            break
        time.sleep(0.05)
    if api._worker is not None:
        api._worker.join(timeout=timeout)


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

class TestStartRunValidationAbort:
    """start_run with invalid input must not spawn a worker (T6.7)."""

    def _make_api(self, tmp_path):
        cfg_path = _write_config(str(tmp_path / "config.yaml"))
        cfg = ConfigManager(path=cfg_path)
        return Api(config=cfg, base_dir=str(tmp_path / "workspace"))

    def test_unset_role_rejected(self, tmp_path):
        api = self._make_api(tmp_path)
        # Unset the judge role.
        api._config.set_role("judge", "")
        res = api.start_run("hello", "world", [], False)
        assert res["ok"] is False
        assert "errors" in res
        assert any("LLM as Judge" in e for e in res["errors"])
        # No worker should have been spawned.
        assert api._worker is None
        assert api._running is False
        # Workspace must be untouched.
        ws = tmp_path / "workspace"
        if ws.exists():
            assert list(ws.iterdir()) == []

    def test_empty_prompt_rejected(self, tmp_path):
        api = self._make_api(tmp_path)
        res = api.start_run("", "world", [], False)
        assert res["ok"] is False
        assert "errors" in res
        assert api._worker is None
        assert api._running is False

    def test_missing_variable_value_rejected(self, tmp_path):
        api = self._make_api(tmp_path)
        res = api.start_run("hello {{x}}", "world", [{"name": "x", "value": ""}], False)
        assert res["ok"] is False
        assert "errors" in res
        assert any("x" in e for e in res["errors"])
        assert api._worker is None
        assert api._running is False


class TestStartRunDoubleStartGuard:
    """Second start_run while a run is in progress → error (T6.3)."""

    def _make_api(self, tmp_path):
        cfg_path = _write_config(str(tmp_path / "config.yaml"))
        cfg = ConfigManager(path=cfg_path)
        return Api(config=cfg, base_dir=str(tmp_path / "workspace"))

    def test_concurrent_start_rejected(self, tmp_path):
        api = self._make_api(tmp_path)

        # Use a blocking mock so the first run stays "in progress".
        release = threading.Event()

        def _blocking_run(*args, **kwargs):
            # Signal that we entered the run, then wait.
            release.set()
            # Wait until the test releases us.
            api._worker_done_wait.wait(timeout=10)
            return OptimizationResult(
                success=True,
                final_template="final {{x}}",
                final_result="ok",
                attempts=1,
            )

        # Add a helper event the test can use to release the worker.
        api._worker_done_wait = threading.Event()

        with mock.patch("ui.api.run_optimization", side_effect=_blocking_run):
            res1 = api.start_run("hello {{x}}", "world",
                                 [{"name": "x", "value": "v"}], False)
            assert res1["ok"] is True, res1

            # Wait for the worker to actually enter run_optimization.
            assert release.wait(timeout=5), "worker did not start in time"
            assert api._running is True

            # Second start while the first is in progress → rejected.
            res2 = api.start_run("hello {{x}}", "world",
                                 [{"name": "x", "value": "v"}], False)
            assert res2["ok"] is False
            assert "already in progress" in res2.get("error", "").lower() or \
                   "already in progress" in res2.get("error", "")

            # Release the worker so the thread can exit.
            api._worker_done_wait.set()

        _wait_worker(api)
        assert api._running is False


class TestStartRunRealRunCompletion:
    """Worker runs run_optimization; on_event → progress; result set (T6.1+T6.2)."""

    def _make_api(self, tmp_path):
        cfg_path = _write_config(str(tmp_path / "config.yaml"))
        cfg = ConfigManager(path=cfg_path)
        return Api(config=cfg, base_dir=str(tmp_path / "workspace"))

    def test_success_sets_result_and_progress(self, tmp_path):
        api = self._make_api(tmp_path)
        events_seen: list[str] = []

        def _fake_run(cfg_data, template, expected, variables, base_dir, hl,
                      on_event=None, hl_bridge=None, stop_event=None):
            if on_event:
                on_event("[TEST] step 1")
                on_event("[TEST] step 2")
            return OptimizationResult(
                success=True,
                final_template="OPTIMIZED {{x}}",
                final_result="the expected answer",
                attempts=3,
            )

        with mock.patch("ui.api.run_optimization", side_effect=_fake_run) as m:
            res = api.start_run("hello {{x}}", "world",
                                [{"name": "x", "value": "v"}], False)
            assert res["ok"] is True
            _wait_worker(api)

        # run_optimization must have been called with the right args.
        assert m.call_count == 1
        args, kwargs = m.call_args
        assert args[1] == "hello {{x}}"          # template
        assert args[2] == "world"                # expected
        assert args[3] == {"x": "v"}             # variables (dict)
        assert args[4] == str(tmp_path / "workspace")
        assert args[5] is False                  # hl

        # T6.2: running cleared, optimization_result = final_template.
        assert api._running is False
        assert api._optimization_result == "OPTIMIZED {{x}}"

        # T6.1: progress deque received the agent events.
        progress = "\n".join(api._progress)
        assert "[TEST] step 1" in progress
        assert "[TEST] step 2" in progress
        assert "Optimization finished" in progress

        # get_state reflects the final state.
        st = api.get_state()
        assert st["ok"] is True
        assert st["running"] is False
        assert st["optimization_result"] == "OPTIMIZED {{x}}"
        assert st["start_button"]["enabled"] is True
        assert st["start_button"]["text"] == "Start"

    def test_hl_flag_forwarded(self, tmp_path):
        api = self._make_api(tmp_path)

        def _fake_run(cfg_data, template, expected, variables, base_dir, hl,
                      on_event=None, hl_bridge=None, stop_event=None):
            return OptimizationResult(
                success=True, final_template="t", final_result="r", attempts=1,
            )

        with mock.patch("ui.api.run_optimization", side_effect=_fake_run) as m:
            res = api.start_run("hello", "world", [], True)
            assert res["ok"] is True
            _wait_worker(api)

        args, _ = m.call_args
        assert args[5] is True  # hl forwarded


class TestStartRunWorkerException:
    """Worker exception → running cleared, error event appended."""

    def _make_api(self, tmp_path):
        cfg_path = _write_config(str(tmp_path / "config.yaml"))
        cfg = ConfigManager(path=cfg_path)
        return Api(config=cfg, base_dir=str(tmp_path / "workspace"))

    def test_llm_error_handled(self, tmp_path):
        api = self._make_api(tmp_path)

        def _failing_run(*args, **kwargs):
            raise RuntimeError("LLM server down")

        with mock.patch("ui.api.run_optimization", side_effect=_failing_run):
            res = api.start_run("hello", "world", [], False)
            assert res["ok"] is True
            _wait_worker(api)

        assert api._running is False
        assert api._optimization_result == ""
        progress = "\n".join(api._progress)
        assert "Run failed: LLM server down" in progress

        st = api.get_state()
        assert st["running"] is False
        assert st["start_button"]["enabled"] is True


class TestStartRunProgressBounded:
    """Progress deque must stay bounded at 500 lines."""

    def _make_api(self, tmp_path):
        cfg_path = _write_config(str(tmp_path / "config.yaml"))
        cfg = ConfigManager(path=cfg_path)
        return Api(config=cfg, base_dir=str(tmp_path / "workspace"))

    def test_maxlen_500(self, tmp_path):
        api = self._make_api(tmp_path)

        def _chatty_run(*args, **kwargs):
            on_event = args[6] if len(args) > 6 else kwargs.get("on_event")
            for i in range(600):
                if on_event:
                    on_event(f"line {i}")
            return OptimizationResult(
                success=True, final_template="t", final_result="r", attempts=1,
            )

        with mock.patch("ui.api.run_optimization", side_effect=_chatty_run):
            res = api.start_run("hello", "world", [], False)
            assert res["ok"] is True
            _wait_worker(api)

        assert len(api._progress) == 500
        # The first 100 lines must have been evicted; the last 500 remain.
        progress = "\n".join(api._progress)
        assert "line 99" not in progress
        assert "line 599" in progress


if __name__ == "__main__":
    unittest.main()
