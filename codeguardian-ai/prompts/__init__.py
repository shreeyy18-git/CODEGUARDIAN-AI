"""Agent prompt templates loaded from the prompts/ directory.

This module provides a single :func:`load_prompt` function that reads
prompt ``.txt`` files from disk and caches them in memory.

Usage::

    from prompts import load_prompt

    system_prompt = load_prompt("security")
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

__all__ = ["load_prompt"]

_PROMPTS_DIR: Path = Path(__file__).resolve().parent


@lru_cache(maxsize=16)
def load_prompt(name: str) -> str:
    """Load a prompt template by name (without the ``.txt`` extension).

    Parameters
    ----------
    name:
        The prompt file basename, e.g. ``"security"`` loads
        ``prompts/security.txt``.

    Returns
    -------
    str
        The full prompt text.

    Raises
    ------
    FileNotFoundError
        If ``prompts/{name}.txt`` does not exist.
    """
    path = _PROMPTS_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8")
