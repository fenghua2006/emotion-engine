"""L3.5 元情绪：二阶评估与有界调节偏置。

元情绪不是第 11 个基础情绪通道，而是“以某个 primary emotion 为对象”的
二阶过程：为自己的愤怒羞耻、为自己的快乐内疚、对恐惧感兴趣、为自己的
平静骄傲。

v0 契约
-------
- 只读：读取 EmotionalState / Appraisal / baseline / atmosphere / tolerance
  zone / scar 信息，不写任何基础状态。
- 只观测：引擎只在 ``trace=True`` 且 ``meta_emotion_enabled=True`` 时调用本层；
  结果只进入 StepTrace，不改变 state、tolerance、respond() 或 Personality。
- 默认中性：没有角色 profile 时，不预设“愤怒→羞耻”等固定倾向；标签由
  四维元评估与通用规则决定。角色 profile 只提供稳定偏好。
- 有界：所有输出都有显式范围，便于后续 L5/L6 安全消费。

模块边界：只依赖标准库与 ``core``，不导入 engine / tolerance / memory /
atmosphere / SQLite / 网络库。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from core import (
    Appraisal,
    Channel,
    EmotionalState,
    NEGATIVE_CHANNELS,
    Personality,
    trust_target,
)


# ══════════════════════════════════════════════════════
# 公开常量
# ══════════════════════════════════════════════════════

META_TARGET_CHANNELS: Tuple[Channel, ...] = tuple(Channel)

META_KIND_ORDER: Tuple[str, ...] = (
    "shame", "guilt", "pride", "contempt",
    "compassion", "interest", "fear",
)

META_ACTION_ORDER: Tuple[str, ...] = (
    "accept", "suppress", "ruminate", "express", "repair", "avoid",
)

DISPLAY_RULE_ORDER: Tuple[str, ...] = (
    "amplify", "suppress", "qualify", "neutral",
)

META_DIMENSIONS: Tuple[str, ...] = (
    "goal_conduciveness",
    "social_acceptability",
    "controllability",
    "identity_congruence",
)

# 激活不是“绝对值超过 0.3”，而是相对该通道自己的参考值。
# trust 的参考值是 trust_target(love)，其他通道用人格基线。
ACTIVATION_FELT_MIN = 0.04
ACTIVATION_RAW_MIN = 0.03
ACTIVATION_FELT_SCALE = 0.25

META_KIND_THRESHOLD = 0.18
MAX_KINDS_PER_TARGET = 3
ACTION_BIAS_BONUS = 0.25
MAX_PROFILE_WEIGHT = 2.0

EXPRESSION_GAIN_MIN = 0.5
EXPRESSION_GAIN_MAX = 1.5
LOAD_PER_TARGET = 0.06
LOAD_MAX = 0.20
MAX_REAPPRAISAL_TARGETS = 2


# 通道级“通用用途基线”。这不是角色 profile，也不指定某种情绪必然变成
# 某种元情绪；它只表达各基础情绪在一般情境下的默认有用性。
GOAL_BASE: Dict[Channel, float] = {
    Channel.JOY: 0.62,
    Channel.SADNESS: 0.38,
    Channel.ANGER: 0.50,
    Channel.FEAR: 0.45,
    Channel.DISGUST: 0.42,
    Channel.SURPRISE: 0.55,
    Channel.GUILT: 0.48,
    Channel.LONGING: 0.45,
    Channel.LOVE: 0.62,
    Channel.TRUST: 0.65,
}

# 原情绪效价，只用于把事件的 goal_conduciveness 映射到“这个情绪对我有利吗”。
VALENCE_SIGN: Dict[Channel, float] = {
    Channel.JOY: 1.0,
    Channel.LOVE: 1.0,
    Channel.TRUST: 1.0,
    Channel.SADNESS: -1.0,
    Channel.ANGER: -1.0,
    Channel.FEAR: -1.0,
    Channel.DISGUST: -1.0,
    Channel.GUILT: -1.0,
    Channel.SURPRISE: 0.0,
    Channel.LONGING: 0.0,
}

# 情绪表达的社会规范偏移。负性情绪默认更难被公开接受。
DISPLAY_NORM: Dict[Channel, float] = {
    Channel.JOY: 0.10,
    Channel.LOVE: 0.10,
    Channel.TRUST: 0.10,
    Channel.SADNESS: -0.10,
    Channel.ANGER: -0.10,
    Channel.FEAR: -0.10,
    Channel.DISGUST: -0.10,
    Channel.GUILT: -0.10,
    Channel.SURPRISE: 0.0,
    Channel.LONGING: 0.0,
}

KIND_VALENCE: Dict[str, float] = {
    "shame": -0.8,
    "guilt": -0.7,
    "pride": 0.8,
    "contempt": -0.4,
    "compassion": 0.5,
    "interest": 0.4,
    "fear": -0.7,
}

# 表达增益：正数放大，负数抑制。元情绪不直接改写基础通道。
KIND_GAIN: Dict[str, float] = {
    "pride": 0.35,
    "interest": 0.15,
    "compassion": 0.10,
    "guilt": -0.10,
    "contempt": -0.25,
    "shame": -0.35,
    "fear": -0.40,
}

REAPPRAISAL_BY_KIND: Dict[str, Tuple[str, ...]] = {
    "shame": ("social_acceptability", "identity_congruence"),
    "guilt": ("goal_conduciveness", "social_acceptability"),
    "pride": ("identity_congruence",),
    "contempt": ("social_acceptability", "identity_congruence"),
    "compassion": ("social_acceptability",),
    "interest": ("goal_conduciveness",),
    "fear": ("controllability",),
}

ACTION_TO_DISPLAY: Dict[str, str] = {
    "accept": "amplify",
    "express": "amplify",
    "suppress": "suppress",
    "avoid": "suppress",
    "repair": "qualify",
    "ruminate": "qualify",
}


# ══════════════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════════════

@dataclass(frozen=True)
class MetaAppraisal:
    """对一个 primary channel 的四维元评估。"""

    target: str = ""
    activation: float = 0.0
    goal_conduciveness: float = 0.5
    social_acceptability: float = 0.5
    controllability: float = 0.5
    identity_congruence: float = 0.5

    def to_dict(self) -> Dict[str, float]:
        return {
            "activation": _round4(self.activation),
            "goal_conduciveness": _round4(self.goal_conduciveness),
            "social_acceptability": _round4(self.social_acceptability),
            "controllability": _round4(self.controllability),
            "identity_congruence": _round4(self.identity_congruence),
        }


@dataclass(frozen=True)
class MetaEmotion:
    """以某个 primary channel 为对象的元情绪。"""

    target: str = ""
    kind: str = ""
    valence: float = 0.0
    intensity: float = 0.0
    acceptance: float = 0.0
    control: float = 0.0
    action: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "target": self.target,
            "kind": self.kind,
            "valence": _round4(self.valence),
            "intensity": _round4(self.intensity),
            "acceptance": _round4(self.acceptance),
            "control": _round4(self.control),
            "action": self.action,
        }


@dataclass(frozen=True)
class MetaEmotionProfile:
    """角色稳定的元情绪倾向；不是 Personality，也不写 Personality。

    kind_weights:  {target: {kind: multiplier}}，乘在通用 kind 分数上。
    action_bias:   {target: action}，给指定动作加一个固定小偏置，不是硬覆盖。
    """

    name: str = "neutral"
    kind_weights: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    action_bias: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RegulationBias:
    """有界调节偏置；v0 只输出到 trace，不实际应用。"""

    expression_gain: Mapping[str, float] = field(default_factory=dict)
    display_rule: Mapping[str, str] = field(default_factory=dict)
    regulation_action: Mapping[str, str] = field(default_factory=dict)
    tolerance_load_bias: float = 0.0
    reappraisal_target: Sequence[str] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, object]:
        return {
            "expression_gain": {
                target: _round4(value)
                for target, value in self.expression_gain.items()
            },
            "display_rule": dict(self.display_rule),
            "regulation_action": dict(self.regulation_action),
            "tolerance_load_bias": _round4(self.tolerance_load_bias),
            "reappraisal_target": list(self.reappraisal_target),
        }


@dataclass(frozen=True)
class MetaEmotionState:
    """一次 tick 的瞬时元情绪结果，不持久化到引擎。"""

    meta_appraisal: Mapping[str, MetaAppraisal] = field(default_factory=dict)
    meta_emotions: Sequence[MetaEmotion] = field(default_factory=tuple)
    regulation_bias: RegulationBias = field(default_factory=RegulationBias)

    def to_trace_dict(self) -> Dict[str, object]:
        return {
            "meta_appraisal": {
                target: appraisal.to_dict()
                for target, appraisal in self.meta_appraisal.items()
            },
            "meta_emotions": [
                emotion.to_dict() for emotion in self.meta_emotions
            ],
            "regulation_bias": self.regulation_bias.to_dict(),
        }


NEUTRAL_META_PROFILE = MetaEmotionProfile()


# ══════════════════════════════════════════════════════
# 小工具
# ══════════════════════════════════════════════════════

def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, _finite(value)))


def _clamp01(value: float) -> float:
    return _clamp(value, 0.0, 1.0)


def _finite(value: float, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _round4(value: float) -> float:
    return round(_finite(value), 4)


def _felt(raw: float) -> float:
    """与 EmotionalState.felt() 相同的感知压缩；保持纯函数可测试。"""
    return math.log10(1.0 + max(0.0, _finite(raw)) * 2.0)


def _kind_rank(kind: str) -> int:
    try:
        return META_KIND_ORDER.index(kind)
    except ValueError:
        return len(META_KIND_ORDER)


def _safe_baseline(
    baseline: Mapping[Channel, float], channel: Channel,
) -> float:
    return max(0.0, _finite(baseline.get(channel, 0.0)))


def _activation_for(
    value: float,
    reference: float,
) -> float:
    raw_excess = max(0.0, value - reference)
    felt_diff = max(0.0, _felt(value) - _felt(reference))
    if felt_diff < ACTIVATION_FELT_MIN or raw_excess < ACTIVATION_RAW_MIN:
        return 0.0
    return _clamp01(felt_diff / ACTIVATION_FELT_SCALE)


def _kind_scores(
    *,
    target: Channel,
    activation: float,
    goal: float,
    social: float,
    control: float,
    identity: float,
    self_agency: float,
    support: float,
    surprise_salience: float,
    threat: float,
    mixedness: float,
    openness: float,
) -> Dict[str, float]:
    """由四维缺口/超出量计算七种元情绪标签的连续分数。"""
    goal_low = max(0.0, 0.5 - goal)
    goal_high = max(0.0, goal - 0.5)
    social_low = max(0.0, 0.5 - social)
    social_high = max(0.0, social - 0.5)
    control_low = max(0.0, 0.5 - control)
    control_high = max(0.0, control - 0.5)
    identity_low = max(0.0, 0.5 - identity)
    identity_high = max(0.0, identity - 0.5)

    scores = {
        "shame": activation * (
            1.8 * social_low + 1.2 * identity_low + 0.6 * goal_low
        ) * (0.6 + 0.4 * control_low),

        "guilt": activation * (
            1.8 * goal_low + 0.8 * social_low
        ) * (0.5 + 0.5 * self_agency) * (0.6 + 0.4 * control_low),

        "pride": activation * (
            1.8 * goal_high + 0.9 * social_high + 0.6 * identity_high
        ) * (0.6 + 0.4 * control_high),

        "contempt": activation * (
            1.5 * social_low + 1.0 * identity_low
        ) * (0.6 + 0.4 * control_high),

        "compassion": activation * (
            0.6 + 1.0 * goal_low
        ) * (0.3 + 0.7 * control_high) * (0.5 + 0.5 * support) * (
            1.0 if target in NEGATIVE_CHANNELS else 0.25
        ),

        "interest": activation * (
            0.35 * surprise_salience
            + 0.25 * openness
            + 0.20 * (1.0 - threat)
            + 0.10 * mixedness
        ) * 0.6,

        "fear": activation * (
            1.4 * control_low + 0.4 * goal_low
        ) * (0.5 + 0.5 * threat),
    }
    return {kind: _clamp01(value) for kind, value in scores.items()}


def _action_scores(
    *,
    scores: Mapping[str, float],
    control: float,
    social: float,
) -> Dict[str, float]:
    return {
        "accept": (
            0.55 * scores["compassion"]
            + 0.35 * scores["interest"]
            + 0.20 * scores["pride"]
            + 0.20 * control
        ),
        "suppress": (
            0.75 * scores["shame"]
            + 0.45 * scores["contempt"]
            + 0.40 * scores["fear"]
            + 0.20 * (1.0 - control)
        ),
        "ruminate": (
            0.75 * scores["guilt"]
            + 0.40 * scores["fear"]
            + 0.30 * scores["shame"]
            + 0.20 * (1.0 - control)
        ),
        "express": (
            0.75 * scores["pride"]
            + 0.40 * scores["interest"]
            + 0.25 * social
            + 0.20 * control
        ),
        "repair": (
            0.80 * scores["guilt"]
            + 0.45 * scores["compassion"]
            + 0.20 * control
        ),
        "avoid": (
            0.70 * scores["fear"]
            + 0.40 * scores["contempt"]
            + 0.30 * scores["shame"]
            + 0.20 * (1.0 - control)
        ),
    }


def _choose_action(
    action_scores: Mapping[str, float],
    profile: Optional[MetaEmotionProfile],
    target: str,
) -> str:
    scores = dict(action_scores)
    preferred = None
    if profile is not None:
        preferred = profile.action_bias.get(target)
    if preferred in META_ACTION_ORDER:
        scores[preferred] += ACTION_BIAS_BONUS
    return max(
        META_ACTION_ORDER,
        key=lambda name: (scores[name], -META_ACTION_ORDER.index(name)),
    )


def _profile_weight(
    profile: Optional[MetaEmotionProfile],
    target: str,
    kind: str,
) -> float:
    if profile is None:
        return 1.0
    weights = profile.kind_weights.get(target, {})
    return _clamp(weights.get(kind, 1.0), 0.0, MAX_PROFILE_WEIGHT)


# ══════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════

def meta_appraise(
    *,
    state: EmotionalState,
    baseline: Mapping[Channel, float],
    personality: Personality,
    appraisal: Optional[Appraisal] = None,
    atmosphere: float = 0.0,
    tolerance_zone: str = "regulated",
    overload_ratio: float = 0.0,
    scar_pressure: float = 0.0,
    scar_channels: Sequence[str] = (),
    blends: Sequence[str] = (),
    profile: Optional[MetaEmotionProfile] = None,
) -> Tuple[Dict[str, MetaAppraisal], List[MetaEmotion]]:
    """primary emotion → 四维元评估 → 元情绪标签。

    返回 ``(meta_appraisal, meta_emotions)``。每个激活的 target 都会出现在
    meta_appraisal 中；只有越过 kind 阈值的 target 才会产生 meta_emotions。
    """
    profile = profile or NEUTRAL_META_PROFILE
    atm = _clamp(atmosphere, -1.0, 1.0)
    overload = _clamp01(overload_ratio)

    support = _clamp01(
        0.6 * _finite(state.trust) + 0.4 * min(1.0, max(0.0, _finite(state.love)))
    )
    negative_mean = _clamp01(
        (_finite(state.fear) + _finite(state.anger) + _finite(state.sadness)) / 3.0
    )
    zone_penalty = {
        "regulated": 0.0,
        "hyper": 0.25,
        "hypo": 0.15,
    }.get(tolerance_zone, 0.0)
    surprise_salience = _clamp01(max(
        0.0,
        _felt(state.surprise) - _felt(_safe_baseline(baseline, Channel.SURPRISE)),
    ) / ACTIVATION_FELT_SCALE)
    mixedness = _clamp01(len(blends) / 3.0)
    scar_set = set(scar_channels)

    if appraisal is None:
        event_gc = 0.0
        event_social = 0.0
        event_coping = 0.5
        self_agency = 0.5
    else:
        event_gc = _clamp(appraisal.goal_conduciveness, -1.0, 1.0)
        event_social = _clamp(appraisal.social_evaluation, -1.0, 1.0)
        event_coping = _clamp01(appraisal.coping_potential)
        self_agency = _clamp01(1.0 - appraisal.other_agency)

    threat0 = _clamp01(
        0.5 * negative_mean
        + 0.3 * max(0.0, atm)
        + 0.2 * (1.0 if tolerance_zone == "hyper" else 0.0)
    )

    meta_appraisals: Dict[str, MetaAppraisal] = {}
    meta_emotions: List[MetaEmotion] = []

    for target in META_TARGET_CHANNELS:
        value = max(0.0, _finite(getattr(state, target.value, 0.0)))
        if target is Channel.TRUST:
            reference = max(
                0.0, _finite(trust_target(max(0.0, _finite(state.love))))
            )
        else:
            reference = _safe_baseline(baseline, target)

        activation = _activation_for(value, reference)
        if activation <= 0.0:
            continue

        scar_target = (
            _clamp01(scar_pressure) if target.value in scar_set else 0.0
        )
        threat = _clamp01(threat0 + 0.15 * scar_target)

        valence_sign = VALENCE_SIGN[target]
        target_factor = 1.0 if target in (Channel.ANGER, Channel.DISGUST) else 0.5

        goal = _clamp01(
            GOAL_BASE[target]
            + 0.25 * event_gc * valence_sign
            + 0.15 * (event_coping - 0.5) * target_factor
            + (0.10 * (self_agency - 0.5) if target is Channel.GUILT else 0.0)
            - 0.08 * scar_target
        )
        social = _clamp01(
            0.5
            + 0.35 * event_social
            + 0.15 * (support - 0.5)
            - 0.20 * max(0.0, atm)
            + DISPLAY_NORM[target]
            - 0.10 * scar_target
        )
        control = _clamp01(
            0.5
            + 0.25 * (event_coping - 0.5)
            + 0.15 * (support - 0.5)
            - 0.25 * activation
            - 0.20 * overload
            - zone_penalty
            - 0.10 * scar_target
        )
        identity = _clamp01(
            0.5
            + 0.10 * event_social
            - 0.10 * max(0.0, atm)
            + 0.05 * (personality.openness - 0.5)
            - 0.05 * (personality.neuroticism - 0.5)
            + 0.10 * event_gc * valence_sign
        )

        meta_appraisals[target.value] = MetaAppraisal(
            target=target.value,
            activation=activation,
            goal_conduciveness=goal,
            social_acceptability=social,
            controllability=control,
            identity_congruence=identity,
        )

        scores = _kind_scores(
            target=target,
            activation=activation,
            goal=goal,
            social=social,
            control=control,
            identity=identity,
            self_agency=self_agency,
            support=support,
            surprise_salience=surprise_salience,
            threat=threat,
            mixedness=mixedness,
            openness=personality.openness,
        )
        for kind in META_KIND_ORDER:
            scores[kind] = _clamp01(
                scores[kind] * _profile_weight(profile, target.value, kind)
            )

        selected = sorted(
            (
                (kind, score)
                for kind, score in scores.items()
                if score >= META_KIND_THRESHOLD
            ),
            key=lambda item: (-item[1], _kind_rank(item[0])),
        )[:MAX_KINDS_PER_TARGET]
        if not selected:
            continue

        actions = _action_scores(scores=scores, control=control, social=social)
        action = _choose_action(actions, profile, target.value)
        acceptance = _clamp01(
            0.45 * control + 0.35 * social + 0.20 * identity
        )

        for kind, intensity in selected:
            meta_emotions.append(MetaEmotion(
                target=target.value,
                kind=kind,
                valence=KIND_VALENCE[kind],
                intensity=_clamp01(intensity),
                acceptance=acceptance,
                control=control,
                action=action,
            ))

    return meta_appraisals, meta_emotions


def meta_regulate(
    meta_appraisal: Mapping[str, MetaAppraisal],
    meta_emotions: Sequence[MetaEmotion],
) -> RegulationBias:
    """元情绪 → 有界表达 / 调节 / 耐受偏置。

    v0 的返回只写入 StepTrace；调用方不得把它回写到 tolerance、state 或
    respond()。``tolerance_load_bias`` 是“未来可消费”的观测值，不是当前负荷。
    """
    del meta_appraisal  # v0 计算不需要回查；保留签名便于后续层扩展。

    grouped: Dict[str, List[MetaEmotion]] = {}
    for emotion in meta_emotions:
        grouped.setdefault(emotion.target, []).append(emotion)

    expression_gain: Dict[str, float] = {}
    display_rule: Dict[str, str] = {}
    regulation_action: Dict[str, str] = {}
    reappraisal: List[str] = []
    load_bias = 0.0

    for target in META_TARGET_CHANNELS:
        target_name = target.value
        emotions = grouped.get(target_name, [])
        if not emotions:
            continue

        action = emotions[0].action
        gain = 1.0 + sum(
            KIND_GAIN.get(emotion.kind, 0.0) * emotion.intensity
            for emotion in emotions
        )
        expression_gain[target_name] = _clamp(
            gain, EXPRESSION_GAIN_MIN, EXPRESSION_GAIN_MAX,
        )
        display_rule[target_name] = ACTION_TO_DISPLAY.get(action, "neutral")
        regulation_action[target_name] = action

        if action in ("suppress", "ruminate", "avoid"):
            load_bias += LOAD_PER_TARGET * max(
                emotion.intensity for emotion in emotions
            )

        dominant = max(
            emotions,
            key=lambda emotion: (
                emotion.intensity,
                -_kind_rank(emotion.kind),
            ),
        )
        for dimension in REAPPRAISAL_BY_KIND.get(dominant.kind, ()):
            if dimension not in reappraisal:
                reappraisal.append(dimension)

    return RegulationBias(
        expression_gain=expression_gain,
        display_rule=display_rule,
        regulation_action=regulation_action,
        tolerance_load_bias=_clamp(load_bias, 0.0, LOAD_MAX),
        reappraisal_target=tuple(reappraisal[:MAX_REAPPRAISAL_TARGETS]),
    )


def evaluate_meta_emotion(
    *,
    state: EmotionalState,
    baseline: Mapping[Channel, float],
    personality: Personality,
    appraisal: Optional[Appraisal] = None,
    atmosphere: float = 0.0,
    tolerance_zone: str = "regulated",
    overload_ratio: float = 0.0,
    scar_pressure: float = 0.0,
    scar_channels: Sequence[str] = (),
    blends: Sequence[str] = (),
    profile: Optional[MetaEmotionProfile] = None,
) -> MetaEmotionState:
    """引擎唯一入口：一次 tick 的元评估 + 标签 + 有界偏置。"""
    appraisals, emotions = meta_appraise(
        state=state,
        baseline=baseline,
        personality=personality,
        appraisal=appraisal,
        atmosphere=atmosphere,
        tolerance_zone=tolerance_zone,
        overload_ratio=overload_ratio,
        scar_pressure=scar_pressure,
        scar_channels=scar_channels,
        blends=blends,
        profile=profile,
    )
    bias = meta_regulate(appraisals, emotions)
    return MetaEmotionState(
        meta_appraisal=appraisals,
        meta_emotions=tuple(emotions),
        regulation_bias=bias,
    )
