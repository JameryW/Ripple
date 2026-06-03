# tests/agents/test_synth_truncation.py
# =============================================================================
# SYNTHESIZE prompt truncation and max_tokens override tests
# =============================================================================
import json
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ripple.agents.omniscient import (
    OmniscientAgent,
    SYNTHESIZE_MAX_SNAPSHOT_CHARS,
    SYNTHESIZE_MAX_OBS_CHARS,
    SYNTHESIZE_MAX_INPUT_CHARS,
    SYNTHESIZE_MAX_TOKENS_OVERRIDE,
)


class TestTruncateJson:
    """Test _truncate_json static method."""

    def test_under_limit_no_truncation(self):
        """Small JSON is returned unchanged."""
        data = {"key": "value"}
        result = OmniscientAgent._truncate_json(data, max_chars=1000)
        assert result == json.dumps(data, ensure_ascii=False, indent=2, default=str)

    def test_over_limit_truncated_with_marker(self):
        """Large JSON is truncated with TRUNCATED marker."""
        data = {"key": "x" * 1000}
        result = OmniscientAgent._truncate_json(data, max_chars=100)
        assert len(result) > 100  # includes marker text
        assert "TRUNCATED" in result
        # Check that the marker contains the total size info
        assert "chars total" in result

    def test_exact_limit_no_truncation(self):
        """JSON at exactly the limit is not truncated."""
        data = {"key": "value"}
        serialized = json.dumps(data, ensure_ascii=False, indent=2, default=str)
        result = OmniscientAgent._truncate_json(data, max_chars=len(serialized))
        assert "TRUNCATED" not in result

    def test_unicode_handling(self):
        """Unicode characters are preserved in truncation."""
        data = {"key": "中文测试"}
        result = OmniscientAgent._truncate_json(data, max_chars=1000)
        assert "中文测试" in result

    def test_env_var_defaults(self):
        """SYNTHESIZE constants have expected defaults."""
        assert SYNTHESIZE_MAX_SNAPSHOT_CHARS == 20000
        assert SYNTHESIZE_MAX_OBS_CHARS == 15000
        assert SYNTHESIZE_MAX_INPUT_CHARS == 5000
        assert SYNTHESIZE_MAX_TOKENS_OVERRIDE == 8192


class TestBuildSynthPrompt:
    """Test _build_synth_prompt uses truncation."""

    def test_build_synth_prompt_truncates_large_snapshot(self):
        """_build_synth_prompt truncates large field_snapshot."""
        caller = AsyncMock(return_value='{"prediction": {}}')
        agent = OmniscientAgent(llm_caller=caller)

        large_snapshot = {"data": "x" * 50000}
        observation = {"obs": "small"}
        simulation_input = {"input": "small"}

        system_prompt, user_prompt = agent._build_synth_prompt(
            large_snapshot, observation, simulation_input,
        )

        # snapshot should be truncated — TRUNCATED marker must appear
        assert "TRUNCATED" in user_prompt

    def test_build_synth_prompt_small_data_unchanged(self):
        """_build_synth_prompt does not truncate small data."""
        caller = AsyncMock(return_value='{"prediction": {}}')
        agent = OmniscientAgent(llm_caller=caller)

        snapshot = {"key": "value"}
        observation = {"obs": "small"}
        simulation_input = {"input": "small"}

        system_prompt, user_prompt = agent._build_synth_prompt(
            snapshot, observation, simulation_input,
        )

        # No truncation marker for small data
        assert "TRUNCATED" not in user_prompt


class TestSynthesizeMaxTokensOverride:
    """Test that synthesize_result uses SYNTHESIZE_MAX_TOKENS_OVERRIDE."""

    @pytest.mark.asyncio
    async def test_synthesize_sets_phase_timeout(self):
        """synthesize_result sets phase timeout budget."""
        caller = AsyncMock(return_value='{"prediction": {"spread_prob": 0.5}, "timeline": [], "bifurcation_points": [], "agent_insights": {}}')
        agent = OmniscientAgent(llm_caller=caller)

        with patch("ripple.engine.runtime._PHASE_TIMEOUTS_ENABLED", True):
            with patch("ripple.engine.runtime._resolve_phase_timeout", return_value=180):
                await agent.synthesize_result(
                    field_snapshot={"agents": []},
                    observation={"obs": "test"},
                    simulation_input={"input": "test"},
                )

        assert agent._current_phase == "SYNTHESIZE"
        assert agent._phase_time_budget == 180

    @pytest.mark.asyncio
    async def test_synthesize_call_timeout_180s(self):
        """synthesize_result passes call_timeout=180.0 to _call_llm."""
        caller = AsyncMock(return_value='{"prediction": {"spread_prob": 0.5}, "timeline": [], "bifurcation_points": [], "agent_insights": {}}')
        agent = OmniscientAgent(llm_caller=caller)

        # Mock _call_llm to capture call_timeout parameter
        original_call_llm = agent._call_llm
        call_args = {}

        async def mock_call_llm(user_prompt, phase="", phase_system_prompt="", call_timeout=None):
            call_args["call_timeout"] = call_timeout
            return '{"prediction": {"spread_prob": 0.5}, "timeline": [], "bifurcation_points": [], "agent_insights": {}}'

        agent._call_llm = mock_call_llm

        with patch("ripple.engine.runtime._PHASE_TIMEOUTS_ENABLED", False):
            await agent.synthesize_result(
                field_snapshot={"agents": []},
                observation={"obs": "test"},
                simulation_input={"input": "test"},
            )

        assert call_args["call_timeout"] == 180.0
