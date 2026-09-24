"""dev.probe_ui — headless UI probe (dev helper, not part of the app).

Launches the real window offscreen, waits for the page + bridge to load,
then drives the DOM via ``window.evaluate_js`` and exits 0 on success.

Phase 1 checks:
- status line shows the parsed config (bridge works);
- both tabs exist and switching to Settings activates the right panel.

Phase 2 checks (scenarios 3.1–3.4 of the GUI test):
- the LLM table is populated from config;
- open the Add dialog, fill it, Save → new row appears;
- open the Edit dialog (double-click a row), change temperature, Save → row updated;
- select a row and Remove (confirm stubbed) → row gone;
- role dropdowns contain all LLM names;
- HL checkbox toggles.

Phase 5 check (dry-run logging):
- click #btn-start → Progress field fills with canned [DRY-RUN] lines;
- Start button disables during the run and re-enables after;
- Optimization result field stays empty (dry run).

NOTE: this probe runs against a **copy** of config.yaml in a temp dir and
points ConfigManager at that copy, so the real project config.yaml is never
touched.

Usage::

    DISPLAY=:0.0 QT_QPA_PLATFORM=offscreen QTWEBENGINE_DISABLE_GPU=1 \
        .venv/bin/python scripts/dev/probe_ui.py
"""

from __future__ import annotations

import os
import shutil
import signal
import sys
import tempfile
import threading
import time

import yaml

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import webview  # noqa: E402

from core.config import ConfigManager  # noqa: E402
from ui.api import Api  # noqa: E402

PROJECT_ROOT = os.path.dirname(_SCRIPTS_DIR)
INDEX_HTML = os.path.join(_SCRIPTS_DIR, "ui", "js", "index.html")
REAL_CONFIG = os.path.join(PROJECT_ROOT, "config.yaml")

# Global hard timeout: if the probe hasn't finished within this many seconds,
# kill the process.  Prevents indefinite hangs if evaluate_js misbehaves.
GLOBAL_TIMEOUT_S = 180

PROBE_JS = """
(() => {
    const status = (document.getElementById('status-line') || {}).textContent || '';
    const tabs = [...document.querySelectorAll('.tab-btn')].map(b =>
        b.textContent + (b.classList.contains('active') ? '*' : ''));
    const panels = [...document.querySelectorAll('.tab-panel')].map(p =>
        p.id + (p.classList.contains('active') ? '*' : ''));
    const settingsBtn = document.querySelector('.tab-btn[data-tab="settings"]');
    if (settingsBtn) settingsBtn.click();
    const afterSwitch = [...document.querySelectorAll('.tab-panel')]
        .filter(p => p.classList.contains('active')).map(p => p.id);
    return {status, tabs, panels, afterSwitch};
})()
"""

