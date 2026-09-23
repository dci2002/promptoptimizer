# 1. Tech stack
- **Python 3.10+** (`list[str]` annotations)
- **langchain-core** — `@tool` decorators, messages
- **langchain-openai** — `ChatOpenAI` against any OpenAI-compatible endpoint
  (`api_base` is configurable; in the shipped config both LLMs point at
  local vLLM-style servers: qwen for llm1, gemma for llm2)
- **langgraph** — `create_react_agent` ReAct runtime
- **PyYAML** — configuration
- `reasoning_effort=None` is explicitly set on every LLM call to disable
  extended-thinking modes on the local servers
- **pywebview** — user interface on the Python side, vanilla JavaScript — for actions on the web page
- **key design template for execution** — implementation code in the file `/scripts/template/interview_copilot.py`
- **architectural design** — in the file `/scripts/template/design.md`

# 2. Interface
## Two tabs:
### 2.1. "Prompts"
- 2.1.1. **Prompt settings area** — contains the fields: "**Prompt**" (multiline field, 10 lines high, with a **button "Load from files"** to the right of the label), "**Result**" (with the **button "Run prompt"** and a **button "Refresh variables"**), **variables table** (with "Add", "Edit", "Remove" buttons) with columns: Variable, Value
- 2.1.2. **Execution area** — contains the **button "Start"**, the **"Progress" field** for tracking progress (multiline, 10 lines high), the **"Optimization result" field** — multiline field (10 lines high)
- 2.1.3. **"Human-in-the-loop" area** — contains the **"Human-in-the-loop" flag** and two multiline fields (10 lines high): **"Source prompt"** (next to the field — **a "Copy" button** — to the clipboard), **"Result prompt"** (available when the flag is enabled, otherwise cleared and disabled)
### 2.2. "Settings"
- 2.2.1. **"LLM list" table** with "Add", "Edit", "Remove" buttons, columns: name, api_base, api_key, model, temperature
- 2.2.2. Two fields **LLM as Judge**, **LLM for prompts** — dropdown list of LLM names from table 2.2.1

# 3. User scenarios
- **3.1. Add settings for a new LLM**
  - The user clicks the "Add" button
  - in the opened window, they set the settings: name (text), api_base (text), api_key (text), model (text), temperature (numeric, fractional) — all fields are required for saving
  - they click the "Save" button (or "Cancel" if no saving is needed) — if saving — the data is stored in the config.yaml file
- **3.2. Edit LLM settings**
  - the user selects the row with the settings to edit in table 2.2.1, opens the settings editing window by double-click or via the "Edit" button
  - in the opened window, they set the settings: name (text), api_base (text), api_key (text), model (text), temperature (numeric, fractional) — all fields are required for saving
  - they click the "Save" button (or "Cancel" if no saving is needed) — if saving — the data is stored in the config.yaml file
- **3.3. Delete LLM settings**
  - the user selects the row with the settings to delete in table 2.2.1
  - clicks the "Remove" button — after confirmation the row is removed from the list — the corresponding setting is removed from config.yaml
- **3.4. Select LLM as Judge, LLM for prompts settings**
  - the required LLM is selected from the dropdown list of LLM names in the settings table 2.2.1
  - after selection the choice is saved in the config.yaml file
- **3.5. Prompt configuration**
  - The user enters the prompt text into the "Prompt" field
  - The user adds or removes variables for the variables table (via the corresponding buttons) — when adding, a window with the fields "Variable name" (single-line) and "Variable value" (multiline, 5 lines high) opens
  - The user can click the "Edit" button (requires a selected row in the variables table) — opens the same window with the "Variable name" field read-only and the "Variable value" field pre-filled — saving updates the value in the table
  - The user can click the "Refresh variables" button — the system extracts all "{{Variable name}}" occurrences from the current "Prompt" field text and adds any new variables to the table (existing variables keep their values, their order in the table is preserved; new variables are appended at the end)
  - The user can click the "Load from files" button (next to the "Prompt" label) — the system reads the workspace files written by the last "Start" (prompt.txt, result.txt, {var}.txt) and fills: the "Prompt" field with the template, the "Result" field with the expected result, and the variables table with variable names extracted from the prompt and their values from the corresponding {var}.txt files (a variable whose file is absent gets an empty value); if prompt.txt is missing — an error message is shown and nothing is changed
  - The user enters the result into the "Result" field
  - The user can click the "Run prompt" button — if the check in item 3.6 passes — the system fills the prompt template with the variables — runs the prompt on LLM as Judge — the "Result" field is filled with the execution result
- **3.5.1. Context menu (right-click) in text fields**
  - right-clicking in any text field ("Prompt", "Result", "Source prompt", "Result prompt", "Progress", "Optimization result", dialog inputs) shows a context menu with "Copy", "Cut", "Paste"
  - "Copy" copies the current selection (or the whole field content if nothing is selected) to the system clipboard
  - "Cut" removes the selection from the field and copies it to the system clipboard
  - "Paste" inserts the system clipboard text at the cursor position (for the "Paste" action the clipboard is read on the Python side — the browser clipboard API is unavailable in the Qt WebEngine backend)
- **3.6. Checking that the settings are filled in for running the prompt** —
  - the system checks that:
    - the LLM as Judge and LLM for prompts settings are selected, and the settings for the corresponding LLM are set for the selected configurations
    - the prompt text is provided, and for the variables specified in the prompt text (in the "{{Variable name}}" format) — the values of all variables are set
  - if the settings are not filled in — the action is interrupted
- **3.7. Running the prompt in automatic mode**
  - started by the "Start" button (the button becomes disabled until processing is complete) with the "Human-in-the-loop" option disabled
  - the check from item 3.6 is performed — if it does not pass — the algorithm is not executed
  - writing to the source files for the processing loop (prompt.txt, result.txt, {{var}}.txt) is performed
  - the data processing loop is executed (the logic is in the template in /scripts/template/interview_copilot.py) — at the same time the logging of steps and tool calls is output to the "Progress" field
  - after the processing is complete, the resulting prompt is displayed in the "Optimization result" field (i.e., the agent must return the obtained prompt template)
- **3.7. Running the prompt in Human-in-the-loop mode**
  - started by the "Start" button (the button becomes disabled until processing is complete) with the "Human-in-the-loop" option enabled
  - the check from item 3.6 is performed — if it does not pass — the algorithm is not executed
  - writing to the source files for the processing loop (prompt.txt, result.txt, {{var}}.txt) is performed
  - the data processing loop is executed (the logic is in the template in /scripts/template/interview_copilot.py) — at the same time the logging of steps and tool calls is output to the "Progress" field
    - intermediate stage with prompt waiting
      - the "Start" button becomes active and its text changes to "Continue" (if the "Result prompt" field is pressed, it causes a user error)
      - the result from the file srcprompt.txt is displayed in the "Source prompt" field — it can be copied to the clipboard via the "Copy" button
      - the user enters text into the "Result prompt" field, clicks the "Continue" button (the button becomes inactive)
  - after the processing is complete, the resulting prompt is displayed in the "Optimization result" field (i.e., the agent must return the obtained prompt template)
# Styles
- if not explicitly specified — the fields are text, single-line
- buttons are displayed as icons, when hovering over a button a text tooltip appears
