# DataSource Provider Architecture

> Executable contracts for the `ripple/providers/` module — external data source abstraction layer.

---

## 1. Scope / Trigger

- **Trigger**: Any change to `ripple/providers/`, `SimulationRuntime` providers param, `LoadedSkill.required_providers`, or the SEED embedding injection logic.
- **Cross-layer**: providers → api/simulate.py → engine/runtime.py → skills/manager.py

---

## 2. Signatures

### Protocol Hierarchy

```python
# ripple/providers/base.py
class DataSourceProvider(Protocol):
    name: str                          # read-only property
    def is_available(self) -> bool: ...
    async def health_check(self) -> bool: ...
```

### Concrete Protocols

| Protocol | Module | Key Method | Return Type |
|----------|--------|------------|-------------|
| `TopologyProvider` | `providers/topology.py` | `get_topology(*, skill_id, platform, constraints)` | `TopologyData \| None` |
| `HistoricalProvider` | `providers/historical.py` | `get_historical(*, skill_id, platform, event_type, limit)` | `List[Dict] \| None` |
| `EmbeddingProvider` | `providers/embedding.py` | `embed(text: str)` | `List[float] \| None` |
| `AmbientProvider` | `providers/ambient.py` | `get_ambient(*, skill_id, platform)` | `Dict \| None` |

### ProviderRegistry

```python
class ProviderRegistry:
    def __init__(
        self,
        yaml_path: str | Path | None = None,       # standalone providers.yaml
        yaml_providers_cfg: Dict | None = None,      # from llm_config.yaml _providers section
        runtime_overrides: Dict[str, DataSourceProvider] | None = None,
    ): ...
    def get(self, category: str) -> DataSourceProvider: ...
    # Convenience properties: .topology, .historical, .embedding, .ambient
    def merge(self, overrides: Dict) -> ProviderRegistry: ...
    async def health_check_all(self) -> Dict[str, bool]: ...
```

### simulate() Signature Extension

```python
async def simulate(
    ...,
    providers: Optional[Any] = None,  # ProviderRegistry or Dict[str, DataSourceProvider]
) -> Dict[str, Any]: ...
```

### LoadedSkill Extension

```python
@dataclass
class LoadedSkill:
    ...
    required_providers: List[str] = field(default_factory=list)  # e.g. ["embedding", "topology"]
```

---

## 3. Contracts

### Priority Resolution

| Priority | Source | Example |
|----------|--------|---------|
| 1 (highest) | `simulate(providers={...})` runtime param | `{"embedding": my_provider}` |
| 2 | `llm_config.yaml` `_providers` section | YAML-declared defaults |
| 3 (lowest) | Stub (returns None → LLM fallback) | `StubEmbeddingProvider()` |

### TopologyData Format

```json
{
  "nodes": [{"id": "agent_1", "type": "star", "...": "..."}],
  "edges": [{"source": "agent_1", "target": "agent_2", "weight": 0.8}]
}
```

### EmbeddingProvider Contract

- `embed("")` → `[]` (empty list, not None)
- `embed(text)` → `List[float]` on success, `None` on failure
- `embed_batch(texts)` → `List[List[float] | None]`, individual items may be None

### SKILL.md required_providers

```yaml
---
name: social-media
required_providers:
  - embedding
  - topology
---
```

Engine validates: if a required provider is unavailable (stub), logs warning but does not fail.

---

## 4. Validation & Error Matrix

| Condition | Behavior |
|-----------|----------|
| No providers passed | All stubs → LLM generates everything (backward compat) |
| Provider `is_available() == False` | Treated as stub, LLM fallback |
| Provider raises exception | `logger.warning()` + silent LLM fallback, simulation continues |
| Unknown provider category in YAML | `logger.warning()` + skip |
| Unknown impl name in YAML | `logger.warning()` + use stub |
| Missing `impl` key in YAML config | `logger.warning()` + skip |
| `required_providers` lists unavailable provider | `logger.warning()` + continue with stub |

---

## 5. Good / Base / Bad Cases

### Good: Inject embedding provider via simulate()

```python
from ripple.providers import OpenAIEmbeddingProvider, ProviderRegistry

emb = OpenAIEmbeddingProvider(url="https://api.openai.com/v1", api_key="sk-...")
result = await simulate(event, providers={"embedding": emb})
# seed_ripple.content_embedding is now populated
```

### Base: No providers (backward compat)

```python
result = await simulate(event)
# seed_ripple.content_embedding == []  (unchanged behavior)
```

### Bad: Provider fails at runtime

```python
# Provider throws httpx.ConnectError
# → logger.warning("EmbeddingProvider failed, leaving content_embedding empty: ...")
# → seed_ripple.content_embedding == []  (graceful fallback)
```

---

## 6. Tests Required

| Test | Assertion Point |
|------|-----------------|
| `test_default_stubs` | ProviderRegistry() returns stubs for all categories |
| `test_runtime_override` | Runtime param overrides YAML and stub |
| `test_yaml_providers_cfg` | YAML dict instantiates correct provider class |
| `test_yaml_providers_cfg_runtime_overrides_priority` | Runtime > YAML priority |
| `test_health_check_all_stubs` | All stubs report False |
| `test_embed_empty_text` | `embed("")` returns `[]` |
| `test_protocol_conformance` | `isinstance(stub, DataSourceProvider)` is True |
| `test_required_providers_default_empty` | LoadedSkill defaults to empty list |
| `test_required_providers_from_frontmatter` | SKILL.md `required_providers` parsed correctly |
| SEED injection integration | EmbeddingProvider available → content_embedding non-empty |
| SEED injection failure | Provider exception → content_embedding remains [] |

---

## 7. Wrong vs Correct

### Wrong: Provider failure crashes simulation

```python
vec = await emb_provider.embed(text)
seed_ripple.content_embedding = vec  # if vec is None, breaks downstream
```

