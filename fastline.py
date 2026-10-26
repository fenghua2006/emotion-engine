"""L1 六条快通道参考线 —— 只锁 joy / sadness / anger / fear / disgust / surprise。

这不是新的引擎层，也不是生产入口。它把 L0 的纯函数按 engine.tick() 的
快通道路径重新组合成一条可复现的参考线，用于：

  - 固定人格、固定 trust / love、固定时钟的 golden 序列；
  - L1 不变量测试（非负、回落、意外度、归因、无凭空喜悦）；
  - 后续 L2 / L3 / L3.5 的“关闭新层时旧曲线不变”对照。

刻意不包含：
  - trust / love / longing / guilt 的动力学（trust / love 只作为固定常数参与门控）；
  - tolerance / 伤疤 / 氛围 / 记忆 / Saga / LLM。

L1 只保留**六通道之间**的交互（surprise→fear、surprise→joy、
disgust→anger、joy+sadness 混合标签）。slow → fast 的交互
（trust→fear、love+fear 等）属于 L2，不在 L1 参考线里。

L1.1（2026-10-26）：交互改为基线相对激活，并保留 `legacy_interactions`
开关复现 v0.7 旧曲线。默认 `idle_interactions=True`，空闲 tick 不再
产生交互增量。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from core import (
    Appraisal,
    Channel,
    EmotionalState,
    HALF_LIFE,
    Personality,
    SHOCK_WEIGHT,
    apply_interactions,
    appraise,
    decay,
    gate_appraisal,
    return_to_baseline,
    saturate,
)

# L1 的六个基础通道。guilt 留给 L2，love / trust / longing 不属于 L1。
SIX_CHANNELS: Tuple[Channel, ...] = (
    Channel.JOY,
    Channel.SADNESS,
    Channel.ANGER,
    Channel.FEAR,
    Channel.DISGUST,
    Channel.SURPRISE,
)
SIX_CHANNEL_NAMES: Tuple[str, ...] = tuple(ch.value for ch in SIX_CHANNELS)

# 固定关系常数：L1 不测试慢通道动力学，只给门控一个健康关系环境。
DEFAULT_TRUST: float = 0.5
DEFAULT_LOVE: float = 0.5

# 与 engine._dominant_channel 保持同一判定方式：原始值最高且 > 0.3。
DOMINANT_THRESHOLD: float = 0.3


@dataclass(frozen=True)
class FastLineStep:
    """一条参考线 tick 的可比较结果。"""

    key: str
    values: Dict[str, float]
    dominant: str
    shock_channels: Tuple[str, ...]
    blends: Tuple[str, ...]
    raw_appraisal: Dict[str, float]
    gated_appraisal: Dict[str, float]


@dataclass
class FastLine:
    """固定人格与固定 trust / love 的六通道参考线。"""

    personality: Personality = field(default_factory=Personality)
    trust: float = DEFAULT_TRUST
    love: float = DEFAULT_LOVE
    # 生产引擎每个 tick 都会跑交互矩阵。L1.1 改为基线相对激活后，
    # 空闲 tick 不再产生交互增量，因此参考线默认也每个 tick 都跑。
    idle_interactions: bool = True
    # 复现 v0.7 的绝对阈值交互（用于旧 golden 对照）。
    legacy_interactions: bool = False
    state: EmotionalState = field(init=False)

    def __post_init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """回到人格基线。每次 reset 都从同一状态出发。"""
        baseline = self.personality.baseline()
        self.state = EmotionalState()
        for ch in Channel:
            setattr(self.state, ch.value, baseline[ch])
        # 慢通道与缺失通道固定，不参与 L1。
        self.state.trust = self.trust
        self.state.love = self.love
        self.state.longing = 0.0
        self.state.guilt = 0.0
        self.state._previous = None

    def _guard_non_finite(self) -> None:
        baseline = self.personality.baseline()
        for ch in SIX_CHANNELS:
            value = getattr(self.state, ch.value)
            if not math.isfinite(value):
                setattr(self.state, ch.value, baseline[ch])

    def step(self,
             appraisal: Optional[Appraisal] = None,
             elapsed_minutes: float = 10.0,
             boundary_violation: bool = False,
             negative_scale: float = 1.0,
             key: str = "") -> FastLineStep:
        """走一遍与 engine.tick() 相同的快通道路径，返回本 tick 结果。"""
        if elapsed_minutes < 0:
            elapsed_minutes = 0.0

        self._guard_non_finite()
        self.state.snapshot_previous()

        # Step 1: 弹性衰减 + 基线回弹（只作用于六条快通道）
        baseline = self.personality.baseline()
        for ch in SIX_CHANNELS:
            value = getattr(self.state, ch.value)
            value = decay(value, HALF_LIFE[ch], elapsed_minutes, baseline[ch])
            value = return_to_baseline(value, baseline[ch], elapsed_minutes)
            setattr(self.state, ch.value, value)

        # Step 2: 认知评估 → 门控 → 饱和写入
        raw_appraisal: Dict[str, float] = {}
        gated_appraisal: Dict[str, float] = {}
        if appraisal is not None:
            raw = appraise(appraisal)
            gated = gate_appraisal(
                raw,
                trust=self.trust,
                love=self.love,
                boundary_violation=boundary_violation,
            )
            raw_appraisal = {ch.value: raw.get(ch, 0.0) for ch in SIX_CHANNELS}
            if negative_scale != 1.0:
                for ch in (Channel.SADNESS, Channel.ANGER,
                           Channel.FEAR, Channel.DISGUST):
                    if gated.get(ch, 0.0) > 0.001:
                        gated[ch] *= negative_scale
            gated_appraisal = {ch.value: gated.get(ch, 0.0) for ch in SIX_CHANNELS}

            for ch in SIX_CHANNELS:
                activation = gated.get(ch, 0.0)
                if activation > 0.001:
                    current = getattr(self.state, ch.value)
                    setattr(
                        self.state,
                        ch.value,
                        current + activation * saturate(current),
                    )

        # Step 3: 交互矩阵。把 slow / guilt 暂时置零，只让六通道规则生效。
        # 这样 L1 不依赖 trust / love / guilt 的数值，也不会被 slow→fast 规则污染。
        blends: List[str] = []
        if appraisal is not None or self.idle_interactions:
            trust_before = self.state.trust
            love_before = self.state.love
            guilt_before = self.state.guilt
            self.state.trust = 0.0
            self.state.love = 0.0
            self.state.guilt = 0.0
            blends = apply_interactions(
                self.state,
                negative_scale=negative_scale,
                baseline=None if self.legacy_interactions
                else self.personality.baseline(),
            )
            self.state.trust = trust_before
            self.state.love = love_before
            self.state.guilt = guilt_before

        # Step 4: 冲击检测（与 engine 相同的指数 Δ 规则）
        shock_channels: List[str] = []
        for ch in SIX_CHANNELS:
            raw_delta = abs(self.state.delta(ch))
            clamped = min(raw_delta, 3.0)
            shock = math.exp(clamped * 2.0) - 1
            if shock > SHOCK_WEIGHT.get(ch, 0.3):
                shock_channels.append(ch.value)

        values = {ch.value: getattr(self.state, ch.value) for ch in SIX_CHANNELS}
        return FastLineStep(
            key=key,
            values=values,
            dominant=dominant_channel(self.state),
            shock_channels=tuple(shock_channels),
            blends=tuple(blends),
            raw_appraisal=raw_appraisal,
            gated_appraisal=gated_appraisal,
        )

    def run(self,
            events: Iterable,
            elapsed_minutes: float = 10.0) -> List[FastLineStep]:
        """按顺序跑一组事件。事件需有 .key 与 .appraisal 属性。"""
        return [
            self.step(
                event.appraisal,
                elapsed_minutes=elapsed_minutes,
                key=event.key,
            )
            for event in events
        ]


def dominant_channel(state: EmotionalState) -> str:
    """六通道内原始值最高者；不超过 0.3 视为 neutral。"""
    best = max(SIX_CHANNELS, key=lambda ch: getattr(state, ch.value))
    value = getattr(state, best.value)
    return best.value if value > DOMINANT_THRESHOLD else "neutral"
