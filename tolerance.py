"""Tolerance window / window of tolerance, revised.

旧版草案把 `load = Σ_all F_i + Σ_all P_i` 当作负荷，会带来三个问题：

1. 慢通道（trust / love）被重复计入；
2. 默认基线总和已经超过 `window_lo`，低阈值基本不可达；
3. “低负荷”被误判成低唤醒/麻木。

这一版改为：

    load = Σ_neg weight_i * max(0, state_i - baseline_i)
         + trust_deficit * w_trust
         + shock_bonus
         + boundary_bonus

`trust / love` 不再进入 load，而是扩展 capacity。
低负荷 = 调节态，不再等于 hypoarousal；hypo 只在**持续过载 + 退缩型情绪占优**时出现。

模式切换带 tick 滞回，避免在两个区间之间抖动。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Optional, Sequence

ZONE_REGULATED = "regulated"
ZONE_HYPER = "hyper"
ZONE_HYPO = "hypo"
VALID_ZONES = (ZONE_REGULATED, ZONE_HYPER, ZONE_HYPO)

NEGATIVE_CHANNELS = ("sadness", "anger", "fear", "disgust", "guilt")

# 负荷只取「负性通道相对基线的偏离」，不取绝对值，也不重复计入慢通道。
LOAD_WEIGHTS: Dict[str, float] = {
    "fear": 0.30,
    "anger": 0.25,
    "sadness": 0.20,
    "disgust": 0.15,
    "guilt": 0.10,
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class ToleranceConfig:
    """可调参数。默认值按“基线附近 load≈0”校准。"""

    base_capacity: float = 0.75
    trust_capacity: float = 0.25
    love_capacity: float = 0.15
    neuroticism_narrowing: float = 0.20

    trust_deficit_load: float = 0.35
    # trust 天然低于 trust_target（信任需要时间生长），
    # 小幅落后不是威胁；只有明显缺口才计入负荷。
    trust_deficit_deadband: float = 0.25
    shock_load: float = 0.10
    boundary_load: float = 0.25
    scar_load: float = 0.15

    recovery_hours: float = 4.0
    hysteresis_ticks: int = 2
    recovery_ratio: float = 0.65

    min_capacity: float = 0.35
    max_capacity: float = 1.80
    max_load: float = 3.00


@dataclass
class ToleranceState:
    """一个 Agent 的当前耐受窗状态。

    这是控制层状态，不是情绪通道；它只改变后续 tick 的增益、
    衰减和交互强度，不直接改写人格。
    """

    load: float = 0.0
    capacity: float = 0.75
    zone: str = ZONE_REGULATED
    overload_ratio: float = 0.0
    pending_zone: Optional[str] = None
    pending_ticks: int = 0
    config: ToleranceConfig = field(default_factory=ToleranceConfig)

    # ── 更新 ──────────────────────────────────────────

    def update(self, *,
               state: Dict[str, float],
               baseline: Dict[str, float],
               trust: float,
               love: float,
               trust_target_value: Optional[float],
               neuroticism: float,
               atmosphere: float = 0.0,
               shock_channels: Sequence[str] = (),
               boundary_violation: bool = False,
               scar_pressure: float = 0.0,
               elapsed_hours: float = 0.0) -> str:
        """更新负荷、容量与模式；返回当前 zone。

        elapsed_hours 用于负荷自然恢复。传入 0 表示“不衰减”，
        适合连续 tick 或测试；正常 tick 传真实间隔即可。
        """
        self.capacity = self._compute_capacity(
            trust=trust,
            love=love,
            neuroticism=neuroticism,
            atmosphere=atmosphere,
            scar_pressure=scar_pressure,
        )

        instant_load = self._compute_instant_load(
            state=state,
            baseline=baseline,
            trust=trust,
            trust_target_value=trust_target_value,
            shock_channels=shock_channels,
            boundary_violation=boundary_violation,
        )

        # 包络式负荷：当前事件立即抬高负荷；没有新事件时按 4h 半衰期回落。
        # 不是把上一帧 load 直接丢掉，也不是无限累加。
        elapsed = max(0.0, elapsed_hours)
        recovery = math.exp(-elapsed / max(0.1, self.config.recovery_hours))
        self.load = _clamp(
            max(instant_load, self.load * recovery),
            0.0,
            self.config.max_load,
        )
        self.overload_ratio = self.load / max(0.05, self.capacity)

        proposed = self._propose_zone(state)
        self._apply_hysteresis(proposed)
        return self.zone

    def _compute_capacity(self, *, trust: float, love: float,
                          neuroticism: float, atmosphere: float,
                          scar_pressure: float) -> float:
        capacity = (
            self.config.base_capacity
            + self.config.trust_capacity * _clamp(trust, 0.0, 1.0)
            + self.config.love_capacity * _clamp(love, 0.0, 1.0)
        )
        # 高神经质收窄窗口；低神经质略宽。
        capacity *= 1.0 - self.config.neuroticism_narrowing * (
            _clamp(neuroticism, 0.0, 1.0) - 0.5
        )
        # 紧绷氛围压缩容量；轻松氛围略微扩展。
        capacity -= 0.10 * max(0.0, atmosphere)
        capacity += 0.05 * max(0.0, -atmosphere)
        # 旧伤疤越多，越容易被同类事件击穿。
        capacity -= self.config.scar_load * _clamp(scar_pressure, 0.0, 1.0)
        return _clamp(capacity, self.config.min_capacity,
                      self.config.max_capacity)

    def _compute_instant_load(self, *, state: Dict[str, float],
                              baseline: Dict[str, float],
                              trust: float,
                              trust_target_value: Optional[float],
                              shock_channels: Sequence[str],
                              boundary_violation: bool) -> float:
        negative = 0.0
        for channel, weight in LOAD_WEIGHTS.items():
            value = float(state.get(channel, 0.0))
            base = float(baseline.get(channel, 0.0))
            negative += weight * max(0.0, value - base)

        trust_gap = 0.0
        if trust_target_value is not None:
            trust_gap = max(
                0.0,
                float(trust_target_value) - float(trust)
                - self.config.trust_deficit_deadband,
            )

        shock_bonus = min(
            0.60,
            self.config.shock_load * len(shock_channels),
        )
        boundary_bonus = self.config.boundary_load if boundary_violation else 0.0

        return negative + self.config.trust_deficit_load * trust_gap + shock_bonus + boundary_bonus

    def _propose_zone(self, state: Dict[str, float]) -> str:
        # 低负荷 → 调节态。这里刻意不设“低负荷 → hypo”；
        # hypo 是过载后的关机，而不是平静。
        if self.overload_ratio >= 1.0:
            fight = (
                float(state.get("anger", 0.0))
                + float(state.get("disgust", 0.0))
                + 0.5 * float(state.get("fear", 0.0))
            )
            withdraw = (
                float(state.get("sadness", 0.0))
                + float(state.get("guilt", 0.0))
                + float(state.get("longing", 0.0))
                + 0.5 * float(state.get("fear", 0.0))
            )
            # 退缩型占优 → 关机；否则战/逃。
            return ZONE_HYPO if withdraw > fight * 1.1 else ZONE_HYPER

        # 已经失调时，要回到足够低才允许回到调节态（滞回）。
        if (self.zone in (ZONE_HYPER, ZONE_HYPO)
                and self.overload_ratio >= self.config.recovery_ratio):
            return self.zone

        return ZONE_REGULATED

    def _apply_hysteresis(self, proposed: str) -> None:
        if proposed == self.zone:
            self.pending_zone = None
            self.pending_ticks = 0
            return

        if proposed == self.pending_zone:
            self.pending_ticks += 1
        else:
            self.pending_zone = proposed
            self.pending_ticks = 1

        if self.pending_ticks >= max(1, self.config.hysteresis_ticks):
            self.zone = proposed
            self.pending_zone = None
            self.pending_ticks = 0

    # ── 控制参数 ──────────────────────────────────────

    @property
    def negative_gain(self) -> float:
        """负性 appraisal 的增益。"""
        return {
            ZONE_HYPER: 1.50,
            ZONE_HYPO: 0.55,
        }.get(self.zone, 1.0)

    @property
    def interaction_scale(self) -> float:
        """负性通道之间的交互强度。"""
        return {
            ZONE_HYPER: 1.30,
            ZONE_HYPO: 0.75,
        }.get(self.zone, 1.0)

    def decay_scale_for(self, channel: str) -> float:
        """负性通道的衰减半衰期倍率；正性通道不受影响。"""
        if channel not in NEGATIVE_CHANNELS:
            return 1.0
        return {
            ZONE_HYPER: 2.00,   # 更难平复
            ZONE_HYPO: 1.50,    # 关机状态下也恢复得慢
        }.get(self.zone, 1.0)

    # ── 序列化 ────────────────────────────────────────

    def to_dict(self) -> Dict[str, object]:
        return {
            "load": round(self.load, 4),
            "capacity": round(self.capacity, 4),
            "zone": self.zone,
            "overload_ratio": round(self.overload_ratio, 4),
            "pending_zone": self.pending_zone,
            "pending_ticks": self.pending_ticks,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, object]],
                  config: Optional[ToleranceConfig] = None) -> "ToleranceState":
        if not data:
            return cls(config=config or ToleranceConfig())

        zone = str(data.get("zone", ZONE_REGULATED))
        if zone not in VALID_ZONES:
            zone = ZONE_REGULATED

        pending = data.get("pending_zone")
        if pending not in VALID_ZONES:
            pending = None

        return cls(
            load=float(data.get("load", 0.0)),
            capacity=float(data.get("capacity", 0.75)),
            zone=zone,
            overload_ratio=float(data.get("overload_ratio", 0.0)),
            pending_zone=pending,
            pending_ticks=int(data.get("pending_ticks", 0)),
            config=config or ToleranceConfig(),
        )


def _demo() -> None:
    state = {
        "joy": 0.3, "sadness": 0.1, "anger": 0.05, "fear": 0.05,
        "love": 0.2, "disgust": 0.0, "surprise": 0.0, "trust": 0.25,
        "longing": 0.0, "guilt": 0.0,
    }
    baseline = dict(state)
    tolerance = ToleranceState()

    for _ in range(3):
        tolerance.update(
            state=state, baseline=baseline, trust=state["trust"],
            love=state["love"], trust_target_value=0.24,
            neuroticism=0.5, atmosphere=-0.3,
        )
    print("calm:", tolerance.to_dict())

    stressed = dict(state)
    stressed.update({"sadness": 0.8, "anger": 0.9, "fear": 0.9,
                     "disgust": 0.7, "guilt": 0.5, "trust": 0.1})
    for _ in range(3):
        tolerance.update(
            state=stressed, baseline=baseline, trust=stressed["trust"],
            love=stressed["love"], trust_target_value=0.5,
            neuroticism=0.5, atmosphere=0.5,
            shock_channels=("anger", "fear", "disgust"),
            boundary_violation=True,
        )
    print("stressed:", tolerance.to_dict())


if __name__ == "__main__":
    _demo()
