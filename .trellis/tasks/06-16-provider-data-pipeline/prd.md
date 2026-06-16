# Provider Data Pipeline for Prediction Quality

## Goal

配置 Historical/Topology Provider 数据管道 + 启用 ensemble_runs，使 ConfidenceGate 的 4 个因子（provider_availability、historical_deviation、topology_calibration、ensemble_stability）从"无数据/中性"状态变为有真实数据支撑的活跃评估。

## What I already know

* `simulate()` API 已支持 `ensemble_runs` 参数（默认=1）、`providers` 参数
* `llm_config.yaml` 的 `_providers` 段可声明 provider 配置
* `ProviderRegistry` 优先级：runtime overrides > YAML > stubs
* `FileHistoricalProvider` 支持 JSON/CSV，按 platform/event_type/limit 过滤
* `FileTopologyProvider` 支持 JSON/CSV/GraphML/GML
* Skill 的 `required_providers` 字段可声明所需 provider

## Requirements

* R1: 在 `llm_config.yaml` 的 `_providers` 段添加 historical 和 topology provider 配置示例
* R2: 创建示例历史数据文件 `data/providers/historical_sample.json`
* R3: 创建示例拓扑数据文件 `data/providers/topology_sample.json`
* R4: 在 `simulate()` API 层将 `ensemble_runs` 默认值从 1 改为 2
* R5: 编写 provider 配置指南文档 `docs/provider-setup.md`
* R6: 在 CLI `doctor` 命令中增加 provider 健康检查输出

## Acceptance Criteria

* [ ] 配置 historical provider 后，ConfidenceGate 的 provider_availability 因子为 HIGH
* [ ] 配置 historical provider + 历史数据后，historical_deviation 因子产出实际偏差值
* [ ] 配置 topology provider 后，topology_calibration 因子产出实际校准结果
* [ ] ensemble_runs=2 时，ensemble_stability 因子有 kappa/agreement_rate 数据
* [ ] 有文档说明如何配置 providers
* [ ] doctor 命令显示 provider 状态

## Definition of Done

* Tests added/updated
* Lint / typecheck / CI green
* Docs updated

## Out of Scope

* 不改 Provider 接口或实现代码
* 不改 ConfidenceGate 逻辑
* 不实现自动数据采集

## Technical Notes

### 关键文件
* `ripple/api/simulate.py` — ensemble_runs 默认值
* `ripple/providers/registry.py` — ProviderRegistry
* `ripple/providers/historical_loaders.py` — FileHistoricalProvider
* `ripple/providers/topology_loaders.py` — FileTopologyProvider
* `ripple/cli/app.py` — doctor 命令
