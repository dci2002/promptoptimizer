#!/usr/bin/env python3
"""Headless smoke test for the core package (Phase 3, T3.4).

Verifies config + prompt_io without a window:

1. Loads config.yaml via ConfigManager;
2. Extracts variables from a sample template;
3. Writes workspace/ files (prompt.txt, result.txt, {var}.txt);
4. Builds the final prompt (substitution);
5. Validates run inputs (req 3.6 matrix);
6. Prints a summary.

Run from the project root:
    python scripts/dev/smoke_core.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def _ensure_scripts_on_path() -> None:
    """Put ``scripts/`` on sys.path so ``import core`` works from anywhere."""
    project_root = Path(__file__).resolve().parents[2]
    scripts_dir = project_root / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


def main() -> int:
    _ensure_scripts_on_path()

    from core.config import ConfigManager
    from core.prompt_io import (
        build_final_prompt,
        extract_variables_from_prompt,
        load_expected_result,
        validate_run_inputs,
        write_run_files,
    )

    failures: list[str] = []

    # ── 1. Load config ──────────────────────────────────────────────────
    print("1. Loading config.yaml …")
    try:
        cfg = ConfigManager()
        data = cfg.data
        llms = data.get("llms", [])
        roles = data.get("roles", {})
        print(f"   OK: {len(llms)} LLM(s), roles: {roles}")
    except Exception as e:
        print(f"   FAIL: {e}")
        return 1

    # ── 2. Extract variables from a sample template ─────────────────────
    print("2. Extracting variables …")
    sample_template = (
        "You are a helpful assistant. Today's date is {{date}}.\n"
        "The user is in {{city}} and wants to talk about {{topic}}.\n"
        "Please mention {{city}} again at the end."
    )
    vars_extracted = extract_variables_from_prompt(sample_template)
    expected_vars = {"date", "city", "topic"}
    if set(vars_extracted) != expected_vars:
        failures.append(
            f"extract_variables: got {vars_extracted}, expected {sorted(expected_vars)}"
        )
    else:
        print(f"   OK: {vars_extracted}")

    # ── 3. Write workspace files ────────────────────────────────────────
    print("3. Writing workspace files …")
    tmp_dir = tempfile.mkdtemp(prefix="smoke_core_")
    ws = os.path.join(tmp_dir, "workspace")
    sample_vars = {
        "date": "2026-09-23",
        "city": "Moscow",
        "topic": "AI in education",
    }
    expected_result = "A helpful response about AI in education in Moscow."
    try:
        written = write_run_files(ws, sample_template, expected_result, sample_vars)
        for p in written:
            if not os.path.isfile(p):
                failures.append(f"write_run_files: missing {p}")
        print(f"   OK: {len(written)} files written to {ws}")
    except Exception as e:
        failures.append(f"write_run_files: {e}")
        print(f"   FAIL: {e}")

    # ── 4. Build final prompt ───────────────────────────────────────────
    print("4. Building final prompt …")
    try:
        final = build_final_prompt(sample_template, ws)
        if "{{" in final:
            failures.append("build_final_prompt: unresolved placeholders remain")
        if "Moscow" not in final or "2026-09-23" not in final:
            failures.append("build_final_prompt: substitution values missing")
        # final_prompt.txt must exist
        fp_path = os.path.join(ws, "final_prompt.txt")
        if not os.path.isfile(fp_path):
            failures.append("build_final_prompt: final_prompt.txt not written")
        print(f"   OK: final prompt length = {len(final)}")
    except Exception as e:
        failures.append(f"build_final_prompt: {e}")
        print(f"   FAIL: {e}")

    # ── 5. Validate run inputs (req 3.6) ────────────────────────────────
    print("5. Validating run inputs …")
    # 5a. All valid → no errors
    try:
        errs = validate_run_inputs(data, sample_template, sample_vars)
        if errs:
            failures.append(f"validate (all valid): unexpected errors {errs}")
        else:
            print("   OK: all-valid case → no errors")
    except Exception as e:
        failures.append(f"validate (all valid): {e}")
        print(f"   FAIL: {e}")

    # 5b. Missing role → error
    try:
        bad_cfg = {"llms": llms, "roles": {"judge": "", "prompts": ""}}
        errs = validate_run_inputs(bad_cfg, sample_template, sample_vars)
        if not errs:
            failures.append("validate (missing roles): expected errors, got none")
        else:
            print(f"   OK: missing roles → {len(errs)} error(s)")
    except Exception as e:
        failures.append(f"validate (missing roles): {e}")
        print(f"   FAIL: {e}")

    # 5c. Empty prompt → error
    try:
        errs = validate_run_inputs(data, "", sample_vars)
        if not errs:
            failures.append("validate (empty prompt): expected errors, got none")
        else:
            print(f"   OK: empty prompt → {len(errs)} error(s)")
    except Exception as e:
        failures.append(f"validate (empty prompt): {e}")
        print(f"   FAIL: {e}")

    # 5d. Missing variable value → error
    try:
        partial_vars = {"date": "2026-09-23", "city": "Moscow"}  # no topic
        errs = validate_run_inputs(data, sample_template, partial_vars)
        if not errs:
            failures.append("validate (missing var): expected errors, got none")
        else:
            print(f"   OK: missing variable → {len(errs)} error(s)")
    except Exception as e:
        failures.append(f"validate (missing var): {e}")
        print(f"   FAIL: {e}")

    # 5e. Empty variable value → error
    try:
        empty_val = {"date": "2026-09-23", "city": "", "topic": "AI"}
        errs = validate_run_inputs(data, sample_template, empty_val)
        if not errs:
            failures.append("validate (empty var value): expected errors, got none")
        else:
            print(f"   OK: empty variable value → {len(errs)} error(s)")
    except Exception as e:
        failures.append(f"validate (empty var value): {e}")
        print(f"   FAIL: {e}")

    # ── 6. Load expected result ─────────────────────────────────────────
    print("6. Loading expected result …")
    try:
        loaded = load_expected_result(ws)
        if loaded != expected_result:
            failures.append(f"load_expected_result: got '{loaded}', expected '{expected_result}'")
        else:
            print(f"   OK: '{loaded}'")
    except Exception as e:
        failures.append(f"load_expected_result: {e}")
        print(f"   FAIL: {e}")

    # ── Cleanup ─────────────────────────────────────────────────────────
    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)

    # ── Summary ─────────────────────────────────────────────────────────
    print()
    if failures:
        print("SMOKE FAILED:")
        for f in failures:
            print(f"  ✗ {f}")
        return 1
    print("SMOKE OK: all Phase 1–3 core checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
