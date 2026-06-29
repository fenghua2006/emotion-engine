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

def grow_slow_channels(state: EmotionalState, events_today: int,
                       positive_count: int, elapsed_hours: float):
    """慢通道随时间 + 正面交互缓慢增长。无硬上限。"""
    for ch in SLOW_CHANNELS:
        current = getattr(state, ch.value)
        # 自然饱和（值越高长得越慢）
        sat = saturate(current, asymptote=2.0)
        growth = elapsed_hours * 0.0005 * sat
        pos_growth = positive_count * 0.02 * sat
        setattr(state, ch.value, current + growth + pos_growth)


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
    """trust/love 在认知评估阶段压低负面/抬高正面。"""
    fear_gate    = max(0.02, 1.0 - trust * 0.9)       # trust=0.8 → fear 打 2 折
    anger_gate   = max(0.05, 1.0 - trust * 0.7)       # trust=0.8 → anger 打 4.4 折
    sadness_gate = max(0.05, 1.0 - love * 0.6)        # love=0.6 → sadness 打 6.4 折
    disgust_gate = max(0.05, 1.0 - trust * 0.8)       # trust=0.8 → disgust 打 3.6 折
    joy_boost    = 1.0 + love * 0.5                   # love=0.6 → joy 加成 30%

    gated = {}
    for ch, val in raw_activation.items():
        if ch == Channel.FEAR:
            gated[ch] = val * fear_gate
        elif ch == Channel.ANGER:
            gated[ch] = val * anger_gate
        elif ch == Channel.SADNESS:
            gated[ch] = val * sadness_gate
        elif ch == Channel.DISGUST:
            gated[ch] = val * disgust_gate
        elif ch == Channel.JOY:
            gated[ch] = val * joy_boost
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
    events_today:       int = 0
    positive_events:    int = 0
    _last_saga:         float = field(default_factory=time.time)

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
        # 公式: longing += love × log₁₀(1 + offline_hours) × 0.1
        #       有爱才有思念。不爱的人不在就不在。
        offline_hours = offline_minutes / 60.0
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

        # Step 4: 交互矩阵
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
            recent = [m for m in self.memory.short_term
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
        }


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

    engine = EmotionEngine(state=state, personality=pers, memory=MemoryStore())

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

    # 事件2: 坏消息（被伴侣放鸽子）
    print("\n=== 事件: 被放鸽子 ===")
    result = engine.tick(Appraisal(
        goal_relevance=0.8, goal_conduciveness=-0.7,
        expectedness=0.2, other_agency=0.9, coping_potential=0.2))
    for k, v in result["state"].items():
        print(f"  {k}: {v:.3f}")
    print(f"  blends: {result['blends']}")
    print(f"  shock: {result['shock_channels']}")

    # 打印交互说明
    print("\n=== 交互矩阵触发说明 ===")
    for b in result['blends']:
        if b == "joy+sadness":
            print("  苦涩的微笑: 喜悦和悲伤同时存在")
        elif b == "love+fear":
            print("  怕失去: 爱在恐惧的底色上")
