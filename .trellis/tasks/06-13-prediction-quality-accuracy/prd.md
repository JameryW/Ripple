# 提升预测分析质量与准确度

## Goal

系统性提升 Ripple 在社交媒体传播预测与 PMF 验证场景下的预测分析质量、稳定性和可校准性。目标不是简单增加提示词约束，而是基于当前 `simulate -> SimulationRuntime -> Agent/Tribunal -> Provider -> Recorder/Reporting` 架构，建立一套从输入证据、过程推演、专家校准、历史基线、集成运行到离线回测的闭环质量体系。

本 PRD 只定义需求与实施边界，不进入实现阶段。

## What I Already Know

* 当前核心执行流位于 `ripple/api/simulate.py` 和 `ripple/engine/runtime.py`，主流程为 `INIT -> SEED -> RIPPLE -> DELIBERATE(可选) -> OBSERVE -> SYNTHESIZE`。
* `SimulationRecorder` 已经记录输入、INIT、SEED、wave、deliberation、observation、synthesis、ensemble run 等过程产物，为质量追踪提供了基础。
* `ProviderRegistry` 已支持 runtime overrides、YAML providers 和 stub fallback，已有 `historical`、`topology`、`embedding`、`ambient` 等 provider 类别。
* 历史数据当前会在运行前注入 `simulation_input["historical"]`，并在 SYNTHESIZE 后通过 `HistoricalValidator` 做后验偏差检查。
* 当前历史校验主要是报告与 insight，不会自动调整最终预测、置信度或触发保守重判。
* `DELIBERATE` 阶段已有 Tribunal evaluate/challenge/revise 流程，适合承载结构化反乐观审计。
* `ensemble_runs` 当前可聚合 PMF 类 ordinal scores、grade agreement、Fleiss kappa、median/range/IQR，但最终结果仍主要基于最后一次 run 叠加 ensemble_stats。
* Skill 层已经有大量领域现实锚点和反乐观规则，例如社交媒体冷启动、平台中位数、PMF 行业基线、channel/vertical 约束等。
* 当前 `SYNTHESIZE` 输出结构主要依赖 Skill prompt，代码层校验较弱。

## Problem Statement

当前系统已经具备多 Agent 推演、合议校准、历史数据注入和 ensemble 统计能力，但预测准确度仍主要依赖 LLM 对提示词的遵守。缺少以下关键闭环：

* 预测输出缺少严格、可校验、跨场景一致的结构化契约。
* EvidencePack 对证据的压缩偏粗，容易放大高能量响应，弱化沉默、忽略、负面和不确定信号。
* Provider 后验校验未转化为预测校准动作。
* Ensemble 统计未充分影响最终预测与置信度。
* 缺少离线回测数据集与误差指标，无法判断模型、prompt、Skill、provider 版本是否真的提升准确度。
* 反乐观规则主要存在于自然语言 prompt，缺少程序化 gate。

## Requirements

### R1. Structured Prediction Contract

系统应为不同 Skill 定义可校验的预测输出契约，至少覆盖：

* 预测目标和单位。
* 时间窗口。
* 预测值或等级。
* 置信度。
* 置信度原因。
* 关键证据引用。
* 关键假设。
* 真实数据待验证项。
* 不可验证或低置信度声明。

社交媒体预测应支持数值区间或分位数表达，例如 `p50/p80/p95`、互动率范围、破圈概率、长尾概率。

PMF 验证应支持等级分布、维度评分、合议分歧、待验证假设、保守结论依据。

### R2. EvidencePack Upgrade

现有 EvidencePack 应升级为更均衡的证据摘要，至少包含：

* 正向行为信号：如 amplify、create、adopt、recommend。
* 负向行为信号：如 reject、suppress、complaint、skepticism。
* 沉默或无行动信号：ignore、no-action、low-energy。
* Star/Sea 分层统计。
* response type 分布。
* energy 衰减曲线或逐 wave 变化摘要。
* 跨圈层传播深度。
* top positive signals 与 top negative signals。
* evidence id，供 Tribunal 与 SYNTHESIZE 引用。

高分、高置信度或乐观结论必须能回指 evidence id。

### R3. Provider-Based Calibration

Provider 不应只作为可选背景数据来源，还应参与预测质量判断：

* 有历史 provider 时，应使用历史基线校准绝对值预测。
* 有 topology provider 时，应校验 LLM 生成拓扑的规模、结构和类型分布。
* 缺少真实 provider 时，输出应自动标记为 relative simulation，并降低可声明的置信度上限。
* provider validation 超过阈值时，应产生结构化 calibration action，例如下调置信度、生成 calibrated prediction、或触发保守重判。

### R4. Historical Validation As Calibration

当前后验历史校验应扩展为校准器：

* 支持均值、中位数、P75、P90、P95 等历史基线。
* 支持按 platform、channel、vertical、account size、content type、product category 分桶。
* 对超过历史合理区间的预测输出校准建议。
* 对超阈值预测自动降低 confidence。
* 在 `provider_insights` 中暴露校准原因、偏差幅度和受影响指标。

