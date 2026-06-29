"""
Emotion Engine v0.2 — 多通道情绪交互引擎（无上限版）
=====================================================
基于：
  - Jennings (2025): 情绪 = 多个并行匹配流同时激活
  - Vanderbilt Emotional Blends: 混合情绪中各组分互不消灭
  - Self-Excited Dynamics (2024): 消极情绪留存更久
  - Sentipolis (CMU, 2025): 情绪-记忆耦合

作者: 枫骅 & SCC
日期: 2026/06/29

v0.2 核心变更:
  - 去除硬上限 1.0 → 自然饱和 + 感知边际递减
  - 弹性衰减: 偏离基线越远回弹越快 (k ∝ distance²)
  - 冲击感 = e^Δ (指数级对比，不是线性差)
  - 值域开放: raw > 1.0 被感知压缩 (log₁₀ 映射)
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


FAST_CHANNELS = [Channel.JOY, Channel.SADNESS, Channel.ANGER,
                 Channel.FEAR, Channel.DISGUST, Channel.SURPRISE]
SLOW_CHANNELS = [Channel.LOVE, Channel.TRUST]


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

    return {
        Channel.JOY:      gc * gr if gc > 0 else 0.0,
        Channel.SADNESS:  -gc * gr * (1.0 - cp) if gc < 0 else 0.0,
        Channel.ANGER:    -gc * app.other_agency * cp if gc < 0 else 0.0,
        Channel.FEAR:     -gc * gr * (1.0 - cp) * ue if gc < 0 else 0.0,
        Channel.DISGUST:  -app.social_evaluation * app.other_agency if app.social_evaluation < 0 else 0.0,
        Channel.SURPRISE: ue * gr,
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
        }


# ══════════════════════════════════════════════════════
# 完整更新循环
# ══════════════════════════════════════════════════════

@dataclass
class EmotionEngine:
    """一次一个 Agent 的实例。"""
    state:       EmotionalState
    personality: Personality
    events_today:       int = 0
    positive_events:    int = 0

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

        # 更新计时器
        self.state._last_update = now

        # 输出
        felt = self.state.felt_all()
        return {
            "state":          self.state.to_dict(),
            "felt":           felt,  # log压缩后的感知值
            "blends":         blends,
            "shock_channels": shock_channels,
            "raw_appraisal":  {k.value: round(v, 3) for k, v in raw.items()},
            "gated_appraisal":{k.value: round(v, 3) for k, v in gated.items()},
            "trust":          round(self.state.trust, 3),
            "love":           round(self.state.love, 3),
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

    engine = EmotionEngine(state=state, personality=pers)

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