# Phase 2: synchronous one-step JS snippets.  pywebview's evaluate_js
# reliably returns the result of a synchronous expression; async IIFEs
# serialize to {} and hang.  We therefore drive the UI one action at a time
# from Python, sleeping between steps on the Python side.
JS_WAIT_ROWS = "document.querySelectorAll('#llms-body tr').length"
JS_ROWS = "[...document.querySelectorAll('#llms-body tr')].map(tr => tr.dataset.name)"
JS_CLICK_ADD = "document.getElementById('btn-llm-add').click()"
JS_DIALOG_OPEN = "document.getElementById('llm-dialog-overlay').classList.contains('open')"
JS_FILL_ADD = """
(() => {
    document.getElementById('llm-name').value = 'probe-new';
    document.getElementById('llm-api-base').value = 'http://127.0.0.1:9999/v1';
    document.getElementById('llm-api-key').value = 'sk-probe';
    document.getElementById('llm-model').value = 'probe/model';
    document.getElementById('llm-temperature').value = '0.3';
    return 'filled';
})()
"""
JS_CLICK_SAVE = "document.getElementById('llm-dialog-save').click()"
JS_DIALOG_CLOSED = "!document.getElementById('llm-dialog-overlay').classList.contains('open')"
JS_DBLCLICK_PROBE = """
(() => {
    const tr = [...document.querySelectorAll('#llms-body tr')].find(t => t.dataset.name === 'probe-new');
    if (!tr) return 'no-row';
    tr.dispatchEvent(new MouseEvent('dblclick', {bubbles: true}));
    return 'dblclicked';
})()
"""
JS_PREFILL_TEMP = "document.getElementById('llm-temperature').value"
JS_SET_TEMP = "(() => { document.getElementById('llm-temperature').value = '0.77'; return 'set'; })()"
JS_EDITED_CELL = """
(() => {
    const tr = [...document.querySelectorAll('#llms-body tr')].find(t => t.dataset.name === 'probe-new');
    return tr ? tr.children[4].textContent.trim() : null;
})()
"""
JS_ROLE_OPTS = """
(() => {
    const opts = (sel) => [...sel.querySelectorAll('option')].map(o => o.value);
    return {
        judge: opts(document.getElementById('role-judge')),
        prompts: opts(document.getElementById('role-prompts'))
    };
})()
"""
JS_STUB_CONFIRM = "(() => { window.confirm = () => true; return 'stubbed'; })()"
JS_SELECT_PROBE = """
(() => {
    const tr = [...document.querySelectorAll('#llms-body tr')].find(t => t.dataset.name === 'probe-new');
    if (!tr) return 'no-row';
    tr.dispatchEvent(new MouseEvent('click', {bubbles: true}));
    return 'selected';
})()
"""
JS_HINT = "(document.getElementById('llm-selection-hint') || {}).textContent || ''"
JS_CLICK_REMOVE = "document.getElementById('btn-llm-remove').click()"
# Set the Settings HL checkbox to the given target value (0/1) and fire change.
# Returns the resulting checked state.
JS_HL_SET = """
((target) => {
    const hl = document.getElementById('hl-flag-settings');
    hl.checked = !!target;
    hl.dispatchEvent(new Event('change', {bubbles: true}));
    return hl.checked;
})({target})
"""

# Check initial role dropdown values (after initSettings, before any CRUD)
JS_INITIAL_ROLE_VALUES = """
(() => ({
    judge: document.getElementById('role-judge').value,
    prompts: document.getElementById('role-prompts').value
}))()
"""

# Check the Prompts-tab HL checkbox value (should mirror the Settings flag / config)
JS_PROMPTS_HL = "!!(document.getElementById('hl-flag') && document.getElementById('hl-flag').checked)"

# Change a role dropdown to the first available non-empty option and verify the change event fires
JS_CHANGE_JUDGE_ROLE = """
(() => {
    const sel = document.getElementById('role-judge');
    const options = [...sel.querySelectorAll('option')].filter(o => o.value !== '');
    if (options.length === 0) return 'no-options';
    // Pick the first option that is different from the current value
    const newOption = options.find(o => o.value !== sel.value) || options[0];
    sel.value = newOption.value;
    sel.dispatchEvent(new Event('change', {bubbles: true}));
    return sel.value;
})()
"""

# Verify the role was persisted
JS_VERIFY_JUDGE_ROLE = "document.getElementById('role-judge').value"

# ── Paste test: simulate contextmenu on #prompt, click Paste, verify text ──
# The app's Paste handler now calls api().get_clipboard() (Python bridge →
# QApplication.clipboard().text()), then inserts the returned text into the
# textarea.  We monkey-patch the bridge method to return a fixed test string,
# so the probe works headless without a real clipboard.
JS_PASTE_SIMULATE = """
(() => {
    const ta = document.getElementById('prompt');
    ta.value = '';
    ta.focus();
    ta.setSelectionRange(0, 0);
    const PASTE_TEXT = 'PASTED_TEST_LINE';
    // Monkey-patch the bridge get_clipboard to simulate Qt clipboard read.
    const origApi = window.pywebview && window.pywebview.api;
    if (origApi) {
        origApi.get_clipboard = () => Promise.resolve({ok: true, text: PASTE_TEXT});
    }
    // Open the context menu on the textarea.
    ta.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, clientX: 100, clientY: 100}));
    const item = document.querySelector('#ctx-menu .ctx-item[data-action="paste"]');
    if (!item) {
        if (origApi) delete origApi.get_clipboard;
        return 'no-paste-item';
    }
    // Click Paste — the handler calls get_clipboard() and inserts async.
    item.dispatchEvent(new MouseEvent('click', {bubbles: true}));
    return 'triggered';
})()
"""

