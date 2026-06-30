"""Groq LLM client factory.

Creates a singleton :class:`langchain_groq.ChatGroq` instance configured
from environment settings.  Groq is the **primary** LLM provider for
CodeGuardian AI, chosen for its ultra-low inference latency.
"""

from __future__ import annotations

from functools import lru_cache

from langchain_groq import ChatGroq

from config import settings


@lru_cache(maxsize=1)
def get_groq_client() -> ChatGroq:
    """Return a cached :class:`ChatGroq` instance.

    Raises
    ------
    ValueError
        If ``GROQ_API_KEY`` is not configured.
    """
    if not settings.groq_api_key:
        raise ValueError(
            "GROQ_API_KEY is not set. Configure it in .env or the environment."
        )

    return ChatGroq(
        model=settings.groq_model,
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout_seconds,
        max_retries=0,  # Router handles fallback, not SDK retries
        api_key=settings.groq_api_key,
    )
