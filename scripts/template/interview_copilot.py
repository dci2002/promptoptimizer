#!/usr/bin/env python3
"""
Interview Copilot - Prompt optimization agent.

Architecture:
- llm1: ReAct agent (langchain / langgraph) that analyzes, checks and corrects prompts.
- llm2: A tool exposed to the ReAct agent. It is a simple LLM call via langchain
        that executes a given prompt and returns the raw result.

Flow:
    1. Build the final prompt from a template + variable files.
    2. llm1 (ReAct agent) uses the llm2 tool to execute the prompt.
    3. llm1 compares the result with the expected result (result.txt).
    4. If the result does not match, llm1 analyzes the diff and produces an
       improved prompt, then repeats the cycle.
    5. The loop stops when the result matches or max attempts is reached.
"""

import os
import re
import yaml
import sys
from pathlib import Path
import time
from typing import Optional

from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent


# ============================================================
# Configuration
# ============================================================

def load_config(config_path: str = "config.yaml") -> dict:
    """Load configuration from config.yaml."""
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ============================================================
# LLM2 - Simple LLM call (tool for the ReAct agent)
# ============================================================

class LLM2Executor:
    """
    LLM2: Executes the FINAL prompt and returns the result.
    Uses OpenAI API via langchain.
    This is the tool that the ReAct agent (llm1) calls.

    NOTE: llm2 reads the FINAL prompt from the file `final_prompt.txt`
    (written by build_final_prompt). The agent only passes the template
    to `build_prompt`; it never passes the final prompt or variable values
    directly to llm2.
    """

    def __init__(self, config: dict):
        self.config = config
        self.api_base = config.get("api_base", "https://api.openai.com/v1")
        self.api_key = config.get("api_key", "")
        self.model = config.get("model", "gpt-4o")
        self.temperature = config.get("temperature", 0.0)
        self.base_dir = config.get("base_dir", ".")
        self.final_prompt_file = os.path.join(self.base_dir, "final_prompt.txt")

    def execute(self) -> str:
        """
        Read the FINAL prompt from `final_prompt.txt` (written by build_final_prompt),
        execute it with the LLM, and return the raw result.
        Uses OpenAI API via langchain.
        """
        # Validate API key
        if not self.api_key or self.api_key == "your-api-key-here":
            raise ValueError(
                "API key is not configured. Please edit config.yaml and set the api_key value."
            )

        # Read the final prompt from the file written by build_final_prompt.
        if not os.path.exists(self.final_prompt_file):
            raise FileNotFoundError(
                f"Final prompt file not found: {self.final_prompt_file}. "
                "Make sure build_final_prompt (build_prompt tool) was called first."
            )
        with open(self.final_prompt_file, "r", encoding="utf-8") as f:
            final_prompt = f.read()

        try:
            from langchain_openai import ChatOpenAI
            from langchain_core.messages import HumanMessage, SystemMessage

            # Disable thinking/reasoning mode
            llm = ChatOpenAI(
                model=self.model,
                temperature=self.temperature,
                openai_api_key=self.api_key,
                openai_api_base=self.api_base,
                reasoning_effort=None,
            )

            # Add system message to disable thinking
            messages = [
                SystemMessage(content="You are a helpful assistant. Respond directly without showing your reasoning."),
                HumanMessage(content=final_prompt),
            ]

            response = llm.invoke(messages)
            return response.content

        except ImportError as e:
            raise ImportError(
                f"Required langchain packages not installed: {e}\n"
                "Please install them with: pip install langchain langchain-openai"
            )
        except Exception as e:
            raise RuntimeError(f"LLM call failed: {e}")


# ============================================================
# Prompt Variable Extraction
# ============================================================

def extract_variables_from_prompt(prompt_text: str) -> list[str]:
    """
    Extract variable names from prompt template.
    Variables are in format {variable_name}.
    """
    variables = re.findall(r'\{\{(\w+)\}\}', prompt_text)
    return list(set(variables))


