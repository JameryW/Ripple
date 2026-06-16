# ripple/backtest/calibration_feedback.py
"""Calibration feedback loop — BacktestReport -> HistoricalCalibrator -> ConfidenceGate.

回测结果自动提取偏差模式，调整 confidence 阈值和 provider 数据，
使后续预测质量持续改善。

闭环流程：
1. 回测完成后，从 BacktestReport 提取偏差模式（signed_mape）
2. 偏差模式写入 CalibrationDataStore
3. 模拟运行时，从 store 读取校准数据，调整 historical_threshold_pct
4. 调整后的阈值传入 ConfidenceGate.evaluate()

设计要点：
- 非致命：反馈失败不应中断回测或模拟流程
- 可配置：开关、反馈强度、冷却期
- 原子写入：temp + rename 模式保证数据一致性
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ripple.backtest.schema import BacktestReport

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 数据存储路径
# ---------------------------------------------------------------------------

_DEFAULT_CALIBRATION_DIR = Path.home() / ".ripple" / "data" / "calibration"


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CalibrationFeedbackConfig:
    """校准反馈闭环配置。

    Attributes:
        enabled: 是否启用闭环反馈
        feedback_strength: 反馈强度 (0.0-1.0)，越大则阈值调整越激进
        cooldown_period: 冷却期 — 两次调整之间至少需要的回测次数
        min_cases_for_adjustment: 触发调整所需的最小回测样本数
        bias_threshold_pct: 偏差阈值 (%) — signed_mape 超过此值才触发调整
    """
    enabled: bool = True
    feedback_strength: float = 0.5
    cooldown_period: int = 3
    min_cases_for_adjustment: int = 5
    bias_threshold_pct: float = 10.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.feedback_strength <= 1.0:
            raise ValueError(
                f"feedback_strength 必须在 [0.0, 1.0] 范围内，当前值: {self.feedback_strength}"
            )
        if self.cooldown_period < 0:
            raise ValueError(
                f"cooldown_period 不能为负数，当前值: {self.cooldown_period}"
            )
        if self.min_cases_for_adjustment < 0:
            raise ValueError(
                f"min_cases_for_adjustment 不能为负数，当前值: {self.min_cases_for_adjustment}"
            )
        if self.bias_threshold_pct < 0:
            raise ValueError(
                f"bias_threshold_pct 不能为负数，当前值: {self.bias_threshold_pct}"
            )


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BiasPattern:
    """从回测报告中提取的偏差模式。

    Attributes:
        bucket_key: 分桶键（如 "platform=xiaohongshu"），空字符串表示全局
        signed_mape: 对称有符号 MAPE（正值=系统性高估，负值=系统性低估）
        sample_size: 样本数量
        timestamp: 提取时间（ISO 8601）
        run_id: 来源回测的 run_id
    """
    bucket_key: str
    signed_mape: float
    sample_size: int
    timestamp: str
    run_id: str


@dataclass(frozen=True)
class CalibrationAdjustment:
    """一次校准调整记录。

    Attributes:
        bucket_key: 分桶键
        old_threshold_pct: 调整前的 historical_threshold_pct
        new_threshold_pct: 调整后的 historical_threshold_pct
        reason: 调整原因描述
        timestamp: 调整时间（ISO 8601）
        run_id: 触发调整的回测 run_id
        feedback_strength: 本次使用的反馈强度
    """
    bucket_key: str
    old_threshold_pct: float
    new_threshold_pct: float
    reason: str
    timestamp: str
    run_id: str
    feedback_strength: float


# ---------------------------------------------------------------------------
# CalibrationDataStore — 基于 JSON 文件的持久化存储
# ---------------------------------------------------------------------------

class CalibrationDataStore:
    """校准数据的持久化存储。

    使用 JSON 文件存储偏差模式和调整记录，支持按 bucket_key 查询。
    文件写入采用 temp + rename 原子模式。

    存储结构：
        {dir}/
          bias_patterns.json     — 偏差模式列表
          adjustments.json       — 调整记录列表
          thresholds.json        — 当前生效的阈值映射
    """

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        if data_dir is None:
            data_dir = _DEFAULT_CALIBRATION_DIR
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

    # ── 原子写入 ─────────────────────────────────────────────────────────

    def _atomic_write_json(self, filename: str, data: Any) -> None:
        """原子写入 JSON 文件（temp + rename）。"""
        target = self._data_dir / filename
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self._data_dir),
                prefix=f".{filename}.tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2, default=str)
                os.replace(tmp_path, str(target))
            except BaseException:
                # 清理临时文件
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as exc:
            logger.warning("原子写入 %s 失败: %s", filename, exc)
            raise

    # ── 读取 ─────────────────────────────────────────────────────────────

    def _read_json(self, filename: str) -> Any:
        """读取 JSON 文件，文件不存在时返回空列表。"""
        path = self._data_dir / filename
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("读取 %s 失败: %s", filename, exc)
            return []

    # ── BiasPattern 操作 ─────────────────────────────────────────────────

    def save_bias_pattern(self, pattern: BiasPattern) -> None:
        """保存一个偏差模式记录。"""
        patterns = self._read_json("bias_patterns.json")
        patterns.append(asdict(pattern))
        self._atomic_write_json("bias_patterns.json", patterns)

    def get_bias_patterns(
        self,
        bucket_key: Optional[str] = None,
    ) -> List[BiasPattern]:
        """获取偏差模式记录。

        Args:
            bucket_key: 分桶键，None 表示返回所有记录

        Returns:
            偏差模式列表，按 timestamp 倒序排列
        """
        raw_list = self._read_json("bias_patterns.json")
        patterns = []
        for raw in raw_list:
            try:
                p = BiasPattern(
                    bucket_key=raw.get("bucket_key", ""),
                    signed_mape=float(raw.get("signed_mape", 0)),
                    sample_size=int(raw.get("sample_size", 0)),
                    timestamp=raw.get("timestamp", ""),
                    run_id=raw.get("run_id", ""),
                )
                patterns.append(p)
            except (TypeError, ValueError) as exc:
                logger.warning("跳过无效偏差记录: %s", exc)
                continue

        if bucket_key is not None:
            patterns = [p for p in patterns if p.bucket_key == bucket_key]

        # 按 timestamp 倒序
        patterns.sort(key=lambda p: p.timestamp, reverse=True)
        return patterns

    # ── CalibrationAdjustment 操作 ───────────────────────────────────────

    def save_adjustment(self, adjustment: CalibrationAdjustment) -> None:
        """保存一个调整记录。"""
        adjustments = self._read_json("adjustments.json")
        adjustments.append(asdict(adjustment))
        self._atomic_write_json("adjustments.json", adjustments)

    def get_adjustments(
        self,
        bucket_key: Optional[str] = None,
    ) -> List[CalibrationAdjustment]:
        """获取调整记录。

        Args:
            bucket_key: 分桶键，None 表示返回所有记录

        Returns:
            调整记录列表，按 timestamp 倒序排列
        """
        raw_list = self._read_json("adjustments.json")
        adjustments = []
        for raw in raw_list:
            try:
                a = CalibrationAdjustment(
                    bucket_key=raw.get("bucket_key", ""),
                    old_threshold_pct=float(raw.get("old_threshold_pct", 50)),
                    new_threshold_pct=float(raw.get("new_threshold_pct", 50)),
                    reason=raw.get("reason", ""),
                    timestamp=raw.get("timestamp", ""),
                    run_id=raw.get("run_id", ""),
                    feedback_strength=float(raw.get("feedback_strength", 0.5)),
                )
                adjustments.append(a)
            except (TypeError, ValueError) as exc:
                logger.warning("跳过无效调整记录: %s", exc)
                continue

        if bucket_key is not None:
            adjustments = [a for a in adjustments if a.bucket_key == bucket_key]

        # 按 timestamp 倒序
        adjustments.sort(key=lambda a: a.timestamp, reverse=True)
        return adjustments

    # ── 阈值映射 ─────────────────────────────────────────────────────────

    def _read_thresholds(self) -> Dict[str, float]:
        """读取当前生效的阈值映射 {bucket_key: threshold_pct}。"""
        data = self._read_json("thresholds.json")
        if isinstance(data, dict):
            return {k: float(v) for k, v in data.items() if isinstance(v, (int, float))}
        return {}

    def _save_thresholds(self, thresholds: Dict[str, float]) -> None:
        """保存阈值映射。"""
        self._atomic_write_json("thresholds.json", thresholds)

    def set_effective_threshold(self, bucket_key: str, threshold_pct: float) -> None:
        """设置某 bucket 的生效阈值。"""
        thresholds = self._read_thresholds()
        thresholds[bucket_key] = threshold_pct
        self._save_thresholds(thresholds)

    def get_effective_threshold(
        self,
        bucket_key: str,
        default: Optional[float] = 50.0,
    ) -> Optional[float]:
        """获取某 bucket 的生效阈值，不存在时返回 default。"""
        thresholds = self._read_thresholds()
        if bucket_key in thresholds:
            return thresholds[bucket_key]
        return default

    def get_all_thresholds(self) -> Dict[str, float]:
        """获取所有生效阈值。"""
        return dict(self._read_thresholds())


# ---------------------------------------------------------------------------
# 偏差提取
# ---------------------------------------------------------------------------

def extract_bias_patterns(
    report: BacktestReport,
    config: CalibrationFeedbackConfig,
) -> List[BiasPattern]:
    """从 BacktestReport 提取偏差模式。

    提取全局偏差和每个分桶的偏差。只提取样本数 >= min_cases_for_adjustment
    且 |signed_mape| >= bias_threshold_pct 的偏差模式。

    Args:
        report: 回测报告
        config: 反馈配置

    Returns:
        偏差模式列表
    """
    now = datetime.now(timezone.utc).isoformat()
    patterns: List[BiasPattern] = []

    # 全局偏差
    if report.signed_mape is not None and report.completed_cases >= config.min_cases_for_adjustment:
        if abs(report.signed_mape) >= config.bias_threshold_pct:
            patterns.append(BiasPattern(
                bucket_key="",
                signed_mape=report.signed_mape,
                sample_size=report.completed_cases,
                timestamp=now,
                run_id=report.run_id,
            ))

    # 分桶偏差
    for bucket_key, bucket_data in report.buckets.items():
        if not isinstance(bucket_data, dict):
            continue
        bucket_signed_mape = bucket_data.get("signed_mape")
        bucket_count = bucket_data.get("count", 0)
        if bucket_signed_mape is None:
            continue
        if not isinstance(bucket_signed_mape, (int, float)):
            continue
        if int(bucket_count) < config.min_cases_for_adjustment:
            continue
        if abs(float(bucket_signed_mape)) < config.bias_threshold_pct:
            continue

        patterns.append(BiasPattern(
            bucket_key=bucket_key,
            signed_mape=float(bucket_signed_mape),
            sample_size=int(bucket_count),
            timestamp=now,
            run_id=report.run_id,
        ))

    return patterns


# ---------------------------------------------------------------------------
# 阈值调整计算
# ---------------------------------------------------------------------------

def compute_threshold_adjustment(
    patterns: List[BiasPattern],
    store: CalibrationDataStore,
    config: CalibrationFeedbackConfig,
) -> List[CalibrationAdjustment]:
    """根据偏差模式计算阈值调整。

    调整逻辑：
    - signed_mape > 0 (系统性高估) → 提高 threshold_pct（更严格的门控）
    - signed_mape < 0 (系统性低估) → 降低 threshold_pct（更宽松的门控）
    - 调整幅度 = |signed_mape| * feedback_strength，有上限
    - 冷却期：最近 cooldown_period 次回测内已调整过则跳过

    Args:
        patterns: 偏差模式列表
        store: 校准数据存储（用于查询冷却期）
        config: 反馈配置

    Returns:
        调整记录列表
    """
    if not patterns:
        return []

    adjustments: List[CalibrationAdjustment] = []
    now = datetime.now(timezone.utc).isoformat()

    # 按 bucket_key 分组，每组取最新一条
    bucket_latest: Dict[str, BiasPattern] = {}
    for p in patterns:
        # 同一 bucket 只保留 signed_mape 绝对值最大的
        if p.bucket_key not in bucket_latest or abs(p.signed_mape) > abs(bucket_latest[p.bucket_key].signed_mape):
            bucket_latest[p.bucket_key] = p

    for bucket_key, pattern in bucket_latest.items():
        # 当前生效阈值
        current_threshold = store.get_effective_threshold(bucket_key, default=50.0)
        if current_threshold is None:
            current_threshold = 50.0

        # 冷却期检查：查询该 bucket 的最近调整
        recent_adjustments = store.get_adjustments(bucket_key)
        if recent_adjustments:
            # 只看最近 cooldown_period 条记录，避免永久冻结
            recent_subset = recent_adjustments[:config.cooldown_period]
            # 如果最近 cooldown_period 次调整中最新一次的 run_id 与当前相同，
            # 说明当前回测已产生过调整 → 跳过（防止同一回测重复调整）
            latest_run_ids = {a.run_id for a in recent_subset}
            if pattern.run_id in latest_run_ids:
                logger.info(
                    "冷却期中，跳过 bucket=%s 的阈值调整（当前 run_id=%s 已有调整记录）",
                    bucket_key,
                    pattern.run_id,
                )
                continue

        # 计算调整量
        # signed_mape > 0 → 高估 → 提高 threshold（更严格）
        # signed_mape < 0 → 低估 → 降低 threshold（更宽松）
        raw_adjustment = pattern.signed_mape * config.feedback_strength
        # 限制单次调整幅度，避免过激
        max_single_adjustment = 15.0  # 一次最多调整 15%
        clamped_adjustment = max(-max_single_adjustment, min(max_single_adjustment, raw_adjustment))

        new_threshold = current_threshold + clamped_adjustment
        # 阈值范围 [10, 200]
        new_threshold = max(10.0, min(200.0, new_threshold))

        if new_threshold == current_threshold:
            continue

        # 确定原因
        if pattern.signed_mape > 0:
            direction = "高估"
        else:
            direction = "低估"

        reason = (
            f"回测 run_id={pattern.run_id} 检测到{direction}偏差 "
            f"(signed_mape={pattern.signed_mape:.1f}%, "
            f"sample_size={pattern.sample_size})，"
            f"阈值从 {current_threshold:.1f}% 调整至 {new_threshold:.1f}% "
            f"(feedback_strength={config.feedback_strength})"
        )

        adjustment = CalibrationAdjustment(
            bucket_key=bucket_key,
            old_threshold_pct=current_threshold,
            new_threshold_pct=new_threshold,
            reason=reason,
            timestamp=now,
            run_id=pattern.run_id,
            feedback_strength=config.feedback_strength,
        )
        adjustments.append(adjustment)

    return adjustments


# ---------------------------------------------------------------------------
# 主入口：应用反馈
# ---------------------------------------------------------------------------

def apply_feedback(
    report: BacktestReport,
    store: CalibrationDataStore,
    config: Optional[CalibrationFeedbackConfig] = None,
) -> List[CalibrationAdjustment]:
    """应用校准反馈闭环。

    流程：提取偏差 → 计算调整 → 持久化 → 日志记录

    此方法是非致命的 — 任何异常都会被捕获并记录，不会中断调用方。

    Args:
        report: 回测报告
        store: 校准数据存储
        config: 反馈配置，None 使用默认配置

    Returns:
        调整记录列表（可能为空）
    """
    if config is None:
        config = CalibrationFeedbackConfig()

    if not config.enabled:
        logger.info("校准反馈闭环已禁用，跳过")
        return []

    try:
        # Step 1: 提取偏差模式
        patterns = extract_bias_patterns(report, config)
        if not patterns:
            logger.info("回测 run_id=%s 未检测到显著偏差模式", report.run_id)
            return []

        logger.info(
            "回测 run_id=%s 检测到 %d 个偏差模式",
            report.run_id,
            len(patterns),
        )
        for p in patterns:
            logger.info(
                "  偏差模式: bucket=%s, signed_mape=%.1f%%, sample=%d",
                p.bucket_key,
                p.signed_mape,
                p.sample_size,
            )

        # Step 2: 计算阈值调整
        adjustments = compute_threshold_adjustment(patterns, store, config)
        if not adjustments:
            logger.info("回测 run_id=%s 未产生阈值调整", report.run_id)
            return []

        # Step 3: 持久化偏差模式和调整
        for p in patterns:
            store.save_bias_pattern(p)

        for adj in adjustments:
            store.save_adjustment(adj)
            # 更新生效阈值
            store.set_effective_threshold(adj.bucket_key, adj.new_threshold_pct)

            # Step 4: 日志记录
            logger.info(
                "校准调整: bucket=%s, 阈值 %.1f%% -> %.1f%%, 原因: %s",
                adj.bucket_key,
                adj.old_threshold_pct,
                adj.new_threshold_pct,
                adj.reason,
            )

        return adjustments

    except Exception as exc:
        logger.warning("校准反馈闭环异常（非致命）: %s", exc)
        return []


# ---------------------------------------------------------------------------
# 运行时集成：为 HistoricalCalibrator / ConfidenceGate 提供校准阈值
# ---------------------------------------------------------------------------

def get_calibrated_threshold(
    bucket_context: Optional[Dict[str, Any]] = None,
    default: float = 50.0,
    store: Optional[CalibrationDataStore] = None,
) -> float:
    """获取校准后的 historical_threshold_pct。

    供 runtime._calibrate_historical() 和 _evaluate_confidence_gate() 调用，
    将校准反馈数据注入模拟流程。

    Args:
        bucket_context: 分桶上下文（如 {"platform": "xiaohongshu"}）
        default: 默认阈值
        store: 校准数据存储，None 使用默认路径

    Returns:
        校准后的 historical_threshold_pct
    """
    if store is None:
        try:
            store = CalibrationDataStore()
        except Exception as exc:
            logger.debug("创建 CalibrationDataStore 失败，使用默认阈值: %s", exc)
            return default

    # 尝试匹配分桶键
    if bucket_context:
        bucket_fields = ["platform", "channel", "vertical"]
        parts = []
        for f in bucket_fields:
            v = bucket_context.get(f)
            if v:
                parts.append(f"{f}={v}")
        bucket_key = ",".join(parts) if parts else ""

        # 先尝试精确匹配
        if bucket_key:
            threshold = store.get_effective_threshold(bucket_key, default=None)
            if threshold is not None:
                logger.debug(
                    "使用校准阈值 bucket=%s: %.1f%%",
                    bucket_key,
                    threshold,
                )
                return threshold

        # 再尝试单字段匹配
        for f in bucket_fields:
            v = bucket_context.get(f)
            if v:
                single_key = f"{f}={v}"
                threshold = store.get_effective_threshold(single_key, default=None)
                if threshold is not None:
                    logger.debug(
                        "使用校准阈值（单字段匹配）bucket=%s: %.1f%%",
                        single_key,
                        threshold,
                    )
                    return threshold

    # 全局阈值
    global_threshold = store.get_effective_threshold("", default=None)
    if global_threshold is not None:
        logger.debug("使用全局校准阈值: %.1f%%", global_threshold)
        return global_threshold

    return default
