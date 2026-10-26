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
from typing import Dict, Mapping, NamedTuple, Optional, Sequence

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

# ── L5 多维容量 ──────────────────────────────────────
CAPACITY_MODEL_SCALAR = "scalar"
CAPACITY_MODEL_MULTI = "multi"
VALID_CAPACITY_MODELS = (CAPACITY_MODEL_MULTI, CAPACITY_MODEL_SCALAR)

DOMAIN_NAMES = ("anger", "fear", "intimacy", "criticism")
DOMAIN_WEIGHTS: Dict[str, float] = {
    "anger": 0.25,
    "fear": 0.30,
    "intimacy": 0.25,
    "criticism": 0.20,
}

# 伤疤按事件类型压缩对应域；同域取 max，不跨域叠加。
SCAR_DOMAIN_MAP: Dict[str, tuple] = {
    "anger": ("social_hurt", "criticism"),
    "fear": ("social_hurt", "cold_shoulder"),
    "intimacy": ("cold_shoulder", "self_blame"),
    "criticism": ("criticism", "social_hurt"),
}

META_LOAD_HARD_CAP = 0.20


class DomainCoefficients(NamedTuple):
    base: float
    trust: float
    love: float
    neuro: float
    atmosphere_positive: float
    atmosphere_negative: float