### Correct: Failure fallback with logging

```python
try:
    vec = await emb_provider.embed(text)
    if vec is not None:
        seed_ripple.content_embedding = vec
except Exception as exc:
    logger.warning("EmbeddingProvider failed: %s", exc)
# content_embedding stays [] — LLM fallback path works
```

---

## Design Decisions

### Decision: Protocol over ABC

**Context**: Need structural subtyping for providers — users should be able to implement the interface without inheriting from a base class.
**Decision**: Use `typing.Protocol` with `@runtime_checkable` instead of ABC.
**Why**: Allows duck-typing; third-party providers don't need to import our base class.

### Decision: Shared embedding endpoint with ModelRouter

**Context**: EmbeddingProvider needs API connection config; ModelRouter already has it.
**Decision**: `OpenAIEmbeddingProvider` shares url/api_key from `ModelEndpointConfig` via `from_endpoint_config()`.
**Why**: Avoids duplicate config surface; one `llm_config.yaml` serves both LLM and embedding.

### Decision: None means "let LLM handle it"

**Context**: Provider methods can return data or None.
**Decision**: `None` return = "I have no data for this, let the engine fall back to LLM generation."
**Why**: Simple, explicit contract. No need for sentinel objects or exception-based control flow.

### Decision: Edge-list topology format

**Context**: TopologyProvider needs a standard interchange format.
**Decision**: `{"nodes": [...], "edges": [...]}` — aligned with LLM INIT output.
**Why**: Zero migration cost; edge-list is the de facto standard for graph data exchange.

---

## TopologyProvider Implementations

### Concrete Providers

| Provider | Module | Config `impl` | Source |
|----------|--------|---------------|--------|
| `FileTopologyProvider` | `providers/topology_loaders.py` | `"file"` | SNAP/JSON/GraphML/CSV/GML files |
| `SyntheticTopologyProvider` | `providers/topology_loaders.py` | `"synthetic"` | NetworkX generators (BA/WS/SBM/ER) |
| `StubTopologyProvider` | `providers/topology.py` | _(default)_ | Returns None → LLM fallback |

### FileTopologyProvider

```python
class FileTopologyProvider:
    def __init__(
        self,
        path: str | Path,
        format: str = "auto",        # "snap"|"json"|"graphml"|"csv"|"gml"|"auto"
        default_type: str = "sea",    # default node type when not in file
        node_type_map: Dict[str, str] | None = None,  # override per-node types
    ): ...
```

- `format="auto"`: infers from extension (`.txt`→snap, `.json`→json, `.graphml`→graphml, `.csv`/`.tsv`→csv, `.gml`→gml)
- Caches result on first load; repeated calls return same object
- SNAP node IDs (integers) mapped to `agent_N` strings
- JSON format: Ripple uses `"edges"` key; NetworkX uses `"links"` — `_load_graph` handles the rename
- All NetworkX I/O wrapped in `asyncio.to_thread()` for async compatibility

### SyntheticTopologyProvider

```python
class SyntheticTopologyProvider:
    def __init__(
        self,
        model: str = "ba",   # "ba"|"ws"|"sbm"|"er"
        n: int = 50,         # number of nodes
        seed: int | None = None,
        **model_kwargs,      # m=2 (BA), k=4/p=0.3 (WS), sizes/p (SBM), p=0.1 (ER)
    ): ...
```

- Node types assigned by degree: top-10% degree → `"star"`, rest → `"sea"`
- Directed graph: undirected models (BA/WS/ER) converted via `to_directed()`
- Caches generated topology; same seed produces same topology

### TopologyValidator (Post-hoc Validation)

```python
class TopologyValidator:
    def __init__(
        self,
        scale_threshold: float = 50.0,   # max acceptable node/edge count deviation %
        type_threshold: float = 30.0,     # max acceptable star/sea ratio deviation %
        auto_correct: bool = False,       # reserved — currently raises NotImplementedError
    ): ...
    def validate(self, llm_topology: TopologyData, provider_topology: TopologyData) -> ValidationReport: ...
```

**Integration point**: `SimulationRuntime._validate_topology()` called after INIT phase completes.

**Validation checks**:

| Check | What it compares | Acceptable threshold |
|-------|------------------|---------------------|
| Scale | Node/edge count deviation `(llm - provider) / provider * 100` | ±50% |
| Structure | Connectivity, isolated nodes, avg degree | LLM disconnected when provider connected → not acceptable |
| Type distribution | Star/sea node ratio deviation | ±30% |

**Behavior**: Validation results logged via `report.log()` — never modifies LLM output. Warnings logged for deviations exceeding thresholds.

### YAML Configuration

```yaml
_providers:
  topology:
    impl: file
    path: data/snap_facebook.txt
    format: snap
    default_type: sea
```

```yaml
_providers:
  topology:
    impl: synthetic
    model: ba
    n: 50
    m: 2
    seed: 42
```

### Lazy Import Mechanism

Topology providers use lazy imports because they require `networkx` (optional dependency):

```python
_PROVIDER_LAZY_IMPORTS = {
    "topology": {
        "file": ("ripple.providers.topology_loaders", "FileTopologyProvider"),
        "synthetic": ("ripple.providers.topology_loaders", "SyntheticTopologyProvider"),
    },
}
```

`_ensure_lazy_imports(category)` resolves lazy entries on first access. Graceful degradation: if `networkx` not installed, `is_available()` returns `False`.

### NetworkX Optional Dependency

- `pyproject.toml`: `[project.optional-dependencies] topology = ["networkx>=3.0"]`
- Runtime: `_HAS_NETWORKX` flag controls availability
- `FileTopologyProvider.is_available()` → `False` if networkx not installed or file not found
- `SyntheticTopologyProvider.is_available()` → `False` if networkx not installed
- `get_topology()` returns `None` with warning log when unavailable

### Tests Required

