"""Tests for the LLM router fallback logic.

These tests mock the Groq and Gemini clients so no real API calls are
made.  Run with::

    pytest tests/test_llm_router.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from llm.router import LLMResponse, invoke_llm


# ── Helpers ─────────────────────────────────────────────────────────────


def _mock_response(content: str) -> MagicMock:
    """Build a fake LangChain response object with ``.content``."""
    resp = MagicMock()
    resp.content = content
    return resp


def _patch_settings(**overrides):
    """Patch :mod:`config.settings` with the given overrides."""
    return patch("llm.router.settings", **overrides)


# ── Tests ───────────────────────────────────────────────────────────────


class TestInvokeLLM:
    """Tests for :func:`llm.router.invoke_llm`."""

    def test_groq_success_no_fallback(self) -> None:
        """When Groq succeeds, Gemini is never called."""
        fake_settings = MagicMock()
        fake_settings.groq_api_key = "test-groq-key"
        fake_settings.gemini_api_key = "test-gemini-key"
        fake_settings.groq_model = "llama-3.3-70b-versatile"
        fake_settings.gemini_model = "gemini-2.0-flash"

        mock_groq = MagicMock()
        mock_groq.invoke.return_value = _mock_response("Groq says hello")

        mock_gemini = MagicMock()

        with (
            patch("llm.router.settings", fake_settings),
            patch("llm.groq_client.get_groq_client", return_value=mock_groq),
            patch("llm.gemini_client.get_gemini_client", return_value=mock_gemini),
        ):
            result = invoke_llm("system", "user")

        assert isinstance(result, LLMResponse)
        assert result.content == "Groq says hello"
        assert result.provider == "groq"
        assert result.model_name == "llama-3.3-70b-versatile"
        assert result.fell_back is False
        assert result.error is None
        mock_groq.invoke.assert_called_once()
        mock_gemini.invoke.assert_not_called()

    def test_groq_failure_falls_back_to_gemini(self) -> None:
        """When Groq raises, Gemini is used and ``fell_back`` is True."""
        fake_settings = MagicMock()
        fake_settings.groq_api_key = "test-groq-key"
        fake_settings.gemini_api_key = "test-gemini-key"
        fake_settings.groq_model = "llama-3.3-70b-versatile"
        fake_settings.gemini_model = "gemini-2.0-flash"

        mock_groq = MagicMock()
        mock_groq.invoke.side_effect = TimeoutError("Groq timed out")

        mock_gemini = MagicMock()
        mock_gemini.invoke.return_value = _mock_response("Gemini saves the day")

        with (
            patch("llm.router.settings", fake_settings),
            patch("llm.groq_client.get_groq_client", return_value=mock_groq),
            patch("llm.gemini_client.get_gemini_client", return_value=mock_gemini),
        ):
            result = invoke_llm("system", "user")

        assert result.content == "Gemini saves the day"
        assert result.provider == "gemini"
        assert result.model_name == "gemini-2.0-flash"
        assert result.fell_back is True
        assert isinstance(result.error, TimeoutError)
        mock_groq.invoke.assert_called_once()
        mock_gemini.invoke.assert_called_once()

    def test_both_providers_fail_raises_runtime_error(self) -> None:
        """When both Groq and Gemini fail, a RuntimeError is raised."""
        fake_settings = MagicMock()
        fake_settings.groq_api_key = "test-groq-key"
        fake_settings.gemini_api_key = "test-gemini-key"
        fake_settings.groq_model = "llama-3.3-70b-versatile"
        fake_settings.gemini_model = "gemini-2.0-flash"

        mock_groq = MagicMock()
        mock_groq.invoke.side_effect = ConnectionError("Groq down")

        mock_gemini = MagicMock()
        mock_gemini.invoke.side_effect = ConnectionError("Gemini down")

        with (
            patch("llm.router.settings", fake_settings),
            patch("llm.groq_client.get_groq_client", return_value=mock_groq),
            patch("llm.gemini_client.get_gemini_client", return_value=mock_gemini),
        ):
            with pytest.raises(RuntimeError, match="Both LLM providers failed"):
                invoke_llm("system", "user")

    def test_no_groq_key_skips_to_gemini(self) -> None:
        """When GROQ_API_KEY is empty, Gemini is used directly."""
        fake_settings = MagicMock()
        fake_settings.groq_api_key = ""
        fake_settings.gemini_api_key = "test-gemini-key"
        fake_settings.groq_model = "llama-3.3-70b-versatile"
        fake_settings.gemini_model = "gemini-2.0-flash"

        mock_gemini = MagicMock()
        mock_gemini.invoke.return_value = _mock_response("Direct Gemini")

        with (
            patch("llm.router.settings", fake_settings),
            patch("llm.gemini_client.get_gemini_client", return_value=mock_gemini),
        ):
            result = invoke_llm("system", "user")

        assert result.content == "Direct Gemini"
        assert result.provider == "gemini"
        assert result.fell_back is True

    def test_no_api_keys_raises_runtime_error(self) -> None:
        """When neither API key is set, RuntimeError is raised."""
        fake_settings = MagicMock()
        fake_settings.groq_api_key = ""
        fake_settings.gemini_api_key = ""

        with patch("llm.router.settings", fake_settings):
            with pytest.raises(RuntimeError, match="No LLM provider available"):
                invoke_llm("system", "user")

    def test_non_string_content_is_coerced(self) -> None:
        """When the LLM returns non-str content, it's coerced to str."""
        fake_settings = MagicMock()
        fake_settings.groq_api_key = "test-groq-key"
        fake_settings.gemini_api_key = "test-gemini-key"
        fake_settings.groq_model = "llama-3.3-70b-versatile"
        fake_settings.gemini_model = "gemini-2.0-flash"

        mock_groq = MagicMock()
        # Some providers return a list of content blocks
        mock_groq.invoke.return_value = _mock_response(["block1", "block2"])

        with (
            patch("llm.router.settings", fake_settings),
            patch("llm.groq_client.get_groq_client", return_value=mock_groq),
        ):
            result = invoke_llm("system", "user")

        assert isinstance(result.content, str)
        assert "block1" in result.content
