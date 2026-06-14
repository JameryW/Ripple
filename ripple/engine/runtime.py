"""Ripple 引擎运行时。 / Ripple engine runtime.

职责 / Responsibilities:
1. 编排（Orchestration）—— 按 5-Phase 调用全视者和星海 Agent / Orchestrate 5-Phase calls to Omniscient & Star/Sea agents
2. 状态管理（State Management）—— 维护 Field、记录轨迹 / Maintain Field state & trace records
3. 安全防护（Safety Guards）—— 死循环检测、输出校验 / Deadloop detection & output validation

不负责：能量计算、激活判定、衰减公式、CAS 参数管理。
/ Not responsible for: energy calc, activation logic, decay formulas, CAS param management.
"""

import asyncio
import inspect
import json
import logging
import math
import os
import re
import time
import uuid
from typing import Any, Callable, Awaitable, Dict, List, Optional, TYPE_CHECKING, Union

from ripple.primitives.events import SimulationEvent
from ripple.primitives.models import (
    AgentActivation,
    Ripple,
    OmniscientVerdict,
    WaveRecord,
)
from ripple.agents.omniscient import OmniscientAgent
from ripple.agents.star import StarAgent
from ripple.agents.sea import SeaAgent

if TYPE_CHECKING:
    from ripple.engine.recorder import SimulationRecorder

logger = logging.getLogger(__name__)

# 类型别名：支持同步和异步回调 / Type alias: supports sync and async callbacks
ProgressCallback = Union[
    Callable[[SimulationEvent], Awaitable[None]],
    Callable[[SimulationEvent], None],
]

SAFETY_WAVE_MULTIPLIER = 3  # 安全上限 = estimated_total_waves * 此系数 / Safety cap = estimated_total_waves * this multiplier

# ---------------------------------------------------------------------------
# Per-phase timeout defaults (seconds). / Per-phase timeout defaults (seconds).
# Can be overridden via env var RIPPLE_PHASE_TIMEOUTS_ENABLED=false to disable.
# Env vars per phase: RIPPLE_PHASE_TIMEOUT_INIT, etc.
# ---------------------------------------------------------------------------
_PHASE_TIMEOUTS_DEFAULTS: Dict[str, float] = {
    "INIT": 60,
    "SEED": 30,
    "RIPPLE": 1200,  # 20min — wave loop can be long
    "DELIBERATE": 600,  # 10min — tribunal rounds
    "OBSERVE": 120,
    "SYNTHESIZE": 180,
}

_PHASE_TIMEOUTS_ENABLED = os.environ.get(
    "RIPPLE_PHASE_TIMEOUTS_ENABLED", "true"
).lower() in ("true", "1", "yes")


def _resolve_phase_timeout(phase_name: str) -> float:
    """Resolve per-phase timeout from env var or defaults."""
    env_key = f"RIPPLE_PHASE_TIMEOUT_{phase_name}"
    env_val = os.environ.get(env_key)
    if env_val is not None:
        try:
            return float(env_val)
        except ValueError:
            logger.warning("Invalid env var %s=%s, using default", env_key, env_val)
    return _PHASE_TIMEOUTS_DEFAULTS.get(phase_name, 300)


# Overall job timeout (seconds). / Overall job timeout (seconds).
# Env var: RIPPLE_JOB_TIMEOUT (default 1800 = 30min)
JOB_TIMEOUT = int(os.environ.get("RIPPLE_JOB_TIMEOUT", "1800"))


class PhaseTimeoutError(Exception):
    """Raised when a simulation phase exceeds its configured timeout."""

    def __init__(self, phase: str, timeout: float):
        self.phase = phase
        self.timeout = timeout
        super().__init__(f"Phase '{phase}' exceeded timeout of {timeout}s")


def _extract_float(value: Any, default: float = 0.0) -> float:
    """从 LLM 输出中提取浮点数，容忍嵌套字典或异常类型。 / Extract float from LLM output; tolerates nested dicts or unusual types."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    if isinstance(value, dict):
        # LLM 有时会返回 {"value": 0.8, "reason": "..."} 之类的嵌套结构 / LLM sometimes returns nested structures like {"value": 0.8, ...}
        for key in ("value", "score", "energy", "initial_energy"):
            if key in value and isinstance(value[key], (int, float)):
                return float(value[key])
    return default


def _extract_int(value: Any, default: int = 0) -> int:
    """从 LLM 输出中提取整数，容忍嵌套字典或异常类型。 / Extract int from LLM output; tolerates nested dicts or unusual types."""
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    if isinstance(value, dict):
        for key in ("value", "count", "total", "estimated_total_waves"):
            if key in value and isinstance(value[key], (int, float)):
                return int(value[key])
    return default


def _parse_hours(s: str) -> float:
    """解析时间字符串为小时数。 / Parse a time string like "4h", "48h", "2.5h", "1d" into hours.

    无法解析时返回 0.0。 / Returns 0.0 if the string cannot be parsed.
    """
    if not s or not isinstance(s, str):
        return 0.0
    s = s.strip().lower()
    # 匹配 "4h", "2.5h", "48h" 格式 / Match patterns like "4h", "2.5h", "48h"
    m = re.match(r"^(\d+(?:\.\d+)?)\s*h$", s)
    if m:
        return float(m.group(1))
    # 匹配 "1d", "2d" 格式 / Match patterns like "1d", "2d"
    m = re.match(r"^(\d+(?:\.\d+)?)\s*d$", s)
    if m:
        return float(m.group(1)) * 24.0
    return 0.0


def _empty_agent_stats() -> Dict[str, Any]:
    """返回未被激活 Agent 的默认状态。 / Return default stats for an unactivated agent."""
    return {
        "activation_count": 0,
        "last_wave": None,
        "last_energy": 0.0,
        "last_response": None,
        "total_outgoing_energy": 0.0,
    }


def _safe_str_list(value: Any) -> List[str]:
    """Coerce an audit field value to a list of strings.

    Handles: list of strings, single string, None, or other types.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None and str(item).strip()]
    return [str(value)]


def _normalize_cap(value: Any) -> Optional[str]:
    """Normalize a confidence cap value to 'low'|'medium'|'high' or None."""
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in ("low", "medium", "high"):
        return s
    return None