| Test | Assertion Point |
|------|-----------------|
| `test_load_snap` | FileTopologyProvider loads SNAP edge list |
| `test_load_json` | FileTopologyProvider loads JSON topology |
| `test_load_csv` | FileTopologyProvider loads CSV edge list |
| `test_load_graphml` | FileTopologyProvider loads GraphML |
| `test_load_gml` | FileTopologyProvider loads GML |
| `test_file_not_found` | Returns None with warning |
| `test_caching` | Second call returns same cached object |
| `test_node_type_map` | Override types applied correctly |
| `test_ba_model` | SyntheticTopologyProvider generates BA graph |
| `test_ws_model` | SyntheticTopologyProvider generates WS graph |
| `test_sbm_model` | SyntheticTopologyProvider generates SBM graph |
| `test_er_model` | SyntheticTopologyProvider generates ER graph |
| `test_node_types_assigned` | Top-10% degree nodes typed "star" |
| `test_invalid_model` | Raises ValueError for unknown model |
| `test_scale_check_identical` | Zero deviation for identical topologies |
| `test_type_dist_mismatch` | Detects star/sea ratio differences |
| `test_auto_correct_not_implemented` | Raises NotImplementedError |
| `test_yaml_config_file_provider` | Registry instantiates FileTopologyProvider from YAML |
| `test_yaml_config_synthetic_provider` | Registry instantiates SyntheticTopologyProvider from YAML |

### Design Decisions

#### Decision: Post-hoc validation (not replacement)

**Context**: TopologyProvider data could replace or validate LLM INIT output.
**Decision**: Post-hoc validation only — compare LLM output with provider data, log deviations.
**Why**: Preserves LLM flexibility (it can adapt topology to scenario context); validation adds trustworthiness without blocking. `auto_correct=False` reserved for future.

#### Decision: Full format support via NetworkX

**Context**: Need to load various graph file formats.
**Decision**: Support SNAP/JSON/GraphML/CSV/GML, all via NetworkX readers.
**Why**: NetworkX already handles all these formats natively; minimal implementation cost for maximum compatibility.

#### Decision: Lazy import for networkx-dependent providers

**Context**: NetworkX is an optional dependency; importing it unconditionally breaks installations without it.
**Decision**: `_PROVIDER_LAZY_IMPORTS` + `_ensure_lazy_imports()` pattern.
**Why**: Keeps `ripple.providers` importable without networkx; providers only loaded when actually requested via YAML or runtime params.

#### Decision: Degree-based node typing for synthetic graphs

**Context**: Synthetic graphs need star/sea node type assignments.
**Decision**: Top-10% degree nodes → "star", rest → "sea".
**Why**: Mirrors the real-world pattern where high-degree hubs are "stars"; simple, deterministic, requires no extra configuration.

---

## HistoricalProvider Implementations

### Concrete Providers

| Provider | Module | Config `impl` | Source |
|----------|--------|---------------|--------|
| `FileHistoricalProvider` | `providers/historical_loaders.py` | `"file"` | JSON/CSV files |
| `WikiPageviewProvider` | `providers/historical_loaders.py` | `"wikipedia"` | Wikimedia Pageview API |
| `RedditArchiveProvider` | `providers/historical_loaders.py` | `"reddit"` | Pushshift/Reddit API |
| `StubHistoricalProvider` | `providers/historical.py` | _(default)_ | Returns None → LLM fallback |

### FileHistoricalProvider

```python
class FileHistoricalProvider:
    def __init__(self, path: str | Path, format: str = "auto"): ...
```

- JSON: list of dicts, or `{"records": [...]}` wrapper
- CSV: DictReader-based, each row = one record
- Filtering by `platform`, `event_type`, `limit` applied after loading
- Caches records on first load

### WikiPageviewProvider

```python
class WikiPageviewProvider:
    def __init__(
        self,
        article: str,
        project: str = "en.wikipedia",
        start: str = "20250101",
        end: str = "20250601",
        granularity: str = "daily",
        access: str = "all-access",
        agent: str = "user",
    ): ...
```

- Calls `https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/{project}/{access}/{agent}/{article}/{granularity}/{start}/{end}`
- Returns records: `{"platform": "wikipedia", "event_type": "pageview", "timestamp": ..., "views": ..., ...}`
- Uses httpx (existing dependency), User-Agent: `Ripple/0.1`
- No authentication required

### RedditArchiveProvider

```python
class RedditArchiveProvider:
    def __init__(self, subreddit: str, size: int = 25, sort_type: str = "score"): ...
```

- Calls Pushshift API: `https://api.pushshift.io/reddit/search/submission`
- Returns records: `{"platform": "reddit", "event_type": "submission", "score": ..., "num_comments": ..., ...}`
- Size capped at 100 per Pushshift limits
- No authentication required (Pushshift is free)

### HistoricalValidator

```python
@dataclass
class MetricDeviation:
    metric: str
    predicted: float
    historical_avg: float
    historical_max: float
    deviation_pct: float  # (predicted - avg) / avg * 100
    threshold: float = 100.0  # max acceptable deviation %

    @property
    def is_acceptable(self) -> bool: ...  # uses self.threshold, NOT a parameter

class HistoricalValidator:
    def __init__(self, threshold: float = 100.0): ...
    def validate(self, prediction: Dict, historical: List[Dict]) -> HistoricalValidationReport: ...
```

- Extracts numeric fields from prediction, computes historical averages
- Compares each metric: `deviation_pct = (predicted - avg) / avg * 100`
- Skips non-metric fields: `step`, `tick`, `t`, `phase`, `agent_id`, `id`, `timestamp`
- `MetricDeviation.threshold` is a dataclass field (NOT a property parameter) — validator passes `self._threshold` when constructing each `MetricDeviation`
- Logs only, never modifies prediction
- Empty historical → warning in report

