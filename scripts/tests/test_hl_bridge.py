#!/usr/bin/env python3
"""tests.test_hl_bridge — unit tests for the GUI HL bridge (Phase 7: T7.1–T7.7).

Covers:
- GuiHLBridge.wait_for_response blocks until Api.continue_hl releases it;
- the released response text is returned intact;
- continue_hl guards: empty text and "not waiting" both rejected;
- start_run injects the GUI bridge into run_optimization (T7.2);
- full HL cycle: worker pauses (hl_waiting=True, source_prompt filled),
  continue_hl resumes, the run completes (headless E2E, T7.7).

All tests mock ``core.runner.run_optimization`` — no real LLM calls.

Run::

    python -m pytest scripts/tests/test_hl_bridge.py -v
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
from ui.api import Api, GuiHLBridge  # noqa: E402


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


def _make_api(tmp_path) -> Api:
    cfg_path = _write_config(str(tmp_path / "config.yaml"))
    cfg = ConfigManager(path=cfg_path)
    return Api(config=cfg, base_dir=str(tmp_path / "workspace"))


def _wait_condition(cond, timeout: float = 10.0) -> bool:
    """Poll ``cond`` until true or timeout; return the final state."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def _wait_worker(api: Api, timeout: float = 10.0) -> None:
    """Block until the worker thread exits (running → False)."""
    assert _wait_condition(lambda: not api._running, timeout)
    if api._worker is not None:
        api._worker.join(timeout=timeout)


# ---------------------------------------------------------------------------
# T7.1 — GuiHLBridge core behaviour
# ---------------------------------------------------------------------------

class TestGuiHLBridge:
    """wait_for_response / continue round-trip on the bridge (T7.1)."""

    def test_wait_blocks_until_continue(self, tmp_path):
        api = _make_api(tmp_path)
        bridge = api._hl_bridge

        result: dict = {}

        def _waiter():
            result["text"] = bridge.wait_for_response()

        t = threading.Thread(target=_waiter, daemon=True)
        t.start()

        # The wait must block: the event is not set yet.
        time.sleep(0.3)
        assert t.is_alive(), "wait_for_response did not block"
        assert not bridge.event.is_set()

        # Release with a response.
        bridge.release("SHA: hello\nCHANGES: fixed")
        t.join(timeout=5)
        assert not t.is_alive()
        assert result["text"] == "SHA: hello\nCHANGES: fixed"

    def test_wait_sets_hl_state_via_api(self, tmp_path):
        """While blocked, Api state must show hl_waiting + source_prompt."""
        api = _make_api(tmp_path)
        bridge = api._hl_bridge

        # The agent writes srcprompt.txt before calling wait_for_response.
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "srcprompt.txt").write_text("ANALYSIS PROMPT", encoding="utf-8")

        done = threading.Event()

        def _waiter():
            bridge.wait_for_response()
            done.set()

        t = threading.Thread(target=_waiter, daemon=True)
        t.start()
        assert _wait_condition(lambda: api._hl_waiting, 5), "hl_waiting not set"

        st = api.get_state()
        assert st["hl_waiting"] is True
        assert st["source_prompt"] == "ANALYSIS PROMPT"
        # The start button must read "Continue" while waiting.
        assert st["start_button"]["text"] == "Continue"

        bridge.release("response")
        t.join(timeout=5)
        assert done.is_set()

        # After the wait the flag is cleared again.
        assert _wait_condition(lambda: not api._hl_waiting, 5)
        st = api.get_state()
        assert st["hl_waiting"] is False
        assert st["start_button"]["text"] == "Start"


# ---------------------------------------------------------------------------
# T7.1/T7.6 — Api.continue_hl guards
# ---------------------------------------------------------------------------

class TestContinueHlGuards:
    """continue_hl validation (T7.1, T7.6)."""

    def test_empty_rejected(self, tmp_path):
        api = _make_api(tmp_path)
        res = api.continue_hl("   ")
        assert res["ok"] is False
        assert "empty" in res["error"]

    def test_not_waiting_rejected(self, tmp_path):
        api = _make_api(tmp_path)
        res = api.continue_hl("hello")
        assert res["ok"] is False
        assert "not waiting" in res["error"].lower()

    def test_release_unblocks_waiter(self, tmp_path):
        """continue_hl through the Api releases a blocked bridge (T7.6)."""
        api = _make_api(tmp_path)
        bridge = api._hl_bridge

        result: dict = {}
        t = threading.Thread(
            target=lambda: result.update(text=bridge.wait_for_response()),
            daemon=True,
        )
        t.start()
        assert _wait_condition(lambda: api._hl_waiting, 5), "hl_waiting not set"

        res = api.continue_hl("human response")
        assert res["ok"] is True, res
        t.join(timeout=5)
        assert result["text"] == "human response"


# ---------------------------------------------------------------------------
# T7.2 — start_run injects the GUI bridge
# ---------------------------------------------------------------------------

