# Error Handling

> How errors are handled in this project.

---

## Overview

Ripple follows a **non-fatal, logged, graceful fallback** pattern for all quality gates, calibration, and provider operations. No quality check may crash the simulation. Errors are logged and the system falls back to LLM-only behavior.

---

## Error Types

| Type | Module | Behavior |
|------|--------|----------|
| Provider exception | `providers/*.py` | `logger.warning()` + stub/LLM fallback |
| Calibration exception | `engine/runtime.py` | `logger.warning()` + skip calibration, preserve LLM output |
| Confidence gate exception | `engine/runtime.py` | `logger.warning()` + gate not applied, original confidence preserved |
| Tribunal audit parse failure | `engine/runtime.py` | Fields default to `None`, gate Factor 5 is neutral |
| Evidence pack build failure | `engine/runtime.py` | `logger.warning()` + `_evidence_pack_v2` stays `None`, gate Factor 4 is neutral |
| Backtest fixture load failure | `backtest/fixtures/loader.py` | `logger.warning()` + empty case list |

---

## Error Handling Patterns

### Pattern 1: Non-fatal quality gate

```python
try:
    gate_result = self._evaluate_confidence_gate(result, provider_insights)
except Exception as exc:
    logger.warning("Confidence gate failed: %s", exc)
    gate_result = None  # simulation continues with original confidence
```

### Pattern 2: Provider fallback

```python
try:
    data = await provider.get_historical(...)
    if data is not None:
        simulation_input["historical"] = data
except Exception as exc:
    logger.warning("HistoricalProvider failed: %s", exc)
    # simulation_input["historical"] stays empty — LLM fallback
```

### Pattern 3: Calibration with exception guard

```python
try:
    self._apply_calibrated_predictions(result)
except Exception as exc:
    logger.warning("Calibrated predictions failed: %s", exc)
    # result["prediction"] keeps original LLM values
```

---

## API Error Responses

Simulation errors return structured error in prediction:

```python
{
    "prediction": {"error": "Phase 'SYNTHESIZE' timed out after 120s"},
    "run_id": "abc123",
    "timed_out": True,
    "timeout_phase": "SYNTHESIZE"
}
```

---

## Common Mistakes

### Mistake: Quality gate crashes simulation

**Bad**: Gate raises exception that propagates to caller
**Good**: Gate exception caught, logged, simulation continues with original confidence

### Mistake: Recorder stores wrong variable

**Bad**: `self._recorder.record_process("evidence_pack", self._evidence_pack)` — stores V1 dict instead of V2 dataclass
**Good**: `self._recorder.record_process("evidence_pack", dataclasses.asdict(self._evidence_pack_v2))` — serializes V2 dataclass

### Mistake: CLI imports from tests/

**Bad**: `from tests.backtest.fixtures.loader import ...` — breaks in production (tests/ not installed)
**Good**: `from ripple.backtest.fixtures.loader import ...` — production path; test fixture re-exports for compat
