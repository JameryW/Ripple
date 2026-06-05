# Provider Insights: 将 Provider 使用情况写入产出结果

## Goal

让 simulation 产出结果（result dict + recorder JSON + API response）体现 DataSource Provider 的使用情况：哪些 Provider 被激活、注入了多少数据、后置校验结论如何。当前这些信息只存在于日志中，用户无法从输出中得知 Provider 是否生效。

## What I already know

* 当前 `_inject_historical()` 和 `_validate_historical()` 只写日志，不写回 result dict
* `_validate_topology()` 同样只写日志，不写回 result dict — 两个 validator 行为一致
* `result` dict 最终由 `simulate()` 返回，附加 `output_file`, `llm_budget`, `disclaimer` 等字段
* `SimulationRecorder` 有 `record_process(key, data)` 方法可写入 `process` 子键
* `recorder._data` 结构: `meta`, `simulation_input`, `process` (init/seed/waves/deliberation/observation/ensemble_runs), 顶层合成键
* `reporting.py` 的 compact log 只提取 `prediction/timeline/bifurcation_points/agent_insights/total_waves`
* `reporting.py` 的 `build_request_report_context()` 已处理 `historical` 数据统计摘要
* ProviderRegistry 有 `health_check_all()` 返回 `Dict[str, bool]`
* 四类 Provider: topology, historical, embedding, ambient

## Assumptions (temporary)

* 需要在 result dict 中添加 provider 相关字段（向后兼容，新增键不影响现有消费方）
* 需要在 recorder JSON 中记录 provider 使用情况
* topology validation 和 historical validation 应对齐处理方式

## Decision (ADR-lite)

**D1 — Provider insights 位置**: 放在 result dict 顶层 `provider_insights`，和 prediction/timeline 平级。最易发现和消费，向后兼容（新增键不影响现有消费方）。

**D2 — Validation report 细节级别**: 摘要 + 超限明细。正常时只返回 `acceptable/deviation_count/max_deviation_pct`，异常时额外返回 `exceeded` 列表（只含超过 threshold 的 metric 明细）。

## Open Questions

*(none remaining)*

## Requirements

* result dict 顶层新增 `provider_insights` 字段
* `provider_insights` 结构: `{"topology": {...}, "historical": {...}, "embedding": {...}, "ambient": {...}}` — 每个 provider type 一个子 dict
* 每个 provider type 子 dict 包含: `"available": bool`, `"records_injected": int` (适用时), `"validation": {...}` (适用时)
* validation 子结构: `{"acceptable": bool, "deviation_count": int, "max_deviation_pct": float, "exceeded": [...]}` — exceeded 只含超限 metric: `[{"metric": str, "predicted": float, "historical_avg": float, "deviation_pct": float}]`
* topology validation 对齐 historical validation — 同样写入 `provider_insights.topology.validation`
* recorder 通过 `record_process("providers", insights)` 记录
* Provider 全不可用时 `provider_insights` 为空 dict `{}`
* 向后兼容：新增字段，不修改/删除现有字段；无 provider 时消费方不受影响

## Acceptance Criteria

- [ ] result dict 包含顶层 `provider_insights` 字段
- [ ] `provider_insights` 按 provider type 分组（topology/historical/embedding/ambient）
- [ ] 每个 provider type 包含 `available` 状态
- [ ] historical provider 包含 `records_injected` 数量
- [ ] historical validation 包含 `acceptable/deviation_count/max_deviation_pct`
- [ ] historical validation 超限时包含 `exceeded` 列表
- [ ] topology validation 对齐写入 `provider_insights.topology.validation`
- [ ] recorder JSON 包含 `process.providers` 记录
- [ ] Provider 全不可用时 `provider_insights` 为 `{}`
- [ ] 无 provider 时输出与当前完全一致（向后兼容）
- [ ] 单元测试覆盖

## Definition of Done

* Tests added/updated
* Lint / typecheck / CI green
* 向后兼容验证（无 provider 时不影响现有输出）
* Rollback: 新增字段，无破坏性变更

## Out of Scope

* 修改 compact log 格式
* 修改 API response schema（FastAPI model）
* Provider 性能指标（延迟、调用次数）
* embedding/ambient provider 的具体 insights（仅标记 available 状态）
* reporting.py 中消费 provider insights
* 新增 Provider 类型（sentiment、market 等）

## Technical Notes

* 关键文件: `ripple/engine/runtime.py` (_inject_historical, _validate_historical, _validate_topology), `ripple/engine/recorder.py` (record_process), `ripple/api/simulate.py` (result enrichment)
* ProviderRegistry: `ripple/providers/registry.py`
* ValidationReport: `ripple/providers/historical_validator.py` (HistoricalValidationReport), `ripple/providers/topology_validator.py` (ValidationReport)
* 现有 result dict 顶层键: prediction, timeline, bifurcation_points, agent_insights, total_waves, run_id, wave_records_count, output_file, llm_budget, disclaimer

## Technical Approach

### 1. 构建 provider_insights dict

在 `SimulationRuntime` 中新增 `_build_provider_insights()` 方法，在 SYNTHESIZE 后调用：

```python
def _build_provider_insights(self) -> Dict[str, Any]:
    insights = {}
    providers = self._providers
    if not providers:
        return insights

    for cat in ("topology", "historical", "embedding", "ambient"):
        p = getattr(providers, cat, None)
        if p is None or isinstance(p, StubXxxProvider):
            continue
        entry = {"available": p.is_available()}
        # ... add records_injected, validation per type
        insights[cat] = entry
    return insights
```

### 2. 修改 _validate_historical / _validate_topology 返回 report

当前两个方法返回 None。改为返回 validation report（或 None），调用方将 report 存入 `self._validation_reports` dict。

### 3. 写入 result dict

在 SYNTHESIZE 后、recorder.record_synthesis 前：
```python
insights = self._build_provider_insights()
if insights:
    result["provider_insights"] = insights
```

### 4. 写入 recorder

```python
if insights:
    self._recorder.record_process("providers", insights)
```
