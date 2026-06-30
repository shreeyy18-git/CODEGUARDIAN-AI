"""Gemini LLM client factory.

Creates a singleton :class:`langchain_google_genai.ChatGoogleGenerativeAI`
instance configured from environment settings.  Gemini is the **fallback**
LLM provider, used when Groq is unavailable (timeout, rate-limit, or error).
"""

from __future__ import annotations

from functools import lru_cache

from langchain_google_genai import ChatGoogleGenerativeAI

from config import settings


@lru_cache(maxsize=1)
def get_gemini_client() -> ChatGoogleGenerativeAI:
    """Return a cached :class:`ChatGoogleGenerativeAI` instance.

    Raises
    ------
    ValueError
        If ``GEMINI_API_KEY`` is not configured.
    """
    if not settings.gemini_api_key:
        raise ValueError(
            "GEMINI_API_KEY is not set. Configure it in .env or the environment."
        )

    return ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout_seconds,
        max_retries=0,  # Router handles errors, not SDK retries
        google_api_key=settings.gemini_api_key,
    )
