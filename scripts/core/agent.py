"""core.agent — ReAct optimization agent (Phase 5: T5.1 + T5.2 + T5.3).

Port of the template ``ReActAgent`` (``scripts/template/interview_copilot.py``)
with two deliberate changes (architecture §3.4):

1. **Logging hooks.** The constructor accepts ``on_event: Callable[[str], None]``.
   Every step and tool call emits an event string via ``_emit`` (e.g.
   ``"[TOOL] llm2: 412 chars"``). The runner forwards events to the UI
   "Progress" field. Logging never breaks the run (swallows exceptions).

2. **Human-in-the-loop bridge.** The template's HL mode polls
   ``dstprompt.txt`` in a blocking ``while True`` loop — incompatible with a
   GUI. Instead, in HL mode ``analyze_prompt`` writes the analysis prompt to
   ``{base_dir}/srcprompt.txt`` and calls ``self._hl_bridge.wait_for_response()``
   (architecture §3.8). The non-HL path is equivalent to the template.

Design rules:

- ``langchain`` / ``langgraph`` are imported **lazily** (inside
  :meth:`_get_llm` and :meth:`run`), so importing this module works in
  environments where langchain is not installed (headless tests mock at the
  ``create_react_agent`` boundary);
- ``reasoning_effort=None`` on every ``ChatOpenAI`` instantiation (via
  :func:`core.llm.make_llm1`);
- Variable values NEVER reach the agent or llm2 — the ``build_prompt`` tool
  (Python) reads ``{var}.txt`` files by name and writes ``final_prompt.txt``;
- The agent works only with the prompt **template** (``{{var}}`` placeholders).

Tools (unchanged from the template): ``build_prompt``, ``llm2``,
``explain_result``, ``analyze_prompt``, ``save_prompt``, ``finish``.
``recursion_limit = max_attempts * 10``.
"""

from __future__ import annotations

import os
import threading
from typing import Callable, Optional, Protocol

from core.llm import LLM2Executor, make_llm1
from core.prompt_io import build_final_prompt

__all__ = ["HLBridge", "RunStopped", "ReActAgent"]


class RunStopped(Exception):
    """Raised when the user requests a stop (Stop button / window close)."""
    ...


class HLBridge(Protocol):
    """Human-in-the-loop bridge (architecture §3.4, §3.8).

    In HL mode the agent must pause while a human reviews the analysis prompt
    and supplies a response. The template did this with a blocking
    ``while True`` poll of ``dstprompt.txt`` — incompatible with a GUI.
    This protocol replaces that loop with an explicit blocking call that the
    runner / API layer implements with a ``threading.Event``:

    - the agent writes the analysis prompt to ``{base_dir}/srcprompt.txt``;
    - the agent calls :meth:`wait_for_response` and blocks until the human
      clicks "Continue" in the UI;
    - the method returns the human's response text (parsed downstream as
      ``ШАБЛОН:`` / ``ИЗМЕНЕНИЯ:``).

    Implemented by ``ui`` (Phase 7). In Phase 5 only the non-HL path is
    exercised; the HL path is present and tested with a stub bridge.
    """

    def wait_for_response(self) -> str:
        """Block until the human supplies a response; return the text.

        Raises
        ------
        Exception
            If the wait is interrupted or the human cancels (propagated to the
            agent and surfaced by the runner).
        """
        ...