> **Gotcha**: `MetricDeviation.is_acceptable` is a `@property` — Python properties cannot accept extra parameters. If threshold were a property parameter, it would be silently ignored at runtime. Threshold must be stored as a dataclass field and passed during construction.

### Pre-injection Integration

`SimulationRuntime._inject_historical()` called before SEED phase:
- If `simulation_input["historical"]` already has data → skip (user-provided takes priority)
- If HistoricalProvider available and returns data → inject into `simulation_input["historical"]`
- Non-fatal on exception

### Post-validation Integration

`SimulationRuntime._validate_historical()` called after SYNTHESIZE phase:
- Validates `synthesize_result.get("prediction", {})` against `simulation_input["historical"]`
- **Must pass `prediction` dict, not full `synthesize_result`** — validator's `_extract_numeric_fields` only inspects top-level numeric fields; the full result has nested structure (`prediction`, `timeline`, etc.)
- Non-fatal on exception — logs warning and continues

### YAML Configuration

```yaml
_providers:
  historical:
    impl: file
    path: data/historical_events.json
    format: json
```

```yaml
_providers:
  historical:
    impl: wikipedia
    article: Python_(programming_language)
    start: "20250101"
    end: "20250601"
```

```yaml
_providers:
  historical:
    impl: reddit
    subreddit: technology
    size: 25
```

### Tests Required

| Test | Assertion Point |
|------|-----------------|
| `test_load_json` | FileHistoricalProvider loads JSON records |
| `test_load_csv` | FileHistoricalProvider loads CSV records |
| `test_load_json_with_records_key` | Handles `{"records": [...]}` wrapper |
| `test_auto_format` | Format auto-detection from file extension |
| `test_file_not_found` | Returns None, `is_available()` returns False |
| `test_filter_by_platform` | Platform filtering works |
| `test_filter_by_event_type` | Event type filtering works |
| `test_limit` | Result count limited correctly |
| `test_caching` | Second call uses cached records (`r1 == r2`, `_cache is not None`) |
| `test_wiki_pageview_success` | WikiPageviewProvider parses API response |
| `test_wiki_pageview_failure` | Returns None on network error |
| `test_wiki_caching` | Cache hit: `call_count == 1` |
| `test_reddit_archive_success` | RedditArchiveProvider parses API response |
| `test_reddit_archive_failure` | Returns None on network error |
| `test_reddit_size_capped` | Size capped at 100 |
| `test_validate_identical` | Zero deviation for identical data |
| `test_validate_deviation` | Detects metric deviation (>100% threshold → not acceptable) |
| `test_validate_no_historical` | Warning when no historical data |
| `test_validate_no_matching_metric` | Empty metric_deviations when no overlap |
| `test_validate_zero_historical_avg` | Acceptable when both predicted and avg are 0 |
| `test_validate_infinite_deviation` | `deviation_pct == inf` when predicted > 0 and avg == 0 |
| `test_validate_skip_non_numeric` | Skips step/tick/agent_id fields |
| `test_report_log` | `report.log()` doesn't raise |
| `test_custom_threshold` | Validator threshold actually used (200% threshold → acceptable) |
| `test_yaml_config_file_provider` | Registry instantiates from YAML |
| `test_yaml_file_provider_get_historical` | Registry provider returns data via `get_historical()` |
| `test_lazy_import_resolves` | All three impls ("file", "wikipedia", "reddit") resolve |

### Design Decisions

#### Decision: Pre-injection + post-validation

**Context**: HistoricalProvider data needs to flow into simulation AND be validated against.
**Decision**: Pre-inject into `simulation_input["historical"]` before SEED phase; post-validate after SYNTHESIZE.
**Why**: Pre-injection lets existing anchored template logic work unchanged; post-validation adds trustworthiness. User-provided data takes priority over provider data.

#### Decision: Three data sources for MVP

**Context**: Need both file-based and API-based historical data sources.
**Decision**: File (JSON/CSV) + Wikipedia Pageview API + Reddit Pushshift API.
**Why**: File covers offline/batch use cases; Wiki and Reddit APIs cover real-time use cases with no authentication required. Covers both types of integration patterns (file I/O vs API calls).

#### Decision: Shared filtering logic

**Context**: All providers need platform/event_type/limit filtering.
**Decision**: Shared `_filter_records()` helper function.
**Why**: DRY; consistent filtering behavior across all providers.

#### Decision: httpx mock pattern for API providers

**Context**: WikiPageviewProvider and RedditArchiveProvider use `httpx.AsyncClient`; tests need to mock HTTP calls.
**Decision**: Use `patch("...httpx.AsyncClient", return_value=httpx.AsyncClient(transport=httpx.MockTransport(handler)))`.
**Why**: `lambda **kw: httpx.AsyncClient(transport=..., **kw)` causes `transport` to be passed twice (once explicit, once via `**kw`), resulting in `TypeError: got multiple values for keyword argument 'transport'`. The `return_value=` pattern avoids this by creating the client once with the transport pre-injected.

#### Decision: Post-validate prediction dict, not full synthesize_result

**Context**: `_validate_historical()` receives the SYNTHESIZE phase result, but `HistoricalValidator._extract_numeric_fields` only looks at top-level numeric fields.
**Decision**: Pass `synthesize_result.get("prediction", {})` to validator.
**Why**: The full `synthesize_result` dict has nested structure (`prediction`, `timeline`, `wave_records`, etc.) — only the `prediction` sub-dict contains the metrics worth comparing against historical baselines.

#### Common Mistake: @property with extra parameters

**Symptom**: Custom threshold values silently ignored; validator always uses default 100%.
**Cause**: Declaring `@property def is_acceptable(self, threshold: float = 100.0)` — Python properties cannot accept extra parameters; the `threshold` param is silently discarded at runtime.
**Fix**: Make `threshold` a dataclass field on `MetricDeviation`, pass it during construction (`MetricDeviation(..., threshold=self._threshold)`).
**Prevention**: Any `@property` that needs configurable behavior must store the config in the instance (field/attribute), not as a method parameter.

