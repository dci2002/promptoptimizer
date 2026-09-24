/* Prompt Optimizer — app.js
 * Phase 1: tab switching + bridge probe.
 * Phase 2: Settings tab — LLM table CRUD, role dropdowns, HL checkbox.
 */

(function () {
    "use strict";

    var statusLine = document.getElementById("status-line");

    function setStatus(text, cls) {
        statusLine.textContent = text;
        statusLine.className = cls || "";
    }

    /* ───────────────────────── Bridge helper ───────────────────────── */

    function api() {
        return (window.pywebview && window.pywebview.api) ? window.pywebview.api : null;
    }

    /* ───────────────────────── Toasts ───────────────────────── */

    function toast(message, kind, ms) {
        var el = document.createElement("div");
        el.className = "toast " + (kind || "");
        el.textContent = message;
        document.body.appendChild(el);
        requestAnimationFrame(function () { el.classList.add("show"); });
        setTimeout(function () {
            el.classList.remove("show");
            setTimeout(function () { el.remove(); }, 250);
        }, ms || 3200);
    }

    /* ───────────────────────── Tab switching ───────────────────────── */

    var tabButtons = document.querySelectorAll(".tab-btn");
    var tabPanels = document.querySelectorAll(".tab-panel");

    function switchTab(name) {
        tabButtons.forEach(function (btn) {
            btn.classList.toggle("active", btn.dataset.tab === name);
        });
        tabPanels.forEach(function (panel) {
            panel.classList.toggle("active", panel.id === "tab-" + name);
        });
    }

    tabButtons.forEach(function (btn) {
        btn.addEventListener("click", function () {
            switchTab(btn.dataset.tab);
        });
    });

    /* ═══════════════════════════ Phase 2 — Settings tab ═══════════════════════════ */

    var llmsBody = document.getElementById("llms-body");
    var roleJudgeSel = document.getElementById("role-judge");
    var rolePromptsSel = document.getElementById("role-prompts");
    var hlFlagSettings = document.getElementById("hl-flag-settings");
    var hlFlag = document.getElementById("hl-flag");
    var selectionHint = document.getElementById("llm-selection-hint");

    var state = {
        llms: [],          // [{name, api_base, api_key, model, temperature}]
        roles: { judge: "", prompts: "" },
        hl: false,
        max_attempts: 30,
        selectedLlm: null, // name of the selected table row
        variables: [],     // [{name, value}]
        selectedVar: null  // name of the selected variable row
    };

    /* ── LLM table rendering ── */

    function renderLlmTable() {
        llmsBody.innerHTML = "";
        state.llms.forEach(function (llm) {
            var tr = document.createElement("tr");
            tr.className = "row-clickable" + (state.selectedLlm === llm.name ? " selected" : "");
            tr.dataset.name = llm.name;

            ["name", "api_base", "api_key", "model", "temperature"].forEach(function (field) {
                var td = document.createElement("td");
                td.textContent = llm[field];
                tr.appendChild(td);
            });

            tr.addEventListener("click", function () {
                state.selectedLlm = llm.name;
                markLlmRowSelected();
            });
            tr.addEventListener("dblclick", function () {
                openLlmDialog(llm);
            });

            llmsBody.appendChild(tr);
        });
        updateSettingsButtons();
    }

    function markLlmRowSelected() {
        var rows = llmsBody.querySelectorAll("tr");
        rows.forEach(function (row) {
            row.classList.toggle("selected", row.dataset.name === state.selectedLlm);
        });
        selectionHint.textContent = state.selectedLlm
            ? "selected: " + state.selectedLlm
            : "(no row selected)";
        updateSettingsButtons();
    }

    function updateSettingsButtons() {
        var hasSelection = !!state.selectedLlm;
        document.getElementById("btn-llm-edit").disabled = !hasSelection;
        document.getElementById("btn-llm-remove").disabled = !hasSelection;
    }

    function getSelectedLlm() {
        if (!state.selectedLlm) return null;
        for (var i = 0; i < state.llms.length; i++) {
            if (state.llms[i].name === state.selectedLlm) return state.llms[i];
        }
        return null;
    }

    /* ── Role dropdowns ── */

    function renderRoleDropdowns() {
        // Map of select element → role key
        var mappings = [
            { sel: roleJudgeSel, role: "judge" },
            { sel: rolePromptsSel, role: "prompts" }
        ];
        mappings.forEach(function (m) {
            var sel = m.sel;
            var roleKey = m.role;
            // Rebuild options
            sel.innerHTML = '<option value="">— not selected —</option>';
            state.llms.forEach(function (llm) {
                var opt = document.createElement("option");
                opt.value = llm.name;
                opt.textContent = llm.name;
                sel.appendChild(opt);
            });
            // Restore selection from state.roles if the LLM still exists
            var desired = state.roles[roleKey] || "";
            var exists = state.llms.some(function (l) { return l.name === desired; });
            sel.value = exists ? desired : "";
        });
    }

    function onRoleChange(role, select) {
        var a = api();
        if (!a) return;
        a.set_role(role, select.value).then(function (res) {
            if (!res || res.ok === false) {
                toast("Role error: " + (res ? res.error : "no response"), "error");
                // reload current state from config to resync the dropdown
                refreshSettings();
                return;
            }
            state.roles = res.roles || state.roles;
            renderRoleDropdowns();
            scheduleValidation();
        });
    }

    /* ── HL checkbox (Settings tab) ── */

    function onHlChange() {
        var a = api();
        if (!a) return;
        var value = hlFlagSettings.checked;
        // Keep the Prompts-tab checkbox in sync immediately (single source of
        // truth is the Settings checkbox; this mirrors the pending value).
        if (hlFlag) hlFlag.checked = value;
        a.set_hl(value).then(function (res) {
            if (!res || res.ok === false) {
                toast("HL error: " + (res ? res.error : "no response"), "error");
                hlFlagSettings.checked = state.hl;
                if (hlFlag) hlFlag.checked = state.hl;
                return;
            }
            state.hl = res.hl;
            hlFlagSettings.checked = state.hl;
            if (hlFlag) hlFlag.checked = state.hl;
        });
    }

    /* ── Settings refresh (after any mutation) ── */

    function refreshSettings() {
        var a = api();
        if (!a) return;
        a.get_config().then(function (cfg) {
            if (!cfg || cfg.ok === false) {
                toast("Failed to reload config: " + (cfg ? cfg.error : "no response"), "error");
                return;
            }
            state.llms = cfg.llms || [];
            state.roles = cfg.roles || {};
            state.hl = !!cfg.hl;
            state.max_attempts = cfg.max_attempts;
            // Drop the selection if the LLM no longer exists.
            if (state.selectedLlm && !state.llms.some(function (l) { return l.name === state.selectedLlm; })) {
                state.selectedLlm = null;
            }
            renderLlmTable();
            markLlmRowSelected();
            renderRoleDropdowns();
            hlFlagSettings.checked = state.hl;
            if (hlFlag) hlFlag.checked = state.hl;
            scheduleValidation();
        });
    }

    /* ═══════════════════ LLM dialog (add / edit) ═══════════════════ */

    var overlay = document.getElementById("llm-dialog-overlay");
    var dlgTitle = document.getElementById("llm-dialog-title");
    var dlgName = document.getElementById("llm-name");
    var dlgApiBase = document.getElementById("llm-api-base");
    var dlgApiKey = document.getElementById("llm-api-key");
    var dlgModel = document.getElementById("llm-model");
    var dlgTemperature = document.getElementById("llm-temperature");
    var dlgError = document.getElementById("llm-dialog-error");
    var dlgSaveBtn = document.getElementById("llm-dialog-save");

    var dialogState = { mode: "add", oldName: "" };

    function openLlmDialog(llm) {
        if (llm) {
            dialogState = { mode: "edit", oldName: llm.name };
            dlgTitle.textContent = "Edit LLM — " + llm.name;
            dlgName.value = llm.name;
            dlgApiBase.value = llm.api_base;
            dlgApiKey.value = llm.api_key;
            dlgModel.value = llm.model;
            dlgTemperature.value = String(llm.temperature);
        } else {
            dialogState = { mode: "add", oldName: "" };
            dlgTitle.textContent = "Add LLM";
            dlgName.value = "";
            dlgApiBase.value = "";
            dlgApiKey.value = "";
            dlgModel.value = "";
            dlgTemperature.value = "";
        }
        dlgError.textContent = "";
        overlay.classList.add("open");
        dlgName.focus();
    }

    function closeLlmDialog() {
        overlay.classList.remove("open");
    }

    function showDlgError(msg) {
        dlgError.textContent = msg || "";
    }

    /* Client-side required-field validation (T2.4). */
    function validateDialogFields() {
        var errors = [];
        var name = dlgName.value.trim();
        var apiBase = dlgApiBase.value.trim();
        var apiKey = dlgApiKey.value.trim();
        var model = dlgModel.value.trim();
        var tempRaw = dlgTemperature.value.trim();

        if (!name) errors.push("name is required");
        if (!apiBase) errors.push("api_base is required");
        if (!apiKey) errors.push("api_key is required");
        if (!model) errors.push("model is required");
        if (tempRaw === "") {
            errors.push("temperature is required");
        } else if (isNaN(Number(tempRaw)) || !isFinite(Number(tempRaw))) {
            errors.push("temperature must be a number");
        }
        return errors;
    }

    function onDialogSave() {
        var fieldErrors = validateDialogFields();
        if (fieldErrors.length) {
            showDlgError(fieldErrors.join("\n"));
            return;
        }
        var a = api();
        if (!a) {
            showDlgError("pywebview bridge not available");
            return;
        }

        var cfg = {
            name: dlgName.value.trim(),
            api_base: dlgApiBase.value.trim(),
            api_key: dlgApiKey.value.trim(),
            model: dlgModel.value.trim(),
            temperature: Number(dlgTemperature.value.trim())
        };
        if (dialogState.mode === "edit") {
            cfg._old_name = dialogState.oldName;
        }

        showDlgError("");
        dlgSaveBtn.disabled = true;
        a.save_llm(cfg).then(function (res) {
            dlgSaveBtn.disabled = false;
            if (!res || res.ok === false) {
                showDlgError(res ? res.error : "no response from bridge");
                return;
            }
            closeLlmDialog();
            toast("LLM saved: " + (res.llm ? res.llm.name : cfg.name), "ok");
            refreshSettings();
        }).catch(function (err) {
            dlgSaveBtn.disabled = false;
            showDlgError("save failed: " + err);
        });
    }

    function onDialogRemove() {
        var llm = getSelectedLlm();
        if (!llm) return;
        var ok = window.confirm(
            "Remove LLM '" + llm.name + "'?\n" +
            "Any role currently pointing to it will be cleared."
        );
        if (!ok) return;

        var a = api();
        if (!a) return;
        a.remove_llm(llm.name).then(function (res) {
            if (!res || res.ok === false) {
                toast("Remove failed: " + (res ? res.error : "no response"), "error");
                return;
            }
            var cleared = (res.cleared_roles || []).join(", ");
            toast(
                "LLM removed: " + llm.name +
                (cleared ? " (cleared roles: " + cleared + ")" : ""),
                "ok"
            );
            refreshSettings();
        });
    }

    /* ═══════════════════════════ Phase 3 — Prompts tab: variables + validation ═══════════════════════════ */

    var promptField = document.getElementById("prompt");
    var resultField = document.getElementById("result");
    var variablesBody = document.getElementById("variables-body");
    var variablesSummary = document.getElementById("variables-summary");
    var validationStrip = document.getElementById("validation-strip");
    var varOverlay = document.getElementById("var-dialog-overlay");
    var varName = document.getElementById("var-name");
    var varValue = document.getElementById("var-value");
    var varError = document.getElementById("var-dialog-error");
    var btnVarRemove = document.getElementById("btn-var-remove");
    var btnVarEdit = document.getElementById("btn-var-edit");

    /* ── Variables table rendering ── */

    function renderVariablesTable() {
        variablesBody.innerHTML = "";
        state.variables.forEach(function (v) {
            var tr = document.createElement("tr");
            tr.className = "row-clickable" + (state.selectedVar === v.name ? " selected" : "");
            tr.dataset.name = v.name;

            var tdName = document.createElement("td");
            tdName.textContent = v.name;
            tr.appendChild(tdName);

            var tdValue = document.createElement("td");
            tdValue.textContent = v.value;
            tr.appendChild(tdValue);

            tr.addEventListener("click", function () {
                state.selectedVar = v.name;
                markVarRowSelected();
            });
            tr.addEventListener("dblclick", function () {
                state.selectedVar = v.name;
                markVarRowSelected();
                openVarEditDialog();
            });

            variablesBody.appendChild(tr);
        });
        updateVariablesUI();
    }

    function markVarRowSelected() {
        variablesBody.querySelectorAll("tr").forEach(function (row) {
            row.classList.toggle("selected", row.dataset.name === state.selectedVar);
        });
        updateVariablesUI();
    }

    function updateVariablesUI() {
        btnVarRemove.disabled = !state.selectedVar;
        btnVarEdit.disabled = !state.selectedVar;
        variablesSummary.textContent = state.variables.length
            ? state.variables.length + " variable" + (state.variables.length > 1 ? "s" : "")
            : "no variables";
    }

    function getSelectedVar() {
        if (!state.selectedVar) return null;
        for (var i = 0; i < state.variables.length; i++) {
            if (state.variables[i].name === state.selectedVar) return state.variables[i];
        }
        return null;
    }

    function hasVariableName(name) {
        return state.variables.some(function (v) { return v.name === name; });
    }

    /* ── Variable dialog (add / edit) ── */

    var varDialogMode = "add"; // "add" | "edit"

    function openVarDialog() {
        varDialogMode = "add";
        varName.value = "";
        varName.readOnly = false;
        varValue.value = "";
        varError.textContent = "";
        varOverlay.classList.add("open");
        varName.focus();
    }

    function openVarEditDialog() {
        var v = getSelectedVar();
        if (!v) {
            toast("Select a row in the Variables table first", "error");
            return;
        }
        varDialogMode = "edit";
        varName.value = v.name;
        varName.readOnly = true;
        varValue.value = v.value;
        varError.textContent = "";
        varOverlay.classList.add("open");
        varValue.focus();
    }

    function closeVarDialog() {
        varOverlay.classList.remove("open");
    }

    function onVarSave() {
        var name = varName.value.trim();
        if (!name) {
            varError.textContent = "Variable name is required";
            return;
        }
        if (!/^\w+$/.test(name)) {
            varError.textContent = "Variable name may contain only letters, digits and underscore";
            return;
        }
        if (varDialogMode === "add") {
            if (hasVariableName(name)) {
                varError.textContent = "Variable '" + name + "' already exists";
                return;
            }
            state.variables.push({ name: name, value: varValue.value });
            state.selectedVar = name;
            closeVarDialog();
            renderVariablesTable();
            toast("Variable added: " + name, "ok");
        } else {
            // edit mode
            for (var i = 0; i < state.variables.length; i++) {
                if (state.variables[i].name === name) {
                    state.variables[i].value = varValue.value;
                    break;
                }
            }
            closeVarDialog();
            renderVariablesTable();
            toast("Variable saved: " + name, "ok");
        }
        scheduleValidation();
    }

    function onVarRemove() {
        var v = getSelectedVar();
        if (!v) return;
        state.variables = state.variables.filter(function (x) { return x.name !== v.name; });
        state.selectedVar = null;
        renderVariablesTable();
        toast("Variable removed: " + v.name, "ok");
        scheduleValidation();
    }

    /* ── Refresh: extract variables from prompt into the table ── */

    function onVarRefresh() {
        var text = promptField.value;
        var re = /\{\{(\w+)\}\}/g;
        var seen = {};
        var names = [];
        var m;
        while ((m = re.exec(text)) !== null) {
            if (!seen[m[1]]) {
                seen[m[1]] = true;
                names.push(m[1]);
            }
        }
        if (!names.length) {
            toast("No {{variables}} found in the prompt", "info");
            return;
        }
        var added = 0;
        names.forEach(function (name) {
            if (!hasVariableName(name)) {
                state.variables.push({ name: name, value: "" });
                added++;
            }
        });
        if (added > 0) {
            renderVariablesTable();
            toast("Extracted " + names.length + " variable" + (names.length > 1 ? "s" : "") + " from prompt" + (added > 1 ? " (" + added + " new)" : ""), "ok");
        } else {
            toast("All " + names.length + " variable" + (names.length > 1 ? "s" : "") + " already present", "ok");
        }
        scheduleValidation();
    }

    /* ── Load from workspace files ── */

    function onRunLoad() {
        var _api = api();
        if (!_api || !_api.load_run_data) {
            toast("Bridge not ready", "error");
            return;
        }
        _api.load_run_data().then(function (res) {
            if (!res || !res.ok) {
                toast("Load failed: " + (res && res.error ? res.error : "unknown error"), "error");
                return;
            }
            // Fill the prompt and result fields.
            promptField.value = res.prompt || "";
            resultField.value = res.result || "";
            // Fill the variables table (extracted from prompt + file values).
            state.variables = (res.variables || []).map(function (v) {
                return { name: v.name, value: v.value || "" };
            });
            state.selectedVar = null;
            renderVariablesTable();
            toast(
                "Loaded: prompt, result, " +
                (res.variables || []).length + " variable" +
                ((res.variables || []).length !== 1 ? "s" : ""),
                "ok"
            );
            scheduleValidation();
        }).catch(function (err) {
            toast("Load failed: " + err, "error");
        });
    }

    /* ── Validation feedback (req 3.6) — debounced ── */

    var validationTimer = null;

    function scheduleValidation() {
        if (validationTimer) clearTimeout(validationTimer);
        validationTimer = setTimeout(runValidation, 400);
    }

    /* ── Run prompt (Phase 4, T4.6) ── */

    function onRunPrompt() {
        var a = api();
        if (!a || !a.run_prompt) {
            toast("Bridge not ready", "error");
            return;
        }
        var btn = document.getElementById("btn-run-prompt");
        var variables = state.variables.map(function (v) { return { name: v.name, value: v.value }; });
        btn.classList.add("loading");
        btn.disabled = true;
        setStatus("Running prompt on LLM as Judge…", "info");
        a.run_prompt(promptField.value, resultField.value, variables).then(function (res) {
            btn.classList.remove("loading");
            btn.disabled = false;
            if (res && res.ok) {
                resultField.value = res.result;
                setStatus("Prompt run finished", "ok");
                toast("Prompt run finished", "ok");
            } else if (res && res.errors) {
                validationStrip.textContent = res.errors.join("  ·  ");
                validationStrip.style.display = "block";
                setStatus("Validation failed", "error");
                toast("Validation failed", "error");
            } else {
                var msg = (res && res.error) ? res.error : "Run prompt failed";
                setStatus(msg, "error");
                toast(msg, "error");
            }
        }).catch(function (err) {
            btn.classList.remove("loading");
            btn.disabled = false;
            setStatus("Run prompt failed: " + err, "error");
            toast("Run prompt failed: " + err, "error");
        });
    }

    function runValidation() {
        var a = api();
        if (!a || !a.run_validation) {
            validationStrip.style.display = "none";
            return;
        }
        var variables = state.variables.map(function (v) { return { name: v.name, value: v.value }; });
        a.run_validation(promptField.value, resultField.value, variables).then(function (res) {
            if (!res || res.ok === false && !res.errors) {
                // Bridge-level error (res.ok === false with .error, not .errors).
                if (res && res.error) {
                    validationStrip.textContent = res.error;
                    validationStrip.style.display = "block";
                }
                return;
            }
            if (res.ok) {
                validationStrip.textContent = "";
                validationStrip.style.display = "none";
            } else {
                validationStrip.textContent = (res.errors || []).join("  ·  ");
                validationStrip.style.display = "block";
            }
        }).catch(function (err) {
            validationStrip.textContent = "validation failed: " + err;
            validationStrip.style.display = "block";
        });
    }

    /* ── Prompts-tab event wiring ── */

    document.getElementById("btn-var-add").addEventListener("click", openVarDialog);
    btnVarEdit.addEventListener("click", openVarEditDialog);
    btnVarRemove.addEventListener("click", onVarRemove);
    document.getElementById("btn-var-refresh").addEventListener("click", onVarRefresh);
    document.getElementById("btn-run-load").addEventListener("click", onRunLoad);
    document.getElementById("btn-run-prompt").addEventListener("click", onRunPrompt);
    document.getElementById("var-dialog-save").addEventListener("click", onVarSave);
    document.getElementById("var-dialog-cancel").addEventListener("click", closeVarDialog);

    // Click on the overlay backdrop closes the dialog.
    varOverlay.addEventListener("click", function (e) {
        if (e.target === varOverlay) closeVarDialog();
    });

    // Escape closes the variable dialog.
    [varName, varValue].forEach(function (input) {
        input.addEventListener("keydown", function (e) {
            if (e.key === "Escape") closeVarDialog();
        });
    });

    // Re-validate on any change of the prompt/result fields.
    promptField.addEventListener("input", scheduleValidation);
    resultField.addEventListener("input", scheduleValidation);

    /* ── Settings event wiring ── */

    document.getElementById("btn-llm-add").addEventListener("click", function () {
        openLlmDialog(null);
    });

    document.getElementById("btn-llm-edit").addEventListener("click", function () {
        var llm = getSelectedLlm();
        if (!llm) {
            toast("Select a row in the LLM table first", "error");
            return;
        }
        openLlmDialog(llm);
    });

    document.getElementById("btn-llm-remove").addEventListener("click", onDialogRemove);

    document.getElementById("llm-dialog-save").addEventListener("click", onDialogSave);
    document.getElementById("llm-dialog-cancel").addEventListener("click", closeLlmDialog);

    // Click on the overlay backdrop closes the dialog.
    overlay.addEventListener("click", function (e) {
        if (e.target === overlay) closeLlmDialog();
    });

    // Enter inside a dialog field triggers Save (Escape closes).
    [dlgName, dlgApiBase, dlgApiKey, dlgModel, dlgTemperature].forEach(function (input) {
        input.addEventListener("keydown", function (e) {
            if (e.key === "Enter") {
                e.preventDefault();
                onDialogSave();
            } else if (e.key === "Escape") {
                closeLlmDialog();
            }
        });
    });

    roleJudgeSel.addEventListener("change", function () { onRoleChange("judge", roleJudgeSel); });
    rolePromptsSel.addEventListener("change", function () { onRoleChange("prompts", rolePromptsSel); });
    hlFlagSettings.addEventListener("change", onHlChange);

    /* ═══════════════════════════ Phase 5 — Execution area ═══════════════════════════
     * Start button → api.start_run() → worker thread (dry-run in Phase 5).
     * A 500 ms polling loop calls api.get_state() and renders:
     *   - the "Progress" field (multi-line log from the worker thread);
     *   - the "Optimization result" field (final template; empty in dry-run);
     *   - the Start button (enabled / disabled + "Start" / "Continue" text).
     *
     * The "Continue" (HL) path is wired in Phase 7; for now the button only
     * starts a run.
     */

    var btnStart = document.getElementById("btn-start");
    var progressField = document.getElementById("progress");
    var optimizationResultField = document.getElementById("optimization-result");
    var hlResultPrompt = document.getElementById("hl-result-prompt");

    var execState = {
        running: false,
        hlWaiting: false,
        lastProgress: "",
        lastResult: "",
        pollTimer: null,
    };

    function renderExecution(st) {
        if (!st) return;

        // Progress: render the full text (the worker thread owns the buffer).
        if (st.progress !== execState.lastProgress) {
            progressField.value = st.progress || "";
            progressField.scrollTop = progressField.scrollHeight;
            execState.lastProgress = st.progress;
        }

        // Optimization result: render when the worker sets it (Phase 6+).
        if (st.optimization_result !== execState.lastResult) {
            optimizationResultField.value = st.optimization_result || "";
            execState.lastResult = st.optimization_result;
        }

        // Start button: enabled / disabled + "Start" / "Continue".
        var btn = st.start_button || { enabled: true, text: "Start" };
        btnStart.disabled = !btn.enabled;
        btnStart.title = btn.text;
        btnStart.setAttribute("aria-label", btn.text);

        // Track state for the polling loop.
        execState.running = !!st.running;
        execState.hlWaiting = !!st.hl_waiting;
    }

    function pollState() {
        var a = api();
        if (!a || !a.get_state) return;
        a.get_state().then(function (st) {
            if (st && st.ok) {
                renderExecution(st);

                // Stop polling once the run is finished (and we're not waiting HL).
                if (!st.running && !st.hl_waiting && execState.pollTimer) {
                    clearInterval(execState.pollTimer);
                    execState.pollTimer = null;
                    execState.running = false;
                }
            }
        }).catch(function () {
            /* transient bridge error — keep polling */
        });
    }

    function startPolling() {
        if (execState.pollTimer) return; // already polling
        execState.lastProgress = "";
        execState.lastResult = "";
        progressField.value = "";
        optimizationResultField.value = "";
        execState.pollTimer = setInterval(pollState, 500);
    }

    function onStartClick() {
        var a = api();
        if (!a || !a.start_run) {
            toast("Bridge not ready", "error");
            return;
        }

        // If the button says "Continue", handle the HL continue (Phase 7).
        // For now, HL is not implemented — show a hint.
        if (btnStart.title === "Continue" || execState.hlWaiting) {
            toast("HL continue is wired in Phase 7", "info");
            return;
        }

        // Reject a concurrent run.
        if (execState.running) {
            toast("A run is already in progress", "error");
            return;
        }

        var variables = state.variables.map(function (v) {
            return { name: v.name, value: v.value };
        });

        setStatus("Starting run …", "info");
        a.start_run(promptField.value, resultField.value, variables, state.hl)
            .then(function (res) {
                if (!res || res.ok === false) {
                    if (res && res.errors) {
                        toast(res.errors.join(" · "), "error", 6000);
                    } else if (res && res.error) {
                        toast(res.error, "error", 6000);
                    }
                    setStatus("Run failed to start", "error");
                    return;
                }
                if (res.dry_run) {
                    toast("Dry-run started — no agent will be launched (Phase 5)", "info");
                }
                setStatus("Run started", "ok");
                startPolling();
            })
            .catch(function (err) {
                setStatus("Run failed to start: " + err, "error");
                toast("Failed to start: " + err, "error");
            });
    }

    btnStart.addEventListener("click", onStartClick);

    /* ═══════════════════════════ Bridge probe (Phase 1) ═══════════════════════════
     * On load call window.pywebview.api.get_config(), log the result to the
     * status line (proves the bridge) and initialise the Settings tab.
     */

    function initSettings(cfg) {
        state.llms = cfg.llms || [];
        state.roles = cfg.roles || {};
        state.hl = !!cfg.hl;
        state.max_attempts = cfg.max_attempts;
        renderLlmTable();
        markLlmRowSelected();
        renderRoleDropdowns();
        hlFlagSettings.checked = state.hl;
        if (hlFlag) hlFlag.checked = state.hl;
        renderVariablesTable();
        scheduleValidation();
    }

    function probeBridge() {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.get_config) {
            window.pywebview.api.get_config().then(function (cfg) {
                    if (!cfg || cfg.ok === false) {
                        setStatus(
                            "Bridge error: " + (cfg ? cfg.error : "no config returned"),
                            "error"
                        );
                        return;
                    }
                    var llms = cfg.llms || [];
                    var roles = cfg.roles || {};
                    var msg =
                        "Config loaded: " +
                        llms.length + " LLMs (" +
                        llms.map(function (l) { return l.name; }).join(", ") +
                        "), roles: judge=" + (roles.judge || "—") +
                        ", prompts=" + (roles.prompts || "—") +
                        ", hl=" + cfg.hl +
                        ", max_attempts=" + cfg.max_attempts;
                    setStatus(msg, "ok");
                    initSettings(cfg);
                })
                .catch(function (err) {
                    setStatus("Bridge error: " + err, "error");
                });
        } else {
            // pywebview injects window.pywebview asynchronously on first event.
            // Retry briefly before giving up.
            var attempts = 0;
            var timer = setInterval(function () {
                attempts += 1;
                if (window.pywebview && window.pywebview.api && window.pywebview.api.get_config) {
                    clearInterval(timer);
                    probeBridge();
                } else if (attempts > 80) {
                    clearInterval(timer);
                    setStatus("pywebview API not available (window bridge offline)", "error");
                }
            }, 250);
        }
    }

    /* ── Context menu (Copy / Cut / Paste) for all text fields ── */

    (function initContextMenu() {
        var menu = document.createElement("div");
        menu.id = "ctx-menu";
        menu.style.display = "none";

        var fields = [
            { label: "Copy",   action: "copy"   },
            { label: "Cut",    action: "cut"    },
            { label: "Paste",  action: "paste"  },
        ];

        fields.forEach(function (f) {
            var item = document.createElement("div");
            item.className = "ctx-item";
            item.textContent = f.label;
            item.dataset.action = f.action;
            item.addEventListener("click", function (e) {
                e.preventDefault();
                e.stopPropagation();
                var sel = document.getSelection();
                var editable = menu._target;
                if (!editable) return;
                var isTextarea = editable.tagName === "TEXTAREA";
                var value = isTextarea ? editable.value : (sel ? sel.toString() : "");

                if (f.action === "copy") {
                    if (isTextarea) {
                        var start = editable.selectionStart;
                        var end = editable.selectionEnd;
                        if (start === end) {
                            editable.select();
                        }
                        document.execCommand("copy");
                        editable.setSelectionRange(start, end);
                    } else {
                        document.execCommand("copy");
                    }
                } else if (f.action === "cut") {
                    if (isTextarea) {
                        var s = editable.selectionStart;
                        var en = editable.selectionEnd;
                        if (s === en) editable.select();
                        document.execCommand("cut");
                        editable.setSelectionRange(s, en);
                    } else {
                        document.execCommand("cut");
                    }
                } else if (f.action === "paste") {
                    // Read clipboard text via the Python bridge (QApplication.clipboard()),
                    // then insert it at the cursor position.  This avoids both the
                    // navigator.clipboard permission crash and the non-firing native
                    // paste event in Qt WebEngine.
                    var _api = api();
                    if (_api && _api.get_clipboard) {
                        var target = editable;
                        var wasTextarea = isTextarea;
                        _api.get_clipboard().then(function (res) {
                            if (res && res.ok && res.text) {
                                var text = res.text;
                                if (wasTextarea) {
                                    var pos = target.selectionStart || 0;
                                    var end = target.selectionEnd || pos;
                                    var before = target.value.substring(0, pos);
                                    var after = target.value.substring(end);
                                    target.value = before + text + after;
                                    target.setSelectionRange(pos + text.length, pos + text.length);
                                } else {
                                    // contentEditable or input: use execCommand insertText
                                    document.execCommand("insertText", false, text);
                                }
                                target.dispatchEvent(new Event("input", { bubbles: true }));
                            } else if (res && !res.ok) {
                                toast("Paste failed: " + res.error, "error");
                            }
                        }).catch(function (err) {
                            toast("Paste failed: " + err, "error");
                        });
                    } else {
                        toast("Bridge not ready — cannot paste", "error");
                    }
                }
                hideMenu();
            });
            menu.appendChild(item);
        });

        document.body.appendChild(menu);

        function showMenu(x, y, target) {
            menu._target = target;
            menu.style.display = "block";
            // Keep within viewport
            var rect = menu.getBoundingClientRect();
            var maxX = window.innerWidth - rect.width - 4;
            var maxY = window.innerHeight - rect.height - 4;
            menu.style.left = Math.min(x, maxX) + "px";
            menu.style.top = Math.min(y, maxY) + "px";
        }

        function hideMenu() {
            menu.style.display = "none";
            menu._target = null;
        }

        document.addEventListener("contextmenu", function (e) {
            var t = e.target;
            var isEditable =
                t.tagName === "TEXTAREA" ||
                t.tagName === "INPUT" ||
                t.isContentEditable;
            if (!isEditable) {
                hideMenu();
                return; // allow default context menu on non-editable areas
            }
            e.preventDefault();
            if (t.tagName === "TEXTAREA") t.focus();
            showMenu(e.clientX, e.clientY, t);
        });

        // Hide on click anywhere else, scroll, or Escape.
        document.addEventListener("click", function (e) {
            if (!menu.contains(e.target)) hideMenu();
        });
        document.addEventListener("scroll", hideMenu, true);
        document.addEventListener("keydown", function (e) {
            if (e.key === "Escape") hideMenu();
        });
        // Hide when window loses focus.
        window.addEventListener("blur", hideMenu);
    })();

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", probeBridge);
    } else {
        probeBridge();
    }
})();
