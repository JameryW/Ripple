# Calibration Feedback Loop from Backtest

## Goal

建立 BacktestReport → HistoricalCalibrator → ConfidenceGate 的闭环反馈：回测结果自动提取偏差模式，调整 confidence 阈值和 provider 数据，使后续预测质量持续改善。

## Requirements

* R1: BacktestReport 提取偏差模式（哪些维度系统性高估/低估）
* R2: 偏差模式写入 HistoricalCalibrator 的校准数据
* R3: 校准数据自动反馈到 ConfidenceGate 的 historical_threshold_pct
* R4: 闭环可配置：开关、反馈强度、冷却期（避免过拟合）
* R5: 记录每次校准调整的日志

## Acceptance Criteria

* [ ] 回测完成后，偏差模式被提取并写入校准数据
* [ ] 后续模拟使用更新后的校准数据
* [ ] 闭环可通过配置关闭
* [ ] 校准调整有日志记录

## Out of Scope

* 不改 BacktestReport 数据结构（已有 quality dimensions，PR #12）
* 不实现跨 skill 的校准共享

## Technical Notes

* BacktestReport 已有 quality_dimensions（PR #12）
* HistoricalCalibrator 在 ripple/providers/historical_calibrator.py
* ConfidenceGate 的 historical_threshold_pct 默认 50%
* 需要设计校准数据的存储格式和更新策略
