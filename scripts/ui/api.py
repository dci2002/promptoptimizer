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

import os
import threading
import traceback
from collections import deque

from typing import Any, Optional

from core.config import ConfigError, ConfigManager
from core.prompt_io import load_run_data, validate_run_inputs
from core.runner import (
    OptimizationResult,
    ValidationAbort,
    run_optimization,
    run_prompt_check,
)

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

__all__ = ["Api", "GuiHLBridge"]


class GuiHLBridge:
    """Human-in-the-loop bridge for the GUI (Phase 7, T7.1 — architecture §3.4, §3.8).

    Satisfies the :class:`core.agent.HLBridge` protocol. The agent thread calls
    :meth:`wait_for_response` and blocks until the human types a response in
    the UI and clicks "Continue" (``Api.continue_hl`` sets the event).

    Thread safety: all state is guarded by ``self._lock``; the blocking wait
    happens on the event *outside* the lock.
    """

    def __init__(self, api: "Api") -> None:
        self._api = api
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._response = ""

    def wait_for_response(self) -> str:
        """Block until the human supplies a response; return the text."""
        with self._lock:
            # Arm the wait: clear leftovers from a previous iteration, notify
            # the UI (hl_waiting + source_prompt) and reset the event.
            self._event.clear()
            self._response = ""
            self._api._set_hl_wait_state(
                waiting=True,
                source_prompt=self._api._read_workspace_file("srcprompt.txt"),
            )
        self._event.wait()
        with self._lock:
            self._api._set_hl_wait_state(waiting=False)
            return self._response

    def release(self, response_text: str) -> None:
        """Set the response text and release the blocked agent thread.

        Named ``release`` (not ``continue``) because ``continue`` is a Python
        keyword and cannot be used as an attribute name in dot notation.
        """
        with self._lock:
            self._response = response_text
        self._event.set()

    # ---- test helpers -------------------------------------------------
    @property
    def event(self) -> threading.Event:
        return self._event

    @property
    def pending(self) -> str:
        with self._lock:
            return self._response


def _stub() -> dict:
    # Return a fresh dict each call so callers can never mutate a shared object.
    return {"ok": False, "error": "not implemented"}


