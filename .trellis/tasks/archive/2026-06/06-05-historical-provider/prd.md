# HistoricalProvider 真实实现

## Goal

为 HistoricalProvider Protocol 提供具体实现，使 Ripple 从真实数据源自动加载历史传播记录，预注入 simulation flow 用于 SYNTHESIZE 锚定预测，并在 SYNTHESIZE 后对 LLM 预测结果做后置校验。

## Requirements

* 实现 `FileHistoricalProvider`: 加载 JSON/CSV 历史记录文件
* 实现 `WikiPageviewProvider`: 调用 Wikipedia Pageview API 获取历史页面浏览量
* 实现 `RedditArchiveProvider`: 从 Pushshift/Reddit API 获取历史帖子数据
* 实现 `HistoricalValidator`: SYNTHESIZE 后置校验（比较 LLM 预测与 Provider 历史，记录偏差）
* 预注入: Provider 数据自动填入 `simulation_input["historical"]`，用户无需手动传入
* 在 `_PROVIDER_IMPLEMENTATIONS["historical"]` 注册所有实现
* 支持 YAML 配置和 `simulate(providers={"historical": ...})` 运行时注入
* 失败 fallback: Provider 异常 → logger.warning → LLM fallback path
* 缓存: 同一 provider 实例内相同参数返回缓存结果

## Acceptance Criteria

- [ ] `FileHistoricalProvider` 可加载 JSON 历史记录文件
- [ ] `FileHistoricalProvider` 可加载 CSV 历史记录文件
- [ ] `WikiPageviewProvider` 可调用 Wikipedia Pageview API
- [ ] `RedditArchiveProvider` 可调用 Reddit/Pushshift API
- [ ] 预注入: Provider 数据自动填入 simulation_input["historical"]
- [ ] 后置校验: HistoricalValidator 比较 LLM 预测与 Provider 历史
- [ ] 所有 Provider 通过 YAML 配置实例化
- [ ] 所有 Provider 通过 `simulate(providers={"historical": ...})` 运行时参数注入
- [ ] Provider 异常时 fallback 到 LLM 生成
- [ ] 单元测试覆盖所有 Provider 和边界情况

## Definition of Done

* Tests added/updated (unit/integration where appropriate)
* Lint / typecheck / CI green
* Code-spec updated (provider-architecture.md) with historical provider details
* Rollback considered: new providers are additive, no existing behavior changed

## Technical Approach

### FileHistoricalProvider

```python
class FileHistoricalProvider:
    def __init__(self, path: str | Path, format: str = "auto", ...): ...
    async def get_historical(self, *, skill_id=None, platform=None, event_type=None, limit=10) -> List[Dict] | None: ...
```

- JSON: list of historical record dicts
- CSV: each row = one record, column headers = field names
- Format auto-detection from extension
- Caching, asyncio.to_thread() wrapping

### WikiPageviewProvider

```python
class WikiPageviewProvider:
    def __init__(self, article: str, start: str, end: str, granularity: str = "daily", ...): ...
    async def get_historical(self, ...) -> List[Dict] | None: ...
```

- Calls `https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/`
- Returns pageview counts as historical metrics
- Uses httpx (already a dependency)

### RedditArchiveProvider

```python
class RedditArchiveProvider:
    def __init__(self, subreddit: str, ...): ...
    async def get_historical(self, ...) -> List[Dict] | None: ...
```

- Uses Pushshift API (free, no auth) or Reddit API (OAuth required)
- Returns post engagement metrics (score, num_comments, upvote_ratio)

### HistoricalValidator

```python
class HistoricalValidator:
    def validate(self, llm_prediction: Dict, provider_historical: List[Dict]) -> ValidationReport: ...
```

- Compares LLM SYNTHESIZE output with historical baselines
- Checks metric deviation (views, shares, engagement rate)
- Logs only, no modification of LLM output

### Pre-injection Integration

In `simulate()` or `runtime.py`, before SEED phase:
```python
if providers and providers.historical and providers.historical.is_available():
    hist = await providers.historical.get_historical(...)
    if hist and "historical" not in simulation_input:
        simulation_input["historical"] = hist
```

### Post-validation Integration

After SYNTHESIZE phase, similar to `_validate_topology()`:
```python
if providers and providers.historical and providers.historical.is_available():
    report = validator.validate(synthesize_result, provider_historical)
    report.log()
```

## Decision (ADR-lite)

**Context**: HistoricalProvider 如何与 simulation flow 集成
**Decision**: 预注入 + 后置校验 — Provider 数据自动填入 simulation_input["historical"]，SYNTHESIZE 后校验 LLM 预测偏差
**Consequences**: 最完整但实现量最大；预注入让现有 anchored template 自动生效；后置校验增加预测可信度

**Context**: Provider 实现范围
**Decision**: 全源实现 (File + Wiki + Reddit)
**Consequences**: 覆盖文件/API 两种数据获取模式；Wiki 无需认证；Reddit Pushshift 免费但可能不稳定

## Out of Scope

* Twitter/X API (Academic Research track discontinued)
* 实时流式数据源
* 数据库类 Provider (SQL, NoSQL)
* 历史数据清洗/标准化管道
* 自动修正模式 (预留扩展点但不实现)

## Technical Notes

* 文件: `ripple/providers/historical.py` (已有 Protocol), 新增 `ripple/providers/historical_loaders.py`, `ripple/providers/historical_validator.py`
* 参考: `ripple/providers/topology_loaders.py` (FileHistoricalProvider 模式)
* 参考: `ripple/providers/openai_embedding.py` (API Provider 模式 — httpx + retries)
* 现有消费点: `omniscient.py:745` (has_historical check), `reporting.py:159` (_historical_metric_summary)
* Wiki API: `https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/{project}/{access}/{agent}/{article}/{granularity}/{start}/{end}`
* Reddit Pushshift: `https://api.pushshift.io/reddit/search/submission/`

## Research References

* [`research/historical-data-sources.md`](research/historical-data-sources.md) — SNAP Higgs, MemeTracker, Wikipedia Pageview API, record schema
