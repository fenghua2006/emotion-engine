"""L7 多角色情绪感染：把 A 的 Appraisal 变成 B 的有界输入。

这一层不新增情绪公式，也不直接改任何角色状态。它只做三件事：

1. 从源角色的 ``Appraisal`` 提取四个跨角色有意义的维度；
2. 按增益 / 关系强度 / 上限生成一个四维偏置；
3. 通过 ``EmotionEngine.tick(contagion_bias=...)`` 交给目标角色，
   让目标角色仍用自己的 ``Personality``、trust/love、耐受窗和伤疤计算。

版本边界：

- v0 只做单向 A → B；v0.1 增加 ``tick_bidirectional()``，两个方向的
  偏置都在任何 tick 之前从输入 appraisal 求出，因此没有同 tick 反馈环。
- v0.2 增加 ``relationship_strength()`` / ``auto_strength=True``：从
  双方 trust / love 自动推导强度，仍然 opt-in。
- v0.3 增加 ``ContagionNetwork``：有向边、多跳传播链与 hop 衰减；所有
  偏置同样在 tick 前算完。
- v0.4 增加 ``DirectionalRelationship`` / ``directional=True``：A → B 与
  B → A 分别读取发起角色自己的 trust / love / familiarity / boundaries。
- 只传 ``goal_conduciveness / expectedness / coping_potential /
  social_evaluation``。``goal_relevance`` 是 A 自己的目标，``other_agency``
  跨角色后语义不稳定，不直接搬运。
- 偏置默认单维 ±0.08、绝对上限 ±0.2，避免多角色场景把目标角色的
  Appraisal 推离合法范围。
- 编排器不导入 ``engine.py``，也不写目标角色的记忆或人格。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
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
DEFAULT_RELATIONSHIP_FAMILIARITY_WEIGHT = 0.2
DEFAULT_RELATIONSHIP_BOUNDARY_PENALTY = 0.25
MAX_RELATIONSHIP_LOVE = 2.0
MAX_RELATIONSHIP_FAMILIARITY = 1.0

# 群体传播：每一跳在边权之外再乘一次 hop_decay；max_hops 防止环路放大。
DEFAULT_NETWORK_HOP_DECAY = 0.5
DEFAULT_NETWORK_MAX_HOPS = 3
MAX_NETWORK_MAX_HOPS = 8

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


def _normalized_hop_decay(value: Any) -> float:
    number = _finite_float(value)
    if number is None:
        return DEFAULT_NETWORK_HOP_DECAY
    return max(0.0, min(1.0, number))


def _normalized_max_hops(value: Any) -> int:
    number = _finite_float(value)
    if number is None:
        return DEFAULT_NETWORK_MAX_HOPS
    return max(1, min(MAX_NETWORK_MAX_HOPS, int(number)))


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


def _normalized_three_weights(
    trust_weight: Any,
    love_weight: Any,
    familiarity_weight: Any,
) -> Tuple[float, float, float]:
    trust = _normalized_weight(
        trust_weight, DEFAULT_RELATIONSHIP_TRUST_WEIGHT,
    )
    love = _normalized_weight(
        love_weight, DEFAULT_RELATIONSHIP_LOVE_WEIGHT,
    )
    familiarity = _normalized_weight(
        familiarity_weight, DEFAULT_RELATIONSHIP_FAMILIARITY_WEIGHT,
    )
    total = trust + love + familiarity
    if total <= _EPSILON:
        defaults = (
            DEFAULT_RELATIONSHIP_TRUST_WEIGHT,
            DEFAULT_RELATIONSHIP_LOVE_WEIGHT,
            DEFAULT_RELATIONSHIP_FAMILIARITY_WEIGHT,
        )
        default_total = sum(defaults)
        return (
            defaults[0] / default_total,
            defaults[1] / default_total,
            defaults[2] / default_total,
        )
    return trust / total, love / total, familiarity / total


def _normalized_boundary_penalty(value: Any) -> float:
    number = _finite_float(value)
    if number is None or number < 0.0:
        return DEFAULT_RELATIONSHIP_BOUNDARY_PENALTY
    return number


def _boundary_count(boundaries: Any) -> int:
    if boundaries is None:
        return 0
    if isinstance(boundaries, str):
        return 1 if boundaries.strip() else 0
    if isinstance(boundaries, Mapping):
        return sum(1 for item in boundaries if str(item).strip())
    if isinstance(boundaries, Iterable):
        return sum(1 for item in boundaries if str(item).strip())
    return 0


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


@dataclass(frozen=True)
class DirectionalRelationship:
    """单向关系快照：A 对 B 的 trust / love / familiarity / boundaries。

    这不是情绪状态，也不写回引擎；``ContagionPair`` 只在计算 A → B
    的感染强度时读取它。历史变化通过调用方更新这些字段体现。
    """

    trust: float = 0.0
    love: float = 0.0
    familiarity: float = 0.0
    boundaries: Tuple[str, ...] = ()


def directional_relationship_strength(
    relationship: Any,
    *,
    trust_weight: float = DEFAULT_RELATIONSHIP_TRUST_WEIGHT,
    love_weight: float = DEFAULT_RELATIONSHIP_LOVE_WEIGHT,
    familiarity_weight: float = DEFAULT_RELATIONSHIP_FAMILIARITY_WEIGHT,
    boundary_penalty: float = DEFAULT_RELATIONSHIP_BOUNDARY_PENALTY,
) -> float:
    """把单个方向的 relation snapshot 映射成 ``[0, 1]`` 强度。

    ``trust`` / ``familiarity`` 按 ``[0,1]``，``love`` 按 ``[0,2]``
    归一化；三者权重先归一化。每条非空 boundary 以
    ``1 / (1 + penalty * count)`` 衰减，表达边界越多、感染越弱。

    ``relationship`` 可以是任意带 ``trust`` / ``love`` /
    ``familiarity`` / ``boundaries`` 属性的对象（包括
    ``memory.RelationshipState``），也可以是同名字段的 Mapping。
    """
    if relationship is None:
        return 0.0

    if isinstance(relationship, Mapping):
        def get(name: str) -> Any:
            return relationship.get(name)
    else:
        def get(name: str) -> Any:
            return getattr(relationship, name, None)

    trust_value = _finite_float(get("trust"))
    love_value = _finite_float(get("love"))
    familiarity_value = _finite_float(get("familiarity"))

    trust_value = (
        0.0 if trust_value is None
        else max(0.0, min(1.0, trust_value))
    )
    love_value = (
        0.0 if love_value is None
        else max(0.0, min(MAX_RELATIONSHIP_LOVE, love_value))
        / MAX_RELATIONSHIP_LOVE
    )
    familiarity_value = (
        0.0 if familiarity_value is None
        else max(
            0.0,
            min(MAX_RELATIONSHIP_FAMILIARITY, familiarity_value),
        )
        / MAX_RELATIONSHIP_FAMILIARITY
    )

    trust_weight, love_weight, familiarity_weight = _normalized_three_weights(
        trust_weight, love_weight, familiarity_weight,
    )
    combined = (
        trust_weight * trust_value
        + love_weight * love_value
        + familiarity_weight * familiarity_value
    )
    penalty = _normalized_boundary_penalty(boundary_penalty)
    factor = 1.0 / (1.0 + penalty * _boundary_count(get("boundaries")))
    return max(0.0, min(1.0, combined * factor))


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

    ``tick_pair()`` 先用源角色本轮的**输入 Appraisal** 生成目标偏置，
    再 tick 源角色与目标角色；偏置在 tick 前算完，不会读取源角色刚更新
    后的状态。源角色的 L4 记忆先验不会隐式传给目标。

    ``strength`` 是关系强度，乘在 ``gain`` 上：``effective_gain =
    gain * strength``。它同时缩放单向与双向模式；``strength=0`` 等价于
    关闭感染。

    ``auto_strength=True`` 时会从双方引擎当前 ``state.trust`` /
    ``state.love`` 自动推导 strength：两个角色各自的
    ``relationship_strength()`` 取平均。自动模式会在每次 ``derive()``
    前刷新，因此关系变化会反映到下一次感染；显式 ``strength`` 仍适用于
    不需要自动推导的场景。

    ``directional=True``（L7.4）时不再使用单一 strength，而是分别计算
    ``source -> target`` 与 ``target -> source``：每个方向读取该方向
    发起角色自己的 relation snapshot（trust / love / familiarity /
    boundaries）。单向模式只用 source -> target；双向模式两个方向各用
    自己的强度。自动关系模型与方向模型互斥。
    """

    def __init__(self, source: Any, target: Any, *,
                 gain: float = DEFAULT_CONTAGION_GAIN,
                 cap: float = DEFAULT_CONTAGION_CAP,
                 strength: float = DEFAULT_CONTAGION_STRENGTH,
                 auto_strength: bool = False,
                 directional: bool = False,
                 source_relationship: Any = None,
                 target_relationship: Any = None,
                 trust_weight: float = DEFAULT_RELATIONSHIP_TRUST_WEIGHT,
                 love_weight: float = DEFAULT_RELATIONSHIP_LOVE_WEIGHT,
                 familiarity_weight: float = (
                     DEFAULT_RELATIONSHIP_FAMILIARITY_WEIGHT
                 ),
                 boundary_penalty: float = (
                     DEFAULT_RELATIONSHIP_BOUNDARY_PENALTY
                 ),
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

        if directional and auto_strength:
            raise ValueError(
                "directional and auto_strength are mutually exclusive"
            )

        self.source = source
        self.target = target
        self.gain = _normalized_gain(gain)
        self.cap = _normalized_cap(cap)
        self.strength = _normalized_strength(strength)
        self.auto_strength = bool(auto_strength)
        self.directional = bool(directional)
        self.source_relationship = source_relationship
        self.target_relationship = target_relationship
        self.trust_weight, self.love_weight = _normalized_weights(
            trust_weight, love_weight,
        )
        (
            self.directional_trust_weight,
            self.directional_love_weight,
            self.familiarity_weight,
        ) = _normalized_three_weights(
            trust_weight, love_weight, familiarity_weight,
        )
        self.boundary_penalty = _normalized_boundary_penalty(
            boundary_penalty,
        )
        self.source_to_target_strength = self.strength
        self.target_to_source_strength = self.strength
        if self.auto_strength or self.directional:
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

    @classmethod
    def from_directional_relationship(
        cls,
        source: Any,
        target: Any,
        *,
        gain: float = DEFAULT_CONTAGION_GAIN,
        cap: float = DEFAULT_CONTAGION_CAP,
        source_relationship: Any = None,
        target_relationship: Any = None,
        trust_weight: float = DEFAULT_RELATIONSHIP_TRUST_WEIGHT,
        love_weight: float = DEFAULT_RELATIONSHIP_LOVE_WEIGHT,
        familiarity_weight: float = DEFAULT_RELATIONSHIP_FAMILIARITY_WEIGHT,
        boundary_penalty: float = DEFAULT_RELATIONSHIP_BOUNDARY_PENALTY,
    ) -> "ContagionPair":
        """创建方向性关系模型；A→B 与 B→A 各自独立。"""
        return cls(
            source,
            target,
            gain=gain,
            cap=cap,
            directional=True,
            source_relationship=source_relationship,
            target_relationship=target_relationship,
            trust_weight=trust_weight,
            love_weight=love_weight,
            familiarity_weight=familiarity_weight,
            boundary_penalty=boundary_penalty,
        )

    @property
    def effective_gain(self) -> float:
        """关系强度缩放后的实际增益（方向模型下为双方平均值）。"""
        return self.effective_gain_for(self.strength)

    def effective_gain_for(self, strength: Any) -> float:
        """给定关系强度时的实际增益。"""
        return _normalized_gain(
            self.gain * _normalized_strength(strength),
        )

    def engine_relationship_strength(self, engine: Any) -> float:
        """读取单个引擎当前 trust / love 得到的关系强度。"""
        state = getattr(engine, "state", None)
        return relationship_strength(
            getattr(state, "trust", None),
            getattr(state, "love", None),
            trust_weight=self.trust_weight,
            love_weight=self.love_weight,
        )

    @staticmethod
    def _default_relationship(engine: Any) -> Any:
        relationship = getattr(engine, "relationship", None)
        if relationship is not None:
            return relationship
        return getattr(engine, "state", None)

    def engine_directional_strength(self, engine: Any,
                                    relationship: Any = None) -> float:
        """读取某个方向发起角色的关系快照，得到该方向强度。"""
        if relationship is None:
            relationship = self._default_relationship(engine)
        return directional_relationship_strength(
            relationship,
            trust_weight=self.directional_trust_weight,
            love_weight=self.directional_love_weight,
            familiarity_weight=self.familiarity_weight,
            boundary_penalty=self.boundary_penalty,
        )

    def derive_relationship_strength(self) -> float:
        """双方关系强度的平均值；任一方缺失时按 0 计算。"""
        source_strength = self.engine_relationship_strength(self.source)
        target_strength = self.engine_relationship_strength(self.target)
        return (source_strength + target_strength) / 2.0

    def refresh_relationship_strength(self) -> float:
        """重新计算双方强度；方向模型下同时刷新两个方向。"""
        if self.directional:
            self.source_to_target_strength = self.engine_directional_strength(
                self.source, self.source_relationship,
            )
            self.target_to_source_strength = self.engine_directional_strength(
                self.target, self.target_relationship,
            )
            self.strength = (
                self.source_to_target_strength + self.target_to_source_strength
            ) / 2.0
            return self.strength

        self.strength = self.derive_relationship_strength()
        self.source_to_target_strength = self.strength
        self.target_to_source_strength = self.strength
        return self.strength

    def _derive_with_strength(self, appraisal: Any,
                              strength: float) -> Dict[str, float]:
        return derive_contagion_bias(
            appraisal,
            gain=self.effective_gain_for(strength),
            cap=self.cap,
        )

    def derive(self, source_appraisal: Any) -> Dict[str, float]:
        """按 source -> target 方向生成偏置。

        自动关系模式先刷新 strength；方向模型先刷新两个方向的强度，再用
        source -> target 的强度。两种模式都不会在同一 tick 读对方输出。
        """
        if self.auto_strength or self.directional:
            self.refresh_relationship_strength()
        strength = (
            self.source_to_target_strength
            if self.directional else self.strength
        )
        return self._derive_with_strength(source_appraisal, strength)

    def derive_reverse(self, target_appraisal: Any) -> Dict[str, float]:
        """按 target -> source 方向生成偏置（双向模式的 source 侧）。"""
        if self.auto_strength or self.directional:
            self.refresh_relationship_strength()
        strength = (
            self.target_to_source_strength
            if self.directional else self.strength
        )
        return self._derive_with_strength(target_appraisal, strength)

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
        """先 tick 源，再 tick 目标；返回 ``(source_result, target_result)``。

        目标偏置在任何 tick 之前计算，因此自动 / 方向关系强度不会读到
        源角色刚更新后的 trust / love；这与双向模式保持同一口径。
        """
        target_bias: Dict[str, float] = {}
        if source_appraisal is not None and target_appraisal is not None:
            target_bias = self.derive(source_appraisal)

        source_result = self._tick_engine(
            self.source,
            source_appraisal,
            contagion_bias=None,
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
            source_bias = self.derive_reverse(target_appraisal)
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


@dataclass(frozen=True)
class ContagionEdge:
    """有向感染边：``source -> target``。

    ``strength`` 是这条边的关系强度，归一化到 ``[0, 1]``。网络传播时
    第一跳使用边权本身，之后每一跳再乘 ``hop_decay``。
    """

    source: str
    target: str
    strength: float = DEFAULT_CONTAGION_STRENGTH

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source:
            raise ValueError("contagion edge source must be a non-empty string")
        if not isinstance(self.target, str) or not self.target:
            raise ValueError("contagion edge target must be a non-empty string")
        if self.source == self.target:
            raise ValueError("contagion edge cannot be a self-loop")
        object.__setattr__(
            self, "strength", _normalized_strength(self.strength),
        )


class ContagionNetwork:
    """多角色有向感染网络：直接边 + 多跳传播 + 距离衰减。

    每个节点是独立的 ``EmotionEngine``。``derive()`` 先为每个有输入
    ``Appraisal`` 的节点生成自己的四维偏置，再沿有向边传播：

    - 第一跳权重 = ``edge.strength``；
    - 之后每一跳再乘 ``hop_decay``；
    - 同一 (origin, target) 只保留最强路径，避免环路重复累加；
    - 所有 origin 的贡献汇总后，每个维度重新 clamp 到 ``±cap``。

    所有偏置都在任何 ``tick()`` 之前算完，因此同一 tick 内没有反馈环；
    传播链的“下一轮”效果由下一次 tick 重新评估实现。
    """

    def __init__(self, engines: Mapping[str, Any],
                 edges: Optional[Iterable[Any]] = None, *,
                 gain: float = DEFAULT_CONTAGION_GAIN,
                 cap: float = DEFAULT_CONTAGION_CAP,
                 hop_decay: float = DEFAULT_NETWORK_HOP_DECAY,
                 max_hops: int = DEFAULT_NETWORK_MAX_HOPS) -> None:
        if not isinstance(engines, Mapping) or not engines:
            raise ValueError("contagion network requires a non-empty engines mapping")

        self.engines: Dict[str, Any] = dict(engines)
        for name, engine in self.engines.items():
            if not isinstance(name, str) or not name:
                raise ValueError("contagion network node names must be non-empty strings")
            if not callable(getattr(engine, "tick", None)):
                raise TypeError(f"contagion network node {name!r} must provide tick()")

        self.node_order: Tuple[str, ...] = tuple(self.engines)
        names = self.node_order
        for index, name in enumerate(names):
            for other_name in names[index + 1:]:
                source = self.engines[name]
                target = self.engines[other_name]
                if source is target:
                    raise ValueError(
                        f"contagion network nodes {name!r} and {other_name!r} "
                        "must be different engines"
                    )
                for attribute in _ISOLATED_ATTRIBUTES:
                    if not hasattr(source, attribute) or not hasattr(target, attribute):
                        continue
                    if getattr(source, attribute) is getattr(target, attribute):
                        raise ValueError(
                            "contagion network engines must not share "
                            f"{attribute}"
                        )

        self.gain = _normalized_gain(gain)
        self.cap = _normalized_cap(cap)
        self.hop_decay = _normalized_hop_decay(hop_decay)
        self.max_hops = _normalized_max_hops(max_hops)
        self.edges = self._normalize_edges(edges)
        self.outgoing: Dict[str, Tuple[ContagionEdge, ...]] = {
            name: tuple(
                edge for edge in self.edges if edge.source == name
            )
            for name in self.node_order
        }

    def _normalize_edges(self, edges: Optional[Iterable[Any]]
                         ) -> Tuple[ContagionEdge, ...]:
        if edges is None:
            return ()
        if isinstance(edges, (str, bytes)):
            raise TypeError("contagion edges must be an iterable of edges")

        normalized = []
        seen = set()
        for raw in edges:
            if isinstance(raw, ContagionEdge):
                edge = raw
            elif isinstance(raw, (tuple, list)):
                if len(raw) == 2:
                    edge = ContagionEdge(raw[0], raw[1])
                elif len(raw) == 3:
                    edge = ContagionEdge(raw[0], raw[1], raw[2])
                else:
                    raise ValueError(
                        "contagion edge tuples must be (source, target) or "
                        "(source, target, strength)"
                    )
            else:
                raise TypeError("unsupported contagion edge")

            if edge.source not in self.engines:
                raise ValueError(f"unknown contagion edge source: {edge.source!r}")
            if edge.target not in self.engines:
                raise ValueError(f"unknown contagion edge target: {edge.target!r}")
            key = (edge.source, edge.target)
            if key in seen:
                raise ValueError(
                    f"duplicate contagion edge: {edge.source!r} -> {edge.target!r}"
                )
            seen.add(key)
            normalized.append(edge)

        return tuple(sorted(
            normalized, key=lambda edge: (edge.source, edge.target),
        ))

    def derive(self, appraisals: Optional[Mapping[str, Any]] = None
               ) -> Dict[str, Dict[str, float]]:
        """返回每个节点本轮应接收的感染偏置。"""
        if not isinstance(appraisals, Mapping):
            return {}

        origin_biases: Dict[str, Dict[str, float]] = {}
        for name in self.node_order:
            if name not in appraisals:
                continue
            bias = derive_contagion_bias(
                appraisals[name], gain=self.gain, cap=self.cap,
            )
            if bias:
                origin_biases[name] = bias
        if not origin_biases:
            return {}

        combined: Dict[str, Dict[str, float]] = {}
        for origin in self.node_order:
            base_bias = origin_biases.get(origin)
            if not base_bias:
                continue

            best_weight: Dict[str, float] = {}
            frontier: Dict[str, float] = {origin: 1.0}
            for hop in range(1, self.max_hops + 1):
                next_frontier: Dict[str, float] = {}
                for node, weight in frontier.items():
                    for edge in self.outgoing.get(node, ()):
                        path_weight = weight * edge.strength
                        if hop > 1:
                            path_weight *= self.hop_decay
                        if path_weight <= _EPSILON or edge.target == origin:
                            continue
                        if path_weight > best_weight.get(edge.target, 0.0):
                            best_weight[edge.target] = path_weight
                        if path_weight > next_frontier.get(edge.target, 0.0):
                            next_frontier[edge.target] = path_weight
                if not next_frontier:
                    break
                frontier = next_frontier

            for target, weight in best_weight.items():
                bucket = combined.setdefault(target, {})
                for dimension, value in base_bias.items():
                    bucket[dimension] = (
                        bucket.get(dimension, 0.0) + value * weight
                    )

        cleaned: Dict[str, Dict[str, float]] = {}
        for name in self.node_order:
            bucket = combined.get(name)
            if not bucket:
                continue
            node_bias: Dict[str, float] = {}
            for dimension in CONTAGION_DIMENSIONS:
                value = max(
                    -self.cap, min(self.cap, bucket.get(dimension, 0.0)),
                )
                if abs(value) > _EPSILON:
                    node_bias[dimension] = value
            if node_bias:
                cleaned[name] = node_bias
        return cleaned

    def tick(self, appraisals: Optional[Mapping[str, Any]] = None, *,
             boundary_violations: Optional[Mapping[str, bool]] = None,
             appraisal_biases: Optional[Mapping[str, Dict[str, float]]] = None,
             now: Optional[float] = None,
             trace: bool = False) -> Dict[str, Dict]:
        """先算全网偏置，再按节点顺序 tick；返回 ``{node: result}``。"""
        appraisals = appraisals if isinstance(appraisals, Mapping) else {}
        boundary_violations = (
            boundary_violations
            if isinstance(boundary_violations, Mapping) else {}
        )
        appraisal_biases = (
            appraisal_biases
            if isinstance(appraisal_biases, Mapping) else {}
        )
        biases = self.derive(appraisals)

        results: Dict[str, Dict] = {}
        for name in self.node_order:
            tick_kwargs: Dict[str, Any] = {}
            appraisal_bias = appraisal_biases.get(name)
            if appraisal_bias:
                tick_kwargs["appraisal_bias"] = appraisal_bias
            contagion_bias = biases.get(name)
            if contagion_bias:
                tick_kwargs["contagion_bias"] = contagion_bias
            results[name] = self.engines[name].tick(
                appraisals.get(name),
                boundary_violation=bool(boundary_violations.get(name, False)),
                now=now,
                trace=trace,
                **tick_kwargs,
            )
        return results


__all__ = [
    "CONTAGION_DIMENSIONS",
    "CONTAGION_NEUTRAL",
    "DEFAULT_CONTAGION_CAP",
    "DEFAULT_CONTAGION_GAIN",
    "DEFAULT_CONTAGION_STRENGTH",
    "DEFAULT_NETWORK_HOP_DECAY",
    "DEFAULT_NETWORK_MAX_HOPS",
    "DEFAULT_RELATIONSHIP_BOUNDARY_PENALTY",
    "DEFAULT_RELATIONSHIP_FAMILIARITY_WEIGHT",
    "DEFAULT_RELATIONSHIP_LOVE_WEIGHT",
    "DEFAULT_RELATIONSHIP_TRUST_WEIGHT",
    "MAX_CONTAGION_CAP",
    "MAX_NETWORK_MAX_HOPS",
    "MAX_RELATIONSHIP_FAMILIARITY",
    "MAX_RELATIONSHIP_LOVE",
    "ContagionEdge",
    "ContagionNetwork",
    "ContagionPair",
    "DirectionalRelationship",
    "derive_contagion_bias",
    "directional_relationship_strength",
    "relationship_strength",
    "sanitize_contagion_bias",
]
