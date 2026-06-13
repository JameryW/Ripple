# Integrate PredictionContract into SYNTHESIZE Pipeline

## Goal

将 R1 已定义的 `PredictionContract`、`NumericPrediction`、`GradePrediction` 数据类集成到 SYNTHESIZE 阶段，使 LLM 输出的 prediction 被解析、校验和丰富为结构化契约，而非仅作为原始 dict 透传。

## Problem

当前 `PredictionContract`/`NumericPrediction`/`GradePrediction` 定义在 `ripple/primitives/prediction_quality.py` 但未被 runtime 或 api 消费。SYNTHESIZE 阶段的 prediction 结果是 LLM 返回的原始 dict，缺少：

- 数值预测的 p50/p80/p95 分位数校验
- 等级预测的分布和维度评分校验
- 证据 ID 回指验证（高置信度声明必须能回指 evidence_id）
- 假设和不可验证声明的结构化提取

## Requirements

### R1a: Prediction Parser

在 `runtime.py` 的 `_finalize_synthesize` 中，将 LLM 输出的 prediction dict 解析为 `PredictionContract`：

1. 检测 prediction 类型（数值型 vs 等级型）
2. 对数值型预测：提取 p50/p80/p95 或 point estimate，校验范围合理性
3. 对等级型预测：提取 grade、distribution、dimension_scores
4. 从 EvidencePackV2 的 key_signals 中回填 evidence_ids
5. 提取 assumptions 和 unverifiable_claims

### R1b: Contract Validation

解析后的 `PredictionContract` 应进行基本校验：

1. 数值预测的 p50 ≤ p80 ≤ p95 逻辑
2. 等级分布之和 ≈ 1.0
3. 高置信度（high）时必须有 evidence_ids 引用
4. 空契约不应崩溃，而是 fallback 到原始 dict

### R1c: Contract Enrichment

将解析后的契约信息写回 result dict（增量，不覆盖原始字段）：

1. `result["prediction_contract"]` — 完整 PredictionContract 的 to_dict()
2. 数值预测字段增加 `_quantiles` 子字段（如果 LLM 输出了 p50/p80/p95）

## Non-Functional

- 向后兼容：不修改现有 prediction dict 的任何键
- 失败时静默降级：解析失败不影响主流程
- 不增加额外的 LLM 调用

## Acceptance Criteria

- [ ] SYNTHESIZE 阶段输出包含 `prediction_contract` 键
- [ ] 数值预测有 p50/p80/p95 或 point + confidence
- [ ] 等级预测有 grade + distribution + dimension_scores
- [ ] 高置信度声明有 evidence_ids 回指
- [ ] 解析失败时不影响主流程
- [ ] 现有 API 返回结构不变（新增字段，不修改）