class Api:
    """JS ↔ Python bridge. Constructed by ``main.app.main()`` with a loaded
    :class:`~core.config.ConfigManager` and an optional workspace base dir."""

    def __init__(self, config: ConfigManager, base_dir: str | None = None) -> None:
        self._config = config
        # Default to <project_root>/workspace (parent of the scripts/ dir that
        # contains this ui/ package).
        self._base_dir = base_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "workspace",
        )
        # ── Execution state (Phase 5, T5.5) ──────────────────────────────
        # All mutable UI-visible state is guarded by a single lock so that
        # the worker thread and the GUI thread (polling get_state) can
        # coexist safely (architecture §3.7).
        self._lock = threading.Lock()
        self._running = False
        self._hl_waiting = False
        self._progress: deque[str] = deque(maxlen=500)
        self._source_prompt = ""
        self._optimization_result = ""
        self._worker: Optional[threading.Thread] = None
        # Phase 7 (T7.1): one shared HL bridge — all HL waits in a session
        # are released via Api.continue_hl on this instance.
        self._hl_bridge: Optional[GuiHLBridge] = GuiHLBridge(self)

    # ------------------------------------------- Phase 7 — HL helpers
    def _set_hl_wait_state(self, waiting: bool, source_prompt: str = "") -> None:
        """Update the HL wait state under the main lock (Phase 7, T7.1)."""
        with self._lock:
            self._hl_waiting = waiting
            if source_prompt:
                self._source_prompt = source_prompt

    def _read_workspace_file(self, name: str) -> str:
        """Read a workspace file (UTF-8); empty string if missing."""
        try:
            with open(os.path.join(self._base_dir, name), "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

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

    def load_run_data(self) -> dict:
        """Load prompt, result and variables from the workspace run files.

        Reads ``workspace/prompt.txt``, ``workspace/result.txt`` and
        ``workspace/{var}.txt`` for each ``{{var}}`` in the prompt.

        ``{"ok": True, "prompt": str, "result": str,
        "variables": [{"name", "value"}, ...]}`` or
        ``{"ok": False, "error": "..."}``.
        """
        try:
            data = load_run_data(self._base_dir)
            return {"ok": True, **data}
        except FileNotFoundError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            return {"ok": False, "error": f"load_run_data failed: {e}"}

    # ------------------------------------------- Phase 4 — Run prompt
    def run_prompt(self, template: str, result: str, variables: list) -> dict:
        """One-shot LLM call (req 3.5): build the final prompt and send it to
        the LLM as Judge.

        ``variables`` is a list of ``{"name": str, "value": str}`` dicts.
        Runs in the GUI thread (a single LLM call is acceptable there).

        Returns ``{"ok": True, "result": str}`` on success, or
        ``{"ok": False, "error": str}`` / ``{"ok": False, "errors": [...]}``.
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

            result = run_prompt_check(
                self._config.data,
                str(template or ""),
                str(result or ""),
                var_map,
                self._base_dir,
            )
            return {"ok": True, "result": result}
        except ValidationAbort as e:
            return {"ok": False, "errors": e.errors}
        except (ValueError, FileNotFoundError) as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            return {"ok": False, "error": f"run_prompt failed: {e}"}

    def start_run(self, template: str, result: str, variables: list, hl: bool) -> dict:
        """Launch the real optimization run (Phase 6, T6.1–T6.3).

        Validates the input (req 3.6) and, on success, spawns a daemon worker
        thread that executes :func:`core.runner.run_optimization` (the full
        ReAct agent loop). Agent events are appended to the lock-protected
        progress deque (max 500 lines) and polled from JS via ``get_state``.

        T6.2 — on worker completion the ``running`` flag is cleared and the
        ``optimization_result`` field is filled with the agent's final
        template (T6.2).

        T6.3 — double-start guard: calling ``start_run`` while a run is in
        progress returns ``{"ok": False, "error": "A run is already in
        progress"}`` immediately.

        Returns ``{"ok": True}`` on success, or
        ``{"ok": False, "error": str}`` / ``{"ok": False, "errors": [...]}``.
        """
        try:
            # T6.3: reject a concurrent run (one worker at a time — architecture §3.7).
            with self._lock:
                if self._running:
                    return {"ok": False, "error": "A run is already in progress"}
                self._running = True
                self._progress.clear()
                self._source_prompt = ""
                self._optimization_result = ""
                self._hl_waiting = False

            # Normalise the JS variables array into a dict.
            var_map: dict[str, str] = {}
            if isinstance(variables, list):
                for row in variables:
                    if isinstance(row, dict):
                        name = str(row.get("name") or "").strip()
                        value = str(row.get("value") or "")
                        if name:
                            var_map[name] = value

            # Validate (req 3.6) — abort before spawning a thread.
            errors = validate_run_inputs(
                self._config.data, str(template or ""), var_map
            )
            if errors:
                with self._lock:
                    self._running = False
                return {"ok": False, "errors": errors}

            # T6.1: real worker — run the full optimization loop.
            def _worker() -> None:
                def _emit(msg: str) -> None:
                    with self._lock:
                        self._progress.append(msg)

                try:
                    res: OptimizationResult = run_optimization(
                        self._config.data,
                        str(template or ""),
                        str(result or ""),
                        var_map,
                        self._base_dir,
                        bool(hl),
                        on_event=_emit,
                        # Phase 7 (T7.2): inject the GUI HL bridge so the
                        # agent can pause for a human response when hl=True.
                        hl_bridge=self._hl_bridge,
                    )
                    _emit(
                        f"Optimization finished: success={res.success}, "
                        f"attempts={res.attempts}, result={len(res.final_result)} chars"
                    )
                    # T6.2: worker completion — store the final template.
                    with self._lock:
                        self._running = False
                        self._optimization_result = res.final_template
                except ValidationAbort as e:
                    _emit("Validation failed inside run: " + "; ".join(e.errors))
                    with self._lock:
                        self._running = False
                except Exception as e:
                    _emit(f"Run failed: {e}")
                    with self._lock:
                        self._running = False
                        self._optimization_result = ""

            self._worker = threading.Thread(target=_worker, daemon=True)
            self._worker.start()
            return {"ok": True}
        except Exception as e:
            with self._lock:
                self._running = False
            return {"ok": False, "error": f"start_run failed: {e}"}

    def continue_hl(self, response_text: str) -> Optional[dict]:
        """Fill the HL response and release the bridge event (Phase 7, T7.6).

        The human types the response in the "Result prompt" field and clicks
        the (now "Continue") start button. ``response_text`` is stored in the
        HL bridge and its event is set, unblocking the agent thread which is
        inside ``wait_for_response``.

        Returns ``{"ok": True}`` on success, or
        ``{"ok": False, "error": str}`` when not waiting / empty input.
        """
        try:
            text = str(response_text or "")
            if not text.strip():
                return {"ok": False, "error": "HL response text is empty"}
            if not self._hl_waiting:
                return {"ok": False, "error": "Not waiting for an HL response"}
            self._hl_bridge.release(text)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": f"continue_hl failed: {e}"}

    def get_state(self) -> dict:
        """Return the current execution state (polled from JS every 500 ms).

        Shape (architecture §3.6):

        ``{"running": bool, "hl_waiting": bool, "progress": str,
        "source_prompt": str, "optimization_result": str,
        "start_button": {"enabled": bool, "text": str}}``
        """
        try:
            with self._lock:
                running = self._running
                hl_waiting = self._hl_waiting
                progress = "\n".join(self._progress)
                source_prompt = self._source_prompt
                optimization_result = self._optimization_result

            # Start button state: disabled while running; "Continue" text
            # while waiting for an HL response (Phase 7).
            if hl_waiting:
                start_text = "Continue"
            else:
                start_text = "Start"
            start_enabled = not running

            return {
                "ok": True,
                "running": running,
                "hl_waiting": hl_waiting,
                "progress": progress,
                "source_prompt": source_prompt,
                "optimization_result": optimization_result,
                "start_button": {
                    "enabled": start_enabled,
                    "text": start_text,
                },
            }
        except Exception as e:
            return {"ok": False, "error": f"get_state failed: {e}"}

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
