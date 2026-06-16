# Refine ConfidenceGate: Tribunal Cap Aggregation & Evidence Balance Thresholds

## Goal

优化 ConfidenceGate 的两个关键因子 + tribunal prompt 可配置性，使最终置信度更合理：tribunal_audit 从"取最低值"改为"多数投票"，evidence_balance 从二值判断改为梯度响应，tribunal prompt 支持 skill prompt 注入。

## Requirements

* R1: tribunal_audit 因子从"取最低 cap"改为"多数投票"聚合策略
  - 修改 `_aggregate_audit_from_agents()` 使用 majority vote
  - 平局时取较低值（保守倾向）
  - 示例：[LOW, MEDIUM, MEDIUM] → MEDIUM；[LOW, LOW, HIGH] → LOW；[LOW, MEDIUM, HIGH] → LOW（平局取低）
* R2: evidence_balance 从二值判断改为梯度响应
  - 正面信号主导：>95% → HIGH（通过），85%-95% → MEDIUM（警告），<85% → LOW（严重失衡）
  - 沉默信号主导：>85% → LOW，70%-85% → MEDIUM，<70% → HIGH
  - 无信号时仍返回 HIGH
* R3: TribunalAgent 支持 skill_prompt 注入
  - `TribunalAgent.__init__` 接受可选 `skill_prompt: str` 参数
  - 注入到 evaluate/revise 的 prompt 中（与 OmniscientAgent 的 skill_prompt 注入模式一致）
  - `DeliberationOrchestrator.__init__` 接受可选 `skill_prompt` 参数，传递给 TribunalAgent
  - 向后兼容：不传时行为不变
* R4: 现有测试同步更新，新增测试覆盖新逻辑

## Acceptance Criteria

* [ ] 3 个 tribunal 角色中 [LOW, MEDIUM, MEDIUM] → tribunal_audit 因子为 MEDIUM
* [ ] 3 个 tribunal 角色中 [LOW, LOW, HIGH] → tribunal_audit 因子为 LOW
* [ ] 3 个 tribunal 角色中 [LOW, MEDIUM, HIGH] → tribunal_audit 因子为 LOW（平局取低）
* [ ] evidence_balance: 正面 96%+反面 1 → HIGH（通过）
* [ ] evidence_balance: 正面 90%+反面 2 → MEDIUM（警告）
* [ ] evidence_balance: 正面 80%+反面 5 → LOW（严重失衡）
* [ ] evidence_balance: 沉默 90%+总数 10 → LOW
* [ ] evidence_balance: 沉默 75%+总数 10 → MEDIUM
* [ ] TribunalAgent 传入 skill_prompt 后，evaluate prompt 中包含 skill 内容
* [ ] 不传 skill_prompt 时行为不变（向后兼容）
* [ ] 所有现有测试更新并通过

## Definition of Done

* Tests added/updated (unit/integration where appropriate)
* Lint / typecheck / CI green
* 向后兼容：不传新参数时行为与改动前一致

## Decision (ADR-lite)

**Context**: ConfidenceGate 过于保守 — tribunal cap 取最低值导致单角色悲观绑架全局；evidence_balance 二值判断无法区分轻度 vs 严重失衡；tribunal prompt 硬编码无法从外部优化。
**Decision**: (1) tribunal cap 改为多数投票；(2) evidence_balance 改为梯度响应（3 级）；(3) tribunal prompt 支持 skill_prompt 注入。
**Consequences**: 置信度更合理地反映多数意见而非最悲观意见；evidence balance 提供更精细的信号；tribunal prompt 可配置为后续优化打开空间。平局时保守倾向（取低值）保留了安全底线。

## Out of Scope

* 不改 Gate 的 min_of 聚合逻辑（6 因子取最低的顶层策略不变）
* 不改 ensemble/historical/provider/topology 因子
* 不改 tribunal 的角色定义或数量
* 不改 _parse_tribunal_audit 的 4 层 fallback 逻辑

## Technical Notes

### 关键文件

* `ripple/primitives/prediction_quality.py` — ConfidenceGate, _check_evidence_balance, _check_tribunal_cap
* `ripple/engine/deliberation.py` — _aggregate_audit_from_agents (majority vote 改这里)
* `ripple/agents/tribunal.py` — TribunalAgent.__init__, evaluate (skill_prompt 注入)
* `ripple/engine/runtime.py` — _evaluate_confidence_gate, DeliberationOrchestrator 创建
* `tests/primitives/test_prediction_quality.py` — evidence_balance 测试
* `tests/engine/test_tribunal_audit.py` — tribunal audit 测试

### 改动清单

1. `deliberation.py:_aggregate_audit_from_agents()` — cap 聚合从 min 改为 majority vote
2. `prediction_quality.py:_check_evidence_balance()` — 二值改为梯度 3 级
3. `tribunal.py:TribunalAgent.__init__` — 新增 skill_prompt 参数
4. `tribunal.py:TribunalAgent.evaluate()` — prompt 中注入 skill_prompt
5. `tribunal.py:TribunalAgent.revise()` — prompt 中注入 skill_prompt（如适用）
6. `deliberation.py:DeliberationOrchestrator.__init__` — 新增 skill_prompt 参数，传递给 TribunalAgent
7. `runtime.py` — 创建 DeliberationOrchestrator 时传入 skill_prompts.get("tribunal", "")
8. 测试文件更新