class SimulationRuntime:
    """Ripple 模拟运行时编排器。 / Ripple simulation runtime orchestrator."""

    _DEFAULT_PHASES = ["INIT", "SEED", "RIPPLE", "OBSERVE", "SYNTHESIZE"]
    _PHASE_PROCESS_KEY_OVERRIDES = {
        # Design/plan canonical key: "#/process/deliberation"
        "DELIBERATE": "deliberation",
    }

    # 默认阶段权重（向后兼容参考）/ Default phase weights (backward compat reference)
    _PHASE_WEIGHTS = {
        "INIT": 0.05,
        "SEED": 0.05,
        "RIPPLE": 0.70,  # 占大头，内部按 wave 细分 / Largest share, subdivided by wave internally
        "OBSERVE": 0.10,
        "SYNTHESIZE": 0.10,
    }

    def __init__(
        self,
        omniscient_caller: Callable[..., Awaitable[str]],
        star_caller: Optional[Callable[..., Awaitable[str]]] = None,
        sea_caller: Optional[Callable[..., Awaitable[str]]] = None,
        skill_profile: str = "",
        on_progress: Optional[ProgressCallback] = None,
        # 增量记录器：模拟过程中动态写入 JSON 文件 / Incremental recorder: writes JSON dynamically during simulation
        recorder: Optional["SimulationRecorder"] = None,
        # 向后兼容：旧签名 agent_caller 同时用于 star 和 sea / Backward compat: legacy agent_caller used for both star and sea
        agent_caller: Optional[Callable[..., Awaitable[str]]] = None,
        # v4: Skill prompts injected into agent system_prompt (trusted zone)
        skill_prompts: Optional[Dict[str, str]] = None,
        # v2 Phase registration: Skills can register extra phases
        extra_phases: Optional[dict] = None,
        # v5: DataSource Providers
        providers: Optional[Any] = None,
    ):
        # v4: Build Omniscient system_prompt with skill context injection
        from ripple.prompts import SKILL_CONTEXT_SEPARATOR, SKILL_CONTEXT_END

        omniscient_system = ""
        if skill_prompts and skill_prompts.get("omniscient"):
            omniscient_system = (
                SKILL_CONTEXT_SEPARATOR
                + skill_prompts["omniscient"]
                + SKILL_CONTEXT_END
            )
        self._omniscient = OmniscientAgent(
            llm_caller=omniscient_caller,
            system_prompt=omniscient_system,
        )
        self._skill_prompts = skill_prompts or {}
        # 兼容旧 API：如果传了 agent_caller，star/sea 未传则用它 / Compat: use agent_caller for star/sea if not provided
        if agent_caller is not None:
            self._star_caller = star_caller or agent_caller
            self._sea_caller = sea_caller or agent_caller
        elif star_caller is not None:
            self._star_caller = star_caller
            self._sea_caller = sea_caller if sea_caller is not None else star_caller
        else:
            raise TypeError(
                "SimulationRuntime 需要 star_caller/sea_caller 或 agent_caller"
            )
        self._skill_profile = skill_profile
        self._on_progress = on_progress
        self._recorder = recorder
        self._providers = providers  # ProviderRegistry or None
        self._stars: Dict[str, StarAgent] = {}
        self._seas: Dict[str, SeaAgent] = {}
        self._wave_records: List[WaveRecord] = []
        self._seed_content: str = ""
        self._seed_energy: float = 0.0
        self._validation_reports: Dict[str, Any] = {}
        self._historical_records_injected: int = 0
        self._evidence_pack_v2: Any = None  # R2: upgraded evidence pack dataclass
        self._run_id: Optional[str] = None  # stored for evidence pack ids
        self._calibration_report: Any = None  # R4: calibration report with actions

        # 构建阶段序列（支持 Skill 注册额外阶段）/ Build phase sequence (supports Skill extra phases)
        self._phases = self._build_phase_sequence(extra_phases)

        # 从阶段序列派生权重和偏移量 / Derive weights and offsets from phase sequence
        self._phase_weights = {name: p["weight"] for name, p in self._phases.items()}
        self._phase_offsets: Dict[str, float] = {}
        offset = 0.0
        for name in self._phases:
            self._phase_offsets[name] = offset
            offset += self._phase_weights[name]
        # Extra phase outputs captured during run (lightweight summaries preferred)
        self._extra_phase_outputs: Dict[str, Any] = {}
        self._evidence_pack: Optional[Dict[str, Any]] = None

    @staticmethod
    def _short_text(value: Any, limit: int = 80) -> str:
        """压缩长文本，便于终端展示。 / Compact long text for terminal display."""
        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 1)] + "…"

    @classmethod
    def _short_agent_label_from_description(
        cls, description: str, fallback: str
    ) -> str:
        """从画像描述中提取短标签。 / Extract a short human label from an agent description."""
        text = str(description or "").strip()
        if not text:
            return fallback
        label = re.split(r"[，。；;：:\n|/（）()]", text, maxsplit=1)[0].strip()
        return cls._short_text(label or text, limit=18) or fallback

    def _agent_label(self, agent_id: str) -> str:
        """返回适合终端展示的 Agent 短名。 / Return a short terminal-friendly label for an agent."""
        agent = self._stars.get(agent_id) or self._seas.get(agent_id)
        if agent is None:
            return agent_id
        return self._short_agent_label_from_description(
            getattr(agent, "description", ""),
            agent_id,
        )

    def _agent_labels(self, agents: Dict[str, Any]) -> List[str]:
        """提取 Agent 短名列表。 / Collect short labels for agent groups."""
        return [
            self._short_agent_label_from_description(
                getattr(agent, "description", ""),
                agent_id,
            )
            for agent_id, agent in agents.items()
        ]

    @classmethod
    def _process_key_for_phase(cls, phase_name: str) -> str:
        """Map phase name to recorder process key."""
        return cls._PHASE_PROCESS_KEY_OVERRIDES.get(phase_name, phase_name.lower())

    def _json_pointer_for_process_key(self, key: str) -> str:
        """Build a JSON Pointer for a recorder process key.

        - Single run output:     #/process/{key}
        - Ensemble run output:   #/process/ensemble_runs/{i}/process/{key}
        """
        idx = None
        if self._recorder is not None:
            idx = getattr(self._recorder, "active_ensemble_run_index", None)
        if isinstance(idx, int):
            return f"#/process/ensemble_runs/{idx}/process/{key}"
        return f"#/process/{key}"

    def _phases_between(self, after_phase: str, before_phase: str) -> List[str]:
        """Return ordered phase names between two phases (exclusive)."""
        phase_names = list(self._phases.keys())
        try:
            start = phase_names.index(after_phase) + 1
            end = phase_names.index(before_phase)
        except ValueError:
            return []
        if start >= end:
            return []
        return phase_names[start:end]

    async def _run_extra_phases_between(
        self,
        *,
        after_phase: str,
        before_phase: str,
        context: Dict[str, Any],
        run_id: str,
        estimated_waves: int,
    ) -> None:
        """Execute registered extra phases between two default phases.

        Context contract (best-effort, handlers must be tolerant to missing keys):
        - Always present:
            - run_id: str
            - simulation_input: Dict[str, Any]
            - skill_profile: str
        - May be present depending on boundary:
            - init_result, estimated_waves, max_waves (after INIT)
            - seed_ripple (after SEED)
            - effective_waves, propagation_history, field_snapshot, evidence_pack (after RIPPLE)
            - observation (after OBSERVE)
            - phase_outputs: Dict[str, Dict[str, Any]] (accumulates extra phase outputs)

        Handler contract:
        - handler(context) -> Dict[str, Any] | Any (non-dict results are wrapped)
        - exceptions are surfaced (simulation fails) and emitted as SimulationEvent(type="error")
        """
        for phase_name in self._phases_between(after_phase, before_phase):
            if phase_name in self._DEFAULT_PHASES:
                continue
            handler = self._phases.get(phase_name, {}).get("handler")
            if handler is None:
                continue

            await self._emit(
                SimulationEvent(
                    type="phase_start",
                    phase=phase_name,
                    run_id=run_id,
                    progress=self._progress(phase_name, 0.0),
                    total_waves=estimated_waves,
                )
            )

            async def emit_progress(
                event_type: str,
                *,
                phase_fraction: float = 0.0,
                detail: Optional[Dict[str, Any]] = None,
            ) -> None:
                await self._emit(
                    SimulationEvent(
                        type=event_type,
                        phase=phase_name,
                        run_id=run_id,
                        progress=self._progress(phase_name, phase_fraction),
                        total_waves=estimated_waves,
                        detail=detail or {},
                    )
                )

            context["emit_progress"] = emit_progress
            try:
                result = handler(context)
                if inspect.isawaitable(result):
                    result = await result
            except Exception as exc:
                logger.error(
                    "[%s] Extra phase '%s' failed: %s",
                    run_id,
                    phase_name,
                    exc,
                )
                await self._emit(
                    SimulationEvent(
                        type="error",
                        phase=phase_name,
                        run_id=run_id,
                        progress=self._progress(phase_name, 0.0),
                        total_waves=estimated_waves,
                        detail={"error": str(exc)},
                    )
                )
                raise

            if not isinstance(result, dict):
                result = {"result": result}

            self._extra_phase_outputs[phase_name] = result
            context.setdefault("phase_outputs", {})[phase_name] = result

            # Persist to recorder under process.<key> so JSON Pointers remain stable
            if self._recorder:
                key = self._process_key_for_phase(phase_name)
                self._recorder.record_process(key, result)

            phase_end_detail: Optional[Dict[str, Any]] = None
            if phase_name == "DELIBERATE" and isinstance(result, dict):
                summary = result.get("deliberation_summary") or {}
                if isinstance(summary, dict):
                    phase_end_detail = {
                        "rounds": summary.get("rounds_executed"),
                        "converged": summary.get("converged"),
                        "consensus_points": list(summary.get("consensus_points") or []),
                        "dissent_points": list(summary.get("dissent_points") or []),
                        "final_positions": [
                            {
                                "member_role": item.get("member_role"),
                                "scores": item.get("scores"),
                            }
                            for item in list(summary.get("final_positions") or [])[:3]
                            if isinstance(item, dict)
                        ],
                    }

            await self._emit(
                SimulationEvent(
                    type="phase_end",
                    phase=phase_name,
                    run_id=run_id,
                    progress=self._progress(phase_name, 1.0),
                    total_waves=estimated_waves,
                    detail=phase_end_detail,
                )
            )
            context.pop("emit_progress", None)

    async def _emit(self, event: SimulationEvent) -> None:
        """触发进度回调（支持同步和异步回调）。 / Emit progress callback (sync and async)."""
        if self._on_progress is None:
            return
        result = self._on_progress(event)
        if inspect.isawaitable(result):
            await result

    def _progress(self, phase: str, phase_fraction: float = 0.0) -> float:
        """计算总进度值 (0.0 ~ 1.0)。 / Compute total progress (0.0 ~ 1.0).

        phase_fraction: 当前阶段内部的完成比例 / Completion ratio within current phase (0.0 ~ 1.0).
        """
        base = self._phase_offsets.get(phase, 0.0)
        weight = self._phase_weights.get(phase, 0.0)
        return min(1.0, base + weight * phase_fraction)

    def _build_phase_sequence(self, extra_phases: Optional[dict] = None) -> dict:
        """Build ordered phase sequence with optional extra phases inserted.

        Default phases use predefined weights. Extra phases are inserted at
        their declared position ('after' key), and all weights are rebalanced.
        """
        from collections import OrderedDict

        phases = OrderedDict(
            (name, {"weight": self._PHASE_WEIGHTS[name], "handler": None})
            for name in self._DEFAULT_PHASES
        )

        if not extra_phases:
            return phases

        # Insert extra phases at declared positions
        ordered_keys = list(phases.keys())
        for phase_name, config in extra_phases.items():
            after = config.get("after", "RIPPLE")
            if after in ordered_keys:
                insert_idx = ordered_keys.index(after) + 1
                ordered_keys.insert(insert_idx, phase_name)
            else:
                ordered_keys.append(phase_name)
            phases[phase_name] = {
                "weight": config.get("weight", 0.10),
                "handler": config.get("handler"),
            }

        # Rebalance weights to sum to 1.0
        total = sum(phases[k]["weight"] for k in ordered_keys)
        if total > 0:
            for k in ordered_keys:
                phases[k]["weight"] /= total

        return OrderedDict((k, phases[k]) for k in ordered_keys)

    async def run(
        self,
        simulation_input: Dict[str, Any],
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """执行完整模拟。 / Execute full simulation.

        Args:
            simulation_input: 模拟输入参数。 / Simulation input parameters.
            run_id: 可选的外部指定 run_id。若不传则自动生成。 / Optional external run_id; auto-generated if omitted.
        """
        run_id = run_id or str(uuid.uuid4())[:8]
        self._run_id = run_id
        logger.info(f"[{run_id}] 开始模拟")

        # Job-level timeout: wrap entire simulation in asyncio.wait_for
        if _PHASE_TIMEOUTS_ENABLED and JOB_TIMEOUT > 0:
            try:
                return await asyncio.wait_for(
                    self._run_inner(simulation_input, run_id),
                    timeout=JOB_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.error(
                    f"[{run_id}] Job exceeded overall timeout of {JOB_TIMEOUT}s"
                )
                return {
                    "prediction": {"error": f"Job timed out after {JOB_TIMEOUT}s"},
                    "timeline": [],
                    "bifurcation_points": [],
                    "agent_insights": {},
                    "run_id": run_id,
                    "timed_out": True,
                    "timeout_phase": "JOB",
                }
        else:
            return await self._run_inner(simulation_input, run_id)

    async def _run_phase(self, coro, phase_name: str, run_id: str):
        """Execute a phase coroutine with per-phase timeout if enabled."""
        if not _PHASE_TIMEOUTS_ENABLED:
            return await coro
        timeout = _resolve_phase_timeout(phase_name)
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except asyncio.TimeoutError:
            logger.error(
                f"[{run_id}] Phase {phase_name} exceeded timeout of {timeout}s"
            )
            raise PhaseTimeoutError(phase_name, timeout)

    async def _run_inner(
        self,
        simulation_input: Dict[str, Any],
        run_id: str,
    ) -> Dict[str, Any]:
        """Inner simulation logic, called by run() with optional job timeout."""
        # HistoricalProvider 预注入 / Pre-inject historical data from provider
        await self._inject_historical(simulation_input, run_id)

        phase_context: Dict[str, Any] = {
            "run_id": run_id,
            "simulation_input": simulation_input,
            "skill_profile": self._skill_profile,
        }

        try:
            return await self._run_phases(simulation_input, run_id, phase_context)
        except PhaseTimeoutError as e:
            logger.error(f"[{run_id}] Phase {e.phase} timed out after {e.timeout}s")
            # Return partial results with timeout marker
            effective_waves = len(self._wave_records)
            return {
                "prediction": {
                    "error": f"Phase '{e.phase}' timed out after {e.timeout}s"
                },
                "timeline": [],
                "bifurcation_points": [],
                "agent_insights": {},
                "run_id": run_id,
                "total_waves": effective_waves,
                "timed_out": True,
                "timeout_phase": e.phase,
            }

    async def _run_phases(
        self,
        simulation_input: Dict[str, Any],
        run_id: str,
        phase_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Execute all simulation phases sequentially."""

        # Phase 0: INIT
        await self._emit(
            SimulationEvent(
                type="phase_start",
                phase="INIT",
                run_id=run_id,
                progress=self._progress("INIT", 0.0),
            )
        )
        init_result = await self._run_phase(
            self._omniscient.init(
                skill_profile=self._skill_profile,
                simulation_input=simulation_input,
            ),
            phase_name="INIT",
            run_id=run_id,
        )
        self._create_agents(init_result)
        # 存储拓扑以供快照使用 / Store topology for snapshot use
        self._topology = init_result.get("topology")

        # TopologyProvider 后置验证 / Post-hoc topology validation
        await self._validate_topology(init_result, simulation_input, run_id)

        dp = init_result.get("dynamic_parameters", {})
        wave_time_window = dp.get("wave_time_window", "")
        wave_time_window_reasoning = str(dp.get("wave_time_window_reasoning", "") or "")
        platform_characteristics = str(dp.get("platform_characteristics", "") or "")
        if isinstance(wave_time_window, (int, float)):
            wave_time_window = f"{wave_time_window}h"
        horizon_str = simulation_input.get("simulation_horizon", "")

        # 确定性 wave 计算 / Deterministic wave calculation
        horizon_hours = _parse_hours(horizon_str)
        window_hours = _parse_hours(wave_time_window)

        if horizon_hours > 0 and window_hours > 0:
            estimated_waves = math.ceil(horizon_hours / window_hours)
            logger.info(
                f"[{run_id}] 确定性 wave 计算: "
                f"ceil({horizon_hours}h / {window_hours}h) = {estimated_waves}"
            )
        else:
            estimated_waves = _extract_int(
                dp.get("estimated_total_waves", 10),
                10,
            )
            logger.info(
                f"[{run_id}] 回退到 LLM 估计: estimated_total_waves = {estimated_waves}"
            )

        safety_max_waves = estimated_waves * SAFETY_WAVE_MULTIPLIER
        max_waves = safety_max_waves
        requested_max_waves = simulation_input.get("max_waves")
        if isinstance(requested_max_waves, (int, float)):
            requested_max_waves = int(requested_max_waves)
            if requested_max_waves > 0:
                max_waves = min(safety_max_waves, requested_max_waves)

        # 存储以供快照和裁决调用使用 / Store for use in snapshot and verdict calls
        self._wave_time_window = wave_time_window
        self._simulation_horizon = horizon_str
        self._energy_decay_per_wave = _extract_float(
            dp.get("energy_decay_per_wave", 0.15), 0.15
        )
        self._wave_time_window_reasoning = wave_time_window_reasoning
        self._platform_characteristics = platform_characteristics

        logger.info(
            f"[{run_id}] INIT 完成: "
            f"Star×{len(init_result.get('star_configs', []))}, "
            f"Sea×{len(init_result.get('sea_configs', []))}, "
            f"预估 {estimated_waves} waves (安全上限 {safety_max_waves}, "
            f"执行上限 {max_waves})"
        )

        # 增量记录：INIT 阶段结果 / Incremental record: INIT phase result
        if self._recorder:
            self._recorder.record_init(
                init_result,
                estimated_waves,
                max_waves,
                safety_max_waves=safety_max_waves,
                requested_max_waves=requested_max_waves,
            )

        await self._emit(
            SimulationEvent(
                type="phase_end",
                phase="INIT",
                run_id=run_id,
                progress=self._progress("INIT", 1.0),
                total_waves=estimated_waves,
                detail={
                    "star_count": len(init_result.get("star_configs", [])),
                    "sea_count": len(init_result.get("sea_configs", [])),
                    "estimated_waves": estimated_waves,
                    "safety_max_waves": safety_max_waves,
                    "requested_max_waves": requested_max_waves,
                    "max_waves": max_waves,
                    "wave_time_window": wave_time_window,
                    "wave_time_window_reasoning": wave_time_window_reasoning,
                    "platform_characteristics": platform_characteristics,
                    "star_labels": self._agent_labels(self._stars),
                    "sea_labels": self._agent_labels(self._seas),
                },
            )
        )
        phase_context["init_result"] = init_result
        phase_context["estimated_waves"] = estimated_waves
        phase_context["max_waves"] = max_waves
        await self._run_extra_phases_between(
            after_phase="INIT",
            before_phase="SEED",
            context=phase_context,
            run_id=run_id,
            estimated_waves=estimated_waves,
        )

        # Phase 1: SEED (no LLM calls, purely data construction — skip timeout wrapper)
        logger.info(f"[{run_id}] ━━━ SEED 阶段 ━━━")
        await self._emit(
            SimulationEvent(
                type="phase_start",
                phase="SEED",
                run_id=run_id,
                progress=self._progress("SEED", 0.0),
                total_waves=estimated_waves,
            )
        )
        seed = init_result.get("seed_ripple", {})
        seed_content = seed.get("content", "")
        if not isinstance(seed_content, str):
            seed_content = str(seed_content)
        seed_energy = _extract_float(seed.get("initial_energy", 0.5), 0.5)
        seed_ripple = Ripple(
            id=f"ripple_{run_id}_seed",
            content=seed_content,
            content_embedding=[],
            energy=seed_energy,
            origin_agent="omniscient",
            ripple_type="seed",
            emotion={},
            trace=["omniscient"],
            tick_born=0,
            mutations=[],
            root_id=f"ripple_{run_id}_seed",
        )
        self._seed_content = seed_content
        self._seed_energy = seed_energy

        # Inject embedding via EmbeddingProvider if available
        if self._providers is not None:
            try:
                from ripple.providers.registry import ProviderRegistry
                if isinstance(self._providers, ProviderRegistry):
                    emb_provider = self._providers.embedding
                    if emb_provider.is_available():
                        vec = await emb_provider.embed(seed_ripple.content)
                        if vec is not None:
                            seed_ripple.content_embedding = vec
            except Exception as exc:
                logger.warning(
                    "EmbeddingProvider failed, leaving content_embedding empty: %s", exc
                )

        # 增量记录：SEED 阶段结果 / Incremental record: SEED phase result
        if self._recorder:
            self._recorder.record_seed(seed_content, seed_energy)

        await self._emit(
            SimulationEvent(
                type="phase_end",
                phase="SEED",
                run_id=run_id,
                progress=self._progress("SEED", 1.0),
                total_waves=estimated_waves,
                detail={
                    "seed_content": seed_content[:200],
                    "seed_energy": seed_energy,
                },
            )
        )
        phase_context["seed_ripple"] = {
            "content": seed_content,
            "initial_energy": seed_energy,
        }
        await self._run_extra_phases_between(
            after_phase="SEED",
            before_phase="RIPPLE",
            context=phase_context,
            run_id=run_id,
            estimated_waves=estimated_waves,
        )

        # Phase 2: RIPPLE (统一涟漪循环) / Unified ripple loop
        wave_count = 0
        content_preview = seed_ripple.content[:50] if seed_ripple.content else ""
        history_lines = [
            f"种子涟漪已注入: '{content_preview}', 能量={seed_ripple.energy}"
        ]

        await self._emit(
            SimulationEvent(
                type="phase_start",
                phase="RIPPLE",
                run_id=run_id,
                progress=self._progress("RIPPLE", 0.0),
                wave=0,
                total_waves=estimated_waves,
            )
        )

        # Track RIPPLE phase start time for per-phase timeout check
        _ripple_phase_start = time.monotonic()
        _ripple_timeout = (
            _resolve_phase_timeout("RIPPLE") if _PHASE_TIMEOUTS_ENABLED else 0
        )
        _ripple_timed_out = False

        while wave_count < max_waves:
            # Per-phase timeout check for RIPPLE
            if _PHASE_TIMEOUTS_ENABLED and _ripple_timeout > 0:
                _elapsed = time.monotonic() - _ripple_phase_start
                if _elapsed > _ripple_timeout:
                    logger.error(
                        f"[{run_id}] RIPPLE phase exceeded timeout of {_ripple_timeout}s "
                        f"after {wave_count} waves"
                    )
                    _ripple_timed_out = True
                    break
            wave_frac = wave_count / max(estimated_waves, 1)
            logger.info(f"[{run_id}] ━━━ Wave {wave_count + 1}/{estimated_waves} ━━━")
            # 增量记录：wave 启动前的场快照 / Incremental record: field snapshot before wave starts
            pre_snapshot = self._build_snapshot()
            if self._recorder:
                self._recorder.record_wave_start(wave_count, pre_snapshot)

            verdict = await self._omniscient.ripple_verdict(
                field_snapshot=pre_snapshot,
                wave_number=wave_count,
                propagation_history=self._build_history_with_window(
                    history_lines[0],
                ),
                wave_time_window=wave_time_window,
                simulation_horizon=horizon_str,
            )

            await self._emit(
                SimulationEvent(
                    type="wave_start",
                    phase="RIPPLE",
                    run_id=run_id,
                    progress=self._progress("RIPPLE", wave_frac),
                    wave=wave_count,
                    total_waves=estimated_waves,
                    detail={
                        "global_observation": verdict.global_observation,
                    },
                )
            )

            if not verdict.continue_propagation:
                logger.info(
                    f"[{run_id}] 传播终止于 wave {wave_count}: "
                    f"{verdict.termination_reason or '全视者判定终止'}"
                )
                # 增量记录：wave 终止（传播结束） / Incremental record: wave terminated (propagation ends)
                if self._recorder:
                    self._recorder.record_wave_end(
                        wave_number=wave_count,
                        verdict=verdict,
                        agent_responses={},
                        post_snapshot=self._build_snapshot(),
                        terminated=True,
                    )
                await self._emit(
                    SimulationEvent(
                        type="wave_end",
                        phase="RIPPLE",
                        run_id=run_id,
                        progress=self._progress("RIPPLE", wave_frac),
                        wave=wave_count,
                        total_waves=estimated_waves,
                        detail={
                            "terminated": True,
                            "reason": verdict.termination_reason or "全视者判定终止",
                            "cas_signal": self._short_text(
                                verdict.global_observation
                                or verdict.termination_reason
                                or "全视者判定终止",
                                limit=120,
                            ),
                        },
                    )
                )
                break

            # Wave 0 Sea 保护: CAS 中种子扰动必须到达至少一个群体 Agent
            # / Wave 0 Sea guard: in CAS, seed perturbation must reach at least one group (Sea) agent
            if wave_count == 0:
                has_sea = any(
                    a.agent_id in self._seas for a in verdict.activated_agents
                )
                if not has_sea and self._seas:
                    first_sea_id = next(iter(self._seas))
                    verdict.activated_agents.append(
                        AgentActivation(
                            agent_id=first_sea_id,
                            incoming_ripple_energy=self._seed_energy * 0.3,
                            activation_reason=(
                                "CAS guard: seed perturbation must reach "
                                "at least one group agent"
                            ),
                        )
                    )
                    logger.warning(f"Wave 0 Sea guard: auto-injected {first_sea_id}")

            # 通知每个被激活的 Agent / Notify each activated agent
            for activation in verdict.activated_agents:
                aid = activation.agent_id
                atype = "sea" if aid in self._seas else "star"
                await self._emit(
                    SimulationEvent(
                        type="agent_activated",
                        phase="RIPPLE",
                        run_id=run_id,
                        progress=self._progress("RIPPLE", wave_frac),
                        wave=wave_count,
                        total_waves=estimated_waves,
                        agent_id=aid,
                        agent_type=atype,
                        detail={
                            "energy": activation.incoming_ripple_energy,
                            "agent_label": self._agent_label(aid),
                            "activation_reason": activation.activation_reason,
                        },
                    )
                )

            # 并行激活被选中的 Agent / Activate selected agents in parallel
            responses = await self._activate_agents(
                verdict,
                ripple_content=seed_ripple.content,
            )

            # 通知每个 Agent 的响应 / Notify each agent's response
            for aid, resp in responses.items():
                atype = "sea" if aid in self._seas else "star"
                response_preview = (
                    resp.get("cluster_reaction")
                    or resp.get("response_content")
                    or resp.get("reasoning")
                    or ""
                )
                await self._emit(
                    SimulationEvent(
                        type="agent_responded",
                        phase="RIPPLE",
                        run_id=run_id,
                        progress=self._progress("RIPPLE", wave_frac),
                        wave=wave_count,
                        total_waves=estimated_waves,
                        agent_id=aid,
                        agent_type=atype,
                        detail={
                            **resp,
                            "agent_label": self._agent_label(aid),
                            "response_preview": self._short_text(
                                response_preview, limit=120
                            ),
                        },
                    )
                )

            # 记录本轮 / Record this wave
            record = WaveRecord(
                wave_number=wave_count,
                verdict=verdict,
                agent_responses=responses,
                events=[],
            )
            self._wave_records.append(record)

            # 增量记录：wave 完成后的场快照和完整数据 / Incremental record: post-wave snapshot and full data
            if self._recorder:
                self._recorder.record_wave_end(
                    wave_number=wave_count,
                    verdict=verdict,
                    agent_responses=responses,
                    post_snapshot=self._build_snapshot(),
                )

            # 更新历史 / Update history
            for aid, resp in responses.items():
                history_lines.append(
                    f"Wave {wave_count}: {aid} → "
                    f"{resp.get('response_type', 'unknown')} "
                    f"(出能量={resp.get('outgoing_energy', 0.0):.2f})"
                )

            wave_count += 1
            response_mix: Dict[str, int] = {}
            response_notes: List[str] = []
            for aid, resp in responses.items():
                rtype = str(resp.get("response_type", "unknown"))
                response_mix[rtype] = response_mix.get(rtype, 0) + 1
                response_text = (
                    resp.get("cluster_reaction")
                    or resp.get("response_content")
                    or resp.get("reasoning")
                    or ""
                )
                if response_text:
                    response_notes.append(
                        f"{self._agent_label(aid)}：{self._short_text(response_text, limit=24)}"
                    )
            cas_signal = self._short_text(
                verdict.global_observation or "；".join(response_notes[:2]),
                limit=120,
            )
            await self._emit(
                SimulationEvent(
                    type="wave_end",
                    phase="RIPPLE",
                    run_id=run_id,
                    progress=self._progress(
                        "RIPPLE", wave_count / max(estimated_waves, 1)
                    ),
                    wave=wave_count - 1,
                    total_waves=estimated_waves,
                    detail={
                        "agent_count": len(responses),
                        "response_mix": response_mix,
                        "cas_signal": cas_signal,
                    },
                )
            )
        else:
            logger.warning(f"[{run_id}] 达到安全上限 {max_waves} waves，强制终止")

        effective_waves = wave_count

        await self._emit(
            SimulationEvent(
                type="phase_end",
                phase="RIPPLE",
                run_id=run_id,
                progress=self._progress("RIPPLE", 1.0),
                wave=effective_waves - 1,
                total_waves=estimated_waves,
                detail={"effective_waves": effective_waves},
            )
        )
        # PMF v3+: build compressed evidence pack for downstream phases (DELIBERATE/OBSERVE/SYNTHESIZE)
        self._evidence_pack = self._build_evidence_pack()
        phase_context["effective_waves"] = effective_waves
        phase_context["propagation_history"] = "\n".join(history_lines)
        phase_context["field_snapshot"] = self._build_snapshot()
        phase_context["evidence_pack"] = self._evidence_pack
        await self._run_extra_phases_between(
            after_phase="RIPPLE",
            before_phase="OBSERVE",
            context=phase_context,
            run_id=run_id,
            estimated_waves=estimated_waves,
        )

        # Phase 3: OBSERVE
        logger.info(f"[{run_id}] ━━━ OBSERVE 阶段 ━━━")
        await self._emit(
            SimulationEvent(
                type="phase_start",
                phase="OBSERVE",
                run_id=run_id,
                progress=self._progress("OBSERVE", 0.0),
                total_waves=estimated_waves,
            )
        )
        # v4.2: OBSERVE should explicitly incorporate DELIBERATE (if present)
        observe_history = "\n".join(history_lines)
        deliberate_output = self._extra_phase_outputs.get("DELIBERATE")
        if isinstance(deliberate_output, dict):
            deliberation_summary = deliberate_output.get("deliberation_summary")
            if deliberation_summary is None:
                # Fallback: include the whole output except raw records
                deliberation_summary = {
                    k: v
                    for k, v in deliberate_output.items()
                    if k != "deliberation_records"
                }
            payload = {
                "deliberation_summary": deliberation_summary,
                "deliberation_records_ref": self._json_pointer_for_process_key(
                    "deliberation"
                ),
            }
            observe_history += (
                "\n\n===== DELIBERATE SUMMARY (DATA) =====\n\n"
                + json.dumps(payload, ensure_ascii=False, indent=2, default=str)
                + "\n\n===== END DELIBERATE SUMMARY =====\n"
            )

        observation = await self._run_phase(
            self._omniscient.observe(
                field_snapshot=self._build_snapshot(),
                full_history=observe_history,
            ),
            phase_name="OBSERVE",
            run_id=run_id,
        )

        # 增量记录：OBSERVE 阶段结果 / Incremental record: OBSERVE phase result
        if self._recorder:
            self._recorder.record_observation(observation)

        await self._emit(
            SimulationEvent(
                type="phase_end",
                phase="OBSERVE",
                run_id=run_id,
                progress=self._progress("OBSERVE", 1.0),
                total_waves=estimated_waves,
                detail={
                    "observation_preview": observation,
                },
            )
        )
        phase_context["observation"] = observation
        phase_context["field_snapshot"] = self._build_snapshot()
        await self._run_extra_phases_between(
            after_phase="OBSERVE",
            before_phase="SYNTHESIZE",
            context=phase_context,
            run_id=run_id,
            estimated_waves=estimated_waves,
        )

        # Phase 4: FEEDBACK & RECORD
        # 拓扑更新由全视者建议（如果有） / Topology update by Omniscient suggestion (if any)
        # ... 持久化逻辑 ... / ... persistence logic ...

        # 合成结果 / Synthesize result
        logger.info(f"[{run_id}] ━━━ SYNTHESIZE 阶段 ━━━")
        await self._emit(
            SimulationEvent(
                type="phase_start",
                phase="SYNTHESIZE",
                run_id=run_id,
                progress=self._progress("SYNTHESIZE", 0.0),
                total_waves=estimated_waves,
            )
        )
        result = await self._run_phase(
            self._omniscient.synthesize_result(
                field_snapshot=self._build_snapshot(),
                observation=observation,
                simulation_input=simulation_input,
            ),
            phase_name="SYNTHESIZE",
            run_id=run_id,
        )

        result["observation"] = observation
        result["total_waves"] = effective_waves
        result["run_id"] = run_id
        result["wave_records_count"] = len(self._wave_records)

        # Propagate RIPPLE phase timeout marker to final result
        if _ripple_timed_out:
            result["timed_out"] = True
            result["timeout_phase"] = "RIPPLE"

        # HistoricalProvider 后置校验 / Post-hoc historical validation
        await self._validate_historical(result, simulation_input, run_id)

        # R4: Historical calibration with percentile baselines and actions
        calibration_report = self._calibrate_historical(result, simulation_input)

        # Build provider_insights from validation reports + provider state
        insights = self._build_provider_insights(simulation_input)

        # R4: Add calibration actions to provider_insights
        if calibration_report is not None and calibration_report.has_actions:
            insights.setdefault("historical", {})["calibration"] = {
                "bucket_key": calibration_report.bucket_key,
                "actions": [
                    {
                        "action_type": a.action_type,
                        "metric": a.metric,
                        "reason": a.reason,
                        "original_value": a.original_value,
                        "calibrated_value": a.calibrated_value,
                        "deviation_pct": a.deviation_pct,
                        "confidence_cap": a.confidence_cap,
                    }
                    for a in calibration_report.actions
                ],
            }

        # Always set provider_insights when providers are configured (even if empty dict);
        # omit the key entirely when no providers were configured (backward compat)
        if self._providers is not None:
            result["provider_insights"] = insights

        # R3/R4/R5/R6: Confidence Gate — evaluate multi-factor confidence
        gate_result = self._evaluate_confidence_gate(result, insights)
        result["confidence_gate"] = {
            "original_confidence": gate_result.original_confidence.value,
            "final_confidence": gate_result.final_confidence.value,
            "gate_applied": gate_result.gate_applied,
            "reason": gate_result.reason,
            "factors": [
                {
                    "name": f.name,
                    "level": f.level.value,
                    "reason": f.reason,
                    "passed": f.passed,
                }
                for f in gate_result.factors
            ],
        }
        # Apply gated confidence to prediction if gate fired
        if gate_result.gate_applied:
            pred = result.get("prediction")
            if isinstance(pred, dict):
                pred["confidence"] = gate_result.final_confidence.value
                pred["confidence_gate_reason"] = gate_result.reason
            result["confidence"] = gate_result.final_confidence.value

            # R3/R4: Generate calibrated predictions from calibration report
            self._apply_calibrated_predictions(result)

        # Store confidence gate result in quality sub-dict for structured access
        result.setdefault("quality", {})["confidence_gate_result"] = {
            "original_confidence": gate_result.original_confidence.value,
            "final_confidence": gate_result.final_confidence.value,
            "gate_applied": gate_result.gate_applied,
            "reason": gate_result.reason,
        }

        # R6: Parse tribunal audit fields from DELIBERATE phase output
        tribunal_audit = self._parse_tribunal_audit()
        if tribunal_audit is not None:
            result["quality"]["tribunal_audit"] = tribunal_audit

        # R3: Mark as relative simulation when no real providers are available
        provider_factor = next((f for f in gate_result.factors if f.name == "provider_availability"), None)
        if provider_factor and not provider_factor.passed:
            result["simulation_mode"] = "relative"

        # R1: Parse prediction into structured contract
        try:
            from ripple.primitives.prediction_quality import parse_prediction_contract
            contract = parse_prediction_contract(
                result.get("prediction", {}),
                skill_id=getattr(self, "_skill_id", "") or "",
                evidence_pack_v2=self._evidence_pack_v2,
            )
            result["prediction_contract"] = contract.to_dict()
        except Exception as exc:
            logger.warning("Prediction contract parsing failed (non-fatal): %s", exc)

        # R8: Generate prediction quality report
        from ripple.engine.quality_report import build_quality_report
        # Extract deliberation_summary from extra phase outputs for tribunal divergence
        _delib_output = self._extra_phase_outputs.get("DELIBERATE", {})
        _delib_summary = _delib_output.get("deliberation_summary") if isinstance(_delib_output, dict) else None
        quality_report = build_quality_report(
            simulation_input=simulation_input,
            result=result,
            providers=self._providers,
            evidence_pack_v2=self._evidence_pack_v2,
            calibration_report=self._calibration_report,
            deliberation_summary=_delib_summary,
        )
        result["quality_report"] = quality_report.to_dict()

        # 增量记录：SYNTHESIZE 阶段结果（合成数据写入顶层键以保持向后兼容） / Incremental record: SYNTHESIZE result (top-level keys for backward compat)
        if self._recorder:
            self._recorder.record_synthesis(result)
            if insights:
                self._recorder.record_process("providers", insights)
            self._recorder.record_process("quality_report", quality_report.to_dict())
            # R2: Store evidence pack V2 in recorder for downstream consumers
            if self._evidence_pack_v2 is not None:
                from dataclasses import asdict as _asdict
                self._recorder.record_process("evidence_pack", _asdict(self._evidence_pack_v2))

        logger.info(f"[{run_id}] 模拟完成: {effective_waves} waves, LLM 调用链结束")
        # Build quality detail for SSE event
        quality_detail: Dict[str, Any] = {
            "total_waves": effective_waves,
            "prediction_verdict": self._short_text(
                (
                    result.get("prediction", {}).get("verdict")
                    if isinstance(result.get("prediction"), dict)
                    else result.get("prediction")
                )
                or result.get("prediction_verdict")
                or "",
                limit=160,
            ),
        }
        # R8: quality fields in SSE — confidence_gate_result
        if "confidence_gate" in result:
            cg = result["confidence_gate"]
            quality_detail["confidence_gate_result"] = {
                "original_confidence": cg.get("original_confidence"),
                "final_confidence": cg.get("final_confidence"),
                "gate_applied": cg.get("gate_applied"),
                "reason": cg.get("reason"),
            }

        # R8: evidence_balance — always include (zero counts when no evidence pack)
        ev_balance: Dict[str, Any] = {
            "positive_count": 0,
            "negative_count": 0,
            "silent_count": 0,
            "balanced": True,
        }
        if self._evidence_pack_v2 is not None:
            ep = self._evidence_pack_v2
            pos = ep.positive_signals.count
            neg = ep.negative_signals.count
            silent = ep.silent_signals.count
            ev_balance = {
                "positive_count": pos,
                "negative_count": neg,
                "silent_count": silent,
                "balanced": not (pos > 0 and neg == 0 and pos > 5) and not (silent > (pos + neg) and (pos + neg + silent) > 5),
            }
        quality_detail["evidence_balance"] = ev_balance
        # Also store in result["quality"] for API consumers
        result.setdefault("quality", {})["evidence_balance"] = ev_balance

        # R8: provider_status — include even when no providers (available: False)
        provider_status_detail: Dict[str, Any] = {"available": False, "categories": []}
        if self._providers is not None:
            from ripple.providers.registry import ProviderRegistry
            if isinstance(self._providers, ProviderRegistry):
                available_categories: List[str] = []
                cat_status: Dict[str, str] = {}
                for cat in ("historical", "topology", "embedding", "ambient"):
                    try:
                        p = self._providers.get(cat)
                        status = "available" if p.is_available() else "stub"
                        cat_status[cat] = status
                        if status == "available":
                            available_categories.append(cat)
                    except Exception:
                        cat_status[cat] = "error"
                provider_status_detail = {
                    "available": len(available_categories) > 0,
                    "categories": available_categories,
                    "detail": cat_status,
                }
        quality_detail["provider_status"] = provider_status_detail
        # Also store in result["quality"] for API consumers
        result.setdefault("quality", {})["provider_status"] = provider_status_detail

        # R8: Full quality report in SSE event
        quality_detail["quality_report"] = result.get("quality_report", {})

        await self._emit(
            SimulationEvent(
                type="phase_end",
                phase="SYNTHESIZE",
                run_id=run_id,
                progress=1.0,
                total_waves=estimated_waves,
                detail=quality_detail,
            )
        )
        return result

    async def _validate_topology(
        self,
        init_result: Dict[str, Any],
        simulation_input: Dict[str, Any],
        run_id: str,
    ) -> None:
        """Post-hoc validation: compare LLM topology with TopologyProvider data."""
        providers = self._providers
        if not providers or not hasattr(providers, "topology"):
            return
        topo_provider = providers.topology
        if not topo_provider or not topo_provider.is_available():
            return

        llm_topology = init_result.get("topology")
        if not llm_topology or not isinstance(llm_topology, dict):
            return

        try:
            provider_topology = await topo_provider.get_topology(
                skill_id=getattr(self, "_skill_id", None),
                platform=simulation_input.get("event", {}).get("platform"),
            )
            if not provider_topology:
                return

            from ripple.providers.topology_validator import TopologyValidator
            from ripple.providers.topology import TopologyData
            validator = TopologyValidator()
            report = validator.validate(TopologyData(llm_topology), provider_topology)
            report.log()
            logger.info(
                "Topology validation complete for run %s — acceptable: %s",
                run_id,
                report.is_acceptable,
            )
            self._validation_reports["topology"] = report
        except Exception as exc:
            logger.warning("Topology validation failed (non-fatal): %s", exc)

    async def _inject_historical(
        self,
        simulation_input: Dict[str, Any],
        run_id: str,
    ) -> None:
        """Pre-inject historical data from HistoricalProvider into simulation_input."""
        providers = self._providers
        if not providers or not hasattr(providers, "historical"):
            return
        hist_provider = providers.historical
        if not hist_provider or not hist_provider.is_available():
            return

        if "historical" in simulation_input and simulation_input["historical"]:
            # User provided historical data; don't override, but count existing records
            self._historical_records_injected = len(simulation_input["historical"])
            return

        try:
            records = await hist_provider.get_historical(
                skill_id=getattr(self, "_skill_id", None),
                platform=simulation_input.get("event", {}).get("platform"),
            )
            if records:
                simulation_input["historical"] = records
                self._historical_records_injected = len(records)
                logger.info(
                    "HistoricalProvider injected %d records for run %s",
                    len(records),
                    run_id,
                )
        except Exception as exc:
            logger.warning("HistoricalProvider injection failed (non-fatal): %s", exc)

    async def _validate_historical(
        self,
        synthesize_result: Dict[str, Any],
        simulation_input: Dict[str, Any],
        run_id: str,
    ) -> None:
        """Post-hoc validation: compare LLM SYNTHESIZE prediction with HistoricalProvider data."""
        providers = self._providers
        if not providers or not hasattr(providers, "historical"):
            return
        hist_provider = providers.historical
        if not hist_provider or not hist_provider.is_available():
            return

        historical = simulation_input.get("historical")
        if not historical or not isinstance(historical, list):
            return

        try:
            from ripple.providers.historical_validator import HistoricalValidator
            validator = HistoricalValidator()
            report = validator.validate(synthesize_result.get("prediction", {}), historical)
            report.log()
            logger.info(
                "Historical validation complete for run %s — acceptable: %s",
                run_id,
                report.is_acceptable,
            )
            self._validation_reports["historical"] = report
        except Exception as exc:
            logger.warning("Historical validation failed (non-fatal): %s", exc)

    def _calibrate_historical(
        self,
        synthesize_result: Dict[str, Any],
        simulation_input: Dict[str, Any],
    ) -> Any:
        """R4: Calibrate prediction against historical data with percentile baselines.

        Returns a CalibrationReport with structured actions, or None on failure.
        """
        historical = simulation_input.get("historical")
        if not historical or not isinstance(historical, list):
            return None

        try:
            from ripple.providers.historical_calibrator import HistoricalCalibrator
            calibrator = HistoricalCalibrator()

            # Build bucket context from simulation input
            bucket_context = {}
            for key in ("platform", "channel", "vertical"):
                val = simulation_input.get(key)
                if val:
                    bucket_context[key] = val

            report = calibrator.calibrate(
                synthesize_result.get("prediction", {}),
                historical,
                bucket_context=bucket_context or None,
            )
            report.log()
            self._calibration_report = report
            return report
        except Exception as exc:
            logger.warning("Historical calibration failed (non-fatal): %s", exc)
            return None

    # ------------------------------------------------------------------
    # Provider Insights — build summary of provider usage for output
    # ------------------------------------------------------------------

    def _build_provider_insights(self, simulation_input: Dict[str, Any]) -> Dict[str, Any]:
        """Build a dict summarizing which providers were active and their validation results.

        Returns an empty dict {} when all providers are stubs/unavailable.
        """
        insights: Dict[str, Any] = {}
        providers = self._providers
        if not providers:
            return insights

        stub_names = {
            "StubTopologyProvider",
            "StubHistoricalProvider",
            "StubEmbeddingProvider",
            "StubAmbientProvider",
        }

        for cat in ("topology", "historical", "embedding", "ambient"):
            try:
                p = getattr(providers, cat, None)
            except Exception:
                continue
            if p is None:
                continue
            # Skip stub providers — they represent "no real provider configured"
            if type(p).__name__ in stub_names:
                continue
            try:
                available = p.is_available()
            except Exception:
                available = False
            entry: Dict[str, Any] = {"available": available}

            if cat == "historical":
                records_injected = self._historical_records_injected
                if records_injected > 0:
                    entry["records_injected"] = records_injected

            if cat in self._validation_reports:
                try:
                    entry["validation"] = self._serialize_validation(
                        self._validation_reports[cat]
                    )
                except Exception:
                    logger.warning("Failed to serialize validation report for %s", cat)

            insights[cat] = entry

        return insights

    def _parse_tribunal_audit(self) -> Optional[Dict[str, Any]]:
        """R6: Robustly parse the 6 audit fields from DELIBERATE phase output.

        Handles:
        - Nested dict access (deliberation_summary.audit.field)
        - Direct field access on DeliberationRecord
        - Fallback to text extraction if structured fields are missing
        - Default values when parsing fails

        Returns a dict with the 6 R6 fields, or None when no DELIBERATE phase exists.
        """
        deliberate_output = self._extra_phase_outputs.get("DELIBERATE")
        if not isinstance(deliberate_output, dict):
            return None

        # Path 1: Structured audit from deliberation_summary.audit
        summary = deliberate_output.get("deliberation_summary")
        if isinstance(summary, dict):
            audit = summary.get("audit")
            if isinstance(audit, dict):
                return {
                    "key_evidence": _safe_str_list(audit.get("key_evidence")),
                    "uncertainties": _safe_str_list(audit.get("uncertainties")),
                    "optimism_audit": _safe_str_list(audit.get("optimism_audit")),
                    "overrated_dimensions": _safe_str_list(audit.get("overrated_dimensions")),
                    "missing_evidence": _safe_str_list(audit.get("missing_evidence")),
                    "recommended_confidence_cap": _normalize_cap(audit.get("recommended_confidence_cap")),
                }

        # Path 2: Audit extracted from DeliberationRecord in simulate.py handler
        # The handler in simulate.py already puts audit fields into deliberation_summary
        # if DeliberationRecord has them. Check for top-level audit fields in summary.
        if isinstance(summary, dict):
            # Check if summary has audit-like fields directly (flat structure)
            has_audit_data = any(
                isinstance(summary.get(k), list) and summary.get(k)
                for k in ("key_evidence", "uncertainties", "optimism_audit",
                          "overrated_dimensions", "missing_evidence")
            )
            if has_audit_data:
                return {
                    "key_evidence": _safe_str_list(summary.get("key_evidence")),
                    "uncertainties": _safe_str_list(summary.get("uncertainties")),
                    "optimism_audit": _safe_str_list(summary.get("optimism_audit")),
                    "overrated_dimensions": _safe_str_list(summary.get("overrated_dimensions")),
                    "missing_evidence": _safe_str_list(summary.get("missing_evidence")),
                    "recommended_confidence_cap": _normalize_cap(summary.get("recommended_confidence_cap")),
                }

        # Path 3: Extract from raw deliberation_records (last round)
        records = deliberate_output.get("deliberation_records", [])
        if isinstance(records, list) and records:
            last_record = records[-1]
            if isinstance(last_record, dict):
                has_audit_data = any(
                    isinstance(last_record.get(k), list) and last_record.get(k)
                    for k in ("key_evidence", "uncertainties", "optimism_audit",
                              "overrated_dimensions", "missing_evidence")
                )
                if has_audit_data:
                    return {
                        "key_evidence": _safe_str_list(last_record.get("key_evidence")),
                        "uncertainties": _safe_str_list(last_record.get("uncertainties")),
                        "optimism_audit": _safe_str_list(last_record.get("optimism_audit")),
                        "overrated_dimensions": _safe_str_list(last_record.get("overrated_dimensions")),
                        "missing_evidence": _safe_str_list(last_record.get("missing_evidence")),
                        "recommended_confidence_cap": _normalize_cap(last_record.get("recommended_confidence_cap")),
                    }

        # Path 4: Text extraction fallback — search narrative text for audit-like keywords
        # This is a last resort for LLMs that don't output structured JSON
        narratives: List[str] = []
        if isinstance(summary, dict):
            final_positions = summary.get("final_positions", [])
            if isinstance(final_positions, list):
                for pos in final_positions:
                    if isinstance(pos, dict) and pos.get("narrative"):
                        narratives.append(str(pos.get("narrative")))
        if isinstance(records, list):
            for rec in records:
                if isinstance(rec, dict):
                    for op in rec.get("opinions", []):
                        if isinstance(op, dict) and op.get("narrative"):
                            narratives.append(str(op.get("narrative")))

        if narratives:
            combined_text = " ".join(narratives)
            # Simple keyword extraction for common audit signals
            optimism_indicators = []
            uncertainty_indicators = []
            _OPTIMISM_KEYWORDS = ("overly optimistic", "overestimate", "too high", "乐观",
                                  "高估", "过于乐观", "overrated")
            _UNCERTAINTY_KEYWORDS = ("uncertain", "unclear", "not sure", "不确定",
                                     "缺乏证据", "insufficient evidence")
            for kw in _OPTIMISM_KEYWORDS:
                if kw.lower() in combined_text.lower():
                    optimism_indicators.append(f"Detected optimism signal: {kw}")
            for kw in _UNCERTAINTY_KEYWORDS:
                if kw.lower() in combined_text.lower():
                    uncertainty_indicators.append(f"Detected uncertainty signal: {kw}")

            if optimism_indicators or uncertainty_indicators:
                return {
                    "key_evidence": [],
                    "uncertainties": uncertainty_indicators,
                    "optimism_audit": optimism_indicators,
                    "overrated_dimensions": [],
                    "missing_evidence": [],
                    "recommended_confidence_cap": None,
                }

        # No audit data found — return None (no DELIBERATE audit available)
        return None

    def _evaluate_confidence_gate(
        self,
        result: Dict[str, Any],
        provider_insights: Dict[str, Any],
    ) -> Any:
        """R3/R4/R5/R6: Evaluate multi-factor confidence gate on SYNTHESIZE output.

        Factors:
        1. Provider availability — missing → cap to medium
        2. Ensemble stability — low kappa/stability → lower confidence
        3. Historical deviation — exceeded threshold → lower confidence
        4. Evidence balance — positive/negative imbalance → lower confidence
        5. Tribunal audit — recommended confidence cap from DELIBERATE phase
        6. Topology calibration — scale/type deviation from provider data
        """
        from ripple.primitives.prediction_quality import ConfidenceGate

        gate = ConfidenceGate()

        # Extract raw confidence from result
        pred = result.get("prediction", {})
        if isinstance(pred, dict):
            raw_confidence = pred.get("confidence", "medium")
        else:
            raw_confidence = result.get("confidence", "medium")

        # Factor 1: Provider availability
        provider_available = False
        if self._providers is not None:
            from ripple.providers.registry import ProviderRegistry
            if isinstance(self._providers, ProviderRegistry):
                for cat in ("historical", "topology", "embedding", "ambient"):
                    try:
                        p = self._providers.get(cat)
                        if p.is_available():
                            provider_available = True
                            break
                    except Exception:
                        pass

        # Factor 2: Ensemble stability
        ensemble_stats = result.get("ensemble_stats", {})
        ensemble_kappa = ensemble_stats.get("dimension_agreement_kappa") if isinstance(ensemble_stats, dict) else None
        ensemble_stability = None
        # Derive from dimension aggregates stability_level if present
        dim_agg = ensemble_stats.get("dimension_aggregates", {}) if isinstance(ensemble_stats, dict) else {}
        if isinstance(dim_agg, dict):
            stability_levels = set()
            for dim_vals in dim_agg.values():
                if isinstance(dim_vals, dict):
                    sl = dim_vals.get("stability_level")
                    if sl:
                        stability_levels.add(sl)
            if stability_levels:
                ensemble_stability = min(stability_levels, key=lambda x: {"high": 0, "medium": 1, "low": 2}.get(x, 1))
        agreement_rate = ensemble_stats.get("grade_agreement_rate") if isinstance(ensemble_stats, dict) else None

        # Factor 3: Historical deviation
        hist_max_dev = None
        hist_insights = provider_insights.get("historical", {})
        if isinstance(hist_insights, dict):
            hist_validation = hist_insights.get("validation", {})
            if isinstance(hist_validation, dict):
                hist_max_dev = hist_validation.get("max_deviation_pct")

        # Factor 4: Evidence balance
        pos_count = 0
        neg_count = 0
        silent_count = 0
        if self._evidence_pack_v2 is not None:
            pos_count = self._evidence_pack_v2.positive_signals.count
            neg_count = self._evidence_pack_v2.negative_signals.count
            silent_count = self._evidence_pack_v2.silent_signals.count

        # R6: Tribunal recommended confidence cap
        # Primary path: from deliberation_summary.audit (populated by simulate.py handler)
        tribunal_cap = None
        deliberate_output = self._extra_phase_outputs.get("DELIBERATE", {})
        if isinstance(deliberate_output, dict):
            audit = (deliberate_output.get("deliberation_summary") or {}).get("audit")
            if isinstance(audit, dict):
                tribunal_cap = audit.get("recommended_confidence_cap")

        # Fallback: use _parse_tribunal_audit for more robust extraction
        if tribunal_cap is None:
            parsed_audit = self._parse_tribunal_audit()
            if parsed_audit is not None:
                tribunal_cap = parsed_audit.get("recommended_confidence_cap")

        # R3: Topology calibration from validation report
        topo_scale_ok = None
        topo_type_ok = None
        topo_report = self._validation_reports.get("topology")
        if topo_report is not None:
            topo_scale_ok = getattr(getattr(topo_report, "scale", None), "is_acceptable", None)
            topo_type_ok = getattr(getattr(topo_report, "type_dist", None), "is_acceptable", None)

        return gate.evaluate(
            raw_confidence,
            provider_available=provider_available,
            ensemble_kappa=ensemble_kappa,
            ensemble_stability=ensemble_stability,
            ensemble_agreement_rate=agreement_rate,
            historical_max_deviation_pct=hist_max_dev,
            evidence_positive_count=pos_count,
            evidence_negative_count=neg_count,
            evidence_silent_count=silent_count,
            tribunal_confidence_cap=tribunal_cap,
            topology_scale_acceptable=topo_scale_ok,
            topology_type_acceptable=topo_type_ok,
        )

    def _apply_calibrated_predictions(
        self,
        result: Dict[str, Any],
    ) -> None:
        """R3/R4: Generate calibrated predictions from HistoricalCalibrator report.

        When the confidence gate fires AND a calibration report exists with
        ``calibrated_prediction`` actions, this method:

        1. Preserves the original LLM numeric fields in ``raw_predictions``.
        2. Overwrites numeric prediction fields with historical percentile
           baselines (P75 cap when predicted > P95, median adjustment otherwise).
        3. Adds ``calibration_method`` explaining what was done.

        The method is non-fatal — any exception is caught and logged.
        """
        if self._calibration_report is None:
            return

        pred = result.get("prediction")
        if not isinstance(pred, dict):
            return

        try:
            # Collect calibrated_prediction actions: metric -> calibrated_value
            calibrations: Dict[str, Dict[str, Any]] = {}
            for action in self._calibration_report.actions:
                if action.action_type == "calibrated_prediction" and action.calibrated_value is not None:
                    calibrations[action.metric] = {
                        "calibrated_value": action.calibrated_value,
                        "original_value": action.original_value,
                        "deviation_pct": action.deviation_pct,
                    }

            if not calibrations:
                return

            # Determine calibration method from the worst action
            methods: List[str] = []
            for action in self._calibration_report.actions:
                if action.action_type == "calibrated_prediction":
                    methods.append("historical_p95_cap")
                    break
            if not methods:
                methods.append("historical_median_adjustment")

            calibration_method = methods[0]

            # For metrics with calibrated_prediction actions, also check
            # if they have a PercentileBaseline in calibrated_metrics
            for cm in self._calibration_report.calibrated_metrics:
                if cm.metric in calibrations and cm.baseline is not None:
                    # If predicted > P95, use P75 as the cap (more conservative)
                    # Otherwise, use the calibrated value (P95 cap from the action)
                    if cm.predicted > cm.baseline.p95:
                        calibrations[cm.metric]["calibrated_value"] = round(cm.baseline.p75, 2)
                        calibration_method = "historical_p75_cap"
                    elif cm.predicted > cm.baseline.median:
                        calibrations[cm.metric]["calibrated_value"] = round(cm.baseline.median, 2)
                        calibration_method = "historical_median_adjustment"

            # Preserve original predictions
            raw_predictions: Dict[str, Any] = {}
            for metric in calibrations:
                if metric in pred and isinstance(pred[metric], (int, float)):
                    raw_predictions[metric] = pred[metric]

            if not raw_predictions:
                return

            # Apply calibrated values
            for metric, cal_info in calibrations.items():
                if metric in raw_predictions:
                    pred[metric] = cal_info["calibrated_value"]

            # Store raw predictions and method for transparency
            pred["raw_predictions"] = raw_predictions
            pred["calibration_method"] = calibration_method

            logger.info(
                "Applied calibrated predictions: %s (method=%s)",
                list(calibrations.keys()),
                calibration_method,
            )

        except Exception as exc:
            logger.warning("Calibrated prediction application failed (non-fatal): %s", exc)

    def _serialize_validation(self, report: Any) -> Dict[str, Any]:
        """Serialize a validation report into the provider_insights schema.

        Supports HistoricalValidationReport and topology ValidationReport.
        """
        from ripple.providers.historical_validator import HistoricalValidationReport
        from ripple.providers.topology_validator import ValidationReport as TopologyValidationReport

        if isinstance(report, HistoricalValidationReport):
            deviations = report.metric_deviations
            exceeded = [d for d in deviations if not d.is_acceptable]
            max_dev = max(
                (abs(d.deviation_pct) for d in deviations), default=0.0
            )
            return {
                "acceptable": report.is_acceptable,
                "deviation_count": len(deviations),
                "max_deviation_pct": round(max_dev, 2),
                "exceeded": [
                    {
                        "metric": d.metric,
                        "predicted": d.predicted,
                        "historical_avg": d.historical_avg,
                        "deviation_pct": d.deviation_pct,
                    }
                    for d in exceeded
                ],
            }

        if isinstance(report, TopologyValidationReport):
            topo_exceeded: List[Dict[str, Any]] = []
            if not report.scale.is_acceptable:
                # Scale has two independent metrics (nodes, edges); emit each that exceeds threshold
                topo_exceeded.extend(self._serialize_scale_checks(report.scale))
            if not report.structure.is_acceptable:
                topo_exceeded.append(self._serialize_topology_check("structure", report.structure))
            if not report.type_dist.is_acceptable:
                topo_exceeded.append(self._serialize_topology_check("type_dist", report.type_dist))
            max_dev = max(
                (
                    abs(v)
                    for v in [
                        report.scale.node_deviation_pct,
                        report.scale.edge_deviation_pct,
                        report.type_dist.star_deviation_pct,
                    ]
                ),
                default=0.0,
            )
            return {
                "acceptable": report.is_acceptable,
                "deviation_count": 3,  # topology has 3 sub-checks (scale, structure, type_dist)
                "max_deviation_pct": round(max_dev, 2),
                "exceeded": topo_exceeded,
            }

        # Unknown report type — return minimal info
        is_acceptable = getattr(report, "is_acceptable", None)
        return {
            "acceptable": is_acceptable,
            "deviation_count": 0,
            "max_deviation_pct": 0.0,
            "exceeded": [],
        }

    @staticmethod
    def _serialize_scale_checks(scale: Any) -> List[Dict[str, Any]]:
        """Serialize a ScaleCheck into one or two exceeded entries.

        Scale has two independent metrics (node_count, edge_count).
        Each that individually exceeds the threshold is emitted separately.
        """
        results: List[Dict[str, Any]] = []
        if abs(scale.node_deviation_pct) > scale.threshold:
            results.append({
                "metric": "node_count",
                "predicted": scale.llm_nodes,
                "historical_avg": scale.provider_nodes,
                "deviation_pct": scale.node_deviation_pct,
            })
        if abs(scale.edge_deviation_pct) > scale.threshold:
            results.append({
                "metric": "edge_count",
                "predicted": scale.llm_edges,
                "historical_avg": scale.provider_edges,
                "deviation_pct": scale.edge_deviation_pct,
            })
        # If neither individually exceeds threshold but is_acceptable is False
        # (shouldn't happen with current ScaleCheck logic, but be safe),
        # emit the most deviated one.
        if not results:
            node_dev = abs(scale.node_deviation_pct)
            edge_dev = abs(scale.edge_deviation_pct)
            if node_dev >= edge_dev:
                results.append({
                    "metric": "node_count",
                    "predicted": scale.llm_nodes,
                    "historical_avg": scale.provider_nodes,
                    "deviation_pct": scale.node_deviation_pct,
                })
            else:
                results.append({
                    "metric": "edge_count",
                    "predicted": scale.llm_edges,
                    "historical_avg": scale.provider_edges,
                    "deviation_pct": scale.edge_deviation_pct,
                })
        return results

    @staticmethod
    def _serialize_topology_check(label: str, check: Any) -> Dict[str, Any]:
        """Serialize a topology sub-check (StructCheck/TypeCheck) into exceeded format."""
        if label == "structure":
            return {
                "metric": "connectivity",
                "predicted": check.llm_connected,
                "historical_avg": check.provider_connected,
                "deviation_pct": 0.0,  # Not a percentage-based deviation
            }

        if label == "type_dist":
            return {
                "metric": "star_ratio",
                "predicted": round(check.llm_star_ratio, 4),
                "historical_avg": round(check.provider_star_ratio, 4),
                "deviation_pct": check.star_deviation_pct,
            }

        # Fallback
        return {
            "metric": label,
            "predicted": None,
            "historical_avg": None,
            "deviation_pct": 0.0,
        }

    def _create_agents(self, init_result: Dict[str, Any]) -> None:
        """根据全视者 INIT 结果创建星海 Agent。 / Create Star/Sea agents from Omniscient INIT result.

        v4: Inject skill prompts into agent system_prompt_template (trusted zone).
        """
        # v4: Build skill context wrappers for star/sea
        from ripple.prompts import SKILL_CONTEXT_SEPARATOR, SKILL_CONTEXT_END

        star_skill = ""
        if self._skill_prompts.get("star"):
            star_skill = (
                SKILL_CONTEXT_SEPARATOR
                + self._skill_prompts["star"]
                + SKILL_CONTEXT_END
            )
        sea_skill = ""
        if self._skill_prompts.get("sea"):
            sea_skill = (
                SKILL_CONTEXT_SEPARATOR + self._skill_prompts["sea"] + SKILL_CONTEXT_END
            )

        for sc in init_result.get("star_configs", []):
            self._stars[sc["id"]] = StarAgent(
                agent_id=sc["id"],
                description=sc.get("description", ""),
                llm_caller=self._star_caller,
                system_prompt_template=star_skill,
            )
        for sc in init_result.get("sea_configs", []):
            self._seas[sc["id"]] = SeaAgent(
                agent_id=sc["id"],
                description=sc.get("description", ""),
                llm_caller=self._sea_caller,
                system_prompt_template=sea_skill,
            )

    async def _activate_agents(
        self,
        verdict: OmniscientVerdict,
        ripple_content: str = "",
    ) -> Dict[str, Dict[str, Any]]:
        """并行激活被裁决选中的 Agent。 / Activate verdict-selected agents in parallel."""
        known_ids = set(self._stars.keys()) | set(self._seas.keys())
        if verdict.activated_agents:
            activated_ids = [a.agent_id for a in verdict.activated_agents]
            logger.info(f"本轮激活 {len(activated_ids)} 个 Agent: {activated_ids}")
        else:
            logger.info(f"本轮未激活任何 Agent（已注册: {list(known_ids)}）")

        tasks = {}
        for activation in verdict.activated_agents:
            aid = activation.agent_id
            agent = self._stars.get(aid) or self._seas.get(aid)
            if agent is None:
                logger.warning(
                    f"全视者激活了未知 Agent: {aid}（已注册: {list(known_ids)}）"
                )
                continue
            is_sea = aid in self._seas
            logger.info(
                f"激活 {'Sea' if is_sea else 'Star'} Agent: {aid}, "
                f"能量={activation.incoming_ripple_energy:.2f}"
            )
            tasks[aid] = agent.respond(
                ripple_content=ripple_content or self._seed_content,
                ripple_energy=activation.incoming_ripple_energy,
                ripple_source="omniscient_verdict",
            )

        results = {}
        if tasks:
            done = await asyncio.gather(
                *tasks.values(),
                return_exceptions=True,
            )
            for aid, result in zip(tasks.keys(), done):
                if isinstance(result, Exception):
                    logger.error(f"Agent {aid} 响应失败: {result}")
                    results[aid] = {"response_type": "error", "outgoing_energy": 0.0}
                else:
                    results[aid] = result

        return results

    def _build_snapshot(self) -> Dict[str, Any]:
        """构建当前 Field 快照供全视者参考。 / Build current Field snapshot for Omniscient reference."""
        agent_stats = self._extract_agent_stats()

        snapshot: Dict[str, Any] = {
            "seed_content": self._seed_content[:200] if self._seed_content else "",
            "seed_energy": self._seed_energy,
            "stars": {
                sid: {
                    "description": s.description,
                    "memory_count": len(s.memory),
                    **agent_stats.get(sid, _empty_agent_stats()),
                }
                for sid, s in self._stars.items()
            },
            "seas": {
                sid: {
                    "description": s.description,
                    "memory_count": len(s.memory),
                    **agent_stats.get(sid, _empty_agent_stats()),
                }
                for sid, s in self._seas.items()
            },
            "wave_records_count": len(self._wave_records),
        }
        # 拓扑信息（INIT 阶段后可用） / Topology info (available after INIT phase)
        topology = getattr(self, "_topology", None)
        if topology is not None:
            snapshot["topology"] = topology
        if getattr(self, "_wave_time_window", ""):
            snapshot["wave_time_window"] = self._wave_time_window
        if getattr(self, "_simulation_horizon", ""):
            snapshot["simulation_horizon"] = self._simulation_horizon
        if hasattr(self, "_energy_decay_per_wave"):
            snapshot["energy_decay_per_wave"] = self._energy_decay_per_wave

        # PMF v3+: compressed evidence pack (used by deliberation + synthesis)
        if getattr(self, "_evidence_pack", None) is not None:
            snapshot["evidence_pack"] = self._evidence_pack

        # Optional extra phase outputs (keep lightweight to avoid context overflow)
        if getattr(self, "_extra_phase_outputs", None):
            view: Dict[str, Any] = {}
            for phase_name, output in self._extra_phase_outputs.items():
                if phase_name == "DELIBERATE" and isinstance(output, dict):
                    view[phase_name] = {
                        k: v for k, v in output.items() if k != "deliberation_records"
                    }
                    view[phase_name]["deliberation_records_ref"] = (
                        self._json_pointer_for_process_key("deliberation")
                    )
                else:
                    view[phase_name] = output
            snapshot["extra_phases"] = view

        return snapshot

    def _extract_agent_stats(self) -> Dict[str, Dict[str, Any]]:
        """从 wave_records 中提取每个 Agent 的累积状态。 / Extract cumulative stats per agent from wave_records."""
        stats: Dict[str, Dict[str, Any]] = {}
        for record in self._wave_records:
            for activation in record.verdict.activated_agents:
                aid = activation.agent_id
                if aid not in stats:
                    stats[aid] = {
                        "activation_count": 0,
                        "last_wave": None,
                        "last_energy": 0.0,
                        "last_response": None,
                        "total_outgoing_energy": 0.0,
                    }
                s = stats[aid]
                s["activation_count"] += 1
                s["last_wave"] = record.wave_number
                s["last_energy"] = activation.incoming_ripple_energy
                resp = record.agent_responses.get(aid, {})
                s["last_response"] = resp.get("response_type")
                s["total_outgoing_energy"] += resp.get("outgoing_energy", 0.0)
        return stats

    def _build_evidence_pack(self) -> Dict[str, Any]:
        """Build a compressed evidence pack from wave records (PMF v3+ / R2).

        Keeps structure stable and bounded:
        - summary: <= 500 chars
        - key_signals: <= 10 items (with evidence_id)
        - positive_signals / negative_signals / silent_signals: classified by type
        - stratified: Star/Sea counts and energy
        - response_type_distribution: full distribution
        - energy_decay: per-wave energy totals
        - cross_layer_depth: max propagation depth
        - pack_id: unique id for cross-referencing
        - full_records_ref: JSON Pointer to raw wave records in recorder output
        """
        from ripple.primitives.prediction_quality import (
            EvidencePackV2,
            SignalSummary,
            StratifiedStats,
            EnergyDecaySummary,
        )

        total_waves = len(self._wave_records)
        response_type_counts: Dict[str, int] = {}
        positive_signals: List[Dict[str, Any]] = []
        negative_signals: List[Dict[str, Any]] = []
        silent_signals: List[Dict[str, Any]] = []
        all_signals: List[Dict[str, Any]] = []
        wave_energies: List[float] = []
        cross_layer_depth = 0
        star_energy_total = 0.0
        sea_energy_total = 0.0
        star_response_types: Dict[str, int] = {}
        sea_response_types: Dict[str, int] = {}
        star_count = len(self._stars)
        sea_count = len(self._seas)
        pack_counter = 0

        _POSITIVE_TYPES = {"amplify", "create", "adopt", "recommend"}
        _NEGATIVE_TYPES = {"reject", "suppress", "complaint", "skepticism"}
        _SILENT_TYPES = {"ignore", "no-action", "low-energy"}

        for record in self._wave_records:
            wave_id = f"w{record.wave_number}"
            wave_total_energy = 0.0
            for aid, resp in (record.agent_responses or {}).items():
                rtype = str(resp.get("response_type", "unknown"))
                response_type_counts[rtype] = response_type_counts.get(rtype, 0) + 1
                try:
                    energy = float(resp.get("outgoing_energy", 0.0) or 0.0)
                except (TypeError, ValueError):
                    energy = 0.0
                wave_total_energy += energy

                is_star = aid in self._stars
                if is_star:
                    star_energy_total += energy
                    star_response_types[rtype] = star_response_types.get(rtype, 0) + 1
                else:
                    sea_energy_total += energy
                    sea_response_types[rtype] = sea_response_types.get(rtype, 0) + 1

                trace_len = int(resp.get("trace_len", 0) or 0)
                if trace_len > cross_layer_depth:
                    cross_layer_depth = trace_len

                signal_text = (
                    resp.get("cluster_reaction")
                    or resp.get("response_content")
                    or resp.get("reasoning")
                    or ""
                )
                pack_counter += 1
                eid = f"ev-{pack_counter}"
                signal_entry = {
                    "evidence_id": eid,
                    "wave_id": wave_id,
                    "agent_id": aid,
                    "agent_type": "star" if is_star else "sea",
                    "response_type": rtype,
                    "outgoing_energy": round(energy, 4),
                    "signal": str(signal_text)[:160],
                }
                all_signals.append(signal_entry)

                if rtype in _POSITIVE_TYPES:
                    positive_signals.append(signal_entry)
                elif rtype in _NEGATIVE_TYPES:
                    negative_signals.append(signal_entry)
                elif rtype in _SILENT_TYPES:
                    silent_signals.append(signal_entry)

            wave_energies.append(round(wave_total_energy, 4))

        # Top signals by outgoing energy, cap at 10
        all_signals.sort(key=lambda x: float(x.get("outgoing_energy", 0.0)), reverse=True)
        key_signals = all_signals[:10]

        stats = {
            "total_waves": total_waves,
            "response_type_counts": response_type_counts,
            "stars_count": star_count,
            "seas_count": sea_count,
        }

        # Build classified summaries (top 5 each)
        positive_signals.sort(key=lambda x: float(x.get("outgoing_energy", 0.0)), reverse=True)
        negative_signals.sort(key=lambda x: float(x.get("outgoing_energy", 0.0)), reverse=True)

        pos_summary = SignalSummary(
            count=len(positive_signals),
            top_signals=positive_signals[:5],
            energy_total=round(sum(s.get("outgoing_energy", 0.0) for s in positive_signals), 4),
        )
        neg_summary = SignalSummary(
            count=len(negative_signals),
            top_signals=negative_signals[:5],
            energy_total=round(sum(s.get("outgoing_energy", 0.0) for s in negative_signals), 4),
        )
        silent_summary = SignalSummary(
            count=len(silent_signals),
            top_signals=silent_signals[:5],
            energy_total=0.0,
        )

        stratified = StratifiedStats(
            star_count=star_count,
            sea_count=sea_count,
            star_energy_total=round(star_energy_total, 4),
            sea_energy_total=round(sea_energy_total, 4),
            star_response_types=star_response_types,
            sea_response_types=sea_response_types,
        )

        peak_wave = 0
        decay_rate = 0.0
        if wave_energies:
            peak_wave = int(max(range(len(wave_energies)), key=lambda i: wave_energies[i]))
            if len(wave_energies) >= 2 and wave_energies[0] > 0:
                nonzero = [e for e in wave_energies if e > 0]
                if len(nonzero) >= 2:
                    decay_rate = round(nonzero[-1] / nonzero[0], 4)

        energy_decay = EnergyDecaySummary(
            wave_energies=wave_energies,
            peak_wave=peak_wave,
            decay_rate=decay_rate,
        )

        pack_id = f"ep-{self._run_id or 'x'}"

        source = (
            f"RIPPLE Phase, Wave 0-{max(0, total_waves - 1)}"
            if total_waves > 0
            else "RIPPLE Phase, Wave 0-0"
        )
        summary = (
            f"{total_waves} waves. +{pos_summary.count} positive, "
            f"-{neg_summary.count} negative, ~{silent_summary.count} silent. "
            f"Star×{star_count}, Sea×{sea_count}. "
            f"Peak wave {peak_wave}."
        )

        # Build V2 pack as dataclass then convert to dict for result
        v2 = EvidencePackV2(
            pack_id=pack_id,
            source=source,
            summary=summary[:500],
            positive_signals=pos_summary,
            negative_signals=neg_summary,
            silent_signals=silent_summary,
            stratified=stratified,
            response_type_distribution=response_type_counts,
            energy_decay=energy_decay,
            cross_layer_depth=cross_layer_depth,
            statistics=stats,
            full_records_ref=self._json_pointer_for_process_key("waves"),
            key_signals=key_signals,
        )

        # Store V2 pack for confidence gate consumption
        self._evidence_pack_v2 = v2

        return {
            "pack_id": v2.pack_id,
            "source": v2.source,
            "summary": v2.summary,
            "key_signals": v2.key_signals,
            "statistics": v2.statistics,
            "full_records_ref": v2.full_records_ref,
            # V2 additions
            "positive_signals": {"count": v2.positive_signals.count, "top_signals": v2.positive_signals.top_signals, "energy_total": v2.positive_signals.energy_total},
            "negative_signals": {"count": v2.negative_signals.count, "top_signals": v2.negative_signals.top_signals, "energy_total": v2.negative_signals.energy_total},
            "silent_signals": {"count": v2.silent_signals.count, "top_signals": v2.silent_signals.top_signals},
            "stratified": {
                "star_count": v2.stratified.star_count,
                "sea_count": v2.stratified.sea_count,
                "star_energy_total": v2.stratified.star_energy_total,
                "sea_energy_total": v2.stratified.sea_energy_total,
                "star_response_types": v2.stratified.star_response_types,
                "sea_response_types": v2.stratified.sea_response_types,
            },
            "response_type_distribution": v2.response_type_distribution,
            "energy_decay": {
                "wave_energies": v2.energy_decay.wave_energies,
                "peak_wave": v2.energy_decay.peak_wave,
                "decay_rate": v2.energy_decay.decay_rate,
            },
            "cross_layer_depth": v2.cross_layer_depth,
        }

    def _build_history_with_window(
        self,
        seed_line: str,
        window_size: int = 5,
    ) -> str:
        """构建带滑动窗口的传播历史。 / Build propagation history with sliding window.

        最近 window_size 轮保留详细记录（含能量），更早的轮次压缩为摘要。
        / Recent window_size waves keep detailed records; older waves compressed to summary.
        """
        lines = [seed_line]

        if not self._wave_records:
            return "\n".join(lines)

        # 压缩摘要：超出窗口的旧记录 / Compressed summary: old records beyond window
        cutoff = len(self._wave_records) - window_size
        if cutoff > 0:
            old_records = self._wave_records[:cutoff]
            summary = self._compress_history(old_records)
            lines.append(summary)

        # 详细记录：最近 window_size 轮 / Detailed records: last window_size waves
        recent_records = self._wave_records[max(0, cutoff) :]
        # 计算每个 Agent 截止到详细窗口起始时的激活次数 / Count activations per agent before detail window
        counts_before: Dict[str, int] = {}
        if cutoff > 0:
            for record in self._wave_records[:cutoff]:
                for act in record.verdict.activated_agents:
                    counts_before[act.agent_id] = counts_before.get(act.agent_id, 0) + 1

        running_counts = dict(counts_before)
        for record in recent_records:
            for act in record.verdict.activated_agents:
                aid = act.agent_id
                running_counts[aid] = running_counts.get(aid, 0) + 1
                resp = record.agent_responses.get(aid, {})
                out_e = resp.get("outgoing_energy", 0.0)
                rtype = resp.get("response_type", "unknown")
                lines.append(
                    f"Wave {record.wave_number}: {aid} → {rtype} "
                    f"(入能量={act.incoming_ripple_energy:.2f}, "
                    f"出能量={out_e:.2f}) "
                    f"[第{running_counts[aid]}次激活]"
                )

        return "\n".join(lines)

    @staticmethod
    def _compress_history(records: List[WaveRecord]) -> str:
        """将多轮 wave 记录压缩为摘要行。 / Compress multiple wave records into a summary line."""
        first_wave = records[0].wave_number
        last_wave = records[-1].wave_number
        agent_counts: Dict[str, int] = {}
        response_counts: Dict[str, int] = {}
        total_out_energy = 0.0

        for record in records:
            for act in record.verdict.activated_agents:
                aid = act.agent_id
                agent_counts[aid] = agent_counts.get(aid, 0) + 1
                resp = record.agent_responses.get(aid, {})
                rtype = resp.get("response_type", "unknown")
                response_counts[rtype] = response_counts.get(rtype, 0) + 1
                total_out_energy += resp.get("outgoing_energy", 0.0)

        agent_parts = [
            f"{aid}×{cnt}"
            for aid, cnt in sorted(agent_counts.items(), key=lambda x: -x[1])
        ]
        resp_parts = [
            f"{rt}({cnt})"
            for rt, cnt in sorted(response_counts.items(), key=lambda x: -x[1])
        ]

        return (
            f"Wave {first_wave}-{last_wave} 摘要: "
            f"激活 {', '.join(agent_parts)}; "
            f"总输出能量={total_out_energy:.1f}; "
            f"响应分布: {', '.join(resp_parts)}"
        )
