"""ui.api — Api class: all JS ↔ Python bridge methods.

``Api`` is the single object exposed to JavaScript as
``window.pywebview.api`` (architecture §3.6). All methods return plain
JSON-serialisable values.

Phase 1: ``get_config`` implemented.
Phase 2: Settings-tab methods implemented on top of
:class:`~core.config.ConfigManager` — ``list_llms``, ``save_llm``,
``remove_llm``, ``get_roles``, ``set_role``, ``get_hl``, ``set_hl``.
Phase 3: Prompts-tab validation — ``run_validation``.
Every other method from architecture §3.6 is a stub returning
``{"ok": False, "error": "not implemented"}``.

Error convention (architecture §8): core errors are converted at this
boundary into ``{"ok": False, "error": "..."}`` dicts — exceptions never
escape to the JS side.
"""

from __future__ import annotations

import traceback

from typing import Any, Optional

from core.config import ConfigError, ConfigManager
from core.prompt_io import validate_run_inputs

# ── Clipboard access (Qt-based, used by get_clipboard / copy_to_clipboard) ──
# Imported lazily to avoid breaking headless test environments where Qt is
# not installed.  QApplication.clipboard() works even before the window is
# shown, as long as the QApplication instance exists (guaranteed by the
# PyQt backend of pywebview).
_QT_AVAILABLE = False
try:
    from PyQt6.QtWidgets import QApplication
    _QT_AVAILABLE = True
except ImportError:
    try:
        from PyQt5.QtWidgets import QApplication  # type: ignore
        _QT_AVAILABLE = True
    except ImportError:
        pass