---

## HistoricalCalibrator (R4)

> Extends HistoricalValidator from log-only to action-producing calibrator.

### Signatures

```python
class HistoricalCalibrator:
    def __init__(
        self,
        threshold: float = 100.0,       # deviation % triggers lower_confidence
        p95_hard_cap: float = 200.0,     # deviation % above P95 → confidence cap "low"
        bucket_fields: List[str] | None = None,  # default: ["platform", "channel", "vertical"]
    ): ...
    def calibrate(
        self,
        prediction: Dict[str, Any],
        historical: List[Dict[str, Any]],
        bucket_context: Dict[str, Any] | None = None,  # e.g. {"platform": "xiaohongshu"}
    ) -> CalibrationReport: ...
```

### CalibrationAction Types

| action_type | Trigger | Effect |
|-------------|---------|--------|
| `lower_confidence` | Deviation > threshold or > p95_hard_cap | `confidence_cap` set to "medium" or "low" |
| `calibrated_prediction` | Predicted > P95 | `calibrated_value` = P95 value |
| `median_adjustment` | Median < Predicted <= P95 | `calibrated_value` = median value |
| `flag_for_review` | Predicted > 2×P95 | Flagged for human review |

### CalibrationReport Contract

```json
{
  "calibrated_metrics": [{"metric": "impressions", "predicted": 5000, "baseline": {...}, "deviation_from_avg_pct": 354.5, "actions": [...]}],
  "actions": [{"action_type": "median_adjustment", "metric": "engagement", "reason": "Predicted 550.0 between median 450.0 and P95 650.0", "original_value": 550.0, "calibrated_value": 450.0}],
  "bucket_key": "platform=xiaohongshu,channel=generic",
  "warnings": []
}
```

### Integration Point

`SimulationRuntime._calibrate_historical()` called after `_validate_historical()` in `_finalize_synthesize`:
- Calibration actions injected into `provider_insights["historical"]["calibration"]`
- `CalibrationReport` stored on `self._calibration_report` for quality report consumption

### Key Rules

| Rule | Behavior |
|------|----------|
| No historical data | Returns `CalibrationReport(warnings=["No historical data"])` with no actions |
| Bucket has fewer records than total | Uses matching bucket records; logs bucket usage |
| `bucket_context` is None | Uses all records (bucket_key = "default") |
| Prediction has no numeric fields | Empty `calibrated_metrics`, no actions |
| Calibrator exception | Returns None (non-fatal), logged by caller |
| `median_adjustment` action | Produced when `median < predicted <= P95`; `calibrated_value` = median |
| `calibrated_prediction` action | Produced when `predicted > P95`; `calibrated_value` = P95 |

### _apply_calibrated_predictions Behavior

Processes both `calibrated_prediction` and `median_adjustment` actions:
- For `calibrated_prediction` with `PercentileBaseline`: if `predicted > P95`, upgrade cap to P75 (more conservative)
- For `median_adjustment`: applies median value directly (calibrator already set `calibrated_value`)
- Original values preserved in `result["prediction"]["raw_predictions"]`
- `calibration_method` = most severe action type present: `"historical_p95_cap"` > `"historical_p75_cap"` > `"historical_median_adjustment"`
- Non-fatal: exceptions caught and logged

---

## ConfidenceGate (R3/R4/R5/R6)

> Multi-factor confidence gate that caps prediction confidence based on quality signals.

### Signatures

```python
class ConfidenceGate:
    def evaluate(
        self,
        raw_confidence: Any,  # str, float, or ConfidenceLevel
        *,
        provider_available: bool = True,
        ensemble_kappa: float | None = None,
        ensemble_stability: str | None = None,       # "high"|"medium"|"low"
        ensemble_agreement_rate: float | None = None,
        historical_max_deviation_pct: float | None = None,
        historical_threshold_pct: float = 100.0,
        evidence_positive_count: int = 0,
        evidence_negative_count: int = 0,
        evidence_silent_count: int = 0,
        tribunal_confidence_cap: str | None = None,   # "medium"|"low"
        topology_scale_acceptable: bool | None = None,
        topology_type_acceptable: bool | None = None,
    ) -> ConfidenceGateResult: ...
```

### Factor Definitions (6 factors)

| # | Factor | Gate Level | Trigger |
|---|--------|-----------|---------|
| 1 | `provider_availability` | MEDIUM | No provider `is_available()` |
| 2 | `ensemble_stability` | LOW/MEDIUM | kappa < 0.4, stability="low", agreement < 0.5 |
| 3 | `historical_deviation` | MEDIUM/LOW | deviation > threshold (2× → LOW) |
| 4 | `evidence_balance` | MEDIUM | positive > 90% of non-silent with < 2 negative; silent > 70% |
| 5 | `tribunal_audit` | per cap | Tribunal `recommended_confidence_cap` |
| 6 | `topology_calibration` | MEDIUM/LOW | Scale or type distribution exceeds provider bounds (both → LOW) |

### Gate Logic

`final_confidence = min(original, factor1_level, factor2_level, ..., factor6_level)`

If any factor produces a level lower than original, `gate_applied = True` and `reason` lists all triggered factors.

### Integration in runtime.py

`_evaluate_confidence_gate()` extracts all factor inputs from:
- `self._providers` → provider_available
- `result["ensemble_stats"]` → kappa, stability, agreement_rate
- `provider_insights["historical"]["validation"]` → max_deviation_pct
- `self._evidence_pack_v2` → positive/negative/silent counts
- `self._extra_phase_outputs["DELIBERATE"]` → tribunal_confidence_cap
- `self._validation_reports["topology"]` → scale/type acceptable

