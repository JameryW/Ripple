"""合议庭评审员 Agent。 / Tribunal Agent — expert evaluator for PMF validation.

TribunalAgent 扮演专业角色（市场分析师、魔鬼代言人等），
对产品方案进行结构化评估和辩论。
/ Plays professional roles (market analyst, devil's advocate, etc.)
for structured evaluation and debate of product proposals.
"""

import json
import logging
from typing import Any, Callable, Awaitable, Dict, List, Optional

from ripple.primitives.pmf_models import TribunalOpinion
from ripple.utils.json_parser import parse_json_from_llm

logger = logging.getLogger(__name__)

FALLBACK_SCORES: Dict[str, int] = {}  # Empty fallback


def _safe_int_score(value: Any, default: int = 3) -> int:
    """安全地将评分值转为 int，兼容 float 字符串、dict、None 等异常类型。

    LLM 有时返回非 int 评分（如 "4.0"、{"score": 3, "reason": "..."}、None），
    直接 int(v) 会抛 TypeError/ValueError 导致整个 evaluate/revise 失败。
    """
    if value is None:
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except (ValueError, TypeError):
            logger.warning(
                f"Cannot coerce score string: {value!r}, using default {default}"
            )
            return default
    if isinstance(value, dict):
        nested = value.get("score", value.get("value", default))
        return _safe_int_score(nested, default)
    logger.warning(
        f"Unexpected score type {type(value).__name__}: {value!r}, using default {default}"
    )
    return default


def _safe_str_list(value: Any) -> List[str]:
    """Coerce an audit field value to a list of strings.

    LLM may return: a list of strings, a single string, None, or other types.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None and str(item).strip()]
    return [str(value)]


def _extract_audit_from_llm_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract and validate R6 audit fields from parsed LLM JSON.

    Returns a dict with the 6 R6 audit fields, with defaults for missing ones.
    """
    audit_raw = data.get("audit", {})
    if not isinstance(audit_raw, dict):
        audit_raw = {}
    return {
        "key_evidence": _safe_str_list(audit_raw.get("key_evidence")),
        "uncertainties": _safe_str_list(audit_raw.get("uncertainties")),
        "optimism_audit": _safe_str_list(audit_raw.get("optimism_audit")),
        "overrated_dimensions": _safe_str_list(audit_raw.get("overrated_dimensions")),
        "missing_evidence": _safe_str_list(audit_raw.get("missing_evidence")),
        "recommended_confidence_cap": _safe_str_cap(audit_raw.get("recommended_confidence_cap")),
    }


def _safe_str_cap(value: Any) -> Optional[str]:
    """Normalize a confidence cap value to 'low'|'medium'|'high' or None."""
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in ("low", "medium", "high"):
        return s
    return None