class ReActAgent:
    """LLM1: a langchain / langgraph ReAct agent that optimizes a prompt template.

    - Works only with the prompt **template** (variable NAMES in ``{{var}}`` placeholders).
    - Uses the ``build_prompt`` tool (Python): reads each variable's ``.txt`` file by name,
      substitutes values into the template and writes the FINAL prompt to ``final_prompt.txt``.
    - Uses the ``llm2`` tool: reads ``final_prompt.txt``, executes it and returns the raw result.
    - Compares the result with the expected result.
    - Analyzes discrepancies and produces an improved prompt template.
    - Repeats until the result matches or ``max_attempts`` is reached.

    The agent is built with ``create_react_agent`` from ``langgraph``.

    Key design: variable values NEVER go to the agent or to llm2 directly.
    The ``build_prompt`` tool (Python) reads the variable files by name and
    writes ``final_prompt.txt``; llm2 then reads ``final_prompt.txt``.

    Parameters
    ----------
    llm2_executor:
        The judge LLM executor (``LLM2Executor``) — runs the final prompt.
    llm1_config:
        The agent LLM entry dict (``api_base``, ``api_key``, ``model``,
        ``temperature``); ``max_attempts`` may be present.
    max_attempts:
        Cap on optimization iterations (default 30).
    hl:
        Human-in-the-loop mode (default False).
    on_event:
        Optional log callback; receives step / tool-call strings.
    hl_bridge:
        Optional :class:`HLBridge`; required when ``hl`` is True.
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
        on_event: Optional[Callable[[str], None]] = None,
        hl_bridge: Optional[HLBridge] = None,
        stop_event: Optional[threading.Event] = None,
    ):
        self.llm2 = llm2_executor
        self.llm1_config = dict(llm1_config or {})
        self.max_attempts = int(max_attempts)
        self.hl = bool(hl)
        self.on_event = on_event
        self.hl_bridge = hl_bridge
        self.stop_event = stop_event
        self.attempt_history: list[dict] = []
        self._llm = None
        self._current_attempt = 1
        self._change_history: list[str] = []
        self._final_prompt: Optional[str] = None

    # ----- Event logging (replaces print) -----

    def _emit(self, msg: str) -> None:
        """Forward an event string to ``on_event`` (never raises)."""
        if self.on_event is None:
            return
        try:
            self.on_event(msg)
        except Exception:
            pass  # logging must never break the run

    # ----- Stop check -----

    def _check_stop(self) -> None:
        """Raise ``RunStopped`` if the stop_event is set."""
        if self.stop_event is not None and self.stop_event.is_set():
            self._emit("[STOP] Run stopped by user")
            raise RunStopped()

    # ----- Lazy initialization of the langchain LLM -----

    def _get_llm(self):
        """Create (once) the ChatOpenAI instance used by the agent (llm1).

        Uses :func:`core.llm.make_llm1` so ``reasoning_effort=None`` and API-key
        validation are consistent with the rest of the LLM layer.
        """
        if self._llm is None:
            self._emit("[INIT] Creating agent LLM (llm1) …")
            self._llm = make_llm1(self.llm1_config)
        return self._llm

    # ----- Tools the ReAct agent can call -----

    def _make_tools(self, base_dir: str, output_dir: str) -> list:
        """Build the list of tools available to the ReAct agent.

        Parameters
        ----------
        base_dir:
            The workspace directory (holds ``final_prompt.txt``,
            ``{var}.txt`` files, and ``srcprompt.txt`` in HL mode).
        output_dir:
            Directory where versioned templates are saved
            (``prompt_V{n}.txt``).
        """
        from langchain_core.tools import tool

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
            self._check_stop()
            path = build_final_prompt(template, base_dir)
            self._emit(f"[TOOL] build_prompt -> {os.path.basename(path)}")
            return path

        @tool
        def llm2() -> str:
            """
            Execute the FINAL prompt with LLM (llm2).

            The final prompt is read from the file final_prompt.txt
            (written by the build_prompt tool). Takes no arguments.

            Returns:
                The raw result from the LLM.
            """
            self._check_stop()
            result = self.llm2.execute()
            self._emit(f"[TOOL] llm2: {len(result)} chars")
            return result

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
            self._check_stop()
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

            try:
                from langchain_openai import ChatOpenAI
                from langchain_core.messages import AIMessage, HumanMessage

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
                result = response.content
                self._emit(f"[TOOL] explain_result: {len(result)} chars")
                return result

            except ImportError as e:
                raise ImportError(
                    f"Required langchain packages not installed: {e}\n"
                    "Please install them with: pip install langchain langchain-openai"
                )
            except (ValueError, FileNotFoundError):
                raise
            except Exception as e:
                raise RuntimeError(f"LLM call failed in explain_result: {e}") from e

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
            self._check_stop()
            path = self._save_template(template, output_dir)
            self._emit(f"[TOOL] save_prompt -> {os.path.basename(path)}")
            return f"Saved template version {self._current_attempt - 1} to {path}"

        @tool
        def analyze_prompt(
            current_template: str,
            expected_result: str,
            actual_result: str,
            explain_result_answer: str = "",
        ) -> str:
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
            self._check_stop()
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

            # Emit change-history context (replaces template print block).
            if self._change_history:
                self._emit(f"[ANALYZE_PROMPT] {len(self._change_history)} prior changes in context")
            else:
                self._emit("[ANALYZE_PROMPT] first iteration (no prior changes)")

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
                # Human-in-the-loop (architecture §3.4, §3.8):
                # 1. write the analysis prompt to {base_dir}/srcprompt.txt;
                # 2. block until the human supplies a response via the bridge.
                if self.hl_bridge is None:
                    raise RuntimeError(
                        "HL mode requires an hl_bridge (HLBridge instance) to be injected."
                    )
                src_prompt_file = os.path.join(base_dir, "srcprompt.txt")
                self._emit(f"[HL] Writing analysis prompt to {src_prompt_file}")
                with open(src_prompt_file, "w", encoding="utf-8") as f:
                    f.write(analysis_prompt)
                self._emit("[HL] Waiting for human response …")
                response_text = self.hl_bridge.wait_for_response().strip()
                self._emit(f"[HL] Received response ({len(response_text)} chars)")
            else:
                response = llm.invoke(analysis_prompt)
                response_text = response.content.strip()

            # Parse response: extract template and changes (template behavior).
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

            # Add changes to history.
            if changes:
                self._change_history.append(changes)
                self._emit(f"[TOOL] analyze_prompt -> template updated ({len(template)} chars), change logged")
            else:
                self._emit(f"[TOOL] analyze_prompt -> template updated ({len(template)} chars)")

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
            self._check_stop()
            self._final_prompt = final_template
            self._emit(f"[TOOL] finish: {len(final_template)} chars template accepted")
            return "DONE"

        return [build_prompt, llm2, explain_result, analyze_prompt, save_prompt, finish]

    # ----- Versioned template save (helper) -----

    def _save_template(self, template: str, output_dir: str) -> str:
        """Write ``prompt_V{attempt}.txt`` in ``output_dir`` and bump the counter.

        Equivalent to the template's ``save_final_prompt`` helper, inlined here
        to avoid an extra import and to keep the version counter inside the
        agent instance.
        """
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, f"prompt_V{self._current_attempt}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(template)
        self._current_attempt += 1
        return path

    # ----- Main entry point -----

    def run(self, prompt_template: str, expected_result: str, base_dir: str, output_dir: str) -> dict:
        """Run the ReAct agent optimization loop.

        Parameters
        ----------
        prompt_template:
            The initial prompt template (``{{var}}`` placeholders).
        expected_result:
            The expected result text (from ``result.txt``).
        base_dir:
            The workspace directory (holds ``final_prompt.txt``,
            ``{var}.txt`` files, and ``srcprompt.txt`` in HL mode).
        output_dir:
            Directory where versioned templates are saved.

        Returns
        -------
        dict
            ``{"success", "attempts", "final_result", "final_prompt_file",
            "final_template", "history"}``
        """
        self._final_prompt = None

        # Create the langchain agent (lazy langgraph import).
        from langgraph.prebuilt import create_react_agent

        llm = self._get_llm()
        tools = self._make_tools(base_dir=base_dir, output_dir=output_dir)
        system_prompt = self.SYSTEM_PROMPT.format(max_attempts=self.max_attempts)

        self._emit("[AGENT] Building ReAct agent …")
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

        self._emit("[AGENT] Starting ReAct agent (llm1) …")
        # Invoke the agent. `recursion_limit` caps the number of agent steps.
        try:
            final_state = agent.invoke(
                {"messages": [("user", task)]},
                config={"recursion_limit": self.max_attempts * 10},
            )
        except RunStopped:
            # Propagate the stop to the runner.
            raise

        # Check if stopped after agent.invoke() returns (e.g., stop was set
        # during the final llm2 call or while the agent was finishing).
        self._check_stop()

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

        # Build the final prompt from the template (Python-side substitution).
        self._check_stop()
        self._emit("[AGENT] Building final prompt from template …")
        final_prompt_file = build_final_prompt(final_template, base_dir)

        # Execute the final prompt one last time to obtain the final result.
        self._emit("[AGENT] Executing final prompt with llm2 …")
        final_result = self.llm2.execute()

        self.attempt_history.append(
            {
                "attempt": 1,
                "result_preview": final_result[:200],
            }
        )

        # Success = the agent called finish (self-comparison passed).
        success = self._final_prompt is not None
        if success:
            self._emit("[AGENT] SUCCESS — agent finished optimization")
        else:
            self._emit("[AGENT] WARNING — agent did not call finish; using last template")

        return {
            "success": success,
            "attempts": 1,
            "final_result": final_result,
            "final_prompt_file": final_prompt_file,
            "final_template": final_template,
            "history": self.attempt_history,
        }
