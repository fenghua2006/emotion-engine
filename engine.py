"""
Emotion Engine v0.3 — 多通道情绪交互引擎（记忆耦合·双时钟）
===========================================================
论文验证的设计决策:
  - Jennings (2025): 情绪 = 多个并行匹配流同时激活
  - Diamond (2007) / Bowen (2016): Arousal 驱动记忆固化，非 Valence
  - Nielson (2007): 30分钟记忆固化窗口
  - Sci Reports (2024): 抑郁现实主义（高 sadness 者回忆更准）
  - Vanderbilt: 混合情绪中各组分互不消灭
  - Self-Excited Dynamics (2024): 消极情绪留存更久

作者: 枫骅 & SCC
日期: 2026/06/30

v0.3 核心变更:
  - 记忆-情绪耦合: Arousal 驱动存储 + 30min 固化窗口 + 三级记忆
  - Saga 效应: 长期记忆持续微拉基线（每 24h tick 调用一次）
  - 双时钟: 在线 tick() + 离线 wake() 各通道独立压缩衰减
  - 记忆召回: Arousal 标记匹配，非情绪一致性
  - Novelty gate: 相似事件不重复存储

v0.2:
  - 去除硬上限 1.0 → tanh 自然饱和 + 感知边际递减
  - 弹性衰减: 偏离基线越远回弹越快
  - 冲击感 = e^Δ（指数级对比，不是线性差）
"""

import time
import math
import json
import sqlite3
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum


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


# ══════════════════════════════════════════════════════
# 核心: EmotionalState
# ══════════════════════════════════════════════════════

@dataclass
class EmotionalState:
    """8 通道全部正值。基线 ≠ 0。"""

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

INTERACTION_RULES: Dict[Tuple[Channel, Channel], Tuple[str, float]] = {
    # (通道A, 通道B) → (效应, 强度)
    (Channel.SADNESS, Channel.ANGER):      ("amplify_anger",   0.3),   # 委屈：愤怒带水
    (Channel.ANGER,   Channel.TRUST):      ("suppress_trust",  0.1),   # 愤怒暂时压信任
    (Channel.TRUST,   Channel.FEAR):       ("suppress_fear",   0.15),  # 信任降低恐惧
    (Channel.FEAR,    Channel.ANGER):      ("amplify_anger",   0.2),   # 防御性攻击
    (Channel.LOVE,    Channel.FEAR):       ("blend",           0.0),   # 怕失去 (标记)
    (Channel.JOY,     Channel.SADNESS):    ("blend",           0.0),   # 苦涩的微笑 (标记)
    (Channel.SURPRISE, Channel.FEAR):      ("amplify_fear",    0.4),
    (Channel.SURPRISE, Channel.JOY):       ("amplify_joy",     0.25),
    (Channel.DISGUST, Channel.ANGER):      ("amplify_anger",   0.35),  # 道德愤怒
    (Channel.LOVE,    Channel.TRUST):      ("amplify_trust",   0.02),  # 慢调制——非常微弱
    (Channel.GUILT,   Channel.TRUST):      ("amplify_trust",   0.1),   # 愧疚→包容信任（需要被原谅）
    (Channel.GUILT,   Channel.ANGER):      ("suppress_anger",  0.3),   # 愧疚压制愤怒（是我的错不是你）
    (Channel.GUILT,   Channel.SADNESS):    ("amplify_sadness",  0.2),  # 愧疚加深悲伤（后悔）
    (Channel.GUILT,   Channel.LOVE):       ("amplify_love",    0.05),  # 愧疚→更用力去爱（弥补）
}


def apply_interactions(state: EmotionalState) -> List[str]:
    """应用交互矩阵。返回触发的混合标签。
    v0.2: 不用 min(1.0, ...)，用 tanh 自然饱和。
    """
    blends = []
    for (a, b), (effect, strength) in INTERACTION_RULES.items():
        va = getattr(state, a.value)
        vb = getattr(state, b.value)
        if va < 0.15 or vb < 0.15:
            continue

        if effect == "amplify_joy":
            amp = vb * strength
            state.joy = state.joy + amp * saturate(state.joy)
        elif effect == "amplify_anger":
            amp = vb * strength
            state.anger = state.anger + amp * saturate(state.anger)
        elif effect == "amplify_fear":
            amp = vb * strength
            state.fear = state.fear + amp * saturate(state.fear)
        elif effect == "amplify_sadness":
            amp = vb * strength
            state.sadness = state.sadness + amp * saturate(state.sadness)
        elif effect == "amplify_love":
            amp = strength
            state.love = state.love + amp * saturate(state.love, asymptote=2.0)
        elif effect == "amplify_trust":
            amp = strength
            state.trust = state.trust + amp * saturate(state.trust)
        elif effect.startswith("suppress"):
            target = effect.split("_")[1]
            current = getattr(state, target)
            setattr(state, target, max(0.0, current - va * strength))
        elif effect == "blend":
            blends.append(f"{a.value}+{b.value}")

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


