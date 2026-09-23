#!/usr/bin/env python3
"""tests.test_integration_run — integration test for run_prompt_check (T4.7).

Runs the **real** one-shot pipeline against the seeded judge LLM from
``config.yaml``. Skipped when the judge server is unreachable so the unit
suite stays green on machines without the local vLLM cluster.

Run explicitly with:  pytest scripts/tests/test_integration_run.py -v
"""

import os
import socket
import sys
import urllib.parse

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.config import ConfigManager
from core.runner import run_prompt_check

# Project root (two levels up from scripts/tests/).
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _server_reachable(api_base: str, timeout: float = 2.0) -> bool:
    """Best-effort TCP check of the api_base host:port."""
    try:
        hostport = urllib.parse.urlsplit(api_base).netloc
        if not hostport:
            return False
        host, _, port = hostport.partition(":")
        port = int(port or (443 if "https" in api_base else 80))
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _load_config_data() -> dict:
    cfg = ConfigManager(os.path.join(_PROJECT_ROOT, "config.yaml"))
    return cfg.load()


def test_run_prompt_check_real_llm(tmp_path):
    cfg_data = _load_config_data()
    roles = cfg_data.get("roles") or {}
    judge_name = roles.get("judge")
    judge = next(
        (l for l in cfg_data.get("llms", []) if l.get("name") == judge_name), None
    )
    if not judge:
        pytest.skip("No judge LLM configured in config.yaml")
    if not _server_reachable(judge["api_base"]):
        pytest.skip(
            f"Judge server unreachable: {judge['api_base']} "
            "(vLLM cluster not available)"
        )

    template = "Say the number 42 and nothing else."
    result = run_prompt_check(
        cfg_data,
        template,
        "42",
        {},
        str(tmp_path),
        on_event=lambda msg: print(f"  [event] {msg}"),
    )

    assert isinstance(result, str)
    assert len(result) > 0
    # The final prompt file must exist and contain the substituted prompt.
    final_prompt = (tmp_path / "final_prompt.txt").read_text(encoding="utf-8")
    assert final_prompt == template