class TestStartRunBridgeInjection:
    """start_run must pass the GUI HL bridge to run_optimization (T7.2)."""

    def test_hl_bridge_injected(self, tmp_path):
        api = _make_api(tmp_path)

        def _fake_run(cfg_data, template, expected, variables, base_dir, hl,
                      on_event=None, hl_bridge=None, stop_event=None):
            return OptimizationResult(
                success=True, final_template="t", final_result="r", attempts=1,
            )

        with mock.patch("ui.api.run_optimization", side_effect=_fake_run) as m:
            res = api.start_run("hello", "world", [], True)
            assert res["ok"] is True
            _wait_worker(api)

        args, kwargs = m.call_args
        # The injected bridge must be the Api's own GUI bridge instance.
        injected = kwargs.get("hl_bridge", args[7] if len(args) > 7 else None)
        assert injected is api._hl_bridge


# ---------------------------------------------------------------------------
# T7.7 — headless E2E: full HL cycle through start_run
# ---------------------------------------------------------------------------

class TestHlFullCycle:
    """Worker pauses in HL, continue_hl resumes, run completes (T7.7)."""

    def test_full_hl_cycle(self, tmp_path):
        api = _make_api(tmp_path)
        ws = str(tmp_path / "workspace")
        human_replied = threading.Event()

        def _hl_run(cfg_data, template, expected, variables, base_dir, hl,
                    on_event=None, hl_bridge=None, stop_event=None):
            # Mimic the real pipeline: write the analysis prompt, then
            # block on the bridge exactly like ReActAgent does.
            on_event and on_event("Validation passed")
            os.makedirs(base_dir, exist_ok=True)
            with open(os.path.join(base_dir, "srcprompt.txt"), "w", encoding="utf-8") as f:
                f.write("ANALYSIS PROMPT FROM AGENT")
            on_event and on_event("[HL] Waiting for human response …")
            assert hl is True
            response = hl_bridge.wait_for_response()
            on_event and on_event(f"[HL] Received response ({len(response)} chars)")
            assert response == "ШАБЛОН: fixed {{x}}\nИЗМЕНЕНИЯ: made it better"
            human_replied.set()
            on_event and on_event("Optimization finished: success=True")
            return OptimizationResult(
                success=True,
                final_template="fixed {{x}}",
                final_result="the expected answer",
                attempts=2,
            )

        with mock.patch("ui.api.run_optimization", side_effect=_hl_run):
            res = api.start_run("hello {{x}}", "world",
                                [{"name": "x", "value": "v"}], True)
            assert res["ok"] is True, res

            # The worker must pause: hl_waiting=True + source_prompt filled.
            deadline = time.time() + 10
            st = None
            while time.time() < deadline:
                st = api.get_state()
                if st.get("hl_waiting"):
                    break
                time.sleep(0.02)
            assert st is not None and st.get("hl_waiting"), f"hl_waiting never set, last={st}"
            assert st["source_prompt"] == "ANALYSIS PROMPT FROM AGENT"
            assert st["running"] is True
            assert st["start_button"]["text"] == "Continue"
            # The Start/Continue button stays disabled while the worker is
            # running (get_state: start_enabled = not running) — even during
            # an HL pause the run is still in progress.
            assert st["start_button"]["enabled"] is False

            # The human types the response and clicks Continue.
            res2 = api.continue_hl("ШАБЛОН: fixed {{x}}\nИЗМЕНЕНИЯ: made it better")
            assert res2["ok"] is True, res2

        # The worker resumes and finishes.
        assert _wait_condition(lambda: human_replied.is_set(), 10), \
            "agent did not resume after continue_hl"
        _wait_worker(api)

        assert api._running is False
        assert api._hl_waiting is False
        assert api._optimization_result == "fixed {{x}}"

        st = api.get_state()
        assert st["running"] is False
        assert st["hl_waiting"] is False
        assert st["start_button"]["text"] == "Start"
        progress = "\n".join(api._progress)
        assert "[HL] Received response" in progress

    def test_double_wait_rearmed(self, tmp_path):
        """Two consecutive HL waits must both work (event re-armed each time)."""
        api = _make_api(tmp_path)
        bridge = api._hl_bridge

        order: list[str] = []
        gate1 = threading.Event()
        gate2 = threading.Event()
        # arm2 is set by the waiter *after* it has re-armed the second wait
        # (i.e. right before the second blocking call), so the test can be
        # sure the second wait is live before it asserts on `order`.
        arm2 = threading.Event()

        def _waiter():
            order.append("wait1")
            bridge.wait_for_response()
            order.append("got1")
            gate1.set()
            order.append("wait2")
            # Re-arm: the bridge clears the event and sets hl_waiting.
            arm2.set()
            bridge.wait_for_response()
            order.append("got2")
            gate2.set()

        t = threading.Thread(target=_waiter, daemon=True)
        t.start()

        # First wait.
        assert _wait_condition(lambda: api._hl_waiting, 5), "hl_waiting not set (wait 1)"
        api.continue_hl("first")
        assert _wait_condition(gate1.is_set, 5), "waiter did not resume after first release"

        # Second wait: the waiter re-arms hl_waiting before blocking.
        assert _wait_condition(arm2.is_set, 5), "waiter did not re-arm second wait"
        assert _wait_condition(lambda: api._hl_waiting, 5), \
            "hl_waiting not re-armed for the second wait"
        api.continue_hl("second")

        # Both waits completed in order.
        assert _wait_condition(gate2.is_set, 5), "waiter did not resume after second release"
        t.join(timeout=5)
        assert not t.is_alive()
        assert order == ["wait1", "got1", "wait2", "got2"]


if __name__ == "__main__":
    unittest.main()