def build_final_prompt(prompt_template: str, base_dir: str) -> str:
    """
    Build the FINAL prompt from a template.

    Only variable NAMES (placeholders {var_name}) are present in the template.
    This function reads each variable's value from a text file named
    `{var_name}.txt` inside `base_dir`, substitutes them into the template,
    and saves the result to `final_prompt.txt` (also in `base_dir`).

    The LLM2 executor later reads the prompt from `final_prompt.txt`.
    """
    variables = extract_variables_from_prompt(prompt_template)
    final_prompt = prompt_template
    for var in variables:
        file_path = os.path.join(base_dir, f"{var}.txt")
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                value = f.read()
        else:
            value = ""
            print(f"[WARNING] Variable file not found: {file_path}")
        final_prompt = final_prompt.replace("{{"+var+"}}", value)

    # Save the fully-substituted prompt to final_prompt.txt for LLM2 to read.
    final_prompt_file = os.path.join(base_dir, "final_prompt.txt")
    with open(final_prompt_file, "w", encoding="utf-8") as f:
        f.write(final_prompt)

    return final_prompt_file


def save_final_prompt(prompt: str, attempt: int, output_dir: str) -> str:
    """
    Save the final prompt as prompt_V[n].txt.
    """
    filename = os.path.join(output_dir, f"prompt_V{attempt}.txt")
    os.makedirs(output_dir, exist_ok=True)
    with open(filename, "w", encoding="utf-8") as f:
        f.write(prompt)
    return filename


# ============================================================
# Result Comparison
# ============================================================

def load_result(result_path: str) -> str:
    """Load expected result from result.txt."""
    with open(result_path, "r", encoding="utf-8") as f:
        return f.read().strip()


# ============================================================
# LLM1 - ReAct Agent for Prompt Optimization
# ============================================================