def grow_slow_channels(state: EmotionalState, events_today: int,
                       positive_count: int, elapsed_hours: float):
    """慢通道互相照应——love 和 trust 是耦合对，不是单向依赖。
    trust 向 trust_target(love) 引力线靠近。
    love 受 trust 影响：trust 长期低于目标时 love 缓慢侵蚀。
    """
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
        sat_love = saturate(state.love, asymptote=2.0)
        love_growth = elapsed_hours * 0.0005 * sat_love + positive_count * 0.02 * sat_love
        state.love += love_growth


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
                   trust: float, love: float) -> Dict[Channel, float]:
    """trust/love 在认知评估阶段压低负面/抬高正面。
    v0.5: 低信任时正面事件也被打折——"我爱你"从陌生人嘴里说出来不是喜悦是警惕。
    """
    fear_gate    = max(0.02, 1.0 - trust * 0.9)       # trust=0.8 → fear 打 2 折
    anger_gate   = max(0.05, 1.0 - trust * 0.7)       # trust=0.8 → anger 打 4.4 折
    sadness_gate = max(0.05, 1.0 - love * 0.6)        # love=0.6 → sadness 打 6.4 折
    disgust_gate = max(0.05, 1.0 - trust * 0.8)       # trust=0.8 → disgust 打 3.6 折
    joy_boost    = 1.0 + love * 0.5                   # love=0.6 → joy 加成 30%
    joy_trust_gate = min(1.0, trust * 1.5)            # trust=0.2 → joy 打 3 折。"我爱你"不可信

    gated = {}
    for ch, val in raw_activation.items():
        if ch == Channel.FEAR:
            gated[ch] = val * fear_gate
            # 低信任+正面事件 → fear 上升（不对劲）
            if trust < 0.3 and raw_activation.get(Channel.JOY, 0) > 0.3:
                gated[ch] += (0.3 - trust) * 0.5
        elif ch == Channel.ANGER:
            gated[ch] = val * anger_gate
        elif ch == Channel.SADNESS:
            gated[ch] = val * sadness_gate
        elif ch == Channel.DISGUST:
            gated[ch] = val * disgust_gate
        elif ch == Channel.JOY:
            gated[ch] = val * joy_boost * joy_trust_gate  # trust 打折正面
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