class TribunalAgent:
    """合议庭评审员：专业角色评估器。 / Tribunal Agent: professional role evaluator."""

    def __init__(
        self,
        role: str,
        perspective: str,
        expertise: str,
        llm_caller: Callable[..., Awaitable[str]],
        system_prompt: str = "",
        max_retries: int = 2,
    ):
        self.role = role
        self.perspective = perspective
        self.expertise = expertise
        self._llm_caller = llm_caller
        self._system_prompt = system_prompt
        self._max_retries = max_retries
        # R6: Last parsed audit fields from the most recent LLM response
        self._last_audit: Dict[str, Any] = {}

    async def _call_llm(
        self, user_prompt: str, call_timeout: Optional[float] = None
    ) -> str:
        """Call LLM with optional per-call timeout. / 调用 LLM，支持单次调用超时。"""
        if call_timeout is not None and call_timeout > 0:
            import asyncio

            try:
                return await asyncio.wait_for(
                    self._llm_caller(
                        system_prompt=self._system_prompt,
                        user_prompt=user_prompt,
                    ),
                    timeout=call_timeout,
                )
            except asyncio.TimeoutError:
                raise TimeoutError(
                    f"TribunalAgent {self.role} LLM call timed out after {call_timeout}s"
                )
        return await self._llm_caller(
            system_prompt=self._system_prompt,
            user_prompt=user_prompt,
        )

    async def evaluate(
        self,
        evidence: str,
        dimensions: List[str],
        rubric: str,
        round_number: int = 0,
    ) -> TribunalOpinion:
        """独立评估：基于证据输出评分卡和叙事。 / Independent evaluation: output scorecard and narrative based on evidence."""
        audit_instruction = (
            "\n\nAdditionally, include an \"audit\" section in your JSON response:\n"
            "```json\n"
            "{\"scores\": {...}, \"narrative\": \"...\", "
            "\"audit\": {"
            "\"key_evidence\": [\"evidence item 1\", \"evidence item 2\"], "
            "\"uncertainties\": [\"uncertain area 1\"], "
            "\"optimism_audit\": [\"optimism risk 1\"], "
            "\"overrated_dimensions\": [\"dimension name: reason\"], "
            "\"missing_evidence\": [\"missing data 1\"], "
            "\"recommended_confidence_cap\": \"medium\""
            "}}\n"
            "```\n"
            "- key_evidence: list of the most important evidence items supporting your assessment\n"
            "- uncertainties: areas where you lack confidence in your own assessment\n"
            "- optimism_audit: specific risks that the simulation may be overly optimistic\n"
            "- overrated_dimensions: dimensions where you believe other evaluators may score too high, with reasons\n"
            "- missing_evidence: critical data or evidence that is missing but would change your assessment\n"
            "- recommended_confidence_cap: \"low\", \"medium\", or \"high\" — your recommendation for "
            "the maximum confidence level the final prediction should claim\n"
        )
        prompt = (
            f"You are a {self.role} with expertise in {self.expertise}.\n"
            f"Your evaluation perspective: {self.perspective}\n\n"
            f"## Evidence from simulation\n{evidence}\n\n"
            f"## Scoring rubric\n{rubric}\n\n"
            f"## Dimensions to evaluate\n{', '.join(dimensions)}\n\n"
            'Respond with JSON: {"scores": {dimension: 1-5}, "narrative": "your analysis"}'
            + audit_instruction
        )
        last_error = None
        for attempt in range(1 + self._max_retries):
            try:
                raw = await self._call_llm(prompt)
                data = parse_json_from_llm(raw)
                scores = {
                    k: _safe_int_score(v) for k, v in data.get("scores", {}).items()
                }
                # R6: Extract audit fields from LLM response
                self._last_audit = _extract_audit_from_llm_data(data)
                return TribunalOpinion(
                    member_role=self.role,
                    scores=scores,
                    narrative=data.get("narrative", ""),
                    round_number=round_number,
                )
            except (json.JSONDecodeError, KeyError) as e:
                last_error = e
                logger.warning(
                    f"TribunalAgent {self.role} evaluate attempt {attempt + 1} failed: {e}"
                )

        logger.error(
            f"TribunalAgent {self.role} evaluate failed after retries: {last_error}"
        )
        self._last_audit = {}
        return TribunalOpinion(
            member_role=self.role,
            scores={d: 3 for d in dimensions},
            narrative=f"Evaluation failed: {last_error}",
            round_number=round_number,
        )

    async def challenge(
        self,
        other_opinion: TribunalOpinion,
    ) -> str:
        """质疑其他评审员的观点。 / Challenge another tribunal member's opinion."""
        prompt = (
            f"You are a {self.role}. Your perspective: {self.perspective}\n\n"
            f"Another evaluator ({other_opinion.member_role}) gave this assessment:\n"
            f"Scores: {json.dumps(other_opinion.scores)}\n"
            f"Narrative: {other_opinion.narrative}\n\n"
            'Respond with JSON: {"challenge": "your specific challenge to their assessment"}'
        )
        try:
            raw = await self._call_llm(prompt)
            data = parse_json_from_llm(raw)
            return data.get("challenge", raw)
        except (json.JSONDecodeError, ValueError):
            return raw if isinstance(raw, str) else ""

    async def revise(
        self,
        original_opinion: TribunalOpinion,
        challenges: List[str],
        round_number: int,
    ) -> TribunalOpinion:
        """基于质疑修正立场。 / Revise position based on challenges received."""
        challenges_text = "\n".join(f"- {c}" for c in challenges)
        audit_instruction = (
            "\n\nInclude an \"audit\" section in your JSON response:\n"
            "```json\n"
            "{\"scores\": {...}, \"narrative\": \"...\", "
            "\"audit\": {"
            "\"key_evidence\": [\"evidence item 1\"], "
            "\"uncertainties\": [\"uncertain area 1\"], "
            "\"optimism_audit\": [\"optimism risk 1\"], "
            "\"overrated_dimensions\": [\"dimension name: reason\"], "
            "\"missing_evidence\": [\"missing data 1\"], "
            "\"recommended_confidence_cap\": \"medium\""
            "}}\n"
            "```\n"
            "The audit fields help the system calibrate final prediction quality:\n"
            "- key_evidence: most important evidence items\n"
            "- uncertainties: areas where you lack confidence\n"
            "- optimism_audit: risks of over-optimism\n"
            "- overrated_dimensions: dimensions others may over-score\n"
            "- missing_evidence: critical missing data\n"
            "- recommended_confidence_cap: \"low\", \"medium\", or \"high\" — recommended max confidence\n"
        )
        prompt = (
            f"You are a {self.role}. Your perspective: {self.perspective}\n\n"
            f"Your previous assessment (round {original_opinion.round_number}):\n"
            f"Scores: {json.dumps(original_opinion.scores)}\n"
            f"Narrative: {original_opinion.narrative}\n\n"
            f"Challenges received:\n{challenges_text}\n\n"
            "Revise your assessment. You may keep, raise, or lower scores.\n"
            'Respond with JSON: {"scores": {dimension: 1-5}, "narrative": "revised analysis"}'
            + audit_instruction
        )
        last_error = None
        for attempt in range(1 + self._max_retries):
            try:
                raw = await self._call_llm(prompt)
                data = parse_json_from_llm(raw)
                scores = {
                    k: _safe_int_score(v) for k, v in data.get("scores", {}).items()
                }
                # R6: Extract audit fields from LLM response
                self._last_audit = _extract_audit_from_llm_data(data)
                return TribunalOpinion(
                    member_role=self.role,
                    scores=scores,
                    narrative=data.get("narrative", ""),
                    round_number=round_number,
                )
            except (json.JSONDecodeError, KeyError) as e:
                last_error = e
                logger.warning(
                    f"TribunalAgent {self.role} revise attempt {attempt + 1} failed: {e}"
                )

        self._last_audit = {}
        return TribunalOpinion(
            member_role=self.role,
            scores=dict(original_opinion.scores),
            narrative=f"Revision failed: {last_error}. Keeping original.",
            round_number=round_number,
        )
