"""LLM router with automatic Groq → Gemini fallback.

This module exposes a single :func:`invoke_llm` function that agents call
instead of invoking a provider client directly.  The router tries Groq
first; on any error (timeout, rate-limit, connection, or generic
exception) it transparently falls back to Gemini and logs the event.

Usage::

    from llm.router import invoke_llm

    response = invoke_llm(system_prompt, user_prompt)
    print(response.content)        # The LLM's text output
    print(response.provider)       # "groq" or "gemini"
    print(response.model_name)     # e.g. "llama-3.3-70b-versatile"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage

from config import settings

_log = logging.getLogger("codeguardian.llm.router")


@dataclass
class LLMResponse:
    """Normalized response returned by :func:`invoke_llm`.

    Attributes
    ----------
    content:
        The raw text output from the LLM.
    provider:
        Which provider handled the call — ``"groq"`` or ``"gemini"``.
    model_name:
        The specific model identifier (e.g. ``"llama-3.3-70b-versatile"``).
    fell_back:
        ``True`` if the primary (Groq) call failed and Gemini was used.
    error:
        The exception that caused the fallback, if any.
    """

    content: str
    provider: str
    model_name: str
    fell_back: bool = False
    error: Optional[Exception] = None


def _invoke(provider: str, system_prompt: str, user_prompt: str) -> str:
    """Call the named provider and return the raw text content.

    Parameters
    ----------
    provider:
        ``"groq"`` or ``"gemini"``.
    system_prompt:
        The system / instruction message.
    user_prompt:
        The human / user message (typically the code diff).

    Returns
    -------
    str
        The LLM's text response.
    """
    if provider == "groq":
        from llm.groq_client import get_groq_client

        client = get_groq_client()
        model_name = settings.groq_model
    elif provider == "gemini":
        from llm.gemini_client import get_gemini_client

        client = get_gemini_client()
        model_name = settings.gemini_model
    else:  # pragma: no cover — defensive guard
        raise ValueError(f"Unknown LLM provider: {provider!r}")

    response = client.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ])
    _log.debug("LLM call succeeded via %s (%s)", provider, model_name)
    return response.content if isinstance(response.content, str) else str(response.content), model_name


def invoke_llm(system_prompt: str, user_prompt: str) -> LLMResponse:
    """Invoke the LLM with automatic Groq → Gemini fallback.

    Tries Groq first.  On any exception (timeout, rate-limit, connection
    error, or generic failure) it falls back to Gemini and records the
    error in the returned :class:`LLMResponse`.

    Parameters
    ----------
    system_prompt:
        The system / instruction message for the agent.
    user_prompt:
        The human / user message — typically the code diff to review.

    Returns
    -------
    LLMResponse
        Normalized response with ``content``, ``provider``,
        ``model_name``, ``fell_back``, and ``error`` fields.

    Raises
    ------
    RuntimeError
        If **both** Groq and Gemini fail (or if neither API key is
        configured).
    """
    # ── Attempt 1: Groq (primary) ──────────────────────────────────────
    if settings.groq_api_key:
        try:
            content, model_name = _invoke("groq", system_prompt, user_prompt)
            return LLMResponse(
                content=content,
                provider="groq",
                model_name=model_name,
                fell_back=False,
            )
        except Exception as exc:  # noqa: BLE001 — we want to catch everything
            _log.warning(
                "Groq call failed (%s: %s) — falling back to Gemini.",
                type(exc).__name__,
                exc,
            )
            groq_error = exc
    else:
        _log.info("GROQ_API_KEY not set — skipping Groq, using Gemini directly.")
        groq_error = ValueError("GROQ_API_KEY not configured")

    # ── Attempt 2: Gemini (fallback) ────────────────────────────────────
    if settings.gemini_api_key:
        try:
            content, model_name = _invoke("gemini", system_prompt, user_prompt)
            return LLMResponse(
                content=content,
                provider="gemini",
                model_name=model_name,
                fell_back=True,
                error=groq_error,
            )
        except Exception as exc:  # noqa: BLE001
            _log.error(
                "Gemini fallback also failed (%s: %s).",
                type(exc).__name__,
                exc,
            )
            raise RuntimeError(
                f"Both LLM providers failed. Groq error: {groq_error}. "
                f"Gemini error: {exc}."
            ) from exc

    raise RuntimeError(
        "No LLM provider available. Set GROQ_API_KEY and/or GEMINI_API_KEY."
    )