**Stability direction**: `ensemble_stability` picks the WORST (lowest) stability level across dimensions using `max(levels, key=lambda x: {"high": 0, "medium": 1, "low": 2}.get(x, 1))`. When dimensions have mixed high/low stability, the result is "low" (not "high").

### Post-Ensemble Confidence Gate

When `ensemble_runs >= 2`, `_run_ensemble` in `simulate.py` runs a second confidence gate pass after merging:
- Uses ensemble-level `dimension_kappa`, `post_ensemble_stability` (derived from `numeric_distributions`), and `grade_agreement`
- Result stored in `merged["confidence_gate"]` AND `merged["quality"]["confidence_gate_result"]`
- If gate fires, prediction confidence is lowered to `gate_result.final_confidence.value`
- Non-fatal: exception caught, logged, gate skipped

### Gate Result Storage

Confidence gate result is stored in two locations (backward compat):

| Key | Location | Format |
|-----|----------|--------|
| `result["confidence_gate"]` | Top-level | Dict with `gate_applied`, `original_confidence` (str), `final_confidence` (str), `reason`, `factors` (list of dicts) |
| `result["quality"]["confidence_gate_result"]` | Quality sub-dict | Same dict |

**Serialization rule**: `ConfidenceLevel` enums and `ConfidenceFactor` dataclasses MUST be serialized to strings/dicts before storing in result dict. Using `.value` for enums and explicit dict construction for factors. JSON recorder and API responses serialize to JSON; raw enum/dataclass objects cause `TypeError`.

---

## PredictionContract Parser (R1)

> Parses LLM output dicts into structured prediction contracts.

### Key Function

```python
def parse_prediction_contract(
    prediction: Any,          # LLM output dict
    *,
    skill_id: str = "",
    evidence_pack_v2: Any = None,  # EvidencePackV2 for evidence_ids
) -> PredictionContract: ...
```

### Field Detection Rules

| Category | Detection | Unit |
|----------|-----------|------|
| Numeric metric | Key in `_NUMERIC_FIELDS` (impressions, engagement, etc.) | "count" |
| Probability metric | Key in `_PROBABILITY_FIELDS` or contains "probability"/"prob" | "probability" |
| Grade prediction | Key "grade" exists with string value | — |

### Validation Warnings

| Warning | Condition |
|---------|-----------|
| Quantile ordering | p50 > p80 or p80 > p95 |
| Grade distribution sum | sum ≠ 1.0 (tolerance ±0.15) |
| High confidence without evidence | confidence=HIGH and evidence_ids is empty |

### Key Rules

- Non-fatal: unknown dict structures return empty contract with warning
- High confidence predictions auto-receive evidence_ids from EvidencePackV2 (up to 3)
- Result written to `result["prediction_contract"]` — never overwrites `result["prediction"]`
- Quantile fields searched as `{field}_p50`, `{field}_p80`, `{field}_p95`

---

## Tribunal Audit Parsing (R6)

> Robust extraction of 6 audit fields from Tribunal/DELIBERATE output.

### 6 Required Audit Fields

| Field | Type | Purpose |
|-------|------|---------|
| `key_evidence` | `List[str]` | Evidence IDs supporting the prediction |
| `uncertainties` | `List[str]` | Key uncertainties in the prediction |
| `optimism_audit` | `str` | Narrative assessment of optimistic bias |
| `overrated_dimensions` | `List[str]` | Dimensions likely overrated |
| `missing_evidence` | `List[str]` | Evidence gaps that would strengthen prediction |
| `recommended_confidence_cap` | `str \| None` | Tribunal's recommended max confidence level |

### 4-Path Parsing Strategy

`_parse_tribunal_audit()` extracts audit fields through 4 paths of decreasing specificity:

| Priority | Path | Source |
|----------|------|--------|
| 1 | Structured audit | `deliberation_summary.audit.<field>` |
| 2 | Flat summary fields | `deliberation_summary.<field>` (no `audit` wrapper) |
| 3 | DeliberationRecord | `self._extra_phase_outputs["DELIBERATE"].deliberation_records[].<field>` |
| 4 | Text keyword fallback | Regex scan of narrative text for optimism/uncertainty keywords |

**Key rule**: Each path fills only fields not already found by a higher-priority path. If all paths fail, fields default to `None` (not empty lists).

### Tribunal Prompt Instructions

`TribunalAgent.evaluate` and `TribunalAgent.revise` prompts now include structured audit instructions requiring the LLM to output an `audit` section with the 6 fields. `_extract_audit_from_llm_data()` parses the audit from each agent's response and stores it in `_last_audit`.

### Audit Aggregation in DeliberationOrchestrator

`_aggregate_audit_from_agents()` collects audit fields from all tribunal agents:
- `key_evidence`, `uncertainties`, `overrated_dimensions`, `missing_evidence` → union of all agents' lists
- `optimism_audit` → concatenated narratives
- `recommended_confidence_cap` → **most conservative** (lowest) value across agents

### Output Storage

Parsed audit stored in `result["quality"]["tribunal_audit"]`:

```json
{
  "quality": {
    "tribunal_audit": {
      "key_evidence": ["ev_001", "ev_003"],
      "uncertainties": ["Platform algorithm changes"],
      "optimism_audit": "Prediction assumes viral sharing...",
      "overrated_dimensions": ["reach"],
      "missing_evidence": ["Historical conversion data"],
      "recommended_confidence_cap": "medium"
    }
  }
}
```

### Gotcha: `_safe_str_list` duplication

`_safe_str_list` is defined in both `tribunal.py` and `runtime.py` with identical logic. This is intentional (each module is independently importable) but must be kept in sync.

---

## SSE Quality Fields (R3/R5/R6)

> Quality fields pushed in SYNTHESIZE `phase_end` SSE events and stored in `result["quality"]`.

### SSE Event Detail Contract

SYNTHESIZE `phase_end` event `detail` dict always includes three quality fields:

```json
{
  "confidence_gate_result": {
    "original_confidence": "high",
    "final_confidence": "medium",
    "gate_applied": true,
    "reason": "provider_availability: No provider data; historical_deviation: Deviation 65.0% > threshold 50.0%"
  },
  "evidence_balance": {
    "positive_count": 5,
    "negative_count": 2,
    "silent_count": 1,
    "balanced": true
  },
  "provider_status": {
    "available": false,
    "categories": [],
    "detail": {}
  }
}
```

### Field Rules

| Field | Always Present? | When Missing |
|-------|----------------|--------------|
| `confidence_gate_result` | Yes | Defaults to `{"final_confidence": "medium", "gate_applied": false}` |
| `evidence_balance` | Yes | Zero counts + `balanced: true` when no evidence pack |
| `provider_status` | Yes | `available: false`, empty categories when no providers |

### API Consumer Access

Same data also available via `result["quality"]` dict after simulation completes:

```python
result["quality"]["evidence_balance"]  # same structure as SSE detail
result["quality"]["provider_status"]   # same structure as SSE detail
result["quality"]["confidence_gate_result"]  # full gate result
result["quality"]["tribunal_audit"]    # parsed audit fields
```

---

## Backtest Seed Fixtures & CLI (R7)

> Offline backtesting infrastructure with versioned cases and CLI runner.

### Fixture Location

Production fixture loader: `ripple/backtest/fixtures/loader.py`
Test re-export: `tests/backtest/fixtures/loader.py` (imports from production path)

### Seed Case Schema

8 synthetic-but-realistic cases in `ripple/backtest/fixtures/seed_cases.yaml`:

| Case | Bias Type | Platform | MAPE Range |
|------|-----------|----------|------------|
| 1-3 | Optimistic (3-4× overprediction) | Xiaohongshu, Weibo | ~235% |
| 4-5 | Conservative (0.3-0.4× underprediction) | Xiaohongshu | ~64% |
| 6-8 | Well-calibrated (0.85-1.2×) | Weibo, Xiaohongshu | ~11% |

### CLI Command

```bash
ripple-cli backtest run           # Human-readable summary table
ripple-cli backtest run --json    # JSON output
```

### Calibration Threshold Rationale

Backtest fixtures validated the 50% threshold:
- Well-calibrated predictions: ~11% deviation → gate NOT triggered
- Optimistic bias predictions: ~235% deviation → gate triggered
- Conservative bias predictions: ~64% deviation → gate triggered

The 50% default cleanly separates calibrated from biased predictions without false-positive gating on reasonable predictions.

### Numeric Metrics Contract

```python
class PredictionError:
    metric: str
    predicted: float
    actual: float
    absolute_error: float
    percentage_error: Optional[float] = None        # None when actual == 0
    signed_percentage_error: Optional[float] = None  # symmetric signed: (p-a)/((p+a)/2)*100; None when p+a == 0
```

`compute_numeric_metrics()` returns:

| Key | Formula | Direction |
|-----|---------|-----------|
| `mae` | `mean(abs_error)` | Always positive |
| `mape` | `mean(abs_error / abs(actual) * 100)` | Always positive |
| `signed_mape` | `mean((predicted - actual) / ((predicted + actual) / 2) * 100)` | Positive = over-predict, Negative = under-predict |
| `rmse` | `sqrt(mean(abs_error^2))` | Always positive |

`signed_mape` uses symmetric denominator to handle near-zero actuals better than standard MAPE. The sign distinguishes systematic over-prediction (positive) from under-prediction (negative).

`BacktestReport` schema includes `signed_mape: Optional[float] = None`. Runner populates it from `compute_numeric_metrics` output (both top-level and per-bucket breakdowns).

---

## Ensemble Merge & Post-Ensemble Gate (R1/R2)

> When `ensemble_runs >= 2`, `_run_ensemble` in `simulate.py` merges results using ensemble medians and re-runs the confidence gate.

### Merge Behavior

After all ensemble runs complete, `_run_ensemble`:
1. **Median replacement**: For each numeric field in `numeric_distributions`, replaces `merged["prediction"][field]` with the ensemble median
2. **Post-ensemble gate**: Runs `ConfidenceGate.evaluate()` with ensemble-level stats:
   - `ensemble_kappa` = Fleiss' kappa from `dimension_agreement_kappa`
   - `ensemble_stability` = worst stability level from `numeric_distributions[*].stability`
   - `ensemble_agreement_rate` = `grade_agreement`
3. **Gate result storage**: Stored in `merged["confidence_gate"]` and `merged["quality"]["confidence_gate_result"]`
4. **Confidence application**: If gate fires, `merged["prediction"]["confidence"]` lowered to `gate_result.final_confidence.value`

### Key Rules

| Rule | Behavior |
|------|----------|
| `ensemble_runs == 1` | No post-ensemble gate; median replacement is no-op (no numeric_distributions) |
| No numeric fields across runs | No median replacement; gate still runs with `post_ensemble_stability = None` |
| Gate exception | `logger.warning`, gate skipped, simulation continues |
| `_SKIP` fields in `_aggregate_numeric_predictions` | `step`, `tick`, `t`, `phase`, `agent_id`, `id`, `timestamp`, `confidence`, `confidence_gate_reason`, `verdict`, `calibration_method`, `raw_predictions` |

### Gotcha: `_run_ensemble` does NOT re-run calibration

The post-ensemble gate uses `provider_available=False` because `_run_ensemble` has no access to provider context. Full calibration still only runs per-run inside `SimulationRuntime._run_phases`.

---

## Provider Insights (Output Contract)

### Scope / Trigger

- **Trigger**: Any change to `_build_provider_insights`, `_serialize_validation`, `_serialize_scale_checks`, `_serialize_topology_check`, or the `provider_insights` output schema.
- **Cross-layer**: runtime.py → result dict → recorder JSON → API response