class ReActAgent:
    """
    LLM1: A proper langchain / langgraph ReAct agent that:
      - Works only with the prompt TEMPLATE (variable NAMES in {var_name} placeholders).
      - Uses the build_prompt tool (Python): reads each variable's .txt file by name,
        substitutes values into the template and writes the FINAL prompt to final_prompt.txt.
      - Uses the llm2 tool: llm2 reads the prompt from final_prompt.txt, executes it
        and returns the raw result.
      - Compares the result with the expected result.
      - Analyzes discrepancies and produces an improved prompt TEMPLATE.
      - Repeats until the result matches or max attempts is reached.

    The agent is built with `create_agent` from `langchain.agents`.

    Key design: Variable values NEVER go to the agent or to llm2 directly.
    The build_prompt tool (Python code) reads the variable files by name and writes
    final_prompt.txt; llm2 then reads final_prompt.txt to obtain the final prompt.
    """

    SYSTEM_PROMPT = """\
 Ты — ReAct-агент по оптимизации промптов для LLM.
  
  Твоя задача — итеративно улучшать ШАБЛОН промпта (с переменными {{переменная}}),
  чтобы результат его выполнения (полученный через инструменты `build_prompt` + `llm2`)
  совпадал с ожидаемым результатом из result.txt.
  
  # Доступные инструменты
  1. `build_prompt(template)` — Python-инструмент: по именам переменных из шаблона читает
     текстовые файлы {{имя_переменной}}.txt, подставляет значения в шаблон и сохраняет
     ФИНАЛЬНЫЙ промпт в файл final_prompt.txt. Ты НЕ должен делать подстановку сам
     и НЕ передаёшь значения переменных — только ШАБЛОН с именами переменных.
  2. `llm2()` — читает ФИНАЛЬНЫЙ промпт из файла final_prompt.txt, выполняет его
     и возвращает сырой результат LLM. Не принимает аргументов.
  3. `explain_result(analysis_request, llm2_result)` — объясняет, почему результат llm2
     не совпадает с ожидаемым. Строит многораундовый диалог для llm2:
     user (финальный промпт) → assistant (результат llm2) → user (запрос на анализ).
     Вызывается, когда результат llm2 НЕ совпадает с ожидаемым.
     В `analysis_request` передай список уточняющих вопросов (не менее 3) о том,
     какие фразы промпта повлияли на результат. Результат — JSON-массив объектов
     вида [{{"question": "...", "answer": "..."}}].
  4. `analyze_prompt(current_template, expected_result, actual_result, explain_result_answer)` —
     анализатор промпта: разбивает промпт на смысловые единицы, анализирует влияние каждой
     с учётом ожидаемого и полученного результата, формирует и возвращает ИСПРАВЛЕННЫЙ шаблон промпта.
     Четвёртый аргумент `explain_result_answer` (опциональный) — JSON-массив ответов от `explain_result`,
     вставляется в промпт анализа как целевой контекст расхождения.
     Используй этот инструмент для улучшения промпта, когда результат не совпадает с ожидаемым.
  5. `save_prompt(template)` — сохраняет текущую версию ШАБЛОНА промпта
     (откорректированную по результатам анализа) в файл prompt_V[номер_попытки].txt.
     Номер попытки отслеживается автоматически.
  6. `finish(final_template)` — завершает работу, если результат уже совпадает или достигнут максимум попыток.
     Аргумент — лучший полученный ШАБЛОН промпта (с переменными {{переменная}}).
  
  # Алгоритм работы
  1. Возьми текущий ШАБЛОН промпта (с переменными {{переменная}}).
  2. Вызови инструмент `build_prompt` с этим шаблоном — он прочитает файлы переменных
     по именам и сохранит финальный промпт в final_prompt.txt.
  3. Вызови инструмент `llm2` — он прочитает final_prompt.txt, выполнит промпт
     и вернёт результат.
  4. Сравни полученный результат с ожидаемым результатом (из result.txt) самостоятельно:
     проанализируй содержимое, структуру, наличие/отсутствие лишних или недостающих элементов.
  5. Если результат совпадает с ожидаемым — заверши работу через `finish`, вернув текущий ШАБЛОН промпта.
  6. Если не совпадает:
     6a. Вызови инструмент `explain_result`, передав:
        - `analysis_request`: список уточняющих вопросов (НЕ МЕНЕЕ 3) о том, какие
          фразы/компоненты промпта привели к расхождению с ожидаемым результатом
          (например: "какие фразы промпта привели к тому, что...", "почему модель
          сгенерировала X вместо Y", "какая часть промпта повлияла на структуру ответа").
        - `llm2_result`: результат генерации, полученный на шаге 3 (результат от llm2).
        Инструмент вернёт объяснение в формате JSON-массива объектов
        вида [{{"question": "...", "answer": "..."}}].
     6b. Вызови инструмент `analyze_prompt`, передав:
        - `current_template`: текущий шаблон промпта
        - `expected_result`: ожидаемый результат
        - `actual_result`: результат от llm2 (шаг 3)
        - `explain_result_answer`: JSON-массив ответов от `explain_result` (шаг 6a)
        Инструмент вернёт ИСПРАВЛЕННЫЙ шаблон промпта.
  7. Сохрани новый шаблон через `save_prompt` и используй его в следующей итерации (вернись к шагу 2).
  8. Не превышай лимит в {max_attempts} попыток. При исчерпании верни лучшую полученную версию ШАБЛОНА.
  
  # Требования к ответу
  - Всегда работай с ШАБЛОНОМ промпта (с именами переменных в фигурных скобках), а не с финальным промптом.
  - Значения переменных не передавай ни в build_prompt, ни в llm2 — их читают Python-функции по именам.
  - Не изменяй имена переменных в шаблоне — сохраняй все исходные имена переменных из переданного шаблона.
  - Не добавляй markdown-разметку, пояснения или код-блоки в сам шаблон промпта.
  - При сравнении результатов оценивай семантическую эквивалентность, а не только точное совпадение.
  """

    def __init__(
        self,
        llm2_executor: LLM2Executor,
        llm1_config: dict,
        max_attempts: int = 30,
        hl: bool = False,
    ):
        self.llm2 = llm2_executor
        self.llm1_config = llm1_config
        self.max_attempts = max_attempts
        self.hl = hl
        self.attempt_history: list[dict] = []
        self._llm = None
        self._agent = None
        self._current_attempt = 1
        self._change_history: list[str] = []

    # ----- Lazy initialization of the langchain LLM and ReAct agent -----

    def _get_llm(self):
        """Create (once) the ChatOpenAI instance used by the ReAct agent (llm1)."""
        if self._llm is None:
            from langchain_openai import ChatOpenAI

            api_base = self.llm1_config.get("api_base", "https://api.openai.com/v1")
            api_key = self.llm1_config.get("api_key", "")
            model = self.llm1_config.get("model", "gpt-4o")
            temperature = self.llm1_config.get("temperature", 0.1)
            verbose = self.llm1_config.get("verbose", True)

            if not api_key or api_key == "your-api-key-here":
                raise ValueError(
                    "llm1 API key is not configured. Please edit config.yaml and set llm1.api_key."
                )

            self._llm = ChatOpenAI(
                model=model,
                temperature=temperature,
                openai_api_key=api_key,
                openai_api_base=api_base,
                reasoning_effort=None,
                verbose=verbose,
            )
        return self._llm

    # ----- Tools the ReAct agent can call -----

    def _make_tools(self, prompt_template: str, expected_result: str, base_dir: str, output_dir: str) -> list:
        """Build the list of tools available to the ReAct agent."""

        @tool
        def build_prompt(template: str) -> str:
            """
            Build the FINAL prompt from a template (Python-side).

            Reads each variable's value from a text file named {var_name}.txt
            (based on the variable NAMES found in the template), substitutes them
            into the template and saves the result to final_prompt.txt.

            Args:
                template: The prompt template containing variable NAMES as {variable_name} placeholders.

            Returns:
                The path to final_prompt.txt where the fully-substituted prompt was saved.
            """
            return build_final_prompt(template, base_dir)

        @tool
        def llm2() -> str:
            """
            Execute the FINAL prompt with LLM (llm2).

            The final prompt is read from the file final_prompt.txt
            (written by the build_prompt tool). Takes no arguments.

            Returns:
                The raw result from the LLM.
            """
            return self.llm2.execute()

        @tool
        def explain_result(analysis_request: str, llm2_result: str) -> str:
            """
            Explain why the llm2 generation result does not match the expected result.

            Builds a multi-turn conversation for llm2:
              - user message: the FINAL prompt (read from final_prompt.txt)
              - assistant message: the llm2 generation result (llm2_result)
              - user message: a request to explain which phrases caused the result,
                based on the given analysis request.
            Then invokes llm2 with these messages and returns the raw result.

            Use this tool when the llm2 result does NOT match the expected result.
            Pass a list of clarification questions (at least 3) in analysis_request —
            e.g. which parts of the prompt you want the model to explain or clarify.

            Args:
                analysis_request: The analysis request — a list of clarification
                    questions (at least 3) asking which phrases in the prompt
                    influenced the result.
                llm2_result: The generation result obtained from the llm2 tool
                    (the raw LLM output that does not match the expected result).

            Returns:
                A JSON array of objects, each with "question" and "answer" keys,
                e.g. [{"question": "...", "answer": "..."}, ...]
            """
            # Read the final prompt from the file written by build_prompt.
            if not os.path.exists(self.llm2.final_prompt_file):
                raise FileNotFoundError(
                    f"Final prompt file not found: {self.llm2.final_prompt_file}. "
                    "Make sure build_prompt tool was called before explain_result."
                )
            with open(self.llm2.final_prompt_file, "r", encoding="utf-8") as f:
                final_prompt = f.read()

            # Build the follow-up user message.
            follow_up_prompt = (
                f"ВАЖНО! ОБЪЯСНИ какие фразы {analysis_request}. "
                "Результат верни в формате JSON — массив объектов, "
                "где каждый объект содержит ключи \"question\" и \"answer\". "
                "Пример: [{\"question\": \"...\", \"answer\": \"...\"}, {\"question\": \"...\", \"answer\": \"...\"}]"
            )

            # Validate API key
            if not self.llm2.api_key or self.llm2.api_key == "your-api-key-here":
                raise ValueError(
                    "llm2 API key is not configured. Please edit config.yaml and set llm2.api_key."
                )

            try:
                from langchain_openai import ChatOpenAI
                from langchain_core.messages import HumanMessage, AIMessage

                llm = ChatOpenAI(
                    model=self.llm2.model,
                    temperature=self.llm2.temperature,
                    openai_api_key=self.llm2.api_key,
                    openai_api_base=self.llm2.api_base,
                    reasoning_effort=None,
                )

                # Build the multi-turn message array:
                # 1. user: the final prompt
                # 2. assistant: the llm2 generation result
                # 3. user: the analysis request
                messages = [
                    HumanMessage(content=final_prompt),
                    AIMessage(content=llm2_result),
                    HumanMessage(content=follow_up_prompt),
                ]

                response = llm.invoke(messages)
                return response.content

            except ImportError as e:
                raise ImportError(
                    f"Required langchain packages not installed: {e}\n"
                    "Please install them with: pip install langchain langchain-openai"
                )
            except Exception as e:
                raise RuntimeError(f"LLM call failed in explain_result: {e}")

        @tool
        def save_prompt(template: str) -> str:
            """
            Save a version of the prompt TEMPLATE (corrected based on analysis)
            as prompt_V[attempt].txt in the output directory.
            The attempt number is tracked automatically.

            Args:
                template: The prompt TEMPLATE (with variable NAMES as {variable_name} placeholders).

            Returns:
                A message confirming the save and the file path.
            """
            path = save_final_prompt(template, self._current_attempt, output_dir)
            self._current_attempt += 1
            return f"Saved template version {self._current_attempt - 1} to {path}"

        @tool
        def analyze_prompt(current_template: str, expected_result: str, actual_result: str, explain_result_answer: str = "") -> str:
            """
            Analyze the current prompt template and generate an improved version.

            This tool uses a separate LLM (llm1) to:
              1. Break down the prompt into semantic units that influence the response.
              2. Analyze the impact of each semantic unit by comparing expected and actual results.
              3. Formulate an improved prompt based on the analysis.
              4. Generate a brief summary of the changes made.

            Args:
                current_template: The current prompt TEMPLATE (with variable NAMES as {variable_name} placeholders).
                expected_result: The expected/required result (from result.txt).
                actual_result: The actual result obtained from llm2 execution.
                explain_result_answer: Optional. The JSON explanation from explain_result
                    (array of {"question": ..., "answer": ...} objects). This is inserted
                    into the analysis prompt as additional context about which phrases
                    in the prompt caused the discrepancy.

            Returns:
                The improved prompt TEMPLATE (with the same variable NAMES).
            """
            llm = self._get_llm()

            # Build change history section
            change_history_section = ""
            if self._change_history:
                change_history_text = "\n".join(
                    f"  {i+1}. {change}" for i, change in enumerate(self._change_history)
                )
                change_history_section = f"""
# История предыдущих изменений (для контекста):
{change_history_text}

Учитывай предыдущие изменения, чтобы не дублировать их и продолжить улучшать промпт.
"""

            # Build explain result section (answers from explain_result tool)
            explain_section = ""
            if explain_result_answer and explain_result_answer.strip():
                explain_section = f"""
# Объяснение расхождения (результат explain_result):
{explain_result_answer}

Используй эти ответы как дополнительный контекст: они указывают, какие именно
фразы/компоненты промпта привели к расхождению с ожидаемым результатом.
Особое внимание удели именно этим компонентам при формировании исправленного шаблона.
"""

            # Print change history to console
            if self._change_history:
                print("\n" + "="*60)
                print("[ANALYZE_PROMPT] История предыдущих изменений:")
                print("="*60)
                for i, change in enumerate(self._change_history):
                    print(f"  {i+1}. {change}")
                print("="*60 + "\n")
            else:
                print("\n[ANALYZE_PROMPT] Нет предыдущих изменений (первая итерация)\n")

            # Build the list of provided items
            provided_items = "1. Текущий ШАБЛОН промпта (с переменными в фигурных скобках)\n2. Ожидаемый результат\n3. Полученный результат"
            if explain_result_answer and explain_result_answer.strip():
                provided_items += "\n4. Объяснение расхождения (ответы на уточняющие вопросы от explain_result)"

            analysis_prompt = f"""Ты — эксперт по оптимизации промптов для LLM.

Тебе предоставлены:
{provided_items}
{change_history_section}{explain_section}
# Текущий шаблон промпта:
{current_template}

# Ожидаемый результат:
{expected_result}

# Полученный результат:
{actual_result}

# Твоя задача:
1. Разбить текущий промпт на смысловые единицы (компоненты), которые влияют на генерацию ответа.
2. Проанализировать влияние каждой смысловой единицы, сравнивая ожидаемый и полученный результаты.
   Определи, какие компоненты работают хорошо, а какие приводят к расхождениям.
   Если предоставлено объяснение расхождения (ответы от explain_result) — используй его
   как целевой контекст: особое внимание удели компонентам, указанным в ответах.
3. Сформируй ИСПРАВЛЕННЫЙ шаблон промпта на основе анализа.
   - Сохрани все имена переменных из исходного шаблона (не изменяй их)
   - Улучши те компоненты, которые приводят к расхождениям
   - Убери или перепиши компоненты, которые мешают правильному результату
   - Не добавляй markdown-разметку, пояснения или код-блоки
4. Сформулируй КРАТКОЕ ОПИСАНИЕ ИЗМЕНЕНИЙ (1-2 предложения), что именно ты изменил в промпте и почему.

# ФОРМАТ ОТВЕТА:
ВЕРНИ ОТВЕТ В СЛЕДУЮЩЕМ ФОРМАТЕ (строго):

ШАБЛОН:
[исправленный шаблон промпта]

ИЗМЕНЕНИЯ:
[краткое описание изменений]

Важно: между "ШАБЛОН:" и "ИЗМЕНЕНИЯ:" не должно быть пустых строк.
"""

            if self.hl:
                # Human-in-the-loop: write analysis prompt to srcprompt.txt,
                # clear dstprompt.txt, then wait until dstprompt.txt has content,
                # and read the response from it.
                base_dir_for_files = Path(__file__).parent.resolve()
                src_prompt_file = base_dir_for_files / "srcprompt.txt"
                dst_prompt_file = base_dir_for_files / "dstprompt.txt"

                print(f"\n[HL] Writing analysis prompt to: {src_prompt_file}")
                with open(src_prompt_file, "w", encoding="utf-8") as f:
                    f.write(analysis_prompt)

                print(f"[HL] Clearing: {dst_prompt_file}")
                with open(dst_prompt_file, "w", encoding="utf-8") as f:
                    f.write("")

                print(f"[HL] Waiting for dstprompt.txt to be filled...")
                while True:
                    if dst_prompt_file.exists():
                        content = dst_prompt_file.read_text(encoding="utf-8").strip()
                        if content:
                            break
                    time.sleep(1)

                print(f"[HL] Read response from dstprompt.txt")
                response_text = content.strip()
            else:
                response = llm.invoke(analysis_prompt)
                response_text = response.content.strip()

            # Parse response: extract template and changes
            template = response_text
            changes = ""

            if "ИЗМЕНЕНИЯ:" in response_text:
                parts = response_text.split("ИЗМЕНЕНИЯ:", 1)
                template_part = parts[0]
                changes = parts[1].strip()

                if template_part.startswith("ШАБЛОН:"):
                    template = template_part[len("ШАБЛОН:"):].strip()
                else:
                    template = template_part.strip()

            # Add changes to history
            if changes:
                self._change_history.append(changes)
                print(f"[ANALYZE_PROMPT] Добавлено в историю изменений: {changes}")

            return template

        @tool
        def finish(final_template: str) -> str:
            """
            End the optimization process.

            Call this tool when:
              - the result matches the expected result, or
              - the maximum number of attempts has been reached.

            Args:
                final_template: The best / final prompt TEMPLATE (with variable NAMES as {variable_name} placeholders).
            """
            self._final_prompt = final_template
            return "DONE"

        return [build_prompt, llm2, explain_result, analyze_prompt, save_prompt, finish]

    # ----- Main entry point -----

    def run(
        self,
        prompt_template: str,
        expected_result: str,
        base_dir: str,
        output_dir: str,
    ) -> dict:
        """
        Run the ReAct agent optimization loop.

        Returns:
            dict with final result, match status, and history.
        """
        self._final_prompt: Optional[str] = None

        # Create the langchain agent.
        llm = self._get_llm()
        tools = self._make_tools(
            prompt_template=prompt_template,
            expected_result=expected_result,
            base_dir=base_dir,
            output_dir=output_dir,
        )
        system_prompt = self.SYSTEM_PROMPT.format(max_attempts=self.max_attempts)

        agent = create_react_agent(
            model=llm,
            tools=tools,
            prompt=system_prompt,
        )

        # Compose the user task for the agent.
        # NOTE: We do NOT pass variable values to the agent. The agent only sees the TEMPLATE.
        # Variable substitution is done by the build_prompt tool (Python code), which
        # reads the variable files by name and writes final_prompt.txt.
        task = (
            "Начни оптимизацию промпта.\n\n"
            f"# Исходный шаблон промпта (с именами переменных в фигурных скобках):\n```\n{prompt_template}\n```\n"
            f"# Ожидаемый результат (result.txt):\n```\n{expected_result}\n```\n\n"
            "Работай с шаблоном промпта (не с финальным промптом). "
            "Для каждого шага: "
            "1) Вызови build_prompt с текущим шаблоном — он прочитает файлы переменных по именам "
            "и сохранит финальный промпт в final_prompt.txt. "
            "2) Вызови llm2 — он прочитает final_prompt.txt, выполнит промпт и вернёт результат. "
            "3) Сравни результат с ожидаемым. "
            "4) Если совпало — вызови finish с текущим шаблоном и результатом объяснением - почему ты решил что полученный результат соответствует ожидаемумом"
            "5) Если не совпало: "
            "   5a) Вызови explain_result, передав: "
            "      - analysis_request: список уточняющих вопросов (НЕ МЕНЕЕ 3) о том, какие фразы промпта "
            "        привели к расхождению с ожидаемым результатом "
            "        (например: 'какие фразы промпта привели к тому, что...', "
            "        'почему модель сгенерировала X вместо Y', "
            "        'какая часть промпта повлияла на структуру ответа'). "
            "      - llm2_result: результат генерации, полученный на шаге 2 (от llm2). "
            "      Инструмент вернёт объяснение в формате JSON-массива объектов вида [{\"question\": \"...\", \"answer\": \"...\"}]. "
            "   5b) Вызови analyze_prompt, передав: current_template (текущий шаблон), "
            "      expected_result (ожидаемый результат), actual_result (результат от llm2), "
            "      explain_result_answer (JSON-массив от explain_result). "
            "      Он вернёт исправленный шаблон. "
            "   Сохрани его через save_prompt и используй в следующей итерации. "
            f"Не превышай {self.max_attempts} попыток."
        )

        print("[INFO] Starting ReAct agent (llm1)...")
        # Invoke the agent. `recursion_limit` caps the number of agent steps.
        final_state = agent.invoke(
            {"messages": [("user", task)]},
            config={"recursion_limit": self.max_attempts * 10},
        )

        # Extract the final prompt TEMPLATE: prefer the one passed to `finish`,
        # otherwise fall back to the last AIMessage content.
        final_template = self._final_prompt
        if final_template is None:
            messages = final_state.get("messages", [])
            for msg in reversed(messages):
                content = getattr(msg, "content", None)
                if content and isinstance(content, str) and len(content) > 50:
                    final_template = content
                    break

        # If we still have no template, fall back to the initial one.
        if final_template is None:
            final_template = prompt_template

        # Build the final prompt from the template: reads variable files by name
        # and writes final_prompt.txt (Python-side substitution).
        print("[INFO] Building final prompt from template (Python-side)...")
        final_prompt_file = build_final_prompt(final_template, base_dir)

        # Execute the final prompt one last time to obtain the final result.
        # llm2 reads the prompt from final_prompt.txt.
        print("[INFO] Executing final prompt with llm2 (reads final_prompt.txt)...")
        final_result = self.llm2.execute()

        # The agent self-comparisons; we cannot deterministically know the match status here.
        self.attempt_history.append({
            "attempt": 1,
            "result_preview": final_result[:200],
        })

        # We report success based on whether the agent finished successfully (called finish).
        success = self._final_prompt is not None
        if success:
            print(f"\n{'='*60}")
            print("[SUCCESS] Agent finished optimization.")
            print(f"{'='*60}")
        else:
            print(f"\n{'='*60}")
            print("[WARNING] Agent did not call finish; using last available template.")
            print(f"{'='*60}")

        return {
            "success": success,
            "attempts": 1,
            "final_result": final_result,
            "final_prompt_file": final_prompt_file,
            "final_template": final_template,
            "history": self.attempt_history,
        }


