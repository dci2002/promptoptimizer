"""core.llm — LLM layer (Phase 4, T4.1 + T4.2).

Direct port of the template's ``LLM2Executor`` (``scripts/template/
interview_copilot.py``) plus the ``make_llm1`` factory used by the ReAct
agent in Phase 6.

Design rules:

- ``reasoning_effort=None`` on **every** ``ChatOpenAI`` instantiation
  (disables extended-thinking on local vLLM-style servers);
- API-key validation at call time: empty or placeholder key →
  ``ValueError`` with a hint pointing at the Settings tab / config.yaml;
- ``langchain_openai`` / ``langchain_core`` are imported lazily inside
  the call methods, so importing this module works in environments where
  langchain is not installed (headless tests mock at the ``ChatOpenAI``
  boundary).
"""

from __future__ import annotations

import os

__all__ = ["LLM2Executor", "make_llm1", "SYSTEM_MESSAGE"]

#: Placeholder keys rejected by the validators (template behavior).
_PLACEHOLDER_KEYS = ("", "your-api-key-here")

#: System message for the one-shot llm2 call (template, verbatim).
SYSTEM_MESSAGE = (
    "You are a helpful assistant. "
    "Respond directly without showing your reasoning."
)


def _check_api_key(api_key: str, label: str) -> None:
    """Raise ``ValueError`` for an empty / placeholder API key."""
    if not api_key or str(api_key).strip() in _PLACEHOLDER_KEYS:
        raise ValueError(
            f"{label} API key is not configured. "
            "Please open the Settings tab (or config.yaml) and set a real api_key."
        )


class LLM2Executor:
    """LLM2: executes the FINAL prompt and returns the raw result.

    Verbatim port of the template class:

    - reads the final prompt from ``{base_dir}/final_prompt.txt`` (written
      by :func:`core.prompt_io.build_final_prompt`);
    - calls the LLM via ``ChatOpenAI`` (OpenAI-compatible ``api_base``)
      with ``reasoning_effort=None``;
    - a fixed system message instructs the model to answer directly
      without showing its reasoning;
    - validates the API key before any network call.

    Parameters
    ----------
    config:
        LLM entry dict (``api_base``, ``api_key``, ``model``,
        ``temperature``) plus ``base_dir`` (workspace directory).
    """

    def __init__(self, config: dict) -> None:
        self.config = dict(config or {})
        self.api_base = self.config.get("api_base", "https://api.openai.com/v1")
        self.api_key = self.config.get("api_key", "")
        self.model = self.config.get("model", "gpt-4o")
        self.temperature = self.config.get("temperature", 0.0)
        self.base_dir = self.config.get("base_dir", ".")
        self.final_prompt_file = os.path.join(self.base_dir, "final_prompt.txt")

    def execute(self) -> str:
        """Read ``final_prompt.txt`` and return the raw LLM result.

        Raises
        ------
        ValueError
            If the API key is missing or a placeholder.
        FileNotFoundError
            If ``final_prompt.txt`` was not written yet.
        RuntimeError
            If the LLM call fails (any other exception).
        """
        # Validate API key.
        _check_api_key(str(self.api_key), "LLM (judge)")

        # Read the final prompt from the file written by build_final_prompt.
        if not os.path.exists(self.final_prompt_file):
            raise FileNotFoundError(
                f"Final prompt file not found: {self.final_prompt_file}. "
                "Make sure build_final_prompt was called first."
            )
        with open(self.final_prompt_file, "r", encoding="utf-8") as f:
            final_prompt = f.read()

        try:
            from langchain_openai import ChatOpenAI
            from langchain_core.messages import HumanMessage, SystemMessage

            llm = ChatOpenAI(
                model=self.model,
                temperature=self.temperature,
                openai_api_key=self.api_key,
                openai_api_base=self.api_base,
                reasoning_effort=None,
            )

            messages = [
                SystemMessage(content=SYSTEM_MESSAGE),
                HumanMessage(content=final_prompt),
            ]

            response = llm.invoke(messages)
            return response.content

        except ImportError as e:
            raise ImportError(
                f"Required langchain packages not installed: {e}\n"
                "Please install them with: pip install langchain-openai langchain-core"
            ) from e
        except (ValueError, FileNotFoundError):
            raise
        except Exception as e:
            raise RuntimeError(f"LLM call failed: {e}") from e


def make_llm1(llm_cfg: dict):
    """Factory for the ReAct agent's LLM (llm1) — Phase 6.

    Same ``reasoning_effort=None`` rule as :class:`LLM2Executor`; the
    verbose flag is taken from the config (default ``False`` for the GUI).

    Parameters
    ----------
    llm_cfg:
        LLM entry dict (``api_base``, ``api_key``, ``model``,
        ``temperature``); ``max_attempts`` may be present (ignored here).

    Returns
    -------
    ChatOpenAI

    Raises
    ------
    ValueError
        If the API key is missing or a placeholder.
    """
    from langchain_openai import ChatOpenAI

    api_base = llm_cfg.get("api_base", "https://api.openai.com/v1")
    api_key = llm_cfg.get("api_key", "")
    model = llm_cfg.get("model", "gpt-4o")
    temperature = llm_cfg.get("temperature", 0.1)
    verbose = llm_cfg.get("verbose", False)

    _check_api_key(str(api_key), "LLM (agent)")

    return ChatOpenAI(
        model=model,
        temperature=temperature,
        openai_api_key=api_key,
        openai_api_base=api_base,
        reasoning_effort=None,
        verbose=verbose,
    )
