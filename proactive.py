"""L8.5 主动联系：独立的调度与意愿评估层。

这一层只回答一个问题：

    "现在，这个角色是否应该主动找用户说话？"

它不推进情绪、不写记忆、不写关系，也不调用 LLM。真正的发送动作由
``social.FriendScene`` / 仪表盘执行；本模块只提供可测试、可复现的
确定性判断。

硬边界：

- 不导入 ``engine.py`` / ``social.py``，只通过 duck typing 读取角色状态；
- 不修改 ``EmotionalState`` / ``Personality`` / 关系快照；
- 不进入 ``tick()``，不新增 golden；
- 只依赖标准库。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

DEFAULT_TARGET_ID = "user"
DEFAULT_PROACTIVE_THRESHOLD = 0.35
DEFAULT_MIN_SILENCE_SECONDS = 30 * 60.0
DEFAULT_SILENCE_WINDOW_SECONDS = 3 * 60 * 60.0
DEFAULT_COOLDOWN_SECONDS = 6 * 60 * 60.0
DEFAULT_MAX_PER_DAY = 2
DEFAULT_MAX_UNANSWERED = 2
DEFAULT_QUIET_START_HOUR = 23
DEFAULT_QUIET_END_HOUR = 7

DEFAULT_LONGING_WEIGHT = 0.35
DEFAULT_ABSENCE_WEIGHT = 0.25
DEFAULT_LOVE_WEIGHT = 0.20
DEFAULT_TRUST_WEIGHT = 0.10
DEFAULT_FAMILIARITY_WEIGHT = 0.10
DEFAULT_TENSION_WEIGHT = 0.25
DEFAULT_UNANSWERED_WEIGHT = 0.20


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(number):
        return float(default)
    return number


def _optional_hour(value: Any) -> Optional[int]:
    """``None`` 表示关闭安静时段；非法值同样安全地关闭。"""
    if value is None or value == "":
        return None
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    if number < 0 or number > 23:
        return None
    return number


def _in_quiet_hours(hour: int, start: Optional[int],
                    end: Optional[int]) -> bool:
    if start is None or end is None or start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def _date_key(timestamp: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(timestamp))


def _state_value(state: Any, name: str) -> float:
    """读取原始通道值；缺失时返回 0。"""
    return max(0.0, _finite_float(getattr(state, name, 0.0), 0.0))


def _felt_value(state: Any, name: str) -> float:
    """优先读取 ``felt_all()`` 的感知压缩值，退回原始值。"""
    felt_all = getattr(state, "felt_all", None)
    if callable(felt_all):
        try:
            values = felt_all()
            if isinstance(values, Mapping) and name in values:
                return max(0.0, _finite_float(values[name], 0.0))
        except Exception:
            pass
    return _state_value(state, name)


@dataclass
class ProactiveConfig:
    """主动联系的调度参数；所有阈值都可显式覆盖。

    时间单位均为秒。``quiet_start_hour`` / ``quiet_end_hour`` 使用本地
    时间；两者相等或为 ``None`` 时关闭安静时段。
    """

    min_silence_seconds: float = DEFAULT_MIN_SILENCE_SECONDS
    silence_window_seconds: float = DEFAULT_SILENCE_WINDOW_SECONDS
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS
    max_per_day: int = DEFAULT_MAX_PER_DAY
    max_unanswered: int = DEFAULT_MAX_UNANSWERED
    quiet_start_hour: Optional[int] = DEFAULT_QUIET_START_HOUR
    quiet_end_hour: Optional[int] = DEFAULT_QUIET_END_HOUR
    threshold: float = DEFAULT_PROACTIVE_THRESHOLD

    longing_weight: float = DEFAULT_LONGING_WEIGHT
    absence_weight: float = DEFAULT_ABSENCE_WEIGHT
    love_weight: float = DEFAULT_LOVE_WEIGHT
    trust_weight: float = DEFAULT_TRUST_WEIGHT
    familiarity_weight: float = DEFAULT_FAMILIARITY_WEIGHT
    tension_weight: float = DEFAULT_TENSION_WEIGHT
    unanswered_weight: float = DEFAULT_UNANSWERED_WEIGHT

    def __post_init__(self) -> None:
        self.min_silence_seconds = max(
            0.0, _finite_float(self.min_silence_seconds,
                               DEFAULT_MIN_SILENCE_SECONDS),
        )
        self.silence_window_seconds = max(
            self.min_silence_seconds,
            _finite_float(self.silence_window_seconds,
                          DEFAULT_SILENCE_WINDOW_SECONDS),
        )
        if self.silence_window_seconds <= 0:
            self.silence_window_seconds = DEFAULT_SILENCE_WINDOW_SECONDS
        self.cooldown_seconds = max(
            0.0, _finite_float(self.cooldown_seconds,
                               DEFAULT_COOLDOWN_SECONDS),
        )
        self.max_per_day = int(_clamp(
            _finite_float(self.max_per_day, DEFAULT_MAX_PER_DAY), 0, 20,
        ))
        self.max_unanswered = int(_clamp(
            _finite_float(self.max_unanswered, DEFAULT_MAX_UNANSWERED), 0, 10,
        ))
        self.quiet_start_hour = _optional_hour(self.quiet_start_hour)
        self.quiet_end_hour = _optional_hour(self.quiet_end_hour)
        self.threshold = _clamp(
            _finite_float(self.threshold, DEFAULT_PROACTIVE_THRESHOLD),
            0.0, 1.0,
        )
        for name in (
            "longing_weight", "absence_weight", "love_weight",
            "trust_weight", "familiarity_weight", "tension_weight",
            "unanswered_weight",
        ):
            setattr(self, name, max(
                0.0, _finite_float(getattr(self, name), 0.0),
            ))

    @classmethod
    def from_mapping(cls, raw: Any) -> "ProactiveConfig":
        raw = raw if isinstance(raw, Mapping) else {}
        return cls(
            min_silence_seconds=raw.get(
                "min_silence_seconds", DEFAULT_MIN_SILENCE_SECONDS,
            ),
            silence_window_seconds=raw.get(
                "silence_window_seconds", DEFAULT_SILENCE_WINDOW_SECONDS,
            ),
            cooldown_seconds=raw.get(
                "cooldown_seconds", DEFAULT_COOLDOWN_SECONDS,
            ),
            max_per_day=raw.get("max_per_day", DEFAULT_MAX_PER_DAY),
            max_unanswered=raw.get(
                "max_unanswered", DEFAULT_MAX_UNANSWERED,
            ),
            quiet_start_hour=raw.get(
                "quiet_start_hour", DEFAULT_QUIET_START_HOUR,
            ),
            quiet_end_hour=raw.get(
                "quiet_end_hour", DEFAULT_QUIET_END_HOUR,
            ),
            threshold=raw.get("threshold", DEFAULT_PROACTIVE_THRESHOLD),
            longing_weight=raw.get(
                "longing_weight", DEFAULT_LONGING_WEIGHT,
            ),
            absence_weight=raw.get(
                "absence_weight", DEFAULT_ABSENCE_WEIGHT,
            ),
            love_weight=raw.get("love_weight", DEFAULT_LOVE_WEIGHT),
            trust_weight=raw.get("trust_weight", DEFAULT_TRUST_WEIGHT),
            familiarity_weight=raw.get(
                "familiarity_weight", DEFAULT_FAMILIARITY_WEIGHT,
            ),
            tension_weight=raw.get(
                "tension_weight", DEFAULT_TENSION_WEIGHT,
            ),
            unanswered_weight=raw.get(
                "unanswered_weight", DEFAULT_UNANSWERED_WEIGHT,
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "min_silence_seconds": round(self.min_silence_seconds, 6),
            "silence_window_seconds": round(
                self.silence_window_seconds, 6,
            ),
            "cooldown_seconds": round(self.cooldown_seconds, 6),
            "max_per_day": self.max_per_day,
            "max_unanswered": self.max_unanswered,
            "quiet_start_hour": self.quiet_start_hour,
            "quiet_end_hour": self.quiet_end_hour,
            "threshold": round(self.threshold, 6),
            "longing_weight": round(self.longing_weight, 6),
            "absence_weight": round(self.absence_weight, 6),
            "love_weight": round(self.love_weight, 6),
            "trust_weight": round(self.trust_weight, 6),
            "familiarity_weight": round(self.familiarity_weight, 6),
            "tension_weight": round(self.tension_weight, 6),
            "unanswered_weight": round(self.unanswered_weight, 6),
        }


@dataclass(frozen=True)
class ProactiveAssessment:
    """某个角色在当前时刻的主动联系评估。"""

    actor_id: str
    target_id: str
    urgency: float
    eligible: bool
    blocked_reason: str
    reasons: Tuple[str, ...]
    components: Mapping[str, float]
    elapsed_seconds: float
    pending: int
    now: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "target_id": self.target_id,
            "urgency": round(self.urgency, 6),
            "eligible": self.eligible,
            "blocked_reason": self.blocked_reason,
            "reasons": list(self.reasons),
            "components": {
                key: round(float(value), 6)
                for key, value in self.components.items()
            },
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "pending": self.pending,
        }


class ProactiveScheduler:
    """基于场景时钟的主动联系调度器。

    状态全部在内存中：上次活动、上次主动联系、每日次数、未回复次数。
    它不持久化，也不写关系；场景重启后调度状态重置。
    """

    def __init__(self, actors: Mapping[str, Any],
                 relationship_fn: Callable[[str, str], Any], *,
                 config: Optional[ProactiveConfig] = None,
                 now: Optional[float] = None) -> None:
        if not isinstance(actors, Mapping) or not actors:
            raise ValueError("actors must be a non-empty mapping")
        self.actors = actors
        self._relationship = relationship_fn
        if config is None:
            self.config = ProactiveConfig()
        elif isinstance(config, ProactiveConfig):
            self.config = config
        else:
            self.config = ProactiveConfig.from_mapping(config)
        timestamp = time.time() if now is None else float(now)
        self.created_at = timestamp
        self.last_activity_at = timestamp
        self.last_user_at: Optional[float] = None
        self.last_speaker: Optional[str] = None
        self.last_actor_at: Dict[str, float] = {}
        self.last_contact_at: Dict[str, Optional[float]] = {
            actor_id: None for actor_id in self.actors
        }
        self.pending: Dict[str, int] = {
            actor_id: 0 for actor_id in self.actors
        }
        self.daily_counts: Dict[str, Dict[str, int]] = {}
        self.total_contacts = 0

    def reset(self, *, now: Optional[float] = None) -> None:
        timestamp = time.time() if now is None else float(now)
        self.created_at = timestamp
        self.last_activity_at = timestamp
        self.last_user_at = None
        self.last_speaker = None
        self.last_actor_at = {}
        self.last_contact_at = {
            actor_id: None for actor_id in self.actors
        }
        self.pending = {actor_id: 0 for actor_id in self.actors}
        self.daily_counts = {}
        self.total_contacts = 0

    def set_config(self, config: Any) -> ProactiveConfig:
        self.config = (
            config if isinstance(config, ProactiveConfig)
            else ProactiveConfig.from_mapping(config)
        )
        return self.config

    def observe_turn(self, source_id: str, *,
                     now: Optional[float] = None) -> None:
        """记录一次场景发言；用户发言会清除所有角色的未回复计数。"""
        timestamp = time.time() if now is None else float(now)
        self.last_activity_at = timestamp
        self.last_speaker = source_id
        if source_id == "user":
            self.last_user_at = timestamp
            for actor_id in self.pending:
                self.pending[actor_id] = 0
        elif source_id in self.actors:
            self.last_actor_at[source_id] = timestamp

    def _daily_count(self, actor_id: str, timestamp: float) -> int:
        return self.daily_counts.get(_date_key(timestamp), {}).get(
            actor_id, 0,
        )

    def _gate_reason(self, actor_id: str, urgency: float,
                     elapsed: float, timestamp: float) -> str:
        config = self.config
        hour = time.localtime(timestamp).tm_hour
        if _in_quiet_hours(
            hour, config.quiet_start_hour, config.quiet_end_hour,
        ):
            return "quiet_hours"
        if elapsed < config.min_silence_seconds:
            return "min_silence"
        last_contact = self.last_contact_at.get(actor_id)
        if (
            last_contact is not None
            and timestamp - last_contact < config.cooldown_seconds
        ):
            return "cooldown"
        if self._daily_count(actor_id, timestamp) >= config.max_per_day:
            return "daily_limit"
        if self.pending.get(actor_id, 0) >= config.max_unanswered:
            return "unanswered_limit"
        if urgency < config.threshold:
            return "below_threshold"
        return ""

    def assess(self, actor_id: str, *,
               now: Optional[float] = None,
               target_id: str = DEFAULT_TARGET_ID) -> ProactiveAssessment:
        if actor_id not in self.actors:
            raise KeyError(f"unknown actor: {actor_id!r}")
        if target_id != "user" and target_id not in self.actors:
            raise KeyError(f"unknown target: {target_id!r}")
        if target_id == actor_id:
            raise ValueError("cannot proactively contact self")
        timestamp = time.time() if now is None else float(now)
        actor = self.actors[actor_id]
        state = getattr(actor, "engine", actor).state
        config = self.config

        longing = _clamp(_felt_value(state, "longing"), 0.0, 1.0)
        love = _clamp(_state_value(state, "love") / 2.0, 0.0, 1.0)
        trust = _clamp(_state_value(state, "trust"), 0.0, 1.0)
        elapsed = max(0.0, timestamp - self.last_activity_at)
        absence = _clamp(
            elapsed / config.silence_window_seconds, 0.0, 1.0,
        )

        familiarity = 0.0
        tension = 0.0
        if target_id != "user":
            rel = self._relationship(actor_id, target_id)
            familiarity = _clamp(
                _finite_float(getattr(rel, "familiarity", 0.0), 0.0),
                0.0, 1.0,
            )
            tension = _clamp(
                _finite_float(getattr(rel, "tension", 0.0), 0.0),
                0.0, 1.0,
            )

        pending = self.pending.get(actor_id, 0)
        components = {
            "longing": config.longing_weight * longing,
            "absence": config.absence_weight * absence,
            "love": config.love_weight * love,
            "trust": config.trust_weight * trust,
            "familiarity": config.familiarity_weight * familiarity,
            "tension": -config.tension_weight * tension,
            "unanswered": -config.unanswered_weight * pending,
        }
        urgency = _clamp(sum(components.values()), 0.0, 1.0)
        reasons = tuple(
            key for key, value in sorted(
                components.items(), key=lambda item: item[1], reverse=True,
            )
            if value > 0.02
        )
        blocked = self._gate_reason(
            actor_id, urgency, elapsed, timestamp,
        )
        return ProactiveAssessment(
            actor_id=actor_id,
            target_id=target_id,
            urgency=urgency,
            eligible=not blocked,
            blocked_reason=blocked,
            reasons=reasons,
            components=components,
            elapsed_seconds=elapsed,
            pending=pending,
            now=timestamp,
        )

    def evaluate(self, *, now: Optional[float] = None,
                 target_id: str = DEFAULT_TARGET_ID
                 ) -> Tuple[ProactiveAssessment, ...]:
        return tuple(
            self.assess(actor_id, now=now, target_id=target_id)
            for actor_id in self.actors
        )

    def eligible(self, *, now: Optional[float] = None,
                 target_id: str = DEFAULT_TARGET_ID
                 ) -> Tuple[ProactiveAssessment, ...]:
        return tuple(
            item for item in self.evaluate(now=now, target_id=target_id)
            if item.eligible
        )

    def choose(self, *, now: Optional[float] = None,
               target_id: str = DEFAULT_TARGET_ID
               ) -> Optional[ProactiveAssessment]:
        candidates = self.eligible(now=now, target_id=target_id)
        if not candidates:
            return None
        return max(candidates, key=lambda item: item.urgency)

    def mark_contact(self, candidate: Any, *,
                     now: Optional[float] = None,
                     target_id: Optional[str] = None) -> Dict[str, Any]:
        """记录一次实际发送；只更新调度状态，不碰引擎或关系。"""
        if isinstance(candidate, ProactiveAssessment):
            actor_id = candidate.actor_id
            target = target_id or candidate.target_id
        else:
            actor_id = str(candidate)
            target = target_id or DEFAULT_TARGET_ID
        if actor_id not in self.actors:
            raise KeyError(f"unknown actor: {actor_id!r}")
        if target != "user" and target not in self.actors:
            raise KeyError(f"unknown target: {target!r}")
        timestamp = time.time() if now is None else float(now)
        self.last_contact_at[actor_id] = timestamp
        self.pending[actor_id] = self.pending.get(actor_id, 0) + 1
        key = _date_key(timestamp)
        day = self.daily_counts.setdefault(key, {})
        day[actor_id] = day.get(actor_id, 0) + 1
        self.total_contacts += 1
        self.last_activity_at = timestamp
        self.last_speaker = actor_id
        self.last_actor_at[actor_id] = timestamp
        return {
            "actor_id": actor_id,
            "target_id": target,
            "at": timestamp,
            "pending": self.pending[actor_id],
            "daily_count": self._daily_count(actor_id, timestamp),
        }

    def snapshot(self, *, now: Optional[float] = None,
                 target_id: str = DEFAULT_TARGET_ID) -> Dict[str, Any]:
        timestamp = time.time() if now is None else float(now)
        assessments = self.evaluate(now=timestamp, target_id=target_id)
        return {
            "config": self.config.to_dict(),
            "now": timestamp,
            "last_activity_at": self.last_activity_at,
            "last_user_at": self.last_user_at,
            "last_speaker": self.last_speaker,
            "pending": dict(self.pending),
            "last_contact_at": dict(self.last_contact_at),
            "daily_counts": {
                key: dict(value) for key, value in self.daily_counts.items()
            },
            "total_contacts": self.total_contacts,
            "actors": {
                item.actor_id: item.to_dict() for item in assessments
            },
        }


__all__ = [
    "DEFAULT_COOLDOWN_SECONDS",
    "DEFAULT_MAX_PER_DAY",
    "DEFAULT_MAX_UNANSWERED",
    "DEFAULT_MIN_SILENCE_SECONDS",
    "DEFAULT_PROACTIVE_THRESHOLD",
    "DEFAULT_SILENCE_WINDOW_SECONDS",
    "DEFAULT_TARGET_ID",
    "ProactiveAssessment",
    "ProactiveConfig",
    "ProactiveScheduler",
]
