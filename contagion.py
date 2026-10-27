"""L7 多角色情绪感染：把 A 的 Appraisal 变成 B 的有界输入。

这一层不新增情绪公式，也不直接改任何角色状态。它只做三件事：

1. 从源角色的 ``Appraisal`` 提取四个跨角色有意义的维度；
2. 按增益 / 关系强度 / 上限生成一个四维偏置；
3. 通过 ``EmotionEngine.tick(contagion_bias=...)`` 交给目标角色，
   让目标角色仍用自己的 ``Personality``、trust/love、耐受窗和伤疤计算。

版本边界：

- v0 只做单向 A → B；v0.1 增加 ``tick_bidirectional()``，两个方向的
  偏置都在任何 tick 之前从输入 appraisal 求出，因此没有同 tick 反馈环。
- 只传 ``goal_conduciveness / expectedness / coping_potential /
  social_evaluation``。``goal_relevance`` 是 A 自己的目标，``other_agency``
  跨角色后语义不稳定，不直接搬运。
- 偏置默认单维 ±0.08、绝对上限 ±0.2，避免多角色场景把目标角色的
  Appraisal 推离合法范围。
- ``ContagionPair`` 只编排两次 ``tick()``；它不导入 ``engine.py``，
  也不写目标角色的记忆或人格。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Dict, Optional, Tuple

from core import APPRAISAL_LIMITS


CONTAGION_DIMENSIONS: Tuple[str, ...] = (
    "goal_conduciveness",
    "expectedness",
    "coping_potential",
    "social_evaluation",
)

# 中性值：偏离这些值才会产生感染偏置。
CONTAGION_NEUTRAL: Dict[str, float] = {
    "goal_conduciveness": 0.0,
    "expectedness": 0.5,
    "coping_potential": 0.5,
    "social_evaluation": 0.0,
}

# expectedness / coping_potential 的合法区间是 0~1，但语义中性点是 0.5；
# 先把偏离归一化到 -1~1，再乘 gain，避免这两个维度只传出一半强度。
_CONTAGION_SCALE: Dict[str, float] = {
    "goal_conduciveness": 1.0,
    "expectedness": 2.0,
    "coping_potential": 2.0,
    "social_evaluation": 1.0,
}

DEFAULT_CONTAGION_GAIN = 0.2
DEFAULT_CONTAGION_CAP = 0.08
DEFAULT_CONTAGION_STRENGTH = 1.0
MAX_CONTAGION_CAP = 0.2

# 自动关系强度：trust ∈ [0,1]、love ∈ [0,2]；先归一化 love，再加权。
DEFAULT_RELATIONSHIP_TRUST_WEIGHT = 0.6
DEFAULT_RELATIONSHIP_LOVE_WEIGHT = 0.4
MAX_RELATIONSHIP_LOVE = 2.0

_EPSILON = 1e-12


def _finite_float(value: Any) -> Optional[float]:
    """只接受有限实数；bool 不是数值。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _normalized_gain(gain: Any) -> float:
    number = _finite_float(gain)
    if number is None:
        return DEFAULT_CONTAGION_GAIN
    return max(0.0, min(1.0, number))


def _normalized_cap(cap: Any) -> float:
    number = _finite_float(cap)
    if number is None:
        return DEFAULT_CONTAGION_CAP
    return max(0.0, min(MAX_CONTAGION_CAP, number))


def _normalized_strength(strength: Any) -> float:
    number = _finite_float(strength)
    if number is None:
        return DEFAULT_CONTAGION_STRENGTH
    return max(0.0, min(1.0, number))


def _normalized_weight(value: Any, default: float) -> float:
    """权重必须是非负有限数；非法值回退到默认权重。"""
    number = _finite_float(value)
    if number is None or number < 0.0:
        return default
    return number


def _normalized_weights(trust_weight: Any,
                        love_weight: Any) -> Tuple[float, float]:
    trust = _normalized_weight(
        trust_weight, DEFAULT_RELATIONSHIP_TRUST_WEIGHT,
    )
    love = _normalized_weight(
        love_weight, DEFAULT_RELATIONSHIP_LOVE_WEIGHT,
    )
    total = trust + love
    if total <= _EPSILON:
        return (
            DEFAULT_RELATIONSHIP_TRUST_WEIGHT,
            DEFAULT_RELATIONSHIP_LOVE_WEIGHT,
        )
    return trust / total, love / total