# Step 2: verify the pasted text is in the prompt field
JS_PASTE_VERIFY = "document.getElementById('prompt').value"

# ── Phase 4: "Run prompt" GUI test (req 3.5 / 3.6) ────────────────────────
# Switch to the Prompts tab, fill prompt + result, and click #btn-run-prompt.
# The real judge LLM call runs on the GUI thread; Python polls for the result.
JS_RUN_PROMPT_TRIGGER = """
(() => {
    const promptsBtn = document.querySelector('.tab-btn[data-tab="prompts"]');
    if (promptsBtn) promptsBtn.click();
    document.getElementById('prompt').value = 'Reply with the single word: pong';
    document.getElementById('result').value = '{sentinel}';
    const btn = document.getElementById('btn-run-prompt');
    btn.click();
    return {loading: btn.classList.contains('loading'), disabled: btn.disabled};
})()
"""
JS_RESULT_VALUE = "document.getElementById('result').value"
JS_RUN_PROMPT_STATE = """
(() => {
    const btn = document.getElementById('btn-run-prompt');
    const toast = [...document.querySelectorAll('.toast')].map(t => t.textContent);
    return {loading: btn.classList.contains('loading'), disabled: btn.disabled, toasts: toast};
})()
"""
# Re-click #btn-run-prompt (the judge LLM's api_base has been broken on the
# Python side) to exercise the req 3.6 error path: error toast + Result
# field unchanged.
JS_RERUN_PROMPT = "document.getElementById('btn-run-prompt').click()"

# ── Phase 5: dry-run logging GUI test ────────────────────────────────────
# Switch to the Prompts tab, fill a trivially valid prompt (no variables →
# validation passes), and click #btn-start.  The dry-run worker then appends
# canned [DRY-RUN] lines to the Progress field over ~2.5 s.
JS_DRYRUN_TRIGGER = """
(() => {
    const promptsBtn = document.querySelector('.tab-btn[data-tab="prompts"]');
    if (promptsBtn) promptsBtn.click();
    document.getElementById('prompt').value = 'Reply with the single word: pong';
    document.getElementById('result').value = 'pong';
    // Ensure no variables are defined (avoids {{var}} validation errors).
    const rows = document.querySelectorAll('#variables-table tbody tr');
    rows.forEach(r => r.remove());
    const btn = document.getElementById('btn-start');
    btn.click();
    return {clicked: true, btnDisabled: btn.disabled};
})()
"""
# Snapshot of the execution-area DOM state (polled after the dry-run).
# Synchronous DOM snapshot (no bridge call).  The authoritative "running"
# flag is read directly from the Python Api instance in the probe thread
# (evaluate_js cannot return Promises — see probe header comment).
JS_EXEC_STATE = """
(() => {
    const btn = document.getElementById('btn-start');
    const progress = document.getElementById('progress');
    const optResult = document.getElementById('optimization-result');
    const toasts = [...document.querySelectorAll('.toast')].map(t => t.textContent);
    return {
        btnDisabled: btn ? btn.disabled : null,
        progressText: progress ? progress.value : '',
        progressLen: progress ? progress.value.length : 0,
        optResult: optResult ? optResult.value : '',
        toasts: toasts
    };
})()
"""


