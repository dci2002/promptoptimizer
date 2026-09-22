"""core.config — ConfigManager: load/save/validate config.yaml.

Single owner of the ``config.yaml`` file. Responsibilities (architecture §3.1):

- ``load() -> dict`` — read YAML, validate shape, return dict;
- ``save(data: dict)`` — atomic write (tmp + ``os.replace``), UTF-8;
- LLM entry validation: all 5 fields required and non-empty —
  ``name``, ``api_base``, ``api_key``, ``model``, ``temperature`` (float);
- roles must reference existing LLM names;
- ``hl`` is a bool, ``max_attempts`` is a positive int.

This module has **no** pywebview imports — it runs headless and is unit-tested
independently of the UI.
"""

from __future__ import annotations

import copy
import os
import tempfile
from typing import Any, Optional

import yaml

__all__ = ["ConfigError", "validate_llm_entry", "ConfigManager"]


class ConfigError(Exception):
    """Raised when config.yaml is missing, unreadable, or has an invalid schema."""


#: The 5 required fields of an LLM entry (architecture §3.1).
LLM_FIELDS = ("name", "api_base", "api_key", "model", "temperature")

#: Role keys in the config (architecture §6: roles.judge / roles.prompts).
ROLE_KEYS = ("judge", "prompts")


def validate_llm_entry(cfg: Any) -> list[str]:
    """Validate a single LLM entry dict.

    Returns a list of human-readable error strings (empty list == valid):

    - the entry must be a ``dict``;
    - all 5 fields (``name``, ``api_base``, ``api_key``, ``model``,
      ``temperature``) must be present;
    - ``name``, ``api_base``, ``api_key``, ``model`` must be non-empty strings;
    - ``temperature`` must be a ``float`` (or int, coerced to float).
    """
    errors: list[str] = []
    if not isinstance(cfg, dict):
        return ["LLM entry must be a mapping, got " + type(cfg).__name__]

    label = str(cfg.get("name") or "<unnamed>")

    for field in LLM_FIELDS:
        if field not in cfg:
            errors.append(f"LLM '{label}': missing required field '{field}'")
    if errors:
        return errors

    for field in ("name", "api_base", "api_key", "model"):
        value = cfg[field]
        if not isinstance(value, str):
            errors.append(
                f"LLM '{label}': field '{field}' must be a string, "
                f"got {type(value).__name__}"
            )
        elif not value.strip():
            errors.append(f"LLM '{label}': field '{field}' must not be empty")

    temp = cfg["temperature"]
    if isinstance(temp, bool) or not isinstance(temp, (int, float)):
        errors.append(
            f"LLM '{label}': field 'temperature' must be a number (float), "
            f"got {type(temp).__name__}"
        )

    return errors


