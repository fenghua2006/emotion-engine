"""L0 核心情绪线 —— 纯动力学，不依赖记忆 / 耐受 / 伤疤 / 氛围 / LLM。

这一层只回答一个问题：给定人格基线和一次认知评估，情绪通道如何变化。

包含：
  - 10 通道定义与快 / 慢 / 缺失 / 负性分组
  - EmotionalState（原始值、felt 压缩、帧间 delta）
  - 弹性衰减、基线回弹、自然饱和
  - 慢通道累积 / 信任破坏
  - 六维 Appraisal → 通道激活
  - trust / love 门控
  - 通道交互矩阵（L1.1：可选基线相对激活 + 共激活）
  - 离线压缩系数
  - StepTrace：一次 tick 的分阶段快照

上层（engine.py）组合这些纯函数；L1 以后的新层应读取 StepTrace、
提供偏置或控制系数，而不是改写这里的公式。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple


# ══════════════════════════════════════════════════════
# 通道定义
# ══════════════════════════════════════════════════════

class Channel(Enum):
    """情绪通道"""
    JOY      = "joy"       # 喜悦
    SADNESS  = "sadness"   # 悲伤
    ANGER    = "anger"     # 愤怒
    FEAR     = "fear"      # 恐惧
    LOVE     = "love"      # 爱/依恋      ← 慢通道
    DISGUST  = "disgust"   # 厌恶
    SURPRISE = "surprise"  # 惊讶
    TRUST    = "trust"     # 信任          ← 慢通道
    LONGING  = "longing"   # 思念          ← 缺失驱动通道
    GUILT    = "guilt"     # 愧疚          ← 自责驱动通道


FAST_CHANNELS = [Channel.JOY, Channel.SADNESS, Channel.ANGER,
                 Channel.FEAR, Channel.DISGUST, Channel.SURPRISE,
                 Channel.GUILT]
SLOW_CHANNELS = [Channel.LOVE, Channel.TRUST]
ABSENCE_CHANNELS = [Channel.LONGING]  # 思念：离线时上升，在线时消退
NEGATIVE_CHANNELS = [
    Channel.SADNESS, Channel.ANGER, Channel.FEAR,
    Channel.DISGUST, Channel.GUILT,
]
NEGATIVE_CHANNEL_NAMES = {ch.value for ch in NEGATIVE_CHANNELS}

# L2: 慢通道的物理上限。love 是感知压缩后的量，允许超过 1；
# trust 是概率式关系量，保持在 [0, 1]。
MAX_LOVE = 2.0
MAX_TRUST = 1.0


# ══════════════════════════════════════════════════════
# 核心: EmotionalState
# ══════════════════════════════════════════════════════

@dataclass
class EmotionalState:
    """10 通道全部正值。基线 ≠ 0。"""

    joy:       float = 0.3
    sadness:   float = 0.1
    anger:     float = 0.05
    fear:      float = 0.05
    love:      float = 0.2
    disgust:   float = 0.0
    surprise:  float = 0.0
    trust:     float = 0.25
    longing:   float = 0.0    # 思念——从零开始。不见才生。
    guilt:     float = 0.0    # 愧疚——从零开始。不做错事就没有。

    # 对比度用的上一个快照
    _previous: Optional[Dict[str, float]] = None
    # 上次更新的时间戳
    _last_update: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, float]:
        return {
            "joy": self.joy, "sadness": self.sadness,
            "anger": self.anger, "fear": self.fear,
            "love": self.love, "disgust": self.disgust,
            "surprise": self.surprise, "trust": self.trust,
            "longing": self.longing,
            "guilt": self.guilt,
        }

    def clone(self) -> "EmotionalState":
        s = EmotionalState(**self.to_dict())
        s._last_update = self._last_update
        if self._previous:
            s._previous = dict(self._previous)
        return s

    def snapshot_previous(self):
        """在衰减前保存快照，供对比度计算"""
        self._previous = self.to_dict()

    def delta(self, ch: Channel) -> float:
        """返回此通道自上一帧的变化量(对比度)"""
        if self._previous is None:
            return 0.0
        return getattr(self, ch.value) - self._previous.get(ch.value, 0.0)

    def felt(self, ch: Channel) -> float:
        """
        感知压缩：log₁₀ 映射，边际递减。
        raw=0.5 → felt≈0.35, raw=1.0 → felt≈0.5
        raw=2.0 → felt≈0.65, raw=5.0 → felt≈0.85
        值越大增长越慢，没有天花板但在感知上趋于平缓。
        """
        raw = getattr(self, ch.value)
        if raw <= 0:
            return 0.0
        return math.log10(1 + raw * 2)

    def felt_all(self) -> Dict[str, float]:
        """所有通道的感知值"""
        return {ch.value: self.felt(ch) for ch in Channel}


# ══════════════════════════════════════════════════════
# 衰减 & 基线配置
# ══════════════════════════════════════════════════════

# 半衰期（分钟）。积极快，消极慢（基于 Self-Excited Dynamics 2024）
HALF_LIFE: Dict[Channel, float] = {
    Channel.JOY:       90,    # 来得快去得快
    Channel.SADNESS:  180,    # 留存最久
    Channel.ANGER:    120,
    Channel.FEAR:      60,
    Channel.LOVE:     None,   # 慢通道——不自动衰减
    Channel.DISGUST:   90,
    Channel.SURPRISE:  30,    # 消散最快
    Channel.GUILT:   180,     # 愧疚留存很久——和悲伤同级
    Channel.TRUST:    None,   # 慢通道——不自动衰减
}

# 冲击阈值: 当 shock = e^(Δ×2)-1 超过此值 → 触发 shock 标记
# 不同通道有不同的"冲击感知阈值"：
#   joy: 0.3     喜悦容易感受但冲击不大（正面情绪缓冲强）
#   sadness: 0.5 悲伤门槛高但一旦触发就是深刻的
#   anger: 0.4   中等阈值
#   fear: 0.5    高门槛——恐惧冲击是严重事件
#   disgust: 0.3 厌恶敏感但消退快
#   surprise: 0.6 最高门槛——惊讶本身就是冲击本质
# 慢通道 (love/trust) 无冲击——渐进累积
SHOCK_WEIGHT: Dict[Channel, float] = {
    Channel.JOY:       0.3,
    Channel.SADNESS:   0.5,
    Channel.ANGER:     0.4,
    Channel.FEAR:      0.5,
    Channel.DISGUST:   0.3,
    Channel.SURPRISE:  0.6,
    Channel.GUILT:    0.4,
}


# ══════════════════════════════════════════════════════
# 情绪衰减
# ══════════════════════════════════════════════════════

def decay(value: float, half_life_minutes: Optional[float],
          elapsed_minutes: float, baseline: float = 0.0) -> float:
    """
    弹性衰减：偏离基线越远，等效半衰期越短（回弹越快）。
      effective_T½ = T½ / (1 + distance)
      distance = |value - baseline| / baseline
    偏离 0 → 正常衰减，偏离 2×基线 → 半衰期折半，回弹加倍

    慢通道 half_life = None → 不衰减
    """
    if half_life_minutes is None or elapsed_minutes <= 0:
        return value

    # 归一化距离
    if baseline > 0.01:
        distance = abs(value - baseline) / baseline
    else:
        distance = abs(value - baseline)

    # 偏离越大，半衰期越短 → 衰减越快
    effective_hl = half_life_minutes / (1 + distance)

    # 衰减向基线
    return baseline + (value - baseline) * (2 ** (-elapsed_minutes / effective_hl))


def return_to_baseline(current: float, baseline: float,
                       elapsed_minutes: float) -> float:
    """
    基线回弹，允许振荡穿越。
    轻度拉力 + 惯性——可能冲过头再弹回来。
    """
    if elapsed_minutes <= 0:
        return current

    # 拉力随时间和距离增加
    distance = current - baseline
    pull_strength = min(0.3, 0.01 * elapsed_minutes)  # 每 tick 最多拉 30%

    return current - distance * pull_strength


# ══════════════════════════════════════════════════════
# 交互矩阵
# ══════════════════════════════════════════════════════

# v0.4: 移除 sadness/fear→anger——愤怒只来自负面事件+厌恶转化，不被悲伤/恐惧推高
INTERACTION_RULES: Dict[Tuple[Channel, Channel], Tuple[str, float]] = {
    # (通道A, 通道B) → (效应, 强度)
    (Channel.ANGER,   Channel.TRUST):      ("suppress_trust",  0.08),  # 愤怒暂时压信任
    (Channel.TRUST,   Channel.FEAR):       ("suppress_fear",   0.15),  # 信任降低恐惧
    (Channel.LOVE,    Channel.FEAR):       ("blend",           0.0),   # 怕失去 (标记)
    (Channel.JOY,     Channel.SADNESS):    ("blend",           0.0),   # 苦涩的微笑 (标记)
    (Channel.SURPRISE, Channel.FEAR):      ("amplify_fear",    0.25),
    (Channel.SURPRISE, Channel.JOY):       ("amplify_joy",     0.15),
    (Channel.DISGUST, Channel.ANGER):      ("amplify_anger",   0.25),  # 道德愤怒——厌恶才转化愤怒
    (Channel.LOVE,    Channel.TRUST):      ("amplify_trust",   0.02),  # 慢调制——非常微弱
    (Channel.GUILT,   Channel.TRUST):      ("amplify_trust",   0.1),   # 愧疚→包容信任（需要被原谅）
    (Channel.GUILT,   Channel.ANGER):      ("suppress_anger",  0.3),   # 愧疚压制愤怒（是我的错不是你）
    (Channel.GUILT,   Channel.SADNESS):    ("amplify_sadness",  0.15), # 愧疚加深悲伤（后悔）
    (Channel.GUILT,   Channel.LOVE):       ("amplify_love",    0.05),  # 愧疚→更用力去爱（弥补）
}


def apply_interactions(
    state: EmotionalState,
    negative_scale: float = 1.0,
    baseline: Optional[Dict[Channel, float]] = None,
    threshold: float = 0.05,
    trust_ceiling: Optional[float] = None,
) -> List[str]:
    """应用交互矩阵。返回触发的混合标签。

    v0.4: 绝对值驱动但强度已校准——连续混合下 4-6 通道同时激活。
    v0.7: negative_scale 由耐受窗控制；只在 hyper/hypo 时放大/缩小负性交互。
    L1.1: 传入 baseline 时改为**基线相对激活**驱动。
    L2: baseline 由调用方按通道提供：trust 的交互基线是
        trust_target(love)，love / 快通道 / guilt 仍用各自人格基线。
        这样正常关系中的 trust 不会在空闲时被当成“额外信任”持续
        压抑 fear。love / guilt 对 trust 的正向增益受 trust_ceiling
        限制，避免慢通道积分器被每 tick 常数增益推离均衡。抑制幅度
        也改用源激活，而不是含基线的绝对值。

    为什么需要 baseline：
      旧实现用绝对通道值做门槛（0.05）。surprise 基线 0.10、disgust 基线
      0.06 都在门槛之上，所以即使没有任何事件，交互矩阵也会持续抬高
      joy / fear / anger，形成“空闲交互地板”。基线是人格底色，不是正在
      发生的情绪；交互应由**高过基线的那部分激活**驱动。

      传 baseline 后：
        - 源通道低于 baseline + threshold → 该规则不触发；
        - 幅度只取 source 高于 baseline 的部分；
        - 混合标签要求两方都高于各自基线；
        - 快通道的放大规则要求目标也已激活（co-activation）：
          惊讶只能放大已经存在的喜悦/恐惧，不能从零制造喜悦；
        - 抑制规则只需要源激活：愤怒可以在信任处于基线时压低信任；
        - 目标通道仍用当前绝对值做饱和，保证高值增益递减。

      不传 baseline 时保持 v0.7 的绝对阈值行为，供旧曲线对照。
    """
    snap = {ch.value: getattr(state, ch.value) for ch in Channel}
    blends = []
    deltas = {ch.value: 0.0 for ch in Channel}
    # L2: 多条规则可能同时给 trust 正向增量；上限必须按**累计增量**算，
    # 否则 love→trust 与 guilt→trust 各自取 room 后会一起越过 trust_target。
    trust_added = 0.0
    # 快通道放大规则必须目标共激活。慢通道的 amplify 是持续耦合，
    # 留给 L2 单独处理，不在这里改变。
    co_activation_targets = {
        Channel.JOY, Channel.FEAR, Channel.ANGER, Channel.SADNESS,
    }

    def activation(ch: Channel) -> float:
        value = snap[ch.value]
        if baseline is None:
            return value
        return max(0.0, value - baseline.get(ch, 0.0))

    for (a, b), (effect, strength) in INTERACTION_RULES.items():
        va = activation(a)
        vb = activation(b)
        if baseline is None:
            # 精确复现 v0.7：源和目标都必须高于绝对阈值。
            if va < threshold or vb < threshold:
                continue
        else:
            if va < threshold:
                continue
            if effect == "blend" and vb < threshold:
                continue
            if (effect.startswith("amplify")
                    and b in co_activation_targets
                    and vb < threshold):
                continue

        if effect == "amplify_joy":
            deltas["joy"] += va * strength * saturate(snap["joy"])
        elif effect == "amplify_anger":
            deltas["anger"] += va * strength * saturate(snap["anger"])
        elif effect == "amplify_fear":
            deltas["fear"] += va * strength * saturate(snap["fear"])
        elif effect == "amplify_sadness":
            deltas["sadness"] += va * strength * saturate(snap["sadness"])
        elif effect == "amplify_love":
            # L2: 愧疚越强，补偿性爱意增量越大；上限由 saturate 保证。
            # legacy 模式保持旧的常数增益。
            love_source = 1.0 if baseline is None else va
            deltas["love"] += (
                love_source * strength * saturate(snap["love"], asymptote=MAX_LOVE)
            )
        elif effect == "amplify_trust":
            # L2: love / guilt 只能把 trust 推向关系均衡线，不能靠每 tick
            # 的常数增益把信任推过 trust_target(love)。按累计增量封顶。
            delta = strength * saturate(snap["trust"])
            if trust_ceiling is not None:
                remaining = trust_ceiling - (snap["trust"] + trust_added)
                delta = min(delta, max(0.0, remaining))
            deltas["trust"] += delta
            trust_added += delta
        elif effect.startswith("suppress"):
            target = effect.split("_")[1]
            # L2: 相对模式只扣“高于基线的部分”；legacy 保留旧的绝对值。
            source = snap[a.value] if baseline is None else va
            deltas[target] -= source * strength
        elif effect == "blend":
            blends.append(f"{a.value}+{b.value}")

    # 统一应用所有交互增量。只缩放“落在负性通道上的增量”，
    # 不缩放 joy/trust/love，也不影响负性事件对正性通道的抑制。
    for ch_name, delta in deltas.items():
        if delta != 0:
            if ch_name in NEGATIVE_CHANNEL_NAMES:
                delta *= negative_scale
            current = getattr(state, ch_name)
            new_value = max(0.0, current + delta)
            if ch_name == Channel.LOVE.value:
                new_value = min(MAX_LOVE, new_value)
            elif ch_name == Channel.TRUST.value:
                new_value = min(MAX_TRUST, new_value)
            setattr(state, ch_name, new_value)

    return blends


def saturate(x: float, asymptote: float = 3.0) -> float:
    """自然饱和因子: 1 - x/asymptote。
    值越高增量越小。x=0→1.0, x=1.5→0.5, x=3.0→0。
    不是 tanh——是线性压缩，算起来轻。
    """
    return max(0.0, 1.0 - x / asymptote)


# ══════════════════════════════════════════════════════
# 慢通道累积 & 破坏
# ══════════════════════════════════════════════════════

def trust_target(love: float) -> float:
    """trust 的引力线：随 love 上升。不是固定基线，是曲线。
    love=0.1 → trust_target=0.10
    love=0.3 → trust_target=0.22
    love=0.6 → trust_target=0.50
    love=0.9 → trust_target=0.78
    斜率递减——爱越高，每单位爱换来的信任增益越小。
    """
    return 1.0 - 1.0 / (1.0 + love * 3.5)


def interaction_baselines(personality: Personality,
                          love: float) -> Dict[Channel, float]:
    """慢通道参与交互时使用的逐通道基线（L2）。

    不能把所有通道都套人格基线：
      - trust 是关系积分器，它的均衡点是 trust_target(love)。若用
        人格基线，正常关系里 trust 长到目标后仍会被当成“额外信任”，
        空闲时持续压低 fear，把快通道拉离基线。
      - love 没有关系目标线，仍以人格基线为准：高于人格底色的爱意
        才算“额外依恋”，用于“怕失去”等标签。
      - trust 对 fear / anger / disgust 的绝对缓冲已经在
        gate_appraisal() 里完成，不依赖交互矩阵。
    快通道与 guilt 仍使用人格基线（guilt 基线为 0）。
    """
    baseline = personality.baseline()
    baseline[Channel.TRUST] = trust_target(love)
    return baseline


def grow_slow_channels(state: EmotionalState, events_today: int,
                       positive_count: int, elapsed_hours: float):
    """慢通道互相照应——love 和 trust 是耦合对，不是单向依赖。
    trust 向 trust_target(love) 引力线靠近。
    love 受 trust 影响：trust 长期低于目标时 love 缓慢侵蚀。

    L2: positive_count 表示**本次间隔内新增**的正面事件数，不是累计值。
    旧引擎传的是累计 positive_events，导致每 tick 的 love 增量随历史事件数
    线性放大；L2 改为每 tick 传 0 / 1，长程曲线不再靠历史总量驱动。
    """
    positive_count = max(0, int(positive_count))

    # trust 向 trust_target(love) 靠近
    target = trust_target(state.love)
    gap = target - state.trust
    if gap > 0:
        state.trust += gap * 0.02 + elapsed_hours * 0.0003
    else:
        state.trust += gap * 0.005  # 溢出时慢慢降回

    # love 也感受 trust——trust 长期低于 target 时 love 被侵蚀
    trust_deficit = max(0.0, target - state.trust)
    if trust_deficit > 0.15:
        # 信任缺口大 → 爱被慢慢磨掉
        state.love -= trust_deficit * 0.005 * elapsed_hours
    else:
        # 信任健康 → 爱自然生长
        sat_love = saturate(state.love, asymptote=MAX_LOVE)
        love_growth = elapsed_hours * 0.0005 * sat_love + positive_count * 0.02 * sat_love
        state.love += love_growth

    # 数值护栏：慢通道是积分器，长时间运行不能靠“参数刚好”避免越界。
    state.trust = max(0.0, min(MAX_TRUST, state.trust))
    state.love = max(0.0, min(MAX_LOVE, state.love))


def damage_trust(state: EmotionalState, betrayal_severity: float):
    """背叛事件打掉信任。值可以短暂降到基线以下。"""
    state.trust = state.trust * (1.0 - betrayal_severity * 0.5)
    # trust 被打掉 → 让 love 也受一点影响（二级效应）
    if betrayal_severity > 0.5:
        state.love = state.love * 0.85


# ══════════════════════════════════════════════════════
# 慢通道 → 快通道 门控（在 appraisal 阶段介入）
# ══════════════════════════════════════════════════════

def gate_appraisal(raw_activation: Dict[Channel, float],
                   trust: float, love: float,
                   boundary_violation: bool = False) -> Dict[Channel, float]:
    """trust/love 在认知评估阶段压低负面/抬高正面。
    boundary_violation: 低信任时被越界称呼（亲密词/侮辱词）"""
    fear_gate    = max(0.02, 1.0 - trust * 0.9)
    anger_gate   = max(0.05, 1.0 - trust * 0.7)
    sadness_gate = max(0.05, 1.0 - love * 0.6)
    disgust_gate = max(0.05, 1.0 - trust * 0.8)
    joy_boost    = 1.0 + love * 0.5
    joy_trust_gate = min(1.0, trust * 1.5)

    gated = {}
    for ch, val in raw_activation.items():
        if ch == Channel.FEAR:
            gated[ch] = val * fear_gate
            if trust < 0.3 and raw_activation.get(Channel.JOY, 0) > 0.3:
                gated[ch] += (0.3 - trust) * 0.5
        elif ch == Channel.ANGER:
            gated[ch] = val * anger_gate
        elif ch == Channel.SADNESS:
            gated[ch] = val * sadness_gate
        elif ch == Channel.DISGUST:
            gated[ch] = val * disgust_gate
            if trust < 0.35 and raw_activation.get(Channel.JOY, 0) > 0.2:
                gated[ch] += (0.35 - trust) * 0.8
            # 低信任+越界称呼 → 厌恶飙升（"你谁啊叫我老公？"）
            if boundary_violation and trust < 0.5:
                gated[ch] += (0.5 - trust) * 1.2
        elif ch == Channel.JOY:
            # 低信任+越界 → joy 被压制（不是喜悦，是冒犯）
            if boundary_violation and trust < 0.5:
                gated[ch] = val * joy_boost * joy_trust_gate * 0.3
            else:
                gated[ch] = val * joy_boost * joy_trust_gate
        else:
            gated[ch] = val
    return gated


# ══════════════════════════════════════════════════════
# 认知评估（简易版）
# ══════════════════════════════════════════════════════

@dataclass
class Appraisal:
    """一个事件的认知评估"""
    goal_relevance:     float = 0.0   # 0~1
    goal_conduciveness: float = 0.0   # -1 障碍 ~ +1 助力
    expectedness:       float = 0.5   # 0 完全意外 ~ 1 完全预期
    coping_potential:   float = 0.5   # 0~1 我能控制的程度
    other_agency:       float = 0.0   # 0~1 他人/外部因素占比
    social_evaluation:  float = 0.0   # -1 负面评价 ~ +1 正面评价

    def to_dict(self) -> Dict[str, float]:
        return {
            "goal_relevance": self.goal_relevance,
            "goal_conduciveness": self.goal_conduciveness,
            "expectedness": self.expectedness,
            "coping_potential": self.coping_potential,
            "other_agency": self.other_agency,
            "social_evaluation": self.social_evaluation,
        }


APPRAISAL_LIMITS: Dict[str, Tuple[float, float]] = {
    "goal_relevance":     (0.0, 1.0),
    "goal_conduciveness": (-1.0, 1.0),
    "expectedness":       (0.0, 1.0),
    "coping_potential":   (0.0, 1.0),
    "other_agency":       (0.0, 1.0),
    "social_evaluation":  (-1.0, 1.0),
}


def apply_appraisal_bias(appraisal: Appraisal,
                         bias: Optional[Dict[str, float]],
                         cap: float = 0.2) -> Appraisal:
    """把有界记忆先验叠加到六维 Appraisal 上，返回新对象。

    L4 的边界：
      - 只改 Appraisal，不直接改 EmotionalState / Personality；
      - 每个维度先按 cap 截断，再按该维度的合法范围 clamp；
      - cap 默认 0.2，与 memory.memory_bias() 的上限一致。
    """
    if not bias:
        return appraisal
    cap = max(0.0, float(cap))
    values: Dict[str, float] = {}
    for name, (lo, hi) in APPRAISAL_LIMITS.items():
        current = float(getattr(appraisal, name, 0.0))
        delta = float(bias.get(name, 0.0))
        delta = max(-cap, min(cap, delta))
        values[name] = max(lo, min(hi, current + delta))
    return Appraisal(**values)


# L2: 自责低于 30% 不足以产生愧疚。低于门槛不是“弱愧疚”，而是“不是我的错”。
# 超过门槛后按剩余责任比例平滑放大，避免在 0.3 处出现跳变。
GUILT_SELF_AGENCY_THRESHOLD = 0.3


def appraise(app: Appraisal,
             guilt_self_agency_threshold: float = GUILT_SELF_AGENCY_THRESHOLD
             ) -> Dict[Channel, float]:
    """认知评估 → 通道原始激活（门控前）
    v0.4: 连续混合替代二元门控——每个通道都有非零激活。
    评鉴维度调制强度，不闸断开/关。正面事件也有微量负面通道（bittersweet），
    负面事件也有正面痕迹。惊讶几乎总伴随所有情绪。
    论文依据: Vanderbilt 混合情绪——各组分互不消灭。

    L2: guilt 只在 self_agency 超过门槛时激活。传 0.0 可复现旧的
    “任何自责都产生愧疚”行为，用于前后对照。
    """
    gc = app.goal_conduciveness       # -1 ~ +1
    gr = app.goal_relevance           # 0 ~ 1
    cp = app.coping_potential         # 0 ~ 1
    ue = 1.0 - app.expectedness       # 意外程度 0~1
    other = app.other_agency          # 0 ~ 1
    self_agency = 1.0 - other         # 0 ~ 1
    se = app.social_evaluation        # -1 ~ +1

    threshold = max(0.0, min(1.0, guilt_self_agency_threshold))
    if self_agency <= threshold + 1e-12 or threshold >= 1.0:
        guilt_agency = 0.0
    else:
        guilt_agency = (self_agency - threshold) / (1.0 - threshold)

    return {
        # 喜悦：正面事件 + 高应对 + 正面社交反馈
        Channel.JOY:      gr * (max(0, gc) * 0.6 + cp * 0.15 + max(0, se) * 0.1),

        # 悲伤：负面事件 + 低应对 + 意外（连好事也可能 bittersweet）
        Channel.SADNESS:  gr * (max(0, -gc) * 0.5 + max(0, gc) * 0.05 + (1 - cp) * 0.15 + ue * 0.1),

        # 愤怒：必须负面事件才激活。外部归因 + 挫败感只放大，不创造
        Channel.ANGER:    gr * max(0, -gc) * (other * 0.4 + 0.15 + (1 - cp) * ue * 0.1),

        # 恐惧：意外 × 低应对 + 不确定性
        Channel.FEAR:     gr * (max(0, -gc) * (ue * 0.4 + (1 - cp) * 0.2) + ue * (1 - cp) * 0.15),

        # 厌恶：负面社交评价 + 外部归因
        Channel.DISGUST:  gr * (max(0, -se) * 0.5 + max(0, -gc) * other * 0.2),

        # 惊讶：意外 × 相关性（始终激活——任何偏差都有惊讶成分）
        Channel.SURPRISE: gr * ue * 0.6 + abs(gc) * ue * 0.2,

        # 愧疚：我的责任 × 负面事件 × 我能控制（本可避免）
        # L2: self_agency 先过 0.3 门槛，再按剩余责任比例放大。
        Channel.GUILT:    gr * max(0, -gc) * guilt_agency * (0.3 + cp * 0.2),
    }


# ══════════════════════════════════════════════════════
# 人格基线
# ══════════════════════════════════════════════════════

@dataclass
class Personality:
    """OCEAN 大五人格 —— 决定基线偏移"""
    openness:          float = 0.5   # 开放性
    conscientiousness: float = 0.5   # 尽责性
    extraversion:      float = 0.5   # 外向性
    agreeableness:     float = 0.5   # 宜人性
    neuroticism:       float = 0.5   # 神经质

    def baseline(self) -> Dict[Channel, float]:
        """从 OCEAN 推导各通道基线（非零）"""
        return {
            Channel.JOY:      0.2 + self.extraversion * 0.3,
            Channel.SADNESS:  0.05 + self.neuroticism * 0.15,
            Channel.ANGER:    0.03 + self.neuroticism * 0.1 - self.agreeableness * 0.05,
            Channel.FEAR:     0.03 + self.neuroticism * 0.15,
            Channel.LOVE:     0.15 + self.agreeableness * 0.2,
            Channel.DISGUST:  0.02 + (1 - self.openness) * 0.08,
            Channel.SURPRISE: 0.05 + self.openness * 0.1,
            Channel.TRUST:    0.15 + self.agreeableness * 0.2 - self.neuroticism * 0.1,
            Channel.LONGING: 0.0,  # 思念基线为零——不见才生
            Channel.GUILT:   0.0,  # 愧疚基线为零——不做错就没有
        }


# ══════════════════════════════════════════════════════
# 双时钟：在线 / 离线 情绪衰减速率
# ══════════════════════════════════════════════════════
# 离线期间（休眠/关机/长时间不对话），时间仍在流逝，
# 但主观体验是压缩的——睡一觉愤怒几乎散尽，爱却不会。
# 压缩系数 < 1.0 表示离线时间 "感受上更短"。

OFFLINE_COMPRESSION: Dict[Channel, float] = {
    # 快通道：离线时衰减加速（值越小 = 衰减越快 = 越不留）
    Channel.ANGER:    0.08,   # 睡前吵架，醒后基本不气
    Channel.SURPRISE: 0.05,   # 冲击感睡一觉几乎清空
    Channel.DISGUST:  0.10,   # 恶心事第二天淡了
    Channel.JOY:      0.20,   # 好事回味还在，但不如当时浓
    Channel.FEAR:     0.25,   # 焦虑能穿透睡眠——醒来还怕
    Channel.SADNESS:  0.30,   # 悲伤穿透力最强——醒来还在被子里
    Channel.GUILT:    0.25,   # 愧疚也穿透睡眠——但没悲伤那么深
    # 慢通道：离线影响极小
    Channel.TRUST:    0.95,   # 信任不因睡觉消失
    Channel.LOVE:     0.98,   # 几乎不打折
}

# 离线 trust 衰减阈值（天）+ 衰减率
OFFLINE_TRUST_DECAY_DAYS: float = 7.0     # 7 天不联系才开始降
OFFLINE_TRUST_DECAY_RATE: float = 0.95    # 每多 7 天 × 0.95


# ══════════════════════════════════════════════════════
# L0 可观测性：一次 tick 的分阶段快照
# ══════════════════════════════════════════════════════

@dataclass
class StepTrace:
    """记录一次 tick 的关键阶段。

    StepTrace 只观察，不参与计算。上层可以用它做回归测试、
    仪表盘调试或消融实验；后续层也不要直接修改快照。
    """

    elapsed_minutes: float = 0.0
    before: Dict[str, float] = field(default_factory=dict)
    after_decay: Dict[str, float] = field(default_factory=dict)
    after_slow_channels: Dict[str, float] = field(default_factory=dict)
    raw_appraisal: Dict[str, float] = field(default_factory=dict)
    gated_appraisal: Dict[str, float] = field(default_factory=dict)
    after_appraisal: Dict[str, float] = field(default_factory=dict)
    after_atmosphere: Dict[str, float] = field(default_factory=dict)
    after_interaction: Dict[str, float] = field(default_factory=dict)
    final: Dict[str, float] = field(default_factory=dict)
    blends: List[str] = field(default_factory=list)
    shock_channels: List[str] = field(default_factory=list)
    atmosphere: float = 0.0
    tolerance_zone: str = "regulated"
    # L2 慢通道可观测性：本 tick 喂给 love 生长的正面事件数，以及
    # interaction 阶段使用的 trust 关系均衡线。
    slow_positive_input: int = 0
    trust_target: Optional[float] = None
    # L3 氛围 / 伤疤可观测性。字段只记录，不参与计算。
    atmosphere_mode: str = "legacy"
    atmosphere_before: float = 0.0
    atmosphere_applied: float = 0.0
    atmosphere_after: float = 0.0
    atmosphere_shock_density: float = 0.0
    atmosphere_arousal: float = 0.0
    atmosphere_multipliers: Dict[str, float] = field(default_factory=dict)
    scar_shift: float = 0.0
    scar_channels: List[str] = field(default_factory=list)
    gated_written: Dict[str, float] = field(default_factory=dict)
    # L4 记忆先验可观测性。只在显式传入 appraisal_bias 时序列化，
    # 默认 trace 键集合保持不变。
    memory_bias_enabled: bool = False
    memory_bias_applied: Dict[str, float] = field(default_factory=dict)
    appraisal_before_memory: Dict[str, float] = field(default_factory=dict)
    # L7 多角色情绪感染可观测性。只在显式传入 contagion_bias 且存在
    # Appraisal 时序列化；默认 trace 键集合保持不变。
    contagion_enabled: bool = False
    contagion_applied: Dict[str, float] = field(default_factory=dict)
    appraisal_before_contagion: Dict[str, float] = field(default_factory=dict)
    # L3.5 元情绪可观测性。只在 meta 开启且 trace=True 时序列化；
    # 这些字段只读，不参与核心计算。
    meta_enabled: bool = False
    meta_appraisal: Dict[str, Dict[str, float]] = field(default_factory=dict)
    meta_emotions: List[Dict[str, object]] = field(default_factory=list)
    regulation_bias: Dict[str, object] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    _PHASES = (
        "before", "after_decay", "after_slow_channels",
        "after_appraisal", "after_atmosphere",
        "after_interaction", "final",
    )

    def mark(self, phase: str, snapshot) -> "StepTrace":
        """记录一个阶段。snapshot 可以是 EmotionalState 或通道字典。"""
        if phase not in self._PHASES:
            self.notes.append(f"unknown phase: {phase}")
            return self
        if hasattr(snapshot, "to_dict"):
            snapshot = snapshot.to_dict()
        setattr(self, phase, dict(snapshot))
        return self

    def to_dict(self) -> Dict[str, object]:
        """返回可序列化副本；不暴露内部可变引用。"""
        data: Dict[str, object] = {
            "elapsed_minutes": round(self.elapsed_minutes, 4),
            "before": dict(self.before),
            "after_decay": dict(self.after_decay),
            "after_slow_channels": dict(self.after_slow_channels),
            "raw_appraisal": dict(self.raw_appraisal),
            "gated_appraisal": dict(self.gated_appraisal),
            "after_appraisal": dict(self.after_appraisal),
            "after_atmosphere": dict(self.after_atmosphere),
            "after_interaction": dict(self.after_interaction),
            "final": dict(self.final),
            "blends": list(self.blends),
            "shock_channels": list(self.shock_channels),
            "atmosphere": round(self.atmosphere, 4),
            "tolerance_zone": self.tolerance_zone,
            "slow_positive_input": self.slow_positive_input,
            "trust_target": (
                round(self.trust_target, 4)
                if self.trust_target is not None else None
            ),
            "atmosphere_mode": self.atmosphere_mode,
            "atmosphere_before": round(self.atmosphere_before, 4),
            "atmosphere_applied": round(self.atmosphere_applied, 4),
            "atmosphere_after": round(self.atmosphere_after, 4),
            "atmosphere_shock_density": round(
                self.atmosphere_shock_density, 4,
            ),
            "atmosphere_arousal": round(self.atmosphere_arousal, 4),
            "atmosphere_multipliers": {
                key: round(value, 4)
                for key, value in self.atmosphere_multipliers.items()
            },
            "scar_shift": round(self.scar_shift, 4),
            "scar_channels": list(self.scar_channels),
            "gated_written": dict(self.gated_written),
            "notes": list(self.notes),
        }
        if self.memory_bias_enabled:
            data.update({
                "memory_bias_enabled": True,
                "memory_bias_applied": {
                    key: round(value, 4)
                    for key, value in self.memory_bias_applied.items()
                },
                "appraisal_before_memory": dict(
                    self.appraisal_before_memory
                ),
            })
        if self.contagion_enabled:
            data.update({
                "contagion_enabled": True,
                "contagion_applied": {
                    key: round(value, 4)
                    for key, value in self.contagion_applied.items()
                },
                "appraisal_before_contagion": dict(
                    self.appraisal_before_contagion
                ),
            })
        if self.meta_enabled:
            bias = self.regulation_bias or {}
            data.update({
                "meta_enabled": True,
                "meta_appraisal": {
                    target: dict(values)
                    for target, values in self.meta_appraisal.items()
                },
                "meta_emotions": [
                    dict(emotion) for emotion in self.meta_emotions
                ],
                "regulation_bias": {
                    "expression_gain": dict(
                        bias.get("expression_gain", {})
                    ),
                    "display_rule": dict(
                        bias.get("display_rule", {})
                    ),
                    "regulation_action": dict(
                        bias.get("regulation_action", {})
                    ),
                    "tolerance_load_bias": bias.get(
                        "tolerance_load_bias", 0.0,
                    ),
                    "reappraisal_target": list(
                        bias.get("reappraisal_target", [])
                    ),
                },
            })
        return data
