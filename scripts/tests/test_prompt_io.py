#!/usr/bin/env python3
"""tests.test_prompt_io — unit tests for core.prompt_io (T3.8).

Covers:
  - extract_variables_from_prompt (basic, repeated vars, order preservation, no vars)
  - write_run_files (fresh workspace, file contents, returned paths)
  - build_final_prompt (substitution, missing variable file → empty string)
  - load_expected_result (reads result.txt, stripped)
  - UTF-8 round-trip
"""

import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.prompt_io import (
    extract_variables_from_prompt,
    write_run_files,
    build_final_prompt,
    load_expected_result,
)


# ───────────────────────── extract_variables_from_prompt ─────────────────────────


class TestExtractVariables:
    def test_basic(self):
        assert extract_variables_from_prompt("Hello {{name}}, you are in {{city}}") == ["name", "city"]

    def test_repeated_variables_deduplicated(self):
        text = "Use {{city}} and {{city}} again, plus {{topic}}"
        assert extract_variables_from_prompt(text) == ["city", "topic"]

    def test_order_preserved(self):
        text = "{{z}} {{a}} {{m}} {{a}}"
        assert extract_variables_from_prompt(text) == ["z", "a", "m"]

    def test_no_variables(self):
        assert extract_variables_from_prompt("no vars here") == []

    def test_empty_string(self):
        assert extract_variables_from_prompt("") == []

    def test_underscore_and_digits(self):
        text = "{{my_var_2}} {{a1}}"
        assert extract_variables_from_prompt(text) == ["my_var_2", "a1"]

    def test_single_brace_not_matched(self):
        assert extract_variables_from_prompt("{name}") == []

    def test_triple_brace_inner_match(self):
        # {{{name}}} — the inner {{name}} matches
        assert extract_variables_from_prompt("{{{name}}}") == ["name"]

    def test_cyrillic_names(self):
        text = "Привет {{город}}, тема {{тема}}"
        assert extract_variables_from_prompt(text) == ["город", "тема"]


# ───────────────────────── write_run_files ─────────────────────────


class TestWriteRunFiles:
    def test_writes_all_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            variables = {"city": "Moscow", "topic": "testing"}
            template = "Greet {{city}} about {{topic}}"
            expected = "expected output"
            paths = write_run_files(tmp, template, expected, variables)

            # prompt.txt
            assert os.path.isfile(os.path.join(tmp, "prompt.txt"))
            assert open(os.path.join(tmp, "prompt.txt"), encoding="utf-8").read() == template

            # result.txt
            assert os.path.isfile(os.path.join(tmp, "result.txt"))
            assert open(os.path.join(tmp, "result.txt"), encoding="utf-8").read() == expected

            # variable files
            for name, value in variables.items():
                path = os.path.join(tmp, name + ".txt")
                assert os.path.isfile(path)
                assert open(path, encoding="utf-8").read() == value

            # returned paths list has correct length (prompt + result + len(vars))
            assert len(paths) == 2 + len(variables)

    def test_fresh_workspace(self):
        """Second call wipes the previous workspace."""
        with tempfile.TemporaryDirectory() as tmp:
            # First write
            write_run_files(tmp, "v1", "r1", {"x": "1"})
            assert os.path.isfile(os.path.join(tmp, "x.txt"))

            # Second write with different vars — x.txt should be gone
            write_run_files(tmp, "v2", "r2", {"y": "2"})
            assert not os.path.exists(os.path.join(tmp, "x.txt"))
            assert os.path.isfile(os.path.join(tmp, "y.txt"))

    def test_no_variables(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_run_files(tmp, "plain prompt", "result", {})
            assert os.path.isfile(os.path.join(tmp, "prompt.txt"))
            assert os.path.isfile(os.path.join(tmp, "result.txt"))
            assert len(paths) == 2

    def test_cyrillic_variable_names_and_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            variables = {"город": "Москва"}
            template = "Город: {{город}}"
            write_run_files(tmp, template, "", variables)
            assert open(os.path.join(tmp, "город.txt"), encoding="utf-8").read() == "Москва"


# ───────────────────────── build_final_prompt ─────────────────────────


class TestBuildFinalPrompt:
    def test_substitution(self):
        with tempfile.TemporaryDirectory() as tmp:
            template = "Hello {{name}}!"
            write_run_files(tmp, template, "result", {"name": "World"})
            final = build_final_prompt(template, tmp)
            assert final == "Hello World!"

    def test_multiple_variables(self):
        with tempfile.TemporaryDirectory() as tmp:
            template = "{{a}} and {{b}}"
            write_run_files(tmp, template, "", {"a": "1", "b": "2"})
            final = build_final_prompt(template, tmp)
            assert final == "1 and 2"

    def test_missing_variable_file_gives_empty_string(self, capsys):
        """If a variable file is absent, the placeholder is replaced with ''
        and a warning is printed to stderr."""
        with tempfile.TemporaryDirectory() as tmp:
            template = "Hi {{name}} from {{city}}"
            # Only write name.txt, not city.txt
            with open(os.path.join(tmp, "name.txt"), "w", encoding="utf-8") as f:
                f.write("Alice")
            # Create prompt.txt and result.txt
            with open(os.path.join(tmp, "prompt.txt"), "w", encoding="utf-8") as f:
                f.write(template)
            with open(os.path.join(tmp, "result.txt"), "w", encoding="utf-8") as f:
                f.write("")

            final = build_final_prompt(template, tmp)
            assert final == "Hi Alice from "
            # A warning should have been emitted to stderr
            captured = capsys.readouterr()
            assert "city" in captured.err

    def test_returns_string_not_path(self):
        """build_final_prompt returns the final prompt string, not a file path."""
        with tempfile.TemporaryDirectory() as tmp:
            template = "Test {{x}}"
            write_run_files(tmp, template, "", {"x": "value"})
            result = build_final_prompt(template, tmp)
            assert isinstance(result, str)
            assert result == "Test value"

    def test_writes_final_prompt_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            template = "Hi {{x}}"
            write_run_files(tmp, template, "", {"x": "Bob"})
            build_final_prompt(template, tmp)
            final_file = os.path.join(tmp, "final_prompt.txt")
            assert os.path.isfile(final_file)
            assert open(final_file, encoding="utf-8").read() == "Hi Bob"

    def test_cyrillic_substitution(self):
        with tempfile.TemporaryDirectory() as tmp:
            template = "Город: {{город}}"
            write_run_files(tmp, template, "", {"город": "Москва"})
            final = build_final_prompt(template, tmp)
            assert final == "Город: Москва"


# ───────────────────────── load_expected_result ─────────────────────────


class TestLoadExpectedResult:
    def test_reads_and_strips(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "result.txt"), "w", encoding="utf-8") as f:
                f.write("  expected answer  \n")
            assert load_expected_result(tmp) == "expected answer"

    def test_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "result.txt"), "w", encoding="utf-8") as f:
                f.write("")
            assert load_expected_result(tmp) == ""

    def test_multiline(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "result.txt"), "w", encoding="utf-8") as f:
                f.write("line1\nline2\nline3")
            assert load_expected_result(tmp) == "line1\nline2\nline3"