### R5. Ensemble Distribution Output

Ensemble 结果应从“最后一次 run + 统计摘要”升级为“分布式预测”：

* 最终结果应优先使用多 run 的中位数、众数、分布区间或保守聚合结果。
* 输出 run 间分歧原因。
* kappa 或 stability level 低时自动降低 confidence。
* grade agreement 低时不得输出 high confidence。
* 对社交媒体数值预测也应支持多 run 分布，而不只支持 PMF ordinal score。

### R6. Tribunal Audit Formalization

Tribunal 输出应正式纳入可解析审计字段：

* `key_evidence`
* `uncertainties`
* `optimism_audit`
* `overrated_dimensions`
* `missing_evidence`
* `recommended_confidence_cap`

程序层应使用这些字段影响最终结论，而不是只把 narrative 当文本参考。

### R7. Offline Backtesting

建立离线回测机制，用历史案例评估预测准确度：

* 每个 case 包含 prediction-time input、真实 outcome、平台/渠道/行业标签、时间窗口。
* 运行时不能看到 outcome。
* 输出误差指标和分桶报告。
* 支持比较不同 model、prompt hash、Skill version、provider version、engine version。

最小回测指标：

* 数值预测：MAE、MAPE、RMSE。
* 概率预测：Brier score、calibration curve。
* 等级预测：confusion matrix、macro F1。
* 置信度校准：high/medium/low confidence 的真实准确率。

### R8. Prediction Quality Report

每次模拟完成后，应能生成结构化质量报告：

* 输入完整性评分。
* provider 覆盖情况。
* evidence 平衡度。
* ensemble 稳定性。
* tribunal 分歧程度。
* 历史偏差。
* 最终 confidence gate 结果。
* 主要残余风险。
* 推荐的真实世界验证动作。

## Non-Functional Requirements

* 向后兼容现有 `simulate()` API 的核心返回结构。
* 新字段应以增量方式加入，避免破坏现有 CLI、service、reporting 和 artifact consumers。
* 所有质量判断必须可追溯到 recorder 产物或 provider 数据。
* 无 provider 时系统仍可运行，但必须显式降低结果声明强度。
* 不引入大型外部 Agent 框架。
* 优先复用当前 Skill、Provider、Recorder、Tribunal、Ensemble 架构。

## Acceptance Criteria

* [ ] PRD 明确区分 prediction contract、evidence、calibration、ensemble、tribunal、backtesting、reporting 七个能力面。
* [ ] 每个能力面都有可测试的行为要求。
* [ ] PRD 明确说明不执行实现、不启动任务。
* [ ] 后续实现可拆成多个小任务，而不是一次性大改。
* [ ] 方案能复用当前架构中的 `ProviderRegistry`、`SimulationRuntime`、`DeliberationOrchestrator`、`SimulationRecorder` 和 `ensemble` 模块。
* [ ] Provider 缺失时 confidence 被强制 cap 到 medium（程序化 gate，非 prompt 建议）。
* [ ] EvidencePack 包含正向/负向/沉默信号、Star/Sea 分层、evidence id。
* [ ] Confidence gate 至少综合 4 个 factor：provider availability、ensemble stability、historical deviation、evidence balance。
* [ ] 数值预测支持 p50/p80/p95 分位数表达。
* [ ] Ensemble 单次运行时退化为单点，不报错。
* [ ] SSE 推送 quality 字段（confidence_gate_result、evidence_balance、provider_status）。
* [ ] Backtest case schema 带版本号。
* [ ] 向后兼容现有 simulate() API 返回结构。

## Resolved Decisions

* Phase 1 MVP 同时覆盖社交媒体传播预测和 PMF 验证。核心基础设施（EvidencePack、confidence gate、provider calibration、Tribunal audit）是跨场景共享的，R1 的 prediction contract 同时支持数值区间（social-media）和等级分布（PMF）。
* 第一批离线回测数据从已有示例 + 手工标注案例获取（乐观偏差、保守偏差、合理预测各几例），R7 的 backtest case schema 和基础框架可在 Phase 1 搭建。
* 当 provider 缺失时，强制 confidence 上限为 medium。这是程序化 confidence gate，在运行时层面保证无数据支撑时不能声明 high confidence。
* 对数值预测，首批全覆盖：曝光量（impressions/reach）、互动量（engagement）、转化量（conversion）、传播概率（virality_probability）、破圈概率（breakout_probability）、长尾概率（long_tail_probability）。PMF 场景同时支持维度评分和等级分布。
* 全量实现：核心行为 + SSE 推送 quality 字段 + 多因素 confidence gate + backtest case 版本化。

## Proposed Implementation Phases

### Phase 1: Contract, Evidence, and Confidence Gate

目标：建立预测输出结构化契约、证据均衡升级、程序化 confidence gate。

范围：