def relationship_strength(
    trust: Any,
    love: Any,
    *,
    trust_weight: float = DEFAULT_RELATIONSHIP_TRUST_WEIGHT,
    love_weight: float = DEFAULT_RELATIONSHIP_LOVE_WEIGHT,
) -> float:
    """把单个角色的 trust / love 映射成 [0, 1] 的关系强度。

    ``trust`` 按 [0, 1] 截断；``love`` 按 [0, 2] 截断后除以 2。
    权重会先归一化。缺失 / 非有限 / bool 值按 0 处理，避免自动关系
    模型被脏数据意外放大。
    """
    trust_value = _finite_float(trust)
    love_value = _finite_float(love)

    trust_value = (
        0.0 if trust_value is None
        else max(0.0, min(1.0, trust_value))
    )
    love_value = (
        0.0 if love_value is None
        else max(0.0, min(MAX_RELATIONSHIP_LOVE, love_value))
        / MAX_RELATIONSHIP_LOVE
    )

    trust_weight, love_weight = _normalized_weights(
        trust_weight, love_weight,
    )
    combined = trust_weight * trust_value + love_weight * love_value
    return max(0.0, min(1.0, combined))


def derive_contagion_bias(source_appraisal: Any, *,
                          gain: float = DEFAULT_CONTAGION_GAIN,
                          cap: float = DEFAULT_CONTAGION_CAP
                          ) -> Dict[str, float]:
    """把源角色的 Appraisal 转成目标角色的四维有界偏置。

    ``gain`` 限制在 [0, 1]，``cap`` 限制在 [0, MAX_CONTAGION_CAP]。
    中性源返回空字典；缺失 / 非有限维度被跳过。
    """
    if source_appraisal is None:
        return {}

    gain = _normalized_gain(gain)
    cap = _normalized_cap(cap)
    if gain <= 0.0 or cap <= 0.0:
        return {}

    bias: Dict[str, float] = {}
    for name in CONTAGION_DIMENSIONS:
        raw = _finite_float(getattr(source_appraisal, name, None))
        if raw is None:
            continue
        lo, hi = APPRAISAL_LIMITS[name]
        raw = max(lo, min(hi, raw))
        normalized = (raw - CONTAGION_NEUTRAL[name]) * _CONTAGION_SCALE[name]
        delta = max(-cap, min(cap, gain * normalized))
        if abs(delta) > _EPSILON:
            bias[name] = delta
    return bias


def sanitize_contagion_bias(bias: Any, *,
                            cap: float = MAX_CONTAGION_CAP
                            ) -> Optional[Dict[str, float]]:
    """清洗外部传入的感染偏置。

    只保留四个合法维度；拒绝 bool、非数值、NaN、Inf；逐维 clamp 到
    ``±cap``；去掉零值。没有任何合法值时返回 ``None``。
    """
    if not isinstance(bias, Mapping):
        return None

    cap = _normalized_cap(cap)
    if cap <= 0.0:
        return None

    cleaned: Dict[str, float] = {}
    for name in CONTAGION_DIMENSIONS:
        value = _finite_float(bias.get(name))
        if value is None:
            continue
        value = max(-cap, min(cap, value))
        if abs(value) > _EPSILON:
            cleaned[name] = value
    return cleaned or None


_ISOLATED_ATTRIBUTES = ("state", "memory", "scars", "tolerance")