# ============================================================
# Main Entry Point
# ============================================================

def main():
    """Main entry point for the interview copilot."""
    # Determine base directory
    base_dir = Path(__file__).parent.resolve()
    config_path = base_dir / "config.yaml"
    prompt_path = base_dir / "prompt.txt"
    result_path = base_dir / "result.txt"

    # Load configuration
    print("[INFO] Loading configuration...")
    config = load_config(str(config_path))

    llm1_config = config.get("llm1", {})
    llm2_config = config.get("llm2", {})
    max_attempts = llm1_config.get("max_attempts", 30)

    # LLM2 reads the final prompt from final_prompt.txt inside this directory.
    llm2_config.setdefault("base_dir", str(base_dir))

    # Initialize llm2 executor (the simple LLM call used as a tool by llm1)
    print("[INFO] Initializing llm2 executor (tool for ReAct agent)...")
    llm2_executor = LLM2Executor(llm2_config)

    # Load prompt template
    print("[INFO] Loading prompt template...")
    with open(prompt_path, "r", encoding="utf-8") as f:
        prompt_template = f.read()

    # Extract variables from prompt
    variables = extract_variables_from_prompt(prompt_template)
    print(f"[INFO] Found variables: {variables}")

    # Load expected result
    print("[INFO] Loading expected result...")
    expected_result = load_result(str(result_path))

    # Create ReAct agent (llm1)
    print("[INFO] Initializing llm1 (langchain ReAct agent)...")
    hl = config.get("HL", False)
    react_agent = ReActAgent(
        llm2_executor=llm2_executor,
        llm1_config=llm1_config,
        max_attempts=max_attempts,
        hl=hl,
    )

    # Run optimization loop
    print("[INFO] Starting prompt optimization...")
    result = react_agent.run(
        prompt_template=prompt_template,
        expected_result=expected_result,
        base_dir=str(base_dir),
        output_dir=str(base_dir),
    )

    # Print final summary
    print(f"\n{'='*60}")
    print("[FINAL SUMMARY]")
    print(f"{'='*60}")
    print(f"Success: {result['success']}")
    print(f"Attempts: {result['attempts']}")

    if result['success']:
        print("\n[RESULT] Agent finished optimization successfully.")
        print(f"\nFinal result:\n{result['final_result']}")
    else:
        print(f"\n[RESULT] Agent did not call finish.")
        print(f"\nBest result:\n{result['final_result']}")
        print(f"\nExpected result:\n{expected_result}")

    # Print history
    print(f"\n{'='*60}")
    print("[ATTEMPT HISTORY]")
    print(f"{'='*60}")
    for entry in result['history']:
        print(f"  Attempt {entry['attempt']}:")
        print(f"    Preview: {entry['result_preview'][:100]}...")

    return 0 if result['success'] else 1


if __name__ == "__main__":
    sys.exit(main())