### Signatures

```python
class SimulationRuntime:
    def _build_provider_insights(self, simulation_input: Dict[str, Any]) -> Dict[str, Any]: ...
    def _serialize_validation(self, report: Any) -> Dict[str, Any]: ...
    @staticmethod
    def _serialize_scale_checks(scale: Any) -> List[Dict[str, Any]]: ...
    @staticmethod
    def _serialize_topology_check(label: str, check: Any) -> Dict[str, Any]: ...
```

### Contracts

#### Result dict: `provider_insights` (top-level key)

```json
{
  "provider_insights": {
    "topology": {
      "available": true,
      "validation": {
        "acceptable": false,
        "deviation_count": 3,
        "max_deviation_pct": 45.2,
        "exceeded": [
          {"metric": "node_count", "predicted": 200, "historical_avg": 110, "deviation_pct": 81.8}
        ]
      }
    },
    "historical": {
      "available": true,
      "records_injected": 15,
      "validation": {
        "acceptable": true,
        "deviation_count": 2,
        "max_deviation_pct": 35.4,
        "exceeded": []
      }
    }
  }
}
```

#### Key rules

| Rule | Behavior |
|------|----------|
| All providers are stubs | `provider_insights` = `{}` (empty dict, not omitted) |
| No providers configured | `provider_insights` key omitted entirely (backward compat) |
| `records_injected` = 0 | Field omitted from historical sub-dict |
| `exceeded` empty | Returns `[]` (not omitted) |
| Validation report unavailable | `validation` key omitted from provider sub-dict |
| `_serialize_validation` fails | `logger.warning` + `validation` key omitted |

#### Recorder: `process.providers`

When `provider_insights` is non-empty, recorder writes via `record_process("providers", insights)`, creating the `process.providers` key in the JSON output.

### Validation & Error Matrix

| Condition | Behavior |
|-----------|----------|
| Provider `is_available()` raises exception | `available = False` in insights, continues |
| `_serialize_validation()` raises exception | `logger.warning`, `validation` key omitted |
| `_build_provider_insights()` raises exception | `provider_insights` = `{}` (safe fallback) |
| Provider is a stub (StubXxxProvider) | Skipped — not included in insights |

### Good / Base / Bad Cases

#### Good: Historical provider with data and acceptable validation

```json
{"provider_insights": {"historical": {"available": true, "records_injected": 15, "validation": {"acceptable": true, "deviation_count": 2, "max_deviation_pct": 35.4, "exceeded": []}}}}
```

#### Base: No providers configured (backward compat)

```json
// provider_insights key omitted entirely — existing consumers unaffected
```

#### Bad: Validation exceeds threshold

```json
{"provider_insights": {"historical": {"available": true, "records_injected": 10, "validation": {"acceptable": false, "deviation_count": 3, "max_deviation_pct": 354.5, "exceeded": [{"metric": "views", "predicted": 5000, "historical_avg": 1100, "deviation_pct": 354.5}]}}}}
```

### Tests Required

| Test | Assertion Point |
|------|-----------------|
| `test_no_providers_returns_empty` | `_build_provider_insights` returns `{}` |
| `test_all_stubs_returns_empty` | Stub providers skipped, returns `{}` |
| `test_available_historical_provider` | Entry has `available: True` |
| `test_records_injected_zero_not_shown` | `records_injected` omitted when 0 |
| `test_validation_report_included` | `validation` sub-dict present |
| `test_exception_on_is_available_handled` | `available: False` on exception |
| `test_historical_report_acceptable` | `exceeded: []` when all within threshold |
| `test_historical_report_with_exceeded` | `exceeded` list contains over-threshold metrics only |
| `test_topology_report_with_exceeded_scale` | Scale deviations in `exceeded` |
| `test_topology_report_with_exceeded_scale_both` | Both node_count and edge_count in `exceeded` when both exceed threshold |
| `test_no_providers_omits_key` | Result dict has no `provider_insights` key |
| `test_all_stubs_produces_empty_dict` | `provider_insights = {}` |
| `test_result_dict_keys_preserved` | Existing keys unchanged |
| `test_no_providers_no_extra_keys` | No unexpected keys added |

### Wrong vs Correct

#### Wrong: Validation report includes all deviations regardless of threshold

```python
exceeded = [d for d in report.metric_deviations]  # includes acceptable ones too
```

#### Correct: Only exceeded deviations (over threshold)

```python
exceeded = [d for d in report.metric_deviations if not d.is_acceptable]
```

### Design Decisions

#### Decision: Top-level `provider_insights` key (not nested)

**Context**: Where should provider usage data appear in the result dict?
**Options**: (1) Top-level `provider_insights`, (2) `meta.providers`, (3) `process.providers`
**Decision**: Top-level `provider_insights` — easiest to discover and consume; same tier as `prediction`.
**Why**: Nested locations (`meta`, `process`) are less visible to API consumers. Top-level mirrors how other high-value outputs (prediction, timeline) are structured.

#### Decision: Summary + exceeded-only validation detail

**Context**: How much validation detail should be in the output?
**Options**: (1) Summary only, (2) Full report, (3) Summary + exceeded-only
**Decision**: Summary + exceeded-only — `acceptable/deviation_count/max_deviation_pct/exceeded[]`.
**Why**: Full reports bloat output when normal; exceeded-only provides actionable detail when something is wrong. `exceeded` is empty `[]` when all metrics are acceptable — no wasted space.

#### Decision: Empty dict `{}` for stub-only, omit key for no providers

**Context**: What should `provider_insights` be when providers are stubs or not configured?
**Decision**: All stubs → `{}` (explicit "checked, nothing active"). No providers configured → key omitted (backward compat — existing consumers don't see new key).
**Why**: Two different states need two different representations. `{}` means "providers were configured but none were active"; omitted means "no providers at all, same as before".
