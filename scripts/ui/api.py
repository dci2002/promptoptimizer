"""ui.api — Api class: all JS ↔ Python bridge methods.

``Api`` is the single object exposed to JavaScript as
``window.pywebview.api`` (architecture §3.6). All methods return plain
JSON-serialisable values.

Phase 1: only :meth:`Api.get_config` is implemented; every other method from
architecture §3.6 is a stub returning ``{"ok": False, "error": "not implemented"}``.
"""

from __future__ import annotations

from typing import Any, Optional

from core.config import ConfigManager

__all__ = ["Api"]

_STUB: dict = {"ok": False, "error": "not implemented"}


def _stub() -> dict:
    # Return a fresh dict each call so callers can never mutate a shared object.
    return {"ok": False, "error": "not implemented"}


class Api:
    """JS ↔ Python bridge. Constructed by ``main.app.main()`` with a loaded
    :class:`~core.config.ConfigManager`."""

    def __init__(self, config: ConfigManager) -> None:
        self._config = config

    # ------------------------------------------------------------- Phase 1
    def get_config(self) -> dict:
        """Return the whole config for the initial UI render.

        ``{"ok": True, "llms": [...], "roles": {...}, "hl": bool,
        "max_attempts": int}``
        """
        data = self._config.data
        return {
            "ok": True,
            "llms": data.get("llms", []),
            "roles": data.get("roles", {}),
            "hl": data.get("hl", False),
            "max_attempts": data.get("max_attempts", 30),
        }

    # ------------------------------------------- stubs (implemented later)
    def list_llms(self) -> dict:
        return _stub()

    def save_llm(self, cfg: dict) -> dict:
        return _stub()

    def remove_llm(self, name: str) -> dict:
        return _stub()

    def get_roles(self) -> dict:
        return _stub()

    def set_role(self, role: str, name: str) -> dict:
        return _stub()

    def get_hl(self) -> dict:
        return _stub()

    def set_hl(self, flag: bool) -> dict:
        return _stub()

    def run_validation(self, template: str, result: str, variables: list) -> dict:
        return _stub()

    def run_prompt(self, template: str, result: str, variables: list) -> dict:
        return _stub()

    def start_run(self, template: str, result: str, variables: list, hl: bool) -> dict:
        return _stub()

    def continue_hl(self, response_text: str) -> Optional[dict]:
        return _stub()

    def get_state(self) -> dict:
        return _stub()

    def copy_to_clipboard(self, text: str) -> dict:
        return _stub()

    def get_llm_dialog(self) -> dict:
        return _stub()

    def get_variable_dialog(self) -> dict:
        return _stub()
