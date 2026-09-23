"""core.config — ConfigManager: load/save/validate config.yaml.

Single owner of the ``config.yaml`` file. Responsibilities (architecture §3.1):

- ``load() -> dict`` — read YAML, validate shape, return dict;
- ``save(data: dict)`` — atomic write (tmp + ``os.replace``), UTF-8;
- LLM entry validation: all 5 fields required and non-empty —
  ``name``, ``api_base``, ``api_key``, ``model``, ``temperature`` (float);
- roles must reference existing LLM names;
- ``hl`` is a bool, ``max_attempts`` is a positive int.

Phase 2 adds LLM CRUD (``list_llms`` / ``add_llm`` / ``update_llm`` /
``remove_llm``) and role/HL setters on top of load/save:

- duplicate LLM names are rejected;
- ``update_llm`` may rename an entry — roles referencing the old name are
  re-pointed to the new one;
- ``remove_llm`` clears any role that references the removed LLM;
- every mutation is persisted atomically via :meth:`ConfigManager.save`.

This module has **no** pywebview imports — it runs headless and is unit-tested
independently of the UI.
"""

from __future__ import annotations

import copy
import os
import tempfile
from typing import Any, Optional

import yaml

__all__ = ["ConfigError", "validate_llm_entry", "normalize_llm_entry", "ConfigManager"]


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


def normalize_llm_entry(cfg: dict) -> dict:
    """Return a clean copy of an LLM entry (Phase 2).

    - all 5 fields are stripped strings (``temperature`` coerced to float);
    - extra keys from unknown producers (e.g. the JS bridge) are dropped.
    """
    errors = validate_llm_entry(cfg)
    if errors:
        raise ConfigError("Invalid LLM entry:\n  - " + "\n  - ".join(errors))
    return {
        "name": str(cfg["name"]).strip(),
        "api_base": str(cfg["api_base"]).strip(),
        "api_key": str(cfg["api_key"]).strip(),
        "model": str(cfg["model"]).strip(),
        "temperature": float(cfg["temperature"]),
    }


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

    # -------------------------------------------------------------- LLM CRUD
    def _find_llm(self, name: str) -> int:
        """Index of the LLM entry with ``name``; -1 if absent."""
        for i, entry in enumerate(self.get_llms()):
            if isinstance(entry, dict) and entry.get("name") == name:
                return i
        return -1

    def list_llms(self) -> list[dict]:
        """Return a deep copy of all LLM entries (safe to hand to the bridge)."""
        return copy.deepcopy(self.get_llms())

    def add_llm(self, cfg: dict) -> dict:
        """Add a new LLM entry; reject duplicate names.

        The entry is validated and normalised first. The mutation is
        persisted atomically. Returns the stored (normalised) entry.

        Raises
        ------
        ConfigError
            If the entry is invalid or the name already exists.
        """
        entry = normalize_llm_entry(cfg)
        if self._find_llm(entry["name"]) != -1:
            raise ConfigError(f"LLM name already exists: '{entry['name']}'")
        data = copy.deepcopy(self._data)
        data.setdefault("llms", []).append(entry)
        self.save(data)
        return copy.deepcopy(entry)

    def update_llm(self, old_name: str, cfg: dict) -> dict:
        """Update the LLM named ``old_name`` with the (normalised) ``cfg``.

        The entry may be renamed (``cfg['name'] != old_name``): in that case
        every role referencing the old name is re-pointed to the new name.
        The mutation is persisted atomically. Returns the stored entry.

        Raises
        ------
        ConfigError
            If ``old_name`` is unknown, the entry is invalid, or the new name
            collides with another existing LLM.
        """
        idx = self._find_llm(old_name)
        if idx == -1:
            raise ConfigError(f"LLM not found: '{old_name}'")
        entry = normalize_llm_entry(cfg)
        new_name = entry["name"]
        if new_name != old_name and self._find_llm(new_name) != -1:
            raise ConfigError(f"LLM name already exists: '{new_name}'")

        data = copy.deepcopy(self._data)
        data["llms"][idx] = entry
        if new_name != old_name:
            roles = data.get("roles")
            if isinstance(roles, dict):
                for role in ROLE_KEYS:
                    if roles.get(role) == old_name:
                        roles[role] = new_name
        self.save(data)
        return copy.deepcopy(entry)

    def remove_llm(self, name: str) -> list[str]:
        """Remove the LLM named ``name``; cascade-clear roles referencing it.

        The mutation is persisted atomically. Returns the list of role keys
        that were cleared (e.g. ``["judge"]``).

        Raises
        ------
        ConfigError
            If ``name`` is unknown, or it is the last remaining LLM
            (the config schema requires at least one entry).
        """
        idx = self._find_llm(name)
        if idx == -1:
            raise ConfigError(f"LLM not found: '{name}'")
        llms = self.get_llms()
        if len(llms) <= 1:
            raise ConfigError(
                "Cannot remove the last remaining LLM — at least one entry is required"
            )

        data = copy.deepcopy(self._data)
        data["llms"].pop(idx)
        cleared: list[str] = []
        roles = data.get("roles")
        if isinstance(roles, dict):
            for role in ROLE_KEYS:
                if roles.get(role) == name:
                    roles[role] = ""
                    cleared.append(role)
        self.save(data)
        return cleared

    # ---------------------------------------------------------- roles / HL
    def set_role(self, role: str, name: str) -> dict:
        """Assign LLM ``name`` to ``role`` and persist.

        An empty ``name`` unsets the role. The name must reference an
        existing LLM. Returns the updated ``roles`` mapping.

        Raises
        ------
        ConfigError
            If ``role`` is not in :data:`ROLE_KEYS` or ``name`` is unknown.
        """
        if role not in ROLE_KEYS:
            raise ConfigError(f"Unknown role: '{role}' (expected one of {ROLE_KEYS})")
        name = (name or "").strip()
        if name and self._find_llm(name) == -1:
            raise ConfigError(f"Cannot assign role '{role}': unknown LLM '{name}'")

        data = copy.deepcopy(self._data)
        data.setdefault("roles", {})[role] = name
        self.save(data)
        return copy.deepcopy(data["roles"])

    def set_hl(self, flag: bool) -> bool:
        """Set the Human-in-the-loop flag and persist. Returns the new value."""
        if not isinstance(flag, bool):
            raise ConfigError("hl flag must be a boolean")
        data = copy.deepcopy(self._data)
        data["hl"] = flag
        self.save(data)
        return flag
