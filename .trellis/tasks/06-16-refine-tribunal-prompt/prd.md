# Refine Tribunal Evaluate Prompt Template

## Goal

优化 TribunalAgent 的硬编码 evaluate/revise prompt 模板，增加对历史对比的引导、反面证据权重说明、更结构化的 audit 输出指引，使 LLM 给出更合理的 confidence cap。

## Requirements

* R1: evaluate prompt 增加历史对比引导段（"如果有历史数据表明类似内容表现为 X，请参考"）
* R2: evaluate prompt 增加反面证据权重说明（"不要仅因缺少反面证据就推荐 LOW cap"）
* R3: revise prompt 增加收敛引导（"如果挑战未提供新证据，保持原有 cap"）
* R4: audit 输出指引更明确，减少格式错误率

## Acceptance Criteria

* [ ] evaluate prompt 包含历史对比引导
* [ ] evaluate prompt 包含反面证据权重说明
* [ ] revise prompt 包含收敛引导
* [ ] 现有测试通过

## Out of Scope

* 不改 ConfidenceGate 逻辑
* 不改 _aggregate_audit_from_agents 聚合策略

## Technical Notes

* TribunalAgent.evaluate() prompt 在 ripple/agents/tribunal.py:145-174
* TribunalAgent.revise() prompt 在 ripple/agents/tribunal.py:235-265
* skill_prompt 注入在 prompt 前部（PR #13），模板优化在 prompt 后部
