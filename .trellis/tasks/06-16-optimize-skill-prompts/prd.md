# Optimize Skill Prompts for Tribunal and Omniscient

## Goal

利用刚加的 TribunalAgent skill_prompt 注入能力，为 tribunal 角色编写领域特定的评估指引，同时优化 omniscient 的 skill prompt，使 LLM 产出更丰富的 evidence 和更合理的 confidence cap。

## Requirements

* R1: 为 tribunal 的 omniscient/dynamics/star 角色编写默认 skill prompt 片段
* R2: 优化 omniscient 的 RIPPLE 阶段 skill prompt，引导产出更多反面信号
* R3: skill prompt 应包含：历史数据引用指引、反面证据权重说明、confidence cap 评估标准
* R4: skill prompt 可通过 YAML 配置覆盖

## Acceptance Criteria

* [ ] tribunal 角色有默认 skill prompt，包含评估指引
* [ ] 传入 skill_prompt 后，tribunal 评估 prompt 中包含领域指引内容
* [ ] skill prompt 可通过 YAML 配置文件自定义

## Out of Scope

* 不改 TribunalAgent 的硬编码 prompt 模板（路径 D 的范围）
* 不改 ConfidenceGate 逻辑

## Technical Notes

* TribunalAgent 已支持 skill_prompt 参数（PR #13）
* skill_prompts 通过 Skill 系统加载（loaded_skill.prompts）
* 当前 tribunal_prompt 从 skill 的 prompts.tribunal 字段读取