def _validate_config_data(data: Any) -> list[str]:
    """Validate the whole config dict. Returns a list of errors (empty == valid)."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["config.yaml: top level must be a mapping"]

    # --- llms[] ---
    llms = data.get("llms")
    if llms is None:
        errors.append("config.yaml: missing required key 'llms'")
    elif not isinstance(llms, list):
        errors.append("config.yaml: 'llms' must be a list")
    elif len(llms) == 0:
        errors.append("config.yaml: 'llms' must contain at least one entry")
    else:
        names: set[str] = set()
        for i, entry in enumerate(llms):
            entry_errors = validate_llm_entry(entry)
            errors.extend(entry_errors)
            if isinstance(entry, dict):
                name = entry.get("name")
                if isinstance(name, str) and name.strip():
                    if name in names:
                        errors.append(f"config.yaml: duplicate LLM name '{name}'")
                    names.add(name)

    # --- roles ---
    roles = data.get("roles")
    if roles is not None:
        if not isinstance(roles, dict):
            errors.append("config.yaml: 'roles' must be a mapping")
        else:
            llm_names = set()
            raw_llms = data.get("llms")
            if isinstance(raw_llms, list):
                for entry in raw_llms:
                    if isinstance(entry, dict):
                        n = entry.get("name")
                        if isinstance(n, str) and n.strip():
                            llm_names.add(n)
            for role in ROLE_KEYS:
                value = roles.get(role)
                if value is not None:
                    if not isinstance(value, str):
                        errors.append(
                            f"config.yaml: role '{role}' must be a string (LLM name)"
                        )
                    elif value and llm_names and value not in llm_names:
                        errors.append(
                            f"config.yaml: role '{role}' references unknown LLM '{value}'"
                        )

    # --- hl ---
    hl = data.get("hl")
    if hl is not None and not isinstance(hl, bool):
        errors.append("config.yaml: 'hl' must be a boolean")

    # --- max_attempts ---
    ma = data.get("max_attempts")
    if ma is not None:
        if isinstance(ma, bool) or not isinstance(ma, int):
            errors.append("config.yaml: 'max_attempts' must be an integer")
        elif ma < 1:
            errors.append("config.yaml: 'max_attempts' must be >= 1")

    return errors


class ConfigManager:
    """Load / save / validate ``config.yaml``.

    Parameters
    ----------
    path:
        Path to the YAML file. Defaults to ``config.yaml`` in the current
        working directory (the app launches from the project root).
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or os.path.join(os.getcwd(), "config.yaml")
        self._data: dict = {}
        self.load()

    # ------------------------------------------------------------------ load
    def load(self) -> dict:
        """Read and validate the YAML file; return the config dict.

        Raises
        ------
        ConfigError
            If the file is missing, unreadable, not valid YAML, or fails
            schema validation.
        """
        if not os.path.isfile(self.path):
            raise ConfigError(f"config.yaml not found at: {self.path}")
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ConfigError(f"config.yaml is not valid YAML: {e}") from e
        except OSError as e:
            raise ConfigError(f"config.yaml could not be read: {e}") from e

        if data is None:
            raise ConfigError("config.yaml is empty")

        errors = _validate_config_data(data)
        if errors:
            raise ConfigError("Invalid config.yaml schema:\n  - " + "\n  - ".join(errors))

        # Normalise: ensure temperature is float.
        if isinstance(data, dict) and isinstance(data.get("llms"), list):
            for entry in data["llms"]:
                if isinstance(entry, dict) and isinstance(entry.get("temperature"), (int, float)) \
                        and not isinstance(entry.get("temperature"), bool):
                    entry["temperature"] = float(entry["temperature"])

        self._data = data
        return self._data

    # ------------------------------------------------------------------ save
    def save(self, data: Optional[dict] = None) -> None:
        """Atomically write the config to disk (tmp file + ``os.replace``).

        Parameters
        ----------
        data:
            Config dict to persist. Defaults to the currently loaded data.
            The dict is validated before writing.
        """
        if data is None:
            data = self._data
        errors = _validate_config_data(data)
        if errors:
            raise ConfigError("Refusing to save invalid config:\n  - " + "\n  - ".join(errors))

        # Write to a temp file in the same directory, then replace atomically.
        directory = os.path.dirname(os.path.abspath(self.path)) or "."
        fd, tmp_path = tempfile.mkstemp(prefix=".config_", suffix=".yaml.tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                yaml.safe_dump(
                    data,
                    f,
                    allow_unicode=True,
                    default_flow_style=False,
                    sort_keys=False,
                )
            os.replace(tmp_path, self.path)
        except BaseException:
            # Clean up the temp file on any failure.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        self._data = copy.deepcopy(data)

    # ---------------------------------------------------------------- access
    @property
    def data(self) -> dict:
        """The currently loaded config dict (live reference)."""
        return self._data

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def get_llms(self) -> list[dict]:
        """Return the list of LLM entry dicts."""
        llms = self._data.get("llms")
        return llms if isinstance(llms, list) else []

    def get_roles(self) -> dict:
        """Return the ``roles`` mapping (judge / prompts)."""
        roles = self._data.get("roles")
        return roles if isinstance(roles, dict) else {}

    def get_role(self, role: str) -> str:
        """Return the LLM name assigned to ``role`` ('' if unset)."""
        return self.get_roles().get(role, "") or ""

    def get_hl(self) -> bool:
        return bool(self._data.get("hl", False))

    def get_max_attempts(self) -> int:
        return int(self._data.get("max_attempts", 30))
