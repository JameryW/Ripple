# tests/engine/test_phase_timeouts.py
# =============================================================================
# Per-phase and job-level timeout tests
# =============================================================================
import asyncio
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ripple.engine.runtime import (
    SimulationRuntime,
    PhaseTimeoutError,
    _resolve_phase_timeout,
    _PHASE_TIMEOUTS_DEFAULTS,
    _PHASE_TIMEOUTS_ENABLED,
    JOB_TIMEOUT,
)


class TestPhaseTimeoutConstants:
    """Test phase timeout defaults and env var resolution."""

    def test_default_timeouts_exist(self):
        """All default phases have timeout values."""
        for phase in ["INIT", "SEED", "RIPPLE", "DELIBERATE", "OBSERVE", "SYNTHESIZE"]:
            assert phase in _PHASE_TIMEOUTS_DEFAULTS
            assert _PHASE_TIMEOUTS_DEFAULTS[phase] > 0

    def test_ripple_has_longest_timeout(self):
        """RIPPLE phase has the longest timeout (wave loop)."""
        assert _PHASE_TIMEOUTS_DEFAULTS["RIPPLE"] == 1200

    def test_synthesize_timeout(self):
        """SYNTHESIZE phase has 180s timeout."""
        assert _PHASE_TIMEOUTS_DEFAULTS["SYNTHESIZE"] == 180

    def test_job_timeout_default(self):
        """Default job timeout is 1800s (30min)."""
        assert JOB_TIMEOUT == 1800

    def test_resolve_phase_timeout_returns_default(self):
        """_resolve_phase_timeout returns default when no env var set."""
        # Clear any env var that might be set
        with patch.dict(os.environ, {}, clear=False):
            # Remove the specific env var if it exists
            os.environ.pop("RIPPLE_PHASE_TIMEOUT_INIT", None)
            assert _resolve_phase_timeout("INIT") == 60

    def test_resolve_phase_timeout_from_env(self):
        """_resolve_phase_timeout reads from env var."""
        with patch.dict(os.environ, {"RIPPLE_PHASE_TIMEOUT_INIT": "120"}):
            assert _resolve_phase_timeout("INIT") == 120.0

    def test_resolve_unknown_phase_returns_300(self):
        """Unknown phases get 300s default."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RIPPLE_PHASE_TIMEOUT_UNKNOWN", None)
            assert _resolve_phase_timeout("UNKNOWN") == 300


class TestPhaseTimeoutError:
    """Test PhaseTimeoutError exception."""

    def test_error_message(self):
        err = PhaseTimeoutError("SYNTHESIZE", 180)
        assert err.phase == "SYNTHESIZE"
        assert err.timeout == 180
        assert "SYNTHESIZE" in str(err)
        assert "180" in str(err)


class TestRunPhaseWithTimeout:
    """Test _run_phase method wraps coroutines with timeout."""

    @pytest.mark.asyncio
    async def test_run_phase_no_timeout(self):
        """_run_phase completes when timeout is disabled."""
        runtime = SimulationRuntime.__new__(SimulationRuntime)

        async def quick_coro():
            return "done"

        # Simulate _PHASE_TIMEOUTS_ENABLED = False
        with patch("ripple.engine.runtime._PHASE_TIMEOUTS_ENABLED", False):
            result = await runtime._run_phase(
                quick_coro(), "TEST", "run1"
            )
            assert result == "done"

    @pytest.mark.asyncio
    async def test_run_phase_timeout_raises(self):
        """_run_phase raises PhaseTimeoutError when coroutine exceeds timeout."""
        runtime = SimulationRuntime.__new__(SimulationRuntime)

        async def slow_coro():
            await asyncio.sleep(10)
            return "done"

        with patch("ripple.engine.runtime._PHASE_TIMEOUTS_ENABLED", True):
            with patch("ripple.engine.runtime._resolve_phase_timeout", return_value=0.1):
                with pytest.raises(PhaseTimeoutError) as exc_info:
                    await runtime._run_phase(slow_coro(), "TEST", "run1")
                assert exc_info.value.phase == "TEST"
                assert exc_info.value.timeout == 0.1