class ContagionPair:
    """A → B 的单向情绪感染编排器，并支持可选的 A ↔ B 双向模式。

    ``tick_pair()`` 先 tick 源角色，再用源角色本轮的**输入 Appraisal**
    生成偏置并 tick 目标角色。源角色的 L4 记忆先验不会隐式传给目标。

    ``strength`` 是关系强度，乘在 ``gain`` 上：``effective_gain =
    gain * strength``。它同时缩放单向与双向模式；``strength=0`` 等价于
    关闭感染。

    ``auto_strength=True`` 时会从双方引擎当前 ``state.trust`` /
    ``state.love`` 自动推导 strength：两个角色各自的
    ``relationship_strength()`` 取平均。自动模式会在每次 ``derive()``
    前刷新，因此关系变化会反映到下一次感染；显式 ``strength`` 仍适用于
    不需要自动推导的场景。
    """

    def __init__(self, source: Any, target: Any, *,
                 gain: float = DEFAULT_CONTAGION_GAIN,
                 cap: float = DEFAULT_CONTAGION_CAP,
                 strength: float = DEFAULT_CONTAGION_STRENGTH,
                 auto_strength: bool = False,
                 trust_weight: float = DEFAULT_RELATIONSHIP_TRUST_WEIGHT,
                 love_weight: float = DEFAULT_RELATIONSHIP_LOVE_WEIGHT
                 ) -> None:
        if source is target:
            raise ValueError("contagion source and target must be different engines")
        if not callable(getattr(source, "tick", None)):
            raise TypeError("contagion source must provide tick()")
        if not callable(getattr(target, "tick", None)):
            raise TypeError("contagion target must provide tick()")

        for attribute in _ISOLATED_ATTRIBUTES:
            if not hasattr(source, attribute) or not hasattr(target, attribute):
                continue
            if getattr(source, attribute) is getattr(target, attribute):
                raise ValueError(
                    f"contagion source and target must not share {attribute}"
                )

        self.source = source
        self.target = target
        self.gain = _normalized_gain(gain)
        self.cap = _normalized_cap(cap)
        self.strength = _normalized_strength(strength)
        self.auto_strength = bool(auto_strength)
        self.trust_weight, self.love_weight = _normalized_weights(
            trust_weight, love_weight,
        )
        if self.auto_strength:
            self.refresh_relationship_strength()

    @classmethod
    def from_relationship(
        cls,
        source: Any,
        target: Any,
        *,
        gain: float = DEFAULT_CONTAGION_GAIN,
        cap: float = DEFAULT_CONTAGION_CAP,
        trust_weight: float = DEFAULT_RELATIONSHIP_TRUST_WEIGHT,
        love_weight: float = DEFAULT_RELATIONSHIP_LOVE_WEIGHT,
    ) -> "ContagionPair":
        """创建自动关系强度模式；等价于 ``auto_strength=True``。"""
        return cls(
            source,
            target,
            gain=gain,
            cap=cap,
            auto_strength=True,
            trust_weight=trust_weight,
            love_weight=love_weight,
        )

    @property
    def effective_gain(self) -> float:
        """关系强度缩放后的实际增益。"""
        return _normalized_gain(self.gain * self.strength)

    def engine_relationship_strength(self, engine: Any) -> float:
        """读取单个引擎当前 trust / love 得到的关系强度。"""
        state = getattr(engine, "state", None)
        return relationship_strength(
            getattr(state, "trust", None),
            getattr(state, "love", None),
            trust_weight=self.trust_weight,
            love_weight=self.love_weight,
        )

    def derive_relationship_strength(self) -> float:
        """双方关系强度的平均值；任一方缺失时按 0 计算。"""
        source_strength = self.engine_relationship_strength(self.source)
        target_strength = self.engine_relationship_strength(self.target)
        return (source_strength + target_strength) / 2.0

    def refresh_relationship_strength(self) -> float:
        """重新从双方 trust / love 计算并写入 ``strength``。"""
        self.strength = self.derive_relationship_strength()
        return self.strength

    def derive(self, source_appraisal: Any) -> Dict[str, float]:
        """按本 pair 的增益 / 关系强度 / 上限生成偏置。

        自动关系模式下先刷新 strength，使本次感染使用最新 trust / love。
        """
        if self.auto_strength:
            self.refresh_relationship_strength()
        return derive_contagion_bias(
            source_appraisal, gain=self.effective_gain, cap=self.cap,
        )

    @staticmethod
    def _tick_engine(engine: Any, appraisal: Any, *,
                     contagion_bias: Optional[Dict[str, float]],
                     boundary_violation: bool,
                     now: Optional[float],
                     trace: bool,
                     appraisal_bias: Optional[Dict[str, float]]
                     ) -> Dict:
        tick_kwargs: Dict[str, Any] = {}
        if appraisal_bias:
            tick_kwargs["appraisal_bias"] = appraisal_bias
        if contagion_bias:
            tick_kwargs["contagion_bias"] = contagion_bias

        return engine.tick(
            appraisal,
            boundary_violation=boundary_violation,
            now=now,
            trace=trace,
            **tick_kwargs,
        )

    def tick_target(self, target_appraisal: Any = None,
                    source_appraisal: Any = None, *,
                    boundary_violation: bool = False,
                    now: Optional[float] = None,
                    trace: bool = False,
                    appraisal_bias: Optional[Dict[str, float]] = None
                    ) -> Dict:
        """只 tick 目标角色；有源 appraisal 时叠加感染偏置。"""
        bias: Dict[str, float] = {}
        if target_appraisal is not None and source_appraisal is not None:
            bias = self.derive(source_appraisal)

        return self._tick_engine(
            self.target,
            target_appraisal,
            contagion_bias=bias,
            boundary_violation=boundary_violation,
            now=now,
            trace=trace,
            appraisal_bias=appraisal_bias,
        )

    def tick_pair(self, source_appraisal: Any = None,
                  target_appraisal: Any = None, *,
                  source_boundary_violation: bool = False,
                  target_boundary_violation: bool = False,
                  source_appraisal_bias: Optional[Dict[str, float]] = None,
                  target_appraisal_bias: Optional[Dict[str, float]] = None,
                  now: Optional[float] = None,
                  trace: bool = False) -> Tuple[Dict, Dict]:
        """先 tick 源，再 tick 目标；返回 ``(source_result, target_result)``。"""
        source_result = self._tick_engine(
            self.source,
            source_appraisal,
            contagion_bias=None,
            boundary_violation=source_boundary_violation,
            now=now,
            trace=trace,
            appraisal_bias=source_appraisal_bias,
        )
        target_result = self.tick_target(
            target_appraisal,
            source_appraisal,
            boundary_violation=target_boundary_violation,
            now=now,
            trace=trace,
            appraisal_bias=target_appraisal_bias,
        )
        return source_result, target_result

    def tick_bidirectional(self, source_appraisal: Any = None,
                           target_appraisal: Any = None, *,
                           source_boundary_violation: bool = False,
                           target_boundary_violation: bool = False,
                           source_appraisal_bias: Optional[Dict[str, float]] = None,
                           target_appraisal_bias: Optional[Dict[str, float]] = None,
                           now: Optional[float] = None,
                           trace: bool = False) -> Tuple[Dict, Dict]:
        """双向 tick：A ↔ B，各自从对方本轮的输入 appraisal 获得偏置。

        两个偏置都在任何 tick 之前计算，因此结果与 tick 顺序无关，也不会
        在同一 tick 内形成反馈回路。缺少任一方的 appraisal 时，两个方向
        都不计算偏置，退化为两次普通 tick。
        """
        source_bias: Dict[str, float] = {}
        target_bias: Dict[str, float] = {}
        if source_appraisal is not None and target_appraisal is not None:
            source_bias = self.derive(target_appraisal)
            target_bias = self.derive(source_appraisal)

        source_result = self._tick_engine(
            self.source,
            source_appraisal,
            contagion_bias=source_bias,
            boundary_violation=source_boundary_violation,
            now=now,
            trace=trace,
            appraisal_bias=source_appraisal_bias,
        )
        target_result = self._tick_engine(
            self.target,
            target_appraisal,
            contagion_bias=target_bias,
            boundary_violation=target_boundary_violation,
            now=now,
            trace=trace,
            appraisal_bias=target_appraisal_bias,
        )
        return source_result, target_result


__all__ = [
    "CONTAGION_DIMENSIONS",
    "CONTAGION_NEUTRAL",
    "DEFAULT_CONTAGION_CAP",
    "DEFAULT_CONTAGION_GAIN",
    "DEFAULT_CONTAGION_STRENGTH",
    "DEFAULT_RELATIONSHIP_LOVE_WEIGHT",
    "DEFAULT_RELATIONSHIP_TRUST_WEIGHT",
    "MAX_CONTAGION_CAP",
    "MAX_RELATIONSHIP_LOVE",
    "ContagionPair",
    "derive_contagion_bias",
    "relationship_strength",
    "sanitize_contagion_bias",
]
