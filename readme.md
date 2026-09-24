# Prompt Optimizer

Desktop application that optimizes LLM prompt templates using a ReAct agent
loop. A "judge" LLM analyzes the prompt + its result and proposes
improvements; an "executor" LLM runs the modified prompt and compares the
output against an expected result. The loop repeats until the agent is
satisfied (or a max-attempt limit is reached).

The app is built on **pywebview** (Qt backend) — the UI is plain HTML/CSS/JS
talking to a Python bridge.

---

## Features

| # | Feature |
|---|---------|
| 3.1 | Add an LLM (name, api_base, api_key, model, temperature) |
| 3.2 | Edit an existing LLM |
| 3.3 | Remove an LLM (roles referencing it are cleared) |
| 3.4 | Assign LLMs to roles: **Judge** (ReAct analyzer) and **Prompts** (executor) |
| 3.5 | **Run prompt** — one-shot LLM call with the current template + variables |
| 3.6 | Validation — roles set, prompt non-empty, all `{{var}}` placeholders have values |
| 3.7 | **Start** — full automatic optimization loop (ReAct agent) |
| 3.7 HL | **Human-in-the-loop** — the agent pauses at the analyze step; the human reads the analysis prompt, types a corrected template + changes, and clicks **Continue** |

---

## Installation

Requires **Python 3.10+**.

```bash
pip install -r requirements.txt
```

Dependencies:

| Package | Purpose |
|---|---|
| `langchain-core`, `langgraph` | ReAct agent framework |
| `langchain-openai` | OpenAI-compatible chat model (any v1 API) |
| `PyYAML` | `config.yaml` persistence |
| `pywebview` + `PyQt6` + `PyQt6-WebEngine` | GUI (Qt/QtWebEngine backend) |
| `pytest` | test suite |

---

## Running

From the project root:

```bash
python scripts/main/app.py
```

The window opens at **1000 × 720** (min 800 × 600, resizable), titled
**"Prompt Optimizer"**. Two tabs: **Prompts** and **Settings**.

> **Headless / CI note:** the app requires a display. On a headless server
> set `QT_QPA_PLATFORM=offscreen` before launching.

---

## Configuration

All persistent state lives in **`config.yaml`** in the project root
(created automatically on first run from `scripts/template/config.yaml`
defaults). The UI reads and writes it through the Python bridge — no manual
editing required, though the file is plain YAML.

```yaml
llms:
  - name: my-judge            # unique name
    api_base: http://host:port/v1
    api_key: sk-...
    model: org/model-name
    temperature: 0.1
  - name: my-executor
    api_base: http://host:port/v1
    api_key: sk-...
    model: org/other-model
    temperature: 0.2

roles:
  judge: my-judge             # LLM name used as the ReAct analyzer
  prompts: my-executor        # LLM name used to execute the prompt

hl: false                     # Human-in-the-loop (analyze step → human)
max_attempts: 30              # max ReAct optimization iterations
```

| Key | Type | Description |
|---|---|---|
| `llms[]` | list | One entry per LLM: `name` (unique), `api_base`, `api_key`, `model`, `temperature` |
| `roles.judge` | str | LLM name for the ReAct agent that analyzes and rewrites prompts |
| `roles.prompts` | str | LLM name that executes the prompt and produces the result |
| `hl` | bool | `true` — the analyze step is delegated to a human (see below) |
| `max_attempts` | int | Upper bound on optimization iterations (default 30) |

---

## Using the app

### 1. Configure LLMs (Settings tab)

1. **＋ Add LLM** — fill in name, api_base, api_key, model, temperature, Save.
2. Select a row → **✎ Edit** or **✕ Remove**.
3. Under **Roles**, pick an LLM for **Judge** and an LLM for **Prompts**.
4. Optionally tick **Human-in-the-loop**.

All changes are saved to `config.yaml` immediately.

### 2. Prepare the prompt (Prompts tab)

1. Paste the prompt template into **Prompt** (`{{variable}}` placeholders
   supported, e.g. `Greet {{name}} about {{topic}}`).
2. Paste the **expected result** into **Result**.
3. Click **⟳ Extract variables** (or add/edit variables manually) — each
   variable gets a **Value** field.
4. Click **▶ Run prompt** to do a one-shot LLM call and see the raw output
   (useful before starting optimization).

### 3. Run optimization

Click **▶ Start** (Execution section). The Progress log streams the agent's
steps:

```
Validation passed
Workspace prepared: 3 files written to …/workspace
Agent configured (max_attempts=30)
Starting optimization loop …
…
Optimization finished: success=True, attempts=4, result=512 chars
```

The final optimized template appears in **Optimization result**.

#### Human-in-the-loop (HL) mode

When **hl** is enabled, the agent pauses at each analyze step:

1. The agent writes its analysis prompt to `workspace/srcprompt.txt` and
   blocks. The **Source prompt** field in the UI fills with that text
   (click **⧉ Copy** to send it to your clipboard — e.g. to paste into a
   chat with a more powerful LLM).
2. The Start button changes to **Continue** and is disabled while the agent
   is blocked.
3. Type the response in **Result prompt** using the template contract:

   ```
   ШАБЛОН: <corrected prompt template>
   ИЗМЕНЕНИЯ: <what was changed and why>
   ```

4. Click **Continue**. The agent thread resumes, parses the response, and
   keeps optimizing.

The same flow repeats for every analyze step until the run finishes.

---

## Workspace isolation

Each run starts with a **fresh** `workspace/` directory — previous artifacts
(`final_prompt.txt`, `srcprompt.txt`, `dstprompt.txt`, `prompt_V*.txt`) are
deleted by `write_run_files` before the agent starts. No leakage between
runs.

After a successful run the workspace contains:

| File | Content |
|---|---|
| `prompt.txt` | the template as entered |
| `result.txt` | the expected result |
| `{var}.txt` | one per variable |
| `final_prompt.txt` | the fully substituted prompt sent to the executor LLM |
| `srcprompt.txt` | (HL only) the agent's analysis prompt for the human |
| `dstprompt.txt` | (HL only) the human's response |
| `prompt_V1.txt`, `prompt_V2.txt`, … | versioned templates saved by the agent |

---

## Tests

```bash
python -m pytest scripts/tests/ -v
```

158 unit + integration tests (no real LLM calls — all LLM interactions are
mocked). Coverage includes config, prompt I/O, LLM layer, runner
orchestration, agent tools, validation, start_run, and the HL bridge.

---

## Project structure

```
├── config.yaml              # persistent config (auto-created)
├── requirements.txt
├── tasklist.md              # implementation plan (phases 0–8)
├── architecture.md          # component & data-flow design
├── scripts/
│   ├── main/app.py          # entry point (pywebview window)
│   ├── core/
│   │   ├── config.py        # ConfigManager (YAML load/save/validate)
│   │   ├── prompt_io.py     # variable extraction, workspace files, substitution
│   │   ├── llm.py           # LLM2Executor (one-shot OpenAI-compatible call)
│   │   ├── agent.py         # ReActAgent (langgraph) + HLBridge protocol
│   │   └── runner.py        # run_optimization / run_prompt_check orchestration
│   ├── ui/
│   │   ├── api.py           # Api bridge (JS ↔ Python) + GuiHLBridge
│   │   └── js/
│   │       ├── index.html   # UI markup (2 tabs + 2 dialogs)
│   │       ├── app.js       # all UI logic
│   │       └── style.css
│   ├── dev/                 # smoke / probe scripts (manual testing)
│   └── tests/               # pytest suite (158 tests)
└── workspace/               # per-run file sandbox (wiped each run)
```

---

## License

See [LICENSE](LICENSE).