def appraise(app: Appraisal) -> Dict[Channel, float]:
    """认知评估 → 通道原始激活（门控前）"""
    gc = app.goal_conduciveness
    gr = app.goal_relevance
    cp = app.coping_potential
    ue = 1.0 - app.expectedness  # 意外程度

    self_agency = 1.0 - app.other_agency  # 我的责任 = 不是别人的

    return {
        Channel.JOY:      gc * gr if gc > 0 else 0.0,
        Channel.SADNESS:  -gc * gr * (1.0 - cp) if gc < 0 else 0.0,
        Channel.ANGER:    -gc * app.other_agency * cp if gc < 0 else 0.0,
        Channel.FEAR:     -gc * gr * (1.0 - cp) * ue if gc < 0 else 0.0,
        Channel.DISGUST:  -app.social_evaluation * app.other_agency if app.social_evaluation < 0 else 0.0,
        Channel.SURPRISE: ue * gr,
        Channel.GUILT:    -gc * gr * self_agency if gc < 0 and self_agency > 0.3 else 0.0,
        # 愧疚 = 坏事发生了 + 是我的责任（自己造成的）
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

@dataclass
class EmotionEngine:
    """一次一个 Agent 的实例。"""
    state:       EmotionalState
    personality: Personality
    memory:      MemoryStore
    scars:       SensitizationStore
    events_today:       int = 0
    positive_events:    int = 0
    _last_saga:         float = field(default_factory=time.time)
    atmosphere:         float = -0.3  # -1 放松 ~ +1 紧绷。初始偏轻松。
    _recent_shocks:     List[float] = field(default_factory=list)  # 最近 N 个事件的 shock 强度

    def wake(self) -> Dict:
        """
        从休眠中醒来。结算离线期间的情绪变化。
        离线时间对每个通道的"感受"不同——愤怒消散快，爱几乎不变。
        调用时机: Agent 启动/恢复对话时，tick() 之前调用一次。
        """
        now = time.time()
        offline_minutes = (now - self.state._last_update) / 60.0
        if offline_minutes < 1:
            return {"offline_minutes": 0, "message": "barely slept"}

        baseline = self.personality.baseline()

        # 快通道：压缩时间衰减
        for ch in FAST_CHANNELS:
            val = getattr(self.state, ch.value)
            comp = OFFLINE_COMPRESSION.get(ch, 0.3)
            effective_minutes = offline_minutes * comp
            val = decay(val, HALF_LIFE[ch], effective_minutes, baseline[ch])
            setattr(self.state, ch.value, val)

        # 慢通道：持续累积（离线期间也有微量生长）
        offline_hours = offline_minutes / 60.0
        grow_slow_channels(self.state, 0, 0, offline_hours)

        # 思念：离线期间生长。love 越深 + 离线越久 → 思念越重
        # 公式: longing += love × log₁₀(1 + offline_hours) × 0.12
        #       有爱才有思念。不爱的人不在就不在。
        if offline_hours > 1 and self.state.love > 0.05:
            longing_growth = self.state.love * math.log10(1 + offline_hours) * 0.12
            self.state.longing = min(self.state.longing + longing_growth, self.state.love * 2)

        # trust 长时间不联系微降
        offline_days = offline_minutes / 1440.0
        if offline_days > OFFLINE_TRUST_DECAY_DAYS:
            excess = offline_days - OFFLINE_TRUST_DECAY_DAYS
            weeks = excess / 7.0
            self.state.trust *= OFFLINE_TRUST_DECAY_RATE ** weeks

        # 记忆固化：离线期间 pending 全部完成
        self.memory.consolidate(now=now)
        self.memory.decay_short_term()

        self.state._last_update = now

        return {
            "offline_minutes":     round(offline_minutes, 1),
            "compressed_fast":     {ch.value: round(offline_minutes * OFFLINE_COMPRESSION.get(ch, 0.3), 1)
                                   for ch in FAST_CHANNELS},
            "state":              self.state.to_dict(),
        }

    def tick(self, appraisal: Optional[Appraisal] = None) -> Dict:
        """
        每次对话轮次/事件 的完整情绪更新。
        返回: {felt_emotion, blends, shock_channels, ...}
        """
        now = time.time()
        elapsed_minutes = (now - self.state._last_update) / 60.0
        elapsed_hours   = elapsed_minutes / 60.0

        # NaN/Inf 防护——极端值后重设
        for ch in Channel:
            val = getattr(self.state, ch.value)
            if math.isnan(val) or math.isinf(val):
                bl = self.personality.baseline()
                setattr(self.state, ch.value, bl[ch])

        # Step 0: 保存上一帧快照（对比度用）
        self.state.snapshot_previous()

        # Step 1: 弹性衰减（偏离基线越远衰减越快）
        baseline = self.personality.baseline()

        # 思念在线时消退——人在身边，思有所归
        # 但有 love 时不会归零——爱着的人即使见面也隐约知道会再分开
        if self.state.longing > 0:
            longing_decay = 0.3 * elapsed_hours if elapsed_hours > 0 else 0.05
            floor = self.state.love * 0.05  # 残留底——爱还在
            self.state.longing = max(floor, self.state.longing - longing_decay)

        for ch in FAST_CHANNELS:
            val = getattr(self.state, ch.value)
            hl  = HALF_LIFE[ch]
            # 弹性衰减：偏离越远越快
            val = decay(val, hl, elapsed_minutes, baseline[ch])
            # 基线回弹（轻拉力）
            val = return_to_baseline(val, baseline[ch], elapsed_minutes)
            setattr(self.state, ch.value, val)

        # Step 2: 慢通道缓慢生长
        grow_slow_channels(self.state, self.events_today,
                           self.positive_events, elapsed_hours)

        # Step 3: 如果有新事件 → 认知评估 → 叠加
        raw = {}
        gated = {}
        shock_channels = []
        if appraisal is not None:
            raw = appraise(appraisal)
            gated = gate_appraisal(raw, self.state.trust, self.state.love)

            # Step 3.5: 触发敏感化——旧伤疤放大负面情绪
            scar_shift = self.scars.get_shift(appraisal)
            if scar_shift > 0.01 and appraisal.goal_conduciveness < 0:
                for ch in (Channel.SADNESS, Channel.FEAR, Channel.ANGER):
                    if ch in gated and gated[ch] > 0.01:
                        gated[ch] *= (1.0 + scar_shift)  # 旧伤疤让负面放大 0~60%

            # 登记触发 / 正面反例
            tag = self.scars.detect(appraisal)
            if tag and appraisal.goal_conduciveness < 0:
                self.scars.register(tag, now)
            elif tag and appraisal.goal_conduciveness > 0:
                self.scars.register_positive(tag)

            for ch in FAST_CHANNELS:
                if ch in gated and gated[ch] > 0.01:
                    current = getattr(self.state, ch.value)
                    # 自然饱和：值越高增量越小（无硬上限）
                    saturation = saturate(current)
                    new_val = current + gated[ch] * saturation
                    setattr(self.state, ch.value, new_val)

            # 检测背叛（信任破坏）
            if (appraisal.goal_conduciveness < -0.5
                and appraisal.other_agency > 0.5):
                damage_trust(self.state, abs(appraisal.goal_conduciveness))

            self.events_today += 1
            if appraisal.goal_conduciveness > 0 and appraisal.other_agency > 0:
                self.positive_events += 1

        # Step 4: 氛围更新 + 调制
        # 氛围基于最近 shock 密度和当前 arousal 漂移
        recent_shock_intensity = len(shock_channels) / max(1, len(FAST_CHANNELS))
        self._recent_shocks.append(recent_shock_intensity)
        if len(self._recent_shocks) > 5:
            self._recent_shocks.pop(0)

        # shock 密度高 + 当前 fear/sadness 高 → 变紧绷
        shock_density = sum(self._recent_shocks) / len(self._recent_shocks)
        arousal_trend = (self.state.fear + self.state.sadness) / 2
        # 向目标漂移，一次最多移 0.1
        target = -0.6 + shock_density * 1.2 + arousal_trend * 0.4
        self.atmosphere += max(-0.1, min(0.1, target - self.atmosphere))

        # 氛围调制 gate：紧绷放大负面，轻松缓冲负面
        # atmosphere > 0: 负面放大, 正面削弱；< 0: 反之
        if self.atmosphere > 0 and appraisal is not None:
            for ch in FAST_CHANNELS:
                if ch in gated and gated[ch] > 0.01:
                    ch_name = ch.value
                    if ch_name in ("joy",):
                        gated[ch] *= (1.0 - self.atmosphere * 0.3)  # 紧绷时喜悦打折
                    elif ch_name in ("sadness", "fear", "anger"):
                        gated[ch] *= (1.0 + self.atmosphere * 0.4)  # 紧绷时负面放大
        elif self.atmosphere < 0 and appraisal is not None:
            for ch in FAST_CHANNELS:
                if ch in gated and gated[ch] > 0.01:
                    ch_name = ch.value
                    if ch_name in ("sadness", "fear", "anger"):
                        gated[ch] *= (1.0 + self.atmosphere * 0.3)  # 轻松时负面缓冲
                    elif ch_name in ("joy",):
                        gated[ch] *= (1.0 - self.atmosphere * 0.2)  # 轻松时喜悦放大

        # Step 5: 交互矩阵
        blends = apply_interactions(self.state)

        # Step 5: 对比度冲击感（指数级 Δ 感知，带安全截断）
        for ch in FAST_CHANNELS:
            raw_delta = abs(self.state.delta(ch))
            # 指数冲击——但 clamp 输入防止溢出
            clamped = min(raw_delta, 3.0)  # Δ > 3.0 时感官已经饱和
            shock = math.exp(clamped * 2.0) - 1
            threshold = SHOCK_WEIGHT.get(ch, 0.3)
            if shock > threshold:
                shock_channels.append(ch.value)

        # Step 6: 记忆存储 + 固化（带 novelty gate）
        if appraisal is not None:
            arousal  = self.state.surprise + len(shock_channels) * 0.5
            # Novelty gate: 30min 内同 arousal ±0.15 且同 relevance ±0.2 → 跳过
            recent = self.memory.recent_items(now, 30) if hasattr(self.memory, 'recent_items') else \
                     [m for m in getattr(self.memory, 'short_term', [])
                      if now - m.timestamp < 30 * 60]
            is_novel = all(
                abs(m.arousal - arousal) > 0.15 or
                abs(m.relevance - appraisal.goal_relevance) > 0.2
                for m in recent
            )
            if is_novel or len(recent) == 0:
                mem = MemoryItem(
                    content=f"gc={appraisal.goal_conduciveness:.1f} gr={appraisal.goal_relevance:.1f}",
                    timestamp=now,
                    emotion_snap=self.state.to_dict(),
                    arousal=arousal,
                    relevance=appraisal.goal_relevance,
                )
                self.memory.store(mem)

        # 30分钟固化窗口检查
        self.memory.consolidate()
        # 短期记忆清理
        self.memory.decay_short_term()

        # Step 7: Saga 效应（每累计 24h 应用一次长期记忆基线拉力）
        saga_elapsed = (now - self._last_saga) / 3600.0  # 小时
        if saga_elapsed > 24 and (self.memory.flash or self.memory.long_term):
            baseline = self.personality.baseline()
            pull = self.memory.saga_pull(baseline)
            for ch_name, offset in pull.items():
                if abs(offset) > 0.001:
                    ch_enum = Channel(ch_name)
                    if ch_enum in FAST_CHANNELS:
                        current = getattr(self.state, ch_name)
                        # Saga 拉力轻柔——每次最多移动 0.02
                        clamped = max(-0.02, min(0.02, offset))
                        setattr(self.state, ch_name, current + clamped)
            self._last_saga = now

        # 更新计时器
        self.state._last_update = now

        # 输出
        felt = self.state.felt_all()
        mem_stats = self.memory.stats()
        return {
            "state":          self.state.to_dict(),
            "felt":           felt,  # log压缩后的感知值
            "blends":         blends,
            "shock_channels": shock_channels,
            "raw_appraisal":  {k.value: round(v, 3) for k, v in raw.items()},
            "gated_appraisal":{k.value: round(v, 3) for k, v in gated.items()},
            "trust":          round(self.state.trust, 3),
            "love":           round(self.state.love, 3),
            "memory":         mem_stats,
            "atmosphere":     round(self.atmosphere, 3),
        }


# ══════════════════════════════════════════════════════
# v0.4 触发敏感化（Kindling + 贝叶斯威胁模型）
# ══════════════════════════════════════════════════════
# 论文依据:
#   - Kindling (Post 1992, Kendler 2000): 重复事件降低触发阈值
#   - 杏仁核贝叶斯模型 (MindLAB 2024): 威胁信号 5× 权重, 修复需 6-18 月
#   - 去情境化恐惧 (Neudert 2024): 恐惧泛化到安全情境

@dataclass
class SensitizationPattern:
    """一个被敏感的触发模式——重复经历形成的'旧伤疤'。"""
    tag:         str                     # 模式标签: "cold_shoulder", "betrayal", "criticism"
    trigger_count: int = 0              # 触发了多少次
    threshold_shift: float = 0.0        # 门槛降了多少 (0=正常, >0=过度敏感)
    last_triggered: float = 0.0         # 上次触发时间
    positive_counter: int = 0           # 正面反例计数（30 次才能复位）


class SensitizationStore:
    """管理角色所有'旧伤疤'。不是人格——是经历刻下的反应模式。"""

    def __init__(self):
        self.patterns: Dict[str, SensitizationPattern] = {}

    def detect(self, appraisal: "Appraisal") -> Optional[str]:
        """检测当前事件是否匹配已知模式或应该形成新模式。
        返回标签（如果匹配）或 None（如果新类型事件）。
        """
        gc = appraisal.goal_conduciveness
        oa = appraisal.other_agency
        se = appraisal.social_evaluation

        # 分类当前事件
        if gc < -0.3 and oa > 0.5:
            return "social_hurt"         # 别人伤害了我
        elif gc < -0.3 and oa < 0.3:
            return "self_blame"          # 我搞砸了
        elif se < -0.3 and oa > 0.3:
            return "criticism"           # 被当众评价
        elif gc < 0 and oa > 0.7:
            return "cold_shoulder"       # 被冷落/忽视
        return None

    def register(self, tag: str, now: float):
        """登记一次触发。重复 3 次 → 敏感化开始。"""
        if tag not in self.patterns:
            self.patterns[tag] = SensitizationPattern(tag=tag)

        p = self.patterns[tag]
        p.trigger_count += 1
        p.last_triggered = now
        p.positive_counter = 0  # 重置——新的触发打破了修复进程

        # Kindling: 第 3 次开始降门槛，之后每次再降
        if p.trigger_count >= 3:
            excess = p.trigger_count - 2
            p.threshold_shift = min(0.6, excess * 0.08)  # 单次门槛降 8%, 最大降 60%

    def register_positive(self, tag: str):
        """正面反例——修复进程。5 次正面抵消 1 次负面 (5:1 贝叶斯权重)"""
        if tag in self.patterns:
            p = self.patterns[tag]
            p.positive_counter += 1
            # 30 次正面反例完全复位 (对应论文 6-18 个月修复期)
            if p.positive_counter >= 30 and p.threshold_shift > 0:
                p.threshold_shift = max(0.0, p.threshold_shift - 0.1)
                p.positive_counter = 0

    def get_shift(self, appraisal: "Appraisal") -> float:
        """返回此事件应被放大多少（因为旧伤疤）。
        返回 0.0 ~ 0.6——越高越敏感。
        """
        tag = self.detect(appraisal)
        if tag and tag in self.patterns:
            return self.patterns[tag].threshold_shift
        return 0.0

    def all_scars(self) -> Dict[str, float]:
        """返回所有旧伤疤及其敏感化程度。"""
        return {tag: p.threshold_shift for tag, p in self.patterns.items()
                if p.threshold_shift > 0.01}


# ══════════════════════════════════════════════════════
# v0.5 SQLite 持久化
# ══════════════════════════════════════════════════════

DB_PATH = "emotion.db"


def _get_db(path: str = DB_PATH) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")     # 并发安全
    db.execute("PRAGMA synchronous=NORMAL")    # 性能
    return db


def init_db(path: str = DB_PATH):
    """初始化数据库——建表。首次调用或 DB 不存在时调用。"""
    db = _get_db(path)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            tier TEXT NOT NULL DEFAULT 'short_term',
            arousal REAL NOT NULL DEFAULT 0,
            relevance REAL NOT NULL DEFAULT 0,
            emotion_snap TEXT NOT NULL DEFAULT '{}',
            timestamp REAL NOT NULL,
            access_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_memories_tier ON memories(tier);
        CREATE INDEX IF NOT EXISTS idx_memories_arousal ON memories(arousal);
        CREATE INDEX IF NOT EXISTS idx_memories_timestamp ON memories(timestamp);

        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            state_json TEXT NOT NULL,
            atmosphere REAL NOT NULL DEFAULT 0,
            saved_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS scars (
            tag TEXT PRIMARY KEY,
            trigger_count INTEGER NOT NULL DEFAULT 0,
            threshold_shift REAL NOT NULL DEFAULT 0,
            positive_counter INTEGER NOT NULL DEFAULT 0,
            last_triggered REAL NOT NULL DEFAULT 0
        );
    """)
    db.commit()
    return db


def save_snapshot(engine: "EmotionEngine", path: str = DB_PATH):
    """保存完整引擎状态——断线恢复用。"""
    db = _get_db(path)
    state_json = json.dumps(engine.state.to_dict())
    db.execute(
        "INSERT INTO snapshots (state_json, atmosphere, saved_at) VALUES (?,?,?)",
        (state_json, engine.atmosphere, time.time()))
    # 同时保存 scars
    for tag, p in engine.scars.patterns.items():
        db.execute(
            "INSERT OR REPLACE INTO scars VALUES (?,?,?,?,?)",
            (tag, p.trigger_count, p.threshold_shift, p.positive_counter, p.last_triggered))
    db.commit()


def load_snapshot(engine: "EmotionEngine", path: str = DB_PATH) -> bool:
    """从上次快照恢复。返回 True 如果成功。"""
    if not os.path.exists(path):
        return False
    db = _get_db(path)
    row = db.execute(
        "SELECT state_json, atmosphere FROM snapshots ORDER BY saved_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        return False
    data = json.loads(row[0])
    for ch in Channel:
        if ch.value in data:
            setattr(engine.state, ch.value, data[ch.value])
    engine.atmosphere = row[1]
    # 恢复 scars
    for row_s in db.execute("SELECT * FROM scars"):
        tag, cnt, shift, pos, last = row_s
        p = SensitizationPattern(tag=tag, trigger_count=cnt,
                                  threshold_shift=shift, positive_counter=pos,
                                  last_triggered=last)
        engine.scars.patterns[tag] = p
    return True


# ══════════════════════════════════════════════════════
# SQLite 版 MemoryStore（替换内存版，可选）
# ══════════════════════════════════════════════════════

class MemoryStoreDB:
    """与 MemoryStore 相同接口，底层用 SQLite。"""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.db = _get_db(db_path)
        self._pending: List[Tuple[float, "MemoryItem"]] = []

    def store(self, item: "MemoryItem"):
        self.db.execute(
            "INSERT INTO memories (content,tier,arousal,relevance,emotion_snap,timestamp) "
            "VALUES (?,?,?,?,?,?)",
            (item.content, item.tier, item.arousal, item.relevance,
             json.dumps(item.emotion_snap), item.timestamp))
        self.db.commit()
        self._pending.append((item.timestamp, item))

    def consolidate(self, now: Optional[float] = None):
        if now is None:
            now = time.time()
        window = 30 * 60
        survived = []
        for stored_at, item in self._pending:
            if now - stored_at < window:
                survived.append((stored_at, item))
                continue
            score = item.arousal * 0.5 + item.relevance * 0.3 + item.arousal * item.relevance * 0.2
            tier = "flash" if score > 0.6 else ("long_term" if score > 0.3 else "short_term")
            self.db.execute("UPDATE memories SET tier=? WHERE id=?",
                            (tier, getattr(item, '_db_id', None)))
            self.db.commit()
        self._pending = survived

    def decay_short_term(self):
        cutoff = time.time() - 7 * 86400
        self.db.execute("DELETE FROM memories WHERE tier='short_term' AND timestamp < ?", (cutoff,))
        self.db.commit()

    def recall(self, current_state: "EmotionalState", shock_count: int,
               limit: int = 5) -> List["MemoryItem"]:
        current_arousal = current_state.surprise + shock_count * 0.5
        rows = self.db.execute(
            "SELECT * FROM memories ORDER BY "
            "CASE tier WHEN 'flash' THEN 3 WHEN 'long_term' THEN 2 ELSE 1 END DESC, "
            "timestamp DESC LIMIT 200"
        ).fetchall()

        scored = []
        for r in rows:
            _, content, tier, arousal, relevance, snap_json, ts, ac = r
            age_days = (time.time() - ts) / 86400.0
            arousal_match = 1.0 - min(1.0, abs(current_arousal - arousal) / 2.0)
            tier_weight = {"flash": 1.0, "long_term": 0.6, "short_term": 0.3}.get(tier, 0.3)
            score = arousal_match * 0.5 + tier_weight * 0.3 + (0.9 ** age_days) * 0.2
            scored.append((score, r))

        scored.sort(key=lambda x: x[0], reverse=True)
        sad = current_state.sadness
        if sad < 0.25:
            scored = [(s, r) for s, r in scored
                      if json.loads(r[5]).get("sadness", 0) < 0.6 or s > 1.0]

        result = []
        for _, r in scored[:limit]:
            _, content, tier, arousal, relevance, snap_json, ts, ac = r
            mem = MemoryItem(content=content, timestamp=ts,
                             emotion_snap=json.loads(snap_json),
                             arousal=arousal, relevance=relevance, tier=tier,
                             access_count=ac)
            self.db.execute("UPDATE memories SET access_count=? WHERE id=?",
                            (ac + 1, r[0]))
            result.append(mem)
        self.db.commit()
        return result

    def saga_pull(self, baseline: Dict[Channel, float]) -> Dict[str, float]:
        pull = {ch.value: 0.0 for ch in Channel}
        total_weight = 0.0
        rows = self.db.execute(
            "SELECT emotion_snap, timestamp FROM memories WHERE tier IN ('flash','long_term')"
        ).fetchall()
        for snap_json, ts in rows:
            snap = json.loads(snap_json)
            age = (time.time() - ts) / 86400.0
            weight = 1.0 * (0.977 ** age)
            total_weight += weight
            for ch_name, val in snap.items():
                if ch_name in pull:
                    baseline_val = baseline.get(Channel(ch_name), 0.0)
                    pull[ch_name] += (val - baseline_val) * weight
        if total_weight > 0:
            for ch in pull:
                pull[ch] /= total_weight
        return pull

    def recent_items(self, now: float, minutes: float = 30) -> List["MemoryItem"]:
        cutoff = now - minutes * 60
        rows = self.db.execute(
            "SELECT * FROM memories WHERE timestamp > ?", (cutoff,)
        ).fetchall()
        return [MemoryItem(content=r[1], timestamp=r[6],
                           emotion_snap=json.loads(r[5]),
                           arousal=r[3], relevance=r[4]) for r in rows]

    def stats(self) -> Dict:
        flash = self.db.execute("SELECT COUNT(*) FROM memories WHERE tier='flash'").fetchone()[0]
        lt = self.db.execute("SELECT COUNT(*) FROM memories WHERE tier='long_term'").fetchone()[0]
        st = self.db.execute("SELECT COUNT(*) FROM memories WHERE tier='short_term'").fetchone()[0]
        return {"flash_count": flash, "long_term_count": lt,
                "short_term_count": st, "pending_count": len(self._pending)}


# ══════════════════════════════════════════════════════
# v0.3 记忆-情绪耦合
# ══════════════════════════════════════════════════════
# 理论依据 (经论文验证):
#   - Arousal 驱动记忆固化，Valence 不（Diamond 2007; Bowen 2016）
#   - 情绪一致性回忆证据弱（York 2025: 贝叶斯中等证据支持零效应）
#   - 固化窗口 30min（Nielson 2007）
#   - 闪光灯记忆无特殊机制（Conway 1994; Neisser 1992）
#   - 抑郁现实主义：高 sadness 者回忆更准，非更偏（Sci Reports 2024）

@dataclass
class MemoryItem:
    """一段被存储的记忆。附带存储时的情绪快照。"""
    content:      str                     # 事件描述
    timestamp:    float                   # 存储时间 (time.time())
    emotion_snap: Dict[str, float]        # 存储时的 8 通道值
    arousal:      float                   # 存储时的 arousal 水平
    relevance:    float                   # 个人相关性
    tier:         str = "short_term"      # flash / long_term / short_term
    access_count: int = 0                 # 被召回的次数

    def age_days(self) -> float:
        return (time.time() - self.timestamp) / 86400.0


class MemoryStore:
    """记忆存储 + 检索。不依赖外部数据库。"""

    def __init__(self, max_flash: int = 20, max_long: int = 100, max_short: int = 200):
        self.flash:      List[MemoryItem] = []       # 闪光灯记忆——极少，永久
        self.long_term:  List[MemoryItem] = []       # 长期——缓慢衰减
        self.short_term: List[MemoryItem] = []       # 短期——7天自动清除
        self.max_flash   = max_flash
        self.max_long    = max_long
        self.max_short   = max_short
        self._pending:   List[Tuple[float, MemoryItem]] = []  # 待固化（30min窗口内）

    # ── 存储判定（基于 Arousal，不是 Valence）──

    def store(self, item: MemoryItem):
        """存入短期层。30 分钟后 arousal 仍高 → 晋升。"""
        if len(self.short_term) >= self.max_short:
            self.short_term.pop(0)
        self.short_term.append(item)
        self._pending.append((item.timestamp, item))

    def consolidate(self, now: Optional[float] = None):
        """
        30 分钟固化窗口检查。
        在线: 逐帧检查; 离线(wake): 一次性全部结算
        """
        if now is None:
            now = time.time()
        window = 30 * 60
        survived = []

        for stored_at, item in self._pending:
            if now - stored_at < window:
                survived.append((stored_at, item))
                continue

            # 固化窗口过完——最终判定
            score = item.arousal * 0.5 + item.relevance * 0.3 + item.arousal * item.relevance * 0.2

            if score > 0.6:
                item.tier = "flash"
                if len(self.flash) >= self.max_flash:
                    self.flash.pop(0)
                self.flash.append(item)
                # 从短期移除（已晋升）
                if item in self.short_term:
                    self.short_term.remove(item)
            elif score > 0.3:
                item.tier = "long_term"
                if len(self.long_term) >= self.max_long:
                    self.long_term.pop(0)
                self.long_term.append(item)
                if item in self.short_term:
                    self.short_term.remove(item)
            # else: 留在 short_term，等 7 天自动衰减

        self._pending = survived

    def decay_short_term(self):
        """短期记忆 7 天后清除。"""
        cutoff = time.time() - 7 * 86400
        self.short_term = [m for m in self.short_term if m.timestamp > cutoff]

    # ── 检索（Arousal 标记召回，非情绪一致性）──

    def recall(self, current_state: EmotionalState, shock_count: int,
               limit: int = 5) -> List[MemoryItem]:
        """
        Arousal 标记召回：当前 arousal 越高 → 越容易触发高 arousal 记忆。
        不要求情绪类型一致（论文不支持情绪一致性假设）。
        """
        current_arousal = current_state.surprise + shock_count * 0.5

        scored = []
        all_memories = self.flash + self.long_term + self.short_term

        for mem in all_memories:
            # Arousal 匹配度（核心权重）
            arousal_match = 1.0 - min(1.0, abs(current_arousal - mem.arousal) / 2.0)
            # 时间衰减（旧记忆权重低）
            age_penalty = 0.9 ** mem.age_days()
            # 访问频率 boost（经常被想起的更容易再想起）
            access_boost = min(0.3, mem.access_count * 0.05)
            # tier 权重
            tier_weight = {"flash": 1.0, "long_term": 0.6, "short_term": 0.3}[mem.tier]

            score = (arousal_match * 0.5 + tier_weight * 0.3 +
                     age_penalty * 0.1 + access_boost) * 0.9 + age_penalty * 0.1
            scored.append((score, mem))

        scored.sort(key=lambda x: x[0], reverse=True)

        # 标记访问
        for _, mem in scored[:limit]:
            mem.access_count += 1

        # 抑郁现实主义修正：高 sadness 者回忆更准，低 sadness 者有轻度正向偏差。
        # 不是硬截断——是权重微调（正常人格的悲伤记忆权重 ×0.85，不是砍掉）
        sad = current_state.sadness
        if sad < 0.25:
            scored = [(s * (0.85 if m.emotion_snap.get("sadness", 0) > 0.5 else 1.0), m)
                      for s, m in scored]

        return [m for _, m in scored[:limit]]

    # ── Saga 效应（旧记忆持续拉基线）──

    def saga_pull(self, baseline: Dict[Channel, float]) -> Dict[str, float]:
        """
        长期记忆（flash + long_term）对基线的累积偏移。
        旧记忆衰减慢，新记忆拉力强。
        年龄 > 90 天的记忆拉力衰减到 < 10%。
        """
        pull = {ch.value: 0.0 for ch in Channel}
        total_weight = 0.0

        for mem in self.flash + self.long_term:
            age = mem.age_days()
            # 半衰期 30 天的权重衰减
            weight = 1.0 * (0.977 ** age)  # 30天→0.5, 90天→0.12
            total_weight += weight

            for ch_name, val in mem.emotion_snap.items():
                baseline_val = baseline[Channel(ch_name)]
                pull[ch_name] += (val - baseline_val) * weight

        # 归一化
        if total_weight > 0:
            for ch in pull:
                pull[ch] /= total_weight

        return pull

    def stats(self) -> Dict:
        return {
            "flash_count":     len(self.flash),
            "long_term_count": len(self.long_term),
            "short_term_count": len(self.short_term),
            "pending_count":   len(self._pending),
        }


# ══════════════════════════════════════════════════════
# 快速测试
# ══════════════════════════════════════════════════════

if __name__ == "__main__":
    # 创建引擎：外向+高神经质人格
    pers = Personality(extraversion=0.7, neuroticism=0.6,
                       agreeableness=0.7, openness=0.8)
    state = EmotionalState()
    # 应用人格基线
    bl = pers.baseline()
    for ch, val in bl.items():
        setattr(state, ch.value, val)

    db = init_db("test_emotion.db")
    engine = EmotionEngine(state=state, personality=pers,
                           memory=MemoryStoreDB("test_emotion.db"),
                           scars=SensitizationStore())

    print("=== 初始状态 ===")
    for k, v in state.to_dict().items():
        print(f"  {k}: {v:.3f}")

    # 事件1: 好消息（被朋友夸奖）
    print("\n=== 事件: 被朋友夸奖 ===")
    result = engine.tick(Appraisal(
        goal_relevance=0.7, goal_conduciveness=0.8,
        expectedness=0.3, other_agency=0.8, social_evaluation=0.6))
    for k, v in result["state"].items():
        print(f"  {k}: {v:.3f}")
    print(f"  blends: {result['blends']}")
    print(f"  shock: {result['shock_channels']}")

    # 事件2: 坏消息（被放鸽子——别人的责任）
    print("\n=== 事件: 被放鸽子 (other_agency=0.9) ===")
    result = engine.tick(Appraisal(
        goal_relevance=0.8, goal_conduciveness=-0.7,
        expectedness=0.2, other_agency=0.9, coping_potential=0.2))
    for k, v in result["state"].items():
        print(f"  {k}: {v:.3f}")
    print(f"  blends: {result['blends']}")
    print(f"  shock: {result['shock_channels']}")
    print(f"  atmosphere: {result.get('atmosphere', 'N/A')}")
    print(f"  memory: {result.get('memory', 'N/A')}")

    # 事件3: 我伤害了别人（高 self_agency → guilt 触发）
    print("\n=== 事件: 我对朋友说了伤人的话 (self_agency=0.7) ===")
    result = engine.tick(Appraisal(
        goal_relevance=0.8, goal_conduciveness=-0.6,
        expectedness=0.3, other_agency=0.3,  # low other → high self
        coping_potential=0.5, social_evaluation=-0.5))
    for k, v in result["state"].items():
        print(f"  {k}: {v:.3f}")
    print(f"  blends: {result['blends']}")
    print(f"  shock: {result['shock_channels']}")
    print(f"  atmosphere: {result.get('atmosphere', 'N/A')}")

    # 打印交互说明
    print("\n=== 交互矩阵 + 新功能验证 ===")
    for b in result['blends']:
        if b == "joy+sadness":
            print("  苦涩的微笑: 喜悦和悲伤同时存在")
        elif b == "love+fear":
            print("  怕失去: 爱在恐惧的底色上")
    print(f"  guilt 已激活: {result['state'].get('guilt', 0):.3f}")
    print(f"  trust-love 耦合: trust={result['state']['trust']:.3f} love={result['state']['love']:.3f}")

    # v0.5: 持久化测试
    print("\n=== v0.5: SQLite save/load ===")
    save_snapshot(engine, "test_emotion.db")
    print(f"  Saved snapshot. trust={engine.state.trust:.3f} guilt={engine.state.guilt:.3f}")

    # 模拟"关掉引擎再打开"
    engine2 = EmotionEngine(state=EmotionalState(),
                            personality=pers,
                            memory=MemoryStoreDB("test_emotion.db"),
                            scars=SensitizationStore())
    if load_snapshot(engine2, "test_emotion.db"):
        print(f"  Restored! trust={engine2.state.trust:.3f} guilt={engine2.state.guilt:.3f}")
        print(f"  State preserved across restart: OK")
    else:
        print("  Restore failed")

    # 清理
    engine2.memory.db.close()
    os.remove("test_emotion.db")