def main() -> int:
    # Work on a copy of the real config so the probe never mutates the project.
    tmp_dir = tempfile.mkdtemp(prefix="probe_ui_")
    cfg_path = os.path.join(tmp_dir, "config.yaml")
    shutil.copyfile(REAL_CONFIG, cfg_path)
    print(f"[probe] using temp config: {cfg_path}", flush=True)

    config = ConfigManager(path=cfg_path)
    api = Api(config)
    window = webview.create_window(
        "Prompt Optimizer (probe)", INDEX_HTML, js_api=api, width=1000, height=720
    )

    error = {"value": None}

    def finish(ok: bool, msg: str = "") -> None:
        try:
            window.hide()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        print(("PROBE OK" if ok else f"PROBE FAILED: {msg}"), flush=True)
        os._exit(0 if ok else 1)

    def probe():
        # ── Phase 1: wait for status line ──────────────────────────────
        phase1 = None
        for _ in range(45):
            time.sleep(1)
            try:
                out = window.evaluate_js(PROBE_JS)
            except Exception:  # noqa: BLE001
                out = None
            if isinstance(out, dict) and "Connecting…" not in str(out.get("status", "")):
                phase1 = out
                break
        if phase1 is None:
            finish(False, "status line never left 'Connecting…' (Phase 1)")
            return

        ok = True
        if not any(t.startswith("Prompts") for t in phase1.get("tabs", [])):
            print("FAIL: Prompts tab missing", flush=True); ok = False
        if not any(t.startswith("Settings") for t in phase1.get("tabs", [])):
            print("FAIL: Settings tab missing", flush=True); ok = False
        if phase1.get("afterSwitch") != ["tab-settings"]:
            print("FAIL: tab switching did not activate the Settings panel", flush=True); ok = False
        if "Config loaded" not in str(phase1.get("status", "")):
            print("FAIL: status line does not report a loaded config", flush=True); ok = False
        print(f"[probe] Phase 1 status: {phase1.get('status')}", flush=True)

        # ── Phase 2: wait for LLM table to render ──────────────────────
        # Read expected HL flag from the temp config (for the initial-sync check).
        import yaml
        with open(cfg_path, "r", encoding="utf-8") as f:
            _disk_cfg = yaml.safe_load(f)
        expected_hl_init = bool(_disk_cfg.get("hl", False))

        rows_count = 0
        for _ in range(30):
            time.sleep(0.5)
            try:
                rows_count = int(window.evaluate_js(JS_WAIT_ROWS) or 0)
            except Exception:  # noqa: BLE001
                rows_count = 0
            if rows_count > 0:
                break
        if rows_count == 0:
            finish(False, "LLM table never rendered (Phase 2)")
            return

        initial_rows = window.evaluate_js(JS_ROWS)
        print(f"[probe] Phase 2 initial rows: {initial_rows}", flush=True)

        # ── 3.0 INITIAL ROLE VALUES (verify initSettings restores from config) ──
        time.sleep(1)  # wait for initSettings to complete
        initial_roles = window.evaluate_js(JS_INITIAL_ROLE_VALUES)
        print(f"[probe] initial role values: {initial_roles}", flush=True)
        
        # Read the expected roles from the temp config file (reuses the
        # expected HL flag already read above).
        expected_cfg = _disk_cfg
        expected_roles = expected_cfg.get("roles", {})
        expected_judge = expected_roles.get("judge", "")
        expected_prompts = expected_roles.get("prompts", "")
        
        if not (isinstance(initial_roles, dict) and
                initial_roles.get("judge") == expected_judge and
                initial_roles.get("prompts") == expected_prompts):
            print(f"FAIL(roles-init): expected judge={expected_judge!r} prompts={expected_prompts!r}, "
                  f"got judge={initial_roles.get('judge')!r} prompts={initial_roles.get('prompts')!r}", flush=True)
            ok = False
        else:
            print("OK(roles-init): initial role dropdowns restored from config.yaml", flush=True)

        # ── 3.0b INITIAL HL SYNC (Prompts-tab checkbox mirrors config) ──
        initial_hl_prompts = window.evaluate_js(JS_PROMPTS_HL)
        print(f"[probe] initial Prompts HL checkbox: {initial_hl_prompts} (config hl={expected_hl_init})", flush=True)
        if bool(initial_hl_prompts) != expected_hl_init:
            print(f"FAIL(hl-init): Prompts HL checkbox={initial_hl_prompts}, config hl={expected_hl_init}", flush=True)
            ok = False
        else:
            print("OK(hl-init): Prompts-tab HL checkbox matches config.yaml", flush=True)

        # ── 3.1 ADD ─────────────────────────────────────────────────────
        window.evaluate_js(JS_CLICK_ADD)
        time.sleep(0.3)
        dialog_open = window.evaluate_js(JS_DIALOG_OPEN)
        window.evaluate_js(JS_FILL_ADD)
        window.evaluate_js(JS_CLICK_SAVE)
        after_add = None
        for _ in range(30):
            time.sleep(0.5)
            after_add = window.evaluate_js(JS_ROWS)
            if isinstance(after_add, list) and "probe-new" in after_add:
                break
        dialog_closed = window.evaluate_js(JS_DIALOG_CLOSED)
        print(f"[probe] 3.1 ADD: dialog_open={dialog_open} after_add={after_add} closed={dialog_closed}", flush=True)
        if not (isinstance(after_add, list) and "probe-new" in after_add):
            print("FAIL(3.1): added LLM row did not appear", flush=True); ok = False
        if not dialog_closed:
            print("FAIL(3.1): dialog did not close after Save", flush=True); ok = False

        # ── 3.2 EDIT ────────────────────────────────────────────────────
        dbl = window.evaluate_js(JS_DBLCLICK_PROBE)
        time.sleep(0.3)
        dialog_open_edit = window.evaluate_js(JS_DIALOG_OPEN)
        prefill = window.evaluate_js(JS_PREFILL_TEMP)
        print(f"[probe] 3.2 EDIT: dblclick={dbl} dialog_open={dialog_open_edit} prefill_temp={prefill}", flush=True)
        if not dialog_open_edit:
            print("FAIL(3.2): double-click did not open the edit dialog", flush=True); ok = False
        window.evaluate_js(JS_SET_TEMP)
        window.evaluate_js(JS_CLICK_SAVE)
        edited_cell = None
        for _ in range(30):
            time.sleep(0.5)
            edited_cell = window.evaluate_js(JS_EDITED_CELL)
            if edited_cell == "0.77":
                break
        print(f"[probe] 3.2 EDIT: edited_cell={edited_cell}", flush=True)
        if edited_cell != "0.77":
            print("FAIL(3.2): edited temperature not applied in the row", flush=True); ok = False

        # ── role dropdowns ──────────────────────────────────────────────
        role_opts = window.evaluate_js(JS_ROLE_OPTS)
        print(f"[probe] role options: {role_opts}", flush=True)
        if not (isinstance(role_opts, dict) and "probe-new" in role_opts.get("judge", [])):
            print("FAIL(roles): dropdown does not contain all LLM names", flush=True); ok = False

        # ── 3.5 ROLE CHANGE PERSISTENCE ─────────────────────────────────
        # Change judge role to a different LLM and verify it persists
        new_judge = window.evaluate_js(JS_CHANGE_JUDGE_ROLE)
        if new_judge == 'no-options':
            print("[probe] 3.5 role change: skipped (no LLM options available)", flush=True)
        else:
            time.sleep(0.5)  # wait for set_role bridge call to complete
            verified_judge = window.evaluate_js(JS_VERIFY_JUDGE_ROLE)
            print(f"[probe] 3.5 role change: judge set to {new_judge}, verified={verified_judge}", flush=True)
            if verified_judge != new_judge:
                print("FAIL(3.5): role change did not persist in dropdown", flush=True); ok = False

        # ── 3.3 REMOVE ──────────────────────────────────────────────────
        window.evaluate_js(JS_STUB_CONFIRM)
        sel = window.evaluate_js(JS_SELECT_PROBE)
        time.sleep(0.2)
        hint = window.evaluate_js(JS_HINT)
        window.evaluate_js(JS_CLICK_REMOVE)
        after_remove = None
        for _ in range(30):
            time.sleep(0.5)
            after_remove = window.evaluate_js(JS_ROWS)
            if isinstance(after_remove, list) and "probe-new" not in after_remove:
                break
        print(f"[probe] 3.3 REMOVE: select={sel} hint={hint!r} after_remove={after_remove}", flush=True)
        if not (isinstance(after_remove, list) and "probe-new" not in after_remove):
            print("FAIL(3.3): removed row is still present", flush=True); ok = False

        # ── 3.4 HL set-to-opposite + sync ───────────────────────────────
        # The real config may start with hl=true or false, so we flip it to the
        # opposite of the initial value and verify both checkboxes follow.
        target_hl = not expected_hl_init
        hl_new = window.evaluate_js(JS_HL_SET.replace("{target}", "1" if target_hl else "0"))
        time.sleep(0.5)
        print(f"[probe] 3.4 HL set: target={target_hl} hl_after={hl_new}", flush=True)
        if bool(hl_new) is not target_hl:
            print(f"FAIL(3.4): HL checkbox expected {target_hl}, got {hl_new}", flush=True); ok = False

        # ── 3.4b HL SYNC (Prompts-tab checkbox follows the Settings flag) ──
        hl_prompts_after = window.evaluate_js(JS_PROMPTS_HL)
        print(f"[probe] 3.4 HL sync: Prompts HL checkbox = {hl_prompts_after} (expected {target_hl})", flush=True)
        if bool(hl_prompts_after) is not target_hl:
            print(f"FAIL(3.4b): Prompts-tab HL checkbox did not sync (got {hl_prompts_after}, expected {target_hl})", flush=True); ok = False
        else:
            print("OK(3.4b): Prompts-tab HL checkbox synced with Settings flag", flush=True)

        # ── 3.6 PASTE (context menu Copy/Cut/Paste) ────────────────────
        # The Paste handler calls api().get_clipboard() (async) and inserts
        # the result.  We poll the field value until the async insertion
        # completes (or timeout).
        paste_result = window.evaluate_js(JS_PASTE_SIMULATE)
        print(f"[probe] 3.6 PASTE: trigger={paste_result!r}", flush=True)
        if paste_result == "no-paste-item":
            print("FAIL(3.6): context menu Paste item not found", flush=True); ok = False
        else:
            paste_value = ""
            for _ in range(20):
                time.sleep(0.3)
                paste_value = window.evaluate_js(JS_PASTE_VERIFY) or ""
                if "PASTED_TEST_LINE" in str(paste_value):
                    break
            print(f"[probe] 3.6 PASTE: verify={paste_value!r}", flush=True)
            if "PASTED_TEST_LINE" not in str(paste_value):
                print(f"FAIL(3.6): pasted text not in prompt field: {paste_value!r}", flush=True); ok = False
            else:
                print("OK(3.6): paste via context menu inserted text into prompt", flush=True)

        # ── 4. RUN PROMPT (Phase 4 GUI test: real one-shot LLM call) ────
        # req 3.5: valid prompt → Result field filled with the judge LLM's
        # output, and workspace/final_prompt.txt holds the substituted prompt.
        _RUN_SENTINEL = "SENTINEL_EXPECTED_RESULT_DO_NOT_USE"
        run_trigger = window.evaluate_js(
            JS_RUN_PROMPT_TRIGGER.replace("{sentinel}", _RUN_SENTINEL)
        )
        print(f"[probe] 4 RUN PROMPT: trigger={run_trigger}", flush=True)
        if not (isinstance(run_trigger, dict) and run_trigger.get("loading") and run_trigger.get("disabled")):
            print("FAIL(4): button did not enter the loading/spinner state", flush=True); ok = False

        prompt_text = "Reply with the single word: pong"
        result_filled = _RUN_SENTINEL
        for _ in range(120):  # up to ~60s for the real LLM call
            time.sleep(0.5)
            st = window.evaluate_js(JS_RUN_PROMPT_STATE)
            if st and st.get("loading") is False:
                break
            result_filled = window.evaluate_js(JS_RESULT_VALUE) or ""
        result_filled = window.evaluate_js(JS_RESULT_VALUE) or ""
        print(f"[probe] 4 RUN PROMPT: result={result_filled!r}", flush=True)
        if not result_filled or result_filled == _RUN_SENTINEL:
            print("FAIL(4): Result field unchanged — the LLM output was not applied", flush=True); ok = False
        else:
            print("OK(4): Result field filled with LLM output", flush=True)

        # final_prompt.txt must exist in the workspace and contain the prompt.
        final_path = os.path.join(PROJECT_ROOT, "workspace", "final_prompt.txt")
        if os.path.exists(final_path):
            fp_content = open(final_path, "r", encoding="utf-8").read()
            if prompt_text in fp_content:
                print("OK(4): final_prompt.txt exists and contains the substituted prompt", flush=True)
            else:
                print(f"FAIL(4): final_prompt.txt missing the prompt text: {fp_content!r}", flush=True); ok = False
        else:
            print(f"FAIL(4): workspace/final_prompt.txt not found at {final_path}", flush=True); ok = False

        # req 3.6: break the judge LLM's api_base → error toast, Result unchanged.
        # Pull the judge name from the on-disk temp config (authoritative).
        with open(cfg_path, "r", encoding="utf-8") as f:
            disk_cfg = yaml.safe_load(f)
        judge_name = (disk_cfg.get("roles") or {}).get("judge", "")
        print(f"[probe] 4 break judge: {judge_name!r}", flush=True)
        broken_result = result_filled  # capture Result value before the error run
        try:
            judge_entry = next(
                (l for l in (disk_cfg.get("llms") or [])
                 if isinstance(l, dict) and l.get("name") == judge_name),
                None,
            )
            if judge_entry:
                config.update_llm(judge_name, {
                    "name": judge_name,
                    "api_base": "http://127.0.0.1:1/v1",  # unroutable
                    "api_key": judge_entry.get("api_key", "test"),
                    "model": judge_entry.get("model", judge_name),
                    "temperature": judge_entry.get("temperature", 0.0),
                })
                rerun = window.evaluate_js(JS_RERUN_PROMPT)
                err_state = None
                for _ in range(120):  # up to ~60s for the failed call
                    time.sleep(0.5)
                    err_state = window.evaluate_js(JS_RUN_PROMPT_STATE)
                    if err_state and err_state.get("loading") is False:
                        break
                result_after_error = window.evaluate_js(JS_RESULT_VALUE) or ""
                print(f"[probe] 4 error path: state={err_state} result={result_after_error!r}", flush=True)
                if result_after_error != broken_result:
                    print("FAIL(4.6): Result field changed after an error run", flush=True); ok = False
                else:
                    print("OK(4.6): Result field unchanged after error run", flush=True)
                toasts = (err_state or {}).get("toasts", [])
                if toasts:
                    print(f"OK(4.6): error toast shown: {toasts}", flush=True)
                else:
                    print("FAIL(4.6): no error toast appeared", flush=True); ok = False
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL(4.6): broke judge config but error run raised: {exc}", flush=True); ok = False

        # ── 5. DRY-RUN LOGGING (Phase 5 GUI test) ───────────────────────
        # Click #btn-start → the dry-run worker appends canned [DRY-RUN] lines
        # to the Progress field over ~2.5 s.  The Start button must be disabled
        # while running and re-enabled after.  The Optimization result field
        # must remain empty (dry run produces no result).
        dryrun_trigger = window.evaluate_js(JS_DRYRUN_TRIGGER)
        print(f"[probe] 5 DRY-RUN: trigger={dryrun_trigger}", flush=True)

        # Immediately after click: the Python _running flag should be True.
        # Read it directly from the Api instance (evaluate_js can't return
        # Promises, and the DOM btn.disabled lags by up to 500 ms).
        time.sleep(0.3)
        try:
            py_state = api.get_state()
            py_running = py_state.get("running") if py_state.get("ok") else None
        except Exception as exc:  # noqa: BLE001
            py_running = None
            print(f"[probe] 5 DRY-RUN: get_state raised: {exc}", flush=True)
        print(f"[probe] 5 DRY-RUN: py_running after click={py_running}", flush=True)
        if py_running is not True:
            print(f"FAIL(5): Python _running not True immediately after start_run click (got {py_running!r})", flush=True)
            ok = False
        else:
            print("OK(5): Python _running=True immediately after Start click", flush=True)

        # Poll until the Python-side run finishes, then read the DOM snapshot.
        exec_st = None
        py_running_final = None
        for _ in range(30):  # up to ~15 s
            time.sleep(0.5)
            try:
                py_st = api.get_state()
                py_running_final = py_st.get("running") if py_st.get("ok") else None
            except Exception:  # noqa: BLE001
                py_running_final = None
            if py_running_final is False:
                # Run finished on the Python side.  Give the DOM a beat to settle
                # (the 500 ms JS poll cycle needs to apply the new state).
                time.sleep(1.0)
                exec_st = window.evaluate_js(JS_EXEC_STATE)
                break
        print(f"[probe] 5 DRY-RUN: py_running_final={py_running_final} exec_st={exec_st}", flush=True)

        if not isinstance(exec_st, dict):
            print("FAIL(5): could not read execution state", flush=True); ok = False
        else:
            progress_text = str(exec_st.get("progressText") or "")
            # Check all 5 canned [DRY-RUN] lines are present.
            expected_lines = [
                "[DRY-RUN] Starting optimization (dry-run mode) …",
                "[DRY-RUN] Validation passed — template + variables OK",
                "[DRY-RUN] build_prompt: simulated (final_prompt.txt written)",
                "[DRY-RUN] llm2: simulated (412 chars result)",
                "[DRY-RUN] Dry-run complete — no agent was started",
            ]
            missing = [ln for ln in expected_lines if ln not in progress_text]
            if missing:
                print(f"FAIL(5): Progress field missing {len(missing)} expected line(s): {missing}", flush=True)
                ok = False
            else:
                print(f"OK(5): Progress field filled with all {len(expected_lines)} [DRY-RUN] lines", flush=True)

            # DOM button must be re-enabled after the run (500 ms poll cycle
            # should have applied the state by now — we waited 1 s).
            if exec_st.get("btnDisabled") is not False:
                print(f"FAIL(5): Start button still disabled in DOM after dry-run: btnDisabled={exec_st.get('btnDisabled')}", flush=True)
                ok = False
            else:
                print("OK(5): Start button re-enabled in DOM after dry-run", flush=True)

            # Optimization result must stay empty.
            opt_val = str(exec_st.get("optResult") or "")
            if opt_val:
                print(f"FAIL(5): Optimization result not empty after dry-run: {opt_val!r}", flush=True)
                ok = False
            else:
                print("OK(5): Optimization result empty (dry-run)", flush=True)

        # ── verify on-disk persistence ──────────────────────────────────
        # Note: scenario 3.3 removes probe-new, so it must NOT be on disk.
        # We verify that the removal was persisted and HL flag was persisted.
        with open(cfg_path, "r", encoding="utf-8") as f:
            disk = yaml.safe_load(f)
        names_on_disk = [e["name"] for e in disk.get("llms", [])]
        print(f"  disk: {names_on_disk}", flush=True)
        if "probe-new" in names_on_disk:
            print("FAIL(disk): probe-new LLM should have been removed", flush=True); ok = False
        else:
            print("OK(disk): probe-new LLM removed and persisted", flush=True)
        if bool(disk.get("hl")) is not target_hl:
            print(f"FAIL(disk): HL flag on disk={disk.get('hl')}, expected {target_hl}", flush=True); ok = False
        else:
            print(f"OK(disk): HL flag persisted as {target_hl}", flush=True)

        finish(ok)

    def on_timeout(signum, frame):  # noqa: ARG001
        finish(False, f"global timeout ({GLOBAL_TIMEOUT_S}s) exceeded")

    signal.signal(signal.SIGALRM, on_timeout)
    signal.alarm(GLOBAL_TIMEOUT_S)

    threading.Thread(target=probe, daemon=True).start()
    webview.start()
    # Unreachable in practice: the probe thread hard-exits the process.
    return 1


if __name__ == "__main__":
    sys.exit(main())
