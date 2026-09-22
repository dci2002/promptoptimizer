"""scripts/tests/test_config.py — Part 1 (Phase 1, T1.8).

Covers:
- load/save round-trip (data fidelity, UTF-8, atomic replace);
- invalid schema rejection (each failure mode);
- ``validate_llm_entry`` (T1.2) per-field rules.

Run from the project root::

    python -m pytest scripts/tests/test_config.py -v
    # or headless:
    python scripts/tests/test_config.py
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest
import yaml

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from core.config import ConfigError, ConfigManager, validate_llm_entry  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

def _valid_llm(name: str = "test-llm") -> dict:
    return {
        "name": name,
        "api_base": "http://127.0.0.1:1234/v1",
        "api_key": "sk-test",
        "model": "org/model",
        "temperature": 0.5,
    }


def _valid_config() -> dict:
    return {
        "llms": [_valid_llm("a"), _valid_llm("b")],
        "roles": {"judge": "a", "prompts": "b"},
        "hl": False,
        "max_attempts": 30,
    }


@pytest.fixture()
def config_file(tmp_path):
    """Path to a temporary config.yaml with a valid seed."""
    path = tmp_path / "config.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(_valid_config(), f, allow_unicode=True, sort_keys=False)
    return str(path)


# ---------------------------------------------------------------------------
# load()
# ---------------------------------------------------------------------------

class TestLoad:
    def test_load_valid(self, config_file):
        mgr = ConfigManager(path=config_file)
        data = mgr.load()
        assert data["llms"][0]["name"] == "a"
        assert data["roles"]["judge"] == "a"
        assert data["hl"] is False
        assert data["max_attempts"] == 30

    def test_load_normalizes_temperature_to_float(self, tmp_path):
        cfg = _valid_config()
        cfg["llms"][0]["temperature"] = 0  # int in YAML
        path = str(tmp_path / "config.yaml")
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
        data = ConfigManager(path=path).load()
        assert isinstance(data["llms"][0]["temperature"], float)

    def test_load_missing_file(self, tmp_path):
        with pytest.raises(ConfigError, match="not found"):
            ConfigManager(path=str(tmp_path / "nope.yaml"))

    def test_load_invalid_yaml(self, tmp_path):
        path = str(tmp_path / "config.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write("llms: [unclosed\n  bad::: [")
        with pytest.raises(ConfigError, match="not valid YAML"):
            ConfigManager(path=path)

    def test_load_empty_file(self, tmp_path):
        p = tmp_path / "config.yaml"
        p.touch()
        with pytest.raises(ConfigError, match="empty"):
            ConfigManager(path=str(p))


# ---------------------------------------------------------------------------
# invalid schema rejection
# ---------------------------------------------------------------------------

class TestInvalidSchema:
    def test_missing_llms_key(self, tmp_path):
        path = str(tmp_path / "config.yaml")
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump({"roles": {}, "hl": False}, f)
        with pytest.raises(ConfigError, match="missing required key 'llms'"):
            ConfigManager(path=path)

    def test_llms_not_a_list(self, tmp_path):
        path = str(tmp_path / "config.yaml")
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump({"llms": "nope"}, f)
        with pytest.raises(ConfigError, match="'llms' must be a list"):
            ConfigManager(path=path)

    def test_llms_empty_list(self, tmp_path):
        path = str(tmp_path / "config.yaml")
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump({"llms": []}, f)
        with pytest.raises(ConfigError, match="at least one"):
            ConfigManager(path=path)

    def test_duplicate_llm_names(self, tmp_path):
        cfg = _valid_config()
        cfg["llms"][1]["name"] = "a"
        cfg["roles"]["prompts"] = "a"
        path = str(tmp_path / "config.yaml")
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
        with pytest.raises(ConfigError, match="duplicate LLM name"):
            ConfigManager(path=path)

    def test_role_references_unknown_llm(self, tmp_path):
        cfg = _valid_config()
        cfg["roles"]["judge"] = "ghost"
        path = str(tmp_path / "config.yaml")
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
        with pytest.raises(ConfigError, match="unknown LLM"):
            ConfigManager(path=path)

    def test_hl_not_bool(self, tmp_path):
        cfg = _valid_config()
        cfg["hl"] = "yes"
        path = str(tmp_path / "config.yaml")
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
        with pytest.raises(ConfigError, match="'hl' must be a boolean"):
            ConfigManager(path=path)

    def test_max_attempts_not_int(self, tmp_path):
        cfg = _valid_config()
        cfg["max_attempts"] = "many"
        path = str(tmp_path / "config.yaml")
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
        with pytest.raises(ConfigError, match="'max_attempts' must be an integer"):
            ConfigManager(path=path)

    def test_max_attempts_zero(self, tmp_path):
        cfg = _valid_config()
        cfg["max_attempts"] = 0
        path = str(tmp_path / "config.yaml")
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
        with pytest.raises(ConfigError, match="must be >= 1"):
            ConfigManager(path=path)


# ---------------------------------------------------------------------------
# save() / round-trip
# ---------------------------------------------------------------------------

class TestSave:
    def test_round_trip(self, config_file):
        mgr = ConfigManager(path=config_file)
        data = mgr.load()
        # mutate
        data["llms"].append(_valid_llm("c"))
        data["roles"]["judge"] = "c"
        data["hl"] = True
        mgr.save(data)

        mgr2 = ConfigManager(path=config_file)
        d2 = mgr2.load()
        assert [e["name"] for e in d2["llms"]] == ["a", "b", "c"]
        assert d2["roles"]["judge"] == "c"
        assert d2["hl"] is True

    def test_save_is_atomic_replace(self, config_file):
        """After save, the file must be a regular file (tmp replaced)."""
        mgr = ConfigManager(path=config_file)
        mgr.save()
        assert os.path.isfile(config_file)
        # and no leftover temp files in the directory
        leftovers = [
            n for n in os.listdir(os.path.dirname(config_file))
            if n.startswith(".config_") and n.endswith(".yaml.tmp")
        ]
        assert leftovers == []

    def test_save_invalid_data_rejected(self, config_file):
        mgr = ConfigManager(path=config_file)
        bad = dict(mgr.data)
        bad["llms"] = []
        with pytest.raises(ConfigError, match="Refusing to save"):
            mgr.save(bad)

    def test_save_utf8(self, tmp_path):
        cfg = _valid_config()
        cfg["llms"][0]["model"] = "модель/модель-тест"
        path = str(tmp_path / "config.yaml")
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
        mgr = ConfigManager(path=path)
        mgr.save()
        with open(path, "r", encoding="utf-8") as f:
            reloaded = yaml.safe_load(f)
        assert reloaded["llms"][0]["model"] == "модель/модель-тест"


# ---------------------------------------------------------------------------
# validate_llm_entry (T1.2)
# ---------------------------------------------------------------------------

class TestValidateLlmEntry:
    def test_valid_entry(self):
        assert validate_llm_entry(_valid_llm()) == []

    def test_not_a_dict(self):
        errs = validate_llm_entry("not a dict")
        assert any("must be a mapping" in e for e in errs)

    def test_missing_field(self):
        cfg = _valid_llm()
        del cfg["api_key"]
        errs = validate_llm_entry(cfg)
        assert any("missing required field 'api_key'" in e for e in errs)

    def test_empty_string_field(self):
        cfg = _valid_llm()
        cfg["api_base"] = "   "
        errs = validate_llm_entry(cfg)
        assert any("'api_base' must not be empty" in e for e in errs)

    def test_non_string_name(self):
        cfg = _valid_llm()
        cfg["name"] = 42
        errs = validate_llm_entry(cfg)
        assert any("'name' must be a string" in e for e in errs)

    def test_temperature_not_number(self):
        cfg = _valid_llm()
        cfg["temperature"] = "hot"
        errs = validate_llm_entry(cfg)
        assert any("'temperature' must be a number" in e for e in errs)

    def test_temperature_bool_rejected(self):
        cfg = _valid_llm()
        cfg["temperature"] = True  # bool is an int subclass — must be rejected
        errs = validate_llm_entry(cfg)
        assert any("'temperature' must be a number" in e for e in errs)

    def test_temperature_int_accepted(self):
        cfg = _valid_llm()
        cfg["temperature"] = 1
        assert validate_llm_entry(cfg) == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
