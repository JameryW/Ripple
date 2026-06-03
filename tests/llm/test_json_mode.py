# tests/llm/test_json_mode.py
# =============================================================================
# json_mode config field and adapter response_format tests
# =============================================================================
import pytest
from ripple.llm.config import ModelEndpointConfig
from ripple.llm.chat_completions_adapter import ChatCompletionsAdapter
from ripple.llm.anthropic_adapter import AnthropicAdapter
from ripple.llm.responses_adapter import ResponsesAPIAdapter


class TestJsonModeConfig:
    """Test json_mode field in ModelEndpointConfig."""

    def test_default_is_false(self):
        """json_mode defaults to False."""
        config = ModelEndpointConfig(
            model_platform="openai",
            model_name="gpt-4o",
        )
        assert config.json_mode is False

    def test_from_dict_true(self):
        """json_mode=True via from_dict."""
        config = ModelEndpointConfig.from_dict({
            "model_name": "gpt-4o",
            "json_mode": True,
        })
        assert config.json_mode is True

    def test_from_dict_false(self):
        """json_mode=False via from_dict."""
        config = ModelEndpointConfig.from_dict({
            "model_name": "gpt-4o",
            "json_mode": False,
        })
        assert config.json_mode is False

    def test_from_dict_missing_defaults_false(self):
        """json_mode defaults to False when not specified."""
        config = ModelEndpointConfig.from_dict({
            "model_name": "gpt-4o",
        })
        assert config.json_mode is False

    def test_from_dict_string_model(self):
        """Shorthand model string defaults json_mode to False."""
        config = ModelEndpointConfig.from_dict("gpt-4o")
        assert config.json_mode is False


class TestJsonModeChatCompletionsAdapter:
    """Test ChatCompletionsAdapter json_mode behavior."""

    def test_json_mode_true_adds_response_format(self):
        """When json_mode=True, response_format is added."""
        adapter = ChatCompletionsAdapter(
            url="https://api.openai.com/v1",
            api_key="test-key",
            model="gpt-4o",
            json_mode=True,
        )
        body = adapter._build_request("system", "user")
        assert "response_format" in body
        assert body["response_format"] == {"type": "json_object"}

    def test_json_mode_false_no_response_format(self):
        """When json_mode=False, no response_format is added."""
        adapter = ChatCompletionsAdapter(
            url="https://api.openai.com/v1",
            api_key="test-key",
            model="gpt-4o",
            json_mode=False,
        )
        body = adapter._build_request("system", "user")
        assert "response_format" not in body

    def test_from_endpoint_config_passes_json_mode(self):
        """from_endpoint_config passes json_mode from config."""
        config = ModelEndpointConfig.from_dict({
            "model_name": "gpt-4o",
            "url": "https://api.openai.com/v1",
            "api_key": "test",
            "json_mode": True,
        })
        adapter = ChatCompletionsAdapter.from_endpoint_config(config)
        assert adapter._json_mode is True


class TestJsonModeAnthropicAdapter:
    """Test AnthropicAdapter json_mode behavior."""

    def test_json_mode_true_adds_prefill(self):
        """When json_mode=True, assistant prefill with \\n{ is appended."""
        adapter = AnthropicAdapter(
            api_key="test-key",
            model="claude-sonnet-4-20250514",
            json_mode=True,
        )
        body = adapter._build_request("system", "user")
        messages = body["messages"]
        assert len(messages) == 2
        assert messages[-1]["role"] == "assistant"
        assert messages[-1]["content"] == "\n{"

    def test_json_mode_false_no_prefill(self):
        """When json_mode=False, no assistant prefill is appended."""
        adapter = AnthropicAdapter(
            api_key="test-key",
            model="claude-sonnet-4-20250514",
            json_mode=False,
        )
        body = adapter._build_request("system", "user")
        messages = body["messages"]
        assert len(messages) == 1  # Only user message
        assert messages[0]["role"] == "user"

    def test_from_endpoint_config_passes_json_mode(self):
        """from_endpoint_config passes json_mode from config."""
        config = ModelEndpointConfig.from_dict({
            "model_name": "claude-sonnet-4-20250514",
            "api_key": "test",
            "model_platform": "anthropic",
            "json_mode": True,
        })
        adapter = AnthropicAdapter.from_endpoint_config(config)
        assert adapter._json_mode is True


class TestJsonModeResponsesAdapter:
    """Test ResponsesAPIAdapter json_mode behavior."""

    def test_json_mode_true_adds_response_format(self):
        """When json_mode=True, response_format is added."""
        adapter = ResponsesAPIAdapter(
            url="https://api.openai.com/v1",
            api_key="test-key",
            model="gpt-4o",
            json_mode=True,
        )
        body = adapter._build_request("system", "user")
        assert "response_format" in body
        assert body["response_format"] == {"type": "json_object"}

    def test_json_mode_false_no_response_format(self):
        """When json_mode=False, no response_format is added."""
        adapter = ResponsesAPIAdapter(
            url="https://api.openai.com/v1",
            api_key="test-key",
            model="gpt-4o",
            json_mode=False,
        )
        body = adapter._build_request("system", "user")
        assert "response_format" not in body