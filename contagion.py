"""L7 多角色情绪感染 v0：把 A 的 Appraisal 变成 B 的有界输入。

这一层不新增情绪公式，也不直接改任何角色状态。它只做三件事：

1. 从源角色的 ``Appraisal`` 提取四个跨角色有意义的维度；
2. 按增益 / 上限生成一个四维偏置；
3. 通过 ``EmotionEngine.tick(contagion_bias=...)`` 交给目标角色，
   让目标角色仍用自己的 ``Personality``、trust/love、耐受窗和伤疤计算。

设计边界：

- 只传 ``goal_conduciveness / expectedness / coping_potential /
  social_evaluation``。``goal_relevance`` 是 A 自己的目标，``other_agency``
  跨角色后语义不稳定，v0 不直接搬运。
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
MAX_CONTAGION_CAP = 0.2

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
    """A → B 的单向情绪感染编排器。

    ``tick_pair()`` 先 tick 源角色，再用源角色本轮的**输入 Appraisal**
    生成偏置并 tick 目标角色。源角色的 L4 记忆先验不会隐式传给目标。
    """

    def __init__(self, source: Any, target: Any, *,
                 gain: float = DEFAULT_CONTAGION_GAIN,
                 cap: float = DEFAULT_CONTAGION_CAP) -> None:
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

    def derive(self, source_appraisal: Any) -> Dict[str, float]:
        """按本 pair 的增益 / 上限生成偏置。"""
        return derive_contagion_bias(
            source_appraisal, gain=self.gain, cap=self.cap,
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

        tick_kwargs: Dict[str, Any] = {}
        if appraisal_bias:
            tick_kwargs["appraisal_bias"] = appraisal_bias
        if bias:
            tick_kwargs["contagion_bias"] = bias

        return self.target.tick(
            target_appraisal,
            boundary_violation=boundary_violation,
            now=now,
            trace=trace,
            **tick_kwargs,
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
        source_kwargs: Dict[str, Any] = {}
        if source_appraisal_bias:
            source_kwargs["appraisal_bias"] = source_appraisal_bias

        source_result = self.source.tick(
            source_appraisal,
            boundary_violation=source_boundary_violation,
            now=now,
            trace=trace,
            **source_kwargs,
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


__all__ = [
    "CONTAGION_DIMENSIONS",
    "CONTAGION_NEUTRAL",
    "DEFAULT_CONTAGION_CAP",
    "DEFAULT_CONTAGION_GAIN",
    "MAX_CONTAGION_CAP",
    "ContagionPair",
    "derive_contagion_bias",
    "sanitize_contagion_bias",
]