# 加权平均对齐旧标量公式的系数：base .75 / trust .28 / love .1375 /
# neuro .205 / atm+ .102 / atm- .0495。域内仍保留各自的特化。
DOMAIN_CAPACITY_COEFFICIENTS: Dict[str, DomainCoefficients] = {
    "anger": DomainCoefficients(
        0.75, 0.20, 0.10, 0.20, 0.08, 0.040,
    ),
    "fear": DomainCoefficients(
        0.75, 0.36, 0.10, 0.25, 0.12, 0.060,
    ),
    "intimacy": DomainCoefficients(
        0.75, 0.20, 0.25, 0.16, 0.08, 0.050,
    ),
    "criticism": DomainCoefficients(
        0.75, 0.36, 0.10, 0.20, 0.13, 0.045,
    ),
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _finite(value: float, default: float = 0.0) -> float:
    """把非有限/非数值输入归到安全默认值。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _resolve_capacity_model(candidate: Optional[str],
                            fallback: Optional[str]) -> str:
    """选择合法容量模型；优先 candidate，再 fallback，最后 multi。"""
    if candidate in VALID_CAPACITY_MODELS:
        return str(candidate)
    if fallback in VALID_CAPACITY_MODELS:
        return str(fallback)
    return CAPACITY_MODEL_MULTI


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

    # L5: 默认多域加权容量；"scalar" 复现 pre-L5 标量模型。
    capacity_model: str = CAPACITY_MODEL_MULTI
    # 多域模式下伤疤对单个域的压缩系数；scalar 仍使用 scar_load。
    domain_scar_load: float = 0.20
    # meta 情绪调节负荷的硬上限；只能调低，不能高于 0.20。
    meta_load_cap: float = 0.20


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
    # L5 观测字段：当前使用的容量模型、四域容量、本 tick 实际 meta 负荷。
    capacity_model: Optional[str] = None
    capacity_by_domain: Dict[str, float] = field(default_factory=dict)
    meta_load_bias: float = 0.0

    def __post_init__(self) -> None:
        if self.config is None:
            self.config = ToleranceConfig()
        self.capacity_model = _resolve_capacity_model(
            self.capacity_model, self.config.capacity_model,
        )

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
               scar_pressure_by_tag: Optional[Mapping[str, float]] = None,
               meta_load_bias: float = 0.0,
               elapsed_hours: float = 0.0,
               capacity_model: Optional[str] = None) -> str:
        """更新负荷、容量与模式；返回当前 zone。

        elapsed_hours 用于负荷自然恢复。传入 0 表示“不衰减”，
        适合连续 tick 或测试；正常 tick 传真实间隔即可。
        """
        model = _resolve_capacity_model(
            capacity_model or self.capacity_model,
            self.config.capacity_model,
        )
        self.capacity_model = model

        if model == CAPACITY_MODEL_SCALAR:
            # pre-L5 路径：容量公式逐字保留，忽略多域伤疤与 meta 负荷。
            self.capacity = self._compute_capacity(
                trust=trust,
                love=love,
                neuroticism=neuroticism,
                atmosphere=atmosphere,
                scar_pressure=scar_pressure,
            )
            self.capacity_by_domain = {}
        else:
            self.capacity, self.capacity_by_domain = (
                self._compute_capacity_domains(
                    trust=trust,
                    love=love,
                    neuroticism=neuroticism,
                    atmosphere=atmosphere,
                    scar_pressure=scar_pressure,
                    scar_pressure_by_tag=scar_pressure_by_tag,
                )
            )

        instant_load = self._compute_instant_load(
            state=state,
            baseline=baseline,
            trust=trust,
            trust_target_value=trust_target_value,
            shock_channels=shock_channels,
            boundary_violation=boundary_violation,
        )

        if model == CAPACITY_MODEL_MULTI:
            cap = min(
                META_LOAD_HARD_CAP,
                max(0.0, _finite(self.config.meta_load_cap,
                                 META_LOAD_HARD_CAP)),
            )
            self.meta_load_bias = _clamp(
                _finite(meta_load_bias, 0.0), 0.0, cap,
            )
            if self.meta_load_bias:
                instant_load += self.meta_load_bias
        else:
            self.meta_load_bias = 0.0

        # 包络式负荷：当前事件立即抬高负荷；没有新事件时按 4h 半衰期回落。
        # 不是把上一帧 load 直接丢掉，也不是无限累加。
        elapsed = max(0.0, _finite(elapsed_hours, 0.0))
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

    def _compute_capacity_domains(
        self, *, trust: float, love: float, neuroticism: float,
        atmosphere: float, scar_pressure: float,
        scar_pressure_by_tag: Optional[Mapping[str, float]],
    ) -> tuple:
        """四域容量 + 加权有效容量。

        每域先独立 clamp，再做加权平均；这样单个域触底不会把
        其它域也拖到最低值。domain_scar_load 只用于多域模型。
        """
        trust_value = _clamp(_finite(trust, 0.0), 0.0, 1.0)
        love_value = _clamp(_finite(love, 0.0), 0.0, 1.0)
        neuroticism_value = _clamp(_finite(neuroticism, 0.5), 0.0, 1.0)
        atmosphere_value = _finite(atmosphere, 0.0)
        atmosphere_positive = max(0.0, atmosphere_value)
        atmosphere_negative = max(0.0, -atmosphere_value)
        domain_scar_load = max(0.0, _finite(
            self.config.domain_scar_load, 0.0,
        ))

        capacities: Dict[str, float] = {}
        for domain in DOMAIN_NAMES:
            coefficients = DOMAIN_CAPACITY_COEFFICIENTS[domain]
            scar = self._domain_scar_pressure(
                domain, scar_pressure, scar_pressure_by_tag,
            )
            # config.base_capacity 可整体平移域容量；系数里的 base 作为
            # 域偏移量保留（当前四个域都是 0.75，偏移为 0）。
            configured_base = _finite(
                self.config.base_capacity, coefficients.base,
            )
            capacity = (
                configured_base
                + (coefficients.base - 0.75)
                + coefficients.trust * trust_value
                + coefficients.love * love_value
            )
            capacity *= 1.0 - coefficients.neuro * (
                neuroticism_value - 0.5
            )
            capacity -= coefficients.atmosphere_positive * atmosphere_positive
            capacity += coefficients.atmosphere_negative * atmosphere_negative
            capacity -= domain_scar_load * scar
            capacities[domain] = _clamp(
                capacity,
                self.config.min_capacity,
                self.config.max_capacity,
            )

        effective = sum(
            DOMAIN_WEIGHTS[domain] * capacities[domain]
            for domain in DOMAIN_NAMES
        )
        return _clamp(
            effective, self.config.min_capacity, self.config.max_capacity,
        ), capacities

    def _domain_scar_pressure(
        self, domain: str, scar_pressure: float,
        scar_pressure_by_tag: Optional[Mapping[str, float]],
    ) -> float:
        """返回某个域受到的伤疤压力。

        - 未提供按 tag 的映射时回退到旧 scalar pressure（向后兼容）。
        - 提供映射时只读取该域对应的 tag，取 max，不累加。
        - 空映射/未知 tag 视为无压缩。
        """
        if scar_pressure_by_tag is None:
            return _clamp(_finite(scar_pressure, 0.0), 0.0, 1.0)
        values = []
        for tag in SCAR_DOMAIN_MAP.get(domain, ()):
            raw = scar_pressure_by_tag.get(tag, 0.0)
            values.append(_clamp(_finite(raw, 0.0), 0.0, 1.0))
        return max(values, default=0.0)

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
        data: Dict[str, object] = {
            "load": round(self.load, 4),
            "capacity": round(self.capacity, 4),
            "zone": self.zone,
            "overload_ratio": round(self.overload_ratio, 4),
            "pending_zone": self.pending_zone,
            "pending_ticks": self.pending_ticks,
        }
        # scalar 模式保持 pre-L5 的旧六个键；多域模式才增加观测字段。
        if self.capacity_model == CAPACITY_MODEL_MULTI:
            data["capacity_model"] = CAPACITY_MODEL_MULTI
            data["capacity_by_domain"] = {
                domain: round(float(self.capacity_by_domain[domain]), 4)
                for domain in DOMAIN_NAMES
                if domain in self.capacity_by_domain
            }
            data["meta_load_bias"] = round(float(self.meta_load_bias), 4)
        return data

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, object]],
                  config: Optional[ToleranceConfig] = None) -> "ToleranceState":
        config = config or ToleranceConfig()
        if not data:
            return cls(config=config)

        zone = str(data.get("zone", ZONE_REGULATED))
        if zone not in VALID_ZONES:
            zone = ZONE_REGULATED

        pending = data.get("pending_zone")
        if pending not in VALID_ZONES:
            pending = None

        raw_model = data.get("capacity_model", config.capacity_model)
        model = _resolve_capacity_model(
            None if raw_model is None else str(raw_model),
            config.capacity_model,
        )

        try:
            pending_ticks = max(0, int(data.get("pending_ticks", 0)))
        except (TypeError, ValueError):
            pending_ticks = 0

        load = _clamp(
            _finite(data.get("load", 0.0), 0.0),
            0.0, config.max_load,
        )
        capacity = _clamp(
            _finite(data.get("capacity", 0.75), 0.75),
            config.min_capacity, config.max_capacity,
        )
        overload_ratio = max(
            0.0, _finite(data.get("overload_ratio", 0.0), 0.0),
        )

        capacity_by_domain: Dict[str, float] = {}
        meta_load_bias = 0.0
        if model == CAPACITY_MODEL_MULTI:
            raw_domains = data.get("capacity_by_domain")
            if isinstance(raw_domains, Mapping):
                for domain in DOMAIN_NAMES:
                    if domain not in raw_domains:
                        continue
                    try:
                        value = float(raw_domains[domain])
                    except (TypeError, ValueError):
                        continue
                    if not math.isfinite(value):
                        continue
                    capacity_by_domain[domain] = _clamp(
                        value, config.min_capacity, config.max_capacity,
                    )
            cap = min(
                META_LOAD_HARD_CAP,
                max(0.0, _finite(
                    config.meta_load_cap, META_LOAD_HARD_CAP,
                )),
            )
            meta_load_bias = _clamp(
                _finite(data.get("meta_load_bias", 0.0), 0.0),
                0.0, cap,
            )

        return cls(
            load=load,
            capacity=capacity,
            zone=zone,
            overload_ratio=overload_ratio,
            pending_zone=pending,
            pending_ticks=pending_ticks,
            config=config,
            capacity_model=model,
            capacity_by_domain=capacity_by_domain,
            meta_load_bias=meta_load_bias,
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
    print("  effective capacity:", round(tolerance.capacity, 4),
          "domains:", {domain: round(value, 4)
                       for domain, value in tolerance.capacity_by_domain.items()})

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
    print("  effective capacity:", round(tolerance.capacity, 4),
          "domains:", {domain: round(value, 4)
                       for domain, value in tolerance.capacity_by_domain.items()})


if __name__ == "__main__":
    _demo()
