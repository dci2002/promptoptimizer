"""scripts/tests/test_config.py — Part 1 (Phase 1, T1.8) + Part 2 (Phase 2, T2.7).

Covers:
- load/save round-trip (data fidelity, UTF-8, atomic replace);
- invalid schema rejection (each failure mode);
- ``validate_llm_entry`` (T1.2) per-field rules;
- LLM CRUD (T2.1): add/update/remove, duplicate rejection, rename cascade,
  remove cascade to roles, last-LLM protection;
- role/HL accessors (T2.2): ``set_role``, ``get_role``, ``set_hl``,
  ``get_hl`` with persistence on every mutation;
- ``normalize_llm_entry`` (T2.1 support).

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

from core.config import (  # noqa: E402
    ConfigError,
    ConfigManager,
    normalize_llm_entry,
    validate_llm_entry,
)


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


def _read_config(path: str) -> dict:
    """Read a config file directly from disk (independence check)."""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


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


# ---------------------------------------------------------------------------
# normalize_llm_entry (Phase 2 support)
# ---------------------------------------------------------------------------

class TestNormalizeLlmEntry:
    def test_strips_and_coerces(self):
        entry = normalize_llm_entry({
            "name": "  x  ",
            "api_base": " http://h:1/v1 ",
            "api_key": " k ",
            "model": " m ",
            "temperature": 1,  # int -> float
        })
        assert entry == {
            "name": "x",
            "api_base": "http://h:1/v1",
            "api_key": "k",
            "model": "m",
            "temperature": 1.0,
        }

    def test_drops_extra_keys(self):
        entry = normalize_llm_entry({**_valid_llm(), "_old_name": "zz", "extra": 1})
        assert set(entry.keys()) == {"name", "api_base", "api_key", "model", "temperature"}

    def test_invalid_raises(self):
        with pytest.raises(ConfigError):
            normalize_llm_entry({**_valid_llm(), "temperature": "hot"})


# ---------------------------------------------------------------------------
# LLM CRUD (Phase 2, T2.1)
# ---------------------------------------------------------------------------

class TestLlmCrud:
    def test_list_llms_returns_copies(self, config_file):
        mgr = ConfigManager(path=config_file)
        llms = mgr.list_llms()
        assert [e["name"] for e in llms] == ["a", "b"]
        # mutating the returned copy must not affect the manager
        llms[0]["name"] = "mutated"
        assert mgr.list_llms()[0]["name"] == "a"

    def test_add_llm_persists(self, config_file):
        mgr = ConfigManager(path=config_file)
        entry = mgr.add_llm(_valid_llm("c"))
        assert entry["name"] == "c"
        assert [e["name"] for e in mgr.list_llms()] == ["a", "b", "c"]
        # persisted to disk
        on_disk = _read_config(config_file)
        assert [e["name"] for e in on_disk["llms"]] == ["a", "b", "c"]
        assert on_disk["llms"][2]["temperature"] == 0.5

    def test_add_llm_duplicate_rejected(self, config_file):
        mgr = ConfigManager(path=config_file)
        with pytest.raises(ConfigError, match="already exists"):
            mgr.add_llm(_valid_llm("a"))
        # and the file is untouched
        on_disk = _read_config(config_file)
        assert [e["name"] for e in on_disk["llms"]] == ["a", "b"]

    def test_add_llm_invalid_rejected(self, config_file):
        mgr = ConfigManager(path=config_file)
        with pytest.raises(ConfigError, match="Invalid LLM entry"):
            mgr.add_llm({**_valid_llm("c"), "temperature": "hot"})
        assert [e["name"] for e in mgr.list_llms()] == ["a", "b"]

    def test_update_llm_in_place(self, config_file):
        mgr = ConfigManager(path=config_file)
        entry = mgr.update_llm("a", {**_valid_llm("a"), "temperature": 0.9})
        assert entry["temperature"] == 0.9
        on_disk = _read_config(config_file)
        assert on_disk["llms"][0]["temperature"] == 0.9
        assert [e["name"] for e in on_disk["llms"]] == ["a", "b"]

    def test_update_llm_unknown_name(self, config_file):
        mgr = ConfigManager(path=config_file)
        with pytest.raises(ConfigError, match="LLM not found"):
            mgr.update_llm("ghost", _valid_llm("ghost"))

    def test_update_llm_rename_cascades_roles(self, config_file):
        """Renaming 'a' (judge) to 'renamed' must re-point roles.judge."""
        mgr = ConfigManager(path=config_file)
        entry = mgr.update_llm("a", {**_valid_llm("renamed"), "temperature": 0.1})
        assert entry["name"] == "renamed"
        assert mgr.get_role("judge") == "renamed"
        assert mgr.get_role("prompts") == "b"
        on_disk = _read_config(config_file)
        assert on_disk["roles"]["judge"] == "renamed"
        assert on_disk["roles"]["prompts"] == "b"
        assert [e["name"] for e in on_disk["llms"]] == ["renamed", "b"]

    def test_update_llm_rename_collision_rejected(self, config_file):
        mgr = ConfigManager(path=config_file)
        with pytest.raises(ConfigError, match="already exists"):
            mgr.update_llm("a", _valid_llm("b"))
        on_disk = _read_config(config_file)
        assert [e["name"] for e in on_disk["llms"]] == ["a", "b"]

    def test_remove_llm_clears_roles(self, config_file):
        """Removing 'a' (judge) must clear roles.judge (set to empty string)."""
        mgr = ConfigManager(path=config_file)
        cleared = mgr.remove_llm("a")
        assert cleared == ["judge"]
        assert mgr.get_role("judge") == ""
        assert mgr.get_role("prompts") == "b"
        on_disk = _read_config(config_file)
        assert on_disk["roles"]["judge"] == ""
        assert on_disk["roles"]["prompts"] == "b"
        assert [e["name"] for e in on_disk["llms"]] == ["b"]

    def test_remove_llm_both_roles(self, tmp_path):
        """An LLM referenced by BOTH roles: both get cleared."""
        cfg = _valid_config()
        cfg["roles"] = {"judge": "a", "prompts": "a"}
        path = str(tmp_path / "config.yaml")
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
        mgr = ConfigManager(path=path)
        cleared = mgr.remove_llm("a")
        assert sorted(cleared) == ["judge", "prompts"]
        on_disk = _read_config(path)
        assert on_disk["roles"]["judge"] == ""
        assert on_disk["roles"]["prompts"] == ""

    def test_remove_llm_unknown_name(self, config_file):
        mgr = ConfigManager(path=config_file)
        with pytest.raises(ConfigError, match="LLM not found"):
            mgr.remove_llm("ghost")

    def test_remove_last_llm_rejected(self, tmp_path):
        """The config schema requires >= 1 LLM entry."""
        cfg = _valid_config()
        cfg["llms"] = [_valid_llm("only")]
        cfg["roles"] = {"judge": "only", "prompts": "only"}
        path = str(tmp_path / "config.yaml")
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
        mgr = ConfigManager(path=path)
        with pytest.raises(ConfigError, match="last remaining LLM"):
            mgr.remove_llm("only")

    def test_crud_is_atomic(self, config_file):
        """Mutations leave no temp files and keep a valid file at all times."""
        mgr = ConfigManager(path=config_file)
        mgr.add_llm(_valid_llm("c"))
        mgr.update_llm("c", {**_valid_llm("c"), "temperature": 0.2})
        mgr.remove_llm("c")
        leftovers = [
            n for n in os.listdir(os.path.dirname(config_file))
            if n.startswith(".config_") and n.endswith(".yaml.tmp")
        ]
        assert leftovers == []
        # file is still valid YAML with the original two LLMs
        on_disk = _read_config(config_file)
        assert [e["name"] for e in on_disk["llms"]] == ["a", "b"]


# ---------------------------------------------------------------------------
# role / HL accessors (Phase 2, T2.2)
# ---------------------------------------------------------------------------

class TestRoleHlAccessors:
    def test_set_role_persists(self, config_file):
        mgr = ConfigManager(path=config_file)
        roles = mgr.set_role("judge", "b")
        assert roles == {"judge": "b", "prompts": "b"}
        on_disk = _read_config(config_file)
        assert on_disk["roles"]["judge"] == "b"

    def test_set_role_empty_unsets(self, config_file):
        mgr = ConfigManager(path=config_file)
        roles = mgr.set_role("judge", "")
        assert roles["judge"] == ""
        on_disk = _read_config(config_file)
        assert on_disk["roles"]["judge"] == ""

    def test_set_role_unknown_llm_rejected(self, config_file):
        mgr = ConfigManager(path=config_file)
        with pytest.raises(ConfigError, match="unknown LLM"):
            mgr.set_role("judge", "ghost")
        on_disk = _read_config(config_file)
        assert on_disk["roles"]["judge"] == "a"

    def test_set_role_unknown_role_rejected(self, config_file):
        mgr = ConfigManager(path=config_file)
        with pytest.raises(ConfigError, match="Unknown role"):
            mgr.set_role("nonsense", "a")

    def test_set_hl_persists(self, config_file):
        mgr = ConfigManager(path=config_file)
        assert mgr.get_hl() is False
        assert mgr.set_hl(True) is True
        assert mgr.get_hl() is True
        on_disk = _read_config(config_file)
        assert on_disk["hl"] is True
        # back to false
        assert mgr.set_hl(False) is False
        assert _read_config(config_file)["hl"] is False

    def test_set_hl_requires_bool(self, config_file):
        mgr = ConfigManager(path=config_file)
        with pytest.raises(ConfigError, match="boolean"):
            mgr.set_hl("yes")  # type: ignore[arg-type]

    def test_accessors_after_reload(self, config_file):
        """A fresh ConfigManager sees the persisted role/HL state."""
        mgr = ConfigManager(path=config_file)
        mgr.set_role("judge", "b")
        mgr.set_hl(True)
        mgr2 = ConfigManager(path=config_file)
        assert mgr2.get_role("judge") == "b"
        assert mgr2.get_role("prompts") == "b"
        assert mgr2.get_hl() is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