__all__ = ["Api"]


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
        try:
            data = self._config.data
            return {
                "ok": True,
                "llms": data.get("llms", []),
                "roles": data.get("roles", {}),
                "hl": data.get("hl", False),
                "max_attempts": data.get("max_attempts", 30),
            }
        except Exception as e:  # defensive: bridge must never raise into JS
            return {"ok": False, "error": f"get_config failed: {e}"}

    # ------------------------------------------------- Phase 2 — Settings
    def list_llms(self) -> dict:
        """Return the LLM table rows.

        ``{"ok": True, "llms": [ {name, api_base, api_key, model,
        temperature}, ... ]}``
        """
        try:
            return {"ok": True, "llms": self._config.list_llms()}
        except Exception as e:
            return {"ok": False, "error": f"list_llms failed: {e}"}

    def save_llm(self, cfg: dict) -> dict:
        """Add or update an LLM.

        ``cfg`` is an ``{"name", "api_base", "api_key", "model",
        "temperature"}`` dict plus an optional ``"_old_name"`` field: if
        present (and != name), the LLM is updated in place (rename
        cascade applied); otherwise a new LLM is added.

        ``{"ok": True, "llm": {...}}`` on success;
        ``{"ok": False, "error": "..."}`` on validation/duplicate failure.
        """
        try:
            if not isinstance(cfg, dict):
                return {"ok": False, "error": "save_llm: expected an object"}
            payload = {k: v for k, v in cfg.items() if not k.startswith("_")}
            old_name = str(cfg.get("_old_name") or "").strip()

            existing = {e.get("name") for e in self._config.get_llms()}
            if old_name and old_name in existing and old_name != str(payload.get("name", "")):
                entry = self._config.update_llm(old_name, payload)
            elif old_name and old_name == str(payload.get("name", "")):
                # same name: treat as an in-place update (no rename)
                entry = self._config.update_llm(old_name, payload)
            elif str(payload.get("name", "")) in existing:
                # no _old_name but name collides — reject as duplicate
                raise ConfigError(
                    f"LLM name already exists: '{payload.get('name')}'"
                )
            else:
                entry = self._config.add_llm(payload)

            return {"ok": True, "llm": entry}
        except ConfigError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            return {"ok": False, "error": f"save_llm failed: {e}"}

    def remove_llm(self, name: str) -> dict:
        """Remove an LLM by name; roles referencing it are cleared.

        ``{"ok": True, "cleared_roles": [...]}`` or
        ``{"ok": False, "error": "..."}``.
        """
        try:
            cleared = self._config.remove_llm(str(name or "").strip())
            return {"ok": True, "cleared_roles": cleared}
        except ConfigError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            return {"ok": False, "error": f"remove_llm failed: {e}"}

    def get_roles(self) -> dict:
        """Return the role assignments.

        ``{"ok": True, "roles": {"judge": "...", "prompts": "..."}}``
        """
        try:
            return {"ok": True, "roles": self._config.get_roles()}
        except Exception as e:
            return {"ok": False, "error": f"get_roles failed: {e}"}

    def set_role(self, role: str, name: str) -> dict:
        """Assign LLM ``name`` to ``role`` ('' unsets). Persists.

        ``{"ok": True, "roles": {...}}`` or ``{"ok": False, "error": ...}``.
        """
        try:
            roles = self._config.set_role(str(role or ""), str(name or ""))
            return {"ok": True, "roles": roles}
        except ConfigError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            return {"ok": False, "error": f"set_role failed: {e}"}

    def get_hl(self) -> dict:
        """``{"ok": True, "hl": bool}``"""
        try:
            return {"ok": True, "hl": self._config.get_hl()}
        except Exception as e:
            return {"ok": False, "error": f"get_hl failed: {e}"}

    def set_hl(self, flag: bool) -> dict:
        """Set the HL flag and persist. ``{"ok": True, "hl": bool}``."""
        try:
            if isinstance(flag, str):
                flag = flag.strip().lower() in ("1", "true", "yes", "on")
            hl = self._config.set_hl(bool(flag))
            return {"ok": True, "hl": hl}
        except ConfigError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            return {"ok": False, "error": f"set_hl failed: {e}"}

    # ------------------------------------------------- Phase 3 — Prompts
    def run_validation(self, template: str, result: str, variables: list) -> dict:
        """Validate the prompt settings (req 3.6) — pure check, no files.

        ``variables`` is a list of ``{"name": str, "value": str}`` dicts
        (the JS variables table rows). Returns ``{"ok": True}`` when valid,
        or ``{"ok": False, "errors": [...]}`` when not.
        """
        try:
            # Normalise the JS variables array into a dict.
            var_map: dict[str, str] = {}
            if isinstance(variables, list):
                for row in variables:
                    if isinstance(row, dict):
                        name = str(row.get("name") or "").strip()
                        value = str(row.get("value") or "")
                        if name:
                            var_map[name] = value

            errors = validate_run_inputs(self._config.data, str(template or ""), var_map)
            if errors:
                return {"ok": False, "errors": errors}
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "errors": [f"run_validation failed: {e}"]}

    # ------------------------------------------- stubs (implemented later)
    def run_prompt(self, template: str, result: str, variables: list) -> dict:
        return _stub()

    def start_run(self, template: str, result: str, variables: list, hl: bool) -> dict:
        return _stub()

    def continue_hl(self, response_text: str) -> Optional[dict]:
        return _stub()

    def get_state(self) -> dict:
        return _stub()

    # ------------------------------------------------- Phase 3 — clipboard
    def get_clipboard(self) -> dict:
        """Return the current system clipboard text.

        Uses ``QApplication.clipboard().text()`` — works reliably in the
        Qt WebEngine backend without triggering permission dialogs (unlike
        ``navigator.clipboard.readText()``).

        ``{"ok": True, "text": "..."}`` or ``{"ok": False, "error": ...}``.
        """
        try:
            if not _QT_AVAILABLE:
                return {"ok": False, "error": "Qt clipboard not available"}
            app = QApplication.instance()
            if app is None:
                return {"ok": False, "error": "QApplication not running"}
            text = app.clipboard().text()
            return {"ok": True, "text": text}
        except Exception as e:
            return {"ok": False, "error": f"get_clipboard failed: {e}"}

    def copy_to_clipboard(self, text: str) -> dict:
        """Write ``text`` to the system clipboard.

        ``{"ok": True}`` or ``{"ok": False, "error": ...}``.
        """
        try:
            if not _QT_AVAILABLE:
                return {"ok": False, "error": "Qt clipboard not available"}
            app = QApplication.instance()
            if app is None:
                return {"ok": False, "error": "QApplication not running"}
            app.clipboard().setText(str(text or ""))
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": f"copy_to_clipboard failed: {e}"}

    def get_llm_dialog(self) -> dict:
        return _stub()

    def get_variable_dialog(self) -> dict:
        return _stub()
