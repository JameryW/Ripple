# tests/agents/test_robust_parser.py
# =============================================================================
# Robust JSON parser integration tests across agents
# =============================================================================
import json
import pytest
from unittest.mock import AsyncMock

from ripple.agents.omniscient import OmniscientAgent
from ripple.agents.star import StarAgent
from ripple.agents.sea import SeaAgent
from ripple.utils.json_parser import parse_json_from_llm


class TestOmniscientRobustParser:
    """Test OmniscientAgent._parse_json uses parse_json_from_llm."""

    def test_parse_plain_json(self):
        """Plain JSON is parsed correctly."""
        caller = AsyncMock()
        agent = OmniscientAgent(llm_caller=caller)
        raw = '{"key": "value"}'
        result = agent._parse_json(raw)
        assert result == {"key": "value"}

    def test_parse_markdown_fenced_json(self):
        """JSON in markdown fences is parsed correctly."""
        caller = AsyncMock()
        agent = OmniscientAgent(llm_caller=caller)
        raw = '```json\n{"key": "value"}\n```'
        result = agent._parse_json(raw)
        assert result == {"key": "value"}

    def test_parse_json_with_prose(self):
        """JSON mixed with prose is extracted by robust parser."""
        caller = AsyncMock()
        agent = OmniscientAgent(llm_caller=caller)
        raw = 'Here is the result:\n{"key": "value"}\nEnd of result.'
        result = agent._parse_json(raw)
        assert result == {"key": "value"}

    def test_parse_invalid_raises_json_decode_error(self):
        """Completely invalid input raises JSONDecodeError."""
        caller = AsyncMock()
        agent = OmniscientAgent(llm_caller=caller)
        with pytest.raises(json.JSONDecodeError):
            agent._parse_json("not json at all no braces")


class TestStarRobustParser:
    """Test StarAgent._parse_response uses parse_json_from_llm."""

    def test_parse_markdown_fenced(self):
        """StarAgent parses markdown-fenced JSON."""
        agent = StarAgent(
            llm_caller=AsyncMock(),
            agent_id="star_0",
            description="TestStar",
        )
        raw = '```json\n{"response_type": "amplify", "response_content": "test", "outgoing_energy": 0.8, "reasoning": "ok"}\n```'
        result = agent._parse_response(raw)
        assert result["response_type"] == "amplify"
        assert result["outgoing_energy"] == 0.8

    def test_parse_plain_json(self):
        """StarAgent parses plain JSON."""
        agent = StarAgent(
            llm_caller=AsyncMock(),
            agent_id="star_0",
            description="TestStar",
        )
        raw = '{"response_type": "amplify", "response_content": "test", "outgoing_energy": 0.8, "reasoning": "ok"}'
        result = agent._parse_response(raw)
        assert result["response_type"] == "amplify"


class TestSeaRobustParser:
    """Test SeaAgent._parse_response uses parse_json_from_llm."""

    def test_parse_markdown_fenced(self):
        """SeaAgent parses markdown-fenced JSON."""
        agent = SeaAgent(
            llm_caller=AsyncMock(),
            agent_id="sea_0",
            description="TestSea",
        )
        raw = '```json\n{"response_type": "amplify", "cluster_reaction": "test", "outgoing_energy": 0.5, "sentiment_shift": "up", "reasoning": "ok"}\n```'
        result = agent._parse_response(raw)
        assert result["response_type"] == "amplify"

    def test_parse_plain_json(self):
        """SeaAgent parses plain JSON."""
        agent = SeaAgent(
            llm_caller=AsyncMock(),
            agent_id="sea_0",
            description="TestSea",
        )
        raw = '{"response_type": "amplify", "cluster_reaction": "test", "outgoing_energy": 0.5, "sentiment_shift": "up", "reasoning": "ok"}'
        result = agent._parse_response(raw)
        assert result["response_type"] == "amplify"


class TestParseJsonFromLlmDirect:
    """Direct tests for parse_json_from_llm utility."""

    def test_plain_json(self):
        assert parse_json_from_llm('{"a": 1}') == {"a": 1}

    def test_fenced_json(self):
        result = parse_json_from_llm('```json\n{"a": 1}\n```')
        assert result == {"a": 1}

    def test_fenced_no_language(self):
        result = parse_json_from_llm('```\n{"a": 1}\n```')
        assert result == {"a": 1}

    def test_json_with_whitespace(self):
        result = parse_json_from_llm('  {"a": 1}  ')
        assert result == {"a": 1}