* 定义 Skill-level prediction contract（数值区间 + 等级分布）。
* 升级 EvidencePack schema（负向/沉默信号、Star/Sea 分层、evidence id、energy 衰减）。
* 给 Tribunal/SYNTHESIZE 引入 evidence id 引用。
* 实现多因素 confidence gate：
  * Factor 1: Provider 缺失 → 强制 medium 上限。
  * Factor 2: Ensemble stability（kappa/stability/agreement）→ 低稳定性降 confidence。
  * Factor 3: Historical deviation → 超阈值降 confidence。
  * Factor 4: Evidence balance → 正负信号失衡降 confidence。
  * Gate 逻辑：取所有 factor 的最低结果作为最终 confidence。
* Confidence 归一化：处理 LLM 输出的 “High”/”high”/0.8/80% 等不一致格式。
* SSE 推送 quality 字段（confidence_gate_result、evidence_balance、provider_status）。

不做：

* 不实现完整回测执行引擎。
* 不修改 provider 数据源。
* 不重写运行时主流程。

### Phase 2: Calibration, Ensemble Distribution, and Tribunal Audit

目标：让历史基线、多次运行和 Tribunal 审计真正影响最终预测。

范围：

* HistoricalValidator 升级为 calibrator：
  * 支持均值、中位数、P75、P90、P95 历史基线。
  * 支持按 platform/channel/vertical/account_size/content_type 分桶。
  * 输出 calibration action（下调置信度、生成 calibrated prediction、触发保守重判）。
* Provider insights 输出 calibration action。
* Ensemble 输出分布式预测：
  * 最终结果使用多 run 的中位数、众数、分布区间。
  * 输出 run 间分歧原因。
  * 数值预测也支持多 run 分布（不只是 PMF ordinal score）。
  * 单次运行时退化为单点输出，不报错。
* Tribunal audit formalization：
  * 新增 `key_evidence`、`uncertainties`、`optimism_audit`、`overrated_dimensions`、`missing_evidence`、`recommended_confidence_cap` 字段。
  * 程序层使用这些字段影响最终结论。

### Phase 3: Backtesting, Quality Report, and SSE Integration

目标：建立准确度闭环 + 质量报告 + 完整 SSE 集成。

范围：

* 定义 backtest case schema（带版本号）。
* 批量运行离线回测。
* 计算误差指标（MAE/MAPE/RMSE/Brier/confusion matrix/macro F1/confidence calibration）。
* 按 Skill/platform/channel/vertical/model/prompt version 分桶。
* 生成质量报告（输入完整性、provider 覆盖、evidence 平衡度、ensemble 稳定性、tribunal 分歧、历史偏差、confidence gate 结果、残余风险、推荐验证动作）。
* SSE 推送完整 quality report。

## Out Of Scope

* 本 PRD 不执行任何代码实现。
* 本 PRD 不运行 `task.py start`。
* 本 PRD 不要求立刻接入外部生产数据源。
* 本 PRD 不要求替换现有 LLM provider 或 Agent 架构。
* 本 PRD 不承诺用模拟结果替代真实市场验证。
* 本 PRD 不把准确度提升定义为单次输出更"详细"，而是定义为可校验、可回测、可校准。
* 不引入大型外部 Agent 框架。
* 不实现 UI dashboard（回测结果通过 CLI/JSON 输出）。

## Technical Notes

Relevant code areas:

* `ripple/api/simulate.py` — simulation entrypoint, Skill loading, provider registry setup, ensemble dispatch.
* `ripple/engine/runtime.py` — 5-phase runtime, evidence pack construction, provider validation, final result assembly.
* `ripple/engine/deliberation.py` — Tribunal orchestration.
* `ripple/agents/omniscient.py` — INIT/RIPPLE/OBSERVE/SYNTHESIZE prompt calls and output parsing.
* `ripple/agents/tribunal.py` — expert scoring, challenge, revise.
* `ripple/providers/historical_validator.py` — current historical deviation logic.
* `ripple/providers/topology_validator.py` — current topology validation logic.
* `ripple/api/ensemble.py` — current ordinal score aggregation and kappa utilities.
* `ripple/engine/recorder.py` — process/result artifact persistence.
* `skills/social-media/prompts/*` — social media prediction and anti-optimism guidance.
* `skills/pmf-validation/prompts/*` and `skills/pmf-validation/rubrics/*` — PMF grading and audit guidance.

Important constraints:

* Existing recorder JSON should remain backward compatible.
* Current service endpoints expose completed output JSON and compact logs; new quality fields should be available through those artifacts.
* Provider validation is currently non-fatal; calibration design should decide which findings remain advisory and which become confidence gates.
* `random_seed` is explicitly documented as a diversity/control hint, not deterministic reproducibility for LLM output.

## Definition Of Done For Future Implementation

* Tests added or updated for each modified module.
* Existing API/CLI behavior remains backward compatible.
* New prediction quality fields are documented in examples or request/response docs.
* Backtest metrics are reproducible from committed fixtures or documented sample datasets.
* Quality gates are covered by unit tests and at least one integration test.
* No implementation starts until this PRD is reviewed and the task is explicitly started.
